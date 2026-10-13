# -*- coding: utf-8 -*-
"""سیگنال‌های شخصی ادمین (با قیمت ورود/حد ضرر/تارگت‌های دلخواه و نوع سفارش market/limit/stop) -
صف، انتشار در کانال، رهگیری دقیق و محاسبه‌ی نتیجه.

⚠️ این ماژول عمداً مستقل از مسیر خودکارِ R-محورِ candle_engine.py است (تارگت‌های ۱/۲/۴/۶R و رانر
دست‌نخورده‌اند). فقط سه نقطه‌ی تماس با بقیه‌ی سیستم دارد:
  ۱) صف manual_signals.json (آیتم‌هایی با کلید "custom" / "custom_cmd") - subscription_bot.py می‌نویسد،
     این ماژول از طریق candle_engine.py می‌خواند (دقیقاً همان الگوی دو-نویسنده‌ی فعلی).
  ۲) فایل data/custom_trades.json - فقط candle_engine.py می‌نویسد (تک‌نویسنده → بدون تعارض گیت).
  ۳) trade_history.json - رکورد نهایی با همان فیلدهای رکوردهای دیگر (final_r دقیق) تا /results،
     /history، Equity و گزارش روزانه بدون هیچ تغییری آن را بشمارند.

مدل نتیجه (R): ریسک = |entry - sl|. هر تارگت i سهم w_i از پوزیشن را در R_i=|tp_i-entry|/ریسک می‌بندد
(وزن‌ها در custom_signal_parser.target_weights). حد ضرر ثابت است؛ باقی‌مانده‌ی پوزیشن در استاپ -1R می‌بندد.
"""
from __future__ import annotations

import re
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from custom_signal_parser import target_weights, CustomSignal

logger = logging.getLogger("custom_signals")

POLL_SECONDS_FAST = 8          # Coinbase/OKX (بدون سهمیه)
POLL_SECONDS_SLOW = 300        # Twelve Data (سهمیه‌ی ۸۰۰ درخواست در روز)
PENDING_EXPIRY_HOURS = 72      # سفارش پر‌نشده بعد از این مدت لغو می‌شود
MARKET_MAX_STALE_PCT = 1.0     # اگر قیمت زنده از قیمت ذکرشده‌ی market بیش از این درصد فاصله داشت، رد می‌شود
HTTP_TIMEOUT = 5
TRACK_FILE = "custom_trades.json"


# ───────────────────────────── منطق خالص (بدون شبکه/فایل؛ تست‌پذیر) ─────────────────────────────

def _is_buy(t: Dict[str, Any]) -> bool:
    return t["side"] == "BUY"


def risk_of(t: Dict[str, Any]) -> float:
    return abs(t["entry"] - t["sl"])


def target_r(t: Dict[str, Any], i: int) -> float:
    return abs(t["targets"][i] - t["entry"]) / risk_of(t)


def banked_r(t: Dict[str, Any]) -> float:
    """سودِ واقعاً بانک‌شده در تارگت‌های خورده (بدون احتساب باقی‌مانده)."""
    return sum(t["weights"][i] * target_r(t, i) for i, h in enumerate(t["hit"]) if h)


def remaining_weight(t: Dict[str, Any]) -> float:
    return max(0.0, 1.0 - sum(t["weights"][i] for i, h in enumerate(t["hit"]) if h))


def final_r_for(t: Dict[str, Any], exit_price: Optional[float] = None, stopped: bool = False) -> float:
    """نتیجه‌ی نهایی. stopped=True: باقی‌مانده روی حد ضرر (-1R). exit_price: باقی‌مانده روی همان قیمت."""
    res = banked_r(t)
    rem = remaining_weight(t)
    if rem <= 1e-12:
        return round(res, 4)
    if stopped:
        res += rem * -1.0
    elif exit_price is not None:
        sgn = 1.0 if _is_buy(t) else -1.0
        res += rem * ((exit_price - t["entry"]) * sgn / risk_of(t))
    return round(res, 4)


def advance(t: Dict[str, Any], bars: List[Tuple[int, float, float]]) -> List[Dict[str, Any]]:
    """بارها (t_ms, high, low) به ترتیب زمان. وضعیت معامله را در جا به‌روز می‌کند و رویدادها را برمی‌گرداند.

    قواعد محافظه‌کارانه: در یک بار که هم حد ضرر و هم تارگت را لمس کرده، حد ضرر مقدم است؛ سفارش
    پر‌نشده‌ای که قبل از پر شدن به حد ضرر یا تارگت ۱ برسد لغو می‌شود. هر بار فقط یک‌بار اثر می‌کند
    (حالت‌ها یک‌طرفه‌اند) پس بازپردازشِ یک بار تکراری بی‌خطر است."""
    events: List[Dict[str, Any]] = []
    buy = _is_buy(t)
    for ts, hi, lo in bars:
        if t["status"] in ("closed", "cancelled"):
            break
        if ts < t.get("armed_ms", 0):
            continue                      # بارهای قبل از ثبت سفارش (ممکن است قبل از انتشار رخ داده باشند)
        fav = hi if buy else lo           # بدترین/بهترین نقطه‌ی این بار
        adv = lo if buy else hi
        if t["status"] == "pending":
            fill_price = None
            ot = t["order_type"]
            if ot == "limit":
                if (buy and lo <= t["entry"]) or (not buy and hi >= t["entry"]):
                    fill_price = t["entry"]
            elif ot == "stop_market":
                if (buy and hi >= t["trigger"]) or (not buy and lo <= t["trigger"]):
                    fill_price = t["trigger"]
            elif ot == "stop_limit":
                if not t.get("triggered") and ((buy and hi >= t["trigger"]) or (not buy and lo <= t["trigger"])):
                    t["triggered"] = True
                    events.append({"type": "triggered", "price": t["trigger"], "ts": ts})
                if t.get("triggered") and ((buy and lo <= t["entry"]) or (not buy and hi >= t["entry"])):
                    fill_price = t["entry"]
            if fill_price is None:
                # قبل از پر شدن: رسیدن به حد ضرر یا تارگت ۱ یعنی ستاپ باطل شده
                if (buy and lo <= t["sl"]) or (not buy and hi >= t["sl"]):
                    t["status"] = "cancelled"
                    events.append({"type": "cancel", "reason": "stop", "price": t["sl"], "ts": ts})
                    break
                if (buy and hi >= t["targets"][0]) or (not buy and lo <= t["targets"][0]):
                    t["status"] = "cancelled"
                    events.append({"type": "cancel", "reason": "target1", "price": t["targets"][0], "ts": ts})
                    break
                continue
            t["status"] = "open"
            t["opened_ms"] = ts
            events.append({"type": "fill", "price": fill_price, "ts": ts})
        # ---- معامله باز ----
        rk = risk_of(t)
        mfe = ((fav - t["entry"]) if buy else (t["entry"] - fav)) / rk
        mae = ((t["entry"] - adv) if buy else (adv - t["entry"])) / rk
        t["mfe_r"] = round(max(t.get("mfe_r") or 0.0, mfe), 4)
        t["mae_r"] = round(max(t.get("mae_r") or 0.0, mae), 4)
        if (buy and lo <= t["sl"]) or (not buy and hi >= t["sl"]):
            t["status"] = "closed"
            t["close_type"] = "stop" if not any(t["hit"]) else "stop_after_partial"
            events.append({"type": "stop", "price": t["sl"], "ts": ts})
            break
        for i, tp in enumerate(t["targets"]):
            if t["hit"][i]:
                continue
            if (buy and hi >= tp) or (not buy and lo <= tp):
                t["hit"][i] = True
                # نتیجه‌ی بانک‌شده همان لحظه روی خودِ رویداد ثبت می‌شود (اگر یک بار چند تارگت را یک‌جا بزند،
                # پیام هر تارگت باید عدد همان لحظه‌ی خودش را نشان دهد، نه وضعیت نهاییِ آن بار)
                events.append({"type": "target", "index": i, "price": tp, "ts": ts, "banked": round(banked_r(t), 4)})
            else:
                break
        if all(t["hit"]):
            t["status"] = "closed"
            t["close_type"] = "all_targets"
            events.append({"type": "all_targets", "ts": ts})
            break
    return events


# ───────────────────────────── قیمت (Coinbase → OKX → Twelve Data) ─────────────────────────────

def _bars_coinbase(base: str, since_ms: int) -> Optional[List[Tuple[int, float, float]]]:
    now_ms = int(time.time() * 1000)
    out: Dict[int, Tuple[float, float]] = {}
    start = since_ms
    for _ in range(24):
        end = min(start + 290 * 60_000, now_ms + 60_000)
        r = requests.get(
            f"https://api.exchange.coinbase.com/products/{base}-USD/candles",
            params={"granularity": 60,
                    "start": datetime.fromtimestamp(start / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": datetime.fromtimestamp(end / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None if not out else sorted((k, v[0], v[1]) for k, v in out.items())
        for row in r.json():
            out[int(row[0]) * 1000] = (float(row[2]), float(row[1]))      # high, low
        if end >= now_ms:
            break
        start = end
    return sorted((k, v[0], v[1]) for k, v in out.items()) or None


def _bars_okx(base: str, since_ms: int) -> Optional[List[Tuple[int, float, float]]]:
    gap_min = (int(time.time() * 1000) - since_ms) / 60_000
    bar, limit = ("1m", 100) if gap_min <= 98 else ("15m", 100)
    r = requests.get("https://www.okx.com/api/v5/market/candles",
                     params={"instId": f"{base}-USDT", "bar": bar, "limit": limit}, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        return None
    rows = (r.json() or {}).get("data") or []
    bars = sorted((int(x[0]), float(x[2]), float(x[3])) for x in rows)
    return [b for b in bars if b[0] >= since_ms - 60_000] or None


def _bars_twelvedata(symbol: str, since_ms: int, api_key: str, base_url: str) -> Optional[List[Tuple[int, float, float]]]:
    if not api_key:
        return None
    gap_min = max(2, int((time.time() * 1000 - since_ms) / 60_000) + 2)
    r = requests.get(f"{base_url}/time_series",
                     params={"symbol": symbol, "interval": "1min", "outputsize": min(gap_min, 300),
                             "apikey": api_key, "timezone": "UTC"}, timeout=HTTP_TIMEOUT)
    vals = (r.json() or {}).get("values") or []
    bars = []
    for v in vals:
        ts = int(datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
        bars.append((ts, float(v["high"]), float(v["low"])))
    return sorted(bars) or None


def fetch_bars(t: Dict[str, Any], deps: Dict[str, Any]) -> Tuple[Optional[List[Tuple[int, float, float]]], str]:
    since = max(int(t.get("last_ms") or t["armed_ms"]) - 120_000, t["armed_ms"] - 60_000)
    base = t["base"]
    for name, fn in (("coinbase", lambda: _bars_coinbase(base, since)),
                     ("okx", lambda: _bars_okx(base, since)),
                     ("twelvedata", lambda: _bars_twelvedata(t["symbol"], since, deps.get("td_key", ""), deps.get("td_base", "https://api.twelvedata.com")))):
        try:
            bars = fn()
        except Exception as e:                                   # noqa: BLE001 - شبکه: منبع بعدی
            logger.warning(f"custom price source {name} failed for {base}: {e}")
            bars = None
        if bars:
            return bars, name
    return None, ""


# ───────────────────────────── قالب پیام‌ها ─────────────────────────────

def _hashtag(sid: str) -> str:
    return "#" + re.sub(r"[^A-Za-z0-9_]", "_", sid)


def _pf(t: Dict[str, Any], deps: Dict[str, Any]):
    return deps["fmt_for"](t["entry"], risk_of(t))


def _head(t: Dict[str, Any]) -> str:
    return f"{t['base']} {t['tf_label']}".strip() if t.get("tf_label") else t["base"]


def _tail(t: Dict[str, Any], deps: Dict[str, Any]) -> str:
    return f"{deps['now_str']()}\n{_hashtag(t['id'])} #Admin_Signal{deps['risk_line']}"


ORDER_LABEL = {"market": "MARKET", "limit": "LIMIT", "stop_limit": "STOP-LIMIT", "stop_market": "STOP"}


def _targets_lines(t: Dict[str, Any], pf) -> str:
    return "\n".join(f"🎯 Target {i + 1}: {pf(tp)}" for i, tp in enumerate(t["targets"]))


def format_entry(t: Dict[str, Any], deps: Dict[str, Any]) -> str:
    pf = _pf(t, deps)
    arrow = "🟢 LONG" if _is_buy(t) else "🔴 SHORT"
    ot = t["order_type"]
    if t["status"] == "open":                      # market (یا ورود فوری)
        return (f"{arrow} — {_head(t)}\nEntry: <b>{pf(t['entry'])}</b> (market)\n❌ Stop: <b>{pf(t['sl'])}</b>\n"
                f"{_targets_lines(t, pf)}\n\n{_tail(t, deps)}")
    lines = [f"{arrow} {ORDER_LABEL[ot]} ORDER — {_head(t)}", "⏳ <b>Pending order</b> — enter only if price reaches the entry level."]
    if ot == "stop_limit":
        lines.append(f"Trigger: <b>{pf(t['trigger'])}</b>  ·  Limit: <b>{pf(t['entry'])}</b>")
    elif ot == "stop_market":
        lines.append(f"Entry (stop order): <b>{pf(t['entry'])}</b>")
    else:
        lines.append(f"Entry (limit): <b>{pf(t['entry'])}</b>")
    lines += [f"❌ Stop: <b>{pf(t['sl'])}</b>", _targets_lines(t, pf), "",
              "<i>Cancelled automatically if price hits the stop or Target 1 before the order fills.</i>", "", _tail(t, deps)]
    return "\n".join(lines)


def format_event(t: Dict[str, Any], ev: Dict[str, Any], deps: Dict[str, Any]) -> str:
    pf = _pf(t, deps)
    d = "LONG" if _is_buy(t) else "SHORT"
    tail = _tail(t, deps)
    typ = ev["type"]
    if typ == "triggered":
        return (f"🔔 TRIGGERED — {_head(t)}\n\nStop price {pf(ev['price'])} was touched on this {d} stop-limit order; "
                f"the limit order at {pf(t['entry'])} is now live.\n\n{tail}")
    if typ == "fill":
        return (f"✅ ORDER FILLED — {_head(t)}\n\n{ORDER_LABEL[t['order_type']].title()} order filled at {pf(ev['price'])} — "
                f"this {d} trade is now active.\n❌ Stop: {pf(t['sl'])}\n\n{tail}")
    if typ == "cancel":
        why = ("price hit the stop-loss before the order was filled" if ev["reason"] == "stop" else
               "price reached Target 1 before the order was filled (the setup is no longer valid)" if ev["reason"] == "target1" else
               "the order expired without being filled" if ev["reason"] == "expired" else "cancelled by the admin")
        return (f"🚫 ORDER CANCELLED — {_head(t)}\n\nThe pending {d} order was never filled — {why}.\n"
                f"No position was taken: result <b>0.00R</b> (not counted).\n\n{tail}")
    if typ == "target":
        i = ev["index"]
        w = int(round(t["weights"][i] * 100))
        r = target_r(t, i)
        return (f"✅ Target {i + 1} HIT ({r:.2f}R) — {_head(t)}\n\nTarget {i + 1} reached on this {d} trade.\n"
                f"Entry {pf(t['entry'])}  ·  Now {pf(ev['price'])}\n\n✂️ Close {w}% of your position here.\n\n"
                f"📊 Result so far: <b>{ev.get('banked', banked_r(t)):+.2f}R</b> (banked)\n\n{tail}")
    if typ == "all_targets":
        return (f"🏁 ALL TARGETS HIT — {_head(t)}\n\nEvery target was reached on this {d} trade — position fully closed.\n"
                f"📊 Final result: <b>{final_r_for(t):+.2f}R</b>\n\n{tail}")
    if typ == "stop":
        if not any(t["hit"]):
            return (f"❌ STOP HIT — {_head(t)}\n\nStop-loss hit on this {d} trade before Target 1 — full position closed.\n"
                    f"Entry was {pf(t['entry'])}  ·  Stop {pf(ev['price'])}\n\n📊 Result: <b>{final_r_for(t, stopped=True):+.2f}R</b> (loss)\n\n{tail}")
        n = max(i for i, h in enumerate(t["hit"]) if h) + 1
        rem = int(round(remaining_weight(t) * 100))
        res = final_r_for(t, stopped=True)
        return (f"🔒 STOP HIT after Target {n} — {_head(t)}\n\nTargets 1–{n} were already banked; the remaining {rem}% closed at the stop "
                f"({pf(ev['price'])}).\nEntry {pf(t['entry'])}\n\n📊 Final result: <b>{res:+.2f}R</b> "
                f"({'profit' if res > 0 else 'breakeven' if res == 0 else 'loss'})\n\n{tail}")
    if typ == "admin_close":
        res = final_r_for(t, exit_price=ev["price"])
        return (f"⚠️ TRADE CLOSED — {_head(t)}\n\nThis {d} trade was closed manually at {pf(ev['price'])}.\n"
                f"Entry {pf(t['entry'])}\n\n📊 Final result: <b>{res:+.2f}R</b> "
                f"({'profit' if res > 0 else 'breakeven' if res == 0 else 'loss'})\n\n{tail}")
    return ""


CLOSE_LABELS = {
    "stop": "Stopped out (full loss)",
    "stop_after_partial": "Stopped after partial targets (remaining closed at stop)",
    "all_targets": "All targets hit",
    "admin_close": "Closed manually by admin",
}


def history_record(t: Dict[str, Any], final_r: float) -> Dict[str, Any]:
    closed_at = datetime.now(timezone.utc).isoformat()
    labels = [f"T{i + 1}" for i, h in enumerate(t["hit"]) if h]
    opened = t.get("opened_ms")
    tf = (t.get("tf_label") or "").lower() or None
    return {
        "signal_id": t["id"],
        "events": t.get("events", []) + [{"type": "closed", "ts": closed_at, "close_type": t["close_type"], "final_r": final_r}],
        "symbol": t["symbol"], "tf": "manual", "logical_tf": tf, "side": t["side"],
        "entry": t["entry"], "sl": t["sl"], "final_r": final_r, "targets_hit": labels,
        "close_type": t["close_type"], "close_reason": CLOSE_LABELS.get(t["close_type"], t["close_type"]),
        "opened_at": datetime.fromtimestamp(opened / 1000, tz=timezone.utc).isoformat() if opened else None,
        "closed_at": closed_at,
        "r_pct": round(risk_of(t) / t["entry"] * 100.0, 4),
        "mfe_r": t.get("mfe_r"), "mae_r": t.get("mae_r"),
        "quality_tier": None, "strategy": "admin_custom", "htf_trend": None, "htf_aligned": None,
        "stop_pct": None, "fee_r_est": None, "net_r_est": None,
        "custom": True, "order_type": t["order_type"], "targets": t["targets"],
    }


# ───────────────────────────── ذخیره‌سازی ─────────────────────────────

def _load(deps) -> Dict[str, Any]:
    d = deps["read_json"](deps["path"](TRACK_FILE), {"trades": {}})
    if not isinstance(d, dict) or "trades" not in d:
        d = {"trades": {}}
    return d


def _save(deps, d) -> None:
    deps["write_json"](deps["path"](TRACK_FILE), d)


def _post(t: Dict[str, Any], text: str, deps, reply: bool = True) -> Optional[int]:
    if not text:
        return None
    mid = deps["send"](text, t.get("entry_msg_id") if reply else None)
    if mid and not t.get("entry_msg_id"):
        t["entry_msg_id"] = mid
    return mid


def _record_event(t: Dict[str, Any], ev: Dict[str, Any]) -> None:
    t.setdefault("events", []).append({"type": ev["type"], "ts": datetime.now(timezone.utc).isoformat(),
                                       **{k: v for k, v in ev.items() if k in ("price", "index", "reason", "banked")}})


def _finish(t: Dict[str, Any], final_r: float, deps) -> None:
    deps["log_result"](history_record(t, final_r))


# ───────────────────────────── ورودی صف و فرمان‌ها ─────────────────────────────

def build_queue_item(sig: CustomSignal, raw_text: str = "") -> Dict[str, Any]:
    """آیتمی که subscription_bot.py در manual_signals.json می‌نویسد (فیلدهای legacy هم پر است)."""
    return {
        "id": f"cs-{int(time.time())}-{uuid.uuid4().hex[:6]}",
        "symbol": sig.symbol, "side": sig.side,
        "entry": float(sig.entry) if sig.entry is not None else None, "sl": float(sig.stop),
        "status": "pending", "created_at": int(time.time()),
        "forwarded_tracking": False, "tf_label": sig.tf_label, "silent": False, "source_signal_id": None,
        "signal_message_id": None,
        "custom": {"order_type": sig.order_type, "trigger": float(sig.trigger) if sig.trigger is not None else None,
                   "targets": [float(x) for x in sig.targets], "base": sig.base, "raw": (raw_text or "")[:600]},
    }


def build_command_item(action: str, signal_id: str, price: Optional[float] = None) -> Dict[str, Any]:
    return {"id": f"cc-{int(time.time())}-{uuid.uuid4().hex[:6]}", "status": "pending", "created_at": int(time.time()),
            "symbol": "", "side": "", "entry": None, "sl": None,
            "custom_cmd": {"action": action, "id": signal_id, "price": price}}


def summarize(sig: CustomSignal) -> str:
    """خلاصه‌ی فارسی برای تأیید به ادمین."""
    side = "🟢 خرید (Long)" if sig.side == "BUY" else "🔴 فروش (Short)"
    otype = {"market": "Market (بازار)", "limit": "Limit", "stop_limit": "Stop-Limit", "stop_market": "Stop (فعال‌سازی با قیمت)"}[sig.order_type]
    lines = [f"{side} — <b>{sig.base}</b>" + (f" {sig.tf_label}" if sig.tf_label else ""), f"نوع سفارش: <b>{otype}</b>"]
    if sig.order_type == "stop_limit":
        lines.append(f"تریگر: <code>{sig.trigger}</code> · لیمیت: <code>{sig.entry}</code>")
    elif sig.entry is not None:
        lines.append(f"ورود: <code>{sig.entry}</code>" + (" (مرجع؛ ورود با قیمت لحظه‌ی انتشار)" if sig.order_type == "market" else ""))
    else:
        lines.append("ورود: با قیمت لحظه‌ی انتشار")
    lines.append(f"حد ضرر: <code>{sig.stop}</code>")
    lines += [f"تارگت {i + 1}: <code>{p}</code>" for i, p in enumerate(sig.targets)]
    for w in sig.warnings:
        lines.append(f"⚠️ {w}")
    return "\n".join(lines)


def _live_hint(t: Dict[str, Any], deps) -> Optional[float]:
    try:
        bars, _src = fetch_bars({**t, "armed_ms": int(time.time() * 1000) - 180_000, "last_ms": None}, deps)
        if bars:
            hi, lo = bars[-1][1], bars[-1][2]
            return (hi + lo) / 2
    except Exception:                                            # noqa: BLE001
        pass
    return None


def _activate(item: Dict[str, Any], state: Dict[str, Any], deps) -> str:
    """یک آیتم صف را به معامله‌ی در حال رهگیری تبدیل و منتشر می‌کند. وضعیت نهایی آیتم صف را برمی‌گرداند."""
    c = item["custom"]
    buy = item["side"] == "BUY"
    for tr in state["trades"].values():                      # دوپلیکیت: همان نماد/جهت/ورود/حد ضرر هنوز فعال
        if (tr["symbol"] == item["symbol"] and tr["side"] == item["side"] and tr["sl"] == item["sl"]
                and tr["targets"] == c["targets"] and tr["entry"] == (item["entry"] or tr["entry"])):
            deps["alert"](f"custom_dup:{item['id']}", f"{item['symbol']} {item['side']} duplicates an active custom signal ({tr['id']}) — skipped.")
            return "skipped_duplicate"
    t: Dict[str, Any] = {
        "queue_id": item["id"], "symbol": item["symbol"], "base": c["base"], "side": item["side"],
        "order_type": c["order_type"], "entry": item["entry"], "trigger": c.get("trigger"), "sl": item["sl"],
        "targets": list(c["targets"]), "weights": [float(w) for w in target_weights(len(c["targets"]))],
        "hit": [False] * len(c["targets"]), "tf_label": item.get("tf_label"), "status": "pending",
        "armed_ms": int(time.time() * 1000), "last_ms": None, "created_ms": int(time.time() * 1000),
        "events": [], "mfe_r": 0.0, "mae_r": 0.0,
    }
    live = _live_hint(t, deps)
    if t["order_type"] == "market":
        px = live if live else item["entry"]
        if not px:
            deps["alert"](f"custom_noprice:{item['id']}", f"{item['symbol']} market signal NOT published — no live price could be fetched and no reference entry was given. Re-send with an entry price.")
            return "rejected_stale"
        if item["entry"] and live and abs(live - item["entry"]) / item["entry"] * 100 > MARKET_MAX_STALE_PCT:
            deps["alert"](f"custom_stale:{item['id']}", f"{item['symbol']} market signal NOT published — live price {live:.8g} is more than {MARKET_MAX_STALE_PCT}% away from the stated entry {item['entry']}. Re-send with a current price.")
            return "rejected_stale"
        t["entry"] = float(px)
        t["status"] = "open"
        t["opened_ms"] = t["armed_ms"]
    if t["entry"] is None:
        return "rejected_invalid"
    if (buy and t["entry"] <= t["sl"]) or (not buy and t["entry"] >= t["sl"]):
        deps["alert"](f"custom_invalid:{item['id']}", f"{item['symbol']} signal rejected — stop-loss is on the wrong side of the entry.")
        return "rejected_stale"
    if live:                                                   # قیمت از قبل از حد ضرر/تارگت ۱ رد شده؟
        passed_sl = live <= t["sl"] if buy else live >= t["sl"]
        passed_tp = live >= t["targets"][0] if buy else live <= t["targets"][0]
        if passed_sl or passed_tp:
            why = "price is already past the stop-loss" if passed_sl else "price is already past Target 1"
            deps["alert"](f"custom_stale:{item['id']}", f"{item['symbol']} {item['side']} NOT published — {why} (live {live:.8g}). Re-send with a current setup.")
            return "rejected_stale"
    t["id"] = deps["mint_id"]()
    t["expire_ms"] = t["armed_ms"] + PENDING_EXPIRY_HOURS * 3_600_000
    mid = _post(t, format_entry(t, deps), deps, reply=False)
    if not mid:
        deps["alert"](f"custom_send_failed:{item['id']}", f"{item['symbol']} signal could not be posted to the channel (Telegram error) — it was NOT activated. Check logs and re-send.")
        return "error"
    _record_event(t, {"type": "opened" if t["status"] == "open" else "pending", "price": t["entry"]})
    state["trades"][t["id"]] = t
    return "active"


def _apply_command(cmd: Dict[str, Any], state: Dict[str, Any], deps) -> str:
    t = state["trades"].get(cmd["id"]) or next((x for x in state["trades"].values() if x["id"].upper() == str(cmd["id"]).upper().replace("_", "-")), None)
    if not t:
        deps["alert"](f"custom_cmd_nf:{cmd['id']}", f"Custom signal {cmd['id']} not found among active/pending custom signals.")
        return "not_found"
    if cmd["action"] == "cancel":
        if t["status"] != "pending":
            deps["alert"](f"custom_cmd_open:{t['id']}", f"{t['id']} is already open — use close instead of cancel.")
            return "not_found"
        ev = {"type": "cancel", "reason": "admin"}
        t["status"] = "cancelled"
        _record_event(t, ev)
        _post(t, format_event(t, ev, deps), deps)
        del state["trades"][t["id"]]
        return "applied"
    if t["status"] == "pending":
        deps["alert"](f"custom_cmd_pend:{t['id']}", f"{t['id']} is still pending (not filled) — use cancel instead of close.")
        return "not_found"
    price = cmd.get("price")
    if not price:
        price = _live_hint(t, deps)
    if not price:
        deps["alert"](f"custom_cmd_np:{t['id']}", f"Could not fetch a live price to close {t['id']} — give one explicitly: /admin_custom_close {t['id']} PRICE")
        return "not_found"
    t["status"] = "closed"
    t["close_type"] = "admin_close"
    ev = {"type": "admin_close", "price": float(price)}
    _record_event(t, ev)
    _post(t, format_event(t, ev, deps), deps)
    _finish(t, final_r_for(t, exit_price=float(price)), deps)
    del state["trades"][t["id"]]
    return "applied"


def process_queue(deps) -> None:
    """آیتم‌های pending صف (custom / custom_cmd) را پردازش می‌کند. هر آیتم دقیقاً یک‌بار."""
    queue = deps["read_json"](deps["path"]("manual_signals.json"), None)
    if not queue:
        return
    todo = [m for m in queue if m.get("status") == "pending" and (m.get("custom") or m.get("custom_cmd"))]
    if not todo:
        return
    state = _load(deps)
    for m in todo:
        try:
            m["status"] = _apply_command(m["custom_cmd"], state, deps) if m.get("custom_cmd") else _activate(m, state, deps)
            m["activated_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:                                     # noqa: BLE001
            logger.exception("custom queue item failed")
            m["status"] = "error"
            m["reject_reason"] = f"internal error: {e}"
            deps["alert"](f"custom_item_crashed:{m.get('id')}", f"Custom signal item {m.get('id')} crashed and was skipped: {e}")
    _save(deps, state)
    deps["write_json"](deps["path"]("manual_signals.json"), queue)


def track(deps) -> None:
    """معاملات pending/open را با کندل‌های ۱دقیقه‌ای واقعی بررسی و پیام/نتیجه را ثبت می‌کند."""
    state = _load(deps)
    if not state["trades"]:
        return
    now_ms = int(time.time() * 1000)
    changed = False
    cache: Dict[str, Tuple[Optional[list], str]] = {}
    for tid, t in list(state["trades"].items()):
        try:
            interval = (POLL_SECONDS_SLOW if t.get("src") == "twelvedata" else POLL_SECONDS_FAST) * 1000
            if now_ms - (t.get("polled_ms") or 0) < interval:
                continue
            t["polled_ms"] = now_ms
            changed = True
            key = f"{t['base']}|{t.get('last_ms')}|{t['armed_ms']}"
            if key not in cache:
                cache[key] = fetch_bars(t, deps)
            bars, src = cache[key]
            if not bars:
                t["polled_ms"] = now_ms + 52_000          # قطعی منبع‌ها: ~۱ دقیقه عقب‌نشینی تا حلقه‌ی اصلی کند نشود
                if now_ms - (t.get("last_ok_ms") or t["armed_ms"]) > 30 * 60_000 and not t.get("nodata_alerted"):
                    t["nodata_alerted"] = True
                    deps["alert"](f"custom_nodata:{tid}", f"No price data for {t['symbol']} ({tid}) for 30+ minutes from Coinbase/OKX/TwelveData — it is NOT being tracked right now.")
                continue
            t["nodata_alerted"] = False
            t["src"] = src
            t["last_ok_ms"] = now_ms
            events = advance(t, bars)
            t["last_ms"] = bars[-1][0]
            if t["status"] == "pending" and now_ms >= t.get("expire_ms", 1 << 62):
                t["status"] = "cancelled"
                events.append({"type": "cancel", "reason": "expired"})
            last_all = any(e["type"] == "all_targets" for e in events)
            for ev in events:
                _record_event(t, ev)
                if last_all and ev["type"] == "target" and ev["index"] == len(t["targets"]) - 1:
                    continue                      # پیام «ALL TARGETS HIT» جای پیام جدای آخرین تارگت را می‌گیرد
                _post(t, format_event(t, ev, deps), deps)
            if t["status"] == "closed":
                _finish(t, final_r_for(t, stopped=(t["close_type"] == "stop_after_partial" or t["close_type"] == "stop")), deps)
                del state["trades"][tid]
            elif t["status"] == "cancelled":
                del state["trades"][tid]
        except Exception as e:                                       # noqa: BLE001
            logger.exception("custom track failed")
            deps["alert"](f"custom_track_crashed:{tid}", f"Tracking {tid} crashed this tick (will retry): {e}")
    if changed:
        _save(deps, state)


def list_active(deps) -> List[Dict[str, Any]]:
    return list(_load(deps)["trades"].values())

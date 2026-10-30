"""
strategy_modules.py — ماژول مستقل استراتژی‌های جدید (HDV3 / SWP / TS1) برای هر دو ربات.

⚠️ طراحی ایمن (مثل custom_signals.py): کاملاً جدا از مسیر قدیمی سیگنال‌ها.
  - ماشین‌حالت و نردبان تارگت قدیمی (check_open_trade / RR_TARGETS) هیچ تغییری نکرده است.
  - وضعیت معاملات در فایل جدا data/module_trades.json (تک‌نویسنده: خودِ ربات اسکنر).
  - همه‌ی ماژول‌ها پیش‌فرض «خاموش» هستند (shared_controls)؛ تا ادمین با دکمه روشن نکند هیچ سیگنالی نمی‌آید.
  - داده: فقط منبع رایگان shared_market_data.fetch_klines (بدون مصرف سهمیه Twelve Data).
  - نتیجه‌ی بسته‌شده با همان فیلدهای trade_history ثبت می‌شود.

نردبان مدیریت معامله (حداقل RR=2): T1..T4 با وزن‌های مشخص + رانر تریلینگ.
  SL پس از T1 → ورود، پس از Tk (k≥2) → قیمت تارگت قبلی. داخل یک کندل، استاپ مقدم است (محافظه‌کارانه)
  و جابه‌جایی SL از کندل بعد اعمال می‌شود.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("strategy_modules")

TRACK_FILE = "module_trades.json"
ADMIN_FILE = "module_admin_requests.json"      # درخواست‌های ادمین روی معاملات ماژول (پنل → 🔎 Search signal)
SEEN_KEEP = 400

import ladder_catalog as lc

try:                                    # استراتژی‌های تایم‌فریم پایین (قفل تا PASS شدن در بک‌تست)
    import lowtf_strategies as lt
except Exception:                       # noqa: BLE001
    lt = None

# نردبان‌ها: همه از ladder_catalog (چندتارگتی + تک‌تارگتیِ B*). مجموع وزن‌ها ≤1 ؛ باقی‌مانده = رانر.
PROFILES = lc.LADDERS

# پیکربندی ماژول‌های ثابت. نردبان پیش‌فرض (profile) فقط «پشتیبان» است؛ نردبان واقعی = shared_controls.effective_profile
# (auto = بهترین نردبانِ بک‌تست در ladder_catalog، یا انتخاب دستی ادمین).
# ⚠️ SWP: بک‌تست تأییدشده فقط با حد ضرر «حداقل ۳ ATR» (A3) اجرا شده؛ نسخه‌ی ساختاری (nat) در همان گزارش منفی بود
# (SWP|none|nat ≈ −0.1R). پس min_atr_stop=3.0 تا ربات زنده با چیزی که آزموده شده یکی باشد.
MODULE_CFG: Dict[str, Dict[str, Any]] = {
    "HDV3": {"tf": "4h", "profile": "H3", "regime": None, "min_atr_stop": 0.0, "tier": "verified",
             "title": "Hidden Divergence + Inside Bar"},
    "SWP":  {"tf": "4h", "profile": "CLASSIC", "regime": 0, "min_atr_stop": 3.0, "tier": "verified",
             "title": "Liquidity Sweep (range regime)"},
    "TS1":  {"tf": "1h", "profile": "H3", "regime": None, "min_atr_stop": 0.0, "tier": "experimental",
             "title": "Two-Leg Retest"},
}


CANDLE_MODS = dict(lc.CANDLE)           # CN1M.. → تایم‌فریم؛ استراتژی اصلی ربات‌ها (کندل سیگنال)


def lowtf_names() -> List[str]:
    """استراتژی‌های تایم‌فریم پایین (قفل تا PASS) — بدون «اصلی»ها."""
    return [g for g in lt.GENS if g not in lt.ORIG] if lt else []


def _cndl_cfg(module: str, verdicts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """کندل سیگنال: همیشه قابل روشن‌شدن توسط ادمین (استراتژی اصلی ربات است)؛ اگر آزمایشگاه برای همین تایم‌فریم PASS داده
    باشد فیلتر/حد ضرر/نردبانِ بهترین واریانت استفاده می‌شود، وگرنه همان منطق اصلی با نردبان پیش‌فرض."""
    tf = CANDLE_MODS[module]
    v = (verdicts or {}).get(lc.vkey(module)) or {}
    ok = v.get("verdict") == "PASS"
    return {"tf": tf, "profile": lc.best_ladder(module, verdicts), "regime": None,
            "min_atr_stop": lt.STOP_K.get(v.get("stop", "nat"), 0.0) if ok else 0.0, "tier": "lowtf" if ok else "orig",
            "title": f"Candle Signal {tf.upper()}", "filter": v.get("filter", "none") if ok else "none",
            "score": float(v.get("avg_r", 0.0)) if ok else 0.0, "gen": "CNDL"}


def sc_is_candidate(v) -> bool:
    import shared_controls as _sc
    return _sc.is_candidate(v)


def _stop_floor_pct(stop: Any) -> float:
    """سیاستِ استاپِ «F100/F150» (full_backtest V10) = استاپ حداقل ۱٫۰٪/۱٫۵٪ قیمتِ ورود (پهن‌کردن، نه حذفِ سیگنال). 0 = بدون کف درصدی.
    در بک‌تست واقعی (۲۸ نماد) پهن‌کردن استاپ ۹۵–۹۶٪ ترکیب‌ها را بهتر کرد (میانگین +۰٫۱۴ تا +۰٫۱۵R)، چون سهمِ کارمزد ۰٫۱۰٪ از R کم می‌شود."""
    try:
        st = str(stop or "")
        return int(st[1:]) / 100.0 if st.startswith("F") and st[1:].isdigit() else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def cfg_for(module: str, verdicts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """پیکربندی ماژول؛ برای استراتژی‌های تایم‌فریم پایین فقط وقتی verdict=PASS دارند (وگرنه None = قفل)."""
    if module in MODULE_CFG:
        return MODULE_CFG[module]
    if lt and module in CANDLE_MODS:
        return _cndl_cfg(module, verdicts)
    if lt and module in lt.GENS:
        v = (verdicts or {}).get(module) or {}
        cand = v.get("verdict") != "PASS"
        if cand and not sc_is_candidate(v):
            return None
        return {"tf": v["tf"], "profile": (lc.best_ladder(module, verdicts) if cand else v.get("best_ladder")) or "CLASSIC", "regime": None,
                "min_atr_stop": lt.STOP_K.get(v.get("stop", "nat"), 0.0), "min_stop_pct": _stop_floor_pct(v.get("stop")),
                "tier": "candidate" if cand else "lowtf", "title": lt.GENS[module]["title"],
                "filter": v.get("filter", "none"), "score": float(v.get("avg_r", 0.0))}
    return None


TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
KLINE_LIMIT = 720


# ------------------------------------------------------------------ تشخیص (لایو)
def _arrays(klines: List[Dict[str, Any]]):
    import numpy as np
    return {"t": np.array([k["open_time"] for k in klines], dtype=np.int64),
            "o": np.array([k["o"] for k in klines], float), "h": np.array([k["h"] for k in klines], float),
            "l": np.array([k["l"] for k in klines], float), "c": np.array([k["c"] for k in klines], float),
            "v": np.array([(k.get("v") or 0.0) for k in klines], float)}


def _adx(d, n=14):
    import numpy as np
    import pandas as pd
    h, l, c = d["h"], d["l"], d["c"]
    up = np.diff(h, prepend=h[0]); dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    a = 1.0 / n
    atr_ = pd.Series(tr).ewm(alpha=a, adjust=False).mean().values
    pdi = 100 * pd.Series(pdm).ewm(alpha=a, adjust=False).mean().values / np.where(atr_ > 0, atr_, np.nan)
    mdi = 100 * pd.Series(mdm).ewm(alpha=a, adjust=False).mean().values / np.where(atr_ > 0, atr_, np.nan)
    dx = 100 * abs(pdi - mdi) / np.where((pdi + mdi) > 0, pdi + mdi, np.nan)
    out = np.array(pd.Series(dx).ewm(alpha=a, adjust=False).mean().values, dtype=float)
    out[:3 * n] = np.nan
    return out


def regime_last(d) -> int:
    """همان regime آزمایشگاه (strategy_lab.regime) برای آخرین کندل: +1 صعودی، -1 نزولی، 0 رنج، 2 گذار."""
    import numpy as np
    import pandas as pd
    c = pd.Series(d["c"])
    e20 = c.ewm(span=20, adjust=False).mean().values
    e50 = c.ewm(span=50, adjust=False).mean().values
    ax = _adx(d)[-1]
    if math.isnan(ax):
        return 2
    if ax < 20:
        return 0
    if ax >= 22 and e20[-1] > e50[-1] and d["c"][-1] > e50[-1]:
        return 1
    if ax >= 22 and e20[-1] < e50[-1] and d["c"][-1] < e50[-1]:
        return -1
    return 2


def _gen(module: str, d, step_ms: int):
    import shared_strategies as ss
    if module == "HDV3":
        return ss.gen_hdv(d, step_ms, thr=3.0)
    if module == "SWP":
        return ss.gen_sweep(d, step_ms)
    if module == "TS1":
        return ss.gen_two_leg2(d, step_ms, m=2, thr=0.8, size_tol=3.0, retr_min=0.0, sweep=False,
                               need_support=False, entry="retest", min_rr=1.0)
    raise KeyError(module)


def detect(module: str, klines: List[Dict[str, Any]], tf_key: str) -> Optional[Dict[str, Any]]:
    """سیگنال روی «آخرین کندل بسته‌شده» یا None. {side, sl, bar_ms, price, regime}.
    کندل‌ها باید فقط بسته‌شده باشند (fetch_klines همین را تضمین می‌کند)."""
    cfg = MODULE_CFG[module]
    if len(klines) < 120:
        return None
    d = _arrays(klines)
    step_ms = TF_SECONDS[tf_key] * 1000
    idx, side, sl, _tp = _gen(module, d, step_ms)
    n = len(d["c"])
    pick = [j for j in range(len(idx)) if int(idx[j]) == n - 1]
    if not pick:
        return None
    j = pick[-1]
    rg = regime_last(d)
    if cfg["regime"] is not None and rg != cfg["regime"]:
        return None
    s = 1 if int(side[j]) > 0 else -1
    stop = float(sl[j])
    price = float(d["c"][-1])
    if cfg["min_atr_stop"]:
        import shared_strategies as ss
        atr = float(ss._atr(d)[-1])
        if atr == atr:
            need = cfg["min_atr_stop"] * atr
            if abs(price - stop) < need:
                stop = price - s * need
    if not (stop < price if s > 0 else stop > price):
        return None
    return {"side": "BUY" if s > 0 else "SELL", "sl": stop, "bar_ms": int(d["t"][-1]), "price": price, "regime": rg}


# ------------------------------------------------------------------ نردبان
def build_trade(module: str, symbol: str, sig: Dict[str, Any], entry: float, tf_key: str, sid: str,
                now_ms: int, profile: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or MODULE_CFG[module]
    profile = profile if profile in PROFILES else cfg["profile"]
    levels, weights, trail = PROFILES[profile]
    levels, weights = lc.expand4(levels, weights)           # همیشه ۴ تارگت (تارگت‌های مرحله‌ای: فقط جابه‌جایی حد ضرر)
    buy = sig["side"] == "BUY"
    sl = sig["sl"]
    risk = abs(entry - sl)
    tg = [entry + (r * risk if buy else -r * risk) for r in levels]
    return {"id": sid, "module": module, "symbol": symbol, "tf": tf_key, "side": sig["side"], "entry": entry,
            "sl0": sl, "sl": sl, "risk": risk, "targets": tg, "levels": list(levels), "weights": list(weights),
            "trail": trail, "hit": [False] * len(levels), "runner_on": False, "best": entry, "status": "open",
            "opened_ms": now_ms, "armed_ms": now_ms, "last_ms": now_ms, "bar_ms": sig["bar_ms"],
            "mfe_r": 0.0, "mae_r": 0.0, "events": [], "tier": cfg["tier"], "profile": profile}


def _sign(t) -> int:
    return 1 if t["side"] == "BUY" else -1


def banked(t) -> float:
    return sum(w * r for w, r, h in zip(t["weights"], t["levels"], t["hit"]) if h)


def remaining(t) -> float:
    return max(0.0, 1.0 - sum(w for w, h in zip(t["weights"], t["hit"]) if h))


def final_r(t, exit_price: Optional[float] = None) -> float:
    """باقی‌مانده روی exit_price (پیش‌فرض: SL فعلی) بسته می‌شود."""
    px = t["sl"] if exit_price is None else exit_price
    rem = remaining(t)
    r_exit = (px - t["entry"]) * _sign(t) / t["risk"]
    return round(banked(t) + rem * r_exit, 4)


def advance(t: Dict[str, Any], bars: List[Tuple[int, float, float]]) -> List[Dict[str, Any]]:
    """bars: (open_ms, high, low) به ترتیب زمان. وضعیت را در جا تغییر می‌دهد و رویدادها را برمی‌گرداند."""
    ev: List[Dict[str, Any]] = []
    s = _sign(t)
    for ts, hi, lo in bars:
        if t["status"] != "open":
            break
        if ts <= t["last_ms"]:
            continue
        t["last_ms"] = ts
        fav, adv = (hi, lo) if s > 0 else (lo, hi)
        t["mfe_r"] = round(max(t["mfe_r"], (fav - t["entry"]) * s / t["risk"]), 4)
        t["mae_r"] = round(max(t["mae_r"], (t["entry"] - adv) * s / t["risk"]), 4)
        # ۱) استاپ (با SL ابتدای کندل)
        if (s > 0 and lo <= t["sl"]) or (s < 0 and hi >= t["sl"]):
            t["status"] = "closed"
            if t["runner_on"]:
                t["close_type"] = "runner_stop"
            elif not any(t["hit"]):
                t["close_type"] = "stop"
            elif not t["hit"][1]:
                t["close_type"] = "breakeven"
            else:
                t["close_type"] = "stop_after_t%d" % max(i + 1 for i, h in enumerate(t["hit"]) if h)
            ev.append({"type": "stop", "price": t["sl"], "ts": ts, "final_r": final_r(t)})
            return ev
        # ۲) تارگت‌ها
        new_hits = []
        for i, tp in enumerate(t["targets"]):
            if t["hit"][i]:
                continue
            if (s > 0 and hi >= tp) or (s < 0 and lo <= tp):
                t["hit"][i] = True
                new_hits.append(i)
            else:
                break
        for i in new_hits:
            ev.append({"type": "target", "index": i, "price": t["targets"][i], "ts": ts, "banked": round(banked(t), 4)})
        # ۳) جابه‌جایی SL از کندل بعد
        if new_hits:
            k = max(new_hits)
            new_sl = t["entry"] if k == 0 else t["targets"][k - 1]
            if (s > 0 and new_sl > t["sl"]) or (s < 0 and new_sl < t["sl"]):
                t["sl"] = new_sl
        if new_hits and all(t["hit"]) and remaining(t) <= 1e-9:      # نردبان تک‌تارگتی/کاملاً بسته‌شده: معامله همین‌جا تمام می‌شود
            t["status"] = "closed"; t["close_type"] = "all_targets"
            ev.append({"type": "done", "price": t["targets"][-1], "ts": ts, "final_r": final_r(t, t["entry"])})
            return ev
        if all(t["hit"]):
            t["runner_on"] = True
        if t["runner_on"]:
            if (s > 0 and hi > t["best"]) or (s < 0 and lo < t["best"]):
                t["best"] = hi if s > 0 else lo
            trail_sl = t["best"] - s * t["trail"] * t["risk"]
            floor = t["targets"][-2] if len(t["targets"]) > 1 else t["entry"]
            if s > 0:
                trail_sl = max(trail_sl, floor)
            else:
                trail_sl = min(trail_sl, floor)
            if (s > 0 and trail_sl > t["sl"]) or (s < 0 and trail_sl < t["sl"]):
                t["sl"] = trail_sl
    return ev


# ------------------------------------------------------------------ پیام‌ها
# ---- قالب پیام‌ها: عیناً سبک ربات مرجع (candle_engine/SGNL): ورود، تارگت، استاپ، بریک‌ایون، رانر ----
_FOOT = "⚠️ Please manage your risk and capital appropriately."


def _pf(t):
    """فرمت‌کننده‌ی قیمت همین معامله (رقم اعشار از روی R) - همان تابع مشترک دو ربات."""
    try:
        import shared_risk_config as _src
        d = _src.signal_price_decimals(t["entry"], t["risk"])
        return lambda v: _src.format_signal_price(v, decimals=d)
    except Exception:  # noqa: BLE001
        return lambda v: f"{v:.6g}"


def _fmt(deps, v: float, entry: float, risk: float) -> str:
    f = deps.get("fmt_for") if isinstance(deps, dict) else None
    try:
        return str(f(entry, risk)(v)) if f else f"{v:.6g}"
    except Exception:
        return f"{v:.6g}"


def _title(t) -> str:
    if t["module"] == "HOT":
        return f"Hot Mover Pullback ({t.get('trigger', 'CNDL')})"
    if t["module"] in MODULE_CFG:
        return MODULE_CFG[t["module"]]["title"]
    if t["module"] in CANDLE_MODS:
        return f"Candle Signal {CANDLE_MODS[t['module']].upper()}"
    return lt.GENS[t["module"]]["title"] if lt and t["module"] in lt.GENS else t["module"]


def _label(t) -> str:
    return str(t["symbol"]).split("/")[0].upper()


def _dirword(t) -> str:
    return "LONG" if t["side"] == "BUY" else "SHORT"


def _idtag(t) -> str:
    import re
    sid = t.get("id")
    return "\n#" + re.sub(r"[^A-Za-z0-9_]", "_", str(sid)) if sid else ""


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _result_line(r: float) -> str:
    word = "profit" if r > 0 else ("breakeven" if r == 0 else "loss")
    return f"📊 Result so far: <b>{r:+.2f}R</b> ({word})"


def _wrap(head: str, body: str, t) -> str:
    return f"{head}\n{body}\n\n{_now_str()}{_idtag(t)}\n\n{_FOOT}"


def format_entry(t, deps, hint: str = "") -> str:
    pf = _pf(t)
    emoji = "🟢" if t["side"] == "BUY" else "🔴"
    tl = "\n".join(f"🎯 Target {i + 1}: {pf(p)}" for i, p in enumerate(t["targets"]))
    body = f"Entry: <b>{pf(t['entry'])}</b>\n❌ Stop: <b>{pf(t['sl0'])}</b>\n{tl}"
    body += "\n🔒 Stop moves to entry at Target 1, then up to the previous target at each new target."
    if hint:
        body += f"\n{hint}"
    ts = datetime.fromtimestamp(t["bar_ms"] / 1000 + TF_SECONDS.get(t["tf"], 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"{emoji} {_dirword(t)} — {_label(t)} {t['tf'].upper()}\n{body}\n\n{ts}{_idtag(t)}\n\n{_FOOT}"


def remaining_w(t) -> float:
    return max(0.0, 1.0 - sum(t["weights"]))


def _pct(x: float) -> int:
    return int(round(x * 100))


def format_event(t, e, deps) -> str:
    pf = _pf(t); lab = f"{_label(t)} {t['tf'].upper()}"; d = _dirword(t); n_t = len(t["targets"])
    if e["type"] == "target":
        n = e["index"] + 1; r = t["levels"][n - 1]; w = t["weights"][n - 1]; rem = _pct(remaining(t))
        move = "to entry (risk-free)" if n == 1 else f"up to Target {n - 1}"
        if n < n_t and w <= 1e-9:
            act = f"🔒 No profit-taking at this target — move your stop-loss {move} so the trade can no longer lose."
        elif n < n_t:
            act = f"✂️ Close {_pct(w)}% of your total position here, and move your stop-loss {move}."
        elif rem > 0:
            act = (f"✂️ Close {_pct(w)}% of your total position here. The final {rem}% (\"Runner\") stays open with a trailing stop "
                   f"{t['trail']:g}R behind the peak — no fixed exit, so a bigger move keeps paying.")
        else:
            act = f"✂️ Close the remaining {_pct(w)}% of your position here — trade complete."
        body = (f"Target {n} reached on this {d} trade.\nEntry {pf(t['entry'])}  ·  Now {pf(e['price'])}\n\n{act}\n\n"
                f"{_result_line(e['banked'])}")
        return _wrap(f"✅ Target {n} HIT ({r:g}R) — {lab}", body, t)
    if e["type"] == "sl_move":
        flat = abs(e["price"] - t["entry"]) < 1e-12
        body = (f"Stop-loss on this {d} trade has been moved to {'the entry price (risk-free)' if flat else pf(e['price'])}.\n"
                f"Entry {pf(t['entry'])}  ·  New stop {pf(e['price'])}")
        return _wrap(f"🔒 STOP UPDATED — {lab}", body, t)
    res = _result_line(e["final_r"])
    if e["type"] == "done":
        body = f"All {n_t} targets were hit on this {d} trade — position fully closed.\nEntry {pf(t['entry'])}  ·  Final target {pf(e['price'])}\n\n{res}"
        return _wrap(f"🏁 ALL TARGETS HIT — {lab}", body, t)
    ct = str(t.get("close_type", "stop"))
    banked_parts = ", ".join(f"{_pct(w)}% @ Target {i + 1}" for i, (w, h) in enumerate(zip(t["weights"], t["hit"])) if h and w > 1e-9)
    rem = _pct(remaining(t))
    if ct == "breakeven":
        was = f" ({_pct(t['weights'][0])}% was already banked at Target 1)" if t["weights"][0] > 1e-9 else " (stop had been moved to entry after Target 1)"
        body = (f"Price returned to entry on this {d} trade — the remaining {rem}% closed with no loss{was}.\nEntry {pf(t['entry'])}\n\n{res}")
        return _wrap(f"⚪ BREAKEVEN — {lab}", body, t)
    if ct.startswith("stop_after_t"):
        k = int(ct.replace("stop_after_t", "") or 2)
        body = (f"Price came back to the Target {k - 1} level on this {d} trade — the remaining {rem}% closed there, locking in profit "
                f"({(banked_parts + ' already banked') if banked_parts else 'stop had been moved up after the targets'}).\nEntry {pf(t['entry'])}  ·  Closed at {pf(e['price'])}\n\n{res}")
        return _wrap(f"🔒 STOP AFTER TARGET {k} — {lab}", body, t)
    if ct == "runner_stop":
        body = (f"All {n_t} targets were already banked on this {d} trade ({_pct(sum(t['weights']))}% of the position) — the final {rem}% "
                f"runner portion just closed on its trailing stop ({t['trail']:g}R behind the peak). Trade fully complete.\n"
                f"Entry {pf(t['entry'])}  ·  Runner closed at {pf(e['price'])}\n\n{res}")
        return _wrap(f"🏁 RUNNER CLOSED — {lab}", body, t)
    if ct == "admin_close":
        body = (f"This {d} trade was closed manually at market — the remaining {rem}% closed at {pf(e['price'])}.\n"
                f"Entry {pf(t['entry'])}  ·  Closed at {pf(e['price'])}\n\n{res}")
        return _wrap(f"⚠️ TRADE CLOSED — {lab}", body, t)
    if ct == "time_stop":
        body = (f"Maximum holding time reached on this {d} trade — the remaining {rem}% closed at market.\n"
                f"Entry {pf(t['entry'])}  ·  Closed at {pf(e['price'])}\n\n{res}")
        return _wrap(f"⏱ TIME EXIT — {lab}", body, t)
    body = (f"Stop-loss hit on this {d} trade before Target 1 — full position closed.\n"
            f"Entry was {pf(t['entry'])}  ·  Stop {pf(e['price'])}\n\n{res}")
    return _wrap(f"❌ STOP HIT — {lab}", body, t)


def history_record(t) -> Dict[str, Any]:
    closed = datetime.now(timezone.utc).isoformat()
    fr = final_r(t, t.get("exit_px"))
    stop_pct = round(t["risk"] / t["entry"] * 100.0, 4)
    return {"signal_id": t["id"], "events": t["events"] + [{"type": "closed", "ts": closed, "close_type": t["close_type"], "final_r": fr}],
            "symbol": t["symbol"], "tf": t["tf"], "logical_tf": t["tf"], "side": t["side"], "entry": t["entry"], "sl": t["sl0"],
            "final_r": fr, "targets_hit": [f"T{i + 1}" for i, h in enumerate(t["hit"]) if h], "close_type": t["close_type"],
            "close_reason": t["close_type"], "opened_at": datetime.fromtimestamp(t["opened_ms"] / 1000, tz=timezone.utc).isoformat(),
            "closed_at": closed, "r_pct": stop_pct, "mfe_r": t["mfe_r"], "mae_r": t["mae_r"], "quality_tier": t["tier"],
            "strategy": "module:" + t["module"], "htf_trend": None, "htf_aligned": None, "stop_pct": stop_pct,
            "fee_r_est": None, "net_r_est": None, "module": t["module"], "ladder": t.get("profile"),
            "trigger": t.get("trigger"), "hot_move_pct": t.get("hot_move_pct")}


# ------------------------------------------------------------------ اسکن و رهگیری (I/O از طریق deps)
def _load(deps) -> Dict[str, Any]:
    d = deps["read_json"](deps["path"](TRACK_FILE), {"trades": {}, "seen": []})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("trades", {}); d.setdefault("seen", [])
    return d


def _time_stop(t, bars, deps, now: int) -> Optional[Dict[str, Any]]:
    """استراتژی‌های هم‌روز (HOT): بعد از max_hold_ms معامله‌ی باز در قیمت بازار بسته می‌شود (R نهایی = R ثبت‌شده + باقی‌مانده روی همان قیمت)."""
    px = None
    try:
        lp = deps["live_price"](t["symbol"]) if deps.get("live_price") else None
        px = float(lp) if lp else None
    except Exception:
        px = None
    if px is None and bars:
        px = (bars[-1][1] + bars[-1][2]) / 2.0
    if px is None or px <= 0:
        return None                      # قیمت در دسترس نیست؛ تیک بعد دوباره
    t["status"] = "closed"; t["close_type"] = "time_stop"; t["exit_px"] = px
    return {"type": "stop", "price": px, "ts": now, "final_r": final_r(t, px)}


def _bars_for(t, deps) -> List[Tuple[int, float, float]]:
    gap = time.time() * 1000 - t["last_ms"]
    sec = 60 if gap < 11 * 3600e3 else (300 if gap < 55 * 3600e3 else 900)
    ks = deps["klines"](t["symbol"], KLINE_LIMIT, sec)
    return [(k["open_time"] + sec * 1000 - 1, k["h"], k["l"]) for k in ks if k["open_time"] + sec * 1000 > t["last_ms"]]


def _market_px(t, deps, bars) -> Optional[float]:
    px = None
    try:
        lp = deps["live_price"](t["symbol"]) if deps.get("live_price") else None
        px = float(lp) if lp else None
    except Exception:
        px = None
    if px is None and bars:
        px = (bars[-1][1] + bars[-1][2]) / 2.0
    return px if (px and px > 0) else None


# 🔴 رفعِ «بعضی پیام‌های رهگیری/نتیجه‌ی ماژول‌ها در کانال نمی‌آید (و بعد، سیگنال بدون نتیجه می‌ماند)»: قبلاً ارسال «بدون بررسیِ نتیجه»
# بود؛ اگر تلگرام/شبکه یک بار خطا می‌داد پیامِ Target/Stop برای همیشه گم می‌شد ولی وضعیتِ معامله جلو می‌رفت. حالا پیامِ ناموفق در
# صفِ outbox (داخل همان فایل وضعیت) می‌ماند و در تیک‌های بعد، به همان ترتیب و با ریپلای به همان پیامِ سیگنال، دوباره فرستاده می‌شود
# (حداکثر ۶ بار؛ بعدش ادمین مطلع می‌شود). پیام‌های یک معامله هیچ‌وقت جابه‌جا نمی‌شوند: تا پیامِ قبلی نرفته، بعدی منتظر می‌ماند.
OUTBOX_MAX_TRIES = 6


def _emit(deps, st, t, text: str) -> bool:
    rid = _reply_id(t)
    ob = st.setdefault("outbox", [])
    if any(m.get("tid") == t.get("id") for m in ob):
        ob.append({"tid": t.get("id"), "reply": rid, "text": text, "tries": 0})
        return False
    try:
        mid = deps["send"](text, rid)
    except Exception:  # noqa: BLE001
        logger.exception("module send failed")
        mid = None
    if mid:
        return True
    ob.append({"tid": t.get("id"), "reply": rid, "text": text, "tries": 1})
    return False


def _flush_outbox(deps, st) -> bool:
    ob = st.get("outbox") or []
    if not ob:
        return False
    keep, blocked = [], set()
    for m in ob:
        tid = m.get("tid")
        if tid in blocked:
            keep.append(m)
            continue
        try:
            mid = deps["send"](m.get("text", ""), m.get("reply"))
        except Exception:  # noqa: BLE001
            mid = None
        if mid:
            continue
        m["tries"] = int(m.get("tries", 0)) + 1
        if m["tries"] >= OUTBOX_MAX_TRIES:
            logger.error(f"module outbox: giving up on a message for {tid}")
            try:
                deps["alert"](f"module_outbox:{tid}", f"A tracking message for module trade {tid} could not be delivered after "
                                                      f"{OUTBOX_MAX_TRIES} attempts and was dropped — please check the channel.")
            except Exception:  # noqa: BLE001
                pass
            continue
        keep.append(m)
        blocked.add(tid)
    st["outbox"] = keep
    return True


def _finalize(t, st, deps) -> None:
    rec = history_record(t)
    if deps.get("history_fix"):
        deps["history_fix"](rec, t)
    deps["log_result"](rec)
    st["trades"].pop(t["id"], None)


def process_admin(deps, st, now: int) -> bool:
    """درخواست‌های ادمین (close / be / sl) روی معاملات باز ماژول‌ها. وضعیت هر درخواست: pending → applied / not_found / rejected.
    close = بستن در قیمت بازار؛ be = استاپ به نقطه‌ی ورود؛ sl = استاپ به قیمت دلخواه (باید سمت درست قیمت فعلی باشد)."""
    reqs = deps["read_json"](deps["path"](ADMIN_FILE), [])
    if not isinstance(reqs, list) or not any(isinstance(r, dict) and r.get("status") == "pending" for r in reqs):
        return False
    changed_trades = False
    for r in reqs:
        if not isinstance(r, dict) or r.get("status") != "pending":
            continue
        t = st["trades"].get(r.get("id"))
        if not t or t.get("status") != "open":
            r["status"] = "not_found"; r["note"] = "not an open module trade (already closed or unknown id)"
            continue
        try:
            bars = _bars_for(t, deps)
            px = _market_px(t, deps, bars)
            act = r.get("action")
            s = _sign(t)
            if px is None:
                r["attempts"] = int(r.get("attempts", 0)) + 1
                if r["attempts"] >= 10:
                    r["status"] = "rejected"; r["note"] = "no market price available"
                continue
            if bars:                                   # اول کندل‌های معوق را بدون دستکاری پردازش کن (تارگت/استاپ واقعی مقدم است)
                for e in advance(t, bars):
                    t["events"].append({k: v for k, v in e.items() if k in ("type", "index", "price", "banked", "final_r")} |
                                       {"ts": datetime.now(timezone.utc).isoformat()})
                    _emit(deps, st, t, format_event(t, e, deps))
                changed_trades = True
                if t["status"] != "open":
                    r["status"] = "not_found"; r["note"] = "trade closed by the market just before the request"
                    _finalize(t, st, deps)
                    continue
            if act == "close":
                t["status"] = "closed"; t["close_type"] = "admin_close"; t["exit_px"] = px
                e = {"type": "stop", "price": px, "ts": now, "final_r": final_r(t, px)}
                t["events"].append({"type": "stop", "price": px, "final_r": e["final_r"], "ts": datetime.now(timezone.utc).isoformat()})
                _emit(deps, st, t, format_event(t, e, deps))
                r["status"] = "applied"
            elif act in ("be", "sl"):
                new_sl = t["entry"] if act == "be" else float(r.get("value"))
                if (s > 0 and new_sl >= px) or (s < 0 and new_sl <= px):
                    r["status"] = "rejected"; r["note"] = "stop would be on the wrong side of the current price"
                    continue
                t["sl"] = new_sl
                e = {"type": "sl_move", "price": new_sl, "ts": now}
                t["events"].append({"type": "sl_move", "price": new_sl, "ts": datetime.now(timezone.utc).isoformat()})
                _emit(deps, st, t, format_event(t, e, deps))
                r["status"] = "applied"
            else:
                r["status"] = "rejected"; r["note"] = "unknown action"
            changed_trades = True
            if t["status"] == "closed":
                _finalize(t, st, deps)
        except Exception as ex:  # noqa: BLE001
            logger.exception("module admin action failed")
            r["attempts"] = int(r.get("attempts", 0)) + 1
            if r["attempts"] >= 5:
                r["status"] = "rejected"; r["note"] = f"error: {ex}"[:80]
    deps["write_json"](deps["path"](ADMIN_FILE), reqs[-300:])
    return changed_trades


def track(deps) -> None:
    """هر تیک صدا زده شود؛ هر معامله حداکثر دقیقه‌ای یک بار بررسی می‌شود."""
    st = _load(deps)
    flushed = bool(st.get("outbox")) and _flush_outbox(deps, st)
    if not st["trades"]:
        if flushed:
            deps["write_json"](deps["path"](TRACK_FILE), st)
        return
    now = int(time.time() * 1000)
    changed = flushed
    try:
        changed = process_admin(deps, st, now) or flushed
    except Exception:  # noqa: BLE001
        logger.exception("module admin queue failed")
    for tid, t in list(st["trades"].items()):
        try:
            if now - t.get("polled_ms", 0) < 55_000:
                continue
            t["polled_ms"] = now; changed = True
            bars = _bars_for(t, deps)
            if not bars and not t.get("max_hold_ms"):
                continue
            evs = advance(t, bars) if bars else []
            if t["status"] == "open" and t.get("max_hold_ms") and now - t["opened_ms"] >= t["max_hold_ms"]:
                ev_ts = _time_stop(t, bars, deps, now)
                if ev_ts:
                    evs.append(ev_ts)
            for e in evs:
                t["events"].append({k: v for k, v in e.items() if k in ("type", "index", "price", "banked", "final_r")} |
                                   {"ts": datetime.now(timezone.utc).isoformat()})
                _emit(deps, st, t, format_event(t, e, deps))
            if t["status"] == "closed":
                rec = history_record(t)
                if deps.get("history_fix"):
                    deps["history_fix"](rec, t)
                deps["log_result"](rec)
                del st["trades"][tid]
        except Exception as ex:  # noqa: BLE001
            logger.exception("module track failed")
            deps["alert"](f"module_track:{tid}", f"Module trade {tid} tracking error (retrying): {ex}")
    if changed:
        deps["write_json"](deps["path"](TRACK_FILE), st)


_HTF_CACHE: Dict[Tuple[str, int], Tuple[int, Any]] = {}
LOWTF_MAX_SYMBOLS = 8


def _htf_klines(deps, sym: str, sec: int, now: int):
    """کندل‌های HTF با کش: فقط وقتی یک کندل HTF تازه بسته شده دوباره گرفته می‌شود."""
    bucket = now // 1000 // sec
    c = _HTF_CACHE.get((sym, sec))
    if c and c[0] == bucket:
        return c[1]
    ks = deps["klines"](sym, KLINE_LIMIT, sec)
    if ks:
        _HTF_CACHE[(sym, sec)] = (bucket, ks)
    return ks or (c[1] if c else [])


def _verdicts(deps) -> Dict[str, Any]:
    try:
        v = deps["verdicts"]() if deps.get("verdicts") else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _reply_id(t):
    """message_id پیامِ اصلیِ سیگنال برای ریپلای؛ مقدارهای نامعتبر (None / ۰ / ۱ از باگِ قدیمیِ SGNL) → None تا به پیامِ بی‌ربط ریپلای نشود."""
    mid = t.get("msg_id")
    return mid if isinstance(mid, int) and not isinstance(mid, bool) and mid > 1 else None


# 🔴 تارگتِ غیرممکن (مثال زنده: SEI 4H SELL با استاپ ۱۶٫۶٪ و نردبان H3 → «Target 4: -0.01810»): برای SELL آخرین تارگت باید
# حداقل ۵٪ قیمتِ ورود باقی بماند؛ وگرنه سیگنال منتشر/ردیابی نمی‌شود. (برای BUY سقفی وجود ندارد.)
MIN_TARGET_PRICE_FRAC = 0.05


def _publish(deps, st, module, sym, cfg, sig, entry, sid, now, ctl, sc, ks, vd, profile=None, extra=None):
    stop_pct = abs(entry - sig["sl"]) / entry * 100.0
    t = build_trade(module, sym, sig, entry, cfg["tf"], sid, now, profile or sc.effective_profile(ctl, module, vd), cfg)
    if min(t["targets"]) < MIN_TARGET_PRICE_FRAC * entry:
        logger.info(f"module {module} {sym}: blocked - stop {stop_pct:.1f}% wide, last target would be {min(t['targets']):.6g} (< "
                    f"{MIN_TARGET_PRICE_FRAC * 100:.0f}% of entry)")
        _dg(st, module, "blocked", "target_unreachable")
        return False
    if extra:
        t.update(extra)
    hint = sc.risk_hint(ctl, stop_pct)
    cap = format_entry(t, dict(deps, verdicts=lambda: vd), hint)
    png = None
    if deps.get("chart") and deps.get("send_photo"):
        try:
            png = deps["chart"](t, ks)
        except Exception:  # noqa: BLE001
            logger.exception("module chart failed")
    msg_id = deps["send_photo"](cap, png) if (png and len(cap) <= 1000) else deps["send"](cap, None)
    if not msg_id:
        deps["alert"](f"module_send:{module}:{sym}", f"Module {module} signal on {sym} could not be published (Telegram send failed) — not tracked.")
        return False
    t["msg_id"] = msg_id
    st["trades"][sid] = t
    return True


# 🔴 رفعِ «یک سیگنال یکسان دو بار در کانال» (مثال زنده: LINK 1H SHORT با #ALT_000435 و #ALT_000436، همان کندل و همان قیمت‌ها):
# ماژول‌های هم‌خانواده (مثلاً TPB و TPS که TPS زیرمجموعه‌ی TPB است) روی یک نماد/جهت/تایم‌فریم هم‌زمان سیگنال می‌دادند و تنها
# کنترلِ تکرار «هر ماژول/نماد یک معامله» بود. حالا بین همه‌ی ماژول‌ها و موتور اصلی: یک معامله‌ی باز برای هر نماد+جهت+تایم‌فریم.
def _base_sym(sym: Any) -> str:
    s = str(sym or "").upper().replace("-", "/")
    for suf in ("/USDT", "/USDC", "/USD", "USDT", "USD"):
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


def _dup_reason(opens: List[Dict[str, Any]], module: str, sym: str, tf: str, side: str, entry: float, now_ms: int) -> Optional[str]:
    """None = تکراری نیست. وگرنه علت. معاملاتِ بدونِ نماد (نامعلوم) نادیده گرفته می‌شوند."""
    base, side = _base_sym(sym), str(side or "").upper()
    for t in opens or []:
        try:
            if not isinstance(t, dict) or t.get("closed") or t.get("status") not in (None, "open"):
                continue
            if _base_sym(t.get("symbol") or t.get("display")) != base or str(t.get("side") or "").upper() != side:
                continue
            ttf = t.get("tf") or t.get("tf_key")
            if ttf and ttf == tf:
                return f"dup_same_tf:{t.get('module') or 'native'}"
            if ttf:
                continue                                                    # تایم‌فریم مشخص و متفاوت = سیگنالِ جدا (قانونی)
            e2 = float(t.get("entry") or 0)
            opened = int(t.get("opened_ms") or t.get("opened_at_ms") or 0)
            if e2 > 0 and entry > 0 and abs(e2 - entry) / entry <= 0.003 and opened and now_ms - opened <= 2 * 3600 * 1000:
                return f"dup_same_price:{t.get('module') or 'native'}"       # برچسب تایم‌فریم ناقص بود ولی همان سیگنال است
        except Exception:                                                   # noqa: BLE001
            continue
    return None


def _mx_check(st, ctl, module, sym, sig, stop_pct, now) -> Optional[str]:
    """تنظیمات اختصاصی هر استراتژی (پنل: ⚙️). None = مجاز؛ وگرنه دلیل مسدودی."""
    side = ctl.get(f"mx_{module}_side", "both")
    if side == "long" and sig["side"] != "BUY":
        return "side_off"
    if side == "short" and sig["side"] != "SELL":
        return "side_off"
    ms = float(ctl.get(f"mx_{module}_maxstop", 0) or 0)
    if ms > 0 and stop_pct > ms:
        return "max_stop"
    cd = int(ctl.get(f"mx_{module}_cd", 0) or 0)
    if cd > 0 and now - int((st.get("mx_last") or {}).get(f"{module}|{sym}", 0)) < cd * 60000:
        return "cooldown"
    cap = int(ctl.get(f"mx_{module}_day", 0) or 0)
    if cap > 0:
        day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        md = st.get("mx_day") or {}
        if md.get("day") == day and int(md.get(module, 0)) >= cap:
            return "strategy_cap"
    return None


def _mx_mark(st, module, sym, now) -> None:
    st.setdefault("mx_last", {})[f"{module}|{sym}"] = now
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    md = st.setdefault("mx_day", {})
    if md.get("day") != day:
        md.clear(); md["day"] = day
    md[module] = int(md.get(module, 0)) + 1


def scan(deps, symbols: List[str], open_trades_provider: Callable[[], List[Dict[str, Any]]],
         closed_trades_provider: Callable[[], List[Dict[str, Any]]]) -> int:
    """برای هر ماژول فعال و هر نماد، آخرین کندل بسته را بررسی و در صورت سیگنال (و عبور از گیت‌ها) معامله باز می‌کند.
    ماژول‌های تایم‌فریم پایین: فقط با verdict=PASS، سقف روزانه، و ترتیب انتشار = بهترین امید ریاضی بک‌تست اول."""
    import shared_controls as sc
    ctl = deps["controls"]()
    names = list(MODULE_CFG) + list(CANDLE_MODS) + lowtf_names()
    if ctl.get("paused") or not any(ctl.get(f"mod_{m}") for m in names + ["HOT"]):
        return 0
    vd = _verdicts(deps)
    st = _load(deps)
    opened = 0
    now = int(time.time() * 1000)
    _kc: Dict[Tuple[str, int], Any] = {}
    _raw_klines = deps["klines"]

    def _klines_cached(sym, lim, sec):
        k = (sym, sec)
        if k not in _kc:
            _kc[k] = _raw_klines(sym, lim, sec)
        return _kc[k]
    deps = dict(deps, klines=_klines_cached)
    cands: List[Dict[str, Any]] = []
    for module in names:
        cfg = cfg_for(module, vd)
        if cfg is None or not ctl.get(f"mod_{module}") or not ctl.get(f"tf_{cfg['tf']}", True):
            continue
        is_cn = module in CANDLE_MODS
        is_lt = module not in MODULE_CFG and not is_cn          # فقط تایم‌فریم‌پایین سقف روزانه/رتبه‌بندی دارد
        use_lt_path = module not in MODULE_CFG                  # کندل سیگنال و lowtf هر دو با آشکارساز lowtf_strategies
        sec = TF_SECONDS[cfg["tf"]]
        for sym in (symbols[:LOWTF_MAX_SYMBOLS] if is_lt else symbols):
            try:
                key_prefix = f"{module}|{sym}|{cfg['tf']}"
                chk = st.setdefault("checked", {})
                expected = (now // 1000 // sec) * sec * 1000 - sec * 1000   # open_time آخرین کندل بسته
                if chk.get(key_prefix) == expected:
                    continue
                ks = deps["klines"](sym, KLINE_LIMIT, sec)
                if not ks or ks[-1]["open_time"] < expected:
                    _dg(st, module, "nodata")
                    continue  # داده هنوز به‌روز نیست؛ تیک بعد دوباره
                chk[key_prefix] = expected
                _dg(st, module, "checks")
                if use_lt_path:
                    d = _arrays(ks); step_ms = sec * 1000
                    need = [p for p in (900, 3600, 14400, 86400) if p * 1000 > step_ms] \
                        if (not is_cn or cfg.get("filter", "none") != "none") else []   # کندل سیگنالِ بدون فیلتر HTF نمی‌خواهد (صرفه‌جویی API)
                    htf = lt.htf_from_klines({p: _htf_klines(deps, sym, p, now) for p in need})
                    sig = lt.detect_last(cfg.get("gen", module), d, step_ms, htf, cfg.get("filter", "none"))
                    if sig:
                        import shared_strategies as ss
                        atr = float(ss._atr(d)[-1]); s_ = 1 if sig["side"] == "BUY" else -1
                        if cfg["min_atr_stop"] and atr == atr:
                            need_d = cfg["min_atr_stop"] * atr
                            if abs(sig["price"] - sig["sl"]) < need_d:
                                sig["sl"] = sig["price"] - s_ * need_d
                        if cfg.get("min_stop_pct"):                      # کفِ درصدیِ استاپ (F100/F150) - همان قاعده‌ی بک‌تست
                            need_p = cfg["min_stop_pct"] / 100.0 * sig["price"]
                            if abs(sig["price"] - sig["sl"]) < need_p:
                                sig["sl"] = sig["price"] - s_ * need_p
                        # حداقل فاصله‌ی استاپ برای همه‌ی ماژول‌های مسیر lowtf **از جمله کندل سیگنال (CN*)**: بدون آن، استاپ‌های ۰٫۰۴–۰٫۱۶٪
                        # (کمتر از کارمزد رفت‌وبرگشت ۰٫۱۰٪) صادر می‌شد و «تارگت ۱» عملاً ضرر خالص بود (گزارش زنده: کارمزد ≈۰٫۲۴R/معامله).
                        if abs(sig["price"] - sig["sl"]) / sig["price"] * 100.0 < lt.MIN_RISK_PCT:
                            sig = None
                        else:
                            sig["bar_ms"] = int(d["t"][-1]); sig["regime"] = None
                else:
                    sig = detect(module, ks, cfg["tf"])
                if not sig:
                    continue
                _dg(st, module, "raw")
                entry = sig["price"]
                live = deps.get("live_price")
                if live:
                    try:
                        lp = live(sym)
                        if lp:
                            entry = float(lp)
                    except Exception:
                        pass
                risk = abs(entry - sig["sl"])
                if risk <= 0 or (sig["side"] == "BUY") != (sig["sl"] < entry):
                    _dg(st, module, "blocked", "bad_stop")
                    continue
                stop_pct = risk / entry * 100.0
                if use_lt_path and stop_pct < lt.MIN_RISK_PCT:      # ورود با قیمت زنده می‌تواند ریسک را از حد مجاز کمتر کند → دوباره چک
                    _dg(st, module, "blocked", "stop_too_small")
                    continue
                opens = list(open_trades_provider()) + list(st["trades"].values())
                ok, why = sc.gate(ctl, side=sig["side"], stop_pct=stop_pct, now_ms=now, open_trades=opens,
                                  closed_trades=closed_trades_provider(), signals_today=_signals_today(st, opens, now),
                                  tf_key=cfg["tf"], module=module,
                                  manual=(deps["manual_news"]() if deps.get("manual_news") else None))
                if not ok:
                    logger.info(f"module {module} {sym}: blocked by control gate ({why})")
                    _dg(st, module, "blocked", str(why)[:40])
                    continue
                if any(x["module"] == module and x["symbol"] == sym for x in st["trades"].values()):
                    _dg(st, module, "blocked", "already_open")
                    continue  # هر ماژول/نماد هم‌زمان یک معامله
                if _dup_reason(opens, module, sym, cfg["tf"], sig["side"], entry, now):
                    _dg(st, module, "blocked", "duplicate_signal")
                    continue  # همان سیگنال را ماژول/موتور دیگری قبلاً منتشر کرده
                mxr = _mx_check(st, ctl, module, sym, sig, stop_pct, now)
                if mxr:
                    _dg(st, module, "blocked", mxr)
                    continue
                if is_lt:
                    cands.append({"score": cfg.get("score", 0.0), "stop_pct": stop_pct, "module": module, "sym": sym, "cfg": cfg,
                                  "sig": sig, "entry": entry, "ks": ks})
                    continue
                sid = deps["mint_id"]()
                if _publish(deps, st, module, sym, cfg, sig, entry, sid, now, ctl, sc, ks, vd):
                    opened += 1; _dg(st, module, "opened"); _mx_mark(st, module, sym, now)
            except Exception as ex:  # noqa: BLE001
                logger.exception("module scan failed")
                deps["alert"](f"module_scan:{module}:{sym}", f"Module {module} scan error on {sym}: {ex}")
    if cands:                                   # بهترین‌ها اول؛ سقف روزانه‌ی تایم‌فریم پایین
        day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        dc = st.setdefault("daily", {})
        if dc.get("day") != day:
            dc.clear(); dc["day"] = day; dc["lowtf"] = 0
        cap = int(ctl.get("lowtf_max_per_day", 5))
        cands.sort(key=lambda c: (c["cfg"].get("tier") == "candidate", -c["score"], c["stop_pct"]))   # PASS ها اول، بعد کاندیداها
        for c in cands:
            if dc.get("lowtf", 0) >= cap:
                logger.info("low-TF daily cap reached - remaining candidates skipped")
                _dg(st, c["module"], "blocked", "daily_cap")
                break
            mxr = _mx_check(st, ctl, c["module"], c["sym"], c["sig"], c["stop_pct"], now)
            if mxr:
                _dg(st, c["module"], "blocked", mxr)
                continue
            if _dup_reason(list(open_trades_provider()) + list(st["trades"].values()), c["module"], c["sym"], c["cfg"]["tf"], c["sig"]["side"], c["entry"], now):
                _dg(st, c["module"], "blocked", "duplicate_signal")      # کاندیدِ بهتر (امتیاز بالاتر) در همین دور قبلاً منتشر شده
                continue
            try:
                sid = deps["mint_id"]()
                if _publish(deps, st, c["module"], c["sym"], c["cfg"], c["sig"], c["entry"], sid, now, ctl, sc, c["ks"], vd):
                    dc["lowtf"] = dc.get("lowtf", 0) + 1; opened += 1; _dg(st, c["module"], "opened"); _mx_mark(st, c["module"], c["sym"], now)
            except Exception as ex:  # noqa: BLE001
                logger.exception("module publish failed")
                deps["alert"](f"module_pub:{c['module']}", f"Module {c['module']} publish error: {ex}")
    if ctl.get("mod_HOT") and deps.get("hot_any_symbol"):       # HOT فقط در ربات آلت‌کوین (SGNL)؛ ربات BTC/ETH هرگز آلت‌کوین صادر نمی‌کند
        try:
            opened += _scan_hot(deps, ctl, vd, st, now, open_trades_provider, closed_trades_provider, sc,
                                None if deps.get("hot_any_symbol") else set(symbols))
        except Exception as ex:  # noqa: BLE001
            logger.exception("HOT scan failed")
            deps["alert"]("module_scan:HOT", f"Module HOT scan error: {ex}")
    deps["write_json"](deps["path"](TRACK_FILE), st)
    return opened


# ------------------------------------------------------------------ HOT: رمزارزهای داغ + پولبک در جهت حرکت (هم‌روز)
HOT_TRIGGERS = ("CNDL", "SWR", "RSM", "TPB")          # آشکارسازهای موجود که می‌توانند «ماشه» باشند
HOT_NEEDS_HTF = ("TPB",)
HOT_BUDGET_S = 12.0                                    # سقف زمان هر تیک (کندی API نباید حلقه‌ی اصلی را بگیرد)
_HOT_RANK: Dict[str, Any] = {"ts": 0.0, "buy": [], "sell": []}


def hot_rank(deps, ctl, force: bool = False) -> Dict[str, Any]:
    """رتبه‌بندی: از میان hot_universe پرحجم‌ترین جفت‌ها، hot_top_n برنده (BUY) و hot_top_n باخته (SELL) بر پایه‌ی تغییر قیمت
    از بازشدن امروز UTC (حداقل hot_min_move_pct). هر hot_refresh_min دقیقه تازه می‌شود؛ خطا → رتبه‌ی قبلی."""
    now = time.time()
    every = max(2, int(ctl.get("hot_refresh_min", 10))) * 60
    if not force and _HOT_RANK["ts"] and now - _HOT_RANK["ts"] < every:
        return _HOT_RANK
    tk = []
    try:
        tk = deps["tickers"]() if deps.get("tickers") else []
    except Exception:
        tk = []
    if not tk:
        _HOT_RANK["ts"] = now - every + 60          # یک دقیقه بعد دوباره
        return _HOT_RANK
    vmin = float(ctl.get("hot_min_vol_musd", 2.0)) * 1e6
    pool = sorted((x for x in tk if x.get("vol_usd", 0) >= vmin), key=lambda x: -x["vol_usd"])[:int(ctl.get("hot_universe", 40))]
    mv = float(ctl.get("hot_min_move_pct", 3.0)); n = int(ctl.get("hot_top_n", 5))
    up = sorted((x for x in pool if x["chg"] >= mv), key=lambda x: -x["chg"])[:n]
    dn = sorted((x for x in pool if x["chg"] <= -mv), key=lambda x: x["chg"])[:n]
    _HOT_RANK.update(ts=now, buy=[(x["sym"], round(x["chg"], 2)) for x in up], sell=[(x["sym"], round(x["chg"], 2)) for x in dn], pool=len(pool))
    return _HOT_RANK


def pullback_depth(ks, side: str, window_h: int, sec: int) -> Optional[float]:
    """عمق اصلاح فعلی نسبت به حرکت اخیر (۰ = روی قله، ۱ = برگشت کامل). BUY: از کف تا قله‌ی پنجره؛ SELL: برعکس."""
    n = max(6, int(window_h * 3600 / sec))
    seg = ks[-n:]
    if len(seg) < 6:
        return None
    hi = [k["h"] for k in seg]; lo = [k["l"] for k in seg]; c = seg[-1]["c"]
    if side == "BUY":
        ih = max(range(len(hi)), key=lambda i: hi[i]); top = hi[ih]; base = min(lo[:ih + 1])
        move = top - base
        return (top - c) / move if move > 0 and move / max(c, 1e-12) >= 0.005 else None
    il = min(range(len(lo)), key=lambda i: lo[i]); bot = lo[il]; base = max(hi[:il + 1])
    move = base - bot
    return (c - bot) / move if move > 0 and move / max(c, 1e-12) >= 0.005 else None


def _scan_hot(deps, ctl, vd, st, now, open_trades_provider, closed_trades_provider, sc, allowed=None) -> int:
    """allowed=None: همه‌ی جفت‌های رتبه‌بندی‌شده (ربات آلت‌کوین). ربات BTC/ETH (TICB) فقط نمادهای خودش را می‌بیند، پس آلت‌کوین صادر نمی‌کند."""
    t0 = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    dh = st.setdefault("hot_daily", {})
    if dh.get("day") != day:
        dh.clear(); dh["day"] = day; dh["n"] = 0
    if dh["n"] >= int(ctl.get("hot_max_per_day", 4)):
        _dg(st, "HOT", "blocked", "daily_cap")
        return 0
    if ctl.get("hot_session_filter", True):
        h = time.gmtime(now / 1000).tm_hour
        a, b = int(ctl.get("hot_session_from", 7)), int(ctl.get("hot_session_to", 21))
        if not (a <= h < b if a < b else (h >= a or h < b)):
            _dg(st, "HOT", "blocked", "off_session")
            return 0
    rk = hot_rank(deps, ctl)
    st["hot_last"] = {"ts": int(now / 1000), "buy": rk.get("buy", []), "sell": rk.get("sell", []), "pool": rk.get("pool", 0)}
    if not rk.get("buy") and not rk.get("sell"):
        _dg(st, "HOT", "blocked", "no_hot_coins")
        return 0
    tfs = [tf for tf in ("5m", "15m") if ctl.get(f"hot_tf_{tf}") and ctl.get(f"tf_{tf}", True)]
    trigs = [g for g in HOT_TRIGGERS if ctl.get(f"hot_t_{g}")]
    if not tfs or not trigs or lt is None:
        return 0
    cd = st.setdefault("hot_cd", {})
    opened = 0
    pb_lo, pb_hi = float(ctl.get("hot_pb_min", 0.2)), float(ctl.get("hot_pb_max", 0.62))
    for side, lst in (("BUY", rk.get("buy", [])), ("SELL", rk.get("sell", []))):
        for sym, chg in lst:
            if allowed is not None and sym not in allowed:
                continue
            if sym.split("/")[0].upper() in ("BTC", "ETH"):          # BTC/ETH فقط با ربات TICB (شناسه TC)
                continue
            for tf in tfs:
                if time.time() - t0 > HOT_BUDGET_S:
                    _dg(st, "HOT", "blocked", "time_budget")
                    return opened
                if dh["n"] >= int(ctl.get("hot_max_per_day", 4)):
                    _dg(st, "HOT", "blocked", "daily_cap")
                    return opened
                sec = TF_SECONDS[tf]; step_ms = sec * 1000
                key = f"HOT|{sym}|{tf}|{side}"
                chk = st.setdefault("checked", {})
                expected = (now // 1000 // sec) * sec * 1000 - sec * 1000
                if chk.get(key) == expected:
                    continue
                if cd.get(sym, 0) > now:
                    continue
                ks = deps["klines"](sym, KLINE_LIMIT, sec)
                if not ks or ks[-1]["open_time"] < expected:
                    _dg(st, "HOT", "nodata")
                    continue
                chk[key] = expected
                _dg(st, "HOT", "checks")
                depth = pullback_depth(ks, side, int(ctl.get("hot_window_h", 6)), sec)
                if depth is None or not (pb_lo <= depth <= pb_hi):
                    _dg(st, "HOT", "blocked", "no_pullback")
                    continue
                d = _arrays(ks)
                sig = None; used = None
                for g in trigs:
                    htf = {}
                    if g in HOT_NEEDS_HTF:
                        need = [p for p in (3600, 14400) if p * 1000 > step_ms]
                        htf = lt.htf_from_klines({p: _htf_klines(deps, sym, p, now) for p in need})
                    try:
                        x = lt.detect_last(g, d, step_ms, htf, "none")
                    except Exception:  # noqa: BLE001
                        x = None
                    if x and x["side"] == side:           # فقط در جهت حرکت رمزارز داغ
                        sig, used = x, g
                        break
                if not sig:
                    continue
                _dg(st, "HOT", "raw")
                sig["bar_ms"] = int(d["t"][-1]); sig["regime"] = None
                entry = sig["price"]
                try:
                    lp = deps["live_price"](sym) if deps.get("live_price") else None
                    if lp:
                        entry = float(lp)
                except Exception:
                    pass
                risk = abs(entry - sig["sl"])
                if risk <= 0 or (side == "BUY") != (sig["sl"] < entry):
                    _dg(st, "HOT", "blocked", "bad_stop")
                    continue
                stop_pct = risk / entry * 100.0
                if stop_pct < lt.MIN_RISK_PCT:
                    _dg(st, "HOT", "blocked", "stop_too_small")
                    continue
                opens = list(open_trades_provider()) + list(st["trades"].values())
                ok, why = sc.gate(ctl, side=side, stop_pct=stop_pct, now_ms=now, open_trades=opens,
                                  closed_trades=closed_trades_provider(), signals_today=_signals_today(st, opens, now),
                                  tf_key=tf, module="HOT",
                                  manual=(deps["manual_news"]() if deps.get("manual_news") else None))
                if not ok:
                    logger.info(f"module HOT {sym}: blocked by control gate ({why})")
                    _dg(st, "HOT", "blocked", str(why)[:40])
                    continue
                if any(x["module"] == "HOT" and x["symbol"] == sym for x in st["trades"].values()):
                    _dg(st, "HOT", "blocked", "already_open")
                    continue
                prof = sc.effective_profile(ctl, "HOT", vd) if ctl.get("prof_HOT", "auto") not in ("auto", "", None) else None
                if prof is None:                          # auto: نردبان همان استراتژیِ ماشه
                    trig_mod = next((m for m, f in CANDLE_MODS.items() if f == tf), "CN5M") if used == "CNDL" else used
                    prof = sc.effective_profile(ctl, trig_mod, vd)
                cfg = {"tf": tf, "profile": prof, "regime": None, "min_atr_stop": 0.0, "tier": "hot", "title": "Hot Mover Pullback"}
                sid = deps["mint_id"]()
                extra = {"trigger": used, "hot_move_pct": chg, "max_hold_ms": int(ctl.get("hot_max_hold_h", 6)) * 3600 * 1000}
                if _publish(deps, st, "HOT", sym, cfg, sig, entry, sid, now, ctl, sc, ks, vd, profile=prof, extra=extra):
                    opened += 1; dh["n"] += 1; _dg(st, "HOT", "opened")
                    cd[sym] = now + int(ctl.get("hot_cooldown_min", 60)) * 60000
    return opened


def _dg(st, module: str, key: str, sub: str = None) -> None:
    """شمارنده‌ی عیب‌یابی هر ماژول (برای صفحه‌ی Module status در پنل ادمین؛ هیچ اثری روی سیگنال‌دهی ندارد)."""
    try:
        d = st.setdefault("diag", {}).setdefault(module, {"since": int(time.time()), "checks": 0, "nodata": 0, "raw": 0, "blocked": {}, "opened": 0, "last_check": 0})
        if sub:
            d[key][sub] = d[key].get(sub, 0) + 1
        else:
            d[key] = d.get(key, 0) + 1
        if key == "checks":
            d["last_check"] = int(time.time())
    except Exception:
        pass


def _signals_today(st, opens, now_ms) -> int:
    day0 = now_ms - (now_ms % 86400000)
    return sum(1 for t in st["trades"].values() if t.get("opened_ms", 0) >= day0)


def list_active(deps) -> List[Dict[str, Any]]:
    return list(_load(deps)["trades"].values())

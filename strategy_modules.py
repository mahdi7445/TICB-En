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
SEEN_KEEP = 400

# نردبان‌ها: (سطوح R, وزن‌ها, ضریب تریل رانر). مجموع وزن‌ها <1 ؛ باقی‌مانده = رانر.
PROFILES: Dict[str, Tuple[Tuple[float, ...], Tuple[float, ...], float]] = {
    # همان نردبان قدیمی ربات‌ها (shared_risk_config): ۱R/۲R/۴R/۶R با وزن ۲۰/۳۰/۱۵/۱۰٪ و رانر ۲۵٪
    "CLASSIC": ((1.0, 2.0, 4.0, 6.0), (0.20, 0.30, 0.15, 0.10), 1.5),
    "Q1": ((2.0, 3.0, 4.0, 6.0), (0.30, 0.25, 0.20, 0.10), 1.5),
    "Q2": ((2.0, 3.0, 5.0, 8.0), (0.30, 0.25, 0.15, 0.10), 1.5),
    "Q3": ((2.0, 4.0, 6.0, 9.0), (0.35, 0.25, 0.15, 0.10), 2.0),
    "Q5": ((2.0, 3.0, 4.0, 6.0), (0.50, 0.20, 0.10, 0.05), 1.5),
}

# پیکربندی ماژول‌ها. profile/stop بعد از اجرای design-run قابل تنظیم است (فقط همین‌جا).
MODULE_CFG: Dict[str, Dict[str, Any]] = {
    "HDV3": {"tf": "4h", "profile": "Q1", "regime": None, "min_atr_stop": 0.0, "tier": "verified",
             "title": "Hidden Divergence + Inside Bar"},
    "SWP":  {"tf": "4h", "profile": "Q1", "regime": 0, "min_atr_stop": 0.0, "tier": "verified",
             "title": "Liquidity Sweep (range regime)"},
    "TS1":  {"tf": "1h", "profile": "Q1", "regime": None, "min_atr_stop": 0.0, "tier": "experimental",
             "title": "Two-Leg Retest"},
}
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
                now_ms: int, profile: Optional[str] = None) -> Dict[str, Any]:
    cfg = MODULE_CFG[module]
    profile = profile if profile in PROFILES else cfg["profile"]
    levels, weights, trail = PROFILES[profile]
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
_TXT = {
    "en": {"entry": "🎯 <b>{sym} {side}</b> — {title}\n⏱ {tf} | {tier}\nEntry: <code>{entry}</code>\nStop: <code>{sl}</code> ({stop_pct:.2f}%)\n{tgs}\n{hint}\n#{sid}",
           "t": "✅ {sym} #{sid} — Target {n} hit ({price}) | banked {banked:+.2f}R. Stop moved to {sl}.",
           "stop": "🛑 {sym} #{sid} — closed ({reason}) | result {final:+.2f}R",
           "runner": "Runner {pct}%: trailing {trail:g}R", "exp": "EXPERIMENTAL (no statistical confirmation)", "ver": "Backtest-validated (4H)",
           "reasons": {"stop": "stop loss", "breakeven": "break-even", "runner_stop": "runner trailing stop"}},
    "fa": {"entry": "🎯 <b>{sym} {side}</b> — {title}\n⏱ {tf} | {tier}\nورود: <code>{entry}</code>\nحد ضرر: <code>{sl}</code> ({stop_pct:.2f}٪)\n{tgs}\n{hint}\n#{sid}",
           "t": "✅ {sym} #{sid} — تارگت {n} ({price}) | سود ثبت‌شده {banked:+.2f}R. حد ضرر به {sl} منتقل شد.",
           "stop": "🛑 {sym} #{sid} — بسته شد ({reason}) | نتیجه {final:+.2f}R",
           "runner": "رانر {pct}٪: تریلینگ {trail:g}R", "exp": "آزمایشی (بدون تأیید آماری)", "ver": "تأییدشده در بک‌تست (۴ساعته)",
           "reasons": {"stop": "حد ضرر", "breakeven": "ریسک‌فری", "runner_stop": "تریلینگ رانر"}},
}


def _fmt(deps, v: float, entry: float, risk: float) -> str:
    f = deps.get("fmt_for")
    try:
        return str(f(entry, risk)(v)) if f else f"{v:.6g}"
    except Exception:
        return f"{v:.6g}"


def format_entry(t, deps, hint: str = "") -> str:
    L = _TXT[deps.get("lang", "en")]
    sp = t["risk"] / t["entry"] * 100.0
    tgs = "\n".join(f"T{i + 1} ({r:g}R): <code>{_fmt(deps, p, t['entry'], t['risk'])}</code>  [{int(w * 100)}%]"
                    for i, (p, r, w) in enumerate(zip(t["targets"], t["levels"], t["weights"])))
    tgs += "\n" + L["runner"].format(pct=int(round(remaining_w(t) * 100)), trail=t["trail"])
    return L["entry"].format(sym=t["symbol"], side=t["side"], title=MODULE_CFG[t["module"]]["title"], tf=t["tf"].upper(),
                             tier=L["ver"] if t["tier"] == "verified" else L["exp"],
                             entry=_fmt(deps, t["entry"], t["entry"], t["risk"]), sl=_fmt(deps, t["sl0"], t["entry"], t["risk"]),
                             stop_pct=sp, tgs=tgs, hint=hint, sid=t["id"]).replace("\n\n", "\n")


def remaining_w(t) -> float:
    return max(0.0, 1.0 - sum(t["weights"]))


def format_event(t, e, deps) -> str:
    L = _TXT[deps.get("lang", "en")]
    if e["type"] == "target":
        return L["t"].format(sym=t["symbol"], sid=t["id"], n=e["index"] + 1, price=_fmt(deps, e["price"], t["entry"], t["risk"]),
                             banked=e["banked"], sl=_fmt(deps, t["sl"], t["entry"], t["risk"]))
    reason = L["reasons"].get(t.get("close_type"), t.get("close_type", ""))
    if str(t.get("close_type", "")).startswith("stop_after_t"):
        reason = ("stop after T%s" if deps.get("lang", "en") == "en" else "حد ضرر پس از T%s") % t["close_type"][-1]
    return L["stop"].format(sym=t["symbol"], sid=t["id"], reason=reason, final=e["final_r"])


def history_record(t) -> Dict[str, Any]:
    closed = datetime.now(timezone.utc).isoformat()
    fr = final_r(t)
    stop_pct = round(t["risk"] / t["entry"] * 100.0, 4)
    return {"signal_id": t["id"], "events": t["events"] + [{"type": "closed", "ts": closed, "close_type": t["close_type"], "final_r": fr}],
            "symbol": t["symbol"], "tf": t["tf"], "logical_tf": t["tf"], "side": t["side"], "entry": t["entry"], "sl": t["sl0"],
            "final_r": fr, "targets_hit": [f"T{i + 1}" for i, h in enumerate(t["hit"]) if h], "close_type": t["close_type"],
            "close_reason": t["close_type"], "opened_at": datetime.fromtimestamp(t["opened_ms"] / 1000, tz=timezone.utc).isoformat(),
            "closed_at": closed, "r_pct": stop_pct, "mfe_r": t["mfe_r"], "mae_r": t["mae_r"], "quality_tier": t["tier"],
            "strategy": "module:" + t["module"], "htf_trend": None, "htf_aligned": None, "stop_pct": stop_pct,
            "fee_r_est": None, "net_r_est": None, "module": t["module"], "ladder": t.get("profile")}


# ------------------------------------------------------------------ اسکن و رهگیری (I/O از طریق deps)
def _load(deps) -> Dict[str, Any]:
    d = deps["read_json"](deps["path"](TRACK_FILE), {"trades": {}, "seen": []})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("trades", {}); d.setdefault("seen", [])
    return d


def _bars_for(t, deps) -> List[Tuple[int, float, float]]:
    gap = time.time() * 1000 - t["last_ms"]
    sec = 60 if gap < 11 * 3600e3 else (300 if gap < 55 * 3600e3 else 900)
    ks = deps["klines"](t["symbol"], KLINE_LIMIT, sec)
    return [(k["open_time"] + sec * 1000 - 1, k["h"], k["l"]) for k in ks if k["open_time"] + sec * 1000 > t["last_ms"]]


def track(deps) -> None:
    """هر تیک صدا زده شود؛ هر معامله حداکثر دقیقه‌ای یک بار بررسی می‌شود."""
    st = _load(deps)
    if not st["trades"]:
        return
    now = int(time.time() * 1000)
    changed = False
    for tid, t in list(st["trades"].items()):
        try:
            if now - t.get("polled_ms", 0) < 55_000:
                continue
            t["polled_ms"] = now; changed = True
            bars = _bars_for(t, deps)
            if not bars:
                continue
            evs = advance(t, bars)
            for e in evs:
                t["events"].append({k: v for k, v in e.items() if k in ("type", "index", "price", "banked", "final_r")} |
                                   {"ts": datetime.now(timezone.utc).isoformat()})
                deps["send"](format_event(t, e, deps), t.get("msg_id"))
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


def scan(deps, symbols: List[str], open_trades_provider: Callable[[], List[Dict[str, Any]]],
         closed_trades_provider: Callable[[], List[Dict[str, Any]]]) -> int:
    """برای هر ماژول فعال و هر نماد، آخرین کندل بسته را بررسی و در صورت سیگنال (و عبور از گیت‌ها) معامله باز می‌کند."""
    import shared_controls as sc
    ctl = deps["controls"]()
    if ctl.get("paused") or not any(ctl.get(f"mod_{m}") for m in MODULE_CFG):
        return 0
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
    for module, cfg in MODULE_CFG.items():
        if not ctl.get(f"mod_{module}") or not ctl.get(f"tf_{cfg['tf']}", True):
            continue
        sec = TF_SECONDS[cfg["tf"]]
        for sym in symbols:
            try:
                key_prefix = f"{module}|{sym}|{cfg['tf']}"
                chk = st.setdefault("checked", {})
                expected = (now // 1000 // sec) * sec * 1000 - sec * 1000   # open_time آخرین کندل بسته
                if chk.get(key_prefix) == expected:
                    continue
                ks = deps["klines"](sym, KLINE_LIMIT, sec)
                if not ks or ks[-1]["open_time"] < expected:
                    continue  # داده هنوز به‌روز نیست؛ تیک بعد دوباره
                chk[key_prefix] = expected
                sig = detect(module, ks, cfg["tf"])
                if not sig:
                    continue
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
                    continue
                stop_pct = risk / entry * 100.0
                opens = list(open_trades_provider()) + list(st["trades"].values())
                ok, why = sc.gate(ctl, side=sig["side"], stop_pct=stop_pct, now_ms=now, open_trades=opens,
                                  closed_trades=closed_trades_provider(), signals_today=_signals_today(st, opens, now),
                                  tf_key=cfg["tf"], module=module,
                                  manual=(deps["manual_news"]() if deps.get("manual_news") else None))
                if not ok:
                    logger.info(f"module {module} {sym}: blocked by control gate ({why})")
                    continue
                if any(x["module"] == module and x["symbol"] == sym for x in st["trades"].values()):
                    continue  # هر ماژول/نماد هم‌زمان یک معامله
                sid = deps["mint_id"]()
                t = build_trade(module, sym, sig, entry, cfg["tf"], sid, now, sc.effective_profile(ctl, module))
                hint = sc.risk_hint(ctl, stop_pct)
                msg_id = deps["send"](format_entry(t, deps, hint), None)
                if not msg_id:
                    deps["alert"](f"module_send:{module}:{sym}", f"Module {module} signal on {sym} could not be published (Telegram send failed) — not tracked.")
                    continue
                t["msg_id"] = msg_id
                st["trades"][sid] = t
                opened += 1
            except Exception as ex:  # noqa: BLE001
                logger.exception("module scan failed")
                deps["alert"](f"module_scan:{module}:{sym}", f"Module {module} scan error on {sym}: {ex}")
    deps["write_json"](deps["path"](TRACK_FILE), st)
    return opened


def _signals_today(st, opens, now_ms) -> int:
    day0 = now_ms - (now_ms % 86400000)
    return sum(1 for t in st["trades"].values() if t.get("opened_ms", 0) >= day0)


def list_active(deps) -> List[Dict[str, Any]]:
    return list(_load(deps)["trades"].values())

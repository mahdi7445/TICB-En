"""
admin_panel.py — پنل کنترل کامل ادمین فقط با دکمه (بدون تایپ دستور).

ادمین‌ها از /start یک دکمه‌ی «🎛 Admin Panel» می‌بینند. همه‌ی بخش‌ها:
  • فیلترها/ماژول‌ها/تایم‌فریم‌های هر ربات (TICB و SGNL) با روشن/خاموش و تنظیم عدد با − / + (یا تایپ عدد)
  • توقف/ادامه‌ی سیگنال‌ها، فیلتر خبر (+ بلاک دستیِ خبر)، حد ضرر روزانه/هفتگی، ...
  • پل به همه‌ی دستورهای قبلی /admin_*: هر کدام یک دکمه + پرسیدن مرحله‌به‌مرحله‌ی ورودی‌ها
    (مقدار انتخابی با دکمه، فقط اعداد/شناسه‌ها تایپ می‌شوند؛ هیچ «دستور»ی نیاز نیست)
  • کارهای مخرب (حذف کاربر، افزایش/کاهش گروهی) مرحله‌ی تأیید جدا دارند.

این ماژول فقط UI است؛ منطق همان توابع قبلی (handle_admin_command) و shared_controls است.
ctx: dict شامل send, edit, set_await, clear_await, run_cmd, parse_num, controls_path
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import admin_texts as T
import ladder_catalog as lc
try:                                    # اگر فایل گزارش‌ها نباشد، بقیه‌ی پنل باید باز شود
    import perf_report as pr
except Exception:  # noqa: BLE001
    pr = None
import shared_controls as sc

BOTS = {"ticb": "TICB (BTC/ETH VIP)", "sgnl": "SGNL (Altcoins)"}

FILTERS: List[Tuple[str, str, List[str]]] = [
    ("news_filter", "📰 News filter", ["news_before_min", "news_after_min", "news_impact"]),
    ("daily_loss_filter", "📉 Daily loss limit", ["daily_loss_limit_r"]),
    ("weekly_loss_filter", "📆 Weekly loss limit", ["weekly_loss_limit_r"]),
    ("streak_filter", "🧊 Loss-streak breaker", ["streak_losses", "streak_pause_min"]),
    ("max_open_filter", "📂 Max open trades", ["max_open_trades"]),
    ("max_per_day_filter", "🔢 Max signals/day", ["max_signals_per_day"]),
    ("same_side_filter", "↔️ Same-side cap", ["max_same_side"]),
    ("min_stop_filter", "📏 Min stop %", ["min_stop_pct"]),
    ("max_stop_filter", "📐 Max stop %", ["max_stop_pct"]),
    ("show_risk_hint", "💡 Risk hint in signals", ["risk_pct"]),
]
PARAM_LABEL = {
    "news_before_min": "Block minutes BEFORE news", "news_after_min": "Block minutes AFTER news",
    "news_impact": "Min impact (1 low/2 med/3 high)", "daily_loss_limit_r": "Daily loss limit (R)",
    "weekly_loss_limit_r": "Weekly loss limit (R)", "streak_losses": "Consecutive losses",
    "streak_pause_min": "Pause (minutes)", "max_open_trades": "Max open trades",
    "max_signals_per_day": "Max signals per day", "max_same_side": "Max same-direction trades",
    "min_stop_pct": "Min stop (%)", "max_stop_pct": "Max stop (%)", "risk_pct": "Risk per trade (%)",
}
PARAM_LABEL.update({
    "hot_top_n": "Coins per side (top N)", "hot_universe": "Universe size (by volume)", "hot_min_move_pct": "Min move today (%)",
    "hot_min_vol_musd": "Min 24h volume (M$)", "hot_refresh_min": "Ranking refresh (min)", "hot_window_h": "Pullback window (hours)",
    "hot_session_from": "Session start (UTC hour)", "hot_session_to": "Session end (UTC hour)",
    "hot_pb_min": "Min pullback depth", "hot_pb_max": "Max pullback depth",
    "hot_max_hold_h": "Max holding (hours)", "hot_max_per_day": "Max HOT signals per day", "hot_cooldown_min": "Cooldown per coin (min)",
})
HOT_GROUPS: Dict[str, Tuple[str, List[str], List[str]]] = {
    "sel": ("🎯 Coin selection", ["hot_top_n", "hot_universe", "hot_min_move_pct", "hot_min_vol_musd", "hot_refresh_min", "hot_window_h",
                                  "hot_session_from", "hot_session_to"], ["hot_session_filter"]),
    "ent": ("🚪 Entry (pullback + trigger)", ["hot_pb_min", "hot_pb_max"],
            ["hot_tf_5m", "hot_tf_15m", "hot_t_CNDL", "hot_t_SWR", "hot_t_RSM", "hot_t_TPB"]),
    "rsk": ("🛡 Limits & time exit", ["hot_max_hold_h", "hot_max_per_day", "hot_cooldown_min"], []),
}
HOT_BOOL = {"hot_session_filter": "Only in volatile sessions", "hot_tf_5m": "Trigger on 5m", "hot_tf_15m": "Trigger on 15m",
            "hot_t_CNDL": "Trigger: Candle Signal", "hot_t_SWR": "Trigger: Sweep & Reclaim", "hot_t_RSM": "Trigger: RSI Reversal",
            "hot_t_TPB": "Trigger: Trend Pullback"}
HOT_KEYS = {k for _, ps, bs in HOT_GROUPS.values() for k in ps + bs}


def _hot_group(key: str) -> str:
    return next(g for g, (_, ps, bs) in HOT_GROUPS.items() if key in ps or key in bs)


STEP = {"hot_top_n": 1, "hot_universe": 5, "hot_min_move_pct": 0.5, "hot_min_vol_musd": 1.0, "hot_refresh_min": 5, "hot_window_h": 1,
        "hot_session_from": 1, "hot_session_to": 1, "hot_pb_min": 0.05, "hot_pb_max": 0.05, "hot_max_hold_h": 1, "hot_max_per_day": 1,
        "hot_cooldown_min": 15,
        "lowtf_max_per_day": 1, "news_before_min": 15, "news_after_min": 15, "news_impact": 1, "daily_loss_limit_r": 0.5,
        "weekly_loss_limit_r": 1.0, "streak_losses": 1, "streak_pause_min": 60, "max_open_trades": 1,
        "max_signals_per_day": 1, "max_same_side": 1, "min_stop_pct": 0.05, "max_stop_pct": 0.5, "risk_pct": 0.25}

# پل دستورها: key -> (عنوان, قالب دستور, [مراحل]) ؛ هر مرحله: (پرسش, [گزینه‌ها یا None])
# مقدار "-" در مراحل اختیاری حذف می‌شود. confirm=True ⇒ مرحله‌ی تأیید.
CMDS: Dict[str, Dict[str, Any]] = {
    "list":    {"t": "📋 Subscribers", "c": "/admin_list {0}", "s": [("Page number:", ["1", "2", "3", "4", "5"])]},
    "status":  {"t": "📊 System status", "c": "/admin_status", "s": []},
    "user":    {"t": "🔎 Find user", "c": "/admin_user {0}", "s": [("Send the user ID (number):", None)]},
    "add":     {"t": "➕ Add / extend user", "c": "/admin_add {0} {1}", "s": [("User ID:", None), ("Days to add:", ["7", "30", "90", "365"])]},
    "reduce":  {"t": "➖ Reduce days", "c": "/admin_reduce {0} {1}", "s": [("User ID:", None), ("Days to subtract:", ["7", "30", "90"])]},
    "remove":  {"t": "🗑 Remove user", "c": "/admin_remove {0}", "s": [("User ID:", None)], "confirm": True},
    "bext":    {"t": "🎁 Bulk extend all", "c": "/admin_bulk_extend {0} {1}", "s": [("Days:", ["1", "3", "7", "30"]), ("Reason (or - to skip):", None)], "confirm": True},
    "bred":    {"t": "⏬ Bulk reduce all", "c": "/admin_bulk_reduce {0} {1}", "s": [("Days:", ["1", "3", "7"]), ("Reason (or - to skip):", None)], "confirm": True},
    "prices":  {"t": "💲 View prices", "c": "/admin_prices", "s": []},
    "setprice": {"t": "✏️ Set plan price", "c": "/admin_set_price {0} {1}", "s": [("Plan:", ["1m", "3m", "6m", "12m"]), ("New price in USD:", None)]},
    "dlist":   {"t": "🏷 Discount codes", "c": "/admin_discount_list", "s": []},
    "dadd":    {"t": "➕ New discount code", "c": "/admin_discount_add {0} {1} {2} {3}",
                "s": [("Code (letters/numbers):", None), ("Percent off:", ["5", "10", "20", "30", "50"]), ("Max uses (or - for unlimited):", None), ("Valid for how many days (or -):", None)]},
    "drem":    {"t": "🗑 Remove discount", "c": "/admin_discount_remove {0}", "s": [("Code:", None)]},
    "pause":   {"t": "⏸ Maintenance pause (all)", "c": "/admin_pause {0}", "s": [("Reason (or - to skip):", None)], "confirm": True},
    "resume":  {"t": "▶️ Resume + compensate", "c": "/admin_resume", "s": [], "confirm": True},
    "close":   {"t": "🛑 Force-close trade", "c": "/admin_close_trade {0} {1}", "s": [("Signal ID (e.g. TC-004821):", None), ("Final R (or - for default):", None)], "confirm": True},
    "manual":  {"t": "✍️ Manual signal", "c": "/admin_manual_signal {0} {1} {2} {3} {4}",
                "s": [("Symbol (e.g. BTC/USD):", None), ("Side:", ["BUY", "SELL"]), ("Timeframe:", ["1M", "5M", "15M", "1H", "4H"]), ("Entry price:", None), ("Stop-loss price:", None)]},
    "custom":  {"t": "🧾 Personal signals", "c": "/admin_custom", "s": []},
    "ccancel": {"t": "❌ Cancel personal order", "c": "/admin_custom_cancel {0}", "s": [("Signal ID:", None)]},
    "cclose":  {"t": "🔒 Close personal trade", "c": "/admin_custom_close {0} {1}", "s": [("Signal ID:", None), ("Price (or - for market):", None)], "confirm": True},
    "photos":  {"t": "🖼 Resend approved photos", "c": "/admin_photos {0}", "s": [("How many (1-50):", ["5", "10", "20"])]},
    "help":    {"t": "📖 Admin command help", "c": "/admin_help", "s": []},
}
CMD_GROUPS = [
    ("👥 Users", ["list", "user", "add", "reduce", "remove", "bext", "bred"]),
    ("💰 Pricing & discounts", ["prices", "setprice", "dlist", "dadd", "drem"]),
    ("🛠 Trades & system", ["status", "close", "pause", "resume", "photos"]),
    ("✍️ Signals (manual)", ["manual", "custom", "ccancel", "cclose"]),
    ("📖 Help", ["help"]),
]


PROFILE_DESC = lc.DESC_EN   # توضیح نردبان‌ها از کاتالوگ مشترک


def _fit(body: str, help_: str, limit: int = 4000) -> str:
    """متن + توضیح انتهایی؛ اگر طولانی بود «بدنه» کوتاه می‌شود (نه توضیح)، چون سقف پیام تلگرام ۴۰۹۶ است."""
    room = limit - len(help_) - 4
    if len(body) > room:
        body = body[:max(0, room)].rsplit("\n", 1)[0] + "\n…"
    return body + help_


def _on(v: bool) -> str:
    return "✅" if v else "⬜"


def _num(v: Any) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _back(to: str = "adm:home") -> List[Dict[str, str]]:
    return [{"text": "⬅️ Back", "callback_data": to}, {"text": "🏠 Menu", "callback_data": "menu:home"}]


def _path(ctx) -> Optional[str]:
    return ctx.get("controls_path")


def _status_line(sec: str, ctx) -> str:
    c = sc.load(sec, _path(ctx))
    act = [n for k, n, _ in FILTERS if c.get(k) and k != "show_risk_hint"]
    mods = [m for m in sc.MODULES if c.get(f"mod_{m}")]
    return (f"<b>{BOTS[sec]}</b>\n"
            f"Signals: {'⏸ PAUSED' if c['paused'] else '▶️ running'}\n"
            f"New modules ON: {', '.join(mods) if mods else 'none'}\n"
            f"Active filters: {', '.join(act) if act else 'none'}")


# ------------------------------------------------------------------ نمایش صفحات
def home(ctx) -> Tuple[str, List[List[Dict[str, str]]]]:
    rows = [[{"text": f"🤖 {BOTS['ticb']}", "callback_data": "adm:ticb:menu"}],
            [{"text": f"🤖 {BOTS['sgnl']}", "callback_data": "adm:sgnl:menu"}]]
    for i, (title, _keys) in enumerate(CMD_GROUPS):
        rows.append([{"text": title, "callback_data": f"adm:grp:{i}"}])
    rows.append([{"text": "📈 Reports", "callback_data": "adm:rep:a"}, {"text": "🪜 Ladder designer", "callback_data": "adm:ldr"}])
    rows.append([{"text": "🔒 Public stats start date", "callback_data": "adm:pub"}])
    rows.append([{"text": "🚨 Emergency: pause BOTH bots", "callback_data": "adm:panic"}])
    rows.append([{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}])
    pf = sc.get_public_from(_path(ctx)) or "everything"
    return _fit("🎛 <b>Admin Panel</b>\nEverything here works with buttons only.\n\n" + _status_line("ticb", ctx) + "\n\n" +
                _status_line("sgnl", ctx) + f"\nPublic stats shown from: <b>{pf}</b>",
                T.block("home")), rows


def sec_menu(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    rows = [
        [{"text": ("▶️ Resume signals" if c["paused"] else "⏸ Pause signals"), "callback_data": f"adm:{sec}:tg:paused"}],
        [{"text": "🧩 Strategy modules", "callback_data": f"adm:{sec}:mods"}, {"text": "🕒 Timeframes", "callback_data": f"adm:{sec}:tfs"}],
        [{"text": "🛡 Safety filters", "callback_data": f"adm:{sec}:flt"}, {"text": "📰 News", "callback_data": f"adm:{sec}:news"}],
        [{"text": "♻️ Reset all to defaults", "callback_data": f"adm:{sec}:reset"}],
        _back(),
    ]
    return _fit(_status_line(sec, ctx), T.block("sec_menu")), rows


TF_GROUPS = [("low", "⚡ Low timeframe (1m · 5m)", ("1m", "5m")),
             ("mid", "⏱ Mid timeframe (15m · 1h)", ("15m", "1h")),
             ("high", "🕓 High timeframe (4h)", ("4h",))]


def mods_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    vd = sc.load_verdicts()
    avail = list(sc.BASE_MODULES) + list(sc.HOTMODS) + list(sc.CANDLE)
    avail += [m for m in sc.LOWTF if sc.lowtf_status(m, vd) in ("PASS", "CAND")]
    lines = ["🧩 <b>Strategy modules</b> (new, OFF by default)"]
    rows = [_back(f"adm:{sec}:menu")]
    for _key, title, tfs in TF_GROUPS:
        ms = sorted([m for m in avail if _tf_of(m, vd) in tfs], key=lambda x: (TF_ORDER.get(_tf_of(x, vd), 9), x))
        if not ms:
            continue
        lines.append(f"\n<b>{title}</b>")
        for m in ms:
            tf = _tf_of(m, vd)
            note = sc.MODULE_INFO[m][1] if m not in sc.LOWTF else ("lab PASS" if sc.lowtf_status(m, vd) == "PASS" else "near-pass")
            lines.append(f"{_on(c['mod_' + m])} <b>{m}</b> — {tf.upper()} — {note}")
            eff = sc.effective_profile(c, m, vd)
            rows.append([{"text": f"{_on(c['mod_' + m])} {m} ({tf.upper()})", "callback_data": f"adm:{sec}:tg:mod_{m}"},
                         {"text": f"🪜 {eff}{' ⭐' if c['prof_' + m] == 'auto' else ''}", "callback_data": f"adm:{sec}:lad:{m}"},
                         {"text": "ℹ️", "callback_data": f"adm:{sec}:sd:{m}"}])
    lines.append("\nCN* = the bots' ORIGINAL candle-signal strategy per timeframe. Turning one ON replaces the old automatic signal of that "
                 "timeframe (BTC/ETH) with the module version that uses the ladder you choose.")
    lines.append(f"\n🪜 = profit-taking ladder per strategy (⭐ = best ladder from the backtest). Low-timeframe daily cap: {c['lowtf_max_per_day']} signals/day.")
    rows.append([{"text": "🔥 Hot movers settings", "callback_data": f"adm:{sec}:hot"}])
    rows.append([{"text": "➖", "callback_data": f"adm:{sec}:st:lowtf_max_per_day:-1"},
                 {"text": f"Daily cap {c['lowtf_max_per_day']}", "callback_data": f"adm:{sec}:mods"},
                 {"text": "➕", "callback_data": f"adm:{sec}:st:lowtf_max_per_day:1"}])
    rows.append([{"text": "⭐ Use recommended ladders for all", "callback_data": f"adm:{sec}:rec"}])
    rows.append([{"text": "📡 Module status", "callback_data": f"adm:{sec}:diag"}])
    return _fit("\n".join(lines), T.block("mods")), rows


def _verdicts_local():
    try:
        return sc.load_verdicts()
    except Exception:
        return {}


def lad_page(sec: str, m: str, ctx):
    c = sc.load(sec, _path(ctx))
    vd = _verdicts_local()
    rec = lc.best_ladder(m, vd)
    eff = sc.effective_profile(c, m, vd)
    stats = lc.stats_for(m, vd)
    lines = [f"🪜 <b>{m} — profit-taking ladder</b>",
             f"Now: <b>{eff}</b>{' (recommended)' if c['prof_' + m] == 'auto' else ' (manual choice)'}",
             (f"\n⭐ <b>Recommended: {rec}</b> — highest net R/trade among the ladders tested for this strategy"
              " (near ties are within statistical noise).") if stats else
             f"\n⭐ <b>Default: {rec}</b> — no backtest result yet for this strategy, so the bots' current ladder is used until the lab report arrives.",
             "", "Backtest per ladder:"]
    for p in lc.ORDER:
        st = stats.get(p)
        mark = "⭐ " if p == rec else ""
        lines.append(f"• {mark}<b>{p}</b> — {PROFILE_DESC[p]}" + (f"\n   {lc.fmt_stat_line(p, st, 'en')}" if st else "\n   (not tested for this strategy)"))
    lines.append("\nChanges apply to NEW signals only; open trades keep their ladder. The channel chart follows the chosen ladder.")
    rows = [[{"text": f"⭐ Use recommended ({rec})", "callback_data": f"adm:{sec}:sp:{m}:auto"}]]
    cur = []
    for p in lc.ORDER:
        cur.append({"text": ("✅ " if eff == p and c['prof_' + m] != 'auto' else "") + p + (" ⭐" if p == rec else ""),
                    "callback_data": f"adm:{sec}:sp:{m}:{p}"})
        if len(cur) == 3:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    rows.append([{"text": "🪜 Ladder designer (make your own)", "callback_data": "adm:ldr"}])
    rows.append(_back(f"adm:{sec}:" + "mods"))
    return _fit("\n".join(lines), T.block("ladders_note")), rows


def sd_page(sec: str, m: str, ctx):
    """توضیح کامل یک استراتژی (منطق، تایم‌فریم، فیلتر/استاپ بهینه، نتیجه‌ی بک‌تست، وضعیت فعلی)."""
    import strategy_texts as stx
    c = sc.load(sec, _path(ctx))
    vd = _verdicts_local()
    v = vd.get(lc.vkey(m))
    eff = sc.effective_profile(c, m, vd)
    cfg_line = f"وضعیت در {BOTS[sec]}: {'روشن' if c.get('mod_' + m) else 'خاموش'} | نردبان فعلی: {eff} — {lc.describe(eff)}"
    if m in sc.LOWTF:
        cfg_line += f" | وضعیت آزمایشگاه: {sc.lowtf_status(m, vd)}"
    body = f"ℹ️ <b>{m}</b>\n\n" + stx.describe(m, v, cfg_line)
    return _fit(body, ""), [_back(f"adm:{sec}:" + "mods")]


TF_ORDER = {"1m": 0, "5m": 1, "15m": 2, "1h": 3, "4h": 4}


def _tf_of(m: str, vd: Dict[str, Any]) -> str:
    """تایم‌فریم نمایشی یک استراتژی: برای کم‌تایم‌فریم‌ها تایم‌فریم بهترین واریانت بک‌تست، وگرنه اولین تایم‌فریم مجازش."""
    if m in sc.MODULE_INFO and sc.MODULE_INFO[m][0] != "?":
        return sc.MODULE_INFO[m][0]
    v = vd.get(m) or {}
    if v.get("tf") in TF_ORDER:
        return v["tf"]
    try:
        import lowtf_strategies as _lt
        return _lt.GENS[m]["tfs"][0]
    except Exception:
        return "5m"


def _hot_lists(sec: str, ctx) -> str:
    """رمزارزهای داغ فعلی (فقط TICB: از فایل وضعیت ماژول‌ها)."""
    import json
    if sec != "ticb":
        return "Current hot lists are in the SGNL bot log (its state lives in its own repo)."
    try:
        import strategy_modules as _sm
        with open(os.path.join(ctx.get("data_dir") or "data", _sm.TRACK_FILE), encoding="utf-8") as f:
            hl = (json.load(f) or {}).get("hot_last") or {}
    except Exception:
        hl = {}
    if not hl:
        return "Hot lists: not computed yet (module OFF or no scan yet)."
    fmt = lambda xs: ", ".join(f"{a.split('/')[0]} {b:+.1f}%" for a, b in xs) or "—"
    return f"🟢 Gainers (BUY only): {fmt(hl.get('buy', []))}\n🔴 Losers (SELL only): {fmt(hl.get('sell', []))}"


def hot_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    vd = _verdicts_local()
    tfs = "/".join(t.upper() for t in ("5m", "15m") if c.get(f"hot_tf_{t}")) or "none"
    trg = "/".join(g for g in ("CNDL", "SWR", "RSM", "TPB") if c.get(f"hot_t_{g}")) or "none"
    sess = f"{c['hot_session_from']:02d}–{c['hot_session_to']:02d} UTC" if c["hot_session_filter"] else "all day"
    lines = [f"🔥 <b>Hot movers + pullback (HOT)</b> — {BOTS[sec]}",
             f"Status: {_on(c['mod_HOT'])} {'ON' if c['mod_HOT'] else 'OFF'} · ladder: {sc.effective_profile(c, 'HOT', vd)}"
             + (" (auto = ladder of the trigger strategy)" if c.get("prof_HOT") == "auto" else ""),
             f"Top {c['hot_top_n']} gainers + top {c['hot_top_n']} losers of the {c['hot_universe']} most liquid coins · move ≥ {c['hot_min_move_pct']:g}% today",
             f"Trigger on {tfs} with {trg} · pullback {c['hot_pb_min']:g}–{c['hot_pb_max']:g} of the move · session {sess}",
             f"Max {c['hot_max_per_day']}/day · cooldown {c['hot_cooldown_min']} min · closes after {c['hot_max_hold_h']} h at the latest",
             "", _hot_lists(sec, ctx)]
    rows = [[{"text": f"{_on(c['mod_HOT'])} HOT {'ON' if c['mod_HOT'] else 'OFF'}", "callback_data": f"adm:{sec}:tgh:mod_HOT"},
             {"text": f"🪜 {sc.effective_profile(c, 'HOT', vd)}{' ⭐' if c.get('prof_HOT') == 'auto' else ''}", "callback_data": f"adm:{sec}:lad:HOT"},
             {"text": "ℹ️", "callback_data": f"adm:{sec}:sd:HOT"}]]
    rows += [[{"text": t, "callback_data": f"adm:{sec}:hg:{g}"}] for g, (t, _, _) in HOT_GROUPS.items()]
    rows.append([{"text": "📡 Module status", "callback_data": f"adm:{sec}:diag"}])
    rows.append(_back(f"adm:{sec}:mods"))
    return _fit("\n".join(lines), T.block("hot")), rows


def hg_page(sec: str, grp: str, ctx):
    c = sc.load(sec, _path(ctx))
    title, nums, bools = HOT_GROUPS[grp]
    lines = [f"{title} — {BOTS[sec]}\n"]
    rows = []
    for b in bools:
        rows.append([{"text": f"{_on(c[b])} {HOT_BOOL[b]}", "callback_data": f"adm:{sec}:tg:{b}"}])
    for q in nums:
        lines.append(f"{PARAM_LABEL[q]}: <b>{_num(c[q])}</b>")
        rows.append([{"text": "➖", "callback_data": f"adm:{sec}:st:{q}:-1"},
                     {"text": f"{PARAM_LABEL[q][:22]}: {_num(c[q])}", "callback_data": f"adm:{sec}:ty:{q}"},
                     {"text": "➕", "callback_data": f"adm:{sec}:st:{q}:1"}])
    rows.append([{"text": "⬅️ Back", "callback_data": f"adm:{sec}:hot"}])
    det = "\n".join([f"• {HOT_BOOL[b]}: {T.HOT_BOOL_DETAIL.get(b, '')}" for b in bools] +
                    [f"• {PARAM_LABEL[q]}: {T.PARAM_DETAIL.get(q, '')}" for q in nums])
    return _fit("\n".join(lines) + "\n<i>➖/➕ = step; tap the middle button to type an exact number.</i>", T.block("hg_" + grp, det)), rows


def diag_page(sec: str, ctx):
    """وضعیت اسکن هر ماژول: چند بار چک شد، چند سیگنال خام آمد، چرا مسدود شد، چند معامله باز شد (فقط خواندنی)."""
    import json
    import time as _t
    c = sc.load(sec, _path(ctx))
    lines = [f"📡 <b>Module status</b> — {BOTS[sec]}\n"]
    st = {}
    if sec == "ticb":
        try:
            import strategy_modules as _sm
            with open(os.path.join(ctx.get("data_dir") or "data", _sm.TRACK_FILE), encoding="utf-8") as f:
                st = json.load(f) or {}
        except Exception:
            st = {}
    else:
        lines.append("SGNL keeps its scan counters in its own state.json (see that bot's Actions log).")
    dg = st.get("diag") or {}
    on = [m for m in sc.MODULES if c.get("mod_" + m)]
    lines.append(f"Paused: {'YES ⚠️' if c.get('paused') else 'no'} · modules ON: {len(on)}" + (f" ({', '.join(on)})" if on else ""))
    now = _t.time()
    for m in on:
        d = dg.get(m)
        if not d:
            lines.append(f"• <b>{m}</b>: no scan recorded yet")
            continue
        age = int((now - d.get("last_check", 0)) / 60) if d.get("last_check") else -1
        bl = ", ".join(f"{k}×{v}" for k, v in sorted((d.get("blocked") or {}).items(), key=lambda x: -x[1])[:3]) or "-"
        lines.append(f"• <b>{m}</b>: checks {d.get('checks', 0)} · no-data {d.get('nodata', 0)} · raw signals {d.get('raw', 0)} · opened {d.get('opened', 0)} · blocked: {bl}"
                     + (f" · last check {age} min ago" if age >= 0 else ""))
    if c.get("mod_HOT"):
        lines.append("\n" + _hot_lists(sec, ctx))
    rows = [[{"text": "🔄 Refresh", "callback_data": f"adm:{sec}:diag"}], _back(f"adm:{sec}:mods")]
    return _fit("\n".join(lines), T.block("diag")), rows


def ltf_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    vd = _verdicts_local()
    lines = ["⚡ <b>Low-timeframe strategies</b> (ordered by timeframe)",
             f"✅ PASS = lab-confirmed · 🧪 near-pass = almost passed, can be switched on · 🔒 = locked. Tap ℹ️ for the full description. "
             f"Daily cap: at most {c['lowtf_max_per_day']} signals per day, best expected R first.\n"]
    rows = []
    order = sorted(sc.LOWTF, key=lambda m: (TF_ORDER.get(_tf_of(m, vd), 9), m))
    cur_tf = None
    for m in order:
        v = vd.get(m) or {}
        tf = _tf_of(m, vd)
        stt = sc.lowtf_status(m, vd)
        if tf != cur_tf:
            cur_tf = tf
            lines.append(f"<b>— {tf.upper()} —</b>")
        if stt in ("PASS", "CAND"):
            mark = "✅" if stt == "PASS" else "🧪"
            lines.append(f"{_on(c['mod_' + m])} <b>{m}</b> {mark} t={v.get('t', 0):.1f} PF={v.get('pf', 0):.2f} avg={v.get('avg_r', 0):+.2f}R n={v.get('n', 0)}")
            rows.append([{"text": f"{_on(c['mod_' + m])} {mark} {m} ({tf.upper()})", "callback_data": f"adm:{sec}:tg:mod_{m}"},
                         {"text": f"🪜 {sc.effective_profile(c, m, vd)}", "callback_data": f"adm:{sec}:lad:{m}"},
                         {"text": "ℹ️", "callback_data": f"adm:{sec}:sd:{m}"}])
        else:
            lines.append(f"🔒 <b>{m}</b> — {'not passed' if stt == 'FAIL' else 'awaiting backtest'}")
            rows.append([{"text": f"🔒 {m} ({tf.upper()}) — ℹ️", "callback_data": f"adm:{sec}:sd:{m}"}])
    rows.append([{"text": "➖", "callback_data": f"adm:{sec}:st:lowtf_max_per_day:-1"},
                 {"text": f"Daily cap {c['lowtf_max_per_day']}", "callback_data": f"adm:{sec}:ltf"},
                 {"text": "➕", "callback_data": f"adm:{sec}:st:lowtf_max_per_day:1"}])
    rows.append(_back(f"adm:{sec}:mods"))
    return _fit("\n".join(lines), T.block("ltf")), rows


def tfs_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    rows, cur = [], []
    for t in sc.TF_KEYS:
        cur.append({"text": f"{_on(c['tf_' + t])} {t.upper()}", "callback_data": f"adm:{sec}:tg:tf_{t}"})
    rows.append(cur)
    rows.append(_back(f"adm:{sec}:menu"))
    return _fit("🕒 <b>Timeframes</b> for the NEW strategy modules.\n"
                "(Existing automatic signals are controlled by the existing settings and are not changed here.)", T.block("tfs")), rows


def flt_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    rows = []
    for key, name, params in FILTERS:
        rows.append([{"text": f"{_on(c[key])} {name}", "callback_data": f"adm:{sec}:tg:{key}"},
                     {"text": "⚙️", "callback_data": f"adm:{sec}:par:{key}"}])
    rows.append(_back(f"adm:{sec}:menu"))
    return _fit("🛡 <b>Safety filters</b>\nTap a name to switch ON/OFF; ⚙️ to set its numbers.\n"
                "Filters apply to automatic signals of the new strategy modules.", T.block("flt")), rows


def par_page(sec: str, fkey: str, ctx):
    c = sc.load(sec, _path(ctx))
    name, params = next((n, p) for k, n, p in FILTERS if k == fkey)
    lines = [f"⚙️ <b>{name}</b> — {_on(c[fkey])} {'ON' if c[fkey] else 'OFF'}\n"]
    rows = [[{"text": f"{_on(c[fkey])} Switch {'OFF' if c[fkey] else 'ON'}", "callback_data": f"adm:{sec}:tgp:{fkey}"}]]
    for p in params:
        lines.append(f"{PARAM_LABEL[p]}: <b>{_num(c[p])}</b>")
        s = STEP[p]
        rows.append([{"text": "➖", "callback_data": f"adm:{sec}:st:{p}:-1"},
                     {"text": f"{PARAM_LABEL[p][:22]}: {_num(c[p])}", "callback_data": f"adm:{sec}:ty:{p}"},
                     {"text": "➕", "callback_data": f"adm:{sec}:st:{p}:1"}])
    rows.append([{"text": "⬅️ Back", "callback_data": f"adm:{sec}:flt"}])
    det = "\n".join(f"• {PARAM_LABEL[q]}: {T.PARAM_DETAIL.get(q, '')}" for q in params)
    return _fit("\n".join(lines) + "\n\n<i>➖/➕ = step; tap the middle button to type an exact number.</i>",
                T.block(fkey, det)), rows


def news_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    man = [m for m in sc.manual_news() if m.get("end_ms", 0) > time.time() * 1000]
    upcoming = []
    try:
        now = time.time() * 1000
        for e in sc.news_events():
            if e["ts_ms"] > now and e["impact"] >= c["news_impact"]:
                upcoming.append(f"• {time.strftime('%a %H:%M', time.gmtime(e['ts_ms'] / 1000))} UTC — {e.get('title', '')}")
    except Exception:
        pass
    txt = (f"📰 <b>News filter</b> — {_on(c['news_filter'])} {'ON' if c['news_filter'] else 'OFF'}\n"
           f"Blocks new signals {c['news_before_min']} min before / {c['news_after_min']} min after US news "
           f"(impact ≥ {c['news_impact']}).\n"
           f"Manual blackouts active: {len(man)}\n\nUpcoming:\n" + ("\n".join(upcoming[:8]) or "—"))
    rows = [[{"text": f"{_on(c['news_filter'])} Switch {'OFF' if c['news_filter'] else 'ON'}", "callback_data": f"adm:{sec}:tg:news_filter"},
             {"text": "⚙️ Windows", "callback_data": f"adm:{sec}:par:news_filter"}],
            [{"text": "🚫 Block 30m", "callback_data": f"adm:{sec}:mn:30"}, {"text": "🚫 1h", "callback_data": f"adm:{sec}:mn:60"},
             {"text": "🚫 2h", "callback_data": f"adm:{sec}:mn:120"}, {"text": "🚫 4h", "callback_data": f"adm:{sec}:mn:240"}],
            [{"text": "🧹 Clear manual blackouts", "callback_data": f"adm:{sec}:mnc"}],
            _back(f"adm:{sec}:menu")]
    return _fit(txt, T.block("news") + "\n" + "• " + T.PARAM_HELP["news_filter"].split("\n")[0]), rows


# ------------------------------------------------------------------ پل دستورها
def grp_page(i: int):
    title, keys = CMD_GROUPS[i]
    rows = [[{"text": CMDS[k]["t"], "callback_data": f"adm:cmd:{k}"}] for k in keys]
    rows.append(_back())
    expl = "\n".join(f"• {CMDS[k]['t']}: {T.CMD_HELP.get(k, '')}" for k in keys)
    return _fit(f"<b>{title}</b>", f"\n\nℹ️ <b>توضیح دکمه‌ها</b>\n{expl}"), rows


def _ask(ctx, chat_id, uid, key, vals: List[str], mid: Optional[int] = None):
    spec = CMDS[key]
    step = len(vals)
    prompt, choices = spec["s"][step]
    ctx["set_await"](uid, "adm_cmd", {"key": key, "vals": vals})
    rows = []
    if choices:
        rows.append([{"text": ch, "callback_data": f"adm:ch:{ch}"} for ch in choices])
    if "(or -" in prompt:
        rows.append([{"text": "Skip (-)", "callback_data": "adm:ch:-"}])
    rows.append([{"text": "✖️ Cancel", "callback_data": "adm:cancel"}])
    text = f"{spec['t']} — step {step + 1}/{len(spec['s'])}\n{prompt}\n\nℹ️ {T.CMD_HELP.get(key, '')}"
    (ctx["edit"](chat_id, mid, text, rows) if mid else ctx["send"](chat_id, text, rows))


def _finish(ctx, chat_id, uid, key, vals, mid=None):
    spec = CMDS[key]
    vals2 = [v for v in vals]
    text = spec["c"].format(*vals2) if vals2 else spec["c"]
    text = " ".join(t for t in text.split() if t != "-")
    ctx["clear_await"](uid)
    if spec.get("confirm"):
        ctx["set_await"](uid, "adm_confirm", {"text": text, "key": key})
        rows = [[{"text": "✅ Confirm", "callback_data": "adm:go"}, {"text": "✖️ Cancel", "callback_data": "adm:cancel"}]]
        msg = f"⚠️ Confirm this action:\n<code>{text}</code>"
        (ctx["edit"](chat_id, mid, msg, rows) if mid else ctx["send"](chat_id, msg, rows))
        return
    ctx["run_cmd"](chat_id, text)
    ctx["send"](chat_id, "✔️ Done.", [_back()])


def start_cmd(ctx, chat_id, uid, key, mid):
    if not CMDS[key]["s"]:
        _finish(ctx, chat_id, uid, key, [], mid)
    else:
        _ask(ctx, chat_id, uid, key, [], mid)


def _valid_step(prompt: str, val: str) -> bool:
    optional = "(or -" in prompt
    if val == "-":
        return optional
    if "Signal ID" in prompt or "Code" in prompt or "Symbol" in prompt:
        return len(val.split()) == 1
    if "Reason" in prompt:
        return len(val) > 0
    if any(k in prompt for k in ("ID", "Days", "Percent", "How many", "price", "Price", "Entry", "Stop-loss",
                                 "Max uses", "Valid for", "Final R")):
        try:
            float(val.replace(",", ""))
            return True
        except ValueError:
            return False
    return len(val) > 0


# ------------------------------------------------------------------ ورودی‌ها
def handle_callback(data: str, chat_id: int, message_id: int, user_id: int, ctx: Dict[str, Any]) -> bool:
    """True اگر callback متعلق به پنل بود."""
    if not data.startswith("adm:"):
        return False
    p = data.split(":")
    edit = lambda text, rows: ctx["edit"](chat_id, message_id, text, rows)  # noqa: E731
    path = _path(ctx)
    a = p[1]
    if a == "home":
        ctx["clear_await"](user_id); edit(*home(ctx)); return True
    if a == "cancel":
        ctx["clear_await"](user_id); edit(*home(ctx)); return True
    if a == "rep":
        if pr is None:
            edit("⚠️ perf_report.py is missing from the repository — copy it next to admin_panel.py to use Reports.", [_back("adm:home")])
            return True
        k = p[2] if len(p) > 2 else "a"
        if k in PERIODS:
            edit(*rep_page(k, ctx))
        elif k == "g":
            edit(*rep_group_page(p[3], p[4], ctx))
        elif k == "gs":
            edit(*rep_group_page(p[3], p[4], ctx, strat=p[5]))
        elif k == "s":
            edit(*rep_strategy_page(p[3], ":".join(p[4:]), ctx))
        elif k in ("f", "v", "e"):
            _send_report_files(ctx, chat_id, k, p[3], None)
        elif k in ("fs", "es"):
            _send_report_files(ctx, chat_id, k[0], p[3], ":".join(p[4:]))
        return True
    if a == "ldr":
        ctx["clear_await"](user_id); edit(*ldr_page(ctx)); return True
    if a == "ld":
        edit(*ld_page(p[2], ctx)); return True
    if a == "ldn":
        key = p[2]
        if key not in lc.CUSTOM_KEYS:
            return True
        ctx["set_await"](user_id, "adm_ladder", {"key": key})
        edit(f"🪜 <b>{'Edit' if key in lc.CUSTOM else 'New'} ladder {key}</b>\nSend the ladder as one message:\n"
             "<code>levels / weights% / trail / note</code>\n"
             "Example: <code>2, 3, 5 / 40, 30, 20 / 1.5 / three-step</code>\n"
             "Single target: <code>3 / 100</code>" + T.block("ldr"), [[{"text": "✖️ Cancel", "callback_data": "adm:ldr"}]])
        return True
    if a == "ldd":
        edit(f"🗑 Delete ladder <b>{p[2]}</b>? Strategies using it return to auto.",
             [[{"text": "✅ Yes, delete", "callback_data": f"adm:ldd2:{p[2]}"}, {"text": "✖️ No", "callback_data": f"adm:ld:{p[2]}"}]]); return True
    if a == "ldd2":
        key = p[2]
        for sec_ in sc.SECTIONS:
            for m_ in sc.MODULES:
                if sc.load(sec_, path).get(f"prof_{m_}") == key:
                    sc.set_value(sec_, f"prof_{m_}", "auto", path)
        lc.delete_custom(key); sc.sync_profile_choices()
        edit(*ldr_page(ctx)); return True
    if a == "pub":
        ctx["clear_await"](user_id); edit(*pub_page(ctx)); return True
    if a in ("pubnow", "pubtm", "pubclr"):
        from datetime import datetime, timedelta, timezone
        n = datetime.now(timezone.utc)
        v = "" if a == "pubclr" else (n.strftime("%Y-%m-%dT%H:%M") if a == "pubnow" else (n + timedelta(days=1)).strftime("%Y-%m-%d"))
        sc.set_public_from(v, path); edit(*pub_page(ctx)); return True
    if a == "pubty":
        ctx["set_await"](user_id, "adm_pub", {})
        edit("Send the start date (UTC), e.g. <code>2026-11-01</code> or <code>2026-11-01T12:00</code>:" + T.block("pub"),
             [[{"text": "✖️ Cancel", "callback_data": "adm:pub"}]]); return True
    if a == "panic":
        for s in sc.SECTIONS:
            sc.set_value(s, "paused", True, path)
        edit(*home(ctx)); return True
    if a == "grp":
        edit(*grp_page(int(p[2]))); return True
    if a == "cmd":
        start_cmd(ctx, chat_id, user_id, p[2], message_id); return True
    if a == "ch":  # انتخاب گزینه‌ی یک مرحله
        aw = ctx["get_await"](user_id)
        if aw and aw.get("kind") == "adm_cmd":
            d = aw["data"]; vals = d["vals"] + [":".join(p[2:])]
            if len(vals) >= len(CMDS[d["key"]]["s"]):
                _finish(ctx, chat_id, user_id, d["key"], vals, message_id)
            else:
                _ask(ctx, chat_id, user_id, d["key"], vals, message_id)
        return True
    if a == "go":
        aw = ctx["get_await"](user_id)
        ctx["clear_await"](user_id)
        if aw and aw.get("kind") == "adm_confirm":
            ctx["run_cmd"](chat_id, aw["data"]["text"])
            edit("✔️ Done.", [_back()])
        else:
            edit("Nothing to confirm (expired).", [_back()])
        return True
    sec = a
    if sec not in sc.SECTIONS:
        return True
    b = p[2] if len(p) > 2 else "menu"
    if b == "menu":
        edit(*sec_menu(sec, ctx))
    elif b == "mods":
        edit(*mods_page(sec, ctx))
    elif b == "tfs":
        edit(*tfs_page(sec, ctx))
    elif b == "ltf":
        edit(*mods_page(sec, ctx))
    elif b == "_ltf_old":
        edit(*ltf_page(sec, ctx))
    elif b == "lad":
        edit(*lad_page(sec, p[3], ctx))
    elif b == "diag":
        edit(*diag_page(sec, ctx))
    elif b == "hot":
        edit(*hot_page(sec, ctx))
    elif b == "hg":
        if p[3] in HOT_GROUPS:
            edit(*hg_page(sec, p[3], ctx))
    elif b == "sd":
        if p[3] in sc.MODULES:
            edit(*sd_page(sec, p[3], ctx))
    elif b == "sp":
        if p[3] in sc.MODULES and p[4] in sc.PROFILE_CHOICES:
            sc.set_value(sec, f"prof_{p[3]}", p[4], path)
        edit(*lad_page(sec, p[3], ctx))
    elif b == "lu":
        edit(*lu_page(sec, p[3], ctx))
    elif b == "lu2":
        key, m_ = p[3], p[4]
        if key in lc.CUSTOM and m_ in sc.MODULES:
            sc.set_value(sec, f"prof_{m_}", "auto" if sc.load(sec, path).get(f"prof_{m_}") == key else key, path)
        edit(*lu_page(sec, key, ctx))
    elif b == "rec":
        sc.apply_recommended(sec, path)
        edit(*mods_page(sec, ctx))
    elif b == "flt":
        edit(*flt_page(sec, ctx))
    elif b == "news":
        edit(*news_page(sec, ctx))
    elif b == "par":
        edit(*par_page(sec, p[3], ctx))
    elif b == "tg":
        key = p[3]
        locked = key.startswith("mod_") and key[4:] in sc.LOWTF and sc.lowtf_status(key[4:], _verdicts_local()) not in ("PASS", "CAND") \
            and not sc.load(sec, path).get(key)
        if key in sc.SCHEMA and not locked:
            sc.toggle(sec, key, path)
        if key in HOT_KEYS and key in sc.SCHEMA:
            edit(*hg_page(sec, _hot_group(key), ctx))
            return True
        pg = ("mods" if key.startswith("mod_") else "tfs" if key.startswith("tf_") else "news" if key == "news_filter"
              else "flt" if key != "paused" else "menu")
        edit(*{"ltf": ltf_page, "mods": mods_page, "tfs": tfs_page, "news": news_page, "flt": flt_page, "menu": sec_menu}[pg](sec, ctx))
    elif b == "tgh":
        if p[3] == "mod_HOT":
            sc.toggle(sec, "mod_HOT", path)
        edit(*hot_page(sec, ctx))
    elif b == "tgp":
        sc.toggle(sec, p[3], path)
        edit(*par_page(sec, p[3], ctx))
    elif b == "st":
        key, sign = p[3], int(p[4])
        sc.step_value(sec, key, sign * STEP[key], path)
        if key in HOT_KEYS:
            edit(*hg_page(sec, _hot_group(key), ctx))
            return True
        if key == "lowtf_max_per_day":
            edit(*ltf_page(sec, ctx))
            return True
        fk = next(k for k, _, ps in FILTERS if key in ps)
        edit(*par_page(sec, fk, ctx))
    elif b == "ty":
        ctx["set_await"](user_id, "adm_num", {"sec": sec, "key": p[3]})
        edit(f"Type the new value for <b>{PARAM_LABEL[p[3]]}</b> (current {_num(sc.load(sec, path)[p[3]])}):",
             [[{"text": "✖️ Cancel", "callback_data": f"adm:{sec}:" + (f"hg:{_hot_group(p[3])}" if p[3] in HOT_KEYS else "flt")}]])
    elif b == "mn":
        mins = int(p[3]); now = int(time.time() * 1000)
        sc.add_manual_news(now, now + mins * 60000, "manual")
        edit(*news_page(sec, ctx))
    elif b == "mnc":
        sc.clear_manual_news(); edit(*news_page(sec, ctx))
    elif b == "reset":
        edit(f"♻️ Reset <b>{BOTS[sec]}</b> controls to defaults (everything new OFF)?" + T.block("reset"),
             [[{"text": "✅ Yes, reset", "callback_data": f"adm:{sec}:reset2"}, {"text": "✖️ No", "callback_data": f"adm:{sec}:menu"}]])
    elif b == "reset2":
        sc.reset(sec, path); edit(*sec_menu(sec, ctx))
    return True


def handle_text(chat_id: int, user_id: int, text: str, awaiting: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    kind = awaiting.get("kind"); data = awaiting.get("data") or {}
    if kind == "adm_num":
        v = ctx["parse_num"](text)
        ctx["clear_await"](user_id)
        if v is None:
            ctx["send"](chat_id, "❌ Not a number.", [_back(f"adm:{data['sec']}:flt")]); return True
        sc.set_value(data["sec"], data["key"], v, _path(ctx))
        if data["key"] in HOT_KEYS:
            ctx["send"](chat_id, *hg_page(data["sec"], _hot_group(data["key"]), ctx)); return True
        fk = next(k for k, _, ps in FILTERS if data["key"] in ps)
        ctx["send"](chat_id, *par_page(data["sec"], fk, ctx)); return True
    if kind == "adm_ladder":
        key = data["key"]
        lv, wt, tr, note, err = parse_ladder_text(text)
        if err:
            ctx["send"](chat_id, f"❌ {err}\nTry again (e.g. <code>2, 3, 5 / 40, 30, 20 / 1.5</code>) or cancel.",
                        [[{"text": "✖️ Cancel", "callback_data": "adm:ldr"}]]); return True
        ok, e2 = lc.save_custom(key, lv, wt, tr, note)
        ctx["clear_await"](user_id)
        if not ok:
            ctx["send"](chat_id, f"❌ {e2}", [_back("adm:ldr")]); return True
        sc.sync_profile_choices()
        ctx["send"](chat_id, *ld_page(key, ctx)); return True
    if kind == "adm_pub":
        v = sc.set_public_from(text.strip(), _path(ctx))
        ctx["clear_await"](user_id)
        if v != text.strip():
            ctx["send"](chat_id, "❌ Invalid date. Use YYYY-MM-DD (UTC).", [_back("adm:pub")]); return True
        ctx["send"](chat_id, *pub_page(ctx)); return True
    if kind == "adm_cmd":
        key = data["key"]; vals = list(data["vals"])
        prompt = CMDS[key]["s"][len(vals)][0]
        t = text.strip()
        if not t or not _valid_step(prompt, t) or any(ch in t for ch in "\n"):
            ctx["send"](chat_id, "❌ Invalid value, try again.", [[{"text": "✖️ Cancel", "callback_data": "adm:cancel"}]])
            return True
        vals.append(t)
        if len(vals) >= len(CMDS[key]["s"]):
            _finish(ctx, chat_id, user_id, key, vals)
        else:
            _ask(ctx, chat_id, user_id, key, vals)
        return True
    return False


# ================================================================== طراح نردبان (Ladder designer)
def _ladder_usage(key: str, ctx) -> List[str]:
    out = []
    for sec in sc.SECTIONS:
        c = sc.load(sec, _path(ctx))
        for m in sc.MODULES:
            if c.get(f"prof_{m}") == key:
                out.append(f"{sec.upper()}:{m}")
    return out


def ldr_page(ctx):
    lc.refresh_custom()
    lines = ["🪜 <b>Ladder designer</b>", f"Your ladders: {len(lc.CUSTOM)}/{lc.CUSTOM_MAX}\n"]
    rows = []
    for k in lc.CUSTOM_KEYS:
        if k in lc.CUSTOM:
            use = _ladder_usage(k, ctx)
            lines.append(f"★ <b>{k}</b> — {lc.describe(k)}" + (f"\n   used by: {', '.join(use)}" if use else "\n   not used yet"))
            rows.append([{"text": f"★ {k} — open", "callback_data": f"adm:ld:{k}"}])
    if not lc.CUSTOM:
        lines.append("No custom ladder yet.")
    if lc.next_free_key():
        rows.append([{"text": f"➕ New ladder ({lc.next_free_key()})", "callback_data": f"adm:ldn:{lc.next_free_key()}"}])
    rows.append(_back())
    return _fit("\n".join(lines), T.block("ldr")), rows


def ld_page(key: str, ctx):
    lc.refresh_custom()
    if key not in lc.CUSTOM:
        return ldr_page(ctx)
    c = lc.CUSTOM[key]
    lines = [f"★ <b>{key}</b> — {lc.describe(key)}"]
    if c.get("note"):
        lines.append(f"Note: {c['note']}")
    lines.append("\nOutcome in R (after fees not included):")
    for name, r in lc.scenarios(key):
        lines.append(f"• {name}: <b>{r:+.2f}R</b>")
    use = _ladder_usage(key, ctx)
    lines.append("\nUsed by: " + (", ".join(use) if use else "no strategy yet"))
    rows = [[{"text": "➕ Add / remove for strategies (TICB)", "callback_data": f"adm:ticb:lu:{key}"}],
            [{"text": "➕ Add / remove for strategies (SGNL)", "callback_data": f"adm:sgnl:lu:{key}"}],
            [{"text": "✏️ Edit (send new values)", "callback_data": f"adm:ldn:{key}"}, {"text": "🗑 Delete", "callback_data": f"adm:ldd:{key}"}],
            _back("adm:ldr")]
    extra = "• ‹Add/remove› همین نردبان را برای هر استراتژی انتخاب یا (با زدن دوباره) به auto برمی‌گرداند.\n• نتایج بالا حالت‌های ثابت‌اند؛ امید ریاضی واقعی فقط با بک‌تست/معاملات واقعی معلوم می‌شود."
    return _fit("\n".join(lines), T.block("ldr", extra)), rows


def lu_page(sec: str, key: str, ctx):
    """انتخاب/حذف نردبان key برای هر استراتژی یک بخش (ربات)."""
    c = sc.load(sec, _path(ctx))
    vd = _verdicts_local()
    mods = list(sc.BASE_MODULES) + list(sc.HOTMODS) + list(sc.CANDLE) + [m for m in sc.LOWTF if sc.lowtf_status(m, vd) in ("PASS", "CAND")]
    lines = [f"★ <b>{key}</b> — {lc.describe(key)}", f"Bot: <b>{BOTS[sec]}</b>\n", "Tap a strategy to USE this ladder (✅) or tap again to return it to auto (⭐):"]
    rows, cur = [], []
    for m in mods:
        on = c.get(f"prof_{m}") == key
        cur.append({"text": f"{'✅' if on else '⬜'} {m}", "callback_data": f"adm:{sec}:lu2:{key}:{m}"})
        if len(cur) == 3:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    rows.append(_back(f"adm:ld:{key}"))
    return _fit("\n".join(lines), "\n\nℹ️ <b>توضیح</b>\nتغییر فقط روی سیگنال‌های جدید اثر دارد. چارت و پیام کانال هر سیگنال از نردبانِ همان استراتژی ساخته می‌شود."), rows


def parse_ladder_text(text: str):
    """«2,3,5 / 40,30,20 / 1.5 / توضیح» → (levels, weights(fraction), trail, note, error)."""
    parts = [x.strip() for x in text.replace("،", ",").split("/")]
    if len(parts) < 2:
        return None, None, None, "", "format: levels / weights% / trail / note"
    try:
        lv = [float(x) for x in parts[0].replace(" ", ",").split(",") if x.strip()]
        wt = [float(x) / 100.0 for x in parts[1].replace(" ", ",").split(",") if x.strip()]
    except ValueError:
        return None, None, None, "", "levels and weights must be numbers"
    trail = 0.0; note = ""
    if len(parts) >= 3 and parts[2]:
        try:
            trail = float(parts[2])
        except ValueError:
            note = parts[2]
    if len(parts) >= 4:
        note = parts[3]
    v, err = lc.validate_ladder(lv, wt, trail)
    if v is None:
        return None, None, None, note, err
    return list(v[0]), list(v[1]), v[2], note, ""


# ================================================================== گزارش‌ها (Reports)
PERIODS = {"7": ("7d", 7), "30": ("30d", 30), "90": ("90d", 90), "a": ("All", None), "p": ("Public view", None)}


def _records(ctx, period: str) -> List[Dict[str, Any]]:
    hist = ctx["load_history"]() if ctx.get("load_history") else []
    recs = pr.load_records(hist, ctx.get("data_dir"))
    if period == "p":
        recs = sc.filter_public(recs, path=_path(ctx))
    else:
        recs = pr.filter_period(recs, days=PERIODS.get(period, ("", None))[1])
    return recs


def _period_row(prefix: str, cur: str) -> List[Dict[str, str]]:
    return [{"text": ("• " if k == cur else "") + v[0], "callback_data": f"adm:rep:{prefix}:{k}" if prefix else f"adm:rep:{k}"} for k, v in PERIODS.items()]


def rep_page(period: str, ctx):
    recs = _records(ctx, period)
    m = pr.metrics(recs)
    lines = [f"📈 <b>Reports</b> — period: <b>{PERIODS.get(period, ('?',))[0]}</b>", ""]
    lines.append("<pre>" + pr.summary_block(m).replace("<", "&lt;") + "</pre>")
    if m.get("n"):
        lines.append("<pre>" + pr.group_table(recs, "s", limit=12).replace("<", "&lt;") + "</pre>")
    rows = [_period_row("", period)]
    strats = list(pr.group(recs, pr.strategy_of).keys())[:12]
    cur = []
    for sname in strats:
        cur.append({"text": f"🔎 {sname[:14]}", "callback_data": f"adm:rep:s:{period}:{sname[:24]}"})
        if len(cur) == 2:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    rows.append([{"text": "🪜 Ladders", "callback_data": f"adm:rep:g:l:{period}"}, {"text": "🕒 TFs", "callback_data": f"adm:rep:g:t:{period}"},
                 {"text": "🪙 Symbols", "callback_data": f"adm:rep:g:y:{period}"}])
    rows.append([{"text": "↕️ Side", "callback_data": f"adm:rep:g:d:{period}"}, {"text": "🏁 Close reason", "callback_data": f"adm:rep:g:c:{period}"},
                 {"text": "📅 Months", "callback_data": f"adm:rep:g:m:{period}"}])
    rows.append([{"text": "⏰ Hours", "callback_data": f"adm:rep:g:h:{period}"}, {"text": "🗓 Weekdays", "callback_data": f"adm:rep:g:w:{period}"}])
    rows.append([{"text": "📄 Full report (.txt)", "callback_data": f"adm:rep:f:{period}"}, {"text": "🧾 CSV", "callback_data": f"adm:rep:v:{period}"},
                 {"text": "📈 Equity chart", "callback_data": f"adm:rep:e:{period}"}])
    rows.append(_back())
    return _fit("\n".join(lines), T.block("rep")), rows


def rep_group_page(gk: str, period: str, ctx, strat: Optional[str] = None):
    recs = _records(ctx, period)
    if strat:
        recs = [h for h in recs if pr.strategy_of(h)[:24] == strat]
    title = pr.GROUPS[gk][0]
    head = f"📈 <b>{('Strategy ' + strat + ' — ') if strat else ''}by {title}</b> — {PERIODS.get(period, ('?',))[0]}\n"
    body = "<pre>" + pr.group_table(recs, gk, limit=30).replace("<", "&lt;") + "</pre>"
    back = f"adm:rep:s:{period}:{strat}" if strat else f"adm:rep:{period}"
    return _fit(head + body, T.block("rep")), [_period_row(f"g:{gk}", period), _back(back)]


def rep_strategy_page(period: str, strat: str, ctx):
    recs = [h for h in _records(ctx, period) if pr.strategy_of(h)[:24] == strat]
    head = f"📈 <b>{strat}</b> — {PERIODS.get(period, ('?',))[0]}\n"
    body = "<pre>" + pr.summary_block(pr.metrics(recs)).replace("<", "&lt;") + "</pre>"
    body += "<pre>" + pr.group_table(recs, "l", limit=10).replace("<", "&lt;") + "\n" + pr.group_table(recs, "t", limit=6).replace("<", "&lt;") + "</pre>"
    rows = [[{"text": ("• " if k == period else "") + v[0], "callback_data": f"adm:rep:s:{k}:{strat}"} for k, v in PERIODS.items()],
            [{"text": "🪙 Symbols", "callback_data": f"adm:rep:gs:y:{period}:{strat}"}, {"text": "↕️ Side", "callback_data": f"adm:rep:gs:d:{period}:{strat}"},
             {"text": "🏁 Close", "callback_data": f"adm:rep:gs:c:{period}:{strat}"}],
            [{"text": "📅 Months", "callback_data": f"adm:rep:gs:m:{period}:{strat}"}, {"text": "⏰ Hours", "callback_data": f"adm:rep:gs:h:{period}:{strat}"}],
            [{"text": "📄 Full report", "callback_data": f"adm:rep:fs:{period}:{strat}"}, {"text": "📈 Equity", "callback_data": f"adm:rep:es:{period}:{strat}"}],
            _back(f"adm:rep:{period}")]
    return _fit(head + body, T.block("rep")), rows


def _send_report_files(ctx, chat_id: int, kind: str, period: str, strat: Optional[str]) -> None:
    import tempfile
    recs = _records(ctx, period)
    if strat:
        recs = [h for h in recs if pr.strategy_of(h)[:24] == strat]
    tag = f"{strat or 'all'}_{PERIODS.get(period, ('x',))[0]}".replace("/", "-").replace(" ", "")
    d = tempfile.mkdtemp()
    if not recs:
        ctx["send"](chat_id, "No closed trades in this selection.", [_back(f"adm:rep:{period}")]); return
    if kind == "f":
        fp = os.path.join(d, f"report_{tag}.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(pr.full_report(recs, f"Performance report — {tag}"))
        ctx["send_file"](chat_id, fp, f"Full report — {len(recs)} trades")
    elif kind == "v":
        fp = os.path.join(d, f"trades_{tag}.csv")
        with open(fp, "w", encoding="utf-8", newline="") as f:
            f.write(pr.to_csv(recs))
        ctx["send_file"](chat_id, fp, f"Trades CSV — {len(recs)} rows")
    elif kind == "e":
        fp = pr.equity_png(recs, tag, os.path.join(d, f"equity_{tag}.png"))
        if fp:
            ctx["send_photo"](chat_id, fp, f"Equity & drawdown — {tag}")
        else:
            ctx["send"](chat_id, "Not enough trades (or charting unavailable).", [_back(f"adm:rep:{period}")])
    ctx["send"](chat_id, "✔️ Sent.", [_back(f"adm:rep:{period}")])


# ================================================================== تاریخ شروع نمایش آمار (Public stats start)
def pub_page(ctx):
    cur = sc.get_public_from(_path(ctx))
    hist = ctx["load_history"]() if ctx.get("load_history") else []
    recs = pr.load_records(hist, ctx.get("data_dir"))
    shown = len(sc.filter_public(recs, path=_path(ctx)))
    lines = ["🔒 <b>Public stats start date</b>",
             f"Now: <b>{cur or 'not set — users see everything'}</b> (UTC)",
             f"Closed trades: total {len(recs)} · visible to users {shown} · hidden (admin only) {len(recs) - shown}"]
    rows = [[{"text": "📅 From NOW", "callback_data": "adm:pubnow"}, {"text": "📅 From tomorrow 00:00", "callback_data": "adm:pubtm"}],
            [{"text": "⌨️ Type a date", "callback_data": "adm:pubty"}, {"text": "🧹 Show everything", "callback_data": "adm:pubclr"}],
            _back()]
    return _fit("\n".join(lines), T.block("pub")), rows

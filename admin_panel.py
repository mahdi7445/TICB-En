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

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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
STEP = {"news_before_min": 15, "news_after_min": 15, "news_impact": 1, "daily_loss_limit_r": 0.5,
        "weekly_loss_limit_r": 1.0, "streak_losses": 1, "streak_pause_min": 60, "max_open_trades": 1,
        "max_signals_per_day": 1, "max_same_side": 1, "min_stop_pct": 0.05, "max_stop_pct": 0.5, "risk_pct": 0.25}

# پل دستورها: key -> (عنوان, قالب دستور, [مراحل]) ؛ هر مرحله: (پرسش, [گزینه‌ها یا None])
# مقدار "-" در مراحل اختیاری حذف می‌شود. confirm=True ⇒ مرحله‌ی تأیید.
CMDS: Dict[str, Dict[str, Any]] = {
    "list":    {"t": "📋 Subscribers", "c": "/admin_list", "s": []},
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
}
CMD_GROUPS = [
    ("👥 Users", ["list", "user", "add", "reduce", "remove", "bext", "bred"]),
    ("💰 Pricing & discounts", ["prices", "setprice", "dlist", "dadd", "drem"]),
    ("🛠 Trades & system", ["status", "close", "pause", "resume", "photos"]),
    ("✍️ Signals (manual)", ["manual", "custom", "ccancel", "cclose"]),
]


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
    rows.append([{"text": "🚨 Emergency: pause BOTH bots", "callback_data": "adm:panic"}])
    rows.append([{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}])
    return ("🎛 <b>Admin Panel</b>\nEverything here works with buttons only.\n\n" + _status_line("ticb", ctx) + "\n\n" +
            _status_line("sgnl", ctx) +
            "\n\n<i>SGNL picks up changes within a few minutes (it reads the shared control file).</i>"), rows


def sec_menu(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    rows = [
        [{"text": ("▶️ Resume signals" if c["paused"] else "⏸ Pause signals"), "callback_data": f"adm:{sec}:tg:paused"}],
        [{"text": "🧩 Strategy modules", "callback_data": f"adm:{sec}:mods"}, {"text": "🕒 Timeframes", "callback_data": f"adm:{sec}:tfs"}],
        [{"text": "🛡 Safety filters", "callback_data": f"adm:{sec}:flt"}, {"text": "📰 News", "callback_data": f"adm:{sec}:news"}],
        [{"text": "♻️ Reset all to defaults", "callback_data": f"adm:{sec}:reset"}],
        _back(),
    ]
    return _status_line(sec, ctx), rows


def mods_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    lines = ["🧩 <b>Strategy modules</b> (new, OFF by default)\n"]
    rows = []
    for m in sc.MODULES:
        tf, note = sc.MODULE_INFO[m]
        lines.append(f"{_on(c['mod_' + m])} <b>{m}</b> — {tf.upper()} — {note}")
        rows.append([{"text": f"{_on(c['mod_' + m])} {m} ({tf.upper()})", "callback_data": f"adm:{sec}:tg:mod_{m}"}])
    lines.append("\nExperimental modules have no statistical confirmation — signals are labelled that way in the channel.")
    rows.append(_back(f"adm:{sec}:menu"))
    return "\n".join(lines), rows


def tfs_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    rows, cur = [], []
    for t in sc.TF_KEYS:
        cur.append({"text": f"{_on(c['tf_' + t])} {t.upper()}", "callback_data": f"adm:{sec}:tg:tf_{t}"})
    rows.append(cur)
    rows.append(_back(f"adm:{sec}:menu"))
    return ("🕒 <b>Timeframes</b> for the NEW strategy modules.\n"
            "(Existing automatic signals are controlled by the existing settings and are not changed here.)"), rows


def flt_page(sec: str, ctx):
    c = sc.load(sec, _path(ctx))
    rows = []
    for key, name, params in FILTERS:
        rows.append([{"text": f"{_on(c[key])} {name}", "callback_data": f"adm:{sec}:tg:{key}"},
                     {"text": "⚙️", "callback_data": f"adm:{sec}:par:{key}"}])
    rows.append(_back(f"adm:{sec}:menu"))
    return ("🛡 <b>Safety filters</b>\nTap a name to switch ON/OFF; ⚙️ to set its numbers.\n"
            "Filters apply to automatic signals of the new strategy modules."), rows


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
    return "\n".join(lines) + "\n\n<i>➖/➕ = step; tap the middle button to type an exact number.</i>", rows


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
    return txt, rows


# ------------------------------------------------------------------ پل دستورها
def grp_page(i: int):
    title, keys = CMD_GROUPS[i]
    rows = [[{"text": CMDS[k]["t"], "callback_data": f"adm:cmd:{k}"}] for k in keys]
    rows.append(_back())
    return f"<b>{title}</b>", rows


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
    text = f"{spec['t']} — step {step + 1}/{len(spec['s'])}\n{prompt}"
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
    elif b == "flt":
        edit(*flt_page(sec, ctx))
    elif b == "news":
        edit(*news_page(sec, ctx))
    elif b == "par":
        edit(*par_page(sec, p[3], ctx))
    elif b == "tg":
        key = p[3]
        if key in sc.SCHEMA:
            sc.toggle(sec, key, path)
        pg = ("mods" if key.startswith("mod_") else "tfs" if key.startswith("tf_") else "news" if key == "news_filter"
              else "flt" if key != "paused" else "menu")
        edit(*{"mods": mods_page, "tfs": tfs_page, "news": news_page, "flt": flt_page, "menu": sec_menu}[pg](sec, ctx))
    elif b == "tgp":
        sc.toggle(sec, p[3], path)
        edit(*par_page(sec, p[3], ctx))
    elif b == "st":
        key, sign = p[3], int(p[4])
        sc.step_value(sec, key, sign * STEP[key], path)
        fk = next(k for k, _, ps in FILTERS if key in ps)
        edit(*par_page(sec, fk, ctx))
    elif b == "ty":
        ctx["set_await"](user_id, "adm_num", {"sec": sec, "key": p[3]})
        edit(f"Type the new value for <b>{PARAM_LABEL[p[3]]}</b> (current {_num(sc.load(sec, path)[p[3]])}):",
             [[{"text": "✖️ Cancel", "callback_data": f"adm:{sec}:flt"}]])
    elif b == "mn":
        mins = int(p[3]); now = int(time.time() * 1000)
        sc.add_manual_news(now, now + mins * 60000, "manual")
        edit(*news_page(sec, ctx))
    elif b == "mnc":
        sc.clear_manual_news(); edit(*news_page(sec, ctx))
    elif b == "reset":
        edit(f"♻️ Reset <b>{BOTS[sec]}</b> controls to defaults (everything new OFF)?",
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
        fk = next(k for k, _, ps in FILTERS if data["key"] in ps)
        ctx["send"](chat_id, *par_page(data["sec"], fk, ctx)); return True
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

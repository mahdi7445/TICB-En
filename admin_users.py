"""
admin_users.py — مدیریت کامل مشترکین فقط با دکمه (بدون تایپ دستور) برای ادمین‌ها.

• لیست مشترکین بی‌نهایت صفحه دارد (هر مشترک یک دکمه؛ اول/قبلی/بعدی/آخر، پرش ±۱۰ صفحه و «رفتن به صفحه‌ی N»).
• فیلترها: همه، رو به انقضا (≤۷ روز)، جدیدترین‌ها، بیشترین زمان باقی‌مانده، پرداخت در انتظار، اعضای ترید خودکار.
• جستجو: شناسه‌ی عددی، @یوزرنیم یا بخشی از نام.
• کلیک روی هر مشترک → صفحه‌ی جزئیات کامل حساب + همه‌ی اقدام‌ها با دکمه (تمدید/کاهش با مقدار آماده یا دلخواه،
  پیام به کاربر، لینک دعوت تازه، حذف با تأیید، پاک‌کردن تخفیف/سفارش در انتظار، رفتن به ترید خودکار، باز‌کردن چت).

این ماژول فقط UI است؛ منطق تمدید/کاهش/حذف همان توابع subscription_bot است که از env تزریق می‌شوند
(پس دستور متنی /admin_add و دکمه‌ها همیشه یک رفتار دارند).

env (dict):
  subs()          -> لیست زنده‌ی مشترکین            referrals() / applied() / pending() -> ساختارهای زنده
  profiles()      -> {uid: {name, username, lang, first, last}}   risk()-> {uid: {balance, risk_pct}}
  at              -> AutoTrade یا None              now() / fmt_date(ts)
  send(chat_id, text, rows=None) / edit(chat_id, mid, text, rows=None)
  extend(uid, days) / reduce(uid, days) / remove(uid) -> str    invite(uid) -> str|None
  tell(uid, text) -> None     set_await(uid, kind, data) / clear_await(uid)    parse_num(text)
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

H = html.escape
PAGE = 8                       # دکمه‌ی مشترک در هر صفحه (هر کدام یک ردیف)
MAX_CUSTOM_DAYS = 3650
DAY = 86400

FILTERS: Dict[str, str] = {
    "all": "📋 All subscribers",
    "soon": "⏳ Expiring within 7 days",
    "new": "🆕 Newest members",
    "long": "👑 Longest time left",
    "pend": "💳 Pending payment",
    "at": "🤖 Auto-trade members",
}

Rows = List[List[Dict[str, Any]]]


# ----------------------------------------------------------------------------- helpers
def _now(env) -> int:
    return int(env["now"]())


def _prof(env, uid: int) -> Dict[str, Any]:
    try:
        return (env["profiles"]() or {}).get(str(uid)) or {}
    except Exception:  # noqa: BLE001
        return {}


def _sub(env, uid: int) -> Optional[Dict[str, Any]]:
    return next((s for s in env["subs"]() if s.get("user_id") == uid), None)


def _name(env, uid: int) -> str:
    p = _prof(env, uid)
    nm = (p.get("name") or "").strip()
    un = (p.get("username") or "").strip()
    if nm and un:
        return f"{nm} @{un}"
    return nm or (f"@{un}" if un else "")


def _days_left(env, sub: Dict[str, Any]) -> int:
    return max(0, int((int(sub.get("expiry_ts") or 0) - _now(env)) // DAY))


def _icon(env, sub: Optional[Dict[str, Any]]) -> str:
    if not sub:
        return "⚪"
    left = int(sub.get("expiry_ts") or 0) - _now(env)
    return "❌" if left <= 0 else ("⚠️" if left <= 7 * DAY else "✅")


def _at_choice(env, uid: int) -> Optional[Dict[str, Any]]:
    at = env.get("at")
    if at is None:
        return None
    try:
        return at.choice(uid)
    except Exception:  # noqa: BLE001
        return None


def _back_row(to: str = "asub:home") -> List[Dict[str, Any]]:
    return [{"text": "⬅️ Back", "callback_data": to}, {"text": "🏠 Menu", "callback_data": "menu:home"}]


def _fit(text: str, limit: int = 3900) -> str:
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


def _uids(env, flt: str) -> List[int]:
    subs = list(env["subs"]())
    now = _now(env)
    if flt == "soon":
        xs = [s for s in subs if 0 < int(s.get("expiry_ts") or 0) - now <= 7 * DAY]
        xs.sort(key=lambda s: int(s.get("expiry_ts") or 0))
    elif flt == "new":
        xs = sorted(subs, key=lambda s: -int(s.get("member_since") or s.get("last_paid_ts") or 0))
    elif flt == "long":
        xs = sorted(subs, key=lambda s: -int(s.get("expiry_ts") or 0))
    elif flt == "pend":
        ids = []
        for p in env["pending"]():
            u = p.get("user_id")
            if isinstance(u, int) and u not in ids:
                ids.append(u)
        return ids
    elif flt == "at":
        xs = [s for s in subs if _at_choice(env, s.get("user_id")) is not None]
        xs.sort(key=lambda s: int(s.get("expiry_ts") or 0))
    else:                                           # all: زودتر منقضی‌شونده اول (نیازمند توجه)
        xs = sorted(subs, key=lambda s: int(s.get("expiry_ts") or 0))
    return [s["user_id"] for s in xs if isinstance(s.get("user_id"), int)]


def _label(env, uid: int) -> str:
    s = _sub(env, uid)
    nm = _name(env, uid)
    nm = (nm[:22] + "…") if len(nm) > 23 else nm
    tail = f" · {_days_left(env, s)}d" if s else " · no sub"
    return f"{_icon(env, s)} {nm + ' · ' if nm else ''}{uid}{tail}"


# ----------------------------------------------------------------------------- screens
def home(env) -> Tuple[str, Rows]:
    subs = list(env["subs"]())
    now = _now(env)
    live = [s for s in subs if int(s.get("expiry_ts") or 0) > now]
    soon3 = sum(1 for s in live if int(s["expiry_ts"]) - now <= 3 * DAY)
    soon7 = sum(1 for s in live if int(s["expiry_ts"]) - now <= 7 * DAY)
    pend = len({p.get("user_id") for p in env["pending"]()})
    nprof = len(env["profiles"]() or {})
    lines = ["👥 <b>Subscribers</b>", "",
             f"✅ Active: <b>{len(live)}</b>  (records: {len(subs)})",
             f"⚠️ Expiring ≤3 days: <b>{soon3}</b> · ≤7 days: <b>{soon7}</b>",
             f"💳 Pending payments: <b>{pend}</b>",
             f"🧾 Known users (ever opened the bot): <b>{nprof}</b>"]
    at = env.get("at")
    if at is not None:
        try:
            c = at.counts()
            lines.append("🤖 Auto-trade: " + " · ".join(f"{k} {v['active']}✅/{v['inactive']}⏸" for k, v in c.items()))
        except Exception:  # noqa: BLE001
            pass
    lines += ["", "Pick a list, or search. Tap any subscriber to see the full account and manage it with buttons."]
    rows: Rows = [[{"text": FILTERS["all"] + f" ({len(subs)})", "callback_data": "asub:l:all:0"}],
                  [{"text": FILTERS["soon"] + f" ({soon7})", "callback_data": "asub:l:soon:0"}],
                  [{"text": FILTERS["new"], "callback_data": "asub:l:new:0"}, {"text": FILTERS["long"], "callback_data": "asub:l:long:0"}],
                  [{"text": FILTERS["pend"] + f" ({pend})", "callback_data": "asub:l:pend:0"}]]
    if at is not None:
        rows.append([{"text": FILTERS["at"], "callback_data": "asub:l:at:0"}])
    rows.append([{"text": "🔎 Search (ID / @username / name)", "callback_data": "asub:find"}])
    rows.append([{"text": "🎁 Bulk extend all", "callback_data": "adm:cmd:bext"}, {"text": "⏬ Bulk reduce all", "callback_data": "adm:cmd:bred"}])
    rows.append([{"text": "⬅️ Admin panel", "callback_data": "adm:home"}, {"text": "🏠 Menu", "callback_data": "menu:home"}])
    return "\n".join(lines), rows


def list_page(env, flt: str, page: int) -> Tuple[str, Rows]:
    if flt not in FILTERS:
        flt = "all"
    ids = _uids(env, flt)
    pages = max(1, (len(ids) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    chunk = ids[page * PAGE:(page + 1) * PAGE]
    rows: Rows = [[{"text": _label(env, u), "callback_data": f"asub:u:{u}"}] for u in chunk]
    if pages > 1:
        nav = []
        if page > 0:
            nav += [{"text": "⏮", "callback_data": f"asub:l:{flt}:0"}, {"text": "◀️", "callback_data": f"asub:l:{flt}:{page - 1}"}]
        nav.append({"text": f"{page + 1}/{pages}", "callback_data": f"asub:go:{flt}"})
        if page < pages - 1:
            nav += [{"text": "▶️", "callback_data": f"asub:l:{flt}:{page + 1}"}, {"text": "⏭", "callback_data": f"asub:l:{flt}:{pages - 1}"}]
        rows.append(nav)
        if pages > 12:
            jump = []
            if page - 10 >= 0:
                jump.append({"text": "−10 pages", "callback_data": f"asub:l:{flt}:{page - 10}"})
            jump.append({"text": "🔢 Go to page…", "callback_data": f"asub:go:{flt}"})
            if page + 10 < pages:
                jump.append({"text": "+10 pages", "callback_data": f"asub:l:{flt}:{page + 10}"})
            rows.append(jump)
    rows.append([{"text": "🔎 Search", "callback_data": "asub:find"}, {"text": "🔄 Refresh", "callback_data": f"asub:l:{flt}:{page}"}])
    rows.append(_back_row())
    head = f"<b>{FILTERS[flt]}</b> — {len(ids)} total · page {page + 1}/{pages}"
    if not ids:
        head += "\n\nNothing here."
    else:
        head += "\n✅ >7d left · ⚠️ ≤7d · ❌ expired · ⚪ no subscription\nTap a person to open their account."
    return head, rows


def detail(env, uid: int, note: str = "") -> Tuple[str, Rows]:
    s = _sub(env, uid)
    p = _prof(env, uid)
    now = _now(env)
    fd = env["fmt_date"]
    L = []
    if note:
        L += [note, ""]
    nm = (p.get("name") or "").strip()
    un = (p.get("username") or "").strip()
    L.append(f"👤 <b>{H(nm) if nm else 'User'}</b>" + (f" · @{H(un)}" if un else ""))
    L.append(f"🆔 <code>{uid}</code>" + (f" · lang {H(str(p.get('lang')))}" if p.get("lang") else ""))
    if p.get("first"):
        L.append(f"🕘 First seen: {H(fd(int(p['first'])))}")
    if p.get("last"):
        L.append(f"🕘 Last active: {H(fd(int(p['last'])))}")
    L.append("")
    if s:
        exp = int(s.get("expiry_ts") or 0)
        active = exp > now
        L.append(f"🎟 <b>Subscription:</b> {'✅ active' if active else '❌ expired'} until {H(fd(exp))}")
        L.append(f"⏳ Time left: <b>{_days_left(env, s)} days</b>" if active else "⏳ Time left: 0")
        if s.get("member_since"):
            L.append(f"📅 Member since: {H(fd(int(s['member_since'])))}")
        if s.get("last_plan"):
            L.append(f"💎 Last plan: {H(str(s['last_plan']))}" + (f" · paid {H(fd(int(s['last_paid_ts'])))}" if s.get("last_paid_ts") else ""))
        L.append(f"🔔 Expiry reminder sent: {'yes' if s.get('reminded') else 'no'}")
    else:
        L.append("🎟 <b>Subscription:</b> none (never subscribed, expired, or removed)")
    refs = env["referrals"]()
    by = refs.get(str(uid)) if isinstance(refs, dict) else None
    n_ref = sum(1 for r in refs.values() if r == uid) if isinstance(refs, dict) else 0
    L.append("")
    L.append(f"🤝 Referred by: {('<code>' + str(by) + '</code>') if by else '—'} · friends referred (not yet paid): <b>{n_ref}</b>")
    disc = (env["applied"]() or {}).get(str(uid))
    if disc:
        L.append(f"🏷 Discount on file: <b>{H(str(disc.get('code')))}</b> ({H(str(disc.get('percent')))}%)")
    for pp in [x for x in env["pending"]() if x.get("user_id") == uid][:3]:
        left = max(0, int((pp.get("expires_at") or 0) - now) // 60)
        L.append(f"⏳ Pending: {H(str(pp.get('plan')))} · {H(str(pp.get('network')))} · {H(str(pp.get('amount')))} USDT · expires in {left} min")
    ch = _at_choice(env, uid)
    if ch:
        L.append(f"🤖 Auto-trade: <b>{H(str(ch.get('exchange')))}</b>")
    rk = (env["risk"]() or {}).get(str(uid))
    if rk:
        L.append(f"🧮 Risk profile: ${H(str(rk.get('balance')))} · {H(str(rk.get('risk_pct')))}%")

    rows: Rows = [
        [{"text": "+1d", "callback_data": f"asub:add:{uid}:1"}, {"text": "+7d", "callback_data": f"asub:add:{uid}:7"},
         {"text": "+30d", "callback_data": f"asub:add:{uid}:30"}, {"text": "+90d", "callback_data": f"asub:add:{uid}:90"}],
        [{"text": "+180d", "callback_data": f"asub:add:{uid}:180"}, {"text": "+365d", "callback_data": f"asub:add:{uid}:365"},
         {"text": "✏️ + custom", "callback_data": f"asub:cust:{uid}:a"}],
    ]
    if s:
        rows.append([{"text": "−1d", "callback_data": f"asub:red:{uid}:1"}, {"text": "−7d", "callback_data": f"asub:red:{uid}:7"},
                     {"text": "−30d", "callback_data": f"asub:red:{uid}:30"}, {"text": "✏️ − custom", "callback_data": f"asub:cust:{uid}:r"}])
    rows.append([{"text": "✉️ Message user", "callback_data": f"asub:msg:{uid}"}, {"text": "🔗 Send new invite link", "callback_data": f"asub:inv:{uid}"}])
    extra = []
    if disc:
        extra.append({"text": "🏷 Clear discount", "callback_data": f"asub:dsc:{uid}"})
    if any(x.get("user_id") == uid for x in env["pending"]()):
        extra.append({"text": "🧹 Cancel pending order", "callback_data": f"asub:pcl:{uid}"})
    if extra:
        rows.append(extra)
    if env.get("at") is not None:
        rows.append([{"text": "🤖 Auto-trade settings", "callback_data": f"atm:u:{uid}"}])
    rows.append([{"text": "💬 Open chat", "url": f"tg://user?id={uid}"}, {"text": "🔄 Refresh", "callback_data": f"asub:u:{uid}"}])
    if s:
        rows.append([{"text": "🗑 Remove from channel + subscribers", "callback_data": f"asub:rmq:{uid}"}])
    rows.append(_back_row("asub:l:all:0"))
    return _fit("\n".join(L)), rows


def search_results(env, term: str) -> Tuple[str, Rows]:
    t = term.strip().lstrip("@").lower()
    hits: List[int] = []
    if t.lstrip("-").isdigit():
        hits.append(int(t))
    profs = env["profiles"]() or {}
    for u, p in profs.items():
        if not str(u).lstrip("-").isdigit():
            continue
        hay = f"{p.get('name', '')} {p.get('username', '')}".lower()
        if t and t in hay and int(u) not in hits:
            hits.append(int(u))
    for s in env["subs"]():                              # مشترک بدون پروفایل ذخیره‌شده هم با شناسه (بخشی از آن) پیدا شود
        u = s.get("user_id")
        if isinstance(u, int) and t and t in str(u) and u not in hits:
            hits.append(u)
    if not hits:
        return f"🔎 No match for <b>{H(term)}</b>.", [[{"text": "🔎 Try again", "callback_data": "asub:find"}], _back_row()]
    if len(hits) == 1:
        return detail(env, hits[0])
    rows: Rows = [[{"text": _label(env, u), "callback_data": f"asub:u:{u}"}] for u in hits[:20]]
    rows.append([{"text": "🔎 Search again", "callback_data": "asub:find"}])
    rows.append(_back_row())
    return f"🔎 {len(hits)} matches for <b>{H(term)}</b>" + (" (first 20 shown)" if len(hits) > 20 else ""), rows


# ----------------------------------------------------------------------------- callbacks
def _ask(env, chat_id, user_id, kind, data, prompt, cancel_to) -> None:
    env["set_await"](user_id, kind, data)
    env["send"](chat_id, prompt, [[{"text": "✖️ Cancel", "callback_data": cancel_to}]])


def handle_callback(data: str, chat_id: int, message_id: int, user_id: int, env: Dict[str, Any]) -> bool:
    p = data.split(":")
    if p[0] != "asub":
        return False
    if user_id not in env["admin_ids"]:
        return True
    act = p[1] if len(p) > 1 else "home"
    edit = lambda t, r: env["edit"](chat_id, message_id, t, r)    # noqa: E731
    try:
        uid = int(p[2]) if act in ("u", "add", "red", "cust", "msg", "inv", "rmq", "rm", "dsc", "pcl") and len(p) > 2 else None
    except ValueError:
        edit("Invalid user.", [_back_row()]); return True
    env["clear_await"](user_id)
    if act == "home":
        edit(*home(env))
    elif act == "l" and len(p) > 3:
        edit(*list_page(env, p[2], int(p[3]) if p[3].lstrip("-").isdigit() else 0))
    elif act == "go" and len(p) > 2:
        _ask(env, chat_id, user_id, "adm_asub_page", {"flt": p[2]}, "🔢 Send the page number:", f"asub:l:{p[2]}:0")
    elif act == "find":
        _ask(env, chat_id, user_id, "adm_asub_find", {}, "🔎 Send a user ID, @username, or part of a name:", "asub:home")
    elif act == "atfind":
        _ask(env, chat_id, user_id, "adm_asub_atfind", {}, "🤖 Send the user ID to open in Auto-Trade admin:", "atm:home")
    elif act == "u" and uid is not None:
        edit(*detail(env, uid))
    elif act == "add" and uid is not None and len(p) > 3 and p[3].isdigit():
        res = env["extend"](uid, int(p[3]))
        edit(*detail(env, uid, f"✅ {H(res)}"))
    elif act == "red" and uid is not None and len(p) > 3 and p[3].isdigit():
        res = env["reduce"](uid, int(p[3]))
        edit(*detail(env, uid, f"✅ {H(res)}"))
    elif act == "cust" and uid is not None and len(p) > 3:
        sign = "r" if p[3] == "r" else "a"
        _ask(env, chat_id, user_id, "adm_asub_days", {"uid": uid, "sign": sign},
             f"✏️ How many days to {'subtract from' if sign == 'r' else 'add to'} <code>{uid}</code>? (1–{MAX_CUSTOM_DAYS})", f"asub:u:{uid}")
    elif act == "msg" and uid is not None:
        _ask(env, chat_id, user_id, "adm_asub_msg", {"uid": uid},
             f"✉️ Type the message to send to <code>{uid}</code> (plain text):", f"asub:u:{uid}")
    elif act == "inv" and uid is not None:
        link = env["invite"](uid)
        if link:
            env["tell"](uid, f"🔗 Here is your channel invite link (valid for 1 hour, single use):\n{link}")
            edit(*detail(env, uid, "✅ New one-time invite link sent to the user."))
        else:
            edit(*detail(env, uid, "❌ Couldn't create the invite link (check bot permissions in the channel)."))
    elif act == "rmq" and uid is not None:
        edit(f"🗑 Remove <code>{uid}</code> from the channel and delete the subscription?\nThe user is notified. This cannot be undone.",
             [[{"text": "✅ Yes, remove", "callback_data": f"asub:rm:{uid}"}], [{"text": "↩️ Cancel", "callback_data": f"asub:u:{uid}"}]])
    elif act == "rm" and uid is not None:
        res = env["remove"](uid)
        edit(*detail(env, uid, f"✅ {H(res)}"))
    elif act == "dsc" and uid is not None:
        (env["applied"]() or {}).pop(str(uid), None)
        edit(*detail(env, uid, "✅ Discount cleared."))
    elif act == "pcl" and uid is not None:
        pend = env["pending"]()
        pend[:] = [x for x in pend if x.get("user_id") != uid]
        edit(*detail(env, uid, "✅ Pending order(s) cancelled."))
    else:
        edit(*home(env))
    return True


def handle_text(chat_id: int, user_id: int, text: str, awaiting: Dict[str, Any], env: Dict[str, Any]) -> bool:
    kind = awaiting.get("kind", "")
    d = awaiting.get("data") or {}
    if not str(kind).startswith("adm_asub") or user_id not in env["admin_ids"]:
        return False
    env["clear_await"](user_id)
    t = text.strip()
    if kind == "adm_asub_page":
        n = env["parse_num"](t)
        flt = d.get("flt", "all")
        if n is None or n < 1:
            env["send"](chat_id, "❌ Not a valid page number.", [_back_row(f"asub:l:{flt}:0")]); return True
        env["send"](chat_id, *list_page(env, flt, int(n) - 1)); return True
    if kind == "adm_asub_find":
        env["send"](chat_id, *search_results(env, t)); return True
    if kind == "adm_asub_atfind":
        at = env.get("at")
        if at is None or not t.lstrip("-").isdigit():
            env["send"](chat_id, "❌ Send a numeric user ID.", [_back_row("atm:home")]); return True
        tx, rows = at.admin_user(t)
        env["send"](chat_id, tx, rows); return True
    if kind == "adm_asub_days":
        n = env["parse_num"](t)
        uid = int(d["uid"])
        if n is None or int(n) < 1 or int(n) > MAX_CUSTOM_DAYS or int(n) != n:
            env["send"](chat_id, f"❌ Send a whole number of days between 1 and {MAX_CUSTOM_DAYS}.", [_back_row(f"asub:u:{uid}")]); return True
        res = env["reduce"](uid, int(n)) if d.get("sign") == "r" else env["extend"](uid, int(n))
        env["send"](chat_id, *detail(env, uid, f"✅ {H(res)}")); return True
    if kind == "adm_asub_msg":
        uid = int(d["uid"])
        if not t:
            env["send"](chat_id, "❌ Empty message.", [_back_row(f"asub:u:{uid}")]); return True
        env["tell"](uid, "📩 <b>Message from the admin</b>\n\n" + H(t[:3500]))
        env["send"](chat_id, *detail(env, uid, "✅ Message sent.")); return True
    return False

# -*- coding: utf-8 -*-
"""
Auto Trading hub of the TICB signal bot.

What it does (everything the member sees is English; code comments are English too so the three projects match):

  * Active channel subscribers get an "Auto Trading" button at the TOP of the main menu (a Telegram mini-app).
  * The member picks ONE exchange: Nobitex (Persian speakers, @TIC2TICbot) or Toobit (English speakers, @TIC2TBTbot).
    The choice is confirmed in the bot, stored in data/autotrade_choices.json and is then LOCKED: only an admin can
    move a member to the other exchange (member -> "contact support").
  * Auto trading is INCLUDED in the channel subscription (no separate fee).  The registry published here
    (data/autotrade_registry.json, HMAC-signed when AUTOTRADE_LINK_SECRET is set) is what the two executor bots read:
    active subscription + exchange == theirs  =>  they trade for that member; subscription ends / is cancelled  =>
    new entries stop automatically (open trades stay protected by their stop / targets).
  * Admin tools: /admin_at, /admin_at_user, /admin_at_move, /admin_at_clear + a button panel ("atm:" callbacks).

The module has no dependency on subscription_bot.py: everything it needs is injected (see AutoTrade.__init__),
which keeps it unit-testable offline.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

H = html.escape

EXCHANGES: Dict[str, Dict[str, str]] = {
    "nobitex": {
        "label": "Nobitex", "code": "nob", "bot_env": "AUTOTRADE_BOT_NOBITEX", "bot_default": "TIC2TICbot",
        "audience": "Built for Persian speakers",
        "about": "Iranian exchange · futures trading in USDT · Persian interface in the bot",
    },
    "toobit": {
        "label": "Toobit", "code": "tob", "bot_env": "AUTOTRADE_BOT_TOOBIT", "bot_default": "TIC2TBTbot",
        "audience": "Built for English speakers",
        "about": "Global exchange · USDT-M perpetual futures · English interface in the bot",
    },
}
CODE_TO_KEY = {v["code"]: k for k, v in EXCHANGES.items()}
REGISTRY_VERSION = 1
REGISTRY_REFRESH_SECONDS = 6 * 3600        # rewrite an unchanged registry at least this often (proves the bot is alive)
PAGE_SIZE = 8


# --------------------------------------------------------------------------- registry format (shared with ticb_link.py)
def canonical_body(doc: dict) -> bytes:
    body = {"version": doc.get("version", 1), "enabled": bool(doc.get("enabled", True)), "entries": doc.get("entries") or {}}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(doc: dict, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_body(doc), hashlib.sha256).hexdigest()


def build_registry(choices: Dict[str, dict], subscribers: List[dict], paused: bool, now: float, enabled: bool,
                   secret: str = "") -> dict:
    subs = {str(s.get("user_id")): s for s in subscribers}
    entries: Dict[str, dict] = {}
    for uid, c in sorted(choices.items()):
        ex = c.get("exchange")
        if ex not in EXCHANGES:
            continue
        s = subs.get(str(uid))
        status, exp = "inactive", 0
        if s:
            exp = int(s.get("expiry_ts") or 0)
            if paused:
                # Maintenance pause: nobody is removed for the passing of time (and everyone is credited the pause
                # afterwards), so the registry must not cut anyone off either.  Day-granular => no file churn.
                exp = max(exp, (int(now) // 86400 + 2) * 86400)
            status = "active" if exp > now else "inactive"
        entries[str(uid)] = {"exchange": ex, "status": status, "expiry_ts": exp, "chosen_at": int(c.get("chosen_at") or 0)}
    doc = {"version": REGISTRY_VERSION, "enabled": bool(enabled), "generated_at": int(now), "entries": entries}
    if secret:
        doc["sig"] = sign(doc, secret)
    return doc


class AutoTrade:
    """deps (all injected by subscription_bot.py):
        load(name, default) / save(name, data)      JSON files in data/
        send(chat_id, text, keyboard=None) / edit(chat_id, message_id, text, keyboard=None)
        alert_admins(text, keyboard=None)
        subscribers()  -> the live list of subscriber dicts      paused() -> bool (maintenance pause)
        fmt_date(ts) -> str        bot_username() -> TICB bot username (for the mini-app deep link)
        publish_now()  -> optional: commit+push right away (a choice should reach the executors in seconds)
    """

    def __init__(self, *, load, save, send, edit, alert_admins, subscribers, paused, fmt_date, bot_username,
                 publish_now=None, now=time.time, admin_ids=()):
        self._load, self._save = load, save
        self.send, self.edit, self.alert_admins = send, edit, alert_admins
        self.subscribers, self.paused, self.fmt_date = subscribers, paused, fmt_date
        self.bot_username = bot_username
        self.publish_now = publish_now
        self.now = now
        self.admin_ids = set(admin_ids)
        env = os.environ.get
        self.miniapp_url = (env("AUTOTRADE_MINIAPP_URL") or "").strip()
        self.secret = (env("AUTOTRADE_LINK_SECRET") or "").strip()
        self.support = (env("SUPPORT_CONTACT") or "").strip()
        self._last_published = b""
        self._last_published_at = 0.0

    # ------------------------------------------------------------------ storage
    def data(self) -> dict:
        d = self._load("autotrade_choices.json", {})
        if not isinstance(d, dict):
            d = {}
        d.setdefault("users", {})
        d.setdefault("settings", {"enabled": True})
        return d

    def enabled(self) -> bool:
        return bool(self.data()["settings"].get("enabled", True))

    def choice(self, user_id) -> Optional[dict]:
        return self.data()["users"].get(str(user_id))

    def bot_for(self, ex_key: str) -> str:
        e = EXCHANGES[ex_key]
        return (os.environ.get(e["bot_env"]) or e["bot_default"]).strip().lstrip("@")

    def bot_url(self, ex_key: str) -> str:
        return f"https://t.me/{self.bot_for(ex_key)}"

    def sub_of(self, user_id) -> Optional[dict]:
        return next((s for s in self.subscribers() if str(s.get("user_id")) == str(user_id)), None)

    def is_active_subscriber(self, user_id) -> bool:
        s = self.sub_of(user_id)
        return bool(s) and (int(s.get("expiry_ts") or 0) > self.now() or bool(self.paused()))

    # ------------------------------------------------------------------ registry publishing
    def registry_doc(self) -> dict:
        d = self.data()
        return build_registry(d["users"], self.subscribers(), bool(self.paused()), self.now(),
                              bool(d["settings"].get("enabled", True)), self.secret)

    def publish_registry(self, force: bool = False) -> bool:
        """Write data/autotrade_registry.json when its content changed (or it is older than REGISTRY_REFRESH_SECONDS).
        Returns True when written.  Never raises."""
        try:
            doc = self.registry_doc()
            body = canonical_body(doc)
            now = self.now()
            if not self._last_published:                    # first call in this process: compare with the file on disk
                old = self._load("autotrade_registry.json", {})
                if isinstance(old, dict) and old.get("entries") is not None:
                    self._last_published = canonical_body(old)
                    self._last_published_at = float(old.get("generated_at") or 0)
            if not force and body == self._last_published and now - self._last_published_at < REGISTRY_REFRESH_SECONDS:
                return False
            self._save("autotrade_registry.json", doc)
            self._last_published, self._last_published_at = body, now
            return True
        except Exception as e:                              # a registry problem must never stop the signal bot
            print(f"[warn] autotrade registry publish failed: {e}")
            return False

    def _changed(self) -> None:
        """A choice / move happened: write the registry and push it right away."""
        if self.publish_registry(force=True) and self.publish_now:
            try:
                self.publish_now()
            except Exception as e:
                print(f"[warn] autotrade immediate push failed: {e}")

    # ------------------------------------------------------------------ menu pieces
    def webapp_url_for(self, user_id) -> str:
        if not self.miniapp_url:
            return ""
        c = self.choice(user_id)
        s = self.sub_of(user_id)
        days = max(0, int(((s or {}).get("expiry_ts") or 0) - self.now()) // 86400) if s else 0
        q = {"v": "1", "ex": (c or {}).get("exchange", ""), "open": "1" if self.enabled() else "0", "d": str(days),
             "me": self.bot_username() or "",
             "nob": self.bot_for("nobitex"), "tob": self.bot_for("toobit")}
        sep = "&" if "?" in self.miniapp_url else "?"
        return self.miniapp_url + sep + urlencode(q)

    def menu_rows(self, user_id) -> List[List[dict]]:
        """The row placed at the very top of the main menu: only for active subscribers."""
        if not self.is_active_subscriber(user_id):
            return []
        url = self.webapp_url_for(user_id)
        if url:
            return [[{"text": "🤖 Auto Trading", "web_app": {"url": url}}]]
        return [[{"text": "🤖 Auto Trading", "callback_data": "at:home"}]]

    def admin_row(self) -> List[dict]:
        return [{"text": "🤖 Auto-Trade Admin", "callback_data": "atm:home"}]

    # ------------------------------------------------------------------ member screens
    def _back(self) -> List[dict]:
        return [{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]

    def home_screen(self, user_id) -> Tuple[str, List[List[dict]]]:
        c = self.choice(user_id)
        active = self.is_active_subscriber(user_id)
        if c and c.get("exchange") in EXCHANGES:
            ex = c["exchange"]
            e = EXCHANGES[ex]
            s = self.sub_of(user_id)
            if active:
                state = f"✅ Active — runs as long as your subscription does (until <b>{H(self.fmt_date(s['expiry_ts']))}</b>)."
            else:
                state = "⏸ Paused — your subscription has ended. Renew it and auto trading resumes by itself (same exchange, same settings)."
            text = (f"🤖 <b>Auto Trading</b>\n\nYour exchange: <b>{H(e['label'])}</b> 🔒\n{state}\n\n"
                    "• Included in your subscription — no extra fee.\n"
                    "• One exchange per member. Switching is done by the admin only"
                    + (f" — contact {H(self.support)}." if self.support else " — use 💬 Support.") + "\n"
                    "• Open trades stay protected by their stop-loss and targets even if the subscription ends.\n\n"
                    f"Next step: open the {H(e['label'])} bot, accept its terms, connect your API key "
                    "(<b>Read + Trade only — never Withdraw</b>) and set your risk.")
            rows = [[{"text": f"▶️ Open {e['label']} bot", "url": self.bot_url(ex)}]]
            if not active:
                rows.append([{"text": "💎 Renew subscription", "callback_data": "menu:plans"}])
            rows.append(self._back())
            return text, rows
        if not active:
            return ("🤖 <b>Auto Trading</b>\n\nAuto trading is included for <b>active subscribers</b>. "
                    "Subscribe to unlock it — no extra fee."), [[{"text": "💎 View Plans", "callback_data": "menu:plans"}], self._back()]
        if not self.enabled():
            return ("🤖 <b>Auto Trading</b>\n\nNew activations are temporarily closed. Please check back soon."), [self._back()]
        text = ("🤖 <b>Auto Trading</b>\n\nLet the bot place the channel's signals on <b>your own exchange account</b> — "
                "entries, stop-loss and targets, automatically. It's <b>included in your subscription</b> (no extra fee) and "
                "stays on while your subscription is active.\n\n"
                "⚠️ <b>Choose carefully: you can use ONE exchange only.</b> After you confirm, only the admin can change it.\n\n"
                + "\n".join(f"• <b>{H(v['label'])}</b> — {H(v['audience'])}. {H(v['about'])}." for v in EXCHANGES.values()))
        rows = [[{"text": f"{v['label']} — {v['audience'].replace('Built for ', '')}", "callback_data": f"at:pick:{v['code']}"}]
                for v in EXCHANGES.values()]
        rows.append(self._back())
        return text, rows

    def confirm_screen(self, code: str) -> Tuple[str, List[List[dict]]]:
        ex = CODE_TO_KEY[code]
        e = EXCHANGES[ex]
        text = (f"🤖 <b>Confirm your exchange: {H(e['label'])}</b>\n\n"
                "Please read before confirming:\n"
                "• You can auto-trade on <b>ONE exchange only</b> — never both.\n"
                "• After you confirm, <b>only the admin can change it</b>.\n"
                "• It's included in your subscription; it stays active as long as your subscription is active.\n"
                f"• Next you'll open the {H(e['label'])} bot, accept its terms and connect an API key "
                "(<b>Read + Trade only, never Withdraw</b>).\n\n"
                "Is this the exchange you actually trade on?")
        return text, [[{"text": f"✅ Yes, use {e['label']}", "callback_data": f"at:ok:{code}"}],
                      [{"text": "↩️ Choose again", "callback_data": "at:home"}]]

    def choose(self, user_id, code: str, name: str = "") -> Tuple[bool, str, List[List[dict]]]:
        """Store the member's choice.  Idempotent and race-safe (a second tap never changes an existing choice)."""
        ex = CODE_TO_KEY.get(code)
        if not ex:
            return False, "Unknown exchange.", [self._back()]
        if not self.is_active_subscriber(user_id):
            t, r = self.home_screen(user_id)
            return False, t, r
        d = self.data()
        cur = d["users"].get(str(user_id))
        if cur and cur.get("exchange") in EXCHANGES:
            t, r = self.home_screen(user_id)
            if cur["exchange"] != ex:
                t = ("ℹ️ Your account is already assigned to <b>" + H(EXCHANGES[cur["exchange"]]["label"]) +
                     "</b>; it can't be changed by you.\n\n") + t
            return False, t, r
        if not d["settings"].get("enabled", True):
            t, r = self.home_screen(user_id)
            return False, t, r
        d["users"][str(user_id)] = {"exchange": ex, "chosen_at": int(self.now()), "chosen_by": "user", "name": name,
                                    "history": [{"ts": int(self.now()), "from": None, "to": ex, "by": "user"}]}
        self._save("autotrade_choices.json", d)
        self._changed()
        e = EXCHANGES[ex]
        try:
            self.alert_admins(f"🤖 Auto-trade: user <code>{user_id}</code>{(' ' + H(name)) if name else ''} chose <b>{H(e['label'])}</b>.",
                              keyboard=[[{"text": "👤 Manage", "callback_data": f"atm:u:{user_id}"}]])
        except Exception:
            pass
        text = (f"✅ <b>{H(e['label'])} auto trading is assigned to your account.</b>\n\n"
                f"Open the {H(e['label'])} bot now: accept its terms, connect your API key "
                "(<b>Read + Trade only — never Withdraw</b>) and set your risk. It's already included in your subscription.")
        return True, text, [[{"text": f"▶️ Open {e['label']} bot", "url": self.bot_url(ex)}], self._back()]

    # ------------------------------------------------------------------ entry points used by subscription_bot
    def handle_start_param(self, chat_id, user_id, param: str, name: str = "") -> bool:
        """/start atsel_<code> (from the mini-app) or /start autotrade.  Returns True when consumed."""
        param = (param or "").strip()
        if param == "autotrade":
            t, r = self.home_screen(user_id)
            self.send(chat_id, t, keyboard=r)
            return True
        if param.startswith("atsel_"):
            code = param[6:]
            if code not in CODE_TO_KEY:
                t, r = self.home_screen(user_id)
            elif not self.is_active_subscriber(user_id) or self.choice(user_id) or not self.enabled():
                t, r = self.home_screen(user_id)
                c = self.choice(user_id)
                if c and c.get("exchange") != CODE_TO_KEY[code]:
                    t = ("ℹ️ Your account is already assigned to <b>" + H(EXCHANGES[c["exchange"]]["label"]) + "</b>.\n\n") + t
            else:
                t, r = self.confirm_screen(code)
            self.send(chat_id, t, keyboard=r)
            return True
        return False

    def handle_callback(self, data: str, chat_id, message_id, user_id, name: str = "") -> bool:
        if data.startswith("atm:"):
            return self.handle_admin_callback(data, chat_id, message_id, user_id)
        if not data.startswith("at:"):
            return False
        parts = data.split(":")
        if parts[1] == "home":
            t, r = self.home_screen(user_id)
        elif parts[1] == "pick" and len(parts) > 2 and parts[2] in CODE_TO_KEY:
            if self.is_active_subscriber(user_id) and not self.choice(user_id) and self.enabled():
                t, r = self.confirm_screen(parts[2])
            else:
                t, r = self.home_screen(user_id)
        elif parts[1] == "ok" and len(parts) > 2:
            _, t, r = self.choose(user_id, parts[2], name)
        else:
            t, r = self.home_screen(user_id)
        self.edit(chat_id, message_id, t, keyboard=r)
        return True

    # ------------------------------------------------------------------ admin
    def counts(self) -> Dict[str, Dict[str, int]]:
        doc = self.registry_doc()
        out = {k: {"active": 0, "inactive": 0} for k in EXCHANGES}
        for e in doc["entries"].values():
            out[e["exchange"]][e["status"]] += 1
        return out

    def admin_home(self) -> Tuple[str, List[List[dict]]]:
        c = self.counts()
        lines = ["🤖 <b>Auto-Trade Admin</b>", ""]
        for k, v in EXCHANGES.items():
            lines.append(f"• <b>{H(v['label'])}</b> (@{H(self.bot_for(k))}): {c[k]['active']} active · {c[k]['inactive']} paused")
        en = self.enabled()
        lines += ["", f"New activations: <b>{'OPEN' if en else 'CLOSED'}</b>",
                  f"Mini-app: {'set ✅' if self.miniapp_url else 'not set (members get the in-chat chooser)'}",
                  f"Registry signing: {'ON' if self.secret else 'OFF (set AUTOTRADE_LINK_SECRET)'}",
                  "", "Members choose once; only you can move them (button below or /admin_at_move &lt;id&gt; nob|tob).",
                  "<i>Moving a member stops new entries on the old exchange at once; trades already open there stay "
                  "protected until they close, then the old API key is removed.</i>"]
        rows = [[{"text": f"👥 {v['label']} members", "callback_data": f"atm:l:{v['code']}:0"}] for v in EXCHANGES.values()]
        rows.append([{"text": "🔒 Close new activations" if en else "🔓 Open new activations", "callback_data": "atm:tog"}])
        rows.append([{"text": "⬅️ Admin menu", "callback_data": "menu:home"}])
        return "\n".join(lines), rows

    def admin_list(self, code: str, page: int) -> Tuple[str, List[List[dict]]]:
        ex = CODE_TO_KEY[code]
        doc = self.registry_doc()["entries"]
        users = sorted((u for u, e in doc.items() if e["exchange"] == ex), key=lambda u: int(u) if u.isdigit() else 0)
        pages = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        chunk = users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        d = self.data()["users"]
        rows = []
        for u in chunk:
            nm = (d.get(u) or {}).get("name") or ""
            rows.append([{"text": f"{'✅' if doc[u]['status'] == 'active' else '⏸'} {u} {nm}".strip(), "callback_data": f"atm:u:{u}"}])
        nav = []
        if page > 0:
            nav.append({"text": "◀️ Prev", "callback_data": f"atm:l:{code}:{page - 1}"})
        if page < pages - 1:
            nav.append({"text": "Next ▶️", "callback_data": f"atm:l:{code}:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([{"text": "⬅️ Back", "callback_data": "atm:home"}])
        return f"👥 <b>{H(EXCHANGES[ex]['label'])}</b> members ({len(users)}) — page {page + 1}/{pages}", rows

    def admin_user(self, uid: str) -> Tuple[str, List[List[dict]]]:
        c = self.choice(uid)
        s = self.sub_of(uid)
        lines = [f"👤 <b>User</b> <code>{H(str(uid))}</code>"]
        if c:
            ex = c["exchange"]
            lines.append(f"Exchange: <b>{H(EXCHANGES[ex]['label'])}</b> (chosen {H(self.fmt_date(c.get('chosen_at', 0)))} by {H(str(c.get('chosen_by', '?')))})")
        else:
            lines.append("Exchange: <i>not chosen yet</i>")
        lines.append("Subscription: " + (f"active until {H(self.fmt_date(s['expiry_ts']))}" if s else "none"))
        for h in (c or {}).get("history", [])[-5:]:
            lines.append(f"  · {H(self.fmt_date(h['ts']))}: {H(str(h.get('from') or '—'))} → {H(str(h['to']))} ({H(str(h['by']))})")
        rows = []
        for k, v in EXCHANGES.items():
            if not c or c["exchange"] != k:
                rows.append([{"text": f"{'➡️ Move to' if c else '➕ Assign'} {v['label']}", "callback_data": f"atm:mv:{uid}:{v['code']}"}])
        if c:
            rows.append([{"text": "♻️ Clear choice (member picks again)", "callback_data": f"atm:clr:{uid}"}])
        rows.append([{"text": "⬅️ Back", "callback_data": "atm:home"}])
        return "\n".join(lines), rows

    def move_confirm(self, uid: str, code: str) -> Tuple[str, List[List[dict]]]:
        e = EXCHANGES[CODE_TO_KEY[code]]
        c = self.choice(uid)
        old = EXCHANGES[c["exchange"]]["label"] if c else None
        text = (f"{'Move' if c else 'Assign'} user <code>{H(str(uid))}</code> {('from <b>' + H(old) + '</b> ') if old else ''}to <b>{H(e['label'])}</b>?\n\n"
                + ("• New entries on the old exchange stop immediately; trades already open there stay protected (stop-loss/targets) until they close, "
                   "then the old API key is deleted automatically.\n" if old else "")
                + "• The member is notified and must connect an API key in the new bot.")
        return text, [[{"text": "✅ Confirm", "callback_data": f"atm:mvok:{uid}:{code}"}], [{"text": "↩️ Cancel", "callback_data": f"atm:u:{uid}"}]]

    def admin_set(self, uid, code: str, by) -> str:
        """Move / assign a member.  Returns a short result text."""
        ex = CODE_TO_KEY.get(code)
        if not ex:
            return "Unknown exchange (use nob or tob)."
        d = self.data()
        cur = d["users"].get(str(uid))
        if cur and cur.get("exchange") == ex:
            return "Already on that exchange."
        hist = (cur or {}).get("history", [])
        hist.append({"ts": int(self.now()), "from": (cur or {}).get("exchange"), "to": ex, "by": f"admin:{by}"})
        d["users"][str(uid)] = {"exchange": ex, "chosen_at": int(self.now()), "chosen_by": f"admin:{by}",
                                "name": (cur or {}).get("name", ""), "history": hist[-20:]}
        self._save("autotrade_choices.json", d)
        self._changed()
        e = EXCHANGES[ex]
        try:
            self.send(int(uid) if str(uid).lstrip("-").isdigit() else uid,
                      f"🔁 <b>Your auto-trading exchange was set to {H(e['label'])}</b> by the admin.\n\n"
                      f"Open the {H(e['label'])} bot, accept its terms and connect your API key "
                      "(<b>Read + Trade only — never Withdraw</b>).\n"
                      "Reminder: one exchange only; trades still open on the previous exchange stay protected until they close.",
                      keyboard=[[{"text": f"▶️ Open {e['label']} bot", "url": self.bot_url(ex)}]])
        except Exception:
            pass
        return f"✅ User {uid} → {e['label']}."

    def admin_clear(self, uid, by) -> str:
        d = self.data()
        if str(uid) not in d["users"]:
            return "No choice stored for that user."
        cur = d["users"].pop(str(uid))
        self._save("autotrade_choices.json", d)
        self._changed()
        return f"♻️ Cleared {uid} (was {EXCHANGES.get(cur.get('exchange'), {}).get('label', '?')}). They can pick again; the old exchange stops new entries."

    def handle_admin_callback(self, data: str, chat_id, message_id, user_id) -> bool:
        if user_id not in self.admin_ids:
            return True
        p = data.split(":")
        if p[1] == "home":
            t, r = self.admin_home()
        elif p[1] == "tog":
            d = self.data()
            d["settings"]["enabled"] = not d["settings"].get("enabled", True)
            self._save("autotrade_choices.json", d)
            self._changed()
            t, r = self.admin_home()
        elif p[1] == "l" and len(p) > 3 and p[2] in CODE_TO_KEY:
            t, r = self.admin_list(p[2], int(p[3]) if p[3].isdigit() else 0)
        elif p[1] == "u" and len(p) > 2:
            t, r = self.admin_user(p[2])
        elif p[1] == "mv" and len(p) > 3 and p[3] in CODE_TO_KEY:
            t, r = self.move_confirm(p[2], p[3])
        elif p[1] == "mvok" and len(p) > 3:
            res = self.admin_set(p[2], p[3], user_id)
            t, r = self.admin_user(p[2])
            t = res + "\n\n" + t
        elif p[1] == "clr" and len(p) > 2:
            t = f"Clear the choice of <code>{H(p[2])}</code>? The member will be able to pick again."
            r = [[{"text": "✅ Confirm", "callback_data": f"atm:clrok:{p[2]}"}], [{"text": "↩️ Cancel", "callback_data": f"atm:u:{p[2]}"}]]
        elif p[1] == "clrok" and len(p) > 2:
            res = self.admin_clear(p[2], user_id)
            t, r = self.admin_user(p[2])
            t = res + "\n\n" + t
        else:
            t, r = self.admin_home()
        self.edit(chat_id, message_id, t, keyboard=r)
        return True

    def handle_admin_text(self, chat_id, text: str, user_id) -> bool:
        """/admin_at, /admin_at_user <id>, /admin_at_move <id> nob|tob, /admin_at_clear <id>.  True when consumed."""
        parts = text.split()
        cmd = parts[0].lower().split("@")[0] if parts else ""
        if not cmd.startswith("/admin_at"):
            return False
        if user_id not in self.admin_ids:
            return True
        if cmd == "/admin_at":
            t, r = self.admin_home()
        elif cmd == "/admin_at_user" and len(parts) == 2:
            t, r = self.admin_user(parts[1])
        elif cmd == "/admin_at_move" and len(parts) == 3 and parts[1].lstrip("-").isdigit():
            code = parts[2].lower()
            code = code if code in CODE_TO_KEY else {"nobitex": "nob", "toobit": "tob"}.get(code, code)
            if code not in CODE_TO_KEY:
                t, r = "Usage: /admin_at_move &lt;user_id&gt; nob|tob", []
            else:
                t, r = self.move_confirm(parts[1], code)
        elif cmd == "/admin_at_clear" and len(parts) == 2:
            t = f"Clear the choice of <code>{H(parts[1])}</code>?"
            r = [[{"text": "✅ Confirm", "callback_data": f"atm:clrok:{parts[1]}"}], [{"text": "↩️ Cancel", "callback_data": "atm:home"}]]
        else:
            t, r = ("Usage:\n/admin_at\n/admin_at_user &lt;id&gt;\n/admin_at_move &lt;id&gt; nob|tob\n/admin_at_clear &lt;id&gt;"), []
        self.send(chat_id, t, keyboard=r or None)
        return True

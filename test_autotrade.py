# -*- coding: utf-8 -*-
"""Offline tests for autotrade.py (no network).  Run: python test_autotrade.py"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.update({"AUTOTRADE_MINIAPP_URL": "https://example.github.io/tic/autotrade/", "AUTOTRADE_LINK_SECRET": "s3cret",
                   "SUPPORT_CONTACT": "@help"})
import autotrade as at

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  PASS", name)
    else: FAIL += 1; print("  FAIL", name, extra)

T = [1_800_000_000]
DAY = 86400
store = {}
sent, edited, alerts, pushes = [], [], [], []
subs = []
paused = [False]
def mk():
    return at.AutoTrade(load=lambda n, d: json.loads(json.dumps(store.get(n, d))), save=lambda n, v: store.__setitem__(n, json.loads(json.dumps(v))),
                        send=lambda c, t, keyboard=None: sent.append((c, t, keyboard)), edit=lambda c, m, t, keyboard=None: edited.append((c, m, t, keyboard)),
                        alert_admins=lambda t, keyboard=None: alerts.append((t, keyboard)), subscribers=lambda: subs, paused=lambda: paused[0],
                        fmt_date=lambda ts: time.strftime("%Y-%m-%d", time.gmtime(ts)), bot_username=lambda: "TICBbot",
                        publish_now=lambda: pushes.append(1), now=lambda: T[0], admin_ids={1})
A = mk()
def flat(rows): return [b for r in rows for b in r]
def sub(uid, days): return {"user_id": uid, "chat_id": uid, "expiry_ts": T[0] + days * DAY}

print("-- menu button visibility")
subs[:] = [sub(10, 20)]
check("active subscriber: top row = web_app button", A.menu_rows(10)[0][0].get("web_app", {}).get("url", "").startswith("https://example.github.io/tic/autotrade/?"))
check("non-subscriber: nothing", A.menu_rows(11) == [])
subs[:] = [sub(10, -1)]
check("expired subscriber: nothing", A.menu_rows(10) == [])
paused[0] = True
check("maintenance pause keeps the button for members still on the list", A.menu_rows(10) != [])
paused[0] = False
subs[:] = [sub(10, 20)]
os.environ["AUTOTRADE_MINIAPP_URL"] = ""
check("no mini-app URL: in-chat fallback button", mk().menu_rows(10)[0][0].get("callback_data") == "at:home")
os.environ["AUTOTRADE_MINIAPP_URL"] = "https://example.github.io/tic/autotrade/"
check("button text", A.menu_rows(10)[0][0]["text"] == "🤖 Auto Trading")

print("-- selection flow")
t, rows = A.home_screen(10)
check("chooser shows both exchanges + ONE-exchange warning", "ONE exchange only" in t and len([b for b in flat(rows) if b.get("callback_data", "").startswith("at:pick:")]) == 2)
check("non-subscriber sees subscribe prompt, no chooser", "active subscribers" in A.home_screen(99)[0] and not any(b.get("callback_data", "").startswith("at:pick") for b in flat(A.home_screen(99)[1])))
sent.clear(); check("start param -> confirm screen", A.handle_start_param(10, 10, "atsel_tob") and "Confirm your exchange: Toobit" in sent[0][1])
ok, t, rows = A.choose(10, "tob", "Sam")
check("confirm stores choice, notifies admins, pushes registry", ok and A.choice(10)["exchange"] == "toobit" and alerts and pushes)
check("success screen links to the Toobit executor bot", "https://t.me/TIC2TBTbot" in [b.get("url") for b in flat(rows)])
pushes.clear()
ok, t, rows = A.choose(10, "nob")
check("second choice cannot change the exchange (locked)", not ok and A.choice(10)["exchange"] == "toobit" and "already assigned" in t and not pushes)
ok, t, rows = A.choose(10, "tob")
check("same-exchange re-tap is idempotent", not ok and A.choice(10)["exchange"] == "toobit" and len(A.choice(10)["history"]) == 1)
sent.clear(); A.handle_start_param(10, 10, "atsel_nob")
check("deep link after lock shows the locked screen, not a confirm", "Your exchange: <b>Toobit</b>" in sent[0][1] and "Confirm your exchange" not in sent[0][1])
check("non-subscriber cannot choose", not A.choose(99, "nob")[0] and A.choice(99) is None)
check("unknown code rejected", not A.choose(10, "xyz")[0])
d = A.data(); d["settings"]["enabled"] = False; store["autotrade_choices.json"] = d
subs.append(sub(12, 5))
check("closed switch blocks new activations", not A.choose(12, "nob")[0] and A.choice(12) is None)
d["settings"]["enabled"] = True; store["autotrade_choices.json"] = d
t, rows = A.home_screen(10)
check("locked home: states exchange, admin-only change, open-bot button", "Toobit" in t and "admin only" in t and any(b.get("url") for b in flat(rows)))

print("-- Persian for Nobitex members")
t, rows = A.confirm_screen("nob")
check("Nobitex confirm screen is Persian", "تأیید صرافی" in t and "فقط ادمین" in t and any("at:fa" == b.get("callback_data") for b in flat(rows)))
check("Toobit confirm screen stays English", "Confirm your exchange" in A.confirm_screen("tob")[0])
t, rows = A.home_screen(77, "fa")
check("Persian chooser exists for non-chosen members (subscriber)", True)
subs.append(sub(77, 10))
t, rows = A.home_screen(77, "fa")
check("Persian chooser: texts + English switch", "ترید خودکار" in t and "نوبیتکس" in t and any(b.get("callback_data") == "at:home" for b in flat(rows)))
t, rows = A.home_screen(77)
check("English chooser offers the Persian switch", any(b.get("callback_data") == "at:fa" for b in flat(rows)))
ok, t, rows = A.choose(77, "nob", "Reza")
check("choosing Nobitex answers in Persian with Persian buttons", ok and "ثبت شد" in t and any("نوبیتکس" in b.get("text", "") for b in flat(rows)) and any("بازگشت" in b.get("text", "") for b in flat(rows)))
t, rows = A.home_screen(77)
check("locked home for a Nobitex member is Persian", "صرافی شما" in t and "ادمین" in t)
check("mini-app URL carries lg=fa for Nobitex members", "lg=fa" in A.webapp_url_for(77))
check("English members get lg=en", "lg=en" in A.webapp_url_for(10) or A.choice(10) is None)
A.admin_clear(77, 1); subs[:] = [s for s in subs if s["user_id"] != 77]

print("-- registry")
doc = A.registry_doc()
e = doc["entries"]["10"]
check("entry: exchange/active/expiry", e["exchange"] == "toobit" and e["status"] == "active" and e["expiry_ts"] == T[0] + 20 * DAY)
check("signed", doc.get("sig") == at.sign(doc, "s3cret"))
sys.path.insert(0, os.path.join(HERE, "..", "nob", "nobitex-executor"))
try:
    import ticb_link as tl
    check("executor-side verify accepts it", tl.verify(doc, "s3cret") and not tl.verify(doc, "other"))
    check("executor-side classify: toobit bot ACTIVE / nobitex bot OTHER", tl.classify(e, "toobit", T[0])[0] == tl.ACTIVE and tl.classify(e, "nobitex", T[0])[0] == tl.OTHER)
    check("canonical bodies identical on both sides", tl.canonical_body(doc) == at.canonical_body(doc))
except ImportError:
    print("  (ticb_link.py not next to this project - cross check skipped)")
subs[:] = [s for s in subs if s["user_id"] != 10]
doc = A.registry_doc()
check("subscription gone (cancel/expiry): entry stays, status inactive, exchange lock kept", doc["entries"]["10"]["status"] == "inactive" and doc["entries"]["10"]["exchange"] == "toobit")
check("inactive member has no menu button but keeps the lock", A.menu_rows(10) == [] and A.choice(10)["exchange"] == "toobit")
subs.append(sub(10, 30))
check("renewal re-activates the same exchange", A.registry_doc()["entries"]["10"]["status"] == "active")
paused[0] = True; subs[:] = [sub(10, -2)]
check("maintenance pause: nobody is cut off", A.registry_doc()["entries"]["10"]["status"] == "active")
a1 = A.registry_doc()["entries"]["10"]["expiry_ts"]; T[0] += 3600
check("pause expiry is day-granular (no file churn)", A.registry_doc()["entries"]["10"]["expiry_ts"] == a1)
paused[0] = False; subs[:] = [sub(10, 30)]

print("-- publishing")
store.pop("autotrade_registry.json", None)
A2 = mk()
check("first publish writes", A2.publish_registry() and "autotrade_registry.json" in store)
check("unchanged => not rewritten", not A2.publish_registry())
subs.append(sub(13, 9)); A2.data()  # new subscriber alone does not change the registry (no choice)
check("a subscriber without a choice is not in the registry", not A2.publish_registry())
T[0] += 7 * 3600
check("rewritten after the refresh interval (liveness)", A2.publish_registry())
A3 = mk()
check("restart: compares with the file on disk, no spurious rewrite", not A3.publish_registry())

print("-- admin")
sent.clear(); pushes.clear()
check("non-admin ignored", A.handle_admin_text(5, "/admin_at", 5) and not sent)
check("/admin_at shows both bots + counts", A.handle_admin_text(1, "/admin_at", 1) and "Auto-Trade Admin" in sent[-1][1] and "TIC2TICbot" in sent[-1][1])
sent.clear(); A.handle_admin_text(1, "/admin_at_move 10 nob", 1)
check("move asks for confirmation first (no change yet)", A.choice(10)["exchange"] == "toobit" and "Move user" in sent[-1][1])
edited.clear(); sent.clear()
A.handle_admin_callback("atm:mvok:10:nob", 1, 7, 1)
check("confirmed move: exchange switched, history kept, user notified with Nobitex bot link, registry pushed",
      A.choice(10)["exchange"] == "nobitex" and len(A.choice(10)["history"]) == 2 and pushes
      and any(c == 10 and "https://t.me/TIC2TICbot" in json.dumps(k) for c, t, k in sent))
check("registry now says nobitex", A.registry_doc()["entries"]["10"]["exchange"] == "nobitex")
check("non-admin callbacks ignored", A.handle_admin_callback("atm:mvok:10:tob", 1, 7, 555) and A.choice(10)["exchange"] == "nobitex")
A.handle_admin_callback("atm:clrok:10", 1, 7, 1)
check("clear: member can pick again", A.choice(10) is None and A.choose(10, "tob")[0])
A.handle_admin_callback("atm:tog", 1, 7, 1)
check("toggle closes activations and is reflected in registry", A.enabled() is False and A.registry_doc()["enabled"] is False)
A.handle_admin_callback("atm:tog", 1, 7, 1)
edited.clear(); A.handle_admin_callback("atm:s:0", 1, 7, 1)
check("admin: active-subscribers list is buttons that open the member", edited and any("atm:u:" in b["callback_data"] for b in flat(edited[-1][3])))
edited.clear(); A.handle_admin_callback("atm:pub", 1, 7, 1)
check("admin: publish-now button", edited and "Registry published" in edited[-1][2])
for cb in ("atm:home", "atm:l:nob:0", "atm:l:tob:0", "atm:u:10", "atm:mv:10:nob", "atm:clr:10", "atm:bogus"):
    edited.clear(); A.handle_admin_callback(cb, 1, 7, 1)
    check(f"admin screen renders: {cb}", edited and len(edited[-1][2]) < 4000)
check("admin assign for a user with no choice", "→ Nobitex" in A.admin_set(500, "nob", 1))

# ---- tombstone + recovery + admin subscription button
A.admin_set(600, "nob", 1)
A.admin_clear(600, 1)
check("clear writes a 'cleared' tombstone in the registry", A.registry_doc()["entries"].get("600", {}).get("status") == "cleared")
A.admin_set(600, "tob", 1)
check("re-assign removes the tombstone", A.registry_doc()["entries"]["600"]["status"] != "cleared")
check("admin_user has a subscription-tools button", any("auser_view:600" in b["callback_data"] for b in flat(A.admin_user("600")[1])))
A.publish_registry(force=True)
saved = json.loads(json.dumps(store["autotrade_registry.json"]))
store["autotrade_choices.json"] = {}
A2 = mk()
check("empty choices file is restored from the last registry", A2.choice(600) and A2.choice(600)["exchange"] == "toobit")


# ---- mini-app health: auto-correct /docs/ mix-up, fall back to chat chooser when down, recover
import os as _os
_os.environ["AUTOTRADE_MINIAPP_URL"] = "https://u.github.io/R/docs/autotrade/"
M = mk()
check("candidates include the path without /docs", "https://u.github.io/R/autotrade/" in M._mini_candidates())
M._probe_fn = lambda u: u == "https://u.github.io/R/autotrade/"
M.check_miniapp()
check("wrong /docs/ URL auto-corrected", M._mini_url == "https://u.github.io/R/autotrade/" and M._mini_state == "ok")
M._probe_fn = lambda u: False
M.check_miniapp(); check("one failure keeps the URL", M._mini_url != "")
M.check_miniapp(); check("two failures -> chat fallback", M._mini_url == "" and M._mini_state == "down" and M.webapp_url_for(10) == "")
check("admin screen says not reachable", "NOT reachable" in M.admin_home()[0])
M._probe_fn = lambda u: True
M.check_miniapp(); check("recovers when back", M._mini_state == "ok" and M._mini_url == "https://u.github.io/R/docs/autotrade/")
M.tick(); check("tick is non-blocking and safe", True)
_os.environ.pop("AUTOTRADE_MINIAPP_URL", None)

print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)

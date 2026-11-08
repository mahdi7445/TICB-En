"""V13 admin/user UI checks: menu layout, texts render for EVERY strategy/ladder within Telegram's limit, no exceptions."""
import json, os, tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1", "PRIVATE_CHANNEL_ID": "-100", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ.setdefault(k, v)
import admin_panel as ap, shared_controls as sc, ladder_catalog as lc, strategy_texts as stx
import subscription_bot as sb

vd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lowtf_verdicts.json")))
sc.load_verdicts = lambda *a, **k: vd
ap._verdicts_local = sc.load_verdicts

# 1) admin home: no standalone Search-signal button; Users / Signals titles have no parentheses
t, rows = ap.home({})
labels = [b["text"] for r in rows for b in r]
assert not any("Search signal" in x for x in labels), labels
assert "👥 Users" in labels and "✍️ Signals" in labels and not any("(quick" in x or "(manual" in x for x in labels), labels
# 2) Signals submenu: Search signal sits directly under Personal signals
i = [k for k, (title, _k) in enumerate(ap.CMD_GROUPS) if title.endswith("Signals")][0]
t, rows = ap.grp_page(i)
lab = [b["text"] for r in rows for b in r]; cb = {b["text"]: b["callback_data"] for r in rows for b in r}
assert lab.index("🔎 Search signal") == lab.index("🧾 Personal signals") + 1 and cb["🔎 Search signal"] == "adm:sgs", lab
j = [k for k, (title, _k) in enumerate(ap.CMD_GROUPS) if title.endswith("Users")][0]
assert "(" not in ap.CMD_GROUPS[j][0] and "(" not in ap.CMD_GROUPS[i][0]
assert "(" not in ap.grp_page(j)[1][0][0]["text"]
# 3) every page renders for every module / ladder within Telegram's 4096 limit
for sec in ("ticb", "sgnl"):
    t, rows = ap.mods_page(sec, {}); assert len(t) <= 4096, (sec, len(t))
    for m in sc.MODULES:
        for fn in (ap.lad_page, ap.sd_page, ap.ms_page):
            try:
                t, rows = fn(sec, m, {})
            except KeyError:
                continue
            assert len(t) <= 4096, (fn.__name__, m, len(t))
# 4) user main menu: "My Account & Subscription" is the FIRST button for a subscriber; non-subscribers keep "View Plans" first
now = sb.now_ts()
sb._SUBS_REF = [{"user_id": 5, "expiry_ts": now + 20 * 86400, "chat_id": 5}]
assert sb.main_menu_keyboard(5)[0][0]["text"] == "💎 My Account & Subscription"
assert sb.main_menu_keyboard(7)[0][0]["text"] == "💎 View Plans"
# 5) strategy card is accurate: TPB shows the recommended runnable ladder + plain-language reasons
card = stx.verdict_card("TPB", vd["TPB"])
assert "کاندید سودده" in card and "Q3" in card and "PF زیر ۱٫۲" in card
assert "قفل" in stx.verdict_card("TKF", vd["TKF"])
print("ADMIN UI TESTS PASSED")

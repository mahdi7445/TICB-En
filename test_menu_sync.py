"""Smoke test of the per-role Telegram menu (☰) sync + profile capture in subscription_bot (fake Telegram, no network)."""
import os, sys, tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1,2", "PRIVATE_CHANNEL_ID": "-100", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ.setdefault(k, v)
import subscription_bot as sb

calls, store = [], {}
sb.tg = lambda m, **p: calls.append((m, p)) or {"ok": True}
sb._load = lambda n, d: store.get(n, d)
sb._save = lambda n, v: store.__setitem__(n, v)
now = sb.now_ts()
subs = [{"user_id": 10, "expiry_ts": now + 100000}, {"user_id": 11, "expiry_ts": now - 5}]
n = sb.sync_user_menus(subs)
got = {p["scope"]["chat_id"]: (m, [c["command"] for c in p.get("commands", [])]) for m, p in calls}
assert n == 3 and set(got) == {1, 2, 10}, got                      # 2 admins + 1 live subscriber; expired one untouched
assert got[1][1][0] == "panel" and got[10][1][0] == "start" and "status" in got[10][1]
calls.clear(); assert sb.sync_user_menus(subs) == 0 and not calls   # idempotent: no API calls when nothing changed
subs[0]["expiry_ts"] = now - 1
assert sb.sync_user_menus(subs) == 1 and calls[0][0] == "deleteMyCommands" and calls[0][1]["scope"]["chat_id"] == 10   # expired -> back to default menu
sb._note_user({"id": 77, "first_name": "Ali", "username": "ali", "language_code": "fa"}); sb._flush_profiles()
assert store["user_profiles.json"]["77"]["username"] == "ali"
sb._note_user({"id": 78, "is_bot": True}); assert "78" not in sb._profiles()
for k in ("def", "sub", "adm"):
    for c in sb._commands_for(k):
        assert c["command"].islower() and 1 <= len(c["description"]) <= 256, c
assert [c["command"] for c in sb._commands_for("def")][:2] == ["start", "plans"]
print("MENU SYNC TESTS PASSED")

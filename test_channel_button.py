"""'Open Signal Channel' link + button responsiveness. Run: python3 test_channel_button.py"""
import os, re, sys, types
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1", "PRIVATE_CHANNEL_ID": "-1001234567890", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ[k] = v
for m in ("mplfinance", "websocket"):
    try: __import__(m)
    except Exception: sys.modules.setdefault(m, types.ModuleType(m))
import subscription_bot as sb

# 1) the link carries a message id (https://t.me/c/<id>/<msg>) - the bare form does nothing in many Telegram clients
sb._RELAY_MAP_REF = {"100": 5001, "sid:ALT-000981": 5030, "101": 5020}
sb._CHANNEL_ANCHOR.update({"t": 0.0, "id": 0})
url = sb._channel_open_url()
assert re.fullmatch(r"https://t\.me/c/1234567890/5030", url), url
# cached for 2 minutes (no recomputation on the hot path)
sb._RELAY_MAP_REF["x"] = 9999
assert sb._channel_open_url().endswith("/5030")
# no relay data -> falls back to the pinned message, then to 1
sb._RELAY_MAP_REF = {}; sb._CHANNEL_ANCHOR.update({"t": 0.0, "id": 0})
sb._load = lambda name, default=None: default
calls = []
sb.tg = lambda method, **kw: calls.append(method) or {"result": {"pinned_message": {"message_id": 777}}}
assert sb._channel_open_url().endswith("/777") and calls == ["getChat"]
sb._CHANNEL_ANCHOR.update({"t": 0.0, "id": 0})
sb.tg = lambda method, **kw: (_ for _ in ()).throw(RuntimeError("no network"))
assert sb._channel_open_url().endswith("/1")
# non -100 ids never produce a (useless) link
sb.PRIVATE_CHANNEL_ID = "123"
assert sb._channel_open_url() is None
sb.PRIVATE_CHANNEL_ID = "-1001234567890"

# 2) the screen a subscribed member sees
sb._RELAY_MAP_REF = {"1": 4242}; sb._CHANNEL_ANCHOR.update({"t": 0.0, "id": 0})
sb._live_subscription = lambda uid: {"user_id": uid}
sb._channel_member_status = lambda uid: "member"
sb._revoke_channel_invite = lambda uid: None
text, rows = sb.build_channel_screen(5)
btn = rows[0][0]
assert btn["text"].endswith("Open Signal Channel") and btn["url"] == "https://t.me/c/1234567890/4242", btn
assert rows[-1][0]["callback_data"] == "menu:home"

# 3) responsiveness: after every heavy step a no-wait poll (timeout=0) is made
orig_handle_updates = sb.handle_updates
seen = []
sb.handle_updates = lambda *a, **k: seen.append(k.get("poll_seconds", "long"))
sb.process_pending_payments = lambda *a, **k: None
sb.process_subscription_lifecycle = lambda *a, **k: None
sb.sync_user_menus = lambda *a, **k: None
args = ({}, [{"x": 1}], [], [], {}, [], {}, {}, [], {"at": 0}, set(), {})
sb.run_cycle(*args[:9], {}, *args[10:])
assert seen[0] == "long" and seen.count(0) >= 3, seen          # payments + lifecycle + menu sync each followed by a quick poll
# the long-poll call itself still waits up to LONG_POLL_SECONDS
sb.handle_updates = orig_handle_updates
got = {}
sb.tg = lambda method, **kw: (got.update(kw), {"result": []})[1]
state = {"last_update_id": 0}
sb.handle_updates(state, [], [], [], {}, [], {}, {}, [], set(), {})
assert got["timeout"] == sb.LONG_POLL_SECONDS, got
sb.handle_updates(state, [], [], [], {}, [], {}, {}, [], set(), {}, poll_seconds=0)
assert got["timeout"] == 0, got
print("CHANNEL BUTTON TESTS PASSED")

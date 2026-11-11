"""copyMessage of the altcoin relay must survive transient network errors. Run: python3 test_relay_retry.py"""
import os, sys, tempfile, types
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1", "PRIVATE_CHANNEL_ID": "-100", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ.setdefault(k, v)
for _m in ("mplfinance", "websocket", "matplotlib", "matplotlib.pyplot", "pandas"):
    try: __import__(_m)
    except Exception: sys.modules.setdefault(_m, types.ModuleType(_m))
import requests, subscription_bot as sb
sb.time.sleep = lambda s: None
def run(seq):
    it = iter(seq); calls = []
    def fake(method, **kw):
        calls.append(1); x = next(it)
        if isinstance(x, Exception): raise x
        return x
    sb.tg = fake
    return sb._copy_message_with_retry({"chat_id": 1}, "k"), len(calls)
ok = {"ok": True, "result": {"message_id": 9}}
r, n = run([requests.ConnectionError("Connection aborted", ConnectionResetError(104, "reset")), ok]); assert r["ok"] and n == 2
r, n = run([requests.ConnectionError("x")] * 3 + [ok]); assert r["ok"] and n == 4
r, n = run([requests.ConnectionError("x")] * 9); assert not r["ok"] and n == 5, (r, n)
bad = requests.HTTPError("400", response=types.SimpleNamespace(status_code=400))
r, n = run([bad, ok]); assert not r["ok"] and n == 1, "4xx is final"
r, n = run([{"ok": False, "error_code": 502}, ok]); assert r["ok"] and n == 2
print("RELAY RETRY TESTS PASSED")

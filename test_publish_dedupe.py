"""strategy_modules._publish honours the bot's signal-ledger hooks. Run: python3 test_publish_dedupe.py"""
import os, sys, types
for m in ("mplfinance", "websocket"):
    try: __import__(m)
    except Exception: sys.modules.setdefault(m, types.ModuleType(m))
import strategy_modules as sm, shared_controls as sc
sent, claims, seen = [], [], set()
deps = {"send": lambda cap, rep=None: sent.append(cap) or 100 + len(sent), "alert": lambda *a, **k: None, "write_json": lambda *a, **k: None, "path": lambda n: n,
        "dup_check": lambda sym, tf, side, bar, e: "dup" if (sym, tf, side, bar) in seen else None,
        "dup_claim": lambda sym, tf, side, bar, e, sid, src: (seen.add((sym, tf, side, bar)), claims.append(sid)), "verdicts": lambda: {}, "fmt_for": lambda e, r: (lambda v: str(v)), "lang": "en"}
ctl = {}
cfg = sm.MODULE_CFG[next(iter(sm.MODULE_CFG))]
sig = {"side": "SELL", "sl": 1.41, "bar_ms": 1763395200000, "price": 1.32}
st = {"trades": {}}
try:
    a = sm._publish(deps, st, "TS1", "ATOM/USD", dict(cfg, tf="4h"), sig, 1.32, "ALT-000976", 1763395500000, ctl, sc, [], {})
    b = sm._publish(deps, st, "SWP", "ATOM/USD", dict(cfg, tf="4h"), dict(sig), 1.32, "ALT-000977", 1763395501000, ctl, sc, [], {})
except Exception as e:
    print("harness issue:", repr(e)); raise
assert a is True and b is False and len(sent) == 1 and claims == ["ALT-000976"], (a, b, sent, claims)
# without hooks behaviour is unchanged (bar-level idempotency still blocks)
deps2 = {k: v for k, v in deps.items() if not k.startswith("dup_")}
st2 = {"trades": {}}
assert sm._publish(deps2, st2, "TS1", "ATOM/USD", dict(cfg, tf="4h"), sig, 1.32, "X1", 1763395500000, ctl, sc, [], {}) is True
assert sm._publish(deps2, st2, "SWP", "ATOM/USD", dict(cfg, tf="4h"), dict(sig), 1.32, "X2", 1763395501000, ctl, sc, [], {}) is False
# cross-timeframe: same coin/side, price within 0.6%, opened < 90 min ago => duplicate; far price or old => separate signal
now = 1763395500000
op = [{"symbol": "ZEC/USD", "side": "SELL", "tf": "5m", "entry": 935.79, "status": "open", "opened_ms": now - 5 * 60000, "module": "RD1"}]
assert str(sm._dup_reason(op, "CN15", "ZEC/USD", "15m", "SELL", 936.57, now)).startswith("dup_cross_tf")
assert sm._dup_reason(op, "CN15", "ZEC/USD", "15m", "SELL", 960.0, now) is None
assert sm._dup_reason(op, "CN15", "ZEC/USD", "15m", "BUY", 936.57, now) is None
assert sm._dup_reason(op, "CN15", "ZEC/USD", "15m", "SELL", 936.57, now + 2 * 3600 * 1000) is None
assert str(sm._dup_reason(op, "RD2", "ZEC/USD", "5m", "SELL", 931.79, now)).startswith("dup_same_tf")
op[0]["status"] = "closed"
assert sm._dup_reason(op, "CN15", "ZEC/USD", "15m", "SELL", 936.57, now) is None
# TICB native vs open module trade (cross-timeframe)
import candle_engine as ce, tempfile, json, time as _t
d = tempfile.mkdtemp(); ce.DATA_DIR = d
json.dump({"trades": {"X1": {"symbol": "BTC/USD", "side": "SELL", "tf": "15m", "entry": 100.0, "status": "open", "opened_ms": int(_t.time() * 1000) - 600000, "module": "CN15"}}}, open(d + "/module_trades.json", "w"))
assert ce._module_open_duplicate("BTC/USD", "5m", "SELL", 100.3) is True
assert ce._module_open_duplicate("BTC/USD", "5m", "SELL", 103.0) is False
assert ce._module_open_duplicate("BTC/USD", "15m", "SELL", 150.0) is True       # same timeframe stays blocked regardless of price
assert ce._module_open_duplicate("BTC/USD", "5m", "BUY", 100.3) is False
print("PUBLISH DEDUPE TESTS PASSED")

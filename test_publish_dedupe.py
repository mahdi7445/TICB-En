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
print("PUBLISH DEDUPE TESTS PASSED")

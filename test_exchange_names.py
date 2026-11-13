# -*- coding: utf-8 -*-
import os, sys
os.environ["EXCHANGE_SYMBOLS_LIVE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exchange_symbols as xs, toobit_watch as tw, rnd_research as rr
assert xs.lookup("PEPE") == ("1000PEPE", 1000.0) and xs.lookup("BTC") == ("BTC", 1.0)
assert xs.trade_fields("SHIB") == {"ex_name": "1000SHIB", "px_mult": 1000.0} and xs.trade_fields("ETH") == {}
assert abs(xs.scale(0.0000044646, 1000.0) - 0.0044646) < 1e-12
info = {"contracts": [{"symbol": "1000PEPE-SWAP-USDT"}, {"symbol": "BTC-SWAP-USDT"}, {"symbol": "1000FLOKI-SWAP-USDT"}]}
assert xs.parse_exchange_info(info) == {"PEPE": ("1000PEPE", 1000.0), "FLOKI": ("1000FLOKI", 1000.0)}
# watch gate: PEPE ≡ 1000PEPE
d = {"blocked": {"1000PEPE": {"reason": "delisted"}}, "pause": {}}
assert tw.block_reason(d, "PEPE", 1.0) == "toobit_delisted" and tw.block_reason(d, "BTC", 1.0) == ""
assert tw.block_reason({"pause": {"until": 9e9}}, "BTC", 1.0) == "toobit_pause"
# profitability gate on own ladder
v = {"verdict": "PASS", "stats": {"CLASSIC": {"n": 300, "avg": 0.01, "t": 0.5, "pf": 1.02, "train": .0, "test": .0, "ok": True, "sem": "live4", "hi": -0.02},
                                   "Q2": {"n": 300, "avg": 0.20, "t": 3.1, "pf": 1.4, "train": .1, "test": .2, "ok": True, "sem": "live4", "hi": 0.1}}}
o = rr.own_ladder_gate(v); assert o["net_gate_ok"] and o["best_ladder"] == "Q2"
v["stats"]["Q2"]["hi"] = -0.01; assert rr.own_ladder_gate(v)["net_gate_ok"] is False
print("EXCHANGE NAMES / WATCH / NET GATE TESTS PASSED")

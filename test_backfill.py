"""VIP must count every SGNL close even if its entry was never tracked. Run: python3 test_backfill.py"""
import os, sys, tempfile, types, json
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1", "PRIVATE_CHANNEL_ID": "-100", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ.setdefault(k, v)
for _m in ("mplfinance", "websocket", "matplotlib", "matplotlib.pyplot", "pandas"):
    try: __import__(_m)
    except Exception: sys.modules.setdefault(_m, types.ModuleType(_m))
import subscription_bot as sb, candle_engine as ce
tmp = tempfile.mkdtemp()
ce.DATA_DIR = tmp
ce.TRADE_HISTORY_FILE = os.path.join(tmp, "trade_history.json")
ce._archive_record = lambda r: None
stop_txt = ("❌ STOP HIT — TIA 4H\nStop-loss hit on this LONG trade before Target 1 — full position closed.\n"
            "Entry was 1.2345  ·  Stop 1.1800\n\n📊 Result so far: <b>-1.00R</b> (loss)\n\n2026-11-14 10:00:00 UTC\n#ALT_000901")
ev = sb.parse_forwarded_result_text(stop_txt)
assert ev["kind"] == "stop" and ev["side"] == "BUY" and abs(ev["stop"] - 1.18) < 1e-9 and ev["source_signal_id"], ev
ev["entry"] = ev["entry"]
assert ce._backfill_forwarded_close(ev) is True
h = json.load(open(ce.TRADE_HISTORY_FILE))
assert len(h) == 1 and h[0]["final_r"] == -1.0 and h[0]["symbol"] == "TIA/USD" and h[0]["logical_tf"] == "4h", h
assert ce._backfill_forwarded_close(ev) is False, "must not double count"
assert len(json.load(open(ce.TRADE_HISTORY_FILE))) == 1
# no id / unknown R => no backfill
assert ce._backfill_forwarded_close({**ev, "source_signal_id": None}) is False
assert ce._backfill_forwarded_close({**ev, "source_signal_id": "ALT-000902", "kind": "target_hit"}) is False
# SHORT breakeven
be = sb.parse_forwarded_result_text("⚪ BREAKEVEN — OP 5M\nPrice returned to entry on this SHORT trade — x\nEntry 2.5\n\n📊 Result so far: <b>+0.20R</b> (profit)\n#ALT_000903")
assert be["side"] == "SELL" and ce._backfill_forwarded_close(be) is True
assert json.load(open(ce.TRADE_HISTORY_FILE))[-1]["final_r"] == 0.2
print("BACKFILL TESTS PASSED")

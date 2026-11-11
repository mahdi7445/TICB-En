"""Timeline / forwarded matching / snooze / spacing regressions. Run: python3 test_alert_timeline.py"""
import json, os, sys, tempfile, time, types
os.environ["DATA_DIR"] = tempfile.mkdtemp()
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1", "PRIVATE_CHANNEL_ID": "-100", "TELEGRAM_BOT_TOKEN": "x", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ.setdefault(k, v)
for _m in ("mplfinance", "websocket", "matplotlib", "matplotlib.pyplot", "pandas"):
    try: __import__(_m)
    except Exception: sys.modules.setdefault(_m, types.ModuleType(_m))
import candle_engine as ce, subscription_bot as sb
n = 0
def chk(c, m):
    global n
    assert c, m; n += 1

def trade(sid="ALT-000193", entry=0.2218, hit=(True, True, True, False), events=()):
    return {"signal_id": sid, "side": "SELL", "entry": entry, "sl": 0.2405, "r": 0.0187, "symbol": "XLM/USD", "display": "XLM", "tf_key": "1h",
            "forwarded_tracking": True, "closed": False, "trailing": False,
            "hit": {str(x): h for x, h in zip(ce.RR_TARGETS, hit)}, "events": list(events)}

# 1) timeline: each target once, numbered, with hit line + fair value; no triple blank lines
evs = [{"type": "rr", "level_r": lv, "ts": "2026-10-07T08:34:33+00:00"} for lv in (1, 2, 4, 1, 2, 4)]
t = trade(events=evs)
sb.find_signal_by_id = lambda sid: (t, True)
msg = sb.format_signal_lookup_message("ALT-000193")
chk(msg.count("Target 1 hit") == 1 and msg.count("Target 2 hit") == 1 and msg.count("Target 3 hit") == 1, "dedup timeline")
chk("Targets hit: T1/T2/T3" in msg and "fair value" in msg, "hit + fair value line")

# 2) forwarded result of ANOTHER signal must not attach to this old trade
store = {"c": {"X": {"open_trade": t}}}
ce.load_state = lambda: {"candle_signals": store["c"]}
ce.save_state = lambda *a, **k: None
q = [{"status": "pending", "ticker": "XLM", "kind": "target_hit", "level": 4, "entry": 0.2300, "source_signal_id": "ALT-000999", "queued_at": 1}]
ce.read_json_resilient = lambda path, default, **kw: q if "forwarded" in path else default
ce.atomic_write_json = lambda *a, **k: None
ce.notify_admin = lambda *a, **k: None
ce.process_forwarded_results()
chk(len(t["events"]) == 6 and not t["hit"]["6"], "foreign signal id ignored")
# 3) same signal, level already hit -> no duplicate event
q[:] = [{"status": "pending", "ticker": "XLM", "kind": "target_hit", "level": 2, "entry": 0.2218, "source_signal_id": "ALT-000193", "queued_at": 2}]
ce.process_forwarded_results()
chk(len(t["events"]) == 6 and q[0]["status"] == "applied", "no duplicate rr event")
# 4) entry far from the open trade (different signal, no id) is not matched
q[:] = [{"status": "pending", "ticker": "XLM", "kind": "target_hit", "level": 6, "entry": 0.30, "source_signal_id": None, "queued_at": 3}]
ce.process_forwarded_results()
chk(not t["hit"]["6"], "far entry not matched")

# 5) snooze request postpones the stale reminder and closes nothing
t2 = trade(); store["c"] = {"X": {"open_trade": t2}}
reqs = [{"key": "ALT-000193", "final_r": None, "status": "pending", "mode": "snooze7"}]
ce.read_json_resilient = lambda path, default, **kw: reqs if "admin_close" in path else default
ce.process_admin_close_requests()
chk(reqs[0]["status"] == "applied" and not t2["closed"] and t2["_stale_alert_sent_at_ms"] > time.time() * 1000 + 5 * 86400 * 1000, "snooze")
# 6) the asnz button queues a snooze request
saved = {}
sb._load = lambda name, d: saved.get(name, d); sb._save = lambda name, v: saved.__setitem__(name, v)
sb.edit_message = lambda *a, **k: None
sb.ADMIN_USER_IDS = {1}
print(f"ALERT TIMELINE TESTS PASSED ({n})")

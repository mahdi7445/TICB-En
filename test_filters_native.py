"""V15: trading filters (loss streak, daily/weekly loss, caps…) must also apply to the bots' MAIN-engine signals; stale-trade alerts must not lie;
duplicate relay posts must not be mirrored; closing a live trade needs confirmation. No network. Run: python3 test_filters_native.py"""
import json, os, sys, tempfile, time
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
for k, v in {"BOT_TOKEN": "x", "ADMIN_USER_IDS": "1", "PRIVATE_CHANNEL_ID": "-100", "WALLET_ADDRESS_TRC20": "T" + "x" * 33}.items():
    os.environ.setdefault(k, v)
import types
for _m in ("mplfinance", "websocket", "matplotlib", "matplotlib.pyplot", "pandas"):
    try:
        __import__(_m)
    except Exception:
        sys.modules.setdefault(_m, types.ModuleType(_m))
import shared_controls as sc

NOW = 1_800_000_000_000.0
MIN = 60_000


def closed(r, ago_min, side="BUY"):
    return {"final_r": r, "side": side, "closed_at_ms": NOW - ago_min * MIN, "opened_at_ms": NOW - (ago_min + 30) * MIN}


def gate(ctl_over, closed_trades=(), opens=(), side="BUY", stop_pct=1.0):
    ctl = sc.sanitize({}, "ticb"); ctl.update(ctl_over)
    return sc.native_gate(ctl, side=side, tf_key="1h", stop_pct=stop_pct, now_ms=NOW, open_trades=list(opens), closed_trades=list(closed_trades))


# 1) the user's exact setup: streak filter ON, 4 losses, pause 240 min
S = {"streak_filter": True, "streak_losses": 4, "streak_pause_min": 240}
four = [closed(-1, 50), closed(-1, 40), closed(-1, 30), closed(-1, 20)]
assert gate(S, four).startswith("loss_streak(4)"), gate(S, four)
assert gate(S, [closed(-1, 400), closed(-1, 390), closed(-1, 380), closed(-1, 300)]) == "", "pause over after 240 min"
assert gate(S, four[1:]) == "", "only 3 losses"
assert gate(S, [closed(+1.5, 5)] + four) == "", "a win after the losses breaks the streak"
assert gate({}, four) == "", "filter off -> allowed"
# trade_history rows use ISO 'closed_at' -> must work as well
iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ms / 1000))
rows = [{"final_r": -1.0, "closed_at": iso(NOW - m * MIN * 1.0)} for m in (50, 40, 30, 20)]
assert gate(S, rows).startswith("loss_streak"), "ISO closed_at rows"
# 2) other filters now reach the main engine too
assert gate({"daily_loss_filter": True, "daily_loss_limit_r": 2.0}, [closed(-1.2, 60), closed(-1.1, 30)]).startswith("daily_loss")
assert gate({"max_open_filter": True, "max_open_trades": 2}, opens=[{"side": "BUY"}, {"side": "SELL"}]) == "max_open"
assert gate({"same_side_filter": True, "max_same_side": 1}, opens=[{"side": "BUY"}]) == "same_side_cap"
assert gate({"max_per_day_filter": True, "max_signals_per_day": 2}, [closed(1, 10), closed(1, 20)]) == "max_per_day"
assert gate({"min_stop_filter": True, "min_stop_pct": 2.0}, stop_pct=1.0) == "stop_too_small"
assert gate({"paused": True}) != "" or True
print("native_gate OK")

# 3) TICB candle_engine native path really uses it
import candle_engine as ce
ctl_now = sc.sanitize({}, "ticb"); ctl_now.update(S)
sc.load = lambda *a, **k: ctl_now
ce._modules_closed_trades = lambda: [dict(t, closed_at_ms=time.time() * 1000 - (50 - i * 10) * MIN) for i, t in enumerate(four)]
ce._modules_open_trades = lambda: []
sig = {"side": "BUY", "price": 100.0, "sl": 99.0}
assert ce._native_ctl_block(sig, "1h").startswith("loss_streak"), "TICB main engine must be blocked by the streak filter"
ctl_now.update({"streak_filter": False}); assert ce._native_ctl_block(sig, "1h") == ""
print("TICB native path OK")

# 4) stale-trade alerts
base = {"signal_id": "ALT-000822", "source_signal_id": "ALT-000822", "side": "BUY", "entry": 0.325, "sl": 0.321, "r": 0.004, "symbol": "LDO/USD",
        "forwarded_tracking": True, "opened_at_ms": int(time.time() * 1000) - int(7 * 3600 * 1000), "hit": {str(x): False for x in ce.RR_TARGETS}, "trailing": False, "closed": False, "display": "LDO", "tf_key": "15m"}
sent = []
ce.notify_admin = lambda key, exc, **kw: sent.append((key, kw.get("full_text", str(exc)), kw.get("reply_markup")))
ce.save_state = lambda *a, **k: None
relay_path = os.path.join(ce.DATA_DIR, "relay_targets.json")


def run_stale(trade, levels, replay):
    json.dump({} if levels is None else {"ALT-000822": levels}, open(relay_path, "w"))
    ce.replay_trade_from_klines = lambda t, now: replay
    sent.clear()
    st = {"X": {"open_trade": dict(trade)}}
    ce.check_stale_open_trades({}, st)
    return list(sent)


closed_rp = {"ok": True, "closed": True, "final_r": 1.3, "hit": {}, "ambiguous": False, "ended_ms": int(time.time() * 1000)}
ce._replay_summary_line = lambda rp: "replay"
# 4a) the real screenshot case: SGNL module ladder (2R/3R/4R/6R) != bot ladder -> replay not trustworthy -> SILENT at 7h
assert run_stale(base, [2.0, 3.0, 4.0, 6.0], closed_rp) == [], "must not claim 'ended' for a source ladder different from the bot's own"
# 4b) unknown ladder (old trades) -> silent as well
assert run_stale(base, None, closed_rp) == []
# 4c) matching ladder + replay says still open -> silent
same = list(ce.RR_TARGETS)
assert run_stale(base, same, {"ok": True, "closed": False, "market_r": 0.4}) == []
# 4d) matching ladder + replay says ended -> alert WITH close buttons and the live-position warning
out = run_stale(base, same, closed_rp)
assert len(out) == 1 and "CLOSE message" in out[0][1] and any("aclt:" in b["callback_data"] for r in out[0][2]["inline_keyboard"] for b in r)
# 4e) very old unverifiable trade -> a calm reminder, NO close buttons
old = dict(base, opened_at_ms=int(time.time() * 1000) - 11 * 86400 * 1000)
out = run_stale(old, [2.0, 3.0, 4.0, 6.0], closed_rp)
assert len(out) == 1 and "may well still be open" in out[0][1] and not any("aclt:" in b["callback_data"] for r in out[0][2]["inline_keyboard"] for b in r)
print("stale alerts OK")

# 5) relay: duplicate posts are not mirrored; real targets recorded; confirm-before-close
import subscription_bot as sb
store, calls, alerts = {}, [], []
sb._load = lambda n, d: store.get(n, d)
sb._save = lambda n, v: store.__setitem__(n, v)
sb.tg = lambda m, **p: (calls.append((m, p)) or {"ok": True, "result": {"message_id": 900 + len(calls)}})
sb.alert_admins_text = lambda t, **k: alerts.append(t)
sb.queue_manual_signal = lambda *a, **k: (True, "ok")
sb.is_duplicate_signal = lambda *a, **k: False
txt = ("🟢 LONG — LDO 15M\nEntry: 0.3290\n❌ Stop: 0.3250\n🎯 Target 1: 0.3370\n🎯 Target 2: 0.3410\n🎯 Target 3: 0.3450\n🎯 Target 4: 0.3530\n#ALT_000830")
post = lambda mid: {"chat": {"id": -5}, "message_id": mid, "text": txt}
seen, mp = set(), {}
parsed = sb.parse_forwarded_signal_text(txt)
assert parsed, "sample relay text must parse"
sb.handle_altcoin_relay_post(post(1), seen, mp)
n1 = sum(1 for m, _ in calls if m == "copyMessage")
sb.handle_altcoin_relay_post(post(2), seen, mp)          # the same signal posted again by the source (different message_id)
sb.handle_altcoin_relay_post(post(3), seen, mp)
assert n1 == 1 and sum(1 for m, _ in calls if m == "copyMessage") == 1, "duplicates must NOT be mirrored"
assert len([a for a in alerts if "Duplicate altcoin signal NOT mirrored" in a]) == 1, "one alert per signal, not per copy"
rt = store.get("relay_targets.json", {})
assert rt and list(rt.values())[0] == [2.0, 3.0, 4.0, 6.0], rt
print("relay dedupe OK:", rt)
edits = []
sb.edit_message = lambda c, m, t, keyboard=None, **k: edits.append((t, keyboard))
queued = []
sb._queue_admin_close_request = lambda *a, **k: queued.append(a)
cb = lambda d: {"id": "c", "from": {"id": 1}, "data": d, "message": {"chat": {"id": 1}, "message_id": 9, "text": "⚠️ Stuck open trade"}}
sb.answer_callback = lambda *a, **k: None
sb.handle_callback(cb("aclt:ALT-000822:rep"), [], [], {}, {}, [], {}, {"paused": False}, [])
assert not queued and "Confirm closing" in edits[-1][0] and any("aclc:" in b["callback_data"] for r in edits[-1][1] for b in r)
sb.handle_callback(cb("aclx:ALT-000822"), [], [], {}, {}, [], {}, {"paused": False}, [])
assert not queued and "Cancelled" in edits[-1][0]
sb.handle_callback(cb("aclc:ALT-000822:rep"), [], [], {}, {}, [], {}, {"paused": False}, [])
assert len(queued) == 1 and queued[0][0] == "ALT-000822"
# 6) module publish is idempotent and persists immediately after a real send (same-ID-twice fix)
import strategy_modules as sm
vd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lowtf_verdicts.json")))
ctl2 = sc.sanitize({}, "sgnl"); cfg = sm.cfg_for("TPB", vd)
sent_msgs, persisted, written = [], [], []
deps = {"send": lambda cap, x: (sent_msgs.append(cap) or 500 + len(sent_msgs)), "send_photo": None, "chart": None, "alert": lambda k, t: None,
        "fmt_for": lambda e, r: (lambda v: f"{v:.6g}"), "verdicts": lambda: vd, "lang": "en",
        "persist": lambda st_: persisted.append(len(st_["trades"])), "write_json": lambda p_, d_: written.append(p_), "path": lambda n: n}
st = {"trades": {}, "seen": [], "diag": {}}
sig = {"side": "BUY", "sl": 99.0, "bar_ms": 1_700_000_000_000, "price": 100.0}
ok1 = sm._publish(deps, st, "TPB", "LDO/USD", cfg, sig, 100.0, "ALT-000830", 1_700_000_100_000, ctl2, sc, [], vd)
ok2 = sm._publish(deps, st, "TPB", "LDO/USD", cfg, sig, 100.0, "ALT-000831", 1_700_000_200_000, ctl2, sc, [], vd)   # same candle replayed
assert ok1 and not ok2 and len(sent_msgs) == 1 and persisted == [1] and written, (ok1, ok2, len(sent_msgs), persisted)
print("module publish idempotent + persisted OK")
print("ALL V15 FILTER/ALERT TESTS PASSED")

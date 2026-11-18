"""AI pool: credit exhaustion -> cooldown + failover; integrity audit. Run: python3 test_rnd_ai_pool.py"""
import os, json, time
import rnd_ai as ai, rnd_lab as rl
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
    os.environ[k] = "k"
GOOD = json.dumps({"analysis": ["x"], "strategies": [{"name": "t1", "tfs": ["15m"], "all": [["close", ">", "ema:20"], ["rsi:14", "<", 70]],
                   "stop": {"kind": "atr", "k": 1.5}, "cooldown": 6, "min_risk_pct": 0.2, "max_stop_atr": 4.0, "why": "w"}], "tuning": []})
AUD = json.dumps({"issues": ["a"], "actions": ["b"]})
calls = []
def fake_post(d, pid, key, model, system, user, mt, to):
    calls.append(pid)
    if pid == "claude":
        return None, "HTTP 400 — credit balance is too low", 400, '{"error":{"message":"Your credit balance is too low to access the API"}}'
    return (AUD if "chief auditor" in system else GOOD), "", 200, ""
ai._post = fake_post
assert ai.classify_error(400, "credit balance is too low") == "credit"
assert ai.classify_error(429, "rate limit per minute") == "rate"
assert ai.classify_error(401, "") == "auth"
ai.load_health({})
bud = {p: 5 for p in ai.PROVIDERS}
r = ai.propose_multi({}, {}, 6, "", bud, ["claude", "openai", "groq"], None, None, 0, [])
assert r["used"] and r["providers"]["claude"]["kind"] == "credit", r["providers"]
assert r["providers"]["claude"].get("covered_by") in ("openai", "groq"), r["providers"]
assert ai.cooldown_left("claude") > 3000 and r["audit"].get("by"), r["audit"]
n_before = calls.count("claude")
h = ai.export_health(); ai.load_health(h)
r2 = ai.propose_multi({}, {}, 6, "", bud, ["claude", "openai", "groq"], None, None, 0, [])
assert calls.count("claude") == n_before and r2["providers"]["claude"]["skipped"] == "cooldown"
# all down -> wait message
ai.load_health({}); ai._post = lambda *a, **k: (None, "HTTP 402", 402, "insufficient credit")
r3 = ai.propose_multi({}, {}, 6, "", bud, ["claude"], None, None, 0, [])
assert not r3["used"] and "claude" in r3["providers"]
r4 = ai.propose_multi({}, {}, 6, "", bud, ["claude"], None, None, 0, [])
assert r4["wait_s"] > 0 and "منتظر" in r4["error"], r4
# integrity
bad = [{"signal_id": "A", "final_r": 1.0, "close_type": "stop", "targets_hit": [], "tf": "manual", "opened_at": "2026-01-02", "closed_at": "2026-01-01"},
       {"signal_id": "A", "final_r": -1.0, "close_type": "sl", "targets_hit": ["T1"], "tf": "5m"}]
kinds = {f["kind"] for f in rl.integrity_audit(bad)}
assert {"dup_id", "stop_profit", "target_loss", "time_order", "no_tf"} <= kinds, kinds
assert rl.integrity_audit([]) == []
print("AI POOL TESTS PASSED")

"""Library + panel pages + performance guard. Run: python3 test_rnd_library.py"""
import os, sys, tempfile, time, json
import rnd_lab as rl, rnd_library as lib_, rnd_panel as rp
import shared_controls as sc
tmp = tempfile.mkdtemp()
ctl = os.path.join(tmp, "bot_controls.json")
sent = []
ctx = {"edit": lambda c, m, t, r=None: sent.append((t, r)), "send": lambda *a, **k: None, "controls_path": ctl, "load_history": lambda: [], "data_dir": tmp}
H = lambda spec: spec["name"]
spec = lambda n, o="ai:gemini": {"name": n, "family": "breakout", "origin": o, "tfs": ["15m"], "all": [["close", ">", "ema:20"]], "stop": {"kind": "atr", "k": 1.5}, "cooldown": 6, "why": "x"}
lib = lib_.load(tmp)
scored = [{"spec": spec(f"S{i}", "search" if i % 2 else "ai:gemini"), "tf": "15m", "n": 90, "avg": 0.1, "t": 3.0 - i * .1} for i in range(30)]
assert lib_.record_screened(lib, scored, H, 1, ["S0"]) == 30
assert lib_.record_screened(lib, scored, H, 2, []) == 0 and lib["items"]["S0"]["seen"] == 2
slots = {"RD1": {"status": "PASS", "spec_id": "S0", "avg_r": .2, "pf": 1.5, "win": 50, "t": 3, "n": 200, "train_r": .2, "test_r": .2, "tf": "15m", "best_ladder": "H1", "gate": {"passed": 10, "total": 10, "checks": []}},
         "RD2": {"status": "FAIL", "spec_id": "S1", "avg_r": -.1, "pf": .9, "win": 30, "t": -1, "n": 100, "train_r": .1, "test_r": -.3, "tf": "15m", "gate": {"passed": 3, "total": 10, "checks": [{"n": "Win rate %", "ok": False}]}}}
sstrat = {"RD1": {"spec": spec("S0")}, "RD2": {"spec": spec("S1", "search")}}
lib_.record_lab(lib, slots, sstrat, 2); lib_.record_lab(lib, slots, sstrat, 2)          # idempotent
assert len(lib["items"]["S0"]["tests"]) == 1
slots["RD1"]["avg_r"] = .25; lib_.record_lab(lib, slots, sstrat, 3)
assert len(lib["items"]["S0"]["tests"]) == 2
lib_.save(lib, tmp)
assert lib_.evaluate(lib["items"]["S0"])["grade"] == "A" and lib_.evaluate(lib["items"]["S1"])["grade"] == "D"
assert lib_.evaluate(lib["items"]["S5"])["grade"] == "—"
c = lib_.counts(lib); assert c["all"] == 30 and c["pass"] == 1 and c["fail"] == 1 and c["scr"] == 28, c
ins = lib_.insights(lib); assert ins["by_origin"]["ai"]["pass"] == 1
# prune keeps PASS
lib_.MAX_ENTRIES = 5; lib_.prune(lib); assert "S0" in lib["items"] and len(lib["items"]) <= 6; lib_.MAX_ENTRIES = 20000
# panel pages
for t, r in (rp.library_page(ctx, "all", 0), rp.library_page(ctx, "pass", 0), rp.library_page(ctx, "scr", 99), rp.lib_detail(ctx, "S0"), rp.lib_detail(ctx, "S1"), rp.lib_detail(ctx, "S9"), rp.insights_page(ctx), rp.strategies_page(ctx)):
    assert len(t) < 4096, len(t)
    for row in r:
        for b in row:
            assert len(b["callback_data"].encode()) <= 64, b
# changes / timeline / guard with sample state
st = rl.load_state(tmp)
now = time.time()
for i in range(12):
    st["applied"].append({"id": f"p{i}", "t": now - i * 3600, "by": "auto", "kind": "ctl", "desc": f"🧠 AI idea: [gemini] raise min stop {i}", "section": "ticb", "key": "mod_MRA", "old": True, "new": False,
                          "state": "active" if i < 6 else "undone", "base": {"n": 30, "avg": .1, "pf": 1.2}, "rb_n": 20, "src": "ai"})
    rl.log(st, "apply", f"✅ 🧠 AI idea: [gemini] raise min stop {i}", now - i * 3600)
rl.save_state(st, tmp)
for t, r in (rp.changes_page(ctx, 0), rp.changes_page(ctx, 1), rp.change_detail(ctx, "p0"), rp.change_detail(ctx, "zz"), rp.timeline_page(ctx, "all", 0), rp.timeline_page(ctx, "ai", 0), rp.guard_page(ctx)):
    assert len(t) < 4096, len(t)
    for row in r:
        for b in row:
            assert len(b["callback_data"].encode()) <= 64, b
# callbacks route
for d in ("adm:rnd:lb:all:0", "adm:rnd:ld:S0:all:0", "adm:rnd:ins", "adm:rnd:ch:0", "adm:rnd:cd:p0", "adm:rnd:tl:ai:0", "adm:rnd:gd"):
    sent.clear(); rp.handle_callback(d.split(":"), 1, 1, 1, ctx); assert sent and "error" not in sent[-1][0].lower(), (d, sent[-1][0][:100])
# performance guard: strong drop after changes -> auto change undone, once
import random
random.seed(1)
recs = [{"ts": now - 400 * 3600 + i * 3600, "r": (0.35 if random.random() < .55 else -1.0) if i < 90 else (-1.0 if random.random() < .8 else 0.5), "bot": "ticb", "s": "native-candle", "tf": "15m"} for i in range(140)]
st = rl.load_state(tmp); cfg = rl.settings_of(st)
for e in st["applied"]:
    if e["state"] == "active":
        e["t"] = recs[-30]["ts"]
env = {"data_dir": tmp, "controls_path": ctl, "load_history": lambda: []}
msgs = rl.performance_guard(st, recs, env, cfg, now)
assert msgs and any("rolled back" in m for m in msgs), msgs
assert sum(1 for e in st["applied"] if e.get("undo_reason", "").startswith("performance guard")) == 1
assert not any("rolled back" in m for m in rl.performance_guard(st, recs, env, cfg, now + 60))       # gap: no second undo within 6h
good = [{"ts": now - 400 * 3600 + i * 3600, "r": 0.3 if i % 2 else -0.5, "bot": "ticb", "s": "x", "tf": "5m"} for i in range(140)]
st2 = rl.load_state(tmp); assert not rl.performance_guard(st2, good, env, cfg, now)
print("RND LIBRARY TESTS PASSED")

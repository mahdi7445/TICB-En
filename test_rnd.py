"""R&D Lab tests: DSL causality/mirror, slots wiring, diagnosis -> proposals, guarded apply/undo/rollback, rules gate, verdict overlay, panel, tick."""
import json, os, random, sys, tempfile, time
from datetime import datetime, timezone, timedelta
tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import rnd_dsl, rnd_lab as rl, shared_controls as sc, lowtf_strategies as ls, rnd_panel
import strategy_lab as sl

# ---- 1) DSL
spec = {"name": "t", "tfs": ["15m"], "all": [["close", ">", "ema:50"], ["rsi:14", "<", 45], ["volr:20", ">", 0.5]], "stop": {"kind": "atr", "k": 1.5}, "cooldown": 3}
ok, why, clean = rnd_dsl.validate_spec(spec); assert ok, why
for bad in ({"all": []}, {"all": [["bogus", ">", 1]]}, {"all": [["close", "~", 1]]}, {"all": [[1, ">", 2]]}, {"all": [["rsi:14", ">", 1]], "stop": {"kind": "x"}}, "x"):
    assert not rnd_dsl.validate_spec(bad)[0], bad
assert all(s in ls.GENS for s in rnd_dsl.SLOTS) and all(s in sc.MODULES for s in rnd_dsl.SLOTS)
assert not any(sc.defaults()[f"mod_{s}"] for s in rnd_dsl.SLOTS), "RD slots must default OFF"
d = sl.synth(6000, 900_000, seed=5, daily_drift=0.15, mom=0.05)
rnd_dsl.set_override("RD1", clean)
idx, side, nsl, _ = ls.run("RD1", d, 900_000)
assert len(idx) > 20 and set(side.tolist()) == {1, -1}, "both sides expected"
b = np.flatnonzero(side == 1); s_ = np.flatnonzero(side == -1)
assert (nsl[b] < d["c"][idx[b]]).all() and (nsl[s_] > d["c"][idx[s_]]).all(), "stops on the right side"
cut = 4000                                  # causality: signals up to bar `cut` do not change when future bars are removed
d2 = {k: v[:cut + 1] for k, v in d.items()}
i2, s2, _, _ = ls.run("RD1", d2, 900_000)
pre = [(int(i), int(s)) for i, s in zip(idx, side) if i < cut]
assert [(int(i), int(s)) for i, s in zip(i2, s2) if i < cut] == pre, "look-ahead detected"
rnd_dsl.set_override("RD1", None)
assert ls.run("RD1", d, 900_000)[0].size == 0, "empty slot = no signals"
print("dsl ok", len(idx), "signals")

# ---- 2) diagnosis
rng = random.Random(1)
t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
hist = []
def add(i, strat, tf, side, r, hour, sp=1.0, bot="TC"):
    o = t0 + timedelta(hours=i * 3 + 0, minutes=0); o = o.replace(hour=hour) if False else o
    o = t0 + timedelta(days=i // 6, hours=hour)
    hist.append({"signal_id": f"{bot}-{i:06d}", "module": strat if strat != "native-candle" else None, "strategy": None, "tf": tf, "side": side,
                 "entry": 100.0, "sl": 100 - sp, "r_pct": sp, "final_r": r, "opened_at": o.isoformat(), "closed_at": (o + timedelta(hours=2)).isoformat()})
for i in range(260):                          # native 1h: BUY bad, SELL fine; tiny stops bad
    side = "BUY" if i % 2 == 0 else "SELL"
    tiny = i % 3 == 0
    add(i, "native-candle", "1h", side, (rng.gauss(-0.45, 0.8) if side == "BUY" else rng.gauss(0.15, 0.8)) - (0.45 if tiny else 0.0), (i * 5) % 24, sp=0.5 if tiny else 1.6)
for i in range(260, 520):                     # a healthy module
    add(i, "TPB", "1h", "BUY" if i % 2 else "SELL", rng.gauss(0.2, 0.9), (i * 7) % 24, sp=1.5)
for i in range(520, 760):                     # a clearly losing module
    add(i, "MRA", "15m", "BUY" if i % 2 else "SELL", rng.gauss(-0.35, 0.7), (i * 7) % 24, sp=1.5)
cfg = rl.settings_of({})
ctl = {b: sc.load(b, os.path.join(tmp, "bot_controls.json")) for b in rl.BOTS}
ctl["ticb"]["mod_MRA"] = True; ctl["ticb"]["mod_TPB"] = True
import perf_report as pr
diag = rl.diagnose(hist, cfg, ctl, {}, {})
kinds = {(p["kind"], p["key"], json.dumps(p.get("rule"), sort_keys=True)) for p in diag["proposals"]}
assert ("ctl", "mod_MRA", "null") in kinds, kinds
assert not any(p["key"] == "mod_TPB" for p in diag["proposals"]), "healthy strategy must not be switched off"
assert any(p["kind"] == "rule" and p["rule"]["side"] == "BUY" and p["rule"]["scope"] == "native" for p in diag["proposals"]), "BUY leak on native expected"
assert any(p["key"] == "nat_min_stop_pct" for p in diag["proposals"]), "tiny-stop leak expected"
# too little data => no conclusions
assert rl.diagnose(hist[:30], cfg, ctl, {}, {})["proposals"] == []
print("diagnosis ok:", len(diag["proposals"]), "proposals,", len(diag["findings"]), "findings")

# ---- 3) apply / undo / rules gate / rollback
env = {"data_dir": tmp, "controls_path": os.path.join(tmp, "bot_controls.json"), "load_history": lambda: hist}
st = rl.load_state(tmp); rl.merge_proposals(st, diag["proposals"])
recs = rl.enrich(hist)
pm = next(p for p in st["proposals"] if p["key"] == "mod_MRA")
assert rl.apply_proposal(st, pm["id"], env, "admin", recs)[0]
assert sc.load("ticb", env["controls_path"])["mod_MRA"] is False
assert rl.undo_applied(st, pm["id"], env)[0] and sc.load("ticb", env["controls_path"])["mod_MRA"] is True
assert not any(p["key"] == "mod_MRA" for p in rl.diagnose(hist, cfg, ctl, {}, st["protected"])["proposals"]), "undone key is protected for 7 days"
pr_ = next(p for p in st["proposals"] if p["kind"] == "rule")
assert rl.apply_proposal(st, pr_["id"], env, "admin", recs)[0]
sc._rnd_rules_cache["t"] = 0
r = pr_["rule"]; now_ms = datetime(2026, 11, 2, 3, tzinfo=timezone.utc).timestamp() * 1000
assert sc.rnd_rule_block("native", r["tf"], r["side"], now_ms).startswith("rnd_rule:") and sc.rnd_rule_block("native", "4h", "SELL", now_ms) == ""
assert sc.rnd_rule_block("TPB", "1h", "BUY", now_ms) == ""
# gate integration: native_gate blocks the ruled side
why = sc.native_gate(ctl["ticb"], side=r["side"], tf_key=r["tf"], stop_pct=1.0, now_ms=now_ms, open_trades=[], closed_trades=[])
assert why.startswith("rnd_rule") or r["hours"], why
# rollback: new trades after the change are much worse than the baseline -> undone automatically
st2 = rl.load_state(tmp); e = [x for x in st["applied"] if x["id"] == pr_["id"]][0]
st["applied"] = [e]; e["base"] = {"n": 100, "avg": 0.2, "pf": 1.4}
bad = []
for i in range(40):
    _x = -0.6 + rng.gauss(0, 0.3); bad.append({"r": _x, "g": _x, "s": "native-candle", "tf": r["tf"], "side": "SELL", "hr": 3, "sp": 1.0, "bot": "ticb", "ladder": "CLASSIC", "sid": f"x{i}", "ts": time.time() + 10 + i, "ot": 0, "mfe": None})
msgs = rl.evaluate_applied(st, bad, env, rl.settings_of({}))
assert msgs and e["state"] == "undone", msgs
sc._rnd_rules_cache["t"] = 0
assert rl._active_rules(tmp)["rules"] == []
print("apply/undo/rollback/gate ok")

# ---- 4) verdict overlay only valid for the same spec_id
rl.write_json(rl.STRATS_NAME, {"slots": {"RD1": {"spec": clean, "spec_id": "aaa"}}}, tmp)
rl.write_json(rl.VERDICTS_NAME, {"RD1": {"verdict": "PASS", "spec_id": "aaa", "tf": "15m"}, "RD2": {"verdict": "PASS", "spec_id": "zzz"}}, tmp)
sc.RND_VERDICT_FILE = os.path.join(tmp, rl.VERDICTS_NAME); rnd_dsl.STRATS_FILE = os.path.join(tmp, rl.STRATS_NAME)
v = sc._overlay_rnd({})
assert v.get("RD1", {}).get("verdict") == "PASS" and "RD2" not in v, v
print("overlay ok")

# ---- 5) panel + tick
sent = []
ctx = {"edit": lambda c, m, t, r=None: sent.append((t, r)), "send": lambda *a, **k: None, "controls_path": env["controls_path"], "load_history": env["load_history"], "data_dir": tmp}
for cb in ["home", "mode", "m:auto", "pl:0", "fd", "sc", "sl", "sd:RD1", "ch", "jr", "set", "sg:ev", "sg:tg", "sg:ac", "sg:rs", "ai", "tg:ai_on", "cy:mode", "sn:min_n:1", "am:claude-haiku-5-5", "aa", "res", "run"]:
    sent.clear()
    assert rnd_panel.handle_callback(["adm", "rnd"] + cb.split(":"), 1, 1, 1, ctx), cb
    t, rows = sent[-1]
    assert len(t) <= 4096 and "error" not in t.lower().split("\n")[0], (cb, t[:200])
    for row in (rows or []):
        for b in row:
            assert len(b["callback_data"].encode()) <= 64, b
st = rl.load_state(tmp); assert rl.settings_of(st)["mode"] in rl.SCHEMA["mode"][3]
notes = []
out = rl.tick(dict(env, notify=lambda t, r=None: notes.append(t)), force=True)
assert out["ran"], out
print("panel/tick ok; notify:", len(notes))
import admin_panel as ap
labels = [b["text"] for r in ap.home({})[1] for b in r]
assert any("R&D" in l for l in labels)
print("ALL R&D TESTS PASSED")

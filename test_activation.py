"""V13: activation rules on the REAL 2026-11-07 report (data/lowtf_verdicts.json) + deployable-only verdict selection in the lab."""
import json, os, tempfile
import numpy as np
import ladder_catalog as lc, shared_controls as sc, strategy_modules as sm, strategy_lab as sl

vd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lowtf_verdicts.json")))

# 1) strategies that show significant, stable profit on their best RUNNABLE ladder are active with that ladder; everything else stays locked
EXPECT = {"TPB": ("1h", "Q3"), "TPS": ("1h", "Q3"), "MRA": ("1h", "Q2"), "MRB": ("1h", "Q3")}
for m, (tf, lad) in EXPECT.items():
    assert sc.lowtf_status(m, vd) == "CAND", m
    cfg = sm.cfg_for(m, vd); assert cfg and cfg["tf"] == tf, m
    assert lc.best_ladder(m, vd) == lad, (m, lc.best_ladder(m, vd))
for m in sc.LOWTF:
    if m not in EXPECT:
        assert sm.cfg_for(m, vd) is None, f"{m} must stay locked"
# TKF/SMM looked profitable on stale single-target (B*) numbers only; with a runnable ladder they are not significant -> locked
assert sc.is_candidate(dict(vd["TKF"])) is False and sc.is_candidate(dict(vd["SMM"])) is False and sc.is_candidate(dict(vd["SMS"])) is False
# 2) defaults + migration: every lowtf strategy is ON by default (the verdict decides lock/unlock); V13->V14 switches them on once; later admin choices stay
c = sc.sanitize({}, "ticb"); assert all(c["mod_" + m] for m in sc.LOWTF) and c["prof_TPB"] == "auto"
o = sc.sanitize({"_profile": "v13-2026-11-07", "mod_SMS": False}, "sgnl"); assert o["mod_SMS"]
k = sc.sanitize({"_profile": sc.PROFILE_VER, "mod_SMS": False}, "sgnl"); assert k["mod_SMS"] is False
# 3) swing ladders: exact lab/live agreement (like Q*/H*/Z*) and gating (only swing/zone gens get them in the lab)
assert all(len(lc.LADDERS[y][0]) == 4 and abs(sum(lc.LADDERS[y][1]) - 0.9) < 1e-9 for y in lc.SWING_LADDERS)
assert set(lc.lab_exits("TPB", ["LAD"])) >= set(lc.SWING_LADDERS) and not set(lc.lab_exits("DKS", ["LAD", "Y1", "Z1"])) & set(lc.SWING_LADDERS + lc.ZONE_LADDERS)
# 4) lab verdict: single-target ladders (B*) are now measured EXACTLY like the live tracker, so they may win; BT (strategy-specific target) never does
rng = np.random.default_rng(1)
def trades(mu, n=400):
    t0 = 1_700_000_000_000
    return [(t0 + i * 3_600_000 * 7, float(rng.normal(mu, 1.0)), 1.0, f"S{i % 6}") for i in range(n)]
res = {tf: {} for tf in sl.TFS}
for ex, mu in (("B4", 0.9), ("BT", 1.5), ("Q3", 0.02)):
    res["1h"][f"TKF|none|nat|{ex}"] = trades(mu)
out = os.path.join(tempfile.mkdtemp(), "v.json")
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()):
    sl.lowtf_report(res, 0.10, ["1h"], out)
v = json.load(open(out))["TKF"]
assert v["filter"] == "none" and "BT" not in json.dumps(v.get("best_ladder")), v
assert v["stats"]["B4"]["sem"] == "live4" and (v["best_ladder"] in (None, "B4")), v
assert sl._deployable_exit("Q3") and sl._deployable_exit("LAD") and sl._deployable_exit("B4") and not sl._deployable_exit("BT")
# 5) stale (pre-V13) single-target stats are ignored; new ones (sem=live4) are honoured
st_old = {"B4": dict(avg=0.5, t=3, win=30, pf=1.5, ok=False, train=.4, test=.6, n=300), "Q3": dict(avg=0.1, t=2.5, win=40, pf=1.2, ok=False, train=.1, test=.1, n=300)}
assert lc.rank_ladders(st_old)[0][0] == "Q3"
st_new = {k: dict(v, sem="live4") for k, v in st_old.items()}
assert lc.rank_ladders(st_new)[0][0] == "B4"
# 6) every activated strategy's ladder builds a valid 4-target live trade (BUY and SELL) that walks to a finite result
for m, (tf, lad) in EXPECT.items():
    for side, sgn in (("BUY", 1), ("SELL", -1)):
        sig = {"side": side, "sl": 100.0 - sgn * 2.0, "bar_ms": 0}
        t = sm.build_trade(m, "SOL/USD", sig, 100.0, tf, "T1", 0, lad, cfg=sm.cfg_for(m, vd))
        assert len(t["targets"]) == 4, (m, t["targets"])
        assert (t["targets"][0] - 100.0) * sgn > 0 and (t["sl"] - 100.0) * sgn < 0, (m, side)
        for i in range(60):
            px = 100.0 + sgn * (0.5 * i)
            sm.advance(t, [(1000 + i, px + 0.3, px - 0.3)])
            if t["status"] == "closed":
                break
        r = sm.final_r(t) if t["status"] == "closed" else sm.final_r(t, 130.0 if sgn > 0 else 70.0)
        assert r == r and -1.01 <= r <= 12, (m, side, r)
print("ACTIVATION TESTS PASSED")

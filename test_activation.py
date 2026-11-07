"""V13: activation rules on the REAL 2026-11-07 report (data/lowtf_verdicts.json) + deployable-only verdict selection in the lab."""
import json, os, tempfile
import numpy as np
import ladder_catalog as lc, shared_controls as sc, strategy_modules as sm, strategy_lab as sl

vd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lowtf_verdicts.json")))

# 1) the two statistically significant strategies are active with their best runnable ladder; the rest stay locked
for m in ("TPB", "TPS"):
    assert sc.lowtf_status(m, vd) == "CAND", m
    cfg = sm.cfg_for(m, vd); assert cfg and cfg["tf"] == "1h", m
    assert lc.best_ladder(m, vd) == "Q3" and len(lc.LADDERS[lc.best_ladder(m, vd)][0]) >= 2
for m in ("TKF", "TKS", "MRA", "MRB", "SMS", "BZ1H", "BZ4H", "BZ5M", "DKS", "TKM"):
    assert sm.cfg_for(m, vd) is None, f"{m} must stay locked"
# TKF looked like a candidate on its B4 headline (+0.458R) but only +0.08R with a runnable ladder -> not a candidate any more
assert sc.is_candidate(dict(vd["TKF"])) is False
# 2) defaults + migration: V12 -> V13 switches TPB/TPS on and TKF/TKS off once; later admin choices are kept
c = sc.sanitize({}, "ticb"); assert c["mod_TPB"] and c["mod_TPS"] and not c["mod_TKF"] and not c["mod_TKS"] and c["prof_TPB"] == "auto"
o = sc.sanitize({"_profile": "v12-2026-11-06", "mod_TPB": False, "mod_TKF": True}, "sgnl"); assert o["mod_TPB"] and not o["mod_TKF"]
k = sc.sanitize({"_profile": sc.PROFILE_VER, "mod_TPB": False}, "sgnl"); assert k["mod_TPB"] is False
# 3) swing ladders: exact lab/live agreement (like Q*/H*/Z*) and gating (only swing/zone gens get them in the lab)
assert all(len(lc.LADDERS[y][0]) == 4 and abs(sum(lc.LADDERS[y][1]) - 0.9) < 1e-9 for y in lc.SWING_LADDERS)
assert set(lc.lab_exits("TPB", ["LAD"])) >= set(lc.SWING_LADDERS) and not set(lc.lab_exits("DKS", ["LAD", "Y1", "Z1"])) & set(lc.SWING_LADDERS + lc.ZONE_LADDERS)
# 4) the lab verdict must be chosen among RUNNABLE exits only: a great-looking B4 / BT variant may not become the headline
rng = np.random.default_rng(1)
def trades(mu, n=400):
    t0 = 1_700_000_000_000
    return [(t0 + i * 3_600_000 * 7, float(rng.normal(mu, 1.0)), 1.0, f"S{i % 6}") for i in range(n)]
res = {tf: {} for tf in sl.TFS}
for ex, mu in (("B4", 1.2), ("BT", 1.5), ("Q3", 0.02)):
    res["1h"][f"TKF|none|nat|{ex}"] = trades(mu)
out = os.path.join(tempfile.mkdtemp(), "v.json")
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()):
    sl.lowtf_report(res, 0.10, ["1h"], out)
v = json.load(open(out))["TKF"]
assert v["verdict"] != "PASS" and (v.get("best_ladder") in (None, "Q3")), v
assert sl._deployable_exit("Q3") and sl._deployable_exit("LAD") and sl._deployable_exit("Y2") and not sl._deployable_exit("B4") and not sl._deployable_exit("BT")
print("ACTIVATION TESTS PASSED")

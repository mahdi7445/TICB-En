"""The compact merge (strategy_lab.merge_pickles) must give exactly the same verdicts as the old list-of-tuples merge, with ~8x less RAM."""
import json, pickle, tempfile, os
import numpy as np
import strategy_lab as sl

sl.DESIGN = True
tmp = tempfile.mkdtemp(); paths = []
for si in range(3):
    d = sl.synth(20000, 300_000, seed=50 + si, daily_drift=0.1, mom=0.08, vol=0.002)
    d["v"] = np.random.default_rng(si).lognormal(4.6, 0.6, len(d["c"]))
    res = {t: {} for t in sl.TFS}
    for v, tr in sl.run_symbol(d, sl.Ctx(sl.synth(3000, 900_000, seed=1)), 300_000, None, gens={"RSM", "BZ5M", "CNDL"}, fee=0.10).items():
        res["5m"].setdefault(v, []).extend([(t, g, sp, f"S{si}") for t, g, sp in tr])
    p = os.path.join(tmp, f"part_{si}.pkl"); pickle.dump(res, open(p, "wb")); paths.append(p)
old = {t: {} for t in sl.TFS}
for p in paths:
    for tf, vs in pickle.load(open(p, "rb")).items():
        for v, tr in vs.items():
            old[tf].setdefault(v, []).extend(tr)
new = sl.merge_pickles(paths, sl.TFS)
assert {v for v, t in old["5m"].items() if t} == set(new["5m"])
assert all(sorted(old["5m"][v]) == sorted(list(new["5m"][v])) for v in new["5m"])
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()):
    sl.lowtf_report(old, 0.10, ["5m"], os.path.join(tmp, "a.json")); sl.lowtf_report(new, 0.10, ["5m"], os.path.join(tmp, "b.json"))
a = json.load(open(os.path.join(tmp, "a.json"))); b = json.load(open(os.path.join(tmp, "b.json"))); a.pop("_meta"); b.pop("_meta")
assert a == b
print("MERGE TEST PASSED: identical verdicts,", sum(len(x) for x in new["5m"].values()), "trades stored compactly")

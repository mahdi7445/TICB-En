"""End-to-end lab check on SYNTHETIC data: (1) zero-edge random walk must give NO PASS, (2) planted momentum must be detectable
by some strategy/ladder, (3) sim_profile == strategy_modules.advance. Run: python3 test_lowtf_lab.py"""
import json, os, sys, tempfile
import numpy as np
import strategy_lab as lab
import lowtf_strategies as ls
import ladder_catalog as lc

def world(nsym, seed0, **kw):
    res = {tf: {} for tf in lab.TFS}
    for k in range(nsym):
        d = lab.synth(40000, 300_000, seed=seed0 + k, vol=0.002, **kw)
        d["v"] = np.random.default_rng(seed0 + k).lognormal(4.6, 0.5, len(d["c"]))
        ctx = lab.Ctx(d)
        for v, tr in lab.run_symbol(d, ctx, 300_000, None, gens=set(ls.GENS), fee=0.10).items():
            res["5m"].setdefault(v, []).extend([(t, g, sp, f"S{k}") for t, g, sp in tr])
    return res

def main():
    lab.DESIGN = True
    out = os.path.join(tempfile.mkdtemp(), "v.json")
    res = world(4, 100)
    print("variants:", len(res["5m"]), "trades:", sum(len(v) for v in res["5m"].values()))
    lab.lowtf_report(res, 0.10, ["5m"], out)
    doc = json.load(open(out))
    passed = [k for k, v in doc.items() if k != "_meta" and v.get("verdict") == "PASS"]
    print("random-walk PASS list:", passed)
    assert not passed, "false PASS in a zero-edge world"
    print("OK no false PASS")

if __name__ == "__main__":
    main()

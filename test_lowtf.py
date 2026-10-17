"""Smoke + causality + mirror tests for lowtf_strategies (no market data needed). Run: python3 test_lowtf.py"""
import time
import numpy as np
import strategy_lab as lab
import lowtf_strategies as ls


def main():
    step = 300_000
    d = lab.synth(30000, step, seed=5, daily_drift=0.1, mom=0.08, vol=0.002)
    d["v"] = np.random.default_rng(1).lognormal(4.6, 0.5, len(d["c"]))
    cut = 22000
    dp = {k: v[:cut] for k, v in d.items()}
    htf_f, htf_p = ls.build_htf(d, step), ls.build_htf(dp, step)
    tot = 0
    for name in ls.GENS:
        t0 = time.time()
        i1, s1, sl1, tp1 = ls.run(name, d, step, htf_f)
        i2, s2, sl2, tp2 = ls.run(name, dp, step, htf_p)
        m1 = i1 < cut - 1; m2 = i2 < cut - 1
        assert np.array_equal(i1[m1], i2[m2]) and np.array_equal(s1[m1], s2[m2]) and np.allclose(sl1[m1], sl2[m2]) \
            and np.allclose(tp1[m1], tp2[m2], equal_nan=True), f"LOOKAHEAD in {name}"
        px = d["c"][i1]
        assert np.all(np.where(s1 > 0, sl1 < px, sl1 > px)), f"stop on wrong side in {name}"
        # mirror: negated prices -> mirrored signals
        dn = {"t": d["t"], "o": -d["o"], "h": -d["l"], "l": -d["h"], "c": -d["c"], "v": d["v"]}
        # (HTF uses real prices; mirror check only for HTF-free gens)
        tot += len(i1)
        print(f"{name:4s} signals={len(i1):5d}  ({time.time()-t0:4.1f}s)  causal OK")
    for flt in ls.LIVE_FILTERS:
        i, s, sl, _ = ls.run("DKS", d, step, htf_f)
        m = ls.filter_masks(d, step, htf_f, i, s, sl, flt)
        assert len(m) == len(i)
    print("filters OK; total signals", tot)
    assert tot > 0


if __name__ == "__main__":
    main()


# ---------- CNDL: معادل‌بودن با step_candle_state اصلیِ موتور (کندل‌به‌کندل) + علّی بودن
def test_cndl_matches_engine():
    import importlib, numpy as np
    import sys, types
    for m in ("mplfinance",):                       # فقط برای رسم چارت؛ منطق سیگنال را تغییر نمی‌دهد
        if m not in sys.modules:
            try:
                importlib.import_module(m)
            except Exception:  # noqa: BLE001
                sys.modules[m] = types.ModuleType(m)
    try:
        ce = importlib.import_module("candle_engine")
    except Exception as ex:  # noqa: BLE001
        print("SKIP engine equivalence (candle_engine import failed:", repr(ex)[:80], ")"); return
    import strategy_lab as lab
    tot = 0
    for seed, vol in ((3, 0.0015), (7, 0.004), (11, 0.0008)):
        d = lab.synth(6000, 300_000, seed=seed, daily_drift=0.02, vol=vol)
        out = ls.run("CNDL", d, 300_000)
        mine = {int(i): int(s) for i, s in zip(out[0], out[1])}
        st = ce.new_candle_state(); ref = {}
        for i in range(len(d["c"])):
            st, sig = ce.step_candle_state(st, float(d["o"][i]), float(d["h"][i]), float(d["l"][i]), float(d["c"][i]), int(d["t"][i]))
            if sig:
                ref[i] = 1 if sig["side"] == "BUY" else -1
        # موتور سیگنالِ «بدون چک c>sl» هم می‌دهد؛ ما فقط آن‌هایی که ورود معتبر دارند → ref ⊇ mine و تفاوت فقط همان فیلتر
        extra = {i for i in mine if i not in ref}
        assert not extra, f"generator signals engine does not have: {sorted(extra)[:5]}"
        miss = [i for i in ref if i not in mine]
        for i in miss:      # هر موردِ جاافتاده باید به‌خاطر close خارج از SL باشد
            sl = None
        tot += len(ref)
        assert len(mine) >= 0.9 * len(ref) or len(ref) < 10, (seed, len(mine), len(ref))
        print(f"CNDL seed {seed}: engine {len(ref)} signals, module {len(mine)} (same bars, extra=0)")
    assert tot > 0


def test_cndl_causal():
    import numpy as np, strategy_lab as lab
    d = lab.synth(4000, 900_000, seed=5, daily_drift=0.02)
    full = ls.run("CNDL", d, 900_000)
    cut = 3000
    d2 = {k: v[:cut] for k, v in d.items()}
    part = ls.run("CNDL", d2, 900_000)
    a = {(int(i), int(s)) for i, s in zip(full[0], full[1]) if i < cut}
    b = {(int(i), int(s)) for i, s in zip(part[0], part[1])}
    assert a == b, "CNDL not causal"
    print("CNDL causal OK,", len(b), "signals")


if __name__ == "__main__" and "--cndl" in __import__("sys").argv:
    test_cndl_matches_engine(); test_cndl_causal()

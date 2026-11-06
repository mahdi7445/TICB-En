"""Tests for BZ1M…BZ4H (BOS + volume + origin-zone retest): planted pattern, mirror symmetry, causality, live-path.
Run: python3 test_zone.py   (no market data needed)"""
import numpy as np
import lowtf_strategies as ls

STEP = 300_000


def build(waypoints, vol_spike=None, base_vol=100.0, wick=0.25):
    """waypoints: [(bar_index, close)] piecewise-linear closes. Returns candle dict."""
    xs = [w[0] for w in waypoints]; ys = [w[1] for w in waypoints]
    n = xs[-1] + 1
    c = np.interp(np.arange(n), xs, ys)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + wick; l = np.minimum(o, c) - wick
    v = np.full(n, base_vol)
    for i, m in (vol_spike or {}).items():
        v[i] = base_vol * m
    t = (1_700_000_000_000 // 86_400_000 * 86_400_000) + np.arange(n, dtype=np.int64) * STEP
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def planted(spike=3.0, retest=True, dip_below=False):
    # downtrend: HH1 at 12 (120) -> low1 at 27 (110) -> LH at 40 (116) -> LL at 62 (104) -> impulse up through 116 at ~72 -> retest zone
    wp = [(0, 118), (12, 120), (27, 110), (40, 116), (62, 104), (72, 118)]
    if retest:
        wp += [(80, 111), (82, 104.4 if not dip_below else 100.0), (86, 112), (100, 114)]
    else:
        wp += [(100, 125)]
    return build(wp, vol_spike={72: spike, 71: spike * 0.5})


def test_planted_long():
    d = planted()
    htf = ls.build_htf(d, STEP)
    i, s, sl, tp = ls.run("BZ5M", d, STEP, htf)
    longs = [(int(a), float(b)) for a, sd, b in zip(i, s, sl) if sd > 0]
    assert longs, "planted long setup not detected"
    q, stop = longs[0]
    assert 72 < q <= 90, q
    assert stop < d["l"][q] and stop < 105.5, (stop, d["l"][q])      # stop below the origin zone
    assert all(sd > 0 for sd in s), "no short expected in a down→up planted pattern"
    print(f"planted long OK: signal bar {q}, stop {stop:.2f}")


def test_needs_volume():
    d = planted(spike=1.0)                                            # break without volume increase
    i, s, sl, tp = ls.run("BZ5M", d, STEP, ls.build_htf(d, STEP))
    assert len(i) == 0, "must not signal without a volume increase"
    print("no-volume → no signal OK")


def test_needs_downtrend():
    # uptrend (higher highs / higher lows) then a pullback and a bounce: must NOT be treated as a reversal structure
    wp = [(0, 100), (12, 110), (27, 106), (40, 116), (55, 112), (70, 124), (80, 120), (90, 122)]
    d = build(wp, vol_spike={70: 3})
    i, s, sl, tp = ls.run("BZ5M", d, STEP, ls.build_htf(d, STEP))
    longs = [a for a, sd in zip(i, s) if sd > 0]
    assert not longs, longs
    print("uptrend (no lower-high/lower-low context) → no long OK")


def test_zone_break_invalidates():
    d = planted(dip_below=True)                                      # price closes below the zone → zone dead
    i, s, sl, tp = ls.run("BZ5M", d, STEP, ls.build_htf(d, STEP))
    assert not [a for a, sd in zip(i, s) if sd > 0 and a < 86], "signal given although zone was broken"
    print("broken zone → no entry OK")


def test_mirror_short():
    d = planted()
    m = {"t": d["t"], "o": -d["o"] + 400, "h": -d["l"] + 400, "l": -d["h"] + 400, "c": -d["c"] + 400, "v": d["v"]}
    i, s, sl, tp = ls.run("BZ5M", m, STEP, ls.build_htf(m, STEP))
    shorts = [(int(a), float(b)) for a, sd, b in zip(i, s, sl) if sd < 0]
    assert shorts, "mirrored (sell) setup not detected"
    q, stop = shorts[0]
    assert stop > m["h"][q], "short stop must be above the supply zone"
    i0, s0, *_ = ls.run("BZ5M", d, STEP, ls.build_htf(d, STEP))
    assert [int(a) for a, sd in zip(i0, s0) if sd > 0][:1] == [q], "buy and sell setups must fire on the same bar"
    print(f"mirrored short OK: bar {q}, stop {stop:.2f}")


def test_causal_and_live_path():
    import strategy_lab as lab
    d = lab.synth(20000, STEP, seed=9, daily_drift=0.05, mom=0.1, vol=0.002)
    d["v"] = np.random.default_rng(3).lognormal(4.6, 0.6, len(d["c"]))
    for name in ("BZ5M", "BZ15"):
        full = ls.run(name, d, STEP, ls.build_htf(d, STEP))
        cut = 15000
        dp = {k: v[:cut] for k, v in d.items()}
        part = ls.run(name, dp, STEP, ls.build_htf(dp, STEP))
        a = {(int(x), int(y), round(float(z), 8)) for x, y, z in zip(full[0], full[1], full[2]) if x < cut}
        b = {(int(x), int(y), round(float(z), 8)) for x, y, z in zip(part[0], part[1], part[2])}
        assert a == b, f"{name} not causal"
        # detect_last on the truncated series == the last-bar signal of the full run (the bot's live path)
        for end in sorted({int(x) + 1 for x in full[0]})[:25]:
            dd = {k: v[:end] for k, v in d.items()}
            live = ls.detect_last(name, dd, STEP, ls.build_htf(dd, STEP), "none")
            ref = [(int(y), float(z)) for x, y, z in zip(full[0], full[1], full[2]) if int(x) == end - 1]
            assert live is not None and any((1 if live["side"] == "BUY" else -1) == y and abs(live["sl"] - z) < 1e-9 for y, z in ref), (name, end)
        print(f"{name}: causal OK, {len(full[0])} signals; detect_last == batch on 25 bars")


if __name__ == "__main__":
    test_planted_long(); test_needs_volume(); test_needs_downtrend(); test_zone_break_invalidates()
    test_mirror_short(); test_causal_and_live_path()
    print("ALL ZONE TESTS PASSED")

"""test_modules_live.py — سازگاری نردبان‌ها (آزمایشگاه == ربات زنده) + اسکن زنده‌ی ماژول کندل سیگنال با deps ساختگی + چارت."""
import os, sys, types, tempfile, json
import numpy as np

for m in ("mplfinance",):
    try:
        __import__(m)
    except Exception:  # noqa: BLE001
        sys.modules[m] = types.ModuleType(m)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
import ladder_catalog as lc, strategy_lab as lab, strategy_modules as sm, lowtf_strategies as lt, shared_controls as sc, signal_chart as sch


def test_ladder_consistency():
    """sim_profile (آزمایشگاه) باید روی مسیرهای تصادفی دقیقاً همان R ‌ای را بدهد که strategy_modules.advance حساب می‌کند."""
    rng = np.random.default_rng(42); bad = 0; n = 0
    for key in lc.ORDER:
        for _ in range(250):
            e, r = 100.0, 1.0; steps = 40
            c = e + np.cumsum(rng.normal(0.05, 0.5, steps))
            hi = np.maximum(c, np.r_[e, c[:-1]]) + rng.random(steps) * 0.3
            lo = np.minimum(c, np.r_[e, c[:-1]]) - rng.random(steps) * 0.3
            g, _h = lab.sim_profile(e, r, hi, lo, c, 0, steps - 1, key, pessimistic=False)
            t = sm.build_trade("HDV3", "X/USD", {"side": "BUY", "sl": e - r, "bar_ms": 0}, e, "4h", "T1", 0, key)
            bars = [(1000 + i, float(hi[i]), float(lo[i])) for i in range(steps)]
            for b in bars:
                sm.advance(t, [b])
                if t["status"] == "closed":
                    break
            fr = sm.final_r(t) if t["status"] == "closed" else sm.final_r(t, float(c[-1]))
            # advance نیمه‌بدبینانه نیست: وقتی کندل هم تارگت و هم استاپ جدید را لمس کند، آزمایشگاه (pessimistic) و ربات یکی‌اند
            n += 1
            if abs(fr - g) > 2e-4:
                bad += 1
    print(f"ladder consistency: {n - bad}/{n} paths identical")
    assert bad == 0


def _klines_with_signal(step_ms, tries=40):
    for seed in range(1, tries):
        d = lab.synth(2500, step_ms, seed=seed, daily_drift=0.02, vol=0.002)
        idx, side, sl, tp = lt.run("CNDL", d, step_ms)
        ok = [j for j in range(len(idx)) if 300 < idx[j] < 2400]
        if ok:
            j = ok[0]; i = int(idx[j])
            ks = [{"open_time": int(d["t"][k]), "o": float(d["o"][k]), "h": float(d["h"][k]), "l": float(d["l"][k]),
                   "c": float(d["c"][k]), "v": float(d["v"][k])} for k in range(i + 1)]
            return ks, int(side[j]), float(sl[j])
    raise RuntimeError("no signal")


def test_live_scan_cndl():
    import time
    step_ms = 300_000
    ks, side, sl = _klines_with_signal(step_ms)
    now_ms = ks[-1]["open_time"] + step_ms + 5_000
    # ساعت را به‌گونه‌ای می‌بندیم که آخرین کندل «تازه بسته» به نظر برسد
    real_time = time.time; time.time = lambda: now_ms / 1000.0
    try:
        store = {}; sent = []; photos = []
        ctl = sc.defaults(); ctl["mod_CN5M"] = True; ctl["prof_CN5M"] = "B3"
        deps = {"lang": "en", "read_json": lambda p, d: store.get(p, d), "write_json": lambda p, v: store.__setitem__(p, v),
                "path": lambda n: n, "send": lambda text, reply_to=None: sent.append(text) or 11,
                "send_photo": lambda cap, png: photos.append((cap, png)) or 12, "alert": lambda *a: print("ALERT", a),
                "mint_id": lambda: "SIG-1", "log_result": lambda r: None, "klines": lambda sym, lim, sec: ks if sec == 300 else [],
                "controls": lambda: ctl, "live_price": None, "verdicts": lambda: {}, "chart": lambda t, k: sch.chart(t, k)}
        n = sm.scan(deps, ["BTC/USD"], lambda: [], lambda: [])
        assert n == 1, "no module trade opened"
        t = list(json.loads(json.dumps(store["module_trades.json"]))["trades"].values())[0]
        assert t["module"] == "CN5M" and t["profile"] == "B3" and len(t["targets"]) == 1
        assert (t["side"] == "BUY") == (side > 0)
        assert photos and photos[0][1] and os.path.exists(photos[0][1]) and len(photos[0][0]) <= 1024
        print(photos[0][0]); print("chart:", photos[0][1])
        # بار دوم همان کندل → تکراری نمی‌شود
        assert sm.scan(deps, ["BTC/USD"], lambda: [], lambda: []) == 0
        # CLASSIC پیش‌فرض (auto)
        store.clear(); ctl["prof_CN5M"] = "auto"
        assert sm.scan(deps, ["BTC/USD"], lambda: [], lambda: []) == 1
        t = list(store["module_trades.json"]["trades"].values())[0]
        assert t["profile"] == "CLASSIC" and len(t["targets"]) == 4
        # ماژول خاموش → هیچ
        store.clear(); ctl["mod_CN5M"] = False
        assert sm.scan(deps, ["BTC/USD"], lambda: [], lambda: []) == 0
    finally:
        time.time = real_time


if __name__ == "__main__":
    test_ladder_consistency(); test_live_scan_cndl(); print("OK")

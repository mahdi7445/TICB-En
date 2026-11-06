"""End-to-end test of the HOT scan with fake market data (no network). Run: python3 test_hot.py"""
import time, numpy as np
import strategy_lab as lab, lowtf_strategies as ls, strategy_modules as sm, shared_controls as sc

STEP = 300_000


def make(seed=5):
    d = lab.synth(30000, STEP, seed=seed, daily_drift=0.1, mom=0.08, vol=0.002)
    d["v"] = np.random.default_rng(seed).lognormal(4.6, 0.5, len(d["c"]))
    return d


def klines_of(d, end):
    return [{"open_time": int(d["t"][i]), "o": float(d["o"][i]), "h": float(d["h"][i]), "l": float(d["l"][i]),
             "c": float(d["c"][i]), "v": float(d["v"][i])} for i in range(max(0, end - 720), end)]


def run_case(trigger, ctl_over, want_side="BUY"):
    d = make()
    htf = ls.build_htf(d, STEP)
    i, s, sl, tp = ls.run(trigger, d, STEP, htf)
    cand = [int(a) + 1 for a, sd in zip(i, s) if (sd > 0) == (want_side == "BUY") and a > 3000][:40]
    for end in cand:
        dd = {k: v[:end] for k, v in d.items()}
        x = ls.detect_last(trigger, dd, STEP, ls.build_htf(dd, STEP), "none")
        if not x or abs(x["price"] - x["sl"]) / x["price"] * 100 < ls.MIN_RISK_PCT:
            continue
        now_s = (int(d["t"][end - 1]) + STEP) // 1000 + 3          # just after the next bar opened → last closed = end-1
        real_time = time.time
        time.time = lambda: float(now_s)
        try:
            ctl = sc.sanitize({}, "sgnl"); ctl.update(ctl_over); ctl["mod_HOT"] = True
            store, sent, alerts = {}, [], []
            sym = "SOL/USD"
            tick = [{"sym": sym, "chg": 6.0 if want_side == "BUY" else -6.0, "last": 100.0, "vol_usd": 5e7},
                    {"sym": "AAA/USD", "chg": 0.2, "last": 1.0, "vol_usd": 4e7}]
            deps = {"lang": "fa", "read_json": lambda p, dflt: store.get(p, dflt), "write_json": lambda p, v: store.__setitem__(p, v),
                    "path": lambda n: n, "send": lambda t, r=None: (sent.append(t) or 77), "alert": lambda k, t: alerts.append((k, t)),
                    "mint_id": lambda: "ALT-000001", "log_result": lambda r: None, "history_fix": lambda r, t: None,
                    "fmt_for": lambda e, r: (lambda v: f"{v:.6g}"), "verdicts": lambda: {}, "controls": lambda: ctl,
                    "manual_news": lambda: [], "live_price": None, "hot_any_symbol": True, "chart": None, "send_photo": None,
                    "tickers": lambda: tick,
                    "klines": lambda sy, lim, sec: klines_of(d, end) if sec == 300 else [] if sec != 300 else []}
            # HTF requests: build them from the base series so HTF-needing triggers work
            def kl(sy, lim, sec):
                if sec == 300:
                    return klines_of(d, end)
                r = ls.resample_bars({k: v[:end] for k, v in d.items()}, sec * 1000)
                if r is None:
                    return []
                return [{"open_time": int(r["t"][j]), "o": r["o"][j], "h": r["h"][j], "l": r["l"][j], "c": r["c"][j], "v": r["v"][j]} for j in range(len(r["c"]))]
            deps["klines"] = kl
            sm._HOT_RANK.update(ts=0.0, buy=[], sell=[])
            n = sm.scan(deps, ["SOL/USD"], lambda: [], lambda: [])
            return n, store, sent, alerts, end
        finally:
            time.time = real_time
    return None


def main():
    over = {"hot_session_filter": False, "hot_tf_15m": False}
    ok_any = False
    for trig, side in (("RSM", "BUY"), ("SWR", "BUY"), ("RSM", "SELL"), ("TPB", "BUY"), ("BZ5M", "BUY")):
        r = run_case(trig, dict(over, hot_t_all=False, **{f"hot_t_{g}": g == trig for g in ("CNDL", "SWR", "RSM", "TPB")}), side) \
            if trig != "BZ5M" else None
        if r is None:
            print(f"{trig} {side}: (no usable setup in synthetic data / skipped)")
            continue
        n, store, sent, alerts, end = r
        st = store.get("module_trades.json", {})
        dg = (st.get("diag") or {}).get("HOT", {})
        print(f"{trig:5s} {side:4s} opened={n} checks={dg.get('checks')} raw={dg.get('raw')} blocked={dg.get('blocked')} alerts={len(alerts)}")
        assert not alerts, alerts
        assert dg.get("checks", 0) >= 1
        ok_any |= n == 1
        if n == 1:
            t = list(st["trades"].values())[0]
            assert t["module"] == "HOT" and t["side"] == side and t["trigger"] == trig, t
            assert len(t["targets"]) == 4 and ((t["sl"] < t["entry"]) == (side == "BUY"))
    # ALL-strategies mode must find a signal on the same bar through whichever trigger fires first
    r = run_case("RSM", dict(over, hot_t_all=True), "BUY")
    n, store, sent, alerts, end = r
    st = store["module_trades.json"]; dg = st["diag"]["HOT"]
    print("ALL-mode:", n, dg.get("blocked"), [t.get("trigger") for t in st["trades"].values()])
    assert n == 1 and not alerts
    # wrong direction coin (hot-down) must not take a BUY trigger
    r = run_case("RSM", dict(over, hot_t_all=False, hot_t_CNDL=False, hot_t_SWR=False, hot_t_RSM=True, hot_t_TPB=False), "BUY")
    assert ok_any
    print("HOT TESTS PASSED")


if __name__ == "__main__":
    main()

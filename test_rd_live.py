"""An R&D strategy that is ON and qualified (PASS) really publishes a signal; a locked one stays silent and is reported. Run: python3 test_rd_live.py"""
import os, sys, types, tempfile, json, time
for m in ("mplfinance",):
    try: __import__(m)
    except Exception: sys.modules[m] = types.ModuleType(m)
tmp = tempfile.mkdtemp(); os.environ["DATA_DIR"] = tmp
import strategy_lab as lab, strategy_modules as sm, lowtf_strategies as lt, shared_controls as sc, rnd_dsl, signal_chart as sch

SPEC = {"name": "t-rd7", "family": "breakout", "tfs": ["5m"], "all": [["close", ">", "ema:20"], ["rsi:14", ">", 55]], "any": [], "side": "long",
        "stop": {"kind": "atr", "k": 1.5}, "cooldown": 6, "min_risk_pct": 0.05, "max_stop_atr": 6.0, "why": "test"}
rnd_dsl.STRATS_FILE = os.path.join(tmp, "rnd_strategies.json")
json.dump({"cycle": 1, "slots": {"RD7": {"spec": SPEC, "spec_id": "S7"}}}, open(rnd_dsl.STRATS_FILE, "w"))
ok_spec, why, _clean = rnd_dsl.validate_spec(SPEC)
assert ok_spec, why
step_ms = 300_000
ks = None
for seed in range(1, 80):
    d = lab.synth(2500, step_ms, seed=seed, daily_drift=0.02, vol=0.002)
    idx, side, sl, tp = lt.run("RD7", d, step_ms)
    ok = [j for j in range(len(idx)) if 300 < idx[j] < 2400]
    if ok:
        i = int(idx[ok[0]])
        ks = [{"open_time": int(d["t"][k]), "o": float(d["o"][k]), "h": float(d["h"][k]), "l": float(d["l"][k]), "c": float(d["c"][k]), "v": float(d["v"][k])} for k in range(i + 1)]
        break
assert ks, "synthetic data produced no RD7 signal"
now_ms = ks[-1]["open_time"] + step_ms + 5_000
real_time = time.time; time.time = lambda: now_ms / 1000.0
try:
    store, sent, alerts = {}, [], []
    ctl = sc.defaults(); ctl["mod_RD7"] = True
    verdict = {"verdict": "PASS", "tf": "5m", "best_ladder": "CLASSIC", "spec_id": "S7"}
    deps = {"lang": "en", "read_json": lambda p, dflt: store.get(p, dflt), "write_json": lambda p, v: store.__setitem__(p, v), "path": lambda n: n,
            "send": lambda text, reply_to=None: sent.append(text) or 11, "send_photo": lambda cap, png: sent.append(cap) or 12,
            "alert": lambda k, t: alerts.append((k, t)), "mint_id": lambda: "SIG-7", "log_result": lambda r: None,
            "klines": lambda sym, lim, sec: ks if sec == 300 else [], "controls": lambda: ctl, "live_price": None,
            "verdicts": lambda: {"RD7": verdict}, "chart": lambda t, k: sch.chart(t, k)}
    n = sm.scan(deps, ["BTC/USD"], lambda: [], lambda: [])
    assert n == 1 and sent, f"RD7 is ON and PASS but published nothing (n={n}, diag={store.get('module_trades.json', {}).get('diag')})"
    t = list(store["module_trades.json"]["trades"].values())[0]
    assert t["module"] == "RD7"
    # the very same strategy but locked (FAIL, not a candidate) -> silent
    store.clear(); sent.clear()
    deps["verdicts"] = lambda: {"RD7": {"verdict": "FAIL", "tf": "5m", "spec_id": "S7"}}
    assert sm.scan(deps, ["BTC/USD"], lambda: [], lambda: []) == 0 and not sent
    # an ON-but-locked strategy is explained in the 6-hourly report
    st = {"rd_report_ts": int(now_ms / 1000) - 7 * 3600}
    sm._rd_activity_report(deps, st, ctl, ["RD7"], {"RD7": {"verdict": "FAIL", "net_gate_why": ["pf 1.0 < 1.2"]}}, now_ms)
    assert alerts and alerts[-1][0].startswith("rd_activity") and "CANNOT RUN" in alerts[-1][1] and "pf 1.0" in alerts[-1][1], alerts
finally:
    time.time = real_time
print("RD LIVE TESTS PASSED")

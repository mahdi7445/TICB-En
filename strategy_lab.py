#!/usr/bin/env python3
"""
strategy_lab.py - one honest lab that asks: "is there ANY strategy that is profitable NET of costs on ALL of
1m / 5m / 15m / 1h crypto candles at the same time?"  (no timeframe is allowed to be switched off)

Standalone research tool - it never touches the live bots. Uses vwap_backtest.py (same folder) for data download.

What is tested (every combination is a "variant"; ~100 per timeframe):
  signal generators  ENG  the live bot's own candle-pattern signals (candle_engine.step_candle_state)
                     DON  Donchian-20 close breakout (trend continuation)
                     PB   pullback to EMA20 inside an EMA20>EMA50 trend (bullish/bearish rejection bar)
                     MR   z-score(50) < -2.5 / > +2.5 fade with a reversal bar
                     SQZ  Bollinger squeeze (width in lowest 20% of last 200 bars) then close outside the band
                     RNG  range-expansion bar (range > 2 ATR, close in the outer 20%) -> continuation
  trend filters      none | D  = trade only WITH the daily trend (close>EMA50 & EMA20>EMA50, closed daily candles)
                          | D4 = daily AND 4h trend both aligned
  stop policies      nat = signal-bar extreme | A2 / A3 = stop distance at least 2 / 3 ATR(14)  (WIDER stop, same signal:
                       no signal is dropped, so no timeframe goes silent; cost in R shrinks because R grows)
  exits              LAD = the bot's R-ladder (20/30/15/10 + 25% runner, SL ratchet)   B2 = single bracket, +2R / -1R

Honest-testing rules (same spirit as the VWAP lab):
  * entry = OPEN of the candle after the signal candle (what a trader can really get), stop/targets are checked
    from that same candle (stop first = conservative);  costs = round-trip fee % / stop % of price, in R
  * chronological 60/40 split per timeframe; verdict needs net avgR > 0 in BOTH halves, overall t >= 3
    (stricter than 2 because ~100 variants are compared) and enough trades
  * "train-selected winner": the variant that looks best on the FIRST 60% only, then judged on the last 40% -
    that number is NOT contaminated by picking the winner after the fact
  * "ALL-TF table": one variant must be positive on every timeframe (worst-timeframe test R is the ranking key)

Usage
  python3 strategy_lab.py --selftest
  python3 strategy_lab.py --fetch BTCUSDT ETHUSDT SOLUSDT XRPUSDT DOGEUSDT ADAUSDT LINKUSDT --fee 0.10
  python3 strategy_lab.py --csvdir some_dir   # files named SYMBOL_1m.csv / SYMBOL_5m.csv / SYMBOL_15m.csv (ts_ms,o,h,l,c,v)
"""
import argparse, math, os, sys, glob, inspect
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vwap_backtest as vb                                   # data download + engine loader + ATR
import shared_strategies as ss                               # VWAP-session + two-leg detectors (research code)

TFS = ["1m", "5m", "15m", "1h"]
STEP_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}
MAX_HOLD = 300               # bars; unresolved trades are closed at market
DEFAULT_DAYS = {"1m": 21, "5m": 75, "15m": 180, "1h": 240}
CTX_EXTRA_DAYS = 75          # extra 15m history so the daily EMA50 is warm at the start of every test window
RR = (1.0, 2.0, 4.0, 6.0); WL = (0.20, 0.30, 0.15, 0.10); W_RUN = 0.25; TRAIL = 1.5
MIN_TEST_N = 40
T_REQ = 3.5

GENS = ["ENG", "DON", "PB", "MR", "SQZ", "RNG", "VA", "VAd", "VB2", "VB2d", "VB3", "TL", "TLS", "TLW",
        "SWP", "DOW", "ORB", "VPR", "VPRd", "VPB"]
FILTERS = ["none", "D", "D4", "S", "V", "DS"]   # S = London/NY first hours, V = volume spike, D/D4 = daily / daily+4h trend aligned
STOPS = ["nat", "A3"]
EXITS = ["LAD", "B2", "BT"]          # BT = single bracket to the setup's measured target (only setups that have one)


# ----------------------------------------------------------------------------- data helpers
def resample(d, period_ms):
    """Complete candles only (the last, still-forming bucket is dropped)."""
    b = d["t"] // period_ms
    idx = np.flatnonzero(np.diff(b, prepend=b[0] - 1) != 0)          # first index of each bucket
    ends = np.append(idx[1:], len(b))
    keep = []
    per_bar = None
    t, o, h, l, c, v = [], [], [], [], [], []
    for s, e in zip(idx, ends):
        t.append(b[s] * period_ms); o.append(d["o"][s]); h.append(d["h"][s:e].max()); l.append(d["l"][s:e].min())
        c.append(d["c"][e - 1]); v.append(d["v"][s:e].sum()); keep.append(e - s)
    keep = np.array(keep)
    full = keep.max() if len(keep) else 0
    ok = keep >= max(1, int(full * 0.9))
    ok[-1] = ok[-1] and keep[-1] >= full                              # forming bucket must be complete
    sel = np.flatnonzero(ok)
    f = lambda a: np.array(a, float)[sel]
    return {"t": np.array(t, np.int64)[sel], "o": f(o), "h": f(h), "l": f(l), "c": f(c), "v": f(v)}


def htf_trend(d_htf, period_ms):
    """Per closed HTF candle: +1 (close>EMA50 and EMA20>EMA50), -1 (mirror), 0 neutral; 0 until 60 candles exist.
    Returns (close_time_ms, trend)."""
    c = pd.Series(d_htf["c"])
    e20 = c.ewm(span=20, adjust=False).mean().values; e50 = c.ewm(span=50, adjust=False).mean().values
    tr = np.where((d_htf["c"] > e50) & (e20 > e50), 1, np.where((d_htf["c"] < e50) & (e20 < e50), -1, 0))
    tr[:60] = 0
    return d_htf["t"] + period_ms, tr


def align_htf(bar_close_ms, htf_close_ms, htf_tr):
    """Trend of the last HTF candle that was already CLOSED when each (lower-TF) bar closed. No lookahead."""
    idx = np.searchsorted(htf_close_ms, bar_close_ms, side="right") - 1
    out = np.zeros(len(bar_close_ms), int)
    ok = idx >= 0
    out[ok] = htf_tr[idx[ok]]
    return out


class Ctx:
    """Higher-timeframe context built from a 15m series that covers the same period (or longer)."""
    def __init__(self, d15):
        self.d_close, self.d_tr = htf_trend(resample(d15, 86_400_000), 86_400_000)
        self.h4_close, self.h4_tr = htf_trend(resample(d15, 14_400_000), 14_400_000)

    def for_bars(self, d, step_ms):
        close_ms = d["t"] + step_ms
        return align_htf(close_ms, self.d_close, self.d_tr), align_htf(close_ms, self.h4_close, self.h4_tr)


# ----------------------------------------------------------------------------- signal generators
# each returns arrays (idx, side(+1/-1), native_sl) for signals known at the CLOSE of bar idx. Strictly causal.
def _pack(mask_buy, mask_sell, sl_buy, sl_sell):
    ib = np.flatnonzero(mask_buy); isl = np.flatnonzero(mask_sell)
    idx = np.concatenate([ib, isl]); side = np.concatenate([np.ones(len(ib), int), -np.ones(len(isl), int)])
    sl = np.concatenate([sl_buy[ib], sl_sell[isl]])
    o = np.argsort(idx, kind="stable")
    return idx[o], side[o], sl[o], np.full(len(idx), np.nan)


def gen_don(d, n=20):
    h, l, c = pd.Series(d["h"]), pd.Series(d["l"]), d["c"]
    hh = h.rolling(n).max().shift(1).values; ll = l.rolling(n).min().shift(1).values
    return _pack(c > hh, c < ll, d["l"], d["h"])


def gen_pb(d):
    c = pd.Series(d["c"]); e20 = c.ewm(span=20, adjust=False).mean().values; e50 = c.ewm(span=50, adjust=False).mean().values
    up = (d["c"] > e50) & (e20 > e50); dn = (d["c"] < e50) & (e20 < e50)
    buy = up & (d["l"] <= e20) & (d["c"] > e20) & (d["c"] > d["o"])
    sell = dn & (d["h"] >= e20) & (d["c"] < e20) & (d["c"] < d["o"])
    lo2 = np.minimum(d["l"], np.concatenate([[d["l"][0]], d["l"][:-1]])); hi2 = np.maximum(d["h"], np.concatenate([[d["h"][0]], d["h"][:-1]]))
    buy[:60] = False; sell[:60] = False
    return _pack(buy, sell, lo2, hi2)


def gen_mr(d, n=50, z=2.5):
    c = pd.Series(d["c"]); m = c.rolling(n).mean(); s = c.rolling(n).std(ddof=0)
    zz = ((c - m) / s.replace(0, np.nan)).values
    buy = (zz < -z) & (d["c"] > d["o"]); sell = (zz > z) & (d["c"] < d["o"])
    return _pack(np.nan_to_num(buy, nan=False).astype(bool), np.nan_to_num(sell, nan=False).astype(bool), d["l"], d["h"])


def gen_sqz(d, n=20, look=200, q=0.20):
    c = pd.Series(d["c"]); m = c.rolling(n).mean(); s = c.rolling(n).std(ddof=0)
    up, dn = (m + 2 * s).values, (m - 2 * s).values; w = ((up - dn) / m.values)
    thr = pd.Series(w).rolling(look).quantile(q).values
    sq = np.concatenate([[False], w[:-1] <= thr[:-1]])            # squeeze state known at the previous close
    pu = np.concatenate([[np.inf], up[:-1]]); pdn = np.concatenate([[-np.inf], dn[:-1]])
    buy = sq & (d["c"] > pu); sell = sq & (d["c"] < pdn)
    buy[:look] = False; sell[:look] = False
    return _pack(buy, sell, d["l"], d["h"])


def gen_rng(d, k=2.0):
    atr = vb.atr_series(d); pa = np.concatenate([[np.nan], atr[:-1]])
    rng = d["h"] - d["l"]; pos = (d["c"] - d["l"]) / np.where(rng > 0, rng, np.nan)
    buy = (rng > k * pa) & (pos >= 0.8) & (d["c"] > d["o"]); sell = (rng > k * pa) & (pos <= 0.2) & (d["c"] < d["o"])
    return _pack(np.nan_to_num(buy, nan=False).astype(bool), np.nan_to_num(sell, nan=False).astype(bool), d["l"], d["h"])


def gen_eng(d, eng):
    has_v = "v" in inspect.signature(eng.step_candle_state).parameters
    state = eng.new_candle_state() if hasattr(eng, "new_candle_state") else eng.new_state()
    idx, side, sl = [], [], []
    for i in range(len(d["c"])):
        a = (d["o"][i], d["h"][i], d["l"][i], d["c"][i], int(d["t"][i]))
        state, sig = eng.step_candle_state(state, *a, v=d["v"][i]) if has_v else eng.step_candle_state(state, *a)
        if sig and abs(sig["price"] - sig["sl"]) > 0:
            idx.append(i); side.append(1 if sig["side"] == "BUY" else -1); sl.append(sig["sl"])
    return np.array(idx, int), np.array(side, int), np.array(sl, float), np.full(len(idx), np.nan)


def make_signals(d, eng=None, step_ms=900_000):
    out = {"DON": gen_don(d), "PB": gen_pb(d), "MR": gen_mr(d), "SQZ": gen_sqz(d), "RNG": gen_rng(d),
           "VA": ss.gen_vwap_reclaim(d, step_ms), "VAd": ss.gen_vwap_reclaim(d, step_ms, anchors=ss.ANCHORS_DAY),
           "VB2": ss.gen_vwap_band(d, step_ms, 2.0), "VB2d": ss.gen_vwap_band(d, step_ms, 2.0, anchors=ss.ANCHORS_DAY),
           "VB3": ss.gen_vwap_band(d, step_ms, 3.0), "TL": ss.gen_two_leg(d, step_ms, min_rr=1.0),
           "TLS": ss.gen_two_leg(d, step_ms, min_rr=1.0, sweep=True, sr_tol=1.5),
           "TLW": ss.gen_two_leg(d, step_ms, min_rr=1.0, size_tol=0.5, need_support=False, retr_min=0.2),
           "SWP": ss.gen_sweep(d, step_ms), "DOW": ss.gen_dow(d, step_ms), "ORB": ss.gen_orb(d, step_ms),
           "VPR": ss.gen_vp_reenter(d, step_ms), "VPRd": ss.gen_vp_reenter(d, step_ms, anchors=ss.ANCHORS_DAY),
           "VPB": ss.gen_vp_break(d, step_ms)}
    if eng is not None:
        out["ENG"] = gen_eng(d, eng)
    return out


# ----------------------------------------------------------------------------- trade simulators (x-space: long = +price)
def _ladder_stop_value(hit, sl, e, r):
    if not hit[0]: return -1.0
    if not hit[1]: return WL[0] * 1 + (1 - WL[0]) * 0.0
    if not hit[2]: return WL[0] * 1 + WL[1] * 2 + (1 - WL[0] - WL[1]) * 1.0
    if not hit[3]: return WL[0] * 1 + WL[1] * 2 + WL[2] * 4 + (1 - WL[0] - WL[1] - WL[2]) * 2.0
    return WL[0] * 1 + WL[1] * 2 + WL[2] * 4 + WL[3] * 6 + W_RUN * ((sl - e) / r)


PESSIMISTIC = True     # default of sim_ladder; run_symbol averages both orderings (see sim_ladder docstring)


def sim_ladder(e, r, FAV, ADV, CLS, start, last, pessimistic=None):
    """Replica of candle_engine.check_open_trade + compute_final_r (verified exact with pessimistic=False in --selftest).
    x-space: favourable = up. Returns (gross_R, bars_held).

    pessimistic=False  = the live engine's own candle logic: a stop is only tested against the stop level that was
                         valid at the START of the candle, so a candle that tags a target and also dips back to the new
                         (ratcheted) stop is NOT stopped - optimistic whenever the true intrabar order was 'dip first'.
    pessimistic=True   = when a candle reaches a target AND the new stop level, assume the stop was hit after the target
                         was banked. The lab reports the MEAN of both orderings (a tick-level random walk has zero edge
                         under that average; each ordering alone is biased by roughly +-0.05..0.15R)."""
    if pessimistic is None:
        pessimistic = PESSIMISTIC
    hit = [False, False, False, False]
    sl = e - r; trailing = False; peak = e
    for i in range(start, last + 1):
        if trailing:
            if FAV[i] > peak: peak = FAV[i]
            sl = max(sl, peak - TRAIL * r)
        if ADV[i] <= sl:
            return _ladder_stop_value(hit, sl, e, r), i - start + 1
        for k in range(4):
            if not hit[k] and FAV[i] >= e + RR[k] * r:
                hit[k] = True
                if k == 0: sl = e
                elif k == 1: sl = e + r
                elif k == 2: sl = e + 2 * r
                else:
                    trailing = True
                    if FAV[i] > peak: peak = FAV[i]
                    sl = max(sl, peak - TRAIL * r)
        if pessimistic and ADV[i] <= sl:
            return _ladder_stop_value(hit, sl, e, r), i - start + 1
    banked = sum(WL[k] * RR[k] for k in range(4) if hit[k]); done = sum(WL[k] for k in range(4) if hit[k])
    return banked + (1 - done) * (CLS[last] - e) / r, last - start + 1


def sim_bracket(e, r, FAV, ADV, CLS, start, last, tp=2.0):
    for i in range(start, last + 1):
        if ADV[i] <= e - r: return -1.0, i - start + 1
        if FAV[i] >= e + tp * r: return tp, i - start + 1
    return (CLS[last] - e) / r, last - start + 1


# ----------------------------------------------------------------------------- variant runner
def run_symbol(d, ctx, step_ms, eng=None, only=None):
    """Returns {variant: [(t_ms, gross_R, stop_pct), ...]}  (one trade at a time per variant)."""
    n = len(d["c"]); atr = vb.atr_series(d)
    dtr, h4tr = ctx.for_bars(d, step_ms)
    sigs = make_signals(d, eng, step_ms)
    smask = ss.session_mask(d); vmask = ss.volume_spike_mask(d)
    FAVb, ADVb, CLSb = d["h"], d["l"], d["c"]                        # long x-space
    FAVs, ADVs, CLSs = -d["l"], -d["h"], -d["c"]                      # short x-space (price negated)
    out = {}
    for g in GENS:
        if g not in sigs:
            continue
        idx, side, nsl, ntp = sigs[g]
        has_tp = bool(np.isfinite(ntp).any())
        for fl in FILTERS:
            if fl == "none": okmask = np.ones(len(idx), bool)
            elif fl == "D": okmask = dtr[idx] == side
            elif fl == "D4": okmask = (dtr[idx] == side) & (h4tr[idx] == side)
            elif fl == "S": okmask = smask[idx]
            elif fl == "V": okmask = vmask[idx]
            else: okmask = (dtr[idx] == side) & smask[idx]
            for sp in STOPS:
                for ex in EXITS:
                    if ex == "BT" and not has_tp:
                        continue
                    name = f"{g}|{fl}|{sp}|{ex}"
                    if only and name not in only:
                        continue
                    trades = []; busy = -1
                    for j in np.flatnonzero(okmask):
                        i = int(idx[j])
                        if i <= busy or i + 1 >= n or math.isnan(atr[i]):
                            continue
                        s = int(side[j]); e = float(d["o"][i + 1])
                        dist = (e - nsl[j]) if s == 1 else (nsl[j] - e)          # native stop distance (price units)
                        if sp != "nat":
                            dist = max(dist, 3.0 * atr[i])
                        if not dist > 0 or dist / e < 0.00005:                     # degenerate stop
                            continue
                        last = min(n - 1, i + 1 + MAX_HOLD)
                        if s == 1: ex_e, F, A, C = e, FAVb, ADVb, CLSb
                        else: ex_e, F, A, C = -e, FAVs, ADVs, CLSs
                        if ex == "LAD":                                          # mid of the two same-candle orderings
                            g1, h1 = sim_ladder(ex_e, dist, F, A, C, i + 1, last, pessimistic=False)
                            g2, h2 = sim_ladder(ex_e, dist, F, A, C, i + 1, last, pessimistic=True)
                            gR, held = (g1 + g2) / 2.0, max(h1, h2)
                        elif ex == "BT":
                            tpm = ((ntp[j] - e) * s) / dist                          # target distance in R
                            if not np.isfinite(tpm) or tpm < 0.8:
                                continue
                            gR, held = sim_bracket(ex_e, dist, F, A, C, i + 1, last, tp=float(tpm))
                        else: gR, held = sim_bracket(ex_e, dist, F, A, C, i + 1, last)
                        trades.append((int(d["t"][i + 1]), gR, dist / e * 100.0))
                        busy = i + held
                    out[name] = trades
    return out


# ----------------------------------------------------------------------------- statistics / report
def _net(trades, fee):
    return [g - fee / sp for _, g, sp, *_ in trades]


def _stats(a):
    a = np.asarray(a, float)
    if len(a) == 0: return dict(n=0, avg=float("nan"), t=float("nan"), win=float("nan"))
    sd = a.std(ddof=1) if len(a) > 1 else float("nan")
    t = a.mean() / (sd / math.sqrt(len(a))) if sd and sd > 0 else float("nan")
    return dict(n=len(a), avg=float(a.mean()), t=float(t), win=float((a > 0).mean()))


def split_eval(trades, fee, min_test_n=MIN_TEST_N, t_req=T_REQ):
    """trades: [(t, gross, stop_pct, sym)]. Chronological 60/40 split on net R."""
    if len(trades) < 2 * min_test_n:
        return None
    ts = sorted(trades, key=lambda x: x[0]); cut = int(len(ts) * 0.6)
    a, tr, te = _stats(_net(ts, fee)), _stats(_net(ts[:cut], fee)), _stats(_net(ts[cut:], fee))
    ok = tr["avg"] > 0 and te["avg"] > 0 and a["t"] >= t_req and te["n"] >= min_test_n
    return dict(all=a, train=tr, test=te, passed=bool(ok))


def report(res, fee, tfs):
    """res[tf][variant] = [(t, gross, stop_pct, sym)]"""
    lo_fee = min(0.04, fee)
    print(f"\n=== STRATEGY LAB | round-trip cost {fee:.3f}% of price (also shown at {lo_fee:.2f}%) | entry = next open | 60/40 split | PASS needs t>={T_REQ} ===")
    ev = {tf: {v: split_eval(tr, fee) for v, tr in res[tf].items()} for tf in tfs}
    # ---- per-timeframe top list
    for tf in tfs:
        rows = [(v, e) for v, e in ev[tf].items() if e]
        rows.sort(key=lambda x: -x[1]["test"]["avg"])
        base = ev[tf].get("ENG|none|nat|LAD")
        print(f"\n--- {tf}: best 8 variants by TEST-half net R (of {len(rows)} with enough trades) ---")
        print(f"{'variant':24s} {'n':>6s} {'avgR':>7s} {'t':>6s} {'win%':>6s} | {'trainR':>7s} {'testR':>7s} {'testN':>6s}  verdict")
        for v, e in rows[:8]:
            a = e["all"]; print(f"{v:24s} {a['n']:6d} {a['avg']:+7.3f} {a['t']:6.2f} {a['win']*100:5.1f}% | {e['train']['avg']:+7.3f} {e['test']['avg']:+7.3f} {e['test']['n']:6d}  {'PASS' if e['passed'] else 'FAIL'}")
        if base:
            a = base["all"]; print(f"{'(live bot) ENG|none|nat|LAD':24s} {a['n']:6d} {a['avg']:+7.3f} {a['t']:6.2f} {a['win']*100:5.1f}% | {base['train']['avg']:+7.3f} {base['test']['avg']:+7.3f} {base['test']['n']:6d}  {'PASS' if base['passed'] else 'FAIL'}")
        npass = sum(1 for _, e in rows if e["passed"])
        print(f"variants that PASS on {tf}: {npass} of {len(rows)}")
    # ---- train-selected winner per TF (unbiased test number)
    print("\n--- train-selected winner: pick the best variant on the FIRST 60% only, judge it on the LAST 40% ---")
    for tf in tfs:
        best = None
        for v, e in ev[tf].items():
            if e and e["train"]["n"] >= MIN_TEST_N and (best is None or e["train"]["avg"] > best[1]["train"]["avg"]):
                best = (v, e)
        if best:
            v, e = best
            print(f"{tf:>4s}: {v:24s} train {e['train']['avg']:+.3f}R  ->  TEST {e['test']['avg']:+.3f}R over {e['test']['n']} trades (t={e['test']['t']:.2f})")
    # ---- one strategy for ALL timeframes
    print("\n--- ONE variant on ALL timeframes (ranked by the WORST timeframe's test R) ---")
    common = []
    for v in ev[tfs[0]]:
        es = [ev[tf].get(v) for tf in tfs]
        if all(es):
            common.append((min(e["test"]["avg"] for e in es), v, es))
    common.sort(key=lambda x: -x[0])
    hdr = "".join(f"{tf + ' test':>10s}" for tf in tfs)
    print(f"{'variant':24s}{hdr}   worst  all-TF verdict")
    for worst, v, es in common[:10]:
        cells = "".join(f"{e['test']['avg']:+10.3f}" for e in es)
        print(f"{v:24s}{cells}  {worst:+6.3f}  {'PASS on all TFs' if all(e['passed'] for e in es) else 'not on all TFs'}")
    n_all = sum(1 for _, _, es in common if all(e["passed"] for e in es))
    print(f"variants that PASS on every timeframe at once: {n_all} of {len(common)}")
    # ---- best variant of every strategy family (so each of your strategies gets its own verdict)
    names = {"ENG": "Candle signal (current bot)", "DON": "Donchian breakout", "PB": "EMA pullback", "MR": "z-score fade",
             "SQZ": "Bollinger squeeze", "RNG": "Range expansion", "VA": "VWAP reclaim, sessions 00/08/13", "VAd": "VWAP reclaim, UTC day",
             "VB2": "VWAP +-2 sigma reversal, sessions", "VB2d": "VWAP +-2 sigma reversal, UTC day", "VB3": "VWAP +-3 sigma reversal, sessions",
             "TL": "Two equal legs + V / inverted V", "TLS": "Two legs + V + liquidity sweep", "TLW": "Two legs + V (loose rules)",
             "SWP": "Liquidity sweep / swing failure", "DOW": "Dow-theory HH/HL continuation", "ORB": "Session opening-range breakout",
             "VPR": "Volume profile: value-area re-entry (sessions)", "VPRd": "Volume profile: value-area re-entry (day)", "VPB": "Volume profile: VAH/VAL acceptance break"}
    print("\n--- every strategy family: its best variant by worst-timeframe TEST R (net of cost) ---")
    print(f"{'family':34s} {'variant':22s}" + "".join(f"{tf:>9s}" for tf in tfs) + "   verdict")
    for g in GENS:
        cands = [(w, v, es) for w, v, es in common if v.split("|")[0] == g]
        if not cands:
            print(f"{names.get(g, g):34s} (no variant has enough trades on every timeframe)")
            continue
        w, v, es = cands[0]
        cells = "".join(f"{e['test']['avg']:+9.3f}" for e in es)
        ok = all(e["passed"] for e in es)
        print(f"{names.get(g, g):34s} {v:22s}{cells}   {'PASS on all TFs' if ok else 'FAIL'}")
    # ---- detail of the best common candidate
    if common:
        worst, v, es = common[0]
        print(f"\n--- detail of the best all-TF candidate: {v} ---")
        for tf, e in zip(tfs, es):
            tr = res[tf][v]
            by_sym = {}
            for t, g, sp, sym in tr: by_sym.setdefault(sym, []).append(g - fee / sp)
            pos = sum(1 for x in by_sym.values() if np.mean(x) > 0)
            lo = _stats([g - lo_fee / sp for _, g, sp, _ in tr])
            gr = _stats([g for _, g, _, _ in tr]); med = float(np.median([sp for _, _, sp, _ in tr]))
            mon = {}
            for t, g, sp, sym in tr: mon.setdefault(pd.Timestamp(t, unit="ms").strftime("%Y-%m"), []).append(g - fee / sp)
            mpos = sum(1 for x in mon.values() if np.mean(x) > 0)
            print(f"{tf:>4s}: n={e['all']['n']:5d} gross {gr['avg']:+.3f}R | net@{fee:.2f}% {e['all']['avg']:+.3f}R | net@{lo_fee:.2f}% {lo['avg']:+.3f}R | "
                  f"median stop {med:.2f}% | symbols positive {pos}/{len(by_sym)} | months positive {mpos}/{len(mon)}")
    print("\nRules: a variant is only worth putting into the bot when it PASSES on every timeframe (table above), is positive on most symbols and"
          "\nmonths, survives the higher fee, and the train-selected winner is also positive on the test half. Otherwise it is noise.")
    return ev


# ----------------------------------------------------------------------------- synthetic data (self-test only)
def synth(n, step_ms, seed=1, daily_drift=0.0, mom=0.0, price=100.0, vol=0.0015, micro=24, regime_days=15):
    """Random-walk candles built from a fine tick path (Brownian bridge, `micro` steps per bar) so highs/lows are
    those of a real martingale path - a zero-edge world really has zero edge.
    daily_drift: per-bar drift (fraction of vol) whose sign flips every ~15 days (planted slow trend the daily
    filter can see). mom: AR(1) coefficient on bar returns (planted momentum)."""
    rng = np.random.default_rng(seed)
    t0 = 1_700_000_000_000 // 86_400_000 * 86_400_000
    t = t0 + np.arange(n, dtype=np.int64) * step_ms
    bars_per_day = 86_400_000 // step_ms
    regime = np.where((np.arange(n) // (bars_per_day * regime_days)) % 2 == 0, 1.0, -1.0)
    eps = rng.normal(0, vol, n)
    r = np.zeros(n); prev = 0.0
    for i in range(n):
        r[i] = daily_drift * vol * regime[i] + mom * prev + eps[i]; prev = r[i]
    W = np.cumsum(rng.normal(0, vol / math.sqrt(micro), (n, micro)), axis=1)
    k = (np.arange(1, micro + 1) / micro)[None, :]
    bridge = W - k * W[:, -1:]                              # pinned to 0 at the end of the bar
    logp_open = np.concatenate([[0.0], np.cumsum(r)[:-1]])
    path = logp_open[:, None] + k * r[:, None] + bridge     # log-price at each micro step
    base = math.log(price)
    o = np.exp(base + logp_open); c = np.exp(base + path[:, -1])
    h = np.exp(base + np.maximum(path.max(axis=1), logp_open)); l = np.exp(base + np.minimum(path.min(axis=1), logp_open))
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": np.full(n, 100.0)}


def selftest(eng_path):
    eng = vb.load_engine(eng_path)
    # 1) ladder replica == the live engine, trade by trade, on random paths (incl. gaps, trailing runner, SELL side)
    rng = np.random.default_rng(5); bad = 0; cnt = 0
    for k in range(400):
        d = synth(400, 900_000, seed=100 + k, vol=0.004)
        n = len(d["c"]); side = 1 if k % 2 == 0 else -1
        e = float(d["o"][10]); r = e * float(rng.uniform(0.002, 0.02))
        sl = e - side * r
        tr = eng.open_new_trade({"side": "BUY" if side == 1 else "SELL", "price": e, "sl": sl}, symbol="BT/USD", tf_key="15m", display="BT",
                                preferred_signal_id=None, candle_states={})
        last = n - 1
        for i in range(10, last + 1):
            eng.check_open_trade(tr, {"o": d["o"][i], "h": d["h"][i], "l": d["l"][i], "c": d["c"][i]}, None)
            if tr.get("closed"): break
        if tr.get("closed"): g_eng = eng.compute_final_r(tr)
        else:
            frac = sum(w for lvl, w in zip(eng.RR_TARGETS, (eng.W1, eng.W2, eng.W3, eng.W4)) if tr["hit"].get(str(lvl)))
            banked = sum(w * lvl for lvl, w in zip(eng.RR_TARGETS, (eng.W1, eng.W2, eng.W3, eng.W4)) if tr["hit"].get(str(lvl)))
            g_eng = banked + (1 - frac) * (d["c"][last] - e) / r * side
        if side == 1: F, A, C, ee = d["h"], d["l"], d["c"], e
        else: F, A, C, ee = -d["l"], -d["h"], -d["c"], -e
        g_lab, _ = sim_ladder(ee, r, F, A, C, 10, last, pessimistic=False)
        cnt += 1
        if abs(g_lab - g_eng) > 1e-9: bad += 1; print("   mismatch", k, side, g_lab, g_eng)
    assert bad == 0, f"{bad}/{cnt} ladder mismatches"
    print(f"selftest 1: lab ladder == live engine ladder on {cnt} random trades (exact)")
    # 2) no lookahead: signals computed on a prefix equal the full-data signals inside that prefix; same for HTF alignment
    d = synth(6000, 900_000, seed=7, daily_drift=0.15, mom=0.1)
    cut = 4000; dp = {k: v[:cut] for k, v in d.items()}
    full, pre = make_signals(d, eng, 900_000), make_signals(dp, eng, 900_000)
    for g in full:
        fi, fs, fsl, ftp = full[g]; pi, ps, psl, ptp = pre[g]
        m = fi < cut - 1
        assert np.array_equal(fi[m], pi[pi < cut - 1]) and np.array_equal(fs[m], ps[pi < cut - 1]) and np.allclose(fsl[m], psl[pi < cut - 1]) and np.allclose(ftp[m], ptp[pi < cut - 1], equal_nan=True), f"lookahead in {g}"
    c_full, c_pre = Ctx(d), Ctx(dp)
    a1 = c_full.for_bars(d, 900_000); a2 = c_pre.for_bars(dp, 900_000)
    assert np.array_equal(a1[0][:cut - 1], a2[0][:cut - 1]) and np.array_equal(a1[1][:cut - 1], a2[1][:cut - 1]), "lookahead in HTF trend"
    print("selftest 2: no lookahead (signals + daily/4h trend identical on a truncated history)")
    # 3) pure random walk, zero cost: nothing may look like an edge
    res = {tf: {} for tf in ("15m",)}
    allr = []
    for sd in range(8):
        d = synth(15000, 900_000, seed=20 + sd); ctx = Ctx(d)
        for v, tr in run_symbol(d, ctx, 900_000, eng).items():
            res["15m"].setdefault(v, []).extend([(t, g, sp, f"S{sd}") for t, g, sp in tr])
    ts = []
    for v, tr in res["15m"].items():
        s = _stats([g for _, g, _, _ in tr])
        if s["n"] >= 150: ts.append((s["t"], v, s["avg"], s["n"]))
    tmax = max(x[0] for x in ts); tmin = min(x[0] for x in ts); mean_avg = float(np.mean([x[2] for x in ts]))
    print(f"selftest 3 (random walk, zero cost): {len(ts)} variants, mean avgR {mean_avg:+.3f}, max t {tmax:+.2f}, min t {tmin:+.2f} (negative = the lab is conservative, only a positive outlier would be a false edge)")
    assert abs(mean_avg) < 0.05 and tmax < 4.2, "false edge in a no-edge world"
    # 4) planted momentum + slow trend: the lab must light up (and daily filter must help for trend-following)
    d = synth(20000, 900_000, seed=33, daily_drift=0.25, mom=0.12); ctx = Ctx(d)
    r4 = run_symbol(d, ctx, 900_000, eng)
    top = max(((_stats([g for _, g, _ in tr])["t"], v) for v, tr in r4.items() if len(tr) > 80), key=lambda x: x[0])
    print(f"selftest 4 (planted trend+momentum): best variant {top[1]} t={top[0]:.2f}")
    assert top[0] > 4.0, "lab failed to detect a planted edge"
    d2 = synth(40000, 900_000, seed=34, daily_drift=0.08, mom=0.0, regime_days=70); r4b = run_symbol(d2, Ctx(d2), 900_000, eng)
    a_none = _stats([g for _, g, _ in r4b["DON|none|nat|B2"]]); a_d = _stats([g for _, g, _ in r4b["DON|D|nat|B2"]])
    print(f"           slow-trend world: DON no filter {a_none['avg']:+.3f}R (n={a_none['n']})  vs  daily-trend filter {a_d['avg']:+.3f}R (n={a_d['n']})")
    assert a_d["avg"] > a_none["avg"] + 0.05, "daily-trend filter did not help where a daily trend is planted"
    # 6) two-leg detector finds a hand-built pattern (long) and its mirror image (short); stop under the V, target = leg-2 top
    rng6 = np.random.default_rng(3)
    pts = [(100, 92, 10), (92, 120, 14), (120, 100, 14), (100, 112, 10), (112, 92, 14), (92, 98, 5), (98, 95, 3), (95, 103, 6), (103, 108, 6)]
    cc = [100.0] * 30
    for a_, b_, n_ in pts: cc += list(np.linspace(a_, b_, n_, endpoint=False))
    cc = np.array(cc) + rng6.normal(0, 0.15, len(cc)); oo = np.concatenate([[cc[0]], cc[:-1]])
    base = {"t": np.arange(len(cc), dtype=np.int64) * 900_000 + 1_700_000_000_000 // 86_400_000 * 86_400_000,
            "o": oo, "h": np.maximum(oo, cc) + 0.3, "l": np.minimum(oo, cc) - 0.3, "c": cc, "v": np.full(len(cc), 1.0)}
    r_l = ss.gen_two_leg(base, 900_000)
    assert len(r_l[0]) == 1 and r_l[1][0] == 1 and 90.5 < r_l[2][0] < 92.5 and abs(r_l[3][0] - 112.3) < 1.0, r_l
    mir = {"t": base["t"], "o": 200 - base["o"], "h": 200 - base["l"], "l": 200 - base["h"], "c": 200 - base["c"], "v": base["v"]}
    r_s = ss.gen_two_leg(mir, 900_000)
    assert len(r_s[0]) == 1 and r_s[1][0] == -1 and r_s[0][0] == r_l[0][0] and abs(r_s[3][0] - (200 - r_l[3][0])) < 1e-6, r_s
    print(f"selftest 6: two-leg detector finds the planted V pattern (long: bar {r_l[0][0]}, stop {r_l[2][0]:.1f}, target {r_l[3][0]:.1f}) and its mirror (short)")
    # 7) session-anchored VWAP resets at 00/08/13 UTC and equals a pandas reference
    dd = synth(1500, 900_000, seed=9)
    vw, sd_, pos_ = ss.anchored_vwap(dd, ss.ANCHORS_SESSIONS)
    hrs = ((dd["t"] % 86_400_000) // 3_600_000)
    sess = (dd["t"] // 86_400_000) * 3 + (hrs >= 0).astype(int) + (hrs >= 8).astype(int) + (hrs >= 13).astype(int)
    df = pd.DataFrame({"tp": (dd["h"] + dd["l"] + dd["c"]) / 3, "v": dd["v"], "s": sess}); df["pv"] = df.tp * df.v
    ref = df.groupby("s").pv.cumsum() / df.groupby("s").v.cumsum()
    assert np.allclose(vw, ref.values), "anchored VWAP mismatch"
    first = np.flatnonzero(np.diff(sess, prepend=-1) != 0)
    assert all(pos_[i] == 0 for i in first) and set((dd["t"][first] % 86_400_000) // 3_600_000) <= {0, 8, 13}
    print("selftest 7: session VWAP resets at 00:00 / 08:00 / 13:00 UTC and matches a pandas reference")
    # 8) liquidity-sweep two-leg: leg 2 pierces the earlier swing low (92) to ~90.6 and closes back above it, then V-neckline break
    pts8 = [(100, 92, 10), (92, 120, 14), (120, 100, 14), (100, 112, 10), (112, 90.6, 14), (90.6, 98, 5), (98, 95, 3), (95, 103, 6), (103, 108, 6)]
    c8 = [100.0] * 30
    for a_, b_, n_ in pts8: c8 += list(np.linspace(a_, b_, n_, endpoint=False))
    c8 = np.array(c8) + np.random.default_rng(3).normal(0, 0.15, len(c8)); o8 = np.concatenate([[c8[0]], c8[:-1]])
    b8 = {"t": np.arange(len(c8), dtype=np.int64) * 900_000 + 1_700_000_000_000 // 86_400_000 * 86_400_000,
          "o": o8, "h": np.maximum(o8, c8) + 0.3, "l": np.minimum(o8, c8) - 0.3, "c": c8, "v": np.full(len(c8), 1.0)}
    r8 = ss.gen_two_leg(b8, 900_000, min_rr=1.0, sweep=True, sr_tol=1.5)
    assert len(r8[0]) == 1 and r8[1][0] == 1 and r8[2][0] < 90.6, r8
    assert len(ss.gen_two_leg(base, 900_000, min_rr=1.0, sweep=True, sr_tol=1.5)[0]) == 0, "sweep variant fired without a sweep"
    print(f"selftest 8: sweep two-leg fires only when leg 2 grabs the liquidity under the old low (stop {r8[2][0]:.1f} sits below the sweep wick)")
    # 9) fixed-range volume profile: POC of the previous session sits where the volume is; levels never use the current session
    n9 = 200; t9 = 1_700_006_400_000 + np.arange(n9, dtype=np.int64) * 900_000      # 00:00 UTC start, 15m bars
    px = 100 + np.sin(np.arange(n9) / 9.0) * 4
    v9 = np.where(np.abs(px - 102) < 0.8, 50.0, 1.0)
    d9 = {"t": t9, "o": px, "h": px + 0.2, "l": px - 0.2, "c": px, "v": v9}
    poc9, vah9, val9 = ss.profile_levels(d9, ss.ANCHORS_DAY)
    first_day_end = int(((t9 // 86_400_000) == (t9[0] // 86_400_000)).sum())
    assert np.isnan(poc9[:first_day_end]).all() and abs(poc9[first_day_end] - 102) < 0.9 and val9[first_day_end] < poc9[first_day_end] < vah9[first_day_end]
    print(f"selftest 9: volume profile POC {poc9[first_day_end]:.2f} (planted 102), VA {val9[first_day_end]:.2f}-{vah9[first_day_end]:.2f}; no levels before a session has finished")
    # 10) sweep / swing-failure and ORB detectors on hand-made candles
    cs = np.full(60, 100.0) + np.random.default_rng(4).normal(0, 0.05, 60); os_ = np.concatenate([[cs[0]], cs[:-1]])
    ds = {"t": np.arange(60, dtype=np.int64) * 900_000 + 1_700_000_000_000 // 86_400_000 * 86_400_000, "o": os_, "h": np.maximum(os_, cs) + 0.1, "l": np.minimum(os_, cs) - 0.1, "c": cs.copy(), "v": np.full(60, 1.0)}
    ds["h"][50] = 101.0; ds["c"][50] = 99.95                      # wick above the 20-bar high, closes back inside
    rs = ss.gen_sweep(ds, 900_000)
    assert 50 in rs[0] and rs[1][list(rs[0]).index(50)] == -1, rs
    print("selftest 10: swing-failure detector flags the spike-and-reject bar (short)")
    # 5) the report runs end to end
    res5 = {tf: {} for tf in TFS}
    for tf in TFS:
        for v, tr in r4.items():
            res5[tf][v] = [(t, g, sp, "S0") for t, g, sp in tr]
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): report(res5, 0.10, TFS)
    assert "ONE variant on ALL timeframes" in buf.getvalue()
    print("selftest 5: report renders")
    print("selftest OK")


# ----------------------------------------------------------------------------- main
def load_dataset(args):
    """-> {sym: {tf: data-dict, 'ctx': Ctx}}"""
    ds = {}
    if args.csvdir:
        syms = sorted({os.path.basename(p).rsplit("_", 1)[0] for p in glob.glob(os.path.join(args.csvdir, "*_15m.csv"))})
        for s in syms:
            m = {tf: vb.load_csv(os.path.join(args.csvdir, f"{s}_{tf}.csv")) for tf in ("1m", "5m", "15m")
                 if os.path.exists(os.path.join(args.csvdir, f"{s}_{tf}.csv"))}
            ds[s] = m
    else:
        for sym in args.fetch:
            m = {}
            base = vb._base(sym)
            ctx_days = max(args.days15, args.days1h) + CTX_EXTRA_DAYS
            m["15m"] = vb.load_csv(vb.fetch_candles(sym, "15m", ctx_days, args.out, args.source))
            for tf, dd in (("5m", args.days5), ("1m", args.days1)):
                if dd > 0:
                    try:
                        m[tf] = vb.load_csv(vb.fetch_candles(sym, tf, dd, args.out, args.source))
                    except SystemExit as e:
                        print(f"[warn] {sym} {tf}: no data ({e}) - timeframe skipped for this symbol", file=sys.stderr)
            ds[base] = m
    return ds


def main():
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.path.join(HERE, "candle_engine.py"))
    ap.add_argument("--fetch", nargs="*", default=[])
    ap.add_argument("--csvdir", default="")
    ap.add_argument("--fee", type=float, default=0.10, help="round-trip fee+slippage in %% of price")
    ap.add_argument("--source", default="auto", choices=["auto"] + vb.SOURCES)
    ap.add_argument("--out", default=os.path.join(HERE, "lab_data"))
    ap.add_argument("--days1", type=int, default=DEFAULT_DAYS["1m"]); ap.add_argument("--days5", type=int, default=DEFAULT_DAYS["5m"])
    ap.add_argument("--days15", type=int, default=DEFAULT_DAYS["15m"]); ap.add_argument("--days1h", type=int, default=DEFAULT_DAYS["1h"])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.engine)
    if not a.fetch and not a.csvdir:
        ap.error("give --fetch SYMBOLS, --csvdir DIR or --selftest")
    eng = vb.load_engine(a.engine)
    ds = load_dataset(a)
    res = {tf: {} for tf in TFS}
    for sym, m in ds.items():
        ctx = Ctx(m["15m"])
        series = {}
        if "1m" in m: series["1m"] = m["1m"]
        if "5m" in m: series["5m"] = m["5m"]
        d15 = m["15m"]; keep15 = d15["t"] >= d15["t"][-1] - a.days15 * 86_400_000
        series["15m"] = {k: v[keep15] for k, v in d15.items()}
        h1 = resample(d15, 3_600_000); keep1 = h1["t"] >= h1["t"][-1] - a.days1h * 86_400_000
        series["1h"] = {k: v[keep1] for k, v in h1.items()}
        for tf, d in series.items():
            print(f"[lab] {sym} {tf}: {len(d['c'])} candles", file=sys.stderr)
            for v, tr in run_symbol(d, ctx, STEP_MS[tf], eng).items():
                res[tf].setdefault(v, []).extend([(t, g, sp, sym) for t, g, sp in tr])
    tfs = [tf for tf in TFS if any(res[tf].values())]
    report(res, a.fee, tfs)


if __name__ == "__main__":
    main()

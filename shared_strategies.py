# -*- coding: utf-8 -*-
"""
shared_strategies.py - candidate strategies for the lower timeframes (1m / 5m / 15m / 1h).

STATUS: research code used by strategy_lab.py ONLY. Nothing here is imported by the live bots. A strategy is moved
into the bots only after it PASSES the lab on real exchange data (see README: "Strategy lab").

Every detector is strictly causal: the signal returned for bar i uses candles <= i only (strategy_lab --selftest
proves this by recomputing on a truncated history). A signal means "bar i just CLOSED and the setup is complete";
the lab (like a real trader) enters at the OPEN of bar i+1.

Return format of every detector: (idx, side, sl, tp)
    idx   int array  - bar index at whose close the signal is known
    side  int array  - +1 long / -1 short
    sl    float array- protective stop price (native stop of the setup)
    tp    float array- measured target price or nan (only some setups have one)
"""
import math
import numpy as np

ANCHORS_SESSIONS = (0, 8, 13)      # UTC hours: Asia / London / New York session opens
ANCHORS_DAY = (0,)                 # plain UTC-day VWAP (the earlier lab)
HOUR_MS = 3_600_000


def _atr(d, n=14):
    pc = np.concatenate([[d["c"][0]], d["c"][:-1]])
    tr = np.maximum(d["h"] - d["l"], np.maximum(abs(d["h"] - pc), abs(d["l"] - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= n:
        out[n - 1] = tr[:n].mean()
        for i in range(n, len(tr)):
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def anchored_vwap(d, anchors=ANCHORS_SESSIONS):
    """VWAP + volume-weighted sigma that RESETS at every session anchor (UTC hours in `anchors`).
    Returns vwap, sd, bars_since_anchor."""
    t = d["t"]; n = len(t)
    day0 = (t // 86_400_000) * 86_400_000
    # id of the latest anchor at or before each bar: day*len(anchors) + number of anchors passed today - 1
    hours_into_day = (t - day0) / HOUR_MS
    passed = np.zeros(n, int)
    for a in anchors:
        passed += (hours_into_day >= a)
    # before the first anchor of the day we are still in the previous day's last session
    sess = (day0 // 86_400_000) * len(anchors) + passed
    tp = (d["h"] + d["l"] + d["c"]) / 3.0
    vwap = np.full(n, np.nan); sd = np.full(n, np.nan); pos = np.zeros(n, int)
    cv = cpv = cpv2 = 0.0; last = None; k = 0
    for i in range(n):
        if sess[i] != last:
            last = sess[i]; cv = cpv = cpv2 = 0.0; k = 0
        v = max(d["v"][i], 1e-12)
        cv += v; cpv += tp[i] * v; cpv2 += tp[i] * tp[i] * v
        m = cpv / cv
        vwap[i] = m; sd[i] = math.sqrt(max(cpv2 / cv - m * m, 0.0)); pos[i] = k; k += 1
    return vwap, sd, pos


def _warm(step_ms, hours=2.0):
    return max(2, int(hours * HOUR_MS / step_ms))


def _pack(idx, side, sl, tp):
    return (np.array(idx, int), np.array(side, int), np.array(sl, float), np.array(tp, float))


# ----------------------------------------------------------------------------- VWAP strategy A: trend side + reclaim
def gen_vwap_reclaim(d, step_ms, anchors=ANCHORS_SESSIONS, buf_atr=0.25):
    """Price above the session VWAP -> longs only; below -> shorts only. Entry after a PULLBACK THROUGH the VWAP that
    closes back on the trend side (continuation). Stop beyond the pullback extreme (3 bars) + 0.25 ATR."""
    vwap, sd, pos = anchored_vwap(d, anchors); atr = _atr(d); warm = _warm(step_ms)
    idx, side, sl, tp = [], [], [], []
    for i in range(3, len(d["c"])):
        if pos[i] < warm or math.isnan(atr[i]):
            continue
        if d["c"][i - 3] > vwap[i - 3] and d["l"][i] < vwap[i] and d["c"][i] > vwap[i] and pos[i - 3] >= 0 and pos[i] > 3:
            idx.append(i); side.append(1); sl.append(min(d["l"][i - 2:i + 1]) - buf_atr * atr[i]); tp.append(np.nan)
        elif d["c"][i - 3] < vwap[i - 3] and d["h"][i] > vwap[i] and d["c"][i] < vwap[i] and pos[i] > 3:
            idx.append(i); side.append(-1); sl.append(max(d["h"][i - 2:i + 1]) + buf_atr * atr[i]); tp.append(np.nan)
    return _pack(idx, side, sl, tp)


# ----------------------------------------------------------------------------- VWAP strategy B: +-k sigma band reversal
def gen_vwap_band(d, step_ms, k=2.0, anchors=ANCHORS_SESSIONS, buf_atr=0.25):
    """Wick through the +-k sigma band that CLOSES back inside -> trade back toward the session VWAP.
    Stop beyond the band extreme + 0.25 ATR; measured target = the VWAP at signal time."""
    vwap, sd, pos = anchored_vwap(d, anchors); atr = _atr(d); warm = _warm(step_ms)
    idx, side, sl, tp = [], [], [], []
    for i in range(len(d["c"])):
        if pos[i] < warm or math.isnan(atr[i]) or not sd[i] > 0:
            continue
        up, dn = vwap[i] + k * sd[i], vwap[i] - k * sd[i]
        if d["h"][i] >= up and d["c"][i] < up:
            idx.append(i); side.append(-1); sl.append(d["h"][i] + buf_atr * atr[i]); tp.append(vwap[i])
        elif d["l"][i] <= dn and d["c"][i] > dn:
            idx.append(i); side.append(1); sl.append(d["l"][i] - buf_atr * atr[i]); tp.append(vwap[i])
    return _pack(idx, side, sl, tp)


# ----------------------------------------------------------------------------- Two-leg (equal legs) V / inverted-V
def _zigzag_pivots_up(h, l, atr, thr):
    """Causal ATR zigzag. Returns [(bar_index, price, 'H'|'L', confirm_bar)]; a pivot only exists once price has
    reversed by thr*ATR away from it, so using it at bar i requires confirm_bar <= i."""
    n = len(h); piv = []
    direction = 0; hi = lo = None; hi_i = lo_i = 0; cur = None
    for i in range(n):
        if math.isnan(atr[i]):
            continue
        if direction == 0:
            if hi is None:
                hi, hi_i, lo, lo_i = h[i], i, l[i], i
                continue
            if h[i] > hi: hi, hi_i = h[i], i
            if l[i] < lo: lo, lo_i = l[i], i
            if hi_i < i and hi - l[i] >= thr * atr[i]:
                piv.append((hi_i, hi, "H", i)); direction = -1; cur = (l[i], i)
            elif lo_i < i and h[i] - lo >= thr * atr[i]:
                piv.append((lo_i, lo, "L", i)); direction = 1; cur = (h[i], i)
        elif direction == 1:                       # tracking a high
            if h[i] > cur[0]: cur = (h[i], i)
            elif cur[0] - l[i] >= thr * atr[i]:
                piv.append((cur[1], cur[0], "H", i)); direction = -1; cur = (l[i], i)
        else:                                      # tracking a low
            if l[i] < cur[0]: cur = (l[i], i)
            elif h[i] - cur[0] >= thr * atr[i]:
                piv.append((cur[1], cur[0], "L", i)); direction = 1; cur = (h[i], i)
    return piv


def _twoleg_long(h, l, c, atr, m=3, thr=1.5, size_tol=0.30, retr_min=0.30, sr_tol=1.0, window=12, min_rr=1.5):
    """Long version in x-space (the short version is the same code on negated prices).
    Pattern: leg1 DOWN (H0->L1), pullback up to H2 (retrace >= retr_min of leg1, H2 below H0), leg2 DOWN from H2 to L3
    with |leg2| ~= |leg1| (within size_tol) ending on SUPPORT (an earlier swing low within sr_tol ATR) and forming a V
    (L3 is the lowest bar of a 2m+1 window). Entry = break of the V top, stop under the V range, target = H2.
    Signal bar = the bar that breaks the V top (known at its close)."""
    n = len(h); out = []
    piv = _zigzag_pivots_up(h, l, atr, thr)
    pend = None; last_sig = -10**9
    pj = 0                                           # number of pivots confirmed so far (causal pointer)
    for i in range(2 * m + 1, n):
        while pj < len(piv) and piv[pj][3] <= i:
            pj += 1
        if math.isnan(atr[i]):
            continue
        # 1) resolve a pending V-break
        if pend is not None:
            v_top, stop, tgt, exp = pend
            if l[i] <= stop:
                pend = None
            elif h[i] >= v_top:
                if (tgt - v_top) >= min_rr * (v_top - stop) and c[i] > stop:
                    out.append((i, stop, tgt)); last_sig = i
                pend = None
            elif i > exp:
                pend = None
        # 2) look for a fresh V low at j = i - m (a fractal low confirmed m bars later)
        if pend is not None or i - last_sig < 2 * m:
            continue
        j = i - m
        if l[j] != l[j - m:j + m + 1].min():
            continue
        known = piv[:pj]
        hs = [p for p in known if p[2] == "H" and p[0] < j]
        if not hs:
            continue
        H2 = hs[-1]
        prev = [p for p in known if p[0] < H2[0]]
        if len(prev) < 2 or prev[-1][2] != "L" or prev[-2][2] != "H":
            continue
        L1, H0 = prev[-1], prev[-2]
        leg1 = H0[1] - L1[1]; leg2 = H2[1] - l[j]
        if leg1 <= 0 or leg2 <= 0 or abs(leg2 / leg1 - 1.0) > size_tol:
            continue
        retr = (H2[1] - L1[1]) / leg1
        if not (retr_min <= retr < 1.0):
            continue
        a = atr[j] if not math.isnan(atr[j]) else atr[i]
        sup = [p[1] for p in known if p[2] == "L" and p[0] < H2[0]]
        if not sup or min(abs(l[j] - s_) for s_ in sup) > sr_tol * a:
            continue
        rng_lo = float(l[j - m:j + m + 1].min())
        v_top = float(h[j - m:j + m + 1].max())
        if h[j + 1:i + 1].size and h[j + 1:i + 1].max() >= v_top:
            continue                                  # V top already taken out before we could place the order
        pend = (v_top, rng_lo - 0.1 * a, H2[1], i + window)
    return out


def gen_two_leg(d, step_ms, m=3, thr=1.5):
    """Both directions. Returns (idx, side, sl, tp) with tp = top (long) / bottom (short) of leg 2."""
    atr = _atr(d)
    L = _twoleg_long(d["h"], d["l"], d["c"], atr, m=m, thr=thr)
    S = _twoleg_long(-d["l"], -d["h"], -d["c"], atr, m=m, thr=thr)
    idx, side, sl, tp = [], [], [], []
    for i, stop, tgt in L: idx.append(i); side.append(1); sl.append(stop); tp.append(tgt)
    for i, stop, tgt in S: idx.append(i); side.append(-1); sl.append(-stop); tp.append(-tgt)
    o = np.argsort(idx, kind="stable") if idx else []
    return _pack([idx[k] for k in o], [side[k] for k in o], [sl[k] for k in o], [tp[k] for k in o])

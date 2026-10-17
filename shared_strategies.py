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


def _twoleg_long(h, l, c, atr, m=3, thr=1.5, size_tol=0.30, retr_min=0.30, sr_tol=1.0, window=12, min_rr=1.5, sweep=False, need_support=True):
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
        if need_support:
            if not sup or min(abs(l[j] - s_) for s_ in sup) > sr_tol * a:
                continue
        if sweep:
            # liquidity grab: leg 2 pierces an earlier swing low (the stops resting under it) and price closes back above it
            near = [s_ for s_ in sup if abs(l[j] - s_) <= sr_tol * a]
            if not near:
                continue
            lvl = min(near, key=lambda s_: abs(l[j] - s_))
            if not (l[j] < lvl - 0.05 * a and max(c[j:j + m + 1]) > lvl):
                continue
        rng_lo = float(l[j - m:j + m + 1].min())
        v_top = float(h[j - m:j + m + 1].max())
        if h[j + 1:i + 1].size and h[j + 1:i + 1].max() >= v_top:
            continue                                  # V top already taken out before we could place the order
        pend = (v_top, rng_lo - 0.1 * a, H2[1], i + window)
    return out


def gen_two_leg(d, step_ms, m=3, thr=1.5, **kw):
    """Both directions. Returns (idx, side, sl, tp) with tp = top (long) / bottom (short) of leg 2.
    kw: size_tol, retr_min, sr_tol, sweep (require a liquidity sweep of an earlier swing), need_support."""
    atr = _atr(d)
    L = _twoleg_long(d["h"], d["l"], d["c"], atr, m=m, thr=thr, **kw)
    S = _twoleg_long(-d["l"], -d["h"], -d["c"], atr, m=m, thr=thr, **kw)
    idx, side, sl, tp = [], [], [], []
    for i, stop, tgt in L: idx.append(i); side.append(1); sl.append(stop); tp.append(tgt)
    for i, stop, tgt in S: idx.append(i); side.append(-1); sl.append(-stop); tp.append(-tgt)
    o = np.argsort(idx, kind="stable") if idx else []
    return _pack([idx[k] for k in o], [side[k] for k in o], [sl[k] for k in o], [tp[k] for k in o])


# ============================================================================= round 2: sessions, volume profile, Dow, sweeps
def session_ids(d, anchors=ANCHORS_SESSIONS):
    """Integer id of the trading session each bar belongs to (a new id starts at every anchor hour, UTC)."""
    t = d["t"]
    day0 = (t // 86_400_000) * 86_400_000
    hours = (t - day0) / HOUR_MS
    passed = np.zeros(len(t), int)
    for a in anchors:
        passed += (hours >= a)
    return (day0 // 86_400_000) * len(anchors) + passed


def hour_of_day(d):
    return ((d["t"] % 86_400_000) // HOUR_MS).astype(int)


def session_mask(d, windows=((8, 12), (13, 17))):
    """True for bars inside the first hours of the London / New York sessions (UTC) - the liquid part of the day."""
    h = hour_of_day(d)
    m = np.zeros(len(h), bool)
    for a, b in windows:
        m |= (h >= a) & (h < b)
    return m


def volume_spike_mask(d, n=20, k=1.5):
    v = np.asarray(d["v"], float)
    avg = np.concatenate([[np.nan], np.convolve(v, np.ones(n) / n, mode="full")[:len(v) - 1]])
    avg[:n] = np.nan
    return v > k * avg


def profile_levels(d, anchors=ANCHORS_SESSIONS, bins=48, va_frac=0.70):
    """FIXED-RANGE volume profile of the PREVIOUS completed session, projected onto the current session
    (levels do not move while the session runs - no lookahead). Returns poc, vah, val arrays (nan = none yet).
    Volume of every bar is spread uniformly over the price bins its high-low range touches."""
    sess = session_ids(d, anchors); n = len(sess)
    poc = np.full(n, np.nan); vah = np.full(n, np.nan); val = np.full(n, np.nan)
    starts = np.flatnonzero(np.diff(sess, prepend=sess[0] - 1) != 0)
    ends = np.append(starts[1:], n)
    prev = None
    for s, e in zip(starts, ends):
        if prev is not None:
            poc[s:e], vah[s:e], val[s:e] = prev
        lo, hi = float(d["l"][s:e].min()), float(d["h"][s:e].max())
        if hi > lo and e - s >= 3:
            w = (hi - lo) / bins; prof = np.zeros(bins)
            for k in range(s, e):
                b0 = min(bins - 1, int((d["l"][k] - lo) / w)); b1 = min(bins - 1, int((d["h"][k] - lo) / w))
                prof[b0:b1 + 1] += max(d["v"][k], 1e-12) / (b1 - b0 + 1)
            p = int(prof.argmax()); tot = prof.sum(); acc = prof[p]; a, b = p, p
            while acc < va_frac * tot and (a > 0 or b < bins - 1):
                up = prof[b + 1] if b < bins - 1 else -1.0
                dn = prof[a - 1] if a > 0 else -1.0
                if up >= dn: b += 1; acc += prof[b]
                else: a -= 1; acc += prof[a]
            prev = (lo + (p + 0.5) * w, lo + (b + 1) * w, lo + a * w)
        else:
            prev = None
    return poc, vah, val


def gen_vp_reenter(d, step_ms, anchors=ANCHORS_SESSIONS, buf_atr=0.25):
    """'Value-area re-entry': the previous session's value area (70% of its volume) is the fair range. Price closed
    OUTSIDE it and now closes back INSIDE -> trade back toward the POC. Stop beyond the excursion extreme."""
    poc, vah, val = profile_levels(d, anchors); atr = _atr(d)
    idx, side, sl, tp = [], [], [], []
    for i in range(2, len(d["c"])):
        if math.isnan(vah[i]) or math.isnan(atr[i]):
            continue
        if d["c"][i - 1] > vah[i] and val[i] <= d["c"][i] <= vah[i]:
            idx.append(i); side.append(-1); sl.append(max(d["h"][i - 2:i + 1]) + buf_atr * atr[i]); tp.append(poc[i])
        elif d["c"][i - 1] < val[i] and val[i] <= d["c"][i] <= vah[i]:
            idx.append(i); side.append(1); sl.append(min(d["l"][i - 2:i + 1]) - buf_atr * atr[i]); tp.append(poc[i])
    return _pack(idx, side, sl, tp)


def gen_vp_break(d, step_ms, anchors=ANCHORS_SESSIONS, buf_atr=0.25):
    """Acceptance outside the previous value area: two closes beyond VAH / VAL with above-average volume ->
    continuation. Stop back inside the value area edge minus a buffer."""
    poc, vah, val = profile_levels(d, anchors); atr = _atr(d); spike = volume_spike_mask(d, 20, 1.2)
    idx, side, sl, tp = [], [], [], []
    for i in range(2, len(d["c"])):
        if math.isnan(vah[i]) or math.isnan(atr[i]) or not spike[i]:
            continue
        if d["c"][i] > vah[i] and d["c"][i - 1] > vah[i] and d["c"][i - 2] <= vah[i]:
            idx.append(i); side.append(1); sl.append(vah[i] - buf_atr * atr[i]); tp.append(np.nan)
        elif d["c"][i] < val[i] and d["c"][i - 1] < val[i] and d["c"][i - 2] >= val[i]:
            idx.append(i); side.append(-1); sl.append(val[i] + buf_atr * atr[i]); tp.append(np.nan)
    return _pack(idx, side, sl, tp)


def gen_orb(d, step_ms, anchors=ANCHORS_SESSIONS, minutes=60, min_range_atr=0.6):
    """Session opening-range breakout: range of the first `minutes` after each session open; first close beyond it
    (after the window) is the signal; stop = the opposite side of the range."""
    sess = session_ids(d, anchors); atr = _atr(d); n = len(sess)
    nb = max(1, int(minutes * 60_000 / step_ms))
    idx, side, sl, tp = [], [], [], []
    starts = np.flatnonzero(np.diff(sess, prepend=sess[0] - 1) != 0); ends = np.append(starts[1:], n)
    for s, e in zip(starts, ends):
        if e - s <= nb + 1:
            continue
        hi, lo = float(d["h"][s:s + nb].max()), float(d["l"][s:s + nb].min())
        a = atr[s + nb - 1]
        if math.isnan(a) or hi - lo < min_range_atr * a:
            continue
        for i in range(s + nb, e):
            if d["c"][i] > hi:
                idx.append(i); side.append(1); sl.append(lo); tp.append(np.nan); break
            if d["c"][i] < lo:
                idx.append(i); side.append(-1); sl.append(hi); tp.append(np.nan); break
    return _pack(idx, side, sl, tp)


def gen_sweep(d, step_ms, n=20, buf_atr=0.25):
    """Liquidity sweep / swing-failure: a bar wicks beyond the last n-bar extreme (stops resting there are taken) and
    CLOSES back inside -> reversal. Stop beyond the wick, measured target = middle of the n-bar range."""
    atr = _atr(d); h, l, c = d["h"], d["l"], d["c"]
    hh = np.concatenate([[np.nan], pd_roll(h, n, np.max)[:-1]]); ll = np.concatenate([[np.nan], pd_roll(l, n, np.min)[:-1]])
    idx, side, sl, tp = [], [], [], []
    for i in range(n + 1, len(c)):
        if math.isnan(atr[i]) or math.isnan(hh[i]):
            continue
        if h[i] > hh[i] and c[i] < hh[i]:
            idx.append(i); side.append(-1); sl.append(h[i] + buf_atr * atr[i]); tp.append((hh[i] + ll[i]) / 2)
        elif l[i] < ll[i] and c[i] > ll[i]:
            idx.append(i); side.append(1); sl.append(l[i] - buf_atr * atr[i]); tp.append((hh[i] + ll[i]) / 2)
    return _pack(idx, side, sl, tp)


def pd_roll(a, n, fn):
    out = np.full(len(a), np.nan)
    for i in range(n - 1, len(a)):
        out[i] = fn(a[i - n + 1:i + 1])
    return out


def gen_dow(d, step_ms, thr=2.0):
    """Dow-theory continuation: uptrend = last two confirmed swing highs AND lows rising (HH + HL). Entry when a close
    breaks the last swing high after the higher-low (trend resumes); stop under the higher low. Mirror for downtrends."""
    atr = _atr(d); h, l, c = d["h"], d["l"], d["c"]
    piv = _zigzag_pivots_up(h, l, atr, thr)
    idx, side, sl, tp = [], [], [], []
    pj = 0; used = set()
    for i in range(len(c)):
        while pj < len(piv) and piv[pj][3] <= i:
            pj += 1
        if pj < 4 or math.isnan(atr[i]):
            continue
        known = piv[:pj]
        Hs = [p for p in known if p[2] == "H"][-2:]; Ls = [p for p in known if p[2] == "L"][-2:]
        if len(Hs) < 2 or len(Ls) < 2:
            continue
        last = known[-1]
        if Hs[1][1] > Hs[0][1] and Ls[1][1] > Ls[0][1] and last[2] == "L" and Ls[1][0] == last[0]:
            if c[i] > Hs[1][1] and ("U", Hs[1][0]) not in used:
                used.add(("U", Hs[1][0])); idx.append(i); side.append(1); sl.append(Ls[1][1] - 0.1 * atr[i]); tp.append(np.nan)
        elif Hs[1][1] < Hs[0][1] and Ls[1][1] < Ls[0][1] and last[2] == "H" and Hs[1][0] == last[0]:
            if c[i] < Ls[1][1] and ("D", Ls[1][0]) not in used:
                used.add(("D", Ls[1][0])); idx.append(i); side.append(-1); sl.append(Hs[1][1] + 0.1 * atr[i]); tp.append(np.nan)
    return _pack(idx, side, sl, tp)


# ============================================================================= round 3: two-leg v2 (user spec), SMC, equal-lows sweep
def _neg(d):
    """Mirror image of a candle dict (short side = the same long code on negated prices)."""
    return {"t": d["t"], "o": -d["o"], "h": -d["l"], "l": -d["h"], "c": -d["c"], "v": d["v"]}


def _both(fn, d, **kw):
    """Run a long-only detector `fn(o,h,l,c,atr,**kw) -> [(i, stop, tp)]` on d and on its mirror; merge to (idx, side, sl, tp)."""
    atr = _atr(d); dn = _neg(d)
    L = fn(d["o"], d["h"], d["l"], d["c"], atr, **kw)
    S = fn(dn["o"], dn["h"], dn["l"], dn["c"], atr, **kw)
    rows = [(i, 1, s, t) for i, s, t in L] + [(i, -1, -s, -t) for i, s, t in S]
    rows.sort(key=lambda r: r[0])
    return _pack([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows])


def _twoleg2_long(o, h, l, c, atr, m=3, thr=1.2, size_tol=0.6, retr_min=0.25, sr_tol=1.5, window=15, retest_window=12,
                  min_rr=1.2, sweep=True, need_support=True, entry="retest", nl_mult=2):
    """TWO-LEG + V, exactly as specified (long version; short = mirror).
      leg 1 DOWN (H0->L1), pullback to H2, leg 2 DOWN (H2->V low) of a similar size;
      the V low must sit on a FLOOR zone (an earlier swing low) and - if sweep=True - must PIERCE it (liquidity under the
      old low is taken) while price closes back above the old low;
      NECKLINE = the highest high of the last `nl_mult*m` bars before the V low (the top of the V);
      entry="retest": after a CLOSE above the neckline wait for price to come BACK to the neckline and bounce
                      (low <= neckline+0.1ATR, bullish close above it) -> signal at that close;
      entry="break" : signal on the neckline-break close itself (for comparison);
      stop = under the V low / sweep wick - 0.1 ATR;  target = H2 (the top where leg 2 started).
    Strictly causal: pivots are used only after their confirm bar; the fractal V low j=i-m is known at bar i."""
    n = len(h); out = []
    piv = _zigzag_pivots_up(h, l, atr, thr)
    pend = None; last_sig = -10**9; pj = 0
    for i in range(2 * m + 1, n):
        while pj < len(piv) and piv[pj][3] <= i:
            pj += 1
        if math.isnan(atr[i]):
            continue
        if pend is not None:
            neck, stop, tgt, phase, exp = pend
            if l[i] <= stop:
                pend = None
            elif phase == 0:
                if c[i] > neck:
                    if entry == "break":
                        if (tgt - c[i]) >= min_rr * (c[i] - stop):
                            out.append((i, stop, tgt)); last_sig = i
                        pend = None
                    else:
                        pend = (neck, stop, tgt, 1, i + retest_window)
                elif i > exp:
                    pend = None
            else:
                if h[i] >= tgt or i > exp or c[i] < neck - 0.5 * atr[i]:
                    pend = None
                elif l[i] <= neck + 0.1 * atr[i] and c[i] > neck and c[i] > o[i]:
                    if (tgt - c[i]) >= min_rr * (c[i] - stop):
                        out.append((i, stop, tgt)); last_sig = i
                    pend = None
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
        if not (retr_min <= (H2[1] - L1[1]) / leg1 < 1.0):
            continue
        a = atr[j] if not math.isnan(atr[j]) else atr[i]
        sup = [p[1] for p in known if p[2] == "L" and p[0] < H2[0]]
        near = [s_ for s_ in sup if abs(l[j] - s_) <= sr_tol * a or l[j] < s_]
        if need_support or sweep:
            if not [s_ for s_ in sup if abs(l[j] - s_) <= sr_tol * a]:
                continue
        if sweep:
            lvl = min([s_ for s_ in sup if abs(l[j] - s_) <= sr_tol * a], key=lambda s_: abs(l[j] - s_))
            if not (l[j] < lvl - 0.02 * a and max(c[j:j + m + 1]) > lvl):
                continue
        neck = float(h[max(j - nl_mult * m, 0):j + 1].max())
        stop = float(l[j - m:j + m + 1].min()) - 0.1 * a
        tgt = H2[1]
        if neck >= tgt or l[j + 1:i + 1].size and l[j + 1:i + 1].min() <= stop:
            continue
        broke = bool(c[j + 1:i + 1].size and (c[j + 1:i + 1] > neck).any())
        if broke:
            if entry == "break" or h[j + 1:i + 1].max() >= tgt:
                continue
            pend = (neck, stop, tgt, 1, i + retest_window)
        else:
            pend = (neck, stop, tgt, 0, i + window)
    return out


def gen_two_leg2(d, step_ms, **kw):
    """Two-leg + V v2 (both directions). kw: m, thr, size_tol, retr_min, sr_tol, sweep, need_support, entry, min_rr, window, retest_window."""
    return _both(_twoleg2_long, d, **kw)


def _smc_long(o, h, l, c, atr, m=3, kind="ob", mode="any", zone_window=30, fvg_impulse=1.0):
    """Smart-money structure (long version; short = mirror).
    Swings = m-bar fractals (known m bars later). BOS = close beyond the last swing high while the structure was already
    up; CHoCH = the same close while it was down (first break against the trend).
    kind="ob" : after the chosen break (mode = "bos" | "choch" | "any") the ORDER BLOCK = last bearish candle of the leg that
                made the break; signal when price comes back into the block and closes bullish above its low.
    kind="fvg": bullish fair-value gap (low[i] > high[i-2]) created by an impulse candle >= fvg_impulse ATR while the
                structure is up; signal when price retraces into the gap and closes bullish above the gap low.
    stop = under the block / gap - buffer; target = the high of the impulse leg (OB) or none (FVG)."""
    n = len(h); out = []
    sh = None; sw = None; trend = 0; zones = []
    for i in range(2 * m + 1, n):
        if math.isnan(atr[i]):
            continue
        j = i - m
        if h[j] == h[j - m:j + m + 1].max() and (sh is None or j > sh[0]):
            sh = [j, h[j], False]
        if l[j] == l[j - m:j + m + 1].min() and (sw is None or j > sw[0]):
            sw = [j, l[j], False]
        # 1) resolve open zones (zones created on an earlier bar only)
        keep = []
        for z in zones:
            zlo, zhi, stop, tgt, born, exp = z
            if c[i] < zlo or i > exp:
                continue
            if l[i] <= zhi and c[i] > o[i] and c[i] >= zlo:
                if not math.isnan(tgt) and tgt <= c[i]:
                    tgt = float("nan")
                out.append((i, stop, tgt))
                continue
            keep.append(z)
        zones = keep[-4:]
        # 2) structure
        if sh is not None and not sh[2] and c[i] > sh[1]:
            was = trend; sh[2] = True; trend = 1
            kb = "bos" if was == 1 else "choch"
            if kind == "ob" and (mode == "any" or mode == kb) and sw is not None and sw[0] < i:
                ob = None
                for k in range(i - 1, max(sw[0] - 3, 0) - 1, -1):   # last bearish candle at / just before the swing low
                    if c[k] < o[k]:
                        ob = k; break
                if ob is not None:
                    zones.append((float(l[ob]), float(h[ob]), float(l[ob]) - 0.1 * atr[i], float(h[sw[0]:i + 1].max()), i, i + zone_window))
        if sw is not None and not sw[2] and c[i] < sw[1]:
            sw[2] = True; trend = -1
        # 3) fair-value gap
        if kind == "fvg" and i >= 2 and (trend == 1 or mode == "any") and l[i] > h[i - 2] and (c[i - 1] - o[i - 1]) >= fvg_impulse * atr[i]:
            zones.append((float(h[i - 2]), float(l[i]), float(h[i - 2]) - 0.25 * atr[i], float("nan"), i, i + zone_window))
    return out


def gen_smc(d, step_ms, **kw):
    return _both(_smc_long, d, **kw)


def _eql_long(o, h, l, c, atr, m=3, tol=0.15, life=150, tp_look=30):
    """Equal-lows liquidity pool (long version): two confirmed swing lows within `tol` ATR of each other = a pool of stops.
    A later bar that wicks UNDER the pool and CLOSES back above it takes the liquidity -> long. Stop under the wick,
    target = highest high of the last `tp_look` bars."""
    n = len(h); out = []; lows = []; levels = []
    for i in range(2 * m + 1, n):
        if math.isnan(atr[i]):
            continue
        j = i - m
        if l[j] == l[j - m:j + m + 1].min():
            for q in lows:
                if j - q[0] <= life and abs(q[1] - l[j]) <= tol * atr[i] and q[0] != j:
                    levels.append([min(q[1], l[j]), i + life, False]); break
            lows.append((j, float(l[j])))
            lows = lows[-30:]
        keep = []; fired = None
        for lv in levels:
            if lv[1] < i:
                continue
            if l[i] < lv[0] - 0.02 * atr[i] and c[i] > lv[0]:
                fired = True                              # one signal per bar; every pool this bar swept is consumed
                continue
            keep.append(lv)
        if fired:
            tp = float(h[max(0, i - tp_look):i].max())
            out.append((i, float(l[i]) - 0.1 * atr[i], tp if tp > c[i] else float("nan")))
        levels = keep[-8:]
    return out


def gen_eql(d, step_ms, **kw):
    return _both(_eql_long, d, **kw)


# ============================================================================= round 4: hidden-divergence + inside-bar (user strategy), simplified two-leg
def _rsi(c, n=14):
    """Wilder RSI (causal). RSI of the negated series == 100 - RSI, so the short side can reuse the long code."""
    c = np.asarray(c, float); out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c); g = np.where(d > 0, d, 0.0); ls = np.where(d < 0, -d, 0.0)
    ag = g[:n].mean(); al = ls[:n].mean()
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for k in range(n, len(d)):
        ag = (ag * (n - 1) + g[k]) / n; al = (al * (n - 1) + ls[k]) / n
        out[k + 1] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def _hdv_long(o, h, l, c, atr, thr=1.5, rsi_n=14, lookback=80, inside=True, brk_wait=4, ib_wait=10, buf_atr=0.1, stop_mode="swing"):
    """HIDDEN BULLISH DIVERGENCE + INSIDE-BAR confirmation (long version; short = mirror).
      1) swing low = ATR-zigzag low (price reversed by >= thr ATR after it) - the 'bottom candle'; known only after that reversal;
      2) hidden bullish divergence: the new swing low is HIGHER than the previous swing low (within `lookback` bars) while RSI at
         it is LOWER than RSI at the previous one (trend continuation: price holds a higher low, momentum resets);
      3) confirmation: within `ib_wait` bars an INSIDE BAR (high <= prev high and low >= prev low, low above the stop) forms;
         then a CLOSE above the inside bar's high within `brk_wait` bars gives the signal (inside=False: a bullish bar that
         closes above the previous bar's high is enough);
      4) stop = under the swing-low candle - buf_atr*ATR (stop_mode="swing", the default); stop_mode="ib" puts it under the INSIDE
         BAR's low instead (much tighter), "mid" halfway between the two; exits are decided by the lab (the bot's 4-target ladder).
    Causal: a swing low is only used from its zigzag confirm bar on; the bars between it and the confirm bar are replayed."""
    n = len(h); out = []
    rsi = _rsi(c, rsi_n)
    piv = _zigzag_pivots_up(h, l, atr, thr)
    prev_low = None                      # (index, price, rsi) of the previous swing low
    st = None                            # active setup: [stop, phase, trigger, deadline]
    def advance(k, live=True):
        nonlocal st
        stop, phase, trig, dl = st[:4]
        if l[k] <= stop or k > dl:
            st = None; return
        if phase == 0:
            if inside:
                if h[k] <= h[k - 1] and l[k] >= l[k - 1] and l[k] > stop:
                    st[1] = 1; st[2] = float(h[k]); st[3] = k + brk_wait
                    ibs = float(l[k]) - buf_atr * atr[k]
                    if stop_mode == "ib": st[0] = max(stop, ibs)
                    elif stop_mode == "mid": st[0] = 0.5 * (stop + max(stop, ibs))
            elif c[k] > o[k] and c[k] > h[k - 1]:
                if live:
                    out.append((k, stop, float("nan")))
                st = None                  # a trigger that fired BEFORE the setup was known (replay) is dropped, never back-dated
        elif c[k] > trig:
            if live:
                out.append((k, st[0], float("nan")))
            st = None
    pj = 0; last_replayed = -1
    for i in range(1, n):
        if math.isnan(atr[i]):
            continue
        while pj < len(piv) and piv[pj][3] <= i:
            pidx, pprice, kind, conf = piv[pj]; pj += 1
            if kind != "L" or math.isnan(rsi[pidx]):
                continue
            if prev_low is not None and pidx - prev_low[0] <= lookback and pprice > prev_low[1] and rsi[pidx] < prev_low[2]:
                st = [float(pprice) - buf_atr * atr[i], 0, 0.0, i + ib_wait]
                for k in range(pidx + 1, i + 1):
                    if st is None:
                        break
                    advance(k, live=(k == i))
                last_replayed = i
            prev_low = (pidx, float(pprice), float(rsi[pidx]))
        if st is not None and i > last_replayed:
            advance(i)
    return out


def gen_hdv(d, step_ms, **kw):
    return _both(_hdv_long, d, **kw)

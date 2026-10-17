# -*- coding: utf-8 -*-
"""
lowtf_strategies.py — استراتژی‌های تایم‌فریم پایین (دو فایل متنیِ کاربر) به‌صورت آشکارساز علّی (causal).

هر آشکارساز دقیقاً مثل shared_strategies (idx, side, sl, tp) برمی‌گرداند: سیگنال روی «بسته‌شدن کندل idx» معلوم است و
ورود (در آزمایشگاه) روی open کندل بعد است. هیچ نگاه‌به‌آینده‌ای نیست (test_lowtf.py با بریدن تاریخچه این را اثبات می‌کند).

⚠️ وضعیت: «کد آماده، نتیجه‌ی بک‌تست نامعلوم». هیچ‌کدام از این استراتژی‌ها تا وقتی strategy_lab_lowtf2 روی داده‌ی واقعی
PASS نگیرد وارد ربات نمی‌شود (data/lowtf_verdicts.json؛ بدون آن همه خاموش و قفل‌اند).

یک پیاده‌سازی برای هر دو مصرف‌کننده:
  • آزمایشگاه (strategy_lab.py) سری‌ی کندلی بلند را می‌دهد و HTF را از خودِ همان سری می‌سازد (build_htf)؛
  • ربات زنده کندل‌های پایه + کندل‌های HTF (۱۵د/۱س/۴س/روزانه) را می‌دهد (htf_from_klines).
  فیلترها (D/D4/S/V/M) هم یک‌جا (filter_masks) تا آزمایشگاه و ربات هرگز drift نکنند.

نکته‌ی داده: «حجم تیک» در دسترس نیست؛ به‌جایش حجم کندل (volume) استفاده می‌شود (در گزارش هم همین قید می‌آید).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import shared_strategies as ss

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
HTF_PERIODS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
LIVE_FILTERS = ("none", "D", "D4", "S", "V", "DS", "M")
STOP_K = {"nat": 0.0, "A15": 1.5, "A2": 2.0, "A3": 3.0}          # حداقل فاصله‌ی حد ضرر بر حسب ATR
MIN_RISK_PCT = 0.20


# ----------------------------------------------------------------------------- ابزارها
def _ema(a, n):
    return pd.Series(a).ewm(span=n, adjust=False).mean().values


def _sma(a, n):
    return pd.Series(a).rolling(n).mean().values


def _std(a, n):
    return pd.Series(a).rolling(n).std(ddof=0).values


def _rsi(c, n=14):
    s = pd.Series(c)
    dl = s.diff()
    g = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ls = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    r = np.array((100 - 100 / (1 + g / ls.replace(0, np.nan))).values, dtype=float)
    r[:n] = np.nan
    return r


def _adx(x, n=14):
    h, l, c = x["h"], x["l"], x["c"]
    up = np.diff(h, prepend=h[0]); dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    a = 1.0 / n
    atr_ = pd.Series(tr).ewm(alpha=a, adjust=False).mean().values
    pdi = 100 * pd.Series(pdm).ewm(alpha=a, adjust=False).mean().values / np.where(atr_ > 0, atr_, np.nan)
    mdi = 100 * pd.Series(mdm).ewm(alpha=a, adjust=False).mean().values / np.where(atr_ > 0, atr_, np.nan)
    dx = 100 * abs(pdi - mdi) / np.where((pdi + mdi) > 0, pdi + mdi, np.nan)
    out = np.array(pd.Series(dx).ewm(alpha=a, adjust=False).mean().values, dtype=float)
    out[:3 * n] = np.nan
    return out


def _shift(a, k=1, fill=np.nan):
    a = np.asarray(a)
    out = np.full(len(a), fill, dtype=float if a.dtype != bool else object)
    if k > 0:
        out[k:] = a[:-k]
    return out


def _prev(a, k=1):
    return pd.Series(a).shift(k).values


def pivots(a, k, hi=True):
    """فراکتال تأییدشده: (قیمت آخرین پیوتِ معلوم، اندیس آن، پرچم پیوت‌های تازه‌تأییدشده).
    پیوت j فقط در j+k معلوم می‌شود (به k کندل بعد نیاز دارد) → کاملاً علّی."""
    a = np.asarray(a, float); n = len(a)
    ext = pd.Series(a).rolling(2 * k + 1, center=True).max().values if hi else pd.Series(a).rolling(2 * k + 1, center=True).min().values
    flag = (a == ext)
    flag = np.nan_to_num(flag, nan=False).astype(bool)
    conf = np.zeros(n, bool)
    if n > k:
        conf[k:] = flag[:n - k]
    idx = np.where(conf, np.arange(n) - k, -1)
    last = np.maximum.accumulate(idx)
    price = np.where(last >= 0, a[np.clip(last, 0, None)], np.nan)
    return price, last, flag


def resample_bars(d, period_ms):
    """کندل‌های کامل HTF از روی سری پایه (کندل در حال تشکیل حذف می‌شود)."""
    b = d["t"] // period_ms
    first = np.flatnonzero(np.diff(b, prepend=b[0] - 1) != 0)
    ends = np.append(first[1:], len(b))
    t, o, h, l, c, v, cnt = [], [], [], [], [], [], []
    for s, e in zip(first, ends):
        t.append(b[s] * period_ms); o.append(d["o"][s]); h.append(d["h"][s:e].max()); l.append(d["l"][s:e].min())
        c.append(d["c"][e - 1]); v.append(d["v"][s:e].sum()); cnt.append(e - s)
    cnt = np.array(cnt)
    if len(cnt) == 0:
        return None
    full = cnt.max()
    ok = cnt >= max(1, int(full * 0.9))
    ok[-1] = ok[-1] and cnt[-1] >= full
    sel = np.flatnonzero(ok)
    f = lambda a: np.array(a, float)[sel]
    return {"t": np.array(t, np.int64)[sel], "o": f(o), "h": f(h), "l": f(l), "c": f(c), "v": f(v)}


def _htf_pack(b, period_ms):
    c = b["c"]
    n = len(c)
    e20, e50, e200 = _ema(c, 20), _ema(c, 50), _ema(c, 200)
    ar = np.arange(n)
    return {"t": b["t"], "close_ms": b["t"] + period_ms, "o": b["o"], "h": b["h"], "l": b["l"], "c": c,
            "e20": e20, "e50": e50, "e200": e200, "ok20": ar >= 20, "ok50": ar >= 60, "ok200": ar >= 200, "n": n}


def build_htf(d, step_ms):
    """HTF از روی همان سری پایه (آزمایشگاه). فقط دوره‌هایی که بزرگ‌تر از تایم‌فریم پایه‌اند."""
    out = {}
    for key, p in HTF_PERIODS.items():
        if p > step_ms:
            b = resample_bars(d, p)
            if b is not None and len(b["c"]) > 5:
                out[key] = _htf_pack(b, p)
    return out


def htf_from_klines(by_period: Dict[int, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """ربات زنده: {ثانیه: کندل‌های بسته‌شده (open_time, o,h,l,c,v)} → همان ساختار build_htf."""
    out = {}
    for key, p in HTF_PERIODS.items():
        ks = by_period.get(p // 1000)
        if not ks or len(ks) < 30:
            continue
        b = {"t": np.array([k["open_time"] for k in ks], np.int64), "o": np.array([k["o"] for k in ks], float),
             "h": np.array([k["h"] for k in ks], float), "l": np.array([k["l"] for k in ks], float),
             "c": np.array([k["c"] for k in ks], float), "v": np.array([(k.get("v") or 0.0) for k in ks], float)}
        out[key] = _htf_pack(b, p)
    return out


class Ctx:
    """زمینه‌ی یک سمت (long در فضای آینه‌ای). sgn=+1 خرید، -1 فروش (قیمت‌ها منفی شده‌اند)."""

    def __init__(self, x, d, sgn, step_ms, htf):
        self.x, self.sgn, self.step_ms, self.htf = x, sgn, step_ms, htf or {}
        self.n = len(x["c"]); self.t = d["t"]; self.v = np.asarray(d["v"], float)
        self.close_ms = d["t"] + step_ms
        self.atr = ss._atr(x)
        self.hours = ((d["t"] // 3_600_000) % 24).astype(int)
        self._al: Dict[str, np.ndarray] = {}

    def aligned(self, key):
        if key not in self._al:
            h = self.htf.get(key)
            self._al[key] = (np.searchsorted(h["close_ms"], self.close_ms, side="right") - 1) if h else np.full(self.n, -1)
        return self._al[key]

    def has(self, key):
        return key in self.htf

    def align_arr(self, key, arr, fill=np.nan):
        j = self.aligned(key); out = np.full(self.n, fill, dtype=float)
        ok = j >= 0
        out[ok] = np.asarray(arr, float)[j[ok]]
        return out

    def bias(self, key, ema="e200"):
        """قیمتِ HTF (در سمت معامله) بالای EMA است؟ HTF موجود نباشد → False (بدون سیگنال، fail-closed)."""
        h = self.htf.get(key)
        if h is None:
            return np.zeros(self.n, bool)
        j = self.aligned(key); out = np.zeros(self.n, bool); ok = j >= 0
        jj = j[ok]
        out[ok] = (self.sgn * (h["c"][jj] - h[ema][jj]) > 0) & h["ok" + ema[1:]][jj]
        return out

    def bias_keys(self, keys, ema):
        m = np.ones(self.n, bool); used = 0
        for k in keys:
            if k in self.htf:
                m &= self.bias(k, ema); used += 1
        return m if used else np.zeros(self.n, bool)

    def auto_key(self):
        return "1h" if self.step_ms < 3_600_000 else "4h"


def _run_both(fn, d, step_ms, htf, **kw):
    rows = []
    for sgn in (1, -1):
        x = d if sgn == 1 else ss._neg(d)
        ctx = Ctx(x, d, sgn, step_ms, htf)
        for i, sl, tp in fn(x, ctx, **kw):
            rows.append((int(i), sgn, sgn * sl, (sgn * tp) if tp == tp else float("nan")))
    rows.sort(key=lambda r: (r[0], -r[1]))
    return ss._pack([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows])


def _vol_ok(ctx, k):
    sm = _prev(_sma(ctx.v, 20), 0)
    return ctx.v > k * sm


# ----------------------------------------------------------------------------- پیوت‌های کلاسیک روزانه
def _levels(ctx, x_sign_price=True):
    """S2,S1,P,R1,R2 روز بسته‌شده‌ی قبل (قیمت واقعی؛ در فضای آینه‌ای با sgn ضرب می‌شود). None اگر HTF روزانه نباشد."""
    h = ctx.htf.get("1d")
    if h is None:
        return None
    H, L, C = h["h"], h["l"], h["c"]
    P = (H + L + C) / 3
    lv = [P - (H - L), 2 * P - H, P, 2 * P - L, P + (H - L)]
    return [ctx.align_arr("1d", a) * ctx.sgn for a in lv]


def _pivot_stop_tp(levels, i, c, atr, cap=3.0, gap=0.5):
    below = [lv[i] for lv in levels if lv[i] == lv[i] and lv[i] < c - gap * atr]
    above = [lv[i] for lv in levels if lv[i] == lv[i] and lv[i] > c + gap * atr]
    if not below:
        return None
    sl = max(below)
    if c - sl > cap * atr:
        sl = c - cap * atr
    return sl, (min(above) if above else float("nan"))


# ----------------------------------------------------------------------------- ۱) Range Fade
def _g_rfd(x, ctx, n=48, poke=0.1, room=1.0, quiet=((21, 24), (0, 7)), adx_max=25.0, w_min=2.0, w_max=10.0):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    L = pd.Series(l).rolling(n).min().shift(1).values; H = pd.Series(h).rolling(n).max().shift(1).values
    q = np.zeros(ctx.n, bool)
    for a, b in quiet:
        q |= (ctx.hours >= a) & (ctx.hours < b)
    width = (H - L) / atr; ax = _adx(x)
    cond = (l < L - poke * atr) & (c > L) & (c > (h + l) / 2) & q & (width >= w_min) & (width <= w_max) & (ax < adx_max)
    out = []
    for i in np.flatnonzero(cond):
        stop = l[i] - 0.15 * atr[i]; risk = c[i] - stop; mid = (H[i] + L[i]) / 2
        if risk > 0 and mid - c[i] >= room * risk:
            out.append((i, stop, mid))
    return out


# ----------------------------------------------------------------------------- ۲) Sweep & Reclaim
def _g_swr(x, ctx, k=3, buf=0.05, body=0.5, vol_k=1.0, min_rng=0.8, max_age=100):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    pl, pli, _ = pivots(l, k, hi=False)
    cond = (l < pl - buf * atr) & (c > pl) & (c > o) & ((c - o) >= body * (h - l)) & ((h - l) >= min_rng * atr) & _vol_ok(ctx, vol_k)
    out = []; used = set()
    for i in np.flatnonzero(cond):
        if i - pli[i] > max_age or pli[i] in used:
            continue
        used.add(int(pli[i])); out.append((i, l[i] - 0.1 * atr[i], float("nan")))
    return out


# ----------------------------------------------------------------------------- ۳) RSI Momentum Reversal
def _g_rsm(x, ctx, n=14, lo=25.0, min_stop_atr=0.6):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    r = _rsi(c, n); mn = pd.Series(r).rolling(3).min().shift(1).values
    cond = (mn < lo) & (r > lo) & (c > o)
    low3 = pd.Series(l).rolling(3).min().values
    out = []
    for i in np.flatnonzero(cond):
        stop = min(low3[i] - 0.1 * atr[i], c[i] - min_stop_atr * atr[i])
        out.append((i, stop, float("nan")))
    return out


# ----------------------------------------------------------------------------- ۴) MTF Scalper
def _g_mts(x, ctx, sl_atr=1.5, tp_atr=3.0, d_max=0.5):
    c = x["c"]; atr = ctx.atr
    r = pd.Series(_rsi(c, 14)); lo = r.rolling(14).min(); hi = r.rolling(14).max()
    st = ((r - lo) / (hi - lo).replace(0, np.nan)).values
    K = _sma(st, 3); D = _sma(K, 3)
    cross = (_prev(K) <= _prev(D)) & (K > D) & (D < d_max)
    cond = cross & ctx.bias_keys(("1h", "4h", "1d"), "e20")
    return [(i, c[i] - sl_atr * atr[i], c[i] + tp_atr * atr[i]) for i in np.flatnonzero(cond)]


# ----------------------------------------------------------------------------- ۵) DkS Market-Structure Breakout
def _g_dks(x, ctx, k=3, vol_k=1.5, max_stop_atr=4.0):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    e1, e2 = _ema(c, 20), _ema(c, 50); r = _rsi(c, 14)
    ph, phi, _ = pivots(h, k, hi=True); pl, _pli, _ = pivots(l, k, hi=False)
    cond = (e1 > e2) & (c > e2) & (c > ph) & (_prev(c) <= ph) & _vol_ok(ctx, vol_k) & (r > 50)
    out = []; used = set()
    for i in np.flatnonzero(cond):
        if phi[i] in used or pl[i] != pl[i]:
            continue
        stop = pl[i] - 0.1 * atr[i]
        if not (stop < c[i]) or c[i] - stop > max_stop_atr * atr[i]:
            continue
        used.add(int(phi[i])); out.append((i, stop, float("nan")))
    return out


# ----------------------------------------------------------------------------- ۶) TickVol Scalper (۳ ماژول، حجم کندل به‌جای تیک)
def _tk_emit(ctx, x, cond, levels):
    c = x["c"]; atr = ctx.atr; out = []
    if levels is None:
        return out
    for i in np.flatnonzero(cond):
        r = _pivot_stop_tp(levels, i, c[i], atr[i])
        if r:
            out.append((i, r[0], r[1]))
    return out


def _g_tkf(x, ctx, win=12, comp=2.0, vol_k=1.5, ny=(13, 21)):
    h, c = x["h"], x["c"]; atr = ctx.atr
    H = pd.Series(h).rolling(win).max().shift(1).values; L = pd.Series(x["l"]).rolling(win).min().shift(1).values
    cond = ((H - L) <= comp * atr) & (c > H) & (ctx.hours >= ny[0]) & (ctx.hours < ny[1]) & _vol_ok(ctx, vol_k)
    return _tk_emit(ctx, x, cond, _levels(ctx))


def _g_tkm(x, ctx, vol_k=1.5, bb=20, kk=2.0):
    o, l, c = x["o"], x["l"], x["c"]
    m, s = _sma(c, bb), _std(c, bb); lo = m - kk * s
    cond = (_prev(l) < _prev(lo)) & (c > lo) & (c > o) & _vol_ok(ctx, vol_k)
    return _tk_emit(ctx, x, cond, _levels(ctx))


def _g_tks(x, ctx, k=6, narrow=0.6, wide=1.5, vol_k=1.5):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    rngm = pd.Series(h - l).rolling(k).mean().shift(1).values
    cond = (rngm < narrow * atr) & ((h - l) >= wide * atr) & (c >= l + 0.75 * (h - l)) & (c > o) & _vol_ok(ctx, vol_k)
    return _tk_emit(ctx, x, cond, _levels(ctx))


# ----------------------------------------------------------------------------- ۷) SuperTrend + MACD + RSI + BB + Volume + ATR
def _supertrend_dir(h, l, c, atr, mult=3.0):
    n = len(c); hl2 = (h + l) / 2; ub = hl2 + mult * atr; lb = hl2 - mult * atr
    fu, fl, tr = ub.copy(), lb.copy(), np.zeros(n, int)
    for i in range(1, n):
        if np.isnan(atr[i]) or np.isnan(fu[i - 1]):
            fu[i], fl[i] = ub[i], lb[i]; continue
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        tr[i] = 1 if c[i] > fu[i - 1] else (-1 if c[i] < fl[i - 1] else tr[i - 1])
    return tr


def _g_smm(x, ctx, w=3, vol_k=1.5, min_atr_pct=0.15, sl_atr=1.0, tp_atr=3.0):
    h, l, c = x["h"], x["l"], x["c"]; atr = ctx.atr; n = ctx.n
    dr = _supertrend_dir(h, l, c, atr, 3.0)
    flip = np.concatenate([[False], (dr[1:] == 1) & (dr[:-1] != 1)]); flip[:60] = False
    lf = np.maximum.accumulate(np.where(flip, np.arange(n), -10 ** 9))
    rec_flip = (np.arange(n) - lf <= w) & (dr == 1)
    macd = _ema(c, 12) - _ema(c, 26); sig = _ema(macd, 9)
    cr = np.concatenate([[False], (macd[1:] > sig[1:]) & (macd[:-1] <= sig[:-1])])
    lc = np.maximum.accumulate(np.where(cr, np.arange(n), -10 ** 9))
    rec_cross = (np.arange(n) - lc <= w)
    m, s = _sma(c, 20), _std(c, 20)
    atrpct = atr / np.abs(c) * 100.0
    ok = rec_flip & rec_cross & (_rsi(c, 14) > 50) & (c > m + 2 * s) & _vol_ok(ctx, vol_k) & (atrpct >= min_atr_pct)
    ok = np.nan_to_num(ok, nan=False).astype(bool)
    first = ok & ~np.concatenate([[False], ok[:-1]])
    return [(i, c[i] - sl_atr * atr[i], c[i] + tp_atr * atr[i]) for i in np.flatnonzero(first)]


# ----------------------------------------------------------------------------- ۸) Liquidity Sweep + MSS + FVG
def _g_lsm(x, ctx, k=3, mss_wait=10, pb_wait=12, buf=0.05, max_age=80):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr; n = ctx.n
    pl, pli, _ = pivots(l, k, hi=False)
    bias = ctx.bias(ctx.auto_key(), "e200")
    sweep = (l < pl - buf * atr) & (c > pl)
    out = {}
    for j in np.flatnonzero(sweep):
        p = int(pli[j])
        if j - p > max_age:
            continue
        LH = h[p:j + 1].max(); low = l[j]; m = None
        for q in range(j + 1, min(n, j + 1 + mss_wait)):
            if l[q] <= low:
                break
            if c[q] > LH:
                m = q; break
        if m is None:
            continue
        top = bot = None
        for t in range(m - 1, j, -1):
            if l[t + 1] > h[t - 1]:
                top, bot = l[t + 1], h[t - 1]; break
        if top is None:
            top, bot = LH, LH - 0.5 * atr[m]
        for q in range(m + 1, min(n, m + 1 + pb_wait)):
            if l[q] <= low:
                break
            if l[q] <= top and c[q] > max(bot, low) and c[q] > o[q]:
                if bias[q]:
                    out.setdefault(q, low - 0.1 * atr[q])
                break
    return [(q, s, float("nan")) for q, s in sorted(out.items())]


# ----------------------------------------------------------------------------- ۹) Trend Pullback + EMA + RSI
def _g_tpb(x, ctx, pb=8, need_bos=False, gap=60):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    e20, e50 = _ema(c, 20), _ema(c, 50); r = _rsi(c, 14)
    trend = (e20 > e50) & (e50 > _prev(e50, 5))
    touch = pd.Series(l).rolling(pb).min().shift(1).values <= _prev(e20)
    held = pd.Series(c).rolling(pb).min().shift(1).values >= _prev(e50) - 0.5 * atr
    sigc = _prev(c) > _prev(o)
    brk = (c > _prev(h)) & (c > o) & (r > 50)
    cond = trend & touch & held & sigc & brk & ctx.bias(ctx.auto_key(), "e200")
    if need_bos:
        cond &= c > pd.Series(h).rolling(pb + 3).max().shift(1).values
    low = pd.Series(l).rolling(pb + 1).min().values
    out = []; last = -10 ** 9
    for i in np.flatnonzero(np.nan_to_num(cond, nan=False).astype(bool)):
        if i - last < pb:
            continue
        stop = low[i] - 0.1 * atr[i]
        if stop < c[i]:
            out.append((i, stop, float("nan"))); last = i
    return out


# ----------------------------------------------------------------------------- ۱۰) Volatility Compression + Breakout
def _g_vcb(x, ctx, win=12, look=6, pct=0.20, hist=100, retest=False, rt_wait=8, min_rng_atr=1.0):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr; n = ctx.n
    m, s = _sma(c, 20), _std(c, 20); bbw = 4 * s / m
    q = pd.Series(bbw).rolling(hist).quantile(pct).shift(1).values
    comp = pd.Series((bbw <= q).astype(float)).rolling(look).max().shift(1).values > 0
    Rh = pd.Series(h).rolling(win).max().shift(1).values; Rl = pd.Series(l).rolling(win).min().shift(1).values
    brk = comp & (c > Rh) & (_prev(c) <= Rh) & ((h - l) >= min_rng_atr * atr) & ctx.bias(ctx.auto_key(), "e50")
    brk = np.nan_to_num(brk, nan=False).astype(bool)
    out = {}
    for b in np.flatnonzero(brk):
        if not retest:
            stop = max(Rl[b] - 0.1 * atr[b], c[b] - 3 * atr[b])
            if stop < c[b]:
                out[b] = stop
            continue
        for q2 in range(b + 1, min(n, b + 1 + rt_wait)):
            if c[q2] < Rh[b] - 0.5 * atr[b]:
                break
            if l[q2] <= Rh[b] + 0.1 * atr[b] and c[q2] > Rh[b] and c[q2] > o[q2]:
                out.setdefault(q2, l[b:q2 + 1].min() - 0.1 * atr[q2]); break
    return [(i, s_, float("nan")) for i, s_ in sorted(out.items())]


# ----------------------------------------------------------------------------- ۱۱) Failed Breakout + Reversal
def _g_fbr(x, ctx, n=48, wait=6, adx_max=25.0, w_min=2.0, w_max=10.0, room=2.0):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr; N = ctx.n
    L = pd.Series(l).rolling(n).min().shift(1).values; H = pd.Series(h).rolling(n).max().shift(1).values
    ax = _adx(x); width = (H - L) / atr
    fail = (l < L - 0.05 * atr) & (c > L) & (ax < adx_max) & (width >= w_min) & (width <= w_max)
    out = {}
    for j in np.flatnonzero(fail):
        for q in range(j + 1, min(N, j + 1 + wait)):
            if l[q] < l[j]:
                break
            if c[q] > h[j]:
                stop = l[j] - 0.1 * atr[q]; risk = c[q] - stop
                if risk > 0 and H[j] - c[q] >= room * risk:
                    out.setdefault(q, (stop, (H[j] + L[j]) / 2))
                break
    return [(q, v[0], v[1]) for q, v in sorted(out.items())]


# ----------------------------------------------------------------------------- ۱۲) Mean Reversion RSI + Bollinger
def _g_mrb(x, ctx, bias=False, back=3):
    o, l, c = x["o"], x["l"], x["c"]; atr = ctx.atr
    m, s = _sma(c, 20), _std(c, 20); lo = m - 2 * s; r = _rsi(c, 14)
    pierce = ((pd.Series((l < lo) & (r < 30)).astype(float).rolling(back).max().shift(1).values) > 0)
    cond = pierce & (c > lo) & (c > o)
    if bias:
        cond &= ctx.bias(ctx.auto_key(), "e200")
    low = pd.Series(l).rolling(back + 1).min().values
    return [(i, low[i] - 0.1 * atr[i], m[i]) for i in np.flatnonzero(np.nan_to_num(cond, nan=False).astype(bool))]


# ----------------------------------------------------------------------------- ۱۳) Multi-Timeframe Confirmation (1h / 15m / 5m)
def _g_mtc(x, ctx, k=6, trig=6, rr_room=3.0, look=288):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; atr = ctx.atr
    if not (ctx.has("15m") and ctx.has("1h")):
        return []
    g = ctx.htf["15m"]
    pb15 = (pd.Series(g["l"]).rolling(3).min().values <= g["e20"]) & (g["c"] >= g["e50"] - 1e-12) & (g["e20"] > g["e50"]) & g["ok50"]
    pb = ctx.align_arr("15m", pb15.astype(float), fill=0.0) > 0
    h1 = ctx.htf["1h"]
    struct = ctx.align_arr("1h", (h1["e20"] > h1["e50"]).astype(float), fill=0.0) > 0
    cond = pb & struct & ctx.bias("1h", "e200") & (c > pd.Series(h).rolling(trig).max().shift(1).values) & (c > o)
    _p, _l, flag = pivots(h, k, hi=True)
    conf = np.flatnonzero(flag) + k
    low = pd.Series(l).rolling(trig + 1).min().values
    out = []
    for i in np.flatnonzero(np.nan_to_num(cond, nan=False).astype(bool)):
        stop = low[i] - 0.1 * atr[i]; dist = c[i] - stop
        if dist <= 0:
            continue
        lo_i = np.searchsorted(conf, i - look); hi_i = np.searchsorted(conf, i, side="right")
        levels = h[conf[lo_i:hi_i] - k]
        if len(levels) and np.any((levels > c[i]) & (levels < c[i] + rr_room * dist)):
            continue
        out.append((i, stop, float("nan")))
    return out


# ----------------------------------------------------------------------------- ثبت
def _mk(fn, title, tfs, family, raw=False, **kw):
    return {"fn": fn, "kw": kw, "title": title, "tfs": tfs, "family": family, "raw": raw}


# ----------------------------------------------------------------------------- CNDL: استراتژی اصلی ربات‌ها («کندل سیگنال»)
# بازنویسی عینی و علّی‌ِ candle_engine.step_candle_state (V5/V6): چکش/ستاره‌ی دنباله‌دار + SMA7 + شیب + ADX + «موج دوم»
# + سفارش Stop Buy/Sell در انتظار (trigger = High/Low کندل سیگنال، SL = سمت مقابل، حداکثر PENDING_MAX کندل صبر).
# سیگنال روی کندلی معلوم می‌شود که سفارش در انتظار را «فعال» می‌کند؛ ورود در آزمایشگاه = open کندل بعد (کمی محافظه‌کارتر از
# قیمت trigger خودِ موتور). test_lowtf.py معادل‌بودن با step_candle_state اصلی را کندل‌به‌کندل چک می‌کند.
CND = dict(BODY_MAX=0.5, WICK_MIN=2.0, WICK_MAX=0.5, SLOPE_ATR=0.03, ADX_MIN=20.0, WAVE_LB=8, COOLDOWN=5, WHIPSAW=0.5,
           PENDING_MAX=10, MIN_RISK=0.20)


def _g_cndl_raw(d, step_ms, htf=None, **_):
    o_, h_, l_, c_ = d["o"], d["h"], d["l"], d["c"]
    n = len(c_); P = CND
    out = []
    atr = None; trb = []; pdm = mdm = None; dmb = []; adx = None; dxb = []
    hist_c = []; hist_sma = []                      # close و sma7 هر کندل قبلی
    trend_prev = "flat"; bull_used = bear_used = False
    last_bull = last_bear = None; last_px = None; pending = None
    for i in range(n):
        o, h, l, c = float(o_[i]), float(h_[i]), float(l_[i]), float(c_[i])
        pc = hist_c[-1] if hist_c else None
        ph = float(h_[i - 1]) if i >= 1 else None; pl = float(l_[i - 1]) if i >= 1 else None
        tr = max(h - l, abs(h - pc), abs(l - pc)) if pc is not None else (h - l)
        if atr is None:
            trb.append(tr)
            atr_i = sum(trb[-14:]) / 14.0 if len(trb) >= 14 else None
        else:
            atr_i = (atr * 13 + tr) / 14.0
        if ph is not None:
            up, dn = h - ph, pl - l
            pdm_ = up if (up > dn and up > 0) else 0.0; mdm_ = dn if (dn > up and dn > 0) else 0.0
        else:
            pdm_ = mdm_ = 0.0
        if pdm is None:
            dmb.append((pdm_, mdm_))
            if len(dmb) >= 14:
                pdm14 = sum(x[0] for x in dmb[-14:]); mdm14 = sum(x[1] for x in dmb[-14:])
            else:
                pdm14 = mdm14 = None
        else:
            pdm14 = pdm - pdm / 14.0 + pdm_; mdm14 = mdm - mdm / 14.0 + mdm_
        adx_i = adx
        if pdm14 is not None and atr_i:
            pdi = 100 * (pdm14 / 14.0) / atr_i if atr_i > 0 else 0
            mdi = 100 * (mdm14 / 14.0) / atr_i if atr_i > 0 else 0
            dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
            if adx is None:
                dxb.append(dx)
                adx_i = sum(dxb[-14:]) / 14.0 if len(dxb) >= 14 else None
            else:
                adx_i = (adx * 13 + dx) / 14.0
        body = abs(c - o); up_sh = h - max(c, o); lo_sh = min(c, o) - l; tot = h - l
        sma_prev = sum(hist_c[-7:]) / 7.0 if len(hist_c) >= 7 else None
        incl = hist_c[-6:] + [c]
        sma_i = sum(incl) / 7.0 if len(incl) >= 7 else None
        small = (body <= P["BODY_MAX"] * tot) if tot > 0 else False
        hammer = small and lo_sh >= P["WICK_MIN"] * body and up_sh <= P["WICK_MAX"] * body
        star = small and up_sh >= P["WICK_MIN"] * body and lo_sh <= P["WICK_MAX"] * body
        above = sma_i is not None and c > sma_i; below = sma_i is not None and c < sma_i
        slope = (sma_i - sma_prev) if (sma_i is not None and sma_prev is not None) else None
        upt = slope is not None and slope > 0; dnt = slope is not None and slope < 0
        flat = True if (atr_i is None or slope is None) else abs(slope) < P["SLOPE_ATR"] * atr_i
        strong = True if adx_i is None else adx_i >= P["ADX_MIN"]
        lb = hist_sma[-P["WAVE_LB"]:]; lbc = hist_c[-P["WAVE_LB"]:]
        pb_bull = any(sm is not None and cc <= sm for sm, cc in zip(lb, lbc))
        pb_bear = any(sm is not None and cc >= sm for sm, cc in zip(lb, lbc))
        raw_bull = hammer and above and upt and (not flat) and strong and pb_bull
        raw_bear = star and below and dnt and (not flat) and strong and pb_bear
        if upt and trend_prev != "up":
            bull_used = False
        if dnt and trend_prev != "down":
            bear_used = False
        cd_b = last_bull is None or i - last_bull >= P["COOLDOWN"]
        cd_s = last_bear is None or i - last_bear >= P["COOLDOWN"]
        pm_ok = True if (last_px is None or atr_i is None) else abs(c - last_px) >= atr_i * P["WHIPSAW"]
        risk_ok = (P["MIN_RISK"] <= 0) or (h > 0 and (h - l) / h * 100.0 >= P["MIN_RISK"])
        new_b = raw_bull and (not bull_used) and cd_b and pm_ok and risk_ok
        new_s = raw_bear and (not bear_used) and cd_s and pm_ok and risk_ok
        if pending is not None:
            waited = i - pending["bar"]
            if pending["side"] > 0:
                inval = l <= pending["sl"]; trig = h >= pending["trig"]
            else:
                inval = h >= pending["sl"]; trig = l <= pending["trig"]
            if inval:
                pending = None
            elif trig:
                sl = pending["sl"]
                if (c > sl) if pending["side"] > 0 else (c < sl):
                    risk = abs(pending["trig"] - sl)
                    out.append((i, pending["side"], sl, pending["trig"] + pending["side"] * 2 * risk))
                pending = None
            elif waited > P["PENDING_MAX"]:
                pending = None
        if pending is None:
            if new_b:
                bull_used = True; last_bull = i; last_px = c; pending = {"side": 1, "trig": h, "sl": l, "bar": i}
            elif new_s:
                bear_used = True; last_bear = i; last_px = c; pending = {"side": -1, "trig": l, "sl": h, "bar": i}
        hist_c.append(c); hist_sma.append(sma_i)
        atr = atr_i; pdm, mdm, adx = pdm14, mdm14, adx_i
        trend_prev = "up" if upt else ("down" if dnt else "flat")
    return ss._pack([r[0] for r in out], [r[1] for r in out], [r[2] for r in out], [r[3] for r in out])


GENS: Dict[str, Dict[str, Any]] = {
    "RFD": _mk(_g_rfd, "Range Fade (quiet sessions)", ("1m", "5m"), "file1#1"),
    "SWR": _mk(_g_swr, "Sweep & Reclaim", ("1m", "5m"), "file1#2"),
    "RSM": _mk(_g_rsm, "RSI Momentum Reversal", ("1m", "5m"), "file1#3"),
    "MTS": _mk(_g_mts, "MTF Scalper (StochRSI)", ("5m", "15m"), "file1#4"),
    "DKS": _mk(_g_dks, "Structure Breakout + Volume", ("5m", "15m"), "file1#5"),
    "TKF": _mk(_g_tkf, "TickVol Scalper: compression breakout (NY)", ("5m", "15m"), "file1#6"),
    "TKM": _mk(_g_tkm, "TickVol Scalper: mean reversion", ("5m", "15m"), "file1#6"),
    "TKS": _mk(_g_tks, "TickVol Scalper: absorption squeeze", ("5m", "15m"), "file1#6"),
    "SMM": _mk(_g_smm, "SuperTrend+MACD multi-confirm", ("1m", "5m"), "file1#7"),
    "SMS": _mk(_g_smm, "SuperTrend+MACD multi-confirm (strict ATR>0.5%)", ("1m", "5m"), "file1#7", min_atr_pct=0.5),
    "LSM": _mk(_g_lsm, "Liquidity Sweep + MSS + FVG pullback", ("5m", "15m"), "file2#1"),
    "TPB": _mk(_g_tpb, "Trend Pullback EMA+RSI", ("5m", "15m", "1h"), "file2#2"),
    "TPS": _mk(_g_tpb, "Trend Pullback + structure break", ("5m", "15m", "1h"), "file2#2", need_bos=True),
    "VCB": _mk(_g_vcb, "Volatility compression breakout", ("5m", "15m"), "file2#3"),
    "VCR": _mk(_g_vcb, "Volatility compression breakout + retest", ("5m", "15m"), "file2#3", retest=True),
    "FBR": _mk(_g_fbr, "Failed breakout reversal", ("5m", "15m"), "file2#4"),
    "MRA": _mk(_g_mrb, "Mean reversion RSI+BB (no trend filter)", ("15m", "1h"), "file2#5"),
    "MRB": _mk(_g_mrb, "Mean reversion RSI+BB (HTF trend filter)", ("15m", "1h"), "file2#5", bias=True),
    "MTC": _mk(_g_mtc, "Multi-timeframe confirmation 1h/15m/5m", ("5m",), "file2#6"),
    # استراتژی اصلی ربات‌ها (کندل سیگنال)؛ per_tf=True → برای هر تایم‌فریم verdict جدا (کلید «CNDL@5m»)
    "CNDL": _mk(_g_cndl_raw, "Candle Signal (hammer/star + SMA7 + stop order)", ("1m", "5m", "15m", "1h", "4h"), "orig", raw=True),
}
GENS["CNDL"]["per_tf"] = True
ORIG = tuple(k for k, v in GENS.items() if v["family"] == "orig")     # ماژول‌های «اصلی»؛ جزو قفل‌شده‌های lowtf نیستند


def run(name: str, d, step_ms: int, htf=None):
    g = GENS[name]
    if g.get("raw"):
        return g["fn"](d, step_ms)
    if htf is None:
        htf = build_htf(d, step_ms)
    return _run_both(g["fn"], d, step_ms, htf, **g["kw"])


# ----------------------------------------------------------------------------- فیلترهای مشترک
def _trend(h):
    c = h["c"]; e20, e50 = h["e20"], h["e50"]
    tr = np.where((c > e50) & (e20 > e50), 1, np.where((c < e50) & (e20 < e50), -1, 0))
    tr = np.array(tr); tr[~h["ok50"]] = 0
    return tr


def _htf_trend_at(d, step_ms, htf, key):
    h = htf.get(key)
    out = np.zeros(len(d["c"]), int)
    if h is None:
        return out
    j = np.searchsorted(h["close_ms"], d["t"] + step_ms, side="right") - 1
    ok = j >= 0
    out[ok] = _trend(h)[j[ok]]
    return out


def filter_masks(d, step_ms, htf, idx, side, sl, flt):
    """بولین برای هر سیگنال. flt ∈ LIVE_FILTERS. یک پیاده‌سازی برای آزمایشگاه و ربات."""
    idx = np.asarray(idx, int); side = np.asarray(side, int)
    if len(idx) == 0 or flt == "none":
        return np.ones(len(idx), bool)
    D = lambda: _htf_trend_at(d, step_ms, htf, "1d")[idx] == side
    if flt == "D":
        return D()
    if flt == "D4":
        return D() & (_htf_trend_at(d, step_ms, htf, "4h")[idx] == side)
    if flt == "S":
        return ss.session_mask(d)[idx]
    if flt == "V":
        return ss.volume_spike_mask(d)[idx]
    if flt == "DS":
        return D() & ss.session_mask(d)[idx]
    if flt == "M":
        return np.abs(d["c"][idx] - np.asarray(sl, float)) / d["c"][idx] * 100.0 >= MIN_RISK_PCT
    raise KeyError(flt)


def detect_last(name: str, d, step_ms: int, htf, flt: str = "none") -> Optional[Dict[str, Any]]:
    """آخرین کندلِ بسته: {side, sl, tp, price} یا None (ربات زنده)."""
    idx, side, sl, tp = run(name, d, step_ms, htf)
    n = len(d["c"])
    pick = np.flatnonzero(idx == n - 1)
    if not len(pick):
        return None
    j = pick[-1]
    if not filter_masks(d, step_ms, htf, idx[[j]], side[[j]], sl[[j]], flt)[0]:
        return None
    return {"side": "BUY" if side[j] > 0 else "SELL", "sl": float(sl[j]), "tp": float(tp[j]), "price": float(d["c"][-1])}

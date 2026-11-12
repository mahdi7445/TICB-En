# -*- coding: utf-8 -*-
"""
rnd_dsl.py — زبان توصیف استراتژی (DSL) برای سیستم تحقیق و توسعه (R&D Lab).

هیچ کدی اجرا نمی‌شود: هر استراتژی یک JSON ساده است (شرط‌ها روی اندیکاتورهای لیست‌سفید) و این فایل آن را «علّی» (بدون نگاه به آینده)
به همان فرمتِ آشکارسازهای lowtf_strategies تبدیل می‌کند: (اندیس کندل, حد ضرر, هدف). به همین دلیل هر استراتژیِ تولیدشده توسط R&D
(حاصل ابتکار هوش مصنوعی یا جست‌وجوی خودکار) دقیقاً از همان مسیرِ آزمایشگاه (strategy_lab: همه‌ی نردبان‌ها، train/test، t-stat، کارمزد)
و همان قفل/PASS و همان ربات زنده عبور می‌کند.  شش اسلات ثابت RD1..RD6 داریم؛ محتوای هر اسلات از data/rnd_strategies.json خوانده می‌شود.

قالب یک استراتژی (سمت خرید؛ سمت فروش آینه‌ی آن است):
{
  "id": "RD1", "name": "Trend pullback + volume", "tfs": ["15m","1h"],
  "all": [ ["close", ">", "ema:200"], ["rsi:14", "<", 40], ["volr:20", ">", 1.2], ["htf:4h:trend", "==", 1] ],
  "stop": {"kind": "atr", "k": 1.5},            # atr | swing(n, buf) | candle(buf)
  "cooldown": 6, "min_risk_pct": 0.2, "max_stop_atr": 4.0,
  "why": "توضیح فرضیه"
}
ویژگی‌ها (سمت چپ/راست شرط): close open high low | ema:N sma:N rsi:N adx:N atrp:N volr:N ret:N z:N bbpos:N emadist:N slope:N
  hh:N ll:N (سقف/کف N کندل قبلی) rng:atr body closepos lwick uwick pin engulf inside | htf:1h:trend htf:4h:trend htf:1d:trend | hour
عملگرها: > < >= <= == != x> (کراس رو به بالا) x< (کراس رو به پایین) ; مقدار راست: عدد یا ویژگی دیگر.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SLOTS = ("RD1", "RD2", "RD3", "RD4", "RD5", "RD6")
TFS_OK = ("1m", "5m", "15m", "1h", "4h")
OPS = (">", "<", ">=", "<=", "==", "!=", "x>", "x<")
MAX_CONDS = 8
_DATA = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STRATS_FILE = os.path.join(_DATA, "rnd_strategies.json")
REMOTE_NAME = "rnd_strategies.json"

# ویژگی‌ها: نام -> (حداقل/حداکثر N, پیش‌فرض N)
FEATS: Dict[str, Tuple[int, int, int]] = {
    "ema": (2, 400, 21), "sma": (2, 400, 20), "rsi": (2, 50, 14), "adx": (5, 50, 14), "atrp": (5, 50, 14), "volr": (5, 100, 20),
    "ret": (1, 200, 5), "z": (10, 200, 50), "bbpos": (10, 100, 20), "emadist": (5, 400, 21), "slope": (5, 100, 21),
    "hh": (2, 400, 20), "ll": (2, 400, 20),
}
PLAIN = ("close", "open", "high", "low", "rng", "body", "closepos", "lwick", "uwick", "pin", "engulf", "inside", "hour")
HTF_KEYS = ("15m", "1h", "4h", "1d")
_FEAT_RE = re.compile(r"^([a-z]+):(\d{1,3})$")
_HTF_RE = re.compile(r"^htf:(15m|1h|4h|1d):(trend|rsi)$")


# ----------------------------------------------------------------------------- اعتبارسنجی
def _parse_feat(f: Any) -> Optional[Tuple[str, Any]]:
    """رشته‌ی ویژگی -> ('num', value) | ('plain', name) | ('feat', (name, n)) | ('htf', (key, what)); None = نامعتبر."""
    if isinstance(f, bool):
        return None
    if isinstance(f, (int, float)):
        return ("num", float(f)) if math.isfinite(float(f)) else None
    if not isinstance(f, str):
        return None
    f = f.strip().lower()
    if f in PLAIN:
        return ("plain", f)
    m = _HTF_RE.match(f)
    if m:
        return ("htf", (m.group(1), m.group(2)))
    m = _FEAT_RE.match(f)
    if m and m.group(1) in FEATS:
        lo, hi, _ = FEATS[m.group(1)]
        n = int(m.group(2))
        if lo <= n <= hi:
            return ("feat", (m.group(1), n))
    return None


def validate_spec(spec: Any, slot: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """(ok, reason, cleaned). cleaned فقط شامل کلیدهای مجاز است؛ هرگز استثنا نمی‌دهد."""
    try:
        if not isinstance(spec, dict):
            return False, "spec is not an object", None
        conds = spec.get("all")
        if not isinstance(conds, list) or not (1 <= len(conds) <= MAX_CONDS):
            return False, f"'all' must hold 1..{MAX_CONDS} conditions", None
        clean_c = []
        for c in conds:
            if not isinstance(c, (list, tuple)) or len(c) != 3:
                return False, f"bad condition {c!r}", None
            a, op, b = c
            if op not in OPS:
                return False, f"bad operator {op!r}", None
            pa, pb = _parse_feat(a), _parse_feat(b)
            if pa is None or pb is None:
                return False, f"unknown feature in {c!r}", None
            if pa[0] == "num" and pb[0] == "num":
                return False, f"constant condition {c!r}", None
            if op in ("x>", "x<") and "num" in (pa[0], pb[0]) and pa[0] == "num":
                return False, "cross needs a feature on the left", None
            clean_c.append([a.strip().lower() if isinstance(a, str) else float(a), op, b.strip().lower() if isinstance(b, str) else float(b)])
        st = spec.get("stop") or {"kind": "atr", "k": 1.5}
        kind = st.get("kind")
        if kind == "atr":
            stop = {"kind": "atr", "k": float(min(6.0, max(0.5, float(st.get("k", 1.5)))))}
        elif kind == "swing":
            stop = {"kind": "swing", "n": int(min(60, max(2, int(st.get("n", 8))))), "buf": float(min(1.5, max(0.0, float(st.get("buf", 0.2)))))}
        elif kind == "candle":
            stop = {"kind": "candle", "buf": float(min(1.5, max(0.0, float(st.get("buf", 0.1)))))}
        else:
            return False, f"bad stop kind {kind!r}", None
        tfs = spec.get("tfs") or ["15m", "1h"]
        if isinstance(tfs, str):
            tfs = [tfs]
        tfs = [t for t in tfs if t in TFS_OK]
        if not tfs:
            return False, "no valid timeframe", None
        out = {
            "id": slot or str(spec.get("id") or ""), "name": str(spec.get("name") or "R&D strategy")[:60],
            "tfs": tfs, "all": clean_c, "stop": stop,
            "cooldown": int(min(200, max(0, int(spec.get("cooldown", 6))))),
            "min_risk_pct": float(min(3.0, max(0.1, float(spec.get("min_risk_pct", 0.2))))),
            "max_stop_atr": float(min(10.0, max(1.0, float(spec.get("max_stop_atr", 4.0))))),
            "why": str(spec.get("why") or "")[:400],
        }
        for k in ("origin", "parent", "created", "family"):
            if spec.get(k) is not None:
                out[k] = str(spec[k])[:60]
        return True, "ok", out
    except Exception as e:  # noqa: BLE001
        return False, f"invalid spec: {e}", None


def describe(spec: Dict[str, Any]) -> str:
    """توضیح کوتاه و خوانا (برای پنل ادمین)."""
    def fmt(x):
        return f"{x:g}" if isinstance(x, (int, float)) else str(x)
    conds = " AND ".join(f"{fmt(a)} {op} {fmt(b)}" for a, op, b in spec.get("all", []))
    st = spec.get("stop", {})
    stop = {"atr": f"ATR×{st.get('k')}", "swing": f"swing {st.get('n')} bars −{st.get('buf')}ATR", "candle": f"signal-candle −{st.get('buf')}ATR"}.get(st.get("kind"), "?")
    return f"BUY when: {conds}\nSELL = mirror image · stop: {stop} · cooldown {spec.get('cooldown')} bars · TFs {','.join(spec.get('tfs', []))}"


# ----------------------------------------------------------------------------- ویژگی‌ها (روی سری آینه‌ایِ x؛ علّی)
def _ema(a, n):
    return pd.Series(a).ewm(span=n, adjust=False).mean().values


def _rsi(c, n):
    s = pd.Series(c)
    dl = s.diff()
    g = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ls_ = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    r = np.array((100 - 100 / (1 + g / ls_.replace(0, np.nan))).values, dtype=float)
    r[:n] = np.nan
    return r


def _atr(x, n=14):
    h, l, c = x["h"], x["l"], x["c"]
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    out = pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values
    out = np.array(out, float)
    out[:n] = np.nan
    return out


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


class _F:
    """محاسبه‌ی تنبل و کش‌شده‌ی ویژگی‌ها برای یک سمت (x = سری آینه‌ای)."""

    def __init__(self, x, ctx):
        self.x, self.ctx = x, ctx
        self.o, self.h, self.l, self.c = (np.asarray(x[k], float) for k in ("o", "h", "l", "c"))
        self.n = len(self.c)
        self.v = np.asarray(ctx.v, float)
        self.atr = np.asarray(ctx.atr, float)
        self._cache: Dict[Any, np.ndarray] = {}

    def get(self, parsed) -> np.ndarray:
        kind, val = parsed
        if kind == "num":
            return np.full(self.n, val, float)
        key = (kind, val)
        if key in self._cache:
            return self._cache[key]
        out = np.asarray(self._calc(kind, val), float)      # بولین‌ها (pin/engulf/inside) هم عددی می‌شوند
        self._cache[key] = out
        return out

    def _calc(self, kind, val) -> np.ndarray:
        c, o, h, l, atr = self.c, self.o, self.h, self.l, self.atr
        rng = np.where(h - l > 0, h - l, np.nan)
        if kind == "plain":
            if val == "close": return c
            if val == "open": return o
            if val == "high": return h
            if val == "low": return l
            if val == "hour": return np.asarray(self.ctx.hours, float)
            if val == "rng": return (h - l) / np.where(atr > 0, atr, np.nan)
            if val == "body": return abs(c - o) / rng
            if val == "closepos": return (c - l) / rng
            if val == "lwick": return (np.minimum(o, c) - l) / rng
            if val == "uwick": return (h - np.maximum(o, c)) / rng
            if val == "pin":          # صعودی: فتیله‌ی پایینی ≥ ۲ برابر بدنه، بسته‌شدن در ۴۰٪ بالای کندل
                body = abs(c - o)
                return ((np.minimum(o, c) - l) >= 2 * np.maximum(body, 1e-12)) & ((c - l) / rng >= 0.6)
            if val == "engulf":       # صعودی: کندل قبلی نزولی و این کندل صعودی با بدنه‌ی پوشاننده
                pc, po = np.concatenate([[np.nan], c[:-1]]), np.concatenate([[np.nan], o[:-1]])
                return (pc < po) & (c > o) & (c >= po) & (o <= pc)
            if val == "inside":       # کندل قبلی داخل کندل ماقبلش بوده و این کندل سقف آن را شکسته
                ph, pl = np.concatenate([[np.nan], h[:-1]]), np.concatenate([[np.nan], l[:-1]])
                pph, ppl = np.concatenate([[np.nan, np.nan], h[:-2]]), np.concatenate([[np.nan, np.nan], l[:-2]])
                return (ph < pph) & (pl > ppl) & (c > ph)
        if kind == "feat":
            name, n = val
            if name == "ema": return _ema(c, n)
            if name == "sma": return pd.Series(c).rolling(n).mean().values
            if name == "rsi": return _rsi(c, n)
            if name == "adx": return _adx(self.x, n)
            if name == "atrp": return _atr(self.x, n) / np.abs(c) * 100.0
            if name == "volr": return self.v / np.where(pd.Series(self.v).rolling(n).mean().shift(1).values > 0, pd.Series(self.v).rolling(n).mean().shift(1).values, np.nan)
            if name == "ret": return (c - pd.Series(c).shift(n).values) / np.abs(pd.Series(c).shift(n).values) * 100.0
            if name == "z":
                m, s = pd.Series(c).rolling(n).mean().values, pd.Series(c).rolling(n).std(ddof=0).values
                return (c - m) / np.where(s > 0, s, np.nan)
            if name == "bbpos":
                m, s = pd.Series(c).rolling(n).mean().values, pd.Series(c).rolling(n).std(ddof=0).values
                lo, hi = m - 2 * s, m + 2 * s
                return (c - lo) / np.where(hi - lo > 0, hi - lo, np.nan)
            if name == "emadist": return (c - _ema(c, n)) / np.where(atr > 0, atr, np.nan)
            if name == "slope":
                e = _ema(c, n)
                return (e - pd.Series(e).shift(3).values) / np.where(atr > 0, atr, np.nan)
            if name == "hh": return pd.Series(h).rolling(n).max().shift(1).values
            if name == "ll": return pd.Series(l).rolling(n).min().shift(1).values
        if kind == "htf":
            key, what = val
            hh = self.ctx.htf.get(key)
            if hh is None:
                return np.full(self.n, np.nan)
            j = self.ctx.aligned(key)
            out = np.full(self.n, np.nan)
            ok = j >= 0
            if what == "trend":   # +1 = روند هم‌سو با معامله (قیمت HTF بالای EMA50 و EMA20>EMA50 در سمت معامله)، -1 مخالف، 0 خنثی
                s = self.ctx.sgn
                cc, e20, e50 = hh["c"], hh["e20"], hh["e50"]
                tr = np.where((s * (cc - e50) > 0) & (s * (e20 - e50) > 0), 1.0, np.where((s * (cc - e50) < 0) & (s * (e20 - e50) < 0), -1.0, 0.0))
                tr[~hh["ok50"]] = np.nan
                out[ok] = tr[j[ok]]
            else:                 # rsi در HTF روی سری سمت معامله
                r = _rsi(self.ctx.sgn * hh["c"], 14)
                out[ok] = r[j[ok]]
            return out
        raise KeyError((kind, val))


def _cmp(a, op, b, prev_a=None, prev_b=None):
    with np.errstate(invalid="ignore"):
        if op == ">": r = a > b
        elif op == "<": r = a < b
        elif op == ">=": r = a >= b
        elif op == "<=": r = a <= b
        elif op == "==": r = a == b
        elif op == "!=": r = a != b
        elif op == "x>": r = (a > b) & (prev_a <= prev_b)
        else: r = (a < b) & (prev_a >= prev_b)
    return np.asarray(r, bool) & ~(np.isnan(a) | np.isnan(b))


def _shift1(a):
    out = np.full(len(a), np.nan)
    out[1:] = a[:-1]
    return out


def signals_for(spec: Dict[str, Any], x, ctx) -> List[Tuple[int, float, float]]:
    """سیگنال‌های سمت خرید در فضای x (برای فروش x آینه‌ای است): [(i, sl, nan)]. علّی: سیگنال با بسته‌شدن کندل i معلوم است."""
    ok, why, sp = validate_spec(spec, spec.get("id"))
    if not ok:
        return []
    step = ctx.step_ms
    tf = next((k for k, v in {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}.items() if v == step), None)
    if tf not in sp["tfs"]:
        return []
    F = _F(x, ctx)
    n = F.n
    m = np.ones(n, bool)
    for a, op, b in sp["all"]:
        pa, pb = _parse_feat(a), _parse_feat(b)
        A, B = F.get(pa), F.get(pb)
        m &= _cmp(A, op, B, _shift1(A), _shift1(B))
    m &= ~np.isnan(F.atr)
    # لبه‌ی بالارونده: فقط وقتی مجموعه‌ی شرط‌ها «تازه» برقرار شد (نه هر کندل در طول برقراری)
    edge = m & ~np.concatenate([[False], m[:-1]])
    st, atr, c, l = sp["stop"], F.atr, F.c, F.l
    out: List[Tuple[int, float, float]] = []
    last = -10 ** 9
    for i in np.flatnonzero(edge):
        i = int(i)
        if i - last < max(1, sp["cooldown"]):
            continue
        if st["kind"] == "atr":
            sl = c[i] - st["k"] * atr[i]
        elif st["kind"] == "swing":
            sl = float(np.min(l[max(0, i - st["n"] + 1): i + 1])) - st["buf"] * atr[i]
        else:
            sl = l[i] - st["buf"] * atr[i]
        dist = c[i] - sl
        if not dist > 0 or dist > sp["max_stop_atr"] * atr[i]:
            continue
        if dist / abs(c[i]) * 100.0 < sp["min_risk_pct"]:
            continue
        out.append((i, float(sl), float("nan")))
        last = i
    return out


# ----------------------------------------------------------------------------- ذخیره/خواندن اسلات‌ها
_remote_cache: Dict[str, Any] = {"t": 0.0, "v": None}
_override: Dict[str, Optional[Dict[str, Any]]] = {}


def set_override(slot: str, spec: Optional[Dict[str, Any]]) -> None:
    """فقط درون‌حافظه (پیش‌غربال R&D): بدون نوشتن روی دیسک."""
    if spec is None:
        _override.pop(slot, None)
    else:
        _override[slot] = spec


def _read(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def load_all(path: Optional[str] = None) -> Dict[str, Any]:
    d = _read(path or STRATS_FILE)
    return d.get("slots", {}) if isinstance(d.get("slots"), dict) else {}


def load_remote(base_url: Optional[str] = None, ttl: float = 300.0) -> Dict[str, Any]:
    """ربات SGNL: اسلات‌ها را از ریپوی TICB-En می‌خواند (مثل verdictها)؛ خطا ⇒ آخرین مقدار/فایل محلی."""
    now = time.time()
    if now - _remote_cache["t"] > ttl:
        _remote_cache["t"] = now
        try:
            import urllib.request
            try:
                import shared_controls as _sc
                base = base_url or _sc.REMOTE_BASE
            except Exception:  # noqa: BLE001
                base = base_url
            if base:
                req = urllib.request.Request(base + f"{REMOTE_NAME}?t={int(now)}", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    d = json.loads(r.read().decode("utf-8"))
                _remote_cache["v"] = d.get("slots", {}) if isinstance(d, dict) else None
        except Exception:  # noqa: BLE001
            pass
    v = _remote_cache["v"]
    return v if isinstance(v, dict) else load_all()


def get_spec(slot: str) -> Optional[Dict[str, Any]]:
    if slot in _override:
        return _override[slot]
    rec = load_all().get(slot)
    if not rec and os.environ.get("RND_REMOTE") == "1":
        rec = load_remote().get(slot)
    if not isinstance(rec, dict):
        return None
    sp = rec.get("spec") if isinstance(rec.get("spec"), dict) else rec
    ok, _, clean = validate_spec(sp, slot)
    return clean if ok else None


def gen_slot(x, ctx, slot: str = "RD1", **_):
    """آشکارساز با امضای lowtf_strategies (fn(x, ctx, **kw) -> [(i, sl, tp)])."""
    spec = get_spec(slot)
    if not spec:
        return []
    try:
        return signals_for(spec, x, ctx)
    except Exception:  # noqa: BLE001  (هرگز ربات/آزمایشگاه را نشکن)
        return []


def register(GENS: Dict[str, Dict[str, Any]], mk) -> None:
    """اسلات‌های RD1..RD6 را در جدول GENS ثبت می‌کند؛ تایم‌فریم‌های مجاز را خودِ spec محدود می‌کند."""
    for s in SLOTS:
        GENS[s] = mk(gen_slot, f"R&D strategy {s[2:]}", ("5m", "15m", "1h", "4h"), "rnd#1", slot=s)

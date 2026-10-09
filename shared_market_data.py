# -*- coding: utf-8 -*-
"""
shared_market_data.py  —  منبع داده‌ی رایگان و بدون کلید + فیلتر روند + سطح‌بندی سیگنال (Tier)

این فایل «تنها منبع حقیقت» است و در ریپوی TICB-En نگه‌داری می‌شود؛ ورک‌فلوی ریپوی SGNL-Fa
(signals.yml) آن را مثل shared_risk_config.py در ابتدای هر اجرا از main همین ریپو می‌کشد.
هر دو ربات آن را با try/except import می‌کنند: اگر فایل نبود/خراب بود، رفتار قبلی ربات (فقط
Twelve Data، بدون فیلتر روند) دقیقاً مثل قبل ادامه پیدا می‌کند و هیچ‌چیز نمی‌شکند.

چه چیزی می‌دهد:
  1) fetch_klines(symbol, limit, bar_seconds): کندل‌های بسته‌شده از Kraken (اول) و Coinbase
     (پشتیبان) — رایگان، بدون API key، بدون هیچ مصرفی از سهمیه‌ی ۸۰۰ درخواستِ Twelve Data.
     خروجی دقیقاً هم‌شکل Twelve Data است: [{"open_time": ms, "dt":..., "o","h","l","c","v"}].
  2) fetch_daily_trend(symbol): روند روزانه (+1 صعودی / -1 نزولی / 0 خنثی) با همان تعریفی که
     در بک‌تست استفاده شد: Close>EMA50 و EMA20>EMA50 (فقط کندل‌های روزانه‌ی *بسته‌شده*).
  3) tier_gate(...): تصمیم می‌گیرد سیگنال «تأییدشده» (۴ساعته، فقط هم‌جهت با روند روزانه) است یا
     «آزمایشی» (۱م/۵م/۱۵م/۱س — مثل قبل منتشر می‌شود، فقط برای تحلیل برچسب می‌خورد).

محدودیت‌ها (برای بقای رایگان): فاصله‌ی حداقل بین دو درخواست Kraken ≈ 1.2 ثانیه، نتیجه‌ی
«نماد پشتیبانی‌نشده» ۲۴ ساعت کش می‌شود، روند روزانه هر نماد ۶ ساعت کش می‌شود.
"""
import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("shared_market_data")

HTTP_TIMEOUT = 20
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{pid}/candles"

# تایم‌فریم (ثانیه) → interval دقیقه‌ای Kraken
_KRAKEN_INTERVAL = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}
# تایم‌فریم (ثانیه) → granularity مستقیم Coinbase (۴ساعته مستقیم ندارد؛ از ۱ساعته ساخته می‌شود)
_CB_GRAN = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}

# نام‌های جایگزین Kraken (BTC=XBT، DOGE=XDG در برخی نام‌ها)
_KRAKEN_ALIASES = {
    "BTC": ["XBT"],
    "DOGE": ["XDG", "DOGE"],
}

# ---------- فیلتر روند + سطح‌بندی ----------
VERIFIED_TFS = ("4h",)                 # فقط ۴ساعته بک‌تست OOS را با فیلتر روند روزانه پاس کرد
STRATEGY_VERSION = "pin+htf1d-v1"
HTF_EMA_FAST = 20
HTF_EMA_SLOW = 50
HTF_MIN_DAILY_CANDLES = 60
DAILY_TREND_TTL = 6 * 3600
UNSUPPORTED_TTL = 24 * 3600
ASSUMED_FEE_PCT = float(os.environ.get("ASSUMED_ROUNDTRIP_FEE_PCT") or "0.15")   # کارمزد+اسلیپیج رفت‌وبرگشت (٪) — فقط برای تخمین R خالص در گزارش

_lock = threading.Lock()
_last_call = {"kraken": 0.0, "coinbase": 0.0}
_MIN_GAP = {"kraken": 1.2, "coinbase": 0.2}
_unsupported: Dict[str, float] = {}      # "provider|symbol" -> expiry
_daily_cache: Dict[str, Any] = {}        # symbol -> (ts, trend)
_daily_fail_until: Dict[str, float] = {}  # symbol -> ts تا آن زمان منبع رایگان برای روند دوباره صدا زده نمی‌شود
LAST_SOURCE: Dict[str, str] = {}         # symbol|bar -> "kraken"/"coinbase" (برای دیباگ)


def _env_flag(name: str, default: str = "1") -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v == "":
        v = default
    return v not in ("0", "false", "no", "off")


def _throttle(provider: str) -> None:
    with _lock:
        gap = _MIN_GAP[provider] - (time.time() - _last_call[provider])
        if gap > 0:
            time.sleep(gap)
        _last_call[provider] = time.time()


def _base(symbol: str) -> str:
    return symbol.split("/")[0].strip().upper()


def _mk_candle(open_s: float, o: float, h: float, l: float, c: float, v: Optional[float]) -> Dict[str, Any]:
    dt = datetime.fromtimestamp(open_s, tz=timezone.utc)
    return {"open_time": int(open_s * 1000), "dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v}


# ====================================================================== Kraken
def _kraken_fetch(symbol: str, bar_seconds: int) -> List[Dict[str, Any]]:
    interval = _KRAKEN_INTERVAL.get(bar_seconds)
    if interval is None:
        return []
    base = _base(symbol)
    names = [a + "USD" for a in _KRAKEN_ALIASES.get(base, [])] + [base + "USD"]
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n); uniq.append(n)
    now = time.time()
    for pair in uniq:
        ukey = f"kraken|{pair}"
        if _unsupported.get(ukey, 0) > now:
            continue
        for attempt in (1, 2):
            _throttle("kraken")
            try:
                r = requests.get(KRAKEN_URL, params={"pair": pair, "interval": interval}, timeout=HTTP_TIMEOUT)
                if r.status_code == 429:
                    time.sleep(5 * attempt)
                    continue
                data = r.json()
            except Exception as e:
                logger.warning(f"Kraken request failed {pair}: {e}")
                break
            errs = data.get("error") or []
            if errs:
                txt = " ".join(str(x) for x in errs)
                if "Unknown asset pair" in txt or "Invalid" in txt:
                    _unsupported[ukey] = now + UNSUPPORTED_TTL
                    break
                if "Rate limit" in txt or "Too many" in txt:
                    time.sleep(5 * attempt)
                    continue
                logger.warning(f"Kraken error {pair}: {txt}")
                break
            res = data.get("result") or {}
            rows = None
            for k, v in res.items():
                if k != "last" and isinstance(v, list):
                    rows = v
                    break
            if not rows:
                break
            out = []
            for x in rows:
                try:
                    t = float(x[0])
                    if t + bar_seconds > now:        # کندلِ در حال تشکیل
                        continue
                    out.append(_mk_candle(t, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6])))
                except Exception:
                    continue
            if out:
                out.sort(key=lambda k: k["open_time"])
                return out
            break
    return []


# ====================================================================== Coinbase
def _cb_raw(pid: str, gran: int) -> List[List[float]]:
    ukey = f"coinbase|{pid}"
    if _unsupported.get(ukey, 0) > time.time():
        return []
    _throttle("coinbase")
    try:
        r = requests.get(COINBASE_URL.format(pid=pid), params={"granularity": gran}, timeout=HTTP_TIMEOUT)
        if r.status_code == 404:
            _unsupported[ukey] = time.time() + UNSUPPORTED_TTL
            return []
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Coinbase request failed {pid}: {e}")
        return []


def _coinbase_fetch(symbol: str, bar_seconds: int) -> List[Dict[str, Any]]:
    pid = _base(symbol) + "-USD"
    now = time.time()
    if bar_seconds == 14400:
        rows = _cb_raw(pid, 3600)
        buckets: Dict[int, List[List[float]]] = {}
        for x in rows:
            try:
                t = int(x[0])
                buckets.setdefault(t - (t % 14400), []).append(x)
            except Exception:
                continue
        out = []
        for start, items in buckets.items():
            if len(items) < 4 or start + 14400 > now:
                continue        # فقط بازه‌های کامل ۴ساعته (۴ کندل ۱ساعته‌ی کامل)
            items.sort(key=lambda z: z[0])
            out.append(_mk_candle(start, float(items[0][3]), max(float(i[2]) for i in items),
                                  min(float(i[1]) for i in items), float(items[-1][4]),
                                  sum(float(i[5]) for i in items)))
        out.sort(key=lambda k: k["open_time"])
        return out
    gran = _CB_GRAN.get(bar_seconds)
    if gran is None:
        return []
    out = []
    for x in _cb_raw(pid, gran):
        try:
            t = float(x[0])
            if t + bar_seconds > now:
                continue
            out.append(_mk_candle(t, float(x[3]), float(x[2]), float(x[1]), float(x[4]), float(x[5])))
        except Exception:
            continue
    out.sort(key=lambda k: k["open_time"])
    return out


def fetch_klines(symbol: str, limit: int, bar_seconds: int) -> List[Dict[str, Any]]:
    """کندل‌های بسته‌شده (قدیم→جدید)، آخرین `limit` عدد. Kraken اول، Coinbase پشتیبان. شکست = []"""
    for name, fn in (("kraken", _kraken_fetch), ("coinbase", _coinbase_fetch)):
        try:
            candles = fn(symbol, bar_seconds)
        except Exception as e:
            logger.warning(f"{name} fetch crashed for {symbol}: {e}")
            candles = []
        if candles:
            LAST_SOURCE[f"{symbol}|{bar_seconds}"] = name
            return candles[-limit:] if limit else candles
    return []


# ====================================================================== روند روزانه
def _ema_last(values: List[float], span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1 - alpha) * e      # دقیقاً pandas ewm(adjust=False)
    return e


def trend_from_closes(closes: List[float]) -> Optional[int]:
    """+1 اگر Close>EMA50 و EMA20>EMA50؛ -1 برعکس؛ 0 در غیر این صورت؛ None اگر داده کم است."""
    if not closes or len(closes) < HTF_MIN_DAILY_CANDLES:
        return None
    e_fast = _ema_last(closes, HTF_EMA_FAST)
    e_slow = _ema_last(closes, HTF_EMA_SLOW)
    c = closes[-1]
    if c > e_slow and e_fast > e_slow:
        return 1
    if c < e_slow and e_fast < e_slow:
        return -1
    return 0


def fetch_daily_trend(symbol: str, td_daily_fetch=None) -> Optional[int]:
    """روند روزانه‌ی یک نماد (کش ۶ساعته؛ شکست فقط ۱۰ دقیقه کش می‌شود تا قطعی شبکه حلقه را کند نکند).
    td_daily_fetch: تابع اختیاری پشتیبان (مثلاً Twelve Data) که لیست کندل‌های روزانه‌ی بسته‌شده برمی‌گرداند.
    None = ناموجود."""
    now = time.time()
    hit = _daily_cache.get(symbol)
    if hit and now - hit[0] < DAILY_TREND_TTL and hit[1] is not None:
        return hit[1]
    trend = None
    if now >= _daily_fail_until.get(symbol, 0):
        candles = fetch_klines(symbol, 200, 86400)
        trend = trend_from_closes([k["c"] for k in candles]) if candles else None
    if trend is None and td_daily_fetch is not None:
        try:
            candles = td_daily_fetch() or []
            trend = trend_from_closes([k["c"] for k in candles]) if candles else None
        except Exception as e:
            logger.warning(f"daily-trend fallback failed for {symbol}: {e}")
    if trend is None:
        _daily_fail_until[symbol] = now + 600
    else:
        _daily_fail_until.pop(symbol, None)
    _daily_cache[symbol] = (now, trend)
    return trend


# ====================================================================== Tier
def tier_for_tf(tf_key: str) -> str:
    return "verified" if tf_key in VERIFIED_TFS else "experimental"


def tier_gate(symbol: str, tf_key: str, side: str, td_daily_fetch=None) -> Dict[str, Any]:
    """
    خروجی: {"allowed": bool, "quality_tier": "verified"/"experimental", "htf_trend": +1/-1/0/None,
            "htf_aligned": bool/None, "reason": str, "strategy": STRATEGY_VERSION}
    - تأییدشده (۴ساعته): فقط وقتی مجاز است که جهت سیگنال با روند روزانه هم‌جهت باشد.
      اگر روند در دسترس نباشد → مجاز نیست (fail-closed)، چون ادعای «تأییدشده» به همین فیلتر بسته است.
    - آزمایشی (۱م/۵م/۱۵م/۱س): همیشه مجاز (طبق تصمیم صاحب کانال، مثل قبل منتشر می‌شود)؛ فقط هم‌جهتی با
      روند روزانه برای تحلیل‌های بعدی ثبت می‌شود.
    - HTF_GATE_ENABLED=0 فیلتر ۴ساعته را خاموش می‌کند (فقط ثبت می‌شود).
    """
    tier = tier_for_tf(tf_key)
    trend = fetch_daily_trend(symbol, td_daily_fetch)
    want = 1 if side == "BUY" else -1
    aligned = None if trend is None else (trend == want)
    res = {"allowed": True, "quality_tier": tier, "htf_trend": trend, "htf_aligned": aligned,
           "reason": "", "strategy": STRATEGY_VERSION}
    if tier == "verified" and _env_flag("HTF_GATE_ENABLED", "1"):
        if trend is None:
            res["allowed"] = False
            res["reason"] = "htf_unavailable"
        elif not aligned:
            res["allowed"] = False
            res["reason"] = "against_daily_trend"
    return res


def trade_tier_fields(meta: Dict[str, Any], entry: float, sl: float) -> Dict[str, Any]:
    """فیلدهایی که روی خودِ trade ذخیره می‌شوند (و در trade_history می‌آیند)."""
    out = {"quality_tier": meta.get("quality_tier"), "strategy": meta.get("strategy"),
           "htf_trend": meta.get("htf_trend"), "htf_aligned": meta.get("htf_aligned")}
    try:
        stop_pct = abs(entry - sl) / entry * 100.0
        out["stop_pct"] = round(stop_pct, 4)
        out["fee_r_est"] = round(ASSUMED_FEE_PCT / stop_pct, 4) if stop_pct > 0 else None
    except Exception:
        pass
    return out


def free_first_tfs() -> set:
    """تایم‌فریم‌هایی که برای کندل‌ها اول از منبع رایگان خوانده می‌شوند (پیش‌فرض ۱م و ۵م - جایی که سهمیه‌ی
    Twelve Data گلوگاه است). با env FREE_DATA_FIRST_TFS قابل تغییر؛ 'none' = خاموش."""
    raw = (os.environ.get("FREE_DATA_FIRST_TFS") or "1m,5m").strip().lower()
    if raw in ("none", "off", "0"):
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}

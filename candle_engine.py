# -*- coding: utf-8 -*-
"""
Candle Signal Engine v4
========================
(کدنویسی و لاگ‌ها فارسی برای خودتان؛ تمام پیام‌هایی که به کاربر نهایی می‌رود انگلیسی است)

تغییر این نسخه نسبت به قبل:
  - حد ضرر ترکیبی: هرکدام از EMA7 کندل سیگنال یا آخرین سوینگ لو/های (۱۰ کندل قبل)
    که فاصله بیشتر و محافظه‌کارانه‌تری از قیمت ورود داشته باشد انتخاب می‌شود
    (برای لانگ: کمینه‌ی این دو مقدار؛ برای شورت: بیشینه‌ی این دو مقدار)

⚠️ درباره تایم‌فریم‌های 1m و 5m: طبق محدودیت سقف ۸۰۰ درخواست/روز Twelve Data،
این دو تایم‌فریم با تاخیر (به‌ترتیب هر ۳۰ و ۲۰ دقیقه) چک می‌شوند - هیچ سیگنالی از
دست نمی‌رود، فقط ممکن است چند دقیقه دیرتر شناسایی شود. جزئیات در README.
"""

import os
import time
import json
import logging
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("CandleEngineV4")

# ================== تنظیمات ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PRIVATE_CHANNEL_ID = os.getenv("PRIVATE_CHANNEL_ID", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
FORCE_RUN_ALL = os.getenv("FORCE_RUN_ALL", "").strip() == "1"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "candle_state.json")
TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.json")
CHART_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_tmp.png")

HTTP_TIMEOUT = 30
TWELVEDATA_BASE = "https://api.twelvedata.com"

BOOTSTRAP_LIMIT = 300
COOLDOWN_BARS = 5
WHIPSAW_ATR_MULT = 0.5
EMA_SLOPE_ATR_MULT = 0.03
ADX_MIN_THRESHOLD = 20  # زیر این مقدار، بازار «رنج/بی‌روند» در نظر گرفته می‌شود و سیگنال صادر نمی‌شود
CANDLE_BODY_MAX_RATIO = 0.5
SHADOW_RATIO = 3.5
HIST_KEEP = 80          # برای ساخت چارت مستقیم از state (بدون درخواست اضافه)
SWING_LOOKBACK = 10      # چند کندل قبل از کندل سیگنال برای پیدا کردن آخرین سوینگ لو/های
SL_BUFFER_ATR_MULT = 0.3  # بافر اضافه فراتر از EMA7/سوینگ تا حد ضرر دقیقاً روی کف/سقف نباشد
RR_TARGETS = [1, 2, 3, 4]
TARGET_LABELS = {1: "Target 1", 2: "Target 2", 3: "Target 3", 4: "Target 4"}

WATCHLIST_SYMBOLS = {
    "BTC/USD": "BTC",
    "ETH/USD": "ETH",
    "XAU/USD": "GOLD",
}

TIMEFRAMES = {
    # ⚠️ 1m و 5m عمداً حذف شدند: با سقف رایگان Twelve Data (۸۰۰ درخواست/روز) غیرممکنه این دو
    # واقعاً لحظه‌ای باشن (حتی با تاخیر ۲۰-۳۰ دقیقه‌ای هم بودجه رو کامل می‌بلعیدن). به‌جاش این
    # بودجه صرف رهگیری قیمت زنده‌ی معاملات باز شده (پایین‌تر، LIVE_CHECK_INTERVAL_SECONDS)
    # که دقت واقعی خیلی بیشتری به هدف اصلی (تشخیص دقیق لحظه‌ی خوردن استاپ/تارگت) می‌ده.
    "15m": {"td_interval": "15min", "bar_seconds": 15 * 60,      "label": "15M"},
    "1h":  {"td_interval": "1h",    "bar_seconds": 60 * 60,      "label": "1H"},
    "4h":  {"td_interval": "4h",    "bar_seconds": 4 * 60 * 60,  "label": "4H"},
    "1d":  {"td_interval": "1day",  "bar_seconds": 24 * 60 * 60, "label": "1D"},
}


TF_CHECK_INTERVAL_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}

LIVE_CHECK_INTERVAL_SECONDS = 15 * 60  # هر ۱۵ دقیقه، فقط برای نمادهایی با معامله‌ی باز
MAX_LIVE_CHECKS_PER_RUN = 6  # سقف تعداد چک زنده در هر اجرا، برای محافظت از بودجه در بدترین حالت


def is_timeframe_due(tf_key: str, now_ts_: int, last_checked: Dict[str, int]) -> bool:
    """
    نسخه‌ی مقاوم در برابر تاخیر واقعی گیت‌هاب اکشنز: گیت‌هاب رسماً تضمین نمی‌کند کرون
    دقیقاً سر زمان تعیین‌شده اجرا شود (گاهی چند دقیقه دیرتر). به همین دلیل به‌جای تطبیق
    دقیق دقیقه/ساعت، بر اساس «چقدر زمان از آخرین چک این تایم‌فریم گذشته» تصمیم می‌گیریم.
    """
    if FORCE_RUN_ALL:
        return True
    interval = TF_CHECK_INTERVAL_SECONDS[tf_key]
    last = last_checked.get(tf_key, 0)
    return (now_ts_ - last) >= interval


# ================== ذخیره‌سازی وضعیت ==================

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"خطا در خواندن state: {e}")
    return {}


def save_state(state: Dict[str, Any]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


CLOSE_TYPE_LABELS = {
    "stop": "Stopped out (full loss)",
    "breakeven": "Breakeven (Target 1-2 banked, rest flat)",
    "trailing_stop": "Trailing stop (Target 1-3 banked)",
    "runner_stop": "Runner closed (all 4 targets + trailing runner)",
    "opposite_signal": "Closed early - opposite signal appeared",
}


def log_trade_result(symbol: str, tf_key: str, trade: Dict[str, Any]):
    """وقتی معامله بسته می‌شود (استاپ/بریک‌ایون/تارگت نهایی/سیگنال مخالف)، نتیجه‌ی شفاف و
    مشخص آن (چطور بسته شده + چند R) را برای آمار/سود‌وزیان کاربران ذخیره می‌کند."""
    os.makedirs(DATA_DIR, exist_ok=True)
    history = []
    if os.path.exists(TRADE_HISTORY_FILE):
        try:
            with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    close_type = trade.get("close_type", "unknown")
    history.append({
        "symbol": symbol, "tf": tf_key, "side": trade["side"],
        "entry": trade["entry"], "sl": trade["sl"],
        "final_r": compute_final_r(trade),
        "close_type": close_type,
        "close_reason": CLOSE_TYPE_LABELS.get(close_type, close_type),
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })
    history = history[-2000:]  # جلوگیری از رشد بی‌نهایت فایل
    with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ================== تلگرام ==================

def send_photo(caption: str, photo_path: Optional[str], reply_to_message_id: Optional[int] = None) -> Optional[int]:
    """پیام (عکس یا متن) رو می‌فرسته و در صورت موفقیت، message_id تلگرام رو برمی‌گردونه
    (برای اینکه پیام‌های استاپ/تارگت بعدی بتونن روی پیام سیگنال اصلی ریپلای بزنن).
    در صورت شکست None برمی‌گردونه."""
    if not TELEGRAM_BOT_TOKEN or not PRIVATE_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN or PRIVATE_CHANNEL_ID not set")
        return None
    try:
        data = {"chat_id": PRIVATE_CHANNEL_ID, "parse_mode": "HTML"}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
            data["allow_sending_without_reply"] = True
        if photo_path and os.path.exists(photo_path):
            data["caption"] = caption
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                    data=data, files={"photo": f}, timeout=60,
                )
        else:
            data["text"] = caption
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data=data, timeout=30,
            )
        result = resp.json()
        ok = resp.status_code == 200 and result.get("ok")
        if not ok:
            logger.error(f"Telegram send error: {resp.text}")
            return None
        return result["result"]["message_id"]
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")
        return None


# ================== داده Twelve Data ==================

def fetch_closed_klines(symbol: str, limit: int, interval: str, bar_seconds: int) -> List[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        return []
    url = f"{TWELVEDATA_BASE}/time_series"
    params = {"symbol": symbol, "interval": interval, "outputsize": limit, "timezone": "UTC", "apikey": TWELVEDATA_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        data = r.json()
    except Exception as e:
        logger.warning(f"Twelve Data request failed for {symbol} [{interval}]: {e}")
        return []
    if not isinstance(data, dict) or data.get("status") == "error" or "values" not in data:
        logger.warning(f"Twelve Data returned no data for {symbol} [{interval}]: {data}")
        return []
    now = time.time()
    candles = []
    for v in data["values"]:
        try:
            dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if dt.timestamp() + bar_seconds > now:
                continue
            candles.append({
                "open_time": int(dt.timestamp() * 1000), "dt": dt,
                "o": float(v["open"]), "h": float(v["high"]), "l": float(v["low"]), "c": float(v["close"]),
            })
        except Exception:
            continue
    candles.sort(key=lambda k: k["open_time"])
    return candles


def fetch_live_price(symbol: str) -> Optional[float]:
    """قیمت لحظه‌ای (نه کندل بسته‌شده) رو از اندپوینت سبک /price می‌گیره - برای دقت لحظه‌ی
    انتشار سیگنال و رهگیری زنده‌ی معاملات باز. هزینه‌ی این تماس هم مثل بقیه، ۱ کردیت است."""
    if not TWELVEDATA_API_KEY:
        return None
    url = f"{TWELVEDATA_BASE}/price"
    params = {"symbol": symbol, "apikey": TWELVEDATA_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        data = r.json()
        return float(data["price"])
    except Exception as e:
        logger.warning(f"fetch_live_price failed for {symbol}: {e}")
        return None


# ================== منطق کندل سیگنال ==================

def new_candle_state() -> Dict[str, Any]:
    return {
        "ema7": None, "ema25": None, "atr": None, "tr_buffer": [],
        "plus_dm14": None, "minus_dm14": None, "dm_buffer": [],
        "adx": None, "dx_buffer": [],
        "hist": [], "trend_prev": "flat",
        "bull_used_this_trend": False, "bear_used_this_trend": False,
        "last_bull_bar_index": None, "last_bear_bar_index": None,
        "last_signal_price": None, "bar_index": 0, "last_open_time": None,
        "open_trade": None,
    }


def _ema_step(prev: Optional[float], price: float, length: int) -> float:
    if prev is None:
        return price
    alpha = 2.0 / (length + 1)
    return alpha * price + (1 - alpha) * prev


def step_candle_state(state: Dict[str, Any], o: float, h: float, l: float, c: float, open_time: int):
    s = dict(state)
    s["hist"] = list(s["hist"])
    s["tr_buffer"] = list(s["tr_buffer"])
    s["dm_buffer"] = list(s.get("dm_buffer", []))
    s["dx_buffer"] = list(s.get("dx_buffer", []))

    ema7_prev, ema25_prev = s["ema7"], s["ema25"]
    ema7_i = _ema_step(ema7_prev, c, 7)
    ema25_i = _ema_step(ema25_prev, c, 25)

    hist = s["hist"]
    prev_bar = hist[-1] if len(hist) >= 1 else None
    prev2_bar = hist[-2] if len(hist) >= 2 else None

    tr_i = max(h - l, abs(h - prev_bar["c"]), abs(l - prev_bar["c"])) if prev_bar else (h - l)
    if s["atr"] is None:
        s["tr_buffer"].append(tr_i)
        atr_i = sum(s["tr_buffer"][-14:]) / 14.0 if len(s["tr_buffer"]) >= 14 else None
    else:
        atr_i = (s["atr"] * 13 + tr_i) / 14.0

    # ---- ADX (قدرت روند) - برای فیلترکردن سیگنال در بازار رنج/بی‌روند ----
    if prev_bar is not None:
        up_move = h - prev_bar["h"]
        down_move = prev_bar["l"] - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
    else:
        plus_dm = minus_dm = 0.0

    if s["plus_dm14"] is None:
        s["dm_buffer"].append((plus_dm, minus_dm))
        if len(s["dm_buffer"]) >= 14:
            plus_dm14 = sum(x[0] for x in s["dm_buffer"][-14:])
            minus_dm14 = sum(x[1] for x in s["dm_buffer"][-14:])
        else:
            plus_dm14 = minus_dm14 = None
    else:
        plus_dm14 = s["plus_dm14"] - (s["plus_dm14"] / 14.0) + plus_dm
        minus_dm14 = s["minus_dm14"] - (s["minus_dm14"] / 14.0) + minus_dm

    adx_i = s["adx"]
    if plus_dm14 is not None and atr_i:
        plus_di = 100 * (plus_dm14 / 14.0) / atr_i if atr_i > 0 else 0
        minus_di = 100 * (minus_dm14 / 14.0) / atr_i if atr_i > 0 else 0
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
        if s["adx"] is None:
            s["dx_buffer"].append(dx)
            adx_i = sum(s["dx_buffer"][-14:]) / 14.0 if len(s["dx_buffer"]) >= 14 else None
        else:
            adx_i = (s["adx"] * 13 + dx) / 14.0

    body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    total_size = h - l

    is_uptrend = ema7_i > ema25_i
    is_downtrend = ema7_i < ema25_i
    trend_is_strong = True if adx_i is None else adx_i >= ADX_MIN_THRESHOLD

    is_valid_bull_candle = (body < CANDLE_BODY_MAX_RATIO * total_size) and (lower_shadow > SHADOW_RATIO * upper_shadow)
    is_valid_bear_candle = (body < CANDLE_BODY_MAX_RATIO * total_size) and (upper_shadow > SHADOW_RATIO * lower_shadow)

    next_invalidates_bull = (prev_bar is not None) and (prev_bar["l"] < l)
    next_invalidates_bear = (prev_bar is not None) and (prev_bar["h"] > h)

    ema7_slope = ema7_i - (ema7_prev if ema7_prev is not None else ema7_i)
    is_ema7_flat = True if atr_i is None else abs(ema7_slope) < (EMA_SLOPE_ATR_MULT * atr_i)

    bullish_engulf = bearish_engulf = False
    if prev_bar is not None:
        po, pc = prev_bar["o"], prev_bar["c"]
        bullish_engulf = (c > o) and (pc < po) and (c > po) and (o < pc)
        bearish_engulf = (c < o) and (pc > po) and (c < po) and (o > pc)

    bullish_pin = (lower_shadow > 2 * body) and (upper_shadow < body)
    bearish_pin = (upper_shadow > 2 * body) and (lower_shadow < body)

    both_above = both_below = False
    if prev_bar is not None and prev2_bar is not None:
        both_above = (prev_bar["c"] > prev_bar["ema7"]) and (prev2_bar["c"] > prev2_bar["ema7"])
        both_below = (prev_bar["c"] < prev_bar["ema7"]) and (prev2_bar["c"] < prev2_bar["ema7"])

    raw_bull = is_uptrend and trend_is_strong and is_valid_bull_candle and (not next_invalidates_bull) and (not is_ema7_flat) and both_above
    raw_bear = is_downtrend and trend_is_strong and is_valid_bear_candle and (not next_invalidates_bear) and (not is_ema7_flat) and both_below

    if is_uptrend and s["trend_prev"] != "up":
        s["bull_used_this_trend"] = False
    if is_downtrend and s["trend_prev"] != "down":
        s["bear_used_this_trend"] = False

    state_ok_bull = not s["bull_used_this_trend"]
    state_ok_bear = not s["bear_used_this_trend"]

    cooldown_ok_bull = (s["last_bull_bar_index"] is None) or (s["bar_index"] - s["last_bull_bar_index"] >= COOLDOWN_BARS)
    cooldown_ok_bear = (s["last_bear_bar_index"] is None) or (s["bar_index"] - s["last_bear_bar_index"] >= COOLDOWN_BARS)

    if s["last_signal_price"] is None or atr_i is None:
        price_move_ok = True
    else:
        price_move_ok = abs(c - s["last_signal_price"]) >= atr_i * WHIPSAW_ATR_MULT

    final_bull = raw_bull and state_ok_bull and cooldown_ok_bull and price_move_ok
    final_bear = raw_bear and state_ok_bear and cooldown_ok_bear and price_move_ok

    signal = None
    swing_window = hist[-SWING_LOOKBACK:] if hist else []

    if final_bull:
        s["bull_used_this_trend"] = True
        s["last_bull_bar_index"] = s["bar_index"]
        s["last_signal_price"] = c
        swing_low = min([b["l"] for b in swing_window]) if swing_window else l
        raw_sl = min(ema7_i, swing_low)  # فاصله بیشتر و محافظه‌کارانه‌تر از این دو
        buffer = SL_BUFFER_ATR_MULT * atr_i if atr_i else abs(c) * 0.001
        sl = raw_sl - buffer  # کمی پایین‌تر تا دقیقاً روی کف نباشد
        signal = {"side": "BUY", "confirmed": bool(bullish_engulf or bullish_pin), "price": c,
                  "open_time": open_time, "sl": sl}
    if final_bear:
        s["bear_used_this_trend"] = True
        s["last_bear_bar_index"] = s["bar_index"]
        s["last_signal_price"] = c
        swing_high = max([b["h"] for b in swing_window]) if swing_window else h
        raw_sl = max(ema7_i, swing_high)
        buffer = SL_BUFFER_ATR_MULT * atr_i if atr_i else abs(c) * 0.001
        sl = raw_sl + buffer  # کمی بالاتر تا دقیقاً روی سقف نباشد
        signal = {"side": "SELL", "confirmed": bool(bearish_engulf or bearish_pin), "price": c,
                  "open_time": open_time, "sl": sl}

    hist.append({"o": o, "h": h, "l": l, "c": c, "ema7": ema7_i, "dt_ms": open_time})
    s["hist"] = hist[-HIST_KEEP:]
    s["ema7"], s["ema25"], s["atr"] = ema7_i, ema25_i, atr_i
    s["plus_dm14"], s["minus_dm14"], s["adx"] = plus_dm14, minus_dm14, adx_i
    s["trend_prev"] = "up" if is_uptrend else ("down" if is_downtrend else "flat")
    s["bar_index"] = s["bar_index"] + 1
    s["last_open_time"] = open_time

    return s, signal


# ================== ردیابی معامله باز و سطوح R:R ==================
#
# مدیریت پوزیشن (طبق درخواست شما):
#   Target 1 (R=1): بستن ۵۰٪ حجم پوزیشن
#   Target 2 (R=2): بستن ۵۰٪ از حجم باقی‌مانده + انتقال حد ضرر به نقطه ورود (ریسک‌فری)
#   Target 3 (R=3): بستن ۷۰٪ از حجم باقی‌مانده + شروع تریل‌کردن حد ضرر
#   Target 4 (R=4): بستن ۸۵٪ از حجم باقی‌مانده + اجازه دادن به ۱۵٪ باقی («Runner») که با
#                   همون تریلینگ‌استاپ باز بمونه تا اگه حرکت بزرگ‌تر از ۴R هم رخ داد،
#                   بخشی از سودش گرفته بشه - به‌جای بستن کامل و از دست دادن حرکت‌های بزرگ‌تر

def open_new_trade(signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = signal["price"]
    sl = signal["sl"]
    r = abs(entry - sl)
    if r <= 0:
        return None
    return {
        "side": signal["side"], "entry": entry, "sl": sl, "r": r,
        "hit": {str(t): False for t in RR_TARGETS}, "trailing": False, "closed": False,
    }


# وزن هر مرحله از حجم اصلی پوزیشن
_W1, _W2, _W3 = 0.5, 0.25, 0.175
_REMAINDER_AT_T4 = 1 - _W1 - _W2 - _W3      # 0.075 از حجم اصلی به تارگت ۴ می‌رسه
_RUNNER_KEEP_FRACTION = 0.15                 # ۱۵٪ از همون باقی‌مانده به‌عنوان Runner باز می‌مونه
_W4 = _REMAINDER_AT_T4 * (1 - _RUNNER_KEEP_FRACTION)   # بسته‌شده در Target 4 ≈ 0.06375
_W_RUNNER = _REMAINDER_AT_T4 * _RUNNER_KEEP_FRACTION    # باز مونده به‌عنوان Runner ≈ 0.01125


def compute_final_r(trade: Dict[str, Any]) -> float:
    """تخمین ساده‌ی نتیجه‌ی نهایی معامله بر حسب R، بر اساس درصد بسته‌شدن پوزیشن در هر مرحله.
    برای بخش تریلینگ/Runner که قیمت خروج دقیقش از قبل معلوم نیست، یک تخمین محافظه‌کارانه
    (نه بیشتر از واقعیت) در نظر گرفته می‌شود."""
    hit = trade["hit"]
    if not hit["1"]:
        return -1.0  # هیچ تارگتی نخورده، کل پوزیشن با ضرر کامل بسته شده
    if not hit["2"]:
        return _W1 * 1 + (1 - _W1) * (-1.0)  # نیمه‌ی اول با سود، نیمه‌ی دوم با حد ضرر اصلی
    if not hit["3"]:
        return _W1 * 1 + _W2 * 2 + (1 - _W1 - _W2) * 0.0  # باقی‌مانده در ریسک‌فری بسته شده
    if not hit["4"]:
        return _W1 * 1 + _W2 * 2 + _W3 * 3 + _REMAINDER_AT_T4 * 3.0  # تریلینگ قبل از تارگت ۴ متوقف شده
    # به Target 4 رسیده: بخش اصلی همون‌جا بسته شده + Runner با تخمین محافظه‌کارانه در همون سطح
    return _W1 * 1 + _W2 * 2 + _W3 * 3 + _W4 * 4.0 + _W_RUNNER * 4.0


def check_open_trade(trade: Dict[str, Any], candle: Dict[str, Any], ema7_now: Optional[float]) -> List[Dict[str, Any]]:
    if trade is None or trade.get("closed"):
        return []
    events = []
    side, entry, r = trade["side"], trade["entry"], trade["r"]

    # تریل‌کردن حد ضرر (فقط بعد از Target 3، و فقط در جهت سودآور - هیچ‌وقت به ضرر معامله حرکت نمی‌کند)
    if trade.get("trailing") and ema7_now is not None:
        if side == "BUY":
            trade["sl"] = max(trade["sl"], ema7_now)
        else:
            trade["sl"] = min(trade["sl"], ema7_now)

    sl = trade["sl"]

    def stop_event_type():
        if trade["hit"]["4"]:
            return "runner_stop"       # همه‌ی تارگت‌ها خورده، فقط Runner باقی بود
        if trade.get("trailing"):
            return "trailing_stop"     # بعد از Target 3، قبل از Target 4
        if abs(sl - entry) < 1e-9:
            return "breakeven"
        return "stop"

    if side == "BUY":
        if candle["l"] <= sl:
            trade["closed"] = True
            trade["close_type"] = stop_event_type()
            events.append({"type": trade["close_type"], "price": sl})
            return events
        for target in RR_TARGETS:
            key = str(target)
            if not trade["hit"][key] and candle["h"] >= entry + target * r:
                trade["hit"][key] = True
                events.append({"type": "rr", "level": target, "price": entry + target * r})
                if target == 2:
                    trade["sl"] = entry  # ریسک‌فری
                if target == 3:
                    trade["trailing"] = True
                # توجه: در Target 4 دیگر trade را کامل نمی‌بندیم؛ ۱۵٪ Runner با
                # تریلینگ‌استاپ باز می‌ماند تا خودش بعداً با رویداد stop بسته شود.
    else:
        if candle["h"] >= sl:
            trade["closed"] = True
            trade["close_type"] = stop_event_type()
            events.append({"type": trade["close_type"], "price": sl})
            return events
        for target in RR_TARGETS:
            key = str(target)
            if not trade["hit"][key] and candle["l"] <= entry - target * r:
                trade["hit"][key] = True
                events.append({"type": "rr", "level": target, "price": entry - target * r})
                if target == 2:
                    trade["sl"] = entry
                if target == 3:
                    trade["trailing"] = True

    return events


def check_open_trade_live(trade: Dict[str, Any], live_price: float) -> List[Dict[str, Any]]:
    """نسخه‌ی سبک check_open_trade که فقط یک قیمت لحظه‌ای (نه کندل کامل) داره - برای
    رهگیری زنده‌ی معامله‌ی باز بین دو بسته‌شدن کندل. منطق (تارگت/استاپ/ریسک‌فری/تریلینگ)
    دقیقاً همون check_open_trade است، فقط به‌جای high/low از یک نقطه‌ی قیمتی استفاده می‌کند."""
    fake_candle = {"o": live_price, "h": live_price, "l": live_price, "c": live_price}
    return check_open_trade(trade, fake_candle, ema7_now=None)


# ================== چارت (از تاریخچه‌ی موجود در state، بدون درخواست اضافه) ==================

def build_chart_from_hist(hist: List[Dict[str, Any]], title: str, trade: Optional[Dict[str, Any]] = None,
                           live_price: Optional[float] = None) -> Optional[str]:
    if len(hist) < 15:
        return None
    hist_to_plot = list(hist)
    if live_price:
        # یک کندل لحظه‌ای اضافه می‌کنیم تا آخرین نقطه‌ی چارت دقیقاً همون قیمتی باشه که
        # باعث فعال‌شدن این رویداد (تارگت/استاپ) شده، نه آخرین کندلِ بسته‌شده که ممکنه
        # چند دقیقه قدیمی‌تر باشه.
        hist_to_plot.append({
            "o": live_price, "h": live_price, "l": live_price, "c": live_price,
            "ema7": hist_to_plot[-1]["ema7"], "dt_ms": int(time.time() * 1000),
        })
    df = pd.DataFrame(hist_to_plot)
    df["dt"] = pd.to_datetime(df["dt_ms"], unit="ms", utc=True)
    df = df.set_index("dt")
    ohlc = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["open", "high", "low", "close"]]
    apds = [mpf.make_addplot(df["ema7"], color="dodgerblue", width=1)]

    hlines_vals, hlines_colors, labels = [], [], []
    if trade:
        sign = 1 if trade["side"] == "BUY" else -1
        hlines_vals.append(trade["entry"]); hlines_colors.append("blue"); labels.append(("Entry", trade["entry"], "blue"))
        hlines_vals.append(trade["sl"]); hlines_colors.append("red"); labels.append(("Stop", trade["sl"], "red"))
        for t in RR_TARGETS:
            lvl = trade["entry"] + sign * t * trade["r"]
            hlines_vals.append(lvl); hlines_colors.append("green")
            labels.append((TARGET_LABELS[t], lvl, "green"))

    try:
        plot_kwargs = dict(type="candle", style="charles", addplot=apds, title=title, volume=False, returnfig=True)
        if hlines_vals:
            plot_kwargs["hlines"] = dict(hlines=hlines_vals, colors=hlines_colors, linestyle="--", linewidths=0.8)
        fig, axlist = mpf.plot(ohlc, **plot_kwargs)
        ax = axlist[0]
        x_right = len(ohlc) - 1
        if live_price:
            ax.annotate("Live", xy=(x_right, live_price), xytext=(5, 12), textcoords="offset points",
                        color="darkorange", fontsize=8, va="center", fontweight="bold")
            ax.scatter([x_right], [live_price], color="darkorange", s=25, zorder=5)
        for name, val, color in labels:
            ax.annotate(name, xy=(x_right, val), xytext=(5, 0), textcoords="offset points",
                        color=color, fontsize=8, va="center", fontweight="bold")
        fig.savefig(CHART_PATH, dpi=150, bbox_inches="tight")
        return CHART_PATH
    except Exception as e:
        logger.warning(f"Chart build failed: {e}")
        return None


# ================== پیام‌ها (انگلیسی، ساده و مینیمال) ==================

RISK_LINE = "\n\n⚠️ Please manage your risk and capital appropriately."

TARGET_ACTION_LINE = {
    1: "\n\n✂️ Close 50% of your position here.",
    2: "\n\n✂️ Close 50% of your remaining position, and move your stop-loss to entry (risk-free).",
    3: "\n\n✂️ Close 70% of your remaining position, and start trailing your stop-loss.",
    4: "\n\n✂️ Close 85% of your remaining position. Let the final 15% run with your trailing "
       "stop — no fixed exit, so a bigger move keeps paying.",
}


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_entry_message(display: str, tf_label: str, signal: Dict[str, Any], trade: Dict[str, Any]) -> str:
    arrow = "🟢 LONG" if signal["side"] == "BUY" else "🔴 SHORT"
    sign = 1 if signal["side"] == "BUY" else -1
    targets_lines = "\n".join([f"🎯 {TARGET_LABELS[t]}: {trade['entry'] + sign * t * trade['r']:.2f}" for t in RR_TARGETS])
    return (
        f"{arrow} — {display} {tf_label}\n\n"
        f"Entry: <b>{signal['price']:.2f}</b>\n"
        f"❌ Stop: <b>{trade['sl']:.2f}</b>\n"
        f"{targets_lines}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_rr_exit_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    level = event["level"]
    label = TARGET_LABELS[level]
    action = TARGET_ACTION_LINE.get(level, "")
    return (
        f"✅ {label} HIT — {display} {tf_label}\n\n"
        f"{label} reached on this {direction} trade.\n"
        f"Entry {trade['entry']:.2f}  ·  Now {event['price']:.2f}\n\n"
        f"{_now_str()}"
        f"{action}"
        f"{RISK_LINE}"
    )


def format_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"❌ STOP HIT — {display} {tf_label}\n\n"
        f"Stop-loss hit on this {direction} trade — full remaining position closed.\n"
        f"Entry was {trade['entry']:.2f}  ·  Stop {event['price']:.2f}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_breakeven_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"⚪ BREAKEVEN — {display} {tf_label}\n\n"
        f"Price returned to entry on this {direction} trade — remaining position closed with no loss "
        f"(50% was already banked earlier at Target 1 and Target 2).\n"
        f"Entry {trade['entry']:.2f}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_trailing_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🔒 TRAILING STOP HIT — {display} {tf_label}\n\n"
        f"Your trailing stop was hit on this {direction} trade — remaining position closed, locking in profit.\n"
        f"Entry {trade['entry']:.2f}  ·  Closed at {event['price']:.2f}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_runner_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🏁 RUNNER CLOSED — {display} {tf_label}\n\n"
        f"All 4 targets were already banked on this {direction} trade — the final 15% "
        f"runner portion just closed on its trailing stop. Trade fully complete.\n"
        f"Entry {trade['entry']:.2f}  ·  Runner closed at {event['price']:.2f}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_forced_close_message(display: str, tf_label: str, trade: Dict[str, Any], exit_price: float) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    opposite = "SHORT" if trade["side"] == "BUY" else "LONG"
    result_r = compute_final_r(trade)
    result_word = "profit" if result_r > 0 else ("breakeven" if result_r == 0 else "loss")
    return (
        f"⚠️ TRADE CLOSED — {display} {tf_label}\n\n"
        f"This is <b>not a hedge</b> — the trend reversed, so a new {opposite} signal is "
        f"replacing this {direction} trade. Closing it now at market so nothing is left ambiguous.\n"
        f"Entry {trade['entry']:.2f}  ·  Closed at {exit_price:.2f}\n"
        f"Result: ~{result_r:+.2f}R ({result_word})\n\n"
        f"👉 A new signal for this reversal follows right after this message.\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


# ================== حلقه اصلی ==================

def apply_live_entry_price(symbol: str, sig: Dict[str, Any], trade: Dict[str, Any]) -> None:
    """قیمت ورود رو با یک کوئری قیمت زنده به‌روز می‌کنه (نه قیمت close کندل که ممکنه چند
    دقیقه قدیمی باشه)، و R/تارگت‌ها رو متناسب با همون قیمت واقعی دوباره حساب می‌کنه.
    اگه قیمت زنده در دسترس نبود، همون قیمت کندل به‌عنوان جایگزین حفظ می‌شه."""
    live = fetch_live_price(symbol)
    if live and live > 0:
        sig["price"] = live
        trade["entry"] = live
        trade["r"] = abs(live - trade["sl"])


def process_symbol_timeframe(symbol: str, display: str, tf_key: str, tf_cfg: Dict[str, Any],
                              candle_states: Dict[str, Any]) -> None:
    state_key = f"{symbol}|{tf_key}"
    sym_state = candle_states.get(state_key)

    if sym_state is None:
        candles = fetch_closed_klines(symbol, BOOTSTRAP_LIMIT, tf_cfg["td_interval"], tf_cfg["bar_seconds"])
        if len(candles) < 30:
            logger.warning(f"Not enough data for {symbol} [{tf_key}]")
            return
        state = new_candle_state()
        last_idx = len(candles) - 1
        for idx, k in enumerate(candles):
            state, sig = step_candle_state(state, k["o"], k["h"], k["l"], k["c"], k["open_time"])
            if idx == last_idx and sig:
                trade = open_new_trade(sig)
                if trade:
                    apply_live_entry_price(symbol, sig, trade)
                    state["open_trade"] = trade
                    _send_entry(display, tf_key, tf_cfg, sig, trade, state["hist"])
        candle_states[state_key] = state
        return

    last_open_time = sym_state.get("last_open_time")
    candles = fetch_closed_klines(symbol, 10, tf_cfg["td_interval"], tf_cfg["bar_seconds"])
    new_candles = [k for k in candles if last_open_time is None or k["open_time"] > last_open_time]

    state = sym_state
    for k in new_candles:
        # اول اندیکاتور همین کندل رو پردازش می‌کنیم تا EMA7 تازه‌اش برای تریل‌کردن در دسترس باشه
        state, sig = step_candle_state(state, k["o"], k["h"], k["l"], k["c"], k["open_time"])
        ema7_now = state.get("ema7")

        if state.get("open_trade"):
            events = check_open_trade(state["open_trade"], k, ema7_now)
            for ev in events:
                _send_exit(display, tf_key, tf_cfg, state["open_trade"], ev, state["hist"], symbol)

        if sig:
            # اگه معامله‌ی قبلی هنوز باز بود (چون هنوز به استاپ/تارگت نخورده)، قبل از باز کردن
            # معامله‌ی جدید مخالف‌جهت، حتماً باید اول رسماً بسته بشه - وگرنه بی‌سروصدا فراموش
            # می‌شد و کاربر گیج می‌موند که چرا یهو سیگنال مخالف اومده بدون توضیح.
            prev_trade = state.get("open_trade")
            if prev_trade and not prev_trade.get("closed"):
                _force_close_trade(display, tf_key, tf_cfg, prev_trade, k, state["hist"], symbol)

            trade = open_new_trade(sig)
            if trade:
                apply_live_entry_price(symbol, sig, trade)
                state["open_trade"] = trade
                _send_entry(display, tf_key, tf_cfg, sig, trade, state["hist"])

    candle_states[state_key] = state


def _force_close_trade(display, tf_key, tf_cfg, trade, candle, hist, symbol):
    """وقتی سیگنال جدیدِ مخالف‌جهت میاد ولی معامله‌ی قبلی هنوز باز بود، این تابع اون رو
    رسماً با یک پیام شفاف می‌بندد (نه اینکه بی‌سروصدا فراموش بشه)."""
    trade["closed"] = True
    trade["close_type"] = "opposite_signal"
    exit_price = candle["c"]
    chart = build_chart_from_hist(hist, f"{display} {tf_cfg['label']} · Closed (new signal)", trade=trade)
    msg = format_forced_close_message(display, tf_cfg["label"], trade, exit_price)
    reply_id = trade.get("signal_message_id")
    if send_photo(msg, chart, reply_to_message_id=reply_id):
        logger.info(f"📤 Forced-close sent: {display} [{tf_key}] (opposite signal)")
    log_trade_result(symbol, tf_key, trade)
    time.sleep(1.5)


def _send_entry(display, tf_key, tf_cfg, sig, trade, hist):
    chart = build_chart_from_hist(hist, f"{display} {tf_cfg['label']} · Entry", trade=trade)
    msg = format_entry_message(display, tf_cfg["label"], sig, trade)
    msg_id = send_photo(msg, chart)
    if msg_id:
        trade["signal_message_id"] = msg_id
        logger.info(f"📤 Entry sent: {display} [{tf_key}] {sig['side']}")
    time.sleep(1.5)


def _send_exit(display, tf_key, tf_cfg, trade, event, hist, symbol, live_price=None):
    if event["type"] == "stop":
        title_suffix = "Stop"
    elif event["type"] == "breakeven":
        title_suffix = "Breakeven"
    elif event["type"] == "trailing_stop":
        title_suffix = "Trailing Stop"
    elif event["type"] == "runner_stop":
        title_suffix = "Runner Closed"
    else:
        title_suffix = TARGET_LABELS[event["level"]]
    title = f"{display} {tf_cfg['label']} · {title_suffix}"
    chart = build_chart_from_hist(hist, title, trade=trade, live_price=live_price)
    if event["type"] == "stop":
        msg = format_stop_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "breakeven":
        msg = format_breakeven_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "trailing_stop":
        msg = format_trailing_stop_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "runner_stop":
        msg = format_runner_stop_message(display, tf_cfg["label"], trade, event)
    else:
        msg = format_rr_exit_message(display, tf_cfg["label"], trade, event)
    reply_id = trade.get("signal_message_id")
    if send_photo(msg, chart, reply_to_message_id=reply_id):
        logger.info(f"📤 Exit sent: {display} [{tf_key}] {event['type']}")
    if trade.get("closed"):
        log_trade_result(symbol, tf_key, trade)
    time.sleep(1.5)



MAX_RUN_SECONDS = 240  # بودجه‌ی زمانی هر اجرا (زیر فاصله‌ی ۵ دقیقه‌ای کرون) تا هیچ‌وقت با اجرای بعدی تداخل نکنه


def main():
    if not TELEGRAM_BOT_TOKEN or not PRIVATE_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN or PRIVATE_CHANNEL_ID not set - exiting")
        return
    if not TWELVEDATA_API_KEY:
        logger.error("TWELVEDATA_API_KEY not set - exiting")
        return

    run_start = time.time()
    now_ts_ = int(datetime.now(timezone.utc).timestamp())
    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    last_checked = state.setdefault("tf_last_checked", {})

    due_tfs = [tf for tf in TIMEFRAMES if is_timeframe_due(tf, now_ts_, last_checked)]
    logger.info(f"Due timeframes this run: {due_tfs or '(none)'}")

    stopped_early = False
    for tf_key in due_tfs:
        tf_cfg = TIMEFRAMES[tf_key]
        for symbol, display in WATCHLIST_SYMBOLS.items():
            if time.time() - run_start > MAX_RUN_SECONDS:
                logger.warning("Time budget reached for this run - saving progress and stopping early "
                                "(remaining work will continue next run).")
                stopped_early = True
                break
            try:
                process_symbol_timeframe(symbol, display, tf_key, tf_cfg, candle_states)
                time.sleep(8)  # رعایت محدودیت نرخ درخواست Twelve Data (۸ درخواست/دقیقه در پلن رایگان)
            except Exception as e:
                logger.error(f"Error processing {symbol} [{tf_key}]: {e}")
        if stopped_early:
            break
        # فقط وقتی همه‌ی نمادهای این تایم‌فریم کامل پردازش شدن، آن را «چک‌شده» علامت بزن
        last_checked[tf_key] = now_ts_
        save_state(state)  # ذخیره‌ی تدریجی تا در صورت قطع‌شدن اجرا، پیشرفت از دست نرود

    save_state(state)
    if stopped_early:
        logger.info("⏸️ Run paused early (time budget) - will resume remaining timeframes next run")
        return  # اگه بودجه‌ی این اجرا تموم شد، رهگیری زنده رو به اجرای بعدی موکول می‌کنیم

    # ================== رهگیری زنده‌ی معاملات باز (دقت لحظه‌ای برای استاپ/تارگت) ==================
    live_last_checked = state.setdefault("live_last_checked", {})
    open_combos = [key for key, s in candle_states.items() if s.get("open_trade") and not s["open_trade"].get("closed")]
    due_live = [key for key in open_combos if (now_ts_ - live_last_checked.get(key, 0)) >= LIVE_CHECK_INTERVAL_SECONDS]
    # سقف ایمنی: حتی در بدترین حالت (خیلی معامله‌ی باز هم‌زمان)، بودجه‌ی API از سقف روزانه رد نشه
    due_live.sort(key=lambda k: live_last_checked.get(k, 0))  # قدیمی‌ترین‌ها اول
    due_live = due_live[:MAX_LIVE_CHECKS_PER_RUN]

    if due_live:
        logger.info(f"Live-price check due for {len(due_live)} open trade(s)")
    for state_key in due_live:
        if time.time() - run_start > MAX_RUN_SECONDS:
            logger.warning("Time budget reached during live-price pass - will resume next run")
            break
        symbol, tf_key = state_key.split("|")
        display = WATCHLIST_SYMBOLS.get(symbol, symbol)
        tf_cfg = TIMEFRAMES.get(tf_key)
        sym_state = candle_states[state_key]
        trade = sym_state.get("open_trade")
        if not tf_cfg or not trade or trade.get("closed"):
            continue
        try:
            live_price = fetch_live_price(symbol)
            if live_price:
                events = check_open_trade_live(trade, live_price)
                for ev in events:
                    _send_exit(display, tf_key, tf_cfg, trade, ev, sym_state["hist"], symbol, live_price=live_price)
            live_last_checked[state_key] = now_ts_
            time.sleep(8)
        except Exception as e:
            logger.error(f"Live-price check failed for {state_key}: {e}")

    save_state(state)
    logger.info("✅ Scan complete")


if __name__ == "__main__":
    main()

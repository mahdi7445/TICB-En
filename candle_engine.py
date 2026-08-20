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
import threading
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
SL_BUFFER_ATR_MULT = 0.75  # بافر اضافه فراتر از EMA7/سوینگ (افزایش‌یافته از ۰.۳ چون حد ضررها خیلی نزدیک بودن)

# ⚠️ سیستم ریسک/ریوارد «RiskRivard» (جایگزین سیستم قبلی): سطوح تارگت دیگر ۱R/۲R/۳R/۴R
# مساوی نیستند - طبق طرح جدید روی ۱R، ۲R، ۴R و ۶R قرار می‌گیرند (به همین دلیل عدد Target
# دیگر با ضریب R یکسان نیست - Target 3 روی 4R و Target 4 روی 6R است، نه 3R/4R قدیمی).
RR_TARGETS = [1, 2, 4, 6]
TARGET_LABELS = {1: "Target 1", 2: "Target 2", 4: "Target 3", 6: "Target 4"}

WATCHLIST_SYMBOLS = {
    "BTC/USD": "BTC",
    "ETH/USD": "ETH",
    # ⚠️ XAU/USD (طلا) طبق تصمیم شما حذف شد. با حذف طلا و رهگیری زنده‌ی رایگان WebSocket
    # برای بیت‌کوین/اتریوم (که دیگه بودجه‌ی REST نمی‌خواد)، بودجه‌ی کافی برای برگردوندن
    # تایم‌فریم‌های ۱ و ۵ دقیقه (فقط برای همین ۲ نماد) آزاد شد.
}

TIMEFRAMES = {
    "1m":  {"td_interval": "1min",  "bar_seconds": 60,            "label": "1M"},
    "5m":  {"td_interval": "5min",  "bar_seconds": 5 * 60,        "label": "5M"},
    "15m": {"td_interval": "15min", "bar_seconds": 15 * 60,       "label": "15M"},
    "1h":  {"td_interval": "1h",    "bar_seconds": 60 * 60,       "label": "1H"},
    # ⚠️ طبق درخواست شما، سقف تایم‌فریم‌ها الان 1h است - 4h و 1d هر دو حذف شدن تا بودجه و
    # توجه کامل روی تایم‌فریم‌های پایین (1m/5m/15m/1h) بمونه. صادقانه: چون 4h فقط ۶
    # درخواست/روز (برای هر دو نماد ~۱۲/روز) مصرف می‌کرد، حذفش هم بودجه‌ی زیادی آزاد نمی‌کنه -
    # فایده‌ی اصلیش سادگی، تمرکز، و کاهش سیگنال‌های خیلی کند-تایم‌فریمه، نه سرعت چشمگیر
    # بیشتر روی 1m/5m. با این حال، بودجه‌ی آزادشده (از حذف 4h و 1d با هم) صرف تنگ‌تر کردن
    # فاصله‌ی چک 1m شد (پایین‌تر ببینید).
}


TF_CHECK_INTERVAL_SECONDS = {
    "1m": 11 * 60,   # از ۱۲ دقیقه به ۱۱ دقیقه تنگ‌تر شد - با بودجه‌ی آزادشده از حذف 4h/1d
    "5m": 10 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
}
# جمع تخمینی: (131+144+96+24) × ۲ نماد ≈ ۷۹۰ درخواست/روز، زیر سقف ۸۰۰
# ⚠️ 15m/1h همین الان هم دقیقاً به‌اندازه‌ی فاصله‌ی بسته‌شدن کندل خودشون چک می‌شن - یعنی از
# نظر بودجه‌ای دیگه جایی برای فشرده‌تر شدن ندارن (چک بیشتر از این باعث درخواست‌های تکراری
# روی یک کندل بسته‌نشده می‌شد، نه سیگنال زودتر). اگه بعد از دیپلوی، از داشبورد Twelve Data
# دیدید هنوز به سقف ۸۰۰ نزدیک نیستید، می‌شه 5m رو هم کمی بیشتر فشرده کرد.

LIVE_CHECK_INTERVAL_SECONDS = 15 * 60  # فقط پشتیبان REST؛ بیت‌کوین/اتریوم اصلی‌شون از WebSocket میاد
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


def git_commit_and_push():
    """هر چند دقیقه یک‌بار تغییرات data/ رو کامیت و پوش می‌کنه، و - مهم - همیشه یک
    `git pull` هم انجام می‌ده حتی اگه خودِ این اسکریپت چیزی برای کامیت نداشته باشه.

    ⚠️ باگ قبلی: pull فقط زمانی اجرا می‌شد که این اسکریپت خودش تغییر محلی برای کامیت
    داشت. یعنی سیگنال‌های دستی/فوروارد‌شده‌ای که subscription_bot.py (یک workflow کاملاً
    جدا با چک‌اوت گیت جداگانه) در manual_signals.json می‌نوشت و پوش می‌کرد، تا هر وقت که
    این اسکریپت به‌طور تصادفی خودش state تغییر می‌کرد دیده نمی‌شدن - در بدترین حالت تا
    پایان همون اجرای ۵ ساعت و ۲۰ دقیقه‌ای! الان با pull بی‌قید‌وشرط در هر دور، این تاخیر
    به حداکثر GIT_COMMIT_EVERY_SECONDS (چند دقیقه) کاهش پیدا می‌کنه."""
    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["git", "add", "data/"], cwd=repo_dir, check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir)
        has_local_changes = (result.returncode != 0)
        if has_local_changes:
            subprocess.run(["git", "commit", "-m", "update candle/trade state [skip ci]"], cwd=repo_dir, check=True)
        pull = subprocess.run(["git", "pull", "--rebase"], cwd=repo_dir, check=False)
        if pull.returncode != 0:
            logger.warning("[git] pull --rebase failed/conflict — will retry next cycle")
        if has_local_changes:
            subprocess.run(["git", "push"], cwd=repo_dir, check=True)
            logger.info("[git] committed & pushed data/ changes")
        elif pull.returncode == 0:
            logger.info("[git] pulled latest data/ (no local changes to push)")
    except Exception as e:
        logger.warning(f"[git] sync failed: {e}")


CLOSE_TYPE_LABELS = {
    "stop": "Stopped out (full loss)",
    "breakeven": "Breakeven (Target 1 banked, rest closed at entry)",
    "sl_after_t2": "Stopped after Target 2 (Target 1-2 banked, rest closed at Target 1 price)",
    "sl_after_t3": "Stopped after Target 3 (Target 1-3 banked, rest closed at Target 2 price)",
    "runner_stop": "Runner closed (all 4 targets + trailing runner)",
    "opposite_signal": "Closed early - opposite signal appeared",
}


DAILY_REPORT_HOUR_UTC = int(os.environ.get("DAILY_REPORT_HOUR_UTC") or "21")  # ساعت ارسال گزارش روزانه (UTC)


def format_daily_report_message(date_str: str, trades: List[Dict[str, Any]]) -> str:
    if not trades:
        return f"📋 <b>Daily Report — {date_str}</b>\n\nNo signals closed today."

    total = len(trades)
    wins = sum(1 for h in trades if h["final_r"] > 0)
    breakeven = sum(1 for h in trades if h["final_r"] == 0)
    losses = sum(1 for h in trades if h["final_r"] < 0)
    total_r = sum(h["final_r"] for h in trades)

    lines = [
        f"📋 <b>Daily Report — {date_str}</b>\n",
        f"Closed today: {total}  ·  ✅ {wins}  ⚪ {breakeven}  ❌ {losses}",
        f"Total: <b>{total_r:+.2f}R</b>\n",
        "<b>Details</b>",
    ]
    for h in trades:
        arrow = "🟢" if h["side"] == "BUY" else "🔴"
        tf_label = h.get("tf", "?")
        # سیگنال‌های دستی/فوروارد‌شده (tf == "manual") فقط با نام خودِ ارز نشون داده می‌شن،
        # نه با یک برچسب «manual» که باعث می‌شد انگار روی یک دارایی جدا معامله شده - دقیقاً
        # مثل بقیه‌ی سیگنال‌ها که در نتایج با نام خودِ ارزشون شناخته می‌شن.
        symbol_part = h['symbol'] if tf_label == "manual" else f"{h['symbol']} · {tf_label}"
        targets_hit = h.get("targets_hit") or []
        targets_part = f" ({'/'.join(targets_hit)} hit)" if targets_hit else ""
        lines.append(f"{arrow} {symbol_part} — {h.get('close_reason', h.get('close_type','?'))}{targets_part} ({h['final_r']:+.2f}R)")
    return "\n".join(lines)


DAILY_REPORT_LOG_FILE = os.path.join(DATA_DIR, "daily_report_log.json")


def _load_daily_report_log() -> List[str]:
    if os.path.exists(DAILY_REPORT_LOG_FILE):
        try:
            with open(DAILY_REPORT_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_daily_report_log(dates: List[str]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DAILY_REPORT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(dates[-60:], f, ensure_ascii=False, indent=2)


def maybe_send_daily_report():
    """هر روز، یک‌بار بعد از ساعت DAILY_REPORT_HOUR_UTC، خلاصه‌ی جزئیاتِ همه‌ی سیگنال‌های
    بسته‌شده‌ی آن روز رو مستقیماً در کانال پست می‌کنه (نه فقط داخل ربات) - برای شفافیت عمومی.

    ⚠️ باگ قبلی که باعث دوبار ارسال شد: پرچمِ «امروز فرستاده شد» بلافاصله push نمی‌شد -
    فقط محلی save می‌شد و منتظر دور بعدیِ commit/push دوره‌ای (هر ۲ دقیقه) می‌موند. اگه بین
    این‌که پرچم محلی ثبت می‌شد و push بعدی، پردازش به هر دلیلی (کرش، ری‌استارت job، محدودیت
    زمانی GitHub Actions) از نو شروع می‌شد، چک‌اوت تازه‌ی گیت هنوز پرچمِ push‌نشده رو نمی‌دید
    و گزارش دوباره فرستاده می‌شد - دقیقاً چیزی که رخ داد.

    راه‌حل: الان (۱) قبل از هر تصمیمی یک‌بار pull تازه می‌کنیم تا اگه اجرای دیگه‌ای همین الان
    گزارش رو فرستاده و push کرده، حتماً ببینیمش، و (۲) به‌محض ثبت پرچم، بلافاصله و همزمان
    (نه منتظر دور بعدی) commit+push می‌کنیم - قبل از اینکه اصلاً پیام رو بفرستیم."""
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour < DAILY_REPORT_HOUR_UTC:
        return

    git_commit_and_push()  # pull تازه، تا اگه یک اجرای دیگه گزارش رو زده و push کرده ببینیمش

    sent_dates = _load_daily_report_log()
    if today_str in sent_dates:
        return

    # mark-then-push-then-send: هم ثبت محلی، هم push فوری، قبل از تلاش برای ارسال پیام
    sent_dates.append(today_str)
    _save_daily_report_log(sent_dates)
    git_commit_and_push()

    history = []
    if os.path.exists(TRADE_HISTORY_FILE):
        try:
            with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    today_trades = [h for h in history if h.get("closed_at", "").startswith(today_str)]

    msg = format_daily_report_message(today_str, today_trades)
    if send_photo(msg, None):
        logger.info(f"📋 Daily report sent for {today_str} ({len(today_trades)} trades)")
    else:
        logger.error(f"📋 Daily report FAILED to send for {today_str} — will not retry today "
                      f"(to avoid ever double-posting); check logs.")


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
    targets_hit = [TARGET_LABELS[t].replace("Target ", "T") for t in RR_TARGETS if trade["hit"][str(t)]]
    history.append({
        "symbol": symbol, "tf": tf_key, "side": trade["side"],
        "entry": trade["entry"], "sl": trade["sl"],
        "final_r": compute_final_r(trade),
        "targets_hit": targets_hit,
        "close_type": close_type,
        "close_reason": CLOSE_TYPE_LABELS.get(close_type, close_type),
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })
    history = history[-2000:]  # جلوگیری از رشد بی‌نهایت فایل
    with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ================== تلگرام ==================

def _send_photo_once(caption: str, photo_path: Optional[str], reply_to_message_id: Optional[int] = None) -> Optional[int]:
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


def send_photo(caption: str, photo_path: Optional[str], reply_to_message_id: Optional[int] = None,
                retries: int = 3) -> Optional[int]:
    """پیام (عکس یا متن) رو می‌فرسته و در صورت موفقیت، message_id تلگرام رو برمی‌گردونه.
    نتیجه‌ی هر سیگنال (استاپ/تارگت/بریک‌ایون) باید حتماً منتشر بشه؛ برای همین به‌جای یک تلاش
    تنها، در صورت خطای موقت شبکه/تلگرام تا ۳ بار با فاصله‌ی کوتاه دوباره امتحان می‌کنه، به‌جای
    اینکه یک پیام نتیجه‌ی مهم بی‌سروصدا گم بشه."""
    for attempt in range(1, retries + 1):
        msg_id = _send_photo_once(caption, photo_path, reply_to_message_id)
        if msg_id:
            return msg_id
        if attempt < retries:
            logger.warning(f"send_photo attempt {attempt}/{retries} failed — retrying in {attempt * 3}s")
            time.sleep(attempt * 3)
    logger.error("send_photo: all retries exhausted — message NOT delivered")
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

    raw_bull = (is_uptrend and trend_is_strong and is_valid_bull_candle and (not next_invalidates_bull)
                and (not is_ema7_flat) and both_above and (bullish_engulf or bullish_pin))
    raw_bear = (is_downtrend and trend_is_strong and is_valid_bear_candle and (not next_invalidates_bear)
                and (not is_ema7_flat) and both_below and (bearish_engulf or bearish_pin))
    # توجه: قبلاً کندل معتبر (shadow-ratio) به‌تنهایی کافی بود؛ الان علاوه بر اون، باید واقعاً
    # یک الگوی اینگولفینگ یا پین‌بار مشخص هم رخ داده باشه (سخت‌گیرانه‌تر، برای کاهش سیگنال کاذب)

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
# سیستم مدیریت پوزیشن «RiskRivard» (طبق طرح/کد ارسالی شما - جایگزین کامل سیستم قبلی):
#
#   Target 1 (1R):  بستن ۲۰٪ از کل حجم پوزیشن + انتقال حد ضرر به Entry (ریسک‌فری)
#   Target 2 (2R):  بستن ۳۰٪ از کل حجم پوزیشن + انتقال حد ضرر به قیمت Target 1
#   Target 3 (4R):  بستن ۱۵٪ از کل حجم پوزیشن + انتقال حد ضرر به قیمت Target 2
#   Target 4 (6R):  بستن ۱۰٪ از کل حجم پوزیشن + فعال‌سازی تریلینگ‌استاپ روی ۲۵٪ باقی‌مانده
#                   («Runner») با فاصله‌ی ثابت ۱.۵R از بالاترین قیمت رسیده (peak) - بدون سقف
#
# جمع درصدها: ۲۰+۳۰+۱۵+۱۰ = ۷۵٪ در ۴ تارگت + ۲۵٪ Runner = ۱۰۰٪.
#
# تفاوت کلیدی با سیستم قبلی: درصدها همه از «کل پوزیشن اولیه» محاسبه می‌شن (نه پله‌ای از
# باقی‌مانده)، و حد ضرر به‌جای پرش مستقیم به ریسک‌فری در Target 2، در هر تارگت یک پله عقب‌تر
# می‌ره (Entry → T1 → T2)، یعنی هرچه جلوتر برید ریسکِ باقی‌مانده‌ی پوزیشن هم کمتر می‌شه، نه
# فقط صفر. تریلینگ هم دیگه از Target 3 شروع نمی‌شه؛ فقط روی ۲۵٪ رانر و فقط بعد از Target 4.
#
# ⚠️ صادقانه: این یک تنظیم مکانیکیِ نحوه‌ی خروج پله‌ایه (نه تغییری در منطق ورود/تشخیص
# سیگنال شما که دست‌نخورده مونده). چون امکان بک‌تست واقعی روی داده‌ی تاریخی این‌جا ندارم،
# نمی‌تونم تضمین کنم نتیجه‌ی نهایی بهتر می‌شه - بعد از مدتی نتایج واقعی /results رو با
# سیستم قبلی مقایسه کنید.

def open_new_trade(signal: Dict[str, Any], symbol: str = None, tf_key: str = None, display: str = None) -> Optional[Dict[str, Any]]:
    entry = signal["price"]
    sl = signal["sl"]
    r = abs(entry - sl)
    if r <= 0:
        return None
    return {
        "side": signal["side"], "entry": entry, "sl": sl, "r": r,
        "hit": {str(t): False for t in RR_TARGETS}, "trailing": False, "closed": False,
        "symbol": symbol, "tf_key": tf_key, "display": display,
    }


# درصدهای بسته‌شدن در هر مرحله - همه‌شون درصدی از حجم اصلی/کل پوزیشن‌اند (نه از باقی‌مانده)
W1 = 0.20   # Target 1 (1R)
W2 = 0.30   # Target 2 (2R)
W3 = 0.15   # Target 3 (4R)
W4 = 0.10   # Target 4 (6R)
W_RUNNER = 0.25  # Runner - باقی‌مانده بعد از Target 4، با تریلینگ ۱.۵R باز می‌مونه
TRAILING_R_MULT = 1.5  # فاصله‌ی تریلینگ‌استاپ رانر از peak price، بر حسب واحد R اولیه‌ی معامله

assert abs((W1 + W2 + W3 + W4 + W_RUNNER) - 1.0) < 1e-9, "جمع درصدها باید دقیقاً ۱۰۰٪ باشه"


def compute_final_r(trade: Dict[str, Any]) -> float:
    """تخمین نتیجه‌ی نهایی معامله بر حسب R، بر اساس درصد بسته‌شدن پوزیشن (از کل) در هر مرحله
    و محل واقعی حد ضرر در همون مرحله. برای رانر که قیمت خروج دقیقش از قبل معلوم نیست (تریلینگ
    باز است)، یک تخمین محافظه‌کارانه در سطح Target 4 (6R) در نظر گرفته می‌شود - نه بیشتر."""
    hit = trade["hit"]
    if not hit["1"]:
        return -1.0  # هیچ تارگتی نخورده، کل پوزیشن با ضرر کامل (حد ضرر اولیه) بسته شده
    if not hit["2"]:
        # بعد از T1: ۲۰٪ با ۱R سود بانک شده، ۸۰٪ باقی‌مانده با SL روی Entry (ریسک‌فری) بسته شده
        return W1 * 1 + (1 - W1) * 0.0
    if not hit["4"]:  # یعنی به Target 3 (سطح 4R) نرسیده
        # بعد از T2: ۲۰٪@1R + ۳۰٪@2R بانک شده، ۵۰٪ باقی‌مانده با SL روی T1 (1R) بسته شده
        return W1 * 1 + W2 * 2 + (1 - W1 - W2) * 1.0
    if not hit["6"]:  # یعنی به Target 4 (سطح 6R) نرسیده
        # بعد از T3: ۲۰٪@1R + ۳۰٪@2R + ۱۵٪@4R بانک شده، ۳۵٪ باقی‌مانده با SL روی T2 (2R) بسته شده
        return W1 * 1 + W2 * 2 + W3 * 4 + (1 - W1 - W2 - W3) * 2.0
    # به Target 4 (6R) رسیده: ۲۰+۳۰+۱۵+۱۰=۷۵٪ بانک شده + ۲۵٪ Runner با تخمین محافظه‌کارانه در 6R
    return W1 * 1 + W2 * 2 + W3 * 4 + W4 * 6 + W_RUNNER * 6.0


def check_open_trade(trade: Dict[str, Any], candle: Dict[str, Any], ema7_now: Optional[float] = None) -> List[Dict[str, Any]]:
    """منطق حد ضرر/تارگت طبق سیستم RiskRivard: بعد از هر تارگت، حد ضرر یک پله عقب‌تر
    می‌ره (Entry ← T1 ← T2)، و بعد از Target 4 (سطح 6R) روی ۲۵٪ Runner باقی‌مانده یک
    تریلینگ‌استاپ با فاصله‌ی ثابت TRAILING_R_MULT×R از بالاترین/پایین‌ترین قیمت رسیده
    (peak) فعال می‌شه - دقیقاً مطابق کد ارسالی. پارامتر ema7_now دیگر استفاده نمی‌شود
    (نگه داشته شده فقط برای سازگاری با فراخوانی‌های موجود)."""
    if trade is None or trade.get("closed"):
        return []
    events = []
    side, entry, r = trade["side"], trade["entry"], trade["r"]

    # به‌روزرسانی peak و تریلینگ‌استاپ رانر (فقط بعد از Target 4/6R فعال می‌شه، و فقط در
    # جهت سودآور حرکت می‌کند - هیچ‌وقت به ضرر معامله برنمی‌گرده)
    if trade.get("trailing"):
        if side == "BUY":
            trade["peak"] = max(trade.get("peak", entry), candle["h"])
            trade["sl"] = max(trade["sl"], trade["peak"] - TRAILING_R_MULT * r)
        else:
            trade["peak"] = min(trade.get("peak", entry), candle["l"])
            trade["sl"] = min(trade["sl"], trade["peak"] + TRAILING_R_MULT * r)

    sl = trade["sl"]

    def stop_event_type():
        if trade["hit"]["6"]:
            return "runner_stop"     # همه‌ی ۴ تارگت خورده، فقط Runner با تریلینگ باقی بود
        if trade["hit"]["4"]:
            return "sl_after_t3"     # بعد از Target 3 (4R)، حد ضرر روی Target 2 (2R) بود
        if trade["hit"]["2"]:
            return "sl_after_t2"     # بعد از Target 2 (2R)، حد ضرر روی Target 1 (1R) بود
        if trade["hit"]["1"]:
            return "breakeven"       # بعد از Target 1 (1R)، حد ضرر روی Entry بود
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
                if target == 1:
                    trade["sl"] = entry              # ریسک‌فری
                elif target == 2:
                    trade["sl"] = entry + 1 * r       # حد ضرر برو به Target 1
                elif target == 4:
                    trade["sl"] = entry + 2 * r       # حد ضرر برو به Target 2
                elif target == 6:
                    trade["trailing"] = True
                    trade["peak"] = max(trade.get("peak", entry), candle["h"])
                    trade["sl"] = max(trade["sl"], trade["peak"] - TRAILING_R_MULT * r)
                # توجه: در Target 4 (6R) دیگر trade را کامل نمی‌بندیم؛ ۲۵٪ Runner با
                # تریلینگ‌استاپ باز می‌ماند تا خودش بعداً با رویداد runner_stop بسته شود.
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
                if target == 1:
                    trade["sl"] = entry
                elif target == 2:
                    trade["sl"] = entry - 1 * r
                elif target == 4:
                    trade["sl"] = entry - 2 * r
                elif target == 6:
                    trade["trailing"] = True
                    trade["peak"] = min(trade.get("peak", entry), candle["l"])
                    trade["sl"] = min(trade["sl"], trade["peak"] + TRAILING_R_MULT * r)

    return events


STOP_CONFIRM_SECONDS = 8  # فاصله‌ی زمانی لازم بین اولین تیک عبور از استاپ و تایید نهایی


def check_open_trade_live(trade: Dict[str, Any], live_price: float) -> List[Dict[str, Any]]:
    """نسخه‌ی سبک check_open_trade که فقط یک قیمت لحظه‌ای (نه کندل کامل) داره - برای
    رهگیری زنده‌ی معامله‌ی باز بین دو بسته‌شدن کندل. منطق تارگت/ریسک‌فری/تریلینگ دقیقاً
    همون check_open_trade است.

    ⚠️ محافظ «استاپ کاذب»: یک‌بار پیش اومد که قیمت واقعاً به استاپ نرسیده بود ولی پیام
    استاپ‌خوردن فرستاده شد - به احتمال زیاد به‌خاطر یک تیک لحظه‌ای پرت/بد از فید قیمت زنده.
    برای همین، برخلاف تارگت‌ها (که فوری و بدون تاخیر ثبت می‌شن - فقط سود رو قفل می‌کنن، ریسکی
    ندارن)، برخورد به استاپ بلافاصله نهایی نمی‌شه: باید در دو تیک متوالی با حداقل
    STOP_CONFIRM_SECONDS ثانیه فاصله، هر دو بار قیمت هنوز از استاپ عبور کرده باشه تا واقعاً
    بسته بشه. این جدا از اینکه منبع قیمت Binance باشه یا Coinbase کار می‌کنه - محافظت
    سطح-منطق، نه فقط سطح-منبع."""
    side, sl = trade["side"], trade["sl"]
    would_stop = (side == "BUY" and live_price <= sl) or (side == "SELL" and live_price >= sl)

    if not would_stop:
        trade.pop("_stop_pending_since", None)
        fake_candle = {"o": live_price, "h": live_price, "l": live_price, "c": live_price}
        return check_open_trade(trade, fake_candle, ema7_now=None)

    now = time.time()
    pending_since = trade.get("_stop_pending_since")
    if pending_since is None:
        trade["_stop_pending_since"] = now
        logger.info(f"⏳ Possible stop-hit tick for {trade.get('symbol')} — waiting for confirmation "
                    f"tick before closing (avoids a single bad print falsely closing the trade)")
        return []
    if now - pending_since < STOP_CONFIRM_SECONDS:
        return []

    trade.pop("_stop_pending_since", None)
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

# توجه: پلن کامل مدیریت پوزیشن (POSITION_PLAN_LINES) طبق درخواست شما از پیام سیگنال اصلی
# حذف شد تا اون پیام کوتاه‌تر بمونه. توضیح کامل هر مرحله («چند درصد ببندید، حد ضرر کجا بره»)
# هنوز دقیقاً همون لحظه‌ای که کاربر بهش نیاز داره ارسال می‌شه: زیر پیام رسیدن به هر تارگت
# (TARGET_ACTION_LINE پایین) و در /disclaimer ربات.

# اقدامی که کاربر باید در همون لحظه‌ی رسیدن به هر تارگت انجام بده - کلید = ضریب R همون تارگت
TARGET_ACTION_LINE = {
    1: "\n\n✂️ Close 20% of your total position here, and move your stop-loss to entry (risk-free).",
    2: "\n\n✂️ Close 30% of your total position here, and move your stop-loss up to Target 1.",
    4: "\n\n✂️ Close 15% of your total position here, and move your stop-loss up to Target 2.",
    6: "\n\n✂️ Close 10% of your total position here. The final 25% (\"Runner\") stays open with "
       "a trailing stop 1.5R behind the peak — no fixed exit, so a bigger move keeps paying.",
}


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _dt(display: str, tf_label: str) -> str:
    """رشته‌ی نمایشی «نماد + تایم‌فریم» رو می‌سازه. برای سیگنال‌های دستی/فوروارد‌شده که
    تایم‌فریم مشخصی ندارن (tf_label خالیه)، فقط خودِ نماد واقعی (BTC یا ETH) نشون داده
    می‌شه - نه یک برچسب عمومی مثل «Manual» که باعث می‌شد به‌نظر برسه سیگنال روی یک دارایی
    جدا به‌اسم «BTC Manual» باز شده، نه خودِ ارز واقعی‌ای که فوروارد/ثبت شده بود."""
    return f"{display} {tf_label}".strip() if tf_label else display


def format_entry_message(display: str, tf_label: str, signal: Dict[str, Any], trade: Dict[str, Any]) -> str:
    arrow = "🟢 LONG" if signal["side"] == "BUY" else "🔴 SHORT"
    sign = 1 if signal["side"] == "BUY" else -1
    targets_lines = "\n".join([f"🎯 {TARGET_LABELS[t]}: {trade['entry'] + sign * t * trade['r']:.2f}" for t in RR_TARGETS])
    return (
        f"{arrow} — {_dt(display, tf_label)}\n\n"
        f"Entry: <b>{signal['price']:.2f}</b>\n"
        f"❌ Stop: <b>{trade['sl']:.2f}</b>\n"
        f"{targets_lines}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def _result_line(trade: Dict[str, Any]) -> str:
    """خط «نتیجه‌ی بانک‌شده تا این لحظه بر حسب R» - طبق درصد واقعی بسته‌شده در هر تارگت
    و محل واقعی حد ضرر. همین مقدار در trade_history.json هم ذخیره می‌شه، پس آمار لحظه‌ای
    این پیام‌ها با /results و گزارش روزانه همیشه یکی و دقیق می‌مونه."""
    r = compute_final_r(trade)
    word = "profit" if r > 0 else ("breakeven" if r == 0 else "loss")
    return f"\n\n📊 Result so far: <b>{r:+.2f}R</b> ({word})"


def format_rr_exit_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    level = event["level"]
    label = TARGET_LABELS[level]
    action = TARGET_ACTION_LINE.get(level, "")
    return (
        f"✅ {label} HIT ({level}R) — {_dt(display, tf_label)}\n\n"
        f"{label} reached on this {direction} trade.\n"
        f"Entry {trade['entry']:.2f}  ·  Now {event['price']:.2f}\n"
        f"{action}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"❌ STOP HIT — {_dt(display, tf_label)}\n\n"
        f"Stop-loss hit on this {direction} trade before Target 1 — full position closed.\n"
        f"Entry was {trade['entry']:.2f}  ·  Stop {event['price']:.2f}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_breakeven_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"⚪ BREAKEVEN — {_dt(display, tf_label)}\n\n"
        f"Price returned to entry on this {direction} trade — the remaining 80% closed with no "
        f"loss (20% was already banked at Target 1).\n"
        f"Entry {trade['entry']:.2f}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_sl_after_t2_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🔒 STOP AFTER TARGET 2 — {_dt(display, tf_label)}\n\n"
        f"Price came back to the Target 1 level on this {direction} trade — the remaining 50% "
        f"closed there, locking in profit (20% @ Target 1, 30% @ Target 2 already banked).\n"
        f"Entry {trade['entry']:.2f}  ·  Closed at {event['price']:.2f}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_sl_after_t3_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🔒 STOP AFTER TARGET 3 — {_dt(display, tf_label)}\n\n"
        f"Price came back to the Target 2 level on this {direction} trade — the remaining 35% "
        f"closed there, locking in more profit (20% @ T1, 30% @ T2, 15% @ T3 already banked).\n"
        f"Entry {trade['entry']:.2f}  ·  Closed at {event['price']:.2f}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_runner_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🏁 RUNNER CLOSED — {_dt(display, tf_label)}\n\n"
        f"All 4 targets were already banked on this {direction} trade (75% of the position) — "
        f"the final 25% runner portion just closed on its trailing stop (1.5R behind the peak). "
        f"Trade fully complete.\n"
        f"Entry {trade['entry']:.2f}  ·  Runner closed at {event['price']:.2f}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_forced_close_message(display: str, tf_label: str, trade: Dict[str, Any], exit_price: float) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    opposite = "SHORT" if trade["side"] == "BUY" else "LONG"
    result_r = compute_final_r(trade)
    result_word = "profit" if result_r > 0 else ("breakeven" if result_r == 0 else "loss")
    return (
        f"⚠️ TRADE CLOSED — {_dt(display, tf_label)}\n\n"
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
                trade = open_new_trade(sig, symbol=symbol, tf_key=tf_key, display=display)
                if trade:
                    apply_live_entry_price(symbol, sig, trade)
                    state["open_trade"] = trade
                    _send_entry(display, tf_key, tf_cfg, sig, trade, state["hist"])
        candle_states[state_key] = state
        return

    last_open_time = sym_state.get("last_open_time")
    candles = fetch_closed_klines(symbol, 10, tf_cfg["td_interval"], tf_cfg["bar_seconds"])
    new_candles = [k for k in candles if last_open_time is None or k["open_time"] > last_open_time]
    last_new_idx = len(new_candles) - 1

    state = sym_state
    for idx, k in enumerate(new_candles):
        # اول اندیکاتور همین کندل رو پردازش می‌کنیم تا EMA7 تازه‌اش برای تریل‌کردن در دسترس باشه
        state, sig = step_candle_state(state, k["o"], k["h"], k["l"], k["c"], k["open_time"])
        ema7_now = state.get("ema7")

        if state.get("open_trade"):
            events = check_open_trade(state["open_trade"], k, ema7_now)
            for ev in events:
                _send_exit(display, tf_key, tf_cfg, state["open_trade"], ev, state["hist"], symbol)

        # ⚠️ فقط اگه سیگنال روی آخرین کندلِ این دسته باشه بازش می‌کنیم، نه هر کندل قدیمی‌تری
        # که توی همین دسته پیدا شد. برای تایم‌فریم‌های 1m/5m، به‌خاطر محدودیت بودجه‌ی
        # Twelve Data، این تایم‌فریم‌ها هر ۱۰-۱۵ دقیقه یک‌بار چک می‌شن - یعنی وقتی بالاخره
        # چک می‌شه، ممکنه چند کندل بسته‌شده‌ی پشت‌سرهم یک‌جا برسن. قبلاً اگه سیگنال روی یکی
        # از کندل‌های قدیمی‌تر این دسته پیدا می‌شد، بلافاصله باز می‌شد و بعد همون حلقه با
        # کندل‌های بعدیِ (که در واقعیت خودشون هم قبلاً بسته شده بودن) تارگت/استاپش رو هم
        # همون لحظه می‌زد - یعنی پیام «سیگنال جدید» و «تارگت خورد» تقریباً پشت‌سرهم می‌رسیدن،
        # چون واقعیت چند دقیقه/کندل قبل رخ داده بود، نه همین الان. با این محدودیت، فقط
        # سیگنالِ روی نزدیک‌ترین کندل به «الان» رو پست می‌کنیم؛ سیگنال‌های قدیمی‌تر همون دسته
        # صرفاً برای به‌روزرسانی اندیکاتور/معامله‌ی باز پردازش می‌شن، نه باز کردن معامله‌ی جدید.
        if sig and idx == last_new_idx:
            # اگه معامله‌ی قبلی هنوز باز بود (چون هنوز به استاپ/تارگت نخورده)، قبل از باز کردن
            # معامله‌ی جدید مخالف‌جهت، حتماً باید اول رسماً بسته بشه - وگرنه بی‌سروصدا فراموش
            # می‌شد و کاربر گیج می‌موند که چرا یهو سیگنال مخالف اومده بدون توضیح.
            prev_trade = state.get("open_trade")
            if prev_trade and not prev_trade.get("closed"):
                _force_close_trade(display, tf_key, tf_cfg, prev_trade, k, state["hist"], symbol)

            trade = open_new_trade(sig, symbol=symbol, tf_key=tf_key, display=display)
            if trade:
                apply_live_entry_price(symbol, sig, trade)
                state["open_trade"] = trade
                _send_entry(display, tf_key, tf_cfg, sig, trade, state["hist"])

    candle_states[state_key] = state


def _force_close_trade(display, tf_key, tf_cfg, trade, candle, hist, symbol):
    """وقتی سیگنال جدیدِ مخالف‌جهت میاد ولی معامله‌ی قبلی هنوز باز بود، این تابع اون رو
    رسماً با یک پیام شفاف می‌بندد (نه اینکه بی‌سروصدا فراموش بشه). بدون چارت - دلیل در
    _send_exit توضیح داده شده."""
    trade["closed"] = True
    trade["close_type"] = "opposite_signal"
    exit_price = candle["c"]
    msg = format_forced_close_message(display, tf_cfg["label"], trade, exit_price)
    reply_id = trade.get("signal_message_id")
    if send_photo(msg, None, reply_to_message_id=reply_id):
        logger.info(f"📤 Forced-close sent: {display} [{tf_key}] (opposite signal)")
    log_trade_result(symbol, tf_key, trade)
    time.sleep(1.5)


def _send_entry(display, tf_key, tf_cfg, sig, trade, hist):
    chart = build_chart_from_hist(hist, f"{_dt(display, tf_cfg['label'])} · Entry", trade=trade)
    msg = format_entry_message(display, tf_cfg["label"], sig, trade)
    msg_id = send_photo(msg, chart)
    if msg_id:
        trade["signal_message_id"] = msg_id
        logger.info(f"📤 Entry sent: {display} [{tf_key}] {sig['side']}")
    time.sleep(1.5)


def _send_exit(display, tf_key, tf_cfg, trade, event, hist, symbol, live_price=None):
    # ⚠️ عمداً بدون چارت: چارتِ لحظه‌ی خروج قبلاً یا از تاریخچه‌ی ذخیره‌شده‌ی (بالقوه قدیمی)
    # ساخته می‌شد، یا با یک درخواست تازه از کندل‌های ۱۵ دقیقه‌ای - که خودش هم دقیقاً «همین
    # لحظه» نیست (تا ۱۵ دقیقه قدیمی می‌تونه باشه) و برای تایم‌فریم‌های پایین (1m/5m) اصلاً
    # گمراه‌کننده بود. چون نمی‌شه یک عکس واقعاً دقیق و لحظه‌ای از قیمت گرفت، به‌جای نمایش
    # چیزی که دقیق نیست، پیام رسیدن به استاپ/تارگت فقط متنی (بدون عکس) ارسال می‌شه - خودِ
    # قیمت دقیق توی متن پیام هست. (چارت پیام ورود دست‌نخورده مونده - اون از کندل‌های واقعی
    # همون لحظه‌ی اسکن ساخته می‌شه و مشکلی نداره.)
    if event["type"] == "stop":
        msg = format_stop_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "breakeven":
        msg = format_breakeven_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "sl_after_t2":
        msg = format_sl_after_t2_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "sl_after_t3":
        msg = format_sl_after_t3_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "runner_stop":
        msg = format_runner_stop_message(display, tf_cfg["label"], trade, event)
    else:
        msg = format_rr_exit_message(display, tf_cfg["label"], trade, event)
    reply_id = trade.get("signal_message_id")
    if send_photo(msg, None, reply_to_message_id=reply_id):
        logger.info(f"📤 Exit sent: {display} [{tf_key}] {event['type']}")
    if trade.get("closed"):
        log_trade_result(symbol, tf_key, trade)
    time.sleep(1.5)



MAX_RUN_SECONDS = 240  # بودجه‌ی زمانی هر «دور اسکن» (نه کل حلقه) تا با دور بعدی تداخل نکنه
LOOP_MAX_SECONDS = int(os.environ.get("LOOP_MAX_SECONDS") or str(5 * 3600 + 20 * 60))  # حدود ۵ ساعت و ۲۰ دقیقه
SCAN_CYCLE_INTERVAL_SECONDS = 300   # هر ۵ دقیقه یک دور اسکن کامل (سیگنال جدید + رهگیری REST طلا)
WS_CHECK_INTERVAL_SECONDS = 5       # هر ۵ ثانیه، معاملات باز بیت‌کوین/اتریوم با آخرین تیک WebSocket چک می‌شود
GIT_COMMIT_EVERY_SECONDS = 120      # هر ۲ دقیقه تغییرات commit می‌شود


def fetch_chart_hist(symbol: str, limit: int = 80, interval: str = "15min",
                      bar_seconds: int = 15 * 60) -> Optional[List[Dict[str, Any]]]:
    """کندل‌های اخیر (با EMA7 محاسبه‌شده روی همون‌ها) رو برای ساخت چارت می‌گیره. هم موقع باز
    شدن سیگنال دستی/فوروارد استفاده می‌شه، هم - مهم‌تر - در لحظه‌ی برخورد به تارگت/استاپ، تا
    چارتِ ارسالی واقعاً وضعیت لحظه‌ای بازار باشه، نه صرفاً یک نقطه‌ی «Live» روی یک چارتِ
    قدیمی که ممکنه از زمان باز شدن سیگنال (چند ساعت/روز قبل) دست‌نخورده مونده باشه."""
    try:
        candles = fetch_closed_klines(symbol, limit, interval, bar_seconds)
    except Exception as e:
        logger.warning(f"fetch_chart_hist failed for {symbol}: {e}")
        return None
    if not candles:
        return None
    closes = [c["c"] for c in candles]
    ema7_vals = []
    ema7_prev = None
    for cl in closes:
        ema7_prev = _ema_step(ema7_prev, cl, 7)
        ema7_vals.append(ema7_prev)
    return [{"o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "ema7": e, "dt_ms": c["open_time"]}
            for c, e in zip(candles, ema7_vals)]


def _find_open_opposite_trade(candle_states: Dict[str, Any], symbol: str, side: str, tf_key: str):
    """توی همون «سطل» تایم‌فریمی (مثلاً همه‌ی سیگنال‌های دستی/فوروارد با tf_key="manual"،
    یا همه‌ی سیگنال‌های خودکار یک تایم‌فریم مشخص)، دنبال یک معامله‌ی باز با جهت مخالف روی
    همین نماد می‌گرده. این تضمین می‌کنه که در یک تایم‌فریم مشخص، هیچ‌وقت هم‌زمان هم BUY و
    هم SELL باز روی یک ارز نباشه. سیگنال‌های خودکار تایم‌فریم‌های دیگه (مثلاً BTC 1H در
    مقابل BTC 4H) عمداً دست‌نخورده می‌مونن - این‌ها استراتژی‌های مستقل و معتبرن، نه تناقض."""
    opposite_side = "SELL" if side == "BUY" else "BUY"
    for key, s in candle_states.items():
        t = s.get("open_trade")
        if (t and not t.get("closed") and t.get("symbol") == symbol
                and t.get("side") == opposite_side and (t.get("tf_key") or "manual") == tf_key):
            return key, t
    return None, None


def process_manual_signals():
    """سیگنال‌هایی که ادمین با /admin_manual_signal یا مستقیم توی کانال (تگ #signal یا
    فوروارد خودکار‌شناسایی‌شده) ثبت کرده رو می‌خونه، دقیقاً با همون فرمت سیگنال‌های خودکار
    (استاپ + ۴ تارگت) در کانال پست می‌کنه، و به همون سیستم رهگیری (WebSocket برای
    بیت‌کوین/اتریوم، REST به‌عنوان پشتیبان) اضافه می‌کنه تا نتیجه‌ش هم داخل /results حساب بشه.

    قبل از باز کردن، اگه همین نماد یک سیگنال دستی/فوروارد مخالف‌جهت هنوز باز داشته باشه
    (مثلاً یک BUY دستی باز روی BTC و حالا یک SELL دستی جدید می‌رسه)، اول اون رو رسماً می‌بندیم
    - دقیقاً مثل رفتار سیگنال‌های خودکار وقتی جهت عوض می‌شه."""
    if not os.path.exists(MANUAL_SIGNALS_FILE):
        return
    try:
        with open(MANUAL_SIGNALS_FILE, "r", encoding="utf-8") as f:
            manual_signals = json.load(f)
    except Exception:
        return

    pending = [m for m in manual_signals if m.get("status") == "pending"]
    if not pending:
        return

    state = load_state()
    candle_states = state.setdefault("candle_signals", {})

    for m in pending:
        symbol, side, entry, sl = m["symbol"], m["side"], float(m["entry"]), float(m["sl"])
        display = WATCHLIST_SYMBOLS.get(symbol, symbol.split("/")[0])
        signal_id = m["id"]
        state_key = f"MANUAL|{signal_id}"

        opp_key, opp_trade = _find_open_opposite_trade(candle_states, symbol, side, "manual")
        if opp_trade:
            opp_display = opp_trade.get("display") or display
            opp_hist = candle_states.get(opp_key, {}).get("hist", [])
            fake_candle = {"o": entry, "h": entry, "l": entry, "c": entry}
            _force_close_trade(opp_display, "manual", MANUAL_TF_CFG, opp_trade, fake_candle, opp_hist, symbol)

        sig = {"side": side, "confirmed": True, "price": entry, "open_time": int(time.time() * 1000), "sl": sl}
        trade = open_new_trade(sig, symbol=symbol, tf_key="manual", display=display)
        if not trade:
            m["status"] = "failed"
            continue

        hist = fetch_chart_hist(symbol) or []
        candle_states[state_key] = {"open_trade": trade, "hist": hist}
        _send_entry(display, "manual", MANUAL_TF_CFG, sig, trade, hist)

        m["status"] = "active"
        m["activated_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"📌 Manual signal activated: {symbol} {side} entry={entry} sl={sl}")

    save_state(state)
    with open(MANUAL_SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(manual_signals, f, ensure_ascii=False, indent=2)


def run_scan_cycle():
    """یک دور کامل اسکن: سیگنال‌های دستی ادمین، سیگنال‌های جدید خودکار (بیت‌کوین/اتریوم)،
    گزارش روزانه (اگه وقتشه)، و رهگیری زنده‌ی REST برای معاملات باز (پشتیبان WebSocket)."""
    try:
        process_manual_signals()
    except Exception as e:
        logger.error(f"process_manual_signals failed: {e}")

    try:
        maybe_send_daily_report()
    except Exception as e:
        logger.error(f"maybe_send_daily_report failed: {e}")

    run_start = time.time()
    now_ts_ = int(datetime.now(timezone.utc).timestamp())
    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    last_checked = state.setdefault("tf_last_checked", {})

    due_tfs = [tf for tf in TIMEFRAMES if is_timeframe_due(tf, now_ts_, last_checked)]
    logger.info(f"Due timeframes this cycle: {due_tfs or '(none)'}")

    stopped_early = False
    for tf_key in due_tfs:
        tf_cfg = TIMEFRAMES[tf_key]
        for symbol, display in WATCHLIST_SYMBOLS.items():
            if time.time() - run_start > MAX_RUN_SECONDS:
                logger.warning("Time budget reached for this cycle - saving progress and stopping early.")
                stopped_early = True
                break
            try:
                process_symbol_timeframe(symbol, display, tf_key, tf_cfg, candle_states)
                time.sleep(8)  # رعایت محدودیت نرخ درخواست Twelve Data (۸ درخواست/دقیقه در پلن رایگان)
            except Exception as e:
                logger.error(f"Error processing {symbol} [{tf_key}]: {e}")
        if stopped_early:
            break
        last_checked[tf_key] = now_ts_
        save_state(state)  # ذخیره‌ی تدریجی تا در صورت قطع‌شدن، پیشرفت از دست نرود

    save_state(state)
    if stopped_early:
        logger.info("⏸️ Cycle paused early (time budget) - will resume remaining timeframes next cycle")
        return

    # ---- رهگیری زنده‌ی REST برای معاملات باز (طلا همیشه از همین مسیر؛ کریپتو فقط پشتیبان) ----
    live_last_checked = state.setdefault("live_last_checked", {})
    open_combos = [key for key, s in candle_states.items() if s.get("open_trade") and not s["open_trade"].get("closed")]
    due_live = [key for key in open_combos if (now_ts_ - live_last_checked.get(key, 0)) >= LIVE_CHECK_INTERVAL_SECONDS]
    due_live.sort(key=lambda k: live_last_checked.get(k, 0))
    due_live = due_live[:MAX_LIVE_CHECKS_PER_RUN]

    if due_live:
        logger.info(f"REST live-price check due for {len(due_live)} open trade(s)")
    for state_key in due_live:
        if time.time() - run_start > MAX_RUN_SECONDS:
            logger.warning("Time budget reached during live-price pass - will resume next cycle")
            break
        sym_state = candle_states[state_key]
        trade = sym_state.get("open_trade")
        if not trade or trade.get("closed"):
            continue
        symbol = trade.get("symbol") or state_key.split("|")[0]
        tf_key = trade.get("tf_key") or "manual"
        display = trade.get("display") or WATCHLIST_SYMBOLS.get(symbol, symbol)
        tf_cfg = TIMEFRAMES.get(tf_key, MANUAL_TF_CFG)
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
    logger.info("✅ Scan cycle complete")


# ================== استریم زنده‌ی WebSocket برای بیت‌کوین/اتریوم (دقت زیر ۱۰ ثانیه، بدون هزینه‌ی API) ==================
#
# ⚠️ توجه: اتصال واقعی این بخش را نمی‌توانم از محیط توسعه‌ی خودم تست کنم (دسترسی شبکه ندارم).
# منطق پردازش پیام (پارس‌کردن قیمت، تطبیق با معامله‌ی باز) کامل تست‌شده، ولی خودِ اتصال
# باید بعد از دیپلوی، از لاگ Actions تایید بشه.
#
# منبع اصلی حالا Binance Futures (طبق درخواست شما) است - مشخصاً از stream «mark price» که
# یک قیمت شاخص‌محورِ نسبتاً هموارشده‌ست (نه صرفاً آخرین معامله‌ی یک صرافی)، دقیقاً برای
# مقاوم‌بودن در برابر یک ویک/تیک لحظه‌ای غیرواقعی روی یک صرافی طراحی شده - همون نوع اتفاقی
# که باعث شد یک‌بار پیام «استاپ خورد» فرستاده بشه درحالی‌که قیمت واقعی به استاپ نرسیده بود.
#
# ⚠️⚠️ هشدار زیرساختی مهم: طبق تجربه‌ی قبلی این پروژه، API صرافی Binance از IPهای
# GitHub Actions (که در دیتاسنترهای آمریکا هستن) بلاک می‌شه - دقیقاً همون دلیلی که باعث شد
# قبلاً کلاً از Binance به Twelve Data/Coinbase مهاجرت کنیم. برای همین این نسخه اول Binance
# Futures رو امتحان می‌کنه، ولی اگه بعد از چند تلاش وصل نشد، خودکار (با یک لاگ هشدار واضح)
# به Coinbase (که قبلاً روی همین زیرساخت تست‌شده و کار می‌کنه) سوییچ می‌کنه - تا سیستم هیچ‌وقت
# بدون قیمت زنده نمونه. حتماً بعد از دیپلوی این نسخه لاگ Actions رو چک کنید:
#   - اگه دیدید «🔌 Binance Futures WebSocket connected» → منبع واقعی Binance Futures است.
#   - اگه دیدید «⚠️ Binance unreachable — falling back to Coinbase» → یعنی همون بلاک قبلی
#     هنوز برقراره و منبع واقعی فعلی Coinbase مونده (نه یک باگ - یک fallback عمدی).

BINANCE_SYMBOL_MAP = {"BTC/USD": "btcusdt", "ETH/USD": "ethusdt"}
BINANCE_SYMBOL_MAP_REVERSE = {v.upper(): k for k, v in BINANCE_SYMBOL_MAP.items()}
BINANCE_FUTURES_WS_URL = ("wss://fstream.binance.com/stream?streams="
                           + "/".join(f"{s}@markPrice@1s" for s in BINANCE_SYMBOL_MAP.values()))
BINANCE_CONNECT_TIMEOUT_SECONDS = 20   # این‌قدر فرصت می‌دیم Binance وصل بشه قبل از fallback
BINANCE_MAX_ATTEMPTS = 3               # تعداد تلاش مجدد Binance قبل از تسلیم‌شدن به Coinbase

WS_URL = "wss://ws-feed.exchange.coinbase.com"
WS_SYMBOL_MAP = {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD"}
WS_SYMBOL_MAP_REVERSE = {v: k for k, v in WS_SYMBOL_MAP.items()}
MANUAL_TF_CFG = {"td_interval": None, "bar_seconds": None, "label": ""}  # بدون برچسب — فقط خودِ نماد نشون داده می‌شه (توضیح در _dt)
MANUAL_SIGNALS_FILE = os.path.join(DATA_DIR, "manual_signals.json")


def parse_binance_mark_price_message(raw_message: str) -> Optional[Dict[str, Any]]:
    """یک پیام markPriceUpdate از Binance Futures رو پارس می‌کنه و
    {"symbol": "BTC/USD", "price": 65000.1} برمی‌گردونه، یا None اگه پیام مرتبط نبود."""
    try:
        data = json.loads(raw_message)
    except Exception:
        return None
    payload = data.get("data", data)  # پشتیبانی از هم فرمت combined-stream و هم تک‌استریم
    if not isinstance(payload, dict) or payload.get("e") != "markPriceUpdate":
        return None
    sym = (payload.get("s") or "").upper()
    price_str = payload.get("p")
    if not sym or price_str is None or sym not in BINANCE_SYMBOL_MAP_REVERSE:
        return None
    try:
        return {"symbol": BINANCE_SYMBOL_MAP_REVERSE[sym], "price": float(price_str)}
    except (TypeError, ValueError):
        return None


def parse_ws_ticker_message(raw_message: str) -> Optional[Dict[str, Any]]:
    """یک پیام ticker از Coinbase رو پارس می‌کنه و {"symbol": "BTC/USD", "price": 65000.1} برمی‌گردونه،
    یا None اگه پیام مرتبط نبود. جدا از اتصال شبکه نوشته شده تا بشه بدون نیاز به سرور واقعی تستش کرد."""
    try:
        data = json.loads(raw_message)
    except Exception:
        return None
    if data.get("type") != "ticker":
        return None
    product_id = data.get("product_id")
    price_str = data.get("price")
    if not product_id or not price_str or product_id not in WS_SYMBOL_MAP_REVERSE:
        return None
    try:
        return {"symbol": WS_SYMBOL_MAP_REVERSE[product_id], "price": float(price_str)}
    except (TypeError, ValueError):
        return None


def _run_binance_stream(latest_prices: Dict[str, float], price_lock, stop_event, connected_flag: Dict[str, bool]) -> None:
    import websocket

    def on_open(ws):
        logger.info("🔌 Binance Futures WebSocket connected (mark price stream)")
        connected_flag["ok"] = True

    def on_message(ws, message):
        parsed = parse_binance_mark_price_message(message)
        if parsed:
            with price_lock:
                latest_prices[parsed["symbol"]] = parsed["price"]

    def on_error(ws, error):
        logger.warning(f"Binance Futures WebSocket error: {error}")

    def on_close(ws, code, msg):
        logger.warning(f"Binance Futures WebSocket closed (code={code}, msg={msg})")

    backoff = 5
    attempts = 0
    while not stop_event.is_set() and attempts < BINANCE_MAX_ATTEMPTS:
        attempts += 1
        try:
            ws_app = websocket.WebSocketApp(BINANCE_FUTURES_WS_URL, on_open=on_open, on_message=on_message,
                                             on_error=on_error, on_close=on_close)
            ws_app.run_forever(ping_interval=20, ping_timeout=10)
            if connected_flag["ok"]:
                backoff = 5
                attempts = 0  # حداقل یک‌بار موفق وصل شده بود؛ شمارنده‌ی تلاش رو صفر کن
        except Exception as e:
            logger.error(f"Binance Futures WebSocket connection failed: {e}")
        if stop_event.is_set():
            return
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def _run_coinbase_stream(latest_prices: Dict[str, float], price_lock, stop_event) -> None:
    product_ids = list(WS_SYMBOL_MAP.values())

    def on_open(ws):
        logger.info(f"🔌 Coinbase WebSocket connected (fallback) — subscribing to {product_ids}")
        ws.send(json.dumps({"type": "subscribe", "product_ids": product_ids, "channels": ["ticker"]}))

    def on_message(ws, message):
        parsed = parse_ws_ticker_message(message)
        if parsed:
            with price_lock:
                latest_prices[parsed["symbol"]] = parsed["price"]

    def on_error(ws, error):
        logger.warning(f"Coinbase WebSocket error: {error}")

    def on_close(ws, code, msg):
        logger.warning(f"Coinbase WebSocket closed (code={code}, msg={msg})")

    backoff = 5
    while not stop_event.is_set():
        try:
            ws_app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message,
                                             on_error=on_error, on_close=on_close)
            ws_app.run_forever(ping_interval=20, ping_timeout=10)
            backoff = 5
        except Exception as e:
            logger.error(f"Coinbase WebSocket connection failed: {e}")
        if stop_event.is_set():
            break
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def start_price_stream(latest_prices: Dict[str, float], price_lock, stop_event) -> None:
    """در یک ترد جداگانه اجرا می‌شه. اول Binance Futures رو امتحان می‌کنه (منبع درخواستی)؛
    اگه ظرف BINANCE_CONNECT_TIMEOUT_SECONDS ثانیه وصل نشه یا بعد از چند تلاش قطع بمونه،
    خودکار به Coinbase سوییچ می‌کنه (توضیح کامل بالای فایل)."""
    try:
        import websocket  # noqa: F401 — فقط برای اطمینان از نصب بودن، قبل از هر تلاش اتصال
    except ImportError:
        logger.error("کتابخانه‌ی websocket-client نصب نیست؛ استریم زنده غیرفعال می‌ماند (فقط REST پشتیبان کار می‌کند)")
        return

    connected_flag = {"ok": False}
    binance_stop = threading.Event()
    binance_thread = threading.Thread(
        target=_run_binance_stream, args=(latest_prices, price_lock, binance_stop, connected_flag), daemon=True,
    )
    binance_thread.start()

    deadline = time.time() + BINANCE_CONNECT_TIMEOUT_SECONDS
    while time.time() < deadline and not stop_event.is_set() and not connected_flag["ok"]:
        time.sleep(1)

    if connected_flag["ok"]:
        logger.info("✅ Using Binance Futures (mark price) as the live price source")
        while not stop_event.is_set():
            time.sleep(1)
        binance_stop.set()
        return

    logger.warning("⚠️ Binance unreachable — falling back to Coinbase (see comment above this section "
                    "about the known GitHub Actions IP block)")
    binance_stop.set()
    _run_coinbase_stream(latest_prices, price_lock, stop_event)


def check_ws_open_trades(latest_prices: Dict[str, float], price_lock) -> None:
    """معاملات باز بیت‌کوین/اتریوم (چه خودکار چه دستی‌ ادمین) رو با آخرین قیمتِ رسیده از
    WebSocket چک می‌کنه - این تابع هر چند ثانیه یک‌بار در حلقه‌ی اصلی صدا زده می‌شه، بدون
    هیچ هزینه‌ی API. روی هر ورودی candle_states کار می‌کنه (state_key هرچی باشه، اتوماتیک
    یا MANUAL|...)، چون نماد از خودِ trade["symbol"] خونده می‌شه، نه از پارس‌کردن کلید."""
    with price_lock:
        prices_snapshot = dict(latest_prices)
    if not prices_snapshot:
        return

    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    changed = False

    for state_key, sym_state in candle_states.items():
        trade = sym_state.get("open_trade")
        if not trade or trade.get("closed"):
            continue
        symbol = trade.get("symbol")
        if symbol not in WS_SYMBOL_MAP:
            continue
        live_price = prices_snapshot.get(symbol)
        if not live_price:
            continue
        display = trade.get("display") or WATCHLIST_SYMBOLS.get(symbol, symbol)
        tf_key = trade.get("tf_key") or "manual"
        tf_cfg = TIMEFRAMES.get(tf_key, MANUAL_TF_CFG)
        events = check_open_trade_live(trade, live_price)
        if events:
            changed = True
        for ev in events:
            _send_exit(display, tf_key, tf_cfg, trade, ev, sym_state["hist"], symbol, live_price=live_price)

    if changed:
        save_state(state)


def main():
    if not TELEGRAM_BOT_TOKEN or not PRIVATE_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN or PRIVATE_CHANNEL_ID not set - exiting")
        return
    if not TWELVEDATA_API_KEY:
        logger.error("TWELVEDATA_API_KEY not set - exiting")
        return

    latest_prices: Dict[str, float] = {}
    price_lock = threading.Lock()
    stop_event = threading.Event()

    ws_thread = threading.Thread(target=start_price_stream, args=(latest_prices, price_lock, stop_event), daemon=True)
    ws_thread.start()
    logger.info("🚀 Continuous engine started — WebSocket monitors BTC/ETH live, "
                f"full scan cycle every {SCAN_CYCLE_INTERVAL_SECONDS}s, will run up to {LOOP_MAX_SECONDS/3600:.1f}h")

    start = time.time()
    last_scan = 0  # صفر یعنی همون اول یک اسکن کامل انجام بشه
    last_commit = start

    while time.time() - start < LOOP_MAX_SECONDS:
        try:
            check_ws_open_trades(latest_prices, price_lock)
        except Exception as e:
            logger.error(f"check_ws_open_trades failed: {e}")

        if time.time() - last_scan >= SCAN_CYCLE_INTERVAL_SECONDS:
            try:
                run_scan_cycle()
            except Exception as e:
                logger.error(f"run_scan_cycle failed: {e}")
            last_scan = time.time()

        if time.time() - last_commit >= GIT_COMMIT_EVERY_SECONDS:
            git_commit_and_push()
            last_commit = time.time()

        time.sleep(WS_CHECK_INTERVAL_SECONDS)

    stop_event.set()
    git_commit_and_push()
    logger.info("✅ Loop finished for this session (next scheduled run continues seamlessly)")


if __name__ == "__main__":
    main()

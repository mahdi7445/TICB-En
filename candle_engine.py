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
import re
import math
import time
import json
import logging
import traceback
import threading
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

# ⚠️ منطق git add/commit/pull --rebase/push (با retry فوری روی برخورد push بین این اسکریپت
# و subscription_bot.py) از اینجا میاد - قبلاً یک نسخه‌ی دستیِ جداگانه (بدون retry فوری) اینجا
# تعریف شده بود که از نسخه‌ی shared_git_sync.py (که خودِ همین کدبیس برای رفع همین drift ساخته
# بود) عقب افتاده بود؛ یعنی دقیقاً همون مشکلی که shared_git_sync.py قرار بود جلویش را بگیرد،
# چون آن ماژول نوشته شده بود ولی هیچ‌جا import/استفاده نمی‌شد. رفع شد.
from shared_git_sync import sync_data_dir, atomic_write_json, read_json_resilient

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("CandleEngineV4")

# ================== تنظیمات ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PRIVATE_CHANNEL_ID = os.getenv("PRIVATE_CHANNEL_ID", "").strip()
# ⚠️ برای گزارش خطای خودکار: اگه یک استثنای غیرمنتظره توی حلقه‌ی اصلی رخ بده، به‌جای اینکه
# فقط توی لاگ Actions (که کسی معمولاً چک نمی‌کنه) دفن بشه، مستقیم به ادمین(ها) روی تلگرام
# پیام می‌ده. همون ADMIN_USER_IDS که در subscription_bot.py هم هست - یک سکرت مشترک.
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").replace(" ", "").split(",") if x}
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
FORCE_RUN_ALL = os.getenv("FORCE_RUN_ALL", "").strip() == "1"

# ⚠️ نقطه‌کور واقعی که notify_admin نمی‌تونه پوشش بده: notify_admin فقط وقتی کار می‌کنه که
# خودِ این پروسه هنوز زنده و در حال اجرا باشه. اگه GitHub Actions به‌طور کامل متوقف بشه (سهمیه‌ی
# رایگان Actions تمام بشه، یک تغییر تصادفی در تنظیمات ریپو workflow رو غیرفعال کنه، یا خودِ
# self-chain مکانیزمِ تداوم به هر دلیلی پاره بشه) - هیچ هشداری نمی‌رسه، چون چیزی که قرار بود
# هشدار بده خودش خاموشه. این یک «dead man's switch» ساده و کاملاً اختیاریه: اگه HEALTHCHECK_URL
# ست بشه (مثلاً یک آدرس رایگان از healthchecks.io یا uptimerobot.com/heartbeat)، این پروسه هر
# چند دقیقه یک‌بار (هم‌زمان با هر git commit، در main پایین‌تر) یک GET سبک به همون آدرس می‌زنه؛
# سرویسِ بیرونی اگه بیش از یک بازه‌ی مشخص (مثلاً ۱۵-۲۰ دقیقه) هیچ pingی نبینه، خودش مستقل و
# بیرون از این کدبیس به ادمین هشدار می‌ده - دقیقاً پوششِ همون سناریویی که notify_admin از داخل
# نمی‌تونه بده. اگه این env var ست نشه، این تابع کاملاً no-op است - هیچ تاثیری روی رفتار فعلی
# نداره.
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL_ENGINE", "").strip()


def send_heartbeat():
    if not HEALTHCHECK_URL:
        return
    try:
        requests.get(HEALTHCHECK_URL, timeout=10)
    except Exception:
        pass  # عمداً بی‌صدا - یک ping ناموفق خودش موضوعِ هشدار نیست؛ فقط یعنی سرویس بیرونی این‌بار خبردار نمی‌شه


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
# ⚠️ این ثابت‌ها و compute_final_r از shared_risk_config.py میان - قبلاً اینجا و توی
# subscription_bot.py دوبار (با کامنت هشدار «باید دستی هماهنگ نگهش داری») تعریف شده بودن؛
# حالا فقط یک نسخه هست و هر دو فایل از همینجا import می‌کنن (توضیح کامل توی خودِ اون فایل).
from shared_risk_config import (
    RR_TARGETS, TARGET_LABELS, W1, W2, W3, W4, W_RUNNER, TRAILING_R_MULT, compute_final_r,
    VALID_TIMEFRAME_LABELS,
)

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
    "4h":  {"td_interval": "4h",    "bar_seconds": 4 * 60 * 60,   "label": "4H"},
    # ⚠️ طبق درخواست شما 4h دوباره اضافه شد (زیر یک‌ساعته‌ها هنوز تمرکز اصلی‌ان، 4h فقط برای
    # پوشش کامل‌تر). چون بودجه‌ی رایگان Twelve Data ثابت (۸۰۰ درخواست/روز) است و اضافه‌کردن
    # 4h (~۶ درخواست/روز/نماد، یعنی ۱۲/روز جمعاً برای BTC+ETH) خودش رایگون نیست، فاصله‌ی چک
    # 1m کمی عقب‌تر رفت (پایین‌تر ببینید) تا جا باز بشه - تنها جایی که واقعاً بودجه‌ی آزاد
    # داشت، چون 5m/15m/1h همین الان هم دقیقاً به‌اندازه‌ی بسته‌شدن کندل خودشون چک می‌شن.
}

# 🔴 گارد ضدِ drift: subscription_bot.py مستقل از این دیکشنری، از VALID_TIMEFRAME_LABELS
# (در shared_risk_config.py) برای اعتبارسنجیِ تایم‌فریم هر سیگنالِ ورودی (دستی/تگ‌شده/
# فوروارد‌شده) استفاده می‌کنه - چون خودش عمداً سبک نگه داشته شده و این دیکشنریِ کامل (با
# td_interval/bar_seconds) رو import نمی‌کنه. اگه یک تایم‌فریم اینجا اضافه/حذف بشه بدون
# اینکه VALID_TIMEFRAME_LABELS هم به‌روز بشه، subscription_bot.py سیگنال‌های همون تایم‌فریم
# جدید رو رد می‌کنه (یا برعکس، برچسبی که اینجا دیگه وجود نداره رو قبول می‌کنه) - این assert
# محض اطمینان، همون لحظه‌ی بالا اومدن candle_engine.py این ناهماهنگی رو با خطای واضح نشون
# می‌ده، نه بعد از هفته‌ها با یک باگ گنگ.
assert {cfg["label"] for cfg in TIMEFRAMES.values()} == VALID_TIMEFRAME_LABELS, (
    "TIMEFRAMES labels drifted from shared_risk_config.VALID_TIMEFRAME_LABELS — update both together."
)


TF_CHECK_INTERVAL_SECONDS = {
    "1m": 12 * 60,   # با اضافه‌شدن 4h، از ۱۱ به ۱۲ دقیقه عقب رفت تا بودجه‌ی 4h تامین بشه
    "5m": 10 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,  # دقیقاً هم‌اندازه‌ی خودِ کندل - هر بار درست وقتی یک کندل 4h جدید بسته
                         # می‌شه چک می‌شه، نه بیشتر (بی‌فایده بود چک بیشتر از این)
}
# جمع تخمینی: (120+144+96+24+6) × ۲ نماد = ۳۹۰ × ۲ = ۷۸۰ درخواست/روز، زیر سقف ۸۰۰ (حدود ۲۰ باقی‌مونده)
# ⚠️ 5m/15m/1h/4h همین الان هم دقیقاً به‌اندازه‌ی فاصله‌ی بسته‌شدن کندل خودشون چک می‌شن - یعنی از
# نظر بودجه‌ای دیگه جایی برای فشرده‌تر شدن ندارن (چک بیشتر از این باعث درخواست‌های تکراری
# روی یک کندل بسته‌نشده می‌شد، نه سیگنال زودتر). فقط 1m یک‌کم شل‌تره (هنوز خیلی کندتر از
# خودِ کندل ۶۰ثانیه‌ای چک می‌شه) و تنها جاییه که اگه لازم شد می‌شه بیشتر فشردش کرد.

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
    return read_json_resilient(STATE_FILE, {}, label="candle_state.json",
                                on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))


def save_state(state: Dict[str, Any]):
    atomic_write_json(STATE_FILE, state)


# ================== شناسه‌ی یکتای هر سیگنال ==================
#
# طبق درخواست صریح: «هر سیگنال شناسه‌ی خاص خودش را داشته باشد و از زمان صادر شدن تا زمان
# بسته شدن، کامل مسیر حرکتی‌اش رهگیری بشه». قبل از این، هیچ شناسه‌ی پایدار/یکتایی برای یک
# سیگنالِ مشخص وجود نداشت: سیگنال‌های خودکار فقط با state_key یعنی f"{symbol}|{tf_key}"
# (مثلاً "BTC/USD|4h") شناخته می‌شدن - که یک «سطل» است، نه شناسه‌ی یک سیگنال؛ همین‌که یک
# سیگنال در آن سطل بسته می‌شد، همون کلید بلافاصله برای سیگنال کاملاً بعدیِ همون نماد/تایم‌فریم
# دوباره استفاده می‌شد - یعنی مثلاً "BTC/USD|4h" در طول یک ماه می‌تونست ده‌ها سیگنالِ کاملاً
# متفاوت رو یکی‌یکی نمایندگی کنه، بدون هیچ راهی برای اشاره‌ی دقیق به «همون یکی که دیروز باز
# شد». سیگنال‌های دستی/فوروارد‌شده هم فقط یک id موقتِ صفِ پردازش داشتن (manual_signals.json،
# با دقت ثانیه - m-{unix_ts} - که در تئوری هم می‌تونست بین دو سیگنال هم‌زمان تصادفی تکرار
# بشه)، نه یک شناسه‌ی پایدار روی خودِ معامله که تا لحظه‌ی بسته‌شدن و ثبت در trade_history.json
# دنبالش بشه.
#
# راه‌حل: یک شناسه‌ی ساده، خوانا و پیوسته («TC-000001»، «TC-000002»، ...) که همون لحظه‌ی باز
# شدنِ هر معامله (چه خودکار چه دستی/فوروارد - هر دو از open_new_trade پایین‌تر رد می‌شن)
# ساخته می‌شه و تا لحظه‌ی بسته‌شدن و ثبت نهایی در trade_history.json (log_trade_result)، روی
# خودِ آبجکتِ trade می‌مونه - در تمام پیام‌های کانال (ورود/تارگت/استاپ/بسته‌شدن) هم نمایش داده
# می‌شه، پس کاربر/ادمین می‌تونه دقیقاً به همون یک سیگنال مشخص اشاره کنه (مثلاً در تیکت پشتیبانی
# یا با /admin_close_trade)، حتی بعد از اینکه سطلش (state_key) با یک سیگنال جدید دیگه پر شده.
#
# چرا یک فایلِ کاملاً جدا (نه یک فیلد داخل candle_state.json): تنها نویسنده‌ی این شمارنده
# خودِ candle_engine.py است - یک پردازش پیوسته‌ی تک‌رشته‌ای برای تصمیم‌گیری (رشته‌های
# WebSocket فقط latest_prices را پر می‌کنن، هیچ‌وقت معامله باز نمی‌کنن - نگاه کنید به
# start_price_stream/_run_binance_stream/_run_coinbase_stream)؛ subscription_bot.py هم
# هیچ‌وقت مستقیماً یک معامله باز نمی‌کنه، فقط در manual_signals.json صف می‌کنه که همینجا
# (process_manual_signals) پردازش می‌شه. یعنی هیچ رقابتِ بین‌فرآیندی روی *تخصیصِ* خودِ عدد
# وجود نداره - فقط تعارض معمولیِ git push/pull (که مثل بقیه‌ی فایل‌ها با retry فوریِ
# shared_git_sync.py حل می‌شه) ممکنه، و چون شماره‌ی بعدی همیشه از روی آخرین مقدارِ *محلیِ*
# با موفقیت خوانده‌شده افزایش پیدا می‌کنه (نه یک حدس از حافظه)، حتی یک تعارض/عقب‌افتادگیِ گذرا
# هم هیچ‌وقت باعث تخصیصِ تکراری یا گم‌شدنِ یک شماره نمی‌شه.
SIGNAL_ID_COUNTER_FILE = os.path.join(DATA_DIR, "signal_id_counter.json")
# 🔴 قبلاً این مقدار داخل کد هاردکد بود ("TC" همیشه) - یعنی دیپلوی دومِ آلتکوین (که دقیقاً
# همین فایل candle_engine.py رو، فقط با env متفاوت، جداگانه اجرا می‌کنه) مجبور بود یک کپیِ
# دستیِ divergedِ همین فایل با این یک خط عوض‌شده (SIGNAL_ID_PREFIX = "ALT") نگه داره - دقیقاً
# همون الگوی drift که shared_risk_config.py/shared_git_sync.py برای رفعش ساخته شدن، اینجا
# هنوز باقی مونده بود. الان از یک متغیر محیطی خونده می‌شه (پیش‌فرض "TC") تا هر دو دیپلوی از
# عیناً یک فایل کد استفاده کنن و فقط env فرق کنه - دیپلوی آلتکوین باید SIGNAL_ID_PREFIX=ALT
# رو به‌عنوان یک secret/env جدید ست کنه (بدون هیچ تغییر کدی).
SIGNAL_ID_PREFIX = (os.environ.get("SIGNAL_ID_PREFIX", "TC").strip().upper() or "TC")


def next_signal_id() -> str:
    current = read_json_resilient(
        SIGNAL_ID_COUNTER_FILE, {"next": 1}, label="signal_id_counter.json",
        on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    n = int(current.get("next", 1))
    atomic_write_json(SIGNAL_ID_COUNTER_FILE, {"next": n + 1})
    return f"{SIGNAL_ID_PREFIX}-{n:06d}"


def _new_trade_event(event_type: str, price: Optional[float] = None, level_r: Optional[int] = None,
                      note: Optional[str] = None) -> Dict[str, Any]:
    """یک رکورد استانداردِ «مسیر حرکتی» می‌سازه - همیشه با timestamp دقیق، تا کل تاریخچه‌ی
    یک سیگنال (از باز شدن تا هر تارگت/استاپ تا بسته شدن نهایی) با زمان و قیمتِ دقیقِ هر
    مرحله قابل بازسازی باشه، نه فقط یک بولینِ hit["1"]=True بدون هیچ زمان/قیمتی."""
    ev = {"type": event_type, "ts": datetime.now(timezone.utc).isoformat()}
    if price is not None:
        ev["price"] = price
    if level_r is not None:
        ev["level_r"] = level_r
    if note:
        ev["note"] = note
    return ev


_GIT_ERROR_MESSAGES = {
    "not_a_repo": lambda msg: f"candle_engine.py: {msg}",
    "add_failed": lambda msg: f"candle_engine.py: git add data/ failed: {msg}",
    "commit_failed": lambda msg: f"candle_engine.py: git commit failed: {msg}",
    "rebase_conflict": lambda msg: (
        f"candle_engine.py: {msg} Won't show up in /results until this resolves."),
    "push_failed": lambda msg: (
        f"candle_engine.py: {msg} Won't show up in subscription_bot.py's /results until this succeeds."),
}


def git_commit_and_push(final: bool = False):
    """Thin wrapper حول sync_data_dir مشترک (shared_git_sync.py) - همون git add/commit/
    pull --rebase (با retry فوری روی برخورد push، + resolve معنایی تعارض‌های آرایه‌ای)
    که subscription_bot.py هم استفاده می‌کنه، تا این دو دیگه هیچ‌وقت دو نسخه‌ی مستقل/
    drift‌شده از همین منطق نداشته باشن (توضیح کامل در docstring خودِ shared_git_sync.py
    و کامنت بالای importش در این فایل).

    final=True فقط برای همون یک فراخوانیِ آخرِ main() (درست قبل از پایان Job، خط پایانی
    حلقه‌ی ~۵ ساعت و ۲۰ دقیقه) استفاده می‌شه: 🔴 چون بعد از این فراخوانی دیگه «دور بعدی»
    وجود نداره که خودش retry کنه - اگه همینجا شکست بخوره، هرچی از آخرین commit موفق تا
    الان جمع شده (سیگنال‌های تازه‌بسته‌شده‌ی این چند دقیقه‌ی آخر) با از بین رفتن checkout
    این Job برای همیشه گم می‌شه. پس اینجا صبورتر عمل می‌کنیم: تعداد retry بیشتر و فاصله‌ی
    retry بلندتر (تا حدود یک دقیقه‌ی کامل تلاش، به‌جای چند ثانیه‌ی معمول)، تا یک مشکل
    گذرای شبکه در همین چند لحظه‌ی آخر هم فرصت خودش رو برای حل‌شدن داشته باشه."""
    repo_dir = os.path.dirname(os.path.abspath(__file__))

    def on_error(kind: str, message: str) -> None:
        logger.warning(f"[git] {kind}: {message}")
        text = _GIT_ERROR_MESSAGES.get(kind, lambda m: f"candle_engine.py: {m}")(message)
        if final:
            text += (" (این تلاشِ نهایی قبل از پایان Job بود - هرچی الان push نشده باشه، "
                      "تا اجرای بعدی هم دیگه در دسترس نیست و باید دستی بررسی بشه.)")
        notify_admin(f"git_{kind}", Exception(text))

    if final:
        sync_data_dir(repo_dir, "update candle/trade state [skip ci]", on_error,
                       max_retries=10, retry_delay_range=(3, 8))
    else:
        sync_data_dir(repo_dir, "update candle/trade state [skip ci]", on_error)


CLOSE_TYPE_LABELS = {
    "stop": "Stopped out (full loss)",
    "breakeven": "Breakeven (Target 1 banked, rest closed at entry)",
    "sl_after_t2": "Stopped after Target 2 (Target 1-2 banked, rest closed at Target 1 price)",
    "sl_after_t3": "Stopped after Target 3 (Target 1-3 banked, rest closed at Target 2 price)",
    "runner_stop": "Runner closed (all 4 targets + trailing runner)",
    "opposite_signal": "Closed early - opposite signal appeared",
    # ⚠️ برای سیگنال‌های فورواردی (forwarded_tracking=True) که رهگیری‌شون از روی خودِ پیام‌های
    # نتیجه‌ی فوروارد‌شده انجام می‌شه، نه قیمت زنده‌ی خودمون - جزئیات کامل در
    # process_forwarded_results پایین‌تر.
    "forwarded_closed": "Closed (reported via a forwarded result message)",
}


DAILY_REPORT_HOUR_UTC = int(os.environ.get("DAILY_REPORT_HOUR_UTC") or "21")  # ساعت ارسال گزارش روزانه (UTC)


def format_daily_report_message(date_str: str, trades: List[Dict[str, Any]], issued_today: int = None) -> str:
    """
    ⚠️ فرمت این تابع بر اساس گزارش روزانه‌ی کانال آلتکوین (دیپلوی دومِ این سیستم، که این‌جا
    دیده شد حرفه‌ای‌تر/مفیدتر بود) بازنویسی شد: به‌جای فقط لیست خام معامله‌به‌معامله، آمار
    تجمیعی (win rate کلی + تفکیک به ازای هر نماد و هر تایم‌فریم + شمار رسیدن به هر تارگت)
    نشون می‌ده - که هم تصویر کلی‌تری از عملکرد روزانه می‌ده، هم برای کانالی با تعداد سیگنال
    بالا (که لیست خام‌اش خیلی طولانی می‌شه) خواناتره.

    خط «every signal sent, no cherry-picking» عمداً حفظ شده: چون trade_history.json واقعاً
    شامل همه‌ی معاملات بسته‌شده‌ست (چه سود چه ضرر)، نه فقط نمونه‌های موفق - این ادعا برای این
    گزارش هم صادقه، نه فقط برای کانال منبع.

    issued_today: تعداد سیگنال‌هایی که *همون روز صادر شدن* (نه لزوماً همون روز بسته شدن) -
    عدد جداگانه‌ای از «trades closed today» چون این دو سوال متفاوتن (یک سیگنال می‌تونه
    روزها باز بمونه). None یعنی این عدد در دسترس نیست (فراخوان قدیمی‌تر)."""
    issued_line = f"📤 New signals issued today: <b>{issued_today}</b>\n" if issued_today is not None else ""
    if not trades:
        return (f"📊 <b>DAILY SIGNAL PERFORMANCE — {date_str}</b>\n\n"
                f"{issued_line}No signals closed today.")

    total = len(trades)
    wins = sum(1 for h in trades if h["final_r"] > 0)
    breakeven = sum(1 for h in trades if h["final_r"] == 0)
    losses = sum(1 for h in trades if h["final_r"] < 0)
    total_r = sum(h["final_r"] for h in trades)
    avg_r = total_r / total
    win_pct = round(100 * wins / total)

    lines = [
        f"📊 <b>DAILY SIGNAL PERFORMANCE — {date_str}</b>",
        issued_line +
        f"<i>{total} trade{'s' if total != 1 else ''} closed today - every signal sent, "
        f"no cherry-picking</i>\n",
        f"✅ Wins: {wins} ({win_pct}%)",
        f"⚪ Breakeven: {breakeven}",
        f"❌ Losses: {losses}\n",
        f"Total: <b>{total_r:+.2f}R</b>",
        f"Average per trade: {avg_r:+.2f}R\n",
    ]

    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for h in trades:
        by_symbol.setdefault(h["symbol"], []).append(h)
    lines.append("<b>By symbol</b>")
    for sym, hs in sorted(by_symbol.items(), key=lambda kv: -sum(x["final_r"] for x in kv[1])):
        n = len(hs)
        w = sum(1 for x in hs if x["final_r"] > 0)
        r = sum(x["final_r"] for x in hs)
        lines.append(f"  {sym}: {n} trade{'s' if n != 1 else ''} · {round(100 * w / n)}% win · {r:+.2f}R")

    by_tf: Dict[str, List[Dict[str, Any]]] = {}
    for h in trades:
        # ⚠️ قبلاً فقط h.get("tf") چک می‌شد - که برای هر معامله‌ی دستی/رله‌شده (یعنی
        # تقریباً همه‌ی سیگنال‌های آلتکوین) همیشه دقیقاً "manual" است (رشته‌ای غیرخالی، پس
        # `or "Manual"` هیچ‌وقت فعال نمی‌شد) - یعنی همه‌ی این معاملات، صرف‌نظر از تایم‌فریم
        # واقعی‌شون، زیر یک سطرِ یکسانِ "manual" جمع می‌شدن. الان از logical_tf (که
        # ⚠️ از این به بعد (بعد از رفعِ اجباری‌شدنِ تایم‌فریم در subscription_bot.py +
        # candle_engine.py) عملاً غیرقابل‌وقوعه که یک معامله‌ی جدید بدون logical_tf ثبت بشه -
        # این fallback فقط برای رکوردهای قدیمی‌ترِ trade_history.json (قبل از اون رفع) که از
        # قبل بدون این فیلد ذخیره شده بودن نگه داشته شده، تا گزارش روی داده‌ی تاریخی هم کرش
        # نکنه؛ به‌مرور که این رکوردهای قدیمی از پنجره‌ی گزارش خارج بشن، این ردیف هم خودش
        # خالی می‌شه.
        tf = h.get("tf")
        if not tf or tf == "manual":
            tf = h.get("logical_tf") or "Manual (legacy/unlabeled)"
        by_tf.setdefault(tf, []).append(h)
    lines.append("\n<b>By timeframe</b>")
    for tf, hs in sorted(by_tf.items(), key=lambda kv: -sum(x["final_r"] for x in kv[1])):
        n = len(hs)
        w = sum(1 for x in hs if x["final_r"] > 0)
        r = sum(x["final_r"] for x in hs)
        lines.append(f"  {tf}: {n} trade{'s' if n != 1 else ''} · {round(100 * w / n)}% win · {r:+.2f}R")

    # شمارشِ رسیدن به هر تارگت - targets_hit روی هر تراکنش لیستی مثل ["T1","T2"]ه (برچسب‌های
    # همون TARGET_LABELS بالا، برای سطوح RR_TARGETS=[1,2,4,6])
    target_reach = {"T1": 0, "T2": 0, "T3": 0, "T4": 0}
    runner_closed = 0
    for h in trades:
        for lbl in h.get("targets_hit") or []:
            if lbl in target_reach:
                target_reach[lbl] += 1
        if h.get("close_type") == "runner_stop":
            runner_closed += 1
    lines.append("\n<b>Targets reached today</b>")
    lines.append(f"  T1 (1R, {int(W1 * 100)}%): {target_reach['T1']} reached")
    lines.append(f"  T2 (2R, {int(W2 * 100)}%): {target_reach['T2']} reached")
    lines.append(f"  T3 (4R, {int(W3 * 100)}%): {target_reach['T3']} reached")
    lines.append(f"  T4 (6R, {int(W4 * 100)}%): {target_reach['T4']} reached")
    lines.append(f"  Runner (final {int(W_RUNNER * 100)}%, trailing {TRAILING_R_MULT}R): {runner_closed} closed")

    return "\n".join(lines)


DAILY_REPORT_LOG_FILE = os.path.join(DATA_DIR, "daily_report_log.json")

# ⚠️ شفافیت عمومی: طبق ادعای اصلی کانال («هر سیگنالی صادر بشه پست می‌شه، بدون گلچین‌کردن»)،
# پین‌کردنِ آخرین گزارش (روزانه/هفتگی) در بالای کانال این ادعا رو برای هر عضو جدید، همون
# لحظه‌ی ورود و بدون هیچ اسکرولی، قابل‌اثبات می‌کنه - نه فقط قابل‌ادعا. قبل از پینِ جدید،
# پینِ قبلیِ همین مکانیزم (نه یک پینِ دستیِ ادمین برای چیز دیگه - چون شناسه‌اش رو جداگانه
# نگه می‌داریم، نه با unpinAllChatMessages که همه‌چیز رو پاک می‌کنه) برداشته می‌شه، تا
# همیشه فقط *یک* گزارش (آخرین) پین بمونه.
PINNED_REPORT_ID_FILE = os.path.join(DATA_DIR, "pinned_report_id.json")


def _pin_channel_message(message_id: Optional[int]) -> None:
    if not message_id or not TELEGRAM_BOT_TOKEN or not PRIVATE_CHANNEL_ID:
        return
    try:
        prev = read_json_resilient(PINNED_REPORT_ID_FILE, {}, label="pinned_report_id.json",
                                    on_corrupt=lambda label, msg: None)
        prev_id = prev.get("message_id")
        if prev_id and prev_id != message_id:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/unpinChatMessage",
                          data={"chat_id": PRIVATE_CHANNEL_ID, "message_id": prev_id}, timeout=15)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/pinChatMessage",
                      data={"chat_id": PRIVATE_CHANNEL_ID, "message_id": message_id, "disable_notification": True},
                      timeout=15)
        atomic_write_json(PINNED_REPORT_ID_FILE, {"message_id": message_id})
    except Exception as e:
        logger.warning(f"Failed to pin report message {message_id}: {e}")



def _load_daily_report_log() -> List[str]:
    return read_json_resilient(DAILY_REPORT_LOG_FILE, [], label="daily_report_log.json",
                                on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))


def _save_daily_report_log(dates: List[str]):
    atomic_write_json(DAILY_REPORT_LOG_FILE, dates[-60:])


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

    history = read_json_resilient(TRADE_HISTORY_FILE, [], label="trade_history.json",
                                   on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    today_trades = [h for h in history if h.get("closed_at", "").startswith(today_str)]

    # 🔴 اضافه شد: شمارشِ «چند سیگنال همون روز صادر شده» - جدا از «چند تا همون روز بسته
    # شده» (today_trades بالا). این از دو منبع جمع می‌شه: (۱) همون رکوردهای trade_history
    # که امروز بسته شدن ولی opened_at هم امروزه (یعنی صادر و بسته هر دو امروز)، به‌علاوه‌ی
    # رکوردهایی که امروز صادر شدن ولی روز دیگه‌ای بسته شدن (این‌ها امروز در today_trades
    # نیستن، پس جدا شمرده می‌شن)، و (۲) معاملاتی که الان هنوز باز هستن ولی همون امروز باز
    # شدن. توجه: برای معاملاتی که *قبل* از این فیلد (opened_at) به trade_history اضافه شدن،
    # این عدد فقط شامل بخشی می‌شه که هنوز باز مونده یا از این به بعد بسته بشه - یک محدودیتِ
    # داده‌ی تاریخی، نه باگ؛ از فردا به بعد کاملاً دقیقه.
    issued_today_from_history = sum(1 for h in history if (h.get("opened_at") or "").startswith(today_str))
    state = load_state()
    open_today = sum(
        1 for s in state.get("candle_signals", {}).values()
        if (t := s.get("open_trade")) and not t.get("closed")
        and t.get("opened_at_ms")
        and datetime.fromtimestamp(t["opened_at_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") == today_str
    )
    issued_today = issued_today_from_history + open_today

    msg = format_daily_report_message(today_str, today_trades, issued_today)
    sent_msg_id = send_photo(msg, None)
    if sent_msg_id:
        logger.info(f"📋 Daily report sent for {today_str} ({len(today_trades)} trades)")
        _pin_channel_message(sent_msg_id)
    else:
        logger.error(f"📋 Daily report FAILED to send for {today_str} — will not retry today "
                      f"(to avoid ever double-posting); check logs.")


# ================== گزارش هفتگی (دقیقاً همون معماریِ ایمنِ گزارش روزانه) ==================
#
# طبق پیشنهاد: نوسان روزانه می‌تونه گمراه‌کننده باشه (یک روز بد در وسط یک هفته‌ی کلاً خوب،
# تنها همون یک روز رو نشون می‌ده)؛ خلاصه‌ی هفتگی تصویر واقعی‌تری از روند کلی می‌ده. علاوه
# بر آمار متنی، چون این‌جا (برخلاف subscription_bot.py) matplotlib از قبل موجوده (برای چارت
# سیگنال‌ها)، یک نمودار PNG واقعی از equity curve هم پیوست می‌شه - چیزی که در بات با یک
# sparkline متنی جایگزین شد (تا وابستگی سنگین جدید بهش اضافه نشه)، این‌جا می‌شه درست انجامش داد.
WEEKLY_REPORT_WEEKDAY_UTC = int(os.environ.get("WEEKLY_REPORT_WEEKDAY_UTC") or "0")  # ۰=دوشنبه (Python weekday())
WEEKLY_REPORT_HOUR_UTC = int(os.environ.get("WEEKLY_REPORT_HOUR_UTC") or str(DAILY_REPORT_HOUR_UTC))
WEEKLY_REPORT_LOG_FILE = os.path.join(DATA_DIR, "weekly_report_log.json")


def _load_weekly_report_log() -> List[str]:
    return read_json_resilient(WEEKLY_REPORT_LOG_FILE, [], label="weekly_report_log.json",
                                on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))


def _save_weekly_report_log(week_ids: List[str]):
    atomic_write_json(WEEKLY_REPORT_LOG_FILE, week_ids[-30:])


def build_equity_chart(trades: List[Dict[str, Any]], title: str) -> Optional[str]:
    """نمودار خطیِ ساده‌ی مجموعِ تجمعیِ R به ترتیب زمانِ بسته‌شدن - برخلاف build_chart_from_hist
    (کندل‌استیک برای یک سیگنال خاص)، این یک نمودار خطی معمولی روی داده‌ی آماریه، نه قیمت."""
    if len(trades) < 2:
        return None
    ordered = sorted(trades, key=lambda h: h.get("closed_at") or "")
    cum, running = [], 0.0
    for h in ordered:
        running += h["final_r"]
        cum.append(running)
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(range(1, len(cum) + 1), cum, color="dodgerblue", linewidth=1.8)
        ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
        ax.fill_between(range(1, len(cum) + 1), cum, 0, alpha=0.08, color="dodgerblue")
        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Closed trades (in order)")
        ax.set_ylabel("Cumulative R")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(CHART_PATH, dpi=150)
        plt.close(fig)
        return CHART_PATH
    except Exception as e:
        logger.warning(f"Equity chart build failed: {e}")
        return None


def format_weekly_report_message(period_start: str, period_end: str, trades: List[Dict[str, Any]]) -> str:
    if not trades:
        return f"📈 <b>WEEKLY SIGNAL PERFORMANCE — {period_start} to {period_end}</b>\n\nNo signals closed this week."

    total = len(trades)
    wins = sum(1 for h in trades if h["final_r"] > 0)
    breakeven = sum(1 for h in trades if h["final_r"] == 0)
    losses = sum(1 for h in trades if h["final_r"] < 0)
    total_r = sum(h["final_r"] for h in trades)
    avg_r = total_r / total
    win_pct = round(100 * wins / total)

    ordered = sorted(trades, key=lambda h: h.get("closed_at") or "")
    cum, running, peak, max_dd = [], 0.0, 0.0, 0.0
    for h in ordered:
        running += h["final_r"]
        cum.append(running)
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for h in trades:
        by_symbol.setdefault(h.get("symbol", "?"), []).append(h)
    symbol_lines = []
    for sym, hs in sorted(by_symbol.items(), key=lambda kv: -sum(x["final_r"] for x in kv[1]))[:10]:
        n = len(hs)
        w = sum(1 for x in hs if x["final_r"] > 0)
        r = sum(x["final_r"] for x in hs)
        symbol_lines.append(f"  {sym}: {n} trade{'s' if n != 1 else ''} · {round(100 * w / n)}% win · {r:+.2f}R")

    return (
        f"📈 <b>WEEKLY SIGNAL PERFORMANCE — {period_start} to {period_end}</b>\n"
        f"<i>{total} trade{'s' if total != 1 else ''} closed this week — every signal we posted, no cherry-picking</i>\n\n"
        f"✅ Wins: {wins} ({win_pct}%)\n"
        f"⚪ Breakeven: {breakeven}\n"
        f"❌ Losses: {losses}\n\n"
        f"Total: <b>{total_r:+.2f}R</b>\n"
        f"Average per trade: {avg_r:+.2f}R\n"
        f"Max drawdown this week: {max_dd:.2f}R\n\n"
        f"<b>Top symbols this week</b>\n" + "\n".join(symbol_lines)
    )


def maybe_send_weekly_report():
    """هر هفته، یک‌بار در روز/ساعتِ WEEKLY_REPORT_WEEKDAY_UTC/WEEKLY_REPORT_HOUR_UTC، خلاصه‌ی
    ۷ روز گذشته رو (هم متنی هم با یک نمودار equity curve واقعی) در کانال پست می‌کنه. همون
    الگوی ایمنِ گزارش روزانه دقیقاً تکرار شده (فایل لاگِ جداگانه، mark-then-push-then-send) -
    توضیح کامل چرایی‌اش بالای maybe_send_daily_report."""
    now = datetime.now(timezone.utc)
    if now.weekday() != WEEKLY_REPORT_WEEKDAY_UTC or now.hour < WEEKLY_REPORT_HOUR_UTC:
        return

    period_end_date = now.date()
    period_start_date = period_end_date - timedelta(days=7)
    week_id = period_start_date.strftime("%Y-%m-%d")  # هر بازه‌ی ۷روزه با تاریخ شروعش شناسایی می‌شه

    git_commit_and_push()  # pull تازه - اگه یک اجرای دیگه همین الان زده و push کرده ببینیمش

    sent_weeks = _load_weekly_report_log()
    if week_id in sent_weeks:
        return

    sent_weeks.append(week_id)
    _save_weekly_report_log(sent_weeks)
    git_commit_and_push()  # فوری پوش - قبل از تلاش برای ارسال پیام

    history = read_json_resilient(TRADE_HISTORY_FILE, [], label="trade_history.json",
                                   on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    period_start_iso = period_start_date.strftime("%Y-%m-%d")
    period_end_iso = period_end_date.strftime("%Y-%m-%d")
    week_trades = [h for h in history if period_start_iso <= (h.get("closed_at") or "")[:10] < period_end_iso]

    msg = format_weekly_report_message(period_start_iso, period_end_iso, week_trades)
    chart_path = build_equity_chart(week_trades, f"Equity curve — {period_start_iso} to {period_end_iso}")
    sent_msg_id = send_photo(msg, chart_path)
    if sent_msg_id:
        logger.info(f"📈 Weekly report sent for {period_start_iso}–{period_end_iso} ({len(week_trades)} trades)")
        _pin_channel_message(sent_msg_id)
    else:
        logger.error(f"📈 Weekly report FAILED to send for {period_start_iso}–{period_end_iso} — will not retry "
                      f"this week (to avoid ever double-posting); check logs.")


def log_trade_result(symbol: str, tf_key: str, trade: Dict[str, Any], final_r_override: Optional[float] = None):
    """وقتی معامله بسته می‌شود (استاپ/بریک‌ایون/تارگت نهایی/سیگنال مخالف)، نتیجه‌ی شفاف و
    مشخص آن (چطور بسته شده + چند R) را برای آمار/سود‌وزیان کاربران ذخیره می‌کند.

    final_r_override: فقط برای سیگنال‌های فورواردی استفاده می‌شه - وقتی خودِ پیام فوروارد‌شده
    مقدار R نهایی رو مستقیم اعلام کرده (مثلاً «Result: ~2.10R»)، به‌جای دوباره‌محاسبه‌کردن با
    وزن‌های خودمون (که ممکنه با منبع اصلی کمی فرق کنه)، همون عدد گزارش‌شده مستقیم ثبت می‌شه.

    🔴 رفعِ باگِ «By timeframe در نتایج/گزارش روزانه دقیق نیست»: این تابع تا الان فقط
    trade["tf_key"] رو (که برای هر معامله‌ی دستی/رله‌شده‌ی آلتکوین همیشه دقیقاً "manual"
    است) روی رکورد trade_history ذخیره می‌کرد و trade["logical_tf"] (تایم‌فریمِ واقعیِ
    تشخیص‌داده‌شده از متن پیام منبع - مثلاً "15m") را اصلاً نمی‌نوشت. یعنی حتی بعد از اینکه
    subscription_bot.py برای /results یک fallback به logical_tf اضافه کرد، آن fallback هیچ‌وقت
    چیزی برای خوندن نداشت - چون خودِ رکورد در همین نقطه، از همون اول، logical_tf را دور
    می‌ریخت. الان logical_tf هم روی رکورد ذخیره می‌شود - هم /results (subscription_bot.py)
    هم گزارش روزانه‌ی همینجا (format_daily_report_message پایین‌تر) از همین یک فیلد مشترک
    درست می‌خونن."""
    os.makedirs(DATA_DIR, exist_ok=True)
    history = read_json_resilient(TRADE_HISTORY_FILE, [], label="trade_history.json",
                                   on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    close_type = trade.get("close_type", "unknown")
    targets_hit = [TARGET_LABELS[t].replace("Target ", "T") for t in RR_TARGETS if trade["hit"][str(t)]]
    final_r = final_r_override if final_r_override is not None else compute_final_r(trade)
    opened_at_ms = trade.get("opened_at_ms")
    closed_at_iso = datetime.now(timezone.utc).isoformat()
    # 🔴 رویدادِ نهاییِ «closed» - جدا از رویدادهای type-specific که قبلاً حین رسیدن به هر
    # تارگت/استاپ در trade["events"] ثبت شدن (check_open_trade/_force_close_trade/...) -
    # اینجا (تنها نقطه‌ی مشترکی که واقعاً برای *همه‌ی* مسیرهای بسته‌شدن صدا زده می‌شه: استاپ
    # عادی، بریک‌ایون، سیگنال مخالف، admin_override، نتیجه‌ی فوروارد‌شده) یک خلاصه‌ی نهایی با
    # همون final_r دقیقی که واقعاً محاسبه/ثبت شده اضافه می‌شه - تا آخرین رکورد این تاریخچه
    # همیشه دقیقاً نتیجه‌ی قطعی و نهایی سیگنال باشه، صرف‌نظر از این‌که از کدوم مسیر بسته شده.
    trade.setdefault("events", []).append(
        {"type": "closed", "ts": closed_at_iso, "close_type": close_type, "final_r": final_r})
    history.append({
        # 🔴 اضافه شد: شناسه‌ی یکتای همین سیگنال (توضیح کامل بالای next_signal_id) - برای
        # رجوع دقیق به همین یک معامله حتی بعد از این‌که سطلش (state_key) با سیگنال بعدی پر
        # شده، و همینطور کل مسیر حرکتی (opened -> هر target hit با قیمت/زمان دقیق -> رویداد
        # بسته‌شدن نهایی) - نه فقط خلاصه‌ی «کدام تارگت‌ها خورد» که قبلاً تنها چیزِ ذخیره‌شده بود.
        "signal_id": trade.get("signal_id"),
        "events": trade.get("events", []),
        "symbol": symbol, "tf": tf_key, "logical_tf": trade.get("logical_tf"), "side": trade["side"],
        "entry": trade["entry"], "sl": trade["sl"],
        "final_r": final_r,
        "targets_hit": targets_hit,
        "close_type": close_type,
        "close_reason": CLOSE_TYPE_LABELS.get(close_type, close_type),
        # 🔴 اضافه شد (قبلاً ذخیره نمی‌شد): زمان واقعیِ صادرشدنِ سیگنال - جدا از closed_at.
        # علتش: گزارش روزانه («DAILY SIGNAL PERFORMANCE») بر اساس closed_at گروه‌بندی می‌شه
        # (چون نتیجه/R فقط بعد از بسته‌شدن معنا داره) - ولی طبق درخواست صریح، باید بشه دقیقاً
        # فهمید «چند سیگنال همون روز صادر شده» هم، که سوال جدایی از «چند تا همون روز بسته
        # شده» است (یک سیگنال می‌تونه روزها باز بمونه قبل از بسته‌شدن). این فیلد دقیقاً
        # همون رو ممکن می‌کنه - بدون این، هیچ‌جا زمان صدورِ واقعی معامله بعد از بسته‌شدنش
        # قابل بازیابی نبود.
        "opened_at": (datetime.fromtimestamp(opened_at_ms / 1000, tz=timezone.utc).isoformat()
                      if opened_at_ms else None),
        "closed_at": closed_at_iso,
    })
    history = history[-2000:]  # جلوگیری از رشد بی‌نهایت فایل
    atomic_write_json(TRADE_HISTORY_FILE, history)


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


# ================== گزارش خطای خودکار به ادمین ==================

_LAST_ADMIN_ALERT: Dict[str, float] = {}
ADMIN_ALERT_COOLDOWN_SECONDS = 900  # اگه دقیقاً همون خطا مدام تکرار بشه (مثلاً قطعی موقت شبکه
                                     # که هر ۵ ثانیه دوباره رخ می‌ده)، هر ۱۵ دقیقه یک‌بار
                                     # پیامش می‌ده، نه هر بار - تا اسپم نشه ولی هم گم هم نشه


def notify_admin(context: str, error: Exception, reply_markup: Optional[Dict[str, Any]] = None,
                  full_text: Optional[str] = None) -> None:
    """وقتی یک بخش از حلقه‌ی اصلی (اسکن، چک زنده، و...) استثنای غیرمنتظره می‌گیره، جدا از
    لاگ Actions، مستقیم به ادمین‌ها (ADMIN_USER_IDS) هم پیام می‌ده - چون لاگ Actions معمولاً
    کسی رصد نمی‌کنه و ممکنه یک خطای مهم (مثلاً API از کار افتاده) روزها بی‌سروصدا بمونه.
    برای جلوگیری از اسپم، همون context رو حداکثر هر ADMIN_ALERT_COOLDOWN_SECONDS یک‌بار
    می‌فرسته، نه هر بار که تکرار می‌شه.

    🔴 دو پارامتر اضافه شد (هر دو اختیاری، پس هیچ‌کدام از caller های قبلی نیاز به تغییر ندارن):
    - full_text: وقتی caller (مثل check_stale_open_trades) یک پیامِ کاملاً ساختاریافته و
      غنی از جزییات خودِ سیگنال (نه صرفاً یک متن خطا) می‌خواد بفرسته، به‌جای قالبِ عمومیِ
      «Context/Error» (که برای خطاهای واقعیِ برنامه‌نویسی مناسبه، نه برای معرفیِ یک سیگنالِ
      مشخص با تمام جزییاتش)، این متن دقیقاً همون‌طور که هست فرستاده می‌شه.
    - reply_markup: طبق درخواست صریح («در زیر هر سیگنال که ربات موردی پیدا کرده، دستورات
      قابل‌انجام روی همون سیگنال آورده بشه تا با یک کلیک انجام بشه») - یک inline keyboard که
      مستقیماً زیر همین پیام نشون داده می‌شه. چون این پیام مستقیماً با Bot API فرستاده می‌شه
      (نه از طریق subscription_bot.py)، ولی callback_query هر دکمه‌ای که ادمین بزنه رو همون
      subscription_bot.py (که getUpdates پیوسته داره) دریافت و پردازش می‌کنه - کاملاً stateless
      از دید تلگرام، هیچ کوپلینگ عجیبی بین دو اسکریپت لازم نیست."""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_USER_IDS:
        return
    now = time.time()
    last = _LAST_ADMIN_ALERT.get(context, 0)
    if now - last < ADMIN_ALERT_COOLDOWN_SECONDS:
        return
    _LAST_ADMIN_ALERT[context] = now
    text = full_text if full_text is not None else (
        f"⚠️ <b>candle_engine error</b>\n"
        f"Context: <code>{context}</code>\n"
        f"Error: <code>{str(error)[:500]}</code>\n"
        f"{_now_str()}"
    )
    for admin_id in ADMIN_USER_IDS:
        try:
            data = {"chat_id": admin_id, "text": text, "parse_mode": "HTML"}
            if reply_markup:
                # درخواستِ form-encoded (data=...، نه json=...) - برخلاف subscription_bot.py
                # که با json= می‌فرسته، اینجا reply_markup باید صریحاً به رشته‌ی JSON تبدیل
                # بشه؛ وگرنه تلگرام یک دیکشنری تودرتو رو در فیلد فرم قبول نمی‌کنه.
                data["reply_markup"] = json.dumps(reply_markup)
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data=data,
                timeout=15,
            )
        except Exception as e:
            logger.error(f"notify_admin: failed to alert {admin_id}: {e}")


# ================== داده Twelve Data ==================

# ⚠️ قبلاً هیچ شمارنده‌ی زنده‌ای از مصرف روزانه‌ی Twelve Data وجود نداشت - مدیریت سهمیه‌ی
# ۸۰۰ درخواست/روز فقط از طریق «طراحیِ فرکانسِ اسکن» انجام می‌شد (نه شمارش واقعی)، یعنی اگر
# یک روز به هر دلیلی (مثلاً واچ‌لیست بزرگ‌تر شد، یا یک باگ باعث اسکن‌های تکراری شد) مصرف
# واقعی از سهمیه رد می‌شد، تنها نشانه‌اش «سیگنال‌ها یهو ساکت شدن» بود - بدون هیچ هشدار
# زودهنگام. این شمارنده (در یک فایل جداگانه‌ی کوچک داخل DATA_DIR، هر روز در UTC midnight
# خودکار صفر می‌شه) هر تماس واقعی به Twelve Data رو می‌شماره، و در ۸۰٪ سهمیه یک‌بار در روز
# به ادمین هشدار می‌ده - قبل از اینکه واقعاً به مشکل بخوریم.
TWELVEDATA_DAILY_LIMIT = int(os.getenv("TWELVEDATA_DAILY_LIMIT", "800"))
API_QUOTA_FILE = os.path.join(DATA_DIR, "api_quota_state.json")
API_QUOTA_WARN_FRACTION = 0.8


def _track_api_call() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    q = read_json_resilient(API_QUOTA_FILE, {}, label="api_quota_state.json",
                             on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    if q.get("date") != today:
        q = {"date": today, "count": 0, "warned_80": False}
    q["count"] = q.get("count", 0) + 1
    threshold = int(TWELVEDATA_DAILY_LIMIT * API_QUOTA_WARN_FRACTION)
    if q["count"] >= threshold and not q.get("warned_80"):
        q["warned_80"] = True
        pct = int(100 * q["count"] / TWELVEDATA_DAILY_LIMIT)
        notify_admin(
            f"api_quota_80pct:{today}",
            Exception(f"Twelve Data usage has hit {q['count']}/{TWELVEDATA_DAILY_LIMIT} requests today "
                      f"({pct}%) — approaching the daily free-tier limit. If it's fully used up, further "
                      f"candle/price checks will silently return nothing until the quota resets at UTC "
                      f"midnight (signals may just go quiet with no other symptom). If this happens often, "
                      f"consider a paid Twelve Data plan or trimming the watchlist/scan frequency."),
        )
    atomic_write_json(API_QUOTA_FILE, q)


def fetch_closed_klines(symbol: str, limit: int, interval: str, bar_seconds: int) -> List[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        return []
    _track_api_call()
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
    _track_api_call()
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

def _signal_id_in_use(signal_id: str, candle_states: Dict[str, Any]) -> bool:
    """چک می‌کنه یک signal_id همین الان روی یک معامله‌ی هنوز-باز دیگه استفاده شده یا نه -
    فقط برای گارد ایمنیِ preferred_signal_id (پایین‌تر)، نه یک چک جامع در برابر کل
    trade_history.json (که برای این مورد نادر لازم نیست - اگه یک ID قدیمی‌تر دوباره فوروارد
    بشه، is_duplicate_signal/دیگر چک‌های duplicate بالادست از قبل جلوش رو می‌گیرن)."""
    for s in candle_states.values():
        t = (s or {}).get("open_trade")
        if t and not t.get("closed") and t.get("signal_id") == signal_id:
            return True
    return False


def open_new_trade(signal: Dict[str, Any], symbol: str = None, tf_key: str = None, display: str = None,
                    preferred_signal_id: str = None, candle_states: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    entry = signal["price"]
    sl = signal["sl"]
    r = abs(entry - sl)
    if r <= 0:
        return None
    # 🔴 اضافه شد: برای سیگنال‌های دستی/فوروارد/رله‌شده که خودشون از قبل یک شناسه‌ی 🆔 در متن
    # منبع دارن (مثلاً «ALT-000005» از دیپلوی دوم آلتکوین)، به‌جای همیشه ساختن یک TC-XXXXXX
    # تازه، همون شناسه‌ی منبع مستقیماً به‌عنوان signal_id همین معامله استفاده می‌شه. چرا مهمه:
    # قبلاً پیام‌هایی که در کانال با copyMessage عیناً منتشر می‌شدن (رله‌ی آلتکوین، silent=True)
    # همیشه شناسه‌ی خودِ منبع («ALT-000005») رو در متن نشون می‌دادن، ولی داخل ربات این معامله
    # با یک signal_id کاملاً متفاوت (یک TC-XXXXXX تازه‌ساخته‌شده، که هیچ‌جا در کانال دیده
    # نمی‌شد چون پستِ دومِ محلی silent=True بود) ردیابی می‌شد - یعنی /signal ALT-000005 یا
    # /admin_close_trade ALT-000005 (دقیقاً همون چیزی که کاربر از روی پیامِ کانال می‌بینه و
    # تایپ می‌کنه) همیشه «not found» برمی‌گردوند. الان شناسه‌ی نمایش‌داده‌شده و شناسه‌ی
    # ردیابیِ داخلی همیشه دقیقاً یکی‌ان - هیچ ابهامی باقی نمی‌مونه.
    signal_id = None
    if preferred_signal_id:
        if candle_states is not None and _signal_id_in_use(preferred_signal_id, candle_states):
            logger.warning(f"preferred_signal_id {preferred_signal_id} already in use by an open trade - "
                            f"minting a fresh id instead to avoid a collision.")
        else:
            signal_id = preferred_signal_id
    if not signal_id:
        signal_id = next_signal_id()
    return {
        "signal_id": signal_id,
        # 🔴 وقتی preferred_signal_id واقعاً استفاده شد، همینجا هم جداگانه ذخیره می‌شه - صرفاً
        # برای شفافیت/دیباگ (که همیشه معلوم باشه این ID از منبع اومده، نه از next_signal_id
        # همین دیپلوی) - trade_history.json و همه‌ی مسیرهای تطبیق فقط از signal_id استفاده
        # می‌کنن.
        "source_signal_id": preferred_signal_id or None,
        "side": signal["side"], "entry": entry, "sl": sl, "r": r,
        "hit": {str(t): False for t in RR_TARGETS}, "trailing": False, "closed": False,
        "symbol": symbol, "tf_key": tf_key, "display": display,
        "opened_at_ms": int(time.time() * 1000),  # برای چک تکراری بین سطل‌های مختلف (پایین‌تر)
        # 🔴 تاریخچه‌ی کامل مسیر حرکتی این سیگنال - از همین لحظه‌ی باز شدن تا لحظه‌ی بسته شدن
        # نهایی (log_trade_result این آرایه رو عیناً در trade_history.json ذخیره می‌کنه، پس
        # بعد از بسته‌شدن هم برای همیشه قابل بازیابیه، نه فقط تا وقتی معامله باز است).
        "events": [_new_trade_event("opened", price=entry)],
    }


assert abs((W1 + W2 + W3 + W4 + W_RUNNER) - 1.0) < 1e-9, "جمع درصدها باید دقیقاً ۱۰۰٪ باشه (چک اضافه، خودِ shared_risk_config.py هم این assert رو داره)"


# ================== فیلتر «روند بازار» + «قدرت نسبی اتریوم/بیت‌کوین» ==================
#
# ⚠️ این یک فیلتر نرم است (طبق تصمیم شما): سیگنالی که برخلاف روند کلی بازار یا قدرت نسبی
# اتریوم/بیت‌کوین باشد حذف/بلاک نمی‌شود. قبلاً این حالت با برچسب هشدار «Counter-trend» در
# متن پیام سیگنال نشون داده می‌شد؛ طبق درخواست صریح، این برچسب از پیام‌ها حذف شد (سیگنال
# دقیقاً مثل بقیه‌ی سیگنال‌ها، بدون هیچ متن اضافه‌ای ارسال می‌شه). فقط روی سیگنال‌های اتریوم
# اعمال می‌شود، بیت‌کوین دست‌نخورده می‌ماند.
#
# ⚠️ صفر درخواست API اضافه: هم روند بازار (بیت‌کوین 1h+4h) و هم قدرت نسبی (نسبت ETH/BTC روی
# 1h+4h) فقط از داده‌ای محاسبه می‌شوند که همین الان هم در candle_states موجود است (چون 1h و
# 4h همین الان هم جزو TIMEFRAMES خودِ موتور هستند) - هیچ fetch جدیدی از Twelve Data لازم نیست.

MARKET_REGIME_TFS = ("1h", "4h")  # طبق تصمیم شما: ترکیب سخت‌گیرانه‌ی دو تایم‌فریم، نه فقط یکی


def _get_symbol_trend(candle_states: Dict[str, Any], symbol: str, tf_key: str) -> Optional[str]:
    """روند یک نماد روی یک تایم‌فریم مشخص - فقط از EMA7/EMA25 همون state ذخیره‌شده،
    بدون هیچ محاسبه یا درخواست اضافه."""
    st = candle_states.get(f"{symbol}|{tf_key}")
    if not st or st.get("ema7") is None or st.get("ema25") is None:
        return None
    if st["ema7"] > st["ema25"]:
        return "up"
    if st["ema7"] < st["ema25"]:
        return "down"
    return "flat"


def get_market_regime(candle_states: Dict[str, Any]) -> str:
    """روند کلی بازار: بیت‌کوین باید هم روی 1h و هم روی 4h هم‌جهت باشه (bullish/bearish)،
    وگرنه «mixed» (نه صعودی قطعی، نه نزولی قطعی)."""
    trends = [_get_symbol_trend(candle_states, "BTC/USD", tf) for tf in MARKET_REGIME_TFS]
    if all(t == "up" for t in trends):
        return "bullish"
    if all(t == "down" for t in trends):
        return "bearish"
    return "mixed"


def _ema_of_series(values: List[float], length: int) -> Optional[float]:
    if len(values) < length:
        return None
    alpha = 2.0 / (length + 1)
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1 - alpha) * e
    return e


def _ratio_trend(candle_states: Dict[str, Any], tf_key: str) -> Optional[str]:
    """روند نسبت close(ETH)/close(BTC) روی یک تایم‌فریم مشخص - از همون hist ذخیره‌شده‌ی
    هر دو نماد (بدون هیچ fetch جدید). کندل‌ها با dt_ms یکسان جفت می‌شوند چون هر دو نماد
    روی یک interval گرفته می‌شوند."""
    btc_st = candle_states.get(f"BTC/USD|{tf_key}")
    eth_st = candle_states.get(f"ETH/USD|{tf_key}")
    if not btc_st or not eth_st:
        return None
    btc_by_time = {b["dt_ms"]: b["c"] for b in btc_st["hist"]}
    ratio_series = [e["c"] / btc_by_time[e["dt_ms"]] for e in eth_st["hist"] if e["dt_ms"] in btc_by_time]
    ema7 = _ema_of_series(ratio_series, 7)
    ema25 = _ema_of_series(ratio_series, 25)
    if ema7 is None or ema25 is None:
        return None
    if ema7 > ema25:
        return "up"
    if ema7 < ema25:
        return "down"
    return "flat"


def get_eth_relative_strength(candle_states: Dict[str, Any]) -> str:
    """قدرت نسبی اتریوم به بیت‌کوین: ترکیب 1h+4h روی نسبت ETH/BTC، هم‌راستا با منطق
    روند کلی بازار بالا."""
    ratios = [_ratio_trend(candle_states, tf) for tf in MARKET_REGIME_TFS]
    if all(r == "up" for r in ratios):
        return "outperforming"
    if all(r == "down" for r in ratios):
        return "underperforming"
    return "mixed"


def eth_signal_is_aligned(candle_states: Dict[str, Any], side: str) -> bool:
    """فقط برای اتریوم: آیا این سیگنال هم‌جهت با روند کلی بازار (بیت‌کوین 1h+4h) و
    هم‌جهت با قدرت نسبی اتریوم/بیت‌کوین است؟ فیلتر نرم است - جواب False فقط باعث
    برچسب هشدار در پیام می‌شود، سیگنال همچنان بلافاصله ارسال می‌شود."""
    regime = get_market_regime(candle_states)
    strength = get_eth_relative_strength(candle_states)
    if side == "BUY":
        return regime == "bullish" and strength == "outperforming"
    return regime == "bearish" and strength == "underperforming"


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

    # 🔴 رفعِ محدودیتِ واقعیِ «مسیر دقیق حرکت سیگنال، بعد از بسته‌شدن، دیگه قابل بازسازی
    # نیست»: قبلاً events این تابع فقط transient بود (فقط برای فرستادن پیام تلگرام استفاده
    # می‌شد و هیچ‌جا دائمی ذخیره نمی‌شد) - روی خودِ trade فقط یک بولینِ hit["1"..."6"]=True/
    # False می‌موند، بدون هیچ زمان/قیمتِ دقیقی که *کِی* و *روی چه قیمتی* هر تارگت خورده. این
    # تابع کمکی هر رویداد رو - همراه با timestamp دقیق لحظه‌ی رصدش - در trade["events"] (که
    # open_new_trade با رویداد "opened" مقداردهی اولیه کرده) هم ثبت می‌کنه، پس log_trade_result
    # می‌تونه کل این آرایه رو عیناً در trade_history.json ذخیره کنه - یعنی حتی سال‌ها بعد هم
    # می‌شه دقیقاً گفت این سیگنال چه ساعتی باز شد، چه ساعتی به کدام تارگت رسید (با چه قیمتی)،
    # و چه ساعتی/چطور بسته شد.
    def _persist(evs):
        history = trade.setdefault("events", [])
        for ev in evs:
            rec = {"type": ev["type"], "ts": datetime.now(timezone.utc).isoformat()}
            if ev.get("price") is not None:
                rec["price"] = ev["price"]
            if "level" in ev:
                rec["level_r"] = ev["level"]
            history.append(rec)
        return evs

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
            return _persist(events)
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
            return _persist(events)
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

    return _persist(events)


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


def _fmt_price(value: float) -> str:
    """قیمت رو با تعداد رقم اعشار مناسبِ مقیاسش نشون می‌ده. برای بیت‌کوین/اتریوم و امثال
    اون‌ها همون ۲ رقم اعشار همیشگی کافیه، ولی حالا که سیگنال‌های دستی/فوروارد‌شده می‌تونن
    روی هر نمادی باشن (نه فقط BTC/ETH) - مثلاً یک آلت‌کوین خیلی کم‌قیمت مثل SHIB که قیمتش
    چیزی مثل 0.00000493 است - فرمت ثابت ۲ رقمی همه چیز رو 0.00 نشون می‌داد. اینجا رقم
    اعشار طوری تنظیم می‌شه که همیشه حداقل ۳ رقم معنادار (significant figure) دیده بشه."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v == 0:
        return "0.00"
    av = abs(v)
    if av >= 1:
        return f"{v:,.2f}"
    decimals = min(12, max(2, -int(math.floor(math.log10(av))) + 3))
    return f"{v:.{decimals}f}"


def _dt(display: str, tf_label: str) -> str:
    """رشته‌ی نمایشی «نماد + تایم‌فریم» رو می‌سازه. برای سیگنال‌های دستی/فوروارد‌شده که
    تایم‌فریم مشخصی ندارن (tf_label خالیه)، فقط خودِ نماد واقعی (BTC یا ETH) نشون داده
    می‌شه - نه یک برچسب عمومی مثل «Manual» که باعث می‌شد به‌نظر برسه سیگنال روی یک دارایی
    جدا به‌اسم «BTC Manual» باز شده، نه خودِ ارز واقعی‌ای که فوروارد/ثبت شده بود."""
    return f"{display} {tf_label}".strip() if tf_label else display


def _id_hashtag(sid: str) -> str:
    """نسخه‌ی هشتگِ یک signal_id. تلگرام در هشتگ فقط حروف/عدد/underscore رو می‌پذیره (نه
    خط‌تیره یا اسلش)، پس «TC-004821» به «#TC_004821» تبدیل می‌شه. طبق درخواست صریح: این
    هشتگ زیر هر پیام (ورود + هر تارگت/استاپ/بسته‌شدن) عیناً تکرار می‌شه - یعنی با تپ‌کردن
    روی این هشتگ در اپ تلگرام، خودِ تلگرام تمام پیام‌های همین یک سیگنال رو در یک نتیجه‌ی
    جستجوی داخل کانال جمع می‌کنه. این «مسیر» یک منبعِ حقیقتِ دومِ کاملاً مستقل از
    trade_history.json/candle_state.json است - حتی اگه یک رویداد به هر دلیلی هیچ‌وقت در
    فایل‌های داخلی ربات اعمال نشه، خودِ کانال (که تلگرام همیشه نگهش می‌داره) کامل و
    قابل‌بازسازیه، پس هیچ سیگنالی واقعاً "گم" نمی‌شه، فقط ممکنه دستی نیاز به تطبیق داشته باشه."""
    return re.sub(r"[^A-Za-z0-9_]", "_", sid)


def _id_line(trade: Dict[str, Any]) -> str:
    """خط هشتگِ شناسه‌ی سیگنال («#TC_004821») - در پیام ورود و هر پیام خروج/تارگت/استاپ نشون
    داده می‌شه، تا هرکسی که کانال رو دنبال می‌کنه (یا ادمین، برای /admin_close_trade) بتونه
    دقیقاً به همون یک سیگنالِ مشخص اشاره کنه - حتی وقتی چند سیگنالِ پی‌درپی روی یک
    نماد/تایم‌فریم جابه‌جا می‌شن. با تپ‌کردن روی این هشتگ در اپ تلگرام، تمام پیام‌های همین یک
    سیگنال در یک نتیجه‌ی جستجوی داخل کانال جمع می‌شن. اگه به هر دلیلی (رکورد خیلی قدیمی، قبل
    از این فیچر) شناسه موجود نباشه، رشته‌ی خالی برمی‌گردونه - هیچ خط اضافه‌ای به پیام اضافه
    نمی‌شه.

    🔴 طبق درخواست صریح، دو تغییر نسبت به نسخه‌ی قبلی: (۱) دیگه خودِ متنِ خام شناسه («🆔
    TC-004821») نمایش داده نمی‌شه - چون کاربردی نداشت و فقط هشتگ («#TC_004821») کافیه؛
    subscription_bot.py هم الان مستقیماً از روی همین هشتگ (نه یک خط 🆔 جدا) شناسه رو استخراج
    می‌کنه (نگاه کنید SOURCE_SIGNAL_ID_RE). (۲) این خط دیگه بالای پیام (زیر خط تیتر) نیست -
    به‌جاش زیرِ خط تاریخ/زمان (_now_str) و درست بالای خط هشدار ریسک منتقل شده - در انتهای
    هر پیام، نه ابتدای اون؛ نگاه کنید محل فراخوانیِ این تابع در هر format_*_message.

    ⚠️ نکته‌ی فنی: یک \n در ابتدا (نه انتها) برمی‌گردونه - چون RISK_LINE خودش از قبل با
    «\n\n» شروع می‌شه (فاصله‌ی یک خط خالی رو خودش تأمین می‌کنه). اگه این تابع هم یک \n در
    انتهاش می‌ذاشت، جمع دو تا \n پشت‌سرهم یک خط خالیِ اضافه در پیام نهایی ایجاد می‌کرد."""
    sid = trade.get("signal_id")
    return f"\n#{_id_hashtag(sid)}" if sid else ""


def format_entry_message(display: str, tf_label: str, signal: Dict[str, Any], trade: Dict[str, Any]) -> str:
    arrow = "🟢 LONG" if signal["side"] == "BUY" else "🔴 SHORT"
    sign = 1 if signal["side"] == "BUY" else -1
    targets_lines = "\n".join([f"🎯 {TARGET_LABELS[t]}: {_fmt_price(trade['entry'] + sign * t * trade['r'])}" for t in RR_TARGETS])
    # ⚠️ طبق درخواست صریح، برچسب هشدار «Counter-trend» دیگه به پیام سیگنال اضافه نمی‌شه.
    # eth_signal_is_aligned پایین‌تر (process_symbol_timeframe) همچنان محاسبه و روی خودِ
    # سیگنال ذخیره می‌شه (بدون هیچ درخواست API اضافه - فقط از candle_states موجود) چون فیلتر
    # نرم بود و سیگنال رو هیچ‌وقت حذف/بلاک نمی‌کرد؛ فقط دیگه در متن پیام نمایش داده نمی‌شه.
    return (
        f"{arrow} — {_dt(display, tf_label)}\n"
        f"Entry: <b>{_fmt_price(signal['price'])}</b>\n"
        f"❌ Stop: <b>{_fmt_price(trade['sl'])}</b>\n"
        f"{targets_lines}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
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
        f"Entry {_fmt_price(trade['entry'])}  ·  Now {_fmt_price(event['price'])}\n"
        f"{action}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
        f"{RISK_LINE}"
    )


def format_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"❌ STOP HIT — {_dt(display, tf_label)}\n\n"
        f"Stop-loss hit on this {direction} trade before Target 1 — full position closed.\n"
        f"Entry was {_fmt_price(trade['entry'])}  ·  Stop {_fmt_price(event['price'])}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
        f"{RISK_LINE}"
    )


def format_breakeven_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"⚪ BREAKEVEN — {_dt(display, tf_label)}\n\n"
        f"Price returned to entry on this {direction} trade — the remaining 80% closed with no "
        f"loss (20% was already banked at Target 1).\n"
        f"Entry {_fmt_price(trade['entry'])}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
        f"{RISK_LINE}"
    )


def format_sl_after_t2_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🔒 STOP AFTER TARGET 2 — {_dt(display, tf_label)}\n\n"
        f"Price came back to the Target 1 level on this {direction} trade — the remaining 50% "
        f"closed there, locking in profit (20% @ Target 1, 30% @ Target 2 already banked).\n"
        f"Entry {_fmt_price(trade['entry'])}  ·  Closed at {_fmt_price(event['price'])}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
        f"{RISK_LINE}"
    )


def format_sl_after_t3_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🔒 STOP AFTER TARGET 3 — {_dt(display, tf_label)}\n\n"
        f"Price came back to the Target 2 level on this {direction} trade — the remaining 35% "
        f"closed there, locking in more profit (20% @ T1, 30% @ T2, 15% @ T3 already banked).\n"
        f"Entry {_fmt_price(trade['entry'])}  ·  Closed at {_fmt_price(event['price'])}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
        f"{RISK_LINE}"
    )


def format_runner_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"🏁 RUNNER CLOSED — {_dt(display, tf_label)}\n\n"
        f"All 4 targets were already banked on this {direction} trade (75% of the position) — "
        f"the final 25% runner portion just closed on its trailing stop (1.5R behind the peak). "
        f"Trade fully complete.\n"
        f"Entry {_fmt_price(trade['entry'])}  ·  Runner closed at {_fmt_price(event['price'])}"
        f"{_result_line(trade)}\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
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
        f"Entry {_fmt_price(trade['entry'])}  ·  Closed at {_fmt_price(exit_price)}\n"
        f"Result: ~{result_r:+.2f}R ({result_word})\n\n"
        f"👉 A new signal for this reversal follows right after this message.\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
        f"{RISK_LINE}"
    )


def _tf_label_for_trade(t: Dict[str, Any]) -> Optional[str]:
    """برچسبِ نمایشیِ تایم‌فریم («15M») رو از روی یک آبجکتِ trade (چه هنوز باز، چه از
    trade_history.json) برمی‌گردونه - برای سیگنال‌های خودکار از tf_key، برای دستی/فوروارد‌شده
    (که tf_key همیشه ثابت \"manual\" است) از logical_tf. اگه هیچ‌کدوم شناخته‌شده نبود، None
    برمی‌گردونه - _dt/format_admin_close_message با None هم درست کار می‌کنن (فقط خودِ نماد
    بدون برچسب تایم‌فریم نشون داده می‌شه)."""
    tf_key = t.get("tf_key") or t.get("tf")
    if tf_key and tf_key in TIMEFRAMES:
        return TIMEFRAMES[tf_key]["label"]
    logical = t.get("logical_tf")
    if logical and logical in TIMEFRAMES:
        return TIMEFRAMES[logical]["label"]
    return None


def format_admin_close_message(display: str, tf_label: str, trade: Dict[str, Any], final_r: float,
                                is_edit: bool = False) -> str:
    """پیام «بسته‌شدن» برای معامله‌ای که ادمین دستی با /admin_close_trade می‌بنده - طبق
    درخواست صریح، دقیقاً هم‌شکل با بقیه‌ی پیام‌های بسته‌شدن (همون هدر ⚠️ TRADE CLOSED، همون
    خط نتیجه، همون تاریخ/هشتگ/فوتر) تا از دید کسی که کانال رو دنبال می‌کنه، هیچ فرقی با یک
    بسته‌شدنِ خودکار (استاپ/تارگت/سیگنال مخالف) نداشته باشه - فقط دلیلش رو (اقدامِ دستیِ
    ادمین، نه قیمتِ زنده) در متن بدنه شفاف می‌گه.

    is_edit فقط برای انتخاب فعل («closed» در بار اول در برابر «corrected» وقتی همین تابع -
    به‌ندرت - برای یک اصلاح که تصمیم گرفتیم به کانال هم اطلاع بدیم استفاده بشه) - در طراحی
    فعلی (پایین‌تر در process_admin_close_requests) این تابع فقط برای بار اولِ واقعیِ
    بسته‌شدن صدا زده می‌شه؛ ویرایشِ صرفِ عددیِ یک سیگنالِ از قبل بسته‌شده، پیامِ عمومیِ جدیدی
    به کانال نمی‌فرسته (چون یک پیامِ بسته‌شدن قبلاً همون یک‌بار درست ارسال شده؛ ویرایش فقط
    آماره‌های داخلی/گزارش‌ها رو اصلاح می‌کنه، نه چیزی که به‌طور عمومی دوباره اعلام بشه)."""
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    word = "profit" if final_r > 0 else ("breakeven" if final_r == 0 else "loss")
    verb = "corrected" if is_edit else "closed"
    return (
        f"⚠️ TRADE CLOSED — {_dt(display, tf_label)}\n\n"
        f"This {direction} trade was manually {verb} by the admin.\n"
        f"Entry {_fmt_price(trade['entry'])}\n"
        f"Result: <b>{final_r:+.2f}R</b> ({word})\n\n"
        f"{_now_str()}"
        f"{_id_line(trade)}"
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
                if symbol == "ETH/USD":
                    sig["aligned"] = eth_signal_is_aligned(candle_states, sig["side"])
                dup_key, dup_trade = _find_cross_source_duplicate(candle_states, symbol, sig["side"], sig["price"], "automatic")
                if dup_trade:
                    logger.info(f"⏭️ Skipping automatic {symbol} [{tf_key}] {sig['side']} — duplicate of "
                                f"manual/forwarded trade '{dup_key}' opened recently at a similar price")
                    continue
                # ⚠️ علاوه بر معامله‌ی خودِ همین سطل خودکار (که پایین‌تر با prev_trade مدیریت
                # می‌شه)، سطل دستی/فوروارد رو هم برای یک معامله‌ی باز هم‌نماد/جهت‌مخالف روی
                # همین تایم‌فریمِ واقعی چک می‌کنیم - وگرنه (علت گزارش «سیگنال فروش ۱ دقیقه‌ای
                # به دو تارگت رسید، بعد بدون توضیح خرید اومد») یک معامله‌ی دستی/فوروارد باز
                # می‌مونه در حالی که موتور خودکار همین لحظه دقیقاً همون نماد/تایم‌فریم رو
                # برعکس تشخیص داده - بدون این چک، هیچ‌وقت رسماً بسته و اعلام نمی‌شد.
                opp_key, opp_trade = _find_open_opposite_manual_trade(candle_states, symbol, sig["side"], tf_key)
                if opp_trade:
                    opp_display = opp_trade.get("display") or display
                    opp_hist = candle_states.get(opp_key, {}).get("hist", [])
                    opp_tf_cfg = opp_trade.get("manual_tf_cfg") or MANUAL_TF_CFG
                    _force_close_trade(opp_display, "manual", opp_tf_cfg, opp_trade, k, opp_hist, symbol)
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
            if symbol == "ETH/USD":
                sig["aligned"] = eth_signal_is_aligned(candle_states, sig["side"])
            dup_key, dup_trade = _find_cross_source_duplicate(candle_states, symbol, sig["side"], sig["price"], "automatic")
            if dup_trade:
                logger.info(f"⏭️ Skipping automatic {symbol} [{tf_key}] {sig['side']} — duplicate of "
                            f"manual/forwarded trade '{dup_key}' opened recently at a similar price")
                candle_states[state_key] = state
                continue
            # اگه معامله‌ی قبلی هنوز باز بود (چون هنوز به استاپ/تارگت نخورده)، قبل از باز کردن
            # معامله‌ی جدید مخالف‌جهت، حتماً باید اول رسماً بسته بشه - وگرنه بی‌سروصدا فراموش
            # می‌شد و کاربر گیج می‌موند که چرا یهو سیگنال مخالف اومده بدون توضیح.
            prev_trade = state.get("open_trade")
            if prev_trade and not prev_trade.get("closed"):
                _force_close_trade(display, tf_key, tf_cfg, prev_trade, k, state["hist"], symbol)

            # ⚠️ همون چک بالا (بوت‌استرپ)، اینجا هم لازمه: سطل دستی/فوروارد رو برای معامله‌ی
            # باز هم‌نماد/جهت‌مخالف روی همین تایم‌فریمِ واقعی چک می‌کنیم - نه فقط سطل خودکار
            # خودمون (prev_trade بالا).
            opp_key, opp_trade = _find_open_opposite_manual_trade(candle_states, symbol, sig["side"], tf_key)
            if opp_trade:
                opp_display = opp_trade.get("display") or display
                opp_hist = candle_states.get(opp_key, {}).get("hist", [])
                opp_tf_cfg = opp_trade.get("manual_tf_cfg") or MANUAL_TF_CFG
                _force_close_trade(opp_display, "manual", opp_tf_cfg, opp_trade, k, opp_hist, symbol)

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
    trade.setdefault("events", []).append(_new_trade_event("opposite_signal", price=exit_price))
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
GIT_COMMIT_EVERY_SECONDS = 45      # هر ۴۵ ثانیه تغییرات commit می‌شود (قبلاً ۲ دقیقه بود؛ تنگ‌تر
                                    # شد چون این تنها تاخیریه که بین «سیگنال/نتیجه به کانال ارسال
                                    # شد» و «توی /results ربات اشتراک هم دیده بشه» فاصله می‌ندازه -
                                    # چون این دو اسکریپت فقط از طریق گیت با هم حرف می‌زنن. کامیت
                                    # خودش هزینه‌ی API نداره، فقط عملیات گیته - پس تنگ‌کردنش ارزون است.


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


def _find_open_opposite_trade(candle_states: Dict[str, Any], symbol: str, side: str, logical_tf: Optional[str]):
    """دنبال یک معامله‌ی بازِ هم‌نماد و هم‌جهت‌مخالف می‌گرده - برای بستن رسمی قبل از باز کردن
    سیگنال معکوس. استفاده‌ی اصلی: وقتی یک سیگنال دستی/فوروارد‌شده‌ی جدید می‌رسه.

    ⚠️ باگ رفع‌شده (علت گزارش «سیگنال فروش ۱ دقیقه‌ای به دو تارگت رسید، بعد بدون هیچ توضیحی
    سیگنال خرید همون تایم‌فریم اومد - کاربران گیج شدن»): قبلاً اینجا همیشه با tf_key ثابتِ
    "manual" صدا زده می‌شد - یعنی این تابع فقط داخل «سطل دستی» دنبال جهت مخالف می‌گشت، حتی
    وقتی خودِ پیام فوروارد‌شده صریحاً تایم‌فریمش رو اعلام کرده بود (مثلاً «SHORT — BTC 1M»).
    نتیجه: اگه معامله‌ی مخالف قبلی از مسیر **خودکار** (candle_engine خودش، tf_key="1m") باز
    شده بود، این تابع اصلاً پیدایش نمی‌کرد - نه _force_close_trade صدا زده می‌شد، نه پیام
    «⚠️ TRADE CLOSED» ای می‌رفت - معامله‌ی خودکار بی‌سروصدا باز می‌موند در حالی که یک سیگنال
    کاملاً معکوس (از منبع دیگه) توی کانال ظاهر می‌شد. الان تابع «تایم‌فریم منطقی» واقعی رو
    می‌گیره (matched_tf_key که process_manual_signals از روی برچسب پیام فوروارد‌شده تشخیص
    می‌ده - نه رشته‌ی ثابت "manual") و هم سطل خودکارِ همون تایم‌فریم دقیق (state_key ==
    f"{symbol}|{logical_tf}") و هم سایر معاملات دستی/فوروارد با همون logical_tf را چک می‌کنه.
    اگه logical_tf ناشناخته باشه (پیام فوروارد‌شده هیچ برچسب تایم‌فریمی نداشت)، محافظه‌کارانه
    فقط بین سایر معاملات دستیِ بدون‌برچسب می‌گرده - هیچ‌وقت یک معامله‌ی خودکار یا یک معامله‌ی
    دستیِ تایم‌فریمِ شناخته‌شده رو با حدس اشتباه نمی‌بندد. سیگنال‌های خودکار تایم‌فریم‌های
    واقعاً متفاوت (مثلاً BTC 1H در مقابل BTC 4H) هنوز دست‌نخورده می‌مونن - این‌ها استراتژی‌های
    مستقل و معتبرن، نه تناقض."""
    opposite_side = "SELL" if side == "BUY" else "BUY"
    for key, s in candle_states.items():
        t = s.get("open_trade")
        if not (t and not t.get("closed") and t.get("symbol") == symbol and t.get("side") == opposite_side):
            continue
        is_manual_bucket = key.startswith("MANUAL|")
        if logical_tf is not None:
            if is_manual_bucket:
                if t.get("logical_tf") == logical_tf:
                    return key, t
            else:
                if key == f"{symbol}|{logical_tf}":
                    return key, t
        else:
            if is_manual_bucket and t.get("logical_tf") is None:
                return key, t
    return None, None


def _find_open_opposite_manual_trade(candle_states: Dict[str, Any], symbol: str, side: str, logical_tf: str):
    """مکمل تابع بالا، برای مسیر خودکار (process_symbol_timeframe): وقتی خودِ موتور خودکار
    سیگنالی روی تایم‌فریم tf_key تشخیص می‌ده، این تابع فقط سطل دستی/فوروارد رو برای یک
    معامله‌ی باز هم‌نماد/جهت‌مخالف با همون logical_tf می‌گرده (سطل خودکارِ خودش رو مسیر
    process_symbol_timeframe مستقیماً و جدا مدیریت می‌کنه، پس اینجا لازم نیست دوباره چک بشه -
    وگرنه همون معامله دوبار force-close می‌شد)."""
    opposite_side = "SELL" if side == "BUY" else "BUY"
    for key, s in candle_states.items():
        if not key.startswith("MANUAL|"):
            continue
        t = s.get("open_trade")
        if (t and not t.get("closed") and t.get("symbol") == symbol
                and t.get("side") == opposite_side and t.get("logical_tf") == logical_tf):
            return key, t
    return None, None


# ⚠️ فاصله‌ی زمانی و تلورانس قیمتی برای تشخیص «سیگنال تکراریِ بین‌منبعی» (پایین‌تر). دلیل
# وجودش: گاهی همون رویداد بازار هم توسط موتور خودکار (process_symbol_timeframe) تشخیص داده
# می‌شه و هم ادمین همون سیگنال رو از یک منبع بیرونی فوروارد می‌کنه (process_manual_signals) -
# چون دو مسیر کاملاً مستقل‌اند (سطل‌های جدا در candle_states)، بدون این چک هر دو پست می‌شن و
# دقیقاً یک سیگنال دوبار توی کانال می‌آد + دوبار در trade_history/آمار حساب می‌شه.
# این عمداً محدود به «دستی/فوروارد در مقابل خودکار» است - بین دو تایم‌فریم خودکار مختلف
# (مثلاً BTC 1H و BTC 4H) اعمال نمی‌شه، چون آن‌ها استراتژی‌های مستقل و معتبرن (طبق طراحی بالا).
DUPLICATE_SIGNAL_WINDOW_SECONDS = 90 * 60   # اگه توی این بازه (۹۰ دقیقه) دو سیگنال هم‌جهت/هم‌نماد
                                             # از دو منبع مختلف اومدن، تکراری در نظر گرفته می‌شن
DUPLICATE_SIGNAL_PRICE_TOLERANCE = 0.006    # و فاصله‌ی Entry‌شون هم حداکثر ۰.۶٪ باشه (برای اینکه
                                             # دو ستاپ واقعاً متفاوت که تصادفاً هم‌زمان شدن رو
                                             # اشتباهی تکراری تشخیص ندیم)


def _find_cross_source_duplicate(candle_states: Dict[str, Any], symbol: str, side: str, entry: float,
                                  source: str):
    """دنبال یک معامله‌ی باز هم‌نماد/هم‌جهت با Entry نزدیک، در بازه‌ی زمانی اخیر، از «منبع
    دیگر» می‌گرده. source="manual" یعنی داریم یک سیگنال دستی/فوروارد جدید باز می‌کنیم - پس
    فقط بین معاملات خودکار (و سایر دستی‌های دیگر) دنبال تطابق می‌گردیم. source="automatic"
    یعنی برعکس - موتور خودکار داره سیگنال جدید تشخیص می‌ده، پس فقط بین معاملات دستی/فوروارد
    باز دنبال تطابق می‌گردیم (بین دو تایم‌فریم خودکار عمداً چک نمی‌کنیم - دلیلش بالاست)."""
    now_ms = int(time.time() * 1000)
    for key, s in candle_states.items():
        t = s.get("open_trade")
        if not t or t.get("closed"):
            continue
        if t.get("symbol") != symbol or t.get("side") != side:
            continue
        is_manual_bucket = key.startswith("MANUAL|")
        if source == "automatic" and not is_manual_bucket:
            continue  # فقط دستی/فوروارد رو در مقابل خودکار چک کن
        if source == "manual" and is_manual_bucket:
            # بین دو سیگنال دستی/فوروارد هم چک می‌کنیم (مثلاً یک منبع دوبار فوروارد شده)
            pass
        opened_at = t.get("opened_at_ms")
        if opened_at is not None and now_ms - opened_at > DUPLICATE_SIGNAL_WINDOW_SECONDS * 1000:
            continue
        t_entry = t.get("entry")
        if t_entry and abs(entry - t_entry) / t_entry <= DUPLICATE_SIGNAL_PRICE_TOLERANCE:
            return key, t
    return None, None


def process_manual_signals():
    """سیگنال‌هایی که ادمین با /admin_manual_signal یا مستقیم توی کانال (تگ #signal یا
    فوروارد خودکار‌شناسایی‌شده) ثبت کرده رو می‌خونه، دقیقاً با همون فرمت سیگنال‌های خودکار
    (استاپ + ۴ تارگت) در کانال پست می‌کنه، و به همون سیستم رهگیری (WebSocket برای
    بیت‌کوین/اتریوم، REST به‌عنوان پشتیبان) اضافه می‌کنه تا نتیجه‌ش هم داخل /results حساب بشه.

    قبل از باز کردن، اگه همین نماد یک سیگنال دستی/فوروارد مخالف‌جهت هنوز باز داشته باشه
    (مثلاً یک BUY دستی باز روی BTC و حالا یک SELL دستی جدید می‌رسه)، اول اون رو رسماً می‌بندیم
    - دقیقاً مثل رفتار سیگنال‌های خودکار وقتی جهت عوض می‌شه.

    ⚠️ چک اعتبار قیمت (اضافه‌شده بعد از یک سیگنال DOGE واقعی که Entry‌اش از قیمت لحظه‌ای خیلی
    فاصله داشت و اصلاً TF هم نداشت): قبل از پست‌کردن، قیمت زنده‌ی همین لحظه رو می‌گیریم و با
    Entry/Stop مقایسه می‌کنیم. اگه قیمت الان از قبل پایین‌تر از حد ضرر (برای BUY) یا بالاتر
    از حد ضرر (برای SELL) باشه - یعنی خودِ ستاپ قبل از اینکه اصلاً پست بشه باطل شده - یا از
    قبل از تارگت ۴ (6R) هم رد شده باشه، این سیگنال اصلاً پست نمی‌شه و به‌جاش به ادمین اطلاع
    داده می‌شه. علتش رایجه: تاخیر بین لحظه‌ای که منبعِ فوروارد سیگنال رو صادر کرده و لحظه‌ای
    که خودِ ادمین فوروارد می‌کنه/ربات پردازشش می‌کنه، یا یک اسپایک لحظه‌ای/نویز قیمتی در همون
    لحظه که دیگه واقعی نیست."""
    manual_signals = read_json_resilient(
        MANUAL_SIGNALS_FILE, None, label="manual_signals.json",
        on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    if manual_signals is None:
        return

    pending = [m for m in manual_signals if m.get("status") == "pending"]
    if not pending:
        return

    state = load_state()
    candle_states = state.setdefault("candle_signals", {})

    for m in pending:
        try:
            symbol, side, entry, sl = m["symbol"], m["side"], float(m["entry"]), float(m["sl"])
            display = WATCHLIST_SYMBOLS.get(symbol, symbol.split("/")[0])
            signal_id = m["id"]
            state_key = f"MANUAL|{signal_id}"
            r = abs(entry - sl)

            # --- چک اعتبار: قیمت زنده رو با Entry/Stop مقایسه می‌کنیم قبل از پست‌کردن ---
            live_price = None
            try:
                live_price = fetch_live_price(symbol)
            except Exception as e:
                logger.warning(f"Could not fetch live price to validate manual signal {symbol}: {e}")

            if live_price and r > 0:
                max_target_dist = max(RR_TARGETS) * r
                if side == "BUY":
                    already_stopped = live_price <= sl
                    already_maxed = live_price >= entry + max_target_dist
                else:
                    already_stopped = live_price >= sl
                    already_maxed = live_price <= entry - max_target_dist
                if already_stopped or already_maxed:
                    reason = (
                        f"live price ({_fmt_price(live_price)}) is already past the stop-loss ({_fmt_price(sl)})"
                        if already_stopped else
                        f"live price ({_fmt_price(live_price)}) has already blown past the final target (6R)"
                    )
                    m["status"] = "rejected_stale"
                    m["reject_reason"] = reason
                    logger.warning(f"Rejected stale manual signal {symbol} {side} entry={_fmt_price(entry)} "
                                    f"sl={_fmt_price(sl)}: {reason}")
                    notify_admin(
                        f"manual_signal_rejected:{symbol}:{side}",
                        Exception(f"{symbol} {side} entry={_fmt_price(entry)} sl={_fmt_price(sl)} was NOT posted — "
                                  f"{reason}. This usually means too much time passed between the source posting it "
                                  f"and it reaching the bot, or a brief price spike/bad tick. Re-send with a current "
                                  f"entry if you still want this trade tracked."),
                    )
                    continue

            # --- تشخیص تایم‌فریم اعلام‌شده در خود پیام/دستور (اجباری - طبق درخواست صریح: هیچ
            # سیگنالی، حتی سیگنال دستی، نباید بدون تایم‌فریم مشخص وجود داشته باشه) ---
            # subscription_bot.py دیگه هیچ سیگنالی رو بدون یک tf_label معتبر (عضو
            # VALID_TIMEFRAME_LABELS) صف نمی‌کنه - این چک اینجا صرفاً یک لایه‌ی دفاعیِ دوم
            # است (برای رکوردهای قدیمی‌تر در manual_signals.json که ممکنه از قبل این فیلد
            # رو نداشته باشن). قبلاً وقتی TF نامعلوم بود، سیگنال با برچسب عمومی «Manual»
            # (بدون تایم‌فریم واقعی) پست/رصد می‌شد - همون چیزی که در گزارش روزانه به‌شکل یک
            # ردیف گمراه‌کننده‌ی «Manual: N trade» زیر «By timeframe» دیده می‌شد. الان به‌جای
            # آن fallback، اگه تایم‌فریم تطبیق نخوره، سیگنال اصلاً پست/باز نمی‌شه و به ادمین
            # اطلاع داده می‌شه تا با تایم‌فریم درست دوباره ارسال بشه.
            tf_label_raw = (m.get("tf_label") or "").strip().upper()
            matched_tf_key = next((k for k, cfg in TIMEFRAMES.items() if cfg["label"] == tf_label_raw), None)
            if not matched_tf_key:
                m["status"] = "rejected_no_tf"
                m["reject_reason"] = f"missing/unrecognized timeframe label ({tf_label_raw or '(none)'})"
                logger.warning(f"Rejected manual signal {symbol} {side} entry={_fmt_price(entry)} — "
                                f"no valid timeframe label ({tf_label_raw or '(none)'}).")
                notify_admin(
                    f"manual_signal_no_tf:{symbol}:{side}",
                    Exception(f"{symbol} {side} entry={_fmt_price(entry)} sl={_fmt_price(sl)} was NOT posted — "
                              f"no valid timeframe was attached (got: {tf_label_raw or 'none'}). Every signal must "
                              f"declare its timeframe (one of: {', '.join(sorted(VALID_TIMEFRAME_LABELS))}). "
                              f"Re-send with the timeframe included."),
                )
                continue
            src_cfg = TIMEFRAMES[matched_tf_key]
            manual_tf_cfg = {"td_interval": src_cfg["td_interval"], "bar_seconds": src_cfg["bar_seconds"], "label": src_cfg["label"]}

            opp_key, opp_trade = _find_open_opposite_trade(candle_states, symbol, side, matched_tf_key)
            if opp_trade:
                opp_display = opp_trade.get("display") or display
                opp_hist = candle_states.get(opp_key, {}).get("hist", [])
                fake_candle = {"o": entry, "h": entry, "l": entry, "c": entry}
                opp_tf_cfg = opp_trade.get("manual_tf_cfg") or MANUAL_TF_CFG
                _force_close_trade(opp_display, "manual", opp_tf_cfg, opp_trade, fake_candle, opp_hist, symbol)

            # --- چک تکراری بین‌منبعی: همین سیگنال رو موتور خودکار (یا یک فوروارد قبلی) همین
            # الان یا چند دقیقه پیش با قیمت نزدیک باز نکرده باشه؛ وگرنه یک سیگنال کاملاً یکسان
            # دوبار توی کانال می‌آد و دوبار در trade_history/آمار حساب می‌شه ---
            dup_key, dup_trade = _find_cross_source_duplicate(candle_states, symbol, side, entry, "manual")
            if dup_trade:
                m["status"] = "skipped_duplicate"
                m["reject_reason"] = f"duplicate of already-open trade '{dup_key}' (same symbol/side, similar entry, opened recently)"
                logger.info(f"⏭️ Skipping manual/forwarded signal {symbol} {side} entry={_fmt_price(entry)} — "
                            f"{m['reject_reason']}")
                notify_admin(
                    f"manual_signal_duplicate:{symbol}:{side}",
                    Exception(f"{symbol} {side} entry={_fmt_price(entry)} was NOT posted — it looks like a duplicate "
                              f"of a trade already open (same symbol/side, entry within "
                              f"{DUPLICATE_SIGNAL_PRICE_TOLERANCE*100:.1f}%, opened in the last "
                              f"{DUPLICATE_SIGNAL_WINDOW_SECONDS//60} min). If this is actually a different setup, "
                              f"forward it again with a clearer/more distinct entry price."),
                )
                continue

            sig = {"side": side, "confirmed": True, "price": entry, "open_time": int(time.time() * 1000), "sl": sl}
            trade = open_new_trade(sig, symbol=symbol, tf_key="manual", display=display,
                                    preferred_signal_id=m.get("source_signal_id"), candle_states=candle_states)
            if not trade:
                m["status"] = "failed"
                continue

            # ⚠️ اگه این سیگنال از یک فوروارد واقعی تلگرام تشخیص داده شده (نه تایپ مستقیم توسط
            # ادمین یا تگ #signal)، رهگیری‌اش با قیمت زنده‌ی خودمون انجام نمی‌شه - چون قرار است
            # ادامه‌ی همین رشته‌ی فوروارد (پیام‌های رسیدن به تارگت/استاپ) هم توی کانال بیاد، و آن‌ها
            # منبع نتیجه‌ی این معامله‌ان (process_forwarded_results پایین‌تر). تا وقتی نتیجه فوروارد
            # نشده، این معامله فقط توی «معاملات باز» می‌مونه.
            # ⚠️ گارد ایمنی: یک سیگنالِ silent (بدون پستِ خودش، بدون چارتِ محلی - پایین‌تر) باید
            # همیشه forwarded_tracking=True هم باشه، وگرنه هیچ‌جا نه چارتی برای نمایش داره نه
            # رهگیری زنده‌ای فعاله و برای همیشه بی‌سروصدا در «معاملات باز» گیر می‌کنه. مسیر فعلی
            # (handle_altcoin_relay_post) همیشه هر دو رو با هم True می‌فرسته؛ این فقط یک محافظِ
            # اضافه‌ست در برابر استفاده‌ی اشتباه از silent در آینده.
            forwarded_tracking = bool(m.get("forwarded_tracking")) or bool(m.get("silent"))
            trade["forwarded_tracking"] = forwarded_tracking
            trade["manual_tf_cfg"] = manual_tf_cfg
            # ⚠️ برای چک تکراری/جهت‌مخالف بین‌منبعی بالا و پایین‌تر (logical_tf) - همون
            # matched_tf_key ("1m"/"5m"/... یا None اگه نامعلوم بود)، جدا از tf_key که همیشه
            # "manual" می‌مونه (برای نمایش/گروه‌بندی در /results دست‌نخورده).
            trade["logical_tf"] = matched_tf_key

            # ⚠️ silent=True (سیگنال‌های رله‌شده‌ی خودکار از کانال دوم - handle_altcoin_relay_post
            # در subscription_bot.py): خودِ پیام اصلی (با چارت کامل) همین الان با copyMessage
            # عیناً در کانال منتشر شده، پس نه نیاز به یک پستِ دومِ بازتولیدشده داریم، نه به
            # گرفتن چارت (fetch_chart_hist) از Twelve Data - که دقیقاً همون سهمیه‌ای‌ست که
            # کانال دوم برای رهاشدن ازش تأسیس شده؛ صدا زدنش اینجا دوباره همون محدودیت رو دور
            # می‌زد. معامله بدون چارت محلی هم کاملاً قابل رهگیری/محاسبه است چون forwarded_tracking
            # حتماً True است (رهگیری‌اش فقط از روی پیام‌های بعدی همون کانال دومه، نه قیمت زنده).
            silent = bool(m.get("silent"))
            hist = []
            if not silent:
                interval = manual_tf_cfg["td_interval"] or "15min"
                bar_seconds = manual_tf_cfg["bar_seconds"] or 15 * 60
                hist = fetch_chart_hist(symbol, interval=interval, bar_seconds=bar_seconds) or []
            candle_states[state_key] = {"open_trade": trade, "hist": hist}
            if not silent:
                _send_entry(display, "manual", manual_tf_cfg, sig, trade, hist)

            m["status"] = "active"
            m["activated_at"] = datetime.now(timezone.utc).isoformat()
            logger.info(f"📌 Manual signal activated: {symbol} {side} entry={entry} sl={sl} tf={manual_tf_cfg['label'] or '(none)'}")
        except Exception as e:
            logger.error(f"Failed to process manual signal {m.get('id')}: {e}")
            m["status"] = "error"
            m["reject_reason"] = f"internal error: {e}"
            notify_admin("manual_signal_item_crashed", Exception(
                f"Manual/forwarded signal {m.get('symbol')} {m.get('side')} (id={m.get('id')}) "
                f"crashed while processing and was skipped (marked 'error') so it doesn't block "
                f"other pending signals: {e}\n\n{traceback.format_exc()[-800:]}"))
            continue

    save_state(state)
    atomic_write_json(MANUAL_SIGNALS_FILE, manual_signals)


FORWARDED_RESULTS_FILE = os.path.join(DATA_DIR, "forwarded_results_queue.json")
FORWARDED_CLOSE_KINDS = {"stop", "breakeven", "sl_after_t2", "sl_after_t3", "runner_stop", "forwarded_closed"}
# 🔴 رفعِ باگِ واقعیِ «یک معامله همین چند لحظه پیش بسته شد ولی داخل ربات (نتایج/تاریخچه)
# هیچ‌وقت ثبت نشد»: قبلاً این عدد ۸ بود (~۴۰ ثانیه، چون این تابع هر تیک یعنی هر
# WS_CHECK_INTERVAL_SECONDS=۵ ثانیه صدا زده می‌شه). اما پیام سیگنال ورودی و پیام نتیجه هر
# دو باید اول از subscription_bot.py (جایی که فوروارد/رله پردازش می‌شه) به این‌جا برسن - که
# حتی با فیکسِ GIT_COMMIT_EVERY_SECONDS اونجا (۱۲۰→۴۵ ثانیه)، بدترین حالت ~۹۰ ثانیه طول
# می‌کشه. برای یک معامله‌ی سریع (باز و در عرض چند دقیقه بسته‌شده - دقیقاً همون چیزی که گزارش
# شد)، ممکنه پیامِ نتیجه زودتر از خودِ سیگنال ورودی به این‌جا برسه؛ با فقط ۴۰ ثانیه فرصت،
# رویداد نتیجه قبل از این‌که سیگنال ورودی اصلاً باز بشه تسلیم می‌شد. الان با ۱۲۰ تلاش
# (~۱۰ دقیقه)، حتی چند برابر بدترین‌حالت تاخیر سینک هم پوشش داده می‌شه - هزینه‌ی این صبرِ
# بیشتر صرفاً نگه‌داشتن یک رکورد کوچک در صف برای مدت بیشتره، هیچ ریسک/عارضه‌ی دیگه‌ای نداره.
FORWARDED_RESULT_MAX_ATTEMPTS = 120


def process_forwarded_results():
    """
    subscription_bot.py وقتی توی کانال یک پیامِ «نتیجه» (رسیدن به تارگت، خوردن استاپ،
    بریک‌ایون، بسته‌شدن رانر، ...) می‌بینه که دقیقاً با فرمت پیام‌های خروجِ همین فایل نوشته
    شده (چون این‌ها هم مثل پیام سیگنال ورودی، از یک منبع بیرونی فوروارد/کپی شده که همین قالب
    رو داره)، اون رو توی forwarded_results_queue.json صف می‌کنه. اینجا (candle_engine.py،
    صاحب واقعی candle_state.json/trade_history.json) هر رویداد صف‌شده رو به معامله‌ی بازِ
    forwarded_tracking=True متناظرش اعمال می‌کنیم.

    ⚠️ عمداً هیچ پیامی به کانال ارسال نمی‌شه - چون خودِ پیام فوروارد‌شده از قبل توسط ادمین
    توی کانال دیده می‌شه؛ اینجا فقط بایگانی/آمار داخلی (candle_state، trade_history، در نتیجه
    /results و گزارش روزانه) به‌روز می‌شه. تا وقتی نتیجه فوروارد نشده، معامله همچنان توی
    «معاملات باز» (Open right now) دیده می‌شه؛ همین‌که پیام نتیجه برسه، اگه نوعش «بستن کامل»
    باشه (استاپ/بریک‌ایون/سطوح میانی/رانر)، فوراً به trade_history منتقل می‌شه و در آمار
    حساب می‌شه؛ اگه فقط «رسیدن به یک تارگت» باشه، فقط hit علامت می‌خوره و باز می‌مونه.

    تطبیق با معامله‌ی درست: بین همه‌ی معاملات باز با forwarded_tracking=True، اونی که هم
    تیکر یکی هم نزدیک‌ترین قیمت ورود رو داره انتخاب می‌شه - چون ممکنه چند سیگنال فورواردی
    هم‌زمان (حتی روی یک نماد با entry متفاوت) باز باشن.
    """
    queue = read_json_resilient(
        FORWARDED_RESULTS_FILE, None, label="forwarded_results_queue.json",
        on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    if queue is None:
        return
    pending = [e for e in queue if e.get("status") == "pending"]
    if not pending:
        return

    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    changed = False

    for ev in pending:
        try:
            ticker = (ev.get("ticker") or "").upper()
            entry_hint = ev.get("entry")
            source_sid = (ev.get("source_signal_id") or "").upper() or None

            best_key, best_trade, best_diff = None, None, None
            # 🔴 اضافه شد: تطبیقِ دقیق بر اساس signal_id (همون 🆔/هشتگی که در متن پیام نتیجه
            # هم نمایش داده می‌شه - نگاه کنید _id_line/_id_hashtag و SOURCE_ID_RE در
            # subscription_bot.py) همیشه اول امتحان می‌شه، قبل از heuristic قدیمیِ
            # ticker+نزدیک‌ترین entry. چرا این اولویت داره و چرا برطرف‌کننده‌ی ریشه‌ای «بعضی
            # سیگنال‌ها اصلاً در نتایج حساب نمی‌شن» است: heuristic قدیمی هیچ آستانه‌ای برای
            # «چقدر نزدیک یعنی واقعاً همون معامله» نداشت - وقتی چند معامله‌ی باز روی همون
            # تیکر (مثلاً چند سیگنال PUMP پشت‌سرهم) forwarded_tracking=True داشتن، همیشه یکی
            # رو (حتی با اختلاف زیاد) به‌عنوان "بهترین" انتخاب می‌کرد؛ و اگه به هر دلیلی
            # (رند شدن قیمت در متن نمایشی، اختلاف جزئی نام تیکر) هیچ‌کدوم واقعاً تطبیق نداشت،
            # نتیجه یا به معامله‌ی اشتباه می‌چسبید یا بعد از FORWARDED_RESULT_MAX_ATTEMPTS با
            # unmatched رها می‌شد و اون معامله برای همیشه در «Open right now» گیر می‌کرد - هرگز
            # وارد trade_history.json نمی‌شد، یعنی هرگز در /results حساب نمی‌شد. signal_id
            # چون منحصر به فرد و پایدار است (دقیقاً همون چیزی که در متن پیام هم دیده می‌شه)،
            # این تطبیق را از یک حدسِ «نزدیک‌ترین» به یک جستجوی دقیقِ کلید-به-کلید تبدیل می‌کنه.
            if source_sid:
                for key, s in candle_states.items():
                    t = s.get("open_trade")
                    if t and not t.get("closed") and t.get("signal_id") == source_sid:
                        best_key, best_trade, best_diff = key, t, 0
                        break

            if not best_trade:
                # پشتیبانِ قدیمی: فقط برای پیام‌هایی که هیچ 🆔/هشتگی در متن‌شون نبود (رکوردهای
                # خیلی قدیمی‌تر از این فیچر) یا وقتی خودِ signal_id به هر دلیلی تطبیق نداشت.
                for key, s in candle_states.items():
                    t = s.get("open_trade")
                    if not t or t.get("closed") or not t.get("forwarded_tracking"):
                        continue
                    if (t.get("display") or "").upper() != ticker:
                        continue
                    diff = abs(t.get("entry", 0) - entry_hint) if entry_hint is not None else 0
                    if best_diff is None or diff < best_diff:
                        best_key, best_trade, best_diff = key, t, diff

            if not best_trade:
                ev["attempts"] = ev.get("attempts", 0) + 1
                if ev["attempts"] >= FORWARDED_RESULT_MAX_ATTEMPTS:
                    ev["status"] = "unmatched"
                    logger.warning(f"Forwarded result event for {ticker} (entry hint {entry_hint}) never matched an "
                                    f"open forwarded-tracked trade after {ev['attempts']} tries — giving up on it")
                    # 🔴 طبق درخواست صریح («در زیر هر پیام خطا دستورات قابل‌انجام آورده بشه تا با
                    # یک کلیک ... اقدام لازم انجام بشه»): این هشدار قبلاً فقط متنِ خام بود -
                    # ادمین باید دستی می‌گشت دنبال اینکه آیا اصلاً یک معامله‌ی باز مرتبط
                    # (forwarded_tracking=True) وجود داره، و اگه بله با چه signal_id ای، تا
                    # بتونه /admin_close_trade رو دستی تایپ کنه. حالا: (۱) همه‌ی معاملات
                    # فوروارد-رصدشونده‌ی هنوز-بازِ فعلی رو لیست می‌کنیم (نه فقط اونایی که دقیقاً
                    # روی ticker مطابقت داشتن - چون خودِ عدم‌تطبیق روی ticker اغلب دقیقاً همون
                    # علتِ ریشه‌ای unmatched شدنه، مثلاً یک اختلاف جزئی در نام نمایشی)، (۲) اگه
                    # این رویداد از نوع «بستن» (kind in FORWARDED_CLOSE_KINDS) باشه، هرکدوم از
                    # این معاملات رو با یک دکمه‌ی مستقیم «✅ Close here» قابل‌انتخاب می‌کنیم - با
                    # کلیک روی هرکدوم، دقیقاً همون final_r این رویداد (اگه پارس شده باشه) روی
                    # همون معامله اعمال و بسته می‌شه؛ صف admin_close_requests.json (همون
                    # مکانیزم تست‌شده‌ی /admin_close_trade) مصرف می‌شه، پس subscription_bot.py
                    # هیچ نیازی به دستکاری مستقیم candle_state.json نداره.
                    open_forwarded = []
                    for key, s in candle_states.items():
                        ot = (s or {}).get("open_trade")
                        if ot and not ot.get("closed") and ot.get("forwarded_tracking"):
                            open_forwarded.append((key, ot))
                    candidates_lines = []
                    command_lines = []
                    keyboard_rows = []
                    kind = ev.get("kind")
                    for key, ot in open_forwarded[:8]:  # سقف ۸ - جلوگیری از یک inline keyboard غول‌آسا
                        sid = ot.get("signal_id", key)
                        candidates_lines.append(f"  • {ot.get('display', '?')} ({ot.get('side', '?')}, "
                                                 f"entry {ot.get('entry', '?')}) — {sid}")
                        if kind in FORWARDED_CLOSE_KINDS:
                            r_label = f"{ev.get('result_r'):+.2f}R" if ev.get("result_r") is not None else "fair value"
                            keyboard_rows.append([{
                                "text": f"✅ Close here ({r_label}): {ot.get('display', '?')} · {sid}",
                                "callback_data": f"fwdapply:{sid}:{ev.get('result_r') if ev.get('result_r') is not None else ''}",
                            }])
                            # 🔴 طبق درخواست صریح («تمام دستورات قابل‌اجرا ... زیر هر پیام خطا
                            # نمایش داده بشه چون ادمین ممکنه فراموش کنه یا اشتباه تایپ کنه»):
                            # علاوه بر دکمه‌ی بالا (که سریع‌تره)، دستور متنیِ معادل هم عیناً
                            # (آماده‌ی کپی/تایپ، نه فقط توضیح) آورده می‌شه - برای وقتی دکمه‌ها
                            # به هر دلیلی در دسترس نیستن (مثلاً پیام قدیمی شده) یا ادمین ترجیح
                            # می‌ده صریح final_r خودش رو بده، نه fair-value/عدد این رویداد.
                            r_arg = f"{ev.get('result_r'):+.2f}" if ev.get("result_r") is not None else ""
                            command_lines.append(f"  <code>/admin_close_trade {sid} {r_arg}</code>".rstrip())
                    id_hint = (f"\nSignal id in this message's text: <b>{source_sid}</b> — no OPEN trade with "
                               f"exactly this id was found (it may already be closed, or its entry signal was "
                               f"never tracked at all)." if source_sid else
                               "\n(No #hashtag id was found in this message's text — it's likely an older-format "
                               "forward from before that feature, so this had to fall back to ticker+entry "
                               "matching, which is less reliable.)")
                    candidates_block = ("\n\nCurrently open forwarded-tracked trades (pick the right one if it's "
                                         "actually one of these under a different name):\n" + "\n".join(candidates_lines)
                                         if candidates_lines else "\n\nNo forwarded-tracked trades are currently open at all "
                                         "— the original entry signal for this may never have been tracked.")
                    commands_block = ("\n\nReady-to-run commands (tap to copy, edit the final_r if needed, then "
                                       "send):\n" + "\n".join(command_lines) if command_lines else "")
                    action_note = (
                        "" if kind in FORWARDED_CLOSE_KINDS else
                        "\n\n⚠️ This was a target-hit update (not a close) — closing a trade here would be wrong if "
                        "it's actually still running, so no one-click button is offered for this kind; please verify "
                        "manually and use /admin_close_trade only if you're sure it should be closed."
                    )
                    text = (
                        f"⚠️ Forwarded result never matched a trade\n\n"
                        f"Ticker: {ticker}  ·  Kind: {kind}  ·  Entry hint: {entry_hint}"
                        f"{id_hint}\n"
                        f"Tried to match for {ev['attempts']} scan cycles, giving up.\n\n"
                        f"If a trade for {ticker} is stuck showing as 'open' when it has actually closed, this is "
                        f"likely why."
                        f"{candidates_block}"
                        f"{commands_block}"
                        f"{action_note}"
                    )
                    keyboard = {"inline_keyboard": keyboard_rows} if keyboard_rows else None
                    notify_admin(f"forwarded_result_unmatched:{ticker}", Exception(text),
                                 reply_markup=keyboard, full_text=text)
                continue

            kind = ev.get("kind")

            if kind == "target_hit":
                level = ev.get("level")
                if level is not None and str(level) in best_trade["hit"]:
                    best_trade["hit"][str(level)] = True
                    changed = True
                    best_trade.setdefault("events", []).append(
                        _new_trade_event("rr", level_r=level, note="forwarded"))
                ev["status"] = "applied"
                continue

            if kind in FORWARDED_CLOSE_KINDS:
                # قبل از بستن، اگه به‌خاطر یک پیام میانی گم‌شده (مثلاً یک فوروارد رو ادمین جا
                # انداخته) هنوز بعضی سطوح رو نداریم، بر اساس خودِ نوع بسته‌شدن سطوح لازم رو هم
                # علامت می‌زنیم - دقیقاً همون فرضی که خودِ close_type ضمنی داره
                if kind in ("breakeven", "sl_after_t2", "sl_after_t3", "runner_stop"):
                    best_trade["hit"]["1"] = True
                if kind in ("sl_after_t2", "sl_after_t3", "runner_stop"):
                    best_trade["hit"]["2"] = True
                if kind in ("sl_after_t3", "runner_stop"):
                    best_trade["hit"]["4"] = True
                if kind == "runner_stop":
                    best_trade["hit"]["6"] = True
                best_trade["closed"] = True
                best_trade["close_type"] = kind
                best_trade.setdefault("events", []).append(_new_trade_event(kind, note="forwarded"))
                # ⚠️ باگ رفع‌شده: قبلاً اینجا از `ticker` (فقط نام کوتاه پارس‌شده از متن پیامِ
                # نتیجه، مثلاً "BTC") به‌جای symbol واقعی معامله استفاده می‌شد - یعنی در
                # trade_history.json (و در نتیجه در /results و گزارش روزانه، بخش «By symbol»)
                # اسم رمزارز بدون جفت‌ارز (/USD) ذخیره و نمایش داده می‌شد. حالا از
                # best_trade["symbol"] که همون لحظه‌ی باز شدن معامله با جفت کامل (مثلاً
                # "BTC/USD") ذخیره شده استفاده می‌شه - دقیقاً هم‌شکل با سیگنال‌های خودکار.
                log_trade_result(best_trade.get("symbol") or ticker, best_trade.get("tf_key") or "manual", best_trade,
                                  final_r_override=ev.get("result_r"))
                changed = True
                ev["status"] = "applied"
                continue

            ev["status"] = "unrecognized"
        except Exception as e:
            logger.error(f"Failed to apply forwarded result event {ev.get('ticker')}/{ev.get('kind')}: {e}")
            ev["status"] = "error"
            notify_admin("forwarded_result_item_crashed", Exception(
                f"Forwarded result event {ev.get('ticker')} {ev.get('kind')} crashed while processing "
                f"and was skipped (marked 'error') so it doesn't block other pending events: {e}\n\n"
                f"{traceback.format_exc()[-800:]}"))

    if changed:
        save_state(state)
    atomic_write_json(FORWARDED_RESULTS_FILE, queue)


ADMIN_CLOSE_REQUESTS_FILE = os.path.join(DATA_DIR, "admin_close_requests.json")


def process_admin_close_requests():
    """طبق درخواست («هر اتفاقی برای یک کاربر/سیگنال پیش بیاد باید توسط ادمین قابل حل باشه،
    بدون اختلال در بقیه‌ی ربات»): یک شیرِ اطمینانِ همیشگی - صرف‌نظر از این‌که چرا یک معامله
    گیر کرده (پیام نتیجه‌اش گم شده، فرمت منبع عوض شده، یا هر دلیل دیگه‌ای که هنوز کشف نشده)،
    ادمین با /admin_close_trade در subscription_bot.py می‌تونه دستی ببندش - بدون نیاز به هیچ
    فیکس کدی جدید. subscription_bot.py درخواست رو اینجا (admin_close_requests.json) صف
    می‌کنه؛ همینجا (candle_engine.py، صاحب واقعی candle_state.json) پیدا و بسته می‌شه، با
    close_type مشخص «admin_override» - تا در گزارش‌ها همیشه واضح باشه این نتیجه از قیمت
    واقعی محاسبه نشده، دستی توسط ادمین ثبت شده.

    🔴 اضافه شد: تطبیق حالا اول با signal_id پایدار (مثلاً «TC-004821» - همون که در پیام‌های
    کانال و هشدار stale_open_trade نشون داده می‌شه) امتحان می‌شه، نه فقط با state_key (کلیدِ
    سطل مثل «BTC/USD|4h»). چرا مهمه: state_key یک «سطل» است که همین‌که معامله‌ی فعلی‌اش بسته
    بشه، بلافاصله برای سیگنال کاملاً بعدیِ همون نماد/تایم‌فریم دوباره استفاده می‌شه - یعنی اگه
    ادمین چند دقیقه دیر /admin_close_trade رو با یک state_key قدیمی بفرسته و در همین فاصله
    یک سیگنال *جدید* در همون سطل باز شده باشه، قبلاً این تابع بدون هیچ خطایی همون سیگنالِ
    جدید (اشتباهی) رو می‌بست، نه سیگنالِ واقعاً مقصود ادمین رو. signal_id چون منحصر به همون
    یک سیگنال است و هیچ‌وقت با سیگنال بعدی دوباره استفاده نمی‌شه، این ابهام رو کاملاً حذف
    می‌کنه. برای سازگاری با هشدارهای قدیمی‌تر (قبل از این فیچر) که هنوز فقط state_key دارن،
    اگه با signal_id چیزی پیدا نشد، به همون روش قبلی (state_key) بازمی‌گرده.
    🔴 رفعِ باگ: قبلاً اینجا از t.get("tf", ...) به‌جای t.get("tf_key", ...) خونده می‌شد - چون
    خودِ آبجکتِ trade هیچ‌وقت فیلدی به‌اسم "tf" نداره (فقط "tf_key")، این همیشه به‌طور خاموش
    مقدار پیش‌فرض "manual" رو برمی‌گردوند، یعنی هر معامله‌ای که با /admin_close_trade بسته
    می‌شد - even یک سیگنال کاملاً خودکار روی 4h - در trade_history.json با tf="manual" ثبت
    می‌شد و آمار «By timeframe» رو (فقط برای معاملات admin-closed) به‌اشتباه زیر ردیف
    «manual» می‌ریخت."""
    requests_ = read_json_resilient(
        ADMIN_CLOSE_REQUESTS_FILE, None, label="admin_close_requests.json",
        on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
    if not requests_:
        return
    pending = [r for r in requests_ if r.get("status") == "pending"]
    if not pending:
        return

    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    changed = False

    def _find_open_by_key(key):
        # اول با signal_id (یکتا و پایدار) بین همه‌ی معاملات باز می‌گردیم؛ اگه پیدا نشد
        # (مثلاً کلیدی که ادمین داده یک state_key قدیمی است، نه یک signal_id)، به روش قبلی
        # (تطبیق مستقیم با state_key) برمی‌گردیم.
        for s in candle_states.values():
            candidate = s.get("open_trade")
            if candidate and not candidate.get("closed") and candidate.get("signal_id") == key:
                return candidate
        s = candle_states.get(key)
        candidate = s.get("open_trade") if s else None
        if candidate and not candidate.get("closed"):
            return candidate
        return None

    def _find_closed_history_record(key):
        # 🔴 اضافه شد: برای پشتیبانی از «ویرایشِ نتیجه‌ی یک سیگنالِ از قبل بسته‌شده» - جستجو
        # در trade_history.json (نه candle_states، چون معاملاتِ بسته‌شده دیگه در
        # candle_states به‌عنوان open_trade نگه داشته نمی‌شن) بر اساس همون signal_id.
        history = read_json_resilient(TRADE_HISTORY_FILE, [], label="trade_history.json",
                                       on_corrupt=lambda label, msg: notify_admin(f"corrupt_{label}", Exception(msg)))
        for h in history:
            if h.get("signal_id") == key:
                return history, h
        return history, None

    for r in pending:
        key = r.get("key")
        t = _find_open_by_key(key)
        if not t:
            # 🔴 طبق درخواست صریح («ادمین قابلیت ویرایش سود یا زیان برای هر سیگنال را داشته
            # باشد» + «تحت هر شرایطی که بود به‌عنوان سیگنال بسته‌شده حساب شود»): قبلاً وقتی
            # کلیدِ درخواستی بین معاملاتِ هنوز-باز پیدا نمی‌شد (یعنی معمولاً چون از قبل بسته
            # شده)، این درخواست همیشه «not_found» می‌شد - حتی اگه ادمین داشت آگاهانه سعی
            # می‌کرد نتیجه‌ی یک سیگنالِ از قبل بسته‌شده رو اصلاح کنه (مثلاً چون final_r غلط
            # ثبت شده بود). الان اول trade_history.json رو هم چک می‌کنیم - اگه سیگنال اونجا
            # پیدا بشه، این یک درخواستِ «ویرایش» است، نه یک شکست.
            history, closed_record = _find_closed_history_record(key)
            if closed_record is not None:
                requested_final_r = r.get("final_r")
                if requested_final_r is None:
                    # ویرایش بدون یک مقدارِ جدید معنا نداره - باید صریحاً final_r بدن.
                    old_r = closed_record.get("final_r")
                    old_r_label = f"{old_r:+.2f}R" if old_r is not None else "unknown"
                    notify_admin(
                        f"admin_close_already_closed:{key}",
                        Exception(f"⚠️ <b>Signal {key} is already closed</b> (current result: {old_r_label}).\n\n"
                                  f"To correct its result, send the command again with an explicit value: "
                                  f"<code>/admin_close_trade {key} NEW_FINAL_R</code> — e.g. "
                                  f"<code>/admin_close_trade {key} +2.5</code>."),
                    )
                    r["status"] = "already_closed_needs_value"
                    continue
                old_r = closed_record.get("final_r")
                closed_record["final_r"] = requested_final_r
                closed_record.setdefault("events", []).append({
                    "type": "admin_edit", "ts": datetime.now(timezone.utc).isoformat(),
                    "note": (f"admin corrected final_r from {old_r:+.2f} to {requested_final_r:+.2f}"
                             if old_r is not None else f"admin set final_r to {requested_final_r:+.2f}"),
                })
                atomic_write_json(TRADE_HISTORY_FILE, history)
                # ⚠️ عمداً پیامِ جدیدی به کانال ارسال نمی‌شه: پیامِ «بسته‌شدن» این سیگنال یک‌بار
                # (چه خودکار، چه با admin_override قبلی) قبلاً درست ارسال شده. این فقط یک
                # اصلاحِ عددی در آمار/گزارش‌هاست - نه یک رویدادِ عمومیِ تازه که لازم باشه به
                # مشترکین دوباره اعلام بشه.
                logger.info(f"Admin-edited closed trade {key}: final_r {old_r} -> {requested_final_r}")
                r["status"] = "applied"
                changed = True
                continue

            r["status"] = "not_found"
            # 🔴 طبق درخواست صریح («تمام دستورات قابل‌اجرا برای ادمین در زیر هر پیام خطا نمایش
            # داده بشه، به‌جای تایپ‌کردن، با دکمه»): قبلاً این هشدار فقط توضیح می‌داد key
            # اشتباهه - بعد فقط یک لیستِ متنیِ دستورات آماده (بدون دکمه) اضافه شد. الان از
            # همون callback_data «fwdapply:{signal_id}:{final_r}» استفاده می‌کنه که
            # forwarded_result_unmatched هم استفاده می‌کنه (handle_callback_query در
            # subscription_bot.py از قبل این pattern رو می‌شناسه و از طریق همون صفِ
            # admin_close_requests.json می‌بندتش) - یعنی کد جدیدی لازم نبود، فقط از یک مکانیزمِ
            # از قبل تست‌شده دوباره استفاده شد. final_r همون مقداری‌ست که خودِ ادمین در
            # درخواستِ اصلیِ ناموفق داده بود (اگه داده بود) - تا با یک کلیک، دقیقاً همون قصد
            # روی معامله‌ی درست اعمال بشه؛ اگه ندادن، خالی می‌مونه و یعنی fair-value خودکار.
            open_now = [(kk, s.get("open_trade")) for kk, s in candle_states.items()
                        if (s or {}).get("open_trade") and not s["open_trade"].get("closed")]
            requested_final_r = r.get("final_r")
            r_str = f"{requested_final_r:.4f}" if requested_final_r is not None else ""
            r_label = f"{requested_final_r:+.2f}R" if requested_final_r is not None else "auto fair-value"
            lines, keyboard_rows = [], []
            for kk, ot in open_now[:8]:  # سقف ۸ - جلوگیری از یک inline keyboard غول‌آسا
                sid = ot.get("signal_id", kk)
                lines.append(f"  • {ot.get('display', '?')} ({ot.get('side', '?')}, entry {ot.get('entry', '?')}) — {sid}")
                keyboard_rows.append([{
                    "text": f"✅ Close here ({r_label}): {ot.get('display', '?')} · {sid}",
                    "callback_data": f"fwdapply:{sid}:{r_str}",
                }])
            open_block = ("\n\nCurrently open trades (tap the matching one below if it's actually one of these "
                          "under a different name):\n" + "\n".join(lines)) if open_now else "\n\nNo trades are open at all right now."
            not_found_text = (
                f"⚠️ <b>/admin_close_trade — signal not found</b>\n\n"
                f"Requested key: <code>{key}</code>\n\n"
                f"No matching trade was found at all — neither open, nor in trade_history.json. "
                f"This usually means a typo in the id. Double-check against the stale-trade alert message "
                f"it came from — prefer the signal_id (e.g. TC-004821 or ALT-004821) shown there over the "
                f"older symbol|timeframe key when both are available."
                f"{open_block}"
            )
            # 🔴 حتماً full_text= پاس داده می‌شه - وگرنه notify_admin (وقتی full_text نداره) این
            # متن رو داخل str(error)[:500] رشته‌ی خطای پیش‌فرض قطع می‌کرد و لیست معاملات
            # باز/دکمه‌ها (که دقیقاً همین ارزشِ این هشدار است) نصفه یا کامل حذف می‌شد.
            keyboard = {"inline_keyboard": keyboard_rows} if keyboard_rows else None
            notify_admin(f"admin_close_not_found:{key}", Exception(not_found_text),
                         reply_markup=keyboard, full_text=not_found_text)
            continue
        final_r = r.get("final_r")
        if final_r is None:
            # 🔴 رفعِ نقصِ واقعیِ «پیش‌فرض قبلی همیشه صفر (بریک‌ایون) بود، حتی وقتی معامله
            # واقعاً چند تارگت رو زده بود»: طبق طراحی این سیستم، وقتی یک سیگنال «گیر می‌کنه»،
            # دلیلش تقریباً همیشه گم‌شدن/عدم تطبیق فقط رویداد *بسته‌شدن* است (نه رویدادهای
            # target_hit، که جدا و معمولاً موفق پردازش می‌شن - نگاه کنید process_forwarded_results)
            # - یعنی t["hit"] معمولاً دقیقاً همون پیشرفت واقعی رو نشون می‌ده. بنابراین وقتی
            # ادمین final_r رو مشخص نمی‌کنه (نه در متن دستور، نه دکمه‌ی «Close now — fair
            # value»)، منصفانه‌ترین و دقیق‌ترین پیش‌فرض همینه: همون چیزی که compute_final_r
            # (صاحبِ واحدِ حقیقتِ این محاسبه در shared_risk_config.py) از روی پیشرفت واقعاً
            # ثبت‌شده حساب می‌کنه - نه یک صفرِ کورکورانه که می‌تونست به‌ناحق سود واقعی یک
            # معامله‌ی چند-تارگته رو نادیده بگیره. برای اجبارِ آشکارِ صفر (وقتی ادمین دلیلی
            # داره که به این تشخیص خودکار اعتماد نکنه)، همچنان می‌شه صریحاً final_r=0 داد.
            final_r = compute_final_r(t)
        t["closed"] = True
        t["close_type"] = "admin_override"
        t.setdefault("events", []).append(_new_trade_event(
            "admin_override", note=f"manually closed by admin (final_r={final_r:+.2f})"))
        try:
            log_trade_result(t.get("symbol", "?"), t.get("tf_key", "manual"), t,
                              final_r_override=final_r)
        except Exception as e:
            logger.error(f"log_trade_result failed for admin close of {key}: {e}")
        # 🔴 اضافه شد: طبق درخواست صریح («پیام بسته‌شدن آن مانند سایر پیام‌های بسته‌شدن مشابه
        # در کانال ارسال شود»): قبلاً وقتی ادمین یک معامله‌ی گیرکرده رو دستی می‌بست، هیچ پیامی
        # به کانال نمی‌رفت - فقط داخل ربات (trade_history.json) ثبت می‌شد؛ یعنی از دید کسی که
        # کانال رو دنبال می‌کنه، اون سیگنال برای همیشه «باز» به‌نظر می‌رسید، هیچ‌وقت خبرِ
        # بسته‌شدنش منتشر نمی‌شد. الان دقیقاً هم‌الگو با بقیه‌ی پیام‌های بسته‌شدن (بدون چارت،
        # ریپلای به پیامِ ورودِ همون سیگنال - نگاه کنید _force_close_trade که همین الگو رو
        # برای «سیگنال مخالف» استفاده می‌کنه) یک پیام ارسال می‌شه. اگه ارسال به هر دلیلی (مثلاً
        # قطعیِ موقتِ API تلگرام) شکست بخوره، خودِ بسته‌شدنِ داخلی/آمار دست‌نخورده می‌مونه -
        # فقط یک notify_admin جداگانه اطلاع می‌ده که پیامِ کانال نرفته، تا لازم باشه دستی چک
        # بشه.
        try:
            tf_label = _tf_label_for_trade(t)
            close_msg = format_admin_close_message(t.get("display", "?"), tf_label, t, final_r)
            reply_id = t.get("signal_message_id")
            if not send_photo(close_msg, None, reply_to_message_id=reply_id):
                notify_admin(f"admin_close_channel_post_failed:{key}", Exception(
                    f"Signal {t.get('signal_id', key)} was closed internally (final_r={final_r:+.2f}) and IS "
                    f"correctly counted in /results and the daily report, but the channel close message failed "
                    f"to send. You may want to post a manual note in the channel."))
        except Exception as e:
            logger.error(f"Failed to send admin-close channel message for {key}: {e}")
            notify_admin(f"admin_close_channel_post_crashed:{key}", Exception(
                f"Signal {t.get('signal_id', key)} was closed internally (final_r={final_r:+.2f}) and IS "
                f"correctly counted in /results and the daily report, but sending the channel close message "
                f"raised an error: {e}"))
        r["status"] = "applied"
        changed = True
        logger.info(f"Admin-closed stuck trade {t.get('signal_id', key)} (final_r={final_r:+.2f})")

    if changed:
        save_state(state)
    atomic_write_json(ADMIN_CLOSE_REQUESTS_FILE, requests_)


def process_manual_and_forwarded_queues():
    """پردازش صف سیگنال‌های دستی/فوروارد‌شده (manual_signals.json) و نتایج فوروارد‌شده
    (forwarded_results_queue.json). قبلاً این دو تابع فقط داخل run_scan_cycle() صدا زده
    می‌شدن - یعنی هر ۵ دقیقه (SCAN_CYCLE_INTERVAL_SECONDS) یک‌بار، چون run_scan_cycle خودش
    محدود به همون فاصله بود (به‌خاطر سقف روزانه‌ی Twelve Data).

    ⚠️ چرا جدا شدن و به یک تابع مستقل با فراخوانیِ هر تیکِ حلقه‌ی اصلی منتقل شدن (علت گزارش
    «تاخیر زیاد در ارسال سیگنال + نقطه‌ی ورود با قیمت لحظه‌ای فرق داره» و «تاخیر زیاد در
    محاسبه/گزارش نتیجه»): برخلاف اسکن BTC/ETH، این دو تابع هیچ هزینه‌ی API واقعی ندارن -
    process_manual_signals فقط وقتی صفِ محلی pending داره یک fetch_live_price (۱ کردیت) به
    ازای هر سیگنالِ در صف انجام می‌ده (نه به ازای هر بار چک‌کردن)، و اگه صف خالی باشه بلافاصله
    و بدون هیچ تماس API برمی‌گرده؛ process_forwarded_results هم مشابه، کاملاً محلی و بدون
    هیچ تماس API است. یعنی چک‌کردنشون هر چند ثانیه یک‌بار (به‌جای هر ۵ دقیقه) هیچ هزینه‌ی
    اضافه‌ای به بودجه‌ی روزانه نمی‌زنه - فقط سرعت رسیدگی رو زیاد می‌کنه.

    این تاخیر (تا ۵ دقیقه، بدترین حالت نزدیک ۱۰ دقیقه اگه سیگنال درست بعد از شروع یک دور صف
    بشه) دقیقاً همون چیزی بود که باعث می‌شد: (۱) سیگنال‌های رله‌شده‌ی VIP دیر واقعاً «باز»
    بشن - یعنی چک اعتبار قیمت (fetch_live_price در process_manual_signals) با تاخیر انجام
    می‌شد، نه لحظه‌ای که سیگنال واقعاً به کانال رسید، (۲) نتیجه‌ی معاملات (تارگت/استاپ) با
    همون تاخیر توی candle_state.json/trade_history.json اعمال می‌شد - یعنی /results و
    گزارش روزانه با تاخیر تا ۵-۱۰ دقیقه‌ای عقب‌تر از چیزی بودن که کاربر همین الان توی کانال
    می‌دید. الان با فراخوانی هر تیک (هر WS_CHECK_INTERVAL_SECONDS=۵ ثانیه)، سقف تاخیر عملاً
    به فاصله‌ی pull گیت بین دو اسکریپت محدود می‌شه.
    🔴 توجه: تا همین اواخر subscription_bot.py هنوز GIT_COMMIT_EVERY_SECONDS=۱۲۰ ثانیه بود
    (نه ۴۵ مثل اینجا) - یعنی بدترین حالتِ واقعی رفت‌وبرگشت ۱۲۰+۴۵=۱۶۵ ثانیه بود، نه «میانگین
    ~۲۲ ثانیه»ای که این کامنت قبلاً ادعا می‌کرد (که فقط سهم candle_engine.py رو حساب کرده
    بود، نه سهم subscription_bot.py رو). دقیقاً همین گپ باعث می‌شد نتیجه‌ی یک معامله‌ی سریع
    (بازشده و ظرف چند دقیقه بسته‌شده) زودتر از خودِ سیگنال ورودی به این‌جا برسه و
    FORWARDED_RESULT_MAX_ATTEMPTS تموم بشه. الان با ۴۵ ثانیه در هر دو طرف، بدترین حالت ~۹۰
    ثانیه‌ست - و FORWARDED_RESULT_MAX_ATTEMPTS هم جداگانه به ۱۲۰ تلاش (~۱۰ دقیقه) افزایش
    پیدا کرده تا چند برابر این مارجین امن باشه.

    ⚠️ همون رفتار مقاوم قبلی حفظ شده: اگه یکی از این دو تابع (نه هر آیتمِ داخلش - اون خودش
    جدا try/except داره) کلاً استثنا بگیره، این wrapping بیرونی notify_admin می‌کنه (با
    traceback کامل) تا بی‌سروصدا برای همیشه گیر نکنه، دقیقاً مثل قبل."""
    try:
        process_manual_signals()
    except Exception as e:
        logger.error(f"process_manual_signals failed: {e}")
        notify_admin("process_manual_signals_crashed", Exception(
            f"{e}\n\n{traceback.format_exc()[-1200:]}\n\n⚠️ This means NO manual/forwarded "
            f"signals were processed this tick (none opened, none closed) - they'll stay "
            f"'pending' until this is fixed."))

    try:
        process_forwarded_results()
    except Exception as e:
        logger.error(f"process_forwarded_results failed: {e}")
        notify_admin("process_forwarded_results_crashed", Exception(
            f"{e}\n\n{traceback.format_exc()[-1200:]}\n\n⚠️ This means NO forwarded target/stop "
            f"result messages were applied this tick - open forwarded trades won't update or "
            f"close until this is fixed."))

    try:
        process_admin_close_requests()
    except Exception as e:
        logger.error(f"process_admin_close_requests failed: {e}")
        notify_admin("process_admin_close_requests_crashed", Exception(
            f"{e}\n\n{traceback.format_exc()[-1200:]}\n\n⚠️ Any pending /admin_close_trade requests "
            f"were not applied this tick."))


def run_scan_cycle(latest_price_ts: Optional[Dict[str, float]] = None, price_lock=None):
    """یک دور کامل اسکن: سیگنال‌های جدید خودکار (بیت‌کوین/اتریوم) + گزارش روزانه (اگه وقتشه)
    + رهگیری زنده‌ی REST برای معاملات باز (پشتیبان WebSocket). این تابع عمداً محدود به هر
    SCAN_CYCLE_INTERVAL_SECONDS (۵ دقیقه) اجرا می‌شه - چون اسکن BTC/ETH هزینه‌ی واقعی API
    (Twelve Data، سقف ۸۰۰ درخواست/روز) داره و نباید بیشتر از این فشرده بشه.

    ⚠️ توجه: process_manual_signals() و process_forwarded_results() دیگه اینجا نیستن - به
    حلقه‌ی اصلی (main) منتقل شدن تا هر تیک (چند ثانیه یک‌بار) اجرا بشن، نه فقط هر ۵ دقیقه.
    دلیل انتقال (علت گزارش «تاخیر زیاد در ارسال سیگنال + تاخیر زیاد در محاسبه/گزارش نتیجه»)
    پایین‌تر، کنار محل صدا زدنشون در main() توضیح داده شده.

    🔴 رفعِ یک اورباجت واقعی در بودجه‌ی روزانه‌ی Twelve Data: تخمین «۷۸۰ از ۸۰۰» (بالای فایل،
    کنار TF_CHECK_INTERVAL_SECONDS) فقط هزینه‌ی اسکنِ دوره‌ای کندل‌ها رو حساب کرده بود - نه
    این‌که رهگیریِ REST پایین‌تر (fetch_live_price، هر بار ۱ کردیت) هم دقیقاً روی همون بودجه‌ی
    مشترک حساب می‌شه. قبلاً معاملات باز بیت‌کوین/اتریوم (که WebSocket همین الان با تاخیر زیر
    ۱۰ ثانیه و بدون هیچ هزینه‌ی API دارن چکشون می‌کنه) هم بدون قید و شرط وارد همین صف REST
    هر ۱۵ دقیقه می‌شدن - یعنی فقط با ۱ معامله‌ی باز بیت‌کوین/اتریوم مداوم، رهگیری REST به‌تنهایی
    تا ~۹۶ کردیت/روز اضافه مصرف می‌کرد، درحالی‌که کل بودجه‌ی آزادِ باقیمانده ~۲۰ کردیت/روز
    بود - یعنی سقف روزانه‌ی Twelve Data معمولاً همون اوایل روز پر می‌شد و بعدش هم اسکن
    کندل‌های جدید و هم همین رهگیری REST (که تنها راه رهگیریِ سیگنال‌های دستیِ altcoin - غیر
    بیت‌کوین/اتریوم - است) با خطای rate-limit شکست می‌خوردن، بدون این‌که کسی متوجه بشه چرا.
    الان: نمادهایی که WebSocket‌شون همین الان (نه ۲۰ ثانیه پیش - طبق همون آستانه‌ی
    WS_PRICE_STALE_SECONDS) سالمه، اصلاً وارد صف REST نمی‌شن - چون قبلاً هر ۵ ثانیه با دقت
    بهتر چک شدن؛ فقط وقتی WebSocket واقعاً قطع/بیات باشه (یا برای نمادهایی که اصلاً روی
    WebSocket نیستن - altcoinهای دستی)، این REST fallback وارد عمل می‌شه. این تقریباً کل
    بودجه‌ی آزاد رو برای جایی که واقعاً بهش نیاز است (سیگنال‌های دستی altcoin) آزاد می‌کنه."""
    try:
        maybe_send_daily_report()
    except Exception as e:
        logger.error(f"maybe_send_daily_report failed: {e}")

    try:
        maybe_send_weekly_report()
    except Exception as e:
        logger.error(f"maybe_send_weekly_report failed: {e}")

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

    def _ws_symbol_healthy(symbol: Optional[str]) -> bool:
        if not symbol or symbol not in WS_SYMBOL_MAP or latest_price_ts is None or price_lock is None:
            return False
        with price_lock:
            ts = latest_price_ts.get(symbol, 0)
        return (time.time() - ts) <= WS_PRICE_STALE_SECONDS

    # ---- رهگیری زنده‌ی REST برای معاملات باز (فقط برای نمادهایی که WebSocket سالم پوششون
    # نمی‌ده - altcoinهای دستی، یا بیت‌کوین/اتریوم در حالت نادر قطعیِ طولانیِ WebSocket) ----
    live_last_checked = state.setdefault("live_last_checked", {})
    open_combos = [key for key, s in candle_states.items()
                   if s.get("open_trade") and not s["open_trade"].get("closed")
                   and not s["open_trade"].get("forwarded_tracking")
                   and not _ws_symbol_healthy(s["open_trade"].get("symbol"))]
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
        tf_cfg = trade.get("manual_tf_cfg") or TIMEFRAMES.get(tf_key, MANUAL_TF_CFG)
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
    try:
        check_stale_open_trades(candle_states)
    except Exception as e:
        logger.error(f"check_stale_open_trades failed: {e}")


# 🔴 اضافه شد: نگهبانِ «معامله‌ی گیرکرده». طبق درخواست صریح («تمام سیگنال‌ها و نتایجشون باید
# در لحظه مشخص بشه، نه با تاخیر زیاد»)، اگه هر معامله‌ای (خودکار، دستی، یا فوروارد‌شده) بیش
# از STALE_OPEN_TRADE_HOURS ساعت بدون هیچ رویداد بسته‌شدنی «باز» بمونه، این خودش یک نشونه‌ی
# قویه که یک‌جای مسیرِ رهگیری (چه REST/WS، چه تطبیق پیام نتیجه‌ی فوروارد‌شده) شکسته - نه اینکه
# لزوماً معامله‌ی واقعی هنوز واقعاً باز باشه. قبلاً هیچ مکانیزمی برای تشخیص این حالت نبود؛ یک
# معامله‌ی گیرکرده می‌تونست هفته‌ها بی‌سروصدا در candle_state.json بمونه، درحالی‌که از دید
# کاربر انگار نتیجه‌اش هیچ‌وقت گزارش نشده. این تابع چیزی رو خودکار نمی‌بندد (چون بستن با یک
# نتیجه‌ی حدسی/اشتباه از هیچ‌چیز بدتره - آمار trade_history رو کاملاً غیرقابل‌اعتماد می‌کنه)؛
# فقط با یک هشدار (هر بار فقط یک‌بار، با cooldown خودِ notify_admin) طوری به ادمین اطلاع
# می‌ده که با ابزارهای موجود (مثلاً فوروارد دوباره‌ی نتیجه، یا بررسی دستی قیمت) حل بشه.
STALE_OPEN_TRADE_HOURS = 48


def check_stale_open_trades(candle_states: Dict[str, Any]) -> None:
    now_ms = int(time.time() * 1000)
    threshold_ms = STALE_OPEN_TRADE_HOURS * 3600 * 1000
    for key, s in candle_states.items():
        t = s.get("open_trade")
        if not t or t.get("closed"):
            continue
        opened_at_ms = t.get("opened_at_ms")
        if not opened_at_ms or (now_ms - opened_at_ms) < threshold_ms:
            continue
        age_hours = round((now_ms - opened_at_ms) / 3600000, 1)
        kind = "forwarded-tracked" if t.get("forwarded_tracking") else "automatic/manual"
        signal_id = t.get("signal_id")
        # 🔴 مرجعی که در دکمه‌ها/متن به‌عنوان هدف /admin_close_trade استفاده می‌شه: signal_id
        # پایدار و یکتا اگه موجود باشه (همه‌ی سیگنال‌های ساخته‌شده بعد از این فیچر)، وگرنه (فقط
        # برای سیگنال‌های خیلی قدیمی‌تر که هنوز در حال ردیابی موندن) همون key سطل به‌عنوان
        # fallback - چون process_admin_close_requests از هر دو پشتیبانی می‌کنه.
        ref = signal_id or key
        hit_labels = [TARGET_LABELS[lvl].replace("Target ", "T") for lvl in RR_TARGETS if t.get("hit", {}).get(str(lvl))]
        hit_part = "/".join(hit_labels) if hit_labels else "none"
        fair_r = compute_final_r(t)
        opened_at_str = (datetime.fromtimestamp(opened_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                          if opened_at_ms else "?")

        # 🔴 طبق درخواست صریح: پیام دیگه فقط یک متنِ خطای فشرده و مبهم نیست - همه‌ی جزییاتِ
        # خودِ سیگنال (نه فقط کلید داخلی‌اش) رو داره، تا بدون نیاز به /signal یا سرچ دستی هم
        # بشه فهمید دقیقاً چه معامله‌ای گیر کرده.
        text = (
            f"⚠️ <b>Stuck open trade — needs review</b>\n\n"
            f"🆔 {ref}\n"
            f"{t.get('display', key)} {t.get('side', '?')} ({kind})\n"
            f"Entry: {t.get('entry', '?')}  ·  Current stop: {t.get('sl', '?')}\n"
            f"Targets hit: {hit_part}{' · Runner trailing' if t.get('trailing') else ''}\n"
            f"Fair-value result if closed now: <b>{fair_r:+.2f}R</b>\n"
            f"Opened: {opened_at_str}  ·  Open for {age_hours}h (threshold {STALE_OPEN_TRADE_HOURS}h)\n\n"
            f"Likely stuck because its close event was missed/unmatched, not because it's "
            f"genuinely still open. Check the real price against entry/stop/targets, then use "
            f"a button below — or /admin_close_trade {ref} [final_r] manually."
        )
        # 🔴 طبق درخواست صریح: «دستورات قابل انجام ... در زیر پیام آورده شود تا با کلیک روی
        # هر دستوری ... اقدام لازم ... در سریعترین حالت ممکن انجام شود» - سه اقدامِ بستن
        # (با پیش‌فرضِ منطقی‌ترین یعنی fair-value، به‌علاوه‌ی دو override صریح) + یک دکمه‌ی
        # نمایش کامل مسیر حرکتی (همون /signal، بدون نیاز به تایپ دستی). subscription_bot.py
        # (تنها فرآیندی که واقعاً callback_query دریافت می‌کنه، چون تنها اونه که getUpdates
        # پیوسته داره) این callback_data ها رو می‌فهمه و می‌بنده - نگاه کنید handle_callback.
        keyboard = {
            "inline_keyboard": [
                [{"text": f"✅ Close now — fair value ({fair_r:+.2f}R)", "callback_data": f"aclt:{ref}:cur"}],
                [{"text": "❌ Force loss (-1R)", "callback_data": f"aclt:{ref}:loss"},
                 {"text": "⚪ Force breakeven (0R)", "callback_data": f"aclt:{ref}:be"}],
                [{"text": "🔍 Full timeline", "callback_data": f"asig:{ref}"}],
            ]
        }
        notify_admin(f"stale_open_trade:{ref}", Exception(text), reply_markup=keyboard, full_text=text)


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


def _run_binance_stream(latest_prices: Dict[str, float], latest_price_ts: Dict[str, float], price_lock, stop_event, connected_flag: Dict[str, bool]) -> None:
    import websocket

    def on_open(ws):
        logger.info("🔌 Binance Futures WebSocket connected (mark price stream)")
        connected_flag["ok"] = True

    def on_message(ws, message):
        parsed = parse_binance_mark_price_message(message)
        if parsed:
            with price_lock:
                latest_prices[parsed["symbol"]] = parsed["price"]
                latest_price_ts[parsed["symbol"]] = time.time()

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


def _run_coinbase_stream(latest_prices: Dict[str, float], latest_price_ts: Dict[str, float], price_lock, stop_event) -> None:
    product_ids = list(WS_SYMBOL_MAP.values())

    def on_open(ws):
        logger.info(f"🔌 Coinbase WebSocket connected (fallback) — subscribing to {product_ids}")
        ws.send(json.dumps({"type": "subscribe", "product_ids": product_ids, "channels": ["ticker"]}))

    def on_message(ws, message):
        parsed = parse_ws_ticker_message(message)
        if parsed:
            with price_lock:
                latest_prices[parsed["symbol"]] = parsed["price"]
                latest_price_ts[parsed["symbol"]] = time.time()

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


def start_price_stream(latest_prices: Dict[str, float], latest_price_ts: Dict[str, float], price_lock, stop_event) -> None:
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
        target=_run_binance_stream, args=(latest_prices, latest_price_ts, price_lock, binance_stop, connected_flag), daemon=True,
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
    _run_coinbase_stream(latest_prices, latest_price_ts, price_lock, stop_event)


# ⚠️ اگه اتصال WebSocket بی‌سروصدا قطع بشه (نه خطای صریح، فقط دیگه هیچ تیکی نیاد)، قبلاً
# کد همچنان با آخرین قیمتِ رسیده - هرچقدرم قدیمی - معامله رو چک می‌کرد؛ یعنی اگه قیمت واقعی
# در همین حین از استاپ/تارگت رد شده باشه، تشخیصش تا وصل‌شدن دوباره‌ی WS عقب می‌افتاد. الان هر
# تیک با زمان دریافتش ثبت می‌شه و اگه قدیمی‌تر از این آستانه بود، اون نماد رو از چک WS رد
# می‌کنیم تا due_live (پشتیبان REST) بجاش قیمت تازه بگیره - یعنی هیچ‌وقت با قیمت بیات تصمیم
# گرفته نمی‌شه.
WS_PRICE_STALE_SECONDS = 20


def check_ws_open_trades(latest_prices: Dict[str, float], latest_price_ts: Dict[str, float], price_lock) -> None:
    """معاملات باز بیت‌کوین/اتریوم (چه خودکار چه دستی‌ ادمین) رو با آخرین قیمتِ رسیده از
    WebSocket چک می‌کنه - این تابع هر چند ثانیه یک‌بار در حلقه‌ی اصلی صدا زده می‌شه، بدون
    هیچ هزینه‌ی API. روی هر ورودی candle_states کار می‌کنه (state_key هرچی باشه، اتوماتیک
    یا MANUAL|...)، چون نماد از خودِ trade["symbol"] خونده می‌شه، نه از پارس‌کردن کلید."""
    now = time.time()
    with price_lock:
        prices_snapshot = dict(latest_prices)
        ts_snapshot = dict(latest_price_ts)
    if not prices_snapshot:
        return

    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    changed = False

    for state_key, sym_state in candle_states.items():
        trade = sym_state.get("open_trade")
        if not trade or trade.get("closed"):
            continue
        if trade.get("forwarded_tracking"):
            # این معامله رهگیری‌اش با پیام‌های فوروارد‌شده انجام می‌شه (process_forwarded_results)
            # نه با قیمت زنده‌ی خودمون - رد می‌کنیم
            continue
        symbol = trade.get("symbol")
        if symbol not in WS_SYMBOL_MAP:
            continue
        live_price = prices_snapshot.get(symbol)
        if not live_price:
            continue
        price_age = now - ts_snapshot.get(symbol, 0)
        if price_age > WS_PRICE_STALE_SECONDS:
            # قیمت WS برای این نماد بیش‌ازحد قدیمیه - به‌جای تصمیم با داده‌ی بیات، رد می‌کنیم
            # و می‌ذاریم due_live (چک REST دوره‌ای) با قیمت تازه رسیدگی کنه
            logger.warning(f"WS price for {symbol} is stale ({price_age:.0f}s old) — skipping, waiting for REST fallback")
            continue
        display = trade.get("display") or WATCHLIST_SYMBOLS.get(symbol, symbol)
        tf_key = trade.get("tf_key") or "manual"
        tf_cfg = trade.get("manual_tf_cfg") or TIMEFRAMES.get(tf_key, MANUAL_TF_CFG)
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
    latest_price_ts: Dict[str, float] = {}
    price_lock = threading.Lock()
    stop_event = threading.Event()

    ws_thread = threading.Thread(target=start_price_stream, args=(latest_prices, latest_price_ts, price_lock, stop_event), daemon=True)
    ws_thread.start()
    logger.info("🚀 Continuous engine started — WebSocket monitors BTC/ETH live, "
                f"full scan cycle every {SCAN_CYCLE_INTERVAL_SECONDS}s, will run up to {LOOP_MAX_SECONDS/3600:.1f}h")

    start = time.time()
    last_scan = 0  # صفر یعنی همون اول یک اسکن کامل انجام بشه
    last_commit = start

    while time.time() - start < LOOP_MAX_SECONDS:
        try:
            check_ws_open_trades(latest_prices, latest_price_ts, price_lock)
        except Exception as e:
            logger.error(f"check_ws_open_trades failed: {e}")
            notify_admin("check_ws_open_trades", e)

        # ⚠️ هر تیک (نه فقط هر ۵ دقیقه‌ی SCAN_CYCLE_INTERVAL_SECONDS) اجرا می‌شه - چون بدون
        # هزینه‌ی API واقعی است و این تنگ‌کردن دقیقاً چیزیه که تاخیر ارسال/محاسبه‌ی نتیجه‌ی
        # سیگنال‌های دستی/فوروارد‌شده (رله‌ی VIP) رو از تا ۵-۱۰ دقیقه به عملاً فاصله‌ی pull
        # گیت بین دو اسکریپت (~۲۲ ثانیه میانگین) کاهش می‌ده. توضیح کامل در docstring خودِ تابع.
        try:
            process_manual_and_forwarded_queues()
        except Exception as e:
            logger.error(f"process_manual_and_forwarded_queues failed: {e}")
            notify_admin("process_manual_and_forwarded_queues", e)

        if time.time() - last_scan >= SCAN_CYCLE_INTERVAL_SECONDS:
            try:
                run_scan_cycle(latest_price_ts, price_lock)
            except Exception as e:
                logger.error(f"run_scan_cycle failed: {e}")
                notify_admin("run_scan_cycle", e)
            last_scan = time.time()

        if time.time() - last_commit >= GIT_COMMIT_EVERY_SECONDS:
            try:
                git_commit_and_push()
            except Exception as e:
                logger.error(f"git_commit_and_push failed: {e}")
                notify_admin("git_commit_and_push", e)
            send_heartbeat()
            last_commit = time.time()

        time.sleep(WS_CHECK_INTERVAL_SECONDS)

    stop_event.set()
    git_commit_and_push(final=True)
    logger.info("✅ Loop finished for this session (next scheduled run continues seamlessly)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error in main(): {e}")
        try:
            notify_admin("main (fatal)", e)
        except Exception:
            pass
        raise

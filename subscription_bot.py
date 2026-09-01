# -*- coding: utf-8 -*-
"""
Subscription Bot v4
(کدنویسی و کامنت‌ها فارسی برای شما؛ تمام پیام‌هایی که کاربر می‌بیند انگلیسی است)

اضافه‌شده نسبت به نسخه قبل:
  - چند ادمین هم‌زمان (ADMIN_USER_IDS)
  - سیستم کد تخفیف قابل‌مدیریت توسط ادمین (درصدی، محدودیت تعداد استفاده، تاریخ انقضا)
  - دکمه «✅ I've Paid» برای تایید آنی پرداخت به‌جای منتظرماندن تا اجرای بعدی کرون
  - پیام‌ها کوتاه‌تر، مینیمال‌تر و خواناتر
"""

import os
import json
import random
import re
import time
import traceback
import requests
from typing import Optional, Dict, Any
from datetime import datetime, timezone

# ⚠️ منطق git add/commit/pull --rebase/push (با retry فوری روی برخورد push بین این اسکریپت
# و candle_engine.py) از اینجا میاد - قبلاً یک نسخه‌ی دستیِ جداگانه (بدون retry فوری) اینجا
# تعریف شده بود که از نسخه‌ی shared_git_sync.py (که خودِ همین کدبیس برای رفع همین drift ساخته
# بود) عقب افتاده بود؛ یعنی دقیقاً همون مشکلی که shared_git_sync.py قرار بود جلویش را بگیرد،
# چون آن ماژول نوشته شده بود ولی هیچ‌جا import/استفاده نمی‌شد. رفع شد.
from shared_git_sync import sync_data_dir, pull_latest_readonly, atomic_write_json, read_json_resilient

# ================== تنظیمات پایه ==================
BOT_TOKEN = os.environ["BOT_TOKEN"]
PRIVATE_CHANNEL_ID = os.environ["PRIVATE_CHANNEL_ID"]

# ⚠️ کانال منبعِ آلتکوین (اختیاری): کانال دومِ متعلق به خودِ کاربر که با یک دیپلوی جداگانه‌ی
# همین سیستم (candle_engine.py + subscription_bot.py دیگر) روی آلتکوین‌ها سیگنال صادر می‌کند.
# اگر ست بشه، این ربات (که باید از قبل ادمینِ آن کانال هم شده باشد - در غیر این صورت اصلاً
# channel_post آن کانال به getUpdates این توکن نمی‌رسد) بی‌سروصدا هر پیام آن کانال (به‌جز
# گزارش روزانه‌ی خودش) را عیناً - بدون برچسب Forwarded from - در PRIVATE_CHANNEL_ID کپی
# می‌کند و اگر ساختار سیگنال/نتیجه‌ی شناخته‌شده داشته باشد، مستقیم در همان خط‌لوله‌ی محاسباتیِ
# موجود (queue_manual_signal / queue_forwarded_result) با forwarded_tracking=True ثبتش
# می‌کند - یعنی هیچ رهگیری زنده‌ی موازی/مستقلی برای این معاملات انجام نمی‌شود؛ نتیجه‌شان
# دقیقاً همان چیزی است که خودِ کانال منبع (که با همین دقت آن را محاسبه کرده) گزارش می‌دهد.
# اگر خالی/ست‌نشده باشد، این قابلیت کاملاً غیرفعال است و رفتار ربات دقیقاً مثل قبل است.
ALTCOIN_SOURCE_CHANNEL_ID = os.environ.get("ALTCOIN_SOURCE_CHANNEL_ID", "").strip()
PUBLIC_CHANNEL_LINK = os.environ.get("PUBLIC_CHANNEL_LINK", "")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "")  # مثلا @your_support_username
ADMIN_USER_IDS = {int(x) for x in os.environ.get("ADMIN_USER_IDS", "").replace(" ", "").split(",") if x}
REFERRAL_REWARD_DAYS = int(os.environ.get("REFERRAL_REWARD_DAYS") or "7")
PHOTO_REWARD_DAYS = int(os.environ.get("PHOTO_REWARD_DAYS") or "1")
MAX_PHOTO_REWARDS_PER_MONTH = int(os.environ.get("MAX_PHOTO_REWARDS_PER_MONTH") or "5")

WALLET_TRC20 = os.environ["WALLET_ADDRESS_TRC20"]
WALLET_BEP20 = os.environ.get("WALLET_ADDRESS_BEP20", "")
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY", "")

PAYMENT_WINDOW_MINUTES = int(os.environ.get("PAYMENT_WINDOW_MINUTES") or "45")

LONG_POLL_SECONDS = 5                                    # هر getUpdates حداکثر ۵ ثانیه صبر می‌کنه - پاسخ‌گویی خیلی سریع‌تر از قبل (۲۵ ثانیه)
LOOP_MAX_SECONDS = int(os.environ.get("LOOP_MAX_SECONDS") or str(5 * 3600 + 20 * 60))  # ۵ ساعت و ۲۰ دقیقه پیش‌فرض
GIT_COMMIT_EVERY_SECONDS = 45     # 🔴 قبلاً ۱۲۰ ثانیه بود - این دقیقاً همون چیزیه که باعث شد
                                   # یک سیگنال/نتیجه‌ی سریع (باز و بسته‌شدن ظرف چند دقیقه)
                                   # هیچ‌وقت به candle_engine.py نرسه: manual_signals.json و
                                   # forwarded_results_queue.json هر دو اینجا (subscription_bot.py)
                                   # نوشته می‌شن، ولی تا این فاصله سپری نشه push نمی‌شدن. یعنی
                                   # تاخیر واقعیِ رفت‌وبرگشت تا candle_engine.py: تا ۱۲۰ (اینجا) +
                                   # ۴۵ (pull candle_engine.py) = ۱۶۵ ثانیه بدترین حالت - درحالی‌که
                                   # process_forwarded_results فقط ۸ تلاش (~۴۰ ثانیه، توضیح در
                                   # FORWARDED_RESULT_MAX_ATTEMPTS) صبر می‌کرد قبل از تسلیم‌شدن؛
                                   # برای یک معامله‌ی سریع، پیام نتیجه راحت زودتر از خودِ سیگنال
                                   # ورودی می‌رسید و هیچ‌وقت جفتش پیدا نمی‌شد. الان با ۴۵ ثانیه
                                   # (دقیقاً هم‌قدم candle_engine.py)، بدترین حالت ~۹۰ ثانیه می‌شه.
PAYMENT_CHECK_INTERVAL_SECONDS = 20   # چک پرداخت‌ها (TronGrid/BscScan) هر ۲۰ ثانیه، نه هر چرخه - تا پاسخ به پیام کاربر معطل درخواست‌های خارجی نشه
LIFECYCLE_CHECK_INTERVAL_SECONDS = 30  # چک انقضای اشتراک هم همین‌طور، جدا از پاسخ‌دهی پیام

# ⚠️ همون «dead man's switch» که candle_engine.py دارد (توضیح کامل آنجا، HEALTHCHECK_URL_ENGINE)،
# اینجا هم با یک آدرس جداگانه (HEALTHCHECK_URL_BOT) - عمداً یک secret متفاوت، نه همون یکی -
# چون این دو پروسه کاملاً مستقل می‌تونن از هم بمیرن (مثلاً subscription_bot.py کرش کنه ولی
# candle_engine.py سالم باشه، یا برعکس) و ادمین باید بتونه دقیقاً بفهمه کدوم یکی مشکل داره،
# نه فقط «یکی از این دو ربات».
HEALTHCHECK_URL_BOT = os.getenv("HEALTHCHECK_URL_BOT", "").strip()


def send_heartbeat():
    if not HEALTHCHECK_URL_BOT:
        return
    try:
        requests.get(HEALTHCHECK_URL_BOT, timeout=10)
    except Exception:
        pass


# ================== پلن‌ها — قیمت کمی زیر میانگین بازار، با تخفیف پلکانی برای تشویق اشتراک بلندمدت ==================
PLANS = {
    "1m":  {"label": "1 Month",   "days": 30,  "usd": 39},
    "3m":  {"label": "3 Months",  "days": 90,  "usd": 99},   # ~15% off monthly rate
    "6m":  {"label": "6 Months",  "days": 180, "usd": 180},  # ~23% off
    "12m": {"label": "12 Months", "days": 365, "usd": 300},  # ~36% off
}

# 🔴 قیمت‌های بالا فقط مقادیر پیش‌فرضِ اولیه‌ان - طبق درخواست («بتونم مقدار مبلغ خرید
# اشتراک‌ها رو تغییر بدم»)، قیمت واقعی هر پلن حالا از data/plan_prices.json (اگه وجود
# داشته باشه) رو‌نویسی می‌شه، و دستور /admin_set_price اون فایل رو آپدیت می‌کنه. عمداً
# ساختار/تعداد روز هر پلن (days) رو دست‌نخورده نگه داشتیم - فقط usd قابل‌تغییره - چون
# تغییر تعداد روزهای یک پلن موجود، اشتراک‌های از قبل خریداری‌شده رو گیج‌کننده می‌کنه؛ برای
# پلن جدید/متفاوت، ساختن یک plan_key جدید در همین دیکشنری (با ادیت مستقیم کد) درست‌تره.
def _apply_plan_price_overrides():
    overrides = _load("plan_prices.json", {})
    for plan_key, usd in overrides.items():
        if plan_key in PLANS:
            PLANS[plan_key]["usd"] = usd

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"

NETWORKS = {
    "trc20": {"label": "USDT (TRC20 - Tron)", "wallet": WALLET_TRC20, "decimals": 6},
    "bep20": {"label": "USDT (BEP20 - BNB Chain)", "wallet": WALLET_BEP20, "decimals": 18},
}

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

WELCOME_TEXT = (
    "👋 <b>Welcome to the Signal Room</b>\n\n"
    "Rule-based entries on <b>BTC &amp; ETH</b> — every timeframe, 1-minute to daily.\n"
    "Every call includes entry, stop-loss, 4 profit targets, and a chart.\n\n"
    "✅ Every signal generated gets posted — wins and losses alike\n"
    f"{('👀 Free samples: ' + PUBLIC_CHANNEL_LINK) if PUBLIC_CHANNEL_LINK else ''}\n\n"
    "New here? Try /guide for a 2-minute walkthrough of how to read and follow a signal.\n\n"
    "Use the buttons below to get started 👇"
)

# ================== راهنمای تعاملیِ ورود (/guide) ==================
#
# طبق پیشنهاد onboarding بهتر: قبلاً برای یک مشترک تازه، هیچ راهنمای گام‌به‌گامی وجود نداشت
# که واقعاً یاد بده «این پیام یعنی چی، باید چیکار کنم» - فقط یک پیام خوش‌آمد و لیست دکمه بود.
# این یک راهنمای ۳-قدمیِ تعاملی (با دکمه‌ی Next/Back) است، با یک مثال واقعی از سیستم
# RiskRivard خودِ کانال (نه یک توضیح انتزاعی) - تا کاربر جدید دقیقاً بفهمه یک سیگنال واقعی
# رو چطور بخونه و دنبال کنه.
GUIDE_STEPS = {
    1: (
        "📖 <b>Guide (1/3) — Reading a signal</b>\n\n"
        "Every signal looks like this:\n\n"
        "🟢 LONG — BTC 4H\n"
        "Entry: <b>65,000</b>\n"
        "❌ Stop: <b>63,700</b>\n"
        "🎯 Target 1: 66,300  🎯 Target 2: 67,600\n"
        "🎯 Target 3: 70,200  🎯 Target 4: 72,800\n\n"
        "<b>Entry</b> — the price the setup triggered at.\n"
        "<b>Stop</b> — if price hits this, the trade is closed for a loss (this defines your risk, "
        "called <b>1R</b>).\n"
        "<b>Targets</b> — 1R, 2R, 4R, and 6R away from entry. Each one bank a portion of the position "
        "(see step 2)."
    ),
    2: (
        "📖 <b>Guide (2/3) — Position sizing (the \"R\" system)</b>\n\n"
        "You never close 100% at one target — the position scales out gradually:\n\n"
        "🎯 Target 1 (1R): close 20%\n"
        "🎯 Target 2 (2R): close 30%  ·  stop moves to Target 1\n"
        "🎯 Target 3 (4R): close 15%  ·  stop moves to Target 2\n"
        "🎯 Target 4 (6R): close 10%  ·  stop moves to Target 3\n"
        "🏁 Final 25%: rides a trailing stop (1.5R behind the peak) for as long as the move continues\n\n"
        "This is why a signal can still end in a small profit even if price eventually reverses after "
        "hitting a target or two — some of the position is already banked, and the stop has moved up "
        "behind it."
    ),
    3: (
        "📖 <b>Guide (3/3) — Tracking your signals</b>\n\n"
        "/results — overall win rate, R total, drawdown, streaks\n"
        "/equity — visual curve of cumulative R over time\n"
        "/history — every closed signal, individually\n"
        "/signal TC-004821 — full timeline of one specific signal (shown as a #hashtag in its channel message; "
        "works for TC- or ALT- ids alike)\n"
        "/setrisk 1000 2 — save your account size + risk % once, then /pnl always shows your real $ numbers\n\n"
        "Every result (win or loss) gets posted in the channel as a reply to the original signal — "
        "nothing is ever cherry-picked or hidden."
    ),
}


def guide_keyboard(step: int):
    nav = []
    if step > 1:
        nav.append({"text": "⬅️ Back", "callback_data": f"guide:{step-1}"})
    if step < 3:
        nav.append({"text": "Next ➡️", "callback_data": f"guide:{step+1}"})
    rows = [nav] if nav else []
    rows.append([{"text": "✅ Got it — take me to the menu", "callback_data": "menu:home"}])
    return rows

HELP_TEXT = (
    "<b>Commands</b>\n\n"
    "/guide — 2-minute walkthrough of how to read and follow a signal\n"
    "/plans — view plans &amp; subscribe\n"
    "/status — your subscription status\n"
    "/results — signal performance stats\n"
    "/history — last 100 closed signals, in detail\n"
    "/equity — visual equity curve (cumulative R over time)\n"
    "/signal ID — full timeline of one specific signal (e.g. /signal TC-004821)\n"
    "/pnl AMOUNT — estimate your P&amp;L (e.g. /pnl 100)\n"
    "/setrisk BALANCE PERCENT — save your risk profile so /pnl (no args) uses your real numbers\n"
    "/code CODE — apply a discount code\n"
    "/refer — get your referral link\n"
    "/support — contact support\n"
    "/disclaimer — risk disclaimer\n"
    "/cancel — cancel your membership\n"
    "/help — this message"
)

DISCLAIMER_TEXT = (
    "⚠️ <b>Risk Disclaimer</b>\n\n"
    "Signals shared here are for educational and informational purposes only and are "
    "<b>not financial advice</b>. Trading cryptocurrencies and other assets involves "
    "substantial risk of loss and is not suitable for everyone.\n\n"
    "Past performance (including the stats shown in /results) does not guarantee future "
    "results. Always do your own research and never risk more than you can afford to lose.\n\n"
    "By using this service you acknowledge that you are solely responsible for your own "
    "trading decisions.\n\n"
    "<b>📏 Position sizing — read this before you take a signal</b>\n\n"
    "• Risk <b>3–5% of your account margin per trade</b>, max. Never more.\n"
    "• Pick your leverage so that a full stop-loss hit costs you at most that 3–5% — not "
    "your whole position. Formula:\n"
    "   <code>Margin to use = (Account × Risk%) / (Stop distance % × Leverage)</code>\n"
    "• Never enter a signal with your entire balance or a large chunk of it, even if it "
    "\"looks like a sure thing\" — one signal is one trade, not your whole strategy.\n"
    "• Every target message tells you what % of the position to close — follow it; it's "
    "how the stated R:R and win-rate numbers actually apply to you.\n\n"
    "This applies to every signal we post, automatic or manual/forwarded, on every "
    "timeframe — no exceptions."
)


def support_text():
    if SUPPORT_CONTACT:
        return f"💬 <b>Need help?</b>\n\nContact support: {SUPPORT_CONTACT}"
    return "💬 <b>Need help?</b>\n\nMessage the channel admin directly for support."


def main_menu_keyboard():
    # 🔴 طبق درخواست صریح: دکمه‌ی راهنما («📖 Guide: How signals work») از اولین گزینه به
    # آخرین گزینه‌ی منو منتقل شد - قبلاً همیشه اولین چیزی بود که کاربر (حتی مشترکِ باتجربه که
    # فقط برای چک‌کردن وضعیت اشتراکش اومده) می‌دید؛ گزینه‌های عملی‌تر (پلن‌ها، وضعیت اشتراک،
    # نتایج) رو به جلو منتقل کردیم تا اول در معرض دید باشن، و راهنما (که بیشتر برای کاربر
    # تازه‌واردِ اولین‌بار مفیده) آخر لیست بمونه - همچنان در دسترس، فقط نه اول از همه.
    return [
        [{"text": "💎 View Plans", "callback_data": "menu:plans"}],
        [{"text": "📊 My Subscription", "callback_data": "menu:status"}],
        [{"text": "📈 Signal Results", "callback_data": "menu:results"}, {"text": "🧮 Calculate P&L", "callback_data": "menu:pnl"}],
        [{"text": "📜 Signal History", "callback_data": "hist:0"}, {"text": "📈 Equity Curve", "callback_data": "menu:equity"}],
        [{"text": "🤝 Refer a Friend", "callback_data": "menu:refer"}],
        [{"text": "💬 Support", "callback_data": "menu:support"}, {"text": "⚠️ Disclaimer", "callback_data": "menu:disclaimer"}],
        [{"text": "📖 Guide: How signals work", "callback_data": "guide:1"}],
    ]


# 🔴 منوی پایینِ نوار پیام (دکمه‌ی ☰ کنار جعبه‌ی تایپ - همون چیزی که در تصویر خواسته شده
# بود): این جدا از main_menu_keyboard بالاست - آن یک inline keyboard زیر یک پیام خاصه،
# این یکی سراسری‌ست (توسط خودِ تلگرام، setChatMenuButton) و همیشه در دسترسه، حتی وسط
# تایپ‌کردن. فقط دستورهای کاربریِ عادی اینجا لیست می‌شن (نه دستورهای /admin_* که فقط برای
# ادمین‌هاست - نشون‌دادنشون به همه‌ی کاربران هم گمراه‌کننده‌ست هم کمی نگران‌کننده).
BOT_COMMANDS_MENU = [
    {"command": "start", "description": "Open the main menu"},
    {"command": "guide", "description": "2-minute walkthrough of how to read a signal"},
    {"command": "plans", "description": "View subscription plans & buy"},
    {"command": "status", "description": "Check your subscription status"},
    {"command": "results", "description": "Signal performance stats"},
    {"command": "history", "description": "Last 100 closed signals, in detail"},
    {"command": "equity", "description": "Visual equity curve (cumulative R over time)"},
    {"command": "signal", "description": "Full timeline of one signal — /signal TC-004821"},
    {"command": "pnl", "description": "Estimate your P&L — e.g. /pnl 100"},
    {"command": "setrisk", "description": "Save your risk profile for /pnl — /setrisk 1000 2"},
    {"command": "refer", "description": "Get your referral link"},
    {"command": "support", "description": "Contact support"},
    {"command": "disclaimer", "description": "Risk disclaimer"},
    {"command": "help", "description": "Show all commands"},
]


def setup_bot_menu():
    """با setMyCommands لیست دستورها رو ثبت می‌کنه و با setChatMenuButton دکمه‌ی ☰ رو کنار
    جعبه‌ی تایپ فعال می‌کنه (type: commands - یعنی زدنش همین لیست رو نشون می‌ده). idempotent
    و بی‌خطره که هر بار روشن‌شدنِ ربات (هر ~۵ ساعت و ۲۰ دقیقه) دوباره صدا زده بشه - تلگرام
    فقط مقدار رو به‌روز می‌کنه، خطایی نمی‌ده. best-effort: اگه شکست بخوره (مثلاً یک مشکل
    موقت شبکه‌ی همون لحظه‌ی startup)، فقط لاگ می‌شه و ربات عادی ادامه می‌ده - نبودِ این منو
    نباید جلوی کارکردِ اصلی رو بگیره."""
    try:
        tg("setMyCommands", commands=BOT_COMMANDS_MENU)
        tg("setChatMenuButton", menu_button={"type": "commands"})
        print("✅ Bot menu button configured")
    except Exception as e:
        print(f"[warn] setup_bot_menu failed (non-fatal, bot continues normally): {e}")

# ================== توابع کمکی JSON ==================

def _load(name, default):
    # 🔴 هم‌راستا با رفعِ candle_engine.py: قبلاً یک `json.load` خام بود - اگه فایل به هر
    # دلیلی (کشته‌شدن پردازش وسط نوشتنِ قبلی، قبل از این‌که _save هم atomic بشه) خراب
    # می‌بود، این تابع کرش می‌کرد و اون کرش تا هر جا که caughtش می‌کرد بالا می‌رفت - در
    # بدترین حالت (اگه caller خودش try/except نداشت) کل run_cycle رو می‌ترکوند. الان با
    # read_json_resilient: فایل خراب کنار گذاشته می‌شه (نه از دست می‌ره، نه بازنویسی)،
    # ادمین‌ها هشدار می‌گیرن، و به‌جای کرش یک default امن برمی‌گرده.
    path = os.path.join(DATA_DIR, name)
    return read_json_resilient(path, default, label=name,
                                on_corrupt=lambda label, msg: alert_admins_text(f"⚠️ {label}: {msg}"))


def _save(name, data):
    # 🔴 رفعِ یکی از دلایل واقعیِ «گاهی ربات از کار می‌افته»: قبلاً نوشتنِ مستقیم
    # (open(..., "w") + json.dump) atomic نبود - اگه پردازش دقیقاً وسط نوشتن کشته می‌شد
    # (مثلاً timeout کلِ Job در GitHub Actions یا لغو دستی)، فایل روی دیسک نصفه/خراب
    # می‌موند و دفعه‌ی بعد خواندنش (توسط _load بالا) کرش/از‌دست‌رفتنِ داده به‌همراه داشت.
    # الان atomic_write_json: می‌نویسه روی یک فایل موقت، بعد با os.replace (atomic در سطح
    # سیستم‌عامل) جایگزین می‌کنه - یا فایل قبلی کامل می‌مونه، یا فایل جدید کامل می‌شینه
    # جاش، هیچ‌وقت حالت نصفه ممکن نیست.
    atomic_write_json(os.path.join(DATA_DIR, name), data)


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def fmt_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def days_remaining(expiry_ts):
    seconds = expiry_ts - now_ts()
    return max(0, seconds // (24 * 3600))


# ================== آمار عملکرد سیگنال‌ها (مشترک با candle_engine.py) ==================

def _ensure_fresh_data() -> None:
    """قبل از پاسخ به /results یا /pnl، یک pull سبک و فوری (بدون commit/push) انجام می‌ده
    تا کاربر همیشه آخرین وضعیتِ موجود روی ریموت را ببینه، نه نسخه‌ای که تصادفاً آخرین بار
    چرخه‌ی دوره‌ای ۱۲۰ثانیه‌ای به‌روزش کرده (توضیح کامل در docstring خودِ
    pull_latest_readonly در shared_git_sync.py). best-effort - اگه شکست بخوره (شبکه/تعارض
    گذرا)، بی‌سروصدا با هر چی روی دیسک هست ادامه می‌دیم؛ بهتر از معطل‌کردن کاربر با خطا."""
    try:
        pull_latest_readonly(os.path.dirname(os.path.abspath(__file__)))
    except Exception as e:
        print(f"[warn] pre-/results pull failed (serving last-known-local data): {e}")


def load_trade_history():
    return _load("trade_history.json", [])


def compute_stats(history):
    if not history:
        return None
    total = len(history)
    wins = sum(1 for h in history if h["final_r"] > 0)
    breakeven = sum(1 for h in history if h["final_r"] == 0)
    losses = sum(1 for h in history if h["final_r"] < 0)
    total_r = sum(h["final_r"] for h in history)

    # 🔴 اضافه شد: سه آماری که معامله‌گرهای جدی واقعاً می‌پرسند ولی قبلاً اصلاً محاسبه نمی‌شد -
    # بدونشون /results فقط win-rate و R کل رو نشون می‌داد که به‌تنهایی می‌تونه گمراه‌کننده
    # باشه (مثلاً یک win-rate بالا با یک drawdown عمیق در وسط راه، یا یک رشته‌ی باخت طولانی
    # که در میانگین‌ها گم می‌شه).
    #
    # max_drawdown_r: بزرگ‌ترین افتِ تجمعیِ R از یک قله تا پایین‌ترین نقطه‌ی بعدش - با ترتیب
    # زمانیِ واقعیِ بسته‌شدن معاملات (closed_at)، نه ترتیب فهرست (که ممکنه از چند نماد/تایم‌فریم
    # به‌هم‌ریخته باشه).
    ordered = sorted(history, key=lambda h: h.get("closed_at") or "")
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for h in ordered:
        cum += h["final_r"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # longest_losing_streak: طولانی‌ترین رشته‌ی معاملاتِ متوالیِ منفی (breakeven رشته رو
    # پاره نمی‌کنه ولی رشته رو هم ادامه نمی‌ده - خنثی حساب می‌شه، نه برد نه باخت).
    longest_losing_streak = 0
    cur_streak = 0
    for h in ordered:
        if h["final_r"] < 0:
            cur_streak += 1
            longest_losing_streak = max(longest_losing_streak, cur_streak)
        elif h["final_r"] > 0:
            cur_streak = 0
        # breakeven: cur_streak دست‌نخورده می‌مونه

    # avg_duration_hours: میانگین فاصله‌ی opened_at تا closed_at - فقط برای رکوردهایی که
    # هر دو فیلد رو دارن (رکوردهای خیلی قدیمی‌تر ممکنه opened_at نداشته باشن، نادیده گرفته می‌شن
    # نه اینکه صفر حساب بشن و میانگین رو کاذب پایین بیارن).
    durations = []
    for h in history:
        try:
            o = datetime.fromisoformat((h.get("opened_at") or "").replace("Z", "+00:00"))
            c = datetime.fromisoformat((h.get("closed_at") or "").replace("Z", "+00:00"))
            durations.append((c - o).total_seconds() / 3600.0)
        except Exception:
            continue
    avg_duration_hours = (sum(durations) / len(durations)) if durations else None

    return {
        "total": total, "wins": wins, "breakeven": breakeven, "losses": losses,
        "total_r": total_r, "avg_r": total_r / total, "win_rate": wins / total * 100,
        "max_drawdown_r": max_dd, "longest_losing_streak": longest_losing_streak,
        "avg_duration_hours": avg_duration_hours,
    }


def compute_stats_by_group(history, key):
    """آمار رو بر اساس یک فیلد (symbol یا tf) دسته‌بندی می‌کنه."""
    groups = {}
    for h in history:
        groups.setdefault(h.get(key, "?"), []).append(h)
    return {name: compute_stats(items) for name, items in groups.items()}


# ================== وضعیت زنده‌ی معاملات باز (RiskRivard) ==================
# ⚠️ این وزن‌ها و منطق _live_r قبلاً اینجا دستی تکرار شده بودن (با یک کامنت هشدار که می‌گفت
# «باید همیشه دستی با candle_engine.py هماهنگ نگهش داری») - حالا از shared_risk_config.py
# (بدون وابستگی سنگین pandas/matplotlib، فقط Python خالص - پس نصب requests کافیه) import
# می‌شن، پس دیگه امکان drift بین دو اسکریپت نیست.
from shared_risk_config import RR_TARGETS as _RR_TARGETS, compute_final_r as _live_r, VALID_TIMEFRAME_LABELS


def _effective_tf(h):
    """تایم‌فریم واقعی یک معامله (چه باز چه بسته) برای نمایش/گروه‌بندی: اگه tf واقعی
    (نه "manual") موجوده همون، وگرنه logical_tf (تایم‌فریمی که خودِ منبع در متن سیگنال
    اعلام کرده و candle_engine.py هنگام پردازش تشخیص داده) - و اگه هیچ‌کدوم نبود None
    (یعنی واقعاً نامعلومه، نه این‌که فراموش شده باشه نمایش داده بشه).

    🔴 قبلاً format_open_trades_section این تابع مشترک رو استفاده نمی‌کرد و مستقیم فقط
    t.get("tf_key") رو چک می‌کرد - که برای همه‌ی معاملاتِ دستی/رله‌شده (یعنی عملاً همه‌ی
    سیگنال‌های آلتکوین) همیشه دقیقاً "manual" است، حتی وقتی logical_tf درست تشخیص داده
    شده بود. یعنی بخش «Open right now» برای هر معامله‌ی آلتکوین/رله‌شده - نه فقط بعضی -
    همیشه بدون تایم‌فریم نمایش داده می‌شد، در حالی که جدول «By timeframe» (که از همین
    ابتدا از logical_tf استفاده می‌کرد) برای همون معاملات، بعد از بسته‌شدن، درست نشون
    می‌داد. حالا هر دو از یک منبع واحد (همین تابع) می‌خونن - دیگه امکان این ناهماهنگی
    بین معاملات باز/بسته نیست."""
    tf = h.get("tf") or h.get("tf_key")
    if tf and tf != "manual":
        return tf
    return h.get("logical_tf") or None


def load_open_trades():
    """candle_state.json رو (که candle_engine.py هر چند دقیقه commit/push می‌کنه) می‌خونه و
    لیست معاملات هنوز-بازِ در حال ردیابی رو برمی‌گردونه - برای نمایش زنده‌ی «تا کدوم تارگت
    رسیده» قبل از این‌که اصلاً بسته بشن (که تازه اون‌موقع وارد trade_history.json می‌شن)."""
    state = _load("candle_state.json", {})
    combos = state.get("candle_signals", {})
    open_trades = []
    for key, s in combos.items():
        t = s.get("open_trade")
        if t and not t.get("closed"):
            open_trades.append((key, t))
    return open_trades


def format_open_trades_section():
    open_trades = load_open_trades()
    if not open_trades:
        return "🔵 <b>Open right now:</b> none"
    lines = ["🔵 <b>Open right now</b>"]
    for key, t in open_trades:
        arrow = "🟢" if t["side"] == "BUY" else "🔴"
        eff_tf = _effective_tf(t)
        symbol_part = f"{t.get('symbol', '?')} · {eff_tf}" if eff_tf else t.get("symbol", "?")
        hit_labels = [f"T{i+1}" for i, lvl in enumerate(_RR_TARGETS) if t.get("hit", {}).get(str(lvl))]
        hit_part = "/".join(hit_labels) + " hit" if hit_labels else "no target hit yet"
        runner = " · Runner trailing" if t.get("trailing") else ""
        r = _live_r(t)
        # 🔴 اضافه شد: signal_id (مثلاً «TC-004821») - همون شناسه‌ای که در پیام کانال و
        # /admin_close_trade هم دیده می‌شه، تا بشه دقیقاً همین ردیف رو با /signal <id> از
        # همین‌جا هم لوکاپ کرد.
        sid = f" [{t.get('signal_id')}]" if t.get("signal_id") else ""
        lines.append(f"  {arrow} {symbol_part}{sid} — {hit_part}{runner} — banked so far: {r:+.2f}R")
    return "\n".join(lines)


def format_results_message():
    history = load_trade_history()
    stats = compute_stats(history)
    if not stats:
        return "📊 No closed signals yet — check back soon!\n\n" + format_open_trades_section()

    by_symbol = compute_stats_by_group(history, "symbol")
    symbol_lines = []
    for name in sorted(by_symbol):
        s = by_symbol[name]
        symbol_lines.append(f"  {name}: {s['total']} trades · {s['win_rate']:.0f}% win · {s['total_r']:+.1f}R")

    # ⚠️ قبلاً هر معامله‌ای با tf=="manual" کلاً از این جدول حذف می‌شد - چون سیگنال‌های
    # دستی/فوروارد‌شده تایم‌فریم واقعی روی فیلد tf نداشتن (همیشه "manual" ذخیره می‌شد، برای
    # این‌که در «By symbol»/آمار کلی دست‌نخورده بمونن). ولی از وقتی رله‌ی خودکار کانال
    # آلتکوین (handle_altcoin_relay_post) فعال شده، این عملاً حجم غالبِ سیگنال‌هاست - یعنی
    # تقریباً همه‌ی معاملات بسته‌شده tf=="manual" داشتن و این جدول همیشه خالی می‌موند (علت
    # گزارش «By timeframe هیچی نشون نمی‌ده»). الان candle_engine.py تایم‌فریمِ واقعیِ
    # اعلام‌شده در خودِ پیام منبع رو هم روی logical_tf ذخیره می‌کنه (مثلاً "1m" برای یک
    # سیگنال «1M») - پس اینجا برای معاملات manual، به‌جای حذف کامل، از logical_tf به‌عنوان
    # جایگزین استفاده می‌کنیم (تابع مشترک _effective_tf بالاتر - همون که format_open_trades_section
    # هم استفاده می‌کنه)؛ فقط وقتی واقعاً هیچ تایم‌فریمی معلوم نیست (نه tf نه logical_tf -
    # مثلاً یک سیگنال دستیِ صرف از دستور /admin_manual_signal بدون هیچ برچسبی) کامل از این
    # breakdown خاص کنار گذاشته می‌شه. رکوردهای قدیمی‌تر که logical_tf ندارن هم به همین شکل
    # (بدون تایم‌فریم مشخص) کنار گذاشته می‌شن - رفتار قبلی برای اون‌ها عوض نشده.
    tf_history = [h for h in history if _effective_tf(h)]
    by_tf = {}
    for h in tf_history:
        by_tf.setdefault(_effective_tf(h), []).append(h)
    by_tf = {name: compute_stats(items) for name, items in by_tf.items()}
    tf_order = ["1m", "5m", "15m", "1h", "4h"]
    tf_lines = []
    for name in sorted(by_tf, key=lambda x: tf_order.index(x) if x in tf_order else 99):
        s = by_tf[name]
        tf_lines.append(f"  {name}: {s['total']} trades · {s['win_rate']:.0f}% win · {s['total_r']:+.1f}R")
    if not tf_lines:
        tf_lines = ["  —"]

    # ⚠️ طبق درخواست شما، بخش «Open right now» (نتایج باز) از وسط پیام به انتهای پیام منتقل
    # شد - یعنی اول آمار نتایج بسته‌شده (Wins/Losses/Total/By symbol/By timeframe) رو می‌بینی،
    # و معاملات هنوز-باز آخر از همه میان.
    # 🔴 اضافه شد: سه معیار ریسک/دوامِ واقعی - نه فقط win-rate/میانگین. drawdown و رشته‌ی
    # باخت به‌خصوص چیزیه که کاربرهای جدی‌تر می‌پرسن و قبلاً هیچ‌جا نشون داده نمی‌شد.
    duration_line = (f"Avg time open: {stats['avg_duration_hours']:.1f}h\n"
                      if stats.get("avg_duration_hours") is not None else "")
    risk_lines = (
        f"Max drawdown: <b>{stats['max_drawdown_r']:.2f}R</b>\n"
        f"Longest losing streak: {stats['longest_losing_streak']} trade"
        f"{'s' if stats['longest_losing_streak'] != 1 else ''}\n"
        f"{duration_line}\n"
    )

    return (
        f"📊 <b>Signal Performance</b>\n"
        f"<i>Last {stats['total']} closed trades — every signal we've posted, no cherry-picking</i>\n\n"
        f"✅ Wins: {stats['wins']} ({stats['win_rate']:.0f}%)\n"
        f"⚪ Breakeven: {stats['breakeven']}\n"
        f"❌ Losses: {stats['losses']}\n\n"
        f"Total: <b>{stats['total_r']:+.1f}R</b>\n"
        f"Average: <b>{stats['avg_r']:+.2f}R</b> per trade\n\n"
        f"{risk_lines}"
        f"<b>By symbol</b>\n" + "\n".join(symbol_lines) + "\n\n"
        f"<b>By timeframe</b>\n" + "\n".join(tf_lines) + "\n\n"
        f"{format_open_trades_section()}"
    )


HISTORY_PAGE_SIZE = 10
HISTORY_MAX_TRADES = 100  # طبق درخواست: حداقل ۱۰۰ سیگنال آخر باید با جزییات در دسترس باشه


def _fmt_history_closed_at(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso_str or "?"


def format_history_page(page: int = 0):
    """۱۰۰ سیگنال بسته‌شده‌ی آخر (جدیدترین اول) رو صفحه‌به‌صفحه (۱۰تا در هر صفحه) با جزییات
    کامل نشون می‌ده - برخلاف /results که فقط آمار تجمیعی است، اینجا خودِ رکورد هر معامله
    (نماد/جهت/ورود/نتیجه‌ی نهایی/چرا بسته شد/چه زمانی) دیده می‌شه."""
    history = load_trade_history()
    recent = list(reversed(history[-HISTORY_MAX_TRADES:]))  # جدیدترین اول
    if not recent:
        return "📜 No closed signals yet — check back soon!", 1

    total_pages = max(1, (len(recent) + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = recent[page * HISTORY_PAGE_SIZE: (page + 1) * HISTORY_PAGE_SIZE]

    lines = [
        f"📜 <b>Signal History</b>\n"
        f"<i>Last {len(recent)} closed trades — page {page + 1}/{total_pages}</i>\n"
    ]
    for t in chunk:
        arrow = "🟢" if t.get("side") == "BUY" else "🔴"
        eff_tf = _effective_tf(t)
        symbol_part = f"{t.get('symbol', '?')} · {eff_tf}" if eff_tf else t.get("symbol", "?")
        r = t.get("final_r", 0.0)
        r_icon = "✅" if r > 0 else ("⚪" if r == 0 else "❌")
        targets = ", ".join(t.get("targets_hit") or []) or "none"
        # 🔴 اضافه شد: signal_id در عنوان هر ردیف - رکوردهای خیلی قدیمی (قبل از این فیچر)
        # این فیلد رو ندارن، پس اگه نبود چیزی اضافه نمی‌شه (بدون کرش/رشته‌ی «None»).
        sid = f" · {t.get('signal_id')}" if t.get("signal_id") else ""
        lines.append(
            f"{arrow} <b>{symbol_part}</b> {t.get('side', '?')} @ {t.get('entry', '?')}{sid}\n"
            f"   {r_icon} <b>{r:+.2f}R</b> — {t.get('close_reason', t.get('close_type', '?'))}\n"
            f"   Targets hit: {targets} · {_fmt_history_closed_at(t.get('closed_at', ''))}"
        )
    return "\n\n".join(lines), total_pages


def history_keyboard(page: int, total_pages: int):
    nav = []
    if page > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"hist:{page - 1}"})
    if page < total_pages - 1:
        nav.append({"text": "Next ➡️", "callback_data": f"hist:{page + 1}"})
    rows = []
    if nav:
        rows.append(nav)
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:home"}])
    return rows


def format_pnl_message(risk_amount: float, profile: Optional[Dict[str, Any]] = None):
    history = load_trade_history()
    stats = compute_stats(history)
    if not stats:
        return "📊 No closed signals yet to calculate from — check back soon!"
    total_pnl = stats["total_r"] * risk_amount
    avg_pnl = stats["avg_r"] * risk_amount
    # 🔴 اضافه شد: اگه این عدد از یک پروفایل ریسک ذخیره‌شده (/setrisk) اومده، همون‌جا توضیح
    # می‌ده از کجا اومده - تا کاربر گیج نشه که این $X از کجا پیدا شد.
    source_line = (f"<i>Using your saved risk profile: ${profile['balance']:.0f} balance × "
                    f"{profile['risk_pct']:.1f}% = ${risk_amount:.2f} per trade. Change it anytime "
                    f"with /setrisk.</i>\n\n") if profile else ""
    return (
        f"🧮 <b>P&amp;L Estimate</b> — risking ${risk_amount:.2f} per trade\n\n"
        f"{source_line}"
        f"Based on {stats['total']} closed trades:\n"
        f"Total: <b>${total_pnl:+.2f}</b>\n"
        f"Average per trade: <b>${avg_pnl:+.2f}</b>\n\n"
        f"<i>Assumes you followed the position plan on every signal — 20% at Target 1, 30% "
        f"at Target 2, 15% at Target 3, 10% at Target 4, and the final 25% runner on its "
        f"trailing stop. This is an estimate, not a guarantee of future results.</i>"
    )


def pnl_keyboard(user_id: Optional[int] = None):
    rows = [
        [{"text": "$50", "callback_data": "pnl:50"}, {"text": "$100", "callback_data": "pnl:100"}, {"text": "$200", "callback_data": "pnl:200"}],
    ]
    # 🔴 اضافه شد: اگه این کاربر یک پروفایل ریسک ذخیره‌شده داره (/setrisk)، یک دکمه‌ی
    # اختصاصی هم نشون داده می‌شه - تا هر بار مجبور نباشه دستی /pnl AMOUNT بزنه یا یکی از
    # سه مبلغِ عمومیِ بالا رو انتخاب کنه.
    if user_id is not None:
        profile = _load_risk_profile(user_id)
        if profile:
            amount = profile["balance"] * profile["risk_pct"] / 100
            rows.append([{"text": f"💼 My profile (${amount:.0f}/trade)", "callback_data": "pnl:mine"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:home"}])
    return rows


# ================== پروفایل ریسک شخصی (/setrisk) ==================
#
# طبق پیشنهاد شخصی‌سازیِ P&amp;L: قبلاً هر بار /pnl باید یک مبلغ دلاری دستی داده می‌شد -
# کاربر باید خودش هر بار (ریسک‌به‌ازای‌معامله = موجودی × درصد ریسک) رو محاسبه می‌کرد. حالا
# یک‌بار با /setrisk موجودی و درصد ریسک ذخیره می‌شه، و از اون به بعد /pnl بدون هیچ آرگومانی
# مستقیماً همون عدد شخصی‌سازی‌شده رو نشون می‌ده.
#
# 🔴 چرا یک فایل کاملاً جدا (user_risk_profiles.json)، نه بخشی از subscribers.json: این
# پروفایل به وضعیت اشتراک/پرداخت هیچ ربطی نداره - حتی یک کاربر بدون اشتراک فعال هم باید
# بتونه ازش استفاده کنه (دقیقاً مثل /pnl که همیشه برای همه باز بوده)، و نباید با انقضا یا
# لغو اشتراک پاک بشه.
def _load_risk_profile(user_id: int) -> Optional[Dict[str, float]]:
    profiles = _load("user_risk_profiles.json", {})
    return profiles.get(str(user_id))


def _save_risk_profile(user_id: int, balance: float, risk_pct: float) -> None:
    profiles = _load("user_risk_profiles.json", {})
    profiles[str(user_id)] = {"balance": balance, "risk_pct": risk_pct}
    _save("user_risk_profiles.json", profiles)


# ================== رهگیریِ کامل یک سیگنالِ مشخص با شناسه‌اش (/signal) ==================
#
# طبق درخواست صریح: «هر سیگنال شناسه‌ی خاص خودش را داشته باشد و از زمان صادر شدن تا زمان
# بسته شدن، کامل مسیر حرکتی‌اش ... محاسبه کند». candle_engine.py از این پس هر سیگنال جدید
# (خودکار یا دستی/فوروارد - فرقی نداره) رو با یک signal_id پایدار («TC-004821») و یک
# تاریخچه‌ی کامل رویدادها (trade["events"]: باز شدن، هر تارگت با قیمت/زمان دقیق، بریک‌ایون/
# تریلینگ، و بسته‌شدن نهایی) می‌سازه - این هم در پیام‌های کانال دیده می‌شه و هم عیناً در
# trade_history.json برای همیشه می‌مونه. این تابع دقیقاً همون شناسه رو می‌گیره و - چه
# سیگنال هنوز باز باشه (از candle_state.json) چه قبلاً بسته شده باشه (از trade_history.json)
# - کل مسیر حرکتی رو خط‌به‌خط و با زمان/قیمتِ دقیقِ هر مرحله نشون می‌ده.
TIMELINE_EVENT_LABELS = {
    "opened": "🚀 Opened",
    "rr": None,  # جداگانه با شماره‌ی تارگت ساخته می‌شه (پایین‌تر)
    "breakeven": "⚪ Stopped at breakeven",
    "sl_after_t2": "🔒 Stopped after Target 2 (locked at T1)",
    "sl_after_t3": "🔒 Stopped after Target 3 (locked at T2)",
    "runner_stop": "🏁 Runner closed (trailing stop)",
    "stop": "❌ Stop-loss hit",
    "opposite_signal": "⚠️ Closed — opposite signal replaced it",
    "admin_override": "🛠 Closed manually by admin",
    "target_hit": "🎯 Target hit",
    "closed": None,  # فقط برای final_r استفاده می‌شه، خودش خط جدا نمی‌سازه (پایین‌تر)
}


def _fmt_event_ts(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts_str or "?"


_SIGNAL_ID_PARTS_RE = re.compile(r"^([A-Z]{2,10})[-_]?(\d{1,8})$")


def _normalize_signal_id(raw: str) -> str:
    """اجازه می‌ده کاربر شناسه رو با هر فرمتی بنویسه - «TC-004821»، «tc-4821»، «ALT_5»،
    «alt-000005»، یا فقط «4821» - و به فرمت استاندارد «PREFIX-XXXXXX» نرمالایز می‌شه.

    🔴 رفعِ باگ: قبلاً این تابع هر پیشوندی به‌جز «TC» رو کور می‌کرد - یعنی «ALT-000005» (شناسه‌ی
    واقعیِ سیگنال‌های رله‌شده از کانال آلتکوین، دقیقاً همون چیزی که در متن پیام کانال زیر 🆔
    دیده می‌شه) به‌جای نرمالایز درست، به یک رشته‌ی بی‌معنیِ «TC-ALT-000005» تبدیل می‌شد؛
    یعنی /signal ALT-000005 یا /admin_close_trade ALT-000005 (دقیقاً همون چیزی که ادمین/کاربر
    از روی خودِ پیام کانال کپی/تایپ می‌کنه) همیشه «not found» برمی‌گردوند، حتی وقتی خودِ
    trade["signal_id"] الان دقیقاً همون مقدار رو داره (نگاه کنید open_new_trade::preferred_signal_id
    در candle_engine.py). الان هر پیشوند حروفیِ ۲ تا ۱۰ کاراکتری (TC، ALT، یا هر دیپلوی
    آینده‌ای) شناخته و حفظ می‌شه؛ فقط یک عدد خالی (بدون پیشوند) همچنان پیش‌فرض «TC» می‌گیره -
    چون این دیپلوی خودش TC است، و این رفتار قبلی برای کاربرهایی که فقط عدد رو تایپ می‌کنن حفظ
    می‌شه."""
    raw = (raw or "").strip().upper().replace(" ", "")
    if not raw:
        return ""
    m = _SIGNAL_ID_PARTS_RE.match(raw)
    if m:
        prefix, digits = m.groups()
        return f"{prefix}-{int(digits):06d}"
    raw_digits = raw.lstrip("-_")
    if raw_digits.isdigit():
        return f"TC-{int(raw_digits):06d}"
    return raw


def _queue_admin_close_request(key: str, final_r):
    """صفِ مشترکِ admin_close_requests.json - هم از دستور متنی /admin_close_trade و هم از
    دکمه‌های inline زیر هشدار stale_open_trade (handle_callback::aclt:) استفاده می‌شه، تا
    منطق append/trim/save فقط یک‌جا باشه. final_r=None یعنی «مشخص نشده» - که
    candle_engine.py::process_admin_close_requests طبق طراحیِ جدید، به‌جای صفرِ کورکورانه،
    اون رو با fair-value واقعیِ محاسبه‌شده از پیشرفتِ تارگت‌های واقعاً رسیده پر می‌کنه."""
    requests_ = _load("admin_close_requests.json", [])
    requests_.append({"key": key, "final_r": final_r, "status": "pending",
                       "requested_at": datetime.now(timezone.utc).isoformat()})
    # قبلاً این فایل هیچ سقفی نداشت (برخلاف forwarded_results_queue.json که با [-500:] محدود
    # می‌شه) - رکوردهای واقعاً قدیمی (applied/not_found از هفته‌ها پیش) هیچ ارزشی به‌جز آرشیو
    # ندارن که trade_history.json/گزارش‌ها از قبل پوشش می‌دن، پس همون سقف رو اینجا هم می‌ذاریم.
    requests_ = requests_[-500:]
    _save("admin_close_requests.json", requests_)


# ================== منحنی equity (متنی، بدون وابستگی به matplotlib) ==================
#
# طبق پیشنهاد «نمودار equity curve»: subscription_bot.py عمداً سبک نگه داشته شده (فقط
# requests در requirements.txt - نه pandas/matplotlib/mplfinance که candle_engine.py داره)
# تا نصب هر اجرا سریع بمونه و وزن ایمیج/ریسک وابستگی اضافه نشه. یک sparkline متنی (با
# کاراکترهای بلوکِ یونیکد) تقریباً همون ارزش بصری رو بدون هیچ وابستگی سنگین جدیدی می‌ده -
# اگه در آینده نسخه‌ی تصویری (PNG واقعی) هم لازم شد، می‌شه به candle_engine.py اضافه کرد
# (که این وابستگی‌ها رو از قبل داره) و از اونجا فرستاد.
_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _sparkline(values):
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return _SPARK_CHARS[4] * len(values)
    chars = []
    for v in values:
        idx = int(round((v - lo) / span * (len(_SPARK_CHARS) - 1)))
        chars.append(_SPARK_CHARS[idx])
    return "".join(chars)


EQUITY_CURVE_MAX_POINTS = 100  # بیشتر از این، خودِ sparkline خیلی فشرده و بی‌معنی می‌شه


def format_equity_message() -> str:
    history = load_trade_history()
    if not history:
        return "📈 No closed signals yet — check back soon!"
    ordered = sorted(history, key=lambda h: h.get("closed_at") or "")
    recent = ordered[-EQUITY_CURVE_MAX_POINTS:]
    cum = []
    running = 0.0
    for h in recent:
        running += h["final_r"]
        cum.append(running)
    spark = _sparkline(cum)
    stats = compute_stats(history)
    first_date = (recent[0].get("closed_at") or "?")[:10]
    last_date = (recent[-1].get("closed_at") or "?")[:10]
    return (
        f"📈 <b>Equity Curve</b> — cumulative R, last {len(recent)} closed trades\n"
        f"<i>{first_date} → {last_date}</i>\n\n"
        f"<code>{spark}</code>\n"
        f"Start: 0.00R  →  Now: <b>{cum[-1]:+.2f}R</b>\n\n"
        f"Peak: {max(cum):+.2f}R  ·  Trough: {min(cum):+.2f}R\n"
        f"Max drawdown: {stats['max_drawdown_r']:.2f}R\n"
        f"Longest losing streak: {stats['longest_losing_streak']} trade"
        f"{'s' if stats['longest_losing_streak'] != 1 else ''}\n\n"
        f"<i>Each block is one closed trade's running R total — taller/lighter means higher.</i>"
    )


def find_signal_by_id(signal_id: str):
    """اول بین معاملاتِ هنوز-بازِ candle_state.json می‌گرده (رایج‌ترین حالت - کاربر معمولاً
    دنبال یک سیگنال تازه/در حال اجراست)، اگه پیدا نشد بین ۲۰۰۰ سیگنالِ بسته‌شده‌ی اخیر در
    trade_history.json. برمی‌گردونه: (found_dict, is_open) یا (None, None)."""
    sid = _normalize_signal_id(signal_id)
    # 🔴 گارد کلی‌تر شد (قبلاً فقط sid=="TC-" رو رد می‌کرد - یعنی مخصوص پیشوند TC بود): حالا
    # هر شناسه‌ای که هیچ رقمی نداره (مثلاً فقط «ALT» یا «TC» بدون عدد) نامعتبر در نظر گرفته
    # می‌شه، صرف‌نظر از پیشوندش - چون _normalize_signal_id دیگه به هیچ پیشوند خاصی محدود نیست.
    if not sid or not any(ch.isdigit() for ch in sid):
        return None, None
    for _, t in load_open_trades():
        if t.get("signal_id") == sid:
            return t, True
    for h in reversed(load_trade_history()):
        if h.get("signal_id") == sid:
            return h, False
    return None, None


def _last_git_commit_info() -> str:
    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(["git", "log", "-1", "--format=%cI"], cwd=repo_dir,
                                 capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or not result.stdout.strip():
            return "unknown"
        commit_dt = datetime.fromisoformat(result.stdout.strip())
        age_minutes = (datetime.now(timezone.utc) - commit_dt.astimezone(timezone.utc)).total_seconds() / 60
        return f"{commit_dt.strftime('%Y-%m-%d %H:%M UTC')} ({age_minutes:.0f} min ago)"
    except Exception as e:
        return f"unavailable ({e})"


def format_admin_status_message() -> str:
    """طبق پیشنهاد «/admin_status که وضعیت کلی رو یک‌جا نشون بده»: چند سیگنال باز است، آخرین
    کامیت کِی بوده، امروز چند درصد سهمیه‌ی Twelve Data مصرف شده، و چیزهایی که واقعاً نیاز به
    توجه دارن (درخواست‌های admin_close/forwarded_results که هنوز حل نشدن). چون
    subscription_bot.py و candle_engine.py روی همون یک ریپو/چک‌اوت مشترک کار می‌کنن (هر دو
    از data/ زیر همون DATA_DIR می‌خونن/می‌نویسن)، این دستور مستقیماً فایل‌های
    candle_engine.py رو هم می‌خونه - بدون هیچ ارتباط مستقیم بین دو پروسه، فقط خواندن مشترک."""
    state = _load("candle_state.json", {})
    candle_states = state.get("candle_signals", {})
    open_trades = [(k, s.get("open_trade")) for k, s in candle_states.items() if (s or {}).get("open_trade") and not s["open_trade"].get("closed")]
    now_ms = int(time.time() * 1000)
    stale_count = sum(1 for _, t in open_trades if t.get("opened_at_ms") and (now_ms - t["opened_at_ms"]) > 48 * 3600 * 1000)

    quota = _load("api_quota_state.json", {})
    quota_line = "no data yet today"
    if quota.get("date") == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        used, limit = quota.get("count", 0), 800
        pct = round(100 * used / limit)
        warn = " ⚠️" if pct >= 80 else ""
        quota_line = f"{used}/{limit} ({pct}%){warn}"

    daily_log = _load("daily_report_log.json", [])
    weekly_log = _load("weekly_report_log.json", [])
    last_daily = daily_log[-1] if daily_log else "never"
    last_weekly = weekly_log[-1] if weekly_log else "never"

    admin_close_reqs = _load("admin_close_requests.json", [])
    not_found = sum(1 for r in admin_close_reqs if r.get("status") == "not_found")
    fwd_queue = _load("forwarded_results_queue.json", [])
    unmatched = sum(1 for e in fwd_queue if e.get("status") == "unmatched")

    attention_lines = []
    if stale_count:
        attention_lines.append(f"  ⚠️ {stale_count} open trade(s) stuck &gt;48h — check stale_open_trade alerts")
    if not_found:
        attention_lines.append(f"  ⚠️ {not_found} admin_close_trade request(s) came back 'not found'")
    if unmatched:
        attention_lines.append(f"  ⚠️ {unmatched} forwarded result(s) never matched a trade")
    attention_block = "\n".join(attention_lines) if attention_lines else "  ✅ nothing flagged"

    return (
        f"🖥 <b>System Status</b>\n\n"
        f"Open trades: <b>{len(open_trades)}</b>\n"
        f"Twelve Data quota today: {quota_line}\n"
        f"Last daily report: {last_daily}\n"
        f"Last weekly report: {last_weekly}\n"
        f"Last git commit: {_last_git_commit_info()}\n\n"
        f"<b>Needs attention</b>\n{attention_block}"
    )


def format_signal_lookup_message(signal_id: str) -> str:
    t, is_open = find_signal_by_id(signal_id)
    sid = _normalize_signal_id(signal_id)
    if t is None:
        return (
            f"🔎 No signal found with id <b>{sid}</b>.\n"
            f"Double-check the id from the channel message (the #hashtag below its date/time) or /history."
        )

    arrow = "🟢" if t.get("side") == "BUY" else "🔴"
    eff_tf = _effective_tf(t)
    symbol_part = f"{t.get('symbol', '?')} · {eff_tf}" if eff_tf else t.get("symbol", "?")
    status_line = "🔵 Still open — tracking live" if is_open else "✅ Closed"
    r_now = _live_r(t) if is_open else t.get("final_r", 0.0)

    timeline_lines = []
    for ev in t.get("events", []):
        ts = _fmt_event_ts(ev.get("ts", ""))
        etype = ev.get("type")
        if etype == "rr":
            level = ev.get("level_r")
            price = ev.get("price")
            price_part = f" @ {price}" if price is not None else ""
            timeline_lines.append(f"  🎯 Target hit ({level}R){price_part} — {ts}")
        elif etype == "closed":
            timeline_lines.append(f"  🏁 Final result: <b>{ev.get('final_r', 0.0):+.2f}R</b> — {ts}")
        else:
            label = TIMELINE_EVENT_LABELS.get(etype, f"• {etype}")
            price = ev.get("price")
            price_part = f" @ {price}" if price is not None else ""
            note = f" ({ev['note']})" if ev.get("note") else ""
            timeline_lines.append(f"  {label}{price_part}{note} — {ts}")

    if not timeline_lines:
        timeline_lines = ["  (no detailed timeline recorded for this signal — likely created "
                           "before this tracking feature was added)"]

    return (
        f"🆔 <b>{sid}</b> — {symbol_part} {t.get('side', '?')} {arrow}\n"
        f"{status_line}\n"
        f"Entry: {t.get('entry', '?')}  ·  Stop: {t.get('sl', '?')}\n"
        f"{'Current' if is_open else 'Final'} result: <b>{r_now:+.2f}R</b>\n\n"
        f"<b>Timeline:</b>\n" + "\n".join(timeline_lines)
    )


def format_status_message(subscribers, user_id):
    active = next((s for s in subscribers if s["user_id"] == user_id), None)
    if not active:
        return "❌ <b>No active subscription</b>\n\nType /plans to subscribe."
    remaining = days_remaining(active["expiry_ts"])
    warn = "\n\n⏰ Your subscription ends soon — type /plans to renew and avoid interruption." if remaining <= 3 else ""
    return (
        f"✅ <b>Active subscription</b>\n\n"
        f"Expires: {fmt_date(active['expiry_ts'])}\n"
        f"Days remaining: <b>{remaining}</b>\n\n"
        f"/plans to renew or upgrade  ·  /cancel to end membership"
        f"{warn}"
    )


# ================== توابع تلگرام ==================

# 🔴 رفعِ یکی از واقعی‌ترین ریسک‌های مقیاس («قرار است هزاران کاربر فعال داشته باشد»): این
# تابع تنها نقطه‌ی مشترکیه که هر فراخوانی API تلگرام (send_message، sendPhoto، هر چیز دیگه)
# ازش رد می‌شه. قبلاً هیچ مدیریتی برای HTTP 429 (Too Many Requests) نداشت - `raise_for_status`
# مستقیم exception می‌داد و caller (مثلاً send_message) فقط لاگ می‌کرد و همون پیام رو برای
# همیشه گم می‌کرد. تلگرام محدودیت نرخِ سراسری (~۳۰ پیام/ثانیه به چت‌های مختلف) داره؛ با
# هزاران کاربر، هر عملیات broadcast (مثل /admin_pause یا اطلاع‌رسانیِ گروهیِ تمدید/کاهش
# اشتراک) قطعاً به این سقف می‌خوره. الان: وقتی 429 برگرده، تلگرام خودش در پاسخ
# `retry_after` (ثانیه) رو می‌گه - دقیقاً همون مدت صبر می‌کنیم و دوباره امتحان می‌کنیم (حداکثر
# چند بار)، به‌جای اینکه پیام رو بی‌صدا گم کنیم.
TG_MAX_RETRIES_ON_RATE_LIMIT = 5


def tg(method, **params):
    # هر متد ممکنه پارامتر تلگرامیِ خودش هم اسمش timeout باشه (برای long-polling)،
    # پس timeout درخواست HTTP خودمون رو جدا و همیشه چند ثانیه بیشتر از اون می‌گیریم.
    http_timeout = params.get("timeout", 0) + 10
    for attempt in range(TG_MAX_RETRIES_ON_RATE_LIMIT + 1):
        resp = requests.post(f"{API}/{method}", json=params, timeout=http_timeout)
        if resp.status_code == 429:
            try:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 2)
            except Exception:
                retry_after = 2
            if attempt < TG_MAX_RETRIES_ON_RATE_LIMIT:
                print(f"[rate-limit] {method} got 429 — waiting {retry_after}s before retry "
                      f"({attempt + 1}/{TG_MAX_RETRIES_ON_RATE_LIMIT})")
                time.sleep(retry_after + 0.2)
                continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()  # اگه تمام تلاش‌ها هم 429 بود، خطای واقعی رو بالا بده تا caller بفهمه


# 🔴 برای broadcast به تعداد زیاد کاربر (اطلاع‌رسانیِ گروهی pause/resume، تمدید/کاهش همگانی
# اشتراک، و هر broadcast دیگه‌ای که در آینده اضافه بشه): علاوه بر مدیریت 429 داخل tg() (که
# یک تک‌پیام رو محافظت می‌کنه)، خودِ حلقه‌ی broadcast باید فاصله بگیره تا اصلاً کمتر به سقف
# نرخ بخوره (fاصله‌گرفتنِ پیشگیرانه همیشه بهتر از تکیه‌ی کامل به retry بعد از رد شدنه)، و
# باید یک خلاصه‌ی دقیق از موفق/ناموفق برگردونه - به‌جای اینکه شکست‌های تک‌تک کاربرها کاملاً
# بی‌اثر و نامرئی از دید ادمین گم بشن.
BROADCAST_PACE_SECONDS = 0.05  # ~۲۰ پیام/ثانیه - با فاصله‌ی امن زیر سقف سراسری تلگرام


def broadcast_send(user_ids, text_fn, keyboard=None):
    """text_fn: یا یک رشته‌ی ثابت، یا تابعی که user_id می‌گیره و متن اختصاصی برمی‌گردونه.
    برمی‌گردونه: (sent_count, failed_user_ids) - failed_user_ids برای این‌که caller بتونه
    به ادمین گزارش دقیق بده (مثلاً «مسدود کرده‌ان»/«اکانت حذف شده») نه فقط یک عدد خام."""
    sent, failed = 0, []
    for uid in user_ids:
        text = text_fn(uid) if callable(text_fn) else text_fn
        try:
            tg("sendMessage", chat_id=uid, text=text, parse_mode="HTML",
               **({"reply_markup": {"inline_keyboard": keyboard}} if keyboard else {}))
            sent += 1
        except Exception as e:
            failed.append(uid)
            print(f"[warn] broadcast_send failed for {uid}: {e}")
        time.sleep(BROADCAST_PACE_SECONDS)
    return sent, failed


def send_message(chat_id, text, keyboard=None, reply_to_message_id=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    try:
        tg("sendMessage", **params)
    except Exception as e:
        print(f"[warn] send_message failed for {chat_id}: {e}")


def edit_message(chat_id, message_id, text, keyboard=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        tg("editMessageText", **params)
    except Exception as e:
        print(f"[warn] edit_message failed for {chat_id}: {e}")


def answer_callback(callback_id, text=""):
    try:
        tg("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except Exception as e:
        print(f"[warn] answer_callback failed: {e}")


def create_invite_link(expire_minutes=60):
    expire_date = now_ts() + expire_minutes * 60
    result = tg("createChatInviteLink", chat_id=PRIVATE_CHANNEL_ID, expire_date=expire_date, member_limit=1)
    return result["result"]["invite_link"]


def remove_member(user_id):
    try:
        tg("banChatMember", chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        tg("unbanChatMember", chat_id=PRIVATE_CHANNEL_ID, user_id=user_id, only_if_banned=True)
    except Exception as e:
        print(f"[warn] remove_member failed for {user_id}: {e}")


# ================== بررسی تراکنش‌های ورودی روی هر شبکه ==================

def fetch_trc20_transfers(limit=50):
    if not WALLET_TRC20:
        return []
    url = f"https://api.trongrid.io/v1/accounts/{WALLET_TRC20}/transactions/trc20"
    params = {"limit": limit, "contract_address": USDT_TRC20_CONTRACT, "only_to": "true", "order_by": "block_timestamp,desc"}
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        out = []
        for tx in r.json().get("data", []):
            out.append({
                "tx_id": tx.get("transaction_id"), "to": tx.get("to", "").lower(),
                "value": int(tx.get("value", "0")), "ts": int(tx.get("block_timestamp", 0)) / 1000, "network": "trc20",
            })
        return out
    except Exception as e:
        print(f"[warn] fetch_trc20_transfers failed: {e}")
        return []


def fetch_bep20_transfers(limit=50):
    if not WALLET_BEP20 or not BSCSCAN_API_KEY:
        return []
    url = "https://api.bscscan.com/api"
    params = {
        "module": "account", "action": "tokentx", "contractaddress": USDT_BEP20_CONTRACT,
        "address": WALLET_BEP20, "sort": "desc", "apikey": BSCSCAN_API_KEY, "page": 1, "offset": limit,
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "1":
            return []
        out = []
        for tx in data.get("result", []):
            out.append({
                "tx_id": tx.get("hash"), "to": tx.get("to", "").lower(),
                "value": int(tx.get("value", "0")), "ts": int(tx.get("timeStamp", 0)), "network": "bep20",
            })
        return out
    except Exception as e:
        print(f"[warn] fetch_bep20_transfers failed: {e}")
        return []


def units_to_usdt(value, decimals):
    return value / (10 ** decimals)


def fetch_all_transfers():
    return fetch_trc20_transfers() + fetch_bep20_transfers()


# ================== تخفیف ==================

_BOT_USERNAME_CACHE = {"value": None}


def get_bot_username():
    if _BOT_USERNAME_CACHE["value"]:
        return _BOT_USERNAME_CACHE["value"]
    try:
        result = tg("getMe")
        username = result["result"]["username"]
        _BOT_USERNAME_CACHE["value"] = username
        return username
    except Exception as e:
        print(f"[warn] get_bot_username failed: {e}")
        return None


def referral_text(user_id):
    username = get_bot_username()
    if not username:
        return "🤝 Referral links are temporarily unavailable — please try again in a moment."
    link = f"https://t.me/{username}?start=ref_{user_id}"
    return (
        f"🤝 <b>Refer a Friend</b>\n\n"
        f"Share your link — when a friend you invite makes their <b>first purchase</b>, "
        f"you get <b>{REFERRAL_REWARD_DAYS} free days</b> added to your subscription automatically.\n\n"
        f"Your link:\n<code>{link}</code>"
    )


# ================== حالت تعمیر/توقف موقت ==================

def is_paused(maintenance):
    return bool(maintenance.get("paused"))


def maintenance_notice_text(maintenance):
    reason = maintenance.get("reason") or "a scheduled update"
    return (
        f"⏸️ <b>Signals are temporarily paused</b>\n\n"
        f"We're pausing for: {reason}\n"
        f"Your subscription time is not being used up during this pause — when we resume, "
        f"everyone's subscription will be extended to make up for it.\n\n"
        f"Thanks for your patience!"
    )


def bulk_adjust_subscribers(subscribers, days, note):
    """به تعداد days (می‌تونه منفی هم باشه) به اشتراک همه‌ی کاربران فعال اضافه/کم می‌کنه
    و به هرکدوم پیام اطلاع‌رسانی می‌فرسته. کاربرهایی که با کم‌کردن به زیر صفر برسن حذف می‌شن.

    🔴 برای مقیاس (هزاران کاربر): این حلقه به هر کاربر جدا پیام می‌فرسته (و بعضی‌ها هم
    remove_member که خودش ۲ فراخوانی API دیگه‌ست) - بدون فاصله بین هر کاربر، این broadcast
    قطعاً به سقف نرخ تلگرام می‌خورد. tg() از قبل هر فراخوانی رو در برابر 429 محافظت می‌کنه
    (retry_after)، ولی یک فاصله‌ی کوچک پیشگیرانه‌ی اضافه اینجا هم می‌ذاریم تا اصلاً کمتر به
    اون سقف بخوریم؛ به‌علاوه، دیگه شکست‌های تک‌تک بی‌صدا نیستن - تعداد واقعی failed برگردونده
    می‌شه تا caller (دستورهای /admin_bulk_extend, /admin_bulk_reduce, /admin_resume) بتونه
    دقیق به ادمین گزارش بده."""
    removed = []
    failed_notify = 0
    for s in list(subscribers):
        s["expiry_ts"] += days * 24 * 3600
        try:
            if s["expiry_ts"] <= now_ts():
                remove_member(s["user_id"])
                send_message(s["chat_id"], f"⌛ {note}\n\nYour subscription has ended. Type /plans to resubscribe.")
                removed.append(s)
            else:
                direction = "extended" if days > 0 else "reduced"
                send_message(s["chat_id"], f"ℹ️ {note}\n\nYour subscription was {direction} by {abs(days)} day(s).\nNew expiry: {fmt_date(s['expiry_ts'])}")
        except Exception as e:
            failed_notify += 1
            print(f"[warn] bulk_adjust_subscribers notify failed for {s.get('user_id')}: {e}")
        time.sleep(BROADCAST_PACE_SECONDS)
    for s in removed:
        subscribers.remove(s)
    return len(subscribers) + len(removed), len(removed), failed_notify


# ================== سیستم پاداش عکس سود ==================

def photo_reward_keyboard(submission_id):
    return [[
        {"text": f"✅ Approve (+{PHOTO_REWARD_DAYS}d)", "callback_data": f"photook:{submission_id}"},
        {"text": "❌ Reject", "callback_data": f"photono:{submission_id}"},
    ]]


def count_approved_this_month(submissions, user_id):
    now = datetime.now(timezone.utc)
    count = 0
    for s in submissions:
        if s["user_id"] != user_id or s["status"] != "approved":
            continue
        reviewed = datetime.fromisoformat(s["reviewed_at"])
        if reviewed.year == now.year and reviewed.month == now.month:
            count += 1
    return count


def find_discount(discounts, code):
    code = code.strip().upper()
    for d in discounts:
        if d["code"] == code and d.get("active", True):
            if d.get("expires_at") and now_ts() > d["expires_at"]:
                continue
            if d.get("max_uses") and d.get("used", 0) >= d["max_uses"]:
                continue
            return d
    return None


# ================== کیبوردها ==================

def plans_keyboard():
    rows = []
    for key, p in PLANS.items():
        monthly = p["usd"] / (p["days"] / 30)
        rows.append([{"text": f"💎 {p['label']} — ${p['usd']} (${monthly:.0f}/mo)", "callback_data": f"plan:{key}"}])
    rows.append([{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}])
    return rows


def networks_keyboard(plan_key):
    rows = []
    for key, n in NETWORKS.items():
        if not n["wallet"]:
            continue
        rows.append([{"text": f"💳 {n['label']}", "callback_data": f"net:{key}:{plan_key}"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "back:plans"}])
    return rows


def payment_keyboard(pending_id, plan_key):
    return [
        [{"text": "✅ I've Paid — Check Now", "callback_data": f"check:{pending_id}"}],
        [{"text": "⬅️ Back", "callback_data": f"back:networks:{plan_key}"}],
    ]


def generate_unique_amount(base_usd, pending):
    """مبلغ رو به عدد صحیح (نزدیک‌ترین دلار) گرد می‌کنه و فقط چند سنت (زیر ۱ دلار، تا ۲ رقم
    اعشار) بهش اضافه می‌کنه تا منحصربه‌فرد بشه — این‌طوری مبلغ نهایی همیشه خیلی نزدیک به
    قیمت واقعی پلن می‌مونه و برای کاربر ابهامی ایجاد نمی‌کنه."""
    base = round(base_usd)
    used = {p["amount"] for p in pending}
    for cents in range(1, 100):  # 0.01 تا 0.99 دلار اضافه
        amount = round(base + cents / 100, 2)
        if amount not in used:
            return amount
    raise RuntimeError("Could not generate a unique payment amount")


# ================== پردازش پیام‌های عادی ==================

def handle_updates(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
                    relay_seen_ids, relay_msgid_map):
    offset = state.get("last_update_id", 0) + 1
    # ⚠️ باگ واقعی پیدا و رفع شد: بدون پارامتر صریح allowed_updates، تلگرام همون فیلترِ
    # آخرین باری که این بات‌توکن getUpdates رو (با هر ابزاری، حتی نسخه‌ی قدیمی همین کد قبل
    # از اضافه‌شدن پشتیبانی channel_post) صدا زده رو یادش می‌مونه و همیشه همون رو اعمال
    # می‌کنه - نه چیزی که الان توی کد نوشته شده. یعنی حتی با اینکه handle_channel_signal_post
    # الان کامل پیاده‌سازی شده، اگه یک بار (قبل یا بعد) این فیلتر بدون channel_post ست شده
    # باشه، آپدیت‌های channel_post اصلاً به getUpdates نمی‌رسن و پیام‌های فوروارد/پست‌شده در
    # کانال هیچ‌وقت دیده نمی‌شن - نه خطایی، نه لاگی، فقط سکوت. برای اینکه دیگه هیچ‌وقت به این
    # مشکل برنخوریم، هر بار صریح لیست کامل allowed_updates رو می‌فرستیم تا فیلتر تلگرام همیشه
    # با همین کد یکی باشه.
    result = tg(
        "getUpdates",
        offset=offset,
        timeout=LONG_POLL_SECONDS,
        allowed_updates=["message", "callback_query", "channel_post", "edited_channel_post"],
    )

    for upd in result.get("result", []):
        state["last_update_id"] = upd["update_id"]

        try:
            if "callback_query" in upd:
                handle_callback(upd["callback_query"], pending, subscribers, discounts, applied, used_tx,
                                 referrals, maintenance, photo_submissions)
                continue

            # پیام‌هایی که مستقیم توی خود کانال خصوصی پست می‌شن (نه در چت خصوصی با ربات) این‌جا
            # میان، نه توی upd["message"] — قبلاً کلاً نادیده گرفته می‌شدن (پایین‌تر توضیح داده شده)
            if "channel_post" in upd:
                post = upd["channel_post"]
                src_chat_id = str(post.get("chat", {}).get("id"))
                # ⚠️ اگه ALTCOIN_SOURCE_CHANNEL_ID ست شده و این پست از همون کانالِ منبع اومده، مسیر
                # رله‌ی خودکار (کپی + ثبت در محاسبات) صدا زده می‌شه؛ نه مسیر عادیِ سیگنال/تگ کانال
                # خودمون. این ربات باید از قبل ادمینِ آن کانال هم شده باشد - وگرنه اصلاً این
                # channel_post به getUpdates این توکن نمی‌رسد (سکوت کامل، نه خطا).
                if ALTCOIN_SOURCE_CHANNEL_ID and src_chat_id == ALTCOIN_SOURCE_CHANNEL_ID:
                    handle_altcoin_relay_post(post, relay_seen_ids, relay_msgid_map)
                else:
                    handle_channel_signal_post(post)
                continue

            # اگه ادمین یک پیام فوروارد/تگ‌شده رو توی کانال ویرایش کنه (مثلاً برای درست کردن
            # Entry:/Stop: بعد از یک پیام خطا)، این‌جا هم بررسی می‌شه - نه فقط پست اول
            #
            # ⚠️ ویرایش‌های کانال منبعِ آلتکوین عمداً رله نمی‌شن: خودِ candle_engine.py هیچ‌وقت
            # پیام‌های خودش رو ویرایش نمی‌کنه، پس یک edited_channel_post از اون کانال یعنی یک
            # دخالت دستی غیرمنتظره - رله‌ی خودکارِ کورکورانه‌ی آن (که یعنی یک copyMessage جدید و
            # دوباره صف‌شدن سیگنال/نتیجه) می‌تونه به‌جای اصلاح، تکراری بسازه؛ امن‌تره که این حالت
            # اصلاً دست‌نخورده بمونه.
            if "edited_channel_post" in upd:
                post = upd["edited_channel_post"]
                if not (ALTCOIN_SOURCE_CHANNEL_ID and str(post.get("chat", {}).get("id")) == ALTCOIN_SOURCE_CHANNEL_ID):
                    handle_channel_signal_post(post)
                continue

            msg = upd.get("message")
            if not msg:
                continue

            chat_id = msg["chat"]["id"]
            user_id = msg["from"]["id"]

            # --- ارسال عکس سود (بدون نیاز به دستور خاص؛ هر عکسی که در چت خصوصی فرستاده بشه) ---
            if "photo" in msg and user_id not in ADMIN_USER_IDS:
                handle_photo_submission(msg, chat_id, user_id, photo_submissions)
                continue

            if "text" not in msg:
                continue

            text = msg["text"].strip()

            if user_id in ADMIN_USER_IDS and text.startswith("/admin"):
                handle_admin_command(chat_id, text, subscribers, discounts, maintenance, photo_submissions,
                                      pending, applied, referrals)
                continue

            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                if len(parts) == 2 and parts[1].startswith("ref_"):
                    try:
                        referrer_id = int(parts[1][4:])
                        already_sub = any(s["user_id"] == user_id for s in subscribers)
                        if referrer_id != user_id and str(user_id) not in referrals and not already_sub:
                            referrals[str(user_id)] = referrer_id
                    except ValueError:
                        pass
                send_message(chat_id, WELCOME_TEXT, keyboard=main_menu_keyboard())

            elif text == "/help":
                # 🔴 طبق درخواست صریح («همه‌ی زیرمجموعه‌های منوی ربات حتماً دکمه‌ی برگشت داشته
                # باشند»): قبلاً /help تنها صفحه‌ی متنیِ کل ربات بود که هیچ دکمه‌ای زیرش
                # نداشت (نه راه برگشتی به منو، نه هیچ چیز دیگه) - چون معمولاً به‌عنوان یک
                # دستور مستقل تایپ می‌شه، نه از طریق کلیک روی یک دکمه‌ی منو. الان یک دکمه‌ی
                # «⬅️ Back to Menu» زیرش اضافه شد تا کاربر بعد از خوندنش مجبور نباشه دوباره
                # /start رو تایپ کنه - دقیقاً همون الگویی که بقیه‌ی صفحات منو دارن.
                send_message(chat_id, HELP_TEXT, keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text == "/refer":
                # 🔴 طبق درخواست صریح («همه‌ی زیرمجموعه‌های منو باید دکمه‌ی برگشت داشته
                # باشند»): این صفحه وقتی از طریق دکمه‌ی منو («🤝 Refer a Friend») باز می‌شه از
                # قبل «⬅️ Back» داشت (نگاه کنید handle_callback_query::menu:refer)، ولی وقتی
                # مستقیماً با تایپ‌کردن /refer باز می‌شد (که خودِ BOT_COMMANDS_MENU/☰ هم
                # پیشنهادش می‌ده) هیچ کیبوردی نداشت - یعنی همون محتوا بسته به مسیر ورود
                # ناهماهنگ بود. الان هر دو مسیر دقیقاً یک کیبورد یکسان می‌دن.
                send_message(chat_id, referral_text(user_id), keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text == "/support":
                send_message(chat_id, support_text(), keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text == "/disclaimer":
                send_message(chat_id, DISCLAIMER_TEXT, keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text == "/plans":
                if is_paused(maintenance):
                    send_message(chat_id, maintenance_notice_text(maintenance))
                    continue
                active = next((s for s in subscribers if s["user_id"] == user_id), None)
                note = f"\n\n<i>Your current plan runs until {fmt_date(active['expiry_ts'])} — a new purchase extends it.</i>" if active else ""
                disc_note = ""
                uid_key = str(user_id)
                if uid_key in applied:
                    disc_note = f"\n\n🏷 Discount <b>{applied[uid_key]['code']}</b> ({applied[uid_key]['percent']}% off) will be applied."
                send_message(chat_id, "💎 <b>Choose a plan</b>" + note + disc_note, keyboard=plans_keyboard())

            elif text.startswith("/code"):
                parts = text.split(maxsplit=1)
                if len(parts) != 2:
                    send_message(chat_id, "Usage: /code YOURCODE")
                else:
                    d = find_discount(discounts, parts[1])
                    if d:
                        applied[str(user_id)] = {"code": d["code"], "percent": d["percent"]}
                        send_message(chat_id, f"🏷 Code <b>{d['code']}</b> applied — {d['percent']}% off your next purchase.\nType /plans to continue.")
                    else:
                        send_message(chat_id, "That code is invalid, expired, or fully used.")

            elif text == "/status":
                send_message(chat_id, format_status_message(subscribers, user_id),
                              keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text == "/results":
                _ensure_fresh_data()
                send_message(chat_id, format_results_message(),
                              keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text == "/guide":
                send_message(chat_id, GUIDE_STEPS[1], keyboard=guide_keyboard(1))

            elif text == "/history":
                _ensure_fresh_data()
                msg, total_pages = format_history_page(0)
                send_message(chat_id, msg, keyboard=history_keyboard(0, total_pages))

            elif text == "/equity":
                _ensure_fresh_data()
                send_message(chat_id, format_equity_message(),
                              keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text.startswith("/signal"):
                parts = text.split(maxsplit=1)
                if len(parts) != 2 or not parts[1].strip():
                    send_message(chat_id, "Usage: /signal ID — e.g. /signal TC-004821\n"
                                           "(the ID is shown as a #hashtag below the date/time of the channel message, "
                                           "and next to each row in /results and /history)")
                else:
                    _ensure_fresh_data()
                    send_message(chat_id, format_signal_lookup_message(parts[1]),
                                  keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])

            elif text.startswith("/pnl"):
                parts = text.split()
                if len(parts) == 1:
                    # 🔴 بدون آرگومان: اگه پروفایل ذخیره‌شده داره همون رو مستقیم نشون بده،
                    # وگرنه دکمه‌های مبلغ عمومی + راهنمای /setrisk رو پیشنهاد بده.
                    profile = _load_risk_profile(user_id)
                    if profile:
                        amount = profile["balance"] * profile["risk_pct"] / 100
                        _ensure_fresh_data()
                        send_message(chat_id, format_pnl_message(amount, profile=profile),
                                      keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])
                    else:
                        send_message(chat_id, "🧮 Pick a risk amount, type <code>/pnl AMOUNT</code> for a custom "
                                               "value, or set up your risk profile once with <code>/setrisk "
                                               "BALANCE RISK_PERCENT</code> (e.g. /setrisk 1000 2) so /pnl always "
                                               "shows your real numbers.", keyboard=pnl_keyboard(user_id))
                elif len(parts) == 2:
                    try:
                        amount = float(parts[1])
                        _ensure_fresh_data()
                        send_message(chat_id, format_pnl_message(amount),
                                      keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])
                    except ValueError:
                        send_message(chat_id, "Amount must be a number, e.g. /pnl 100")
                else:
                    send_message(chat_id, "Usage: /pnl AMOUNT — e.g. /pnl 100 (or just /pnl if you've set up "
                                           "/setrisk)")

            elif text.startswith("/setrisk"):
                parts = text.split()
                if len(parts) != 3:
                    send_message(chat_id, "Usage: /setrisk BALANCE RISK_PERCENT — e.g. /setrisk 1000 2\n"
                                           "This means: $1000 account, risking 2% ($20) per signal. After this, "
                                           "/pnl will automatically use these numbers.")
                else:
                    try:
                        balance = float(parts[1])
                        risk_pct = float(parts[2])
                        if balance <= 0 or risk_pct <= 0 or risk_pct > 100:
                            send_message(chat_id, "❌ Balance must be positive, and risk % must be between 0 and 100.")
                        else:
                            _save_risk_profile(user_id, balance, risk_pct)
                            amount = balance * risk_pct / 100
                            send_message(chat_id, f"✅ Risk profile saved: ${balance:.0f} balance × {risk_pct:.1f}% "
                                                   f"= <b>${amount:.2f}</b> per trade.\nJust type /pnl anytime to "
                                                   f"see your P&amp;L with these numbers.",
                                          keyboard=[[{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}]])
                    except ValueError:
                        send_message(chat_id, "Both values must be numbers, e.g. /setrisk 1000 2")

            elif text == "/cancel":
                active = next((s for s in subscribers if s["user_id"] == user_id), None)
                if active:
                    remove_member(user_id)
                    subscribers.remove(active)
                    send_message(chat_id, "Your membership has been cancelled and you've been removed from the channel.\nType /plans anytime to rejoin.")
                else:
                    send_message(chat_id, "You don't have an active membership.")
        except Exception as e:
            # 🔴 رفعِ باگِ واقعیِ «ربات به بعضی پیام‌ها اصلاً جواب نمی‌ده»: قبلاً
            # state["last_update_id"] (خط بالا) همیشه *قبل* از پردازش هر آپدیت تنظیم می‌شد.
            # یعنی اگه پردازش یک دستور/دکمه (مثلاً یک باگ داخل format_results_message یا
            # handle_callback) خطا می‌داد، آن آپدیت already «دیده‌شده» علامت خورده بود - پس
            # هیچ‌وقت دوباره fetch/پردازش نمی‌شد (نه یک retry بی‌نهایت، دقیقاً برعکس: برای
            # همیشه رد می‌شد)، و از طرفی چون کل exception فقط توسط handler عمومیِ حلقه‌ی
            # اصلی (main) گرفته می‌شد، هیچ‌جا لاگ خاصی برای *همون آپدیت* ثبت نمی‌شد و هیچ
            # هشداری به ادمین نمی‌رفت - یعنی از دید کاربر، ربات «هیچ جواب نمی‌ده»، بدون هیچ
            # نشانه‌ای برای پیدا کردن علت. الان: خطا اینجا (سطح هر آپدیت، نه کل batch) گرفته
            # می‌شه، لاگ و به ادمین هشدار داده می‌شه (با traceback، تا واقعاً دیده بشه نه گم
            # بشه)، و اگه chat_id همون آپدیت قابل تشخیص باشه، خودِ کاربر هم یک پیام کوتاه
            # می‌گیره که لااقل بدونه پیامش گم نشده و باید دوباره امتحان کنه.
            print(f"[error] Failed to process update {upd.get('update_id')}: {e}")
            alert_admins_text(
                f"⚠️ Bot failed to process update id={upd.get('update_id')}: {e}\n\n"
                f"{traceback.format_exc()[-1200:]}")
            try:
                _fallback_chat_id = (
                    upd.get("callback_query", {}).get("message", {}).get("chat", {}).get("id")
                    or upd.get("message", {}).get("chat", {}).get("id")
                )
                if _fallback_chat_id:
                    send_message(_fallback_chat_id,
                                 "⚠️ Something went wrong processing your last message — please try again in a moment.")
            except Exception:
                pass


def handle_photo_submission(msg, chat_id, user_id, photo_submissions):
    photos = msg["photo"]
    largest = photos[-1]  # تلگرام سایزهای مختلف رو از کوچیک به بزرگ می‌فرسته
    file_id = largest["file_id"]
    file_unique_id = largest["file_unique_id"]

    if any(s["file_unique_id"] == file_unique_id for s in photo_submissions):
        send_message(chat_id, "You've already submitted this photo before.")
        return

    submission_id = f"{user_id}-{now_ts()}"
    photo_submissions.append({
        "id": submission_id, "user_id": user_id, "chat_id": chat_id,
        "file_id": file_id, "file_unique_id": file_unique_id,
        "submitted_at": now_ts(), "status": "pending",
        "reviewed_by": None, "reviewed_at": None,
    })

    remaining = max(0, MAX_PHOTO_REWARDS_PER_MONTH - count_approved_this_month(photo_submissions, user_id))
    send_message(chat_id, f"📸 Thanks! Your profit screenshot was sent for review.\nYou'll be notified once it's approved ({remaining} reward slot(s) left this month).")

    caption = f"📸 New profit screenshot from user <code>{user_id}</code>\nApprove to grant +{PHOTO_REWARD_DAYS} day(s)."
    for admin_id in ADMIN_USER_IDS:
        try:
            tg("sendPhoto", chat_id=admin_id, photo=file_id, caption=caption, parse_mode="HTML",
               reply_markup={"inline_keyboard": photo_reward_keyboard(submission_id)})
        except Exception as e:
            print(f"[warn] failed to forward photo to admin {admin_id}: {e}")


def render_networks_screen(chat_id, message_id, plan_key, user_id, applied):
    """صفحه‌ی «انتخاب روش پرداخت» رو نشون می‌ده - هم وقتی اولین‌بار یک پلن انتخاب می‌شه
    صدا زده می‌شه، هم وقتی کاربر از صفحه‌ی پرداخت (آدرس کیف‌پول) دکمه‌ی Back رو بزنه. جدا
    شدن به یک تابع مستقل دقیقاً برای همین استفاده‌ی دوباره است."""
    plan = PLANS[plan_key]
    price_line = f"${plan['usd']}"
    uid_key = str(user_id)
    if uid_key in applied:
        pct = applied[uid_key]["percent"]
        discounted = round(plan["usd"] * (1 - pct / 100))
        price_line = f"<s>${plan['usd']}</s> ${discounted} (-{pct}%)"
    edit_message(chat_id, message_id, f"💎 <b>{plan['label']}</b> — {price_line}\n\nChoose a payment method:", keyboard=networks_keyboard(plan_key))


def handle_callback(cb, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions):
    data = cb["data"]
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    user_id = cb["from"]["id"]
    answer_callback(cb["id"])

    if data == "back:plans":
        edit_message(chat_id, message_id, "💎 <b>Choose a plan</b>", keyboard=plans_keyboard())
        return

    if data.startswith("back:networks:"):
        plan_key = data.split(":", 2)[2]
        render_networks_screen(chat_id, message_id, plan_key, user_id, applied)
        return

    if data == "menu:home":
        edit_message(chat_id, message_id, WELCOME_TEXT, keyboard=main_menu_keyboard())
        return

    if data == "menu:plans":
        if is_paused(maintenance):
            rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
            edit_message(chat_id, message_id, maintenance_notice_text(maintenance), keyboard=rows)
            return
        active = next((s for s in subscribers if s["user_id"] == user_id), None)
        note = f"\n\n<i>Your current plan runs until {fmt_date(active['expiry_ts'])} — a new purchase extends it.</i>" if active else ""
        edit_message(chat_id, message_id, "💎 <b>Choose a plan</b>" + note, keyboard=plans_keyboard())
        return

    if data == "menu:status":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, format_status_message(subscribers, user_id), keyboard=rows)
        return

    if data == "menu:results":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, format_results_message(), keyboard=rows)
        return

    if data.startswith("hist:"):
        page = int(data.split(":")[1])
        msg, total_pages = format_history_page(page)
        edit_message(chat_id, message_id, msg, keyboard=history_keyboard(page, total_pages))
        return

    if data == "menu:pnl":
        edit_message(chat_id, message_id, "🧮 Pick a risk amount per trade, or type <code>/pnl AMOUNT</code> for a custom value:", keyboard=pnl_keyboard(user_id))
        return

    if data.startswith("guide:"):
        step = int(data.split(":")[1])
        edit_message(chat_id, message_id, GUIDE_STEPS[step], keyboard=guide_keyboard(step))
        return

    if data == "menu:equity":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, format_equity_message(), keyboard=rows)
        return

    if data == "menu:refer":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, referral_text(user_id), keyboard=rows)
        return

    if data == "menu:support":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, support_text(), keyboard=rows)
        return

    if data == "menu:disclaimer":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, DISCLAIMER_TEXT, keyboard=rows)
        return

    if data == "pnl:mine":
        # 🔴 اضافه شد: دکمه‌ی «پروفایل من» - همون amount ذخیره‌شده با /setrisk رو مستقیم نشون می‌ده
        profile = _load_risk_profile(user_id)
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:pnl"}]]
        if not profile:
            edit_message(chat_id, message_id, "No saved risk profile yet — set one up with /setrisk BALANCE "
                                               "RISK_PERCENT (e.g. /setrisk 1000 2).", keyboard=rows)
        else:
            amount = profile["balance"] * profile["risk_pct"] / 100
            edit_message(chat_id, message_id, format_pnl_message(amount, profile=profile), keyboard=rows)
        return

    if data.startswith("pnl:"):
        amount = float(data.split(":")[1])
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:pnl"}]]
        edit_message(chat_id, message_id, format_pnl_message(amount), keyboard=rows)
        return

    if data.startswith("plan:"):
        plan_key = data.split(":")[1]
        render_networks_screen(chat_id, message_id, plan_key, user_id, applied)
        return

    if data.startswith("net:"):
        _, net_key, plan_key = data.split(":")
        plan = PLANS[plan_key]
        net = NETWORKS[net_key]

        base_usd = plan["usd"]
        uid_key = str(user_id)
        applied_code = None
        if uid_key in applied:
            pct = applied[uid_key]["percent"]
            applied_code = applied[uid_key]["code"]
            base_usd = round(base_usd * (1 - pct / 100), 2)

        pending[:] = [p for p in pending if p["user_id"] != user_id]

        amount = generate_unique_amount(base_usd, pending)
        pending_id = f"{user_id}-{now_ts()}"
        pending.append({
            "id": pending_id, "user_id": user_id, "chat_id": chat_id, "plan": plan_key, "network": net_key,
            "amount": amount, "created_at": now_ts(), "expires_at": now_ts() + PAYMENT_WINDOW_MINUTES * 60,
            "discount_code": applied_code,
        })
        if uid_key in applied:
            del applied[uid_key]

        edit_message(
            chat_id, message_id,
            f"🔐 <b>Send payment</b>\n\n"
            f"Plan: {plan['label']}\n"
            f"Network: {net['label']}\n\n"
            f"Amount: <b>{amount} USDT</b>\n"
            f"Address:\n<code>{net['wallet']}</code>\n\n"
            f"⚠️ Send the <b>exact amount</b> shown (a few cents above the plan "
            f"price are added automatically so your payment is identified instantly).\n"
            f"⚠️ {net['label']} network only.\n"
            f"⏳ Valid for {PAYMENT_WINDOW_MINUTES} minutes.\n\n"
            f"Paid already? Tap the button below to check instantly.",
            keyboard=payment_keyboard(pending_id, plan_key),
        )
        return

    if data.startswith("check:"):
        pending_id = data.split(":", 1)[1]
        p = next((x for x in pending if x["id"] == pending_id), None)
        if not p:
            answer_callback(cb["id"], "This payment request is no longer active.")
            return
        transfers = fetch_all_transfers()
        confirmed = try_confirm_payment(p, transfers, subscribers, discounts, used_tx, referrals)
        if confirmed:
            pending.remove(p)
        else:
            send_message(chat_id, "⏳ No matching payment found yet — this can take a minute or two after you send it. Try again shortly.")
        return

    if data.startswith("photook:") or data.startswith("photono:"):
        if user_id not in ADMIN_USER_IDS:
            return
        approve = data.startswith("photook:")
        submission_id = data.split(":", 1)[1]
        sub = next((s for s in photo_submissions if s["id"] == submission_id), None)
        if not sub:
            edit_message(chat_id, message_id, "This submission is no longer available.")
            return
        if sub["status"] != "pending":
            edit_message(chat_id, message_id, f"Already reviewed ({sub['status']}).")
            return

        sub["reviewed_by"] = user_id
        sub["reviewed_at"] = datetime.now(timezone.utc).isoformat()

        if not approve:
            sub["status"] = "rejected"
            edit_message(chat_id, message_id, "❌ Rejected.")
            send_message(sub["chat_id"], "Your profit screenshot wasn't approved this time. Feel free to reach out to support if you think this is a mistake.")
            return

        remaining_before = MAX_PHOTO_REWARDS_PER_MONTH - count_approved_this_month(photo_submissions, sub["user_id"])
        sub["status"] = "approved"
        if remaining_before <= 0:
            edit_message(chat_id, message_id, f"✅ Approved (kept for marketing), but user already hit this month's reward limit ({MAX_PHOTO_REWARDS_PER_MONTH}) — no extra days granted.")
            send_message(sub["chat_id"], "📸 Your screenshot was approved — thank you! You've already reached this month's reward limit, so no extra days this time, but keep them coming for next month 🙌")
            return

        existing = next((s for s in subscribers if s["user_id"] == sub["user_id"]), None)
        base_ts = existing["expiry_ts"] if existing and existing["expiry_ts"] > now_ts() else now_ts()
        new_expiry = base_ts + PHOTO_REWARD_DAYS * 24 * 3600
        if existing:
            existing["expiry_ts"] = new_expiry
        else:
            subscribers.append({"user_id": sub["user_id"], "chat_id": sub["chat_id"], "expiry_ts": new_expiry, "reminded": False})

        edit_message(chat_id, message_id, f"✅ Approved — +{PHOTO_REWARD_DAYS} day(s) granted.")
        send_message(sub["chat_id"], f"🎉 Your profit screenshot was approved! +{PHOTO_REWARD_DAYS} day(s) added.\nNew expiry: {fmt_date(new_expiry)}")
        return

    # 🔴 اضافه شد: طبق درخواست صریح - «در زیر هر سیگنال که ربات موردی پیدا کرده، دستورات
    # قابل‌انجام آورده بشه تا با کلیک روی هر دستوری ... اقدام لازم در سریعترین حالت ممکن انجام
    # شود». این دکمه‌ها را candle_engine.py::check_stale_open_trades زیر هشدار stale_open_trade
    # می‌ذاره؛ callback_query هرچی که هست (چه از candle_engine.py فرستاده شده باشه چه از
    # subscription_bot.py) همیشه اینجا (تنها فرآیندی با getUpdates پیوسته) می‌رسه - کاملاً
    # stateless از دید تلگرام، بدون نیاز به هیچ ارتباط مستقیم بین دو اسکریپت.
    if data.startswith("aclt:"):
        if user_id not in ADMIN_USER_IDS:
            return
        _, ref, mode = data.split(":", 2)
        final_r = {"cur": None, "loss": -1.0, "be": 0.0}.get(mode)
        if mode not in ("cur", "loss", "be"):
            return
        _queue_admin_close_request(ref, final_r)
        final_r_desc = "auto fair-value" if final_r is None else f"{final_r:+.2f}R"
        # دکمه‌ها رو با یک پیامِ تاییدِ روشن جایگزین می‌کنیم - هم جلوی دوبار-کلیکِ تصادفی رو
        # می‌گیره، هم واضح می‌کنه که اقدام واقعاً صف شده، بدون این‌که خودِ جزییات سیگنال
        # (متنِ بالای پیام) از بین بره.
        edit_message(chat_id, message_id, cb["message"].get("text", "") +
                     f"\n\n✅ Queued to close ({final_r_desc}) — will apply within a few seconds.")
        return

    if data.startswith("asig:"):
        if user_id not in ADMIN_USER_IDS:
            return
        ref = data.split(":", 1)[1]
        send_message(chat_id, format_signal_lookup_message(ref))
        return

    # 🔴 اضافه شد: دکمه‌های زیر هشدار forwarded_result_unmatched
    # (candle_engine.py::process_forwarded_results) - وقتی یک پیام نتیجه‌ی فوروارد‌شده
    # (از کانال آلت‌کوین) هیچ‌وقت با یک معامله‌ی باز تطبیق پیدا نکرد، هر معامله‌ی
    # فوروارد-رصدشونده‌ی هنوز-باز به‌عنوان یک گزینه‌ی «شاید همینه» با دکمه نشون داده
    # می‌شه. با کلیک، دقیقاً همون final_r این رویداد (اگه پارس شده بود) روی همون
    # معامله‌ی انتخابی اعمال و بسته می‌شه - از طریق همون صفِ admin_close_requests.json
    # که /admin_close_trade هم استفاده می‌کنه (تست‌شده، بدون نیاز به منطق جدید).
    if data.startswith("fwdapply:"):
        if user_id not in ADMIN_USER_IDS:
            return
        _, ref, r_str = data.split(":", 2)
        final_r = float(r_str) if r_str else None
        _queue_admin_close_request(ref, final_r)
        final_r_desc = "auto fair-value" if final_r is None else f"{final_r:+.2f}R"
        edit_message(chat_id, message_id, cb["message"].get("text", "") +
                     f"\n\n✅ Applied to {ref} ({final_r_desc}) — will close within a few seconds.")
        return



# ================== تایید پرداخت (مشترک بین چک دستی و چک دوره‌ای) ==================

def try_confirm_payment(p, transfers, subscribers, discounts, used_tx, referrals) -> bool:
    net = NETWORKS[p["network"]]
    for tx in transfers:
        if tx["network"] != p["network"] or tx["tx_id"] in used_tx:
            continue
        if tx["to"] != net["wallet"].lower():
            continue
        value_usdt = units_to_usdt(tx["value"], net["decimals"])
        if abs(value_usdt - p["amount"]) < 0.005 and tx["ts"] >= p["created_at"] - 60:
            used_tx.append(tx["tx_id"])
            plan = PLANS[p["plan"]]
            existing = next((s for s in subscribers if s["user_id"] == p["user_id"]), None)
            is_first_ever_purchase = existing is None
            base_ts = existing["expiry_ts"] if existing and existing["expiry_ts"] > now_ts() else now_ts()
            new_expiry = base_ts + plan["days"] * 24 * 3600

            if existing:
                existing["expiry_ts"] = new_expiry
                existing["reminded"] = False
            else:
                subscribers.append({"user_id": p["user_id"], "chat_id": p["chat_id"], "expiry_ts": new_expiry, "reminded": False})

            if p.get("discount_code"):
                d = next((x for x in discounts if x["code"] == p["discount_code"]), None)
                if d:
                    d["used"] = d.get("used", 0) + 1

            # اگه این اولین خرید کاربر بوده و از طریق لینک رفرال اومده، پاداش رو به معرف بده
            if is_first_ever_purchase:
                referrer_id = referrals.pop(str(p["user_id"]), None)
                if referrer_id:
                    ref_sub = next((s for s in subscribers if s["user_id"] == referrer_id), None)
                    ref_base = ref_sub["expiry_ts"] if ref_sub and ref_sub["expiry_ts"] > now_ts() else now_ts()
                    ref_new_expiry = ref_base + REFERRAL_REWARD_DAYS * 24 * 3600
                    if ref_sub:
                        ref_sub["expiry_ts"] = ref_new_expiry
                    else:
                        subscribers.append({"user_id": referrer_id, "chat_id": referrer_id, "expiry_ts": ref_new_expiry, "reminded": False})
                    send_message(referrer_id, f"🎉 A friend you referred just subscribed! You've earned <b>{REFERRAL_REWARD_DAYS} free days</b>.\nNew expiry: {fmt_date(ref_new_expiry)}")

            try:
                link = create_invite_link(expire_minutes=60)
                extra = "" if not existing else " (renewed)"
                send_message(p["chat_id"], f"✅ <b>Payment confirmed!</b>{extra}\nExpires: {fmt_date(new_expiry)}\n\nJoin here (one-time link, 1 hour):\n{link}")
            except Exception as e:
                send_message(p["chat_id"], "✅ Payment confirmed, but we couldn't create your invite link automatically. Message us and we'll add you.")
                print(f"[error] invite link failed: {e}")
            return True
    return False


def process_pending_payments(pending, subscribers, discounts, used_tx, referrals):
    if not pending:
        return
    transfers = fetch_all_transfers()
    still_pending = []
    for p in pending:
        if now_ts() > p["expires_at"]:
            send_message(p["chat_id"], "⌛ This payment window expired. Type /plans to try again.")
            continue
        if try_confirm_payment(p, transfers, subscribers, discounts, used_tx, referrals):
            continue
        still_pending.append(p)
    pending[:] = still_pending


def process_subscription_lifecycle(subscribers, maintenance):
    if is_paused(maintenance):
        return  # در حالت تعمیر، هیچ کاربری به‌خاطر گذشت زمان حذف نمی‌شود
    still_active = []
    for s in subscribers:
        remaining = s["expiry_ts"] - now_ts()
        if remaining <= 0:
            remove_member(s["user_id"])
            send_message(s["chat_id"], "⌛ Your subscription has ended and you've been removed from the channel.\nType /plans to renew.")
            continue
        if remaining <= 3 * 24 * 3600 and not s.get("reminded"):
            send_message(s["chat_id"], f"🔔 Your subscription ends {fmt_date(s['expiry_ts'])}. Type /plans to renew.")
            s["reminded"] = True
        still_active.append(s)
    subscribers[:] = still_active


# ================== دستورات ادمین ==================

ADMIN_HELP = (
    "<b>Admin commands</b>\n\n"
    "<u>Users</u>\n"
    "/admin_list [page] — list active subscribers (paginated, 50/page)\n"
    "/admin_user &lt;user_id&gt; — full detail on one user (support lookups)\n"
    "/admin_add &lt;user_id&gt; &lt;days&gt; — add/extend a user\n"
    "/admin_extend &lt;user_id&gt; &lt;days&gt; — add N days\n"
    "/admin_reduce &lt;user_id&gt; &lt;days&gt; — subtract N days\n"
    "/admin_remove &lt;user_id&gt; — remove immediately\n\n"
    "<u>Bulk (all active subscribers)</u>\n"
    "/admin_bulk_extend &lt;days&gt; [reason...] — add N days to everyone + notify\n"
    "/admin_bulk_reduce &lt;days&gt; [reason...] — subtract N days from everyone + notify\n\n"
    "<u>System</u>\n"
    "/admin_status — one-glance dashboard: open trades, API quota, last report/commit, anything flagged\n\n"
    "<u>Stuck trades</u>\n"
    "/admin_close_trade &lt;signal_id or key&gt; [final_r] — force-close any stuck open trade "
    "(prefer the signal_id, e.g. TC-004821, from a stale_open_trade alert or the channel message's "
    "#hashtag below its date/time — the older symbol|timeframe key still works as a fallback). Always works, regardless "
    "of the underlying cause.\n\n"
    "<u>Pricing</u>\n"
    "/admin_prices — view current plan prices\n"
    "/admin_set_price &lt;plan_id&gt; &lt;usd&gt; — change a plan's price (takes effect immediately)\n\n"
    "<u>Maintenance / Pause</u>\n"
    "/admin_pause [reason...] — pause new signals &amp; expiry removals, notify everyone\n"
    "/admin_resume — resume + auto-extend everyone by the pause duration\n\n"
    "<u>Manual signals</u>\n"
    "/admin_manual_signal &lt;SYMBOL/USD&gt; &lt;BUY|SELL&gt; &lt;TF&gt; &lt;entry&gt; &lt;sl&gt; — post a "
    "signal in the standard format (stop + 4 targets), tracked live and counted in /results. "
    "TF must be one of: 1M, 5M, 15M, 1H, 4H. Any symbol works (e.g. BTC/USD, SHIB/USD), not "
    "just BTC/ETH.\n"
    "Or post/forward directly in the channel with a tagged line, e.g.:\n"
    "<code>#signal BTC/USD BUY 1H 65000 64000</code>\n"
    "— same tracking, same /results. Or just forward/post a message already shaped like "
    "our own signals (🟢/🔴, LONG/SHORT — SYMBOL, Entry:, Stop:) and it's auto-detected, "
    "on any symbol. Untagged, unstructured posts are left alone.\n\n"
    "<u>Photo rewards</u>\n"
    "/admin_photos [n] — resend the last n approved screenshots (default 10)\n\n"
    "<u>Discounts</u>\n"
    "/admin_discount_add &lt;CODE&gt; &lt;percent&gt; [max_uses] [days_valid]\n"
    "/admin_discount_list\n"
    "/admin_discount_remove &lt;CODE&gt;"
)


def _find_sub(subscribers, user_id):
    return next((s for s in subscribers if s["user_id"] == user_id), None)


SYMBOL_FORMAT_RE = re.compile(r"^[A-Z0-9]{2,15}/USD$")


def queue_manual_signal(symbol, side, entry_s, sl_s, forwarded_tracking=False, tf_label=None, silent=False,
                         source_signal_id=None):
    """
    یک سیگنال دستی رو اعتبارسنجی و در manual_signals.json صف می‌کنه تا candle_engine.py
    (تابع process_manual_signals) پیکش کنه، به کانال با چارت و ۴ تارگت پست کنه، و
    رصدش کنه (استاپ/تارگت/برک‌ایون/تریلینگ/رانر) دقیقاً مثل سیگنال‌های خودکار — یعنی
    نتیجه‌اش هم توی /results حساب می‌شه. این تابع مشترکه بین دستور /admin_manual_signal
    (در چت خصوصی با ربات) و پست مستقیم در کانال با تگ #signal یا فوروارد خودکار‌شناسایی‌شده.

    نماد دیگه محدود به BTC/USD یا ETH/USD نیست - هر نمادی به فرمت TICKER/USD (مثلاً
    SHIB/USD) پذیرفته می‌شه، چون candle_engine.py قیمت رو از Twelve Data (که هر جفت‌ارزی
    رو پشتیبانی می‌کنه) می‌گیره، نه از یک لیست ثابت. فقط برای بیت‌کوین/اتریوم WebSocket
    زنده (بدون هزینه) هست؛ بقیه‌ی نمادها از همون مسیر REST که قبلاً برای طلا استفاده
    می‌شد رصد می‌شن - کندتر (چند ثانیه تا حدود یک دقیقه) ولی همچنان زنده و دقیق.

    forwarded_tracking: فقط برای پیام‌هایی که تلگرام واقعاً به‌عنوان فوروارد علامت زده
    (is_forwarded_message==True) روشن می‌شه. وقتی روشنه، candle_engine.py این معامله رو با
    قیمت زنده‌ی خودش رصد نمی‌کنه - چون قراره ادامه‌ی همون رشته‌ی فوروارد (تارگت/استاپ) هم
    توی کانال بیاد و خودشون منبع نتیجه‌ان (parse_forwarded_result_text بالاتر).

    tf_label: اگه از خط تیتر پیام یک تایم‌فریم (مثلاً «5M») استخراج شده باشه، اینجا پاس داده
    می‌شه - candle_engine.py سعی می‌کنه با یکی از تایم‌فریم‌های شناخته‌شده تطبیقش بده (برای
    نمایش درست + گرفتن چارت با کندل‌های هم‌اندازه)؛ اگه چیزی نبود/تطبیق نخورد، به‌جای خالی
    ماندن صریحاً «Manual» نشون داده می‌شه - قبلاً همیشه بی‌هیچ برچسبی پست می‌شد.

    silent: وقتی True باشه، candle_engine.py (process_manual_signals) این معامله رو دقیقاً
    مثل بقیه باز/رهگیری/در نتایج حساب می‌کنه، ولی هیچ پیام جدیدی (چارت/متن استاندارد) در
    کانال پست نمی‌کنه. برای زمانی‌ست که خودِ متن اصلی سیگنال از قبل (با فرمت کامل و چارت
    خودش) در کانال منتشر شده - مثلاً توسط رله‌ی خودکار کانال آلتکوین (handle_altcoin_relay_post)
    که خودش با copyMessage عیناً پستش کرده - و یک پستِ دومِ بازتولیدشده فقط تکراری/گیج‌کننده
    می‌بود.

    🔴 اضافه شد: source_signal_id - اگه متنِ منبع خودش یک خط 🆔 (مثلاً «🆔 ALT-000005») داشته
    باشه، از _extract_source_signal_id استخراج و اینجا پاس داده می‌شه. candle_engine.py
    (process_manual_signals::open_new_trade) این رو به‌عنوان signal_id واقعیِ همین معامله
    استفاده می‌کنه - نه یک TC-XXXXXX تازه - تا شناسه‌ای که در کانال دیده می‌شه همیشه دقیقاً
    همون شناسه‌ای باشه که /signal، /admin_close_trade، و تطبیقِ نتایج فوروارد‌شده هم می‌شناسنش.
    """
    symbol = (symbol or "").upper()
    side = (side or "").upper()
    if not SYMBOL_FORMAT_RE.match(symbol):
        return False, "❌ Symbol must look like TICKER/USD, e.g. BTC/USD, ETH/USD, or SHIB/USD."
    if side not in ("BUY", "SELL"):
        return False, "❌ Side must be BUY or SELL."
    # 🔴 اجباری: طبق درخواست صریح، هیچ سیگنالی - حتی سیگنال دستی - نباید بدون تایم‌فریمِ
    # مشخص وارد سیستم بشه. قبلاً tf_label اختیاری بود و وقتی نامشخص می‌موند، بعداً در
    # candle_engine.py زیر یک برچسب عمومی «Manual» (بدون تایم‌فریم واقعی) پست/رصد می‌شد -
    # دقیقاً همون چیزی که در «By timeframe»ی گزارش روزانه به‌شکل یک ردیف گمراه‌کننده دیده
    # می‌شد. الان این تابع - که هر سه مسیر ورودی (/admin_manual_signal، تگ #signal، و
    # فوروارد خودکار‌شناسایی‌شده) از همینجا رد می‌شن - اصلاً اجازه نمی‌ده سیگنالی بدون یک
    # تایم‌فریمِ معتبر (عضو VALID_TIMEFRAME_LABELS) صف بشه؛ به‌جاش خطای واضح برمی‌گردونه تا
    # فراخوان (چه پاسخ به ادمین، چه هشدار خودکار) اطلاع بده که باید با تایم‌فریم دوباره
    # فرستاده بشه.
    tf_label = (tf_label or "").strip().upper()
    if tf_label not in VALID_TIMEFRAME_LABELS:
        return False, (
            f"❌ Every signal needs a valid timeframe — got: {tf_label or '(none)'}. "
            f"Valid options: {', '.join(sorted(VALID_TIMEFRAME_LABELS))}."
        )
    try:
        entry, sl = float(entry_s), float(sl_s)
    except ValueError:
        return False, "❌ entry and sl must be numbers."
    if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
        return False, "❌ Stop-loss must be below entry for BUY, or above entry for SELL."

    signals = _load("manual_signals.json", [])
    # 🔴 گارد ایمنیِ اضافه: اگه این source_signal_id همین الان pending/active در همین صفه
    # (مثلاً یک ری‌استارت/آفست باعث شده همون پیامِ منبع دوباره پردازش بشه)، دوباره صف نمی‌شه -
    # وگرنه دو معامله‌ی جدا با عیناً یک signal_id تلاش می‌کردن باز بشن (open_new_trade خودش هم
    # این تصادم رو چک می‌کنه، ولی جلوگیریِ زودتر از اینجا تمیزتره).
    if source_signal_id:
        for s in signals:
            if s.get("source_signal_id") == source_signal_id and s.get("status") in ("pending", "active"):
                return False, f"↩️ Already queued/active under the same source id ({source_signal_id}) — skipped."
    signal_id = f"m-{now_ts()}"
    signals.append({
        "id": signal_id, "symbol": symbol, "side": side, "entry": entry, "sl": sl,
        "status": "pending", "created_at": now_ts(), "forwarded_tracking": bool(forwarded_tracking),
        "tf_label": tf_label, "silent": bool(silent), "source_signal_id": source_signal_id,
    })
    _save("manual_signals.json", signals)
    tracking_note = (
        " Follow-up target/stop messages you forward for this trade will be read automatically "
        "to record the result — no need to track it live myself."
        if forwarded_tracking else ""
    )
    if silent:
        return True, (
            f"📌 Manual signal queued (silent): {symbol} {side} entry={entry} sl={sl} — "
            f"tracked internally, no extra channel post.{tracking_note}"
        )
    return True, (
        f"📌 Manual signal queued: {symbol} {side} entry={entry} sl={sl}\n\n"
        f"It'll be posted to the channel (with chart + 4 targets, same as automatic "
        f"signals) within a few minutes by the candle engine — after a quick live-price "
        f"sanity check (skipped and flagged to admins if the price has already blown "
        f"through the stop or final target by then) — and its result will count in "
        f"/results.{tracking_note}"
    )


CHANNEL_SIGNAL_TAG_RE = re.compile(
    r"#signal\s+([A-Z0-9]{2,15}/USD)\s+(BUY|SELL)\s+(1M|5M|15M|1H|4H)\s+([\d.]+)\s+([\d.]+)",
    re.IGNORECASE,
)

# فرمتی که خود candle_engine.py برای پیام سیگنال جدید می‌سازه (format_entry_message):
#   🟢 LONG — BTC 1h            یا           🔴 SHORT — SHIB 4H
#
#   Entry: 65000.00
#   ❌ Stop: 64000.00
#   🎯 Target 1: ...
#   ...
# چون فوروارد شما دقیقاً همین ساختار رو داره، به‌جای نیاز به تگ دستی، مستقیم از روی همین
# سه چیز (LONG/SHORT + نماد، Entry:، Stop:) استخراج می‌کنیم.
#
# ⚠️ قبلاً نماد فقط از بین BTC/ETH با \b(BTC|ETH)\b جستجو می‌شد - یعنی هر فوروارد روی یک
# نماد دیگه (مثلاً SHIB، DOGE و...) رد می‌شد، حتی اگه دقیقاً ساختار سیگنال خودمون
# (LONG/SHORT + Entry: + Stop:) رو داشت. الان نماد مستقیم از همون خط تیتر پیام - دقیقاً
# جایی که candle_engine.py می‌ذارتش، یعنی بلافاصله بعد از «LONG —» یا «SHORT —» - استخراج
# می‌شه، پس هر نمادی (نه فقط BTC/ETH) شناسایی می‌شه.
#
# 🔴 رفعِ باگِ اصلیِ ریشه‌ای «نتایج ربات دقیق نیست / تعدادی از سیگنال‌ها اصلاً نمایش داده
# نمی‌شن / تایم‌فریم خالیه»: خودِ format_entry_message (هم در candle_engine.py هم در
# bot.py) قیمتِ Entry/Stop رو داخل تگ HTML بولد می‌ذاره - دقیقاً «Entry: <b>65000.00</b>»
# و «Stop: <b>64000.00</b>» (نه فقط «Entry: 65000.00» ساده‌ای که کامنت بالا و این دو regex
# فرض کرده بودن). یعنی این دو regex، از همون اول، برای «Entry:» یک رقم مستقیم بعدش انتظار
# داشتن ولی همیشه یک تگ «<b>» بین این دو بود - پس روی «هر» پیام سیگنال ورودی (چه سیگنال
# خودکارِ کانال آلتکوین که خودکار رله می‌شه، چه هر فوروارد دستی دیگه‌ای با همین فرمت)
# entry_m/stop_m همیشه None بودن، parse_forwarded_signal_text همیشه None برمی‌گردوند، و در
# نتیجه queue_manual_signal اصلاً صدا زده نمی‌شد - یعنی خودِ معامله هیچ‌وقت وارد
# manual_signals.json نمی‌شد (نه در candle_state.json به‌عنوان معامله‌ی باز، نه بعداً در
# trade_history.json به‌عنوان معامله‌ی بسته). پیام‌های نتیجه‌ی بعدی (تارگت/استاپ/...) هم
# چون معامله‌ی بازِ متناظری برای reply/match پیدا نمی‌کردن، بی‌اثر می‌موندن. جالب این‌که
# FORWARDED_RESULT_R_RE پایین‌تر (برای «Result so far: <b>...R</b>») از قبل درست همین تگ
# رو در نظر گرفته بود - یعنی نویسنده به مشکل تگ HTML آگاه بوده، فقط اینجا (که ساختار پیام
# فرق داره: Entry/Stop در پیام سیگنال ورودی بولد است، ولی در پیام‌های نتیجه خط «Entry X ·
# Now Y» بولد نیست) از قلم افتاده بود. رفع شد: یک تگ HTML اختیاری بین «Entry:»/«Stop:» و
# خودِ رقم اکنون مجاز است (با یا بدون آن هر دو کار می‌کند).
FORWARDED_HEADER_RE = re.compile(
    r"\b(LONG|SHORT)\b\s*(?:—|-|–)\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)",
    re.IGNORECASE,
)
FORWARDED_ENTRY_RE = re.compile(r"Entry:\s*(?:<[^>]+>\s*)?\$?([\d,]+\.?\d*)", re.IGNORECASE)
FORWARDED_STOP_RE = re.compile(r"Stop:\s*(?:<[^>]+>\s*)?\$?([\d,]+\.?\d*)", re.IGNORECASE)
SIGNAL_EMOJI_PREFIX = ("🟢", "🔴")  # همون ایموجی‌هایی که خودِ ربات برای LONG/SHORT استفاده می‌کنه

# ⚠️ استخراج شناسه‌ی منبع (به فرمت هشتگ، مثلاً #TC_004821 یا #ALT_000005) از متن هر پیامِ
# فوروارد/رله‌شده - چه پیام سیگنال ورودی چه پیام نتیجه (تارگت/استاپ/...) - چون _id_line در
# candle_engine.py این هشتگ رو در هر دوی این‌ها می‌ذاره. طبق درخواست صریح («مسیر هر سیگنال را
# از روی هشتگ‌های آن دنبال و محاسبه کن، به شرطی که تحت هیچ شرایطی مسیر هیچ سیگنالی گم نشود»):
# این ID دقیقاً همون چیزیه که کاربر زیر هر پیام کانال می‌بینه - با استخراج و نگه‌داشتنش از همون
# لحظه‌ی ورودی، همه‌ی مراحل بعدی (باز شدن معامله، تطبیق نتایج فوروارد‌شده در
# process_forwarded_results، و جستجوی /signal یا /admin_close_trade) می‌تونن دقیقاً همین یک ID
# رو دنبال کنن - نه یک حدسِ جداگانه (نزدیک‌ترین قیمت/تیکر) که ممکنه اشتباه بره یا هیچ‌وقت
# تطبیق پیدا نکنه.
#
# 🔴 طبق درخواست صریح، این regex از خوندنِ خط قدیمیِ «🆔 TC-004821» به خوندنِ مستقیمِ خودِ
# هشتگ («#TC_004821») تغییر کرد - چون _id_line در candle_engine.py دیگه اصلاً اون خط 🆔 رو
# نمی‌فرسته (فقط هشتگ، زیر خط تاریخ/زمان). آندرلاینِ داخل هشتگ اینجا به خط‌تیره برگردونده
# می‌شه تا _normalize_signal_id (که فرمت استاندارد «PREFIX-XXXXXX» رو انتظار داره) درست کار
# کنه؛ خودِ آن تابع هم از قبل هر دو جداکننده (- و _) رو می‌شناسه، پس این تبدیل صرفاً برای
# خوانایی/سازگاری با بقیه‌ی کدیه، نه یک ضرورت.
SOURCE_SIGNAL_ID_RE = re.compile(r"#([A-Za-z]{2,10}_\d{3,8})\b")


def _extract_source_signal_id(text: str) -> Optional[str]:
    m = SOURCE_SIGNAL_ID_RE.search(text or "")
    if not m:
        return None
    return _normalize_signal_id(m.group(1))



def starts_with_signal_emoji(text):
    """
    تشخیصِ مبتنی‌بر محتوا (نه متادیتای تلگرام): اگه پیام با همین ایموجی لانگ (🟢) یا
    شورت (🔴) شروع شده باشه، به‌عنوان یکی از سیگنال‌های ربات در نظر گرفته می‌شه - چه واقعاً
    فوروارد شده باشه چه مستقیم تایپ/پیست شده باشه. قبلاً این تشخیص فقط روی پیام‌هایی اعمال
    می‌شد که تلگرام متادیتای «فوروارد» روشون گذاشته بود؛ ولی بعضی حالت‌ها (مثلاً کپی-پیست
    دستی، یا فوروارد از کانالی که «محافظت از محتوا» روشنه و تلگرام متادیتای فوروارد رو حذف
    می‌کنه) اون متادیتا رو نداشتن و رد می‌شدن. الان کافیه شکل ظاهری پیام مطابق سیگنال‌های
    خودمون باشه.

    نکته: قبل از چک، کاراکترهای نامرئی رایج (zero-width space/joiner، BOM، RTL/LTR mark) از
    ابتدای متن حذف می‌شن - بعضی کلاینت‌ها یا فوروارد از منابع خاص این‌ها رو قبل از ایموجی
    اضافه می‌کنن و باعث می‌شد startswith درست تشخیص نده.
    """
    t = (text or "").strip()
    t = t.lstrip("\u200b\u200c\u200d\u200e\u200f\ufeff ")
    return t.startswith(SIGNAL_EMOJI_PREFIX)


def is_forwarded_message(post):
    """
    فقط برای لاگ/اطلاع - آیا تلگرام این پیام رو واقعاً به‌عنوان فوروارد علامت زده یا نه
    (هم فیلد جدید forward_origin و هم فیلدهای قدیمی‌تر). دیگه شرط لازم برای تشخیص سیگنال
    نیست (به starts_with_signal_emoji نگاه کنید)، فقط برای توضیح بیشتر در تاییدیه استفاده می‌شه.
    """
    return bool(
        post.get("forward_origin")
        or post.get("forward_date")
        or post.get("forward_from")
        or post.get("forward_from_chat")
        or post.get("forward_sender_name")
    )


def parse_forwarded_signal_text(text):
    """
    از متن پیام (فوروارد‌شده یا مستقیم تایپ‌شده، فرقی نداره) که با ایموجی لانگ/شورت شروع
    شده، ساختار سیگنال رو استخراج می‌کنه: خط تیتر LONG/SHORT — نماد [تایم‌فریم اختیاری] +
    خط Entry: + خط Stop:. اگه Entry/Stop پیدا نشه None برمی‌گردونه (یعنی با این‌که با ایموجی
    شروع شده، ساختار کامل سیگنال رو نداره - پس دست‌نخورده رد می‌شه، نه اینکه با داده‌ی ناقص
    صف بشه).

    نماد دیگه محدود به BTC/ETH نیست - هر تیکری که توی خط تیتر بعد از LONG/SHORT بیاد
    (مثلاً SHIB، DOGE، ...) به‌صورت TICKER/USD ساخته می‌شه و مستقیم به queue_manual_signal
    داده می‌شه که خودش با SYMBOL_FORMAT_RE اعتبارسنجی نهایی رو انجام می‌ده.

    ⚠️ اگه بعد از نماد یک توکن دیگه هم روی همون خط باشه (مثلاً «— DOGE 5M»)، به‌عنوان
    تایم‌فریم اعلام‌شده توسط منبع در نظر گرفته می‌شه و به candle_engine.py پاس داده می‌شه -
    قبلاً این بخش کلاً نادیده گرفته می‌شد و هر سیگنال دستی/فوروارد بدون هیچ برچسب تایم‌فریمی
    پست می‌شد (حتی وقتی خودِ منبع مشخصاً یکی اعلام کرده بود).

    🔴 اضافه شد: عنصر ششمِ خروجی، source_signal_id - اگه پیام یک خط 🆔 (مثلاً «🆔 ALT-000005»)
    داشته باشه، همینجا استخراج می‌شه تا بعداً به‌جای ساختن یک signal_id تازه، همون‌یکی
    مستقیماً روی معامله‌ی محلی گذاشته بشه (نگاه کنید open_new_trade::preferred_signal_id در
    candle_engine.py) - یعنی شناسه‌ای که کاربر زیر پیام کانال می‌بینه همیشه دقیقاً همونیه که
    /signal یا /admin_close_trade هم می‌شناسدش.
    """
    if not text:
        return None
    header_m = FORWARDED_HEADER_RE.search(text)
    entry_m = FORWARDED_ENTRY_RE.search(text)
    stop_m = FORWARDED_STOP_RE.search(text)
    if not (header_m and entry_m and stop_m):
        return None

    side = "BUY" if header_m.group(1).upper() == "LONG" else "SELL"
    symbol = f"{header_m.group(2).upper()}/USD"
    tf_label = header_m.group(3)  # اختیاری - ممکنه None باشه
    try:
        entry = float(entry_m.group(1).replace(",", ""))
        sl = float(stop_m.group(1).replace(",", ""))
    except ValueError:
        return None
    source_signal_id = _extract_source_signal_id(text)
    return symbol, side, entry, sl, tf_label, source_signal_id


# ⚠️ همون‌طور که پیام سیگنال ورودی از یک منبع بیرونی فوروارد می‌شه، ادامه‌ی همون رشته (رسیدن
# به هر تارگت، خوردن استاپ، بریک‌ایون، بسته‌شدن رانر) هم فوروارد می‌شه - و چون این پیام‌ها هم
# دقیقاً با همون فرمتی نوشته شدن که خودِ candle_engine.py برای پیام‌های خروجش استفاده می‌کنه
# (format_rr_exit_message / format_stop_message / format_breakeven_message / ...)، می‌شه
# مستقیم از روی متن‌شون نتیجه رو استخراج کرد - به‌جای اینکه ربات مجبور باشه با قیمت زنده‌ی
# خودش (که ممکنه اصلاً این نماد رو نتونه دنبال کنه) دوباره همون چیزی رو حدس بزنه.
FORWARDED_RESULT_HEADER_PATTERNS = [
    ("target_hit", re.compile(r"✅\s*Target\s*\d+\s*HIT\s*\((\d+)R\)\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
    ("stop", re.compile(r"❌\s*STOP HIT\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
    ("breakeven", re.compile(r"⚪\s*BREAKEVEN\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
    ("sl_after_t2", re.compile(r"🔒\s*STOP AFTER TARGET 2\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
    ("sl_after_t3", re.compile(r"🔒\s*STOP AFTER TARGET 3\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
    ("runner_stop", re.compile(r"🏁\s*RUNNER CLOSED\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
    ("forwarded_closed", re.compile(r"⚠️\s*TRADE CLOSED\s*—\s*([A-Za-z0-9]{2,15})(?:[ \t]+(\S+))?(?=\r?\n|$)", re.IGNORECASE)),
]
FORWARDED_RESULT_ENTRY_RE = re.compile(r"Entry(?:\s*was)?\s*\$?([\d,]+\.?\d*)", re.IGNORECASE)
FORWARDED_RESULT_R_RE = re.compile(r"Result(?:\s*so far)?:\s*~?(?:<b>)?([+-]?\d+\.?\d*)R", re.IGNORECASE)


def parse_forwarded_result_text(text):
    """
    از متن یک پیامِ نتیجه (رسیدن به تارگت/خوردن استاپ/بریک‌ایون/رانر/...) که با فرمت پیام‌های
    خروج خودِ candle_engine.py نوشته شده، رویداد رو استخراج می‌کنه. اگه با هیچ‌کدوم از این
    قالب‌های شناخته‌شده مطابقت نداشت None برمی‌گردونه (یعنی این یک پیام نتیجه نیست، دست‌نخورده
    رد می‌شه - مثلاً یک اعلامیه‌ی عادی یا عکس بدون این ساختار)."""
    if not text:
        return None

    kind = ticker = tf_label = level = None
    for k, pat in FORWARDED_RESULT_HEADER_PATTERNS:
        m = pat.search(text)
        if m:
            kind = k
            if k == "target_hit":
                level = int(m.group(1))
                ticker = m.group(2).upper()
                tf_label = m.group(3)
            else:
                ticker = m.group(1).upper()
                tf_label = m.group(2)
            break
    if not kind:
        return None

    entry = None
    m = FORWARDED_RESULT_ENTRY_RE.search(text)
    if m:
        try:
            entry = float(m.group(1).replace(",", ""))
        except ValueError:
            entry = None

    result_r = None
    m = FORWARDED_RESULT_R_RE.search(text)
    if m:
        try:
            result_r = float(m.group(1))
        except ValueError:
            result_r = None

    # 🔴 اضافه شد: همون‌طور که parse_forwarded_signal_text شناسه‌ی سیگنالِ ورودی رو استخراج
    # می‌کنه، اینجا هم شناسه‌ی همون سیگنال از پیام نتیجه (که _id_line در candle_engine.py
    # همیشه توش می‌ذاره) استخراج می‌شه - تا process_forwarded_results بتونه اول با تطبیقِ
    # دقیقِ signal_id امتحان کنه، نه فقط heuristic قدیمیِ ticker+نزدیک‌ترین entry.
    source_signal_id = _extract_source_signal_id(text)

    return {"kind": kind, "ticker": ticker, "tf_label": tf_label, "entry": entry, "level": level,
            "result_r": result_r, "source_signal_id": source_signal_id}


def queue_forwarded_result(ev):
    """رویداد پارس‌شده رو توی forwarded_results_queue.json صف می‌کنه تا candle_engine.py
    (صاحب واقعی candle_state.json/trade_history.json) توی دور اسکن بعدیش اعمالش کنه - همون
    الگوی صف‌کردن که manual_signals.json برای سیگنال‌های ورودی استفاده می‌شه."""
    queue = _load("forwarded_results_queue.json", [])
    queue.append({
        "kind": ev["kind"], "ticker": ev["ticker"], "tf_label": ev.get("tf_label"),
        "entry": ev.get("entry"), "level": ev.get("level"), "result_r": ev.get("result_r"),
        "source_signal_id": ev.get("source_signal_id"),
        "status": "pending", "attempts": 0,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    })
    queue = queue[-500:]  # جلوگیری از رشد بی‌نهایت فایل
    _save("forwarded_results_queue.json", queue)


def is_duplicate_signal(symbol, side, entry, sl, window_seconds=6 * 3600):
    """
    جلوگیری از صف‌شدن دوباره‌ی همون سیگنال — مثلاً اگه یک پیام یک‌بار با تگ #signal و یک‌بار
    به‌صورت فوروارد خام دوباره پست بشه، یا خودِ پیام قدیمی کانال دوباره فوروارد بشه.
    """
    signals = _load("manual_signals.json", [])
    cutoff = now_ts() - window_seconds
    for s in signals:
        try:
            same = (
                s.get("symbol") == symbol and s.get("side") == side
                and abs(float(s.get("entry", 0)) - entry) < 1e-6
                and abs(float(s.get("sl", 0)) - sl) < 1e-6
                and s.get("created_at", 0) >= cutoff
            )
        except (TypeError, ValueError):
            same = False
        if same:
            return True
    return False


def handle_channel_signal_post(post):
    """
    قبلاً: سیگنال‌هایی که ادمین‌ها مستقیم (یا با فوروارد) توی خود کانال خصوصی پست می‌کردن
    اصلاً دیده نمی‌شدن، چون حلقه‌ی handle_updates فقط آپدیت‌های نوع "message" (چت خصوصی/گروه)
    رو می‌خوند و "channel_post" رو کلاً نادیده می‌گرفت؛ پس نه رصدی روی حدضرر/تارگت انجام
    می‌شد، نه نتیجه‌ای توی /results حساب می‌شد.

    دو مسیر برای صف‌شدن یک سیگنال از توی کانال:

    ۱) تگ صریح #signal — برای وقتی خودتون دستی متن سیگنال رو می‌نویسید یا فرمت منبع فوروارد
       ناشناخته/متفاوته:
           #signal BTC/USD BUY 65000 64000

    ۲) تشخیص خودکار بر اساس محتوا — هر پیامی (فوروارد شده یا مستقیم پیست/تایپ‌شده، فرقی
       نداره) که با همون ایموجی لانگ (🟢) یا شورت (🔴) شروع بشه و ساختار سیگنال خودمون رو
       داشته باشه (خط تیتر LONG/SHORT — نماد، خط Entry:، خط Stop:)، خودکار به‌عنوان یکی از
       سیگنال‌های ربات شناخته و صف می‌شه - دقیقاً مثل بقیه‌ی سیگنال‌ها: چارت و پیام
       تارگت/استاپ منتشر می‌شه، و در نتایج با نام خودِ همون ارز (نه یک برچسب جدا) حساب
       می‌شه. نماد دیگه محدود به BTC/ETH نیست - هر تیکری (SHIB، DOGE، ...) که توی خط تیتر
       باشه پذیرفته می‌شه. پیام‌هایی که این ایموجی رو ندارن (اعلامیه، فوروارد بی‌ربط)
       دست‌نخورده رد می‌شن.

       ⚠️ اگه پیام واقعاً فوروارد تلگرامیه (نه تایپ مستقیم)، فرض می‌کنیم ادامه‌ی همون رشته
       (پیام‌های رسیدن به تارگت/خوردن استاپ) هم بعداً فوروارد می‌شه - پس رصدِ زنده‌ی خودمون
       رو روی این معامله فعال نمی‌کنیم؛ نتیجه‌اش رو از همون پیام‌های بعدی می‌خونیم (مسیر ۳).

    ۳) پیام نتیجه‌ی فوروارد‌شده (رسیدن به تارگت/استاپ/بریک‌ایون/رانر) — وقتی متن دقیقاً با
       فرمت پیام‌های خروج خودِ candle_engine.py مطابقت داره (چون این پیام‌ها هم از همون منبع
       فوروارد می‌شن)، بدون نیاز به هیچ تگی خودکار شناسایی و صف می‌شه. برخلاف مسیر ۲، اینجا
       هیچ پیامی به کانال ارسال نمی‌شه (چون خودِ فوروارد از قبل توی کانال دیده می‌شه) - فقط
       معامله‌ی باز متناظرش (اگه پیدا بشه) به‌روز می‌شه: اگه فقط رسیدن به یک تارگته، باز
       می‌مونه (توی «معاملات باز» دیده می‌شه)؛ اگه نوع بسته‌شدنه (استاپ/بریک‌ایون/رانر/...)،
       فوراً وارد trade_history می‌شه و در /results و گزارش روزانه حساب می‌شه.

    مسیر ۱ و ۲ دقیقاً از همون تابع queue_manual_signal (مسیر /admin_manual_signal) استفاده
    می‌کنن، پس نتیجه یکسانه: پست استاندارد با چارت + ۴ تارگت، رصد زنده (یا رصد از روی فوروارد
    برای مسیر ۲‌ی واقعاً فوروارد‌شده)، و شمارش در /results.
    """
    chat = post.get("chat") or {}
    if str(chat.get("id")) != str(PRIVATE_CHANNEL_ID):
        return

    text = (post.get("text") or post.get("caption") or "")
    message_id = post.get("message_id")

    # مسیر ۱: تگ صریح #signal
    if "#signal" in text.lower():
        m = CHANNEL_SIGNAL_TAG_RE.search(text)
        if not m:
            send_message(
                chat["id"],
                "⚠️ #signal tag found but the format is wrong.\n"
                "Correct format: <code>#signal BTC/USD BUY 1H 65000 64000</code>\n"
                "(symbol, side, timeframe [1M/5M/15M/1H/4H], entry, stop-loss — space separated)",
                reply_to_message_id=message_id,
            )
            return
        symbol, side, tf_label, entry_s, sl_s = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        ok, note = queue_manual_signal(symbol, side, entry_s, sl_s, tf_label=tf_label)
        send_message(chat["id"], note, reply_to_message_id=message_id)
        return

    # مسیر ۲: پیامی که با ایموجی لانگ/شورت شروع شده (فوروارد یا مستقیم، فرقی نداره)
    if starts_with_signal_emoji(text):
        parsed = parse_forwarded_signal_text(text)
        if not parsed:
            send_message(
                chat["id"],
                "⚠️ This starts with the signal emoji but I couldn't find a clear "
                "<code>LONG —</code>/<code>SHORT —</code> symbol header plus both an "
                "<code>Entry:</code> line and a <code>Stop:</code> line — skipped. "
                "Use <code>#signal SHIB/USD BUY 0.00000493 0.00000431</code> "
                "if you want to enter it manually.",
                reply_to_message_id=message_id,
            )
            return
        symbol, side, entry, sl, tf_label, source_signal_id = parsed
        if is_duplicate_signal(symbol, side, entry, sl):
            send_message(
                chat["id"],
                f"↩️ This looks like a duplicate of a signal already queued/active "
                f"({symbol} {side} entry={entry}) — skipped to avoid double-tracking.",
                reply_to_message_id=message_id,
            )
            return
        forwarded = is_forwarded_message(post)
        ok, note = queue_manual_signal(symbol, side, str(entry), str(sl), forwarded_tracking=forwarded,
                                        tf_label=tf_label, source_signal_id=source_signal_id)
        # ⚠️ طبق درخواست کاربر: وقتی این مسیر منبعش یک ربات پرحجم دیگه‌ست که مدام پیام‌های
        # کانال دیگه‌ای رو کپی‌پیست می‌کنه (نه فقط فوروارد گاه‌به‌گاه یک ادمین)، یک ریپلای
        # تاییدی برای هر سیگنال موفق («🔎 Detected as a ... signal») کانال رو شلوغ و پرنویز
        # می‌کنه، در حالی که خودِ سیگنال (با چارت/تارگت/استاپ) توسط process_manual_signals
        # جداگانه پست می‌شه - یعنی این ریپلای برای موفقیت اصلاً چیز جدیدی اضافه نمی‌کنه.
        # پس فقط روی شکست (ok=False - اعتبارسنجی ناموفق) ریپلای می‌فرستیم که مشکل واقعاً
        # دیده بشه؛ موفقیت کاملاً ساکته - «بدون هیچ ریکشنی، فقط دقیق محاسبه بشه».
        if not ok:
            send_message(chat["id"], f"⚠️ Signal detected but couldn't be queued: {note}", reply_to_message_id=message_id)
        return

    # مسیر ۳: پیام نتیجه (رسیدن به تارگت/استاپ/بریک‌ایون/رانر) که فوروارد شده - سکوت کامل
    # چه موفق چه ناموفق: هیچ پاسخی داده نمی‌شه، چون این نوع پیام‌ها می‌تونن پشت‌سرهم و زیاد
    # (تا ۵-۶ تا برای یک معامله) بیان و پرشدن کانال از پاسخ‌های ربات زیر هرکدوم دقیقاً همون
    # چیزیه که ادمین نمی‌خواد (پیام‌های خودش نباید مزاحمت ببینه).
    result_ev = parse_forwarded_result_text(text)
    if result_ev:
        queue_forwarded_result(result_ev)
        return


DAILY_REPORT_RELAY_SKIP_RE = re.compile(r"Daily\s*Report|DAILY\s*SIGNAL\s*PERFORMANCE", re.IGNORECASE)


def handle_altcoin_relay_post(post, relay_seen_ids, relay_msgid_map):
    """
    آینه‌ی خودکارِ کانال آلتکوین: هر دو کانال متعلق به خودِ کاربره (ادمین هر دو). این تابع
    جایگزینِ «ادمین دستی فوروارد کنه» است - همون کاری که handle_channel_signal_post برای
    فوروارد دستی انجام می‌داد را برای یک منبعِ دائمی و پرحجم (کانال آلتکوینِ خودِ کاربر که
    دیپلوی جداگانه‌ی همین سیستم است) خودکار می‌کند.

    سه کار، به همین ترتیب، برای هر پیامِ کانال منبع:
      ۱) اگه قبلاً دیده شده (message_id تکراری - مثلاً به‌خاطر ری‌استارت/آفست) هیچ کاری نکن.
      ۲) اگه گزارش روزانه‌ی خودِ کانال ۲ است، رد کن (کپی نمی‌شه - طبق درخواست کاربر).
      ۳) وگرنه با copyMessage عیناً (بدون برچسب Forwarded from) در PRIVATE_CHANNEL_ID پست کن،
         و اگه ساختار سیگنال ورودی یا پیام نتیجه داره، در همون خط‌لوله‌ی محاسباتی موجود ثبتش
         کن - دقیقاً مثل مسیر ۲/۳ در handle_channel_signal_post.

    ⚠️ تفاوت کلیدی با فوروارد دستی: اونجا forwarded_tracking از روی متادیتای واقعیِ فوروارد
    تلگرام (is_forwarded_message) تشخیص داده می‌شد، چون معلوم نبود منبع واقعاً «رشته»ی کامل
    (سیگنال + همه‌ی پیام‌های بعدیِ نتیجه) رو هم می‌فرسته یا نه. اینجا این تضمین صد در صد
    برقراره (این تابع خودش، برای هر پیامی که از این کانال بیاد، هم منبع سیگنال است هم منبع
    قطعیِ نتایج بعدی‌اش) - پس همیشه forwarded_tracking=True پاس داده می‌شه، فارغ از اینکه
    خودِ copyMessage متادیتای فوروارد تلگرامی می‌ذاره یا نه (نمی‌ذاره). نتیجه: این معاملات
    هرگز با قیمت زنده‌ی خودمون رهگیری نمی‌شن (نه REST نه WS) - فقط از پیام‌های بعدیِ همین
    کانال می‌خونیم، یعنی دقیقاً همون عددی که کانال منبع (با همون دقت/همون لحظه) اعلام کرده،
    نه یک تخمین مستقل که ممکنه به‌خاطر تفاوت منبع قیمت/تاخیر شبکه کمی فرق کنه.

    ⚠️ سیگنال‌ها با silent=True صف می‌شن: چون خودِ پیامِ اصلی (با چارت/متن کامل، عیناً همون
    چیزی که کانال ۲ پست کرده) همین الان با copyMessage منتشر شده، یک پستِ دومِ بازتولیدشده
    توسط process_manual_signals فقط تکراری/گیج‌کننده می‌بود.

    ⚠️ حفظ ریپلای/کوتیشن: candle_engine.py پیام‌های نتیجه (تارگت/استاپ/بریک‌ایون/...) رو با
    reply_to_message_id به پیام سیگنالِ همون معامله ریپلای می‌کنه (همون نوار آبی/کوتیشن بالای
    هر نتیجه که کاربر می‌بینه) - ولی copyMessage خودش این ریپلای رو به مقصد منتقل نمی‌کنه
    (چون message_id مقصد با مبدأ فرق داره). برای همین یک نگاشتِ message_id مبدأ→مقصد
    (relay_msgid_map) نگه می‌داریم: هر پیامی که خودش در کانال منبع ریپلای به پیام دیگه‌ای بود
    (post["reply_to_message"])، اگه اون پیامِ اصلی رو قبلاً کپی کرده باشیم، همون ریپلای رو با
    reply_to_message_id به کپیِ مقصد وصل می‌کنیم - در نتیجه در کانال ۱ هم دقیقاً همون
    کوتیشن/نوار آبی زیر هر نتیجه دیده می‌شه، بدون هیچ تفاوت بصری با سیگنال‌های خودِ ربات.

    هیچ ریپلای/پیام تاییدی‌ای فرستاده نمی‌شه (نه برای موفقیت، نه برای شکست پارس) - این کانال
    قراره کاملاً هم‌شکلِ سیگنال‌های خودِ ربات به نظر برسه، بدون هیچ ردی از اینکه یک رله در
    کار است.
    """
    chat_id = post.get("chat", {}).get("id")
    message_id = post.get("message_id")
    seen_key = f"{chat_id}:{message_id}"
    if seen_key in relay_seen_ids:
        return
    # اول علامت می‌زنیم که «دیده شده»، بعد پردازش - اگه هر جای زیر خطا بده، دیگه دوباره
    # copyMessage/صف نمی‌شه (نهایتاً یک پیام گم می‌شه، نه دوبار پست/محاسبه می‌شه - خطای گم‌شدن
    # قابل جبران با هشدار به ادمینه، خطای تکرار قابل جبران نیست).
    relay_seen_ids.add(seen_key)

    text = post.get("text") or post.get("caption") or ""

    if text.strip().startswith(("📋", "📊")) and DAILY_REPORT_RELAY_SKIP_RE.search(text):
        return  # گزارش روزانه‌ی کانال ۲ - کپی نمی‌شه

    dest_reply_to = None
    src_reply = post.get("reply_to_message")
    if src_reply:
        dest_reply_to = relay_msgid_map.get(str(src_reply.get("message_id")))

    try:
        copy_kwargs = {"chat_id": PRIVATE_CHANNEL_ID, "from_chat_id": chat_id, "message_id": message_id}
        if dest_reply_to:
            copy_kwargs["reply_to_message_id"] = dest_reply_to
        copy_result = tg("copyMessage", **copy_kwargs)
    except Exception as e:
        copy_result = {"ok": False, "description": str(e)}
    if not copy_result.get("ok"):
        print(f"[error] Altcoin relay: copyMessage failed for {seen_key}: {copy_result}")
        alert_admins_text(
            f"⚠️ Altcoin relay: copyMessage failed for source message {seen_key} — this message "
            f"was NOT mirrored into the channel and will not be retried (offset already "
            f"advanced): {copy_result}")
        return

    dest_message_id = (copy_result.get("result") or {}).get("message_id")
    if dest_message_id:
        # فقط برای پیام‌هایی نگهش می‌داریم که ممکنه بعداً یک نتیجه به‌شون ریپلای بزنه - یعنی
        # عملاً پیام‌های سیگنال ورودی (که همیشه چارت/عکس دارن)؛ ولی برای سادگی همه رو ثبت
        # می‌کنیم چون سایز فایل هرحال با cap کنترل می‌شه (پایین‌تر در save_all)
        relay_msgid_map[str(message_id)] = dest_message_id

    if starts_with_signal_emoji(text):
        parsed = parse_forwarded_signal_text(text)
        if parsed:
            symbol, side, entry, sl, tf_label, source_signal_id = parsed
            if not is_duplicate_signal(symbol, side, entry, sl):
                # 🔴 اضافه شد: source_signal_id (استخراج‌شده از خط 🆔 خودِ متن منبع، مثلاً
                # «ALT-000005») اینجا پاس داده می‌شه. چرا اینجا حیاتی‌تر از هرجای دیگه‌ست:
                # این مسیر (رله‌ی خودکار آلتکوین) دقیقاً همون مسیریه که copyMessage متن اصلی
                # رو عیناً (با 🆔 ALT-000005 خودش) توی کانال می‌ذاره، بدون هیچ پستِ دومِ محلی
                # (silent=True پایین‌تر) - یعنی تنها ID ای که کاربر واقعاً می‌بینه همون
                # source_signal_id است. قبلاً اینجا اصلاً پاس داده نمی‌شد، پس open_new_trade
                # همیشه یک TC-XXXXXX تازه می‌ساخت که هیچ‌جا در کانال دیده نمی‌شد - این دقیقاً
                # همون چیزیه که باعث می‌شد /admin_close_trade ALT-000005 یا تطبیقِ نتایج
                # فوروارد‌شده (که حالا هم اول از روی همین ID دقیق امتحان می‌شه) شکست بخوره.
                ok, note = queue_manual_signal(symbol, side, str(entry), str(sl),
                                                forwarded_tracking=True, tf_label=tf_label, silent=True,
                                                source_signal_id=source_signal_id)
                if not ok:
                    # ⚠️ این مسیر copyMessage رو از قبل (بالاتر) انجام داده - یعنی خودِ متن
                    # سیگنال با چارتش همین الان توی کانال دیده می‌شه. اما اگه اینجا رد بشه
                    # (مثلاً چون تایم‌فریم نداشت)، دیگه هیچ رهگیری/محاسبه‌ای براش انجام
                    # نمی‌شه - قبلاً این حالت کاملاً بی‌صدا از دست می‌رفت (نه پیامی، نه
                    # هشداری)، الان لااقل به ادمین‌ها اطلاع داده می‌شه.
                    alert_admins_text(
                        f"⚠️ Altcoin relay signal {symbol} {side} entry={entry} was mirrored into the "
                        f"channel but NOT tracked (no /results/history entry, no target/stop follow-up): {note}")
        return

    result_ev = parse_forwarded_result_text(text)
    if result_ev:
        queue_forwarded_result(result_ev)


def handle_admin_command(chat_id, text, subscribers, discounts, maintenance, photo_submissions,
                          pending, applied, referrals):
    parts = text.split()
    cmd = parts[0]

    if cmd in ("/admin_list", "/admin"):
        # 🔴 رفعِ یک باگِ واقعیِ مقیاس: قبلاً همه‌ی مشترکین رو توی یک پیام خام می‌ریخت. با
        # هزاران کاربر، این پیام از سقف ۴۰۹۶ کاراکتریِ تلگرام رد می‌شه و sendMessage با خطا
        # شکست می‌خوره - و چون send_message شکست رو فقط لاگ می‌کنه، ادمین هیچی نمی‌بینه (نه
        # خطا، نه لیست) - از دیدش انگار دستور اصلاً کار نکرد. الان صفحه‌بندی شده (۵۰ کاربر
        # در هر صفحه، صریحاً با /admin_list [page]) تا در هر مقیاسی همیشه قابل‌استفاده بمونه.
        if not subscribers:
            send_message(chat_id, "No active subscribers.\n\n" + ADMIN_HELP)
            return
        page = 0
        if len(parts) > 1:
            try:
                page = max(0, int(parts[1]) - 1)
            except ValueError:
                pass
        page_size = 50
        total_pages = max(1, (len(subscribers) + page_size - 1) // page_size)
        page = min(page, total_pages - 1)
        chunk = subscribers[page * page_size:(page + 1) * page_size]
        lines = [f"<code>{s['user_id']}</code> — until {fmt_date(s['expiry_ts'])}" for s in chunk]
        header = f"<b>Active subscribers</b> ({len(subscribers)} total) — page {page + 1}/{total_pages}\n"
        footer = f"\n\nUse /admin_list {page + 2} for next page." if page + 1 < total_pages else ""
        footer += "\nUse /admin_user &lt;user_id&gt; to look up one person in detail."
        send_message(chat_id, header + "\n".join(lines) + footer)
        return

    if cmd == "/admin_user":
        # 🔴 قابلیت مستقیماً برای «هر اتفاقی که ممکنه برای یک کاربر پیش بیاد، ادمین بتونه
        # پیگیری کنه» - به‌جای گشتن دستی توی یک لیست هزاران‌نفره، جزئیات کامل یک کاربر خاص.
        if len(parts) != 2:
            send_message(chat_id, "Usage: /admin_user <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            send_message(chat_id, "user_id must be a number.")
            return
        sub = _find_sub(subscribers, target_id)
        ref_count = sum(1 for r in referrals.values() if r == target_id) if isinstance(referrals, dict) else 0
        disc = applied.get(str(target_id))
        lines = [f"👤 <b>User {target_id}</b>\n"]
        if sub:
            active = sub["expiry_ts"] > now_ts()
            lines.append(f"Subscription: {'✅ active' if active else '❌ expired'} until {fmt_date(sub['expiry_ts'])}")
        else:
            lines.append("Subscription: no record (never subscribed, or already removed)")
        lines.append(f"Referrals credited to this user: {ref_count}")
        if disc:
            lines.append(f"Discount code on file: {disc.get('code')} ({disc.get('percent')}%)")
        pending_purchase = [p for p in pending if p.get("user_id") == target_id]
        if pending_purchase:
            lines.append(f"⏳ Pending purchase(s): {len(pending_purchase)} (unpaid/awaiting confirmation)")
        send_message(chat_id, "\n".join(lines))
        return

    if cmd == "/admin_help":
        send_message(chat_id, ADMIN_HELP)
        return

    if cmd == "/admin_status":
        # 🔴 طبق پیشنهاد: یک دستور واحد که همه‌ی چیزهایی که ادمین برای «آیا الان همه‌چیز
        # سالمه؟» نیاز داره رو یک‌جا نشون بده - قبلاً برای فهمیدن این‌ها باید چند دستور جدا
        # می‌زدی یا لاگ Actions رو باز می‌کردی. چون subscription_bot.py و candle_engine.py
        # روی همون یک ریپو/چک‌اوت مشترک کار می‌کنن (هر دو از data/ زیر همون DATA_DIR
        # می‌خونن/می‌نویسن)، این دستور می‌تونه مستقیماً فایل‌های candle_engine.py رو هم
        # بخونه - بدون هیچ ارتباط مستقیم بین دو پروسه، فقط خواندن فایل مشترک.
        send_message(chat_id, format_admin_status_message())
        return

    if cmd in ("/admin_add", "/admin_extend"):
        if len(parts) != 3:
            send_message(chat_id, f"Usage: {cmd} <user_id> <days>")
            return
        try:
            target_id, days = int(parts[1]), int(parts[2])
        except ValueError:
            send_message(chat_id, "user_id and days must be numbers.")
            return
        sub = _find_sub(subscribers, target_id)
        base = sub["expiry_ts"] if sub and sub["expiry_ts"] > now_ts() else now_ts()
        new_expiry = base + days * 24 * 3600
        if sub:
            sub["expiry_ts"] = new_expiry
            sub["reminded"] = False
        else:
            subscribers.append({"user_id": target_id, "chat_id": target_id, "expiry_ts": new_expiry, "reminded": False})
        send_message(chat_id, f"Done. User {target_id} active until {fmt_date(new_expiry)}.")
        send_message(target_id, f"🎁 Your subscription was updated by an admin.\nNew expiry: {fmt_date(new_expiry)}")
        return

    if cmd == "/admin_reduce":
        if len(parts) != 3:
            send_message(chat_id, "Usage: /admin_reduce <user_id> <days>")
            return
        try:
            target_id, days = int(parts[1]), int(parts[2])
        except ValueError:
            send_message(chat_id, "user_id and days must be numbers.")
            return
        sub = _find_sub(subscribers, target_id)
        if not sub:
            send_message(chat_id, "That user has no active subscription.")
            return
        sub["expiry_ts"] -= days * 24 * 3600
        if sub["expiry_ts"] <= now_ts():
            remove_member(target_id)
            subscribers.remove(sub)
            send_message(chat_id, f"User {target_id}'s subscription reached zero and was removed.")
            send_message(target_id, "Your subscription has ended. Type /plans to resubscribe.")
        else:
            send_message(chat_id, f"Done. User {target_id} now active until {fmt_date(sub['expiry_ts'])}.")
            send_message(target_id, f"Your subscription was adjusted by an admin.\nNew expiry: {fmt_date(sub['expiry_ts'])}")
        return

    if cmd == "/admin_remove":
        if len(parts) != 2:
            send_message(chat_id, "Usage: /admin_remove <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            send_message(chat_id, "user_id must be a number.")
            return
        sub = _find_sub(subscribers, target_id)
        remove_member(target_id)
        if sub:
            subscribers.remove(sub)
        send_message(chat_id, f"User {target_id} removed.")
        send_message(target_id, "Your membership has been removed by an admin.")
        return

    if cmd == "/admin_discount_add":
        if len(parts) < 3:
            send_message(chat_id, "Usage: /admin_discount_add <CODE> <percent> [max_uses] [days_valid]")
            return
        code = parts[1].upper()
        try:
            percent = float(parts[2])
            max_uses = int(parts[3]) if len(parts) > 3 else None
            days_valid = int(parts[4]) if len(parts) > 4 else None
        except ValueError:
            send_message(chat_id, "percent/max_uses/days_valid must be numbers.")
            return
        discounts[:] = [d for d in discounts if d["code"] != code]
        discounts.append({
            "code": code, "percent": percent, "max_uses": max_uses, "used": 0,
            "expires_at": (now_ts() + days_valid * 24 * 3600) if days_valid else None,
            "active": True,
        })
        send_message(chat_id, f"Discount <b>{code}</b> created: {percent}% off" +
                     (f", max {max_uses} uses" if max_uses else "") +
                     (f", valid {days_valid} days" if days_valid else ", no expiry") + ".")
        return

    if cmd == "/admin_discount_list":
        if not discounts:
            send_message(chat_id, "No discount codes.")
            return
        lines = []
        for d in discounts:
            exp = fmt_date(d["expires_at"]) if d.get("expires_at") else "never"
            uses = f"{d.get('used', 0)}/{d['max_uses']}" if d.get("max_uses") else f"{d.get('used', 0)}/∞"
            lines.append(f"<b>{d['code']}</b> — {d['percent']}% off, used {uses}, expires {exp}, active={d.get('active', True)}")
        send_message(chat_id, "\n".join(lines))
        return

    if cmd == "/admin_discount_remove":
        if len(parts) != 2:
            send_message(chat_id, "Usage: /admin_discount_remove <CODE>")
            return
        code = parts[1].upper()
        before = len(discounts)
        discounts[:] = [d for d in discounts if d["code"] != code]
        send_message(chat_id, f"Removed {code}." if len(discounts) < before else f"No such code: {code}")
        return

    if cmd == "/admin_prices":
        lines = ["💲 <b>Current plan prices</b>\n"]
        for k, p in PLANS.items():
            lines.append(f"<code>{k}</code> — {p['label']} ({p['days']}d): ${p['usd']}")
        lines.append("\nChange with: /admin_set_price &lt;plan_id&gt; &lt;usd&gt;")
        send_message(chat_id, "\n".join(lines))
        return

    if cmd == "/admin_set_price":
        # 🔴 قابلیت درخواست‌شده: تغییر مبلغ خرید اشتراک‌ها بدون نیاز به ویرایش کد/دیپلوی
        # مجدد. فقط usd قابل‌تغییره (نه days - توضیح کامل بالای تعریف PLANS)؛ مقدار در
        # data/plan_prices.json ذخیره می‌شه (atomic، از قبل مقاوم در برابر خرابی) و بلافاصله
        # هم روی خودِ PLANS در حافظه اعمال می‌شه - نیازی به ری‌استارت ربات نیست.
        if len(parts) != 3:
            send_message(chat_id, f"Usage: /admin_set_price <plan_id> <usd>\nValid plan_id: {', '.join(PLANS.keys())}")
            return
        plan_key, usd_s = parts[1], parts[2]
        if plan_key not in PLANS:
            send_message(chat_id, f"❌ Unknown plan_id '{plan_key}'. Valid: {', '.join(PLANS.keys())}")
            return
        try:
            usd = float(usd_s)
            if usd <= 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "❌ usd must be a positive number.")
            return
        overrides = _load("plan_prices.json", {})
        overrides[plan_key] = usd
        _save("plan_prices.json", overrides)
        PLANS[plan_key]["usd"] = usd  # فوری روی حافظه هم اعمال می‌شه - بدون نیاز به ری‌استارت
        send_message(chat_id, f"✅ {PLANS[plan_key]['label']} price updated to ${usd:g}. Takes effect immediately for new purchases.")
        return

    if cmd == "/admin_bulk_extend" or cmd == "/admin_bulk_reduce":
        if len(parts) < 2:
            send_message(chat_id, f"Usage: {cmd} <days> [reason...]")
            return
        try:
            days = int(parts[1])
        except ValueError:
            send_message(chat_id, "days must be a number.")
            return
        reason = text.split(maxsplit=2)[2] if len(parts) > 2 else "A subscription adjustment was made by an admin."
        if cmd == "/admin_bulk_reduce":
            days = -days
        total, removed, failed_notify = bulk_adjust_subscribers(subscribers, days, reason)
        fail_note = f" ({failed_notify} failed to notify)" if failed_notify else ""
        send_message(chat_id, f"Done. Adjusted {total} subscriber(s) by {days:+d} day(s). "
                               f"{removed} were removed (ran out){fail_note}.")
        return

    if cmd == "/admin_pause":
        if is_paused(maintenance):
            send_message(chat_id, "Already paused.")
            return
        reason = text.split(maxsplit=1)[1] if len(parts) > 1 else "scheduled maintenance"
        maintenance["paused"] = True
        maintenance["since"] = now_ts()
        maintenance["reason"] = reason
        notice = maintenance_notice_text(maintenance)
        # 🔴 قبلاً send_message خام توی حلقه (بدون فاصله/مدیریت 429) - با هزاران مشترک،
        # این broadcast قطعاً به سقف نرخ تلگرام می‌خورد و بخش زیادی از پیام‌ها بی‌صدا گم
        # می‌شدن. الان broadcast_send با فاصله‌گذاری + retry خودکار روی 429 استفاده می‌شه،
        # و تعداد واقعیِ موفق/ناموفق به ادمین گزارش می‌شه (نه فقط len(subscribers) خام).
        sent, failed = broadcast_send([s["chat_id"] for s in subscribers], notice)
        fail_note = f", {len(failed)} failed to notify (likely blocked the bot)" if failed else ""
        send_message(chat_id, f"⏸️ Paused. New signals stopped, no one will be removed for lapsed time, "
                               f"and {sent}/{len(subscribers)} subscriber(s) were notified{fail_note}.")
        return

    if cmd == "/admin_resume":
        if not is_paused(maintenance):
            send_message(chat_id, "Not currently paused.")
            return
        paused_seconds = now_ts() - (maintenance.get("since") or now_ts())
        extend_days = max(1, -(-paused_seconds // (24 * 3600)))  # سقف به بالا (حداقل ۱ روز)
        maintenance["paused"] = False
        maintenance["since"] = None
        maintenance["reason"] = ""
        note = f"We're back online! Your subscription was extended by {extend_days} day(s) to make up for the pause."
        total, removed, failed_notify = bulk_adjust_subscribers(subscribers, extend_days, note)
        fail_note = f" ({failed_notify} failed to notify)" if failed_notify else ""
        send_message(chat_id, f"▶️ Resumed. Everyone's subscription extended by {extend_days} day(s); {total} notified{fail_note}.")
        return

    if cmd == "/admin_close_trade":
        # 🔴 شیرِ اطمینانِ همیشگی برای معاملات گیرکرده (هر دلیلی که باشه - قابل‌حل بدون فیکس
        # کد): key ترجیحاً همون signal_id پایدار («TC-004821» - از هشدار stale_open_trade یا
        # خط 🆔 پیام کانال) است؛ کلید قدیمی‌تر (مثلاً «BTC/USD|4h» یا «MANUAL|m-1787774585»)
        # هم به‌عنوان fallback همچنان کار می‌کنه (candle_engine.py::process_admin_close_requests
        # اول با signal_id می‌گرده، بعد با کلید سطل). final_r اختیاریه - اگه ندی،
        # candle_engine.py دیگه صفر فرض نمی‌کنه، بلکه fair-value واقعی (بر اساس تارگت‌های
        # واقعاً رسیده) رو خودش محاسبه می‌کنه؛ اگه ادمین دلیلی داره که به این عدد خودکار
        # اعتماد نکنه (مثلاً می‌دونه قیمت واقعی جای دیگه‌ایه)، می‌تونه عدد دقیق رو صریحاً بده.
        if len(parts) not in (2, 3):
            send_message(chat_id, "Usage: /admin_close_trade <signal_id or key> [final_r]\n"
                                   "Prefer the signal_id from a stale_open_trade alert or the channel "
                                   "message's #hashtag (below its date/time), e.g. TC-004821 (the older key like BTC/USD|4h "
                                   "or MANUAL|m-1787774585 still works too).\n"
                                   "final_r: leave it out to auto-use the trade's own tracked fair-value "
                                   "result (based on targets actually hit) — or give an explicit number "
                                   "(e.g. -1 or 2.5) to override it.")
            return
        key = parts[1]
        final_r = None
        if len(parts) == 3:
            try:
                final_r = float(parts[2])
            except ValueError:
                send_message(chat_id, "❌ final_r must be a number, e.g. -1 or 2.5")
                return
        _queue_admin_close_request(key, final_r)
        final_r_desc = f"{final_r:+.2f}R (explicit)" if final_r is not None else "auto fair-value"
        send_message(chat_id, f"✅ Queued — candle_engine.py will close '{key}' ({final_r_desc}) "
                               f"within a few seconds. You'll get an alert if the key wasn't found.")
        return

    if cmd == "/admin_manual_signal":
        if len(parts) != 6:
            send_message(chat_id, "Usage: /admin_manual_signal <SYMBOL/USD> <BUY|SELL> <TF> <entry> <sl> "
                                   "(TF: 1M, 5M, 15M, 1H, or 4H — any symbol, e.g. BTC/USD, SHIB/USD)")
            return
        symbol, side, tf_label, entry_s, sl_s = parts[1].upper(), parts[2].upper(), parts[3].upper(), parts[4], parts[5]
        ok, note = queue_manual_signal(symbol, side, entry_s, sl_s, tf_label=tf_label)
        send_message(chat_id, note)
        return

    if cmd == "/admin_photos":
        n = 10
        if len(parts) == 2:
            try:
                n = int(parts[1])
            except ValueError:
                pass
        approved = [s for s in photo_submissions if s["status"] == "approved"]
        if not approved:
            send_message(chat_id, "No approved photos yet.")
            return
        for sub in approved[-n:]:
            try:
                tg("sendPhoto", chat_id=chat_id, photo=sub["file_id"],
                   caption=f"From user <code>{sub['user_id']}</code> · approved {sub['reviewed_at']}", parse_mode="HTML")
            except Exception as e:
                print(f"[warn] resend photo failed: {e}")
        return

    send_message(chat_id, "Unknown admin command.\n\n" + ADMIN_HELP)


# ================== main ==================

def alert_admins_text(text: str) -> None:
    """یک پیام متنی ساده به همه‌ی ادمین‌ها می‌فرسته - برای خطاهایی که فقط لاگ Actions کافی
    نیست و باید فوراً دیده بشن (مثلاً شکست git sync که باعث می‌شه داده‌ها بین دو ربات
    هماهنگ نشن)."""
    for admin_id in ADMIN_USER_IDS:
        try:
            send_message(admin_id, text)
        except Exception as e:
            print(f"[warn] failed to alert admin {admin_id}: {e}")


_GIT_ERROR_MESSAGES = {
    "not_a_repo": lambda msg: f"⚠️ subscription_bot.py: {msg}",
    "add_failed": lambda msg: f"⚠️ subscription_bot.py: git add data/ failed: {msg}",
    "commit_failed": lambda msg: f"⚠️ subscription_bot.py: git commit failed: {msg}",
    "rebase_conflict": lambda msg: f"⚠️ subscription_bot.py: {msg}",
    "push_failed": lambda msg: (
        f"⚠️ subscription_bot.py: {msg} Local data changes will NOT be visible to "
        f"candle_engine.py until this succeeds."),
}


def git_commit_and_push(final: bool = False):
    """Thin wrapper حول sync_data_dir مشترک (shared_git_sync.py) - همون git add/commit/
    pull --rebase (با retry فوری روی برخورد push، + resolve معنایی تعارض‌های آرایه‌ای) که
    candle_engine.py هم استفاده می‌کنه، تا این دو دیگه هیچ‌وقت دو نسخه‌ی مستقل/drift‌شده از
    همین منطق نداشته باشن (توضیح کامل در docstring خودِ shared_git_sync.py و کامنت بالای
    importش در این فایل).

    final=True فقط برای فراخوانیِ آخرِ حلقه‌ی اصلی، درست قبل از پایان Job - همون دلیلی که
    توی candle_engine.py هم اضافه شد: بعد از این فراخوانی دیگه دور بعدی‌ای نیست که خودش
    retry کنه، پس صبورتر عمل می‌کنیم (retry بیشتر، فاصله‌ی بلندتر) تا چیزی که تا همین
    لحظه push نشده، برای همیشه با پایان Job از بین نره."""
    repo_dir = os.path.dirname(os.path.abspath(__file__))

    def on_error(kind: str, message: str) -> None:
        print(f"[git] {kind}: {message}")
        text = _GIT_ERROR_MESSAGES.get(kind, lambda m: f"⚠️ subscription_bot.py: {m}")(message)
        if final:
            text += (" (این تلاشِ نهایی قبل از پایان Job بود - هرچی الان push نشده باشه، "
                      "تا اجرای بعدی هم دیگه در دسترس نیست و باید دستی بررسی بشه.)")
        alert_admins_text(text)

    if final:
        sync_data_dir(repo_dir, "update subscription data [skip ci]", on_error,
                       max_retries=10, retry_delay_range=(3, 8))
    else:
        sync_data_dir(repo_dir, "update subscription data [skip ci]", on_error)


def run_cycle(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions, timers,
              relay_seen_ids, relay_msgid_map):
    handle_updates(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
                    relay_seen_ids, relay_msgid_map)

    now = time.time()
    if pending and now - timers.get("last_payment_check", 0) >= PAYMENT_CHECK_INTERVAL_SECONDS:
        process_pending_payments(pending, subscribers, discounts, used_tx, referrals)
        timers["last_payment_check"] = now

    if now - timers.get("last_lifecycle_check", 0) >= LIFECYCLE_CHECK_INTERVAL_SECONDS:
        process_subscription_lifecycle(subscribers, maintenance)
        timers["last_lifecycle_check"] = now

    used_tx[:] = used_tx[-800:]


def save_all(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
             relay_seen_ids, relay_msgid_map):
    _save("state.json", state)
    _save("pending.json", pending)
    _save("subscribers.json", subscribers)
    _save("discounts.json", discounts)
    _save("applied_discounts.json", applied)
    _save("used_tx.json", used_tx)
    _save("referrals.json", referrals)
    _save("maintenance.json", maintenance)
    _save("photo_submissions.json", photo_submissions)
    # فقط ۵۰۰۰ تای آخر - جلوگیری از رشد بی‌نهایت فایل؛ کافیه چون فقط برای جلوگیری از
    # پردازش دوباره‌ی همون آپدیت‌های اخیر (بین ری‌استارت‌ها) لازمه، نه یک آرشیو دائمی
    _save("relay_seen_ids.json", list(relay_seen_ids)[-5000:])
    # همینطور فقط ۲۰۰۰ تای آخرِ نگاشتِ message_id مبدأ→مقصد - یک معامله معمولاً ظرف چند
    # ساعت بسته می‌شه، پس نگه‌داشتنِ نگاشت‌های خیلی قدیمی فایده‌ای نداره؛ اگه یک نتیجه به یک
    # سیگنالِ خیلی قدیمی‌تر از این کش ریپلای بزنه، فقط ریپلای بصری‌اش از دست می‌ره (نه خودِ
    # محاسبه - queue_forwarded_result با تیکر/entry تطبیق می‌ده، نه با ریپلای)
    trimmed_map = dict(list(relay_msgid_map.items())[-2000:])
    relay_msgid_map.clear()
    relay_msgid_map.update(trimmed_map)
    _save("relay_msgid_map.json", relay_msgid_map)


def main():
    state = _load("state.json", {"last_update_id": 0})
    pending = _load("pending.json", [])
    subscribers = _load("subscribers.json", [])
    discounts = _load("discounts.json", [])
    applied = _load("applied_discounts.json", {})
    used_tx = _load("used_tx.json", [])
    referrals = _load("referrals.json", {})
    maintenance = _load("maintenance.json", {"paused": False, "since": None, "reason": ""})
    photo_submissions = _load("photo_submissions.json", [])
    relay_seen_ids = set(_load("relay_seen_ids.json", []))
    relay_msgid_map = _load("relay_msgid_map.json", {})

    _apply_plan_price_overrides()
    setup_bot_menu()

    start = time.time()
    last_commit = start
    cycles = 0
    timers = {}

    print(f"🚀 Starting continuous loop — will run for up to {LOOP_MAX_SECONDS/3600:.1f}h, "
          f"long-polling every {LONG_POLL_SECONDS}s for instant responses.")

    while time.time() - start < LOOP_MAX_SECONDS:
        try:
            run_cycle(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
                      timers, relay_seen_ids, relay_msgid_map)
        except Exception as e:
            print(f"[error] run_cycle failed: {e}")
            time.sleep(5)  # جلوگیری از حلقه‌ی خطای سریع در صورت مشکل موقت شبکه

        cycles += 1
        if time.time() - last_commit >= GIT_COMMIT_EVERY_SECONDS:
            save_all(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
                     relay_seen_ids, relay_msgid_map)
            git_commit_and_push()
            send_heartbeat()
            last_commit = time.time()

    save_all(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
             relay_seen_ids, relay_msgid_map)
    git_commit_and_push(final=True)
    print(f"✅ Loop finished after {cycles} cycles, {(time.time()-start)/3600:.2f}h — "
          f"pending: {len(pending)}, subscribers: {len(subscribers)}")


if __name__ == "__main__":
    main()

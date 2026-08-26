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
import requests
from datetime import datetime, timezone

# ⚠️ منطق git add/commit/pull --rebase/push (با retry فوری روی برخورد push بین این اسکریپت
# و candle_engine.py) از اینجا میاد - قبلاً یک نسخه‌ی دستیِ جداگانه (بدون retry فوری) اینجا
# تعریف شده بود که از نسخه‌ی shared_git_sync.py (که خودِ همین کدبیس برای رفع همین drift ساخته
# بود) عقب افتاده بود؛ یعنی دقیقاً همون مشکلی که shared_git_sync.py قرار بود جلویش را بگیرد،
# چون آن ماژول نوشته شده بود ولی هیچ‌جا import/استفاده نمی‌شد. رفع شد.
from shared_git_sync import sync_data_dir, pull_latest_readonly

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
GIT_COMMIT_EVERY_SECONDS = 120                            # هر ۲ دقیقه تغییرات commit می‌شود
PAYMENT_CHECK_INTERVAL_SECONDS = 20   # چک پرداخت‌ها (TronGrid/BscScan) هر ۲۰ ثانیه، نه هر چرخه - تا پاسخ به پیام کاربر معطل درخواست‌های خارجی نشه
LIFECYCLE_CHECK_INTERVAL_SECONDS = 30  # چک انقضای اشتراک هم همین‌طور، جدا از پاسخ‌دهی پیام

# ================== پلن‌ها — قیمت کمی زیر میانگین بازار، با تخفیف پلکانی برای تشویق اشتراک بلندمدت ==================
PLANS = {
    "1m":  {"label": "1 Month",   "days": 30,  "usd": 39},
    "3m":  {"label": "3 Months",  "days": 90,  "usd": 99},   # ~15% off monthly rate
    "6m":  {"label": "6 Months",  "days": 180, "usd": 180},  # ~23% off
    "12m": {"label": "12 Months", "days": 365, "usd": 300},  # ~36% off
}

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
    "Use the buttons below to get started 👇"
)

HELP_TEXT = (
    "<b>Commands</b>\n\n"
    "/plans — view plans &amp; subscribe\n"
    "/status — your subscription status\n"
    "/results — signal performance stats\n"
    "/history — last 100 closed signals, in detail\n"
    "/pnl AMOUNT — estimate your P&amp;L (e.g. /pnl 100)\n"
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
    return [
        [{"text": "💎 View Plans", "callback_data": "menu:plans"}],
        [{"text": "📊 My Subscription", "callback_data": "menu:status"}],
        [{"text": "📈 Signal Results", "callback_data": "menu:results"}, {"text": "🧮 Calculate P&L", "callback_data": "menu:pnl"}],
        [{"text": "📜 Signal History", "callback_data": "hist:0"}],
        [{"text": "🤝 Refer a Friend", "callback_data": "menu:refer"}],
        [{"text": "💬 Support", "callback_data": "menu:support"}, {"text": "⚠️ Disclaimer", "callback_data": "menu:disclaimer"}],
    ]

# ================== توابع کمکی JSON ==================

def _load(name, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    return {
        "total": total, "wins": wins, "breakeven": breakeven, "losses": losses,
        "total_r": total_r, "avg_r": total_r / total, "win_rate": wins / total * 100,
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
from shared_risk_config import RR_TARGETS as _RR_TARGETS, compute_final_r as _live_r


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
        lines.append(f"  {arrow} {symbol_part} — {hit_part}{runner} — banked so far: {r:+.2f}R")
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
    return (
        f"📊 <b>Signal Performance</b>\n"
        f"<i>Last {stats['total']} closed trades — every signal we've posted, no cherry-picking</i>\n\n"
        f"✅ Wins: {stats['wins']} ({stats['win_rate']:.0f}%)\n"
        f"⚪ Breakeven: {stats['breakeven']}\n"
        f"❌ Losses: {stats['losses']}\n\n"
        f"Total: <b>{stats['total_r']:+.1f}R</b>\n"
        f"Average: <b>{stats['avg_r']:+.2f}R</b> per trade\n\n"
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
        lines.append(
            f"{arrow} <b>{symbol_part}</b> {t.get('side', '?')} @ {t.get('entry', '?')}\n"
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


def format_pnl_message(risk_amount: float):
    history = load_trade_history()
    stats = compute_stats(history)
    if not stats:
        return "📊 No closed signals yet to calculate from — check back soon!"
    total_pnl = stats["total_r"] * risk_amount
    avg_pnl = stats["avg_r"] * risk_amount
    return (
        f"🧮 <b>P&amp;L Estimate</b> — risking ${risk_amount:.0f} per trade\n\n"
        f"Based on {stats['total']} closed trades:\n"
        f"Total: <b>${total_pnl:+.2f}</b>\n"
        f"Average per trade: <b>${avg_pnl:+.2f}</b>\n\n"
        f"<i>Assumes you followed the position plan on every signal — 20% at Target 1, 30% "
        f"at Target 2, 15% at Target 3, 10% at Target 4, and the final 25% runner on its "
        f"trailing stop. This is an estimate, not a guarantee of future results.</i>"
    )


def pnl_keyboard():
    return [
        [{"text": "$50", "callback_data": "pnl:50"}, {"text": "$100", "callback_data": "pnl:100"}, {"text": "$200", "callback_data": "pnl:200"}],
        [{"text": "⬅️ Back", "callback_data": "menu:home"}],
    ]


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

def tg(method, **params):
    # هر متد ممکنه پارامتر تلگرامیِ خودش هم اسمش timeout باشه (برای long-polling)،
    # پس timeout درخواست HTTP خودمون رو جدا و همیشه چند ثانیه بیشتر از اون می‌گیریم.
    http_timeout = params.get("timeout", 0) + 10
    resp = requests.post(f"{API}/{method}", json=params, timeout=http_timeout)
    resp.raise_for_status()
    return resp.json()


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
    و به هرکدوم پیام اطلاع‌رسانی می‌فرسته. کاربرهایی که با کم‌کردن به زیر صفر برسن حذف می‌شن."""
    removed = []
    for s in list(subscribers):
        s["expiry_ts"] += days * 24 * 3600
        if s["expiry_ts"] <= now_ts():
            remove_member(s["user_id"])
            send_message(s["chat_id"], f"⌛ {note}\n\nYour subscription has ended. Type /plans to resubscribe.")
            removed.append(s)
        else:
            direction = "extended" if days > 0 else "reduced"
            send_message(s["chat_id"], f"ℹ️ {note}\n\nYour subscription was {direction} by {abs(days)} day(s).\nNew expiry: {fmt_date(s['expiry_ts'])}")
    for s in removed:
        subscribers.remove(s)
    return len(subscribers) + len(removed), len(removed)


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
            handle_admin_command(chat_id, text, subscribers, discounts, maintenance, photo_submissions)
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
            send_message(chat_id, HELP_TEXT)

        elif text == "/refer":
            send_message(chat_id, referral_text(user_id))

        elif text == "/support":
            send_message(chat_id, support_text())

        elif text == "/disclaimer":
            send_message(chat_id, DISCLAIMER_TEXT)

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
            send_message(chat_id, format_status_message(subscribers, user_id))

        elif text == "/results":
            _ensure_fresh_data()
            send_message(chat_id, format_results_message())

        elif text == "/history":
            _ensure_fresh_data()
            msg, total_pages = format_history_page(0)
            send_message(chat_id, msg, keyboard=history_keyboard(0, total_pages))

        elif text.startswith("/pnl"):
            parts = text.split()
            if len(parts) != 2:
                send_message(chat_id, "Usage: /pnl AMOUNT — e.g. /pnl 100")
            else:
                try:
                    amount = float(parts[1])
                    _ensure_fresh_data()
                    send_message(chat_id, format_pnl_message(amount))
                except ValueError:
                    send_message(chat_id, "Amount must be a number, e.g. /pnl 100")

        elif text == "/cancel":
            active = next((s for s in subscribers if s["user_id"] == user_id), None)
            if active:
                remove_member(user_id)
                subscribers.remove(active)
                send_message(chat_id, "Your membership has been cancelled and you've been removed from the channel.\nType /plans anytime to rejoin.")
            else:
                send_message(chat_id, "You don't have an active membership.")


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
        edit_message(chat_id, message_id, "🧮 Pick a risk amount per trade, or type <code>/pnl AMOUNT</code> for a custom value:", keyboard=pnl_keyboard())
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
    "/admin_list — list active subscribers\n"
    "/admin_add &lt;user_id&gt; &lt;days&gt; — add/extend a user\n"
    "/admin_extend &lt;user_id&gt; &lt;days&gt; — add N days\n"
    "/admin_reduce &lt;user_id&gt; &lt;days&gt; — subtract N days\n"
    "/admin_remove &lt;user_id&gt; — remove immediately\n\n"
    "<u>Bulk (all active subscribers)</u>\n"
    "/admin_bulk_extend &lt;days&gt; [reason...] — add N days to everyone + notify\n"
    "/admin_bulk_reduce &lt;days&gt; [reason...] — subtract N days from everyone + notify\n\n"
    "<u>Maintenance / Pause</u>\n"
    "/admin_pause [reason...] — pause new signals &amp; expiry removals, notify everyone\n"
    "/admin_resume — resume + auto-extend everyone by the pause duration\n\n"
    "<u>Manual signals</u>\n"
    "/admin_manual_signal &lt;SYMBOL/USD&gt; &lt;BUY|SELL&gt; &lt;entry&gt; &lt;sl&gt; — post a "
    "signal in the standard format (stop + 4 targets), tracked live and counted in /results. "
    "Any symbol works (e.g. BTC/USD, SHIB/USD), not just BTC/ETH.\n"
    "Or post/forward directly in the channel with a tagged line, e.g.:\n"
    "<code>#signal BTC/USD BUY 65000 64000</code>\n"
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


def queue_manual_signal(symbol, side, entry_s, sl_s, forwarded_tracking=False, tf_label=None, silent=False):
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
    """
    symbol = (symbol or "").upper()
    side = (side or "").upper()
    if not SYMBOL_FORMAT_RE.match(symbol):
        return False, "❌ Symbol must look like TICKER/USD, e.g. BTC/USD, ETH/USD, or SHIB/USD."
    if side not in ("BUY", "SELL"):
        return False, "❌ Side must be BUY or SELL."
    try:
        entry, sl = float(entry_s), float(sl_s)
    except ValueError:
        return False, "❌ entry and sl must be numbers."
    if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
        return False, "❌ Stop-loss must be below entry for BUY, or above entry for SELL."

    signals = _load("manual_signals.json", [])
    signal_id = f"m-{now_ts()}"
    signals.append({
        "id": signal_id, "symbol": symbol, "side": side, "entry": entry, "sl": sl,
        "status": "pending", "created_at": now_ts(), "forwarded_tracking": bool(forwarded_tracking),
        "tf_label": tf_label, "silent": bool(silent),
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
    r"#signal\s+([A-Z0-9]{2,15}/USD)\s+(BUY|SELL)\s+([\d.]+)\s+([\d.]+)",
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
    return symbol, side, entry, sl, tf_label


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

    return {"kind": kind, "ticker": ticker, "tf_label": tf_label, "entry": entry, "level": level, "result_r": result_r}


def queue_forwarded_result(ev):
    """رویداد پارس‌شده رو توی forwarded_results_queue.json صف می‌کنه تا candle_engine.py
    (صاحب واقعی candle_state.json/trade_history.json) توی دور اسکن بعدیش اعمالش کنه - همون
    الگوی صف‌کردن که manual_signals.json برای سیگنال‌های ورودی استفاده می‌شه."""
    queue = _load("forwarded_results_queue.json", [])
    queue.append({
        "kind": ev["kind"], "ticker": ev["ticker"], "tf_label": ev.get("tf_label"),
        "entry": ev.get("entry"), "level": ev.get("level"), "result_r": ev.get("result_r"),
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
                "Correct format: <code>#signal BTC/USD BUY 65000 64000</code>\n"
                "(symbol, side, entry, stop-loss — space separated)",
                reply_to_message_id=message_id,
            )
            return
        symbol, side, entry_s, sl_s = m.group(1), m.group(2), m.group(3), m.group(4)
        ok, note = queue_manual_signal(symbol, side, entry_s, sl_s)
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
        symbol, side, entry, sl, tf_label = parsed
        if is_duplicate_signal(symbol, side, entry, sl):
            send_message(
                chat["id"],
                f"↩️ This looks like a duplicate of a signal already queued/active "
                f"({symbol} {side} entry={entry}) — skipped to avoid double-tracking.",
                reply_to_message_id=message_id,
            )
            return
        forwarded = is_forwarded_message(post)
        ok, note = queue_manual_signal(symbol, side, str(entry), str(sl), forwarded_tracking=forwarded, tf_label=tf_label)
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
            symbol, side, entry, sl, tf_label = parsed
            if not is_duplicate_signal(symbol, side, entry, sl):
                queue_manual_signal(symbol, side, str(entry), str(sl),
                                     forwarded_tracking=True, tf_label=tf_label, silent=True)
        return

    result_ev = parse_forwarded_result_text(text)
    if result_ev:
        queue_forwarded_result(result_ev)


def handle_admin_command(chat_id, text, subscribers, discounts, maintenance, photo_submissions):
    parts = text.split()
    cmd = parts[0]

    if cmd in ("/admin_list", "/admin"):
        if not subscribers:
            send_message(chat_id, "No active subscribers.\n\n" + ADMIN_HELP)
        else:
            lines = [f"<code>{s['user_id']}</code> — until {fmt_date(s['expiry_ts'])}" for s in subscribers]
            send_message(chat_id, "<b>Active subscribers:</b>\n" + "\n".join(lines) + "\n\n" + ADMIN_HELP)
        return

    if cmd == "/admin_help":
        send_message(chat_id, ADMIN_HELP)
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
        total, removed = bulk_adjust_subscribers(subscribers, days, reason)
        send_message(chat_id, f"Done. Adjusted {total} subscriber(s) by {days:+d} day(s). {removed} were removed (ran out).")
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
        for s in subscribers:
            send_message(s["chat_id"], notice)
        send_message(chat_id, f"⏸️ Paused. New signals stopped, no one will be removed for lapsed time, and {len(subscribers)} subscriber(s) were notified.")
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
        total, removed = bulk_adjust_subscribers(subscribers, extend_days, note)
        send_message(chat_id, f"▶️ Resumed. Everyone's subscription extended by {extend_days} day(s); {total} notified.")
        return

    if cmd == "/admin_manual_signal":
        if len(parts) != 5:
            send_message(chat_id, "Usage: /admin_manual_signal <SYMBOL/USD> <BUY|SELL> <entry> <sl> (any symbol, e.g. BTC/USD, SHIB/USD)")
            return
        symbol, side, entry_s, sl_s = parts[1].upper(), parts[2].upper(), parts[3], parts[4]
        ok, note = queue_manual_signal(symbol, side, entry_s, sl_s)
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
            last_commit = time.time()

    save_all(state, pending, subscribers, discounts, applied, used_tx, referrals, maintenance, photo_submissions,
             relay_seen_ids, relay_msgid_map)
    git_commit_and_push(final=True)
    print(f"✅ Loop finished after {cycles} cycles, {(time.time()-start)/3600:.2f}h — "
          f"pending: {len(pending)}, subscribers: {len(subscribers)}")


if __name__ == "__main__":
    main()

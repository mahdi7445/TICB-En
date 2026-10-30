"""
shared_controls.py — لایه‌ی کنترل مشترک (هر دو ربات: TICB و SGNL)

همه‌ی فیلترهای ایمنی/نظارتی پیش‌فرض «خاموش» هستند؛ یعنی با نصب این فایل رفتار فعلی ربات‌ها
هیچ تغییری نمی‌کند. ادمین‌ها فقط با دکمه‌ها (پنل ادمین در subscription_bot) مقادیر را عوض می‌کنند؛
خودِ ربات‌ها فقط می‌خوانند.

فایل: data/bot_controls.json  →  {"ticb": {...}, "sgnl": {...}}
هر بخش فقط کلیدهای تعریف‌شده در DEFAULTS را می‌پذیرد (مقدار نامعتبر → پیش‌فرض).
"""
from __future__ import annotations

import json
import os
import time
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_DATA = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONTROLS_FILE = os.path.join(_DATA, "bot_controls.json")
SECTIONS = ("ticb", "sgnl")
TF_KEYS = ("1m", "5m", "15m", "1h", "4h")
import ladder_catalog as _lc

try:
    import lowtf_strategies as _lt
    LOWTF = tuple(g for g in _lt.GENS if g not in _lt.ORIG)
except Exception:  # noqa: BLE001
    LOWTF = ()
BASE_MODULES = ("HDV3", "SWP", "TS1")
CANDLE = tuple(_lc.CANDLE)                   # CN1M/CN5M/CN15/CN1H/CN4H = استراتژی اصلی (کندل سیگنال)
HOTMODS = ("HOT",)                           # استراتژی «رمزارزهای داغ + پولبک» (تایم‌فریم پایین، با ماژول‌های دیگر ترکیب می‌شود)
MODULES = BASE_MODULES + HOTMODS + CANDLE + LOWTF  # ماژول‌های استراتژی جدید (+ تایم‌فریم پایین؛ قفل تا PASS)

# (نوع, پیش‌فرض, حداقل, حداکثر)  — نوع: bool/int/float
SCHEMA: Dict[str, Tuple[str, Any, Any, Any]] = {
    "paused":              ("bool", False, None, None),   # توقف همه‌ی سیگنال‌های خودکار
    "news_filter":         ("bool", False, None, None),   # عدم صدور سیگنال اطراف خبر
    "news_before_min":     ("int", 30, 0, 720),
    "news_after_min":      ("int", 30, 0, 720),
    "news_impact":         ("int", 3, 1, 3),              # 3=فقط High, 2=Medium+, 1=همه
    "daily_loss_filter":   ("bool", False, None, None),
    "daily_loss_limit_r":  ("float", 3.0, 0.5, 50.0),
    "weekly_loss_filter":  ("bool", False, None, None),
    "weekly_loss_limit_r": ("float", 8.0, 1.0, 200.0),
    "streak_filter":       ("bool", False, None, None),
    "streak_losses":       ("int", 4, 2, 20),
    "streak_pause_min":    ("int", 240, 10, 4320),
    "max_open_filter":     ("bool", False, None, None),
    "max_open_trades":     ("int", 6, 1, 100),
    "max_per_day_filter":  ("bool", False, None, None),
    "max_signals_per_day": ("int", 10, 1, 200),
    "same_side_filter":    ("bool", False, None, None),
    "max_same_side":       ("int", 3, 1, 50),
    "min_stop_filter":     ("bool", False, None, None),
    "min_stop_pct":        ("float", 0.20, 0.01, 10.0),
    "max_stop_filter":     ("bool", False, None, None),
    "max_stop_pct":        ("float", 8.0, 0.1, 30.0),
    "risk_pct":            ("float", 1.0, 0.1, 5.0),      # پیشنهاد ریسک هر معامله (فقط نمایش در پیام)
    "show_risk_hint":      ("bool", False, None, None),
    # ---- استراتژی HOT (رمزارزهای داغ + پولبک) ----
    "hot_top_n":           ("int", 5, 1, 10),             # چند رمزارز برنده و چند باخته (هرکدام)
    "hot_universe":        ("int", 40, 10, 100),          # فقط این تعداد پرحجم‌ترین جفت‌ها بررسی می‌شوند
    "hot_min_move_pct":    ("float", 3.0, 0.5, 30.0),     # حداقل تغییر قیمت از بازشدن امروز (UTC) برای «داغ» بودن
    "hot_min_vol_musd":    ("float", 2.0, 0.1, 500.0),    # حداقل حجم ۲۴ساعته (میلیون دلار)
    "hot_refresh_min":     ("int", 10, 2, 120),           # هر چند دقیقه رتبه‌بندی تازه می‌شود
    "hot_window_h":        ("int", 6, 2, 24),             # پنجره‌ی ساعتی برای سنجش حرکت و عمق پولبک
    "hot_session_filter":  ("bool", True, None, None),    # فقط در ساعات پرنوسان
    "hot_session_from":    ("int", 7, 0, 23),
    "hot_session_to":      ("int", 21, 1, 24),
    "hot_pb_min":          ("float", 0.20, 0.05, 0.8),    # حداقل عمق اصلاح (کسری از حرکت)
    "hot_pb_max":          ("float", 0.62, 0.2, 0.95),    # حداکثر عمق اصلاح
    "hot_tf_5m":           ("bool", True, None, None),
    "hot_tf_15m":          ("bool", False, None, None),
    "hot_t_CNDL":          ("bool", True, None, None),    # ماشه: کندل سیگنال
    "hot_t_SWR":           ("bool", False, None, None),   # ماشه: Sweep & Reclaim
    "hot_t_RSM":           ("bool", False, None, None),   # ماشه: RSI Momentum Reversal
    "hot_t_TPB":           ("bool", False, None, None),   # ماشه: Trend Pullback
    "hot_max_hold_h":      ("int", 6, 1, 24),             # بستن اجباری معامله بعد از این تعداد ساعت
    "hot_max_per_day":     ("int", 4, 1, 30),             # سقف سیگنال HOT در روز
    "hot_cooldown_min":    ("int", 60, 0, 720),           # فاصله‌ی دو سیگنال روی یک رمزارز
    "lowtf_max_per_day":   ("int", 5, 1, 50),             # سقف سیگنال روزانه‌ی استراتژی‌های تایم‌فریم پایین (بهترین‌ها اول)
}
# ---- 🆕 «موتور اصلی» (سیگنال‌های خودکارِ کندل، غیر از ماژول‌ها): همه پیش‌فرضِ «آزاد» = رفتار فعلی بدون تغییر ----
SCHEMA["nat_side"] = ("choice", "both", None, ["both", "long", "short"])   # جهت مجاز
# 🔴 اصلاح (V9): فیلتر حداقل استاپِ موتور اصلی پیش‌فرض «روشن» با ۱٫۲۵٪ شد. دلیل (از ۳۲۰ معامله‌ی زنده‌ی ۲۰۲۶-۰۹-۱۷ تا ۱۰-۲۶):
# کارمزد رفت‌وبرگشت ≈ ۰٫۱۰٪ قیمت است، پس سهم آن از هر معامله = ۰٫۱۰ ÷ استاپ٪ (R). معاملاتِ با استاپ زیر ۰٫۷۵٪ (۱۷۰ معامله)
# در مجموع ≈ -۸۰R خالص دادند (ناخالص فقط ≈ -۱۶R)، یعنی بیشترِ زیان از کارمزد است نه از جهت‌گیریِ بازار. با کفِ ۱٫۲۵٪ سهم کارمزد
# ≤ ۰٫۰۸R می‌شود. اگر ادمین قبلاً این مقدار را از پنل ذخیره کرده باشد، مقدار ذخیره‌شده همچنان برنده است (این فقط پیش‌فرض است).
SCHEMA["nat_min_stop_filter"] = ("bool", True, None, None)                 # حداقل فاصله‌ی استاپ (کاهش سهم کارمزد)
SCHEMA["nat_min_stop_pct"] = ("float", 1.25, 0.05, 10.0)
for _t in TF_KEYS:
    SCHEMA[f"nat_tf_{_t}"] = ("bool", True, None, None)                    # روشن/خاموش‌کردن هر تایم‌فریم موتور اصلی
for _m in MODULES:
    SCHEMA[f"mod_{_m}"] = ("bool", False, None, None)       # ماژول جدید: پیش‌فرض خاموش
# تنظیمات اختصاصی هر استراتژی (همه پیش‌فرضِ «بدون محدودیت»): سقف روزانه‌ی خودش، کولدان هر نماد، جهت مجاز، حداکثر استاپ٪
MX_STRATS = tuple(m for m in MODULES if m != "HOT")
MX_STEP = {"day": 1, "cd": 15, "maxstop": 0.5}
for _m in MX_STRATS:
    SCHEMA[f"mx_{_m}_day"] = ("int", 0, 0, 50)          # 0 = بدون سقف اختصاصی
    SCHEMA[f"mx_{_m}_cd"] = ("int", 0, 0, 720)                          # دقیقه؛ 0 = بدون کولدان
    SCHEMA[f"mx_{_m}_maxstop"] = ("float", 0.0, 0.0, 30.0)              # 0 = بدون محدودیت
    SCHEMA[f"mx_{_m}_side"] = ("choice", "both", None, ["both", "long", "short"])
# نردبان مدیریت معامله‌ی هر ماژول: "auto" = پیشنهاد سیستم (RECOMMENDED_PROFILE)
PROFILE_CHOICES = ["auto"] + list(_lc.ORDER)    # لیست زنده (نردبان‌های سفارشی U1..U9 با sync_profile_choices اضافه می‌شوند)
RECOMMENDED_PROFILE = {m: _lc.best_ladder(m) for m in BASE_MODULES}   # محاسبه‌شده از بک‌تست گزارش
def _why(m):
    b = _lc.best_ladder(m)
    st = _lc.stats_for(m).get(b)
    return f"Best ladder from the backtest report: {b} — " + (_lc.fmt_stat_line(b, st, "en") if st else _lc.ladder_summary(b, "en"))


RECOMMENDED_WHY = {m: _why(m) for m in BASE_MODULES}
for _m in MODULES:
    SCHEMA[f"prof_{_m}"] = ("choice", "auto", None, PROFILE_CHOICES)
for _t in TF_KEYS:
    SCHEMA[f"tf_{_t}"] = ("bool", True, None, None)         # تایم‌فریم فعال (فقط برای ماژول‌های جدید)

# ----------------------------------------------------------------------------- پروفایل پیش‌فرض V9 (بر پایه‌ی full_backtest واقعی)
# گزارش full_backtest (۲۸ نماد، کارمزد ۰٫۱۰٪، انتخاب روی ۶۰٪ اول زمان و قضاوت روی ۴۰٪ آخر) نشان داد:
#   • موتور اصلی کندلی (ENG) و همه‌ی CN* روی هر ۵ تایم‌فریم شکست خوردند (بهترینش ۴h با PF ۰٫۹۷؛ ۱۵د/۱س/۵د: PF ۰٫۸۵–۰٫۹۶) و در
#     معاملات زنده‌ی ۴۰ روزه ‎−۸۸R ‎ثبت شد ⇒ به‌طور پیش‌فرض خاموش (nat_tf_*=False).
#   • فقط این‌ها هم‌زمان در TRAIN و TEST مثبت و معنادار بودند ⇒ پیش‌فرضِ روشن، هرکدام با «نردبان مخصوص خودش»:
#       HDV3 (4h) → Q2       win≈۴۵٪ PF≈۱٫۵۷ t≈۳٫۷  (۲۴ ماهه‌ی اخیر، n≈۳۶۸)
#       SWP  (4h، رژیم رنج، A3) → CLASSIC  win≈۵۳٪ PF≈۱٫۱۹ t≈۳٫۷ (n≈۲۶۰۰)
#       TPS  (1h، فیلتر S، A3) → H3        win≈۴۴٪ PF≈۱٫۱۴ t≈۴٫۴ (n≈۵۴۰۰؛ Q3 سودده‌تر ولی win≈۳۸٪ <۴۰٪)
#       TKF  (15m، فیلتر DS، A3) → Q3      win≈۴۱٪ PF≈۱٫۳۳ t≈۲٫۸ (n≈۵۷۰)
#       TKS  (15m، فیلتر DS، کف استاپ ۱٫۵٪) → Q2  win≈۴۳٪ PF≈۱٫۴۶ t≈۳٫۲ (n≈۳۸۱؛ TEST: win ۴۶٪ PF ۱٫۵۳) — V10
#   • TS1/TPB/HOT و استراتژی‌های ۵ و ۱ دقیقه‌ای (SMS/TKS/…) از معیار آماری رد شدند ⇒ خاموش/قفل می‌مانند.
# این پروفایل «یک‌بار» روی تنظیمات ذخیره‌شده هم اعمال می‌شود (نشانگر _profile)؛ بعد از آن هر چه ادمین از پنل عوض کند ماندگار است.
PROFILE_KEY = "_profile"
PROFILE_VER = "v10-2026-10-30"
V9_PROFILE: Dict[str, Any] = {
    "mod_HDV3": True, "mod_SWP": True, "mod_TPS": True, "mod_TKF": True, "mod_TKS": True,
    "prof_HDV3": "Q2", "prof_SWP": "CLASSIC", "prof_TPS": "H3", "prof_TKF": "Q3", "prof_TKS": "Q2",
    "nat_min_stop_filter": True, "nat_min_stop_pct": 1.25,
}
for _t in TF_KEYS:
    V9_PROFILE[f"nat_tf_{_t}"] = False
for _m in MODULES:                                      # هر ماژولِ ردشده در بک‌تست (TS1/TPB/HOT/CN*/بقیه‌ی lowtf) صراحتاً خاموش
    V9_PROFILE.setdefault(f"mod_{_m}", False)
for _k, _v in V9_PROFILE.items():
    if _k in SCHEMA:
        SCHEMA[_k] = (SCHEMA[_k][0], _v, SCHEMA[_k][2], SCHEMA[_k][3])

# ماژول تأییدشده‌ی ۴ساعته می‌تواند بعد از تأیید ادمین روشن شود؛ بقیه آزمایشی‌اند.
MODULE_INFO = {
    "HDV3": ("4h", "تأییدشده (۴ساعته)"),
    "SWP":  ("4h", "تأییدشده (۴ساعته، با فیلتر رژیم)"),
    "TS1":  ("1h", "آزمایشی (تأیید آماری ندارد)"),
    "HOT":  ("5m", "Hot movers + pullback (low-timeframe, same-day trades)"),
}
for _m in LOWTF:
    MODULE_INFO[_m] = ("?", "تایم‌فریم پایین (قفل تا PASS)")
for _m, _tf in _lc.CANDLE.items():
    MODULE_INFO[_m] = (_tf, "original candle-signal strategy (replaces the old auto signal of this timeframe)")


def sync_profile_choices(refresh_local: bool = True) -> None:
    """نردبان‌های سفارشی را (از data/custom_ladders.json) می‌خواند و گزینه‌های مجاز نردبان را به‌روز می‌کند."""
    if refresh_local:
        _lc.refresh_custom()
    PROFILE_CHOICES[:] = ["auto"] + list(_lc.ORDER)


def defaults() -> Dict[str, Any]:
    return {k: v[1] for k, v in SCHEMA.items()}


def _coerce(key: str, val: Any) -> Any:
    typ, dflt, lo, hi = SCHEMA[key]
    try:
        if typ == "bool":
            if isinstance(val, str):
                return val.strip().lower() in ("1", "true", "on", "yes")
            return bool(val)
        if typ == "choice":
            return val if val in hi else dflt
        v = int(val) if typ == "int" else float(val)
        if lo is not None and v < lo:
            v = lo
        if hi is not None and v > hi:
            v = hi
        return v
    except Exception:
        return dflt


def sanitize(section: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = defaults()
    if isinstance(section, dict):
        for k in SCHEMA:
            if k in section:
                out[k] = _coerce(k, section[k])
        if section.get(PROFILE_KEY) != PROFILE_VER:      # پروفایل V9 هنوز روی این بخش اعمال (ذخیره) نشده → یک‌بار روی مقدارهای قدیمی بنشیند
            for k, v in V9_PROFILE.items():
                if k in SCHEMA:
                    out[k] = _coerce(k, v)
    return out


def _read_all(path: str = None) -> Dict[str, Any]:
    path = path or CONTROLS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load(section: str, path: str = None) -> Dict[str, Any]:
    """همیشه یک دیکشنری کامل و معتبر برمی‌گرداند (هرگز استثنا نمی‌دهد)."""
    try:
        sync_profile_choices()
        return sanitize(_read_all(path).get(section))
    except Exception:
        return defaults()


def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ctl_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def set_value(section: str, key: str, val: Any, path: str = None) -> Dict[str, Any]:
    if section not in SECTIONS or key not in SCHEMA:
        raise KeyError(f"unknown control {section}.{key}")
    path = path or CONTROLS_FILE
    allc = _read_all(path)
    sec = sanitize(allc.get(section))
    sec[key] = _coerce(key, val)
    sec[PROFILE_KEY] = PROFILE_VER                      # از این به بعد انتخاب‌های ادمین دوباره با پروفایل پیش‌فرض بازنویسی نمی‌شوند
    allc[section] = sec
    _atomic_write(path, allc)
    return sec


def toggle(section: str, key: str, path: str = None) -> bool:
    cur = load(section, path)
    if SCHEMA[key][0] != "bool":
        raise TypeError(key)
    return bool(set_value(section, key, not cur[key], path)[key])


def reset(section: str, path: str = None) -> Dict[str, Any]:
    path = path or CONTROLS_FILE
    allc = _read_all(path)
    allc[section] = defaults()
    allc[section][PROFILE_KEY] = PROFILE_VER
    _atomic_write(path, allc)
    return allc[section]


def step_value(section: str, key: str, delta: float, path: str = None) -> Any:
    cur = load(section, path)[key]
    return set_value(section, key, cur + delta, path)[key]


# --------------------------------------------------------------------- اخبار
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_news_cache: Dict[str, Any] = {"t": 0.0, "events": []}
NEWS_TTL = 3600.0
# رویدادهای دستی (ادمین با دکمه اضافه می‌کند): [{"start_ms":..,"end_ms":..,"title":..}]
MANUAL_NEWS_FILE = os.path.join(_DATA, "manual_news.json")


def _fetch_news() -> List[Dict[str, Any]]:
    import urllib.request
    req = urllib.request.Request(NEWS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = json.loads(r.read().decode("utf-8"))
    ev = []
    for e in raw:
        try:
            if e.get("country") != "USD":
                continue
            imp = {"High": 3, "Medium": 2, "Low": 1}.get(e.get("impact"), 0)
            if imp == 0:
                continue
            ts = datetime.fromisoformat(e["date"]).astimezone(timezone.utc).timestamp() * 1000
            ev.append({"ts_ms": ts, "impact": imp, "title": e.get("title", "")})
        except Exception:
            continue
    return ev


def news_events(now_s: float = None, fetcher=None) -> List[Dict[str, Any]]:
    now_s = now_s or time.time()
    if now_s - _news_cache["t"] > NEWS_TTL:
        try:
            _news_cache["events"] = (fetcher or _fetch_news)()
        except Exception:
            pass  # آخرین کش معتبر می‌ماند؛ fail-open (فیلتر خبر نباید ربات را متوقف کند)
        _news_cache["t"] = now_s
    return _news_cache["events"]


def manual_news(path: str = None) -> List[Dict[str, Any]]:
    try:
        with open(path or MANUAL_NEWS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def add_manual_news(start_ms: int, end_ms: int, title: str = "manual", path: str = None) -> None:
    path = path or MANUAL_NEWS_FILE
    now_ms = time.time() * 1000
    lst = [x for x in manual_news(path) if x.get("end_ms", 0) > now_ms]
    lst.append({"start_ms": int(start_ms), "end_ms": int(end_ms), "title": title})
    _atomic_write(path, lst)  # type: ignore[arg-type]


def clear_manual_news(path: str = None) -> None:
    _atomic_write(path or MANUAL_NEWS_FILE, [])  # type: ignore[arg-type]


def news_block(ctl: Dict[str, Any], now_ms: float, events=None, manual=None) -> Optional[str]:
    if not ctl.get("news_filter"):
        return None
    for m in (manual if manual is not None else manual_news()):
        if m.get("start_ms", 0) <= now_ms <= m.get("end_ms", 0):
            return f"manual_news:{m.get('title', '')}"
    evs = events if events is not None else news_events(now_ms / 1000.0)
    b, a = ctl["news_before_min"] * 60000, ctl["news_after_min"] * 60000
    for e in evs:
        if e["impact"] >= ctl["news_impact"] and e["ts_ms"] - b <= now_ms <= e["ts_ms"] + a:
            return f"news:{e.get('title', '')}"
    return None


# --------------------------------------------------------------------- گیت اصلی
def _r_of(t: Dict[str, Any]) -> Optional[float]:
    for k in ("final_r", "result_r", "r_result"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _closed_ms(t: Dict[str, Any]) -> Optional[float]:
    for k in ("closed_at_ms", "close_ms", "closed_ms"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    v = t.get("closed_at")
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return None
    return None


def gate(ctl: Dict[str, Any], *, side: str, stop_pct: Optional[float], now_ms: float,
         open_trades: List[Dict[str, Any]], closed_trades: List[Dict[str, Any]],
         signals_today: int = 0, tf_key: str = None, module: str = None,
         events=None, manual=None) -> Tuple[bool, str]:
    """(مجاز؟, دلیل). روی سیگنال‌های خودکار اعمال می‌شود. هر خطا → مجاز (fail-open)."""
    try:
        if ctl.get("paused"):
            return False, "paused"
        if module is not None:
            if not ctl.get(f"mod_{module}"):
                return False, "module_off"
        if tf_key is not None and module is not None and not ctl.get(f"tf_{tf_key}", True):
            return False, "tf_off"
        nb = news_block(ctl, now_ms, events, manual)
        if nb:
            return False, nb
        day0 = now_ms - (now_ms % 86400000)
        week0 = day0 - ((datetime.fromtimestamp(now_ms / 1000, timezone.utc).weekday()) * 86400000)
        if ctl.get("daily_loss_filter") or ctl.get("weekly_loss_filter") or ctl.get("streak_filter"):
            rows = []
            for t in closed_trades:
                r, c = _r_of(t), _closed_ms(t)
                if r is not None and c is not None:
                    rows.append((c, r))
            rows.sort()
            if ctl.get("daily_loss_filter"):
                s = sum(r for c, r in rows if c >= day0)
                if s <= -ctl["daily_loss_limit_r"]:
                    return False, f"daily_loss({s:.2f}R)"
            if ctl.get("weekly_loss_filter"):
                s = sum(r for c, r in rows if c >= week0)
                if s <= -ctl["weekly_loss_limit_r"]:
                    return False, f"weekly_loss({s:.2f}R)"
            if ctl.get("streak_filter") and rows:
                n = 0
                for c, r in reversed(rows):
                    if r < 0:
                        n += 1
                    else:
                        break
                last_c = rows[-1][0]
                if n >= ctl["streak_losses"] and now_ms - last_c < ctl["streak_pause_min"] * 60000:
                    return False, f"loss_streak({n})"
        if ctl.get("max_open_filter") and len(open_trades) >= ctl["max_open_trades"]:
            return False, "max_open"
        if ctl.get("max_per_day_filter") and signals_today >= ctl["max_signals_per_day"]:
            return False, "max_per_day"
        if ctl.get("same_side_filter"):
            if sum(1 for t in open_trades if t.get("side") == side) >= ctl["max_same_side"]:
                return False, "same_side_cap"
        if stop_pct is not None:
            if ctl.get("min_stop_filter") and stop_pct < ctl["min_stop_pct"]:
                return False, "stop_too_small"
            if ctl.get("max_stop_filter") and stop_pct > ctl["max_stop_pct"]:
                return False, "stop_too_wide"
    except Exception:
        return True, "gate_error"
    return True, ""


NATIVE_MAX_R = 6.0     # دورترین تارگتِ مسیر کندل بومی (shared_risk_config.RR_TARGETS[-1]) - با آن هماهنگ بماند


def native_block(ctl: Dict[str, Any], *, side: Optional[str], tf_key: Optional[str], stop_pct: Optional[float]) -> str:
    """برای سیگنال‌های خودکارِ «موتور اصلی»: رشته‌ی دلیل = رد، \"\" = مجاز. خطا ⇒ مجاز (fail-open). paused ⇒ رد."""
    try:
        if ctl.get("paused"):                      # دکمه‌ی Pause signals: «موتور اصلی» هم سیگنال جدید نمی‌دهد
            return "paused"
        if tf_key and not ctl.get(f"nat_tf_{tf_key}", True):
            return "tf_off"
        ns = ctl.get("nat_side", "both")
        if ns == "long" and side == "SELL":
            return "side_long_only"
        if ns == "short" and side == "BUY":
            return "side_short_only"
        if ctl.get("nat_min_stop_filter") and stop_pct is not None and stop_pct < ctl.get("nat_min_stop_pct", 0.5):
            return "stop_too_small"
        # 🔴 تارگتِ غیرممکن: برای SELL قیمتِ آخرین تارگت = entry × (1 − stop% × NATIVE_MAX_R / 100). اگر استاپ پهن باشد آن قیمت
        # منفی/نزدیکِ صفر می‌شود (مثال دیده‌شده «Target 4: -0.01810»). آخرین تارگتِ SELL باید حداقل ۵٪ قیمتِ ورود بماند.
        # این تابع را هر دو ربات (candle_engine.py و SGNL bot.py) برای مسیر کندل بومی صدا می‌زنند.
        if side == "SELL" and stop_pct is not None and stop_pct * NATIVE_MAX_R / 100.0 > 0.95:
            return "target_unreachable"
    except Exception:
        return ""
    return ""


def any_filter_active(ctl: Dict[str, Any]) -> bool:
    keys = ("news_filter", "daily_loss_filter", "weekly_loss_filter", "streak_filter", "max_open_filter",
            "max_per_day_filter", "same_side_filter", "min_stop_filter", "max_stop_filter", "paused")
    return any(ctl.get(k) for k in keys)


def risk_hint(ctl: Dict[str, Any], stop_pct: float) -> str:
    """نمایش اندازه‌ی موقعیت پیشنهادی: ریسک٪ ÷ حد ضرر٪ = اهرم/درصد سرمایه."""
    if not ctl.get("show_risk_hint") or not stop_pct or stop_pct <= 0:
        return ""
    pos = ctl["risk_pct"] / stop_pct * 100.0
    return f"Risk {ctl['risk_pct']:.1f}% of equity → position ≈ {pos:.0f}% of equity (stop {stop_pct:.2f}%)"


# --------------------------------------------------------------------- خواندن از راه دور (برای SGNL)
# 🔴 رفع باگِ بحرانی: آدرس قبلاً با «mahdi744» نوشته شده بود ولی صاحب واقعیِ ریپو «mahdi7445» است ⇒ همه‌ی دانلودها 404
# می‌شد و ماژول‌های SGNL (از جمله HOT) هرگز اجرا نمی‌شدند. حالا مالک از محیط GitHub Actions (یا TICB_REPO_OWNER) خوانده می‌شود.
REPO_OWNER = (os.environ.get("TICB_REPO_OWNER") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "mahdi7445").strip()
REMOTE_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/TICB-En/main/data/"
_remote_cache: Dict[str, Any] = {"t": 0.0, "ctl": None, "manual": []}


def load_remote(section: str, base_url: str = None, ttl: float = 90.0, fetcher=None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(کنترل‌های این بخش, بلاک‌های دستیِ خبر). منبع: فایل‌های data/ ریپوی TICB-En (که پنل ادمین می‌نویسد).
    خطای شبکه → آخرین مقدار موفق؛ اگر هرگز موفق نبود → فایل محلی/پیش‌فرض (همه‌ی فیلترها خاموش)."""
    now = time.time()
    if now - _remote_cache["t"] > ttl:
        _remote_cache["t"] = now
        try:
            import urllib.request

            def get(name):
                if fetcher:
                    return fetcher(name)
                req = urllib.request.Request((base_url or REMOTE_BASE) + name + f"?t={int(now)}", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8"))
            _remote_cache["ctl"] = get("bot_controls.json")
            try:
                _lc.apply_custom(get("custom_ladders.json")); sync_profile_choices(refresh_local=False)
            except Exception:
                pass
            try:
                m = get("manual_news.json")
                _remote_cache["manual"] = m if isinstance(m, list) else []
            except Exception:
                pass
        except Exception:
            pass
    if isinstance(_remote_cache["ctl"], dict):
        sync_profile_choices(refresh_local=False)
        return sanitize(_remote_cache["ctl"].get(section)), _remote_cache["manual"]
    return load(section), manual_news()


def effective_profile(ctl: Dict[str, Any], module: str, verdicts: Optional[Dict[str, Any]] = None) -> str:
    """نردبان واقعی: انتخاب دستی ادمین، وگرنه بهترین نردبانِ همان استراتژی (کاتالوگ/verdict)."""
    p = ctl.get(f"prof_{module}", "auto")
    return _lc.best_ladder(module, verdicts) if p in ("auto", "", None) else p


# ----------------------------------------------------------------- verdict استراتژی‌های تایم‌فریم پایین
VERDICT_FILE = os.path.join(_DATA, "lowtf_verdicts.json")


def load_verdicts(path: str = None) -> Dict[str, Any]:
    try:
        with open(path or VERDICT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load_verdicts_remote(base_url: str = None, ttl: float = 300.0, fetcher=None) -> Dict[str, Any]:
    now = time.time()
    c = _remote_cache.setdefault("verd", {"t": 0.0, "v": None})
    if now - c["t"] > ttl:
        c["t"] = now
        try:
            import urllib.request
            if fetcher:
                c["v"] = fetcher("lowtf_verdicts.json")
            else:
                req = urllib.request.Request((base_url or REMOTE_BASE) + f"lowtf_verdicts.json?t={int(now)}", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    c["v"] = json.loads(r.read().decode("utf-8"))
        except Exception:
            pass
    return c["v"] if isinstance(c["v"], dict) else load_verdicts()


def is_candidate(v: Dict[str, Any]) -> bool:
    """«نزدیک به پاس»: PASS نشده ولی نتیجه‌ی مثبت و معنادار دارد. معیار ثابت و از پیش تعیین‌شده (نه تنظیم‌شده روی نتایج):
    t≥2، میانگین ≥0.05R، PF≥1.1، دوره‌های train و test هر دو مثبت، ≥۶۰٪ نمادها مثبت و ≥۱۵۰ معامله."""
    try:
        sp = v.get("symbols_positive")
        # full_backtest v9 سهم نمادهای مثبت را اندازه نمی‌گیرد (null): جبرانش با آستانه‌ی سخت‌گیرانه‌تر t≥2.5 (نه حذف بی‌قیدوشرط شرط).
        sym_ok = (sp >= 0.6) if isinstance(sp, (int, float)) else (v["t"] >= 2.5)
        return (v.get("verdict") != "PASS" and v["t"] >= 2.0 and v["avg_r"] >= 0.05 and v["pf"] >= 1.1 and v["train_r"] > 0
                and v["test_r"] > 0 and sym_ok and v["n"] >= 150)
    except Exception:
        return False


def lowtf_status(module: str, verdicts: Dict[str, Any]) -> str:
    v = (verdicts or {}).get(_lc.vkey(module))
    return "NA" if not v else ("PASS" if v.get("verdict") == "PASS" else "CAND" if is_candidate(v) else "FAIL")


def apply_recommended(section: str, path: str = None) -> None:
    for m in MODULES:
        set_value(section, f"prof_{m}", "auto", path)


# ----------------------------------------------------------------------------- تاریخ شروع نمایش آمار به کاربران
# «_public»: {"from": "YYYY-MM-DD"} در bot_controls.json (مشترک برای هر دو ربات). هر آماری که به کاربران نشان داده می‌شود
# (نتایج، تاریخچه، منحنی سرمایه، P&L، گزارش روزانه/هفتگی کانال، دراودان، استریک‌ها ...) فقط از معاملاتی ساخته می‌شود که
# «بعد از این تاریخ بسته شده‌اند»؛ هر چه قبل از آن بوده (تست‌های ربات) فقط برای ادمین در گزارش‌ها دیده می‌شود. خالی = همه.
PUBLIC_KEY = "_public"


def _parse_date(v: str) -> Optional[float]:
    try:
        v = (v or "").strip()
        if not v:
            return None
        d = datetime.fromisoformat(v if "T" in v else v + "T00:00:00+00:00")
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def get_public_from(path: str = None, remote: bool = False) -> str:
    try:
        if remote and isinstance(_remote_cache.get("ctl"), dict):
            allc = _remote_cache["ctl"]
        else:
            allc = _read_all(path)
        v = (allc.get(PUBLIC_KEY) or {}).get("from", "")
        return v if _parse_date(v) is not None else ""
    except Exception:
        return ""


def set_public_from(value: str, path: str = None) -> str:
    """value = 'YYYY-MM-DD' (یا 'YYYY-MM-DDTHH:MM') یا '' برای نمایش همه. مقدار نامعتبر → بدون تغییر و برگرداندن مقدار فعلی."""
    path = path or CONTROLS_FILE
    value = (value or "").strip()
    if value and _parse_date(value) is None:
        return get_public_from(path)
    allc = _read_all(path)
    allc[PUBLIC_KEY] = {"from": value}
    _atomic_write(path, allc)
    return value


def public_cutoff_ts(path: str = None, remote: bool = False) -> Optional[float]:
    return _parse_date(get_public_from(path, remote))


def filter_public(records: List[Dict[str, Any]], key: str = "closed_at", path: str = None, remote: bool = False) -> List[Dict[str, Any]]:
    """فقط رکوردهایی که key‌شان (پیش‌فرض closed_at) بعد از تاریخ شروع نمایش است. رکورد بی‌زمان → حذف (محافظه‌کارانه)."""
    cut = public_cutoff_ts(path, remote)
    if cut is None:
        return list(records)
    out = []
    for r in records:
        t = _parse_date(str(r.get(key) or "")) if isinstance(r, dict) else None
        if t is not None and t >= cut:
            out.append(r)
    return out

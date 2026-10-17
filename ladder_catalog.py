# -*- coding: utf-8 -*-
"""
ladder_catalog.py — کاتالوگ «نردبان خروج» (تارگت‌گذاری) برای هر استراتژی + نتایج بک‌تست هر نردبان.

چرا این فایل: قبلاً نردبان‌ها داخل strategy_modules.PROFILES و «پیشنهاد» داخل shared_controls (دستی و متنی) بود.
حالا:
  • همه‌ی نردبان‌ها (چندتارگتی + تک‌تارگتی «براکت») یک‌جا تعریف شده‌اند؛
  • برای هر استراتژی، نتیجه‌ی بک‌تست هر نردبان (از design_report.txt شما: ۲۸ نماد، ~۵٫۷ سال، کارمزد ۰٫۱۰٪) ذخیره شده؛
  • «بهترین نردبان هر استراتژی» با یک قاعده‌ی ثابت و شفاف از روی همین جدول محاسبه می‌شود (نه دستی)؛
  • استراتژی‌های تایم‌فریم‌پایین بعد از اجرای strategy_lab_lowtf همین ساختار را از data/lowtf_verdicts.json می‌گیرند.

قاعده‌ی انتخاب (rank_ladders): اول فقط نردبان‌هایی که در بک‌تست «تأیید» شده‌اند (میانگین R خالص>0، t≥2.3،
هر دو نیمه‌ی train/test مثبت)؛ اگر هیچ‌کدام تأیید نبود همه؛ سپس بیشترین میانگین R خالص به‌ازای هر معامله،
و در تساوی t بالاتر. توجه: تفاوت بین نردبان‌های نزدیک به هم (مثلاً ۰٫۳۷ در برابر ۰٫۳۵) در حدّ نویز آماری است؛
جدول کامل در پنل ادمین نشان داده می‌شود تا ادمین خودش هم قضاوت کند.

بدون وابستگی (فقط Python خالص) تا هر دو ربات و پنل ادمین بتوانند import کنند.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# کلید → (سطوح R, وزن‌ها, ضریب تریل رانر). مجموع وزن‌ها ≤ 1 ؛ باقی‌مانده = رانر (اگر >0).
# براکت‌ها (B*) تک‌تارگتی‌اند: ۱۰۰٪ پوزیشن در یک تارگت بسته می‌شود، حد ضرر جابه‌جا نمی‌شود، رانر ندارند.
LADDERS: Dict[str, Tuple[Tuple[float, ...], Tuple[float, ...], float]] = {
    # همان نردبان قدیمی ربات‌ها (shared_risk_config): ۱R/۲R/۴R/۶R با وزن ۲۰/۳۰/۱۵/۱۰٪ و رانر ۲۵٪
    "CLASSIC": ((1.0, 2.0, 4.0, 6.0), (0.20, 0.30, 0.15, 0.10), 1.5),
    "Q1": ((2.0, 3.0, 4.0, 6.0), (0.30, 0.25, 0.20, 0.10), 1.5),
    "Q2": ((2.0, 3.0, 5.0, 8.0), (0.30, 0.25, 0.15, 0.10), 1.5),
    "Q3": ((2.0, 4.0, 6.0, 9.0), (0.35, 0.25, 0.15, 0.10), 2.0),
    "Q5": ((2.0, 3.0, 4.0, 6.0), (0.50, 0.20, 0.10, 0.05), 1.5),
    "B2": ((2.0,), (1.0,), 0.0),
    "B25": ((2.5,), (1.0,), 0.0),
    "B3": ((3.0,), (1.0,), 0.0),
    "B4": ((4.0,), (1.0,), 0.0),
}
ORDER = ("CLASSIC", "Q1", "Q2", "Q3", "Q5", "B2", "B25", "B3", "B4")
# Q4 عمداً نیست: تعریف آن در کدِ در دسترس (zip) نبود و ربات هم هیچ‌وقت آن را عرضه نکرده بود.

DESC_EN = {
    "CLASSIC": "1R/2R/4R/6R — 20/30/15/10% + 25% runner (the bots' classic ladder)",
    "Q1": "2R/3R/4R/6R — 30/25/20/10% + 15% runner",
    "Q2": "2R/3R/5R/8R — 30/25/15/10% + 20% runner (aims higher)",
    "Q3": "2R/4R/6R/9R — 35/25/15/10% + 15% runner, wide trail 2R",
    "Q5": "2R/3R/4R/6R — 50/20/10/5% + 15% runner (banks half at 2R)",
    "B2": "Single target 2R (100%), no stop move",
    "B25": "Single target 2.5R (100%), no stop move",
    "B3": "Single target 3R (100%), no stop move",
    "B4": "Single target 4R (100%), no stop move",
}
DESC_FA = {
    "CLASSIC": "۱R/۲R/۴R/۶R — ۲۰/۳۰/۱۵/۱۰٪ + رانر ۲۵٪ (نردبان کلاسیک ربات‌ها)",
    "Q1": "۲R/۳R/۴R/۶R — ۳۰/۲۵/۲۰/۱۰٪ + رانر ۱۵٪",
    "Q2": "۲R/۳R/۵R/۸R — ۳۰/۲۵/۱۵/۱۰٪ + رانر ۲۰٪ (هدف بلندتر)",
    "Q3": "۲R/۴R/۶R/۹R — ۳۵/۲۵/۱۵/۱۰٪ + رانر ۱۵٪ با تریل ۲R",
    "Q5": "۲R/۳R/۴R/۶R — ۵۰/۲۰/۱۰/۵٪ + رانر ۱۵٪ (نصف پوزیشن در ۲R)",
    "B2": "تک‌تارگت ۲R (۱۰۰٪)، بدون جابه‌جایی حد ضرر",
    "B25": "تک‌تارگت ۲٫۵R (۱۰۰٪)، بدون جابه‌جایی حد ضرر",
    "B3": "تک‌تارگت ۳R (۱۰۰٪)، بدون جابه‌جایی حد ضرر",
    "B4": "تک‌تارگت ۴R (۱۰۰٪)، بدون جابه‌جایی حد ضرر",
}

for _k, (_lv, _w, _tr) in LADDERS.items():  # اعتبارسنجی ساختار هنگام import
    assert len(_lv) == len(_w) and sum(_w) <= 1.0 + 1e-9 and all(a < b for a, b in zip(_lv, _lv[1:])), _k
    assert (sum(_w) < 1.0 - 1e-9) == (_tr > 0), f"{_k}: runner weight and trail must agree"

# ----------------------------------------------------------------------------- نتایج بک‌تست (design_report.txt)
# module -> ladder -> {n, avg (R خالص پس از کارمزد ۰٫۱۰٪), t, win (٪), pf, train, test, sym (٪ نمادهای مثبت), net20 (R خالص با کارمزد ۰٫۲۰٪),
#                       ok (تأییدشده؟), yp/yn (سال‌های مثبت/کل)}
# منبع: گزارش «CONFIRMATION on unseen symbols | cost 0.10%» — HDV3: 4h حد ضرر ساختاری؛ SWP: 4h فیلتر رژیم رنج + حد ضرر ≥۳ATR (A3)؛
# TS1: 1h حد ضرر ساختاری. (SWP فقط با A3 آزمایش شده است — ماژول زنده هم باید همان حد ضرر را داشته باشد.)
BACKTEST_SOURCE = "28 coins, ~5.7y, round-trip cost 0.10%, entry next-bar open (design_report.txt)"
STATS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "HDV3": {
        "B2": {"n": 380, "avg": 0.297, "t": 3.95, "win": 44.7, "pf": 1.54, "train": 0.171, "test": 0.486, "sym": 75, "net20": 0.284, "ok": True, "yp": 6, "yn": 6},
        "B25": {"n": 371, "avg": 0.377, "t": 4.38, "win": 42.3, "pf": 1.65, "train": 0.301, "test": 0.49, "sym": 79, "net20": 0.364, "ok": True, "yp": 6, "yn": 6},
        "B3": {"n": 364, "avg": 0.354, "t": 3.75, "win": 38.5, "pf": 1.58, "train": 0.273, "test": 0.473, "sym": 79, "net20": 0.341, "ok": True, "yp": 6, "yn": 6},
        "B4": {"n": 359, "avg": 0.317, "t": 3.03, "win": 34.5, "pf": 1.49, "train": 0.311, "test": 0.326, "sym": 71, "net20": 0.304, "ok": True, "yp": 5, "yn": 6},
        "CLASSIC": {"n": 378, "avg": 0.189, "t": 2.98, "win": 57.4, "pf": 1.44, "train": 0.165, "test": 0.224, "sym": 86, "net20": 0.176, "ok": True, "yp": 5, "yn": 6},
        "Q1": {"n": 368, "avg": 0.292, "t": 3.49, "win": 43.8, "pf": 1.52, "train": 0.242, "test": 0.366, "sym": 75, "net20": 0.279, "ok": True, "yp": 6, "yn": 6},
        "Q2": {"n": 365, "avg": 0.306, "t": 3.54, "win": 43.6, "pf": 1.54, "train": 0.267, "test": 0.364, "sym": 79, "net20": 0.293, "ok": True, "yp": 6, "yn": 6},
        "Q3": {"n": 362, "avg": 0.255, "t": 2.95, "win": 43.6, "pf": 1.45, "train": 0.247, "test": 0.267, "sym": 61, "net20": 0.242, "ok": True, "yp": 6, "yn": 6},
        "Q5": {"n": 368, "avg": 0.287, "t": 3.56, "win": 43.8, "pf": 1.51, "train": 0.219, "test": 0.387, "sym": 75, "net20": 0.274, "ok": True, "yp": 6, "yn": 6},
    },
    "SWP": {
        "B2": {"n": 2551, "avg": 0.064, "t": 2.26, "win": 36.2, "pf": 1.1, "train": 0.033, "test": 0.11, "sym": 71, "net20": 0.049, "ok": False, "yp": 4, "yn": 6},
        "B25": {"n": 2342, "avg": 0.078, "t": 2.36, "win": 31.9, "pf": 1.11, "train": 0.033, "test": 0.147, "sym": 64, "net20": 0.063, "ok": True, "yp": 5, "yn": 6},
        "B3": {"n": 2192, "avg": 0.081, "t": 2.17, "win": 28.7, "pf": 1.11, "train": 0.015, "test": 0.18, "sym": 68, "net20": 0.066, "ok": False, "yp": 5, "yn": 6},
        "B4": {"n": 2025, "avg": 0.048, "t": 1.12, "win": 24.3, "pf": 1.06, "train": -0.054, "test": 0.203, "sym": 57, "net20": 0.034, "ok": False, "yp": 3, "yn": 6},
        "CLASSIC": {"n": 2621, "avg": 0.095, "t": 3.8, "win": 52.8, "pf": 1.2, "train": 0.044, "test": 0.172, "sym": 79, "net20": 0.08, "ok": True, "yp": 5, "yn": 6},
        "Q1": {"n": 2247, "avg": 0.072, "t": 2.18, "win": 36.6, "pf": 1.11, "train": 0.027, "test": 0.139, "sym": 50, "net20": 0.057, "ok": False, "yp": 5, "yn": 6},
        "Q2": {"n": 2174, "avg": 0.063, "t": 1.88, "win": 36.3, "pf": 1.1, "train": 0.016, "test": 0.134, "sym": 57, "net20": 0.048, "ok": False, "yp": 5, "yn": 6},
        "Q3": {"n": 2029, "avg": 0.049, "t": 1.35, "win": 35.8, "pf": 1.08, "train": -0.026, "test": 0.163, "sym": 57, "net20": 0.034, "ok": False, "yp": 4, "yn": 6},
        "Q5": {"n": 2247, "avg": 0.072, "t": 2.29, "win": 36.6, "pf": 1.11, "train": 0.028, "test": 0.139, "sym": 61, "net20": 0.057, "ok": False, "yp": 5, "yn": 6},
    },
    "TS1": {
        "B2": {"n": 202, "avg": 0.196, "t": 1.88, "win": 41.6, "pf": 1.32, "train": 0.206, "test": 0.181, "sym": 71, "net20": 0.135, "ok": False, "yp": 5, "yn": 6},
        "B25": {"n": 202, "avg": 0.167, "t": 1.43, "win": 35.1, "pf": 1.25, "train": 0.204, "test": 0.111, "sym": 61, "net20": 0.106, "ok": False, "yp": 5, "yn": 6},
        "B3": {"n": 202, "avg": 0.199, "t": 1.53, "win": 31.7, "pf": 1.28, "train": 0.282, "test": 0.074, "sym": 64, "net20": 0.138, "ok": False, "yp": 6, "yn": 6},
        "B4": {"n": 202, "avg": 0.312, "t": 2.01, "win": 28.2, "pf": 1.42, "train": 0.455, "test": 0.099, "sym": 61, "net20": 0.251, "ok": False, "yp": 5, "yn": 6},
        "CLASSIC": {"n": 202, "avg": 0.06, "t": 0.68, "win": 55.4, "pf": 1.13, "train": 0.158, "test": -0.087, "sym": 57, "net20": -0.001, "ok": False, "yp": 4, "yn": 6},
        "Q1": {"n": 202, "avg": 0.289, "t": 2.35, "win": 41.6, "pf": 1.47, "train": 0.38, "test": 0.154, "sym": 75, "net20": 0.228, "ok": True, "yp": 6, "yn": 6},
        "Q2": {"n": 202, "avg": 0.284, "t": 2.27, "win": 41.6, "pf": 1.46, "train": 0.394, "test": 0.119, "sym": 71, "net20": 0.223, "ok": False, "yp": 6, "yn": 6},
        "Q3": {"n": 202, "avg": 0.318, "t": 2.38, "win": 41.6, "pf": 1.52, "train": 0.451, "test": 0.119, "sym": 71, "net20": 0.258, "ok": True, "yp": 5, "yn": 6},
        "Q5": {"n": 202, "avg": 0.267, "t": 2.3, "win": 41.6, "pf": 1.44, "train": 0.336, "test": 0.165, "sym": 71, "net20": 0.207, "ok": True, "yp": 6, "yn": 6},
    },
}


# ----------------------------------------------------------------------------- انتخاب
def rank_ladders(stats: Optional[Dict[str, Dict[str, Any]]]) -> List[Tuple[str, Dict[str, Any]]]:
    """نردبان‌ها از بهترین به بدترین (قاعده‌ی بالا). ورودی خالی → []"""
    if not stats:
        return []
    items = [(k, v) for k, v in stats.items() if k in LADDERS and isinstance(v, dict) and isinstance(v.get("avg"), (int, float))]
    conf = [(k, v) for k, v in items if v.get("ok")]
    pool = conf if conf else items
    pool.sort(key=lambda kv: (-float(kv[1]["avg"]), -float(kv[1].get("t", 0) or 0), kv[0]))
    rest = [kv for kv in items if kv not in pool]
    rest.sort(key=lambda kv: (-float(kv[1]["avg"]), kv[0]))
    return pool + rest


def best_ladder(module: str, verdicts: Optional[Dict[str, Any]] = None, default: str = "CLASSIC") -> str:
    """بهترین نردبان یک ماژول: اول verdict بک‌تست تایم‌فریم‌پایین (در صورت وجود)، بعد جدول STATS، وگرنه default."""
    try:
        v = (verdicts or {}).get(vkey(module)) or {}
        if v.get("best_ladder") in LADDERS:
            return v["best_ladder"]
        r = rank_ladders(v.get("stats") or STATS.get(module))
        if r:
            return r[0][0]
    except Exception:
        pass
    return default


# ماژول‌های «کندل سیگنال» (استراتژی اصلی ربات‌ها) برای هر تایم‌فریم؛ verdict هرکدام با کلید «CNDL@tf» ذخیره می‌شود.
CANDLE: Dict[str, str] = {"CN1M": "1m", "CN5M": "5m", "CN15": "15m", "CN1H": "1h", "CN4H": "4h"}


def vkey(module: str) -> str:
    return f"CNDL@{CANDLE[module]}" if module in CANDLE else module


def stats_for(module: str, verdicts: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    v = (verdicts or {}).get(vkey(module)) or {}
    return v.get("stats") or STATS.get(module) or {}


def fmt_stat_line(key: str, st: Dict[str, Any], lang: str = "en") -> str:
    """یک خط خلاصه‌ی نتیجه‌ی بک‌تست یک نردبان."""
    if not st:
        return "no data" if lang == "en" else "بدون داده"
    mark = "✓" if st.get("ok") else "·"
    return (f"{mark} {st['avg']:+.2f}R/trade · PF {st['pf']:.2f} · win {st['win']:.0f}% · n={st['n']} · t={st['t']:.1f}")


def ladder_summary(key: str, lang: str = "en") -> str:
    return (DESC_FA if lang == "fa" else DESC_EN).get(key, key)

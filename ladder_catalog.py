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
    # نردبان‌های «چهارتارگتیِ واقعی» (هر چهار تارگت وزن >0 دارد)، طراحی‌شده برای هدف PF 1.4–1.6 و وین‌ریت بالای ۴۵٪ و فاصله‌ی
    # آخرین تارگت ≥ ۶R: تارگت اول نزدیک‌تر (۱–۱٫۵R) با وزن بیشتر تا وین‌ریت بالا برود، بعد از آن حد ضرر به ورود می‌رسد.
    # نتیجه‌ی بک‌تست تأییدی (design_report، ۲۸ نماد ناشناخته) در STATS ثبت شده؛ H3 برای HDV3 و TS1 نردبان انتخابی است.
    "H1": ((1.5, 2.5, 4.0, 6.0), (0.35, 0.25, 0.15, 0.10), 1.5),
    "H2": ((1.0, 2.5, 4.0, 6.0), (0.30, 0.25, 0.20, 0.10), 1.5),
    "H3": ((1.5, 3.0, 5.0, 8.0), (0.40, 0.25, 0.15, 0.10), 2.0),
    "H4": ((2.0, 3.0, 4.5, 7.0), (0.40, 0.25, 0.15, 0.10), 1.5),
}
ORDER = ["CLASSIC", "Q1", "Q2", "Q3", "Q5", "H1", "H2", "H3", "H4", "B2", "B25", "B3", "B4"]   # لیست زنده: نردبان‌های سفارشی (U1..U9) هم به آن اضافه می‌شوند
BUILTIN = tuple(ORDER)
# Q4 عمداً نیست: تعریف آن در کدِ در دسترس (zip) نبود و ربات هم هیچ‌وقت آن را عرضه نکرده بود.

DESC_EN = {
    "CLASSIC": "1R/2R/4R/6R — 20/30/15/10% + 25% runner (the bots' classic ladder)",
    "Q1": "2R/3R/4R/6R — 30/25/20/10% + 15% runner",
    "Q2": "2R/3R/5R/8R — 30/25/15/10% + 20% runner (aims higher)",
    "Q3": "2R/4R/6R/9R — 35/25/15/10% + 15% runner, wide trail 2R",
    "Q5": "2R/3R/4R/6R — 50/20/10/5% + 15% runner (banks half at 2R)",
    "H1": "1.5R/2.5R/4R/6R — 35/25/15/10% + 15% runner (earlier first target, higher win rate)",
    "H2": "1R/2.5R/4R/6R — 30/25/20/10% + 15% runner (fast break-even)",
    "H3": "1.5R/3R/5R/8R — 40/25/15/10% + 10% runner, wide trail 2R",
    "H4": "2R/3R/4.5R/7R — 40/25/15/10% + 10% runner (wider targets)",
    "B2": "Full close at 2R (100%); shown as 4 targets — earlier ones only move the stop",
    "B25": "Full close at 2.5R (100%); shown as 4 targets — earlier ones only move the stop",
    "B3": "Full close at 3R (100%); shown as 4 targets — earlier ones only move the stop",
    "B4": "Full close at 4R (100%); shown as 4 targets — earlier ones only move the stop",
}
DESC_FA = {
    "CLASSIC": "۱R/۲R/۴R/۶R — ۲۰/۳۰/۱۵/۱۰٪ + رانر ۲۵٪ (نردبان کلاسیک ربات‌ها)",
    "Q1": "۲R/۳R/۴R/۶R — ۳۰/۲۵/۲۰/۱۰٪ + رانر ۱۵٪",
    "Q2": "۲R/۳R/۵R/۸R — ۳۰/۲۵/۱۵/۱۰٪ + رانر ۲۰٪ (هدف بلندتر)",
    "Q3": "۲R/۴R/۶R/۹R — ۳۵/۲۵/۱۵/۱۰٪ + رانر ۱۵٪ با تریل ۲R",
    "Q5": "۲R/۳R/۴R/۶R — ۵۰/۲۰/۱۰/۵٪ + رانر ۱۵٪ (نصف پوزیشن در ۲R)",
    "H1": "۱٫۵R/۲٫۵R/۴R/۶R — ۳۵/۲۵/۱۵/۱۰٪ + رانر ۱۵٪ (تارگت اول نزدیک‌تر، وین‌ریت بالاتر)",
    "H2": "۱R/۲٫۵R/۴R/۶R — ۳۰/۲۵/۲۰/۱۰٪ + رانر ۱۵٪ (بریک‌ایون سریع)",
    "H3": "۱٫۵R/۳R/۵R/۸R — ۴۰/۲۵/۱۵/۱۰٪ + رانر ۱۰٪ با تریل ۲R",
    "H4": "۲R/۳R/۴٫۵R/۷R — ۴۰/۲۵/۱۵/۱۰٪ + رانر ۱۰٪ (تارگت‌های دورتر)",
    "B2": "بستن کامل در ۲R (۱۰۰٪)؛ با ۴ تارگت نمایش داده می‌شود — تارگت‌های قبلی فقط حد ضرر را جابه‌جا می‌کنند",
    "B25": "بستن کامل در ۲٫۵R (۱۰۰٪)؛ با ۴ تارگت نمایش داده می‌شود — تارگت‌های قبلی فقط حد ضرر را جابه‌جا می‌کنند",
    "B3": "بستن کامل در ۳R (۱۰۰٪)؛ با ۴ تارگت نمایش داده می‌شود — تارگت‌های قبلی فقط حد ضرر را جابه‌جا می‌کنند",
    "B4": "بستن کامل در ۴R (۱۰۰٪)؛ با ۴ تارگت نمایش داده می‌شود — تارگت‌های قبلی فقط حد ضرر را جابه‌جا می‌کنند",
}

for _k, (_lv, _w, _tr) in LADDERS.items():  # اعتبارسنجی ساختار هنگام import
    assert len(_lv) == len(_w) and sum(_w) <= 1.0 + 1e-9 and all(a < b for a, b in zip(_lv, _lv[1:])), _k
    assert (sum(_w) < 1.0 - 1e-9) == (_tr > 0), f"{_k}: runner weight and trail must agree"

# ----------------------------------------------------------------------------- نردبان‌های سفارشی (طراحی‌شده توسط ادمین)
# فایل data/custom_ladders.json: {"U1": {"levels":[2,3,5], "weights":[0.4,0.3,0.2], "trail":1.5, "note":"..."}, ...}
# اعتبارسنجی سخت‌گیرانه (validate_ladder) تا نردبانِ نادرست هرگز به ربات نرسد؛ حداکثر ۹ نردبان سفارشی (U1..U9).
import json as _json
import os as _os

CUSTOM_FILE = _os.path.join(_os.environ.get("DATA_DIR") or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data"),
                            "custom_ladders.json")
CUSTOM_MAX = 9
CUSTOM_KEYS = tuple(f"U{i}" for i in range(1, CUSTOM_MAX + 1))
CUSTOM: Dict[str, Dict[str, Any]] = {}
_custom_mtime = [None]


def validate_ladder(levels, weights, trail) -> Tuple[Optional[Tuple[Tuple[float, ...], Tuple[float, ...], float]], str]:
    """(نردبان نرمال‌شده یا None, پیام خطا). قوانین: ۱ تا ۶ تارگت؛ سطح‌ها صعودی در [0.5, 30]R؛ وزن‌ها >0 و جمع ≤۱
    (باقی‌مانده = رانر)؛ اگر رانر هست تریل >0 (۰٫۵ تا ۱۰R)، اگر رانر نیست تریل = ۰."""
    try:
        lv = tuple(round(float(x), 4) for x in levels); wt = tuple(round(float(x), 4) for x in weights); tr = round(float(trail), 4)
    except Exception:
        return None, "numbers only"
    if not (1 <= len(lv) <= 6):
        return None, "1 to 6 targets"
    if len(lv) != len(wt):
        return None, "levels and weights must have the same count"
    if any(not (0.5 <= x <= 30) for x in lv) or any(b <= a for a, b in zip(lv, lv[1:])):
        return None, "levels must be increasing, each between 0.5R and 30R"
    if any(w <= 0 for w in wt) or sum(wt) > 1.0 + 1e-9:
        return None, "weights must be > 0 and sum to at most 100%"
    rem = 1.0 - sum(wt)
    if rem > 1e-6:
        if rem < 0.01:
            return None, "remainder (runner) must be at least 1% or exactly 0"
        if not (0.5 <= tr <= 10):
            return None, "a runner exists, so trail must be between 0.5R and 10R"
    else:
        tr = 0.0
    return (lv, wt, tr), ""


def expand4(levels, weights):
    """هر معامله‌ی ماژول همیشه ۴ تارگت مشخص دارد. نردبانی که کمتر از ۴ تارگت دارد (براکت‌های B* یا نردبان سفارشی)
    با «تارگت‌های مرحله‌ای» با وزن ۰ تکمیل می‌شود که زیر اولین تارگتِ خودِ نردبان قرار می‌گیرند: در هر کدام سودی بسته
    نمی‌شود ولی حد ضرر جابه‌جا می‌شود (T1 → ورود، بعد → تارگت قبلی). تارگت‌ها و وزن‌های اصلی دست‌نخورده می‌مانند.
    خروجی: (levels, weights) هم‌طول با ≥۴ عضو."""
    lv, wt = list(levels), list(weights)
    m = 4 - len(lv)
    if m <= 0:
        return tuple(lv), tuple(wt)
    first = lv[0]
    ms = [round(first * i / (m + 1) * 4) / 4 for i in range(1, m + 1)]
    if any(b <= a for a, b in zip(ms, ms[1:])) or ms[0] <= 0 or ms[-1] >= first:
        ms = [round(first * i / (m + 1), 3) for i in range(1, m + 1)]
    return tuple(ms + lv), tuple([0.0] * m + wt)


def describe(key: str, lang: str = "en") -> str:
    lv, wt, tr = LADDERS[key]
    rem = max(0.0, 1.0 - sum(wt))
    a = "/".join(f"{x:g}R" for x in lv); b = "/".join(f"{round(w * 100):g}" for w in wt)
    if lang == "fa":
        return f"{a} — {b}٪" + (f" + رانر {round(rem * 100):g}٪ با تریل {tr:g}R" if rem > 1e-6 else (" (تک‌تارگت)" if len(lv) == 1 else ""))
    return f"{a} — {b}%" + (f" + {round(rem * 100):g}% runner, trail {tr:g}R" if rem > 1e-6 else (" (single target)" if len(lv) == 1 else ""))


def scenarios(key: str) -> List[Tuple[str, float]]:
    """نتیجه‌ی R در سناریوهای مهم (با همان منطق advance: بعد از T1 استاپ=ورود؛ بعد از Tk استاپ=T(k-1)).
    «stop at T_k» = تارگت k گرفته شد و بعد باقی‌مانده با استاپ جدید بسته شد؛ «all targets» = همه‌ی تارگت‌ها + رانر روی کفِ تریل (حداقل)."""
    lv, wt, tr = LADDERS[key]
    out: List[Tuple[str, float]] = [("stop before T1", -1.0)]
    banked = 0.0; rem = 1.0
    for i, (l, w) in enumerate(zip(lv, wt)):
        banked += w * l; rem -= w
        if i == len(lv) - 1:
            break
        stop_r = 0.0 if i == 0 else lv[i - 1]
        out.append((f"T{i + 1} hit, then stopped", round(banked + rem * stop_r, 3)))
    if len(lv) == 1 and rem <= 1e-9:
        out.append(("T1 hit (full close)", round(banked, 3)))
    else:
        floor = lv[-2] if len(lv) > 1 else 0.0
        out.append((f"all {len(lv)} targets, runner stopped at floor", round(banked + rem * floor, 3)))
    return out


def apply_custom(data: Optional[Dict[str, Any]]) -> None:
    """data = محتوای custom_ladders.json. نردبان‌های سفارشی قبلی حذف و دوباره ساخته می‌شوند؛ نردبان نامعتبر نادیده گرفته می‌شود."""
    for k in list(CUSTOM):
        LADDERS.pop(k, None); DESC_EN.pop(k, None); DESC_FA.pop(k, None)
    CUSTOM.clear()
    if isinstance(data, dict):
        for k in CUSTOM_KEYS:
            e = data.get(k)
            if not isinstance(e, dict):
                continue
            v, err = validate_ladder(e.get("levels"), e.get("weights"), e.get("trail", 0))
            if v is None:
                continue
            LADDERS[k] = v; CUSTOM[k] = {"levels": list(v[0]), "weights": list(v[1]), "trail": v[2], "note": str(e.get("note", ""))[:60]}
            DESC_EN[k] = "★ custom: " + describe(k, "en") + (f" — {CUSTOM[k]['note']}" if CUSTOM[k]["note"] else "")
            DESC_FA[k] = "★ سفارشی: " + describe(k, "fa") + (f" — {CUSTOM[k]['note']}" if CUSTOM[k]["note"] else "")
    ORDER[:] = list(BUILTIN) + [k for k in CUSTOM_KEYS if k in CUSTOM]


def refresh_custom(path: Optional[str] = None, force: bool = False) -> None:
    """خواندن فایل محلی (فقط اگر تغییر کرده باشد). خطا → بدون تغییر."""
    path = path or CUSTOM_FILE
    try:
        m = _os.path.getmtime(path) if _os.path.exists(path) else -1
        if not force and _custom_mtime[0] == m:
            return
        data = {}
        if m != -1:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        apply_custom(data); _custom_mtime[0] = m
    except Exception:
        pass


def save_custom(key: str, levels, weights, trail, note: str = "", path: Optional[str] = None) -> Tuple[bool, str]:
    v, err = validate_ladder(levels, weights, trail)
    if v is None:
        return False, err
    if key not in CUSTOM_KEYS:
        return False, "bad key"
    path = path or CUSTOM_FILE
    data = {k: {"levels": e["levels"], "weights": e["weights"], "trail": e["trail"], "note": e.get("note", "")} for k, e in CUSTOM.items()}
    data[key] = {"levels": list(v[0]), "weights": list(v[1]), "trail": v[2], "note": note[:60]}
    _write_custom(data, path)
    return True, ""


def delete_custom(key: str, path: Optional[str] = None) -> None:
    path = path or CUSTOM_FILE
    data = {k: {"levels": e["levels"], "weights": e["weights"], "trail": e["trail"], "note": e.get("note", "")} for k, e in CUSTOM.items() if k != key}
    _write_custom(data, path)


def _write_custom(data: Dict[str, Any], path: str) -> None:
    import tempfile
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_os.path.dirname(path), suffix=".tmp")
    with _os.fdopen(fd, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=1)
    _os.replace(tmp, path)
    refresh_custom(path, force=True)


def next_free_key() -> Optional[str]:
    return next((k for k in CUSTOM_KEYS if k not in CUSTOM), None)


refresh_custom()


# ----------------------------------------------------------------------------- نتایج بک‌تست (design_report.txt)
# module -> ladder -> {n, avg (R خالص پس از کارمزد ۰٫۱۰٪), t, win (٪), pf, train, test, sym (٪ نمادهای مثبت), net20 (R خالص با کارمزد ۰٫۲۰٪),
#                       ok (تأییدشده؟), yp/yn (سال‌های مثبت/کل)}
# منبع: گزارش «CONFIRMATION on unseen symbols | cost 0.10%» — HDV3: 4h حد ضرر ساختاری؛ SWP: 4h فیلتر رژیم رنج + حد ضرر ≥۳ATR (A3)؛
# TS1: 1h حد ضرر ساختاری. (SWP فقط با A3 آزمایش شده است — ماژول زنده هم باید همان حد ضرر را داشته باشد.)
BACKTEST_SOURCE = "28 unseen coins, 2021-2026, round-trip cost 0.10%, entry next-bar open (design_report.txt, CONFIRMATION run, all 13 ladders)"
# ⚠️ pf در این جدول «تخمینی» است (گزارش تأییدی PF چاپ نمی‌کند): pf = (avg + L) / L با L = (1 − win) × 1.05R؛ روی ردیف‌هایی که PF واقعی
# داشتند (جدول قبلی) خطای این تخمین حداکثر ۰٫۰۳ بود. PF واقعی را strategy_lab با --verdicts می‌نویسد.
STATS_PF_ESTIMATED = True
STATS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "HDV3": {
        "CLASSIC": {"n": 380, "avg": 0.192, "t": 3.05, "win": 57.9, "pf": 1.43, "train": 0.176, "test": 0.218, "sym": 86, "net20": 0.179, "ok": True, "yp": 5, "yn": 6},
        "Q1": {"n": 369, "avg": 0.296, "t": 3.55, "win": 43.9, "pf": 1.5, "train": 0.246, "test": 0.371, "sym": 79, "net20": 0.283, "ok": True, "yp": 6, "yn": 6},
        "Q2": {"n": 366, "avg": 0.311, "t": 3.6, "win": 43.7, "pf": 1.53, "train": 0.278, "test": 0.36, "sym": 79, "net20": 0.298, "ok": True, "yp": 6, "yn": 6},
        "Q3": {"n": 363, "avg": 0.259, "t": 3.01, "win": 43.8, "pf": 1.44, "train": 0.257, "test": 0.263, "sym": 61, "net20": 0.246, "ok": True, "yp": 6, "yn": 6},
        "Q5": {"n": 369, "avg": 0.291, "t": 3.63, "win": 43.9, "pf": 1.49, "train": 0.223, "test": 0.392, "sym": 79, "net20": 0.278, "ok": True, "yp": 6, "yn": 6},
        "H1": {"n": 371, "avg": 0.235, "t": 3.19, "win": 48.0, "pf": 1.43, "train": 0.169, "test": 0.333, "sym": 75, "net20": 0.222, "ok": True, "yp": 6, "yn": 6},
        "H2": {"n": 377, "avg": 0.229, "t": 3.47, "win": 57.8, "pf": 1.52, "train": 0.226, "test": 0.233, "sym": 79, "net20": 0.216, "ok": True, "yp": 6, "yn": 6},
        "H3": {"n": 368, "avg": 0.239, "t": 3.18, "win": 48.1, "pf": 1.44, "train": 0.179, "test": 0.33, "sym": 71, "net20": 0.226, "ok": True, "yp": 6, "yn": 6},
        "H4": {"n": 367, "avg": 0.298, "t": 3.62, "win": 43.9, "pf": 1.51, "train": 0.24, "test": 0.385, "sym": 79, "net20": 0.285, "ok": True, "yp": 6, "yn": 6},
        "B2": {"n": 381, "avg": 0.301, "t": 4.01, "win": 44.9, "pf": 1.52, "train": 0.18, "test": 0.481, "sym": 75, "net20": 0.288, "ok": True, "yp": 6, "yn": 6},
        "B25": {"n": 372, "avg": 0.381, "t": 4.44, "win": 42.5, "pf": 1.63, "train": 0.304, "test": 0.495, "sym": 79, "net20": 0.368, "ok": True, "yp": 6, "yn": 6},
        "B3": {"n": 365, "avg": 0.358, "t": 3.8, "win": 38.6, "pf": 1.56, "train": 0.277, "test": 0.478, "sym": 79, "net20": 0.345, "ok": True, "yp": 6, "yn": 6},
        "B4": {"n": 360, "avg": 0.321, "t": 3.08, "win": 34.7, "pf": 1.47, "train": 0.314, "test": 0.331, "sym": 75, "net20": 0.308, "ok": True, "yp": 6, "yn": 6},
    },
    "SWP": {
        "CLASSIC": {"n": 2622, "avg": 0.098, "t": 3.92, "win": 52.9, "pf": 1.2, "train": 0.049, "test": 0.172, "sym": 75, "net20": 0.083, "ok": True, "yp": 5, "yn": 6},
        "Q1": {"n": 2247, "avg": 0.071, "t": 2.16, "win": 36.6, "pf": 1.11, "train": 0.025, "test": 0.14, "sym": 61, "net20": 0.056, "ok": False, "yp": 5, "yn": 6},
        "Q2": {"n": 2176, "avg": 0.063, "t": 1.86, "win": 36.4, "pf": 1.09, "train": 0.015, "test": 0.134, "sym": 57, "net20": 0.048, "ok": False, "yp": 5, "yn": 6},
        "Q3": {"n": 2029, "avg": 0.052, "t": 1.43, "win": 35.9, "pf": 1.08, "train": -0.026, "test": 0.169, "sym": 54, "net20": 0.037, "ok": False, "yp": 4, "yn": 6},
        "Q5": {"n": 2247, "avg": 0.072, "t": 2.27, "win": 36.6, "pf": 1.11, "train": 0.027, "test": 0.14, "sym": 61, "net20": 0.057, "ok": False, "yp": 5, "yn": 6},
        "H1": {"n": 2361, "avg": 0.094, "t": 3.28, "win": 43.5, "pf": 1.16, "train": 0.05, "test": 0.159, "sym": 71, "net20": 0.079, "ok": True, "yp": 5, "yn": 6},
        "H2": {"n": 2528, "avg": 0.09, "t": 3.56, "win": 52.7, "pf": 1.18, "train": 0.049, "test": 0.153, "sym": 71, "net20": 0.075, "ok": True, "yp": 5, "yn": 6},
        "H3": {"n": 2193, "avg": 0.079, "t": 2.63, "win": 43.2, "pf": 1.13, "train": 0.024, "test": 0.161, "sym": 71, "net20": 0.064, "ok": True, "yp": 4, "yn": 6},
        "H4": {"n": 2218, "avg": 0.067, "t": 2.07, "win": 36.5, "pf": 1.1, "train": 0.019, "test": 0.138, "sym": 61, "net20": 0.052, "ok": False, "yp": 5, "yn": 6},
        "B2": {"n": 2553, "avg": 0.064, "t": 2.26, "win": 36.3, "pf": 1.1, "train": 0.035, "test": 0.108, "sym": 71, "net20": 0.049, "ok": False, "yp": 4, "yn": 6},
        "B25": {"n": 2343, "avg": 0.078, "t": 2.34, "win": 31.9, "pf": 1.11, "train": 0.03, "test": 0.149, "sym": 64, "net20": 0.063, "ok": True, "yp": 5, "yn": 6},
        "B3": {"n": 2192, "avg": 0.078, "t": 2.09, "win": 28.6, "pf": 1.1, "train": 0.012, "test": 0.176, "sym": 75, "net20": 0.063, "ok": False, "yp": 5, "yn": 6},
        "B4": {"n": 2021, "avg": 0.054, "t": 1.23, "win": 24.4, "pf": 1.07, "train": -0.045, "test": 0.201, "sym": 54, "net20": 0.039, "ok": False, "yp": 3, "yn": 6},
    },
    "TS1": {
        "CLASSIC": {"n": 202, "avg": 0.063, "t": 0.71, "win": 55.4, "pf": 1.13, "train": 0.163, "test": -0.087, "sym": 57, "net20": 0.002, "ok": False, "yp": 4, "yn": 6},
        "Q1": {"n": 202, "avg": 0.291, "t": 2.36, "win": 41.6, "pf": 1.47, "train": 0.383, "test": 0.154, "sym": 75, "net20": 0.23, "ok": True, "yp": 6, "yn": 6},
        "Q2": {"n": 202, "avg": 0.283, "t": 2.27, "win": 41.6, "pf": 1.46, "train": 0.392, "test": 0.119, "sym": 71, "net20": 0.222, "ok": False, "yp": 6, "yn": 6},
        "Q3": {"n": 202, "avg": 0.318, "t": 2.38, "win": 41.6, "pf": 1.52, "train": 0.451, "test": 0.119, "sym": 71, "net20": 0.257, "ok": True, "yp": 5, "yn": 6},
        "Q5": {"n": 202, "avg": 0.269, "t": 2.31, "win": 41.6, "pf": 1.44, "train": 0.339, "test": 0.165, "sym": 71, "net20": 0.209, "ok": True, "yp": 6, "yn": 6},
        "H1": {"n": 202, "avg": 0.278, "t": 2.61, "win": 50.5, "pf": 1.53, "train": 0.38, "test": 0.125, "sym": 79, "net20": 0.217, "ok": True, "yp": 6, "yn": 6},
        "H2": {"n": 202, "avg": 0.119, "t": 1.27, "win": 55.9, "pf": 1.26, "train": 0.251, "test": -0.077, "sym": 68, "net20": 0.059, "ok": False, "yp": 5, "yn": 6},
        "H3": {"n": 202, "avg": 0.288, "t": 2.64, "win": 50.5, "pf": 1.55, "train": 0.396, "test": 0.127, "sym": 79, "net20": 0.228, "ok": True, "yp": 6, "yn": 6},
        "H4": {"n": 202, "avg": 0.266, "t": 2.25, "win": 41.6, "pf": 1.43, "train": 0.36, "test": 0.126, "sym": 71, "net20": 0.206, "ok": False, "yp": 6, "yn": 6},
        "B2": {"n": 202, "avg": 0.196, "t": 1.88, "win": 41.6, "pf": 1.32, "train": 0.206, "test": 0.181, "sym": 71, "net20": 0.135, "ok": False, "yp": 5, "yn": 6},
        "B25": {"n": 202, "avg": 0.167, "t": 1.43, "win": 35.1, "pf": 1.25, "train": 0.204, "test": 0.111, "sym": 61, "net20": 0.106, "ok": False, "yp": 5, "yn": 6},
        "B3": {"n": 202, "avg": 0.199, "t": 1.53, "win": 31.7, "pf": 1.28, "train": 0.282, "test": 0.074, "sym": 64, "net20": 0.138, "ok": False, "yp": 6, "yn": 6},
        "B4": {"n": 202, "avg": 0.312, "t": 2.01, "win": 28.2, "pf": 1.41, "train": 0.455, "test": 0.099, "sym": 61, "net20": 0.251, "ok": False, "yp": 5, "yn": 6},
    },
}


# ----------------------------------------------------------------------------- انتخاب
# اهداف محصول (هر دو ربات): وین‌ریت > ۴۵٪ ، PF بین ۱٫۴ و ۱٫۶ ، دورترین تارگت ≥ ۲٫۵R (فاصله‌ی تارگت‌ها، نه R:R تحقق‌یافته).
TARGET = {"win_min": 45.0, "pf_min": 1.4, "pf_max": 1.6, "rr_min": 2.5}


def _far_target(key: str) -> float:
    try:
        return float(LADDERS[key][0][-1])
    except Exception:
        return 0.0


def rank_ladders(stats: Optional[Dict[str, Dict[str, Any]]]) -> List[Tuple[str, Dict[str, Any]]]:
    """نردبان‌ها از بهترین به بدترین. طبقه‌ها: (۰) تأییدشده + وین‌ریت ≥ TARGET + دورترین تارگت ≥ ۲٫۵R ،
    (۱) بقیه‌ی تأییدشده‌ها ، (۲) تأییدنشده‌ها ، (۳) تک‌تارگت‌های B* (آخرین اولویت، چون آمار آزمایشگاهشان با رفتار زنده نمی‌خواند). داخل هر طبقه: میانگین R خالص (بزرگ‌تر بهتر) ، بعد t. ورودی خالی → []"""
    if not stats:
        return []
    items = [(k, v) for k, v in stats.items() if k in LADDERS and isinstance(v, dict) and isinstance(v.get("avg"), (int, float))]

    def tier(kv):
        k, v = kv
        if len(LADDERS[k][0]) < 2:        # تک‌تارگت‌ها (B*): شبیه‌ساز آزمایشگاه با ماشین‌حالت زنده (۴ تارگتِ مرحله‌ای با جابه‌جایی استاپ) یکی نیست
            return 3                      # (تست test_modules_live: ۱۱۰–۱۴۰ از ۲۵۰ مسیر متفاوت، زنده ۰٫۰۹ تا ۰٫۳۲R بدتر) → خودکار انتخاب نمی‌شوند
        if not v.get("ok"):
            return 2
        meets = float(v.get("win", 0) or 0) >= TARGET["win_min"] and _far_target(k) >= TARGET["rr_min"]
        return 0 if meets else 1

    items.sort(key=lambda kv: (tier(kv), -float(kv[1]["avg"]), -float(kv[1].get("t", 0) or 0), kv[0]))
    return items


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
    pf = f"PF≈{st['pf']:.2f}" if STATS_PF_ESTIMATED and st.get("net20") is not None else f"PF {st['pf']:.2f}"
    return (f"{mark} {st['avg']:+.2f}R/trade · {pf} · win {st['win']:.0f}% · n={st['n']} · t={st['t']:.1f}")


def ladder_summary(key: str, lang: str = "en") -> str:
    return (DESC_FA if lang == "fa" else DESC_EN).get(key, key)

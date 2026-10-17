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
MODULES = ("HDV3", "SWP", "TS1")  # ماژول‌های استراتژی جدید

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
}
for _m in MODULES:
    SCHEMA[f"mod_{_m}"] = ("bool", False, None, None)       # ماژول جدید: پیش‌فرض خاموش
# نردبان مدیریت معامله‌ی هر ماژول: "auto" = پیشنهاد سیستم (RECOMMENDED_PROFILE)
PROFILE_CHOICES = ("auto", "CLASSIC", "Q1", "Q2", "Q3", "Q5")
RECOMMENDED_PROFILE = {"HDV3": "Q1", "SWP": "CLASSIC", "TS1": "Q1"}
RECOMMENDED_WHY = {
    "HDV3": "Backtest 28 coins/5y: first target at 2R beat the classic 1R ladder (+0.286R vs +0.181R per trade).",
    "SWP": "Backtest: the classic ladder (1R first) beat fixed 2R (+0.082R confirmed vs +0.057R not confirmed) — range reversals fade fast.",
    "TS1": "Experimental, small sample: using the same 2R-first ladder as HDV3 (no evidence for a better one).",
}
for _m in MODULES:
    SCHEMA[f"prof_{_m}"] = ("choice", "auto", None, PROFILE_CHOICES)
for _t in TF_KEYS:
    SCHEMA[f"tf_{_t}"] = ("bool", True, None, None)         # تایم‌فریم فعال (فقط برای ماژول‌های جدید)

# ماژول تأییدشده‌ی ۴ساعته می‌تواند بعد از تأیید ادمین روشن شود؛ بقیه آزمایشی‌اند.
MODULE_INFO = {
    "HDV3": ("4h", "تأییدشده (۴ساعته)"),
    "SWP":  ("4h", "تأییدشده (۴ساعته، با فیلتر رژیم)"),
    "TS1":  ("1h", "آزمایشی (تأیید آماری ندارد)"),
}


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
REMOTE_BASE = "https://raw.githubusercontent.com/mahdi744/TICB-En/main/data/"
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
                m = get("manual_news.json")
                _remote_cache["manual"] = m if isinstance(m, list) else []
            except Exception:
                pass
        except Exception:
            pass
    if isinstance(_remote_cache["ctl"], dict):
        return sanitize(_remote_cache["ctl"].get(section)), _remote_cache["manual"]
    return load(section), manual_news()


def effective_profile(ctl: Dict[str, Any], module: str) -> str:
    p = ctl.get(f"prof_{module}", "auto")
    return RECOMMENDED_PROFILE.get(module, "Q1") if p in ("auto", "", None) else p


def apply_recommended(section: str, path: str = None) -> None:
    for m in MODULES:
        set_value(section, f"prof_{m}", "auto", path)

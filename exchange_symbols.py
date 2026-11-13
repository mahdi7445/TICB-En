# -*- coding: utf-8 -*-
"""
exchange_symbols.py — نام قرارداد «دقیقاً مطابق صرافی» + مقیاس قیمت (مشترک بین SGNL و TICB؛ هر دو ریپو یک نسخه‌ی یکسان دارند).

مشکل: توبیت (و بیشتر صرافی‌های فیوچرز) برای رمزارزهای خیلی ارزان، قرارداد «×۱۰۰۰» لیست می‌کند:
    PEPE  →  1000PEPEUSDT   (قیمت 0.0044646  ≈ ۱۰۰۰ × قیمت اسپات PEPE)
    SHIB  →  1000SHIBUSDT   (قیمت 0.004324)
    FLOKI →  1000FLOKIUSDT  (قیمت 0.02043)
ولی داده‌ی کندل (Kraken/Coinbase/TwelveData) قیمت اسپات را می‌دهد. اگر سیگنال با نام «PEPE» و قیمت اسپات منتشر شود،
ربات ترید خودکار «PEPEUSDT» را در توبیت پیدا نمی‌کند (یا قیمت ۱۰۰۰ برابر اشتباه می‌شود). این ماژول هر دو را درست می‌کند:
  • lookup("PEPE")  → ("1000PEPE", 1000.0)     نام قرارداد + ضریب قیمت
  • scale(0.0000044646, 1000.0) → 0.0044646

منبع جدول (به‌ترتیب اولویت):
  1) متغیر محیطی EXCHANGE_SYMBOL_MAP  (مثلاً  "BONK=1000BONK,SATS=1000SATS:1000")  — برای override دستی
  2) کشف زنده از API عمومی توبیت (exchangeInfo؛ بدون کلید)، کش ۶ ساعته، خطا ⇒ نادیده
  3) جدول ثابت پشتیبان (۳ مورد تأییدشده با تصاویر صرافی + چند مورد رایج)
اگر نمادی در هیچ‌کدام نباشد نام و قیمت دست‌نخورده می‌ماند (رفتار قبلی). خاموش‌کردن کامل: EXCHANGE_SYMBOLS=0 .
هیچ خطایی از این ماژول به بیرون نمی‌رسد.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from typing import Dict, Optional, Tuple

TOOBIT_BASE = (os.environ.get("TOOBIT_API_BASE") or "https://api.toobit.com").rstrip("/")
LIVE_TTL = 6 * 3600.0

# پیشوندهای مقیاس (متن → ضریب). «1» تنها عمداً نیست تا ۱INCH و مشابه اشتباه تشخیص داده نشوند.
PREFIX_MULT = {"1000000": 1e6, "100000": 1e5, "10000": 1e4, "1000": 1e3, "1M": 1e6, "1B": 1e9, "100K": 1e5, "10K": 1e4, "1K": 1e3}
_PREFIX_RE = re.compile(r"^(?P<p>1000000|100000|10000|1000|1M|1B|100K|10K|1K)(?P<core>[A-Z][A-Z0-9]*)$")

# جدول ثابت پشتیبان: spot-ticker → (نام قرارداد، ضریب). سه مورد اول با تصاویر توبیت تأیید شده‌اند.
STATIC: Dict[str, Tuple[str, float]] = {
    "PEPE": ("1000PEPE", 1000.0),
    "SHIB": ("1000SHIB", 1000.0),
    "FLOKI": ("1000FLOKI", 1000.0),
    "BONK": ("1000BONK", 1000.0),
    "SATS": ("1000SATS", 1000.0),
    "LUNC": ("1000LUNC", 1000.0),
    "XEC": ("1000XEC", 1000.0),
    "RATS": ("1000RATS", 1000.0),
}

_lock = threading.Lock()
_live: Dict[str, Tuple[str, float]] = {}
_live_at = 0.0
_live_ok = False


def enabled() -> bool:
    return (os.environ.get("EXCHANGE_SYMBOLS") or "1").strip().lower() not in ("0", "false", "no", "off")


def mult_of_prefix(prefix: str) -> float:
    return PREFIX_MULT.get(str(prefix or "").upper(), 1.0)


def split_prefix(base: str) -> Tuple[str, float]:
    """'1000PEPE' → ('PEPE', 1000.0) ؛ 'PEPE' → ('PEPE', 1.0) ؛ '1INCH' → ('1INCH', 1.0)."""
    b = str(base or "").upper().strip()
    m = _PREFIX_RE.match(b)
    if not m:
        return b, 1.0
    return m.group("core"), mult_of_prefix(m.group("p"))


def _env_map() -> Dict[str, Tuple[str, float]]:
    out: Dict[str, Tuple[str, float]] = {}
    raw = (os.environ.get("EXCHANGE_SYMBOL_MAP") or "").strip()
    for part in raw.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().upper()
        mult = None
        if ":" in v:
            v, ms = v.split(":", 1)
            try:
                mult = float(ms)
            except ValueError:
                mult = None
        v = v.strip().upper()
        if not k or not v:
            continue
        if mult is None:
            mult = split_prefix(v)[1] if split_prefix(v)[0] == k else 1.0
        out[k] = (v, mult)
    return out


def parse_exchange_info(info) -> Dict[str, Tuple[str, float]]:
    """exchangeInfo → {spot_ticker: (contract_base, mult)} فقط برای قراردادهای USDT که نامشان پیشوند مقیاس دارد و نسخه‌ی
    بدون پیشوندش در همان صرافی لیست نشده است."""
    rows = []
    if isinstance(info, dict):
        rows = info.get("contracts") or info.get("symbols") or info.get("data") or []
    elif isinstance(info, list):
        rows = info
    bases = set()
    for c in rows:
        if not isinstance(c, dict):
            continue
        sym = str(c.get("symbol") or "")
        if "-SWAP-" in sym:
            if not sym.upper().endswith("-USDT"):
                continue
            bases.add(sym.split("-")[0].upper())
        elif sym.upper().endswith("USDT") and "-" not in sym:
            bases.add(sym.upper()[:-4])
    out: Dict[str, Tuple[str, float]] = {}
    for b in bases:
        core, mult = split_prefix(b)
        if mult != 1.0 and core and core not in bases:
            out[core] = (b, mult)
    return out


def refresh(force: bool = False, fetcher=None) -> bool:
    """جدول زنده را از توبیت می‌گیرد (کش ۶ ساعته). موفق ⇒ True. هیچ‌وقت exception نمی‌دهد."""
    global _live, _live_at, _live_ok
    if not enabled() or (os.environ.get("EXCHANGE_SYMBOLS_LIVE") or "1").strip() in ("0", "false", "no", "off"):
        return False
    now = time.time()
    with _lock:
        if not force and _live_at and now - _live_at < LIVE_TTL:
            return _live_ok
        _live_at = now                       # شکست هم کش می‌شود (۶ ساعت) تا هر پیام یک درخواست شبکه نزند؛ force=True دوباره امتحان می‌کند
    try:
        if fetcher is not None:
            info = fetcher()
        else:
            req = urllib.request.Request(TOOBIT_BASE + "/api/v1/exchangeInfo", headers={"User-Agent": "tradeiscool-bot"})
            with urllib.request.urlopen(req, timeout=8) as r:
                info = json.loads(r.read().decode("utf-8"))
        table = parse_exchange_info(info)
        if table:
            with _lock:
                _live = table
                _live_ok = True
            return True
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        if not _live:
            _live_ok = False
    return _live_ok


def lookup(base: str, *, live: bool = True) -> Tuple[str, float]:
    """(نام قرارداد، ضریب قیمت) برای یک spot-ticker. ناشناخته ⇒ (base, 1.0)."""
    b = str(base or "").upper().strip()
    if not b or not enabled():
        return b, 1.0
    try:
        env = _env_map()
        if b in env:
            return env[b]
        if live:
            refresh()
        with _lock:
            if b in _live:
                return _live[b]
            if _live_ok:                      # لیست زنده در دست است و این نماد در آن «پیشوندی» نیست ⇒ نام ساده درست است
                return b, 1.0
        if b in STATIC:
            return STATIC[b]
    except Exception:  # noqa: BLE001
        pass
    return b, 1.0


def display(base: str) -> str:
    return lookup(base)[0]


def scale(value, mult: float):
    try:
        if value is None or not mult or float(mult) == 1.0:
            return value
        return float(value) * float(mult)
    except (TypeError, ValueError):
        return value


def trade_fields(base: str) -> Dict[str, object]:
    """فیلدهایی که هنگام باز شدن معامله روی آن ذخیره می‌شوند تا نام/ضریب در تمام عمر معامله ثابت بماند."""
    name, mult = lookup(base)
    if name == str(base or "").upper().strip() and mult == 1.0:
        return {}
    return {"ex_name": name, "px_mult": mult}

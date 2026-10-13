# -*- coding: utf-8 -*-
"""پارسر هوشمند «سیگنال شخصی ادمین» (فارسی/انگلیسی/مخلوط) برای ربات TICB.

ورودی: متن آزاد (تایپ‌شده یا فوروارد از هر کانال). خروجی: یک CustomSignal دقیق یا دلیل رد.
اصل طراحی: «هر چیز مشکوک رد می‌شود، حدس زده نمی‌شود» - چون یک پارس غلط یعنی یک سیگنال غلط
در کانال مشترکین. پارسر کاملاً مستقل است (هیچ import ای از بقیه‌ی ربات ندارد) و هیچ‌جا
حالت سراسری تغییر نمی‌دهد.

چه چیزهایی فهمیده می‌شود (حروف بزرگ/کوچک مهم نیست، ارقام فارسی/عربی، 85,956، ۶۰٬۰۰۰، 60k):
  نماد    BTCUSDT, btcusdt.p, BTC/USDT, BTC-USD, BTC PERP, #BTC, $BTC, «BTC long»,
          و نام فارسی (بیت کوین، اتریوم، سولانا، ...)  → خروجی TICKER/USD
  جهت     buy/long/خرید/لانگ  یا  sell/short/فروش/شورت (اگر نباشد از روی اعداد استنباط می‌شود؛
          اگر صریحاً گفته شده و با اعداد نخواند → رد)
  ورود    entry, entry price, enter, ورود, قیمت ورود, buy/sell limit X, buy/sell at X ...
  نوع     market (market/cmp/now/بازار/مارکت/قیمت فعلی) | limit (پیش‌فرض/لیمیت)
          | stop_limit (stop limit / استاپ لیمیت؛ trigger + limit) | stop_market (buy stop / sell stop)
  حد ضرر  stop loss, stoploss, SL, S/L, stop, حد ضرر, استاپ, ضرر
  تارگت   target N, TP, TP1, T1, take profit, tgt, حد سود, تارگت, هدف, close/exit/بستن/خروج + قیمت
          و لیست زیر هدر «Targets:»
  تایم‌فریم (اختیاری) 1m 5m 15m 30m 1h 4h 1d ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

MAX_TARGETS = 6

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_K_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s?[kK](?![A-Za-z0-9])")

_STOP_LIMIT_PH = re.compile(r"(?:\bstop\s*[-_]?\s*limit\b|استاپ\s*[-_]?\s*لیمیت|استاپ\s*لیمت)", re.IGNORECASE)
_STOP_ORDER_PH = re.compile(r"\b(?:buy|sell)\s*[-_]?\s*stop\b(?!\s*[-_]?\s*(?:loss|limit))", re.IGNORECASE)
_STOP_PRICE_PH = re.compile(r"(?:\bstop\s*price\b|\btrigger\s*price\b|\bactivation\s*price\b)", re.IGNORECASE)

_TRIG_RE = re.compile(r"(?:\btrigger\b|\bactivation\b|تریگر|فعال\s*سازی)", re.IGNORECASE)
_ENTRY_RE = re.compile(
    r"(?:\bentry(?:\s*(?:price|zone|point|level|area))?\b|\benter\b|\bentries\b|\blimit\s*price\b"
    r"|\blimit\b(?=\s*[:=@]\s*\d)|قیمت\s*ورود|ورودی|\bورود\b"
    r"|\b(?:buy|sell|long|short)\s*(?:limit\s*)?(?:at|@)\b|\b(?:buy|sell)\s+limit\b)", re.IGNORECASE)
_STOP_RE = re.compile(
    r"(?:stop\s*[-_/]?\s*loss|\bstoploss\b|\bs\s*/\s*l\b|(?<![a-z])sl(?![a-z])|(?<![a-z])stop(?![a-z])"
    r"|حد\s*ضرر|حدضرر|استاپ|ضرر)", re.IGNORECASE)
_TP_RE = re.compile(
    r"(?:take\s*[-_]?\s*profits?|profit\s*targets?|(?<![a-z])t\s*/\s*p(?![a-z])|\btargets?(?![a-z])|\btgts?(?![a-z])"
    r"|(?<![a-z])tps?(?![a-z])|(?<![a-z])tp(?=\d)|(?<![a-z0-9])t(?=\d\b)"
    r"|\b(?:close|exit)(?:\s*(?:at|@|price))?(?![a-z])"
    r"|حد\s*سود|تارگت|اهداف|هدف|تی\s*پی|بستن|خروج)\s*[#:.\-]?\s*(?:(\d{1,2})(?![\d.]))?", re.IGNORECASE)
_SHORT_RE = re.compile(r"(?:\bsell\b|\bshort\b|فروش|شورت)", re.IGNORECASE)
_LONG_RE = re.compile(r"(?:\bbuy\b|\blong\b|خرید|لانگ)", re.IGNORECASE)
_MARKET_RE = re.compile(r"(?:\bmarket\b|\bcmp\b|\bnow\b|current\s*price|at\s*market|بازار|مارکت|قیمت\s*فعلی|همین\s*الان|فوری)", re.IGNORECASE)
_LIMIT_RE = re.compile(r"(?:\blimit\b|لیمیت|لیمت)", re.IGNORECASE)
_SYMBOL_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,10})\s*[/\-_ ]?\s*(?:USDT|USD|USDC)(?:\.?P(?:ERP)?)?(?![A-Za-z])", re.IGNORECASE)
_TAG_RE = re.compile(r"(?:#|\$)([A-Za-z]{2,10})(?![A-Za-z0-9])")
_COIN_SIDE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6})\s+(?:long|short|buy|sell)\b", re.IGNORECASE)
_TF_RE = re.compile(r"(?<![A-Za-z0-9])(1|3|5|15|30)\s?(?:m|min)(?![A-Za-z])|(?<![A-Za-z0-9])(1|2|4|6|12)\s?(?:h|hr)(?![A-Za-z])|(?<![A-Za-z0-9])(1)\s?d(?![A-Za-z])", re.IGNORECASE)
_SIDE_ENTRY_RE = re.compile(
    r"(?:\b(?:buy|sell|long|short)\b|خرید|فروش|لانگ|شورت)[^\S\n]*(?:limit[^\S\n]*)?"
    r"(?:([A-Za-z]{2,10})(?:[^\S\n]*[/\-_]?[^\S\n]*(?:USDT|USD)(?:\.?P(?:ERP)?)?)?[^\S\n]*)?"
    r"(?:\b(?:at|price|in)\b|@|:|=|در|از|->|➡️)?[^\S\n]*(\d+(?:\.\d+)?)", re.IGNORECASE)
_NOT_COINS = {"ENTRY", "STOP", "LOSS", "TARGET", "TP", "SL", "LIMIT", "SELL", "BUY", "LONG", "SHORT", "MARKET", "PRICE",
              "TAKE", "PROFIT", "TARGETS", "ZONE", "SIGNAL", "FUTURES", "SPOT", "PERP", "USDT", "USD", "USDC", "FREE", "VIP",
              "NEW", "THE", "AND", "FOR", "NOW", "CMP", "RISK", "LEVERAGE", "CROSS", "ISOLATED", "SWING", "SCALP",
              "TRIGGER", "CLOSE", "EXIT", "TGT", "TGTS", "CALL", "ALERT", "SETUP", "TRADE", "ENTER", "AT", "IN"}
_ENTRY_FILLER = {"NOW", "AT", "IN", "CMP", "MARKET", "PRICE", "LIMIT", "ENTRY", "ENTER"}
_COMMON = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "LTC", "TON", "AVAX", "LINK", "DOT", "SHIB", "PEPE",
           "BCH", "XLM", "NEAR", "ATOM", "UNI", "APT", "ARB", "OP", "SUI", "INJ", "FIL", "ETC", "HBAR", "AAVE", "TAO", "WIF",
           "ENA", "SEI", "TIA", "RUNE", "FTM", "ALGO", "ICP", "ZEC", "XMR", "MATIC", "POL", "BONK", "FLOKI", "JUP", "ONDO"}
_WORD_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6})(?![A-Za-z0-9])")
_CLEAN_PAREN = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_CLEAN_UNITS = re.compile(r"[+\-]?\d+(?:\.\d+)?\s*(?:%|x\b|×|usdt\b|r\b|pips?\b)", re.IGNORECASE)
_RANGE_RE = re.compile(r"^\s*[:=@\-]*\s*(\d+(?:\.\d+)?)\s*(?:[-–—~/]|to|تا)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_LIST_ITEM_RE = re.compile(r"^\W*(?:\d{1,2}\s*[).:\-]\s*)?(\d+(?:\.\d+)?)\W*$")

_FA_COINS = {
    "بیت کوین": "BTC", "بیتکوین": "BTC", "بیت‌کوین": "BTC", "اتریوم": "ETH", "سولانا": "SOL", "ریپل": "XRP",
    "دوج کوین": "DOGE", "دوجکوین": "DOGE", "دوج": "DOGE", "کاردانو": "ADA", "ترون": "TRX", "لایت کوین": "LTC",
    "لایتکوین": "LTC", "بایننس کوین": "BNB", "تون کوین": "TON", "آوالانچ": "AVAX", "چین لینک": "LINK",
    "پولکادات": "DOT", "شیبا": "SHIB", "پپه": "PEPE", "ستاره": "XLM", "استلار": "XLM",
}
_FA_COIN_RE = re.compile("|".join(sorted((re.escape(k) for k in _FA_COINS), key=len, reverse=True)))


@dataclass
class CustomSignal:
    symbol: str                      # BTC/USD (فرمت داخلی ربات)
    base: str                        # BTC
    side: str                        # BUY | SELL
    order_type: str                  # market | limit | stop_limit | stop_market
    entry: Optional[Decimal]         # قیمت ورود (limit/stop_limit: قیمت لیمیت؛ stop_market: قیمت تریگر؛ market: مرجع اختیاری)
    stop: Decimal
    targets: List[Decimal]           # نزدیک‌ترین اول
    trigger: Optional[Decimal] = None
    tf_label: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _expand_k(m) -> str:
    return format((Decimal(m.group(1)) * 1000).normalize(), "f")


def _norm(text: str) -> str:
    t = str(text or "").translate(_DIGITS)
    t = _URL_RE.sub(" ", t)
    t = re.sub(r"(?<=\d)[,\u066c\u060c\u2019'](?=\d{3}(?!\d))", "", t)
    t = _K_RE.sub(_expand_k, t)
    t = t.replace("٫", ".").replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    return t.replace("：", ":").replace("＠", "@")


def _nums(s: str) -> List[Decimal]:
    out = []
    for m in _NUM_RE.findall(s):
        try:
            out.append(Decimal(m))
        except InvalidOperation:
            pass
    return out


def _clean_seg(seg: str) -> str:
    return _CLEAN_UNITS.sub(" ", _CLEAN_PAREN.sub(" ", seg))


def _price_after_label(seg: str):
    seg = _clean_seg(seg)
    nums = _nums(seg)
    if not nums:
        return None, False, 0
    m = _RANGE_RE.match(seg)
    if m:
        try:
            a, b = Decimal(m.group(1)), Decimal(m.group(2))
            if a > 0 and a != b and abs(a - b) / a < Decimal("0.2"):
                return nums[0], True, 2
        except InvalidOperation:
            pass
    return nums[0], False, len(set(nums))


def _prep(text: str) -> Tuple[str, bool, bool]:
    """متن نرمال‌شده + پرچم stop_limit / stop_order؛ عبارت‌های نوع سفارش از متن حذف می‌شوند تا با «stop loss» قاطی نشوند."""
    t = _norm(text)
    t = _STOP_PRICE_PH.sub(" trigger ", t)
    stop_limit = bool(_STOP_LIMIT_PH.search(t))
    t = _STOP_LIMIT_PH.sub(" ", t)
    stop_order = bool(_STOP_ORDER_PH.search(t))
    t = _STOP_ORDER_PH.sub(lambda m: m.group(0).split()[0], t)
    return t, stop_limit, stop_order


def looks_like_signal(text: str) -> bool:
    """پیش‌بررسی ارزان: حد ضرر + (ورود/تارگت/لیمیت/مارکت) + عدد."""
    t, _a, _b = _prep(text)
    if not _NUM_RE.search(t):
        return False
    return bool(_STOP_RE.search(t)) and bool(_ENTRY_RE.search(t) or _TP_RE.search(t) or _LIMIT_RE.search(t) or _MARKET_RE.search(t))


def _find_symbol(t: str, fa_hint: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str]]:
    """(base, خطا). اگر چند نماد متفاوت باشد خطا می‌دهد."""
    explicit, tags = [], []
    for m in _SYMBOL_RE.finditer(t):
        b = m.group(1).upper()
        if b not in _NOT_COINS and b not in explicit:
            explicit.append(b)
    for m in _TAG_RE.finditer(t):
        b = m.group(1).upper()
        if b not in _NOT_COINS and b not in tags:
            tags.append(b)
    fa = list(fa_hint or [])
    pool = explicit or tags or fa
    if len(pool) > 1:
        return None, "چند نماد مختلف در یک پیام پیدا شد (" + ", ".join(pool[:4]) + ")؛ هر سیگنال را جدا بفرستید."
    if pool:
        return pool[0], None
    for m in _COIN_SIDE_RE.finditer(t):
        b = m.group(1).upper()
        if b not in _NOT_COINS:
            return b, None
    bare = []
    for m in _WORD_RE.finditer(t):
        b = m.group(1).upper()
        if b in _COMMON and b not in bare:
            bare.append(b)
    if len(bare) == 1:
        return bare[0], None
    if len(bare) > 1:
        return None, "چند نماد مختلف در یک پیام پیدا شد (" + ", ".join(bare[:4]) + ")؛ هر سیگنال را جدا بفرستید."
    return None, "نماد رمزارز پیدا نشد (مثلاً BTCUSDT یا #BTC یا بیت کوین)."


def parse_custom_signal(text: str) -> Tuple[Optional[CustomSignal], str]:
    """(CustomSignal, '') یا (None, دلیل فارسی)."""
    t, stop_limit, stop_order = _prep(text)
    if not t.strip():
        return None, "متن خالی است."
    fa_found: List[str] = []
    for m in _FA_COIN_RE.finditer(t):
        if _FA_COINS[m.group(0)] not in fa_found:
            fa_found.append(_FA_COINS[m.group(0)])
    t = _FA_COIN_RE.sub(lambda m: " " + _FA_COINS[m.group(0)] + " ", t)
    base, err = _find_symbol(t, fa_found)
    if err:
        return None, err

    entry_vals: List[Decimal] = []
    trig_vals: List[Decimal] = []
    stop_vals: List[Decimal] = []
    tps: Dict[int, Decimal] = {}
    loose: List[Decimal] = []
    range_seen = False
    multi_entry = False
    in_tp_block = False
    pending_kind = None

    for line in t.splitlines():
        low = line.strip()
        if not low:
            in_tp_block = False
            continue
        if pending_kind:
            kind_p, pending_kind = pending_kind, None
            mp = re.match(r"^\W*(\d+(?:\.\d+)?)\W*$", low)
            if mp:
                {"entry": entry_vals, "stop": stop_vals, "trig": trig_vals}[kind_p].append(Decimal(mp.group(1)))
                continue
        found = ([("stop", m) for m in _STOP_RE.finditer(low)] + [("entry", m) for m in _ENTRY_RE.finditer(low)]
                 + [("tp", m) for m in _TP_RE.finditer(low)] + [("trig", m) for m in _TRIG_RE.finditer(low)])
        if in_tp_block and not found:
            li = _LIST_ITEM_RE.match(_clean_seg(low))
            if li:
                loose.append(Decimal(li.group(1)))
                continue
            in_tp_block = False
        spans = sorted([(m.start(), kind, m) for kind, m in found], key=lambda x: (x[0], -(x[2].end() - x[2].start())))
        dedup = []
        for sp in spans:
            if dedup and sp[0] < dedup[-1][2].end() and sp[1] == dedup[-1][1]:
                continue
            dedup.append(sp)
        spans = dedup
        for i, (pos, kind, m) in enumerate(spans):
            end = spans[i + 1][0] if i + 1 < len(spans) else len(low)
            seg = low[m.end():end]
            if kind == "tp":
                idx = m.group(1)
                nums = _nums(_clean_seg(seg))
                if idx and idx.isdigit() and nums:
                    tps[int(idx)] = nums[0]
                    in_tp_block = False
                elif nums:
                    loose.extend(nums)
                    in_tp_block = True
                else:
                    in_tp_block = True
            elif kind in ("entry", "trig"):
                v, is_range, distinct = _price_after_label(seg)
                if v is None and i == len(spans) - 1 and not _MARKET_RE.search(seg):
                    pending_kind = kind
                if v is not None:
                    if kind == "entry":
                        if is_range:
                            range_seen = True
                        elif distinct > 1:
                            multi_entry = True
                        entry_vals.append(v)
                    else:
                        trig_vals.append(v)
            else:
                v, _r, _d = _price_after_label(seg)
                if v is None and i == len(spans) - 1:
                    pending_kind = "stop"
                if v is not None:
                    stop_vals.append(v)

    has_market_word = bool(_MARKET_RE.search(t))
    has_limit_word = bool(_LIMIT_RE.search(t))
    if not entry_vals and not trig_vals:
        for line in t.splitlines():
            for m in _SIDE_ENTRY_RE.finditer(_clean_seg(line)):
                w = (m.group(1) or "").upper()
                if w in _NOT_COINS and w not in _ENTRY_FILLER:
                    continue
                entry_vals.append(Decimal(m.group(2)))
                break
            if entry_vals:
                break

    if range_seen:
        return None, "قیمت ورود به‌صورت محدوده داده شده؛ برای امنیت فقط یک قیمت ورود پذیرفته می‌شود (با یک عدد دوباره بفرستید)."
    if multi_entry or len(set(entry_vals)) > 1:
        return None, f"برای ورود چند قیمت مختلف داده شده ({', '.join(str(x) for x in entry_vals[:3])})؛ مبهم است."
    if len(set(trig_vals)) > 1:
        return None, "برای تریگر چند قیمت مختلف داده شده؛ مبهم است."
    if not stop_vals:
        return None, "حد ضرر (stop loss) پیدا نشد."
    if len(set(stop_vals)) > 1:
        return None, "برای حد ضرر چند قیمت مختلف داده شده؛ مبهم است."
    stop = stop_vals[0]
    if stop <= 0:
        return None, "قیمت‌ها باید مثبت باشند."

    warnings: List[str] = []
    entry: Optional[Decimal] = entry_vals[0] if entry_vals else None
    trigger: Optional[Decimal] = trig_vals[0] if trig_vals else None

    if stop_limit:
        order_type = "stop_limit"
        if trigger is None and entry is None:
            return None, "برای Stop Limit قیمت تریگر و قیمت لیمیت پیدا نشد."
        if trigger is None:
            trigger = entry
            warnings.append("تریگر مشخص نبود؛ برابر قیمت لیمیت گرفته شد.")
        if entry is None:
            entry = trigger
            warnings.append("قیمت لیمیت مشخص نبود؛ برابر تریگر گرفته شد.")
    elif stop_order:
        order_type = "stop_market"
        if entry is None:
            entry = trigger
        if entry is None:
            return None, "برای سفارش Stop (buy stop / sell stop) قیمت فعال‌سازی پیدا نشد."
        trigger = entry
    else:
        if trigger is not None and entry is None:
            entry = trigger
        trigger = None
        if entry is None:
            if has_market_word:
                order_type = "market"
            else:
                return None, "قیمت ورود (entry) پیدا نشد (برای ورود بازاری کلمه‌ی market یا «بازار» بنویسید)."
        elif has_market_word and not has_limit_word:
            order_type = "market"
        else:
            order_type = "limit"
    if entry is not None and entry <= 0:
        return None, "قیمت‌ها باید مثبت باشند."
    if entry is not None and entry == stop:
        return None, "قیمت ورود و حد ضرر برابرند."

    ref = entry if entry is not None else stop
    lo, hi = ref * Decimal("0.3"), ref * Decimal("3")
    raw = [tps[k] for k in sorted(tps)] + loose
    prices = [p for p in raw if lo <= p <= hi and p > 0]
    if not prices:
        return None, "هیچ تارگتی (target/TP) پیدا نشد."
    uniq, seen = [], set()
    for p in prices:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    prices = uniq

    has_short, has_long = bool(_SHORT_RE.search(t)), bool(_LONG_RE.search(t))
    if entry is not None:
        geo = "SELL" if stop > entry else "BUY"
    else:
        geo = "SELL" if stop > max(prices) else ("BUY" if stop < min(prices) else "")
        if not geo:
            return None, "جهت معامله از روی حد ضرر و تارگت‌ها مشخص نیست."
    if has_short and has_long:
        side = geo
    elif has_short:
        side = "SELL"
    elif has_long:
        side = "BUY"
    else:
        side = geo
    if side != geo:
        return None, (f"جهت ({'فروش/Short' if side == 'SELL' else 'خرید/Long'}) با اعداد نمی‌خواند: "
                      f"حد ضرر باید {'بالاتر' if side == 'SELL' else 'پایین‌تر'} از ورود باشد.")

    base_price = entry if entry is not None else stop
    bad = [p for p in prices if (p >= base_price if side == "SELL" else p <= base_price)]
    if bad:
        return None, (f"تارگت {', '.join(str(x) for x in bad)} سمت سود قیمت ورود نیست "
                      f"({'پایین‌تر' if side == 'SELL' else 'بالاتر'} از ورود باید باشد).")
    if entry is not None and abs(entry - stop) / entry > Decimal("0.5"):
        return None, "فاصله‌ی حد ضرر از ورود بیش از ۵۰٪ است؛ احتمالاً عدد اشتباه است."
    if trigger is not None and order_type == "stop_limit":
        # تریگر باید هم‌سمت ورود باشد و حد ضرر را نشکند
        if (side == "BUY" and trigger <= stop) or (side == "SELL" and trigger >= stop):
            return None, "قیمت تریگر با حد ضرر نمی‌خواند (تریگر نباید از حد ضرر رد شده باشد)."
    prices.sort(reverse=(side == "SELL"))
    if len(prices) > MAX_TARGETS:
        warnings.append(f"فقط {MAX_TARGETS} تارگت اول (نزدیک‌ترین‌ها) استفاده شد.")
        prices = prices[:MAX_TARGETS]

    tf = None
    mt = _TF_RE.search(t)
    if mt:
        if mt.group(1):
            tf = f"{mt.group(1)}M"
        elif mt.group(2):
            tf = f"{mt.group(2)}H"
        else:
            tf = "1D"
    return CustomSignal(symbol=f"{base}/USD", base=base, side=side, order_type=order_type, entry=entry,
                        stop=stop, targets=prices, trigger=trigger, tf_label=tf, warnings=warnings), ""


# سهم هر تارگت از پوزیشن (جمع = ۱۰۰٪؛ بدون رانر - ادمین خودش تارگت‌ها و استاپِ ثابت داده است).
_WEIGHTS = {
    1: ["1"], 2: ["0.5", "0.5"], 3: ["0.4", "0.3", "0.3"], 4: ["0.3", "0.3", "0.2", "0.2"],
    5: ["0.25", "0.25", "0.2", "0.15", "0.15"], 6: ["0.2", "0.2", "0.2", "0.15", "0.15", "0.1"],
}


def target_weights(n: int) -> List[Decimal]:
    ws = [Decimal(x) for x in _WEIGHTS[max(1, min(int(n), MAX_TARGETS))]]
    assert sum(ws) == Decimal(1)
    return ws

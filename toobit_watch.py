# -*- coding: utf-8 -*-
"""
toobit_watch.py — «دیده‌بان توبیت» برای بخش R&D: وضعیت قراردادها و اخبار صرافی Toobit را پایش می‌کند و بهترین اقدام را روی ربات‌ها انجام می‌دهد.

منابع (هیچ کلیدی لازم نیست):
  ۱) API عمومی /api/v1/exchangeInfo: مقایسه‌ی «عکس قبلی» با «عکس فعلی» ⇒ لیست‌شدن قرارداد جدید، حذف (delist)، تعلیق/توقف و بازگشت قرارداد.
  ۲) آدرس‌های اعلامیه/خبر (اختیاری) در متغیر محیطی TOOBIT_NEWS_URLS (جدا‌شده با کاما؛ JSON یا RSS یا HTML): عنوان‌ها با کلیدواژه
     (delist/suspend/maintenance/settlement/… ) طبقه‌بندی می‌شوند. آدرسِ ناقابل‌خواندن بی‌صدا نادیده گرفته می‌شود و هرگز ربات را نمی‌اندازد.

اقدام‌ها (همه با دکمه قابل خاموش/روشن در پنل R&D → «🛰 Toobit watch»):
  • auto_block: نمادی که حذف/تعلیق شد در همه‌ی ربات‌ها «بلاک» می‌شود (سیگنال جدید نمی‌گیرد) تا دوباره TRADING شود.
  • auto_pause: خبر «تعمیر/توقف کل صرافی» ⇒ توقف سیگنال‌های جدید برای N ساعت (معاملات باز دست‌نخورده).
  • notify: پیام فوری به ادمین‌ها.
خروجی در data/toobit_watch.json (SGNL آن را از ریپوی TICB می‌خواند: shared_controls.watch_block / watch_pause).
منطق خالص است؛ دسترسی شبکه تزریق‌پذیر (fetcher) است تا تست‌پذیر باشد.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR") or os.path.join(_HERE, "data")
FILE = "toobit_watch.json"
TOOBIT_BASE = (os.environ.get("TOOBIT_API_BASE") or "https://api.toobit.com").rstrip("/")
MAX_EVENTS = 60

DEFAULTS = {"on": True, "auto_block": True, "auto_pause": False, "notify": True, "pause_h": 3, "news": True}
LABEL = {"on": "Watch Toobit", "auto_block": "Auto-block delisted/suspended coins", "auto_pause": "Auto-pause on exchange maintenance news",
         "notify": "Notify admins", "news": "Read announcement URLs"}

_TRADING = {"TRADING", "ONLINE", "ENABLED", "ENABLE", "OPEN", "NORMAL", "1", "TRUE", "LISTED", ""}

# کلیدواژه‌های خبر ⇒ نوع رویداد (اولین تطبیق). نماد از متن عنوان استخراج می‌شود (BTCUSDT / 1000PEPE-SWAP-USDT / PEPE).
NEWS_RULES: List[Tuple[str, str, str]] = [
    ("delist", r"\b(delist\w*|remov(e|al) of|termination of|will (cease|stop) trading|settle(ment)? and delist)\b|下架", "high"),
    ("suspend", r"\b(suspend\w*|halt\w*|temporar\w+ (stop|clos)\w*|pause[sd]? (trading|deposits|withdraw)\w*)\b|暂停", "high"),
    ("maintenance", r"\b(system (upgrade|maintenance)|scheduled maintenance|maintenance|downtime|migration)\b|维护", "med"),
    ("settlement", r"\b(settlement|contract adjust\w*|adjust(ment)? (of )?(leverage|margin|tick)\w*|funding (rate )?(cap|adjust)\w*)\b", "med"),
    ("listing", r"\b(will (list|launch)|new (perpetual|futures|usdt-m|listing)|lists?|listing)\b|上线", "info"),
]
_SYM_RE = re.compile(r"\b((?:1000000|100000|10000|1000|1M|1B|100K|10K|1K)?[A-Z][A-Z0-9]{1,14})(?:-SWAP)?-?USDT\b")


# ----------------------------------------------------------------------------- ورودی/خروجی
def _path(data_dir: Optional[str] = None) -> str:
    return os.path.join(data_dir or DATA, FILE)


def load(data_dir: Optional[str] = None) -> Dict[str, Any]:
    try:
        with open(_path(data_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("settings", {})
            d.setdefault("blocked", {})
            d.setdefault("events", [])
            d.setdefault("snapshot", {})
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"settings": {}, "blocked": {}, "events": [], "snapshot": {}, "pause": {}, "last_ok": 0, "last_try": 0, "last_error": ""}


def save(d: Dict[str, Any], data_dir: Optional[str] = None) -> None:
    p = _path(data_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def settings_of(d: Dict[str, Any]) -> Dict[str, Any]:
    raw = d.get("settings") if isinstance(d.get("settings"), dict) else {}
    out = dict(DEFAULTS)
    for k, v in raw.items():
        if k in DEFAULTS:
            out[k] = bool(v) if isinstance(DEFAULTS[k], bool) else max(1, min(48, int(v)))
    return out


# ----------------------------------------------------------------------------- عکس وضعیت قراردادها
def _is_trading(status: Any) -> bool:
    return str(status if status is not None else "").strip().upper() in _TRADING


def parse_contracts(info: Any) -> Dict[str, str]:
    """exchangeInfo → {BASE(USDT-M): 'TRADING'|وضعیت}. BASE همان نام قرارداد بدون USDT است (مثل 1000PEPE)."""
    rows: List[Any] = []
    if isinstance(info, dict):
        rows = info.get("contracts") or info.get("symbols") or info.get("data") or []
    elif isinstance(info, list):
        rows = info
    out: Dict[str, str] = {}
    for c in rows:
        if not isinstance(c, dict):
            continue
        sym = str(c.get("symbol") or "").upper()
        if "-SWAP-" in sym:
            if not sym.endswith("-USDT"):
                continue
            base = sym.split("-")[0]
        elif sym.endswith("USDT") and "-" not in sym:
            base = sym[:-4]
        else:
            continue
        st = c.get("status")
        if st is None:
            st = c.get("state", c.get("tradeStatus", "TRADING"))
        out[base] = "TRADING" if _is_trading(st) else str(st).upper()[:20]
    return out


def diff_snapshots(prev: Dict[str, str], cur: Dict[str, str]) -> List[Dict[str, Any]]:
    """رویدادهای تغییر بین دو عکس. اگر عکس قبلی خالی باشد (اولین اجرا) هیچ رویدادی ساخته نمی‌شود (فقط مبنا ثبت می‌شود).
    اگر عکس فعلی «مشکوک خالی/ناقص» باشد (کمتر از ۵۰٪ قبلی) ⇒ رد (خطای API نباید ده‌ها delist بسازد)."""
    if not prev or not cur:
        return []
    if len(cur) < 0.5 * len(prev):
        return []
    ev: List[Dict[str, Any]] = []
    for b, st in cur.items():
        if b not in prev:
            ev.append({"kind": "listed", "sev": "info", "symbol": b, "text": f"New USDT-M contract listed: {b}USDT"})
        elif prev[b] == "TRADING" and st != "TRADING":
            ev.append({"kind": "suspended", "sev": "high", "symbol": b, "text": f"{b}USDT is no longer TRADING ({st}) — suspended"})
        elif prev[b] != "TRADING" and st == "TRADING":
            ev.append({"kind": "resumed", "sev": "info", "symbol": b, "text": f"{b}USDT trading resumed"})
    for b in prev:
        if b not in cur:
            ev.append({"kind": "delisted", "sev": "high", "symbol": b, "text": f"{b}USDT disappeared from Toobit contracts — delisted"})
    return ev


# ----------------------------------------------------------------------------- اخبار
def classify_news(title: str) -> Optional[Dict[str, Any]]:
    t = str(title or "")
    if not t.strip():
        return None
    for kind, rx, sev in NEWS_RULES:
        if re.search(rx, t, re.I):
            syms = sorted({m.group(1) for m in _SYM_RE.finditer(t.upper())})
            return {"kind": f"news_{kind}", "sev": sev, "symbols": syms, "text": t.strip()[:200]}
    return None


def _titles_from(body: str) -> List[str]:
    """عنوان‌ها را از JSON / RSS / HTML به‌صورت تحمل‌پذیر بیرون می‌کشد."""
    titles: List[str] = []
    try:
        d = json.loads(body)

        def walk(x, depth=0):
            if depth > 6:
                return
            if isinstance(x, dict):
                for k in ("title", "name", "headline", "subject"):
                    if isinstance(x.get(k), str):
                        titles.append(x[k])
                for v in x.values():
                    if isinstance(v, (dict, list)):
                        walk(v, depth + 1)
            elif isinstance(x, list):
                for v in x[:200]:
                    walk(v, depth + 1)
        walk(d)
        return titles[:200]
    except Exception:  # noqa: BLE001
        pass
    titles += re.findall(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", body, re.S | re.I)
    titles += re.findall(r"<a [^>]*>([^<]{12,200})</a>", body, re.S | re.I)
    return [re.sub(r"\s+", " ", t).strip() for t in titles][:200]


def fetch_news(urls: List[str], fetcher: Optional[Callable[[str], str]] = None) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for u in urls:
        try:
            if fetcher:
                body = fetcher(u)
            else:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 tradeiscool-bot"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = r.read(600_000).decode("utf-8", "replace")
            for t in _titles_from(body):
                c = classify_news(t)
                if c and c["text"] not in seen:
                    seen.add(c["text"])
                    out.append(c)
        except Exception:  # noqa: BLE001
            continue
    return out


def news_urls() -> List[str]:
    return [u.strip() for u in (os.environ.get("TOOBIT_NEWS_URLS") or "").split(",") if u.strip().startswith("http")]


# ----------------------------------------------------------------------------- چرخه
def _fetch_info() -> Any:
    req = urllib.request.Request(TOOBIT_BASE + "/api/v1/exchangeInfo", headers={"User-Agent": "tradeiscool-bot"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def tick(data_dir: Optional[str] = None, now: Optional[float] = None, info_fetcher: Optional[Callable[[], Any]] = None,
         news_fetcher: Optional[Callable[[str], str]] = None, urls: Optional[List[str]] = None, min_gap: float = 300.0,
         force: bool = False) -> Dict[str, Any]:
    """یک چرخه. {ran, events:[جدید], error}. هرگز استثنا نمی‌دهد. تکرار پشت‌سرهم زیر min_gap ثانیه نادیده گرفته می‌شود."""
    res: Dict[str, Any] = {"ran": False, "events": [], "error": ""}
    try:
        now = now or time.time()
        d = load(data_dir)
        cfg = settings_of(d)
        if not cfg["on"] and not force:
            return res
        if not force and now - float(d.get("last_try", 0)) < min_gap:
            return res
        _fp = lambda x: json.dumps({k: v for k, v in x.items() if k not in ("last_try", "last_ok")}, sort_keys=True, default=str)   # noqa: E731
        before, before_ok = _fp(d), float(d.get("last_ok", 0))
        d["last_try"] = now
        res["ran"] = True
        new_ev: List[Dict[str, Any]] = []
        try:
            cur = parse_contracts((info_fetcher or _fetch_info)())
            if cur:
                prev = d.get("snapshot") or {}
                new_ev += diff_snapshots(prev, cur)
                d["snapshot"] = cur
                d["last_ok"] = now
                d["last_error"] = ""
            else:
                d["last_error"] = "exchangeInfo returned no USDT contracts"
        except Exception as e:  # noqa: BLE001
            d["last_error"] = f"{type(e).__name__}: {e}"[:200]
        if cfg["news"]:
            seen = set(d.get("news_seen", []))
            for n in fetch_news(urls if urls is not None else news_urls(), news_fetcher):
                if n["text"] in seen:
                    continue
                seen.add(n["text"])
                new_ev.append(n)
            d["news_seen"] = list(seen)[-300:]
        for e in new_ev:
            e["t"] = now
        # --- اقدام خودکار
        blocked = d.setdefault("blocked", {})
        for e in new_ev:
            syms = [e["symbol"]] if e.get("symbol") else list(e.get("symbols") or [])
            if cfg["auto_block"] and e["kind"] in ("suspended", "delisted", "news_delist", "news_suspend"):
                for s in syms:
                    blocked[s] = {"since": now, "reason": e["kind"], "text": e["text"][:120]}
                    e["action"] = "blocked"
            if e["kind"] == "resumed" and e.get("symbol") in blocked and blocked[e["symbol"]].get("reason") in ("suspended",):
                del blocked[e["symbol"]]
                e["action"] = "unblocked"
            if cfg["auto_pause"] and e["kind"] == "news_maintenance":
                d["pause"] = {"until": now + cfg["pause_h"] * 3600, "reason": e["text"][:120], "since": now}
                e["action"] = "paused"
        # نمادی که دوباره TRADING شد و دلیلش تعلیق بود از بلاک بیرون می‌آید (حتی اگر رویداد از دست رفته باشد)
        snap = d.get("snapshot") or {}
        for s in [s for s, v in blocked.items() if v.get("reason") == "suspended" and snap.get(s) == "TRADING"]:
            del blocked[s]
        d["events"] = (new_ev + list(d.get("events", [])))[:MAX_EVENTS]
        # فقط وقتی چیزِ معناداری عوض شد (یا هر ~۶ ساعت برای نشان‌دادن «زنده بودن») ذخیره می‌کنیم تا گیت هر ۱۰ دقیقه کامیت بی‌ارزش نگیرد
        if force or _fp(d) != before or now - before_ok > 6 * 3600:
            save(d, data_dir)
        res["events"] = new_ev
        res["error"] = d.get("last_error", "")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"[:200]
    return res


# ----------------------------------------------------------------------------- کنترل دستی (پنل)
def set_setting(key: str, value: Any, data_dir: Optional[str] = None) -> Dict[str, Any]:
    d = load(data_dir)
    if key in DEFAULTS:
        d.setdefault("settings", {})[key] = value
        save(d, data_dir)
    return d


def unblock(symbol: str, data_dir: Optional[str] = None) -> bool:
    d = load(data_dir)
    ok = d.get("blocked", {}).pop(str(symbol).upper(), None) is not None
    if ok:
        save(d, data_dir)
    return ok


def clear_pause(data_dir: Optional[str] = None) -> None:
    d = load(data_dir)
    d["pause"] = {}
    save(d, data_dir)


def block(symbol: str, text: str = "manual", data_dir: Optional[str] = None) -> None:
    d = load(data_dir)
    d.setdefault("blocked", {})[str(symbol).upper()] = {"since": time.time(), "reason": "manual", "text": text[:120]}
    save(d, data_dir)


# ----------------------------------------------------------------------------- گیت (برای shared_controls؛ بدون I/O)
def _base_of(symbol: str) -> str:
    s = str(symbol or "").upper().replace("/USD", "").replace("USDT", "").replace("-SWAP-", "").replace("-", "").strip()
    return s


def block_reason(d: Dict[str, Any], symbol: Optional[str], now: Optional[float] = None) -> str:
    """"" = مجاز. نماد با/بدون پیشوند ×۱۰۰۰ هر دو شناسایی می‌شوند (PEPE ≡ 1000PEPE)."""
    now = now or time.time()
    try:
        p = d.get("pause") or {}
        if p and float(p.get("until", 0)) > now:
            return "toobit_pause"
        if symbol:
            b = _base_of(symbol)
            bl = d.get("blocked") or {}
            if not bl:
                return ""
            try:
                import exchange_symbols as xs
                core = xs.split_prefix(b)[0]
            except Exception:  # noqa: BLE001
                core = b
            for k in bl:
                kc = k
                try:
                    kc = xs.split_prefix(k)[0]
                except Exception:  # noqa: BLE001
                    pass
                if k == b or kc == core:
                    return f"toobit_{bl[k].get('reason', 'blocked')}"
    except Exception:  # noqa: BLE001
        return ""
    return ""


if __name__ == "__main__":      # اجرای مستقل (ورک‌فلو/کرون): یک چرخه + چاپ خلاصه
    r = tick(force=True)
    print(json.dumps({"ran": r["ran"], "events": r["events"], "error": r["error"]}, ensure_ascii=False, indent=1))

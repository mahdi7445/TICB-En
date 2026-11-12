# -*- coding: utf-8 -*-
"""
rnd_ai.py — دستیار هوش مصنوعی (Claude) برای آزمایشگاه R&D.

نقش هوش مصنوعی «ایده‌پرداز و مفسّر» است، نه تصمیم‌گیر:
  • نتایج تشخیص (معاملات واقعی) + نتایج قبلی پژوهش را می‌خواند و علت‌های احتمالی ضرر را به زبان ساده توضیح می‌دهد؛
  • استراتژی جدید را فقط در قالب DSL (rnd_dsl: JSON، بدون کد) پیشنهاد می‌دهد؛ هر خروجی با validate_spec سخت‌گیرانه پالایش می‌شود؛
  • چند «ایده‌ی تنظیم» (کلیدهای لیست‌سفید) می‌دهد که فقط با تأیید ادمین اعمال می‌شود (auto_ok=False).
حکم نهایی همیشه با آزمایشگاه آماری (strategy_lab: train/test، t-stat، کارمزد، همه‌ی نردبان‌ها) و گاردریل‌های rnd_lab است.

کلید: متغیر محیطی ANTHROPIC_API_KEY (Secret گیت‌هاب). بدون کلید یا با هر خطا ⇒ بی‌اثر؛ پژوهش خودکار (جست‌وجوی ساختاریافته) ادامه می‌دهد.
فقط urllib (بدون وابستگی تازه). سقف تماس روزانه از تنظیمات پنل.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

DSL_HELP = """
STRATEGY DSL (JSON, BUY side only — SELL is the exact mirror image automatically):
{"name": "short name", "tfs": ["15m","1h"], "all": [[lhs, op, rhs], ...(1..6 conditions)],
 "stop": {"kind":"atr","k":1.5} | {"kind":"swing","n":8,"buf":0.2} | {"kind":"candle","buf":0.1},
 "cooldown": 6, "min_risk_pct": 0.2, "max_stop_atr": 4.0, "why": "one-sentence hypothesis"}
Features (lhs/rhs are strings or numbers): close open high low | ema:N sma:N (N 2..400) | rsi:N adx:N atrp:N volr:N ret:N z:N bbpos:N emadist:N slope:N hh:N ll:N
 (hh/ll = highest high / lowest low of the previous N bars) | rng (bar range / ATR) body closepos lwick uwick (0..1 of bar range) |
 pin engulf inside (1 = bullish pattern present) | htf:1h:trend htf:4h:trend htf:1d:trend (+1 = higher-timeframe trend agrees with the trade, -1 = against)
 | htf:4h:rsi | hour (UTC 0..23).
Operators: > < >= <= == != x> (crosses up) x< (crosses down).
Meaning of mirror: for SELL every price feature is mirrored, so write conditions as if buying.
Signals fire once when ALL conditions become true (rising edge). Entry = next bar open; exit ladders are handled elsewhere; the stop is yours.
""".strip()

SYSTEM = (
    "You are the research brain of a crypto trading-signal service (Telegram channels; BTC/ETH and altcoins; timeframes 1m-4h; "
    "each signal has four targets and is managed by an R-multiple ladder). You analyse the service's CLOSED trades and propose improvements. "
    "Rules: (1) never invent data — reason only from the diagnostics given; (2) beware overfitting: simple, economically motivated rules, at most 6 conditions, round thresholds; "
    "(3) every strategy you propose is only a HYPOTHESIS that an independent statistical lab will test on unseen data — most will fail, that is expected; "
    "(4) prefer ideas that would have avoided the losing patterns in the diagnostics (e.g. fee-eaten tiny stops, counter-trend trades, low-volume signals, chop) while keeping at least ~1 signal per day per symbol; "
    "(5) answer with ONE JSON object only, no markdown fences, no prose outside JSON."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def call(model: str, system: str, user: str, max_tokens: int = 3500, timeout: float = 120.0) -> Tuple[Optional[str], str]:
    """(text, error). هرگز استثنا نمی‌دهد."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None, "ANTHROPIC_API_KEY is not set"
    try:
        import urllib.request
        body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(API_URL, data=body, method="POST",
                                     headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in d.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
        return text, ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:300]


def extract_json(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            d = json.loads(t[i:j + 1])
            return d if isinstance(d, dict) else None
        except Exception:  # noqa: BLE001
            return None
    return None


def build_user_prompt(diag: Dict[str, Any], prev: Dict[str, Any], n_strats: int, controls_hint: str = "") -> str:
    strat_rows = (diag.get("strategies") or [])[:28]
    compact = {
        "overall": diag.get("overall"), "bots": diag.get("bots"),
        "per_strategy_timeframe_net_R": [{k: r.get(k) for k in ("bot", "s", "tf", "n", "avg", "t", "pf", "win")} for r in strat_rows],
        "detected_leaks": [f.get("text") for f in (diag.get("findings") or [])[:14]],
    }
    prev_slots = []
    for slot, r in ((prev or {}).get("slots") or {}).items():
        prev_slots.append({k: r.get(k) for k in ("name", "status", "tf", "avg_r", "t", "pf", "win", "n", "fail_reasons", "family", "origin")} | {"slot": slot})
    tested = (prev or {}).get("history") or []
    payload = {"diagnostics": compact, "previous_research_slots": prev_slots, "recent_candidates_summary": tested[-12:]}
    return (
        f"{DSL_HELP}\n\nDIAGNOSTICS (net R after fees; R = initial risk):\n{json.dumps(payload, ensure_ascii=False)[:14000]}\n\n"
        f"TASK: return JSON {{\"analysis\": [3-6 short English insights: why trades lose and what to change], "
        f"\"strategies\": [up to {n_strats} DSL objects, each different in logic], "
        f"\"tuning\": [up to 3 objects {{\"section\":\"ticb\"|\"sgnl\",\"key\":\"<mx_<MODULE>_day|mx_<MODULE>_cd|mx_<MODULE>_maxstop|nat_min_stop_pct>\",\"value\":<number>,\"why\":\"...\"}}]}}.\n"
        f"{controls_hint}"
    )


def propose(diag: Dict[str, Any], prev: Dict[str, Any], n_strats: int, model: str, calls_left: int) -> Dict[str, Any]:
    """{used, error, analysis[], specs[], hints[]}. specs = لیست DSLِ معتبر (اعتبارسنجی‌شده)."""
    import rnd_dsl
    out: Dict[str, Any] = {"used": False, "error": "", "analysis": [], "specs": [], "hints": [], "model": model}
    if calls_left <= 0:
        out["error"] = "daily AI call budget used up"
        return out
    if not available():
        out["error"] = "no ANTHROPIC_API_KEY secret"
        return out
    text, err = call(model, SYSTEM, build_user_prompt(diag, prev, n_strats))
    if err:
        out["error"] = err
        return out
    d = extract_json(text)
    if not d:
        out["error"] = "AI reply was not valid JSON"
        return out
    out["used"] = True
    out["analysis"] = [str(x)[:260] for x in (d.get("analysis") or [])[:6]]
    for s in (d.get("strategies") or [])[:max(1, n_strats)]:
        ok, why, clean = rnd_dsl.validate_spec(s)
        if ok:
            clean["origin"] = "ai"
            out["specs"].append(clean)
    for t in (d.get("tuning") or [])[:3]:
        if isinstance(t, dict) and t.get("section") in ("ticb", "sgnl") and isinstance(t.get("key"), str) and "value" in t:
            out["hints"].append({"section": t["section"], "key": t["key"], "value": t["value"], "desc": str(t.get("why", ""))[:200]})
    return out

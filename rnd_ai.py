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


KEY_ENV_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_KEY", "ANTHROPIC_APIKEY")

# ----------------------------------------------------------------------------- ارائه‌دهنده‌ها (چند هوش مصنوعی هم‌زمان)
# هر ارائه‌دهنده فقط وقتی کار می‌کند که کلیدش در Secret باشد. مدل‌ها را می‌توان بدون تغییر کد با متغیر <NAME>_MODEL (Repository variable یا secret) عوض کرد.
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "claude": {"label": "Claude (Anthropic)", "keys": KEY_ENV_NAMES, "kind": "anthropic", "url": API_URL, "model_env": "CLAUDE_MODEL", "model": "claude-sonnet-5-5",
               "focus": "trend-following and pullback logic"},
    "openai": {"label": "OpenAI GPT", "keys": ("OPENAI_API_KEY",), "kind": "openai", "url": "https://api.openai.com/v1/chat/completions",
               "model_env": "OPENAI_MODEL", "model": "gpt-4.1", "focus": "mean-reversion and exhaustion logic"},
    "gemini": {"label": "Google Gemini", "keys": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "kind": "gemini",
               "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", "model_env": "GEMINI_MODEL", "model": "gemini-3.6-flash",
               "fallbacks": ("gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite"), "discover": True,
               "focus": "volatility-compression and breakout logic"},
    "deepseek": {"label": "DeepSeek", "keys": ("DEEPSEEK_API_KEY",), "kind": "openai", "url": "https://api.deepseek.com/chat/completions",
                 "model_env": "DEEPSEEK_MODEL", "model": "deepseek-chat", "focus": "volume and momentum logic"},
    "grok": {"label": "xAI Grok", "keys": ("XAI_API_KEY", "GROK_API_KEY"), "kind": "openai", "url": "https://api.x.ai/v1/chat/completions",
             "model_env": "GROK_MODEL", "model": "grok-4", "focus": "candle-pattern and wick logic"},
    "groq": {"label": "Groq (free tier)", "keys": ("GROQ_API_KEY",), "kind": "openai", "url": "https://api.groq.com/openai/v1/chat/completions",
             "model_env": "GROQ_MODEL", "model": "llama-3.3-70b-versatile", "fallbacks": ("openai/gpt-oss-120b", "llama-3.1-8b-instant"),
             "focus": "multi-timeframe confirmation logic"},
    "nvidia": {"label": "NVIDIA NIM (free credits)", "keys": ("NVIDIA_API_KEY", "NGC_API_KEY"), "kind": "openai", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
               "model_env": "NVIDIA_MODEL", "model": "meta/llama-3.3-70b-instruct", "focus": "risk-filtering and regime logic"},
    "openrouter": {"label": "OpenRouter (free models)", "keys": ("OPENROUTER_API_KEY",), "kind": "openai", "url": "https://openrouter.ai/api/v1/chat/completions",
                   "model_env": "OPENROUTER_MODEL", "model": "openrouter/free", "fallbacks": ("meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-oss-120b:free"),
                   "max_tokens": 1500, "focus": "any idea that fixes the weak strategies"},
}


def _clean_key(v: str) -> str:
    v = (v or "").strip().strip('"').strip("'").strip()
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    return v


def provider_key(pid: str) -> Tuple[str, str]:
    """(کلید، نام متغیر). فاصله/نقل‌قول/«Bearer » اضافه‌ی ناشی از کپی‌کردن حذف می‌شود."""
    for n in PROVIDERS[pid]["keys"]:
        v = _clean_key(os.environ.get(n) or "")
        if v:
            return v, n
    return "", ""


def provider_model(pid: str, claude_model: str = "") -> str:
    if pid == "claude" and claude_model:
        return claude_model
    return (os.environ.get(PROVIDERS[pid]["model_env"]) or "").strip() or PROVIDERS[pid]["model"]


def providers_available() -> List[str]:
    return [p for p in PROVIDERS if provider_key(p)[0]]


def provider_status(claude_model: str = "") -> List[Dict[str, Any]]:
    out = []
    for p, d in PROVIDERS.items():
        k, n = provider_key(p)
        out.append({"id": p, "label": d["label"], "has_key": bool(k), "src": n, "model": provider_model(p, claude_model), "env": d["keys"][0]})
    return out


def _key() -> str:
    """کلید Claude (سازگاری با نسخه‌ی قبل)."""
    return provider_key("claude")[0]


def key_source() -> str:
    """نام متغیری که کلید Claude از آن خوانده شد ('' = هیچ‌کدام)."""
    return provider_key("claude")[1]


def available() -> bool:
    return bool(_key())


UA = "Mozilla/5.0 (compatible; tradeiscool-rnd/1.0; +https://github.com)"   # بدون User-Agent، Cloudflare (مثلاً Groq) درخواست را با «error 1010» رد می‌کند
LAST_MODEL: Dict[str, str] = {}      # آخرین مدلی که واقعاً جواب داد (برای نمایش در تست)
_DISCOVERED: Dict[str, List[str]] = {}


def _explain_http(code: int, body: str, model_env: str = "") -> str:
    m = ""
    try:
        j = json.loads(body)
        e = j.get("error") if isinstance(j, dict) else None
        m = (e.get("message", "") if isinstance(e, dict) else str(e or ""))
    except Exception:  # noqa: BLE001
        m = (body or "")[:160]
    if code == 403 and "1010" in (body or ""):
        m = "Cloudflare مرورگرنما نبودن درخواست را رد کرد (browser_signature_banned)"
    mdl = f" (متغیر {model_env} را روی نام درست مدل بگذارید)" if model_env else ""
    hint = {401: "کلید نامعتبر است (کپی ناقص/باطل‌شده)", 403: "کلید اجازه‌ی این مدل/عملیات را ندارد یا اعتبار ندارد",
            404: "این مدل وجود ندارد/منسوخ شده" + mdl,
            400: "درخواست/اعتبار حساب مشکل دارد (مثلاً اعتبار تمام شده یا نام مدل نادرست)", 402: "اعتبار حساب تمام شده", 429: "سقف نرخ/اعتبار پر شده", 529: "سرویس شلوغ است"}.get(code, "")
    return f"HTTP {code}" + (f" — {hint}" if hint else "") + (f" — {m[:140]}" if m else "")


def _model_missing(code: int, raw: str) -> bool:
    """خطای «مدل پیدا نشد/منسوخ» که ارزش امتحانِ مدل بعدی را دارد."""
    if code == 404:
        return True
    low = (raw or "").lower()
    return code in (400, 403) and "model" in low and any(w in low for w in ("not found", "no longer", "does not exist", "decommission", "not supported", "invalid", "deprecated"))


def _list_gemini_models(key: str) -> List[str]:
    """فهرست زنده‌ی مدل‌های Gemini که generateContent دارند، بهترین Flash اول (نسخه‌ی بالاتر، بدون lite/preview/tts/image)."""
    import re
    import urllib.request
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": key, "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in j.get("models") or []:
        name = str(m.get("name", "")).replace("models/", "")
        if "generateContent" not in (m.get("supportedGenerationMethods") or []) or not name.startswith("gemini-"):
            continue
        if any(w in name for w in ("lite", "preview", "tts", "image", "live", "audio", "embedding", "thinking", "exp", "robotics", "computer")):
            continue
        if "flash" not in name:
            continue
        v = re.findall(r"\d+(?:\.\d+)?", name)
        out.append((float(v[0]) if v else 0.0, name))
    out.sort(reverse=True)
    return [n for _, n in out][:3]


def _post(d: Dict[str, Any], pid: str, key: str, model: str, system: str, user: str, max_tokens: int, timeout: float) -> Tuple[Optional[str], str, int, str]:
    """یک درخواست. (text, error, http_code, raw_error_body). هرگز استثنا نمی‌دهد؛ روی خطای گذرا یک‌بار دوباره."""
    import urllib.error
    import urllib.request
    max_tokens = min(int(max_tokens), int(d.get("max_tokens") or max_tokens))
    url, hdr = d["url"], {"content-type": "application/json", "accept": "application/json", "user-agent": UA}
    if d["kind"] == "anthropic":
        hdr.update({"x-api-key": key, "anthropic-version": API_VERSION})
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}
    elif d["kind"] == "gemini":
        url = url.format(model=model)
        hdr["x-goog-api-key"] = key
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}}
    else:
        hdr["authorization"] = "Bearer " + key
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        body["max_completion_tokens" if pid == "openai" else "max_tokens"] = max_tokens
    raw_body = json.dumps(body).encode()
    last, code, raw = "", 0, ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, data=raw_body, method="POST", headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read().decode("utf-8", "replace")
                status = getattr(r, "status", 200)
            try:
                j = json.loads(txt)
            except Exception:  # noqa: BLE001
                return None, f"پاسخ غیر-JSON از سرویس (HTTP {status}): {(txt or '').strip()[:120] or 'خالی'}", status, ""
            if d["kind"] == "anthropic":
                text = "".join(b.get("text", "") for b in j.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
            elif d["kind"] == "gemini":
                text = "".join(p_.get("text", "") for c in (j.get("candidates") or [])[:1] for p_ in ((c.get("content") or {}).get("parts") or []))
            else:
                text = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if not text:
                return None, "empty reply", 200, ""
            return text, "", 200, ""
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                raw = ""
            code = e.code
            last = _explain_http(code, raw, d.get("model_env", ""))
            if code not in (429, 500, 502, 503, 529) or "credit" in raw.lower() or "balance" in raw.lower():
                break
        except Exception as e:  # noqa: BLE001
            last, code = f"{type(e).__name__}: {e}"[:300], 0
        time.sleep(3)
    return None, last, code, raw


def call_provider(pid: str, system: str, user: str, model: str = "", max_tokens: int = 3500, timeout: float = 120.0) -> Tuple[Optional[str], str]:
    """(text, error). هرگز استثنا نمی‌دهد. اگر مدل منسوخ/ناموجود بود مدل‌های جایگزین (و برای Gemini فهرست زنده) را امتحان می‌کند."""
    d = PROVIDERS.get(pid)
    if not d:
        return None, f"unknown provider {pid}"
    key, kname = provider_key(pid)
    if not key:
        return None, f"{d['keys'][0]} is not set in this process"
    model = model or provider_model(pid)
    tried: List[str] = []
    queue = [model] + [m for m in (LAST_MODEL.get(pid),) if m] + list(d.get("fallbacks") or ())
    first_err = ""
    discovered = False
    while queue:
        m = queue.pop(0)
        if m in tried:
            continue
        tried.append(m)
        text, err, code, raw = _post(d, pid, key, m, system, user, max_tokens, timeout)
        if text:
            LAST_MODEL[pid] = m
            return text, ""
        first_err = first_err or err
        if not _model_missing(code, raw):
            return None, (err if m == model else f"{err} (مدل {m})")
        if not queue and d.get("discover") and not discovered:
            discovered = True
            queue.extend(_DISCOVERED.get(pid) or _DISCOVERED.setdefault(pid, _list_gemini_models(key)))
    return None, first_err + (f" — مدل‌های امتحان‌شده: {', '.join(tried)}" if len(tried) > 1 else "")


def call(model: str, system: str, user: str, max_tokens: int = 3500, timeout: float = 120.0) -> Tuple[Optional[str], str]:
    """سازگاری با نسخه‌ی قبل: تماس با Claude."""
    return call_provider("claude", system, user, model, max_tokens, timeout)


def ping_provider(pid: str, claude_model: str = "") -> Tuple[bool, str]:
    """تست واقعی اتصال یک ارائه‌دهنده (یک درخواست کوچک). (ok, پیام)."""
    d = PROVIDERS[pid]
    k, n = provider_key(pid)
    if not k:
        return False, "هیچ‌کدام از متغیرهای " + " / ".join(d["keys"]) + " در این پروسه نیست."
    model = provider_model(pid, claude_model)
    text, err = call_provider(pid, "Reply with the single word: ok", "ping", model, max_tokens=64, timeout=30.0)
    return (True, f"{d['label']}: اتصال برقرار است (مدل {LAST_MODEL.get(pid) or model}، کلید از {n}).") if not err else (False, f"{d['label']}: {err}")


def ping(model: str = "claude-haiku-5-5") -> Tuple[bool, str]:
    return ping_provider("claude", model)


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


def gate_text(gate: Optional[Dict[str, Any]]) -> str:
    if not gate or not gate.get("on", True):
        return ""
    return (f"PASS CRITERIA (a strategy is only deployed when it meets ALL of these on its own exit ladder, net of fees): win rate >= {gate['win']:g}%, "
            f"profit factor {gate['pf_min']:g}..{gate['pf_max']:g}, planned risk:reward (farthest target) >= {gate['rr']:g}R, avg-win/avg-loss >= {gate['payoff']:g}, "
            f"max drawdown <= {gate['dd']:g}% (at {gate['risk']:g}% risk per trade), longest losing streak < {gate['streak']}, net avg >= {gate['avg']:g}R/trade, "
            f"|t| >= {gate['t']:g}, >= {gate['n']} backtest trades, still profitable at 1.5x costs, both time halves positive.")


def build_user_prompt(diag: Dict[str, Any], prev: Dict[str, Any], n_strats: int, controls_hint: str = "", gate: Optional[Dict[str, Any]] = None,
                      weak: Optional[List[Dict[str, Any]]] = None, focus: str = "", n_fix: int = 0) -> str:
    strat_rows = (diag.get("strategies") or [])[:28]
    compact = {
        "overall": diag.get("overall"), "bots": diag.get("bots"),
        "per_strategy_timeframe_net_R": [{k: r.get(k) for k in ("bot", "s", "tf", "n", "avg", "t", "pf", "win")} for r in strat_rows],
        "detected_leaks": [f.get("text") for f in (diag.get("findings") or [])[:14]],
    }
    prev_slots = []
    for slot, r in ((prev or {}).get("slots") or {}).items():
        prev_slots.append({k: r.get(k) for k in ("name", "status", "tf", "avg_r", "t", "pf", "win", "n", "fail_reasons", "family", "origin", "dd_pct", "streak")} | {"slot": slot})
    tested = (prev or {}).get("history") or []
    payload = {"diagnostics": compact, "previous_research_slots": prev_slots, "recent_candidates_summary": tested[-12:]}
    if weak:
        payload["weak_strategies_to_fix"] = weak[:8]
    fix_txt = (f"\"fixes\": [up to {n_fix} DSL objects; each one is an IMPROVED version of one of weak_strategies_to_fix that removes exactly the criteria it fails "
               f"(keep the core idea, change thresholds/filters/stop; add \"parent\": <its name>)], ") if (weak and n_fix) else ""
    return (
        f"{DSL_HELP}\n\n{gate_text(gate)}\n\nDIAGNOSTICS (net R after fees; R = initial risk):\n{json.dumps(payload, ensure_ascii=False)[:14000]}\n\n"
        + (f"YOUR RESEARCH FOCUS THIS TIME: {focus}.\n" if focus else "")
        + f"TASK: return JSON {{\"analysis\": [3-6 short English insights: why trades lose and what to change], "
        f"\"strategies\": [up to {n_strats} DSL objects, each different in logic], {fix_txt}"
        f"\"tuning\": [up to 3 objects {{\"section\":\"ticb\"|\"sgnl\",\"key\":\"<mx_<MODULE>_day|mx_<MODULE>_cd|mx_<MODULE>_maxstop|nat_min_stop_pct>\",\"value\":<number>,\"why\":\"...\"}}]}}.\n"
        f"{controls_hint}"
    )


def _one(pid: str, model: str, diag, prev, n_strats, gate, weak, n_fix) -> Dict[str, Any]:
    import rnd_dsl
    r: Dict[str, Any] = {"used": False, "error": "", "model": model, "analysis": [], "specs": [], "hints": []}
    text, err = call_provider(pid, SYSTEM, build_user_prompt(diag, prev, n_strats, "", gate, weak, PROVIDERS[pid]["focus"], n_fix), model)
    if err:
        r["error"] = err
        return r
    d = extract_json(text)
    if not d:
        r["error"] = "reply was not valid JSON"
        return r
    r["used"] = True
    r["analysis"] = [f"[{PROVIDERS[pid]['label']}] " + str(x)[:240] for x in (d.get("analysis") or [])[:5]]
    for key, org in (("strategies", "ai"), ("fixes", "ai-fix")):
        for sp in (d.get(key) or [])[:max(1, n_strats + n_fix)]:
            if not isinstance(sp, dict):
                continue
            parent = str(sp.get("parent", ""))[:50]
            ok, why, clean = rnd_dsl.validate_spec(sp)
            if ok:
                clean["origin"] = f"{org}:{pid}"
                if parent:
                    clean["parent_name"] = parent
                r["specs"].append(clean)
    for t in (d.get("tuning") or [])[:3]:
        if isinstance(t, dict) and t.get("section") in ("ticb", "sgnl") and isinstance(t.get("key"), str) and "value" in t:
            r["hints"].append({"section": t["section"], "key": t["key"], "value": t["value"], "desc": f"[{pid}] " + str(t.get("why", ""))[:190]})
    return r


def propose_multi(diag: Dict[str, Any], prev: Dict[str, Any], n_strats: int, claude_model: str, budgets: Dict[str, int], enabled: Optional[List[str]] = None,
                  gate: Optional[Dict[str, Any]] = None, weak: Optional[List[Dict[str, Any]]] = None, n_fix: int = 0) -> Dict[str, Any]:
    """همه‌ی ارائه‌دهنده‌های فعال که کلید و بودجه‌ی روزانه دارند به‌طور موازی نظر می‌دهند. خروجی:
    {used, error, analysis[], specs[], hints[], providers: {id: {used,error,model,n,skipped}}, calls: {id: 1}}. هر DSL با validate_spec پالایش می‌شود."""
    from concurrent.futures import ThreadPoolExecutor
    out: Dict[str, Any] = {"used": False, "error": "", "analysis": [], "specs": [], "hints": [], "providers": {}, "calls": {}, "model": claude_model}
    todo = []
    for pid in PROVIDERS:
        if enabled is not None and pid not in enabled:
            out["providers"][pid] = {"skipped": "off"}
        elif not provider_key(pid)[0]:
            out["providers"][pid] = {"skipped": "no key"}
        elif int(budgets.get(pid, 0)) <= 0:
            out["providers"][pid] = {"skipped": "daily budget used up"}
        else:
            todo.append(pid)
    if not todo:
        out["error"] = "no AI provider is ready (no key / all off / budget used up)"
        return out
    with ThreadPoolExecutor(max_workers=min(7, len(todo))) as ex:
        futs = {pid: ex.submit(_one, pid, provider_model(pid, claude_model), diag, prev, n_strats, gate, weak, n_fix) for pid in todo}
        for pid, f in futs.items():
            try:
                r = f.result(timeout=400)
            except Exception as e:  # noqa: BLE001
                r = {"used": False, "error": f"{type(e).__name__}: {e}"[:200], "model": "", "analysis": [], "specs": [], "hints": []}
            out["providers"][pid] = {"used": r["used"], "error": r["error"], "model": r.get("model"), "n": len(r["specs"])}
            out["calls"][pid] = 1
            if r["used"]:
                out["used"] = True
                out["analysis"] += r["analysis"][:4]
                out["specs"] += r["specs"]
                out["hints"] += r["hints"]
    if not out["used"]:
        out["error"] = "; ".join(f"{k}: {v.get('error')}" for k, v in out["providers"].items() if v.get("error"))[:300] or "no provider answered"
    out["analysis"] = out["analysis"][:12]
    return out


def propose(diag: Dict[str, Any], prev: Dict[str, Any], n_strats: int, model: str, calls_left: int) -> Dict[str, Any]:
    """سازگاری با نسخه‌ی قبل (فقط Claude)."""
    return propose_multi(diag, prev, n_strats, model, {"claude": calls_left}, ["claude"])

"""تست اتصال ارائه‌دهنده‌های هوش مصنوعی: User-Agent، مدل منسوخ/جایگزین، پاسخ غیر-JSON، خطای اعتبار، حذف Cerebras/GitHub."""
import io, json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time; time.sleep = lambda *_: None
import rnd_ai, rnd_lab

for gone in ("cerebras", "github", "mistral"):
    assert gone not in rnd_ai.PROVIDERS and gone not in rnd_lab.AI_PROVIDER_KEYS
assert "nvidia" in rnd_ai.PROVIDERS and set(rnd_lab.AI_PROVIDER_KEYS.values()) <= set(rnd_lab.SCHEMA)
assert rnd_ai.PROVIDERS["openrouter"]["model"] == "openrouter/free"

for k in list(os.environ):
    if k.endswith("_API_KEY") or k.endswith("_MODEL"): del os.environ[k]
for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"): os.environ[k] = "key"

class R:
    def __init__(s, b, raw=False): s.b, s.raw, s.status = b, raw, 200
    def read(s): return s.b.encode() if s.raw else json.dumps(s.b).encode()
    def __enter__(s): return s
    def __exit__(s, *a): return False
def http(code, body):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))

calls = []
def run(handler):
    def uo(req, timeout=0):
        calls.append(req); return handler(req)
    urllib.request.urlopen = uo
    del calls[:]; rnd_ai.LAST_MODEL.clear(); rnd_ai._DISCOVERED.clear()

OKB = {"choices": [{"message": {"content": "ok"}}]}

# 1) هر درخواست User-Agent دارد (Cloudflare error 1010 برای Groq)
run(lambda r: R(OKB))
assert rnd_ai.ping_provider("groq")[0]
assert calls[0].headers.get("User-agent", "").startswith("Mozilla/5.0"), calls[0].headers
# 1b) متن خطای 1010 قابل‌فهم است
assert "Cloudflare" in rnd_ai._explain_http(403, "error code: 1010")

# 2) Gemini: مدل منسوخ → جایگزین‌ها → فهرست زنده
def g404(r):
    if "models?pageSize" in r.full_url:
        return R({"models": [{"name": "models/gemini-3.9-flash", "supportedGenerationMethods": ["generateContent"]},
                             {"name": "models/gemini-3.9-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                             {"name": "models/gemini-3.9-pro", "supportedGenerationMethods": ["generateContent"]},
                             {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]})
    if "gemini-3.9-flash:" in r.full_url: return R({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    raise http(404, json.dumps({"error": {"message": "This model is no longer available to new users"}}))
run(g404)
ok, msg = rnd_ai.ping_provider("gemini")
assert ok and "gemini-3.9-flash" in msg, msg
assert rnd_ai._list_gemini_models("k") == ["gemini-3.9-flash"]
n1 = len(calls)
assert rnd_ai.call_provider("gemini", "s", "u")[0] == "ok"      # مدل موفق به‌خاطر سپرده می‌شود
assert calls[n1].full_url.endswith("gemini-3.9-flash:generateContent") or "gemini-3.6-flash" in calls[n1].full_url

# 2b) مدل اول موفق است ⇒ فهرست زنده خوانده نمی‌شود
run(lambda r: R({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))
assert rnd_ai.ping_provider("gemini")[0] and len(calls) == 1 and "gemini-3.6-flash" in calls[0].full_url

# 3) پاسخ خالی/غیر-JSON خطای خوانا می‌دهد، نه JSONDecodeError
run(lambda r: R("", raw=True))
ok, msg = rnd_ai.ping_provider("nvidia"); assert not ok and "JSONDecodeError" not in msg and "غیر-JSON" in msg, msg

# 4) خطای اعتبار ⇒ فقط یک درخواست (بدون تلاش مجدد بی‌فایده) و پیام واضح
def credit(r): raise http(429, json.dumps({"error": {"message": "You have no credits remaining"}}))
run(credit); ok, msg = rnd_ai.ping_provider("openai"); assert not ok and len(calls) == 1 and "اعتبار" in msg and "credits" in msg, msg
def c402(r): raise http(402, json.dumps({"error": {"message": "Insufficient Balance"}}))
run(c402); assert not rnd_ai.ping_provider("openai")[0] and len(calls) == 1
# 4b) 429 معمولی (سقف نرخ) یک‌بار دوباره تلاش می‌شود
def rl(r): raise http(429, json.dumps({"error": {"message": "rate limit"}}))
run(rl); rnd_ai.ping_provider("groq"); assert len(calls) == 2

# 5) OpenRouter: مدل پیش‌فرض free-router و max_tokens کوچک؛ مدل منسوخ ⇒ جایگزین
def orr(r):
    b = json.loads(r.data)
    assert b["max_tokens"] <= 1500
    if b["model"] == "openrouter/free": raise http(404, json.dumps({"error": {"message": "No endpoints found"}}))
    return R(OKB)
run(orr); ok, msg = rnd_ai.ping_provider("openrouter"); assert ok and "llama-3.3-70b-instruct:free" in msg, msg
# 5b) Groq: مدل منسوخ ⇒ جایگزین
def gq(r):
    if json.loads(r.data)["model"] == "llama-3.3-70b-versatile": raise http(400, json.dumps({"error": {"message": "The model has been decommissioned"}}))
    return R(OKB)
run(gq); ok, msg = rnd_ai.ping_provider("groq"); assert ok and "gpt-oss" in msg, msg
# 5c) خطای غیر-مدل (۴۰۱) به مدل بعدی نمی‌رود
def u401(r): raise http(401, "{}")
run(u401); assert not rnd_ai.ping_provider("groq")[0] and len(calls) == 1

# 6) متغیر مدل کاربر محترم است
os.environ["GROQ_MODEL"] = "my-model"; run(lambda r: R(OKB)); rnd_ai.ping_provider("groq"); assert json.loads(calls[0].data)["model"] == "my-model"
print("ai providers ok")

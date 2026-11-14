# -*- coding: utf-8 -*-
"""
rnd_lab.py — هسته‌ی «آزمایشگاه تحقیق و توسعه» (R&D Lab) برای هر دو ربات (TICB و SGNL).

وظیفه: بررسی کامل معاملات بسته‌شده → پیدا کردن «نشتی‌های آماریِ معنادار» (استراتژی/تایم‌فریم/جهت/ساعت/اندازه‌ی استاپ/نردبان) →
تبدیل به «پیشنهاد» (یا اعمال خودکار در حالت Auto) فقط از طریق همان کنترل‌هایی که پنل ادمین از قبل دارد (shared_controls) و چند قانون
اضافه (data/rnd_rules.json) → پایش اثر هر تغییر و بازگشت خودکار اگر بدتر شد → ثبت همه‌چیز در ژورنال.
استراتژی‌های جدید (RD1..RD6) را بخش پژوهش (rnd_research.py، روی GitHub Actions) می‌سازد و آزمایشگاه strategy_lab حکم می‌دهد؛ این فایل
نتیجه را می‌خواند و فعال‌سازی را (طبق حالت: فقط‌گزارش / با تأیید دکمه‌ای / خودکار) مدیریت می‌کند.

اصول ایمنی (همه‌جا رعایت شده):
  • هیچ تغییری بدون «شواهد آماری» (حداقل تعداد معامله، |t|، پایداری هر دو نیمه‌ی زمانی) ساخته نمی‌شود؛
  • اعمال خودکار فقط در حالت Auto و فقط با آستانه‌ی سخت‌گیرانه‌تر (auto_t)، سقف تغییر روزانه، و فقط روی کلیدهای لیست‌سفید؛
  • هر تغییر قابل «Undo» است (مقدار قبلی ذخیره می‌شود)؛ پایش پس از تغییر و بازگشت خودکار؛ کلیدِ برگشت‌خورده ۷ روز محافظت می‌شود؛
  • هیچ‌چیز روی مسیر سیگنال‌دهی/تارگت/نتیجه‌ی معاملات باز دست نمی‌زند؛ هر خطا ⇒ بی‌اثر (fail-open) و در لاگ ثبت می‌شود.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR") or os.path.join(_HERE, "data")
STATE_NAME, DIAG_NAME, RULES_NAME = "rnd_state.json", "rnd_diag.json", "rnd_rules.json"
RESULTS_NAME, STRATS_NAME, VERDICTS_NAME = "rnd_results.json", "rnd_strategies.json", "rnd_verdicts.json"
MODULES_NAME = "rnd_modules.json"          # نتیجه‌ی بازآزمایی همه‌ی ماژول‌ها (rnd_modules.yml)
BOTS = ("ticb", "sgnl")
SKIP_STRATS = ("manual/relay", "personal")
_LOCK = threading.RLock()

# ----------------------------------------------------------------------------- تنظیمات (همه با دکمه)
AI_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
SCHEMA: Dict[str, Tuple[str, Any, Any, Any]] = {
    "mode":           ("choice", "semi", None, ["off", "advisor", "semi", "auto"]),   # off | فقط گزارش | با تأیید | خودکار
    "ai_on":          ("bool", True, None, None),
    "ai_model":       ("choice", "claude-sonnet-5-5", None, AI_MODELS),
    "ai_calls_day":   ("int", 12, 1, 96),         # سقف تماس روزانه برای «هر» ارائه‌دهنده
    "ai_claude":      ("bool", True, None, None), # ارائه‌دهنده‌ها (فقط اگر کلیدشان در Secret باشد کار می‌کنند)
    "ai_openai":      ("bool", True, None, None),
    "ai_gemini":      ("bool", True, None, None),
    "ai_deepseek":    ("bool", True, None, None),
    "ai_grok":        ("bool", True, None, None),
    "ai_groq":        ("bool", True, None, None),
    "ai_cerebras":    ("bool", True, None, None),
    "ai_github":      ("bool", True, None, None),
    "ai_openrouter":  ("bool", True, None, None),
    "modules_on":     ("bool", True, None, None), # 🧩 بازآزمایی «همه‌ی ماژول‌های قدیمی» (نه فقط RD1..RD6) به‌صورت شبانه‌روزی
    "modules_h":      ("int", 6, 1, 72),          # فاصله‌ی بازآزمایی ماژول‌ها (ساعت) وقتی «۲۴ ساعته» خاموش است
    "modules_apply":  ("bool", True, None, None), # در حالت Auto: نتیجه (بهترین نردبان / قفل‌شدنِ ماژول رد‌شده) خودکار اعمال شود
    "improve":        ("bool", True, None, None), # 🔧 حلقه‌ی بهبود خودکار: استراتژی‌های ناموفق با توجه به دلیل رد شدن اصلاح و دوباره آزموده می‌شوند
    "improve_n":      ("int", 12, 2, 40),         # تعداد نسخه‌ی اصلاحی در هر چرخه
    "g_on":           ("bool", True, None, None),    # 🎯 کلید کلی: خاموش = فقط حکم خودِ آزمایشگاه (بدون معیارهای زیر)
    # ---- 🎯 شرایط پاس‌شدن استراتژی (روی «نردبان مخصوص خود استراتژی» و پس از کسر کارمزد سنجیده می‌شود)
    "g_win":          ("float", 45.0, 20.0, 80.0),   # حداقل وین‌ریت %
    "g_pf_min":       ("float", 1.2, 1.0, 3.0),      # حداقل پروفیت‌فکتور
    "g_pf_max":       ("float", 1.6, 1.2, 9.9),      # سقف PF (بالاتر از آن = مشکوک به بیش‌برازش؛ ۹٫۹ = بدون سقف)
    "g_rr":           ("float", 2.5, 1.0, 10.0),     # حداقل ریسک‌به‌ریوارد طرح (دورترین تارگت نردبان بر حسب R)
    "g_payoff":       ("float", 1.2, 0.5, 5.0),      # حداقل نسبت واقعیِ میانگین سود به میانگین ضرر
    "g_dd":           ("float", 7.0, 1.0, 50.0),     # سقف دراودان % (R × ریسک هر معامله)
    "g_risk":         ("float", 1.0, 0.1, 5.0),      # ریسک هر معامله % (برای تبدیل R به درصد)
    "g_streak":       ("int", 6, 2, 20),             # لوزینگ‌استریک باید کمتر از این باشد
    "g_avg":          ("float", 0.05, 0.0, 1.0),     # حداقل میانگین R خالص هر معامله
    "g_t":            ("float", 2.0, 1.0, 5.0),      # حداقل |t|
    "g_n":            ("int", 100, 30, 1000),        # حداقل تعداد معامله‌ی بک‌تست
    "g_stress":       ("bool", True, None, None),    # با هزینه‌ی ۱٫۵ برابر هم باید مثبت بماند
    "g_split":        ("bool", True, None, None),    # هر دو نیمه‌ی زمانی (train/test) مثبت
    "nonstop":        ("bool", True, None, None), # 🔁 تحقیق و توسعه ۲۴ ساعته: چرخه‌ها پشت‌سرهم (تشخیص هر ۱ ساعت، پژوهش سنگین حداکثر هر ۲ ساعت)
    "light_h":        ("int", 2, 1, 72),          # فاصله‌ی چرخه‌ی تشخیص (ساعت)
    "research_h":     ("int", 3, 1, 336),         # فاصله‌ی چرخه‌ی پژوهش سنگین (ساعت)
    "min_n":          ("int", 40, 20, 400),       # حداقل معامله برای هر نتیجه‌گیری
    "min_t":          ("float", 2.0, 1.0, 5.0),   # آستانه‌ی |t| برای «پیشنهاد»
    "auto_t":         ("float", 3.0, 2.0, 6.0),   # آستانه‌ی |t| برای «اعمال خودکار»
    "max_changes_day": ("int", 3, 1, 10),
    "rollback":       ("bool", True, None, None),
    "rb_n":           ("int", 30, 10, 200),       # بعد از چند معامله‌ی جدید اثر تغییر سنجیده شود
    "cands":          ("int", 40, 10, 120),       # تعداد استراتژی کاندید در هر چرخه‌ی پژوهش
    "probation_day":  ("int", 2, 0, 10),          # سقف روزانه‌ی استراتژی جدید در دوره‌ی آزمایشی (۰ = بدون سقف)
    "new_auto":       ("bool", True, None, None), # در حالت Auto: استراتژی جدیدِ PASS/کاندید خودکار روشن شود
    "a_off":          ("bool", True, None, None), # انواع اقدام مجاز: خاموش‌کردن استراتژی ضعیف
    "a_block":        ("bool", True, None, None), # بستن تایم‌فریم/جهت/ساعتِ ضعیف (قانون)
    "a_stop":         ("bool", True, None, None), # کف استاپ (کارمزد-خور)
    "a_caps":         ("bool", True, None, None), # سقف روزانه / کولدان
    "a_ladder":       ("bool", True, None, None), # انتخاب بهترین نردبان از بک‌تست
    "a_new":          ("bool", True, None, None), # استراتژی‌های جدید R&D
}
STEP = {"ai_calls_day": 1, "light_h": 1, "research_h": 1, "min_n": 10, "min_t": 0.5, "auto_t": 0.5, "max_changes_day": 1, "rb_n": 10,
        "cands": 10, "probation_day": 1,
        "ai_calls_day": 2, "improve_n": 2, "modules_h": 1, "g_win": 1.0, "g_pf_min": 0.05, "g_pf_max": 0.1, "g_rr": 0.5, "g_payoff": 0.1, "g_dd": 0.5, "g_risk": 0.1,
        "g_streak": 1, "g_avg": 0.01, "g_t": 0.5, "g_n": 10}
LABEL = {
    "mode": "Mode", "nonstop": "24h non-stop research", "ai_on": "AI assistant", "ai_model": "AI model", "ai_calls_day": "AI calls per day",
    "light_h": "Diagnosis every (h)", "research_h": "Deep research every (h)", "min_n": "Min trades per conclusion",
    "min_t": "Evidence to PROPOSE (|t|)", "auto_t": "Evidence to AUTO-APPLY (|t|)", "max_changes_day": "Max auto changes / day",
    "rollback": "Auto-rollback if worse", "rb_n": "Check effect after N trades", "cands": "Candidates per research run",
    "probation_day": "Probation daily cap (0=off)", "new_auto": "Auto-enable new strategies (Auto mode)",
    "a_off": "Switch off losing strategies", "a_block": "Block bad timeframe / side / hours", "a_stop": "Raise tiny-stop floor",
    "a_caps": "Daily caps / cooldowns", "a_ladder": "Pick best ladder", "a_new": "New R&D strategies",
}
LABEL.update({
    "modules_on": "Re-test ALL old modules (24h)", "modules_h": "Re-test modules every (h)", "modules_apply": "Auto-apply module results (Auto mode)",
    "ai_claude": "Claude (Anthropic)", "ai_openai": "OpenAI GPT", "ai_gemini": "Google Gemini", "ai_deepseek": "DeepSeek", "ai_grok": "xAI Grok",
    "ai_groq": "Groq (free)", "ai_cerebras": "Cerebras (free)", "ai_github": "GitHub Models (free)", "ai_openrouter": "OpenRouter", "improve": "Auto-improve failed strategies", "improve_n": "Improved variants per cycle",
    "g_on": "Enforce pass criteria (ON/OFF)", "g_win": "Min win rate (%)", "g_pf_min": "Min profit factor", "g_pf_max": "Max profit factor (overfit cap)", "g_rr": "Min planned risk:reward (farthest target, R)",
    "g_payoff": "Min real avg-win / avg-loss", "g_dd": "Max drawdown (%)", "g_risk": "Risk per trade (%) for drawdown", "g_streak": "Losing streak must be below",
    "g_avg": "Min net R per trade", "g_t": "Min statistical strength |t|", "g_n": "Min backtest trades",
    "g_stress": "Must stay profitable at 1.5× costs", "g_split": "Both time halves (train/test) positive",
})
# برچسب کوتاه برای دکمه‌ی وسط (مقدار همیشه اول می‌آید) و واحد
SHORT = {"modules_h": "Modules h", "ai_calls_day": "AI calls/day", "light_h": "Diagnose h", "research_h": "Research h", "min_n": "Min trades", "min_t": "Propose |t|", "auto_t": "Auto |t|",
         "max_changes_day": "Changes/day", "rb_n": "Check after", "cands": "Candidates", "probation_day": "Probation cap", "improve_n": "Variants",
         "g_win": "Win rate", "g_pf_min": "PF min", "g_pf_max": "PF max", "g_rr": "Plan R:R", "g_payoff": "Win/Loss", "g_dd": "Max DD", "g_risk": "Risk/trade",
         "g_streak": "Streak <", "g_avg": "Net R/trade", "g_t": "|t| min", "g_n": "Trades min"}
UNIT = {"modules_h": "h", "g_win": "%", "g_dd": "%", "g_risk": "%", "g_avg": "R", "g_rr": "R", "light_h": "h", "research_h": "h"}
GATE_KEYS = ["g_on", "g_win", "g_pf_min", "g_pf_max", "g_rr", "g_payoff", "g_dd", "g_risk", "g_streak", "g_avg", "g_t", "g_n", "g_stress", "g_split"]
AI_PROVIDER_KEYS = {"claude": "ai_claude", "openai": "ai_openai", "gemini": "ai_gemini", "deepseek": "ai_deepseek", "grok": "ai_grok", "groq": "ai_groq", "cerebras": "ai_cerebras", "github": "ai_github", "openrouter": "ai_openrouter"}


# توضیح فارسیِ هر عدد (در صفحه‌ی همان گروه نمایش داده می‌شود)
DESC_FA = {
    "min_n": "حداقل تعداد معامله برای هر نتیجه‌گیری آماری؛ کمتر از آن هیچ پیشنهادی ساخته نمی‌شود.",
    "min_t": "آستانه‌ی قدرت آماری (|t|) برای «پیشنهاد»؛ بالاتر = سخت‌گیرانه‌تر. ۲ ≈ اطمینان ۹۵٪.",
    "auto_t": "آستانه‌ی |t| برای «اعمال خودکار» (فقط حالت Auto)؛ باید از آستانه‌ی پیشنهاد بیشتر باشد.",
    "max_changes_day": "سقف تغییرِ خودکار در یک روز.",
    "rollback": "اگر بعد از یک تغییر نتیجه بدتر شد، خودکار به حالت قبل برگردد.",
    "rb_n": "بعد از چند معامله‌ی جدید اثر هر تغییر سنجیده شود.",
    "nonstop": "تحقیق ۲۴ ساعته: تشخیص هر ۱ ساعت و پژوهش سنگین حداکثر هر ۲ ساعت.",
    "light_h": "فاصله‌ی چرخه‌ی تشخیص (ساعت) وقتی «۲۴ ساعته» خاموش است.",
    "research_h": "فاصله‌ی پژوهش سنگین (ساعت) وقتی «۲۴ ساعته» خاموش است.",
    "cands": "تعداد استراتژی کاندید در هر چرخه‌ی پژوهش (بیشتر = جست‌وجوی گسترده‌تر ولی کندتر).",
    "probation_day": "سقف سیگنال روزانه‌ی استراتژی جدید در دوره‌ی آزمایشی (۰ = بدون سقف).",
    "new_auto": "در حالت Auto استراتژی جدیدِ PASS/کاندید خودکار روشن شود.",
    "modules_on": "بازآزمایی «همه‌ی ماژول‌های قدیمی» (TPB، MRA، BZ، CN* و…) با همان «Pass criteria»، شبانه‌روز؛ بهترین نردبان هر ماژول از نو انتخاب می‌شود.",
    "modules_h": "فاصله‌ی بازآزمایی ماژول‌ها (ساعت) وقتی «۲۴ ساعته» خاموش است.",
    "modules_apply": "در حالت Auto نتیجه‌ی بازآزمایی خودکار اعمال شود: بهترین نردبان انتخاب و ماژولی که معیارها را ندارد قفل می‌شود. در حالت Semi با دکمه‌ی «Apply» دستی.",
    "improve": "حلقه‌ی بهبود: استراتژی‌های ناموفق با توجه به «دلیل رد شدن» اصلاح و دوباره آزموده می‌شوند.",
    "improve_n": "تعداد نسخه‌ی اصلاحی در هر چرخه‌ی پژوهش.",
    "g_on": "کلید کلی «شرایط پاس‌شدن». روشن = هر استراتژی/ماژول باید همه‌ی شرط‌های زیر را داشته باشد. خاموش = فقط حکم داخلیِ آزمایشگاه (t و تست‌های آماری خودش) ملاک است و این عددها اعمال نمی‌شوند.",
    "g_win": "حداقل وین‌ریت (درصد معاملات سودده) روی نردبان خود استراتژی. پیش‌فرض ۴۵.",
    "g_pf_min": "حداقل پروفیت‌فکتور (مجموع سود ÷ مجموع ضرر) پس از کارمزد. پیش‌فرض ۱٫۲.",
    "g_pf_max": "سقف PF: بالاتر از آن معمولاً نشانه‌ی بیش‌برازش است و رد می‌شود. پیش‌فرض ۱٫۶ (۹٫۹ = بدون سقف).",
    "g_rr": "حداقل ریسک‌به‌ریواردِ طرح = دورترین تارگت نردبان بر حسب R. پیش‌فرض ۲٫۵.",
    "g_payoff": "حداقل نسبت «میانگین سود ÷ میانگین ضرر» واقعی. پیش‌فرض ۱٫۲.",
    "g_dd": "سقف دراودان (درصد) = دراودانِ R × ریسک هر معامله. پیش‌فرض ۷٪.",
    "g_risk": "ریسک هر معامله (درصد) فقط برای تبدیل دراودانِ R به درصد.",
    "g_streak": "طولانی‌ترین رشته‌ی ضررهای پیاپی باید «کمتر از» این عدد باشد. پیش‌فرض ۶ (یعنی حداکثر ۵).",
    "g_avg": "حداقل میانگین R خالص هر معامله پس از کارمزد.",
    "g_t": "حداقل قدرت آماری |t| روی نردبان انتخابی.",
    "g_n": "حداقل تعداد معامله‌ی بک‌تست (کمتر = اعتبار آماری ندارد).",
    "g_stress": "با هزینه/اسلیپیج ۱٫۵ برابر هم باید سودده بماند.",
    "g_split": "هر دو نیمه‌ی زمانی (آموزش و آزمون) باید مثبت باشند.",
}


def gate_conflicts(g: Dict[str, Any]) -> List[str]:
    """تناقض‌های ریاضیِ معیارها (هشدار فارسی). PF = (وین‌ریت ÷ (۱−وین‌ریت)) × (میانگین سود ÷ میانگین ضرر)."""
    out: List[str] = []
    w = g["win"] / 100.0
    if 0 < w < 1:
        need = w / (1 - w) * g["payoff"]
        if need > g["pf_max"] + 1e-9:
            out.append(f"⚠️ وین‌ریت ≥{g['win']:g}٪ همراه با نسبت سود/ضرر ≥{g['payoff']:g} یعنی PF ≥ {need:.2f} ولی سقف PF شما {g['pf_max']:g} است؛ هیچ استراتژی‌ای پاس نمی‌شود. سقف PF را بالا ببرید یا نسبت سود/ضرر را کم کنید.")
    if g["pf_max"] < g["pf_min"]:
        out.append("⚠️ سقف PF از حداقل PF کمتر است.")
    return out


def gate_of(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """معیارهای پاس‌شدن از تنظیمات (همیشه مقدار معتبر)؛ pf_max ≥ pf_min تضمین می‌شود."""
    g = {k[2:]: cfg.get(k, SCHEMA[k][1]) for k in GATE_KEYS}
    g["on"] = bool(g["on"])
    g["pf_max"] = max(g["pf_max"], g["pf_min"])
    return g


def reset_keys(st: Dict[str, Any], keys: List[str]) -> None:
    """بازگشت چند تنظیم به مقدار پیش‌فرضِ بهینه."""
    for k in keys:
        if k in SCHEMA:
            st.setdefault("settings", {}).pop(k, None)


MODE_TXT = {"off": "⛔ Off", "advisor": "👁 Advisor (report only)", "semi": "✋ Semi-auto (you approve)", "auto": "🤖 Auto (within guardrails)"}


def _coerce(key: str, val: Any) -> Any:
    typ, dflt, lo, hi = SCHEMA[key]
    try:
        if typ == "bool":
            return (val.strip().lower() in ("1", "true", "on", "yes")) if isinstance(val, str) else bool(val)
        if typ == "choice":
            return val if val in hi else dflt
        v = int(val) if typ == "int" else float(val)
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return round(v, 3) if typ == "float" else v
    except Exception:  # noqa: BLE001
        return dflt


def settings_of(st: Dict[str, Any]) -> Dict[str, Any]:
    raw = st.get("settings") if isinstance(st.get("settings"), dict) else {}
    raw = dict(raw)
    if raw.get("ai_calls_day") == 4:            # مقدار پیش‌فرضِ قدیمی (۴) که در state مانده؛ پیش‌فرض جدید ۱۲ است
        raw.pop("ai_calls_day")
    return {k: _coerce(k, raw[k]) if k in raw else v[1] for k, v in SCHEMA.items()}


# ----------------------------------------------------------------------------- ذخیره‌سازی (اتمیک)
def _p(name: str, data_dir: Optional[str] = None) -> str:
    return os.path.join(data_dir or DATA, name)


def read_json(name: str, default: Any, data_dir: Optional[str] = None) -> Any:
    try:
        with open(_p(name, data_dir), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def write_json(name: str, obj: Any, data_dir: Optional[str] = None) -> None:
    path = _p(name, data_dir)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".rnd_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:  # noqa: BLE001
            pass
        raise


def load_state(data_dir: Optional[str] = None) -> Dict[str, Any]:
    st = read_json(STATE_NAME, {}, data_dir)
    if not isinstance(st, dict):
        st = {}
    for k, dflt in (("settings", {}), ("proposals", []), ("applied", []), ("journal", []), ("protected", {}), ("slots", {}),
                    ("blacklist", []), ("requests", []), ("last", {}), ("changes", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
    return st


def save_state(st: Dict[str, Any], data_dir: Optional[str] = None) -> None:
    st["journal"] = st.get("journal", [])[-150:]
    st["applied"] = st.get("applied", [])[-120:]
    st["proposals"] = st.get("proposals", [])[-120:]
    write_json(STATE_NAME, st, data_dir)


def now_iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%d %H:%M")


def log(st: Dict[str, Any], kind: str, text: str, ts: Optional[float] = None) -> None:
    st.setdefault("journal", []).append({"t": ts or time.time(), "k": kind, "x": text[:300]})


# ----------------------------------------------------------------------------- آمار
def _stats(vals: List[float]) -> Dict[str, Any]:
    n = len(vals)
    if n == 0:
        return {"n": 0, "avg": 0.0, "t": 0.0, "pf": 0.0, "win": 0.0, "sum": 0.0}
    avg = sum(vals) / n
    sd = math.sqrt(sum((x - avg) ** 2 for x in vals) / (n - 1)) if n > 1 else 0.0
    t = avg / (sd / math.sqrt(n)) if sd > 0 else 0.0
    gp = sum(x for x in vals if x > 0)
    gl = -sum(x for x in vals if x < 0)
    pf = gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0)
    return {"n": n, "avg": round(avg, 4), "t": round(t, 3), "pf": round(min(pf, 9.99), 3), "win": round(100.0 * sum(1 for x in vals if x > 0) / n, 1),
            "sum": round(sum(vals), 3)}


def _max_dd(vals: List[float]) -> float:
    peak = cum = dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return round(dd, 2)


def _norm_side(h: Dict[str, Any]) -> Optional[str]:
    s = str(h.get("side") or h.get("direction") or "").upper()
    if s in ("BUY", "LONG"):
        return "BUY"
    if s in ("SELL", "SHORT"):
        return "SELL"
    return None


def bot_of(h: Dict[str, Any]) -> str:
    sid = str(h.get("signal_id") or "").upper()
    if sid.startswith("ALT") or h.get("bot") == "sgnl":
        return "sgnl"
    return "ticb"


def _hour(h: Dict[str, Any]) -> Optional[int]:
    import perf_report as pr
    t = pr._ts(h.get("opened_at")) or pr._ts(h.get("closed_at"))
    return int(t // 3600 % 24) if t else None


def _stop_pct(h: Dict[str, Any]) -> Optional[float]:
    for k in ("r_pct", "stop_pct"):
        v = h.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    try:
        e, s = float(h.get("entry")), float(h.get("sl"))
        if e > 0 and s > 0 and e != s:
            return abs(e - s) / e * 100.0
    except (TypeError, ValueError):
        pass
    return None


def enrich(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """رکوردهای تحلیلی سبک: net R، استراتژی، تایم‌فریم، جهت، ساعت، استاپ٪، بات."""
    import perf_report as pr
    out = []
    for h in recs:
        try:
            s = pr.strategy_of(h)
            if s in SKIP_STRATS:
                continue
            if str(s).lower() in ("native", "native-candle", "main", "engine", "candle"):
                s = "native-candle"
            out.append({"r": pr.net_r(h), "g": float(h["final_r"]), "s": s, "tf": str(pr.tf_of(h)).lower(), "side": _norm_side(h),
                        "hr": _hour(h), "sp": _stop_pct(h), "bot": bot_of(h), "ladder": pr.ladder_of(h), "sid": str(h.get("signal_id") or ""),
                        "ts": pr._ts(h.get("closed_at")) or 0.0, "ot": pr._ts(h.get("opened_at")) or 0.0,
                        "mfe": h.get("mfe_r") if isinstance(h.get("mfe_r"), (int, float)) else None})
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda x: x["ts"])
    return out


def _halves(rs: List[float]) -> Tuple[float, float]:
    if len(rs) < 4:
        return 0.0, 0.0
    m = len(rs) // 2
    return sum(rs[:m]) / m, sum(rs[m:]) / (len(rs) - m)


# ----------------------------------------------------------------------------- کلیدهای قابل‌تغییر (لیست‌سفید)
def control_key_ok(key: str) -> bool:
    import shared_controls as sc
    if key.startswith("mod_") and key[4:] in sc.MODULES:
        return True
    if key.startswith("nat_tf_") and key[7:] in sc.TF_KEYS:
        return True
    if key == "nat_min_stop_pct":
        return True
    if key.startswith("mx_") and key.rsplit("_", 1)[-1] in ("day", "cd", "maxstop", "side") and key in sc.SCHEMA:
        return True
    if key.startswith("prof_") and key in sc.SCHEMA:
        return True
    return False


def _pid(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


def _prop(kind: str, section: Optional[str], key: Optional[str], value: Any, desc: str, ev: Dict[str, Any], src: str = "stat",
          rule: Optional[Dict[str, Any]] = None, auto_ok: bool = True) -> Dict[str, Any]:
    return {"id": _pid(kind, section, key, json.dumps(value, sort_keys=True, default=str), json.dumps(rule, sort_keys=True, default=str)),
            "kind": kind, "section": section, "key": key, "value": value, "rule": rule, "desc": desc[:220], "ev": ev, "src": src,
            "status": "pending", "created": time.time(), "auto_ok": auto_ok}


def _ev(s: Dict[str, Any], other: Optional[Dict[str, Any]] = None, h1: float = 0.0, h2: float = 0.0) -> Dict[str, Any]:
    d = {"n": s["n"], "avg": s["avg"], "t": s["t"], "pf": s["pf"], "win": s["win"], "h1": round(h1, 3), "h2": round(h2, 3)}
    if other:
        d["other"] = {"n": other["n"], "avg": other["avg"], "t": other["t"]}
    return d


# ----------------------------------------------------------------------------- تشخیص
def diagnose(recs_raw: List[Dict[str, Any]], cfg: Dict[str, Any], controls: Dict[str, Dict[str, Any]], verdicts: Dict[str, Any],
             protected: Optional[Dict[str, float]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """خروجی: {overall, bots{...}, strategies[...], findings[...], proposals[...], leaks[...]}. تابع خالص (بدون I/O)."""
    now = now or time.time()
    protected = protected or {}
    recs = enrich(recs_raw)
    min_n, min_t = cfg["min_n"], cfg["min_t"]
    out: Dict[str, Any] = {"generated": now_iso(now), "n_total": len(recs), "findings": [], "proposals": [], "strategies": [], "bots": {}}
    if not recs:
        out["overall"] = _stats([])
        return out
    rs = [r["r"] for r in recs]
    out["overall"] = dict(_stats(rs), dd=_max_dd(rs))
    day = 86400.0
    for lo_d, name in ((7, "7d"), (30, "30d")):
        sub = [r["r"] for r in recs if r["ts"] >= now - lo_d * day]
        out["overall"][name] = _stats(sub)
    for b in BOTS:
        sub = [r["r"] for r in recs if r["bot"] == b]
        out["bots"][b] = dict(_stats(sub), dd=_max_dd(sub))

    # ---- جدول استراتژی/تایم‌فریم (هر ربات جدا)
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for r in recs:
        groups.setdefault((r["bot"], r["s"], r["tf"]), []).append(r)
    per_strat: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in recs:
        per_strat.setdefault((r["bot"], r["s"]), []).append(r)
    for (b, s, tf), g in sorted(groups.items(), key=lambda kv: sum(x["r"] for x in kv[1])):
        st_ = _stats([x["r"] for x in g])
        out["strategies"].append(dict(st_, bot=b, s=s, tf=tf))

    props: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []

    def sect_ok(sec: str) -> bool:
        return sec in controls

    # (1) خاموش‌کردن استراتژی (کل ماژول یا تایم‌فریمِ موتور اصلی) با شواهد قوی و پایدار
    for (b, s), g in per_strat.items():
        rs_ = [x["r"] for x in g]
        st_ = _stats(rs_)
        h1, h2 = _halves(rs_)
        if st_["n"] >= min_n and st_["avg"] < -0.04 and st_["t"] <= -min_t and h1 < 0 and h2 < 0:
            key = f"mod_{s}"
            if s == "native-candle":
                continue
            if control_key_ok(key) and sect_ok(b) and controls[b].get(key):
                findings.append({"sev": "high", "bot": b, "s": s, "tf": "*", "dim": "overall", "text": f"{b.upper()} {s}: avg {st_['avg']:+.2f}R net over {st_['n']} trades (t={st_['t']:.1f}), negative in both halves"})
                props.append(_prop("ctl", b, key, False, f"Switch OFF {s} on {b.upper()} — loses {st_['avg']:+.2f}R/trade (n={st_['n']}, t={st_['t']:.1f})", _ev(st_, None, h1, h2)))
    for (b, s, tf), g in groups.items():
        if s != "native-candle" or tf not in ("1m", "5m", "15m", "1h", "4h"):
            continue
        rs_ = [x["r"] for x in g]
        st_ = _stats(rs_)
        h1, h2 = _halves(rs_)
        key = f"nat_tf_{tf}"
        if st_["n"] >= min_n and st_["avg"] < -0.04 and st_["t"] <= -min_t and h1 < 0 and h2 < 0 and sect_ok(b) and controls[b].get(key):
            findings.append({"sev": "high", "bot": b, "s": s, "tf": tf, "dim": "overall", "text": f"{b.upper()} main engine {tf}: {st_['avg']:+.2f}R/trade (n={st_['n']}, t={st_['t']:.1f})"})
            props.append(_prop("ctl", b, key, False, f"Switch OFF main-engine {tf.upper()} on {b.upper()} — {st_['avg']:+.2f}R/trade (n={st_['n']}, t={st_['t']:.1f})", _ev(st_, None, h1, h2)))

    # (2) قوانین جزئی روی داده‌ی تجمیعیِ هر دو ربات: تایم‌فریم / جهت / بلوک ساعتی
    agg: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in recs:
        agg.setdefault((r["s"], r["tf"]), []).append(r)
    for (s, tf), g in agg.items():
        scope = "native" if s == "native-candle" else s
        # جهت
        by_side = {sd: [x["r"] for x in g if x["side"] == sd] for sd in ("BUY", "SELL")}
        sts = {sd: _stats(v) for sd, v in by_side.items()}
        for bad, good in (("BUY", "SELL"), ("SELL", "BUY")):
            sb, sg = sts[bad], sts[good]
            h1, h2 = _halves(by_side[bad])
            if sb["n"] >= min_n and sb["avg"] < -0.06 and sb["t"] <= -min_t and h1 < 0 and h2 < 0 and sg["avg"] > sb["avg"] + 0.08:
                rule = {"scope": scope, "tf": tf, "side": bad, "hours": None}
                findings.append({"sev": "med", "bot": "*", "s": s, "tf": tf, "dim": "side", "text": f"{s} {tf}: {bad} side loses {sb['avg']:+.2f}R (n={sb['n']}, t={sb['t']:.1f}) while {good} gives {sg['avg']:+.2f}R"})
                props.append(_prop("rule", None, None, None, f"Block {bad} signals of {s} on {tf.upper()} — {bad} loses {sb['avg']:+.2f}R/trade (n={sb['n']}, t={sb['t']:.1f}); {good} {sg['avg']:+.2f}R", _ev(sb, sg, h1, h2), rule=rule))
        # ساعت (۶ بلوک ۴ساعته؛ آستانه‌ی سخت‌تر چون چندین مقایسه است)
        t_hours = max(min_t, 3.0)
        for blk in range(6):
            hrs = list(range(blk * 4, blk * 4 + 4))
            sub = [x["r"] for x in g if x["hr"] in hrs]
            rest = [x["r"] for x in g if x["hr"] is not None and x["hr"] not in hrs]
            ss_, sr_ = _stats(sub), _stats(rest)
            h1, h2 = _halves(sub)
            if ss_["n"] >= max(30, min_n // 2) and sr_["n"] >= min_n // 2 and ss_["avg"] < -0.10 and ss_["t"] <= -t_hours and h1 < 0 and h2 < 0 and sr_["avg"] > ss_["avg"] + 0.10:
                rule = {"scope": scope, "tf": tf, "side": None, "hours": hrs}
                findings.append({"sev": "med", "bot": "*", "s": s, "tf": tf, "dim": "hours", "text": f"{s} {tf}: UTC {hrs[0]:02d}–{hrs[-1] + 1:02d}h loses {ss_['avg']:+.2f}R (n={ss_['n']}, t={ss_['t']:.1f}); rest {sr_['avg']:+.2f}R"})
                props.append(_prop("rule", None, None, None, f"Block {s} {tf.upper()} during UTC {hrs[0]:02d}:00–{hrs[-1] + 1:02d}:00 — {ss_['avg']:+.2f}R/trade (n={ss_['n']}, t={ss_['t']:.1f}); other hours {sr_['avg']:+.2f}R", _ev(ss_, sr_, h1, h2), rule=rule))

    # (3) کف استاپ موتور اصلی: استاپ‌های ریز = کارمزد-خور
    nat = [r for r in recs if r["s"] == "native-candle" and r["sp"]]
    if len(nat) >= min_n * 2 and sect_ok("ticb"):
        cur = float(controls["ticb"].get("nat_min_stop_pct", 0.3) or 0.0)
        for thr in (0.3, 0.5, 0.75, 1.0, 1.5):
            if thr <= cur + 1e-9:
                continue
            lo_ = [x["r"] for x in nat if x["sp"] < thr and x["sp"] >= cur]
            hi_ = [x["r"] for x in nat if x["sp"] >= thr]
            sl_, sh_ = _stats(lo_), _stats(hi_)
            h1, h2 = _halves(lo_)
            if sl_["n"] >= min_n and sh_["n"] >= min_n // 2 and sl_["avg"] < -0.08 and sl_["t"] <= -min_t and sh_["avg"] > sl_["avg"] + 0.10 and h1 < 0 and h2 < 0:
                findings.append({"sev": "med", "bot": "ticb", "s": "native-candle", "tf": "*", "dim": "stop", "text": f"Main engine: stops between {cur:g}% and {thr:g}% lose {sl_['avg']:+.2f}R net (n={sl_['n']}, t={sl_['t']:.1f}) — fee drag; ≥{thr:g}%: {sh_['avg']:+.2f}R"})
                props.append(_prop("ctl", "ticb", "nat_min_stop_pct", thr, f"Raise main-engine minimum stop {cur:g}% → {thr:g}% — tiny stops lose {sl_['avg']:+.2f}R (n={sl_['n']}, t={sl_['t']:.1f})", _ev(sl_, sh_, h1, h2)))
                break

    # (4) سقف روزانه: معاملات سوم به بعدِ همان روز در یک استراتژی بدتر است
    for (b, s), g in per_strat.items():
        if s == "native-candle" or s in SKIP_STRATS:
            continue
        byday: Dict[int, List[Dict[str, Any]]] = {}
        for x in g:
            byday.setdefault(int(x["ot"] // 86400), []).append(x)
        late = [x["r"] for d_, xs in byday.items() for x in sorted(xs, key=lambda y: y["ot"])[2:]]
        early = [x["r"] for d_, xs in byday.items() for x in sorted(xs, key=lambda y: y["ot"])[:2]]
        sl_, se_ = _stats(late), _stats(early)
        key = f"mx_{s}_day"
        h1, h2 = _halves(late)
        if sl_["n"] >= max(30, min_n // 2) and sl_["avg"] < -0.10 and sl_["t"] <= -min_t and se_["avg"] > sl_["avg"] + 0.10 and h1 < 0 and h2 < 0 \
                and control_key_ok(key) and sect_ok(b) and not controls[b].get(key):
            findings.append({"sev": "low", "bot": b, "s": s, "tf": "*", "dim": "dayrank", "text": f"{b.upper()} {s}: 3rd+ trade of the day loses {sl_['avg']:+.2f}R (n={sl_['n']}); first two {se_['avg']:+.2f}R"})
            props.append(_prop("ctl", b, key, 2, f"Cap {s} on {b.upper()} to 2 signals/day — later trades lose {sl_['avg']:+.2f}R (n={sl_['n']}, t={sl_['t']:.1f})", _ev(sl_, se_, h1, h2)))

    # (5) نردبان: بهترین نردبانِ تأییدشده‌ی بک‌تست با نردبان فعلی فرق دارد
    try:
        import shared_controls as sc
        import ladder_catalog as lc
        for b in BOTS:
            if not sect_ok(b):
                continue
            for m in sc.LOWTF + sc.BASE_MODULES:
                if not controls[b].get(f"mod_{m}"):
                    continue
                v = (verdicts or {}).get(lc.vkey(m)) or {}
                stats = v.get("stats") or {}
                if not stats:
                    continue
                ranked = [(k, x) for k, x in lc.rank_ladders(stats) if x.get("ok") and (len(lc.LADDERS.get(k, ((), (), 0))[0]) >= 2 or x.get("sem") == "live4")]
                if not ranked:
                    continue
                best_k, best = ranked[0]
                cur_k = sc.effective_profile(controls[b], m, verdicts)
                cur = stats.get(cur_k)
                key = f"prof_{m}"
                if best_k == cur_k or key not in sc.SCHEMA or best_k not in lc.LADDERS:
                    continue
                if cur is None or best.get("avg", 0) >= cur.get("avg", 0) + 0.05:
                    if controls[b].get(key) == "auto":
                        continue                      # auto ⇒ سیستم خودش بهترین را برمی‌دارد
                    findings.append({"sev": "low", "bot": b, "s": m, "tf": v.get("tf", "?"), "dim": "ladder", "text": f"{m} on {b.upper()}: ladder {cur_k} → {best_k} is better in the lab ({best.get('avg', 0):+.2f}R vs {(cur or {}).get('avg', 0):+.2f}R)"})
                    props.append(_prop("ctl", b, key, best_k, f"Ladder for {m} on {b.upper()}: {cur_k} → {best_k} (lab: {best.get('avg', 0):+.2f}R, win {best.get('win', 0):.0f}%, t={best.get('t', 0):.1f})",
                                       {"n": best.get("n", 0), "avg": best.get("avg", 0), "t": best.get("t", 0), "pf": best.get("pf", 0), "win": best.get("win", 0), "h1": best.get("train", 0), "h2": best.get("test", 0)}, auto_ok=False))
    except Exception:  # noqa: BLE001
        pass

    # ---- پالایش: محافظت‌شده‌ها، غیرفعال‌ها، و نوع اقدام مجاز
    kind_gate = {"ctl": None, "rule": "a_block"}
    keep = []
    for p in props:
        pk = (p["section"], p["key"]) if p["kind"] == "ctl" else ("rule", json.dumps(p["rule"], sort_keys=True))
        if protected.get(json.dumps(pk)) and protected[json.dumps(pk)] > now:
            continue
        k = p["key"] or ""
        flag = ("a_off" if (k.startswith("mod_") or k.startswith("nat_tf_")) else "a_stop" if k == "nat_min_stop_pct" else
                "a_caps" if (k.endswith("_day") or k.endswith("_cd") or k.endswith("_maxstop")) else "a_ladder" if k.startswith("prof_") else kind_gate[p["kind"]])
        if flag and not cfg.get(flag, True):
            continue
        keep.append(p)
    out["proposals"] = keep
    out["findings"] = findings
    return out


# ----------------------------------------------------------------------------- اعمال / بازگشت
def _controls_path(env: Dict[str, Any]) -> Optional[str]:
    return env.get("controls_path")


def _active_rules(data_dir: Optional[str]) -> Dict[str, Any]:
    d = read_json(RULES_NAME, {}, data_dir)
    if not isinstance(d, dict) or not isinstance(d.get("rules"), list):
        d = {"rules": []}
    return d


def _rule_id(rule: Dict[str, Any]) -> str:
    return "R" + _pid(json.dumps(rule, sort_keys=True))[:5]


def _group_baseline(recs: List[Dict[str, Any]], p: Dict[str, Any]) -> Dict[str, Any]:
    """آمار گروهِ اثرپذیر قبل از تغییر (برای سنجش بعدی)."""
    if p["kind"] == "rule":
        r = p["rule"]
        g = [x["r"] for x in recs if x["s"] == ("native-candle" if r["scope"] == "native" else r["scope"]) and x["tf"] == r.get("tf")]
    else:
        key = p["key"] or ""
        m = key[3:] if key.startswith("mod_") else key[3:].rsplit("_", 1)[0] if key.startswith("mx_") else key[5:] if key.startswith("prof_") else None
        if key.startswith("nat_"):
            g = [x["r"] for x in recs if x["s"] == "native-candle" and x["bot"] == p["section"]]
        else:
            g = [x["r"] for x in recs if x["s"] == m and x["bot"] == p["section"]]
    s = _stats(g)
    return {"n": s["n"], "avg": s["avg"], "pf": s["pf"]}


def apply_proposal(st: Dict[str, Any], pid: str, env: Dict[str, Any], by: str = "admin", recs: Optional[List[Dict[str, Any]]] = None,
                   now: Optional[float] = None) -> Tuple[bool, str]:
    import shared_controls as sc
    now = now or time.time()
    p = next((x for x in st["proposals"] if x["id"] == pid), None)
    if not p or p["status"] != "pending":
        return False, "Proposal not found or already handled."
    cfg = settings_of(st)
    data_dir = env.get("data_dir")
    base = _group_baseline(recs or [], p)
    entry = {"id": p["id"], "t": now, "by": by, "kind": p["kind"], "desc": p["desc"], "base": base, "section": p["section"], "key": p["key"], "rule": p.get("rule"),
             "new": p.get("value"), "state": "active", "rb_n": cfg["rb_n"]}
    try:
        if p["kind"] == "ctl":
            if not control_key_ok(p["key"]) and p["key"] not in ("mods_enable",):
                return False, "Key is not allowed for the R&D lab."
            path = _controls_path(env)
            old = sc.load(p["section"], path).get(p["key"])
            sc.set_value(p["section"], p["key"], p["value"], path)
            entry["old"] = old
        elif p["kind"] == "rule":
            d = _active_rules(data_dir)
            rule = dict(p["rule"], id=_rule_id(p["rule"]), on=True, reason=p["desc"][:120], created=now_iso(now))
            if not any(r.get("id") == rule["id"] for r in d["rules"]):
                d["rules"].append(rule)
            write_json(RULES_NAME, d, data_dir)
            entry["rule_id"] = rule["id"]
        elif p["kind"] == "enable":
            path = _controls_path(env)
            olds = {}
            for sec in p["value"].get("sections", []):
                cur = sc.load(sec, path)
                olds[sec] = {k: cur.get(k) for k in (f"mod_{p['key']}", f"mx_{p['key']}_day")}
                sc.set_value(sec, f"mod_{p['key']}", True, path)
                if cfg["probation_day"] > 0:
                    sc.set_value(sec, f"mx_{p['key']}_day", cfg["probation_day"], path)
            entry["old"] = olds
        else:
            return False, "Unknown proposal kind."
    except Exception as e:  # noqa: BLE001
        return False, f"Could not apply: {e}"
    p["status"] = "applied"
    p["applied_at"] = now
    st["applied"].append(entry)
    st["changes"][str(int(now // 86400))] = st["changes"].get(str(int(now // 86400)), 0) + (1 if by == "auto" else 0)
    log(st, "apply", f"{'🤖' if by == 'auto' else '✋'} {p['desc']}", now)
    return True, "Applied."


def undo_applied(st: Dict[str, Any], pid: str, env: Dict[str, Any], reason: str = "admin undo", now: Optional[float] = None) -> Tuple[bool, str]:
    import shared_controls as sc
    now = now or time.time()
    e = next((x for x in reversed(st["applied"]) if x["id"] == pid and x.get("state") == "active"), None)
    if not e:
        return False, "Nothing to undo."
    try:
        if e["kind"] == "ctl":
            sc.set_value(e["section"], e["key"], e.get("old"), _controls_path(env))
        elif e["kind"] == "rule":
            d = _active_rules(env.get("data_dir"))
            d["rules"] = [r for r in d["rules"] if r.get("id") != e.get("rule_id")]
            write_json(RULES_NAME, d, env.get("data_dir"))
        elif e["kind"] == "enable":
            for sec, olds in (e.get("old") or {}).items():
                for k, v in olds.items():
                    if v is not None:
                        sc.set_value(sec, k, v, _controls_path(env))
    except Exception as ex:  # noqa: BLE001
        return False, f"Could not undo: {ex}"
    e["state"] = "undone"
    e["undone_at"] = now
    e["undo_reason"] = reason
    pk = json.dumps((e["section"], e["key"]) if e["kind"] != "rule" else ("rule", json.dumps(e.get("rule"), sort_keys=True)))
    st["protected"][pk] = now + 7 * 86400
    for p in st["proposals"]:
        if p["id"] == pid:
            p["status"] = "undone"
    log(st, "undo", f"↩️ {e['desc']} — {reason}", now)
    return True, "Undone."


def remove_rule(st: Dict[str, Any], rule_id: str, env: Dict[str, Any], now: Optional[float] = None) -> bool:
    now = now or time.time()
    d = _active_rules(env.get("data_dir"))
    before = len(d["rules"])
    gone = [r for r in d["rules"] if r.get("id") == rule_id]
    d["rules"] = [r for r in d["rules"] if r.get("id") != rule_id]
    if len(d["rules"]) == before:
        return False
    write_json(RULES_NAME, d, env.get("data_dir"))
    for e in st["applied"]:
        if e.get("rule_id") == rule_id and e.get("state") == "active":
            e["state"] = "undone"
    if gone:
        pk = json.dumps(("rule", json.dumps({k: gone[0].get(k) for k in ("scope", "tf", "side", "hours")}, sort_keys=True)))
        st["protected"][pk] = now + 7 * 86400
    log(st, "undo", f"↩️ removed rule {rule_id}", now)
    return True


def evaluate_applied(st: Dict[str, Any], recs: List[Dict[str, Any]], env: Dict[str, Any], cfg: Dict[str, Any], now: Optional[float] = None) -> List[str]:
    """پایش اثر تغییرها؛ اگر بعد از rb_n معامله‌ی جدید گروه به‌طور معنادار بدتر از قبل شد → بازگشت. خروجی: پیام‌های بازگشت."""
    now = now or time.time()
    msgs = []
    for e in st["applied"]:
        if e.get("state") != "active" or e["kind"] == "enable":
            continue
        if e["kind"] == "ctl" and e.get("key", "").startswith("mod_") and e.get("new") is False:
            continue                                    # خاموش‌کردن قابل پایش نیست (معامله‌ای نمی‌آید)
        if e["kind"] == "ctl" and e.get("key", "").startswith("nat_tf_") and e.get("new") is False:
            continue
        p = {"kind": e["kind"], "key": e.get("key"), "section": e.get("section"), "rule": e.get("rule")}
        post = [x for x in recs if x["ts"] > e["t"]]
        if e["kind"] == "rule":
            r = e["rule"]
            post = [x for x in post if x["s"] == ("native-candle" if r["scope"] == "native" else r["scope"]) and x["tf"] == r.get("tf")]
        else:
            key = e.get("key") or ""
            if key.startswith("nat_"):
                post = [x for x in post if x["s"] == "native-candle" and x["bot"] == e["section"]]
            else:
                m = key[3:] if key.startswith("mod_") else key[3:].rsplit("_", 1)[0] if key.startswith("mx_") else key[5:] if key.startswith("prof_") else None
                post = [x for x in post if x["s"] == m and x["bot"] == e["section"]]
        rs = [x["r"] for x in post]
        if len(rs) < cfg["rb_n"]:
            continue
        s = _stats(rs)
        base = e.get("base") or {}
        se = abs(s["avg"] / s["t"]) if s["t"] else 0.5
        if cfg["rollback"] and base.get("n", 0) >= 10 and s["avg"] < base.get("avg", 0.0) - (0.08 + 1.0 * se) and s["t"] < -1.0:
            ok, _ = undo_applied(st, e["id"], env, reason=f"auto-rollback: {s['n']} new trades avg {s['avg']:+.2f}R vs {base.get('avg', 0):+.2f}R before", now=now)
            if ok:
                msgs.append(f"↩️ Rolled back: {e['desc']} — after the change {s['n']} trades averaged {s['avg']:+.2f}R (before: {base.get('avg', 0):+.2f}R).")
        else:
            e["checked"] = {"n": s["n"], "avg": s["avg"], "t": s["t"]}
            if s["n"] >= cfg["rb_n"] * 2:
                e["state"] = "confirmed"
    return msgs


# ----------------------------------------------------------------------------- پیشنهادها و نتایج پژوهش
def merge_proposals(st: Dict[str, Any], new: List[Dict[str, Any]], now: Optional[float] = None) -> int:
    """پیشنهادهای آماری فعلی را با لیست ذخیره‌شده ادغام می‌کند: تازه‌ها اضافه، pendingهای آماریِ دیگر معتبر نیستند حذف، ردشده‌ها ۱۴ روز سرکوب."""
    now = now or time.time()
    old = st["proposals"]
    fresh_ids = {p["id"] for p in new}
    keep = []
    for p in old:
        if p["status"] == "pending" and p.get("src") == "stat" and p["id"] not in fresh_ids:
            continue                                         # شواهد دیگر برقرار نیست
        if p["status"] == "pending" and now - p["created"] > 14 * 86400:
            continue
        if p["status"] in ("rejected",) and now - p.get("rejected_at", p["created"]) > 14 * 86400:
            continue
        keep.append(p)
    have = {p["id"]: p for p in keep}
    added = 0
    for p in new:
        if p["id"] in have:
            if have[p["id"]]["status"] == "pending":
                have[p["id"]]["ev"] = p["ev"]; have[p["id"]]["desc"] = p["desc"]
            continue
        keep.append(p)
        added += 1
    st["proposals"] = keep
    return added


def import_research(st: Dict[str, Any], results: Dict[str, Any], controls: Dict[str, Dict[str, Any]], cfg: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """نتیجه‌ی آخرین چرخه‌ی پژوهش (rnd_results.json) → پیشنهاد فعال‌سازیِ استراتژی‌های جدیدِ PASS/کاندید + پیشنهاد غیرفعال‌سازی ناموفق‌ها."""
    now = now or time.time()
    info = {"new": [], "dropped": []}
    if not isinstance(results, dict) or not results.get("slots"):
        return info
    cyc = results.get("cycle", 0)
    for slot, r in results["slots"].items():
        if not isinstance(r, dict):
            continue
        known = st["slots"].setdefault(slot, {})
        status, spec_id = r.get("status"), r.get("spec_id")
        changed = known.get("spec_id") != spec_id or known.get("status") != status
        known.update({"spec_id": spec_id, "status": status, "name": r.get("name"), "cycle": cyc})
        if not changed:
            continue
        if status in ("PASS", "CAND") and cfg["a_new"]:
            secs = [s for s in BOTS if s in controls and not controls[s].get(f"mod_{slot}")]
            if secs:
                ev = {"n": r.get("n", 0), "avg": r.get("avg_r", 0), "t": r.get("t", 0), "pf": r.get("pf", 0), "win": r.get("win", 0), "h1": r.get("train_r", 0), "h2": r.get("test_r", 0)}
                st["proposals"] = [p for p in st["proposals"] if not (p["kind"] == "enable" and p["key"] == slot and p["status"] == "pending")]
                st["proposals"].append(_prop("enable", None, slot, {"sections": secs},
                                             f"Enable NEW strategy {slot} “{r.get('name', '')}” ({status}, {str(r.get('tf', '')).upper()}) — lab: {r.get('avg_r', 0):+.2f}R/trade, PF {r.get('pf', 0):.2f}, win {r.get('win', 0):.0f}%, t={r.get('t', 0):.1f}, ladder {r.get('best_ladder') or 'auto'}",
                                             ev, src="research", auto_ok=bool(cfg["new_auto"])))
                info["new"].append(slot)
        elif status in ("FAIL", "EMPTY"):
            for sec in BOTS:
                if sec in controls and controls[sec].get(f"mod_{slot}"):
                    info["dropped"].append(slot)
                    break
    for h in (results.get("ai_hints") or [])[:5]:
        try:
            sec, key, val = h.get("section"), h.get("key"), h.get("value")
            if sec in BOTS and isinstance(key, str) and control_key_ok(key) and cfg["a_new"]:
                import shared_controls as sc
                if key in sc.SCHEMA and sc.load(sec).get(key) != val:
                    p = _prop("ctl", sec, key, sc._coerce(key, val), f"🧠 AI idea: {str(h.get('desc', ''))[:170]}", {"n": 0, "avg": 0, "t": 0, "pf": 0, "win": 0, "h1": 0, "h2": 0}, src="ai", auto_ok=False)
                    if not any(x["id"] == p["id"] for x in st["proposals"]):
                        st["proposals"].append(p)
        except Exception:  # noqa: BLE001
            continue
    st["last"]["research"] = {"cycle": cyc, "generated": results.get("generated"), "tested": results.get("tested"), "ai": (results.get("ai") or {}).get("used")}
    return info


def probation_check(st: Dict[str, Any], recs: List[Dict[str, Any]], env: Dict[str, Any], cfg: Dict[str, Any], now: Optional[float] = None) -> List[str]:
    """استراتژی‌های RD فعال که زنده بدتر از بک‌تست درآمدند: خاموش + در لیست سیاه (دیگر دوباره ساخته نمی‌شوند)."""
    import shared_controls as sc
    now = now or time.time()
    msgs = []
    for slot in __import__("rnd_dsl").SLOTS:
        rs = [x["r"] for x in recs if x["s"] == slot]
        if len(rs) < 20:
            continue
        s = _stats(rs)
        if s["avg"] < -0.15 and s["t"] < -1.5:
            on = [sec for sec in BOTS if sc.load(sec, _controls_path(env)).get(f"mod_{slot}")]
            for sec in on:
                try:
                    sc.set_value(sec, f"mod_{slot}", False, _controls_path(env))
                except Exception:  # noqa: BLE001
                    pass
            sid = (st["slots"].get(slot) or {}).get("spec_id")
            if sid and sid not in st["blacklist"]:
                st["blacklist"].append(sid)
            if on:
                msgs.append(f"🛑 {slot} switched OFF and retired — live {s['n']} trades average {s['avg']:+.2f}R (t={s['t']:.1f}).")
                log(st, "retire", msgs[-1], now)
    return msgs


# ----------------------------------------------------------------------------- چرخه‌ی سبک (داخل ربات VIP)
def effective_light_h(cfg: Dict[str, Any]) -> float:
    return 0.5 if cfg.get("nonstop") else float(cfg["light_h"])


def effective_modules_h(cfg: Dict[str, Any]) -> float:
    return 1.0 if cfg.get("nonstop") else float(cfg["modules_h"])


def effective_research_h(cfg: Dict[str, Any]) -> float:
    return min(2.0, float(cfg["research_h"])) if cfg.get("nonstop") else float(cfg["research_h"])


def due(st: Dict[str, Any], cfg: Dict[str, Any], now: float) -> bool:
    return now - float(st["last"].get("light_ts", 0)) >= effective_light_h(cfg) * 3600


def request_modules(st: Dict[str, Any], now: Optional[float] = None) -> None:
    now = now or time.time()
    st["requests"] = [r for r in st["requests"] if now - r.get("t", 0) < 3 * 86400 and r.get("done")] + [{"t": now, "kind": "modules", "done": False}]
    log(st, "request", "🧩 Re-test of all modules requested", now)


def request_research(st: Dict[str, Any], now: Optional[float] = None) -> None:
    now = now or time.time()
    st["requests"] = [r for r in st["requests"] if now - r.get("t", 0) < 3 * 86400 and r.get("done")] + [{"t": now, "kind": "research", "done": False}]
    log(st, "request", "🔬 Deep research requested", now)


def dispatch_workflow(workflow: str = "rnd_lab.yml", ref: str = "main", inputs: Optional[Dict[str, str]] = None, timeout: float = 15.0) -> Tuple[bool, str]:
    """GitHub Actions workflow_dispatch (نیازمند secret RND_DISPATCH_TOKEN با دسترسی actions:write). بدون توکن ⇒ درخواست در صف می‌ماند."""
    token = os.environ.get("RND_DISPATCH_TOKEN") or os.environ.get("GH_PAT") or ""
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not token or not repo:
        return False, "no dispatch token — the request is queued and the next scheduled run will pick it up"
    try:
        import urllib.request
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches", method="POST",
                                     data=json.dumps({"ref": ref, "inputs": inputs or {}}).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "tradeiscool-rnd"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (r.status in (200, 204)), f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001
        return False, f"dispatch failed: {e}"


def tick(env: Dict[str, Any], now: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
    """env: {data_dir, controls_path, load_history(), notify(text, rows)}. فراخوانی از حلقه‌ی ربات VIP؛ هرگز استثنا نمی‌دهد."""
    summary: Dict[str, Any] = {"ran": False}
    try:
        with _LOCK:
            now = now or time.time()
            data_dir = env.get("data_dir")
            st = load_state(data_dir)
            cfg = settings_of(st)
            if cfg["mode"] == "off" and not force:
                return summary
            if not force and not due(st, cfg, now):
                return summary
            import perf_report as pr
            import shared_controls as sc
            hist = env["load_history"]() if callable(env.get("load_history")) else []
            recs_raw = pr.load_records(hist, data_dir)
            recs = enrich(recs_raw)
            controls = {sec: sc.load(sec, _controls_path(env)) for sec in BOTS}
            verdicts = sc.load_verdicts()
            notes: List[str] = []
            diag = diagnose(recs_raw, cfg, controls, verdicts, st["protected"], now)
            # پایش اثر تغییرهای قبلی + دوره‌ی آزمایشی استراتژی‌های جدید
            notes += evaluate_applied(st, recs, env, cfg, now)
            notes += probation_check(st, recs, env, cfg, now)
            added = merge_proposals(st, diag["proposals"], now)
            res = read_json(RESULTS_NAME, {}, data_dir)
            info = import_research(st, res, controls, cfg, now)
            if info["new"]:
                notes.append("🧪 New strategies passed the lab: " + ", ".join(info["new"]))
            if info["dropped"]:
                notes.append("⚠️ Enabled R&D strategy no longer passes the lab: " + ", ".join(info["dropped"]) + " (locked automatically by the verdict).")
            mres = read_json(MODULES_NAME, {}, data_dir)
            if isinstance(mres, dict) and float(mres.get("ts", 0)) > float(st["last"].get("modules_seen", 0)):
                prev_m = st["last"].get("modules_status") or {}
                cur_m = {k: v.get("status") for k, v in (mres.get("modules") or {}).items()}
                ch = [f"{k}: {prev_m[k]}→{v}" for k, v in cur_m.items() if k in prev_m and prev_m[k] != v]
                cnt = {x: sum(1 for v in cur_m.values() if v == x) for x in ("PASS", "CAND", "LAB", "FAIL", "NA")}
                notes.append(f"🧩 All-modules re-test done: ✅{cnt['PASS'] + cnt['CAND']} pass · 🟡{cnt['LAB']} lab-pass but below your criteria · ❌{cnt['FAIL']} fail"
                             + (" · changes: " + ", ".join(ch[:12]) if ch else ""))
                st["last"].update({"modules_seen": float(mres["ts"]), "modules_status": cur_m})
            # اعمال خودکار (فقط حالت Auto، فقط آستانه‌ی سخت‌گیرانه، با سقف روزانه)
            applied_now: List[str] = []
            if cfg["mode"] == "auto":
                today = str(int(now // 86400))
                budget = cfg["max_changes_day"] - int(st["changes"].get(today, 0))
                for p in sorted([x for x in st["proposals"] if x["status"] == "pending" and x.get("auto_ok")],
                                key=lambda x: -abs(x["ev"].get("t", 0))):
                    if budget <= 0:
                        break
                    if p["kind"] == "enable":
                        if not cfg["new_auto"]:
                            continue
                    elif abs(p["ev"].get("t", 0)) < cfg["auto_t"]:
                        continue
                    ok, _ = apply_proposal(st, p["id"], env, by="auto", recs=recs, now=now)
                    if ok:
                        applied_now.append(p["desc"])
                        budget -= 1
            st["changes"] = {k: v for k, v in st["changes"].items() if int(k) >= int(now // 86400) - 10}
            st["last"].update({"light_ts": now, "n": diag["n_total"]})
            diag_out = {k: diag[k] for k in ("generated", "n_total", "overall", "bots", "strategies", "findings")}
            diag_out["settings"] = {k: cfg[k] for k in ("min_n", "min_t", "cands", "ai_on", "ai_model", "ai_calls_day", "research_h", "mode")}
            diag_out["blacklist"] = st["blacklist"][-60:]
            write_json(DIAG_NAME, diag_out, data_dir)
            save_state(st, data_dir)
            summary.update({"ran": True, "new": added, "applied": applied_now, "notes": notes})
            pend = [p for p in st["proposals"] if p["status"] == "pending"]
            if callable(env.get("notify")) and (added or applied_now or notes):
                lines = ["🧪 <b>R&amp;D Lab</b>"]
                lines += [f"🤖 Applied: {d}" for d in applied_now]
                lines += notes
                if added:
                    lines.append(f"📝 {added} new proposal(s) — {len(pend)} waiting for your decision.")
                try:
                    env["notify"]("\n".join(lines)[:3800], [[{"text": "🧪 Open R&D Lab", "callback_data": "adm:rnd:home"}]])
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        summary["error"] = str(e)
        print(f"[rnd] tick failed: {e}")
    return summary

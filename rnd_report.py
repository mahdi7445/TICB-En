"""گزارش دقیق و توضیح‌دار اقدامات هوش مصنوعی و سیستم تحقیق و توسعه برای ادمین (تلگرام).

فقط متن می‌سازد (بدون I/O)؛ rnd_lab.tick آن را بعد از هر چرخه‌ی پژوهش/بازآزمایی و هر تغییر خودکار می‌فرستد.
همه‌ی متن‌های پویا html.escape می‌شوند؛ هر پیام زیر ۳۸۰۰ نویسه می‌ماند.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

_ST = {"PASS": "🟢 PASS", "CAND": "🟡 کاندید", "FAIL": "🔴 رد", "EMPTY": "⚪ خالی"}
_LIM = 3800


def _e(x: Any) -> str:
    return html.escape(str(x))


def _f(v: Any, fmt: str = "{:.2f}", dash: str = "—") -> str:
    try:
        return fmt.format(float(v))
    except Exception:  # noqa: BLE001
        return dash


def _cut(lines: List[str]) -> List[str]:
    """چند خط → یک یا چند پیام زیر سقف تلگرام."""
    out, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > _LIM and cur:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + ln[:1200]
    if cur:
        out.append(cur)
    return out


def ev_text(ev: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ev, dict) or not ev.get("n"):
        return "—"
    return (f"n={ev.get('n')} · میانگین {_f(ev.get('avg'), '{:+.2f}')}R · t={_f(ev.get('t'), '{:.1f}')} · PF {_f(ev.get('pf'))} · وین‌ریت {_f(ev.get('win'), '{:.0f}')}%")


def explain_applied(p: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """یک تغییرِ اعمال‌شده‌ی خودکار: چه بود، چرا، شواهد، و چطور پایش/برگشت می‌شود."""
    kind = p.get("kind")
    what = {"ctl": "تغییر تنظیم", "rule": "قانون فیلتر جدید", "enable": "فعال‌سازی استراتژی"}.get(kind, "تغییر")
    why = {"stat": "تشخیص آماری روی معاملات بسته‌شده (نشتِ معنادار)", "ai": "ایده‌ی هوش مصنوعی", "research": "استراتژی جدیدِ قبول‌شده در آزمایشگاه"}.get(p.get("src"), "سیستم R&D")
    return (f"🤖 <b>{what} (خودکار)</b>\n{_e(p.get('desc', ''))}\n"
            f"• چرا: {why}\n• شواهد: {ev_text(p.get('ev'))}\n"
            f"• پایش: بعد از {cfg.get('rb_n', '?')} معاملهٔ بعدی اثرش سنجیده می‌شود؛ اگر معنادار بدتر شد خودکار برمی‌گردد. "
            f"با «🛡 Active changes» هم با یک ضربه قابل برگشت است.")


def _ai_lines(res: Dict[str, Any]) -> List[str]:
    ai = res.get("ai") or {}
    prov = ai.get("providers") or {}
    lines = ["🧠 <b>تیم هوش مصنوعی</b>"]
    if not prov:
        lines.append("• در این چرخه هیچ هوش مصنوعی‌ای اجرا نشد" + (f" ({_e(ai.get('error'))})" if ai.get("error") else " (کلید ثبت نشده/همه خاموش/سقف روزانه تمام شد)."))
        return lines
    n_ok = sum(1 for r in prov.values() if isinstance(r, dict) and r.get("used"))
    for pid, r in prov.items():
        if not isinstance(r, dict):
            continue
        if r.get("used"):
            lines.append(f"✅ {_e(pid)}: {r.get('n', 0)} ایده/استراتژی تحویل داد" + (f" (مدل {_e(r.get('model'))})" if r.get("model") else ""))
        elif r.get("error"):
            lines.append(f"⚠️ {_e(pid)}: {_e(str(r['error'])[:160])}")
        else:
            lines.append(f"⏭ {_e(pid)}: {_e(r.get('skipped') or 'اجرا نشد')}")
    if not n_ok:
        lines.append("❗ هیچ هوش مصنوعی‌ای جواب نداد؛ R&D بدون آن‌ها با آزمایشگاه آماری و بهبود خودکار ادامه می‌دهد. در «🧠 AI assistant» → «🔌 Test all» علت را ببینید.")
    an = [str(x) for x in (ai.get("analysis") or [])][:5]
    if an:
        lines.append("<b>تحلیل‌ها:</b>")
        lines += [f"• {_e(x[:260])}" for x in an]
    hints = res.get("ai_hints") or []
    if hints:
        lines.append(f"💡 {len(hints)} پیشنهاد تنظیم از هوش مصنوعی (در «📝 Proposals» منتظر تصمیم شماست؛ هرگز خودکار اعمال نمی‌شود).")
    return lines


def _gate_line(res: Dict[str, Any]) -> str:
    g = res.get("gate")
    if not isinstance(g, dict) or not g:
        return ""
    if g.get("g_on") is False or g.get("on") is False:
        return "🎯 معیار قبولی ادمین: غیرفعال (فقط حکم آزمایشگاه)"
    keys = [("g_win", "win≥{}%"), ("g_pf_min", "PF≥{}"), ("g_pf_max", "PF≤{}"), ("g_rr", "R:R≥{}"), ("g_dd", "DD≤{}%"), ("g_streak", "باخت پیاپی &lt;{}")]
    parts = [fmt.format(g[k]) for k, fmt in keys if k in g]
    return "🎯 معیار قبولی ادمین: " + (" · ".join(parts) if parts else "فعال")


def cycle_reports(res: Dict[str, Any], slots_before: Dict[str, Any], info: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    """گزارش چرخه‌ی پژوهشِ تازه: خلاصه + هوش مصنوعی + هر اسلات + نزدیک‌ترین‌ها + اقدام‌های لازم."""
    if not isinstance(res, dict) or not res.get("slots"):
        return []
    head = [f"🔬 <b>گزارش چرخه‌ی پژوهش #{_e(res.get('cycle', '?'))}</b> — {_e(res.get('generated', ''))}",
            f"• ایده‌ی آزمایش‌شده: {res.get('tested', 0)} (پیش‌غربال {res.get('prescreened', 0)}) · نسخه‌ی اصلاح‌شده‌ی خودکار: {res.get('improved', 0)}"]
    g = _gate_line(res)
    if g:
        head.append(g)
    head += [""] + _ai_lines(res)
    msgs = _cut(head)

    body = ["🧪 <b>وضعیت استراتژی‌های جدید (RD)</b>"]
    for slot, r in (res.get("slots") or {}).items():
        if not isinstance(r, dict):
            continue
        before = (slots_before or {}).get(slot) or {}
        st_ = r.get("status")
        if st_ == "EMPTY":
            body.append(f"⚪ {_e(slot)}: خالی (کاندیدِ قابل‌قبول پیدا نشد)")
            continue
        changed = before.get("spec_id") != r.get("spec_id")
        tag = " 🆕 جایگزین شد" if changed else (" ↔️ وضعیت عوض شد" if before.get("status") != st_ else "")
        gt = r.get("gate") or {}
        fails = [c.get("n") for c in (gt.get("checks") or []) if c.get("ok") is False]
        ln = (f"{_ST.get(st_, st_)} <b>{_e(slot)}</b> «{_e(r.get('name', ''))}» {_e(str(r.get('tf', '')).upper())}{tag}\n"
              f"   n={r.get('n', 0)} · {_f(r.get('avg_r'), '{:+.2f}')}R · PF {_f(r.get('pf'))} · win {_f(r.get('win'), '{:.0f}')}% · t={_f(r.get('t'), '{:.1f}')} · نردبان {_e(r.get('best_ladder') or 'auto')}"
              f" · معیارها {gt.get('passed', '?')}/{gt.get('total', '?')}")
        if fails:
            ln += f"\n   ❌ رد شده در: {_e(', '.join(str(x) for x in fails[:6]))}"
        elif st_ == "FAIL" and r.get("fail_reasons"):
            ln += f"\n   ❌ علت: {_e(', '.join(str(x) for x in r['fail_reasons'][:4]))}"
        body.append(ln)
    near = [a for a in (res.get("archive") or []) if isinstance(a, dict) and a.get("status") not in ("PASS", "CAND")][:3]
    if near:
        body.append("\n🔧 <b>نزدیک‌ترین ناموفق‌ها (والدِ بهبود خودکار)</b>")
        for a in near:
            body.append(f"• {_e(a.get('name') or (a.get('spec') or {}).get('name') or a.get('spec_id'))} — رد در: {_e(', '.join(str(x) for x in (a.get('fails') or [])[:5]) or '—')}")
    msgs += _cut(body)

    act = []
    if info.get("new"):
        act.append("📝 پیشنهاد فعال‌سازی ثبت شد برای: " + _e(", ".join(info["new"])) + " (در «📝 Proposals»؛ در حالت Auto با آستانه‌ی سخت‌گیرانه خودکار اعمال می‌شود).")
    if info.get("dropped"):
        act.append("🔒 قفل خودکار (دیگر معیار را ندارد): " + _e(", ".join(info["dropped"])))
    if act:
        msgs.append("\n".join(["⚙️ <b>اقدام‌ها</b>"] + act))
    return msgs


def modules_report(mres: Dict[str, Any], prev_status: Dict[str, Any], cfg: Dict[str, Any], applied: Optional[Any] = None) -> List[str]:
    """گزارش بازآزمایی ۲۴ساعته‌ی همه‌ی ماژول‌ها: شمارش، تغییر وضعیت‌ها با علت، بدترین‌ها."""
    mods = (mres or {}).get("modules") or {}
    if not mods:
        return []
    cnt = {k: sum(1 for m in mods.values() if m.get("status") == k) for k in ("PASS", "CAND", "LAB", "FAIL", "NA")}
    lines = [f"🧩 <b>گزارش بازآزمایی همه‌ی ماژول‌ها</b> — {_e((mres or {}).get('generated', ''))}",
             f"✅ {cnt['PASS']} کامل · 🟢 {cnt['CAND']} کاندید · 🟡 {cnt['LAB']} (آزمایشگاه PASS ولی زیر معیار شما) · ❌ {cnt['FAIL']} رد · ➖ {cnt['NA']} بدون داده"]
    chg = []
    for k, m in mods.items():
        old = (prev_status or {}).get(k)
        if old and old != m.get("status"):
            gt = m.get("gate") or {}
            fails = [c.get("n") for c in (gt.get("checks") or []) if c.get("ok") is False][:3]
            chg.append(f"• {_e(k)}: {_e(old)} → <b>{_e(m.get('status'))}</b> · نردبان {_e(m.get('best_ladder') or '—')}" + (f" · رد: {_e(', '.join(str(x) for x in fails))}" if fails else ""))
    if chg:
        lines += ["\n<b>تغییر وضعیت</b>"] + chg[:15]
    bad = [(k, m) for k, m in mods.items() if m.get("status") == "FAIL"][:6]
    if bad:
        lines.append("\n<b>ردشده‌ها (در حالت Auto قفل می‌شوند)</b>")
        for k, m in bad:
            gt = m.get("gate") or {}
            fails = [c.get("n") for c in (gt.get("checks") or []) if c.get("ok") is False][:3]
            lines.append(f"• {_e(k)}" + (f" — رد: {_e(', '.join(str(x) for x in fails))}" if fails else (f" — {_e(', '.join(str(x) for x in (m.get('fail_reasons') or [])[:2]))}" if m.get("fail_reasons") else "")))
    if cfg.get("modules_apply") and cfg.get("mode") == "auto":
        lines.append("\n⚙️ اعمال خودکار روشن است: بهترین نردبانِ ماژول‌های ✅/🟢 و قفلِ ❌ها روی هر دو ربات ثبت می‌شود.")
    else:
        lines.append("\nℹ️ اعمال خودکار خاموش/غیر-Auto است؛ در «🧩 All modules» دکمه‌ی «Apply to bots now» را بزنید.")
    return _cut(lines)

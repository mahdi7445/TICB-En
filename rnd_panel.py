# -*- coding: utf-8 -*-
"""
rnd_panel.py — رابط دکمه‌ای «🧪 R&D Lab» (زیرمنوی Admin Panel). فقط UI؛ منطق در rnd_lab / rnd_dsl است.
همه‌ی callbackها «adm:rnd:...» (admin_panel.handle_callback آن‌ها را به handle_callback همین فایل می‌دهد). هیچ‌چیز نیاز به تایپ ندارد.
ctx همان ctx پنل: send, edit, controls_path, load_history, data_dir.
"""
from __future__ import annotations

import html
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import admin_texts as T
import rnd_lab as rl

Rows = List[List[Dict[str, str]]]
HOME = "adm:rnd:home"


def _b(text: str, cb: str) -> Dict[str, str]:
    return {"text": text, "callback_data": cb}


def _nav(back: str = HOME) -> List[Dict[str, str]]:
    return [_b("⬅️ Back", back), _b("🏠 Admin", "adm:home")]


def _fit(body: str, key: str, limit: int = 3990) -> str:
    """متن + «ℹ️ توضیح» فارسیِ همان صفحه؛ اگر طولانی شد «بدنه» کوتاه می‌شود (نه توضیح) تا زیر سقف ۴۰۹۶ نویسه‌ی تلگرام بماند."""
    h = T.block(key)
    room = limit - len(h) - 4
    if len(body) > room:
        body = body[:max(0, room)].rsplit("\n", 1)[0] + "\n…"
    return body + h


def _env(ctx) -> Dict[str, Any]:
    return {"data_dir": ctx.get("data_dir"), "controls_path": ctx.get("controls_path"), "load_history": ctx.get("load_history")}


def _st(ctx) -> Dict[str, Any]:
    return rl.load_state(ctx.get("data_dir"))


def _ago(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    m = int((time.time() - ts) / 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago" if m < 3000 else f"{m // 1440} d ago"


def _fmt_ev(ev: Dict[str, Any]) -> str:
    if not ev or not ev.get("n"):
        return ""
    s = f"n={ev['n']} · avg {ev['avg']:+.2f}R · t={ev['t']:.1f} · PF {ev['pf']:.2f} · win {ev['win']:.0f}%"
    if ev.get("h1") or ev.get("h2"):
        s += f" · halves {ev['h1']:+.2f}/{ev['h2']:+.2f}"
    return s


def _health(diag: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    o = diag.get("overall") or {}
    if not o.get("n"):
        return "No closed trades analysed yet."
    def mark(ok):
        return "✅" if ok else "⚠️"
    l30 = o.get("30d") or {}
    lines = [f"All time: {o['n']} trades · {o['avg']:+.2f}R avg · PF {o['pf']:.2f} {mark(o['pf'] >= cfg['g_pf_min'])} · win {o['win']:.0f}% {mark(o['win'] >= cfg['g_win'])} · max DD {o.get('dd', 0):.1f}R"]
    if l30.get("n"):
        lines.append(f"Last 30d: {l30['n']} trades · {l30['avg']:+.2f}R avg · PF {l30['pf']:.2f} · win {l30['win']:.0f}%")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ صفحات
def home(ctx) -> Tuple[str, Rows]:
    st = _st(ctx)
    cfg = rl.settings_of(st)
    diag = rl.read_json(rl.DIAG_NAME, {}, ctx.get("data_dir"))
    res = rl.read_json(rl.RESULTS_NAME, {}, ctx.get("data_dir"))
    pend = [p for p in st["proposals"] if p["status"] == "pending"]
    act = [e for e in st["applied"] if e.get("state") == "active"]
    slots = (res or {}).get("slots") or {}
    ns = {k: sum(1 for x in slots.values() if x.get("status") == k) for k in ("PASS", "CAND", "FAIL")}
    txt = ("🧪 <b>R&amp;D Lab</b> — finds why trades lose, tests fixes and new strategies, and keeps improving win rate and R:R.\n\n"
           f"Mode: <b>{rl.MODE_TXT[cfg['mode']]}</b>\n"
           f"Last diagnosis: {_ago(st['last'].get('light_ts'))} · deep research: {_ago((res or {}).get('ts'))}\n"
           f"New strategies in lab: 🟢{ns['PASS']} PASS · 🟡{ns['CAND']} candidate · 🔴{ns['FAIL']} failed\n"
           f"Proposals waiting: <b>{len(pend)}</b> · active changes: {len(act)}\n\n" + _health(diag, cfg))
    rows: Rows = [
        [_b(f"📝 Proposals ({len(pend)})", "adm:rnd:pl:0"), _b("🩺 Findings", "adm:rnd:fd")],
        [_b("🧪 New strategies", "adm:rnd:sl"), _b("📊 Scorecard", "adm:rnd:sc")],
        [_b("🛡 Active changes & rules", "adm:rnd:ch"), _b("📜 Journal", "adm:rnd:jr")],
        [_b("▶️ Diagnose now", "adm:rnd:run"), _b("🔬 Deep research now", "adm:rnd:res")],
        [_b("🎚 Mode", "adm:rnd:mode"), _b("🧠 AI assistant", "adm:rnd:ai")],
        [_b("🧩 All modules", "adm:rnd:mod"), _b("🛰 Toobit watch", "adm:rnd:wt")],
        [_b("⬅️ Admin Panel", "adm:home"), _b("⚙️ Settings", "adm:rnd:set")],      # جای Settings و Admin Panel طبق درخواست عوض شد
    ]
    return _fit(txt, "rnd_home"), rows


def mode_page(ctx):
    cfg = rl.settings_of(_st(ctx))
    txt = ("🎚 <b>Mode</b>\n\n⛔ <b>Off</b> — nothing runs.\n👁 <b>Advisor</b> — analyses and reports only; never changes anything.\n"
           "✋ <b>Semi-auto</b> — makes proposals; a change is applied only after you tap Approve.\n"
           "🤖 <b>Auto</b> — applies changes that meet the strict evidence bar, within the daily limit, and rolls them back if results get worse.\n\n"
           f"Now: <b>{rl.MODE_TXT[cfg['mode']]}</b>")
    rows = [[_b(("✅ " if cfg["mode"] == m else "") + rl.MODE_TXT[m], f"adm:rnd:m:{m}")] for m in ("off", "advisor", "semi", "auto")]
    rows.append(_nav())
    return _fit(txt, "rnd_mode"), rows


def proposals_page(ctx, page: int = 0):
    st = _st(ctx)
    pend = sorted([p for p in st["proposals"] if p["status"] == "pending"], key=lambda p: -abs(p["ev"].get("t", 0)))
    per = 6
    page = max(0, min(page, max(0, (len(pend) - 1) // per)))
    lines = [f"📝 <b>Proposals</b> ({len(pend)})\nTap one to see the evidence and approve or reject."]
    rows: Rows = []
    for p in pend[page * per:(page + 1) * per]:
        icon = {"stat": "📈", "ai": "🧠", "research": "🧪"}.get(p.get("src"), "•")
        rows.append([_b(f"{icon} {p['desc'][:52]}", f"adm:rnd:pv:{p['id']}")])
    if not pend:
        lines.append("\nNothing to decide right now. 👍")
    nav = []
    if page > 0:
        nav.append(_b("◀️", f"adm:rnd:pl:{page - 1}"))
    if (page + 1) * per < len(pend):
        nav.append(_b("▶️", f"adm:rnd:pl:{page + 1}"))
    if nav:
        rows.append(nav)
    if pend:
        rows.append([_b("✅ Approve all…", "adm:rnd:aa"), _b("🗑 Reject all…", "adm:rnd:ra")])
    rows.append(_nav())
    return _fit("\n".join(lines), "rnd_proposals"), rows


def proposal_page(ctx, pid: str):
    st = _st(ctx)
    p = next((x for x in st["proposals"] if x["id"] == pid), None)
    if not p:
        return "Proposal not found.", [_nav("adm:rnd:pl:0")]
    # 🔴 رفعِ «R&D Lab error: 'int' object is not subscriptable»: قبلاً هر سه متن (ctl / rule / enable) هم‌زمان ساخته می‌شد و برای
    # پیشنهادِ تنظیم با مقدار عددی، p["value"]["sections"] (مخصوص kind=enable) خطا می‌داد. حالا فقط متنِ همان نوع ساخته می‌شود.
    kind = p.get("kind")
    if kind == "ctl":
        what = f"Setting <code>{html.escape(str(p.get('key')))}</code> on <b>{html.escape(str(p.get('section') or '').upper())}</b> → <b>{html.escape(str(p.get('value')))}</b>"
    elif kind == "rule":
        what = f"New rule: <code>{html.escape(json.dumps(p.get('rule'), ensure_ascii=False, default=str)[:300])}</code>"
    elif kind == "enable":
        v = p.get("value")
        secs = v.get("sections") if isinstance(v, dict) else None
        what = f"Turn ON <b>{html.escape(str(p.get('key')))}</b> on {', '.join(str(x).upper() for x in (secs or []))} (probation cap applies)"
    else:
        what = ""
    txt = (f"{html.escape(str(p.get('desc', '')))}\n\n{what}\n\nEvidence: {_fmt_ev(p['ev']) or 'n/a'}\nSource: {p.get('src')}"
           + ("\n\n<i>Every applied change can be undone, and is checked automatically after the next trades.</i>"))
    rows = [[_b("✅ Approve", f"adm:rnd:ap:{pid}"), _b("✖️ Reject", f"adm:rnd:rj:{pid}")], _nav("adm:rnd:pl:0")]
    return _fit(txt, "rnd_proposal"), rows


def findings_page(ctx):
    diag = rl.read_json(rl.DIAG_NAME, {}, ctx.get("data_dir"))
    fs = diag.get("findings") or []
    lines = [f"🩺 <b>Findings</b> — statistically significant leaks (from {diag.get('n_total', 0)} closed trades, {diag.get('generated', 'not yet run')})"]
    if not fs:
        lines.append("\nNo significant leak detected with the current evidence thresholds. Keep collecting trades or lower “Evidence to PROPOSE” in Settings.")
    for f in fs[:12]:
        lines.append(f"\n{'🔴' if f['sev'] == 'high' else '🟠' if f['sev'] == 'med' else '🟡'} {f['text']}")
    return _fit("\n".join(lines)[:3000], "rnd_findings"), [[_b("▶️ Diagnose now", "adm:rnd:run")], _nav()]


def scorecard_page(ctx):
    diag = rl.read_json(rl.DIAG_NAME, {}, ctx.get("data_dir"))
    cfg = rl.settings_of(_st(ctx))
    lines = ["📊 <b>Scorecard</b> (net R after fees)\n", _health(diag, cfg)]
    for b, s in (diag.get("bots") or {}).items():
        if s.get("n"):
            lines.append(f"{b.upper()}: {s['n']} trades · {s['avg']:+.2f}R · PF {s['pf']:.2f} · win {s['win']:.0f}%")
    rows_ = [r for r in (diag.get("strategies") or []) if r["n"] >= 5]
    if rows_:
        lines.append("\n<b>Worst</b>")
        for r in rows_[:5]:
            lines.append(f"{r['bot'].upper()} {r['s']} {r['tf']}: {r['sum']:+.1f}R · n={r['n']} · win {r['win']:.0f}%")
        lines.append("\n<b>Best</b>")
        for r in sorted(rows_, key=lambda x: -x["sum"])[:5]:
            lines.append(f"{r['bot'].upper()} {r['s']} {r['tf']}: {r['sum']:+.1f}R · n={r['n']} · win {r['win']:.0f}%")
    return _fit("\n".join(lines)[:3000], "rnd_scorecard"), [_nav()]


def strategies_page(ctx):
    import shared_controls as sc
    res = rl.read_json(rl.RESULTS_NAME, {}, ctx.get("data_dir"))
    st = _st(ctx)
    slots = (res or {}).get("slots") or {}
    lines = ["🧪 <b>New strategies</b> invented and tested by the lab (6 slots).",
             f"Last deep research: {_ago((res or {}).get('ts'))} · tested {(res or {}).get('tested', 0)} ideas",
             "🟢 PASS · 🟡 candidate · 🔴 failed the lab (locked) · ⚪ empty. Only PASS/candidate can ever trade."]
    rows: Rows = []
    try:
        import rnd_library as lib_
        lib = lib_.load(ctx.get("data_dir"))
        c = lib_.counts(lib, _on_ids(ctx))
        lines.append(f"\n📚 <b>Library</b> (every idea ever found, no limit): <b>{c['all']}</b> · 🟢{c['pass']} 🟡{c['cand']} 🔴{c['fail']} ⚪{c['scr']} · ON now: {c['on']}")
        rows.append([_b(f"📚 Library ({c['all']})", "adm:rnd:lb:all:0"), _b("📈 Insights", "adm:rnd:ins")])
    except Exception as e:  # noqa: BLE001
        print(f"[rnd_panel] library summary failed: {e}")
    for s in __import__("rnd_dsl").SLOTS:
        r = slots.get(s) or {"status": "EMPTY"}
        on = [b.upper() for b in rl.BOTS if sc.load(b, ctx.get("controls_path")).get(f"mod_{s}")]
        ic = {"PASS": "🟢", "CAND": "🟡", "FAIL": "🔴"}.get(r["status"], "⚪")
        label = f"{ic} {s} · {r.get('name') or 'empty'}" + (f" · ON {'/'.join(on)}" if on else "")
        rows.append([_b(label[:60], f"adm:rnd:sd:{s}")])
    rows.append(_nav())
    return _fit("\n".join(lines), "rnd_strategies"), rows


def slot_page(ctx, slot: str):
    import shared_controls as sc
    import rnd_dsl
    res = rl.read_json(rl.RESULTS_NAME, {}, ctx.get("data_dir"))
    r = ((res or {}).get("slots") or {}).get(slot) or {"status": "EMPTY"}
    spec = rl.read_json(rl.STRATS_NAME, {}, ctx.get("data_dir")).get("slots", {}).get(slot, {}).get("spec")
    lines = [f"🧪 <b>{slot}</b> — {r.get('name') or 'empty'} ({r['status']})"]
    if spec:
        lines.append("\n" + rnd_dsl.describe(spec))
        if spec.get("why"):
            lines.append(f"Idea: {spec['why']}")
        lines.append(f"Origin: {spec.get('origin', '?')}")
    if r.get("avg_r") is not None:
        lines.append(f"\nLab ({str(r.get('tf')).upper()}): n={r.get('n')} · {r['avg_r']:+.2f}R · PF {r.get('pf', 0):.2f} · win {r.get('win', 0):.0f}% · t={r.get('t', 0):.1f}\n"
                     f"train {r.get('train_r', 0):+.2f}R / test {r.get('test_r', 0):+.2f}R · best ladder {r.get('best_ladder') or 'auto'}")
        gt = r.get("gate") or {}
        if gt.get("checks"):
            lines.append(f"\n🎯 <b>Pass criteria</b> ({gt.get('passed')}/{gt.get('total')} met, on ladder {r.get('best_ladder')})")
            for c in gt["checks"]:
                mark = {True: "✅", False: "❌", None: "➖"}[c.get("ok")]
                lines.append(f"{mark} {c['n']}: {'n/a' if c.get('v') is None else c['v']} (need {c['lim']})")
        elif r["status"] == "FAIL" and r.get("fail_reasons"):
            lines.append(f"Did not pass: {', '.join(str(x) for x in r['fail_reasons'])}")
    rows: Rows = []
    for b in rl.BOTS:
        on = bool(sc.load(b, ctx.get("controls_path")).get(f"mod_{slot}"))
        rows.append([_b(f"{'✅' if on else '⬜'} {b.upper()} {'ON' if on else 'OFF'}", f"adm:rnd:st:{slot}:{b}")])
    rows.append([_b("⚙️ Strategy settings (ladder, caps…)", f"adm:ticb:ms:{slot}")])
    rows.append(_nav("adm:rnd:sl"))
    return _fit("\n".join(lines)[:2900], "rnd_slot"), rows



# ------------------------------------------------------------------------------------------ 📚 کتابخانه‌ی استراتژی‌ها
def _on_ids(ctx) -> set:
    """spec_id استراتژی‌هایی که همین الان روی حداقل یک ربات روشن‌اند (جایگاه فعلی ← controls)."""
    import shared_controls as sc
    out = set()
    try:
        slots = rl.read_json(rl.STRATS_NAME, {}, ctx.get("data_dir")).get("slots", {})
        for slot, rec in (slots or {}).items():
            if any(sc.load(b, ctx.get("controls_path")).get(f"mod_{slot}") for b in rl.BOTS) and rec.get("spec_id"):
                out.add(rec["spec_id"])
    except Exception:  # noqa: BLE001
        pass
    return out


_ST_IC = {"PASS": "🟢", "CAND": "🟡", "FAIL": "🔴", "SCREENED": "⚪"}
_FLT = (("all", "All"), ("pass", "🟢"), ("cand", "🟡"), ("fail", "🔴"), ("scr", "⚪"), ("on", "ON"))
LIB_PAGE = 8


def _m(t: Dict[str, Any]) -> str:
    if not t:
        return "no lab test yet"
    return f"{t.get('avg', 0) or 0:+.2f}R · PF {t.get('pf', 0) or 0:.2f} · win {t.get('win', 0) or 0:.0f}% · t={t.get('t', 0) or 0:.1f} · n={t.get('n', 0) or 0}"


def library_page(ctx, flt: str = "all", page: int = 0):
    import rnd_library as lib_
    lib = lib_.load(ctx.get("data_dir"))
    on = _on_ids(ctx)
    c = lib_.counts(lib, on)
    flt = flt if flt in dict(_FLT) else "all"
    items = lib_.sorted_items(lib, flt, on)
    pages = max(1, (len(items) + LIB_PAGE - 1) // LIB_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * LIB_PAGE:(page + 1) * LIB_PAGE]
    lines = [f"📚 <b>Strategy library</b> — {c['all']} ideas on record (no limit)",
             f"🟢 PASS {c['pass']} · 🟡 candidate {c['cand']} · 🔴 failed {c['fail']} · ⚪ screened {c['scr']} · ON now {c['on']}",
             f"Filter: <b>{dict(_FLT)[flt]}</b> · {len(items)} shown · page {page + 1}/{pages}", ""]
    rows: Rows = [[_b(("• " if k == flt else "") + f"{lab} {c[k]}", f"adm:rnd:lb:{k}:0") for k, lab in _FLT[:3]],
                  [_b(("• " if k == flt else "") + f"{lab} {c[k]}", f"adm:rnd:lb:{k}:0") for k, lab in _FLT[3:]]]
    for e in chunk:
        ev = lib_.evaluate(e)
        t = lib_.last_test(e) or (e.get("scr") or {})
        st_ = lib_.status_of(e)
        o = lib_.ORIGIN_FA.get(lib_.origin_group(e.get("origin")), str(e.get("origin") or "?"))
        pv = lib_.provider_of(e.get("origin"))
        lines.append(f"{_ST_IC.get(st_, '⚪')}{ev['grade']} <b>{html.escape(str(e.get('name') or e['id']))}</b> · {str(t.get('tf') or '?').upper()} · {_m(lib_.last_test(e)) if lib_.last_test(e) else 'train t=' + str(t.get('t'))} · {o}{' ' + pv if pv else ''}{' · 🔛 ON' if e['id'] in on else ''}")
        rows.append([_b(f"{_ST_IC.get(st_, '⚪')}{ev['grade']} {str(e.get('name') or e['id'])[:26]} {((lib_.last_test(e) or {}).get('avg') or 0):+.2f}R"[:60], f"adm:rnd:ld:{e['id']}:{flt}:{page}")])
    if not chunk:
        lines.append("Nothing here yet — the lab adds ideas after every research cycle.")
    nav = []
    if page > 0:
        nav.append(_b("◀️ Prev", f"adm:rnd:lb:{flt}:{page - 1}"))
    if page < pages - 1:
        nav.append(_b("Next ▶️", f"adm:rnd:lb:{flt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_b("📈 Insights", "adm:rnd:ins"), _b("⬅️ Back", "adm:rnd:sl")])
    return _fit("\n".join(lines)[:3000], "rnd_library"), rows


def lib_detail(ctx, sid: str, flt: str = "all", page: int = 0):
    import rnd_library as lib_
    import rnd_dsl
    lib = lib_.load(ctx.get("data_dir"))
    e = (lib.get("items") or {}).get(sid)
    back = f"adm:rnd:lb:{flt}:{page}"
    if not e:
        return "Strategy not found in the library.", [[_b("⬅️ Back", back)]]
    on = _on_ids(ctx)
    ev = lib_.evaluate(e)
    st_ = lib_.status_of(e)
    lines = [f"{_ST_IC.get(st_, '⚪')} <b>{html.escape(str(e.get('name') or sid))}</b> — {st_} · grade <b>{ev['grade']}</b> · score {ev['score']}",
             f"id <code>{sid}</code> · {html.escape(str(e.get('family') or '?'))} · from <b>{html.escape(str(e.get('origin') or '?'))}</b>" + (f" · derived from <code>{e['parent']}</code>" if e.get("parent") else ""),
             f"First seen {e.get('first')} · last {e.get('last')} · cycles {e.get('c0')}–{e.get('c1')} · generated {e.get('seen', 0)}×"]
    if e["id"] in on:
        lines.append("🔛 <b>Trading live now</b> on at least one bot")
    spec = e.get("spec")
    if spec:
        lines.append("\n" + html.escape(rnd_dsl.describe(spec)))
        if spec.get("why"):
            lines.append("Idea: " + html.escape(str(spec["why"])[:160]))
    t = lib_.last_test(e)
    if t:
        lines.append(f"\n<b>Latest lab result</b> ({str(t.get('tf') or '').upper()}, ladder {t.get('lad') or 'auto'}, cycle {t.get('c')})\n{_m(t)}\ntrain {t.get('tr') or 0:+.2f}R / test {t.get('te') or 0:+.2f}R"
                     + (f" · DD {t['dd']}%" if t.get("dd") is not None else "") + (f" · streak {t['stk']}" if t.get("stk") is not None else "") + f" · criteria {t.get('gp')}/{t.get('gt')}")
        hist = e.get("tests") or []
        if len(hist) > 1:
            lines.append("History: " + " → ".join(f"{_ST_IC.get(x.get('st'), '⚪')}{(x.get('avg') or 0):+.2f}" for x in hist[-8:]))
    elif e.get("scr"):
        lines.append(f"\nTrain screen: t={e['scr'].get('t')} · n={e['scr'].get('n')} · {str(e['scr'].get('tf') or '').upper()} · avg {e['scr'].get('avg')}")
    lines.append("\n<b>Evaluation</b>: " + html.escape(ev["verdict"]))
    lines += [f"✅ {html.escape(x)}" for x in ev["strengths"][:4]]
    lines += [f"⚠️ {html.escape(x)}" for x in ev["weaknesses"][:5]]
    acts = [a for a in _st(ctx).get("applied", []) if a.get("kind") == "enable" and a.get("key") in (e.get("slots") or []) and a.get("t", 0) >= _ts_of(e.get("first"))][-4:]
    for a in acts:
        lines.append(f"🔌 {rl.now_iso(a['t'])} {'auto' if a.get('by') == 'auto' else 'you'}: {a.get('state')} — {html.escape(str(a.get('desc'))[:90])}")
    if e.get("ev"):
        lines.append("\n<b>Timeline</b>")
        lines += [f"• {d} {html.escape(str(x))}" for d, x in e["ev"][-7:]]
    rows: Rows = []
    cur = next((sl for sl in (e.get("slots") or []) if (rl.read_json(rl.STRATS_NAME, {}, ctx.get("data_dir")).get("slots", {}).get(sl) or {}).get("spec_id") == sid), None)
    if cur:
        rows.append([_b(f"🎛 Open slot {cur} (ON/OFF, settings)", f"adm:rnd:sd:{cur}")])
    rows.append([_b("⬅️ Library", back), _b("🏠 R&D", HOME)])
    return _fit("\n".join(lines)[:3300], "rnd_lib_detail"), rows


def _ts_of(iso: Any) -> float:
    try:
        return time.mktime(time.strptime(str(iso), "%Y-%m-%d %H:%M")) - time.timezone
    except Exception:  # noqa: BLE001
        return 0.0


def insights_page(ctx):
    import rnd_library as lib_
    lib = lib_.load(ctx.get("data_dir"))
    ins = lib_.insights(lib)
    lines = [f"📈 <b>Library insights</b> — {ins['n_lab']} lab-tested strategies (success = PASS or candidate)"]

    def block(title: str, d: Dict[str, Any], top: int = 7) -> None:
        if not d:
            return
        lines.append(f"\n<b>{title}</b>")
        for k, b in sorted(d.items(), key=lambda kv: (-kv[1]["rate"], -kv[1]["n"]))[:top]:
            lines.append(f"• {html.escape(str(k))}: {b['pass']}🟢 {b['cand']}🟡 of {b['n']} ({b['rate']}%) · avg {b['avg']:+.2f}R")
    block("By source", ins["by_origin"])
    block("By AI provider", ins["by_ai"])
    block("By family", ins["by_family"])
    block("By timeframe", ins["by_tf"])
    if ins["ladders"]:
        lines.append("\n<b>Ladders of qualified strategies</b>: " + ", ".join(f"{k}×{v}" for k, v in sorted(ins["ladders"].items(), key=lambda kv: -kv[1])[:6]))
    if ins["n_lab"] == 0:
        lines.append("\nNo lab results yet.")
    return _fit("\n".join(lines)[:3300], "rnd_insights"), [[_b("📚 Library", "adm:rnd:lb:all:0"), _b("⬅️ Back", "adm:rnd:sl")]]


_ST_TAG = {"active": "👁 watching", "confirmed": "✔️ confirmed", "undone": "↩️ undone", "rolled": "↩️ rolled back"}
_KIND_IC = {"apply": "✅", "undo": "↩️", "confirm": "✔️", "reject": "✖️", "guard": "🛡", "research": "🔬", "audit": "🔍", "strategy": "🧪", "retire": "🛑", "mode": "🎚",
            "settings": "⚙️", "manual": "✋", "diag": "🩺", "propose": "📝"}
CH_PAGE = 4
TL_PAGE = 9


def _is_ai(e: Dict[str, Any]) -> bool:
    d = str(e.get("desc") or e.get("x") or "")
    return e.get("src") == "ai" or "AI idea" in d or "🧠" in d or "AI" in str(e.get("k") or "") or e.get("k") in ("research", "audit")


def _guard_lines(st: Dict[str, Any]) -> List[str]:
    g = (st.get("last") or {}).get("guard") or {}
    out = []
    for scope in ("all",) + tuple(rl.BOTS):
        x = g.get(scope)
        if x:
            out.append(f"{'⚠️' if x.get('bad') else '✅'} {scope.upper()}: last {x['n']} trades {x['avg']:+.2f}R (PF {x['pf']}) vs {x['base_avg']:+.2f}R (PF {x['base_pf']}) before")
    return out or ["Waiting for enough closed trades to compare (needs ≈50)."]


def _effect(e: Dict[str, Any]) -> str:
    b, c = e.get("base") or {}, e.get("checked")
    s = f"before: {b.get('avg', 0):+.2f}R over {b.get('n', 0)} trades" if b.get("n") else "no baseline yet"
    if c:
        s += f" → after: {c['avg']:+.2f}R over {c['n']}/{e.get('rb_n')} trades"
    return s


def changes_page(ctx, page: int = 0):
    st = _st(ctx)
    allc = st["applied"]
    live = [e for e in allc if e.get("state") in ("active", "confirmed")]
    n_ai = sum(1 for e in allc if _is_ai(e))
    n_un = sum(1 for e in allc if e.get("state") == "undone")
    rules = rl._active_rules(ctx.get("data_dir")).get("rules", [])
    live = list(reversed(live))
    pages = max(1, (len(live) + CH_PAGE - 1) // CH_PAGE)
    page = max(0, min(page, pages - 1))
    lines = ["🛡 <b>Active changes &amp; rules</b>",
             f"Total actions on record: {len(allc)} · live {len(live)} · undone/rolled back {n_un} · from AI {n_ai}",
             "<b>Performance guard</b> (checked every cycle)"] + _guard_lines(st)
    rows: Rows = []
    for e in live[page * CH_PAGE:(page + 1) * CH_PAGE]:
        chk = e.get("checked")
        tag = "✔️ confirmed" if e.get("state") == "confirmed" else (f"checking {chk['n']}/{e.get('rb_n')}" if chk else "watching")
        lines.append(f"\n• {html.escape(e['desc'][:130])} <i>({tag}, {'auto' if e.get('by') == 'auto' else 'you'}, {rl.now_iso(e.get('t'))})</i>\n   {_effect(e)}")
        rows.append([_b(f"🔎 Details: {e['desc'][:30]}", f"adm:rnd:cd:{e['id']}"), _b("↩️ Undo", f"adm:rnd:un:{e['id']}")])
    if page == 0:
        for r in rules:
            lines.append(f"\n🚫 Rule {r.get('id')}: {r.get('scope')} {r.get('tf') or ''} {r.get('side') or ''} {('UTC ' + str(r['hours'][0]) + '–' + str(r['hours'][-1] + 1) + 'h') if r.get('hours') else ''}")
            rows.append([_b(f"❌ Remove rule {r.get('id')}", f"adm:rnd:rr:{r.get('id')}")])
    if not live and not rules:
        lines.append("\nNothing active yet.")
    nav = []
    if page > 0:
        nav.append(_b("◀️ Prev", f"adm:rnd:ch:{page - 1}"))
    if page < pages - 1:
        nav.append(_b("Next ▶️", f"adm:rnd:ch:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_b("📖 Full AI & R&D timeline", "adm:rnd:tl:all:0"), _b("🛡 Guard", "adm:rnd:gd")])
    rows.append(_nav())
    return _fit("\n".join(lines)[:3400], "rnd_changes"), rows


def change_detail(ctx, cid: str):
    st = _st(ctx)
    e = next((x for x in reversed(st["applied"]) if x.get("id") == cid), None)
    if not e:
        return "Change not found.", [[_b("⬅️ Back", "adm:rnd:ch:0")]]
    p = next((x for x in st["proposals"] if x.get("id") == cid), {}) or {}
    lines = [f"🔎 <b>{html.escape(str(e.get('desc')))}</b>",
             f"State: <b>{_ST_TAG.get(e.get('state'), e.get('state'))}</b> · applied {rl.now_iso(e.get('t'))} by {'🤖 auto' if e.get('by') == 'auto' else '✋ you'} · source: {html.escape(str(e.get('src') or p.get('src') or '?'))}"]
    if e.get("kind") == "ctl":
        lines.append(f"Setting: {e.get('section', '').upper()} · <code>{html.escape(str(e.get('key')))}</code>: {html.escape(str(e.get('old')))} → <b>{html.escape(str(e.get('new')))}</b>")
    elif e.get("kind") == "rule":
        lines.append(f"Rule: <code>{html.escape(json.dumps(e.get('rule'), ensure_ascii=False, default=str)[:200])}</code>")
    else:
        lines.append(f"Enabled strategy {html.escape(str(e.get('key')))} on {', '.join(str(x).upper() for x in (e.get('old') or {}))}")
    ev = e.get("ev") or p.get("ev") or {}
    if ev and ev.get("n"):
        lines.append(f"Evidence when proposed: {_fmt_ev(ev)}")
    lines.append(f"Effect: {_effect(e)}")
    if e.get("confirmed_at"):
        lines.append(f"✔️ Confirmed {rl.now_iso(e['confirmed_at'])}")
    if e.get("undone_at"):
        lines.append(f"↩️ Undone {rl.now_iso(e['undone_at'])} — {html.escape(str(e.get('undo_reason') or ''))}")
    rel = [j for j in st["journal"] if cid in str(j.get("x", "")) or str(e.get("desc", ""))[:40] in str(j.get("x", ""))][-5:]
    for j in rel:
        lines.append(f"• {rl.now_iso(j['t'])} {html.escape(str(j['x'])[:110])}")
    rows: Rows = []
    if e.get("state") == "active":
        rows.append([_b("↩️ Undo this change", f"adm:rnd:un:{cid}")])
    rows.append([_b("⬅️ Changes", "adm:rnd:ch:0"), _b("📖 Timeline", "adm:rnd:tl:all:0")])
    return _fit("\n".join(lines)[:3300], "rnd_change_detail"), rows


def timeline_page(ctx, flt: str = "all", page: int = 0):
    st = _st(ctx)
    items = list(reversed(st["journal"]))
    if flt == "ai":
        items = [j for j in items if _is_ai(j)]
    elif flt == "guard":
        items = [j for j in items if j.get("k") in ("guard", "undo", "retire", "confirm")]
    elif flt == "strat":
        items = [j for j in items if j.get("k") in ("strategy", "retire", "apply") and ("strategy" in str(j.get("x", "")).lower() or j.get("k") == "strategy")]
    pages = max(1, (len(items) + TL_PAGE - 1) // TL_PAGE)
    page = max(0, min(page, pages - 1))
    names = {"all": "All", "ai": "🧠 AI", "guard": "🛡 Guard", "strat": "🧪 Strategies"}
    lines = [f"📖 <b>Full timeline</b> — {len(items)} records (newest first) · page {page + 1}/{pages}"]
    for j in items[page * TL_PAGE:(page + 1) * TL_PAGE]:
        lines.append(f"\n{rl.now_iso(j['t'])} {_KIND_IC.get(j.get('k'), '•')} {html.escape(str(j['x'])[:170])}")
    if not items:
        lines.append("\nEmpty.")
    rows: Rows = [[_b(("• " if k == flt else "") + v, f"adm:rnd:tl:{k}:0") for k, v in names.items()]]
    nav = []
    if page > 0:
        nav.append(_b("◀️ Newer", f"adm:rnd:tl:{flt}:{page - 1}"))
    if page < pages - 1:
        nav.append(_b("Older ▶️", f"adm:rnd:tl:{flt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_b("⬅️ Changes", "adm:rnd:ch:0"), _b("🏠 R&D", HOME)])
    return _fit("\n".join(lines)[:3400], "rnd_timeline"), rows


def guard_page(ctx, note: str = ""):
    st = _st(ctx)
    cfg = rl.settings_of(st)
    lines = ["🛡 <b>Performance guard</b>", "Compares the latest closed trades with the trades before them (average R and profit factor) for ALL, TICB and SGNL, every R&amp;D cycle."]
    lines += _guard_lines(st)
    g = (st.get("last") or {}).get("guard") or {}
    if g.get("ts"):
        lines.append(f"Last check: {_ago(g['ts'])}")
    lines.append(f"\nAuto-rollback: <b>{'ON' if cfg['rollback'] else 'OFF'}</b> — when a drop is detected the guard rolls back the newest <i>automatic</i> change made around that time (max one per {rl.GUARD_UNDO_GAP_H}h); changes you made by hand only raise an alert.")
    if note:
        lines.append("\n" + note)
    return _fit("\n".join(lines)[:3000], "rnd_guard"), [[_b("▶️ Check now", "adm:rnd:gdr")], [_b("⬅️ Changes", "adm:rnd:ch:0"), _b("🏠 R&D", HOME)]]


def journal_page(ctx):
    st = _st(ctx)
    lines = ["📜 <b>Journal</b> (latest first)"]
    for j in reversed(st["journal"][-40:]):
        lines.append(f"\n{rl.now_iso(j['t'])} — {j['x']}")
    if len(lines) == 1:
        lines.append("\nEmpty.")
    return _fit("\n".join(lines)[:3300], "rnd_journal"), [[_b("📖 Full timeline", "adm:rnd:tl:all:0")], _nav()]


def _fmt(v: Any) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _ordered(keys: List[str]) -> List[str]:
    """اعدادِ ➖/➕ اول، گزینه‌های تیک‌خور (روشن/خاموش) آخر؛ ترتیب نسبیِ هر دسته حفظ می‌شود. توضیح‌ها و دکمه‌ها هر دو از همین ترتیب می‌آیند."""
    return [k for k in keys if rl.SCHEMA[k][0] != "bool"] + [k for k in keys if rl.SCHEMA[k][0] == "bool"]


def _setting_rows(keys: List[str], cfg: Dict[str, Any], back: str) -> Rows:
    """دکمه‌ی وسطِ هر عدد = «مقدار + واحد · نام کوتاه» (مقدار همیشه دیده می‌شود)؛ لمس آن = تایپ عدد دقیق."""
    rows: Rows = []
    for k in _ordered(keys):
        typ = rl.SCHEMA[k][0]
        if typ == "bool":
            rows.append([_b(f"{'✅' if cfg[k] else '⬜'} {rl.LABEL[k]}"[:60], f"adm:rnd:tg:{k}")])
        elif typ == "choice":
            rows.append([_b(f"🔁 {rl.LABEL[k]}: {cfg[k]}"[:60], f"adm:rnd:cy:{k}")])
        else:
            rows.append([_b("➖", f"adm:rnd:sn:{k}:-1"),
                         _b(f"{_fmt(cfg[k])}{rl.UNIT.get(k, '')} · {rl.SHORT.get(k, rl.LABEL[k])}"[:30], f"adm:rnd:ty:{k}"),
                         _b("➕", f"adm:rnd:sn:{k}:1")])
    rows.append(_nav(back))
    return rows


GROUPS = {
    "gt": ("🎯 Pass criteria", rl.GATE_KEYS),
    "ev": ("🔬 Evidence & safety", ["min_n", "min_t", "auto_t", "max_changes_day", "rollback", "rb_n"]),
    "rs": ("🔬 Research", ["nonstop", "light_h", "research_h", "cands", "improve", "improve_n", "probation_day", "new_auto"]),
    "ac": ("🧰 What the lab may change", ["a_off", "a_block", "a_stop", "a_caps", "a_ladder", "a_new"]),
}


def settings_page(ctx):
    rows = [[_b(t, f"adm:rnd:sg:{g}")] for g, (t, _) in GROUPS.items()]
    rows.append(_nav())
    return _fit("⚙️ <b>R&amp;D settings</b> — all with buttons.", "rnd_settings"), rows


def group_page(ctx, g: str, note: str = ""):
    cfg = rl.settings_of(_st(ctx))
    t, keys = GROUPS[g]
    lines = [f"<b>{t}</b>", "➖/➕ = یک گام کم/زیاد · لمس عدد = تایپ مقدار دقیق · ♻️ = برگشت به پیش‌فرضِ بهینه\n"]
    if g == "gt":
        lines.append("استراتژی جدید فقط وقتی به ربات‌ها اضافه می‌شود که روی نردبانِ مخصوص خودش، پس از کسر کارمزد، <b>همه</b> این شرایط را داشته باشد. "
                     "اگر نداشت، سامانه به‌طور خودکار نسخه‌ی اصلاح‌شده می‌سازد و دوباره می‌آزماید.\n")
    for k in _ordered(keys):
        v = cfg[k]
        sv = ("✅" if v else "⬜") if rl.SCHEMA[k][0] == "bool" else f"<b>{_fmt(v)}{rl.UNIT.get(k, '')}</b>"
        lines.append(f"• {rl.LABEL[k]}: {sv}\n  <i>{rl.DESC_FA.get(k, '')}</i>")
    if g == "gt" and not cfg["g_on"]:
        lines.append("\n⏸ <b>شرایط پاس‌شدن غیرفعال است</b> — فقط حکم داخلی آزمایشگاه ملاک است.")
    if g == "gt":
        for w in rl.gate_conflicts(rl.gate_of(cfg)):
            lines.append("\n" + w)
    if note:
        lines.append("\n" + note)
    rows = _setting_rows(keys, cfg, "adm:rnd:set")
    rows.insert(len(rows) - 1, [_b("♻️ Reset this page to best defaults", f"adm:rnd:grs:{g}")])
    return _fit("\n".join(lines)[:3300], "rnd_sg_" + g), rows


def ai_page(ctx, test_msg: str = ""):
    import rnd_ai
    st = _st(ctx)
    cfg = rl.settings_of(st)
    res = rl.read_json(rl.RESULTS_NAME, {}, ctx.get("data_dir")) or {}
    ai = res.get("ai") or {}
    prov = ai.get("providers") or {}
    by = ((res.get("ai_calls_by") or {}).get(time.strftime("%Y-%m-%d", time.gmtime()), {}))
    lines = ["🧠 <b>AI team</b> — چند هوش مصنوعی هم‌زمان و شبانه‌روزی ایده می‌دهند؛ حکم نهایی همیشه با آزمایشگاه آماری است.",
             f"Master switch: {'ON' if cfg['ai_on'] else 'OFF'} · max {cfg['ai_calls_day']} calls/day per AI · Claude model <b>{cfg['ai_model']}</b>",
             "🔑 = کلید در ربات دیده می‌شود · آخرین اجرای پژوهش:"]
    for p_ in rnd_ai.provider_status(cfg["ai_model"]):
        on = cfg.get(rl.AI_PROVIDER_KEYS[p_["id"]])
        r = prov.get(p_["id"]) or {}
        last = "✅ used" if r.get("used") else (f"⚠️ {html.escape(str(r.get('error'))[:70])}" if r.get("error") else (f"— {r.get('skipped')}" if r.get("skipped") else "— not run yet"))
        lines.append(f"{'🟢' if (on and p_['has_key']) else '⚪'} <b>{html.escape(p_['label'])}</b> {'🔑 ' + p_['src'] if p_['has_key'] else '❌ no key (' + p_['env'] + ')'} · {html.escape(str(p_['model']))} · today {by.get(p_['id'], 0)}/{cfg['ai_calls_day']} · {last}")
    hl = res.get("ai_health") or {}
    waits = []
    for pid_, h_ in hl.items():
        left = float(h_.get("until", 0) or 0) - time.time()
        if left > 0 and pid_ in rnd_ai.PROVIDERS:
            m_ = int(left // 60)
            waits.append(f"⏳ {html.escape(rnd_ai.PROVIDERS[pid_]['label'])}: {html.escape(rnd_ai.KIND_FA.get(h_.get('kind'), 'error'))} — retry in {m_ // 60}h{m_ % 60:02d}m (others cover its work)")
    if waits:
        lines.append("\n" + "\n".join(waits))
    if test_msg:
        lines.append("\n🔌 Test: " + html.escape(test_msg))
    au = ai.get("audit") or {}
    if au.get("issues") or au.get("actions"):
        lines.append("\n<b>🔍 Chief auditor</b>")
        lines += [f"⚠️ {html.escape(str(x))}" for x in (au.get("issues") or [])[:4]]
        lines += [f"➡️ {html.escape(str(x))}" for x in (au.get("actions") or [])[:3]]
    if ai.get("error"):
        lines.append(f"\nLast research run: ⚠️ {html.escape(str(ai['error']))}")
    if ai.get("analysis"):
        lines.append("\n<b>Latest AI analysis</b>")
        lines += [f"• {html.escape(str(x))}" for x in ai["analysis"][:8]]
    rows: Rows = [[_b(f"{'✅' if cfg['ai_on'] else '⬜'} AI team", "adm:rnd:tg:ai_on"), _b("🔌 Test all", "adm:rnd:aitest:all")]]
    for p_ in rnd_ai.provider_status(cfg["ai_model"]):
        k = rl.AI_PROVIDER_KEYS[p_["id"]]
        rows.append([_b(f"{'✅' if cfg[k] else '⬜'} {p_['label']}"[:40], f"adm:rnd:tg:{k}"), _b("🔌 Test", f"adm:rnd:aitest:{p_['id']}")])
    rows.append([_b(("✅ " if cfg["ai_model"] == m else "") + m.replace("claude-", ""), f"adm:rnd:am:{m}") for m in rl.AI_MODELS])
    rows.append([_b("➖", "adm:rnd:sn:ai_calls_day:-1"), _b(f"{cfg['ai_calls_day']} · calls/day each", "adm:rnd:ty:ai_calls_day"), _b("➕", "adm:rnd:sn:ai_calls_day:1")])
    rows.append(_nav())
    return _fit("\n".join(lines)[:3300], "rnd_ai"), rows


_MST = {"PASS": "✅", "CAND": "🟢", "LAB": "🟡", "FAIL": "❌", "NA": "➖"}
_MPAGE = 8


def _modules_data(ctx):
    res = rl.read_json(rl.MODULES_NAME, {}, ctx.get("data_dir")) or {}
    mods = res.get("modules") or {}
    order = {"PASS": 0, "CAND": 1, "LAB": 2, "FAIL": 3, "NA": 4}
    keys = sorted(mods, key=lambda k: (order.get(mods[k].get("status"), 9), -float(mods[k].get("avg_r") or -9)))
    return res, mods, keys


def modules_page(ctx, page: int = 0, note: str = ""):
    cfg = rl.settings_of(_st(ctx))
    res, mods, keys = _modules_data(ctx)
    lines = ["🧩 <b>All modules — 24h re-test</b>",
             f"Re-test: {'ON' if cfg['modules_on'] else 'OFF'} · every {rl.effective_modules_h(cfg):g}h · {'non-stop' if cfg['nonstop'] else 'scheduled'} · auto-apply: {'ON' if (cfg['modules_apply'] and cfg['mode'] == 'auto') else 'off (needs Auto mode)'}",
             f"Last run: {res.get('generated', 'never')}",
             "✅ passes all your criteria · 🟢 candidate · 🟡 lab says PASS but below YOUR criteria · ❌ fails · ➖ no data"]
    if not keys:
        lines.append("\nNo result yet. Tap «Re-test now» (needs RND_DISPATCH_TOKEN) or wait for the next hourly run.")
    cnt = {k: sum(1 for m in mods.values() if m.get("status") == k) for k in _MST}
    if keys:
        lines.append(f"\n✅ {cnt['PASS']} · 🟢 {cnt['CAND']} · 🟡 {cnt['LAB']} · ❌ {cnt['FAIL']} · ➖ {cnt['NA']}")
    n_pages = max(1, (len(keys) + _MPAGE - 1) // _MPAGE)
    page = max(0, min(page, n_pages - 1))
    for k in keys[page * _MPAGE:(page + 1) * _MPAGE]:
        m = mods[k]
        gt = m.get("gate") or {}
        lines.append(f"{_MST.get(m.get('status'), '❔')} <b>{k}</b> · ladder {m.get('best_ladder') or '—'} · {gt.get('passed', '?')}/{gt.get('total', '?')} criteria"
                     + (f" · {m.get('avg_r'):+.2f}R PF {m.get('pf'):.2f} win {m.get('win'):.0f}%" if m.get("avg_r") is not None and m.get("pf") is not None and m.get("win") is not None else ""))
    if note:
        lines.append("\n" + note)
    rows: Rows = []
    chunk = keys[page * _MPAGE:(page + 1) * _MPAGE]
    for i in range(0, len(chunk), 2):
        rows.append([_b(f"{_MST.get(mods[k].get('status'), '❔')} {k}"[:30], f"adm:rnd:mdt:{keys.index(k)}") for k in chunk[i:i + 2]])
    if n_pages > 1:
        rows.append([_b("⬅️", f"adm:rnd:mod:{page - 1}") if page > 0 else _b("·", "adm:rnd:mod:0"), _b(f"{page + 1}/{n_pages}", f"adm:rnd:mod:{page}"),
                     _b("➡️", f"adm:rnd:mod:{page + 1}") if page < n_pages - 1 else _b("·", f"adm:rnd:mod:{page}")])
    rows.append([_b("🔬 Re-test now", "adm:rnd:mrun"), _b("✅ Apply to bots now", "adm:rnd:mapply")])
    rows.append([_b(f"{'✅' if cfg['modules_on'] else '⬜'} 24h re-test", "adm:rnd:tg:modules_on"), _b(f"{'✅' if cfg['modules_apply'] else '⬜'} Auto-apply", "adm:rnd:tg:modules_apply")])
    rows.append(_nav())
    return _fit("\n".join(lines)[:3300], "rnd_modules"), rows


def module_detail(ctx, idx: int):
    res, mods, keys = _modules_data(ctx)
    if not (0 <= idx < len(keys)):
        return modules_page(ctx)
    k = keys[idx]
    m = mods[k]
    gt = m.get("gate") or {}
    lines = [f"{_MST.get(m.get('status'), '❔')} <b>{k}</b> — status {m.get('status')} (lab verdict {m.get('verdict')}) · best ladder {m.get('best_ladder') or '—'}"]
    if gt.get("checks"):
        lines.append(f"\n🎯 <b>Pass criteria</b> ({gt.get('passed')}/{gt.get('total')} met)")
        for c in gt["checks"]:
            lines.append(f"{{True: '✅', False: '❌', None: '➖'}}[c.get('ok')] {c['n']}: {'n/a' if c.get('v') is None else c['v']} (need {c['lim']})".replace("{True: '✅', False: '❌', None: '➖'}[c.get('ok')]", {True: "✅", False: "❌", None: "➖"}[c.get("ok")]))
    elif m.get("fail_reasons"):
        lines.append("Reasons: " + html.escape(", ".join(str(x) for x in m["fail_reasons"])[:600]))
    lines.append("\nنتیجه از بک‌تست چندنمادی با کارمزد؛ ماژول ✅ در حالت Auto با بهترین نردبانش فعال می‌ماند و ماژول ❌ قفل می‌شود.")
    return "\n".join(lines)[:3800], [[_b("⬅️ All modules", "adm:rnd:mod"), _b("🏠 R&D", HOME)]]


def watch_page(ctx, note: str = ""):
    import toobit_watch as tw
    d = tw.load(ctx.get("data_dir"))
    cfg = tw.settings_of(d)
    now = time.time()
    lines = ["🛰 <b>Toobit watch</b> — contracts &amp; exchange news",
             f"Status: {'ON' if cfg['on'] else 'OFF'} · last check: {_ago(d.get('last_ok'))}"
             + (f" · ⚠️ {d['last_error']}" if d.get("last_error") else ""),
             f"Contracts tracked: {len(d.get('snapshot') or {})} · news URLs: {len(tw.news_urls())}"]
    p = d.get("pause") or {}
    if p and float(p.get("until", 0)) > now:
        lines.append(f"⏸ Signals paused for {int((float(p['until']) - now) / 60)} more min — {p.get('reason', '')}")
    bl = d.get("blocked") or {}
    if bl:
        lines.append("\n<b>Blocked coins</b>")
        for k, v in list(bl.items())[:12]:
            lines.append(f"• {k}USDT — {v.get('reason')} ({_ago(v.get('since'))})")
    ev = d.get("events") or []
    if ev:
        lines.append("\n<b>Latest events</b>")
        for e in ev[:6]:
            lines.append(f"• {({'high': '🔴', 'med': '🟠'}.get(e.get('sev'), '🟢'))} {str(e.get('text', ''))[:90]} <i>({_ago(e.get('t'))})</i>")
    if note:
        lines.append("\n" + note)
    rows: Rows = [[_b(f"{'✅' if cfg[k] else '⬜'} {tw.LABEL[k]}"[:60], f"adm:rnd:wtg:{k}")] for k in ("on", "auto_block", "auto_pause", "notify", "news")]
    rows.append([_b("➖", "adm:rnd:wsn:-1"), _b(f"Pause length: {cfg['pause_h']} h", "adm:rnd:wt"), _b("➕", "adm:rnd:wsn:1")])
    rows.append([_b("▶️ Run now", "adm:rnd:wrun")] + ([_b("⏯ Clear pause", "adm:rnd:wcp")] if p and float(p.get("until", 0)) > now else []))
    for k in list(bl)[:6]:
        rows.append([_b(f"🔓 Unblock {k}USDT", f"adm:rnd:wub:{k}")])
    rows.append(_nav())
    return _fit("\n".join(lines)[:3000], "rnd_watch"), rows


def handle_text(chat_id: int, user_id: int, text: str, awaiting: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    """مقدار تایپ‌شده‌ی یک تنظیم عددی (adm_rnd_num). عدد خارج از بازه به حد مجاز نزدیک می‌شود (rl._coerce)."""
    data = awaiting.get("data") or {}
    k = data.get("key")
    ctx["clear_await"](user_id)
    g = next((g_ for g_, (_, ks) in GROUPS.items() if k in ks), None)
    back = "adm:rnd:ai" if str(k).startswith("ai_") else f"adm:rnd:sg:{g}" if g else "adm:rnd:set"
    v = ctx["parse_num"](text)
    if k not in rl.SCHEMA or rl.SCHEMA[k][0] not in ("int", "float") or v is None:
        ctx["send"](chat_id, "❌ عدد معتبر نبود.", [[_b("⬅️ Back", back)]])
        return True
    st = _st(ctx)
    st["settings"][k] = rl._coerce(k, v)
    rl.log(st, "settings", f"{rl.LABEL[k]} → {_fmt(st['settings'][k])}")
    rl.save_state(st, ctx.get("data_dir"))
    ctx["send"](chat_id, *(ai_page(ctx) if str(k).startswith("ai_") else group_page(ctx, g, f"✅ {rl.LABEL[k]} = {_fmt(st['settings'][k])}") if g else settings_page(ctx)))
    return True


def confirm_page(kind: str):
    what = "Approve ALL pending proposals" if kind == "aa" else "Reject ALL pending proposals"
    return f"⚠️ {what}?", [[_b("✅ Yes", f"adm:rnd:{kind}y"), _b("✖️ No", "adm:rnd:pl:0")]]


# ------------------------------------------------------------------------------------------ callbackها
def handle_callback(p: List[str], chat_id: int, message_id: int, user_id: int, ctx: Dict[str, Any]) -> bool:
    """p = data.split(':') ؛ p[0]='adm', p[1]='rnd', p[2]=عمل."""
    import shared_controls as sc
    edit = lambda t, r: ctx["edit"](chat_id, message_id, t, r)       # noqa: E731
    act = p[2] if len(p) > 2 else "home"
    arg = p[3:] if len(p) > 3 else []
    dd = ctx.get("data_dir")
    try:
        if act == "home":
            edit(*home(ctx))
        elif act == "mode":
            edit(*mode_page(ctx))
        elif act == "m" and arg:
            st = _st(ctx)
            st["settings"]["mode"] = arg[0] if arg[0] in rl.SCHEMA["mode"][3] else "semi"
            rl.log(st, "mode", f"Mode → {rl.MODE_TXT[st['settings']['mode']]}")
            rl.save_state(st, dd)
            edit(*mode_page(ctx))
        elif act == "pl":
            edit(*proposals_page(ctx, int(arg[0]) if arg else 0))
        elif act == "pv" and arg:
            edit(*proposal_page(ctx, arg[0]))
        elif act in ("ap", "rj") and arg:
            st = _st(ctx)
            if act == "ap":
                recs = rl.enrich(__import__("perf_report").load_records(ctx["load_history"](), dd))
                ok, msg = rl.apply_proposal(st, arg[0], _env(ctx), "admin", recs)
                note = "✅ Applied. It will be checked automatically after the next trades — you can undo it any time." if ok else f"❌ {msg}"
            else:
                for q in st["proposals"]:
                    if q["id"] == arg[0] and q["status"] == "pending":
                        q["status"] = "rejected"; q["rejected_at"] = time.time()
                        rl.log(st, "reject", f"✖️ {q['desc']}")
                note = "✖️ Rejected (won't be proposed again for 14 days)."
            rl.save_state(st, dd)
            edit(note, [[_b("📝 Proposals", "adm:rnd:pl:0"), _b("🧪 R&D", HOME)]])
        elif act in ("aa", "ra"):
            edit(*confirm_page(act))
        elif act in ("aay", "ray"):
            st = _st(ctx)
            n = 0
            recs = rl.enrich(__import__("perf_report").load_records(ctx["load_history"](), dd)) if act == "aay" else []
            for q in [x for x in st["proposals"] if x["status"] == "pending"]:
                if act == "aay":
                    n += 1 if rl.apply_proposal(st, q["id"], _env(ctx), "admin", recs)[0] else 0
                else:
                    q["status"] = "rejected"; q["rejected_at"] = time.time(); n += 1
            rl.save_state(st, dd)
            edit(f"Done: {n} proposal(s) {'applied' if act == 'aay' else 'rejected'}.", [[_b("🧪 R&D", HOME)]])
        elif act == "fd":
            edit(*findings_page(ctx))
        elif act == "sc":
            edit(*scorecard_page(ctx))
        elif act == "sl":
            edit(*strategies_page(ctx))
        elif act == "sd" and arg:
            edit(*slot_page(ctx, arg[0]))
        elif act == "st" and len(arg) > 1 and arg[1] in rl.BOTS:
            slot, b = arg[0], arg[1]
            cur = bool(sc.load(b, ctx.get("controls_path")).get(f"mod_{slot}"))
            sc.set_value(b, f"mod_{slot}", not cur, ctx.get("controls_path"))
            st = _st(ctx)
            rl.log(st, "manual", f"{'Enabled' if not cur else 'Disabled'} {slot} on {b.upper()} (manual)")
            rl.save_state(st, dd)
            edit(*slot_page(ctx, slot))
        elif act == "ch":
            edit(*changes_page(ctx, int(arg[0]) if arg and arg[0].isdigit() else 0))
        elif act == "cd" and arg:
            edit(*change_detail(ctx, arg[0]))
        elif act == "tl":
            edit(*timeline_page(ctx, arg[0] if arg else "all", int(arg[1]) if len(arg) > 1 and arg[1].isdigit() else 0))
        elif act == "gd":
            edit(*guard_page(ctx))
        elif act == "gdr":
            edit("⏳ Checking performance…", [])
            st = _st(ctx)
            recs = rl.enrich(__import__("perf_report").load_records(ctx["load_history"](), dd))
            ms = rl.performance_guard(st, recs, _env(ctx), rl.settings_of(st))
            rl.save_state(st, dd)
            edit(*guard_page(ctx, "\n".join(ms) if ms else "✅ No significant drop detected."))
        elif act == "lb":
            edit(*library_page(ctx, arg[0] if arg else "all", int(arg[1]) if len(arg) > 1 and arg[1].isdigit() else 0))
        elif act == "ld" and arg:
            edit(*lib_detail(ctx, arg[0], arg[1] if len(arg) > 1 else "all", int(arg[2]) if len(arg) > 2 and arg[2].isdigit() else 0))
        elif act == "ins":
            edit(*insights_page(ctx))
        elif act == "un" and arg:
            st = _st(ctx)
            ok, msg = rl.undo_applied(st, arg[0], _env(ctx))
            rl.save_state(st, dd)
            edit(f"{'↩️ Undone.' if ok else '❌ ' + msg}", [[_b("🛡 Changes", "adm:rnd:ch"), _b("🧪 R&D", HOME)]])
        elif act == "rr" and arg:
            st = _st(ctx)
            ok = rl.remove_rule(st, arg[0], _env(ctx))
            rl.save_state(st, dd)
            edit("↩️ Rule removed." if ok else "Rule not found.", [[_b("🛡 Changes", "adm:rnd:ch"), _b("🧪 R&D", HOME)]])
        elif act == "jr":
            edit(*journal_page(ctx))
        elif act == "run":
            edit("⏳ Diagnosing all closed trades…", [])
            out = rl.tick(dict(_env(ctx)), force=True)
            msg = (f"✅ Diagnosis done. New proposals: {out.get('new', 0)}." + ("".join("\n" + n for n in out.get("notes", []))) + ("".join("\n🤖 " + a for a in out.get("applied", []))) if out.get("ran") else f"⚠️ {out.get('error') or 'Mode is Off — choose a mode first.'}")
            edit(msg, [[_b("📝 Proposals", "adm:rnd:pl:0"), _b("🩺 Findings", "adm:rnd:fd")], _nav()])
        elif act == "res":
            st = _st(ctx)
            rl.request_research(st)
            rl.save_state(st, dd)
            ok, why = rl.dispatch_workflow("rnd_lab.yml", inputs={"force": "true"})
            edit("🔬 Deep research requested. " + ("Started on GitHub Actions now — results appear here in roughly 30–90 minutes." if ok else f"Queued: {why}."),
                 [[_b("🧪 New strategies", "adm:rnd:sl")], _nav()])
        elif act == "set":
            edit(*settings_page(ctx))
        elif act == "sg" and arg and arg[0] in GROUPS:
            edit(*group_page(ctx, arg[0]))
        elif act in ("tg", "cy", "sn", "am") and arg:
            st = _st(ctx)
            cfg = rl.settings_of(st)
            k = arg[0] if act != "am" else "ai_model"
            if k in rl.SCHEMA:
                if act == "tg":
                    st["settings"][k] = not cfg[k]
                elif act == "cy":
                    ch = rl.SCHEMA[k][3]
                    st["settings"][k] = ch[(ch.index(cfg[k]) + 1) % len(ch)]
                elif act == "sn":
                    st["settings"][k] = rl._coerce(k, cfg[k] + float(arg[1]) * rl.STEP.get(k, 1))
                else:
                    st["settings"][k] = arg[0]
                rl.save_state(st, dd)
            g = next((g_ for g_, (_, ks) in GROUPS.items() if k in ks), None)
            edit(*(ai_page(ctx) if k.startswith("ai_") else group_page(ctx, g) if g else settings_page(ctx)))
        elif act == "ty" and arg and arg[0] in rl.SCHEMA and rl.SCHEMA[arg[0]][0] in ("int", "float"):
            k = arg[0]
            cfg = rl.settings_of(_st(ctx))
            typ, dflt, lo, hi = rl.SCHEMA[k]
            ctx["set_await"](user_id, "adm_rnd_num", {"key": k})
            g = next((g_ for g_, (_, ks) in GROUPS.items() if k in ks), None)
            edit(f"✏️ مقدار جدید «{rl.LABEL[k]}» را تایپ کنید.\nفعلی: <b>{_fmt(cfg[k])}{rl.UNIT.get(k, '')}</b> · پیش‌فرض: {_fmt(dflt)} · بازه‌ی مجاز: {_fmt(lo)} تا {_fmt(hi)}",
                 [[_b("✖️ Cancel", "adm:rnd:ai" if k.startswith("ai_") else f"adm:rnd:sg:{g}" if g else "adm:rnd:set")]])
        elif act == "grs" and arg and arg[0] in GROUPS:
            st = _st(ctx)
            rl.reset_keys(st, GROUPS[arg[0]][1])
            rl.log(st, "settings", f"♻️ Reset {GROUPS[arg[0]][0]} to best defaults")
            rl.save_state(st, dd)
            edit(*group_page(ctx, arg[0], "♻️ همه‌ی مقادیر این صفحه به پیش‌فرضِ بهینه برگشت."))
        elif act == "ai":
            edit(*ai_page(ctx))
        elif act == "aitest":
            import rnd_ai
            cm = rl.settings_of(_st(ctx))["ai_model"]
            which = arg[0] if arg else "claude"
            ids = [x["id"] for x in rnd_ai.provider_status(cm) if x["has_key"]] if which == "all" else [which]
            edit("⏳ Testing the connection…", [])
            from concurrent.futures import ThreadPoolExecutor
            ids = [i for i in ids if i in rnd_ai.PROVIDERS]
            with ThreadPoolExecutor(max_workers=max(1, min(8, len(ids)))) as ex:
                msgs = [("✅ " if ok else "❌ ") + msg for ok, msg in ex.map(lambda i: rnd_ai.ping_provider(i, cm), ids)]
            edit(*ai_page(ctx, "\n".join(msgs) or "هیچ کلیدی در ربات دیده نشد."))
        elif act == "mod":
            edit(*modules_page(ctx, int(arg[0]) if arg and arg[0].lstrip("-").isdigit() else 0))
        elif act == "mdt" and arg and arg[0].isdigit():
            edit(*module_detail(ctx, int(arg[0])))
        elif act == "mrun":
            st = _st(ctx)
            rl.request_modules(st)
            rl.save_state(st, dd)
            ok, why = rl.dispatch_workflow("rnd_modules.yml", inputs={"force": "true"})
            edit(*modules_page(ctx, 0, "🔬 " + ("Started on GitHub Actions now — takes roughly 1–5 hours." if ok else f"Queued: {why}.")))
        elif act == "mapply":
            import rnd_research as rr
            npass, nlock = rr.apply_modules(dd)
            rl.log(_st(ctx), "apply", f"🧩 modules applied: {npass} pass, {nlock} locked")
            edit(*modules_page(ctx, 0, f"✅ Applied to lowtf_verdicts: {npass} module(s) pass with their best ladder, {nlock} locked."))
        elif act == "wt":
            edit(*watch_page(ctx))
        elif act == "wtg" and arg:
            import toobit_watch as tw
            cur = tw.settings_of(tw.load(dd))
            if arg[0] in tw.DEFAULTS and isinstance(tw.DEFAULTS[arg[0]], bool):
                tw.set_setting(arg[0], not cur[arg[0]], dd)
            edit(*watch_page(ctx))
        elif act == "wsn" and arg:
            import toobit_watch as tw
            cur = tw.settings_of(tw.load(dd))
            tw.set_setting("pause_h", max(1, min(48, cur["pause_h"] + int(float(arg[0])))), dd)
            edit(*watch_page(ctx))
        elif act == "wrun":
            import toobit_watch as tw
            edit("⏳ Checking Toobit…", [])
            r = tw.tick(dd, force=True)
            edit(*watch_page(ctx, f"✅ Checked — {len(r['events'])} new event(s)." if not r.get("error") else f"⚠️ {r['error']}"))
        elif act == "wcp":
            import toobit_watch as tw
            tw.clear_pause(dd)
            edit(*watch_page(ctx, "⏯ Pause cleared."))
        elif act == "wub" and arg:
            import toobit_watch as tw
            tw.unblock(arg[0], dd)
            edit(*watch_page(ctx, f"🔓 {arg[0]}USDT unblocked."))
        else:
            edit(*home(ctx))
    except Exception as e:  # noqa: BLE001
        print(f"[rnd_panel] {act} failed: {e}")
        edit(f"⚠️ R&D Lab error: {e}", [_nav()])
    return True

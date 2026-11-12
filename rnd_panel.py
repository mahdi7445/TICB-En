# -*- coding: utf-8 -*-
"""
rnd_panel.py — رابط دکمه‌ای «🧪 R&D Lab» (زیرمنوی Admin Panel). فقط UI؛ منطق در rnd_lab / rnd_dsl است.
همه‌ی callbackها «adm:rnd:...» (admin_panel.handle_callback آن‌ها را به handle_callback همین فایل می‌دهد). هیچ‌چیز نیاز به تایپ ندارد.
ctx همان ctx پنل: send, edit, controls_path, load_history, data_dir.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import rnd_lab as rl

Rows = List[List[Dict[str, str]]]
HOME = "adm:rnd:home"


def _b(text: str, cb: str) -> Dict[str, str]:
    return {"text": text, "callback_data": cb}


def _nav(back: str = HOME) -> List[Dict[str, str]]:
    return [_b("⬅️ Back", back), _b("🏠 Admin", "adm:home")]


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
    lines = [f"All time: {o['n']} trades · {o['avg']:+.2f}R avg · PF {o['pf']:.2f} {mark(o['pf'] >= cfg['target_pf'])} · win {o['win']:.0f}% {mark(o['win'] >= cfg['target_win'])} · max DD {o.get('dd', 0):.1f}R"]
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
        [_b("⚙️ Settings", "adm:rnd:set"), _b("⬅️ Admin Panel", "adm:home")],
    ]
    return txt, rows


def mode_page(ctx):
    cfg = rl.settings_of(_st(ctx))
    txt = ("🎚 <b>Mode</b>\n\n⛔ <b>Off</b> — nothing runs.\n👁 <b>Advisor</b> — analyses and reports only; never changes anything.\n"
           "✋ <b>Semi-auto</b> — makes proposals; a change is applied only after you tap Approve.\n"
           "🤖 <b>Auto</b> — applies changes that meet the strict evidence bar, within the daily limit, and rolls them back if results get worse.\n\n"
           f"Now: <b>{rl.MODE_TXT[cfg['mode']]}</b>")
    rows = [[_b(("✅ " if cfg["mode"] == m else "") + rl.MODE_TXT[m], f"adm:rnd:m:{m}")] for m in ("off", "advisor", "semi", "auto")]
    rows.append(_nav())
    return txt, rows


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
    return "\n".join(lines), rows


def proposal_page(ctx, pid: str):
    st = _st(ctx)
    p = next((x for x in st["proposals"] if x["id"] == pid), None)
    if not p:
        return "Proposal not found.", [_nav("adm:rnd:pl:0")]
    what = {"ctl": f"Setting <code>{p['key']}</code> on <b>{(p['section'] or '').upper()}</b> → <b>{p['value']}</b>", "rule": f"New rule: <code>{p['rule']}</code>",
            "enable": f"Turn ON <b>{p['key']}</b> on {', '.join(s.upper() for s in p['value']['sections'])} (probation cap applies)"}.get(p["kind"], "")
    txt = (f"{p['desc']}\n\n{what}\n\nEvidence: {_fmt_ev(p['ev']) or 'n/a'}\nSource: {p.get('src')}"
           + ("\n\n<i>Every applied change can be undone, and is checked automatically after the next trades.</i>"))
    rows = [[_b("✅ Approve", f"adm:rnd:ap:{pid}"), _b("✖️ Reject", f"adm:rnd:rj:{pid}")], _nav("adm:rnd:pl:0")]
    return txt, rows


def findings_page(ctx):
    diag = rl.read_json(rl.DIAG_NAME, {}, ctx.get("data_dir"))
    fs = diag.get("findings") or []
    lines = [f"🩺 <b>Findings</b> — statistically significant leaks (from {diag.get('n_total', 0)} closed trades, {diag.get('generated', 'not yet run')})"]
    if not fs:
        lines.append("\nNo significant leak detected with the current evidence thresholds. Keep collecting trades or lower “Evidence to PROPOSE” in Settings.")
    for f in fs[:12]:
        lines.append(f"\n{'🔴' if f['sev'] == 'high' else '🟠' if f['sev'] == 'med' else '🟡'} {f['text']}")
    return "\n".join(lines)[:3900], [[_b("▶️ Diagnose now", "adm:rnd:run")], _nav()]


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
    return "\n".join(lines)[:3900], [_nav()]


def strategies_page(ctx):
    import shared_controls as sc
    res = rl.read_json(rl.RESULTS_NAME, {}, ctx.get("data_dir"))
    st = _st(ctx)
    slots = (res or {}).get("slots") or {}
    lines = ["🧪 <b>New strategies</b> invented and tested by the lab (6 slots).",
             f"Last deep research: {_ago((res or {}).get('ts'))} · tested {(res or {}).get('tested', 0)} ideas",
             "🟢 PASS · 🟡 candidate · 🔴 failed the lab (locked) · ⚪ empty. Only PASS/candidate can ever trade."]
    rows: Rows = []
    for s in __import__("rnd_dsl").SLOTS:
        r = slots.get(s) or {"status": "EMPTY"}
        on = [b.upper() for b in rl.BOTS if sc.load(b, ctx.get("controls_path")).get(f"mod_{s}")]
        ic = {"PASS": "🟢", "CAND": "🟡", "FAIL": "🔴"}.get(r["status"], "⚪")
        label = f"{ic} {s} · {r.get('name') or 'empty'}" + (f" · ON {'/'.join(on)}" if on else "")
        rows.append([_b(label[:60], f"adm:rnd:sd:{s}")])
    rows.append(_nav())
    return "\n".join(lines), rows


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
        if r["status"] == "FAIL" and r.get("fail_reasons"):
            lines.append(f"Did not pass: {', '.join(r['fail_reasons'])}")
    rows: Rows = []
    for b in rl.BOTS:
        on = bool(sc.load(b, ctx.get("controls_path")).get(f"mod_{slot}"))
        rows.append([_b(f"{'✅' if on else '⬜'} {b.upper()} {'ON' if on else 'OFF'}", f"adm:rnd:st:{slot}:{b}")])
    rows.append([_b("⚙️ Strategy settings (ladder, caps…)", f"adm:ticb:ms:{slot}")])
    rows.append(_nav("adm:rnd:sl"))
    return "\n".join(lines)[:3900], rows


def changes_page(ctx):
    st = _st(ctx)
    act = [e for e in st["applied"] if e.get("state") in ("active", "confirmed")][-8:]
    rules = rl._active_rules(ctx.get("data_dir")).get("rules", [])
    lines = ["🛡 <b>Active changes &amp; rules</b>\nEvery change can be undone with one tap."]
    rows: Rows = []
    for e in reversed(act):
        chk = e.get("checked")
        tag = "✔️ confirmed" if e.get("state") == "confirmed" else (f"checking {chk['n']}/{e.get('rb_n')}" if chk else "watching")
        lines.append(f"\n• {e['desc'][:150]} <i>({tag}, {'auto' if e.get('by') == 'auto' else 'you'})</i>")
        rows.append([_b(f"↩️ Undo: {e['desc'][:40]}", f"adm:rnd:un:{e['id']}")])
    for r in rules:
        lines.append(f"\n🚫 Rule {r.get('id')}: {r.get('scope')} {r.get('tf') or ''} {r.get('side') or ''} {('UTC ' + str(r['hours'][0]) + '–' + str(r['hours'][-1] + 1) + 'h') if r.get('hours') else ''}")
        rows.append([_b(f"❌ Remove rule {r.get('id')}", f"adm:rnd:rr:{r.get('id')}")])
    if not act and not rules:
        lines.append("\nNothing active yet.")
    rows.append(_nav())
    return "\n".join(lines)[:3900], rows


def journal_page(ctx):
    st = _st(ctx)
    lines = ["📜 <b>Journal</b> (latest first)"]
    for j in reversed(st["journal"][-14:]):
        lines.append(f"\n{rl.now_iso(j['t'])} — {j['x']}")
    if len(lines) == 1:
        lines.append("\nEmpty.")
    return "\n".join(lines)[:3900], [_nav()]


def _setting_rows(keys: List[str], cfg: Dict[str, Any], back: str) -> Rows:
    rows: Rows = []
    for k in keys:
        typ = rl.SCHEMA[k][0]
        if typ == "bool":
            rows.append([_b(f"{'✅' if cfg[k] else '⬜'} {rl.LABEL[k]}", f"adm:rnd:tg:{k}")])
        elif typ == "choice":
            rows.append([_b(f"🔁 {rl.LABEL[k]}: {cfg[k]}", f"adm:rnd:cy:{k}")])
        else:
            rows.append([_b("➖", f"adm:rnd:sn:{k}:-1"), _b(f"{rl.LABEL[k]}: {cfg[k]:g}", HOME.replace("home", "set")), _b("➕", f"adm:rnd:sn:{k}:1")])
    rows.append(_nav(back))
    return rows


GROUPS = {
    "ev": ("🔬 Evidence & safety", ["min_n", "min_t", "auto_t", "max_changes_day", "rollback", "rb_n"]),
    "tg": ("🎯 Targets", ["target_pf", "target_win"]),
    "ac": ("🧰 What the lab may change", ["a_off", "a_block", "a_stop", "a_caps", "a_ladder", "a_new"]),
    "rs": ("🔬 Research", ["light_h", "research_h", "cands", "probation_day", "new_auto"]),
}


def settings_page(ctx):
    rows = [[_b(t, f"adm:rnd:sg:{g}")] for g, (t, _) in GROUPS.items()]
    rows.append(_nav())
    return "⚙️ <b>R&amp;D settings</b> — all with buttons.", rows


def group_page(ctx, g: str):
    cfg = rl.settings_of(_st(ctx))
    t, keys = GROUPS[g]
    return f"{t}\n\nTap ➖/➕ to change numbers, or a name to toggle.", _setting_rows(keys, cfg, "adm:rnd:set")


def ai_page(ctx):
    import os
    st = _st(ctx)
    cfg = rl.settings_of(st)
    res = rl.read_json(rl.RESULTS_NAME, {}, ctx.get("data_dir")) or {}
    ai = res.get("ai") or {}
    lines = ["🧠 <b>AI assistant</b>\nClaude reads your real trade diagnostics, explains why trades lose and invents strategy ideas. "
             "It never decides: every idea must still pass the statistical lab, and AI tuning ideas always need your approval.",
             f"\nStatus: {'ON' if cfg['ai_on'] else 'OFF'} · model <b>{cfg['ai_model']}</b> · max {cfg['ai_calls_day']} calls/day",
             "Key: the secret ANTHROPIC_API_KEY must exist in the research workflow (GitHub → Settings → Secrets)."]
    if ai.get("error"):
        lines.append(f"Last run: ⚠️ {ai['error']}")
    elif ai.get("used"):
        lines.append("Last run: ✅ used")
    if ai.get("analysis"):
        lines.append("\n<b>Latest AI analysis</b>")
        lines += [f"• {x}" for x in ai["analysis"]]
    rows = [[_b(f"{'✅' if cfg['ai_on'] else '⬜'} AI assistant", "adm:rnd:tg:ai_on")],
            [_b(("✅ " if cfg["ai_model"] == m else "") + m.replace("claude-", ""), f"adm:rnd:am:{m}") for m in rl.AI_MODELS],
            [_b("➖", "adm:rnd:sn:ai_calls_day:-1"), _b(f"Calls/day: {cfg['ai_calls_day']}", "adm:rnd:ai"), _b("➕", "adm:rnd:sn:ai_calls_day:1")],
            _nav()]
    return "\n".join(lines)[:3900], rows


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
            edit(*changes_page(ctx))
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
        elif act == "ai":
            edit(*ai_page(ctx))
        else:
            edit(*home(ctx))
    except Exception as e:  # noqa: BLE001
        print(f"[rnd_panel] {act} failed: {e}")
        edit(f"⚠️ R&D Lab error: {e}", [_nav()])
    return True

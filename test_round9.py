"""R&D proposal page (int-value bug), toggles-last ordering, detailed admin reports, duplicate-signal guards."""
import json, os, re, sys, tempfile, time
tmp = tempfile.mkdtemp(); os.environ["DATA_DIR"] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rnd_lab as rl, rnd_panel as rp, rnd_report as rep

ctx = {"data_dir": tmp, "controls_path": os.path.join(tmp, "bot_controls.json"), "load_history": lambda: [], "await": {}}
ctx["edit"] = lambda c, m, t, rows=None: ctx.__setitem__("last", (t, rows))
ctx["send"] = ctx["edit"]
ctx["set_await"] = lambda u, k, d=None: None
ctx["clear_await"] = lambda u: None
ctx["parse_num"] = lambda t: None

# 1) proposal detail never crashes — every kind, int/float/str/dict values
ev = {"n": 80, "avg": 0.1, "t": 2.5, "pf": 1.3, "win": 48, "h1": 0.1, "h2": 0.1}
st = rl.load_state(tmp)
for i, (kind, val, rule, key) in enumerate([("ctl", 3, None, "mx_X_day"), ("ctl", 0.5, None, "k"), ("ctl", True, None, "k2"), ("ctl", "both", None, "k3"),
                                              ("rule", None, {"a": "<b>"}, None), ("enable", {"sections": ["ticb", "sgnl"]}, None, "RD1"), ("weird", 5, None, None)]):
    st["proposals"].append({"id": f"p{i}", "kind": kind, "section": "ticb", "key": key, "value": val, "rule": rule, "desc": "desc <x> & y", "ev": ev, "src": "stat",
                            "status": "pending", "created": time.time(), "auto_ok": False})
rl.save_state(st, tmp)
for i in range(7):
    t, rows = rp.proposal_page(ctx, f"p{i}"); assert "Evidence" in t and len(t) <= 4096 and not re.search(r"<(?!/?(b|i|code)>)", t), (i, t)
assert "3" in rp.proposal_page(ctx, "p0")[0] and "RD1" in rp.proposal_page(ctx, "p5")[0]
t, rows = rp.proposals_page(ctx, 0); assert "Proposals" in t
ok = []
def cb(*a):
    ok.append(a)
import types
print("proposal page ok")

# 2) toggles last, descriptions in the same order as buttons, in every settings group
for g, (title, keys) in rp.GROUPS.items():
    t, rows = rp.group_page(ctx, g)
    kinds = [rl.SCHEMA[k][0] == "bool" for k in rp._ordered(keys)]
    assert kinds == sorted(kinds), (g, kinds)                                    # all False first, then all True
    texts = [x for x in t.split("\n") if x.startswith("• ")]
    labels = [f"• {rl.LABEL[k]}:" for k in rp._ordered(keys)]
    assert all(texts[i].startswith(labels[i]) for i in range(len(labels))), (g, [(texts[i], labels[i]) for i in range(len(labels)) if not texts[i].startswith(labels[i])])
    btn = [r for r in rows if r[0]["callback_data"].startswith(("adm:rnd:tg:", "adm:rnd:sn:", "adm:rnd:cy:"))]
    order = [re.search(r"(?:tg|sn|cy):(\w+)", r[0]["callback_data"]).group(1) for r in btn]
    assert order == rp._ordered(keys), (g, order)
    assert all(len(b["callback_data"]) <= 64 for r in rows for b in r) and len(t) <= 4096
# the static help text lists items in the same order as the page (spot checks)
import admin_texts as T
h = T.block("rnd_sg_rs"); assert h.index("Candidates per run") < h.index("24h non-stop") < h.index("Auto-improve") < h.index("Auto-enable new")
h = T.block("rnd_sg_ev"); assert h.index("Check effect after") < h.index("Auto-rollback")
print("order ok")

# 3) detailed reports
res = {"cycle": 7, "generated": "2026-11-14 18:00 UTC", "tested": 40, "prescreened": 18, "improved": 6,
       "gate": {"g_win": 45, "g_pf_min": 1.2, "g_pf_max": 1.6, "g_rr": 2.5, "g_dd": 7, "g_streak": 6},
       "ai": {"providers": {"gemini": {"used": True, "n": 4, "model": "gemini-3.6-flash"}, "openai": {"error": "HTTP 429 — اعتبار <پر> شده"}, "groq": {"skipped": "no key"}},
              "analysis": ["wide stops lose <b>fees</b>"]},
       "ai_hints": [{"k": 1}],
       "slots": {"RD1": {"status": "PASS", "spec_id": "a", "name": "Alpha", "tf": "15m", "n": 200, "avg_r": 0.12, "pf": 1.4, "win": 47, "t": 2.4, "best_ladder": "H3", "gate": {"passed": 12, "total": 12, "checks": []}},
                 "RD2": {"status": "FAIL", "spec_id": "b", "name": "Beta", "tf": "1h", "n": 90, "avg_r": -0.01, "pf": 0.9, "win": 40, "t": -0.2, "gate": {"passed": 5, "total": 12, "checks": [{"n": "Win rate", "ok": False}, {"n": "PF", "ok": False}]}},
                 "RD3": {"status": "EMPTY"}},
       "archive": [{"name": "Gamma", "status": "FAIL", "fails": ["win", "dd"]}]}
msgs = rep.cycle_reports(res, {"RD1": {"spec_id": "zzz", "status": "FAIL"}}, {"new": ["RD1"], "dropped": ["RD2"]}, rl.settings_of({}))
txt = "\n".join(msgs)
for need in ("گزارش چرخه‌ی پژوهش #7", "gemini", "openai", "تحلیل", "RD1", "جایگزین شد", "Win rate", "Gamma", "پیشنهاد فعال‌سازی", "قفل خودکار", "معیار قبولی"):
    assert need in txt, need
assert all(len(m) <= 3900 for m in msgs) and not re.search(r"<(?!/?(b|i|code)>)", txt), txt
assert "&lt;پر&gt;" in txt and "&lt;b&gt;fees" in txt                              # hostile text escaped
none_ai = dict(res, ai={"providers": {}}); assert "هیچ هوش مصنوعی" in "\n".join(rep.cycle_reports(none_ai, {}, {}, rl.settings_of({})))
allfail = dict(res, ai={"providers": {"openai": {"error": "HTTP 402"}}}); assert "هیچ هوش مصنوعی‌ای جواب نداد" in "\n".join(rep.cycle_reports(allfail, {}, {}, rl.settings_of({})))
ap = rep.explain_applied({"kind": "ctl", "desc": "Set X to 3", "ev": ev, "src": "stat"}, rl.settings_of({}))
assert "چرا" in ap and "پایش" in ap and "n=80" in ap
mr = rep.modules_report({"generated": "g", "modules": {"TPB@15m": {"status": "PASS", "best_ladder": "H3"}, "MRA@1h": {"status": "FAIL", "gate": {"checks": [{"n": "PF", "ok": False}]}}}}, {"MRA@1h": "PASS"}, dict(rl.settings_of({}), modules_apply=True, mode="auto"))
assert "PASS → <b>FAIL</b>" in "\n".join(mr) or "PASS → <b>FAIL" in "\n".join(mr)
print("reports ok")

# 4) tick sends the research report once per new results, then stays quiet
import rnd_lab
sent = []
env = {"data_dir": tmp, "controls_path": os.path.join(tmp, "bot_controls.json"), "load_history": lambda: [], "notify": lambda txt, rows=None: sent.append(txt)}
st = rl.load_state(tmp); st["settings"]["mode"] = "semi"; rl.save_state(st, tmp)
res["ts"] = time.time()
rl.write_json(rl.RESULTS_NAME, res, tmp)
r1 = rnd_lab.tick(env, force=True); assert any("گزارش چرخه‌ی پژوهش #7" in m for m in sent), (sent, r1)
n1 = len(sent); sent.clear()
r2 = rnd_lab.tick(env, force=True); assert not any("گزارش چرخه‌ی پژوهش" in m for m in sent), sent
print("tick report ok")

# 5) SGNL / module duplicate guards
import strategy_modules as sm
op = [{"module": "SWP", "symbol": "LTC/USD", "side": "SELL", "tf": "4H", "status": "open"}]
assert sm._dup_reason(op, "HDV3", "LTC/USD", "4h", "SELL", 46.09, 0) == "dup_same_tf:SWP"          # case-insensitive tf
assert sm._dup_reason(op, "HDV3", "LTC/USD", "1h", "SELL", 46.09, 0) is None                        # other tf is legal
sdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "SGNL-Fa_V12", "SGNL-Fa V12")
print("module dup ok")

# 6) one candle, two modules (different ladders) => only the first is published
import shared_controls as sc
names = list(sm.MODULE_CFG)[:2]
sent, alerts = [], []
deps = {"send": lambda c, x=None: (sent.append(c) or len(sent) + 5), "send_photo": None, "alert": lambda k, t: alerts.append(k), "chart": None, "controls": lambda: {}, "verdicts": lambda: {},
        "persist": False, "write_json": lambda *a: None, "path": lambda x: x, "klines": lambda *a: []}
stt = {"trades": {}, "seen": []}
ctl = sc.load("sgnl", os.path.join(tmp, "c.json"))
sig = {"side": "SELL", "sl": 49.62, "bar_ms": 1_700_000_000_000, "price": 46.09, "regime": None}
now = int(time.time() * 1000)
r1 = sm._publish(deps, stt, names[0], "LTC/USD", dict(sm.MODULE_CFG[names[0]], tf="4h"), sig, 46.09, "ALT_000946", now, ctl, sc, [], {})
r2 = sm._publish(deps, stt, names[1], "LTC/USD", dict(sm.MODULE_CFG[names[1]], tf="4h"), sig, 46.09, "ALT_000947", now, ctl, sc, [], {})
assert r1 and not r2 and len(sent) == 1 and any(a.startswith("dupblock") for a in alerts)
r3 = sm._publish(deps, stt, names[1], "LTC/USD", dict(sm.MODULE_CFG[names[1]], tf="4h"), dict(sig, bar_ms=sig["bar_ms"] + 14400000), 46.0, "ALT_000948", now, ctl, sc, [], {})
assert r3 and len(sent) == 2                                                      # next candle is a new signal
print("publish dup ok")
print("ROUND9 TESTS PASSED")

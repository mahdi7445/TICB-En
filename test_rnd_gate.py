"""R&D: Pass-criteria gate, auto-improve loop, multi-AI team, panel numeric UI (no network: HTTP is stubbed)."""
import json, os, random, sys, tempfile, types
tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rnd_lab as rl, rnd_research as rr, rnd_ai, rnd_panel as rp, rnd_dsl

g = rl.gate_of(rl.settings_of({}))
assert (g["win"], g["pf_min"], g["pf_max"], g["rr"], g["dd"], g["streak"]) == (45.0, 1.2, 1.6, 2.5, 7.0, 6), g

def stat(**kw):
    d = dict(n=300, avg=0.12, t=3.0, win=48.0, pf=1.35, dd=5.0, streak=4, train=0.1, test=0.14, hi=0.05, ok=True, sem="live4")
    d.update(kw); return d
def chk(k, x, gg=g):
    return {c["k"]: c["ok"] for c in rr.gate_checks(k, x, gg)}

ok = chk("H3", stat())                          # H3 farthest target 8R
assert all(v is not False for v in ok.values()), ok
assert chk("H3", stat(win=40))["win"] is False
assert chk("H3", stat(pf=1.1))["pf_min"] is False
assert chk("H3", stat(pf=2.0))["pf_max"] is False                      # overfit cap
assert chk("H3", stat(dd=9.0))["dd"] is False                          # 9R x 1% = 9% > 7%
assert chk("H3", stat(streak=6))["streak"] is False and chk("H3", stat(streak=5))["streak"] is True
assert chk("Q2", stat())["rr"] is True and chk("B2", stat())["rr"] is False   # single 2R target < 2.5R
assert chk("H3", stat(hi=-0.01))["stress"] is False
assert chk("H3", stat(train=-0.1))["split"] is False
assert chk("H3", stat(dd=None, streak=None))["dd"] is None            # old verdict without dd/streak: n/a, not a failure
# the admin can relax / tighten: same stats, different verdict
g2 = dict(g, win=50.0); assert chk("H3", stat(), g2)["win"] is False
g3 = dict(g, risk=2.0); assert chk("H3", stat(dd=5.0), g3)["dd"] is False
print("gate checks ok")

# own_ladder_gate picks the best ladder that satisfies every criterion
v = {"verdict": "PASS", "stats": {"CLASSIC": stat(avg=0.30, win=60.0, pf=1.9), "H3": stat(avg=0.12), "Q2": stat(avg=0.20, dd=12.0)}}
o = rr.own_ladder_gate(v, g)
assert o["net_gate_ok"] is True and o["best_ladder"] == "H3", (o.get("best_ladder"), o.get("net_gate_why"))
assert o["gate"]["passed"] == o["gate"]["total"] and o["dd_pct"] == 5.0
bad = rr.own_ladder_gate({"verdict": "PASS", "stats": {"H3": stat(win=38.0, pf=1.15)}}, g)
assert bad["net_gate_ok"] is False and any("Win rate" in w for w in bad["net_gate_why"]), bad["net_gate_why"]
assert rr.own_ladder_gate({"verdict": "PASS"}, g)["net_gate_ok"] is None
off = rr.own_ladder_gate({"verdict": "PASS", "stats": {"H3": stat(win=10.0, pf=0.5)}}, dict(g, on=False))
assert off["net_gate_ok"] is None and "gate" not in off                      # master switch OFF: criteria not enforced
assert rl.gate_of(rl.settings_of({"settings": {"g_on": False}}))["on"] is False and rl.settings_of({})["g_on"] is True
assert rnd_ai.gate_text(dict(g, on=False)) == "" and "PASS CRITERIA" in rnd_ai.gate_text(g)
print("ladder gate ok")

# contradictions are reported (win>=45 & payoff 2.5 => PF>=2.05 > cap 1.6)
assert not rl.gate_conflicts(g) and rl.gate_conflicts(dict(g, payoff=2.5))
print("conflict warning ok")

# improvement loop
r = random.Random(3)
spec = rnd_dsl.validate_spec({"name": "Pull", "tfs": ["15m"], "all": [["close", ">", "ema:50"], ["rsi:14", "<", 45], ["volr:20", ">", 1.2]], "stop": {"kind": "atr", "k": 1.5}, "cooldown": 3})[2]
vs = rr.improve_variants(spec, ["win", "streak", "dd"], r, 6)
assert vs and all(x["origin"] == "improve" and rr.spec_hash(x) != rr.spec_hash(spec) for x in vs)
assert all(rnd_dsl.validate_spec(x)[0] for x in vs)
vs2 = rr.improve_variants(spec, ["n"], r, 4); assert vs2
vs3 = rr.improve_variants(spec, ["pf_max"], r, 4); assert vs3
prev = {"slots": {"RD1": {"status": "FAIL", "spec_id": "a1", "name": "Pull", "fail_reasons": ["x"], "win": 40}, "RD2": {"status": "PASS", "spec_id": "b"}}}
w = rr.weak_list(prev, [{"spec_id": "a1", "spec": spec, "fails": ["win"]}])
assert len(w) == 1 and w[0]["fails"] == ["win"]
print("improve loop ok", len(vs))

# multi-AI: stubbed providers
for k in list(os.environ):
    if k.endswith("_API_KEY") or k.endswith("_APIKEY") or k == "ANTHROPIC_KEY":
        del os.environ[k]
assert rnd_ai.providers_available() == []
os.environ["ANTHROPIC_API_KEY"] = " Bearer sk-test "; os.environ["OPENAI_API_KEY"] = "k2"; os.environ["GEMINI_API_KEY"] = "k3"
assert rnd_ai._key() == "sk-test" and set(rnd_ai.providers_available()) == {"claude", "openai", "gemini"}
good = json.dumps({"analysis": ["too many tiny stops"], "strategies": [{"name": "A", "tfs": ["1h"], "all": [["close", ">", "ema:100"], ["adx:14", ">", 20]], "stop": {"kind": "atr", "k": 2}}],
                   "fixes": [dict(spec, parent="Pull")], "tuning": [{"section": "ticb", "key": "nat_min_stop_pct", "value": 0.3, "why": "fees"}]})
def fake(pid, system, user, model="", max_tokens=0, timeout=0):
    if pid == "gemini": return None, "HTTP 404 — نام مدل اشتباه است"
    assert "PASS CRITERIA" in user and "win rate >= 45%" in user, "criteria must be sent to every AI"
    return good, ""
rnd_ai.call_provider = fake
out = rnd_ai.propose_multi({}, {}, 4, "claude-sonnet-5-5", {p: 5 for p in rnd_ai.PROVIDERS}, list(rnd_ai.PROVIDERS), g, w, 3)
assert out["used"] and out["providers"]["claude"]["used"] and out["providers"]["openai"]["used"], out["providers"]
assert out["providers"]["gemini"]["error"].startswith("HTTP 404") and out["providers"]["grok"]["skipped"] == "no key"
origins = sorted({s["origin"] for s in out["specs"]}); assert origins == ["ai-fix:claude", "ai-fix:openai", "ai:claude", "ai:openai"], origins
assert out["calls"] == {"claude": 1, "openai": 1, "gemini": 1} and len(out["hints"]) == 2
out2 = rnd_ai.propose_multi({}, {}, 4, "claude-sonnet-5-5", {"claude": 0, "openai": 1, "gemini": 0}, ["claude", "openai"], g)
assert out2["providers"]["claude"]["skipped"] == "daily budget used up" and out2["calls"] == {"openai": 1}
out3 = rnd_ai.propose_multi({}, {}, 4, "m", {"claude": 5}, ["grok"], g)
assert not out3["used"] and out3["error"]
print("multi-AI ok")

# real request shapes (stub urlopen)
import urllib.request
seen = {}
class R:
    def __init__(s, b): s.b = b
    def read(s): return json.dumps(s.b).encode()
    def __enter__(s): return s
    def __exit__(s, *a): return False
def uo(req, timeout=0):
    seen[req.full_url] = (dict(req.headers), json.loads(req.data))
    if "anthropic" in req.full_url: return R({"content": [{"type": "text", "text": "ok"}]})
    if "googleapis" in req.full_url: return R({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    return R({"choices": [{"message": {"content": "ok"}}]})
urllib.request.urlopen = uo
import importlib; importlib.reload(rnd_ai)
for pid in ("claude", "openai", "gemini"):
    assert rnd_ai.ping_provider(pid)[0], pid
h, b = seen["https://api.openai.com/v1/chat/completions"]; assert h["Authorization"] == "Bearer k2" and "max_completion_tokens" in b
h, b = seen["https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"]; assert h["X-goog-api-key"] == "k3" and "systemInstruction" in b
h, b = seen["https://api.anthropic.com/v1/messages"]; assert h["X-api-key"] == "sk-test" and b["system"]
os.environ["GEMINI_MODEL"] = "gemini-x"; assert rnd_ai.provider_model("gemini") == "gemini-x"
print("provider requests ok")

# panel: numeric rows show the VALUE first; typed entry; reset; AI page
ctx = {"data_dir": tmp, "controls_path": os.path.join(tmp, "bot_controls.json"), "load_history": lambda: [], "sent": [], "await": {}}
ctx["edit"] = lambda c, m, t, rows=None: ctx.__setitem__("last", (t, rows))
ctx["send"] = lambda c, t, rows=None: ctx.__setitem__("last", (t, rows))
ctx["set_await"] = lambda u, k, d=None: ctx["await"].update(kind=k, data=d or {})
ctx["clear_await"] = lambda u: ctx["await"].clear()
ctx["parse_num"] = lambda t: float(t.replace(",", ".")) if t.replace(",", ".").replace(".", "", 1).replace("-", "", 1).isdigit() else None
t, rows = rp.group_page(ctx, "gt")
assert "Min win rate" in t and "Pass criteria" in t
mids = [r[1]["text"] for r in rows if len(r) == 3]
assert mids and all(m.split(" · ")[0].replace("%", "").replace("R", "").replace(".", "", 1).isdigit() for m in mids), mids
assert any(m.startswith("45%") for m in mids) and any(m.startswith("1.6") for m in mids) and all(len(m) <= 30 for m in mids)
assert len(t) <= 4096
p = lambda *a: rp.handle_callback(["adm", "rnd"] + list(a), 1, 2, 3, ctx)
p("sn", "g_win", "1"); assert rl.settings_of(rl.load_state(tmp))["g_win"] == 46.0
p("ty", "g_dd"); assert ctx["await"]["kind"] == "adm_rnd_num"
rp.handle_text(1, 3, "9", dict(ctx["await"]), ctx); assert rl.settings_of(rl.load_state(tmp))["g_dd"] == 9.0
rp.handle_text(1, 3, "999", {"data": {"key": "g_dd"}}, ctx); assert rl.settings_of(rl.load_state(tmp))["g_dd"] == 50.0      # clamped
rp.handle_text(1, 3, "abc", {"data": {"key": "g_dd"}}, ctx); assert "معتبر" in ctx["last"][0]
p("grs", "gt"); s = rl.settings_of(rl.load_state(tmp)); assert s["g_win"] == 45.0 and s["g_dd"] == 7.0
p("sn", "g_pf_max", "-1"); p("sn", "g_pf_max", "-1")
p("sn", "ai_calls_day", "1"); assert rl.settings_of(rl.load_state(tmp))["ai_calls_day"] == 14
t, rows = rp.ai_page(ctx); assert "Claude" in t and "OpenAI" in t and "no key" in t and any("Test all" in b["text"] for r in rows for b in r)
rnd_ai.ping_provider = lambda pid, cm="": (pid != "openai", f"{pid} msg")
p("aitest", "all"); assert "❌ openai msg" in ctx["last"][0] and "✅ claude msg" in ctx["last"][0]
t, rows = rp.settings_page(ctx); assert any("Pass criteria" in b["text"] for r in rows for b in r)
assert all(len(b["callback_data"]) <= 64 for r in rows for b in r)
for gk in rp.GROUPS:
    t, rows = rp.group_page(ctx, gk); assert len(t) <= 4096 and all(len(b["callback_data"]) <= 64 for r in rows for b in r), gk
t, rows = rp.group_page(ctx, "gt"); assert rows[0][0]["text"].startswith("✅ Enforce")
p("tg", "g_on"); t, rows = rp.group_page(ctx, "gt"); assert rows[0][0]["text"].startswith("⬜ Enforce") and "غیرفعال" in t
p("tg", "g_on")
# hostile provider error text (a '<' used to make Telegram reject the whole page => "button does nothing")
res_ = rl.read_json(rl.RESULTS_NAME, {}, tmp) or {}
res_["ai"] = {"error": "bad <NAME>_MODEL & <b>x", "providers": {"openai": {"error": "HTTP 404 <html>oops</html>"}}, "analysis": ["use <ema> & rsi"]}
rl.write_json(rl.RESULTS_NAME, res_, tmp)
t, rows = rp.ai_page(ctx, "❌ x <y>")
import re as _re
assert not _re.search(r"<(?!/?b>|/?i>)", t), t
print("panel ok")

# finalize end-to-end with a raw verdict: criteria come from the admin's settings and end up in results
import shutil
data = tempfile.mkdtemp(); st = rl.load_state(data); st["settings"]["g_win"] = 55.0; rl.save_state(st, data)
spec = rnd_dsl.validate_spec({"name": "E2E", "tfs": ["1h"], "all": [["close", ">", "ema:100"]], "stop": {"kind": "atr", "k": 2}})[2]
rl.write_json(rl.STRATS_NAME, {"slots": {"RD1": {"spec": spec, "spec_id": rr.spec_hash(spec)}, "RD2": {"spec": spec, "spec_id": "zz"}}}, data)
raw = {"RD1": {"verdict": "PASS", "tf": "1h", "stats": {"H3": stat()}}, "RD2": {"verdict": "PASS", "tf": "1h", "stats": {"H3": stat(win=56.0, pf=1.55)}}}
json.dump(raw, open(os.path.join(data, "raw.json"), "w"))
a = types.SimpleNamespace(raw=os.path.join(data, "raw.json"), data=data)
os.chdir(data); a.raw = "raw.json"
rr.cmd_finalize(a)
res = rl.read_json(rl.RESULTS_NAME, {}, data); sl_ = res["slots"]
assert sl_["RD1"]["status"] == "FAIL" and "win" in [c["k"] for c in sl_["RD1"]["gate"]["checks"] if c["ok"] is False]      # 48% < admin's 55%
assert sl_["RD2"]["status"] == "PASS" and sl_["RD2"]["gate"]["passed"] == sl_["RD2"]["gate"]["total"] and res["gate"]["win"] == 55.0
assert rl.read_json(rl.VERDICTS_NAME, {}, data)["RD1"]["verdict"] == "FAIL"
arch = {x["spec_id"]: x for x in res["archive"]}; assert arch[rr.spec_hash(spec)]["fails"] == ["win"]
import subprocess
o = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(rr.__file__)), "rnd_research.py"), "gate-env", "--data", data], capture_output=True, text=True).stdout.strip()
assert o == "RND_GATE_PF=1.2 RND_GATE_WIN=55", o
print("finalize ok")
# ---- 🧩 all-modules re-test: plan / finalize / apply / panel / notification
import subprocess
md = tempfile.mkdtemp(); st = rl.load_state(md); st["settings"]["mode"] = "auto"; rl.save_state(st, md)
raw = {"_meta": {"x": 1},
       "TPB@15m": {"verdict": "PASS", "tf": "15m", "stats": {"H3": stat()}},
       "MRA@1h": {"verdict": "PASS", "tf": "1h", "stats": {"H3": stat(win=40.0, pf=1.15)}},       # lab PASS but below the admin's criteria
       "BZ4H@4h": {"verdict": "FAIL", "tf": "4h", "stats": {"H3": stat(avg=-0.1, t=-1.0, pf=0.8, win=30.0)}},
       "RFD@1h": {"verdict": "NO_DATA"}}
json.dump(raw, open(os.path.join(md, "raw_m.json"), "w"))
json.dump({"_meta": {"keep": True}, "OLD@1h": {"verdict": "PASS", "best_ladder": "CLASSIC"}, "MRA@1h": {"verdict": "PASS", "best_ladder": "Q2"}}, open(os.path.join(md, "lowtf_verdicts.json"), "w"))
cwd = os.getcwd(); os.chdir(md)
rr.cmd_modules_finalize(types.SimpleNamespace(raw="raw_m.json", data=md)); os.chdir(cwd)
mres = rl.read_json(rl.MODULES_NAME, {}, md)
assert {k: v["status"] for k, v in mres["modules"].items()} == {"TPB@15m": "PASS", "MRA@1h": "LAB", "BZ4H@4h": "FAIL", "RFD@1h": "NA"}, {k: v["status"] for k, v in mres["modules"].items()}
vd = rl.read_json("lowtf_verdicts.json", {}, md)
assert vd["TPB@15m"]["verdict"] == "PASS" and vd["TPB@15m"]["best_ladder"] == "H3"                  # auto mode: applied
assert vd["MRA@1h"]["verdict"] == "FAIL" and vd["MRA@1h"]["best_ladder"] is None                      # locked: below the admin's criteria
assert vd["OLD@1h"]["verdict"] == "PASS" and vd["_meta"] == {"keep": True} and "RFD@1h" not in vd     # untouched: not re-tested
# semi mode: nothing written until the admin taps Apply
md2 = tempfile.mkdtemp(); st = rl.load_state(md2); st["settings"]["mode"] = "semi"; rl.save_state(st, md2)
json.dump(raw, open(os.path.join(md2, "raw_m.json"), "w")); os.chdir(md2)
rr.cmd_modules_finalize(types.SimpleNamespace(raw="raw_m.json", data=md2)); os.chdir(cwd)
assert not os.path.exists(os.path.join(md2, "lowtf_verdicts.json"))
assert rr.apply_modules(md2) == (1, 2) and os.path.exists(os.path.join(md2, "lowtf_verdicts.json"))
# plan: nonstop => due >=1h after the last result; off => never; button request => now
def plan(d, force=False):
    out = os.path.join(d, "gh_out"); open(out, "w").close(); os.environ["GITHUB_OUTPUT"] = out
    rr.cmd_modules_plan(types.SimpleNamespace(data=d, force=force)); return open(out).read().strip()
assert plan(md) == "go=false"                                      # just ran
res_ = rl.read_json(rl.MODULES_NAME, {}, md); res_["ts"] -= 2 * 3600; rl.write_json(rl.MODULES_NAME, res_, md); assert plan(md) == "go=true"
st = rl.load_state(md); st["settings"]["modules_on"] = False; rl.save_state(st, md); assert plan(md) == "go=false"
st = rl.load_state(md); rl.request_modules(st); rl.save_state(st, md); assert plan(md) == "go=true"       # explicit button request wins
assert plan(md, force=True) == "go=true"
# panel
ctx["data_dir"] = md
t, rows = rp.modules_page(ctx); assert "TPB@15m" in t and "MRA@1h" in t and len(t) <= 4096 and "BZ4H@4h" in t
assert all(len(b["callback_data"]) <= 64 for r in rows for b in r)
cb = [b["callback_data"] for r in rows for b in r if b["text"].startswith("✅ TPB")][0]
t, rows = rp.module_detail(ctx, int(cb.split(":")[-1])); assert "TPB@15m" in t and "Win rate" in t and "✅" in t
t, rows = rp.module_detail(ctx, [k for k in ("TPB@15m",)] and sorted(mres["modules"], key=lambda k: 0).index("MRA@1h")); assert "❌" in t           # shows which criteria it fails
t, rows = rp.home_page(ctx) if hasattr(rp, "home_page") else (None, None)
# notification on the next tick
import rnd_lab
sent = []
env = {"data_dir": md, "controls_path": os.path.join(md, "bot_controls.json"), "load_history": lambda: [], "notify": lambda txt, rows=None: sent.append(txt)}
st = rl.load_state(md); st["settings"]["modules_on"] = True; st["settings"]["mode"] = "semi"; rl.save_state(st, md)
r_ = rnd_lab.tick(env, force=True)
assert any("All-modules re-test done" in x for x in sent), (sent, r_)
rl.dispatch_workflow = lambda wf, ref="main", inputs=None, timeout=15.0: (wf == "rnd_modules.yml" and inputs == {"force": "true"}, "stub")
p("mrun"); assert "Started on GitHub Actions" in ctx["last"][0] and any(r.get("kind") == "modules" for r in rl.load_state(md)["requests"])
p("mapply"); assert "Applied to lowtf_verdicts" in ctx["last"][0]
p("mod", "0"); p("mdt", "0"); p("mdt", "99")
print("modules re-test ok")
print("ALL R&D GATE TESTS PASSED")

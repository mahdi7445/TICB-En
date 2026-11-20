"""Strategies page (pagination, ENG as a strategy, button limits), min-stop stepping, profitability quotas, TF-diverse R&D picks. Run: python3 test_strategies_page.py"""
import os, sys, types, tempfile, json
for m in ("mplfinance", "websocket"):
    try: __import__(m)
    except Exception: sys.modules.setdefault(m, types.ModuleType(m))
import shared_controls as sc, admin_panel as ap, strategy_modules as sm, rnd_research as rr

tmp = tempfile.mkdtemp(); ctl = os.path.join(tmp, "bot_controls.json")
ctx = {"controls_path": ctl, "data_dir": tmp}
ap._path = lambda c: ctl
# a verdict file where all 12 R&D slots + 4 old strategies are tradable candidates -> the page must still fit Telegram's limits
vd = {}
for i in range(1, 13):
    vd[f"RD{i}"] = {"verdict": "CAND", "tf": ["5m", "15m", "1h", "4h"][i % 4], "n": 300, "avg_r": 0.12}
sc.load_verdicts = lambda: vd
sc.lowtf_status = lambda m, v=None: "CAND" if str(m).startswith("RD") else "NA"
ap._verdicts_local = lambda: vd
for sec in ("ticb", "sgnl"):
    seen_mods = set()
    pages = 1
    pg = 0
    while pg < pages:
        text, rows = ap.mods_page(sec, ctx, pg)
        n_btn = sum(len(r) for r in rows)
        assert n_btn <= 100, (sec, pg, n_btn)
        assert len(text) <= 4096
        assert all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r), "callback too long"
        cbs = [b["callback_data"] for r in rows for b in r]
        assert any(c.endswith(":rec") for c in cbs) and any(c.endswith(":diag") for c in cbs), "general settings missing"
        assert any("lowtf_max_per_day" in c for c in cbs), "daily cap row missing"
        assert rows[-1][0]["text"].startswith("⬅️ Back") and rows[-1][1]["callback_data"] == "menu:home", rows[-1]
        if pg == 0:
            assert rows[0][0]["callback_data"] == f"adm:{sec}:nat" and "ENG" in rows[0][0]["text"], rows[0]          # main engine = first strategy row
        for r in rows:
            for b in r:
                if ":tg:mod_" in b["callback_data"]:
                    seen_mods.add(b["callback_data"].split(":tg:mod_")[1].split(":")[0])
        pages = max(pages, int(text.split("page ")[1].split("/")[1].split("\n")[0].split()[0]))
        pg += 1
    assert {f"RD{i}" for i in range(1, 13)} <= seen_mods and {"HDV3", "SWP", "TS1"} <= seen_mods, seen_mods
    assert pages >= 2, "list should paginate"
# the separate Main engine button is gone from the bot menu, the page is reachable from the list and goes back to the list
_t, rows = ap.sec_menu("ticb", ctx)
assert not any(b["callback_data"].endswith(":nat") for r in rows for b in r)
_t, rows = ap.nat_page("ticb", ctx)
assert rows[-1][0]["callback_data"] == "adm:ticb:mods"
_t, rows = ap.sd_page("ticb", "ENG", ctx); assert "Main engine" in _t

# min stop: identical stepping for both bots, no float drift
sc.set_value("ticb", "nat_min_stop_pct", 0.30, ctl); sc.set_value("sgnl", "nat_min_stop_pct", 0.30, ctl)
assert ap.STEP["nat_min_stop_pct"] == 0.05
for sec in ("ticb", "sgnl"):
    for _ in range(3): sc.step_value(sec, "nat_min_stop_pct", 0.05, ctl)
    assert sc.load(sec, ctl)["nat_min_stop_pct"] == 0.45, sc.load(sec, ctl)["nat_min_stop_pct"]
    for _ in range(3): sc.step_value(sec, "nat_min_stop_pct", -0.05, ctl)
    assert sc.load(sec, ctl)["nat_min_stop_pct"] == 0.3
# one-time migration unifies old stored values
json.dump({"ticb": {"_profile": "v14-2026-11-08", "nat_min_stop_pct": 0.35, "lowtf_max_per_day": 5}, "sgnl": {"_profile": "v14-2026-11-08", "nat_min_stop_pct": 0.1}}, open(ctl, "w"))
assert sc.load("ticb", ctl)["nat_min_stop_pct"] == 0.3 and sc.load("sgnl", ctl)["nat_min_stop_pct"] == 0.3 and sc.load("ticb", ctl)["lowtf_max_per_day"] == 8
sc.set_value("ticb", "nat_min_stop_pct", 0.45, ctl)              # an admin choice made after the migration is permanent
assert sc.load("ticb", ctl)["nat_min_stop_pct"] == 0.45

# daily quotas follow proven profit, lower timeframe gets more, every valid strategy gets at least 1
w = {"A": sm._module_weight({"score": 0.30, "tf": "5m"}), "B": sm._module_weight({"score": 0.10, "tf": "4h"}), "C": sm._module_weight({"score": 0.20, "tf": "15m", "tier": "candidate"})}
qa, qb, qc = (sm.module_daily_quota(m, 8, w) for m in "ABC")
assert qa > qc >= 1 and qb >= 1 and qa <= 8, (qa, qb, qc)
rw = {f"RD{i}": sm._module_weight({"score": 0.12, "tf": "5m"}) for i in range(1, 13)}
assert all(sm.module_daily_quota(m, 8, rw) >= 1 for m in rw)
assert sm.module_daily_quota("A", 8, {**w, "RD1": 5.0}) == qa          # R&D has its own bucket and does not dilute the old strategies

# R&D picks cover the low timeframes instead of all 4H
def row(i, tf, t): return {"tf": tf, "n": 100, "avg": 0.1, "t": t, "spec": {"name": f"s{i}", "family": f"f{i % 9}", "tfs": [tf]}}
scored = sorted([row(i, "4h", 9.0 - i * 0.01) for i in range(10)] + [row(20 + i, "5m", 3.0 - i * 0.01) for i in range(10)] +
                [row(40 + i, "15m", 2.5 - i * 0.01) for i in range(10)] + [row(60 + i, "1h", 2.0 - i * 0.01) for i in range(10)], key=lambda r: -r["t"])
pick = rr.pick_diverse(scored, 12, {})
tfs = [r["tf"] for r in pick]
assert len(pick) == 12 and tfs.count("5m") >= 3 and tfs.count("15m") >= 3 and tfs.count("4h") <= 3, tfs
assert rr.pick_diverse(scored, 3)[0]["tf"] == "4h"                   # legacy call (no TF quota) unchanged
print("STRATEGIES PAGE TESTS PASSED")

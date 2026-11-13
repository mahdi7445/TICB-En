#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rnd_research.py — چرخه‌ی «پژوهش سنگین» آزمایشگاه R&D (روی GitHub Actions؛ ورک‌فلو rnd_lab.yml).

مراحل:
  plan      آیا الان نوبت پژوهش است؟ (فاصله‌ی تنظیم‌شده یا درخواست دکمه‌ای) → خروجی go=true/false
  prepare   ۱) ایده‌ها: هوش مصنوعی (اگر کلید باشد) + جست‌وجوی هدایت‌شده با «نشتی‌های واقعی معاملات» + جهش/بهینه‌سازیِ برترین‌های قبلی
            ۲) پیش‌غربال «فقط روی ۶۰٪ اولِ» داده (همان بخش train آزمایشگاه؛ ۴۰٪ آخر دست‌نخورده می‌ماند)
            ۳) برترین‌ها در اسلات‌های آزادِ RD1..RD6 قرار می‌گیرند (قهرمان‌های PASS/کاندید جایشان را نگه می‌دارند)
  (ورک‌فلو) strategy_lab.py --gens RD1..RD6 --design روی همه‌ی تایم‌فریم‌ها/نمادها/نردبان‌ها → pickle ها → merge → verdict خام
  finalize  verdict خام → data/rnd_verdicts.json (فقط RD*) + data/rnd_results.json (برای پنل/ربات)

⚠️ هیچ‌چیز مستقیم روی ربات‌ها اعمال نمی‌شود: فقط فایل‌های داده نوشته می‌شوند؛ تصمیم فعال‌سازی با rnd_lab (حالت Semi/Auto) است.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rnd_dsl                                   # noqa: E402
import rnd_lab as rl                             # noqa: E402

SLOTS = rnd_dsl.SLOTS
SYMS_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]
PRE_LADDERS = ("CLASSIC", "H1", "Q2", "H3")
MIN_TRAIN_N = 40
MIN_TRAIN_T = 1.0


def spec_hash(spec: Dict[str, Any]) -> str:
    core = {k: spec.get(k) for k in ("all", "stop", "tfs", "cooldown", "min_risk_pct", "max_stop_atr")}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:10]


# ----------------------------------------------------------------------------- تولید ایده
_FILTERS = [
    ["htf:4h:trend", "==", 1], ["htf:1d:trend", "==", 1], ["volr:20", ">", 1.2], ["volr:20", ">", 1.5], ["adx:14", ">", 20],
    ["adx:14", ">", 25], ["atrp:14", ">", 0.25], ["rng", ">", 0.8], ["body", ">", 0.4],
]


def _stop(r: random.Random) -> Dict[str, Any]:
    k = r.choice(["atr", "atr", "swing", "candle"])
    if k == "atr":
        return {"kind": "atr", "k": r.choice([1.0, 1.5, 2.0, 2.5, 3.0])}
    if k == "swing":
        return {"kind": "swing", "n": r.choice([5, 8, 12, 20]), "buf": r.choice([0.1, 0.2, 0.3])}
    return {"kind": "candle", "buf": r.choice([0.1, 0.2, 0.4])}


def _tfs(r: random.Random) -> List[str]:
    pool = ["15m", "1h", "4h", "5m"]
    return sorted(r.sample(pool, r.choice([1, 2, 2, 3])), key=lambda x: ["5m", "15m", "1h", "4h"].index(x))


def _t_pullback(r):
    slow, fast = r.choice([100, 200]), r.choice([20, 21, 34, 50])
    return "pullback", [["close", ">", f"ema:{slow}"], [f"ema:{fast}", ">", f"ema:{slow}"], ["low", "<", f"ema:{fast}"], ["close", ">", f"ema:{fast}"], ["rsi:14", "<", r.choice([40, 45, 50])]]


def _t_breakout(r):
    n = r.choice([10, 20, 30, 50])
    return "breakout", [["close", ">", f"hh:{n}"], ["volr:20", ">", r.choice([1.3, 1.6, 2.0])], ["close", ">", f"ema:{r.choice([50, 100, 200])}"], ["closepos", ">", r.choice([0.6, 0.7, 0.8])]]


def _t_meanrev(r):
    return "meanrev", [["bbpos:20", "<", r.choice([0.02, 0.05, 0.1])], ["rsi:14", "<", r.choice([25, 30, 35])], [r.choice(["pin", "engulf"]), "==", 1], ["close", ">", f"ema:{r.choice([100, 200])}"]]


def _t_momentum(r):
    return "momentum", [[f"ret:{r.choice([3, 5, 8])}", ">", r.choice([1.0, 1.5, 2.5])], ["rng", ">", r.choice([1.2, 1.5, 2.0])], ["closepos", ">", 0.75], ["volr:20", ">", r.choice([1.2, 1.5])]]


def _t_squeeze(r):
    return "squeeze", [["atrp:14", "<", r.choice([0.35, 0.5, 0.8])], ["close", ">", f"hh:{r.choice([10, 20])}"], ["volr:20", ">", r.choice([1.2, 1.5])], ["adx:14", "<", r.choice([22, 28])]]


def _t_cross(r):
    f, s = r.choice([(9, 21), (12, 26), (20, 50)])
    return "cross", [[f"ema:{f}", "x>", f"ema:{s}"], ["adx:14", ">", r.choice([18, 22])], ["close", ">", f"ema:{r.choice([100, 200])}"]]


def _t_wick(r):
    e = r.choice([21, 50])
    return "wick", [["low", "<", f"ema:{e}"], ["close", ">", f"ema:{e}"], ["lwick", ">", r.choice([0.45, 0.55])], [f"ema:{e}", ">", f"ema:{r.choice([100, 200])}"]]


def _t_inside(r):
    return "inside", [["inside", "==", 1], ["close", ">", f"ema:{r.choice([50, 100])}"], [f"slope:{r.choice([21, 34])}", ">", r.choice([0.02, 0.05])]]


TEMPLATES = [_t_pullback, _t_breakout, _t_meanrev, _t_momentum, _t_squeeze, _t_cross, _t_wick, _t_inside]


def _clean(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ok, _, c = rnd_dsl.validate_spec(spec)
    return c if ok else None


def heuristic_candidates(r: random.Random, n: int, hints: Dict[str, Any]) -> List[Dict[str, Any]]:
    out, tries = [], 0
    while len(out) < n and tries < n * 6:
        tries += 1
        fam, conds = r.choice(TEMPLATES)(r)
        pool = list(_FILTERS)
        r.shuffle(pool)
        # فیلترهای هدایت‌شده با نشتی‌های واقعی: ضد-روند/حجم کم/استاپ ریز در معاملات واقعی ضرر می‌دادند
        extra = pool[:r.choice([0, 1, 1, 2])]
        if hints.get("counter_trend") and not any(str(c[0]).startswith("htf:") for c in conds + extra):
            extra.append(["htf:4h:trend", "==", 1])
        if hints.get("low_volume") and not any(str(c[0]).startswith("volr") for c in conds + extra):
            extra.append(["volr:20", ">", 1.2])
        allc = conds + [e for e in extra if e[0] not in {c[0] for c in conds}]
        spec = {"name": f"{fam.title()} #{r.randint(100, 999)}", "family": fam, "tfs": _tfs(r), "all": allc[:7], "stop": _stop(r),
                "cooldown": r.choice([3, 6, 12]), "min_risk_pct": r.choice([0.2, 0.3, 0.5]) if not hints.get("tiny_stops") else r.choice([0.4, 0.5, 0.8]),
                "why": f"{fam} idea (guided random search)", "origin": "search"}
        c = _clean(spec)
        if c:
            out.append(c)
    return out


def mutate(spec: Dict[str, Any], r: random.Random) -> Optional[Dict[str, Any]]:
    s = json.loads(json.dumps(spec))
    s["origin"] = "mutation"
    s["parent"] = spec_hash(spec)
    roll = r.random()
    conds = s["all"]
    if roll < 0.55:                                   # جابه‌جایی یک آستانه‌ی عددی
        idx = [i for i, c in enumerate(conds) if isinstance(c[2], (int, float)) and not isinstance(c[2], bool) and c[1] in (">", "<", ">=", "<=")]
        if idx:
            i = r.choice(idx)
            v = float(conds[i][2]) * r.choice([0.7, 0.85, 1.15, 1.3])
            conds[i][2] = round(v, 3 if abs(v) < 5 else 1)
    elif roll < 0.75:                                 # تغییر حد ضرر
        s["stop"] = _stop(r)
    elif roll < 0.9 and len(conds) < 7:               # افزودن فیلتر
        f = r.choice(_FILTERS)
        if f[0] not in {c[0] for c in conds}:
            conds.append(list(f))
    elif len(conds) > 2:                              # حذف یک شرط (ساده‌سازی)
        conds.pop(r.randrange(len(conds)))
    s["name"] = (str(s.get("name", "R&D")).split(" ·")[0] + " · v" + str(r.randint(2, 9)))[:60]
    return _clean(s)


def hints_from_diag(diag: Dict[str, Any]) -> Dict[str, Any]:
    h: Dict[str, Any] = {}
    for f in (diag.get("findings") or []):
        t = (f.get("text") or "").lower()
        if f.get("dim") == "stop" or "fee" in t:
            h["tiny_stops"] = True
        if f.get("dim") == "side":
            h["counter_trend"] = True
    return h


# ----------------------------------------------------------------------------- داده و پیش‌غربال
def build_series(m: Dict[str, Any], days: Dict[str, int], sl) -> Dict[str, Dict[str, Any]]:
    """همان منطق main() آزمایشگاه: سری هر تایم‌فریم از داده‌ی خام."""
    series: Dict[str, Dict[str, Any]] = {}
    if "5m" in m:
        series["5m"] = m["5m"]
    d15 = m["15m"]
    keep15 = d15["t"] >= d15["t"][-1] - days["15m"] * 86_400_000
    series["15m"] = {k: v[keep15] for k, v in d15.items()}
    h1 = sl.resample(d15, 3_600_000)
    k1 = h1["t"] >= h1["t"][-1] - days["1h"] * 86_400_000
    series["1h"] = {k: v[k1] for k, v in h1.items()}
    h4 = sl.resample(d15, 14_400_000)
    k4 = h4["t"] >= h4["t"][-1] - days["4h"] * 86_400_000
    series["4h"] = {k: v[k4] for k, v in h4.items()}
    return series


def sim_train(spec: Dict[str, Any], d: Dict[str, Any], tf: str, fee: float, sl, ls, frac: float = 0.6) -> List[Tuple[float, float, float]]:
    """معاملات پیش‌غربال روی ۶۰٪ اول: [(t_ms, net_R_بهترین_نردبان_نمونه, stop%)] — یک معامله در هر زمان (مثل آزمایشگاه)."""
    step = sl.STEP_MS[tf]
    n_all = len(d["c"])
    cut = int(n_all * frac)
    if cut < 400:
        return []
    dt = {k: v[:cut] for k, v in d.items()}
    n = cut
    rnd_dsl.set_override("RD1", spec)
    try:
        htf = ls.build_htf(dt, step)
        idx, side, nsl, _ntp = ls.run("RD1", dt, step, htf)
    finally:
        rnd_dsl.set_override("RD1", None)
    if len(idx) == 0:
        return []
    atr = sl.vb.atr_series(dt)
    FAVb, ADVb, CLSb = dt["h"], dt["l"], dt["c"]
    FAVs, ADVs, CLSs = -dt["l"], -dt["h"], -dt["c"]
    rows = []
    busy = -1
    for j in range(len(idx)):
        i = int(idx[j])
        if i <= busy or i + 1 >= n or math.isnan(atr[i]):
            continue
        s_ = int(side[j])
        e = float(dt["o"][i + 1])
        dist = (e - nsl[j]) if s_ == 1 else (nsl[j] - e)
        if not dist > 0 or dist / e < 0.00005:
            continue
        last = min(n - 1, i + 1 + sl.MAX_HOLD)
        if s_ == 1:
            ex_e, F, A, C = e, FAVb, ADVb, CLSb
        else:
            ex_e, F, A, C = -e, FAVs, ADVs, CLSs
        best = None
        held_max = 1
        for lk in PRE_LADDERS:
            res = sl._sim_exit(lk, ex_e, dist, F, A, C, i + 1, last, float("nan"))
            if res is None:
                continue
            g, held = res
            held_max = max(held_max, held)
            net = g - fee / (dist / e * 100.0)
            if best is None or net > best:
                best = net
        if best is None:
            continue
        rows.append((float(dt["t"][i + 1]), float(best), dist / e * 100.0))
        busy = i + held_max
    return rows


def _t_stat(v: List[float]) -> Tuple[float, float]:
    a = np.asarray(v, float)
    if len(a) < 3:
        return 0.0, 0.0
    sd = a.std(ddof=1)
    return float(a.mean()), float(a.mean() / (sd / math.sqrt(len(a)))) if sd > 0 else 0.0


def prescreen(cands: List[Dict[str, Any]], datasets: Dict[str, Dict[str, Dict[str, Any]]], fee: float, sl, ls, log=print) -> List[Dict[str, Any]]:
    """datasets = {symbol: {tf: candle-dict}}. نتیجه: لیست کاندیدها با {spec, tf, n, avg, t} بر اساس t روی train."""
    scored = []
    for k, spec in enumerate(cands):
        best = None
        for tf in spec["tfs"]:
            pooled: List[float] = []
            for sym, series in datasets.items():
                d = series.get(tf)
                if d is None or len(d["c"]) < 800:
                    continue
                try:
                    pooled += [x[1] for x in sim_train(spec, d, tf, fee, sl, ls)]
                except Exception as e:  # noqa: BLE001
                    log(f"[prescreen] {spec.get('name')} {tf} {sym}: {e}")
            if len(pooled) < MIN_TRAIN_N:
                continue
            avg, t = _t_stat(pooled)
            row = {"tf": tf, "n": len(pooled), "avg": round(avg, 4), "t": round(t, 3)}
            if best is None or row["t"] > best["t"]:
                best = row
        if best:
            scored.append(dict(best, spec=dict(spec, tfs=[best["tf"]] if len(spec["tfs"]) == 1 else spec["tfs"])))
        if (k + 1) % 10 == 0:
            log(f"[prescreen] {k + 1}/{len(cands)} candidates")
    scored.sort(key=lambda r: -r["t"])
    return scored


# ----------------------------------------------------------------------------- اسلات‌ها
def free_slots(results: Dict[str, Any], strategies: Dict[str, Any], blacklist: List[str]) -> List[str]:
    """اسلات‌های قهرمان (PASS/کاندید فعلی) دست‌نخورده؛ بقیه آزادند."""
    free = []
    for s in SLOTS:
        rec = (results.get("slots") or {}).get(s) or {}
        has = bool((strategies.get(s) or {}).get("spec"))
        keep = has and rec.get("status") in ("PASS", "CAND") and rec.get("spec_id") == (strategies.get(s) or {}).get("spec_id") \
            and rec.get("spec_id") not in blacklist
        if not keep:
            free.append(s)
    return free


def pick_diverse(scored: List[Dict[str, Any]], k: int) -> List[Dict[str, Any]]:
    out, fam = [], {}
    for r in scored:
        if r["t"] < MIN_TRAIN_T or r["avg"] <= 0:
            continue
        f = r["spec"].get("family") or r["spec"].get("name", "?").split(" ")[0]
        if fam.get(f, 0) >= 2:
            continue
        fam[f] = fam.get(f, 0) + 1
        out.append(r)
        if len(out) >= k:
            break
    return out


# ----------------------------------------------------------------------------- فرمان‌ها
def _args_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def cmd_plan(a) -> int:
    st = rl.load_state(a.data)
    cfg = rl.settings_of(st)
    res = rl.read_json(rl.RESULTS_NAME, {}, a.data)
    now = time.time()
    last = float((res or {}).get("ts", 0))
    pending = [r for r in st.get("requests", []) if not r.get("done") and float(r.get("t", 0)) > last]
    hrs = rl.effective_research_h(cfg)
    go = a.force or bool(pending) or (cfg["mode"] != "off" and now - last >= hrs * 3600)
    if cfg["mode"] == "off" and not a.force and not pending:
        go = False
    print(f"[plan] mode={cfg['mode']} last={rl.now_iso(last) if last else 'never'} pending_requests={len(pending)} -> go={go}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"go={'true' if go else 'false'}\n")
    return 0


def cmd_prepare(a) -> int:
    import strategy_lab as sl
    import lowtf_strategies as ls
    import rnd_ai
    r = random.Random(a.seed or int(time.time()))
    st = rl.load_state(a.data)
    cfg = rl.settings_of(st)
    diag = rl.read_json(rl.DIAG_NAME, {}, a.data)
    prev = rl.read_json(rl.RESULTS_NAME, {}, a.data)
    strategies = rl.read_json(rl.STRATS_NAME, {}, a.data)
    sstrat = strategies.get("slots", {}) if isinstance(strategies, dict) else {}
    blacklist = list(st.get("blacklist", []))
    free = free_slots(prev if isinstance(prev, dict) else {}, sstrat, blacklist)
    print(f"[prepare] free slots: {free}")
    cands: List[Dict[str, Any]] = []
    ai_info: Dict[str, Any] = {"used": False}
    # (۱) هوش مصنوعی
    ai_calls = int((prev or {}).get("ai_calls_today", {}).get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0)) if isinstance(prev, dict) else 0
    if cfg["ai_on"] and cfg["mode"] != "off":
        ai_info = rnd_ai.propose(diag, prev if isinstance(prev, dict) else {}, min(12, cfg["cands"] // 3), cfg["ai_model"], cfg["ai_calls_day"] - ai_calls)
        cands += ai_info.get("specs", [])
        print(f"[prepare] AI used={ai_info.get('used')} specs={len(ai_info.get('specs', []))} err={ai_info.get('error')}")
    # (۲) جهش/بهینه‌سازی برترین‌های قبلی (آرشیو) + (۳) جست‌وجوی هدایت‌شده
    archive = [x for x in (prev or {}).get("archive", []) if isinstance(x, dict) and x.get("spec")] if isinstance(prev, dict) else []
    archive.sort(key=lambda x: -float(x.get("score", 0)))
    n_mut = min(cfg["cands"] // 4, len(archive) * 4)
    for k in range(n_mut):
        m = mutate(archive[k % max(1, min(5, len(archive)))]["spec"], r)
        if m:
            cands.append(m)
    cands += heuristic_candidates(r, max(5, cfg["cands"] - len(cands)), hints_from_diag(diag))
    seen, uniq = set(), []
    for c in cands:
        h = spec_hash(c)
        if h in seen or h in blacklist:
            continue
        seen.add(h)
        uniq.append(c)
    cands = uniq[:cfg["cands"] + 12]
    print(f"[prepare] {len(cands)} unique candidates")
    # (۴) داده + پیش‌غربال روی train
    if a.synthetic:
        ds = {"SYN1": {"15m": sl.synth(30000, 900_000, seed=3, daily_drift=0.15, mom=0.05)}, "SYN2": {"15m": sl.synth(30000, 900_000, seed=4, daily_drift=0.15, mom=0.05)}}
        raw_ds = ds
    else:
        ns = _args_ns(csvdir="", fetch=a.symbols, days1=0, days5=a.days5, days15=a.days15, days1h=a.days15, days4h=a.days15, out=os.path.join(HERE, "lab_data"), source="auto")
        raw_ds = sl.load_dataset(ns)
    days = {"15m": a.days15, "1h": a.days15, "4h": a.days15}
    datasets = {sym: build_series(m, days, sl) for sym, m in raw_ds.items()}
    sl.DESIGN = True
    scored = prescreen(cands, datasets, a.fee, sl, ls)
    print(f"[prepare] {len(scored)} candidates produced enough train trades; best t={scored[0]['t'] if scored else 'n/a'}")
    chosen = pick_diverse(scored, len(free))
    new_slots = dict(sstrat)
    cyc = int((prev or {}).get("cycle", 0)) + 1 if isinstance(prev, dict) else 1
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for slot, row in zip(free, chosen):
        spec = dict(row["spec"], id=slot, created=iso)
        sid = spec_hash(spec)
        new_slots[slot] = {"spec": spec, "spec_id": sid, "set": iso, "prescreen": {k: row[k] for k in ("tf", "n", "avg", "t")}}
    for slot in free[len(chosen):]:
        new_slots.pop(slot, None)                         # اسلات آزاد بدون کاندیدِ قابل‌قبول ⇒ خالی
    out = {"cycle": cyc, "set": iso, "slots": new_slots}
    rl.write_json(rl.STRATS_NAME, out, a.data)
    # ذخیره‌ی میانی برای مراحل بعد (متن AI و آرشیو)
    rl.write_json("rnd_prepare.json", {"cycle": cyc, "tested": len(scored), "candidates": len(cands), "ai": dict({k: ai_info.get(k) for k in ("used", "error", "model", "analysis")}, key_seen=rnd_ai.available(), key_src=rnd_ai.key_source(), at=time.time()),
                                       "hints": ai_info.get("hints", []), "free": free, "chosen": [c["spec"].get("name") for c in chosen],
                                       "history": [{"name": s["spec"].get("name"), "family": s["spec"].get("family"), "tf": s["tf"], "t": s["t"], "n": s["n"], "origin": s["spec"].get("origin")} for s in scored[:25]],
                                       "ai_calls_used": 1 if ai_info.get("used") else 0}, a.data)
    print(f"[prepare] slots now: { {s: v.get('spec', {}).get('name') for s, v in new_slots.items()} }")
    return 0


def _status(v: Dict[str, Any]) -> str:
    import shared_controls as sc
    if not v or v.get("verdict") in (None, "NO_DATA"):
        return "FAIL" if v else "EMPTY"
    if v.get("verdict") == "PASS":
        return "PASS"
    return "CAND" if sc.is_candidate(v) else "FAIL"


NET_MIN_AVG_R = 0.05      # حداقل میانگین R خالص هر معامله پس از کارمزد
NET_MIN_PF = 1.10
NET_MIN_T = 2.0
NET_MIN_N = 100


def own_ladder_gate(v: Dict[str, Any]) -> Dict[str, Any]:
    """🔴 دروازه‌ی سودآوری: استراتژی جدید فقط وقتی PASS/کاندید می‌ماند که روی «نردبان مخصوص خودش» (بهترین نردبانِ قابل‌اجرا در ربات)
    پس از کسر کارمزد سودده باشد: میانگین R خالص ≥ ۰٫۰۵، PF ≥ ۱٫۱، |t| ≥ ۲، train و test مثبت، و با هزینه‌ی ۱٫۵× هم مثبت.
    اعداد نمایش‌داده‌شده (avg_r/t/pf/win/n) از همان نردبان گرفته می‌شوند و best_ladder همان نردبان می‌شود (قبلاً فقط برای PASS ست می‌شد
    و اعداد کاندیدها از نردبان دیگری بود). stats نداشت (verdict قدیمی) ⇒ دست‌نخورده. خروجی: verdict جدید با کلید net_gate_ok/net_gate_why."""
    out = dict(v)
    try:
        import ladder_catalog as lc
        ranked = [(k, x) for k, x in lc.rank_ladders(v.get("stats")) if len(lc.LADDERS[k][0]) >= 2 or x.get("sem") == "live4"]
    except Exception:  # noqa: BLE001
        ranked = []
    if not ranked:
        out["net_gate_ok"] = None
        return out
    prof = [(k, x) for k, x in ranked if float(x.get("avg", 0)) > 0]
    k, x = (prof or ranked)[0]
    why = []
    if float(x.get("avg", 0)) < NET_MIN_AVG_R:
        why.append(f"net avg {float(x.get('avg', 0)):+.2f}R < {NET_MIN_AVG_R}R after fees")
    if float(x.get("pf", 0)) < NET_MIN_PF:
        why.append("PF")
    if float(x.get("t", 0)) < NET_MIN_T:
        why.append("t-stat")
    if float(x.get("train", 0)) <= 0 or float(x.get("test", 0)) <= 0:
        why.append("train/test")
    if int(x.get("n", 0)) < NET_MIN_N:
        why.append("too few trades")
    if x.get("hi") is not None and float(x["hi"]) <= 0:
        why.append("loses with 1.5x costs")
    out.update(avg_r=x.get("avg"), t=x.get("t"), pf=x.get("pf"), win=x.get("win"), n=x.get("n"), train_r=x.get("train"), test_r=x.get("test"),
               best_ladder=k, net_gate_ok=not why, net_gate_why=why)
    if why:
        out["fail_reasons"] = list(v.get("fail_reasons") or []) + ["net-after-fees: " + ", ".join(why)]
    return out


def cmd_finalize(a) -> int:
    raw = rl.read_json(os.path.relpath(a.raw), {}, ".") if a.raw else {}
    strategies = rl.read_json(rl.STRATS_NAME, {}, a.data)
    prep = rl.read_json("rnd_prepare.json", {}, a.data)
    prev = rl.read_json(rl.RESULTS_NAME, {}, a.data)
    prev = prev if isinstance(prev, dict) else {}
    sstrat = strategies.get("slots", {}) if isinstance(strategies, dict) else {}
    verdicts: Dict[str, Any] = {}
    slots: Dict[str, Any] = {}
    archive = list(prev.get("archive", []))
    for s in SLOTS:
        rec = sstrat.get(s)
        if not rec or not rec.get("spec"):
            slots[s] = {"status": "EMPTY"}
            continue
        v = raw.get(s) or {}
        if v:
            v = own_ladder_gate(v)
        status = _status(v) if v else "FAIL"
        if v and v.get("net_gate_ok") is False and status in ("PASS", "CAND"):
            status = "FAIL"                                   # سودده‌ی خالص روی نردبان خودش نیست ⇒ هرگز به ربات‌ها اضافه نمی‌شود
            v = dict(v, verdict="FAIL")                       # verdict هم FAIL تا هیچ مسیری (shared_controls) قفل را باز نکند
        v = dict(v, spec_id=rec["spec_id"], spec_name=rec["spec"].get("name"))
        verdicts[s] = v
        slots[s] = {"status": status, "spec_id": rec["spec_id"], "name": rec["spec"].get("name"), "family": rec["spec"].get("family"), "origin": rec["spec"].get("origin"),
                    "tf": v.get("tf"), "avg_r": v.get("avg_r"), "t": v.get("t"), "pf": v.get("pf"), "win": v.get("win"), "n": v.get("n"), "best_ladder": v.get("best_ladder"),
                    "train_r": v.get("train_r"), "test_r": v.get("test_r"), "fail_reasons": v.get("fail_reasons"), "symbols_positive": v.get("symbols_positive")}
        if v.get("avg_r") is not None and v.get("t") is not None:
            archive = [x for x in archive if x.get("spec_id") != rec["spec_id"]]
            archive.append({"spec_id": rec["spec_id"], "spec": rec["spec"], "score": float(v.get("t", 0)) + (3.0 if status in ("PASS", "CAND") else 0.0), "status": status})
    archive.sort(key=lambda x: -float(x.get("score", 0)))
    ai = prep.get("ai") or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    calls = dict(prev.get("ai_calls_today", {}))
    calls = {k: v for k, v in calls.items() if k == today}
    calls[today] = calls.get(today, 0) + int(prep.get("ai_calls_used", 0))
    hist = (prev.get("history") or [])[-40:] + (prep.get("history") or [])[:12]
    results = {"ts": time.time(), "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "cycle": int(prep.get("cycle", prev.get("cycle", 0))),
               "tested": prep.get("candidates"), "prescreened": prep.get("tested"), "slots": slots, "archive": archive[:15], "history": hist[-50:],
               "ai": {"used": bool(ai.get("used")), "model": ai.get("model"), "error": ai.get("error") or "", "analysis": ai.get("analysis") or [],
                      "key_seen": bool(ai.get("key_seen")), "key_src": ai.get("key_src") or "", "at": ai.get("at")},
               "ai_hints": prep.get("hints") or [], "ai_calls_today": calls}
    rl.write_json(rl.VERDICTS_NAME, verdicts, a.data)
    rl.write_json(rl.RESULTS_NAME, results, a.data)
    # درخواست‌های دکمه‌ای انجام‌شده را نشان می‌دهیم (ربات بعد از pull می‌بیند: results.ts > request.t)
    npass = sum(1 for x in slots.values() if x.get("status") == "PASS")
    ncand = sum(1 for x in slots.values() if x.get("status") == "CAND")
    print(f"[finalize] cycle {results['cycle']}: PASS={npass} CAND={ncand} FAIL={sum(1 for x in slots.values() if x.get('status') == 'FAIL')} EMPTY={sum(1 for x in slots.values() if x.get('status') == 'EMPTY')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "prepare", "finalize"])
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--symbols", nargs="*", default=SYMS_DEFAULT)
    ap.add_argument("--days15", type=int, default=700)
    ap.add_argument("--days5", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--synthetic", action="store_true", help="offline self-test: synthetic candles instead of downloads")
    ap.add_argument("--raw", default="", help="finalize: raw verdict JSON written by strategy_lab --verdicts")
    a = ap.parse_args()
    os.environ["DATA_DIR"] = a.data
    return {"plan": cmd_plan, "prepare": cmd_prepare, "finalize": cmd_finalize}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

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


_LOOSEN = (">", ">=")


def improve_variants(spec: Dict[str, Any], fails: List[str], r: random.Random, n: int) -> List[Dict[str, Any]]:
    """🔧 اصلاح هدفمند یک استراتژی ناموفق بر اساس «شرط‌هایی که رد شده»:
      win/streak/dd/payoff/avg/pf/t  → کیفیت ورود بالا (فیلتر روند/حجم/ADX) یا آستانه‌ی سخت‌گیرانه‌تر؛ حد ضرر باز‌تر (کمتر استاپ‌خوردن)
      n (معامله‌ی کم)               → حذف یک شرط یا شل‌کردن آستانه‌ها
      pf_max (مشکوک به بیش‌برازش)    → ساده‌سازی: حذف شرط، گرد‌کردن آستانه‌ها
    خروجی‌ها قبل از پیش‌غربال اعتبارسنجی می‌شوند؛ حکم نهایی همیشه با آزمایشگاه است."""
    out: List[Dict[str, Any]] = []
    quality = {"win", "streak", "dd", "payoff", "avg", "pf_min", "t", "split", "stress"}
    for _ in range(n * 3):
        if len(out) >= n:
            break
        s = json.loads(json.dumps(spec))
        s["origin"] = "improve"
        s["parent"] = spec_hash(spec)
        conds = s["all"]
        fl = set(fails)
        moves = []
        if fl & quality:
            moves += ["add_filter", "tighten", "wider_stop", "tighten"]
        if "n" in fl:
            moves += ["drop", "loosen"]
        if "pf_max" in fl:
            moves += ["drop", "round"]
        if not moves:
            moves = ["tighten", "wider_stop", "add_filter"]
        mv = r.choice(moves)
        num = [i for i, c in enumerate(conds) if isinstance(c[2], (int, float)) and not isinstance(c[2], bool) and c[1] in (">", "<", ">=", "<=")]
        if mv == "add_filter" and len(conds) < 7:
            f = r.choice(_FILTERS)
            if f[0] not in {c[0] for c in conds}:
                conds.append(list(f))
            else:
                continue
        elif mv in ("tighten", "loosen") and num:
            i = r.choice(num)
            gt = conds[i][1] in _LOOSEN
            up = (mv == "tighten") == gt                 # «>» سخت‌گیرانه‌تر = بزرگ‌تر؛ «<» سخت‌گیرانه‌تر = کوچک‌تر
            v = float(conds[i][2]) * (r.choice([1.1, 1.2, 1.3]) if up else r.choice([0.9, 0.8, 0.7]))
            conds[i][2] = round(v, 3 if abs(v) < 5 else 1)
        elif mv == "wider_stop":
            st = dict(s.get("stop") or {})
            if st.get("kind") == "atr":
                st["k"] = round(min(4.0, float(st.get("k", 1.5)) + r.choice([0.5, 1.0])), 2)
            elif st.get("kind") == "swing":
                st["buf"] = round(min(0.6, float(st.get("buf", 0.2)) + 0.2), 2)
            else:
                st = {"kind": "atr", "k": r.choice([2.0, 2.5, 3.0])}
            s["stop"] = st
        elif mv == "drop" and len(conds) > 2:
            conds.pop(r.randrange(len(conds)))
        elif mv == "round":
            for i in num:
                v = float(conds[i][2])
                conds[i][2] = round(v, 1) if abs(v) >= 1 else round(v, 2)
        else:
            continue
        s["name"] = (str(s.get("name", "R&D")).split(" ·")[0] + " · fix" + str(r.randint(2, 99)))[:60]
        c = _clean(s)
        if c and spec_hash(c) != spec_hash(spec) and all(spec_hash(c) != spec_hash(x) for x in out):
            out.append(c)
    return out


def weak_list(prev: Dict[str, Any], archive: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """استراتژی‌های ناموفقِ اخیر (برای حلقه‌ی بهبود و برای هوش مصنوعی): نام، DSL، دلیل رد‌شدن."""
    rows = []
    sl = (prev or {}).get("slots") or {}
    by_id = {a.get("spec_id"): a for a in archive}
    for slot, rec in sl.items():
        if rec.get("status") in ("FAIL", "CAND") and rec.get("spec_id") in by_id:
            a = by_id[rec["spec_id"]]
            rows.append({"name": rec.get("name"), "spec": a.get("spec"), "fails": a.get("fails") or [], "metrics": {k: rec.get(k) for k in ("tf", "avg_r", "t", "pf", "win", "n", "dd_pct", "streak", "best_ladder")},
                         "reasons": rec.get("fail_reasons")})
    rows.sort(key=lambda x: len(x["fails"]))
    return rows


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
    # (۱) هوش مصنوعی (چند ارائه‌دهنده‌ی هم‌زمان)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by = ((prev or {}).get("ai_calls_by") or {}).get(today, {}) if isinstance(prev, dict) else {}
    gate = rl.gate_of(cfg)
    archive0 = [x for x in (prev or {}).get("archive", []) if isinstance(x, dict) and x.get("spec")] if isinstance(prev, dict) else []
    weak = weak_list(prev if isinstance(prev, dict) else {}, archive0) if cfg["improve"] else []
    if cfg["ai_on"] and cfg["mode"] != "off":
        enabled = [p for p, k in rl.AI_PROVIDER_KEYS.items() if cfg.get(k)]
        budgets = {p: cfg["ai_calls_day"] - int(by.get(p, 0)) for p in rnd_ai.PROVIDERS}
        ai_info = rnd_ai.propose_multi(diag, prev if isinstance(prev, dict) else {}, min(12, cfg["cands"] // 3), cfg["ai_model"], budgets, enabled, gate, weak,
                                       cfg["improve_n"] // 2 if weak else 0)
        cands += ai_info.get("specs", [])
        print(f"[prepare] AI used={ai_info.get('used')} specs={len(ai_info.get('specs', []))} providers={ {k: v.get('n', v.get('skipped')) for k, v in ai_info.get('providers', {}).items()} } err={ai_info.get('error')}")
    # (۱-ب) حلقه‌ی بهبود خودکار: ضعیف‌ترین‌های اخیر را بر اساس دلیل رد‌شدنشان اصلاح می‌کند تا دوباره آزمون شوند
    improved = 0
    if cfg["improve"] and weak:
        per = max(1, cfg["improve_n"] // max(1, min(4, len(weak))))
        for w in weak[:4]:
            vs = improve_variants(w["spec"], w["fails"], r, per)
            improved += len(vs)
            cands += vs
        print(f"[prepare] improve loop: {improved} fixed variants from {min(4, len(weak))} weak strategies")
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
    rl.write_json("rnd_prepare.json", {"cycle": cyc, "tested": len(scored), "candidates": len(cands), "improved": improved, "ai": dict({k: ai_info.get(k) for k in ("used", "error", "model", "analysis", "providers")}, key_seen=rnd_ai.available(), key_src=rnd_ai.key_source(), at=time.time()),
                                       "hints": ai_info.get("hints", []), "free": free, "chosen": [c["spec"].get("name") for c in chosen],
                                       "history": [{"name": s["spec"].get("name"), "family": s["spec"].get("family"), "tf": s["tf"], "t": s["t"], "n": s["n"], "origin": s["spec"].get("origin")} for s in scored[:25]],
                                       "ai_calls_used": sum((ai_info.get("calls") or {}).values()) if ai_info.get("calls") else (1 if ai_info.get("used") else 0),
                                       "ai_calls_by": ai_info.get("calls") or {}}, a.data)
    print(f"[prepare] slots now: { {s: v.get('spec', {}).get('name') for s, v in new_slots.items()} }")
    return 0


def _status(v: Dict[str, Any]) -> str:
    import shared_controls as sc
    if not v or v.get("verdict") in (None, "NO_DATA"):
        return "FAIL" if v else "EMPTY"
    if v.get("verdict") == "PASS":
        return "PASS"
    return "CAND" if sc.is_candidate(v) else "FAIL"


def _far_target(k: str) -> float:
    try:
        import ladder_catalog as lc
        return float(lc.LADDERS[k][0][-1])
    except Exception:  # noqa: BLE001
        return 0.0


def gate_checks(k: str, x: Dict[str, Any], g: Dict[str, Any]) -> List[Dict[str, Any]]:
    """🎯 همه‌ی شرایط پاس‌شدن روی یک نردبان. هر مورد: {k: کلید، n: نام، v: مقدار، lim: حد، ok}. معیاری که آمارش موجود نیست (verdict قدیمی بدون dd/streak) «n/a» می‌شود و رد نمی‌کند."""
    win = float(x.get("win", 0) or 0)
    pf = float(x.get("pf", 0) or 0)
    payoff = pf * (100.0 - win) / win if win > 0 else 0.0
    out: List[Dict[str, Any]] = []

    def add(key, name, val, lim, ok):
        out.append({"k": key, "n": name, "v": None if val is None else round(float(val), 3), "lim": lim, "ok": None if val is None else bool(ok)})

    add("win", "Win rate %", win, f"≥ {g['win']:g}", win >= g["win"])
    add("pf_min", "Profit factor", pf, f"≥ {g['pf_min']:g}", pf >= g["pf_min"])
    add("pf_max", "PF overfit cap", pf, f"≤ {g['pf_max']:g}", pf <= g["pf_max"])
    far = _far_target(k)
    add("rr", "Planned R:R", far, f"≥ {g['rr']:g}", far >= g["rr"])
    add("payoff", "Avg win / avg loss", payoff, f"≥ {g['payoff']:g}", payoff >= g["payoff"])
    dd = x.get("dd")
    add("dd", "Max drawdown %", None if dd is None else float(dd) * g["risk"], f"≤ {g['dd']:g}", dd is not None and float(dd) * g["risk"] <= g["dd"])
    st = x.get("streak")
    add("streak", "Max losing streak", None if st is None else st, f"< {g['streak']}", st is not None and int(st) < g["streak"])
    add("avg", "Net R / trade", float(x.get("avg", 0) or 0), f"≥ {g['avg']:g}", float(x.get("avg", 0) or 0) >= g["avg"])
    add("t", "|t| strength", float(x.get("t", 0) or 0), f"≥ {g['t']:g}", float(x.get("t", 0) or 0) >= g["t"])
    add("n", "Backtest trades", int(x.get("n", 0) or 0), f"≥ {g['n']}", int(x.get("n", 0) or 0) >= g["n"])
    if g.get("stress"):
        hi = x.get("hi")
        add("stress", "Net R at 1.5× costs", None if hi is None else hi, "> 0", hi is not None and float(hi) > 0)
    if g.get("split"):
        tr, te = float(x.get("train", 0) or 0), float(x.get("test", 0) or 0)
        add("split", "Worst half (train/test)", min(tr, te), "> 0", min(tr, te) > 0)
    return out


def own_ladder_gate(v: Dict[str, Any], g: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """🔴 دروازه‌ی پاس‌شدن: استراتژی جدید فقط وقتی PASS/کاندید می‌ماند که روی «نردبان مخصوص خودش» (بهترین نردبانِ قابل‌اجرا) پس از کسر کارمزد
    «همه‌ی» شرایطی را که ادمین در 🎯 Pass criteria گذاشته داشته باشد (وین‌ریت، PF حداقل/سقف، ریسک‌به‌ریوارد، دراودان، لوزینگ‌استریک، میانگین R، t، تعداد، آزمون ۱٫۵× هزینه، دو نیمه).
    نردبان انتخابی = اولین نردبانی که همه‌ی شروط را دارد (بیشترین میانگین R)، وگرنه نردبانی با کمترین شرط رد‌شده. اعداد نمایشی (avg_r/t/pf/win/n) از همان نردبان‌اند.
    stats نداشت (verdict قدیمی) ⇒ دست‌نخورده (net_gate_ok=None). خروجی: net_gate_ok، net_gate_why، gate = {checks, passed, total}."""
    g = g or rl.gate_of(rl.settings_of({}))
    out = dict(v)
    if not g.get("on", True):                       # 🎯 کلید کلی خاموش: فقط حکم آزمایشگاه
        out["net_gate_ok"] = None
        return out
    try:
        import ladder_catalog as lc
        ranked = [(k, x) for k, x in lc.rank_ladders(v.get("stats")) if len(lc.LADDERS[k][0]) >= 2 or x.get("sem") == "live4"]
    except Exception:  # noqa: BLE001
        ranked = []
    if not ranked:
        out["net_gate_ok"] = None
        return out
    best = None
    for k, x in ranked:
        chk = gate_checks(k, x, g)
        fails = [c for c in chk if c["ok"] is False]
        key = (len(fails), -float(x.get("avg", 0) or 0))
        if best is None or key < best[0]:
            best = (key, k, x, chk, fails)
    _, k, x, chk, fails = best
    why = [f"{c['n']} {c['v']:g} (need {c['lim']})" for c in fails]
    dd = x.get("dd")
    out.update(avg_r=x.get("avg"), t=x.get("t"), pf=x.get("pf"), win=x.get("win"), n=x.get("n"), train_r=x.get("train"), test_r=x.get("test"),
               best_ladder=k, net_gate_ok=not why, net_gate_why=why,
               dd_pct=None if dd is None else round(float(dd) * g["risk"], 2), streak=x.get("streak"),
               gate={"checks": chk, "passed": sum(1 for c in chk if c["ok"]), "total": sum(1 for c in chk if c["ok"] is not None)})
    if why:
        out["fail_reasons"] = list(v.get("fail_reasons") or []) + ["criteria: " + "; ".join(why)]
    return out


def cmd_finalize(a) -> int:
    raw = rl.read_json(os.path.relpath(a.raw), {}, ".") if a.raw else {}
    strategies = rl.read_json(rl.STRATS_NAME, {}, a.data)
    prep = rl.read_json("rnd_prepare.json", {}, a.data)
    prev = rl.read_json(rl.RESULTS_NAME, {}, a.data)
    prev = prev if isinstance(prev, dict) else {}
    sstrat = strategies.get("slots", {}) if isinstance(strategies, dict) else {}
    gate = rl.gate_of(rl.settings_of(rl.load_state(a.data)))
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
            v = own_ladder_gate(v, gate)
        status = _status(v) if v else "FAIL"
        if v and v.get("net_gate_ok") is False and status in ("PASS", "CAND"):
            status = "FAIL"                                   # سودده‌ی خالص روی نردبان خودش نیست ⇒ هرگز به ربات‌ها اضافه نمی‌شود
            v = dict(v, verdict="FAIL")                       # verdict هم FAIL تا هیچ مسیری (shared_controls) قفل را باز نکند
        v = dict(v, spec_id=rec["spec_id"], spec_name=rec["spec"].get("name"))
        verdicts[s] = v
        slots[s] = {"status": status, "spec_id": rec["spec_id"], "name": rec["spec"].get("name"), "family": rec["spec"].get("family"), "origin": rec["spec"].get("origin"),
                    "tf": v.get("tf"), "avg_r": v.get("avg_r"), "t": v.get("t"), "pf": v.get("pf"), "win": v.get("win"), "n": v.get("n"), "best_ladder": v.get("best_ladder"),
                    "train_r": v.get("train_r"), "test_r": v.get("test_r"), "fail_reasons": v.get("fail_reasons"), "symbols_positive": v.get("symbols_positive"),
                    "dd_pct": v.get("dd_pct"), "streak": v.get("streak"), "gate": v.get("gate")}
        if v.get("avg_r") is not None and v.get("t") is not None:
            archive = [x for x in archive if x.get("spec_id") != rec["spec_id"]]
            gt = v.get("gate") or {}
            close = (float(gt.get("passed", 0)) / max(1, float(gt.get("total", 1)))) * 3.0      # هرچه به معیارهای ادمین نزدیک‌تر، امتیاز بیشتر ⇒ والد بهتر برای بهبود
            archive.append({"spec_id": rec["spec_id"], "spec": rec["spec"], "status": status,
                            "score": float(v.get("t", 0) or 0) + close + (3.0 if status in ("PASS", "CAND") else 0.0),
                            "fails": [c["k"] for c in (gt.get("checks") or []) if c.get("ok") is False]})
    archive.sort(key=lambda x: -float(x.get("score", 0)))
    ai = prep.get("ai") or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    calls = dict(prev.get("ai_calls_today", {}))
    calls = {k: v for k, v in calls.items() if k == today}
    calls[today] = calls.get(today, 0) + int(prep.get("ai_calls_used", 0))
    by_all = {k: v for k, v in (prev.get("ai_calls_by") or {}).items() if k == today}
    tb = dict(by_all.get(today, {}))
    for pk, nn in (prep.get("ai_calls_by") or {}).items():
        tb[pk] = int(tb.get(pk, 0)) + int(nn)
    by_all[today] = tb
    hist = (prev.get("history") or [])[-40:] + (prep.get("history") or [])[:12]
    results = {"ts": time.time(), "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "cycle": int(prep.get("cycle", prev.get("cycle", 0))),
               "tested": prep.get("candidates"), "prescreened": prep.get("tested"), "slots": slots, "archive": archive[:15], "history": hist[-50:],
               "gate": gate, "improved": prep.get("improved", 0),
               "ai": {"used": bool(ai.get("used")), "model": ai.get("model"), "error": ai.get("error") or "", "analysis": ai.get("analysis") or [],
                      "providers": ai.get("providers") or {},
                      "key_seen": bool(ai.get("key_seen")), "key_src": ai.get("key_src") or "", "at": ai.get("at")},
               "ai_hints": prep.get("hints") or [], "ai_calls_today": calls, "ai_calls_by": by_all}
    rl.write_json(rl.VERDICTS_NAME, verdicts, a.data)
    rl.write_json(rl.RESULTS_NAME, results, a.data)
    # درخواست‌های دکمه‌ای انجام‌شده را نشان می‌دهیم (ربات بعد از pull می‌بیند: results.ts > request.t)
    npass = sum(1 for x in slots.values() if x.get("status") == "PASS")
    ncand = sum(1 for x in slots.values() if x.get("status") == "CAND")
    print(f"[finalize] cycle {results['cycle']}: PASS={npass} CAND={ncand} FAIL={sum(1 for x in slots.values() if x.get('status') == 'FAIL')} EMPTY={sum(1 for x in slots.values() if x.get('status') == 'EMPTY')}")
    return 0


def cmd_modules_plan(a) -> int:
    """آیا الان نوبت بازآزمایی همه‌ی ماژول‌هاست؟ (۲۴ ساعته: ≥ ۱ ساعت بعد از آخرین نتیجه؛ یا درخواست دکمه‌ای). خروجی go=true/false."""
    st = rl.load_state(a.data)
    cfg = rl.settings_of(st)
    res = rl.read_json(rl.MODULES_NAME, {}, a.data)
    now = time.time()
    last = float((res or {}).get("ts", 0))
    pending = [r for r in st.get("requests", []) if r.get("kind") == "modules" and not r.get("done") and float(r.get("t", 0)) > last]
    go = a.force or bool(pending) or (cfg["modules_on"] and cfg["mode"] != "off" and now - last >= rl.effective_modules_h(cfg) * 3600)
    print(f"[modules-plan] on={cfg['modules_on']} mode={cfg['mode']} last={rl.now_iso(last) if last else 'never'} pending={len(pending)} -> go={go}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"go={'true' if go else 'false'}\n")
    return 0


def module_status(v: Dict[str, Any]) -> str:
    """PASS = آزمایشگاه PASS داده «و» همه‌ی معیارهای ادمین برقرار؛ LAB = آزمایشگاه PASS داده ولی معیارهای ادمین نه؛ FAIL؛ NA = داده نبود."""
    if not v or v.get("verdict") in (None, "NO_DATA"):
        return "NA"
    if v.get("net_gate_ok") is False:
        return "LAB" if v.get("verdict") in ("PASS", "CAND") else "FAIL"
    return v["verdict"] if v.get("verdict") in ("PASS", "CAND") else "FAIL"


def apply_modules(data_dir: str) -> Tuple[int, int]:
    """نتیجه‌ی rnd_modules.json را در data/lowtf_verdicts.json می‌نویسد: ماژولِ PASS ⇒ بهترین نردبانِ انتخاب‌شده با معیارهای ادمین؛
    ماژولی که معیارها را ندارد ⇒ FAIL (قفل). ماژول‌هایی که این بار آزموده نشدند (بازنشسته‌ها) دست‌نخورده می‌مانند. (تعداد PASS، تعداد قفل)"""
    res = rl.read_json(rl.MODULES_NAME, {}, data_dir)
    mods = (res or {}).get("modules") or {}
    path = "lowtf_verdicts.json"
    doc = rl.read_json(path, {}, data_dir)
    doc = doc if isinstance(doc, dict) else {}
    npass = nlock = 0
    for key, m in mods.items():
        old = dict(doc.get(key) or {})
        if m.get("status") == "NA":
            continue
        if m["status"] in ("PASS", "CAND"):
            old.update({k: m.get(k) for k in ("verdict", "best_ladder", "avg_r", "t", "win", "pf", "n", "train_r", "test_r") if m.get(k) is not None})
            old["verdict"] = m["status"]
            npass += 1
        else:
            old["verdict"] = "FAIL"
            old["best_ladder"] = None
            old["fail_reasons"] = m.get("fail_reasons") or ["criteria"]
            nlock += 1
        old["rnd_gate"] = {"at": res.get("ts"), "passed": (m.get("gate") or {}).get("passed"), "total": (m.get("gate") or {}).get("total")}
        doc[key] = old
    rl.write_json(path, doc, data_dir)
    return npass, nlock


def cmd_modules_finalize(a) -> int:
    raw = rl.read_json(os.path.relpath(a.raw), {}, ".") if a.raw else {}
    st = rl.load_state(a.data)
    cfg = rl.settings_of(st)
    gate = rl.gate_of(cfg)
    mods: Dict[str, Any] = {}
    for key, v in raw.items():
        if key.startswith("_") or not isinstance(v, dict):
            continue
        v = own_ladder_gate(v, gate) if v.get("stats") else dict(v)
        mods[key] = {"status": module_status(v), "verdict": v.get("verdict"), "tf": v.get("tf"), "best_ladder": v.get("best_ladder"), "avg_r": v.get("avg_r"),
                     "t": v.get("t"), "pf": v.get("pf"), "win": v.get("win"), "n": v.get("n"), "train_r": v.get("train_r"), "test_r": v.get("test_r"),
                     "dd_pct": v.get("dd_pct"), "streak": v.get("streak"), "gate": v.get("gate"), "fail_reasons": v.get("fail_reasons"), "title": v.get("title")}
    res = {"ts": time.time(), "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "gate": gate, "modules": mods}
    rl.write_json(rl.MODULES_NAME, res, a.data)
    c = {k: sum(1 for m in mods.values() if m["status"] == k) for k in ("PASS", "CAND", "LAB", "FAIL", "NA")}
    print(f"[modules-finalize] {len(mods)} modules: {c}")
    if cfg["mode"] == "auto" and cfg["modules_apply"]:
        np_, nl = apply_modules(a.data)
        print(f"[modules-finalize] applied to lowtf_verdicts.json: {np_} PASS, {nl} locked")
    return 0


def cmd_gate_env(a) -> int:
    """خروجی برای ورک‌فلو: `export $(python rnd_research.py gate-env)` ⇒ حدهای PF/وین‌ریتِ آزمایشگاه از پنل ادمین می‌آید (نه ثابت کد)."""
    g = rl.gate_of(rl.settings_of(rl.load_state(a.data)))
    if g.get("on", True):
        print(f"RND_GATE_PF={g['pf_min']:g} RND_GATE_WIN={g['win']:g}")
    else:
        print("RND_GATE_PF=1.2 RND_GATE_WIN=40")      # پیش‌فرض قدیمیِ آزمایشگاه
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "prepare", "finalize", "gate-env", "modules-plan", "modules-finalize"])
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
    return {"plan": cmd_plan, "prepare": cmd_prepare, "finalize": cmd_finalize, "gate-env": cmd_gate_env, "modules-plan": cmd_modules_plan, "modules-finalize": cmd_modules_finalize}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
full_backtest.py — بک‌تست کاملِ همه‌ی استراتژی‌ها روی همه‌ی تایم‌فریم‌ها (۵ دقیقه با اولویت بالاتر)،
انتخاب بهترین نردبان هر استراتژی، پیدا کردن عیب‌ها و پیشنهاد اصلاح.

این فایل ابزار پژوهشی مستقل است؛ هیچ‌جای ربات‌های زنده را تغییر نمی‌دهد و فقط از strategy_lab.py
(همان شبیه‌ساز و همان منطق نردبانِ ربات) استفاده می‌کند. دو مرحله دارد:

  1) RUN   (روی GitHub Actions، چون داده‌ی قیمت لازم است):
       python3 full_backtest.py run --fetch BTCUSDT ETHUSDT ... --tf 5m --days5 730 --dump part_5m_s1.pkl
     (یا --csvdir با فایل‌های SYMBOL_1m/5m/15m.csv)
  2) MERGE (تحلیل):
       python3 full_backtest.py merge part_*.pkl --fee 0.10 --out-dir out
     خروجی: full_backtest_report.txt ، full_backtest_results.csv ، full_backtest_recommendations.json

قواعد صداقت (ضدِ بیش‌برازش):
  * ورود = open کندلِ بعد از سیگنال؛ کارمزد رفت‌وبرگشت (پیش‌فرض ۰٫۱۰٪) ÷ استاپ٪ از هر معامله به‌صورت R کم می‌شود.
  * تقسیم زمانی ۶۰/۴۰: نردبان/فیلتر/استاپ فقط روی ۶۰٪ اولِ زمان (TRAIN) انتخاب می‌شود و روی ۴۰٪ آخر (TEST) قضاوت می‌شود.
  * «عیب» فقط وقتی اعلام می‌شود که هم در TRAIN و هم در TEST دیده شود (نه نویز یک نیمه).
  * نردبان‌های تک‌تارگتی B* و تارگت اختصاصیِ BT به‌صورت پیش‌فرض پیشنهاد نمی‌شوند (آمار آزمایشگاهشان با ماشین‌حالت زنده
    یکی نیست؛ --allow-brackets برای دیدنشان).
هدف‌ها (قابل تغییر با پارامتر): winrate ≥ 40٪ ، 1.2 ≤ PF ≤ 1.6 ، دورترین تارگت ≥ 2R ، افت سرمایه متناسب (recovery ≥ 1.5).
"""
import argparse, csv, inspect, json, math, os, pickle, sys, zlib
from collections import defaultdict
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TF_ORDER = ["5m", "15m", "1h", "4h", "1m"]          # اولویت کاربر: ۵ دقیقه مهم‌تر از ۱ دقیقه
TF_PRIORITY = {"5m": "★★★ highest", "15m": "★★ high", "1h": "★★ high", "4h": "★ normal", "1m": "· low (user: 5m matters more)"}
CORE_GENS = ["ENG", "CNDL", "HDV3", "SWP", "TS1"]   # ENG = موتور کندلِ زنده (candle_engine) ، CNDL = همان الگو در آزمایشگاه
GOALS = dict(win=0.40, pf_min=1.20, pf_max=1.60, far_target=2.0, recovery=1.5, t_min=3.0, t_near=1.5, min_n_train=40, min_n_test=25)
FLOORS = [0.0, 0.3, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


# ----------------------------------------------------------------------------- lab import + trade tuple extension
def _load_lab():
    import strategy_lab as sl
    src = inspect.getsource(sl.run_symbol)
    old_app = 'trades.append((int(d["t"][i + 1]), gR, dist / e * 100.0))'
    old_prune = "for _, g_, sp_ in trades"
    assert old_app in src and old_prune in src, "strategy_lab.run_symbol changed - update full_backtest.py patch points"
    src = src.replace(old_app, 'trades.append((int(d["t"][i + 1]), gR, dist / e * 100.0, s_))')
    src = src.replace(old_prune, "for _, g_, sp_, *_x in trades").replace("def run_symbol(", "def run_symbol_ex(", 1)
    exec(compile(src, "run_symbol_ex", "exec"), sl.__dict__)      # نسخه‌ی یکسان با run_symbol + ستون جهت (side)
    return sl


# ----------------------------------------------------------------------------- RUN stage
def cmd_run(a):
    sl = _load_lab()
    import vwap_backtest as vb
    import lowtf_strategies as ls
    sl.DESIGN = True                                                # همه‌ی نردبان‌های کاتالوگ (CLASSIC/Q*/H*/B*/سفارشی) + BT
    want = set(a.tf) or set(sl.TFS)
    if "1m" not in want: a.days1 = 0
    if "5m" not in want: a.days5 = 0
    if not a.fetch and not a.csvdir:
        raise SystemExit("give --fetch SYMBOLS or --csvdir DIR")
    gens = set(a.gens) if a.gens else (set(CORE_GENS) | set(ls.GENS))
    eng = vb.load_engine(a.engine)
    ds = sl.load_dataset(a)
    res = {tf: {} for tf in sl.TFS}
    for sym, m in ds.items():
        ctx = sl.Ctx(m["15m"]); series = {}
        if "1m" in m: series["1m"] = m["1m"]
        if "5m" in m: series["5m"] = m["5m"]
        d15 = m["15m"]; k15 = d15["t"] >= d15["t"][-1] - a.days15 * 86_400_000
        series["15m"] = {k: v[k15] for k, v in d15.items()}
        h1 = sl.resample(d15, 3_600_000); k1 = h1["t"] >= h1["t"][-1] - a.days1h * 86_400_000
        series["1h"] = {k: v[k1] for k, v in h1.items()}
        h4 = sl.resample(d15, 14_400_000); k4 = h4["t"] >= h4["t"][-1] - a.days4h * 86_400_000
        series["4h"] = {k: v[k4] for k, v in h4.items()}
        for tf, d in series.items():
            if tf not in want:
                continue
            print(f"[full] {sym} {tf}: {len(d['c'])} candles", file=sys.stderr, flush=True)
            for v, tr in sl.run_symbol_ex(d, ctx, sl.STEP_MS[tf], eng, gens=gens, fee=a.fee).items():
                if tr:
                    res[tf].setdefault(v, []).append(to_arr(tr, sym))      # آرایه‌ی فشرده؛ نه tuple پایتونی (کاهش ~۸۰ برابری حجم)
    out = {tf: {v: np.concatenate(c) for v, c in vs.items()} for tf, vs in res.items()}
    with open(a.dump, "wb") as fh:
        pickle.dump(out, fh, protocol=4)
    print(f"[dump] {a.dump}: " + ", ".join(f"{tf}={sum(len(v) for v in out[tf].values())}" for tf in sl.TFS) +
          f"  size={os.path.getsize(a.dump) / 1e6:.0f}MB", flush=True)


# ----------------------------------------------------------------------------- compact trade arrays (memory-safe)
# علتِ شکستِ MERGE در اجرای قبلی: هر قسمت ده‌ها میلیون معامله را به‌صورت tuple پایتونی pickle می‌کرد (چند گیگابایت)
# و مرج همه را یک‌جا در RAM می‌خواند. اینجا هر معامله ۲۱ بایت است و مرج تایم‌فریم‌به‌تایم‌فریم کار می‌کند.
DT = np.dtype([("t", "i8"), ("g", "f4"), ("sp", "f4"), ("sd", "i1"), ("sym", "i4")])
EMPTY = np.zeros(0, DT)


def to_arr(tr, sym=None):
    """tr: list of (t,g,sp,side) یا (t,g,sp,sym,side) → آرایه‌ی فشرده."""
    if len(tr) == 0:
        return EMPTY
    if sym is not None:
        a = np.empty(len(tr), DT)
        a["t"] = [x[0] for x in tr]; a["g"] = [x[1] for x in tr]; a["sp"] = [x[2] for x in tr]; a["sd"] = [x[3] for x in tr]
        a["sym"] = zlib.crc32(sym.encode()) & 0x7fffffff
        return a
    a = np.empty(len(tr), DT)
    a["t"] = [x[0] for x in tr]; a["g"] = [x[1] for x in tr]; a["sp"] = [x[2] for x in tr]; a["sd"] = [x[4] for x in tr]
    a["sym"] = [zlib.crc32(str(x[3]).encode()) & 0x7fffffff for x in tr]
    return a


# ----------------------------------------------------------------------------- statistics
def metrics(net):
    net = np.asarray(net, float); n = len(net)
    if n == 0:
        return dict(n=0, wr=float("nan"), pf=float("nan"), avg=float("nan"), t=float("nan"), dd=0.0, tot=0.0, rec=float("nan"))
    gp, gl = net[net > 0].sum(), -net[net < 0].sum()
    eq = np.cumsum(net); dd = float(np.max(np.maximum.accumulate(eq) - eq))
    sd = net.std(ddof=1) if n > 1 else 0.0
    return dict(n=n, wr=float((net > 0).mean()), pf=float(gp / gl) if gl > 0 else 99.0, avg=float(net.mean()),
                t=float(net.mean() / (sd / math.sqrt(n))) if sd > 0 else float("nan"), dd=dd, tot=float(net.sum()),
                rec=float(net.sum() / dd) if dd > 0 else (99.0 if net.sum() > 0 else 0.0))


def split(A, frac=0.6):
    A = A[np.argsort(A["t"], kind="stable")]; cut = int(len(A) * frac)
    return A, A[:cut], A[cut:]


def netR(A, fee, floor=0.0):
    sp = A["sp"].astype(np.float64)
    m = sp >= floor
    return A["g"].astype(np.float64)[m] - fee / sp[m]


def ladder_of(ex):
    return "CLASSIC" if ex == "LAD" else ex


def far_target(ex, lc):
    k = ladder_of(ex)
    try:
        return float(lc.LADDERS[k][0][-1])
    except Exception:
        return 0.0


def is_bracket(ex):
    return ex == "BT" or (ex.startswith("B") and ex[1:].replace(".", "").isdigit())


def goals_met(m):
    """هدف‌ها روی یک بخش از معاملات (ALL یا TEST)."""
    miss = []
    if not (m["n"] and m["wr"] >= GOALS["win"]): miss.append("winrate<40%")
    if not (m["n"] and m["pf"] >= GOALS["pf_min"]): miss.append("PF<1.2")
    if m["n"] and m["pf"] > GOALS["pf_max"] and m["pf"] < 99: miss.append("PF>1.6(suspect-optimistic)")
    if not (m["n"] and m["rec"] >= GOALS["recovery"]): miss.append("drawdown-too-deep")
    return miss


# ----------------------------------------------------------------------------- diagnostics
def consistent_bucket_flags(A, fee, keyfn, min_n=30):
    """فقط دسته‌هایی که در TRAIN و TEST هر دو منفی (یا هر دو مثبت) و n کافی دارند - ضد نویز."""
    _, tr, te = split(A)
    def grp(part):
        if len(part) == 0:
            return {}
        net = netR(part, fee); k = keyfn(part)
        return {int(u): net[k == u] for u in np.unique(k)}
    a_, b_ = grp(tr), grp(te)
    bad, good = [], []
    for k in set(a_) & set(b_):
        a, b = a_[k], b_[k]
        if len(a) >= min_n and len(b) >= min_n:
            both = np.concatenate([a, b])
            if a.mean() < 0 and b.mean() < 0: bad.append((k, len(both), float(both.mean())))
            elif a.mean() > 0 and b.mean() > 0: good.append((k, len(both), float(both.mean())))
    return sorted(bad, key=lambda x: x[2]), sorted(good, key=lambda x: -x[2])


def hour_of(A): return ((A["t"] // 3_600_000) % 24) // 4 * 4        # بلوک‌های ۴ساعته‌ی UTC
def dow_of(A): return (A["t"] // 86_400_000 + 4) % 7                # 0=Mon
def side_of(A): return A["sd"].astype(np.int64)
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def floor_sweep(A, fee):
    _, tr, te = split(A)
    return [dict(floor=f, tr=metrics(netR(tr, fee, f)), te=metrics(netR(te, fee, f))) for f in FLOORS]


def pick_floor(rows):
    """کفِ استاپ: بیشترین میانگین R روی TRAIN با n کافی؛ نتیجه روی TEST گزارش می‌شود."""
    ok = [r for r in rows if r["tr"]["n"] >= GOALS["min_n_train"] and r["te"]["n"] >= GOALS["min_n_test"]]
    if not ok:
        return None
    return max(ok, key=lambda r: (r["tr"]["avg"], r["floor"]))


def diagnose(base, sel, fee, lad_stats):
    """base = معاملاتِ نسخه‌ی پایه (none|nat|CLASSIC) ، sel = معاملاتِ نسخه‌ی انتخاب‌شده. خروجی: فهرست عیب‌ها (فارسی + کد)."""
    flaws, fixes = [], []
    if len(base) >= 60:
        gross = float(np.mean(base["g"])); drag = float(np.mean(fee / base["sp"].astype(np.float64)))
        medsp = float(np.median(base["sp"]))
        if gross <= 0.02:
            flaws.append(f"[NO_RAW_EDGE] قبل از کارمزد هم لبه ندارد (میانگین ناخالص {gross:+.3f}R) → با تغییر نردبان/استاپ درست نمی‌شود؛ باید شرط ورود عوض شود.")
        if drag >= 0.10 or (gross > 0 and drag > 0.5 * gross):
            flaws.append(f"[FEE_BOUND] کارمزد {drag:.3f}R از هر معامله می‌خورد (میانه‌ی استاپ {medsp:.2f}%) → کفِ استاپ/استاپ ATR لازم است.")
            fixes.append("کفِ حداقل استاپ (nat_min_stop_pct) یا استاپ پهن‌تر A15/A3")
        bad, _g = consistent_bucket_flags(base, fee, side_of, 40)
        for k, n, avg in bad:
            flaws.append(f"[SIDE] جهت {'BUY' if k == 1 else 'SELL'} در TRAIN و TEST هر دو زیان‌ده است (n={n}, avg {avg:+.3f}R).")
            fixes.append(f"فقط {'SELL' if k == 1 else 'BUY'} (mx_*_side) — فقط اگر رژیم بازار عوض‌شدنی نباشد؛ با داده‌ی بیشتر تأیید شود")
        bad, _g = consistent_bucket_flags(base, fee, hour_of, 40)
        if bad:
            flaws.append("[HOURS] بلوک‌های ساعتیِ دائماً زیان‌ده (UTC): " + ", ".join(f"{k:02d}-{k + 4:02d}h({avg:+.2f}R,n={n})" for k, n, avg in bad[:3]))
        bad, _g = consistent_bucket_flags(base, fee, dow_of, 40)
        if bad:
            flaws.append("[WEEKDAY] روزهای دائماً زیان‌ده: " + ", ".join(f"{DOW[k]}({avg:+.2f}R,n={n})" for k, n, avg in bad[:3]))
    if lad_stats:
        avgs = [v["avg"] for v in lad_stats.values() if v["n"] >= 40]
        if len(avgs) >= 4 and max(avgs) - min(avgs) < 0.04:
            flaws.append(f"[LADDER_IRRELEVANT] اختلاف همه‌ی نردبان‌ها فقط {max(avgs) - min(avgs):.3f}R است → مشکل نردبان نیست؛ کیفیت ورود/استاپ است.")
    if len(sel) >= 100:
        _, tr, te = split(sel); a, b = metrics(netR(tr, fee)), metrics(netR(te, fee))
        if a["avg"] > 0 and b["avg"] < 0.5 * a["avg"]:
            flaws.append(f"[OVERFIT_RISK] TEST ({b['avg']:+.3f}R) خیلی ضعیف‌تر از TRAIN ({a['avg']:+.3f}R) است؛ انتخاب پایدار نیست.")
    return flaws, fixes


# ----------------------------------------------------------------------------- MERGE stage
def _tf_of_path(p):
    parts = os.path.basename(p).replace(".pkl", "").split("_")
    return parts[1] if len(parts) > 1 and parts[1] in TF_ORDER else None


def _load_tf(paths, tf):
    """فقط تایم‌فریم tf را از قسمت‌ها می‌خواند و فوراً به آرایه‌ی فشرده تبدیل می‌کند."""
    chunks = defaultdict(list)
    for p in paths:
        hint = _tf_of_path(p)
        if hint not in (None, tf):
            continue
        with open(p, "rb") as fh:
            part = pickle.load(fh)
        for v, tr in (part.get(tf) or {}).items():
            chunks[v].append(tr if isinstance(tr, np.ndarray) else to_arr(tr))
        del part
    out = {}
    for v in list(chunks):                                   # یکی‌یکی concat و آزاد می‌کنیم تا اوج RAM دو برابر نشود
        c = chunks.pop(v)
        out[v] = np.concatenate(c) if len(c) > 1 else c[0]
        del c
    return out


def cmd_merge(a):
    import ladder_catalog as lc
    fee = a.fee
    os.makedirs(a.out_dir, exist_ok=True)
    rows_csv, recs, lines = [], {}, []
    P = lines.append
    P(f"FULL BACKTEST | round-trip cost {fee:.2f}% | goals: win>=40%, PF 1.2-1.6, farthest target>=2R, recovery>=1.5 | selection on first 60% (TRAIN), judged on last 40% (TEST)")
    P("Timeframe priority (user): 5m first. 1m last.")
    summary = []
    for tf in TF_ORDER:
        R = _load_tf(a.pickles, tf)
        if not R:
            continue
        P("\n" + "=" * 118); P(f"TIMEFRAME {tf}   priority {TF_PRIORITY.get(tf, '')}"); P("=" * 118)
        by_g = defaultdict(list)
        for v in R:
            by_g[v.split("|")[0]].append(v)
        for g in sorted(by_g):
            cands = []
            for v in by_g[g]:
                _, fl, sp, ex = v.split("|")
                allt, trn, tst = split(R[v])
                m_all, m_tr, m_te = metrics(netR(allt, fee)), metrics(netR(trn, fee)), metrics(netR(tst, fee))
                span = max(1.0, (int(allt["t"][-1]) - int(allt["t"][0])) / 86_400_000) if len(allt) else 1.0
                nsym = len(np.unique(allt["sym"])) if len(allt) else 1
                rows_csv.append(dict(tf=tf, strategy=g, filter=fl, stop=sp, ladder=ladder_of(ex), n=m_all["n"], winrate=round(m_all["wr"], 4) if m_all["n"] else "",
                                     pf=round(m_all["pf"], 3) if m_all["n"] else "", avg_net_r=round(m_all["avg"], 4) if m_all["n"] else "",
                                     t=round(m_all["t"], 2) if m_all["n"] and m_all["t"] == m_all["t"] else "", max_dd_r=round(m_all["dd"], 2),
                                     train_avg=round(m_tr["avg"], 4) if m_tr["n"] else "", test_avg=round(m_te["avg"], 4) if m_te["n"] else "",
                                     per_day_total=round(len(allt) / span, 3), symbols=nsym))
                if not a.allow_brackets and is_bracket(ex):
                    continue
                if m_tr["n"] >= GOALS["min_n_train"] and m_te["n"] >= GOALS["min_n_test"]:
                    cands.append(dict(v=v, fl=fl, sp=sp, ex=ex, m_all=m_all, m_tr=m_tr, m_te=m_te, perday=len(allt) / span, nsym=nsym))
            title = f"{g}@{tf}"
            if not cands:
                P(f"\n[{title}] NO variant has enough trades (need train>={GOALS['min_n_train']}, test>={GOALS['min_n_test']}) → not testable on this data/timeframe.")
                recs[title] = dict(status="NO_DATA")
                continue
            far_ok = [c for c in cands if far_target(c["ex"], lc) >= GOALS["far_target"] or c["ex"] == "BT"]
            pool = far_ok or cands
            best = max(pool, key=lambda c: (round(c["m_tr"]["avg"], 4), c["m_all"]["t"] if c["m_all"]["t"] == c["m_all"]["t"] else -9,
                                            c["sp"] == "nat", c["fl"] == "none", c["ex"] == "LAD"))   # تساوی → ساده‌ترین (استاپ زنده/بدون فیلتر)
            lad_stats = {}
            for c in cands:
                if c["fl"] == best["fl"] and c["sp"] == best["sp"]:
                    k = ladder_of(c["ex"])
                    lad_stats[k] = dict(n=c["m_all"]["n"], avg=c["m_all"]["avg"], pf=c["m_all"]["pf"], wr=c["m_all"]["wr"], dd=c["m_all"]["dd"],
                                        tr=c["m_tr"]["avg"], te=c["m_te"]["avg"], far=far_target(c["ex"], lc))
            base = R.get(f"{g}|none|nat|LAD", EMPTY)
            sel = R[best["v"]]
            sweep = floor_sweep(sel, fee); pf_ = pick_floor(sweep)
            flaws, fixes = diagnose(base, sel, fee, lad_stats)
            miss_all, miss_te = goals_met(best["m_all"]), goals_met(best["m_te"])
            tpos = best["m_te"]["avg"] > 0 and best["m_tr"]["avg"] > 0
            t_ok = best["m_all"]["t"] == best["m_all"]["t"] and best["m_all"]["t"] >= GOALS["t_min"]
            if tpos and not miss_te and not miss_all and t_ok:
                status = "MEETS_GOALS"
            elif tpos and best["m_te"]["pf"] >= 1.05 and best["m_all"]["t"] == best["m_all"]["t"] and best["m_all"]["t"] >= GOALS["t_near"]:
                status = "NEAR"        # «سرنخ»: روی داده‌ی بیشتر تأیید شود؛ بهترین-از-صدها-ترکیب است و ممکن است شانسی باشد
            else:
                status = "FAIL"
            bm, bt = best["m_all"], best["m_te"]
            P(f"\n[{title}] status={status}   best-by-TRAIN: filter={best['fl']} stop={best['sp']} ladder={ladder_of(best['ex'])}   (far target {far_target(best['ex'], lc):g}R)")
            P(f"   ALL  n={bm['n']:6d} win={bm['wr'] * 100:5.1f}% PF={bm['pf']:5.2f} avg={bm['avg']:+.3f}R t={bm['t']:5.2f} maxDD={bm['dd']:6.1f}R recovery={bm['rec']:5.2f} /day(all syms)={best['perday']:.2f} syms={best['nsym']}")
            P(f"   TEST n={bt['n']:6d} win={bt['wr'] * 100:5.1f}% PF={bt['pf']:5.2f} avg={bt['avg']:+.3f}R   train avg={best['m_tr']['avg']:+.3f}R   goals missed on TEST: {', '.join(miss_te) or 'none'}")
            if lad_stats:
                P("   ladders (same filter/stop; sorted by TRAIN avg):  " + "  ".join(
                    f"{k}:{v['tr']:+.3f}/{v['te']:+.3f}(win {v['wr'] * 100:.0f}%,PF {v['pf']:.2f})" for k, v in sorted(lad_stats.items(), key=lambda kv: -kv[1]["tr"])[:8]))
            P("   stop-floor sweep (TRAIN→TEST avg R, n_test):  " + "  ".join(f">={r['floor']:g}%: {r['tr']['avg']:+.3f}→{r['te']['avg']:+.3f} n={r['te']['n']}" for r in sweep if r["tr"]["n"] >= 20))
            if pf_:
                P(f"   suggested stop floor: {pf_['floor']:g}%  → TEST win={pf_['te']['wr'] * 100:.0f}% PF={pf_['te']['pf']:.2f} avg={pf_['te']['avg']:+.3f}R n={pf_['te']['n']}")
            for f_ in flaws:
                P("   ✗ " + f_)
            if status == "FAIL" and not flaws:
                P("   ✗ [NO_STABLE_EDGE] هیچ ترکیبی هم در TRAIN و هم در TEST مثبت نشد.")
            recs[title] = dict(status=status, tf=tf, strategy=g, filter=best["fl"], stop=best["sp"], ladder=ladder_of(best["ex"]), far_target_r=far_target(best["ex"], lc),
                               suggested_min_stop_pct=pf_["floor"] if pf_ else None,
                               all=dict(n=bm["n"], win=round(bm["wr"], 4), pf=round(bm["pf"], 3), avg_r=round(bm["avg"], 4), max_dd_r=round(bm["dd"], 2), per_day=round(best["perday"], 3)),
                               test=dict(n=bt["n"], win=round(bt["wr"], 4), pf=round(bt["pf"], 3), avg_r=round(bt["avg"], 4)),
                               goals_missed_on_test=miss_te, flaws=[f.split("]")[0].lstrip("[") for f in flaws], fixes=sorted(set(fixes)))
            summary.append((tf, g, status, ladder_of(best["ex"]), best["fl"], best["sp"], bm, bt, best["perday"]))
        del R
    P("\n" + "=" * 118); P("SUMMARY (5m first)"); P("=" * 118)
    P(f"{'tf':3s} {'strategy':8s} {'status':12s} {'filter':6s} {'stop':4s} {'ladder':8s} | {'n':>6s} {'win%':>5s} {'PF':>5s} {'avgR':>7s} | TEST {'win%':>5s} {'PF':>5s} {'avgR':>7s} | /day")
    for tf, g, st, lad, fl, sp, bm, bt, pdv in sorted(summary, key=lambda r: (TF_ORDER.index(r[0]), {"MEETS_GOALS": 0, "NEAR": 1, "FAIL": 2}[r[2]], -r[7]["avg"])):
        P(f"{tf:3s} {g:8s} {st:12s} {fl:6s} {sp:4s} {lad:8s} | {bm['n']:6d} {bm['wr'] * 100:5.1f} {bm['pf']:5.2f} {bm['avg']:+7.3f} | TEST {bt['wr'] * 100:5.1f} {bt['pf']:5.2f} {bt['avg']:+7.3f} | {pdv:.2f}")
    nm = sum(1 for s in summary if s[2] == "MEETS_GOALS"); nn = sum(1 for s in summary if s[2] == "NEAR")
    P(f"\nMEETS_GOALS: {nm} | NEAR: {nn} | FAIL: {len(summary) - nm - nn}   (hundreds of variants are compared per unit: MEETS_GOALS/NEAR = 'confirm on fresh data', never proof. On zero-edge synthetic data NEAR must stay rare - see self-check)")
    txt = os.path.join(a.out_dir, "full_backtest_report.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(a.out_dir, "full_backtest_results.csv"), "w", newline="", encoding="utf-8") as f:
        if rows_csv:
            w = csv.DictWriter(f, fieldnames=list(rows_csv[0])); w.writeheader(); w.writerows(rows_csv)
    with open(os.path.join(a.out_dir, "full_backtest_recommendations.json"), "w", encoding="utf-8") as f:
        json.dump(dict(_meta=dict(fee_pct=fee, goals=GOALS, note="selection on TRAIN only; 'test' = unseen 40%"), units=recs), f, indent=1, ensure_ascii=False)
    print("\n".join(lines))
    print(f"\n[written] {txt} (+ .csv, recommendations.json)")


# ----------------------------------------------------------------------------- CLI
def main():
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="simulate every strategy x timeframe x filter x stop x ladder on real candles and dump trades")
    r.add_argument("--engine", default=os.path.join(HERE, "candle_engine.py"))
    r.add_argument("--fetch", nargs="*", default=[]); r.add_argument("--csvdir", default="")
    r.add_argument("--fee", type=float, default=0.10); r.add_argument("--source", default="auto")
    r.add_argument("--out", default=os.path.join(HERE, "lab_data"))
    r.add_argument("--days1", type=int, default=21); r.add_argument("--days5", type=int, default=75); r.add_argument("--days15", type=int, default=180)
    r.add_argument("--days1h", type=int, default=240); r.add_argument("--days4h", type=int, default=700)
    r.add_argument("--tf", nargs="*", default=[]); r.add_argument("--gens", nargs="*", default=[])
    r.add_argument("--dump", required=True)
    m = sub.add_parser("merge", help="analyse pickles: best ladder per strategy, flaws, goal check")
    m.add_argument("pickles", nargs="+"); m.add_argument("--fee", type=float, default=0.10)
    m.add_argument("--out-dir", default="full_backtest_out"); m.add_argument("--allow-brackets", action="store_true")
    a = ap.parse_args()
    {"run": cmd_run, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()

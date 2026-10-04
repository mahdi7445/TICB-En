# -*- coding: utf-8 -*-
"""
تحلیل آماریِ نتایج واقعیِ ربات (مستقل از ربات؛ فقط data/trade_history.json را می‌خواند).

اجرا:   python analyze_trades.py [مسیر trade_history.json] [--fee 0.12] [--since 2026-09-01]

  --fee  : کارمزد+اسلیپیجِ رفت‌وبرگشتِ تخمینی به‌درصدِ حجم معامله (پیش‌فرض ۰٫۱۲٪ ≈ ۲×۰٫۰۵٪ کارمزدِ
           تیکر + کمی اسلیپیج). عدد واقعیِ صرافی/بروکرِ خودتان را بگذارید.

این اسکریپت چهار سؤال را با داده‌ی خودِ ربات جواب می‌دهد (نه با حدس):
  ۱) آیا میانگینِ R واقعاً مثبت است یا در محدوده‌ی نوسانِ تصادفی؟ (بازه‌ی اطمینان ۹۵٪ + تعداد معاملهٔ لازم)
  ۲) قیمت در هر معامله تا چند R به نفع ما رفته؟ (احتمال رسیدن به ۱R/۲R/۴R/۶R - بر پایه‌ی mfe_r)
  ۳) بعد از کم‌کردنِ کارمزد، کدام نماد/تایم‌فریم/اندازه‌ی استاپ هنوز مثبت است؟ (بر پایه‌ی r_pct)
  ۴) آیا رشته‌ی باخت‌ها بیشتر از حدِ شانس خوشه‌ای است؟ (نشانه‌ی همبستگیِ معاملات)

فیلدهای mfe_r / mae_r / r_pct فقط روی معاملاتی که بعد از این نسخه بسته شده‌اند ثبت می‌شوند؛ بخش‌های ۲ و ۳
با افزایش داده دقیق‌تر می‌شوند. هیچ‌چیز در ربات را تغییر نمی‌دهد.
"""
import sys, json, math, random, os
from datetime import datetime


def _args(argv):
    path, fee, since = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "trade_history.json"), 0.12, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--fee":
            fee = float(argv[i + 1]); i += 1
        elif a == "--since":
            since = argv[i + 1]; i += 1
        elif not a.startswith("--"):
            path = a
        i += 1
    return path, fee, since


def _eff_tf(h):
    tf = h.get("tf")
    return tf if tf and tf != "manual" else (h.get("logical_tf") or "?")


def _mean_sd(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return m, sd


def _boot_ci(xs, k=3000, seed=1):
    rnd = random.Random(seed); n = len(xs)
    means = sorted(sum(rnd.choice(xs) for _ in range(n)) / n for _ in range(k))
    return means[int(0.025 * k)], means[int(0.975 * k)]


def _row(name, rs):
    n = len(rs); m, sd = _mean_sd(rs)
    half = 1.96 * sd / math.sqrt(n) if n > 1 else float("nan")
    flag = "  (n<30: too few to judge)" if n < 30 else ""
    return f"  {name:12s} n={n:4d}  win={sum(r > 0 for r in rs) / n * 100:3.0f}%  avg={m:+.3f}R  ±{half:.2f}{flag}"


def _longest_losing_streak(rs):
    best = cur = 0
    for r in rs:
        if r < 0:
            cur += 1; best = max(best, cur)
        elif r > 0:
            cur = 0
    return best


def main():
    path, fee, since = _args(sys.argv[1:])
    hist = [h for h in json.load(open(path, encoding="utf-8")) if isinstance(h, dict) and isinstance(h.get("final_r"), (int, float))]
    hist.sort(key=lambda h: h.get("closed_at") or "")
    if since:
        hist = [h for h in hist if (h.get("closed_at") or "") >= since]
    if len(hist) < 5:
        print("Not enough closed trades to analyse (need at least 5)."); return
    rs = [h["final_r"] for h in hist]
    n = len(rs); m, sd = _mean_sd(rs); lo, hi = _boot_ci(rs)

    print(f"=== 1) Is the edge real?  ({n} closed trades) ===")
    wins = sum(r > 0 for r in rs); small = sum(0 < r <= 0.25 for r in rs); losses = sum(r < 0 for r in rs)
    print(f"  total {sum(rs):+.1f}R | avg {m:+.3f}R | sd {sd:.2f}R | 95% CI of avg: [{lo:+.3f}, {hi:+.3f}]R")
    print(f"  wins {wins} ({wins / n * 100:.0f}%) of which 'small' (<=+0.25R, i.e. breakeven-after-T1): {small} | losses {losses}")
    if lo <= 0 <= hi:
        need = int((1.96 * sd / abs(m)) ** 2) if abs(m) > 1e-9 else None
        print("  -> CI contains 0: with this sample the positive average is NOT statistically proven."
              + (f" At the current avg/sd roughly {need} trades would be needed." if need else ""))
    elif lo > 0:
        print("  -> CI is entirely above 0: the edge is statistically positive (before costs).")
    else:
        print("  -> CI is entirely below 0: the system is losing on this sample.")

    print("\n=== 1b) By close type ===")
    by = {}
    for h in hist:
        by.setdefault(h.get("close_type", "?"), []).append(h["final_r"])
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:16s} n={len(v):4d}  avg={sum(v) / len(v):+.2f}R  share={len(v) / n * 100:4.1f}%")

    with_mfe = [h for h in hist if isinstance(h.get("mfe_r"), (int, float))]
    print(f"\n=== 2) How far does price go in our favour?  ({len(with_mfe)} trades with MFE data) ===")
    if len(with_mfe) >= 10:
        for k in (0.5, 0.8, 1, 1.5, 2, 3, 4, 6):
            p = sum(h["mfe_r"] >= k for h in with_mfe) / len(with_mfe)
            print(f"  P(reached >= {k:>3}R) = {p * 100:5.1f}%")
        stopped = [h for h in with_mfe if h.get("close_type") == "stop"]
        if stopped:
            for k in (0.5, 0.8):
                print(f"  full-loss trades that had first reached >= {k}R: "
                      f"{sum(h['mfe_r'] >= k for h in stopped) / len(stopped) * 100:.0f}%  (n={len(stopped)})")
        print("  Use these reach-probabilities (not guesses) to decide where partial exits/stops should sit.")
    else:
        print("  Not enough MFE data yet - it is recorded automatically from now on; re-run after ~50 more trades.")

    with_r = [h for h in hist if isinstance(h.get("r_pct"), (int, float)) and h["r_pct"] > 0]
    print(f"\n=== 3) After costs (round-trip {fee:.2f}% of notional)  ({len(with_r)} trades with r_pct) ===")
    if len(with_r) >= 10:
        net = [h["final_r"] - fee / h["r_pct"] for h in with_r]
        gross_m = sum(h["final_r"] for h in with_r) / len(with_r)
        print(f"  gross avg {gross_m:+.3f}R  ->  net avg {sum(net) / len(net):+.3f}R   "
              f"(median stop distance {sorted(h['r_pct'] for h in with_r)[len(with_r) // 2]:.2f}% of price, "
              f"median cost {sorted(fee / h['r_pct'] for h in with_r)[len(with_r) // 2]:.2f}R per trade)")
        buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.8), (0.8, 1e9)]
        for a, b in buckets:
            sel = [(h, nr) for h, nr in zip(with_r, net) if a <= h["r_pct"] < b]
            if sel:
                g = sum(h["final_r"] for h, _ in sel) / len(sel); nn = sum(x for _, x in sel) / len(sel)
                lab = f"{a:.1f}-{b:.1f}%" if b < 1e8 else f">{a:.1f}%"
                print(f"  stop {lab:9s} n={len(sel):4d}  gross {g:+.3f}R  net {nn:+.3f}R")
        print("  By timeframe (net of costs):")
        groups = {}
        for h, nr in zip(with_r, net):
            groups.setdefault(_eff_tf(h), []).append(nr)
        for k, v in sorted(groups.items()):
            print(_row(k, v))
    else:
        print("  Not enough r_pct data yet - recorded automatically from now on.")

    print("\n=== 3b) By symbol (gross R, all trades) ===")
    groups = {}
    for h in hist:
        groups.setdefault(h.get("symbol", "?"), []).append(h["final_r"])
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(_row(k, v))

    print("\n=== 4) Are losses clustered more than chance? ===")
    p_loss = losses / n; obs = _longest_losing_streak(rs)
    rnd = random.Random(7); ge = 0; sims = 4000
    for _ in range(sims):
        if _longest_losing_streak([-1 if rnd.random() < p_loss else 1 for _ in range(n)]) >= obs:
            ge += 1
    print(f"  longest losing streak {obs}; if trades were independent that long a streak would happen in {ge / sims * 100:.1f}% of random runs")
    if ge / sims < 0.05:
        print("  -> losses are clustered (many trades lose together, e.g. several symbols/timeframes stopped by the same market move).")
        print("     That points to correlated exposure - limiting simultaneous same-direction signals would cut drawdown.")
    else:
        print("  -> the streak is within normal chance.")


if __name__ == "__main__":
    main()

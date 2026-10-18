# -*- coding: utf-8 -*-
"""
perf_report.py — گزارش عملکرد دقیق برای ادمین (هر استراتژی / نردبان / نماد / تایم‌فریم / جهت / نوع بسته‌شدن).

ورودی: رکوردهای trade_history.json (+ آرشیو نامحدود data/trade_archive.jsonl که هر معاملهٔ بسته‌شده به آن اضافه می‌شود؛
trade_history فقط ۲۰۰۰ مورد آخر را نگه می‌دارد). همه‌ی اعداد بر حسب R هستند؛ «خالص» = پس از کارمزد (net_r_est یا برآورد از r_pct).
فقط Python خالص (+ matplotlib اختیاری برای نمودار) تا ادمین‌پنل و تست‌ها بتوانند import کنند.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

FEE_PCT = 0.10          # کارمزد+اسلیپیج رفت‌وبرگشت (٪ قیمت) برای رکوردهایی که net_r_est ندارند
ARCHIVE_NAME = "trade_archive.jsonl"


# ----------------------------------------------------------------------------- ورودی
def archive_append(rec: Dict[str, Any], data_dir: str) -> None:
    """افزودن یک رکورد بسته‌شده به آرشیو نامحدود (خطا هرگز مانع ثبت اصلی نمی‌شود)."""
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, ARCHIVE_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def load_records(history: Optional[List[Dict[str, Any]]] = None, data_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """ادغام تاریخچه + آرشیو، حذف تکراری (signal_id + closed_at)، فقط رکوردهای دارای final_r عددی، مرتب بر اساس closed_at."""
    rows: List[Dict[str, Any]] = [h for h in (history or []) if isinstance(h, dict)]
    if data_dir:
        p = os.path.join(data_dir, ARCHIVE_NAME)
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            if isinstance(d, dict):
                                rows.append(d)
                        except Exception:
                            pass
        except FileNotFoundError:
            pass
    seen, out = set(), []
    for h in rows:
        fr = h.get("final_r")
        if not isinstance(fr, (int, float)) or fr != fr:
            continue
        k = (h.get("signal_id"), h.get("closed_at"))
        if k in seen:
            continue
        seen.add(k); out.append(h)
    out.sort(key=lambda h: h.get("closed_at") or "")
    return out


def _ts(iso: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def filter_period(recs: List[Dict[str, Any]], days: Optional[float] = None, since: Optional[str] = None, now: Optional[float] = None) -> List[Dict[str, Any]]:
    lo = None
    if days:
        lo = (now or datetime.now(timezone.utc).timestamp()) - days * 86400
    if since:
        t = _ts(since if "T" in since else since + "T00:00:00+00:00")
        lo = t if lo is None else max(lo, t or 0)
    if lo is None:
        return list(recs)
    return [h for h in recs if (_ts(h.get("closed_at")) or 0) >= lo]


# ----------------------------------------------------------------------------- برچسب‌ها
def strategy_of(h: Dict[str, Any]) -> str:
    m = h.get("module")
    if m:
        return str(m)
    s = h.get("strategy")
    if isinstance(s, str) and s.startswith("module:"):
        return s.split(":", 1)[1]
    if isinstance(s, str) and s:
        return s
    sid = str(h.get("signal_id") or "")
    if sid.startswith("ALT") or h.get("tf") == "manual":
        return "manual/relay"
    if sid.startswith("CS") or sid.startswith("PS"):
        return "personal"
    return "native-candle"


def ladder_of(h: Dict[str, Any]) -> str:
    return str(h.get("ladder") or ("CLASSIC" if strategy_of(h) == "native-candle" else "—"))


def tf_of(h: Dict[str, Any]) -> str:
    t = h.get("logical_tf") or h.get("tf") or "?"
    return str(t)


def net_r(h: Dict[str, Any], fee_pct: float = FEE_PCT) -> float:
    n = h.get("net_r_est")
    if isinstance(n, (int, float)) and n == n:
        return float(n)
    rp = h.get("r_pct") or h.get("stop_pct")
    fr = float(h["final_r"])
    if isinstance(rp, (int, float)) and rp > 0:
        return fr - fee_pct / float(rp)
    return fr


# ----------------------------------------------------------------------------- معیارها
def _streaks(vals: List[float]) -> Tuple[int, int, int]:
    best_l = best_w = cur = 0
    for v in vals:
        if v < 0:
            cur = cur + 1 if cur > 0 else 1
        elif v > 0:
            cur = cur - 1 if cur < 0 else -1
        else:
            cur = 0
        if cur > 0:
            best_l = max(best_l, cur)
        if cur < 0:
            best_w = max(best_w, -cur)
    # جاری: تعداد باخت/برد متوالی در انتهای فهرست (منفی = برد)
    cur_l = 0
    for v in reversed(vals):
        if v < 0:
            cur_l += 1
        else:
            break
    return best_l, best_w, cur_l


def max_drawdown(vals: List[float]) -> Tuple[float, int, int]:
    """(بیشترین افت از قله بر حسب R, تعداد معامله از قله تا کف, بیشترین طول زمانی بدون قلهٔ جدید بر حسب معامله)."""
    peak = cum = 0.0; dd = 0.0; pk_i = 0; dd_len = 0; longest = 0; last_peak_i = 0
    for i, v in enumerate(vals, 1):
        cum += v
        if cum > peak:
            peak = cum; last_peak_i = i
        longest = max(longest, i - last_peak_i)
        if peak - cum > dd:
            dd = peak - cum; dd_len = i - last_peak_i
    return dd, dd_len, longest


def metrics(recs: List[Dict[str, Any]], fee_pct: float = FEE_PCT) -> Dict[str, Any]:
    n = len(recs)
    if not n:
        return {"n": 0}
    g = [float(h["final_r"]) for h in recs]
    nt = [net_r(h, fee_pct) for h in recs]
    wins = [x for x in nt if x > 0]; loss = [x for x in nt if x < 0]
    gp, gl = sum(wins), -sum(loss)
    mean = sum(nt) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in nt) / (n - 1)) if n > 1 else 0.0
    dd, dd_len, longest_flat = max_drawdown(nt)
    sl, sw, cur_l = _streaks(nt)
    mfe = [h["mfe_r"] for h in recs if isinstance(h.get("mfe_r"), (int, float))]
    mae = [h["mae_r"] for h in recs if isinstance(h.get("mae_r"), (int, float))]
    hold = []
    for h in recs:
        a, b = _ts(h.get("opened_at")), _ts(h.get("closed_at"))
        if a and b and b >= a:
            hold.append((b - a) / 3600.0)
    hits: Dict[str, int] = {}
    for h in recs:
        for t in (h.get("targets_hit") or []):
            k = str(t) if str(t).startswith("T") else f"T{t}"
            hits[k] = hits.get(k, 0) + 1
    t0, t1 = _ts(recs[0].get("closed_at")), _ts(recs[-1].get("closed_at"))
    days = max(1.0, ((t1 - t0) / 86400.0)) if t0 and t1 else 1.0
    return {
        "n": n, "wins": len(wins), "losses": len(loss), "be": n - len(wins) - len(loss),
        "win_pct": 100.0 * len(wins) / n, "gross_total": sum(g), "net_total": sum(nt), "net_avg": mean, "gross_avg": sum(g) / n,
        "fee_drag": (sum(g) - sum(nt)) / n, "pf": (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0),
        "avg_win": (gp / len(wins)) if wins else 0.0, "avg_loss": (-gl / len(loss)) if loss else 0.0,
        "payoff": ((gp / len(wins)) / (gl / len(loss))) if wins and loss else 0.0,
        "sd": sd, "t": (mean / (sd / math.sqrt(n))) if sd > 0 else 0.0,
        "max_dd": dd, "dd_len": dd_len, "longest_flat": longest_flat, "recovery": (sum(nt) / dd) if dd > 0 else 0.0,
        "max_loss_streak": sl, "max_win_streak": sw, "cur_loss_streak": cur_l,
        "avg_mfe": (sum(mfe) / len(mfe)) if mfe else None, "avg_mae": (sum(mae) / len(mae)) if mae else None,
        "capture": ((sum(nt) / n) / (sum(mfe) / len(mfe))) if mfe and sum(mfe) > 0 else None,
        "avg_hold_h": (sum(hold) / len(hold)) if hold else None, "hits": hits,
        "best": max(nt), "worst": min(nt), "per_day": sum(nt) / days, "trades_per_day": n / days,
        "first": recs[0].get("closed_at"), "last": recs[-1].get("closed_at"),
    }


def group(recs: List[Dict[str, Any]], key: Callable[[Dict[str, Any]], str]) -> "OrderedDict[str, List[Dict[str, Any]]]":
    d: Dict[str, List[Dict[str, Any]]] = {}
    for h in recs:
        d.setdefault(key(h), []).append(h)
    return OrderedDict(sorted(d.items(), key=lambda kv: -sum(net_r(x) for x in kv[1])))


GROUPS: Dict[str, Tuple[str, Callable[[Dict[str, Any]], str]]] = {
    "s": ("Strategy", strategy_of), "l": ("Ladder", ladder_of), "t": ("Timeframe", tf_of),
    "y": ("Symbol", lambda h: str(h.get("symbol") or "?")), "d": ("Side", lambda h: str(h.get("side") or "?")),
    "c": ("Close reason", lambda h: str(h.get("close_type") or "?")),
    "h": ("Hour (UTC)", lambda h: f"{(datetime.fromtimestamp(_ts(h.get('opened_at')) or 0, tz=timezone.utc).hour):02d}" if _ts(h.get("opened_at")) else "?"),
    "w": ("Weekday", lambda h: (datetime.fromtimestamp(_ts(h.get("opened_at")) or 0, tz=timezone.utc).strftime("%a") if _ts(h.get("opened_at")) else "?")),
    "m": ("Month", lambda h: (h.get("closed_at") or "?")[:7]),
}


# ----------------------------------------------------------------------------- نمایش
def _f(v, spec="{:+.2f}", none="—"):
    return none if v is None else spec.format(v)


def row_line(name: str, m: Dict[str, Any], w: int = 14) -> str:
    if not m.get("n"):
        return f"{name[:w]:<{w}} —"
    return (f"{name[:w]:<{w}} n={m['n']:<4d} win {m['win_pct']:4.0f}% avg {m['net_avg']:+.2f}R tot {m['net_total']:+7.1f}R "
            f"PF {m['pf']:4.2f} DD {m['max_dd']:4.1f}R")


def summary_block(m: Dict[str, Any], title: str = "") -> str:
    if not m.get("n"):
        return (title + "\n" if title else "") + "No closed trades in this period."
    hit = " ".join(f"{k}:{100.0 * v / m['n']:.0f}%" for k, v in sorted(m["hits"].items())) or "—"
    L = [title] if title else []
    L += [
        f"Trades {m['n']} (W {m['wins']} / L {m['losses']} / BE {m['be']}) — win rate {m['win_pct']:.1f}%",
        f"Net total {m['net_total']:+.2f}R · avg/trade {m['net_avg']:+.3f}R (gross {m['gross_avg']:+.3f}R, fees ≈ {m['fee_drag']:.3f}R/trade)",
        f"Profit factor {m['pf']:.2f} · avg win {m['avg_win']:+.2f}R · avg loss {m['avg_loss']:+.2f}R · payoff {m['payoff']:.2f}",
        f"t-stat {m['t']:.2f} (≥2 ≈ unlikely luck; needs many trades) · std {m['sd']:.2f}R",
        f"Max drawdown {m['max_dd']:.2f}R over {m['dd_len']} trades · longest time below peak {m['longest_flat']} trades · recovery factor {m['recovery']:.2f}",
        f"Longest losing streak {m['max_loss_streak']} · longest winning streak {m['max_win_streak']} · current losing streak {m['cur_loss_streak']}",
        f"Avg MFE {_f(m['avg_mfe'])}R · avg MAE {_f(m['avg_mae'])}R · profit capture {_f(m['capture'], '{:.0%}')} of MFE",
        f"Target hit rates: {hit}",
        f"Best {m['best']:+.2f}R · worst {m['worst']:+.2f}R · ≈{m['trades_per_day']:.2f} trades/day · {m['per_day']:+.2f}R/day",
        f"Avg time in trade {_f(m['avg_hold_h'], '{:.1f}')} h · period {str(m['first'])[:10]} → {str(m['last'])[:10]}",
    ]
    return "\n".join(L)


def group_table(recs: List[Dict[str, Any]], gkey: str, min_n: int = 1, limit: int = 15) -> str:
    title, fn = GROUPS[gkey]
    lines = [f"— by {title} —"]
    items = [(k, v) for k, v in group(recs, fn).items() if len(v) >= min_n][:limit]
    for k, v in items:
        lines.append(row_line(k, metrics(v)))
    return "\n".join(lines) if len(lines) > 1 else f"— by {title} — (none)"


def full_report(recs: List[Dict[str, Any]], title: str = "Performance report") -> str:
    out = [f"{title}", "=" * len(title), "(R-multiples; 'net' = after estimated fees; all closed trades incl. archive)", "",
           summary_block(metrics(recs), "OVERALL")]
    for gk in ("s", "l", "t", "y", "d", "c", "m", "h", "w"):
        out += ["", group_table(recs, gk, limit=40)]
    # استراتژی × نردبان
    sl = group(recs, lambda h: f"{strategy_of(h)}|{ladder_of(h)}")
    out += ["", "— by Strategy × Ladder —"] + [row_line(k, metrics(v), 22) for k, v in sl.items()]
    return "\n".join(out)


def to_csv(recs: List[Dict[str, Any]]) -> str:
    cols = ["signal_id", "strategy", "ladder", "symbol", "tf", "side", "entry", "sl", "stop_pct", "final_r", "net_r", "mfe_r", "mae_r",
            "targets_hit", "close_type", "opened_at", "closed_at", "hold_hours"]
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    for h in recs:
        a, b = _ts(h.get("opened_at")), _ts(h.get("closed_at"))
        w.writerow([h.get("signal_id"), strategy_of(h), ladder_of(h), h.get("symbol"), tf_of(h), h.get("side"), h.get("entry"), h.get("sl"),
                    h.get("stop_pct") or h.get("r_pct"), h.get("final_r"), round(net_r(h), 4), h.get("mfe_r"), h.get("mae_r"),
                    ",".join(map(str, h.get("targets_hit") or [])), h.get("close_type"), h.get("opened_at"), h.get("closed_at"),
                    round((b - a) / 3600.0, 3) if a and b and b >= a else ""])
    return buf.getvalue()


def equity_png(recs: List[Dict[str, Any]], title: str, path: str) -> Optional[str]:
    """منحنی سرمایه (R تجمعی خالص) + منطقهٔ افت. None اگر داده کم یا matplotlib نبود."""
    if len(recs) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    nt = [net_r(h) for h in recs]; cum = []; c = 0.0; peak = 0.0; dd = []
    for v in nt:
        c += v; cum.append(c); peak = max(peak, c); dd.append(c - peak)
    x = list(range(1, len(cum) + 1))
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 5.6), dpi=110, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    bg, fg = "#0f1620", "#d8dee9"
    fig.patch.set_facecolor(bg)
    for a in (a1, a2):
        a.set_facecolor(bg); a.tick_params(colors=fg, labelsize=7); a.grid(color="#1c2633", lw=0.5)
        for sp in a.spines.values():
            sp.set_color("#2b3645")
    a1.plot(x, cum, color="#26a69a" if cum[-1] >= 0 else "#ef5350", lw=1.6)
    a1.axhline(0, color="#90caf9", lw=0.8, ls="--")
    a1.set_title(f"{title} — {len(recs)} trades, net {cum[-1]:+.1f}R", color=fg, fontsize=10, loc="left")
    a1.set_ylabel("cumulative R", color=fg, fontsize=8)
    a2.fill_between(x, dd, 0, color="#ef5350", alpha=0.5); a2.set_ylabel("drawdown R", color=fg, fontsize=8)
    a2.set_xlabel("closed trades", color=fg, fontsize=8)
    fig.tight_layout(); fig.savefig(path, facecolor=bg); plt.close(fig)
    return path

"""signal_chart.py — چارت سیگنال ماژول‌ها بر اساس «نردبان خاص همان استراتژی».
فقط matplotlib. chart(trade, klines) -> مسیر PNG (یا None). ورودی trade همان خروجی strategy_modules.build_trade است."""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional


BB = dict(facecolor="#0f1620", edgecolor="none", pad=1.5)


def _p(v: float) -> str:
    a = abs(v)
    return f"{v:.2f}" if a >= 100 else f"{v:.4f}" if a >= 1 else f"{v:.6f}"


def _chart_legacy(t: Dict[str, Any], klines: List[Dict[str, Any]], bars: int = 70, out: Optional[str] = None) -> Optional[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    ks = list(klines)[-bars:]
    if len(ks) < 10:
        return None
    buy = t["side"] == "BUY"
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=110)
    bg, fg = "#0f1620", "#d8dee9"
    fig.patch.set_facecolor(bg); ax.set_facecolor(bg)
    for i, k in enumerate(ks):
        up = k["c"] >= k["o"]
        col = "#26a69a" if up else "#ef5350"
        ax.plot([i, i], [k["l"], k["h"]], color=col, lw=0.8)
        ax.add_patch(Rectangle((i - 0.32, min(k["o"], k["c"])), 0.64, max(abs(k["c"] - k["o"]), 1e-12), color=col))
    n = len(ks)
    ax.set_xlim(-1, n + 14)
    entry, sl = t["entry"], t["sl0"]
    ax.axhline(entry, color="#90caf9", lw=1.1, ls="--")
    ax.text(n + 0.5, entry, f"ENTRY {_p(entry)}", color="#90caf9", va="center", fontsize=8, zorder=6, bbox=BB)
    ax.axhline(sl, color="#ef5350", lw=1.1)
    ax.text(n + 0.5, sl, f"SL {_p(sl)}  (-1R)", color="#ef5350", va="center", fontsize=8, zorder=6, bbox=BB)
    ax.axhspan(min(entry, sl), max(entry, sl), color="#ef5350", alpha=0.07)
    lv, wt = t["levels"], t["weights"]
    for i, (r, w, px) in enumerate(zip(lv, wt, t["targets"])):
        ax.axhline(px, color="#66bb6a", lw=1.0, alpha=0.9)
        lab = f"T{i + 1} {_p(px)}  {r:g}R" + (f" · {int(round(w * 100))}%" if len(lv) > 1 or w < 1 else " · 100%")
        ax.text(n + 0.5, px, lab, color="#66bb6a", va="center", fontsize=8, zorder=6, bbox=BB)
    if lv:
        ax.axhspan(entry, t["targets"][-1], color="#66bb6a", alpha=0.05)
    rem = max(0.0, 1.0 - sum(wt))
    ys = [k["h"] for k in ks] + [k["l"] for k in ks] + list(t["targets"]) + [sl, entry]
    pad = (max(ys) - min(ys)) * 0.06
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.tick_params(colors=fg, labelsize=7)
    for sp in ax.spines.values():
        sp.set_color("#2b3645")
    ax.grid(color="#1c2633", lw=0.5)
    ax.set_xticks([])
    note = f"ladder {t.get('profile')}" + (f" · runner {int(round(rem * 100))}% trail {t['trail']:g}R" if rem > 1e-9 else " · single target")
    ax.set_title(f"{t['symbol']} · {t['side']} · {t['module']} · {t['tf'].upper()}   [{note}]", color=fg, fontsize=10, loc="left")
    fig.tight_layout()
    out = out or os.path.join(tempfile.gettempdir(), f"mod_chart_{t['id']}.png")
    fig.savefig(out, facecolor=bg)
    plt.close(fig)
    return out


def chart(t: Dict[str, Any], klines: List[Dict[str, Any]], bars: int = 60, out: Optional[str] = None) -> Optional[str]:
    """چارت سیگنال به سبک ربات مرجع (SGNL/VIP، استایل «charles»): پس‌زمینه‌ی روشن، کندل سبز/قرمز، EMA7 آبی، خطوط چین
    Entry (آبی)، Stop (قرمز) و T1..Tn (سبز) با برچسب سمت راست، محور قیمت سمت راست و عنوان «SYMBOL TF · Entry».
    سطوح از نردبان همان معامله می‌آیند. فقط matplotlib/numpy (بدون mplfinance)؛ هر خطا → نسخه‌ی قدیمی."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime, timezone
        from matplotlib.patches import Rectangle
        ks = list(klines)[-bars:]
        if len(ks) < 15:
            return None
        n = len(ks)
        fig, ax = plt.subplots(figsize=(8.4, 5.6), dpi=100)
        fig.patch.set_facecolor("white"); ax.set_facecolor("#f7f7f7")
        up_c, dn_c = "#006340", "#a02128"
        for i, k in enumerate(ks):
            col = up_c if k["c"] >= k["o"] else dn_c
            ax.plot([i, i], [k["l"], k["h"]], color="black", lw=0.7, zorder=2)
            ax.add_patch(Rectangle((i - 0.35, min(k["o"], k["c"])), 0.7, max(abs(k["c"] - k["o"]), 1e-12), facecolor=col, edgecolor="black", lw=0.5, zorder=3))
        alpha = 2.0 / 8.0; ema = []; prev = None
        for k in ks:
            prev = k["c"] if prev is None else alpha * k["c"] + (1 - alpha) * prev
            ema.append(prev)
        ax.plot(range(n), ema, color="dodgerblue", lw=1.0, zorder=4)
        entry, sl = t["entry"], t["sl0"]
        lines = [("Entry", entry, "blue"), ("Stop", sl, "red")] + [(f"T{i + 1}", px, "green") for i, px in enumerate(t["targets"])]
        for name, val, col in lines:
            ax.axhline(val, color=col, lw=0.8, ls="--", zorder=1)
            ax.annotate(name, xy=(n - 1, val), xytext=(5, 0), textcoords="offset points", color=col, fontsize=8, va="center", fontweight="bold", annotation_clip=False)
        ys = [k["h"] for k in ks] + [k["l"] for k in ks] + [v for _, v, _ in lines]
        pad = (max(ys) - min(ys)) * 0.05
        ax.set_ylim(min(ys) - pad, max(ys) + pad); ax.set_xlim(-1, n + 4)
        step = max(1, n // 9)
        idx = list(range(0, n, step))
        ax.set_xticks(idx)
        ax.set_xticklabels([datetime.fromtimestamp(ks[i]["open_time"] / 1000, tz=timezone.utc).strftime("%H:%M") for i in idx], rotation=30, fontsize=8)
        ax.yaxis.tick_right(); ax.yaxis.set_label_position("right"); ax.set_ylabel("Price", fontsize=9)
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(color="#cccccc", lw=0.5, ls=":")
        for sp in ("top", "left"):
            ax.spines[sp].set_visible(False)
        ax.set_title(f"{str(t['symbol']).split('/')[0].upper()} {t['tf'].upper()} · Entry", fontsize=11, fontweight="bold")
        fig.tight_layout()
        out = out or os.path.join(tempfile.gettempdir(), f"mod_chart_{t['id']}.png")
        fig.savefig(out, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return out
    except Exception:  # noqa: BLE001
        return _chart_legacy(t, klines, 70, out)

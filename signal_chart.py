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


def chart(t: Dict[str, Any], klines: List[Dict[str, Any]], bars: int = 70, out: Optional[str] = None) -> Optional[str]:
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

#!/usr/bin/env python3
"""
Walk-forward backtest lab: does anchored VWAP (+/-1,2,3 sigma bands) improve the bot's
sub-1H timeframes?  Standalone tool - it never touches the live bots.

Variants (all costs charged, same R-ladder as the live bot [20/30/15/10 + 25% runner]):
  V0  baseline        - the live engine's own signals (candle_engine.step_candle_state + check_open_trade)
  V1  vwap_filter     - V0 signals, but LONG only if price > VWAP, SHORT only if price < VWAP
  V2  band2_reversal  - wick through +/-2 sigma band and close back inside -> trade back toward VWAP
  V3  band3_reversal  - same with the +/-3 sigma band
  V4  reclaim_trend   - trend side = side of VWAP; price dips through VWAP then closes back on the trend side
                        -> continuation entry (stop beyond the pullback extreme)
  Reversal variants (V2/V3) run with two exits: "ladder" (bot's R-ladder) and "mean" (full exit at VWAP).

Honesty rules baked in: chronological 60/40 split (nothing is tuned on the test part), fixed parameters,
round-trip cost deducted per trade (fee+slippage as % of price, converted to R), and a verdict that
requires NET average R > 0 in BOTH halves, t-stat >= 2 overall and enough trades.

Usage
  python3 vwap_backtest.py --fetch BTCUSDT ETHUSDT --tf 15m --days 120          # needs internet (OKX/KuCoin/Gate/Bybit/Binance/TwelveData, auto-fallback)
  python3 vwap_backtest.py --csv data/BTCUSDT_15m.csv --tf 15m                   # ts_ms,o,h,l,c,v
  python3 vwap_backtest.py --selftest                                            # synthetic sanity checks
"""
import argparse, csv, glob, importlib.util, math, os, sys, tempfile, types, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WARMUP_BARS = 12            # skip the first bars of each UTC session (bands not meaningful yet)
MAX_HOLD = 300              # bars; unresolved trades are closed at market
BAND_STOP_BUFFER_ATR = 0.25 # stop beyond the extreme by this many ATR
ATR_LEN = 14


# ----------------------------------------------------------------------------- data
def load_csv(path):
    rows = []
    with open(path) as f:
        for r in csv.reader(f):
            try:
                rows.append([float(x) for x in r[:6]])
            except ValueError:
                continue                      # header
    a = np.array(rows)
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "v": a[:, 5]}


STEP_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800}
SOURCES = ["okx", "kucoin", "gate", "bybit", "binance", "twelvedata"]   # tried in this order (--source auto)


def _http_json(url, timeout=30):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (vwap-lab)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _base(symbol):
    s = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


# every page function returns rows [t_ms, o, h, l, c, v] for candles with open time <= end_ms (any order)
def _page_okx(base, tf, end_ms):
    d = _http_json(f"https://www.okx.com/api/v5/market/history-candles?instId={base}-USDT&bar={tf}&after={end_ms + 1}&limit=100")
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in d.get("data", [])]


def _page_kucoin(base, tf, end_ms):
    typ = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min"}[tf]
    end = end_ms // 1000
    d = _http_json(f"https://api.kucoin.com/api/v1/market/candles?symbol={base}-USDT&type={typ}&startAt={end - 1400 * STEP_SEC[tf]}&endAt={end + 1}")
    # kucoin row order: time, open, close, high, low, volume, turnover
    return [[int(r[0]) * 1000, float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5])] for r in d.get("data", [])]


def _page_gate(base, tf, end_ms):
    end = end_ms // 1000
    d = _http_json(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={base}_USDT&interval={tf}&from={end - 990 * STEP_SEC[tf]}&to={end}")
    # gate row order: time, quote_volume, close, high, low, open, base_volume
    return [[int(r[0]) * 1000, float(r[5]), float(r[3]), float(r[4]), float(r[2]), float(r[6])] for r in d]


def _page_bybit(base, tf, end_ms):
    iv = {"1m": "1", "5m": "5", "15m": "15", "30m": "30"}[tf]
    d = _http_json(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={base}USDT&interval={iv}&end={end_ms}&limit=1000")
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in d["result"]["list"]]


def _page_binance(base, tf, end_ms):
    d = _http_json(f"https://data-api.binance.vision/api/v3/klines?symbol={base}USDT&interval={tf}&endTime={end_ms}&limit=1000")
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in d]


def _page_twelvedata(base, tf, end_ms):
    import datetime as _dt
    key = (os.environ.get("TWELVEDATA_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY not set")
    iv = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min"}[tf]
    end = _dt.datetime.fromtimestamp(end_ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    d = _http_json(f"https://api.twelvedata.com/time_series?symbol={base}/USD&interval={iv}&outputsize=5000&end_date={end.replace(' ', '%20')}&timezone=UTC&apikey={key}")
    if d.get("status") == "error" or "values" not in d:
        raise RuntimeError(d.get("message", "twelvedata error"))
    out = []
    for r in d["values"]:
        t = _dt.datetime.strptime(r["datetime"][:19], "%Y-%m-%d %H:%M:%S" if " " in r["datetime"] else "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
        out.append([int(t.timestamp() * 1000), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0)])
    return out


_PAGE_FN = {"okx": _page_okx, "kucoin": _page_kucoin, "gate": _page_gate, "bybit": _page_bybit,
            "binance": _page_binance, "twelvedata": _page_twelvedata}


def fetch_history(source, symbol, tf, days, sleep=0.15, now_ms=None):
    """Walk backwards page by page until `days` of candles are collected. Returns sorted, de-duplicated rows."""
    import time as _t
    base = _base(symbol); fn = _PAGE_FN[source]
    now_ms = now_ms or int(_t.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    step = STEP_SEC[tf] * 1000
    end = now_ms; got = {}; guard = 0
    while end > start_ms and guard < 2000:
        guard += 1
        rows = fn(base, tf, end)
        if not rows:
            break
        for r in rows:
            got[r[0]] = r
        oldest = min(r[0] for r in rows)
        if oldest >= end:                  # no progress -> stop (avoid an endless loop)
            break
        end = oldest - 1
        _t.sleep(sleep)
    rows = sorted(r for t, r in got.items() if t >= start_ms)
    # drop the still-forming last candle
    if rows and rows[-1][0] + step > now_ms:
        rows = rows[:-1]
    return rows


def fetch_candles(symbol, tf, days, out_dir, source="auto"):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"{_base(symbol)}_{tf}.csv")
    order = SOURCES if source == "auto" else [source]
    errs = []
    for src in order:
        try:
            rows = fetch_history(src, symbol, tf, days)
            if len(rows) < 500:
                raise RuntimeError(f"only {len(rows)} candles")
            with open(p, "w", newline="") as f:
                csv.writer(f).writerows(rows)
            print(f"[data] {symbol} {tf}: {len(rows)} candles from {src} -> {p}", file=sys.stderr)
            return p
        except Exception as e:                      # blocked region, rate limit, unknown pair ... -> next source
            errs.append(f"{src}: {type(e).__name__} {e}")
            print(f"[data] {src} failed for {symbol}: {type(e).__name__} {e}", file=sys.stderr)
    raise SystemExit("No data source worked:\n  " + "\n  ".join(errs) +
                     "\nTip: download candles yourself as CSV (ts_ms,open,high,low,close,volume) and use --csv file.csv, "
                     "or set TWELVEDATA_API_KEY (same key the bot uses) / use a VPN.")


# ----------------------------------------------------------------------------- indicators
def session_vwap(d):
    """UTC-day anchored VWAP + volume-weighted sigma. Returns vwap, sd, bar index within session."""
    n = len(d["c"]); tp = (d["h"] + d["l"] + d["c"]) / 3.0
    day = d["t"] // 86_400_000
    vwap = np.full(n, np.nan); sd = np.full(n, np.nan); pos = np.zeros(n, int)
    cv = cpv = cpv2 = 0.0; last_day = None; k = 0
    for i in range(n):
        if day[i] != last_day:
            last_day = day[i]; cv = cpv = cpv2 = 0.0; k = 0
        v = max(d["v"][i], 1e-12)
        cv += v; cpv += tp[i] * v; cpv2 += tp[i] ** 2 * v
        m = cpv / cv
        vwap[i] = m; sd[i] = math.sqrt(max(cpv2 / cv - m * m, 0.0)); pos[i] = k; k += 1
    return vwap, sd, pos


def atr_series(d, n=ATR_LEN):
    pc = np.concatenate([[d["c"][0]], d["c"][:-1]])
    tr = np.maximum(d["h"] - d["l"], np.maximum(abs(d["h"] - pc), abs(d["l"] - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= n:
        out[n - 1] = tr[:n].mean()
        for i in range(n, len(tr)):
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


# ----------------------------------------------------------------------------- engine access
def load_engine(path):
    sys.modules.setdefault("mplfinance", types.ModuleType("mplfinance"))
    for k, v in (("TELEGRAM_BOT_TOKEN", "x"), ("BOT_TOKEN", "x"), ("PRIVATE_CHANNEL_ID", "1"),
                 ("WALLET_ADDRESS_TRC20", "t"), ("WALLET_ADDRESS_BEP20", "b"), ("MIN_RISK_PCT", "0")):
        os.environ.setdefault(k, v)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    sp = importlib.util.spec_from_file_location("engine_under_test", path)
    m = importlib.util.module_from_spec(sp); sys.modules["engine_under_test"] = m
    cwd = os.getcwd(); os.chdir(tempfile.mkdtemp())          # engine writes small counter files
    try:
        sp.loader.exec_module(m)
    finally:
        os.chdir(cwd)
    m.next_signal_id = lambda *a, **k: "BT-000001"           # never touch the real signal-id counter file
    return m


def new_trade(eng, side, entry, sl):
    return eng.open_new_trade({"side": side, "price": entry, "sl": sl}, symbol="BT/USD", tf_key="15m",
                              display="BT", preferred_signal_id=None, candle_states={})


def ladder_result(eng, trade, d, start, fee_pct):
    """Feed candles start.. to the live engine's own check_open_trade. Returns (net_R, bars_held, gross_R)."""
    entry, r, side = trade["entry"], trade["r"], trade["side"]
    sign = 1 if side == "BUY" else -1
    last = min(len(d["c"]) - 1, start + MAX_HOLD)
    for i in range(start, last + 1):
        eng.check_open_trade(trade, {"o": d["o"][i], "h": d["h"][i], "l": d["l"][i], "c": d["c"][i]}, None)
        if trade.get("closed"):
            g = eng.compute_final_r(trade)
            return g - fee_pct / 100.0 * entry / r, i - start + 1, g
    # unresolved -> close remainder at market
    banked = 0.0
    for lvl, w in zip(eng.RR_TARGETS, (eng.W1, eng.W2, eng.W3, eng.W4)):
        if trade["hit"].get(str(lvl)):
            banked += w * lvl
    frac_done = sum(w for lvl, w in zip(eng.RR_TARGETS, (eng.W1, eng.W2, eng.W3, eng.W4)) if trade["hit"].get(str(lvl)))
    g = banked + (1 - frac_done) * (d["c"][last] - entry) / r * sign
    return g - fee_pct / 100.0 * entry / r, last - start + 1, g


def mean_result(side, entry, sl, d, start, vwap, fee_pct):
    """Full exit at the (moving) session VWAP, or at the stop, or at market after MAX_HOLD."""
    r = abs(entry - sl); sign = 1 if side == "BUY" else -1
    last = min(len(d["c"]) - 1, start + MAX_HOLD)
    for i in range(start, last + 1):
        stopped = d["l"][i] <= sl if side == "BUY" else d["h"][i] >= sl
        if stopped:                                   # stop checked first (conservative)
            g = -1.0
            return g - fee_pct / 100.0 * entry / r, i - start + 1, g
        tgt = vwap[i]
        if (side == "BUY" and d["h"][i] >= tgt) or (side == "SELL" and d["l"][i] <= tgt):
            g = (tgt - entry) / r * sign
            return g - fee_pct / 100.0 * entry / r, i - start + 1, g
    g = (d["c"][last] - entry) / r * sign
    return g - fee_pct / 100.0 * entry / r, last - start + 1, g


META = {"V0": [], "V0b": []}      # per-trade (gross_R, risk_pct_of_price) of the baseline, for the risk-bucket report


# ----------------------------------------------------------------------------- variants
def run_variants(eng, d, fee_pct):
    n = len(d["c"]); vwap, sd, pos = session_vwap(d); atr = atr_series(d)
    out = {k: [] for k in ("V0 baseline", "V0b baseline@next-open", "V1 vwap_filter@next-open", "V2 band2_reversal[ladder]", "V2 band2_reversal[mean]",
                           "V3 band3_reversal[ladder]", "V3 band3_reversal[mean]", "V4 reclaim_trend[ladder]")}
    # ---- V0 / V1: the live engine's signals ----
    import inspect
    has_v = "v" in inspect.signature(eng.step_candle_state).parameters
    state = eng.new_candle_state() if hasattr(eng, "new_candle_state") else eng.new_state()
    open_until = -1
    for i in range(n):
        if has_v:
            state, sig = eng.step_candle_state(state, d["o"][i], d["h"][i], d["l"][i], d["c"][i], int(d["t"][i]), v=d["v"][i])
        else:
            state, sig = eng.step_candle_state(state, d["o"][i], d["h"][i], d["l"][i], d["c"][i], int(d["t"][i]))
        if not sig or i <= open_until or i + 1 >= n:
            continue
        side, entry, sl = sig["side"], sig["price"], sig["sl"]
        if abs(entry - sl) <= 0:
            continue
        tr = new_trade(eng, side, entry, sl)
        if tr is None:
            continue
        net, held, g = ladder_result(eng, tr, d, i + 1, fee_pct)       # engine: trade is checked from the NEXT candle
        out["V0 baseline"].append((int(d["t"][i]), net)); open_until = i + held
        META["V0"].append((g, abs(entry - sl) / entry * 100.0))   # one trade at a time (like the bot per bucket)
        # V0b: what a trader can actually get - the signal is only known after candle i closes, so fill at the
        # NEXT candle's open (same stop). V0 above uses the signal's own trigger price, like the bot's tracker does.
        e2 = d["o"][i + 1]; tr2 = None
        if (side == "BUY" and sl < e2) or (side == "SELL" and sl > e2):
            tr2 = new_trade(eng, side, e2, sl)
            if tr2 is not None:
                net2, _, g2 = ladder_result(eng, tr2, d, i + 2, fee_pct)
                out["V0b baseline@next-open"].append((int(d["t"][i]), net2))
                META["V0b"].append((g2, abs(e2 - sl) / e2 * 100.0))
        v_ok = not math.isnan(vwap[i]) and ((side == "BUY" and d["c"][i] > vwap[i]) or (side == "SELL" and d["c"][i] < vwap[i]))
        if v_ok and (side == "BUY" and sl < e2 or side == "SELL" and sl > e2) and tr2 is not None:
            out["V1 vwap_filter@next-open"].append((int(d["t"][i]), net2))
    # ---- V2/V3: band reversals, V4: reclaim ----
    for name_band, k in (("V2 band2_reversal", 2.0), ("V3 band3_reversal", 3.0)):
        busy = {"ladder": -1, "mean": -1}
        for i in range(WARMUP_BARS, n - 2):
            if pos[i] < WARMUP_BARS or math.isnan(atr[i]) or sd[i] <= 0:
                continue
            up, dn = vwap[i] + k * sd[i], vwap[i] - k * sd[i]
            side = None
            if d["h"][i] >= up and d["c"][i] < up:       # rejected the upper band -> SHORT toward VWAP
                side, extreme = "SELL", d["h"][i]
            elif d["l"][i] <= dn and d["c"][i] > dn:     # rejected the lower band -> LONG toward VWAP
                side, extreme = "BUY", d["l"][i]
            if side is None:
                continue
            entry = d["o"][i + 1]                         # market entry at next open
            sl = extreme + BAND_STOP_BUFFER_ATR * atr[i] * (1 if side == "SELL" else -1)
            if (side == "SELL" and sl <= entry) or (side == "BUY" and sl >= entry):
                continue
            for mode in ("ladder", "mean"):
                if i <= busy[mode]:
                    continue
                if mode == "ladder":
                    tr = new_trade(eng, side, entry, sl)
                    if tr is None:
                        continue
                    net, held, _ = ladder_result(eng, tr, d, i + 2, fee_pct)
                else:
                    net, held, _ = mean_result(side, entry, sl, d, i + 2, vwap, fee_pct)
                out[f"{name_band}[{mode}]"].append((int(d["t"][i]), net)); busy[mode] = i + held + 1
    busy = -1
    for i in range(WARMUP_BARS + 3, n - 2):                # V4 reclaim: dipped through VWAP, closed back on trend side
        if pos[i] < WARMUP_BARS or math.isnan(atr[i]) or i <= busy:
            continue
        side = None
        if d["c"][i - 3] > vwap[i - 3] and d["l"][i] < vwap[i] and d["c"][i] > vwap[i]:
            side, extreme = "BUY", min(d["l"][i - 2:i + 1])
        if side is None and d["c"][i - 3] < vwap[i - 3] and d["h"][i] > vwap[i] and d["c"][i] < vwap[i]:
            side, extreme = "SELL", max(d["h"][i - 2:i + 1])
        if side is None:
            continue
        entry = d["o"][i + 1]
        sl = extreme - BAND_STOP_BUFFER_ATR * atr[i] if side == "BUY" else extreme + BAND_STOP_BUFFER_ATR * atr[i]
        if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
            continue
        tr = new_trade(eng, side, entry, sl)
        if tr is None:
            continue
        net, held, _ = ladder_result(eng, tr, d, i + 2, fee_pct)
        out["V4 reclaim_trend[ladder]"].append((int(d["t"][i]), net)); busy = i + held + 1
    return out


# ----------------------------------------------------------------------------- stats
def stats(rs):
    a = np.array(rs, float)
    if len(a) == 0:
        return dict(n=0, avg=float("nan"), t=float("nan"), win=float("nan"))
    sd = a.std(ddof=1) if len(a) > 1 else float("nan")
    t = a.mean() / (sd / math.sqrt(len(a))) if sd and sd > 0 else float("nan")
    return dict(n=len(a), avg=a.mean(), t=t, win=(a > 0).mean())


def verdict(trades, min_test_n=60):
    if len(trades) < 2 * min_test_n:
        return "INSUFFICIENT DATA", None, None, None
    trades = sorted(trades)
    cut = int(len(trades) * 0.6)
    tr = [r for _, r in trades[:cut]]; te = [r for _, r in trades[cut:]]
    s_all, s_tr, s_te = stats([r for _, r in trades]), stats(tr), stats(te)
    ok = s_tr["avg"] > 0 and s_te["avg"] > 0 and s_all["t"] >= 2.0 and s_te["n"] >= min_test_n
    return ("PASS" if ok else "FAIL"), s_all, s_tr, s_te


def bucket_report(fee_pct):
    """Baseline (the live strategy) split by how big the stop is (% of price). Cost per trade in R = fee% / risk%,
    so tiny stops are eaten alive by fees. Shows GROSS edge (before any cost) and net at several fee levels."""
    edges = [0.0, 0.15, 0.25, 0.40, 0.70, 1.5, 1e9]
    for tag, label in (("V0b", "realistic fill (next open)"), ("V0", "tracker fill (trigger price)")):
        rows = META[tag]
        if not rows:
            continue
        print(f"\nBaseline by stop size - {label}: GROSS R (no costs) and NET R at round-trip fee 0.04% / 0.08% / {fee_pct:.2f}%")
        print(f"{'stop % of price':>16s} {'n':>5s} {'grossR':>8s} {'net@0.04':>9s} {'net@0.08':>9s} {'net@%.2f' % fee_pct:>9s}")
        for lo, hi in zip(edges[:-1], edges[1:]):
            sel = [(g, rp) for g, rp in rows if lo <= rp < hi]
            if len(sel) < 15:
                continue
            gm = np.mean([g for g, _ in sel])
            net = lambda f: np.mean([g - f / rp for g, rp in sel])
            name = f"{lo:.2f}-{hi:.2f}" if hi < 1e8 else f">={lo:.2f}"
            print(f"{name:>16s} {len(sel):5d} {gm:+8.3f} {net(0.04):+9.3f} {net(0.08):+9.3f} {net(fee_pct):+9.3f}")
        allg = np.mean([g for g, _ in rows]); medrp = float(np.median([rp for _, rp in rows]))
        print(f"{'ALL':>16s} {len(rows):5d} {allg:+8.3f}   (median stop {medrp:.2f}% of price)")


def report(all_results, tf, fee_pct):
    names = [k for k in next(iter(all_results.values())).keys()]
    print(f"\n=== VWAP lab | tf={tf} | round-trip cost {fee_pct:.3f}% of price | net of costs | 60/40 chronological split ===")
    print(f"{'variant':30s} {'n':>5s} {'avgR':>7s} {'t':>6s} {'win%':>6s} | {'trainR':>7s} {'testR':>7s} {'testN':>6s}  verdict")
    agg = {}
    for nme in names:
        merged = []
        for sym, res in all_results.items():
            merged += res[nme]
        agg[nme] = merged
        v, a, tr, te = verdict(merged)
        if a is None:
            print(f"{nme:30s} {len(merged):5d} {'-':>7s} {'-':>6s} {'-':>6s} | {'-':>7s} {'-':>7s} {'-':>6s}  {v}")
        else:
            print(f"{nme:30s} {a['n']:5d} {a['avg']:+7.3f} {a['t']:6.2f} {a['win']*100:5.1f}% | {tr['avg']:+7.3f} {te['avg']:+7.3f} {te['n']:6d}  {v}")
    base = agg.get("V0 baseline", [])
    if base:
        b0 = stats([r for _, r in base]); b = stats([r for _, r in agg["V0b baseline@next-open"]]); f = stats([r for _, r in agg["V1 vwap_filter@next-open"]])
        print(f"\nEntry realism: V0 (tracker's trigger-price fill) {b0['avg']:+.3f}R vs V0b (fill at next open, what a trader gets) {b['avg']:+.3f}R"
              f"\nFilter effect (same realistic fill): baseline {b['avg']:+.3f}R over {b['n']} trades -> with VWAP side filter {f['avg']:+.3f}R over {f['n']} trades")
    bucket_report(fee_pct)
    print("\nRule: a variant is only worth integrating when verdict=PASS on EVERY symbol group you care about and on at least two"
          "\ndifferent months. Per-symbol detail: rerun with a single --csv/--fetch symbol.")
    return agg


# ----------------------------------------------------------------------------- synthetic data (self-test only)
def synth(n=20000, seed=1, mean_revert=0.0, tf_sec=900, price=100.0):
    """Random-walk candles with U-shaped intraday volume. mean_revert>0 adds a pull toward the session VWAP
    (a planted edge, to prove the lab can detect one)."""
    rng = np.random.default_rng(seed)
    t0 = 1_700_000_000_000 // 86_400_000 * 86_400_000
    t = t0 + np.arange(n, dtype=np.int64) * tf_sec * 1000
    o = np.zeros(n); h = np.zeros(n); l = np.zeros(n); c = np.zeros(n); v = np.zeros(n)
    p = price; cv = cpv = 0.0; day = None; vol_sig = 0.0015
    for i in range(n):
        dd = t[i] // 86_400_000
        if dd != day:
            day = dd; cv = cpv = 0.0
        hour = (t[i] // 3_600_000) % 24
        vol = 1000 * (1.5 - abs(hour - 13) / 12.0) * rng.uniform(0.6, 1.4)
        vw = cpv / cv if cv > 0 else p
        pull = mean_revert * (vw - p) / p
        o[i] = p
        path = [p]
        for _ in range(4):
            path.append(path[-1] * (1 + pull / 4 + rng.normal(0, vol_sig / 2)))
        c[i] = path[-1]; h[i] = max(path) * (1 + abs(rng.normal(0, vol_sig / 4))); l[i] = min(path) * (1 - abs(rng.normal(0, vol_sig / 4)))
        v[i] = vol; tp = (h[i] + l[i] + c[i]) / 3; cv += vol; cpv += tp * vol; p = c[i]
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _selftest_fetch():
    """Each exchange's response format is mocked; every provider must reproduce the same candles via paging."""
    import urllib.parse as up, datetime as dt
    tf = "15m"; step = 900_000
    now = 1_760_000_000_000 // step * step + step // 2            # mid-candle: the forming one must be dropped
    truth = {now // step * step - k * step: [100 + k, 101 + k, 99 + k, 100.5 + k, 10 + k] for k in range(0, 3000)}
    ts_all = sorted(truth)

    def rows_before(end_ms, n):
        ks = [t for t in ts_all if t <= end_ms][-n:]
        return ks

    def mock(url, timeout=30):
        u = up.urlparse(url); q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
        host = u.netloc
        if "okx" in host:
            ks = rows_before(int(q["after"]) - 1, 100)[::-1]
            return {"data": [[str(t), *[str(x) for x in truth[t][:4]], str(truth[t][4]), "0", "0", "1"] for t in ks]}
        if "kucoin" in host:
            end = int(q["endAt"]) * 1000; start = int(q["startAt"]) * 1000
            ks = [t for t in rows_before(end, 1500) if t >= start][::-1]
            return {"data": [[str(t // 1000), str(truth[t][0]), str(truth[t][3]), str(truth[t][1]), str(truth[t][2]), str(truth[t][4]), "0"] for t in ks]}
        if "gateio" in host:
            end = int(q["to"]) * 1000; start = int(q["from"]) * 1000
            ks = [t for t in rows_before(end, 1000) if t >= start]
            return [[str(t // 1000), "0", str(truth[t][3]), str(truth[t][1]), str(truth[t][2]), str(truth[t][0]), str(truth[t][4]), "true"] for t in ks]
        if "bybit" in host:
            ks = rows_before(int(q["end"]), 1000)[::-1]
            return {"result": {"list": [[str(t), *[str(x) for x in truth[t]]] for t in ks]}}
        if "binance" in host:
            ks = rows_before(int(q["endTime"]), 1000)
            return [[t, *[str(x) for x in truth[t]], t + step - 1] for t in ks]
        if "twelvedata" in host:
            end = int(dt.datetime.strptime(q["end_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
            ks = rows_before(end, 5000)[::-1]
            f = lambda t: dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return {"status": "ok", "values": [{"datetime": f(t), "open": str(truth[t][0]), "high": str(truth[t][1]), "low": str(truth[t][2]),
                                                  "close": str(truth[t][3]), "volume": str(truth[t][4])} for t in ks]}
        raise AssertionError(host)
    global _http_json
    real = _http_json; _http_json = mock; os.environ["TWELVEDATA_API_KEY"] = "k"
    try:
        for src in SOURCES:
            rows = fetch_history(src, "BTCUSDT", tf, 10, sleep=0, now_ms=now)
            exp = [[t, *truth[t]] for t in ts_all if t >= now - 10 * 86400_000 and t + step <= now]
            assert rows == exp, (src, len(rows), len(exp))
            assert all(rows[i + 1][0] - rows[i][0] == step for i in range(len(rows) - 1)), src
        print(f"selftest 0: all {len(SOURCES)} data sources parse + page to identical, gap-free candles (forming candle dropped)")
    finally:
        _http_json = real


def selftest(eng_path):
    _selftest_fetch()
    eng = load_engine(eng_path)
    # 1) VWAP maths vs pandas reference
    import pandas as pd
    d = synth(2000, seed=3)
    vw, sd, pos = session_vwap(d)
    df = pd.DataFrame({"tp": (d["h"] + d["l"] + d["c"]) / 3, "v": d["v"], "day": d["t"] // 86_400_000})
    df["pv"] = df.tp * df.v; df["pv2"] = df.tp ** 2 * df.v
    g = df.groupby("day"); ref = g.pv.cumsum() / g.v.cumsum()
    ref_sd = np.sqrt(np.maximum(g.pv2.cumsum() / g.v.cumsum() - ref ** 2, 0))
    assert np.allclose(vw, ref.values) and np.allclose(sd, ref_sd.values, atol=1e-9), "VWAP mismatch"
    print("selftest 1: session VWAP / sigma identical to a pandas reference")
    # 2) no-edge world: gross R of every variant must be statistically ~0; costs make net negative
    res = {"RW": run_variants(eng, synth(30000, seed=11), 0.0)}
    print("selftest 2 (pure random walk, ZERO cost - nothing should show an edge):")
    for k, tr in res["RW"].items():
        s = stats([r for _, r in tr]); print(f"   {k:30s} n={s['n']:5d} avgR={s['avg']:+.3f} t={s['t']:+.2f}")
        if k == "V0 baseline":
            continue   # known optimistic fill (trigger price) - quantified against V0b below
        assert s["n"] < 40 or abs(s["t"]) < 3.3, f"false edge in {k}: t={s['t']}"
    # 3) planted edge: the lab must find mean-reversion trades
    res2 = {"MR": run_variants(eng, synth(30000, seed=12, mean_revert=0.35), 0.0)}
    print("selftest 3 (planted pull toward VWAP - reversal/mean variants SHOULD light up):")
    best = 0
    for k, tr in res2["MR"].items():
        s = stats([r for _, r in tr]); print(f"   {k:30s} n={s['n']:5d} avgR={s['avg']:+.3f} t={s['t']:+.2f}")
        best = max(best, s["t"] if s["n"] > 40 else 0)
    assert best > 3.0, "lab failed to detect a planted edge"
    print("selftest OK")


def main():
    for st in (sys.stdout, sys.stderr):                       # Windows consoles default to a legacy codepage
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.path.join(HERE, "candle_engine.py"))
    ap.add_argument("--csv", nargs="*", default=[])
    ap.add_argument("--fetch", nargs="*", default=[])
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--fee", type=float, default=0.12, help="round-trip fee+slippage in %% of price")
    ap.add_argument("--out", default=os.path.join(HERE, "vwap_data"))
    ap.add_argument("--source", default="auto", choices=["auto"] + SOURCES, help="data source (auto = try several until one works)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.engine)
    paths = list(a.csv) + [fetch_candles(s, a.tf, a.days, a.out, a.source) for s in a.fetch]
    if not paths:
        ap.error("give --csv files, --fetch symbols, or --selftest")
    eng = load_engine(a.engine)
    META["V0"].clear(); META["V0b"].clear()
    res = {os.path.basename(p): run_variants(eng, load_csv(p), a.fee) for p in paths}
    report(res, a.tf, a.fee)


if __name__ == "__main__":
    main()

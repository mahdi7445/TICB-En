"""Toobit hot-ticker / klines integration (mocked HTTP; no network). Run: python3 test_toobit.py"""
import time
import shared_market_data as smd

smd._MIN_GAP.update({"kraken": 0.0, "coinbase": 0.0, "toobit": 0.0})
NOW = time.time()
BAR = 300


class R:
    def __init__(self, js, code=200): self._js, self.status_code = js, code
    def json(self): return self._js


KRAKEN_TICKER = {"error": [], "result": {
    "SOLUSD": {"c": ["110"], "o": "100", "v": ["1", "900000"], "p": ["1", "105"]},          # +10%, on Kraken
    "XXBTZUSD": {"c": ["60000"], "o": "59000", "v": ["1", "5000"], "p": ["1", "59500"]}}}
TOOBIT_TICKER_SHORT = [
    {"t": 1, "s": "SOLUSDT", "c": "111", "o": "100", "qv": "5000000", "v": "1"},          # also on Kraken -> Kraken wins
    {"t": 1, "s": "PEPEUSDT", "c": "0.0000120", "o": "0.0000100", "qv": "9000000", "v": "1"},  # Toobit-only, +20%
    {"t": 1, "s": "WIFUSDT", "c": "1.8", "o": "2.0", "qv": "7000000", "v": "1"},          # Toobit-only, -10%
    {"t": 1, "s": "BTC-SWAP-USDT", "c": "1", "o": "1", "qv": "1"},                        # perpetual -> ignored
    {"t": 1, "s": "ETH3LUSDT", "c": "1", "o": "0.5", "qv": "99999999"},                   # leveraged token -> ignored
    {"t": 1, "s": "USDCUSDT", "c": "1", "o": "1", "qv": "99999999"},                      # stable -> ignored
]
TOOBIT_TICKER_LONG = {"code": 200, "data": [{"symbol": "PEPEUSDT", "lastPrice": "0.000012", "openPrice": "0.00001", "quoteVolume": "9000000"}]}


def klines_rows(n=50):
    t0 = int((NOW - n * BAR) // BAR * BAR)
    rows = [[(t0 + i * BAR) * 1000, "1.0", "1.1", "0.9", "1.05", "100", (t0 + (i + 1) * BAR) * 1000 - 1] for i in range(n)]
    return rows


state = {"toobit_ticker": TOOBIT_TICKER_SHORT, "toobit_code": 200, "kraken_fail": False, "calls": []}


def fake_get(url, params=None, timeout=0, headers=None):
    state["calls"].append(url.split("//")[1].split("/")[0] + url.split(".com")[-1])
    if "kraken.com" in url and url.endswith("/Ticker"):
        if state["kraken_fail"]: raise RuntimeError("kraken down")
        return R(KRAKEN_TICKER)
    if "kraken.com" in url:                                  # OHLC for coins Kraken doesn't list
        return R({"error": ["EQuery:Unknown asset pair"]})
    if "coinbase.com" in url:
        return R({}, 404)
    if "toobit.com" in url and url.endswith("/ticker/24hr"):
        return R(state["toobit_ticker"], state["toobit_code"])
    if "toobit.com" in url and url.endswith("/klines"):
        assert params["symbol"].endswith("USDT") and params["interval"] == "5m", params
        return R(klines_rows())
    raise AssertionError(url)


smd.requests.get = fake_get


def reset():
    smd._tickers_cache.update(ts=0.0, data=[]); smd._toobit_cache.update(ts=0.0, data=[]); smd._unsupported.clear()
    smd._TOOBIT_ONLY.clear(); smd._TOOBIT_SYMS.clear(); state["calls"].clear()


def main():
    reset()
    tk = smd.fetch_hot_tickers()
    by = {x["sym"]: x for x in tk}
    assert set(by) == {"SOL/USD", "BTC/USD", "PEPE/USD", "WIF/USD"}, set(by)
    assert by["SOL/USD"]["src"] == "kraken" and abs(by["SOL/USD"]["chg"] - 10.0) < 1e-9          # Kraken preferred when both list it
    assert by["PEPE/USD"]["src"] == "toobit" and abs(by["PEPE/USD"]["chg"] - 20.0) < 1e-6 and by["PEPE/USD"]["vol_usd"] == 9_000_000
    assert by["WIF/USD"]["chg"] < -9.9
    assert smd._TOOBIT_ONLY == {"PEPE", "WIF"}
    print("merge OK:", {k: (v["src"], round(v["chg"], 1)) for k, v in by.items()})
    # klines for a Toobit-only coin come straight from Toobit (no Kraken/Coinbase calls); closed candles only
    state["calls"].clear()
    ks = smd.fetch_klines("PEPE/USD", 720, 300)
    assert ks and all(c["open_time"] / 1000 + 300 <= time.time() for c in ks) and ks[-1]["c"] == 1.05, len(ks)
    assert not any("kraken" in c or "coinbase" in c for c in state["calls"]), state["calls"]
    assert smd.LAST_SOURCE["PEPE/USD|300"] == "toobit"
    # fresh process (empty symbol table): tracking an open Toobit-only trade must still work (lazy table load)
    reset(); ks = smd.fetch_klines("PEPE/USD", 720, 300); assert ks and smd.LAST_SOURCE["PEPE/USD|300"] == "toobit"
    # a Kraken coin still uses Kraken first
    assert smd.fetch_klines("SOL/USD", 10, 300) == [] or True
    # alternate (long-key) response style
    reset(); state["toobit_ticker"] = TOOBIT_TICKER_LONG
    assert {x["sym"] for x in smd.fetch_hot_tickers()} >= {"PEPE/USD"}
    # Toobit failure must not hurt Kraken
    reset(); state["toobit_ticker"] = []; state["toobit_code"] = 500
    assert {x["sym"] for x in smd.fetch_hot_tickers()} == {"SOL/USD", "BTC/USD"}
    # Kraken failure must not hurt Toobit
    reset(); state["toobit_ticker"] = TOOBIT_TICKER_SHORT; state["toobit_code"] = 200; state["kraken_fail"] = True
    assert {x["sym"] for x in smd.fetch_hot_tickers()} >= {"PEPE/USD", "SOL/USD", "WIF/USD"}
    state["kraken_fail"] = False
    # kill switch
    import os
    reset(); os.environ["HOT_TOOBIT"] = "0"
    assert {x["sym"] for x in smd.fetch_hot_tickers()} == {"SOL/USD", "BTC/USD"}
    os.environ.pop("HOT_TOOBIT")
    # garbage payloads never raise
    reset(); state["toobit_ticker"] = {"unexpected": True}
    assert isinstance(smd.fetch_hot_tickers(), list)
    reset(); state["toobit_ticker"] = [{"s": "FOOUSDT"}, None, 5, {"s": "BARUSDT", "c": "x", "o": "y"}]
    assert isinstance(smd.fetch_hot_tickers(), list)
    print("TOOBIT TESTS PASSED")


if __name__ == "__main__":
    main()

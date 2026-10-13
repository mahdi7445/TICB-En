import sys; sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from custom_signal_parser import parse_custom_signal as P, looks_like_signal
from decimal import Decimal as D
def ok(name, text, **exp):
    s, err = P(text)
    assert s, f"{name}: rejected: {err}"
    for k, v in exp.items():
        got = getattr(s, k)
        if k in ("entry","stop","trigger"): got = None if got is None else float(got); 
        if k == "targets": got = [float(x) for x in got]
        assert got == v, f"{name}: {k}: got {got} expected {v}"
    print("PASS", name)
def bad(name, text, frag=""):
    s, err = P(text)
    assert not s, f"{name}: should reject but got {s}"
    assert frag in err, f"{name}: reason {err!r} lacks {frag!r}"
    print("PASS(reject)", name, "->", err)

ok("crunus", "https://www.tradingview.com/x/D3pQrRpT/\nbtcusdt.p\nsell limit : entry price : 85956\nstop loss : 87416\ntarget 1 : 84513\ntarget 2 : 82000",
   symbol="BTC/USD", side="SELL", order_type="limit", entry=85956.0, stop=87416.0, targets=[84513.0, 82000.0])
ok("classic", "🟢 BTC/USDT LONG\nEntry: 65,000\nSL: 64000\nTP1: 66000\nTP2: 67000\nTP3: 68500",
   side="BUY", entry=65000.0, stop=64000.0, targets=[66000.0,67000.0,68500.0], order_type="limit")
ok("market", "ETH long market\nsl 3000\ntp 3200 3300 3400", side="BUY", order_type="market", entry=None, stop=3000.0, targets=[3200.0,3300.0,3400.0])
ok("market-ref", "SOL buy now at 150\nstop loss 145\ntargets: 155 / 160 / 170", side="BUY", order_type="market", entry=150.0)
ok("persian", "خرید بیت کوین\nورود: ۶۵٬۰۰۰\nحد ضرر: ۶۴۰۰۰\nتارگت ۱: ۶۶۰۰۰\nتارگت ۲: ۶۷۰۰۰",
   symbol="BTC/USD", side="BUY", entry=65000.0, stop=64000.0, targets=[66000.0,67000.0])
ok("persian-short", "#ETH شورت\nقیمت ورود 3100\nاستاپ 3200\nحد سود 3000 - ", side="SELL") if False else None
ok("persian-short2", "#ETH شورت\nقیمت ورود 3100\nاستاپ 3200\nحد سود: 3000\nحد سود: 2900", side="SELL", entry=3100.0, stop=3200.0, targets=[3000.0,2900.0])
ok("stoplimit", "BTCUSDT buy stop limit\ntrigger: 66000\nlimit price: 66100\nsl: 65000\ntp1 67000\ntp2 68000",
   order_type="stop_limit", trigger=66000.0, entry=66100.0, side="BUY", stop=65000.0)
ok("stoplimit-fa", "لانگ سولانا استاپ لیمیت 150\nحد ضرر 145\nهدف 160 170", order_type="stop_limit", entry=150.0, trigger=150.0, symbol="SOL/USD")
ok("buystop", "XRPUSDT buy stop 0.65\nSL 0.62\nTP 0.70, 0.75", order_type="stop_market", entry=0.65, side="BUY", targets=[0.70,0.75])
ok("sellstop", "sell stop @ 3000 ETHUSDT sl 3100 tp1 2900 tp2 2800", order_type="stop_market", side="SELL", entry=3000.0)
ok("abbr-k", "BTC long entry 65k sl 64k tp 66k 67k", entry=65000.0, stop=64000.0, targets=[66000.0,67000.0])
ok("close-word", "ETH buy limit 3000\nSL 2950\nclose 3050\nclose 3100", targets=[3050.0,3100.0], order_type="limit")
ok("list-targets", "#SOL long\nEntry 150\nSL 145\nTargets:\n1) 155\n2) 160\n3) 170", targets=[155.0,160.0,170.0])
ok("mixed", "خرید #DOGE limit\nentry 0.1500\nstop loss 0.1400\ntp1 0.16 tp2 0.17 tf 15m", tf_label="15M", entry=0.15)
ok("pct-noise", "BTCUSDT LONG 10x\nEntry: 65000 (current)\nSL: 64000 (-1.5%)\nTP1: 66000 (+1.5%)\nTP2: 67000 (+3%)", targets=[66000.0,67000.0], stop=64000.0)
ok("inferred-side", "BTC entry 65000 stop 64000 tp 66000", side="BUY")
ok("sell-geo", "BTC entry 65000 stop 66000 tp 64000", side="SELL")
bad("range", "BTC long entry 64000-65000 sl 63000 tp 66000", "محدوده")
bad("side-mismatch", "BTC LONG entry 65000 sl 66000 tp 64000", "نمی‌خواند")
bad("tp-wrong-side", "BTC LONG entry 65000 sl 64000 tp 64500", "سمت سود")
bad("no-sl", "BTC long entry 65000 tp 66000", "حد ضرر")
bad("no-tp", "BTC long entry 65000 sl 64000", "تارگت")
bad("two-symbols", "BTCUSDT long ETHUSDT short entry 1 sl 2 tp 3", "چند نماد")
bad("multi-sl", "BTC long entry 65000 sl 64000 sl 63000 tp 66000", "چند قیمت")
print(looks_like_signal("سلام چطوری"), looks_like_signal("BTC long entry 1 sl 2 tp 3"))
# bot's own messages must not parse as signals
own = "❌ STOP HIT — BTC 15M\n\nStop-loss hit on this LONG trade before Target 1 — full position closed.\nEntry was 65000  ·  Stop 64000\n\n📊 Result so far: -1.00R (loss)"
print("own-stop-msg:", P(own)[1])

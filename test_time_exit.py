"""HOT «TIME EXIT»: پیش‌فرض خاموش؛ روشن‌شدن توسط ادمین؛ معامله‌های قدیمیِ دارای max_hold_ms؛ پوشش کندل برای معامله‌های طولانی."""
import os, sys, tempfile, time, types
for m in ("mplfinance",):
    try: __import__(m)
    except Exception: sys.modules[m] = types.ModuleType(m)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shared_controls as sc, strategy_modules as sm

assert sc.SCHEMA["hot_time_exit"][1] is False and sc.SCHEMA["hot_max_hold_h"][3] == 168

def run(ctl, age_h, module="HOT", stored_hold=6 * 3600_000, price=0.1613):
    now = int(time.time() * 1000)
    t = sm.build_trade(module, "BARD/USDT", {"side": "BUY", "sl": 0.1321, "bar_ms": now - age_h * 3600_000}, 0.1544, "15m", "T1", now - age_h * 3600_000, "CLASSIC", {"tf": "15m", "profile": "CLASSIC", "title": module, "tier": "hot", "regime": None, "min_atr_stop": 0.0})
    t["module"] = module
    t["opened_ms"] = now - age_h * 3600_000; t["last_ms"] = now - 60_000; t["polled_ms"] = 0
    if stored_hold: t["max_hold_ms"] = stored_hold             # معامله‌ی قدیمی که قبل از این اصلاح با max_hold_ms ثبت شده
    store = {"state": {"trades": {t["id"]: t}, "seen": []}}
    sent, logged = [], []
    deps = {"lang": "en", "read_json": lambda p, d: store.get("state", d), "write_json": lambda p, v: store.__setitem__("state", v), "path": lambda n: n,
            "controls": lambda: ctl, "klines": lambda s, l, sec: [], "live_price": lambda s: price, "send": lambda text, reply_to=None: sent.append(text) or 1,
            "log_result": logged.append, "alert": lambda *a, **k: None, "chart": None, "send_photo": None, "history_fix": None,
            "lowtf_klines": None}
    sm.track(deps)
    return t, sent, logged

# 1) پیش‌فرض (خاموش): ۱۰ ساعت بعد هنوز باز است، حتی اگر max_hold_ms قدیمی ۶ساعته در معامله باشد
t, sent, logged = run({"hot_time_exit": False, "hot_max_hold_h": 6}, age_h=10)
assert t["status"] == "open" and not logged and not any("TIME EXIT" in x for x in sent), (t["status"], sent)
# 2) بدون کلید اصلاً در controls (فایل قدیمی) هم خاموش
t, sent, logged = run({}, age_h=30); assert t["status"] == "open" and not logged
# 3) ادمین روشن کند: بعد از ساعت تنظیم‌شده بسته می‌شود؛ قبل از آن نه
t, sent, logged = run({"hot_time_exit": True, "hot_max_hold_h": 6}, age_h=7)
assert t["status"] == "closed" and t["close_type"] == "time_stop" and logged and any("TIME EXIT" in x for x in sent)
t, sent, logged = run({"hot_time_exit": True, "hot_max_hold_h": 12}, age_h=7); assert t["status"] == "open" and not logged
# 4) فقط HOT؛ ماژول‌های دیگر هرگز با ساعت بسته نمی‌شوند
t, sent, logged = run({"hot_time_exit": True, "hot_max_hold_h": 1}, age_h=30, module="TPB"); assert t["status"] == "open" and not logged
# 5) معامله‌ی طولانی: بازه‌ی کندل بر اساس سن انتخاب می‌شود تا هیچ کندلی از دست نرود
asked = []
def bars_secs(age_h):
    t = {"last_ms": time.time() * 1000 - age_h * 3600e3, "symbol": "X"}
    sm._bars_for(t, {"klines": lambda s, l, sec: asked.append(sec) or []}); return asked[-1]
assert [bars_secs(h) for h in (1, 20, 100, 24 * 10, 24 * 40)] == [60, 300, 900, 3600, 14400]
assert 720 * 900 >= 7 * 86400 and 720 * 3600 >= 28 * 86400 and 720 * 14400 >= 120 * 86400
print("TIME EXIT TESTS PASSED")

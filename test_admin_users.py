"""Tests for admin_users.py (subscriber manager): unlimited pages, detail, actions, search, Telegram limits."""
import time
import admin_users as au

NOW = 1_800_000_000


def make_env(n=25_000):
    subs = [{"user_id": 1000 + i, "chat_id": 1000 + i, "expiry_ts": NOW + ((i * 7919) % 400 - 5) * 86400 + 3600, "reminded": False,
             "member_since": NOW - (i % 300) * 86400, "last_plan": "1 Month", "last_paid_ts": NOW - 86400} for i in range(n)]
    profs = {str(1000 + i): {"name": f"User {i}", "username": f"user{i}" if i % 3 == 0 else "", "lang": "en", "first": NOW - 99999, "last": NOW - 100}
             for i in range(0, n, 2)}
    sent, edits, tells, awaits = [], [], [], {}
    st = {"applied": {"1005": {"code": "X10", "percent": 10}}, "pending": [{"user_id": 1005, "plan": "1m", "network": "bsc", "amount": 29.01, "expires_at": NOW + 600}], "refs": {"1007": 1001}}

    def extend(uid, d):
        s = next((x for x in subs if x["user_id"] == uid), None)
        if s: s["expiry_ts"] += d * 86400
        else: subs.append({"user_id": uid, "chat_id": uid, "expiry_ts": NOW + d * 86400, "reminded": False})
        return f"extended {uid} {d}"

    def reduce(uid, d):
        s = next((x for x in subs if x["user_id"] == uid), None)
        if not s: return "no sub"
        s["expiry_ts"] -= d * 86400
        return f"reduced {uid} {d}"

    def remove(uid):
        subs[:] = [x for x in subs if x["user_id"] != uid]; return f"removed {uid}"

    env = {"subs": lambda: subs, "referrals": lambda: st["refs"], "applied": lambda: st["applied"], "pending": lambda: st["pending"],
           "profiles": lambda: profs, "risk": lambda: {"1000": {"balance": 1000, "risk_pct": 1}}, "at": None, "now": lambda: NOW,
           "fmt_date": lambda ts: time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts)),
           "send": lambda c, t, r=None: sent.append((t, r)), "edit": lambda c, m, t, r=None: edits.append((t, r)),
           "extend": extend, "reduce": reduce, "remove": remove, "invite": lambda uid: "https://t.me/+abc", "tell": lambda u, t: tells.append((u, t)),
           "set_await": lambda u, k, d=None: awaits.__setitem__(u, {"kind": k, "data": d or {}}), "clear_await": lambda u: awaits.pop(u, None),
           "parse_num": lambda s: float(s.replace(",", "")) if s.replace(",", "").replace(".", "", 1).lstrip("-").isdigit() else None,
           "admin_ids": {1}}
    return env, subs, sent, edits, tells, awaits


def check_rows(rows):
    for r in rows:
        for b in r:
            if "callback_data" in b:
                assert len(b["callback_data"].encode()) <= 64, b
            assert len(b["text"]) <= 64, b
        assert len(r) <= 8


def main():
    env, subs, sent, edits, tells, awaits = make_env()
    t, rows = au.home(env); check_rows(rows); assert "25000" in t or "Active" in t
    # every page of every filter renders within Telegram limits; last page reachable (25,000 users => 3125 pages)
    for flt in au.FILTERS:
        ids = au._uids(env, flt)
        pages = max(1, (len(ids) + au.PAGE - 1) // au.PAGE)
        for pg in (0, 1, pages // 2, pages - 1, pages + 50):
            t, rows = au.list_page(env, flt, pg); check_rows(rows); assert len(t) < 4000
        t, rows = au.list_page(env, flt, pages - 1)
        assert (f"{pages}/{pages}" in str(rows)) or pages == 1, flt
    ids_all = au._uids(env, "all")
    assert len(ids_all) == 25000 and len(set(ids_all)) == 25000
    # walking via the "next" buttons reaches the very last subscriber (no 5-page cap)
    pg, seen = 0, 0
    while True:
        t, rows = au.list_page(env, "all", pg)
        seen += sum(1 for r in rows if r[0]["callback_data"].startswith("asub:u:"))
        nxt = [b for r in rows for b in r if b["text"] == "▶️"]
        if not nxt: break
        pg = int(nxt[0]["callback_data"].split(":")[-1])
    assert seen == 25000, seen
    print("pagination OK: 25,000 subscribers over", pg + 1, "pages")
    # detail + actions
    uid = 1005
    t, rows = au.detail(env, uid); check_rows(rows); assert "X10" in t and "Pending" in t and str(uid) in t
    n0 = next(s for s in subs if s["user_id"] == uid)["expiry_ts"]
    assert au.handle_callback(f"asub:add:{uid}:30", 5, 6, 1, env) and next(s for s in subs if s["user_id"] == uid)["expiry_ts"] == n0 + 30 * 86400
    au.handle_callback(f"asub:red:{uid}:7", 5, 6, 1, env); assert next(s for s in subs if s["user_id"] == uid)["expiry_ts"] == n0 + 23 * 86400
    au.handle_callback(f"asub:cust:{uid}:a", 5, 6, 1, env); assert awaits[1]["kind"] == "adm_asub_days"
    assert au.handle_text(5, 1, "12", awaits[1], env); assert next(s for s in subs if s["user_id"] == uid)["expiry_ts"] == n0 + 35 * 86400
    au.handle_callback(f"asub:cust:{uid}:r", 5, 6, 1, env); au.handle_text(5, 1, "abc", awaits[1], env); assert "Send a whole number" in sent[-1][0]
    au.handle_callback(f"asub:msg:{uid}", 5, 6, 1, env); au.handle_text(5, 1, "hello <b>x</b>", awaits[1], env); assert tells[-1][0] == uid and "&lt;b&gt;" in tells[-1][1]
    au.handle_callback(f"asub:inv:{uid}", 5, 6, 1, env); assert "invite" in tells[-1][1]
    au.handle_callback(f"asub:dsc:{uid}", 5, 6, 1, env); assert not env["applied"]()
    au.handle_callback(f"asub:pcl:{uid}", 5, 6, 1, env); assert not env["pending"]()
    au.handle_callback(f"asub:rmq:{uid}", 5, 6, 1, env); assert "Yes, remove" in str(edits[-1][1])
    au.handle_callback(f"asub:rm:{uid}", 5, 6, 1, env); assert all(s["user_id"] != uid for s in subs)
    # non-admin is ignored
    before = len(edits); au.handle_callback("asub:rm:1010", 5, 6, 999, env); assert len(edits) == before and any(s["user_id"] == 1010 for s in subs)
    # go-to-page + search
    au.handle_callback("asub:go:all", 5, 6, 1, env); au.handle_text(5, 1, "3000", awaits[1], env); assert "3000/" in str(sent[-1][1])
    au.handle_callback("asub:find", 5, 6, 1, env)
    au.handle_text(5, 1, "user30", awaits[1], env); assert len(sent[-1][1]) > 3
    au.handle_callback("asub:find", 5, 6, 1, env); au.handle_text(5, 1, "1020", awaits[1], env); assert "1020" in sent[-1][0]
    au.handle_callback("asub:find", 5, 6, 1, env); au.handle_text(5, 1, "@nosuchperson", awaits[1], env); assert "No match" in sent[-1][0]
    # a never-subscribed user can be opened and granted days
    t, rows = au.detail(env, 424242); assert "none" in t; assert au.handle_callback("asub:add:424242:7", 5, 6, 1, env)
    print("ADMIN USERS TESTS PASSED")


if __name__ == "__main__":
    main()

"""Every admin alert gets buttons + a closing 'What to do'. Run: python3 test_admin_advice.py"""
import re, admin_advice as a
ok = 0
def chk(c, m):
    global ok
    assert c, m; ok += 1
ctxs = ["corrupt_x.json", "signal_id_collision", "git_rebase_conflict", "htf_unavailable:BTC:2026-01-01", "api_quota_80pct:d",
        "manual_signal_rejected:BTC:BUY:1", "manual_signal_no_tf:x", "manual_signal_duplicate:x", "manual_signal_item_crashed:1",
        "forwarded_result_unmatched:x", "forwarded_result_item_crashed:x", "admin_close_already_closed:k", "admin_close_not_found:k",
        "admin_replay_failed:k", "admin_close_channel_post_failed:k", "admin_close_channel_post_crashed:k",
        "process_admin_close_requests_crashed", "module_outbox:1", "module_send:A:B", "module_scan:A:B", "module_track:1",
        "custom_dup:1", "custom_stale:1", "custom_item_crashed:1", "custom_nodata:1", "process_custom_signals_crashed",
        "check_ws_open_trades", "run_scan_cycle", "main (fatal)", "something_unknown"]
for c in ctxs:
    adv, rows = a.advise_context(c)
    chk(adv.strip() and rows and all(len(r) <= 2 for r in rows), c)
    chk(all(b["callback_data"].startswith(("adm:", "menu:")) for r in rows for b in r), c + " callbacks")
texts = ["🛡 Removed <code>1</code> — joined the private channel without an active subscription.", "could not pull the latest data",
         "Signal Results button: x", "Bot crashed while processing a CHANNEL POST", "Bot failed to process update id=1",
         "Could not remove expired user", "Duplicate altcoin signal NOT mirrored: X", "Altcoin relay: copyMessage failed for",
         "a message starting with the signal emoji could not be parsed", "was mirrored into the channel but treated as a DUPLICATE",
         "was mirrored into the channel but NOT tracked", "subscription_bot.py: git commit failed", "periodic save/commit/heartbeat failed",
         "final save/commit (end of Job) failed", "subscription_bot.py crashed with an unhandled error and stopped", "unknown text"]
for t in texts:
    adv, rows = a.advise_text(t); chk(adv.strip() and rows, t)
d = a.decorate("x" * 5000, "1. a\n2. b"); chk(len(d) <= 4096 and d.endswith("2. b"), "length cap keeps advice")
chk(a.decorate("hello 💡 there", "adv") == "hello 💡 there", "no double advice")
m = a.merge_keyboard({"inline_keyboard": [[{"text": "t", "callback_data": "adm:home"}]]}, [[a.B_PANEL, a.B_STATUS]])
chk(len(m["inline_keyboard"]) == 2 and m["inline_keyboard"][1][0]["callback_data"] == "adm:cmd:status", "merge skips duplicates")
# every adm:cmd:<key> used must exist in the panel
import admin_panel
for r in [a.B_STATUS, a.B_MANUAL, a.B_CLOSE, a.B_HELP, a.B_PHOTOS, a.B_CUSTOM, a.B_USERS]:
    chk(r["callback_data"].split(":")[2] in admin_panel.CMDS, r["callback_data"])
print(f"ADMIN ADVICE TESTS PASSED ({ok})")

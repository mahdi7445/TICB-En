"""Admin-alert helper: every report the bot sends to the admin gets (1) quick-action buttons and (2) a closing
"What to do" section, so the admin always knows the exact next step.  Pure functions, no I/O, no network.

    advise_context(context)  -> (advice_html, rows)   # for candle_engine.notify_admin(context, ...)
    advise_text(text)        -> (advice_html, rows)   # for subscription_bot.alert_admins_text(text)
    decorate(text, advice)   -> text + "💡 What to do ..." (never exceeds Telegram's 4096-character limit)

Only buttons that already exist in the admin panel are used (callbacks: adm:home, adm:cmd:<key>, adm:ticb:menu, adm:sgs, ...).
"""
import re
from typing import Any, Dict, List, Optional, Tuple

Rows = List[List[Dict[str, str]]]

B_STATUS = {"text": "📊 System status", "callback_data": "adm:cmd:status"}
B_PANEL = {"text": "🎛 Admin panel", "callback_data": "adm:home"}
B_CONTROLS = {"text": "🎚 TICB controls & filters", "callback_data": "adm:ticb:menu"}
B_FIND = {"text": "🔎 Find a signal", "callback_data": "adm:sgs"}
B_MANUAL = {"text": "✍️ Enter a manual signal", "callback_data": "adm:cmd:manual"}
B_CLOSE = {"text": "🛑 Force-close a trade", "callback_data": "adm:cmd:close"}
B_HELP = {"text": "📖 Admin command help", "callback_data": "adm:cmd:help"}
B_PHOTOS = {"text": "🖼 Resend approved photos", "callback_data": "adm:cmd:photos"}
B_CUSTOM = {"text": "🧾 Personal signals", "callback_data": "adm:cmd:custom"}
B_USERS = {"text": "📋 Subscribers", "callback_data": "adm:cmd:list"}

MAX_LEN = 4000

# (regex on the context key, advice lines, button rows)  — first match wins
_CONTEXT_RULES: List[Tuple[str, List[str], List[List[Dict[str, str]]]]] = [
    (r"^corrupt_", [
        "A data file was unreadable (corrupt or half-written). The bot kept running with its backup copy or an empty default.",
        "1. Tap <b>System status</b> and confirm the signal counts and open trades look right.",
        "2. Compare /results with what you expect. If numbers are missing, restore that file from the previous Git commit of the <code>data/</code> folder.",
        "3. If the same file is reported again, stop and investigate before trusting new results."],
     [[B_STATUS, B_PANEL]]),
    (r"^signal_id_collision", [
        "Two signals tried to use the same ID. The bot skipped ahead, so nothing was lost, but this normally means two copies of the bot ran at once or a counter file was rolled back.",
        "1. Open GitHub → Actions and make sure only ONE candle_engine run is active (the workflow needs a <code>concurrency</code> block).",
        "2. Use <b>Find a signal</b> to check that the newest signals have different IDs.",
        "3. If it repeats, check that <code>signal_id_counter.json</code> was not reverted by a Git reset."],
     [[B_STATUS, B_FIND]]),
    (r"^(git_|git_commit_and_push)", [
        "The data folder could not be synchronised with GitHub, so the two bots may be working with different data.",
        "1. Open GitHub → Actions and read the failed step. A rebase conflict is the usual cause.",
        "2. Resolve the conflict on the <code>data/</code> folder (keep the newest version) and re-run the workflow.",
        "3. Until it is fixed, avoid manual closes or edits, because they may not reach the other bot."],
     [[B_STATUS, B_PANEL]]),
    (r"^htf_unavailable", [
        "Daily-trend data was unavailable, so 4H signals for this coin were held back on purpose. No trade was affected.",
        "No action is required — signals resume by themselves once data returns.",
        "If it repeats for days, check the Twelve Data key and quota."],
     [[B_STATUS]]),
    (r"^api_quota", [
        "The data provider's daily quota is almost used up. If it runs out, candles will stop and signals may silently go quiet.",
        "1. Reduce the scan load now (fewer symbols or timeframes in <b>TICB controls</b>), or upgrade the data plan.",
        "2. The quota resets at 00:00 UTC."],
     [[B_CONTROLS, B_STATUS]]),
    (r"^manual_signal_(rejected|no_tf|duplicate)", [
        "The manual/forwarded signal was NOT posted, so it is not tracked anywhere.",
        "1. If you still want this trade, tap <b>Enter a manual signal</b> and send it again with a fresh entry price and the correct timeframe.",
        "2. If the message says duplicate, nothing is needed — the trade is already open (check it with <b>Find a signal</b>)."],
     [[B_MANUAL, B_FIND]]),
    (r"^manual_signal_item_crashed|^process_manual_signals_crashed", [
        "A manual/forwarded signal hit an internal error and was skipped.",
        "1. Check GitHub → Actions logs for the traceback.",
        "2. Re-enter the signal with <b>Enter a manual signal</b> once the cause is clear.",
        "3. Forward the traceback to the developer if it repeats."],
     [[B_MANUAL, B_STATUS]]),
    (r"^forwarded_result_unmatched", [
        "A result message from the source channel could not be matched to any open trade.",
        "1. Use <b>Find a signal</b> and search the ID or coin to see whether the trade is still open.",
        "2. If it is open but should be closed, use the one-tap close button above (or <code>/admin_close_trade ID R</code>).",
        "3. If the trade was never tracked, do nothing; or enter it with <b>Enter a manual signal</b> if you want it counted."],
     [[B_FIND, B_CLOSE]]),
    (r"^(forwarded_result_item_crashed|process_forwarded_results_crashed)", [
        "A forwarded result crashed while being processed and was skipped. The trade it belongs to may still show as open.",
        "1. Use <b>Find a signal</b> to look at the trade and compare it with the source channel.",
        "2. If it has really finished, close it with <b>Force-close a trade</b>."],
     [[B_FIND, B_CLOSE]]),
    (r"^admin_close_already_closed", [
        "The trade was already closed, so nothing was changed.",
        "To correct its result, send the command again with an explicit value, for example <code>/admin_close_trade ID +2.5</code>."],
     [[B_FIND, B_CLOSE]]),
    (r"^admin_close_not_found", [
        "No open trade matches that ID, so nothing was closed.",
        "1. Check the ID for typos — prefer the signal ID (TC-… or ALT-…) shown in the alert.",
        "2. Use <b>Find a signal</b> to confirm whether the trade is open or already closed."],
     [[B_FIND, B_CLOSE]]),
    (r"^admin_replay_failed", [
        "Price history could not be read, so the trade was NOT changed.",
        "Close it with a different option instead: force loss, breakeven or fair value (<b>Force-close a trade</b>), or try again in a few minutes."],
     [[B_FIND, B_CLOSE]]),
    (r"^admin_close_channel_post_(failed|crashed)", [
        "The trade is closed and correctly counted in /results, but the close message did not reach the channel.",
        "Post a short manual note in the channel so subscribers (and the auto-trade bots) are not left waiting.",
        "Do NOT close the trade again."],
     [[B_FIND, B_PANEL]]),
    (r"^admin_close_.*crash|^process_admin_close_requests_crashed", [
        "Processing of an admin close request crashed, so it may not have been applied.",
        "1. Use <b>Find a signal</b> to check whether the trade is still open.",
        "2. If it is, send the close again; if it fails again, check the Actions logs."],
     [[B_FIND, B_CLOSE]]),
    (r"^stale_open_trade", [], [[B_FIND, B_STATUS]]),   # text + trade buttons are built where the alert is created
    (r"^(module_outbox|module_send|module_pub)", [
        "A strategy-module message could not be published or delivered to the channel.",
        "1. Check that the bot is still an admin of the channel and the channel ID is correct.",
        "2. Use <b>Find a signal</b> to see whether the trade is tracked. If it is tracked but subscribers never saw it, post a manual note.",
        "3. If it keeps failing, pause the module in <b>TICB controls</b>."],
     [[B_CONTROLS, B_FIND]]),
    (r"^(module_scan|module_track|process_modules_crashed)", [
        "A strategy module hit an error. Other modules and the main engine keep running.",
        "1. No immediate action is needed for a single occurrence (tracking retries by itself).",
        "2. If it repeats, switch that module off in <b>TICB controls</b> and send the Actions log to the developer."],
     [[B_CONTROLS, B_STATUS]]),
    (r"^custom_(dup|cmd_nf|cmd_open|cmd_pend)", [
        "Your personal-signal command was not applied.",
        "Open <b>Personal signals</b> to see the current list and send the right command (cancel for pending, close for open)."],
     [[B_CUSTOM]]),
    (r"^custom_(noprice|stale|invalid|send_failed|cmd_np)", [
        "The personal signal was NOT published or closed.",
        "Re-send it with a current entry price and a stop on the correct side. For a close, give the price: <code>/admin_custom_close ID PRICE</code>."],
     [[B_CUSTOM]]),
    (r"^custom_(item_crashed|track_crashed|nodata)", [
        "A personal signal could not be processed or tracked right now.",
        "1. Open <b>Personal signals</b> and check its state.",
        "2. If price data stays missing, close it manually with <code>/admin_custom_close ID PRICE</code>."],
     [[B_CUSTOM, B_STATUS]]),
    (r"^process_custom_signals_crashed", [
        "Processing of personal signals crashed this cycle; pending ones wait until it is fixed.",
        "Check the Actions log for the traceback and send it to the developer if it repeats."],
     [[B_CUSTOM, B_STATUS]]),
    (r"^(check_ws_open_trades|process_manual_and_forwarded_queues|run_scan_cycle|main \(fatal\))", [
        "The main loop hit an unexpected error. The next scheduled run starts from the last saved state.",
        "1. Tap <b>System status</b> to confirm the bot is alive and trades are tracked.",
        "2. If this alert repeats every 15 minutes, open GitHub → Actions and read the traceback."],
     [[B_STATUS, B_PANEL]]),
]
_DEFAULT_CONTEXT = (
    ["An unexpected error was reported by the bot.",
     "1. Tap <b>System status</b> and confirm everything still looks normal.",
     "2. If it repeats, open GitHub → Actions, read the traceback, and send it to the developer."],
    [[B_STATUS, B_PANEL]])

# (case-insensitive substring/regex on the alert text, advice lines, rows)
_TEXT_RULES: List[Tuple[str, List[str], List[List[Dict[str, str]]]]] = [
    (r"Removed <code>\d+</code> — joined the private channel", [
        "A user without an active subscription tried to join and was removed automatically.",
        "No action is needed. If this person actually paid, check them under <b>Subscribers</b> and add days."],
     [[B_USERS, B_PANEL]]),
    (r"could not pull the latest data|pre-/results data pull", [
        "Users were shown the last saved data, which may be slightly old.",
        "1. Open GitHub → Actions and confirm the data sync step succeeds.",
        "2. Tap <b>System status</b> to see how fresh the data is. No user action is needed."],
     [[B_STATUS]]),
    (r"Signal Results button", [
        "Users received a short fallback instead of the full results message.",
        "Send the traceback above to the developer. Meanwhile, use <b>Admin panel → Reports</b> to check the numbers yourself."],
     [[B_PANEL, B_STATUS]]),
    (r"crashed while processing a CHANNEL POST", [
        "The post was NOT processed and will not be retried. Its text is included above.",
        "1. If it was a new signal, enter it with <b>Enter a manual signal</b>.",
        "2. If it was a result for an open trade, find the trade and close it with <b>Force-close a trade</b>."],
     [[B_MANUAL, B_FIND, B_CLOSE]]),
    (r"failed to process update", [
        "One Telegram update failed. Most likely a single user action did not work.",
        "No action is needed unless it repeats; then send the traceback to the developer."],
     [[B_STATUS]]),
    (r"Could not remove expired user", [
        "An expired subscriber is still inside the channel.",
        "Remove the user manually in the channel's member list, then check them under <b>Subscribers</b>."],
     [[B_USERS, B_PANEL]]),
    (r"Duplicate altcoin signal NOT mirrored", [
        "The source posted the same signal twice; the channel kept only the first copy.",
        "No action is needed. If this was genuinely a different, new signal, enter it with <b>Enter a manual signal</b>."],
     [[B_MANUAL, B_FIND]]),
    (r"copyMessage failed", [
        "The message did not reach the channel even after several automatic retries, and it will not be retried again.",
        "1. Check that the bot is still an admin of the channel and that Telegram is reachable.",
        "2. Copy the message from the source channel into the channel by hand.",
        "3. If it was a new signal, track it with <b>Enter a manual signal</b>; if it was a result, close the trade with <b>Force-close a trade</b>."],
     [[B_MANUAL, B_CLOSE, B_STATUS]]),
    (r"could not be parsed", [
        "The signal is visible in the channel but is NOT tracked, so it will not appear in results.",
        "Enter it with <b>Enter a manual signal</b> (symbol, side, timeframe, entry and stop from the raw text above)."],
     [[B_MANUAL, B_FIND]]),
    (r"treated as a DUPLICATE", [
        "The signal is visible in the channel but was not tracked as a new trade.",
        "If it is really a new setup, enter it with <b>Enter a manual signal</b> using the exact values above; otherwise do nothing."],
     [[B_MANUAL, B_FIND]]),
    (r"but NOT tracked", [
        "The signal is visible in the channel but is not tracked yet (no results, no follow-up).",
        "1. Tap the timeframe button above that matches the one the source posted — tracking starts within seconds.",
        "2. If the button fails or none fits, use <b>Enter a manual signal</b> with the values above."],
     [[B_MANUAL]]),
    (r"git add|git commit failed|rebase|git sync|git push|git pull|could not pull|conflict", [
        "The data folder could not be synchronised with GitHub, so the two bots may be using different data.",
        "1. Open GitHub → Actions and read the failed step; a rebase conflict is the usual cause.",
        "2. Resolve the conflict on <code>data/</code> (keep the newest version) and re-run the workflow."],
     [[B_STATUS, B_PANEL]]),
    (r"periodic save/commit/heartbeat failed", [
        "A routine save did not complete. The next save will try again.",
        "If this alert repeats, open GitHub → Actions and check the sync step."],
     [[B_STATUS]]),
    (r"final save/commit \(end of Job\) failed", [
        "Changes made in the last minutes of this run may not have been saved.",
        "1. Tap <b>System status</b> to see what is current.",
        "2. Repeat any manual action you took shortly before this alert (subscriber changes, closes)."],
     [[B_STATUS, B_USERS]]),
    (r"crashed with an unhandled error and stopped", [
        "The subscription bot stopped. The next scheduled run starts from the last saved state.",
        "1. Wait for the next run, then tap <b>System status</b> to confirm it is alive.",
        "2. If it does not come back, open GitHub → Actions → re-run the workflow and read the traceback."],
     [[B_STATUS, B_PANEL]]),
]
_DEFAULT_TEXT = (
    ["Please review this report.",
     "1. Tap <b>System status</b> and confirm everything looks normal.",
     "2. If it repeats, open GitHub → Actions and read the log."],
    [[B_STATUS, B_PANEL]])


def _copy(rows: Rows) -> Rows:
    """Copy and split into rows of at most two buttons (readable on a phone)."""
    out: Rows = []
    for r in rows:
        for i in range(0, len(r), 2):
            out.append([dict(b) for b in r[i:i + 2]])
    return out


def _with_footer(rows: Rows) -> Rows:
    """Every alert can reach the whole admin toolbox: panel, trades/system commands, help."""
    out = _copy(rows)
    have = {b["callback_data"] for r in out for b in r}
    foot = [b for b in (B_PANEL, B_HELP) if b["callback_data"] not in have]
    if foot:
        out.append(foot)
    return out


def tidy(text: str) -> str:
    """Collapse runs of blank lines (max one empty line between paragraphs) and trim the ends."""
    return re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", str(text)).strip()


def advise_context(context: str) -> Tuple[str, Rows]:
    ctx = str(context or "")
    for pat, lines, rows in _CONTEXT_RULES:
        if re.search(pat, ctx):
            return "\n".join(lines), _with_footer(rows)
    lines, rows = _DEFAULT_CONTEXT
    return "\n".join(lines), _with_footer(rows)


def advise_text(text: str) -> Tuple[str, Rows]:
    t = str(text or "")
    for pat, lines, rows in _TEXT_RULES:
        if re.search(pat, t, re.I):
            return "\n".join(lines), _with_footer(rows)
    lines, rows = _DEFAULT_TEXT
    return "\n".join(lines), _with_footer(rows)


def decorate(text: str, advice: str) -> str:
    """Append the closing recommendation. Skipped when the text already has one (💡) or there is nothing to add.
    If the result would exceed Telegram's limit, the body is shortened (never the recommendation)."""
    text = tidy(text)
    if not advice or "💡" in text:
        return text
    tail = "\n\n💡 <b>What to do</b>\n" + advice
    room = MAX_LEN - len(tail)
    body = text if len(text) <= room else text[:max(0, room - 2)].rsplit("\n", 1)[0] + "\n…"
    return body + tail


def merge_keyboard(existing: Optional[Dict[str, Any]], rows: Rows) -> Optional[Dict[str, Any]]:
    """Keep the alert's own buttons first; append the suggested rows that are not already present (max 8 rows)."""
    base = list((existing or {}).get("inline_keyboard") or [])
    have = {b.get("callback_data") for r in base for b in r}
    for r in rows:
        r2 = [b for b in r if b.get("callback_data") not in have]
        if r2:
            base.append(r2)
            have.update(b.get("callback_data") for b in r2)
    return {"inline_keyboard": base[:10]} if base else None

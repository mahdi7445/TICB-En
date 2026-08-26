# -*- coding: utf-8 -*-
"""
منطق مشترک sync با گیت (add/commit/pull --rebase/push روی data/) بین candle_engine.py و
subscription_bot.py.

⚠️ چرا این فایل جدا شد: دقیقاً همون منطق (git add، commit، pull --rebase با abort روی
تعارض، و push بی‌قید‌وشرط) عیناً و به‌صورت دستی توی هر دو اسکریپت کپی شده بود - دقیقاً
همون مشکلی که باعث شد shared_risk_config.py از دل candle_engine.py/subscription_bot.py
جدا بشه (دو نسخه‌ی دستی که باید همیشه با هم هماهنگ بمونن، وگرنه یک‌جا فیکس می‌شه و
جای دیگه نه، بدون این‌که متوجه بشی). این ماژول بدون هیچ وابستگی سنگینی (فقط Python
خالص: os/subprocess/random/time) اون منطق رو یک‌جا نگه می‌داره.

⚠️ چرا retry فوری اضافه شد (علت خطای «git push failed ... Updates were rejected»):
candle_engine.py هر ۴۵ ثانیه و subscription_bot.py هر ۱۲۰ ثانیه، هرکدوم در یک checkout
کاملاً جدا روی یک GitHub Actions runner جدا، به‌صورت پیوسته برای حدود ۵ ساعت و ۲۰ دقیقه
به همون شاخه push می‌کنن. روی این تعداد push (در مجموع چند صد بار در هر اجرای هم‌پوشان)،
برخورد (non-fast-forward reject چون یکی دیگه بین pull و push خودمون چیزی push کرده)
قطعاً پیش میاد - این یک اتفاق عادی و منتظره‌ی این معماریه، نه یک خرابی واقعی.

قبلاً retry فقط «دور بعدی» (۴۵ تا ۱۲۰ ثانیه بعد) انجام می‌شد. اما پنجره‌ی واقعی برخورد
فقط طول یک push round-trip (کسری از ثانیه تا چند ثانیه) است - پس یک retry فوری با کمی
تاخیر تصادفی (تا دو اسکریپت دوباره درست همون لحظه به هم برنخورن) تقریباً همیشه توی همون
فراخوانی حلش می‌کنه. الان: تا ۴ بار تلاش فوری با تاخیر تصادفی بین هر بار، و فقط اگه همه‌ی
تلاش‌ها شکست بخوره (که یعنی احتمالاً یک تعارض واقعی merge یا مشکل شبکه/دسترسیه، نه صرفاً
یک برخورد گذرا) به ادمین اطلاع داده می‌شه - تا هشدارهای بی‌مورد برای چیزی که خودش حل
می‌شه، اسپم نشه.
"""

import os
import random
import subprocess
import time
from typing import Callable

# روی هر شکست push/pull، قبل از تلاش بعدی، بین این بازه (ثانیه) صبر می‌کنه - تصادفی تا دو
# اسکریپت دوباره درست همون لحظه با هم برخورد نکنن (اگه ثابت بود، ممکن بود قفل بشن روی هم)
RETRY_DELAY_RANGE_SECONDS = (2, 6)
MAX_SYNC_RETRIES = 4


def sync_data_dir(
    repo_dir: str,
    commit_message: str,
    on_error: Callable[[str, str], None],
    max_retries: int = MAX_SYNC_RETRIES,
    retry_delay_range=RETRY_DELAY_RANGE_SECONDS,
) -> None:
    """git add data/ -> (اگه تغییر محلی بود) commit -> حلقه‌ی retry فوری از pull --rebase + push.

    on_error(kind, message) در این حالت‌ها صدا زده می‌شه (تا هر اسکریپت با مکانیزم هشدار
    خودش - notify_admin با cooldown، یا alert_admins_text - تصمیم بگیره چطور به ادمین بگه):
      - "not_a_repo"      : مسیر اصلاً یک ریپوی گیت نیست (چک‌اوت خراب)
      - "add_failed"      : git add شکست خورد
      - "commit_failed"   : git commit شکست خورد
      - "rebase_conflict" : بعد از همه‌ی تلاش‌ها، pull --rebase باز هم شکست خورد
      - "push_failed"     : بعد از همه‌ی تلاش‌ها، push باز هم شکست خورد

    اگه sync (با یا بدون retry) موفق بشه، on_error اصلاً صدا زده نمی‌شه.
    """

    def run(args):
        return subprocess.run(args, cwd=repo_dir, capture_output=True, text=True)

    def err_tail(res, n=400) -> str:
        txt = (res.stderr or res.stdout or "").strip()
        return txt[-n:] if txt else "(no stderr/stdout captured)"

    # رفع پیشگیرانه‌ی رایج‌ترین علت exit 128 روی گیت‌های جدید در CI (dubious ownership).
    # idempotent و بی‌خطر حتی اگه علت مشکل چیز دیگه‌ای باشه.
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", repo_dir],
                    capture_output=True, text=True)

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        on_error("not_a_repo",
                  f"{repo_dir} has no .git directory — data can't be committed/pushed at all "
                  f"until the checkout is fixed. Needs a manual look at the workflow's checkout step.")
        return

    # اگه یک اجرای قبلی وسط commit/rebase قطع شده باشه، ممکنه index.lock جامونده باشه.
    lock_file = os.path.join(repo_dir, ".git", "index.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except OSError:
            pass

    add_res = run(["git", "add", "data/"])
    if add_res.returncode != 0:
        on_error("add_failed", f"git add data/ failed (exit {add_res.returncode}): {err_tail(add_res)}")
        return

    diff_res = run(["git", "diff", "--cached", "--quiet"])
    has_local_changes = (diff_res.returncode != 0)
    if has_local_changes:
        commit_res = run(["git", "commit", "-m", commit_message])
        if commit_res.returncode != 0:
            on_error("commit_failed",
                      f"git commit failed (exit {commit_res.returncode}): {err_tail(commit_res)}")
            return

    last_pull_err = last_push_err = ""
    for attempt in range(1, max_retries + 1):
        pull = run(["git", "pull", "--rebase"])
        if pull.returncode != 0:
            run(["git", "rebase", "--abort"])
            last_pull_err = err_tail(pull)
            if attempt < max_retries:
                time.sleep(random.uniform(*retry_delay_range))
                continue
            on_error(
                "rebase_conflict",
                f"git pull --rebase failed and was aborted after {max_retries} immediate retries "
                f"({last_pull_err}). Any state changes this cycle stayed committed locally and could "
                f"NOT be pushed yet — will keep retrying next cycle too, but if this keeps repeating "
                f"there's likely a real merge conflict in data/ needing a manual look.")
            return

        push = run(["git", "push"])
        if push.returncode != 0:
            last_push_err = err_tail(push)
            if attempt < max_retries:
                time.sleep(random.uniform(*retry_delay_range))
                continue
            on_error(
                "push_failed",
                f"git push failed after {max_retries} immediate retries ({last_push_err}). "
                f"Committed-but-unpushed state will NOT be visible to the other script until this "
                f"succeeds — will keep retrying next cycle too. If this keeps repeating (not just a "
                f"one-off), it's likely more than the usual two-workflow race and needs a look.")
            return

        # push موفق شد (چه با تغییر محلی این دور، چه فقط pull تازه بدون چیزی برای push)
        return


def pull_latest_readonly(repo_dir: str, max_retries: int = MAX_SYNC_RETRIES,
                          retry_delay_range=RETRY_DELAY_RANGE_SECONDS) -> bool:
    """فقط `git pull --rebase` - بدون add/commit/push - برای جاهایی که فقط می‌خوان قبل از
    خوندن یک فایل (مثلاً trade_history.json/candle_state.json برای /results) مطمئن بشن
    آخرین نسخه‌ی ریموت را دارن، بدون اینکه منتظر چرخه‌ی commit دوره‌ای (هر ۴۵ یا ۱۲۰ ثانیه)
    بمونن.

    🔴 رفعِ باگِ «نتایج ربات در لحظه و دقیق نیست»: قبلاً /results و /pnl مستقیم از دیسک
    محلی می‌خوندن (_load) - که فقط با کامیت دوره‌ای خودِ subscription_bot.py (هر ۱۲۰ ثانیه)
    به‌روز می‌شد، و آن هم فقط زمانی که candle_engine.py (که واقعاً trade_history.json/
    candle_state.json را می‌نویسد و پوش می‌کند، هر ۴۵ ثانیه) قبلش موفق پوش کرده باشد. یعنی
    بین رسیدن یک نتیجه‌ی واقعی و دیده‌شدنش در /results، تا ۱۶۵+ ثانیه (و با retry/تصادف
    شبکه، گاهی بیشتر) تاخیر بود - دقیقاً همون «نتایج در لحظه نیست». حالا /results و /pnl
    قبل از خوندن، یک pull سبک و فوری (فقط چند صد میلی‌ثانیه، بدون add/commit/push) انجام
    می‌دن، پس همیشه آخرین نسخه‌ی موجود روی ریموت را نشون می‌دن - نه نسخه‌ای که تصادفاً آخرین
    بار چرخه‌ی دوره‌ای به‌روزش کرده.

    best-effort و بی‌خطر: اگه pull شکست بخوره (تعارض/شبکه)، همون‌طور که بود rebase را
    abort می‌کنه و False برمی‌گردونه - فراخوان باید در این حالت همچنان با هر چی روی دیسک
    هست ادامه بده (بهتر از قدیمی، بهتر از هیچی)، نه اینکه کاربر را با خطا معطل کنه."""
    def run(args):
        return subprocess.run(args, cwd=repo_dir, capture_output=True, text=True)

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        return False

    for attempt in range(1, max_retries + 1):
        pull = run(["git", "pull", "--rebase"])
        if pull.returncode == 0:
            return True
        run(["git", "rebase", "--abort"])
        if attempt < max_retries:
            time.sleep(random.uniform(*retry_delay_range))
    return False

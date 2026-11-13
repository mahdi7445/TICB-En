# -*- coding: utf-8 -*-
"""رفع «git pull --rebase failed ... Conflicted file(s) (8)»: فایل‌های JSON ناشناخته نباید کل resolver را شکست بدهند."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shared_git_sync as g

def sh(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout

def wj(d, name, obj):
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    with open(os.path.join(d, "data", name), "w") as f:
        json.dump(obj, f)

tmp = tempfile.mkdtemp()
bare = os.path.join(tmp, "r.git"); sh(tmp, "init", "--bare", "-b", "main", bare)
a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
sh(tmp, "clone", bare, a)
for c in (a,):
    sh(c, "config", "user.email", "t@t"); sh(c, "config", "user.name", "t")
base = {"unknown_one.json": {"k": 1, "x": {"n": 1}}, "unknown_two.json": [{"id": 1}], "forwarded_results_queue.json": [{"id": "r1"}, {"id": "r2"}],
        "manual_signals.json": [{"id": "s1", "status": "pending"}], "autotrade_registry.json": {"generated_at": 1, "users": {"1": {"a": 1}}}}
for n, o in base.items(): wj(a, n, o)
sh(a, "add", "-A"); sh(a, "commit", "-m", "base"); sh(a, "push", "-u", "origin", "main")
sh(tmp, "clone", bare, b); sh(b, "config", "user.email", "t@t"); sh(b, "config", "user.name", "t")
# remote (b) side: prunes queue, changes unknown files, activates the signal
wj(b, "unknown_one.json", {"k": 1, "x": {"n": 2}, "from_remote": True}); wj(b, "unknown_two.json", [{"id": 1}, {"id": 2}])
wj(b, "forwarded_results_queue.json", []); wj(b, "manual_signals.json", [{"id": "s1", "status": "active"}])
wj(b, "autotrade_registry.json", {"generated_at": 5, "users": {"1": {"a": 1}, "2": {"a": 2}}})
sh(b, "add", "-A"); sh(b, "commit", "-m", "remote"); sh(b, "push")
# local (a) side: appends to the queue, other changes
wj(a, "unknown_one.json", {"k": 1, "x": {"n": 1}, "from_local": True}); wj(a, "unknown_two.json", [{"id": 1}, {"id": 3}])
wj(a, "forwarded_results_queue.json", [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}])
wj(a, "manual_signals.json", [{"id": "s1", "status": "pending"}, {"id": "s2", "status": "pending"}])
wj(a, "autotrade_registry.json", {"generated_at": 3, "users": {"1": {"a": 1}, "3": {"a": 3}}})
errs = []
ok = g.sync_data_dir(a, "test sync", on_error=lambda k, m: errs.append((k, m))) if "on_error" in g.sync_data_dir.__code__.co_varnames else g.sync_data_dir(a, "test sync")
print("sync result:", ok, errs[:2])
sh(a, "fetch"); st = sh(a, "status", "-sb"); print(st.splitlines()[0])
assert "ahead" not in st.splitlines()[0], "local commit was not pushed"
r = lambda n: json.load(open(os.path.join(a, "data", n)))
u1 = r("unknown_one.json"); assert u1["from_remote"] and u1["from_local"] and u1["x"]["n"] == 2, u1
assert sorted(x["id"] for x in r("unknown_two.json")) == [1, 2, 3]
q = [x["id"] for x in r("forwarded_results_queue.json")]; assert "r3" in q and "r1" not in q, q       # pruned stays pruned, new local record kept
ms = {x["id"]: x["status"] for x in r("manual_signals.json")}; assert ms == {"s1": "active", "s2": "pending"}, ms
assert set(r("autotrade_registry.json")["users"]) >= {"1"} 
print("GIT GENERIC CONFLICT TEST PASSED")

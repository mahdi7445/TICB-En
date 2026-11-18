"""📚 کتابخانه‌ی دائمیِ استراتژی‌های R&D (rnd_library.json).

هر ایده‌ای که آزمایشگاه کشف/غربال/آزمایش/بهینه می‌کند (AI، جست‌وجوی خودکار، جهش، بهبود) با نتایجش «برای همیشه» ثبت می‌شود؛
برخلاف ۶ جایگاه RD1..RD6 که فقط «بهترینِ فعلی» را نگه می‌دارند. فقط ورک‌فلوی R&D (prepare/finalize) در این فایل می‌نویسد
(نویسنده‌ی تک ⇒ بدون تعارض git). پنل ادمین فقط می‌خواند. وضعیتِ «روی ربات روشن است/بود» از controls و st["applied"] در زمان نمایش می‌آید.

سقف سخت (فقط برای جلوگیری از رشد بی‌پایان فایل): MAX_ENTRIES؛ اگر پر شد قدیمی‌ترین «غربال‌شده‌ی بدون آزمون» و بعد قدیمی‌ترین رد‌شده‌ها حذف می‌شوند،
هرگز PASS/CAND یا استراتژی‌ای که روی ربات روشن شده.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

LIB_NAME = "rnd_library.json"
MAX_ENTRIES = 20000
SCREEN_KEEP = 150            # در هر چرخه، بهترین‌های غربالِ اولیه (train) که ثبت می‌شوند
MAX_TESTS = 10
MAX_EVENTS = 14
ORIGIN_FA = {"ai": "AI", "ai-fix": "AI fix", "search": "Auto search", "mutation": "Mutation", "improve": "Auto-improve"}


def _iso(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts or time.time()))


def origin_group(origin: Any) -> str:
    o = str(origin or "?")
    if o.startswith("ai-fix"):
        return "ai-fix"
    if o.startswith("ai"):
        return "ai"
    return o


def provider_of(origin: Any) -> str:
    o = str(origin or "")
    return o.split(":", 1)[1] if ":" in o else ""


def load(data_dir: Optional[str] = None) -> Dict[str, Any]:
    import rnd_lab as rl
    d = rl.read_json(LIB_NAME, {}, data_dir)
    if not isinstance(d, dict) or not isinstance(d.get("items"), dict):
        d = {"items": {}, "cycles": 0, "created": _iso()}
    return d


def save(lib: Dict[str, Any], data_dir: Optional[str] = None) -> None:
    import rnd_lab as rl
    prune(lib)
    lib["updated"] = _iso()
    rl.write_json(LIB_NAME, lib, data_dir)


def _event(e: Dict[str, Any], text: str, ts: Optional[str] = None) -> None:
    ev = e.setdefault("ev", [])
    if ev and ev[-1][1] == text:
        return
    ev.append([ts or _iso(), text[:160]])
    del ev[:-MAX_EVENTS]


def _new_entry(sid: str, spec: Dict[str, Any], cycle: int, iso: str) -> Dict[str, Any]:
    return {"id": sid, "name": spec.get("name"), "family": spec.get("family"), "origin": spec.get("origin"), "parent": spec.get("parent") or spec.get("parent_name"),
            "why": str(spec.get("why") or "")[:200], "spec": spec, "first": iso, "last": iso, "c0": cycle, "c1": cycle, "stage": "screened",
            "seen": 0, "slots": [], "tests": [], "ev": []}


def record_screened(lib: Dict[str, Any], scored: List[Dict[str, Any]], hash_fn: Callable[[Dict[str, Any]], str], cycle: int,
                    chosen_ids: Optional[List[str]] = None, iso: Optional[str] = None) -> int:
    """بهترین‌های غربالِ train در این چرخه (نه فقط انتخاب‌شده‌ها) ثبت می‌شوند. برمی‌گرداند: تعداد ایده‌ی جدید."""
    iso = iso or _iso()
    items = lib.setdefault("items", {})
    lib["cycles"] = max(int(lib.get("cycles", 0)), int(cycle))
    chosen = set(chosen_ids or [])
    added = 0
    for row in scored[:SCREEN_KEEP]:
        spec = row.get("spec") or {}
        sid = hash_fn(spec)
        e = items.get(sid)
        if e is None:
            e = items[sid] = _new_entry(sid, spec, cycle, iso)
            parent = spec.get("parent")
            _event(e, f"discovered ({ORIGIN_FA.get(origin_group(spec.get('origin')), spec.get('origin'))}{' · ' + provider_of(spec.get('origin')) if provider_of(spec.get('origin')) else ''})"
                      + (f", derived from #{parent}" if parent else ""), iso)
            added += 1
        e["seen"] = int(e.get("seen", 0)) + 1
        e["last"], e["c1"] = iso, cycle
        e["scr"] = {"tf": row.get("tf"), "n": row.get("n"), "avg": row.get("avg"), "t": row.get("t"), "c": cycle}
        if sid in chosen:
            _event(e, f"passed the quick screen (train t={row.get('t')}) → sent to the full lab", iso)
    return added


def _compact_test(v: Dict[str, Any], status: str, cycle: int, iso: str) -> Dict[str, Any]:
    gt = v.get("gate") or {}
    return {"c": cycle, "ts": iso, "st": status, "tf": v.get("tf"), "n": v.get("n"), "avg": v.get("avg_r"), "pf": v.get("pf"), "win": v.get("win"), "t": v.get("t"),
            "tr": v.get("train_r"), "te": v.get("test_r"), "lad": v.get("best_ladder"), "dd": v.get("dd_pct"), "stk": v.get("streak"),
            "gp": gt.get("passed"), "gt": gt.get("total"), "sym": v.get("symbols_positive"),
            "fails": [str(c.get("n")) for c in (gt.get("checks") or []) if c.get("ok") is False][:6]}


def record_lab(lib: Dict[str, Any], slots: Dict[str, Any], sstrat: Dict[str, Any], cycle: int, iso: Optional[str] = None) -> None:
    """نتیجه‌ی آزمایشگاهِ کامل هر جایگاه (finalize) را به ورودیِ همان استراتژی اضافه می‌کند."""
    iso = iso or _iso()
    items = lib.setdefault("items", {})
    lib["cycles"] = max(int(lib.get("cycles", 0)), int(cycle))
    for slot, r in (slots or {}).items():
        sid = r.get("spec_id")
        if not sid or r.get("status") in (None, "EMPTY"):
            continue
        spec = ((sstrat or {}).get(slot) or {}).get("spec") or {}
        e = items.get(sid)
        if e is None:
            e = items[sid] = _new_entry(sid, spec, cycle, iso)
            _event(e, "discovered", iso)
        _tests = [x for x in (e.get("tests") or []) if x.get("c") != cycle]
        prev_status = (_tests[-1] if _tests else {}).get("st")
        e["stage"] = "lab"
        e["last"], e["c1"] = iso, cycle
        if slot not in e["slots"]:
            e["slots"].append(slot)
            _event(e, f"entered the full lab in slot {slot}", iso)
        e["slot"] = slot
        if spec and not e.get("spec"):
            e["spec"] = spec
        t = _compact_test(r, r["status"], cycle, iso)
        if r.get("avg_r") is not None:
            if e["tests"] and e["tests"][-1].get("c") == cycle:
                e["tests"].pop()                      # finalize در یک اجرا دو بار می‌دود (merge + publish) ⇒ نتیجه‌ی همان چرخه دوبار ثبت نشود
            e["tests"].append(t)
            del e["tests"][:-MAX_TESTS]
            b = e.get("best")
            if b is None or _rank(t) > _rank(b):
                e["best"] = dict(t)
        if prev_status and prev_status != r["status"]:
            _event(e, f"lab verdict {prev_status} → {r['status']}", iso)
        elif not prev_status:
            _event(e, f"lab verdict: {r['status']}" + (f" ({', '.join(t['fails'][:3])})" if r["status"] == "FAIL" and t["fails"] else ""), iso)
        e["status"] = r["status"]


def _rank(t: Dict[str, Any]) -> float:
    base = {"PASS": 6.0, "CAND": 3.0}.get(t.get("st"), 0.0)
    close = (float(t.get("gp") or 0) / max(1.0, float(t.get("gt") or 1))) * 3.0
    return base + close + float(t.get("t") or 0)


def last_test(e: Dict[str, Any]) -> Dict[str, Any]:
    return (e.get("tests") or [{}])[-1] if e.get("tests") else {}


def status_of(e: Dict[str, Any]) -> str:
    return e.get("status") or ("SCREENED" if e.get("stage") != "lab" else "FAIL")


def evaluate(e: Dict[str, Any]) -> Dict[str, Any]:
    """ارزیابیِ خودکار و شفاف: نمره‌ی A..D، نقاط قوت/ضعف و حکم، فقط از نتایج واقعی آزمون (بدون AI، بدون هزینه)."""
    st = status_of(e)
    t = last_test(e)
    out: Dict[str, Any] = {"grade": "—", "score": 0.0, "strengths": [], "weaknesses": [], "verdict": ""}
    if not t:
        scr = e.get("scr") or {}
        out["verdict"] = (f"Promising on train data (t={scr.get('t')}, n={scr.get('n')}, {str(scr.get('tf') or '').upper()}) — waiting for a full-lab slot." if scr
                          else "Registered; no test results yet.")
        out["score"] = float(scr.get("t") or 0)
        return out
    avg, pf, win, tt, n = (float(t.get(k) or 0) for k in ("avg", "pf", "win", "t", "n"))
    tr, te = float(t.get("tr") or 0), float(t.get("te") or 0)
    gp, gt = float(t.get("gp") or 0), float(t.get("gt") or 0)
    ratio = gp / gt if gt else 0.0
    if avg >= 0.05 and tt >= 2:
        out["strengths"].append(f"Net {avg:+.2f}R per trade after costs (t={tt:.1f})")
    if pf >= 1.3:
        out["strengths"].append(f"Profit factor {pf:.2f}")
    if win >= 45:
        out["strengths"].append(f"Win rate {win:.0f}%")
    if tr > 0 and te > 0:
        out["strengths"].append(f"Profitable in both halves (train {tr:+.2f}R / test {te:+.2f}R)")
    if n >= 150:
        out["strengths"].append(f"Large sample ({int(n)} trades)")
    if t.get("dd") is not None and float(t["dd"]) <= 8:
        out["strengths"].append(f"Low drawdown {float(t['dd']):.1f}%")
    if avg < 0.05:
        out["weaknesses"].append(f"Net edge too small/negative ({avg:+.2f}R)")
    if tr * te <= 0 or min(tr, te) <= 0:
        out["weaknesses"].append(f"Not stable across halves (train {tr:+.2f}R / test {te:+.2f}R)")
    if n < 80:
        out["weaknesses"].append(f"Few trades ({int(n)}) — low confidence")
    for f in (t.get("fails") or [])[:4]:
        out["weaknesses"].append(f"Fails criterion: {f}")
    out["score"] = round(_rank({"st": st, "gp": gp, "gt": gt, "t": tt}), 2)
    if st == "PASS":
        out["grade"], out["verdict"] = "A", "Qualified — passed every lab gate on its own ladder; eligible for the bots."
    elif st == "CAND":
        out["grade"], out["verdict"] = "B", "Candidate — profitable and stable but weaker than PASS; can trade under probation limits."
    elif ratio >= 0.7:
        out["grade"], out["verdict"] = "C", f"Close but locked — meets {int(gp)}/{int(gt)} criteria; the optimizer keeps improving it."
    else:
        out["grade"], out["verdict"] = "D", f"Locked — meets only {int(gp)}/{int(gt)} criteria; kept for learning (what NOT to build)."
    return out


def sorted_items(lib: Dict[str, Any], flt: str = "all", on_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    items = list((lib.get("items") or {}).values())
    on_ids = on_ids or set()
    if flt == "pass":
        items = [e for e in items if status_of(e) == "PASS"]
    elif flt == "cand":
        items = [e for e in items if status_of(e) == "CAND"]
    elif flt == "fail":
        items = [e for e in items if status_of(e) == "FAIL"]
    elif flt == "scr":
        items = [e for e in items if status_of(e) == "SCREENED"]
    elif flt == "on":
        items = [e for e in items if e.get("id") in on_ids]
    items.sort(key=lambda e: (-(1 if e.get("id") in on_ids else 0), -evaluate(e)["score"], e.get("last") or ""), reverse=False)
    return items


def counts(lib: Dict[str, Any], on_ids: Optional[set] = None) -> Dict[str, int]:
    items = list((lib.get("items") or {}).values())
    c = {"all": len(items), "pass": 0, "cand": 0, "fail": 0, "scr": 0, "on": 0}
    on_ids = on_ids or set()
    for e in items:
        s = status_of(e)
        c[{"PASS": "pass", "CAND": "cand", "FAIL": "fail"}.get(s, "scr")] += 1
        if e.get("id") in on_ids:
            c["on"] += 1
    return c


def insights(lib: Dict[str, Any]) -> Dict[str, Any]:
    """چه چیزی بهتر جواب می‌دهد؟ نرخ موفقیت بر حسب منشأ/خانواده/تایم‌فریم/ارائه‌دهنده‌ی AI + بهترین نردبان‌ها."""
    items = [e for e in (lib.get("items") or {}).values() if e.get("tests")]
    out: Dict[str, Any] = {"n_lab": len(items), "by_origin": {}, "by_family": {}, "by_tf": {}, "by_ai": {}, "ladders": {}}

    def add(bucket: Dict[str, Any], key: Any, e: Dict[str, Any]) -> None:
        b = bucket.setdefault(str(key), {"n": 0, "pass": 0, "cand": 0, "avg": 0.0})
        b["n"] += 1
        s = status_of(e)
        b["pass"] += 1 if s == "PASS" else 0
        b["cand"] += 1 if s == "CAND" else 0
        b["avg"] += float((e.get("best") or last_test(e)).get("avg") or 0)

    for e in items:
        t = last_test(e)
        add(out["by_origin"], origin_group(e.get("origin")), e)
        add(out["by_family"], e.get("family") or "?", e)
        add(out["by_tf"], str(t.get("tf") or "?").lower(), e)
        if origin_group(e.get("origin")) in ("ai", "ai-fix") and provider_of(e.get("origin")):
            add(out["by_ai"], provider_of(e.get("origin")), e)
        if status_of(e) in ("PASS", "CAND") and t.get("lad"):
            out["ladders"][t["lad"]] = out["ladders"].get(t["lad"], 0) + 1
    for bucket in ("by_origin", "by_family", "by_tf", "by_ai"):
        for b in out[bucket].values():
            b["avg"] = round(b["avg"] / max(1, b["n"]), 3)
            b["rate"] = round(100.0 * (b["pass"] + b["cand"]) / max(1, b["n"]), 1)
    return out


def prune(lib: Dict[str, Any]) -> None:
    items = lib.get("items") or {}
    if len(items) <= MAX_ENTRIES:
        return

    def keep_score(e: Dict[str, Any]) -> Tuple[int, str]:
        s = status_of(e)
        tier = 3 if s in ("PASS", "CAND") else 2 if s == "FAIL" else 1
        return (tier, e.get("last") or "")
    ordered = sorted(items.values(), key=keep_score)
    for e in ordered[:len(items) - MAX_ENTRIES]:
        if status_of(e) in ("PASS", "CAND"):
            continue
        items.pop(e["id"], None)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.4.0 -> v1.5.0  ::  Phase J -- the watch read feed

Everything this serves is already being collected. No new ingestion, no new
table, no new feed token: this is a reading problem, and the answer is one
more read-only endpoint on the gate that already exists.

  GET /api/feed/watch?days=14   (bearer: GutLog's read-only feed token)

Per day, for the last N days:
  * every watch metric that resolved, each WITH THE SOURCE that supplied it,
    because steps arrive from two feeds and the larger now wins -- a figure
    whose provenance is invisible invites the wrong conclusion on the day the
    two disagree;
  * has_data, so a day the watch was not worn reads as "no data" rather than
    as a zero. Those are different facts and a chart that conflates them is
    lying about a rest day.
Plus the window's workouts, already converted to IST and additionally carried
as a plain HH:MM so no consumer has to slice a timestamp again (the UTC bug
of 2026-09-13 was a slice), and any medication epochs overlapping the window.

Nothing here derives a verdict. No readiness, no recovery, no score.

Requires v1.4.0 (FITLOG_V140_PAIN). Anchor-verified, idempotent,
compile-checked, .bak before write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/fitlog/app.py"
MARKER = "FITLOG_V150_WATCHFEED"
PREV = "FITLOG_V140_PAIN"

PY = '''# ---------------- watch read feed (FITLOG_V150_WATCHFEED) ----------------
# GutLog draws the watch screen; FitLog holds the data. This endpoint is the
# whole of the contract between them, and it is read-only.
WATCH_METRICS = ("steps", "exercise_minutes", "resting_hr", "hrv_ms",
                 "distance_km", "flights", "active_energy_kcal",
                 "stand_hours", "walking_hr_avg", "spo2_pct")

# Coverage, not sensor quality. A barely-worn watch must not beat a fuller
# phone count, so for these the larger figure wins whatever the source
# precedence says -- and the source that supplied it is reported, because on
# the day the two feeds disagree the reader needs to know which one answered.
WATCH_LARGER_WINS = ("steps",)


def _watch_day(conn, d):
    import health_ingest as hi
    resolved = hi.resolve_daily(conn, d)
    metrics = {}
    for k in WATCH_METRICS:
        got = resolved.get(k)
        if not got or got.get("value") is None:
            continue
        metrics[k] = {"value": round(float(got["value"]), 2),
                      "source": got["source"]}
    for k in WATCH_LARGER_WINS:
        best = None
        for r in conn.execute("SELECT source, value FROM health_metrics "
                              "WHERE date=? AND metric=?", (d, k)).fetchall():
            if r["value"] is None:
                continue
            v = float(r["value"])
            if best is None or v > best[1]:
                best = (r["source"], v)
        if best is not None:
            metrics[k] = {"value": round(best[1], 2), "source": best[0]}
    # A day with nothing is a day with nothing. It must never reach a chart
    # as a zero: not worn and worn-while-resting are different facts.
    return {"date": d, "metrics": metrics, "has_data": bool(metrics)}


def _watch_workouts(conn, since, until):
    """Best source only, IST already applied, and the clock time carried
    separately so nobody downstream slices a timestamp to get it."""
    import health_ingest as hi
    rows = conn.execute(
        "SELECT date, start_ts, end_ts, wtype, duration_s, distance_km, source "
        "FROM health_workouts WHERE date>=? AND date<=? ORDER BY start_ts",
        (since, until)).fetchall()
    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    out = []
    for d in sorted(by_day):
        day = by_day[d]
        srcs = [r["source"] for r in day if r["source"] in hi.SOURCE_PRECEDENCE]
        best = min(srcs, key=hi.SOURCE_PRECEDENCE.index) if srcs else None
        for r in day:
            if best is not None and r["source"] != best:
                continue
            start, end = _ist(r["start_ts"]), _ist(r["end_ts"])
            out.append({
                "date": d, "kind": hi.classify_workout(r["wtype"]),
                "wtype": r["wtype"] or "", "start": start, "end": end,
                "start_hm": start[11:16] if len(start) >= 16 else "",
                "end_hm": end[11:16] if len(end) >= 16 else "",
                "minutes": round((r["duration_s"] or 0) / 60.0, 1),
                "distance_km": round(r["distance_km"], 2) if r["distance_km"] else None,
                "source": r["source"]})
    return out


def _watch_epochs(since, until):
    """Medication epochs overlapping the window. Resting HR and HRV inside a
    drug change are artefacts of the change; the band exists so that is read
    off the chart instead of remembered."""
    out = []
    try:
        rows = db().execute("SELECT label, date_start, date_end FROM med_epochs "
                            "ORDER BY date_start, id").fetchall()
    except Exception:
        return out
    for r in rows:
        s = (r["date_start"] or "")[:10]
        e = (r["date_end"] or "")[:10]
        if e and e < since:
            continue
        if s and s > until:
            continue
        out.append({"label": r["label"] or "", "date_start": s, "date_end": e})
    return out


@app.route("/api/feed/watch")
def api_feed_watch():
    if not _feed_authorised(request):
        return {"ok": False, "error": "unauthorised"}, 401
    try:
        days = int(request.args.get("days") or 14)
    except (TypeError, ValueError):
        days = 14
    days = max(1, min(60, days))
    until = today()
    start = date.today() - timedelta(days=days - 1)
    since = start.isoformat()
    daily, workouts = [], []
    conn = None
    try:
        import health_ingest as hi
        conn = hi._connect()
    except Exception:
        conn = None
    if conn is not None:
        try:
            for i in range(days - 1, -1, -1):
                daily.append(_watch_day(conn, (date.today() - timedelta(days=i)).isoformat()))
            workouts = _watch_workouts(conn, since, until)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return {"ok": True, "app": "fitlog", "days": days, "since": since,
            "today": until, "daily": daily, "workouts": workouts,
            "epochs": _watch_epochs(since, until)}


'''

PY_ANCHOR = "# ---------------- warning flags ----------------\n"


def build_edits():
    E = []
    a = "FITLOG_V140_PAIN -- FitLog v1.4.0 analgesic mirror + operating-day load.\n"
    E.append(("docstring marker", a,
              a + "FITLOG_V150_WATCHFEED -- FitLog v1.5.0 read-only watch feed for GutLog.\n"))
    E.append(("python", PY_ANCHOR, PY + PY_ANCHOR))
    return E


def read(path):
    fh = open(path, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(path, text):
    fh = open(path, "w", encoding="utf-8", newline="")
    try:
        fh.write(text)
    finally:
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("FitLog Phase J: watch read feed -> v1.5.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v1.4.0. Apply that first.")
        return 1
    if "def api_feed_watch" in src:
        print("FATAL: /api/feed/watch already present. Nothing written.")
        return 1

    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)

    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    write(tmpf, out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v150-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("-" * 60)
    print("Next:  python3 ../gutlog/test_phase_j.py   then  systemctl restart fitlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

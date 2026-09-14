#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.13.0 -> v3.14.0  ::  the watch strip falls back one day

First real-use finding on the Phase J card: at 07:30 all five tiles read
"no data", because the phone had not yet uploaded the day. That was correct
and useless. He opens this screen between 5 and 7am and the sync happens
later, so the strip was blank at exactly the hour he looks at it.

  * A metric with no figure for today now shows YESTERDAY's, labelled
    "yesterday". Quietly: a fallback that is not labelled is a lie.
  * "no data" is reserved for the case where neither day has a figure. That
    is still a real state and still says so.
  * The direction arrow compares the SHOWN day against the trailing median
    with that day excluded. Comparing a value against a median it is itself
    inside is comparing it with itself, and on a fallback day it would read
    "level" every time.
  * The tile carries n -- the number of days behind the median -- so a thin
    baseline is visible as thin. Deliberately NOT a plausibility threshold:
    the steps median is currently dragged down by two days from before the
    source fix, that self-corrects as the window moves, and a heuristic
    written today would outlive the problem it was written for.

Standing load falls back on the same terms. At 5am an operating day has not
necessarily been tapped either, and a labelled "yesterday" is honest where a
blank tile is merely unhelpful.

Requires v3.13.0 (GUTLOG_V3130_WATCH). Anchor-verified, idempotent,
compile-checked, Jinja-safe, .bak before write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3140_FALLBACK"
PREV = "GUTLOG_V3130_WATCH"

OLD_STRIP = '''    def history(metric):
        out = []
        for r in daily:
            if r.get("date") == tday:
                continue
            m = (r.get("metrics") or {}).get(metric) or {}
            if m.get("value") is not None:
                out.append(float(m["value"]))
        return out

    tmets = (by_date.get(tday) or {}).get("metrics") or {}
    strip = {}
    for k in WATCH_STRIP:
        cur = tmets.get(k) or {}
        v = float(cur["value"]) if cur.get("value") is not None else None
        d_, med, n = _direction(v, history(k))
        strip[k] = {"value": v, "source": cur.get("source") or "",
                    "dir": d_, "median": med, "n": n}
    lh = load.get(tday)
    d_, med, n = _direction(
        (lh / 60.0) if lh is not None else None,
        [v / 60.0 for k2, v in load.items() if k2 != tday])
    strip["load_hours"] = {"value": (round(lh / 60.0, 1) if lh is not None else None),
                           "source": "gutlog", "dir": d_, "median": med, "n": n}
'''

NEW_STRIP = '''    # GUTLOG_V3140_FALLBACK -- one series per tile, the watch metrics from
    # the feed and standing load from GutLog's own rows, so both go through
    # the same fallback and the same median.
    series = {}
    for k in WATCH_STRIP:
        s = {}
        for r in daily:
            m = (r.get("metrics") or {}).get(k)
            if m and m.get("value") is not None:
                s[r.get("date")] = m
        series[k] = s
    series["load_hours"] = dict(
        (d, {"value": round(v / 60.0, 1), "source": "gutlog"})
        for d, v in load.items())

    strip = {}
    for k in WATCH_STRIP + ("load_hours",):
        s = series.get(k) or {}
        # Today if it has a figure, else yesterday. He opens this between 5
        # and 7am; the phone syncs later, so at the hour he actually looks
        # today is usually still empty and a blank strip is correct and
        # useless. Falling back is always labelled, never silent.
        day = None
        for cand in (tday, yday):
            if s.get(cand) and s[cand].get("value") is not None:
                day = cand
                break
        cur = s.get(day) or {}
        v = float(cur["value"]) if cur.get("value") is not None else None
        # The median excludes the day being shown. Leave it in and the figure
        # is compared against a median it is itself inside, which on a
        # fallback day reads "level" every time.
        hist = [float(x["value"]) for d2, x in s.items()
                if d2 != day and x and x.get("value") is not None]
        d_, med, n = _direction(v, hist)
        strip[k] = {"value": v, "source": cur.get("source") or "",
                    "day": day or "", "stale": bool(day) and day != tday,
                    "dir": d_, "median": med, "n": n}
'''

JS_OLD = """  const bits=[];
  if(d.dir&&d.median!==null&&d.median!==undefined){
    bits.push(WK_ARROW[d.dir]+' vs '+wkNum(d.median,k)+' median of '+d.n+'d');
  }else if(d.value!==null&&d.value!==undefined){
    bits.push('not enough history to compare');
  }
"""

JS_NEW = """  const bits=[];
  if(d.stale)bits.push('yesterday');
  if(d.dir&&d.median!==null&&d.median!==undefined){
    bits.push(WK_ARROW[d.dir]+' vs '+wkNum(d.median,k)+' median of '+d.n+' d');
  }else if(d.value!==null&&d.value!==undefined){
    /* n is shown either way, so a thin baseline is visible as thin rather
       than quietly standing behind an arrow */
    bits.push(d.n?('only '+d.n+' d of history'):'no history to compare');
  }
"""

JS_SUM_OLD = """  const s=j.strip||{};
  const sum=[];
  if(s.steps&&s.steps.value!==null&&s.steps.value!==undefined)
    sum.push(wkNum(s.steps.value,'steps')+' steps');
  if(s.exercise_minutes&&s.exercise_minutes.value)
    sum.push(Math.round(s.exercise_minutes.value)+' min');
  if(s.load_hours&&s.load_hours.value)sum.push(s.load_hours.value+' h on legs');
  $('#wkSum').textContent=sum.length?sum.join(' \\u00b7 '):'no data today';
"""

JS_SUM_NEW = """  const s=j.strip||{};
  const sum=[];
  if(s.steps&&s.steps.value!==null&&s.steps.value!==undefined)
    sum.push(wkNum(s.steps.value,'steps')+' steps');
  if(s.exercise_minutes&&s.exercise_minutes.value)
    sum.push(Math.round(s.exercise_minutes.value)+' min');
  if(s.load_hours&&s.load_hours.value)sum.push(s.load_hours.value+' h on legs');
  /* say so in the header too, or a fallback figure reads as today's */
  const anyStale=['steps','exercise_minutes','load_hours','resting_hr','hrv_ms']
    .some(k=>s[k]&&s[k].stale);
  if(sum.length&&anyStale)sum.push('yesterday');
  $('#wkSum').textContent=sum.length?sum.join(' \\u00b7 '):'no data yet';
"""


def build_edits():
    E = []
    E.append(("version marker", PREV + "\n", PREV + " " + MARKER + "\n"))
    a = ('    tday = today()\n'
         '    since = (date.today() - timedelta(days=days - 1)).isoformat()\n')
    E.append(("yesterday", a,
              '    tday = today()\n'
              '    yday = (date.today() - timedelta(days=1)).isoformat()\n'
              '    since = (date.today() - timedelta(days=days - 1)).isoformat()\n'))
    E.append(("strip fallback", OLD_STRIP, NEW_STRIP))
    E.append(("tile label", JS_OLD, JS_NEW))
    E.append(("summary label", JS_SUM_OLD, JS_SUM_NEW))
    return E


MUST_DEFINE = ["loadWatch", "wkChart", "wkTile", "wkNum", "loadNow",
               "buildNowStatics", "loadPain", "buildActTiles", "loadActivity"]


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
    print("GutLog watch strip: fall back one day -> v3.14.0")
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
        print("FATAL: this file is not at v3.13.0. Apply that first.")
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

    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2

    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)

    missing = [f for f in MUST_DEFINE if not re.search(r"function\s+" + f + r"\s*\(", out)]
    if missing:
        print("DEFINITION CHECK FAILED, nothing written: " + ", ".join(missing))
        return 2

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
    bak = args.file + ".bak-v3140-" + stamp
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
    print("Next:  python3 test_phase_j.py   then  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

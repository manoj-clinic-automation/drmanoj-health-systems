#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.6.x -> v1.7.0  ::  FITLOG_V170_RINGGOALS -- the rings get their goals back

The activity rings fill against the goals the Watch sends. Only the earlier
iOS feed carried those goals; Health Auto Export, which feeds FitLog now,
sends none. So every ring drew as a bare grey outline with "no goal on file".

  * w_last_goals(day): the newest goal on or before the day. A goal is a
    setting, not a measurement, so it holds until the Watch sends a new one.
    A goal from an earlier day is always named with its date on the page.
  * Both the "Today so far" strip and the /watch rings card use it.
  * The /watch Move ring falls back to active energy when the Watch sent no
    separate Move figure -- the strip already did this; Move IS active energy.
  * Goals remain context-only. No rule reads a goal.

Requires FITLOG_SLEEP_MEDIAN_N7. Anchor-verified, idempotent, compile-checked,
.bak before write, self-restoring, --reverse. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "app.py")
PREV = "FITLOG_SLEEP_MEDIAN_N7"
MARKER = "FITLOG_V170_RINGGOALS"

EDITS = [('header', 'FITLOG_SLEEP_P2_PAGE -- the sleep record on /watch. Measured, never scored.\n', 'FITLOG_SLEEP_P2_PAGE -- the sleep record on /watch. Measured, never scored.\nFITLOG_V170_RINGGOALS -- FitLog v1.7.0 rings use the last goals the Watch sent.\n'), ('helper', 'W_DASH = "\\u2014"\n', 'W_DASH = "\\u2014"\n\n\n# FITLOG_V170_RINGGOALS -- a ring\'s goal is a setting on the Watch, not a\n# daily measurement. Health Auto Export sends none; only the earlier iOS feed\n# did. So the last goal the Watch sent still describes today until a newer\n# one arrives -- and it is always shown with the date it was sent, never\n# passed off as today\'s. Goals stay context-only: no rule reads one.\nW_GOAL_KEYS = ("move_goal_kcal", "exercise_goal_min", "stand_goal_hours")\n\n\ndef w_last_goals(day):\n    """{goal_key: (value, date)} -- the newest positive goal on or before day."""\n    out = {}\n    for k in W_GOAL_KEYS:\n        r = db().execute(\n            "SELECT date, value FROM health_metrics WHERE source = ? AND "\n            "metric = ? AND date <= ? AND value > 0 ORDER BY date DESC LIMIT 1",\n            (W_SRC, k, day)).fetchone()\n        if r:\n            out[k] = (r["value"], r["date"])\n    return out\n\n\ndef w_goal_note(used):\n    """Words for goals carried forward from an earlier day."""\n    days = sorted(set(d for _v, d in used.values()))\n    if not days:\n        return ""\n    try:\n        txt = datetime.strptime(days[0], "%Y-%m-%d").strftime("%d %b").lstrip("0")\n    except ValueError:\n        txt = days[0]\n    return "goals as last sent by the Watch on " + txt\n'), ('rings goal', '    cells = []\n    for label, got_key, goal_key, unit, colour in W_RINGS:\n        got = rows[day].get(got_key)\n        goal = rows[day].get(goal_key)\n', '    cells = []\n    last_goals = w_last_goals(day)\n    used = {}\n    for label, got_key, goal_key, unit, colour in W_RINGS:\n        got = rows[day].get(got_key)\n        if got is None and got_key == "move_energy_kcal":\n            got = rows[day].get("active_energy_kcal")\n        goal = rows[day].get(goal_key)\n        if not goal and goal_key in last_goals:\n            goal = last_goals[goal_key][0]\n            used[goal_key] = last_goals[goal_key]\n'), ('rings note', '            \' &middot; source: Apple Watch</div><div class="wrings">\' +\n', '            \' &middot; source: Apple Watch\' +\n            ((\' &middot; \' + w_esc(w_goal_note(used))) if used else \'\') +\n            \'</div><div class="wrings">\' +\n'), ('strip goals init', '    for label, keys, goal_key, unit, colour in W_STRIP:\n', '    last_goals = w_last_goals(t)\n    used_goals = {}\n    for label, keys, goal_key, unit, colour in W_STRIP:\n'), ('strip goal', '        goal = vals.get(goal_key)\n', '        goal = vals.get(goal_key)\n        if not goal and goal_key in last_goals:\n            goal = last_goals[goal_key][0]\n            used_goals[goal_key] = last_goals[goal_key]\n'), ('strip note', '    return (\'<div class="card wstrip" id="wstrip"><div class="wstrip-h">\'\n', '    if used_goals:\n        ctx_html += (\'<div class="small wctx">Rings drawn against the \' +\n                     w_esc(w_goal_note(used_goals)) + ".</div>")\n    return (\'<div class="card wstrip" id="wstrip"><div class="wstrip-h">\'\n')]


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



def reverse(path, out_path):
    src = read(path)
    if MARKER not in src:
        print("FATAL: not patched: " + path)
        return 1
    out = src
    for label, old, new in reversed(EDITS):
        if out.count(new) != 1:
            print("REVERSE FAILED, nothing written: " + label)
            return 1
        out = out.replace(new, old, 1)
    if MARKER in out:
        print("REVERSE FAILED: marker survives, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed previous build -> " + out_path)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("FitLog: rings use the last goals the Watch sent -> v1.7.0")
    print("file : " + args.file)
    if not os.path.exists(args.file):
        print("FATAL: not found")
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    bad = [(l, src.count(o)) for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        for l, c in bad:
            print("  %s: found %d times, need 1" % (l, c))
        print("Refusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    bak = args.file + ".bak-v170-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(EDITS))
    print("Next: python3 test_ring_goals.py app.py ; test_watch_strip.py ; test_watch_page.py")
    print("Back: cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

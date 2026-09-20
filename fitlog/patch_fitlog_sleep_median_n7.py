#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog :: FITLOG_SLEEP_MEDIAN_N7 -- no median until there are seven nights.

WHY
---
The Sleep card shipped showing his own median over however many nights were on
record, with the count stated beside it. Saying "median 3 h 45 m, over 2 nights
with data" is honest and still wrong to show:

  * a median over two or three nights is not a baseline, it is two or three
    numbers wearing the word. Printing n does not stop it being read as one.
  * the figure invites a comparison that cannot be made, and the whole reason
    this record exists is that a nightly comparison is the thing that makes
    insomnia worse. An honest label on a misleading number is still a
    misleading number.
  * the early nights on this record include 2026-09-15, which reached the
    server truncated because Health Auto Export sent one block of the night.
    A median resting on a night we KNOW is wrong is worse than no median.

So it is withheld, not qualified. Below seven nights the card says there are
not enough nights yet and names how many there are, which is a statement about
the record rather than about him.

Seven because it is a week: enough that one bad night does not move it, and the
shortest span over which "your own normal" means anything.

Requires FITLOG_SLEEP_P2_PAGE. Anchor-verified, idempotent, compile-checked,
.bak before write, self-restoring, reversible. Python 3.9.

    python3 patch_fitlog_sleep_median_n7.py --check
    python3 patch_fitlog_sleep_median_n7.py --file /root/fitlog/app.py
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "app.py")
PREV = "FITLOG_SLEEP_P2_PAGE"
MARKER = "FITLOG_SLEEP_MEDIAN_N7"

# ---------------------------------------------------------------- edit 1
CONST_OLD = '''W_SLEEP_NIGHTS = 14
'''

CONST_NEW = '''W_SLEEP_NIGHTS = 14

# FITLOG_SLEEP_MEDIAN_N7. Below this many nights with data, no median is
# shown at all. A median over two or three nights is not a baseline, and
# printing the count beside it does not stop it being read as one -- an
# honest label on a misleading number is still a misleading number. Seven
# is a week: enough that one night does not move it.
W_SLEEP_MEDIAN_MIN_N = 7
'''

# ---------------------------------------------------------------- edit 2
MED_OLD = '''    # His own recent median, over the nights the Watch actually recorded.
    vals = [nights[d].get("asleep_h") for d in days]
    med = w_median(vals)
    n_med = len([v for v in vals if v is not None])
    med_txt = ""
    if med is not None:
        med_txt = ('<div class="wmed">Your own last ' + str(len(days)) +
                   (" night" if len(days) == 1 else " nights") +
                   " on record: median <b>" + w_hm(med) + "</b> asleep, over "
                   + str(n_med) + (" night" if n_med == 1 else " nights") +
                   " with data. This is the only thing on this page anything "
                   "is compared with.</div>")
'''

MED_NEW = '''    # His own recent median, over the nights the Watch actually recorded --
    # and only once there are enough of them to mean anything.
    vals = [nights[d].get("asleep_h") for d in days]
    n_med = len([v for v in vals if v is not None])
    if n_med < W_SLEEP_MEDIAN_MIN_N:
        # Withheld, not qualified. See FITLOG_SLEEP_MEDIAN_N7.
        med_txt = ('<div class="wmed">Not enough nights yet to say what your '
                   "own normal looks like \\u2014 " + str(n_med) +
                   (" night" if n_med == 1 else " nights") + " on record, and "
                   "this page will not put a night against a median drawn "
                   "from fewer than " + str(W_SLEEP_MEDIAN_MIN_N) +
                   ". Nothing is being compared until then.</div>")
    else:
        med_txt = ('<div class="wmed">Your own last ' + str(len(days)) +
                   (" night" if len(days) == 1 else " nights") +
                   " on record: median <b>" + w_hm(w_median(vals)) +
                   "</b> asleep, over " + str(n_med) +
                   (" night" if n_med == 1 else " nights") +
                   " with data. This is the only thing on this page anything "
                   "is compared with.</div>")
'''

EDITS = [
    ("the threshold", CONST_OLD, CONST_NEW),
    ("withhold the median below it", MED_OLD, MED_NEW),
]


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
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    bad = ["  " + label + ": " + str(src.count(new)) + " (need 1)"
           for label, old, new in EDITS if src.count(new) != 1]
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    out = src
    for label, old, new in EDITS:
        out = out.replace(new, old, 1)
    if MARKER in out or PREV not in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed " + PREV + " -> " + out_path
          + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)

    print("=" * 66)
    print("FitLog " + MARKER + ": no median until seven nights")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at " + PREV + ".")
        return 1

    problems = ["  " + label + ": found " + str(src.count(old))
                + " times, need 1"
                for label, old, new in EDITS if src.count(old) != 1]
    print("anchors: " + str(len(EDITS) - len(problems)) + "/"
          + str(len(EDITS)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, old, new in EDITS:
        out = out.replace(old, new, 1)
    if not re.search(r"\nW_SLEEP_MEDIAN_MIN_N = 7\n", out):
        print("DEFINITION CHECK FAILED: W_SLEEP_MEDIAN_MIN_N")
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
    bak = args.file + ".bak-medn7-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(EDITS)) + " edits")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

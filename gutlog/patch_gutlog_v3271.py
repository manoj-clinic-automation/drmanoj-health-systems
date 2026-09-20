#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.27.0 -> v3.27.1  ::  GUTLOG_V3271_TARGETJS

v3.27.0 moved the protein target to the diet plan's figure -- but only on
the server. Three places in the page still carried the old 57:

  * the basket line, "day protein would reach n/57 g",
  * the day protein bar, which filled at 57 and so read full while the card
    above it said "of 100",
  * the ring's own fallback, which is legitimate but should follow the same
    single value.

The page now keeps one target, PROT_TGT, taken from the server the moment
the rings load, with 57 only as the fallback if the server ever gives none.
Caught by Claude Code after v3.27.0 went live; the v3.27.0 suite read the
server helper and never rendered the basket or the bar.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
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
MARKER = "GUTLOG_V3271_TARGETJS"
PREV = "GUTLOG_V3270_TIMEPICK"

DECL_OLD = "let LIB=[], PRN=[], basket=[], libFilter='all', dayProtein=0;\n"
DECL_NEW = ("let LIB=[], PRN=[], basket=[], libFilter='all', dayProtein=0;\n"
            "/* GUTLOG_V3271_TARGETJS -- one protein target on the page too. The\n"
            "   server decides it (the diet plan's, when there is one); 57 is only\n"
            "   the fallback until the first answer arrives. */\n"
            "let PROT_TGT=57;\n")

RING_OLD = ("  dayProtein=s.protein||0;\n")
RING_NEW = ("  dayProtein=s.protein||0;\n"
            "  if(s.target)PROT_TGT=s.target;\n")

PCT_OLD = "  const pPct=Math.min(1,(s.protein||0)/(s.target||57));\n"
PCT_NEW = "  const pPct=Math.min(1,(s.protein||0)/(s.target||PROT_TGT));\n"

BASKET_OLD = "day protein would reach <b>${proj.toFixed(0)}/57 g</b>`;"
BASKET_NEW = "day protein would reach <b>${proj.toFixed(0)}/${PROT_TGT} g</b>`;"

BAR_OLD = "  $('#dayPbar').style.width=Math.min(100,p/57*100)+'%';\n"
BAR_NEW = "  $('#dayPbar').style.width=Math.min(100,p/PROT_TGT*100)+'%';\n"

EDITS = [
    ("version", "GUTLOG_V3260_TRIALS GUTLOG_V3270_TIMEPICK\n",
     "GUTLOG_V3260_TRIALS GUTLOG_V3270_TIMEPICK " + MARKER + "\n"),
    ("declare", DECL_OLD, DECL_NEW),
    ("rings target", RING_OLD, RING_NEW),
    ("ring pct", PCT_OLD, PCT_NEW),
    ("basket line", BASKET_OLD, BASKET_NEW),
    ("day bar", BAR_OLD, BAR_NEW),
]
PAGE_TEXT = [DECL_NEW, RING_NEW, PCT_NEW, BASKET_NEW, BAR_NEW]


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("=" * 66)
    print("GutLog one protein target on the page as well -> v3.27.1")
    print("file : " + a.file)
    print("=" * 66)
    for t in PAGE_TEXT:
        if re.search(r"\{[{%#]", t):
            print("FATAL: a Jinja token in new page text (CLAUDE.md 5b). Nothing written.")
            return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.27.0.")
        return 1
    bad = [l for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + ". Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    if re.search(r"/57 g|p/57\*100", out):
        print("FATAL: a hardcoded 57 survives in the page. Nothing written.")
        return 1
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cand.py")
    write(f, out)
    try:
        py_compile.compile(f, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
    bak = a.file + ".bak-v3271-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    print("backup : " + bak)
    write(a.file, out)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, a.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(EDITS))
    print("Next:  python3 test_v3271.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

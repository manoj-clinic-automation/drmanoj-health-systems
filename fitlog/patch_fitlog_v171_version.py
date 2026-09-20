#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.7.0 -> v1.7.1  ::  FITLOG_V171_VERSION -- /health tells the truth

`/health` answered {"version": "1.3.1"} while FitLog was v1.7.0: the literal
had not been touched since v1.3.1, and the deploy runbook reads that endpoint
to confirm a release. A health check that confirms the wrong build is worse
than none. The version now lives in one constant, APP_VERSION, which every
release bumps, and `/health` reports it.

Found by Claude Code on 20-Sep-2026 while adding GutLog's /healthz.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse. Python 3.9.
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
MARKER = "FITLOG_V171_VERSION"
PREV = "FITLOG_V170_RINGGOALS"

EDITS = [
    ("header",
     "FITLOG_V170_RINGGOALS -- FitLog v1.7.0 rings use the last goals the Watch sent.\n",
     "FITLOG_V170_RINGGOALS -- FitLog v1.7.0 rings use the last goals the Watch sent.\n"
     "FITLOG_V171_VERSION -- FitLog v1.7.1 /health reports the running version.\n"),
    ("constant",
     "import health_sso  # noqa: E402\n",
     "import health_sso  # noqa: E402\n\n"
     "# FITLOG_V171_VERSION -- one version, reported by /health. BUMP THIS IN EVERY\n"
     "# RELEASE: the deploy runbook reads /health to confirm which build is running,\n"
     "# and it answered 1.3.1 through four releases before this was noticed.\n"
     'APP_VERSION = "1.7.1"\n'),
    ("health",
     '    return {"app": "fitlog", "version": "1.3.1", "ok": True}\n',
     '    return {"app": "fitlog", "version": APP_VERSION, "ok": True}\n'),
]


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
    print("FitLog /health reports the running version -> v1.7.1")
    print("file : " + a.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v1.7.0.")
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
    bak = a.file + ".bak-v171-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_health_version.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

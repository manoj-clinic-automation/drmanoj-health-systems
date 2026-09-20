#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.27.1 -> v3.27.2  ::  GUTLOG_V3272_HEALTHZ

GutLog had no /healthz, and no version constant either. RxGuard has both
(`APP_VERSION` and a plain-text `ok <version>`); FitLog has /health. Every
GutLog runbook step that said "read /healthz" was reading a route that has
never existed, and a 404 is indistinguishable from a broken deploy.

  * APP_VERSION, next to the other module constants, carrying the marker
    list in a trailing comment exactly as RxGuard's does.
  * GET /healthz -> text/plain "ok 3.27.2". No login, no database, no
    session. It answers before the app has a password set, because its
    whole job is to say the process is up.

Shipped as its own patcher rather than folded into v3.27.1: that release's
six anchors are all inside the page JavaScript, and its manifest is already
evidenced. Two anchors here, both outside it.

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

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3272_HEALTHZ"
PREV = "GUTLOG_V3271_TARGETJS"

VERSION = "3.27.2"

CONST_OLD = 'MAX_FILE_MB = 25   # v3.11.0: report pages are saved at ~220dpi now, not ~110\n'
CONST_NEW = (
    'MAX_FILE_MB = 25   # v3.11.0: report pages are saved at ~220dpi now, not ~110\n'
    '# GUTLOG_V3272_HEALTHZ -- one place that states the running version.\n'
    'APP_VERSION = "3.27.2"   # GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS '
    'GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS\n'
)

ROUTE_OLD = '# HEALTH_SSO_V1 ---------------------------------------------------------\n@app.route("/sso/vouch")\n'
ROUTE_NEW = (
    '# GUTLOG_V3272_HEALTHZ --------------------------------------------------\n'
    '# Plain text, no login, no database, no session. It says the process is\n'
    '# up and which build it is, and nothing else -- deliberately nothing the\n'
    '# record could leak through, because it is the one route with no auth.\n'
    '@app.route("/healthz")\n'
    'def healthz():\n'
    '    return Response("ok %s" % APP_VERSION, mimetype="text/plain")\n'
    '\n'
    '# HEALTH_SSO_V1 ---------------------------------------------------------\n'
    '@app.route("/sso/vouch")\n'
)

EDITS = [
    ("version constant", CONST_OLD, CONST_NEW),
    ("healthz route", ROUTE_OLD, ROUTE_NEW),
]

# CLAUDE.md 5b: a Jinja token in new text breaks the whole page at render
# time and still passes py_compile. Nothing this patcher adds may carry one.
JINJA = ("{{", "{%", "{#")
NEW_TEXT = [CONST_NEW, ROUTE_NEW]


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

    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)

    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0

    print("==================================================================")
    print("GutLog a health endpoint that exists -> v" + VERSION)
    print("file : " + a.file)
    print("==================================================================")

    for t in NEW_TEXT:
        for tok in JINJA:
            if tok in t:
                print("FATAL: new text carries the Jinja token %r. Nothing written." % tok)
                return 1

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
    if a.check:
        print("All anchors OK.")
        return 0

    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)

    if len(re.findall(r'@app\.route\("/healthz"\)', out)) != 1:
        print("FATAL: /healthz is not defined exactly once. Nothing written.")
        return 1

    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    bak = a.file + ".bak-v3272-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3272_healthz.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

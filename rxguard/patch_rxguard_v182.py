#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.1 -> v1.8.2  ::  one session key for every worker (RXGUARD_V182_SECRETFILE)

When RXGUARD_SECRET is not in the environment, create_app() fell back to a
fresh random key -- one per gunicorn worker, and a new one at every restart.
Sessions signed by one worker were refused by the other. The fallback is now
a key file beside the database (<db>.secret, mode 600), created once and
shared, exactly as GutLog keeps its key. RXGUARD_SECRET, when set, still wins.

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

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V182_SECRETFILE"
PREV = "RXGUARD_V181_LABELMAX"

EDITS_ONE = [('version', 'APP_VERSION = "1.8.1"   # ', 'APP_VERSION = "1.8.2"   # RXGUARD_V182_SECRETFILE '), ('key file helper', 'def create_app(db_path=None, secret=None):\n', 'def _secret_from_file(db_path):\n    """RXGUARD_V182_SECRETFILE -- the session key when RXGUARD_SECRET is unset.\n\n    A random key per process meant each gunicorn worker signed sessions with\n    a key of its own: a request that landed on the other worker looked logged\n    out, and every restart logged everyone out. The key now lives beside the\n    database, as GutLog\'s does: created once, mode 600, shared by every\n    worker. Two workers starting together cannot both create it -- the file\n    appears by an atomic link, and the loser reads the winner\'s key.\n\n    A key file that cannot be read never stops the app: it falls back to a\n    temporary key and says so in the error log.\n    """\n    path = db_path + ".secret"\n    for _attempt in range(2):\n        try:\n            with open(path, "r", encoding="ascii") as fh:\n                key = fh.read().strip()\n            if len(key) >= 32:\n                return key\n            sys.stderr.write("rxguard: %s is too short to be a key; "\n                             "using a temporary one\\n" % path)\n            return secrets.token_hex(32)\n        except FileNotFoundError:\n            pass\n        except (OSError, UnicodeDecodeError) as exc:\n            sys.stderr.write("rxguard: cannot read %s (%s); "\n                             "using a temporary key\\n" % (path, exc))\n            return secrets.token_hex(32)\n        key = secrets.token_hex(32)\n        tmp = "%s.%d.tmp" % (path, os.getpid())\n        try:\n            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n            with os.fdopen(fd, "w", encoding="ascii") as fh:\n                fh.write(key + "\\n")\n            try:\n                os.link(tmp, path)\n                return key\n            except FileExistsError:\n                continue\n        except OSError as exc:\n            sys.stderr.write("rxguard: cannot write %s (%s); "\n                             "using a temporary key\\n" % (path, exc))\n            return key\n        finally:\n            try:\n                os.remove(tmp)\n            except OSError:\n                pass\n    return secrets.token_hex(32)\n\n\ndef create_app(db_path=None, secret=None):\n'), ('fallback', '    app.secret_key = (secret or os.environ.get("RXGUARD_SECRET")\n                      or secrets.token_hex(32))\n', '    app.secret_key = (secret or os.environ.get("RXGUARD_SECRET")\n                      or _secret_from_file(app.config["DB_PATH"]))\n')]
EDITS_ALL = []


def build_edits():
    return [(l, o, n) for l, o, n in EDITS_ONE]


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
    """Reconstruct v1.8.1 for tools/NEGATIVE_CONTROL.py."""
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    out = src
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            print("REVERSE FAILED, nothing written: " + label + " found " + str(c) + " times")
            return 1
        out = out.replace(new, anchor, 1)
    for label, anchor, new, cnt in EDITS_ALL:
        if out.count(new) != cnt:
            print("REVERSE FAILED, nothing written: " + label)
            return 1
        out = out.replace(new, anchor)
    if MARKER in out or PREV not in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed " + PREV + " -> " + out_path + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT",
                    help="reconstruct v1.8.1 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("RxGuard: one session key for every worker -> v1.8.2")
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
        print("FATAL: this file is not at v1.8.1. Apply that first.")
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
    for label, anchor, new, cnt in EDITS_ALL:
        if src.count(anchor) != cnt:
            print("ANCHOR FAILURE: %s found %d times, need %d. Nothing written." % (label, src.count(anchor), cnt))
            return 1
    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    for label, anchor, new, cnt in EDITS_ALL:
        out = out.replace(anchor, new)
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
    bak = args.file + ".bak-v182-" + stamp
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
    print("-" * 66)
    print("Next:  python3 test_secret_file.py app.py")
    print("       python3 smoke_test.py   then  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

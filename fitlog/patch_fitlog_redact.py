#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog -- anchor-verified patcher: drug name out of the epochs form.

The med-epoch form's placeholder read "e.g. Nortriptyline taper", naming a
real psychiatric medication in a PUBLIC repository. A placeholder is example
text; a generic one does the same job.

Found by widening the redaction sweep -- this drug was outside the term list,
which had been derived from GutLog's prnmeds and so never saw it.

Compile-checks before writing, takes a .bak, idempotent, self-restoring.
Python 3.9 compatible. NOTE: fitlog/app.py contains f-strings, so the
compile check runs under whatever interpreter you invoke -- run this on the
server (3.9), not on a newer local build.

Usage:
    python3 patch_fitlog_redact.py                # /root/fitlog/app.py
    python3 patch_fitlog_redact.py --dry-run
"""

import os
import py_compile
import shutil
import sys
import tempfile
from datetime import datetime

TARGET = "/root/fitlog/app.py"
MARKER = 'placeholder="e.g. medication taper"'

A1_OLD = 'placeholder="e.g. Nortriptyline taper"'
A1_NEW = 'placeholder="e.g. medication taper"'

EDITS = [("epochs form placeholder", A1_OLD, A1_NEW)]


def main():
    dry = False
    positional = []
    for item in sys.argv[1:]:
        if item == "--dry-run":
            dry = True
            continue
        if item.startswith("--"):
            print("FAIL: unknown flag " + item)
            return 2
        positional.append(item)

    target = positional[0] if positional else TARGET

    if not target.endswith(".py"):
        print("FAIL: target must be a .py file, got: " + target)
        return 2
    if not os.path.exists(target):
        print("FAIL: not found: " + target)
        return 2

    print("Target  : " + target)

    with open(target, "r", encoding="utf-8") as fh:
        src = fh.read()

    if MARKER in src:
        print("SKIP: already redacted. Nothing to do.")
        return 0

    patched = src
    for name, old, new in EDITS:
        count = patched.count(old)
        if count != 1:
            print("FAIL: anchor '" + name + "' matched " + str(count)
                  + " times (need exactly 1).")
            print("Refusing to patch. Nothing written.")
            return 1
        patched = patched.replace(old, new, 1)
        print("  anchored: " + name)

    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    with open(tmpf, "w", encoding="utf-8") as fh:
        fh.write(patched)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("Compile : ok")
    except py_compile.PyCompileError as exc:
        print("FAIL: patched source does not compile:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 1
    shutil.rmtree(tmpd, ignore_errors=True)

    if dry:
        print("Dry run. Nothing written.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = target + ".bak-redact-" + stamp
    shutil.copy2(target, bak)
    print("Backup  : " + bak)

    with open(target, "w", encoding="utf-8") as fh:
        fh.write(patched)

    try:
        py_compile.compile(target, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, target)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2

    print("Patched : ok")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

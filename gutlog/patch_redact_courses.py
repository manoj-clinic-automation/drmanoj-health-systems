#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog -- anchor-verified patcher: drug names out of the courses UI.

The courses panel carried a real psychiatric medication history: a hint
naming a specific SSRI-to-SNRI switch, and a chip list with four drugs and
their doses. app.py is tracked in a PUBLIC repository.

The chip list is a CONVENIENCE, not data: it prefills a free-text field.
Anything previously started still shows, because started courses live in the
courses table, not in this list. The field stays free text, so any drug can
still be typed.

The default chips move to regimen.local.json ('course_chips'), gitignored,
and fall back to a single 'Other' entry when it is absent -- the same reasoning
as PRN_SEED: a clone gets an empty-ish default, not a stranger's prescriptions.

Found by widening the redaction sweep: these drugs were outside the term list,
which had been derived from prnmeds only and so never saw them.

Compile-checks before writing, takes a .bak, idempotent, self-restoring.
Python 3.9 compatible.

Usage:
    python3 patch_redact_courses.py                # /root/gutlog/app.py
    python3 patch_redact_courses.py --dry-run
"""

import os
import py_compile
import shutil
import sys
import tempfile
from datetime import datetime

TARGET = "/root/gutlog/app.py"
MARKER = "_course_chips"

A1_OLD = ('    <p class="hint">Multi-day drugs with a live day counter. '
          'The paroxetine &rarr; duloxetine switch runs here.</p>')
A1_NEW = ('    <p class="hint">Multi-day drugs with a live day counter. '
          'Switches and tapers run here.</p>')

# APP_PAGE is a RAW triple-quoted string served through
# render_template_string, i.e. Jinja. So the chip list becomes a Jinja
# placeholder, not a Python concatenation -- inside r""" ... """ a
# concatenation would render as literal text.
A2_OLD = ('      data-v="Duloxetine 30 mg OD|Paroxetine taper|'
          'Nortriptyline (HS)|Rifaximin 550 mg BD|Other"></div>')
A2_NEW = '      data-v="{{ course_chips }}"></div>'

A4_OLD = 'return render_template_string(APP_PAGE)'
A4_NEW = 'return render_template_string(APP_PAGE, course_chips=_course_chips())'

# The loader, inserted just above the seed loader added by
# patch_redact_seed.py so both sit together.
A3_OLD = 'def _local_seed(key):'
A3_NEW = '''def _course_chips():
    """
    Default chips for the 'Start a course' picker.

    These were four real drugs with doses, hardcoded in a public repository.
    They are a prefill convenience for a free-text field, so an empty default
    costs nothing but a little typing. Existing courses are unaffected: they
    live in the courses table, not here.
    """
    vals = _local_seed("course_chips")
    return "|".join(vals) if vals else "Other"


def _local_seed(key):'''

EDITS = [
    ("courses hint", A1_OLD, A1_NEW),
    ("course chip list", A2_OLD, A2_NEW),
    ("course chip loader", A3_OLD, A3_NEW),
    ("bind chips at render", A4_OLD, A4_NEW),
]


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
        print("SKIP: course chips already externalised. Nothing to do.")
        return 0

    if "_local_seed" not in src:
        print("FAIL: run patch_redact_seed.py first -- this patch builds on")
        print("      the _local_seed() loader it installs.")
        return 1

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
    bak = target + ".bak-courses-" + stamp
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

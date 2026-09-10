#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog -- anchor-verified patcher: move seed lists out of app.py.

PRN_SEED carried fifteen real medicine names and DOCTOR_SEED two named
treating doctors. app.py is tracked in a PUBLIC repository, so both were
published. They now come from regimen.local.json, which is gitignored.

WHY AN EMPTY FALLBACK IS SAFE
    _seed() returns immediately when settings['seeded_v3'] is set, which it
    is on any database that has ever been opened. So the live database is
    untouched by this change -- these lists are read only when seeding a
    brand-new database.

    With the file absent, a fresh install starts with NO medicines and NO
    doctors instead of someone else's. That is the correct default for
    anyone who clones this repo: an empty catalogue they fill in, not a
    stranger's prescription.

    This is deliberately NOT a loud failure, unlike add_regimen.py and
    tidy_extras.py. Those are operator tools where an empty plan would
    masquerade as success. This is application startup: refusing to boot
    because an optional seed file is missing would turn a cosmetic default
    into an outage.

Compile-checks before writing, takes a .bak, idempotent, self-restoring.
Python 3.9 compatible.

Usage:
    python3 patch_redact_seed.py                 # /root/gutlog/app.py
    python3 patch_redact_seed.py --dry-run
    python3 patch_redact_seed.py /path/to/app.py
"""

import os
import py_compile
import shutil
import sys
import tempfile
from datetime import datetime

TARGET = "/root/gutlog/app.py"
MARKER = "_local_seed"

A1_OLD = '''PRN_SEED = ["Colospa (mebeverine 135)","Drotaverine 80","Paracetamol 500",
    "Etoricoxib 60","Allegra 180","Bilastine 20","Fluticasone nasal spray",
    "Peppermint oil","Psyllium","PEG","Cremaffin","Clonazepam 0.5",
    "Zolpidem 5","ORS","Probiotic"]

DOCTOR_SEED = ["Prof. Ahuja (Gastro)","Dr. V.K. Srivastav (Psychiatry)",
    "Ophthalmology","Orthopaedics","Physician","Other"]'''

A1_NEW = '''def _local_seed(key):
    """
    Seed lists for a BRAND-NEW database, read from regimen.local.json
    beside this file.

    These were literals here until 2026-09-10: fifteen real medicine names
    and two named doctors, in a public repository. The data moved out; the
    file is gitignored.

    Missing file returns an empty list on purpose. _seed() only runs when
    settings['seeded_v3'] is unset, so an existing database never reaches
    this code and is unaffected. A fresh install starts empty rather than
    with someone else's prescription -- which is the right default for a
    clone. Startup must not fail over an optional seed file.
    """
    try:
        with open(os.path.join(BASE, "regimen.local.json"), "r",
                  encoding="utf-8") as fh:
            val = json.load(fh).get(key)
            return val if isinstance(val, list) else []
    except (IOError, OSError, ValueError):
        return []

PRN_SEED = _local_seed("prn_seed")

DOCTOR_SEED = _local_seed("doctor_seed")'''

# -- display strings that name a drug -------------------------------------
# The patches table, its endpoints and the JS element ids are already
# drug-neutral; only these four strings carry the molecule. Renaming them is
# cosmetic - no schema, route or id changes - but it removes an opioid
# prescription from a public repository.

A2_OLD = ('Meds (per-dose PRN ledger, courses, buprenorphine patch) . '
          'Files (vault, labs,')
A2_NEW = ('Meds (per-dose PRN ledger, courses, transdermal patch) . '
          'Files (vault, labs,')

A3_OLD = '<p class="q">Buprenorphine patch history</p>'
A3_NEW = '<p class="q">Transdermal patch history</p>'

A4_OLD = '<div><b>Buprenorphine patch on</b> &middot; ${p.strength}<br>'
A4_NEW = '<div><b>Patch on</b> &middot; ${p.strength}<br>'

A5_OLD = ('<div><b>Buprenorphine patch</b><br>'
          '<small style="color:var(--muted)">5 mcg/hr &middot; occasional'
          '</small></div>')
A5_NEW = ('<div><b>Transdermal patch</b><br>'
          '<small style="color:var(--muted)">occasional</small></div>')

# An incidental molecule name in a food-library note.
A6_OLD = '"Soluble fibre like psyllium"'
A6_NEW = '"Soluble fibre"'

EDITS = [
    ("seed lists out of app.py", A1_OLD, A1_NEW),
    ("docstring: patch label", A2_OLD, A2_NEW),
    ("review card: patch history", A3_OLD, A3_NEW),
    ("patch-on label", A4_OLD, A4_NEW),
    ("patch card label", A5_OLD, A5_NEW),
    ("food note: fibre", A6_OLD, A6_NEW),
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
        print("SKIP: seed lists already externalised. Nothing to do.")
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
    print("")
    print("Ensure regimen.local.json sits beside app.py, then:")
    print("  systemctl restart gutlog")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

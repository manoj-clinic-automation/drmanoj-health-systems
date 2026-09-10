#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: read FITLOG_DB from ingest.env.

Root cause this closes:

    health_ingest.py took DB_PATH from the environment, falling back to a
    hardcoded /root/fitlog/fitlog.db. Nothing ever set FITLOG_DB --
    fitlog.service loads .env (which does not exist), not ingest.env. So a
    live database under a non-default name was migrated by the
    orchestrator while the blueprint kept writing to fitlog.db: two
    databases, one of them unmigrated, HTTP 500 on the first real POST.

    ingest.env is the one file the blueprint already reads directly, and
    it is already mode 600. Putting FITLOG_DB there needs no unit edit and
    no systemd reload -- the same reasoning that put the tokens there.

Precedence after this patch:  environment  >  ingest.env  >  hardcoded default.
The environment stays first so the test suites, which set FITLOG_DB to a
temp database, are unaffected.

Compile-checks before writing, takes a .bak, idempotent.
Python 3.9 compatible.

Usage:
    python3 patch_db_pin.py                  # /root/fitlog/health_ingest.py
    python3 patch_db_pin.py --dry-run
    python3 patch_db_pin.py /path/to/health_ingest.py
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

A1_OLD = '''TOKEN_FILE = os.environ.get("FITLOG_INGEST_ENV", "/root/fitlog/ingest.env")
DB_PATH = os.environ.get("FITLOG_DB", "/root/fitlog/fitlog.db")'''

A1_NEW = '''TOKEN_FILE = os.environ.get("FITLOG_INGEST_ENV", "/root/fitlog/ingest.env")


def _env_file_value(key):
    """
    Read one KEY=value out of the ingest env file. Returns None if the file
    or the key is absent.

    This file is not loaded by systemd -- fitlog.service points
    EnvironmentFile at .env, not ingest.env. The blueprint reads it
    directly, which is exactly why the tokens live here.
    """
    try:
        with open(TOKEN_FILE, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, val = line.split("=", 1)
                if name.strip() == key:
                    return val.strip().strip('"').strip("'")
    except IOError:
        return None
    return None


# Environment first (the test suites set it), then ingest.env, then the
# historical default. Without the middle term, a live database under a
# non-default name gets migrated by the orchestrator while this module
# keeps writing to fitlog.db.
DB_PATH = (os.environ.get("FITLOG_DB")
           or _env_file_value("FITLOG_DB")
           or "/root/fitlog/fitlog.db")'''

EDITS = [
    ("FITLOG_DB from ingest.env", A1_OLD, A1_NEW),
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

    with open(target, "r") as fh:
        src = fh.read()

    if "_env_file_value" in src:
        print("SKIP: FITLOG_DB already read from ingest.env. Nothing to do.")
        return 0

    patched = src
    for name, old, new in EDITS:
        count = patched.count(old)
        if count != 1:
            print("FAIL: anchor '" + name + "' matched " + str(count) + " times.")
            print("Refusing to patch. Nothing written.")
            return 1
        patched = patched.replace(old, new, 1)
        print("  anchored: " + name)

    try:
        compile(patched, target, "exec")
    except SyntaxError as exc:
        print("FAIL: patched source does not compile: " + str(exc))
        return 1
    print("Compile : ok")

    if dry:
        print("Dry run. Nothing written.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = target + ".bak_" + stamp
    shutil.copy2(target, bak)
    print("Backup  : " + bak)

    with open(target, "w") as fh:
        fh.write(patched)

    with open(target, "r") as fh:
        verify = fh.read()
    if "_env_file_value" not in verify:
        shutil.copy2(bak, target)
        print("FAIL: write verification failed. Rolled back from " + bak)
        return 1

    print("Patched : ok")
    print("")
    print("Then: python3 test_db_pin.py  &&  systemctl restart fitlog")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

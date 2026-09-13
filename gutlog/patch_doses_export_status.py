#!/usr/bin/env python3
"""
GutLog - add the missing 'status' column to /export/doses.csv

Why: the doses table stores TAKEN / SKIPPED / EXTRA, but the CSV export never
emitted the status, so a skipped dose was indistinguishable from one taken.

What it changes: exactly one dictionary value inside export_csv() in app.py.
The SELECT statement and the CSV writer are both built from that same string,
so nothing else needs touching.

Safe by construction: pre-flights the live schema, requires the anchor to match
exactly once, compiles the result before writing, keeps a .bak, and is
idempotent - running it twice is harmless.
"""
import os
import shutil
import sqlite3
import sys
import time

APP = os.environ.get("GUTLOG_APP", "/root/gutlog/app.py")
DB = os.environ.get("GUTLOG_DB", "/root/gutlog/health3.db")

OLD = '"doses": "day,dtime,medicine,reason,effect,notes",'
NEW = '"doses": "day,dtime,medicine,status,reason,effect,notes",'


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def main():
    # 1 - pre-flight the live schema. Never add a column to a SELECT before
    #     confirming the table actually has it.
    if not os.path.exists(DB):
        fail("database not found at " + DB)
    con = sqlite3.connect(DB)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(doses)").fetchall()]
    finally:
        con.close()
    if not cols:
        fail("table 'doses' not found in " + DB)
    if "status" not in cols:
        fail("table 'doses' has no 'status' column - columns are: " + ", ".join(cols))
    print("pre-flight OK: doses.status exists")

    # 2 - read the app
    if not os.path.exists(APP):
        fail("app not found at " + APP)
    # newline="" on every read and write below. Without it, running this on
    # Windows against an LF file rewrites every line ending to CRLF. The
    # content is identical and every test still passes, but the repo copy
    # stops being byte-identical to the server's -- the invariant the sync
    # rule in CLAUDE.md rests on.
    src = open(APP, "r", encoding="utf-8", newline="").read()

    # 3 - idempotency
    if NEW in src:
        print("already applied - nothing to do")
        return 0

    # 4 - anchor must match exactly once
    n = src.count(OLD)
    if n != 1:
        fail("expected the anchor line exactly once, found " + str(n)
             + ". Nothing written. Anchor was: " + OLD)
    print("anchor OK: found exactly once")

    out = src.replace(OLD, NEW)

    # 5 - compile before writing
    try:
        compile(out, APP, "exec")
    except SyntaxError as e:
        fail("patched source does not compile: " + str(e))
    print("compile OK")

    # 6 - backup, then write
    bak = APP + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(APP, bak)
    tmp = APP + ".tmp"
    f = open(tmp, "w", encoding="utf-8", newline="")
    f.write(out)
    f.close()
    os.replace(tmp, APP)
    print("written; rollback copy at " + bak)

    # 7 - read back and confirm
    check = open(APP, "r", encoding="utf-8", newline="").read()
    if NEW not in check or OLD in check:
        fail("read-back did not match - restore with: cp " + bak + " " + APP)
    print("read-back OK")
    print("PATCH OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

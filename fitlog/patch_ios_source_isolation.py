#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: per-source isolation in health_hc_records.

Why this exists
---------------
patch_ios_payload.py generalises store_hc() so Apple Watch records land in
health_hc_records alongside Health Connect records. That table has no source
column, and the daily recompute reads:

    SELECT SUM(value) FROM health_hc_records WHERE date = ? AND metric = ?

With records from two wearables on the same date+metric, that sums ACROSS
sources and writes the combined figure under whichever source posted last.
Rule S01 is explicit that values are never summed or averaged across
sources - the phone feed may only fill a date the Watch left silent.

This is not hypothetical. At the time of writing health_hc_records held 129
healthconnect `steps` rows on 2026-09-10 and 2026-09-11, and the iOS payload
in health_raw id 11 carries `steps` records for exactly those IST dates.
Left alone, the owner's step count for both days would have been silently
inflated by the Samsung total.

test_ios_ingest.py cannot catch this: it runs against a fresh temp database
containing only iOS rows, so the cross-source branch is never entered. See
CLAUDE.md rule 2.

What it changes
---------------
1. Adds an idempotent `_ensure_hc_source_column()` - additive ALTER TABLE,
   existing rows default to 'healthconnect', which is what they are.
2. Stores `source` on every health_hc_records row.
3. Filters the daily recompute by source.
4. Binds `source` as a parameter in the health_metrics INSERT instead of
   concatenating it into the SQL string.

Compile-checks before writing, takes a .bak, idempotent.
Python 3.9 compatible.

Usage:
    python3 patch_ios_source_isolation.py --dry-run
    python3 patch_ios_source_isolation.py
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

# ---------------------------------------------------------------- anchors

B1_OLD = '''def store_hc(conn, records, raw_text, source="healthconnect",
             summed=None, workouts=None):'''

B1_NEW = '''def _ensure_hc_source_column(conn):
    """
    health_hc_records predates multi-source record ingest.

    Without a source column the daily recompute sums Apple Watch and
    Health Connect records for the same date+metric into one total,
    which breaks S01. The column is added in place; every row that
    existed before this ran came from Health Connect, which is exactly
    what the default records.

    Additive, idempotent, and a no-op when the table is absent (some
    suites build their own schema).
    """
    cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(health_hc_records)").fetchall()]
    if not cols:
        return False
    if "source" not in cols:
        conn.execute(
            "ALTER TABLE health_hc_records ADD COLUMN source TEXT "
            "NOT NULL DEFAULT 'healthconnect'"
        )
    return True


def store_hc(conn, records, raw_text, source="healthconnect",
             summed=None, workouts=None):'''

B2_OLD = '''    redelivery.
    """
    ts = _now()
    cur = conn.cursor()'''

B2_NEW = '''    redelivery.
    """
    ts = _now()
    _ensure_hc_source_column(conn)
    cur = conn.cursor()'''

B3_OLD = '''            "INSERT INTO health_hc_records "
            "(record_key, date, metric, value, unit, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_key) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (key, date, metric, value, unit, ts),'''

B3_NEW = '''            "INSERT INTO health_hc_records "
            "(record_key, date, metric, value, unit, ingested_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_key) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at, source=excluded.source",
            (key, date, metric, value, unit, ts, source),'''

B4_OLD = '''            "SELECT " + agg + ", unit FROM health_hc_records "
            "WHERE date = ? AND metric = ?",
            (date, metric),'''

B4_NEW = '''            "SELECT " + agg + ", unit FROM health_hc_records "
            "WHERE date = ? AND metric = ? AND source = ?",
            (date, metric, source),'''

B5_OLD = '''            "VALUES (?, ?, ?, ?, '" + source + "', ?) "
            "ON CONFLICT(date, metric, source) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (date, metric, row[0], row[1], ts),'''

B5_NEW = '''            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, metric, source) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (date, metric, row[0], row[1], source, ts),'''

EDITS = [
    ("source-column helper", B1_OLD, B1_NEW),
    ("call helper in store_hc", B2_OLD, B2_NEW),
    ("store source on hc records", B3_OLD, B3_NEW),
    ("filter recompute by source", B4_OLD, B4_NEW),
    ("bind source as parameter", B5_OLD, B5_NEW),
]


def main():
    dry = "--dry-run" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
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

    if "_ensure_hc_source_column" in src:
        print("SKIP: source isolation already present.")
        return 0
    if "parse_ios_payload" not in src:
        print("FAIL: run patch_ios_payload.py first.")
        return 1

    patched = src
    for name, old, new in EDITS:
        count = patched.count(old)
        if count != 1:
            print("FAIL: anchor '" + name + "' matched " + str(count) + " times.")
            print("Nothing written.")
            return 1
        patched = patched.replace(old, new, 1)
        print("  anchored: " + name)

    try:
        compile(patched, target, "exec")
    except SyntaxError as exc:
        print("FAIL: does not compile: " + str(exc))
        return 1
    print("Compile : ok")

    if dry:
        print("Dry run. Nothing written.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    bak = target + ".bak_" + stamp
    shutil.copy2(target, bak)
    print("Backup  : " + bak)

    with open(target, "w") as fh:
        fh.write(patched)

    print("Patched : ok")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

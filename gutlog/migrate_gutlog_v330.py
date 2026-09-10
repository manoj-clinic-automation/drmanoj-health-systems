#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.2.0 -> v3.3.0 schema migration.

Quick-Log surface: medication schedule, status-bearing dose events,
event-level Bristol.

PROPERTIES
  - Additive only. No DROP, no column rewrite, no data loss.
  - Idempotent. Safe to run any number of times.
  - Backs up the live DB via the Python sqlite3.backup() API before any
    write (the sqlite3 CLI is absent on this server).
  - Python 3.9.25 compatible. No PEP 701 f-strings, no backslash escapes
    inside format expressions.

USAGE
  python3 migrate_gutlog_v330.py --dry-run
  python3 migrate_gutlog_v330.py
  python3 migrate_gutlog_v330.py --db /root/gutlog/health3.db

EXIT CODES
  0 success (or dry-run completed)
  1 precondition failure (db missing, unexpected schema)
  2 migration error (transaction rolled back)
"""

import argparse
import datetime
import os
import sqlite3
import sys

VERSION = "3.3.0"
DEFAULT_DB = "/root/gutlog/health3.db"
# Matches the convention already used by /root/gutlog/backup.sh.
# One backup location, not two.
#
# Note on retention: backup.sh prunes with -name '*-20*.db' -mtime +30.
# This filename uses underscores before the timestamp, so it does NOT
# match that glob and will NOT be auto-deleted. Deliberate: a
# pre-migration snapshot should outlive the 30-day rolling window.
BACKUP_DIR = "/root/backups/gutlog"

# ----------------------------------------------------------------------
# Declarative migration plan
# ----------------------------------------------------------------------

# table -> list of (column, type-and-default clause)
ADD_COLUMNS = [
    ("prnmeds", "molecule", "TEXT DEFAULT ''"),
    ("prnmeds", "form", "TEXT DEFAULT ''"),
    ("prnmeds", "pack_size", "INTEGER DEFAULT 0"),
    ("prnmeds", "stock", "REAL DEFAULT 0"),
    ("prnmeds", "active", "INTEGER DEFAULT 1"),
    ("prnmeds", "scheduled", "INTEGER DEFAULT 0"),
    ("doses", "status", "TEXT DEFAULT 'TAKEN'"),
    ("doses", "med_id", "INTEGER"),
    ("doses", "sched_id", "INTEGER"),
    ("doses", "dose_text", "TEXT DEFAULT ''"),
    ("episodes", "bristol", "TEXT DEFAULT ''"),
]

CREATE_MED_SCHEDULE = """
CREATE TABLE IF NOT EXISTS med_schedule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  med_id     INTEGER NOT NULL,
  slot       TEXT    NOT NULL,
  dose_text  TEXT    DEFAULT '',
  with_food  TEXT    DEFAULT 'ANY',
  valid_from TEXT    NOT NULL,
  valid_to   TEXT    DEFAULT '',
  epoch      INTEGER DEFAULT 1,
  notes      TEXT    DEFAULT '',
  created    TEXT
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sched_med ON med_schedule (med_id)",
    "CREATE INDEX IF NOT EXISTS idx_sched_open ON med_schedule (valid_to)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_one_open "
    "ON med_schedule (med_id, slot) WHERE valid_to = ''",
    "CREATE INDEX IF NOT EXISTS idx_doses_day ON doses (day)",
    "CREATE INDEX IF NOT EXISTS idx_doses_sched ON doses (day, sched_id)",
]

# key -> default value. Only inserted when absent; never overwrites.
SETTINGS_DEFAULTS = [
    ("slot_times",
     '{"MORNING":"08:00","NOON":"14:00","EVENING":"20:00","NIGHT":"22:30"}'),
    ("slot_window_min", "120"),
    ("bp_flag_sys", "140"),
    ("bp_flag_dia", "90"),
    ("med_epoch", "1"),
    ("schema_version", VERSION),
]

REQUIRED_TABLES = ["prnmeds", "doses", "episodes", "vitals", "settings"]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class Report(object):
    """Collects what happened so the operator sees a real summary."""

    def __init__(self):
        self.lines = []
        self.changed = 0
        self.skipped = 0

    def did(self, msg):
        self.changed += 1
        self.lines.append("  [+] " + msg)

    def already(self, msg):
        self.skipped += 1
        self.lines.append("  [=] " + msg)

    def note(self, msg):
        self.lines.append("  [i] " + msg)

    def dump(self):
        for line in self.lines:
            print(line)


def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return set(r[0] for r in rows)


def column_names(conn, table):
    rows = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
    return set(r[1] for r in rows)


def index_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return set(r[0] for r in rows)


def backup_db(src_path, report):
    """Full copy via the sqlite3 backup API. Returns the backup path."""
    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    base = os.path.basename(src_path).replace(".db", "")
    dest = os.path.join(
        BACKUP_DIR, base + "_pre_v330_" + now_stamp() + ".db")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    size = os.path.getsize(dest)
    report.note("backup written: " + dest + " (" + str(size) + " bytes)")
    return dest


def preflight(conn):
    """Refuse to touch a database that is not GutLog v3.2.0-shaped."""
    present = table_names(conn)
    missing = [t for t in REQUIRED_TABLES if t not in present]
    if missing:
        return "missing expected tables: " + ", ".join(missing)
    return None


# ----------------------------------------------------------------------
# Migration steps
# ----------------------------------------------------------------------

def step_add_columns(conn, report, dry):
    for table, col, decl in ADD_COLUMNS:
        existing = column_names(conn, table)
        if col in existing:
            report.already(table + "." + col + " already present")
            continue
        stmt = "ALTER TABLE " + table + " ADD COLUMN " + col + " " + decl
        if dry:
            report.did("WOULD RUN: " + stmt)
        else:
            conn.execute(stmt)
            report.did(table + "." + col + " added")


def step_med_schedule(conn, report, dry):
    if "med_schedule" in table_names(conn):
        report.already("med_schedule already present")
    elif dry:
        report.did("WOULD CREATE TABLE med_schedule")
    else:
        conn.execute(CREATE_MED_SCHEDULE)
        report.did("med_schedule created")


def step_indexes(conn, report, dry):
    have = index_names(conn)
    for stmt in CREATE_INDEXES:
        # index name sits between "EXISTS " and " ON"
        head = stmt.split(" ON ")[0]
        name = head.split("EXISTS ")[-1].strip()
        if name in have:
            report.already("index " + name + " already present")
            continue
        if dry:
            report.did("WOULD CREATE index " + name)
        else:
            conn.execute(stmt)
            report.did("index " + name + " created")


def step_backfill_status(conn, report, dry):
    """Existing dose rows are historical PRN doses: TAKEN, unscheduled."""
    if "status" not in column_names(conn, "doses"):
        # dry run: the column would have been added earlier in this run
        report.note("doses.status not yet present; backfill deferred")
        return
    cur = conn.execute(
        "SELECT COUNT(*) FROM doses WHERE status IS NULL OR status = ''")
    n = cur.fetchone()[0]
    if n == 0:
        report.already("dose status backfill not needed")
        return
    if dry:
        report.did("WOULD SET status='TAKEN' on " + str(n) + " dose row(s)")
    else:
        conn.execute(
            "UPDATE doses SET status='TAKEN' "
            "WHERE status IS NULL OR status = ''")
        report.did("status='TAKEN' set on " + str(n) + " dose row(s)")


def step_backfill_med_id(conn, report, dry):
    """
    Normalise doses.medicine -> prnmeds.id by exact name match.

    Exact match only. No fuzzy logic: a wrong silent match in a
    medication log is worse than an unmatched row an operator can see.
    The free-text `medicine` column is retained as the label written at
    the time, so history stays readable if a med is later renamed.
    """
    if "med_id" not in column_names(conn, "doses"):
        report.note("doses.med_id not yet present; backfill deferred")
        return
    rows = conn.execute(
        "SELECT d.id, d.medicine, p.id FROM doses d "
        "LEFT JOIN prnmeds p ON p.name = d.medicine "
        "WHERE d.med_id IS NULL").fetchall()
    if not rows:
        report.already("med_id backfill not needed")
        return

    matched = [(r[2], r[0]) for r in rows if r[2] is not None]
    unmatched = [r[1] for r in rows if r[2] is None]

    if dry:
        report.did("WOULD LINK " + str(len(matched)) + " dose row(s) to prnmeds")
    elif matched:
        conn.executemany("UPDATE doses SET med_id=? WHERE id=?", matched)
        report.did(str(len(matched)) + " dose row(s) linked to prnmeds")

    if unmatched:
        report.note("UNMATCHED medicine names (left NULL, review manually): "
                    + ", ".join(sorted(set(unmatched))))


def step_recompute_scheduled(conn, report, dry):
    """prnmeds.scheduled is a denormalised convenience flag, never truth."""
    if dry:
        report.did("WOULD recompute prnmeds.scheduled")
        return
    if "scheduled" not in column_names(conn, "prnmeds"):
        report.note("prnmeds.scheduled not present; recompute skipped")
        return
    if "med_schedule" not in table_names(conn):
        report.note("med_schedule absent; recompute skipped")
        return
    conn.execute("UPDATE prnmeds SET scheduled = 0")
    conn.execute(
        "UPDATE prnmeds SET scheduled = 1 WHERE id IN "
        "(SELECT DISTINCT med_id FROM med_schedule WHERE valid_to = '')")
    n = conn.execute(
        "SELECT COUNT(*) FROM prnmeds WHERE scheduled = 1").fetchone()[0]
    report.did("prnmeds.scheduled recomputed (" + str(n) + " scheduled)")


def step_settings(conn, report, dry):
    have = set(r[0] for r in conn.execute("SELECT key FROM settings"))
    for key, val in SETTINGS_DEFAULTS:
        if key in have and key != "schema_version":
            report.already("setting '" + key + "' already set")
            continue
        if dry:
            report.did("WOULD SET setting '" + key + "'")
        else:
            # Explicit select-then-write rather than UPSERT: ON CONFLICT
            # needs SQLite 3.24+, which is not worth assuming here.
            if key in have:
                conn.execute("UPDATE settings SET value=? WHERE key=?",
                             (val, key))
            else:
                conn.execute("INSERT INTO settings (key, value) VALUES (?,?)",
                             (key, val))
            report.did("setting '" + key + "' = " + val)


STEPS = [
    ("add columns", step_add_columns),
    ("med_schedule table", step_med_schedule),
    ("indexes", step_indexes),
    ("backfill dose status", step_backfill_status),
    ("backfill dose med_id", step_backfill_med_id),
    ("recompute scheduled flag", step_recompute_scheduled),
    ("settings defaults", step_settings),
]


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="GutLog v3.3.0 schema migration (additive, idempotent)")
    ap.add_argument("--db", default=DEFAULT_DB, help="path to health3.db")
    ap.add_argument("--dry-run", action="store_true",
                    help="report intended changes, write nothing")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip backup (testing only)")
    args = ap.parse_args()

    print("=" * 62)
    print("GutLog migration -> v" + VERSION)
    print("db      : " + args.db)
    print("mode    : " + ("DRY RUN" if args.dry_run else "LIVE"))
    print("time    : " + now_iso())
    print("=" * 62)

    if not os.path.exists(args.db):
        print("FATAL: database not found: " + args.db)
        return 1

    report = Report()

    conn = sqlite3.connect(args.db)
    print("sqlite  : " + sqlite3.sqlite_version)
    problem = preflight(conn)
    if problem:
        conn.close()
        print("FATAL preflight: " + problem)
        print("Refusing to migrate a database of unexpected shape.")
        return 1
    conn.close()

    if not args.dry_run and not args.no_backup:
        backup_db(args.db, report)

    conn = sqlite3.connect(args.db)
    conn.isolation_level = None
    try:
        if not args.dry_run:
            conn.execute("BEGIN")
        for label, fn in STEPS:
            report.note("-- " + label)
            fn(conn, report, args.dry_run)
        if not args.dry_run:
            conn.execute("COMMIT")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        print("MIGRATION ERROR: " + str(exc))
        print("Transaction rolled back. Database unchanged.")
        report.dump()
        return 2
    conn.close()

    report.dump()
    print("-" * 62)
    print("changed: " + str(report.changed) + "   already-ok: "
          + str(report.skipped))
    if args.dry_run:
        print("DRY RUN complete. Nothing written.")
    else:
        print("Migration complete. Run test_migration_v330.py before restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

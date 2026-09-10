#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - Phase 3.5 migration: wearable ingest tables.

Creates health_metrics, health_workouts, health_raw.
Idempotent: safe to run repeatedly.
Takes a .bak copy of the DB before any DDL.

Python 3.9 compatible (server constraint).

Usage:
    python3 migrate_health_ingest.py [/path/to/fitlog.db]
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = "/root/fitlog/fitlog.db"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS health_metrics (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT    NOT NULL,
        metric      TEXT    NOT NULL,
        value       REAL,
        unit        TEXT,
        source      TEXT    NOT NULL,
        ingested_at TEXT    NOT NULL,
        UNIQUE (date, metric, source)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_health_metrics_date
        ON health_metrics (date)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_health_metrics_metric_date
        ON health_metrics (metric, date)
    """,
    """
    CREATE TABLE IF NOT EXISTS health_workouts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT    NOT NULL,
        start_ts    TEXT    NOT NULL,
        end_ts      TEXT,
        wtype       TEXT,
        duration_s  REAL,
        energy_kcal REAL,
        distance_km REAL,
        avg_hr      REAL,
        max_hr      REAL,
        source      TEXT    NOT NULL,
        ingested_at TEXT    NOT NULL,
        UNIQUE (start_ts, wtype, source)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_health_workouts_date
        ON health_workouts (date)
    """,
    """
    CREATE TABLE IF NOT EXISTS health_raw (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        received_at TEXT    NOT NULL,
        source      TEXT,
        n_bytes     INTEGER,
        payload     TEXT    NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_health_raw_received
        ON health_raw (received_at)
    """,
]

EXPECTED_TABLES = ("health_metrics", "health_workouts", "health_raw")


def backup(db_path):
    """Backup via the sqlite3 backup API (sqlite3 CLI absent on server)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = db_path + ".bak_" + stamp
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return dest


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB

    if not os.path.exists(db_path):
        print("FAIL: database not found: " + db_path)
        print("Pass the correct path as the first argument.")
        return 2

    print("DB      : " + db_path)
    bak = backup(db_path)
    print("Backup  : " + bak)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    before = set(
        r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )

    try:
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        print("FAIL: migration error: " + str(exc))
        print("Restore with: cp " + bak + " " + db_path)
        return 1

    after = set(
        r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )
    conn.close()

    created = sorted(after - before)
    print("Created : " + (", ".join(created) if created else "(none - already present)"))

    missing = [t for t in EXPECTED_TABLES if t not in after]
    if missing:
        print("FAIL: expected tables missing: " + ", ".join(missing))
        return 1

    print("OK: all ingest tables present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

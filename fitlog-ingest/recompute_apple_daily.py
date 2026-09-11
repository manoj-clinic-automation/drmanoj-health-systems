#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - one-off: rebuild daily figures from stored health_raw bodies.

Why
---
Two things left health_metrics holding figures nothing would ever
correct:

  * distance_km was written by the ios Health Webhook feed and then
    orphaned. Auto Export calls the metric walking_running_distance and
    nothing mapped that name, so the stale ios value simply sat there.

  * before patch_apple_records.py, a day was worth whatever the last
    payload said. Any day whose last delivery was a partial export is
    understated and will stay understated, because a fresh export only
    covers the last 48 hours.

Every body ever posted is kept in health_raw, so both are recoverable by
re-parsing. This script replays those bodies through the CURRENT parsers
into the record table, then recomputes health_metrics under rule S02.

It reproduces exactly what a fresh POST of the same body would compute:
  applewatch / manual   _APPLE_SUM, every feed considered (S02)
  healthconnect         HC_SUMMED, pinned to the HC Webhook feed, which
                        is what store_hc does on the live path

Safety
------
  * --dry-run works on a throwaway copy of the database and writes
    nothing. Run it first; it prints the same table.
  * a real run takes a .bak through the sqlite3 backup API first
    (no sqlite3 CLI on the server).
  * health_raw is only ever read.

Python 3.9 compatible.

Usage:
    python3 recompute_apple_daily.py --dry-run
    python3 recompute_apple_daily.py
    python3 recompute_apple_daily.py --from 2026-09-09 --to 2026-09-11
    python3 recompute_apple_daily.py --db /root/fitlog/fitlog.db
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

DEFAULT_FROM = "2026-09-09"
DEFAULT_TO = "2026-09-11"

# Dropped: not displayed, no F-rule reads it, and it meant active+basal
# from one feed and something else from another. Raw bodies are kept, so
# re-deriving it later costs a re-parse.
DEAD_METRICS = ("total_energy_kcal",)

USAGE = """usage: recompute_apple_daily.py [options]

  --db PATH        database to work on (default: health_ingest.DB_PATH)
  --from DATE      first date to rebuild, YYYY-MM-DD (default %s)
  --to DATE        last date to rebuild,  YYYY-MM-DD (default %s)
  --source NAME    source to rebuild, repeatable (default: every source
                   that appears in health_raw)
  --keep-total-energy   leave the dropped total_energy_kcal rows in place
  --dry-run        work on a copy, write nothing, print the same table
""" % (DEFAULT_FROM, DEFAULT_TO)


def _valid_date(text):
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except (ValueError, TypeError):
        return False
    return True


def backup(db_path):
    """sqlite3 backup API - there is no sqlite3 CLI on this server."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = db_path + ".bak_" + stamp
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return dest


def snapshot(conn, date_from, date_to):
    rows = conn.execute(
        "SELECT date, metric, source, value, unit FROM health_metrics "
        "WHERE date >= ? AND date <= ? ORDER BY date, metric, source",
        (date_from, date_to),
    ).fetchall()
    out = {}
    for date, metric, source, value, unit in rows:
        out[(date, metric, source)] = (value, unit)
    return out


def classify(hi, payload, source):
    """
    Route a stored body the same way api_ingest routes a live one.
    Returns (records, feed) where a record is
    (key, date, metric, value, unit, grain).
    """
    if isinstance(payload, dict) and payload.get("platform") == "ios":
        records, _workouts = hi.parse_ios_payload(payload)
        return ([(k, d, m, v, u, "interval") for k, d, m, v, u in records],
                "")
    if source == "healthconnect" and isinstance(payload, dict) \
            and "data" not in payload:
        records = hi.parse_hc_payload(payload)
        return ([(k, d, m, v, u, "interval") for k, d, m, v, u in records],
                "")
    records, _workouts, _skipped = hi.parse_apple_records(payload)
    return records, hi.FEED_HAE


def replay(hi, conn, date_from, date_to, sources, drop_dead):
    """
    Re-parse every stored body into health_hc_records.

    Records keep the receipt time of the body that carried them, so the
    ordering the recompute sees is the ordering the ingest saw.
    Returns (touched, n_bodies, n_records, n_unparseable).
    """
    hi._ensure_hc_record_columns(conn)
    cur = conn.cursor()

    bodies = cur.execute(
        "SELECT id, received_at, source, payload FROM health_raw "
        "ORDER BY id ASC"
    ).fetchall()

    touched = set()
    n_bodies = 0
    n_records = 0
    n_bad = 0
    for _rid, received_at, source, raw in bodies:
        source = (source or "").strip().lower() or "applewatch"
        if sources and source not in sources:
            continue
        try:
            payload = json.loads(raw or "")
        except ValueError:
            n_bad = n_bad + 1
            continue
        if not isinstance(payload, dict):
            n_bad = n_bad + 1
            continue

        records, feed = classify(hi, payload, source)
        used = False
        for key, date, metric, value, unit, grain in records:
            if date < date_from or date > date_to:
                continue
            if drop_dead and metric in DEAD_METRICS:
                continue
            cur.execute(
                "INSERT INTO health_hc_records "
                "(record_key, date, metric, value, unit, ingested_at, "
                " source, grain, feed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(record_key) DO UPDATE SET "
                "value=excluded.value, unit=excluded.unit, "
                "ingested_at=excluded.ingested_at, source=excluded.source, "
                "grain=excluded.grain, feed=excluded.feed",
                (key, date, metric, value, unit, received_at, source,
                 grain, feed),
            )
            touched.add((date, metric, source))
            n_records = n_records + 1
            used = True
        if used:
            n_bodies = n_bodies + 1

    return touched, n_bodies, n_records, n_bad


def recompute(hi, conn, touched):
    """Rewrite health_metrics for every touched date+metric+source."""
    cur = conn.cursor()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    written = 0
    for date, metric, source in sorted(touched):
        if source == "healthconnect":
            summed_set = hi.HC_SUMMED
            only_feed = ""
        else:
            summed_set = hi._APPLE_SUM
            only_feed = None
        got = hi._recompute_value(cur, date, metric, source, summed_set,
                                  only_feed=only_feed)
        if got is None:
            continue
        hi._write_daily(cur, date, metric, got[0], got[1], source, ts)
        written = written + 1
    return written


def purge_dead(conn):
    cur = conn.cursor()
    removed = {}
    for metric in DEAD_METRICS:
        n1 = cur.execute("SELECT COUNT(*) FROM health_metrics WHERE metric = ?",
                         (metric,)).fetchone()[0]
        cur.execute("DELETE FROM health_metrics WHERE metric = ?", (metric,))
        n2 = 0
        try:
            n2 = cur.execute("SELECT COUNT(*) FROM health_hc_records "
                             "WHERE metric = ?", (metric,)).fetchone()[0]
            cur.execute("DELETE FROM health_hc_records WHERE metric = ?",
                        (metric,))
        except sqlite3.OperationalError:
            n2 = 0
        removed[metric] = (n1, n2)
    return removed


def fmt(value):
    if value is None:
        return "-"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return "%.4f" % value


def report(before, after, date_from, date_to):
    keys = sorted(set(list(before.keys()) + list(after.keys())))
    keys = [k for k in keys if date_from <= k[0] <= date_to]
    print("")
    print("date        metric               source        before      after")
    print("-" * 68)
    changed = 0
    for key in keys:
        date, metric, source = key
        old = before.get(key)
        new = after.get(key)
        old_v = None if old is None else old[0]
        new_v = None if new is None else new[0]
        same = (old_v is not None and new_v is not None
                and abs(old_v - new_v) < 1e-9)
        mark = "   " if same else " * "
        if not same:
            changed = changed + 1
        print("%-11s %-20s %-12s %10s %10s%s"
              % (date, metric, source, fmt(old_v), fmt(new_v), mark))
    print("-" * 68)
    print(str(changed) + " of " + str(len(keys)) + " figures changed"
          + ("  (* marks a change)" if changed else ""))


def main():
    argv = sys.argv[1:]
    db_path = None
    date_from = DEFAULT_FROM
    date_to = DEFAULT_TO
    sources = set()
    dry = False
    drop_dead = True

    idx = 0
    while idx < len(argv):
        item = argv[idx]
        if item in ("-h", "--help"):
            print(USAGE)
            return 0
        if item == "--dry-run":
            dry = True
            idx = idx + 1
            continue
        if item == "--keep-total-energy":
            drop_dead = False
            idx = idx + 1
            continue
        if item in ("--db", "--from", "--to", "--source"):
            if idx + 1 >= len(argv):
                print("FAIL: " + item + " needs a value")
                return 2
            val = argv[idx + 1]
            if item == "--db":
                db_path = val
            elif item == "--from":
                date_from = val
            elif item == "--to":
                date_to = val
            else:
                sources.add(val.strip().lower())
            idx = idx + 2
            continue
        print("FAIL: unknown argument " + item)
        print(USAGE)
        return 2

    if not _valid_date(date_from) or not _valid_date(date_to):
        print("FAIL: --from/--to must be YYYY-MM-DD")
        return 2
    if date_from > date_to:
        print("FAIL: --from is after --to")
        return 2

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if db_path:
        os.environ["FITLOG_DB"] = db_path
    import health_ingest as hi

    if not hasattr(hi, "parse_apple_records"):
        print("FAIL: health_ingest.py is not patched. "
              "Run patch_apple_records.py first.")
        return 1

    db_path = db_path or hi.DB_PATH
    if not os.path.exists(db_path):
        print("FAIL: database not found: " + db_path)
        print("Pass the real path with --db.")
        return 2

    print("DB      : " + db_path)
    print("Range   : " + date_from + " .. " + date_to)
    print("Sources : " + (", ".join(sorted(sources)) if sources else "all"))
    print("Mode    : " + ("DRY RUN (working on a copy)" if dry else "LIVE"))

    work_dir = None
    if dry:
        work_dir = tempfile.mkdtemp(prefix="fitlog_recompute_")
        work_db = os.path.join(work_dir, "copy.db")
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(work_db)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    else:
        bak = backup(db_path)
        print("Backup  : " + bak)
        work_db = db_path

    conn = sqlite3.connect(work_db, timeout=15)
    try:
        before = snapshot(conn, date_from, date_to)
        touched, n_bodies, n_records, n_bad = replay(
            hi, conn, date_from, date_to, sources, drop_dead)
        written = recompute(hi, conn, touched)
        removed = {}
        if drop_dead:
            removed = purge_dead(conn)
        conn.commit()
        after = snapshot(conn, date_from, date_to)
    except Exception as exc:
        conn.rollback()
        conn.close()
        print("FAIL: " + str(exc))
        if not dry:
            print("Nothing was committed. Restore anyway with:")
            print("  cp " + bak + " " + db_path)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print("Bodies  : " + str(n_bodies) + " contributed records in range"
          + (("  (" + str(n_bad) + " unparseable, skipped)") if n_bad else ""))
    print("Records : " + str(n_records) + " replayed")
    print("Rebuilt : " + str(written) + " daily figures")
    for metric in sorted(removed):
        n1, n2 = removed[metric]
        print("Dropped : " + metric + " - " + str(n1)
              + " daily rows, " + str(n2) + " record rows")

    report(before, after, date_from, date_to)

    if dry:
        print("")
        print("Dry run. " + db_path + " untouched.")
        print("Copy used: " + work_db + "  (safe to delete)")
    else:
        print("")
        print("Committed. Rollback: cp " + bak + " " + db_path)
        print("Then: systemctl restart fitlog")
    return 0


if __name__ == "__main__":
    sys.exit(main())

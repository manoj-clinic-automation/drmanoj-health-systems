#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - rebuild the sleep record from the bodies already stored.

WHY
---
Fixing the parser does not fix what was stored by the old one. Rule S03 keys
a sleep sample by its own sleepStart and rebuilds the night from block spans,
but every night already in health_metrics was written before that existed and
carries whatever the old path produced. health_raw keeps every body verbatim,
so re-deriving costs a re-parse and nothing more.

WHAT IT CAN AND CANNOT RECOVER
------------------------------
It can recover anything that was DELIVERED and then lost or mangled on the way
into the database: a night split across blocks that collided on one record
slot, a night read as zero because `asleep` arrived as 0, block spans and stage
splits that were simply never stored.

It CANNOT recover what was never delivered. If Health Auto Export sent a
1 h 44 m fragment of a 5 h 41 m night, re-parsing that body yields the same
1 h 44 m. Those hours are on the phone, not on this server, and the only way to
get them is a fresh export covering that date with a wider window -- which,
with S03 deployed, will now land correctly instead of overwriting itself.

The report says which of the two happened, per date, so the difference is never
guessed at.

SAFETY
------
sqlite3.backup() before any write (there is no sqlite3 CLI on this server).
--dry-run does the whole job in a transaction and rolls it back, so the
before/after table can be read before anything is committed. Idempotent: a
block is keyed by its span, so re-running changes nothing.

Python 3.9.

    python3 reparse_sleep_blocks.py --dry-run
    python3 reparse_sleep_blocks.py
    python3 reparse_sleep_blocks.py --from 2026-09-01 --to 2026-09-15
"""
import argparse
import datetime
import importlib.util
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = "/root/fitlog/fitlog.db"
DEFAULT_MODULE = os.path.join(HERE, "health_ingest.py")


def load(path):
    spec = importlib.util.spec_from_file_location("health_ingest_reparse", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def backup(db_path):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = db_path + ".pre-sleepreparse-" + stamp
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    src.close()
    return dest, ok


def fmt(v, places=2):
    if v is None:
        return "-"
    return str(round(float(v), places))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--module", default=DEFAULT_MODULE)
    ap.add_argument("--source", default="applewatch")
    ap.add_argument("--from", dest="date_from", default="0000-00-00")
    ap.add_argument("--to", dest="date_to", default="9999-99-99")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("FATAL: no such database: " + args.db)
        return 2
    if not os.path.exists(args.module):
        print("FATAL: no such module: " + args.module)
        return 2

    hi = load(args.module)
    if not hasattr(hi, "parse_sleep_blocks") or not hasattr(hi, "sleep_night"):
        print("FATAL: " + args.module + " predates FITLOG_SLEEP_P1. "
              "Run patch_fitlog_sleep_p1.py first.")
        return 2

    print("=" * 74)
    print("FitLog -- rebuild the sleep record from stored bodies")
    print("db     : " + args.db)
    print("module : " + args.module)
    print("mode   : " + ("DRY RUN (rolled back)" if args.dry_run else "COMMIT"))
    print("=" * 74)

    if not args.dry_run:
        dest, ok = backup(args.db)
        print("backup : " + dest)
        print("integ  : " + ok)
        if ok != "ok":
            print("FATAL: backup failed its integrity check. Nothing written.")
            return 1

    conn = sqlite3.connect(args.db, timeout=30)
    hi._ensure_sleep_block_table(conn)

    # ---- what the record says now -------------------------------------
    before = {}
    for date, value in conn.execute(
            "SELECT date, value FROM health_metrics WHERE metric='sleep_hours' "
            "AND source=? AND date >= ? AND date <= ?",
            (args.source, args.date_from, args.date_to)).fetchall():
        before[date] = value

    # ---- re-parse every stored body -----------------------------------
    bodies = conn.execute(
        "SELECT id, received_at, source, payload FROM health_raw "
        "ORDER BY id ASC").fetchall()

    n_bodies = 0
    n_bad = 0
    n_blocks = 0
    touched = set()
    # Delivered spans per date, so the report can say whether a night was
    # ever delivered whole in the first place.
    delivered = {}

    for _rid, received_at, src, raw in bodies:
        src = (src or "").strip().lower() or "applewatch"
        if src != args.source:
            continue
        try:
            payload = json.loads(raw or "")
        except ValueError:
            n_bad = n_bad + 1
            continue
        if not isinstance(payload, dict):
            n_bad = n_bad + 1
            continue
        if payload.get("platform") == "ios" or "data" not in payload:
            continue

        blocks = hi.parse_sleep_blocks(payload)
        blocks = [b for b in blocks
                  if args.date_from <= (b.get("date") or "") <= args.date_to]
        if not blocks:
            continue
        n_bodies = n_bodies + 1
        n_blocks = n_blocks + len(blocks)
        # The body's own receipt time, so the ordering the rebuild sees is
        # the ordering the ingest saw.
        hi.store_sleep_blocks(conn, blocks, args.source,
                              received_at or hi._now())
        for b in blocks:
            touched.add(b["date"])
            delivered.setdefault(b["date"], []).append(b)

    # ---- rewrite the day figure from the blocks -----------------------
    after = {}
    nights = {}
    for date in sorted(touched):
        night = hi.sleep_night(conn, date, args.source)
        nights[date] = night
        if night is None or night.get("asleep_h") is None:
            continue
        conn.execute(
            "INSERT INTO health_metrics "
            "(date, metric, value, unit, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, metric, source) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (date, "sleep_hours", night["asleep_h"], "hr", args.source,
             hi._now()))
        after[date] = night["asleep_h"]

    # ---- report --------------------------------------------------------
    print("")
    print("bodies re-parsed : " + str(n_bodies)
          + "   unreadable: " + str(n_bad)
          + "   sleep blocks seen: " + str(n_blocks))
    print("")
    print("%-12s %8s %8s   %-17s %-17s %s"
          % ("date", "before", "after", "recorded from", "to", "blocks"))
    print("-" * 92)
    changed = 0
    for date in sorted(touched):
        night = nights.get(date)
        b = before.get(date)
        a = after.get(date)
        if b is None or a is None or abs(float(a) - float(b)) > 0.0005:
            changed = changed + 1
        start = (night or {}).get("start_ts") or "-"
        end = (night or {}).get("end_ts") or "-"
        print("%-12s %8s %8s   %-17s %-17s %s"
              % (date, fmt(b), fmt(a), start[11:] if len(start) > 11 else start,
                 end[11:] if len(end) > 11 else end,
                 str((night or {}).get("blocks", 0))))
    print("-" * 92)
    print("dates touched: " + str(len(touched)) + "   values changed: "
          + str(changed))

    # ---- was the night ever delivered whole? ---------------------------
    print("")
    print("Delivered span against recorded sleep -- a night whose blocks only")
    print("cover a little more than the sleep they report was never delivered")
    print("whole, and no re-parse can recover the rest. Re-export that date")
    print("from the phone with a wider window instead.")
    print("")
    for date in sorted(touched):
        night = nights.get(date)
        if not night:
            continue
        span = night.get("in_bed_h")
        asleep = night.get("asleep_h")
        awake = night.get("awake_h")
        note = ""
        if span is not None and asleep is not None:
            unaccounted = float(span) - float(asleep) - float(awake or 0)
            if abs(unaccounted) < 0.05:
                note = ("the blocks account for the whole recorded span -- "
                        "nothing was lost in here; if the night was longer, "
                        "it was never sent")
            else:
                note = ("%s h of the recorded span is outside the blocks"
                        % fmt(unaccounted))
        print("  %s  asleep %s h, awake %s h, span %s h"
              % (date, fmt(asleep), fmt(awake), fmt(span)))
        if note:
            print("      " + note)

    if args.dry_run:
        conn.rollback()
        print("")
        print("DRY RUN -- rolled back. Nothing was written.")
    else:
        conn.commit()
        print("")
        print("committed.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

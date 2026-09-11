#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: iOS Health Webhook payload support.

Health Webhook on iOS posts the same family of snake_case arrays as the
Android app, but with different value keys and, critically, UTC
timestamps.

    "start_time": "2026-09-08T18:30:00.000Z"   ==  2026-09-09 00:00 IST

Taking the first ten characters of that string files the record a full
day early. Every metric would land on the wrong date, and the error is
silent. This patch converts to IST before deriving the date.

Also generalises the record store so it is no longer hardcoded to the
healthconnect source, and routes by payload SHAPE rather than by source
name, so an iOS payload arriving as source=applewatch is recognised.

Compile-checks before writing, takes a .bak, idempotent.
Python 3.9 compatible.

Usage:
    python3 patch_ios_payload.py
    python3 patch_ios_payload.py --dry-run
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

# ---------------------------------------------------------------- anchors

A1_OLD = '''HC_ARRAY_MAP = {'''

A1_NEW = '''# iOS Health Webhook arrays. Same idea as Android, different value keys.
# basal_metabolic_rate is deliberately excluded - hundreds of records a
# day and no use in any rule.
IOS_ARRAY_MAP = {
    "steps": ("steps", "count", "count"),
    "distance": ("distance_km", "meters", "km"),
    "active_calories": ("active_energy_kcal", "kilocalories", "kcal"),
    "total_calories": ("total_energy_kcal", "kilocalories", "kcal"),
    "resting_heart_rate": ("resting_hr", "bpm", "count/min"),
    "heart_rate": ("hr", "bpm", "count/min"),
    "heart_rate_variability": ("hrv_ms", "milliseconds", "ms"),
}

# Summed across the day; everything else is averaged.
IOS_SUMMED = ("steps", "distance_km", "active_energy_kcal",
              "total_energy_kcal")

IST_OFFSET_MINUTES = 330


def _to_ist_date(raw):
    """
    Derive the IST calendar date from an ISO timestamp.

    iOS Health Webhook emits UTC with a Z suffix. 18:30Z is 00:00 IST
    the FOLLOWING day, so slicing the first ten characters files the
    record a day early. Offsets already expressed as +05:30 are taken
    at face value.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        if text.endswith("Z"):
            base = text[:-1].split(".")[0]
            stamp = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
            stamp = stamp + timedelta(minutes=IST_OFFSET_MINUTES)
            return stamp.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return _parse_date(text)


def parse_ios_payload(payload):
    """
    Returns (records, workouts).
    records: list of (record_key, date, metric, value, unit)
    """
    records = []
    workouts = []
    if not isinstance(payload, dict):
        return records, workouts

    for array_name, spec in IOS_ARRAY_MAP.items():
        metric, value_key, unit = spec
        entries = payload.get(array_name)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            stamp = item.get("start_time") or item.get("time")
            date = _to_ist_date(stamp)
            if not date:
                continue
            val = _num(item.get(value_key))
            if val is None:
                continue
            if metric == "distance_km":
                val = val / 1000.0
            end = item.get("end_time") or ""
            key = "|".join(["ios", metric, str(stamp).strip(), str(end).strip()])
            records.append((key, date, metric, val, unit))

    # activity_rings already carries a local calendar date - trust it.
    for ring in payload.get("activity_rings") or []:
        if not isinstance(ring, dict):
            continue
        date = _parse_date(str(ring.get("date") or ""))
        if not date:
            continue
        for field, metric, unit in (
            ("stand_hours", "stand_hours", "count"),
            ("exercise_minutes", "exercise_minutes", "min"),
            ("move_kilocalories", "move_energy_kcal", "kcal"),
        ):
            val = _num(ring.get(field))
            if val is None:
                continue
            key = "|".join(["ios", metric, date])
            records.append((key, date, metric, val, unit))

    for ex in payload.get("exercise") or []:
        if not isinstance(ex, dict):
            continue
        start = ex.get("start_time")
        date = _to_ist_date(start)
        if not date:
            continue
        dist = _num(ex.get("distance_meters"))
        workouts.append((
            date,
            str(start).strip(),
            str(ex.get("end_time") or "").strip() or None,
            str(ex.get("type") or "unknown"),
            _num(ex.get("duration_seconds")),
            _num(ex.get("kilocalories")),
            (dist / 1000.0) if dist is not None else None,
            None,
            None,
        ))

    return records, workouts


HC_ARRAY_MAP = {'''

# -- generalise the record store away from a hardcoded source -------------

A2_OLD = '''def store_hc(conn, records, raw_text):'''

A2_NEW = '''def store_hc(conn, records, raw_text, source="healthconnect",
             summed=None, workouts=None):'''

A3_OLD = '''        (ts, "healthconnect", len(raw_text or ""), raw_text or ""),'''

A3_NEW = '''        (ts, source, len(raw_text or ""), raw_text or ""),'''

A4_OLD = '''    for date, metric in touched:
        if metric in HC_SUMMED:'''

A4_NEW = '''    for row in (workouts or []):
        cur.execute(
            "INSERT INTO health_workouts "
            "(date, start_ts, end_ts, wtype, duration_s, energy_kcal, "
            " distance_km, avg_hr, max_hr, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(start_ts, wtype, source) DO UPDATE SET "
            "end_ts=excluded.end_ts, duration_s=excluded.duration_s, "
            "energy_kcal=excluded.energy_kcal, "
            "distance_km=excluded.distance_km, "
            "ingested_at=excluded.ingested_at",
            tuple(row) + (source, ts),
        )

    summed_set = HC_SUMMED if summed is None else summed
    for date, metric in touched:
        if metric in summed_set:'''

A5_OLD = '''            "VALUES (?, ?, ?, ?, 'healthconnect', ?) "'''

A5_NEW = '''            "VALUES (?, ?, ?, ?, '" + source + "', ?) "'''

# -- route by payload shape, not by source name ---------------------------

A6_OLD = '''    is_hc = (source == "healthconnect" and "data" not in payload)

    if is_hc:
        records = parse_hc_payload(payload)
        conn = _connect()
        try:
            new_rows, updated_rows, dates = store_hc(conn, records, raw_text)'''

A6_NEW = '''    # Route by payload SHAPE. Health Webhook on iOS posts the same array
    # style as the Android app but under source=applewatch, so keying off
    # the source name alone silently drops it into the Apple parser and
    # stores nothing.
    is_ios = isinstance(payload, dict) and payload.get("platform") == "ios"
    is_hc = (source == "healthconnect" and "data" not in payload)

    if is_ios:
        records, workouts = parse_ios_payload(payload)
        conn = _connect()
        try:
            new_rows, updated_rows, dates = store_hc(
                conn, records, raw_text, source=source,
                summed=IOS_SUMMED, workouts=workouts,
            )
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "source": source,
            "format": "ios_health_webhook",
            "records_seen": len(records),
            "records_new": new_rows,
            "records_updated": updated_rows,
            "workouts_stored": len(workouts),
            "dates": dates,
        }), 200

    if is_hc:
        records = parse_hc_payload(payload)
        conn = _connect()
        try:
            new_rows, updated_rows, dates = store_hc(conn, records, raw_text)'''

A7_OLD = '''from datetime import datetime, timedelta'''
A7_NEW = '''from datetime import datetime, timedelta'''

EDITS = [
    ("ios parser + ist conversion", A1_OLD, A1_NEW),
    ("generalise store signature", A2_OLD, A2_NEW),
    ("generalise raw source", A3_OLD, A3_NEW),
    ("workouts + summed set", A4_OLD, A4_NEW),
    ("generalise metric source", A5_OLD, A5_NEW),
    ("route by payload shape", A6_OLD, A6_NEW),
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

    if "parse_ios_payload" in src:
        print("SKIP: iOS support already present.")
        return 0
    if "parse_hc_payload" not in src:
        print("FAIL: HC support missing. Run patch_hc_support.py first.")
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
    print("")
    print("Next:")
    print("  python3 test_health_ingest.py")
    print("  python3 test_hc_ingest.py")
    print("  python3 test_ios_ingest.py")
    print("  systemctl restart fitlog")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

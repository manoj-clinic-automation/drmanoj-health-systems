#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: HC Webhook (Health Connect) support.

Adds two things to health_ingest.py:

  1. A separate URL-borne token for the healthconnect feed only.
     HC Webhook cannot send custom headers, so its token rides in ?k=.
     That token is POST-only, healthconnect-only, and cannot read
     anything. The main bearer token is unchanged and stays header-only.

  2. Record-level ingest for HC Webhook's payload shape.
     HC sends individual interval records, incrementally, with retries.
     Each record is stored once under a unique key; daily totals in
     health_metrics are then RECOMPUTED as the sum of stored records.
     That is correct under partial batches and idempotent under retries.

Compile-checks before writing, takes a .bak, idempotent.
Python 3.9 compatible.

Usage:
    python3 patch_hc_support.py            # /root/fitlog/health_ingest.py
    python3 patch_hc_support.py --dry-run
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

# ---------------------------------------------------------------- anchors

A1_OLD = '''def _authorised(req):'''

A1_NEW = '''def _load_hc_token():
    """
    Separate token for the healthconnect feed only.

    HC Webhook has no custom-header field, so this one travels in the URL
    as ?k=<token>. It is deliberately scoped: POST only, source
    healthconnect only, no read access to any endpoint. Rotate it
    independently of the main bearer token.
    """
    env_token = os.environ.get("FITLOG_HC_TOKEN")
    if env_token:
        return env_token.strip()
    try:
        with open(TOKEN_FILE, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() == "FITLOG_HC_TOKEN":
                    return val.strip().strip('"').strip("'")
    except IOError:
        return None
    return None


def _authorised_hc(req):
    """URL-token auth, valid only for POSTing healthconnect data."""
    expected = _load_hc_token()
    if not expected:
        return False
    supplied = (req.args.get("k") or "").strip()
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _authorised(req):'''

# -- HC parsing and record-level storage, appended before the routes ------

A2_OLD = '''# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------'''

A2_NEW = '''# --------------------------------------------------------------------------
# HC Webhook (Health Connect) - record-level ingest
# --------------------------------------------------------------------------

# HC Webhook posts one JSON object: timestamp, app_version, plus a
# snake_case array per data type. Records are intervals, delivered
# incrementally, with retries. Summed per day after dedup.

HC_ARRAY_MAP = {
    "steps": ("steps", "count"),
    "distance": ("distance_km", "km"),
    "active_calories_burned": ("active_energy_kcal", "kcal"),
    "total_calories_burned": ("total_energy_kcal", "kcal"),
    "floors_climbed": ("flights", "count"),
}

# Summed across the day. Anything not listed here is averaged instead.
HC_SUMMED = ("steps", "distance_km", "active_energy_kcal",
             "total_energy_kcal", "flights")

_HC_START_KEYS = ("start_time", "startTime", "time", "date", "start")
_HC_VALUE_KEYS = ("count", "value", "qty", "steps", "distance", "energy",
                  "kilocalories", "meters", "amount")


def _hc_first(record, keys):
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


_HC_KM_UNITS = ("km", "kilometer", "kilometers", "kilometre", "kilometres")


def _hc_value(record, metric):
    raw = _hc_first(record, _HC_VALUE_KEYS)
    if isinstance(raw, dict):
        raw = _hc_first(raw, _HC_VALUE_KEYS)
    val = _num(raw)
    if val is None:
        return None
    # Health Connect's canonical unit for Distance is metres. Convert
    # unconditionally unless the record explicitly says kilometres.
    # A magnitude heuristic is wrong here: short interval records are
    # the normal case for record-level ingest, and 50 m must not become
    # 50 km.
    if metric == "distance_km":
        unit = str(record.get("unit") or record.get("units") or "").lower()
        if unit not in _HC_KM_UNITS:
            val = val / 1000.0
    return val


def parse_hc_payload(payload):
    """
    Returns a list of (record_key, date, metric, value, unit).
    record_key is stable across redeliveries of the same record.
    """
    out = []
    if not isinstance(payload, dict):
        return out

    for array_name, spec in HC_ARRAY_MAP.items():
        metric, unit = spec
        records = payload.get(array_name)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            start = _hc_first(record, _HC_START_KEYS)
            date = _parse_date(start if isinstance(start, str) else "")
            if not date:
                continue
            value = _hc_value(record, metric)
            if value is None:
                continue
            end = record.get("end_time") or record.get("endTime") or ""
            # The key identifies the INTERVAL, never the value. HC
            # Webhook redelivers a growing in-progress interval with an
            # updated count; including the value would let both land and
            # SUM would add them together, inflating a rule-bearing
            # metric. Interval identity plus upsert gives last-write-wins,
            # which is idempotent under retry and correct under update.
            key = "|".join([metric, str(start).strip(), str(end).strip()])
            out.append((key, date, metric, value, unit))
    return out


def store_hc(conn, records, raw_text):
    """
    Insert records under a unique key, then recompute daily totals for
    every affected date. Correct under partial batches, idempotent under
    redelivery.
    """
    ts = _now()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO health_raw (received_at, source, n_bytes, payload) "
        "VALUES (?, ?, ?, ?)",
        (ts, "healthconnect", len(raw_text or ""), raw_text or ""),
    )

    new_rows = 0
    updated_rows = 0
    touched = set()
    for key, date, metric, value, unit in records:
        prior = cur.execute(
            "SELECT value FROM health_hc_records WHERE record_key = ?",
            (key,),
        ).fetchone()
        cur.execute(
            "INSERT INTO health_hc_records "
            "(record_key, date, metric, value, unit, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_key) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (key, date, metric, value, unit, ts),
        )
        if prior is None:
            new_rows = new_rows + 1
        elif prior[0] != value:
            updated_rows = updated_rows + 1
        touched.add((date, metric))

    for date, metric in touched:
        if metric in HC_SUMMED:
            agg = "SUM(value)"
        else:
            agg = "AVG(value)"
        row = cur.execute(
            "SELECT " + agg + ", unit FROM health_hc_records "
            "WHERE date = ? AND metric = ?",
            (date, metric),
        ).fetchone()
        if row is None or row[0] is None:
            continue
        cur.execute(
            "INSERT INTO health_metrics "
            "(date, metric, value, unit, source, ingested_at) "
            "VALUES (?, ?, ?, ?, 'healthconnect', ?) "
            "ON CONFLICT(date, metric, source) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (date, metric, row[0], row[1], ts),
        )

    conn.commit()
    return new_rows, updated_rows, sorted(set(d for d, _ in touched))


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------'''

# -- route: accept URL token for healthconnect, branch to HC parser -------

A3_OLD = '''    if not _authorised(request):
        return jsonify({"ok": False, "error": "unauthorised"}), 401

    source = (request.args.get("source") or "").strip().lower()
    if source not in ALLOWED_SOURCES:'''

A3_NEW = '''    source = (request.args.get("source") or "").strip().lower()

    # healthconnect may authenticate by URL token, since HC Webhook
    # cannot send headers. Every other source stays header-only.
    ok_auth = _authorised(request)
    if not ok_auth and source == "healthconnect":
        ok_auth = _authorised_hc(request)
    if not ok_auth:
        return jsonify({"ok": False, "error": "unauthorised"}), 401

    if source not in ALLOWED_SOURCES:'''

A4_OLD = '''    metric_rows, workout_rows, skipped = parse_payload(payload)

    conn = _connect()
    try:
        store(conn, metric_rows, workout_rows, source, raw_text)
        dates = sorted(set(r[0] for r in metric_rows))
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "source": source,
        "metrics_stored": len(metric_rows),
        "workouts_stored": len(workout_rows),
        "dates": dates,
        "skipped_metrics": skipped,
    }), 200'''

A4_NEW = '''    # HC Webhook uses a different payload shape: snake_case arrays of
    # interval records rather than daily totals under data.metrics.
    is_hc = (source == "healthconnect" and "data" not in payload)

    if is_hc:
        records = parse_hc_payload(payload)
        conn = _connect()
        try:
            new_rows, updated_rows, dates = store_hc(conn, records, raw_text)
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "source": source,
            "format": "hc_webhook",
            "records_seen": len(records),
            "records_new": new_rows,
            "records_updated": updated_rows,
            "dates": dates,
        }), 200

    metric_rows, workout_rows, skipped = parse_payload(payload)

    conn = _connect()
    try:
        store(conn, metric_rows, workout_rows, source, raw_text)
        dates = sorted(set(r[0] for r in metric_rows))
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "source": source,
        "metrics_stored": len(metric_rows),
        "workouts_stored": len(workout_rows),
        "dates": dates,
        "skipped_metrics": skipped,
    }), 200'''

EDITS = [
    ("hc token loader + url auth", A1_OLD, A1_NEW),
    ("hc parser + record store", A2_OLD, A2_NEW),
    ("route auth branch", A3_OLD, A3_NEW),
    ("route format branch", A4_OLD, A4_NEW),
]

DDL_RECORDS = """
CREATE TABLE IF NOT EXISTS health_hc_records (
    record_key  TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL,
    unit        TEXT,
    ingested_at TEXT NOT NULL
)
"""

DDL_IDX = """
CREATE INDEX IF NOT EXISTS ix_hc_records_date_metric
    ON health_hc_records (date, metric)
"""


def migrate_records_table(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(DDL_RECORDS)
    conn.execute(DDL_IDX)
    conn.commit()
    ok = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='health_hc_records'"
    ).fetchone()
    conn.close()
    return bool(ok)


def main():
    # Parse flags by consuming their VALUES too. Filtering only on a
    # leading '--' leaves the flag's argument behind and it gets read as
    # a positional - which is how an earlier build tried to parse the
    # live SQLite database as Python source.
    VALUE_FLAGS = ("--db",)
    dry = False
    positional = []
    flags = {}
    argv = sys.argv[1:]
    idx = 0
    while idx < len(argv):
        item = argv[idx]
        if item in VALUE_FLAGS:
            if idx + 1 >= len(argv):
                print("FAIL: " + item + " needs a value")
                return 2
            flags[item] = argv[idx + 1]
            idx = idx + 2
            continue
        if item == "--dry-run":
            dry = True
            idx = idx + 1
            continue
        if item.startswith("--"):
            print("FAIL: unknown flag " + item)
            return 2
        positional.append(item)
        idx = idx + 1

    target = positional[0] if positional else TARGET
    db_path = flags.get("--db")
    if db_path is None:
        db_path = os.path.join(os.path.dirname(target) or ".", "fitlog.db")

    if not target.endswith(".py"):
        print("FAIL: target must be a .py file, got: " + target)
        print("Did you mean:  python3 patch_hc_support.py --db " + target)
        return 2

    if not os.path.exists(target):
        print("FAIL: not found: " + target)
        return 2
    print("Target  : " + target)

    with open(target, "r") as fh:
        src = fh.read()

    if "parse_hc_payload" in src:
        print("SKIP: HC support already present. Nothing to do.")
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

    if not os.path.exists(db_path):
        print("WARN: db not found at " + db_path + " - pass --db <path>")
        print("      table health_hc_records NOT created")
    else:
        made = migrate_records_table(db_path)
        print("Table   : health_hc_records " + ("ok" if made else "FAILED"))

    print("Patched : ok")
    print("")
    print("Now add the healthconnect token:")
    print("  python3 -c \"import secrets; print('FITLOG_HC_TOKEN=' + secrets.token_urlsafe(24))\" >> /root/fitlog/ingest.env")
    print("  cat /root/fitlog/ingest.env")
    print("")
    print("Then: python3 test_health_ingest.py  &&  systemctl restart fitlog")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

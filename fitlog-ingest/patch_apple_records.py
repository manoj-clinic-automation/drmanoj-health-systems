#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: record-level Auto Export ingest,
distance restored, total_energy_kcal dropped.

WHY
---
1. PARTIAL EXPORTS SILENTLY SHRINK A DAY.
   parse_payload() aggregates the samples inside ONE payload and store()
   upserts that single figure on (date, metric, source). The day is
   therefore worth whatever the last payload said. That is only safe
   because Health Auto Export happens to post a whole-day rollup three
   minutes after the per-hour batch. Any truncated batch, any
   incremental export, any retry of a half-day window overwrites a
   fuller day with a smaller one, and nothing anywhere says so. The
   per-hour export of 2026-09-11 replayed through the pre-aggregation
   parser stored steps = 4.61 against a real 4914.

   Fixed by construction: every sample becomes a record with a dedup
   key, exactly as the HC and ios paths already do, and the daily figure
   in health_metrics is RECOMPUTED from the stored records. Records
   accumulate, so a partial batch can only ever add to a day.

2. RULE S02 DAY-GRAIN PRECEDENCE (new, documented in the module).
   Auto Export sends the same metric at two grains: per-hour interval
   samples, and a single whole-day rollup sample. Summing both
   double-counts the day; letting either replace the other loses data
   in one of the two arrival orders. So, per date + metric + source +
   feed:

       interval records  ->  SUM   (dedup by interval identity)
       day records       ->  the most recent one replaces the previous
       the day's value   ->  the GREATER of those two

   A partial interval batch cannot undercut a complete rollup, and a
   stale rollup cannot cap a fuller interval set. Feeds are never summed
   together either - they are alternative views of one quantity, so the
   fuller feed wins. This is what lets the retired ios records and the
   Auto Export records for 2026-09-09..11 coexist without inflating a
   rule-bearing metric.

   Known and accepted: a downward correction lands only at its own
   grain. A revised rollup cannot pull a day below the interval records
   already stored for it. Apple revises a day downwards far less often
   than an export is truncated, and silent under-reporting of a
   rule-bearing metric is the worse failure.

3. DISTANCE WAS ORPHANED.
   Auto Export calls it walking_running_distance. Nothing mapped that
   name, so distance_km held whatever the retired ios feed last wrote.
   Mapped now, with mi/m/km normalisation.

4. total_energy_kcal DROPPED.
   Not displayed, no F-rule reads it, and it meant two different things
   depending on the feed. METRIC_MAP also mapped HC's
   total_calories_burned onto active_energy_kcal - a different quantity
   (it includes BMR) - so that entry goes too. Raw bodies are retained
   in health_raw, so nothing is unrecoverable.

Does not touch the rule engine. No F-rule reads an ingested metric.

Compile-checks before writing, .bak with a microsecond stamp, idempotent.
Python 3.9 compatible.

Usage:
    python3 patch_apple_records.py --dry-run
    python3 patch_apple_records.py [target]
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

# ---------------------------------------------------------------- anchors

# -- 1. module docstring: name the new rule -------------------------------

A1_OLD = '''    S01 Source Precedence
      - applewatch wins for every metric it reports
      - healthconnect fills a date only when applewatch has no record
      - values are never summed or averaged across sources
'''

A1_NEW = '''    S01 Source Precedence
      - applewatch wins for every metric it reports
      - healthconnect fills a date only when applewatch has no record
      - values are never summed or averaged across sources

    S02 Day-Grain Precedence
      - every sample is stored as a record under a dedup key; the daily
        figure in health_metrics is recomputed from those records
      - within one feed: interval records SUM, the latest whole-day
        rollup replaces the previous one, and the day is the GREATER of
        the two
      - across feeds of the same source: the greater, never the sum
      - a partial batch can only add to a day, never shrink it
'''

# -- 2. METRIC_MAP: distance in, total calories out -----------------------

A2_OLD = '''    "weight_body_mass": "weight_kg",
    "blood_oxygen_saturation": "spo2_pct",
    # Health Connect / generic aliases
    "steps": "steps",
    "total_calories_burned": "active_energy_kcal",
    "resting_heart_rate_bpm": "resting_hr",
    "sleep_session": "sleep_hours",
}'''

A2_NEW = '''    "weight_body_mass": "weight_kg",
    "blood_oxygen_saturation": "spo2_pct",
    # Distance. Orphaned when the ios feed was retired - Auto Export
    # calls it walking_running_distance and nothing mapped that name, so
    # distance_km kept whatever the dead feed last wrote.
    "walking_running_distance": "distance_km",
    "distance_walking_running": "distance_km",
    # Health Connect / generic aliases
    "steps": "steps",
    # total_calories_burned deliberately absent: it is active + basal,
    # not active, and mapping it onto active_energy_kcal inflated that
    # metric by the whole basal rate. See total_energy_kcal, dropped.
    "resting_heart_rate_bpm": "resting_hr",
    "sleep_session": "sleep_hours",
}'''

# -- 3. unit normalisation for distance -----------------------------------

A3_OLD = '''_KJ_UNITS = ("kj", "kilojoule", "kilojoules")
_KCAL_UNITS = ("kcal", "cal", "calorie", "calories", "kilocalorie",
               "kilocalories")


def _apple_convert(canonical, value, unit):
    """Normalise a sample to the unit its canonical name promises."""
    u = str(unit or "").strip().lower()
    if canonical in _ENERGY_METRICS:
        if u in _KJ_UNITS:
            return value / _KJ_PER_KCAL, "kcal"
        if u in _KCAL_UNITS:
            return value, "kcal"
    return value, unit'''

A3_NEW = '''_KJ_UNITS = ("kj", "kilojoule", "kilojoules")
_KCAL_UNITS = ("kcal", "cal", "calorie", "calories", "kilocalorie",
               "kilocalories")

# Auto Export reports distance in whatever the phone's units setting
# says. The canonical name promises km, so the value is converted.
_KM_UNITS = ("km", "kilometer", "kilometers", "kilometre", "kilometres")
_MI_UNITS = ("mi", "mile", "miles")
_M_UNITS = ("m", "meter", "meters", "metre", "metres")
_KM_PER_MILE = 1.609344


def _apple_convert(canonical, value, unit):
    """Normalise a sample to the unit its canonical name promises."""
    u = str(unit or "").strip().lower()
    if canonical in _ENERGY_METRICS:
        if u in _KJ_UNITS:
            return value / _KJ_PER_KCAL, "kcal"
        if u in _KCAL_UNITS:
            return value, "kcal"
    if canonical == "distance_km":
        if u in _KM_UNITS:
            return value, "km"
        if u in _MI_UNITS:
            return value * _KM_PER_MILE, "km"
        if u in _M_UNITS:
            return value / 1000.0, "km"
    return value, unit'''

# -- 4. split parse_payload into samples + two views ----------------------

A4_OLD = '''def parse_payload(payload):
    """
    Normalise a Health Auto Export style body into metric and workout rows.
    Returns (metric_rows, workout_rows, skipped_metric_names).
    """
    metrics_out = []
    workouts_out = []
    skipped = set()

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    for entry in data.get("metrics") or []:
        if not isinstance(entry, dict):
            continue
        raw_name = (entry.get("name") or "").strip().lower()
        canonical = METRIC_MAP.get(raw_name)
        if not canonical:
            if raw_name:
                skipped.add(raw_name)
            continue
        unit = entry.get("units") or ""
        for point in entry.get("data") or []:
            if not isinstance(point, dict):
                continue
            date = _parse_date(point.get("date"))
            if not date:
                continue
            if canonical == "sleep_hours":
                value = _sleep_hours(point)
            else:
                value = _num(point.get("qty"))
                if value is None:
                    value = _num(point.get("Avg"))
            if value is None:
                continue
            # Bind to a fresh name: `unit` is the entry-level unit and is
            # reused by every point in this entry. Rebinding it converts
            # the first sample, then makes every later sample look like
            # it is already in kcal and pass through unconverted.
            value, point_unit = _apple_convert(canonical, value, unit)
            metrics_out.append((date, canonical, value, point_unit))

    # Without this the last sample of the day silently wins.
    metrics_out = _apple_aggregate(metrics_out)

    for wk in data.get("workouts") or []:'''

A4_NEW = '''# Which inbound shape wrote a record. Rows that predate the column
# default to "" - HC Webhook and the retired ios Health Webhook - and
# keep the arithmetic they always had. Auto Export rows are tagged so
# they are never summed together with the older feeds for the same day.
FEED_HAE = "hae"


def _apple_time(raw):
    """
    Time-of-day from an Auto Export timestamp, "" when unreadable.

    "2026-09-11 05:00:00 +0530" and "2026-09-11T05:00:00" both put
    HH:MM:SS at offset 11.
    """
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if len(text) < 19:
        return ""
    part = text[11:19]
    if len(part) != 8 or part[2] != ":" or part[5] != ":":
        return ""
    return part


def _apple_samples(payload):
    """
    Every Auto Export sample, unaggregated, with its time of day.

    Single source of truth for this payload shape. parse_payload()
    collapses these into a day view; parse_apple_records() turns them
    into dedupable records. Returns (samples, workouts, skipped) where a
    sample is (date, time, canonical, value, unit).
    """
    samples = []
    workouts_out = []
    skipped = set()

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    for entry in data.get("metrics") or []:
        if not isinstance(entry, dict):
            continue
        raw_name = (entry.get("name") or "").strip().lower()
        canonical = METRIC_MAP.get(raw_name)
        if not canonical:
            if raw_name:
                skipped.add(raw_name)
            continue
        unit = entry.get("units") or ""
        for point in entry.get("data") or []:
            if not isinstance(point, dict):
                continue
            stamp = point.get("date")
            date = _parse_date(stamp)
            if not date:
                continue
            if canonical == "sleep_hours":
                value = _sleep_hours(point)
            else:
                value = _num(point.get("qty"))
                if value is None:
                    value = _num(point.get("Avg"))
            if value is None:
                continue
            # Bind to a fresh name: `unit` is the entry-level unit and is
            # reused by every point in this entry. Rebinding it converts
            # the first sample, then makes every later sample look like
            # it is already in kcal and pass through unconverted.
            value, point_unit = _apple_convert(canonical, value, unit)
            samples.append((date, _apple_time(stamp), canonical,
                            value, point_unit))

    for wk in data.get("workouts") or []:'''

A5_OLD = '''            _num(max_hr.get("qty") if isinstance(max_hr, dict) else max_hr),
        ))

    return metrics_out, workouts_out, sorted(skipped)'''

A5_NEW = '''            _num(max_hr.get("qty") if isinstance(max_hr, dict) else max_hr),
        ))

    return samples, workouts_out, sorted(skipped)


def parse_payload(payload):
    """
    Normalise a Health Auto Export style body into metric and workout rows.
    Returns (metric_rows, workout_rows, skipped_metric_names).

    In-memory day view of one payload. Correct only for a payload that
    covers the whole day, which is why the ingest route stores records
    instead - see parse_apple_records and rule S02.
    """
    samples, workouts_out, skipped = _apple_samples(payload)
    rows = [(d, m, v, u) for d, t, m, v, u in samples]
    return _apple_aggregate(rows), workouts_out, skipped


def _apple_grain(samples):
    """
    Tag each sample interval or day.

    Auto Export posts either per-hour/per-minute interval samples or one
    already-aggregated sample per day at 00:00:00. Within a payload, a
    date carrying exactly one timestamp for a metric, and that timestamp
    being midnight, is the daily rollup shape; anything else is an
    interval. Keyed on distinct times, so two spellings of the same
    metric in one body do not make a rollup look like two intervals.

    Accepted edge: an hourly export whose only sample for a date is the
    00:00 hour is read as a rollup of the day so far. Under S02 that
    figure is compared, never summed, so later hours still win.
    """
    times = {}
    for date, tm, metric, value, unit in samples:
        key = (date, metric)
        if key not in times:
            times[key] = set()
        times[key].add(tm)

    out = []
    for date, tm, metric, value, unit in samples:
        if len(times[(date, metric)]) == 1 and tm in ("00:00:00", ""):
            grain = "day"
        else:
            grain = "interval"
        out.append((date, tm, metric, value, unit, grain))
    return out


def parse_apple_records(payload):
    """
    Record-level view of an Auto Export body.

    Returns (records, workouts, skipped) where a record is
    (record_key, date, metric, value, unit, grain).

    The key identifies the SAMPLE SLOT - feed, metric, grain, date, time
    - never the value. A redelivered or revised sample lands on its own
    key and updates in place, so a replay cannot add a day to itself.
    Grain is part of the key so that a whole-day rollup and an hour-00
    interval sample do not overwrite one another.
    """
    samples, workouts_out, skipped = _apple_samples(payload)
    records = []
    for date, tm, metric, value, unit, grain in _apple_grain(samples):
        key = "|".join([FEED_HAE, metric, grain, date, tm])
        records.append((key, date, metric, value, unit, grain))
    return records, workouts_out, skipped'''

# -- 5. schema + shared recompute + the Auto Export record store ----------

A6_OLD = '''def store_hc(conn, records, raw_text, source="healthconnect",
             summed=None, workouts=None):'''

A6_NEW = '''def _ensure_hc_record_columns(conn):
    """
    Bring health_hc_records up to the multi-source, multi-grain shape.

    Creates the table when it is missing (the base migration does not
    build it, and several suites hand-roll their own), then adds any
    absent column in place. Every row that existed before `source` was
    added came from Health Connect; every row that existed before
    `grain` and `feed` were added was an interval record from a feed
    that predates the column, which is exactly what the defaults say.

    Additive and idempotent.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS health_hc_records ("
        "record_key TEXT PRIMARY KEY, date TEXT NOT NULL, "
        "metric TEXT NOT NULL, value REAL, unit TEXT, "
        "ingested_at TEXT NOT NULL, "
        "source TEXT NOT NULL DEFAULT 'healthconnect', "
        "grain TEXT NOT NULL DEFAULT 'interval', "
        "feed TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_hc_records_date_metric "
        "ON health_hc_records (date, metric)"
    )
    cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(health_hc_records)").fetchall()]
    if "source" not in cols:
        conn.execute(
            "ALTER TABLE health_hc_records ADD COLUMN source TEXT "
            "NOT NULL DEFAULT 'healthconnect'"
        )
    if "grain" not in cols:
        conn.execute(
            "ALTER TABLE health_hc_records ADD COLUMN grain TEXT "
            "NOT NULL DEFAULT 'interval'"
        )
    if "feed" not in cols:
        conn.execute(
            "ALTER TABLE health_hc_records ADD COLUMN feed TEXT "
            "NOT NULL DEFAULT ''"
        )
    return True


def _recompute_value(cur, date, metric, source, summed_set, only_feed=None):
    """
    Rule S02 Day-Grain Precedence for one date + metric + source.

    Returns (value, unit), or None when no record covers it.

    Within a feed, interval records sum and the latest day record
    stands alone; a summed metric takes the GREATER of the two, so
    neither a partial interval batch nor a stale rollup can shrink the
    day. Across feeds, a summed metric again takes the greater - feeds
    are alternative views of one quantity and must never be added
    together. Levels (anything outside summed_set) are not additive:
    the day record wins if there is one, else the mean of the
    intervals, and the most recently written feed supplies the answer.

    only_feed pins the calculation to a single feed. The HC Webhook and
    retired ios paths pass "" so their arithmetic is exactly what it was
    before feeds existed.
    """
    sql = ("SELECT value, unit, grain, feed, ingested_at, record_key "
           "FROM health_hc_records "
           "WHERE date = ? AND metric = ? AND source = ?")
    args = [date, metric, source]
    if only_feed is not None:
        sql = sql + " AND feed = ?"
        args.append(only_feed)
    # Oldest first, so the last day record seen is the newest one.
    sql = sql + " ORDER BY ingested_at ASC, record_key ASC"
    rows = cur.execute(sql, tuple(args)).fetchall()
    if not rows:
        return None

    summed = metric in summed_set
    order = []
    feeds = {}
    for value, unit, grain, feed, iat, rkey in rows:
        if feed not in feeds:
            feeds[feed] = {"interval": [], "day": None,
                           "unit": unit, "at": (iat or "", rkey or "")}
            order.append(feed)
        slot = feeds[feed]
        stamp = (iat or "", rkey or "")
        if stamp >= slot["at"]:
            slot["at"] = stamp
            slot["unit"] = unit
        if grain == "day":
            slot["day"] = value
        else:
            slot["interval"].append(value)

    best = None
    best_unit = None
    best_at = None
    for feed in order:
        slot = feeds[feed]
        intervals = [v for v in slot["interval"] if v is not None]
        day_value = slot["day"]
        if summed:
            candidates = []
            if intervals:
                candidates.append(sum(intervals))
            if day_value is not None:
                candidates.append(day_value)
            if not candidates:
                continue
            value = max(candidates)
            if best is None or value > best:
                best = value
                best_unit = slot["unit"]
                best_at = slot["at"]
            continue
        if day_value is not None:
            value = day_value
        elif intervals:
            value = sum(intervals) / len(intervals)
        else:
            continue
        if best_at is None or slot["at"] > best_at:
            best = value
            best_unit = slot["unit"]
            best_at = slot["at"]

    if best is None:
        return None
    return best, best_unit


def _write_daily(cur, date, metric, value, unit, source, ts):
    cur.execute(
        "INSERT INTO health_metrics "
        "(date, metric, value, unit, source, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(date, metric, source) DO UPDATE SET "
        "value=excluded.value, unit=excluded.unit, "
        "ingested_at=excluded.ingested_at",
        (date, metric, value, unit, source, ts),
    )


def store_apple(conn, records, workout_rows, source, raw_text):
    """
    Record-level store for the Auto Export shape.

    Same construction as the HC and ios paths: every sample is written
    under its own dedup key and the daily figure is recomputed from the
    stored records under rule S02. A partial batch therefore adds to a
    day and can never replace it with a smaller number.

    Returns (new_records, updated_records, dates_touched).
    """
    ts = _now()
    _ensure_hc_record_columns(conn)
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO health_raw (received_at, source, n_bytes, payload) "
        "VALUES (?, ?, ?, ?)",
        (ts, source, len(raw_text or ""), raw_text or ""),
    )

    new_rows = 0
    updated_rows = 0
    touched = set()
    for key, date, metric, value, unit, grain in records:
        prior = cur.execute(
            "SELECT value FROM health_hc_records WHERE record_key = ?",
            (key,),
        ).fetchone()
        cur.execute(
            "INSERT INTO health_hc_records "
            "(record_key, date, metric, value, unit, ingested_at, "
            " source, grain, feed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_key) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at, source=excluded.source, "
            "grain=excluded.grain, feed=excluded.feed",
            (key, date, metric, value, unit, ts, source, grain, FEED_HAE),
        )
        if prior is None:
            new_rows = new_rows + 1
        elif prior[0] != value:
            updated_rows = updated_rows + 1
        touched.add((date, metric))

    for row in (workout_rows or []):
        cur.execute(
            "INSERT INTO health_workouts "
            "(date, start_ts, end_ts, wtype, duration_s, energy_kcal, "
            " distance_km, avg_hr, max_hr, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(start_ts, wtype, source) DO UPDATE SET "
            "end_ts=excluded.end_ts, duration_s=excluded.duration_s, "
            "energy_kcal=excluded.energy_kcal, "
            "distance_km=excluded.distance_km, "
            "avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, "
            "ingested_at=excluded.ingested_at",
            tuple(row) + (source, ts),
        )

    for date, metric in sorted(touched):
        got = _recompute_value(cur, date, metric, source, _APPLE_SUM)
        if got is None:
            continue
        _write_daily(cur, date, metric, got[0], got[1], source, ts)

    conn.commit()
    return new_rows, updated_rows, sorted(set(d for d, _ in touched))


def store_hc(conn, records, raw_text, source="healthconnect",
             summed=None, workouts=None):'''

A7_OLD = '''    ts = _now()
    _ensure_hc_source_column(conn)
    cur = conn.cursor()'''

A7_NEW = '''    ts = _now()
    _ensure_hc_record_columns(conn)
    cur = conn.cursor()'''

A8_OLD = '''    summed_set = HC_SUMMED if summed is None else summed
    for date, metric in touched:
        if metric in summed_set:
            agg = "SUM(value)"
        else:
            agg = "AVG(value)"
        row = cur.execute(
            "SELECT " + agg + ", unit FROM health_hc_records "
            "WHERE date = ? AND metric = ? AND source = ?",
            (date, metric, source),
        ).fetchone()
        if row is None or row[0] is None:
            continue
        cur.execute(
            "INSERT INTO health_metrics "
            "(date, metric, value, unit, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, metric, source) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (date, metric, row[0], row[1], source, ts),
        )'''

A8_NEW = '''    # One shared recompute, pinned to this path's own feed. HC Webhook
    # records and the retired ios records both predate the feed column
    # and carry "", so every figure here is exactly what it was before;
    # Auto Export records stay out of a sum that would count the same
    # day twice.
    summed_set = HC_SUMMED if summed is None else summed
    for date, metric in sorted(touched):
        got = _recompute_value(cur, date, metric, source, summed_set,
                               only_feed="")
        if got is None:
            continue
        _write_daily(cur, date, metric, got[0], got[1], source, ts)'''

# -- 6. drop total_energy_kcal from both record maps ----------------------

A9_OLD = '''    "active_calories_burned": ("active_energy_kcal", "kcal"),
    "total_calories_burned": ("total_energy_kcal", "kcal"),
    "floors_climbed": ("flights", "count"),
}

# Summed across the day. Anything not listed here is averaged instead.
HC_SUMMED = ("steps", "distance_km", "active_energy_kcal",
             "total_energy_kcal", "flights")'''

A9_NEW = '''    "active_calories_burned": ("active_energy_kcal", "kcal"),
    "floors_climbed": ("flights", "count"),
}

# total_calories_burned is deliberately unmapped. total_energy_kcal was
# never displayed, no F-rule read it, and it meant active+basal from one
# feed and something else from another. The raw bodies are kept in
# health_raw, so re-deriving it later costs a re-parse and nothing more.

# Summed across the day. Anything not listed here is averaged instead.
HC_SUMMED = ("steps", "distance_km", "active_energy_kcal", "flights")'''

A10_OLD = '''    "active_calories": ("active_energy_kcal", "kilocalories", "kcal"),
    "total_calories": ("total_energy_kcal", "kilocalories", "kcal"),
    "resting_heart_rate": ("resting_hr", "bpm", "count/min"),'''

A10_NEW = '''    "active_calories": ("active_energy_kcal", "kilocalories", "kcal"),
    "resting_heart_rate": ("resting_hr", "bpm", "count/min"),'''

A11_OLD = '''IOS_SUMMED = ("steps", "distance_km", "active_energy_kcal",
              "total_energy_kcal")'''

A11_NEW = '''IOS_SUMMED = ("steps", "distance_km", "active_energy_kcal")'''

# -- 7. route the Auto Export shape through the record store --------------

A12_OLD = '''    metric_rows, workout_rows, skipped = parse_payload(payload)

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

A12_NEW = '''    # Record level, not a single upserted daily figure. store() is left
    # in place for callers that already hold a day view, but nothing on
    # this route may overwrite a day with one payload's worth of it.
    records, workout_rows, skipped = parse_apple_records(payload)

    conn = _connect()
    try:
        new_rows, updated_rows, dates = store_apple(
            conn, records, workout_rows, source, raw_text)
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "source": source,
        "format": "apple_auto_export",
        "metrics_stored": len(records),
        "records_new": new_rows,
        "records_updated": updated_rows,
        "workouts_stored": len(workout_rows),
        "dates": dates,
        "skipped_metrics": skipped,
    }), 200'''

EDITS = [
    ("document rule S02", A1_OLD, A1_NEW),
    ("metric map: distance in, total calories out", A2_OLD, A2_NEW),
    ("distance unit normalisation", A3_OLD, A3_NEW),
    ("split parse_payload into samples", A4_OLD, A4_NEW),
    ("day view + record view", A5_OLD, A5_NEW),
    ("schema, shared recompute, apple record store", A6_OLD, A6_NEW),
    ("store_hc uses the new schema helper", A7_OLD, A7_NEW),
    ("store_hc uses the shared recompute", A8_OLD, A8_NEW),
    ("drop total energy from the hc map", A9_OLD, A9_NEW),
    ("drop total energy from the ios map", A10_OLD, A10_NEW),
    ("drop total energy from the ios summed set", A11_OLD, A11_NEW),
    ("route auto export to the record store", A12_OLD, A12_NEW),
]

REQUIRED = (
    ("_apple_aggregate", "patch_apple_aggregation.py"),
    ("parse_hc_payload", "patch_hc_support.py"),
    ("parse_ios_payload", "patch_ios_payload.py"),
    ("_ensure_hc_source_column", "patch_ios_source_isolation.py"),
)


def main():
    dry = "--dry-run" in sys.argv
    positional = []
    for item in sys.argv[1:]:
        if item == "--dry-run":
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

    if "parse_apple_records" in src:
        print("SKIP: record-level Auto Export ingest already present.")
        return 0

    for marker, patcher in REQUIRED:
        if marker not in src:
            print("FAIL: " + marker + " missing. Run " + patcher + " first.")
            return 1

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

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    bak = target + ".bak_" + stamp
    shutil.copy2(target, bak)
    print("Backup  : " + bak)

    with open(target, "w") as fh:
        fh.write(patched)

    with open(target, "r") as fh:
        verify = fh.read()
    if "parse_apple_records" not in verify:
        shutil.copy2(bak, target)
        print("FAIL: write verification failed. Rolled back from " + bak)
        return 1

    print("Patched : ok")
    print("")
    print("Next, in order:")
    print("  python3 test_health_ingest.py")
    print("  python3 test_hc_ingest.py")
    print("  python3 test_ios_ingest.py")
    print("  python3 test_ios_source_isolation.py")
    print("  python3 test_apple_aggregation.py")
    print("  python3 test_apple_records.py")
    print("  python3 recompute_apple_daily.py --dry-run")
    print("  systemctl restart fitlog")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

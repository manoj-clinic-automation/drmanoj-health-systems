# -*- coding: utf-8 -*-
"""
FitLog - wearable ingest blueprint.

Accepts Health Auto Export (iOS, v2 JSON) and Health Connect payloads,
stores whitelisted daily metrics plus every raw body, and resolves
multi-source conflicts under a single named rule.

    S01 Source Precedence
      - applewatch wins for every metric it reports
      - healthconnect fills a date only when applewatch has no record
      - values are never summed or averaged across sources

Deterministic. No LLM in the path. Python 3.9 compatible.

Endpoints
    POST /api/ingest?source=applewatch      Bearer token required
    GET  /api/health/daily?date=YYYY-MM-DD  Bearer token required
    GET  /api/ingest/status                 Bearer token required
"""

import hmac
import json
import os
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

health_ingest_bp = Blueprint("health_ingest", __name__)

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

TOKEN_FILE = os.environ.get("FITLOG_INGEST_ENV", "/root/fitlog/ingest.env")


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
           or "/root/fitlog/fitlog.db")

MAX_BODY_BYTES = 12 * 1024 * 1024

# Health Auto Export metric name -> FitLog canonical name.
# Anything outside this map is kept in health_raw only.
METRIC_MAP = {
    "step_count": "steps",
    "active_energy": "active_energy_kcal",
    "basal_energy_burned": "basal_energy_kcal",
    "apple_exercise_time": "exercise_minutes",
    "apple_stand_hour": "stand_hours",
    "flights_climbed": "flights",
    "resting_heart_rate": "resting_hr",
    "walking_heart_rate_average": "walking_hr_avg",
    "heart_rate_variability": "hrv_ms",
    "respiratory_rate": "resp_rate",
    "sleep_analysis": "sleep_hours",
    "weight_body_mass": "weight_kg",
    "blood_oxygen_saturation": "spo2_pct",
    # Health Connect / generic aliases
    "steps": "steps",
    "total_calories_burned": "active_energy_kcal",
    "resting_heart_rate_bpm": "resting_hr",
    "sleep_session": "sleep_hours",
}

# Metrics that inform the rule engine. Everything else is stored as context.
RULE_BEARING = ("steps", "exercise_minutes", "stand_hours", "flights")

# Explicitly non-rule-bearing: HR is unreliable during medication titration,
# aerobic intensity stays governed by talk-test only.
CONTEXT_ONLY = (
    "resting_hr",
    "walking_hr_avg",
    "hrv_ms",
    "spo2_pct",
    "resp_rate",
)

ALLOWED_SOURCES = ("applewatch", "healthconnect", "manual")

SOURCE_PRECEDENCE = ("applewatch", "healthconnect", "manual")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _load_token():
    """Read FITLOG_INGEST_TOKEN from the secrets file. Returns None if absent."""
    env_token = os.environ.get("FITLOG_INGEST_TOKEN")
    if env_token:
        return env_token.strip()
    try:
        with open(TOKEN_FILE, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() == "FITLOG_INGEST_TOKEN":
                    return val.strip().strip('"').strip("'")
    except IOError:
        return None
    return None


def _load_hc_token():
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


def _authorised(req):
    expected = _load_token()
    if not expected:
        return False
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    supplied = header[7:].strip()
    return hmac.compare_digest(supplied, expected)


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(raw):
    """
    Health Auto Export emits '2026-09-09 00:00:00 +0530'.
    Health Connect exporters emit ISO 8601. Both start with YYYY-MM-DD.
    Returns the date string, or None when unparseable.
    """
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()[:10]
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None
    return candidate


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sleep_hours(point):
    """
    Sleep arrives either as a total or split into phases.
    Prefer an explicit asleep total; otherwise sum the phases.
    Values are hours in HAE v2.
    """
    for key in ("totalSleep", "asleep"):
        val = _num(point.get(key))
        if val is not None:
            return val
    phases = ("deep", "core", "rem", "light")
    total = 0.0
    seen = False
    for key in phases:
        val = _num(point.get(key))
        if val is not None:
            total = total + val
            seen = True
    if seen:
        return total
    return _num(point.get("qty"))


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def parse_payload(payload):
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
            metrics_out.append((date, canonical, value, unit))

    for wk in data.get("workouts") or []:
        if not isinstance(wk, dict):
            continue
        start_raw = wk.get("start") or wk.get("startDate")
        date = _parse_date(start_raw)
        if not date:
            continue
        energy = wk.get("activeEnergyBurned") or wk.get("activeEnergy") or {}
        distance = wk.get("distance") or {}
        avg_hr = wk.get("avgHeartRate") or wk.get("heartRateAvg") or {}
        max_hr = wk.get("maxHeartRate") or wk.get("heartRateMax") or {}
        workouts_out.append((
            date,
            str(start_raw).strip(),
            str(wk.get("end") or wk.get("endDate") or "").strip() or None,
            (wk.get("name") or wk.get("workoutActivityType") or "unknown").strip(),
            _num(wk.get("duration")),
            _num(energy.get("qty") if isinstance(energy, dict) else energy),
            _num(distance.get("qty") if isinstance(distance, dict) else distance),
            _num(avg_hr.get("qty") if isinstance(avg_hr, dict) else avg_hr),
            _num(max_hr.get("qty") if isinstance(max_hr, dict) else max_hr),
        ))

    return metrics_out, workouts_out, sorted(skipped)


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def store(conn, metric_rows, workout_rows, source, raw_text):
    ts = _now()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO health_raw (received_at, source, n_bytes, payload) "
        "VALUES (?, ?, ?, ?)",
        (ts, source, len(raw_text or ""), raw_text or ""),
    )

    for date, metric, value, unit in metric_rows:
        cur.execute(
            "INSERT INTO health_metrics "
            "(date, metric, value, unit, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, metric, source) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at",
            (date, metric, value, unit, source, ts),
        )

    for row in workout_rows:
        cur.execute(
            "INSERT INTO health_workouts "
            "(date, start_ts, end_ts, wtype, duration_s, energy_kcal, "
            " distance_km, avg_hr, max_hr, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(start_ts, wtype, source) DO UPDATE SET "
            "end_ts=excluded.end_ts, duration_s=excluded.duration_s, "
            "energy_kcal=excluded.energy_kcal, distance_km=excluded.distance_km, "
            "avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, "
            "ingested_at=excluded.ingested_at",
            tuple(row) + (source, ts),
        )

    conn.commit()


def resolve_daily(conn, date):
    """
    Apply S01 Source Precedence for one date.
    Returns {metric: {value, unit, source, rule_bearing}}.
    """
    rows = conn.execute(
        "SELECT metric, value, unit, source FROM health_metrics WHERE date = ?",
        (date,),
    ).fetchall()

    best = {}
    for row in rows:
        metric = row["metric"]
        source = row["source"]
        if source not in SOURCE_PRECEDENCE:
            continue
        rank = SOURCE_PRECEDENCE.index(source)
        current = best.get(metric)
        if current is None or rank < current["rank"]:
            best[metric] = {
                "rank": rank,
                "value": row["value"],
                "unit": row["unit"],
                "source": source,
            }

    out = {}
    for metric, info in best.items():
        out[metric] = {
            "value": info["value"],
            "unit": info["unit"],
            "source": info["source"],
            "rule_bearing": metric in RULE_BEARING,
        }
    return out


# --------------------------------------------------------------------------
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
# --------------------------------------------------------------------------

@health_ingest_bp.route("/api/ingest", methods=["POST"])
def api_ingest():
    source = (request.args.get("source") or "").strip().lower()

    # healthconnect may authenticate by URL token, since HC Webhook
    # cannot send headers. Every other source stays header-only.
    ok_auth = _authorised(request)
    if not ok_auth and source == "healthconnect":
        ok_auth = _authorised_hc(request)
    if not ok_auth:
        return jsonify({"ok": False, "error": "unauthorised"}), 401

    if source not in ALLOWED_SOURCES:
        return jsonify({
            "ok": False,
            "error": "unknown source",
            "allowed": list(ALLOWED_SOURCES),
        }), 400

    raw_text = request.get_data(as_text=True) or ""
    if len(raw_text) > MAX_BODY_BYTES:
        return jsonify({"ok": False, "error": "payload too large"}), 413

    try:
        payload = json.loads(raw_text)
    except ValueError:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    # HC Webhook uses a different payload shape: snake_case arrays of
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
    }), 200


@health_ingest_bp.route("/api/health/daily", methods=["GET"])
def api_health_daily():
    if not _authorised(request):
        return jsonify({"ok": False, "error": "unauthorised"}), 401

    date = _parse_date(request.args.get("date") or "")
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    conn = _connect()
    try:
        resolved = resolve_daily(conn, date)
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "date": date,
        "rule": "S01 Source Precedence",
        "metrics": resolved,
    }), 200


@health_ingest_bp.route("/api/ingest/status", methods=["GET"])
def api_ingest_status():
    if not _authorised(request):
        return jsonify({"ok": False, "error": "unauthorised"}), 401

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT source, MAX(ingested_at) AS last_seen, "
            "       MAX(date) AS latest_date, COUNT(*) AS n "
            "FROM health_metrics GROUP BY source"
        ).fetchall()
        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        sources = []
        for row in rows:
            sources.append({
                "source": row["source"],
                "last_seen": row["last_seen"],
                "latest_date": row["latest_date"],
                "rows": row["n"],
                "stale": (row["last_seen"] or "") < cutoff,
            })
    finally:
        conn.close()

    return jsonify({"ok": True, "sources": sources}), 200

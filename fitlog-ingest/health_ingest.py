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
DB_PATH = os.environ.get("FITLOG_DB", "/root/fitlog/fitlog.db")

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
# routes
# --------------------------------------------------------------------------

@health_ingest_bp.route("/api/ingest", methods=["POST"])
def api_ingest():
    if not _authorised(request):
        return jsonify({"ok": False, "error": "unauthorised"}), 401

    source = (request.args.get("source") or "").strip().lower()
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

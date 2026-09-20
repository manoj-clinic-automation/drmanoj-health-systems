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

    S02 Day-Grain Precedence
      - every sample is stored as a record under a dedup key; the daily
        figure in health_metrics is recomputed from those records
      - within one feed: interval records SUM, the latest whole-day
        rollup replaces the previous one, and the day is the GREATER of
        the two
      - across feeds of the same source: the greater, never the sum
      - a partial batch can only add to a day, never shrink it

    S03 Sleep Block Identity  (FITLOG_SLEEP_P1)
      - Auto Export dates every sleep point at midnight of the day the
        night is filed under, so two blocks of one night arrive with
        identical stamps; a sleep sample is keyed by its own sleepStart
      - blocks that OVERLAP are competing descriptions of one stretch of
        the night and the longest-span one wins, never the sum
      - blocks that do not overlap are different stretches and ADD
      - a total is believed only when greater than zero: `asleep` is
        Apple's retired pre-stage category and arrives as 0 on every
        Watch night seen here. Awake time is never counted as sleep.

    S04 Overnight Basis  (FITLOG_SLEEP_P2)
      - a metric reported "overnight" is averaged over the samples whose
        own timestamps fall inside the sleep span, and says so
      - where no sample carried a time inside the span, the day's figure
        is shown and labelled a day figure, never passed off as one
        measured over the night
      - resting HR and HRV are derived overnight by the Watch: they are
        sleep figures as much as cardiac ones, and are shown as inputs,
        not conclusions

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
    "mindful_minutes": "mindful_min",
    "mindful_session": "mindful_min",
    # FITLOG_SLEEP_P2. Wrist temperature is measured by the Watch every
    # night and was being dropped on the floor: no name here claimed it.
    # He has had subjective feverishness for over two years with, until
    # 2026-09-14, no documented temperature at all.
    #
    # As of the 2026-09-15 census of all 212 stored bodies, Auto Export is
    # not sending ANY of these yet -- it has to be switched on in the app.
    # Several spellings are claimed so the value lands whichever one it
    # arrives under; anything else still shows up by name under
    # skipped_metrics rather than being dropped silently.
    "apple_sleeping_wrist_temperature": "wrist_temp_c",
    "sleeping_wrist_temperature": "wrist_temp_c",
    "wrist_temperature": "wrist_temp_c",
    # A thermometer reading that reaches Health. Distinct from the wrist
    # sensor and never averaged together with it.
    "body_temperature": "body_temp_c",
    "basal_body_temperature": "basal_body_temp_c",
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
    # FITLOG_SLEEP_P2. Temperature is context, never a rule input. It is
    # here to be LOOKED AT beside a night and a symptom, not to make the
    # engine decide anything.
    "wrist_temp_c",
    "body_temp_c",
    "basal_body_temp_c",
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
    Hours ASLEEP in one sleep block. None when the point measures nothing.

    Every Apple Watch night in health_raw on this server carries
    asleep=0 and inBed=0 beside a real totalSleep and real core / deep /
    rem / awake figures: `asleep` is Apple's retired pre-stage category,
    not a total. Preferring it merely because it is present would store
    a zero night, so a total is believed only when it is greater than
    zero.

    Values are hours in HAE v2.
    """
    for key in ("totalSleep", "asleep"):
        val = _num(point.get(key))
        if val is not None and val > 0:
            return val
    # Apple's stages are Awake / REM / Core / Deep. "light" is kept for
    # exporters using the older naming. "awake" is deliberately absent:
    # it is time in bed not asleep, and adding it would overstate the
    # night by exactly the part he was lying there awake for.
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
    qty = _num(point.get("qty"))
    if qty is not None and qty > 0:
        return qty
    # Neither a positive total, nor stages, nor a usable qty. That is
    # nothing measured, not a measurement of nothing -- returning 0.0
    # would put a zero night on the record.
    return None


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

# FITLOG_V120_ACTIVITY -- workout names and kinds
def _wname(wk):
    """Workout name; '(indoor)' added when the watch marks it indoor."""
    name = str(wk.get("name") or wk.get("workoutActivityType") or "unknown").strip()
    loc = str(wk.get("location") or "").strip().lower()
    indoor = wk.get("isIndoor") in (True, 1, "true", "True", "1") or loc == "indoor"
    if indoor and "indoor" not in name.lower():
        name += " (indoor)"
    return name


def classify_workout(wtype):
    """walk / treadmill / cycle_road / cycle_static / meditation / other."""
    w = (wtype or "").lower()
    indoor = "indoor" in w
    if "treadmill" in w:
        return "treadmill"
    if "cycl" in w or "bik" in w or "cycle" in w:
        return "cycle_static" if (indoor or "stationary" in w or "spin" in w) else "cycle_road"
    if "walk" in w or "hik" in w:
        return "treadmill" if indoor else "walk"
    if "mind" in w or "meditat" in w or "breath" in w:
        return "meditation"
    return "other"


# Health Auto Export reports energy in kilojoules. Canonical names end
# in _kcal, so the value has to be converted, not just relabelled.
_KJ_PER_KCAL = 4.184

_ENERGY_METRICS = ("active_energy_kcal", "basal_energy_kcal")

_KJ_UNITS = ("kj", "kilojoule", "kilojoules")
_KCAL_UNITS = ("kcal", "cal", "calorie", "calories", "kilocalorie",
               "kilocalories")

# Auto Export reports distance in whatever the phone's units setting
# says. The canonical name promises km, so the value is converted.
_KM_UNITS = ("km", "kilometer", "kilometers", "kilometre", "kilometres")
_MI_UNITS = ("mi", "mile", "miles")
_M_UNITS = ("m", "meter", "meters", "metre", "metres")
_KM_PER_MILE = 1.609344

# FITLOG_SLEEP_P2. Temperature, converted rather than relabelled.
_TEMP_METRICS = ("wrist_temp_c", "body_temp_c", "basal_body_temp_c")
_C_UNITS = ("degc", "\u00b0c", "c", "celsius", "centigrade")
_F_UNITS = ("degf", "\u00b0f", "f", "fahrenheit")


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
    if canonical in _TEMP_METRICS:
        if u in _F_UNITS:
            return (value - 32.0) * 5.0 / 9.0, "degC"
        if u in _C_UNITS:
            return value, "degC"
        # A temperature in a unit this cannot convert is DROPPED, not
        # guessed at and not stored under its own unit. A canonical name
        # ending _c quietly holding 98.6 is a lie in a health record, and
        # a unit column nobody reads is not a defence. The caller skips
        # a None.
        return None, unit
    return value, unit


# A day's samples must be combined, not overwritten. Counts and durations
# add up; levels are averaged. Every canonical name in METRIC_MAP appears
# in one of these two lists.
_APPLE_SUM = (
    "steps", "active_energy_kcal", "basal_energy_kcal",
    "exercise_minutes", "stand_hours", "flights",
    "mindful_min", "sleep_hours", "distance_km",
)

_APPLE_MEAN = (
    "resting_hr", "walking_hr_avg", "hrv_ms", "resp_rate",
    "spo2_pct", "weight_kg",
    # Levels, never totals. Adding two temperatures together would be
    # nonsense, and the default is the mean anyway -- they are named here
    # so that nobody has to check the default to know that.
    "wrist_temp_c", "body_temp_c", "basal_body_temp_c",
)


def _apple_aggregate(rows):
    """
    Collapse one calendar day's samples per metric.

    Anything not named in either list defaults to the mean, which fails
    far less loudly than a silent overwrite. Insertion order is kept so
    the result is deterministic.
    """
    order = []
    bucket = {}
    for date, metric, value, unit in rows:
        key = (date, metric)
        if key not in bucket:
            bucket[key] = []
            order.append(key)
        bucket[key].append((value, unit))

    out = []
    for key in order:
        date, metric = key
        samples = bucket[key]
        nums = [v for v, u in samples]
        unit = samples[-1][1]
        if metric in _APPLE_SUM:
            value = sum(nums)
        else:
            value = sum(nums) / len(nums)
        out.append((date, metric, value, unit))
    return out


# Which inbound shape wrote a record. Rows that predate the column
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


def _ist_stamp(raw):
    """
    "YYYY-MM-DD HH:MM:SS" in IST, or None when the stamp cannot be read.

    Every time this system stores or shows is IST. Auto Export already
    sends +0530 and passes straight through; a Z stamp is UTC and is
    moved on by 5h30m; any other offset is converted. A naive stamp is
    taken as already local, which is what every feed here sends.

    None rather than a guess: a sleep block filed at the wrong hour is
    worse than a blank one, because it would move the night onto the
    wrong day.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("T", " ")
    if len(text) < 19:
        return None
    try:
        stamp = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    rest = text[19:].strip()
    if rest[:1] == ".":
        rest = rest.lstrip(".0123456789").strip()
    if not rest:
        return stamp.strftime("%Y-%m-%d %H:%M:%S")
    if rest[0] in ("Z", "z"):
        moved = stamp + timedelta(minutes=IST_OFFSET_MINUTES)
        return moved.strftime("%Y-%m-%d %H:%M:%S")
    if rest[0] not in ("+", "-"):
        return None
    digits = rest[1:].replace(":", "")
    if len(digits) < 4 or not digits[:4].isdigit():
        return None
    offset = int(digits[:2]) * 60 + int(digits[2:4])
    if rest[0] == "-":
        offset = -offset
    moved = stamp + timedelta(minutes=IST_OFFSET_MINUTES - offset)
    return moved.strftime("%Y-%m-%d %H:%M:%S")


def _sleep_block_time(point, stamp):
    """
    The time of day that identifies WHICH block of the night this is.

    S03. Auto Export stamps every sleep point at midnight of the day the
    night is filed under -- the 2026-09-14 point on this server covers
    23:08 on 09-13 to 03:19 on 09-14 and is still stamped
    "2026-09-14 00:00:00 +0530". Two blocks of one night therefore arrive
    indistinguishable, collide on one record slot, and the second
    replaces the first. sleepStart is what tells them apart.

    The hour returned may belong to the previous calendar day; it is a
    slot label, not a date. Two blocks of one night sharing a start time
    to the second would be the same block.
    """
    for key in ("sleepStart", "inBedStart"):
        got = _apple_time(point.get(key))
        if got:
            return got
    return _apple_time(stamp)


def parse_sleep_blocks(payload):
    """
    Every sleep block in an Auto Export body, with its real IST span.

    One dict per sleep_analysis point. The day's figure is derived from
    these spans rather than from a single number, which is what lets a
    night delivered in pieces be put back together -- and what stops a
    re-segmented night from being added to itself.
    """
    out = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    for entry in data.get("metrics") or []:
        if not isinstance(entry, dict):
            continue
        raw_name = (entry.get("name") or "").strip().lower()
        if METRIC_MAP.get(raw_name) != "sleep_hours":
            continue
        for point in entry.get("data") or []:
            if not isinstance(point, dict):
                continue
            date = _parse_date(point.get("date"))
            if not date:
                continue
            start = _ist_stamp(point.get("sleepStart"))
            end = _ist_stamp(point.get("sleepEnd"))
            bed_start = _ist_stamp(point.get("inBedStart"))
            bed_end = _ist_stamp(point.get("inBedEnd"))
            asleep = _sleep_hours(point)
            if asleep is None and not (start and end):
                continue
            out.append({
                "date": date,
                "start_ts": start,
                "end_ts": end,
                "in_bed_start": bed_start,
                "in_bed_end": bed_end,
                "asleep_h": asleep,
                "rem_h": _num(point.get("rem")),
                "core_h": _num(point.get("core")),
                "deep_h": _num(point.get("deep")),
                "awake_h": _num(point.get("awake")),
                "device": str(point.get("source") or "").strip(),
            })
    return out


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
                # S03: the slot a sleep sample occupies is its BLOCK, not
                # the midnight stamp every block of the night shares.
                tod = _sleep_block_time(point, stamp)
            else:
                value = _num(point.get("qty"))
                if value is None:
                    value = _num(point.get("Avg"))
                tod = _apple_time(stamp)
            if value is None:
                continue
            # Bind to a fresh name: `unit` is the entry-level unit and is
            # reused by every point in this entry. Rebinding it converts
            # the first sample, then makes every later sample look like
            # it is already in kcal and pass through unconverted.
            value, point_unit = _apple_convert(canonical, value, unit)
            if value is None:
                # FITLOG_SLEEP_P2: a temperature in a unit the converter
                # does not know. Dropped rather than filed under a name
                # that promises Celsius.
                continue
            samples.append((date, tod, canonical, value, point_unit))

    for wk in data.get("workouts") or []:
        if not isinstance(wk, dict):
            continue
        start_raw = wk.get("start") or wk.get("startDate")
        # Workouts are dated in IST, never by slicing the stamp. A 'Z'
        # stamp is UTC and IST is UTC+5:30, so [:10] files anything
        # starting before 05:30 IST under the previous day - which is
        # exactly when he walks. _to_ist_date converts a Z stamp and
        # passes a '+0530' stamp straight through to _parse_date, so the
        # Auto Export shape in use today is unaffected.
        date = _to_ist_date(start_raw)
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
            _wname(wk),
            _num(wk.get("duration")),
            _num(energy.get("qty") if isinstance(energy, dict) else energy),
            _num(distance.get("qty") if isinstance(distance, dict) else distance),
            _num(avg_hr.get("qty") if isinstance(avg_hr, dict) else avg_hr),
            _num(max_hr.get("qty") if isinstance(max_hr, dict) else max_hr),
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
    return records, workouts_out, skipped


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

# iOS Health Webhook arrays. Same idea as Android, different value keys.
# basal_metabolic_rate is deliberately excluded - hundreds of records a
# day and no use in any rule.
IOS_ARRAY_MAP = {
    "steps": ("steps", "count", "count"),
    "distance": ("distance_km", "meters", "km"),
    "active_calories": ("active_energy_kcal", "kilocalories", "kcal"),
    "resting_heart_rate": ("resting_hr", "bpm", "count/min"),
    "heart_rate": ("hr", "bpm", "count/min"),
    "heart_rate_variability": ("hrv_ms", "milliseconds", "ms"),
}

# Summed across the day; everything else is averaged.
IOS_SUMMED = ("steps", "distance_km", "active_energy_kcal")

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
            # The goal each ring is measured against. Context-only by
            # construction: a target is not a measurement, and no F-rule
            # may read one. Absent from RULE_BEARING on purpose.
            ("stand_goal_hours", "stand_goal_hours", "count"),
            ("exercise_goal_minutes", "exercise_goal_min", "min"),
            ("move_goal_kilocalories", "move_goal_kcal", "kcal"),
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


HC_ARRAY_MAP = {
    "steps": ("steps", "count"),
    "distance": ("distance_km", "km"),
    "active_calories_burned": ("active_energy_kcal", "kcal"),
    "floors_climbed": ("flights", "count"),
}

# total_calories_burned is deliberately unmapped. total_energy_kcal was
# never displayed, no F-rule read it, and it meant active+basal from one
# feed and something else from another. The raw bodies are kept in
# health_raw, so re-deriving it later costs a re-parse and nothing more.

# Summed across the day. Anything not listed here is averaged instead.
HC_SUMMED = ("steps", "distance_km", "active_energy_kcal", "flights")

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


def _ensure_hc_source_column(conn):
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


def _ensure_hc_record_columns(conn):
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


def _ensure_sleep_block_table(conn):
    """
    The per-block sleep store. Additive, idempotent, created in place.

    Separate from health_hc_records because a sleep block is a span with
    stages, not a scalar sample, and the night's arithmetic needs the
    spans. Built here rather than in the base migration for the same
    reason health_hc_records is: several suites hand-roll their schema
    and the live database must not need a second deploy step.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS health_sleep_blocks ("
        "block_key TEXT PRIMARY KEY, date TEXT NOT NULL, "
        "source TEXT NOT NULL, feed TEXT NOT NULL DEFAULT '', "
        "start_ts TEXT, end_ts TEXT, "
        "in_bed_start TEXT, in_bed_end TEXT, "
        "asleep_h REAL, rem_h REAL, core_h REAL, deep_h REAL, "
        "awake_h REAL, device TEXT, ingested_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_sleep_blocks_date "
        "ON health_sleep_blocks (date, source)"
    )
    return True


def _span_minutes(start, end):
    """Minutes between two IST stamps, or None."""
    if not start or not end:
        return None
    try:
        a = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return (b - a).total_seconds() / 60.0


def cluster_sleep_blocks(blocks):
    """
    Group blocks that describe the same stretch of the night. Rule S03.

    Two blocks that overlap are competing descriptions of one stretch --
    Auto Export re-segmenting a night it has already delivered -- and the
    longest-span description wins. Adding them would invent sleep he did
    not have. Two blocks that do not overlap are different stretches and
    add; touching end-to-start counts as separate, because that is two
    recorded blocks with no gap rather than one.

    Blocks with no usable span cannot be placed against the others and
    stand alone. Returns a list of clusters, earliest first.
    """
    usable = []
    loose = []
    for b in blocks:
        if b.get("start_ts") and b.get("end_ts") and b["end_ts"] > b["start_ts"]:
            usable.append(b)
        else:
            loose.append(b)
    usable.sort(key=lambda b: (b["start_ts"], b["end_ts"]))

    clusters = []
    for b in usable:
        if clusters and b["start_ts"] < clusters[-1]["end"]:
            cur = clusters[-1]
            cur["members"].append(b)
            if b["end_ts"] > cur["end"]:
                cur["end"] = b["end_ts"]
        else:
            clusters.append({"start": b["start_ts"], "end": b["end_ts"],
                             "members": [b]})

    for cur in clusters:
        best = cur["members"][0]
        best_span = _span_minutes(best["start_ts"], best["end_ts"]) or 0.0
        for b in cur["members"][1:]:
            span = _span_minutes(b["start_ts"], b["end_ts"]) or 0.0
            if span > best_span:
                best, best_span = b, span
        cur["winner"] = best
        cur["span_min"] = best_span

    for b in loose:
        clusters.append({"start": b.get("start_ts"), "end": b.get("end_ts"),
                         "members": [b], "winner": b, "span_min": None})
    return clusters


def _in_bed_hours(clusters):
    """
    Time in bed, summed over the stretches of the night.

    NOT the span from the first stretch to the last. A night that runs
    23:00 to 01:30, breaks, and resumes 02:10 to 05:00 SPANS six hours
    and holds five hours twenty of recorded bed time. Spanning the gap
    would put forty minutes he may well have spent out of bed into the
    figure, and "in bed" would then read longer than anything the Watch
    actually recorded -- on a record built for someone with insomnia,
    overstating time in bed is precisely the wrong way to be wrong.

    The in-bed stamps are preferred; a stretch that carries none falls
    back to its sleep span.
    """
    total = 0.0
    seen = False
    for cur in clusters:
        win = cur.get("winner") or {}
        mins = _span_minutes(win.get("in_bed_start"), win.get("in_bed_end"))
        if mins is None:
            mins = _span_minutes(win.get("start_ts"), win.get("end_ts"))
        if mins is None:
            continue
        total = total + mins
        seen = True
    if not seen:
        return None
    return total / 60.0


def _sum_stage(clusters, key):
    got = [c["winner"].get(key) for c in clusters]
    got = [g for g in got if g is not None]
    if not got:
        return None
    return sum(got)


# Metrics worth reading over the NIGHT rather than over the day.
# Deliberately all context-only: this is a record to look at, not an
# input to any rule.
OVERNIGHT_METRICS = ("wrist_temp_c", "resp_rate", "spo2_pct", "hrv_ms",
                     "resting_hr")


def _hae_record_time(record_key):
    """
    The time of day out of a FEED_HAE record key.

    The key is built in parse_apple_records as
    feed|metric|grain|date|HH:MM:SS and this is the only place that reads
    it back; the two belong together. Valid for hae rows ONLY -- the HC
    and retired ios keys are a different shape, which is why the caller
    filters on feed. Returns "" rather than guessing.
    """
    parts = (record_key or "").split("|")
    if len(parts) != 5 or parts[0] != FEED_HAE:
        return ""
    tod = parts[4]
    if len(tod) == 8 and tod[2] == ":" and tod[5] == ":":
        return tod
    return ""


def overnight_metrics(conn, date, source="applewatch",
                      start_ts=None, end_ts=None, metrics=None):
    """
    What the Watch measured DURING the night. Rule S04 Overnight Basis.

    Shown as inputs, not conclusions. Nothing here is scored, ranked, or
    compared with a population figure.

    Each metric comes back with a `basis`:
      "night" -- the mean of the samples whose own timestamps fall inside
                 the sleep span, with n, min and max so the reader can see
                 how much it rests on
      "day"   -- no sample carried a time inside the span, so the day's
                 stored figure is shown AND SAID TO BE a day figure.
                 Apple reports wrist temperature once a night stamped at
                 midnight, which is exactly this case.

    A metric with neither is absent, not zero.
    """
    names = tuple(metrics) if metrics else OVERNIGHT_METRICS
    out = {}

    if start_ts and end_ts:
        days = sorted(set([start_ts[:10], end_ts[:10]]))
        marks = ",".join(["?"] * len(days))
        holes = ",".join(["?"] * len(names))
        rows = conn.execute(
            "SELECT record_key, date, metric, value, unit "
            "FROM health_hc_records "
            "WHERE source = ? AND feed = ? AND date IN (" + marks + ") "
            "AND metric IN (" + holes + ")",
            tuple([source, FEED_HAE] + days + list(names))).fetchall()
        bucket = {}
        for record_key, rdate, metric, value, unit in rows:
            if value is None:
                continue
            tod = _hae_record_time(record_key)
            if not tod:
                continue
            stamp = rdate + " " + tod
            if stamp < start_ts or stamp > end_ts:
                continue
            bucket.setdefault(metric, []).append((float(value), unit))
        for metric in names:
            got = bucket.get(metric)
            if not got:
                continue
            nums = [v for v, u in got]
            out[metric] = {
                "value": sum(nums) / len(nums),
                "n": len(nums),
                "min": min(nums),
                "max": max(nums),
                "unit": got[-1][1],
                "basis": "night",
            }

    missing = [m for m in names if m not in out]
    if missing:
        holes = ",".join(["?"] * len(missing))
        rows = conn.execute(
            "SELECT metric, value, unit FROM health_metrics "
            "WHERE date = ? AND source = ? AND metric IN (" + holes + ")",
            tuple([date, source] + list(missing))).fetchall()
        for metric, value, unit in rows:
            if value is None:
                continue
            out[metric] = {
                "value": float(value),
                "n": None,
                "min": None,
                "max": None,
                "unit": unit,
                "basis": "day",
            }
    return out


def sleep_night(conn, date, source="applewatch"):
    """
    The whole night filed under one date, rebuilt from its blocks.

    Reports what was measured and nothing else. There is no score here
    and no grade: a night is shown, never marked.

    Returns None when nothing was recorded for that date. `awakenings`
    counts the BREAKS BETWEEN recorded sleep blocks, which is a floor,
    not a count of times he woke -- Auto Export's aggregate carries no
    awakening count. `awake_h` is the measured time awake and is exact.
    """
    _ensure_sleep_block_table(conn)
    rows = conn.execute(
        "SELECT block_key, date, start_ts, end_ts, in_bed_start, in_bed_end, "
        "asleep_h, rem_h, core_h, deep_h, awake_h, device "
        "FROM health_sleep_blocks WHERE date = ? AND source = ? "
        "ORDER BY start_ts, block_key", (date, source)).fetchall()
    if not rows:
        return None

    blocks = []
    for r in rows:
        blocks.append({
            "block_key": r[0], "date": r[1], "start_ts": r[2], "end_ts": r[3],
            "in_bed_start": r[4], "in_bed_end": r[5], "asleep_h": r[6],
            "rem_h": r[7], "core_h": r[8], "deep_h": r[9], "awake_h": r[10],
            "device": r[11],
        })

    clusters = cluster_sleep_blocks(blocks)
    starts = [c["start"] for c in clusters if c["start"]]
    ends = [c["end"] for c in clusters if c["end"]]
    bed_starts = [b["in_bed_start"] for b in blocks if b.get("in_bed_start")]
    bed_ends = [b["in_bed_end"] for b in blocks if b.get("in_bed_end")]

    start_ts = min(starts) if starts else None
    end_ts = max(ends) if ends else None
    bed_start = min(bed_starts) if bed_starts else start_ts
    bed_end = max(bed_ends) if bed_ends else end_ts

    asleep = _sum_stage(clusters, "asleep_h")
    return {
        "date": date,
        "source": source,
        "rule": "S03 Sleep Block Identity",
        "blocks": len(blocks),
        "stretches": len(clusters),
        # Breaks BETWEEN recorded blocks. A floor on the number of times
        # he woke, never presented as the number of times he woke.
        "awakenings": max(0, len(clusters) - 1),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "in_bed_start": bed_start,
        "in_bed_end": bed_end,
        "asleep_h": asleep,
        "rem_h": _sum_stage(clusters, "rem_h"),
        "core_h": _sum_stage(clusters, "core_h"),
        "deep_h": _sum_stage(clusters, "deep_h"),
        "awake_h": _sum_stage(clusters, "awake_h"),
        # Summed over the stretches, NOT the span from first to last.
        # See _in_bed_hours: spanning a break would count time he may
        # have spent out of bed as time in bed.
        "in_bed_h": _in_bed_hours(clusters),
        "device": (blocks[0].get("device") or "") if blocks else "",
        # S04. Measured during the night, each carrying the basis it was
        # worked out on. Never a verdict about the night.
        "overnight": overnight_metrics(conn, date, source, start_ts, end_ts),
    }


def store_sleep_blocks(conn, blocks, source, ts, feed=None):
    """
    Upsert one payload's sleep blocks and return the dates they touch.

    The key is the block's SPAN, never its value, so a redelivery of the
    same block updates in place and a night cannot be added to itself.
    """
    if feed is None:
        feed = FEED_HAE
    _ensure_sleep_block_table(conn)
    cur = conn.cursor()
    touched = set()
    for b in blocks or []:
        key = "|".join([feed, source, b.get("date") or "",
                        b.get("start_ts") or "", b.get("end_ts") or ""])
        cur.execute(
            "INSERT INTO health_sleep_blocks "
            "(block_key, date, source, feed, start_ts, end_ts, "
            " in_bed_start, in_bed_end, asleep_h, rem_h, core_h, deep_h, "
            " awake_h, device, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(block_key) DO UPDATE SET "
            "in_bed_start=excluded.in_bed_start, "
            "in_bed_end=excluded.in_bed_end, asleep_h=excluded.asleep_h, "
            "rem_h=excluded.rem_h, core_h=excluded.core_h, "
            "deep_h=excluded.deep_h, awake_h=excluded.awake_h, "
            "device=excluded.device, ingested_at=excluded.ingested_at",
            (key, b.get("date"), source, feed, b.get("start_ts"),
             b.get("end_ts"), b.get("in_bed_start"), b.get("in_bed_end"),
             b.get("asleep_h"), b.get("rem_h"), b.get("core_h"),
             b.get("deep_h"), b.get("awake_h"), b.get("device"), ts),
        )
        if b.get("date"):
            touched.add(b["date"])
    return sorted(touched)


def store_apple(conn, records, workout_rows, source, raw_text,
                sleep_blocks=None):
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

    # S03: sleep_hours is written LAST and from the blocks, not from the
    # generic day recompute. The generic path can only add scalars up; a
    # night needs its spans, so that a re-segmented night is not added to
    # itself and a night delivered in pieces is put back together.
    sleep_dates = store_sleep_blocks(conn, sleep_blocks, source, ts)
    for date in sorted(set(sleep_dates) | set(d for d, m in touched
                                              if m == "sleep_hours")):
        night = sleep_night(conn, date, source)
        if night is None or night.get("asleep_h") is None:
            continue
        _write_daily(cur, date, "sleep_hours", night["asleep_h"], "hr",
                     source, ts)

    conn.commit()
    all_dates = set(d for d, _ in touched) | set(sleep_dates)
    return new_rows, updated_rows, sorted(all_dates)


def store_hc(conn, records, raw_text, source="healthconnect",
             summed=None, workouts=None):
    """
    Insert records under a unique key, then recompute daily totals for
    every affected date. Correct under partial batches, idempotent under
    redelivery.
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
    for key, date, metric, value, unit in records:
        prior = cur.execute(
            "SELECT value FROM health_hc_records WHERE record_key = ?",
            (key,),
        ).fetchone()
        cur.execute(
            "INSERT INTO health_hc_records "
            "(record_key, date, metric, value, unit, ingested_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_key) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, "
            "ingested_at=excluded.ingested_at, source=excluded.source",
            (key, date, metric, value, unit, ts, source),
        )
        if prior is None:
            new_rows = new_rows + 1
        elif prior[0] != value:
            updated_rows = updated_rows + 1
        touched.add((date, metric))

    for row in (workouts or []):
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

    # One shared recompute, pinned to this path's own feed. HC Webhook
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
        _write_daily(cur, date, metric, got[0], got[1], source, ts)

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
    # Route by payload SHAPE. Health Webhook on iOS posts the same array
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

    # Record level, not a single upserted daily figure. store() is left
    # in place for callers that already hold a day view, but nothing on
    # this route may overwrite a day with one payload's worth of it.
    records, workout_rows, skipped = parse_apple_records(payload)
    # S03: the night is rebuilt from its blocks and their spans, not from
    # the one midnight-stamped figure a day used to be.
    sleep_blocks = parse_sleep_blocks(payload)

    conn = _connect()
    try:
        new_rows, updated_rows, dates = store_apple(
            conn, records, workout_rows, source, raw_text,
            sleep_blocks=sleep_blocks)
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
        "sleep_blocks": len(sleep_blocks),
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

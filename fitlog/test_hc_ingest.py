#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - smoke test for the HC Webhook (Health Connect) ingest path.

This exists because test_health_ingest.py does NOT exercise this code.
Every healthconnect case in that suite uses the legacy
{"data":{"metrics":[...]}} shape, so is_hc is false and none of the HC
path runs. It passes 23/23 on broken HC code. Run BOTH suites.

Includes regression tests for two bugs found in review:
  R1  interval redelivered with an updated value must REPLACE, not add
  R2  short distance records must not be stored 1000x too large

Runs against a throwaway temp database. Never touches the live DB.
Python 3.9 compatible.

Usage:
    cd /root/fitlog && python3 test_hc_ingest.py
"""

import os
import sqlite3
import sys
import tempfile

PASS = 0
FAIL = 0
FAILURES = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS = PASS + 1
        print("  ok   " + name)
    else:
        FAIL = FAIL + 1
        FAILURES.append(name + ((" :: " + detail) if detail else ""))
        print("  FAIL " + name + ((" :: " + detail) if detail else ""))


tmpdir = tempfile.mkdtemp(prefix="fitlog_hc_test_")
TEST_DB = os.path.join(tmpdir, "test.db")
TEST_ENV = os.path.join(tmpdir, "ingest.env")
MAIN_TOKEN = "main-token-smoke-only"
HC_TOKEN = "hc-token-smoke-only"

with open(TEST_ENV, "w") as fh:
    fh.write("FITLOG_INGEST_TOKEN=" + MAIN_TOKEN + "\n")
    fh.write("FITLOG_HC_TOKEN=" + HC_TOKEN + "\n")

os.environ["FITLOG_DB"] = TEST_DB
os.environ["FITLOG_INGEST_ENV"] = TEST_ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None)
os.environ.pop("FITLOG_HC_TOKEN", None)

sqlite3.connect(TEST_DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migrate_health_ingest  # noqa: E402

sys.argv = ["migrate", TEST_DB]
migrate_health_ingest.main()

conn = sqlite3.connect(TEST_DB)
conn.execute(
    "CREATE TABLE IF NOT EXISTS health_hc_records ("
    "record_key TEXT PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL, "
    "value REAL, unit TEXT, ingested_at TEXT NOT NULL)"
)
conn.commit()
conn.close()

import health_ingest  # noqa: E402
from flask import Flask  # noqa: E402

if not hasattr(health_ingest, "parse_hc_payload"):
    print("FAIL: health_ingest.py has not been patched for HC support.")
    print("Run patch_hc_support.py first.")
    sys.exit(1)

app = Flask(__name__)
app.register_blueprint(health_ingest.health_ingest_bp)
client = app.test_client()

HC = "/api/ingest?source=healthconnect&k=" + HC_TOKEN
AUTH = {"Authorization": "Bearer " + MAIN_TOKEN}
DAY = "2026-09-11"


def steps_rec(hour, count):
    start = DAY + "T" + ("%02d" % hour) + ":00:00+05:30"
    end = DAY + "T" + ("%02d" % (hour + 1)) + ":00:00+05:30"
    return {"start_time": start, "end_time": end, "count": count}


def payload(key, records):
    body = {"timestamp": DAY + "T23:00:00Z", "app_version": "1.0.0"}
    body[key] = records
    return body


def daily(metric):
    resp = client.get("/api/health/daily?date=" + DAY, headers=AUTH)
    metrics = resp.get_json().get("metrics", {})
    return metrics.get(metric, {}).get("value")


# --- auth scoping ---------------------------------------------------------

print("\n[1] auth scoping of the URL token")

r = client.post("/api/ingest?source=healthconnect", json=payload("steps", []))
check("no token rejected", r.status_code == 401, str(r.status_code))

r = client.post("/api/ingest?source=healthconnect&k=wrong", json=payload("steps", []))
check("wrong token rejected", r.status_code == 401, str(r.status_code))

r = client.post("/api/ingest?source=applewatch&k=" + HC_TOKEN, json={"data": {}})
check("url token cannot post as applewatch", r.status_code == 401, str(r.status_code))

r = client.get("/api/ingest/status?k=" + HC_TOKEN)
check("url token cannot read status", r.status_code == 401, str(r.status_code))

r = client.get("/api/health/daily?date=" + DAY + "&k=" + HC_TOKEN)
check("url token cannot read daily", r.status_code == 401, str(r.status_code))

# --- basic ingest ---------------------------------------------------------

print("\n[2] record ingest and daily aggregation")

r = client.post(HC, json=payload("steps", [steps_rec(7, 1200), steps_rec(8, 800)]))
body = r.get_json()
check("hc payload accepted", r.status_code == 200, str(r.status_code))
check("detected as hc_webhook format", body.get("format") == "hc_webhook",
      str(body.get("format")))
check("two new records", body.get("records_new") == 2, str(body.get("records_new")))
check("daily total is 2000", daily("steps") == 2000, str(daily("steps")))

# --- R1: the double-count regression -------------------------------------

print("\n[3] R1 - updated interval must replace, not add")

r = client.post(HC, json=payload("steps", [steps_rec(7, 1200), steps_rec(8, 800)]))
body = r.get_json()
check("identical retry adds nothing", body.get("records_new") == 0,
      str(body.get("records_new")))
check("total unchanged after retry", daily("steps") == 2000, str(daily("steps")))

# same interval, grown value - this is HC Webhook's normal behaviour for an
# in-progress interval. The old key included the value, so both rows landed
# and SUM added them: 800 + 1150 = 1950 instead of 1150.
r = client.post(HC, json=payload("steps", [steps_rec(8, 1150)]))
body = r.get_json()
check("grown interval counted as update", body.get("records_updated") == 1,
      str(body.get("records_updated")))
check("grown interval NOT counted as new", body.get("records_new") == 0,
      str(body.get("records_new")))
check("total is 2350 not 3150", daily("steps") == 2350, str(daily("steps")))

conn = sqlite3.connect(TEST_DB)
n_rows = conn.execute(
    "SELECT COUNT(*) FROM health_hc_records WHERE metric='steps'"
).fetchone()[0]
conn.close()
check("one row per interval, not per value", n_rows == 2, str(n_rows))

# --- incremental delivery -------------------------------------------------

print("\n[4] incremental delivery")

r = client.post(HC, json=payload("steps", [steps_rec(9, 500)]))
check("new interval adds", r.get_json().get("records_new") == 1)
check("total is 2850", daily("steps") == 2850, str(daily("steps")))

# --- R2: distance scaling regression -------------------------------------

print("\n[5] R2 - short distance records must not be 1000x too large")

cases = [(50, 0.05), (80, 0.08), (150, 0.15), (1200, 1.2)]
for metres, expected_km in cases:
    start = DAY + "T12:00:00+05:30"
    end = DAY + "T12:30:00+05:30"
    conn = sqlite3.connect(TEST_DB)
    conn.execute("DELETE FROM health_hc_records WHERE metric='distance_km'")
    conn.execute("DELETE FROM health_metrics WHERE metric='distance_km'")
    conn.commit()
    conn.close()
    client.post(HC, json=payload("distance", [{
        "start_time": start, "end_time": end, "value": metres, "unit": "meters",
    }]))
    got = daily("distance_km")
    check(str(metres) + " m stored as " + str(expected_km) + " km",
          got is not None and abs(got - expected_km) < 1e-9, str(got))

# a record that declares kilometres must not be divided again
conn = sqlite3.connect(TEST_DB)
conn.execute("DELETE FROM health_hc_records WHERE metric='distance_km'")
conn.execute("DELETE FROM health_metrics WHERE metric='distance_km'")
conn.commit()
conn.close()
client.post(HC, json=payload("distance", [{
    "start_time": DAY + "T13:00:00+05:30", "end_time": DAY + "T13:30:00+05:30",
    "value": 2.5, "unit": "km",
}]))
check("record declaring km is left alone", daily("distance_km") == 2.5,
      str(daily("distance_km")))

# --- S01 precedence still holds ------------------------------------------

print("\n[6] S01 precedence unaffected")

client.post("/api/ingest?source=applewatch", headers=AUTH, json={"data": {"metrics": [{
    "name": "step_count", "units": "count",
    "data": [{"date": DAY + " 00:00:00 +0530", "qty": 9999}],
}]}})
resp = client.get("/api/health/daily?date=" + DAY, headers=AUTH).get_json()
watch = resp["metrics"]["steps"]
check("watch overrides hc on same date",
      watch["value"] == 9999 and watch["source"] == "applewatch",
      str(watch))

# --- legacy path untouched -----------------------------------------------

print("\n[7] apple path still works")

r = client.post("/api/ingest?source=applewatch", headers=AUTH, json={"data": {"metrics": [{
    "name": "step_count", "units": "count",
    "data": [{"date": "2026-09-12 00:00:00 +0530", "qty": 5000}],
}]}})
check("legacy payload still parses", r.get_json().get("metrics_stored") == 1,
      str(r.get_json()))

r = client.post("/api/ingest?source=healthconnect&k=" + HC_TOKEN,
                json={"data": {"metrics": [{
                    "name": "steps", "units": "count",
                    "data": [{"date": "2026-09-13 00:00:00 +0530", "qty": 700}],
                }]}})
check("legacy shape from hc still routes to old parser",
      r.get_json().get("metrics_stored") == 1, str(r.get_json()))

# --- added in review: gaps the sections above leave open ------------------

print("\n[8] coverage added in review")

DAY8 = "2026-09-14"


def daily8(metric):
    resp = client.get("/api/health/daily?date=" + DAY8, headers=AUTH)
    metrics = resp.get_json().get("metrics", {})
    return metrics.get(metric, {})


def rec(hour, key, value):
    body = {"start_time": DAY8 + "T" + ("%02d" % hour) + ":00:00+05:30",
            "end_time": DAY8 + "T" + ("%02d" % (hour + 1)) + ":00:00+05:30"}
    body[key] = value
    return body


# Real HC Webhook posts several arrays in one body, not one at a time.
# Sections [2]-[5] only ever send a single array.
multi = {
    "timestamp": DAY8 + "T23:00:00Z",
    "app_version": "1.0.0",
    "steps": [rec(7, "count", 300)],
    "floors_climbed": [rec(7, "count", 4)],
    "active_calories_burned": [rec(7, "value", 88)],
}
r = client.post(HC, json=multi)
body = r.get_json()
check("multi-array payload accepted", r.status_code == 200, str(r.status_code))
check("all three arrays parsed", body.get("records_seen") == 3,
      str(body.get("records_seen")))

# floors_climbed -> flights is RULE-BEARING and was never exercised.
check("floors_climbed maps to flights", daily8("flights").get("value") == 4,
      str(daily8("flights")))
check("flights flagged rule-bearing",
      daily8("flights").get("rule_bearing") is True,
      str(daily8("flights")))
check("active_calories_burned maps to active_energy_kcal",
      daily8("active_energy_kcal").get("value") == 88,
      str(daily8("active_energy_kcal")))
check("hc-derived steps flagged rule-bearing",
      daily8("steps").get("rule_bearing") is True, str(daily8("steps")))

# An exact retry must report zero updates, not just zero new rows. Only
# records_new was asserted before, so a spurious update would pass unseen.
r = client.post(HC, json=multi)
check("exact retry reports zero updates",
      r.get_json().get("records_updated") == 0,
      str(r.get_json().get("records_updated")))

# Negative controls.
r = client.post(HC, json=payload("steps", [
    {"start_time": "not-a-date", "end_time": "also-not", "count": 5}]))
check("unparseable date skipped, not stored",
      r.status_code == 200 and r.get_json().get("records_seen") == 0,
      str(r.get_json()))

r = client.post(HC, json=payload("steps", ["not-a-dict", 42, None]))
check("malformed records skipped without crashing",
      r.status_code == 200 and r.get_json().get("records_seen") == 0,
      str(r.get_json()))

r = client.post(HC, json=payload("heart_rate", [rec(8, "count", 70)]))
check("array outside HC_ARRAY_MAP ignored",
      r.status_code == 200 and r.get_json().get("records_seen") == 0,
      str(r.get_json()))

conn = sqlite3.connect(TEST_DB)
raw_before = conn.execute(
    "SELECT COUNT(*) FROM health_raw WHERE source='healthconnect'").fetchone()[0]
conn.close()
client.post(HC, json=payload("steps", [rec(9, "count", 10)]))
conn = sqlite3.connect(TEST_DB)
raw_after = conn.execute(
    "SELECT COUNT(*) FROM health_raw WHERE source='healthconnect'").fetchone()[0]
conn.close()
check("every hc body retained in health_raw", raw_after == raw_before + 1,
      str(raw_before) + " -> " + str(raw_after))


# --- report ---------------------------------------------------------------

total = PASS + FAIL
print("\n" + "=" * 52)
print("HC RESULT: " + str(PASS) + "/" + str(total) + " passed")
if FAILURES:
    for f in FAILURES:
        print("  - " + f)
print("=" * 52)
print("temp dir: " + tmpdir + "  (safe to delete)")

sys.exit(0 if FAIL == 0 else 1)

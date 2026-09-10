#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - on-server smoke test for the wearable ingest blueprint.

Runs against a throwaway temp database. Never touches the live DB.
Must pass 18/18 before systemctl restart.

Python 3.9 compatible.

Usage:
    cd /root/fitlog && python3 test_health_ingest.py
"""

import json
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
        FAILURES.append(name + (" :: " + detail if detail else ""))
        print("  FAIL " + name + ((" :: " + detail) if detail else ""))


# --- isolate before importing the blueprint -------------------------------

tmpdir = tempfile.mkdtemp(prefix="fitlog_ingest_test_")
TEST_DB = os.path.join(tmpdir, "test.db")
TEST_ENV = os.path.join(tmpdir, "ingest.env")
TOKEN = "smoke-test-token-do-not-use-in-production"

with open(TEST_ENV, "w") as fh:
    fh.write("# test token\n")
    fh.write("FITLOG_INGEST_TOKEN=" + TOKEN + "\n")

os.environ["FITLOG_DB"] = TEST_DB
os.environ["FITLOG_INGEST_ENV"] = TEST_ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None)

sqlite3.connect(TEST_DB).close()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migrate_health_ingest  # noqa: E402
import health_ingest  # noqa: E402
from flask import Flask  # noqa: E402


# --- build schema ---------------------------------------------------------

print("\n[1] migration")
rc = migrate_health_ingest.main.__wrapped__ if False else None
sys.argv = ["migrate", TEST_DB]
rc = migrate_health_ingest.main()
check("migration returns 0", rc == 0, "rc=" + str(rc))

conn = sqlite3.connect(TEST_DB)
tables = set(
    r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
)
conn.close()
check("health_metrics created", "health_metrics" in tables)
check("health_workouts created", "health_workouts" in tables)
check("health_raw created", "health_raw" in tables)

sys.argv = ["migrate", TEST_DB]
rc2 = migrate_health_ingest.main()
check("migration is idempotent", rc2 == 0, "rc=" + str(rc2))


# --- app under test -------------------------------------------------------

app = Flask(__name__)
app.register_blueprint(health_ingest.health_ingest_bp)
client = app.test_client()

AUTH = {"Authorization": "Bearer " + TOKEN}

WATCH_PAYLOAD = {
    "data": {
        "metrics": [
            {
                "name": "step_count",
                "units": "count",
                "data": [
                    {"date": "2026-09-08 00:00:00 +0530", "qty": 9120},
                    {"date": "2026-09-09 00:00:00 +0530", "qty": 11430},
                ],
            },
            {
                "name": "resting_heart_rate",
                "units": "count/min",
                "data": [{"date": "2026-09-09 00:00:00 +0530", "qty": 61}],
            },
            {
                "name": "sleep_analysis",
                "units": "hr",
                "data": [{
                    "date": "2026-09-09 00:00:00 +0530",
                    "deep": 1.1, "core": 3.4, "rem": 1.2, "awake": 0.3,
                }],
            },
            {
                "name": "environmental_audio_exposure",
                "units": "dBASPL",
                "data": [{"date": "2026-09-09 00:00:00 +0530", "qty": 68}],
            },
        ],
        "workouts": [{
            "name": "Walking",
            "start": "2026-09-09 07:10:00 +0530",
            "end": "2026-09-09 07:44:00 +0530",
            "duration": 2040,
            "activeEnergyBurned": {"qty": 132, "units": "kcal"},
            "avgHeartRate": {"qty": 104, "units": "count/min"},
        }],
    }
}

PHONE_PAYLOAD = {
    "data": {
        "metrics": [
            {
                "name": "steps",
                "units": "count",
                "data": [
                    {"date": "2026-09-09 00:00:00 +0530", "qty": 4200},
                    {"date": "2026-09-10 00:00:00 +0530", "qty": 6600},
                ],
            }
        ]
    }
}

print("\n[2] auth")
r = client.post("/api/ingest?source=applewatch", json=WATCH_PAYLOAD)
check("rejects missing token", r.status_code == 401, str(r.status_code))

r = client.post(
    "/api/ingest?source=applewatch",
    json=WATCH_PAYLOAD,
    headers={"Authorization": "Bearer wrong-token"},
)
check("rejects wrong token", r.status_code == 401, str(r.status_code))

r = client.get("/api/health/daily?date=2026-09-09")
check("daily endpoint is gated", r.status_code == 401, str(r.status_code))

print("\n[3] validation")
r = client.post("/api/ingest?source=fitbit", json=WATCH_PAYLOAD, headers=AUTH)
check("rejects unknown source", r.status_code == 400, str(r.status_code))

r = client.post(
    "/api/ingest?source=applewatch",
    data="{not json",
    headers=AUTH,
    content_type="application/json",
)
check("rejects malformed json", r.status_code == 400, str(r.status_code))

print("\n[4] ingest")
r = client.post("/api/ingest?source=applewatch", json=WATCH_PAYLOAD, headers=AUTH)
body = r.get_json()
check("watch ingest accepted", r.status_code == 200, str(r.status_code))
check("stored 4 metric rows", body.get("metrics_stored") == 4,
      str(body.get("metrics_stored")))
check("stored 1 workout", body.get("workouts_stored") == 1,
      str(body.get("workouts_stored")))
check("non-whitelisted metric skipped",
      "environmental_audio_exposure" in (body.get("skipped_metrics") or []))

conn = sqlite3.connect(TEST_DB)
sleep_val = conn.execute(
    "SELECT value FROM health_metrics WHERE metric='sleep_hours' AND date='2026-09-09'"
).fetchone()
conn.close()
check("sleep phases summed to 5.7h",
      sleep_val is not None and abs(sleep_val[0] - 5.7) < 0.001,
      str(sleep_val))

print("\n[5] idempotency")
client.post("/api/ingest?source=applewatch", json=WATCH_PAYLOAD, headers=AUTH)
client.post("/api/ingest?source=applewatch", json=WATCH_PAYLOAD, headers=AUTH)
conn = sqlite3.connect(TEST_DB)
n_steps = conn.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE metric='steps' AND source='applewatch'"
).fetchone()[0]
n_workouts = conn.execute("SELECT COUNT(*) FROM health_workouts").fetchone()[0]
n_raw = conn.execute("SELECT COUNT(*) FROM health_raw").fetchone()[0]
conn.close()
check("repeat POST does not duplicate metrics", n_steps == 2, str(n_steps))
check("repeat POST does not duplicate workouts", n_workouts == 1, str(n_workouts))
check("every payload retained in health_raw", n_raw == 3, str(n_raw))

print("\n[6] S01 source precedence")
client.post("/api/ingest?source=healthconnect", json=PHONE_PAYLOAD, headers=AUTH)

r = client.get("/api/health/daily?date=2026-09-09", headers=AUTH)
day = r.get_json()["metrics"]
check("watch wins on contested date",
      day["steps"]["source"] == "applewatch" and day["steps"]["value"] == 11430,
      json.dumps(day.get("steps")))

r = client.get("/api/health/daily?date=2026-09-10", headers=AUTH)
day10 = r.get_json()["metrics"]
check("phone fills gap where watch is silent",
      day10["steps"]["source"] == "healthconnect" and day10["steps"]["value"] == 6600,
      json.dumps(day10.get("steps")))

check("steps flagged rule-bearing", day["steps"]["rule_bearing"] is True)
check("resting hr flagged context-only",
      day["resting_hr"]["rule_bearing"] is False)

print("\n[7] status")
r = client.get("/api/ingest/status", headers=AUTH)
srcs = dict((s["source"], s) for s in r.get_json()["sources"])
check("status lists both sources",
      "applewatch" in srcs and "healthconnect" in srcs, json.dumps(list(srcs)))

# --- report ---------------------------------------------------------------

total = PASS + FAIL
print("\n" + "=" * 52)
print("RESULT: " + str(PASS) + "/" + str(total) + " passed")
if FAILURES:
    for f in FAILURES:
        print("  - " + f)
print("=" * 52)
print("temp dir: " + tmpdir + "  (safe to delete)")

sys.exit(0 if FAIL == 0 else 1)

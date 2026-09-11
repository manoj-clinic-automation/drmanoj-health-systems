#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - smoke test for the iOS Health Webhook ingest path.

The headline case is the UTC boundary: iOS posts "...T18:30:00.000Z",
which is midnight IST the NEXT day. Filing it by the first ten
characters puts every record a day early, silently.

Runs against a throwaway temp database.
Python 3.9 compatible.
"""
import os, sqlite3, sys, tempfile

PASS = 0; FAIL = 0; FAILURES = []
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   " + name)
    else:
        FAIL += 1; FAILURES.append(name); print("  FAIL " + name + ((" :: " + detail) if detail else ""))

tmp = tempfile.mkdtemp(prefix="fitlog_ios_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
TOK = "main-token-ios-smoke"
open(ENV, "w").write("FITLOG_INGEST_TOKEN=" + TOK + "\nFITLOG_HC_TOKEN=hc-smoke\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()
c = sqlite3.connect(DB)
c.execute("CREATE TABLE IF NOT EXISTS health_hc_records (record_key TEXT PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL, value REAL, unit TEXT, ingested_at TEXT NOT NULL)")
c.commit(); c.close()
import health_ingest
from flask import Flask
if not hasattr(health_ingest, "parse_ios_payload"):
    print("FAIL: not patched for iOS. Run patch_ios_payload.py"); sys.exit(1)
app = Flask(__name__); app.register_blueprint(health_ingest.health_ingest_bp)
cl = app.test_client()
A = {"Authorization": "Bearer " + TOK}
P = "/api/ingest?source=applewatch"

def daily(date, metric):
    m = cl.get("/api/health/daily?date=" + date, headers=A).get_json()["metrics"]
    return m.get(metric, {}).get("value")

print("\n[1] UTC to IST date boundary")
body = {"platform": "ios", "app_version": "1.1.2", "timestamp": "2026-09-11T11:26:04.324Z",
        "steps": [{"start_time": "2026-09-08T18:30:00.000Z", "end_time": "2026-09-09T18:30:00.000Z", "count": 439}]}
r = cl.post(P, headers=A, json=body); b = r.get_json()
check("ios payload accepted", r.status_code == 200, str(r.status_code))
check("detected as ios format", b.get("format") == "ios_health_webhook", str(b.get("format")))
check("18:30Z files as NEXT IST day", b.get("dates") == ["2026-09-09"], str(b.get("dates")))
check("value on 09-09", daily("2026-09-09", "steps") == 439, str(daily("2026-09-09","steps")))
check("nothing on 09-08", daily("2026-09-08", "steps") is None, str(daily("2026-09-08","steps")))

print("\n[2] units and aggregation")
cl.post(P, headers=A, json={"platform":"ios","distance":[
    {"start_time":"2026-09-09T04:00:00.000Z","end_time":"2026-09-09T05:00:00.000Z","meters":302.8195897503756}]})
d = daily("2026-09-09","distance_km")
check("metres to km", d is not None and abs(d-0.3028195897503756)<1e-9, str(d))
cl.post(P, headers=A, json={"platform":"ios","steps":[
    {"start_time":"2026-09-09T04:00:00.000Z","end_time":"2026-09-09T05:00:00.000Z","count":100},
    {"start_time":"2026-09-09T05:00:00.000Z","end_time":"2026-09-09T06:00:00.000Z","count":250}]})
check("steps summed with earlier record", daily("2026-09-09","steps") == 789, str(daily("2026-09-09","steps")))

print("\n[3] heart rate averaged, not summed")
cl.post(P, headers=A, json={"platform":"ios","heart_rate":[
    {"time":"2026-09-09T04:00:00.000Z","bpm":80},{"time":"2026-09-09T05:00:00.000Z","bpm":100}]})
check("hr averaged to 90", daily("2026-09-09","hr") == 90, str(daily("2026-09-09","hr")))
check("hr is context-only", cl.get("/api/health/daily?date=2026-09-09",headers=A).get_json()["metrics"]["hr"]["rule_bearing"] is False)

print("\n[4] activity rings use their own local date")
cl.post(P, headers=A, json={"platform":"ios","activity_rings":[
    {"date":"2026-09-09","stand_hours":7,"exercise_minutes":0,"move_kilocalories":124.7,
     "stand_goal_hours":12,"exercise_goal_minutes":30,"move_goal_kilocalories":300}]})
check("stand_hours stored", daily("2026-09-09","stand_hours") == 7, str(daily("2026-09-09","stand_hours")))
check("stand_hours rule-bearing", cl.get("/api/health/daily?date=2026-09-09",headers=A).get_json()["metrics"]["stand_hours"]["rule_bearing"] is True)

print("\n[5] idempotency")
before = daily("2026-09-09","steps")
r = cl.post(P, headers=A, json=body)
check("replay adds no records", r.get_json().get("records_new") == 0, str(r.get_json().get("records_new")))
check("total unchanged on replay", daily("2026-09-09","steps") == before, str(daily("2026-09-09","steps")))

print("\n[6] workouts")
r = cl.post(P, headers=A, json={"platform":"ios","exercise":[
    {"start_time":"2026-09-10T16:28:41.272Z","end_time":"2026-09-10T16:36:25.321Z","type":"WALKING",
     "kilocalories":28.696,"distance_meters":234.6,"duration_seconds":464}]})
check("workout stored", r.get_json().get("workouts_stored") == 1, str(r.get_json()))
conn = sqlite3.connect(DB)
w = conn.execute("SELECT date, wtype, distance_km, source FROM health_workouts").fetchone()
conn.close()
check("workout on correct IST date", w is not None and w[0] == "2026-09-10", str(w))
check("workout distance in km", w is not None and abs(w[2]-0.2346)<1e-9, str(w))
check("workout tagged applewatch", w is not None and w[3] == "applewatch", str(w))

print("\n[7] source attribution and S01")
conn = sqlite3.connect(DB)
srcs = set(r[0] for r in conn.execute("SELECT DISTINCT source FROM health_metrics").fetchall())
conn.close()
check("metrics tagged applewatch not healthconnect", srcs == {"applewatch"}, str(srcs))

total = PASS + FAIL
print("\n" + "="*52); print("IOS RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES: print("  - " + f)
print("="*52)
sys.exit(0 if FAIL == 0 else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - regression suite for the Health Auto Export parser.

Fixtures mirror the real payload shapes taken from health_raw on
2026-09-11: id 22 (per-hour / per-minute export) and id 23 (daily
rollup). Both shapes come from the same app and must both parse
correctly.

Guards the two defects fixed by patch_apple_aggregation.py:
  * energy arrives in kJ under a _kcal canonical name,
  * a day's samples used to overwrite each other instead of combining.

And pins the thing that is NOT a defect: stand_hours 19 on 2026-09-11.
apple_stand_hour carries no stood/idle flag, every sample is qty 1, and
19 distinct hours were recorded. A future "fix" that turns 19 into 12
must fail here.

Runs against a throwaway temp database. Python 3.9 compatible.
"""
import os, sqlite3, sys, tempfile

PASS = 0; FAIL = 0; FAILURES = []
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   " + name)
    else:
        FAIL += 1; FAILURES.append(name); print("  FAIL " + name + ((" :: " + detail) if detail else ""))

tmp = tempfile.mkdtemp(prefix="fitlog_apple_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
TOK = "apple-agg-smoke"
open(ENV, "w").write("FITLOG_INGEST_TOKEN=" + TOK + "\nFITLOG_HC_TOKEN=hc-agg\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()
# The record-level table is created by patch_hc_support.py, not by the base
# migration, so every suite that touches the ios path builds it itself.
c = sqlite3.connect(DB)
c.execute("CREATE TABLE IF NOT EXISTS health_hc_records ("
          "record_key TEXT PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL, "
          "value REAL, unit TEXT, ingested_at TEXT NOT NULL)")
c.commit(); c.close()
import health_ingest
from flask import Flask

if not hasattr(health_ingest, "_apple_aggregate"):
    print("FAIL: not patched. Run patch_apple_aggregation.py"); sys.exit(1)

app = Flask(__name__); app.register_blueprint(health_ingest.health_ingest_bp)
cl = app.test_client()
A = {"Authorization": "Bearer " + TOK}
P = "/api/ingest?source=applewatch"
WATCH = "Manoj’s Apple Watch"


def metric(date, name):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT value, unit FROM health_metrics "
                       "WHERE date=? AND metric=? AND source='applewatch'",
                       (date, name)).fetchone()
    conn.close()
    return row


def hourly(day, h0, h1, qty=1, src=""):
    return [{"date": "%s %02d:00:00 +0530" % (day, h), "qty": qty, "source": src}
            for h in range(h0, h1 + 1)]


def minutely(day, hh, m0, m1, qty=1, src=WATCH):
    return [{"date": "%s %02d:%02d:00 +0530" % (day, hh, m), "qty": qty, "source": src}
            for m in range(m0, m1 + 1)]


def post(metrics):
    return cl.post(P, headers=A, json={"data": {"metrics": metrics}})


print("\n[1] per-hour apple_stand_hour - the real id 22 shape")
# 2026-09-11 recorded hours 05:00 through 23:00 inclusive = 19 samples,
# every one qty 1. There is no stood/idle flag in this payload.
r = post([{"name": "apple_stand_hour", "units": "count",
           "data": hourly("2026-09-11", 5, 23)}])
check("accepted", r.status_code == 200, str(r.status_code))
got = metric("2026-09-11", "stand_hours")
check("19 hourly samples sum to 19", got is not None and got[0] == 19, str(got))
check("NOT 1 (last-sample-wins regression)", got is not None and got[0] != 1, str(got))
check("NOT 12 (do not 'correct' a real 19)", got is not None and got[0] != 12, str(got))

print("\n[2] per-minute apple_exercise_time")
r = post([{"name": "apple_exercise_time", "units": "min",
           "data": (minutely("2026-09-11", 5, 57, 58)
                    + minutely("2026-09-11", 7, 11, 20)
                    + minutely("2026-09-11", 16, 25, 29))}])
got = metric("2026-09-11", "exercise_minutes")
check("2 + 10 + 5 minutes sum to 17", got is not None and got[0] == 17, str(got))

print("\n[3] energy: kJ converted to kcal")
r = post([{"name": "active_energy", "units": "kJ", "data": [
    {"date": "2026-09-09 00:00:00 +0530", "qty": 521.8159279999999, "source": WATCH},
    {"date": "2026-09-11 00:00:00 +0530", "qty": 1845.1272639999993, "source": WATCH}]}])
g9 = metric("2026-09-09", "active_energy_kcal")
g11 = metric("2026-09-11", "active_energy_kcal")
# 521.8159 kJ / 4.184 is 124.717, exactly what the ios ring reported for
# that day as move_kilocalories. That cross-check is why we trust 4.184.
check("09-09 converts to the ios ring's 124.717",
      g9 is not None and abs(g9[0] - 124.71699999999986) < 1e-6, str(g9))
check("09-11 converts to about 441 kcal",
      g11 is not None and abs(g11[0] - 441.0) < 0.1, str(g11))
check("unit relabelled kcal, not kJ", g11 is not None and g11[1] == "kcal", str(g11))
check("not stored raw as 1845", g11 is not None and g11[0] < 1000, str(g11))

print("\n[4] energy already in kcal is left alone")
post([{"name": "active_energy", "units": "kcal", "data": [
    {"date": "2026-09-08 00:00:00 +0530", "qty": 300.0, "source": WATCH}]}])
g8 = metric("2026-09-08", "active_energy_kcal")
check("300 kcal stays 300", g8 is not None and abs(g8[0] - 300.0) < 1e-9, str(g8))
check("unit kcal", g8 is not None and g8[1] == "kcal", str(g8))

print("\n[5] basal energy converts too")
post([{"name": "basal_energy_burned", "units": "kJ", "data": [
    {"date": "2026-09-11 00:00:00 +0530", "qty": 7260.402, "source": WATCH}]}])
gb = metric("2026-09-11", "basal_energy_kcal")
check("7260 kJ becomes about 1735 kcal",
      gb is not None and abs(gb[0] - 1735.3) < 0.5, str(gb))

print("\n[6] levels are averaged, never summed")
post([{"name": "resting_heart_rate", "units": "count/min", "data": [
    {"date": "2026-09-11 06:10:48 +0530", "qty": 89, "source": WATCH},
    {"date": "2026-09-11 12:10:48 +0530", "qty": 91, "source": WATCH},
    {"date": "2026-09-11 20:10:48 +0530", "qty": 90, "source": WATCH}]}])
gh = metric("2026-09-11", "resting_hr")
check("89/91/90 average to 90", gh is not None and abs(gh[0] - 90.0) < 1e-9, str(gh))
check("not summed to 270", gh is not None and gh[0] != 270, str(gh))

print("\n[7] steps sum across many samples")
post([{"name": "step_count", "units": "count", "data": [
    {"date": "2026-09-11 07:00:00 +0530", "qty": 1200, "source": WATCH},
    {"date": "2026-09-11 13:00:00 +0530", "qty": 2500, "source": WATCH},
    {"date": "2026-09-11 19:00:00 +0530", "qty": 1214, "source": WATCH}]}])
gs = metric("2026-09-11", "steps")
check("1200 + 2500 + 1214 = 4914", gs is not None and gs[0] == 4914, str(gs))

print("\n[8] the daily-rollup shape - the real id 23 shape")
# Same app, one sample per day already aggregated.
post([{"name": "apple_stand_hour", "units": "count", "data": [
    {"date": "2026-09-09 00:00:00 +0530", "qty": 7, "source": ""},
    {"date": "2026-09-10 00:00:00 +0530", "qty": 11, "source": ""},
    {"date": "2026-09-11 00:00:00 +0530", "qty": 19, "source": ""}]}])
check("rollup 09-09 stays 7", metric("2026-09-09", "stand_hours")[0] == 7)
check("rollup 09-10 stays 11", metric("2026-09-10", "stand_hours")[0] == 11)
check("rollup 09-11 stays 19", metric("2026-09-11", "stand_hours")[0] == 19)
check("rollup agrees with the per-hour total",
      metric("2026-09-11", "stand_hours")[0] == 19)

print("\n[9] flights - single count sample")
post([{"name": "flights_climbed", "units": "count", "data": [
    {"date": "2026-09-11 05:58:00 +0530", "qty": 1, "source": WATCH}]}])
gf = metric("2026-09-11", "flights")
check("1 flight stays 1", gf is not None and gf[0] == 1, str(gf))

print("\n[10] idempotency - replaying a payload does not double it")
body = [{"name": "apple_stand_hour", "units": "count",
         "data": hourly("2026-09-07", 8, 19)}]
post(body)
first = metric("2026-09-07", "stand_hours")[0]
post(body)
second = metric("2026-09-07", "stand_hours")[0]
check("12 hours parse to 12", first == 12, str(first))
check("replay leaves it at 12, not 24", second == 12, str(second))

print("\n[11] rule-bearing flags unchanged")
m = cl.get("/api/health/daily?date=2026-09-11", headers=A).get_json()["metrics"]
check("stand_hours rule-bearing", m["stand_hours"]["rule_bearing"] is True)
check("exercise_minutes rule-bearing", m["exercise_minutes"]["rule_bearing"] is True)
check("flights rule-bearing", m["flights"]["rule_bearing"] is True)
check("active energy context-only", m["active_energy_kcal"]["rule_bearing"] is False)
check("resting hr context-only", m["resting_hr"]["rule_bearing"] is False)

print("\n[12] the ios snake_case path is untouched")
r = cl.post(P, headers=A, json={"platform": "ios", "steps": [
    {"start_time": "2026-09-05T18:30:00.000Z",
     "end_time": "2026-09-06T18:30:00.000Z", "count": 555}]})
b = r.get_json()
check("ios payload still routes to its own parser",
      b.get("format") == "ios_health_webhook", str(b.get("format")))
check("ios value lands on the IST date", b.get("dates") == ["2026-09-06"], str(b.get("dates")))

total = PASS + FAIL
print("\n" + "="*52); print("APPLE AGG RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES: print("  - " + f)
print("="*52)
sys.exit(0 if FAIL == 0 else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - the calendar day an Auto Export workout is filed under.

Guards patch_workout_day_ist.py. IST is UTC+5:30, so slicing [:10] off a
'Z' stamp files anything starting before 05:30 IST under the previous
day. Nothing in the existing ingest suites enters that branch: every
workout fixture they carry uses a '+0530' stamp, for which slicing
happens to be right (CLAUDE.md rule 2).

Section [4] is the negative control - it shows the old slicing expression
still gets the same fixture wrong, so a pass here means something.

Synthetic fixture, throwaway temp database. Python 3.9 compatible.
"""
import os, sqlite3, sys, tempfile

PASS = 0; FAIL = 0; FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   " + name)
    else:
        FAIL += 1; FAILURES.append(name)
        print("  FAIL " + name + ((" :: " + detail) if detail else ""))


tmp = tempfile.mkdtemp(prefix="fitlog_wkday_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
TOK = "wkday-smoke"
open(ENV, "w").write("FITLOG_INGEST_TOKEN=" + TOK + "\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()
import health_ingest
from flask import Flask

app = Flask(__name__); app.register_blueprint(health_ingest.health_ingest_bp)
cl = app.test_client()
A = {"Authorization": "Bearer " + TOK}
P = "/api/ingest?source=applewatch"


def post_workout(start, end, name="Outdoor Walk", secs=1800):
    return cl.post(P, headers=A, json={"data": {"workouts": [{
        "name": name, "start": start, "end": end, "duration": secs}]}})


def stored(start_ts):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT date, start_ts FROM health_workouts "
                       "WHERE start_ts=?", (start_ts,)).fetchone()
    conn.close()
    return row


print("\n[1] a walk before 05:30 IST keeps its own day")
# 23:30Z on the 15th is 05:00 IST on the 16th. Slicing files it under
# the 15th - a day he did not walk - and the 16th shows nothing.
S1 = "2026-01-15T23:30:00.000Z"
r = post_workout(S1, "2026-01-15T23:59:00.000Z")
check("accepted", r.status_code == 200, str(r.status_code))
g1 = stored(S1)
check("row stored", g1 is not None, str(g1))
check("filed under 2026-01-16, the IST day",
      g1 is not None and g1[0] == "2026-01-16", str(g1))
check("NOT under the UTC 2026-01-15",
      g1 is not None and g1[0] != "2026-01-15", str(g1))

print("\n[2] a Z stamp that does not cross the boundary stays put")
S2 = "2026-01-15T01:41:29.553Z"          # 07:11 IST, same day
r = post_workout(S2, "2026-01-15T02:11:29.553Z")
g2 = stored(S2)
check("07:11 IST files under 2026-01-15",
      g2 is not None and g2[0] == "2026-01-15", str(g2))

print("\n[3] a '+0530' stamp behaves exactly as before")
S3 = "2026-01-15 07:05:00 +0530"
r = post_workout(S3, "2026-01-15 07:45:00 +0530")
g3 = stored(S3)
check("local stamp still files under its own day",
      g3 is not None and g3[0] == "2026-01-15", str(g3))
check("stamp is stored as delivered, not rewritten",
      g3 is not None and g3[1] == S3, str(g3))

print("\n[4] NEGATIVE CONTROL - the expression that was there before")
# _parse_date is still in the module. Run the same fixture through it.
old = health_ingest._parse_date(S1)
check("the old slicing expression still gets 2026-01-15",
      old == "2026-01-15", str(old))
check("...which is not what the row says",
      g1 is not None and old != g1[0], str(old) + " vs " + str(g1))
check("the old expression is right for a '+0530' stamp",
      health_ingest._parse_date(S3) == "2026-01-15",
      str(health_ingest._parse_date(S3)))

print("\n[5] metrics were never affected - confirm they still are not")
# _apple_samples dates METRICS from point['date'], which Auto Export
# sends as '+0530'. Nothing here should have moved that.
r = cl.post(P, headers=A, json={"data": {"metrics": [{
    "name": "step_count", "units": "count",
    "data": [{"date": "2026-01-15 06:00:00 +0530", "qty": 500}]}]}})
conn = sqlite3.connect(DB)
row = conn.execute("SELECT date, value FROM health_metrics "
                   "WHERE metric='steps'").fetchone()
conn.close()
check("a 06:00 IST step sample stays on 2026-01-15",
      row is not None and row[0] == "2026-01-15", str(row))

print("\n[6] replaying the same body does not duplicate the workout")
post_workout(S1, "2026-01-15T23:59:00.000Z")
conn = sqlite3.connect(DB)
n = conn.execute("SELECT COUNT(*) FROM health_workouts "
                 "WHERE start_ts=?", (S1,)).fetchone()[0]
conn.close()
check("still one row", n == 1, str(n))

total = PASS + FAIL
print("\n" + "=" * 52)
print("WORKOUT DAY RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES:
    print("  - " + f)
print("=" * 52)
print("temp dir: " + tmp + "  (safe to delete)")
sys.exit(0 if FAIL == 0 else 1)

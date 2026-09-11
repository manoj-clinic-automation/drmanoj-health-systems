#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - smoke test for the read-only Apple Watch page at /watch.

Server suites never run page JS and never render a page, which is how
GutLog v3.4.0 shipped with two deleted functions at 18/18. This suite
actually renders /watch and asserts on the HTML, so a broken f-string,
a missing column or a None that reaches float() fails here instead of
on the owner's phone.

Also guards the two standing constraints: the page is read-only, and
Health Connect data is parked, not deleted.

Runs against a throwaway temp database. Python 3.9 compatible.
"""
import os, sqlite3, sys, tempfile
from datetime import date, timedelta

PASS = 0; FAIL = 0; FAILURES = []
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   " + name)
    else:
        FAIL += 1; FAILURES.append(name); print("  FAIL " + name + ((" :: " + detail) if detail else ""))

tmp = tempfile.mkdtemp(prefix="fitlog_watch_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
open(ENV, "w").write("FITLOG_INGEST_TOKEN=watch-smoke\nFITLOG_HC_TOKEN=hc-smoke\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()

D0 = date.today()
def dstr(n): return (D0 - timedelta(days=n)).isoformat()

con = sqlite3.connect(DB)
# Apple Watch: three days of the full metric set, including ring goals.
seed = [
    (dstr(0), "steps", 3874, "count"), (dstr(1), "steps", 2305, "count"),
    (dstr(2), "steps", 439, "count"),
    (dstr(0), "active_energy_kcal", 319.157, "kcal"),
    (dstr(1), "active_energy_kcal", 257.294, "kcal"),
    (dstr(0), "exercise_minutes", 17, "min"), (dstr(1), "exercise_minutes", 11, "min"),
    (dstr(0), "stand_hours", 12, "count"), (dstr(1), "stand_hours", 11, "count"),
    (dstr(0), "resting_hr", 90, "count/min"), (dstr(1), "resting_hr", 91, "count/min"),
    (dstr(0), "hrv_ms", 24.3764, "ms"), (dstr(1), "hrv_ms", 20.8513, "ms"),
    (dstr(0), "move_energy_kcal", 319.157, "kcal"),
    (dstr(0), "move_goal_kcal", 300, "kcal"),
    (dstr(0), "exercise_goal_min", 30, "min"),
    (dstr(0), "stand_goal_hours", 12, "count"),
]
for d, m, v, u in seed:
    con.execute("INSERT INTO health_metrics (date, metric, value, unit, source, ingested_at) "
                "VALUES (?,?,?,?,'applewatch','2026-09-11 00:00:00')", (d, m, v, u))
# Health Connect: parked, must survive and must be shown as parked.
con.execute("INSERT INTO health_metrics (date, metric, value, unit, source, ingested_at) "
            "VALUES (?,'steps',1206,'count','healthconnect','2026-09-10 12:00:00')", (dstr(1),))
con.execute("INSERT INTO health_workouts (date, start_ts, end_ts, wtype, duration_s, "
            "energy_kcal, distance_km, avg_hr, max_hr, source, ingested_at) "
            "VALUES (?,?,?,'WALKING',627,23.84,0.375,NULL,NULL,'applewatch','2026-09-11 00:00:00')",
            (dstr(0), dstr(0) + "T10:00:00.000Z", dstr(0) + "T10:10:27.000Z"))
# A workout with null distance/energy: the renderer must not crash on None.
con.execute("INSERT INTO health_workouts (date, start_ts, end_ts, wtype, duration_s, "
            "energy_kcal, distance_km, avg_hr, max_hr, source, ingested_at) "
            "VALUES (?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,'applewatch','2026-09-11 00:00:00')",
            (dstr(2), dstr(2) + "T08:00:00.000Z", dstr(2) + "T08:05:00.000Z"))
con.commit(); con.close()

import app as fitlog
if not hasattr(fitlog, "watch_view"):
    print("FAIL: watch page not patched in. Run patch_watch_page.py"); sys.exit(1)
with fitlog.app.app_context():
    fitlog.set_setting("password_hash", fitlog.sha("pw"))
    fitlog.set_setting("owner_hash", fitlog.sha("ok"))
fitlog.app.config["TESTING"] = True
cl = fitlog.app.test_client()

def counts():
    c = sqlite3.connect(DB)
    out = tuple(c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                for t in ("health_metrics", "health_workouts", "health_raw"))
    c.close()
    return out

print("\n[1] auth gate")
r = cl.get("/watch")
check("unauthenticated is redirected", r.status_code == 302, str(r.status_code))
check("redirect goes to login", "/login" in (r.headers.get("Location") or ""),
      str(r.headers.get("Location")))

with cl.session_transaction() as s:
    s["auth"] = True

print("\n[2] page renders")
before = counts()
r = cl.get("/watch")
html = r.get_data(as_text=True)
check("200 OK", r.status_code == 200, str(r.status_code))
check("no traceback in body", "Traceback" not in html)
check("titled Apple Watch", "<h1>Apple Watch</h1>" in html)

print("\n[3] activity rings against goals")
check("rings section present", "Activity rings" in html)
check("ring svg drawn", "stroke-dasharray=" in html)
check("move goal 300 shown", "/ 300 kcal" in html, "goal missing")
check("exercise goal 30 shown", "/ 30 min" in html)
check("stand goal 12 shown", "/ 12 h" in html)
check("ring percentage rendered", "106%" in html, "319/300 should read 106%")

print("\n[4] recent days")
check("recent days section", "Recent days" in html)
check("steps value shown", ">3874<" in html)
check("hrv rendered to 1dp", ">24.4<" in html, "hrv 24.3764 -> 24.4")
check("missing day is a dash not a zero", "—" in html)

print("\n[5] rule-bearing vs context-only")
check("legend explains R", "rule-bearing" in html)
check("legend explains C", "context-only" in html)
check("R badge rendered", 'class="wr"' in html)
check("C badge rendered", 'class="wc"' in html)
check("steps listed as rule-bearing", "steps" in html and "exercise_minutes" in html)
check("says no F-rule consumes ingested data", "No F-rule consumes an ingested metric" in html)

print("\n[6] workouts")
check("workouts section", "Workouts" in html)
check("walking workout listed", "Walking" in html)
check("duration in minutes", "10 min" in html, "627s -> 10 min")
check("distance in km", "0.38 km" in html or "0.375 km" in html)
check("null workout did not crash render", "Unknown" in html)

print("\n[7] trend")
check("trend section", "Trend" in html)
check("trend window is 28 days", "last 28 days" in html)
check("bars drawn", 'class="wbar"' in html)
check("summary table has Mean", "<th>Mean</th>" in html)

print("\n[8] sources: Samsung parked, not deleted")
check("sources section", "Sources" in html)
check("applewatch shown active", "active" in html)
check("healthconnect still listed", "healthconnect" in html)
check("marked parked", "parked" in html)
check("S01 precedence explained", "never summed" in html)
c = sqlite3.connect(DB)
hc = c.execute("SELECT COUNT(*) FROM health_metrics WHERE source='healthconnect'").fetchone()[0]
c.close()
check("healthconnect rows untouched", hc == 1, str(hc))

print("\n[9] read-only")
cl.get("/watch"); cl.get("/watch")
check("no rows written by rendering", counts() == before, str(before) + " -> " + str(counts()))

print("\n[10] switcher bar")
check("nav carries Watch link", 'href="/watch"' in html)
check("switcher bar intact", "rx.dr-manoj.in" in html and "health.dr-manoj.in" in html)

total = PASS + FAIL
print("\n" + "="*52); print("WATCH PAGE RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES: print("  - " + f)
print("="*52)
sys.exit(0 if FAIL == 0 else 1)

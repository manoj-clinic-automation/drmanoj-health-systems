#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - suite for recompute_apple_daily.py.

Builds a database in the shape the live one was left in:

  * an ios Health Webhook body in health_raw carrying steps and
    distance for 2026-09-09, from the feed that has since been retired,
  * Auto Export bodies for 09-09..09-11 - a daily rollup plus the
    per-hour batch for 09-11,
  * health_metrics holding what the old last-payload-wins code left:
    a stale ios distance, a partial 09-11 step count, and a
    total_energy_kcal row for a metric that has been dropped.

Then runs the script and checks it rebuilds the day from the raw bodies
without ever summing one quantity twice.

Python 3.9 compatible.
"""
import json, os, sqlite3, subprocess, sys, tempfile

PASS = 0; FAIL = 0; FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   " + name)
    else:
        FAIL += 1; FAILURES.append(name)
        print("  FAIL " + name + ((" :: " + detail) if detail else ""))


HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp(prefix="fitlog_recompute_test_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
open(ENV, "w").write("FITLOG_INGEST_TOKEN=recompute-smoke\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, HERE)
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()
import health_ingest

if not hasattr(health_ingest, "parse_apple_records"):
    print("FAIL: not patched. Run patch_apple_records.py"); sys.exit(1)

HOURS = [(5, 120), (6, 80), (7, 450), (8, 300), (9, 300), (10, 200),
         (11, 150), (12, 400), (13, 500), (14, 250), (15, 180), (16, 220),
         (17, 300), (18, 350), (19, 410), (20, 300), (21, 150), (22, 120),
         (23, 134)]
DAY_TOTAL = sum(q for _, q in HOURS)          # 4914

# --- the retired ios feed: 09-09 only -------------------------------------
IOS_BODY = {
    "platform": "ios",
    "steps": [
        {"start_time": "2026-09-09T04:30:00.000Z",
         "end_time": "2026-09-09T05:30:00.000Z", "count": 1800},
        {"start_time": "2026-09-09T10:30:00.000Z",
         "end_time": "2026-09-09T11:30:00.000Z", "count": 1200},
    ],
    "distance": [
        {"start_time": "2026-09-09T04:30:00.000Z",
         "end_time": "2026-09-09T05:30:00.000Z", "meters": 1500},
        {"start_time": "2026-09-09T10:30:00.000Z",
         "end_time": "2026-09-09T11:30:00.000Z", "meters": 1000},
    ],
    # A metric only this feed ever carried. Auto Export has no
    # equivalent, so it is the marker for whether the retired bodies
    # were replayed at all.
    "heart_rate": [
        {"start_time": "2026-09-09T04:30:00.000Z",
         "end_time": "2026-09-09T05:30:00.000Z", "bpm": 96},
        {"start_time": "2026-09-09T10:30:00.000Z",
         "end_time": "2026-09-09T11:30:00.000Z", "bpm": 104},
    ],
}

# --- Auto Export: daily rollups for three days ----------------------------
HAE_ROLLUP = {"data": {"metrics": [
    {"name": "step_count", "units": "count", "data": [
        {"date": "2026-09-09 00:00:00 +0530", "qty": 4200},
        {"date": "2026-09-10 00:00:00 +0530", "qty": 5100},
        {"date": "2026-09-11 00:00:00 +0530", "qty": DAY_TOTAL}]},
    {"name": "walking_running_distance", "units": "km", "data": [
        {"date": "2026-09-09 00:00:00 +0530", "qty": 3.1},
        {"date": "2026-09-10 00:00:00 +0530", "qty": 4.2},
        {"date": "2026-09-11 00:00:00 +0530", "qty": 5.32}]},
]}}

# --- Auto Export: the per-hour batch for 09-11 ----------------------------
HAE_HOURLY = {"data": {"metrics": [
    {"name": "step_count", "units": "count", "data": [
        {"date": "2026-09-11 %02d:00:00 +0530" % h, "qty": q}
        for h, q in HOURS]},
]}}

conn = sqlite3.connect(DB)
for at, body in (("2026-09-10 06:00:00", IOS_BODY),
                 ("2026-09-11 23:03:00", HAE_HOURLY),
                 ("2026-09-11 23:06:00", HAE_ROLLUP)):
    text = json.dumps(body)
    conn.execute("INSERT INTO health_raw (received_at, source, n_bytes, payload)"
                 " VALUES (?, 'applewatch', ?, ?)", (at, len(text), text))

# What the old code left behind in health_metrics.
STALE = [
    # distance: the ios figure, orphaned once Auto Export took over
    ("2026-09-09", "distance_km", 2.5, "km"),
    # steps on 09-11: a partial export was the last writer
    ("2026-09-11", "steps", 650.0, "count"),
    ("2026-09-09", "steps", 3000.0, "count"),
    # a metric that has been dropped
    ("2026-09-10", "total_energy_kcal", 2300.0, "kcal"),
]
for date, metric, value, unit in STALE:
    conn.execute("INSERT INTO health_metrics "
                 "(date, metric, value, unit, source, ingested_at) "
                 "VALUES (?, ?, ?, ?, 'applewatch', '2026-09-11 23:06:00')",
                 (date, metric, value, unit))
conn.commit(); conn.close()


def value(date, metric, source="applewatch"):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT value FROM health_metrics "
                       "WHERE date=? AND metric=? AND source=?",
                       (date, metric, source)).fetchone()
    conn.close()
    return None if row is None else row[0]


def run(*args):
    cmd = [sys.executable, os.path.join(HERE, "recompute_apple_daily.py"),
           "--db", DB] + list(args)
    env = dict(os.environ)
    env["FITLOG_DB"] = DB
    return subprocess.run(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True)


print("\n[1] dry run writes nothing")
r = run("--dry-run")
check("exits 0", r.returncode == 0, r.stdout[-400:])
check("says DRY RUN", "DRY RUN" in r.stdout)
check("stale distance untouched on disk", value("2026-09-09", "distance_km") == 2.5,
      str(value("2026-09-09", "distance_km")))
check("stale steps untouched on disk", value("2026-09-11", "steps") == 650.0,
      str(value("2026-09-11", "steps")))
check("dropped metric still on disk",
      value("2026-09-10", "total_energy_kcal") == 2300.0)
check("preview already shows the corrected 4914", "4914" in r.stdout)

print("\n[2] live run rebuilds the days")
r = run()
check("exits 0", r.returncode == 0, r.stdout[-600:])
check("took a backup", "Backup  :" in r.stdout)
check("09-11 steps rebuilt to 4914", value("2026-09-11", "steps") == 4914,
      str(value("2026-09-11", "steps")))
check("09-11 is NOT the partial 650", value("2026-09-11", "steps") != 650)
check("09-11 is NOT hourly+rollup doubled to 9828",
      value("2026-09-11", "steps") != 9828)
check("09-10 steps from the rollup", value("2026-09-10", "steps") == 5100,
      str(value("2026-09-10", "steps")))

print("\n[3] the retired ios feed is skipped by default")
# Its bodies are mid-day snapshots. Replaying them would re-create
# half-finished days under metrics Auto Export never sends - and the
# watch strip prefers move_energy_kcal over active_energy_kcal, so the
# Move ring would go backwards.
check("retired bodies reported as skipped", "Skipped :" in r.stdout,
      r.stdout[:400])
check("an ios-only metric is NOT resurrected",
      value("2026-09-09", "hr") is None, str(value("2026-09-09", "hr")))
check("09-09 steps still from Auto Export", value("2026-09-09", "steps") == 4200,
      str(value("2026-09-09", "steps")))

print("\n[3b] --replay-retired-ios compares feeds, never adds them")
# ios reported 3000 steps for 09-09, Auto Export 4200. S02 keeps the
# fuller view; summing would have given 7200.
rio = run("--replay-retired-ios")
check("exits 0", rio.returncode == 0, rio.stdout[-400:])
check("09-09 steps is 4200, not 7200", value("2026-09-09", "steps") == 4200,
      str(value("2026-09-09", "steps")))
check("the ios-only metric appears when asked for",
      value("2026-09-09", "hr") == 100.0, str(value("2026-09-09", "hr")))
check("distance still the fuller Auto Export figure",
      abs(value("2026-09-09", "distance_km") - 3.1) < 1e-9,
      str(value("2026-09-09", "distance_km")))

print("\n[4] distance restored")
check("09-09 distance is the Auto Export 3.1, not the stale ios 2.5",
      abs(value("2026-09-09", "distance_km") - 3.1) < 1e-9,
      str(value("2026-09-09", "distance_km")))
check("09-10 distance 4.2",
      abs(value("2026-09-10", "distance_km") - 4.2) < 1e-9,
      str(value("2026-09-10", "distance_km")))
check("09-11 distance 5.32",
      abs(value("2026-09-11", "distance_km") - 5.32) < 1e-9,
      str(value("2026-09-11", "distance_km")))
conn = sqlite3.connect(DB)
unit = conn.execute("SELECT unit FROM health_metrics WHERE date='2026-09-11' "
                    "AND metric='distance_km'").fetchone()
conn.close()
check("unit is km", unit is not None and unit[0] == "km", str(unit))

print("\n[5] total_energy_kcal removed")
check("daily row gone", value("2026-09-10", "total_energy_kcal") is None)
conn = sqlite3.connect(DB)
n = conn.execute("SELECT COUNT(*) FROM health_hc_records "
                 "WHERE metric='total_energy_kcal'").fetchone()[0]
conn.close()
check("no record rows either", n == 0, str(n))
check("reported in the output", "Dropped : total_energy_kcal" in r.stdout)

print("\n[6] the run is idempotent")
r2 = run()
check("second run exits 0", r2.returncode == 0, r2.stdout[-400:])
check("09-11 still 4914", value("2026-09-11", "steps") == 4914,
      str(value("2026-09-11", "steps")))
check("09-09 still 4200", value("2026-09-09", "steps") == 4200,
      str(value("2026-09-09", "steps")))
check("second run reports nothing changed", "0 of " in r2.stdout)

print("\n[7] range is respected")
conn = sqlite3.connect(DB)
conn.execute("INSERT INTO health_raw (received_at, source, n_bytes, payload) "
             "VALUES ('2026-09-12 23:00:00', 'applewatch', 10, ?)",
             (json.dumps({"data": {"metrics": [{
                 "name": "step_count", "units": "count",
                 "data": [{"date": "2026-09-12 00:00:00 +0530",
                           "qty": 7777}]}]}}),))
conn.commit(); conn.close()
r3 = run("--from", "2026-09-09", "--to", "2026-09-11")
check("out-of-range date not written", value("2026-09-12", "steps") is None,
      str(value("2026-09-12", "steps")))
r4 = run("--from", "2026-09-12", "--to", "2026-09-12")
check("in-range date written", value("2026-09-12", "steps") == 7777,
      str(value("2026-09-12", "steps")))

print("\n[8] argument handling")
check("rejects a bad date", run("--from", "09/09/2026").returncode == 2)
check("rejects from after to",
      run("--from", "2026-09-11", "--to", "2026-09-09").returncode == 2)
check("rejects an unknown flag", run("--whatever").returncode == 2)
check("rejects a flag with no value", run("--to").returncode == 2)

total = PASS + FAIL
print("\n" + "=" * 52)
print("RECOMPUTE RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES:
    print("  - " + f)
print("=" * 52)
print("temp dir: " + tmp + "  (safe to delete)")
sys.exit(0 if FAIL == 0 else 1)

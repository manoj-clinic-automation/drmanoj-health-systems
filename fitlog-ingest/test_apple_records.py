#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - regression suite for record-level Auto Export ingest (rule S02).

Covers the hole patch_apple_records.py closes: a day's figure used to be
one payload's aggregate, upserted on (date, metric, source), so whichever
payload landed last owned the day. It survived only because Auto Export
happens to post a whole-day rollup minutes after the per-hour batch.

Sections 1-3 are the three arrival orders the owner asked for:
  [1] per-hour samples with no rollup behind them
  [2] a partial batch arriving after a full one
  [3] a rollup arriving before the per-hour samples

Section 9 is the negative control. It replays the SAME fixtures through
the unfixed parsers - still present in the module - and asserts both
defects reproduce. A suite that cannot fail against the broken code is
not evidence of anything (CLAUDE.md rule 2).

Runs against a throwaway temp database. Python 3.9 compatible.
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


tmp = tempfile.mkdtemp(prefix="fitlog_apple_rec_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
TOK = "apple-rec-smoke"
open(ENV, "w").write("FITLOG_INGEST_TOKEN=" + TOK + "\nFITLOG_HC_TOKEN=hc-rec\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()
import health_ingest
from flask import Flask

for marker, patcher in (("_apple_aggregate", "patch_apple_aggregation.py"),
                        ("parse_apple_records", "patch_apple_records.py")):
    if not hasattr(health_ingest, marker):
        print("FAIL: not patched. Run " + patcher); sys.exit(1)

app = Flask(__name__); app.register_blueprint(health_ingest.health_ingest_bp)
cl = app.test_client()
A = {"Authorization": "Bearer " + TOK}
P = "/api/ingest?source=applewatch"
WATCH = "Manoj's Apple Watch"

# 19 recorded hours on 2026-09-11, summing to the real 4914. The last
# sample is 134, which is what last-sample-wins used to store.
HOURS = [(5, 120), (6, 80), (7, 450), (8, 300), (9, 300), (10, 200),
         (11, 150), (12, 400), (13, 500), (14, 250), (15, 180), (16, 220),
         (17, 300), (18, 350), (19, 410), (20, 300), (21, 150), (22, 120),
         (23, 134)]
DAY_TOTAL = sum(q for _, q in HOURS)
LAST_SAMPLE = HOURS[-1][1]
FIRST_THREE = sum(q for _, q in HOURS[:3])


def hourly(day, pairs, name="step_count", units="count"):
    return {"name": name, "units": units,
            "data": [{"date": "%s %02d:00:00 +0530" % (day, h),
                      "qty": q, "source": WATCH} for h, q in pairs]}


def rollup(day, qty, name="step_count", units="count"):
    return {"name": name, "units": units,
            "data": [{"date": day + " 00:00:00 +0530", "qty": qty,
                      "source": WATCH}]}


def post(entries):
    return cl.post(P, headers=A, json={"data": {"metrics": entries}})


def metric(date, name, source="applewatch"):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT value, unit FROM health_metrics "
                       "WHERE date=? AND metric=? AND source=?",
                       (date, name, source)).fetchone()
    conn.close()
    return row


def value(date, name, source="applewatch"):
    row = metric(date, name, source)
    return None if row is None else row[0]


print("\n[0] fixture sanity")
check("19 hours sum to 4914", DAY_TOTAL == 4914, str(DAY_TOTAL))
check("last sample is not the day total", LAST_SAMPLE != DAY_TOTAL)

print("\n[1] per-hour samples, no rollup behind them")
D1 = "2026-09-11"
r = post([hourly(D1, HOURS)])
check("accepted", r.status_code == 200, str(r.status_code))
b = r.get_json()
check("routed to the record store",
      b.get("format") == "apple_auto_export", str(b.get("format")))
check("one record per sample, not one per day",
      b.get("metrics_stored") == len(HOURS), str(b.get("metrics_stored")))
check("day totals 4914 with no rollup involved",
      value(D1, "steps") == 4914, str(value(D1, "steps")))
check("NOT the last sample (last-sample-wins regression)",
      value(D1, "steps") != LAST_SAMPLE, str(value(D1, "steps")))

print("\n[2] a partial batch arriving after a full one")
check("baseline before the partial", value(D1, "steps") == 4914)
r = post([hourly(D1, HOURS[:3])])
check("partial re-delivery accepted", r.status_code == 200, str(r.status_code))
check("day does NOT drop to the partial's 650",
      value(D1, "steps") == 4914, str(value(D1, "steps")))
check("partial did not shrink the day",
      value(D1, "steps") >= 4914, str(value(D1, "steps")))
# A partial that carries hours nobody has seen must ADD, not replace.
D2 = "2026-09-12"
post([hourly(D2, HOURS[:3])])
check("first partial stores its own 650",
      value(D2, "steps") == FIRST_THREE, str(value(D2, "steps")))
post([hourly(D2, HOURS[3:])])
check("the rest of the day adds up to 4914",
      value(D2, "steps") == 4914, str(value(D2, "steps")))

print("\n[3] a rollup arriving BEFORE the per-hour samples")
D3 = "2026-09-13"
post([rollup(D3, 4914)])
check("rollup alone stores 4914", value(D3, "steps") == 4914,
      str(value(D3, "steps")))
post([hourly(D3, HOURS)])
check("per-hour samples do not double it to 9828",
      value(D3, "steps") == 4914, str(value(D3, "steps")))
# The reverse order, on its own date.
D4 = "2026-09-14"
post([hourly(D4, HOURS)])
post([rollup(D4, 4914)])
check("per-hour then rollup is also 4914",
      value(D4, "steps") == 4914, str(value(D4, "steps")))
# A rollup, then a partial hourly batch. The rollup covers the day; the
# partial does not. S02 keeps the greater.
D5 = "2026-09-15"
post([rollup(D5, 4914)])
post([hourly(D5, HOURS[:3])])
check("a partial batch cannot undercut a complete rollup",
      value(D5, "steps") == 4914, str(value(D5, "steps")))
# And a stale rollup cannot cap a fuller interval set.
D6 = "2026-09-16"
post([rollup(D6, 650)])
post([hourly(D6, HOURS)])
check("a stale rollup does not cap the fuller hourly set",
      value(D6, "steps") == 4914, str(value(D6, "steps")))

print("\n[4] redelivery is idempotent")
before = value(D1, "steps")
post([hourly(D1, HOURS)])
post([hourly(D1, HOURS)])
check("replaying the whole batch twice leaves 4914",
      value(D1, "steps") == before == 4914, str(value(D1, "steps")))
conn = sqlite3.connect(DB)
n = conn.execute("SELECT COUNT(*) FROM health_hc_records "
                 "WHERE date=? AND metric='steps' AND source='applewatch'",
                 (D1,)).fetchone()[0]
conn.close()
check("19 hours remain 19 records", n == len(HOURS), str(n))
# A revised sample updates its slot rather than adding to it.
post([hourly(D1, [(23, 200)])])
check("a revised hour replaces that hour, not adds to it",
      value(D1, "steps") == 4914 - 134 + 200, str(value(D1, "steps")))
post([hourly(D1, [(23, 134)])])

print("\n[5] distance is mapped again")
D7 = "2026-09-17"
post([hourly(D7, [(7, 1.2), (8, 2.1), (19, 2.02)],
             name="walking_running_distance", units="km")])
got = metric(D7, "distance_km")
check("walking_running_distance lands on distance_km", got is not None,
      str(got))
check("1.2 + 2.1 + 2.02 = 5.32 km",
      got is not None and abs(got[0] - 5.32) < 1e-9, str(got))
check("unit km", got is not None and got[1] == "km", str(got))
check("distance is not rule-bearing",
      "distance_km" not in health_ingest.RULE_BEARING)
D8 = "2026-09-18"
post([rollup(D8, 3.0, name="walking_running_distance", units="mi")])
gm = metric(D8, "distance_km")
check("3 miles convert to 4.828 km",
      gm is not None and abs(gm[0] - 4.828032) < 1e-6, str(gm))
check("miles relabelled km", gm is not None and gm[1] == "km", str(gm))
D9 = "2026-09-19"
post([rollup(D9, 4500.0, name="walking_running_distance", units="m")])
gme = metric(D9, "distance_km")
check("4500 m convert to 4.5 km",
      gme is not None and abs(gme[0] - 4.5) < 1e-9, str(gme))

print("\n[6] feeds are compared, never summed")
# The retired ios feed left records on health_hc_records with no feed
# tag. Auto Export now writes the same dates. Summing them would inflate
# a rule-bearing metric; S02 takes the greater.
D10 = "2026-09-20"
conn = sqlite3.connect(DB)
health_ingest._ensure_hc_record_columns(conn)
for i, qty in enumerate([1000, 900, 1100]):
    conn.execute(
        "INSERT INTO health_hc_records "
        "(record_key, date, metric, value, unit, ingested_at, source, "
        " grain, feed) VALUES (?, ?, 'steps', ?, 'count', "
        "'2026-09-20 09:00:00', 'applewatch', 'interval', '')",
        ("ios|steps|" + D10 + "|" + str(i), D10, qty))
conn.commit(); conn.close()
post([hourly(D10, HOURS)])
check("ios 3000 and auto export 4914 do not sum to 7914",
      value(D10, "steps") == 4914, str(value(D10, "steps")))
D11 = "2026-09-21"
conn = sqlite3.connect(DB)
conn.execute(
    "INSERT INTO health_hc_records "
    "(record_key, date, metric, value, unit, ingested_at, source, "
    " grain, feed) VALUES (?, ?, 'steps', 8000, 'count', "
    "'2026-09-21 09:00:00', 'applewatch', 'interval', '')",
    ("ios|steps|" + D11, D11))
conn.commit(); conn.close()
post([hourly(D11, HOURS[:3])])
check("the fuller feed wins when auto export is the thin one",
      value(D11, "steps") == 8000, str(value(D11, "steps")))

print("\n[7] total_energy_kcal is gone")
check("dropped from the hc map",
      "total_calories_burned" not in health_ingest.HC_ARRAY_MAP)
check("dropped from the ios map",
      "total_calories" not in health_ingest.IOS_ARRAY_MAP)
check("dropped from the hc summed set",
      "total_energy_kcal" not in health_ingest.HC_SUMMED)
check("dropped from the ios summed set",
      "total_energy_kcal" not in health_ingest.IOS_SUMMED)
check("no longer mapped onto active energy",
      health_ingest.METRIC_MAP.get("total_calories_burned") is None)
r = cl.post("/api/ingest?source=healthconnect", headers=A, json={
    "steps": [{"start_time": "2026-09-22T10:00:00+05:30",
               "end_time": "2026-09-22T11:00:00+05:30", "count": 500}],
    "total_calories_burned": [{"start_time": "2026-09-22T10:00:00+05:30",
                               "end_time": "2026-09-22T11:00:00+05:30",
                               "kilocalories": 1800}]})
check("hc payload still accepted", r.status_code == 200, str(r.status_code))
check("hc steps still stored",
      value("2026-09-22", "steps", "healthconnect") == 500,
      str(value("2026-09-22", "steps", "healthconnect")))
check("no total_energy_kcal row written",
      value("2026-09-22", "total_energy_kcal", "healthconnect") is None)
check("active energy not inflated by the total",
      value("2026-09-22", "active_energy_kcal", "healthconnect") is None)

print("\n[8] levels are still averaged, never summed")
D12 = "2026-09-23"
post([hourly(D12, [(6, 89), (12, 91), (20, 90)],
             name="resting_heart_rate", units="count/min")])
check("89/91/90 average to 90", value(D12, "resting_hr") == 90.0,
      str(value(D12, "resting_hr")))
check("not summed to 270", value(D12, "resting_hr") != 270)
# A whole-day figure for a level replaces the interval mean; it is the
# source's own answer for the day, not another sample of it.
post([rollup(D12, 72, name="resting_heart_rate", units="count/min")])
check("a daily rollup wins for a level", value(D12, "resting_hr") == 72.0,
      str(value(D12, "resting_hr")))
check("and is not maxed to 90", value(D12, "resting_hr") != 90.0)

print("\n[9] NEGATIVE CONTROL - the same fixtures on the unfixed paths")
# 9a. The pre-aggregation parser: one row per sample, upserted, so the
#     LAST sample of the day wins. This is the 4.61-instead-of-4914 bug.
NC = "2026-08-01"
conn = sqlite3.connect(DB)
samples, _w, _s = health_ingest._apple_samples(
    {"data": {"metrics": [hourly(NC, HOURS)]}})
rows = [(d, m, v, u) for d, t, m, v, u in samples]
health_ingest.store(conn, rows, [], "applewatch", "{}")
conn.close()
check("unfixed: 19 samples store the last one, not the total",
      value(NC, "steps") == LAST_SAMPLE, str(value(NC, "steps")))
check("unfixed: the day is NOT 4914", value(NC, "steps") != 4914,
      str(value(NC, "steps")))
check("fixed path on the same fixture gives 4914", value(D1, "steps") == 4914)

# 9b. The deployed day-view parser: correct within one payload, but the
#     result is upserted, so a partial payload replaces a full day.
NC2 = "2026-08-02"
conn = sqlite3.connect(DB)
full, _w, _s = health_ingest.parse_payload({"data": {"metrics": [hourly(NC2, HOURS)]}})
health_ingest.store(conn, full, [], "applewatch", "{}")
conn.close()
check("unfixed: a full payload does store 4914",
      value(NC2, "steps") == 4914, str(value(NC2, "steps")))
conn = sqlite3.connect(DB)
part, _w, _s = health_ingest.parse_payload(
    {"data": {"metrics": [hourly(NC2, HOURS[:3])]}})
health_ingest.store(conn, part, [], "applewatch", "{}")
conn.close()
check("unfixed: the partial silently overwrites it with 650",
      value(NC2, "steps") == FIRST_THREE, str(value(NC2, "steps")))
check("fixed path on the same fixture held at 4914",
      value(D1, "steps") == 4914, str(value(D1, "steps")))

print("\n[10] documented S02 limit: grain comes from cadence, not magnitude")
# Within ONE body a midnight rollup is indistinguishable from the 00:00
# interval sample - same timestamp, same shape. Cadence decides: more
# than one timestamp for a date means intervals. Pinned here so that a
# future change to this reading is deliberate rather than accidental.
D13 = "2026-09-24"
post([hourly(D13, [(0, 300), (1, 50)])])
check("two timestamps in one body read as intervals",
      value(D13, "steps") == 350, str(value(D13, "steps")))
D14 = "2026-09-25"
post([hourly(D14, [(0, 300)])])
check("a lone midnight sample reads as the day so far",
      value(D14, "steps") == 300, str(value(D14, "steps")))
post([hourly(D14, [(0, 300), (1, 50), (2, 70)])])
check("later hours still win over it", value(D14, "steps") == 420,
      str(value(D14, "steps")))

print("\n[11] rule-bearing set unchanged")
m = cl.get("/api/health/daily?date=" + D1, headers=A).get_json()["metrics"]
check("steps rule-bearing", m["steps"]["rule_bearing"] is True)
check("rule-bearing set is still the original four",
      health_ingest.RULE_BEARING ==
      ("steps", "exercise_minutes", "stand_hours", "flights"),
      str(health_ingest.RULE_BEARING))
m7 = cl.get("/api/health/daily?date=" + D7, headers=A).get_json()["metrics"]
check("distance is context-only", m7["distance_km"]["rule_bearing"] is False)

print("\n[12] every body still retained in health_raw")
conn = sqlite3.connect(DB)
n_raw = conn.execute("SELECT COUNT(*) FROM health_raw").fetchone()[0]
conn.close()
check("health_raw is non-empty", n_raw > 0, str(n_raw))

total = PASS + FAIL
print("\n" + "=" * 52)
print("APPLE RECORDS RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES:
    print("  - " + f)
print("=" * 52)
print("temp dir: " + tmp + "  (safe to delete)")
sys.exit(0 if FAIL == 0 else 1)

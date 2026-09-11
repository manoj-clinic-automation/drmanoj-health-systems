#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - regression suite for cross-source contamination in
health_hc_records.

test_ios_ingest.py runs against a fresh temp database holding only iOS
rows, so it never enters the branch where two wearables have records for
the same date+metric. This suite seeds Health Connect records first, the
way the live database actually looked on 2026-09-11 (129 healthconnect
`steps` rows on 09-10 and 09-11), and only then posts the Apple Watch
payload for the same IST dates.

Guards rule S01: values are never summed or averaged across sources.

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

tmp = tempfile.mkdtemp(prefix="fitlog_iso_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
TOK = "main-token-iso-smoke"
open(ENV, "w").write("FITLOG_INGEST_TOKEN=" + TOK + "\nFITLOG_HC_TOKEN=hc-iso\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()

# Deliberately the PRE-migration schema: no source column. The live table
# looked exactly like this, and the patch has to cope in place.
c = sqlite3.connect(DB)
c.execute("CREATE TABLE IF NOT EXISTS health_hc_records ("
          "record_key TEXT PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL, "
          "value REAL, unit TEXT, ingested_at TEXT NOT NULL)")
# Seed Samsung steps on 2026-09-10: 1206 across 3 records, as Health
# Connect stores them (interval-keyed, no source prefix).
for i, val in enumerate((400.0, 500.0, 306.0)):
    c.execute("INSERT INTO health_hc_records "
              "(record_key, date, metric, value, unit, ingested_at) VALUES (?,?,?,?,?,?)",
              ("steps|2026-09-10T0%d:00:00.000Z|2026-09-10T0%d:30:00.000Z" % (i, i),
               "2026-09-10", "steps", val, "count", "2026-09-10 12:00:00"))
c.execute("INSERT INTO health_metrics (date, metric, value, unit, source, ingested_at) "
          "VALUES ('2026-09-10','steps',1206,'count','healthconnect','2026-09-10 12:00:00')")
c.commit(); c.close()

import health_ingest
from flask import Flask
if not hasattr(health_ingest, "_ensure_hc_source_column"):
    print("FAIL: not patched for source isolation. Run patch_ios_source_isolation.py"); sys.exit(1)
app = Flask(__name__); app.register_blueprint(health_ingest.health_ingest_bp)
cl = app.test_client()
A = {"Authorization": "Bearer " + TOK}
P_IOS = "/api/ingest?source=applewatch"
P_HC = "/api/ingest?source=healthconnect"

def metric_rows(date, metric):
    conn = sqlite3.connect(DB)
    rows = dict((r[0], r[1]) for r in conn.execute(
        "SELECT source, value FROM health_metrics WHERE date=? AND metric=?",
        (date, metric)).fetchall())
    conn.close()
    return rows

def hc_cols():
    conn = sqlite3.connect(DB)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(health_hc_records)").fetchall()]
    conn.close()
    return cols

print("\n[1] pre-state: Samsung steps already on 2026-09-10")
check("seeded hc total is 1206", metric_rows("2026-09-10", "steps").get("healthconnect") == 1206,
      str(metric_rows("2026-09-10", "steps")))
check("no source column before patch runs", "source" not in hc_cols(), str(hc_cols()))

print("\n[2] Watch posts steps for the SAME IST date")
# 2026-09-09T18:30:00Z == 2026-09-10 00:00 IST
body = {"platform": "ios", "steps": [
    {"start_time": "2026-09-09T18:30:00.000Z", "end_time": "2026-09-10T18:30:00.000Z", "count": 517}]}
r = cl.post(P_IOS, headers=A, json=body)
check("ios payload accepted", r.status_code == 200, str(r.status_code))
check("filed on 2026-09-10 IST", r.get_json().get("dates") == ["2026-09-10"], str(r.get_json().get("dates")))

print("\n[3] S01: no summing across sources")
rows = metric_rows("2026-09-10", "steps")
check("applewatch total is Watch-only (517)", rows.get("applewatch") == 517, str(rows))
check("applewatch total is NOT 1723", rows.get("applewatch") != 1723, str(rows))
check("healthconnect total untouched (1206)", rows.get("healthconnect") == 1206, str(rows))
check("both sources present as separate rows", set(rows.keys()) == {"applewatch", "healthconnect"}, str(rows))

print("\n[4] migration labelled the pre-existing rows correctly")
check("source column added in place", "source" in hc_cols(), str(hc_cols()))
conn = sqlite3.connect(DB)
by_src = dict((r[0], r[1]) for r in conn.execute(
    "SELECT source, COUNT(*) FROM health_hc_records GROUP BY source").fetchall())
seeded_still = conn.execute(
    "SELECT SUM(value) FROM health_hc_records WHERE source='healthconnect' AND date='2026-09-10'"
).fetchone()[0]
conn.close()
check("3 seeded rows kept as healthconnect", by_src.get("healthconnect") == 3, str(by_src))
check("1 new row tagged applewatch", by_src.get("applewatch") == 1, str(by_src))
check("seeded values preserved (1206)", seeded_still == 1206, str(seeded_still))

print("\n[5] S01 read path still prefers the Watch")
m = cl.get("/api/health/daily?date=2026-09-10", headers=A).get_json()["metrics"]
check("daily resolves to applewatch", m["steps"]["source"] == "applewatch", str(m["steps"]))
check("daily value is 517", m["steps"]["value"] == 517, str(m["steps"]))

print("\n[6] Health Connect path still works after the migration")
r = cl.post(P_HC, headers=A, json={"steps": [
    {"start_time": "2026-09-10T04:00:00.000Z", "end_time": "2026-09-10T04:30:00.000Z", "count": 94}]})
check("hc payload accepted", r.status_code == 200, str(r.status_code))
rows = metric_rows("2026-09-10", "steps")
check("hc total grew to 1300, not 1817", rows.get("healthconnect") == 1300, str(rows))
check("applewatch still 517 after hc post", rows.get("applewatch") == 517, str(rows))

print("\n[7] a date only the Watch covers is unaffected")
cl.post(P_IOS, headers=A, json={"platform": "ios", "steps": [
    {"start_time": "2026-09-11T18:30:00.000Z", "end_time": "2026-09-12T18:30:00.000Z", "count": 88}]})
rows = metric_rows("2026-09-12", "steps")
check("lone applewatch date stores 88", rows.get("applewatch") == 88, str(rows))
check("no healthconnect row invented", "healthconnect" not in rows, str(rows))

total = PASS + FAIL
print("\n" + "="*52); print("ISOLATION RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES: print("  - " + f)
print("="*52)
sys.exit(0 if FAIL == 0 else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - regression suite for the non-default database name.

Reproduces the original split-brain failure and proves it is closed:

    The orchestrator discovers and migrates the real database, which is
    NOT named fitlog.db. Nothing sets FITLOG_DB in the environment --
    fitlog.service loads .env, which does not exist. Before the fix,
    health_ingest.py fell back to its hardcoded /root/fitlog/fitlog.db and
    wrote to an unmigrated database: "no such table: health_raw", HTTP 500.

This suite deliberately does NOT set os.environ["FITLOG_DB"] before the
import, because setting it is exactly the thing that used to be missing.
It relies on FITLOG_DB being read from ingest.env.

Runs against a throwaway temp directory. Never touches the live DB.
Python 3.9 compatible.

Usage:
    cd /root/fitlog && python3 test_db_pin.py
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


tmpdir = tempfile.mkdtemp(prefix="fitlog_dbpin_test_")

# The real database, under a name that is NOT the default. This mirrors the
# GutLog health.db/health3.db lesson that CLAUDE.md records.
REAL_DB = os.path.join(tmpdir, "fitlog3.db")

# A decoy at the default name, in the same directory. It must stay empty:
# if anything lands here, the blueprint fell back to the hardcoded default.
DECOY_DB = os.path.join(tmpdir, "fitlog.db")

TEST_ENV = os.path.join(tmpdir, "ingest.env")
TOKEN = "dbpin-token-smoke-only"

sqlite3.connect(REAL_DB).close()
sqlite3.connect(DECOY_DB).close()

with open(TEST_ENV, "w") as fh:
    fh.write("# FitLog ingest token - keep out of git\n")
    fh.write("FITLOG_INGEST_TOKEN=" + TOKEN + "\n")
    fh.write("FITLOG_DB=" + REAL_DB + "\n")

os.environ["FITLOG_INGEST_ENV"] = TEST_ENV
# Deliberately NOT setting FITLOG_DB -- that is the point of this suite.
os.environ.pop("FITLOG_DB", None)
os.environ.pop("FITLOG_INGEST_TOKEN", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migrate_health_ingest  # noqa: E402

sys.argv = ["migrate", REAL_DB]
rc = migrate_health_ingest.main()

import health_ingest  # noqa: E402
from flask import Flask  # noqa: E402

print("\n[1] resolution")

check("migration of the non-default db returned 0", rc == 0, "rc=" + str(rc))

if not hasattr(health_ingest, "_env_file_value"):
    print("  FAIL health_ingest.py has not been patched (run patch_db_pin.py)")
    print("\nDBPIN RESULT: 0/1 passed")
    sys.exit(1)

check("DB_PATH resolves to the non-default database",
      os.path.abspath(health_ingest.DB_PATH) == os.path.abspath(REAL_DB),
      health_ingest.DB_PATH)

check("DB_PATH is not the hardcoded default",
      os.path.basename(health_ingest.DB_PATH) != "fitlog.db",
      health_ingest.DB_PATH)

print("\n[2] precedence")

check("ingest.env value is readable",
      health_ingest._env_file_value("FITLOG_DB") == REAL_DB,
      str(health_ingest._env_file_value("FITLOG_DB")))

check("absent key returns None",
      health_ingest._env_file_value("FITLOG_NOPE") is None)

check("token still reads from the same file",
      health_ingest._load_token() == TOKEN,
      str(health_ingest._load_token()))

print("\n[3] writes land in the real database")

app = Flask(__name__)
app.register_blueprint(health_ingest.health_ingest_bp)
client = app.test_client()
AUTH = {"Authorization": "Bearer " + TOKEN}

payload = {"data": {"metrics": [{
    "name": "step_count", "units": "count",
    "data": [{"date": "2026-03-01 00:00:00 +0530", "qty": 4321}],
}]}}

r = client.post("/api/ingest?source=applewatch", json=payload, headers=AUTH)
check("ingest succeeds (was HTTP 500 before the fix)",
      r.status_code == 200, str(r.status_code) + " " + str(r.get_json()))

conn = sqlite3.connect(REAL_DB)
real_rows = conn.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE date='2026-03-01'").fetchone()[0]
real_raw = conn.execute("SELECT COUNT(*) FROM health_raw").fetchone()[0]
conn.close()
check("row landed in the non-default database", real_rows == 1, str(real_rows))
check("raw payload landed there too", real_raw == 1, str(real_raw))

conn = sqlite3.connect(DECOY_DB)
decoy_tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
conn.close()
check("decoy fitlog.db was never touched", decoy_tables == [],
      str(decoy_tables))

print("\n[4] read path agrees with the write path")

r = client.get("/api/health/daily?date=2026-03-01", headers=AUTH)
val = r.get_json().get("metrics", {}).get("steps", {}).get("value")
check("daily read returns what was written", val == 4321, str(val))

r = client.get("/api/ingest/status", headers=AUTH)
srcs = [s["source"] for s in r.get_json().get("sources", [])]
check("status reports the source", "applewatch" in srcs, str(srcs))

total = PASS + FAIL
print("\n" + "=" * 52)
print("DBPIN RESULT: " + str(PASS) + "/" + str(total) + " passed")
if FAILURES:
    for f in FAILURES:
        print("  - " + f)
print("=" * 52)
print("temp dir: " + tmpdir + "  (safe to delete)")

sys.exit(0 if FAIL == 0 else 1)

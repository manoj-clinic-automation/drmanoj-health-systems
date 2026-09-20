#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog FITLOG_SLEEP_P1 -- the night is stored whole.

Guards the truncation found on 2026-09-15: Apple Health held 5 h 41 m for
the night of 14->15 Sep and FitLog showed 1.7.

The cause, read out of health_raw, was NOT this module -- Health Auto Export
delivered one 1 h 44 m block and the parser stored it faithfully. What this
suite guards is the defect that would have eaten the fix: Auto Export stamps
every sleep point at midnight of the day the night is filed under, so the
moment a wider export window delivers a night in two blocks they collide on
one record slot and the second replaces the first. Widening the export alone
would have left the number wrong and looked like it had worked.

Every fixture here is SYNTHETIC (CLAUDE.md rule 5d). The shapes are real --
asleep 0, inBed 0, a positive totalSleep, stages Awake/REM/Core/Deep, +0530
stamps -- the numbers are not his.

CLOCK-INDEPENDENT BY CONSTRUCTION: every date is a literal from the payload,
so no case can pass at noon and fail at 00:02. Run it all three ways anyway,
because that is the claim being made:

    python3 tools/RUN_AT_TIME.py 00 02 fitlog/test_sleep_night.py fitlog/health_ingest.py
    python3 tools/RUN_AT_TIME.py 23 58 fitlog/test_sleep_night.py fitlog/health_ingest.py

    python3 test_sleep_night.py [path/to/health_ingest.py]

Python 3.9.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------- fixtures
# One 5 h 41 m night, 2026-09-14 22:31 -> 2026-09-15 04:57, filed by Auto
# Export under the WAKE date, 2026-09-15, exactly as the real feed does.
# 4.10 + 1.583333 = 5.683333 h = 5 h 41 m.

NIGHT_TOTAL = 4.10 + 1.583333


def point(date, start, end, total, core, deep, rem, awake):
    """An Auto Export sleep point in the shape the real feed sends."""
    return {
        "date": date + " 00:00:00 +0530",
        "sleepStart": start, "sleepEnd": end,
        "inBedStart": start, "inBedEnd": end,
        # Both of these arrive as 0 on every Apple Watch night on this
        # server. asleep is the retired pre-stage category, not a total.
        "asleep": 0, "inBed": 0,
        "totalSleep": total,
        "core": core, "deep": deep, "rem": rem, "awake": awake,
        "source": "A Watch",
    }


BLOCK_A = point("2026-09-15", "2026-09-14 22:31:00 +0530",
                "2026-09-15 02:57:00 +0530", 4.10, 3.10, 0.45, 0.55, 0.333333)
BLOCK_B = point("2026-09-15", "2026-09-15 03:13:00 +0530",
                "2026-09-15 04:57:00 +0530", 1.583333, 1.15, 0.05, 0.383333,
                0.15)
# The same night re-segmented by a later export as one merged block.
BLOCK_MERGED = point("2026-09-15", "2026-09-14 22:31:00 +0530",
                     "2026-09-15 04:57:00 +0530", NIGHT_TOTAL,
                     4.25, 0.50, 0.933333, 0.75)

# A night with no totalSleep at all: asleep 0 beside real stages.
STAGES_ONLY = {
    "date": "2026-09-12 00:00:00 +0530",
    "sleepStart": "2026-09-11 23:00:00 +0530",
    "sleepEnd": "2026-09-12 04:30:00 +0530",
    "asleep": 0, "inBed": 0,
    "core": 3.0, "deep": 0.5, "rem": 1.0, "awake": 0.4,
    "source": "A Watch",
}

# A feed that stamps UTC. 17:30Z is 23:00 IST the same evening.
Z_NIGHT = {
    "date": "2026-09-17 00:00:00 +0530",
    "sleepStart": "2026-09-16T17:30:00.000Z",
    "sleepEnd": "2026-09-16T23:00:00.000Z",
    "inBedStart": "2026-09-16T17:20:00.000Z",
    "inBedEnd": "2026-09-16T23:00:00.000Z",
    "asleep": 0, "inBed": 0, "totalSleep": 5.0,
    "core": 3.7, "deep": 0.4, "rem": 0.9, "awake": 0.5,
    "source": "A Watch",
}


def body(points):
    return {"data": {"metrics": [
        {"name": "sleep_analysis", "units": "hr", "data": list(points)}]}}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    mod_path = (os.path.abspath(sys.argv[1]) if len(sys.argv) > 1
                else os.path.join(here, "health_ingest.py"))
    if not os.path.exists(mod_path):
        print("FATAL: not found: " + mod_path)
        return 1

    work = tempfile.mkdtemp(prefix="fitlog_sleep_")
    db = os.path.join(work, "t.db")
    env = os.path.join(work, "e.env")
    tok = "sleep-p1-not-a-real-token"
    fh = open(env, "w")
    fh.write("FITLOG_INGEST_TOKEN=" + tok + "\n")
    fh.close()
    sqlite3.connect(db).close()

    # Both are read at import time, so they are set before the load.
    os.environ["FITLOG_DB"] = db
    os.environ["FITLOG_INGEST_ENV"] = env
    os.environ["FITLOG_INGEST_TOKEN"] = tok
    os.environ.pop("FITLOG_HC_TOKEN", None)

    sys.path.insert(0, here)
    argv = sys.argv[:]
    mig = load("migrate_sleep_p1", os.path.join(here, "migrate_health_ingest.py"))
    sys.argv = ["migrate", db]
    rc = mig.main()
    sys.argv = argv
    if rc != 0:
        print("FATAL: migration failed")
        return 1

    hi = load("health_ingest_under_test", mod_path)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(hi.health_ingest_bp)
    cl = app.test_client()
    AUTH = {"Authorization": "Bearer " + tok}
    URL = "/api/ingest?source=applewatch"

    def post(points):
        return cl.post(URL, headers=AUTH, json=body(points))

    def stored(date):
        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT value, unit FROM health_metrics WHERE date=? "
            "AND metric='sleep_hours' AND source='applewatch'",
            (date,)).fetchone()
        con.close()
        return row

    def blocks(date):
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT block_key, start_ts, end_ts, asleep_h FROM "
            "health_sleep_blocks WHERE date=? ORDER BY start_ts",
            (date,)).fetchall()
        con.close()
        return rows

    def night(date):
        con = sqlite3.connect(db)
        try:
            return hi.sleep_night(con, date, "applewatch")
        finally:
            con.close()

    # ---------------------------------------------------------------- 01
    def t01():
        r = post([BLOCK_A, BLOCK_B])
        assert r.status_code == 200, str(r.status_code) + " " + r.get_data(as_text=True)
        got = stored("2026-09-15")
        assert got is not None, "no sleep row stored at all"
        assert abs(got[0] - NIGHT_TOTAL) < 0.001, (
            "stored " + str(got[0]) + ", expected " + str(NIGHT_TOTAL))
        assert abs(got[0] - 1.583333) > 0.5, (
            "stored the last block only -- the night was truncated")
        return ("two blocks of one night stored as "
                + str(round(got[0], 2)) + " h, not 1.58")
    check("01 a 5h41m night delivered in two blocks stores 5.68, not the "
          "last block", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        n = night("2026-09-15")
        assert n is not None, "sleep_night returned nothing"
        assert n["blocks"] == 2 and n["stretches"] == 2, (n["blocks"],
                                                          n["stretches"])
        assert n["start_ts"] == "2026-09-14 22:31:00", n["start_ts"]
        assert n["end_ts"] == "2026-09-15 04:57:00", n["end_ts"]
        assert n["awakenings"] == 1, n["awakenings"]
        assert abs(n["awake_h"] - 0.483333) < 0.001, n["awake_h"]
        assert abs(n["rem_h"] - 0.933333) < 0.001, n["rem_h"]
        assert abs(n["core_h"] - 4.25) < 0.001, n["core_h"]
        assert abs(n["deep_h"] - 0.50) < 0.001, n["deep_h"]
        return ("night carries its real IST span, one break between blocks, "
                "and the stage split")
    check("02 the night carries its span, its stages and its breaks", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        n = night("2026-09-15")
        banned = ("score", "grade", "readiness", "recovery", "rating",
                  "quality", "target", "streak")
        hits = [k for k in n.keys()
                for w in banned if w in k.lower()]
        assert not hits, "the night carries a verdict: " + str(hits)
        assert "awakenings" in n and "awake_h" in n, sorted(n.keys())
        return "measurements only: " + ", ".join(sorted(n.keys()))
    check("03 the night is never scored, graded or given a verdict", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        before = stored("2026-09-15")[0]
        post([BLOCK_A, BLOCK_B])
        post([BLOCK_A, BLOCK_B])
        after = stored("2026-09-15")[0]
        assert abs(after - before) < 1e-9, (before, after)
        assert abs(after - NIGHT_TOTAL) < 0.001, after
        assert len(blocks("2026-09-15")) == 2, blocks("2026-09-15")
        return "replayed twice, still " + str(round(after, 2)) + " h"
    check("04 replaying the night does not add it to itself", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        # A later export re-segments the same night as ONE merged block.
        # It overlaps both, so it is a competing description, not extra
        # sleep. 5.68 must not become 11.37.
        r = post([BLOCK_MERGED])
        assert r.status_code == 200, str(r.status_code)
        got = stored("2026-09-15")[0]
        assert abs(got - NIGHT_TOTAL) < 0.001, (
            "re-segmented night stored as " + str(got))
        n = night("2026-09-15")
        assert n["blocks"] == 3 and n["stretches"] == 1, (n["blocks"],
                                                          n["stretches"])
        assert n["awakenings"] == 0, n["awakenings"]
        return ("three stored blocks collapse to one stretch of "
                + str(round(got, 2)) + " h")
    check("05 a re-segmented night is not added to itself", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        # Delivered one block per payload, the way an incremental export
        # sends them. Same night, same answer.
        con = sqlite3.connect(db)
        con.execute("DELETE FROM health_sleep_blocks")
        con.execute("DELETE FROM health_hc_records WHERE metric='sleep_hours'")
        con.execute("DELETE FROM health_metrics WHERE metric='sleep_hours'")
        con.commit()
        con.close()
        post([BLOCK_A])
        first = stored("2026-09-15")[0]
        post([BLOCK_B])
        got = stored("2026-09-15")[0]
        assert abs(first - 4.10) < 0.001, first
        assert abs(got - NIGHT_TOTAL) < 0.001, (
            "separate payloads stored " + str(got))
        return ("first payload " + str(round(first, 2)) + " h, then "
                + str(round(got, 2)) + " h")
    check("06 blocks arriving in separate payloads combine, never replace",
          t06)

    # ---------------------------------------------------------------- 07
    def t07():
        # The whole night is filed under the WAKE date Auto Export gives
        # it, even though it starts at 22:31 the previous evening.
        assert stored("2026-09-15") is not None
        assert stored("2026-09-14") is None, (
            "part of the night leaked onto 2026-09-14")
        keys = [b[0] for b in blocks("2026-09-15")]
        assert len(set(keys)) == 2, keys
        return "filed under 2026-09-15; nothing on 2026-09-14"
    check("07 a night that starts before midnight is filed under its wake "
          "date", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        r = post([STAGES_ONLY])
        assert r.status_code == 200, str(r.status_code)
        got = stored("2026-09-12")
        assert got is not None, "asleep=0 stored nothing at all"
        assert abs(got[0] - 4.5) < 0.001, (
            "asleep=0 was believed as a total: stored " + str(got[0]))
        return "3.0 + 0.5 + 1.0 stages read as 4.5 h, not asleep=0"
    check("08 asleep=0 beside real stages is not a zero night", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        n = night("2026-09-12")
        assert abs(n["asleep_h"] - 4.5) < 0.001, n["asleep_h"]
        assert abs(n["awake_h"] - 0.4) < 0.001, n["awake_h"]
        assert n["asleep_h"] < n["asleep_h"] + n["awake_h"], "sanity"
        got = stored("2026-09-12")[0]
        assert abs(got - 4.9) > 0.01, "awake time was counted as sleep"
        return "4.5 h asleep, 0.4 h awake, kept apart"
    check("09 awake time is never counted as sleep", t09)

    # ---------------------------------------------------------------- 10
    def t10():
        r = post([Z_NIGHT])
        assert r.status_code == 200, str(r.status_code)
        n = night("2026-09-17")
        assert n is not None, "the Z-stamped night stored nothing"
        assert n["start_ts"] == "2026-09-16 23:00:00", (
            "UTC reached the store: " + str(n["start_ts"]))
        assert n["end_ts"] == "2026-09-17 04:30:00", n["end_ts"]
        for key in ("start_ts", "end_ts", "in_bed_start", "in_bed_end"):
            val = str(n.get(key) or "")
            assert "Z" not in val and "T" not in val, (key, val)
        return "17:30Z stored as 23:00 IST; no UTC stamp on the night"
    check("10 a UTC-stamped block is stored in IST, never as UTC", t10)

    ok = all(r[0] for r in RESULTS)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name
              + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

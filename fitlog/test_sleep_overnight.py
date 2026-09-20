#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog FITLOG_SLEEP_P2 -- carry the whole night, not one number.

Wrist temperature is the reason this phase exists. He has had subjective
feverishness for over two years with, until 2026-09-14, not one documented
temperature, and the Watch has been measuring it nightly and discarding it
because no name in METRIC_MAP claimed it. These cases prove it lands, that
Fahrenheit is converted, and that a unit the converter does not know is
DROPPED rather than filed under a canonical name ending `_c`.

The rest proves rule S04 Overnight Basis: a figure said to be measured over
the night really is averaged over samples inside the sleep span, and a figure
that is not says so instead of being passed off as one.

Nothing here is scored. Assertion 09 fails the build if a verdict-shaped key
ever appears on the night or on anything it carries.

All fixtures SYNTHETIC (CLAUDE.md rule 5d). Clock-independent by construction:
every date is a payload literal.

    python3 test_sleep_overnight.py [path/to/health_ingest.py]
    python3 tools/RUN_AT_TIME.py 00 02 fitlog/test_sleep_overnight.py fitlog/health_ingest.py

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


# The night under test: 2026-09-20 03:13 -> 04:57, filed under 09-20.
# Deliberately a late-starting block, so that a sample stamped 00:00 on the
# same date falls OUTSIDE the sleep span and must be reported as a day
# figure rather than an overnight one.
NIGHT = {
    "date": "2026-09-20 00:00:00 +0530",
    "sleepStart": "2026-09-20 03:13:00 +0530",
    "sleepEnd": "2026-09-20 04:57:00 +0530",
    "inBedStart": "2026-09-20 03:05:00 +0530",
    "inBedEnd": "2026-09-20 04:57:00 +0530",
    "asleep": 0, "inBed": 0, "totalSleep": 1.5,
    "core": 1.1, "deep": 0.1, "rem": 0.3, "awake": 0.2,
    "source": "A Watch",
}

# A night that spans midnight, for the cross-date window case.
NIGHT2 = {
    "date": "2026-09-21 00:00:00 +0530",
    "sleepStart": "2026-09-20 22:40:00 +0530",
    "sleepEnd": "2026-09-21 05:10:00 +0530",
    "inBedStart": "2026-09-20 22:30:00 +0530",
    "inBedEnd": "2026-09-21 05:10:00 +0530",
    "asleep": 0, "inBed": 0, "totalSleep": 5.9,
    "core": 4.2, "deep": 0.6, "rem": 1.1, "awake": 0.35,
    "source": "A Watch",
}


# A night with a BREAK in it: 22:50 in bed, up at 01:30, back at 02:10,
# out at 05:00. It SPANS 6 h 10 m and holds 5 h 30 m of recorded bed time.
NIGHT3A = {
    "date": "2026-09-25 00:00:00 +0530",
    "sleepStart": "2026-09-24 23:00:00 +0530",
    "sleepEnd": "2026-09-25 01:30:00 +0530",
    "inBedStart": "2026-09-24 22:50:00 +0530",
    "inBedEnd": "2026-09-25 01:30:00 +0530",
    "asleep": 0, "inBed": 0, "totalSleep": 2.3,
    "core": 1.7, "deep": 0.2, "rem": 0.4, "awake": 0.2,
    "source": "A Watch",
}
NIGHT3B = {
    "date": "2026-09-25 00:00:00 +0530",
    "sleepStart": "2026-09-25 02:10:00 +0530",
    "sleepEnd": "2026-09-25 05:00:00 +0530",
    "inBedStart": "2026-09-25 02:10:00 +0530",
    "inBedEnd": "2026-09-25 05:00:00 +0530",
    "asleep": 0, "inBed": 0, "totalSleep": 2.6,
    "core": 1.8, "deep": 0.2, "rem": 0.6, "awake": 0.25,
    "source": "A Watch",
}


def pt(day, hhmmss, qty):
    return {"date": day + " " + hhmmss + " +0530", "qty": qty,
            "source": "A Watch"}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    mod_path = (os.path.abspath(sys.argv[1]) if len(sys.argv) > 1
                else os.path.join(here, "health_ingest.py"))
    if not os.path.exists(mod_path):
        print("FATAL: not found: " + mod_path)
        return 1

    work = tempfile.mkdtemp(prefix="fitlog_overnight_")
    db = os.path.join(work, "t.db")
    env = os.path.join(work, "e.env")
    tok = "sleep-p2-not-a-real-token"
    fh = open(env, "w")
    fh.write("FITLOG_INGEST_TOKEN=" + tok + "\n")
    fh.close()
    sqlite3.connect(db).close()

    os.environ["FITLOG_DB"] = db
    os.environ["FITLOG_INGEST_ENV"] = env
    os.environ["FITLOG_INGEST_TOKEN"] = tok
    os.environ.pop("FITLOG_HC_TOKEN", None)

    sys.path.insert(0, here)
    argv = sys.argv[:]
    mig = load("migrate_sleep_p2",
               os.path.join(here, "migrate_health_ingest.py"))
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

    def post(metrics):
        return cl.post(URL, headers=AUTH, json={"data": {"metrics": metrics}})

    def stored(date, metric):
        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT value, unit FROM health_metrics WHERE date=? AND metric=? "
            "AND source='applewatch'", (date, metric)).fetchone()
        con.close()
        return row

    def night(date):
        con = sqlite3.connect(db)
        try:
            return hi.sleep_night(con, date, "applewatch")
        finally:
            con.close()

    # Both nights, plus the overnight samples around them.
    post([{"name": "sleep_analysis", "units": "hr", "data": [NIGHT, NIGHT2]}])

    # ---------------------------------------------------------------- 01
    def t01():
        r = post([{"name": "apple_sleeping_wrist_temperature", "units": "degC",
                   "data": [pt("2026-09-20", "00:00:00", 35.42)]}])
        assert r.status_code == 200, str(r.status_code)
        skipped = r.get_json().get("skipped_metrics") or []
        assert "apple_sleeping_wrist_temperature" not in skipped, (
            "still unmapped: " + str(skipped))
        got = stored("2026-09-20", "wrist_temp_c")
        assert got is not None, "wrist temperature was not stored"
        assert abs(got[0] - 35.42) < 0.001, got
        assert got[1] == "degC", got
        return "35.42 degC stored as wrist_temp_c"
    check("01 wrist temperature is mapped and stored, not skipped", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        post([{"name": "apple_sleeping_wrist_temperature", "units": "degF",
               "data": [pt("2026-09-22", "00:00:00", 97.7)]}])
        got = stored("2026-09-22", "wrist_temp_c")
        assert got is not None, "Fahrenheit night stored nothing"
        assert abs(got[0] - 36.5) < 0.01, (
            "97.7 degF stored as " + str(got[0]) + ", expected 36.5 degC")
        assert got[1] == "degC", got
        return "97.7 degF converted to 36.5 degC"
    check("02 a Fahrenheit temperature is converted, never relabelled", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        r = post([{"name": "apple_sleeping_wrist_temperature", "units": "K",
                   "data": [pt("2026-09-23", "00:00:00", 308.6)]}])
        assert r.status_code == 200, str(r.status_code)
        got = stored("2026-09-23", "wrist_temp_c")
        assert got is None, (
            "a Kelvin value was filed under a Celsius name: " + str(got))
        return "unconvertible unit dropped, not stored as degC"
    check("03 a temperature in an unknown unit is dropped, not filed under "
          "a Celsius name", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        post([{"name": "body_temperature", "units": "degF",
               "data": [pt("2026-09-20", "19:40:00", 100.4)]}])
        got = stored("2026-09-20", "body_temp_c")
        assert got is not None, "a thermometer reading was not stored"
        assert abs(got[0] - 38.0) < 0.01, got
        wrist = stored("2026-09-20", "wrist_temp_c")
        assert wrist is not None and abs(wrist[0] - 35.42) < 0.001, (
            "the thermometer reading was averaged into the wrist sensor")
        return "100.4 degF thermometer reading kept apart from the wrist sensor"
    check("04 a thermometer reading is stored and never merged with the "
          "wrist sensor", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        m = cl.get("/api/health/daily?date=2026-09-20",
                   headers=AUTH).get_json()["metrics"]
        assert "wrist_temp_c" in m, sorted(m.keys())
        assert m["wrist_temp_c"]["rule_bearing"] is False, m["wrist_temp_c"]
        assert m["body_temp_c"]["rule_bearing"] is False, m["body_temp_c"]
        return "temperature is context-only; no rule may read it"
    check("05 temperature is context-only, never a rule input", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        # Three respiratory-rate samples: two inside the 22:40 -> 05:10
        # span on either side of midnight, one at lunchtime the day before.
        post([{"name": "respiratory_rate", "units": "count/min", "data": [
            pt("2026-09-20", "13:00:00", 19.0),
            pt("2026-09-20", "23:30:00", 14.0),
            pt("2026-09-21", "02:00:00", 16.0)]}])
        n = night("2026-09-21")
        assert n is not None and "overnight" in n, sorted((n or {}).keys())
        rr = n["overnight"].get("resp_rate")
        assert rr is not None, n["overnight"]
        assert rr["basis"] == "night", rr
        assert rr["n"] == 2, ("samples used: " + str(rr["n"]))
        assert abs(rr["value"] - 15.0) < 0.001, (
            "overnight mean " + str(rr["value"]) + ", expected 15.0")
        return ("14 and 16 inside the span average to 15; the 13:00 sample "
                "of 19 is left out")
    check("06 an overnight figure is averaged over the samples inside the "
          "sleep span", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        # The 09-20 night runs 03:13 -> 04:57. The wrist temperature is
        # stamped 00:00, outside it. That must be reported as a DAY figure,
        # not dressed up as an overnight one.
        n = night("2026-09-20")
        wt = n["overnight"].get("wrist_temp_c")
        assert wt is not None, n["overnight"]
        assert wt["basis"] == "day", (
            "a 00:00 sample was reported as measured over the night: "
            + str(wt))
        assert wt["n"] is None, wt
        assert abs(wt["value"] - 35.42) < 0.001, wt
        return ("stamped 00:00, outside a 03:13-04:57 night, and labelled "
                "a day figure")
    check("07 a figure not measured inside the night says so", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        post([{"name": "heart_rate_variability", "units": "ms", "data": [
            pt("2026-09-20", "23:50:00", 30.0),
            pt("2026-09-21", "03:30:00", 40.0)]}])
        post([{"name": "resting_heart_rate", "units": "count/min", "data": [
            pt("2026-09-21", "04:00:00", 62.0)]}])
        n = night("2026-09-21")
        hrv = n["overnight"].get("hrv_ms")
        rhr = n["overnight"].get("resting_hr")
        assert hrv is not None and hrv["basis"] == "night", hrv
        assert abs(hrv["value"] - 35.0) < 0.001, hrv
        assert hrv["min"] == 30.0 and hrv["max"] == 40.0, hrv
        assert rhr is not None and rhr["basis"] == "night", rhr
        assert abs(rhr["value"] - 62.0) < 0.001, rhr
        return ("HRV 30/40 over the night with min and max shown; resting HR "
                "62 carried on the night it was derived from")
    check("08 resting HR and HRV are carried on the night they were derived "
          "from", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        n = night("2026-09-21")
        banned = ("score", "grade", "readiness", "recovery", "rating",
                  "quality", "target", "streak", "verdict")
        hits = [k for k in n.keys() for w in banned if w in k.lower()]
        for metric, info in n["overnight"].items():
            for k in info.keys():
                for w in banned:
                    if w in k.lower():
                        hits.append(metric + "." + k)
        assert not hits, "the night carries a verdict: " + str(hits)
        for metric, info in n["overnight"].items():
            assert info["basis"] in ("night", "day"), (metric, info["basis"])
        return ("measurements and a basis, nothing else: "
                + ", ".join(sorted(n["overnight"].keys())))
    check("09 nothing the night carries is a score, a grade or a verdict",
          t09)

    # ---------------------------------------------------------------- 10
    def t10():
        # A metric with no sample at all is ABSENT, never zero. A zero
        # blood oxygen on a sleep page would read as an event.
        n = night("2026-09-21")
        assert "spo2_pct" not in n["overnight"], n["overnight"]
        assert abs(n["asleep_h"] - 5.9) < 0.001, n["asleep_h"]
        return "blood oxygen never reported, so it is absent rather than 0"
    check("10 a metric the Watch never sent is absent, not zero", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        post([{"name": "sleep_analysis", "units": "hr",
               "data": [NIGHT3A, NIGHT3B]}])
        n = night("2026-09-25")
        assert n is not None, "the broken night stored nothing"
        assert n["awakenings"] == 1, n["awakenings"]
        assert abs(n["asleep_h"] - 4.9) < 0.001, n["asleep_h"]
        assert abs(n["in_bed_h"] - 5.5) < 0.001, (
            "time in bed is " + str(n["in_bed_h"]) + " h, expected 5.5 -- "
            "the 40-minute break is being counted as time in bed")
        assert n["in_bed_h"] < 6.0, (
            "time in bed spans the break instead of summing the stretches")
        assert n["start_ts"] == "2026-09-24 23:00:00", n["start_ts"]
        assert n["end_ts"] == "2026-09-25 05:00:00", n["end_ts"]
        return ("a night spanning 6 h 10 m holds 5 h 30 m in bed; the 40 "
                "minutes he was up are not counted as bed time")
    check("11 time in bed sums the stretches and never spans a break", t11)

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

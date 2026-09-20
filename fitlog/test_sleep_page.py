#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog FITLOG_SLEEP_P2_PAGE -- the night, on the page, never scored.

Server suites do not render pages, which is how GutLog v3.4.0 shipped with
two deleted functions at 18/18. This one renders /watch and asserts on the
HTML that actually comes back.

The assertion this suite exists for is 10: **the page never scores a night.**
He has post-discontinuation insomnia and a number graded every morning becomes
its own cause. A future change that adds a readiness figure, a recovery
percentage, a streak or a "poor night" verdict fails here, by name.

Fixtures synthetic (CLAUDE.md rule 5d). Dates are derived from the clock so
the 14-day window really is exercised; run it at 00:02 and 23:58 too, because
a night is filed under its wake date and that is where date handling breaks.

    python3 test_sleep_page.py [path/to/app.py]
    python3 tools/RUN_AT_TIME.py 00 02 fitlog/test_sleep_page.py fitlog/app.py

Python 3.9.
"""
import importlib.util
import os
import re
import secrets
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = (os.path.abspath(sys.argv[1]) if len(sys.argv) > 1
                else os.path.join(here, "app.py"))
    if not os.path.exists(app_path):
        print("FATAL: not found: " + app_path)
        return 1

    work = tempfile.mkdtemp(prefix="fitlog_sleeppage_")
    db = os.path.join(work, "t.db")
    env = os.path.join(work, "e.env")
    tokf = os.path.join(work, "feed.token")
    FEED_TOK = "page-feed-not-a-real-token"
    fh = open(env, "w")
    INGEST_TOK = "page-" + secrets.token_hex(8)   # minted per run; no literal for NO_SECRETS to flag
    fh.write("FITLOG_INGEST_TOKEN=" + INGEST_TOK + "\n")
    fh.close()
    fh = open(tokf, "w")
    fh.write(FEED_TOK + "\n")
    fh.close()
    sqlite3.connect(db).close()

    os.environ["FITLOG_DB"] = db
    os.environ["FITLOG_INGEST_ENV"] = env
    os.environ["FITLOG_SECRET"] = "test-secret-not-real"
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = tokf
    os.environ["FITLOG_GUTLOG_FEED"] = "0"
    os.environ.pop("FITLOG_INGEST_TOKEN", None)

    sys.path.insert(0, here)
    argv = sys.argv[:]
    mig = load("migrate_sleep_page",
               os.path.join(here, "migrate_health_ingest.py"))
    sys.argv = ["migrate", db]
    rc = mig.main()
    sys.argv = argv
    if rc != 0:
        print("FATAL: migration failed")
        return 1

    D0 = date.today()

    def d(n):
        return (D0 - timedelta(days=n)).isoformat()

    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS health_hc_records ("
        "record_key TEXT PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL,"
        " value REAL, unit TEXT, ingested_at TEXT NOT NULL,"
        " source TEXT NOT NULL DEFAULT 'healthconnect',"
        " grain TEXT NOT NULL DEFAULT 'interval',"
        " feed TEXT NOT NULL DEFAULT '')")
    con.execute(
        "CREATE TABLE IF NOT EXISTS health_sleep_blocks ("
        "block_key TEXT PRIMARY KEY, date TEXT NOT NULL, source TEXT NOT NULL,"
        " feed TEXT NOT NULL DEFAULT '', start_ts TEXT, end_ts TEXT,"
        " in_bed_start TEXT, in_bed_end TEXT, asleep_h REAL, rem_h REAL,"
        " core_h REAL, deep_h REAL, awake_h REAL, device TEXT,"
        " ingested_at TEXT NOT NULL)")

    # Tonight, filed under today, in TWO blocks with one break between them.
    # 2.30 + 2.60 = 4.90 h asleep = 4 h 54 m.
    blocks = [
        (d(0), d(1) + " 23:00:00", d(0) + " 01:30:00", d(1) + " 22:50:00",
         d(0) + " 01:30:00", 2.30, 0.40, 1.70, 0.20, 0.20),
        (d(0), d(0) + " 02:10:00", d(0) + " 05:00:00", d(0) + " 02:10:00",
         d(0) + " 05:00:00", 2.60, 0.60, 1.80, 0.20, 0.25),
        (d(1), d(2) + " 23:30:00", d(1) + " 04:30:00", d(2) + " 23:20:00",
         d(1) + " 04:30:00", 4.00, 0.80, 2.90, 0.30, 0.30),
        (d(3), d(4) + " 23:45:00", d(3) + " 03:30:00", d(4) + " 23:40:00",
         d(3) + " 03:30:00", 3.00, 0.50, 2.20, 0.30, 0.40),
    ]
    for row in blocks:
        key = "|".join(["hae", "applewatch", row[0], row[1], row[2]])
        con.execute(
            "INSERT OR REPLACE INTO health_sleep_blocks "
            "(block_key, date, source, feed, start_ts, end_ts, in_bed_start, "
            " in_bed_end, asleep_h, rem_h, core_h, deep_h, awake_h, device, "
            " ingested_at) VALUES (?,?,?,'hae',?,?,?,?,?,?,?,?,?,?,?)",
            (key, row[0], "applewatch", row[1], row[2], row[3], row[4],
             row[5], row[6], row[7], row[8], row[9], "A Watch",
             "2026-09-15 06:00:00"))

    # Overnight samples. Two respiratory-rate readings inside tonight's span
    # on either side of midnight, and one at lunchtime that must be left out.
    samples = [
        ("resp_rate", d(1), "23:30:00", 14.0, "count/min"),
        ("resp_rate", d(0), "03:00:00", 16.0, "count/min"),
        ("resp_rate", d(0), "13:00:00", 19.0, "count/min"),
        ("hrv_ms", d(0), "02:00:00", 34.0, "ms"),
        ("resting_hr", d(0), "04:00:00", 61.0, "count/min"),
        # Stamped well after he was up, so it is OUTSIDE the night and must
        # be reported as a day figure rather than dressed up as one measured
        # while he slept.
        ("wrist_temp_c", d(0), "09:00:00", 35.42, "degC"),
    ]
    for metric, day, tod, val, unit in samples:
        key = "|".join(["hae", metric, "interval", day, tod])
        con.execute(
            "INSERT OR REPLACE INTO health_hc_records "
            "(record_key, date, metric, value, unit, ingested_at, source, "
            " grain, feed) VALUES (?,?,?,?,?,?,'applewatch','interval','hae')",
            (key, day, metric, val, unit, "2026-09-15 06:00:00"))

    for day, metric, val, unit in (
            (d(0), "steps", 3874, "count"),
            (d(1), "steps", 2305, "count"),
            (d(0), "sleep_hours", 4.90, "hr"),
            (d(1), "sleep_hours", 4.00, "hr"),
            (d(0), "resting_hr", 61, "count/min"),
            (d(0), "hrv_ms", 34.0, "ms"),
            (d(0), "wrist_temp_c", 35.42, "degC")):
        con.execute(
            "INSERT OR REPLACE INTO health_metrics "
            "(date, metric, value, unit, source, ingested_at) "
            "VALUES (?,?,?,?,'applewatch','2026-09-15 06:00:00')",
            (day, metric, val, unit))
    con.commit()
    con.close()

    fl = load("fitlog_sleep_page", app_path)
    with fl.app.app_context():
        fl.set_setting("password_hash", fl.sha("pw"))
    fl.app.config["TESTING"] = True
    cl = fl.app.test_client()
    with cl.session_transaction() as s:
        s["auth"] = True

    html = cl.get("/watch").get_data(as_text=True)
    home = cl.get("/").get_data(as_text=True)

    def card():
        i = html.find("<h2>Sleep</h2>")
        assert i >= 0, "there is no Sleep card on /watch"
        j = html.find("<h2>", i + 10)
        return html[i:j if j > 0 else len(html)]

    # ---------------------------------------------------------------- 01
    def t01():
        assert "Traceback" not in html, "the page raised"
        c = card()
        assert "Night of " + d(0) in c, "the night is not named"
        assert "A Watch" in c, "the device is not named"
        return "Sleep card renders and names the night and the device"
    check("01 the Sleep card renders and names the night", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        c = card()
        assert "4 h 54 m asleep" in c, (
            "asleep is not shown in hours and minutes")
        assert "4.9" not in c.split("asleep")[0][-40:], (
            "a raw decimal is being shown for the night")
        return "two blocks read as 4 h 54 m asleep, not 4.9"
    check("02 the night is shown in hours and minutes, not a decimal", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        c = card()
        assert "23:00" in c and "05:00" in c, "the clock times are missing"
        assert "IST" in c, "the times are not declared IST"
        times = re.findall(r"\d{2}:\d{2}", c)
        assert times, "no clock times at all"
        assert "T23:00" not in c and "Z</" not in c, "a UTC stamp reached the page"
        assert "0530" not in c, "a raw offset reached the page"
        return "23:00 -> 05:00 IST, no UTC stamp anywhere on the card"
    check("03 the night's clock times are IST and no UTC reaches the page",
          t03)

    # ---------------------------------------------------------------- 04
    def t04():
        c = card()
        for label in ("REM", "Core", "Deep", "Awake"):
            assert label in c, "stage missing: " + label
        assert 'class="wstage"' in c, "the stage bar is not drawn"
        assert "1 h" in c, "no stage duration shown"
        widths = re.findall(r'width:([\d.]+)%', c)
        assert widths, "the stage bar has no widths"
        total = sum(float(w) for w in widths)
        assert abs(total - 100.0) < 0.5, ("stage widths sum to " + str(total))
        return ("four stages named and drawn to scale, widths summing to "
                + str(round(total, 1)) + "%")
    check("04 the stage split is named and drawn to scale", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        c = card()
        assert "1 break between recorded sleep blocks" in c, (
            "breaks are not reported as breaks between recorded blocks")
        assert "27 m awake in the night" in c, (
            "measured awake time is not shown separately")
        assert "It is a floor, not a count of times you woke" in c, (
            "the page does not say the break count is a floor")
        # 22:50 -> 01:30 is 2 h 40 m and 02:10 -> 05:00 is 2 h 50 m. The
        # night SPANS 6 h 10 m; only 5 h 30 m of it was recorded in bed.
        assert "5 h 30 m in bed" in c, (
            "time in bed spans the 40-minute break instead of summing the "
            "stretches")
        assert "6 h 10 m in bed" not in c and "6 h in bed" not in c, (
            "the gap is being counted as time in bed")
        return ("one break and 27 m awake kept separate, 5 h 30 m in bed "
                "over a 6 h 10 m span, and the floor stated in words")
    check("05 breaks and measured awake time are separate, and the floor "
          "is stated", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        c = card()
        assert "Respiratory rate" in c, "respiratory rate is not shown"
        assert "15.0 /min" in c, (
            "the overnight mean is not 15.0 -- the 13:00 sample leaked in")
        assert "over the night" in c, "the basis is not stated"
        assert "2 readings" in c, "the number of readings is not shown"
        return "14 and 16 inside the span shown as 15.0 /min over 2 readings"
    check("06 an overnight figure is averaged over the night and says how "
          "many readings it rests on", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        c = card()
        assert "Wrist temperature" in c, "wrist temperature is not shown"
        assert "35.42" in c, "the wrist temperature value is missing"
        assert "the day's figure, not measured inside the night" in c, (
            "a figure measured outside the night is being passed off as an "
            "overnight one")
        return ("wrist temperature shown and labelled a day figure, because "
                "its sample sits outside the sleep span")
    check("07 a figure not measured inside the night says so on the page",
          t07)

    # ---------------------------------------------------------------- 08
    def t08():
        assert "Rest HR overnight" in html, (
            "resting HR is still labelled as if it were a daytime reading")
        assert "HRV overnight" in html, (
            "HRV is still labelled as if it were a daytime reading")
        assert "derived by the Watch while you" in card(), (
            "the Sleep card does not say where they come from")
        return "both labelled overnight in the tables and explained on the card"
    check("08 resting HR and HRV are labelled as derived overnight", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        assert "derived overnight by the Watch" in home, (
            "the Today-so-far strip does not say where Rest HR and HRV "
            "come from")
        assert "context only, no rule reads these" in home, (
            "the strip lost its context-only declaration")
        return "the Today-so-far strip says both, and keeps the R/C rule"
    check("09 the Today-so-far strip says Rest HR and HRV are overnight",
          t09)

    # ---------------------------------------------------------------- 10
    # THE ONE THIS SUITE EXISTS FOR.
    def t10():
        # Scanned in two halves. The card's own closing note has to USE the
        # words -- it is the place that promises there is no score -- so the
        # ban applies to everything the card DISPLAYS, and the note is
        # checked separately for saying what it must.
        full = card()
        at = full.find('<div class="wnote">')
        assert at > 0, "the Sleep card has lost its closing note"
        shown = full[:at].lower()
        note = full[at:].lower()
        banned = ("sleep score", "score:", "readiness", "recovery score",
                  "recovery %", "poor night", "bad night", "good night",
                  "streak", "out of 100", "/100", "grade", "rating",
                  "efficiency %", "sleep quality")
        hits = [w for w in banned if w in shown]
        assert not hits, "the Sleep card displays a verdict: " + str(hits)
        assert "measured, not scored" in note, (
            "the note does not state that the page never scores a night")
        assert "no sleep score on this page" in note, (
            "the note does not say there is no score")
        assert "population" in note and "never with a" in note, (
            "the note does not rule out a population comparison")
        return ("nothing displayed is a verdict; the note states the "
                "constraint in words")
    check("10 the page never scores, grades or ranks a night", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        # THREE nights on record. A median over three nights is not a
        # baseline, and stating n beside it does not stop it being read as
        # one -- so it is withheld, not qualified.
        c = card()
        # The word appears in the refusal itself ("a median drawn from fewer
        # than 7"), so what is checked is that no median FIGURE is rendered.
        assert "median <b>" not in c, (
            "a median figure is being shown over only 3 nights")
        assert "Your own last" not in c, (
            "the card is still presenting a baseline over 3 nights")
        assert "Not enough nights yet" in c, (
            "the card does not say why there is no median")
        assert "3 nights on record" in c, (
            "the card does not say how many nights there are")
        assert "Nothing is being compared until then" in c, (
            "the card does not make the absence of comparison explicit")
        return "no median over 3 nights; the card says so and names the count"
    check("11 no median is shown until there are enough nights", t11)

    # 14 is defined here but RUN LAST: it adds four nights to the fixture,
    # which would otherwise change what 12 and 13 are looking at.
    def t14():
        # Four more nights takes it to seven, and the median appears.
        # 3.00 3.50 4.00 4.50 4.90 5.00 6.00 -> 4.50 -> "4 h 30 m".
        more = [
            (d(4), d(5) + " 23:00:00", d(4) + " 04:00:00", 5.00, 0.9, 3.6,
             0.5, 0.3),
            (d(5), d(6) + " 23:10:00", d(5) + " 04:00:00", 4.50, 0.8, 3.3,
             0.4, 0.3),
            (d(6), d(7) + " 23:20:00", d(6) + " 03:20:00", 3.50, 0.6, 2.5,
             0.4, 0.3),
            (d(7), d(8) + " 22:40:00", d(7) + " 05:10:00", 6.00, 1.1, 4.3,
             0.6, 0.4),
        ]
        c2 = sqlite3.connect(db)
        for row in more:
            key = "|".join(["hae", "applewatch", row[0], row[1], row[2]])
            c2.execute(
                "INSERT OR REPLACE INTO health_sleep_blocks "
                "(block_key, date, source, feed, start_ts, end_ts, "
                " in_bed_start, in_bed_end, asleep_h, rem_h, core_h, deep_h,"
                " awake_h, device, ingested_at) "
                "VALUES (?,?,?,'hae',?,?,?,?,?,?,?,?,?,?,?)",
                (key, row[0], "applewatch", row[1], row[2], row[1], row[2],
                 row[3], row[4], row[5], row[6], row[7], "A Watch",
                 "2026-09-15 06:00:00"))
        c2.commit()
        c2.close()
        h2 = cl.get("/watch").get_data(as_text=True)
        assert "Traceback" not in h2
        i = h2.find("<h2>Sleep</h2>")
        j = h2.find("<h2>", i + 10)
        c = h2[i:j if j > 0 else len(h2)]
        assert "Not enough nights yet" not in c, (
            "the median is still withheld at 7 nights")
        assert "median <b>4 h 30 m</b> asleep" in c, (
            "the median of the seven nights is not 4 h 30 m")
        assert "over 7 nights with data" in c, (
            "the median does not say how many nights it rests on")
        assert "average" not in c.lower(), (
            "a mean is being shown where a median was promised")
        assert "Your own last" in c, "the median is not declared as his own"
        return ("at 7 nights the median appears: 4 h 30 m over 7 nights, "
                "declared as his own")

    # ---------------------------------------------------------------- 12
    def t12():
        i = html.find("<style")
        j = html.find("</style>", i)
        assert i >= 0 and j > i, "no stylesheet on the page"
        css = html[i:j]
        bad = []
        for rule in css.split("}"):
            if "{" not in rule:
                continue
            sel, body = rule.split("{", 1)
            if not re.search(r"wsleep|wstage|wbasis|wnote|wmed|wover", sel):
                continue
            for size in re.findall(r"font-size:(\d+)px", body):
                if int(size) < 14:
                    bad.append(sel.strip() + " -> " + size + "px")
        assert not bad, "type below 14px in new rules: " + str(bad)
        return "every new rule sets 14px or larger"
    check("12 nothing added here sets type below 14px", t12)

    # ---------------------------------------------------------------- 13
    def t13():
        r = fl.app.test_client().get(
            "/api/feed/watch?days=7",
            headers={"Authorization": "Bearer " + FEED_TOK})
        j = r.get_json()
        assert r.status_code == 200 and j.get("ok"), (r.status_code, j)
        today_row = [x for x in j["daily"] if x["date"] == d(0)][0]
        night = today_row.get("sleep")
        assert night, "the feed does not carry the night"
        assert abs(night["asleep_h"] - 4.90) < 0.001, night["asleep_h"]
        assert night["start_ts"] == d(1) + " 23:00:00", night["start_ts"]
        assert night["awakenings"] == 1, night["awakenings"]
        assert "overnight" in night, sorted(night.keys())
        empty = [x for x in j["daily"] if x["date"] == d(5)][0]
        assert empty.get("sleep") is None, (
            "a night with nothing recorded came back as a shell, not None")
        return "the feed carries the whole night; a night with none is None"
    check("13 the watch feed carries the whole night, and absence as None",
          t13)

    # Last, because it changes the fixture.
    check("14 at seven nights the median appears, as his own and with n",
          t14)

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

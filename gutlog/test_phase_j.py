#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.13.0 / FitLog v1.5.0 Phase J -- the watch display.

Runs a REAL GutLog and a REAL FitLog, each on its own scratch database, on two
free loopback ports. Nothing live is touched and no outward call leaves the
machine. The fixture is synthetic throughout: invented figures, invented
sources, an invented epoch label. No medicine name appears anywhere in this
file, and none should be added -- the label on the real epoch is data and
lives on the server.

This screen is made of times and dates, so it is written to be run under
tools/RUN_AT_TIME.py as well as directly:

  python3 test_phase_j.py [/path/to/fitlog]
  python3 ../tools/RUN_AT_TIME.py 00 02 test_phase_j.py

Properties, not counts. Pinning "5 tiles" and "42 passed" has broken twice, so
nothing here asserts a magic number that a later change would have to edit;
the assertions are about what the numbers mean.
"""
import importlib.util
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gut_path = os.path.join(here, "app.py")
    fit_dir = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(here), "fitlog")
    fit_path = os.path.join(fit_dir, "app.py")
    if not os.path.exists(fit_path):
        print("FATAL: FitLog not found at " + fit_path)
        print("       python3 test_phase_j.py /path/to/fitlog")
        return 1

    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    gport, fport = free_port(), free_port()

    os.environ.update(
        GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
        GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
        GUTLOG_ICONS=here, GUTLOG_FEED_TOKEN_FILE=tokf, GUTLOG_LINKS="1",
        GUTLOG_FITLOG_URL="http://127.0.0.1:%d" % fport,
        GUTLOG_FEED_URL="http://127.0.0.1:%d" % gport)
    sys.path.insert(0, here)
    gl = load("gutlog_under_test", gut_path)
    gc = gl.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
            follow_redirects=True)
    gdb = os.environ["GUTLOG_DB"]

    os.environ.update(FITLOG_DB=os.path.join(work, "f.db"),
                      FITLOG_SECRET="test-secret-not-real",
                      FITLOG_GUTLOG_FEED="1")
    sys.path.insert(0, fit_dir)
    fl = load("fitlog_under_test", fit_path)
    fdb = os.environ["FITLOG_DB"]

    from werkzeug.serving import make_server
    gsrv = make_server("127.0.0.1", gport, gl.app)
    fsrv = make_server("127.0.0.1", fport, fl.app)
    for s in (gsrv, fsrv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    time.sleep(0.3)

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def fq(sql, a=()):
        con = sqlite3.connect(fdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    # ------------------------------------------------------------- fixture
    # Times are derived from the clock, never written down: GutLog refuses a
    # time that has not come yet, so a literal turns this suite red in the
    # small hours. Clamped to midnight.
    NOW_DT = datetime.now()
    MIDNIGHT = NOW_DT.replace(hour=0, minute=0, second=0, microsecond=0)

    def ago(mins):
        t = NOW_DT - timedelta(minutes=mins)
        return (t if t >= MIDNIGHT else MIDNIGHT).strftime("%H:%M")

    T = date.today()
    D = dict((n, (T - timedelta(days=n)).isoformat()) for n in range(0, 16))
    TODAY = D[0]

    # The wearable tables live in migrate_health_ingest.py, not in FitLog's
    # own SCHEMA, and every FitLog suite builds them for itself. Run the real
    # migration rather than copying its DDL here: a copy would drift, and a
    # suite testing a schema it invented proves nothing about the server's.
    _argv = sys.argv[:]
    mig = load("migrate_under_test", os.path.join(fit_dir, "migrate_health_ingest.py"))
    sys.argv = ["migrate", fdb]
    rc = mig.main()
    sys.argv = _argv
    assert rc == 0, "the wearable migration failed on the scratch database"
    for junk in (fdb + ".bak_",):
        for f in os.listdir(os.path.dirname(fdb)):
            if f.startswith(os.path.basename(junk)):
                os.unlink(os.path.join(os.path.dirname(fdb), f))

    def metric(day, name, value, source, unit=""):
        fq("INSERT OR REPLACE INTO health_metrics"
           "(date,metric,value,unit,source,ingested_at) VALUES(?,?,?,?,?,?)",
           (day, name, value, unit, source, datetime.now().isoformat()))

    # A fortnight with deliberate holes and a mixture of sources:
    #   D13..D2   watch days, steady, so the median is well defined
    #   D5        NO WATCH DATA AT ALL -- must read "no data", never zero
    #   D4        healthconnect only
    #   D3        both feeds disagree; the larger must win and say which
    #   D0 today  watch, clearly above his own median
    for n in range(13, 1, -1):
        if n in (5, 4, 3):
            continue
        metric(D[n], "steps", 5000 + n * 10, "applewatch", "count")
        metric(D[n], "exercise_minutes", 20, "applewatch", "min")
        metric(D[n], "resting_hr", 60, "applewatch", "count/min")
        metric(D[n], "hrv_ms", 40, "applewatch", "ms")
    metric(D[4], "steps", 4321, "healthconnect", "count")
    metric(D[3], "steps", 700, "applewatch", "count")
    metric(D[3], "steps", 6800, "healthconnect", "count")
    metric(D[2], "steps", 5020, "applewatch", "count")
    metric(D[2], "exercise_minutes", 20, "applewatch", "min")
    metric(D[2], "resting_hr", 60, "applewatch", "count/min")
    metric(D[2], "hrv_ms", 40, "applewatch", "ms")
    # yesterday, with a figure unlike any other day's, so a fallback to it
    # cannot be confused with a neighbour
    YSTEPS = 6543
    metric(D[1], "steps", YSTEPS, "applewatch", "count")
    metric(D[1], "exercise_minutes", 22, "applewatch", "min")
    metric(D[1], "resting_hr", 59, "applewatch", "count/min")
    metric(D[1], "hrv_ms", 41, "applewatch", "ms")
    metric(TODAY, "steps", 9000, "applewatch", "count")
    metric(TODAY, "exercise_minutes", 35, "applewatch", "min")
    metric(TODAY, "resting_hr", 52, "applewatch", "count/min")
    metric(TODAY, "hrv_ms", 61, "applewatch", "ms")

    # a workout stored as a UTC Z-stamp: the feed must hand back IST
    fq("INSERT INTO health_workouts(date,start_ts,end_ts,wtype,duration_s,"
       "distance_km,source,ingested_at) VALUES(?,?,?,?,?,?,?,?)",
       (D[1], D[1] + "T01:41:29Z", D[1] + "T02:11:29Z", "Walking", 1800.0,
        2.4, "applewatch", datetime.now().isoformat()))

    # an epoch that STARTS INSIDE the window, so the band has a boundary to
    # draw rather than covering everything. Label is invented.
    EPOCH = "test epoch alpha"
    EPOCH_START = D[6]
    fq("INSERT INTO med_epochs(label,date_start,date_end,notes) VALUES(?,?,?,?)",
       (EPOCH, EPOCH_START, "", "synthetic"))

    # GutLog's own side: an operating day, and a day with pain and no activity
    gc.get("/api/now")
    gc.post("/api/activity", json={"kind": "ot_day", "minutes": 480,
                                   "day": D[2], "atime": "08:00"})
    gc.post("/api/activity", json={"kind": "ot_day", "minutes": 360,
                                   "day": TODAY, "atime": ago(30)})
    gq("INSERT INTO episodes(day,etime,category,etype,side,severity,duration,"
       "notes,created,treatments,radiates) VALUES(?,?,'pain','glute_r','R',6,"
       "'','',?,'',0)", (D[7], "09:00", datetime.now().isoformat()))
    gq("INSERT INTO episodes(day,etime,category,etype,side,severity,duration,"
       "notes,created,treatments,radiates) VALUES(?,?,'pain','low_back','',4,"
       "'','',?,'',0)", (TODAY, ago(60), datetime.now().isoformat()))

    ctx = {}

    def watch():
        j = gc.get("/api/watch?days=14").get_json()
        assert j.get("link"), "GutLog could not reach FitLog: " + str(j.get("err"))
        return j

    def row_for(j, d):
        m = [r for r in j["row"] if r["date"] == d]
        assert m, "no row for " + d
        return m[0]

    # ------------------------------------------------------------- the feed
    def t00_feed_is_bearer_gated_and_read_only():
        import urllib.error
        import urllib.request
        url = "http://127.0.0.1:%d/api/feed/watch?days=14" % fport
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            op.open(urllib.request.Request(url), timeout=3)
            raise AssertionError("no token was accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 401, "expected 401, got " + str(e.code)
        tok = open(tokf, encoding="utf-8").read().strip()
        with op.open(urllib.request.Request(
                url, headers={"Authorization": "Bearer " + tok}), timeout=3) as r:
            j = json.loads(r.read().decode("utf-8"))
        assert j["ok"] and j["app"] == "fitlog", str(j)[:120]
        assert len(j["daily"]) == j["days"], "a day is missing from the window"
        ctx["feed"] = j
        return "bearer-gated; the window is complete"

    def t01_no_ingestion_path_was_added():
        """Phase J is a reading problem. If this suite ever finds a write
        path here, someone has solved the wrong problem."""
        src = open(fit_path, encoding="utf-8", newline="").read()
        # the block itself, not from the docstring mention of it -- splitting
        # on the bare marker caught the whole file and failed for the wrong
        # reason, which is its own small lesson about anchors
        head = "# ---------------- watch read feed (FITLOG_V150_WATCHFEED)"
        assert head in src, "the watch feed block is not where it says it is"
        blk = src.split(head, 1)[1].split(
            "# ---------------- warning flags", 1)[0]
        for bad in ("INSERT", "UPDATE", "DELETE", "CREATE TABLE", "commit("):
            assert bad not in blk, "the watch feed writes: " + bad
        assert "def api_feed_watch" in blk, "the endpoint is outside the block"
        return "the feed block contains no INSERT, UPDATE, DELETE or commit"

    # ------------------------------------------------------ A: today strip
    def t02_strip_carries_every_figure_asked_for():
        j = watch()
        for k in ("steps", "exercise_minutes", "load_hours", "resting_hr", "hrv_ms"):
            assert k in j["strip"], k + " missing from the strip"
        return "steps, exercise, standing load, resting HR and HRV all present"

    def t03_direction_is_against_his_own_median():
        j = watch()
        st = j["strip"]["steps"]
        # steps is cumulative, so the headline is the last complete day
        assert st["value"] == YSTEPS, str(st["value"])
        assert st["median"] is not None and st["n"] >= 3, str(st)
        assert 4000 < st["median"] < 7000, \
            "the median is not drawn from his own trailing days: " + str(st["median"])
        assert st["dir"] == "up", str(st)
        # the settled pair take today's reading
        assert j["strip"]["resting_hr"]["dir"] == "down", str(j["strip"]["resting_hr"])
        assert j["strip"]["hrv_ms"]["dir"] == "up", str(j["strip"]["hrv_ms"])
        return ("steps up, resting HR down, HRV up -- each against its own "
                "median of " + str(st["n"]) + " days")

    def without_days(days_, fn):
        """Run fn with those days' metrics removed, then put them back --
        whatever happens.

        2026-09-14: a case that deleted today's metrics failed mid-way, left
        them deleted, and case 15 then failed for a completely unrelated
        reason. A case that mutates the fixture must not be able to poison
        the ones after it, or a single real failure turns into a screen of
        noise and the actual finding is the one you stop reading at.
        """
        marks = ",".join("?" for _ in days_)
        saved = fq("SELECT date,metric,value,unit,source,ingested_at FROM "
                   "health_metrics WHERE date IN (" + marks + ")", tuple(days_))
        try:
            fq("DELETE FROM health_metrics WHERE date IN (" + marks + ")",
               tuple(days_))
            gl._LINK_CACHE.clear()
            return fn()
        finally:
            for r in saved:
                fq("INSERT OR REPLACE INTO health_metrics"
                   "(date,metric,value,unit,source,ingested_at) "
                   "VALUES(?,?,?,?,?,?)", tuple(r))
            gl._LINK_CACHE.clear()

    def t03b_strip_falls_back_to_yesterday():
        """At 07:30 the phone has not uploaded yet, so today is empty and the
        strip read "no data" at exactly the hour he looks at it. Yesterday's
        figure, labelled, is the useful answer; an unlabelled one would be a
        lie."""
        def body():
            j = watch()
            st = j["strip"]["steps"]
            assert st["value"] == YSTEPS, \
                "the strip did not fall back to yesterday: " + str(st["value"])
            assert st["stale"] is True and st["day"] == D[1], str(st)
            assert st["source"] == "applewatch", \
                "the fallback lost its source: " + str(st)
            # every tile, not just steps -- all five were blank at 07:30
            for k in ("exercise_minutes", "resting_hr", "hrv_ms"):
                assert j["strip"][k]["value"] is not None, k + " is still blank"
                assert j["strip"][k]["stale"] is True, k + " is not flagged stale"
            assert j["row"][-1]["has_data"] is False, \
                "the row must still say today itself has nothing"
            return ("today empty -> yesterday's " + str(YSTEPS) +
                    " shown on every tile, flagged stale, row unchanged")
        return without_days([TODAY], body)

    def t03c_median_excludes_the_day_being_shown():
        """The bug this guards: a figure compared against a median it is
        itself inside compares with itself, and every fallback day reads
        level."""
        def body():
            j = watch()
            st = j["strip"]["steps"]
            assert st["day"] == D[1], "not in the fallback state: " + str(st)
            vals = [r["steps"] for r in j["row"]
                    if r["date"] not in (TODAY, D[1]) and r["steps"] is not None]
            assert st["n"] == len(vals), \
                "median is over %d days, expected %d with the shown day out" % (
                    st["n"], len(vals))
            assert st["median"] != st["value"], \
                "the shown day is inside its own median, so it is being " \
                "compared with itself"
            assert st["dir"] is not None, "no direction at all on a fallback day"
            assert st["dir"] == "up", \
                str(YSTEPS) + " against a median of " + str(st["median"]) + \
                " should read up, got " + str(st["dir"])
            return ("median over " + str(st["n"]) + " days with the shown day "
                    "excluded; " + str(YSTEPS) + " vs " + str(st["median"]) +
                    " reads " + st["dir"])
        return without_days([TODAY], body)

    def t03d_no_data_only_when_neither_day_has_one():
        """The one-day rule belongs to the SETTLED metrics. A reading is
        yesterday's or it is nothing; a cumulative total legitimately
        headlines the last complete day, however far back that is, because
        a complete day is a complete day."""
        def body():
            j = watch()
            for k in ("resting_hr", "hrv_ms"):
                d = j["strip"][k]
                assert d["value"] is None, \
                    k + " reached past yesterday: " + str(d)
                assert d["stale"] is False and d["day"] == "", str(d)
            # the cumulative one does reach further, and says which day
            st = j["strip"]["steps"]
            assert st["value"] == 5020 and st["day"] == D[2], \
                "steps did not fall through to the last complete day: " + str(st)
            return ("settled pair -> no data; cumulative headlines " + D[2] +
                    ", the last complete day, and names it")
        return without_days([TODAY, D[1]], body)

    def t03e_the_tile_shows_how_thin_the_baseline_is():
        """Deliberately not a plausibility threshold. Two days from before the
        source fix are dragging the live median down; that self-corrects as
        the window moves, and a heuristic written for it would outlive it. So
        show n and let a thin baseline look thin."""
        j = watch()
        for k, d in j["strip"].items():
            assert "n" in d, k + " carries no n"
            assert isinstance(d["n"], int) and d["n"] >= 0, str(d)
        src = open(gut_path, encoding="utf-8", newline="").read()
        blk = src.split("GUTLOG_V3140_FALLBACK", 1)[1][:4000]
        for bad in ("implausible", "MIN_STEPS", "> 100", "plausib"):
            assert bad not in blk, "a plausibility threshold crept in: " + bad
        h = gc.get("/").get_data(as_text=True)
        assert "days to compare with" in h, \
            "the tile cannot say how few days it has"
        assert "d.n+(d.n===1?' day':' days')" in h, \
            "n is not on the provenance line, so a thin baseline looks like any other"
        return ("every tile carries n and the page prints it on its own line; "
                "no threshold anywhere in the new block")

    # ---------------------------------------------- Phase K: kind and voice
    def t03f_cumulative_headline_is_the_last_complete_day():
        """A part-day total against whole-day medians is not a comparison: it
        points down every morning by construction. Steps, exercise and
        standing load therefore headline the last COMPLETE day."""
        j = watch()
        for k in ("steps", "exercise_minutes", "load_hours"):
            d = j["strip"][k]
            assert d["kind"] == "cumulative", k + " is not marked cumulative"
            if d["value"] is not None:
                assert d["day"] < TODAY, \
                    k + " headlines a day that is still running: " + str(d["day"])
        st = j["strip"]["steps"]
        assert st["day"] == D[1], "steps should headline yesterday: " + str(st["day"])
        assert st["value"] == YSTEPS, str(st["value"])
        assert st["dir"] is not None, "the settled day carries no arrow"
        return "steps headlines " + st["day"] + ", the last complete day, with an arrow"

    def t03g_todays_running_figure_has_no_arrow():
        j = watch()
        st = j["strip"]["steps"]
        assert st["today"] is not None, "today's running figure is missing"
        assert st["today"]["value"] == 9000, str(st["today"])
        assert st["today"]["day"] == TODAY, str(st["today"])
        assert "dir" not in st["today"], \
            "a part-day figure was given a direction: " + str(st["today"])
        assert st["value"] != st["today"]["value"], \
            "the headline and the running figure are the same number"
        h = gc.get("/").get_data(as_text=True)
        assert "so far today" in h, "the running figure is not labelled on the page"
        return "today's 9,000 reported separately, labelled, with no arrow"

    def t03h_settled_metrics_are_unchanged_by_the_split():
        j = watch()
        for k in ("resting_hr", "hrv_ms"):
            d = j["strip"][k]
            assert d["kind"] == "settled", k + " is not marked settled"
            assert d["day"] == TODAY, \
                k + " should take today when today has a reading: " + str(d["day"])
            assert d.get("today") is None, \
                "a settled metric grew a running figure: " + str(d)
        return "resting HR and HRV still take today's reading when it exists"

    def t03i_the_kind_is_declared_in_one_place():
        src = open(gut_path, encoding="utf-8", newline="").read()
        assert src.count("WATCH_KIND = {") == 1, "WATCH_KIND is declared more than once"
        for k in ("steps", "exercise_minutes", "load_hours", "resting_hr", "hrv_ms"):
            assert k in gl.WATCH_KIND, k + " has no declared kind"
        assert set(gl.WATCH_KIND.values()) == {"cumulative", "settled"}, \
            str(set(gl.WATCH_KIND.values()))
        # and nothing decides it from the clock
        blk = src.split("GUTLOG_V3150_READ -- what KIND", 1)[1][:3000]
        for bad in ("datetime.now().hour", "now_hm()", "strftime(\"%H", "hour <", "hour >"):
            assert bad not in blk, "a time-of-day threshold crept in: " + bad
        return "one WATCH_KIND table, five metrics, no clock in the decision"

    def t03j_every_figure_names_its_day():
        j = watch()
        for k, d in j["strip"].items():
            if d["value"] is not None:
                assert d["day"], k + " shows a figure without naming its day"
            if d.get("today"):
                assert d["today"].get("day") == TODAY, str(d["today"])
        h = gc.get("/").get_data(as_text=True)
        assert "function wkDay(" in h, "there is no day-naming helper on the page"
        assert "'today'" in h and "'yesterday'" in h, \
            "the page cannot name today or yesterday"
        return "every tile with a figure carries a day, and the page can name it"

    def t03k_no_third_person_in_any_rendered_string():
        """The briefs are written about him, not to him, and 'On his legs'
        made it onto the screen from one. Server suites never run page JS, so
        this reads the string literals the page is built from."""
        import re as _re
        banned = _re.compile(r"\b(his|him|he)\b", _re.I)
        src = open(gut_path, encoding="utf-8", newline="").read()
        blk = src.split("GUTLOG_V3150_READ -- Watch card", 1)[1]
        blk = blk.split("@media (prefers-reduced-motion", 1)[0]
        js = src.split("const WK_LABEL=", 1)[1].split("function buildNowStatics", 1)[0]
        bad = []
        for lit in _re.findall(r"'((?:[^'\\]|\\.)*)'", js + blk):
            if banned.search(lit):
                bad.append(lit)
        # and the server-side strings the payload carries
        for name in ("WATCH_NOTE",):
            if banned.search(getattr(gl, name, "") or ""):
                bad.append(name)
        assert not bad, "third person in a user-facing string: " + str(bad[:3])
        assert "On your legs" in js, "the standing-load label is not second person"
        return "no third-person pronoun in any string the Watch card renders"

    def t04_no_verdict_anywhere():
        """Inputs, not conclusions. No score, no readiness, no recovery."""
        j = watch()
        # the footnote is the one place these words may appear, because its
        # whole job is to say none of them is being computed
        blob = json.dumps(dict((k, v) for k, v in j.items() if k != "note")).lower()
        for word in ("readiness", "recovery", "body battery", "fitness age",
                     "score", "verdict"):
            assert word not in blob, "a verdict leaked into the payload: " + word
        note = (j.get("note") or "").lower()
        assert note, "the footnote is missing"
        assert "input" in note and "not conclusion" in note, j["note"]
        assert "no readiness" in note, "the footnote does not disclaim a verdict"
        h = gc.get("/").get_data(as_text=True).lower()
        for word in ("readiness", "body battery", "fitness age"):
            assert word not in h, "a verdict leaked into the page: " + word
        return "no readiness, recovery, battery, age or score; the footnote stands"

    def t05_a_level_day_is_not_a_missing_day():
        """dir=None means 'not enough to say' and must not be reported as
        'level' -- they are different statements."""
        with gl.app.test_request_context():
            d1, _m, _n = gl._direction(10.0, [])
            d2, _m2, n2 = gl._direction(10.0, [10.0, 10.0, 10.0])
            d3, _m3, _n3 = gl._direction(None, [1.0, 2.0, 3.0])
        assert d1 is None, "no history should give no direction, got " + str(d1)
        assert d2 == "level", str(d2)
        assert d3 is None, "no value today should give no direction"
        return "no history -> None, flat history -> level, no value -> None"

    # ------------------------------------------------- B: fourteen-day row
    def t06_row_spans_the_window_and_ends_today():
        j = watch()
        dates = [r["date"] for r in j["row"]]
        assert dates == sorted(dates), "the row is out of order"
        assert dates[-1] == TODAY, "the row does not end today: " + dates[-1]
        assert len(dates) == len(set(dates)), "a day appears twice"
        assert dates[0] == j["since"], "the row does not start at `since`"
        return str(len(dates)) + " days, in order, ending today"

    def t07_a_day_with_no_watch_data_is_not_a_zero():
        j = watch()
        gone = row_for(j, D[5])
        assert gone["has_data"] is False, "a day with nothing claims data"
        assert gone["steps"] is None, \
            "a day with no data reported steps=" + str(gone["steps"])
        here_ = row_for(j, D[4])
        assert here_["has_data"] is True, "a healthconnect-only day reads as empty"
        return "no-data day carries has_data False and steps None, never 0"

    def t08_source_is_named_and_the_larger_feed_wins():
        j = watch()
        hc = row_for(j, D[4])
        assert hc["source"] == "healthconnect", str(hc)
        both = row_for(j, D[3])
        assert both["steps"] == 6800, \
            "the smaller feed won: " + str(both["steps"])
        assert both["source"] == "healthconnect", \
            "the winning figure is attributed to the wrong feed: " + str(both)
        watchday = row_for(j, D[2])
        assert watchday["source"] == "applewatch", str(watchday)
        return "larger count wins and the day says which feed answered"

    def t09_pain_and_load_lanes_mark_the_right_days():
        j = watch()
        assert row_for(j, D[7])["pain"] is True, "a logged pain day is unmarked"
        assert row_for(j, D[7])["ot"] is False, "a pain day was marked as operating"
        assert row_for(j, D[2])["ot"] is True, "an operating day is unmarked"
        assert row_for(j, D[2])["ot_hours"] == 8.0, str(row_for(j, D[2]))
        assert row_for(j, TODAY)["pain"] is True and row_for(j, TODAY)["ot"] is True
        quiet = row_for(j, D[9])
        assert quiet["pain"] is False and quiet["ot"] is False, str(quiet)
        return "pain and operating lanes mark exactly the days that carry them"

    def t10_pain_day_without_activity_still_appears():
        """The case the whole screen exists for: a day with pain and nothing
        else must still be a day on the chart, not a gap."""
        j = watch()
        r = row_for(j, D[7])
        assert r["pain"] is True, str(r)
        assert r["has_data"] is True and r["steps"] is not None, \
            "the day vanished because it had no pain-related metric: " + str(r)
        return "a pain day is on the chart whether or not anything else happened"

    # ------------------------------------------------ C: workouts, real IST
    def t11_workouts_carry_ist_not_utc():
        j = watch()
        assert j["workouts"], "no workouts came through"
        w = j["workouts"][0]
        assert w["start_hm"] == "07:11", \
            "a 01:41Z workout must read 07:11 IST, got " + str(w["start_hm"])
        assert w["end_hm"] == "07:41", str(w["end_hm"])
        assert "Z" not in (w["start"] or ""), "a raw Z-stamp reached the consumer"
        return "01:41:29Z -> 07:11 IST, and the HH:MM is carried, not sliced"

    def t12_the_page_never_slices_a_timestamp():
        """The 2026-09-13 bug was a slice. The feed now carries start_hm, so
        the page has no reason to cut a timestamp up again."""
        src = open(gut_path, encoding="utf-8", newline="").read()
        blk = src.split("GUTLOG_V3130_WATCH -- the watch screen", 1)[1]
        blk = blk.split("function buildNowStatics", 1)[0]
        assert "start_hm" in blk, "the page is not using the feed's own clock time"
        for bad in ("start.slice", "start.substr", "['start'].slice",
                    ".start.substring"):
            assert bad not in blk, "the page slices a timestamp: " + bad
        return "the page reads start_hm; no slicing of a timestamp"

    # ----------------------------------------------------- D: epoch band
    def t13_epoch_band_has_a_boundary_in_the_window():
        j = watch()
        inside = [r["date"] for r in j["row"] if r["epoch"]]
        outside = [r["date"] for r in j["row"] if not r["epoch"]]
        assert inside and outside, \
            "the band covers everything or nothing, so it marks nothing"
        assert min(inside) == EPOCH_START, \
            "the band starts on the wrong day: " + min(inside)
        assert max(outside) < EPOCH_START, "a day before the epoch was banded"
        assert TODAY in inside, "an open epoch must reach today"
        assert any(e["label"] == EPOCH for e in j["epochs"]), str(j["epochs"])
        return "band runs from " + EPOCH_START + " to today; days before it are clear"

    def t14_no_medicine_name_is_written_into_the_code():
        """The epoch label is data. It lives in the database on the server and
        must not appear in this repository -- in code, prose, docstring or
        fixture. The grammar-example near-leak of 2026-09-14 is the precedent.

        The word list is read from tools/clinical_terms.local.txt, the same
        one NO_SECRETS.py uses, for two reasons: one source of truth, and a
        test that spelled the words out would itself be the leak. That file is
        gitignored, so off the publishing machine this reports SKIPPED rather
        than pretending to have checked.
        """
        import re as _re
        terms_path = os.path.join(os.path.dirname(here), "tools",
                                  "clinical_terms.local.txt")
        if not os.path.exists(terms_path):
            return ("SKIPPED - no tools/clinical_terms.local.txt on this machine; "
                    "NO_SECRETS.py is the gate, and it runs where the list lives")
        # one term per line; '#' lines are the file's own header
        words = []
        for line in open(terms_path, encoding="utf-8").read().splitlines():
            w = line.strip().lower()
            if w and not w.startswith("#") and len(w) > 3:
                words.append(w)
        assert words, "the term list is present but empty"
        hits = []
        for path in (gut_path, fit_path, os.path.abspath(__file__)):
            src = open(path, encoding="utf-8", newline="").read().lower()
            for w in words:
                if _re.search(r"\b" + _re.escape(w) + r"\b", src):
                    hits.append(os.path.basename(path) + ":" + w)
        assert not hits, "clinical terms in code: " + ", ".join(sorted(set(hits))[:6])
        return ("no term from the shared list appears in either app file or in "
                "this suite (" + str(len(words)) + " terms checked)")

    # ------------------------------------------- standing load never exercise
    def t15_standing_load_is_never_exercise():
        j = watch()
        exd = j["strip"]["exercise_minutes"]
        lhd = j["strip"]["load_hours"]
        # both are cumulative now, so both headline the last complete day and
        # carry today's running figure separately
        assert lhd["value"] == 8.0 and lhd["day"] == D[2], \
            "the standing-load headline is not the last complete day: " + str(lhd)
        assert lhd["today"] and lhd["today"]["value"] == 6.0, \
            "today's operating hours are not reported: " + str(lhd["today"])
        assert exd["value"] == 22 and exd["day"] == D[1], str(exd)
        assert exd["today"] and exd["today"]["value"] == 35, str(exd["today"])
        assert exd["value"] < 8 * 60 and exd["today"]["value"] < 6 * 60, \
            "the load was added into exercise minutes"
        act = gc.get("/api/activity?day=" + TODAY).get_json()["summary"]
        assert act["minutes"] == 0 and act["load_minutes"] == 360, str(act)
        # and it is not folded into the median either
        hist = j["strip"]["exercise_minutes"]
        assert hist["median"] is not None and hist["median"] < 60, str(hist)
        return "6 h of load beside 35 exercise minutes, and out of the median too"

    # ----------------------------------------------------------- degradation
    def t16_fitlog_down_reads_as_a_message():
        fsrv.shutdown()
        time.sleep(0.3)
        gl._LINK_CACHE.clear()
        j = gc.get("/api/watch?days=14").get_json()
        assert j["ok"] is True, "the endpoint failed instead of degrading"
        assert j["link"] is False, "it claimed a link that is down"
        assert j["err"], "no message was given for the reader"
        assert j["row"], "the row vanished; pain and operating days are GutLog's own"
        assert any(r["pain"] for r in j["row"]), \
            "GutLog's own pain days were lost when FitLog went down"
        return "degrades to a message, keeps what GutLog itself knows"

    # ------------------------------------------------------------ the page
    def t17_page_renders_and_is_jinja_clean():
        r = gc.get("/")
        assert r.status_code == 200, str(r.status_code)
        h = r.get_data(as_text=True)
        for el in ("nowWatch", "wkStrip", "wkChart", "wkWork", "loadWatch",
                   "wkChart(", "wkTile("):
            assert el in h, "missing from the page: " + el
        blk = h.split("GUTLOG_V3130_WATCH -- the watch screen", 1)[1]
        blk = blk.split("function buildNowStatics", 1)[0]
        for tok in ("{{", "{%", "{#"):
            assert tok not in blk, "Jinja token " + tok + " in the new JS (rule 5b)"
        assert "no data" in h, "the page has no way to say a day is empty"
        return "page renders, new JS Jinja-clean, 'no data' is expressible"

    tests = [
        ("00 feed is bearer-gated and complete", t00_feed_is_bearer_gated_and_read_only),
        ("01 no ingestion path was added", t01_no_ingestion_path_was_added),
        ("02 strip carries every figure", t02_strip_carries_every_figure_asked_for),
        ("03 direction is against his own median", t03_direction_is_against_his_own_median),
        ("03b strip falls back to yesterday", t03b_strip_falls_back_to_yesterday),
        ("03c median excludes the shown day", t03c_median_excludes_the_day_being_shown),
        ("03d no data only when neither day has one", t03d_no_data_only_when_neither_day_has_one),
        ("03e the tile shows how thin the baseline is", t03e_the_tile_shows_how_thin_the_baseline_is),
        ("03f cumulative headlines the last complete day", t03f_cumulative_headline_is_the_last_complete_day),
        ("03g today's running figure has no arrow", t03g_todays_running_figure_has_no_arrow),
        ("03h settled metrics unchanged by the split", t03h_settled_metrics_are_unchanged_by_the_split),
        ("03i the kind is declared in one place", t03i_the_kind_is_declared_in_one_place),
        ("03j every figure names its day", t03j_every_figure_names_its_day),
        ("03k no third person in a rendered string", t03k_no_third_person_in_any_rendered_string),
        ("04 no verdict anywhere", t04_no_verdict_anywhere),
        ("05 'not enough to say' is not 'level'", t05_a_level_day_is_not_a_missing_day),
        ("06 row spans the window, ends today", t06_row_spans_the_window_and_ends_today),
        ("07 a no-data day is not a zero", t07_a_day_with_no_watch_data_is_not_a_zero),
        ("08 source named, larger feed wins", t08_source_is_named_and_the_larger_feed_wins),
        ("09 pain and load lanes are right", t09_pain_and_load_lanes_mark_the_right_days),
        ("10 pain day without activity shows", t10_pain_day_without_activity_still_appears),
        ("11 workouts carry IST, not UTC", t11_workouts_carry_ist_not_utc),
        ("12 the page never slices a timestamp", t12_the_page_never_slices_a_timestamp),
        ("13 epoch band has a boundary", t13_epoch_band_has_a_boundary_in_the_window),
        ("14 no medicine name in the code", t14_no_medicine_name_is_written_into_the_code),
        ("15 standing load is never exercise", t15_standing_load_is_never_exercise),
        ("16 FitLog down reads as a message", t16_fitlog_down_reads_as_a_message),
        ("17 page renders, Jinja-clean", t17_page_renders_and_is_jinja_clean),
    ]
    print("=" * 72)
    print("Phase J - the watch display (real GutLog + real FitLog)")
    print("=" * 72)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 72)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    for s in (gsrv, fsrv):
        try:
            s.shutdown()
        except Exception:
            pass
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

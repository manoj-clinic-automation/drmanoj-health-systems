#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.2.0 -- activity feed test (both directions with GutLog v3.7.0).

Runs a REAL GutLog (scratch database, scratch token) and a REAL FitLog
(scratch database, scratch ingest token) on free loopback ports. Watch data
arrives through FitLog's own /api/ingest. Nothing live is touched.
Synthetic fixture. Python 3.9.

  python3 test_activity_feed.py [/root/gutlog/app.py]   -> must print 12/12 passed
"""
import importlib.util
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
from datetime import date

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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gut_path = sys.argv[1] if len(sys.argv) > 1 else "/root/gutlog/app.py"
    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    gport, fport = free_port(), free_port()
    from werkzeug.serving import make_server

    # ---- GutLog
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(os.path.abspath(gut_path)),
                      GUTLOG_FEED_TOKEN_FILE=tokf)
    sys.path.insert(0, os.path.dirname(os.path.abspath(gut_path)))
    spec = importlib.util.spec_from_file_location("gutlog_under_test", gut_path)
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    gc = G.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"})
    gs = make_server("127.0.0.1", gport, G.app)
    threading.Thread(target=gs.serve_forever, daemon=True).start()
    tok = open(tokf).read().strip()

    # ---- FitLog
    fdb = os.path.join(work, "f.db")
    envf = os.path.join(work, "ingest.env")
    open(envf, "w").write("FITLOG_INGEST_TOKEN=ingest-test-token-not-real\n")
    os.environ.update(FITLOG_DB=fdb, FITLOG_INGEST_ENV=envf, GUTLOG_FEED_URL="http://127.0.0.1:%d" % gport,
                      FITLOG_GUTLOG_FEED="1")
    os.environ.pop("FITLOG_INGEST_TOKEN", None)
    sqlite3.connect(fdb).close()
    sys.path.insert(0, here)
    import migrate_health_ingest
    sys.argv = ["migrate", fdb]
    migrate_health_ingest.main()
    fspec = importlib.util.spec_from_file_location("fitlog_under_test", os.path.join(here, "app.py"))
    A = importlib.util.module_from_spec(fspec)
    fspec.loader.exec_module(A)
    import health_ingest as HI
    A.app.config["TESTING"] = True
    fc = A.app.test_client()
    fc.post("/setup", data={"pw": "testpw1", "okey": "ownerkey1"})
    fs = make_server("127.0.0.1", fport, A.app)
    threading.Thread(target=fs.serve_forever, daemon=True).start()
    FURL = "http://127.0.0.1:%d" % fport

    T = date.today().isoformat()
    D = T + " 00:00:00 +0530"
    payload = {"data": {
        "metrics": [
            {"name": "step_count", "units": "count", "data": [{"date": D, "qty": 8421}]},
            {"name": "apple_exercise_time", "units": "min", "data": [{"date": D, "qty": 52}]},
            {"name": "mindful_minutes", "units": "min", "data": [{"date": D, "qty": 12}]}],
        "workouts": [
            {"name": "Outdoor Walk", "start": T + " 07:05:00 +0530", "end": T + " 07:45:00 +0530",
             "duration": 2400, "distance": {"qty": 3.1, "units": "km"}},
            {"name": "Indoor Cycling", "start": T + " 18:00:00 +0530", "end": T + " 18:20:00 +0530",
             "duration": 1200},
            {"name": "Walk", "location": "Indoor", "start": T + " 19:00:00 +0530",
             "end": T + " 19:15:00 +0530", "duration": 900},
            {"name": "<b>x</b>", "start": T + " 20:00:00 +0530", "end": T + " 20:05:00 +0530",
             "duration": 300}]}}
    H = {"Authorization": "Bearer " + tok}

    def fq(sql, a=()):
        con = sqlite3.connect(fdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def t00_classify():
        c = HI.classify_workout
        cases = [("Outdoor Walk", "walk"), ("Walk (indoor)", "treadmill"), ("Indoor Walk", "treadmill"),
                 ("Treadmill", "treadmill"), ("Hiking", "walk"), ("Outdoor Cycling", "cycle_road"),
                 ("Cycling", "cycle_road"), ("Indoor Cycling", "cycle_static"), ("Stationary Bike", "cycle_static"),
                 ("Mind and Body", "meditation"), ("Mindfulness", "meditation"), ("Yoga", "other"),
                 ("Outdoor Run", "other"), ("", "other")]
        bad = [(w, c(w), k) for w, k in cases if c(w) != k]
        assert not bad, str(bad)
        return str(len(cases)) + " workout names classified"

    def t01_ingest():
        r = fc.post("/api/ingest?source=applewatch", data=json.dumps(payload),
                    headers={"Authorization": "Bearer ingest-test-token-not-real",
                             "Content-Type": "application/json"})
        j = r.get_json()
        assert r.status_code == 200 and "mindful_minutes" not in j["skipped_metrics"], str(j)
        assert fq("SELECT value FROM health_metrics WHERE metric='mindful_min'")[0][0] == 12
        names = [x[0] for x in fq("SELECT wtype FROM health_workouts ORDER BY start_ts")]
        assert names[2] == "Walk (indoor)" and names[1] == "Indoor Cycling", str(names)
        fq("INSERT INTO health_workouts(date,start_ts,end_ts,wtype,duration_s,source,ingested_at) "
           "VALUES(?,?,?,?,?,?,?)", (T, T + "T10:00:00", T + "T11:00:00", "Walking", 3600, "healthconnect", "t"))
        return "mindful minutes kept; indoor walk stored as 'Walk (indoor)'"

    def t02_feed_auth():
        for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": tok}):
            assert fc.get("/api/feed/activity?day=" + T, headers=h).status_code == 401, str(h)
        assert fc.get("/api/feed/activity?day=" + T, headers=H).status_code == 200
        return "no/wrong/bare token -> 401; GutLog's token -> 200 (no login needed)"

    def t03_feed_content():
        j = fc.get("/api/feed/activity?day=" + T, headers=H).get_json()
        assert j["steps"] == 8421 and j["mindful_min"] == 12 and j["exercise_minutes"] == 52, str(j)
        kinds = [w["kind"] for w in j["workouts"]]
        assert kinds == ["walk", "cycle_static", "treadmill", "other"], "kinds " + str(kinds)
        w = j["workouts"][0]
        assert w["minutes"] == 40 and w["distance_km"] == 3.1 and w["start"].endswith("07:05:00"), str(w)
        return "8421 steps, 52 exercise min, 12 mindful; watch only (Health Connect duplicate dropped)"

    def t04_bad_day():
        j = fc.get("/api/feed/activity?day=nonsense", headers=H).get_json()
        assert j["day"] == T, j["day"]
        return "a bad date falls back to today"

    def t05_gutlog_pulls_watch():
        os.environ["GUTLOG_LINKS"] = "1"
        G.FITLOG_URL = FURL
        G.RXGUARD_URL = "http://127.0.0.1:9"
        G._LINK_CACHE.clear()
        G.now_hm = lambda: "23:59"   # the fixture's times are fixed; the clock may be earlier
        assert gc.post("/api/activity", json={"kind": "walk", "minutes": 30, "intensity": "Moderate",
                                              "day": T, "atime": "07:15"}).get_json()["ok"]
        assert gc.post("/api/activity", json={"kind": "meditation", "minutes": 15, "day": T,
                                              "atime": "06:30"}).get_json()["ok"]
        j = gc.get("/api/activity?day=" + T).get_json()
        walks = [i for i in j["items"] if i["kind"] == "walk"]
        assert len(walks) == 1 and walks[0]["confirmed"] and walks[0]["minutes"] == 40, str(walks)
        assert j["summary"]["steps"] == 8421 and j["watch"]["ok"], str(j["summary"])
        assert not any(i["label"] == "Mindful minutes" for i in j["items"]), "mindful doubled the tap"
        return "GutLog card: watch walk confirms the 07:15 tap; steps 8421; no doubles"

    def home():
        A._GA_CACHE.clear()
        return fc.get("/").get_data(as_text=True)

    def t06_home_card():
        h = home()
        assert "Activity today" in h and "8,421 steps" in h and "52 exercise min" in h, "header"
        assert "⌚ Walk 40 min · 3.1 km" in h, "watch walk"
        assert "Meditation 15 min (GutLog)" in h, "GutLog tap missing"
        assert "Walk 30 min" not in h, "a tap the watch recorded shows twice"
        assert "Mindful minutes" not in h, "mindful doubled the tapped meditation"
        assert "health.dr-manoj.in/?open=act" in h, "GutLog link"
        return "Home card: watch + GutLog taps, matched walk once, link to GutLog"

    def t07_escaping():
        h = home()
        assert "<b>x</b>" not in h and "&lt;b&gt;x&lt;/b&gt;" in h, "workout name not escaped"
        return "workout names from the watch are escaped"

    def t08_cache_not_mutated():
        home()
        h = fc.get("/").get_data(as_text=True)
        assert "Meditation 15 min (GutLog)" in h and "Walk 30 min" not in h, "second render differs"
        return "a cached render repeats identically"

    def t09_scratch_isolated():
        os.environ["FITLOG_GUTLOG_FEED"] = ""
        try:
            h = home()
            assert "(GutLog)" not in h and "8,421 steps" in h, "scratch DB read GutLog"
        finally:
            os.environ["FITLOG_GUTLOG_FEED"] = "1"
        return "scratch DB: watch data only, GutLog never read"

    def t10_gutlog_down():
        old = A.GUTLOG_FEED_URL
        A.GUTLOG_FEED_URL = "http://127.0.0.1:9"
        try:
            h = home()
            assert "GutLog not reachable" in h and "8,421 steps" in h, "down message"
        finally:
            A.GUTLOG_FEED_URL = old
        return "GutLog down -> card still shows watch data, says so"

    def t11_token_missing():
        old = A.GUTLOG_TOKEN_FILE
        A.GUTLOG_TOKEN_FILE = os.path.join(work, "nope")
        try:
            assert fc.get("/api/feed/activity", headers=H).status_code == 401
            assert fc.get("/api/feed/activity", headers={"Authorization": "Bearer "}).status_code == 401
        finally:
            A.GUTLOG_TOKEN_FILE = old
        return "no token file -> feed closed (even to an empty Bearer)"

    tests = [
        ("00 classify workouts", t00_classify),
        ("01 ingest: mindful + indoor", t01_ingest),
        ("02 feed auth", t02_feed_auth),
        ("03 feed content", t03_feed_content),
        ("04 bad day", t04_bad_day),
        ("05 GutLog pulls watch data", t05_gutlog_pulls_watch),
        ("06 Home activity card", t06_home_card),
        ("07 escaping", t07_escaping),
        ("08 cache not mutated", t08_cache_not_mutated),
        ("09 scratch DB isolated", t09_scratch_isolated),
        ("10 GutLog down", t10_gutlog_down),
        ("11 token file missing", t11_token_missing),
    ]
    print("=" * 66)
    print("FitLog v1.2.0 - activity feed test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    gs.shutdown()
    fs.shutdown()
    os.environ.pop("GUTLOG_LINKS", None)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

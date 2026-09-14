#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.6.0 -- the trend leaves GutLog's down days out (FITLOG_V160_DOWNDAYS).

Runs a REAL GutLog (from ../gutlog/app.py, or GUTLOG_APP) on a loopback port
so FitLog reads its down-day feed the way it does in production, and asserts
on the rendered /watch page. Scratch databases only; FITLOG_GUTLOG_FEED=1 so
the feed is read from a scratch DB at all. Every case here fails against
FitLog v1.5.0 and is declared in new_assertions_v160.json. Python 3.9.

  python3 test_downdays_trend.py [path/to/fitlog/app.py]
"""
import importlib.util
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, timedelta

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
    fit_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    gut_path = os.environ.get("GUTLOG_APP") or os.path.join(os.path.dirname(here), "gutlog", "app.py")
    if not os.path.exists(gut_path):
        print("FATAL: GutLog not found at " + gut_path)
        return 1
    T = date.today()
    D = dict((n, (T - timedelta(days=n)).isoformat()) for n in range(0, 40))

    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    gport = free_port()
    os.environ.update(
        GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
        GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
        GUTLOG_ICONS=os.path.dirname(gut_path), GUTLOG_FEED_TOKEN_FILE=tokf,
        GUTLOG_LINKS="0", GUTLOG_FEED_URL="http://127.0.0.1:%d" % gport)
    sys.path.insert(0, os.path.dirname(gut_path))
    gl = load("gutlog_dd", gut_path)
    gc = gl.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    gc.get("/login")
    has_down = hasattr(gl, "_down_runs")

    os.environ.update(FITLOG_DB=os.path.join(work, "f.db"),
                      FITLOG_SECRET="test-secret-not-real", FITLOG_GUTLOG_FEED="1")
    sys.path.insert(0, here)
    fl = load("fitlog_dd", fit_path)
    fdb = os.environ["FITLOG_DB"]
    _argv = sys.argv[:]
    mig = load("migrate_dd", os.path.join(here, "migrate_health_ingest.py"))
    sys.argv = ["migrate", fdb]
    assert mig.main() == 0
    sys.argv = _argv

    from werkzeug.serving import make_server
    gsrv = make_server("127.0.0.1", gport, gl.app)
    threading.Thread(target=gsrv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    fcon = sqlite3.connect(fdb)
    for n in range(10, 0, -1):
        fcon.execute("INSERT OR REPLACE INTO health_metrics(date,metric,value,unit,source,ingested_at) "
                     "VALUES(?,?,?,?,?,?)", (D[n], "steps", 6000, "count", "applewatch", "x"))
        fcon.execute("INSERT OR REPLACE INTO health_metrics(date,metric,value,unit,source,ingested_at) "
                     "VALUES(?,?,?,?,?,?)", (D[n], "sleep_hours", 6.5, "h", "applewatch", "x"))
    fcon.execute("INSERT OR REPLACE INTO health_metrics(date,metric,value,unit,source,ingested_at) "
                 "VALUES(?,?,?,?,?,?)", (D[4], "steps", 100, "count", "applewatch", "x"))
    fcon.commit()
    fcon.close()

    if has_down:
        gc.post("/api/downday", data=json.dumps({"day": D[4]}), content_type="application/json")

    with fl.app.app_context():
        fl.set_setting("password_hash", fl.sha("pw"))
    fl.app.config["TESTING"] = True
    fc = fl.app.test_client()
    with fc.session_transaction() as s:
        s["auth"] = True
    tok = open(tokf, encoding="utf-8").read().strip()

    def t01():
        assert hasattr(fl, "gutlog_downdays"), "no gutlog_downdays reader"
        with fl.app.app_context():
            days, err = fl.gutlog_downdays(D[20])
        assert has_down, "GutLog under test has no down days; the reader cannot be exercised"
        assert D[4] in days and not err, (days, err)
        return "read " + str(sorted(days)) + " through the bearer feed"
    check("01 FitLog reads GutLog's down days through the feed", t01)

    def t02():
        html = fc.get("/watch").get_data(as_text=True)
        assert "Traceback" not in html
        assert 'class="wbar down"' in html, "the down day's bar is not marked"
        assert "down day (GutLog)" in html, "the bar's title does not say what it is"
        n = html.count('class="wbar down"')
        assert n == 1, "marked bars: " + str(n)
        return "one bar marked and named"
    check("02 the down day's bar is drawn, marked and named, not hidden", t02)

    def t03():
        html = fc.get("/watch").get_data(as_text=True)
        m = re.search(r"Steps</td><td>(\d+)[^<]*</td><td>(\d+)", html)
        assert m, "no steps row in the trend table"
        assert m.group(1) == "6000" and m.group(2) == "6000", (m.group(1), m.group(2))
        assert "leaving out 1 down day marked in GutLog" in html
        return "mean 6000 and low 6000 over the nine kept days; the note says one was left out"
    check("03 mean, low and high leave the down day out and say so", t03)

    def t04():
        r = fl.app.test_client().get("/api/feed/watch?days=180",
                                     headers={"Authorization": "Bearer " + tok})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"], j
        assert j["days"] == 180 and len(j["daily"]) == 180, (j["days"], len(j["daily"]))
        d4 = [x for x in j["daily"] if x["date"] == D[4]][0]
        assert "sleep_hours" in d4["metrics"] and d4["metrics"]["sleep_hours"]["value"] == 6.5, d4["metrics"]
        return "180 days answered; sleep_hours carried"
    check("04 the watch feed reaches back 180 days and carries sleep", t04)

    def t05():
        gsrv.shutdown()
        time.sleep(0.2)
        fl._GD_CACHE.clear()
        with fl.app.app_context():
            days, err = fl.gutlog_downdays(D[20])
        assert days == set() and err and err != "off", (days, err)
        html = fc.get("/watch").get_data(as_text=True)
        assert "Traceback" not in html and "could not be read from GutLog" in html
        assert 'class="wbar down"' not in html
        return "empty set plus a reason; the page says down days could not be read"
    check("05 GutLog down degrades to a message, never an exception", t05)

    ok = all(r[0] for r in RESULTS)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

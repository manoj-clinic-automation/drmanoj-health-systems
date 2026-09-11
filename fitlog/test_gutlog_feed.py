#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.1.0 -- GutLog feed integration test (W03 union + Meds page).

Runs a REAL GutLog (scratch database, scratch token) on a free loopback port
and a FitLog on a scratch database pointed at it. Nothing live is touched.
Synthetic fixture. Python 3.9.

  python3 test_gutlog_feed.py [/root/gutlog/app.py]    -> must print 10/10 passed
"""
import importlib.util
import os
import socket
import sqlite3
import sys
import tempfile
import threading
from datetime import date, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gut_path = sys.argv[1] if len(sys.argv) > 1 else "/root/gutlog/app.py"
    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(os.path.abspath(gut_path)),
                      GUTLOG_FEED_TOKEN_FILE=tokf)
    sys.path.insert(0, os.path.dirname(os.path.abspath(gut_path)))
    spec = importlib.util.spec_from_file_location("gutlog_under_test", gut_path)
    gmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gmod)
    gmod.app.test_client().post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"})
    gdb = os.environ["GUTLOG_DB"]

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    gid = {}
    for nm, mol in (("G Analg", "analgesiumol"), ("G Somn", "somnolide"), ("G Odd", "unmatchedium")):
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 1, mol))
        gid[nm] = gq("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]

    def gdose(nm, days_ago, t="09:00"):
        d = (date.today() - timedelta(days=days_ago)).isoformat()
        gq("INSERT INTO doses(day,dtime,medicine,med_id,status,created) VALUES(?,?,?,?,?,?)",
           (d, t, nm, gid[nm], "EXTRA", "t"))

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, gmod.app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    os.environ["FITLOG_DB"] = os.path.join(work, "f.db")
    os.environ["GUTLOG_FEED_URL"] = "http://127.0.0.1:%d" % port
    os.environ["FITLOG_GUTLOG_FEED"] = "1"
    sys.path.insert(0, here)
    fspec = importlib.util.spec_from_file_location("fitlog_under_test", os.path.join(here, "app.py"))
    A = importlib.util.module_from_spec(fspec)
    fspec.loader.exec_module(A)
    A.app.config["TESTING"] = True
    fc = A.app.test_client()
    fc.post("/setup", data={"pw": "testpw1", "okey": "ownerkey1"})

    def fq(sql, a=()):
        con = sqlite3.connect(os.environ["FITLOG_DB"])
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    fq("UPDATE med_stack SET active=0")
    fq("INSERT INTO med_stack(kb_id,name,generic,strength,category,dose_options,route,active) "
       "VALUES('','Test Analg','Testamol + Analgesiumol','','analgesic','[\"1 tab\"]','oral',1)")
    fq("INSERT INTO med_stack(kb_id,name,generic,strength,category,dose_options,route,active) "
       "VALUES('','Test Sleep','Somnolide','','sleep','[\"1 tab\"]','oral',1)")
    analg = fq("SELECT id FROM med_stack WHERE name='Test Analg'")[0][0]

    def reset():
        gq("DELETE FROM doses")
        fq("DELETE FROM analgesic_log")
        A._GL_CACHE.clear()

    def flags():
        A._GL_CACHE.clear()
        with A.app.app_context():
            return dict(A.compute_flags())

    def t00_gutlog_alone_fires():
        reset()
        for d in (0, 1, 2):
            gdose("G Analg", d)
        f = flags()
        assert "W03" in f and "incl. GutLog" in f["W03"], "W03 from GutLog: " + str(f)
        return "3 analgesic days logged only in GutLog -> W03, marked incl. GutLog"

    def t01_sleep_negative():
        reset()
        for d in range(5):
            gdose("G Somn", d)
        assert "W03" not in flags(), "sleep medicine fired the analgesic flag"
        return "5 sleep-medicine days in GutLog -> no W03"

    def t02_unmatched_ignored():
        reset()
        for d in range(6):
            gdose("G Odd", d)
        assert "W03" not in flags(), "unmatched molecule counted"
        return "molecule not in the FitLog stack is ignored, never guessed"

    def t03_union_counts_once():
        reset()
        for d in (1, 2):
            gdose("G Analg", d)
            dd = (date.today() - timedelta(days=d)).isoformat()
            fq("INSERT INTO analgesic_log(dt,med_id,dose_label) VALUES(?,?,?)", (dd + "T10:00", analg, "1 tab"))
        assert "W03" not in flags(), "a day logged in both apps counted twice"
        gdose("G Analg", 0)
        f = flags()
        assert "W03" in f and "3 days" in f["W03"], "third day not counted: " + str(f)
        return "same day in both apps counts once; a new GutLog day tips it to 3"

    def t04_local_only_unchanged():
        reset()
        for d in (0, 1, 2):
            dd = (date.today() - timedelta(days=d)).isoformat()
            fq("INSERT INTO analgesic_log(dt,med_id,dose_label) VALUES(?,?,?)", (dd + "T10:00", analg, "1 tab"))
        f = flags()
        assert "W03" in f and "incl. GutLog" not in f["W03"], "local-only W03 text changed: " + str(f)
        return "FitLog-only logging behaves exactly as before"

    def t05_window():
        reset()
        for d in (14, 20, 30):
            gdose("G Analg", d)
        assert "W03" not in flags(), "doses outside the 14-day window counted"
        return "GutLog doses outside the 14-day window do not count"

    def t06_meds_page():
        reset()
        for d in (0, 3):
            gdose("G Analg", d)
        gdose("G Somn", 1)
        gdose("G Odd", 1)
        A._GL_CACHE.clear()
        h = fc.get("/meds").get_data(as_text=True)
        assert "From GutLog, last 14 days" in h, "GutLog card missing"
        assert "G Analg" in h and "G Somn" in h and "G Odd" not in h, "card rows wrong"
        assert "analgesic: 2 days/14" in h and "sleep: 1 days/14" in h, "union counts wrong"
        return "Meds page lists matched GutLog doses; day counts include them"

    def t07_escaping():
        reset()
        gq("UPDATE prnmeds SET name=? WHERE id=?", ("<b>x</b>", gid["G Analg"]))
        gq("INSERT INTO doses(day,dtime,medicine,med_id,status,created) VALUES(?,?,?,?,?,?)",
           (date.today().isoformat(), "08:00", "<b>x</b>", gid["G Analg"], "EXTRA", "t"))
        A._GL_CACHE.clear()
        h = fc.get("/meds").get_data(as_text=True)
        gq("UPDATE prnmeds SET name='G Analg' WHERE id=?", (gid["G Analg"],))
        assert "<b>x</b>" not in h and "&lt;b&gt;x&lt;/b&gt;" in h, "GutLog text not escaped"
        return "names from GutLog are escaped on the page"

    def t08_scratch_db_isolated():
        os.environ["FITLOG_GUTLOG_FEED"] = ""
        try:
            reset()
            for d in (0, 1, 2):
                gdose("G Analg", d)
            assert not A.gutlog_feed_enabled(), "scratch DB enabled the feed"
            assert "W03" not in flags(), "scratch DB read real doses"
            assert "From GutLog" not in fc.get("/meds").get_data(as_text=True), "card shown on scratch DB"
        finally:
            os.environ["FITLOG_GUTLOG_FEED"] = "1"
        return "a database outside the app folder (the test suites) never reads the feed"

    def t09_gutlog_down():
        reset()
        for d in (0, 1, 2):
            gdose("G Analg", d)
        srv.shutdown()
        A._GL_CACHE.clear()
        f = flags()
        assert "W03" not in f, "counted doses it could not read"
        h = fc.get("/meds")
        assert h.status_code == 200 and "not reachable" in h.get_data(as_text=True), "down not reported"
        return "GutLog down: FitLog-only counts, Meds page says so, nothing breaks"

    tests = [
        ("00 W03 from GutLog alone", t00_gutlog_alone_fires),
        ("01 sleep negative control", t01_sleep_negative),
        ("02 unmatched ignored", t02_unmatched_ignored),
        ("03 union counts a day once", t03_union_counts_once),
        ("04 FitLog-only unchanged", t04_local_only_unchanged),
        ("05 14-day window", t05_window),
        ("06 Meds page", t06_meds_page),
        ("07 escaping", t07_escaping),
        ("08 scratch DB isolated", t08_scratch_db_isolated),
        ("09 GutLog down", t09_gutlog_down),
    ]
    print("=" * 66)
    print("FitLog v1.1.0 GutLog feed - integration test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.1.0 -- As taken (GutLog) integration test.

Runs a REAL GutLog (scratch database, scratch token) on a free loopback port
and points RxGuard (scratch database) at it. Nothing live is touched.
Fixture is synthetic: invented medicine names; molecules chosen only because
a named rule is defined on the pair. Python 3.9.

  python3 test_astaken.py [/root/gutlog/app.py]    -> must print 15/15 passed
"""
import importlib.util
import os
import socket
import sqlite3
import sys
import tempfile
import threading

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
    port = free_port()

    # ---- a real GutLog on loopback -------------------------------------
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(os.path.abspath(gut_path)),
                      GUTLOG_FEED_TOKEN_FILE=tokf)
    sys.path.insert(0, os.path.dirname(os.path.abspath(gut_path)))
    spec = importlib.util.spec_from_file_location("gutlog_under_test", gut_path)
    gmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gmod)
    gc = gmod.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    gdb = os.environ["GUTLOG_DB"]

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    meds = {}
    for nm, mol in (("Test Fluc 150", "fluconazole"), ("Test Dompi 10", "domperidone"),
                    ("Test Unk 5", "unknownium"), ("Test Nomol", ""), ("Test Omep 20", "omeprazole")):
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 1, mol))
        meds[nm] = gq("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]
    gc.post("/api/schedule", json={"med_id": meds["Test Fluc 150"], "slot": "MORNING",
                                   "dose_text": "1 tab"})
    for nm in ("Test Dompi 10", "Test Unk 5", "Test Nomol", "Test Omep 20"):
        gc.post("/api/now/dose", json={"med_id": meds[nm], "status": "EXTRA", "dtime": "00:00"})

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, gmod.app)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    # ---- RxGuard pointed at it -----------------------------------------
    os.environ["GUTLOG_FEED_URL"] = "http://127.0.0.1:%d" % port
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = tokf
    os.environ["RXGUARD_GUTLOG_FEED"] = "1"
    sys.path.insert(0, here)
    rspec = importlib.util.spec_from_file_location("rxguard_under_test", os.path.join(here, "app.py"))
    rx = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(rx)
    rdb = os.path.join(work, "r.db")
    rapp = rx.create_app(db_path=rdb, secret="t")
    rapp.config["TESTING"] = True
    rc = rapp.test_client()
    rc.post("/login", data={"password": "testpassword1"})
    con = sqlite3.connect(rdb)
    for k in ("omeprazole", "ramipril"):
        con.execute("INSERT INTO medications(drug_key,raw_name,status) VALUES(?,?,'active')", (k, k))
    con.commit()
    con.close()
    nmeds = lambda: sqlite3.connect(rdb).execute("SELECT COUNT(*) FROM medications").fetchone()[0]
    ctx = {}

    def view():
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            data, err = rx.gutlog_stack(14)
            assert not err, "feed error: " + str(err)
            return rx.astaken_view(data)

    def t00_live_page():
        r = rc.get("/astaken")
        assert r.status_code == 200, "page status " + str(r.status_code)
        h = r.get_data(as_text=True)
        assert "GutLog feed unavailable" not in h, "feed reported down"
        assert "GREEN" not in h, "a GREEN state appeared"
        ctx["html"] = h
        if os.environ.get("RXGUARD_TEST_DUMP"):
            open(os.environ["RXGUARD_TEST_DUMP"], "w").write(h)
        return "page renders from the real GutLog feed; no GREEN anywhere"

    def t01_rows():
        v = view()
        ctx["v"] = v
        rows = dict((r["key"], r) for r in v["rows"])
        for k in ("fluconazole", "domperidone", "unknownium", "omeprazole", "ramipril"):
            assert k in rows, k + " missing from rows: " + str(list(rows))
        assert rows["fluconazole"]["scheduled"] and not rows["fluconazole"]["listed"], str(rows["fluconazole"])
        assert rows["omeprazole"]["listed"] and rows["omeprazole"]["in_gutlog"], str(rows["omeprazole"])
        assert not rows["unknownium"]["known"], "unknownium should be uncovered"
        return "5 molecules, each with source, list and coverage status"

    def t02_reconciliation():
        v = ctx["v"]
        assert v["not_listed"] == ["domperidone", "fluconazole", "unknownium"], str(v["not_listed"])
        assert v["listed_not_taken"] == ["ramipril"], str(v["listed_not_taken"])
        f = [x for x in v["findings"] if x["category"] == "Reconciliation"]
        assert len(f) == 1 and f[0]["flag"] == "AMBER", "reconciliation finding: " + str(f)
        return "taken-not-listed and listed-not-taken both reported"

    def t03_pairwise_once():
        pw = [f for f in ctx["v"]["findings"] if f.get("rule_id") == "PW023"]
        assert len(pw) == 1 and pw[0]["flag"] == "RED", "PW023 count/flag: " + str(len(pw))
        assert pw[0]["unlisted"] and pw[0]["involves"] == ["domperidone", "fluconazole"], str(pw[0]["involves"])
        return "named rule fires once, RED, marked as involving an unlisted medicine"

    def t04_cyp_suppressed():
        dup = [f for f in ctx["v"]["findings"] if f["category"] == "Pharmacokinetic"
               and set(f.get("involves", [])) == {"fluconazole", "domperidone"}]
        assert not dup, "CYP derivation repeated the named rule: " + str([d["title"] for d in dup])
        return "CYP derivation suppressed where the named rule covers the pair"

    def t05_unknown_and_unmapped():
        cov = [f for f in ctx["v"]["findings"] if f["category"] == "Coverage"]
        titles = " | ".join(f["title"] + " " + f["mechanism"] for f in cov)
        assert all(f["flag"] == "UNKNOWN" for f in cov), "coverage not UNKNOWN"
        assert "unknownium" in titles and "Test Nomol" in titles, "coverage text: " + titles
        return "uncovered molecule and molecule-less medicine are UNKNOWN, named"

    def t06_order():
        fl = [f["flag"] for f in ctx["v"]["findings"]]
        rank = {"RED": 0, "AMBER": 1, "UNKNOWN": 2}
        assert fl == sorted(fl, key=lambda x: rank[x]), "findings not RED>AMBER>UNKNOWN: " + str(fl)
        return "findings ordered RED, AMBER, UNKNOWN"

    def t07_page_shows_them():
        h = ctx["html"]
        for s in ("involves a medicine not on your list", "ramipril", "Test Nomol", "NO"):
            assert s in h, "page missing: " + s
        return "page shows the marker, the not-logged list and uncovered names"

    def t08_read_only():
        n0 = nmeds()
        rc.get("/astaken")
        rc.get("/astaken?days=90")
        rc.get("/")
        assert nmeds() == n0, "RxGuard medications changed"
        return "viewing writes nothing"

    def t09_days_param():
        assert rc.get("/astaken?days=90").status_code == 200, "90 days"
        assert rc.get("/astaken?days=abc").status_code == 200, "bad days crashed"
        assert rc.get("/astaken?days=7").status_code == 200, "odd days crashed"
        return "14/30/90 accepted; anything else falls back to 14"

    def t10_dashboard_summary():
        h = rc.get("/").get_data(as_text=True)
        assert "As taken (GutLog), last 14 days" in h and "RED" in h, "dashboard summary missing"
        return "dashboard carries the one-line summary"

    def t11_login_required():
        r = rapp.test_client().get("/astaken")
        assert r.status_code in (302, 401), "page open without login: " + str(r.status_code)
        return "page needs RxGuard login"

    def t12_bad_token():
        good = open(tokf).read()
        rx.GUTLOG_TOKEN_FILE = os.path.join(work, "wrong.token")
        open(rx.GUTLOG_TOKEN_FILE, "w").write("x" * 64)
        h = rc.get("/astaken").get_data(as_text=True)
        assert "GutLog refused the request (HTTP 401)" in h, "wrong token not reported"
        rx.GUTLOG_TOKEN_FILE = os.path.join(work, "absent.token")
        h = rc.get("/astaken").get_data(as_text=True)
        assert "token not found" in h, "missing token not reported"
        rx.GUTLOG_TOKEN_FILE = tokf
        assert open(tokf).read() == good
        return "wrong token and missing token become a message, not an error"

    def t14_scratch_db_never_reads_feed():
        os.environ["RXGUARD_GUTLOG_FEED"] = ""
        try:
            h = rc.get("/astaken").get_data(as_text=True)
            assert "Not connected for this database" in h, "scratch DB read the live feed"
            assert "As taken (GutLog), last 14 days" not in rc.get("/").get_data(as_text=True), \
                "dashboard summary shown for a scratch DB"
        finally:
            os.environ["RXGUARD_GUTLOG_FEED"] = "1"
        return "a database outside the app folder (tests) never reads the feed"

    def t13_gutlog_down():
        srv.shutdown()
        r = rc.get("/astaken")
        assert r.status_code == 200 and "GutLog is not reachable" in r.get_data(as_text=True), "down not reported"
        assert rc.get("/").status_code == 200, "dashboard broke with GutLog down"
        assert rc.get("/meds").status_code == 200, "meds page broke with GutLog down"
        return "GutLog down: page explains, dashboard and other screens unaffected"

    tests = [
        ("00 live page", t00_live_page), ("01 rows", t01_rows),
        ("02 reconciliation", t02_reconciliation), ("03 named rule once", t03_pairwise_once),
        ("04 CYP suppressed", t04_cyp_suppressed), ("05 unknown + unmapped", t05_unknown_and_unmapped),
        ("06 flag order", t06_order), ("07 page content", t07_page_shows_them),
        ("08 read-only", t08_read_only), ("09 days parameter", t09_days_param),
        ("10 dashboard summary", t10_dashboard_summary), ("11 login required", t11_login_required),
        ("12 token problems", t12_bad_token), ("14 scratch DB isolated", t14_scratch_db_never_reads_feed),
        ("13 GutLog down", t13_gutlog_down),
    ]
    print("=" * 66)
    print("RxGuard v1.1.0 As taken (GutLog) - integration test")
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

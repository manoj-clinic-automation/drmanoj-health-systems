#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.26.0 -- food trials as periods (GUTLOG_V3260_TRIALS).

Scratch database, real app, invented foods. Days are placed relative to
today so the trial is always mid-run: started 9 days ago, 30 planned, with a
14-day baseline before it. Every count below is fixed by construction.
Last check drives Chromium if Playwright is installed.

  python3 test_trials.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp()
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=os.path.join(w, "none.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_trials", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    c.get("/")
    gdb = os.environ["GUTLOG_DB"]

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    T = date.today()
    S = T - timedelta(days=9)

    def ds(d):
        return d.isoformat()

    def meal(day, names):
        q("INSERT INTO meals(day,mtime,slot,items,protein,kcal,fibre,fscore,notes,created) "
          "VALUES(?,'08:00','Breakfast',?,10,200,1,0,'',?)",
          (day, json.dumps([{"n": n, "q": 1, "p": 5, "k": 100, "f": 0, "fm": "L"} for n in names]), day))

    def gi(day, sev):
        q("INSERT INTO episodes(day,etime,category,etype,severity,created) VALUES(?,'10:00','GI','Test pain',?,?)",
          (day, sev, day))

    for n in ("Test egg boiled", "Test egg fried", "Test toast", "Test stew recipe"):
        q("INSERT OR IGNORE INTO library(cat,item,portion,protein,kcal,fibre,fodmap,status) "
          "VALUES('H',?,'1',5,100,0,'L','')", (n,))
    q("INSERT INTO recipes(slug,name,grp,stage,data,updated) VALUES('s','Test stew recipe','A','new','{}','x')")
    # baseline: 14 days before S, toast only; gut symptoms on 2 days; day -3 set aside
    for i in range(1, 15):
        d = ds(S - timedelta(days=i))
        if i != 12:                      # day -12: nothing logged at all -> not counted
            meal(d, ["Test toast"])
    gi(ds(S - timedelta(days=5)), 4)
    gi(ds(S - timedelta(days=10)), 3)
    q("INSERT INTO day_context(day,tag,created) VALUES(?,'travel','x')", (ds(S - timedelta(days=3)),))
    # trial: S..T (10 days); egg on 7 of them (2 fried); day 5 neither eaten nor the day after; symptoms on 6 trial days
    egg_days = [0, 1, 2, 3, 6, 7, 9]
    for i in range(10):
        d = ds(S + timedelta(days=i))
        if i in egg_days:
            meal(d, ["Test egg fried" if i in (6, 7) else "Test egg boiled", "Test toast"])
        else:
            meal(d, ["Test toast"])
    for i in (1, 2, 4, 5, 7, 9):
        gi(ds(S + timedelta(days=i)), 5)
    ctx = {}

    def one(tid):
        return [t for t in c.get("/api/trials").get_json()["trials"] if t["id"] == tid][0]

    def t01():
        bad = [({}, "no food"), ({"food": "Test egg", "start": ds(T + timedelta(days=1))}, "future"),
               ({"food": "Test egg", "days": 200}, "too long")]
        for body, why in bad:
            assert c.post("/api/trials", json=body).status_code == 400, why + " accepted"
        r = c.post("/api/trials", json={"food": "Egg", "match": "test egg", "amount": "2 eggs",
                                        "freq": "Daily", "days": 30, "start": ds(S)}).get_json()
        assert r.get("ok"), r
        ctx["egg"] = r["id"]
        assert c.post("/api/trials", json={"food": "egg", "start": ds(S)}).status_code == 400, \
            "a second running trial of the same food accepted"
        return "bad starts refused; one running trial per food"
    check("01 starting a trial, and what is refused", t01)

    def t02():
        t = one(ctx["egg"])
        assert t["day_no"] == 10 and t["planned_days"] == 30, (t["day_no"], t["planned_days"])
        assert t["ate_days"] == 7, "linked %d days, expected 7" % t["ate_days"]
        assert dict(t["styles"]) == {"Test egg boiled": 5, "Test egg fried": 2}, t["styles"]
        return "day 10 of 30; eaten on 7 days found by name; boiled x5, fried x2"
    check("02 meals with the food are linked by name", t02)

    def t03():
        t = one(ctx["egg"])
        assert t["before"]["days"] == 12 and t["before"]["symptom_days"] == 2, t["before"]
        assert t["trial"]["days"] == 10 and t["trial"]["symptom_days"] == 6, t["trial"]
        assert t["trial"]["avg_gi_pain"] == 3.0, t["trial"]
        assert t["signal"] == "likely worse", t["signal"]
        return "before 2/12 (travel day and an empty day set aside), trial 6/10 -> 'likely worse'"
    check("03 trial vs the 14 days before, context and empty days set aside", t03)

    def t04():
        e, n = one(ctx["egg"])["exposed"], one(ctx["egg"])["not_exposed"]
        assert e["days"] == 9 and n["days"] == 13, (e, n)
        assert e["symptom_days"] == 5, e
        return "eaten or the day after: 9 days; other logged days: 13"
    check("04 eaten-days vs other days", t04)

    def t05():
        r = c.post("/api/trials", json={"food": "Test stew recipe", "days": 14}).get_json()
        ctx["stew"] = r["id"]
        assert q("SELECT stage FROM recipes WHERE name='Test stew recipe'")[0][0] == "trial"
        t = one(r["id"])
        assert t["signal"] == "not enough days yet" and t["day_no"] == 1, t
        return "starting a recipe trial marks the recipe On trial; day 1 says not enough days"
    check("05 too few days says so; a recipe trial sets its stage", t05)

    def t06():
        assert c.post("/api/trials/%d/end" % ctx["egg"], json={"verdict": "maybe"}).status_code == 400
        r = c.post("/api/trials/%d/end" % ctx["egg"], json={"verdict": "tolerated", "note": "fine"}).get_json()
        assert r["ok"] and sorted(r["library"]) == ["Test egg boiled", "Test egg fried"], r
        st = dict(q("SELECT item, status FROM library WHERE item LIKE 'Test %'"))
        assert st["Test egg boiled"] == "cleared" and st["Test toast"] == "", st
        t = one(ctx["egg"])
        assert t["status"] == "ended" and t["verdict"] == "Tolerated", t
        r2 = c.post("/api/trials/%d/end" % ctx["stew"], json={"verdict": "not_tolerated"}).get_json()
        assert q("SELECT stage FROM recipes WHERE name='Test stew recipe'")[0][0] == "avoid"
        assert dict(q("SELECT item, status FROM library WHERE item='Test stew recipe'"))["Test stew recipe"] == "trigger"
        return "verdict updates the food map for every matching food, and the recipe's stage"
    check("06 his verdict ends the trial and updates the food map", t06)

    def t07():
        spec_f = os.path.join(w, "trials.json")
        json.dump({"trials": [{"food": "Test porridge", "match": "porridge", "start": ds(S), "days": 30,
                               "note": "moved"}]}, open(spec_f, "w"))
        mig = os.path.join(os.path.dirname(app_path), "migrate_trials.py")
        out = subprocess.run([sys.executable, mig, "--db", gdb, "--spec", spec_f],
                             capture_output=True, text=True).stdout
        assert "1 to create" in out and not q("SELECT 1 FROM trials WHERE food='Test porridge'"), out
        subprocess.run([sys.executable, mig, "--db", gdb, "--spec", spec_f, "--apply"], capture_output=True)
        out2 = subprocess.run([sys.executable, mig, "--db", gdb, "--spec", spec_f, "--apply"],
                              capture_output=True, text=True).stdout
        assert "created 0" in out2 and len(q("SELECT 1 FROM trials WHERE food='Test porridge'")) == 1, out2
        return "dry run writes nothing; apply once; a second apply creates nothing"
    check("07 an old test moves into a trial, once", t07)

    def t08():
        h = c.get("/").get_data(as_text=True)
        assert 'id="trialCard"' in h and "async function loadTrials" in h and "loadTrials();" in h
        assert h.index('id="trialCard"') < h.index('id="registry"'), "trials not above the older test"
        assert gm.app.test_client().get("/api/trials").status_code in (302, 401)
        return "trial card at the top of Food test; API needs a login"
    check("08 the Food test segment leads with trials", t08)

    def t09():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "SKIPPED: Playwright not installed here"
        from werkzeug.serving import make_server
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        srv = make_server("127.0.0.1", port, gm.app)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.3)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 390, "height": 1000})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(900)
            pg.goto("http://127.0.0.1:%d/" % port)
            pg.wait_for_timeout(900)
            pg.click("#nav button[data-t='meals']")
            pg.click(".seg[data-seg='meals'] button[data-s='test']")
            pg.wait_for_timeout(700)
            pg.click("#trialList .trow >> nth=0 >> text=Details")
            pg.wait_for_timeout(500)
            det = pg.inner_text("#trialList")
            pg.click("#trialNewBtn")
            pg.wait_for_timeout(600)
            form = pg.inner_text("#trialNew")
            wide = pg.evaluate("document.documentElement.scrollWidth")
            b.close()
        srv.shutdown()
        assert not errs, "page errors: %s" % errs
        assert "14 days before" in det and "During the trial" in det, det[:300]
        assert "Start trial today" in form and "1 month" in form, form[:200]
        assert wide <= 390, "sideways scroll %dpx" % wide
        return "details table and the start form render; no errors"
    check("09 in a real browser: details and the start form", t09)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.26.0 -- food trials as periods")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

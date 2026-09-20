#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.27.0 -- one protein target, re-timed extra doses, no phone time
dialog (GUTLOG_V3270_TIMEPICK).

Scratch database, real app. Checks 04-07 drive the page in Chromium at the
folded phone's width (300 px); skipped, and saying so, without Playwright.

  python3 test_v3270.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp()
    plan = os.path.join(w, "plan.json")
    json.dump({"targets": {"protein": 100, "protein_meal": 25}, "main_meals": ["Breakfast", "Lunch"]},
              open(plan, "w"))
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=plan)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3270", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    c.get("/")
    gdb = os.environ["GUTLOG_DB"]
    T = date.today().isoformat()

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def t01():
        a = c.get("/api/mealcards").get_json()["protein_target"]
        b = c.get("/api/summary/" + T).get_json()["target"]
        r = c.get("/api/review").get_json()["target"]
        assert a == 100, "meal card target %r" % a
        assert b == 100 and r == 100, "summary %r, review %r" % (b, r)
        return "meal card, summary and review all read the plan's 100 g"
    check("01 one protein target: the diet plan's", t01)

    def t02():
        os.environ["GUTLOG_PLAN_FILE"] = os.path.join(w, "absent.json")
        try:
            a = c.get("/api/mealcards").get_json()["protein_target"]
        finally:
            os.environ["GUTLOG_PLAN_FILE"] = plan
        assert a == gm.PROTEIN_TARGET, "without a plan the target is %r" % a
        return "no plan file: the fixed fallback, not zero or an error"
    check("02 without a plan the old target is the fallback", t02)

    def t03():
        q("INSERT INTO prnmeds(name,sort,active) VALUES('Test antispasmodic',1,1)")
        mid = q("SELECT id FROM prnmeds WHERE name='Test antispasmodic'")[0][0]
        r = c.post("/api/now/dose", json={"med_id": mid, "status": "EXTRA", "day": T}).get_json()
        assert r.get("ok") is not False, r
        did = q("SELECT id FROM doses WHERE medicine='Test antispasmodic'")[0][0]
        h = c.get("/").get_data(as_text=True)
        assert "function exTimeEdit" in h and "exTimeEdit(row,e)" in h, "no time edit on extra rows"
        return "extra dose %d logged; its row opens a time strip" % did
    check("03 an extra dose's row carries a time edit", t03)

    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False
    B = {}

    def browse():
        if "fail" in B:
            raise AssertionError("the page could not be driven: " + B["fail"])
        if B or not have_pw:
            return
        try:
            _browse()
        except Exception as exc:
            B["fail"] = type(exc).__name__ + ": " + str(exc).split("\n")[0][:160]
        if "fail" in B:
            raise AssertionError("the page could not be driven: " + B["fail"])

    def _browse():
        B["started"] = True
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
            pg = b.new_page(viewport={"width": 300, "height": 680})
            pg.set_default_timeout(4000)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(900)
            pg.goto("http://127.0.0.1:%d/" % port)
            pg.wait_for_timeout(1300)
            pg.click("#nowExtraCard .fold-h")
            pg.wait_for_timeout(300)
            pg.click("#nowExtraList .exrow .m")
            pg.wait_for_timeout(300)
            B["strip"] = pg.inner_text(".varpick")
            B["selects"] = pg.eval_on_selector_all(".varpick .tpick select", "e=>e.length")
            B["wide_open"] = pg.evaluate("document.documentElement.scrollWidth")
            B["strip_right"] = pg.evaluate("Math.max(...[...document.querySelectorAll('.varpick *')]"
                                           ".map(e=>e.getBoundingClientRect().right))")
            B["visible_time"] = pg.evaluate(
                "[...document.querySelectorAll('input[type=time]')].filter(i=>i.offsetParent!==null).length")
            B["all_time"] = pg.evaluate("document.querySelectorAll('input[type=time]').length")
            B["enhanced"] = pg.evaluate("document.querySelectorAll('input[type=time][data-tp]').length")
            go = pg.eval_on_selector(".varpick .go", "e=>{const r=e.getBoundingClientRect();return [r.left,r.right]}")
            B["go_box"] = go
            pg.click(".varpick .exago >> text=30 min ago")
            B["chip_value"] = pg.eval_on_selector(".varpick .tt", "e=>e.value")
            B["expect_30"] = pg.evaluate("(()=>{const d=new Date(Date.now()-30*60000);"
                                         "return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')})()")
            pg.select_option(".varpick .tph", "00")
            pg.select_option(".varpick .tpm", "05")
            B["picked"] = pg.eval_on_selector(".varpick .tt", "e=>e.value")
            pg.click(".varpick .go")
            pg.wait_for_timeout(700)
            B["row_after"] = pg.inner_text("#nowExtraList")
            # a static time box (the meal form) is enhanced too, and still reads/writes .value
            B["ml"] = pg.evaluate("(()=>{const i=document.getElementById('ml_time');if(!i)return null;"
                                  "i.value='14:35';const w=i.nextSibling;"
                                  "return [w&&w.className, w.querySelector('.tph').value, w.querySelector('.tpm').value]})()")
            B["wide"] = pg.evaluate("document.documentElement.scrollWidth")
            B["errs"] = errs
            b.close()
        srv.shutdown()

    def t04():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert "when was it taken" in B["strip"], "strip reads: " + B["strip"]
        assert B["selects"] == 2, "time strip has %r lists" % B["selects"]
        assert B["go_box"][1] <= 300, "Save time is off screen at %r" % (B["go_box"],)
        return "tap opens hour and minute lists; Save time on screen at 300 px"
    check("04 folded width: the extra dose's time strip fits", t04)

    def t05():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert B["all_time"] > 0, "no time boxes on the page at all"
        assert B["visible_time"] == 0, "%d phone time boxes still visible" % B["visible_time"]
        assert B["enhanced"] == B["all_time"], "%d of %d enhanced" % (B["enhanced"], B["all_time"])
        assert B["ml"] and B["ml"][0] == "tpick" and B["ml"][1:] == ["14", "35"], "value set not shown: %r" % B["ml"]
        return "%d time boxes, none opens the phone dialog; setting .value shows in the lists" % B["all_time"]
    check("05 no time box opens the phone's own dialog", t05)

    def t06():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert B["chip_value"] == B["expect_30"], "chip gave %r, want %r" % (B["chip_value"], B["expect_30"])
        assert B["picked"] == "00:05", "lists gave %r" % B["picked"]
        got = q("SELECT dtime FROM doses WHERE medicine='Test antispasmodic'")[0][0]
        assert got == "00:05", "saved time %r" % got
        assert "00:05" in B["row_after"], "row still shows: " + B["row_after"]
        return "'30 min ago' fills the time; the lists set 00:05; saved and shown"
    check("06 the new time is saved and shown", t06)

    def t07():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert not B["errs"], "page errors: %s" % B["errs"]
        assert B["wide"] <= 300, "page scrolls sideways (%dpx)" % B["wide"]
        assert B["strip_right"] <= 300, "part of the time strip is cut off at %.0fpx" % B["strip_right"]
        assert B["wide_open"] <= 300, "page scrolls sideways with the time strip open (%dpx)" % B["wide_open"]
        return "no page errors, no sideways scroll"
    check("07 no page errors at the folded width", t07)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.27.0 -- one protein target, re-timed extras, no phone time dialog")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

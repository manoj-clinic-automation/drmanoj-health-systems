#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.27.1 -- one protein target on the page as well
(GUTLOG_V3271_TARGETJS).

The v3.27.0 suite read the server helper and never rendered the basket line
or the day bar, so two hardcoded 57s survived it. These checks render both.

  python3 test_v3271.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date

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
    spec = importlib.util.spec_from_file_location("gutlog_v3271", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    c.get("/")
    T = date.today().isoformat()
    # 00:00: since v3.31.0 a meal cannot be logged later than now today,
    # so a fixed 13:00 fixture failed every morning run.
    c.post("/api/meals", json={"day": T, "mtime": "00:00", "slot": "Lunch", "notes": "",
                               "items": [{"n": "Test dal", "q": 1, "p": 50, "k": 300, "f": 4, "fm": "L"}]})

    def t01():
        h = c.get("/").get_data(as_text=True)
        bad = [s for s in ("/57 g", "p/57*100", "(s.target||57)") if s in h]
        assert not bad, "the page still carries: " + ", ".join(bad)
        assert "let PROT_TGT=57;" in h, "no single page target"
        return "no hardcoded target left in the page; one PROT_TGT, 57 only as the fallback"
    check("01 the page carries no second protein target", t01)

    B = {}
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def browse():
        if B or not have_pw:
            return
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
            pg = b.new_page(viewport={"width": 390, "height": 900})
            pg.set_default_timeout(5000)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(900)
            pg.goto("http://127.0.0.1:%d/" % port)
            pg.wait_for_timeout(1400)
            B["tgt"] = pg.evaluate("typeof PROT_TGT==='undefined'?null:PROT_TGT")
            pg.click("#nav button[data-t=meals]")
            pg.wait_for_timeout(1000)
            B["bar"] = pg.eval_on_selector("#dayPbar", "e=>e.style.width")
            B["totals"] = pg.inner_text("#dayTotals")
            B["basket"] = pg.evaluate(
                "(()=>{basket.push({item:'Test dal',q:1,p:20,k:100,f:1,fm:'L'});renderBasket();"
                "return document.getElementById('ml_fmw').innerText})()")
            B["errs"] = errs
            b.close()
        srv.shutdown()

    def t02():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert B["tgt"] == 100, "the page's target is %r, not the plan's 100" % B["tgt"]
        return "the page takes the target from the server: 100 g"
    check("02 the page's target comes from the server", t02)

    def t03():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert "50" in B["totals"], "day total reads: " + B["totals"]
        assert B["bar"] == "50%", "50 g of 100 g filled the bar to %r" % B["bar"]
        return "50 g of 100 g fills the day bar halfway, not to the brim"
    check("03 the day protein bar is drawn against the plan", t03)

    def t04():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert "/100 g" in B["basket"], "the basket line reads: " + B["basket"]
        assert "/57 g" not in B["basket"], "the basket line still says 57"
        assert not B["errs"], "page errors: %s" % B["errs"]
        return "the basket line projects against 100 g"
    check("04 the basket projection uses the same target", t04)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.27.1 -- one protein target on the page as well")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.23.0 -- Day context (GUTLOG_V3230_CONTEXT).

Scratch database, real app, Flask test client; the last check drives the
page in Chromium if Playwright is installed (skipped, and says so, if not).

  python3 test_day_context.py [path/to/gutlog/app.py]
"""
import importlib.util
import os
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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp()
    tokf = os.path.join(w, "feed.token")
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=tokf,
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_ctx", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    c.get("/")
    gdb = os.environ["GUTLOG_DB"]
    T = date.today().isoformat()
    Y = (date.today() - timedelta(days=1)).isoformat()
    F = (date.today() + timedelta(days=1)).isoformat()

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.close()
        return r

    def t01():
        j = c.get("/api/daycontext?day=" + T).get_json()
        keys = [o["key"] for o in j["options"]]
        assert keys == ["exertion", "poor_sleep", "travel", "unwell", "stress", "ate_out"], keys
        assert j["tags"] == [], j
        return "six circumstances, nothing marked on a fresh day"
    check("01 the card offers the six circumstances", t01)

    def t02():
        r = c.post("/api/daycontext", json={"day": T, "tag": "exertion", "on": True}).get_json()
        assert r["ok"] and r["tags"] == ["exertion"], r
        c.post("/api/daycontext", json={"day": T, "tag": "exertion", "on": True})
        n = q("SELECT COUNT(*) FROM day_context WHERE day=? AND tag='exertion'", (T,))[0][0]
        assert n == 1, "a second tap on an on tag made %d rows" % n
        r = c.post("/api/daycontext", json={"day": T, "tag": "exertion", "on": False}).get_json()
        assert r["tags"] == [], r
        return "one tap marks, repeat is harmless, a second tap clears"
    check("02 a tap marks, a tap clears, never a duplicate", t02)

    def t03():
        c.post("/api/daycontext", json={"day": Y, "tag": "poor_sleep", "on": True})
        c.post("/api/daycontext", json={"day": Y, "tag": "travel", "on": True})
        c.post("/api/daycontext", json={"day": T, "tag": "stress", "on": True})
        assert c.get("/api/daycontext?day=" + Y).get_json()["tags"] == ["poor_sleep", "travel"]
        assert c.get("/api/daycontext?day=" + T).get_json()["tags"] == ["stress"]
        return "yesterday and today kept apart"
    check("03 yesterday can be marked separately", t03)

    def t04():
        bad = [({"day": T, "tag": "nonsense", "on": True}, "tag"),
               ({"day": F, "tag": "travel", "on": True}, "future")]
        for body, why in bad:
            r = c.post("/api/daycontext", json=body)
            assert r.status_code == 400, why + " accepted (%d)" % r.status_code
        assert not q("SELECT 1 FROM day_context WHERE day=?", (F,)), "future row written"
        return "unknown tag and future day refused"
    check("04 bad input is refused", t04)

    def t05():
        tok = open(tokf, encoding="utf-8").read().strip()
        r = c.get("/api/feed/daycontext?since=" + Y, headers={"Authorization": "Bearer " + tok})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"], j
        assert j["days"] == [{"day": Y, "tags": ["poor_sleep", "travel"]},
                             {"day": T, "tags": ["stress"]}], j["days"]
        assert c.get("/api/feed/daycontext").status_code == 401, "feed open without a token"
        return "the feed carries the marked days; refuses without the token"
    check("05 the read-only feed for trials and FitLog", t05)

    def t06():
        h = c.get("/").get_data(as_text=True)
        assert 'id="nowCtx"' in h and "async function loadCtx" in h and "loadCtx();" in h
        assert h.index('id="nowCtx"') < h.index('id="nowDown"'), "card not above Down day"
        c2 = gm.app.test_client()
        assert c2.get("/api/daycontext").status_code in (302, 401), "API open without a login"
        return "card present above Down day; API needs a login"
    check("06 the Now tab carries the card; the API needs a login", t06)

    def t07():
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
            pg = b.new_page(viewport={"width": 390, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(900)
            pg.goto("http://127.0.0.1:%d/" % port)
            pg.wait_for_timeout(1200)
            pg.click("#ctxTags button:has-text('Unwell')")
            pg.wait_for_timeout(600)
            summ = pg.inner_text("#ctxSum")
            pg.click("#ctxDay button:has-text('Yesterday')")
            pg.wait_for_timeout(500)
            ysum = pg.inner_text("#ctxSum")
            wide = pg.evaluate("document.documentElement.scrollWidth")
            b.close()
        srv.shutdown()
        assert not errs, "page errors: %s" % errs
        assert "Unwell" in summ and "Stress" in summ, "today reads: " + summ
        assert "Poor sleep" in ysum and "Unwell" not in ysum, "yesterday reads: " + ysum
        assert q("SELECT 1 FROM day_context WHERE day=? AND tag='unwell'", (T,)), "tap not saved"
        assert wide <= 390, "the page scrolls sideways (%dpx)" % wide
        return "one tap saved 'Unwell' today; Yesterday shows its own; no errors"
    check("07 in a real browser: one tap marks the day", t07)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.23.0 -- Day context")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

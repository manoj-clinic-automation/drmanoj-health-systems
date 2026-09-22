#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.30.0 -- the Now tab says when the Drive mirror has gone stale
(GUTLOG_V3300_MIRRORSTALE).

The failure this guards against is silence: Claude on his phone answering
confidently from a week-old copy. So the checks are about what happens when
the mirror STOPS, not when it works.

  python3 test_v3300_mirror.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

RESULTS = []
B = {}


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
    w = tempfile.mkdtemp(prefix="gutlog_mirror_")
    stamp = os.path.join(w, "last_success.json")
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_MIRROR_STAMP=stamp)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3300", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)

    def set_stamp(hours_ago):
        when = datetime.now() - timedelta(hours=hours_ago)
        json.dump({"at": when.isoformat(), "epoch": int(when.timestamp())},
                  open(stamp, "w"))

    def state():
        return json.loads(c.get("/api/mirror").get_data(as_text=True))

    # ---------------------------------------------------------------- 01
    def t01():
        if os.path.exists(stamp):
            os.remove(stamp)
        j = state()
        assert j["ok"] is False, "no mirror has ever run and it reports ok"
        assert j["never"] is True, j
        assert "never" in j["text"].lower(), j["text"]
        return "a mirror that has never finished is not reported as fine"
    check("01 a mirror that never ran is not 'ok'", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        set_stamp(2)
        j = state()
        assert j["ok"] is True, "a two-hour-old mirror is reported stale: %r" % j
        assert j["hours"] < 3, j
        return "two hours old reads ok"
    check("02 a fresh mirror is ok", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        set_stamp(35)
        assert state()["ok"] is True, "35 hours is inside the 36-hour window"
        set_stamp(37)
        j = state()
        assert j["ok"] is False, "37 hours old and still reported ok"
        assert "37" in j["text"] or "36" in j["text"] or "hours ago" in j["text"], j["text"]
        return "ok at 35 hours, stale at 37 -- the line is at %d" % gm.MIRROR_STALE_HOURS
    check("03 the line is at 36 hours, not somewhere near it", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        set_stamp(80)
        j = state()
        low = j["text"].lower()
        assert "that old" in low or "hours ago" in low, \
            "the warning does not say the answers are that old: %r" % j["text"]
        anon = gm.app.test_client()
        assert anon.get("/api/mirror").status_code in (301, 302), \
            "the mirror state is readable logged out"
        return "the warning says why it matters, and the route is gated"
    check("04 the warning explains the consequence, and needs a login", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        # Assert the PROPERTY, not a list of production strings. The first
        # version probed for "/root" and "health3", which never appear in a
        # temp path, so a mutation that handed the page the stamp path sailed
        # through it. The property is: four known keys, and no value that is
        # a filesystem path.
        set_stamp(2)
        j = state()
        assert set(j.keys()) == {"ok", "hours", "never", "text"}, \
            "the payload grew keys: %s" % sorted(j.keys())
        for k, v in j.items():
            s = str(v)
            assert stamp not in s, "%s carries the stamp path" % k
            assert "/" not in s and "\\" not in s, \
                "%s looks like a path: %r" % (k, s)
        for probe in ("token", "secret", "rclone", "drive"):
            assert probe not in str(j).lower(), "the state names %r" % probe
        return "four keys, a time and a sentence -- no path, no token, no record"
    check("05 the mirror state carries no path, token or record", t05)

    # --------------------------------------------------- the browser check
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def browse():
        if B or not have_pw:
            return
        from werkzeug.serving import make_server
        set_stamp(80)
        srv = make_server("127.0.0.1", 0, gm.app)
        port = srv.server_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 300, "height": 680})
            pg.set_default_timeout(6000)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1200)
            B["stale_text"] = pg.inner_text("#nowMirror")
            B["stale_wide"] = pg.evaluate("document.documentElement.scrollWidth")
            set_stamp(1)
            pg.reload()
            pg.wait_for_timeout(1200)
            B["fresh_text"] = pg.inner_text("#nowMirror")
            B["errs"] = errs
            b.close()
        srv.shutdown()

    def t06():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert not B["errs"], "page errors: %s" % B["errs"]
        assert "mirror" in B["stale_text"].lower(), \
            "a stale mirror shows nothing on the Now tab: %r" % B["stale_text"]
        assert "hours ago" in B["stale_text"].lower(), B["stale_text"]
        return "the Now tab carries the line when the mirror is stale"
    check("06 the warning really appears on the Now tab", t06)

    def t07():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert B["fresh_text"].strip() == "", \
            "a working mirror still shows a warning: %r" % B["fresh_text"]
        assert B["stale_wide"] <= 300, \
            "the warning scrolls the page sideways (%dpx)" % B["stale_wide"]
        return "nothing is shown when the mirror is current, and it fits 300 px"
    check("07 nothing is shown when the mirror is working", t07)

    print("")
    for ok, name, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))
    bad = [r for r in RESULTS if not r[0]]
    print("-" * 72)
    print("%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
    print("RESULT: " + ("FAILURES" if bad else "ALL PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

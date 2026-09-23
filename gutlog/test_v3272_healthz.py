#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.27.2 -- a health endpoint that exists (GUTLOG_V3272_HEALTHZ).

Every GutLog runbook step that said "read /healthz" was reading a route
that had never been written, and a 404 there is indistinguishable from a
broken deploy. These checks are about the route being reachable WITHOUT a
session, saying which build is running, and -- because it is the one route
with no auth in front of it -- carrying nothing else at all.

  python3 test_v3272_healthz.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
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
    json.dump({"targets": {"protein": 100, "protein_meal": 25},
               "main_meals": ["Breakfast", "Lunch"]}, open(plan, "w"))
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=plan)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3272", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)

    # Deliberately BEFORE /setup: a health endpoint has to answer on a box
    # where the app has never been configured. That is when it is needed.
    fresh = gm.app.test_client()

    def t01():
        r = fresh.get("/healthz")
        assert r.status_code == 200, "status %s on an unconfigured app" % r.status_code
        ct = (r.headers.get("Content-Type") or "")
        assert ct.startswith("text/plain"), "content-type is %r, not text/plain" % ct
        return "200 text/plain before /setup has ever been run"
    check("01 /healthz answers in plain text with no session", t01)

    def t02():
        body = fresh.get("/healthz").get_data(as_text=True).strip()
        assert re.match(r"^ok \d+\.\d+\.\d+$", body), "body is %r, want 'ok <x.y.z>'" % body
        assert body == "ok " + gm.APP_VERSION, \
            "body %r does not match APP_VERSION %r" % (body, gm.APP_VERSION)
        return "body is %r and APP_VERSION is the only source of it" % body
    check("02 it names the running build, from one constant", t02)

    # Now configure the app and log in, so the no-auth claim is tested
    # against a build that really does gate everything else.
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    T = date.today().isoformat()
    # 00:00: since v3.31.0 a meal cannot be logged later than now today,
    # so a fixed 13:00 fixture failed every morning run.
    c.post("/api/meals", json={"day": T, "mtime": "00:00", "slot": "Lunch", "notes": "",
                               "items": [{"n": "Test dal", "q": 1, "p": 50, "k": 300,
                                          "f": 4, "fm": "L"}]})

    def t03():
        anon = gm.app.test_client()
        assert anon.get("/").status_code == 302, "/ is not gated, so this proves nothing"
        r = anon.get("/healthz")
        assert r.status_code == 200, "/healthz redirected or refused: %s" % r.status_code
        assert "login" not in (r.headers.get("Location") or ""), "bounced to login"
        return "/ redirects to login, /healthz still answers 200"
    check("03 it needs no login, on an app that gates everything else", t03)

    def t04():
        body = c.get("/healthz").get_data(as_text=True)
        assert len(body) < 40, "body is %d bytes; a health line is short by design" % len(body)
        # Nothing from the record, and nothing that names the box either.
        leaks = [w for w in ("dal", "Lunch", "protein", "kcal", "100", "50",
                             "/root", "health3", "secret", "token", "sqlite")
                 if w.lower() in body.lower()]
        assert not leaks, "the unauthenticated body carries: " + ", ".join(leaks)
        con = sqlite3.connect(os.environ["GUTLOG_DB"])
        n = con.execute("SELECT COUNT(*) FROM meals").fetchone()[0]
        con.close()
        assert n >= 1, "the fixture meal did not store, so the leak check is vacuous"
        return "%r only, with %d meal row(s) present to leak and none leaked" % (body.strip(), n)
    check("04 it carries no record data and no host detail", t04)

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

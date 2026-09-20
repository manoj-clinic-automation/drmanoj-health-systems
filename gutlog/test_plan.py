#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.25.0 -- the diet plan in the app (GUTLOG_V3250_PLAN).

Scratch database, real app, a scratch plan file whose foods are invented.
Meals are written straight into a fixed PAST week (Monday..Thursday of the
week before last), so every figure is known in advance and the suite reads
the same on any day. Last check drives Chromium if Playwright is installed.

  python3 test_plan.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
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


PLAN = {
    "targets": {"kcal": [1800, 1900], "protein": 100, "protein_meal": 25, "fibre": [30, 35],
                "calcium": [1000, 1200], "plants_week": 25, "sweets_day": 1},
    "main_meals": ["Breakfast", "Lunch", "Dinner"],
    "quarter": ["testspice"],
    "easy_adds": ["testfruit", "testgrain"],
    "rules": [
        {"id": "dal_repeat", "type": "no_repeat_days", "tag": "dal:", "label": "Same dal two days running"},
        {"id": "sabzi_twice", "type": "max_each", "tag": "sabzi:", "max": 2, "label": "Same sabzi at most 2 a week"},
        {"id": "fish", "type": "min_days", "tag": "protein:fish", "min": 2, "label": "Fish", "unit": "times"},
        {"id": "parantha", "type": "max_days", "tag": "grain:parantha", "max": 2, "label": "Parantha"},
        {"id": "sweet_day", "type": "max_per_day", "tag": "sweet", "max": 1, "label": "Sweet once a day"}],
    "foods": {
        "Test dal A": {"plants": ["testlentil"], "ca": 30, "tags": ["dal:a"]},
        "Test dal B": {"plants": ["testlentil2"], "ca": 25, "tags": ["dal:b"]},
        "Test gourd": {"plants": ["testgourd", "testspice"], "ca": 20, "tags": ["sabzi:gourd"]},
        "Test flatbread": {"plants": ["testwheat"], "ca": 12, "tags": ["grain:parantha"]},
        "Test fish dish": {"ca": 60, "tags": ["protein:fish"]},
        "Test laddu": {"plants": ["testseed"], "ca": 50, "tags": ["sweet:hi"]},
        "Test pudding": {"plants": [], "ca": 150, "tags": ["sweet"]},
        "Test mystery": {"plants": ["testroot"], "tags": []}}}

RECIPE_PLANTS = ["testherb", "testleaf"]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp()
    pf = os.path.join(w, "plan.json")
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"), GUTLOG_PLAN_FILE=pf)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_plan", app_path)
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

    t = date.today()
    MON = t - timedelta(days=t.weekday() + 14)
    D = [(MON + timedelta(days=i)).isoformat() for i in range(4)]   # Mon..Thu
    PREV_SUN = (MON - timedelta(days=1)).isoformat()

    def meal(day, slot, items, p, k, f):
        q("INSERT INTO meals(day,mtime,slot,items,protein,kcal,fibre,fscore,notes,created) "
          "VALUES(?,?,?,?,?,?,?,0,'',?)",
          (day, "12:00", slot, json.dumps([{"n": n, "q": qq, "p": 0, "k": 0, "f": 0, "fm": "L"}
                                           for n, qq in items]), p, k, f, day))

    q("INSERT INTO recipes(slug,name,grp,stage,data,updated) VALUES('t','Test recipe stew','A','new',?, 'x')",
      (json.dumps({"plants": RECIPE_PLANTS}),))
    meal(PREV_SUN, "Lunch", [("Test gourd", 1)], 5, 100, 2)             # last week: not counted
    meal(D[0], "Lunch", [("Test dal A", 1), ("Test gourd", 1), ("Test flatbread", 1)], 20, 400, 8)
    meal(D[1], "Lunch", [("Test dal B", 1), ("Test gourd", 1)], 18, 350, 7)
    meal(D[1], "Dinner", [("Test flatbread", 1), ("Test laddu", 1)], 10, 300, 3)
    meal(D[2], "Lunch", [("Test dal B", 1), ("Test gourd", 1), ("Test recipe stew", 1)], 22, 420, 9)
    meal(D[2], "Breakfast", [("Test mystery", 2)], 12, 200, 2)
    meal(D[2], "Dinner", [("Test laddu", 1)], 3, 110, 1)
    meal(D[2], "Snack", [("Test pudding", 0.5)], 2, 80, 0)

    def plan(day):
        return c.get("/api/plan?day=" + day).get_json()

    def rule(j, rid):
        return [r for r in j["rules"] if r["id"] == rid][0]

    def t01():
        j = plan(D[2])
        assert j.get("on"), j
        os.rename(pf, pf + ".off") if os.path.exists(pf) else None
        try:
            assert plan(D[2]) == {"on": False}, "no plan file, yet on"
        finally:
            os.rename(pf + ".off", pf)
        return "with no plan file the card is off"
    json.dump(PLAN, open(pf, "w", encoding="utf-8"))
    check("01 no plan file, no card", t01)

    def t02():
        tt = plan(D[2])["today"]
        assert (tt["protein"], tt["kcal"], tt["fibre"]) == (39, 810, 12), tt
        assert tt["calcium"] == 25 + 20 + 50 + 75, "calcium %s" % tt["calcium"]
        assert set(tt["calcium_missing"]) == {"Test mystery", "Test recipe stew"}, tt["calcium_missing"]
        mains = dict((m["slot"], m["protein"]) for m in tt["mains"])
        assert mains == {"Breakfast": 12, "Lunch": 22, "Dinner": 3}, mains
        assert tt["sweets"] == 2, tt["sweets"]
        return "totals, calcium by quantity (half a pudding = 75), unknowns named, protein per main meal"
    check("02 today's totals against the targets", t02)

    def t03():
        wk = plan(D[2])["week"]
        want = {"testlentil", "testlentil2", "testgourd", "testspice", "testwheat", "testseed",
                "testroot", "testherb", "testleaf"}
        assert set(wk["plants"]) == want, wk["plants"]
        assert wk["points"] == 8.25, "points %s (spice counts a quarter)" % wk["points"]
        assert wk["easy"] == ["testfruit", "testgrain"], wk["easy"]
        return "9 plants incl. recipe plants, spice as a quarter = 8.25; last week not counted"
    check("03 this week's plant count", t03)

    def t04():
        j = plan(D[2])
        r = rule(j, "dal_repeat")
        assert r["status"] == "over" and "b" in r["detail"], r
        j1 = plan(D[1])
        assert rule(j1, "dal_repeat")["status"] == "ok", "different dals flagged"
        j3 = plan(D[3])
        assert any("not b again today" in x for x in j3["tips"]), j3["tips"]
        return "B two days running flagged; A then B fine; next day told 'not b again'"
    check("04 the same dal two days running", t04)

    def t05():
        r = rule(plan(D[2]), "sabzi_twice")
        assert r["status"] == "over" and "gourd 3 times" in r["detail"], r
        r1 = rule(plan(D[1]), "sabzi_twice")
        assert r1["status"] == "full", r1
        return "gourd: 2 = at limit on Tuesday, 3 = over on Wednesday (Sunday before not counted)"
    check("05 the same sabzi more than twice a week", t05)

    def t06():
        j = plan(D[2])
        r = rule(j, "fish")
        assert r["status"] == "due" and r["detail"].startswith("0 of 2 this week"), r
        assert not any(x.startswith("Fish") for x in j["tips"]), "fish nagged with 5 days left"
        sat = (MON + timedelta(days=5)).isoformat()
        js = plan(sat)
        assert "Fish: 0 of 2 this week \u2014 have it today" in js["tips"], js["tips"]
        sun = (MON + timedelta(days=6)).isoformat()
        ju = plan(sun)
        assert rule(ju, "fish")["status"] == "short", rule(ju, "fish")
        assert any(x.startswith("Fish: 0 of 2 this week \u2014 short") for x in ju["tips"]), ju["tips"]
        return "due midweek, quiet; Saturday 'have it today'; Sunday 'short', never 'on track'"
    check("06 fish falling behind", t06)

    def t07():
        j = plan(D[1])
        r = rule(j, "parantha")
        assert r["status"] == "full" and any(x.startswith("Parantha: 2 this week") for x in j["tips"]), (r, j["tips"])
        s = rule(plan(D[2]), "sweet_day")
        assert s["status"] == "over", s
        s1 = rule(plan(D[1]), "sweet_day")
        assert s1["status"] == "full" and "Sweet: done for today" in plan(D[1])["tips"], s1
        return "parantha at 2 = none more; one sweet = done, two = over"
    check("07 limits: parantha and sweets", t07)

    def t08():
        h = c.get("/").get_data(as_text=True)
        assert 'id="nowPlan"' in h and "async function loadPlan" in h and "loadPlan();" in h
        assert h.index('id="nowMeal"') < h.index('id="nowPlan"')
        assert gm.app.test_client().get("/api/plan").status_code in (302, 401)
        return "card under the meal card; API needs a login"
    check("08 the card sits under the meal card", t08)

    def t09():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "SKIPPED: Playwright not installed here"
        from werkzeug.serving import make_server
        import socket
        meal(t.isoformat(), "Lunch", [("Test dal A", 1), ("Test pudding", 1)], 30, 500, 10)
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
            pg.wait_for_timeout(1500)
            vis = pg.is_visible("#nowPlan")
            bars = pg.inner_text("#planBars")
            pg.click("#planMore")
            wk = pg.inner_text("#planWeek")
            wide = pg.evaluate("document.documentElement.scrollWidth")
            b.close()
        srv.shutdown()
        assert not errs, "page errors: %s" % errs
        assert vis and "Protein" in bars and "30 / 100" in bars and "Calcium" in bars, bars
        assert "Sweet once a day" in wk, wk
        assert wide <= 390, "sideways scroll %dpx" % wide
        return "card shows today's bars and the week rules; no errors"
    check("09 in a real browser: the card and the week", t09)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.25.0 -- the diet plan in the app")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.22.0 -- meal cards on the Now tab (GUTLOG_V3220_MEALS).

Scratch database, real app, Flask test client, and a scratch meal-card file
(GUTLOG_MEALS_FILE) whose foods are all invented, so this file describes no
one's diet. Every entry is made YESTERDAY at fixed times, except "Again",
which is today by design. Optional last check drives the page in Chromium
(Playwright) if it is installed; it is skipped, and says so, if not.

  python3 test_meal_cards.py [path/to/gutlog/app.py]
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


CARDS = {"cards": [
    {"name": "Breakfast", "from": "00:00", "rows": [
        {"kind": "count", "label": "Eggs", "unit": "egg", "q": 2},
        {"kind": "pick", "label": "Style", "sel": 1, "options": [
            {"t": "Boiled", "items": [["Test egg boiled", "@Eggs"]]},
            {"t": "Fried", "items": [["Test egg fried", "@Eggs"]]}]},
        {"kind": "count", "label": "Bread", "unit": "slice", "q": 1,
         "items": [["Test bread slice", 1]]},
        {"kind": "opt", "label": "Juice", "text": "Juice", "on": False,
         "items": [["Test juice", 1]]}]},
    {"name": "Dinner", "from": "18:00", "onion": True, "rows": [
        {"kind": "pick", "label": "Sabzi", "sel": 0, "options": [
            {"t": "Gourd", "items": [["Test gourd", 1]]},
            {"t": "Ghost", "items": [["Test not in library", 1]]}]},
        {"kind": "pick", "label": "Sweet", "sel": 0, "options": [
            {"t": "Ball", "items": [["Test sweet ball", 1]]}, {"t": "None", "items": []}]}]}]}

FOODS = [("Test egg boiled", 6, 70, 0, "L"), ("Test egg fried", 6, 90, 0, "L"),
         ("Test bread slice", 2.5, 65, 1.5, "M"), ("Test juice", 0.5, 60, 0, "L-M"),
         ("Test gourd", 0.6, 55, 1.5, "L"), ("Test sweet ball", 2.5, 110, 2, "L"),
         ("Onion (tarka base)", 0, 0, 0, "H")]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp()
    mf = os.path.join(w, "meals.test.json")
    with open(mf, "w", encoding="utf-8") as fh:
        json.dump(CARDS, fh)
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"), GUTLOG_MEALS_FILE=mf)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_meals", app_path)
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

    for n, p, k, f, fm in FOODS:
        q("INSERT OR IGNORE INTO library(cat,item,portion,protein,kcal,fibre,fodmap) "
          "VALUES('H',?,'1',?,?,?,?)", (n, p, k, f, fm))
    Y = (date.today() - timedelta(days=1)).isoformat()
    ctx = {}

    def items(mid):
        return dict((i["n"], i["q"]) for i in json.loads(
            q("SELECT items FROM meals WHERE id=?", (mid,))[0][0]))

    def t01():
        j = c.get("/api/mealcards?day=" + Y).get_json()
        assert [x["name"] for x in j["cards"]] == ["Breakfast", "Dinner"], j["cards"]
        assert "Test not in library" in j["missing"], "missing food not reported: %s" % j["missing"]
        assert j["lib"]["Test egg fried"]["k"] == 90, "nutrition map absent"
        return "two cards, the food the library lacks is named"
    check("01 the cards come from the meal file, with what the library lacks", t01)

    def t02():
        r = c.post("/api/mealcards/log", json={"card": "Breakfast", "choices": {},
                                               "day": Y, "mtime": "08:10"}).get_json()
        assert r.get("ok"), r
        it = items(r["id"])
        assert it == {"Test egg fried": 2, "Test bread slice": 1}, it
        slot = q("SELECT slot, mtime FROM meals WHERE id=?", (r["id"],))[0]
        assert slot == ("Breakfast", "08:10"), slot
        ctx["b1"] = r["id"]
        return "defaults: 2 fried eggs + 1 slice, juice off; slot Breakfast at 08:10"
    check("02 one tap logs the card as it stands", t02)

    def t03():
        r = c.post("/api/mealcards/log", json={"card": "Breakfast", "day": Y, "mtime": "08:20",
                                               "choices": {"0": 3, "1": 0, "2": 0.5, "3": True}}).get_json()
        it = items(r["id"])
        assert it == {"Test egg boiled": 3, "Test bread slice": 0.5, "Test juice": 1}, it
        j = c.get("/api/mealcards?day=" + Y).get_json()
        last = j["cards"][0]["last"]
        assert last == {"0": 3, "1": 0, "2": 0.5, "3": True}, "last choices: %s" % last
        ctx["b2"] = r["id"]
        return "3 eggs carry into the chosen style; half a slice; the card now opens set to this"
    check("03 choices, counts in halves, and 'last time' remembered", t03)

    def t04():
        r = c.post("/api/mealcards/log", json={"card": "Dinner", "day": Y, "mtime": "20:00",
                                               "choices": {"0": 0, "1": -1}, "onion": True}).get_json()
        it = items(r["id"])
        assert it == {"Test gourd": 1, "Onion (tarka base)": 1}, it
        fm = [i["fm"] for i in json.loads(q("SELECT items FROM meals WHERE id=?", (r["id"],))[0][0])
              if i["n"] == "Onion (tarka base)"]
        assert fm == ["H"], fm
        ctx["d1"] = r["id"]
        return "onion switch adds the onion item (H); a deselected sweet adds nothing"
    check("04 the onion switch and a deselected choice", t04)

    def t05():
        r = c.post("/api/mealcards/log", json={"card": "Dinner", "day": Y, "mtime": "20:30",
                                               "choices": {"0": 1, "1": 0}}).get_json()
        assert r.get("ok") and r.get("missing") == ["Test not in library"], r
        assert items(r["id"]) == {"Test sweet ball": 1}, items(r["id"])
        return "a food the library lacks is left out and named, the rest is logged"
    check("05 a missing food never breaks a log", t05)

    def t06():
        r = c.post("/api/meals/%d/replace" % ctx["b1"],
                   json={"card": "Breakfast", "choices": {"0": 1, "1": 0}}).get_json()
        assert r.get("ok"), r
        row = q("SELECT mtime, day FROM meals WHERE id=?", (ctx["b1"],))[0]
        assert row == ("08:10", Y), "edit moved the meal: %s" % (row,)
        assert items(ctx["b1"]) == {"Test egg boiled": 1, "Test bread slice": 1}, items(ctx["b1"])
        meta = json.loads(q("SELECT choices FROM meal_meta WHERE meal_id=?", (ctx["b1"],))[0][0])
        assert meta == {"0": 1, "1": 0}, meta
        return "edit changes what was eaten, keeps 08:10 yesterday"
    check("06 Edit keeps the original time and rewrites the meal", t06)

    def t07():
        r = c.post("/api/meals/%d/again" % ctx["d1"], json={}).get_json()
        row = q("SELECT day, slot FROM meals WHERE id=?", (r["id"],))[0]
        assert row == (date.today().isoformat(), "Dinner"), row
        assert items(r["id"]) == items(ctx["d1"]), "again changed the food"
        assert q("SELECT card FROM meal_meta WHERE meal_id=?", (r["id"],))[0][0] == "Dinner"
        ctx["again"] = r["id"]
        return "same food, today, now, still a Dinner card meal"
    check("07 Again logs the same meal now", t07)

    def t08():
        c.post("/api/meals/%d/delete" % ctx["again"], json={})
        assert not q("SELECT 1 FROM meals WHERE id=?", (ctx["again"],)), "meal still there"
        assert not q("SELECT 1 FROM meal_meta WHERE meal_id=?", (ctx["again"],)), "meta left behind"
        return "meal and its card record both gone"
    check("08 Delete removes the meal", t08)

    def t09():
        r = c.post("/api/foods/new", json={"name": "Test pizza slice"}).get_json()
        assert r.get("ok") and not r["existed"] and r["kind"] == "bread", r
        row = q("SELECT tags, note FROM library WHERE item='Test pizza slice'")[0]
        assert "estimated" in row[0] and "Estimated" in row[1], row
        r2 = c.post("/api/foods/new", json={"name": "test PIZZA slice"}).get_json()
        assert r2["existed"] and r2["n"] == "Test pizza slice", r2
        o = c.post("/api/mealcards/log", json={"card": "", "slot": "Snack", "day": Y, "mtime": "17:00",
                                               "extra": [{"n": "Test pizza slice", "q": 1.5}]}).get_json()
        assert o.get("ok") and items(o["id"]) == {"Test pizza slice": 1.5}, o
        assert q("SELECT slot FROM meals WHERE id=?", (o["id"],))[0][0] == "Snack"
        return "name only -> estimated bread-kind dish; same name again is reused; logged as a Snack"
    check("09 a dish never seen before, by name only", t09)

    def t10():
        j = c.get("/api/foods/search").get_json()
        names = [f["n"] for f in j["foods"]]
        assert names[0] == "Test sweet ball", "latest eaten (20:30) is not first: %s" % names[:3]
        flags = [f["recent"] for f in j["foods"]]
        assert flags == sorted(flags, reverse=True), "a food never eaten sits above a recent one"
        assert "Test pizza slice" in [f["n"] for f in j["foods"] if f["recent"]]
        j = c.get("/api/foods/search?q=gourd").get_json()
        assert [f["n"] for f in j["foods"]] == ["Test gourd"], j
        return "recent first; a word narrows it"
    check("10 search puts what was eaten recently first", t10)

    def t11():
        h = c.get("/").get_data(as_text=True)
        assert 'id="nowMeal"' in h and "async function loadMeals" in h and "loadMeals();" in h
        return "card present and loaded with the Now tab"
    check("11 the Now tab carries the meal card", t11)

    def t12():
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
        before = q("SELECT COUNT(*) FROM meals WHERE day=?", (date.today().isoformat(),))[0][0]
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
            pg.click("#mealTabs >> text=Breakfast")
            label = pg.inner_text("#mealBody .btn.primary")
            pg.click("#mealBody .btn.primary")
            pg.wait_for_timeout(900)
            listed = pg.inner_text("#mealToday")
            pg.click("#mealTabs >> text=Other meal")
            pg.fill("#mcQ", "Test chole bhature")
            pg.wait_for_timeout(700)
            pg.click("text=Add “Test chole bhature”")
            pg.wait_for_timeout(500)
            pg.click("#mealBody .btn.primary")
            pg.wait_for_timeout(900)
            wide = pg.evaluate("document.documentElement.scrollWidth")
            b.close()
        srv.shutdown()
        after = q("SELECT COUNT(*) FROM meals WHERE day=?", (date.today().isoformat(),))[0][0]
        assert not errs, "page errors: %s" % errs
        assert "same as last time" in label, "button reads: " + label
        assert after - before == 2, "%d meals logged from the page, expected 2" % (after - before)
        assert "Breakfast" in listed and "Delete" in listed, listed
        assert wide <= 390, "the page scrolls sideways (%dpx)" % wide
        return "one tap logged breakfast 'same as last time'; a new dish added and logged; no errors"
    check("12 in a real browser: one tap, and a new dish", t12)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.22.0 -- meal cards on the Now tab")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

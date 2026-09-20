#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.24.0 -- Recipes (GUTLOG_V3240_RECIPES).

Scratch database, real app, invented recipe cards (no real dish), loaded by
the real seed_recipes.py. Last check drives Chromium if Playwright exists.

  python3 test_recipes.py [path/to/gutlog/app.py]
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
from datetime import date

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def card(i, name, grp, onion=False, of=None):
    return {"id": i, "name": name, "group": grp, "source": "test", "serves": 4,
            "serving": "1 katori", "ing": [["testgrain", 100, "1 cup"], ["onion", 50, "1 small"] if onion
                                          else ["testleaf", 20, "handful"]],
            "method": ["Step one.", "Step two."], "notes": ["A note."],
            "per_serving": {"kcal": 200, "protein": 8.5, "fat": 5, "fibre": 3},
            "plant_points": 2, "plants": ["testgrain"], "yield_est": False,
            "flags": {"onion": onion, "garlic": False, "high_fodmap": ["onion"] if onion else [],
                      "sweetener": False}, "onion_free": of or []}


CARDS = [card("test-khichri", "Test khichri", "A"),
         card("test-masala", "Test masala bowl", "B", onion=True, of=["Onion out: add hing."]),
         card("test-cake", "Test festive cake", "C")]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    seed = os.path.join(os.path.dirname(app_path), "seed_recipes.py")
    w = tempfile.mkdtemp()
    cf = os.path.join(w, "cards.json")
    json.dump(CARDS, open(cf, "w", encoding="utf-8"))
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_rec", app_path)
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

    def run_seed(apply):
        return subprocess.run([sys.executable, seed, "--db", gdb, "--cards", cf] +
                              (["--apply"] if apply else []), capture_output=True, text=True).stdout

    def t01():
        out = run_seed(False)
        assert "3 new" in out and not q("SELECT 1 FROM recipes"), "dry run wrote: " + out
        out = run_seed(True)
        assert "added 3" in out and "library +3" in out, out
        return "dry run writes nothing; apply loads 3 cards and their library items"
    check("01 the seed loads cards, dry run first", t01)

    def t02():
        j = c.get("/api/recipes").get_json()
        names = [r["name"] for r in j["recipes"]]
        assert names == ["Test khichri", "Test masala bowl", "Test festive cake"], names
        m = [r for r in j["recipes"] if r["slug"] == "test-masala"][0]
        assert m["onion"] and m["has_onion_free"] and m["stage"] == "new", m
        assert [s["key"] for s in j["stages"]] == ["new", "trial", "rotation", "paused", "avoid"]
        return "listed by group, flags and stage carried"
    check("02 the list carries group, flags and stage", t02)

    def t03():
        j = c.get("/api/recipes/test-masala").get_json()
        r = j["recipe"]
        assert r["ing"][1][0] == "onion" and r["method"] == ["Step one.", "Step two."], r
        assert r["onion_free"] == ["Onion out: add hing."], r["onion_free"]
        assert c.get("/api/recipes/nope").status_code == 404
        return "full card: ingredients, method, onion-free version"
    check("03 a card opens in full", t03)

    def t04():
        r = c.post("/api/recipes/test-khichri/stage", json={"stage": "rotation"}).get_json()
        assert r["ok"], r
        assert c.post("/api/recipes/test-khichri/stage", json={"stage": "eaten"}).status_code == 400
        CARDS[0]["serving"] = "1 plate"
        json.dump(CARDS, open(cf, "w", encoding="utf-8"))
        out = run_seed(True)
        assert "refreshed 1" in out, out
        row = q("SELECT stage, data FROM recipes WHERE slug='test-khichri'")[0]
        assert row[0] == "rotation", "re-seeding reset his stage to " + row[0]
        assert json.loads(row[1])["serving"] == "1 plate"
        return "stage set; re-seeding refreshes the card and keeps his stage"
    check("04 his stage survives a re-seed", t04)

    def t05():
        r = c.post("/api/recipes/test-cake/log", json={"q": 0.5, "slot": "Snack",
                                                       "day": date.today().isoformat(),
                                                       "mtime": "00:00"}).get_json()
        assert r.get("ok"), r
        row = q("SELECT slot, items, protein FROM meals WHERE id=?", (r["id"],))[0]
        it = json.loads(row[1])
        assert row[0] == "Snack" and it[0]["n"] == "Test festive cake" and it[0]["q"] == 0.5, row
        assert abs(row[2] - 4.2) < 0.11, "protein %s for half a serving of 8.5" % row[2]
        j = c.get("/api/recipes/test-cake").get_json()
        assert j["recipe"]["logged"], "the card does not show it was had"
        return "half a serving logged as a Snack, 4.2 g protein; card shows it was had"
    check("05 a serving logs as a meal", t05)

    def t06():
        h = c.get("/").get_data(as_text=True)
        assert 'data-s="recipes"' in h and 'id="meals-recipes"' in h and "loadRecipes();" in h
        assert c.__class__(gm.app).get("/api/recipes").status_code in (302, 401)
        return "Recipes segment present; API needs a login"
    check("06 the Meals tab has a Recipes segment; the API needs a login", t06)

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
            pg.wait_for_timeout(1000)
            pg.click("#nav button[data-t='meals']")
            pg.click(".seg[data-seg='meals'] button[data-s='recipes']")
            pg.wait_for_timeout(700)
            pg.fill("#rc_q", "masala")
            pg.wait_for_timeout(300)
            rows = pg.locator("#rc_rows .rcrow").count()
            pg.click("#rc_rows .rcrow")
            pg.wait_for_timeout(700)
            txt = pg.inner_text("#rcOne")
            save_shown = pg.evaluate("getComputedStyle(document.querySelector('.save')).display")
            wide = pg.evaluate("document.documentElement.scrollWidth")
            b.close()
        srv.shutdown()
        assert not errs, "page errors: %s" % errs
        assert rows == 1, "search left %d rows" % rows
        assert "Onion-free version" in txt and "Step two." in txt and "Send to the cook" in txt, txt[:300]
        assert save_shown == "none", "the meal Save bar shows over the recipes"
        assert wide <= 390, "sideways scroll %dpx" % wide
        return "search, open, onion-free version, method, share; no errors"
    check("07 in a real browser: find and open a recipe", t07)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.24.0 -- Recipes")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

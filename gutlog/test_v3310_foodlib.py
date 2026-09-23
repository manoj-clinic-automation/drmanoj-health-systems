#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.31.0 -- a food library by weight, a sourced nutrition table, and a
time on every entry that can be changed (GUTLOG_V3310_FOODLIB).

The server half is checked through the API against a scratch database. The
page half is driven in Chromium at the folded width, because the food list,
the weight fields and the time lists are drawn by the page's own JavaScript
and a server fetch would prove nothing about them (the v3.4.0 lesson).

Scratch database, real app, synthetic foods, "Medicine A". No real food
list, dose or medicine name appears here: this file is TRACKED and the
repository is public.

  python3 test_v3310_foodlib.py [path/to/gutlog/app.py]
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
B = {}

CARDS = {"cards": [
    {"name": "Lunch", "from": "00:00", "rows": [
        {"kind": "fixed", "label": "Test khichdi", "text": "Test khichdi",
         "items": [["Test khichdi", 1]]}]}]}


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
    w = tempfile.mkdtemp(prefix="gutlog_food_")
    mf = os.path.join(w, "meals.json")
    json.dump(CARDS, open(mf, "w"))
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=mf, GUTLOG_PLAN_FILE=os.path.join(w, "none.json"),
                      GUTLOG_MIRROR_STAMP=os.path.join(w, "stamp.json"))
    # The bundled table sits beside the real app.py; a reconstructed or
    # mutated copy is written beside it too, so the default path finds it.
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3310", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)

    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)
    c.get("/")
    gdb = os.environ["GUTLOG_DB"]

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        con.row_factory = sqlite3.Row
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def cols(table):
        return [r[1] for r in q("PRAGMA table_info(" + table + ")")]

    def j(resp):
        return json.loads(resp.get_data(as_text=True))

    T = date.today()
    D0 = T.isoformat()
    D1 = (T - timedelta(days=1)).isoformat()
    D2 = (T - timedelta(days=2)).isoformat()
    D3 = (T - timedelta(days=3)).isoformat()
    NOW = datetime.now().strftime("%H:%M")

    # ------------------------------------------------ the pre-v3.31 library
    # Three foods as the old library held them: a portion in grams, a
    # household measure, and a text with no weight in it at all. They are
    # written with plain SQL and the new columns cleared, then the schema
    # version is put back, so the next request runs the migration exactly as
    # the live database will on the first request after the restart.
    OLD = [("Test roasted chana", "30 g", 5.0, 110.0, 4.0),
           ("Test moong dal", "1 katori", 9.8, 180.0, 4.0),
           ("Test mixed plate", "any usual serve", 7.0, 300.0, 3.0),
           ("Test khichdi", "1 bowl", 8.0, 250.0, 3.0)]
    for n, por, p, k, f in OLD:
        q("INSERT OR IGNORE INTO library(cat,item,portion,protein,kcal,fibre,fodmap) "
          "VALUES('H',?,?,?,?,?,'L')", (n, por, p, k, f))
    # a past day with meals on it, logged before the migration
    for day, mt, n, p, k, f in ((D2, "13:00", "Test moong dal", 9.8, 180, 4.0),
                                (D2, "20:00", "Test roasted chana", 5.0, 110, 4.0)):
        r = c.post("/api/meals", json={"day": day, "mtime": mt, "slot": "Lunch", "notes": "",
                                       "items": [{"n": n, "q": 1, "p": p, "k": k, "f": f,
                                                  "fm": "L"}]})
        assert r.status_code == 200, "seeding a past meal failed: %s" % r.get_data(as_text=True)
    # The "before" picture is taken BEFORE the schema version is put back:
    # any request after that runs the migration, and a snapshot taken by one
    # would already contain whatever the migration did -- the negative control
    # caught exactly that, a migration that rewrote meals passing this check.
    before = dict((d, j(c.get("/api/nutrition/day/" + d))) for d in (D2,))
    meals_before = [tuple(r) for r in q("SELECT id, day, mtime, items, protein, kcal, fibre, "
                                        "fscore FROM meals ORDER BY id")]
    newcols = [x for x in ("portion_qty", "b_protein", "source", "portion_est") if x in cols("library")]
    if newcols:
        q("UPDATE library SET portion_qty=NULL, b_protein=NULL, b_kcal=NULL, b_fibre=NULL, "
          "portion_est=0, source='', source_date='' WHERE item LIKE 'Test %'")
        q("UPDATE settings SET value='3.3.4' WHERE key='schema_version'")
    c.get("/api/summary/" + D0)     # any request: db() -> _migrate()
    LIB = dict((x["item"], x) for x in j(c.get("/api/library")))

    # ---------------------------------------------------------------- 01
    def t01():
        a = LIB["Test roasted chana"]
        assert a.get("portion_qty") == 30 and a.get("portion_unit") == "g", \
            "a '30 g' portion did not become 30 g: %r" % [a.get("portion_qty"), a.get("portion_unit")]
        assert a.get("portion_est") == 0, "a portion written in grams was marked estimated"
        assert abs((a.get("b_protein") or 0) - 16.67) < 0.01, \
            "per-100 protein is %r, not 5 g x 100/30" % a.get("b_protein")
        b = LIB["Test moong dal"]
        assert b.get("portion_qty") == 150 and b.get("portion_est") == 1, \
            "'1 katori' is %r g, estimated=%r" % (b.get("portion_qty"), b.get("portion_est"))
        assert b.get("source") == "estimated", "the household measure's source is %r" % b.get("source")
        c3 = LIB["Test mixed plate"]
        assert c3.get("portion_qty") in (None, 0) and c3.get("portion_est") == 1, \
            "a portion with no weight in it was given one: %r" % c3.get("portion_qty")
        for n, por, p, k, f in OLD:
            x = LIB[n]
            assert (x["protein"], x["kcal"], x["fibre"]) == (p, k, f), \
                "%s per-portion values changed in the migration" % n
        return "30 g exact; 1 katori = 150 g estimated; no weight left blank; per-portion untouched"
    check("01 the migration: grams exactly, a household measure flagged estimated", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        after = j(c.get("/api/nutrition/day/" + D2))
        for key in ("kcal", "protein", "fibre", "meals"):
            assert after[key] == before[D2][key], \
                "%s on %s moved from %r to %r" % (key, D2, before[D2][key], after[key])
        now_rows = [tuple(r) for r in q("SELECT id, day, mtime, items, protein, kcal, fibre, "
                                        "fscore FROM meals ORDER BY id")]
        assert now_rows == meals_before, "the migration wrote to the meals table"
        return "%s: %d kcal, %s g protein, before and after" % (D2, after["kcal"], after["protein"])
    check("02 a past day's totals are unchanged by the migration", t02)

    # ---------------------------------------------------------------- 03
    ctx = {}

    def t03():
        r = c.post("/api/library", json={"item": "Test oats", "portion": "", "fodmap": "L",
                                         "cat": "A", "portion_qty": 100, "portion_unit": "g",
                                         "b_protein": 10, "b_kcal": 200, "b_fibre": 5})
        assert r.status_code == 200, r.get_data(as_text=True)
        ctx["oats"] = j(r)["id"]
        x = dict((y["item"], y) for y in j(c.get("/api/library")))["Test oats"]
        assert x["portion_qty"] == 100 and x["b_protein"] == 10, "the basis was not stored"
        assert x["protein"] == 10, "the per-portion value was not worked out from the basis"
        s = j(c.get("/api/foods/search?q=test%20oats"))["foods"]
        hit = [f for f in s if f["n"] == "Test oats"]
        assert hit and hit[0].get("w") is True, "the new food is not offered by weight"
        return "saved with its weight, and offered for logging by weight"
    check("03 a new food is saved with its weight and is reusable", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        r = c.post("/api/meals", json={"day": D1, "mtime": "09:00", "slot": "Breakfast",
                                       "items": [{"n": "Test oats", "g": 40,
                                                  "p": 99, "k": 999, "f": 99}]})
        assert r.status_code == 200, r.get_data(as_text=True)
        d = j(c.get("/api/nutrition/day/" + D1))
        assert d["protein"] == 4.0 and d["kcal"] == 80 and d["fibre"] == 2.0, \
            "40 g of a 10/200/5 per-100 food logged as %r/%r/%r" % (d["protein"], d["kcal"], d["fibre"])
        it = d["rows"][0]["items"][0]
        assert it.get("g") == 40, "the weight is not kept on the meal: %r" % it
        return "40 g -> 4.0 g protein, 80 kcal, 2.0 g fibre (the page's numbers ignored)"
    check("04 logging 40 g of a food with a 100 g basis records 0.4 x its values", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        r = c.post("/api/library", json={"item": "Test masoor dry", "fodmap": "M", "cat": "B",
                                         "portion_qty": 50, "portion_unit": "g",
                                         "weighed_dry": True,
                                         "b_protein": 24, "b_kcal": 350, "b_fibre": 11})
        assert r.status_code == 200, r.get_data(as_text=True)
        r = c.post("/api/meals", json={"day": D3, "mtime": "13:00", "slot": "Lunch",
                                       "items": [{"n": "Test masoor dry", "g": 60}]})
        assert r.status_code == 200, r.get_data(as_text=True)
        d = j(c.get("/api/nutrition/day/" + D3))
        assert d["protein"] == 14.4 and d["kcal"] == 210 and d["fibre"] == 6.6, \
            "60 g dry logged as %r/%r/%r" % (d["protein"], d["kcal"], d["fibre"])
        it = d["rows"][0]["items"][0]
        assert it.get("dry") == 1, "the meal does not say the weight was dry: %r" % it
        return "60 g dry -> 14.4 g protein, 210 kcal, 6.6 g fibre, marked dry"
    check("05 a dry-weight food logged at 60 g dry records 0.6 x the dry basis", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        m = j(c.get("/api/foodtable?q=masoor%20dal&dry=1"))
        assert m.get("available"), "the bundled table did not load"
        top = m["matches"][0]
        assert top["desc"] == "Lentils, raw", "masoor dal, dry, found %r first" % top["desc"]
        r = c.post("/api/library", json={"item": "Test lentil table", "fodmap": "M", "cat": "B",
                                         "portion_qty": 40, "weighed_dry": True, "fdc": top["fdc"],
                                         "b_protein": top["protein"], "b_kcal": top["kcal"],
                                         "b_fibre": top["fibre"]})
        assert j(r).get("source") == "USDA", "table values were not recorded as USDA: %r" % j(r)
        lid = j(r)["id"]
        r = c.post("/api/library/%d" % lid, json={"b_protein": 25, "b_kcal": top["kcal"],
                                                  "b_fibre": top["fibre"], "fdc": top["fdc"],
                                                  "portion_qty": 40})
        assert j(r).get("source") == "own", "an edited value stayed %r" % j(r).get("source")
        c.get("/api/foodtable?q=masoor%20dal&dry=1")        # a later lookup
        c.post("/api/library/%d" % lid, json={"fav": True})  # a star tap
        c.post("/api/library/%d" % lid, json={"b_protein": 25, "b_kcal": top["kcal"],
                                              "b_fibre": top["fibre"], "portion_qty": 40})
        c.get("/api/library")                                 # the backfill runs here
        x = q("SELECT source, b_protein FROM library WHERE id=?", (lid,))[0]
        assert x["source"] == "own" and x["b_protein"] == 25, \
            "his value reverted: source %r, protein %r" % (x["source"], x["b_protein"])
        return "table pick = USDA; his edit = own, and it stays own"
    check("06 editing nutrition makes it 'own' and a later lookup never reverts it", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        top = j(c.get("/api/foodtable?q=masoor%20dal&dry=1"))["matches"][0]
        r = c.post("/api/library", json={"item": "Test not the table", "fodmap": "M",
                                         "portion_qty": 40, "fdc": top["fdc"],
                                         "b_protein": top["protein"] + 3, "b_kcal": top["kcal"],
                                         "b_fibre": top["fibre"]})
        assert j(r).get("source") == "own", \
            "values that differ from the table row were labelled %r" % j(r).get("source")
        none = j(c.get("/api/foodtable?q=paneer"))["matches"]
        assert not [m for m in none if m["full"]], "a food the table does not know got a match"
        return "a changed number is not passed off as the table's; unknown food -> no match"
    check("07 only values that match the table row are labelled USDA", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        r = c.post("/api/mealcards/log", json={"card": "", "slot": "Snack", "day": D1,
                                               "mtime": "16:00",
                                               "extra": [{"n": "Test oats", "q": 1, "g": 25}]})
        assert r.status_code == 200, r.get_data(as_text=True)
        mid = j(r)["id"]
        row = q("SELECT items, protein FROM meals WHERE id=?", (mid,))[0]
        assert row["protein"] == 2.5, "25 g from the Now card logged %r g protein" % row["protein"]
        meta = json.loads(q("SELECT extra FROM meal_meta WHERE meal_id=?", (mid,))[0]["extra"])
        assert meta and meta[0].get("g") == 25, "the card edit would lose the weight: %r" % meta
        return "Now card 'also had' 25 g -> 2.5 g protein, and the weight is kept for Edit"
    check("08 the Now card logs by weight too", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        r = c.post("/api/meals", json={"day": D2, "mtime": "08:00", "slot": "Breakfast",
                                       "items": [{"n": "Test oats", "g": 50}]})
        assert r.status_code == 200, r.get_data(as_text=True)
        mid = q("SELECT id FROM meals WHERE day=? AND mtime='08:00'", (D2,))[0]["id"]
        d2a = j(c.get("/api/nutrition/day/" + D2))
        r = c.post("/api/retime", json={"table": "meals", "id": mid, "day": D2, "time": "21:30"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        d2b = j(c.get("/api/nutrition/day/" + D2))
        assert [m["mtime"] for m in d2b["rows"] if m["id"] == mid] == ["21:30"], \
            "the past meal's time did not change"
        assert d2b["protein"] == d2a["protein"], "a same-day time change moved the total"
        d3a = j(c.get("/api/nutrition/day/" + D3))
        r = c.post("/api/retime", json={"table": "meals", "id": mid, "day": D3, "time": "23:10"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        d2c = j(c.get("/api/nutrition/day/" + D2))
        d3b = j(c.get("/api/nutrition/day/" + D3))
        assert round(d2a["protein"] - d2c["protein"], 1) == 5.0, \
            "%s kept the moved meal: %r -> %r" % (D2, d2a["protein"], d2c["protein"])
        assert round(d3b["protein"] - d3a["protein"], 1) == 5.0, \
            "%s did not gain it: %r -> %r" % (D3, d3a["protein"], d3b["protein"])
        ed = q("SELECT old_day, old_time, new_day, new_time FROM edits WHERE tbl='meals' AND rid=?",
               (mid,))
        assert len(ed) == 2, "the two changes were not both recorded: %r" % [tuple(e) for e in ed]
        return "08:00 -> 21:30 on %s, then onto %s: -5.0 g there, +5.0 g here, both audited" % (D2, D3)
    check("09 a past-day meal's time is edited and the day totals move with it", t09)

    # ---------------------------------------------------------------- 10
    def t10():
        c.post("/api/prnmeds", json={"name": "Medicine A"})
        mid = q("SELECT id FROM prnmeds WHERE name='Medicine A'")[0]["id"]
        r = c.post("/api/now/dose", json={"med_id": mid, "status": "EXTRA", "day": D2,
                                          "dtime": "10:00"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        did = j(r)["id"]
        n2a = j(c.get("/api/summary/" + D2))["doses"]
        n3a = j(c.get("/api/summary/" + D3))["doses"]
        r = c.post("/api/retime", json={"table": "doses", "id": did, "day": D3, "time": "22:45"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        n2b = j(c.get("/api/summary/" + D2))["doses"]
        n3b = j(c.get("/api/summary/" + D3))["doses"]
        assert (n2a - n2b, n3b - n3a) == (1, 0 + 1), \
            "dose counts %s %r->%r, %s %r->%r" % (D2, n2a, n2b, D3, n3a, n3b)
        v = j(c.get("/api/dayview?day=" + D3))["entries"]
        hit = [e for e in v if e["tbl"] == "doses" and e["id"] == did]
        assert hit and hit[0]["time"] == "22:45" and hit[0]["edited"], \
            "the day view does not show the new time as edited: %r" % hit
        t = j(c.get("/api/doses/today/" + D3))
        assert t and t[0].get("ids") == [did], "the Meds list cannot reach the dose to edit it: %r" % t
        return "a past dose moved a day: counts follow it, the view marks it edited"
    check("10 a past-day dose's time is edited and the day counts move with it", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        if NOW >= "23:58":
            return "SKIPPED: too close to midnight to ask for a later time"
        later = (datetime.now() + timedelta(minutes=2)).strftime("%H:%M")
        r = c.post("/api/mealcards/log", json={"card": "Lunch", "day": D0, "mtime": later})
        assert r.status_code == 400, "a meal was logged at %s, later than now" % later
        r = c.post("/api/meals", json={"day": D0, "mtime": later, "slot": "Snack",
                                       "items": [{"n": "Test oats", "g": 10}]})
        assert r.status_code == 400, "the basket logged a meal later than now"
        r = c.post("/api/mealcards/log", json={"card": "Lunch", "day": D1, "mtime": "23:55"})
        assert r.status_code == 200, "a late time on a PAST day was refused"
        return "later than now today is refused; any time on a past day is fine"
    check("11 a meal cannot be logged later than now today", t11)

    # ---------------------------------------------------------------- 12
    def t12():
        r = c.post("/api/mealcards/log", json={"card": "Lunch", "day": D1, "mtime": "12:30"})
        mid = j(r)["id"]
        r = c.post("/api/meals/%d/replace" % mid, json={"card": "Lunch", "mtime": "13:15"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        row = q("SELECT day, mtime FROM meals WHERE id=?", (mid,))[0]
        assert (row["day"], row["mtime"]) == (D1, "13:15"), \
            "the edited meal is at %r" % (tuple(row),)
        ed = q("SELECT old_time, new_time FROM edits WHERE tbl='meals' AND rid=?", (mid,))
        assert [tuple(e) for e in ed] == [("12:30", "13:15")], \
            "a time changed from the card was not recorded: %r" % [tuple(e) for e in ed]
        return "a past meal edited from the card keeps its day, takes the new time, and is audited"
    check("12 a time changed from the meal card is kept and recorded", t12)

    # ------------------------------------------------ the browser checks
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def browse():
        if B or not have_pw:
            return
        B["done"] = True
        # today's dose for the Meds tab
        c.post("/api/doses", json={"meds": ["Medicine A"], "day": D0, "dtime": "00:00"})
        from werkzeug.serving import make_server
        srv = make_server("127.0.0.1", 0, gm.app)
        port = srv.server_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 300, "height": 700})
            pg.set_default_timeout(6000)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("dialog", lambda d: d.accept())
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1000)

            def tap(sel):
                # The fixed nav and save bar cover the bottom of a 300px
                # screen; scroll the control to the middle and tap it there.
                pg.wait_for_selector(sel, state="attached")
                pg.eval_on_selector(sel, "e=>{e.scrollIntoView({block:'center'});e.click();}")

            def wide():
                # the width, and what sticks out -- a bare number says nothing
                # about which element to fix
                return pg.evaluate(
                    "(()=>{const w=document.documentElement.scrollWidth;if(w<=300)return w;"
                    "const o=[...document.querySelectorAll('body *')].filter(e=>e.offsetParent!==null"
                    "&&e.getBoundingClientRect().right>301).slice(-4).map(e=>e.tagName+'.'+"
                    "(e.className||'')+'#'+(e.id||''));return w+' '+o.join(' | ');})()")

            def visible_time_inputs():
                return pg.evaluate("[...document.querySelectorAll('input[type=time]')]"
                                   ".filter(e=>e.offsetParent!==null).length")

            def home(tab=None):
                pg.goto("http://127.0.0.1:%d/" % port)
                pg.wait_for_timeout(1000)
                if tab:
                    pg.click("#nav button[data-t=%s]" % tab)
                    pg.wait_for_timeout(700)

            hh = NOW[:2]

            # Each part starts from a fresh page and records its own failure,
            # so one broken step cannot take every other check down with it
            # -- which would make a negative control "see" failures that say
            # nothing about the assertion being controlled.
            def step(name, fn):
                try:
                    fn()
                except Exception as exc:
                    B["err_" + name] = type(exc).__name__ + ": " + str(exc).split("\n")[0]

            # ---- the Now card: a Time field, default now, in two lists
            def s_now():
                B["now_time_lists"] = pg.evaluate(
                    "(()=>{const i=document.getElementById('mcTime');if(!i)return null;"
                    "const w=i.nextElementSibling;return {hidden:i.style.display==='none',"
                    "lists:!!(w&&w.querySelector('select.tph')&&w.querySelector('select.tpm')),"
                    "value:i.value};})()")
                # read the clock AFTER the page did, so "defaults to now" is a
                # window, not a race against the minute the suite started in
                B["now_at"] = datetime.now().strftime("%H:%M")
                B["wide_now"] = wide()
                pg.select_option("#mealBody .mctime select.tph", hh)
                pg.select_option("#mealBody .mctime select.tpm", "00")
                tap("#mcGo")
                pg.wait_for_timeout(900)
                B["now_logged"] = [tuple(r) for r in q(
                    "SELECT mtime FROM meals WHERE day=? AND slot='Lunch'", (D0,))]
                B["now_row"] = pg.inner_text("#mealToday")
                tap("#mealToday .chip.tbtn")
                pg.wait_for_timeout(400)
                B["now_edit_lists"] = pg.locator(".varpick.tedit select.tph").count()

            # ---- the Meals tab: the basket by weight
            def s_basket():
                home("meals")
                B["ml_time_now"] = pg.evaluate("document.getElementById('ml_time').value")
                pg.fill("#ml_search", "Test masoor")
                pg.wait_for_timeout(300)
                pg.click("#ml_results .chip")
                pg.wait_for_timeout(300)
                B["basket_text"] = pg.inner_text("#ml_basket")
                pg.fill("#ml_basket input.bkg", "60")
                pg.wait_for_timeout(200)
                B["basket_tot"] = pg.inner_text("#ml_tot")
                B["wide_basket"] = wide()

            # ---- two days back, change a meal's time from its row
            def s_d2():
                pg.goto("http://127.0.0.1:%d/?open=meals&day=%s" % (port, D2))
                pg.wait_for_timeout(1200)
                first = pg.locator("#mlDayMeals .mlmeal .chip.tbtn").first
                B["d2_first_time"] = first.inner_text()
                first.click()
                pg.wait_for_timeout(400)
                B["d2_visible_time_inputs"] = visible_time_inputs()
                B["d2_lists"] = pg.locator(".varpick.tedit select.tph").count()
                pg.select_option(".varpick.tedit select.tph", "19")
                pg.select_option(".varpick.tedit select.tpm", "45")
                tap(".varpick.tedit button.go")
                pg.wait_for_timeout(900)
                B["d2_after_list"] = pg.inner_text("#mlDayMeals")
                B["d2_rows"] = [tuple(r) for r in q("SELECT day, mtime FROM meals WHERE mtime='19:45'")]
                B["wide_meals"] = wide()

            # ---- the food library screen
            def s_lib():
                home("meals")
                tap("#openFoods")
                pg.wait_for_timeout(700)
                B["lib_text"] = pg.inner_text("#lib_list")
                B["lib_row"] = pg.inner_text("#lib_list .librow[data-item='Test roasted chana']")
                B["lib_row_dry"] = pg.inner_text("#lib_list .librow[data-item='Test masoor dry']")
                B["wide_lib"] = wide()

            # ---- the editor: change a value; add a food the table knows
            def s_editor():
                home("meals")
                tap("#openFoods")
                pg.wait_for_timeout(700)
                tap("#lib_list .librow[data-item='Test roasted chana']")
                pg.wait_for_timeout(700)
                B["wide_edit"] = wide()
                pg.fill("#lib_edit .lf_p", "20")
                tap("#lib_edit .lf_save")
                pg.wait_for_timeout(900)
                B["ed_saved"] = [tuple(r) for r in q(
                    "SELECT b_protein, protein, source FROM library WHERE item='Test roasted chana'")]
                tap("#lib_addnew")
                pg.wait_for_timeout(300)
                pg.fill("#lib_edit .lf_item", "Masoor dal")
                pg.wait_for_timeout(1200)
                B["new_src"] = pg.inner_text("#lib_edit .lf_src")
                B["new_p"] = pg.input_value("#lib_edit .lf_p")
                pg.fill("#lib_edit .lf_pq", "40")
                pg.check("#lib_edit .lf_dry")
                pg.wait_for_timeout(900)
                tap("#lib_edit .lf_save")
                pg.wait_for_timeout(900)
                B["new_saved"] = [tuple(r) for r in q(
                    "SELECT portion_qty, weighed_dry, source FROM library WHERE item='Masoor dal'")]

            # ---- the Meds tab: today's dose time is a button
            def s_meds():
                home("meds")
                B["wide_meds"] = wide()
                B["meds_btn"] = pg.locator("#prnToday .chip.tbtn").count()
                if hh != "00":
                    tap("#prnToday .chip.tbtn")
                    pg.wait_for_timeout(400)
                    pg.select_option(".varpick.tedit select.tph", hh)
                    pg.select_option(".varpick.tedit select.tpm", "00")
                    tap(".varpick.tedit button.go")
                    pg.wait_for_timeout(800)
                    B["meds_after"] = pg.inner_text("#prnToday")

            for name, fn in (("now", s_now), ("basket", s_basket), ("d2", s_d2),
                             ("lib", s_lib), ("editor", s_editor), ("meds", s_meds)):
                step(name, fn)
            B["errs"] = errs
            b.close()
        srv.shutdown()

    def need():
        if not have_pw:
            return False
        browse()
        return True

    def G(key, part):
        """A browser value, or the reason its part never produced it."""
        if key not in B:
            raise AssertionError("the %s step did not get this far: %s"
                                 % (part, B.get("err_" + part, "no error recorded")))
        return B[key]

    def t13():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert not B["errs"], "page errors: %s" % B["errs"]
        row = G("lib_row", "lib")
        assert "30 g" in row, "the food's portion weight is not on screen: %r" % row
        assert "per 100 g" in row, "the per-100 basis is not on screen: %r" % row
        assert "estimated" in row, "the source is not on screen: %r" % row
        dry = G("lib_row_dry", "lib")
        assert "dry" in dry and "your values" in dry, "the dry food's row: %r" % dry
        assert G("lib_text", "lib").strip().upper().startswith("MOST USED"), \
            "the list does not start with the most used foods: %r" % B["lib_text"][:60]
        return "portion 30 g, per 100 g, source, and most used first"
    check("13 the food list shows each food's portion, per-100 values and source", t13)

    def t14():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert "dry weight" in G("basket_text", "basket"), \
            "the weight field does not say dry weight: %r" % B["basket_text"]
        assert "14.4 g protein" in G("basket_tot", "basket"), \
            "60 g dry in the basket shows %r" % B["basket_tot"]
        return "the basket row says 'dry weight' and 60 g shows 14.4 g protein"
    check("14 the basket takes a weight and says when it is a dry weight", t14)

    def t15():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("d2_lists", "d2") == 1, "the time editor is not the two lists"
        assert G("d2_visible_time_inputs", "d2") == 0, "a native time box is showing"
        assert "19:45" in G("d2_after_list", "d2"), \
            "the past meal did not take the new time: %r" % B["d2_after_list"]
        assert [r[0] for r in G("d2_rows", "d2")] == [D2], \
            "the re-timed meal did not stay on its own day: %r" % B["d2_rows"]
        return "%s -> 19:45 on %s, in the lists, no native picker" % (B["d2_first_time"], D2)
    check("15 a meal's time on a past day is changed from its row", t15)

    def t16():
        if not need():
            return "SKIPPED: Playwright not installed here"
        t = G("now_time_lists", "now")
        assert t and t["hidden"] and t["lists"], "the Now card's time is not the two lists: %r" % t
        assert t["value"] and NOW <= t["value"] <= B.get("now_at", NOW), \
            "the Now card's time does not default to now: %r (suite %s, read %s)" % (t, NOW, B.get("now_at"))
        assert G("now_logged", "now") == [(NOW[:2] + ":00",)], \
            "the card logged at %r, not the time chosen" % B["now_logged"]
        assert (NOW[:2] + ":00") in G("now_row", "now"), "the logged meal does not show its time"
        assert G("now_edit_lists", "now") == 1, "tapping the time did not open the lists"
        return "default now, chosen %s:00 logged, and the row's time opens the lists" % NOW[:2]
    check("16 the Now meal card has a time, default now, and its rows can be re-timed", t16)

    def t17():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("meds_btn", "meds") >= 1, "today's dose time is not a button"
        if NOW[:2] != "00":
            assert (NOW[:2] + ":00") in G("meds_after", "meds"), \
                "the dose time did not change: %r" % B["meds_after"]
        return "the Meds tab's dose time is a button and saves"
    check("17 a dose time on the Meds tab is changed from the list", t17)

    def t18():
        if not need():
            return "SKIPPED: Playwright not installed here"
        s = G("ed_saved", "editor")
        assert s and s[0][0] == 20 and s[0][2] == "own", "editing per-100 protein saved %r" % s
        assert abs(s[0][1] - 6.0) < 0.01, "the portion value was not 20 x 30/100: %r" % s
        assert "USDA" in G("new_src", "editor") and G("new_p", "editor") not in ("", None), \
            "a new known food was not filled from the table: %r %r" % (B["new_src"], B["new_p"])
        assert G("new_saved", "editor") and B["new_saved"][0] == (40, 1, "USDA"), \
            "the new food saved as %r" % B["new_saved"]
        return "edit -> own, 6.0 g a portion; a new 'masoor dal' filled from USDA and saved"
    check("18 the editor saves per-100 values and fills a new food from the table", t18)

    def t19():
        if not need():
            return "SKIPPED: Playwright not installed here"
        for k, part in (("wide_now", "now"), ("wide_basket", "basket"), ("wide_meals", "d2"),
                        ("wide_lib", "lib"), ("wide_edit", "editor"), ("wide_meds", "meds")):
            v = G(k, part)
            assert isinstance(v, int) and v <= 300, "%s scrolls sideways: %s" % (k, v)
        return "Now, basket, Meals, food list, editor and Meds all fit 300 px"
    check("19 everything new fits the folded screen", t19)

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

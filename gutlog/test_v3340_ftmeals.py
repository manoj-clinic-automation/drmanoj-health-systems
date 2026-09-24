#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.34.0 -- Week 0 of the food test reads dinner from Meals; the Now
page folds and reorders; PRN Add medicine goes on to Salts
(GUTLOG_V3340_FTMEALS).

The engine through the API: both meal paths, the size rule, every edit that
must follow through, the cramp question, and the one-off backfill run on
23..29-Sep with the server's clock set to 30-Sep. The page in Chromium at
300 px, because the card, the folds and the order are drawn by the page's
own JavaScript -- including the 30-Sep card, with the browser's clock set
to that day as well.

Scratch database, a SYNTHETIC plan ("Test chana"), synthetic foods,
"Medicine A". Python 3.9.

  python3 test_v3340_ftmeals.py [path/to/gutlog/app.py]
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
PLAN = {"plan_id": None, "reminder": "Test reminder.",
        "items": [{"slug": "w0", "week": "Week 0", "label": "Dinner changes only", "kind": "dinner",
                   "days": 7, "notes": ["Test note."]},
                  {"slug": "tchana", "week": "Week 1", "label": "Test chana", "meal": "Breakfast",
                   "basis": "dry", "steps": [{"g": 12}, {"g": 25}], "washout": 2}]}
CARDS = {"cards": [{"name": "Dinner", "from": "00:00",
                    "rows": [{"kind": "fixed", "label": "Test dal", "text": "Test dal",
                              "items": [["Test dal", 1]]}]}]}
WANT_ORDER = ["nowDoses", "nowExtraCard", "nowMeal", "nowSym", "nowPain", "ftCard", "nowBP", "nowAct",
              "nowCtx", "nowDown", "nowWatch"]
SEP = ["2026-09-%02d" % d for d in range(23, 30)]


class FakeDate(date):
    @classmethod
    def today(cls):
        return date(2026, 9, 30)


class FakeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 30, 8, 0, 0)


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
    w = tempfile.mkdtemp(prefix="gutlog_w0_")
    cards = os.path.join(w, "cards.json")
    with open(cards, "w", encoding="utf-8") as fh:
        json.dump(CARDS, fh)
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=cards,
                      GUTLOG_PLAN_FILE=os.path.join(w, "none.json"),
                      GUTLOG_MIRROR_STAMP=os.path.join(w, "stamp.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3340", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    real_date, real_dt = gm.date, gm.datetime
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    c.get("/")
    gdb = os.environ["GUTLOG_DB"]

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        con.row_factory = sqlite3.Row
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def j(resp):
        return json.loads(resp.get_data(as_text=True))

    def D(n):
        return (date.today() - timedelta(days=n)).isoformat()

    def tab_meal(day, mt, slot, kcal=500):
        r = c.post("/api/meals", json={"day": day, "mtime": mt, "slot": slot, "notes": "",
                                       "items": [{"n": "Test rice", "q": 1, "p": 2, "k": kcal, "f": 1,
                                                  "fm": "L"}]})
        assert r.status_code == 200, r.get_data(as_text=True)
        return q("SELECT id FROM meals WHERE day=? AND mtime=? AND slot=? ORDER BY id DESC",
                 (day, mt, slot))[0]["id"]

    def w0(day=None):
        rows = [dict(r) for r in q("SELECT * FROM ft_log WHERE slug='w0' AND kind='dinner' ORDER BY day")]
        return [r for r in rows if r["day"] == day] if day else rows

    def n_meals(day):
        return q("SELECT COUNT(*) AS n FROM meals WHERE day=?", (day,))[0]["n"]

    def fake(on):
        gm.date, gm.datetime = (FakeDate, FakeDateTime) if on else (real_date, real_dt)

    if hasattr(gm, "ft_seed"):
        with gm.app.app_context():
            gm.ft_seed(gm.db(), PLAN)
    q("INSERT OR REPLACE INTO settings(key, value) VALUES('ft_week0_from', ?)", (D(35),))

    # ---------------------------------------------------------------- 01
    def t01():
        tab_meal(D(40), "19:00", "Dinner")          # before the plan's first day: never counts
        assert not w0(), "a Dinner from before the plan counted for Week 0"
        mid = tab_meal(D(12), "19:00", "Dinner")
        r = w0(D(12))
        assert r, "a Dinner from the Meals tab did not complete the Week 0 day"
        r = r[0]
        assert (r["meal_id"], r["ltime"], r["step"], r["cramp"]) == (mid, "19:00", 0, None), r
        assert n_meals(D(12)) == 1, "a second meal row was made: %d rows" % n_meals(D(12))
        before = len(q("SELECT id FROM ft_log"))
        x = c.post("/api/ft/log", json={"slug": "w0", "step": 1, "day": D(0), "time": "00:00",
                                        "size": "small", "cramp": True})
        assert x.status_code == 409, "the Food Test still takes a dinner entry: %s" % x.status_code
        assert len(q("SELECT id FROM ft_log")) == before and n_meals(D(0)) == 0, "the refusal wrote something"
        return "before the plan: nothing; step 0 linked to meal %d at 19:00, cramp blank; one meal row; " \
               "/api/ft/log dinner -> 409" % mid
    check("01 a Dinner from the Meals tab completes the Week 0 day, with no second meal row", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        c.post("/api/foods/new", json={"name": "Test dal", "kind": "curry"})
        r = c.post("/api/mealcards/log", json={"card": "Dinner", "choices": {}, "day": D(11), "mtime": "19:30"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        # read the step BEFORE anything else touches the day: a later Meals-tab
        # write re-syncs it, and would hide a card path that never synced.
        row = w0(D(11))
        assert row and row[0]["ltime"] == "19:30" and row[0]["step"] == 1, "card Dinner: %r" % row
        mid = q("SELECT id FROM meals WHERE day=? AND slot='Dinner'", (D(11),))[0]["id"]
        assert row[0]["meal_id"] == mid, "not linked to the card's meal"
        tab_meal(D(11), "13:00", "Lunch")
        assert n_meals(D(11)) == 2, "meals on the day: %d, want the dinner and the lunch" % n_meals(D(11))
        assert len(w0()) == 2, "the Lunch made a Week 0 row"
        return "Now card Dinner -> step 1 at 19:30; the Lunch changes nothing"
    check("02 a Dinner from the Now meal card completes the day too", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        k11 = q("SELECT kcal FROM meals WHERE day=? AND slot='Dinner'", (D(11),))[0]["kcal"]
        assert k11 < 300, "precondition: the card dinner is %s kcal" % k11
        tab_meal(D(10), "19:00", "Dinner", 500)   # two earlier Dinner days only -> usual
        tab_meal(D(9), "19:00", "Dinner", 300)    # median 500 -> below 375 -> small
        tab_meal(D(8), "19:00", "Dinner", 700)    # median 400 -> above 500 -> large
        tab_meal(D(7), "19:00", "Dinner", 0)      # no kcal -> usual
        got = [(r["day"], r["size"]) for r in w0() if r["day"] in (D(10), D(9), D(8), D(7))]
        want = [(D(10), "usual"), (D(9), "small"), (D(8), "large"), (D(7), "usual")]
        assert sorted(got) == sorted(want), "sizes %r" % got
        return "fewer than 3 earlier days: usual; 300 vs 500: small; 700 vs 400: large; no kcal: usual"
    check("03 the size follows the stated rule, never a guess", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        mid = lambda dd: q("SELECT id FROM meals WHERE day=? AND slot='Dinner'", (dd,))[0]["id"]
        r = c.post("/api/retime", json={"table": "meals", "id": mid(D(10)), "day": D(10), "time": "20:15"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        assert w0(D(10))[0]["ltime"] == "20:15", "re-timing the Dinner did not re-time the step"
        r = c.post("/api/meals/%d/replace" % mid(D(11)), json={"card": "Dinner", "choices": {}, "mtime": "21:00"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        assert w0(D(11))[0]["ltime"] == "21:00", "editing the card Dinner's time did not follow"
        m9 = mid(D(9))
        r = c.post("/api/retime", json={"table": "meals", "id": m9, "day": D(6), "time": "19:45"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        assert not w0(D(9)) and w0(D(6)) and w0(D(6))[0]["ltime"] == "19:45" and w0(D(6))[0]["meal_id"] == m9, \
            "moving the Dinner to another day did not move the step"
        c.post("/api/meals/%d/delete" % mid(D(7)), json={})
        assert not w0(D(7)), "deleting the Dinner in Meals left its step"
        c.post("/api/delete/meals/%d" % mid(D(8)), json={})
        assert not w0(D(8)), "deleting the Dinner from Day by day left its step"
        r = c.post("/api/meals/%d/replace" % mid(D(11)),
                   json={"card": "", "slot": "Lunch", "extra": [{"n": "Test dal", "q": 1}]})
        assert j(r).get("ok"), r.get_data(as_text=True)
        assert not w0(D(11)), "a Dinner edited into a Lunch kept its step"
        got = [(r["day"], r["step"]) for r in w0()]
        assert got == [(D(12), 0), (D(10), 1), (D(6), 2)], "steps after the edits: %r" % got
        return "retime, edit, move, delete (both ways), slot change: the step follows; steps 0,1,2 in day order"
    check("04 editing, re-timing, moving and deleting the Dinner follow through to the step", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        rid = w0(D(6))[0]["id"]
        vals = []
        for v in (1, 0, None):
            r = c.post("/api/ft/cramp", json={"id": rid, "cramp": v})
            assert j(r).get("ok"), r.get_data(as_text=True)
            vals.append(q("SELECT cramp FROM ft_log WHERE id=?", (rid,))[0]["cramp"])
        assert vals == [1, 0, None], "cramp stored as %r" % vals
        cu = j(c.get("/api/ft"))["current"]
        assert cu["slug"] == "w0" and cu["step"] == 3, "a blank cramp un-counted the day: %r" % cu
        r = c.post("/api/ft/undo/%d" % rid, json={})
        assert r.status_code == 409 and w0(D(6)), "Undo removed a day that comes from Meals"
        return "yes, no, blank all stored; the day still counts; Undo points him to Meals"
    check("05 the cramp question is yes, no or blank, and blank still counts", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        mig = os.path.join(os.path.dirname(app_path), "migrate_gutlog_v3340_week0.py")
        ms = importlib.util.spec_from_file_location("mig_v3340", mig)
        mm = importlib.util.module_from_spec(ms)
        ms.loader.exec_module(mm)
        for t in ("ft_log", "meals", "meal_meta", "edits"):
            q("DELETE FROM " + t)
        q("DELETE FROM settings WHERE key='ft_week0_from'")
        con = sqlite3.connect(gdb)
        ins = ("INSERT INTO meals(day, mtime, slot, items, protein, kcal, fibre, fscore, notes, created) "
               "VALUES(?,?,?,'[]',0,?,0,0,'','')")
        con.execute(ins, ("2026-09-22", "19:00", "Dinner", 450))       # before the plan: never counts
        for d in SEP:
            con.execute(ins, (d, "19:00", "Dinner", 450))
        con.execute(ins, ("2026-09-23", "21:55", "Dinner", 120))       # a split dinner is one dinner
        con.execute(ins, ("2026-09-25", "13:00", "Lunch", 600))
        con.commit()
        con.close()
        fake(True)
        try:
            dry = mm.run(gm, "2026-09-23", None, False, w)
            assert dry["to_add"] == SEP and not w0(), "dry run: %r, rows %d" % (dry["to_add"], len(w0()))
            res = mm.run(gm, "2026-09-23", None, True, w)
            again = mm.run(gm, "2026-09-23", None, True, w)
            st = j(c.get("/api/ft"))
        finally:
            fake(False)
        assert res["added"] == 7 and os.path.exists(res["backup"]), res
        got = [(r["day"], r["step"]) for r in w0()]
        assert got == [(d, i) for i, d in enumerate(SEP)], "Week 0 rows %r" % got
        first = q("SELECT id FROM meals WHERE day='2026-09-23' AND mtime='19:00'")[0]["id"]
        assert w0("2026-09-23")[0]["meal_id"] == first, "23-Sep is not linked to its first dinner"
        assert again["added"] == 0, "a second --apply added %r" % again["added"]
        cu = st["current"]
        assert cu["slug"] == "tchana" and cu["step"] == 0 and cu["g"] == 12 and cu["unit"] == "g dry", cu
        assert st["header"] == "Week 1 · Test chana 12 g dry", "header %r" % st["header"]
        return "dry run wrote nothing; 23..29-Sep linked (22-Sep not), backup taken, re-run adds 0; " \
               "on 30-Sep: Test chana 12 g dry"
    check("06 backfill: 23..29-Sep dinners, and on 30-Sep the next step is the 12 g dry dose", t06)

    # ------------------------------------------------ the browser checks
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def fresh():
        for t in ("ft_log", "ft_score", "meals", "meal_meta", "edits"):
            q("DELETE FROM " + t)
        q("INSERT OR REPLACE INTO settings(key, value) VALUES('ft_week0_from', ?)", (D(10),))
        for n in (3, 2, 1):
            tab_meal(D(n), "19:00", "Dinner")
        tab_meal(D(0), "00:01", "Breakfast")
        tab_meal(D(0), "00:02", "Lunch")

    def browse():
        if B or not have_pw:
            return
        B["done"] = True
        from werkzeug.serving import make_server
        srv = make_server("127.0.0.1", 0, gm.app)
        port = srv.server_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        base = "http://127.0.0.1:%d" % port
        with sync_playwright() as p:
            b = p.chromium.launch()
            errs = []

            def page(clock=None):
                ctx = b.new_context(viewport={"width": 300, "height": 760})
                pg = ctx.new_page()
                pg.set_default_timeout(6000)
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.on("dialog", lambda d: d.accept("Medicine A"))
                if clock:
                    pg.clock.set_fixed_time(clock)
                pg.goto(base + "/login")
                pg.fill("input[type=password]", "testpassword1")
                pg.keyboard.press("Enter")
                pg.wait_for_timeout(1200)
                return pg

            def tap(pg, sel):
                pg.wait_for_selector(sel, state="attached")
                pg.eval_on_selector(sel, "e=>{e.scrollIntoView({block:'center'});e.click();}")
                pg.wait_for_timeout(700)

            def step(name, fn):
                try:
                    fn()
                except Exception as exc:
                    B["err_" + name] = type(exc).__name__ + ": " + str(exc).split("\n")[0]

            def s_sep30():
                fake(True)
                try:
                    pg = page(datetime(2026, 9, 30, 8, 0, 0))
                    B["sep_head"] = pg.inner_text("#ftCard .fold-h")
                    tap(pg, "#ftCard .fold-h")
                    B["sep_body"] = pg.inner_text("#ftCard")
                    pg.context.close()
                finally:
                    fake(False)

            def s_order():
                fresh()
                pg = page()
                B["order"] = pg.evaluate(
                    "(()=>{const want=%s;return [...document.querySelectorAll('#tab-now [id]')]"
                    ".map(e=>e.id).filter(i=>want.indexOf(i)>=0);})()" % json.dumps(WANT_ORDER))
                B["folds"] = pg.evaluate(
                    "['nowMeal','ftCard'].map(i=>{const c=document.getElementById(i);"
                    "return [i,c.classList.contains('open'),getComputedStyle(c.querySelector('.cbody')).display];})")
                B["meal_head"] = pg.inner_text("#mealSum")
                B["ft_head"] = pg.inner_text("#ftSum")
                B["wide"] = pg.evaluate("document.documentElement.scrollWidth")
                B["head_fit"] = pg.evaluate(
                    "['#nowMeal','#ftCard'].every(s=>{const h=document.querySelector(s+' .fold-h');"
                    "return h.scrollWidth<=h.clientWidth+1;})")
                B["pg"] = pg

            def s_dinner():
                pg = B["pg"]
                tap(pg, "#nowMeal .fold-h")
                pg.click("#mealTabs >> text=Dinner")
                pg.wait_for_timeout(300)
                tap(pg, "#mcGo")
                pg.wait_for_timeout(900)
                B["din_meals"] = q("SELECT COUNT(*) AS n FROM meals WHERE day=? AND slot='Dinner'",
                                   (D(0),))[0]["n"]
                B["din_rows"] = [(r["step"], bool(r["meal_id"])) for r in w0(D(0))]
                B["din_head"] = pg.inner_text("#ftSum")
                tap(pg, "#ftCard .fold-h")
                B["din_card"] = pg.inner_text("#ftCard")
                B["din_inputs"] = pg.evaluate(
                    "(()=>{const c=document.getElementById('ftCard');return {taken:!!c.querySelector('#ftTaken'),"
                    "size:!!c.querySelector('.chips[data-k=size]'),cramp:c.querySelectorAll('#ftDinner .chip').length};})()")
                B["din_wide"] = pg.evaluate("document.documentElement.scrollWidth")
                pg.locator("#ftDinner .chip").filter(has_text="Yes").first.click()
                pg.wait_for_timeout(700)
                B["din_cramp"] = [r["cramp"] for r in w0(D(0))]

            def s_prn():
                pg = B["pg"]
                pg.evaluate("switchTab('meds')")
                pg.wait_for_timeout(400)
                tap(pg, "#prnAdd")
                pg.wait_for_timeout(700)
                B["prn"] = pg.evaluate(
                    "(()=>{const s=document.getElementById('meds-salts');return {tab:document.querySelector('.tab.sel').id,"
                    "salts:!!s&&s.classList.contains('sel')&&s.offsetParent!==null,"
                    "seg:(document.querySelector('.seg[data-seg=meds] button.sel')||{}).dataset.s};})()")
                B["prn_row"] = [r["name"] for r in q("SELECT name FROM prnmeds WHERE name='Medicine A'")]

            for name, fn in (("sep30", s_sep30), ("order", s_order), ("dinner", s_dinner), ("prn", s_prn)):
                step(name, fn)
            B["errs"] = errs
            b.close()
        srv.shutdown()

    def need():
        if not have_pw:
            return False
        browse()
        return True

    def G(k, part):
        if k not in B:
            raise AssertionError("the %s step did not get this far: %s" % (part, B.get("err_" + part, "?")))
        return B[k]

    def t07():
        if not need():
            return "SKIPPED: Playwright not installed here"
        h, body = G("sep_head", "sep30"), G("sep_body", "sep30")
        assert "Week 1 · Test chana 12 g dry" in h, "30-Sep header %r" % h
        assert "Week 1 · day 1 — Test chana 12 g dry" in body, "30-Sep card %r" % body
        return "with both clocks at 30-Sep 08:00 the card reads Week 1 · day 1 — Test chana 12 g dry"
    check("07 on 30-Sep the Now card shows the 12 g dry dose after the backfilled Week 0", t07)

    def t08():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert not B["errs"], "page errors: %s" % B["errs"]
        assert G("order", "order") == WANT_ORDER, "Now page order %r" % B["order"]
        assert G("folds", "order") == [["nowMeal", False, "none"], ["ftCard", False, "none"]], B["folds"]
        assert G("meal_head", "order") == "Breakfast ✓ · Lunch ✓ · Dinner —", "meals header %r" % B["meal_head"]
        assert G("ft_head", "order") == "Week 0 · day 4 — dinner not logged yet", "food test header %r" % B["ft_head"]
        assert B["wide"] <= 300 and B["head_fit"], "the page or a header runs past 300 px (%s)" % B["wide"]
        return "medicines, meals, symptoms, food test, the rest; both cards folded; summaries right at 300 px"
    check("08 the Now page order and folds at 300 px, with the summary lines", t08)

    def t09():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("din_meals", "dinner") == 1, "Dinner rows today: %s" % B["din_meals"]
        assert G("din_rows", "dinner") == [(3, True)], "Week 0 row today: %r" % B["din_rows"]
        assert B["din_head"] == "Week 0 · day 4 — dinner logged ✓", "header %r" % B["din_head"]
        card = B["din_card"]
        assert "Cramp after dinner?" in card and "dinner logged in Meals" in card, card
        assert G("din_inputs", "dinner") == {"taken": False, "size": False, "cramp": 2}, B["din_inputs"]
        assert B["din_wide"] <= 300, "the card runs past 300 px"
        assert G("din_cramp", "dinner") == [1], "the cramp tap stored %r" % B["din_cramp"]
        return "one Dinner from the card -> day 4 logged; the card asks only cramp; Yes stored"
    check("09 a Dinner on the Now meal card completes the day and the card asks only about cramp", t09)

    def t10():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("prn", "prn") == {"tab": "tab-meds", "salts": True, "seg": "salts"}, B["prn"]
        assert G("prn_row", "prn") == ["Medicine A"], "the medicine was not added"
        return "added, then Meds -> Salts is showing"
    check("10 PRN Add medicine goes on to Meds -> Salts", t10)

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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.32.0 -- the food test (GUTLOG_V3320_FOODTEST).

The engine -- which step is next, what a skip or a pause does, what a stop
records, what the results show and flag -- is checked through the API. The
card and the results page are driven in Chromium at the folded width,
because a server fetch of a JS-drawn card proves nothing about it.

Scratch database, a SYNTHETIC plan ("Test chana" and so on), "Medicine A".
His plan, foods and amounts are data and never appear here: this file is
tracked and the repository is public.

  python3 test_v3320_foodtest.py [path/to/gutlog/app.py]
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

PLAN = {"plan_id": None, "reminder": "Test reminder: change nothing else.",
        "items": [
            {"slug": "w0", "week": "Week 0", "label": "Dinner changes only", "kind": "dinner", "days": 7,
             "notes": ["Test note."]},
            {"slug": "tchana", "week": "Week 1", "label": "Test chana", "meal": "Breakfast", "basis": "dry",
             "steps": [{"g": 12, "cooked": "30 g"}, {"g": 25}, {"g": 40},
                       {"g": 60, "cond": "only if days 1-3 were clean"}],
             "washout": 3, "cooking": "Test cooking.",
             "library": {"name": "Test chana (dry)", "fdc": 173756, "cat": "B"}},
            {"slug": "tcauli", "week": "Week 3", "label": "Test cauli", "meal": "Lunch", "basis": "cooked",
             "steps": [{"g": 50}, {"g": 75}, {"g": 100}], "washout": 4},
            {"slug": "tsafed", "week": "Week 4", "label": "Test safed", "basis": "dry", "optional": True,
             "requires": "tchana", "steps": [{"g": 40}, {"g": 40}], "washout": None},
            {"slug": "trajma", "week": "Week 5", "label": "Test rajma", "basis": "dry", "optional": True,
             "steps": [{"g": 12}], "washout": None}],
        "safe_plate": [["Freely", "Test carrot"]], "safe_notes": [], "rules": ["Test rule."]}


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
    w = tempfile.mkdtemp(prefix="gutlog_ft_")
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=os.path.join(w, "none.json"),
                      GUTLOG_MIRROR_STAMP=os.path.join(w, "stamp.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3320", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
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

    def state():
        return j(c.get("/api/ft"))

    NOW = datetime.now().strftime("%H:%M")
    seeded = {}
    if hasattr(gm, "ft_seed"):
        with gm.app.app_context():
            seeded = gm.ft_seed(gm.db(), PLAN)

    # ---------------------------------------------------------------- 01
    def t01():
        s = state()
        assert s.get("seeded"), "the plan did not seed"
        assert s["current"]["slug"] == "w0" and s["current"]["kind"] == "dinner", s.get("current")
        with gm.app.app_context():
            st = gm.ft_steps([x for x in gm.ft_items() if x["slug"] == "tchana"][0])
            cl = gm.ft_steps([x for x in gm.ft_items() if x["slug"] == "tcauli"][0])
        doses = [(x["g"], x["unit"]) for x in st if x["kind"] == "dose"]
        assert doses == [(12, "g dry"), (25, "g dry"), (40, "g dry"), (60, "g dry")], doses
        assert st[3]["cond"], "the conditional step lost its condition"
        assert [x["kind"] for x in st].count("washout") == 3, "washout days wrong"
        assert all(x["unit"] == "g cooked" for x in cl if x["kind"] == "dose"), "cooked unit lost"
        lib = q("SELECT source, portion_qty, weighed_dry FROM library WHERE item='Test chana (dry)'")
        assert lib and lib[0]["source"] == "USDA" and lib[0]["weighed_dry"] == 1, \
            "the test food was not added to the library from the table: %r" % [tuple(x) for x in lib]
        return "ladders, units, the conditional step and washout seeded; food added as USDA"
    check("01 the plan seeds with its ladders and units", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        for i in range(7):
            r = c.post("/api/ft/log", json={"slug": "w0", "step": i, "day": D(20 - i), "time": "19:00",
                                            "size": "small", "cramp": i == 2})
            assert j(r).get("ok"), r.get_data(as_text=True)
        cu = state()["current"]
        assert cu["slug"] == "tchana" and cu["step"] == 0 and cu["g"] == 12, cu
        r = c.post("/api/ft/log", json={"slug": "tchana", "step": 0, "day": D(10), "time": "08:00"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        cu = state()["current"]
        assert cu["step"] == 1 and cu["g"] == 25 and "day 2" in cu["text"], cu
        return "after the seventh dinner: Week 1 day 1; after it is logged: day 2, 25 g dry"
    check("02 the next step follows what was logged", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        r = c.post("/api/ft/skip", json={"day": D(9), "reason": "Holiday"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        cu = state()["current"]
        assert cu["step"] == 1 and cu["g"] == 25, "a skipped day moved the plan on: %r" % cu
        return "a skipped day leaves the next step at 25 g"
    check("03 a skipped day does not move the plan on", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        c.post("/api/ft/pause", json={"reason": "Illness"})
        s = state()
        assert s["paused"] and s["paused"]["reason"] == "Illness", s.get("paused")
        assert s["current"]["step"] == 1, "a pause moved the plan"
        c.post("/api/ft/pause", json={"resume": True})
        s = state()
        assert not s["paused"] and s["current"]["step"] == 1, s
        return "paused and resumed; the next step never moved"
    check("04 a pause holds the plan where it was", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        r = c.post("/api/ft/log", json={"slug": "tchana", "step": 1, "day": D(8), "time": "07:30",
                                        "amount": 25})
        body = j(r)
        assert body.get("ok") and body.get("meal_id"), "no meal for the dose: %r" % body
        lid = body["id"]
        a8 = j(c.get("/api/nutrition/day/" + D(8)))
        assert a8["protein"] == 5.1, "25 g dry did not reach the day's protein: %r" % a8["protein"]
        r = c.post("/api/ft/retime", json={"table": "log", "id": lid, "day": D(7), "time": "08:15"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        b8 = j(c.get("/api/nutrition/day/" + D(8)))
        b7 = j(c.get("/api/nutrition/day/" + D(7)))
        assert not b8["logged"] and b7["protein"] == 5.1, \
            "the meal did not follow the step: %s %r, %s %r" % (D(8), b8["protein"], D(7), b7["protein"])
        assert [m["mtime"] for m in b7["rows"]] == ["08:15"], "the meal kept its old time"
        ed = q("SELECT tbl FROM edits WHERE new_day=? AND new_time='08:15'", (D(7),))
        assert sorted(e["tbl"] for e in ed) == ["ft_log", "meals"], [tuple(e) for e in ed]
        return "a past-day dose re-timed onto the next day took its meal and totals with it"
    check("05 a step logged on a past day and re-timed carries the day totals", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        r = c.post("/api/ft/score", json={"day": D(10), "time": "21:00", "pain": 1, "bloating": 0,
                                          "urgency": 0})
        assert r.status_code == 400, "a score without the stool type was saved"
        r = c.post("/api/ft/score", json={"day": D(10), "time": "21:00", "pain": 1, "bloating": 0,
                                          "urgency": 0, "bristol": 4})
        sid = j(r)["id"]
        got = j(c.get("/api/ft/score?day=" + D(10)))["score"]
        assert (got["pain"], got["bloating"], got["urgency"], got["bristol"]) == (1, 0, 0, 4), got
        r = c.post("/api/ft/score", json={"day": D(10), "time": "21:30", "pain": 2, "bloating": 1,
                                          "urgency": 0, "bristol": 5})
        got = j(c.get("/api/ft/score?day=" + D(10)))["score"]
        assert got["id"] == sid and got["pain"] == 2 and got["stime"] == "21:30", \
            "the edit made a second score instead of changing the first: %r" % got
        return "all four required; saved, re-opened and edited in place"
    check("06 the evening score needs all four and re-opens for editing", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        r = c.post("/api/ft/score", json={"day": D(7), "time": "21:00", "pain": 6, "bloating": 1,
                                          "urgency": 1, "bristol": 6, "clear": True})
        offer = j(r).get("offer_stop")
        assert offer and offer["slug"] == "tchana" and offer["limit_g"] == 25, \
            "a clear-symptom day did not offer to stop at 25 g: %r" % offer
        assert not q("SELECT 1 FROM ft_outcome WHERE slug='tchana'"), \
            "the stop was applied without asking"
        r = c.post("/api/ft/stop", json={"slug": "tchana", "day": D(7)})
        assert j(r).get("ok"), r.get_data(as_text=True)
        o = q("SELECT outcome, limit_g FROM ft_outcome WHERE slug='tchana'")[0]
        assert (o["outcome"], o["limit_g"]) == ("limit", 25), tuple(o)
        cu = state()["current"]
        assert cu["slug"] == "tchana" and cu["kind"] == "washout" and cu["n"] == 1, \
            "the ladder did not move to washout: %r" % cu
        r = c.post("/api/ft/score", json={"day": D(6), "time": "21:00", "pain": 2, "bloating": 0,
                                          "urgency": 0, "bristol": 4})
        assert j(r).get("washout"), "saving a washout day's score did not count the day"
        return "offered, not imposed; one tap -> limit at 25 g, then washout"
    check("07 clear symptoms offer the stop; taking it records the limit and washout", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        with gm.app.app_context():
            before = [b for b in gm.ft_results() if b["slug"] == "tchana"][0]["flags"]
        assert not [f for f in before if "medicine" in f], "a medicine flag with no change: %r" % before
        c.post("/api/prnmeds", json={"name": "Medicine A"})
        mid = q("SELECT id FROM prnmeds WHERE name='Medicine A'")[0]["id"]
        q("INSERT INTO med_schedule(med_id, slot, dose_text, valid_from, valid_to, created) "
          "VALUES(?, 'NIGHT', '1', ?, '', ?)", (mid, D(8), D(8)))
        with gm.app.app_context():
            after = [b for b in gm.ft_results() if b["slug"] == "tchana"][0]["flags"]
            cauli = [b for b in gm.ft_results() if b["slug"] == "tcauli"][0]["flags"]
        assert any("medicine change during this week" in f for f in after), after
        assert any("gap of 2 days" in f for f in after), "the 2-day gap is not flagged: %r" % after
        assert not cauli, "a week with no doses carries a flag: %r" % cauli
        h = c.get("/foodtest").get_data(as_text=True)
        assert "medicine change during this week" in h, "the flag is not on the results page"
        return "a schedule change inside the week and a 2-day gap are both flagged"
    check("08 a medicine change inside a challenge week raises the flag", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        with gm.app.app_context():
            b = [x for x in gm.ft_results() if x["slug"] == "tchana"][0]
        got = [(r["day"], r["role"], bool(r.get("score"))) for r in b["rows"]]
        want = [(D(10), "challenge", True), (D(7), "challenge", True), (D(6), "washout", True)]
        assert got == want, "results rows %r, wanted %r" % (got, want)
        h = c.get("/foodtest").get_data(as_text=True)
        assert "first washout day" in h and "Limit at 25 g" in h, "the results page misses them"
        return "two challenge days and the first washout day, each with its score"
    check("09 results show the challenge days plus the first washout day", t09)

    # ---------------------------------------------------------------- 10
    def t10():
        s = state()["current"]
        r = c.post("/api/ft/log", json={"slug": s["slug"], "step": s["step"] + 1, "day": D(5)})
        assert r.status_code == 409, "a step out of order was accepted"
        r = c.post("/api/ft/log", json={"slug": s["slug"], "step": s["step"], "day": D(6)})
        assert r.status_code == 409, "a second step on one day was accepted"
        return "only the next step, and one a day"
    check("10 only the next step can be logged, once a day", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        with gm.app.app_context():
            ids = [x["slug"] for x in gm.ft_items()]
        r = c.post("/api/ft/outcome", json={"slug": "tcauli", "outcome": "limit"})
        assert r.status_code == 400, "a limit without grams was accepted"
        r = c.post("/api/ft/outcome", json={"slug": "tchana", "outcome": "tolerated"})
        assert j(r).get("ok")
        r = c.post("/api/ft/outcome", json={"slug": "tchana", "outcome": "limit", "limit_g": 25})
        o = q("SELECT outcome, limit_g FROM ft_outcome WHERE slug='tchana'")[0]
        assert (o["outcome"], o["limit_g"]) == ("limit", 25), "the outcome could not be changed back"
        return "outcome set, changed, and a limit must say its grams (%d items)" % len(ids)
    check("11 an outcome is his to set and to change", t11)

    # ------------------------------------------------ the browser checks
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def fresh():
        """Start the plan over: the browser checks begin at Week 0, day 7."""
        for t in ("ft_log", "ft_score", "ft_outcome", "meals", "meal_meta", "med_schedule"):
            q("DELETE FROM " + t)
        q("DELETE FROM settings WHERE key='ft_paused'")
        for i in range(6):
            c.post("/api/ft/log", json={"slug": "w0", "step": i, "day": D(10 - i), "time": "19:00"})

    def browse():
        if B or not have_pw:
            return
        B["done"] = True
        fresh()
        from werkzeug.serving import make_server
        srv = make_server("127.0.0.1", 0, gm.app)
        port = srv.server_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        hh = NOW[:2]
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
                pg.wait_for_selector(sel, state="attached")
                pg.eval_on_selector(sel, "e=>{e.scrollIntoView({block:'center'});e.click();}")
                pg.wait_for_timeout(700)

            def card():
                return pg.inner_text("#nowFT")

            def wide():
                return pg.evaluate("document.documentElement.scrollWidth")

            def spill():
                # The page width alone is not enough: a line that runs past
                # the card's edge can be clipped rather than widen the page,
                # and the negative control caught exactly that. So: does
                # anything inside the card end beyond the card's own edge?
                return pg.evaluate(
                    "(()=>{const c=document.getElementById('ftCard');if(!c)return -1;"
                    "const r=c.getBoundingClientRect().right+1;"
                    "return [...c.querySelectorAll('*')].filter(e=>e.offsetParent!==null"
                    "&&e.getBoundingClientRect().right>r).length;})()")

            def home():
                pg.goto("http://127.0.0.1:%d/" % port)
                pg.wait_for_timeout(1100)

            def step(name, fn):
                try:
                    fn()
                except Exception as exc:
                    B["err_" + name] = type(exc).__name__ + ": " + str(exc).split("\n")[0]

            def s_logged():
                home()
                B["c_form"] = card()
                B["wide_card"] = wide()
                B["spill_form"] = spill()
                B["vis_time_inputs"] = pg.evaluate(
                    "[...document.querySelectorAll('#nowFT input[type=time]')]"
                    ".filter(e=>e.offsetParent!==null).length")
                tap("#nowFT .chips[data-k=size] .chip")
                tap("#ftTaken")
                B["c_logged"] = card()

            def s_skip():
                tap("#ftUndo")
                tap("#ftSkip")
                B["c_skipped"] = card()

            def s_pause():
                q("DELETE FROM ft_log WHERE kind='skip'")
                home()
                tap("#ftPause")
                B["c_paused"] = card()
                tap("#ftResume")
                B["c_resumed"] = card()

            def s_dose():
                c.post("/api/ft/log", json={"slug": "w0", "step": 6, "day": D(1), "time": "19:00"})
                home()
                B["c_dose"] = card()
                B["spill_dose"] = spill()
                pg.fill("#ftAmt", "10")
                pg.select_option("#nowFT select.tph", hh)
                pg.select_option("#nowFT select.tpm", "00")
                tap("#ftTaken")
                B["dose_row"] = [tuple(r) for r in q(
                    "SELECT amount, unit, day, ltime FROM ft_log WHERE kind='dose'")]
                B["dose_meal"] = [tuple(r) for r in q("SELECT day, mtime, protein FROM meals")]
                B["c_dose_done"] = card()

            def s_score():
                if pg.locator("#ftScoreBtn").count():
                    tap("#ftScoreBtn")
                for k, v in (("pain", "5"), ("bloating", "Yes"), ("urgency", "No"), ("bristol", "6"),
                             ("clear", "Yes")):
                    pg.locator("#ftScore .chips[data-k=%s] .chip" % k).filter(has_text=v).first.click()
                tap("#ftScoreSave")
                B["ask"] = pg.locator("#ftStopAsk").count()
                B["outcome_before_tap"] = [tuple(r) for r in q("SELECT outcome FROM ft_outcome")]
                tap("#ftStop")
                B["c_after_stop"] = card()
                B["outcome_after"] = [tuple(r) for r in q("SELECT outcome, limit_g FROM ft_outcome")]

            def s_results():
                q("INSERT INTO med_schedule(med_id, slot, dose_text, valid_from, valid_to, created) "
                  "VALUES(1, 'NIGHT', '1', ?, '', ?)", (D(0), D(0)))
                pg.goto("http://127.0.0.1:%d/foodtest" % port)
                pg.wait_for_timeout(900)
                B["res"] = pg.inner_text("main")
                B["wide_res"] = wide()

            for name, fn in (("logged", s_logged), ("skip", s_skip), ("pause", s_pause),
                             ("dose", s_dose), ("score", s_score), ("results", s_results)):
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
        if key not in B:
            raise AssertionError("the %s step did not get this far: %s"
                                 % (part, B.get("err_" + part, "no error recorded")))
        return B[key]

    def t12():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert not B["errs"], "page errors: %s" % B["errs"]
        f = G("c_form", "logged")
        assert "Week 0 · day 7" in f and "Test reminder" in f, "the card does not show the step: %r" % f
        assert G("vis_time_inputs", "logged") == 0, "a native time box is showing on the card"
        lg = G("c_logged", "logged")
        assert "Today:" in lg and "Next: Week 1 · day 1 — Test chana 12 g dry" in lg, \
            "after logging, the card shows %r" % lg
        return "day 7 -> logged -> next is Week 1 day 1, 12 g dry"
    check("12 the card shows the next step after a day is logged", t12)

    def t13():
        if not need():
            return "SKIPPED: Playwright not installed here"
        sk = G("c_skipped", "skip")
        assert "Skipped today" in sk and "Next is still: Week 0 · day 7" in sk, sk
        pa = G("c_paused", "pause")
        assert "Paused since" in pa and "Resume" in pa and "Taken" not in pa and "Save dinner" not in pa, pa
        assert "Save dinner" in G("c_resumed", "pause"), "resuming did not bring the step back"
        return "skipped: the step stays; paused: no step offered until Resume"
    check("13 the card holds the step after a skipped day and while paused", t13)

    def t14():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert "Week 1 · day 1 — Test chana 12 g dry" in G("c_dose", "dose"), B["c_dose"]
        row = G("dose_row", "dose")
        assert row == [(10.0, "g dry", D(0), NOW[:2] + ":00")], "the dose was saved as %r" % row
        assert G("dose_meal", "dose") and B["dose_meal"][0][2] == 2.0, \
            "10 g dry did not become a meal of 2.0 g protein: %r" % B["dose_meal"]
        return "amount changed to 10 g and the time chosen in the lists: both saved"
    check("14 the amount and time can be changed before Taken", t14)

    def t15():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("ask", "score") == 1, "saving a clear-symptom score did not ask to stop"
        assert G("outcome_before_tap", "score") == [], "the stop was recorded before he chose it"
        assert G("outcome_after", "score") == [("limit", 10.0)], B["outcome_after"]
        assert "washout day 1 of 3" in G("c_after_stop", "score"), B["c_after_stop"]
        return "clear -> asked -> one tap: limit at 10 g, washout next"
    check("15 the card offers the stop and records it only on a tap", t15)

    def t16():
        if not need():
            return "SKIPPED: Playwright not installed here"
        r = G("res", "results")
        assert "Test chana" in r and "10 g dry" in r and "first washout day" in r, r[:300]
        assert "medicine change during this week" in r, "the results page hides the flag"
        for k, part in (("wide_card", "logged"), ("wide_res", "results")):
            assert G(k, part) <= 300, "%s is %spx wide" % (k, B[k])
        for k, part in (("spill_form", "logged"), ("spill_dose", "dose")):
            assert G(k, part) == 0, "%d element(s) run past the card's edge (%s)" % (B[k], k)
        return "results list the dose, the washout day and the flag; card and page fit 300 px"
    check("16 the results page reads right and fits the folded screen", t16)

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

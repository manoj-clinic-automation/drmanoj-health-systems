#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.29.0 -- nutrition history and a Meals tab that answers to a date
(GUTLOG_V3290_NUTRITION).

The history page is server-rendered, so it is checked with a plain fetch.
The Meals tab is drawn by the app's own JavaScript, so the stepper is driven
in Chromium at the folded width -- a server fetch of that tab would prove
nothing about it, which is the whole v3.4.0 lesson.

Scratch database, real app, synthetic food. No real title or medicine name
appears here: this file is TRACKED and the repository is public.

  python3 test_v3290_nutrition.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta

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
    w = tempfile.mkdtemp(prefix="gutlog_nut_")
    plan = os.path.join(w, "plan.json")
    json.dump({"targets": {"protein": 100, "fibre": 30},
               "main_meals": ["Breakfast", "Lunch", "Dinner"]}, open(plan, "w"))
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=plan)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3290", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)

    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)

    T = date.today()
    D0 = T.isoformat()                          # today: 3 meals, a full day
    D1 = (T - timedelta(days=1)).isoformat()    # yesterday: 1 meal, partial
    D2 = (T - timedelta(days=2)).isoformat()    # nothing at all

    def meal(day, mtime, slot, n, p, k, f):
        r = c.post("/api/meals", json={"day": day, "mtime": mtime, "slot": slot,
                                       "notes": "",
                                       "items": [{"n": n, "q": 1, "p": p, "k": k,
                                                  "f": f, "fm": "L"}]})
        assert r.status_code == 200, "seeding %s failed: %s" % (day, r.status_code)

    # Today's three at 00:00: since v3.31.0 a meal cannot be logged later
    # than now today, and 13:00 / 20:00 fixtures failed every morning run.
    # Nothing here reads their order.
    meal(D0, "00:00", "Breakfast", "Test porridge", 12, 300, 6)
    meal(D0, "00:00", "Lunch", "Test dal", 20, 450, 9)
    meal(D0, "00:00", "Dinner", "Test khichdi", 18, 400, 7)
    meal(D1, "13:30", "Lunch", "Test soup", 9, 210, 4)

    # ---------------------------------------------------------------- 01
    def t01():
        h = c.get("/nutrition").get_data(as_text=True)
        assert "Traceback" not in h, "the page raised"
        want = gm.plan_dmy(D0)
        assert want in h, "today's date %r is not on the history page" % want
        assert "1150 kcal" in h, "today's kcal is not 1150 on the page"
        assert "50 of 100 g protein" in h, "today's protein line is wrong"
        assert "22 of 30 g fibre" in h, "today's fibre line is wrong"
        assert "3 meals" in h, "the meal count is not shown"
        return "1150 kcal, 50 of 100 g protein, 22 of 30 g fibre, 3 meals"
    check("01 history shows a logged day's kcal, protein and fibre", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        h = c.get("/nutrition").get_data(as_text=True)
        assert "not logged" in h, "a day with nothing on it is not labelled"
        assert gm.plan_dmy(D2) in h, "the empty day is missing from the list"
        # the empty day's row must carry no zero intake
        row = h.split('href="/?open=meals&amp;day=%s"' % D2)[1].split("</a>")[0]
        assert "not logged" in row, "the empty day's own row does not say so"
        assert "0 kcal" not in row, "the empty day is printed as 0 kcal"
        return "the day with nothing logged says so, and shows no zero"
    check("02 a day with no meals reads 'not logged', never zero", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        h = c.get("/nutrition").get_data(as_text=True)
        row = h.split('href="/?open=meals&amp;day=%s"' % D1)[1].split("</a>")[0]
        assert "partial" in row, "a 1-meal day against 3 usual is not marked partial"
        assert "210 kcal" in row, "the partial day's kcal is missing"
        full = h.split('href="/?open=meals&amp;day=%s"' % D0)[1].split("</a>")[0]
        assert "partial" not in full, "a full day was marked partial"
        return "1 of 3 usual meals is 'partial'; a full day is not"
    check("03 a thin day is labelled partial, a full day is not", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        hist = json.loads(c.get("/api/nutrition/history?days=14").get_data(as_text=True))
        byday = dict((d["day"], d) for d in hist["days"])
        for day in (D0, D1, D2):
            card = json.loads(c.get("/api/nutrition/day/" + day).get_data(as_text=True))
            h = byday[day]
            for k in ("kcal", "protein", "fibre", "meals", "logged", "partial"):
                assert card[k] == h[k], \
                    "%s disagrees on %s: card %r, history %r" % (day, k, card[k], h[k])
        return "card and history agree on kcal, protein, fibre, meals, logged, partial"
    check("04 the history total equals the day card total, day for day", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        h14 = c.get("/nutrition").get_data(as_text=True)
        assert h14.count('class="nrow') == 14, \
            "the default view drew %d rows, not 14" % h14.count('class="nrow')
        assert "Show 30 days" in h14, "there is no way to see 30 days"
        h30 = c.get("/nutrition?days=30").get_data(as_text=True)
        assert h30.count('class="nrow') == 30, \
            "30 days asked for, %d rows drawn" % h30.count('class="nrow')
        assert "Show 14 days" in h30, "no way back to 14 days"
        anon = gm.app.test_client()
        assert anon.get("/nutrition").status_code in (301, 302), "the page is not gated"
        return "14 by default, 30 on request, and logged out is bounced"
    check("05 fourteen days by default, thirty on request, login required", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        rows = json.loads(c.get("/api/nutrition/history?days=14").get_data(as_text=True))["days"]
        assert rows[0]["day"] == D0, "the list does not start with today"
        days = [r["day"] for r in rows]
        assert days == sorted(days, reverse=True), "the list is not newest first"
        return "newest first, starting today"
    check("06 the history is newest first", t06)

    # ------------------------------------------------ the browser checks
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def browse():
        if B or not have_pw:
            return
        from werkzeug.serving import make_server
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
            pg.wait_for_timeout(900)
            pg.click("#nav button[data-t=meals]")
            pg.wait_for_timeout(700)
            B["today_totals"] = pg.inner_text("#dayTotals")
            B["next_disabled_today"] = pg.eval_on_selector("#mlNext", "e=>e.disabled")
            B["hist_href"] = pg.eval_on_selector(".mlhist", "e=>e.getAttribute('href')")
            # step back one day
            pg.click("#mlPrev")
            pg.wait_for_timeout(600)
            B["prev_totals"] = pg.inner_text("#dayTotals")
            B["prev_head"] = pg.inner_text("#mlDayHead")
            B["prev_meals"] = pg.inner_text("#mlDayMeals")
            B["next_disabled_past"] = pg.eval_on_selector("#mlNext", "e=>e.disabled")
            B["wide"] = pg.evaluate("document.documentElement.scrollWidth")
            # and back two, to the day with nothing
            pg.click("#mlPrev")
            pg.wait_for_timeout(600)
            B["empty_totals"] = pg.inner_text("#dayTotals")
            # a row on the history page opens that day here
            pg.goto("http://127.0.0.1:%d/?open=meals&day=%s" % (port, D1))
            pg.wait_for_timeout(1100)
            B["deep_totals"] = pg.inner_text("#dayTotals")
            B["deep_wide"] = pg.evaluate("document.documentElement.scrollWidth")
            B["errs"] = errs
            b.close()
        srv.shutdown()

    def t07():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert not B["errs"], "page errors: %s" % B["errs"]
        assert "Today" in B["today_totals"], B["today_totals"]
        assert "50.0 g protein" in B["today_totals"], B["today_totals"]
        assert B["next_disabled_today"] is True, "the next-day arrow is live on today"
        return "today's card reads 50.0 g protein and the next arrow is disabled"
    check("07 the Meals tab opens on today with the next arrow disabled", t07)

    def t08():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        want = gm.plan_dmy(D1)
        assert want in B["prev_totals"], \
            "stepping back did not change the card: %r" % B["prev_totals"]
        assert "9.0 g protein" in B["prev_totals"], B["prev_totals"]
        assert "partial" in B["prev_totals"], "the past day is not marked partial"
        assert "Test soup" in B["prev_meals"], \
            "that day's meal is not listed: %r" % B["prev_meals"]
        for act in ("Edit", "Again", "Delete"):
            assert act in B["prev_meals"], "%s is missing on a past day" % act
        assert B["next_disabled_past"] is False, "the next arrow stayed disabled on a past day"
        return "the card, the list and the arrows all follow the chosen day"
    check("08 stepping back shows that day's totals and its meals", t08)

    def t09():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert "not logged" in B["empty_totals"], \
            "a day with nothing shows %r" % B["empty_totals"]
        assert "0.0 g protein" not in B["empty_totals"], "an empty day printed as zero"
        return "an empty day says 'not logged' on the card too"
    check("09 an empty day is not shown as zero intake on the card", t09)

    def t10():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert B["hist_href"] == "/nutrition", "the History button points at %r" % B["hist_href"]
        assert gm.plan_dmy(D1) in B["deep_totals"], \
            "opening ?open=meals&day= did not land on that day: %r" % B["deep_totals"]
        return "History links to /nutrition, and a day link lands on that day"
    check("10 History is reachable and a day link opens that day", t10)

    def t11():
        if not have_pw:
            return "SKIPPED: Playwright not installed here"
        browse()
        assert B["wide"] <= 300, "the Meals tab scrolls sideways (%dpx)" % B["wide"]
        assert B["deep_wide"] <= 300, "the deep-linked day scrolls sideways (%dpx)" % B["deep_wide"]
        return "stepper and History button fit the folded screen"
    check("11 the stepper fits 300 px", t11)

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

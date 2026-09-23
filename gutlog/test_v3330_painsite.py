#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.33.0 -- pain site, onset time, and one true-time day
(GUTLOG_V3330_PAINSITE).

The server half through the API; the grid, the "Started at" box and the
evening score driven in Chromium at 300 px, because they are drawn by the
page's own JavaScript. The Health Mirror is run against the same scratch
database, so it is checked on the columns this build actually creates.

Scratch database, synthetic plan and foods, "Medicine A". Region names are
anatomy, not medicine. Python 3.9.

  python3 test_v3330_painsite.py [path/to/gutlog/app.py]
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
        "items": [{"slug": "tchana", "week": "Week 1", "label": "Test chana", "meal": "Breakfast",
                   "basis": "dry", "steps": [{"g": 12}, {"g": 25}], "washout": 2}]}
WANT_ORDER = ["Right upper pain", "Epigastric pain", "Left upper pain", "Right flank pain",
              "Umbilical pain", "Left flank pain", "Right iliac pain", "Hypogastrium pain",
              "Left iliac pain", "Diffuse abdominal pain"]


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
    w = tempfile.mkdtemp(prefix="gutlog_pain_")
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=os.path.join(w, "none.json"),
                      GUTLOG_MIRROR_STAMP=os.path.join(w, "stamp.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3330", app_path)
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

    def dv(day):
        return j(c.get("/api/dayview?day=" + day))["entries"]

    def meal(day, mt, slot):
        r = c.post("/api/meals", json={"day": day, "mtime": mt, "slot": slot, "notes": "",
                                       "items": [{"n": "Test rice", "q": 1, "p": 2, "k": 100, "f": 1,
                                                  "fm": "L"}]})
        assert r.status_code == 200, r.get_data(as_text=True)

    def episode(day, et, etype, sev=5, cat="GI"):
        r = c.post("/api/episodes", json={"day": day, "etime": et, "category": cat, "etype": etype,
                                          "severity": sev})
        assert r.status_code == 200, r.get_data(as_text=True)

    if hasattr(gm, "ft_seed"):
        with gm.app.app_context():
            gm.ft_seed(gm.db(), PLAN)

    # ---------------------------------------------------------------- 01
    def t01():
        cols = [r[1] for r in q("PRAGMA table_info(ft_score)")]
        assert "pain_sites" in cols and "onset" in cols, "ft_score lacks the columns: %r" % cols
        sv = q("SELECT value FROM settings WHERE key='schema_version'")[0][0]
        assert sv == "3.3.6", "schema_version is %s" % sv
        h = c.get("/").get_data(as_text=True)
        assert "__ABD_SITES__" not in h and "const ABD_SITES=[[" in h, "the region list is not in the page"
        return "two columns, schema 3.3.6, the list written into the page"
    check("01 the migration adds the score columns and the page gets the list", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        meal(D(2), "18:30", "Dinner")
        episode(D(2), "18:10", "Left upper pain")
        episode(D(2), "19:40", "Epigastric pain")
        episode(D(3), "11:00", "Umbilical pain")
        e2 = dict((e["title"], e["sub"]) for e in dv(D(2)) if e["tbl"] == "episodes")
        assert "20 min before dinner" in e2["Left upper pain"], e2
        assert "1 h 10 min after dinner" in e2["Epigastric pain"], e2
        e3 = [e["sub"] for e in dv(D(3)) if e["tbl"] == "episodes"]
        assert "no meal logged within 4 h" in e3[0], e3
        return "18:10 -> 20 min before dinner; 19:40 -> 1 h 10 min after; no meal -> none within 4 h"
    check("02 each GI episode says where it fell against the nearest meal", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        episode(D(4), "09:00", "Left iliac pain", 6)
        episode(D(4), "09:00", "Hypogastrium pain", 3)
        titles = [e["title"] for e in dv(D(4)) if e["tbl"] == "episodes"]
        assert sorted(titles) == ["Hypogastrium pain", "Left iliac pain"], titles
        rv = j(c.get("/api/review?days=30"))["episodes"]
        assert {"Left iliac pain", "Hypogastrium pain"} <= set(e["etype"] for e in rv), \
            "the old labels are gone from review"
        # and the grid still SAVES them: the page's list carries both exactly
        h = c.get("/").get_data(as_text=True)
        m = h.split("const ABD_SITES=", 1)[1].split(";\n", 1)[0] if "const ABD_SITES=" in h else "[]"
        stored = [x[0] for x in json.loads(m)]
        assert "Left iliac pain" in stored and "Hypogastrium pain" in stored, \
            "the grid no longer stores the old labels: %r" % stored
        return "old labels saved by the grid and shown unchanged in Day by day and review"
    check("03 the two old labels still save and show as before", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        base = {"day": D(1), "time": "21:00", "bloating": 0, "urgency": 0, "bristol": 4}
        r = c.post("/api/ft/score", json=dict(base, pain=0, pain_sites=["Left upper pain"], onset="18:00"))
        s = j(c.get("/api/ft/score?day=" + D(1)))["score"]
        assert (s["pain_sites"], s["onset"]) == ("", ""), "sites kept with no pain: %r" % s
        r = c.post("/api/ft/score", json=dict(base, pain=4, pain_sites=["Left upper pain", "Epigastric pain",
                                                                         "not a region"], onset="18:05"))
        assert j(r).get("ok"), r.get_data(as_text=True)
        s = j(c.get("/api/ft/score?day=" + D(1)))["score"]
        assert s["pain_sites"] == "Epigastric pain|Left upper pain" and s["onset"] == "18:05", s
        r = c.post("/api/ft/score", json=dict(base, day=D(5), pain=3))
        assert j(r).get("ok"), "a score without site and onset was refused"
        return "no pain: none kept; pain 4: two regions (in grid order, unknown dropped) and 18:05"
    check("04 the evening score keeps site and onset only with pain, both optional", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        c.post("/api/ft/log", json={"slug": "tchana", "step": 0, "day": D(1), "time": "07:30"})
        meal(D(1), "12:30", "Lunch")
        c.post("/api/prnmeds", json={"name": "Medicine A"})
        mid = q("SELECT id FROM prnmeds WHERE name='Medicine A'")[0]["id"]
        c.post("/api/now/dose", json={"med_id": mid, "status": "EXTRA", "day": D(1), "dtime": "12:20"})
        episode(D(1), "12:10", "Left upper pain")
        got = [(e["time"], e["kind"]) for e in dv(D(1))]
        want = [("07:30", "Food test"), ("12:10", "Symptom"), ("12:20", "Extra"), ("12:30", "Meal"),
                ("18:05", "Pain onset"), ("21:00", "Score")]
        assert got == want, "the day reads %r" % got
        ons = [e for e in dv(D(1)) if e["kind"] == "Pain onset"][0]
        assert "Epigastrium" in ons["title"] and "Left upper" in ons["title"], ons
        return "Food test 07:30, symptom 12:10, dose 12:20, meal 12:30, onset 18:05, score 21:00"
    check("05 Day by day is one timeline in true time, Food Test included", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        e = dict((x["kind"], x) for x in dv(D(1)))
        for kind, t in (("Food test", "06:45"), ("Score", "21:40"), ("Pain onset", "17:50")):
            r = c.post("/api/retime", json={"table": e[kind]["tbl"], "id": e[kind]["id"], "day": D(1),
                                            "time": t})
            assert j(r).get("ok"), "%s: %s" % (kind, r.get_data(as_text=True))
        got = dict((x["kind"], x["time"]) for x in dv(D(1)))
        assert (got["Food test"], got["Score"], got["Pain onset"]) == ("06:45", "21:40", "17:50"), got
        r = c.post("/api/retime", json={"table": "ft_score_onset", "id": e["Pain onset"]["id"],
                                        "day": D(2), "time": "17:50"})
        assert r.status_code == 400, "the onset moved to another day"
        ed = sorted(x["tbl"] for x in q("SELECT tbl FROM edits WHERE tbl LIKE 'ft_%'"))
        assert ed == ["ft_log", "ft_score", "ft_score_onset"], "edits recorded: %r" % ed
        return "each new kind moved from Day by day, each move recorded; onset stays on its day"
    check("06 the Food Test rows and the onset retime from Day by day", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        h = c.get("/foodtest").get_data(as_text=True)
        assert "Onset vs meals" in h, "no onset summary"
        assert "Epigastrium, Left upper" in h and "started 17:50" in h, \
            "the result's score does not carry site and onset"
        assert "evening-score pain started 17:50" in h, "no meal relation under the result day"
        return "summary counts, site and onset beside the score, relation under the day"
    check("07 the Food Test page shows site, onset and the meal relation", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        mirror = os.path.join(os.path.dirname(os.path.dirname(app_path)), "ops", "health_mirror.py")
        if not os.path.exists(mirror):
            mirror = "/root/ops/health_mirror.py"
        os.environ.update(MIRROR_GUTLOG_DB=gdb, MIRROR_FITLOG_DB=os.path.join(w, "nofit.db"),
                          MIRROR_PLANS_DIR=os.path.join(w, "pl"), MIRROR_UPLOAD_DIR=os.path.join(w, "up"))
        ms = importlib.util.spec_from_file_location("hm_v3330", mirror)
        hm = importlib.util.module_from_spec(ms)
        ms.loader.exec_module(hm)
        out = os.path.join(w, "mirror")
        counts, md = hm.generate(out, days=30)
        csvt = open(os.path.join(out, "snapshot", "csv", "food_test_scores.csv"), encoding="utf-8").read()
        assert "pain_sites" in csvt.split("\n")[0] and "onset" in csvt.split("\n")[0], csvt[:120]
        assert "Epigastric pain|Left upper pain" in csvt and "17:50" in csvt, "the values are not in the CSV"
        assert "Epigastric pain, Left upper pain" in md and "17:50" in md, "not in the snapshot"
        return "snapshot and food_test_scores.csv carry pain_sites and onset"
    check("08 the Health Mirror carries site and onset", t08)

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
        q("DELETE FROM episodes WHERE day=?", (D(0),))
        q("DELETE FROM ft_score WHERE day=?", (D(0),))
        from werkzeug.serving import make_server
        srv = make_server("127.0.0.1", 0, gm.app)
        port = srv.server_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 300, "height": 760})
            pg.set_default_timeout(6000)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1000)

            def tap(sel):
                pg.wait_for_selector(sel, state="attached")
                pg.eval_on_selector(sel, "e=>{e.scrollIntoView({block:'center'});e.click();}")
                pg.wait_for_timeout(300)

            def step(name, fn):
                try:
                    fn()
                except Exception as exc:
                    B["err_" + name] = type(exc).__name__ + ": " + str(exc).split("\n")[0]

            def s_grid():
                tap("#nowSym .fold-h")
                B["tiles"] = pg.eval_on_selector_all("#n_painSites .abdt", "l=>l.map(e=>e.dataset.site)")
                B["head"] = pg.inner_text("#n_painSites .abdhd")
                B["wide"] = pg.evaluate("document.documentElement.scrollWidth")
                B["diffuse_full"] = pg.evaluate(
                    "(()=>{const t=[...document.querySelectorAll('#n_painSites .abdt')];"
                    "return Math.round(t[9].getBoundingClientRect().width)>="
                    "Math.round(t[0].getBoundingClientRect().width*2.5);})()")

            def s_save():
                tap("#n_painSites .abdt[data-site='Left upper pain']")
                tap("#n_painSites .abdrow[data-site='Left upper pain'] .chip:nth-child(5)")
                tap("#n_painSites .abdt[data-site='Epigastric pain']")
                tap("#n_painSites .abdrow[data-site='Epigastric pain'] .chip:nth-child(3)")
                B["rows_below"] = pg.evaluate(
                    "(()=>{const g=document.querySelector('#n_painSites .abdgrid').getBoundingClientRect();"
                    "return [...document.querySelectorAll('#n_painSites .abdrow')].every(r=>"
                    "r.getBoundingClientRect().top>=g.bottom-1);})()")
                tap("#n_symAgo .chip:nth-child(3)")          # 30 min ago
                B["ago"] = pg.evaluate("document.getElementById('n_symStart').value")
                B["ago_at"] = datetime.now()
                pg.select_option("#nowSym .nstart select.tph", "00")
                pg.select_option("#nowSym .nstart select.tpm", "05")
                tap("#n_symSave")
                pg.wait_for_timeout(700)
                B["saved"] = sorted(tuple(r) for r in q(
                    "SELECT etype, severity, etime FROM episodes WHERE day=?", (D(0),)))

            def s_score():
                c.post("/api/ft/pause", json={"resume": True})
                pg.goto("http://127.0.0.1:%d/" % port)
                pg.wait_for_timeout(1100)
                if pg.locator("#ftScoreBtn").count():
                    tap("#ftScoreBtn")
                B["px_nopain"] = pg.evaluate("(()=>{const x=document.getElementById('ftPainX');"
                                             "return x?getComputedStyle(x).display:'missing';})()")
                pg.locator("#ftScore .chips[data-k=pain] .chip").filter(has_text="4").first.click()
                B["px_pain"] = pg.evaluate("getComputedStyle(document.getElementById('ftPainX')).display")
                pg.locator("#ftPainX .abdt[data-site='Right iliac pain']").click()
                pg.select_option("#ftPainX select.tph", "00")
                pg.select_option("#ftPainX select.tpm", "02")
                for k, v in (("bloating", "No"), ("urgency", "No"), ("bristol", "4"), ("clear", "No")):
                    pg.locator("#ftScore .chips[data-k=%s] .chip" % k).filter(has_text=v).first.click()
                B["wide_score"] = pg.evaluate("document.documentElement.scrollWidth")
                tap("#ftScoreSave")
                pg.wait_for_timeout(700)
                B["score_row"] = [tuple(r) for r in q(
                    "SELECT pain, pain_sites, onset FROM ft_score WHERE day=?", (D(0),))]
                B["score_line"] = pg.inner_text("#ftScore")
                tap("#ftScoreEdit")
                pg.wait_for_timeout(500)
                B["reopen"] = pg.evaluate(
                    "(()=>({sel:[...document.querySelectorAll('#ftPainX .abdt.sel')].map(e=>e.dataset.site),"
                    "onset:(document.getElementById('ftOnset')||{}).value}))()")

            for name, fn in (("grid", s_grid), ("save", s_save), ("score", s_score)):
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

    def t09():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert not B["errs"], "page errors: %s" % B["errs"]
        assert G("tiles", "grid") == WANT_ORDER, "tiles out of anatomical order: %r" % B["tiles"]
        assert "your right" in B["head"] and "your left" in B["head"], B["head"]
        assert B["wide"] <= 300, "the grid scrolls sideways (%spx)" % B["wide"]
        assert B["diffuse_full"], "the diffuse tile is not full width"
        return "10 tiles, right-to-left as seen facing him, diffuse full width, fits 300 px"
    check("09 the region grid renders in anatomical order at 300 px", t09)

    def t10():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("rows_below", "save"), "a score row is not below the grid"
        got = G("saved", "save")
        assert got == [("Epigastric pain", 3, "00:05"), ("Left upper pain", 5, "00:05")], \
            "saved %r" % got
        return "two regions, two episodes, both at the chosen 00:05 -- not the save time"
    check("10 scoring two regions saves two episodes at the chosen onset", t10)

    def t11():
        if not need():
            return "SKIPPED: Playwright not installed here"
        want = (G("ago_at", "save") - timedelta(minutes=30)).strftime("%H:%M")
        near = (B["ago_at"] - timedelta(minutes=31)).strftime("%H:%M")
        assert B["ago"] in (want, near), "30 min ago set %s, expected %s" % (B["ago"], want)
        return "the chip set %s" % B["ago"]
    check("11 '30 min ago' sets the picker to now minus 30 minutes", t11)

    def t12():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("px_nopain", "score") == "none", "the grid shows with no pain: %s" % B["px_nopain"]
        assert B["px_pain"] != "none", "the grid does not show with pain 4"
        assert G("score_row", "score") == [(4, "Right iliac pain", "00:02")], B["score_row"]
        assert "Right iliac" in B["score_line"] and "started 00:02" in B["score_line"], B["score_line"]
        assert G("reopen", "score") == {"sel": ["Right iliac pain"], "onset": "00:02"}, B["reopen"]
        assert B["wide_score"] <= 300, "the score form scrolls sideways (%spx)" % B["wide_score"]
        return "hidden at 0, shown at 4, saved, and re-opened with the region and 00:02"
    check("12 the evening score takes site and onset only with pain, and re-opens with them", t12)

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

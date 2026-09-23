#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.12.0 Phase I -- server-side functional test.

The pain entry surface, the "eased" tap, the operating day, and the feed
additions RxGuard needs. Builds its own scratch database; never touches the
live one, and GUTLOG_LINKS=0 so no outward call is made. Python 3.9.

Every case here ENTERS a v3.12.0 code path and fails on v3.11.0 (CLAUDE.md
rule 2: a green suite is only evidence for the code it actually executes).
The cross-app half -- GutLog writing FitLog's analgesic_log -- is proved in
fitlog/test_analgesic_mirror.py, which runs both applications for real.

  python3 test_phase_i.py [path/to/app.py]
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def load_app(app_path, workdir):
    os.environ["GUTLOG_DB"] = os.path.join(workdir, "t.db")
    os.environ["GUTLOG_UPLOADS"] = os.path.join(workdir, "up")
    os.environ["GUTLOG_INSECURE"] = "1"
    os.environ["GUTLOG_SECRET"] = "test-secret-not-real"
    os.environ["GUTLOG_ICONS"] = os.path.dirname(os.path.abspath(app_path))
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = os.path.join(workdir, "feed.token")
    os.environ["GUTLOG_LINKS"] = "0"
    sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
    spec = importlib.util.spec_from_file_location("gutlog_app_i", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def js_balanced(block):
    """Balanced (), [], {} in a JS block, ignoring quoted strings and //
    comments. Not a parser -- enough to catch a stray brace in generated code
    that would otherwise only show up in a browser."""
    depth = {"(": 0, "[": 0, "{": 0}
    close = {")": "(", "]": "[", "}": "{"}
    i, n = 0, len(block)
    quote = None
    while i < n:
        c = block[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
        elif c == "/" and i + 1 < n and block[i + 1] == "/":
            while i < n and block[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and block[i + 1] == "*":
            j = block.find("*/", i + 2)
            i = (j + 2) if j >= 0 else n
            continue
        elif c in depth:
            depth[c] += 1
        elif c in close:
            depth[close[c]] -= 1
            if depth[close[c]] < 0:
                return False, "closed too many " + c
        i += 1
    if quote:
        return False, "unterminated " + quote
    bad = [k for k in depth if depth[k]]
    return (not bad), ("unbalanced " + ", ".join(bad) if bad else "")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "app.py")
    work = tempfile.mkdtemp()
    mod = load_app(app_path, work)
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)
    dbp = os.environ["GUTLOG_DB"]
    src = open(app_path, "r", encoding="utf-8", newline="").read()

    def q(sql, a=()):
        con = sqlite3.connect(dbp)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    # Synthetic analgesic chips. The real ones are medicine names and live in
    # regimen.local.json (this repository is public), so the suite supplies its
    # own rather than depending on the owner's file or on his medicines.
    # Guarded so that an older app.py fails each case with its own message
    # rather than dying here: "0/18, and here is why" is evidence, a traceback
    # before the first test is not.
    mod.PAIN_ANALGESICS = {"Testamol 500": ("testamol", "Testamol 500"),
                           "Testcoxib 60": ("testcoxib", "Testcoxib 60")}
    mod.PAIN_TREATMENTS = list(getattr(mod, "PAIN_TREATMENTS_BASE", [])) \
        + list(mod.PAIN_ANALGESICS)
    c.get("/api/now")
    for nm, mol in (("Test Analg A", "testamol"), ("Test Analg B", "testcoxib")):
        q("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,9,?,1)", (nm, mol))

    T = date.today()
    TODAY = T.isoformat()
    Y1, Y8, Y20 = [(T - timedelta(days=n)).isoformat() for n in (1, 8, 20)]
    ctx = {}

    # Every time this suite writes to TODAY is derived from the clock, never
    # written down. GutLog refuses a time that has not come yet, so a literal
    # "08:00" turns the suite red at 03:48 and green at 18:00 -- and 5am is
    # exactly when this diary gets used. Clamped to midnight so the small
    # hours stay inside the day.
    NOW_DT = datetime.now()
    MIDNIGHT = NOW_DT.replace(hour=0, minute=0, second=0, microsecond=0)

    def ago(mins):
        """A real HH:MM earlier today, at most `mins` minutes back."""
        t = NOW_DT - timedelta(minutes=mins)
        return (t if t >= MIDNIGHT else MIDNIGHT).strftime("%H:%M")

    def minutes_since(hhmm):
        h, m = [int(x) for x in hhmm.split(":")]
        return int(round((datetime.now() - MIDNIGHT.replace(
            hour=h, minute=m)).total_seconds() / 60.0))

    # ---------------------------------------------------------------- schema
    def t00_migration():
        c.get("/api/now")          # _migrate() runs per request, not at startup
        cols = [r[1] for r in q("PRAGMA table_info(episodes)")]
        assert "treatments" in cols, "episodes.treatments missing: " + str(cols)
        assert "radiates" in cols, "episodes.radiates missing: " + str(cols)
        sv = q("SELECT value FROM settings WHERE key='schema_version'")
        assert sv and sv[0][0] == mod.SCHEMA_VERSION, \
            "schema_version not stamped: " + str(sv)
        return "schema_version " + sv[0][0] + ", both columns present"

    def t01_nine_tiles_one_row_each():
        want = ["hip_thigh_both", "hip_thigh_r", "hip_thigh_l", "glute_both",
                "glute_r", "glute_l", "low_back", "neck_arm_r", "neck_arm_l"]
        got = [s[0] for s in mod.PAIN_SITES_MSK]
        assert got == want, "tile order changed: " + str(got)
        j = c.get("/api/pain").get_json()
        assert [s["slug"] for s in j["sites"]] == want
        assert j["sites"][0]["label"].startswith("Both hips"), "hero tile is not first"
        return "9 tiles, hero first"

    def t02_row_shape_per_tile():
        """Side must never be averaged away: right is the THR side."""
        for slug, label, side, canrad in mod.PAIN_SITES_MSK:
            r = c.post("/api/pain", json={"site": slug, "score": 4, "etime": "09:00",
                                          "day": Y1})
            assert r.status_code == 200, slug + ": " + r.get_data(as_text=True)
        rows = q("SELECT etype, side, category, severity, duration FROM episodes "
                 "WHERE day=? ORDER BY id", (Y1,))
        assert len(rows) == 9, "expected 9 rows, got " + str(len(rows))
        for (slug, label, side, canrad), row in zip(mod.PAIN_SITES_MSK, rows):
            assert row[0] == slug, "etype " + str(row[0]) + " != " + slug
            assert row[1] == side, slug + " side " + str(row[1]) + " != " + side
            assert row[2] == "pain", slug + " category " + str(row[2])
            assert row[3] == 4, slug + " severity " + str(row[3])
            assert row[4] == "", slug + " duration prefilled: " + repr(row[4])
        sides = sorted(set(r[1] for r in rows))
        assert sides == ["", "L", "R", "both"], str(sides)
        return "9 rows in `episodes`, sides L/R/both kept apart"

    def t03_radiates_only_where_asked():
        c.post("/api/pain", json={"site": "glute_r", "score": 6, "radiates": 1,
                                  "day": Y8, "etime": "10:00"})
        c.post("/api/pain", json={"site": "low_back", "score": 6, "radiates": 1,
                                  "day": Y8, "etime": "10:01"})
        c.post("/api/pain", json={"site": "neck_arm_l", "score": 6, "radiates": 1,
                                  "day": Y8, "etime": "10:02"})
        rows = dict((r[0], r[1]) for r in
                    q("SELECT etype, radiates FROM episodes WHERE day=?", (Y8,)))
        assert rows["glute_r"] == 1, "glute tile lost the below-the-knee tap"
        assert rows["low_back"] == 0, "low back must not carry radiates"
        assert rows["neck_arm_l"] == 0, "neck tile must not carry radiates"
        return "below-the-knee kept on glute/hip tiles only"

    def t04_treatments_pipe_joined():
        assert "Heat pad" in mod.PAIN_TREATMENTS_BASE, str(mod.PAIN_TREATMENTS_BASE)
        r = c.post("/api/pain", json={"site": "hip_thigh_both", "score": 7,
                                      "treatments": ["Heat pad", "Rest", "Voodoo"],
                                      "day": TODAY, "etime": ago(7)})
        assert r.status_code == 200, r.get_data(as_text=True)
        ctx["eid"] = r.get_json()["id"]
        row = q("SELECT treatments FROM episodes WHERE id=?", (ctx["eid"],))[0][0]
        assert row == "Heat pad|Rest", "treatments: " + repr(row)
        assert q("SELECT COUNT(*) FROM doses")[0][0] == 0, \
            "a non-medicine chip wrote a dose row"
        return "pipe-joined like days.syms; unknown chip dropped; no dose row"

    def t05_analgesic_writes_exactly_one_dose():
        before = q("SELECT COUNT(*) FROM doses")[0][0]
        at = ago(6)
        r = c.post("/api/pain", json={"site": "glute_both", "score": 8,
                                      "treatments": ["Heat pad", "Testamol 500",
                                                     "Testcoxib 60"],
                                      "day": TODAY, "etime": at})
        j = r.get_json()
        assert j["ok"], str(j)
        rows = q("SELECT medicine, reason, status, med_id, day, dtime FROM doses "
                 "ORDER BY id")
        assert len(rows) == before + 2, \
            "expected exactly 2 new dose rows, got " + str(len(rows) - before)
        names = sorted(r[0] for r in rows)
        assert names == ["Test Analg A", "Test Analg B"], str(names)
        for row in rows:
            assert row[1] == "Glutes - both", "reason is not the pain site: " + repr(row[1])
            assert row[2] == "EXTRA", "status " + repr(row[2])
            assert row[3] is not None, "dose not tied to the prnmeds row: " + repr(row)
            assert row[4] == TODAY and row[5] == at, \
                "the dose did not take the tile's time: " + str(row)
        assert q("SELECT COUNT(*) FROM episodes WHERE category='pain' "
                 "AND etype='glute_both' AND day=?", (TODAY,))[0][0] == 1, \
            "more than one episode row for one tap"
        return "2 chips -> 2 real doses rows, reason = the site, one episode row"

    def t06_unknown_medicine_still_recorded():
        q("UPDATE prnmeds SET active=0 WHERE molecule='testamol'")
        before = q("SELECT COUNT(*) FROM doses")[0][0]
        # v3.21.0 (GUTLOG_V3210_ONEDOSE): a chip within 6 h of a dose of the
        # same molecule is linked to it, not written -- and t05 logged one a
        # minute ago. Yesterday at noon has no prior dose, so this still tests
        # what it always tested: no matching medicine, still recorded.
        y = (date.today() - timedelta(days=1)).isoformat()
        c.post("/api/pain", json={"site": "low_back", "score": 3,
                                  "treatments": ["Testamol 500"],
                                  "day": y, "etime": "12:00"})
        rows = q("SELECT medicine, med_id FROM doses ORDER BY id DESC LIMIT 1")
        assert q("SELECT COUNT(*) FROM doses")[0][0] == before + 1, "dose lost"
        assert rows[0][0] == "Testamol 500" and rows[0][1] is None, str(rows)
        q("UPDATE prnmeds SET active=1 WHERE molecule='testamol'")
        return "no matching medicine: the dose is still recorded, under its label"

    def t07_guards():
        bad = [({"site": "nonsense", "score": 3}, "site"),
               ({"site": "low_back"}, "no score"),
               ({"site": "low_back", "score": 11}, "score 11"),
               ({"site": "low_back", "score": -1}, "score -1"),
               ({"site": "low_back", "score": 3, "day": (T + timedelta(days=1)).isoformat()},
                "tomorrow"),
               ({"site": "low_back", "score": 3, "etime": "99:99"}, "bad time")]
        for payload, why in bad:
            r = c.post("/api/pain", json=payload)
            assert r.status_code == 400, why + " was accepted: " + r.get_data(as_text=True)
        return "6 bad payloads refused"

    # ------------------------------------------------------------- eased tap
    def t08_eased_stamps_duration():
        """The elapsed time, measured from etime to now. Clock-independent:
        the start is as far back as the day allows, and the expectation is
        computed from it rather than written down."""
        start = ago(95)
        want = minutes_since(start)
        r = c.post("/api/pain", json={"site": "hip_thigh_r", "score": 5,
                                      "day": TODAY, "etime": start})
        eid = r.get_json()["id"]
        assert q("SELECT duration FROM episodes WHERE id=?", (eid,))[0][0] == "", \
            "duration was prefilled; nothing may be asked at the moment of pain"
        j = c.post("/api/episode/eased/" + str(eid), json={}).get_json()
        assert j["ok"], str(j)
        assert abs(j["minutes"] - want) <= 1, \
            "elapsed " + str(j["minutes"]) + ", expected about " + str(want)
        stored = q("SELECT duration FROM episodes WHERE id=?", (eid,))[0][0]
        assert stored == j["duration"], repr(stored)
        ctx["eased_id"] = eid
        return str(want) + " min from " + start + " to now -> " + j["duration"]

    def t09_duration_reads_as_written():
        """The arithmetic itself, on fixed inputs -- the one part of this that
        must not move with the clock. An hour boundary is where a duration
        silently starts reading wrong."""
        want = [(0, "0 min"), (1, "1 min"), (45, "45 min"), (59, "59 min"),
                (60, "1 h 00 min"), (61, "1 h 01 min"), (95, "1 h 35 min"),
                (125, "2 h 05 min"), (600, "10 h 00 min")]
        got = [(m, mod._dur_text(m)) for m, _ in want]
        assert got == want, "duration formatting: " + str(
            [g for g, w in zip(got, want) if g != w])
        short = ago(12)
        r = c.post("/api/pain", json={"site": "glute_l", "score": 2,
                                      "day": TODAY, "etime": short})
        j = c.post("/api/episode/eased/" + str(r.get_json()["id"]), json={}).get_json()
        if minutes_since(short) < 60:
            assert j["duration"].endswith(" min") and " h " not in j["duration"], \
                j["duration"]
        assert c.post("/api/episode/eased/999999", json={}).status_code == 404
        return "9 fixed durations exact, incl. both sides of the hour; 404 on an unknown id"

    def t10_pain_list_and_dayview():
        j = c.get("/api/pain?day=" + TODAY).get_json()
        assert j["rows"], "today's pain rows not returned"
        assert any(r["id"] == ctx["eased_id"] and r["duration"] for r in j["rows"]), \
            "eased row has no duration in the list"
        assert any(not r["duration"] for r in j["rows"]), "nothing left to ease"
        assert all(r["label"] for r in j["rows"]), "a row has no readable label"
        dv = c.get("/api/dayview?day=" + TODAY).get_json()
        pains = [e for e in dv["entries"] if e["kind"] == "Pain"]
        assert pains, "no Pain entries in the day view: " + \
            str(sorted(set(e["kind"] for e in dv["entries"])))
        assert all(e["tbl"] == "episodes" for e in pains), "pain is not in `episodes`"
        eased = [e for e in pains if e.get("eased")]
        assert eased, "no eased flag on the day view row"
        assert any("eased after" in (e["sub"] or "") for e in eased), str(eased[:1])
        assert any("below the knee" in (e["sub"] or "")
                   for e in c.get("/api/dayview?day=" + Y8).get_json()["entries"]), \
            "radiates not shown in the day view"
        return str(len(pains)) + " Pain rows in the day view, from `episodes`"

    # ------------------------------------------------------- operating day
    def t11_ot_day_logged_in_hours_stored_in_minutes():
        assert "ot_day" in mod.ACT_KINDS, "ACT_KINDS has no ot_day"
        assert "ot_day" in mod.LOAD_KINDS, "ot_day is not a load kind"
        r = c.post("/api/activity", json={"kind": "ot_day", "minutes": 480,
                                          "day": TODAY, "atime": ago(4)})
        assert r.status_code == 200, r.get_data(as_text=True)
        r = c.post("/api/activity", json={"kind": "walk", "minutes": 30,
                                          "day": TODAY, "atime": ago(3)})
        assert r.status_code == 200, r.get_data(as_text=True)
        row = q("SELECT kind, minutes FROM activities WHERE kind='ot_day'")
        assert row and float(row[0][1]) == 480.0, str(row)
        return "8 h stored as 480 minutes, like every other activity"

    def t12_ot_day_is_not_exercise():
        j = c.get("/api/activity?day=" + TODAY).get_json()
        s = j["summary"]
        assert s["minutes"] == 30, \
            "operating hours counted as exercise minutes: " + str(s["minutes"])
        assert s["load_minutes"] == 480, "load minutes " + str(s.get("load_minutes"))
        ot = [i for i in j["items"] if i["kind"] == "ot_day"]
        assert ot and ot[0]["load"] is True, "ot_day not flagged as load: " + str(ot)
        walk = [i for i in j["items"] if i["kind"] == "walk"]
        assert walk and walk[0]["load"] is False, str(walk)
        return "exercise 30 min, load 480 min, counted apart"

    def t13_ot_day_reaches_fitlog_and_the_day_view():
        tok = open(os.environ["GUTLOG_FEED_TOKEN_FILE"]).read().strip()
        f = c.get("/api/feed/activities?since=" + TODAY,
                  headers={"Authorization": "Bearer " + tok}).get_json()
        kinds = [a["kind"] for a in f["activities"]]
        assert "ot_day" in kinds, "ot_day not on the activity feed: " + str(kinds)
        dv = c.get("/api/dayview?day=" + TODAY).get_json()
        load = [e for e in dv["entries"] if e["kind"] == "Load"]
        assert load, "no Load row in the day view"
        assert "8.0 h" in load[0]["title"], load[0]["title"]
        assert "not exercise" in load[0]["sub"], load[0]["sub"]
        return "on /api/feed/activities unchanged; shown as Load, 8.0 h"

    # -------------------------------------------------- feed for RxGuard
    def t14_feed_carries_valid_from_and_ended():
        meds = c.get("/api/prnmeds/full").get_json()
        keep, drop = meds[0]["id"], meds[1]["id"]
        c.post("/api/schedule", json={"med_id": keep, "slot": "MORNING",
                                      "valid_from": Y20, "dose_text": "1 tab"})
        r = c.post("/api/schedule", json={"med_id": drop, "slot": "EVENING",
                                          "valid_from": Y20, "dose_text": "1 tab"})
        sid = [s for s in c.get("/api/schedule").get_json()["rows"]
               if s["med_id"] == drop][0]["id"]
        c.post("/api/schedule/close/" + str(sid), json={})
        # closing stamps valid_to with today; back-date it to the 8th day so it
        # reads as a schedule ended a week ago, the case this exists for
        q("UPDATE med_schedule SET valid_to=? WHERE id=?", (Y8, sid))
        tok = open(os.environ["GUTLOG_FEED_TOKEN_FILE"]).read().strip()
        j = c.get("/api/feed/stack?days=14",
                  headers={"Authorization": "Bearer " + tok}).get_json()
        reg = [x for x in j["regimen"] if x["med_id"] == keep]
        assert reg and reg[0].get("valid_from") == Y20, \
            "regimen line carries no valid_from: " + str(reg[:1])
        assert not [x for x in j["regimen"] if x["med_id"] == drop], \
            "an ended schedule is still in `regimen`"
        ended = [x for x in (j.get("ended") or []) if x["med_id"] == drop]
        assert ended, "ended schedules missing from the feed: " + str(j.get("ended"))
        assert ended[0]["valid_to"] == Y8, str(ended[0])
        return "regimen + valid_from, and the ended schedule with valid_to " + Y8

    # ------------------------------------------------------- house rules
    def t15_python_39_syntax():
        """The 2026-08-02 lesson: v1.0 shipped with PEP 701 f-strings that only
        3.12 accepts. Server runs 3.9.25. So: the file must parse as 3.9, no
        f-string may carry a backslash or a nested same-type quote inside its
        braces, and the new block avoids f-strings altogether."""
        tree = ast.parse(src, feature_version=(3, 9))
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            seg = ast.get_source_segment(src, node) or ""
            m = re.match(r"[A-Za-z]*(\"\"\"|'''|\"|')", seg)
            if not m:
                continue
            qc = m.group(1)[0]
            inner = seg[len(m.group(0)):]
            for expr in re.findall(r"\{([^{}]*)\}", inner):
                if "\\" in expr or qc in expr:
                    bad.append(seg[:60])
        assert not bad, "PEP 701 f-string, 3.12-only: " + str(bad[:2])
        blk = src.split("GUTLOG_V3120_PAIN -- musculoskeletal pain", 1)[1]
        blk = blk.split("# ------------------------------------------------------------------ records", 1)[0]
        new_f = re.findall(r"(?<![A-Za-z0-9_])[fF][\"']", blk)
        assert not new_f, "the new block uses f-strings: " + str(new_f[:3])
        # rule 5d: the analgesic chips are medicine names, so they are read
        # from regimen.local.json, never written into this public file
        assert '_local_seed("pain_analgesics")' in src, \
            "the analgesic chips are not coming from regimen.local.json"
        return "parses as 3.9; no PEP 701 f-string anywhere; none in the new block"

    def t16_page_is_still_a_jinja_template():
        r = c.get("/")
        assert r.status_code == 200, "the page did not render: " + str(r.status_code)
        h = r.get_data(as_text=True)
        for el in ("nowPain", "n_msk", "painList", "buildPainTiles", "loadPain",
                   "Operating day"):
            assert el in h, "missing from the page: " + el
        # rule 5d: medicine names live in regimen.local.json, not in the page
        for lbl in mod.PAIN_ANALGESICS:
            assert lbl not in h, "an analgesic chip name is baked into the page: " + lbl
        # v3.33.0: the gut pain-by-site list is the nine-region grid plus
        # diffuse, written in from ABD_SITES; both v3.4.2 labels are kept
        # exactly so history stays continuous (test_ui_now drives the grid)
        m = re.search(r"const ABD_SITES=(\[.*?\]\]);", h)
        assert m, "the region list was not written into the page"
        sites = [x[0] for x in json.loads(m.group(1))]
        assert len(sites) == 10 and "Left iliac pain" in sites and "Hypogastrium pain" in sites, \
            "the gut pain-by-site list changed: %r" % sites
        assert "__ABD_SITES__" not in h, "the placeholder reached the page"
        blk = h.split("GUTLOG_V3120_PAIN -- pain tiles", 1)[1].split("function buildNowStatics", 1)[0]
        for tok in ("{{", "{%", "{#"):
            assert tok not in blk, "Jinja token " + tok + " in the new JS (rule 5b)"
        ok, why = js_balanced(blk)
        assert ok, "new JS is unbalanced: " + why
        return "page renders, gut tiles still 2, new JS Jinja-clean and balanced"

    def t17_activity_picker_pinned_for_the_browser_suite():
        """Server suites never run page JS (rule 5b), and test_ui_now.py needs
        Playwright and Chromium, which the server has not got. So the constants
        its operating-day checks stand on are pinned here as well: change the
        picker and this fails offline, instead of only in a suite that cannot
        be run where the code is deployed."""
        h = c.get("/").get_data(as_text=True)
        m = re.search(r"const ACT=\[(.*?)\];", h, re.S)
        assert m, "the ACT tile list is gone"
        kinds = re.findall(r"\['([a-z_]+)',", m.group(1))
        assert kinds == ["walk", "treadmill", "cycle_road", "cycle_static",
                         "meditation", "ot_day"], "ACT changed: " + str(kinds)
        assert "const ACT_LOAD=['ot_day'];" in h, "ACT_LOAD is no longer ['ot_day']"
        assert "const ACT_HOURS=[2,4,6,8,10];" in h, "the hours picker changed"
        assert "isLoad?ACT_HOURS:[10,15,20,30,45,60]" in h, \
            "the load tile no longer swaps the minutes picker for hours"
        assert "k!=='meditation'&&!isLoad" in h, "the load tile regained intensity"
        assert not set(kinds) - set(mod.ACT_KINDS), \
            "a tile has no ACT_KINDS entry: " + str(set(kinds) - set(mod.ACT_KINDS))
        assert set(mod.LOAD_KINDS) <= set(kinds), "a load kind has no tile"
        return "6 tiles, ot_day last and load-only, hours 2/4/6/8/10 pinned"

    tests = [
        ("00 migration adds both columns", t00_migration),
        ("01 nine tiles, hero first", t01_nine_tiles_one_row_each),
        ("02 episodes row shape per tile, side kept", t02_row_shape_per_tile),
        ("03 below-the-knee only where asked", t03_radiates_only_where_asked),
        ("04 treatments pipe-joined, no dose row", t04_treatments_pipe_joined),
        ("05 analgesic chip = exactly one doses row", t05_analgesic_writes_exactly_one_dose),
        ("06 unknown medicine still recorded", t06_unknown_medicine_still_recorded),
        ("07 bad payloads refused", t07_guards),
        ("08 eased stamps a real duration", t08_eased_stamps_duration),
        ("09 duration reads as written", t09_duration_reads_as_written),
        ("10 pain list and day view", t10_pain_list_and_dayview),
        ("11 operating day stored in minutes", t11_ot_day_logged_in_hours_stored_in_minutes),
        ("12 operating day is not exercise", t12_ot_day_is_not_exercise),
        ("13 operating day reaches the feed", t13_ot_day_reaches_fitlog_and_the_day_view),
        ("14 feed: valid_from and ended", t14_feed_carries_valid_from_and_ended),
        ("15 Python 3.9 syntax throughout", t15_python_39_syntax),
        ("16 page renders, JS Jinja-clean", t16_page_is_still_a_jinja_template),
        ("17 activity picker pinned for test_ui_now", t17_activity_picker_pinned_for_the_browser_suite),
    ]
    print("=" * 70)
    print("GutLog v3.12.0 Phase I - pain surface, eased tap, operating day")
    print("=" * 70)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 70)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

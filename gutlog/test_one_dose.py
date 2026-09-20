#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.21.0 -- one tablet is one dose, however many symptoms
(GUTLOG_V3210_ONEDOSE).

Scratch database, real app, Flask test client. Every medicine and molecule
here is invented (alphacet, betacox), so the file names nothing on anyone's
record. The chips are set on the module, not read from regimen.local.json.
All entries are made on YESTERDAY at fixed times, so the suite reads the same
at any hour (GutLog refuses a time later than now on today's date).

  python3 test_one_dose.py [path/to/gutlog/app.py]
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

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
    work = tempfile.mkdtemp()
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_onedose", app_path)
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

    mid = {}
    for nm, mol in (("Test Alpha 500", "alphacet"), ("Test Beta 60", "betacox"),
                    ("Test Combo", "betacox + alphacet")):
        q("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 1, mol))
        mid[nm] = q("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]
    for label, mol, nm in (("Alpha", "alphacet", "Test Alpha 500"), ("Beta", "betacox", "Test Beta 60"),
                           ("Combo", "betacox + alphacet", "Test Combo")):
        gm.PAIN_ANALGESICS[label] = (mol, nm)
        gm.PAIN_TREATMENTS.append(label)
        gm.DOWN_COPED.append(label)

    Y = (date.today() - timedelta(days=1)).isoformat()

    def doses():
        return q("SELECT dtime, medicine FROM doses WHERE day=? ORDER BY dtime, id", (Y,))

    ctx = {}

    def t01():
        r = c.post("/api/downday", json={"day": Y, "coped": ["Combo"]}).get_json()
        assert r.get("ok"), r
        assert doses() == [("12:00", "Test Combo")], doses()
        return "the down-day chip writes the one combination dose at 12:00"
    check("01 a medicine chip still writes a dose when none is logged", t01)

    def t02():
        r = c.post("/api/pain", json={"site": "hip_thigh_both", "score": 6, "day": Y,
                                      "etime": "12:03", "treatments": ["Alpha", "Beta"]}).get_json()
        assert r.get("ok"), r
        assert doses() == [("12:00", "Test Combo")], (
            "one tablet became %d dose rows: %s" % (len(doses()), doses()))
        same = r.get("same_dose") or []
        assert sorted(s["label"] for s in same) == ["Alpha", "Beta"], same
        assert all(s["time"] == "12:00" and s["dose_name"] == "Test Combo" for s in same), same
        ctx["same"] = same
        return "both chips linked to the 12:00 combination dose; no new rows"
    check("02 a chip on a second symptom links to the dose already logged", t02)

    def t03():
        r = c.post("/api/pain", json={"site": "hip_thigh_r", "score": 5, "day": Y,
                                      "etime": "19:30", "treatments": ["Beta"]}).get_json()
        assert not r.get("same_dose"), "a dose 7.5 h later was linked: %s" % r.get("same_dose")
        assert ("19:30", "Test Beta 60") in doses(), doses()
        return "7.5 hours later is a new dose"
    check("03 a chip long after the last dose is a new dose", t03)

    def t04():
        r = c.post("/api/pain", json={"site": "hip_thigh_l", "score": 4, "day": Y,
                                      "etime": "19:40", "treatments": ["Combo"]}).get_json()
        assert not r.get("same_dose"), (
            "the combination was linked to a single-ingredient dose that lacks half of it: %s"
            % r.get("same_dose"))
        assert ("19:40", "Test Combo") in doses(), doses()
        return "a combination is not absorbed into a dose missing one of its ingredients"
    check("04 a combination links only to a dose carrying all its ingredients", t04)

    def t07():
        h = c.get("/").get_data(as_text=True)
        assert 'id="samedose"' in h and "function showSame" in h, "no same-dose strip on the page"
        assert "It was a new dose" in h and "showSame(r.same_dose)" in h, "strip not wired"
        return "strip present and wired to the pain tile and the down-day card"
    check("05 the page shows which dose a chip was counted with", t07)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.21.0 -- one tablet is one dose")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Functional test of GutLog v3.3.0 Phase A against a live patched app.

Boots the patched module with a throwaway DB, logs in through the real
setup flow, then exercises every new endpoint end to end.

  python3 test_phase_a.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((True, name, detail or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def load_app(app_path, workdir):
    """Import the patched app.py with its DB pointed at a scratch dir."""
    os.environ["GUTLOG_DB"] = os.path.join(workdir, "t.db")
    os.environ["GUTLOG_UPLOADS"] = os.path.join(workdir, "up")
    os.environ["GUTLOG_INSECURE"] = "1"        # allow http test client
    os.environ["GUTLOG_SECRET"] = "test-secret-not-real"
    os.environ["GUTLOG_ICONS"] = os.path.dirname(os.path.abspath(app_path))

    # pwa.py must be importable from the same directory
    sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
    spec = importlib.util.spec_from_file_location("gutlog_app", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "app.py")
    app_path = sys.argv[1] if len(sys.argv) > 1 else default
    if not os.path.exists(app_path):
        print("FATAL: app not found: " + app_path)
        return 1
    work = tempfile.mkdtemp()

    mod = load_app(app_path, work)
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()

    # --- real setup + login, not a faked session ----------------------
    r = c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
               follow_redirects=True)
    assert r.status_code == 200, "setup failed"

    def t00_login():
        r = c.get("/api/now")
        assert r.status_code == 200, "not authenticated after setup"
        return "authenticated via real setup flow"

    def t01_now_empty():
        j = c.get("/api/now").get_json()
        assert j["has_schedule"] is False, "empty DB reported a schedule"
        assert j["slots"] == [], "slots should be empty"
        assert len(j["meds"]) == 15, "expected 15 seeded meds, got " \
                                     + str(len(j["meds"]))
        return "empty schedule handled; 15 seeded meds offered"

    def t02_add_schedule():
        meds = c.get("/api/prnmeds/full").get_json()
        para = [m for m in meds if m["name"].startswith("Paracetamol")][0]
        colo = [m for m in meds if m["name"].startswith("Colospa")][0]
        r = c.post("/api/schedule", json={
            "med_id": para["id"], "slot": "MORNING",
            "dose_text": "1 tab", "with_food": "AFTER"})
        assert r.get_json().get("ok"), "schedule add failed"
        r = c.post("/api/schedule", json={
            "med_id": colo["id"], "slot": "EVENING", "dose_text": "1 cap"})
        assert r.get_json().get("ok"), "second schedule add failed"
        d = c.get("/api/schedule").get_json()
        assert len(d["rows"]) == 2, "expected 2 open rows"
        return "2 regimen lines added across 2 slots"

    def t03_now_renders():
        j = c.get("/api/now").get_json()
        assert j["has_schedule"] is True, "schedule not detected"
        assert len(j["slots"]) == 2, "expected 2 slots, got " \
                                     + str(len(j["slots"]))
        assert j["slots"][0]["slot"] == "MORNING", "slot order wrong"
        row = j["slots"][0]["rows"][0]
        assert row["status"] is None, "unlogged dose should have null status"
        assert j["slots"][0]["done"] == 0, "done count should be 0"
        return "2 slots render in order, nothing logged yet"

    def t04_tap_take():
        j = c.get("/api/now").get_json()
        row = j["slots"][0]["rows"][0]
        r = c.post("/api/now/dose", json={
            "med_id": row["med_id"], "sched_id": row["sched_id"],
            "status": "TAKEN", "dose_text": row["dose_text"]})
        assert r.get_json().get("ok"), "dose log failed"
        j = c.get("/api/now").get_json()
        row = j["slots"][0]["rows"][0]
        assert row["status"] == "TAKEN", "status did not persist"
        assert j["slots"][0]["done"] == 1, "done count did not move"
        return "one tap logs TAKEN and the count moves"

    def t05_no_duplicate_on_retap():
        j = c.get("/api/now").get_json()
        row = j["slots"][0]["rows"][0]
        c.post("/api/now/dose", json={
            "med_id": row["med_id"], "sched_id": row["sched_id"],
            "status": "SKIPPED"})
        j = c.get("/api/now").get_json()
        assert len(j["slots"][0]["rows"]) == 1, \
            "re-tap duplicated the expected-dose row"
        assert j["slots"][0]["rows"][0]["status"] == "SKIPPED", \
            "status did not correct to SKIPPED"
        return "re-tap corrects in place, never duplicates"

    def t06_skip_is_data():
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        n = con.execute(
            "SELECT COUNT(*) FROM doses WHERE status='SKIPPED'").fetchone()[0]
        con.close()
        assert n == 1, "expected 1 SKIPPED row, found " + str(n)
        return "a skip is a real row, not an absence"

    def t07_extra_dose():
        meds = c.get("/api/prnmeds/full").get_json()
        zol = [m for m in meds if m["name"].startswith("Zolpidem")][0]
        r = c.post("/api/now/dose", json={"med_id": zol["id"],
                                          "status": "EXTRA"})
        assert r.get_json().get("ok"), "extra dose failed"
        j = c.get("/api/now").get_json()
        assert len(j["extras"]) == 1, "extra not listed"
        assert j["extras"][0]["medicine"].startswith("Zolpidem"), \
            "wrong extra recorded"
        return "extra dose logged with sched_id NULL and listed separately"

    def t08_undo():
        j = c.get("/api/now").get_json()
        eid = j["extras"][0]["id"]
        r = c.post("/api/now/undo/" + str(eid), json={})
        assert r.get_json().get("ok"), "undo failed"
        j = c.get("/api/now").get_json()
        assert len(j["extras"]) == 0, "extra survived undo"
        return "mistap undo removes the row"

    def t09_schedule_change_is_dated():
        meds = c.get("/api/prnmeds/full").get_json()
        para = [m for m in meds if m["name"].startswith("Paracetamol")][0]
        before = c.get("/api/schedule").get_json()
        ep0 = int([r for r in before["rows"]
                   if r["med_id"] == para["id"]][0]["epoch"])
        c.post("/api/schedule", json={
            "med_id": para["id"], "slot": "MORNING", "dose_text": "2 tab"})
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        rows = con.execute(
            "SELECT dose_text, valid_from, valid_to, epoch FROM med_schedule "
            "WHERE med_id=? AND slot='MORNING' ORDER BY id",
            (para["id"],)).fetchall()
        con.close()
        assert len(rows) == 2, "change did not create a history row"
        assert rows[0][2] != "", "old row was not closed"
        assert rows[1][2] == "", "new row is not open"
        assert rows[1][0] == "2 tab", "new dose not recorded"
        assert int(rows[1][3]) > ep0, "epoch did not increment"
        return "close-and-open honoured; epoch " + str(ep0) + " -> " \
               + str(rows[1][3])

    def t10_unique_open_enforced():
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        try:
            # Must target a med that genuinely has an OPEN row in this slot,
            # or the constraint is never challenged. (An earlier version of
            # this test used LIMIT 1 with no ORDER BY and silently picked a
            # med whose open row was in a different slot -- SQLite is free to
            # return any row for an unordered LIMIT, and idx_sched_med
            # changed which one came back.)
            row = con.execute(
                "SELECT med_id, slot FROM med_schedule "
                "WHERE valid_to='' ORDER BY id LIMIT 1").fetchone()
            assert row, "no open schedule row to test against"
            med, slot = row[0], row[1]
            failed = False
            try:
                con.execute(
                    "INSERT INTO med_schedule(med_id,slot,valid_from,valid_to)"
                    " VALUES(?,?,'2026-01-01','')", (med, slot))
                con.commit()
            except Exception:
                failed = True
            assert failed, \
                "DB accepted a second open row for med " + str(med) + "/" + slot

            # and the same insert must be accepted once the first is closed
            con.execute("UPDATE med_schedule SET valid_to='2026-01-01' "
                        "WHERE med_id=? AND slot=? AND valid_to=''",
                        (med, slot))
            con.execute(
                "INSERT INTO med_schedule(med_id,slot,valid_from,valid_to)"
                " VALUES(?,?,'2026-01-02','')", (med, slot))
            con.commit()
            return "duplicate open row rejected; close-then-open accepted"
        finally:
            con.close()

    def t11_stop_medicine():
        d = c.get("/api/schedule").get_json()
        sid = d["rows"][0]["id"]
        r = c.post("/api/schedule/close/" + str(sid), json={})
        assert r.get_json().get("ok"), "close failed"
        d2 = c.get("/api/schedule").get_json()
        assert len(d2["rows"]) == len(d["rows"]) - 1, "row still open"
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        still = con.execute("SELECT COUNT(*) FROM med_schedule WHERE id=?",
                            (sid,)).fetchone()[0]
        con.close()
        assert still == 1, "stopping deleted history instead of closing it"
        return "stop closes the row and keeps the history"

    def t12_bristol_on_episode():
        r = c.post("/api/episodes", json={
            "category": "GI", "etype": "Cramp", "severity": 6, "bristol": "6"})
        assert r.get_json().get("ok"), "episode failed"
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        cols = [x[1] for x in con.execute("PRAGMA table_info(episodes)")]
        con.close()
        assert "bristol" in cols, "episodes.bristol missing"
        return "episode accepted; bristol column present"

    def t13_vitals_unchanged():
        r = c.post("/api/vitals", json={"sys": 138, "dia": 86, "pulse": 74})
        assert r.get_json().get("ok"), "vitals failed"
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        row = con.execute(
            "SELECT sys,dia,pulse FROM vitals ORDER BY id DESC LIMIT 1"
        ).fetchone()
        con.close()
        assert row == (138, 86, 74), "vitals not stored: " + str(row)
        return "BP path works unchanged (138/86, pulse 74)"

    def t14_pwa_served():
        r = c.get("/manifest.webmanifest")
        assert r.status_code == 200, "manifest not served"
        m = r.get_json()
        assert len(m["shortcuts"]) == 3, "shortcuts missing"
        r = c.get("/sw.js")
        assert r.status_code == 200, "sw.js not served"
        r = c.get("/icon-192.png")
        assert r.status_code == 200, "icon not served"
        return "manifest, service worker and icons all served"

    def t15_page_renders():
        r = c.get("/")
        assert r.status_code == 200, "home page failed"
        html = r.data.decode("utf-8")
        for want in ['id="tab-now"', 'data-t="now"', 'manifest.webmanifest',
                     'id="meds-sched"', "let tab='now'"]:
            assert want in html, "missing from page: " + want
        assert html.count('class="tab sel"') == 1, \
            "more than one default tab"
        return "page renders with Now default, schedule panel, PWA head"

    def t16_legacy_endpoints_alive():
        for path in ["/api/prnmeds", "/api/courses/active", "/api/patch",
                     "/api/doses/today/2026-09-10", "/api/labs/status"]:
            r = c.get(path)
            assert r.status_code == 200, path + " broke (" \
                                         + str(r.status_code) + ")"
        return "5 pre-existing endpoints still respond"

    def t17_migrate_fast_path():
        con = mod.sqlite3.connect(os.environ["GUTLOG_DB"])
        v = con.execute("SELECT value FROM settings WHERE key='schema_version'"
                        ).fetchone()[0]
        con.close()
        # any 3.3.x: the point is that _migrate stamped a version and will
        # therefore short-circuit, not which patch level is current
        assert v.startswith("3.3."), "schema_version not stamped: " + str(v)
        return "schema_version stamped " + v + " (migrate short-circuits)"

    tests = [
        ("00 authenticated boot", t00_login),
        ("01 /api/now on empty schedule", t01_now_empty),
        ("02 add regimen lines", t02_add_schedule),
        ("03 /api/now renders slots", t03_now_renders),
        ("04 one tap logs TAKEN", t04_tap_take),
        ("05 re-tap corrects, no duplicate", t05_no_duplicate_on_retap),
        ("06 skip is stored data", t06_skip_is_data),
        ("07 extra dose path", t07_extra_dose),
        ("08 undo a mistap", t08_undo),
        ("09 schedule change is effective-dated", t09_schedule_change_is_dated),
        ("10 one-open-row constraint enforced", t10_unique_open_enforced),
        ("11 stop keeps history", t11_stop_medicine),
        ("12 bristol on episodes", t12_bristol_on_episode),
        ("13 vitals regression", t13_vitals_unchanged),
        ("14 PWA assets served", t14_pwa_served),
        ("15 page renders correctly", t15_page_renders),
        ("16 legacy endpoints alive", t16_legacy_endpoints_alive),
        ("17 migrate fast path stamped", t17_migrate_fast_path),
    ]

    print("=" * 66)
    print("GutLog v3.3.0 Phase A - functional test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)

    passed = 0
    for ok, name, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        line = "[" + mark + "] " + name
        if detail:
            line += "  -- " + detail
        print(line)

    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

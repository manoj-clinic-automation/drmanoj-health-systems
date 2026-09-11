#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.5.0 Phase B -- server-side functional test.

Retime, backfill, the day view, and the guards around them. Builds its own
scratch database; never touches the live one. Python 3.9.

  python3 test_phase_b.py [path/to/app.py]     -> must print 16/16 passed
"""
import importlib.util
import os
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
    sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
    spec = importlib.util.spec_from_file_location("gutlog_app_b", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    q = lambda sql, a=(): sqlite3.connect(dbp).execute(sql, a).fetchall()

    T = date.today()
    TODAY, Y1, Y2, Y3, Y9 = [(T - timedelta(days=n)).isoformat() for n in (0, 1, 2, 3, 9)]
    TOMORROW = (T + timedelta(days=1)).isoformat()
    NOW = datetime.now().strftime("%H:%M")
    ctx = {}

    def now_row(day, med_id):
        j = c.get("/api/now?day=" + day).get_json()
        for s in j["slots"]:
            for r in s["rows"]:
                if r["med_id"] == med_id:
                    return r
        return None

    def t00_setup():
        meds = c.get("/api/prnmeds/full").get_json()
        assert len(meds) >= 3, "need 3 seeded meds (regimen.local.json beside app.py?)"
        ctx["a"], ctx["b"], ctx["x"] = meds[0]["id"], meds[1]["id"], meds[2]["id"]
        for mid, extra in ((ctx["a"], {"dose_text": "1 tab"}),
                           (ctx["b"], {"dose_text": "", "variants": "72|145|290"})):
            body = {"med_id": mid, "slot": "MORNING", "valid_from": Y3}
            body.update(extra)
            r = c.post("/api/schedule", json=body).get_json()
            assert r.get("ok"), "schedule add failed: " + str(r)
        assert now_row(Y2, ctx["a"]) is not None, "schedule not effective 2 days back"
        assert now_row(Y9, ctx["a"]) is None, "schedule wrongly effective 9 days back"
        return "2 MORNING lines effective from 3 days back"

    def t01_backfill_yesterday():
        r = c.post("/api/now/dose", json={"med_id": ctx["a"], "sched_id": now_row(Y1, ctx["a"])["sched_id"],
                                          "status": "TAKEN", "day": Y1, "dtime": "07:30",
                                          "dose_text": "1 tab"})
        assert r.status_code == 200, "backfill refused: " + r.get_data(as_text=True)
        row = now_row(Y1, ctx["a"])
        assert row["status"] == "TAKEN" and row["dtime"] == "07:30", "backfill not stored: " + str(row)
        assert now_row(TODAY, ctx["a"])["status"] is None, "backfill leaked into today"
        return "yesterday's dose entered at 07:30, today untouched"

    def t02_backfill_variant():
        sid = now_row(Y1, ctx["b"])["sched_id"]
        c.post("/api/now/dose", json={"med_id": ctx["b"], "sched_id": sid, "status": "TAKEN",
                                      "day": Y1, "dtime": "08:10", "dose_text": "145 + 72"})
        row = now_row(Y1, ctx["b"])
        assert row["logged_dose"] == "145 + 72", "variant dose lost: " + str(row)
        return "variant backfill keeps '145 + 72'"

    def t03_backfill_future_refused():
        sid = now_row(TODAY, ctx["a"])["sched_id"]
        r = c.post("/api/now/dose", json={"med_id": ctx["a"], "sched_id": sid, "status": "TAKEN",
                                          "day": TOMORROW, "dtime": "07:00"})
        assert r.status_code == 400, "future day accepted"
        if NOW < "23:59":
            r = c.post("/api/now/dose", json={"med_id": ctx["a"], "sched_id": sid, "status": "TAKEN",
                                              "day": TODAY, "dtime": "23:59"})
            assert r.status_code == 400, "future time today accepted"
        r = c.post("/api/now/dose", json={"med_id": ctx["a"], "sched_id": sid, "status": "TAKEN",
                                          "day": TODAY, "dtime": "7:5"})
        assert r.status_code == 400, "malformed time accepted"
        assert q("SELECT COUNT(*) FROM doses WHERE day IN (?,?)", (TODAY, TOMORROW))[0][0] == 0, \
            "a refused backfill still wrote a row"
        return "future day, future time and bad time refused, nothing written"

    def t04_now_tap_still_one_tap():
        sid = now_row(TODAY, ctx["a"])["sched_id"]
        r = c.post("/api/now/dose", json={"med_id": ctx["a"], "sched_id": sid,
                                          "status": "TAKEN", "day": TODAY})
        assert r.status_code == 200, "plain tap refused"
        ctx["dose_today"] = r.get_json()["id"]
        assert now_row(TODAY, ctx["a"])["status"] == "TAKEN", "tap not stored"
        return "Now-tab tap without a time still logs at the current time"

    def t05_retime_today():
        r = c.post("/api/retime", json={"table": "doses", "id": ctx["dose_today"],
                                        "day": TODAY, "time": "00:00"})
        assert r.status_code == 200 and r.get_json().get("ok"), "retime failed: " + r.get_data(as_text=True)
        assert now_row(TODAY, ctx["a"])["dtime"] == "00:00", "time not changed"
        e = q("SELECT old_day, new_day, new_time FROM edits WHERE tbl='doses' AND rid=?",
              (ctx["dose_today"],))
        assert len(e) == 1 and e[0][1] == TODAY and e[0][2] == "00:00", "edit not recorded: " + str(e)
        return "retimed to 00:00, old/new kept in edits"

    def t06_retime_unchanged_writes_nothing():
        n0 = q("SELECT COUNT(*) FROM edits")[0][0]
        r = c.post("/api/retime", json={"table": "doses", "id": ctx["dose_today"],
                                        "day": TODAY, "time": "00:00"}).get_json()
        assert r.get("unchanged"), "no-op not recognised"
        assert q("SELECT COUNT(*) FROM edits")[0][0] == n0, "no-op wrote an edit row"
        return "same time again = no change, no audit noise"

    def t07_retime_guards():
        base = {"table": "doses", "id": ctx["dose_today"]}
        bad = [dict(base, day=TOMORROW, time="07:00"), dict(base, day=TODAY, time="24:00"),
               dict(base, day=TODAY, time="ab:cd"), dict(base, day="2026-13-40", time="07:00"),
               {"table": "settings", "id": 1, "day": TODAY, "time": "07:00"},
               dict(base, id=999999, day=TODAY, time="07:00")]
        if NOW < "23:59":
            bad.append(dict(base, day=TODAY, time="23:59"))
        for b in bad:
            r = c.post("/api/retime", json=b)
            assert r.status_code in (400, 404), "accepted: " + str(b)
        assert now_row(TODAY, ctx["a"])["dtime"] == "00:00", "a refused retime changed the row"
        return str(len(bad)) + " bad retimes refused, row unchanged"

    def t08_move_onto_logged_day_refused():
        r = c.post("/api/retime", json={"table": "doses", "id": ctx["dose_today"],
                                        "day": Y1, "time": "07:00"})
        assert r.status_code == 409, "moved onto a day already logged: " + str(r.status_code)
        return "cannot move a scheduled dose onto a day it is already logged"

    def t09_move_outside_schedule_refused():
        r = c.post("/api/retime", json={"table": "doses", "id": ctx["dose_today"],
                                        "day": Y9, "time": "07:00"})
        assert r.status_code == 400, "moved to a day the regimen did not cover"
        return "cannot move a scheduled dose to a day its regimen line did not cover"

    def t10_move_to_free_day():
        r = c.post("/api/retime", json={"table": "doses", "id": ctx["dose_today"],
                                        "day": Y2, "time": "06:45"})
        assert r.status_code == 200, "move to a free covered day refused"
        assert now_row(Y2, ctx["a"])["status"] == "TAKEN", "not on the new day"
        assert now_row(TODAY, ctx["a"])["status"] is None, "still counted today"
        return "moved to 2 days back; today shows it pending again"

    def t11_extra_has_no_schedule_limit():
        r = c.post("/api/now/dose", json={"med_id": ctx["x"], "status": "EXTRA", "day": TODAY})
        did = r.get_json()["id"]
        r = c.post("/api/retime", json={"table": "doses", "id": did, "day": Y9, "time": "22:15"})
        assert r.status_code == 200, "extra dose could not move"
        return "an extra dose moves to any past day"

    def t12_other_streams_retime():
        c.post("/api/episodes", json={"day": Y1, "etime": "09:00", "category": "GI",
                                      "etype": "Left iliac pain", "severity": "6", "bristol": "4"})
        c.post("/api/vitals", json={"day": Y1, "vtime": "07:00", "sys": 128, "dia": 82, "pulse": 70})
        c.post("/api/meals", json={"day": Y1, "mtime": "13:00", "slot": "Lunch",
                                   "items": [{"n": "Dal", "q": 1, "p": 9, "k": 150, "f": 4, "fm": "L"}]})
        ids = {t: q("SELECT id FROM " + t + " WHERE day=? ORDER BY id DESC LIMIT 1", (Y1,))[0][0]
               for t in ("episodes", "vitals", "meals")}
        for t, tm in (("episodes", "09:20"), ("vitals", "06:50"), ("meals", "13:30")):
            r = c.post("/api/retime", json={"table": t, "id": ids[t], "day": Y1, "time": tm})
            assert r.status_code == 200, t + " retime refused"
        col = {"episodes": "etime", "vitals": "vtime", "meals": "mtime"}
        got = [q("SELECT " + col[t] + " FROM " + t + " WHERE id=?", (ids[t],))[0][0]
               for t in ("episodes", "vitals", "meals")]
        assert got == ["09:20", "06:50", "13:30"], "stored times wrong: " + str(got)
        return "symptom, BP and meal retime and store"

    def t13_dayview_merges_in_order():
        j = c.get("/api/dayview?day=" + Y1).get_json()
        seq = [(e["time"], e["kind"]) for e in j["entries"]]
        want = [("06:50", "BP"), ("07:30", "Dose"), ("08:10", "Dose"),
                ("09:20", "Symptom"), ("13:30", "Meal")]
        assert seq == want, "order/kinds wrong: " + str(seq)
        sym = [e for e in j["entries"] if e["kind"] == "Symptom"][0]
        assert "6/10" in sym["sub"] and "Bristol 4" in sym["sub"], "symptom detail: " + sym["sub"]
        assert sym["edited"] is True, "retimed entry not marked edited"
        dose = [e for e in j["entries"] if e["time"] == "07:30"][0]
        assert dose["edited"] is False, "untouched entry marked edited"
        return "5 entries, 4 streams, time order, edited flags right"

    def t14_dayview_future_falls_back():
        j = c.get("/api/dayview?day=" + TOMORROW).get_json()
        assert j["day"] == TODAY, "future day served: " + j["day"]
        return "a future day in the URL falls back to today"

    def t15_skip_backfill_is_data():
        sid = now_row(Y3, ctx["a"])["sched_id"]
        r = c.post("/api/now/dose", json={"med_id": ctx["a"], "sched_id": sid, "status": "SKIPPED",
                                          "day": Y3, "dtime": "08:00"})
        assert r.status_code == 200, "backfilled skip refused"
        j = c.get("/api/dayview?day=" + Y3).get_json()
        assert [e["kind"] for e in j["entries"]] == ["Skipped"], "skip not in day view"
        return "a backfilled skip is a real row"

    tests = [
        ("00 setup: regimen from 3 days back", t00_setup),
        ("01 backfill yesterday's dose", t01_backfill_yesterday),
        ("02 backfill a variant dose", t02_backfill_variant),
        ("03 future backfill refused", t03_backfill_future_refused),
        ("04 Now tap unchanged", t04_now_tap_still_one_tap),
        ("05 retime today + audit", t05_retime_today),
        ("06 no-op retime is silent", t06_retime_unchanged_writes_nothing),
        ("07 retime guards", t07_retime_guards),
        ("08 no move onto a logged day", t08_move_onto_logged_day_refused),
        ("09 no move outside the regimen", t09_move_outside_schedule_refused),
        ("10 move to a free day", t10_move_to_free_day),
        ("11 extras move freely", t11_extra_has_no_schedule_limit),
        ("12 symptom / BP / meal retime", t12_other_streams_retime),
        ("13 day view merges in order", t13_dayview_merges_in_order),
        ("14 day view refuses the future", t14_dayview_future_falls_back),
        ("15 backfilled skip is data", t15_skip_backfill_is_data),
    ]
    print("=" * 66)
    print("GutLog v3.5.0 Phase B - functional test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

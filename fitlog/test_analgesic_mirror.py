#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.4.0 -- the analgesic mirror and the operating-day load.

Runs a REAL GutLog and a REAL FitLog, each on its own scratch database and a
scratch feed token, on two free loopback ports. Nothing live is touched and no
outward call leaves the machine. The fixture is synthetic throughout: invented
medicine names, invented molecules, and chips the suite supplies itself rather
than reading the owner's regimen.local.json.

What it proves, which no single-app suite can:
  * a pain tile tapped in GutLog produces exactly ONE `doses` row there and
    exactly ONE analgesic_log row here -- not two records that disagree;
  * the score entered on the tile arrives as pain_at_time. That is the whole
    reason for this endpoint: the dose feed can say a drug was taken, never
    how bad it was when he took it;
  * a retry does not double the record;
  * an operating day is shown as standing load and never as exercise.

  python3 test_analgesic_mirror.py [/root/gutlog/app.py]
"""
import importlib.util
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gut_path = sys.argv[1] if len(sys.argv) > 1 else "/root/gutlog/app.py"
    if not os.path.exists(gut_path):
        print("FATAL: GutLog not found at " + gut_path)
        print("       python3 test_analgesic_mirror.py /path/to/gutlog/app.py")
        return 1
    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    gport, fport = free_port(), free_port()

    # ---- a real GutLog, pointed at the FitLog we are about to start -----
    os.environ.update(
        GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
        GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
        GUTLOG_ICONS=os.path.dirname(os.path.abspath(gut_path)),
        GUTLOG_FEED_TOKEN_FILE=tokf, GUTLOG_LINKS="1",
        GUTLOG_FITLOG_URL="http://127.0.0.1:%d" % fport,
        GUTLOG_FEED_URL="http://127.0.0.1:%d" % gport)
    sys.path.insert(0, os.path.dirname(os.path.abspath(gut_path)))
    gl = load("gutlog_under_test", gut_path)
    gc = gl.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
            follow_redirects=True)
    gdb = os.environ["GUTLOG_DB"]
    # Synthetic chips, synthetic molecules. The real labels are medicine names
    # and live in GutLog's regimen.local.json (this repository is public), so
    # the suite invents its own; the two below exercise the exact-generic and
    # the combination-component halves of the match.
    gl.PAIN_ANALGESICS = {"Testamol 500": ("testamol", "Testamol 500"),
                          "Testcoxib 60": ("testcoxib", "Testcoxib 60")}
    gl.PAIN_TREATMENTS = gl.PAIN_TREATMENTS_BASE + list(gl.PAIN_ANALGESICS)

    # ---- a real FitLog on the port GutLog was told about -----------------
    os.environ.update(FITLOG_DB=os.path.join(work, "f.db"),
                      FITLOG_SECRET="test-secret-not-real",
                      FITLOG_GUTLOG_FEED="1")
    sys.path.insert(0, here)
    fl = load("fitlog_under_test", os.path.join(here, "app.py"))
    fdb = os.environ["FITLOG_DB"]

    from werkzeug.serving import make_server
    gsrv = make_server("127.0.0.1", gport, gl.app)
    fsrv = make_server("127.0.0.1", fport, fl.app)
    for s in (gsrv, fsrv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    time.sleep(0.3)

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def fq(sql, a=()):
        con = sqlite3.connect(fdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    # synthetic stack: these two generics are what the GutLog tiles offer
    for kb, nm, gen in (("T01", "Testamol 500", "testamol"),
                        ("T02", "Testcoxib 60", "testcoxib + testamol")):
        fq("INSERT INTO med_stack(kb_id,name,generic,strength,category,dose_options,"
           "route,active) VALUES(?,?,?,'','analgesic','[\"1 tab\"]','oral',1)",
           (kb, nm, gen))
    # and the matching medicines in GutLog, so a chip resolves to a real
    # prnmeds row there exactly as it does on the server
    gc.get("/api/now")
    for nm, mol in (("Test Analg A", "testamol"), ("Test Analg B", "testcoxib")):
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,9,?,1)", (nm, mol))

    TODAY = date.today().isoformat()
    # Every time written to TODAY comes from the clock, never from a literal.
    # GutLog refuses a time that has not come yet, so a hardcoded "06:45"
    # turns this suite red at 03:50 and green at 18:00 -- and the small hours
    # are exactly when this diary gets used. Clamped to midnight.
    NOW_DT = datetime.now()
    MIDNIGHT = NOW_DT.replace(hour=0, minute=0, second=0, microsecond=0)

    def ago(mins):
        t = NOW_DT - timedelta(minutes=mins)
        return (t if t >= MIDNIGHT else MIDNIGHT).strftime("%H:%M")

    NOWHM = NOW_DT.strftime("%H:%M")
    EARLY = ago(90)
    OLD = (date.today() - timedelta(days=3)).isoformat()
    tok = open(tokf, encoding="utf-8").read().strip()
    ctx = {}

    def post_fit(payload, bearer=None):
        import json as _j
        import urllib.error
        import urllib.request
        hdr = {"Content-Type": "application/json"}
        if bearer is not False:
            hdr["Authorization"] = "Bearer " + (bearer or tok)
        req = urllib.request.Request("http://127.0.0.1:%d/api/analgesic" % fport,
                                     data=_j.dumps(payload).encode("utf-8"),
                                     headers=hdr, method="POST")
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with op.open(req, timeout=3) as r:
                return r.status, _j.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, None

    def t00_endpoint_is_bearer_gated():
        code, _ = post_fit({"molecule": "testamol"}, bearer=False)
        assert code == 401, "no token was accepted: " + str(code)
        code, _ = post_fit({"molecule": "testamol"}, bearer="0" * 64)
        assert code == 401, "a wrong token was accepted: " + str(code)
        assert fq("SELECT COUNT(*) FROM analgesic_log")[0][0] == 0
        return "machine-to-machine, bearer-gated on GutLog's feed token"

    def t01_one_tap_one_record_each_side():
        before_g = gq("SELECT COUNT(*) FROM doses")[0][0]
        before_f = fq("SELECT COUNT(*) FROM analgesic_log")[0][0]
        r = gc.post("/api/pain", json={"site": "hip_thigh_both", "score": 7,
                                       "treatments": ["Heat pad", "Testamol 500"],
                                       "day": TODAY, "etime": EARLY})
        j = r.get_json()
        assert j.get("ok"), str(j)
        assert j["linked"] is True, "the link was off, so nothing was proved"
        assert j["mirrored"], "GutLog reports nothing mirrored: " + str(j)
        assert not j["not_mirrored"], str(j["not_mirrored"])
        dg = gq("SELECT COUNT(*) FROM doses")[0][0] - before_g
        df = fq("SELECT COUNT(*) FROM analgesic_log")[0][0] - before_f
        assert dg == 1, "GutLog wrote " + str(dg) + " dose rows, not 1"
        assert df == 1, "FitLog wrote " + str(df) + " analgesic rows, not 1"
        ctx["row"] = fq("SELECT dt, med_id, dose_label, pain_at_time, notes "
                        "FROM analgesic_log ORDER BY id DESC LIMIT 1")[0]
        return "one tile tap -> 1 doses row in GutLog, 1 analgesic_log row here"

    def t02_the_score_travels():
        dt, med_id, dose_label, pain, notes = ctx["row"]
        assert pain == 7, "pain_at_time is " + str(pain) + ", not the 7 he entered"
        assert dt == TODAY + "T" + EARLY, "timestamp " + str(dt)
        nm = fq("SELECT name, generic FROM med_stack WHERE id=?", (med_id,))[0]
        assert nm[1] == "testamol", "matched the wrong stack row: " + str(nm)
        assert "Both hips" in (notes or ""), "the site did not travel: " + repr(notes)
        return "pain_at_time=7, matched " + nm[0] + ", site carried in the note"

    def t03_two_chips_two_records_one_score():
        before_g = gq("SELECT COUNT(*) FROM doses")[0][0]
        before_f = fq("SELECT COUNT(*) FROM analgesic_log")[0][0]
        j = gc.post("/api/pain", json={"site": "glute_r", "score": 5,
                                       "treatments": ["Testamol 500", "Testcoxib 60"],
                                       "day": TODAY, "etime": NOWHM}).get_json()
        assert len(j["mirrored"]) == 2 and not j["not_mirrored"], str(j)
        assert gq("SELECT COUNT(*) FROM doses")[0][0] - before_g == 2
        assert fq("SELECT COUNT(*) FROM analgesic_log")[0][0] - before_f == 2
        rows = fq("SELECT pain_at_time FROM analgesic_log ORDER BY id DESC LIMIT 2")
        assert [r[0] for r in rows] == [5, 5], str(rows)
        gens = fq("SELECT DISTINCT m.generic FROM analgesic_log l JOIN med_stack m "
                  "ON m.id=l.med_id WHERE l.dt=?", (TODAY + "T" + NOWHM,))
        assert len(gens) == 2, "both chips matched the same stack row: " + str(gens)
        return "2 chips -> 2 + 2 rows, each carrying the same score"

    def t04_a_retry_does_not_double_it():
        # a fixed past day, so this timestamp can never collide with one the
        # clock-derived cases above happened to produce
        payload = {"dt": OLD + "T06:05", "molecule": "testamol",
                   "name": "Testamol 500", "dose_label": "Testamol 500",
                   "pain_at_time": 6, "notes": "GutLog: Low back",
                   "ref": "gutlog-episode-999"}
        code, a = post_fit(payload)
        assert code == 200 and a["ok"] and not a.get("duplicate"), str(a)
        code, b = post_fit(payload)
        assert code == 200 and b["ok"] and b.get("duplicate"), str(b)
        assert a["id"] == b["id"], "a retry made a second row"
        n = fq("SELECT COUNT(*) FROM analgesic_log WHERE dt=?",
               (OLD + "T06:05",))[0][0]
        assert n == 1, str(n) + " rows for one event"
        return "same event twice -> one row, the first id returned again"

    def t05_unknown_molecule_is_refused_not_invented():
        before = fq("SELECT COUNT(*) FROM analgesic_log")[0][0]
        code, a = post_fit({"dt": OLD + "T06:30", "molecule": "unobtainium",
                            "pain_at_time": 4})
        assert code == 200 and a["ok"] is False, str(a)
        # and not by a chance substring of a real generic: "test" is inside
        # "testcoxib", and must not file a dose under it
        code, b = post_fit({"dt": OLD + "T06:31", "molecule": "test",
                            "pain_at_time": 4})
        assert code == 200 and b["ok"] is False, "substring match: " + str(b)
        assert fq("SELECT COUNT(*) FROM analgesic_log")[0][0] == before, \
            "a row was written for a molecule the stack does not carry"
        return "no matching medicine: refused and said so, nothing invented"

    def t06_gutlog_is_told_when_the_mirror_misses():
        # nothing in the stack carries testamol at all, as a whole generic
        # or as a component of a combination
        fq("UPDATE med_stack SET active=0 WHERE generic LIKE '%testamol%'")
        before = gq("SELECT COUNT(*) FROM doses")[0][0]
        j = gc.post("/api/pain", json={"site": "low_back", "score": 3,
                                       "treatments": ["Testamol 500"],
                                       "day": TODAY, "etime": ago(3)}).get_json()
        assert j.get("not_mirrored"), "GutLog claimed a mirror that did not happen: " + str(j)
        assert not j["mirrored"], str(j)
        # a delta, not a count keyed on the time: in the first 90 minutes after
        # midnight every derived time clamps to 00:00 and a time-keyed count
        # picks up an earlier case's row
        assert gq("SELECT COUNT(*) FROM doses")[0][0] == before + 1, \
            "the dose was lost because the mirror failed"
        fq("UPDATE med_stack SET active=1 WHERE generic LIKE '%testamol%'")
        return "mirror fails -> GutLog still records the dose and says so"

    def t07_operating_day_is_load_not_exercise():
        assert "ot_day" in fl.ACT_LABEL, "ACT_LABEL has no ot_day"
        assert "ot_day" in fl.LOAD_KINDS, "ot_day is not a load kind"
        r1 = gc.post("/api/activity", json={"kind": "ot_day", "minutes": 480,
                                            "day": TODAY, "atime": ago(2)})
        r2 = gc.post("/api/activity", json={"kind": "walk", "minutes": 25,
                                            "day": TODAY, "atime": ago(1)})
        assert r1.status_code == 200 and r2.status_code == 200, \
            r1.get_data(as_text=True) + " / " + r2.get_data(as_text=True)
        fl._GA_CACHE.clear()
        with fl.app.test_request_context():
            card = fl.activity_card(TODAY)
        assert "Standing load" in card, "no standing-load block on the card"
        assert "Operating day" in card and "8.0 h" in card, card[-600:]
        assert "Operating day 480 min" not in card, "shown as 480 exercise minutes"
        assert "never counted as exercise minutes" in card
        assert "Walk 25 min" in card, "the walk was lost"
        return "operating day shown as 8.0 h of standing load, apart from exercise"

    tests = [
        ("00 /api/analgesic is bearer-gated", t00_endpoint_is_bearer_gated),
        ("01 one tap, one record each side", t01_one_tap_one_record_each_side),
        ("02 the score travels with the dose", t02_the_score_travels),
        ("03 two chips, two records, one score", t03_two_chips_two_records_one_score),
        ("04 a retry does not double it", t04_a_retry_does_not_double_it),
        ("05 unknown molecule refused", t05_unknown_molecule_is_refused_not_invented),
        ("06 GutLog is told when the mirror misses", t06_gutlog_is_told_when_the_mirror_misses),
        ("07 operating day is load, not exercise", t07_operating_day_is_load_not_exercise),
    ]
    print("=" * 70)
    print("FitLog v1.4.0 - analgesic mirror (real GutLog + real FitLog)")
    print("=" * 70)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 70)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    for s in (gsrv, fsrv):
        try:
            s.shutdown()
        except Exception:
            pass
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

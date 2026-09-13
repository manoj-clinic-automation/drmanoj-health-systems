#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.5.0 -- the GutLog reconciliation.

Runs a REAL GutLog (scratch database, scratch token) on a free loopback port
and points RxGuard (scratch database) at it. Nothing live is touched. The
fixture is synthetic: invented brand names, molecules chosen only because the
knowledge base defines a rule on the pair.

It reproduces the September 2026 failure in miniature: a medicine ended in
GutLog, still active in RxGuard, and an interaction finding that rests on it.

  python3 test_reconcile.py [/root/gutlog/app.py]
"""
import importlib.util
import os
import socket
import sqlite3
import sys
import tempfile
import threading
from datetime import date, timedelta

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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gut_path = sys.argv[1] if len(sys.argv) > 1 else "/root/gutlog/app.py"
    if not os.path.exists(gut_path):
        print("FATAL: GutLog not found at " + gut_path)
        return 1
    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    port = free_port()

    T = date.today()
    TODAY = T.isoformat()
    Y2, Y8, Y30 = [(T - timedelta(days=n)).isoformat() for n in (2, 8, 30)]

    # ---- a real GutLog on loopback --------------------------------------
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"),
                      GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(os.path.abspath(gut_path)),
                      GUTLOG_FEED_TOKEN_FILE=tokf, GUTLOG_LINKS="0")
    sys.path.insert(0, os.path.dirname(os.path.abspath(gut_path)))
    spec = importlib.util.spec_from_file_location("gutlog_under_test", gut_path)
    gmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gmod)
    gc = gmod.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
            follow_redirects=True)
    gdb = os.environ["GUTLOG_DB"]

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    meds = {}
    for nm, mol in (("Test Cipro 500", "ciprofloxacin"),  # in the regimen, current
                    ("Test Tizan 2", "tizanidine"),       # schedule ENDED 8 days ago
                    ("Test Omep 20", "omeprazole"),       # in the regimen, unknown to RxGuard
                    ("Test Dompi 10", "domperidone")):    # not scheduled, but taken 2 days ago
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 1, mol))
        meds[nm] = gq("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]

    for nm in ("Test Cipro 500", "Test Tizan 2", "Test Omep 20"):
        gc.post("/api/schedule", json={"med_id": meds[nm], "slot": "MORNING",
                                       "dose_text": "1 tab", "valid_from": Y30})
    sid = [s for s in gc.get("/api/schedule").get_json()["rows"]
           if s["med_id"] == meds["Test Tizan 2"]][0]["id"]
    gc.post("/api/schedule/close/" + str(sid), json={})
    gq("UPDATE med_schedule SET valid_to=? WHERE id=?", (Y8, sid))
    gc.post("/api/now/dose", json={"med_id": meds["Test Dompi 10"], "status": "EXTRA",
                                   "day": Y2, "dtime": "09:00"})

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, gmod.app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # ---- RxGuard pointed at it ------------------------------------------
    os.environ["GUTLOG_FEED_URL"] = "http://127.0.0.1:%d" % port
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = tokf
    os.environ["RXGUARD_GUTLOG_FEED"] = "1"
    sys.path.insert(0, here)
    rspec = importlib.util.spec_from_file_location("rxguard_under_test",
                                                   os.path.join(here, "app.py"))
    rx = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(rx)
    rdb = os.path.join(work, "r.db")
    rapp = rx.create_app(db_path=rdb, secret="t")
    rapp.config["TESTING"] = True
    rc = rapp.test_client()
    rc.post("/login", data={"password": "testpassword1"})

    con = sqlite3.connect(rdb)
    for k, dose in (("ciprofloxacin", "500 mg"), ("tizanidine", "2 mg"),
                    ("ramipril", "5 mg"), ("domperidone", "10 mg")):
        con.execute("INSERT INTO medications(drug_key,raw_name,dose,status,start_date) "
                    "VALUES(?,?,?,'active',?)", (k, k, dose, Y30))
    con.commit()
    con.close()

    def rq(sql, a=()):
        c2 = sqlite3.connect(rdb)
        c2.row_factory = sqlite3.Row
        r = c2.execute(sql, a).fetchall()
        c2.close()
        return r

    def view():
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            data, err = rx.gutlog_stack(14)
            assert not err, "feed error: " + str(err)
            return rx.astaken_view(data), data

    ctx = {}

    def t00_feed_carries_the_end_date():
        v, data = view()
        ctx["v"] = v
        assert data.get("ended") is not None, \
            "GutLog is older than v3.12.0: no `ended` list on the feed"
        ends = dict((e["molecule"], e["valid_to"]) for e in data["ended"])
        assert ends.get("tizanidine") == Y8, str(ends)
        reg = dict((r["molecule"], r.get("valid_from")) for r in data["regimen"])
        assert reg.get("ciprofloxacin") == Y30, str(reg)
        assert "tizanidine" not in reg, "an ended schedule is still in the regimen"
        return "ended tizanidine on " + Y8 + ", regimen carries valid_from"

    def t01_fires_on_all_three_conditions_only():
        """active/tapering here AND absent from GutLog's regimen AND no dose
        for 7+ days. Drop any one and the line must not appear."""
        keys = [r["key"] for r in ctx["v"]["rec"]["stopped"]]
        assert "tizanidine" in keys, "the ended medicine did not raise a line: " + str(keys)
        assert "ramipril" in keys, "a medicine GutLog never saw did not raise a line"
        assert "ciprofloxacin" not in keys, "a current regimen medicine raised a line"
        assert "domperidone" not in keys, \
            "a medicine dosed 2 days ago raised a line -- the 7-day test is not applied"
        return "2 of 4 fire: ended + never-seen; current and recently-dosed do not"

    def t02_the_line_carries_gutlogs_date():
        row = [r for r in ctx["v"]["rec"]["stopped"] if r["key"] == "tizanidine"][0]
        assert row["valid_to"] == Y8, "valid_to " + str(row["valid_to"])
        assert row["status"] == "active", str(row["status"])
        bare = [r for r in ctx["v"]["rec"]["stopped"] if r["key"] == "ramipril"][0]
        assert bare["valid_to"] == "", \
            "a stop date was invented for a drug GutLog never carried"
        return "tizanidine offers " + Y8 + "; ramipril offers no date at all"

    def t03_the_mirror_case():
        miss = dict((r["key"], r) for r in ctx["v"]["rec"]["missing"])
        assert "omeprazole" in miss, "GutLog's regimen medicine is not reported: " + str(list(miss))
        assert miss["omeprazole"]["valid_from"] == Y30, str(miss["omeprazole"])
        assert "ciprofloxacin" not in miss and "tizanidine" not in miss, str(list(miss))
        return "omeprazole reported as in GutLog since " + Y30 + ", not on the list"

    def t04_findings_on_a_stale_drug_are_marked_not_dropped():
        fs = ctx["v"]["findings"]
        stale = [f for f in fs if f.get("gut_stale")]
        assert stale, "no finding was marked possibly-stale"
        assert any("tizanidine" in f["gut_stale"] for f in stale), \
            "tizanidine findings not marked: " + str([f["title"] for f in stale])
        for f in stale:
            assert f["flag"] in ("RED", "AMBER"), f["flag"]
        # the two GutLog has not seen: one ended 8 days ago, one it never had
        gone = {"tizanidine", "ramipril"}
        involving = [f for f in fs if f["flag"] in ("RED", "AMBER")
                     and (set(f.get("involves") or []) & gone)]
        assert involving, "the fixture raised no finding on a stale drug at all"
        missed = [f["title"] for f in involving if not f.get("gut_stale")]
        assert not missed, "a finding on a stale drug was not marked: " + str(missed)
        assert len(stale) == len(involving), \
            "a finding resting only on current drugs was marked stale: " + \
            str([f["title"] for f in stale if not (set(f.get("involves") or []) & gone)])
        assert any(f["flag"] == "RED" for f in stale), \
            "the RED that rests on a stopped drug was not marked: " + \
            str([(f["flag"], f["title"]) for f in stale])
        ctx["stale_titles"] = [f["flag"] + " " + f["title"] for f in stale]
        return str(len(stale)) + " marked: " + "; ".join(ctx["stale_titles"])[:80]

    def t05_nothing_was_written_by_itself():
        rows = dict((r["drug_key"], r["status"]) for r in
                    rq("SELECT drug_key, status FROM medications"))
        assert rows == {"ciprofloxacin": "active", "tizanidine": "active",
                        "ramipril": "active", "domperidone": "active"}, str(rows)
        assert not rq("SELECT * FROM med_events"), "an event was written without a tap"
        return "reading the mismatch changed nothing: a drug record must not edit itself"

    def t06_the_page_offers_the_tap():
        r = rc.get("/astaken")
        assert r.status_code == 200, str(r.status_code)
        h = r.get_data(as_text=True)
        assert "GutLog feed unavailable" not in h, "feed reported down"
        assert "Reconciliation" in h, "no reconciliation section on the page"
        assert "Mark stopped on " + Y8 in h, "the one-tap stop date is not GutLog's"
        assert "Mark stopped on " + TODAY not in h, "today's date was offered"
        assert "Possibly stale" in h, "the stale marking does not reach the page"
        assert "Add as active, started " + Y30 in h, "the mirror case offers no tap"
        return "page offers 'Mark stopped on " + Y8 + "' and the inverted case"

    def t07_one_tap_stops_it_on_gutlogs_date():
        r = rc.post("/reconcile", data={"action": "stop", "drug_key": "tizanidine",
                                        "days": "14"}, follow_redirects=True)
        assert r.status_code == 200, str(r.status_code)
        m = rq("SELECT status, stop_date, last_change FROM medications "
               "WHERE drug_key='tizanidine'")[0]
        assert m["status"] == "stopped", str(m["status"])
        assert m["stop_date"] == Y8, "stop_date is " + str(m["stop_date"]) + ", not GutLog's " + Y8
        assert m["stop_date"] != TODAY, "stopped as of today instead of the real day"
        assert m["last_change"] == TODAY, "the day it was confirmed is not recorded"
        ev = rq("SELECT event_date, action, source FROM med_events "
                "WHERE drug_key='tizanidine'")
        assert len(ev) == 1 and ev[0]["action"] == "stop", str([dict(e) for e in ev])
        assert ev[0]["event_date"] == Y8 and ev[0]["source"] == "gutlog", str(dict(ev[0]))
        return "status stopped, stop_date " + Y8 + " from valid_to, event dated the same"

    def t08_no_date_means_no_one_tap_stop():
        r = rc.post("/reconcile", data={"action": "stop", "drug_key": "ramipril",
                                        "days": "14"}, follow_redirects=True)
        h = r.get_data(as_text=True)
        m = rq("SELECT status, stop_date FROM medications WHERE drug_key='ramipril'")[0]
        assert m["status"] == "active", "stopped without a date to stop it on"
        assert not m["stop_date"], str(m["stop_date"])
        assert "records no end date" in h, "no explanation was given: " + h[:0]
        return "refused, and said why, rather than stopping it as of today"

    def t09_one_tap_adds_the_mirror_case():
        rc.post("/reconcile", data={"action": "add", "drug_key": "omeprazole",
                                    "days": "14"}, follow_redirects=True)
        m = rq("SELECT status, start_date, raw_name FROM medications "
               "WHERE drug_key='omeprazole'")
        assert len(m) == 1, str(len(m)) + " omeprazole rows"
        assert m[0]["status"] == "active", str(m[0]["status"])
        assert m[0]["start_date"] == Y30, \
            "start_date is " + str(m[0]["start_date"]) + ", not GutLog's valid_from " + Y30
        return "added as active from " + Y30 + ", GutLog's own start date"

    def t10_the_lines_clear_once_applied():
        v, _data = view()
        keys = [r["key"] for r in v["rec"]["stopped"]]
        assert "tizanidine" not in keys, "still asking about a medicine now stopped"
        assert "ramipril" in keys, "the undated case must stay on the list"
        assert not [r for r in v["rec"]["missing"] if r["key"] == "omeprazole"], \
            "still asking to add a medicine now on the list"
        return "applied lines disappear; the one needing a decision stays"

    def t11_a_second_tap_changes_nothing():
        before = [dict(r) for r in rq("SELECT * FROM med_events")]
        rc.post("/reconcile", data={"action": "stop", "drug_key": "tizanidine",
                                    "days": "14"}, follow_redirects=True)
        rc.post("/reconcile", data={"action": "add", "drug_key": "omeprazole",
                                    "days": "14"}, follow_redirects=True)
        after = [dict(r) for r in rq("SELECT * FROM med_events")]
        assert before == after, "a repeated tap wrote again: " + str(len(after) - len(before))
        assert len(rq("SELECT * FROM medications WHERE drug_key='omeprazole'")) == 1
        return "idempotent: the test no longer holds, so nothing happens"

    def t12_engine_now_sees_the_change():
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            keys = [m["drug_key"] for m in rx.active_meds()]
        assert "tizanidine" not in keys, "active_meds still carries the stopped drug"
        assert "omeprazole" in keys, "active_meds still misses the added drug"
        v, _d = view()
        assert not any("tizanidine" in (f.get("gut_stale") or "") for f in v["findings"]), \
            "a stale marking survived the reconciliation"
        return "the engine runs on active_meds(), and active_meds() has moved"

    tests = [
        ("00 feed carries GutLog's end date", t00_feed_carries_the_end_date),
        ("01 fires on all three conditions only", t01_fires_on_all_three_conditions_only),
        ("02 the line carries GutLog's date", t02_the_line_carries_gutlogs_date),
        ("03 the mirror case, inverted", t03_the_mirror_case),
        ("04 stale findings marked, not dropped", t04_findings_on_a_stale_drug_are_marked_not_dropped),
        ("05 nothing written without a tap", t05_nothing_was_written_by_itself),
        ("06 the page offers the tap", t06_the_page_offers_the_tap),
        ("07 one tap stops it on GutLog's date", t07_one_tap_stops_it_on_gutlogs_date),
        ("08 no date means no one-tap stop", t08_no_date_means_no_one_tap_stop),
        ("09 one tap adds the mirror case", t09_one_tap_adds_the_mirror_case),
        ("10 applied lines clear", t10_the_lines_clear_once_applied),
        ("11 a second tap changes nothing", t11_a_second_tap_changes_nothing),
        ("12 the engine now sees the change", t12_engine_now_sees_the_change),
    ]
    print("=" * 70)
    print("RxGuard v1.5.0 - GutLog reconciliation (real GutLog on loopback)")
    print("=" * 70)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 70)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    try:
        srv.shutdown()
    except Exception:
        pass
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

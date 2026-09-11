#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.4.0 -- conditions from GutLog's health record.

New conditions and rules CR010-CR015 in the engine; kb_sync's condition
sync (tick, untick only its own, never touch hand-set ones); and the whole
path from a REAL GutLog (scratch database, scratch token, scratch profile)
on loopback. Nothing live is touched. Synthetic fixture. Python 3.9.

  python3 test_conditions.py [/root/gutlog/app.py]   -> must print 10/10 passed
"""
import importlib.util
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading

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
    gut_path = sys.argv[1] if len(sys.argv) > 1 else "/root/gutlog/app.py"
    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(os.path.abspath(gut_path)), GUTLOG_FEED_TOKEN_FILE=tokf)
    sys.path.insert(0, os.path.dirname(os.path.abspath(gut_path)))
    gspec = importlib.util.spec_from_file_location("gutlog_c", gut_path)
    G = importlib.util.module_from_spec(gspec)
    gspec.loader.exec_module(G)
    G.PROFILE_FILE = os.path.join(work, "records_profile.local.json")
    json.dump({"conditions": ["thrombocytopenia", "conduction_disease", "coronary_disease", "not_a_code"]},
              open(G.PROFILE_FILE, "w"))
    G.app.test_client().post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"})
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, G.app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    os.environ["GUTLOG_FEED_URL"] = "http://127.0.0.1:%d" % port
    sys.path.insert(0, here)
    rspec = importlib.util.spec_from_file_location("app", os.path.join(here, "app.py"))
    rx = importlib.util.module_from_spec(rspec)
    sys.modules["app"] = rx
    rspec.loader.exec_module(rx)
    rdb = os.path.join(work, "r.db")
    os.environ["RXGUARD_DB"] = rdb
    rapp = rx.create_app(db_path=rdb, secret="t")
    rapp.config["TESTING"] = True
    dead = "http://127.0.0.1:9"
    os.environ.update(RXGUARD_KB_CACHE=os.path.join(work, "cache"), RXGUARD_RXNAV=dead, RXGUARD_OPENFDA=dead,
                      RXGUARD_FDA_CYP_URL=dead, RXGUARD_DDINTER=dead, RXGUARD_PVPI_URL=dead)
    ks = importlib.util.spec_from_file_location("kb_sync_c", os.path.join(here, "kb_sync.py"))
    kb = importlib.util.module_from_spec(ks)
    ks.loader.exec_module(kb)

    def q(sql, a=()):
        con = sqlite3.connect(rdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def set_conditions(*codes):
        q("UPDATE conditions SET active=0")
        for c in codes:
            q("INSERT OR IGNORE INTO conditions(code, active) VALUES(?,0)", (c,))
            q("UPDATE conditions SET active=1 WHERE code=?", (c,))

    def ids(drug, *codes, meds=()):
        set_conditions(*codes)
        q("DELETE FROM medications")
        for m in meds:
            q("INSERT INTO medications(drug_key,raw_name,status) VALUES(?,?,'active')", (m, m))
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            return set(f.get("rule_id") for f in rx.analyse(drug)["findings"] if f.get("rule_id"))

    def t00_rules_loaded():
        got = [r["id"] for r in rx.CONDITION_RULES][-6:]
        assert got == ["CR010", "CR011", "CR012", "CR013", "CR014", "CR015"], str(got)
        assert rx.RULES_DOC["_meta"]["version"] == "1.1.0"
        assert all(r.get("source") and r.get("reviewed") for r in rx.CONDITION_RULES), "rule without source/date"
        for c in ("thrombocytopenia", "hyponatraemia", "hypocalcaemia", "conduction_disease", "coronary_disease"):
            assert c in rx.CONDITION_LABELS, c
        return "6 sourced rules, 5 new conditions, rules v1.1.0"

    def t01_nsaid():
        a = ids("diclofenac", "coronary_disease", "thrombocytopenia")
        assert {"CR014", "CR010"} <= a, str(a)
        assert not ({"CR014", "CR010"} & ids("diclofenac")), "fired without the condition"
        return "NSAID: coronary disease + low platelets both flagged; nothing without the conditions"

    def t02_conduction():
        assert "CR013" in ids("diltiazem", "conduction_disease"), "rate-slowing drug with conduction disease"
        assert "CR013" not in ids("losartan", "conduction_disease"), "fired on a drug that does not slow"
        return "diltiazem flagged with conduction disease; losartan not"

    def t03_sodium():
        assert "CR011" in ids("sertraline", "hyponatraemia")
        assert "CR011" in ids("fluoxetine", "hyponatraemia")
        assert "CR011" not in ids("rosuvastatin", "hyponatraemia")
        return "two SSRIs flagged with low sodium; statin not"

    def t04_calcium_steroid():
        assert "CR012" in ids("escitalopram", "hypocalcaemia"), "QT drug + low calcium"
        assert "CR015" in ids("prednisolone", "diabetes"), "steroid + diabetes"
        return "QT drug with low calcium; steroid with diabetes"

    def t05_sync():
        con = sqlite3.connect(rdb)
        con.execute("UPDATE conditions SET active=0")
        con.execute("UPDATE conditions SET active=1 WHERE code='constipation'")
        con.commit()
        r1 = kb.sync_conditions(con, ["thrombocytopenia", "coronary_disease", "bogus"])
        act = set(x[0] for x in con.execute("SELECT code FROM conditions WHERE active=1"))
        assert act == {"constipation", "thrombocytopenia", "coronary_disease"}, str(act)
        r2 = kb.sync_conditions(con, ["thrombocytopenia"])
        act = set(x[0] for x in con.execute("SELECT code FROM conditions WHERE active=1"))
        con.close()
        assert act == {"constipation", "thrombocytopenia"} and r2["cleared"] == ["coronary_disease"], str(act)
        assert "bogus" not in r1["set"]
        return "ticks GutLog's codes, unticks only its own, leaves hand-set constipation alone"

    def t06_live_rule():
        os.environ["RXGUARD_GUTLOG_FEED"] = ""
        assert kb.gutlog_profile() is None, "scratch database read the GutLog profile"
        return "scratch database never reads the profile feed"

    def t07_feed_end_to_end():
        os.environ["RXGUARD_GUTLOG_FEED"] = "1"
        codes = kb.gutlog_profile()
        assert codes == ["thrombocytopenia", "conduction_disease", "coronary_disease", "not_a_code"], str(codes)
        con = sqlite3.connect(rdb)
        st = kb.sync_conditions(con, codes)
        con.close()
        assert st["set"] == ["thrombocytopenia", "conduction_disease", "coronary_disease"], str(st)
        return "real GutLog profile feed -> three conditions ticked, unknown code ignored"

    def t08_run_carries_it():
        con = sqlite3.connect(rdb)
        con.executescript(rx.SCHEMA)
        st = kb.run(con, data={"ok": True, "regimen": [], "taken": []}, now="2026-09-12 07:00",
                    ddi_budget=0, profile=["hyponatraemia"])
        con.close()
        assert st["conditions"]["set"] == ["hyponatraemia"], str(st.get("conditions"))
        return "the 30-minute sync runs the condition step"

    def t09_profile_page():
        set_conditions("conduction_disease")
        c = rapp.test_client()
        c.post("/login", data={"password": "testpassword1"})
        r = c.get("/profile")
        h = r.get_data(as_text=True)
        assert r.status_code == 200 and "Conduction disease" in h and "Low platelet count" in h, \
            "new conditions missing from the profile page (%d)" % r.status_code
        return "new conditions appear on the profile page"

    tests = [("00 rules and conditions", t00_rules_loaded), ("01 NSAID", t01_nsaid), ("02 conduction", t02_conduction),
             ("03 sodium", t03_sodium), ("04 calcium, steroid", t04_calcium_steroid), ("05 sync", t05_sync),
             ("06 live-database rule", t06_live_rule), ("07 GutLog feed end to end", t07_feed_end_to_end),
             ("08 sync step in run", t08_run_carries_it), ("09 profile page", t09_profile_page)]
    print("=" * 66)
    print("RxGuard v1.4.0 - conditions test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    srv.shutdown()
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

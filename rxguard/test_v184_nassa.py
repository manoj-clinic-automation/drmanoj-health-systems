#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.4 -- the NaSSA entry, the MAO-inhibitor contraindication, a
start check, and as-needed medicines counted only when taken
(RXGUARD_V184_NASSA).

Scratch database, the real engine and knowledge base. This file names NO
medicine: the NaSSA is the "a" side of rule PW035, the others are the "b"
sides of the new rules or are found by class or by marker. The GutLog dose
feed is replaced by a stub so "taken today" is decided here, not by a diary.
Python 3.9.

  python3 test_v184_nassa.py [path/to/rxguard/app.py]
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
    rdb = os.path.join(work, "r.db")
    os.environ["RXGUARD_DB"] = rdb
    os.environ["RXGUARD_GUTLOG_FEED"] = ""
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("app", app_path)
    rx = importlib.util.module_from_spec(spec)
    sys.modules["app"] = rx
    spec.loader.exec_module(rx)
    rapp = rx.create_app(db_path=rdb, secret="t")
    rapp.config["TESTING"] = True
    FEED = {"events": None}

    def fake_doses(days=1):
        if FEED["events"] is None:
            return None, "no feed in this test"
        return {"ok": True, "events": FEED["events"]}, None
    rx.gutlog_doses = fake_doses

    def q(sql, a=()):
        con = sqlite3.connect(rdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    with rapp.test_request_context():
        from flask import g
        g.db_path = rdb
        rx.get_db()

    by_id = dict((r["id"], r) for r in rx.PAIRWISE)
    NASSA = by_id["PW035"]["a"] if "PW035" in by_id else "?"

    def other(rid):
        return by_id[rid]["b"]

    def of_class(word):
        for k, v in sorted(rx.DRUGS.items()):
            if word in (v.get("class") or "").lower():
                return k
        raise AssertionError("no drug of class %r" % word)

    def run(drug, meds=(), conds=(), stopped=(), action="start"):
        q("DELETE FROM medications")
        q("UPDATE conditions SET active=0")
        for c in conds:
            q("INSERT OR IGNORE INTO conditions(code, active) VALUES(?,0)", (c,))
            q("UPDATE conditions SET active=1 WHERE code=?", (c,))
        for m in meds:
            key, kind = (m if isinstance(m, tuple) else (m, "chronic"))
            q("INSERT INTO medications(drug_key,raw_name,status,kind) VALUES(?,?,'active',?)", (key, key, kind))
        for key, day in stopped:
            q("INSERT INTO medications(drug_key,raw_name,status,stop_date) VALUES(?,?,'stopped',?)", (key, key, day))
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            return rx.analyse(drug, action=action)

    def rules(res):
        return dict((f.get("rule_id"), f["flag"]) for f in res["findings"] if f.get("rule_id"))

    def t01():
        d = rx.get_drug(NASSA)
        assert d and "(NaSSA)" in d["class"], "the NaSSA entry is missing"
        assert d["strengths"] == ["3.75 mg", "7.5 mg", "15 mg", "30 mg", "45 mg"], d.get("strengths")
        n = d["notes"]
        for bit in ("3.75–7.5 mg", "sedation falls", "agranulocytosis", "glycaemic",
                    "benzodiazepines and alcohol", "Z-drugs", "CYP2D6"):
            assert bit in n, "the notes do not say %r" % bit
        return "strengths and every note the owner asked for"
    check("01 the NaSSA entry carries its strengths and notes", t01)

    def t02():
        for rid in ("PW035", "PW036", "PW037"):
            assert rules(run(NASSA, meds=[other(rid)])).get(rid) == "AMBER", rid
        for rid in ("PW038", "PW039", "PW040", "PW040B"):
            assert rules(run(NASSA, meds=[other(rid)])).get(rid) == "AMBER", rid
        return "strong CYP3A4 inhibitors and inducers: AMBER, with the label's dose advice"
    check("02 CYP3A inhibitors and inducers are flagged", t02)

    def t03():
        maoi = sorted(rx.mao_keys())[0] if hasattr(rx, "mao_keys") else "?"
        f = [x for x in run(NASSA, meds=[maoi])["findings"] if x["category"] == "Contraindication"]
        assert f and f[0]["flag"] == "RED", "no RED with a current MAO inhibitor"
        recent = (date.today() - timedelta(days=5)).isoformat()
        f = [x for x in run(NASSA, stopped=[(maoi, recent)])["findings"] if x["category"] == "Contraindication"]
        assert f and "under 14 days" in f[0]["mechanism"], "no RED within 14 days of stopping one"
        old = (date.today() - timedelta(days=30)).isoformat()
        f = [x for x in run(NASSA, stopped=[(maoi, old)])["findings"] if x["category"] == "Contraindication"]
        assert not f, "fired 30 days after the MAO inhibitor stopped"
        f = [x for x in run(maoi, meds=[NASSA])["findings"] if x["category"] == "Contraindication"]
        assert f and f[0]["flag"] == "RED", "no RED when the MAO inhibitor is the one proposed"
        return "RED with one current or stopped under 14 days; not at 30 days; both directions"
    check("03 the MAO-inhibitor contraindication fires", t03)

    def t04():
        f = [x for x in run(NASSA)["findings"] if x["category"] == "Monitoring"]
        assert f and "Sodium" in f[0]["title"] and "2–3 weeks" in f[0]["monitoring"], f
        f = [x for x in run(NASSA, action="increase")["findings"] if x["category"] == "Monitoring"]
        assert not f, "the start check appears on a dose change"
        return "sodium check 2-3 weeks after starting; only on start"
    check("04 starting it owes a sodium check", t04)

    def t05():
        for rid in ("PW041", "PW042", "PW043", "PW044", "PW045"):
            assert rules(run(NASSA, meds=[other(rid)])).get(rid) == "AMBER", rid
        for rid in ("PW046", "PW047"):
            assert rules(run(NASSA, meds=[other(rid)])).get(rid) == "AMBER", rid
        assert rules(run(NASSA, conds=["diabetes"])).get("CR017") == "AMBER", "no diabetes rule"
        return "Z-drugs, benzodiazepines, serotonergic caution, diabetes"
    check("05 sedation, serotonergic and diabetes rules fire", t05)

    def t06():
        opioid = of_class("opioid")
        FEED["events"] = []                       # the feed answers: nothing taken today
        res = run(NASSA, meds=[(opioid, "episodic")])
        sed = [x for x in res["findings"] if x["title"].startswith("Sedation")]
        assert res.get("not_counted") and not sed, \
            "an as-needed drug not taken today still counted: %r / %r" % (res.get("not_counted"), [x["title"] for x in sed])
        FEED["events"] = [{"day": rx.dose_now().date().isoformat(), "name": opioid, "molecule": opioid}]
        res = run(NASSA, meds=[(opioid, "episodic")])
        assert not res.get("not_counted") and [x for x in res["findings"] if x["title"].startswith("Sedation")], \
            "taken today, but not counted"
        FEED["events"] = None                     # the feed cannot be read
        res = run(NASSA, meds=[(opioid, "episodic")])
        assert not res.get("not_counted"), "a missing feed made the burden smaller"
        FEED["events"] = []
        res = run(NASSA, meds=[(opioid, "chronic")])
        assert not res.get("not_counted"), "a regular medicine was left out"
        return "as needed: out when not taken today, in when taken, in when the feed is down; regular: always in"
    check("06 an as-needed medicine counts only on a day it was taken", t06)

    print("")
    for ok, name, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))
    bad = [r for r in RESULTS if not r[0]]
    print("-" * 66)
    print("%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
    print("RESULT: " + ("FAILURES" if bad else "ALL PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

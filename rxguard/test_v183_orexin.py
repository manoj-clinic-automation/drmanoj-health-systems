#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.3 -- an orexin receptor antagonist and narcolepsy
(RXGUARD_V183_LEMBOREXANT).

Scratch database, the real engine and knowledge base. Checks that the entry
is there and that each interaction the label names produces its flag, and
that a harmless neighbour does not.

This file names NO medicine. Every drug it uses is read out of the knowledge
base -- the new entry is the "a" side of rule PW025, the interacting drugs
are the "b" sides of rules PW025-PW034, and the neighbours are found by
class -- so it can never put a name from his list into the public tree.
Python 3.9.

  python3 test_v183_orexin.py [path/to/rxguard/app.py]
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

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
    NEW = by_id["PW025"]["a"] if "PW025" in by_id else "?"

    def other(rid):
        return by_id[rid]["b"]

    def of_class(word, skip=()):
        for k, v in sorted(rx.DRUGS.items()):
            if word in (v.get("class") or "").lower() and k not in skip:
                return k
        raise AssertionError("no drug of class %r in the knowledge base" % word)

    def run(drug, meds=(), conds=()):
        q("DELETE FROM medications")
        q("UPDATE conditions SET active=0")
        for c in conds:
            q("INSERT OR IGNORE INTO conditions(code, active) VALUES(?,0)", (c,))
            q("UPDATE conditions SET active=1 WHERE code=?", (c,))
        for m in meds:
            q("INSERT INTO medications(drug_key,raw_name,status) VALUES(?,?,'active')", (m, m))
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            return rx.analyse(drug)

    def rules(res):
        return dict((f.get("rule_id"), f["flag"]) for f in res["findings"] if f.get("rule_id"))

    def t01():
        d = rx.get_drug(NEW)
        assert d, "the new orexin antagonist is not in the knowledge base"
        assert d["class"].startswith("Dual orexin receptor antagonist"), d["class"]
        assert d["strengths"] == ["5 mg", "10 mg"], d.get("strengths")
        assert d["cyp"]["substrate"].get("CYP3A4") == "major", d["cyp"]
        assert d["burden"]["sedation"] == 3 and d["rxcui"] == "2272403"
        syn = [k for k, v in rx.SYNONYMS.items() if v == NEW]
        assert syn and rx.norm_key(syn[0].capitalize()) == NEW, "the brand name does not resolve"
        assert run(NEW)["known"], "analyse() treats it as unknown"
        return "class, 5/10 mg, CYP3A4 major substrate, sedation 3, RxCUI, brand resolves"
    check("01 the orexin antagonist is in the knowledge base", t01)

    def t02():
        ids = ["PW025", "PW026", "PW027", "PW028", "PW029", "PW030"]
        for rid in ids:
            r = rules(run(NEW, meds=[other(rid)]))
            assert r.get(rid) == "RED", "%s: %r" % (rid, r)
        calm = of_class("angiotensin")
        assert not [f for f in run(NEW, meds=[calm])["findings"] if f["flag"] == "RED"], \
            "a non-interacting drug raised RED"
        return "RED 'avoid' with all six CYP3A inhibitors; nothing with an unrelated drug"
    check("02 strong or moderate CYP3A inhibitors are RED", t02)

    def t03():
        for rid in ("PW031", "PW031B"):
            r = rules(run(NEW, meds=[other(rid)]))
            assert r.get(rid) == "RED", "%s: %r" % (rid, r)
        r = rules(run(other("PW025"), meds=[NEW]))
        assert r.get("PW025") == "RED", "the rule does not fire from the other side: %r" % r
        return "the inducer, under both its names, RED; rules fire whichever drug is proposed"
    check("03 a strong CYP3A inducer is RED", t03)

    def t04():
        benzo = of_class("benzodiazepine")
        f = run(NEW, meds=[benzo])["findings"]
        sed = [x for x in f if x["category"] == "Cumulative burden" and x["title"].startswith("Sedation")]
        assert sed and sed[0]["flag"] in ("AMBER", "RED"), "no sedation finding with a benzodiazepine"
        opioid = of_class("opioid")
        f = run(NEW, meds=[opioid])["findings"]
        assert [x for x in f if x["title"].startswith("Sedation")], "no sedation finding with an opioid"
        r = rules(run(NEW, meds=[other("PW032")]))
        assert r.get("PW032") == "AMBER", r
        return "sedation burden with a benzodiazepine and an opioid; Z-drug rule AMBER"
    check("04 additive sedation is flagged", t04)

    def t05():
        assert "narcolepsy" in rx.CONDITION_LABELS, "narcolepsy is not a condition"
        assert rules(run(NEW, conds=["narcolepsy"])).get("CR016") == "RED"
        assert "CR016" not in rules(run(NEW)), "fired without the condition"
        return "RED with narcolepsy recorded; nothing without it"
    check("05 narcolepsy is a contraindication", t05)

    def t06():
        f = run(NEW, conds=["hepatic_impairment"])["findings"]
        h = [x for x in f if "hepatic" in x["title"]]
        assert h and "Severe hepatic impairment: not recommended" in h[0]["mechanism"], h
        assert "5 mg" in h[0]["mechanism"]
        return "hepatic impairment: moderate max 5 mg, severe not recommended"
    check("06 hepatic impairment carries the label's limits", t06)

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

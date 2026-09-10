#!/usr/bin/env python3
"""
RxGuard smoke tests. Runs against a throwaway SQLite database using Flask's
test client. No network, no external services.

THE FIXTURE IS SYNTHETIC. Every drug, dose, indication, prescriber, date and
symptom below is invented to exercise a rule, not drawn from anyone's record.
This repository is public.

The index case is a strong CYP2D6 inhibitor (paroxetine) alongside a
narrow-therapeutic-index 2D6 substrate (nortriptyline), plus a rate-lowering
calcium channel blocker (diltiazem), against a profile carrying ventricular
ectopy, constipation and hypertension. That combination is what drives the
rules under test:

    PW001  paroxetine raises nortriptyline exposure (RED, sets the flag)
    PW002  paroxetine blocks tramadol activation      (step 6)
    CR001  constipating burden >= 3 on constipation   (2 + 1 + 1 = 4)
    CR002  QT-affecting drug on ventricular ectopy    (nortriptyline)
    CR004  BP-raising drug on hypertension            (nortriptyline)
    WD001  discontinuation syndrome on stopping       (step 7)
    WD002  stopping an inhibitor raises substrate clearance

Paroxetine and nortriptyline are named because PW001 and PW002 are defined on
that exact pair -- the pair IS the rule, and rules.json is unchanged. They
identify a textbook interaction, not a person.
"""

import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as rxguard  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(step, name, cond, detail=""):
    results.append((step, name, PASS if cond else FAIL, detail))
    return cond


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    application = rxguard.create_app(db_path=path, secret="test-secret")
    application.config["TESTING"] = True
    c = application.test_client()

    # 1 ---------------------------------------------------- boot and health
    r = c.get("/healthz")
    check(1, "App boots and health endpoint responds", r.status_code == 200)
    r = c.get("/", follow_redirects=False)
    check(1, "Unauthenticated root redirects to login", r.status_code == 302)

    # 2 ------------------------------------------------- first-run password
    r = c.post("/login", data={"password": "correct-horse-battery"},
               follow_redirects=True)
    check(2, "First-run sets password and signs in", r.status_code == 200
          and b"Dashboard" in r.data)
    with application.app_context():
        pass
    r = c.get("/logout", follow_redirects=True)
    r = c.post("/login", data={"password": "wrong"}, follow_redirects=True)
    check(2, "Wrong password rejected", b"Incorrect password" in r.data)
    r = c.post("/login", data={"password": "correct-horse-battery"},
               follow_redirects=True)
    check(2, "Correct password accepted", b"Dashboard" in r.data)

    # 3 -------------------------------------------------- name normalising
    with application.app_context():
        rxguard.g.db_path = path
        check(3, "Misspelling 'paroxitene' resolves",
              rxguard.norm_key("paroxitene") == "paroxetine")
        check(3, "Misspelling 'amitryptiline' resolves",
              rxguard.norm_key("amitryptiline") == "amitriptyline")
        check(3, "Salt suffix stripped ('nortriptyline HCl')",
              rxguard.norm_key("Nortriptyline HCl") == "nortriptyline")
        check(3, "Unknown molecule stays unresolved",
              rxguard.get_drug(rxguard.norm_key("zzzfakedrug")) is None)

    # 4 ------------------------------------------------------ build ledger
    # Synthetic ledger. Doses, indications and prescribers are placeholders
    # chosen to satisfy the form, not transcribed from any record.
    for drug, dose, freq, ind, pres, spec in [
        ("nortriptyline", "25 mg", "HS", "neuropathic pain", "Dr One", "neurology"),
        ("paroxetine", "20 mg", "OD", "anxiety", "Dr Two", "psychiatry"),
        ("diltiazem", "60 mg", "TDS", "rate control", "Dr Three", "cardiology"),
    ]:
        c.post("/meds", data={"drug": drug, "dose": dose, "frequency": freq,
                              "indication": ind, "prescriber": pres, "specialty": spec,
                              "kind": "chronic", "benefit": "none", "source": "phone"},
               follow_redirects=True)
    r = c.get("/meds")
    check(4, "Three chronic drugs recorded",
          all(x in r.data for x in (b"nortriptyline", b"paroxetine", b"diltiazem")))

    # conditions
    c.post("/profile", data={"form": "conditions", "ventricular_ectopy": "1",
                             "constipation": "1", "hypertension": "1"},
           follow_redirects=True)
    r = c.get("/profile")
    check(4, "Conditions saved", b"checked" in r.data)

    # Synthetic constraint text. It must contain "ventricular ectopy" because
    # the dashboard assertion below and condition rules CR002/CR003 key on
    # that condition; the surrounding narrative is invented.
    c.post("/profile", data={"form": "constraint",
                             "text": "Test fixture: documented ventricular ectopy, "
                                     "constipation tendency and hypertension on file."},
           follow_redirects=True)
    r = c.get("/")
    check(4, "Constraints card shows on dashboard", b"ventricular ectopy" in r.data)

    # 5 ------------------------------------- INDEX CASE: the mandatory test
    with application.app_context():
        rxguard.g.db_path = path
        res = rxguard.analyse("nortriptyline", action="increase", dose="25 mg",
                              frequency="HS", indication="neuropathic pain")
    titles = " || ".join(f["title"] for f in res["findings"])
    blob = json.dumps(res).lower()

    check(5, "Index case returns RED", res["flag"] == "RED", res["flag"])
    check(5, "Surfaces CYP2D6 relevance", "cyp2d6" in blob or "2d6" in blob)
    check(5, "Surfaces paroxetine/nortriptyline interaction by name",
          any("PW001" == f.get("rule_id") for f in res["findings"]))
    check(5, "Surfaces anticholinergic or constipating burden",
          "anticholinergic" in blob or "constipating" in blob)
    check(5, "Links constipation constraint to the burden",
          "constipation is a recorded personal constraint" in blob)
    check(5, "Surfaces cardiac / QT consideration",
          "qt" in blob or "conduction" in blob or "ectopy" in blob)
    check(5, "Ventricular ectopy condition rule fires",
          any(f.get("rule_id", "").startswith("CR00") for f in res["findings"]))
    check(5, "Produces a prescriber discussion line",
          len(res["discussion"]) > 40 and "nortriptyline" in res["discussion"].lower())
    check(5, "Does not assert proven causation",
          not any(w in blob for w in ("definitely caused", "proven to cause",
                                      "caused the tachycardia")))
    check(5, "No GREEN state exists anywhere",
          "green" not in blob and res["flag"] in ("RED", "AMBER", "UNKNOWN"))

    # 6 ------------------------------------------- episodic self-treatment
    with application.app_context():
        rxguard.g.db_path = path
        tram = rxguard.analyse("tramadol", action="start", dose="50 mg",
                               indication="radicular pain")
    tblob = json.dumps(tram).lower()
    check(6, "Tramadol on paroxetine flags RED", tram["flag"] == "RED", tram["flag"])
    check(6, "Explains failed activation and serotonergic rise",
          "active metabolite" in tblob and "serotonergic" in tblob)
    check(6, "Warns against dose escalation", "escalat" in tblob)

    with application.app_context():
        rxguard.g.db_path = path
        cipro = rxguard.analyse("tizanidine", action="start",
                                extra_keys=["ciprofloxacin"])
    check(6, "Tizanidine + ciprofloxacin flags RED", cipro["flag"] == "RED")

    with application.app_context():
        rxguard.g.db_path = path
        nsaid = rxguard.analyse("diclofenac", action="start", indication="MSK pain")
    nblob = json.dumps(nsaid).lower()
    check(6, "NSAID on SSRI flags GI bleeding risk", "bleeding" in nblob)
    check(6, "NSAID with hypertension flags BP effect",
          "blood pressure" in nblob or "blood-pressure" in nblob)

    # quick-check route end to end
    r = c.post("/episode", data={"drugs": "etoricoxib, thiocolchicoside"},
               follow_redirects=True)
    check(6, "Quick check route returns findings", r.status_code == 200
          and (b"RED" in r.data or b"AMBER" in r.data))

    # 7 ----------------------------------------- withdrawal / mirror effect
    with application.app_context():
        rxguard.g.db_path = path
        stop = rxguard.analyse("paroxetine", action="stop")
    sblob = json.dumps(stop).lower()
    check(7, "Stopping paroxetine flags discontinuation syndrome",
          "discontinuation" in sblob)
    check(7, "Stopping an inhibitor flags falling substrate levels",
          "clearance" in sblob or "levels fall" in sblob or "subtherapeutic" in sblob)
    check(7, "Names nortriptyline as the affected substrate",
          "nortriptyline" in sblob)

    # 8 ------------------------- adverse history, symptom timeline, review
    # Synthetic adverse event. It exercises structured temporality
    # (latency, dechallenge, rechallenge) and the causality grading that
    # follows from it -- the values are invented for that purpose.
    c.post("/adverse", data={
        "drug": "nortriptyline", "symptom": "test symptom for causality grading",
        "onset_date": "2026-01-15", "dose_at_onset": "25 mg", "latency_days": "18",
        "dechallenge": "yes", "dechallenge_resolved": "yes",
        "rechallenge": "no", "confounders": "", "severity": "moderate",
        "notes": "Fixture: no benefit recorded, to exercise the review path."},
        follow_redirects=True)
    r = c.get("/adverse")
    check(8, "Adverse event stored with structured temporality", b"18" in r.data)
    check(8, "Causality assessed, not asserted",
          b"probable" in r.data or b"possible" in r.data)

    with application.app_context():
        rxguard.g.db_path = path
        again = rxguard.analyse("nortriptyline", action="increase", dose="50 mg")
    check(8, "Personal history resurfaces on re-proposal",
          any(f["category"] == "Personal history" for f in again["findings"]))
    check(8, "History labelled as personal observation, not universal effect",
          "personal historical observation" in json.dumps(again).lower())

    r = c.post("/symptom", data={"symptom": "palpitations", "save": "1"},
               follow_redirects=True)
    check(8, "Symptom screen ranks recent drug changes", r.status_code == 200
          and b"nortriptyline" in r.data)

    r = c.post("/overrides", data={"analysis_id": "1", "finding_title": "Test finding",
                                   "flag": "AMBER", "reason": "benefit outweighs"},
               follow_redirects=True)
    check(8, "Override logs and schedules review", b"Review queue" in r.data)
    r = c.get("/reviews")
    check(8, "Review queue shows the scheduled item", b"benefit outweighs" in r.data)

    r = c.get("/card")
    check(8, "One-page list renders", r.status_code == 200 and b"nortriptyline" in r.data)
    r = c.get("/knowledge")
    check(8, "Knowledge base page renders", r.status_code == 200)

    # coverage honesty
    with application.app_context():
        rxguard.g.db_path = path
        unk = rxguard.analyse("zzzfakedrug", action="start")
    check(8, "Unknown molecule returns UNKNOWN, not silence",
          unk["flag"] == "UNKNOWN" and not unk["known"])

    os.unlink(path)

    # ------------------------------------------------------------- report
    width = 66
    print("=" * width)
    print("RxGuard smoke test")
    print("=" * width)
    cur = None
    for step, name, status, detail in results:
        if step != cur:
            cur = step
            print("\n[%d]" % step)
        print("  %-4s %-52s %s" % (status, name[:52], detail))
    failed = [r for r in results if r[2] == FAIL]
    print("\n" + "=" * width)
    print("%d checks, %d passed, %d failed" % (len(results),
                                               len(results) - len(failed), len(failed)))
    print("=" * width)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

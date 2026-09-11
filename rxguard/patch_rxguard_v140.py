#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.3.0 -> v1.4.0  ::  your conditions, from GutLog's health record

  * Five new conditions: low platelet count, low sodium, low ionic calcium,
    conduction disease (e.g. bundle branch block), coronary artery disease.
  * Six new condition rules (CR010-CR015), each sourced:
      bleeding-risk drug + low platelets; sodium-lowering drug + low sodium;
      QT drug + low calcium; rate/AV-slowing drug + conduction disease;
      NSAID + coronary disease; corticosteroid + diabetes.
    A rule may now name its drugs directly (trigger "drugs").
  * kb_sync.py (shipped alongside) reads GutLog's /api/feed/profile -- codes
    only -- every 30 minutes and ticks those conditions; a code GutLog stops
    listing is unticked. Conditions you set by hand are left alone.

Patches app.py and knowledge/rules.json together (both or neither).
Requires v1.3.0. Anchor-verified, idempotent, compile-checked, .bak before
write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import json
import os
import py_compile
import shutil
import sys
import tempfile

DIR = "/root/rxguard"
MARKER = "RXGUARD_V140_CONDITIONS"
PREV = "RXGUARD_V130_REVIEW"

NEW_CONDITIONS = '''    ("seizure_history", "Seizure history"),
    ("thrombocytopenia", "Low platelet count"),
    ("hyponatraemia", "Low sodium (recent or recurrent)"),
    ("hypocalcaemia", "Low ionic calcium"),
    ("conduction_disease", "Conduction disease (e.g. bundle branch block)"),
    ("coronary_disease", "Coronary artery disease"),
'''

RULES = [
    {"id": "CR010", "condition": "thrombocytopenia", "trigger": {"burden": "bleeding", "min": 1}, "flag": "AMBER",
     "title": "Bleeding-risk drug with a low platelet count",
     "consequence": "The drug's bleeding tendency adds to the low platelet count: bruising, gastrointestinal or "
                    "procedural bleeding.",
     "action": "Prefer a drug without bleeding burden; for pain, paracetamol within hepatic limits before any NSAID. "
               "Check a recent platelet count before starting.",
     "source": "Class labelling (NSAIDs, SSRIs/SNRIs, antiplatelets, anticoagulants).", "reviewed": "2026-09-11"},
    {"id": "CR011", "condition": "hyponatraemia",
     "trigger": {"drugs": ["paroxetine", "sertraline", "escitalopram", "citalopram", "fluoxetine", "venlafaxine",
                           "duloxetine", "hydrochlorothiazide", "chlorthalidone", "indapamide", "amitriptyline",
                           "nortriptyline", "mirtazapine", "carbamazepine", "oxcarbazepine", "desmopressin"]},
     "flag": "AMBER", "title": "Sodium-lowering drug with a history of low sodium",
     "consequence": "Recurrence or worsening of hyponatraemia (SIADH or salt loss): fatigue, confusion, falls.",
     "monitoring": "Sodium before starting and one to two weeks after.",
     "source": "Product labelling (SSRIs, SNRIs, thiazides, TCAs, mirtazapine, carbamazepine/oxcarbazepine, "
               "desmopressin).", "reviewed": "2026-09-11"},
    {"id": "CR012", "condition": "hypocalcaemia", "trigger": {"qt_any": True}, "flag": "AMBER",
     "title": "QT-affecting drug with low calcium",
     "consequence": "Low calcium prolongs the QT interval on its own and adds to any drug effect.",
     "action": "Correct calcium, vitamin D and magnesium first where possible; ECG if the drug is a known QT prolonger.",
     "source": "Established QT risk-factor frameworks (CredibleMeds; product labelling).", "reviewed": "2026-09-11"},
    {"id": "CR013", "condition": "conduction_disease", "trigger": {"hr": "decrease"}, "flag": "AMBER",
     "title": "Rate- or AV-slowing drug with conduction disease",
     "consequence": "Additive slowing of conduction: symptomatic bradycardia or higher-grade AV block, especially "
                    "with two such drugs together.",
     "monitoring": "ECG and pulse after starting or combining.",
     "source": "Class labelling (beta-blockers, verapamil/diltiazem, digoxin, ivabradine).", "reviewed": "2026-09-11"},
    {"id": "CR014", "condition": "coronary_disease",
     "trigger": {"drugs": ["diclofenac", "ibuprofen", "naproxen", "etoricoxib", "aceclofenac", "celecoxib",
                           "ketorolac", "indomethacin", "mefenamic_acid", "nimesulide"]},
     "flag": "AMBER", "title": "NSAID with coronary artery disease",
     "consequence": "NSAIDs, COX-2 selective agents included, raise the risk of myocardial infarction and stroke and "
                    "raise blood pressure.",
     "action": "Avoid, or the shortest course at the lowest dose; paracetamol first.",
     "source": "FDA boxed warning: NSAID cardiovascular thrombotic events.", "reviewed": "2026-09-11"},
    {"id": "CR015", "condition": "diabetes",
     "trigger": {"drugs": ["prednisolone", "methylprednisolone", "dexamethasone", "hydrocortisone", "deflazacort",
                           "betamethasone"]},
     "flag": "AMBER", "title": "Corticosteroid with diabetes",
     "consequence": "Raised blood sugar during and just after the course.",
     "monitoring": "Glucose checks during the course, especially afternoon and evening.",
     "source": "Class labelling (systemic corticosteroids).", "reviewed": "2026-09-11"},
]


def rules_text():
    parts = []
    for r in RULES:
        parts.append("    " + json.dumps(r, ensure_ascii=False))
    return ",\n" + ",\n".join(parts)


def app_edits():
    return [
        ("version", 'APP_VERSION = "1.3.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES RXGUARD_V130_REVIEW',
         'APP_VERSION = "1.4.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES RXGUARD_V130_REVIEW ' + MARKER),
        ("conditions", '    ("seizure_history", "Seizure history"),\n', NEW_CONDITIONS),
        ("drugs trigger", '        if "burden" in t:\n            hit = totals.get(t["burden"], 0) >= t.get("min", 1)\n',
         '        if "burden" in t:\n            hit = totals.get(t["burden"], 0) >= t.get("min", 1)\n'
         '        elif "drugs" in t:\n            hit = proposed_key in t["drugs"]\n'),
    ]


RULES_ANCHOR = ('      "source": "Class labelling.", "reviewed": "2026-07-25"\n    }\n  ],\n\n  "burden_thresholds": {')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DIR)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    fa = os.path.join(args.dir, "app.py")
    fr = os.path.join(args.dir, "knowledge", "rules.json")
    print("=" * 60)
    print("RxGuard conditions from the health record -> v1.4.0")
    print("dir  : " + args.dir)
    print("=" * 60)
    for f in (fa, fr):
        if not os.path.exists(f):
            print("FATAL: not found: " + f)
            return 1
    sa = open(fa, encoding="utf-8").read()
    sr = open(fr, encoding="utf-8").read()
    if MARKER in sa and '"CR010"' in sr:
        print("Already patched. Nothing to do.")
        return 0
    if MARKER in sa or '"CR010"' in sr:
        print("FATAL: only one of app.py / rules.json is patched -- unexpected state. Nothing written.")
        return 1
    if PREV not in sa:
        print("FATAL: app.py is not at v1.3.0. Apply that first.")
        return 1
    if not os.path.exists(os.path.join(args.dir, "kb_sync.py")) or \
            "sync_conditions" not in open(os.path.join(args.dir, "kb_sync.py"), encoding="utf-8").read():
        print("FATAL: the v1.4.0 kb_sync.py must be copied beside app.py first.")
        return 1
    E = app_edits()
    bad = [l for l, a, n in E if sa.count(a) != 1]
    if sr.count(RULES_ANCHOR) != 1:
        bad.append("rules.json anchor")
    if sr.count('"version": "1.0.0"') != 1:
        bad.append("rules.json version")
    print("anchors: %d/%d matched" % (len(E) + 2 - len(bad), len(E) + 2))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + "\nRefusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0
    oa = sa
    for l, a, n in E:
        oa = oa.replace(a, n, 1)
    head = '      "source": "Class labelling.", "reviewed": "2026-07-25"\n    }'
    orr = sr.replace(RULES_ANCHOR, head + rules_text() + '\n  ],\n\n  "burden_thresholds": {', 1)
    orr = orr.replace('"version": "1.0.0"', '"version": "1.1.0"', 1)
    try:
        doc = json.loads(orr)
        ids = [r["id"] for r in doc["condition_rules"]]
        assert ids[-6:] == [r["id"] for r in RULES] and len(set(ids)) == len(ids)
    except Exception as exc:
        print("RULES CHECK FAILED, nothing written: %s" % exc)
        return 2
    tmpd = tempfile.mkdtemp()
    try:
        t = os.path.join(tmpd, "app.py")
        with open(t, "w", encoding="utf-8") as fh:
            fh.write(oa)
        py_compile.compile(t, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    baks = []
    for f in (fa, fr):
        b = f + ".bak-v140-" + stamp
        shutil.copy2(f, b)
        baks.append((f, b))
        print("backup : " + b)
    try:
        with open(fa, "w", encoding="utf-8") as fh:
            fh.write(oa)
        with open(fr, "w", encoding="utf-8") as fh:
            fh.write(orr)
        py_compile.compile(fa, doraise=True)
    except Exception as exc:
        for f, b in baks:
            shutil.copy2(b, f)
        print("POST-WRITE FAILURE. Both files restored.\n" + str(exc))
        return 2
    print("applied: %d app edits + %d rules" % (len(E), len(RULES)))
    print("-" * 60)
    print("Next:  systemctl restart rxguard")
    print("Back:  " + " ; ".join("cp " + b + " " + f for f, b in baks))
    return 0


if __name__ == "__main__":
    sys.exit(main())

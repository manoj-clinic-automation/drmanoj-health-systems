#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.2 -> v1.8.3  ::  RXGUARD_V183_LEMBOREXANT -- lemborexant in the
curated knowledge base, and narcolepsy as a condition.

What the knowledge base did not know on 23-Sep-2026: lemborexant (DAYVIGO),
a dual orexin receptor antagonist. Checked in the curated files and in the
server's approved overlay before adding. Every statement below is from the
US label (DAYVIGO SPL, read through openFDA on 23-Sep-2026); RxCUI 2272403
from NLM RxNorm.

  knowledge/drugs.json  (medsafe-drugs 1.0.0 -> 1.1.0)
    lemborexant: CYP3A4 major substrate; sedation 3; 5 and 10 mg; start
    5 mg, max 10 mg once nightly; max 5 mg with a weak CYP3A inhibitor;
    moderate hepatic impairment max 5 mg, SEVERE NOT RECOMMENDED. Synonym
    "dayvigo".
  knowledge/rules.json  (medsafe-rules 1.1.0 -> 1.2.0)
    PW025-PW031  RED  avoid with strong or moderate CYP3A inhibitors --
                      clarithromycin, itraconazole, ketoconazole, diltiazem,
                      verapamil, fluconazole (the knowledge base's own
                      moderate CYP3A4 inhibitor) -- and with rifampicin /
                      rifampin (strong inducer, loss of effect).
    PW032-PW034  AMBER additive CNS depression with the Z-drugs.
                 Opioids and benzodiazepines are covered by the existing
                 sedation-burden threshold (lemborexant contributes 3);
                 alcohol is not a medicine, so it lives in the notes and in
                 every one of these rules' actions.
    CR016        RED  lemborexant (and the class) with narcolepsy.
  app.py
    ("narcolepsy", "Narcolepsy") in CONDITIONS, and the version.

A curated rule always beats the property engine for the same pair (see
analyse()), so clarithromycin, diltiazem and verapamil read with the label's
"avoid" wording rather than the derived RED/AMBER.

Anchor-verified, idempotent, .bak of all three files before any write, JSON
parse-checked before writing, compile-checked, self-restoring, --reverse
(app.py, for the negative control). Python 3.9.
"""
import argparse
import datetime
import json
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V183_LEMBOREXANT"
PREV = "RXGUARD_V182_SECRETFILE"
VERSION = "1.8.3"
REVIEWED = "2026-09-23"
SRC = ("US SPL, DAYVIGO (lemborexant), read through openFDA 2026-09-23")

# ------------------------------------------------------------------- app.py
APP_EDITS = [
    ("version", 'APP_VERSION = "1.8.2"   # RXGUARD_V182_SECRETFILE ',
     'APP_VERSION = "1.8.3"   # RXGUARD_V183_LEMBOREXANT RXGUARD_V182_SECRETFILE '),
    ("narcolepsy condition", '    ("coronary_disease", "Coronary artery disease"),\n]\n',
     '    ("coronary_disease", "Coronary artery disease"),\n'
     '    ("narcolepsy", "Narcolepsy"),   # RXGUARD_V183_LEMBOREXANT\n]\n'),
]

# ------------------------------------------------------------- drugs.json
LEMB = {
    "class": "Dual orexin receptor antagonist (hypnotic)",
    "atc": "N05CJ02", "rxcui": "2272403",
    "cyp": {"substrate": {"CYP3A4": "major"}, "inhibitor": {}, "inducer": {}},
    "burden": {"anticholinergic": 0, "serotonergic": 0, "sedation": 3, "constipating": 0,
               "bleeding": 0, "nephrotoxic": 0, "seizure": 0},
    "qt": "none", "hr": "none", "bp": "none",
    "strengths": ["5 mg", "10 mg"],
    "hepatic": ("Moderate hepatic impairment: initial and maximum dose 5 mg. "
                "Severe hepatic impairment: not recommended."),
    "notes": ("Tablets 5 mg and 10 mg. Recommended 5 mg no more than once a night, immediately "
              "before bed with at least 7 hours before planned awakening; maximum 10 mg once "
              "daily. Maximum 5 mg with a weak CYP3A inhibitor; avoid with strong or moderate "
              "CYP3A inhibitors and with strong or moderate CYP3A inducers. Additive CNS "
              "depression with other CNS depressants; no alcohol with it. Contraindicated in "
              "narcolepsy. Next-day impairment is possible, more so at 10 mg."),
    "source": SRC + "; RxCUI from NLM RxNorm.",
    "reviewed": REVIEWED,
}
D_OLD = '    }\n\n  },\n\n  "synonyms": {\n'
D_NEW = ('    },\n\n    "lemborexant": ' + json.dumps(LEMB, ensure_ascii=False) + '\n\n  },\n\n'
         '  "synonyms": {\n    "dayvigo": "lemborexant",\n')
DV_OLD = '"schema": "medsafe-drugs/1",\n    "version": "1.0.0",'
DV_NEW = '"schema": "medsafe-drugs/1",\n    "version": "1.1.0",'

# ------------------------------------------------------------- rules.json
AVOID_ACT = ("Avoid the combination (label). If both are truly needed, the prescriber decides; "
             "no alcohol with lemborexant in any case.")


def _inh(rid, other, strength):
    return {"id": rid, "a": "lemborexant", "b": other, "flag": "RED",
            "title": "%s (%s CYP3A inhibitor): avoid with lemborexant" % (other.capitalize(), strength),
            "mechanism": ("Lemborexant is cleared mainly by CYP3A4. %s is a %s CYP3A inhibitor, so "
                          "lemborexant exposure rises on an unchanged dose." % (other.capitalize(), strength)),
            "consequence": ("More sedation and next-day impairment: drowsiness on waking, slowed "
                            "reactions, falls, driving risk."),
            "monitoring": "Next-morning alertness if the two ever overlap.",
            "action": AVOID_ACT,
            "source": SRC + ": 'Avoid concomitant use of DAYVIGO with strong or moderate CYP3A inhibitors.'",
            "reviewed": REVIEWED}


def _ind(rid, other):
    return {"id": rid, "a": "lemborexant", "b": other, "flag": "RED",
            "title": "%s (strong CYP3A inducer): avoid with lemborexant" % other.capitalize(),
            "mechanism": ("Lemborexant is cleared mainly by CYP3A4. %s strongly induces CYP3A, so "
                          "lemborexant exposure falls." % other.capitalize()),
            "consequence": "Loss of effect: the dose that worked stops working, without any change in the dose.",
            "monitoring": "Sleep response while the inducer is taken and for about two weeks after it stops.",
            "action": AVOID_ACT,
            "source": SRC + ": 'Avoid concomitant use of DAYVIGO with strong or moderate CYP3A inducers.'",
            "reviewed": REVIEWED}


def _cns(rid, other):
    return {"id": rid, "a": "lemborexant", "b": other, "flag": "AMBER",
            "title": "%s with lemborexant: additive CNS depression" % other.capitalize(),
            "mechanism": "Two hypnotics acting by different mechanisms; the sedative effects add.",
            "consequence": "Excess sedation, complex sleep behaviours, next-day impairment, falls.",
            "monitoring": "Next-morning alertness; avoid driving until the combination is known.",
            "action": "Usually one hypnotic, not two. No alcohol with either.",
            "source": SRC + ": co-administration with other CNS depressants increases the risk of CNS depression.",
            "reviewed": REVIEWED}


NEW_PAIRS = [_inh("PW025", "clarithromycin", "strong"), _inh("PW026", "itraconazole", "strong"),
             _inh("PW027", "ketoconazole", "strong"), _inh("PW028", "diltiazem", "moderate"),
             _inh("PW029", "verapamil", "moderate"), _inh("PW030", "fluconazole", "moderate"),
             _ind("PW031", "rifampicin"), dict(_ind("PW031B", "rifampin")),
             _cns("PW032", "zolpidem"), _cns("PW033", "zopiclone"), _cns("PW034", "eszopiclone")]
NEW_COND = {"id": "CR016", "condition": "narcolepsy",
            "trigger": {"drugs": ["lemborexant", "suvorexant", "daridorexant"]},
            "flag": "RED", "title": "Orexin receptor antagonist with narcolepsy: contraindicated",
            "consequence": "Orexin loss is the cause of narcolepsy; blocking the receptor can worsen it.",
            "action": "Do not use. Choose a different hypnotic.",
            "source": SRC + ": 'DAYVIGO is contraindicated in patients with narcolepsy.' Class labelling.",
            "reviewed": REVIEWED}

P_OLD = '    }\n  ],\n\n  "condition_rules": [\n'
P_NEW = ('    },\n' + ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in NEW_PAIRS)
         + '\n  ],\n\n  "condition_rules": [\n')
C_OLD = '"reviewed": "2026-09-11"}\n  ],\n\n  "burden_thresholds": {\n'
C_NEW = ('"reviewed": "2026-09-11"},\n    ' + json.dumps(NEW_COND, ensure_ascii=False)
         + '\n  ],\n\n  "burden_thresholds": {\n')
RV_OLD = '"schema": "medsafe-rules/1",\n    "version": "1.1.0",'
RV_NEW = '"schema": "medsafe-rules/1",\n    "version": "1.2.0",'

KB_EDITS = {
    "drugs.json": [("lemborexant entry", D_OLD, D_NEW), ("drugs version", DV_OLD, DV_NEW)],
    "rules.json": [("pairwise rules", P_OLD, P_NEW), ("condition rule", C_OLD, C_NEW),
                   ("rules version", RV_OLD, RV_NEW)],
}


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def apply_edits(src, edits):
    bad = [(l, src.count(o)) for l, o, n in edits if src.count(o) != 1]
    if bad:
        return None, bad
    for l, o, n in edits:
        src = src.replace(o, n, 1)
    return src, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)
    kdir = os.path.join(os.path.dirname(os.path.abspath(a.file)), "knowledge")

    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(APP_EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " app.py -> " + a.reverse + " (knowledge files are not reversed)")
        return 0

    print("=" * 66)
    print("RxGuard lemborexant + narcolepsy -> v" + VERSION)
    print("=" * 66)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    new_app, bad = apply_edits(src, APP_EDITS)
    outs = {}
    for fname, edits in KB_EDITS.items():
        p = os.path.join(kdir, fname)
        s = read(p)
        if "lemborexant" in s and fname == "drugs.json":
            print("FATAL: drugs.json already names lemborexant. Nothing written.")
            return 1
        o, b = apply_edits(s, edits)
        bad += [(fname + ": " + l, c) for l, c in b]
        outs[p] = o
    print("anchors: %s" % ("all matched" if not bad else "MISSING"))
    for l, c in bad:
        print("  %s: found %d times, need 1" % (l, c))
    if bad:
        print("Refusing to patch. Nothing written.")
        return 1
    for p, o in outs.items():
        try:
            json.loads(o)
        except ValueError as exc:
            print("JSON CHECK FAILED for %s, nothing written: %s" % (p, exc))
            return 2
    tmpd = tempfile.mkdtemp()
    try:
        cand = os.path.join(tmpd, "cand.py")
        write(cand, new_app)
        py_compile.compile(cand, doraise=True)
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    print("json + compile check: OK")
    if a.check:
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    baks = []
    for p in [a.file] + list(outs):
        b = p + ".bak-v183-" + stamp
        shutil.copy2(p, b)
        baks.append((p, b))
    for p, o in outs.items():
        write(p, o)
    write(a.file, new_app)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        for p, b in baks:
            shutil.copy2(b, p)
        print("POST-WRITE COMPILE FAILED. All three restored.\n" + str(exc))
        return 2
    for p, b in baks:
        print("backup : " + b)
    print("applied: app.py %d edits, drugs.json 2, rules.json 3" % len(APP_EDITS))
    return 0


if __name__ == "__main__":
    sys.exit(main())

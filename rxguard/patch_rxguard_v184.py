#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.3 -> v1.8.4  ::  RXGUARD_V184_NASSA -- the NaSSA entry filled
in from its label, the MAO-inhibitor contraindication made to fire, a start
check, and as-needed medicines counted only on a day they were taken.

ENGINE (app.py)
  * analyse(): an as-needed row (kind 'episodic' or 'prn') counts toward a
    burden total and the QT sum only if GutLog logged a dose of it TODAY
    (IST). Pairwise and CYP checks still see it -- a rarely-taken drug still
    interacts on the day it is taken. When GutLog cannot be read, it counts:
    a missing feed must never make a burden look smaller. What was left out
    is named on every burden finding and returned as `not_counted`.
  * mao_findings(): the `mao_inhibitor` marker was in the base and read by
    nothing. Now a drug marked `mao_contraindicated` with any MAO inhibitor
    -- current, or stopped within 14 days (the label's interval) -- is RED,
    whichever of the two is proposed. MAO inhibitors are the base's marked
    entries plus `mao_inhibitor_keys` in rules.json, so a molecule not in the
    base still counts by name.
  * start_check_findings(): a drug may carry `start_checks` -- a monitoring
    step owed after starting it -- shown when the action is start.

KNOWLEDGE (drugs.json 1.1.0 -> 1.2.0, rules.json 1.2.0 -> 1.3.0)
  The NaSSA entry: strengths, the sleep-dose note, mao_contraindicated, a
  sodium check 2-3 weeks after starting, notes for appetite and weight,
  glucose, agranulocytosis, benzodiazepines, Z-drugs and alcohol -- from its
  US label (openFDA, 23-Sep-2026) except where marked as the owner's.
  PW035-PW037 AMBER strong CYP3A4 inhibitors (label: a dose decrease may be
  needed); PW038-PW040B AMBER strong CYP3A inducers (a dose increase may be
  needed, and a decrease when the inducer stops); PW041-PW043 AMBER Z-drugs;
  PW044-PW045 AMBER benzodiazepines (label: avoid); PW046-PW047 AMBER
  serotonergic caution. CR017 AMBER with diabetes. `mao_inhibitor_keys`.

  NOT added, on purpose: CYP2D6 inhibitors. The label reports that
  paroxetine "did not cause relevant changes in the pharmacokinetics" of the
  drug, so a rule saying it raises levels would contradict its own source.
  Recorded in the notes instead.

Anchor-verified, idempotent, .bak of all three files, JSON parse-checked,
compile-checked, self-restoring, --reverse (app.py). Python 3.9.
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
MARKER = "RXGUARD_V184_NASSA"
PREV = "RXGUARD_V183_LEMBOREXANT"
VERSION = "1.8.4"
REVIEWED = "2026-09-23"
SRC = "US SPL (mirtazapine), read through openFDA 2026-09-23"
KEY = "mirtazapine"

# ------------------------------------------------------------------- app.py
FUNCS = r'''# RXGUARD_V184_NASSA -- three engine pieces the knowledge base now needs.
PRN_KINDS = ("episodic", "prn")


def prn_untaken_today(keys):
    """As-needed rows among `keys` with no dose logged in GutLog today (IST).
    When GutLog cannot be read nothing is left out -- a missing feed must
    never make a burden total look smaller than it is."""
    prn = set()
    for m in active_meds():
        if m["drug_key"] in keys and (m["kind"] or "") in PRN_KINDS:
            prn.add(m["drug_key"])
    if not prn:
        return []
    data, err = gutlog_doses(days=1)
    if err or not data:
        return []
    day = dose_now().date().isoformat()
    taken = set()
    for ev in data.get("events") or []:
        if ev.get("day") != day:
            continue
        taken.update(_split_molecules(ev.get("molecule") or ""))
        taken.add(norm_key(ev.get("name") or ""))
    return sorted(k for k in prn if k not in taken)


def mao_keys():
    ks = set(k for k, d in DRUGS.items() if d.get("mao_inhibitor"))
    ks.update(RULES_DOC.get("mao_inhibitor_keys") or [])
    return ks


def mao_findings(proposed_key, other_keys):
    """A drug marked mao_contraindicated with an MAO inhibitor -- current, or
    stopped within 14 days -- is RED, whichever of the two is proposed."""
    maoi = mao_keys()
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    pool = [(k, "on the current list") for k in other_keys]
    for r in get_db().execute(
            "SELECT drug_key, stop_date FROM medications WHERE status='stopped' "
            "AND COALESCE(stop_date,'')>=?", (cutoff,)).fetchall():
        if r["drug_key"] not in other_keys:
            pool.append((r["drug_key"], "stopped on %s, under 14 days ago" % r["stop_date"]))
    p = get_drug(proposed_key) or {}
    out = []
    for k, when in pool:
        if k == proposed_key:
            continue
        o = get_drug(k) or {}
        if (p.get("mao_contraindicated") and k in maoi) or \
                (proposed_key in maoi and o.get("mao_contraindicated")):
            drug, inh = (proposed_key, k) if k in maoi else (k, proposed_key)
            out.append(finding(
                "RED", "Contraindication",
                "%s with an MAO inhibitor (%s): contraindicated" % (display_name(drug), display_name(inh)),
                mechanism="%s is %s. At least 14 days must pass between stopping an MAO "
                          "inhibitor and starting %s." % (display_name(inh), when, display_name(drug)),
                consequence="Serotonin syndrome and hypertensive reactions.",
                action="Do not combine. Keep the 14-day gap.",
                source=(get_drug(drug) or {}).get("source", "Product labelling."),
                reviewed=(get_drug(drug) or {}).get("reviewed", "")))
    return out


def start_check_findings(proposed_key, action):
    """A monitoring step the knowledge base says is owed after starting."""
    if action != "start":
        return []
    d = get_drug(proposed_key) or {}
    out = []
    for c in d.get("start_checks") or []:
        out.append(finding(
            c.get("flag", "AMBER"), "Monitoring", c.get("title", "Check after starting"),
            consequence=c.get("consequence", ""), monitoring=c.get("monitoring", ""),
            action=c.get("action", ""), source=d.get("source", ""), reviewed=d.get("reviewed", "")))
    return out


'''

APP_EDITS = [
    ("version", 'APP_VERSION = "1.8.3"   # RXGUARD_V183_LEMBOREXANT ',
     'APP_VERSION = "1.8.4"   # RXGUARD_V184_NASSA RXGUARD_V183_LEMBOREXANT '),
    ("engine pieces", 'def analyse(proposed_key, action="start", dose="", frequency="", indication="",\n',
     FUNCS + 'def analyse(proposed_key, action="start", dose="", frequency="", indication="",\n'),
    ("as-needed counting",
     '    if action == "stop":\n'
     '        burden_keys = [k for k in all_keys if k != proposed_key]\n'
     '    else:\n'
     '        burden_keys = all_keys\n'
     '\n'
     '    totals, contributors = compute_burdens(burden_keys)\n',
     '    if action == "stop":\n'
     '        burden_keys = [k for k in all_keys if k != proposed_key]\n'
     '    else:\n'
     '        burden_keys = all_keys\n'
     '    # RXGUARD_V184_NASSA -- an as-needed medicine counts toward a total only\n'
     '    # on a day it was taken; pairwise and CYP checks still see it.\n'
     '    not_counted = prn_untaken_today([k for k in burden_keys if k != proposed_key])\n'
     '    burden_keys = [k for k in burden_keys if k not in not_counted]\n'
     '\n'
     '    totals, contributors = compute_burdens(burden_keys)\n'),
    ("new findings",
     '    findings += history_findings(proposed_key)\n'
     '    findings += withdrawal_findings(proposed_key, action, other_keys)\n',
     '    if action != "stop":\n'
     '        findings += mao_findings(proposed_key, other_keys)\n'
     '        findings += start_check_findings(proposed_key, action)\n'
     '    if not_counted:\n'
     '        left = ", ".join(display_name(k) for k in not_counted)\n'
     '        for f in findings:\n'
     '            if f["category"] in ("Cumulative burden", "QT / conduction"):\n'
     '                f["mechanism"] += (" Not counted: %s (as needed, no dose logged today)." % left)\n'
     '    findings += history_findings(proposed_key)\n'
     '    findings += withdrawal_findings(proposed_key, action, other_keys)\n'),
    ("result field", '        "considered": [display_name(k) for k in all_keys],\n',
     '        "considered": [display_name(k) for k in all_keys],\n'
     '        "not_counted": [display_name(k) for k in not_counted],\n'),
]

# ------------------------------------------------------------- drugs.json
M_OLD = ('''    "mirtazapine": {
      "class": "Noradrenergic and specific serotonergic antidepressant", "atc": "N06AX11", "rxcui": "15996",
      "cyp": {"substrate": {"CYP3A4": "major", "CYP2D6": "minor", "CYP1A2": "minor"}, "inhibitor": {}, "inducer": {}},
      "burden": {"anticholinergic": 1, "serotonergic": 1, "sedation": 3, "constipating": 2, "bleeding": 1, "nephrotoxic": 0, "seizure": 1},
      "qt": "conditional", "hr": "none", "bp": "decrease", "withdrawal": "moderate",
      "notes": "Strongly sedating at low dose. Weight gain. Blocks 5HT2/5HT3 so serotonin-syndrome contribution is lower than SSRI/SNRI but not zero.",
      "source": "US SPL.", "reviewed": "2026-07-25"
    },
''')
START = [{"flag": "AMBER", "title": "Sodium check 2–3 weeks after starting",
          "consequence": ("Hyponatraemia (SIADH) can follow a serotonergic antidepressant; the label "
                          "names older age, diuretics and volume depletion as greater risk, and it is "
                          "most often seen over 55."),
          "monitoring": ("Serum sodium 2–3 weeks after starting, sooner with headache, confusion, "
                         "unsteadiness, nausea or weakness.")}]
NOTES = ("Sleep: the useful dose is 3.75–7.5 mg, where antihistamine (H1) action dominates; "
         "noradrenergic activity rises with the dose and sedation falls, so raising the dose for "
         "sleep is the wrong move (owner's note). 3.75 mg is half a 7.5 mg tablet; the US label "
         "lists 7.5, 15, 30 and 45 mg. Appetite increase in 17% vs 2% and weight gain of 7% or "
         "more in 7.5% (label); may worsen glycaemic control. Rare agranulocytosis: with a sore "
         "throat, fever, mouth ulcers or other signs of infection, check the blood count and stop "
         "if the white count is low (label). Avoid benzodiazepines and alcohol (label); Z-drugs add "
         "sedation too. MAO inhibitors: contraindicated, and 14 days must pass after stopping one "
         "(label). CYP2D6: the label reports that paroxetine did not change its pharmacokinetics "
         "relevantly, so CYP2D6 inhibitors are not flagged. QT: the label found no clinically "
         "meaningful QTc change at 75 mg; kept at 'conditional' as before. Blocks 5HT2/5HT3, so its "
         "serotonin-syndrome contribution is lower than an SSRI/SNRI but not zero.")
M_NEW = ('''    "mirtazapine": {
      "class": "Noradrenergic and specific serotonergic antidepressant (NaSSA)", "atc": "N06AX11", "rxcui": "15996",
      "cyp": {"substrate": {"CYP3A4": "major", "CYP2D6": "minor", "CYP1A2": "minor"}, "inhibitor": {}, "inducer": {}},
      "burden": {"anticholinergic": 1, "serotonergic": 1, "sedation": 3, "constipating": 2, "bleeding": 1, "nephrotoxic": 0, "seizure": 1},
      "qt": "conditional", "hr": "none", "bp": "decrease", "withdrawal": "moderate",
      "strengths": ["3.75 mg", "7.5 mg", "15 mg", "30 mg", "45 mg"],
      "mao_contraindicated": true,
      "start_checks": ''' + json.dumps(START, ensure_ascii=False) + ''',
      "notes": ''' + json.dumps(NOTES, ensure_ascii=False) + ''',
      "source": "''' + SRC + '''; the 3.75 mg strength and the sleep-dose note are the owner's.", "reviewed": "''' + REVIEWED + '''"
    },
''')
DV_OLD = '"schema": "medsafe-drugs/1",\n    "version": "1.1.0",'
DV_NEW = '"schema": "medsafe-drugs/1",\n    "version": "1.2.0",'


# ------------------------------------------------------------- rules.json
def _rule(rid, other, flag, title, mech, cons, act, quote):
    return {"id": rid, "a": KEY, "b": other, "flag": flag, "title": title, "mechanism": mech,
            "consequence": cons, "action": act, "source": SRC + ": '" + quote + "'",
            "reviewed": REVIEWED}


def _inh(rid, other):
    return _rule(rid, other, "AMBER",
                 "%s (strong CYP3A4 inhibitor) raises mirtazapine levels" % other.capitalize(),
                 "Mirtazapine is cleared largely by CYP3A4; %s strongly inhibits it." % other.capitalize(),
                 "More sedation, dizziness and other dose-related effects on an unchanged dose.",
                 "A lower mirtazapine dose may be needed while both are taken, and a higher one again when the inhibitor stops.",
                 "A decrease in dosage of mirtazapine tablets may be needed with concomitant use of strong CYP3A4 inhibitors.")


def _ind(rid, other):
    return _rule(rid, other, "AMBER",
                 "%s (strong CYP3A inducer) lowers mirtazapine levels" % other.capitalize(),
                 "Enzyme induction speeds mirtazapine clearance.",
                 "Loss of effect on an unchanged dose -- and a rise when the inducer is stopped.",
                 "A higher mirtazapine dose may be needed with the inducer, and a lower one again when it stops.",
                 "An increase in dosage of mirtazapine tablets may be needed with concomitant strong CYP3A inducer use.")


def _sed(rid, other, what):
    return _rule(rid, other, "AMBER", "%s with mirtazapine: additive sedation" % other.capitalize(),
                 "Both depress the central nervous system; the effects add.",
                 "Excess sedation, impaired thinking and motor skills, falls.",
                 "Usually one sedative at night, not two. No alcohol.",
                 "Avoid concomitant use of benzodiazepines and alcohol with mirtazapine" if what == "benzo"
                 else "impairment of cognitive and motor skills is additive with other CNS depressants")


def _ser(rid, other):
    return _rule(rid, other, "AMBER", "%s with mirtazapine: serotonergic caution" % other.capitalize(),
                 "Both act on serotonin; mirtazapine's share is small but not nil.",
                 "Serotonin syndrome is uncommon but possible: agitation, tremor, clonus, sweating, fever.",
                 "Use the combination knowingly; stop both and seek care if those signs appear.",
                 "Serotonin syndrome ... with concomitant use of other serotonergic drugs")


NEW_PAIRS = [_inh("PW035", "ketoconazole"), _inh("PW036", "itraconazole"), _inh("PW037", "clarithromycin"),
             _ind("PW038", "carbamazepine"), _ind("PW039", "phenytoin"), _ind("PW040", "rifampicin"),
             _ind("PW040B", "rifampin"),
             _sed("PW041", "zolpidem", "z"), _sed("PW042", "zopiclone", "z"), _sed("PW043", "eszopiclone", "z"),
             _sed("PW044", "alprazolam", "benzo"), _sed("PW045", "clonazepam", "benzo"),
             _ser("PW046", "tapentadol"), _ser("PW047", "sumatriptan")]
NEW_COND = {"id": "CR017", "condition": "diabetes", "trigger": {"drugs": [KEY]}, "flag": "AMBER",
            "title": "Mirtazapine with diabetes: appetite, weight and glucose",
            "consequence": "Appetite and weight rise on mirtazapine (17% vs 2%; weight gain of 7% or more in 7.5%), which can worsen glycaemic control.",
            "monitoring": "Weight and glucose after starting, and after any dose change.",
            "source": SRC + ": 'Appetite increase was reported in 17% of patients'", "reviewed": REVIEWED}
MAO_KEYS = ["linezolid", "phenelzine", "tranylcypromine", "isocarboxazid", "selegiline", "rasagiline",
            "safinamide", "moclobemide", "methylene_blue"]

P_OLD = '"reviewed": "2026-09-23"}\n  ],\n\n  "condition_rules": [\n'
P_NEW = ('"reviewed": "2026-09-23"},\n' + ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in NEW_PAIRS)
         + '\n  ],\n\n  "condition_rules": [\n')
C_OLD = 'labelling.", "reviewed": "2026-09-23"}\n  ],\n\n  "burden_thresholds": {\n'
C_NEW = ('labelling.", "reviewed": "2026-09-23"},\n    ' + json.dumps(NEW_COND, ensure_ascii=False)
         + '\n  ],\n\n  "mao_inhibitor_keys": ' + json.dumps(MAO_KEYS) + ',\n\n  "burden_thresholds": {\n')
RV_OLD = '"schema": "medsafe-rules/1",\n    "version": "1.2.0",'
RV_NEW = '"schema": "medsafe-rules/1",\n    "version": "1.3.0",'

KB_EDITS = {
    "drugs.json": [("NaSSA entry", M_OLD, M_NEW), ("drugs version", DV_OLD, DV_NEW)],
    "rules.json": [("pairwise rules", P_OLD, P_NEW), ("condition rule + MAO keys", C_OLD, C_NEW),
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
    print("RxGuard NaSSA, MAO inhibitors, as-needed counting -> v" + VERSION)
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
        o, b = apply_edits(read(p), edits)
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
        b = p + ".bak-v184-" + stamp
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

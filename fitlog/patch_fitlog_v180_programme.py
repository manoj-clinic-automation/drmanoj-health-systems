#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.7.1 -> v1.8.0  ::  FITLOG_V180_PROGRAMME -- a knee- and ankle-sparing programme.

WHY: the Family Edition's `joint` profile needs the same deterministic session
engine the owner uses, but with knees and ankles spared: no step-ups,
sit-to-stands, calf raises, wall sits, carries or treadmill; low-impact
aerobic work instead (static cycling, water walking, a water class) and
seated strength.

WHAT
  * knowledge/exercises.json gains eight exercises in NEW categories
    (Knee strength, Seated strength, Joint mobility, Aerobic (low impact)) --
    none of the owner's template slots names them, so his sessions draw from
    exactly the pool they drew from before -- and a "programmes" block:
    "joint" = its own templates per verdict plus an "avoid" list.
  * app.py: FITLOG_PROGRAMME (environment; unset = the owner's templates, as
    before) selects the programme in build_plan(); an avoided exercise never
    enters a pool. APP_VERSION 1.8.0.
  * tests/kb_lint.py validates every programme's templates too.

Anchor-verified, idempotent, compile-checked, .bak, --reverse (app.py; the
knowledge additions are inert without FITLOG_PROGRAMME). Python 3.9.
"""
import argparse
import datetime
import json
import os
import py_compile
import shutil
import sys

TARGET = "/root/fitlog/app.py"
MARKER = "FITLOG_V180_PROGRAMME"
PREV = 'APP_VERSION = "1.7.1"'
VERSION = "1.8.0"

E = []
E.append(("version", 'APP_VERSION = "1.7.1"\n',
          'APP_VERSION = "1.8.0"   # FITLOG_V180_PROGRAMME\n'))
E.append(("programme",
          'PAIN_SITES = ["Glute L", "Glute R", "Post hip", "Ant thigh", "Lumbar", "Other"]\n',
          'PAIN_SITES = ["Glute L", "Glute R", "Post hip", "Ant thigh", "Lumbar", "Other"]\n'
          '# FITLOG_V180_PROGRAMME -- unset is the owner\'s templates, exactly as before.\n'
          'PROGRAMME = os.environ.get("FITLOG_PROGRAMME", "").strip()\n'))
E.append(("build_plan",
          '    tpl = EXKB["templates"].get(verdict) or EXKB["templates"]["YELLOW"]\n',
          '    # FITLOG_V180_PROGRAMME -- a programme brings its own templates and an avoid list.\n'
          '    prog = (EXKB.get("programmes") or {}).get(PROGRAMME) or {}\n'
          '    tpls = prog.get("templates") or EXKB["templates"]\n'
          '    avoid = set(prog.get("avoid") or [])\n'
          '    tpl = tpls.get(verdict) or tpls.get("YELLOW") or EXKB["templates"]["YELLOW"]\n'))
E.append(("pool avoid",
          '                and tier.upper() in e["tiers"] and e["id"] not in used]\n',
          '                and tier.upper() in e["tiers"] and e["id"] not in used\n'
          '                and e["id"] not in avoid]   # FITLOG_V180_PROGRAMME\n'))


def ex(i, en, hi, cat, tiers, doses, purpose, cues, mistakes, equip=""):
    d = {"id": i, "name_en": en, "name_hi": hi, "category": cat, "tiers": tiers,
         "purpose": purpose, "cues": cues, "mistakes": mistakes, "equipment": equip,
         "image_path": "", "video_url": "", "active": 1}
    for t, v in zip(tiers, doses):
        d["dose_" + t.lower()] = v
    return d


NEW_EX = [
    ex("E19", "Water Walking", "पानी में चलना", "Aerobic (low impact)", ["G", "Y"],
       ["20-30 min, chest-deep water", "10-15 min, waist-deep water"],
       "Aerobic work with the knees and ankles unloaded by the water.",
       "Upright, whole foot down, arms moving.", "Hurrying in shallow water, where the load comes back.", "pool"),
    ex("E20", "Water Exercise Class", "पानी में व्यायाम", "Aerobic (low impact)", ["G"],
       ["30-45 min low-impact class"], "Longer aerobic session without impact.",
       "Stay in the depth where the joint is comfortable.", "Jumping moves.", "pool"),
    ex("E21", "Seated Knee Extension", "बैठकर घुटना सीधा करना", "Knee strength", ["G", "Y", "R"],
       ["3 x 12 each leg, 2-s hold at the top", "2 x 10 each leg", "1 x 10 each leg, small range"],
       "Quadriceps strength without weight through the knee.",
       "Sit tall, straighten slowly, hold, lower slowly.", "Swinging the leg; locking hard.", "chair"),
    ex("E22", "Straight-Leg Raise (lying)", "लेटकर सीधी टांग उठाना", "Knee strength", ["G", "Y", "R"],
       ["3 x 10 each leg", "2 x 8 each leg", "1 x 8 each leg"],
       "Quadriceps and hip strength with the knee kept straight and unloaded.",
       "Other knee bent, tighten the thigh first, lift to the height of the bent knee.",
       "Arching the back.", "mat"),
    ex("E23", "Seated Marching", "बैठकर कदमताल", "Seated strength", ["G", "Y", "R"],
       ["3 x 30 s", "2 x 30 s", "1 x 30 s, slow"], "Hip strength and circulation from a chair.",
       "Sit tall, lift one knee at a time, steady rhythm.", "Leaning back.", "chair"),
    ex("E24", "Ankle Pumps and Circles (seated)", "टखने घुमाना", "Joint mobility", ["G", "Y", "R"],
       ["2 x 20 pumps + 10 circles each way", "20 pumps + 10 circles each way", "10 pumps + 5 circles, gently"],
       "Keeps the ankle moving without load; eases morning stiffness.",
       "Slow, full range the ankle allows today.", "Forcing into pain.", "chair"),
    ex("E25", "Heel Slides", "एड़ी खिसकाना", "Joint mobility", ["G", "Y", "R"],
       ["2 x 15 each leg", "15 each leg", "10 each leg, small range"],
       "Knee bending range without weight.", "Lying or sitting, slide the heel in, then out.",
       "Bouncing at the end of range.", "mat"),
    ex("E26", "Seated Band Row", "बैठकर बैंड रो", "Seated strength", ["G", "Y"],
       ["3 x 12", "2 x 10"], "Upper-back strength from a chair.",
       "Shoulders down, squeeze the blades.", "Shrugging.", "theraband"),
]

PROGRAMMES = {"joint": {
    "label": "Knee- and ankle-sparing",
    "note": "FITLOG_V180_PROGRAMME. The same verdicts and rotation as the owner's; knees and ankles are never loaded standing or with impact.",
    "avoid": ["E04", "E09", "E10", "E11", "E12", "E13", "E15"],
    "templates": {
        "GREEN": {"slots": [["Knee strength", 2], ["Glute", 1], ["Core", 1], ["Seated strength", 1],
                            ["Aerobic (low impact)|Aerobic", 1]]},
        "YELLOW": {"slots": [["Knee strength", 1], ["Joint mobility", 1], ["Core", 1],
                             ["Aerobic (low impact)|Aerobic", 1]]},
        "DELOAD": {"slots": [["Joint mobility", 1], ["Knee strength", 1], ["Aerobic (low impact)|Aerobic", 1]]},
        "RED": {"fixed": ["E24", "E25", "E17"], "pick": 3},
        "TRAVEL": {"fixed": ["E24", "E23", "E01"], "pick": 3},
        "RECOVERY": {"checklist": ["Rest the sore joint; ice or warmth, whichever eases it",
                                   "Ankle pumps and heel slides, 5 minutes",
                                   "A short easy walk only if the pain is below 4 out of 10",
                                   "Night light on; rise slowly from bed"]}}}}

LINT_OLD = "# protocols\npids = "
LINT_NEW = ('# FITLOG_V180_PROGRAMME -- every programme\'s templates, after its avoid list.\n'
            'for pname, prog in (exkb.get("programmes") or {}).items():\n'
            '    avoid = set(prog.get("avoid") or [])\n'
            '    for a in avoid:\n'
            '        if a not in eids: errs.append(f"programme {pname}: unknown avoid {a}")\n'
            '    for v in ["GREEN", "YELLOW", "RED", "RECOVERY", "DELOAD", "TRAVEL"]:\n'
            '        if v not in (prog.get("templates") or {}): errs.append(f"programme {pname}: no template {v}")\n'
            '    for tname, tpl in (prog.get("templates") or {}).items():\n'
            '        for eid in tpl.get("fixed", []):\n'
            '            if eid not in eids or eid in avoid: errs.append(f"programme {pname} {tname}: bad fixed {eid}")\n'
            '        for slot in tpl.get("slots", []):\n'
            '            cats = slot[0].split("|")\n'
            '            if not any(e["category"] in cats and e["active"] and e["id"] not in avoid\n'
            '                       for e in exkb["exercises"]):\n'
            '                errs.append(f"programme {pname} {tname}: empty slot {slot[0]}")\n\n'
            + LINT_OLD)


def read(p):
    with open(p, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write(p, t):
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    src = read(a.file)
    if a.reverse:
        out = src
        for label, old, new in reversed(E):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed 1.7.1 -> " + a.reverse)
        return 0
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    bad = [l for l, o, n in E if src.count(o) != 1]
    here = os.path.dirname(os.path.abspath(a.file))
    kpath = os.path.join(here, "knowledge", "exercises.json")
    lpath = os.path.join(here, "tests", "kb_lint.py")
    lint = read(lpath)
    if lint.count(LINT_OLD) != 1 and MARKER not in lint:
        bad.append("kb_lint anchor")
    if bad:
        print("Refusing to patch; anchors: " + ", ".join(bad))
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in E:
        out = out.replace(o, n, 1)
    kb = json.loads(read(kpath))
    have = set(e["id"] for e in kb["exercises"])
    kb["exercises"] += [e for e in NEW_EX if e["id"] not in have]
    kb["programmes"] = PROGRAMMES
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for p in (a.file, kpath, lpath):
        shutil.copy2(p, p + ".bak-v180-" + stamp)
    write(a.file, out)
    py_compile.compile(a.file, doraise=True)
    write(kpath, json.dumps(kb, indent=2, ensure_ascii=False) + "\n")
    if MARKER not in lint:
        write(lpath, lint.replace(LINT_OLD, LINT_NEW, 1))
    print("applied: app.py, knowledge/exercises.json (+%d exercises, programmes), tests/kb_lint.py"
          % len(NEW_EX))
    return 0


if __name__ == "__main__":
    sys.exit(main())

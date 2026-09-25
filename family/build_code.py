#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_code.py -- assemble a Family Edition code tree from the owner's apps.

FAMILY_EDITION_V1. The family copies run the SAME code as the owner's apps.
This copies it -- by an explicit allowlist, never by "everything except" --
into a new tree:

    <out>/gutlog/   app.py pwa.py health_sso.py records_worker.py import_records.py
                    scanner_widget.js food_table_usda.json snack_swaps.json icons
    <out>/rxguard/  app.py dose_ceiling.py health_sso.py knowledge/{drugs,rules}.json
                    + the owner-APPROVED overlay knowledge/{drugs,rules}.local.json,
                      only after check_overlay() finds no personal field in it
                    + knowledge/dose_rules.generic.json when present (generic ceilings)
    <out>/fitlog/   app.py health_ingest.py health_sso.py migrate_health_ingest.py
                    knowledge/*.json except *.local.json
    <out>/family/   every family/*.py

Nothing the owner keeps beside his apps -- regimen.local.json, meals.local.json,
diet_plan.local.json, records_profile.local.json, foodtest, dose_rules.local.json,
databases, secrets, tokens, logs -- can reach a family tree, because nothing is
copied that is not named here. Every .py is compiled on the way in.

  python3 build_code.py --src-gut /root/gutlog --src-rx /root/rxguard \
      --src-fit /root/fitlog --src-family /root/family --out /opt/family/code/<stamp>

Python 3.9.
"""
import argparse
import glob
import json
import os
import py_compile
import shutil
import sys

GUT = ["app.py", "pwa.py", "health_sso.py", "records_worker.py", "import_records.py",
       "scanner_widget.js", "food_table_usda.json", "snack_swaps.json",
       "icon-192.png", "icon-512.png", "kitchen_measures.json", "kitchen_rules.json"]
RX = ["app.py", "dose_ceiling.py", "health_sso.py", "knowledge/drugs.json", "knowledge/rules.json"]
RX_OPTIONAL = ["knowledge/dose_rules.generic.json"]
RX_OVERLAY = ["knowledge/drugs.local.json", "knowledge/rules.local.json"]
FIT = ["app.py", "health_ingest.py", "health_sso.py", "migrate_health_ingest.py"]
# A field in the approved overlay that could carry a person's circumstances.
# 2026-09-25: the check was run on the live overlay before the first copy and
# found one: `strength_logged`, the strength the OWNER logged for each drug he
# approved. It is stripped on the way in (STRIP_FIELDS) and check_overlay()
# still refuses it, so a strip that stops working fails the build instead of
# copying his doses into someone else's copy.
PERSONAL_KEYS = ("personal_relevance", "personal", "patient", "owner", "note_personal",
                 "my_", "his_", "her_", "strength_logged", "logged", "dose_taken")
STRIP_FIELDS = ("strength_logged", "personal_relevance")


def check_overlay(path):
    """Names of any keys in an approved-overlay file that look personal.
    The overlay is drug pharmacology approved from public sources; this says
    so by looking, rather than by trusting."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return ["unreadable: %s" % exc]
    bad = []

    def walk(o, where):
        if isinstance(o, dict):
            for k, v in o.items():
                lk = str(k).lower()
                if any(lk.startswith(p) or lk == p.rstrip("_") for p in PERSONAL_KEYS):
                    bad.append("%s.%s" % (where, k))
                walk(v, where + "." + str(k))
        elif isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, "%s[%d]" % (where, i))
    walk(doc, os.path.basename(path))
    return bad


def strip_personal(o):
    if isinstance(o, dict):
        return dict((k, strip_personal(v)) for k, v in o.items() if k not in STRIP_FIELDS)
    if isinstance(o, list):
        return [strip_personal(x) for x in o]
    return o


def copy(src_dir, rel, out_dir, required=True):
    s = os.path.join(src_dir, rel)
    if not os.path.isfile(s):
        if required:
            raise SystemExit("build: missing %s" % s)
        return False
    d = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(s, d)
    if d.endswith(".py"):
        py_compile.compile(d, doraise=True, cfile=d + "c.tmp")
        os.remove(d + "c.tmp")
    return True


def build(src_gut, src_rx, src_fit, src_family, out):
    if os.path.exists(out):
        raise SystemExit("build: %s exists" % out)
    os.makedirs(out)
    for rel in GUT:
        copy(src_gut, rel, os.path.join(out, "gutlog"))
    for rel in RX:
        copy(src_rx, rel, os.path.join(out, "rxguard"))
    for rel in RX_OPTIONAL:
        copy(src_rx, rel, os.path.join(out, "rxguard"), required=False)
    for rel in RX_OVERLAY:
        p = os.path.join(src_rx, rel)
        if not os.path.isfile(p):
            continue
        d = os.path.join(out, "rxguard", rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(p, encoding="utf-8") as fh:
            doc = strip_personal(json.load(fh))
        with open(d, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        bad = check_overlay(d)
        if bad:
            raise SystemExit("build: the approved overlay %s carries personal-looking fields: %s"
                             % (rel, ", ".join(bad[:5])))
    for rel in FIT:
        copy(src_fit, rel, os.path.join(out, "fitlog"))
    for p in sorted(glob.glob(os.path.join(src_fit, "knowledge", "*.json"))):
        if p.endswith(".local.json"):
            continue
        copy(src_fit, os.path.join("knowledge", os.path.basename(p)), os.path.join(out, "fitlog"))
    for p in sorted(glob.glob(os.path.join(src_family, "*.py")) + glob.glob(os.path.join(src_family, "*.html"))):
        b = os.path.basename(p)
        if b.startswith(("test_", "_nc", "famtest")):
            continue
        copy(src_family, b, os.path.join(out, "family"))
    for d in ("gutlog", "rxguard", "fitlog", "family"):
        for p in glob.glob(os.path.join(out, d, "**", "*"), recursive=True):
            if ".local." in os.path.basename(p) and not (d == "rxguard" and os.path.basename(p) in
                                                        ("drugs.local.json", "rules.local.json")):
                raise SystemExit("build: a local file reached the tree: %s" % p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-gut", default="/root/gutlog")
    ap.add_argument("--src-rx", default="/root/rxguard")
    ap.add_argument("--src-fit", default="/root/fitlog")
    ap.add_argument("--src-family", default="/root/family")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.src_gut, a.src_rx, a.src_fit, a.src_family, a.out)
    n = sum(len(f) for _r, _d, f in os.walk(a.out))
    print("built %s (%d files)" % (a.out, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())

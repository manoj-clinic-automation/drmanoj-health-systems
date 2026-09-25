#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rename_meal_cards_v3360.py -- line the meal cards up with the six slots.

GUTLOG_V3360_FAMILY, carried from the v3.35.0 report. meals.local.json is
data (gitignored); this edits it in place, with a copy first:

  "Morning"      -> "Mid-morning"   (aka "Morning", so an old row still edits)
  "Before lunch" -> kept, slot "auto" (Mid-morning or Lunch as its time says)
  "Evening tea"  -> "Evening"        (aka "Evening tea")

History rows are not rewritten. Idempotent. Dry run by default; --apply writes.

  python3 rename_meal_cards_v3360.py [--file /root/gutlog/meals.local.json] [--apply]
Python 3.9.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime

RENAMES = {"Morning": "Mid-morning", "Evening tea": "Evening"}
AUTO = ("Before lunch",)


def change(doc):
    notes = []
    for c in doc.get("cards") or []:
        n = c.get("name")
        if n in RENAMES:
            c["name"] = RENAMES[n]
            aka = list(c.get("aka") or [])
            if n not in aka:
                aka.append(n)
            c["aka"] = aka
            notes.append("%s -> %s" % (n, c["name"]))
        elif n in AUTO and c.get("slot") != "auto":
            c["slot"] = "auto"
            notes.append("%s: slot by the clock" % n)
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="/root/gutlog/meals.local.json")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    with open(a.file, encoding="utf-8") as fh:
        doc = json.load(fh)
    names = [c.get("name") for c in doc.get("cards") or []]
    notes = change(doc)
    print("cards before: " + ", ".join(names))
    print("changes: " + ("; ".join(notes) or "none (already done)"))
    if not notes or not a.apply:
        if notes:
            print("dry run: nothing written. Re-run with --apply.")
        return 0
    bak = a.file + ".bak-v3360-" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    with open(a.file, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    print("written; backup " + bak)
    print("cards after: " + ", ".join(c.get("name") for c in doc.get("cards") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())

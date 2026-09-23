#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build gutlog/food_table_usda.json from USDA FoodData Central, SR Legacy.

GUTLOG_V3310_FOODLIB. The food library's "from the table" values come from
this one file, bundled beside app.py. The app never calls out for them: a
lookup is a search of this file and nothing else, so the same name gives the
same numbers on every run and nothing about his diet leaves the server.

WHY SR LEGACY AND NOT IFCT 2017
    The brief put the Indian Food Composition Tables first. No machine-
    readable copy of IFCT 2017 is published under a licence that allows
    bundling it -- the tables are the National Institute of Nutrition's
    copyright, sold as a book -- so the brief's fallback applies. SR Legacy
    is USDA public-domain data (FoodData Central publishes its data under
    CC0 1.0). It knows raw lentils, chickpeas, mung and mungo beans, rice,
    oats, besan, ghee and yogurt; it does not know paneer or poha, and for
    those the values stay his to type. A missing match is left blank, never
    guessed.

SOURCE
    FoodData_Central_sr_legacy_food_csv_2018-04.zip, from
    https://fdc.nal.usda.gov/fdc-datasets/ -- downloaded 23-Sep-2026 with the
    owner's go-ahead. The zip is NOT kept in the repository; only this
    script and the three numbers per food it extracts are.

    Nutrient ids: 1003 protein (g), 1008 energy (kcal), 1079 total dietary
    fibre (g), all per 100 g edible portion, which is SR Legacy's basis.

ROWS LEFT OUT ON PURPOSE
    This file is tracked and the repository is public, so it passes through
    tools/NO_SECRETS.py check C, which blocks any tracked file naming a term
    on the gitignored tools/clinical_terms.local.txt. A few SR Legacy names
    (a sweet, a herbal tea) contain such a term as an ordinary food word. The
    gate's own rule is "fix the file, do not widen the allowlist", so any
    row whose name contains a listed term is left out here, and only the
    COUNT is printed -- naming the terms would be the disclosure. None of
    them is a food this library needs. Without the list file nothing is left
    out, and the script says so.

  python build_food_table.py <unzipped SR Legacy folder> [out.json]

Python 3.9.
"""
import csv
import hashlib
import json
import os
import re
import sys

NUTRIENTS = {"1003": 2, "1008": 3, "1079": 4}   # id -> column in the output row


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "food_table_usda.json")

    foods = {}
    with open(os.path.join(src, "food.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            foods[r["fdc_id"]] = [int(r["fdc_id"]), r["description"].strip(), None, None, None]
    with open(os.path.join(src, "food_nutrient.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            col = NUTRIENTS.get(r["nutrient_id"])
            row = foods.get(r["fdc_id"])
            if col is None or row is None:
                continue
            try:
                row[col] = round(float(r["amount"]), 2)
            except ValueError:
                pass

    # The gate's own loader and its own whole-word match, so this leaves out
    # exactly what check C would refuse and nothing more.
    sys.path.insert(0, os.path.join(here, "..", "tools"))
    try:
        from NO_SECRETS import load_terms
        terms = load_terms() or []
    except ImportError:
        terms = []
    if not terms:
        print("NOTE: no local clinical list -- nothing left out, and NO_SECRETS")
        print("      check C may refuse the table at publish time.")
    pats = [re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE) for t in terms]
    rows, dropped = [], 0
    for r in sorted((r for r in foods.values() if r[3] is not None), key=lambda r: r[1].lower()):
        if any(p.search(r[1]) for p in pats):
            dropped += 1
            continue
        rows.append(r)
    print("%d row(s) left out for naming a term on the local clinical list" % dropped)
    digest = hashlib.sha256()
    for name in ("food.csv", "food_nutrient.csv"):
        with open(os.path.join(src, name), "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    doc = {
        "name": "USDA FoodData Central, SR Legacy (April 2018)",
        "short": "USDA",
        "licence": "Public domain, CC0 1.0 (USDA FoodData Central)",
        "basis": "per 100 g edible portion",
        "columns": ["fdc_id", "description", "protein_g", "kcal", "fibre_g"],
        "source_csv_sha256": digest.hexdigest(),
        "foods": rows,
    }
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    print("%d foods -> %s (%d bytes)" % (len(rows), out, os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

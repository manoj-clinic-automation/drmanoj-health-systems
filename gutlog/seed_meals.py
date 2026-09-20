#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_meals.py -- GUTLOG_V3220_MEALS: put the foods the meal cards need, and
the recipe cards, into GutLog's food library. Adds only (INSERT OR IGNORE by
name): nothing already there is changed, so a food he has edited keeps his
values. Dry run by default; --apply writes, after a sqlite3.backup().

  python3 seed_meals.py --db /root/gutlog/health3.db --meals meals.local.json \\
      [--recipes 01_recipe_cards_v1.json] [--apply]

Recipes go in as composites tagged "recipe", per serving, values marked
estimated. FODMAP from each card's flags: onion or garlic -> H, any other
high-FODMAP ingredient -> M-H, none -> L-M (a guess, and the note says so).
Every card name used by meals.local.json is then checked against the
library; any still missing is listed and the run fails. Python 3.9.
"""
import argparse
import datetime
import json
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    ap.add_argument("--meals", required=True)
    ap.add_argument("--recipes")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(a.meals, encoding="utf-8"))
    rows = [tuple(r) + ("", 0, "estimated", "Added for the meal cards; values estimated.")
            for r in cfg.get("library") or []]
    if a.recipes:
        for c in json.load(open(a.recipes, encoding="utf-8")):
            ps = c.get("per_serving") or {}
            fl = c.get("flags") or {}
            fm = "H" if (fl.get("onion") or fl.get("garlic")) else ("M-H" if fl.get("high_fodmap") else "L-M")
            rows.append(("H", c["name"][:80], (c.get("serving") or "1 serving")[:60],
                         round(float(ps.get("protein") or 0), 1), round(float(ps.get("kcal") or 0)),
                         round(float(ps.get("fibre") or 0), 1), fm, "", 0, "recipe estimated",
                         ("From your recipe card, per serving; values estimated; FODMAP from its "
                          "flags. Plants: " + ", ".join(c.get("plants") or []))[:200]))
    con = sqlite3.connect(a.db)
    have = set(r[0] for r in con.execute("SELECT item FROM library"))
    new = [r for r in rows if r[1] not in have]
    print("library: %d items, %d to add, %d already there" % (len(have), len(new), len(rows) - len(new)))
    for r in new:
        print("  + %-55s %s" % (r[1], r[2]))
    names = set(have) | set(r[1] for r in new)
    need = set()
    for c in cfg.get("cards") or []:
        for row in c.get("rows") or []:
            for n, _q in row.get("items") or []:
                need.add(n)
            for o in row.get("options") or []:
                for n, _q in o.get("items") or []:
                    need.add(n)
    need.add("Onion (tarka base)")
    missing = sorted(need - names)
    if missing:
        print("MISSING after seeding (the cards name them): " + ", ".join(missing))
        return 1
    print("every food the cards name is present")
    if not a.apply:
        print("dry run. Re-run with --apply.")
        return 0
    dst = a.db + ".bak-seedmeals-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = sqlite3.connect(dst)
    con.backup(out)
    out.close()
    print("backup: " + dst)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for r in new:
        con.execute("INSERT OR IGNORE INTO library(cat,item,portion,protein,kcal,fibre,fodmap,"
                    "status,fav,tags,note,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", r + (now,))
    con.commit()
    print("added %d" % len(new))
    return 0


if __name__ == "__main__":
    sys.exit(main())

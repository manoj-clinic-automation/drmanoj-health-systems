#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_recipes.py -- GUTLOG_V3240_RECIPES: load recipe cards into the recipes
table. Adds new cards; for a card already there, refreshes its content but
never touches the stage he has set. Dry run by default; --apply writes after
a sqlite3.backup(). Also adds each card's library item if absent (the same
per-serving estimate seed_meals.py uses), so a serving can be logged.

  python3 seed_recipes.py --db /root/gutlog/health3.db --cards recipe_cards.local.json [--apply]

Python 3.9.
"""
import argparse
import datetime
import json
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    ap.add_argument("--cards", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cards = json.load(open(a.cards, encoding="utf-8"))
    con = sqlite3.connect(a.db)
    if not con.execute("SELECT name FROM sqlite_master WHERE name='recipes'").fetchone():
        print("FATAL: no recipes table -- deploy v3.24.0 and load one page first")
        return 1
    have = dict(con.execute("SELECT slug, data FROM recipes").fetchall())
    lib = set(r[0] for r in con.execute("SELECT item FROM library"))
    new = [c for c in cards if c["id"] not in have]
    upd = [c for c in cards if c["id"] in have and json.loads(have[c["id"]]) != c]
    libadd = [c for c in cards if c["name"][:80] not in lib]
    print("recipes: %d cards, %d new, %d refreshed, %d unchanged; library items to add: %d"
          % (len(cards), len(new), len(upd), len(cards) - len(new) - len(upd), len(libadd)))
    for c in new:
        print("  + [%s] %s" % (c.get("group", ""), c["name"]))
    if not a.apply:
        print("dry run. Re-run with --apply.")
        return 0
    dst = a.db + ".bak-seedrecipes-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = sqlite3.connect(dst)
    con.backup(out)
    out.close()
    print("backup: " + dst)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for c in new:
        con.execute("INSERT INTO recipes(slug, name, grp, stage, data, updated) VALUES(?,?,?,?,?,?)",
                    (c["id"], c["name"], c.get("group", ""), "new", json.dumps(c, ensure_ascii=False), now))
    for c in upd:
        con.execute("UPDATE recipes SET name=?, grp=?, data=?, updated=? WHERE slug=?",
                    (c["name"], c.get("group", ""), json.dumps(c, ensure_ascii=False), now, c["id"]))
    for c in libadd:
        ps = c.get("per_serving") or {}
        fl = c.get("flags") or {}
        fm = "H" if (fl.get("onion") or fl.get("garlic")) else ("M-H" if fl.get("high_fodmap") else "L-M")
        con.execute("INSERT OR IGNORE INTO library(cat,item,portion,protein,kcal,fibre,fodmap,status,fav,"
                    "tags,note,created) VALUES('H',?,?,?,?,?,?,'',0,'recipe estimated',?,?)",
                    (c["name"][:80], (c.get("serving") or "1 serving")[:60],
                     round(float(ps.get("protein") or 0), 1), round(float(ps.get("kcal") or 0)),
                     round(float(ps.get("fibre") or 0), 1), fm,
                     "From your recipe card, per serving; values estimated.", now))
    con.commit()
    print("added %d, refreshed %d, library +%d" % (len(new), len(upd), len(libadd)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

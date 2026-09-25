#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_kitchen.py -- put the owner's recipe cards into the Family Kitchen.

FAMILY_EDITION_V1 (Phase C). Reads his GutLog `recipes` table READ-ONLY and
adds each card to the pool as a recipe added by him: name, group, servings,
ingredients with their grams, method, notes, source. A card with onion or
garlic keeps its onion-free version beside it, as a variant.

What is NOT carried: his stage for a card (tried / on trial ...), the per-
serving estimate (each copy works out its own), and any note line that is
about him rather than the dish -- a trial, a dose, a titration, "your ...".
Those lines are dropped and COUNTED, never printed. Idempotent (a card
already in the pool is left alone). Dry run by default; --apply writes.

  python3 seed_kitchen.py --gutlog-db /root/gutlog/health3.db \
      --kitchen-db /srv/family/kitchen/kitchen.db --name Manoj [--apply]
Python 3.9.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# A note line about the person, not the dish. Applied to notes and the
# onion-free lines only -- a method step ("until you see bubbles") is the dish.
PERSONAL = re.compile(r"\b(your|trial|dose|doses|titrat\w*|tested|food test|his|my|week \d)\b", re.I)
GROUP_WORDS = [
    ("Dal & curry", ("dal", "curry", "jhol", "kadhi", "sambar", "rasam", "chole", "rajma", "saag")),
    ("Egg, paneer & fish", ("fish", "egg", "paneer", "chicken", "sol", "tofu")),
    ("Rice & roti", ("rice", "pulao", "khichdi", "roti", "paratha", "bread", "chilla", "cheela", "polenta")),
    ("Breakfast", ("upma", "poha", "idli", "dosa", "oats", "porridge", "bhurji", "toast")),
    ("Sweet", ("halwa", "kheer", "laddu", "barfi", "cake", "sweet", "katli dessert")),
    ("Drink", ("cooler", "lassi", "chaas", "soup", "shake", "smoothie", "tea", "drink")),
    ("Salad", ("salad", "raita", "chutney", "skewer", "slaw")),
    ("Snack", ("kabab", "tikki", "kachori", "falafel", "pizza", "sandwich", "roll", "katli", "chaat", "cutlet")),
    ("Sabzi", ("sabzi", "bhaji", "keema", "matar", "aloo", "gobi", "bhindi", "lauki", "tinda")),
]
PLACEHOLDER = "Method not written down yet."
ONION = re.compile(r"\b(onion|garlic|pyaz|lahsun|shallot|leek)\b", re.I)


def group_of(name):
    n = name.lower()
    for g, words in GROUP_WORDS:
        if any(re.search(r"\b" + w + r"\b", n) for w in words):
            return g
    return "Other"


def keep_lines(lines, dropped):
    out = []
    for x in lines or []:
        x = str(x).strip()
        if not x:
            continue
        if PERSONAL.search(x):
            dropped[0] += 1
            continue
        out.append(x)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gutlog-db", default="/root/gutlog/health3.db")
    ap.add_argument("--kitchen-db", default="/srv/family/kitchen/kitchen.db")
    ap.add_argument("--name", default="Manoj")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    src = sqlite3.connect("file:%s?mode=ro" % a.gutlog_db, uri=True)
    cards = [json.loads(d) for (d,) in src.execute("SELECT data FROM recipes ORDER BY id").fetchall()]
    src.close()
    import kitchen as K
    con = sqlite3.connect(a.kitchen_db)
    con.row_factory = sqlite3.Row
    con.executescript(K.SCHEMA)
    have = set(r[0] for r in con.execute("SELECT slug FROM recipes"))
    now = datetime.now().isoformat(timespec="seconds")
    dropped = [0]
    add = var = 0
    for c in cards:
        slug = "owner-" + str(c.get("id") or "")[:60]
        if not c.get("id") or slug in have:
            continue
        ings = [{"item": str(i[0]), "qty": i[1], "unit": "g" if i[1] else "", "grams": i[1] if i[1] else None,
                 "text": str(i[2]) if len(i) > 2 else ""} for i in c.get("ing") or [] if i]
        method = [str(x).strip() for x in c.get("method") or [] if str(x).strip()]
        # A card with no method carries a placeholder about him ("Your validated
        # recipe -- method not written down"); the family sees a neutral one.
        method = [PLACEHOLDER if re.search(r"not written down", x, re.I) and PERSONAL.search(x) else x
                  for x in method]
        notes = keep_lines(c.get("notes"), dropped)
        row = (slug, c.get("name") or slug, group_of(c.get("name") or ""), float(c.get("serves") or 2),
               str(c.get("serving") or "")[:80], json.dumps(ings), json.dumps(method), json.dumps(notes),
               str(c.get("source") or "")[:200], "", a.name, "owner", now, now, None, "")
        add += 1
        if a.apply:
            con.execute("INSERT INTO recipes(slug, name, grp, servings, serving_text, ingredients, method, notes, "
                        "source, attach, added_by, added_slug, added_at, updated, variant_of, variant_label) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        of = c.get("onion_free") or []
        flags = c.get("flags") or {}
        if of and (flags.get("onion") or flags.get("garlic") or any(ONION.search(i["item"]) for i in ings)):
            v_ings = [i for i in ings if not ONION.search(i["item"])]
            v_ings.append({"item": "hing (asafoetida)", "qty": None, "unit": "pinch", "grams": None,
                           "text": "a pinch of hing in the hot oil"})
            var += 1
            if a.apply:
                con.execute("INSERT INTO recipes(slug, name, grp, servings, serving_text, ingredients, method, "
                            "notes, source, attach, added_by, added_slug, added_at, updated, variant_of, "
                            "variant_label) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (slug + "-onion-free", (c.get("name") or slug) + " (onion-free)", row[2], row[3],
                             row[4], json.dumps(v_ings), json.dumps(method),
                             json.dumps(keep_lines(of, dropped)), row[8], "", a.name, "owner", now, now,
                             rid, "onion-free"))
    if a.apply:
        con.commit()
    con.close()
    print("%s: %d card(s) to add, %d onion-free version(s); %d personal note line(s) left out"
          % ("added" if a.apply else "dry run", add, var, dropped[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

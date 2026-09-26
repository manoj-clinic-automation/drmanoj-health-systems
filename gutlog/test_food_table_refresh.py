#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_food_table_refresh.py -- the refreshed food table keeps every food in use
exactly where it was, and now carries fat, carbohydrate and calcium.

    python3 gutlog/test_food_table_refresh.py [old_table.json] [new_table.json]

Old defaults to the last backup beside app.py (food_table_usda.json.bak-*),
new to gutlog/food_table_usda.json. Asserts, on the foods GutLog already
uses -- the late-snack buttons' fdc ids and every component of the eleven
seeded dishes, looked up by the same words the app uses -- that the id, the
kcal, protein and fibre are unchanged; that every row of the old table is in
the new one with the same first five columns; that the three new columns are
present and non-empty for the foods in use; and the table's size and Python
load time. Names no medicine. Python 3.9.
"""
import glob
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RES = []


def check(name, cond, detail=""):
    RES.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else "  -- " + str(detail)[:400]), flush=True)


def load_app():
    os.environ.update(GUTLOG_DB=os.path.join(os.environ.get("TEMP", "/tmp"), "ftr_%d.db" % os.getpid()),
                      GUTLOG_NOSPAWN="1", GUTLOG_LINKS="0", GUTLOG_INSECURE="1")
    sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("gut_ftr", os.path.join(HERE, "app.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    new = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "food_table_usda.json")
    if len(sys.argv) > 1:
        old = sys.argv[1]
    else:
        baks = sorted(glob.glob(os.path.join(HERE, "food_table_usda.json.bak-*")))
        old = baks[-1] if baks else None
    if not old or not os.path.exists(old):
        print("no old table to compare against (pass its path)")
        return 2
    t0 = time.time()
    with open(new, encoding="utf-8") as fh:
        nd = json.load(fh)
    load_s = time.time() - t0
    with open(old, encoding="utf-8") as fh:
        od = json.load(fh)
    orows = dict((r[0], r) for r in od["foods"])
    nrows = dict((r[0], r) for r in nd["foods"])
    print("old: %d foods, %d bytes; new: %d foods, %d bytes; load %.3f s on Python %s"
          % (len(orows), os.path.getsize(old), len(nrows), os.path.getsize(new), load_s, sys.version.split()[0]))
    check("T01 the new table names its eight columns, the first five as before",
          nd.get("columns") == ["fdc_id", "description", "protein_g", "kcal", "fibre_g", "fat_g", "carbs_g", "calcium_mg"]
          and od.get("columns") == nd["columns"][:5], nd.get("columns"))
    missing = [i for i in orows if i not in nrows]
    changed = [i for i in orows if i in nrows and nrows[i][:5] != orows[i][:5]]
    check("T02 every food of the old table is in the new one with the same id, name, protein, kcal and fibre",
          not missing and not changed, "missing %d changed %d e.g. %s" % (len(missing), len(changed), (missing + changed)[:5]))
    gut = load_app()
    used = [b[5][1] for b in gut.LATE_SNACK_BUTTONS if b[5]]
    comps = sorted(set(c for _n, vs in gut.DISH_SEED for _v, _l, parts in vs for c, _s in parts))
    hits = {}
    for c in comps:
        h = [x for x in gut.food_lookup(c, limit=3) if x.get("full")]
        if h:
            hits[c] = h[0]["fdc"]
    used_ids = set(used) | set(hits.values())
    bad = [i for i in used_ids if i not in nrows or i not in orows or nrows[i][:5] != orows[i][:5]]
    check("T03 the late-snack foods (%d ids) and the eleven dishes' components (%d matched) keep their id, kcal, "
          "protein and fibre" % (len(used), len(hits)), used_ids and not bad, bad)
    nofat = [i for i in used_ids if i in nrows and (len(nrows[i]) < 8 or nrows[i][5] is None or nrows[i][6] is None)]
    check("T04 fat and carbohydrate are present for every food in use; calcium for most",
          not nofat and sum(1 for i in used_ids if nrows[i][7] is not None) >= len(used_ids) - 2, nofat)
    have = sum(1 for r in nd["foods"] if len(r) >= 8 and r[5] is not None and r[6] is not None)
    check("T05 the three new columns are filled across the table (fat and carbs on more than 95 percent of rows)",
          have >= 0.95 * len(nd["foods"]), "%d of %d" % (have, len(nd["foods"])))
    check("T06 the table loads in under a second", load_s < 1.0, "%.3f s" % load_s)
    bad_ = [n for n, ok in RES if not ok]
    print("")
    print("%d checks, %d failed" % (len(RES), len(bad_)))
    print("RESULT: " + ("ALL PASS" if not bad_ else "FAIL"))
    return 0 if not bad_ else 1


if __name__ == "__main__":
    sys.exit(main())

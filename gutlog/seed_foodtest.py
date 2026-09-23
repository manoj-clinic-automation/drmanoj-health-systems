#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Load his food-test plan file into GutLog (GUTLOG_V3320_FOODTEST).

The plan -- which foods, which amounts, how to cook them, the safe plate --
is his diet, so it lives in a gitignored *.local.json and in the database,
never in this repository. This script names none of it.

Dry run by default. With --apply it takes its own sqlite3.backup() first
and writes nothing if that fails. Re-running replaces the plan's DEFINITION
only; a logged step, a score or an outcome is never touched. Each test food
the plan names is added to the food library from the bundled USDA table when
it is not there already, labelled USDA.

  python3 seed_foodtest.py --plan foodtest.local.json           # dry run
  python3 seed_foodtest.py --plan foodtest.local.json --apply

Python 3.9.
"""
import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(a.plan, encoding="utf-8"))
    items = cfg.get("items") or []
    print("plan document id : %s" % cfg.get("plan_id"))
    for it in items:
        steps = it.get("steps") or []
        if it.get("kind") == "dinner":
            desc = "%d dinner days" % int(it.get("days") or 7)
        else:
            desc = ", ".join("%g" % s["g"] for s in steps) + " g " + (it.get("basis") or "dry")
            desc += "; washout %s" % (it.get("washout") if it.get("washout") is not None else "not stated")
        print("  %-7s %-26s %s%s" % (it.get("week"), it.get("label"), desc,
                                      " (optional)" if it.get("optional") else ""))
    if not a.apply:
        print("dry run -- nothing written. Add --apply.")
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(a.app)))
    spec = importlib.util.spec_from_file_location("gutlog_seed", a.app)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    bak = gm.DB_PATH + ".bak-seedfoodtest-" + datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        s = sqlite3.connect(gm.DB_PATH)
        t = sqlite3.connect(bak)
        with t:
            s.backup(t)
        t.close()
        s.close()
    except sqlite3.Error as exc:
        print("BACKUP FAILED, nothing written: %s" % exc)
        return 1
    print("backup: " + bak)
    with gm.app.app_context():
        res = gm.ft_seed(gm.db(), cfg)
    print("seeded %d item(s); library foods added: %s"
          % (res["items"], ", ".join(res["library_added"]) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

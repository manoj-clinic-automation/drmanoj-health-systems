#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_gutlog_v3350_snacks.py -- GUTLOG_V3350_SNACKS one-off seed.

Adds what the late-snack buttons, the Quick Bite buttons and the dishes need
and is missing: foods from the bundled table where it has them (source USDA),
typical values marked "estimated" where it does not, and the dishes (Tinda /
+ paneer, Parwal / + aloo, ...) that are not there yet. It never changes a
food or a dish he already has, and it calls the app's OWN snacks_seed(), so
the seed and the running app cannot disagree. Dry run by default; --apply
takes a sqlite3.backup() first. Idempotent.

  python3 migrate_gutlog_v3350_snacks.py [--app /root/gutlog/app.py] [--apply]

Run it after the first page load of v3.35.0 (the dishes table comes from
SCHEMA). Python 3.9.
"""
import argparse
import importlib.util
import os
import sqlite3
import sys
from datetime import datetime


def run(gm, apply=False, backup_dir=None):
    with gm.app.app_context():
        con = gm.db()
        if not con.execute("SELECT name FROM sqlite_master WHERE name='dishes'").fetchone():
            print("FATAL: no dishes table -- deploy v3.35.0 and load one page first.")
            return {"error": "schema"}
        rep = gm.snacks_seed(con, apply=False)
        print("foods from the table : %s" % (", ".join(rep["foods"]) or "none needed"))
        print("estimated foods      : %s" % (", ".join(rep["estimated"]) or "none needed"))
        print("dishes to add        : %s" % (", ".join(rep["dishes"]) or "none needed"))
        if rep["missing_components"]:
            print("NOT in the food list (those dish variants will say so): "
                  + ", ".join(rep["missing_components"]))
        if not apply:
            print("dry run: nothing written. Re-run with --apply.")
            return dict(rep, dry=True)
        bdir = backup_dir or os.path.dirname(os.path.abspath(gm.DB_PATH))
        dst = os.path.join(bdir, "health3-pre-v3350-seed-" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
        src = sqlite3.connect(gm.DB_PATH)
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
        chk = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
        print("backup: %s (integrity %s)" % (dst, chk))
        if chk != "ok":
            print("FATAL: the backup is not sound. Nothing written.")
            return {"error": "backup"}
        done = gm.snacks_seed(con, apply=True)
        print("added %d foods, %d estimated, %d dishes"
              % (len(done["foods"]), len(done["estimated"]), len(done["dishes"])))
        return dict(done, dry=False, backup=dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="/root/gutlog/app.py")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", default=None)
    a = ap.parse_args()
    if "GUTLOG_V3350_SNACKS" not in open(a.app, encoding="utf-8").read():
        print("FATAL: app.py is not v3.35.0 -- deploy it first.")
        return 1
    os.environ.setdefault("GUTLOG_NOSPAWN", "1")
    sys.path.insert(0, os.path.dirname(os.path.abspath(a.app)))
    spec = importlib.util.spec_from_file_location("gutlog_live", a.app)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    r = run(gm, a.apply, a.backup_dir)
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())

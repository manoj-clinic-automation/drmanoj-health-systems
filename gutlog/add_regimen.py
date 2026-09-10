#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog regimen loader  --  names, molecules, schedule, in one run.

Three jobs, in this order:

  1. NAMES     bring the six new medicines into the convention the
               original fifteen already use:
                   Brand (molecule strength)
                   Molecule strength
  2. MOLECULES fill prnmeds.molecule, blank on every row since the
               v3.3.0 migration added it. This is the field RxGuard
               matches against in Phase C, so doing it now means that
               phase starts with the mapping already done.
  3. SCHEDULE  write the regular regimen into med_schedule, effective-
               dated exactly as the app's own endpoint does.

DRY RUN BY DEFAULT. Nothing is written without --apply.

  python3 add_regimen.py            # show the plan
  python3 add_regimen.py --apply    # write it

Python 3.9 compatible.
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys

DB = "/root/gutlog/health3.db"

# ------------------------------------------------------------ regimen data
# The names, molecules and schedule below used to be literals in this file,
# which published a real medication list to a PUBLIC repository. They now
# live in regimen.local.json, which is gitignored. See tools/NO_SECRETS.py.
#
# The commentary that explained the clinical choices moved with the data --
# 'molecules_left_blank' and 'regimen_not_scheduled' in that file record why
# three medicines carry no molecule, and why two more are deliberately left
# off the schedule.
REGIMEN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "regimen.local.json")


def load_regimen():
    """
    Returns (new_meds, new_sched, renames, molecules, regimen) or None.

    Fails loudly. An empty plan would print a tidy report saying there was
    nothing to do, which is indistinguishable from success and would be
    believed.
    """
    if not os.path.exists(REGIMEN_FILE):
        print("FATAL: regimen data not found: " + REGIMEN_FILE)
        print("")
        print("That file is gitignored on purpose - it holds the medicine")
        print("names, molecules and schedule, which this public repository")
        print("must not carry. Copy it into the app directory on the server,")
        print("or restore it from your own backup.")
        print("")
        print("Refusing to run. An empty plan would report 'nothing to do'")
        print("and look exactly like success.")
        return None
    try:
        with open(REGIMEN_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except ValueError as exc:
        print("FATAL: " + REGIMEN_FILE + " is not valid JSON: " + str(exc))
        return None

    for key in ("new_meds", "new_sched", "renames", "molecules", "regimen"):
        if key not in d:
            print("FATAL: '" + key + "' missing from " + REGIMEN_FILE)
            return None

    new_meds = [tuple(r) for r in d["new_meds"]]
    new_sched = [tuple(r) for r in d["new_sched"]]
    renames = [tuple(r) for r in d["renames"]]
    # JSON object keys are strings; these are prnmeds ids.
    molecules = dict((int(k), v) for k, v in d["molecules"].items())
    regimen = [tuple(r) for r in d["regimen"]]
    return new_meds, new_sched, renames, molecules, regimen


VALID_SLOTS = ("MORNING", "NOON", "EVENING", "NIGHT")
SLOT_ORDER = {"MORNING": 0, "NOON": 1, "EVENING": 2, "NIGHT": 3}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--apply", action="store_true",
                    help="write. Without this it is a dry run.")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("FATAL: db not found: " + args.db)
        return 1

    loaded = load_regimen()
    if loaded is None:
        return 1
    NEW_MEDS, NEW_SCHED, RENAMES, MOLECULES, REGIMEN = loaded

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    ver = con.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()
    if not ver or ver[0] not in ("3.3.0", "3.3.2"):
        print("FATAL: schema is not 3.3.x. Run the migration first.")
        con.close()
        return 1

    today = datetime.date.today()
    today_s = today.isoformat()
    yday = (today - datetime.timedelta(days=1)).isoformat()
    now_s = datetime.datetime.now().isoformat(timespec="seconds")

    # ---- plan the new medicines --------------------------------------
    new_plan = []
    for name, mol, sort in NEW_MEDS:
        row = con.execute("SELECT id FROM prnmeds WHERE name=?",
                          (name,)).fetchone()
        if not row:
            new_plan.append((name, mol, sort))

    # ---- plan the renames --------------------------------------------
    rename_plan = []
    for mid, old, new in RENAMES:
        row = con.execute("SELECT name FROM prnmeds WHERE id=?",
                          (mid,)).fetchone()
        if not row:
            print("FATAL: prnmeds id " + str(mid) + " is missing.")
            con.close()
            return 1
        if row["name"] == old:
            rename_plan.append((mid, old, new))
        elif row["name"] == new:
            pass                      # already applied, no-op
        else:
            print("FATAL: id " + str(mid) + " is '" + row["name"]
                  + "', which is neither the old nor the new name.")
            print("Refusing to rename something unexpected.")
            con.close()
            return 1

    # ---- plan the molecules ------------------------------------------
    mol_plan = []
    for mid, mol in sorted(MOLECULES.items()):
        row = con.execute("SELECT name, molecule FROM prnmeds WHERE id=?",
                          (mid,)).fetchone()
        if not row:
            continue
        if (row["molecule"] or "") != mol:
            mol_plan.append((mid, row["name"], mol))

    # ---- plan the schedule -------------------------------------------
    problems = []
    sched_plan = []
    for med_id, frag, slot, dose, food in REGIMEN:
        row = con.execute("SELECT id, name FROM prnmeds WHERE id=?",
                          (med_id,)).fetchone()
        if not row:
            problems.append("id " + str(med_id) + " does not exist")
            continue
        # check the fragment against the name this row will HAVE after
        # the renames above, not the one it carries right now
        final_name = row["name"]
        for rid, _old, new in rename_plan:
            if rid == med_id:
                final_name = new
        if frag.lower() not in final_name.lower():
            problems.append("id " + str(med_id) + " is '" + final_name
                            + "', expected something containing '"
                            + frag + "'")
            continue
        if slot not in VALID_SLOTS:
            problems.append("bad slot " + slot)
            continue
        existing = con.execute(
            "SELECT id FROM med_schedule "
            "WHERE med_id=? AND slot=? AND valid_to=''",
            (med_id, slot)).fetchone()
        sched_plan.append((med_id, final_name, slot, dose, food, existing))

    if problems:
        print("REFUSING - the list does not match the database:")
        for p in problems:
            print("  !! " + p)
        con.close()
        return 1

    # ---- show the plan -----------------------------------------------
    print("=" * 64)
    print("GutLog regimen loader" + ("" if args.apply else "   [DRY RUN]"))
    print("db    : " + args.db)
    print("dated : " + today_s)
    print("=" * 64)

    print("")
    print("1. NEW MEDICINES")
    if new_plan:
        for name, mol, _sort in new_plan:
            print("   + " + name.ljust(32) + mol)
        for nm, slot, _dt, _wf, var in NEW_SCHED:
            if any(n[0] == nm for n in new_plan):
                print("     scheduled " + slot.lower()
                      + ", dose picked at tap time from: "
                      + var.replace("|", " / "))
    else:
        print("   nothing to add")

    print("")
    print("2. NAMES")
    if rename_plan:
        for _mid, old, new in rename_plan:
            print("   '" + old + "'")
            print("      -> '" + new + "'")
    else:
        print("   nothing to change")

    print("")
    print("3. MOLECULES")
    if mol_plan:
        for _mid, name, mol in mol_plan:
            print("   " + name.ljust(32) + mol)
    else:
        print("   nothing to change")
    blanks = con.execute(
        "SELECT id, name FROM prnmeds WHERE active=1 "
        "AND (molecule IS NULL OR molecule='') ORDER BY sort, id").fetchall()
    left = [b for b in blanks if b["id"] not in MOLECULES]
    if left:
        print("   left blank on purpose:")
        for b in left:
            print("      " + b["name"])

    print("")
    print("4. SCHEDULE")
    for _mid, name, slot, dose, food, existing in sorted(
            sched_plan, key=lambda r: (SLOT_ORDER[r[2]], r[1])):
        line = "   " + slot.ljust(8) + name
        if dose:
            line += "  (" + dose + ")"
        if existing:
            line += "   [replaces an existing line]"
        print(line)
    print("")
    print("   " + str(len(sched_plan)) + " schedule line(s)")

    if not args.apply:
        print("")
        print("DRY RUN - nothing written. Re-run with --apply to write.")
        con.close()
        return 0

    # ---- write --------------------------------------------------------
    try:
        con.execute("BEGIN")

        for name, mol, sort in new_plan:
            con.execute(
                "INSERT OR IGNORE INTO prnmeds(name,sort,molecule,active) "
                "VALUES(?,?,?,1)", (name, sort, mol))

        for mid, old, new in rename_plan:
            con.execute("UPDATE prnmeds SET name=? WHERE id=? AND name=?",
                        (new, mid, old))
            # keep already-logged dose rows readable under the new label
            con.execute(
                "UPDATE doses SET medicine=? WHERE med_id=? AND medicine=?",
                (new, mid, old))

        for mid, _name, mol in mol_plan:
            con.execute("UPDATE prnmeds SET molecule=? WHERE id=?",
                        (mol, mid))

        ep = con.execute(
            "SELECT value FROM settings WHERE key='med_epoch'").fetchone()
        try:
            epoch = int(ep[0]) + 1 if ep else 1
        except (TypeError, ValueError):
            epoch = 1
        con.execute("UPDATE settings SET value=? WHERE key='med_epoch'",
                    (str(epoch),))

        for med_id, _name, slot, dose, food, existing in sched_plan:
            if existing:
                con.execute("UPDATE med_schedule SET valid_to=? WHERE id=?",
                            (yday, existing["id"]))
            con.execute(
                "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,"
                "valid_from,valid_to,epoch,notes,created) "
                "VALUES(?,?,?,?,?,'',?,'',?)",
                (med_id, slot, dose, food, today_s, epoch, now_s))

        # scheduled lines for the medicines created above
        for nm, slot, dose, food, var in NEW_SCHED:
            row = con.execute("SELECT id FROM prnmeds WHERE name=?",
                              (nm,)).fetchone()
            if not row:
                continue
            mid = row["id"]
            ex = con.execute(
                "SELECT id FROM med_schedule WHERE med_id=? AND slot=? "
                "AND valid_to=''", (mid, slot)).fetchone()
            if ex:
                con.execute("UPDATE med_schedule SET valid_to=? WHERE id=?",
                            (yday, ex["id"]))
            con.execute(
                "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,"
                "valid_from,valid_to,epoch,notes,created,variants) "
                "VALUES(?,?,?,?,?,'',?,'',?,?)",
                (mid, slot, dose, food, today_s, epoch, now_s, var))

        con.execute("UPDATE prnmeds SET scheduled=0")
        con.execute(
            "UPDATE prnmeds SET scheduled=1 WHERE id IN "
            "(SELECT DISTINCT med_id FROM med_schedule WHERE valid_to='')")
        con.execute("COMMIT")
    except Exception as exc:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        con.close()
        print("")
        print("ERROR: " + str(exc))
        print("Rolled back. Nothing written.")
        return 2

    # ---- read back ----------------------------------------------------
    print("")
    print("Written. Regimen now live:")
    rows = con.execute(
        "SELECT s.slot, p.name FROM med_schedule s "
        "JOIN prnmeds p ON p.id=s.med_id WHERE s.valid_to='' "
        "ORDER BY CASE s.slot WHEN 'MORNING' THEN 0 WHEN 'NOON' THEN 1 "
        "WHEN 'EVENING' THEN 2 ELSE 3 END, p.name").fetchall()
    for r in rows:
        print("   " + r["slot"].ljust(8) + r["name"])
    n_mol = con.execute(
        "SELECT COUNT(*) FROM prnmeds WHERE active=1 "
        "AND molecule<>''").fetchone()[0]
    n_all = con.execute(
        "SELECT COUNT(*) FROM prnmeds WHERE active=1").fetchone()[0]
    print("")
    print("   " + str(len(rows)) + " schedule line(s), epoch " + str(epoch))
    print("   " + str(n_mol) + "/" + str(n_all)
          + " medicines now carry a molecule")
    print("   Open the Now tab - they should appear as tappable rows.")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

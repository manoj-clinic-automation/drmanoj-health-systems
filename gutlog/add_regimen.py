#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog regimen loader  --  names, molecules, schedule, in one run.

Three jobs, in this order:

  1. NAMES     bring the six new medicines into the convention the
               original fifteen already use:
                   Brand (molecule strength)     e.g. Colospa (mebeverine 135)
                   Molecule strength             e.g. Paracetamol 500
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
import os
import sqlite3
import sys

DB = "/root/gutlog/health3.db"

# ------------------------------------------------------------ new meds
# (name, molecule, sort). Inserted only if the name is not already there.
NEW_MEDS = [
    ("Lintide (linaclotide)", "linaclotide", -1),
]

# Scheduled lines for medicines created above, keyed by name because their
# id does not exist until this script runs.
# (name, slot, dose_text, with_food, variants)
#
# Linaclotide is taken every morning but the dose moves between 72, 145 and
# 290 mcg, sometimes as a combination. One scheduled row carrying the three
# strengths as variants means the morning reminder is honest AND the dose
# actually taken is what gets recorded. Three separate rows would collect
# two false misses every day; a single fixed strength would record the
# wrong dose whenever it changed.
NEW_SCHED = [
    ("Lintide (linaclotide)", "MORNING", "", "ANY", "72|145|290"),
]

# ---------------------------------------------------------------- names
# (id, current name, new name). Applied only when the current name
# matches exactly, so a re-run after the change is a silent no-op.
RENAMES = [
    (14, "ORS",           "Electral (ORS sachet, 1 L)"),
    (16, "calaptin 40",   "Calaptin (verapamil 40)"),
    (17, "jiardiance 10", "Jardiance (empagliflozin 10)"),
    (18, "Telma 40",      "Telma (telmisartan 40)"),
    (21, "Fulnite 2",     "Fulnite (eszopiclone 2)"),
]

# ------------------------------------------------------------ molecules
# id -> molecule key. Lower case, one per row; a combination product
# would use semicolons.
#
# DELIBERATELY BLANK, and why:
#   11 Cremaffin  - the plain and Plus formulations differ, the Plus
#                   adding sodium picosulfate. Guessing would put a wrong
#                   laxative into an interaction check. Fill it once you
#                   have checked which pack you actually use.
#   14 ORS        - a salt and glucose mix, not a molecule.
#   15 Probiotic  - an organism, not a molecule, and strain-dependent.
MOLECULES = {
    1: "mebeverine",
    2: "drotaverine",
    3: "paracetamol",
    4: "etoricoxib",
    5: "fexofenadine",
    6: "bilastine",
    7: "fluticasone",
    8: "peppermint oil",
    9: "psyllium",
    10: "polyethylene glycol",
    12: "clonazepam",
    13: "zolpidem",
    16: "verapamil",
    17: "empagliflozin",
    18: "telmisartan",
    19: "rosuvastatin",
    20: "nebivolol",
    21: "eszopiclone",
}

# ------------------------------------------------------------- schedule
# (id, name fragment, slot, dose_text, with_food)
# The fragment guards against an id pointing at a different drug than the
# one this list was written for. It must survive the renames above, so it
# uses only the part of the name that does not change.
#
# NOT SCHEDULED, deliberately:
#   Lintide 72 / 145 / 290 - one of three strengths is taken, varying, and
#     sometimes in the afternoon rather than the morning. Scheduling all
#     three would show three expected rows every morning against one dose
#     actually taken, so the day would sit permanently at 1/3 and collect
#     two false misses. Scheduling a single strength would log the wrong
#     drug on every day the strength differed. As extra-dose chips at the
#     front of the list, one tap records the strength actually taken and
#     the timestamp records when.
#   Electral - one to two sachets a day is a count the schedule cannot
#     express. Tapping each one gives a true daily count instead.
REGIMEN = [
    (16, "alaptin",      "MORNING", "", "ANY"),
    (16, "alaptin",      "EVENING", "", "ANY"),
    (17, "ardiance",     "MORNING", "", "ANY"),
    (18, "Telma",        "MORNING", "", "ANY"),
    (19, "Rosuvastatin", "EVENING", "", "ANY"),
    (20, "Nebivolol",    "EVENING", "", "ANY"),
    (21, "ulnite",       "NIGHT",   "", "ANY"),
]

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

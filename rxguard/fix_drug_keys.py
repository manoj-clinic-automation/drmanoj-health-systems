#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correct a `medications.drug_key` the knowledge base cannot resolve.

Why this exists
---------------
A drug_key that does not resolve leaves that drug out of every interaction,
CYP, duplication, burden, QT and condition check -- silently, on every screen.
One missing letter is enough. Editing the key by hand in the database is easy
to get half-right: `med_events` carries the same key, and a rename that
touches only `medications` leaves the history pointing at a drug that no
longer exists under that name.

So this renames both together, refuses to create a new unresolvable key, takes
its own backup, and is DRY-RUN BY DEFAULT. (CLAUDE.md gap 3: `tidy_extras.py`
writes without taking a backup. Not repeating that.)

A combination product cannot be renamed into one key, because RxGuard holds
one row per molecule. `--split` turns one combination row into one row per
component, each keeping the original dates, status, prescriber and notes, and
records the split in `med_events` so the change is visible rather than
mysterious.

  python3 fix_drug_keys.py                        # report what does not resolve
  python3 fix_drug_keys.py --rename OLD NEW       # dry run
  python3 fix_drug_keys.py --rename OLD NEW --apply
  python3 fix_drug_keys.py --split OLD A B        # dry run
  python3 fix_drug_keys.py --split OLD A B --apply

Read-only unless --apply is given. Python 3.9.
"""
import argparse
import datetime
import importlib.util
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "rxguard.db")


def load_app():
    os.environ.setdefault("RXGUARD_GUTLOG_FEED", "0")
    sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("rx_fix", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def backup(db):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db + ".bak-keyfix-" + stamp
    src = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    dest = sqlite3.connect(dst)
    with dest:
        src.backup(dest)
    ok = dest.execute("PRAGMA integrity_check").fetchone()[0]
    dest.close()
    src.close()
    if ok != "ok":
        raise RuntimeError("backup failed integrity check: " + str(ok))
    return dst


def report(rx, db):
    app = rx.create_app(db_path=db, secret="key-fix-read-only")
    with app.test_request_context():
        from flask import g
        g.db_path = db
        rows = rx.unresolved_keys()
    if not rows:
        print("Every drug_key resolves. Nothing to fix.")
        return 0
    print(str(len(rows)) + " key(s) the knowledge base cannot resolve:")
    print("")
    for r in rows:
        print("  %-9s %-34s %s" % ("ACTIVE" if r["live"] else "stopped",
                                   r["key"] or "(no key)", r["why"]))
        if r["raw"]:
            print("            on the list as: " + r["raw"])
    print("")
    print("A live one is a gap in today's checks. A stopped one is a record that")
    print("will mislead whoever reads it later. Fix both.")
    return 1


def do_rename(rx, db, old, new, apply_it):
    if not rx.get_drug(new):
        print("REFUSING: '" + new + "' is not a knowledge-base key either, so the")
        print("rename would swap one invisible drug for another.")
        sug = rx.norm_key(new)
        if sug != new and rx.get_drug(sug):
            print("Did you mean: " + sug)
        return 2
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    meds = con.execute("SELECT id, raw_name, status FROM medications WHERE drug_key=?",
                       (old,)).fetchall()
    nev = con.execute("SELECT COUNT(*) FROM med_events WHERE drug_key=?", (old,)).fetchone()[0]
    if not meds:
        print("No medications row has drug_key '" + old + "'.")
        con.close()
        return 2
    print("rename  " + old + "  ->  " + new)
    for m in meds:
        print("  medications id=%s  status=%s  on the list as %r" % (m["id"], m["status"], m["raw_name"]))
    print("  med_events rows carrying the old key: %d" % nev)
    if not apply_it:
        print("")
        print("DRY RUN. Nothing written. Re-run with --apply to make the change.")
        con.close()
        return 0
    bak = backup(db)
    print("  backup: " + bak)
    con.execute("UPDATE medications SET drug_key=? WHERE drug_key=?", (new, old))
    con.execute("UPDATE med_events SET drug_key=? WHERE drug_key=?", (new, old))
    for m in meds:
        con.execute("INSERT INTO med_events (med_id, drug_key, event_date, action, "
                    "old_dose, new_dose, source, note) VALUES (?,?,?,?,?,?,?,?)",
                    (m["id"], new, datetime.date.today().isoformat(), "key-corrected",
                     "", "", "self",
                     "drug_key '" + old + "' did not resolve, so this medicine was "
                     "absent from every check; corrected to '" + new + "'."))
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM medications WHERE drug_key=?", (old,)).fetchone()[0]
    con.close()
    print("  applied. rows still carrying the old key: %d" % left)
    return 0


def do_split(rx, db, old, parts, apply_it):
    missing = [p for p in parts if not rx.get_drug(p)]
    if missing:
        print("REFUSING: not knowledge-base keys: " + ", ".join(missing))
        return 2
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM medications WHERE drug_key=?", (old,)).fetchall()
    if not rows:
        print("No medications row has drug_key '" + old + "'.")
        con.close()
        return 2
    if len(rows) > 1:
        print("REFUSING: " + str(len(rows)) + " rows carry that key; split them one at a time.")
        con.close()
        return 2
    r = rows[0]
    print("split   " + old + "  ->  " + " + ".join(parts))
    print("  from medications id=%s  status=%s  dose=%r  %s -> %s" % (
        r["id"], r["status"], r["dose"], r["start_date"], r["stop_date"]))
    print("  each new row keeps the dates, status, kind, prescriber and indication.")
    print("  the original row is removed; %d med_events row(s) are re-pointed at the first part."
          % con.execute("SELECT COUNT(*) FROM med_events WHERE drug_key=?", (old,)).fetchone()[0])
    if not apply_it:
        print("")
        print("DRY RUN. Nothing written. Re-run with --apply to make the change.")
        con.close()
        return 0
    bak = backup(db)
    print("  backup: " + bak)
    today = datetime.date.today().isoformat()
    note = ("was one combination row, drug_key '" + old + "', which resolved to nothing "
            "and so was absent from every check; split into " + " + ".join(parts) + ".")
    made = []
    for p in parts:
        cur = con.execute(
            "INSERT INTO medications (drug_key, raw_name, dose, frequency, route, "
            "indication, prescriber, specialty, kind, status, benefit, start_date, "
            "last_change, stop_date, last_reviewed_by_prescriber, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p, r["raw_name"] or "", r["dose"] or "", r["frequency"] or "",
             r["route"] or "oral", r["indication"] or "", r["prescriber"] or "",
             r["specialty"] or "", r["kind"] or "chronic", r["status"] or "stopped",
             r["benefit"] or "unknown", r["start_date"], today, r["stop_date"],
             r["last_reviewed_by_prescriber"] or "",
             ((r["notes"] or "") + " " + note).strip()))
        made.append((p, cur.lastrowid))
        con.execute("INSERT INTO med_events (med_id, drug_key, event_date, action, "
                    "old_dose, new_dose, source, note) VALUES (?,?,?,?,?,?,?,?)",
                    (cur.lastrowid, p, today, "key-corrected", "", "", "self", note))
    con.execute("UPDATE med_events SET med_id=?, drug_key=? WHERE drug_key=?",
                (made[0][1], made[0][0], old))
    con.execute("DELETE FROM medications WHERE id=?", (r["id"],))
    con.commit()
    con.close()
    for p, rid in made:
        print("  created medications id=%s  %s" % (rid, p))
    print("  applied.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("RXGUARD_DB", DEFAULT_DB))
    ap.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"))
    ap.add_argument("--split", nargs="+", metavar="KEY",
                    help="OLD PART1 PART2 [...] -- one combination row into one row per molecule")
    ap.add_argument("--apply", action="store_true", help="write; omit for a dry run")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("FATAL: no database at " + args.db)
        return 1
    rx = load_app()
    print("=" * 72)
    print("RxGuard key fix  ::  " + args.db + ("" if args.apply else "   (DRY RUN)"))
    print("=" * 72)

    if args.rename:
        return do_rename(rx, args.db, args.rename[0], args.rename[1], args.apply)
    if args.split:
        if len(args.split) < 3:
            print("--split needs OLD and at least two parts.")
            return 2
        return do_split(rx, args.db, args.split[0], args.split[1:], args.apply)
    return report(rx, args.db)


if __name__ == "__main__":
    sys.exit(main())

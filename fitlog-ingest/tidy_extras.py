#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reorder the extra-dose chips by use, not by entry order.

The chips currently sit in the order the medicines happened to be added.
This groups them the way a hand reaches for them, most-likely first:

    gut / antispasmodic    the reason this app exists
    pain
    bowel and hydration
    allergy
    sleep

Scheduled medicines are not listed here at all -- the app hides them from
the extras row -- so their sort value does not matter.

Matching is by name fragment, so it survives renaming and does not depend
on ids staying put. Anything not named keeps its current sort and is
reported, rather than being silently pushed to the end.

DRY RUN BY DEFAULT.

  python3 tidy_extras.py
  python3 tidy_extras.py --apply

Python 3.9 compatible.
"""

import argparse
import json
import os
import sqlite3
import sys

DB = "/root/gutlog/health3.db"

# The ordering IS clinical data -- it names the medicines and groups them by
# what they treat. It lives outside the repository, which is public.
# See tools/NO_SECRETS.py.
REGIMEN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "regimen.local.json")


def load_order():
    """
    (fragment, group) pairs. Order here is the order on screen.

    Fails loudly rather than returning an empty list: a silent empty would
    leave every sort value untouched and still print a clean-looking report
    saying the job was done.
    """
    if not os.path.exists(REGIMEN_FILE):
        print("FATAL: regimen data not found: " + REGIMEN_FILE)
        print("")
        print("That file is gitignored on purpose - it holds the medicine")
        print("names, which this public repository must not carry. Copy it")
        print("into the app directory on the server, or restore it from your")
        print("own backup. Refusing to run rather than reorder nothing and")
        print("report success.")
        return None
    try:
        with open(REGIMEN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        print("FATAL: " + REGIMEN_FILE + " is not valid JSON: " + str(exc))
        return None
    rows = data.get("extra_order")
    if not rows:
        print("FATAL: 'extra_order' is missing or empty in " + REGIMEN_FILE)
        return None
    return [(r[0], r[1]) for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("FATAL: db not found: " + args.db)
        return 1

    ORDER = load_order()
    if ORDER is None:
        return 1

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT id, name, sort, COALESCE(scheduled,0) AS sched "
        "FROM prnmeds WHERE active=1 ORDER BY sort, id").fetchall()

    plan = []
    used = set()
    for i, (frag, group) in enumerate(ORDER):
        hit = None
        for r in rows:
            if r["id"] in used:
                continue
            if frag.lower() in r["name"].lower():
                hit = r
                break
        if hit:
            used.add(hit["id"])
            plan.append((hit["id"], hit["name"], i, group))

    missing = [f for f, _g in ORDER
               if not any(f.lower() in n.lower() for _i, n, _s, _g in plan)]
    unlisted = [r for r in rows if r["id"] not in used]

    print("=" * 60)
    print("Extra-dose chip order" + ("" if args.apply else "   [DRY RUN]"))
    print("=" * 60)
    print("")
    last = None
    for _id, name, sort, group in plan:
        if group != last:
            print("  -- " + group)
            last = group
        print("     " + str(sort).rjust(2) + "  " + name)

    if missing:
        print("")
        print("  not found in the database, skipped:")
        for f in missing:
            print("     " + f)

    if unlisted:
        print("")
        print("  not in this ordering, sort left untouched:")
        for r in unlisted:
            tag = " (scheduled, hidden from extras)" if r["sched"] else ""
            print("     " + r["name"] + tag)

    if not args.apply:
        print("")
        print("DRY RUN - nothing written. Re-run with --apply.")
        con.close()
        return 0

    try:
        con.execute("BEGIN")
        for mid, _name, sort, _group in plan:
            con.execute("UPDATE prnmeds SET sort=? WHERE id=?", (sort, mid))
        con.execute("COMMIT")
    except Exception as exc:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        con.close()
        print("ERROR: " + str(exc) + " -- rolled back.")
        return 2

    print("")
    print("Written. Extra-dose chips will now appear in this order.")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

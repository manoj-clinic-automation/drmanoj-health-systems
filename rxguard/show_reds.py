#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
What is RxGuard actually flagging right now, and does any of it rest on a
drug GutLog has not seen?

Read-only. Opens the live database, runs the same as-taken view the page runs,
and prints every RED and AMBER with the molecules it rests on and whether the
new v1.5.0 staleness test marks it. Writes nothing, changes nothing.

The question this exists to answer: a RED is worth acting on only if the drugs
underneath it are still being taken. Before v1.5.0 there was no way to tell
from the page.

  python3 show_reds.py                 # live database, 14-day GutLog window
  python3 show_reds.py --days 30
  python3 show_reds.py --db /path/to/other.db

Nothing here is printed anywhere but the terminal. Do not redirect it into the
repository: the output is the health record.
"""
import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "rxguard.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("RXGUARD_DB", DEFAULT_DB))
    ap.add_argument("--days", type=int, default=14, choices=(14, 30, 90))
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("FATAL: no database at " + args.db)
        return 1
    os.environ["RXGUARD_GUTLOG_FEED"] = "1"     # the live feed, explicitly
    sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("rx_live", os.path.join(HERE, "app.py"))
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    app = rx.create_app(db_path=args.db, secret="read-only-report")

    with app.test_request_context():
        from flask import g
        g.db_path = args.db
        listed = rx.active_meds()
        print("=" * 72)
        print("RxGuard " + rx.APP_VERSION + "  ::  " + args.db)
        print("active / tapering on this list: " + str(len(listed)))
        print("=" * 72)

        data, err = rx.gutlog_stack(args.days)
        if err or not data:
            print("GutLog feed unavailable: " + str(err))
            print("The engine still runs on the list above, but nothing can be")
            print("said about whether those drugs are still being taken.")
            return 1

        v = rx.astaken_view(data)
        for flag in ("RED", "AMBER"):
            hits = [f for f in v["findings"] if f["flag"] == flag]
            print("")
            print(flag + " x " + str(len(hits)))
            print("-" * 72)
            for f in hits:
                print("  " + f["title"])
                print("    category : " + f["category"]
                      + (("  rule " + f["rule_id"]) if f.get("rule_id") else ""))
                print("    rests on : " + (", ".join(
                    rx.display_name(k) for k in (f.get("involves") or [])) or "-"))
                if f.get("gut_stale"):
                    print("    STALE    : " + f["gut_stale"])
                if f.get("unlisted"):
                    print("    NOTE     : involves a medicine not on your list")
                print("")

        rec = v.get("rec") or {"stopped": [], "missing": []}
        print("")
        print("Reconciliation")
        print("-" * 72)
        for r in rec["stopped"]:
            when = ("ended in GutLog on " + r["valid_to"]) if r["valid_to"] else (
                "not in GutLog's regimen" +
                ((", last dose " + r["last_dose"] + " (" + str(r["gap"]) + " d ago)")
                 if r["last_dose"] else ", no dose in the window"))
            print("  " + r["name"] + " " + (r["dose"] or "") + " -- " + when
                  + ", still " + r["status"] + " here")
        for r in rec["missing"]:
            print("  " + r["name"] + " -- in GutLog's regimen"
                  + ((" since " + r["valid_from"]) if r["valid_from"] else "")
                  + ", not on your list")
        if not rec["stopped"] and not rec["missing"]:
            print("  nothing to reconcile")
        print("")
        print("Apply either direction on the As taken (GutLog) page. Nothing")
        print("here has changed anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

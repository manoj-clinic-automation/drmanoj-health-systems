#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
correct_doses.py -- apply an owner-confirmed correction to logged doses.

The corrections name medicines, so they live in a gitignored spec file
(*.local.json); this script names nothing. Dry run by default.

  python3 correct_doses.py --spec fix.local.json                 # dry run
  python3 correct_doses.py --spec fix.local.json --apply

Spec:
  {"why": "...owner's words...",
   "gutlog": [{"find": {"day": "YYYY-MM-DD", "dtime": "HH:MM", "medicine": "..."},
               "do": "delete"},
              {"find": {...}, "do": "relabel", "to": "<exact prnmeds name>",
               "reason": "<optional new reason>"}],
   "fitlog": [{"find": {"dt": "YYYY-MM-DDTHH:MM", "dose_label": "..."},
               "do": "delete" | "relabel", "to": "<label>"}]}

Rules: every GutLog find must match EXACTLY one row or nothing is written.
A FitLog find may match none (the mirror may never have arrived) or one.
Both databases are backed up with sqlite3.backup() before any write, and
every change is appended to corrections.log beside the GutLog database with
the full old row. Stock is computed from the dose rows at read time, so a
deleted extra dose puts its tablet back by itself. Python 3.9.
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

GUT_DB = "/root/gutlog/health3.db"
FIT_DB = "/root/fitlog/fitlog.db"


def backup(path):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = path + ".bak-correct-" + stamp
    src = sqlite3.connect(path)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--gutlog-db", default=GUT_DB)
    ap.add_argument("--fitlog-db", default=FIT_DB)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))

    g = sqlite3.connect(a.gutlog_db)
    g.row_factory = sqlite3.Row
    plan, bad = [], []
    for act in spec.get("gutlog") or []:
        f = act["find"]
        rows = g.execute("SELECT * FROM doses WHERE day=? AND dtime=? AND medicine=?",
                         (f["day"], f["dtime"], f["medicine"])).fetchall()
        if len(rows) != 1:
            bad.append("gutlog %s: %d rows match, need exactly 1" % (json.dumps(f), len(rows)))
            continue
        to = None
        if act["do"] == "relabel":
            to = g.execute("SELECT id, name FROM prnmeds WHERE name=?", (act["to"],)).fetchone()
            if not to:
                bad.append("gutlog relabel target not in prnmeds: " + act["to"])
                continue
        elif act["do"] != "delete":
            bad.append("unknown action " + act["do"])
            continue
        plan.append(("gutlog", act, dict(rows[0]), to))

    f_con = None
    if spec.get("fitlog"):
        if os.path.exists(a.fitlog_db):
            f_con = sqlite3.connect(a.fitlog_db)
            f_con.row_factory = sqlite3.Row
            for act in spec["fitlog"]:
                f = act["find"]
                rows = f_con.execute("SELECT * FROM analgesic_log WHERE dt=? AND dose_label=?",
                                     (f["dt"], f["dose_label"])).fetchall()
                if len(rows) > 1:
                    bad.append("fitlog %s: %d rows match, need 0 or 1" % (json.dumps(f), len(rows)))
                elif rows:
                    plan.append(("fitlog", act, dict(rows[0]), None))
                else:
                    print("fitlog: no mirror row for %s (never arrived) - nothing to do" % json.dumps(f))
        else:
            print("fitlog db not found at %s - FitLog part skipped" % a.fitlog_db)

    print("=" * 66)
    for app, act, row, to in plan:
        what = act["do"] + ((" -> " + act["to"]) if act["do"] == "relabel" else "")
        print("%-6s id=%-5s %s  %s" % (app, row["id"], json.dumps(act["find"]), what))
    print("=" * 66)
    if bad:
        print("REFUSED, nothing written:")
        for b in bad:
            print("  " + b)
        return 1
    if not a.apply:
        print("dry run: %d change(s) planned. Re-run with --apply." % len(plan))
        return 0

    print("backup: " + backup(a.gutlog_db))
    if f_con is not None and any(p[0] == "fitlog" for p in plan):
        print("backup: " + backup(a.fitlog_db))
    logp = os.path.join(os.path.dirname(os.path.abspath(a.gutlog_db)), "corrections.log")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with open(logp, "a", encoding="utf-8") as log:
        for app, act, row, to in plan:
            con = g if app == "gutlog" else f_con
            tbl = "doses" if app == "gutlog" else "analgesic_log"
            if act["do"] == "delete":
                con.execute("DELETE FROM " + tbl + " WHERE id=?", (row["id"],))
            elif app == "gutlog":
                con.execute("UPDATE doses SET medicine=?, med_id=?, reason=COALESCE(?, reason) "
                            "WHERE id=?", (to["name"], to["id"], act.get("reason"), row["id"]))
            else:
                con.execute("UPDATE analgesic_log SET dose_label=? WHERE id=?", (act["to"], row["id"]))
            log.write(json.dumps({"at": now, "app": app, "action": act, "old_row": row,
                                  "why": spec.get("why", "")}) + "\n")
    g.commit()
    if f_con is not None:
        f_con.commit()
    print("applied %d change(s); audit appended to %s" % (len(plan), logp))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_trials.py -- GUTLOG_V3260_TRIALS: create trials described in an
owner-confirmed spec (gitignored; it names his foods), e.g. to move an old
one-day food test into a trial period. Skips a trial whose food and start
already exist. Dry run by default; --apply writes after a sqlite3.backup().

  python3 migrate_trials.py --db /root/gutlog/health3.db --spec trials.local.json [--apply]

Spec: {"trials": [{"food", "match", "amount", "freq", "start", "days", "note"}]}
Python 3.9.
"""
import argparse
import datetime
import json
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))
    con = sqlite3.connect(a.db)
    if not con.execute("SELECT name FROM sqlite_master WHERE name='trials'").fetchone():
        print("FATAL: no trials table -- deploy v3.26.0 and load one page first")
        return 1
    todo = []
    for t in spec.get("trials") or []:
        if con.execute("SELECT 1 FROM trials WHERE LOWER(food)=LOWER(?) AND start=?",
                       (t["food"], t["start"])).fetchone():
            print("  = exists: %s from %s" % (t["food"], t["start"]))
            continue
        todo.append(t)
        print("  + %s from %s for %s days (match: %s)" % (t["food"], t["start"], t.get("days", 14),
                                                          t.get("match") or t["food"]))
    if not a.apply:
        print("dry run: %d to create. Re-run with --apply." % len(todo))
        return 0
    dst = a.db + ".bak-trials-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = sqlite3.connect(dst)
    con.backup(out)
    out.close()
    print("backup: " + dst)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for t in todo:
        con.execute("INSERT INTO trials(food,match,amount,freq,start,days,ended,status,verdict,"
                    "verdict_note,note,created) VALUES(?,?,?,?,?,?,'','active','','',?,?)",
                    (t["food"], (t.get("match") or t["food"]).lower(), t.get("amount", ""),
                     t.get("freq", ""), t["start"], int(t.get("days", 14)), t.get("note", ""), now))
    con.commit()
    print("created %d" % len(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())

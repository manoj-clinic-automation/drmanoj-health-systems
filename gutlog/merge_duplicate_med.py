#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merge a duplicate GutLog medicine into the one to keep.

Two prnmeds rows for the same product at the same strength (they differ only
in spelling or case) split its doses, schedule and stock in two. This moves
everything from DROP to KEEP and retires DROP (active=0, never deleted, so
its id stays valid for anything that remembered it):

  doses.med_id (+ doses.medicine text), med_schedule.med_id,
  stock_events.med_id, stock_links.stock_med_id,
  stock_order_cfg / stock_meds / med_salts only where KEEP has none.

Refuses if both have an OPEN schedule in the same slot (that would break the
one-open-row-per-slot rule) -- stop one first. Dry run by default; --apply
takes a sqlite3.backup() first and writes in one transaction.

  python3 merge_duplicate_med.py --keep "NAME" --drop "NAME" [--db PATH] [--apply]

Python 3.9. No medicine name appears in this file.
"""
import argparse
import datetime
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", required=True)
    ap.add_argument("--drop", required=True)
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    k = con.execute("SELECT id, name FROM prnmeds WHERE name=?", (a.keep,)).fetchone()
    d = con.execute("SELECT id, name FROM prnmeds WHERE name=?", (a.drop,)).fetchone()
    if not k or not d or k["id"] == d["id"]:
        print("FATAL: need two different existing medicines, exact names.")
        return 1
    K, D = k["id"], d["id"]
    tables = set(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    clash = con.execute(
        "SELECT a.slot FROM med_schedule a JOIN med_schedule b ON a.slot=b.slot "
        "WHERE a.med_id=? AND b.med_id=? AND COALESCE(a.valid_to,'')='' AND COALESCE(b.valid_to,'')=''",
        (K, D)).fetchall()
    if clash:
        print("REFUSING: both have an open schedule in slot(s) " + ", ".join(r[0] for r in clash)
              + ". Stop one of them first.")
        return 1
    n = lambda sql, p: con.execute(sql, p).fetchone()[0]
    report = [("doses", n("SELECT COUNT(*) FROM doses WHERE med_id=? OR (med_id IS NULL AND medicine=?)", (D, d["name"]))),
              ("schedule rows", n("SELECT COUNT(*) FROM med_schedule WHERE med_id=?", (D,))),
              ("stock events", n("SELECT COUNT(*) FROM stock_events WHERE med_id=?", (D,)) if "stock_events" in tables else 0)]
    print("keep: id %d  %s" % (K, k["name"]))
    print("drop: id %d  %s" % (D, d["name"]))
    for label, c in report:
        print("  move %-14s %d" % (label, c))
    if not a.apply:
        print("DRY RUN - nothing written. Add --apply to write.")
        return 0
    bak = a.db + ".bak-merge-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = sqlite3.connect(bak)
    con.backup(dst)
    dst.close()
    print("backup : " + bak)
    con.execute("UPDATE doses SET med_id=?, medicine=? WHERE med_id=? OR (med_id IS NULL AND medicine=?)",
                (K, k["name"], D, d["name"]))
    con.execute("UPDATE med_schedule SET med_id=? WHERE med_id=?", (K, D))
    if "stock_events" in tables:
        con.execute("UPDATE stock_events SET med_id=? WHERE med_id=?", (K, D))
    if "stock_links" in tables:
        con.execute("UPDATE stock_links SET stock_med_id=? WHERE stock_med_id=?", (K, D))
    for t in ("stock_order_cfg", "stock_meds", "med_salts"):
        if t in tables:
            if not con.execute("SELECT 1 FROM " + t + " WHERE med_id=?", (K,)).fetchone():
                con.execute("UPDATE " + t + " SET med_id=? WHERE med_id=?", (K, D))
            else:
                con.execute("DELETE FROM " + t + " WHERE med_id=?", (D,))
    cols = set(r[1] for r in con.execute("PRAGMA table_info(prnmeds)"))
    if "active" in cols:
        con.execute("UPDATE prnmeds SET active=0 WHERE id=?", (D,))
    con.commit()
    print("merged; the dropped entry is retired (active=0), not deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

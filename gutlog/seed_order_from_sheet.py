#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-time seed for the v3.19.0 monthly order, from the interim master sheet.

Reads order_seed.local.json (gitignored -- it names medicines; CLAUDE.md 5d)
and, for every sheet row it can match to a GutLog medicine by name:
  * sets the pack size and pack type,
  * sets keep-on-hand for an SOS row (the sheet's target), 0 for a regular one,
  * sets a stock COUNT from the sheet's total units -- ONLY where GutLog has
    no count yet. An existing count is never overwritten; a difference is
    reported so he can recount if he wants.

Dry run by default: prints the match table and changes nothing. --apply
takes a sqlite3.backup() copy of the live database first, then writes.
Unmatched rows are listed by name and left alone. Idempotent: running it
again changes nothing but pack details that already hold the same values.

  python3 seed_order_from_sheet.py --seed order_seed.local.json [--db /root/gutlog/health3.db]
                                   [--map order_map.local.json] [--apply]

Python 3.9. No medicine name appears in this file.
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

PACK_TYPES = ("strip", "bottle", "pouch", "box", "tube", "sachet", "vial", "pack")


def norm(s):
    return re.sub(r"[^a-z0-9.]+", " ", (s or "").lower()).strip()


def keys(row):
    """Names a sheet row may go by in GutLog, most specific first."""
    b, g, st = norm(row["brand"]), norm(row["generic"]), norm(row["strength"])
    out = [b, b.replace(" ", ""), g + " " + st, g]
    bare = re.sub(r"\s*[0-9.]+$", "", b)
    if bare != b:
        out.append(bare)
    return [k for k in out if k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--map", help="JSON file {sheet brand: GutLog name} for rows the names do not settle")
    a = ap.parse_args()
    rows = json.load(open(a.seed, encoding="utf-8"))["rows"]
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    have = set(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    if "stock_order_cfg" not in have:
        print("FATAL: stock_order_cfg missing. Deploy v3.19.0 and open GutLog once first.")
        return 1
    meds = con.execute("SELECT id, name, pack_size FROM prnmeds").fetchall()
    by = {}
    for m in meds:
        n = norm(m["name"])
        by.setdefault(n, m)
        by.setdefault(n.replace(" ", ""), m)
    manual = json.load(open(a.map, encoding="utf-8")) if a.map else {}
    by_name = dict((m["name"], m) for m in meds)
    counted = set(r[0] for r in con.execute(
        "SELECT DISTINCT med_id FROM stock_events WHERE kind='COUNT'"))
    plan, unmatched, used = [], [], set()
    for r in rows:
        hit = by_name.get(manual.get(r["brand"], ""))
        for k in ([] if hit else keys(r)):
            if k in by and by[k]["id"] not in used:
                hit = by[k]
                break
        if not hit:
            # brand's first word + the strength as a whole word, if that
            # singles out exactly one medicine
            w0 = norm(r["brand"]).split(" ")[0]
            st = norm(r["strength"]).split(" ")[0]
            cand = [m for m in meds if m["id"] not in used
                    and re.search(r"\b" + re.escape(w0) + r"\b", norm(m["name"]))
                    and re.search(r"(?<![0-9.])" + re.escape(st) + r"(?![0-9.])", norm(m["name"]))]
            if len(cand) == 1:
                hit = cand[0]
        if not hit:
            unmatched.append(r["brand"])
            continue
        used.add(hit["id"])
        pt = (r.get("pack_type") or "").lower()
        pt = pt if pt in PACK_TYPES else "pack"
        keep = float(r["target_units"]) if r["use"] == "SOS" else 0.0
        plan.append((hit, r, pt, keep, hit["id"] not in counted))
    print("%-28s %-28s %6s %-7s %6s %s" % ("sheet", "gutlog", "pack", "type", "keep", "count"))
    for hit, r, pt, keep, setc in plan:
        print("%-28s %-28s %6d %-7s %6g %s" % (r["brand"][:28], hit["name"][:28], int(r["units_per_pack"]),
              pt, keep, ("set %g" % r["total_units"]) if setc else "kept (already counted)"))
    print("unmatched (left alone): " + (", ".join(unmatched) if unmatched else "none"))
    if not a.apply:
        print("DRY RUN - nothing written. Add --apply to write.")
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = a.db + ".bak-seedorder-" + stamp
    dst = sqlite3.connect(bak)
    con.backup(dst)
    dst.close()
    print("backup : " + bak)
    now = datetime.datetime.now()
    at, created = now.strftime("%Y-%m-%d %H:%M"), now.isoformat(timespec="seconds")
    for hit, r, pt, keep, setc in plan:
        con.execute("UPDATE prnmeds SET pack_size=? WHERE id=?", (int(r["units_per_pack"]), hit["id"]))
        con.execute("INSERT INTO stock_order_cfg(med_id, pack_type, keep_units) VALUES(?,?,?) "
                    "ON CONFLICT(med_id) DO UPDATE SET pack_type=excluded.pack_type, "
                    "keep_units=excluded.keep_units", (hit["id"], pt, keep))
        if setc:
            con.execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) VALUES(?,?,?,?,?,?)",
                        (hit["id"], "COUNT", float(r["total_units"]), at, "seed:sheet", created))
    con.commit()
    print("applied: %d medicines" % len(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())

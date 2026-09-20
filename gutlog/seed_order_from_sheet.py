#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Seed GutLog's stock from the interim master sheet (v3.19.0, extended v3.20.0).

Reads order_seed.local.json (gitignored -- it names medicines; CLAUDE.md 5d).
For every sheet row matched to a GutLog medicine by name (or by --map):
  * pack size and pack type,
  * keep-on-hand: the row's "keep" if given, else the sheet target for an
    SOS row and 0 for a regular one,
  * a stock COUNT from the sheet's total units -- ONLY where GutLog has no
    count yet. An existing count is never overwritten; it is reported.

--add-missing: a row that matches nothing and carries "add_name" is ADDED to
GutLog as a medicine (name, molecule, strength), then seeded like the rest.
A row without add_name is left alone and listed.

--links: the file's "links" section links each strength of a medicine whose
strengths vary to the sheet row (pack) it comes out of, with units
({"72": ["SHEET BRAND", 1], "290": ["SHEET BRAND", 2]}). Existing links for
that medicine are replaced.

Dry run by default: prints what it would do, changes nothing. --apply takes a
sqlite3.backup() copy of the live database first, then writes in one
transaction. Idempotent: a second --apply adds nothing and counts nothing.

  python3 seed_order_from_sheet.py --seed order_seed.local.json
        [--db /root/gutlog/health3.db] [--map order_map.local.json]
        [--add-missing] [--links] [--apply]

Python 3.9. No medicine name appears in this file.
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys

PACK_TYPES = ("strip", "bottle", "pouch", "box", "tube", "sachet", "vial", "pack")


def norm(s):
    return re.sub(r"[^a-z0-9.]+", " ", (s or "").lower()).strip()


def keys(row):
    b, g, st = norm(row["brand"]), norm(row["generic"]), norm(row["strength"])
    out = [b, b.replace(" ", ""), g + " " + st, g]
    bare = re.sub(r"\s*[0-9.]+$", "", b)
    if bare != b:
        out.append(bare)
    if row.get("add_name"):
        out.insert(0, norm(row["add_name"]))
    return [k for k in out if k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    ap.add_argument("--map", help="JSON {sheet brand: GutLog name} for rows the names do not settle")
    ap.add_argument("--add-missing", action="store_true")
    ap.add_argument("--links", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    seed = json.load(open(a.seed, encoding="utf-8"))
    rows = seed["rows"]
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    have = set(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    need = {"stock_order_cfg"} | ({"stock_links"} if a.links else set())
    if not need <= have:
        print("FATAL: %s missing. Deploy the build and open GutLog once first." % ", ".join(sorted(need - have)))
        return 1
    manual = json.load(open(a.map, encoding="utf-8")) if a.map else {}
    manual = dict((k, v) for k, v in manual.items() if not k.startswith("_"))

    def load_meds():
        return con.execute("SELECT id, name, pack_size FROM prnmeds").fetchall()

    def match(meds):
        by, by_name = {}, dict((m["name"], m) for m in meds)
        for m in meds:
            n = norm(m["name"])
            by.setdefault(n, m)
            by.setdefault(n.replace(" ", ""), m)
        out, used = {}, set()
        for r in rows:
            hit = by_name.get(manual.get(r["brand"], ""))
            for k in ([] if hit else keys(r)):
                if k in by and by[k]["id"] not in used:
                    hit = by[k]
                    break
            if not hit:
                w0 = norm(r["brand"]).split(" ")[0]
                st = norm(r["strength"]).split(" ")[0]
                cand = [m for m in meds if m["id"] not in used
                        and re.search(r"\b" + re.escape(w0) + r"\b", norm(m["name"]))
                        and re.search(r"(?<![0-9.])" + re.escape(st) + r"(?![0-9.])", norm(m["name"]))]
                if len(cand) == 1:
                    hit = cand[0]
            if hit:
                used.add(hit["id"])
                out[r["brand"]] = hit
        return out

    hits = match(load_meds())
    to_add = [r for r in rows if r["brand"] not in hits and r.get("add_name")] if a.add_missing else []
    left = [r["brand"] for r in rows if r["brand"] not in hits and r not in to_add]

    if a.apply:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = a.db + ".bak-seedorder-" + stamp
        dst = sqlite3.connect(bak)
        con.backup(dst)
        dst.close()
        print("backup : " + bak)

    now = datetime.datetime.now()
    at, created = now.strftime("%Y-%m-%d %H:%M"), now.isoformat(timespec="seconds")
    cols = set(r[1] for r in con.execute("PRAGMA table_info(prnmeds)"))
    for r in to_add:
        print("ADD    %-24s -> %s" % (r["brand"][:24], r["add_name"]))
        if a.apply:
            con.execute("INSERT OR IGNORE INTO prnmeds(name, sort) VALUES(?, 99)", (r["add_name"],))
            mid = con.execute("SELECT id FROM prnmeds WHERE name=?", (r["add_name"],)).fetchone()[0]
            if "molecule" in cols and r.get("molecule"):
                con.execute("UPDATE prnmeds SET molecule=? WHERE id=?", (r["molecule"], mid))
            if "active" in cols:
                con.execute("UPDATE prnmeds SET active=1 WHERE id=?", (mid,))
            if "med_salts" in have and r.get("salt_strength"):
                con.execute("INSERT INTO med_salts(med_id, strength, no_salt, updated) VALUES(?,?,0,?) "
                            "ON CONFLICT(med_id) DO UPDATE SET strength=excluded.strength",
                            (mid, r["salt_strength"], created))
    if a.apply and to_add:
        hits = match(load_meds())

    counted = set(x[0] for x in con.execute("SELECT DISTINCT med_id FROM stock_events WHERE kind='COUNT'"))
    print("%-24s %-34s %5s %-7s %5s %s" % ("sheet", "gutlog", "pack", "type", "keep", "count"))
    for r in rows:
        hit = hits.get(r["brand"])
        if not hit:
            continue
        pt = (r.get("pack_type") or "").lower()
        pt = pt if pt in PACK_TYPES else "pack"
        if "keep" in r:
            keep = float(r["keep"])
        else:
            keep = float(r["target_units"]) if r["use"] == "SOS" else 0.0
        setc = hit["id"] not in counted
        print("%-24s %-34s %5d %-7s %5g %s" % (r["brand"][:24], hit["name"][:34], int(r["units_per_pack"]),
              pt, keep, ("set %g" % r["total_units"]) if setc else "kept (already counted)"))
        if a.apply:
            con.execute("UPDATE prnmeds SET pack_size=? WHERE id=?", (int(r["units_per_pack"]), hit["id"]))
            con.execute("INSERT INTO stock_order_cfg(med_id, pack_type, keep_units) VALUES(?,?,?) "
                        "ON CONFLICT(med_id) DO UPDATE SET pack_type=excluded.pack_type, "
                        "keep_units=excluded.keep_units", (hit["id"], pt, keep))
            if setc:
                con.execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) VALUES(?,?,?,?,?,?)",
                            (hit["id"], "COUNT", float(r["total_units"]), at, "seed:sheet", created))
    print("left alone: " + (", ".join(left) if left else "none"))

    if a.links:
        meds = dict((m["name"], m["id"]) for m in load_meds())
        for vname, spec in (seed.get("links") or {}).items():
            if vname.startswith("_"):
                continue
            vid = meds.get(vname)
            if not vid:
                print("LINK   %s: no such GutLog medicine - skipped" % vname)
                continue
            out = []
            for label, (brand, units) in spec.items():
                h = hits.get(brand)
                if not h:
                    print("LINK   %s %s: pack %s not in GutLog - skipped" % (vname, label, brand))
                    continue
                out.append((vid, label.strip().lower(), h["id"], float(units)))
                print("LINK   %s  %s -> %s x %g" % (vname, label, h["name"], float(units)))
            if a.apply and out:
                con.execute("DELETE FROM stock_links WHERE med_id=?", (vid,))
                con.executemany("INSERT INTO stock_links(med_id, variant, stock_med_id, units) "
                                "VALUES(?,?,?,?)", out)
    if a.apply:
        con.commit()
        print("applied.")
    else:
        print("DRY RUN - nothing written. Add --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

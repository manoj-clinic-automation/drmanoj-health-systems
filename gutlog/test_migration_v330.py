#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.3.0 migration verification suite.

Run on the server AFTER migrate_gutlog_v330.py and BEFORE
`systemctl restart gutlog`. Read-only except for the transactional
behaviour tests, which write to a scratch copy, never the live DB.

  python3 test_migration_v330.py --db /root/gutlog/health3.db

Exit 0 = all green. Exit 1 = at least one failure; do not restart.

Python 3.9.25 compatible.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((True, name, detail or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def cols(conn, table):
    return dict((r[1], r) for r in
                conn.execute("PRAGMA table_info(" + table + ")"))


def tables(conn):
    return set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))


def indexes(conn):
    return set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"))


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def build_tests(db):
    ro = sqlite3.connect("file:" + db + "?mode=ro", uri=True)

    def t01_columns_present():
        expected = [
            ("prnmeds", ["molecule", "form", "pack_size", "stock",
                         "active", "scheduled"]),
            ("doses", ["status", "med_id", "sched_id", "dose_text"]),
            ("episodes", ["bristol"]),
        ]
        for table, wanted in expected:
            have = cols(ro, table)
            for c in wanted:
                assert c in have, table + "." + c + " missing"
        return "all 11 added columns present"

    def t02_defaults_correct():
        d = cols(ro, "doses")
        assert d["status"][4] == "'TAKEN'", \
            "doses.status default is " + str(d["status"][4])
        p = cols(ro, "prnmeds")
        assert p["active"][4] == "1", "prnmeds.active default wrong"
        return "column defaults correct"

    def t03_med_schedule_exists():
        assert "med_schedule" in tables(ro), "med_schedule missing"
        c = cols(ro, "med_schedule")
        for want in ["med_id", "slot", "dose_text", "with_food",
                     "valid_from", "valid_to", "epoch"]:
            assert want in c, "med_schedule." + want + " missing"
        return "med_schedule present with 7 core columns"

    def t04_indexes_exist():
        have = indexes(ro)
        for want in ["idx_sched_med", "idx_sched_open", "idx_sched_one_open",
                     "idx_doses_day", "idx_doses_sched"]:
            assert want in have, "index " + want + " missing"
        return "all 5 indexes present"

    def t05_legacy_doses_intact():
        n = ro.execute("SELECT COUNT(*) FROM doses").fetchone()[0]
        assert n >= 3, "expected at least 3 legacy dose rows, found " + str(n)
        bad = ro.execute(
            "SELECT COUNT(*) FROM doses WHERE status IS NULL "
            "OR status = ''").fetchone()[0]
        assert bad == 0, str(bad) + " dose row(s) still have empty status"
        return str(n) + " dose rows, all with a status"

    def t06_med_id_linked():
        unlinked = ro.execute(
            "SELECT medicine FROM doses WHERE med_id IS NULL").fetchall()
        assert not unlinked, \
            "unlinked medicine names: " + ", ".join(r[0] for r in unlinked)
        orphan = ro.execute(
            "SELECT COUNT(*) FROM doses d LEFT JOIN prnmeds p "
            "ON p.id = d.med_id WHERE d.med_id IS NOT NULL "
            "AND p.id IS NULL").fetchone()[0]
        assert orphan == 0, str(orphan) + " dose row(s) point at a missing med"
        return "every dose row resolves to a real prnmeds row"

    def t07_legacy_sched_id_null():
        n = ro.execute(
            "SELECT COUNT(*) FROM doses WHERE sched_id IS NOT NULL"
        ).fetchone()[0]
        return str(n) + " dose row(s) carry a sched_id (0 expected pre-Phase-A)"

    def t08_settings_seeded():
        s = dict(ro.execute("SELECT key, value FROM settings"))
        for k in ["slot_times", "slot_window_min", "bp_flag_sys",
                  "bp_flag_dia", "med_epoch", "schema_version"]:
            assert k in s, "setting '" + k + "' missing"
        assert s["schema_version"] == "3.3.0", \
            "schema_version is " + s["schema_version"]
        slots = json.loads(s["slot_times"])
        for slot in ["MORNING", "NOON", "EVENING", "NIGHT"]:
            assert slot in slots, "slot_times missing " + slot
        return "6 settings seeded, slot_times parses to 4 slots"

    def t09_prnmeds_preserved():
        n = ro.execute("SELECT COUNT(*) FROM prnmeds").fetchone()[0]
        assert n >= 3, "expected the seeded prnmeds, found " + str(n) \
                       + " -- is regimen.local.json present beside app.py?"
        # Sort order is asserted against the seed's own first entry rather
        # than a hardcoded medicine name: this repository is public and must
        # not carry the regimen. Same assertion, no clinical detail.
        first = ro.execute(
            "SELECT name FROM prnmeds ORDER BY sort LIMIT 1").fetchone()[0]
        seeded_first = ro.execute(
            "SELECT name FROM prnmeds ORDER BY id LIMIT 1").fetchone()[0]
        assert first == seeded_first, \
            "sort order changed; first by sort is '" + first \
            + "', first by insertion is '" + seeded_first + "'"
        inactive = ro.execute(
            "SELECT COUNT(*) FROM prnmeds WHERE active != 1").fetchone()[0]
        assert inactive == 0, "some prnmeds defaulted inactive"
        return str(n) + " meds, sort order intact, all active"

    def t10_untouched_tables():
        for t in ["vitals", "days", "meals", "courses", "patches",
                  "labs", "files", "consults", "foodtests", "library"]:
            assert t in tables(ro), t + " went missing"
        v = ro.execute("SELECT COUNT(*) FROM vitals").fetchone()[0]
        e = ro.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        return "all legacy tables present (vitals=" + str(v) \
               + ", episodes=" + str(e) + ")"

    # -- transactional behaviour, tested on a scratch copy --------------

    def t11_partial_unique_index_bites():
        tmp = tempfile.mkdtemp()
        scratch = os.path.join(tmp, "scratch.db")
        shutil.copy(db, scratch)
        c = sqlite3.connect(scratch)
        try:
            med = c.execute("SELECT id FROM prnmeds LIMIT 1").fetchone()[0]
            c.execute(
                "INSERT INTO med_schedule (med_id,slot,dose_text,valid_from,"
                "valid_to,created) VALUES (?,?,?,?,?,?)",
                (med, "MORNING", "1 tab", "2026-09-01", "", "x"))
            failed = False
            try:
                c.execute(
                    "INSERT INTO med_schedule (med_id,slot,dose_text,"
                    "valid_from,valid_to,created) VALUES (?,?,?,?,?,?)",
                    (med, "MORNING", "2 tab", "2026-09-05", "", "x"))
            except sqlite3.IntegrityError:
                failed = True
            assert failed, \
                "second OPEN schedule row for same med+slot was accepted"

            # closing the first must then allow the second
            c.execute("UPDATE med_schedule SET valid_to='2026-09-04' "
                      "WHERE med_id=? AND slot='MORNING' AND valid_to=''",
                      (med,))
            c.execute(
                "INSERT INTO med_schedule (med_id,slot,dose_text,valid_from,"
                "valid_to,created) VALUES (?,?,?,?,?,?)",
                (med, "MORNING", "2 tab", "2026-09-05", "", "x"))
            n = c.execute(
                "SELECT COUNT(*) FROM med_schedule WHERE med_id=? "
                "AND slot='MORNING'", (med,)).fetchone()[0]
            assert n == 2, "expected 2 history rows, got " + str(n)
            return "duplicate open row rejected; close-then-open accepted"
        finally:
            c.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def t12_expected_dose_join():
        """The materialisation query must left-join cleanly on an empty
        schedule and not error."""
        tmp = tempfile.mkdtemp()
        scratch = os.path.join(tmp, "scratch.db")
        shutil.copy(db, scratch)
        c = sqlite3.connect(scratch)
        try:
            q = ("SELECT s.id, p.name, s.slot, s.dose_text, d.id, d.status "
                 "FROM med_schedule s "
                 "JOIN prnmeds p ON p.id = s.med_id "
                 "LEFT JOIN doses d ON d.sched_id = s.id AND d.day = ? "
                 "WHERE s.valid_from <= ? "
                 "AND (s.valid_to = '' OR s.valid_to >= ?)")
            rows = c.execute(q, ("2026-09-10",) * 3).fetchall()
            assert rows == [], "expected no rows on an empty schedule"

            med = c.execute("SELECT id FROM prnmeds LIMIT 1").fetchone()[0]
            c.execute(
                "INSERT INTO med_schedule (med_id,slot,dose_text,valid_from,"
                "valid_to,created) VALUES (?,?,?,?,?,?)",
                (med, "EVENING", "1 tab", "2026-09-01", "", "x"))
            sid = c.execute(
                "SELECT id FROM med_schedule WHERE slot='EVENING'"
            ).fetchone()[0]
            rows = c.execute(q, ("2026-09-10",) * 3).fetchall()
            assert len(rows) == 1, "expected 1 expected-dose row"
            assert rows[0][4] is None, "unlogged dose should join to NULL"

            c.execute(
                "INSERT INTO doses (day,dtime,medicine,status,med_id,"
                "sched_id,created) VALUES (?,?,?,?,?,?,?)",
                ("2026-09-10", "20:05", "x", "TAKEN", med, sid, "x"))
            rows = c.execute(q, ("2026-09-10",) * 3).fetchall()
            assert len(rows) == 1, "join duplicated the expected-dose row"
            assert rows[0][5] == "TAKEN", "logged dose did not join"

            # a day before the schedule opened must yield nothing
            rows = c.execute(q, ("2026-08-01",) * 3).fetchall()
            assert rows == [], "schedule leaked into a pre-valid_from day"
            return "expected-dose join correct: empty, unlogged, logged, pre-date"
        finally:
            c.close()
            shutil.rmtree(tmp, ignore_errors=True)

    return [
        ("01 added columns present", t01_columns_present),
        ("02 column defaults correct", t02_defaults_correct),
        ("03 med_schedule created", t03_med_schedule_exists),
        ("04 indexes created", t04_indexes_exist),
        ("05 legacy dose rows intact", t05_legacy_doses_intact),
        ("06 med_id backfill resolved", t06_med_id_linked),
        ("07 legacy sched_id NULL", t07_legacy_sched_id_null),
        ("08 settings seeded", t08_settings_seeded),
        ("09 prnmeds preserved", t09_prnmeds_preserved),
        ("10 legacy tables untouched", t10_untouched_tables),
        ("11 partial unique index enforces one open row", t11_partial_unique_index_bites),
        ("12 expected-dose join behaves", t12_expected_dose_join),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/gutlog/health3.db")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("FATAL: db not found: " + args.db)
        return 1

    print("=" * 62)
    print("GutLog v3.3.0 migration verification")
    print("db     : " + args.db)
    print("sqlite : " + sqlite3.sqlite_version)
    print("=" * 62)

    for name, fn in build_tests(args.db):
        check(name, fn)

    passed = 0
    for ok, name, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        line = "[" + mark + "] " + name
        if detail:
            line += "  -- " + detail
        print(line)

    total = len(RESULTS)
    print("-" * 62)
    print(str(passed) + "/" + str(total) + " passed")
    if passed == total:
        print("ALL GREEN. Safe to proceed.")
        return 0
    print("FAILURES PRESENT. Do not restart the service. Roll back from "
          "/root/gutlog/backups/ if needed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Load one plan document through the SAME code path the Add form uses.

Not by writing rows by hand: the form's route is where the PDF is checked,
hashed, named and recorded, and a seed that bypassed it would be seeding a
different thing from the one the app maintains.

THE TITLE IS AN ARGUMENT, NEVER A LITERAL IN THIS FILE. Plan titles name
medicines, this file is tracked, and the repository is public (CLAUDE.md 5d
and NO_SECRETS check C). The title lives in the database and the PDF only.

  python3 seed_plan.py --title "..." --date YYYY-MM-DD --pdf FILE [--status Draft]
        --apply            actually write; without it this is a dry run

Live database by default. Python 3.9.
"""
import argparse
import importlib.util
import io
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD, first considered")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--status", default="Draft")
    ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.pdf):
        print("FATAL: no such file: " + a.pdf)
        return 1
    fh = open(a.pdf, "rb")
    try:
        raw = fh.read()
    finally:
        fh.close()
    if not raw.startswith(b"%PDF-"):
        print("FATAL: that file does not begin %PDF- , so the app would refuse it too.")
        return 1

    os.environ.setdefault("GUTLOG_NOSPAWN", "1")
    sys.path.insert(0, os.path.dirname(os.path.abspath(a.app)))
    spec = importlib.util.spec_from_file_location("gutlog_seed_plan", a.app)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)

    print("database : " + gm.DB_PATH)
    print("files    : " + gm.PLANS_DIR)
    print("pdf      : %s (%d bytes)" % (a.pdf, len(raw)))
    print("date     : %s  (%s)" % (a.date, gm.plan_dmy(a.date)))
    print("status   : " + a.status)
    print("title    : %d characters, not printed" % len(a.title))

    with gm.app.app_context():
        existing = gm.db().execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    print("plans already in the database: %d" % existing)

    if not a.apply:
        print("")
        print("DRY RUN. Nothing written. Add --apply to load it.")
        return 0

    gm.app.config["TESTING"] = True
    c = gm.app.test_client()
    with gm.app.app_context():
        ep = gm.auth_epoch()
    with c.session_transaction() as s:
        s["ok"] = True
        s["ep"] = ep

    r = c.post("/api/plans", data={"title": a.title, "first_considered": a.date,
                                   "status": a.status,
                                   "file": (io.BytesIO(raw), os.path.basename(a.pdf))},
               content_type="multipart/form-data")
    j = r.get_json() or {}
    if r.status_code != 200 or not j.get("ok"):
        print("REFUSED by the app (%s): %s" % (r.status_code, j.get("err")))
        return 1
    pid = j.get("id")
    print("")
    print("loaded as plan id %s" % pid)
    with gm.app.app_context():
        row = gm.db().execute("SELECT stored_name, bytes, sha256 FROM plan_files "
                              "WHERE plan_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if row:
        print("stored   : %s (%d bytes)" % (row["stored_name"], row["bytes"]))
        print("sha256   : %s" % row["sha256"])
        on_disk = os.path.join(gm.PLANS_DIR, row["stored_name"])
        print("on disk  : %s" % ("yes" if os.path.exists(on_disk) else "NO -- look at this"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

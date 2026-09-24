#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_gutlog_v3340_week0.py -- GUTLOG_V3340_FTMEALS one-off backfill.

Week 0 days from the plan's first day to today that already have a Dinner in
Meals get their Week 0 row now. Days with no Dinner stay unlogged -- nothing
is invented. It calls the app's OWN ft_dinner_sync(), so the backfill and
the running app cannot apply different rules. Dry run by default; --apply
takes a sqlite3.backup() first and prints its path. Idempotent: a second
--apply adds nothing.

  python3 migrate_gutlog_v3340_week0.py [--app /root/gutlog/app.py] [--from 2026-09-23] [--apply]

The database is the app's own (GUTLOG_DB, else health3.db beside app.py).
Python 3.9.
"""
import argparse
import importlib.util
import os
import sqlite3
import sys
from datetime import datetime, timedelta


def plan(gm, frm, to):
    """[(day, status, detail)] -- read-only. Status: 'add', 'linked',
    'no dinner', 'week 0 full', 'paused'."""
    it = gm.ft_week0()
    if not it:
        return []
    slug = it["slug"]
    cur, steps = gm.ft_progress(it, gm.ft_rows(slug))
    room = len(steps) - cur
    paused = (gm.setting("ft_paused") or "null") not in ("null", "")
    out = []
    d = gm.date.fromisoformat(frm)
    end = gm.date.fromisoformat(to)
    while d <= end:
        day = d.isoformat()
        meals = gm.db().execute("SELECT mtime, kcal FROM meals WHERE day=? AND slot=? ORDER BY mtime, id",
                                (day, gm.FT_DINNER_SLOT)).fetchall()
        row = gm.db().execute("SELECT id FROM ft_log WHERE slug=? AND kind='dinner' AND day=?",
                              (slug, day)).fetchone()
        det = ", ".join("%s (%s kcal)" % (m["mtime"], int(m["kcal"] or 0)) for m in meals)
        if row:
            out.append((day, "linked", det))
        elif not meals:
            out.append((day, "no dinner", ""))
        elif paused:
            out.append((day, "paused", det))
        elif room <= 0:
            out.append((day, "week 0 full", det))
        else:
            out.append((day, "add", det))
            room -= 1
        d += timedelta(days=1)
    return out


def run(gm, frm=None, to=None, apply=False, backup_dir=None):
    """The whole backfill against an imported app module. Returns a dict."""
    with gm.app.app_context():
        if not frm:
            frm = gm.ft_week0_from() if apply else _from_readonly(gm)
        to = to or gm.today()
        rows = plan(gm, frm, to)
        print("Week 0 from %s to %s" % (frm, to))
        for day, status, det in rows:
            print("  %s  %-11s %s" % (day, status, det))
        todo = [r[0] for r in rows if r[1] == "add"]
        if not apply:
            print("dry run: %d day(s) would be added. Re-run with --apply." % len(todo))
            return {"dry": True, "to_add": todo}
        bdir = backup_dir or os.path.dirname(os.path.abspath(gm.DB_PATH))
        dst = os.path.join(bdir, "health3-pre-v3340-week0-"
                           + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
        src = sqlite3.connect(gm.DB_PATH)
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
        chk = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
        print("backup: %s (integrity %s)" % (dst, chk))
        if chk != "ok":
            print("FATAL: the backup is not sound. Nothing written.")
            return {"dry": False, "error": "backup"}
        gm.set_setting("ft_week0_from", frm)
        added = gm.ft_dinner_sync(*[r[0] for r in rows])
        slug = gm.ft_week0()["slug"]
        linked = [r["day"] for r in gm.db().execute(
            "SELECT day FROM ft_log WHERE slug=? AND kind='dinner' AND day BETWEEN ? AND ? ORDER BY day",
            (slug, frm, to)).fetchall()]
        print("added: %d  ·  Week 0 days now logged: %s" % (added, ", ".join(linked) or "none"))
        return {"dry": False, "added": added, "linked": linked, "backup": dst}


def _from_readonly(gm):
    v = (gm.setting("ft_week0_from") or "").strip()
    if v:
        return v
    r = gm.db().execute("SELECT MIN(seeded) AS s FROM ft_items").fetchone()
    return ((r["s"] if r else "") or "")[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="/root/gutlog/app.py")
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", default=None)
    a = ap.parse_args()
    if "GUTLOG_V3340_FTMEALS" not in open(a.app, encoding="utf-8").read():
        print("FATAL: app.py is not v3.34.0 -- deploy it first.")
        return 1
    os.environ.setdefault("GUTLOG_NOSPAWN", "1")
    sys.path.insert(0, os.path.dirname(os.path.abspath(a.app)))
    spec = importlib.util.spec_from_file_location("gutlog_live", a.app)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    r = run(gm, a.frm, None, a.apply, a.backup_dir)
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())

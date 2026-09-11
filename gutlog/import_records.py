#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_records.py -- load the health record into GutLog v3.8.0 (Records).

  python3 import_records.py /root/gutlog/records_import

The folder is the medical-records folder as uploaded from the PC. It must
contain records_manifest.local.json (anywhere inside it). The manifest lists
which reports to take (duplicates and working copies are simply not listed),
each with its date, kind, title, source and one-line finding; the laboratory
values as printed; the investigation plan; and the summary profile.

  * Reports are copied into GutLog's uploads folder, de-duplicated by content
    (sha256). Re-running updates titles/findings and adds only new files.
  * Lab values are upserted by (date, test, laboratory) -- values exactly as
    printed, lab flag kept, a number extracted only for the chart.
  * Plan items are upserted by test; their done/planned status is kept.
  * A report may be named by "sha" instead of "path": a scan already sitting
    in uploads/inbox/ is then taken from there, and every inbox copy whose
    report is now in the record is removed.
  * The profile is written to records_profile.local.json (mode 600).
Prints counts only -- never clinical text. Python 3.9, standard library.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("GUTLOG_DB", os.path.join(HERE, "health3.db"))
UPLOADS = os.environ.get("GUTLOG_UPLOADS", os.path.join(HERE, "uploads"))
PROFILE = os.environ.get("GUTLOG_PROFILE", os.path.join(HERE, "records_profile.local.json"))
MANIFEST = "records_manifest.local.json"

DDL = """
CREATE TABLE IF NOT EXISTS rec_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, kind TEXT, title TEXT, source TEXT,
  finding TEXT, stored TEXT DEFAULT '', orig TEXT DEFAULT '', sha TEXT UNIQUE, status TEXT DEFAULT 'filed', created TEXT);
CREATE TABLE IF NOT EXISTS rec_labs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, test TEXT, section TEXT, value TEXT, num REAL,
  unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, UNIQUE(day, test, lab));
CREATE TABLE IF NOT EXISTS rec_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pos INTEGER, test TEXT UNIQUE, why TEXT, timing TEXT,
  status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '');
"""
_NUM = re.compile(r"[-+]?\d*\.?\d+")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def num_of(value):
    """Number for the chart only: first number in the printed value."""
    m = _NUM.search(str(value or "").replace(",", ""))
    try:
        return float(m.group(0)) if m else None
    except ValueError:
        return None


def find_manifest(root):
    for dirpath, _dirs, files in os.walk(root):
        if MANIFEST in files:
            return os.path.join(dirpath, MANIFEST)
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(root, db_path=DB, uploads=UPLOADS, profile_path=PROFILE):
    mpath = find_manifest(root)
    if not mpath:
        return {"ok": False, "err": "manifest %s not found under %s" % (MANIFEST, root)}
    base = os.path.dirname(mpath)
    with open(mpath, encoding="utf-8") as fh:
        man = json.load(fh)
    os.makedirs(uploads, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(DDL)
    for t, c, d in (("rec_docs", "origin", "TEXT DEFAULT ''"), ("rec_docs", "checked", "INTEGER DEFAULT 1"),
                    ("rec_docs", "file_id", "INTEGER"), ("rec_labs", "origin", "TEXT DEFAULT ''"),
                    ("rec_labs", "doc_id", "INTEGER")):
        if c not in [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (t, c, d))
    now = datetime.now().isoformat(timespec="seconds")
    st = {"docs_new": 0, "docs_updated": 0, "docs_missing": 0, "labs": 0, "plan": 0, "profile": False,
          "inbox_cleared": 0}
    inbox = os.path.join(uploads, "inbox")
    inbox_map = {}
    if os.path.isdir(inbox):
        for n in os.listdir(inbox):
            fp = os.path.join(inbox, n)
            if os.path.isfile(fp):
                inbox_map[sha256(fp)] = fp
    for d in man.get("docs") or []:
        src = os.path.join(base, *str(d.get("path", "")).replace("\\", "/").split("/")) if d.get("path") else ""
        if not (src and os.path.isfile(src)):
            src = inbox_map.get(str(d.get("sha") or ""), "")   # a scan already on the server
        if not src:
            st["docs_missing"] += 1
            continue
        h = sha256(src)
        day = d.get("day") if _DAY.match(str(d.get("day") or "")) else ""
        row = con.execute("SELECT id FROM rec_docs WHERE sha=?", (h,)).fetchone()
        meta = (day, d.get("kind") or "Other", (d.get("title") or os.path.basename(src))[:200],
                (d.get("source") or "")[:200], (d.get("finding") or "")[:1500])
        if row:
            con.execute("UPDATE rec_docs SET day=?, kind=?, title=?, source=?, finding=? WHERE id=?",
                        meta + (row[0],))
            st["docs_updated"] += 1
            continue
        ext = os.path.splitext(src)[1].lower() or ".pdf"
        stored = "rec_" + h[:24] + ext
        dest = os.path.join(uploads, stored)
        if not os.path.exists(dest):
            shutil.copy2(src, dest)
        con.execute("INSERT INTO rec_docs(day, kind, title, source, finding, stored, orig, sha, status, created) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    meta + (stored, os.path.basename(src)[:200], h, "filed", now))
        st["docs_new"] += 1
    for x in man.get("labs") or []:
        day, test = str(x.get("day") or ""), str(x.get("test") or "").strip()
        if not _DAY.match(day) or not test:
            continue
        value = str(x.get("value") or "").strip()
        flag = 1 if ("**" in value or x.get("flag")) else 0
        con.execute("INSERT INTO rec_labs(day, test, section, value, num, unit, ref, flag, lab, created) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(day, test, lab) DO UPDATE SET "
                    "section=excluded.section, value=excluded.value, num=excluded.num, unit=excluded.unit, "
                    "ref=excluded.ref, flag=excluded.flag",
                    (day, test, x.get("section") or "", value, num_of(value), x.get("unit") or "",
                     x.get("ref") or "", flag, x.get("lab") or "", now))
        st["labs"] += 1
    for i, p in enumerate(man.get("plan") or []):
        con.execute("INSERT INTO rec_plan(pos, test, why, timing) VALUES(?,?,?,?) ON CONFLICT(test) DO UPDATE "
                    "SET pos=excluded.pos, why=excluded.why, timing=excluded.timing",
                    (p.get("pos") or i + 1, p.get("test"), p.get("why") or "", p.get("timing") or ""))
        st["plan"] += 1
    con.commit()
    done = set(r[0] for r in con.execute("SELECT sha FROM rec_docs WHERE sha IS NOT NULL"))
    con.close()
    for h, fp in inbox_map.items():          # processed scans leave the inbox
        if h in done:
            try:
                os.remove(fp)
                st["inbox_cleared"] += 1
            except OSError:
                pass
    prof = man.get("profile")
    if isinstance(prof, dict):
        tmp = profile_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(prof, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, profile_path)
        os.chmod(profile_path, 0o600)
        st["profile"] = True
    st["ok"] = True
    return st


def main():
    if len(sys.argv) < 2:
        print("usage: python3 import_records.py <uploaded records folder>")
        return 1
    st = run(sys.argv[1])
    if not st.get("ok"):
        print("IMPORT FAILED: " + st.get("err", "?"))
        return 1
    print("records import: %d new reports, %d updated, %d listed but not found; %d lab values; "
          "%d plan items; %d processed scans cleared from the inbox; profile %s"
          % (st["docs_new"], st["docs_updated"], st["docs_missing"], st["labs"], st["plan"],
             st["inbox_cleared"], "written" if st["profile"] else "not in manifest"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

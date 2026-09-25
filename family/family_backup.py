#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_backup.py -- nightly backup of every family member, and the Kitchen.

FAMILY_EDITION_V1. Cron, as root (the only user that can read every member's
folder). For each member in /root/family/members.local.json -- enabled or
disabled, because a disabled member's data is still theirs -- and for the
Family Kitchen:

  * every SQLite database in their folder, copied with sqlite3.backup() (no
    sqlite3 CLI on this server) and checked with PRAGMA integrity_check;
  * everything else in the folder (reports, plan files, keys, tokens) in one
    tarball, with the uploaded files COUNTED in the tarball against the disk
    -- "it is already carried" is what was believed about the database that
    turned out not to be backed up at all (CLAUDE.md rule 4);
  * into /root/backups/family/<slug>/, one folder each, 30 days kept.

A run that cannot back something up says so and exits non-zero; it never
reports success for a copy it did not verify.

  python3 family_backup.py [--root /]     (--root is for the test suite)

Python 3.9.
"""
import argparse
import json
import os
import sqlite3
import sys
import tarfile
import time
from datetime import datetime

KEEP_DAYS = 30


def dbs_in(folder):
    out = []
    for r, _d, files in os.walk(folder):
        for f in files:
            if f.endswith(".db"):
                out.append(os.path.join(r, f))
    return sorted(out)


def backup_one(src, dst):
    s = sqlite3.connect("file:" + src + "?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    with d:
        s.backup(d)
    s.close()
    ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    d.close()
    os.chmod(dst, 0o600)
    return ok


def counted_files(folder):
    n = 0
    for r, _d, files in os.walk(folder):
        if os.sep + "uploads" in r or os.sep + "plans_files" in r or os.sep + "attach" in r:
            n += len(files)
    return n


def backup_folder(label, folder, dest_root, stamp):
    dest = os.path.join(dest_root, label)
    os.makedirs(dest, exist_ok=True)
    os.chmod(dest, 0o700)
    errs = []
    for db in dbs_in(folder):
        rel = os.path.relpath(db, folder).replace(os.sep, "_")[:-3]
        dst = os.path.join(dest, "%s-%s.db" % (rel, stamp))
        try:
            ok = backup_one(db, dst)
            print("  %s: %s -> %s (%s)" % (label, os.path.relpath(db, folder), os.path.basename(dst), ok))
            if ok != "ok":
                errs.append("%s integrity %s" % (db, ok))
        except Exception as exc:
            errs.append("%s: %s" % (db, exc))
    tpath = os.path.join(dest, "files-%s.tar.gz" % stamp)
    with tarfile.open(tpath, "w:gz") as t:
        def keep(ti):
            b = os.path.basename(ti.name)
            # Databases are copied above by sqlite3.backup(); their .bak copies
            # (FitLog's migration makes one) are not carried. <db>.secret is.
            if b.endswith(".db") or ".db-" in b or "__pycache__" in ti.name \
                    or (".db." in b and not b.endswith(".secret")):
                return None
            return ti
        t.add(folder, arcname=label, filter=keep)
    os.chmod(tpath, 0o600)
    with tarfile.open(tpath, "r:gz") as t:
        names = t.getnames()
    in_tar = sum(1 for n in names if ("/uploads/" in n or "/plans_files/" in n or "/attach/" in n)
                 and not n.endswith("/") and "." in os.path.basename(n))
    on_disk = counted_files(folder)
    print("  %s: files -> %s (%d of %d uploaded files carried)"
          % (label, os.path.basename(tpath), in_tar, on_disk))
    if in_tar < on_disk:
        errs.append("%s: %d uploaded files missing from the tarball" % (label, on_disk - in_tar))
    cut = time.time() - KEEP_DAYS * 86400
    for f in os.listdir(dest):
        p = os.path.join(dest, f)
        if os.path.isfile(p) and os.path.getmtime(p) < cut:
            os.remove(p)
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/")
    a = ap.parse_args()
    j = lambda p: os.path.join(a.root, p.lstrip("/"))
    try:
        with open(j("/root/family/members.local.json"), encoding="utf-8") as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        reg = {"members": []}
    stamp = datetime.now().strftime("%Y%m%d")
    dest_root = j("/root/backups/family")
    os.makedirs(dest_root, exist_ok=True)
    os.chmod(dest_root, 0o700)
    print("family backup %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    errs = []
    targets = [(m["slug"], j("/srv/family/" + m["slug"])) for m in reg.get("members") or []]
    targets.append(("kitchen", j("/srv/family/kitchen")))
    for label, folder in targets:
        if not os.path.isdir(folder):
            if label != "kitchen":
                errs.append("%s: folder %s is missing" % (label, folder))
            continue
        errs += backup_folder(label, folder, dest_root, stamp)
    if errs:
        print("BACKUP INCOMPLETE:")
        for e in errs:
            print("  " + e)
        return 1
    print("ok: %d folder(s)" % len([t for t in targets if os.path.isdir(t[1])]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

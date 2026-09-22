#!/bin/bash
set -euo pipefail
STAMP=$(date +%F)
APP=/root/gutlog
DEST=/root/backups/gutlog
mkdir -p "$DEST"
shopt -s nullglob

for db in "$APP"/*.db; do
  base=$(basename "$db" .db)
  case "$base" in backup_*) continue ;; esac
  /usr/bin/python3 - "$db" "$DEST/${base}-$STAMP.db" <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2])
with d:
    s.backup(d)
d.close(); s.close(); print("db ->", sys.argv[2])
PY
done

# The databases are already copied above by sqlite3.backup(), so the tarball
# needs neither the live files nor their timestamped snapshots. Those snapshots
# do not END in .db -- health3.db.bak-v340-20260910_105612, health3.db.pre-v3260-*
# and so on -- so '*.db' alone let about twenty full copies of the diary into
# every nightly tarball (91 MB on 2026-09-20).
#
# '*.db*' would catch them, but it also catches health3.db.secret, the key that
# the backup exists to preserve, and tar has no way to put an excluded file back
# (naming it explicitly on the command line does not work -- measured). So:
#   *.db        the live databases
#   *.db-*      sqlite sidecars: -wal, -shm, -journal
#   *.db.[!s]*  every *.db.<tag> snapshot EXCEPT *.db.secret*, which is kept
tar czf "$DEST/gutlog-files-$STAMP.tar.gz" \
  --exclude='*.db' --exclude='*.db-*' --exclude='*.db.[!s]*' \
  --exclude='__pycache__' --exclude='venv' --exclude='.git' \
  --exclude='backup_*' -C /root gutlog
echo "files -> $DEST/gutlog-files-$STAMP.tar.gz"

# GUTLOG_V3280_PLANS. The plan documents live in $APP/plans_files, which is
# inside the folder the tar above already walks -- so they are carried without
# a new rule, and no exclusion matches them. But "it is already carried" is
# exactly what was believed about the database that turned out not to be
# backed up at all (CLAUDE.md rule 4). So count them and SAY so, and refuse
# quietly succeeding if any are missing.
PLANS_IN=$(tar tzf "$DEST/gutlog-files-$STAMP.tar.gz" | grep -c '^gutlog/plans_files/..*' || true)
PLANS_ON=$(find "$APP/plans_files" -type f 2>/dev/null | wc -l)
echo "plans -> $PLANS_IN of $PLANS_ON plan document(s) in the tarball"
if [ "$PLANS_ON" -gt 0 ] && [ "$PLANS_IN" -lt "$PLANS_ON" ]; then
  echo "BACKUP INCOMPLETE: plan documents are missing from the tarball" >&2
  exit 1
fi

find "$DEST" -name '*-20*.db' -mtime +30 -delete
find "$DEST" -name 'gutlog-files-*.tar.gz' -mtime +30 -delete

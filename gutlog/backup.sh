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

tar czf "$DEST/gutlog-files-$STAMP.tar.gz" \
  --exclude='*.db' --exclude='__pycache__' --exclude='venv' --exclude='.git' \
  --exclude='backup_*' -C /root gutlog
echo "files -> $DEST/gutlog-files-$STAMP.tar.gz"

find "$DEST" -name '*-20*.db' -mtime +30 -delete
find "$DEST" -name 'gutlog-files-*.tar.gz' -mtime +30 -delete

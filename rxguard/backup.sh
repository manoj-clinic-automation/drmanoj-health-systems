#!/bin/bash
# RxGuard backup. Uses Python's sqlite3 module — no sqlite3 CLI needed.
set -euo pipefail
STAMP=$(date +%F)
DEST=/root/backups/rxguard
SRC=/root/rxguard/rxguard.db
mkdir -p "$DEST"

if [ -f "$SRC" ]; then
  /usr/bin/python3 - "$SRC" "$DEST/rxguard-$STAMP.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
with d:
    s.backup(d)
d.close(); s.close()
print("database ->", dst)
PY
else
  echo "no database yet at $SRC"
fi

tar czf "$DEST/knowledge-$STAMP.tar.gz" -C /root/rxguard knowledge
echo "knowledge -> $DEST/knowledge-$STAMP.tar.gz"

find "$DEST" -name 'rxguard-*.db' -mtime +30 -delete
find "$DEST" -name 'knowledge-*.tar.gz' -mtime +30 -delete

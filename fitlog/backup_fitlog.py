#!/usr/bin/env python3
"""FitLog backup - sqlite3.backup() API (no sqlite3 CLI on server). Cron: 20 2 * * *
Verifies the LIVE file path explicitly (GutLog lesson: silent cron success != correctness)."""
import sqlite3, os, glob, sys
from datetime import datetime, timedelta

LIVE = "/root/fitlog/fitlog.db"          # <-- the actual live DB. Verify after deploy.
DEST_DIR = "/root/backups/fitlog"
RETAIN_DAYS = 30

if not os.path.exists(LIVE):
    print(f"FATAL: live DB not found at {LIVE}"); sys.exit(1)
os.makedirs(DEST_DIR, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M")
dest = os.path.join(DEST_DIR, f"fitlog_{stamp}.db")
src = sqlite3.connect(LIVE); dst = sqlite3.connect(dest)
with dst: src.backup(dst)
src.close(); dst.close()
# integrity check on the copy
chk = sqlite3.connect(dest).execute("PRAGMA integrity_check").fetchone()[0]
if chk != "ok":
    print(f"FATAL: backup integrity check failed: {chk}"); sys.exit(1)
n = sqlite3.connect(dest).execute("SELECT COUNT(*) FROM checkins").fetchone()[0]
print(f"OK {dest} ({os.path.getsize(dest)} bytes, {n} checkins, integrity ok)")
cutoff = datetime.now() - timedelta(days=RETAIN_DAYS)
for f in glob.glob(os.path.join(DEST_DIR, "fitlog_*.db")):
    if datetime.fromtimestamp(os.path.getmtime(f)) < cutoff:
        os.remove(f); print(f"pruned {f}")

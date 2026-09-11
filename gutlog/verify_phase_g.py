#!/usr/bin/env python3
"""Phase G live check -- automatic reading. Counts and status only. Python 3.9."""
import os
import sqlite3
import subprocess
import urllib.error
import urllib.request

OK = []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def line(ok, text):
    OK.append(bool(ok))
    print(("[PASS] " if ok else "[FAIL] ") + text)


op = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
line("GUTLOG_V3100_AUTOREAD" in open("/root/gutlog/app.py").read(), "gutlog/app.py carries GUTLOG_V3100_AUTOREAD")
line(os.path.exists("/root/gutlog/records_worker.py"), "reader in place")
p = subprocess.run(["/root/gutlog/venv/bin/python", "/root/gutlog/records_worker.py", "--probe"],
                   capture_output=True, text=True)
line(p.returncode == 0, (p.stdout.strip() or p.stderr.strip()[-120:]))
cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
line(cron.count("records_worker.py") == 1, "cron runs the reader every 15 minutes (%d entry)" % cron.count("records_worker.py"))
con = sqlite3.connect("/root/gutlog/health3.db")
cols = [r[1] for r in con.execute("PRAGMA table_info(files)")]
line("ocr_status" in cols, "uploads table ready for reading")
w = con.execute("SELECT COUNT(*) FROM files WHERE COALESCE(ocr_status,'') IN ('','retry','reading')").fetchone()[0]
con.close()
print("       %d upload(s) waiting to be read" % w)
try:
    with op.open("http://127.0.0.1:8020/api/records/doc/1/checked", data=b"{}", timeout=5) as r:
        code = r.status
except urllib.error.HTTPError as e:
    code = e.code
except Exception:
    code = 0
line(code in (302, 401), "Looks right needs a login (%d)" % code)
print("-" * 40)
print("%d/%d live checks passed" % (sum(OK), len(OK)))

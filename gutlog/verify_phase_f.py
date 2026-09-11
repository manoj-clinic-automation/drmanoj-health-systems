#!/usr/bin/env python3
"""Phase F live check -- after the restarts and the records import. Prints
counts and status only: no names, no values, no token. Python 3.9."""
import json
import os
import sqlite3
import urllib.error
import urllib.request

OK = []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


op = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def get(url, tok=None):
    h = {"Authorization": "Bearer " + tok} if tok else {}
    try:
        with op.open(urllib.request.Request(url, headers=h), timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, type(e).__name__


def line(ok, text):
    OK.append(bool(ok))
    print(("[PASS] " if ok else "[FAIL] ") + text)


tok = open("/root/gutlog/feed.token").read().strip()
line("GUTLOG_V380_RECORDS" in open("/root/gutlog/app.py").read(), "gutlog/app.py carries GUTLOG_V380_RECORDS")
line("GUTLOG_V390_SCAN" in open("/root/gutlog/app.py").read(), "gutlog/app.py carries GUTLOG_V390_SCAN")
line(os.path.exists("/root/gutlog/scanner_widget.js"), "scanner widget in place")
line("RXGUARD_V140_CONDITIONS" in open("/root/rxguard/app.py").read(), "rxguard/app.py carries RXGUARD_V140_CONDITIONS")
con = sqlite3.connect("/root/gutlog/health3.db")
n = lambda t: con.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
nd, nl, npl = n("rec_docs"), n("rec_labs"), n("rec_plan")
missing = [r[0] for r in con.execute("SELECT stored FROM rec_docs WHERE stored<>''")
           if not os.path.exists(os.path.join("/root/gutlog/uploads", r[0]))]
con.close()
line(nd >= 45, "records: %d reports filed" % nd)
line(not missing, "every filed report is on disk (%d missing)" % len(missing))
line(nl >= 300, "records: %d laboratory values" % nl)
line(npl >= 10, "records: %d plan items" % npl)
p = "/root/gutlog/records_profile.local.json"
mode = oct(os.stat(p).st_mode & 0o777) if os.path.exists(p) else "missing"
line(mode == "0o600", "health profile present, mode " + mode)
for u in ("/api/records/summary", "/api/records/docs", "/api/records/labs", "/scan", "/scanner_widget.js"):
    code, _ = get("http://127.0.0.1:8020" + u)
    line(code in (302, 401), "GutLog %s needs a login (%d)" % (u, code))
code, _ = get("http://127.0.0.1:8020/api/feed/profile")
line(code == 401, "profile feed refuses no token (%d)" % code)
code, body = get("http://127.0.0.1:8020/api/feed/profile", tok)
codes = json.loads(body).get("conditions", []) if code == 200 else []
line(code == 200 and len(codes) >= 5, "profile feed gives %d condition codes, nothing else" % len(codes))
code, body = get("http://127.0.0.1:8031/healthz")
line(code == 200 and "1.4.0" in body, "RxGuard up: " + body.strip()[:40])
rc = sqlite3.connect("/root/rxguard/rxguard.db")
act = rc.execute("SELECT COUNT(*) FROM conditions WHERE active=1").fetchone()[0]
rc.close()
line(act >= len([c for c in codes]) - 1, "RxGuard: %d conditions active" % act)
code, _ = get("http://127.0.0.1:8040/health")
line(code == 200, "FitLog up (%d)" % code)
print("-" * 40)
print("%d/%d live checks passed" % (sum(OK), len(OK)))

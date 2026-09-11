#!/usr/bin/env python3
"""Phase D live check -- run on the VPS after the restarts and the first
source sync. Prints counts and status only: no medicine names, no token.
Python 3.9."""
import json
import os
import subprocess
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


def js(body):
    try:
        return json.loads(body)
    except ValueError:
        return {}


tok = open("/root/gutlog/feed.token").read().strip()
for f, m in (("/root/gutlog/app.py", "GUTLOG_V370_SALTS_ACTIVITY"), ("/root/rxguard/app.py", "RXGUARD_V120_SOURCES"),
             ("/root/fitlog/app.py", "FITLOG_V120_ACTIVITY"), ("/root/fitlog/health_ingest.py", "FITLOG_V120_ACTIVITY")):
    line(m in open(f).read(), f.split("/")[2] + "/" + os.path.basename(f) + " carries " + m)

code, _ = get("http://127.0.0.1:8020/api/feed/activities")
line(code == 401, "GutLog activities feed refuses no token (%d)" % code)
code, body = get("http://127.0.0.1:8020/api/feed/activities", tok)
line(code == 200 and js(body).get("ok"), "GutLog activities feed answers with the token (%d rows)"
     % len(js(body).get("activities") or []))
code, body = get("http://127.0.0.1:8020/api/feed/stack", tok)
reg = js(body).get("regimen") or []
line(code == 200 and all("strength" in r for r in reg), "GutLog stack feed carries strength (%d lines)" % len(reg))
code, _ = get("http://127.0.0.1:8020/api/salts")
line(code in (302, 401), "GutLog Salts needs a login (%d)" % code)

code, body = get("http://127.0.0.1:8031/healthz")
line(code == 200 and "1.2.0" in body, "RxGuard up: " + body.strip()[:60])
code, _ = get("http://127.0.0.1:8031/api/feed/status")
line(code == 401, "RxGuard status feed refuses no token (%d)" % code)
code, body = get("http://127.0.0.1:8031/api/feed/status", tok)
s = js(body)
line(code == 200 and s.get("ok"), "RxGuard status: %s drafts to review, %s interaction candidates, %s RED, %s AMBER"
     % (s.get("drafts"), s.get("pairs"), s.get("red"), s.get("amber")))
code, _ = get("http://127.0.0.1:8031/kb")
line(code in (302, 401), "RxGuard Sources review needs a login (%d)" % code)

code, body = get("http://127.0.0.1:8040/health")
line(code == 200 and "1.2.0" in body, "FitLog up: " + body.strip()[:60])
code, _ = get("http://127.0.0.1:8040/api/feed/activity")
line(code == 401, "FitLog activity feed refuses no token (%d)" % code)
code, body = get("http://127.0.0.1:8040/api/feed/activity", tok)
a = js(body)
line(code == 200 and a.get("ok"), "FitLog activity feed: today %s steps, %s workouts, %s mindful min"
     % (a.get("steps"), len(a.get("workouts") or []), a.get("mindful_min")))

cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
line(cron.count("kb_sync.py") == 1, "cron runs the source sync every 30 minutes (%d entry)" % cron.count("kb_sync.py"))
print("-" * 40)
print("%d/%d live checks passed" % (sum(OK), len(OK)))

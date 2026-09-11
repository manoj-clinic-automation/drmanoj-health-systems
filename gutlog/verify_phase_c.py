#!/usr/bin/env python3
"""Phase C live check -- run on the VPS after the restarts. Prints counts and
status only: no medicine names, no token. Python 3.9."""
import json
import os
import stat
import urllib.request
import urllib.error

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
    OK.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + text)


p = "/root/gutlog/feed.token"
mode = oct(os.stat(p).st_mode & 0o777) if os.path.exists(p) else "missing"
line(mode == "0o600", "feed.token present, mode " + mode)
tok = open(p).read().strip() if os.path.exists(p) else ""

code, _ = get("http://127.0.0.1:8020/api/feed/stack")
line(code == 401, "GutLog feed refuses a request with no token (" + str(code) + ")")
code, body = get("http://127.0.0.1:8020/api/feed/stack", tok)
try:
    j = json.loads(body)
    line(code == 200 and j.get("ok"), "GutLog feed answers with the token: %d regimen lines, %d medicines taken in 14 days"
         % (len(j.get("regimen", [])), len(j.get("taken", []))))
    nomol = sum(1 for t in j.get("taken", []) if not t.get("molecule"))
    print("       (%d of those have no molecule recorded)" % nomol)
except ValueError:
    line(False, "GutLog feed answer was not JSON (" + str(code) + ")")
code, body = get("http://127.0.0.1:8020/api/stock")
line(code in (302, 401), "GutLog stock page needs a login (" + str(code) + ")")
code, body = get("http://127.0.0.1:8031/healthz")
line(code == 200 and "1.1.0" in body, "RxGuard up: " + body.strip())
code, body = get("http://127.0.0.1:8040/health")
line(code == 200, "FitLog up (" + str(code) + ")")
for f, m in (("/root/rxguard/app.py", "RXGUARD_V110_ASTAKEN"), ("/root/fitlog/app.py", "FITLOG_V110_GUTLOG_FEED"),
             ("/root/gutlog/app.py", "GUTLOG_V360_PHASE_C")):
    line(m in open(f).read(), os.path.basename(os.path.dirname(f)) + " carries " + m)
print("-" * 40)
print("%d/%d live checks passed" % (sum(OK), len(OK)))

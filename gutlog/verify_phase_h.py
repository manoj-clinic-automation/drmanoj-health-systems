#!/usr/bin/env python3
"""Phase H live check -- scan quality. Status only, no image maths. Python 3.9."""
import hashlib
import subprocess
import urllib.request

OK = []
def line(ok, text):
    OK.append(bool(ok)); print(("[PASS] " if ok else "[FAIL] ") + text)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
app = open("/root/gutlog/app.py").read()
wid = open("/root/gutlog/scanner_widget.js").read()

line("GUTLOG_V3110_SCANQ" in app, "gutlog/app.py carries GUTLOG_V3110_SCANQ")
line("SCANNER_REPORT_V24" in wid, "scanner_widget.js is the report build (v2.4)")
line("captureMax: 2600" in app and "wholePageFirst: true" in app,
     "the scan page asks for full-page, 220dpi capture")
line("MAX_FILE_MB = 25" in app, "upload ceiling 25 MB")
try:
    with op.open("http://127.0.0.1:8020/scanner_widget.js", timeout=8) as r:
        body = r.read().decode("utf-8", "replace"); code = r.status
except Exception as e:
    body, code = "", 0
    print("       fetch failed: %s" % e)
line(code == 200 and "SCANNER_REPORT_V24" in body,
     "the served widget is the report build (%d, md5 %s)" % (code, hashlib.md5(body.encode()).hexdigest()[:8]))
st = subprocess.run(["systemctl", "is-active", "gutlog"], capture_output=True, text=True).stdout.strip()
line(st == "active", "gutlog service %s" % st)
print("-" * 40)
print("%d/%d live checks passed" % (sum(OK), len(OK)))

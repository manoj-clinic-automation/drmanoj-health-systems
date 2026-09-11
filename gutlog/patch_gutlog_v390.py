#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.8.0 -> v3.9.0  ::  the clinic scanner inside GutLog

  * /scan -- the same document scanner the clinic uses (scanner_widget v2.3:
    live camera, autocrop to the page, perspective flattening, contrast for
    print, multi-page PDF, batch mode). Choose the type and the report date,
    scan, Save: the PDF is stored in GutLog and listed under Records, Reports.
  * Records gets a "Scan a report" button (Upload and Reports).
  * Every upload is fingerprinted and a readable copy is kept in
    uploads/inbox/, so a batch can go to the PC for processing in one drag.
    A report already processed into the record drops off the waiting list.
  * Deep link /?open=records opens Records, Reports.

Needs scanner_widget.js beside app.py (shipped with this patch).
Requires v3.8.0. Anchor-verified, idempotent, compile-checked, Jinja-safe,
.bak before write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V390_SCAN"
PREV = "GUTLOG_V380_RECORDS"

PY = '# ------------------------------------------------------------------ scanner\n# GUTLOG_V390_SCAN -- the clinic\'s document scanner (scanner_widget v2.3, the\n# same file the Asset Register and finance screens use) inside GutLog. Every\n# upload, scanned or chosen, also lands in uploads/inbox/ under a readable\n# name, so a batch can be taken to the PC for processing in one drag.\nimport hashlib\nimport shutil\nSCANNER_JS = os.path.join(BASE, "scanner_widget.js")\nINBOX_DIR = os.path.join(UPLOAD_DIR, "inbox")\n\n\ndef _sha_file(path):\n    h = hashlib.sha256()\n    with open(path, "rb") as fh:\n        for chunk in iter(lambda: fh.read(1 << 20), b""):\n            h.update(chunk)\n    return h.hexdigest()\n\n\ndef _after_upload(stored):\n    """Fingerprint the new file and drop a readable copy into the inbox.\n    Never fails the upload."""\n    try:\n        r = db().execute("SELECT id, day, label FROM files WHERE stored=?", (stored,)).fetchone()\n        if not r:\n            return\n        src = os.path.join(UPLOAD_DIR, stored)\n        db().execute("UPDATE files SET sha=? WHERE id=?", (_sha_file(src), r["id"]))\n        db().commit()\n        os.makedirs(INBOX_DIR, exist_ok=True)\n        base = secure_filename(os.path.splitext(r["label"] or "")[0])[:60] or "report"\n        shutil.copy2(src, os.path.join(INBOX_DIR, "%s_%s_%d%s" % (r["day"], base, r["id"],\n                                                                   os.path.splitext(stored)[1])))\n    except Exception:\n        pass\n\n\n@app.route("/scanner_widget.js")\n@login_required\ndef scanner_widget_js():\n    if not os.path.exists(SCANNER_JS):\n        abort(404)\n    with open(SCANNER_JS, "rb") as fh:\n        body = fh.read()\n    return Response(body, mimetype="application/javascript", headers={"Cache-Control": "no-cache"})\n\n\nSCAN_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scan a report - GutLog</title>\n<style>\nbody{font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;margin:0;background:#F3F6F5;color:#1B2B28}\nheader{background:#0F6B5C;color:#fff;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:9}\nheader a{color:#fff;text-decoration:none;font-weight:700;padding:6px 10px;border:1.5px solid rgba(255,255,255,.7);border-radius:9px}\nmain{padding:12px 16px;max-width:720px;margin:0 auto}\n.card{background:#fff;border-radius:14px;padding:14px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}\n.row{display:flex;gap:10px;flex-wrap:wrap}\nlabel.f{flex:1;min-width:140px;font-size:13px;color:#5B6B67;font-weight:700}\nlabel.f select,label.f input{display:block;width:100%;margin-top:4px;font-size:16px;padding:10px;border:1px solid #CFD8D5;border-radius:10px;box-sizing:border-box;background:#fff;color:#1B2B28}\n#scanroot .btn,#scanroot button{background:#0F6B5C;color:#fff;border:0}\n.muted{color:#6A7773;font-size:13px}\n</style></head><body>\n<header><b>Scan a report</b><a href="/?open=records">Done</a></header>\n<main>\n<div class="card"><div class="row">\n<label class="f">Type<select id="s_type"><option>Lab report</option><option>Imaging report</option>\n<option>Prescription</option><option>Consultation note</option><option>Discharge summary</option><option>Other</option></select></label>\n<label class="f">Report date<input type="date" id="s_day" value="__DAY__" max="__DAY__"></label>\n</div><p class="muted">Each saved PDF goes to Records, Reports, where it waits to be processed into your record.\nBatch mode saves every page as its own file.</p></div>\n<div class="card"><div id="scanroot"></div></div>\n</main>\n<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>\n<script>\nwindow.SCANNER_CONFIG = {title: "Scan a report", uploadUrl: "/api/upload", fileField: "file",\n  uploadFields: {ftype: "Lab report", day: "__DAY__"}, nameBase: "Lab_report", backUrl: "/?open=records",\n  allowIdCard: false, allowBatch: true};\n(function () {\n  var C = window.SCANNER_CONFIG;\n  document.getElementById("s_type").onchange = function () {\n    C.uploadFields.ftype = this.value; C.nameBase = this.value.replace(/\\s+/g, "_");\n  };\n  document.getElementById("s_day").onchange = function () { C.uploadFields.day = this.value || "__DAY__"; };\n})();\n</script>\n<script src="/scanner_widget.js?v=__V__"></script>\n</body></html>\n"""\n\n\n@app.route("/scan")\n@login_required\ndef scan_page():\n    try:\n        v = str(int(os.path.getmtime(SCANNER_JS)))\n    except OSError:\n        v = "0"\n    html = SCAN_PAGE.replace("__DAY__", today()).replace("__V__", v)\n    return Response(html, mimetype="text/html")\n\n\n'
SCANCARD = '    <a class="scanbtn" href="/scan">&#128247; Scan a report with the camera</a>\n    <p class="hint" style="margin-top:0">Autocrop, flattening and multi-page PDF, the same scanner the clinic uses.</p>\n'
CSS = '/* GUTLOG_V390_SCAN */\n.scanbtn{display:block;text-align:center;background:var(--teal);color:#fff;font-weight:800;font-size:16px;\n  padding:14px 12px;border-radius:13px;text-decoration:none;margin:0 0 10px;box-shadow:0 1px 3px rgba(0,0,0,.12)}\n'


def build_edits():
    E = []
    a = "GUTLOG_V370_SALTS_ACTIVITY GUTLOG_V380_RECORDS"
    E.append(("version", a, a + " " + MARKER))
    a = '    ("med_schedule", "variants", "TEXT DEFAULT \'\'"),\n]'
    E.append(("files.sha column", a, '    ("med_schedule", "variants", "TEXT DEFAULT \'\'"),\n    ("files", "sha", "TEXT DEFAULT \'\'"),\n]'))
    a = "# ------------------------------------------------------------------ review\n"
    E.append(("python", a, PY + a))
    a = ('            secure_filename(f.filename)[:120], size])\n    return jsonify(ok=True)\n')
    E.append(("upload inbox", a, '            secure_filename(f.filename)[:120], size])\n    _after_upload(stored)\n    return jsonify(ok=True)\n'))
    a = '"SELECT id, day, ftype, label FROM files ORDER BY day DESC, id DESC"'
    E.append(("hide processed", a, '"SELECT id, day, ftype, label FROM files WHERE COALESCE(sha,\'\')=\'\' OR sha NOT IN "\n'
                                   '                             "(SELECT sha FROM rec_docs WHERE sha IS NOT NULL) ORDER BY day DESC, id DESC"'))
    a = ('<p class="hint">New reports, prescriptions, scan photos. PDF / JPG / PNG, up to 12 MB. '
         'They appear under Reports and are processed into your record.</p>')
    E.append(("scan card upload", a, SCANCARD + a))
    a = '  <div class="sub" id="files-reports">\n    <div class="chips" id="rdKinds"></div>\n'
    E.append(("scan button reports", a, '  <div class="sub" id="files-reports">\n'
                                        '    <a class="scanbtn" href="/scan">&#128247; Scan a report</a>\n'
                                        '    <div class="chips" id="rdKinds"></div>\n'))
    a = "  if(!p)return;\n  switchTab('now');\n"
    E.append(("deep link records", a, "  if(!p)return;\n  if(p==='records'){switchTab('files');setSeg('files','reports');return;}\n  switchTab('now');\n"))
    a = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n</style></head><body>"
    E.append(("css", a, CSS + a))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow", "buildNowStatics",
               "bindFolds", "loadDayView", "dvEdit", "dvMissRow", "loadReview", "loadStock",
               "loadStockAlerts", "stockRow", "renderVitals", "vitalsChart", "loadMedStatus",
               "loadSalts", "saltGuess", "buildActTiles", "loadActivity",
               "loadRecSummary", "loadRecDocs", "loadRecTrends", "loadRecPlan", "loadRecords", "spark"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("GutLog scanner -> v3.9.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.8.0. Apply that first.")
        return 1
    if "def scan_page" in src:
        print("FATAL: v3.7.0 pieces already present -- unexpected state. Nothing written.")
        return 1
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(args.file)), "scanner_widget.js")):
        print("FATAL: scanner_widget.js must be copied beside app.py first.")
        return 1
    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2
    if args.check:
        print("All anchors OK.")
        return 0
    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    missing = [f for f in MUST_DEFINE if not re.search(r"function\s+" + f + r"\s*\(", out)]
    if missing:
        print("DEFINITION CHECK FAILED, nothing written: " + ", ".join(missing))
        return 2
    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    with open(tmpf, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v390-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("-" * 60)
    print("Next:  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

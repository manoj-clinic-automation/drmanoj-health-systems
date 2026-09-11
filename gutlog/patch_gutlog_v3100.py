#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.9.0 -> v3.10.0  ::  reports read automatically

  * Every upload or scan starts records_worker.py at once (and cron runs it
    every 15 minutes as a safety net): Sarvam Document Intelligence reads
    the report, and it is filed -- Reports, Trends, plan ticks -- with no
    step from you.
  * Machine-read reports carry a "machine-read" mark and a "Looks right"
    button until you glance at them; Trends marks their values "auto".
  * Waiting uploads say what is happening: being read, will retry, or
    could not be read (and why).
  * The Summary says how many machine-read reports await a glance.

Requires v3.9.0 and records_worker.py beside app.py. Anchor-verified,
idempotent, compile-checked, Jinja-safe, .bak before write, self-restoring.
Python 3.9.
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
MARKER = "GUTLOG_V3100_AUTOREAD"
PREV = "GUTLOG_V390_SCAN"

PY = '# ------------------------------------------------------------------ auto-read\n# GUTLOG_V3100_AUTOREAD -- start the report reader the moment a file lands.\nimport subprocess\nimport sys as _sys\nWORKER = os.path.join(BASE, "records_worker.py")\n\n\ndef _spawn_reader():\n    """Start records_worker.py in the background. Live database only (tests\n    and scratch copies never reach out); GUTLOG_NOSPAWN=1 disables."""\n    if os.environ.get("GUTLOG_NOSPAWN") == "1" or not _links_enabled() or not os.path.exists(WORKER):\n        return False\n    try:\n        logf = open(os.path.join(BASE, "records_worker.log"), "a")\n        subprocess.Popen([_sys.executable, WORKER], cwd=BASE, stdout=logf, stderr=logf, start_new_session=True)\n        return True\n    except Exception:\n        return False\n\n\ndef _upload_note(r):\n    st, note = (r["ocr_status"] or ""), (r["ocr_note"] or "")\n    if st in ("", "reading"):\n        return "Being read automatically - it joins your record in a minute or two."\n    if st == "retry":\n        return "Could not be read yet (%s) - trying again shortly." % note\n    if st == "failed":\n        return "Could not be read automatically (%s). It stays here as a file." % note\n    if st == "skipped":\n        return "Kept as a file (%s)." % note\n    return "Uploaded by you."\n\n\n@app.route("/api/records/doc/<int:did>/checked", methods=["POST"])\n@login_required\ndef api_rec_checked(did):\n    cur = db().execute("UPDATE rec_docs SET checked=1 WHERE id=?", (did,))\n    db().commit()\n    if not cur.rowcount:\n        return jsonify(ok=False, err="Not found."), 404\n    return jsonify(ok=True)\n\n\n'
CSS = '/* GUTLOG_V3100_AUTOREAD */\n.rd-auto{background:#FFF3DC;color:#8A5A00;border-radius:6px;padding:1px 6px;font-weight:700}\n#tab-files .rd-ok{margin-top:8px;margin-right:10px;border:1.5px solid var(--teal);color:var(--teal);background:#fff}\n.rs-auto{background:#FFF8EA;border-radius:10px;padding:8px 10px;margin:0 0 10px}\n'


def build_edits():
    E = []
    a = "GUTLOG_V380_RECORDS GUTLOG_V390_SCAN"
    E.append(("version", a, a + " " + MARKER))
    a = "finding TEXT, stored TEXT DEFAULT '', orig TEXT DEFAULT '', sha TEXT UNIQUE, status TEXT DEFAULT 'filed', created TEXT);\nCREATE TABLE IF NOT EXISTS rec_labs ("
    E.append(("rec_docs ddl", a, "finding TEXT, stored TEXT DEFAULT '', orig TEXT DEFAULT '', sha TEXT UNIQUE, status TEXT DEFAULT 'filed', created TEXT,\n"
                                 "  origin TEXT DEFAULT '', checked INTEGER DEFAULT 1, file_id INTEGER);\nCREATE TABLE IF NOT EXISTS rec_labs ("))
    a = "unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, UNIQUE(day, test, lab));"
    E.append(("rec_labs ddl", a, "unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, origin TEXT DEFAULT '', doc_id INTEGER,\n"
                                 "  UNIQUE(day, test, lab));"))
    a = '    ("files", "sha", "TEXT DEFAULT \'\'"),\n]'
    E.append(("columns", a, '    ("files", "sha", "TEXT DEFAULT \'\'"),\n'
                            '    ("files", "ocr_status", "TEXT DEFAULT \'\'"),\n    ("files", "ocr_note", "TEXT DEFAULT \'\'"),\n'
                            '    ("files", "ocr_tries", "INTEGER DEFAULT 0"),\n    ("rec_docs", "origin", "TEXT DEFAULT \'\'"),\n'
                            '    ("rec_docs", "checked", "INTEGER DEFAULT 1"),\n    ("rec_docs", "file_id", "INTEGER"),\n'
                            '    ("rec_labs", "origin", "TEXT DEFAULT \'\'"),\n    ("rec_labs", "doc_id", "INTEGER"),\n]'))
    a = "    _after_upload(stored)\n    return jsonify(ok=True)\n"
    E.append(("spawn on upload", a, "    _after_upload(stored)\n    _spawn_reader()\n    return jsonify(ok=True)\n"))
    a = "# ------------------------------------------------------------------ review\n"
    E.append(("python", a, PY + a))
    a = '"SELECT id, day, kind, title, source, finding, status, (stored<>\'\') AS has_file "'
    E.append(("docs select", a, '"SELECT id, day, kind, title, source, finding, status, (stored<>\'\') AS has_file, "\n'
                                '        "COALESCE(origin,\'\') AS origin, COALESCE(checked,1) AS checked "'))
    a = ('    ups = [{"id": r["id"], "day": r["day"], "kind": "Uploaded", "title": r["label"],\n'
         '            "source": r["ftype"], "finding": "Uploaded by you - waiting to be processed into the record.",\n'
         '            "status": "inbox", "has_file": 1, "vault": True}\n'
         '           for r in db().execute("SELECT id, day, ftype, label FROM files WHERE')
    E.append(("uploads note", a, '    ups = [{"id": r["id"], "day": r["day"], "kind": "Uploaded", "title": r["label"],\n'
                                 '            "source": r["ftype"], "finding": _upload_note(r),\n'
                                 '            "status": "inbox", "has_file": 1, "vault": True, "ocr": r["ocr_status"] or ""}\n'
                                 '           for r in db().execute("SELECT id, day, ftype, label, ocr_status, ocr_note FROM files WHERE'))
    a = '"SELECT day, value, num, unit, ref, flag, lab FROM rec_labs WHERE test=? ORDER BY day, id"'
    E.append(("series origin", a, '"SELECT day, value, num, unit, ref, flag, lab, COALESCE(origin,\'\') AS origin FROM rec_labs "\n'
                                  '        "WHERE test=? ORDER BY day, id"'))
    a = '                   plan={"total": len(plan), "done": sum(1 for x in plan if x["status"] == "done")},'
    E.append(("summary unchecked", a, a + '\n                   unchecked=db().execute("SELECT COUNT(*) AS n FROM rec_docs WHERE "\n'
                                          '                                          "origin=\'auto\' AND checked=0").fetchone()["n"],'))
    a = "' reports on file · medicines and vitals are live from GutLog';"
    E.append(("summary note", a, a + "\n  if(j.unchecked)box.appendChild(el('p','hint rs-auto',j.unchecked+' report'+(j.unchecked>1?'s were':' was')+"
                                     "' read automatically - a glance under Reports confirms '+(j.unchecked>1?'them.':'it.')));"))
    a = "    if(d.has_file){const a=el('a','rs-link','Open report');"
    E.append(("docs badge", a, "    if(d.origin==='auto'&&!d.checked){top.appendChild(el('span','rd-auto','machine-read'));\n"
                               "      const ok=el('button','btn tiny rd-ok','Looks right');ok.type='button';\n"
                               "      ok.onclick=async()=>{try{await post('/api/records/doc/'+d.id+'/checked',{});loadRecDocs();}catch(e){toast(e.message);} };\n"
                               "      r.appendChild(ok);}\n" + a))
    a = "        tr.appendChild(el('td','rs-meta',x.lab||''));"
    E.append(("trend auto", a, "        tr.appendChild(el('td','rs-meta',(x.lab||'')+(x.origin==='auto'?' · auto':'')));"))
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
    print("GutLog automatic reading -> v3.10.0")
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
        print("FATAL: this file is not at v3.9.0. Apply that first.")
        return 1
    if "def _spawn_reader" in src:
        print("FATAL: v3.7.0 pieces already present -- unexpected state. Nothing written.")
        return 1
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(args.file)), "records_worker.py")):
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
    bak = args.file + ".bak-v3100-" + stamp
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

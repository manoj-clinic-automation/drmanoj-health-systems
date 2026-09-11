#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.9.0 Phase F -- the scanner and the inbox. Scratch database,
uploads and token; nothing live is touched. Python 3.9.

  python3 test_phase_f.py [path/to/app.py]     -> must print 8/8 passed
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "app.py")
    adir = os.path.dirname(os.path.abspath(app_path))
    work = tempfile.mkdtemp()
    os.environ.update(GUTLOG_DB=os.path.join(work, "t.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real", GUTLOG_ICONS=adir,
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"))
    sys.path.insert(0, adir)
    spec = importlib.util.spec_from_file_location("gutlog_app_f", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.PROFILE_FILE = os.path.join(work, "prof.json")
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    ispec = importlib.util.spec_from_file_location("imp_f", os.path.join(adir, "import_records.py"))
    imp = importlib.util.module_from_spec(ispec)
    ispec.loader.exec_module(imp)
    up = os.environ["GUTLOG_UPLOADS"]
    inbox = os.path.join(up, "inbox")
    ctx = {}

    def t00_scan_page():
        anon = app.test_client()
        assert anon.get("/scan").status_code in (302, 401), "scan page open without login"
        assert anon.get("/scanner_widget.js").status_code in (302, 401), "widget open without login"
        h = c.get("/scan").get_data(as_text=True)
        today = mod.today()
        for s_ in ('id="scanroot"', "window.SCANNER_CONFIG", 'uploadUrl: "/api/upload"', 'value="%s"' % today,
                   "/scanner_widget.js?v=", "allowIdCard: false", 'href="/?open=records"'):
            assert s_ in h, s_ + " missing from the scan page"
        return "login-gated page with the widget config, today's date and a way back"

    def t01_widget():
        r = c.get("/scanner_widget.js")
        assert r.status_code == 200 and r.mimetype == "application/javascript", r.status_code
        assert b"SCANNER_CONFIG" in r.data and b"autoDetect" in r.data, "not the scanner widget"
        return "the shared scanner widget is served"

    def t02_upload_inbox():
        r = c.post("/api/upload", data={"file": (io.BytesIO(b"%PDF-1.4 scanned CBC"), "Lab_report_2026-09-11.pdf"),
                                        "ftype": "Lab report", "day": mod.today()},
                   content_type="multipart/form-data")
        assert r.get_json()["ok"]
        names = os.listdir(inbox)
        assert len(names) == 1 and names[0].startswith(mod.today() + "_Lab_report_2026-09-11_"), str(names)
        sha = mod._sha_file(os.path.join(inbox, names[0]))
        con = __import__("sqlite3").connect(os.environ["GUTLOG_DB"])
        assert con.execute("SELECT sha FROM files").fetchone()[0] == sha, "fingerprint not stored"
        con.close()
        ctx["sha"] = sha
        return "upload fingerprinted; readable copy in the inbox"

    def t03_waiting():
        j = c.get("/api/records/docs").get_json()
        assert len(j["uploads"]) == 1 and j["uploads"][0]["status"] == "inbox"
        return "listed under Reports as waiting to be processed"

    def t04_process_by_sha():
        src = os.path.join(work, "upd")
        os.makedirs(src)
        json.dump({"docs": [{"sha": ctx["sha"], "day": mod.today(), "kind": "Blood", "title": "CBC",
                             "source": "Lab X", "finding": "Synthetic finding"}]},
                  open(os.path.join(src, "records_manifest.local.json"), "w"))
        st = imp.run(src, db_path=os.environ["GUTLOG_DB"], uploads=up, profile_path=mod.PROFILE_FILE)
        assert st["docs_new"] == 1 and st["inbox_cleared"] == 1 and st["docs_missing"] == 0, str(st)
        assert os.listdir(inbox) == [], "processed scan left in the inbox"
        return "a manifest naming the scan by fingerprint files it; the inbox copy is cleared"

    def t05_processed_hidden():
        j = c.get("/api/records/docs").get_json()
        assert j["uploads"] == [] and j["docs"][0]["title"] == "CBC", str(j)
        r = c.get("/rec/doc/%d" % j["docs"][0]["id"])
        assert r.status_code == 200 and r.data == b"%PDF-1.4 scanned CBC"
        return "processed scan leaves the waiting list and opens as a record"

    def t06_page_buttons():
        h = c.get("/").get_data(as_text=True)
        assert h.count('href="/scan"') == 2, "scan buttons missing"
        assert "if(p==='records')" in h, "deep link missing"
        return "Scan buttons under Upload and Reports; /?open=records deep link"

    def t07_no_widget_file():
        keep = mod.SCANNER_JS
        mod.SCANNER_JS = os.path.join(work, "absent.js")
        try:
            assert c.get("/scanner_widget.js").status_code == 404
            assert c.get("/scan").status_code == 200
        finally:
            mod.SCANNER_JS = keep
        return "missing widget file gives 404, never a crash"

    tests = [("00 scan page", t00_scan_page), ("01 widget served", t01_widget), ("02 upload to inbox", t02_upload_inbox),
             ("03 waiting list", t03_waiting), ("04 process by fingerprint", t04_process_by_sha),
             ("05 processed hidden", t05_processed_hidden), ("06 buttons and deep link", t06_page_buttons),
             ("07 widget missing", t07_no_widget_file)]
    print("=" * 66)
    print("GutLog v3.9.0 Phase F - scanner test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

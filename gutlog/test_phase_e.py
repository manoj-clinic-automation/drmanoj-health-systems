#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.8.0 Phase E -- Records: import, reports, trends, summary, plan,
profile feed. Synthetic fixture on a scratch database, scratch uploads and a
scratch token; nothing live is touched. Python 3.9.

  python3 test_phase_e.py [path/to/app.py]     -> must print 13/13 passed
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta

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
    imp_path = os.path.join(os.path.dirname(os.path.abspath(app_path)), "import_records.py")
    work = tempfile.mkdtemp()
    os.environ["GUTLOG_DB"] = os.path.join(work, "t.db")
    os.environ["GUTLOG_UPLOADS"] = os.path.join(work, "up")
    os.environ["GUTLOG_INSECURE"] = "1"
    os.environ["GUTLOG_SECRET"] = "test-secret-not-real"
    os.environ["GUTLOG_ICONS"] = os.path.dirname(os.path.abspath(app_path))
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = os.path.join(work, "feed.token")
    os.environ.pop("GUTLOG_LINKS", None)
    sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
    spec = importlib.util.spec_from_file_location("gutlog_app_e", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.PROFILE_FILE = os.path.join(work, "records_profile.local.json")
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    tok = open(os.environ["GUTLOG_FEED_TOKEN_FILE"]).read().strip()
    H = {"Authorization": "Bearer " + tok}
    ispec = importlib.util.spec_from_file_location("import_records_e", imp_path)
    imp = importlib.util.module_from_spec(ispec)
    ispec.loader.exec_module(imp)

    T = date.today()
    D = [(T - timedelta(days=n)).isoformat() for n in (400, 200, 30, 2)]
    src = os.path.join(work, "upload", "records folder")
    os.makedirs(os.path.join(src, "01_Blood"))
    os.makedirs(os.path.join(src, "02_Scope"))
    open(os.path.join(src, "01_Blood", "a.pdf"), "wb").write(b"%PDF-1.4 synthetic report A")
    open(os.path.join(src, "01_Blood", "a_copy.pdf"), "wb").write(b"%PDF-1.4 synthetic report A")
    open(os.path.join(src, "02_Scope", "b.pdf"), "wb").write(b"%PDF-1.4 synthetic report B")
    open(os.path.join(src, "narr.pdf"), "wb").write(b"%PDF-1.4 synthetic narrative")
    labs = []
    for i, (d, v) in enumerate(zip(D, ["12", "30 **", "18", "11"])):
        labs.append({"day": d, "test": "Testarate", "section": "SYNTHETIC", "value": v, "unit": "u", "ref": "0-20",
                     "lab": "Lab X"})
    labs.append({"day": D[3], "test": "Otherium", "section": "SYNTHETIC", "value": ">1200", "unit": "", "ref": "",
                 "lab": "Lab Y"})
    labs.append({"day": D[3], "test": "Textish", "section": "SYNTHETIC", "value": "NEGATIVE", "unit": "", "ref": "",
                 "lab": "Lab Y"})
    man = {"docs": [
        {"path": "01_Blood/a.pdf", "day": D[0], "kind": "Blood", "title": "Panel A", "source": "Lab X",
         "finding": "Synthetic finding A"},
        {"path": "01_Blood/a_copy.pdf", "day": D[0], "kind": "Blood", "title": "Panel A (copy)", "source": "Lab X",
         "finding": "same content"},
        {"path": "02_Scope/b.pdf", "day": D[1], "kind": "Endoscopy", "title": "Scope B", "source": "Unit Z",
         "finding": "Synthetic finding B"},
        {"path": "narr.pdf", "day": D[2], "kind": "Summary", "title": "Narrative record", "source": "Self",
         "finding": ""},
        {"path": "missing/none.pdf", "day": D[2], "kind": "Blood", "title": "Missing", "source": "", "finding": ""}],
        "labs": labs,
        "plan": [{"pos": 1, "test": "Test one", "why": "because", "timing": "now"},
                 {"pos": 2, "test": "Test two", "why": "", "timing": "later"}],
        "profile": {"updated": D[3], "key_tests": ["Testarate", "Otherium"],
                    "problems": [{"name": "Synthetic problem", "since": "2020", "status": "active"}],
                    "resolved": [{"name": "Old thing", "status": "excluded"}],
                    "precautions": [{"flag": "AMBER", "title": "Synthetic precaution", "text": "why"}],
                    "missing": ["Report Q"],
                    "conditions": ["constipation", "thrombocytopenia", "Bad Code!", "x"]}}
    json.dump(man, open(os.path.join(src, "records_manifest.local.json"), "w"))
    ctx = {}

    def run_import():
        return imp.run(os.path.join(work, "upload"), db_path=os.environ["GUTLOG_DB"],
                       uploads=os.environ["GUTLOG_UPLOADS"], profile_path=mod.PROFILE_FILE)

    def t00_import():
        st = run_import()
        assert st["ok"] and st["docs_new"] == 3 and st["docs_updated"] == 1 and st["docs_missing"] == 1, str(st)
        assert st["labs"] == 6 and st["plan"] == 2 and st["profile"], str(st)
        assert oct(os.stat(mod.PROFILE_FILE).st_mode & 0o777) == "0o600", "profile mode"
        return "3 reports (duplicate content kept once), 1 missing reported, 6 values, 2 plan items, profile 600"

    def t01_idempotent():
        c.post("/api/records/plan/1", json={"status": "done", "done_day": D[3]})
        man["labs"][1]["value"] = "31 **"
        json.dump(man, open(os.path.join(src, "records_manifest.local.json"), "w"))
        st = run_import()
        assert st["docs_new"] == 0, "re-import duplicated reports"
        n = len(os.listdir(os.environ["GUTLOG_UPLOADS"]))
        assert n == 3, "files copied twice: %d" % n
        s = c.get("/api/records/labs?test=Testarate").get_json()["series"]
        assert [x["value"] for x in s] == ["12", "31 **", "18", "11"], "upsert: " + str([x["value"] for x in s])
        p = c.get("/api/records/plan").get_json()["items"]
        assert p[0]["status"] == "done", "re-import reset a done plan item"
        return "re-import: no duplicates, changed value updated, done status kept"

    def t02_cli_prints_counts_only():
        out = subprocess.run([sys.executable, imp_path, os.path.join(work, "upload")], capture_output=True, text=True,
                             env=dict(os.environ, GUTLOG_PROFILE=mod.PROFILE_FILE)).stdout
        assert "records import:" in out and "Synthetic" not in out and "Panel" not in out, out
        return "command line prints counts only"

    def t03_docs():
        c.post("/api/upload", data={"file": (io.BytesIO(b"%PDF-1.4 new"), "new.pdf"), "day": D[3],
                                    "ftype": "Lab report", "label": "New CBC"},
               content_type="multipart/form-data")
        j = c.get("/api/records/docs").get_json()
        assert [d["title"] for d in j["docs"]] == ["Narrative record", "Scope B", "Panel A (copy)"], \
            str([d["title"] for d in j["docs"]])
        assert j["uploads"] and j["uploads"][0]["status"] == "inbox" and j["uploads"][0]["title"] == "New CBC"
        ctx["doc"] = j["docs"][1]["id"]
        return "reports newest first; your upload listed as waiting to be processed"

    def t04_file_serving():
        r = c.get("/rec/doc/%d" % ctx["doc"])
        assert r.status_code == 200 and r.data == b"%PDF-1.4 synthetic report B", r.status_code
        assert c.get("/rec/doc/99999").status_code == 404
        assert app.test_client().get("/rec/doc/%d" % ctx["doc"]).status_code in (302, 401), "open without login"
        return "original opens; unknown 404; needs login"

    def t05_trends():
        t = c.get("/api/records/labs").get_json()["tests"]
        names = [x["test"] for x in t]
        assert names[:2] == ["Testarate", "Otherium"] and t[0]["key"], "key tests first: " + str(names)
        pts = t[0]["points"]
        assert [p[1] for p in pts] == [12.0, 31.0, 18.0, 11.0] and pts[1][2] == 1, str(pts)
        assert t[1]["points"][0][1] == 1200.0 and t[1]["last_value"] == ">1200", "'>1200' kept as printed"
        tx = [x for x in t if x["test"] == "Textish"][0]
        assert tx["points"] == [] and tx["last_value"] == "NEGATIVE"
        return "key tests first; numbers for charts, printed values and lab flags kept"

    def t06_summary():
        meds = c.get("/api/prnmeds/full").get_json()
        c.post("/api/schedule", json={"med_id": meds[0]["id"], "slot": "MORNING", "valid_from": D[2],
                                      "dose_text": "1 tab"})
        c.post("/api/salt", json={"med_id": meds[0]["id"], "molecule": "testamol", "strength": "5 mg"})
        c.post("/api/vitals", json={"day": D[3], "vtime": "08:00", "sys": 128, "dia": 82, "pulse": 74, "temp": 98.2})
        j = c.get("/api/records/summary").get_json()
        assert j["meds"] and j["meds"][0]["molecule"] == "testamol" and j["meds"][0]["strength"] == "5 mg", str(j["meds"])
        assert j["vitals"][0]["sys"] == 128 and j["vitals"][0]["temp"] == 98.2
        assert [x["test"] for x in j["labs"]] == ["Testarate", "Otherium"] and j["labs"][0]["value"] == "11"
        assert j["profile"]["problems"][0]["name"] == "Synthetic problem" and j["profile"]["missing"] == ["Report Q"]
        assert j["plan"] == {"total": 2, "done": 1} and j["docs"] == 3
        assert j["master"] and j["master"][0]["title"] == "Narrative record"
        assert "conditions" not in j["profile"]
        return "summary: live medicines (salt, strength), vitals, key results, problems, plan 1/2, narrative link"

    def t07_plan():
        assert c.post("/api/records/plan/2", json={"status": "nope"}).status_code == 400
        fut = (T + timedelta(days=2)).isoformat()
        assert c.post("/api/records/plan/2", json={"status": "done", "done_day": fut}).status_code == 400
        assert c.post("/api/records/plan/999", json={"status": "done"}).status_code == 404
        assert c.post("/api/records/plan/2", json={"status": "done"}).get_json()["ok"]
        assert c.post("/api/records/plan/2", json={"status": "planned"}).get_json()["ok"]
        it = c.get("/api/records/plan").get_json()["items"][1]
        assert it["status"] == "planned" and it["done_day"] == ""
        return "done / undo; bad status, future date and unknown item refused"

    def t08_profile_feed():
        assert c.get("/api/feed/profile").status_code == 401
        j = app.test_client().get("/api/feed/profile", headers=H).get_json()
        assert j["ok"] and j["conditions"] == ["constipation", "thrombocytopenia"], str(j)
        assert set(j) == {"ok", "app", "conditions"}, "feed carries more than codes"
        return "token-gated; condition codes only, junk codes dropped"

    def t09_page():
        h = c.get("/").get_data(as_text=True)
        for s_ in ('id="files-summary"', 'id="files-reports"', 'id="files-trends"', 'id="files-plan"',
                   "Records</button>", 'data-s="vault">Upload', "function loadRecSummary", "files:'summary'"):
            assert s_ in h, s_ + " missing"
        return "Records tab renders with Summary, Reports, Trends, Plan"

    def t10_no_profile():
        os.remove(mod.PROFILE_FILE)
        j = c.get("/api/records/summary").get_json()
        assert j["profile"]["problems"] is None and j["meds"], str(j["profile"])
        assert app.test_client().get("/api/feed/profile", headers=H).get_json()["conditions"] == []
        return "no profile file: summary still works, feed sends no codes"

    def t11_missing_manifest():
        st = imp.run(os.path.join(work, "empty_dir_nonexistent"), db_path=os.environ["GUTLOG_DB"],
                     uploads=os.environ["GUTLOG_UPLOADS"], profile_path=mod.PROFILE_FILE)
        assert not st["ok"] and "manifest" in st["err"]
        return "no manifest: refuses, writes nothing"

    def t12_login_required():
        a = app.test_client()
        for u in ("/api/records/docs", "/api/records/labs", "/api/records/summary", "/api/records/plan"):
            assert a.get(u).status_code in (302, 401), u + " open without login"
        return "every records page needs the diary login"

    tests = [("00 import", t00_import), ("01 re-import idempotent", t01_idempotent),
             ("02 counts only", t02_cli_prints_counts_only), ("03 reports list", t03_docs),
             ("04 file serving", t04_file_serving), ("05 trends", t05_trends), ("06 summary", t06_summary),
             ("07 plan", t07_plan), ("08 profile feed", t08_profile_feed), ("09 page", t09_page),
             ("10 no profile", t10_no_profile), ("11 no manifest", t11_missing_manifest),
             ("12 login required", t12_login_required)]
    print("=" * 66)
    print("GutLog v3.8.0 Phase E - records test")
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

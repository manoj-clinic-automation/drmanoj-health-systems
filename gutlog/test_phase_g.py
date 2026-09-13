#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.10.0 Phase G -- reports read automatically. The Sarvam reader is
replaced by a fake that returns what Sarvam's extract returns; nothing
leaves this machine. Scratch database, uploads, token. Python 3.9.

  python3 test_phase_g.py [path/to/app.py]     -> must print 12/12 passed
"""
import importlib.util
import io
import json
import os
import shutil
import sqlite3
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
    adir = os.path.dirname(os.path.abspath(app_path))
    work = tempfile.mkdtemp()
    os.environ.update(GUTLOG_DB=os.path.join(work, "t.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real", GUTLOG_ICONS=adir,
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"),
                      GUTLOG_PROFILE=os.path.join(work, "prof.json"), GUTLOG_SARVAM_ENV=os.path.join(work, "none.env"))
    os.environ.pop("SARVAM_API_KEY", None)
    os.environ.pop("GUTLOG_LINKS", None)
    sys.path.insert(0, adir)
    spec = importlib.util.spec_from_file_location("gutlog_app_g", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.PROFILE_FILE = os.environ["GUTLOG_PROFILE"]
    json.dump({"patient_match": ["manoj"]}, open(mod.PROFILE_FILE, "w"))
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    wspec = importlib.util.spec_from_file_location("records_worker_g", os.path.join(adir, "records_worker.py"))
    W = importlib.util.module_from_spec(wspec)
    wspec.loader.exec_module(W)
    dbp = os.environ["GUTLOG_DB"]
    today = date.today()
    printed = today.strftime("%d/%m/%Y")

    def q(sql, a=()):
        con = sqlite3.connect(dbp)
        try:
            r = con.execute(sql, a).fetchall()
            con.commit()
            return r
        finally:
            con.close()

    # the record already knows these tests (as the import would have left them)
    q("CREATE TABLE IF NOT EXISTS rec_labs (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, test TEXT, section TEXT, "
      "value TEXT, num REAL, unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, "
      "origin TEXT DEFAULT '', doc_id INTEGER, UNIQUE(day, test, lab))")
    for t_, sec in (("Haemoglobin", "CBC"), ("Platelet Count", "CBC"), ("ESR (Erythrocyte Sedimentation Rate)", "INFL"),
                    ("Serum Sodium", "ELEC")):
        q("INSERT INTO rec_labs(day,test,section,value,num,lab) VALUES(?,?,?,?,?,?)",
          ((today - timedelta(days=60)).isoformat(), t_, sec, "1", 1.0, "Lab Old"))
    q("CREATE TABLE IF NOT EXISTS rec_plan (id INTEGER PRIMARY KEY AUTOINCREMENT, pos INTEGER, test TEXT UNIQUE, "
      "why TEXT, timing TEXT, status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '')")
    q("INSERT INTO rec_plan(pos,test) VALUES(1,'Westergren ESR repeat'),(2,'FibroScan with CAP')")

    GOOD = {"patient_name": "Dr. Manoj Kumar Agarwal", "report_date": printed, "laboratory": "Test Lab",
            "document_type": "blood test", "title": "Complete blood count",
            "results": [{"test": "Hb", "value": "13.9", "unit": "g/dL", "reference_range": "13.0 - 18.0", "flag": ""},
                        {"test": "Platelet count", "value": "1.05", "unit": "lakh/cmm", "reference_range": "1.50 - 4.00",
                         "flag": "L"},
                        {"test": "ESR (Westergren)", "value": "12", "unit": "mm/1st hr", "reference_range": "0 - 20",
                         "flag": ""},
                        {"test": "Serum Sodium", "value": "131", "unit": "mEq/L", "reference_range": "135 - 147",
                         "flag": ""},
                        {"test": "Novelium", "value": "5", "unit": "", "reference_range": "", "flag": ""}],
            "impression": ""}
    calls = []

    def reader_for(data, note=""):
        def r(path, schema):
            calls.append(path)
            assert "results" in schema["properties"]
            return (json.loads(json.dumps(data)) if data else None), note
        return r

    def upload(name, body):
        r = c.post("/api/upload", data={"file": (io.BytesIO(body), name), "ftype": "Lab report",
                                        "day": today.isoformat()}, content_type="multipart/form-data")
        assert r.get_json()["ok"]

    def run(reader):
        con = sqlite3.connect(dbp)
        try:
            return W.run(con, reader=reader)
        finally:
            con.close()

    def t00_no_spawn_on_scratch():
        assert mod._spawn_reader() is False, "a scratch database started the reader"
        return "uploads on a scratch database never start the reader"

    def t01_read_and_file():
        upload("cbc.pdf", b"%PDF-1.4 synthetic CBC")
        st = run(reader_for(GOOD))
        assert st["filed"] == 1 and st["values"] == 5 and st["held"] == 0, str(st)
        d = q("SELECT day, kind, title, source, origin, checked, finding FROM rec_docs")[0]
        assert d[0] == today.isoformat() and d[1] == "Blood" and d[2] == "Complete blood count" and d[3] == "Test Lab", str(d)
        assert d[4] == "auto" and d[5] == 0, "not marked machine-read"
        assert "Platelet Count 1.05" in d[6] and "Serum Sodium 131" in d[6], "flag summary: " + d[6]
        return "report dated from its own date, filed as Blood, flagged values summarised"

    def t02_names_matched():
        rows = dict((r[0], r[1:]) for r in q("SELECT test, value, flag, origin, section FROM rec_labs WHERE day=?",
                                            (today.isoformat(),)))
        assert set(rows) == {"Haemoglobin", "Platelet Count", "ESR (Erythrocyte Sedimentation Rate)", "Serum Sodium",
                             "Novelium"}, str(sorted(rows))
        assert rows["Platelet Count"][1] == 1 and rows["Serum Sodium"][1] == 1 and rows["Haemoglobin"][1] == 0
        assert rows["Haemoglobin"][3] == "CBC" and rows["Novelium"][3] == "READ AUTOMATICALLY"
        assert all(v[2] == "auto" for v in rows.values())
        return "Hb -> Haemoglobin, ESR (Westergren) -> ESR, lab L flag and out-of-range both flagged; new test kept"

    def t03_plan_ticked():
        p = dict((r[0], r[1:]) for r in q("SELECT test, status, note FROM rec_plan"))
        assert p["Westergren ESR repeat"][0] == "done" and "automatically" in p["Westergren ESR repeat"][1]
        assert p["FibroScan with CAP"][0] == "planned"
        return "the ESR plan item ticked itself; FibroScan left open"

    def t04_inbox_cleared():
        assert os.listdir(os.path.join(os.environ["GUTLOG_UPLOADS"], "inbox")) == [], "inbox copy left"
        j = c.get("/api/records/docs").get_json()
        assert j["uploads"] == [] and j["docs"][0]["origin"] == "auto" and j["docs"][0]["checked"] == 0
        return "filed report leaves the waiting list and the inbox"

    def t05_looks_right():
        did = c.get("/api/records/docs").get_json()["docs"][0]["id"]
        s = c.get("/api/records/summary").get_json()
        assert s["unchecked"] == 1, str(s.get("unchecked"))
        assert c.post("/api/records/doc/%d/checked" % did).get_json()["ok"]
        assert c.get("/api/records/summary").get_json()["unchecked"] == 0
        assert c.post("/api/records/doc/99999/checked").status_code == 404
        ser = c.get("/api/records/labs?test=Haemoglobin").get_json()["series"]
        assert ser[-1]["origin"] == "auto"
        return "Summary counts machine-read reports; Looks right clears it; Trends marks auto values"

    def t06_idempotent():
        n = len(calls)
        st = run(reader_for(GOOD))
        assert len(calls) == n and st["read"] == 0, "a filed report was read again"
        return "a second run reads nothing"

    def t07_wrong_patient():
        upload("other.pdf", b"%PDF-1.4 someone else")
        bad = dict(GOOD, patient_name="Mr Somebody Else")
        st = run(reader_for(bad))
        assert st["held"] == 1 and st["values"] == 0, str(st)
        d = q("SELECT status, finding FROM rec_docs ORDER BY id DESC LIMIT 1")[0]
        assert d[0] == "check" and d[1].startswith("CHECK:"), str(d)
        return "a different patient's report is filed as a document only, flagged, values held back"

    def t08_failures():
        upload("blurry.pdf", b"%PDF-1.4 unreadable")
        run(reader_for(None, "reader error (Timeout)"))
        up = [u for u in c.get("/api/records/docs").get_json()["uploads"] if u["title"].startswith("blurry")][0]
        assert "trying again" in up["finding"], up["finding"]
        run(reader_for(None, "reader error (Timeout)"))
        run(reader_for(None, "reader error (Timeout)"))
        up = [u for u in c.get("/api/records/docs").get_json()["uploads"] if u["title"].startswith("blurry")][0]
        assert "Could not be read automatically" in up["finding"], up["finding"]
        n = len(calls)
        run(reader_for(GOOD))
        assert len(calls) == n, "gave up after three tries"
        return "failure -> retry with the reason shown; after 3 tries it stays a file"

    def t09_dates():
        pd = W.parse_day
        cases = {"11/09/2026": "2026-09-11", "11-Sep-2026": "2026-09-11", "2026-09-11": "2026-09-11",
                 "11.09.26": "2026-09-11", "Collected on: 3 Aug 2026 10:30": "2026-08-03", "": "",
                 "31/02/2026": "", "01/01/2099": ""}
        bad = [(k, pd(k), v) for k, v in cases.items() if pd(k) != v and not (v and pd(k) > today.isoformat())]
        assert not bad, str(bad)
        return "printed dates read day-first; impossible and future dates refused"

    def t10_key_from_env_file():
        ef = os.path.join(work, "wa.env")
        open(ef, "w").write("OTHER=1\nSARVAM_API_KEY=test-key-not-real\n")
        W.ENV_FILE = ef
        assert W.sarvam_key() == "test-key-not-real"
        W.ENV_FILE = os.path.join(work, "absent.env")
        assert W.sarvam_extract(os.path.join(work, "x.pdf"), W.LAB_SCHEMA) == (None, "no Sarvam key on this server")
        return "key read in place from the clinic's env file; no key -> a clear reason, no crash"

    def t11_coerce():
        cz = W.coerce
        d = {"patient_name": "x", "results": []}
        class R:  # noqa: E306
            result = json.dumps(d)
        assert cz(R()) == d and cz({"result": d}) == d and cz([json.dumps(d)]) == d and cz("not json") is None
        return "the reader's answer is found in any of the SDK's shapes"

    def t12_schema_descriptions():
        """The service rejects the WHOLE schema -- SCHEMA_INVALID, 400, before it
        ever looks at the file -- if any field, or the item object inside an
        array, has no description. A missing one on `results.items` is what made
        every uploaded PDF come back "reader error (BadRequestError)"."""
        bad = []

        def walk(node, path):
            if not isinstance(node, dict):
                return
            for name, f in (node.get("properties") or {}).items():
                if not str(f.get("description") or "").strip():
                    bad.append(path + "/" + name)
                walk(f, path + "/" + name)
            it = node.get("items")
            if isinstance(it, dict):
                if not str(it.get("description") or "").strip():
                    bad.append(path + "/items")
                walk(it, path + "/items")

        walk(W.LAB_SCHEMA, "")
        assert not bad, "no description on: " + ", ".join(bad)
        return "every field and every array item carries a description"

    def t13_error_keeps_the_message():
        """A bare exception class name in ocr_note cost a round trip to the
        server to learn that the schema, not the file, was wrong."""
        class Boom(Exception):
            body = {"detail": "SCHEMA_INVALID: field \"results\": needs a description"}

        def raiser(*a, **k):
            raise Boom()

        real = W.sarvam_key
        W.sarvam_key = lambda: "x"
        try:
            import sarvamai
            realc = sarvamai.SarvamAI
            sarvamai.SarvamAI = lambda **k: type("C", (), {"doc_ai": type("D", (), {"extract": raiser})()})()
            try:
                out, note = W.sarvam_extract(__file__, W.LAB_SCHEMA)
            finally:
                sarvamai.SarvamAI = realc
        except ImportError:
            return "reader library absent, skipped"
        finally:
            W.sarvam_key = real
        assert out is None, "should not have produced a reading"
        assert "SCHEMA_INVALID" in note, "note lost the service's message: " + note
        return "the note carries what the service actually said"

    tests = [("00 scratch never spawns", t00_no_spawn_on_scratch), ("01 read and file", t01_read_and_file),
             ("02 test names matched", t02_names_matched), ("03 plan ticked", t03_plan_ticked),
             ("04 inbox cleared", t04_inbox_cleared), ("05 looks right", t05_looks_right),
             ("06 idempotent", t06_idempotent), ("07 wrong patient", t07_wrong_patient),
             ("08 failures and retries", t08_failures), ("09 dates", t09_dates),
             ("10 key from env file", t10_key_from_env_file), ("11 answer shapes", t11_coerce),
             ("12 schema descriptions", t12_schema_descriptions),
             ("13 error keeps the message", t13_error_keeps_the_message)]
    print("=" * 66)
    print("GutLog v3.10.0 Phase G - automatic reading test")
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

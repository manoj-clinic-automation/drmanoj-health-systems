#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.28.0 -- the Plans page (GUTLOG_V3280_PLANS).

These checks render what he sees and exercise what he can do to it. No real
title is used anywhere: the live plan titles name medicines and this file is
TRACKED in a public repo (CLAUDE.md 5d). "Plan A" throughout.

  python3 test_v3280_plans.py [path/to/gutlog/app.py]
"""
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date

RESULTS = []

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
NOT_PDF = b"MZ\x90\x00this is not a pdf at all, whatever it is called\n"


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp(prefix="gutlog_plans_")
    plans_dir = os.path.join(w, "plans_files")
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=plans_dir, GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3280", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)

    anon = gm.app.test_client()
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)

    def add(title, when, status="Draft", blob=PDF, fname="plan.pdf"):
        data = {"title": title, "first_considered": when, "status": status}
        if blob is not None:
            data["file"] = (io.BytesIO(blob), fname)
        return c.post("/api/plans", data=data, content_type="multipart/form-data")

    # ---------------------------------------------------------------- 01
    def t01():
        r = add("Plan A", "2026-09-22", "Draft")
        assert r.status_code == 200, "upload returned %s" % r.status_code
        assert r.get_json().get("ok"), r.get_json()
        h = c.get("/plans").get_data(as_text=True)
        assert "Plan A" in h, "the title is not on the page"
        assert "First considered: 22-Sep-2026" in h or "22-Sep-2026" in h, \
            "the DD-Mon-YYYY date is not on the page"
        return "page carries the title"
    check("01 a plan is added and the page shows it", t01)

    # The list is drawn by JS from /api/plans, so assert on what it is handed
    # as well as on the page: a title in the HTML shell would prove nothing.
    def t02():
        rows = json.loads(c.get("/api/plans").get_data(as_text=True))
        assert len(rows) == 1, "expected 1 plan, got %d" % len(rows)
        p = rows[0]
        assert p["title"] == "Plan A", p["title"]
        assert p["first_considered_text"] == "22-Sep-2026", p["first_considered_text"]
        assert p["status"] == "Draft", p["status"]
        assert p["has_file"] is True, "the plan has no file"
        h = c.get("/plans").get_data(as_text=True)
        for word in (">Open<", ">Share<"):
            assert word in h, "the %s button is not on the page" % word
        return "date reads 22-Sep-2026; Open and Share both present"
    check("02 the row carries the date text and both buttons", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        r = add("Plan B", "2026-09-21", blob=NOT_PDF, fname="sneaky.pdf")
        assert r.status_code == 400, "a non-PDF named .pdf was accepted (%s)" % r.status_code
        assert "not a PDF" in (r.get_json().get("err") or ""), r.get_json()
        rows = json.loads(c.get("/api/plans").get_data(as_text=True))
        assert len(rows) == 1, "the refused upload left a plan row behind (%d rows)" % len(rows)
        names = os.listdir(plans_dir) if os.path.isdir(plans_dir) else []
        assert len(names) == 1, "the refused upload left a file behind: %r" % names
        return "refused on the magic bytes, and left no row and no file"
    check("03 a non-PDF is refused however it is named", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        pid = json.loads(c.get("/api/plans").get_data(as_text=True))[0]["id"]
        r = anon.get("/plan/%d/file" % pid)
        assert r.status_code in (301, 302), \
            "the file answered %s to a logged-out request" % r.status_code
        assert "login" in (r.headers.get("Location") or ""), r.headers.get("Location")
        assert anon.get("/plans").status_code in (301, 302), "the page is not gated"
        assert anon.get("/api/plans").status_code in (301, 302), "the list is not gated"
        ok = c.get("/plan/%d/file" % pid)
        assert ok.status_code == 200, "signed in, the file returned %s" % ok.status_code
        assert ok.data.startswith(b"%PDF-"), "what came back is not the PDF"
        return "logged out is bounced to login; signed in gets the PDF"
    check("04 the file is refused when logged out", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        pid = json.loads(c.get("/api/plans").get_data(as_text=True))[0]["id"]
        r = c.get("/plan/%d/file" % pid)
        assert r.headers.get("Content-Type", "").startswith("application/pdf"), \
            r.headers.get("Content-Type")
        assert r.headers.get("Cache-Control") == "no-store", r.headers.get("Cache-Control")
        disp = r.headers.get("Content-Disposition") or ""
        assert disp.startswith("inline;"), disp
        dl = c.get("/plan/%d/file?dl=1" % pid)
        assert (dl.headers.get("Content-Disposition") or "").startswith("attachment;"), \
            dl.headers.get("Content-Disposition")
        return "inline by default, attachment on ?dl=1, no-store on both"
    check("05 the file is served inline and never cached", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        pid = json.loads(c.get("/api/plans").get_data(as_text=True))[0]["id"]
        r = c.post("/api/plans/%d" % pid, json={"status": "Active"})
        assert r.get_json().get("ok"), r.get_json()
        rows = json.loads(c.get("/api/plans").get_data(as_text=True))
        assert rows[0]["status"] == "Active", rows[0]["status"]
        bad = c.post("/api/plans/%d" % pid, json={"status": "Whatever"})
        assert bad.status_code == 400, "an unknown status was accepted"
        rows = json.loads(c.get("/api/plans").get_data(as_text=True))
        assert rows[0]["status"] == "Active", "the refused status still changed the row"
        return "Draft -> Active; an unknown status is refused and changes nothing"
    check("06 status can be changed, to a known status only", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        pid = json.loads(c.get("/api/plans").get_data(as_text=True))[0]["id"]
        stored = [r[0] for r in sqlite3.connect(os.environ["GUTLOG_DB"]).execute(
            "SELECT stored_name FROM plan_files WHERE plan_id=?", (pid,))]
        r = c.post("/api/plans/%d" % pid, json={"archived": 1})
        assert r.get_json().get("ok"), r.get_json()
        rows = json.loads(c.get("/api/plans").get_data(as_text=True))
        assert len(rows) == 0, "an archived plan is still in the list"
        shown = json.loads(c.get("/api/plans?archived=1").get_data(as_text=True))
        assert len(shown) == 1, "the archived plan is gone entirely"
        for s in stored:
            assert os.path.exists(os.path.join(plans_dir, s)), \
                "archiving deleted the file %s" % s
        assert c.get("/plan/%d/file" % pid).status_code == 200, \
            "the file stopped being readable after archiving"
        return "hidden from the list, kept on disk and still openable"
    check("07 archive hides the plan and keeps the file", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        con = sqlite3.connect(os.environ["GUTLOG_DB"])
        cols = dict((r[1], r[2]) for r in con.execute("PRAGMA table_info(plans)"))
        for need in ("started_on", "notes", "archived", "first_considered", "created_at"):
            assert need in cols, "plans has no %s column" % need
        fcols = [r[1] for r in con.execute("PRAGMA table_info(plan_files)")]
        for need in ("plan_id", "stored_name", "original_name", "bytes", "sha256"):
            assert need in fcols, "plan_files has no %s column" % need
        row = con.execute("SELECT stored_name, sha256, original_name FROM plan_files "
                          "ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        assert row[0] == row[1] + ".pdf", \
            "the stored name is not the sha256: %r vs %r" % (row[0], row[1])
        assert row[2] == "plan.pdf", "the uploaded name was not kept: %r" % row[2]
        return "tables carry the room to grow; stored name is the sha256"
    check("08 the stored name is the hash, the uploaded name is kept", t08)

    print("")
    for ok, name, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))
    bad = [r for r in RESULTS if not r[0]]
    print("-" * 72)
    print("%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
    print("RESULT: " + ("FAILURES" if bad else "ALL PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

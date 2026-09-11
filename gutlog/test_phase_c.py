#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.6.0 Phase C -- server-side functional test.

Stock (count, bought, pillbox fill, per-dose use, alerts), the read-only
feed and its token, and the vitals log. Builds its own scratch database and
its own token file; never touches the live ones. Python 3.9.

  python3 test_phase_c.py [path/to/app.py]     -> must print 20/20 passed
"""
import importlib.util
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
    work = tempfile.mkdtemp()
    os.environ["GUTLOG_DB"] = os.path.join(work, "t.db")
    os.environ["GUTLOG_UPLOADS"] = os.path.join(work, "up")
    os.environ["GUTLOG_INSECURE"] = "1"
    os.environ["GUTLOG_SECRET"] = "test-secret-not-real"
    os.environ["GUTLOG_ICONS"] = os.path.dirname(os.path.abspath(app_path))
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = os.path.join(work, "feed.token")
    sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
    spec = importlib.util.spec_from_file_location("gutlog_app_c", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)
    dbp = os.environ["GUTLOG_DB"]

    def q(sql, a=()):
        con = sqlite3.connect(dbp)
        try:
            r = con.execute(sql, a).fetchall()
            con.commit()
            return r
        finally:
            con.close()

    T = date.today()
    TODAY, Y1, Y3 = [(T - timedelta(days=n)).isoformat() for n in (0, 1, 3)]
    ctx = {}

    def row(mid):
        for r in c.get("/api/stock").get_json()["rows"]:
            if r["med_id"] == mid:
                return r
        raise AssertionError("med %s missing from /api/stock" % mid)

    def count_yesterday(mid, qty):
        r = c.post("/api/stock/count", json={"med_id": mid, "qty": qty})
        assert r.status_code == 200, "count refused: " + r.get_data(as_text=True)
        q("UPDATE stock_events SET at=? WHERE id=(SELECT MAX(id) FROM stock_events)",
          (Y1 + " 00:00",))

    def now_row(mid):
        for s in c.get("/api/now").get_json()["slots"]:
            for r in s["rows"]:
                if r["med_id"] == mid:
                    return r

    def t00_units():
        u = mod._units
        cases = [("1 tab", 1), ("2 caps", 2), ("1/2", 0.5), ("½ tab", 0.5), ("", 1),
                 ("40 mg", 1), ("40mg", 1), ("0", 1), ("abc", 1), ("1.5 tab", 1.5)]
        bad = [(s, u(s), w) for s, w in cases if abs(u(s) - w) > 1e-9]
        assert not bad, "unit parse wrong: " + str(bad)
        return str(len(cases)) + " dose texts parse to the right tablet count"

    def t01_setup():
        meds = c.get("/api/prnmeds/full").get_json()
        assert len(meds) >= 4, "need 4 seeded meds"
        ctx["A"], ctx["B"], ctx["V"], ctx["X"] = [m["id"] for m in meds[:4]]
        for mid, body in ((ctx["A"], {"dose_text": "1 tab"}),
                          (ctx["B"], {"dose_text": "2 tab", "slot": "NIGHT"}),
                          (ctx["V"], {"dose_text": "", "variants": "72|145|290"})):
            b = {"med_id": mid, "slot": "MORNING", "valid_from": Y3}
            b.update(body)
            assert c.post("/api/schedule", json=b).get_json().get("ok"), "schedule add failed"
        rA, rX, rV = row(ctx["A"]), row(ctx["X"]), row(ctx["V"])
        assert rA["mode"] == "pillbox" and rA["per_day"] == 1 and not rA["tracked"], str(rA)
        assert rX["mode"] == "per_dose" and not rX["can_pillbox"], str(rX)
        assert not rV["trackable"] and "strengths vary" in rV["why"], str(rV)
        return "scheduled -> pillbox, extra -> per dose, variant -> not tracked"

    def t02_guards():
        bad = [("/api/stock/count", {"med_id": ctx["A"], "qty": -1}),
               ("/api/stock/count", {"med_id": 999999, "qty": 5}),
               ("/api/stock/count", {"med_id": ctx["A"], "qty": "x"}),
               ("/api/stock/add", {"med_id": ctx["A"], "qty": 10}),
               ("/api/stock/mode", {"med_id": ctx["A"], "mode": "weekly"}),
               ("/api/stock/fill", {"days": 7}),
               ("/api/stock/fill", {"days": 40})]
        for u, b in bad:
            r = c.post(u, json=b)
            assert r.status_code == 400, "accepted: " + u + " " + str(b)
        assert q("SELECT COUNT(*) FROM stock_events")[0][0] == 0, "a refused call wrote a row"
        return str(len(bad)) + " bad calls refused (incl. Bought before Count, fill with nothing counted)"

    def t03_count_and_extras():
        count_yesterday(ctx["X"], 10)
        assert row(ctx["X"])["current"] == 10, "count not read back"
        for _ in range(3):
            c.post("/api/now/dose", json={"med_id": ctx["X"], "status": "EXTRA",
                                          "day": TODAY, "dtime": "00:00"})
        r = row(ctx["X"])
        assert r["current"] == 7, "extras did not deduct: " + str(r["current"])
        assert abs(r["per_day"] - 3 / 14.0) < 0.01, "14-day average wrong: " + str(r["per_day"])
        return "count 10, three extras -> 7 left, 0.21/day"

    def t04_undo_restores():
        did = q("SELECT MAX(id) FROM doses")[0][0]
        c.post("/api/now/undo/" + str(did), json={})
        assert row(ctx["X"])["current"] == 8, "undo did not put the tablet back"
        return "undoing a dose puts it back in stock, no bookkeeping"

    def t05_bought():
        c.post("/api/stock/add", json={"med_id": ctx["X"], "qty": 15})
        assert row(ctx["X"])["current"] == 23, "purchase not added"
        return "Bought 15 -> 23"

    def t06_before_count_not_deducted():
        count_yesterday(ctx["A"], 30)
        q("UPDATE stock_events SET at=? WHERE id=(SELECT MAX(id) FROM stock_events)",
          (TODAY + " 00:00",))
        c.post("/api/now/dose", json={"med_id": ctx["A"], "status": "EXTRA", "day": Y1,
                                      "dtime": "09:00"})
        assert row(ctx["A"])["current"] == 30, "a dose before the count was deducted"
        q("UPDATE stock_events SET at=? WHERE med_id=? AND kind='COUNT'", (Y1 + " 00:00", ctx["A"]))
        assert row(ctx["A"])["current"] == 29, "extra of a pillbox med after count not deducted"
        return "only doses after the count deduct; an extra of a pillbox medicine does"

    def t07_pillbox_taken_not_deducted():
        c.post("/api/now/dose", json={"med_id": ctx["A"], "sched_id": now_row(ctx["A"])["sched_id"],
                                      "status": "TAKEN", "day": TODAY, "dtime": "00:00",
                                      "dose_text": "1 tab"})
        assert row(ctx["A"])["current"] == 29, "pillbox dose deducted twice"
        return "a dose taken from the pillbox does not deduct again"

    def t08_fill():
        count_yesterday(ctx["B"], 40)
        pv = c.get("/api/stock").get_json()["fill_preview"]
        per = dict((p["med_id"], p["per_day"]) for p in pv)
        assert per.get(ctx["A"]) == 1 and per.get(ctx["B"]) == 2, "fill preview wrong: " + str(pv)
        assert ctx["X"] not in per, "per-dose med in pillbox preview"
        r = c.post("/api/stock/fill", json={"days": 7}).get_json()
        assert r.get("ok") and r["n"] == 2, "fill failed: " + str(r)
        assert row(ctx["A"])["current"] == 22 and row(ctx["B"])["current"] == 26, \
            "fill deduction wrong: A %s B %s" % (row(ctx["A"])["current"], row(ctx["B"])["current"])
        return "7-day fill takes 7 x A and 14 x B (2 tab)"

    def t09_undo_fill():
        c.post("/api/stock/fill", json={"days": 3})
        assert row(ctx["A"])["current"] == 19, "second fill not applied"
        c.post("/api/stock/fill/undo", json={})
        assert row(ctx["A"])["current"] == 22 and row(ctx["B"])["current"] == 26, "undo fill wrong"
        return "undo removes exactly the last fill, both medicines"

    def t10_pillbox_alerts():
        rB = row(ctx["B"])
        assert rB["level"] == "AMBER" and "one more fill" in rB["why"], "B (26, needs 14/fill): " + str(rB)
        c.post("/api/stock/count", json={"med_id": ctx["A"], "qty": 5})
        rA = row(ctx["A"])
        assert rA["level"] == "RED" and "7-day fill" in rA["why"], "A (5, needs 7): " + str(rA)
        c.post("/api/stock/count", json={"med_id": ctx["A"], "qty": 30})
        assert row(ctx["A"])["level"] == "", "A at 30 should be quiet"
        return "pillbox: red below one fill, amber below two, quiet above"

    def t11_per_dose_alerts():
        c.post("/api/stock/count", json={"med_id": ctx["X"], "qty": 0})
        rX = row(ctx["X"])
        assert rX["level"] == "RED" and rX["why"] == "none left", "X used and empty: " + str(rX)
        q("DELETE FROM doses WHERE med_id=?", (ctx["X"],))
        assert row(ctx["X"])["level"] == "AMBER", "unused and empty should be amber"
        for d in range(14):
            day = (T - timedelta(days=d)).isoformat()
            q("INSERT INTO doses(day,dtime,medicine,med_id,status,created) VALUES(?,?,?,?,?,?)",
              (day, "00:00", "x", ctx["X"], "EXTRA", "t"))
        c.post("/api/stock/count", json={"med_id": ctx["X"], "qty": 4})
        r2 = row(ctx["X"])
        assert r2["level"] == "AMBER" and r2["days_left"] == 4, "4 left at 1/day: " + str(r2)
        c.post("/api/stock/count", json={"med_id": ctx["X"], "qty": 2})
        assert row(ctx["X"])["level"] == "RED", "2 left at 1/day should be red"
        return "per dose: red under 3 days, amber under 7, amber when empty and unused"

    def t12_mode_switch():
        assert row(ctx["A"])["per_day"] == 1, "pillbox A uses the regimen rate"
        c.post("/api/stock/mode", json={"med_id": ctx["A"], "mode": "per_dose"})
        rA = row(ctx["A"])
        assert rA["mode"] == "per_dose", "mode not saved"
        assert abs(rA["per_day"] - 2 / 14.0) < 0.01, "per-dose A should count its scheduled dose too: " + str(rA["per_day"])
        c.post("/api/stock/mode", json={"med_id": ctx["A"], "mode": "pillbox"})
        assert row(ctx["A"])["mode"] == "pillbox", "mode not restored"
        return "switching a medicine to per dose counts its scheduled doses as use"

    def t13_page_and_review():
        html = c.get("/").get_data(as_text=True)
        for needle in ('id="meds-stock"', 'id="vitalsLog"', 'id="nowStock"',
                       "function loadStock(", "function renderVitals("):
            assert needle in html, "page missing " + needle
        c.post("/api/vitals", json={"day": Y1, "vtime": "07:10", "sys": 131, "dia": 84, "pulse": 68})
        v = c.get("/api/review?days=30").get_json()["vitals"]
        assert v and v[0]["sys"] == 131, "vitals not in review feed"
        return "page renders stock, banner and vitals log; review carries vitals"

    def t14_feed_token_file():
        p = os.environ["GUTLOG_FEED_TOKEN_FILE"]
        assert os.path.exists(p), "token file not created"
        assert oct(os.stat(p).st_mode & 0o777) == "0o600", "token file mode " + oct(os.stat(p).st_mode & 0o777)
        ctx["tok"] = open(p).read().strip()
        assert len(ctx["tok"]) >= 32, "token too short"
        return "feed.token created, mode 600"

    def t15_feed_auth():
        for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": ctx["tok"]}):
            for u in ("/api/feed/stack", "/api/feed/doses"):
                r = c.get(u, headers=h)
                assert r.status_code == 401, "feed open with " + str(list(h.values()))
        c2 = app.test_client()
        r = c2.get("/api/feed/stack", headers={"Authorization": "Bearer " + ctx["tok"]})
        assert r.status_code == 200, "token refused without a session"
        return "no token, wrong token, missing Bearer -> 401; token alone (no login) -> 200"

    def t16_feed_stack():
        H = {"Authorization": "Bearer " + ctx["tok"]}
        j = app.test_client().get("/api/feed/stack?days=14", headers=H).get_json()
        reg = dict((r["med_id"], r) for r in j["regimen"])
        assert set(reg) == set([ctx["A"], ctx["B"], ctx["V"]]), "regimen wrong: " + str(list(reg))
        assert reg[ctx["V"]]["variants"] == "72|145|290", "variants lost"
        tk = dict((t["med_id"], t) for t in j["taken"])
        assert tk[ctx["X"]]["days"] == 14 and not tk[ctx["X"]]["scheduled"], "X aggregate: " + str(tk.get(ctx["X"]))
        assert tk[ctx["A"]]["scheduled"], "A scheduled flag"
        return "regimen (3 lines, variants kept) and taken aggregates"

    def t17_feed_doses_skips_and_legacy():
        H = {"Authorization": "Bearer " + ctx["tok"]}
        c.post("/api/now/dose", json={"med_id": ctx["B"], "sched_id": None, "status": "SKIPPED",
                                      "day": TODAY, "dtime": "00:00"})
        q("UPDATE prnmeds SET molecule='testmolecule' WHERE id=?", (ctx["X"],))
        nm = q("SELECT name FROM prnmeds WHERE id=?", (ctx["X"],))[0][0]
        q("INSERT INTO doses(day,dtime,medicine,created) VALUES(?,?,?,?)", (Y1, "10:00", nm, "t"))
        ev = app.test_client().get("/api/feed/doses?since=" + Y3, headers=H).get_json()["events"]
        assert not any(e.get("status") == "SKIPPED" for e in ev), "skips leaked"
        legacy = [e for e in ev if e["day"] == Y1 and e["time"] == "10:00"]
        assert legacy and legacy[0]["molecule"] == "testmolecule" and legacy[0]["med_id"] == ctx["X"], \
            "legacy name-only dose not mapped: " + str(legacy)
        assert all(e["day"] >= Y3 for e in ev), "since ignored"
        return "skips excluded; old name-only PRN rows mapped to their molecule"

    def t18_feed_since_clamped():
        H = {"Authorization": "Bearer " + ctx["tok"]}
        j = app.test_client().get("/api/feed/doses?since=2020-01-01", headers=H).get_json()
        floor = (T - timedelta(days=180)).isoformat()
        assert j["since"] >= floor, "since not clamped: " + j["since"]
        j = app.test_client().get("/api/feed/doses?since=" + (T + timedelta(days=3)).isoformat(),
                                  headers=H).get_json()
        assert j["since"] <= TODAY, "future since accepted"
        return "since clamped to 180 days; a future date falls back"

    def t19_feed_read_only():
        H = {"Authorization": "Bearer " + ctx["tok"]}
        n0 = q("SELECT COUNT(*) FROM doses")[0][0]
        r = app.test_client().post("/api/now/dose", headers=H,
                                   json={"med_id": ctx["X"], "status": "EXTRA"})
        assert r.status_code in (302, 401), "feed token could write: " + str(r.status_code)
        assert q("SELECT COUNT(*) FROM doses")[0][0] == n0, "a row was written"
        return "the feed token cannot write anything"

    tests = [
        ("00 dose-text unit parsing", t00_units),
        ("01 default modes", t01_setup),
        ("02 stock guards", t02_guards),
        ("03 count + per-dose use", t03_count_and_extras),
        ("04 undo restores stock", t04_undo_restores),
        ("05 bought adds", t05_bought),
        ("06 only after-count doses deduct", t06_before_count_not_deducted),
        ("07 pillbox dose not double-counted", t07_pillbox_taken_not_deducted),
        ("08 pillbox fill", t08_fill),
        ("09 undo last fill", t09_undo_fill),
        ("10 pillbox alerts", t10_pillbox_alerts),
        ("11 per-dose alerts", t11_per_dose_alerts),
        ("12 mode switch", t12_mode_switch),
        ("13 page + review carry new cards", t13_page_and_review),
        ("14 feed token file", t14_feed_token_file),
        ("15 feed auth", t15_feed_auth),
        ("16 feed stack", t16_feed_stack),
        ("17 feed doses: skips, legacy rows", t17_feed_doses_skips_and_legacy),
        ("18 feed since clamped", t18_feed_since_clamped),
        ("19 feed is read-only", t19_feed_read_only),
    ]
    print("=" * 66)
    print("GutLog v3.6.0 Phase C - functional test")
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

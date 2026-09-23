#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.17.0 / FitLog v1.6.0 Phase M -- Down days.

Properties, not counts. Every case ENTERS a v3.17.0 code path and fails on
v3.16.0 (CLAUDE.md rule 2), and every assertion here is declared in
new_assertions_v3170.json so tools/NEGATIVE_CONTROL.py can see it fail.

Two halves. The first runs GutLog alone on a scratch database with outward
links OFF, and covers the data model, the runs, the temperature write-
through, the analgesic chip, the feed, the watch lane, the day view and the
view payload. The second runs a REAL GutLog and a REAL FitLog on two loopback
ports, with the wearable tables built by FitLog's own migration, and proves
the two connections: FitLog's trend leaves a marked day out of its summary,
and the medicine chip reaches FitLog's analgesic log exactly once.

Times are derived from the clock, never written down, so this suite is the
same colour at 00:02, 05:05 and 23:58 (tools/RUN_AT_TIME.py). The run
grouping is date arithmetic on stored day strings and never reads a clock
at all.

  python3 test_phase_m.py [path/to/gutlog/app.py]   (FitLog at ../fitlog)
"""
import ast
import importlib.util
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def js_balanced(block):
    depth = {"(": 0, "[": 0, "{": 0}
    close = {")": "(", "]": "[", "}": "{"}
    i, n, quote = 0, len(block), None
    while i < n:
        c = block[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
        elif c == "/" and i + 1 < n and block[i + 1] == "/":
            while i < n and block[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and block[i + 1] == "*":
            j = block.find("*/", i + 2)
            i = (j + 2) if j >= 0 else n
            continue
        elif c in depth:
            depth[c] += 1
        elif c in close:
            depth[close[c]] -= 1
            if depth[close[c]] < 0:
                return False
        i += 1
    return all(v == 0 for v in depth.values())


ADVICE = re.compile(r"\b(should|must|recommend|advis|try to|you need|better to)\b", re.I)
THIRD = re.compile(r"\b(his|him|he)\b", re.I)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gut_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    fit_dir = os.environ.get("GUTLOG_TEST_FITLOG") or os.path.join(os.path.dirname(here), "fitlog")
    fit_path = os.path.join(fit_dir, "app.py")
    src = open(gut_path, encoding="utf-8", newline="").read()

    T = date.today()
    D = dict((n, (T - timedelta(days=n)).isoformat()) for n in range(0, 40))
    TODAY = D[0]

    # ================================================================ half 1
    work = tempfile.mkdtemp()
    os.environ.update(
        GUTLOG_DB=os.path.join(work, "g.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
        GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
        GUTLOG_ICONS=os.path.dirname(gut_path),
        GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"), GUTLOG_LINKS="0")
    sys.path.insert(0, os.path.dirname(gut_path))
    mod = load("gutlog_m", gut_path)
    # Synthetic medicine chip, as test_phase_i does: no real name anywhere.
    mod.PAIN_ANALGESICS = {"Testamol 500": ("testamol", "Testamol 500")}
    mod.PAIN_TREATMENTS = list(mod.PAIN_TREATMENTS_BASE) + list(mod.PAIN_ANALGESICS)
    if hasattr(mod, "DOWN_COPED_BASE"):
        mod.DOWN_COPED = list(mod.DOWN_COPED_BASE) + list(mod.PAIN_ANALGESICS)
    cl = mod.app.test_client()
    cl.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    gdb = os.environ["GUTLOG_DB"]

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def get(u):
        r = cl.get(u)
        return r.status_code, (r.get_json(silent=True) or {})

    def post(u, body):
        r = cl.post(u, data=json.dumps(body), content_type="application/json")
        return r.status_code, (r.get_json(silent=True) or {})

    cl.get("/login")   # one request: _migrate runs per request, not at boot
    q("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,9,?,1)", ("Testamol 500", "testamol"))

    def t01():
        cols = [r[1] for r in q("PRAGMA table_info(down_days)")]
        assert cols, "down_days table absent after a request"
        assert set(cols) == set(["id", "day", "components", "coped", "note", "created"]), cols
        assert "temp" not in cols, "temperature must never live in down_days"
        sv = q("SELECT value FROM settings WHERE key='schema_version'")[0][0]
        # v3.31.0 bumped it to 3.3.5 (library weights). What this checks is
        # that the down_days migration RAN, i.e. the version reached 3.3.4;
        # pinning the exact string made every later schema bump fail it.
        assert tuple(int(x) for x in sv.split(".")) >= (3, 3, 4), "schema_version " + sv
        idx = q("SELECT sql FROM sqlite_master WHERE tbl_name='down_days' AND sql LIKE '%UNIQUE%'")
        assert idx or "UNIQUE" in (q("SELECT sql FROM sqlite_master WHERE name='down_days'")[0][0] or ""), \
            "day is not UNIQUE"
        return "down_days(" + ",".join(cols) + "), day UNIQUE, schema 3.3.4, no temp column"
    check("01 table exists after one request, day UNIQUE, no temperature column", t01)

    def t02():
        s, j = post("/api/downday", {"day": TODAY})
        assert s == 200 and j.get("ok"), j
        assert j["marked"] is True and j["run"]["n"] == 1 and j["run"]["length"] == 1, j.get("run")
        assert q("SELECT COUNT(*) FROM down_days")[0][0] == 1
        s, g = get("/api/downday?day=" + TODAY)
        assert g["marked"] and g["components"] == [] and g["coped"] == [], g
        assert "components_all" in g and "coped_all" in g and len(g["components_all"]) == 8
        return "one tap, one row, day 1 of 1; eight components offered"
    check("02 one tap marks today and nothing else is asked", t02)

    def t03():
        post("/api/downday", {"day": TODAY, "components": ["Fatigue", "Heavy head / headache"]})
        s, j = post("/api/downday", {"day": TODAY, "components": ["Fatigue"]})
        assert q("SELECT COUNT(*) FROM down_days WHERE day=?", (TODAY,))[0][0] == 1, "duplicated"
        assert j["components"] == ["Fatigue"], j["components"]
        row = q("SELECT components FROM down_days WHERE day=?", (TODAY,))[0][0]
        assert row == "Fatigue", row
        return "second tap corrected the row: one row, components 'Fatigue'"
    check("03 marking twice corrects rather than duplicates", t03)

    def t04():
        for n in (12, 11, 10):
            s, j = post("/api/downday", {"day": D[n]})
            assert s == 200, j
        s, j = get("/api/feed/downdays?since=" + D[20])   # unauthorised here; runs via login route
        seen = []
        for n in (12, 11, 10):
            s, j = get("/api/downday?day=" + D[n])
            seen.append((j["run"]["n"], j["run"]["length"], j["run"]["start"], j["run"]["end"]))
        assert seen == [(1, 3, D[12], D[10]), (2, 3, D[12], D[10]), (3, 3, D[12], D[10])], seen
        return "three consecutive days -> one run of 3, positions 1/2/3"
    check("04 three consecutive days are one run", t04)

    def t05():
        post("/api/downday", {"day": D[7]})
        post("/api/downday", {"day": D[5]})   # gap at D[6]
        s, a = get("/api/downday?day=" + D[7])
        s, b = get("/api/downday?day=" + D[5])
        assert a["run"]["length"] == 1 and b["run"]["length"] == 1, (a["run"], b["run"])
        assert a["run"]["start"] != b["run"]["start"]
        runs = mod._down_runs([D[12], D[11], D[10], D[7], D[5], D[0]])
        assert [r["length"] for r in runs] == [3, 1, 1, 1], runs
        return "a one-day gap makes two runs; " + str([r["length"] for r in runs])
    check("05 a gap of one day makes two runs", t05)

    def t06():
        s, j = post("/api/downday", {"day": D[3]})
        assert j["marked"] and j["components"] == [] and j["coped"] == []
        s, v = get("/api/dayview?day=" + D[3])
        ents = [e for e in v.get("entries", []) if e.get("kind") == "Down day"]
        assert len(ents) == 1, v.get("entries")
        assert "no components" in ents[0]["sub"], ents[0]
        return "marked with nothing noted; day view says so"
    check("06 a down day with no components is still a down day", t06)

    def t07():
        before_v = q("SELECT COUNT(*) FROM vitals")[0][0]
        s, j = post("/api/downday", {"day": TODAY, "temp": 37.6})
        assert s == 200 and j.get("temp_saved") == 37.6, j
        rows = q("SELECT day, temp, notes FROM vitals WHERE day=? AND temp IS NOT NULL", (TODAY,))
        assert len(rows) == 1 and rows[0][1] == 37.6, rows
        assert q("SELECT COUNT(*) FROM vitals")[0][0] == before_v + 1
        cols = [r[1] for r in q("PRAGMA table_info(down_days)")]
        assert "temp" not in cols
        s, g = get("/api/downday?day=" + TODAY)
        assert g["temp"] and g["temp"]["value"] == 37.6, g.get("temp")
        assert q("SELECT COUNT(*) FROM down_days WHERE day=?", (TODAY,))[0][0] == 1
        return "37.6 reached vitals.temp; down_days unchanged and has no such column"
    check("07 a temperature reaches vitals, not down_days", t07)

    def t08():
        n0 = q("SELECT COUNT(*) FROM doses")[0][0]
        s, j = post("/api/downday", {"day": TODAY, "coped": ["Rested", "Testamol 500"]})
        assert s == 200 and j["doses"] == ["Testamol 500"], j
        rows = q("SELECT medicine, reason, status, med_id FROM doses WHERE day=? AND reason='Down day'", (TODAY,))
        assert len(rows) == 1 and rows[0][0] == "Testamol 500" and rows[0][2] == "EXTRA", rows
        assert rows[0][3] is not None, "not tied to the prnmeds row"
        assert q("SELECT COUNT(*) FROM doses")[0][0] == n0 + 1
        # correcting the row with the same chip must not log the tablet again
        s, j2 = post("/api/downday", {"day": TODAY, "coped": ["Rested", "Testamol 500", "Heat pad"]})
        assert j2["doses"] == [], j2["doses"]
        assert q("SELECT COUNT(*) FROM doses")[0][0] == n0 + 1, "logged twice"
        return "one chip, one doses row with reason 'Down day'; re-saving added none"
    check("08 an analgesic chip writes exactly one doses row", t08)

    def t09():
        s, j = get("/api/downday?day=" + D[11])
        assert j["run"]["n"] == 2 and not j["protocol"], j
        s, j = get("/api/downday?day=" + D[10])
        assert j["run"]["n"] == 3, j["run"]
        assert j["protocol"] == mod.DOWN_PROTOCOL, j["protocol"]
        assert "calprotectin" in j["protocol"] and "48 hours" in j["protocol"]
        assert not ADVICE.search(j["protocol"]), j["protocol"]
        return "day 2 silent; day 3 carries the protocol note verbatim, no advice words"
    check("09 the third consecutive day surfaces the flare protocol quietly", t09)

    def t10():
        s, j = get("/api/watch?days=14")
        row = j["row"]
        assert all("down" in r for r in row), "no down key on the row"
        marked = set(r["date"] for r in row if r["down"])
        want = set(d for d in (D[12], D[11], D[10], D[7], D[5], D[3], D[0]) if d >= row[0]["date"])
        assert marked == want, (marked, want)
        return "row carries a down lane marking exactly " + str(len(want)) + " days"
    check("10 the 14-day row carries a down-day lane on exactly the marked days", t10)

    def t11():
        s, j = post("/api/downday", {"day": D[25]})
        assert s == 200 and j["marked"], j
        s, v = get("/api/dayview?day=" + D[25])
        assert any(e.get("kind") == "Down day" for e in v.get("entries", [])), v.get("entries")
        fut = (T + timedelta(days=1)).isoformat()
        s, j = post("/api/downday", {"day": fut})
        assert s == 400, (s, j)
        assert q("SELECT COUNT(*) FROM down_days WHERE day=?", (fut,))[0][0] == 0
        return "a past day marked and listed in Day by day; a future day refused"
    check("11 past days backfill; future days are refused", t11)

    def t12():
        s, j = post("/api/downday/unmark", {"day": D[25]})
        assert s == 200 and j["marked"] is False, j
        assert q("SELECT COUNT(*) FROM down_days WHERE day=?", (D[25],))[0][0] == 0
        return "unmarked: row gone, state reads unmarked"
    check("12 unmark removes the row", t12)

    def t13():
        tok = open(os.environ["GUTLOG_FEED_TOKEN_FILE"], encoding="utf-8").read().strip()
        r = cl.get("/api/feed/downdays?since=" + D[20])
        assert r.status_code == 401, r.status_code
        r = cl.get("/api/feed/downdays?since=" + D[20], headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 200, r.status_code
        j = r.get_json()
        days = [d["day"] for d in j["days"]]
        assert D[12] in days and D[0] in days and D[25] not in days, days
        assert [x["length"] for x in j["runs"]][0] == 3, j["runs"]
        return "401 without the token, 200 with; days and runs listed"
    check("13 the down-day feed is bearer-gated and read-only", t13)

    # the day before D[7] is D[8]: give D[8] rows that already exist
    q("INSERT INTO activities(day,atime,kind,minutes,intensity,notes,created) VALUES(?,?,?,?,?,?,?)",
      (D[8], "09:00", "ot_day", 360, "", "", "x"))
    q("INSERT INTO activities(day,atime,kind,minutes,intensity,notes,created) VALUES(?,?,?,?,?,?,?)",
      (D[8], "18:00", "walk", 35, "", "", "x"))
    q("INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,status) VALUES(?,?,?,?,?,?,?,?)",
      (D[8], "20:00", "Testamol 500", "x", None, "", "x", "EXTRA"))
    q("INSERT INTO days(day,sleep,updated) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET sleep=excluded.sleep",
      (D[8], "5-6 h", "x"))

    def t14():
        s, j = get("/api/downdays?days=30")
        assert s == 200 and j["ok"], j
        pair = [p for p in j["pairs"] if p["day"] == D[7]]
        assert len(pair) == 1, [p["day"] for p in j["pairs"]]
        p = pair[0]
        assert p["before"] == D[8], p["before"]
        assert p["b"]["load_h"] == 6.0 and p["b"]["act_min"] == 35, p["b"]
        assert p["b"]["doses"] == 1 and p["b"]["sleep"] == "5-6 h", p["b"]
        assert p["d"]["day"] == D[7] and p["d"]["doses"] == 0, p["d"]
        assert "steps" in p["b"] and p["b"]["steps"] is None and j["link"] is False
        mm = dict((m["month"], m) for m in j["months"])
        assert sum(m["n"] for m in j["months"]) == len(j["marked"])
        assert sorted(r["length"] for r in j["runs"]) == [1, 1, 1, 1, 3], j["runs"]
        return "D-1 beside D: 6.0 h on legs, 35 min, 1 dose, sleep '5-6 h'; months and runs summed"
    check("14 each down day sits beside the day before it, from rows that already exist", t14)

    def t15():
        post("/api/downday", {"day": D[12], "coped": ["Kept moving indoors"]})
        post("/api/downday", {"day": D[7], "coped": ["Rested"]})
        s, j = get("/api/downdays?days=30")
        cr = j["coped_runs"]
        # today was tagged Rested in case 08, so two single-day runs carry it
        assert cr["kept_moving"]["n"] == 1 and cr["kept_moving"]["mean_len"] == 3.0, cr
        assert cr["rested"]["n"] == 2 and cr["rested"]["mean_len"] == 1.0, cr
        assert "not a recommendation" in cr["note"]
        blob = json.dumps(j)
        assert not ADVICE.search(blob), ADVICE.search(blob).group(0)
        assert not THIRD.search(blob), THIRD.search(blob).group(0)
        assert j["temps"]["with"] == 1 and j["temps"]["of"] == len(j["marked"])
        comps = dict((c["name"], c["n"]) for c in j["components"])
        assert comps.get("Fatigue") == 1, comps
        return "kept moving n=1 mean 3.0; rested n=2 mean 1.0; temps 1 of %d; no advice, no third person" % j["temps"]["of"]
    check("15 what he did is reported against run length as an observation with n", t15)

    # ================================================================ half 2
    two = os.path.exists(fit_path)

    def t16():
        assert two, "FitLog not found at " + fit_path + " (set GUTLOG_TEST_FITLOG)"
        work2 = tempfile.mkdtemp()
        tokf = os.path.join(work2, "feed.token")
        gport, fport = free_port(), free_port()
        os.environ.update(
            GUTLOG_DB=os.path.join(work2, "g.db"), GUTLOG_UPLOADS=os.path.join(work2, "up"),
            GUTLOG_FEED_TOKEN_FILE=tokf, GUTLOG_LINKS="1",
            GUTLOG_FITLOG_URL="http://127.0.0.1:%d" % fport,
            GUTLOG_FEED_URL="http://127.0.0.1:%d" % gport)
        gl = load("gutlog_m2", gut_path)
        gl.PAIN_ANALGESICS = {"Testamol 500": ("testamol", "Testamol 500")}
        gl.DOWN_COPED = list(gl.DOWN_COPED_BASE) + ["Testamol 500"]
        gc = gl.app.test_client()
        gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
        gc.get("/login")
        con = sqlite3.connect(os.environ["GUTLOG_DB"])
        con.execute("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,9,?,1)", ("Testamol 500", "testamol"))
        con.commit()
        con.close()

        os.environ.update(FITLOG_DB=os.path.join(work2, "f.db"),
                          FITLOG_SECRET="test-secret-not-real", FITLOG_GUTLOG_FEED="1")
        sys.path.insert(0, fit_dir)
        fl = load("fitlog_m2", fit_path)
        fdb = os.environ["FITLOG_DB"]
        _argv = sys.argv[:]
        mig = load("migrate_m2", os.path.join(fit_dir, "migrate_health_ingest.py"))
        sys.argv = ["migrate", fdb]
        assert mig.main() == 0, "wearable migration failed"
        sys.argv = _argv

        from werkzeug.serving import make_server
        gsrv = make_server("127.0.0.1", gport, gl.app)
        fsrv = make_server("127.0.0.1", fport, fl.app)
        for s in (gsrv, fsrv):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        time.sleep(0.3)

        fcon = sqlite3.connect(fdb)
        for n in range(10, 0, -1):
            fcon.execute("INSERT OR REPLACE INTO health_metrics(date,metric,value,unit,source,ingested_at) "
                         "VALUES(?,?,?,?,?,?)", (D[n], "steps", 6000, "count", "applewatch", "x"))
        fcon.execute("INSERT OR REPLACE INTO health_metrics(date,metric,value,unit,source,ingested_at) "
                     "VALUES(?,?,?,?,?,?)", (D[4], "steps", 100, "count", "applewatch", "x"))
        fcon.execute("INSERT INTO med_stack(name,generic,category,active) VALUES(?,?,?,1)",
                     ("Testamol 500", "testamol", "analgesic"))
        fcon.commit()
        fcon.close()

        # D[4] is a down day in GutLog, with the medicine chip
        r = gc.post("/api/downday", data=json.dumps({"day": D[4], "coped": ["Testamol 500"]}),
                    content_type="application/json")
        j = r.get_json()
        assert j["doses"] == ["Testamol 500"] and j["linked"] is True, j
        assert j["mirrored"] == ["Testamol 500"], j

        with fl.app.app_context():
            fl.set_setting("password_hash", fl.sha("pw"))
        fl.app.config["TESTING"] = True
        fc = fl.app.test_client()
        with fc.session_transaction() as s:
            s["auth"] = True
        html = fc.get("/watch").get_data(as_text=True)
        assert "Traceback" not in html
        assert 'class="wbar down"' in html, "down bar not marked"
        assert "down day (GutLog)" in html
        assert "leaving out 1 down day marked in GutLog" in html, html[html.find("Mean, low"):][:200]
        # the mean is over the nine 6000-step days only: 6000, not (9*6000+100)/10
        m = re.search(r"Steps</td><td>(\d+)", html)
        assert m and m.group(1) == "6000", (m.group(1) if m else html[:300])
        low = re.search(r"Steps</td><td>\d+</td><td>(\d+)", html)
        assert low and low.group(1) == "6000", "low still reads the down day"

        fcon = sqlite3.connect(fdb)
        n = fcon.execute("SELECT COUNT(*) FROM analgesic_log").fetchone()[0]
        fcon.close()
        assert n == 1, "analgesic_log rows: " + str(n)
        # re-saving the same chip must not mirror again
        gc.post("/api/downday", data=json.dumps({"day": D[4], "coped": ["Testamol 500", "Rested"]}),
                content_type="application/json")
        fcon = sqlite3.connect(fdb)
        n2 = fcon.execute("SELECT COUNT(*) FROM analgesic_log").fetchone()[0]
        fcon.close()
        assert n2 == 1, "mirrored twice: " + str(n2)

        # the view now sees steps for the day before, through FitLog's feed
        r = gc.get("/api/downdays?days=30")
        jv = r.get_json()
        assert jv["link"] is True
        p = [x for x in jv["pairs"] if x["day"] == D[4]][0]
        assert p["b"]["steps"] == 6000 and p["d"]["steps"] == 100, (p["b"], p["d"])
        gsrv.shutdown()
        fsrv.shutdown()
        return "bar marked, 'leaving out 1 down day', mean and low 6000; analgesic_log 1 row, re-save still 1; view sees 6000 before / 100 on"
    check("16 FitLog's trend leaves the down day out and the chip reaches its log once", t16)

    def t17():
        ast.parse(src, feature_version=(3, 9))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.JoinedStr):
                seg = ast.get_source_segment(src, node) or ""
                inner = re.findall(r"\{([^{}]*)\}", seg)
                for x in inner:
                    assert "\\" not in x, "PEP 701 backslash in f-string: " + seg[:80]
        i = src.index("/* GUTLOG_V3170_DOWN -- a down day is a calendar day")
        j = src.index("/* GUTLOG_V3130_WATCH -- the watch screen.", i)
        block = src[i:j]
        for tok in ("{{", "{%", "{#"):
            assert tok not in block, "Jinja token in new JS: " + tok
        assert js_balanced(block), "new JS block is not balanced"
        r = cl.get("/")
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and 'id="nowDown"' in html and 'id="downView"' in html
        assert 'id="dvDown"' in html
        return "parses as 3.9; new JS Jinja-clean and balanced; page renders with the card, the view and the backfill button"
    check("17 Python 3.9 syntax, Jinja-clean JS, page renders", t17)

    def t18():
        i = src.index("/* GUTLOG_V3170_DOWN -- a down day is a calendar day")
        j = src.index("/* GUTLOG_V3130_WATCH -- the watch screen.", i)
        lits = re.findall(r"'((?:[^'\\]|\\.)*)'", src[i:j])
        lits += list(mod.DOWN_COMPONENTS) + list(mod.DOWN_COPED_BASE) + [mod.DOWN_PROTOCOL]
        bad = [l for l in lits if THIRD.search(l)]
        assert not bad, bad[:3]
        assert "your" in mod.DOWN_PROTOCOL
        return "%d strings checked; none in the third person" % len(lits)
    check("18 no third-person pronoun in any rendered string", t18)

    def t19():
        p = os.path.join(os.path.dirname(here), "tools", "clinical_terms.local.txt")
        if not os.path.exists(p):
            return "SKIPPED out loud: tools/clinical_terms.local.txt absent on this machine"
        terms = [t.strip().lower() for t in open(p, encoding="utf-8") if t.strip() and not t.startswith("#")]
        hay = src.lower() + open(os.path.abspath(__file__), encoding="utf-8").read().lower()
        if os.path.exists(fit_path):
            hay += open(fit_path, encoding="utf-8").read().lower()
        hits = [t for t in terms if re.search(r"\b" + re.escape(t) + r"\b", hay)]
        assert not hits, "clinical term in code: " + ", ".join(hits[:5])
        return "%d terms checked against both app files and this suite" % len(terms)
    check("19 no medicine name in the code", t19)

    ok = all(r[0] for r in RESULTS)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

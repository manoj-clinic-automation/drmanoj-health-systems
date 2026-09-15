#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.7.0 -- /astaken counts what was taken (RXGUARD_V170_HONEST).

Runs a REAL GutLog (../gutlog/app.py, or GUTLOG_APP) on a free loopback port
and points a scratch RxGuard at it, so the page is exercised through the same
bearer feed it uses in production. Scratch databases and a scratch token only;
nothing live is touched.

FIXTURE. The medicine names are invented and the molecules are chosen ONLY
because the knowledge base scores them in the shape the case needs -- one
burden that crosses AMBER on what was taken and RED only when the untaken
medicines are added back, and a second that exists only if they are. It is
not anyone's list and must not be read as one: none of these molecules is on
the owner's record, and the file is on NO_SECRETS CLINICAL_ALLOW for that
reason.

    dicyclomine   constipating 3   scheduled in GutLog and dosed   -> taken
    diltiazem     constipating 1   dosed                           -> taken
    codeine       constipating 3   on the list, never dosed        -> NOT taken
    ibuprofen     nephrotoxic  2   on the list, never dosed        -> NOT taken
    naproxen      nephrotoxic  2   on the list, never dosed        -> NOT taken

so constipating is 4 as taken (AMBER) and 7 if everything on the list were
taken (RED), and nephrotoxic is 0 as taken and 4 if-all-taken (RED). Before
v1.7.0 the page led with both REDs.

Every assertion here is declared in new_assertions_v170.json and is seen
failing against the reconstructed v1.6.0 by tools/NEGATIVE_CONTROL.py.
Python 3.9.

  python3 test_astaken_honest.py [path/to/rxguard/app.py]
"""
import importlib.util
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime

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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    rx_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 \
        else os.path.join(here, "app.py")
    gut_path = os.environ.get("GUTLOG_APP") or \
        os.path.join(os.path.dirname(here), "gutlog", "app.py")
    if not os.path.exists(gut_path):
        print("FATAL: GutLog not found at " + gut_path)
        print("RESULT: FAILURES")
        return 1

    work = tempfile.mkdtemp()
    tokf = os.path.join(work, "feed.token")
    port = free_port()

    # ---- a real GutLog on loopback ---------------------------------------
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"),
                      GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(gut_path),
                      GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=tokf)
    sys.path.insert(0, os.path.dirname(gut_path))
    gm = load("gutlog_honest", gut_path)
    gc = gm.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
            follow_redirects=True)
    gdb = os.environ["GUTLOG_DB"]

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    # Names invented; molecules real, because the molecules are what is scored.
    FIX = [("Test Dicy 20", "dicyclomine"), ("Test Dilt 30", "diltiazem"),
           ("Test Unk 5", "unknownium")]
    mid = {}
    for nm, mol in FIX:
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 1, mol))
        mid[nm] = gq("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]
    # the first is in GutLog's own regimen AND dosed; the other two are dosed
    gc.post("/api/schedule", json={"med_id": mid["Test Dicy 20"], "slot": "MORNING",
                                   "dose_text": "1 tab"})
    # Derived from the clock, never literal. GutLog refuses a dose timed later
    # than now, so a fixture with a plausible-looking "08:00" logs nothing at
    # all on a morning run and every "not taken" assertion below then passes
    # vacuously. A literal "00:00" fails the same way two minutes after
    # midnight, which is exactly what tools/RUN_AT_TIME.py caught here.
    NOW_HM = datetime.now().strftime("%H:%M")
    for nm in ("Test Dicy 20", "Test Dilt 30", "Test Unk 5"):
        gc.post("/api/now/dose", json={"med_id": mid[nm], "status": "EXTRA", "dtime": NOW_HM})
    # three more exist in GutLog as as-needed medicines with no dose at all,
    # which is the case under test
    for nm, mol in (("Test Code 30", "codeine"), ("Test Ibu 400", "ibuprofen"),
                    ("Test Nap 250", "naproxen")):
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 9, mol))
        mid[nm] = gq("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, gm.app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    # ---- RxGuard pointed at it -------------------------------------------
    os.environ["GUTLOG_FEED_URL"] = "http://127.0.0.1:%d" % port
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = tokf
    os.environ["RXGUARD_GUTLOG_FEED"] = "1"
    sys.path.insert(0, here)
    rx = load("rxguard_honest", rx_path)
    rdb = os.path.join(work, "r.db")
    rapp = rx.create_app(db_path=rdb, secret="t")
    rapp.config["TESTING"] = True
    rc = rapp.test_client()
    rc.post("/login", data={"password": "testpassword1"})
    con = sqlite3.connect(rdb)
    for k, kind in (("dicyclomine", "chronic"), ("diltiazem", "chronic"),
                    ("codeine", "prn"), ("ibuprofen", "prn"),
                    ("naproxen", "prn")):
        con.execute("INSERT INTO medications(drug_key,raw_name,status,kind) "
                    "VALUES(?,?,'active',?)", (k, k, kind))
    con.commit()
    con.close()

    def view():
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            data, err = rx.gutlog_stack(14)
            assert not err, "feed error: " + str(err)
            return rx.astaken_view(data)

    ctx = {}

    def burden(v, word):
        for f in v.get("findings", []) + v.get("theoretical", []):
            if f["category"] == "Cumulative burden" and f["title"].lower().startswith(word):
                return f
        return None

    def total_of(f):
        m = re.search(r"total (\d+)", f["title"])
        return int(m.group(1)) if m else -1

    # ---------------------------------------------------------------- 01
    def t01():
        v = view()
        ctx["v"] = v
        ctx["html"] = rc.get("/astaken").get_data(as_text=True)
        assert "Traceback" not in ctx["html"], "the page raised"
        assert v["red"] == 0, (
            "headline RED count is %d, not 0 -- counted: %s"
            % (v["red"], [f["title"] for f in v["findings"] if f["flag"] == "RED"]))
        assert "<span class=\"flag RED\">0 RED</span>" in ctx["html"], \
            "the page does not print 0 RED"
        return "0 RED on a window whose only RED-making molecules were never taken"
    check("01 the headline counts 0 RED when nothing red was actually taken", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        f = burden(ctx["v"], "constipating")
        assert f, "no constipating burden finding at all"
        assert not f.get("theoretical"), "constipating was filed as theoretical"
        assert total_of(f) == 4 and f["flag"] == "AMBER", \
            "constipating reads %s %s, expected 4 AMBER" % (total_of(f), f["flag"])
        assert "dicyclomine (3)" in f["mechanism"] and "diltiazem (1)" in f["mechanism"], \
            "contributors: " + f["mechanism"]
        assert "codeine" not in f["mechanism"], \
            "an untaken molecule is inside the as-taken total: " + f["mechanism"]
        return "constipating 4 AMBER from dicyclomine and diltiazem only"
    check("02 a cumulative burden totals only the molecules with a logged dose", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        f = burden(ctx["v"], "constipating")
        assert f.get("if_all"), "no if-all-taken figure on the finding"
        assert "7" in f["if_all"] and "codeine (3)" in f["if_all"], \
            "if-all-taken text: " + f["if_all"]
        assert "7" not in f["title"], "the if-all-taken figure is in the title: " + f["title"]
        assert "If every medicine on the list were taken" in ctx["html"], \
            "the if-all-taken figure is not labelled on the page"
        return "7 shown inside the finding, labelled, and in no count"
    check("03 the if-all-taken figure is kept, labelled, and counted nowhere", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        v = ctx["v"]
        theo = v["theoretical"]
        assert theo, "nothing was filed as theoretical"
        nef = burden(v, "nephrotoxic")
        assert nef is not None and nef.get("theoretical"), \
            "nephrotoxic is not theoretical: " + str(nef and nef["flag"])
        assert nef["flag"] == "RED" and total_of(nef) == 4, \
            "nephrotoxic reads %s %s, expected 4 RED" % (total_of(nef), nef["flag"])
        assert nef not in v["findings"], "a theoretical finding is in the counted list"
        return "%d theoretical finding(s), including the nephrotoxic RED, all uncounted" % len(theo)
    check("05 findings resting only on untaken molecules are moved out of the count", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        h = ctx["html"]
        assert "If you also take your as-needed medicines" in h, "no theoretical section"
        txt = ctx["v"]["not_taken_text"]
        for n in ("codeine", "ibuprofen", "naproxen"):
            assert n in txt, "the section does not name " + n + ": " + txt
        assert "no dose logged in the last 14 days" in txt, txt
        mark = "on your list with no dose logged in the last"
        assert h.count(mark) == 1, \
            "the untaken medicines are named %d times, not once" % h.count(mark)
        return "one section, naming the three untaken medicines once"
    check("06 one collapsed section names the untaken medicines exactly once", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        h = ctx["html"]
        assert "Possibly stale" not in h and "POSSIBLY STALE" not in h, \
            "the per-finding stale block is still rendered"
        assert "may be theoretical rather than current" not in h, \
            "the per-finding stale sentence is still rendered"
        return "no per-finding stale block anywhere on the page"
    check("07 the per-finding POSSIBLY STALE block is gone from the page", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        h = ctx["html"]
        assert '<details class="finding' in h, "findings are not collapsible"
        one = h.split('<details class="finding', 1)[1].split("</details>", 1)[0]
        head, rest = one.split("</summary>", 1)
        assert "<h3>" in head, "the title is not in the collapsed state"
        assert 'class="cons"' in head, "the consequence is not in the collapsed state"
        for word in ("Mechanism", "Source"):
            assert word not in head, word + " is visible before the finding is opened"
            assert word in rest, word + " is missing from the expansion"
        return "chip, title and consequence collapsed; the rest one tap away"
    check("08 a finding shows severity, title and consequence until it is opened", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        h = ctx["html"]
        acts = [f for f in ctx["v"]["findings"] if f.get("action")]
        assert acts, "the fixture produced no finding with an ACTION to mark"
        assert 'class="needsact"' in h, \
            "a finding with an ACTION carries no marker in the collapsed state"
        blocks = [b for b in h.split('<details class="finding')[1:]
                  if 'class="needsact"' in b.split("</summary>", 1)[0]]
        assert len(blocks) == len(acts), \
            "%d marked, %d findings carry an action" % (len(blocks), len(acts))
        return "%d finding(s) with an action, each marked before opening" % len(acts)
    check("09 a finding carrying an action says so while still collapsed", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        h = ctx["html"]
        assert "plus the proposed change" not in h, \
            "the burden mechanism still mentions a proposed change on this page"
        f = burden(ctx["v"], "constipating")
        assert "what GutLog shows was taken" in f["mechanism"], f["mechanism"]
        return "the as-taken burden names its own basis, not a proposed change"
    check("10 no burden on this page claims a proposed change it does not have", t09)

    # ---------------------------------------------------------------- 10
    def t10():
        h = ctx["html"]
        caveat = "Absence of a flag means nothing was found in this knowledge base"
        assert caveat in h, "the caveat has been lost"
        assert h.count(caveat) == 1, "the caveat appears %d times" % h.count(caveat)
        assert h.index(caveat) > h.index("<h2>Findings from what was actually taken</h2>"), \
            "the caveat is still above the findings instead of at the page foot"
        return "the caveat survives, once, below the findings"
    check("11 the absence-of-a-flag caveat sits at the page foot", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        tok = open(tokf, encoding="utf-8").read().strip()
        r = rapp.test_client().get("/api/feed/status",
                                   headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 200, "feed status " + str(r.status_code)
        j = r.get_json()
        assert j["red"] == 0, "GutLog's banner would read %d RED" % j["red"]
        assert j.get("theoretical", 0) == len(ctx["v"]["theoretical"]), \
            "the feed does not carry the theoretical count"
        return "the banner feed reads 0 RED and %d theoretical" % j.get("theoretical", 0)
    check("12 the count GutLog's banner reads is the as-taken one", t11)

    # ---------------------------------------------------------------- 12
    # The one that has to move. An assertion that holds on both branches is
    # not evidence, so this compares BEFORE and AFTER a real dose row rather
    # than asserting RED once a dose exists -- v1.6.0 said RED either way.
    def t12():
        before = burden(ctx["v"], "constipating")
        b = (total_of(before), before["flag"], ctx["v"]["red"])
        gc.post("/api/now/dose", json={"med_id": mid["Test Code 30"],
                                       "status": "EXTRA", "dtime": NOW_HM})
        v2 = view()
        ctx["v2"] = v2
        after = burden(v2, "constipating")
        a = (total_of(after), after["flag"], v2["red"])
        assert b == (4, "AMBER", 0), "before the dose: %s, expected (4, 'AMBER', 0)" % (b,)
        assert a == (7, "RED", 1), "after the dose: %s, expected (7, 'RED', 1)" % (a,)
        assert "codeine (3)" in after["mechanism"], after["mechanism"]
        return "4 AMBER / 0 RED before the dose, 7 RED / 1 RED after it"
    check("04 logging a dose for an untaken medicine moves the as-taken total", t12)

    # ---------------------------------------------------------------- 13
    def t13():
        v2 = ctx["v2"]
        assert "codeine" not in (v2["not_taken_text"] or ""), \
            "codeine is still named as untaken after a dose: " + v2["not_taken_text"]
        h = rc.get("/astaken").get_data(as_text=True)
        assert '<span class="flag RED">1 RED</span>' in h, "the headline did not follow"
        return "the section and the headline both follow the new dose"
    check("13 the untaken list and the headline both follow the dose log", t13)

    # ---------------------------------------------------------------- 14
    def t14():
        src = open(rx_path, encoding="utf-8").read()
        block = src.split("details.finding{padding:0}", 1)
        assert len(block) == 2, "the v1.7.0 CSS block is not present"
        css = "details.finding{padding:0}" + block[1].split(".cat{font-size", 1)[0]
        small = [m for m in re.findall(r"font-size:([0-9.]+)px", css)
                 if float(m) < 14]
        assert not small, "new CSS declares type below 14px: " + ", ".join(small)
        return "every font-size in the new CSS is 14px or larger"
    check("14 nothing new on this page is typed below 14px", t14)

    # ---------------------------------------------------------------- 15/16
    # These two guard behaviour v1.6.0 already had, so no version control can
    # make them fail; each is broken on purpose in the manifest instead.
    def t15():
        r = rapp.test_client().get("/astaken")
        assert r.status_code in (302, 401), \
            "the page answered %d without a login" % r.status_code
        return "the page still needs an RxGuard login"
    check("15 the page still refuses to render without a login", t15)

    def t16():
        srv.shutdown()
        time.sleep(0.2)
        h = rc.get("/astaken").get_data(as_text=True)
        assert "GutLog is not reachable" in h and "Traceback" not in h, \
            "GutLog down was not degraded to a message"
        assert rc.get("/").status_code == 200, "dashboard broke with GutLog down"
        return "GutLog down is still a message, not a stack trace"
    check("16 GutLog being down is still a message, not an exception", t16)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("RxGuard v1.7.0 -- /astaken counts what was taken")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

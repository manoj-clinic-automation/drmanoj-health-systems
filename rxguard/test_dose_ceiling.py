#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.1 -- Daily dose, label maxima (RXGUARD_V180_DOSE + RXGUARD_V181_LABELMAX).

Runs a REAL GutLog (../gutlog/app.py, or GUTLOG_APP) on a free loopback port
and points a scratch RxGuard at it, through the same bearer feed as
production. Scratch databases, a scratch token and a scratch rules file;
nothing live is touched.

FIXTURE. Every medicine AND every molecule here is invented -- alphacet,
betacox, gammazol, deltapam, epsilotide, zetadol, etaverine exist nowhere --
so this file names nothing on anyone's record and needs no NO_SECRETS
allowance. The rules file is written by the test into its scratch folder.

    alphacet    standalone 500 and inside a 325 combination      one pool
    betacox     inside the combination, ceiling 60, taken twice  over -> RED
    zetadol     oral 50 and a nasal spray set to 10 per unit      one pool
    epsilotide  strengths vary: '20 + 10' logged                  30 mcg
    gammazol    the nightly regular; deltapam added on top        class load
    etaverine   strength recorded as a concentration              unreadable

Every assertion is declared in new_assertions_v180.json and is seen failing
against the reconstructed v1.7.0 by tools/NEGATIVE_CONTROL.py. Python 3.9.

  python3 test_dose_ceiling.py [path/to/rxguard/app.py]
"""
import importlib.util
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

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


RULES = {
    "_meta": {"version": "test-rules"},
    "ingredients": {
        "alphacet": {"label": "Alphacet", "ceiling": 3000, "unit": "mg", "window_h": 24},
        "betacox": {"label": "Betacox", "ceiling": 60, "unit": "mg", "window_h": 20,
                    "source": "Test label section 4.2: 60 mg once daily.",
                    "course_above": 50, "course_days": 2, "course_note": "Short course only."},
        "zetadol": {"label": "Zetadol", "ceiling": 100, "unit": "mg", "window_h": 24},
        "epsilotide": {"label": "Epsilotide", "ceiling": 290, "unit": "mcg", "window_h": 20},
        "gammazol": {"label": "Gammazol", "ceiling": 3, "unit": "mg", "window_h": 20},
        "deltapam": {"label": "Deltapam", "ceiling": 0.5, "unit": "mg", "window_h": 20},
        "etaverine": {"label": "Etaverine", "ceiling": 240, "unit": "mg", "window_h": 24},
    },
    "products": {"test zeta spray": {"per_unit": {"zetadol": 10}}},
    "classes": [
        {"id": "SED", "label": "Sedative load", "type": "addition_to_regular",
         "regular": "gammazol", "members": ["gammazol", "deltapam", "zetadol"], "window_h": 12},
    ],
}


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
    rules_path = os.path.join(work, "dose_rules.local.json")
    with open(rules_path, "w", encoding="utf-8") as fh:
        json.dump(RULES, fh)
    port = free_port()

    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"),
                      GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(gut_path),
                      GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=tokf)
    sys.path.insert(0, os.path.dirname(gut_path))
    gm = load("gutlog_dose", gut_path)
    gc = gm.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
            follow_redirects=True)
    gc.get("/")  # _migrate() runs per request, not at import
    gdb = os.environ["GUTLOG_DB"]

    def gq(sql, a=()):
        con = sqlite3.connect(gdb)
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    FIX = [("Test Alpha 500", "alphacet", ""),
           ("Test Combo", "betacox + alphacet", "60 mg + 325 mg"),
           ("Test Zeta 50", "zetadol", "50 mg"),
           ("Test Zeta Spray", "zetadol", "20 mg/mL"),
           ("Test Epsi", "epsilotide", "20"),
           ("Test Gamma 2", "gammazol", "2"),
           ("Test Delta 0.25", "deltapam", "0.25 mg"),
           ("Test Eta Syrup", "etaverine", "40 mg/5 mL")]
    mid = {}
    for nm, mol, st in FIX:
        gq("INSERT INTO prnmeds(name,sort,molecule,active) VALUES(?,?,?,1)", (nm, 1, mol))
        mid[nm] = gq("SELECT id FROM prnmeds WHERE name=?", (nm,))[0][0]
        if st:
            gq("INSERT OR REPLACE INTO med_salts(med_id,strength,no_salt,updated) VALUES(?,?,0,'t')",
               (mid[nm], st))
    gc.post("/api/schedule", json={"med_id": mid["Test Epsi"], "slot": "MORNING",
                                   "dose_text": "", "variants": "10|20|30"})
    gc.post("/api/schedule", json={"med_id": mid["Test Gamma 2"], "slot": "NIGHT",
                                   "dose_text": "1 tab"})

    # IST now, exactly as RxGuard computes it; every dose is placed relative
    # to it so the suite reads the same at any hour.
    NOW = datetime.utcnow() + timedelta(minutes=330)

    def dose(name, hours_ago, dose_text="", status="EXTRA"):
        t = NOW - timedelta(hours=hours_ago)
        gq("INSERT INTO doses(day,dtime,medicine,med_id,status,dose_text,created) "
           "VALUES(?,?,?,?,?,?,?)",
           (t.strftime("%Y-%m-%d"), t.strftime("%H:%M"), name, mid[name], status,
            dose_text, t.isoformat()))

    dose("Test Alpha 500", 6)
    dose("Test Alpha 500", 3)
    dose("Test Combo", 5)
    dose("Test Combo", 1)                   # betacox 120 in 20 h -> RED
    for back in (24, 48, 72):               # a run of daily betacox before today (DC010)
        dose("Test Combo", back)
    dose("Test Zeta 50", 4)
    dose("Test Zeta Spray", 2, "2")         # 2 x 10 by the rules override
    dose("Test Epsi", 2, "20 + 10", "TAKEN")
    dose("Test Gamma 2", 23, "1 tab", "TAKEN")   # yesterday's, 23 h ago
    dose("Test Gamma 2", 0.5, "1 tab", "TAKEN")  # tonight's
    dose("Test Delta 0.25", 1.5)
    dose("Test Delta 0.25", 1.45)           # three minutes later -- a double entry?
    dose("Test Eta Syrup", 2)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", port, gm.app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    os.environ["GUTLOG_FEED_URL"] = "http://127.0.0.1:%d" % port
    os.environ["GUTLOG_FEED_TOKEN_FILE"] = tokf
    os.environ["RXGUARD_GUTLOG_FEED"] = "1"
    os.environ["RXGUARD_DOSE_RULES"] = rules_path
    sys.path.insert(0, os.path.dirname(rx_path))
    rx = load("rxguard_dose", rx_path)
    rdb = os.path.join(work, "r.db")
    rapp = rx.create_app(db_path=rdb, secret="t")
    rapp.config["TESTING"] = True
    rc = rapp.test_client()
    rc.post("/login", data={"password": "testpassword1"})

    def view():
        with rapp.test_request_context():
            from flask import g
            g.db_path = rdb
            data, err = rx.gutlog_stack(14)
            assert not err, "feed error: " + str(err)
            return rx.astaken_view(data)

    ctx = {}

    def row(v, name):
        for r in v["dose"]["rows"]:
            if r["name"] == name:
                return r
        raise AssertionError("no Daily dose row for %s; rows: %s"
                             % (name, [r["name"] for r in v["dose"]["rows"]]))

    def by_rule(v, rid):
        return [f for f in v["findings"] if (f.get("rule_id") or "").startswith(rid)]

    def t01():
        v = view()
        ctx["v"] = v
        assert v.get("dose", {}).get("on"), "Daily dose is not on: %s" % v.get("dose")
        assert not v["dose"]["err"], v["dose"]["err"]
        r = row(v, "Alphacet")
        assert abs(r["total"] - 1650) < 1e-6, "alphacet total %s, expected 1650" % r["total"]
        assert set(r["products"]) == {"Test Alpha 500", "Test Combo"}, r["products"]
        return "500 + 500 standalone + 325 + 325 inside the combination = 1650 mg, one pool"
    check("01 an ingredient inside a combination counts into the standalone pool", t01)

    def t02():
        v = ctx["v"]
        red = [f for f in by_rule(v, "DC001") if "Betacox" in f["title"]]
        assert red and red[0]["flag"] == "RED", "no DC001 RED for betacox: %s" % \
            [f["title"] for f in v["findings"]]
        assert "120 mg" in red[0]["title"] and "60 mg" in red[0]["title"], red[0]["title"]
        assert "No more Betacox until" in red[0]["action"], red[0]["action"]
        assert row(v, "Betacox")["state"] == "over"
        return red[0]["title"]
    check("02 over the ceiling is RED, with the time it frees", t02)

    def t03():
        v = ctx["v"]
        assert v["red"] >= 1, "the as-taken RED count does not include the dose RED"
        tok = open(tokf, encoding="utf-8").read().strip()
        j = rapp.test_client().get("/api/feed/status",
                                   headers={"Authorization": "Bearer " + tok}).get_json()
        assert j["red"] == v["red"], "banner reads %s, page %s" % (j["red"], v["red"])
        return "page and GutLog banner both count it (%d RED)" % j["red"]
    check("03 the dose RED is counted in the page headline and GutLog's banner", t03)

    def t04():
        r = row(ctx["v"], "Zetadol")
        assert abs(r["total"] - 70) < 1e-6, "zetadol %s, expected 50 oral + 2 x 10 nasal = 70" % r["total"]
        assert set(r["routes"]) == {"oral", "nasal"}, r["routes"]
        return "oral 50 + nasal 2 x 10 = 70 mg, one pool"
    check("04 one generic by two routes is one pool", t04)

    def t05():
        r = row(ctx["v"], "Epsilotide")
        assert abs(r["total"] - 30) < 1e-6, "epsilotide %s mcg, expected 30" % r["total"]
        return "'20 + 10' logged on a varying-strength medicine = 30 mcg"
    check("05 a varying-strength dose is read from the strengths picked", t05)

    def t06():
        r = row(ctx["v"], "Gammazol")
        assert abs(r["total"] - 2) < 1e-6, (
            "gammazol reads %s mg: last night's dose 23 h ago was counted with tonight's"
            % r["total"])
        assert not [f for f in by_rule(ctx["v"], "DC00") if "Gammazol" in f["title"]
                    and f["rule_id"] in ("DC001", "DC002")], "a once-daily dose read as two"
        return "a nightly dose taken an hour earlier than yesterday's is not two doses"
    check("06 a once-a-day ingredient is not doubled by an earlier dose time", t06)

    def t07():
        f = [x for x in by_rule(ctx["v"], "DC006")]
        assert f, "no class-load finding for an addition on top of the regular"
        assert "Deltapam" in f[0]["mechanism"] and "Gammazol" in f[0]["title"], f[0]
        return f[0]["flag"] + " " + f[0]["title"]
    check("07 an addition on top of the nightly regular raises the class load", t07)

    def t08():
        f = by_rule(ctx["v"], "DC008")
        assert f and f[0]["flag"] == "UNKNOWN", "a double entry 3 minutes apart was not named"
        assert "Test Delta 0.25" in f[0]["title"], f[0]["title"]
        return f[0]["title"]
    check("08 the same product logged twice minutes apart is named, not hidden", t08)

    def t09():
        f = [x for x in by_rule(ctx["v"], "DC009") if "Test Eta Syrup" in x["title"]]
        assert f and f[0]["flag"] == "UNKNOWN", "an unreadable amount was not reported"
        return f[0]["title"]
    check("09 a dose whose amount cannot be read is reported, not silently zero", t09)

    def t10():
        h = rc.get("/dose").get_data(as_text=True)
        assert "Traceback" not in h and "<h1>Daily dose</h1>" in h, "the page did not render"
        assert "Betacox" in h and "Change" in h, "table or ceiling editor missing"
        rc.post("/dose", data={"ing": "betacox", "ceiling": "150"})
        v = view()
        r = row(v, "Betacox")
        assert r["ceiling"] == 150 and r["confirmed"], "ceiling %s own %r" % (r["ceiling"], r["confirmed"])
        assert not [f for f in by_rule(v, "DC001") if "Betacox" in f["title"]], \
            "still RED after the ceiling was raised to 150"
        assert "your limit" in rc.get("/dose").get_data(as_text=True)
        rc.post("/dose", data={"ing": "betacox", "ceiling": ""})
        r = row(view(), "Betacox")
        assert r["ceiling"] == 60 and not r["confirmed"], "clearing did not return to the label: %s" % r
        return "his own limit wins and shows as 'your limit'; clearing returns to the label maximum"
    check("10 his own limit overrides; clearing returns to the label maximum", t10)

    def t11():
        h = rc.get("/astaken").get_data(as_text=True)
        assert "<h2>Daily dose</h2>" in h and "Traceback" not in h, "no Daily dose card"
        assert h.index("<h2>Daily dose</h2>") < h.index("<h2>Findings from what was actually taken</h2>")
        assert "Daily dose</a>" in h, "no nav link"
        return "the card sits above the findings, and the nav links the page"
    check("11 /astaken carries the Daily dose card and the nav link", t11)

    def t14():
        h = rc.get("/dose").get_data(as_text=True)
        assert "Test label section 4.2" in h, "the ceiling's source is not shown"
        assert "Confirm" not in h and 'class="chip">default' not in h, "the page still asks to confirm"
        v = view()
        txt = " ".join((f.get("action") or "") + (f.get("source") or "") for f in v["findings"])
        assert "confirm" not in txt.lower(), "a finding still asks him to confirm: " + txt[:200]
        red = [f for f in by_rule(v, "DC001") if "Betacox" in f["title"]][0]
        assert "Test label section 4.2" in red["source"], red["source"]
        return "label maximum with its source; nothing asks to be confirmed"
    check("14 a ceiling is the label maximum, with its source, never 'confirm'", t14)

    def t15():
        f = by_rule(view(), "DC010")
        assert f and f[0]["flag"] == "AMBER" and "Betacox" in f[0]["title"], [x["title"] for x in view()["findings"]]
        assert "days running" in f[0]["title"] and "2 days at most" in f[0]["title"], f[0]["title"]
        return f[0]["title"]
    check("15 a short-course dose taken day after day is named", t15)

    def t12():
        rx.DOSE_RULES_PATH = os.path.join(work, "absent.local.json")
        try:
            v = view()
            assert not v["dose"]["on"] and not v["dose"]["findings"], "rules absent yet on"
            h = rc.get("/dose").get_data(as_text=True)
            assert "not installed" in h and "Traceback" not in h, "no clean message"
        finally:
            rx.DOSE_RULES_PATH = rules_path
        return "no rules file: feature off, one line, nothing raised"
    check("12 without a rules file the feature is off and says so", t12)

    def t13():
        srv.shutdown()
        time.sleep(0.2)
        h = rc.get("/dose").get_data(as_text=True)
        assert "Traceback" not in h and "GutLog" in h, "GutLog down broke /dose"
        return "GutLog down is a message on /dose"
    check("13 GutLog being down is a message on /dose, not an exception", t13)


    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("RxGuard v1.8.1 -- Daily dose, label maxima")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.7.0 Phase D -- server-side functional test.

Salts and strengths, the medicine-status banner, the Activity card (tapped
entries, watch merge, undo, retime), the activities feed and the outward
links (tested against a fake local server -- the real RxGuard, FitLog and
NLM are never contacted). Scratch database and token only. Python 3.9.

  python3 test_phase_d.py [path/to/app.py]     -> must print 18/18 passed
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

RESULTS = []
SEEN = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


class Fake(BaseHTTPRequestHandler):
    """Plays RxGuard status, FitLog activity and RxNav spelling suggestions."""
    token = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append((self.path, self.headers.get("Authorization") or ""))
        auth = self.headers.get("Authorization") == "Bearer " + Fake.token
        if self.path.startswith("/REST/spellingsuggestions.json"):
            body = {"suggestionGroup": {"suggestionList": {"suggestion": ["Loratadine", "Lorazepam"]}}}
        elif self.path.startswith("/api/feed/status"):
            if not auth:
                self.send_response(401)
                self.end_headers()
                return
            body = {"ok": True, "drafts": 2, "pairs": 1, "alerts": 0, "red": 1, "amber": 3,
                    "not_checkable": 0, "url": "/kb"}
        elif self.path.startswith("/api/feed/activity"):
            if not auth:
                self.send_response(401)
                self.end_headers()
                return
            day = self.path.split("day=")[-1]
            body = {"ok": True, "day": day, "steps": 8421, "exercise_minutes": 40, "mindful_min": 12,
                    "workouts": [{"kind": "walk", "wtype": "Outdoor Walk", "start": day + " 07:05:00",
                                  "end": day + " 07:45:00", "minutes": 40.2, "distance_km": 3.1}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        b = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


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
    os.environ.pop("GUTLOG_LINKS", None)
    sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
    spec = importlib.util.spec_from_file_location("gutlog_app_d", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)
    dbp = os.environ["GUTLOG_DB"]
    tok = open(os.environ["GUTLOG_FEED_TOKEN_FILE"]).read().strip()
    H = {"Authorization": "Bearer " + tok}

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

    def t00_schema_and_gate():
        names = set(r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'"))
        assert {"med_salts", "activities"} <= names, "tables missing"
        assert not mod._links_enabled(), "a scratch database must never reach out"
        assert c.get("/api/salt/suggest?q=lorat").get_json()["suggestions"] == [], "reached out"
        assert c.get("/api/medstatus").get_json()["rx"] is None, "status reached out"
        return "med_salts + activities; scratch DB makes no outward call"

    def t01_salts_list():
        j = c.get("/api/salts").get_json()
        assert j["meds"] and j["need"] == len(j["meds"]), "seeded meds should all need a salt"
        ctx["A"], ctx["B"], ctx["C"] = [m["id"] for m in j["meds"][:3]]
        assert c.get("/api/medstatus").get_json()["need_salt"] == j["need"], "banner count"
        return str(j["need"]) + " medicines need a salt; banner agrees"

    def t02_save_salt():
        r = c.post("/api/salt", json={"med_id": ctx["A"], "molecule": "  Fluconazole ",
                                      "strength": "150 mg"}).get_json()
        assert r["ok"] and r["molecule"] == "fluconazole", str(r)
        r = c.post("/api/salt", json={"med_id": ctx["B"], "molecule": "Ramipril+Hydrochlorothiazide",
                                      "strength": "5/12.5 mg"}).get_json()
        assert r["molecule"] == "ramipril + hydrochlorothiazide", "combination not normalised: " + r["molecule"]
        m = dict((x["id"], x) for x in c.get("/api/salts").get_json()["meds"])
        assert m[ctx["A"]]["strength"] == "150 mg" and not m[ctx["A"]]["needs"], str(m[ctx["A"]])
        assert q("SELECT molecule FROM prnmeds WHERE id=?", (ctx["A"],))[0][0] == "fluconazole"
        return "salt lower-cased, combinations joined with ' + ', strength kept"

    def t03_salt_guards():
        bad = [{"med_id": ctx["A"], "molecule": "drop table;"},
               {"med_id": 999999, "molecule": "fluconazole"},
               {"med_id": "x", "molecule": "fluconazole"},
               {"med_id": ctx["A"], "molecule": "a" * 130}]
        for b in bad:
            assert c.post("/api/salt", json=b).status_code == 400, "accepted " + str(b)[:50]
        assert q("SELECT molecule FROM prnmeds WHERE id=?", (ctx["A"],))[0][0] == "fluconazole"
        return str(len(bad)) + " bad salts refused; saved value untouched"

    def t04_not_single_drug():
        need0 = c.get("/api/salts").get_json()["need"]
        c.post("/api/salt", json={"med_id": ctx["C"], "molecule": "", "no_salt": 1})
        j = c.get("/api/salts").get_json()
        assert j["need"] == need0 - 1, "no_salt should stop asking"
        c.post("/api/salt", json={"med_id": ctx["C"], "molecule": "", "no_salt": 0})
        assert c.get("/api/salts").get_json()["need"] == need0, "unticking should ask again"
        return "'Not a single drug' stops the prompt; undoing brings it back"

    def t05_feed_strength():
        assert c.post("/api/schedule", json={"med_id": ctx["A"], "slot": "MORNING", "valid_from": Y3,
                                             "dose_text": "1 tab"}).get_json().get("ok")
        c.post("/api/now/dose", json={"med_id": ctx["B"], "status": "EXTRA", "day": TODAY, "dtime": "00:00"})
        j = app.test_client().get("/api/feed/stack", headers=H).get_json()
        reg = dict((r["med_id"], r) for r in j["regimen"])
        tk = dict((t["med_id"], t) for t in j["taken"])
        assert reg[ctx["A"]]["strength"] == "150 mg", "regimen strength missing"
        assert tk[ctx["B"]]["strength"] == "5/12.5 mg", "taken strength missing"
        return "the stack feed carries strength (regimen and as-taken)"

    def t06_activity_guards():
        bad = [{"kind": "swim", "minutes": 20}, {"kind": "walk", "minutes": 0},
               {"kind": "walk", "minutes": 601}, {"kind": "walk", "minutes": "x"},
               {"kind": "walk", "minutes": 20, "day": (T + timedelta(days=1)).isoformat()},
               {"kind": "walk", "minutes": 20, "atime": "25:00"},
               {"kind": "walk", "minutes": 20, "day": TODAY, "atime": "23:59"}]
        if datetime.now().strftime("%H:%M") >= "23:59":
            bad.pop()
        for b in bad:
            assert c.post("/api/activity", json=b).status_code == 400, "accepted " + str(b)
        assert q("SELECT COUNT(*) FROM activities")[0][0] == 0, "a refused call wrote a row"
        return str(len(bad)) + " bad entries refused"

    def t07_activity_add_undo():
        ids = []
        for b in ({"kind": "walk", "minutes": 30, "intensity": "Moderate", "day": Y1, "atime": "07:15"},
                  {"kind": "meditation", "minutes": 15, "day": Y1, "atime": "21:00"},
                  {"kind": "cycle_static", "minutes": 20, "intensity": "Hard", "day": Y1, "atime": "18:00"},
                  {"kind": "treadmill", "minutes": 10, "intensity": "Silly", "day": Y1, "atime": "19:00"}):
            r = c.post("/api/activity", json=b).get_json()
            assert r["ok"], str(r)
            ids.append(r["id"])
        ctx["walk"] = ids[0]
        j = c.get("/api/activity?day=" + Y1).get_json()
        assert [i["kind"] for i in j["items"]] == ["walk", "cycle_static", "treadmill", "meditation"], \
            "order: " + str([i["kind"] for i in j["items"]])
        assert j["summary"]["minutes"] == 75 and j["summary"]["steps"] == 0, str(j["summary"])
        assert j["items"][2]["intensity"] == "", "junk intensity kept"
        assert j["watch"] is None, "watch status shown although links are off"
        c.post("/api/activity/undo/" + str(ids[3]), json={})
        assert c.get("/api/activity?day=" + Y1).get_json()["summary"]["minutes"] == 65, "undo"
        return "4 logged in time order, 75 min; junk intensity dropped; undo -> 65 min"

    def t08_merge_rules():
        man = [{"id": 1, "kind": "walk", "atime": "07:15", "minutes": 30, "intensity": "Easy"},
               {"id": 2, "kind": "walk", "atime": "09:00", "minutes": 20, "intensity": ""},
               {"id": 3, "kind": "cycle_road", "atime": "07:20", "minutes": 25, "intensity": ""}]
        w = {"workouts": [{"kind": "walk", "start": "2026-01-01 07:05:00", "end": "2026-01-01 07:45:00",
                           "minutes": 40.2, "distance_km": 3.1}], "mindful_min": 12}
        it = mod.merge_activity(man, w)
        walks = [i for i in it if i["kind"] == "walk"]
        assert len(walks) == 2, "the matched walk should show once: " + str(walks)
        cw = [i for i in walks if i["confirmed"]][0]
        assert cw["minutes"] == 40 and cw["intensity"] == "Easy" and cw["id"] == 1, str(cw)
        assert any(i["id"] == 2 and i["source"] == "manual" for i in walks), "09:00 walk lost"
        assert any(i["kind"] == "cycle_road" and i["source"] == "manual" for i in it), "other kind merged"
        assert any(i["label"] == "Mindful minutes" and i["minutes"] == 12 for i in it), "mindful missing"
        it2 = mod.merge_activity(man + [{"id": 4, "kind": "meditation", "atime": "21:00", "minutes": 10,
                                         "intensity": ""}], w)
        assert not any(i["label"] == "Mindful minutes" for i in it2), "mindful doubled a tapped meditation"
        return "watch walk confirms the 07:15 tap (watch minutes, your intensity); 09:00 stays; no doubles"

    def t09_dayview_retime_delete():
        ev = c.get("/api/dayview?day=" + Y1).get_json()["entries"]
        acts = [e for e in ev if e["kind"] == "Activity"]
        assert len(acts) == 3 and any("Walk 30 min" in e["title"] for e in acts), str(acts)
        r = c.post("/api/retime", json={"table": "activities", "id": ctx["walk"], "day": Y1,
                                        "time": "06:40"}).get_json()
        assert r["ok"], str(r)
        assert q("SELECT atime FROM activities WHERE id=?", (ctx["walk"],))[0][0] == "06:40"
        assert q("SELECT COUNT(*) FROM edits WHERE tbl='activities'")[0][0] == 1, "no audit row"
        rid = q("SELECT id FROM activities WHERE kind='meditation'")[0][0]
        assert c.post("/api/delete/activities/" + str(rid), json={}).status_code == 200
        assert not q("SELECT 1 FROM activities WHERE id=?", (rid,)), "delete failed"
        return "activities in Day by day; retime audited; delete works"

    def t10_feed_activities():
        assert c.get("/api/feed/activities").status_code == 401, "feed open without token"
        j = app.test_client().get("/api/feed/activities?since=" + Y3, headers=H).get_json()
        assert j["ok"] and len(j["activities"]) == 2, str(j)
        assert set(j["activities"][0]) == {"day", "atime", "kind", "minutes", "intensity"}, "extra fields"
        r = app.test_client().post("/api/activity", headers=H, json={"kind": "walk", "minutes": 5})
        assert r.status_code in (302, 401), "feed token could write"
        return "token-gated, 2 rows, minimal fields, cannot write"

    def t11_page():
        html = c.get("/").get_data(as_text=True)
        for needle in ('id="nowAct"', 'id="nowMedStatus"', 'id="meds-salts"', 'data-s="salts"',
                       "function loadActivity", "function loadSalts", "act:'#nowAct'"):
            assert needle in html, needle + " missing from page"
        return "page renders the Activity card, banner and Salts segment"

    # ---- outward links, against a fake local server
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    Fake.token = tok
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:" + str(srv.server_address[1])

    def links_on():
        os.environ["GUTLOG_LINKS"] = "1"
        mod.RXGUARD_URL = mod.FITLOG_URL = mod.RXNAV_URL = base
        mod._LINK_CACHE.clear()

    def t12_medstatus_linked():
        links_on()
        j = c.get("/api/medstatus").get_json()
        assert j["rx"] and j["rx"]["drafts"] == 2 and j["rx"]["red"] == 1, str(j)
        return "RxGuard status read with the feed token: 2 drafts, 1 RED"

    def t13_watch_linked():
        c.post("/api/activity", json={"kind": "walk", "minutes": 30, "intensity": "Moderate",
                                      "day": Y1, "atime": "07:15"})
        j = c.get("/api/activity?day=" + Y1).get_json()
        walks = [i for i in j["items"] if i["kind"] == "walk"]
        assert j["summary"]["steps"] == 8421 and j["watch"]["ok"], str(j["summary"])
        assert any(i["confirmed"] and i["minutes"] == 40 and i["distance_km"] == 3.1 for i in walks), str(walks)
        assert any(i["label"] == "Mindful minutes" and i["minutes"] == 12 for i in j["items"]), "mindful missing"
        return "watch walk, steps and mindful minutes joined; 07:15 tap shows as watch-confirmed"

    def t14_suggest_no_token():
        SEEN[:] = []
        s = c.get("/api/salt/suggest?q=lorat").get_json()["suggestions"]
        assert s[:1] == ["loratadine"], str(s)
        nlm = [a for p, a in SEEN if p.startswith("/REST/")]
        assert nlm and all(a == "" for a in nlm), "the feed token was sent to NLM"
        assert c.get("/api/salt/suggest?q=li").get_json()["suggestions"] == [], "short query sent"
        return "suggestions lower-cased; no token ever sent to NLM"

    def t15_cache():
        SEEN[:] = []
        for _ in range(3):
            c.get("/api/medstatus")
        assert sum(1 for p, a in SEEN if p.startswith("/api/feed/status")) == 0, "status not cached"
        return "status cached (no repeat calls within 5 minutes)"

    def t16_down():
        mod.RXGUARD_URL = mod.FITLOG_URL = "http://127.0.0.1:9"
        mod._LINK_CACHE.clear()
        j = c.get("/api/medstatus").get_json()
        assert j["rx"] is None and "need_salt" in j, str(j)
        a = c.get("/api/activity?day=" + Y1).get_json()
        assert a["watch"] == {"ok": False} and a["summary"]["steps"] == 0, str(a["watch"])
        assert any(i["source"] == "manual" for i in a["items"]), "tapped entries lost when watch down"
        return "RxGuard/FitLog down -> banner and card still work, 'not reachable' shown"

    def t17_override_off():
        os.environ["GUTLOG_LINKS"] = "0"
        mod.RXGUARD_URL = base
        mod._LINK_CACHE.clear()
        SEEN[:] = []
        c.get("/api/medstatus")
        assert not SEEN, "GUTLOG_LINKS=0 still reached out"
        return "GUTLOG_LINKS=0 stops every outward call"

    tests = [
        ("00 schema + no outward calls", t00_schema_and_gate),
        ("01 salts list", t01_salts_list),
        ("02 save salt + strength", t02_save_salt),
        ("03 salt guards", t03_salt_guards),
        ("04 not a single drug", t04_not_single_drug),
        ("05 feed carries strength", t05_feed_strength),
        ("06 activity guards", t06_activity_guards),
        ("07 activity add + undo", t07_activity_add_undo),
        ("08 watch merge rules", t08_merge_rules),
        ("09 day view, retime, delete", t09_dayview_retime_delete),
        ("10 activities feed", t10_feed_activities),
        ("11 page", t11_page),
        ("12 RxGuard status (fake)", t12_medstatus_linked),
        ("13 watch data (fake FitLog)", t13_watch_linked),
        ("14 NLM suggestions (fake)", t14_suggest_no_token),
        ("15 status cached", t15_cache),
        ("16 companions down", t16_down),
        ("17 override off", t17_override_off),
    ]
    print("=" * 66)
    print("GutLog v3.7.0 Phase D - functional test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    srv.shutdown()
    os.environ.pop("GUTLOG_LINKS", None)
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

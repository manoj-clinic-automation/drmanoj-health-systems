#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.20.0 Phase O -- two stock pipelines and the strength links.

Properties, not counts. Every case enters a v3.20.0 code path; each is
declared in new_assertions_v3200.json so tools/NEGATIVE_CONTROL.py can see it
fail against v3.19.0 or against a deliberate break of v3.20.0.

Scratch database, outward links off, synthetic medicine ids only -- no
medicine name appears in this file. Dates come from the clock.

  python3 test_phase_o.py [path/to/app.py]
"""
import ast
import importlib.util
import math
import os
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
    spec = importlib.util.spec_from_file_location("gutlog_app_o", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = mod.app
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)
    dbp = os.environ["GUTLOG_DB"]
    src = open(app_path, encoding="utf-8").read()

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
    first = date(T.year + 1, 1, 1) if T.month == 12 else date(T.year, T.month + 1, 1)
    GAP = (first - T).days
    ctx = {}

    def count_yesterday(mid, qty):
        r = c.post("/api/stock/count", json={"med_id": mid, "qty": qty})
        assert r.status_code == 200, "count refused: " + r.get_data(as_text=True)
        q("UPDATE stock_events SET at=? WHERE id=(SELECT MAX(id) FROM stock_events)",
          (Y1 + " 00:00",))

    def stock(mid):
        for r in c.get("/api/stock").get_json()["rows"]:
            if r["med_id"] == mid:
                return r
        raise AssertionError("med %s not in /api/stock" % mid)

    def order():
        r = c.get("/api/order")
        assert r.status_code == 200, "GET /api/order -> %d" % r.status_code
        return r.get_json()

    def dose(mid, txt):
        r = c.post("/api/now/dose", json={"med_id": mid, "status": "EXTRA", "day": TODAY,
                                         "dtime": "00:00", "dose_text": txt})
        assert r.status_code == 200, "dose refused: " + r.get_data(as_text=True)

    def pack(mid, ps, keep=0):
        r = c.post("/api/stock/pack", json={"med_id": mid, "pack_size": ps,
                                            "pack_type": "strip", "keep": keep})
        assert r.status_code == 200, "pack refused"

    meds = c.get("/api/prnmeds/full").get_json()
    assert len(meds) >= 7, "need 7 seeded medicines"
    V, P145, P72, S, U, A, W = [m["id"] for m in meds[:7]]
    names = dict((m["id"], m["name"]) for m in meds)
    for mid, body in ((V, {"dose_text": "", "variants": "72|145|290"}),
                      (A, {"dose_text": "1 tab"}),
                      (W, {"dose_text": "", "variants": "1|2"})):
        b = {"med_id": mid, "slot": "MORNING", "valid_from": Y3}
        b.update(body)
        assert c.post("/api/schedule", json=b).get_json().get("ok"), "schedule add failed"
    LINKS = [{"variant": "72", "stock_med_id": P72, "units": 1},
             {"variant": "145", "stock_med_id": P145, "units": 1},
             {"variant": "290", "stock_med_id": P145, "units": 2}]

    def t01():
        bad = [("/api/stock/link", {"med_id": A, "links": LINKS}),
               ("/api/stock/link", {"med_id": V, "links": [{"variant": "999", "stock_med_id": P145}]}),
               ("/api/stock/link", {"med_id": V, "links": [{"variant": "72", "stock_med_id": V}]}),
               ("/api/stock/link", {"med_id": V, "links": [{"variant": "72", "stock_med_id": P72, "units": 20}]}),
               ("/api/stock/link", {"med_id": V, "links": [{"variant": "72", "stock_med_id": W}]}),
               ("/api/stock/link", {"med_id": 999999, "links": LINKS}),
               ("/api/order/sos/save", {})]
        for u, b in bad:
            r = c.post(u, json=b)
            assert r.status_code == 400, "not refused: %s %s -> %d" % (u, b, r.status_code)
        assert q("SELECT COUNT(*) FROM stock_links")[0][0] == 0, "a refused call wrote a link"
        assert q("SELECT COUNT(*) FROM stock_orders")[0][0] == 0, "a refused call wrote an order"
        return "%d bad link and SOS calls refused" % len(bad)
    check("01 bad strength links and an empty SOS order are refused", t01)

    def t02():
        r = c.post("/api/stock/link", json={"med_id": V, "links": LINKS})
        assert r.status_code == 200 and r.get_json()["n"] == 3, r.get_data(as_text=True)
        count_yesterday(P145, 30)
        count_yesterday(P72, 10)
        dose(V, "145")
        assert stock(P145)["current"] == 29, "145 did not come out of its pack"
        dose(V, "290")
        assert stock(P145)["current"] == 27, "290 did not take two of the 145 pack"
        dose(V, "145 + 72")
        assert stock(P145)["current"] == 26 and stock(P72)["current"] == 9, \
            "a combined dose did not take one from each pack"
        did = q("SELECT MAX(id) FROM doses")[0][0]
        c.post("/api/now/undo/" + str(did), json={})
        assert stock(P145)["current"] == 27 and stock(P72)["current"] == 10, "undo did not restore both packs"
        dose(V, "145 + 72")
        return "145 -> 1, 290 -> 2 of the 145 pack, 145 + 72 -> one each; undo restores both"
    check("02 a dose logged by strength comes out of the linked packs", t02)

    def t03():
        rv = stock(V)
        assert not rv["trackable"] and rv["variants"] == ["72", "145", "290"], rv
        assert len(rv["links"]) == 3 and names[P145] in rv["why"] and names[P72] in rv["why"], rv["why"]
        rp = stock(P145)
        assert rp["tracked"] and rp["mode"] == "per_dose" and rp["linked_from"] == names[V], rp
        assert abs(rp["per_day"] - 4 / 14.0) < 0.01, "linked use not averaged: %s" % rp["per_day"]
        rw = stock(W)
        assert "not tracked until" in rw["why"], rw["why"]
        return "the variant row names where it is counted; the pack is per-dose stock at 4/14 a day"
    check("03 the variant row says where it is counted, the pack is tracked", t03)

    def t04():
        count_yesterday(P145, 3)
        j = order()
        l = [x for x in j["lines"] if x["med_id"] == P145]
        assert l and l[0]["basis"] == "linked", "linked pack not on the monthly order: %s" % j["lines"]
        pd = stock(P145)["per_day"]
        want_t = max(pd * 40, 0)
        exp = stock(P145)["current"] - pd * GAP
        assert abs(l[0]["target"] - round(want_t, 1)) < 0.06, (l[0], want_t)
        assert l[0]["units"] == int(math.ceil(want_t - exp - 1e-9)), (l[0], want_t, exp)
        pack(P72, 10, keep=10)
        count_yesterday(P72, 5)
        l72 = [x for x in order()["lines"] if x["med_id"] == P72]
        assert l72 and l72[0]["target"] == 10 and l72[0]["qty"] == "1 strip of 10", \
            "keep is not the floor for a linked pack: %s" % l72
        return "linked pack ordered from its use x 40, keep as the floor; %s" % l[0]["qty"]
    check("04 linked packs ride the monthly order", t04)

    def t05():
        j = order()
        sos_ids = [x["med_id"] for x in j["sos"]["lines"]]
        assert P72 not in sos_ids and P145 not in sos_ids, "a daily pack was put on the SOS list"
        assert A not in sos_ids
        return "linked and scheduled medicines never reach the SOS list"
    check("05 daily medicines never reach the SOS list", t05)

    def t06():
        pack(S, 15, keep=15)
        count_yesterday(S, 5)
        assert S not in [x["med_id"] for x in order()["sos"]["lines"]], "5 of 15 raised as low"
        count_yesterday(S, 4)
        l = [x for x in order()["sos"]["lines"] if x["med_id"] == S]
        assert l and l[0]["reorder_at"] == 5 and l[0]["qty"] == "1 strip of 15", l
        assert S not in [x["med_id"] for x in order()["lines"]], "SOS medicine on the monthly order"
        pack(U, 1, keep=1)
        count_yesterday(U, 1)
        assert U not in [x["med_id"] for x in order()["sos"]["lines"]], "1 of keep 1 raised as low"
        count_yesterday(U, 0)
        assert U in [x["med_id"] for x in order()["sos"]["lines"]], "0 of keep 1 not raised"
        return "low under a third of keep: 5/15 fine, 4/15 low; 1/1 fine, 0/1 low"
    check("06 an SOS medicine is raised once below a third of its keep", t06)

    def t07():
        r = c.post("/api/order/sos/save", json={})
        assert r.status_code == 200, r.get_data(as_text=True)
        oid = r.get_json()["id"]
        j = order()["sos"]
        assert not j["lines"] and set(j["ordered"]) == set([names[S], names[U]]), j
        assert c.post("/api/order/sos/save", json={}).status_code == 400, "saved an empty SOS order"
        r2 = c.post("/api/order/received", json={"id": oid})
        assert r2.status_code == 200 and r2.get_json()["n"] == 2
        assert stock(S)["current"] == 19 and stock(U)["current"] == 1, "received did not add"
        assert not order()["sos"]["lines"], "raised again after receipt"
        c.post("/api/stock/count", json={"med_id": U, "qty": 0})
        r3 = c.post("/api/order/sos/save", json={})
        assert r3.status_code == 200 and r3.get_json()["id"] != oid, "a new low after receipt reused the old order"
        assert q("SELECT COUNT(*) FROM stock_orders WHERE month='SOS'")[0][0] == 2
        return "on order -> shown as ordered; received adds; the next low starts a new SOS order"
    check("07 the SOS order: ordered once, received, then a fresh one", t07)

    def t08():
        r = c.post("/api/stock/link", json={"med_id": V, "links": []})
        assert r.status_code == 200
        assert "not tracked until" in stock(V)["why"]
        assert P145 not in [x["med_id"] for x in order()["lines"]], "unlinked pack still ordered monthly"
        c.post("/api/stock/link", json={"med_id": V, "links": LINKS})
        return "clearing the links takes the packs back off the monthly order"
    check("08 clearing the links takes the packs off the monthly order", t08)

    def t09():
        anon = app.test_client()
        for u in ("/api/order/sos/save", "/api/stock/link"):
            r = anon.post(u, json={})
            assert r.status_code in (301, 302, 401), "%s answered without login: %d" % (u, r.status_code)
        return "both new routes need the login"
    check("09 the new routes refuse without a login", t09)

    def t10():
        ast.parse(src, feature_version=(3, 9))
        i = src.index("/* ---------- TWO PIPELINES (GUTLOG_V3200_PIPES)")
        j = src.index("/* ---------- VITALS LOG", i)
        blk = src[i:j]
        for t in ("{{", "{%", "{#"):
            assert t not in blk, "Jinja token " + t
        assert blk.count("{") == blk.count("}") and blk.count("(") == blk.count(")"), "unbalanced JS"
        html = c.get("/").get_data(as_text=True)
        for ident in ('id="sosCard"', 'id="sosSend"', 'id="sosRecv"', "Link strengths"):
            assert ident in html, "page lacks " + ident
        return "parses as 3.9; new JS Jinja-clean and balanced; page renders the SOS card and link form"
    check("10 Python 3.9 syntax, Jinja-clean JS, page renders the SOS card", t10)

    ok = all(r[0] for r in RESULTS)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

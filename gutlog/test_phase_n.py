#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.19.0 Phase N -- the monthly medicine order.
(Cases 05, 06 and 10 updated for v3.20.0: SOS medicines left the monthly order.)

Properties, not counts. Every numbered case enters a v3.19.0 code path, and
every one is declared in new_assertions_v3190.json so tools/NEGATIVE_CONTROL.py
can see it fail against v3.18.0 (or against a deliberate break of v3.19.0).

The arithmetic is checked against the date it runs on, never a written-down
date: `gap` is the number of days from today to the 1st of next month, and
each expected figure is derived from it, so the suite is the same colour on
the 1st, the 15th and the 31st. The last-week rule is checked on fixed dates
by calling the plan directly.

Scratch database, outward links off, synthetic medicine ids only -- no
medicine name appears in this file.

  python3 test_phase_n.py [path/to/app.py]
"""
import ast
import importlib.util
import math
import os
import re
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


def month_after(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


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
    spec = importlib.util.spec_from_file_location("gutlog_app_n", app_path)
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
    FIRST = month_after(T)
    GAP = (FIRST - T).days
    ctx = {}

    def count_yesterday(mid, qty):
        r = c.post("/api/stock/count", json={"med_id": mid, "qty": qty})
        assert r.status_code == 200, "count refused: " + r.get_data(as_text=True)
        q("UPDATE stock_events SET at=? WHERE id=(SELECT MAX(id) FROM stock_events)",
          (Y1 + " 00:00",))

    def order():
        r = c.get("/api/order")
        assert r.status_code == 200, "GET /api/order -> %d" % r.status_code
        return r.get_json()

    def line(j, mid):
        for l in j["lines"]:
            if l["med_id"] == mid:
                return l
        return None

    def stock(mid):
        for r in c.get("/api/stock").get_json()["rows"]:
            if r["med_id"] == mid:
                return r

    def pack(mid, ps, pt="strip", keep=0):
        r = c.post("/api/stock/pack", json={"med_id": mid, "pack_size": ps,
                                            "pack_type": pt, "keep": keep})
        assert r.status_code == 200, "pack refused: " + r.get_data(as_text=True)

    meds = c.get("/api/prnmeds/full").get_json()
    assert len(meds) >= 6, "need 6 seeded medicines"
    ctx["A"], ctx["X"], ctx["Y"], ctx["Z"], ctx["V"], ctx["E"] = [m["id"] for m in meds[:6]]
    for mid, body in ((ctx["A"], {"dose_text": "1 tab"}),
                      (ctx["Z"], {"dose_text": "1 tab"}),
                      (ctx["E"], {"dose_text": "1 tab"}),
                      (ctx["V"], {"dose_text": "", "variants": "1|2|3"})):
        b = {"med_id": mid, "slot": "MORNING", "valid_from": Y3}
        b.update(body)
        assert c.post("/api/schedule", json=b).get_json().get("ok"), "schedule add failed"

    def t01():
        bad = [("/api/stock/pack", {"med_id": ctx["A"], "pack_size": -1}),
               ("/api/stock/pack", {"med_id": ctx["A"], "pack_size": 5000}),
               ("/api/stock/pack", {"med_id": ctx["A"], "pack_size": 10, "pack_type": "crate"}),
               ("/api/stock/pack", {"med_id": 999999, "pack_size": 10}),
               ("/api/stock/pack", {"med_id": ctx["A"], "pack_size": "x"}),
               ("/api/order/days", {"days": 5}),
               ("/api/order/days", {"days": 200}),
               ("/api/order/received", {"id": 999999}),
               ("/api/order/received/undo", {"id": 999999}),
               ("/api/order/save", {})]
        for u, b in bad:
            r = c.post(u, json=b)
            assert r.status_code == 400, "not refused with 400: %s %s -> %d" % (u, b, r.status_code)
        assert q("SELECT COUNT(*) FROM stock_orders")[0][0] == 0, "a refused call wrote an order"
        return "%d bad calls refused, including saving an empty order" % len(bad)
    check("01 bad pack, days, order and receipt calls are refused", t01)

    def t02():
        count_yesterday(ctx["A"], 10)
        pack(ctx["A"], 10, "strip")
        j = order()
        l = line(j, ctx["A"])
        assert l, "scheduled medicine short of target is not in the order"
        need = 40 - (10 - GAP)
        packs = int(math.ceil(need / 10.0))
        assert l["packs"] == packs and l["units"] == packs * 10, \
            "gap %d: want %d packs, got %s" % (GAP, packs, l)
        assert l["qty"] == "%d %s of 10" % (packs, "strip" if packs == 1 else "strips"), l["qty"]
        assert l["basis"] == "schedule" and l["target"] == 40, l
        want_label = ("January February March April May June July August September "
                      "October November December").split()[FIRST.month - 1] + " " + str(FIRST.year)
        assert j["label"] == want_label and j["month"] == FIRST.strftime("%Y-%m"), j["label"]
        assert j["text"].startswith("Medicines order - " + want_label + "\n1. "), j["text"][:60]
        return "count 10, %d days to the 1st -> %s; order is for %s" % (GAP, l["qty"], want_label)
    check("02 the order counts from the stock expected on the 1st, in whole packs", t02)

    def t03():
        count_yesterday(ctx["A"], 45 + GAP)
        assert line(order(), ctx["A"]) is None, "ordered although 45 days will be on hand on the 1st"
        count_yesterday(ctx["A"], 35 + GAP)
        l = line(order(), ctx["A"])
        assert l and l["packs"] == 1 and l["units"] == 10, "5 short should be one strip: %s" % l
        return "45 on the 1st -> nothing; 35 on the 1st -> one strip, not a flat 40"
    check("03 the order is a top-up to the target, never a flat month", t03)

    def t04():
        count_yesterday(ctx["A"], 20)
        before = line(order(), ctx["A"])
        r = c.post("/api/stock/fill", json={"days": 7})
        assert r.status_code == 200, "fill refused"
        after = line(order(), ctx["A"])
        assert stock(ctx["A"])["current"] == 13, "fill did not come out of stock"
        assert before and after and before["units"] == after["units"] and \
            abs(before["expected"] - after["expected"]) < 0.01, \
            "filling the pillbox changed the order: %s -> %s" % (before, after)
        c.post("/api/stock/fill/undo", json={})
        return "expected on the 1st %.1f before and after a 7-day fill" % after["expected"]
    check("04 filling the pillbox does not change the order", t04)

    def t05():
        # v3.20.0: an SOS medicine rides its own running-low list, not the
        # monthly order, and is judged on stock now against its keep figure.
        count_yesterday(ctx["X"], 3)
        pack(ctx["X"], 15, "strip", keep=15)
        j = order()
        assert line(j, ctx["X"]) is None, "an SOS medicine rode the monthly order"
        l = [x for x in j["sos"]["lines"] if x["med_id"] == ctx["X"]]
        assert l and l[0]["basis"] == "keep" and l[0]["keep"] == 15, "keep-on-hand not used: %s" % l
        assert l[0]["packs"] == 1 and l[0]["qty"] == "1 strip of 15", l
        assert stock(ctx["X"])["keep_units"] == 15 and stock(ctx["X"])["pack_type"] == "strip"
        return "SOS medicine, 3 on hand, keep 15 -> 1 strip of 15 on the running-low list"
    check("05 an SOS medicine is topped up to its keep-on-hand figure", t05)

    def t06():
        count_yesterday(ctx["Y"], 8)
        for _ in range(7):
            c.post("/api/now/dose", json={"med_id": ctx["Y"], "status": "EXTRA",
                                          "day": TODAY, "dtime": "00:00"})
        j = order()
        names = dict((m["id"], m["name"]) for m in meds)
        assert line(j, ctx["Y"]) is None, "an unscheduled medicine rode the monthly order"
        assert not [x for x in j["sos"]["lines"] if x["med_id"] == ctx["Y"]], "ordered with no keep figure"
        assert names[ctx["Y"]] in j["sos"]["nokeep"], "not named as needing a keep figure"
        return "used but unscheduled, no keep -> not ordered, named for a keep figure"
    check("06 a medicine with no schedule and no keep is not ordered, and says so", t06)

    def t07():
        sk = dict((s["name"], s["why"]) for s in order()["skipped"])
        names = dict((m["id"], m["name"]) for m in meds)
        assert "not counted" in sk.get(names[ctx["Z"]], ""), "uncounted medicine not reported: %s" % sk
        assert "strengths vary" in sk.get(names[ctx["V"]], ""), "variant medicine not reported: %s" % sk
        return "uncounted and variant medicines are named, not silently left out"
    check("07 what cannot be ordered is named with the reason", t07)

    def t08():
        count_yesterday(ctx["E"], 5)
        q("UPDATE med_schedule SET valid_to=? WHERE med_id=?",
          ((FIRST - timedelta(days=1)).isoformat(), ctx["E"]))
        j = order()
        names = dict((m["id"], m["name"]) for m in meds)
        assert line(j, ctx["E"]) is None, "a medicine that stops this month was ordered"
        assert "schedule ends" in dict((s["name"], s["why"]) for s in j["skipped"]).get(names[ctx["E"]], "")
        return "a schedule ending before the 1st is not ordered, and says why"
    check("08 a medicine stopping before the 1st is not ordered", t08)

    def t09():
        count_yesterday(ctx["A"], 10)
        r = c.post("/api/order/save", json={})
        assert r.status_code == 200, r.get_data(as_text=True)
        oid = r.get_json()["id"]
        r2 = c.post("/api/order/save", json={})
        assert r2.get_json()["id"] == oid, "sending again made a second order"
        assert q("SELECT COUNT(*) FROM stock_orders")[0][0] == 1
        j = order()
        assert j["saved"] and j["saved"]["status"] == "OPEN" and not j["due"], j["saved"]
        ctx["oid"], ctx["lineA"] = oid, line(j, ctx["A"])
        return "one order per month; a saved order clears the due flag"
    check("09 sending saves the order once for the month", t09)

    def t10():
        a0 = stock(ctx["A"])["current"]
        r = c.post("/api/order/received", json={"id": ctx["oid"]})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert stock(ctx["A"])["current"] == a0 + ctx["lineA"]["units"], "received did not add to stock"
        assert c.post("/api/order/received", json={"id": ctx["oid"]}).status_code == 400, "received twice"
        assert c.post("/api/order/save", json={}).status_code == 400, "a received month was overwritten"
        c.post("/api/order/received/undo", json={"id": ctx["oid"]})
        assert stock(ctx["A"])["current"] == a0, "undo left stock behind"
        assert order()["saved"]["status"] == "OPEN"
        return "received adds every line once; undo takes it all back"
    check("10 order received adds to stock, once, and undoes cleanly", t10)

    def t11():
        assert c.post("/api/order/days", json={"days": 50}).status_code == 200
        l = line(order(), ctx["A"])
        assert l and l["target"] == 50, "target did not follow the setting: %s" % l
        c.post("/api/order/days", json={"days": 40})
        return "setting 50 days moves the target to 50"
    check("11 the 40 days is a setting", t11)

    def t12():
        with app.test_request_context():
            mod.db()
            p = mod._order_plan
            res = [(d, p(d)["last_week"]) for d in
                   (date(2026, 9, 24), date(2026, 9, 23), date(2026, 9, 30),
                    date(2027, 2, 22), date(2027, 2, 21), date(2026, 12, 25))]
        want = [True, False, True, True, False, True]
        got = [x[1] for x in res]
        assert got == want, "last-week rule wrong: %s" % got
        with app.test_request_context():
            mod.db()
            assert mod._order_plan(date(2026, 12, 25))["label"] == "January 2027"
        return "last 7 days of 30-, 28- and 31-day months; December orders January"
    check("12 the due window is the last seven days of the month", t12)

    def t13():
        anon = app.test_client()
        for u in ("/api/order",):
            r = anon.get(u)
            assert r.status_code in (301, 302, 401), "%s answered without login: %d" % (u, r.status_code)
        for u in ("/api/order/save", "/api/order/received", "/api/order/days", "/api/stock/pack"):
            r = anon.post(u, json={})
            assert r.status_code in (301, 302, 401), "%s answered without login: %d" % (u, r.status_code)
        return "every order route needs the login"
    check("13 the order routes refuse without a login", t13)

    def t14():
        ast.parse(src, feature_version=(3, 9))
        i = src.index("/* ---------- MONTHLY ORDER (GUTLOG_V3190_ORDER)")
        j = src.index("/* ---------- VITALS LOG", i)
        blk = src[i:j]
        for t in ("{{", "{%", "{#"):
            assert t not in blk, "Jinja token " + t + " in order JS"
        assert blk.count("{") == blk.count("}") and blk.count("(") == blk.count(")"), "unbalanced order JS"
        html = c.get("/").get_data(as_text=True)
        for ident in ('id="ordCard"', 'id="nowOrder"', 'id="ordSend"', 'id="ordRecv"', 'id="ordDays"'):
            assert ident in html, "page lacks " + ident
        return "parses as 3.9; order JS Jinja-clean and balanced; page renders the card and the banner box"
    check("14 Python 3.9 syntax, Jinja-clean JS, page renders the order card", t14)

    ok = all(r[0] for r in RESULTS)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

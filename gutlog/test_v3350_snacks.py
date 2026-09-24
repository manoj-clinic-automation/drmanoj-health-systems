#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.35.0 -- meals and snacks (GUTLOG_V3350_SNACKS).

The engine through the API: the slot guess, the grouped picker, a new food by
dry weight, dishes from their parts, one-tap late snacks, the Quick Bite, the
weekly review on fixed synthetic weeks (the server's clock set), the nudge,
the Food Test lines and the Health Mirror. The page in Chromium at 300 px,
because the sheet, the buttons, the picker and the review card are drawn by
the page's own JavaScript.

Scratch database, synthetic foods ("Test millet") alongside the stock food
list, a synthetic plan ("Test chana"). No medicine is named. Python 3.9.

  python3 test_v3350_snacks.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

RESULTS = []
B = {}
PLAN = {"plan_id": None, "reminder": "Test reminder.",
        "items": [{"slug": "w0", "week": "Week 0", "label": "Dinner changes only", "kind": "dinner", "days": 7},
                  {"slug": "tchana", "week": "Week 1", "label": "Test chana", "meal": "Breakfast",
                   "basis": "dry", "steps": [{"g": 12}, {"g": 25}], "washout": 2}]}
CARDS = {"cards": [{"name": "Dinner", "from": "19:00",
                    "rows": [{"kind": "fixed", "label": "Test dal", "text": "Test dal",
                              "items": [["Test dal", 1]]}]}]}
SLOTS = ["Breakfast", "Mid-morning", "Lunch", "Evening", "Dinner", "Late snack"]


class FakeDate(date):
    TODAY = date(2026, 10, 5)

    @classmethod
    def today(cls):
        return cls.TODAY


class FakeDateTime(datetime):
    NOW = datetime(2026, 10, 5, 16, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.NOW


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
    w = tempfile.mkdtemp(prefix="gutlog_snk_")
    cards = os.path.join(w, "cards.json")
    with open(cards, "w", encoding="utf-8") as fh:
        json.dump(CARDS, fh)
    os.environ.update(GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=os.path.join(w, "pl"), GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=cards,
                      GUTLOG_PLAN_FILE=os.path.join(w, "none.json"),
                      GUTLOG_MIRROR_STAMP=os.path.join(w, "stamp.json"))
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_v3350", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    real_date, real_dt = gm.date, gm.datetime
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"}, follow_redirects=True)
    c.get("/")
    gdb = os.environ["GUTLOG_DB"]
    NOW = datetime.now().strftime("%H:%M")

    def q(sql, a=()):
        con = sqlite3.connect(gdb)
        con.row_factory = sqlite3.Row
        r = con.execute(sql, a).fetchall()
        con.commit()
        con.close()
        return r

    def j(resp):
        return json.loads(resp.get_data(as_text=True))

    def D(n):
        return (date.today() - timedelta(days=n)).isoformat()

    def meal(day, mt, slot, items=None, kcal=500):
        body = {"day": day, "mtime": mt, "notes": "",
                "items": items or [{"n": "Test rice", "q": 1, "p": 2, "k": kcal, "f": 1, "fm": "L"}]}
        if slot:
            body["slot"] = slot
        r = c.post("/api/meals", json=body)
        assert r.status_code == 200, r.get_data(as_text=True)
        return q("SELECT * FROM meals ORDER BY id DESC LIMIT 1")[0]

    def fake(on, today=None, now=None):
        if on:
            FakeDate.TODAY = today or FakeDate.TODAY
            FakeDateTime.NOW = now or FakeDateTime.NOW
            gm.date, gm.datetime = FakeDate, FakeDateTime
        else:
            gm.date, gm.datetime = real_date, real_dt

    def sql_meal(day, mt, slot, items, reason=""):
        k = sum(float(i.get("q", 1)) * float(i.get("k", 0)) for i in items)
        p = sum(float(i.get("q", 1)) * float(i.get("p", 0)) for i in items)
        q("INSERT INTO meals(day, mtime, slot, items, protein, kcal, fibre, fscore, notes, created, reason) "
          "VALUES(?,?,?,?,?,?,0,0,'','',?)", (day, mt, slot, json.dumps(items), p, k, reason))

    seed = {}
    try:
        mig = os.path.join(os.path.dirname(app_path), "migrate_gutlog_v3350_snacks.py")
        ms = importlib.util.spec_from_file_location("mig_v3350", mig)
        mm = importlib.util.module_from_spec(ms)
        ms.loader.exec_module(mm)
        seed = mm.run(gm, True, w)
    except Exception as exc:
        seed = {"error": repr(exc)}
    if hasattr(gm, "ft_seed"):
        with gm.app.app_context():
            gm.ft_seed(gm.db(), PLAN)
    q("INSERT OR REPLACE INTO settings(key, value) VALUES('ft_week0_from', ?)", (D(30),))

    # ---------------------------------------------------------------- 01
    def t01():
        def guess(day, t):
            return j(c.get("/api/meals/slotguess?day=%s&time=%s" % (day, t)))["slot"]
        got = [(t, guess(D(3), t)) for t in ("09:59", "10:00", "11:59", "12:00", "15:29", "15:30",
                                             "17:59", "18:00", "23:00")]
        want = [("09:59", "Breakfast"), ("10:00", "Mid-morning"), ("11:59", "Mid-morning"), ("12:00", "Lunch"),
                ("15:29", "Lunch"), ("15:30", "Evening"), ("17:59", "Evening"), ("18:00", "Dinner"),
                ("23:00", "Dinner")]
        assert got == want, "by the clock: %r" % got
        meal(D(3), "19:30", "Dinner")
        assert guess(D(3), "21:00") == "Late snack", "after today's Dinner it is not a Late snack"
        assert guess(D(3), "19:00") == "Dinner", "before the Dinner it should still be Dinner"
        r = meal(D(3), "21:40", None, kcal=120)
        assert r["slot"] == "Late snack", "an entry after Dinner was filed as %r" % r["slot"]
        meal(D(4), "17:00", "Snack", kcal=200)
        t4 = j(c.get("/api/nutrition/day/" + D(4)))
        assert t4["kcal"] == 200 and [m["slot"] for m in t4["rows"]] == ["Snack"], t4
        dv = [e for e in j(c.get("/api/dayview?day=" + D(4)))["entries"] if e["tbl"] == "meals"]
        assert dv and dv[0]["title"] == "Snack", "an old Snack row no longer shows as such: %r" % dv
        return "clock slots right; after Dinner -> Late snack (API and a save with no slot); old Snack totals"
    check("01 the slot is guessed from the clock and from what is logged; old Snack rows still show", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        cols = [r[1] for r in q("PRAGMA table_info(meals)")]
        assert "reason" in cols, "meals.reason missing: %r" % cols
        names = [r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]
        assert "dishes" in names and "snack_marks" in names, names
        sv = q("SELECT value FROM settings WHERE key='schema_version'")[0][0]
        assert sv == "3.3.7", "schema_version %s" % sv
        h = c.get("/").get_data(as_text=True)
        assert "__MEAL_CFG__" not in h and "const MEALCFG=" in h, "the lists are not in the page"
        cfg = json.loads(h.split("const MEALCFG=", 1)[1].split(";\n", 1)[0])
        assert cfg[0] == SLOTS and cfg[1] == ["Eating out"] and cfg[2] == "Quick bite", cfg[:3]
        assert not seed.get("error"), "the seed failed: %r" % seed
        return "reason column, two tables, schema 3.3.7, one slot list in the page"
    check("02 schema 3.3.7 and the one slot list in the page", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        meal(D(2), "11:00", "Mid-morning", items=[{"n": "Kiwi", "q": 1, "p": 0.8, "k": 45, "f": 2, "fm": "L"}])
        fr = j(c.get("/api/foods/picker?group=Fruit"))["foods"]
        assert fr and all(f["group"] == "Fruit" for f in fr), "not all Fruit: %r" % [(f["n"], f["group"]) for f in fr]
        names = [f["n"] for f in fr]
        favs = [f["n"] for f in fr if f["fav"]]
        assert names[:len(favs)] == sorted(favs, key=str.lower), "favourites not first A-Z: %r" % names[:5]
        assert names[len(favs)] == "Kiwi", "the recently used food is not next: %r" % names[:6]
        rest = [f["n"] for f in fr[len(favs) + 1:] if not f["recent"]]
        assert rest == sorted(rest, key=str.lower), "the rest is not A-Z: %r" % rest
        pr = [f["n"] for f in j(c.get("/api/foods/picker?group=Protein"))["foods"]]
        assert "Paneer 50 g" in pr and "Curd / dahi" not in pr, "the group mapping is wrong: %r" % pr
        s = j(c.get("/api/foods/picker?group=Dal&q=banana"))["foods"]
        assert {"Fruit", "Sabzi"} <= set(f["group"] for f in s), "search does not span groups: %r" % \
            [(f["n"], f["group"]) for f in s]
        sb = j(c.get("/api/foods/picker?group=Sabzi"))["dishes"]
        assert "Tinda" in [d["name"] for d in sb] and len(sb) == 11, "dishes under Sabzi: %r" % [d["name"] for d in sb]
        return "favourites A-Z, then Kiwi (recent), then A-Z; paneer is Protein; search spans groups; 11 dishes"
    check("03 the picker: groups, favourites then recent then A-Z, search across groups", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        body = {"item": "Test millet (dry)", "cat": "A", "portion": "40 g", "portion_qty": 40, "portion_unit": "g",
                "weighed_dry": True, "fdc": None, "b_protein": 11, "b_kcal": 378, "b_fibre": 8.5, "fodmap": "L",
                "tags": "group:Grain, own entry", "note": "Your own entry, added test."}
        r = c.post("/api/library", json=body)
        assert j(r).get("ok"), r.get_data(as_text=True)
        row = q("SELECT * FROM library WHERE item='Test millet (dry)'")[0]
        assert (row["weighed_dry"], row["b_protein"], row["source"]) == (1, 11, "own"), dict(row)
        gr = [f["n"] for f in j(c.get("/api/foods/picker?group=Grain"))["foods"]]
        assert "Test millet (dry)" in gr, "the picked group was not kept"
        m = meal(D(1), "08:00", "Breakfast", items=[{"n": "Test millet (dry)", "g": 60}])
        assert (m["protein"], m["kcal"]) == (6.6, 227), "60 g dry totals %s g, %s kcal" % (m["protein"], m["kcal"])
        r = c.post("/api/library", json=dict(body, item="Test almonds", portion="6 g", portion_qty=6, weighed_dry=False,
                                             fdc=170567, b_protein=21.15, b_kcal=579, b_fibre=12.5,
                                             tags="group:Nuts & seeds, own entry"))
        src = q("SELECT source FROM library WHERE item='Test almonds'")[0][0]
        assert src == "USDA", "a table match is not marked as the table's: %s" % src
        # Sweets shares category F with Snacks, so only the group he picked
        # can put this food under Sweets -- the form's tag must be read.
        r = c.post("/api/library", json=dict(body, item="Test gajak", cat="F", portion="1 piece (~20 g)",
                                             portion_qty=20, weighed_dry=False, tags="group:Sweets, own entry"))
        sw = [f["n"] for f in j(c.get("/api/foods/picker?group=Sweets"))["foods"]]
        assert "Test gajak" in sw, "a food he put under Sweets is not there: %r" % sw
        return "dry weight kept; own entry; group Grain; 60 g dry -> 6.6 g protein, 227 kcal; table match -> USDA"
    check("04 a new food with its dry weight saves and totals by grams", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        ds = j(c.get("/api/dishes"))["dishes"]
        t = [d for d in ds if d["name"] == "Tinda"][0]
        assert [v["label"] for v in t["variants"]] == ["Tinda", "Tinda + paneer"], t["variants"]
        lt = q("SELECT b_protein, b_kcal FROM library WHERE item='Tinda'")[0]
        lp = q("SELECT b_protein, b_kcal FROM library WHERE item='Paneer 50 g'")[0]
        want_k = lt["b_kcal"] * 105 / 100 + lp["b_kcal"] * 45 / 100
        want_p = lt["b_protein"] * 105 / 100 + lp["b_protein"] * 45 / 100
        it = j(c.get("/api/dishes/item?id=%d&v=1&g=150" % t["id"]))["item"]
        assert abs(it["k"] - want_k) < 0.2 and abs(it["p"] - want_p) < 0.05, "1 katori: %r vs %.1f kcal" % (it, want_k)
        m = meal(D(5), "13:00", "Lunch", items=[{"dish": t["id"], "v": 1, "g": 150}])
        assert m["kcal"] == round(want_k), "the logged dish is %s kcal" % m["kcal"]
        r = c.post("/api/dishes/%d" % t["id"], json={"variants": [
            {"name": "plain", "label": "Tinda", "parts": [["Tinda", 100]]},
            {"name": "+ paneer", "label": "Tinda + paneer", "parts": [["Tinda", 50], ["Paneer 50 g", 50]]}]})
        assert j(r).get("ok"), r.get_data(as_text=True)
        it2 = j(c.get("/api/dishes/item?id=%d&v=1&g=150" % t["id"]))["item"]
        want2 = lt["b_kcal"] * 75 / 100 + lp["b_kcal"] * 75 / 100
        assert abs(it2["k"] - want2) < 0.2 and abs(it2["k"] - it["k"]) > 5, "50/50 did not change it: %r" % it2
        r = c.post("/api/dishes/%d" % t["id"], json={"variants": [
            {"name": "plain", "parts": [["Tinda", 60], ["Paneer 50 g", 30]]}]})
        assert r.status_code == 400, "shares adding to 90 % were accepted"
        r = c.post("/api/mealcards/log", json={"card": "", "slot": "Lunch", "day": D(6), "mtime": "13:00",
                                               "extra": [{"n": "Tinda + paneer", "dish": t["id"], "v": 1, "g": 75}]})
        assert j(r).get("ok"), r.get_data(as_text=True)
        k6 = q("SELECT kcal FROM meals WHERE day=?", (D(6),))[0][0]
        assert abs(k6 - round(want2 / 2)) <= 1, "half a katori on the card path: %s" % k6
        return "1 katori = 105 g tinda + 45 g paneer = %.0f kcal; 50/50 -> %.0f; bad shares refused" % (want_k, want2)
    check("05 a dish variant is worked out from its parts, and a new proportion changes it", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        bj = j(c.get("/api/snacks/buttons"))
        assert len(bj["late"]) == 11 and all(b["payload"] for b in bj["late"]), \
            "buttons without a food: %r" % [b["key"] for b in bj["late"] if not b["payload"]]
        assert "night tablet" in bj["note"] and "21:00" in bj["note"], bj["note"]
        milk = q("SELECT source, portion_qty, portion_unit FROM library WHERE item='Skimmed milk (warm)'")
        assert milk and tuple(milk[0]) == ("USDA", 200, "ml"), "milk was not added from the table: %r" % \
            [tuple(x) for x in milk]
        before = q("SELECT COUNT(*) FROM meals")[0][0]
        r = c.post("/api/snacks/late", json={"key": "milk", "day": D(0), "mtime": NOW})
        assert j(r).get("ok"), r.get_data(as_text=True)
        row = q("SELECT slot, items, kcal FROM meals ORDER BY id DESC LIMIT 1")[0]
        it = json.loads(row["items"])
        assert row["slot"] == "Late snack" and it[0]["n"] == "Skimmed milk (warm)" and it[0]["g"] == 200, dict(row)
        assert row["kcal"] == 68 and q("SELECT COUNT(*) FROM meals")[0][0] == before + 1, row["kcal"]
        return "11 buttons, each with a food; the note; one tap -> 200 ml skimmed milk, 68 kcal, Late snack"
    check("06 a late-snack button logs its default portion in one tap", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        r = c.post("/api/quickbite", json={"items": [{"n": "Namkeen (30 g)"}], "amount": "lot",
                                           "reason": "bored", "day": D(1), "mtime": "16:40"})
        assert j(r).get("ok"), r.get_data(as_text=True)
        row = q("SELECT * FROM meals ORDER BY id DESC LIMIT 1")[0]
        it = json.loads(row["items"])
        assert (row["slot"], row["reason"], row["mtime"], it[0]["q"], row["kcal"]) == \
            ("Quick bite", "bored", "16:40", 1.5, 240), dict(row)
        t = j(c.get("/api/nutrition/day/" + D(1)))
        total = q("SELECT SUM(kcal) FROM meals WHERE day=?", (D(1),))[0][0]
        assert t["kcal"] == round(total) and t["bites"] == {"n": 1, "kcal": 240, "protein": 6.0}, t
        assert "Quick bite 1 · 240 kcal" in t["snack_text"], t["snack_text"]
        h = c.get("/nutrition").get_data(as_text=True)
        assert "Quick bite 1 · 240 kcal" in h, "the history page does not say it apart"
        dv = [e for e in j(c.get("/api/dayview?day=" + D(1)))["entries"] if e["tbl"] == "meals"]
        qb = [e for e in dv if e["title"] == "Quick bite"]
        assert qb and qb[0]["time"] == "16:40" and qb[0]["sub"].startswith("why: bored"), dv
        assert c.post("/api/quickbite", json={"items": [], "amount": "normal"}).status_code == 400
        return "what, 1.5 x, bored, 16:40 -> 240 kcal inside the day and on its own line; Day by day at 16:40"
    check("07 a Quick bite keeps what, how much, why and when, and shows apart in totals and Day by day", t07)

    # ---------------------------------------------------------------- 08
    NK = {"n": "Namkeen (30 g)", "q": 1, "p": 4, "k": 160, "f": 2, "fm": "M"}
    BS = {"n": "Parle-G Gold", "q": 1, "p": 1.2, "k": 85, "f": 0.3, "fm": "M"}
    GV = {"n": "Guava (ripe)", "q": 0.5, "p": 1, "k": 55, "f": 5.5, "fm": "L"}

    def t08():
        for day, t, why in (("2026-09-28", "16:10", "bored"), ("2026-09-29", "16:40", "bored"),
                            ("2026-09-30", "17:05", "bored"), ("2026-10-01", "17:20", "stressed")):
            sql_meal(day, t, "Quick bite", [NK], why)
        sql_meal("2026-10-02", "11:00", "Quick bite", [BS], "hungry")
        sql_meal("2026-10-03", "21:30", "Late snack", [GV])
        sql_meal("2026-10-01", "13:00", "Lunch", [{"n": "Test rice", "q": 1, "p": 2, "k": 600, "f": 1, "fm": "L"}])
        sql_meal("2026-09-27", "16:20", "Quick bite", [NK], "bored")    # the Sunday before: not this week
        sql_meal("2026-10-05", "16:20", "Quick bite", [NK], "habit")    # today, Monday: next week
        fake(True, date(2026, 10, 5))
        try:
            r = j(c.get("/api/snacks/review"))
            r7 = j(c.get("/api/snacks/review?mode=7d"))
            c.post("/api/snacks/mark", json={"week_start": "2026-09-28", "item": "Namkeen (30 g)", "mark": "Swap"})
            r2 = j(c.get("/api/snacks/review"))
            fake(True, date(2026, 10, 12))
            sql_meal("2026-10-08", "11:00", "Quick bite", [BS], "hungry")
            nx = j(c.get("/api/snacks/review"))
        finally:
            fake(False)
        assert (r["from"], r["to"], r["bites"], r["late"]) == ("2026-09-28", "2026-10-04", 5, 1), \
            "week %s..%s, %s bites, %s late" % (r["from"], r["to"], r["bites"], r["late"])
        assert r["band"] == {"n": 4, "of": 6, "from": "16:00", "to": "17:30"}, r["band"]
        assert r["reasons"] == [["bored", 3], ["hungry", 1], ["stressed", 1]], r["reasons"]
        top = r["top"][0]
        assert (top["item"], top["times"], top["kcal"], top["share"]) == ("Namkeen (30 g)", 4, 640, 47), top
        assert [(s["item"], s["swap"]) for s in r["swaps"]] == [("Namkeen (30 g)", "makhana + mattha")], r["swaps"]
        assert (r7["from"], r7["to"]) == ("2026-09-29", "2026-10-05"), (r7["from"], r7["to"])
        assert r2["swaps"][0]["mark"] == "Swap", "the mark did not save"
        assert (nx["from"], nx["followup"]) == ("2026-10-05", [{"item": "Namkeen (30 g)", "mark": "Swap",
                                                                "before": 4, "after": 1}]), nx["followup"]
        return "Mon 28-Sep..Sun 04-Oct: 5 bites + 1 late, 4 of 6 in 16:00-17:30, bored 3; namkeen 47 % -> " \
               "makhana + mattha; Swap -> next week 4 -> 1"
    check("08 the weekly review counts the right week, band, reasons, top items and swaps; marks carry on", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        sql_meal("2026-11-10", "15:10", "Quick bite", [NK], "bored")
        sql_meal("2026-11-11", "15:20", "Quick bite", [NK], "bored")
        with gm.app.app_context():
            two = gm.snack_nudge("2026-11-14", "15:15")          # the week before holds only 2 in the band
            yes = gm.snack_nudge("2026-10-05", "16:30")
            out = gm.snack_nudge("2026-10-05", "18:00")
        assert not two["show"], "a band with 2 quick bites nudged: %r" % two
        assert yes["show"] and yes["text"] == "Usual snack time — planned swap: makhana + mattha.", yes
        assert not out["show"], "the nudge shows outside the band"
        fake(True, date(2026, 10, 5), datetime(2026, 10, 5, 16, 30))
        try:
            a = j(c.get("/api/snacks/nudge"))
            c.post("/api/snacks/nudge", json={})
            b = j(c.get("/api/snacks/nudge"))
        finally:
            fake(False)
        assert a["show"] and not b["show"], "dismiss did not hide it for the day: %r %r" % (a, b)
        return "4 in 16:00-17:30 last week -> the line at 16:30, not at 18:00, not with 2; dismissed for the day"
    check("09 the nudge shows only in a band with 3 or more quick bites last week, and hides when dismissed", t09)

    # ---------------------------------------------------------------- 10
    def t10():
        meal(D(1), "20:00", "Dinner")
        h = c.get("/foodtest").get_data(as_text=True)
        assert "Quick bite 16:40: Namkeen (30 g) (bored)" in h, "the Food Test day does not list the quick bite"
        return "the day's quick bite is on its own line under the Week 0 day"
    check("10 Food Test results list the day's late snacks and quick bites", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        mirror = os.path.join(os.path.dirname(os.path.dirname(app_path)), "ops", "health_mirror.py")
        if not os.path.exists(mirror):
            mirror = "/root/ops/health_mirror.py"
        os.environ.update(MIRROR_GUTLOG_DB=gdb, MIRROR_FITLOG_DB=os.path.join(w, "nofit.db"),
                          MIRROR_PLANS_DIR=os.path.join(w, "pl"), MIRROR_UPLOAD_DIR=os.path.join(w, "up"))
        spec2 = importlib.util.spec_from_file_location("hm_v3350", mirror)
        hm = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(hm)
        out = os.path.join(w, "mirror")
        counts, md = hm.generate(out, days=30)
        rows = open(os.path.join(out, "snapshot", "csv", "meal_rows.csv"), encoding="utf-8").read()
        head = rows.split("\n")[0]
        assert "slot" in head and "reason" in head, head
        assert "Quick bite" in rows and "bored" in rows, "the Quick bite and its reason are not in the CSV"
        mk = open(os.path.join(out, "snapshot", "csv", "snack_marks.csv"), encoding="utf-8").read()
        assert "2026-09-28" in mk and "Namkeen (30 g)" in mk and "Swap" in mk, mk[:200]
        assert "Quick bite" in md and "Swap" in md, "the snapshot does not carry them"
        return "meal_rows.csv (slot, reason) and snack_marks.csv, and both in the snapshot"
    check("11 the Health Mirror carries slot, the Quick-bite reason and the weekly marks", t11)

    # ------------------------------------------------ the browser checks
    try:
        from playwright.sync_api import sync_playwright
        have_pw = True
    except ImportError:
        have_pw = False

    def browse():
        if B or not have_pw:
            return
        B["done"] = True
        q("DELETE FROM meals WHERE day=?", (D(0),))
        meal(D(0), "00:00", "Dinner")                  # so the slot guess is Late snack now
        wk_end = date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
        for n in (5, 4):
            sql_meal((wk_end - timedelta(days=n)).isoformat(), "16:15", "Quick bite", [NK], "bored")
        from werkzeug.serving import make_server
        srv = make_server("127.0.0.1", 0, gm.app)
        port = srv.server_port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 300, "height": 760})
            pg.set_default_timeout(6000)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/login" % port)
            pg.fill("input[type=password]", "testpassword1")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1300)

            def tap(sel):
                pg.wait_for_selector(sel, state="attached")
                pg.eval_on_selector(sel, "e=>{e.scrollIntoView({block:'center'});e.click();}")
                pg.wait_for_timeout(600)

            def wide():
                return pg.evaluate("document.documentElement.scrollWidth")

            def step(name, fn):
                try:
                    fn()
                except Exception as exc:
                    B["err_" + name] = type(exc).__name__ + ": " + str(exc).split("\n")[0]

            def s_qb():
                B["qb_top"] = pg.evaluate(
                    "(()=>{const a=document.getElementById('qbBtn'),d=document.getElementById('nowDoses');"
                    "return !!a&&a.getBoundingClientRect().top<d.getBoundingClientRect().top;})()")
                tap("#qbBtn")
                tap("#qbWhat .chip[data-key=namkeen]")
                tap("#qbAmt .chip[data-v=little]")
                tap("#qbWhy .chip[data-v=habit]")
                pg.select_option("#qbSheet select.tph", NOW[:2])
                pg.select_option("#qbSheet select.tpm", "00")
                B["qb_wide"] = wide()
                tap("#qbSave")
                pg.wait_for_timeout(600)
                B["qb_row"] = [tuple(r) for r in q(
                    "SELECT slot, reason, mtime, items FROM meals WHERE day=? AND slot='Quick bite'", (D(0),))]

            def s_late():
                tap("#nowMeal .fold-h")
                B["late_btns"] = pg.eval_on_selector_all("#lateSnacks .chip", "l=>l.map(e=>e.dataset.key)")
                B["late_note"] = pg.inner_text("#lateSnacks .lsnote")
                B["late_slot"] = pg.eval_on_selector_all("#mealBody .chip.sel", "l=>l.map(e=>e.textContent)")
                tap("#lateSnacks .chip[data-key=kiwi]")
                pg.wait_for_timeout(600)
                B["late_row"] = [(r["slot"], json.loads(r["items"])[0]["n"]) for r in q(
                    "SELECT slot, items FROM meals WHERE day=? AND slot='Late snack'", (D(0),))]

            def s_pick():
                pg.locator("#mealBody button", has_text="+ Something else").first.click()
                pg.wait_for_timeout(700)
                B["groups"] = pg.eval_on_selector_all("#mealBody .fgroups .chip", "l=>l.map(e=>e.textContent)")
                tap("#mealBody .fgroups .chip[data-g=Sabzi]")
                pg.wait_for_timeout(500)
                pg.evaluate("[...document.querySelectorAll('#mealBody .fres .chip.dish')]"
                            ".find(e=>e.textContent.indexOf('Tinda')>=0).click()")
                pg.wait_for_timeout(500)
                pg.locator("#mealBody .dishpk .dvar .chip", has_text="Tinda + paneer").first.click()
                pg.wait_for_timeout(700)
                B["dish_prev"] = pg.inner_text("#mealBody .dishpk .dprev")
                B["pick_wide"] = wide()
                tap("#mealBody .dishadd")
                B["extra"] = pg.inner_text("#mealBody")
                pg.locator("#mealBody button", has_text="+ Something else").first.click()
                pg.wait_for_timeout(500)
                pg.fill("#mcQ", "zzqfood")
                pg.wait_for_timeout(900)
                tap("#mealBody .nfopen")
                B["nf"] = pg.evaluate("(()=>{const f=document.getElementById('nfForm');return f?{groups:"
                                      "f.querySelectorAll('#nf_group .chip').length,units:"
                                      "f.querySelectorAll('#nf_unit .chip').length,dry:"
                                      "f.querySelectorAll('#nf_dry .chip').length}:null;})()")
                B["nf_wide"] = wide()
                pg.evaluate("switchTab('meals')")
                pg.wait_for_timeout(1200)
                B["ml_slots"] = pg.eval_on_selector_all("#mlSlots .chip", "l=>l.map(e=>e.textContent)")
                B["ml_groups"] = pg.eval_on_selector_all("#ml_groups .chip", "l=>l.length")

            def s_review():
                B["rv_text"] = pg.inner_text("#snackReview")
                B["rv_wide"] = wide()
                tap("#snackReview .srswap .chip[data-v=Swap]")
                pg.wait_for_timeout(600)
                B["rv_mark"] = [tuple(r) for r in q("SELECT week_start, item, mark FROM snack_marks "
                                                    "WHERE week_start<>'2026-09-28'")]

            for name, fn in (("qb", s_qb), ("late", s_late), ("pick", s_pick), ("review", s_review)):
                step(name, fn)
            B["errs"] = errs
            B["wk_start"] = (wk_end - timedelta(days=6)).isoformat()
            B["wk_end"] = wk_end.isoformat()
            b.close()
        srv.shutdown()

    def need():
        if not have_pw:
            return False
        browse()
        return True

    def G(k, part):
        if k not in B:
            raise AssertionError("the %s step did not get this far: %s" % (part, B.get("err_" + part, "?")))
        return B[k]

    def t12():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert G("qb_top", "qb"), "the Quick Bite button is not above the doses"
        row = G("qb_row", "qb")
        assert len(row) == 1, "Quick bite rows today: %r" % row
        slot, reason, mt, items = row[0]
        it = json.loads(items)
        assert (slot, reason, mt, it[0]["n"], it[0]["q"]) == ("Quick bite", "habit", NOW[:2] + ":00",
                                                               "Namkeen (30 g)", 0.5), row
        assert B["qb_wide"] <= 300, "the sheet runs past 300 px (%s)" % B["qb_wide"]
        return "one sheet: namkeen, a little, habit, the time chosen -> one Quick bite row"
    check("12 the Quick Bite button is at the top and its sheet saves what, how much, why and time", t12)

    def t13():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert "Late snack" in G("late_slot", "late"), "the card did not open on Late snack: %r" % B["late_slot"]
        assert len(B["late_btns"]) == 11, "late-snack buttons: %r" % B["late_btns"]
        assert "night tablet" in B["late_note"], B["late_note"]
        assert G("late_row", "late") == [("Late snack", "Kiwi")], B["late_row"]
        return "after today's Dinner the card opens on Late snack; 11 buttons and the note; Kiwi in one tap"
    check("13 under Late snack the buttons log in one tap, with the note", t13)

    def t14():
        if not need():
            return "SKIPPED: Playwright not installed here"
        assert not B["errs"], "page errors: %s" % B["errs"]
        assert G("groups", "pick") == ["★ Mine"] + ["Dal", "Sabzi", "Protein", "Grain", "Fruit", "Dairy",
                                                   "Nuts & seeds", "Sweets", "Snacks"], B["groups"]
        assert "kcal" in G("dish_prev", "pick") and "from its parts" in B["dish_prev"], B["dish_prev"]
        assert "Tinda + paneer" in G("extra", "pick"), "the dish did not land in the meal"
        assert G("nf", "pick") == {"groups": 9, "units": 3, "dry": 2}, B["nf"]
        for k in ("pick_wide", "nf_wide"):
            assert B[k] <= 300, "%s is %spx" % (k, B[k])
        assert G("ml_slots", "pick") == SLOTS + ["Eating out"], B["ml_slots"]
        assert B["ml_groups"] == 10, "Meals tab group chips: %s" % B["ml_groups"]
        return "groups, a dish variant by katori, the new-food form, the Meals tab slots and groups -- at 300 px"
    check("14 the picker and the new-food form at 300 px, in the Now card and the Meals tab", t14)

    def t15():
        if not need():
            return "SKIPPED: Playwright not installed here"
        tx = G("rv_text", "review")
        n = q("SELECT COUNT(*) FROM meals WHERE slot='Quick bite' AND day BETWEEN ? AND ?",
              (B["wk_start"], B["wk_end"]))[0][0]
        nk = q("SELECT COUNT(*) FROM meals WHERE slot='Quick bite' AND day BETWEEN ? AND ? AND items LIKE "
               "'%Namkeen%'", (B["wk_start"], B["wk_end"]))[0][0]
        assert n >= 2 and ("Quick bites %d" % n) in tx, "expected %d quick bites: %s" % (n, tx)
        assert ("Namkeen (30 g) ×%d" % nk) in tx and "makhana + mattha" in tx, tx
        assert B["rv_wide"] <= 300, "the review runs past 300 px"
        assert G("rv_mark", "review") == [(B["wk_start"], "Namkeen (30 g)", "Swap")], B["rv_mark"]
        return "the card counts last week's quick bites, suggests the swap, and Swap saves for that week"
    check("15 the weekly review card renders on the Meals tab and Keep / Swap / Stop saves", t15)

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

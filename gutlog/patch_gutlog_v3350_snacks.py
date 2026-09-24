#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.34.0 -> v3.35.0  ::  GUTLOG_V3350_SNACKS -- meals and snacks.

WHY: his day is not three meals. A late snack after dinner was being filed
as dinner, random daytime snacks were not logged at all, the food list was one
long run of chips, and a combination sabzi could not be logged as he cooks it.
The point is to uncover the snacking and correct it -- by counts and his own
marks, never by a score.

WHAT
  * One slot list, MEAL_SLOTS (Breakfast, Mid-morning, Lunch, Evening, Dinner,
    Late snack; Eating out kept as a choice), written into the page, used by
    the Meals tab and the Now card. Old "Snack" rows keep their slot and show
    as they are. The slot is guessed: after the day's last Dinner it is a Late
    snack; otherwise by the clock (<10:00, 10-12, 12-15:30, 15:30-18, 18:00+).
    One tap changes it.
  * Day totals carry Late snack and Quick bites as their own lines (Meals tab,
    /nutrition, Day by day).
  * The picker in groups (Dal, Sabzi, Protein, Grain, Fruit, Dairy, Nuts &
    seeds, Sweets, Snacks) -- a mapping of library.cat plus name words, never a
    rename -- favourites first, then recently used, then A-Z; search spans all
    groups. The same picker in the Meals tab and the Now card.
  * A new food on the spot from the picker's not-found state: name, group, dry
    or cooked weight, portion (katori / piece / grams), values per 100 g from
    the bundled table when he picks a match, editable; his own entry, dated.
    Uses the v3.31.0 library fields.
  * Dishes: a base vegetable with variants (Tinda / + paneer ...), shares of
    the cooked katori, editable; logged as ½ / 1 / 1½ katori or grams and
    worked out from the components. One `dishes` table.
  * Late-snack buttons, one tap each, with a note that names no medicine.
  * Quick Bite: what / how much / why / when in one sheet, stored as a meal
    row with slot "Quick bite" and a new meals.reason column.
  * A weekly snack review on the Meals tab (Mon-Sun, or the last 7 days):
    counts, the busiest 90-minute band, reasons, top items with kcal and share,
    swap ideas from snack_swaps.json for anything eaten twice or more, his
    Keep / Swap / Stop marks, and next week's before -> after. A one-line nudge
    on the Now page during a band that held >= 3 quick bites last week,
    dismissible for the day.
  * Food Test results list late snacks and quick bites under each day.

Schema 3.3.6 -> 3.3.7 (meals.reason through _migrate() AND SCHEMA; dishes and
snack_marks in SCHEMA). The foods and dishes the buttons need are added by
migrate_gutlog_v3350_snacks.py. Anchor-verified, idempotent, compile-checked,
.bak, self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3350_SNACKS"
PREV = "GUTLOG_V3340_FTMEALS"
VERSION = "3.35.0"

E = []

E.append(("header",
          'GUTLOG_V3340_FTMEALS -- Week 0 reads dinner from Meals; the Now page folds and reorders.\n',
          'GUTLOG_V3340_FTMEALS -- Week 0 reads dinner from Meals; the Now page folds and reorders.\n'
          'GUTLOG_V3350_SNACKS -- slots, grouped picker, dishes, late snacks, Quick Bite, weekly review.\n'))
E.append(("version", 'APP_VERSION = "3.34.0"   # GUTLOG_V3340_FTMEALS ',
          'APP_VERSION = "3.35.0"   # GUTLOG_V3350_SNACKS GUTLOG_V3340_FTMEALS '))
E.append(("schema version", 'SCHEMA_VERSION = "3.3.6"   # GUTLOG_V3330_PAINSITE ',
          'SCHEMA_VERSION = "3.3.7"   # GUTLOG_V3350_SNACKS GUTLOG_V3330_PAINSITE '))
E.append(("meals schema",
          "  items TEXT, protein REAL, kcal REAL, fibre REAL, fscore REAL,\n  notes TEXT, created TEXT);\n",
          "  items TEXT, protein REAL, kcal REAL, fibre REAL, fscore REAL,\n  notes TEXT, created TEXT,\n"
          "  reason TEXT DEFAULT '');\n"))
E.append(("new tables",
          "CREATE TABLE IF NOT EXISTS meal_meta (\n"
          "  meal_id INTEGER PRIMARY KEY, card TEXT DEFAULT '', choices TEXT DEFAULT '{}',\n"
          "  onion INTEGER DEFAULT 0, extra TEXT DEFAULT '[]');\n",
          "CREATE TABLE IF NOT EXISTS meal_meta (\n"
          "  meal_id INTEGER PRIMARY KEY, card TEXT DEFAULT '', choices TEXT DEFAULT '{}',\n"
          "  onion INTEGER DEFAULT 0, extra TEXT DEFAULT '[]');\n"
          "CREATE TABLE IF NOT EXISTS dishes (\n"
          "  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, katori_g REAL DEFAULT 150,\n"
          "  variants TEXT DEFAULT '[]', updated TEXT);\n"
          "CREATE TABLE IF NOT EXISTS snack_marks (\n"
          "  week_start TEXT NOT NULL, item TEXT NOT NULL, mark TEXT NOT NULL, set_at TEXT,\n"
          "  PRIMARY KEY (week_start, item));\n"))
E.append(("reason column",
          '    ("ft_score", "onset", "TEXT DEFAULT \'\'"),\n]\n',
          '    ("ft_score", "onset", "TEXT DEFAULT \'\'"),\n'
          '    # GUTLOG_V3350_SNACKS -- why a Quick bite was eaten.\n'
          '    ("meals", "reason", "TEXT DEFAULT \'\'"),\n]\n'))

# ------------------------------------------------ meals: slot guess and dishes
E.append(("meals tab dish items",
          '    for it in items[:20]:\n        if it.get("g") not in (None, "", 0, "0"):\n',
          '    for it in items[:20]:\n'
          '        if it.get("dish"):   # GUTLOG_V3350_SNACKS -- worked out from its parts\n'
          '            w, miss = dish_item(it.get("dish"), it.get("v", 0), it.get("g"))\n'
          '            if not w:\n'
          '                return jsonify(ok=False, err="Not in the food list with a weight: "\n'
          '                               + ", ".join(miss)), 400\n'
          '            clean.append(w)\n'
          '            p += w["q"] * w["p"]; k += w["q"] * w["k"]; f += w["q"] * w["f"]\n'
          '            fs += w["q"] * FMAP[w["fm"]]\n'
          '            continue\n'
          '        if it.get("g") not in (None, "", 0, "0"):\n'))
E.append(("meals tab slot guess",
          '           [day, mtime, d.get("slot") or "Meal",\n',
          '           [day, mtime, d.get("slot") or meal_slot_guess(day, mtime),   # GUTLOG_V3350_SNACKS\n'))
E.append(("card extras dish",
          '''    extra = [(x.get("n"), x.get("q", 1), x.get("g")) for x in (d.get("extra") or [])
             if x.get("n")][:20]
    items, missing = _meal_items(pairs + extra, lib)
''',
          '''    extra = [(x.get("n"), x.get("q", 1), x.get("g")) for x in (d.get("extra") or [])
             if x.get("n") and not x.get("dish")][:20]
    items, missing = _meal_items(pairs + extra, lib)
    # GUTLOG_V3350_SNACKS -- a dish is worked out from its parts, here too.
    dish_x = []
    for x in [x for x in (d.get("extra") or []) if x.get("dish")][:10]:
        it, miss = dish_item(x.get("dish"), x.get("v", 0), x.get("g"), lib)
        if it:
            items.append(it)
            dish_x.append({"n": it["n"], "q": 1, "g": it["g"], "dish": it["dish"], "v": it["v"]})
        else:
            missing += miss
'''))
E.append(("card slot guess",
          '    slot = card_name or (d.get("slot") or "Meal")[:30]\n',
          '    slot = card_name or (d.get("slot") or meal_slot_guess(day, mtime))[:30]   # GUTLOG_V3350_SNACKS\n'))
E.append(("card meta dish",
          '                              for x in extra])))\n',
          '                              for x in extra] + dish_x)))\n'))
E.append(("mealcards guess",
          '                   protein_target=_protein_target())\n',
          '                   protein_target=_protein_target(),\n'
          '                   slot_guess=meal_slot_guess(day, now_hm()))   # GUTLOG_V3350_SNACKS\n'))

# ------------------------------------------------ totals with their own lines
E.append(("nut day open",
          '    return {"day": day, "date_text": plan_dmy(day), "meals": n,\n',
          '    t = {"day": day, "date_text": plan_dmy(day), "meals": n,\n'))
E.append(("nut day close",
          '            "logged": n > 0, "partial": 0 < n < usual, "usual_meals": usual}\n',
          '            "logged": n > 0, "partial": 0 < n < usual, "usual_meals": usual}\n'
          '    t.update(nut_snacks(day))   # GUTLOG_V3350_SNACKS -- inside the totals, and said apart\n'
          '    t["snack_text"] = snack_lines_text(t)\n'
          '    return t\n'))
E.append(("history snack line",
          '''            out.append('<span class="nv">%d meal%s</span>'
                       % (d["meals"], "" if d["meals"] == 1 else "s"))
''',
          '''            out.append('<span class="nv">%d meal%s</span>'
                       % (d["meals"], "" if d["meals"] == 1 else "s"))
            if d.get("snack_text"):   # GUTLOG_V3350_SNACKS
                out.append('<span class="nv snk">' + plan_esc(d["snack_text"]) + '</span>')
'''))
E.append(("dayview reason",
          '''            "SELECT id, mtime, slot, items, protein FROM meals WHERE day=?",
            (day,)).fetchall():
''',
          '''            "SELECT id, mtime, slot, items, protein, reason FROM meals WHERE day=?",
            (day,)).fetchall():
'''))
E.append(("dayview reason sub",
          '''        add("meals", r["id"], r["mtime"], "Meal", r["slot"] or "Meal", sub)
''',
          '''        if r["reason"]:   # GUTLOG_V3350_SNACKS -- why a Quick bite was eaten
            sub = "why: " + r["reason"] + (" · " + sub if sub else "")
        add("meals", r["id"], r["mtime"], "Meal", r["slot"] or "Meal", sub)
'''))
E.append(("ft results snacks",
          '''            for ln in ft_day_pain_lines(r["day"], r.get("score")):   # GUTLOG_V3330_PAINSITE
                out.append('<div class="rel">' + plan_esc(ln) + '</div>')
''',
          '''            for ln in ft_day_pain_lines(r["day"], r.get("score")):   # GUTLOG_V3330_PAINSITE
                out.append('<div class="rel">' + plan_esc(ln) + '</div>')
            for ln in ft_day_snack_lines(r["day"]):   # GUTLOG_V3350_SNACKS
                out.append('<div class="rel snk">' + plan_esc(ln) + '</div>')
'''))

CODE = r'''# ------------------------------------------------------------ meals and snacks
# GUTLOG_V3350_SNACKS. One slot list for every meal screen; a slot guessed from
# the clock and from what is already logged; foods arranged in groups; sabzi as
# dishes with variants; one-tap late snacks; a Quick Bite with its reason; and a
# weekly review that counts snacks, says when and why, and carries his own
# Keep / Swap / Stop marks forward. Counts and his marks only -- no scores.
MEAL_SLOTS = ["Breakfast", "Mid-morning", "Lunch", "Evening", "Dinner", "Late snack"]
MEAL_SLOT_EXTRA = ["Eating out"]          # kept as a choice, not a time slot
QUICK_BITE = "Quick bite"
LATE_SNACK = "Late snack"
SNACK_SLOTS = (QUICK_BITE, LATE_SNACK)
# before this time -> this slot; from 18:00 it is Dinner. Anything after the
# day's (last) Dinner is a Late snack, whatever the clock says.
MEAL_SLOT_BY_TIME = (("10:00", "Breakfast"), ("12:00", "Mid-morning"), ("15:30", "Lunch"),
                     ("18:00", "Evening"))
QB_REASONS = ["hungry", "bored", "stressed", "tired", "offered", "craving", "habit"]
QB_AMOUNTS = [["little", "a little", 0.5], ["normal", "normal", 1.0], ["lot", "a lot", 1.5]]
SNACK_BAND_MIN = 90          # a time band is 90 minutes, starting on the half hour
SNACK_NUDGE_MIN = 3          # quick bites in one band last week before the Now page says so
DISH_KATORI_G = 150.0        # one katori of dry sabzi, cooked; editable per dish

FOOD_GROUPS = ["Dal", "Sabzi", "Protein", "Grain", "Fruit", "Dairy", "Nuts & seeds", "Sweets", "Snacks"]
# His library's categories -> groups. A mapping, not a rename: library.cat is
# never changed. Words in the name are checked first; a tag "group:X" (set when
# he adds a food and picks its group) beats both.
FOOD_GROUP_BY_CAT = {"A": "Grain", "B": "Dal", "C": "Protein", "D": "Dairy", "E": "Sabzi",
                     "F": "Snacks", "G": "Fruit", "H": "Snacks"}
FOOD_GROUP_WORDS = (
    ("Protein", ("paneer", "egg", "fish", "chicken", "soya", "tofu")),
    ("Nuts & seeds", ("almond", "walnut", "peanut", "cashew", "pista", "nuts", "seed", "makhana")),
    ("Sweets", ("laddu", "chikki", "choc", "barfi", "halwa", "kheer", "mithai", "sweet", "jaggery")),
    ("Dairy", ("curd", "dahi", "milk", "mattha", "buttermilk", "lassi", "yogurt")),
)
FOOD_GROUP_CAT = {"Dal": "B", "Sabzi": "E", "Protein": "C", "Grain": "A", "Fruit": "G",
                  "Dairy": "D", "Nuts & seeds": "F", "Sweets": "F", "Snacks": "F"}

# Late-snack buttons: (key, label, library names to use -- the first that
# exists, grams when that food has a weight, portions when it has none, and
# the food to add from the table when none exists: name, fdc, grams, unit,
# fodmap, cat). The note under them names no medicine.
LATE_SNACK_BUTTONS = [
    ("milk", "Warm skimmed milk 200 ml", ["Skimmed milk (warm)"], 200, 1,
     ("Skimmed milk (warm)", 171269, 200, "ml", "M", "D")),
    ("orange", "Orange, 1 small", ["Orange / mausambi", "Orange"], 100, 0.75,
     ("Orange", 169097, 100, "g", "L", "G")),
    ("kiwi", "Kiwi, 1", ["Kiwi"], 75, 1, ("Kiwi", 168153, 75, "g", "L", "G")),
    ("guava", "Guava, ½", ["Guava (ripe)", "Guava"], 50, 0.5, ("Guava", 173044, 100, "g", "L", "G")),
    ("makhana", "Roasted makhana 15 g", ["Makhana"], 15, 0.5, None),
    ("peanuts", "Peanuts 10 g", ["Salted peanuts", "Peanuts, roasted"], 10, 0.33,
     ("Peanuts, roasted", 173806, 10, "g", "L", "C")),
    ("almonds", "Almonds, 5", ["Almonds (soaked)", "Almonds"], 6, 1, ("Almonds", 170567, 6, "g", "L", "F")),
    ("curd", "Curd, 1 small katori (100 g)", ["Curd / dahi", "Curd"], 100, 0.67,
     ("Curd", 171284, 100, "g", "L-M", "D")),
    ("paneer", "Paneer 30 g", ["Paneer 50 g", "Paneer"], 30, 0.6, None),
    ("cucumber", "Cucumber, 1", ["Cucumber"], 150, 1, ("Cucumber", 168409, 150, "g", "L", "E")),
    ("carrot", "Carrot, 1", ["Carrot (raw)", "Carrot"], 60, 1, ("Carrot (raw)", 170393, 60, "g", "L", "E")),
]
LATE_SNACK_NOTE = "Before 21:00, at least an hour before the night tablet."
# Quick Bite "what" buttons: a food, or a group to open. Estimated foods are
# added only when none of the names exists: (name, portion, grams, protein,
# kcal, fibre, fodmap, cat) for ONE portion, marked estimated.
QB_WHAT = [
    ("biscuit", "Biscuit", ["Parle-G Gold", "Biscuits (2 pieces)"],
     ("Biscuits (2 pieces)", "2 biscuits (~18 g)", 18, 1.2, 85, 0.3, "M", "A")),
    ("namkeen", "Namkeen", ["Namkeen (30 g)"], ("Namkeen (30 g)", "30 g", 30, 4, 160, 2, "M", "F")),
    ("chikki", "Chikki", ["Chikki (1 piece)"], ("Chikki (1 piece)", "1 piece (~25 g)", 25, 3, 120, 1, "L", "F")),
    ("laddu", "Laddu", ["Rajgira laddu", "Til laddu", "Laddu (1)"],
     ("Laddu (1)", "1 (~25 g)", 25, 2.5, 110, 1.5, "M", "F")),
    ("fruit", "Fruit", "group:Fruit", None),
    ("nuts", "Nuts", "group:Nuts & seeds", None),
    ("tea", "Tea with something", ["Tea (your cup)"], ("Tea (your cup)", "1 cup", 150, 1.5, 65, 0, "M", "H")),
]
# Dishes: combination dry sabzi. Shares are of the cooked katori and editable
# by him on the dish; these are only the first values.
DISH_SEED = [
    ("Tinda", [("plain", "Tinda", [("Tinda", 100)]),
               ("+ paneer", "Tinda + paneer", [("Tinda", 70), ("Paneer 50 g", 30)])]),
    ("Parwal", [("plain", "Parwal", [("Parval", 100)]),
                ("+ aloo", "Parwal + aloo", [("Parval", 60), ("Potato", 40)])]),
    ("Lauki", [("plain", "Lauki", [("Lauki", 100)]),
               ("+ paneer", "Lauki + paneer", [("Lauki", 70), ("Paneer 50 g", 30)])]),
    ("Aloo", [("plain", "Aloo", [("Potato", 100)])]),
    ("Arbi", [("plain", "Arbi", [("Arbi", 100)])]),
    ("Bhindi", [("plain", "Bhindi", [("Bhindi", 100)])]),
    ("Beans", [("plain", "Beans", [("French beans", 100)])]),
    ("Carrot-beans", [("plain", "Carrot-beans", [("Carrot", 50), ("French beans", 50)])]),
    ("Torai", [("plain", "Torai", [("Torai", 100)])]),
    ("Kaddu", [("plain", "Kaddu", [("Kaddu", 100)])]),
    ("Palak", [("plain", "Palak", [("Palak (spinach)", 100)])]),
]


def meal_slot_guess(day, hm):
    """The slot a new entry most likely is. After the day's last Dinner it is
    a Late snack -- never added onto dinner; otherwise by the clock."""
    hm = _valid_hm(hm) or now_hm()
    r = db().execute("SELECT MAX(mtime) AS t FROM meals WHERE day=? AND slot=?",
                     (day, "Dinner")).fetchone()
    if r and r["t"] and hm > r["t"]:
        return LATE_SNACK
    for until, slot in MEAL_SLOT_BY_TIME:
        if hm < until:
            return slot
    return "Dinner"


@app.route("/api/meals/slotguess")
@login_required
def api_meal_slotguess():
    day = _valid_day(request.args.get("day")) or today()
    return jsonify(slot=meal_slot_guess(day, request.args.get("time")), day=day)


def nut_snacks(day):
    """Late snack and Quick bites as their own lines under a day's totals.
    They are already inside the day's totals; these say how much of it."""
    out = {}
    for slot, key in ((LATE_SNACK, "late"), (QUICK_BITE, "bites")):
        r = db().execute("SELECT COUNT(*) AS n, COALESCE(SUM(kcal),0) AS k, COALESCE(SUM(protein),0) AS p "
                         "FROM meals WHERE day=? AND slot=?", (day, slot)).fetchone()
        out[key] = {"n": int(r["n"] or 0), "kcal": int(round(float(r["k"] or 0))),
                    "protein": round(float(r["p"] or 0), 1)}
    return out


def snack_lines_text(t):
    """'Late snack 120 kcal · Quick bites 2 · 250 kcal' -- empty when neither."""
    bits = []
    if t.get("late", {}).get("n"):
        bits.append("Late snack %d kcal" % t["late"]["kcal"])
    if t.get("bites", {}).get("n"):
        b = t["bites"]
        bits.append("Quick bite%s %d · %d kcal" % ("" if b["n"] == 1 else "s", b["n"], b["kcal"]))
    return " · ".join(bits)


# ------------------------------------------------ food groups and the picker
def food_group(r):
    tags = (r["tags"] or "").lower()
    if "group:" in tags:
        g = tags.split("group:", 1)[1].split(",")[0].strip()
        for x in FOOD_GROUPS:
            if x.lower() == g or x.lower().startswith(g):
                return x
    name = " " + (r["item"] or "").lower() + " "
    for grp, words in FOOD_GROUP_WORDS:
        if any(w in name for w in words):
            return grp
    return FOOD_GROUP_BY_CAT.get((r["cat"] or "")[:1], "Snacks")


def _food_json(r, recent):
    return dict({"n": r["item"], "portion": r["portion"], "p": r["protein"], "k": r["kcal"],
                 "group": food_group(r), "fav": bool(r["fav"]), "recent": recent,
                 "est": "estimated" in (r["tags"] or "")}, **lib_wt(r))


@app.route("/api/foods/picker")
@login_required
def api_foods_picker():
    """Foods in groups: favourites first, then recently used (latest first),
    then A-Z. A search looks in every group. Sabzi also lists his dishes."""
    grp = request.args.get("group") or ""
    qy = (request.args.get("q") or "").strip().lower()
    lib = _lib_map()
    since = (date.today() - timedelta(days=60)).isoformat()
    last = {}
    for r in db().execute("SELECT day, mtime, items FROM meals WHERE day>=? ORDER BY day, mtime", (since,)):
        try:
            for it in json.loads(r["items"] or "[]"):
                if it.get("n"):
                    last[it["n"]] = (r["day"] or "") + " " + (r["mtime"] or "")
        except (ValueError, AttributeError):
            pass
    names = list(lib)
    if qy:
        words = qy.split()
        names = [n for n in names if all(w in (n + " " + (lib[n]["tags"] or "")).lower() for w in words)]
    elif grp in FOOD_GROUPS:
        names = [n for n in names if food_group(lib[n]) == grp]
    else:
        names = [n for n in names if lib[n]["fav"] or n in last]
    fav = sorted([n for n in names if lib[n]["fav"]], key=lambda n: n.lower())
    rec = sorted([n for n in names if not lib[n]["fav"] and n in last], key=lambda n: last[n], reverse=True)
    rest = sorted([n for n in names if not lib[n]["fav"] and n not in last], key=lambda n: n.lower())
    foods = [_food_json(lib[n], n in last) for n in (fav + rec + rest)[:60]]
    dishes = []
    if grp == "Sabzi" or qy:
        for d in dish_rows(lib):
            if grp == "Sabzi" or all(w in d["name"].lower() for w in qy.split()):
                dishes.append(d)
    return jsonify(foods=foods, dishes=dishes, groups=FOOD_GROUPS, group=grp)


# ------------------------------------------------ dishes
def _dish_variants(row):
    try:
        v = json.loads(row["variants"] or "[]")
    except ValueError:
        v = []
    return v if isinstance(v, list) else []


def dish_item(dish_id, vi, grams, lib=None):
    """One meal item from a dish variant and a cooked weight, worked out from
    its components' per-100 values in the food list. (item, missing names);
    item is None when any component is missing or has no weight -- a dish is
    never half-computed."""
    try:
        row = db().execute("SELECT * FROM dishes WHERE id=?", (int(dish_id),)).fetchone()
        g = float(grams)
        vi = int(vi or 0)
    except (TypeError, ValueError):
        return None, ["that dish"]
    if not row or not (0 < g <= 2000):
        return None, ["that dish"]
    vs = _dish_variants(row)
    if not (0 <= vi < len(vs)):
        return None, ["that variant"]
    v = vs[vi]
    lib = lib if lib is not None else _lib_map()
    p = k = f = 0.0
    worst, missing = "L", []
    for name, share in v.get("parts") or []:
        r = lib.get(name)
        w = lib_weigh(r, g * float(share) / 100.0) if r else None
        if not w:
            missing.append(name)
            continue
        p += w["q"] * w["p"]
        k += w["q"] * w["k"]
        f += w["q"] * w["f"]
        if FMAP.get(w["fm"], 1.0) > FMAP.get(worst, 0.0):
            worst = w["fm"]
    if missing:
        return None, missing
    return {"n": v.get("label") or row["name"], "q": 1, "g": round(g, 1), "u": "g",
            "p": round(p, 2), "k": round(k, 1), "f": round(f, 2), "fm": worst,
            "dish": row["id"], "v": vi}, []


def dish_rows(lib=None):
    lib = lib if lib is not None else _lib_map()
    out = []
    for row in db().execute("SELECT * FROM dishes ORDER BY name").fetchall():
        vs = []
        for i, v in enumerate(_dish_variants(row)):
            it, miss = dish_item(row["id"], i, row["katori_g"] or DISH_KATORI_G, lib)
            vs.append({"name": v.get("name"), "label": v.get("label"), "parts": v.get("parts") or [],
                       "katori": ({"p": it["p"], "k": it["k"], "f": it["f"]} if it else None),
                       "missing": miss})
        out.append({"id": row["id"], "name": row["name"], "katori_g": row["katori_g"] or DISH_KATORI_G,
                    "variants": vs})
    return out


@app.route("/api/dishes")
@login_required
def api_dishes():
    return jsonify(dishes=dish_rows())


@app.route("/api/dishes/item")
@login_required
def api_dish_item():
    it, miss = dish_item(request.args.get("id"), request.args.get("v"), request.args.get("g"))
    if not it:
        return jsonify(ok=False, err="Not in the food list with a weight: " + ", ".join(miss)), 400
    return jsonify(ok=True, item=it)


@app.route("/api/dishes/<int:did>", methods=["POST"])
@login_required
def api_dish_edit(did):
    """His proportions: each variant's shares must add up to 100."""
    d = J()
    row = db().execute("SELECT * FROM dishes WHERE id=?", (did,)).fetchone()
    if not row:
        return jsonify(ok=False, err="That dish is gone."), 404
    old = _dish_variants(row)
    new = d.get("variants") if isinstance(d.get("variants"), list) else old
    clean = []
    for i, v in enumerate(new[:6]):
        parts = []
        try:
            for name, share in v.get("parts") or []:
                share = float(share)
                if share <= 0 or not str(name).strip():
                    raise ValueError
                parts.append([str(name).strip()[:80], round(share, 1)])
        except (TypeError, ValueError):
            return jsonify(ok=False, err="Each part needs a food and a share above 0."), 400
        if not parts or abs(sum(s for _, s in parts) - 100.0) > 0.5:
            return jsonify(ok=False, err="The shares of %s must add up to 100 %%."
                           % (v.get("label") or v.get("name") or "a variant")), 400
        base = old[i] if i < len(old) else {}
        clean.append({"name": (v.get("name") or base.get("name") or "variant")[:30],
                      "label": (v.get("label") or base.get("label") or row["name"])[:60], "parts": parts})
    try:
        kg = float(d.get("katori_g") or row["katori_g"] or DISH_KATORI_G)
    except (TypeError, ValueError):
        kg = DISH_KATORI_G
    if not (50 <= kg <= 400):
        return jsonify(ok=False, err="A katori is between 50 and 400 g."), 400
    db().execute("UPDATE dishes SET variants=?, katori_g=?, updated=? WHERE id=?",
                 (json.dumps(clean), kg, now_s(), did))
    db().commit()
    return jsonify(ok=True)


# ------------------------------------------------ one-tap snacks and the Quick Bite
def _first_food(lib, names):
    for n in names:
        if n in lib:
            return lib[n]
    return None


def late_snack_buttons(lib=None):
    lib = lib if lib is not None else _lib_map()
    out = []
    for key, label, names, grams, q, _new in LATE_SNACK_BUTTONS:
        r = _first_food(lib, names)
        pay = None
        if r is not None:
            pay = {"n": r["item"], "g": grams} if lib_wt(r)["w"] else {"n": r["item"], "q": q}
        out.append({"key": key, "label": label, "item": r["item"] if r is not None else "",
                    "payload": pay})
    return out


def qb_buttons(lib=None):
    lib = lib if lib is not None else _lib_map()
    out = []
    for key, label, names, _new in QB_WHAT:
        if isinstance(names, str):
            out.append({"key": key, "label": label, "group": names.split(":", 1)[1]})
            continue
        r = _first_food(lib, names)
        out.append({"key": key, "label": label, "item": r["item"] if r is not None else "",
                    "w": bool(r is not None and lib_wt(r)["w"]),
                    "pq": (r["portion_qty"] if r is not None else None)})
    return out


@app.route("/api/snacks/buttons")
@login_required
def api_snack_buttons():
    lib = _lib_map()
    return jsonify(late=late_snack_buttons(lib), note=LATE_SNACK_NOTE, quick=qb_buttons(lib),
                   reasons=QB_REASONS, amounts=QB_AMOUNTS)


@app.route("/api/snacks/late", methods=["POST"])
@login_required
def api_snack_late():
    """One tap: the button's default portion, as a Late snack, now. It is an
    ordinary meal row, so Edit changes it afterwards."""
    d = J()
    b = [x for x in late_snack_buttons() if x["key"] == d.get("key")]
    if not b or not b[0]["payload"]:
        return jsonify(ok=False, err="That food is not in your list yet."), 400
    body, code = _log_meal({"card": "", "slot": LATE_SNACK, "day": d.get("day") or today(),
                            "mtime": d.get("mtime") or now_hm(), "extra": [b[0]["payload"]]})
    return jsonify(**body), code


@app.route("/api/quickbite", methods=["POST"])
@login_required
def api_quickbite():
    """What, how much, why, when -- one row, slot Quick bite, reason kept."""
    d = J()
    amt = dict((a[0], a[2]) for a in QB_AMOUNTS).get(d.get("amount"), 1.0)
    reason = d.get("reason") if d.get("reason") in QB_REASONS else ""
    extra = []
    for x in (d.get("items") or [])[:10]:
        if x.get("dish"):
            try:
                extra.append({"n": x.get("n") or "dish", "dish": x["dish"], "v": x.get("v", 0),
                              "g": float(x.get("g")) * amt})
            except (TypeError, ValueError):
                continue
        elif x.get("n"):
            try:
                if x.get("g") not in (None, "", 0, "0"):
                    extra.append({"n": x["n"], "g": round(float(x["g"]) * amt, 1)})
                else:
                    extra.append({"n": x["n"], "q": float(x.get("q") or 1) * amt})
            except (TypeError, ValueError):
                continue
    if not extra:
        return jsonify(ok=False, err="Pick what you had."), 400
    body, code = _log_meal({"card": "", "slot": QUICK_BITE, "day": d.get("day") or today(),
                            "mtime": d.get("mtime") or now_hm(), "extra": extra})
    if body.get("ok"):
        db().execute("UPDATE meals SET reason=? WHERE id=?", (reason, body["id"]))
        db().commit()
        body["reason"] = reason
    return jsonify(**body), code


# ------------------------------------------------ the weekly review
def snack_swaps():
    try:
        path = os.environ.get("GUTLOG_SNACK_SWAPS") or os.path.join(BASE, "snack_swaps.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("rules") or []
    except (OSError, ValueError, AttributeError):
        return []


def snack_swap_for(name, late=False, rules=None):
    low = (name or "").lower()
    for r in (rules if rules is not None else snack_swaps()):
        if r.get("late_only") and not late:
            continue
        if any(w in low for w in r.get("words") or []):
            return r.get("swap") or ""
    return ""


def _hm_text(m):
    m = max(0, min(int(m), 24 * 60))
    return "%02d:%02d" % divmod(m, 60) if m < 24 * 60 else "24:00"


def snack_band(times, minimum=2):
    """The 90-minute band (starting on a half hour) holding the most entries:
    {'n', 'of', 'from', 'to'}, or None below `minimum`."""
    mins = sorted(_hm_min(t) for t in times if _valid_hm(t))
    best_n, best_s = 0, None
    for s in range(0, 24 * 60, 30):
        n = sum(1 for m in mins if s <= m < s + SNACK_BAND_MIN)
        if n > best_n:
            best_n, best_s = n, s
    if best_s is None or best_n < minimum:
        return None
    return {"n": best_n, "of": len(mins), "from": _hm_text(best_s), "to": _hm_text(best_s + SNACK_BAND_MIN)}


def snack_week(ref=None, mode="week"):
    """(start, end) ISO days. 'week' is Monday..Sunday ending on the most
    recent Sunday (today, on a Sunday); '7d' is the last seven days."""
    t = ref or date.today()
    if mode == "7d":
        return (t - timedelta(days=6)).isoformat(), t.isoformat()
    end = t - timedelta(days=(t.weekday() + 1) % 7)
    return (end - timedelta(days=6)).isoformat(), end.isoformat()


def _snack_rows(a, b):
    return [dict(r) for r in db().execute(
        "SELECT id, day, mtime, slot, items, kcal, protein, reason FROM meals "
        "WHERE day BETWEEN ? AND ? AND slot IN (?, ?) ORDER BY day, mtime", (a, b) + SNACK_SLOTS).fetchall()]


def _snack_items(rows):
    agg = {}
    for r in rows:
        try:
            its = json.loads(r["items"] or "[]")
        except ValueError:
            its = []
        seen = set()
        for it in its:
            n = it.get("n")
            if not n:
                continue
            a = agg.setdefault(n, {"n": 0, "kcal": 0.0, "protein": 0.0, "late": False})
            q = float(it.get("q") or 1)
            a["kcal"] += q * float(it.get("k") or 0)
            a["protein"] += q * float(it.get("p") or 0)
            a["late"] = a["late"] or r["slot"] == LATE_SNACK
            if n not in seen:
                a["n"] += 1
                seen.add(n)
    return agg


def snack_review(ref=None, mode="week"):
    a, b = snack_week(ref, mode)
    rows = _snack_rows(a, b)
    tot = db().execute("SELECT COALESCE(SUM(kcal),0) AS k FROM meals WHERE day BETWEEN ? AND ?",
                       (a, b)).fetchone()["k"] or 0
    rules = snack_swaps()
    agg = _snack_items(rows)
    marks = dict((r["item"], r["mark"]) for r in db().execute(
        "SELECT item, mark FROM snack_marks WHERE week_start=?", (a,)).fetchall())
    top = sorted(agg.items(), key=lambda kv: (-kv[1]["kcal"], kv[0]))[:6]
    reasons = {}
    for r in rows:
        if r["slot"] == QUICK_BITE and r.get("reason"):
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    out = {"from": a, "to": b, "mode": mode, "from_text": plan_dmy(a), "to_text": plan_dmy(b),
           "bites": len([r for r in rows if r["slot"] == QUICK_BITE]),
           "late": len([r for r in rows if r["slot"] == LATE_SNACK]),
           "band": snack_band([r["mtime"] for r in rows], 2),
           "reasons": sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])),
           "week_kcal": int(round(float(tot))),
           "top": [{"item": n, "times": v["n"], "kcal": int(round(v["kcal"])),
                    "protein": round(v["protein"], 1), "sugar": None,
                    "share": int(round(100.0 * v["kcal"] / tot)) if tot else 0} for n, v in top],
           "sugar_note": "Sugar is not in the bundled food table, so it is not shown.",
           "swaps": [], "followup": []}
    for n, v in sorted(agg.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        if v["n"] >= 2:
            out["swaps"].append({"item": n, "times": v["n"], "swap": snack_swap_for(n, v["late"], rules),
                                 "mark": marks.get(n, "")})
    if mode == "week":
        pa = (date.fromisoformat(a) - timedelta(days=7)).isoformat()
        pb = (date.fromisoformat(a) - timedelta(days=1)).isoformat()
        before = _snack_items(_snack_rows(pa, pb))
        for r in db().execute("SELECT item, mark FROM snack_marks WHERE week_start=? AND mark IN "
                              "('Swap','Stop') ORDER BY item", (pa,)).fetchall():
            out["followup"].append({"item": r["item"], "mark": r["mark"],
                                    "before": before.get(r["item"], {}).get("n", 0),
                                    "after": agg.get(r["item"], {}).get("n", 0)})
    return out


@app.route("/api/snacks/review")
@login_required
def api_snack_review():
    return jsonify(snack_review(mode="7d" if request.args.get("mode") == "7d" else "week"))


@app.route("/api/snacks/mark", methods=["POST"])
@login_required
def api_snack_mark():
    d = J()
    mark = d.get("mark") if d.get("mark") in ("Keep", "Swap", "Stop") else None
    ws = _valid_day(d.get("week_start"))
    item = (d.get("item") or "").strip()[:80]
    if not (mark and ws and item):
        return jsonify(ok=False, err="Pick Keep, Swap or Stop."), 400
    db().execute("INSERT OR REPLACE INTO snack_marks(week_start, item, mark, set_at) VALUES(?,?,?,?)",
                 (ws, item, mark, now_s()))
    db().commit()
    return jsonify(ok=True)


def snack_nudge(day=None, hm=None):
    """One line on the Now page during a band that held SNACK_NUDGE_MIN or
    more Quick bites in the seven days before today. Dismissible for the day."""
    day = day or today()
    hm = hm or now_hm()
    if setting("snack_nudge_off") == day:
        return {"show": False}
    d = date.fromisoformat(day)
    rows = [r for r in _snack_rows((d - timedelta(days=7)).isoformat(), (d - timedelta(days=1)).isoformat())
            if r["slot"] == QUICK_BITE]
    band = snack_band([r["mtime"] for r in rows], SNACK_NUDGE_MIN)
    if not band or not (band["from"] <= hm < band["to"]):
        return {"show": False, "band": band}
    inband = [r for r in rows if band["from"] <= (r["mtime"] or "") < band["to"]]
    agg = _snack_items(inband)
    marked = dict((r["item"], r["mark"]) for r in db().execute(
        "SELECT item, mark FROM snack_marks ORDER BY week_start").fetchall())
    swap = ""
    for n, v in sorted(agg.items(), key=lambda kv: (0 if marked.get(kv[0]) == "Swap" else 1, -kv[1]["n"], kv[0])):
        swap = snack_swap_for(n, v["late"])
        if swap:
            break
    text = "Usual snack time — planned swap: %s." % swap if swap else "Usual snack time."
    return {"show": True, "text": text, "band": band}


@app.route("/api/snacks/nudge", methods=["GET", "POST"])
@login_required
def api_snack_nudge():
    if request.method == "POST":
        set_setting("snack_nudge_off", today())
        return jsonify(ok=True)
    return jsonify(snack_nudge())


def ft_day_snack_lines(day):
    """Late snacks and Quick bites on a Food Test day, each on its own line,
    so a random snack sits next to the test food instead of hiding in it."""
    out = []
    for r in db().execute("SELECT mtime, slot, items, reason FROM meals WHERE day=? AND slot IN (?, ?) "
                          "ORDER BY mtime", (day,) + SNACK_SLOTS).fetchall():
        try:
            names = ", ".join(str(i.get("n")) for i in json.loads(r["items"] or "[]") if i.get("n"))
        except (ValueError, AttributeError):
            names = ""
        out.append("%s %s: %s%s" % (r["slot"], r["mtime"] or "?", names or "—",
                                    (" (%s)" % r["reason"]) if r["reason"] else ""))
    return out


# ------------------------------------------------ the seed
def _seed_food(con, name, cat, portion, qty, unit, b, fm, source, ref, tags, note):
    per = [round(v * qty / 100.0, 2) if v is not None else None for v in b]
    con.execute("INSERT INTO library(cat, item, portion, protein, kcal, fibre, fodmap, status, fav, tags, note, "
                "created, portion_qty, portion_unit, basis_qty, basis_unit, b_protein, b_kcal, b_fibre, "
                "weighed_dry, portion_est, source, source_date, source_ref) "
                "VALUES(?,?,?,?,?,?,?,'',0,?,?,?,?,?,100,?,?,?,?,0,0,?,?,?)",
                (cat, name, portion, per[0], per[1], per[2], fm, tags, note, now_s(), qty, unit, unit,
                 b[0], b[1], b[2], source, today(), ref))


def snacks_seed(con, apply=True):
    """Adds what the late-snack and Quick Bite buttons and the dishes need and
    is missing -- from the bundled table where it has the food, marked
    estimated where it does not -- and the dishes that are not there yet. Never
    changes a food or a dish he already has. Returns what it did (or would)."""
    have = set(r[0] for r in con.execute("SELECT item FROM library").fetchall())
    _doc, byid = food_table()
    rep = {"foods": [], "estimated": [], "dishes": [], "missing_components": []}
    for key, label, names, grams, q, new in LATE_SNACK_BUTTONS:
        if any(n in have for n in names) or not new:
            continue
        nm, fdc, qty, unit, fm, cat = new
        t = byid.get(int(fdc))
        if not t:
            continue
        rep["foods"].append(nm)
        if apply:
            _seed_food(con, nm, cat, "%g %s" % (qty, unit), float(qty), unit, [t[2], t[3], t[4]], fm,
                       "USDA", "USDA SR Legacy #%d: %s" % (t[0], t[1]), "late snack",
                       "Added for the late-snack buttons; values from the table.")
        have.add(nm)
    for key, label, names, new in QB_WHAT:
        if isinstance(names, str) or any(n in have for n in names) or not new:
            continue
        nm, portion, qty, p, k, f, fm, cat = new
        rep["estimated"].append(nm)
        if apply:
            _seed_food(con, nm, cat, portion, float(qty), "g",
                       [round(v * 100.0 / qty, 2) for v in (p, k, f)], fm, "estimated", "",
                       "estimated quick bite", "Estimated typical values for the Quick Bite button; edit when known.")
        have.add(nm)
    dnames = set(r[0] for r in con.execute("SELECT name FROM dishes").fetchall())
    for name, variants in DISH_SEED:
        for _v, _l, parts in variants:
            for comp, _s in parts:
                if comp not in have and comp not in rep["missing_components"]:
                    rep["missing_components"].append(comp)
        if name in dnames:
            continue
        rep["dishes"].append(name)
        if apply:
            con.execute("INSERT INTO dishes(name, katori_g, variants, updated) VALUES(?,?,?,?)",
                        (name, DISH_KATORI_G,
                         json.dumps([{"name": v, "label": l, "parts": [[c, s] for c, s in parts]}
                                     for v, l, parts in variants]), now_s()))
    if apply:
        con.commit()
    return rep


'''
E.append(("snacks code", '# ------------------------------------------------------------ week 0 from meals\n',
          CODE + '# ------------------------------------------------------------ week 0 from meals\n'))
E.append(("page gets the lists",
          '    return render_template_string(APP_PAGE.replace("__ABD_SITES__", json.dumps(ABD_SITES)),\n',
          '    # GUTLOG_V3350_SNACKS -- the slot and group lists, written in before render.\n'
          '    mc = [MEAL_SLOTS, MEAL_SLOT_EXTRA, QUICK_BITE, LATE_SNACK, FOOD_GROUPS,\n'
          '          [[g, FOOD_GROUP_CAT[g]] for g in FOOD_GROUPS]]\n'
          '    return render_template_string(APP_PAGE.replace("__ABD_SITES__", json.dumps(ABD_SITES))\n'
          '                                  .replace("__MEAL_CFG__", json.dumps(mc)),\n'))

# ================================================================== the page
E.append(("quick bite bar",
          '  <div id="nowMirror"></div>\n  <!-- GUTLOG_V3340_FTMEALS',
          '  <div id="nowMirror"></div>\n'
          '  <!-- GUTLOG_V3350_SNACKS -- always there, small, not a card. -->\n'
          '  <div class="qbbar"><button type="button" class="btn tiny" id="qbBtn">+ Quick bite</button>'
          '<span class="nudge" id="nowNudge"></span></div>\n'
          '  <!-- GUTLOG_V3340_FTMEALS'))
E.append(("meals tab slots",
          '      <div class="chips" data-f="slot" data-sec="meal" data-v="Breakfast|Lunch|Dinner|Snack"></div></div>\n',
          '      <div class="chips" id="mlSlots"></div></div>\n'))
E.append(("meals tab groups",
          '      <input type="text" id="ml_search" placeholder="Search your foods... (dal, roti, guava)" autocomplete="off">\n',
          '      <input type="text" id="ml_search" placeholder="Search all foods... (dal, roti, guava)" autocomplete="off">\n'
          '      <div class="chips fgroups" id="ml_groups" style="margin-top:8px"></div>\n'))
E.append(("review card",
          '''    <div class="card" id="mlDayCard"><p class="q" id="mlDayHead">Meals</p>
      <div id="mlDayMeals"></div></div>
''',
          '''    <div class="card" id="mlDayCard"><p class="q" id="mlDayHead">Meals</p>
      <div id="mlDayMeals"></div></div>
    <div class="card" id="snackReview"></div>
'''))
E.append(("css",
          '.ftdin .chips{gap:6px;margin:4px 0 6px}\n',
          '.ftdin .chips{gap:6px;margin:4px 0 6px}\n'
          '/* GUTLOG_V3350_SNACKS -- sized for 300px. */\n'
          '.qbbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 10px}\n'
          '.qbbar .btn.tiny{padding:7px 12px;font-size:14px;width:auto}\n'
          '.nudge{font-size:13px;color:var(--ink);display:flex;align-items:center;gap:6px;flex:1 1 160px;min-width:0}\n'
          '.nudgex{border:0;background:none;color:var(--muted);font-size:18px;cursor:pointer;padding:0 4px}\n'
          '.fgroups{margin:8px 0 4px;gap:5px}.fgroups .chip{padding:6px 10px;font-size:13px}\n'
          '.fres{margin-top:6px}\n'
          '.dishpk,.nff{border-top:1px dashed var(--line);margin-top:10px;padding-top:10px}\n'
          '.nfr{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}\n'
          '.nfr .lb{font-size:13px;font-weight:700;color:var(--muted);flex:0 0 100%}\n'
          '.nfr input[type=text]{flex:1 1 100%;min-width:0}\n'
          '.nfr input[type=number]{width:90px}\n'
          '.nfv{display:flex;gap:6px;flex-wrap:wrap}.nfv input{flex:1 1 70px;min-width:0;width:auto}\n'
          '.lsn .chips{gap:6px}.lsnote{margin:6px 2px 0}\n'
          '.qbsheet{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;display:flex;'
          'align-items:flex-end;justify-content:center}\n'
          '.qbcard{background:var(--card);color:var(--ink);width:100%;max-width:520px;max-height:92vh;'
          'overflow:auto;border-radius:16px 16px 0 0;padding:14px 14px 18px;box-sizing:border-box}\n'
          '.qbcard .chips{gap:6px}\n'
          '.snk{font-size:12.5px;color:var(--muted)}\n'
          '#snackReview:empty{display:none}\n'
          '.srrange,.srcount,.srband,.srwhy{margin:4px 0;font-size:13.5px}\n'
          '.srcount{font-weight:700}.srrow{margin:3px 0;font-size:13px}\n'
          '.srswap{border-top:1px solid var(--line);padding-top:6px;margin-top:6px;font-size:13.5px}\n'
          '.srswap p{margin:0 0 5px}\n'))

E.append(("now card slot list",
          "    ['Breakfast','Lunch','Snack','Dinner','Eating out'].forEach(s=>{const t=el('button','chip'+(mcSel.slot===s?' sel':''),s);t.type='button';t.onclick=()=>{mcSel.slot=s;mcRender();};ch.appendChild(t);});\n"
          "    row.appendChild(ch);b.appendChild(row);\n",
          "    /* GUTLOG_V3350_SNACKS -- the one slot list, from the server. */\n"
          "    mealSlotList(mcSel.slot).forEach(s=>{const t=el('button','chip'+(mcSel.slot===s?' sel':''),s);t.type='button';t.onclick=()=>{mcSel.slot=s;mcRender();};ch.appendChild(t);});\n"
          "    row.appendChild(ch);b.appendChild(row);\n"
          "    if(mcSel.slot===LATE_SLOT)b.appendChild(lateSnackRow());\n"))
E.append(("now card dish line",
          "      const v=el('span','mq',x.g>0?'by weight':'×'+x.q);\n"
          "      mi.onclick=()=>{if(x.g>0){x.g=0;}else if(x.q<=0.5){mcSel.extra.splice(xi,1);}else{x.q=x.q<=1?0.5:x.q-1;}mcRender();};\n",
          "      const v=el('span','mq',x.dish?(x.g+' g'):(x.g>0?'by weight':'×'+x.q));   /* GUTLOG_V3350_SNACKS */\n"
          "      mi.onclick=()=>{if(x.dish){mcSel.extra.splice(xi,1);}else if(x.g>0){x.g=0;}else if(x.q<=0.5){mcSel.extra.splice(xi,1);}else{x.q=x.q<=1?0.5:x.q-1;}mcRender();};\n"))
E.append(("now card slot guess",
          "function mcSlotGuess(){const h=Number(mcNowHM().slice(0,2));return h<11?'Breakfast':h<16?'Lunch':h<19?'Snack':'Dinner';}\n",
          "/* GUTLOG_V3350_SNACKS -- the server's guess: after Dinner it is a Late snack. */\n"
          "function mcSlotGuess(){return (MC&&MC.slot_guess)||'Dinner';}\n"))
E.append(("old picker out",
          r"""function mcPicker(){
  const box=el('div','mpick');const inp=document.createElement('input');inp.type='text';inp.placeholder='Search food or type a new dish';inp.id='mcQ';
  const res=el('div','chips');res.style.marginTop='8px';const nd=el('div','');
  box.appendChild(inp);box.appendChild(res);box.appendChild(nd);
  let tm=null;
  const run=async()=>{const q=inp.value.trim();let j;try{j=await jget('/api/foods/search?q='+encodeURIComponent(q));}catch(e){return;}
    res.innerHTML='';nd.innerHTML='';
    j.foods.forEach(f=>{const t=el('button','chip',f.n+(f.est?' *':''));t.type='button';t.title=f.portion||'';
      t.onclick=()=>{MC.lib[f.n]={p:f.p||0,k:f.k||0,est:f.est,w:f.w,pq:f.pq,u:f.u,dry:f.dry,bp:f.bp,bk:f.bk,bf:f.bf};mcSel.extra.push({n:f.n,q:1,g:0});mcPick=false;mcRender();};res.appendChild(t);});
    if(q.length>=2&&!j.foods.some(f=>f.n.toLowerCase()===q.toLowerCase()))nd.appendChild(mcNewDish(q));};
  inp.oninput=()=>{clearTimeout(tm);tm=setTimeout(run,220);};
  setTimeout(()=>{run();},0);
  const cl=el('button','btn ghost','Close');cl.type='button';cl.style.marginTop='8px';cl.onclick=()=>{mcPick=false;mcRender();};box.appendChild(cl);
  return box;
}
""",
          "/* GUTLOG_V3350_SNACKS -- mcPicker() is the arranged picker now (below). */\n"))
E.append(("auto late snack",
          "function mcAuto(){\n  const now=mcNowHM();let best=-1,any=-1;\n"
          "  MC.cards.forEach((c,i)=>{if((c.from||'00:00')<=now){any=i;if(MC.done.indexOf(c.name)<0)best=i;}});\n",
          "function mcAuto(){\n  const now=mcNowHM();let best=-1,any=-1;\n"
          "  MC.cards.forEach((c,i)=>{if((c.from||'00:00')<=now){any=i;if(MC.done.indexOf(c.name)<0)best=i;}});\n"
          "  /* GUTLOG_V3350_SNACKS -- after Dinner, a Late snack, never the Dinner card again. */\n"
          "  if(best<0&&MC.slot_guess===LATE_SLOT)return -1;\n"))
E.append(("load meals other",
          "  if(!mcEdit){mcCur=mcAuto();mcSel=mcFresh(mcCur);}\n",
          "  if(!mcEdit){mcCur=mcAuto();\n"
          "    mcSel=mcCur>=0?mcFresh(mcCur):{choices:{},onion:false,extra:[],slot:mcSlotGuess()};}   /* GUTLOG_V3350_SNACKS */\n"))
E.append(("fresh keeps dish",
          "    extra:(from&&from.extra?from.extra:[]).map(x=>({n:x.n,q:x.q,g:x.g||0}))};\n",
          "    extra:(from&&from.extra?from.extra:[]).map(x=>(x.dish?{n:x.n,q:1,g:x.g,dish:x.dish,v:x.v}:{n:x.n,q:x.q,g:x.g||0}))};\n"))
E.append(("edit keeps dish",
          "        extra:(m.items||[]).map(i=>({n:i.n,q:i.q,g:i.g||0}))};\n",
          "        extra:(m.items||[]).map(i=>(i.dish?{n:i.n,q:1,g:i.g,dish:i.dish,v:i.v}:{n:i.n,q:i.q,g:i.g||0}))};\n"))
E.append(("edit opens card",
          "  const nm=$('#nowMeal');if(nm)nm.scrollIntoView({behavior:'smooth'});\n",
          "  const nm=$('#nowMeal');if(nm){nm.classList.add('open');nm.scrollIntoView({behavior:'smooth'});}   /* GUTLOG_V3350_SNACKS */\n"))
E.append(("recipe slot guess",
          "    try{const h=Number(mcNowHM().slice(0,2));const slot=h<11?'Breakfast':h<16?'Lunch':h<19?'Snack':'Dinner';\n",
          "    try{const slot=(await jget('/api/meals/slotguess?day='+todayISO+'&time='+mcNowHM())).slot;   /* GUTLOG_V3350_SNACKS */\n"))
E.append(("meals tab picker",
          r"""function renderResults(qstr){
  const box=$('#ml_results');box.innerHTML='';
  const q=qstr.trim().toLowerCase();
  let list;
  if(!q){list=LIB.filter(x=>x.fav).slice(0,10); if(!list.length)list=LIB.slice(0,8);}
  else list=LIB.filter(x=>x.item.toLowerCase().includes(q)||(x.tags||'').includes(q)).slice(0,12);
  list.forEach(x=>{
    const b=document.createElement('button');b.type='button';b.className='chip';
    b.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span>${x.item}`;
    b.onclick=()=>addToBasket(x);
    box.appendChild(b);
  });
""",
          r"""/* GUTLOG_V3350_SNACKS -- the Meals tab uses the arranged picker too: group
   chips, favourites then recent then A-Z, search across every group, his
   dishes under Sabzi, and a new food with its weight from the not-found state. */
let mlGrp='',mlTok=0;
function mlGroupsDraw(){const gr=$('#ml_groups');if(!gr)return;gr.innerHTML='';
  [['','★ Mine']].concat(FOOD_GROUPS.map(g=>[g,g])).forEach(g=>{const b=el('button','chip'+(mlGrp===g[0]?' sel':''),g[1]);
    b.type='button';b.dataset.g=g[0];b.onclick=()=>{mlGrp=g[0];$('#ml_search').value='';mlGroupsDraw();renderResults('');};gr.appendChild(b);});}
async function renderResults(qstr){
  const box=$('#ml_results');const q=qstr.trim();const my=++mlTok;
  if(!$('#ml_groups').children.length)mlGroupsDraw();
  let j;try{j=await jget('/api/foods/picker?group='+encodeURIComponent(q?'':mlGrp)+'&q='+encodeURIComponent(q));}catch(e){return;}
  if(my!==mlTok)return;
  box.innerHTML='';
  const nfx=$('#ml_newfood');
  (j.dishes||[]).forEach(d=>{const b=el('button','chip dish','🥘 '+d.name);b.type='button';b.dataset.dish=d.id;
    b.onclick=()=>{nfx.dataset.open='1';nfx.style.display='block';nfx.innerHTML='';
      nfx.appendChild(dishPick(d,it=>{addDishToBasket(it);nfx.dataset.open='';nfx.innerHTML='';nfx.style.display='none';}));};
    box.appendChild(b);});
  const list=j.foods.map(f=>libItem(f.n)).filter(Boolean);
  list.forEach(x=>{
    const b=document.createElement('button');b.type='button';b.className='chip';
    b.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span>`;
    b.appendChild(document.createTextNode((x.fav?'★ ':'')+x.item));
    b.onclick=()=>addToBasket(x);
    box.appendChild(b);
  });
"""))
E.append(("meals tab new food",
          r"""    a.onclick=()=>{nf.dataset.open='1';
      lfEditor(nf,{id:0,item:qstr.trim(),fodmap:'M'},
        async name=>{nf.dataset.open='';$('#ml_search').value='';await loadLib();
          const x=libItem(name);if(x)addToBasket(x);toast('Food added');},
        ()=>{nf.dataset.open='';renderResults($('#ml_search').value||'');});};
""",
          r"""    a.onclick=()=>{nf.dataset.open='1';nf.innerHTML='';   /* GUTLOG_V3350_SNACKS -- group, katori/piece/grams, table values */
      nf.appendChild(newFoodForm(qstr.trim(),mlGrp,
        async name=>{nf.dataset.open='';$('#ml_search').value='';await loadLib();
          const x=libItem(name);if(x)addToBasket(x);},
        ()=>{nf.dataset.open='';renderResults($('#ml_search').value||'');}));};
"""))
E.append(("basket dish text",
          "    else row.appendChild(el('div','bkw','No weight set for this food — by portion only.'));\n",
          "    else row.appendChild(el('div','bkw',b.dish?('Dish: '+b.dg+' g a portion, worked out from its parts.'):'No weight set for this food — by portion only.'));\n"))
E.append(("save meal dish and slot",
          "    items:basket.map(b=>b.g>0?{n:b.item,g:b.g}:{n:b.item,q:b.q,p:b.p,k:b.k,f:b.f,fm:b.fm})});\n"
          "  basket=[];renderBasket();$('#ml_notes').value='';tmReset('ml_time');await loadRings();await loadMealTotals();\n",
          "    items:basket.map(b=>b.dish?{n:b.item,dish:b.dish,v:b.v,g:b.dg*b.q}:(b.g>0?{n:b.item,g:b.g}:{n:b.item,q:b.q,p:b.p,k:b.k,f:b.f,fm:b.fm}))});\n"
          "  basket=[];renderBasket();$('#ml_notes').value='';tmReset('ml_time');await loadRings();await loadMealTotals();\n"
          "  S.meal.slotSet=0;mlSlotAuto();loadSnackReview();   /* GUTLOG_V3350_SNACKS */\n"))
E.append(("meals tab opens",
          "  if(t==='meals'){tmNow('ml_time');mlRenderStep();loadMealTotals();loadRegistry();renderTestFoods();}\n",
          "  if(t==='meals'){tmNow('ml_time');mlRenderStep();loadMealTotals();loadRegistry();renderTestFoods();\n"
          "    mlSlotAuto();loadSnackReview();}   /* GUTLOG_V3350_SNACKS */\n"))
E.append(("now loads nudge",
          "async function loadNow(){\n  loadMirror();\n",
          "async function loadNow(){\n  loadMirror();\n  loadNudge();   /* GUTLOG_V3350_SNACKS */\n"))
E.append(("day totals snack line",
          "    $('#dayFmap').innerHTML=`FODMAP load: <b>${lab}</b>`;\n",
          "    $('#dayFmap').innerHTML=`FODMAP load: <b>${lab}</b>`;\n"
          "    if(j.snack_text)$('#dayFmap').appendChild(el('div','snk',j.snack_text));   /* GUTLOG_V3350_SNACKS */\n"))

JS = r'''/* GUTLOG_V3350_SNACKS -- meals and snacks. The lists come from the server
   (MEAL_SLOTS, FOOD_GROUPS and friends), written in before render, so the
   page and the server cannot disagree about a slot or a group. */
const MEALCFG=__MEAL_CFG__;
const MEAL_SLOTS=MEALCFG[0],MEAL_EXTRA=MEALCFG[1],QB_SLOT=MEALCFG[2],LATE_SLOT=MEALCFG[3],FOOD_GROUPS=MEALCFG[4];
const FOOD_GROUP_CAT={};MEALCFG[5].forEach(x=>{FOOD_GROUP_CAT[x[0]]=x[1];});
let SNB=null;
async function snackButtons(){if(!SNB){try{SNB=await jget('/api/snacks/buttons');}catch(e){SNB=null;}}return SNB;}
/* An old slot ("Snack") on a meal being edited stays choosable. */
function mealSlotList(cur){const l=MEAL_SLOTS.concat(MEAL_EXTRA);if(cur&&l.indexOf(cur)<0&&cur!==QB_SLOT)l.push(cur);return l;}

/* ---- the arranged picker: search on top (all groups), then group chips,
   favourites first, then recently used, then A-Z. Sabzi lists his dishes. */
function foodPicker(o){
  const box=el('div','mpick fpick');
  const inp=document.createElement('input');inp.type='text';inp.id=o.qid;inp.autocomplete='off';
  inp.placeholder='Search all foods';
  const gr=el('div','chips fgroups');const res=el('div','chips fres');if(o.rid)res.id=o.rid;
  const nd=el('div','fnew');
  box.appendChild(inp);box.appendChild(gr);box.appendChild(res);box.appendChild(nd);
  let grp=o.group||'',tm=null,tok=0;
  const drawGroups=()=>{gr.innerHTML='';
    [['','★ Mine']].concat(FOOD_GROUPS.map(g=>[g,g])).forEach(g=>{
      const b=el('button','chip'+(grp===g[0]?' sel':''),g[1]);b.type='button';b.dataset.g=g[0];
      b.onclick=()=>{grp=g[0];inp.value='';drawGroups();run();};gr.appendChild(b);});};
  const run=async()=>{const q=inp.value.trim();const my=++tok;let j;
    try{j=await jget('/api/foods/picker?group='+encodeURIComponent(q?'':grp)+'&q='+encodeURIComponent(q));}catch(e){return;}
    if(my!==tok)return;
    res.innerHTML='';nd.innerHTML='';
    (j.dishes||[]).forEach(d=>{const b=el('button','chip dish','🥘 '+d.name);b.type='button';b.dataset.dish=d.id;
      b.onclick=()=>{nd.innerHTML='';nd.appendChild(dishPick(d,o.dish));};res.appendChild(b);});
    j.foods.forEach(f=>{const b=el('button','chip',(f.fav?'★ ':'')+f.n+(f.est?' *':''));b.type='button';
      b.title=f.portion||'';b.dataset.group=f.group;b.onclick=()=>o.pick(f);res.appendChild(b);});
    if(q.length>=2&&!j.foods.some(f=>f.n.toLowerCase()===q.toLowerCase())){
      if(o.estimate)nd.appendChild(mcNewDish(q));
      const a=el('button','btn ghost nfopen','Add “'+q+'” with its weight and values');a.type='button';a.id=o.nfid||'';
      a.onclick=()=>{nd.innerHTML='';nd.appendChild(newFoodForm(q,grp,o.added,()=>run()));};nd.appendChild(a);}
    else if(!j.foods.length&&!(j.dishes||[]).length)res.appendChild(el('p','hint',q?'Nothing found.':'Nothing here yet.'));};
  inp.oninput=()=>{clearTimeout(tm);tm=setTimeout(run,220);};
  drawGroups();setTimeout(run,0);
  if(o.close){const cl=el('button','btn ghost','Close');cl.type='button';cl.style.marginTop='8px';cl.onclick=o.close;box.appendChild(cl);}
  box.run=run;
  return box;
}
/* ---- a dish: variant chip, then ½ / 1 / 1½ katori or grams. The numbers
   come from the server, worked out from the dish's parts. */
function dishPick(d,onAdd){
  const w=el('div','dishpk');w.dataset.dish=d.id;
  w.appendChild(el('p','lbl',d.name+' — which one, and how much?'));
  let vi=Math.max(0,d.variants.findIndex(v=>!(v.missing&&v.missing.length))),mult=1,grams=0,item=null;
  const vc=el('div','chips dvar'),pc=el('div','chips dpor'),pv=el('p','hint dprev','');
  const gi=document.createElement('input');gi.type='number';gi.className='bkg';gi.min='0';gi.step='5';
  gi.inputMode='decimal';gi.placeholder='g';gi.setAttribute('aria-label','Cooked weight in grams');
  const g=()=>grams>0?grams:mult*d.katori_g;
  const prev=async()=>{item=null;const v=d.variants[vi];
    if(v.missing&&v.missing.length){pv.textContent='Not in your foods with a weight: '+v.missing.join(', ');return;}
    try{const j=await jget('/api/dishes/item?id='+d.id+'&v='+vi+'&g='+g());item=j.item;
      pv.textContent=Math.round(g())+' g · '+Math.round(item.k)+' kcal · '+item.p.toFixed(1)+' g protein, from its parts';}
    catch(e){pv.textContent=e.message;}};
  const drawV=()=>{vc.innerHTML='';d.variants.forEach((v,i)=>{const b=el('button','chip'+(i===vi?' sel':''),v.label||v.name);
    b.type='button';b.onclick=()=>{vi=i;drawV();prev();};vc.appendChild(b);});};
  const drawP=()=>{pc.innerHTML='';[[0.5,'½ katori'],[1,'1 katori'],[1.5,'1½ katori']].forEach(x=>{
    const b=el('button','chip'+(!grams&&mult===x[0]?' sel':''),x[1]);b.type='button';
    b.onclick=()=>{mult=x[0];grams=0;gi.value='';drawP();prev();};pc.appendChild(b);});};
  gi.oninput=()=>{const n=Number(gi.value);grams=(isFinite(n)&&n>0)?n:0;drawP();prev();};
  const gr=el('div','bkw');gr.appendChild(el('span','','or'));gr.appendChild(gi);gr.appendChild(el('span','','g cooked'));
  const add=el('button','btn primary','Add');add.type='button';add.className='btn primary dishadd';add.style.marginTop='8px';
  add.onclick=()=>{if(item)onAdd(item);else toast('Pick a variant and a portion');};
  w.appendChild(vc);w.appendChild(pc);w.appendChild(gr);w.appendChild(pv);w.appendChild(add);
  drawV();drawP();prev();
  return w;
}
/* ---- a new food, on the spot: name, group, dry or cooked weight, portion,
   and values per 100 g filled from the bundled table when he picks a match.
   Saved as his own entry, with the date. */
function newFoodForm(name,grp,onAdded,onCancel){
  const w=el('div','nff');w.id='nfForm';
  const st={group:FOOD_GROUPS.indexOf(grp)>=0?grp:'',dry:false,unit:'katori',fdc:null};
  const row=(label,node)=>{const r=el('div','nfr');r.appendChild(el('span','lb',label));r.appendChild(node);w.appendChild(r);return r;};
  const nm=document.createElement('input');nm.type='text';nm.id='nf_name';nm.value=name||'';nm.maxLength=80;
  row('Name',nm);
  const gc=el('div','chips');gc.id='nf_group';
  const drawG=()=>{gc.innerHTML='';FOOD_GROUPS.forEach(g=>{const b=el('button','chip'+(st.group===g?' sel':''),g);b.type='button';
    b.onclick=()=>{st.group=g;drawG();};gc.appendChild(b);});};
  row('Group',gc);drawG();
  const dc=el('div','chips');dc.id='nf_dry';
  const drawD=()=>{dc.innerHTML='';[[false,'Cooked weight'],[true,'Dry weight']].forEach(x=>{const b=el('button','chip'+(st.dry===x[0]?' sel':''),x[1]);
    b.type='button';b.onclick=()=>{st.dry=x[0];drawD();look();};dc.appendChild(b);});};
  row('Weighed',dc);drawD();
  const pc=el('div','chips');pc.id='nf_unit';
  const qi=document.createElement('input');qi.type='number';qi.id='nf_qty';qi.min='1';qi.step='1';qi.inputMode='decimal';qi.value='150';
  qi.setAttribute('aria-label','Grams in one portion');
  const drawU=()=>{pc.innerHTML='';[['katori','1 katori'],['piece','1 piece'],['grams','grams']].forEach(x=>{
    const b=el('button','chip'+(st.unit===x[0]?' sel':''),x[1]);b.type='button';
    b.onclick=()=>{st.unit=x[0];if(x[0]==='katori')qi.value='150';else if(x[0]==='grams')qi.value='100';drawU();};pc.appendChild(b);});};
  row('Portion',pc);drawU();
  const qr=el('div','nfr');qr.appendChild(el('span','lb','One portion is'));qr.appendChild(qi);qr.appendChild(el('span','','g'));w.appendChild(qr);
  const fm=document.createElement('select');fm.id='nf_fm';fm.setAttribute('aria-label','FODMAP');
  [['M','not known'],['L','low'],['L-M','low-moderate'],['M-H','moderate-high'],['H','high']].forEach(x=>fm.add(new Option('FODMAP '+x[1],x[0])));
  row('FODMAP',fm);
  const tb=el('div','chips');tb.id='nf_tab';
  w.appendChild(el('p','lbl','From the food table (per 100 g) — tap a match, or type your own'));w.appendChild(tb);
  const vals=el('div','nfv');const vin={};
  [['p','Protein g'],['k','kcal'],['f','Fibre g']].forEach(x=>{const i=document.createElement('input');i.type='number';i.min='0';i.step='0.1';
    i.inputMode='decimal';i.id='nf_'+x[0];i.placeholder=x[1];i.setAttribute('aria-label',x[1]+' per 100 g');
    i.oninput=()=>{st.fdc=null;};vin[x[0]]=i;vals.appendChild(i);});
  w.appendChild(vals);
  let lt=null;
  const look=async()=>{const q=nm.value.trim();tb.innerHTML='';if(q.length<2)return;
    let j;try{j=await jget('/api/foodtable?q='+encodeURIComponent(q)+'&dry='+(st.dry?'1':'0'));}catch(e){return;}
    if(!j.available){tb.appendChild(el('p','hint',j.text||'No food table here; type the values.'));return;}
    (j.matches||[]).slice(0,5).forEach(m=>{const b=el('button','chip',m.desc);b.type='button';b.title=m.kcal+' kcal / 100 g';
      b.onclick=()=>{vin.p.value=m.protein==null?'':m.protein;vin.k.value=m.kcal==null?'':m.kcal;vin.f.value=m.fibre==null?'':m.fibre;
        st.fdc=m.fdc;tb.querySelectorAll('.chip').forEach(c=>c.classList.toggle('sel',c===b));};tb.appendChild(b);});
    if(!(j.matches||[]).length)tb.appendChild(el('p','hint','No match in the table; type the values.'));};
  nm.oninput=()=>{clearTimeout(lt);lt=setTimeout(look,300);};
  const sv=el('button','btn primary','Save to my foods');sv.type='button';sv.id='nf_save';
  sv.onclick=async()=>{const n=nm.value.trim(),qty=Number(qi.value);
    if(!n){toast('Name the food');return;}if(!st.group){toast('Pick a group');return;}
    if(!(qty>0)){toast('Say how many grams one portion is');return;}
    const portion=st.unit==='katori'?('1 katori (~'+qty+' g)'):st.unit==='piece'?('1 piece (~'+qty+' g)'):(qty+' g');
    const num=i=>i.value===''?null:Number(i.value);
    try{await post('/api/library',{item:n,cat:FOOD_GROUP_CAT[st.group]||'F',portion:portion,portion_qty:qty,portion_unit:'g',
        weighed_dry:st.dry,fdc:st.fdc,b_protein:num(vin.p),b_kcal:num(vin.k),b_fibre:num(vin.f),fodmap:fm.value,
        tags:'group:'+st.group+', own entry',note:'Your own entry, added '+mlDmyText(todayISO)+'.'});
      toast(n+' added to your foods');if(onAdded)onAdded(n);}
    catch(e){toast(e.message);}};
  const cx=el('button','btn ghost','Cancel');cx.type='button';cx.onclick=()=>{if(onCancel)onCancel();};
  const br=el('div','btnrow');br.appendChild(sv);br.appendChild(cx);w.appendChild(br);
  look();
  return w;
}
/* ---- the Now card's "+ Something else" is the same picker. */
function mcPicker(){
  return foodPicker({qid:'mcQ',estimate:true,
    pick:f=>{MC.lib[f.n]={p:f.p||0,k:f.k||0,est:f.est,w:f.w,pq:f.pq,u:f.u,dry:f.dry,bp:f.bp,bk:f.bk,bf:f.bf};
      mcSel.extra.push({n:f.n,q:1,g:0});mcPick=false;mcRender();},
    dish:it=>{MC.lib[it.n]={p:it.p,k:it.k};mcSel.extra.push({n:it.n,q:1,g:it.g,dish:it.dish,v:it.v});mcPick=false;mcRender();},
    added:async n=>{try{const j=await jget('/api/foods/picker?q='+encodeURIComponent(n));const f=j.foods.find(x=>x.n===n);
        if(f){MC.lib[f.n]={p:f.p||0,k:f.k||0,w:f.w,pq:f.pq,u:f.u,dry:f.dry,bp:f.bp,bk:f.bk,bf:f.bf};mcSel.extra.push({n:f.n,q:1,g:0});}}
      catch(e){}mcPick=false;mcRender();},
    close:()=>{mcPick=false;mcRender();}});
}
/* ---- under Late snack: one tap logs the default portion, now. */
function lateSnackRow(){
  const w=el('div','mrow lsn');w.id='lateSnacks';
  w.appendChild(el('p','lbl','One tap logs it now'));
  const ch=el('div','chips');w.appendChild(ch);
  const note=el('p','hint lsnote','');w.appendChild(note);
  snackButtons().then(j=>{if(!j)return;note.textContent=j.note||'';
    j.late.forEach(b=>{const t=el('button','chip',b.label);t.type='button';t.dataset.key=b.key;
      if(!b.payload){t.disabled=true;t.title='Not in your foods yet';}
      t.onclick=async()=>{if(t.dataset.busy)return;t.dataset.busy=1;
        try{await post('/api/snacks/late',{key:b.key,day:todayISO,mtime:mcNowHM()});toast(b.label+' logged — Edit changes it');
          mcPick=false;await loadMeals();}catch(e){toast(e.message);}t.dataset.busy='';};
      ch.appendChild(t);});});
  return w;
}
/* ---- Quick Bite: what, how much, why, when -- one sheet. */
let QB={items:[],amount:'normal',reason:''};
async function qbOpen(){
  const j=await snackButtons();if(!j){toast('Could not load the buttons');return;}
  QB={items:[],amount:'normal',reason:''};
  let sh=$('#qbSheet');if(sh)sh.remove();
  sh=el('div','qbsheet');sh.id='qbSheet';
  const card=el('div','qbcard');sh.appendChild(card);
  card.appendChild(el('p','q','Quick bite'));
  const sel=el('p','hint qbsel','Nothing picked yet');sel.id='qbSel';
  const drawSel=()=>{sel.textContent=QB.items.length?('Picked: '+QB.items.map(x=>x.n).join(', ')):'Nothing picked yet';};
  const toggle=(it)=>{const i=QB.items.findIndex(x=>x.n===it.n);if(i>=0)QB.items.splice(i,1);else QB.items.push(it);drawSel();};
  card.appendChild(el('p','lbl','What'));
  const wc=el('div','chips');wc.id='qbWhat';card.appendChild(wc);
  const pk=el('div','qbpick');card.appendChild(pk);
  const openPick=g=>{pk.innerHTML='';pk.appendChild(foodPicker({qid:'qbQ',group:g,
    pick:f=>{toggle({n:f.n,q:1});pk.innerHTML='';},dish:it=>{toggle({n:it.n,dish:it.dish,v:it.v,g:it.g});pk.innerHTML='';},
    added:n=>{toggle({n:n,q:1});pk.innerHTML='';},close:()=>{pk.innerHTML='';}}));};
  j.quick.forEach(b=>{const t=el('button','chip',b.label);t.type='button';t.dataset.key=b.key;
    t.onclick=()=>{if(b.group){openPick(b.group);return;}
      if(!b.item){toast('Not in your foods yet');return;}
      toggle({n:b.item,q:1});t.classList.toggle('sel',QB.items.some(x=>x.n===b.item));};
    wc.appendChild(t);});
  const ot=el('button','chip','Other');ot.type='button';ot.dataset.key='other';ot.onclick=()=>openPick('');wc.appendChild(ot);
  card.appendChild(sel);
  card.appendChild(el('p','lbl','How much'));
  const ac=el('div','chips');ac.id='qbAmt';card.appendChild(ac);
  const drawA=()=>{ac.innerHTML='';j.amounts.forEach(a=>{const t=el('button','chip'+(QB.amount===a[0]?' sel':''),a[1]);t.type='button';t.dataset.v=a[0];
    t.onclick=()=>{QB.amount=a[0];drawA();};ac.appendChild(t);});};drawA();
  card.appendChild(el('p','lbl','Why'));
  const rc=el('div','chips');rc.id='qbWhy';card.appendChild(rc);
  const drawR=()=>{rc.innerHTML='';j.reasons.forEach(r=>{const t=el('button','chip'+(QB.reason===r?' sel':''),r);t.type='button';t.dataset.v=r;
    t.onclick=()=>{QB.reason=QB.reason===r?'':r;drawR();};rc.appendChild(t);});};drawR();
  const tr=el('div','vtm');tr.appendChild(el('span','lb','Time'));
  const ti=document.createElement('input');ti.type='time';ti.id='qbTime';ti.value=mcNowHM();tr.appendChild(ti);card.appendChild(tr);
  const br=el('div','btnrow');
  const sv=el('button','btn primary','Save');sv.type='button';sv.id='qbSave';
  sv.onclick=async()=>{if(!QB.items.length){toast('Pick what you had');return;}if(sv.dataset.busy)return;sv.dataset.busy=1;
    try{await post('/api/quickbite',{items:QB.items,amount:QB.amount,reason:QB.reason,day:todayISO,
        mtime:($('#qbTime')&&$('#qbTime').value)||mcNowHM()});
      toast('Quick bite saved');sh.remove();loadMeals();loadNudge();}
    catch(e){sv.dataset.busy='';toast(e.message);}};
  const cx=el('button','btn ghost','Cancel');cx.type='button';cx.onclick=()=>sh.remove();
  br.appendChild(sv);br.appendChild(cx);card.appendChild(br);
  document.body.appendChild(sh);
}
/* ---- the gentle nudge: one line, during his usual snack band. */
async function loadNudge(){
  const box=$('#nowNudge');if(!box)return;box.innerHTML='';
  let j;try{j=await jget('/api/snacks/nudge');}catch(e){return;}
  if(!j.show)return;
  box.appendChild(el('span','',j.text));
  const x=el('button','nudgex','×');x.type='button';x.setAttribute('aria-label','Hide for today');
  x.onclick=async()=>{try{await post('/api/snacks/nudge',{});}catch(e){}box.innerHTML='';};
  box.appendChild(x);
}
/* ---- Meals tab: the slot chips from the one list, guessed until he taps. */
function mlSlotDraw(){
  const box=$('#mlSlots');if(!box)return;box.innerHTML='';
  mealSlotList(S.meal.slot).forEach(s=>{const b=el('button','chip'+(S.meal.slot===s?' sel':''),s);b.type='button';b.dataset.v=s;
    b.onclick=()=>{S.meal.slot=s;S.meal.slotSet=1;mlSlotDraw();};box.appendChild(b);});
}
async function mlSlotAuto(){
  if(S.meal.slotSet){mlSlotDraw();return;}
  const t=($('#ml_time')&&$('#ml_time').value)||nowHM();
  try{const j=await jget('/api/meals/slotguess?day='+mlDay()+'&time='+t);S.meal.slot=j.slot;}catch(e){}
  mlSlotDraw();
}
(function(){const t=$('#ml_time');if(t)t.addEventListener('change',()=>{if(!S.meal.slotSet)mlSlotAuto();});
  const b=$('#qbBtn');if(b)b.onclick=qbOpen;})();
function addDishToBasket(it){
  basket.push({item:it.n,portion:it.g+' g a portion',q:1,g:0,p:it.p,k:it.k,f:it.f,fm:it.fm,w:false,
    dish:it.dish,v:it.v,dg:it.g});
  renderBasket();
}
/* ---- the weekly snack review: counts, when, why, top items, his marks. */
let SRmode='week';
async function loadSnackReview(mode){
  if(mode)SRmode=mode;
  const box=$('#snackReview');if(!box)return;
  let j;try{j=await jget('/api/snacks/review?mode='+SRmode);}catch(e){return;}
  box.innerHTML='';
  const hd=el('div','dwtop');hd.appendChild(el('p','q','Snacks this week'));box.appendChild(hd);
  const mc=el('div','chips srmode');[['week','Week (Mon–Sun)'],['7d','Last 7 days']].forEach(m=>{
    const b=el('button','chip'+(SRmode===m[0]?' sel':''),m[1]);b.type='button';b.onclick=()=>loadSnackReview(m[0]);mc.appendChild(b);});
  box.appendChild(mc);
  box.appendChild(el('p','srrange',j.from_text+' to '+j.to_text));
  box.appendChild(el('p','srcount','Quick bites '+j.bites+' · Late snacks '+j.late));
  if(j.band)box.appendChild(el('p','srband',j.band.n+' of '+j.band.of+' between '+j.band.from+' and '+j.band.to));
  if(j.reasons.length)box.appendChild(el('p','srwhy','Why: '+j.reasons.map(r=>r[0]+' '+r[1]).join(' · ')));
  if(j.top.length){const t=el('div','srtop');
    j.top.forEach(x=>t.appendChild(el('p','srrow',x.item+' ×'+x.times+' · '+x.kcal+' kcal · '+x.protein+' g protein · '+x.share+' % of the week')));
    box.appendChild(t);box.appendChild(el('p','hint',j.sugar_note));}
  else box.appendChild(el('p','hint','No quick bites or late snacks in these days.'));
  j.swaps.forEach(s=>{const r=el('div','srswap');r.dataset.item=s.item;
    r.appendChild(el('p','',s.item+' ×'+s.times+(s.swap?(' — swap idea: '+s.swap):'')));
    if(j.mode==='week'){const ch=el('div','chips');['Keep','Swap','Stop'].forEach(m=>{const b=el('button','chip'+(s.mark===m?' sel':''),m);
        b.type='button';b.dataset.v=m;b.onclick=async()=>{try{await post('/api/snacks/mark',{week_start:j.from,item:s.item,mark:m});
          loadSnackReview();}catch(e){toast(e.message);}};ch.appendChild(b);});r.appendChild(ch);}
    box.appendChild(r);});
  if(j.followup.length){const f=el('div','srfollow');f.appendChild(el('p','lbl','Since last week'));
    j.followup.forEach(x=>f.appendChild(el('p','srrow',x.item+': '+x.before+' → '+x.after+' ('+x.mark+')')));box.appendChild(f);}
}

'''
E.append(("page code", '/* ---------- boot ---------- */\n', JS + '/* ---------- boot ---------- */\n'))

EDITS = E
JINJA = ("{{", "{%", "{#")


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("==================================================================")
    print("GutLog meals and snacks -> v" + VERSION)
    print("file : " + a.file)
    print("==================================================================")
    for label, old, new in EDITS:
        for tok in JINJA:
            if new.count(tok) > old.count(tok):
                print("FATAL: %s adds the Jinja token %r. Nothing written." % (label, tok))
                return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    bad = [(l, src.count(o)) for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        for l, c in bad:
            print("  %s: found %d times, need 1" % (l, c))
        print("Refusing to patch. Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    bak = a.file + ".bak-v3350-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    print("backup : " + bak)
    write(a.file, out)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, a.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(EDITS))
    print("Next:  python3 migrate_gutlog_v3350_snacks.py   (dry run), then --apply")
    print("       python3 test_v3350_snacks.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

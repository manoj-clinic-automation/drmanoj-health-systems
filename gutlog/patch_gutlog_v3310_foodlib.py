#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.30.0 -> v3.31.0  ::  GUTLOG_V3310_FOODLIB -- a food library by
weight, a nutrition table he can trust, and a time on every entry that he
can change.

WHAT WAS THERE (read-only check, 22/23-Sep)

  * library.portion was free text ("1 katori", "30 g") and protein / kcal /
    fibre were per THAT portion. Nothing numeric described a weight.
  * Logging was whole multiples of the stored portion only: the basket's
    +/- counter, small/medium/large on a new dish, 1/2 .. 2 on a recipe.
  * Every value was hardcoded or typed. No table, no source.
  * Times: the Day by day card could move any entry, and a scheduled or
    extra dose could be re-timed on the Now tab -- but a meal card logged at
    the moment Log was pressed with no way to say "this was lunch at 1",
    the meal rows showed a time that could not be tapped, and the Meds tab's
    "today so far" list showed dose times as plain text.

WHAT THIS DOES

  Food library
  * Every food gets a numeric basis: portion_qty + portion_unit (g or ml)
    and per-100 values b_protein / b_kcal / b_fibre. The existing
    protein / kcal / fibre columns stay the PER-PORTION values that every
    reader already uses, and lib_apply() is the one place that works one out
    from the other -- so the meal card, the basket and the recipe log all go
    on reading the same columns and nothing that reads them changes.
  * weighed_dry: for dal, rice, poha, oats. The basis is then per 100 g DRY,
    so the dry weight he puts on the scale is exact and no cooked-yield
    factor is guessed. The field says "dry weight" beside it.
  * source ('own', 'USDA', 'estimated') and source_date on every food,
    shown in the list. lib_apply() decides it on the server: values that
    match the table row he picked are 'USDA'; values he changed are 'own';
    a save that changes no value keeps what was there. Nothing ever writes
    table values over his without his tap.
  * The table is food_table_usda.json beside this file, built by
    build_food_table.py from USDA FoodData Central SR Legacy (public domain,
    CC0). IFCT 2017 is not bundled: no licence-clean machine-readable copy
    exists. No live API call -- a lookup is a search of that file.
  * lib_backfill(): the migration. Library only. A portion that starts with
    a number and unit ("30 g", "100 ml") gets its basis derived exactly; a
    household measure (katori, roti, slice, egg, glass, tsp, tbsp) gets its
    grams from a small table and portion_est=1; anything else gets no weight
    and portion_est=1. The per-portion values are NOT touched, and meals
    are not touched at all: a meal keeps the values recorded when it was
    logged. Runs from _migrate() (schema 3.3.4 -> 3.3.5), from _seed(), and
    before /api/library reads, so a food added by any other route is filled
    in the same way.

  Logging by weight
  * The basket row and the Now card's "Also had" rows keep their counter and
    gain a grams/ml field. Type 40 and the server scales from the per-100
    basis (lib_weigh); the client never supplies a weighed item's numbers.

  Times
  * teOpen(): one way to change the time of anything logged, using the same
    two lists every time box on the page uses (v3.27.0), never the phone's
    own dialog. Wired to: meal rows on the Meals tab (any day), meal rows on
    the Now tab, the Meds tab's today list, and the Review tab's recent-dose
    table. All of them save through /api/retime, so every change is audited
    in `edits` exactly as the Day by day card's are.
  * The meal card on the Now tab gets a Time field, default now, and Save on
    an edited meal keeps or changes it (an edit that moves it is recorded in
    `edits` too). A meal cannot be logged later than now today -- the same
    rule /api/retime and /api/now/dose already apply.
  * The Meals and Meds time boxes reset to now each time the tab opens,
    unless he has set one.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3310_FOODLIB"
PREV = "GUTLOG_V3300_MIRRORSTALE"
VERSION = "3.31.0"

# ---------------------------------------------------------------- 1. header
HEAD_OLD = 'GUTLOG_V3300_MIRRORSTALE -- the Now tab says when the Drive mirror is stale.\n'
HEAD_NEW = ('GUTLOG_V3300_MIRRORSTALE -- the Now tab says when the Drive mirror is stale.\n'
            'GUTLOG_V3310_FOODLIB -- foods by weight with a sourced table; every time editable.\n')

# --------------------------------------------------------------- 2. version
VER_OLD = ('APP_VERSION = "3.30.0"   # GUTLOG_V3300_MIRRORSTALE GUTLOG_V3290_NUTRITION '
           'GUTLOG_V3280_PLANS GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS GUTLOG_V3270_TIMEPICK '
           'GUTLOG_V3260_TRIALS\n')
VER_NEW = ('APP_VERSION = "3.31.0"   # GUTLOG_V3310_FOODLIB GUTLOG_V3300_MIRRORSTALE '
           'GUTLOG_V3290_NUTRITION GUTLOG_V3280_PLANS GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS '
           'GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS\n')

# ---------------------------------------------------------------- 3. SCHEMA
SCH_OLD = ('''CREATE TABLE IF NOT EXISTS library (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cat TEXT, item TEXT UNIQUE, portion TEXT,
  protein REAL, kcal REAL, fibre REAL, fodmap TEXT, status TEXT DEFAULT '',
  tags TEXT DEFAULT '', fav INTEGER DEFAULT 0, note TEXT DEFAULT '', created TEXT);
''')
SCH_NEW = ('''CREATE TABLE IF NOT EXISTS library (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cat TEXT, item TEXT UNIQUE, portion TEXT,
  protein REAL, kcal REAL, fibre REAL, fodmap TEXT, status TEXT DEFAULT '',
  tags TEXT DEFAULT '', fav INTEGER DEFAULT 0, note TEXT DEFAULT '', created TEXT,
  portion_qty REAL, portion_unit TEXT DEFAULT '', basis_qty REAL DEFAULT 100,
  basis_unit TEXT DEFAULT '', b_protein REAL, b_kcal REAL, b_fibre REAL,
  weighed_dry INTEGER DEFAULT 0, portion_est INTEGER DEFAULT 0,
  source TEXT DEFAULT '', source_date TEXT DEFAULT '', source_ref TEXT DEFAULT '');
''')

SV_OLD = 'SCHEMA_VERSION = "3.3.4"   # GUTLOG_V330_PHASE_A '
SV_NEW = 'SCHEMA_VERSION = "3.3.5"   # GUTLOG_V3310_FOODLIB GUTLOG_V330_PHASE_A '

COLS_OLD = '''    ("rec_labs", "doc_id", "INTEGER"),
]
'''
COLS_NEW = '''    ("rec_labs", "doc_id", "INTEGER"),
    # GUTLOG_V3310_FOODLIB -- a numeric basis for every food. Also in SCHEMA,
    # so a new database has them without this step.
    ("library", "portion_qty", "REAL"),
    ("library", "portion_unit", "TEXT DEFAULT ''"),
    ("library", "basis_qty", "REAL DEFAULT 100"),
    ("library", "basis_unit", "TEXT DEFAULT ''"),
    ("library", "b_protein", "REAL"),
    ("library", "b_kcal", "REAL"),
    ("library", "b_fibre", "REAL"),
    ("library", "weighed_dry", "INTEGER DEFAULT 0"),
    ("library", "portion_est", "INTEGER DEFAULT 0"),
    ("library", "source", "TEXT DEFAULT ''"),
    ("library", "source_date", "TEXT DEFAULT ''"),
    ("library", "source_ref", "TEXT DEFAULT ''"),
]
'''

MIG_OLD = '''    for key, val in _V330_SETTINGS:
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
'''
MIG_NEW = '''    # GUTLOG_V3310_FOODLIB -- the library gets its weights. Library only;
    # meals keep the values recorded when they were logged.
    lib_backfill(con)

    for key, val in _V330_SETTINGS:
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
'''

SEED_OLD = '''    for i, m in enumerate(PRN_SEED):
        con.execute("INSERT OR IGNORE INTO prnmeds(name,sort) VALUES(?,?)", (m, i))
'''
SEED_NEW = '''    lib_backfill(con)   # GUTLOG_V3310_FOODLIB
    for i, m in enumerate(PRN_SEED):
        con.execute("INSERT OR IGNORE INTO prnmeds(name,sort) VALUES(?,?)", (m, i))
'''

# ------------------------------------------------------- 4. library routes
LIB_OLD = '''@app.route("/api/library")
@login_required
def api_library():
    return jsonify([dict(r) for r in db().execute(
        "SELECT * FROM library ORDER BY cat, item")])

@app.route("/api/library", methods=["POST"])
@login_required
def api_library_add():
    d = J()
    item = (d.get("item") or "").strip()[:80]
    if not item: return jsonify(ok=False, err="Name the food."), 400
    if d.get("fodmap") not in FMAP: return jsonify(ok=False, err="Pick a FODMAP flag."), 400
    def num(k):
        try: return round(float(d.get(k) or 0), 1)
        except (TypeError, ValueError): return 0
    try:
        insert("library", ["cat","item","portion","protein","kcal","fibre","fodmap","status","fav","tags","note"],
               [(d.get("cat") or "F")[:1], item, (d.get("portion") or "")[:60],
                num("protein"), num("kcal"), num("fibre"), d["fodmap"],
                d.get("status") or "", 1 if d.get("fav") else 0,
                (d.get("tags") or "")[:40], note(d, "note", 200)])
    except sqlite3.IntegrityError:
        return jsonify(ok=False, err="That food already exists."), 400
    rid = db().execute("SELECT id FROM library WHERE item=?", (item,)).fetchone()["id"]
    return jsonify(ok=True, id=rid)

@app.route("/api/library/<int:lid>", methods=["POST"])
@login_required
def api_library_edit(lid):
    d = J()
    r = db().execute("SELECT * FROM library WHERE id=?", (lid,)).fetchone()
    if not r: abort(404)
    def num(k, cur):
        if k not in d: return cur
        try: return round(float(d.get(k) or 0), 1)
        except (TypeError, ValueError): return cur
    fodmap = d.get("fodmap", r["fodmap"])
    if fodmap not in FMAP: fodmap = r["fodmap"]
    db().execute("""UPDATE library SET item=?,portion=?,protein=?,kcal=?,fibre=?,
        fodmap=?,status=?,fav=?,tags=?,note=? WHERE id=?""",
        ((d.get("item") or r["item"]).strip()[:80], d.get("portion", r["portion"]),
         num("protein", r["protein"]), num("kcal", r["kcal"]), num("fibre", r["fibre"]),
         fodmap, d.get("status", r["status"]),
         (1 if d["fav"] else 0) if "fav" in d else r["fav"],
         d.get("tags", r["tags"]), d.get("note", r["note"]), lid))
    db().commit(); return jsonify(ok=True)
'''
LIB_NEW = '''@app.route("/api/library")
@login_required
def api_library():
    # GUTLOG_V3310_FOODLIB -- a food added by any route (a new dish, a recipe,
    # the seeder) gets its weight filled in the same way before it is shown.
    lib_backfill(db())
    db().commit()
    uses = lib_uses()
    out = []
    for r in db().execute("SELECT * FROM library ORDER BY cat, item"):
        x = dict(r)
        x["uses"] = uses.get(x["item"], 0)
        out.append(x)
    return jsonify(out)

@app.route("/api/library", methods=["POST"])
@login_required
def api_library_add():
    d = J()
    item = (d.get("item") or "").strip()[:80]
    if not item: return jsonify(ok=False, err="Name the food."), 400
    if d.get("fodmap") not in FMAP: return jsonify(ok=False, err="Pick a FODMAP flag."), 400
    f, err = lib_apply(d, None)
    if err: return jsonify(ok=False, err=err), 400
    keys = sorted(f)
    try:
        insert("library", ["cat","item","portion","fodmap","status","fav","tags","note"] + keys,
               [(d.get("cat") or "F")[:1], item, (d.get("portion") or "")[:60], d["fodmap"],
                d.get("status") or "", 1 if d.get("fav") else 0,
                (d.get("tags") or "")[:40], note(d, "note", 200)] + [f[k] for k in keys])
    except sqlite3.IntegrityError:
        return jsonify(ok=False, err="That food already exists."), 400
    rid = db().execute("SELECT id FROM library WHERE item=?", (item,)).fetchone()["id"]
    return jsonify(ok=True, id=rid, source=f.get("source"))

@app.route("/api/library/<int:lid>", methods=["POST"])
@login_required
def api_library_edit(lid):
    d = J()
    r = db().execute("SELECT * FROM library WHERE id=?", (lid,)).fetchone()
    if not r: abort(404)
    f, err = lib_apply(d, r)
    if err: return jsonify(ok=False, err=err), 400
    fodmap = d.get("fodmap", r["fodmap"])
    if fodmap not in FMAP: fodmap = r["fodmap"]
    db().execute("""UPDATE library SET item=?,portion=?,
        fodmap=?,status=?,fav=?,tags=?,note=? WHERE id=?""",
        ((d.get("item") or r["item"]).strip()[:80], (d.get("portion", r["portion"]) or "")[:60],
         fodmap, d.get("status", r["status"]),
         (1 if d["fav"] else 0) if "fav" in d else r["fav"],
         d.get("tags", r["tags"]), d.get("note", r["note"]), lid))
    if f:
        keys = sorted(f)
        db().execute("UPDATE library SET " + ", ".join(k + "=?" for k in keys) + " WHERE id=?",
                     [f[k] for k in keys] + [lid])
    db().commit()
    x = db().execute("SELECT source FROM library WHERE id=?", (lid,)).fetchone()
    return jsonify(ok=True, source=x["source"])
'''

# ----------------------------------------------------- 5. the basket route
MEALS_OLD = '''    clean, p, k, f, fs = [], 0.0, 0.0, 0.0, 0.0
    for it in items[:20]:
        try:
            q = max(0.25, min(10.0, float(it.get("q", 1))))
            ip, ik, ifb = float(it.get("p", 0)), float(it.get("k", 0)), float(it.get("f", 0))
        except (TypeError, ValueError):
            continue
        fm = it.get("fm") if it.get("fm") in FMAP else "M"
        n = (it.get("n") or "?").strip()[:80]
        clean.append({"n": n, "q": q, "p": ip, "k": ik, "f": ifb, "fm": fm})
        p += q * ip; k += q * ik; f += q * ifb; fs += q * FMAP[fm]
    if not clean: return jsonify(ok=False, err="Add at least one item."), 400
    insert("meals", ["day","mtime","slot","items","protein","kcal","fibre","fscore","notes"],
           [d.get("day") or today(), d.get("mtime") or now_hm(), d.get("slot") or "Meal",
            json.dumps(clean), round(p,1), round(k), round(f,1), round(fs,2), note(d)])
'''
MEALS_NEW = '''    # GUTLOG_V3310_FOODLIB -- the date and time are checked as /api/retime
    # checks them, and an item sent by weight is worked out HERE from the
    # food's per-100 basis; the page never supplies a weighed item's numbers.
    day = d.get("day") or today()
    mtime = d.get("mtime") or now_hm()
    if not _valid_day(day) or not _valid_hm(mtime):
        return jsonify(ok=False, err="Pick a real date and time."), 400
    if day == today() and mtime > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400
    lib = None
    clean, p, k, f, fs = [], 0.0, 0.0, 0.0, 0.0
    for it in items[:20]:
        if it.get("g") not in (None, "", 0, "0"):
            if lib is None:
                lib = _lib_map()
            n = (it.get("n") or "").strip()[:80]
            row = lib.get(n)
            w = lib_weigh(row, it.get("g")) if row else None
            if not w:
                return jsonify(ok=False, err="No weight is set for " + (n or "that food")
                               + " -- set it in the food list, or log it by portion."), 400
            clean.append(w)
            p += w["q"] * w["p"]; k += w["q"] * w["k"]; f += w["q"] * w["f"]
            fs += w["q"] * FMAP[w["fm"]]
            continue
        try:
            q = max(0.25, min(10.0, float(it.get("q", 1))))
            ip, ik, ifb = float(it.get("p", 0)), float(it.get("k", 0)), float(it.get("f", 0))
        except (TypeError, ValueError):
            continue
        fm = it.get("fm") if it.get("fm") in FMAP else "M"
        n = (it.get("n") or "?").strip()[:80]
        clean.append({"n": n, "q": q, "p": ip, "k": ik, "f": ifb, "fm": fm})
        p += q * ip; k += q * ik; f += q * ifb; fs += q * FMAP[fm]
    if not clean: return jsonify(ok=False, err="Add at least one item."), 400
    insert("meals", ["day","mtime","slot","items","protein","kcal","fibre","fscore","notes"],
           [day, mtime, d.get("slot") or "Meal",
            json.dumps(clean), round(p,1), round(k), round(f,1), round(fs,2), note(d)])
'''

# ------------------------------------------------ 6. card items by weight
ITEMS_OLD = '''    out, missing = [], []
    for name, q in pairs:
        r = lib.get(name)
        if not r:
            missing.append(name)
            continue
        try:
'''
ITEMS_NEW = '''    out, missing = [], []
    for pr in pairs:
        name, q = pr[0], pr[1]
        r = lib.get(name)
        if not r:
            missing.append(name)
            continue
        # GUTLOG_V3310_FOODLIB -- an item with a weight is worked out from the
        # food's per-100 basis; one without stays a multiple of the portion.
        if len(pr) > 2 and pr[2] not in (None, "", 0, "0"):
            w = lib_weigh(r, pr[2])
            if w:
                out.append(w)
            else:
                missing.append(name + " (no weight set)")
            continue
        try:
'''

LOG_OLD = '''    extra = [(x.get("n"), x.get("q", 1)) for x in (d.get("extra") or []) if x.get("n")][:20]
    items, missing = _meal_items(pairs + extra, lib)
    if not items:
        return {"ok": False, "err": "Nothing to log." + (
            " Not in the food list: " + ", ".join(missing) if missing else "")}, 400
    day = d.get("day") or today()
    mtime = d.get("mtime") or now_hm()
    if not _valid_day(day) or not _valid_hm(mtime):
        return {"ok": False, "err": "Pick a real date and time."}, 400
'''
LOG_NEW = '''    extra = [(x.get("n"), x.get("q", 1), x.get("g")) for x in (d.get("extra") or [])
             if x.get("n")][:20]
    items, missing = _meal_items(pairs + extra, lib)
    if not items:
        return {"ok": False, "err": "Nothing to log." + (
            " Not in the food list: " + ", ".join(missing) if missing else "")}, 400
    # GUTLOG_V3310_FOODLIB -- an edited meal keeps its own day unless told
    # otherwise, so its time is checked against THAT day, not today's clock.
    prior = None
    if replace_id:
        prior = db().execute("SELECT * FROM meals WHERE id=?", (replace_id,)).fetchone()
    day = d.get("day") or (prior["day"] if prior else today())
    mtime = d.get("mtime") or ((prior["mtime"] or now_hm()) if prior else now_hm())
    if not _valid_day(day) or not _valid_hm(mtime):
        return {"ok": False, "err": "Pick a real date and time."}, 400
    if day == today() and mtime > now_hm():
        return {"ok": False, "err": "That time has not come yet today."}, 400
'''

REPL_OLD = '''        mid = replace_id
    else:
'''
REPL_NEW = '''        mid = replace_id
        # GUTLOG_V3310_FOODLIB -- a time changed from the meal card is a
        # retime like any other, and is recorded the same way.
        if (day, mtime) != (old["day"], old["mtime"] or ""):
            db().execute(
                "INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("meals", replace_id, old["day"], old["mtime"] or "", day, mtime, now_s()))
    else:
'''

META_OLD = '''                  json.dumps([{"n": n, "q": q} for n, q in extra])))
'''
META_NEW = '''                  json.dumps([dict({"n": x[0], "q": x[1]}, **({"g": x[2]} if x[2] else {}))
                              for x in extra])))
'''

# ---------------------------------------- 7. lookup payloads carry weights
CARDS_OLD = '''    nut = dict((n, {"p": r["protein"] or 0, "k": r["kcal"] or 0, "f": r["fibre"] or 0,
                    "fm": r["fodmap"]}) for n, r in lib.items())
'''
CARDS_NEW = '''    nut = dict((n, dict({"p": r["protein"] or 0, "k": r["kcal"] or 0, "f": r["fibre"] or 0,
                         "fm": r["fodmap"]}, **lib_wt(r))) for n, r in lib.items())
'''

SEARCH_OLD = '''    return jsonify(foods=[{"n": n, "portion": lib[n]["portion"], "p": lib[n]["protein"],
                           "k": lib[n]["kcal"], "est": "estimated" in (lib[n]["tags"] or ""),
                           "recent": n in recent} for n in names[:30]])
'''
SEARCH_NEW = '''    return jsonify(foods=[dict({"n": n, "portion": lib[n]["portion"], "p": lib[n]["protein"],
                                "k": lib[n]["kcal"], "est": "estimated" in (lib[n]["tags"] or ""),
                                "recent": n in recent}, **lib_wt(lib[n])) for n in names[:30]])
'''

DOSES_OLD = '''    rows = db().execute("SELECT medicine, dtime FROM doses WHERE day=? ORDER BY dtime", (day,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["medicine"], []).append(r["dtime"] or "")
    return jsonify([{"medicine": m, "times": ts} for m, ts in out.items()])
'''
DOSES_NEW = '''    rows = db().execute("SELECT id, medicine, dtime FROM doses WHERE day=? ORDER BY dtime",
                        (day,)).fetchall()
    out, ids = {}, {}
    for r in rows:
        out.setdefault(r["medicine"], []).append(r["dtime"] or "")
        ids.setdefault(r["medicine"], []).append(r["id"])   # GUTLOG_V3310_FOODLIB
    return jsonify([{"medicine": m, "times": ts, "ids": ids[m], "day": day}
                    for m, ts in out.items()])
'''

EXPORT_OLD = '''                vals[i] = "; ".join(f'{it["n"]} x{it["q"]:g}' for it in json.loads(vals[i] or "[]"))
'''
EXPORT_NEW = '''                vals[i] = "; ".join(_export_item(it) for it in json.loads(vals[i] or "[]"))
'''
EXPORT2_OLD = '''        "library": "cat,item,portion,protein,kcal,fibre,fodmap,status,tags,fav,note",
    }.get(table)
'''
EXPORT2_NEW = '''        "library": "cat,item,portion,protein,kcal,fibre,fodmap,status,tags,fav,note,"
                   "portion_qty,portion_unit,b_protein,b_kcal,b_fibre,weighed_dry,portion_est,"
                   "source,source_date,source_ref",
    }.get(table)
'''

# ---------------------------------------------------- 8. the new code block
CODE = r'''# ------------------------------------------------------------ food library
# GUTLOG_V3310_FOODLIB. A numeric basis for every food, a bundled table to
# fill it from, and the one place that decides where a food's numbers came
# from. The per-portion columns (protein, kcal, fibre) stay what every meal
# reader uses; lib_apply() works them out from the per-100 basis whenever a
# weight is known, so there is no second calculation to drift.
import re as _re

LIB_UNITS = ("g", "ml")
LIB_B = ("b_protein", "b_kcal", "b_fibre")
LIB_P = ("protein", "kcal", "fibre")
LIB_NUT_KEYS = LIB_B + LIB_P + ("portion_qty", "fdc", "portion_unit", "weighed_dry", "weight_ok")
LIB_SOURCES = ("own", "USDA", "estimated")
# Household measures -> grams or ml. ONLY used to put a first weight on an
# old portion text, and every food filled from here is marked estimated so
# the list says so and he can correct it.
LIB_HOUSEHOLD = (("katori", 150.0, "g"), ("roti", 40.0, "g"), ("chapati", 40.0, "g"),
                 ("slice", 30.0, "g"), ("egg", 50.0, "g"), ("glass", 250.0, "ml"),
                 ("tbsp", 15.0, "g"), ("tsp", 5.0, "g"))
_LIB_NUM = r"(\d+(?:\.\d+)?|\d+/\d+|½|¼|¾)"


def _lib_num(s):
    s = (s or "").strip()
    if not s:
        return 1.0
    if s in ("½", "¼", "¾"):
        return {"½": 0.5, "¼": 0.25, "¾": 0.75}[s]
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b) if float(b) else 1.0
    return float(s)


def lib_parse_portion(text):
    """(qty, unit, estimated) from an old portion text. A text that STARTS
    with a number and a unit is his own number and is exact; a "~150 g"
    inside the text or a household word gives an estimate; anything else
    gives no weight at all rather than a guess."""
    t = (text or "").strip().lower()
    m = _re.match(r"^" + _LIB_NUM + r"\s*(g|gm|gms|gram|grams|ml)\b", t)
    if m:
        return _lib_num(m.group(1)), ("ml" if m.group(2) == "ml" else "g"), 0
    m = _re.search(r"~\s*" + _LIB_NUM + r"\s*(g|ml)\b", t)
    if m:
        return _lib_num(m.group(1)), m.group(2), 1
    for word, grams, unit in LIB_HOUSEHOLD:
        m = _re.search(r"(?:^|[\s(])(?:" + _LIB_NUM + r"\s*)?" + word + r"s?\b", t)
        if m:
            return _lib_num(m.group(1)) * grams, unit, 1
    return None, "", 1


def lib_backfill(con):
    """The migration, library only, idempotent: it touches only rows whose
    source is still empty, and it never changes their per-portion values.
    Meals are not read or written here."""
    rows = con.execute("SELECT id, portion, protein, kcal, fibre FROM library "
                       "WHERE COALESCE(source,'')=''").fetchall()
    if not rows:
        return None
    n = {"exact": 0, "estimated": 0, "no_weight": 0}
    stamp = today()
    for r in rows:
        qty, unit, est = lib_parse_portion(r[1])
        if qty and qty > 0:
            b = [round(float(v) * 100.0 / qty, 2) if v is not None else None
                 for v in (r[2], r[3], r[4])]
            con.execute("UPDATE library SET portion_qty=?, portion_unit=?, basis_qty=100, "
                        "basis_unit=?, b_protein=?, b_kcal=?, b_fibre=?, portion_est=?, "
                        "source='estimated', source_date=? WHERE id=?",
                        (round(qty, 1), unit, unit, b[0], b[1], b[2], est, stamp, r[0]))
            n["estimated" if est else "exact"] += 1
        else:
            con.execute("UPDATE library SET portion_est=1, source='estimated', "
                        "source_date=? WHERE id=?", (stamp, r[0]))
            n["no_weight"] += 1
    prev = con.execute("SELECT value FROM settings WHERE key='lib_backfill_v3310'").fetchone()
    try:
        tot = json.loads(prev[0]) if prev else {}
    except ValueError:
        tot = {}
    for k, v in n.items():
        tot[k] = int(tot.get(k, 0)) + v
    tot["at"] = now_s()
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('lib_backfill_v3310',?)",
                (json.dumps(tot),))
    return n


def lib_uses(days=90):
    """How often each food was logged lately, for 'most used first'."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out = {}
    for r in db().execute("SELECT items FROM meals WHERE day>=?", (since,)):
        try:
            for it in json.loads(r["items"] or "[]"):
                if it.get("n"):
                    out[it["n"]] = out.get(it["n"], 0) + 1
        except (ValueError, AttributeError):
            pass
    return out


def lib_wt(r):
    """What a page needs to log a food by weight."""
    pq = r["portion_qty"]
    b = [r["b_protein"], r["b_kcal"], r["b_fibre"]]
    ok = bool(pq and pq > 0 and any(v is not None for v in b))
    return {"w": ok, "pq": pq, "u": r["portion_unit"] or "g", "dry": bool(r["weighed_dry"]),
            "bp": b[0] or 0, "bk": b[1] or 0, "bf": b[2] or 0}


def lib_weigh(r, g):
    """One meal item as grams (or ml) of a library food, from its per-100
    basis. None when the food has no weight set -- never a guess. q stays
    the multiple of the portion, so q*p is exactly basis*grams/100."""
    try:
        g = float(g)
    except (TypeError, ValueError):
        return None
    pq = r["portion_qty"]
    bq = r["basis_qty"] or 100.0
    b = [r["b_protein"], r["b_kcal"], r["b_fibre"]]
    if not (0 < g <= 5000) or not pq or pq <= 0 or all(v is None for v in b):
        return None
    b = [float(v or 0) for v in b]
    it = {"n": r["item"], "q": round(g / pq, 4), "g": round(g, 1),
          "u": r["portion_unit"] or "g",
          "p": b[0] * pq / bq, "k": b[1] * pq / bq, "f": b[2] * pq / bq,
          "fm": r["fodmap"] if r["fodmap"] in FMAP else "M"}
    if r["weighed_dry"]:
        it["dry"] = 1
    return it


def _lnum(v, hi):
    if v is None or str(v).strip() == "":
        return None
    x = float(v)
    if x < 0 or x > hi:
        raise ValueError("out of range")
    return x


def _lclose(a, b, tol):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def lib_apply(d, cur=None):
    """The one place that turns a save into library columns: the portion
    weight, the per-100 basis, the per-portion values every meal reads, and
    the source. Returns (fields, error). No nutritional key sent (a star
    tap) means nothing nutritional changes.

    Source: values equal to the table row he picked are 'USDA'; values that
    differ from what was stored are 'own'; a save that changes no value
    keeps the source it had, so a later lookup can never turn 'own' back."""
    if cur is not None and not any(k in d for k in LIB_NUT_KEYS):
        return {}, None
    try:
        if cur is None or "portion_qty" in d:
            pq = _lnum(d.get("portion_qty"), 5000.0)
        else:
            pq = cur["portion_qty"]
        if pq is not None and pq <= 0:
            pq = None
        unit = d.get("portion_unit")
        if unit not in LIB_UNITS:
            unit = ((cur["portion_unit"] if cur is not None else "") or "g")
        if pq:
            b = []
            for k in LIB_B:
                if k in d:
                    b.append(_lnum(d.get(k), 1000.0))
                elif cur is not None and cur[k] is not None:
                    b.append(cur[k])
                elif cur is not None and cur["portion_qty"] and cur[LIB_P[LIB_B.index(k)]] is not None:
                    b.append(round(cur[LIB_P[LIB_B.index(k)]] * 100.0 / cur["portion_qty"], 2))
                else:
                    b.append(None)
            per = [round(v * pq / 100.0, 2) if v is not None else None for v in b]
        else:
            b = [None, None, None]
            per = [(_lnum(d.get(k), 5000.0) if k in d else (cur[k] if cur is not None else None))
                   for k in LIB_P]
    except (TypeError, ValueError):
        return None, "Numbers only, and not negative."
    f = {"portion_qty": pq, "portion_unit": unit if pq else "", "basis_qty": 100.0,
         "basis_unit": unit if pq else "", "b_protein": b[0], "b_kcal": b[1], "b_fibre": b[2],
         "protein": per[0], "kcal": per[1], "fibre": per[2]}
    if "weighed_dry" in d:
        f["weighed_dry"] = 1 if d.get("weighed_dry") else 0
    if cur is None:
        f["portion_est"] = 0 if pq else 1
    elif (("portion_qty" in d and not _lclose(pq, cur["portion_qty"], 0.001))
          or d.get("weight_ok")):
        f["portion_est"] = 0 if pq else 1

    t = None
    if d.get("fdc") not in (None, ""):
        try:
            t = food_table()[1].get(int(d.get("fdc")))
        except (TypeError, ValueError):
            t = None
    stamp = today()
    if t is not None and pq and all(
            (b[i] is None) if t[2 + i] is None else _lclose(b[i], t[2 + i], 0.051)
            for i in range(3)):
        f.update(source="USDA", source_date=stamp,
                 source_ref="USDA SR Legacy #%d: %s" % (t[0], t[1]))
    elif cur is None:
        f.update(source="own", source_date=stamp, source_ref="")
    else:
        had_b = any(cur[k] is not None for k in LIB_B) and cur["portion_qty"]
        if pq and had_b:
            same = all(_lclose(b[i], cur[LIB_B[i]], 0.0051) for i in range(3))
        else:
            same = all(_lclose(per[i], cur[LIB_P[i]], 0.06) for i in range(3))
        if not same:
            f.update(source="own", source_date=stamp, source_ref="")
    return f, None


def _export_item(it):
    if it.get("g"):
        return "%s %g %s%s" % (it["n"], it["g"], it.get("u") or "g", " dry" if it.get("dry") else "")
    return "%s x%g" % (it["n"], it["q"])


# ------------------------------------------------------------ the table
FOOD_TABLE_PATH = (os.environ.get("GUTLOG_FOOD_TABLE")
                   or os.path.join(BASE, "food_table_usda.json"))
_FOOD_TABLE = {}
# Indian names -> the words the table uses. Phrases first ("moong dal" is
# mung, not lentils), then single words. Unknown words pass through as they
# are, so an English name finds itself.
FOOD_PHRASES = (("moong dal", "mung"), ("mung dal", "mung"), ("chana dal", "chickpeas"),
                ("urad dal", "mungo"), ("toor dal", "pigeonpeas"), ("arhar dal", "pigeonpeas"),
                ("masoor dal", "lentils"), ("kabuli chana", "chickpeas"),
                ("whole wheat", "wheat whole"))
FOOD_WORDS = {"dal": "lentils", "daal": "lentils", "masoor": "lentils", "moong": "mung",
              "urad": "mungo", "toor": "pigeonpeas", "arhar": "pigeonpeas",
              "chana": "chickpeas", "chole": "chickpeas", "rajma": "kidney beans",
              "atta": "wheat flour whole", "jowar": "sorghum", "bajra": "millet",
              "dahi": "yogurt", "curd": "yogurt", "chawal": "rice", "anda": "egg",
              "doodh": "milk", "badam": "almonds", "kaju": "cashew", "palak": "spinach",
              "aloo": "potatoes", "gobhi": "cauliflower", "bhindi": "okra",
              "tamatar": "tomatoes", "pyaz": "onions", "kheera": "cucumber",
              "amrood": "guavas", "kela": "bananas", "papita": "papaya", "seb": "apples"}
FOOD_DROP = frozenset("a an the of and with in on katori bowl plate glass cup homemade home "
                      "made plain fresh my small medium large".split())
FOOD_NOISE = ("babyfood", "candies", "restaurant", "fast foods", "snacks", "cereals ready-to-eat",
              "infant formula", "puddings", "pastry", "beverages", "soup", "sauce",
              "salad dressing", "frozen novelties", "cookies", "crackers", "formulated bar",
              "breakfast bars", "potatoes, mashed", "school lunch", "meatless", "fruit salad")


def _fstem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def food_table():
    """(document, rows by fdc id), read once per worker. No file means no
    lookups -- the editor then says so and the values stay his to type."""
    if "doc" not in _FOOD_TABLE:
        try:
            with open(FOOD_TABLE_PATH, encoding="utf-8") as fh:
                doc = json.load(fh)
            rows = doc.get("foods") or []
            _FOOD_TABLE["byid"] = dict((int(r[0]), r) for r in rows)
            _FOOD_TABLE["words"] = [frozenset(_fstem(w) for w in _re.findall(r"[a-z]+", r[1].lower()))
                                    for r in rows]
            _FOOD_TABLE["doc"] = doc
        except (OSError, ValueError, TypeError, IndexError):
            _FOOD_TABLE.update(doc=None, byid={}, words=[])
    return _FOOD_TABLE["doc"], _FOOD_TABLE["byid"]


def food_lookup(q, dry=False, limit=8):
    """Deterministic search of the bundled table: every query word must be
    in the name for a full match; brand, restaurant and snack rows sort
    last; a food whose name STARTS with the word sorts first; for a food
    weighed dry the raw row comes first."""
    doc, _ = food_table()
    if not doc:
        return []
    s = " " + " ".join(_re.findall(r"[a-z]+", (q or "").lower())) + " "
    for a, b in FOOD_PHRASES:
        s = s.replace(" " + a + " ", " " + b + " ")
    toks = []
    for w in s.split():
        if w in FOOD_DROP:
            continue
        for x in FOOD_WORDS.get(w, w).split():
            x = _fstem(x)
            if x not in toks:
                toks.append(x)
    if not toks:
        return []
    found = []
    for r, ws in zip(doc["foods"], _FOOD_TABLE["words"]):
        m = sum(1 for t in toks if t in ws)
        if not m:
            continue
        low = r[1].lower()
        noise = 1 if (low.startswith(FOOD_NOISE)
                      or _re.search(r"\b(?!USDA\b)[A-Z]{3,}\b", r[1])) else 0
        first = _re.findall(r"[a-z]+", low)
        primary = 0 if (first and _fstem(first[0]) in toks) else 1
        rawp = (0 if "raw" in ws else 1) if dry else 0
        found.append(((-m, noise, primary, rawp, len(low)), r, m == len(toks)))
    found.sort(key=lambda x: x[0])
    return [{"fdc": r[0], "desc": r[1], "protein": r[2], "kcal": r[3], "fibre": r[4],
             "full": full} for _, r, full in found[:limit]]


@app.route("/api/foodtable")
@login_required
def api_foodtable():
    doc, _ = food_table()
    q = (request.args.get("q") or "").strip()[:80]
    dry = request.args.get("dry") in ("1", "true")
    if not doc:
        return jsonify(available=False, matches=[],
                       text="No food table on this server; type the values.")
    return jsonify(available=True, name=doc.get("name"), licence=doc.get("licence"),
                   basis=doc.get("basis"), matches=food_lookup(q, dry) if q else [])


'''
CODE_OLD = '# ------------------------------------------------------------------ recipes\n'
CODE_NEW = CODE + CODE_OLD

# ================================================================== the page
CSS_OLD = '.mlmeal .macts{display:flex;gap:5px;margin-left:auto}\n'
CSS_NEW = ('.mlmeal .macts{display:flex;gap:5px;margin-left:auto}\n'
           '/* GUTLOG_V3310_FOODLIB -- weight fields, the table lookup, time buttons.\n'
           '   Sized to sit inside 300px, like everything else on this page. */\n'
           '.bk{flex-wrap:wrap}\n'
           '.bkw{flex:1 0 100%;display:flex;align-items:center;gap:6px;flex-wrap:wrap;\n'
           '  font-size:12.5px;color:var(--muted);padding:3px 0 0 18px}\n'
           '.mrow .bkw{padding-left:0;margin:0 0 6px}\n'
           '.bkw input.bkg{width:76px;flex:0 0 76px;padding:6px 8px;font-size:15px}\n'
           '.dryw{color:var(--ink);font-size:12.5px}\n'
           '.chip.tbtn{padding:4px 10px;font-size:13.5px;font-weight:700;font-variant-numeric:tabular-nums}\n'
           '.chip.rvt{padding:2px 7px;font-size:12.5px}\n'
           '.mth{display:flex;align-items:center;gap:8px;flex-wrap:wrap}\n'
           '.mlmeal .chip.tbtn{margin-right:2px}\n'
           '.todayrow{flex-wrap:wrap}.todayrow .tms{display:flex;flex-wrap:wrap;gap:5px}\n'
           '.lfed input[type=text],.lfed input[type=number]{margin-bottom:8px}\n'
           '.lfed .row3 input{margin-bottom:0}\n'
           '.lfw{display:flex;gap:8px;align-items:center;margin-bottom:4px}\n'
           '.lfw input{flex:1 1 auto;min-width:0;margin:0 !important}\n'
           '.lfw .chips{flex:0 0 auto;flex-wrap:nowrap;gap:5px}\n'
           '.lfw .chip{padding:7px 11px}\n'
           '.lfchk{display:flex;gap:8px;align-items:center;font-size:13.5px;margin:6px 0}\n'
           '.lfchk input{width:auto;margin:0}\n'
           '.lfhit{display:block;width:100%;text-align:left;border:1px solid var(--line);\n'
           '  background:var(--card);color:var(--ink);border-radius:10px;padding:8px 10px;\n'
           '  margin:0 0 6px;font:inherit;font-size:13.5px;cursor:pointer}\n'
           '.lfhit small{display:block;color:var(--muted);font-size:12px}\n'
           '.lfbtn{display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap}\n'
           '.lfbtn .backlink{margin:0 0 0 auto}\n'
           '.lf_per,.lf_src{margin:6px 0 0}\n'
           '.mdt{display:flex;flex-wrap:wrap;gap:10px}\n'
           '.mdt>div{flex:1 1 130px;min-width:0}\n')

NF_OLD = ('''      <div id="ml_newfood" style="display:none;margin-top:10px;border-top:1px dashed var(--line);padding-top:10px">
        <p class="q">&#10133; New food</p>
        <div class="row2"><div><p class="lbl">Name</p><input type="text" id="nf_item"></div>
          <div><p class="lbl">Portion</p><input type="text" id="nf_portion" placeholder="1 katori"></div></div>
        <div class="row3" style="margin-top:8px">
          <div><p class="lbl">Protein g</p><input type="number" id="nf_p" step="0.1" inputmode="decimal"></div>
          <div><p class="lbl">kcal</p><input type="number" id="nf_k" inputmode="numeric"></div>
          <div><p class="lbl">Fibre g</p><input type="number" id="nf_f" step="0.1" inputmode="decimal"></div></div>
        <p class="lbl" style="margin-top:8px">FODMAP</p>
        <div class="chips" id="nf_fm"></div>
        <button type="button" class="mini" id="nf_save" style="margin-top:6px">Save food &amp; add to meal</button>
      </div></div>
''')
NF_NEW = ('''      <div id="ml_newfood" style="display:none;margin-top:10px;border-top:1px dashed var(--line);padding-top:10px"></div></div>
''')

# The Meds tab's Date | Time row. Its .row2 grid is 1fr 1fr and grid items
# do not shrink below their content, so the time's two 72px lists pushed the
# page to 316px on the folded screen -- measured on v3.30.0 as well, the same
# fault v3.29.0 found and removed on the Meals tab. The cells now wrap: side
# by side where they fit, the time on its own line where they do not.
MDT_OLD = ('''    <div class="card"><div class="row2">
      <div><p class="lbl">Date</p><input type="date" id="m_day"></div>
      <div><p class="lbl">Time taken</p><input type="time" id="m_time"></div></div></div>
''')
MDT_NEW = ('''    <div class="card"><div class="mdt">
      <div><p class="lbl">Date</p><input type="date" id="m_day"></div>
      <div><p class="lbl">Time taken</p><input type="time" id="m_time"></div></div></div>
''')

HINT_OLD = ('<p class="hint">The full library - 87 foods and growing. Tap a row to edit; '
            'star = favourite in the meal picker.</p>')
HINT_NEW = ('<p class="hint">Your foods, most used first. Each row shows one portion with its '
            'weight and values, the values per 100 g, and where the numbers came from. Tap a '
            'row to edit; star = favourite in the meal picker.</p>')

JS1_OLD = r'''function renderResults(qstr){
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
  $('#ml_newfood').style.display=(q&&!list.length)?'block':'none';
  if(q&&!list.length){$('#nf_item').value=qstr.trim();}
}
function addToBasket(x){
  const ex=basket.find(b=>b.item===x.item);
  if(ex)ex.q++; else basket.push({item:x.item,portion:x.portion,q:1,p:x.protein,k:x.kcal,f:x.fibre,fm:x.fodmap});
  renderBasket();
}
function renderBasket(){
  const box=$('#ml_basket');
  if(!basket.length){box.innerHTML='<p class="hint" style="margin:0">Nothing added yet - search above or tap a favourite.</p>';
    $('#ml_tot').textContent='';$('#ml_meter').style.display='none';$('#ml_fmw').textContent='';return;}
  box.innerHTML='';
  let p=0,k=0,f=0,fs=0;
  basket.forEach((b,i)=>{
    p+=b.q*b.p;k+=b.q*b.k;f+=b.q*b.f;fs+=b.q*FMAP[b.fm];
    const row=document.createElement('div');row.className='bk';
    row.innerHTML=`<span class="fd" style="background:${FMCOL[b.fm]}"></span>
      <span class="nm">${b.item}<small>${b.portion||''}</small></span>
      <button type="button">&minus;</button><span class="qv">${b.q}</span>
      <button type="button">+</button><button type="button" class="rm">&times;</button>`;
    const[minus,,plus,rm]=row.querySelectorAll('button, .qv, button');
    const btns=row.querySelectorAll('button');
    btns[0].onclick=()=>{b.q--;if(b.q<=0)basket.splice(i,1);renderBasket();};
    btns[1].onclick=()=>{b.q++;renderBasket();};
    btns[2].onclick=()=>{basket.splice(i,1);renderBasket();};
    box.appendChild(row);
  });
  const proj=dayProtein+p;
  $('#ml_tot').innerHTML=`This meal: <b>${p.toFixed(1)} g protein</b> &middot; ${Math.round(k)} kcal &middot; ${f.toFixed(1)} g fibre`;
  const avg=basket.length?fs/basket.reduce((a,b)=>a+b.q,0):0;
  $('#ml_meter').style.display='block';
  $('#ml_pin').style.left=(avg/2*100)+'%';
  const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
  $('#ml_fmw').innerHTML=`FODMAP load: <b>${lab}</b> &middot; day protein would reach <b>${proj.toFixed(0)}/${PROT_TGT} g</b>`;
}
$('#ml_search').oninput=e=>renderResults(e.target.value);
/* new food inline */
(function(){const box=$('#nf_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip';
  b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;
  b.onclick=()=>{box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');box.dataset.v=k;};box.appendChild(b);});})();
$('#nf_save').onclick=async()=>{
  const item=$('#nf_item').value.trim();if(!item)return toast('Name the food.');
  const fm=$('#nf_fm').dataset.v;if(!fm)return toast('Pick a FODMAP flag.');
  try{await post('/api/library',{item,portion:$('#nf_portion').value,protein:$('#nf_p').value,
    kcal:$('#nf_k').value,fibre:$('#nf_f').value,fodmap:fm,cat:'F'});
    await loadLib();const x=libItem(item);if(x)addToBasket(x);
    $('#ml_newfood').style.display='none';$('#ml_search').value='';renderResults('');
    ['nf_item','nf_portion','nf_p','nf_k','nf_f'].forEach(id=>$('#'+id).value='');$('#nf_fm').dataset.v='';
    $$('#nf_fm .chip').forEach(c=>c.classList.remove('sel'));toast('Food added');
  }catch(e){toast(e.message);}
};
$('#openFoods').onclick=()=>setSeg('meals','foods');
$('#closeFoods').onclick=()=>setSeg('meals','meal');
'''
JS1_NEW = r'''function renderResults(qstr){
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
  /* GUTLOG_V3310_FOODLIB -- a food that is not in the list is added with its
     weight, in the same editor the food list uses, and lands in the meal. */
  const nf=$('#ml_newfood');
  if(nf.dataset.open)return;
  nf.innerHTML='';
  nf.style.display=(q&&!list.length)?'block':'none';
  if(q&&!list.length){
    const a=el('button','btn ghost','Add “'+qstr.trim()+'” to your foods, with its weight');
    a.type='button';a.id='nfOpen';
    a.onclick=()=>{nf.dataset.open='1';
      lfEditor(nf,{id:0,item:qstr.trim(),fodmap:'M'},
        async name=>{nf.dataset.open='';$('#ml_search').value='';await loadLib();
          const x=libItem(name);if(x)addToBasket(x);toast('Food added');},
        ()=>{nf.dataset.open='';renderResults($('#ml_search').value||'');});};
    nf.appendChild(a);
  }
}
function addToBasket(x){
  const ex=basket.find(b=>b.item===x.item);
  if(ex){if(ex.g>0){ex.g=0;ex.q=1;}else ex.q++;}
  else basket.push({item:x.item,portion:lfPortionText(x),q:1,g:0,p:x.protein||0,k:x.kcal||0,
    f:x.fibre||0,fm:x.fodmap,w:lfCanWeigh(x),u:x.portion_unit||'g',dry:!!x.weighed_dry,
    pq:x.portion_qty||0,bp:x.b_protein||0,bk:x.b_kcal||0,bf:x.b_fibre||0});
  renderBasket();
}
/* GUTLOG_V3310_FOODLIB -- a row is either a count of portions or a weight.
   A weight is worked out from the per-100 basis, here for the preview and
   again on the server for the record. */
function bkVals(b){
  if(b.g>0)return [b.bp*b.g/100,b.bk*b.g/100,b.bf*b.g/100,b.pq>0?b.g/b.pq:1];
  return [b.q*b.p,b.q*b.k,b.q*b.f,b.q];
}
function renderBasket(){
  const box=$('#ml_basket');
  if(!basket.length){box.innerHTML='<p class="hint" style="margin:0">Nothing added yet - search above or tap a favourite.</p>';
    $('#ml_tot').textContent='';$('#ml_meter').style.display='none';$('#ml_fmw').textContent='';return;}
  box.innerHTML='';
  basket.forEach((b,i)=>{
    const row=document.createElement('div');row.className='bk';
    row.innerHTML=`<span class="fd" style="background:${FMCOL[b.fm]}"></span>
      <span class="nm"></span>
      <button type="button">&minus;</button><span class="qv"></span>
      <button type="button">+</button><button type="button" class="rm">&times;</button>`;
    const nm=row.querySelector('.nm');nm.textContent=b.item;nm.appendChild(el('small','',b.portion||''));
    const qv=row.querySelector('.qv');qv.textContent=b.g>0?'–':String(b.q);
    const btns=row.querySelectorAll('button');
    btns[0].onclick=()=>{if(b.g>0){b.g=0;}else{b.q--;if(b.q<=0)basket.splice(i,1);}renderBasket();};
    btns[1].onclick=()=>{if(b.g>0){b.g=0;}else b.q++;renderBasket();};
    btns[2].onclick=()=>{basket.splice(i,1);renderBasket();};
    if(b.w)row.appendChild(wtLine(b,b.u,b.dry,b.item,()=>{qv.textContent=b.g>0?'–':String(b.q);bkTotals();}));
    else row.appendChild(el('div','bkw','No weight set for this food — by portion only.'));
    box.appendChild(row);
  });
  bkTotals();
}
function bkTotals(){
  let p=0,k=0,f=0,fs=0,nq=0;
  basket.forEach(b=>{const v=bkVals(b);p+=v[0];k+=v[1];f+=v[2];fs+=v[3]*FMAP[b.fm];nq+=v[3];});
  const proj=dayProtein+p;
  $('#ml_tot').innerHTML=`This meal: <b>${p.toFixed(1)} g protein</b> &middot; ${Math.round(k)} kcal &middot; ${f.toFixed(1)} g fibre`;
  const avg=nq?fs/nq:0;
  $('#ml_meter').style.display='block';
  $('#ml_pin').style.left=(avg/2*100)+'%';
  const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
  $('#ml_fmw').innerHTML=`FODMAP load: <b>${lab}</b> &middot; day protein would reach <b>${proj.toFixed(0)}/${PROT_TGT} g</b>`;
}
$('#ml_search').oninput=e=>renderResults(e.target.value);
$('#openFoods').onclick=()=>setSeg('meals','foods');
$('#closeFoods').onclick=()=>setSeg('meals','meal');
'''

JS2_OLD = r'''/* library manager */
function renderLibList(){
  const q=($('#lib_search').value||'').trim().toLowerCase();
  const box=$('#lib_list');box.innerHTML='';
  const CATN={A:'Grains & breads',B:'Dals & legumes',C:'Soy & protein',D:'Dairy & fats',E:'Sabzis',F:'Snacks & nuts',G:'Fruit',H:'Drinks & composites'};
  let list=LIB.filter(x=>{
    if(libFilter==='fav'&&!x.fav)return false;
    if(libFilter==='comfort'&&!(x.tags||'').includes('comfort'))return false;
    if(libFilter==='trigger'&&x.status!=='trigger')return false;
    if(q&&!x.item.toLowerCase().includes(q))return false;
    return true;});
  let cur='';
  list.forEach(x=>{
    if(x.cat!==cur){cur=x.cat;const h=document.createElement('p');h.className='cathead';h.textContent=CATN[cur]||cur;box.appendChild(h);}
    const row=document.createElement('div');row.className='librow';
    const badge=x.status==='cleared'?'<span class="badge b-ok">cleared</span>':
      x.status==='trigger'?'<span class="badge b-bad">trigger</span>':
      x.status==='test'?'<span class="badge b-sus">test</span>':
      (x.tags||'').includes('comfort')?'<span class="badge b-cmf">comfort</span>':'';
    row.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span>
      <span class="nm">${x.item} ${badge}<small>${x.portion||''} &middot; ${x.protein}g P &middot; ${x.kcal} kcal &middot; ${x.fodmap}</small></span>
      <button type="button" class="star ${x.fav?'on':''}">&#9733;</button>`;
    row.querySelector('.star').onclick=async(ev)=>{ev.stopPropagation();await post('/api/library/'+x.id,{fav:!x.fav});await loadLib();};
    row.onclick=()=>editFood(x);
    box.appendChild(row);
  });
  if(!list.length)box.innerHTML='<p class="hint" style="margin:0">No foods match.</p>';
}
$('#lib_search').oninput=renderLibList;
(function(){const box=$('#lib_filters');[['all','All'],['fav','★ Favourites'],['comfort','Comfort'],['trigger','Triggers']].forEach(([k,l])=>{
  const b=document.createElement('button');b.type='button';b.className='chip'+(k==='all'?' sel':'');b.textContent=l;
  b.onclick=()=>{libFilter=k;box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');renderLibList();};box.appendChild(b);});})();
function editFood(x){
  const e=$('#lib_edit');e.style.display='block';$('#lib_list').style.display='none';$('#lib_addnew').style.display='none';
  e.innerHTML=`<p class="q">Edit &middot; ${x.item}</p>
   <div class="row2"><div><p class="lbl">Name</p><input type="text" id="ed_item"></div>
     <div><p class="lbl">Portion</p><input type="text" id="ed_portion"></div></div>
   <div class="row3" style="margin-top:8px"><div><p class="lbl">Protein</p><input type="number" id="ed_p" step="0.1"></div>
     <div><p class="lbl">kcal</p><input type="number" id="ed_k"></div>
     <div><p class="lbl">Fibre</p><input type="number" id="ed_f" step="0.1"></div></div>
   <p class="lbl" style="margin-top:8px">FODMAP</p><div class="chips" id="ed_fm"></div>
   <p class="lbl" style="margin-top:8px">Status</p><div class="chips" id="ed_st"></div>
   <div style="display:flex;gap:8px;margin-top:12px">
     <button type="button" class="mini" id="ed_save">Save changes</button>
     <button type="button" class="del" id="ed_del">Delete food</button>
     <button type="button" class="backlink" id="ed_cancel" style="margin:0 0 0 auto">Cancel</button></div>`;
  $('#ed_item').value=x.item;$('#ed_portion').value=x.portion||'';$('#ed_p').value=x.protein;$('#ed_k').value=x.kcal;$('#ed_f').value=x.fibre;
  let fm=x.fodmap,st=x.status||'';
  const fb=$('#ed_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===fm?' sel':'');
    b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;b.onclick=()=>{fm=k;fb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fb.appendChild(b);});
  const sb=$('#ed_st');[['','library'],['cleared','cleared'],['trigger','trigger'],['test','test']].forEach(([k,l])=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===st?' sel':'');
    b.textContent=l;b.onclick=()=>{st=k;sb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};sb.appendChild(b);});
  $('#ed_save').onclick=async()=>{try{await post('/api/library/'+x.id,{item:$('#ed_item').value,portion:$('#ed_portion').value,
    protein:$('#ed_p').value,kcal:$('#ed_k').value,fibre:$('#ed_f').value,fodmap:fm,status:st});closeEdit();await loadLib();toast('Saved');}catch(er){toast(er.message);}};
  $('#ed_del').onclick=async()=>{if(!confirm('Delete '+x.item+' from the library?'))return;await post('/api/delete/library/'+x.id,{});closeEdit();await loadLib();toast('Deleted');};
  $('#ed_cancel').onclick=closeEdit;
}
function closeEdit(){$('#lib_edit').style.display='none';$('#lib_list').style.display='block';$('#lib_addnew').style.display='inline-block';}
$('#lib_addnew').onclick=()=>editFood({id:0,item:'',portion:'',protein:'',kcal:'',fibre:'',fodmap:'M',status:''});

/* fix add-new-food save (id 0 -> create) */
function editFood(x){
  const isNew=!x.id;
  const e=$('#lib_edit');e.style.display='block';$('#lib_list').style.display='none';$('#lib_addnew').style.display='none';$('#lib_filters').style.display='none';$('#lib_search').style.display='none';
  const CATS=[['A','Grains'],['B','Dals'],['C','Soy/protein'],['D','Dairy'],['E','Sabzi'],['F','Snacks'],['G','Fruit'],['H','Drinks']];
  e.innerHTML=`<p class="q">${isNew?'Add a food':'Edit &middot; '+x.item}</p>
   <div class="row2"><div><p class="lbl">Name</p><input type="text" id="ed_item"></div>
     <div><p class="lbl">Portion</p><input type="text" id="ed_portion"></div></div>
   ${isNew?'<p class="lbl" style="margin-top:8px">Group</p><div class="chips" id="ed_cat"></div>':''}
   <div class="row3" style="margin-top:8px"><div><p class="lbl">Protein</p><input type="number" id="ed_p" step="0.1"></div>
     <div><p class="lbl">kcal</p><input type="number" id="ed_k"></div>
     <div><p class="lbl">Fibre</p><input type="number" id="ed_f" step="0.1"></div></div>
   <p class="lbl" style="margin-top:8px">FODMAP</p><div class="chips" id="ed_fm"></div>
   <p class="lbl" style="margin-top:8px">Status</p><div class="chips" id="ed_st"></div>
   <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
     <button type="button" class="mini" id="ed_save">${isNew?'Add food':'Save changes'}</button>
     ${isNew?'':'<button type="button" class="del" id="ed_del">Delete</button>'}
     <button type="button" class="backlink" id="ed_cancel" style="margin:0 0 0 auto">Cancel</button></div>`;
  $('#ed_item').value=x.item||'';$('#ed_portion').value=x.portion||'';$('#ed_p').value=x.protein;$('#ed_k').value=x.kcal;$('#ed_f').value=x.fibre;
  let fm=x.fodmap||'M',st=x.status||'',cat='F';
  if(isNew){const cb=$('#ed_cat');CATS.forEach(([k,l])=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===cat?' sel':'');
    b.textContent=l;b.onclick=()=>{cat=k;cb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};cb.appendChild(b);});}
  const fb=$('#ed_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===fm?' sel':'');
    b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;b.onclick=()=>{fm=k;fb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fb.appendChild(b);});
  const sb=$('#ed_st');[['','library'],['cleared','cleared'],['trigger','trigger'],['test','test']].forEach(([k,l])=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===st?' sel':'');
    b.textContent=l;b.onclick=()=>{st=k;sb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};sb.appendChild(b);});
  $('#ed_save').onclick=async()=>{
    const item=$('#ed_item').value.trim();if(!item)return toast('Name the food.');
    const body={item,portion:$('#ed_portion').value,protein:$('#ed_p').value,kcal:$('#ed_k').value,fibre:$('#ed_f').value,fodmap:fm,status:st};
    try{if(isNew){body.cat=cat;await post('/api/library',body);}else{await post('/api/library/'+x.id,body);}
      closeEdit();await loadLib();toast('Saved');}catch(er){toast(er.message);}};
  if(!isNew)$('#ed_del').onclick=async()=>{if(!confirm('Delete '+x.item+'?'))return;await post('/api/delete/library/'+x.id,{});closeEdit();await loadLib();toast('Deleted');};
  $('#ed_cancel').onclick=closeEdit;
}
function closeEdit(){$('#lib_edit').style.display='none';$('#lib_list').style.display='block';$('#lib_addnew').style.display='inline-block';$('#lib_filters').style.display='flex';$('#lib_search').style.display='block';}
'''
JS2_NEW = r'''/* library manager -- GUTLOG_V3310_FOODLIB. Most used first; each row shows
   one portion with its weight and the values for it, the per-100 basis,
   and where the numbers came from. The editor is lfEditor(), shared with
   the Meals tab's "add a food". */
function lfFmt(v){if(v===null||v===undefined||v==='')return '–';const n=Number(v);
  if(!isFinite(n))return '–';return String(Math.round(n*10)/10);}
function lfIn(v){if(v===null||v===undefined||v==='')return '';const n=Number(v);
  return isFinite(n)?String(Math.round(n*100)/100):'';}
function lfCanWeigh(x){return !!(x&&x.portion_qty>0&&(x.b_protein!=null||x.b_kcal!=null||x.b_fibre!=null));}
function lfPortionText(x){
  const lab=(x.portion||'').trim();
  if(!(x.portion_qty>0))return (lab?lab+' · ':'')+'no weight set';
  const w=lfFmt(x.portion_qty)+' '+(x.portion_unit||'g')+(x.weighed_dry?' dry':'')+(x.portion_est?' (est.)':'');
  return (lab&&!/^[\d.]+\s*(g|ml)$/i.test(lab))?(lab+' · '+w):w;
}
const LF_SRC={USDA:'USDA table',own:'your values',estimated:'estimated'};
function lfSourceText(x){return LF_SRC[x.source]||'not set';}
function renderLibList(){
  const q=($('#lib_search').value||'').trim().toLowerCase();
  const box=$('#lib_list');box.innerHTML='';
  const CATN={A:'Grains & breads',B:'Dals & legumes',C:'Soy & protein',D:'Dairy & fats',E:'Sabzis',F:'Snacks & nuts',G:'Fruit',H:'Drinks & composites'};
  const list=LIB.filter(x=>{
    if(libFilter==='fav'&&!x.fav)return false;
    if(libFilter==='comfort'&&!(x.tags||'').includes('comfort'))return false;
    if(libFilter==='trigger'&&x.status!=='trigger')return false;
    if(q&&!x.item.toLowerCase().includes(q))return false;
    return true;});
  const head=t=>box.appendChild(el('p','cathead',t));
  const used=list.filter(x=>x.uses>0).sort((a,b)=>b.uses-a.uses||a.item.localeCompare(b.item));
  if(used.length){head('Most used');used.forEach(x=>box.appendChild(lfRow(x)));}
  let cur='';
  list.filter(x=>!(x.uses>0)).forEach(x=>{
    if(x.cat!==cur){cur=x.cat;head(CATN[cur]||cur);}
    box.appendChild(lfRow(x));});
  if(!list.length)box.innerHTML='<p class="hint" style="margin:0">No foods match.</p>';
}
function lfRow(x){
  const row=document.createElement('div');row.className='librow';row.dataset.item=x.item;
  const badge=x.status==='cleared'?'<span class="badge b-ok">cleared</span>':
    x.status==='trigger'?'<span class="badge b-bad">trigger</span>':
    x.status==='test'?'<span class="badge b-sus">test</span>':
    (x.tags||'').includes('comfort')?'<span class="badge b-cmf">comfort</span>':'';
  row.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span><span class="nm"></span>
    <button type="button" class="star ${x.fav?'on':''}">&#9733;</button>`;
  const nm=row.querySelector('.nm');nm.appendChild(document.createTextNode(x.item+' '));
  if(badge)nm.insertAdjacentHTML('beforeend',badge);
  nm.appendChild(el('small','lfport',lfPortionText(x)+' · '+lfFmt(x.protein)+' g protein · '+
    lfFmt(x.kcal)+' kcal · '+lfFmt(x.fibre)+' g fibre'));
  const u=x.portion_unit||'g';
  nm.appendChild(el('small','lfbasis',(lfCanWeigh(x)?('per 100 '+u+(x.weighed_dry?' dry':'')+': '+
    lfFmt(x.b_protein)+' g protein · '+lfFmt(x.b_kcal)+' kcal · '+lfFmt(x.b_fibre)+' g fibre'):
    'no per-100 values yet')+' · '+lfSourceText(x)));
  row.querySelector('.star').onclick=async(ev)=>{ev.stopPropagation();await post('/api/library/'+x.id,{fav:!x.fav});await loadLib();};
  row.onclick=()=>editFood(x);
  return row;
}
$('#lib_search').oninput=renderLibList;
(function(){const box=$('#lib_filters');[['all','All'],['fav','★ Favourites'],['comfort','Comfort'],['trigger','Triggers']].forEach(([k,l])=>{
  const b=document.createElement('button');b.type='button';b.className='chip'+(k==='all'?' sel':'');b.textContent=l;
  b.onclick=()=>{libFilter=k;box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');renderLibList();};box.appendChild(b);});})();
function editFood(x){
  const e=$('#lib_edit');e.style.display='block';$('#lib_list').style.display='none';$('#lib_addnew').style.display='none';
  $('#lib_filters').style.display='none';$('#lib_search').style.display='none';
  lfEditor(e,x,async()=>{closeEdit();await loadLib();toast('Saved');},closeEdit);
}
function closeEdit(){$('#lib_edit').style.display='none';$('#lib_edit').innerHTML='';$('#lib_list').style.display='block';$('#lib_addnew').style.display='inline-block';$('#lib_filters').style.display='flex';$('#lib_search').style.display='block';}
$('#lib_addnew').onclick=()=>editFood({id:0,item:'',portion:'',fodmap:'M',status:''});
/* One editor for a food, new or old. Weight first, then the values PER 100
   (per 100 g DRY when weighed dry), so a weighed meal scales exactly. A
   food saved before weights existed opens per portion until a weight is set.
   The table fills values only on a tap -- or, for a new food whose name it
   knows and whose values are still blank, once, with the match shown -- and
   any number he changes makes the values his. */
function lfEditor(box,x,done,cancel){
  const isNew=!x.id;
  box.innerHTML='';
  box.appendChild(el('p','q',isNew?'Add a food':'Edit · '+x.item));
  const f=el('div','lfed');box.appendChild(f);
  f.innerHTML='<p class="lbl">Name</p><input type="text" class="lf_item" maxlength="80">'+
    '<p class="lbl">Portion name (optional)</p><input type="text" class="lf_label" maxlength="60" placeholder="1 katori">'+
    '<p class="lbl">Weight of one portion</p><div class="lfw"><input type="number" class="lf_pq" min="0" step="1" inputmode="decimal" aria-label="Weight of one portion"><div class="chips lf_unit"></div></div>'+
    '<label class="lfchk"><input type="checkbox" class="lf_dry"> Weighed dry (dal, rice, poha, oats)</label>'+
    '<label class="lfchk lf_okw" style="display:none"><input type="checkbox" class="lf_ok"> This weight is right (it was estimated)</label>'+
    '<p class="lbl lf_bh"></p>'+
    '<div class="row3"><div><p class="lbl">Protein g</p><input type="number" class="lf_p" min="0" step="0.1" inputmode="decimal"></div>'+
    '<div><p class="lbl">kcal</p><input type="number" class="lf_k" min="0" step="1" inputmode="decimal"></div>'+
    '<div><p class="lbl">Fibre g</p><input type="number" class="lf_f" min="0" step="0.1" inputmode="decimal"></div></div>'+
    '<p class="hint lf_per"></p><p class="hint lf_src"></p>'+
    '<p class="lbl" style="margin-top:10px">Look it up in the food table</p>'+
    '<input type="text" class="lf_q" placeholder="e.g. masoor dal, oats, guava"><div class="lf_hits"></div>'+
    (isNew?'<p class="lbl">Group</p><div class="chips lf_cat"></div>':'')+
    '<p class="lbl" style="margin-top:8px">FODMAP</p><div class="chips lf_fm"></div>'+
    '<p class="lbl" style="margin-top:8px">Status</p><div class="chips lf_st"></div>'+
    '<div class="lfbtn"><button type="button" class="mini lf_save"></button>'+
    (isNew?'':'<button type="button" class="del lf_del">Delete</button>')+
    '<button type="button" class="backlink lf_cx">Cancel</button></div>';
  const q1=s=>f.querySelector(s);
  const I={item:q1('.lf_item'),label:q1('.lf_label'),pq:q1('.lf_pq'),dry:q1('.lf_dry'),ok:q1('.lf_ok'),
    p:q1('.lf_p'),k:q1('.lf_k'),fb:q1('.lf_f'),q:q1('.lf_q')};
  I.item.value=x.item||'';I.label.value=x.portion||'';
  let unit=x.portion_unit||'g',fm=x.fodmap||'M',st=x.status||'',cat='F';
  let fdc=null,fdcDesc='',auto=false,touched=false;
  let mode=(isNew||x.portion_qty>0)?'b':'p';
  I.pq.value=x.portion_qty>0?lfIn(x.portion_qty):'';
  I.dry.checked=!!x.weighed_dry;
  if(x.portion_est&&x.portion_qty>0)q1('.lf_okw').style.display='flex';
  const setVals=v=>{I.p.value=lfIn(v[0]);I.k.value=lfIn(v[1]);I.fb.value=lfIn(v[2]);};
  const rd=()=>[I.p.value,I.k.value,I.fb.value].map(v=>v===''?null:Number(v));
  if(mode==='b'){
    let b=[x.b_protein,x.b_kcal,x.b_fibre];
    if(b.every(v=>v==null)&&x.portion_qty>0)b=[x.protein,x.kcal,x.fibre].map(v=>v==null?null:v*100/x.portion_qty);
    setVals(b);
  }else setVals([x.protein,x.kcal,x.fibre]);
  const draw=()=>{
    const pq=Number(I.pq.value)||0,dry=I.dry.checked?' dry':'';
    q1('.lf_bh').textContent=mode==='b'?('Nutrition per 100 '+unit+dry):
      ('Nutrition per portion'+(pq>0?' — saved per 100 '+unit+' using the weight':''));
    const v=rd();
    q1('.lf_per').textContent=(mode==='b'&&pq>0&&v.some(a=>a!=null))?('One portion ('+lfFmt(pq)+' '+unit+dry+'): '+
      lfFmt(v[0]==null?null:v[0]*pq/100)+' g protein · '+lfFmt(v[1]==null?null:v[1]*pq/100)+' kcal · '+
      lfFmt(v[2]==null?null:v[2]*pq/100)+' g fibre'):'';
    q1('.lf_src').textContent=fdc?('From the USDA table: '+fdcDesc+'. Change any number to make it yours.'):
      (touched?'Your values.':('Source: '+lfSourceText(x)+(x.source_ref?(' — '+x.source_ref):'')));
  };
  const drawUnit=()=>{const u=q1('.lf_unit');u.innerHTML='';['g','ml'].forEach(k=>{
    const c=el('button','chip'+(k===unit?' sel':''),k);c.type='button';c.onclick=()=>{unit=k;drawUnit();draw();};u.appendChild(c);});};
  const hits=q1('.lf_hits');
  const useHit=m=>{mode='b';setVals([m.protein,m.kcal,m.fibre]);fdc=m.fdc;fdcDesc=m.desc;touched=false;auto=true;draw();};
  let seq=0;
  const lookup=async(q,autofill)=>{
    const my=++seq;
    if(!q.trim()){hits.innerHTML='';return;}
    let j;try{j=await jget('/api/foodtable?q='+encodeURIComponent(q)+'&dry='+(I.dry.checked?1:0));}catch(e){return;}
    if(my!==seq)return;
    hits.innerHTML='';
    if(!j.available){hits.appendChild(el('p','hint',j.text||'No food table on this server.'));return;}
    if(!j.matches.length){hits.appendChild(el('p','hint','Nothing in the table by that name — type the values.'));return;}
    j.matches.slice(0,6).forEach(m=>{const b=el('button','lfhit');b.type='button';b.appendChild(el('span','',m.desc));
      b.appendChild(el('small','',lfFmt(m.protein)+' g protein · '+lfFmt(m.kcal)+' kcal · '+
        (m.fibre==null?'fibre not listed':lfFmt(m.fibre)+' g fibre')+' per 100 g'));
      b.onclick=()=>useHit(m);hits.appendChild(b);});
    hits.appendChild(el('p','hint',j.name+' · '+j.licence));
    const top=j.matches[0];
    if(autofill&&top&&top.full&&!touched&&(auto||rd().every(v=>v==null)))useHit(top);
  };
  let tmr=null;
  const later=(fn)=>{clearTimeout(tmr);tmr=setTimeout(fn,250);};
  I.item.oninput=()=>{if(isNew)later(()=>lookup(I.item.value,true));};
  I.q.oninput=()=>later(()=>lookup(I.q.value,false));
  I.pq.oninput=draw;
  I.dry.onchange=()=>{draw();const q=I.q.value||I.item.value;if(q)lookup(q,isNew);};
  [I.p,I.k,I.fb].forEach(i=>i.oninput=()=>{fdc=null;fdcDesc='';touched=true;auto=false;draw();});
  if(isNew){const cb=q1('.lf_cat');[['A','Grains'],['B','Dals'],['C','Soy/protein'],['D','Dairy'],['E','Sabzi'],['F','Snacks'],['G','Fruit'],['H','Drinks']].forEach(([k,l])=>{
    const b=el('button','chip'+(k===cat?' sel':''),l);b.type='button';
    b.onclick=()=>{cat=k;cb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};cb.appendChild(b);});}
  const fb=q1('.lf_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===fm?' sel':'');
    b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;b.onclick=()=>{fm=k;fb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fb.appendChild(b);});
  const sb=q1('.lf_st');[['','library'],['cleared','cleared'],['trigger','trigger'],['test','test']].forEach(([k,l])=>{
    const b=el('button','chip'+(k===st?' sel':''),l);b.type='button';
    b.onclick=()=>{st=k;sb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};sb.appendChild(b);});
  const sv=q1('.lf_save');sv.textContent=isNew?'Add food':'Save changes';
  sv.onclick=async()=>{
    const item=I.item.value.trim();if(!item)return toast('Name the food.');
    const pq=Number(I.pq.value)||0;
    if(isNew&&!(pq>0))return toast('Set its weight — grams or ml for one portion.');
    const v=rd();
    if(v.some(a=>a!=null&&(!isFinite(a)||a<0)))return toast('Numbers only, and not negative.');
    const body={item,portion:I.label.value.trim(),fodmap:fm,status:st,portion_qty:pq>0?pq:'',
      portion_unit:unit,weighed_dry:I.dry.checked,weight_ok:I.ok.checked};
    if(pq>0){const b=mode==='b'?v:v.map(a=>a==null?null:Math.round(a*100/pq*100)/100);
      body.b_protein=b[0];body.b_kcal=b[1];body.b_fibre=b[2];}
    else{body.protein=v[0];body.kcal=v[1];body.fibre=v[2];}
    if(fdc)body.fdc=fdc;
    try{
      if(isNew){body.cat=cat;await post('/api/library',body);}else await post('/api/library/'+x.id,body);
      await done(item);
    }catch(er){toast(er.message);}
  };
  if(!isNew)q1('.lf_del').onclick=async()=>{if(!confirm('Delete '+x.item+'?'))return;
    await post('/api/delete/library/'+x.id,{});cancel();await loadLib();toast('Deleted');};
  q1('.lf_cx').onclick=()=>cancel();
  drawUnit();draw();
  if(x.item)lookup(x.item,isNew);
}
'''

# ------------------------------------------------ meal rows: time buttons
MLROW_OLD = "    line.appendChild(el('b','',m.mtime+' · '+m.slot));\n"
MLROW_NEW = ("    /* GUTLOG_V3310_FOODLIB -- the time is a button: tap it to change it. */\n"
             "    line.appendChild(teBtn({table:'meals',id:m.id,day:m.day,time:m.mtime,title:m.slot,row:line,\n"
             "      after:async()=>{await loadMealTotals();loadMeals();}}));\n"
             "    line.appendChild(el('b','',m.slot));\n")

MLITEMS_OLD = "    line.appendChild(el('small','',(m.items||[]).map(i=>i.n+(i.q!==1?' ×'+i.q:'')).join(', ')+\n"
MLITEMS_NEW = "    line.appendChild(el('small','',mealItemsText(m.items)+\n"

MLEDIT_OLD = ('''  mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;
  const ci=(MC.cards||[]).findIndex(c=>c.name===m.card);
  if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
  else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,
        extra:(m.items||[]).map(i=>({n:i.n,q:i.q}))};
''')
MLEDIT_NEW = ('''  mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;mcTimeV=null;
  const ci=(MC.cards||[]).findIndex(c=>c.name===m.card);
  if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
  else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,
        extra:(m.items||[]).map(i=>({n:i.n,q:i.q,g:i.g||0}))};
''')

FRESH_OLD = "extra:(from&&from.extra?from.extra:[]).map(x=>({n:x.n,q:x.q}))};"
FRESH_NEW = "extra:(from&&from.extra?from.extra:[]).map(x=>({n:x.n,q:x.q,g:x.g||0}))};"

EDHINT_OLD = "if(mcEdit){const e=el('p','hint','Editing the '+mcEdit.slot+' logged at '+mcEdit.mtime+'. Save keeps that time.');"
EDHINT_NEW = ("if(mcEdit){const e=el('p','hint','Editing the '+mcEdit.slot+' logged at '+mcEdit.mtime+"
              "'. Its time is below — change it if it was different.');")

MCBODY_OLD = r'''  if(mcSel.extra.length){const row=el('div','mrow');row.appendChild(el('p','lbl',mcCur>=0?'Also had':'Had'));
    mcSel.extra.forEach((x,xi)=>{const line=el('div','mx');const nm=el('span','',x.n+(MC.lib[x.n]&&MC.lib[x.n].est?' (estimated)':''));
      const st=el('div','mstep');const mi=el('button','chip num','−');mi.type='button';const pl=el('button','chip num','+');pl.type='button';
      const v=el('span','mq','×'+x.q);
      mi.onclick=()=>{if(x.q<=0.5){mcSel.extra.splice(xi,1);}else{x.q=x.q<=1?0.5:x.q-1;}mcRender();};
      pl.onclick=()=>{x.q=x.q<1?1:x.q+1;mcRender();};
      st.appendChild(mi);st.appendChild(v);st.appendChild(pl);line.appendChild(nm);line.appendChild(st);row.appendChild(line);});
    b.appendChild(row);}
  if(mcPick)b.appendChild(mcPicker());
  else{const a=el('button','btn ghost','+ Something else');a.type='button';a.style.marginTop='10px';a.onclick=()=>{mcPick=true;mcRender();};b.appendChild(a);}
  const pairs=mcPairs(mcCur,mcSel).concat(mcSel.extra.map(x=>[x.n,x.q]));
  const e=mcEst(pairs);
  b.appendChild(el('p','hint',pairs.length?('About '+e[0].toFixed(0)+' g protein · '+Math.round(e[1])+' kcal (estimated) · time taken when you press log'):'Add what you had.'));
  if(MC.missing&&MC.missing.length&&mcCur>=0){const w=el('p','hint','Not in the food list yet, left out: '+MC.missing.join(', '));b.appendChild(w);}
  const name=mcCur>=0?MC.cards[mcCur].name:(mcSel.slot||'Meal');
  const go=el('button','btn primary',mcEdit?'Save changes':(mcSame()?('Log '+name.toLowerCase()+' — same as last time'):('Log '+name.toLowerCase())));
  go.type='button';go.disabled=!pairs.length;
  go.onclick=async()=>{if(go.dataset.busy)return;go.dataset.busy=1;
    const body={card:mcCur>=0?MC.cards[mcCur].name:'',choices:mcSel.choices,onion:!!mcSel.onion,extra:mcSel.extra,slot:name};
    try{
      if(mcEdit){await post('/api/meals/'+mcEdit.id+'/replace',body);toast(name+' updated');}
      else{body.day=todayISO;body.mtime=mcNowHM();const r=await post('/api/mealcards/log',body);
        toast(name+' logged'+(r.missing&&r.missing.length?' · left out: '+r.missing.join(', '):''));}
      mcEdit=null;mcPick=false;await loadMeals();
    }catch(err){go.dataset.busy='';toast(err.message);}};
  b.appendChild(go);
'''
MCBODY_NEW = r'''  if(mcSel.extra.length){const row=el('div','mrow');row.appendChild(el('p','lbl',mcCur>=0?'Also had':'Had'));
    mcSel.extra.forEach((x,xi)=>{const L=MC.lib[x.n]||{};const line=el('div','mx');const nm=el('span','',x.n+(L.est?' (estimated)':''));
      const st=el('div','mstep');const mi=el('button','chip num','−');mi.type='button';const pl=el('button','chip num','+');pl.type='button';
      const v=el('span','mq',x.g>0?'by weight':'×'+x.q);
      mi.onclick=()=>{if(x.g>0){x.g=0;}else if(x.q<=0.5){mcSel.extra.splice(xi,1);}else{x.q=x.q<=1?0.5:x.q-1;}mcRender();};
      pl.onclick=()=>{if(x.g>0){x.g=0;}else{x.q=x.q<1?1:x.q+1;}mcRender();};
      st.appendChild(mi);st.appendChild(v);st.appendChild(pl);line.appendChild(nm);line.appendChild(st);row.appendChild(line);
      /* GUTLOG_V3310_FOODLIB -- the same weight field as the Meals tab. */
      if(L.w)row.appendChild(wtLine(x,L.u||'g',L.dry,x.n,()=>{v.textContent=x.g>0?'by weight':'×'+x.q;mcEstDraw();}));});
    b.appendChild(row);}
  if(mcPick)b.appendChild(mcPicker());
  else{const a=el('button','btn ghost','+ Something else');a.type='button';a.style.marginTop='10px';a.onclick=()=>{mcPick=true;mcRender();};b.appendChild(a);}
  const eh=el('p','hint');eh.id='mcEst';b.appendChild(eh);
  if(MC.missing&&MC.missing.length&&mcCur>=0){const w=el('p','hint','Not in the food list yet, left out: '+MC.missing.join(', '));b.appendChild(w);}
  /* GUTLOG_V3310_FOODLIB -- when this meal was eaten. Default now; an edited
     meal opens at its own time. The box is shown as the two lists every
     time box on this page uses. */
  const tr=el('div','vtm mctime');tr.appendChild(el('span','lb','Time eaten'));
  const ti=document.createElement('input');ti.type='time';ti.id='mcTime';
  ti.value=mcTimeV||(mcEdit?mcEdit.mtime:mcNowHM());
  ti.addEventListener('change',()=>{mcTimeV=ti.value;});
  tr.appendChild(ti);b.appendChild(tr);
  const name=mcCur>=0?MC.cards[mcCur].name:(mcSel.slot||'Meal');
  const go=el('button','btn primary',mcEdit?'Save changes':(mcSame()?('Log '+name.toLowerCase()+' — same as last time'):('Log '+name.toLowerCase())));
  go.type='button';go.id='mcGo';
  go.onclick=async()=>{if(go.dataset.busy)return;go.dataset.busy=1;
    const mt=($('#mcTime')&&$('#mcTime').value)||mcNowHM();
    const body={card:mcCur>=0?MC.cards[mcCur].name:'',choices:mcSel.choices,onion:!!mcSel.onion,extra:mcSel.extra,slot:name,mtime:mt};
    try{
      if(mcEdit){await post('/api/meals/'+mcEdit.id+'/replace',body);toast(name+' updated');}
      else{body.day=todayISO;const r=await post('/api/mealcards/log',body);
        toast(name+' logged at '+mt+(r.missing&&r.missing.length?' · left out: '+r.missing.join(', '):''));}
      mcEdit=null;mcPick=false;mcTimeV=null;await loadMeals();
    }catch(err){go.dataset.busy='';toast(err.message);}};
  b.appendChild(go);mcEstDraw();
'''

PICK_OLD = "t.onclick=()=>{MC.lib[f.n]={p:f.p||0,k:f.k||0,est:f.est};mcSel.extra.push({n:f.n,q:1});"
PICK_NEW = ("t.onclick=()=>{MC.lib[f.n]={p:f.p||0,k:f.k||0,est:f.est,w:f.w,pq:f.pq,u:f.u,dry:f.dry,"
            "bp:f.bp,bk:f.bk,bf:f.bf};mcSel.extra.push({n:f.n,q:1,g:0});")

TODAY_OLD = ('''    const txt=el('div','');txt.appendChild(el('b','',m.mtime+' · '+m.slot));
    txt.appendChild(el('small','',m.items.map(i=>i.n+(i.q!==1?' ×'+i.q:'')).join(', ')+' · '+Math.round(m.protein||0)+' g protein'));
''')
TODAY_NEW = ('''    /* GUTLOG_V3310_FOODLIB -- the time is a button: tap it to change it. */
    const txt=el('div','');const hd=el('div','mth');
    hd.appendChild(teBtn({table:'meals',id:m.id,day:MC.day||todayISO,time:m.mtime,title:m.slot,row:line,after:loadMeals}));
    hd.appendChild(el('b','',m.slot));txt.appendChild(hd);
    txt.appendChild(el('small','',mealItemsText(m.items)+' · '+Math.round(m.protein||0)+' g protein'));
''')

TODAYED_OLD = ('''    ed.onclick=()=>{mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;
      const ci=MC.cards.findIndex(c=>c.name===m.card);
      if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
      else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,extra:(m.card?[]:m.items.map(i=>({n:i.n,q:i.q}))).concat(m.card?[]:[])};
''')
TODAYED_NEW = ('''    ed.onclick=()=>{mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;mcTimeV=null;
      const ci=MC.cards.findIndex(c=>c.name===m.card);
      if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
      else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,extra:(m.card?[]:m.items.map(i=>({n:i.n,q:i.q,g:i.g||0}))).concat(m.card?[]:[])};
''')

PRN_OLD = r'''  box.innerHTML='';rows.forEach(r=>{const d=document.createElement('div');d.className='todayrow';
    d.innerHTML=`<b>${r.medicine}</b> &times;${r.times.length}<span class="tms">${r.times.join(', ')}</span>
      <button type="button" class="p1">+1 dose</button>`;
'''
PRN_NEW = r'''  box.innerHTML='';rows.forEach(r=>{const d=document.createElement('div');d.className='todayrow';
    d.innerHTML=`<b></b> &times;${r.times.length}<span class="tms"></span>
      <button type="button" class="p1">+1 dose</button>`;
    d.querySelector('b').textContent=r.medicine;
    /* GUTLOG_V3310_FOODLIB -- each time is a button: tap it to change it. */
    const tms=d.querySelector('.tms');
    r.times.forEach((t,i)=>tms.appendChild(teBtn({table:'doses',id:(r.ids||[])[i],day:r.day||todayISO,
      time:t,title:r.medicine,row:d,after:loadPRNToday})));
'''

SAVEMEAL_OLD = ('''    items:basket.map(b=>({n:b.item,q:b.q,p:b.p,k:b.k,f:b.f,fm:b.fm}))});
  basket=[];renderBasket();$('#ml_notes').value='';await loadRings();await loadMealTotals();
''')
SAVEMEAL_NEW = ('''    items:basket.map(b=>b.g>0?{n:b.item,g:b.g}:{n:b.item,q:b.q,p:b.p,k:b.k,f:b.f,fm:b.fm})});
  basket=[];renderBasket();$('#ml_notes').value='';tmReset('ml_time');await loadRings();await loadMealTotals();
''')

SAVEDOSE_OLD = "  S.prn={};renderPRNGrid();resetChips('prn');$('#m_notes').value='';await loadPRNToday();await loadRings();\n"
SAVEDOSE_NEW = ("  S.prn={};renderPRNGrid();resetChips('prn');$('#m_notes').value='';tmReset('m_time');"
                "await loadPRNToday();await loadRings();\n")

RV_OLD = r'''    rv.doses.slice(0,40).forEach(d=>{t+=`<tr><td>${d.day}</td><td>${d.dtime||''}</td><td>${d.medicine}</td><td>${d.effect||''}</td>
      <td><button class="del" data-t="doses" data-i="${d.id}">&times;</button></td></tr>`;});
    dt.innerHTML=t+'</table>';}else dt.innerHTML='<p class="hint" style="margin:0">No doses in range.</p>';
'''
RV_NEW = r'''    rv.doses.slice(0,40).forEach(d=>{t+=`<tr><td>${d.day}</td><td><button type="button" class="chip tbtn rvt" data-i="${d.id}" data-day="${d.day}" data-tm="${d.dtime||''}">${d.dtime||'--:--'}</button></td><td>${d.medicine}</td><td>${d.effect||''}</td>
      <td><button class="del" data-t="doses" data-i="${d.id}">&times;</button></td></tr>`;});
    dt.innerHTML=t+'</table>';
    /* GUTLOG_V3310_FOODLIB -- a dose's time is changed where it is shown. */
    dt.querySelectorAll('.rvt').forEach(b=>b.onclick=()=>teOpen(dt,{table:'doses',id:+b.dataset.i,
      day:b.dataset.day,time:b.dataset.tm,title:b.closest('tr').children[2].textContent,after:loadReview}));
  }else dt.innerHTML='<p class="hint" style="margin:0">No doses in range.</p>';
'''

TAB1_OLD = "  if(t==='meals'){mlRenderStep();loadMealTotals();loadRegistry();renderTestFoods();}\n"
TAB1_NEW = "  if(t==='meals'){tmNow('ml_time');mlRenderStep();loadMealTotals();loadRegistry();renderTestFoods();}\n"
TAB2_OLD = "  if(t==='meds'){loadPRNToday();loadPatch();loadCourses();}\n"
TAB2_NEW = "  if(t==='meds'){tmNow('m_time');loadPRNToday();loadPatch();loadCourses();}\n"

JS3 = r'''/* GUTLOG_V3310_FOODLIB -- times. One way to change the time of anything
   logged: the same two lists every time box on this page uses (v3.27.0),
   never the phone's own dialog, saving through /api/retime so every change
   is recorded in `edits` like the Day by day card's. */
let mcTimeV=null;
function teOpen(rowEl,o){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick tedit';
  box.innerHTML='<p class="vt"></p><div class="vtm"><span class="lb">Time</span><input type="time" class="tt"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button><button type="button" class="go">Save time</button></div>';
  box.querySelector('.vt').textContent=(o.title||'Entry')+' · '+(o.day===todayISO?'today':mlDmyText(o.day));
  const tt=box.querySelector('.tt');tt.value=o.time||'';
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!tt.value){toast('Pick a time');return;}
    try{const r=await post('/api/retime',{table:o.table,id:o.id,day:o.day,time:tt.value});
      toast(r.unchanged?'No change':('Time set to '+tt.value));box.remove();if(o.after)await o.after();}
    catch(err){toast(err.message);}};
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
  return box;
}
function teBtn(o){
  const b=el('button','chip tbtn',o.time||'--:--');b.type='button';
  b.setAttribute('aria-label','Time '+(o.time||'not set')+', tap to change');
  b.onclick=ev=>{ev.stopPropagation();teOpen(o.row||b.parentNode,o);};
  return b;
}
function tmNow(id){const i=$('#'+id);if(i&&!i.dataset.set)i.value=nowHM();}
function tmReset(id){const i=$('#'+id);if(i){delete i.dataset.set;i.value=nowHM();}}
['ml_time','m_time'].forEach(id=>{const i=$('#'+id);if(i)i.addEventListener('change',()=>{i.dataset.set='1';});});
function mealItemsText(items){
  return (items||[]).map(i=>i.n+(i.g>0?(' '+lfFmt(i.g)+' '+(i.u||'g')+(i.dry?' dry':'')):
    (i.q!==1?' ×'+i.q:''))).join(', ');
}
/* A weight field for one row of a meal. dry says "dry weight" beside it. */
function wtLine(o,unit,dry,label,onChange){
  const w=el('div','bkw');w.appendChild(el('span','','or by weight'));
  const i=document.createElement('input');i.type='number';i.className='bkg';i.min='0';i.step='1';
  i.inputMode='decimal';i.setAttribute('aria-label',label+', weight in '+unit);
  if(o.g>0)i.value=o.g;
  i.oninput=()=>{const n=Number(i.value);o.g=(isFinite(n)&&n>0)?n:0;onChange();};
  w.appendChild(i);w.appendChild(el('span','',unit));
  if(dry)w.appendChild(el('b','dryw','dry weight'));
  return w;
}
function mcQ(x){const L=MC.lib[x.n];return (x.g>0&&L&&L.pq>0)?x.g/L.pq:x.q;}
function mcEstDraw(){
  const pairs=mcPairs(mcCur,mcSel).concat(mcSel.extra.map(x=>[x.n,mcQ(x)]));
  const e=mcEst(pairs);const h=$('#mcEst');
  if(h)h.textContent=pairs.length?('About '+e[0].toFixed(0)+' g protein · '+Math.round(e[1])+' kcal (estimated)'):'Add what you had.';
  const go=$('#mcGo');if(go)go.disabled=!pairs.length;
}

'''
BOOT_OLD = '/* ---------- boot ---------- */\n'
BOOT_NEW = JS3 + BOOT_OLD

EDITS = [
    ("header", HEAD_OLD, HEAD_NEW),
    ("version", VER_OLD, VER_NEW),
    ("library schema", SCH_OLD, SCH_NEW),
    ("schema version", SV_OLD, SV_NEW),
    ("library columns", COLS_OLD, COLS_NEW),
    ("migrate hook", MIG_OLD, MIG_NEW),
    ("seed hook", SEED_OLD, SEED_NEW),
    ("library routes", LIB_OLD, LIB_NEW),
    ("basket route", MEALS_OLD, MEALS_NEW),
    ("card items by weight", ITEMS_OLD, ITEMS_NEW),
    ("card log time", LOG_OLD, LOG_NEW),
    ("card edit audit", REPL_OLD, REPL_NEW),
    ("card meta weights", META_OLD, META_NEW),
    ("cards lib weights", CARDS_OLD, CARDS_NEW),
    ("search weights", SEARCH_OLD, SEARCH_NEW),
    ("doses today ids", DOSES_OLD, DOSES_NEW),
    ("export items", EXPORT_OLD, EXPORT_NEW),
    ("export library", EXPORT2_OLD, EXPORT2_NEW),
    ("food library code", CODE_OLD, CODE_NEW),
    ("css", CSS_OLD, CSS_NEW),
    ("inline new food", NF_OLD, NF_NEW),
    ("meds date and time", MDT_OLD, MDT_NEW),
    ("library hint", HINT_OLD, HINT_NEW),
    ("basket", JS1_OLD, JS1_NEW),
    ("library manager", JS2_OLD, JS2_NEW),
    ("day meal time", MLROW_OLD, MLROW_NEW),
    ("day meal items", MLITEMS_OLD, MLITEMS_NEW),
    ("past meal edit", MLEDIT_OLD, MLEDIT_NEW),
    ("card fresh weights", FRESH_OLD, FRESH_NEW),
    ("edit hint", EDHINT_OLD, EDHINT_NEW),
    ("card body", MCBODY_OLD, MCBODY_NEW),
    ("picker weights", PICK_OLD, PICK_NEW),
    ("today meal time", TODAY_OLD, TODAY_NEW),
    ("today meal edit", TODAYED_OLD, TODAYED_NEW),
    ("meds today times", PRN_OLD, PRN_NEW),
    ("save meal", SAVEMEAL_OLD, SAVEMEAL_NEW),
    ("save dose", SAVEDOSE_OLD, SAVEDOSE_NEW),
    ("review dose times", RV_OLD, RV_NEW),
    ("meals tab now", TAB1_OLD, TAB1_NEW),
    ("meds tab now", TAB2_OLD, TAB2_NEW),
    ("time helpers", BOOT_OLD, BOOT_NEW),
]

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
    print("GutLog food library by weight, and a time on every entry -> v" + VERSION)
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

    bak = a.file + ".bak-v3310-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3310_foodlib.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

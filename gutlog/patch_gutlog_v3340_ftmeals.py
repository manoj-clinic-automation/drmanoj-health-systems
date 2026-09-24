#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.33.0 -> v3.34.0  ::  GUTLOG_V3340_FTMEALS -- Week 0 of the food
test reads dinner from Meals; the Now page scrolls less.

WHY: on 24-Sep the Food Test card asked for dinner time, size and cramp,
and the dinner was already in Meals. He would not enter it twice -- rightly
-- and because a step advances only when logged, Week 0 could never finish
and the Week 1 ladder due on 30-Sep would never start.

WHAT
  * A meal logged as Dinner (the Meals tab or the Now meal card, any path
    that writes a meal) completes that day's Week 0 step by itself:
    ft_dinner_sync(day) writes one ft_log row with meal_id set, ltime = the
    earliest Dinner's time, and size by a stated rule (FT_SIZE_RULE): that
    day's Dinner kcal against the median of his Dinner days in the 28 days
    before -- small below 75 %, large above 125 %, "usual" otherwise and
    whenever kcal or three earlier Dinner days are missing. Nothing guessed.
  * Editing, re-timing, moving or deleting the Dinner re-syncs the day: the
    step follows it or goes. Steps are renumbered in day order, so a hole
    never shifts the plan. Once a later week has a logged step Week 0 is
    closed: a deleted Dinner then only unlinks its row.
  * The Food Test card asks only "Cramp after dinner? yes / no" -- optional,
    blank still counts. /api/ft/log refuses a Week 0 dinner (409): there is
    no second dinner entry anywhere. Undo refuses a row that comes from Meals.
  * The Now page: medicines first, then meals, then symptoms, then the Food
    Test card, then the rest. The Meals card and the Food Test card start
    folded; their headers carry today's summary ("Breakfast ✓ · Lunch ✓ ·
    Dinner —", "Week 0 · day 3 — dinner logged ✓").
  * Meds -> PRN -> Add medicine goes on to Meds -> Salts, as the Now tab's
    Add medicine already does.

No schema change. The one-off backfill is migrate_gutlog_v3340_week0.py
(dry run by default, sqlite3.backup() before writing), which calls the same
ft_dinner_sync(). Anchor-verified, idempotent, compile-checked, .bak,
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
MARKER = "GUTLOG_V3340_FTMEALS"
PREV = "GUTLOG_V3330_PAINSITE"
VERSION = "3.34.0"

E = []

E.append(("header",
          'GUTLOG_V3330_PAINSITE -- nine-region pain site, onset time, one true-time day.\n',
          'GUTLOG_V3330_PAINSITE -- nine-region pain site, onset time, one true-time day.\n'
          'GUTLOG_V3340_FTMEALS -- Week 0 reads dinner from Meals; the Now page folds and reorders.\n'))
E.append(("version", 'APP_VERSION = "3.33.0"   # GUTLOG_V3330_PAINSITE ',
          'APP_VERSION = "3.34.0"   # GUTLOG_V3340_FTMEALS GUTLOG_V3330_PAINSITE '))

# ------------------------------------------------ every path that writes a meal
E.append(("meals tab sync",
          '''           [day, mtime, d.get("slot") or "Meal",
            json.dumps(clean), round(p,1), round(k), round(f,1), round(fs,2), note(d)])
    return jsonify(ok=True, protein=round(p,1))
''',
          '''           [day, mtime, d.get("slot") or "Meal",
            json.dumps(clean), round(p,1), round(k), round(f,1), round(fs,2), note(d)])
    ft_dinner_sync(day)   # GUTLOG_V3340_FTMEALS
    return jsonify(ok=True, protein=round(p,1))
'''))
E.append(("meal card sync",
          '''                              for x in extra])))
    db().commit()
    return {"ok": True, "id": mid, "protein": round(p, 1), "missing": missing}, 200
''',
          '''                              for x in extra])))
    db().commit()
    # GUTLOG_V3340_FTMEALS -- a Dinner, new or edited, carries Week 0 with it;
    # an edit that moved the meal re-syncs the day it left as well.
    ft_dinner_sync(day, prior["day"] if prior else None)
    return {"ok": True, "id": mid, "protein": round(p, 1), "missing": missing}, 200
'''))
E.append(("meal delete sync",
          '''def api_meal_delete(mid):
    db().execute("DELETE FROM meal_meta WHERE meal_id=?", (mid,))
    db().execute("DELETE FROM meals WHERE id=?", (mid,))
    db().commit()
    return jsonify(ok=True)
''',
          '''def api_meal_delete(mid):
    r = db().execute("SELECT day FROM meals WHERE id=?", (mid,)).fetchone()   # GUTLOG_V3340_FTMEALS
    db().execute("DELETE FROM meal_meta WHERE meal_id=?", (mid,))
    db().execute("DELETE FROM meals WHERE id=?", (mid,))
    db().commit()
    if r:
        ft_dinner_sync(r["day"])
    return jsonify(ok=True)
'''))
E.append(("meal again sync",
          '''        db().execute("INSERT OR REPLACE INTO meal_meta(meal_id, card, choices, onion, extra) "
                     "VALUES(?,?,?,?,?)", (nid, m["card"], m["choices"], m["onion"], m["extra"]))
        db().commit()
    return jsonify(ok=True, id=nid)
''',
          '''        db().execute("INSERT OR REPLACE INTO meal_meta(meal_id, card, choices, onion, extra) "
                     "VALUES(?,?,?,?,?)", (nid, m["card"], m["choices"], m["onion"], m["extra"]))
        db().commit()
    ft_dinner_sync(today())   # GUTLOG_V3340_FTMEALS
    return jsonify(ok=True, id=nid)
'''))
E.append(("generic delete sync",
          '''    db().execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    db().commit(); return jsonify(ok=True)
''',
          '''    # GUTLOG_V3340_FTMEALS -- a Dinner deleted from Day by day or Review
    # takes its Week 0 step with it, as it does from the Meals tab.
    gone = db().execute("SELECT day FROM meals WHERE id=?", (rid,)).fetchone() if table == "meals" else None
    db().execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    db().commit()
    if gone:
        db().execute("DELETE FROM meal_meta WHERE meal_id=?", (rid,))
        ft_dinner_sync(gone["day"])
    return jsonify(ok=True)
'''))
E.append(("retime sync",
          '''    db().execute("UPDATE " + tbl + " SET day=?, " + col + "=? WHERE id=?",
                 (new_d, new_t, rid))
    db().execute(
        "INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
        "VALUES(?,?,?,?,?,?,?)", (tbl, rid, old_d, old_t, new_d, new_t, now_s()))
    db().commit()
    return jsonify(ok=True)
''',
          '''    db().execute("UPDATE " + tbl + " SET day=?, " + col + "=? WHERE id=?",
                 (new_d, new_t, rid))
    db().execute(
        "INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
        "VALUES(?,?,?,?,?,?,?)", (tbl, rid, old_d, old_t, new_d, new_t, now_s()))
    db().commit()
    if tbl == "meals":   # GUTLOG_V3340_FTMEALS -- re-timing the Dinner re-times the step
        ft_dinner_sync(old_d, new_d)
    return jsonify(ok=True)
'''))
E.append(("ft retime sync",
          '''            db().execute("UPDATE meals SET day=?, mtime=? WHERE id=?", (day, t, row["meal_id"]))
            db().execute("INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                         "VALUES(?,?,?,?,?,?,?)",
                         ("meals", row["meal_id"], m["day"], m["mtime"] or "", day, t, now_s()))
    db().commit()
    return jsonify(ok=True)
''',
          '''            db().execute("UPDATE meals SET day=?, mtime=? WHERE id=?", (day, t, row["meal_id"]))
            db().execute("INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                         "VALUES(?,?,?,?,?,?,?)",
                         ("meals", row["meal_id"], m["day"], m["mtime"] or "", day, t, now_s()))
    db().commit()
    if tbl[0] == "ft_log" and row["kind"] == "dinner":   # GUTLOG_V3340_FTMEALS
        ft_dinner_sync(old_d, day)
    return jsonify(ok=True)
'''))

# ------------------------------------------------ no second dinner entry
E.append(("ft log refuses dinner",
          '''    st = steps[cur]
    kind = st["kind"]
    amount, unit, size, cramp, meal_id = None, "", "", None, None
''',
          '''    st = steps[cur]
    kind = st["kind"]
    if kind == "dinner":   # GUTLOG_V3340_FTMEALS
        return jsonify(ok=False, err="Week 0 reads dinner from Meals — log it there and "
                                     "this day counts by itself."), 409
    amount, unit, size, cramp, meal_id = None, "", "", None, None
'''))
E.append(("ft undo refuses meal row",
          '''    if not r:
        return jsonify(ok=False, err="Already gone."), 404
    if r["kind"] in FT_STEP_KINDS:
''',
          '''    if not r:
        return jsonify(ok=False, err="Already gone."), 404
    if r["kind"] == "dinner" and r["meal_id"]:   # GUTLOG_V3340_FTMEALS
        return jsonify(ok=False, err="This day comes from your Dinner in Meals — "
                                     "edit or delete the dinner there."), 409
    if r["kind"] in FT_STEP_KINDS:
'''))

# ------------------------------------------------ the card's state
E.append(("state today step",
          '''    st["today_step"] = dict(t) if t else None
    if st["today_step"]:
''',
          '''    st["today_step"] = dict(t) if t else None
    if st["today_step"]:
        st["today_step"]["from_meal"] = bool(t["meal_id"]) and t["kind"] == "dinner"   # GUTLOG_V3340_FTMEALS
'''))
E.append(("state header",
          '''    st["score_today"] = dict(sc) if sc else None
    st["offer_stop"] = ft_offer_stop(day) if (sc and sc["clear"]) else None
    return st
''',
          '''    st["score_today"] = dict(sc) if sc else None
    st["offer_stop"] = ft_offer_stop(day) if (sc and sc["clear"]) else None
    st["header"] = ft_header(st, it, cur, steps, items)   # GUTLOG_V3340_FTMEALS
    return st
'''))
E.append(("results cramp blank",
          '''                what = "dinner %s · %s · cramp %s" % (
                    r.get("ltime") or "", r.get("size") or "size not noted",
                    "yes" if r.get("cramp") else "no")
''',
          '''                what = "dinner %s · %s · cramp %s" % (
                    r.get("ltime") or "", r.get("size") or "size not noted",
                    "not noted" if r.get("cramp") is None else ("yes" if r.get("cramp") else "no"))
'''))

# ------------------------------------------------ the server code block
CODE = r'''# ------------------------------------------------------------ week 0 from meals
# GUTLOG_V3340_FTMEALS. Week 0 asks for the dinner; the dinner is already in
# Meals. So a meal logged as Dinner completes that day's Week 0 step by itself
# and the card asks only about cramp. ONE function does it, and every path
# that writes, edits, moves or deletes a meal calls it with the day(s) it
# touched. Nothing is invented: a day with no Dinner stays unlogged.
FT_DINNER_SLOT = "Dinner"
# The size rule, stated: that day's Dinner kcal (all meals logged as Dinner
# that day -- a split dinner is one dinner) against the MEDIAN of his Dinner
# days in the 28 days before. Below 75 % small, above 125 % large, otherwise
# usual -- and "usual" whenever the day's kcal is missing or fewer than three
# earlier Dinner days carry kcal. No guessing.
FT_SIZE_RULE = {"days": 28, "min_days": 3, "small_below": 0.75, "large_above": 1.25}


def ft_week0():
    for it in ft_items():
        if it.get("kind") == "dinner":
            return it
    return None


def ft_week0_from():
    """The first day a Dinner can count for Week 0: the `ft_week0_from`
    setting, or the day the plan was seeded (then kept, so a re-seed does not
    move it)."""
    v = (setting("ft_week0_from") or "").strip()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        pass
    r = db().execute("SELECT MIN(seeded) AS s FROM ft_items").fetchone()
    s = ((r["s"] if r else "") or "")[:10]
    try:
        s = date.fromisoformat(s).isoformat()
    except ValueError:
        return None
    set_setting("ft_week0_from", s)
    return s


def ft_usual_dinner_kcal(day):
    lo = (date.fromisoformat(day) - timedelta(days=FT_SIZE_RULE["days"])).isoformat()
    tot = sorted(r["k"] for r in db().execute(
        "SELECT day, SUM(COALESCE(kcal, 0)) AS k FROM meals WHERE slot=? AND day>=? AND day<? "
        "GROUP BY day", (FT_DINNER_SLOT, lo, day)).fetchall() if (r["k"] or 0) > 0)
    n = len(tot)
    if n < FT_SIZE_RULE["min_days"]:
        return None
    return tot[n // 2] if n % 2 else (tot[n // 2 - 1] + tot[n // 2]) / 2.0


def ft_dinner_size(day, kcal):
    usual = ft_usual_dinner_kcal(day)
    if not kcal or kcal <= 0 or not usual:
        return "usual"
    if kcal < usual * FT_SIZE_RULE["small_below"]:
        return "small"
    if kcal > usual * FT_SIZE_RULE["large_above"]:
        return "large"
    return "usual"


def ft_dinner_sync(*days):
    """Make each day's Week 0 row match that day's Dinner in Meals. Returns
    the number of rows added. A new row only while Week 0 is unfinished, not
    paused, on or after ft_week0_from(); an existing row follows its Dinner's
    time and size; a row whose Dinner is gone is deleted -- or, once a later
    week has a logged step, only unlinked, so a finished Week 0 is never
    re-opened. Steps are then renumbered in day order."""
    it = ft_week0()
    if not it:
        return 0
    slug = it["slug"]
    frm = ft_week0_from()
    try:
        paused = json.loads(setting("ft_paused") or "null")
    except ValueError:
        paused = None
    closed = bool(db().execute(
        "SELECT 1 FROM ft_log WHERE slug<>? AND kind IN ('dose','washout','decline') LIMIT 1",
        (slug,)).fetchone())
    added = 0
    for day in sorted(set(x for x in days if x)):
        meals = db().execute("SELECT id, mtime, kcal FROM meals WHERE day=? AND slot=? "
                             "ORDER BY mtime, id", (day, FT_DINNER_SLOT)).fetchall()
        row = db().execute("SELECT * FROM ft_log WHERE slug=? AND kind='dinner' AND day=? "
                           "ORDER BY id LIMIT 1", (slug, day)).fetchone()
        if meals:
            first = meals[0]
            size = ft_dinner_size(day, sum((m["kcal"] or 0) for m in meals))
            if row:
                db().execute("UPDATE ft_log SET meal_id=?, ltime=?, size=? WHERE id=?",
                             (first["id"], first["mtime"] or "", size, row["id"]))
            elif frm and day >= frm and not paused and not closed:
                cur, steps = ft_progress(it, ft_rows(slug))
                if cur < len(steps):
                    insert("ft_log", ["slug", "step", "kind", "day", "ltime", "size", "cramp",
                                      "reason", "meal_id"],
                           [slug, cur, "dinner", day, first["mtime"] or "", size, None, "",
                            first["id"]])
                    added += 1
        elif row and row["meal_id"]:
            if closed:
                db().execute("UPDATE ft_log SET meal_id=NULL WHERE id=?", (row["id"],))
            else:
                db().execute("DELETE FROM ft_log WHERE id=?", (row["id"],))
    for i, r in enumerate(db().execute(
            "SELECT id FROM ft_log WHERE slug=? AND kind='dinner' ORDER BY day, ltime, id",
            (slug,)).fetchall()):
        db().execute("UPDATE ft_log SET step=? WHERE id=?", (i, r["id"]))
    db().commit()
    return added


def ft_header(st, it, cur, steps, items):
    """The folded Food Test card's header: today's step only."""
    if st.get("paused"):
        return "Paused"
    t = st.get("today_step")
    if t:
        src = [x for x in items if x["slug"] == t["slug"]]
        wk = src[0].get("week") if src else ""
        if t["kind"] == "dinner":
            return "%s · day %d — dinner logged ✓" % (wk, (t["step"] or 0) + 1)
        if t["kind"] == "dose":
            return "%s · %s %s %s ✓" % (wk, src[0].get("label") if src else "", _nut_num(t["amount"]),
                                        t["unit"] or "g")
        if t["kind"] == "washout":
            return "%s · washout day ✓" % wk
        return "%s · passed over ✓" % wk
    if it is None:
        return "Every planned week is done"
    s = steps[cur]
    if s["kind"] == "dinner":
        return "%s · day %d — dinner not logged yet" % (it.get("week"), s["n"])
    if s["kind"] == "washout":
        return "%s · washout day %d of %d" % (it.get("week"), s["n"], s["of"])
    return "%s · %s %s %s" % (it.get("week"), it.get("label"), _nut_num(s["g"]), s["unit"])


@app.route("/api/ft/cramp", methods=["POST"])
@login_required
def api_ft_cramp():
    """The one Week 0 question: cramp after dinner -- yes, no, or blank."""
    d = J()
    try:
        rid = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Bad entry."), 400
    row = db().execute("SELECT id FROM ft_log WHERE id=? AND kind='dinner'", (rid,)).fetchone()
    if not row:
        return jsonify(ok=False, err="That dinner day is gone."), 404
    v = d.get("cramp")
    val = None if v in (None, "") else (1 if v in (1, True, "1", "yes") else 0)
    db().execute("UPDATE ft_log SET cramp=? WHERE id=?", (val, rid))
    db().commit()
    return jsonify(ok=True, cramp=val)


'''
E.append(("week0 code", '# ------------------------------------------------------------ pain site\n',
          CODE + '# ------------------------------------------------------------ pain site\n'))

# ================================================================== the page
OLD_NOW = r'''<section class="tab sel" id="tab-now">
  <div id="nowMirror"></div>
  <div id="nowFT"></div>
  <div id="nowStock"></div>
  <div id="nowOrder"></div>
  <div id="nowMedStatus"></div>
  <div class="card" id="nowBP">
    <p class="q">Blood pressure</p>
    <div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="—"></div>
      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="—"></div>
      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="—"></div>
    </div>
    <button type="button" class="btn primary" id="n_bpSave">Save reading</button>
    <p class="hint" id="n_bpLast" style="margin:10px 0 0"></p>
  </div>

  <div class="card" id="nowMeal">
    <div class="dwtop"><p class="q">Meal</p><span class="fs" id="mealSum"></span></div>
    <div class="chips" id="mealTabs"></div>
    <div id="mealBody"></div>
    <div id="mealToday"></div>
  </div>
  <div class="card" id="nowPlan" style="display:none">
    <div class="dwtop"><p class="q">Today against the plan</p><span class="fs" id="planSum"></span></div>
    <div id="planBars"></div>
    <div id="planTips"></div>
    <button type="button" class="btn ghost" id="planMore" style="margin-top:8px">This week</button>
    <div id="planWeek" style="display:none"></div>
  </div>


  <div class="card fold" id="nowDoses">
    <button type="button" class="fold-h">
      <span class="ft">Today&rsquo;s doses</span><span class="fs" id="doseSum"></span><span class="fc"></span>
    </button>
    <div class="cbody"><div id="nowSched"></div></div>
  </div>

  <div class="card fold" id="nowExtraCard">
    <button type="button" class="fold-h">
      <span class="ft">Extra dose</span><span class="fs" id="exSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div class="chips" id="nowExtras"></div>
      <div id="nowExtraList"></div>
      <div class="btnrow">
        <button type="button" class="btn ghost" id="nowShowAll">Show all medicines</button>
        <button type="button" class="btn ghost" id="nowAddMed">Add medicine</button>
      </div>
      <p class="hint" style="margin:10px 0 0">One tap logs it at the current time.</p>
    </div>
  </div>

  <div class="card fold" id="nowAct">
    <button type="button" class="fold-h">
      <span class="ft">Activity</span><span class="fs" id="actSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div id="actTiles"></div>
      <div id="actList"></div>
      <p class="hint" id="actWatch" style="margin:8px 2px 0"></p>
    </div>
  </div>

  <div class="card fold" id="nowSym">
'''
NEW_NOW = r'''<section class="tab sel" id="tab-now">
  <div id="nowMirror"></div>
  <!-- GUTLOG_V3340_FTMEALS -- medicines, then meals, then symptoms, then the
       Food Test, then the rest. Only order and folding changed. -->
  <div class="card fold" id="nowDoses">
    <button type="button" class="fold-h">
      <span class="ft">Today&rsquo;s doses</span><span class="fs" id="doseSum"></span><span class="fc"></span>
    </button>
    <div class="cbody"><div id="nowSched"></div></div>
  </div>

  <div class="card fold" id="nowExtraCard">
    <button type="button" class="fold-h">
      <span class="ft">Extra dose</span><span class="fs" id="exSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div class="chips" id="nowExtras"></div>
      <div id="nowExtraList"></div>
      <div class="btnrow">
        <button type="button" class="btn ghost" id="nowShowAll">Show all medicines</button>
        <button type="button" class="btn ghost" id="nowAddMed">Add medicine</button>
      </div>
      <p class="hint" style="margin:10px 0 0">One tap logs it at the current time.</p>
    </div>
  </div>
  <div id="nowStock"></div>
  <div id="nowOrder"></div>
  <div id="nowMedStatus"></div>

  <div class="card fold" id="nowMeal">
    <button type="button" class="fold-h">
      <span class="ft">Meals</span><span class="fs" id="mealSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div class="chips" id="mealTabs"></div>
      <div id="mealBody"></div>
      <div id="mealToday"></div>
      <p class="hint" id="mealProt" style="margin:8px 2px 0"></p>
    </div>
  </div>
  <div class="card" id="nowPlan" style="display:none">
    <div class="dwtop"><p class="q">Today against the plan</p><span class="fs" id="planSum"></span></div>
    <div id="planBars"></div>
    <div id="planTips"></div>
    <button type="button" class="btn ghost" id="planMore" style="margin-top:8px">This week</button>
    <div id="planWeek" style="display:none"></div>
  </div>

  <div class="card fold" id="nowSym">
'''
E.append(("now order top", OLD_NOW, NEW_NOW))

OLD_MID = r'''      <div id="n_msk"></div>
      <div id="painList"></div>
    </div>
  </div>

  <div class="card" id="nowCtx">
'''
NEW_MID = r'''      <div id="n_msk"></div>
      <div id="painList"></div>
    </div>
  </div>

  <div id="nowFT"></div>

  <div class="card" id="nowBP">
    <p class="q">Blood pressure</p>
    <div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="—"></div>
      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="—"></div>
      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="—"></div>
    </div>
    <button type="button" class="btn primary" id="n_bpSave">Save reading</button>
    <p class="hint" id="n_bpLast" style="margin:10px 0 0"></p>
  </div>

  <div class="card fold" id="nowAct">
    <button type="button" class="fold-h">
      <span class="ft">Activity</span><span class="fs" id="actSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div id="actTiles"></div>
      <div id="actList"></div>
      <p class="hint" id="actWatch" style="margin:8px 2px 0"></p>
    </div>
  </div>

  <div class="card" id="nowCtx">
'''
E.append(("now order rest", OLD_MID, NEW_MID))

E.append(("css",
          '.ftcard summary{cursor:pointer;color:var(--teal);font-weight:600}\n',
          '.ftcard summary{cursor:pointer;color:var(--teal);font-weight:600}\n'
          '/* GUTLOG_V3340_FTMEALS -- folded headers carry a summary; let it wrap at 300px. */\n'
          '#ftCard .fold-h .fs,#nowMeal .fold-h .fs{flex:1 1 auto;min-width:0;text-align:right;\n'
          '  font-size:13px;line-height:1.3}\n'
          '#ftCard .fthd{justify-content:flex-end;margin-bottom:4px}\n'
          '.ftdin .chips{gap:6px;margin:4px 0 6px}\n'))

E.append(("meal summary",
          "  $('#mealSum').textContent=t.length?(t.length+' logged · '+Math.round(p)+' of '+MC.protein_target+' g protein'):'nothing yet today';\n",
          "  /* GUTLOG_V3340_FTMEALS -- the folded header says which meals are in. */\n"
          "  $('#mealSum').textContent=mealSumText(t);\n"
          "  const mp=$('#mealProt');if(mp)mp.textContent=t.length?(t.length+' logged · '+Math.round(p)+' of '+MC.protein_target+' g protein'):'nothing yet today';\n"))

E.append(("meals refresh the food test",
          "  if(!mcEdit){mcCur=mcAuto();mcSel=mcFresh(mcCur);}\n  mcRender();\n  loadPlan();\n}\n",
          "  if(!mcEdit){mcCur=mcAuto();mcSel=mcFresh(mcCur);}\n  mcRender();\n  loadPlan();\n"
          "  if(FT)loadFT();   /* GUTLOG_V3340_FTMEALS -- a Dinner logged, edited or deleted here moves Week 0 */\n}\n"))
E.append(("prn add goes to salts",
          "  try{await post('/api/prnmeds',{name:n});await loadPRN();toast('Added');}catch(e){toast(e.message);}};\n",
          "  try{await post('/api/prnmeds',{name:n});await loadPRN();loadSchedMeds();   /* GUTLOG_V3340_FTMEALS */\n"
          "    switchTab('meds');setSeg('meds','salts');toast('Added. Now give its salt and strength.');}\n"
          "  catch(e){toast(e.message);}};\n"))

# ------------------------------------------------ the Food Test card
E.append(("ft fold state", "let FT=null,ftScoreOpen=false,ftSc={},ftStopHidden=false;\n",
          "let FT=null,ftScoreOpen=false,ftSc={},ftStopHidden=false;\n"
          "let ftFold=false;   /* GUTLOG_V3340_FTMEALS -- the card starts folded */\n"))
E.append(("ft card folds",
          '''  const c=el('div','card ftcard');c.id='ftCard';box.appendChild(c);
  const hd=el('div','fthd');hd.appendChild(el('p','q','Food test'));
  const a=el('a','fthist','Results');a.href='/foodtest';hd.appendChild(a);c.appendChild(hd);
''',
          '''  /* GUTLOG_V3340_FTMEALS -- folded; the header says only today's step. */
  const oc=el('div','card fold ftcard'+(ftFold?' open':''));oc.id='ftCard';box.appendChild(oc);
  const fh=el('button','fold-h');fh.type='button';fh.appendChild(el('span','ft','Food test'));
  const fs=el('span','fs',j.header||'');fs.id='ftSum';fh.appendChild(fs);fh.appendChild(el('span','fc',''));
  fh.onclick=()=>{oc.classList.toggle('open');ftFold=oc.classList.contains('open');};
  oc.appendChild(fh);
  const c=el('div','cbody');oc.appendChild(c);
  const hd=el('div','fthd');
  const a=el('a','fthist','Results');a.href='/foodtest';hd.appendChild(a);c.appendChild(hd);
'''))
E.append(("ft dinner done",
          "    if(j.today_step){\n      const t=el('div','ftdone');\n",
          "    if(j.today_step&&j.today_step.from_meal){\n"
          "      c.appendChild(ftDinnerDone(j.today_step));c.appendChild(el('p','ftnext','Next: '+cu.text));\n"
          "    }else if(j.today_step){\n      const t=el('div','ftdone');\n"))
E.append(("ft step form dinner",
          '''  }else if(cu.kind==='washout'){
    w.appendChild(el('p','ftsub','Just the evening score today — saving it counts this washout day.'));
    return w;
  }
''',
          '''  }else if(cu.kind==='washout'){
    w.appendChild(el('p','ftsub','Just the evening score today — saving it counts this washout day.'));
    return w;
  }else if(cu.kind==='dinner'){
    /* GUTLOG_V3340_FTMEALS -- nothing to enter here: the Dinner in Meals is the day. */
    w.appendChild(el('p','ftsub','Log dinner in Meals — this day counts by itself. Nothing to enter here.'));
    const go=el('button','btn ghost','Open Meals');go.type='button';go.id='ftToMeals';
    go.onclick=()=>{const m=$('#nowMeal');if(!m)return;m.classList.add('open');
      const i=(MC&&MC.cards)?MC.cards.findIndex(x=>x.name==='Dinner'):-1;
      if(i>=0){mcCur=i;mcEdit=null;mcPick=false;mcSel=mcFresh(i);mcRender();}
      setTimeout(()=>m.scrollIntoView({behavior:'smooth',block:'start'}),80);};
    w.appendChild(go);return w;
  }
'''))
E.append(("ft step form no size",
          '''    a.value=lfFmt(cu.g);a.setAttribute('aria-label','Amount');ar.appendChild(a);ar.appendChild(el('b','',cu.unit));
  }else{
    ar.appendChild(el('span','lb','Size'));ar.appendChild(ftChips('size',FT.sizes.map(s=>[s,s])));
    ar.appendChild(el('span','lb','Cramp'));ar.appendChild(ftChips('cramp',[[0,'No'],[1,'Yes']]));
  }
''',
          '''    a.value=lfFmt(cu.g);a.setAttribute('aria-label','Amount');ar.appendChild(a);ar.appendChild(el('b','',cu.unit));
  }   /* GUTLOG_V3340_FTMEALS -- no dinner size or cramp here: Week 0 is read from Meals */
'''))
E.append(("ft step form taken",
          "  const go=el('button','btn primary',cu.kind==='dose'?'Taken':'Save dinner');go.type='button';go.id='ftTaken';\n",
          "  const go=el('button','btn primary','Taken');go.type='button';go.id='ftTaken';\n"))
E.append(("ft step form body",
          "    if(cu.kind==='dose')body.amount=$('#ftAmt').value;else{body.size=ftSc.size||'';body.cramp=ftSc.cramp===1;}\n",
          "    body.amount=$('#ftAmt').value;\n"))

JS = r'''/* GUTLOG_V3340_FTMEALS -- Week 0 from Meals: the day's dinner is already
   logged, so the card asks only about cramp. Tap the chosen answer again to
   leave it blank; blank still counts the day. */
function ftDinnerDone(t){
  const w=el('div','ftdin');w.id='ftDinner';
  w.appendChild(el('p','ftdone','✓ Today: dinner logged in Meals at '+(t.ltime||'--:--')+(t.size?(' · '+t.size):'')));
  w.appendChild(el('p','lbl','Cramp after dinner? (optional)'));
  const ch=el('div','chips');ch.dataset.k='cramp';
  [[0,'No'],[1,'Yes']].forEach(o=>{const b=el('button','chip'+(t.cramp===o[0]?' sel':''),o[1]);b.type='button';b.dataset.v=o[0];
    b.onclick=async()=>{const v=(t.cramp===o[0])?null:o[0];
      try{await post('/api/ft/cramp',{id:t.id,cramp:v});toast(v===null?'Left blank':'Saved');loadFT();}catch(e){toast(e.message);}};
    ch.appendChild(b);});
  w.appendChild(ch);
  w.appendChild(el('p','ftsub','From your Dinner in Meals — change its time or size there.'));
  return w;
}
/* The folded Meals card's header: the three main meals, ✓ or —. */
function mealSumText(t){
  const got=s=>t.some(m=>(m.slot||'')===s);
  const main=['Breakfast','Lunch','Dinner'];
  const other=t.filter(m=>main.indexOf(m.slot||'')<0).length;
  return main.map(s=>s+' '+(got(s)?'✓':'—')).join(' · ')+(other?(' · +'+other):'');
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
    print("GutLog Week 0 from Meals, Now page order and folds -> v" + VERSION)
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
    bak = a.file + ".bak-v3340-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3340_ftmeals.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.31.0 -> v3.32.0  ::  GUTLOG_V3320_FOODTEST -- run the FODMAP
reintroduction from the phone, in under 30 seconds a day.

WHAT IT DOES

  * The plan is DATA. seed_foodtest.py loads his plan file (gitignored, like
    meals.local.json) into ft_items; the settings key ft_cfg holds the
    reminder line, the plan document's id and the safe plate. Nothing in
    this file names his foods, amounts or plan.
  * A Food test card on the Now tab: today's step ("Week 1 · day 2 -- Kala
    chana 25 g dry") with ONE Taken button. The amount can be changed before
    saving, and the day (today or up to six days back) and time (default
    now, the same two lists every time box uses) too.
  * Steps advance when LOGGED, never by the calendar: ft_progress() walks
    the logged steps, so a missed day leaves the next undone step where it
    was. Skip today and Pause (holiday, operating list, illness, other) are
    noted as gaps, never as results. One step per item per day.
  * The evening score: pain 0-10, bloating, urgency, Bristol -- all four
    required -- plus "clear symptoms today?". Time defaults to now; the
    score re-opens for editing. Washout days ask only this.
  * The stop rule is OFFERED, never applied: a score marked clear, on a
    ladder with a recent dose, brings up "Stop this ladder and record the
    limit at N g?". One tap records "limit reached at N g" and moves to
    washout. Nothing is ever concluded from the scores themselves.
  * An outcome per group -- Tolerated / Limit at N g / Not tolerated / Not
    tested -- set by him when a week ends, changeable later.
  * /foodtest: one block per group -- the doses with their dates and times,
    the score for each challenge day PLUS the first washout day (reactions
    run 24-48 h behind), the outcome, and two flags: a medicine start, stop
    or dose change recorded in GutLog inside that window (med_schedule
    effective dates, courses), and a gap of more than one day inside a
    ladder. Every time on it is editable (/api/ft/retime, audited in
    `edits`). Also the plan PDF link, the cooking notes and the safe plate.
  * A dose also writes an ordinary meal when the item names a library food
    with a weight (seed_foodtest.py adds those from the USDA table), so the
    day's nutrition totals include it; re-timing or undoing the step moves
    or removes that meal with it.

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
MARKER = "GUTLOG_V3320_FOODTEST"
PREV = "GUTLOG_V3310_FOODLIB"
VERSION = "3.32.0"

HEAD_OLD = 'GUTLOG_V3310_FOODLIB -- foods by weight with a sourced table; every time editable.\n'
HEAD_NEW = ('GUTLOG_V3310_FOODLIB -- foods by weight with a sourced table; every time editable.\n'
            'GUTLOG_V3320_FOODTEST -- the FODMAP reintroduction, run from the Now tab.\n')

VER_OLD = 'APP_VERSION = "3.31.0"   # GUTLOG_V3310_FOODLIB '
VER_NEW = 'APP_VERSION = "3.32.0"   # GUTLOG_V3320_FOODTEST GUTLOG_V3310_FOODLIB '

SCH_OLD = 'CREATE INDEX IF NOT EXISTS ix_plan_files_plan ON plan_files(plan_id, id DESC);\n'
SCH_NEW = ('CREATE INDEX IF NOT EXISTS ix_plan_files_plan ON plan_files(plan_id, id DESC);\n'
           '-- GUTLOG_V3320_FOODTEST. The plan is data (ft_items, seeded from his\n'
           '-- gitignored file); what he did is ft_log and ft_score; what he decided\n'
           '-- is ft_outcome. Tables in SCHEMA, so no migration step.\n'
           'CREATE TABLE IF NOT EXISTS ft_items (\n'
           '  slug TEXT PRIMARY KEY, pos INTEGER NOT NULL, data TEXT NOT NULL, seeded TEXT);\n'
           'CREATE TABLE IF NOT EXISTS ft_log (\n'
           '  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT DEFAULT \'\', step INTEGER,\n'
           '  kind TEXT NOT NULL, day TEXT NOT NULL, ltime TEXT DEFAULT \'\', amount REAL,\n'
           '  unit TEXT DEFAULT \'\', size TEXT DEFAULT \'\', cramp INTEGER, reason TEXT DEFAULT \'\',\n'
           '  meal_id INTEGER, created TEXT);\n'
           'CREATE INDEX IF NOT EXISTS ix_ft_log_day ON ft_log(day);\n'
           'CREATE TABLE IF NOT EXISTS ft_score (\n'
           '  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, stime TEXT DEFAULT \'\',\n'
           '  pain INTEGER, bloating INTEGER, urgency INTEGER, bristol INTEGER,\n'
           '  clear INTEGER DEFAULT 0, created TEXT, updated TEXT);\n'
           'CREATE INDEX IF NOT EXISTS ix_ft_score_day ON ft_score(day);\n'
           'CREATE TABLE IF NOT EXISTS ft_outcome (\n'
           '  slug TEXT PRIMARY KEY, outcome TEXT NOT NULL, limit_g REAL, set_at TEXT);\n')

CODE = r'''# ------------------------------------------------------------ food test
# GUTLOG_V3320_FOODTEST. The FODMAP reintroduction, run from the phone. The
# plan is data in ft_items; progress is worked out from what was LOGGED, so
# a missed day never moves the plan on; the stop rule is offered, never
# applied; and nothing here draws a conclusion from a score.
FT_OUTCOMES = (("tolerated", "Tolerated"), ("limit", "Limit at N g"),
               ("not_tolerated", "Not tolerated"), ("not_tested", "Not tested"))
FT_REASONS = ("Holiday", "Operating list", "Illness", "Other")
FT_SIZES = ("small", "usual", "large")
FT_STEP_KINDS = ("dose", "washout", "dinner", "decline")


def ft_cfg():
    try:
        return json.loads(setting("ft_cfg") or "{}")
    except ValueError:
        return {}


def ft_items():
    out = []
    for r in db().execute("SELECT slug, pos, data FROM ft_items ORDER BY pos, slug"):
        try:
            it = json.loads(r["data"])
        except ValueError:
            continue
        it["slug"] = r["slug"]
        out.append(it)
    return out


def ft_unit(it):
    return "g " + (it.get("basis") or "dry")


def ft_steps(it):
    """The item as an ordered list of steps: doses then washout days, or
    dinner days for Week 0. A washout the plan does not state is absent."""
    if it.get("kind") == "dinner":
        return [{"kind": "dinner", "n": i + 1} for i in range(int(it.get("days") or 7))]
    out = []
    for i, s in enumerate(it.get("steps") or []):
        out.append({"kind": "dose", "n": i + 1, "g": float(s.get("g") or 0),
                    "unit": ft_unit(it), "cooked": s.get("cooked") or "",
                    "cond": s.get("cond") or ""})
    for i in range(int(it.get("washout") or 0)):
        out.append({"kind": "washout", "n": i + 1, "of": int(it.get("washout") or 0)})
    return out


def ft_rows(slug):
    return [dict(r) for r in db().execute(
        "SELECT * FROM ft_log WHERE slug=? ORDER BY day, ltime, id", (slug,)).fetchall()]


def ft_progress(it, rows):
    """(cursor, steps). The cursor is the first step not yet logged -- by what
    was logged, never by the date. After a stop the untaken doses are passed
    over and the next step is the first washout day."""
    steps = ft_steps(it)
    done = set(r["step"] for r in rows if r["kind"] in FT_STEP_KINDS and r["step"] is not None)
    stopped = any(r["kind"] == "stop" for r in rows)
    cur = 0
    while cur < len(steps) and (cur in done or (stopped and steps[cur]["kind"] == "dose")):
        cur += 1
    return cur, steps


def ft_outcomes():
    return dict((r["slug"], dict(r)) for r in db().execute("SELECT * FROM ft_outcome"))


def ft_step_text(it, st):
    if st["kind"] == "dinner":
        return "%s · day %d — dinner changes only" % (it.get("week"), st["n"])
    if st["kind"] == "washout":
        return "%s · washout day %d of %d — safe plate only" % (it.get("week"), st["n"], st["of"])
    return "%s · day %d — %s %s %s" % (it.get("week"), st["n"], it.get("label"),
                                                 _nut_num(st["g"]), st["unit"])


def ft_current():
    """The item and step that come next, or why nothing does."""
    outs = ft_outcomes()
    need = []
    for it in ft_items():
        rows = ft_rows(it["slug"])
        cur, steps = ft_progress(it, rows)
        started = any(r["kind"] in FT_STEP_KINDS for r in rows)
        if cur >= len(steps):
            if it.get("kind") != "dinner" and it["slug"] not in outs:
                doses = [r for r in rows if r["kind"] == "dose"]
                need.append({"slug": it["slug"], "label": it.get("label"), "week": it.get("week"),
                             "last_g": doses[-1]["amount"] if doses else None,
                             "unit": ft_unit(it)})
            continue
        if not started and it["slug"] in outs:
            continue                                   # an optional week he declined
        wait = ""
        if it.get("optional") and not started and it.get("requires"):
            req = outs.get(it["requires"])
            if req is None:
                wait = "needs the outcome of the earlier week first"
            elif req["outcome"] != "tolerated":
                continue                               # the plan offers it only after a pass
        return it, cur, steps, rows, need, wait
    return None, 0, [], [], need, ""


def ft_state(day=None):
    day = day or today()
    cfg = ft_cfg()
    items = ft_items()
    if not items:
        return {"seeded": False}
    it, cur, steps, rows, need, wait = ft_current()
    try:
        paused = json.loads(setting("ft_paused") or "null")
    except ValueError:
        paused = None
    st = {"seeded": True, "day": day, "reminder": cfg.get("reminder") or "",
          "plan_url": ("/plan/%d/file" % int(cfg["plan_id"])) if cfg.get("plan_id") else "",
          "paused": paused, "need_outcome": need, "active": it is not None,
          "reasons": list(FT_REASONS), "sizes": list(FT_SIZES),
          "outcomes": [{"key": k, "label": l} for k, l in FT_OUTCOMES]}
    st["skipped_today"] = bool(db().execute(
        "SELECT 1 FROM ft_log WHERE kind='skip' AND day=?", (day,)).fetchone())
    t = db().execute("SELECT * FROM ft_log WHERE kind IN ('dose','washout','dinner','decline') "
                     "AND day=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    st["today_step"] = dict(t) if t else None
    if st["today_step"]:
        src = [x for x in items if x["slug"] == t["slug"]]
        st["today_step"]["text"] = (ft_step_text(src[0], ft_steps(src[0])[t["step"]])
                                    if src and t["step"] is not None
                                    and t["step"] < len(ft_steps(src[0])) else "")
    if it is not None:
        s = steps[cur]
        st["current"] = dict(s, slug=it["slug"], step=cur, text=ft_step_text(it, s),
                             week=it.get("week"), label=it.get("label"),
                             meal=it.get("meal") or "", cooking=it.get("cooking") or "",
                             notes=it.get("notes") or [], optional=bool(it.get("optional")),
                             started=any(r["kind"] in FT_STEP_KINDS for r in rows),
                             wait=wait, group=it.get("group") or "")
    sc = db().execute("SELECT * FROM ft_score WHERE day=? ORDER BY id DESC LIMIT 1",
                      (day,)).fetchone()
    st["score_today"] = dict(sc) if sc else None
    st["offer_stop"] = ft_offer_stop(day) if (sc and sc["clear"]) else None
    return st


def ft_offer_stop(day):
    """The ladder a clear-symptom day could stop: the one with a dose on that
    day or the two before, not already stopped and with no outcome yet."""
    lo = (date.fromisoformat(day) - timedelta(days=2)).isoformat()
    outs = ft_outcomes()
    for it in ft_items():
        if it.get("kind") == "dinner" or it["slug"] in outs:
            continue
        rows = ft_rows(it["slug"])
        if any(r["kind"] == "stop" for r in rows):
            continue
        doses = [r for r in rows if r["kind"] == "dose" and lo <= r["day"] <= day]
        if doses:
            d = doses[-1]
            return {"slug": it["slug"], "label": it.get("label"), "limit_g": d["amount"],
                    "unit": d["unit"]}
    return None


def _ft_meal(it, amount, day, ltime):
    """A dose is also food: an ordinary meal row, when the item names a library
    food with a weight. Returns the meal id, or None when there is none."""
    name = ((it.get("library") or {}).get("name") or "").strip()
    if not name:
        return None
    r = db().execute("SELECT * FROM library WHERE item=?", (name,)).fetchone()
    w = lib_weigh(r, amount) if r else None
    if not w:
        return None
    insert("meals", ["day", "mtime", "slot", "items", "protein", "kcal", "fibre", "fscore", "notes"],
           [day, ltime, (it.get("meal") or "Food test")[:30], json.dumps([w]),
            round(w["q"] * w["p"], 1), round(w["q"] * w["k"]), round(w["q"] * w["f"], 1),
            round(w["q"] * FMAP[w["fm"]], 2), "Food test: " + (it.get("label") or "")])
    return db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]


def _ft_when(d):
    day = d.get("day") or today()
    ltime = d.get("time") or now_hm()
    if not _valid_day(day) or not _valid_hm(ltime):
        return None, None, "Pick a real date and time."
    if day == today() and ltime > now_hm():
        return None, None, "That time has not come yet today."
    return day, ltime, None


@app.route("/api/ft")
@login_required
def api_ft():
    return jsonify(ft_state())


@app.route("/api/ft/log", methods=["POST"])
@login_required
def api_ft_log():
    """Log the NEXT step of an item -- only that one, and one a day."""
    d = J()
    day, ltime, err = _ft_when(d)
    if err:
        return jsonify(ok=False, err=err), 400
    it, cur, steps, rows, need, wait = ft_current()
    if it is None or it["slug"] != d.get("slug") or cur != d.get("step"):
        return jsonify(ok=False, err="That is not the next step any more — reload."), 409
    if wait:
        return jsonify(ok=False, err="This week " + wait + "."), 409
    if any(r["day"] == day and r["kind"] in FT_STEP_KINDS for r in rows):
        return jsonify(ok=False, err="A step of this week is already logged on " + plan_dmy(day) + "."), 409
    st = steps[cur]
    kind = st["kind"]
    amount, unit, size, cramp, meal_id = None, "", "", None, None
    if kind == "dose":
        if d.get("decline"):
            if not st.get("cond"):
                return jsonify(ok=False, err="Only a conditional step can be passed over."), 400
            kind = "decline"
        else:
            try:
                amount = float(d.get("amount") if d.get("amount") not in (None, "") else st["g"])
            except (TypeError, ValueError):
                return jsonify(ok=False, err="The amount must be a number."), 400
            if not (0 < amount <= 1000):
                return jsonify(ok=False, err="The amount must be between 0 and 1000 g."), 400
            unit = st["unit"]
            meal_id = _ft_meal(it, amount, day, ltime)
    elif kind == "dinner":
        size = d.get("size") if d.get("size") in FT_SIZES else ""
        cramp = 1 if d.get("cramp") else 0
    insert("ft_log", ["slug", "step", "kind", "day", "ltime", "amount", "unit", "size", "cramp",
                      "reason", "meal_id"],
           [it["slug"], cur, kind, day, ltime, amount, unit, size, cramp, "", meal_id])
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    return jsonify(ok=True, id=rid, meal_id=meal_id)


@app.route("/api/ft/skip", methods=["POST"])
@login_required
def api_ft_skip():
    """A skipped day is a gap, not a result: the next step does not move."""
    d = J()
    day = _valid_day(d.get("day") or today())
    if not day:
        return jsonify(ok=False, err="Pick a real date."), 400
    it = ft_current()[0]
    insert("ft_log", ["slug", "kind", "day", "ltime", "reason"],
           [it["slug"] if it else "", "skip", day, now_hm(),
            d.get("reason") if d.get("reason") in FT_REASONS else "Other"])
    return jsonify(ok=True)


@app.route("/api/ft/pause", methods=["POST"])
@login_required
def api_ft_pause():
    d = J()
    it = ft_current()[0]
    if d.get("resume"):
        p = setting("ft_paused")
        set_setting("ft_paused", "null")
        insert("ft_log", ["slug", "kind", "day", "ltime", "reason"],
               [it["slug"] if it else "", "resume", today(), now_hm(), ""])
        return jsonify(ok=True, was=p)
    reason = d.get("reason") if d.get("reason") in FT_REASONS else "Other"
    set_setting("ft_paused", json.dumps({"since": today(), "reason": reason}))
    insert("ft_log", ["slug", "kind", "day", "ltime", "reason"],
           [it["slug"] if it else "", "pause", today(), now_hm(), reason])
    return jsonify(ok=True)


@app.route("/api/ft/score", methods=["GET", "POST"])
@login_required
def api_ft_score():
    """The evening score: all four fields, one per day, editable."""
    if request.method == "GET":
        day = _valid_day(request.args.get("day") or today())
        r = db().execute("SELECT * FROM ft_score WHERE day=? ORDER BY id DESC LIMIT 1",
                         (day,)).fetchone() if day else None
        return jsonify(score=dict(r) if r else None, day=day)
    d = J()
    day, stime, err = _ft_when(d)
    if err:
        return jsonify(ok=False, err=err), 400

    def iv(k, lo, hi):
        try:
            v = int(d.get(k))
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None
    vals = {"pain": iv("pain", 0, 10), "bloating": iv("bloating", 0, 1),
            "urgency": iv("urgency", 0, 1), "bristol": iv("bristol", 1, 7)}
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        return jsonify(ok=False, err="Fill in all four: " + ", ".join(missing) + "."), 400
    clear = 1 if d.get("clear") else 0
    ex = db().execute("SELECT id FROM ft_score WHERE day=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    if ex:
        db().execute("UPDATE ft_score SET stime=?, pain=?, bloating=?, urgency=?, bristol=?, "
                     "clear=?, updated=? WHERE id=?",
                     (stime, vals["pain"], vals["bloating"], vals["urgency"], vals["bristol"],
                      clear, now_s(), ex["id"]))
        db().commit()
        sid = ex["id"]
    else:
        insert("ft_score", ["day", "stime", "pain", "bloating", "urgency", "bristol", "clear", "updated"],
               [day, stime, vals["pain"], vals["bloating"], vals["urgency"], vals["bristol"],
                clear, now_s()])
        sid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    # A washout day asks only this score, so saving it IS the washout step --
    # on that day, when washout is what comes next and nothing is logged yet.
    washed = False
    it, cur, steps, rows, need, wait = ft_current()
    if (it is not None and steps[cur]["kind"] == "washout"
            and not any(r["day"] == day and r["kind"] in FT_STEP_KINDS for r in rows)):
        insert("ft_log", ["slug", "step", "kind", "day", "ltime"], [it["slug"], cur, "washout", day, stime])
        washed = True
    return jsonify(ok=True, id=sid, washout=washed,
                   offer_stop=ft_offer_stop(day) if clear else None)


@app.route("/api/ft/stop", methods=["POST"])
@login_required
def api_ft_stop():
    """He chose to stop: the last dose is the limit, and washout begins."""
    d = J()
    day = _valid_day(d.get("day") or today())
    offer = ft_offer_stop(day) if day else None
    if not offer or offer["slug"] != d.get("slug"):
        return jsonify(ok=False, err="There is no ladder to stop on that day."), 400
    db().execute("INSERT OR REPLACE INTO ft_outcome(slug, outcome, limit_g, set_at) VALUES(?,?,?,?)",
                 (offer["slug"], "limit", offer["limit_g"], now_s()))
    insert("ft_log", ["slug", "kind", "day", "ltime", "amount", "unit"],
           [offer["slug"], "stop", day, now_hm(), offer["limit_g"], offer["unit"]])
    return jsonify(ok=True, limit_g=offer["limit_g"])


@app.route("/api/ft/outcome", methods=["POST"])
@login_required
def api_ft_outcome():
    d = J()
    slug = d.get("slug")
    if slug not in [it["slug"] for it in ft_items() if it.get("kind") != "dinner"]:
        return jsonify(ok=False, err="No such food group."), 400
    oc = d.get("outcome")
    if oc not in [k for k, _ in FT_OUTCOMES]:
        return jsonify(ok=False, err="Pick an outcome."), 400
    lim = None
    if oc == "limit":
        try:
            lim = float(d.get("limit_g"))
        except (TypeError, ValueError):
            lim = None
        if not lim or lim <= 0:
            return jsonify(ok=False, err="Say the limit in grams."), 400
    db().execute("INSERT OR REPLACE INTO ft_outcome(slug, outcome, limit_g, set_at) VALUES(?,?,?,?)",
                 (slug, oc, lim, now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/ft/retime", methods=["POST"])
@login_required
def api_ft_retime():
    """Change the day or time of a logged step or score. A dose's meal moves
    with it. Every change is recorded in `edits`, like /api/retime."""
    d = J()
    tbl = {"log": ("ft_log", "ltime"), "score": ("ft_score", "stime")}.get(d.get("table"))
    if not tbl:
        return jsonify(ok=False, err="Unknown entry type."), 400
    try:
        rid = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Bad entry."), 400
    row = db().execute("SELECT * FROM " + tbl[0] + " WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify(ok=False, err="Entry not found."), 404
    day, t, err = _ft_when({"day": d.get("day") or row["day"], "time": d.get("time")})
    if err:
        return jsonify(ok=False, err=err), 400
    if day != row["day"]:
        if tbl[0] == "ft_score" and db().execute(
                "SELECT 1 FROM ft_score WHERE day=? AND id<>?", (day, rid)).fetchone():
            return jsonify(ok=False, err="There is already a score on " + plan_dmy(day) + "."), 409
        if tbl[0] == "ft_log" and row["kind"] in FT_STEP_KINDS and db().execute(
                "SELECT 1 FROM ft_log WHERE slug=? AND day=? AND id<>? AND kind IN "
                "('dose','washout','dinner','decline')", (row["slug"], day, rid)).fetchone():
            return jsonify(ok=False, err="A step of this week is already on " + plan_dmy(day) + "."), 409
    old_d, old_t = row["day"], row[tbl[1]] or ""
    if (old_d, old_t) == (day, t):
        return jsonify(ok=True, unchanged=True)
    db().execute("UPDATE " + tbl[0] + " SET day=?, " + tbl[1] + "=? WHERE id=?", (day, t, rid))
    db().execute("INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                 "VALUES(?,?,?,?,?,?,?)", (tbl[0], rid, old_d, old_t, day, t, now_s()))
    if tbl[0] == "ft_log" and row["meal_id"]:
        m = db().execute("SELECT day, mtime FROM meals WHERE id=?", (row["meal_id"],)).fetchone()
        if m:
            db().execute("UPDATE meals SET day=?, mtime=? WHERE id=?", (day, t, row["meal_id"]))
            db().execute("INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                         "VALUES(?,?,?,?,?,?,?)",
                         ("meals", row["meal_id"], m["day"], m["mtime"] or "", day, t, now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/ft/undo/<int:lid>", methods=["POST"])
@login_required
def api_ft_undo(lid):
    """Undo a mistap: only the latest step of its week, and its meal too."""
    r = db().execute("SELECT * FROM ft_log WHERE id=?", (lid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="Already gone."), 404
    if r["kind"] in FT_STEP_KINDS:
        last = db().execute("SELECT MAX(step) AS m FROM ft_log WHERE slug=? AND kind IN "
                            "('dose','washout','dinner','decline')", (r["slug"],)).fetchone()["m"]
        if r["step"] != last:
            return jsonify(ok=False, err="Only the latest step of a week can be undone."), 409
    if r["meal_id"]:
        db().execute("DELETE FROM meal_meta WHERE meal_id=?", (r["meal_id"],))
        db().execute("DELETE FROM meals WHERE id=?", (r["meal_id"],))
    db().execute("DELETE FROM ft_log WHERE id=?", (lid,))
    db().commit()
    return jsonify(ok=True)


def ft_flags(a, b, dose_days):
    """What makes a result less trustworthy. Stated, never interpreted."""
    flags = []
    if a and b:
        n = db().execute(
            "SELECT COUNT(*) FROM med_schedule WHERE (valid_from BETWEEN ? AND ?) "
            "OR (COALESCE(valid_to,'')<>'' AND valid_to BETWEEN ? AND ?)",
            (a, b, a, b)).fetchone()[0]
        n += db().execute(
            "SELECT COUNT(*) FROM courses WHERE (start_day BETWEEN ? AND ?) "
            "OR (COALESCE(end_day,'')<>'' AND end_day BETWEEN ? AND ?)",
            (a, b, a, b)).fetchone()[0]
        if n:
            flags.append("medicine change during this week — result may be unreliable.")
    ds = sorted(set(dose_days))
    gap = 0
    for x, y in zip(ds, ds[1:]):
        gap = max(gap, (date.fromisoformat(y) - date.fromisoformat(x)).days - 1)
    if gap > 1:
        flags.append("a gap of %d days inside this ladder — result may be unreliable." % gap)
    return flags


def ft_results():
    outs = ft_outcomes()
    scores = {}
    for r in db().execute("SELECT * FROM ft_score ORDER BY day, id"):
        scores[r["day"]] = dict(r)
    blocks = []
    for it in ft_items():
        rows = ft_rows(it["slug"])
        b = {"slug": it["slug"], "week": it.get("week"), "label": it.get("label"),
             "kind": it.get("kind") or "ladder", "unit": ft_unit(it), "meal": it.get("meal") or "",
             "cooking": it.get("cooking") or "", "notes": it.get("notes") or [],
             "optional": bool(it.get("optional")), "outcome": outs.get(it["slug"]),
             "steps": [s for s in ft_steps(it)], "rows": [], "flags": []}
        if it.get("kind") == "dinner":
            b["rows"] = [dict(r, score=scores.get(r["day"])) for r in rows if r["kind"] == "dinner"]
            blocks.append(b)
            continue
        doses = [r for r in rows if r["kind"] == "dose"]
        wash = [r for r in rows if r["kind"] == "washout"]
        b["rows"] = [dict(r, score=scores.get(r["day"]), role="challenge") for r in doses]
        first_wash = None
        if wash:
            first_wash = wash[0]["day"]
        elif doses:
            first_wash = (date.fromisoformat(doses[-1]["day"]) + timedelta(days=1)).isoformat()
        if first_wash and doses:
            b["rows"].append({"day": first_wash, "role": "washout", "score": scores.get(first_wash),
                              "id": wash[0]["id"] if wash else None,
                              "ltime": wash[0]["ltime"] if wash else ""})
        b["stopped"] = [r for r in rows if r["kind"] == "stop"]
        b["gaps"] = [r for r in rows if r["kind"] in ("skip", "pause")]
        if doses:
            b["flags"] = ft_flags(doses[0]["day"], first_wash or doses[-1]["day"],
                                  [r["day"] for r in doses])
        blocks.append(b)
    return blocks


def ft_seed(con, cfg):
    """Load his plan file into ft_items and ft_cfg. Replaces the plan's
    definition only -- never a logged step, a score or an outcome -- and adds
    each test food to the library from the table when it is not there yet.
    Returns what it did."""
    now = now_s()
    n = 0
    for pos, it in enumerate(cfg.get("items") or []):
        slug = (it.get("slug") or "").strip()
        if not slug:
            continue
        data = dict(it)
        data.pop("slug", None)
        con.execute("INSERT OR REPLACE INTO ft_items(slug, pos, data, seeded) VALUES(?,?,?,?)",
                    (slug, pos, json.dumps(data), now))
        n += 1
    keep = dict((k, cfg.get(k)) for k in ("plan_id", "reminder", "safe_plate", "safe_notes", "rules"))
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('ft_cfg', ?)", (json.dumps(keep),))
    added = []
    doc, byid = food_table()
    for it in cfg.get("items") or []:
        lb = it.get("library") or {}
        name = (lb.get("name") or "").strip()
        if not name or con.execute("SELECT 1 FROM library WHERE item=?", (name,)).fetchone():
            continue
        t = byid.get(int(lb.get("fdc") or 0))
        if not t:
            continue
        steps = it.get("steps") or [{}]
        f, err = lib_apply({"portion_qty": steps[0].get("g"), "portion_unit": "g",
                            "weighed_dry": (it.get("basis") or "dry") == "dry",
                            "fdc": t[0], "b_protein": t[2], "b_kcal": t[3], "b_fibre": t[4]}, None)
        if err:
            continue
        keys = sorted(f)
        con.execute("INSERT INTO library(cat, item, portion, fodmap, status, fav, tags, note, created, "
                    + ", ".join(keys) + ") VALUES(?,?,?,?,?,?,?,?,?" + ",?" * len(keys) + ")",
                    [(lb.get("cat") or "B")[:1], name, "", "H", "test", 0, "food test",
                     "Food test food; values from the table.", now] + [f[k] for k in keys])
        added.append(name)
    con.commit()
    return {"items": n, "library_added": added}


def _ft_hm_selects(cls, value):
    h = ['<option value="">--</option>'] + ['<option value="%02d"%s>%02d</option>'
         % (i, ' selected' if value[:2] == "%02d" % i else "", i) for i in range(24)]
    m = ['<option value="">--</option>'] + ['<option value="%02d"%s>%02d</option>'
         % (i, ' selected' if value[3:5] == "%02d" % i else "", i) for i in range(60)]
    return ('<span class="tp"><select class="%s-h" aria-label="Hour">%s</select>:'
            '<select class="%s-m" aria-label="Minute">%s</select></span>'
            % (cls, "".join(h), cls, "".join(m)))


def _ft_day_select(value):
    opts = []
    for i in range(0, 21):
        d = (date.today() - timedelta(days=i)).isoformat()
        opts.append('<option value="%s"%s>%s</option>'
                    % (d, ' selected' if d == value else "", plan_esc(plan_dmy(d))))
    if value and value not in [(date.today() - timedelta(days=i)).isoformat() for i in range(21)]:
        opts.append('<option value="%s" selected>%s</option>' % (plan_esc(value), plan_esc(plan_dmy(value))))
    return '<select class="ed-d" aria-label="Day">' + "".join(opts) + "</select>"


def _ft_score_text(s):
    if not s:
        return "no score"
    return "pain %s · bloating %s · urgency %s · Bristol %s%s" % (
        s["pain"], "yes" if s["bloating"] else "no", "yes" if s["urgency"] else "no",
        s["bristol"], " · clear symptoms" if s["clear"] else "")


def _ft_edit(table, rid, day, t):
    return ('<details class="ed"><summary>change</summary><div class="edb" data-t="%s" data-i="%s">'
            '%s %s <button type="button" class="edgo">Save</button></div></details>'
            % (table, rid, _ft_day_select(day), _ft_hm_selects("ed", t or "")))


def ft_page_html():
    cfg = ft_cfg()
    blocks = ft_results()
    st = ft_state()
    out = []
    if not blocks:
        out.append('<p class="lead">No food test plan is loaded yet.</p>')
    if st.get("active") and cfg.get("reminder"):
        out.append('<p class="remind">' + plan_esc(cfg["reminder"]) + '</p>')
    if cfg.get("plan_id"):
        out.append('<p class="lead"><a href="/plan/%d/file">Open the plan (PDF)</a></p>' % int(cfg["plan_id"]))
    labels = dict(FT_OUTCOMES)
    for b in blocks:
        out.append('<section class="blk" id="ft-%s">' % plan_esc(b["slug"]))
        head = "%s — %s" % (b["week"], b["label"])
        if b["kind"] != "dinner":
            head += " (%s%s)" % (b["unit"], (", " + b["meal"].lower()) if b["meal"] else "")
        out.append('<h2>' + plan_esc(head) + (' <i class="opt">optional</i>' if b["optional"] else "")
                   + '</h2>')
        if b["kind"] != "dinner":
            o = b["outcome"]
            ot = "not set yet"
            if o:
                ot = labels.get(o["outcome"], o["outcome"])
                if o["outcome"] == "limit":
                    ot = "Limit at %s g" % _nut_num(o["limit_g"])
            out.append('<p class="oc">Outcome: <b class="ocv">' + plan_esc(ot) + '</b></p>')
            out.append('<div class="ocf" data-s="%s"><select class="oc-s" aria-label="Outcome">%s</select>'
                       '<input class="oc-g" type="number" min="0" step="1" placeholder="g" '
                       'aria-label="Limit in grams"><button type="button" class="ocgo">Set</button></div>'
                       % (plan_esc(b["slug"]), "".join('<option value="%s">%s</option>' % (k, plan_esc(l))
                                                        for k, l in FT_OUTCOMES)))
            for f in b["flags"]:
                out.append('<p class="flag">⚠ ' + plan_esc(f) + '</p>')
        if not b["rows"]:
            out.append('<p class="none">Nothing logged yet.</p>')
        for r in b["rows"]:
            if b["kind"] == "dinner":
                what = "dinner %s · %s · cramp %s" % (
                    r.get("ltime") or "", r.get("size") or "size not noted",
                    "yes" if r.get("cramp") else "no")
            elif r.get("role") == "washout":
                what = "first washout day"
            else:
                what = "%s %s %s" % (r.get("ltime") or "", _nut_num(r["amount"]), r["unit"])
            out.append('<div class="row"><span class="d">%s</span> <span class="w">%s</span>'
                       '<span class="s">%s</span>' % (plan_esc(plan_dmy(r["day"])), plan_esc(what),
                                                      plan_esc(_ft_score_text(r.get("score")))))
            if r.get("id"):
                out.append(_ft_edit("log", r["id"], r["day"], r.get("ltime") or ""))
            if r.get("score"):
                out.append('<div class="sced">score at %s %s</div>'
                           % (plan_esc(r["score"]["stime"] or ""),
                              _ft_edit("score", r["score"]["id"], r["day"], r["score"]["stime"] or "")))
            out.append('</div>')
        for g in b.get("gaps") or []:
            out.append('<p class="gap">%s: %s (%s)</p>' % (plan_esc(plan_dmy(g["day"])),
                       "skipped" if g["kind"] == "skip" else "paused", plan_esc(g["reason"] or "")))
        for s in b.get("stopped") or []:
            out.append('<p class="gap">%s: ladder stopped, limit %s %s</p>'
                       % (plan_esc(plan_dmy(s["day"])), _nut_num(s["amount"]), plan_esc(s["unit"] or "")))
        if b["cooking"]:
            out.append('<details><summary>How to cook</summary><p>' + plan_esc(b["cooking"]) + '</p></details>')
        for n in b["notes"]:
            out.append('<p class="note">' + plan_esc(n) + '</p>')
        out.append('</section>')
    if cfg.get("safe_plate"):
        out.append('<details class="safe"><summary>The safe plate</summary>')
        for row in cfg["safe_plate"]:
            out.append('<p><b>%s:</b> %s</p>' % (plan_esc(row[0]), plan_esc(row[1])))
        for n in cfg.get("safe_notes") or []:
            out.append('<p class="note">' + plan_esc(n) + '</p>')
        out.append('</details>')
    for n in cfg.get("rules") or []:
        out.append('<p class="note">' + plan_esc(n) + '</p>')
    return "".join(out)


@app.route("/foodtest")
@login_required
def foodtest_page():
    return Response(FOODTEST_PAGE.replace("__BODY__", ft_page_html()), mimetype="text/html")


FOODTEST_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Food test - GutLog</title>
<style>
:root{--bg:#F4F6F5;--card:#fff;--ink:#17201D;--muted:#55645E;--line:#DCE4E0;
      --teal:#2E7D6B;--teal-d:#1F5F51;--chip:#EDF3F0;--amber:#8A5A00}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;--line:#2C3733;
  --teal:#5FB49C;--teal-d:#8ED3BD;--chip:#243029;--amber:#E8B86A}}
:root[data-theme="dark"]{--bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;
  --line:#2C3733;--teal:#5FB49C;--teal-d:#8ED3BD;--chip:#243029;--amber:#E8B86A}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;flex-wrap:wrap;
       background:var(--card);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;font-weight:700;flex:1;white-space:nowrap}
header a,a{color:var(--teal-d);font-weight:600}
header a{text-decoration:none;font-size:13px}
main{padding:14px;max-width:760px;margin:0 auto}
.lead{color:var(--muted);font-size:13px;margin:0 0 10px}
.remind{background:var(--chip);border-radius:10px;padding:8px 11px;font-size:13.5px;font-weight:600;margin:0 0 10px}
.blk{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 13px;margin:0 0 10px}
.blk h2{font-size:15px;margin:0 0 6px}
.opt{font-style:normal;font-size:11px;color:var(--amber);font-weight:700}
.oc{margin:0 0 4px;font-size:13.5px}
.ocf{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 6px}
.ocf select,.ocf input,.ed select{font:inherit;padding:5px;border-radius:8px;border:1px solid var(--line);
  background:var(--card);color:var(--ink);min-width:0}
.ocf input{width:64px}
button{font:inherit;border:1px solid var(--teal);background:var(--teal);color:#fff;border-radius:8px;
  padding:5px 11px;font-weight:700;cursor:pointer}
.flag{color:var(--amber);font-weight:700;font-size:13.5px;margin:4px 0}
.row{border-top:1px solid var(--line);padding:7px 0;font-size:13.5px}
.row .d{font-weight:700}.row .s{display:block;color:var(--muted)}
.none,.gap,.note{color:var(--muted);font-size:13px;margin:4px 0}
details{margin:4px 0;font-size:13px}summary{cursor:pointer;color:var(--teal-d);font-weight:600}
.edb{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:5px}
.tp{display:inline-flex;gap:3px;align-items:center}
.sced{color:var(--muted);font-size:12.5px}
.safe{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 12px}
</style></head><body>
<header><h1>Food test</h1><a href="/?open=now">Now</a><a href="/">GutLog</a></header>
<main>
__BODY__
</main>
<script>
function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b)}).then(r=>r.json().then(j=>{if(!r.ok||j.ok===false)throw new Error(j.err||'Failed');return j;}));}
document.querySelectorAll('.edgo').forEach(b=>b.onclick=()=>{
  const w=b.parentNode,h=w.querySelector('.ed-h').value,m=w.querySelector('.ed-m').value;
  if(!h||!m){alert('Pick a time');return;}
  post('/api/ft/retime',{table:w.dataset.t,id:+w.dataset.i,day:w.querySelector('.ed-d').value,time:h+':'+m})
    .then(()=>location.reload()).catch(e=>alert(e.message));});
document.querySelectorAll('.ocgo').forEach(b=>b.onclick=()=>{
  const w=b.parentNode;
  post('/api/ft/outcome',{slug:w.dataset.s,outcome:w.querySelector('.oc-s').value,
    limit_g:w.querySelector('.oc-g').value}).then(()=>location.reload()).catch(e=>alert(e.message));});
</script>
</body></html>
"""


'''
CODE_OLD = '# ------------------------------------------------------------------ diet plan\n'
CODE_NEW = CODE + CODE_OLD

# ================================================================== the page
HTML_OLD = '  <div id="nowMirror"></div>\n'
HTML_NEW = '  <div id="nowMirror"></div>\n  <div id="nowFT"></div>\n'

LOAD_OLD = 'async function loadNow(){\n  loadMirror();\n'
LOAD_NEW = 'async function loadNow(){\n  loadMirror();\n  loadFT();\n'

CSS_OLD = '.mdt>div{flex:1 1 130px;min-width:0}\n'
CSS_NEW = ('.mdt>div{flex:1 1 130px;min-width:0}\n'
           '/* GUTLOG_V3320_FOODTEST -- the food test card. Sized for 300px. */\n'
           '.fthd{display:flex;align-items:center;gap:8px}.fthd .q{flex:1;margin:0}\n'
           '.fthist{font-size:12.5px;font-weight:700;padding:5px 10px;border:1px solid var(--teal);\n'
           '  border-radius:9px;color:var(--teal);text-decoration:none}\n'
           '.ftrem{font-size:13px;font-weight:600;background:var(--chip);border-radius:10px;padding:7px 10px;margin:8px 0}\n'
           '.ftstep{font-size:15px;font-weight:700;margin:8px 0 4px}\n'
           '.ftsub{font-size:12.5px;color:var(--muted);margin:0 0 6px}\n'
           '.ftrow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}\n'
           '.ftrow input[type=number]{width:84px;flex:0 0 84px;padding:8px}\n'
           '.ftrow select{width:auto;flex:1 1 120px;min-width:0;padding:8px}\n'
           '.ftrow .lb{font-size:13px;font-weight:700;color:var(--muted)}\n'
           '.ftdone{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:14px;margin:8px 0 2px}\n'
           '.ftnext{font-size:13px;color:var(--muted);margin:4px 0}\n'
           '.ftsc{border-top:1px solid var(--line);margin-top:10px;padding-top:8px}\n'
           '.ftsc .chips{gap:6px}.ftsc .chip{padding:7px 11px;font-size:14px}\n'
           '.ftask{border:1.5px dashed var(--teal);border-radius:12px;padding:9px 11px;margin:8px 0}\n'
           '.ftask p{margin:0 0 7px;font-size:14px;font-weight:600}\n'
           '.ftcard details{font-size:13px;margin:6px 0}\n'
           '.ftcard summary{cursor:pointer;color:var(--teal);font-weight:600}\n')

JS = r'''/* GUTLOG_V3320_FOODTEST -- the food test on the Now tab. One tap for the
   step, one short evening score; the server works out everything else --
   which step is next, what a stop means, what the results show. */
let FT=null,ftScoreOpen=false,ftSc={},ftStopHidden=false;
function ftDays(){const o=[];for(let i=0;i<7;i++){const d=new Date();d.setDate(d.getDate()-i);
  const s=d.getFullYear()+'-'+tpPad(d.getMonth()+1)+'-'+tpPad(d.getDate());
  o.push([s,i===0?'Today':(i===1?'Yesterday':mlDmyText(s))]);}return o;}
function ftSel(id,opts,cur,label){const s=document.createElement('select');s.id=id;
  s.setAttribute('aria-label',label);opts.forEach(o=>s.add(new Option(o[1],o[0])));if(cur!=null)s.value=cur;return s;}
function ftChips(key,opts){const w=el('div','chips');w.dataset.k=key;
  opts.forEach(o=>{const b=el('button','chip'+(ftSc[key]===o[0]?' sel':''),o[1]);b.type='button';b.dataset.v=o[0];
    b.onclick=()=>{ftSc[key]=o[0];w.querySelectorAll('.chip').forEach(x=>x.classList.toggle('sel',x===b));};
    w.appendChild(b);});return w;}
function ftOpen(rowEl,o){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick tedit';
  box.innerHTML='<p class="vt"></p><div class="vtm"><span class="lb">Time</span><input type="time" class="tt"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button><button type="button" class="go">Save time</button></div>';
  box.querySelector('.vt').textContent=o.title+' · '+(o.day===todayISO?'today':mlDmyText(o.day));
  const tt=box.querySelector('.tt');tt.value=o.time||'';
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{if(!tt.value){toast('Pick a time');return;}
    try{const r=await post('/api/ft/retime',{table:o.table,id:o.id,day:o.day,time:tt.value});
      toast(r.unchanged?'No change':('Time set to '+tt.value));box.remove();loadFT();loadMeals();}
    catch(err){toast(err.message);}};
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  return box;
}
function ftTimeBtn(o,rowFn){const b=el('button','chip tbtn',o.time||'--:--');b.type='button';
  b.setAttribute('aria-label','Time '+(o.time||'not set')+', tap to change');
  b.onclick=ev=>{ev.stopPropagation();ftOpen(rowFn(),o);};return b;}
function ftStepForm(cu){
  const w=el('div','ftform');
  w.appendChild(el('p','ftstep',cu.text));
  if(cu.kind==='dose'){
    const sub=[cu.cooked?('cooked, roughly '+cu.cooked):'',cu.meal?('at '+cu.meal.toLowerCase()):'',cu.cond].filter(Boolean).join(' · ');
    if(sub)w.appendChild(el('p','ftsub',sub));
  }else if(cu.kind==='washout'){
    w.appendChild(el('p','ftsub','Just the evening score today — saving it counts this washout day.'));
    return w;
  }
  const ar=el('div','ftrow');
  if(cu.kind==='dose'){
    const a=document.createElement('input');a.type='number';a.id='ftAmt';a.min='0';a.step='1';a.inputMode='decimal';
    a.value=lfFmt(cu.g);a.setAttribute('aria-label','Amount');ar.appendChild(a);ar.appendChild(el('b','',cu.unit));
  }else{
    ar.appendChild(el('span','lb','Size'));ar.appendChild(ftChips('size',FT.sizes.map(s=>[s,s])));
    ar.appendChild(el('span','lb','Cramp'));ar.appendChild(ftChips('cramp',[[0,'No'],[1,'Yes']]));
  }
  w.appendChild(ar);
  const tr=el('div','ftrow');tr.appendChild(ftSel('ftDay',ftDays(),todayISO,'Day'));
  const ti=document.createElement('input');ti.type='time';ti.id='ftTime';ti.value=nowHM();tr.appendChild(ti);
  w.appendChild(tr);
  const go=el('button','btn primary',cu.kind==='dose'?'Taken':'Save dinner');go.type='button';go.id='ftTaken';
  go.onclick=async()=>{if(go.dataset.busy)return;go.dataset.busy=1;
    const body={slug:cu.slug,step:cu.step,day:$('#ftDay').value,time:$('#ftTime').value||nowHM()};
    if(cu.kind==='dose')body.amount=$('#ftAmt').value;else{body.size=ftSc.size||'';body.cramp=ftSc.cramp===1;}
    try{await post('/api/ft/log',body);toast('Logged');ftSc={};loadFT();loadMeals();}
    catch(err){go.dataset.busy='';toast(err.message);}};
  w.appendChild(go);
  if(cu.kind==='dose'&&cu.cond){const d=el('button','btn ghost','Not clean — go to washout');d.type='button';d.id='ftDecline';
    d.onclick=async()=>{try{await post('/api/ft/log',{slug:cu.slug,step:cu.step,decline:true,day:$('#ftDay').value,time:$('#ftTime').value||nowHM()});loadFT();}catch(err){toast(err.message);}};
    w.appendChild(d);}
  if(cu.optional&&!cu.started){const s=el('button','btn ghost','Not doing this week');s.type='button';s.id='ftSkipWeek';
    s.onclick=async()=>{try{await post('/api/ft/outcome',{slug:cu.slug,outcome:'not_tested'});loadFT();}catch(err){toast(err.message);}};
    w.appendChild(s);}
  return w;
}
function ftGapRow(){
  const r=el('div','ftrow');r.appendChild(ftSel('ftWhy',FT.reasons.map(x=>[x,x]),null,'Reason'));
  const sk=el('button','btn ghost','Skip today');sk.type='button';sk.id='ftSkip';
  sk.onclick=async()=>{try{await post('/api/ft/skip',{reason:$('#ftWhy').value});toast('Skipped — the plan waits');loadFT();}catch(e){toast(e.message);}};
  const pa=el('button','btn ghost','Pause');pa.type='button';pa.id='ftPause';
  pa.onclick=async()=>{try{await post('/api/ft/pause',{reason:$('#ftWhy').value});toast('Paused');loadFT();}catch(e){toast(e.message);}};
  r.appendChild(sk);r.appendChild(pa);return r;
}
function ftScoreBlock(j){
  const w=el('div','ftsc');w.id='ftScore';
  const s=j.score_today;
  if(s&&!ftScoreOpen){
    const line=el('div','ftdone');
    line.appendChild(el('span','','Evening score: pain '+s.pain+' · bloating '+(s.bloating?'yes':'no')+
      ' · urgency '+(s.urgency?'yes':'no')+' · Bristol '+s.bristol+(s.clear?' · clear symptoms':'')+' at'));
    line.appendChild(ftTimeBtn({table:'score',id:s.id,day:s.day,time:s.stime,title:'Evening score'},()=>line));
    const ed=el('button','chip','Edit');ed.type='button';ed.id='ftScoreEdit';
    ed.onclick=()=>{ftScoreOpen=true;ftSc={pain:s.pain,bloating:s.bloating,urgency:s.urgency,bristol:s.bristol,clear:s.clear,stime:s.stime};loadFT();};
    line.appendChild(ed);w.appendChild(line);return w;
  }
  if(!ftScoreOpen&&nowHM()<'17:00'){const b=el('button','btn ghost','Evening score');b.type='button';b.id='ftScoreBtn';
    b.onclick=()=>{ftScoreOpen=true;loadFT();};w.appendChild(b);return w;}
  w.appendChild(el('p','q','Evening score'));
  w.appendChild(el('p','lbl','Pain'));w.appendChild(ftChips('pain',[0,1,2,3,4,5,6,7,8,9,10].map(i=>[i,String(i)])));
  w.appendChild(el('p','lbl','Bloating'));w.appendChild(ftChips('bloating',[[0,'No'],[1,'Yes']]));
  w.appendChild(el('p','lbl','Urgency'));w.appendChild(ftChips('urgency',[[0,'No'],[1,'Yes']]));
  w.appendChild(el('p','lbl','Stool (Bristol)'));w.appendChild(ftChips('bristol',[1,2,3,4,5,6,7].map(i=>[i,String(i)])));
  w.appendChild(el('p','lbl','Clear symptoms today?'));w.appendChild(ftChips('clear',[[0,'No'],[1,'Yes']]));
  const tr=el('div','ftrow');tr.appendChild(el('span','lb','Time'));
  const ti=document.createElement('input');ti.type='time';ti.id='ftScTime';ti.value=ftSc.stime||nowHM();tr.appendChild(ti);w.appendChild(tr);
  const go=el('button','btn primary','Save score');go.type='button';go.id='ftScoreSave';
  go.onclick=async()=>{
    try{const r=await post('/api/ft/score',{day:todayISO,time:$('#ftScTime').value||nowHM(),pain:ftSc.pain,
        bloating:ftSc.bloating,urgency:ftSc.urgency,bristol:ftSc.bristol,clear:ftSc.clear===1});
      toast(r.washout?'Score saved · washout day counted':'Score saved');ftScoreOpen=false;ftSc={};ftStopHidden=false;loadFT();}
    catch(err){toast(err.message);}};
  w.appendChild(go);return w;
}
function ftStopRow(o){
  const w=el('div','ftask');w.id='ftStopAsk';
  w.appendChild(el('p','','Stop this ladder and record the limit at '+lfFmt(o.limit_g)+' '+o.unit+'?'));
  const y=el('button','btn primary','Stop and record the limit');y.type='button';y.id='ftStop';
  y.onclick=async()=>{try{await post('/api/ft/stop',{slug:o.slug,day:todayISO});toast(o.label+': limit at '+lfFmt(o.limit_g)+' g · washout next');loadFT();}catch(e){toast(e.message);}};
  const n=el('button','btn ghost','Not now');n.type='button';n.onclick=()=>{ftStopHidden=true;w.remove();};
  w.appendChild(y);w.appendChild(n);return w;
}
function ftOutcomeRow(n){
  const w=el('div','ftask');w.dataset.slug=n.slug;
  w.appendChild(el('p','',n.week+' — '+n.label+' is done. Your outcome?'));
  const ch=el('div','chips');
  FT.outcomes.forEach(o=>{const lab=o.key==='limit'?('Limit at '+(n.last_g!=null?lfFmt(n.last_g):'N')+' g'):o.label;
    const b=el('button','chip',lab);b.type='button';b.dataset.o=o.key;
    b.onclick=async()=>{try{await post('/api/ft/outcome',{slug:n.slug,outcome:o.key,limit_g:n.last_g});toast('Recorded — change it any time on Results');loadFT();}catch(e){toast(e.message);}};
    ch.appendChild(b);});
  w.appendChild(ch);return w;
}
async function loadFT(){
  const box=$('#nowFT');if(!box)return;
  let j;try{j=await jget('/api/ft');}catch(e){return;}
  FT=j;box.innerHTML='';
  if(!j.seeded)return;
  const c=el('div','card ftcard');c.id='ftCard';box.appendChild(c);
  const hd=el('div','fthd');hd.appendChild(el('p','q','Food test'));
  const a=el('a','fthist','Results');a.href='/foodtest';hd.appendChild(a);c.appendChild(hd);
  if(j.active&&j.reminder)c.appendChild(el('p','ftrem',j.reminder));
  (j.need_outcome||[]).forEach(n=>c.appendChild(ftOutcomeRow(n)));
  if(j.paused){
    c.appendChild(el('p','ftstep','Paused since '+mlDmyText(j.paused.since)+' ('+j.paused.reason+'). The plan waits where it was.'));
    const r=el('button','btn primary','Resume');r.type='button';r.id='ftResume';
    r.onclick=async()=>{try{await post('/api/ft/pause',{resume:true});toast('Resumed');loadFT();}catch(e){toast(e.message);}};
    c.appendChild(r);
  }else if(!j.active){
    c.appendChild(el('p','ftstep','Every planned week is done — see Results.'));
  }else{
    const cu=j.current;
    if(j.today_step){
      const t=el('div','ftdone');
      t.appendChild(el('span','','✓ Today: '+(j.today_step.text||'logged')+' at'));
      t.appendChild(ftTimeBtn({table:'log',id:j.today_step.id,day:j.today_step.day,time:j.today_step.ltime,title:j.today_step.text||'Step'},()=>t));
      const u=el('button','chip','Undo');u.type='button';u.id='ftUndo';
      u.onclick=async()=>{try{await post('/api/ft/undo/'+j.today_step.id,{});toast('Undone');loadFT();loadMeals();}catch(e){toast(e.message);}};
      t.appendChild(u);c.appendChild(t);
      c.appendChild(el('p','ftnext','Next: '+cu.text));
    }else if(j.skipped_today){
      c.appendChild(el('p','ftstep','Skipped today.'));c.appendChild(el('p','ftnext','Next is still: '+cu.text));
    }else if(cu.wait){
      c.appendChild(el('p','ftstep',cu.week+' — '+cu.label+': '+cu.wait+'.'));
    }else{
      c.appendChild(ftStepForm(cu));
    }
    if(!j.today_step&&!j.skipped_today)c.appendChild(ftGapRow());
    if(cu.cooking){const d=document.createElement('details');d.appendChild(el('summary','','How to cook'));
      d.appendChild(el('p','ftsub',cu.cooking));c.appendChild(d);}
    (cu.notes||[]).forEach(n=>c.appendChild(el('p','ftsub',n)));
  }
  c.appendChild(ftScoreBlock(j));
  if(j.offer_stop&&!ftStopHidden)c.appendChild(ftStopRow(j.offer_stop));
}

'''
BOOT_OLD = '/* ---------- boot ---------- */\n'
BOOT_NEW = JS + BOOT_OLD

EDITS = [
    ("header", HEAD_OLD, HEAD_NEW),
    ("version", VER_OLD, VER_NEW),
    ("tables", SCH_OLD, SCH_NEW),
    ("food test code", CODE_OLD, CODE_NEW),
    ("now card slot", HTML_OLD, HTML_NEW),
    ("now loads it", LOAD_OLD, LOAD_NEW),
    ("css", CSS_OLD, CSS_NEW),
    ("card code", BOOT_OLD, BOOT_NEW),
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
    print("GutLog food test -> v" + VERSION)
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
    bak = a.file + ".bak-v3320-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3320_foodtest.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

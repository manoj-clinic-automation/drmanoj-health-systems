#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.39.0 -> v3.40.0  ::  GUTLOG_V3400_WEIGHT -- the `weight` profile.

WHY: a third family profile beside gut and joint. The person it is for starts
on a Sunday with a once-a-week injection, a weigh-in, a diet with its own meal
times, and a set of check-ins her doctor asked for. Every feature is here for
every copy (the owner's Now page is unchanged unless settings.now_profile says
`weight`); the profile only decides what the Now page shows first.

WHAT
  * WEEKLY slot in med_schedule (weekday + time): shown on the Now page only
    on that day, a countdown on the other days (This-week card), a reminder
    banner from 30 min before, Taken / Skipped like any dose; a dose not
    logged by the next day is asked about ("yesterday's weekly dose -- taken
    late / skipped?"). Stock counts it per dose (never as a daily pillbox).
    Variants, Retime, Day by day and the feed's shape are unchanged.
  * Meal windows from the person's setup (settings.meal_windows): the slot
    guess uses them instead of the fixed clock; a member whose breakfast is
    at 11:30 is never filed as Mid-morning.
  * Check-ins (table checkins; schedules in settings.checkins): PHQ-9, PHQ-2,
    Epworth, a side-effect check the day after a weekly dose, a weekly
    weigh-in, monthly measurements. Shown only when due, gone when done,
    never nagging; totals and bands computed here; PHQ-9 item 9 > 0 sets a
    same-day flag the caretaker's Family page shows, and the member is asked
    to tell the caretaker today. Nothing else is automated.
  * Weight and waist chart (7-90 days) with milestone lines from
    settings.weight_plan (start weight, milestone percentages); a milestone
    counts as crossed after two consecutive weigh-ins at or below it, and
    the next one becomes current. /checkins page; /export/checkins.csv.
  * This-week card (profile weight, first on the page): weight and waist,
    injection countdown, milestone line, steps today from FitLog (blank when
    there are none -- never "0"), protein today against the target.
  * The joint cards show, folded, under `weight` too.

Schema 3.3.9 (two guarded columns on med_schedule, one new table).
No medicine, person or figure is named here. Anchor-verified, idempotent,
compile-checked, .bak, self-restoring, --reverse, refuses Jinja tokens.
Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3400_WEIGHT"
PREV = "GUTLOG_V3390_KITCHENBY"
VERSION = "3.40.0"

# ----------------------------------------------------------------- python
PY_BLOCK = r'''# ------------------------------------------------------------ weight profile
# GUTLOG_V3400_WEIGHT. A third family profile beside gut and joint: a weekly
# medicine slot, meal windows from the person's own setup, check-ins on a
# schedule, a weight and waist chart with milestones, and a This-week card.
# Every feature is here for every copy; the profile only decides what the Now
# page shows first. No medicine, person or figure is named in this file: the
# plan, the windows and the schedules are settings written by the person's own
# setup (a family seed, or the caretaker).
CARETAKER_NAME = os.environ.get("GUTLOG_CARETAKER_NAME", "").strip()[:40]
CHECKIN_OPTS4 = ["Not at all", "Several days", "More than half the days", "Nearly every day"]
PHQ9_ITEMS = [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself - or that you are a failure or have let yourself or your family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed - or the opposite, being so "
    "fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead, or of hurting yourself in some way",
]
PHQ_DIFFICULTY = ["Not difficult at all", "Somewhat difficult", "Very difficult", "Extremely difficult"]
EPWORTH_ITEMS = [
    "Sitting and reading", "Watching TV",
    "Sitting inactive in a public place (a theatre or a meeting)",
    "As a passenger in a car for an hour without a break",
    "Lying down to rest in the afternoon when circumstances permit",
    "Sitting and talking to someone", "Sitting quietly after a lunch without alcohol",
    "In a car, while stopped for a few minutes in traffic",
]
EPWORTH_OPTS = ["Would never doze", "Slight chance of dozing", "Moderate chance of dozing", "High chance of dozing"]
SIDE_EFFECT_ITEMS = ["Nausea", "Vomiting", "Diarrhoea", "Constipation", "Loss of appetite"]
SIDE_EFFECT_OPTS = ["None", "Mild", "Moderate", "Severe"]
CHECKIN_KINDS = {
    "phq9": ("Mood check (PHQ-9)", "Over the last 2 weeks, how often have you been bothered by any of "
                                   "the following problems?"),
    "phq2": ("Mood check (PHQ-2)", "Over the last 2 weeks, how often have you been bothered by the following?"),
    "epworth": ("Sleepiness check (Epworth)", "How likely are you to doze off or fall asleep in these "
                                             "situations, in contrast to feeling just tired? Think of your "
                                             "usual way of life in recent times."),
    "side_effects": ("Side-effect check", "Since yesterday's weekly dose:"),
    "weigh_in": ("Weekly weigh-in", "Same scale, same time, before breakfast."),
    "measurements": ("Monthly measurements", "Tape measure, relaxed, in centimetres."),
}
CHECKIN_ORDER = ["weigh_in", "side_effects", "phq2", "phq9", "epworth", "measurements"]


def checkin_form(kind):
    """What the page renders for one check-in: items with 4 tap-buttons,
    numbers, 0-10 scales, a free note. Standard wording, plain English."""
    if kind not in CHECKIN_KINDS:
        return None
    title, lead = CHECKIN_KINDS[kind]
    f = {"kind": kind, "title": title, "lead": lead, "items": [], "numbers": [], "scales": [], "note": False}
    if kind in ("phq9", "phq2"):
        n = 9 if kind == "phq9" else 2
        f["items"] = [{"key": "q%d" % (i + 1), "text": t, "opts": CHECKIN_OPTS4} for i, t in enumerate(PHQ9_ITEMS[:n])]
        if kind == "phq9":
            f["items"].append({"key": "difficulty", "opts": PHQ_DIFFICULTY, "text":
                               "If you ticked any problems, how difficult have they made it for you to do "
                               "your work, take care of things at home, or get along with other people?"})
    elif kind == "epworth":
        f["items"] = [{"key": "e%d" % (i + 1), "text": t, "opts": EPWORTH_OPTS} for i, t in enumerate(EPWORTH_ITEMS)]
    elif kind == "side_effects":
        f["items"] = [{"key": t.lower().replace(" ", "_"), "text": t, "opts": SIDE_EFFECT_OPTS} for t in SIDE_EFFECT_ITEMS]
        f["scales"] = [{"key": "energy", "label": "Energy today (0 = none, 10 = full)", "min": 0, "max": 10}]
        f["note"] = True
    elif kind == "weigh_in":
        f["numbers"] = [{"key": "weight", "label": "Weight", "unit": "kg", "min": 20, "max": 300, "optional": False},
                        {"key": "waist", "label": "Waist", "unit": "cm", "min": 40, "max": 200, "optional": True}]
    elif kind == "measurements":
        f["numbers"] = [{"key": "waist", "label": "Waist", "unit": "cm", "min": 40, "max": 200, "optional": False},
                        {"key": "hips", "label": "Hips", "unit": "cm", "min": 40, "max": 220, "optional": False},
                        {"key": "neck", "label": "Neck", "unit": "cm", "min": 20, "max": 70, "optional": False}]
    return f


def _phq_band(total, n):
    if n == 2:
        return "positive screen - do the PHQ-9" if total >= 3 else "negative screen"
    for lim, b in ((4, "minimal"), (9, "mild"), (14, "moderate"), (19, "moderately severe")):
        if total <= lim:
            return b
    return "severe"


def _epworth_band(total):
    for lim, b in ((10, "normal"), (12, "mild sleepiness"), (15, "moderate sleepiness")):
        if total <= lim:
            return b
    return "severe sleepiness"


def checkin_score(kind, answers):
    """(total, band, clean answers, flag, error). Deterministic; the rule that
    fired is the band's own name. flag = PHQ-9 item 9 above 0."""
    f = checkin_form(kind)
    if not f:
        return None, "", {}, False, "Unknown check-in."
    a = answers if isinstance(answers, dict) else {}
    clean, total, flag = {}, 0, False
    for it in f["items"]:
        v = a.get(it["key"])
        try:
            v = int(v)
        except (TypeError, ValueError):
            v = None
        if v is None or not 0 <= v < len(it["opts"]):
            if it["key"] == "difficulty":
                continue
            return None, "", {}, False, "Please answer every question."
        clean[it["key"]] = v
        if it["key"] != "difficulty":
            total += v
    for nm in f["numbers"]:
        v = a.get(nm["key"])
        if v in (None, ""):
            if nm.get("optional"):
                continue
            return None, "", {}, False, "Please fill in %s." % nm["label"].lower()
        try:
            v = round(float(v), 1)
        except (TypeError, ValueError):
            return None, "", {}, False, "%s must be a number." % nm["label"]
        if not nm["min"] <= v <= nm["max"]:
            return None, "", {}, False, "%s: that does not look right." % nm["label"]
        clean[nm["key"]] = v
    for sc in f["scales"]:
        v = a.get(sc["key"])
        try:
            v = int(v)
        except (TypeError, ValueError):
            return None, "", {}, False, "Please pick a number for %s." % sc["label"].split(" (")[0].lower()
        if not sc["min"] <= v <= sc["max"]:
            return None, "", {}, False, "Out of range."
        clean[sc["key"]] = v
    band = ""
    if kind == "phq9":
        band = _phq_band(total, 9)
        flag = clean.get("q9", 0) > 0
    elif kind == "phq2":
        band = _phq_band(total, 2)
    elif kind == "epworth":
        band = _epworth_band(total)
    elif kind == "side_effects":
        worst = max(clean.get(k, 0) for k in [i["key"] for i in f["items"]])
        band = SIDE_EFFECT_OPTS[worst].lower() if worst else "none"
        total = None
    else:
        total = None
    return total, band, clean, flag, ""


def checkin_schedule():
    try:
        v = json.loads(setting("checkins") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _checkin_period_start(spec, tday, hm):
    """The day this check-in became due (ISO), or '' when it is not due by its
    rule. monthly:N | weekly:DDD[ HH:MM] | day_after_weekly_dose."""
    spec = (spec or "").strip()
    d = date.fromisoformat(tday)
    if spec.startswith("monthly:"):
        try:
            n = max(1, min(28, int(spec.split(":", 1)[1])))
        except ValueError:
            return ""
        if d.day >= n:
            return d.replace(day=n).isoformat()
        return (d.replace(day=1) - timedelta(days=1)).replace(day=n).isoformat()
    if spec.startswith("weekly:"):
        rest = spec.split(":", 1)[1].strip().split()
        wd = (rest[0] if rest else "").upper()[:3]
        at = _valid_hm(rest[1]) if len(rest) > 1 else ""
        if wd not in WEEKDAYS:
            return ""
        back = (d.weekday() - WEEKDAYS.index(wd)) % 7
        start = d - timedelta(days=back)
        if back == 0 and at and hm < at:
            start = d - timedelta(days=7)
        return start.isoformat()
    if spec == "day_after_weekly_dose":
        r = db().execute("SELECT MAX(d.day) AS day FROM doses d JOIN med_schedule s ON s.id=d.sched_id "
                         "WHERE s.slot=? AND d.status='TAKEN' AND d.day<?", (WEEKLY_SLOT, tday)).fetchone()
        if not r or not r["day"]:
            return ""
        dd = date.fromisoformat(r["day"])
        if 1 <= (d - dd).days <= 2:
            return (dd + timedelta(days=1)).isoformat()
        return ""
    return ""


def checkins_due(tday=None, hm=None):
    """Every scheduled check-in that is due and not yet answered in its
    period -- and nothing else. Answered = gone; not due = never shown."""
    tday = tday or today()
    hm = hm or now_hm()
    sched = checkin_schedule()
    out = []
    for kind in CHECKIN_ORDER:
        spec = sched.get(kind)
        if not spec:
            continue
        start = _checkin_period_start(spec, tday, hm)
        if not start or start > tday:
            continue
        done = db().execute("SELECT 1 FROM checkins WHERE kind=? AND day>=? AND day<=?",
                            (kind, start, tday)).fetchone()
        if done:
            continue
        f = checkin_form(kind)
        f["since"] = start
        out.append(f)
    return out


def checkin_flag():
    """{day, kind} when a PHQ-9 answered TODAY had item 9 above 0; else None."""
    try:
        v = json.loads(setting("checkin_flag") or "null")
    except ValueError:
        v = None
    return v if isinstance(v, dict) and v.get("day") == today() else None


@app.route("/api/checkins/due")
@login_required
def api_checkins_due():
    return jsonify(ok=True, due=checkins_due(), flag=checkin_flag(), caretaker=CARETAKER_NAME or "your caretaker",
                   scheduled=[k for k in CHECKIN_ORDER if checkin_schedule().get(k)],
                   kinds=[{"kind": k, "title": CHECKIN_KINDS[k][0]} for k in CHECKIN_ORDER])


@app.route("/api/checkins/form")
@login_required
def api_checkins_form():
    f = checkin_form(request.args.get("kind") or "")
    if not f:
        return jsonify(ok=False, err="Unknown check-in."), 404
    return jsonify(ok=True, form=f)


@app.route("/api/checkins", methods=["GET", "POST"])
@login_required
def api_checkins():
    if request.method == "GET":
        try:
            days = max(1, min(400, int(request.args.get("days") or 90)))
        except ValueError:
            days = 90
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        kind = request.args.get("kind") or ""
        q = "SELECT id, kind, day, ctime, answers, total, band, note FROM checkins WHERE day>=?"
        args = [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        rows = []
        for r in db().execute(q + " ORDER BY day DESC, id DESC", args).fetchall():
            x = dict(r)
            try:
                x["answers"] = json.loads(x["answers"] or "{}")
            except ValueError:
                x["answers"] = {}
            x["title"] = CHECKIN_KINDS.get(x["kind"], (x["kind"], ""))[0]
            rows.append(x)
        return jsonify(ok=True, rows=rows, kinds=dict((k, v[0]) for k, v in CHECKIN_KINDS.items()))
    d = J()
    kind = (d.get("kind") or "").strip()
    total, band, clean, flag, err = checkin_score(kind, d.get("answers"))
    if err:
        return jsonify(ok=False, err=err), 400
    day = _valid_day(d.get("day")) or today()
    ctime = _valid_hm(d.get("time")) or now_hm()
    nt = note(d, "note", 300)
    db().execute("INSERT INTO checkins(kind, day, ctime, answers, total, band, note, created) "
                 "VALUES(?,?,?,?,?,?,?,?)", (kind, day, ctime, json.dumps(clean), total, band, nt, now_s()))
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    if kind == "weigh_in":
        insert("vitals", ["day", "vtime", "weight", "waist", "notes"],
               [day, ctime, clean.get("weight"), clean.get("waist"), "weigh-in"])
    elif kind == "measurements":
        insert("vitals", ["day", "vtime", "waist", "notes"], [day, ctime, clean.get("waist"), "measurements"])
    if flag:
        set_setting("checkin_flag", json.dumps({"day": day, "kind": kind, "at": ctime}))
    db().commit()
    return jsonify(ok=True, id=rid, total=total, band=band, tell_caretaker=bool(flag),
                   caretaker=CARETAKER_NAME or "your caretaker")


@app.route("/api/checkins/<int:cid>/delete", methods=["POST"])
@login_required
def api_checkins_delete(cid):
    cur = db().execute("DELETE FROM checkins WHERE id=?", (cid,))
    db().commit()
    return jsonify(ok=bool(cur.rowcount))


def meal_windows():
    """{slot: [start, end]} from settings.meal_windows, only the slots the
    page knows and only well-formed times; {} when the person has none."""
    try:
        v = json.loads(setting("meal_windows") or "{}")
    except ValueError:
        return {}
    out = {}
    for slot, w in (v.items() if isinstance(v, dict) else []):
        if slot in MEAL_SLOTS and isinstance(w, (list, tuple)) and len(w) == 2:
            a, b = _valid_hm(w[0]), _valid_hm(w[1])
            if a and b and a < b:
                out[slot] = [a, b]
    return out


def _slot_by_windows(hm, win):
    """The slot whose window holds hm; in a gap, the nearer window's slot;
    before the first window the first slot, after the last the last."""
    order = [s for s in MEAL_SLOTS if s in win]
    for s in order:
        if win[s][0] <= hm < win[s][1]:
            return s
    m = lambda t: int(t[:2]) * 60 + int(t[3:])
    x = m(hm)
    best, dist = order[-1], None
    for s in order:
        a, b = m(win[s][0]), m(win[s][1])
        dd = (a - x) if x < a else (x - b)
        if dist is None or dd < dist:
            best, dist = s, dd
    return best


@app.route("/api/meal_windows", methods=["GET", "POST"])
@login_required
def api_meal_windows():
    if request.method == "POST":
        d = J()
        w = d.get("windows") if isinstance(d.get("windows"), dict) else {}
        set_setting("meal_windows", json.dumps(w))
        return jsonify(ok=True, windows=meal_windows())
    return jsonify(ok=True, windows=meal_windows(), slots=MEAL_SLOTS)


def weight_plan():
    try:
        v = json.loads(setting("weight_plan") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


@app.route("/api/weight_plan", methods=["GET", "POST"])
@login_required
def api_weight_plan():
    if request.method == "POST":
        d = J()
        p = weight_plan()
        for k in ("start_weight", "start_waist"):
            if d.get(k) not in (None, ""):
                try:
                    p[k] = float(d[k])
                except (TypeError, ValueError):
                    return jsonify(ok=False, err="%s must be a number." % k), 400
        if d.get("start_day"):
            p["start_day"] = _valid_day(d["start_day"]) or p.get("start_day") or today()
        if isinstance(d.get("milestones_pct"), list):
            p["milestones_pct"] = sorted(set(float(x) for x in d["milestones_pct"] if 0 < float(x) <= 50))
        if isinstance(d.get("targets"), dict):
            p["targets"] = d["targets"]
        set_setting("weight_plan", json.dumps(p))
        return jsonify(ok=True, plan=p)
    return jsonify(ok=True, plan=weight_plan())


def weight_series(days):
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    w = [[r["day"], r["weight"]] for r in db().execute(
        "SELECT day, weight FROM vitals WHERE weight IS NOT NULL AND day>=? ORDER BY day, vtime, id", (since,))]
    ws = [[r["day"], r["waist"]] for r in db().execute(
        "SELECT day, waist FROM vitals WHERE waist IS NOT NULL AND day>=? ORDER BY day, vtime, id", (since,))]
    return w, ws


def weight_milestones():
    """[{pct, kg, reached, current}] from the plan's start weight. Crossed =
    two consecutive weigh-ins at or below the line (any window); the first
    not crossed is current. Nothing here guesses."""
    p = weight_plan()
    try:
        start = float(p.get("start_weight") or 0)
    except (TypeError, ValueError):
        start = 0.0
    pcts = p.get("milestones_pct") or []
    if not start or not pcts:
        return []
    allw = [float(r["weight"]) for r in db().execute(
        "SELECT weight FROM vitals WHERE weight IS NOT NULL AND day>=? ORDER BY day, vtime, id",
        (p.get("start_day") or "0000",))]
    out, cur_set = [], False
    for pct in sorted(float(x) for x in pcts):
        kg = round(start * (1 - pct / 100.0), 1)
        reached = any(allw[i] <= kg and allw[i + 1] <= kg for i in range(len(allw) - 1))
        m = {"pct": pct, "kg": kg, "reached": reached, "current": False}
        if not reached and not cur_set:
            m["current"] = True
            cur_set = True
        out.append(m)
    return out


@app.route("/api/weight")
@login_required
def api_weight():
    try:
        days = max(7, min(90, int(request.args.get("days") or 28)))
    except ValueError:
        days = 28
    w, ws = weight_series(days)
    return jsonify(ok=True, days=days, weight=w, waist=ws, milestones=weight_milestones(), plan=weight_plan())


def weekly_next(day):
    """The next date of every open weekly medicine, from `day`: name, weekday,
    time, days to go, and whether that date's dose is already logged."""
    d = date.fromisoformat(day)
    out = []
    for s in db().execute("SELECT s.id AS sched_id, s.med_id, s.weekday, s.at_time, s.dose_text, s.variants, "
                          "p.name FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id WHERE s.slot=? "
                          "AND s.valid_from<=? AND (s.valid_to='' OR s.valid_to IS NULL OR s.valid_to>=?) "
                          "ORDER BY p.sort, p.id", (WEEKLY_SLOT, day, day)).fetchall():
        if s["weekday"] not in WEEKDAYS:
            continue
        ahead = (WEEKDAYS.index(s["weekday"]) - d.weekday()) % 7
        nxt = d + timedelta(days=ahead)
        logged = db().execute("SELECT status FROM doses WHERE sched_id=? AND day=?",
                              (s["sched_id"], nxt.isoformat())).fetchone()
        if ahead == 0 and logged and logged["status"] in ("TAKEN", "SKIPPED"):
            done_today = True
        else:
            done_today = False
        out.append({"sched_id": s["sched_id"], "med_id": s["med_id"], "name": s["name"], "weekday": s["weekday"],
                    "weekday_name": WEEKDAY_NAMES.get(s["weekday"], s["weekday"]), "time": s["at_time"] or "",
                    "day": nxt.isoformat(), "days_to": ahead, "due_today": ahead == 0 and not done_today,
                    "done_today": done_today, "dose_text": s["dose_text"] or "", "variants": s["variants"] or ""})
    out.sort(key=lambda x: (x["days_to"], x["time"]))
    return out


def weekly_late_rows(day):
    """Yesterday's weekly doses with nothing logged for them yesterday or
    today -- asked about once, on the day after, never nagged."""
    if day != today():
        return []
    y = date.fromisoformat(day) - timedelta(days=1)
    yd = y.isoformat()
    out = []
    for s in db().execute("SELECT s.id AS sched_id, s.med_id, s.dose_text, s.variants, s.at_time, p.name "
                          "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id WHERE s.slot=? AND s.weekday=? "
                          "AND s.valid_from<=? AND (s.valid_to='' OR s.valid_to IS NULL OR s.valid_to>=?)",
                          (WEEKLY_SLOT, WEEKDAYS[y.weekday()], yd, yd)).fetchall():
        if db().execute("SELECT 1 FROM doses WHERE sched_id=? AND day IN (?,?)",
                        (s["sched_id"], yd, day)).fetchone():
            continue
        out.append(dict(s, due_day=yd))
    return out


def _fit_steps_today():
    """Steps today from FitLog's feed, or None when there are none. Never 0
    for 'no data': a blank is honest, a zero is a claim."""
    feed = _link_get(FITLOG_URL + "/api/feed/watch?days=1", ttl=120)
    if not feed or not feed.get("ok"):
        return None
    for dd in feed.get("daily") or []:
        if dd.get("date") == today():
            v = ((dd.get("metrics") or {}).get("steps") or {}).get("value")
            if v is not None:
                try:
                    return int(round(float(v)))
                except (TypeError, ValueError):
                    return None
    return None


@app.route("/api/week")
@login_required
def api_week():
    """The This-week card: shown under the weight profile only."""
    on = now_profile() == "weight"
    tday = today()
    last = db().execute("SELECT day, weight FROM vitals WHERE weight IS NOT NULL ORDER BY day DESC, vtime DESC, "
                        "id DESC LIMIT 1").fetchone()
    lastw = db().execute("SELECT day, waist FROM vitals WHERE waist IS NOT NULL ORDER BY day DESC, vtime DESC, "
                         "id DESC LIMIT 1").fetchone()
    ms = weight_milestones()
    cur = next((m for m in ms if m["current"]), None)
    milestone = None
    if cur and last:
        to_go = round(float(last["weight"]) - cur["kg"], 1)
        milestone = {"pct": cur["pct"], "kg": cur["kg"], "to_go": to_go,
                     "text": "-%g %% = %g kg, %g kg to go" % (cur["pct"], cur["kg"], max(to_go, 0))}
    elif ms and last:
        milestone = {"pct": ms[-1]["pct"], "kg": ms[-1]["kg"], "to_go": 0, "text": "every milestone reached"}
    prot = db().execute("SELECT COALESCE(SUM(protein),0) AS p FROM meals WHERE day=?", (tday,)).fetchone()
    nxt = weekly_next(tday)
    due = [f["kind"] for f in checkins_due()]
    return jsonify(on=on, profile=now_profile(), today=tday,
                   weight={"kg": last["weight"], "day": last["day"],
                           "waist": lastw["waist"] if lastw else None, "waist_day": lastw["day"] if lastw else None}
                   if last else None,
                   milestone=milestone, milestones=ms, injection=nxt[0] if nxt else None, injections=nxt,
                   steps=_fit_steps_today(),
                   protein={"today": round(float(prot["p"] or 0), 1), "target": _protein_target()},
                   weigh_in_due="weigh_in" in due, checkins_due=due)


@app.route("/checkins")
@login_required
def checkins_page():
    return Response(CHECKINS_PAGE.replace("__CARETAKER__", json.dumps(CARETAKER_NAME or "your caretaker")),
                    mimetype="text/html")


CHECKINS_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Check-ins</title>
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--bad:#a4262c;--ms:#8a4b00}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--bad:#ff8a80;--ms:#f0b35a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:36rem;margin:0 auto;padding:12px 14px 40px;overflow-wrap:anywhere}h1{font-size:21px;margin:4px 0 10px}h2{font-size:18px;margin:0 0 6px}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:7px 12px;font:inherit;font-size:15px;cursor:pointer}
.chip.sel{background:var(--acc);color:#fff;border-color:var(--acc)}.chip.num{min-width:40px;text-align:center}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}
.mut{color:var(--mut);font-size:15px}.lbl{font-weight:600;margin:10px 0 4px;font-size:15px}.flag{color:var(--bad);font-weight:600}
input,textarea{width:100%;font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
button.btn{font:inherit;font-weight:600;padding:10px 14px;border-radius:10px;border:0;background:var(--acc);color:#fff;margin:8px 6px 0 0}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc)}
svg{width:100%;height:auto;display:block}.row{display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-top:1px solid var(--line)}.row:first-child{border-top:0}
a{color:var(--acc)}
</style></head><body><main>
<p><a href="/">&larr; Back</a></p><h1>Check-ins</h1>
<div id="due"></div>
<div class="card"><h2>Weight and waist</h2><div class="chips" id="spans"></div><div id="chart" style="margin-top:8px"></div><div id="ms" class="mut"></div></div>
<div class="card"><h2>Do one now</h2><div class="chips" id="kinds"></div><div id="form"></div></div>
<div class="card"><h2>Trends</h2><div id="hist"></div></div>
<div id="toast" class="mut" style="position:fixed;bottom:12px;left:12px;right:12px;text-align:center"></div>
</main>
<script>
/* GUTLOG_V3400_WEIGHT -- the Check-ins page: chart with milestone lines, the
   forms, the history. */
const CARETAKER=__CARETAKER__;
const $=q=>document.querySelector(q);
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;}
function toast(m){const t=$('#toast');t.textContent=m;setTimeout(()=>{t.textContent='';},2400);}
async function jget(u){const r=await fetch(u,{credentials:'same-origin'});return r.json();}
async function post(u,b){const r=await fetch(u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const j=await r.json().catch(()=>({}));if(!r.ok){throw new Error(j.err||'Save failed');}return j;}
let SPAN=28,ANS={},OPEN=null;
function svgEl(t,a){const e=document.createElementNS('http://www.w3.org/2000/svg',t);Object.keys(a||{}).forEach(k=>e.setAttribute(k,a[k]));return e;}
function chart(j){
  const box=$('#chart');box.innerHTML='';
  const w=j.weight||[],ws=j.waist||[],ms=j.milestones||[];
  if(!w.length&&!ws.length){box.appendChild(el('p','mut','No weigh-in in this window yet.'));return;}
  const W=320,H=180,L=34,R=34,T=10,B=24;
  const days=[];const d0=new Date();d0.setDate(d0.getDate()-(j.days-1));
  for(let i=0;i<j.days;i++){const d=new Date(d0);d.setDate(d0.getDate()+i);days.push(d.toLocaleDateString('en-CA'));}
  const x=d=>L+(W-L-R)*Math.max(0,days.indexOf(d))/Math.max(1,j.days-1);
  const wv=w.map(p=>p[1]).concat(ms.map(m=>m.kg));let lo=Math.min.apply(null,wv),hi=Math.max.apply(null,wv);if(hi-lo<2){lo-=1;hi+=1;}lo=Math.floor(lo-0.5);hi=Math.ceil(hi+0.5);
  const y=v=>T+(H-T-B)*(hi-v)/(hi-lo);
  const s=svgEl('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'Weight and waist'});
  [lo,hi].forEach(v=>{const t=svgEl('text',{x:2,y:y(v)+5,'font-size':'11',fill:'currentColor'});t.textContent=v+' kg';s.appendChild(t);});
  ms.forEach(m=>{if(m.kg<lo||m.kg>hi)return;s.appendChild(svgEl('line',{x1:L,x2:W-R,y1:y(m.kg),y2:y(m.kg),stroke:'var(--ms)','stroke-dasharray':m.reached?'2 3':'6 4','stroke-width':m.current?'2':'1'}));
    const t=svgEl('text',{x:W-R+2,y:y(m.kg)+4,'font-size':'10',fill:'var(--ms)'});t.textContent='-'+m.pct+'%';s.appendChild(t);});
  if(w.length){s.appendChild(svgEl('polyline',{points:w.map(p=>x(p[0])+','+y(p[1])).join(' '),fill:'none',stroke:'var(--acc)','stroke-width':'2'}));
    w.forEach(p=>s.appendChild(svgEl('circle',{cx:x(p[0]),cy:y(p[1]),r:'3',fill:'var(--acc)'})));}
  if(ws.length){const wl=Math.min.apply(null,ws.map(p=>p[1]))-2,wh=Math.max.apply(null,ws.map(p=>p[1]))+2;const y2=v=>T+(H-T-B)*(wh-v)/Math.max(1,wh-wl);
    s.appendChild(svgEl('polyline',{points:ws.map(p=>x(p[0])+','+y2(p[1])).join(' '),fill:'none',stroke:'var(--mut)','stroke-width':'1.5','stroke-dasharray':'4 3'}));
    [wl,wh].forEach(v=>{const t=svgEl('text',{x:W-R+2,y:y2(v)+4,'font-size':'10',fill:'var(--mut)'});t.textContent=v+' cm';s.appendChild(t);});}
  [days[0],days[days.length-1]].forEach((d,i)=>{const t=svgEl('text',{x:i?W-R:L,y:H-6,'font-size':'11',fill:'currentColor','text-anchor':i?'end':'start'});t.textContent=d.slice(5);s.appendChild(t);});
  box.appendChild(s);
  const legend=el('p','mut','Solid: weight (kg). Dashed grey: waist (cm, right scale). Orange lines: milestones' + (ms.length?'':' (none set)') + '.');box.appendChild(legend);
  const m=$('#ms');m.innerHTML='';ms.forEach(x=>m.appendChild(el('p','', '-'+x.pct+'% = '+x.kg+' kg'+(x.reached?' - reached':(x.current?' - current':'')))));
}
async function loadChart(){const j=await jget('/api/weight?days='+SPAN);const sp=$('#spans');sp.innerHTML='';
  [7,14,28,60,90].forEach(n=>{const c=el('button','chip'+(SPAN===n?' sel':''),n+' days');c.onclick=()=>{SPAN=n;loadChart();};sp.appendChild(c);});chart(j);}
function form(f,done){
  const w=el('div');w.appendChild(el('p','lbl',f.title));if(f.lead)w.appendChild(el('p','mut',f.lead));
  (f.items||[]).forEach(it=>{w.appendChild(el('p','lbl',it.text));const ch=el('div','chips');
    it.opts.forEach((o,i)=>{const t=el('button','chip'+(ANS[it.key]===i?' sel':''),o);t.onclick=()=>{ANS[it.key]=i;[...ch.children].forEach((x,k)=>x.classList.toggle('sel',k===i));};ch.appendChild(t);});w.appendChild(ch);});
  (f.numbers||[]).forEach(n=>{w.appendChild(el('p','lbl',n.label+' ('+n.unit+')'+(n.optional?' - optional':'')));const inp=el('input');inp.type='number';inp.inputMode='decimal';inp.step='0.1';inp.setAttribute('aria-label',n.label);inp.oninput=()=>{ANS[n.key]=inp.value;};w.appendChild(inp);});
  (f.scales||[]).forEach(sc=>{w.appendChild(el('p','lbl',sc.label));const ch=el('div','chips');for(let i=sc.min;i<=sc.max;i++){const t=el('button','chip num'+(ANS[sc.key]===i?' sel':''),String(i));t.onclick=()=>{ANS[sc.key]=i;[...ch.children].forEach(x=>x.classList.toggle('sel',x===t));};ch.appendChild(t);}w.appendChild(ch);});
  if(f.note){w.appendChild(el('p','lbl','Anything else'));const ta=el('textarea');ta.rows=2;ta.oninput=()=>{ANS._note=ta.value;};w.appendChild(ta);}
  const sv=el('button','btn','Save');sv.onclick=async()=>{try{const r=await post('/api/checkins',{kind:f.kind,answers:ANS,note:ANS._note||''});toast(r.band?(f.title+': '+r.band):'Saved');done(r);}catch(e){toast(e.message);}};
  w.appendChild(sv);const cn=el('button','btn ghost','Not now');cn.onclick=()=>done(null);w.appendChild(cn);return w;
}
async function loadDue(){
  const j=await jget('/api/checkins/due');const b=$('#due');b.innerHTML='';
  if(j.flag)b.appendChild(el('p','flag','Please tell '+CARETAKER+' today.'));
  (j.due||[]).forEach(f=>{const c=el('div','card');
    if(OPEN===f.kind){c.appendChild(form(f,()=>{OPEN=null;ANS={};loadAll();}));}
    else{c.appendChild(el('b','',f.title+' - due'));const bt=el('button','btn','Answer');bt.onclick=()=>{OPEN=f.kind;ANS={};loadAll();};c.appendChild(bt);}
    b.appendChild(c);});
  const k=$('#kinds');k.innerHTML='';(j.kinds||[]).forEach(x=>{const c=el('button','chip',x.title);c.onclick=async()=>{const r=await jget('/api/checkins/form?kind='+x.kind);ANS={};$('#form').innerHTML='';$('#form').appendChild(form(r.form,()=>{$('#form').innerHTML='';ANS={};loadAll();}));};k.appendChild(c);});
}
async function loadHist(){
  const j=await jget('/api/checkins?days=180');const b=$('#hist');b.innerHTML='';
  if(!(j.rows||[]).length){b.appendChild(el('p','mut','Nothing answered yet.'));return;}
  const by={};(j.rows||[]).forEach(r=>{(by[r.kind]=by[r.kind]||[]).push(r);});
  Object.keys(by).forEach(k=>{b.appendChild(el('p','lbl',j.kinds[k]||k));by[k].slice(0,12).forEach(r=>{const row=el('div','row');row.appendChild(el('span','',r.day+' '+(r.ctime||'')));
    let v='';if(r.total!=null)v=r.total+(r.band?' - '+r.band:'');else if(k==='weigh_in')v=r.answers.weight+' kg'+(r.answers.waist?' - waist '+r.answers.waist+' cm':'');else if(k==='measurements')v='waist '+r.answers.waist+' - hips '+r.answers.hips+' - neck '+r.answers.neck;else v=r.band||'';
    row.appendChild(el('span','mut',v));b.appendChild(row);});});
}
function loadAll(){loadDue();loadChart();loadHist();}
loadAll();
</script></body></html>"""


'''

# ----------------------------------------------------------------- html
HTML_WEEK = '''  <!-- GUTLOG_V3400_WEIGHT -- the This-week card, first under the weight profile. -->
  <div class="card" id="nowWeek" style="display:none">
    <p class="q">This week</p>
    <div id="wkBody"></div>
  </div>
'''

HTML_CHECKINS = '''  <!-- GUTLOG_V3400_WEIGHT -- check-ins due today; hidden when nothing is due. -->
  <div class="card" id="nowCheckins" style="display:none">
    <p class="q">Check-ins</p>
    <div id="ckBody"></div>
  </div>
'''

HTML_WEEKLY_FORM = '''      <div class="row2" id="sc_weekly" style="display:none;margin-top:10px">
        <div><p class="lbl">Day of the week</p><select id="sc_wday">
          <option value="MON">Monday</option><option value="TUE">Tuesday</option><option value="WED">Wednesday</option>
          <option value="THU">Thursday</option><option value="FRI">Friday</option><option value="SAT">Saturday</option>
          <option value="SUN">Sunday</option></select></div>
        <div><p class="lbl">Time</p><input type="time" id="sc_wtime"></div>
      </div>
'''

# ----------------------------------------------------------------- js
JS_BLOCK = r'''/* GUTLOG_V3400_WEIGHT -- the This-week card, the check-ins card, the weekly
   dose prompts, and folding the joint cards under the weight profile. */
async function loadWeek(){
  const c=$('#nowWeek');if(!c)return;
  let j;try{j=await jget('/api/week');}catch(e){return;}
  if(!j.on){c.style.display='none';return;}
  c.style.display='';const b=$('#wkBody');b.innerHTML='';
  const line=(l,v)=>{const p=el('p','wkl');p.appendChild(el('span','wkk',l));p.appendChild(el('span','wkv',v));b.appendChild(p);};
  line('Weight',j.weight&&j.weight.kg!=null?(j.weight.kg+' kg · '+j.weight.day.slice(5)+(j.weigh_in_due?' · weigh-in due':'')):(j.weigh_in_due?'weigh-in due':'no weigh-in yet'));
  line('Waist',j.weight&&j.weight.waist!=null?(j.weight.waist+' cm'):'—');
  if(j.injection){const i=j.injection;
    line('Injection',i.done_today?('taken today · '+i.name):(i.due_today?('today at '+i.time+' · '+i.name):(i.days_to+' day'+(i.days_to===1?'':'s')+' to go · '+i.weekday_name+' '+i.time)));}
  if(j.milestone)line('Milestone',j.milestone.text);
  line('Steps today',j.steps==null?'':(j.steps.toLocaleString()+' steps'));
  line('Protein',Math.round(j.protein.today)+' of '+j.protein.target+' g');
  const a=el('a','','Check-ins and chart');a.href='/checkins';const p=el('p','hint');p.style.margin='6px 2px 0';p.appendChild(a);b.appendChild(p);
}
let CK={open:null,ans:{}};
async function loadCheckins(){
  const c=$('#nowCheckins');if(!c)return;
  let j;try{j=await jget('/api/checkins/due');}catch(e){return;}
  const b=$('#ckBody');b.innerHTML='';
  if(!(j.due||[]).length&&!j.flag){c.style.display='none';return;}
  c.style.display='';
  if(j.flag)b.appendChild(el('p','ckflag','Please tell '+(j.caretaker||'your caretaker')+' today.'));
  (j.due||[]).forEach(f=>{
    if(CK.open===f.kind){b.appendChild(ckForm(f,()=>{CK.open=null;CK.ans={};loadCheckins();loadWeek();}));return;}
    const row=el('div','ckrow');row.appendChild(el('b','',f.title));
    const bt=el('button','btn tiny','Answer');bt.type='button';bt.onclick=()=>{CK.open=f.kind;CK.ans={};loadCheckins();};
    row.appendChild(bt);b.appendChild(row);});
}
function ckForm(f,done){
  const w=el('div','ckform');w.appendChild(el('p','lbl',f.title));if(f.lead)w.appendChild(el('p','hint',f.lead));
  (f.items||[]).forEach(it=>{w.appendChild(el('p','lbl',it.text));const ch=el('div','chips');
    it.opts.forEach((o,i)=>{const t=el('button','chip'+(CK.ans[it.key]===i?' sel':''),o);t.type='button';
      t.onclick=()=>{CK.ans[it.key]=i;[...ch.children].forEach((x,k)=>x.classList.toggle('sel',k===i));};ch.appendChild(t);});
    w.appendChild(ch);});
  (f.numbers||[]).forEach(n=>{w.appendChild(el('p','lbl',n.label+' ('+n.unit+')'+(n.optional?' — optional':'')));
    const inp=document.createElement('input');inp.type='number';inp.inputMode='decimal';inp.step='0.1';inp.setAttribute('aria-label',n.label);
    inp.value=CK.ans[n.key]==null?'':CK.ans[n.key];inp.oninput=()=>{CK.ans[n.key]=inp.value;};w.appendChild(inp);});
  (f.scales||[]).forEach(s=>{w.appendChild(el('p','lbl',s.label));const ch=el('div','chips');
    for(let i=s.min;i<=s.max;i++){const t=el('button','chip num'+(CK.ans[s.key]===i?' sel':''),String(i));t.type='button';
      t.onclick=()=>{CK.ans[s.key]=i;[...ch.children].forEach(x=>x.classList.toggle('sel',x===t));};ch.appendChild(t);}
    w.appendChild(ch);});
  if(f.note){w.appendChild(el('p','lbl','Anything else'));const ta=document.createElement('textarea');ta.rows=2;ta.maxLength=300;ta.oninput=()=>{CK.ans._note=ta.value;};w.appendChild(ta);}
  const sv=el('button','btn primary','Save');sv.type='button';sv.style.marginTop='10px';
  sv.onclick=async()=>{try{const r=await post('/api/checkins',{kind:f.kind,answers:CK.ans,note:CK.ans._note||''});
      toast(r.band?(f.title+': '+r.band):'Saved');done(r);}catch(e){toast(e.message);}};
  w.appendChild(sv);
  const cn=el('button','btn ghost','Not now');cn.type='button';cn.style.marginTop='10px';cn.onclick=()=>done(null);w.appendChild(cn);
  return w;
}
/* Yesterday's weekly dose with nothing logged: one question, on the day after. */
function weeklyLateRow(w){
  const d=document.createElement('div');d.className='doserow late';
  d.innerHTML='<div class="tick">?</div><div class="nm"><b></b><span></span></div>';
  d.querySelector('.nm b').textContent=w.name;
  d.querySelector('.nm span').textContent='yesterday’s weekly dose — taken late / skipped?';
  const row=el('div','btnrow');
  const a=el('button','btn tiny','Taken late');a.type='button';
  a.onclick=async e=>{e.stopPropagation();
    if(w.variants){openVariantPicker(d,{med_id:w.med_id,sched_id:w.sched_id,variants:w.variants,name:w.name});return;}
    try{await post('/api/now/dose',{med_id:w.med_id,sched_id:w.sched_id,status:'TAKEN',day:todayISO,dose_text:w.dose_text,notes:'late: due '+w.due_day});
      toast('Logged '+w.name);loadNow();}catch(err){toast(err.message);}};
  const s=el('button','btn tiny ghost','Skipped');s.type='button';
  s.onclick=async e=>{e.stopPropagation();
    try{await post('/api/now/dose',{med_id:w.med_id,sched_id:w.sched_id,status:'SKIPPED',day:w.due_day,dose_text:w.dose_text});
      toast('Marked skipped');loadNow();}catch(err){toast(err.message);}};
  row.appendChild(a);row.appendChild(s);d.appendChild(row);
  return d;
}
/* A reminder from 30 minutes before a weekly dose that is not yet logged. */
function weeklyBanner(box,data){
  const now=nowHM();
  (data.slots||[]).filter(s=>s.slot==='WEEKLY').forEach(s=>{
    s.rows.filter(r=>!r.status&&r.at_time).forEach(r=>{
      const m=t=>parseInt(t.slice(0,2),10)*60+parseInt(t.slice(3,5),10);
      if(m(now)<m(r.at_time)-30)return;
      const p=el('p','wkban','Weekly dose '+(m(now)>m(r.at_time)?'was due':'due')+' at '+r.at_time+' · '+r.name);
      box.insertBefore(p,box.firstChild);});});
}
/* Under the weight profile the joint cards are there, folded. */
function foldify(id){
  const card=document.getElementById(id);if(!card||card.dataset.folded)return;card.dataset.folded='1';
  const title=card.querySelector('.q');const t=title?title.textContent:'';if(title)title.remove();
  const body=el('div','cbody');[...card.childNodes].forEach(k=>body.appendChild(k));
  const h=document.createElement('button');h.type='button';h.className='fold-h';
  h.innerHTML='<span class="ft"></span><span class="fs"></span><span class="fc"></span>';h.querySelector('.ft').textContent=t;
  card.appendChild(h);card.appendChild(body);card.classList.add('fold');
  h.onclick=()=>{card.classList.toggle('open');};
}
'''

CSS_BLOCK = ("#nowWeek .wkl{display:flex;justify-content:space-between;gap:10px;margin:4px 0;font-size:16px}"
             "#nowWeek .wkk{color:var(--muted)}#nowWeek .wkv{font-weight:600;text-align:right}\n"
             ".ckrow{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:6px 0}"
             ".ckflag{color:var(--err);font-weight:700;margin:0 0 8px}.ckform{margin-top:6px}\n"
             ".wkban{margin:0 0 8px;padding:8px 10px;border-radius:9px;background:var(--amberbg,rgba(138,90,0,.12));"
             "color:var(--amber);font-weight:600;font-size:15px}.doserow.late .tick{color:var(--amber)}\n")

E = []
E.append(("header",
          'GUTLOG_V3390_KITCHENBY -- "Recipe by <Name>" on every card, list and logged meal; By person; '
          'the owner can hide; kitchen members on the Family page.\n',
          'GUTLOG_V3390_KITCHENBY -- "Recipe by <Name>" on every card, list and logged meal; By person; '
          'the owner can hide; kitchen members on the Family page.\n'
          'GUTLOG_V3400_WEIGHT -- the weight profile: a WEEKLY medicine slot, meal windows, check-ins, '
          'a weight chart with milestones, a This-week card.\n'))
E.append(("version", 'APP_VERSION = "3.39.0"   # GUTLOG_V3390_KITCHENBY ',
          'APP_VERSION = "3.40.0"   # GUTLOG_V3400_WEIGHT GUTLOG_V3390_KITCHENBY '))
E.append(("schema med_schedule",
          "  valid_to TEXT DEFAULT '', epoch INTEGER DEFAULT 1, notes TEXT DEFAULT '', created TEXT,\n"
          "  variants TEXT DEFAULT '');\n",
          "  valid_to TEXT DEFAULT '', epoch INTEGER DEFAULT 1, notes TEXT DEFAULT '', created TEXT,\n"
          "  variants TEXT DEFAULT '', weekday TEXT DEFAULT '', at_time TEXT DEFAULT '');\n"))
E.append(("schema checkins",
          "CREATE TABLE IF NOT EXISTS med_salts (\n"
          "  med_id INTEGER PRIMARY KEY, strength TEXT DEFAULT '', no_salt INTEGER DEFAULT 0, updated TEXT);\n",
          "CREATE TABLE IF NOT EXISTS med_salts (\n"
          "  med_id INTEGER PRIMARY KEY, strength TEXT DEFAULT '', no_salt INTEGER DEFAULT 0, updated TEXT);\n"
          "-- GUTLOG_V3400_WEIGHT. One row per answered check-in: the kind, the IST day and time,\n"
          "-- the answers as given, the total and band the rule computed.\n"
          "CREATE TABLE IF NOT EXISTS checkins (\n"
          "  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, day TEXT NOT NULL, ctime TEXT DEFAULT '',\n"
          "  answers TEXT DEFAULT '{}', total INTEGER, band TEXT DEFAULT '', note TEXT DEFAULT '', created TEXT);\n"
          "CREATE INDEX IF NOT EXISTS ix_checkins_kind_day ON checkins(kind, day);\n"))
E.append(("schema version", 'SCHEMA_VERSION = "3.3.8"   # GUTLOG_V3370_JOINT ',
          'SCHEMA_VERSION = "3.3.9"   # GUTLOG_V3400_WEIGHT GUTLOG_V3370_JOINT '))
E.append(("guarded columns",
          '    ("med_schedule", "variants", "TEXT DEFAULT \'\'"),\n',
          '    ("med_schedule", "variants", "TEXT DEFAULT \'\'"),\n'
          '    # GUTLOG_V3400_WEIGHT -- a weekly medicine: its weekday and time.\n'
          '    ("med_schedule", "weekday", "TEXT DEFAULT \'\'"),\n'
          '    ("med_schedule", "at_time", "TEXT DEFAULT \'\'"),\n'))
E.append(("weekly constants",
          'SLOTS = [("MORNING", "Morning", "08:00"), ("NOON", "Noon", "14:00"),\n'
          '         ("EVENING", "Evening", "20:00"), ("NIGHT", "Night", "22:30")]\n',
          'SLOTS = [("MORNING", "Morning", "08:00"), ("NOON", "Noon", "14:00"),\n'
          '         ("EVENING", "Evening", "20:00"), ("NIGHT", "Night", "22:30")]\n'
          '# GUTLOG_V3400_WEIGHT -- a fifth kind of schedule line: once a week, on a\n'
          '# weekday at a time. Not in SLOTS on purpose: it is shown only on its day.\n'
          'WEEKLY_SLOT = "WEEKLY"\n'
          'WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]\n'
          'WEEKDAY_NAMES = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",\n'
          '                 "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}\n'
          '\n'
          '\n'
          'def weekday_of(day):\n'
          '    return WEEKDAYS[date.fromisoformat(day).weekday()]\n'))
E.append(("slot guess with windows",
          "    for until, slot in MEAL_SLOT_BY_TIME:\n"
          "        if hm < until:\n"
          "            return slot\n"
          "    return \"Dinner\"\n",
          "    win = meal_windows()   # GUTLOG_V3400_WEIGHT -- the person's own meal times, when set\n"
          "    if win:\n"
          "        return _slot_by_windows(hm, win)\n"
          "    for until, slot in MEAL_SLOT_BY_TIME:\n"
          "        if hm < until:\n"
          "            return slot\n"
          "    return \"Dinner\"\n"))
E.append(("mealcards profile",
          "                   protein_target=_protein_target(),\n"
          "                   slot_guess=meal_slot_guess(day, now_hm()))   # GUTLOG_V3350_SNACKS\n",
          "                   protein_target=_protein_target(), profile=now_profile(),   # GUTLOG_V3400_WEIGHT\n"
          "                   slot_guess=meal_slot_guess(day, now_hm()))   # GUTLOG_V3350_SNACKS\n"))
E.append(("api_now select",
          '        "SELECT s.id AS sched_id, s.med_id, s.slot, s.dose_text, s.with_food, s.variants, "\n'
          '        "       p.name AS name, "\n',
          '        "SELECT s.id AS sched_id, s.med_id, s.slot, s.dose_text, s.with_food, s.variants, "\n'
          '        "       s.weekday, s.at_time, "\n'
          '        "       p.name AS name, "\n'))
E.append(("api_now weekly",
          "    by_slot = {}\n"
          "    for r in rows:\n"
          "        by_slot.setdefault(r[\"slot\"], []).append(dict(r))\n"
          "\n"
          "    slots = []\n"
          "    for meta in _slot_meta():\n"
          "        items = by_slot.get(meta[\"slot\"], [])\n"
          "        if not items:\n"
          "            continue\n"
          "        done = len([i for i in items if i[\"status\"] in (\"TAKEN\", \"SKIPPED\")])\n"
          "        meta = dict(meta)\n"
          "        meta[\"rows\"] = items\n"
          "        meta[\"done\"] = done\n"
          "        meta[\"total\"] = len(items)\n"
          "        slots.append(meta)\n",
          "    # GUTLOG_V3400_WEIGHT -- a weekly line belongs to its weekday only.\n"
          "    wday = weekday_of(day)\n"
          "    has_any = bool(rows)\n"
          "    rows = [r for r in rows if r[\"slot\"] != WEEKLY_SLOT or r[\"weekday\"] == wday]\n"
          "    by_slot = {}\n"
          "    for r in rows:\n"
          "        by_slot.setdefault(r[\"slot\"], []).append(dict(r))\n"
          "\n"
          "    slots = []\n"
          "    wk = by_slot.get(WEEKLY_SLOT, [])\n"
          "    metas = _slot_meta() + ([{\"slot\": WEEKLY_SLOT, \"time\": min(r[\"at_time\"] or \"23:59\" for r in wk),\n"
          "                             \"label\": \"Weekly \\u00b7 \" + WEEKDAY_NAMES.get(wday, wday)}] if wk else [])\n"
          "    for meta in metas:\n"
          "        items = by_slot.get(meta[\"slot\"], [])\n"
          "        if not items:\n"
          "            continue\n"
          "        done = len([i for i in items if i[\"status\"] in (\"TAKEN\", \"SKIPPED\")])\n"
          "        meta = dict(meta)\n"
          "        meta[\"rows\"] = items\n"
          "        meta[\"done\"] = done\n"
          "        meta[\"total\"] = len(items)\n"
          "        slots.append(meta)\n"))
E.append(("api_now return",
          "    return jsonify(day=day, slots=slots, extras=extras, meds=meds,\n"
          "                   meds_all=meds_all, has_schedule=bool(rows))\n",
          "    return jsonify(day=day, slots=slots, extras=extras, meds=meds,\n"
          "                   meds_all=meds_all, has_schedule=has_any,\n"
          "                   weekly_late=weekly_late_rows(day), weekly_next=weekly_next(day))   # GUTLOG_V3400_WEIGHT\n"))
E.append(("schedule post validation",
          '    if not med_id or slot not in [s for s, _, _ in SLOTS]:\n'
          '        return jsonify(ok=False, err="Pick a medicine and a slot."), 400\n',
          '    if not med_id or slot not in [s for s, _, _ in SLOTS] + [WEEKLY_SLOT]:\n'
          '        return jsonify(ok=False, err="Pick a medicine and a slot."), 400\n'
          '    # GUTLOG_V3400_WEIGHT -- a weekly line needs its weekday and time.\n'
          '    weekday, at_time = "", ""\n'
          '    if slot == WEEKLY_SLOT:\n'
          '        weekday = (d.get("weekday") or "").strip().upper()[:3]\n'
          '        at_time = _valid_hm(d.get("at_time") or d.get("time")) or ""\n'
          '        if weekday not in WEEKDAYS or not at_time:\n'
          '            return jsonify(ok=False, err="A weekly medicine needs its day of the week and time."), 400\n'))
E.append(("schedule post insert",
          '        "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,"\n'
          '        "valid_to,epoch,notes,created) VALUES(?,?,?,?,?,\'\',?,?,?)",\n'
          '        (med_id, slot, (d.get("dose_text") or "")[:40],\n'
          '         (d.get("with_food") or "ANY")[:10], day, int(epoch), note(d), now_s()))\n',
          '        "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,"\n'
          '        "valid_to,epoch,notes,created,weekday,at_time) VALUES(?,?,?,?,?,\'\',?,?,?,?,?)",\n'
          '        (med_id, slot, (d.get("dose_text") or "")[:40],\n'
          '         (d.get("with_food") or "ANY")[:10], day, int(epoch), note(d), now_s(), weekday, at_time))\n'))
E.append(("stock: weekly per dose",
          '    for l in con.execute(\n'
          '            "SELECT med_id, dose_text, variants FROM med_schedule WHERE valid_from<=? "\n'
          '            "AND (valid_to=\'\' OR valid_to IS NULL OR valid_to>=?)", (tday, tday)).fetchall():\n'
          '        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False, "labels": []})\n'
          '        if (l["variants"] or "").strip():\n'
          '            s["variants"] = True\n'
          '            s["labels"] += [v.strip() for v in l["variants"].split("|") if v.strip()]\n'
          '        else:\n'
          '            s["units"] += _units(l["dose_text"])\n',
          '    for l in con.execute(\n'
          '            "SELECT med_id, dose_text, variants, slot FROM med_schedule WHERE valid_from<=? "\n'
          '            "AND (valid_to=\'\' OR valid_to IS NULL OR valid_to>=?)", (tday, tday)).fetchall():\n'
          '        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False, "labels": []})\n'
          '        if (l["variants"] or "").strip():\n'
          '            s["variants"] = True\n'
          '            s["labels"] += [v.strip() for v in l["variants"].split("|") if v.strip()]\n'
          '        elif l["slot"] == WEEKLY_SLOT:\n'
          '            pass   # GUTLOG_V3400_WEIGHT -- a weekly dose is counted as it is logged, never as a daily pillbox\n'
          '        else:\n'
          '            s["units"] += _units(l["dose_text"])\n'))
E.append(("export checkins",
          '        "labs": "day,analyte,value",\n',
          '        "labs": "day,analyte,value",\n'
          '        "checkins": "day,ctime,kind,total,band,answers,note",   # GUTLOG_V3400_WEIGHT\n'))
E.append(("now_profile weight",
          '    v = setting("now_profile") or "gut"\n'
          '    return v if v in ("gut", "joint", "general") else "gut"\n',
          '    v = setting("now_profile") or "gut"\n'
          '    return v if v in ("gut", "joint", "general", "weight") else "gut"   # GUTLOG_V3400_WEIGHT\n'))
E.append(("msk sites under weight",
          '    return (PAIN_SITES_JOINT + PAIN_SITES_MSK) if now_profile() == "joint" else PAIN_SITES_MSK\n',
          '    return (PAIN_SITES_JOINT + PAIN_SITES_MSK) if now_profile() in ("joint", "weight") else PAIN_SITES_MSK\n'))
E.append(("joint cards under weight",
          '    return jsonify(profile=now_profile(), show=now_profile() == "joint",\n',
          '    return jsonify(profile=now_profile(), show=now_profile() in ("joint", "weight"),   # GUTLOG_V3400_WEIGHT\n'))
E.append(("family page flag",
          '            ("Last report: %d days ago" % rep) if rep is not None else "Last report: none",\n'
          '        ]\n',
          '            ("Last report: %d days ago" % rep) if rep is not None else "Last report: none",\n'
          '            # GUTLOG_V3400_WEIGHT -- a check-in answer that needs the caretaker today.\n'
          '            "<b class=\'bad\'>Check-in flag today &mdash; please call</b>" if st.get("flag") else "",\n'
          '        ]\n'
          '        bits = [b for b in bits if b]\n'))
E.append(("python block",
          "# ------------------------------------------------------------------ auto-read\n"
          "# GUTLOG_V3100_AUTOREAD -- start the report reader the moment a file lands.\n",
          PY_BLOCK +
          "# ------------------------------------------------------------------ auto-read\n"
          "# GUTLOG_V3100_AUTOREAD -- start the report reader the moment a file lands.\n"))
E.append(("css",
          "#nowPlan .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}\n",
          "#nowPlan .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}\n" + CSS_BLOCK))
E.append(("html week card",
          '  <div class="card fold" id="nowDoses">\n'
          '    <button type="button" class="fold-h">\n'
          '      <span class="ft">Today&rsquo;s doses</span>',
          HTML_WEEK +
          '  <div class="card fold" id="nowDoses">\n'
          '    <button type="button" class="fold-h">\n'
          '      <span class="ft">Today&rsquo;s doses</span>'))
E.append(("html checkins card",
          '  <div class="card fold" id="nowSym">\n'
          '    <button type="button" class="fold-h">\n'
          '      <span class="ft">Symptom now</span>',
          HTML_CHECKINS +
          '  <div class="card fold" id="nowSym">\n'
          '    <button type="button" class="fold-h">\n'
          '      <span class="ft">Symptom now</span>'))
E.append(("html weekly form",
          '      <div class="chips" id="sc_slot"></div>\n'
          '      <div class="row2" style="margin-top:10px">\n'
          '        <div><p class="lbl">Dose</p><input type="text" id="sc_dose"',
          '      <div class="chips" id="sc_slot"></div>\n' + HTML_WEEKLY_FORM +
          '      <div class="row2" style="margin-top:10px">\n'
          '        <div><p class="lbl">Dose</p><input type="text" id="sc_dose"'))
E.append(("js schedule chips",
          "    d.slots.forEach(s=>{const b=document.createElement('div');b.className='chip';b.textContent=s.label;\n"
          "      b.onclick=()=>{schedSlot=s.slot;[...sl.children].forEach(c=>c.classList.toggle('sel',c===b));};\n"
          "      sl.appendChild(b);});}\n",
          "    /* GUTLOG_V3400_WEIGHT -- a Weekly chip opens the weekday and time. */\n"
          "    d.slots.concat([{slot:'WEEKLY',label:'Weekly'}]).forEach(s=>{const b=document.createElement('div');b.className='chip';b.textContent=s.label;\n"
          "      b.onclick=()=>{schedSlot=s.slot;[...sl.children].forEach(c=>c.classList.toggle('sel',c===b));\n"
          "        const w=$('#sc_weekly');if(w)w.style.display=s.slot==='WEEKLY'?'':'none';};\n"
          "      sl.appendChild(b);});}\n"))
E.append(("js schedule rows",
          "    row.querySelector('.s').textContent=r.slot.slice(0,3);\n"
          "    row.querySelector('.n').textContent=r.name+(r.dose_text?' \\u00b7 '+r.dose_text:'');\n",
          "    row.querySelector('.s').textContent=r.slot==='WEEKLY'?(r.weekday||'WK'):r.slot.slice(0,3);\n"
          "    row.querySelector('.n').textContent=r.name+(r.dose_text?' \\u00b7 '+r.dose_text:'')+(r.slot==='WEEKLY'&&r.at_time?' \\u00b7 weekly '+r.at_time:'');\n"))
E.append(("js schedule post",
          "    try{await post('/api/schedule',{med_id:parseInt($('#sc_med').value,10),slot:schedSlot,\n"
          "      dose_text:$('#sc_dose').value,with_food:$('#sc_food').value});\n",
          "    try{await post('/api/schedule',{med_id:parseInt($('#sc_med').value,10),slot:schedSlot,\n"
          "      dose_text:$('#sc_dose').value,with_food:$('#sc_food').value,\n"
          "      weekday:$('#sc_wday')?$('#sc_wday').value:'',at_time:$('#sc_wtime')?$('#sc_wtime').value:''});\n"))
E.append(("js loadNow calls",
          "  loadJoint();   /* GUTLOG_V3370_JOINT */\n",
          "  loadJoint();   /* GUTLOG_V3370_JOINT */\n"
          "  loadWeek();loadCheckins();   /* GUTLOG_V3400_WEIGHT */\n"))
E.append(("js loadNow weekly rows",
          "    nowData.slots.forEach(s=>{\n"
          "      done+=s.done;total+=s.total;\n",
          "    (nowData.weekly_late||[]).forEach(w=>box.appendChild(weeklyLateRow(w)));   /* GUTLOG_V3400_WEIGHT */\n"
          "    nowData.slots.forEach(s=>{\n"
          "      done+=s.done;total+=s.total;\n"))
E.append(("js loadNow banner",
          "    $('#doseSum').textContent=done+' of '+total+' taken';\n"
          "    const card=$('#nowDoses');\n",
          "    $('#doseSum').textContent=done+' of '+total+' taken';\n"
          "    weeklyBanner(box,nowData);   /* GUTLOG_V3400_WEIGHT */\n"
          "    const card=$('#nowDoses');\n"))
E.append(("js joint fold",
          "  if(!cfg.show)return;\n"
          "  jBuild(cfg);jList();loadJointWatch();loadPainMeds();loadLipids();\n",
          "  if(!cfg.show)return;\n"
          "  if(cfg.profile==='weight')JOINT_CARDS.forEach(foldify);   /* GUTLOG_V3400_WEIGHT -- folded */\n"
          "  jBuild(cfg);jList();loadJointWatch();loadPainMeds();loadLipids();\n"))
E.append(("js meal header protein",
          "  $('#mealSum').textContent=mealSumText(t);\n",
          "  $('#mealSum').textContent=mealSumText(t)+(MC.profile==='weight'?(' \\u00b7 '+Math.round(p)+' of '+MC.protein_target+' g protein'):'');   /* GUTLOG_V3400_WEIGHT */\n"))
E.append(("js block",
          "/* GUTLOG_V3380_KITCHEN -- one line, only when the Kitchen answers. */\n",
          JS_BLOCK + "/* GUTLOG_V3380_KITCHEN -- one line, only when the Kitchen answers. */\n"))

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
    print("GutLog: the weight profile -> v" + VERSION)
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
    out = src
    for l, o, n in EDITS:
        if out.count(o) != 1:
            print("anchor %s: found %d times, need 1. Nothing written." % (l, out.count(o)))
            return 1
        out = out.replace(o, n, 1)
    print("anchors: %d/%d matched" % (len(EDITS), len(EDITS)))
    if a.check:
        print("All anchors OK.")
        return 0
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
    bak = a.file + ".bak-v3400-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 family/test_family_e.py gutlog/app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

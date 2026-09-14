#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.16.0 -> v3.17.0  ::  Phase M -- Down days

WHY. A recurring cluster the record has carried for over two years as
"recurrent fatigue and subjective feverishness", evidenced entirely by
recollection: no frequency, no duration, no pattern. Every day around it is
already fully recorded -- steps, hours on legs, sleep, doses, the drug epoch.
What is missing is the marker saying which days were the bad ones. The
moment they are marked, every row already collected becomes comparable.
This phase is that marker, and the view that turns it into an answer.

THE DATA MODEL -- decided in the brief, not redesigned here.
  down_days(id, day UNIQUE, components, coped, note, created)
  A down day is a CALENDAR DAY, not a timestamped moment, so it is not an
  episodes row. day is UNIQUE: a second tap corrects rather than duplicates.
  components / coped are pipe-joined, the days.syms convention.
  RUNS ARE COMPUTED AT READ TIME from consecutive days. No run id, no
  episode id, no state that can rot -- the same principle as expected
  scheduled doses.
  TEMPERATURE DOES NOT GO IN THIS TABLE. vitals.temp already exists. The
  card prompts for it and writes a real vitals row, exactly as the analgesic
  chips write a real doses row. The card shows whether one has been taken
  today, and that is all it holds about it.

THE ENTRY. One tap on the Now screen marks today. Done. Components and
"coped with" sit behind it, optional, autosaving on tap. ONE active prompt --
temperature -- with a "not now" that does not nag. A consecutive day extends
the run and the card reads "day 2 of this run". Past days are marked from
Day by day. Nothing is seeded.

THE VIEW, reachable from Review: count per month and the length of each run;
EACH DOWN DAY BESIDE THE DAY BEFORE IT (steps, hours on legs, exercise
minutes, sleep, doses, epoch) -- the whole reason for the phase; which
components co-occur; how many down days carry a temperature; and whether what
he did changes the run length, as an observation with its n, never advice.

TWO CONNECTIONS. On the third consecutive day, a quiet note: the flare
protocol asks for calprotectin and ESR/CRP within 48 hours. And the 14-day
watch row carries a down-day lane (a square, in a fourth validated colour),
while FitLog's trend excludes those days -- see patch_fitlog_v160.py.

The two analgesic chips write through to the doses log with the reason set,
via the same helper the pain tiles use (factored out of api_pain here). Their
labels come from regimen.local.json, as every medicine name does; this file
carries none.

Requires v3.16.0 (GUTLOG_V3160_DARK). Anchor-verified, idempotent,
compile-checked, Jinja-safe, .bak before write, self-restoring, reversible.
Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "app.py")
PREV = "GUTLOG_V3160_DARK"
MARKER = "GUTLOG_V3170_DOWN"

# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------
DDL_ANCHOR = "CREATE TABLE IF NOT EXISTS rec_plan (\n"
DDL_NEW = (
    "CREATE TABLE IF NOT EXISTS down_days (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT UNIQUE, components TEXT DEFAULT '',\n"
    "  coped TEXT DEFAULT '', note TEXT DEFAULT '', created TEXT);\n"
    + DDL_ANCHOR
)

VER_ANCHOR = 'SCHEMA_VERSION = "3.3.3"   # GUTLOG_V330_PHASE_A'
VER_NEW = 'SCHEMA_VERSION = "3.3.4"   # GUTLOG_V330_PHASE_A'

MIG_ANCHOR = "    for stmt in _V330_INDEXES:\n        con.execute(stmt)\n"
MIG_NEW = MIG_ANCHOR + (
    "\n"
    "    # -- GUTLOG_V3170_DOWN: a down day is a calendar day, so it gets its\n"
    "    # own table rather than an episodes row. Idempotent, like the rest.\n"
    "    con.execute(\"CREATE TABLE IF NOT EXISTS down_days (\"\n"
    "                \"id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT UNIQUE, \"\n"
    "                \"components TEXT DEFAULT '', coped TEXT DEFAULT '', \"\n"
    "                \"note TEXT DEFAULT '', created TEXT)\")\n"
)

# --------------------------------------------------------------------------
# constants + helpers, placed after the analgesic chips are known
# --------------------------------------------------------------------------
PY_CONST_ANCHOR = "        PAIN_TREATMENTS.append(_pa[0])\n"
PY_CONST_NEW = PY_CONST_ANCHOR + '''

# GUTLOG_V3170_DOWN -- the down-day cluster as the record describes it, not a
# generic symptom list, and what was done about it. The two medicine chips
# are the analgesic labels from regimen.local.json: this file names no
# medicine.
DOWN_COMPONENTS = ["Hip / thigh ache", "Left abdominal pain", "Fatigue",
                   "Feverishness", "Heavy head / headache",
                   "Eyes burning or watering", "Broken sleep", "Low mood"]
DOWN_COPED_BASE = ["Kept moving indoors", "Rested", "Skipped exercise",
                   "Worked anyway", "Heat pad", "Hot shower", "NormaTec"]
DOWN_COPED = DOWN_COPED_BASE + list(PAIN_ANALGESICS.keys())
DOWN_MOVED, DOWN_RESTED = "Kept moving indoors", "Rested"

# Verbatim from the Action Plan's flare protocol. A note, not an alarm, and
# not advice: it repeats what his own plan already says, on the day it says
# to do it.
DOWN_PROTOCOL = ("Third day of this run; your flare protocol asks for "
                 "calprotectin and ESR/CRP within 48 hours.")


def _log_analgesics(labels, day, hm, reason, score, ref):
    """Write one doses row per analgesic chip and mirror each to FitLog with
    the score attached. Shared by the pain tiles and the down-day card, so a
    medicine is recorded the same way whichever surface it was tapped on.
    Returns (doses, mirrored, not_mirrored, linked)."""
    doses, mirrored, missed = [], [], []
    linked = _links_enabled()
    for t in labels:
        if t not in PAIN_ANALGESICS:
            continue
        mol, fallback = PAIN_ANALGESICS[t]
        mid, name = _pain_med(mol, fallback)
        db().execute(
            "INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,"
            "status,med_id,sched_id,dose_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (day, hm, name, reason, None, "", now_s(), "EXTRA", mid, None, ""))
        db().commit()
        doses.append(name)
        if not linked:
            continue
        ans = _link_post(FITLOG_URL + "/api/analgesic",
                         {"dt": day + "T" + hm, "molecule": mol, "name": name,
                          "dose_label": name, "pain_at_time": score,
                          "notes": "GutLog: " + reason, "source": "gutlog",
                          "ref": ref + "-" + t.lower().replace(" ", "-")})
        (mirrored if (ans or {}).get("ok") else missed).append(name)
    return doses, mirrored, missed, linked


def _down_runs(days):
    """Consecutive calendar days grouped into runs. Computed every time it is
    read: date arithmetic on the stored days, nothing stored about the run."""
    out = []
    for d in sorted(set(days)):
        try:
            cur = date.fromisoformat(d)
        except ValueError:
            continue
        if out and (cur - date.fromisoformat(out[-1]["end"])).days == 1:
            out[-1]["end"] = d
            out[-1]["days"].append(d)
        else:
            out.append({"start": d, "end": d, "days": [d]})
    for r in out:
        r["length"] = len(r["days"])
    return out


def _down_all_days():
    return [r["day"] for r in db().execute(
        "SELECT day FROM down_days ORDER BY day").fetchall()]


def _down_run_of(day):
    """(position in its run, the run) for a marked day; (0, None) otherwise."""
    for r in _down_runs(_down_all_days()):
        if day in r["days"]:
            return r["days"].index(day) + 1, r
    return 0, None


def _split_pipe(s):
    return [x for x in (s or "").split("|") if x]


def _down_row(day):
    r = db().execute("SELECT day, components, coped, note FROM down_days WHERE day=?",
                     (day,)).fetchone()
    if not r:
        return None
    return {"day": r["day"], "components": _split_pipe(r["components"]),
            "coped": _split_pipe(r["coped"]), "note": r["note"] or ""}


def _temp_on(day):
    r = db().execute("SELECT temp, vtime FROM vitals WHERE day=? AND temp IS NOT NULL "
                     "ORDER BY vtime DESC, id DESC LIMIT 1", (day,)).fetchone()
    return {"value": r["temp"], "vtime": r["vtime"] or ""} if r else None
'''

# --------------------------------------------------------------------------
# api_pain uses the shared helper instead of its own copy of the loop
# --------------------------------------------------------------------------
PAIN_LOOP_OLD_START = "    doses, mirrored, missed = [], [], []\n    linked = _links_enabled()\n    for t in treats:\n"
PAIN_LOOP_OLD_END = "        (mirrored if (ans or {}).get(\"ok\") else missed).append(name)\n    return jsonify(ok=True, id=eid,"
PAIN_LOOP_NEW = (
    "    doses, mirrored, missed, linked = _log_analgesics(\n"
    "        treats, day, etime, meta[1], score, \"gutlog-episode-\" + str(eid))\n"
    "    return jsonify(ok=True, id=eid,"
)

# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
ROUTE_ANCHOR = '@app.route("/api/episode/eased/<int:eid>", methods=["POST"])\n'
ROUTE_NEW = '''# ------------------------------------------------------------------ down days
# GUTLOG_V3170_DOWN
def _down_state(day):
    row = _down_row(day)
    n, run = _down_run_of(day) if row else (0, None)
    proto = ""
    if run and n >= 3:
        proto = DOWN_PROTOCOL if n == 3 else (
            "Day " + str(n) + " of this run; your flare protocol asks for "
            "calprotectin and ESR/CRP within 48 hours.")
    return {"day": day, "marked": bool(row),
            "components": row["components"] if row else [],
            "coped": row["coped"] if row else [],
            "note": row["note"] if row else "",
            "run": ({"n": n, "length": run["length"], "start": run["start"],
                     "end": run["end"]} if run else None),
            "protocol": proto, "temp": _temp_on(day),
            "components_all": DOWN_COMPONENTS, "coped_all": DOWN_COPED}


@app.route("/api/downday")
@login_required
def api_downday():
    day = _valid_day(request.args.get("day")) or today()
    return jsonify(ok=True, **_down_state(day))


@app.route("/api/downday", methods=["POST"])
@login_required
def api_downday_set():
    """Mark a day. With no body beyond the day it is one tap; components,
    coped and temp are each optional and each may arrive on its own later.
    day is UNIQUE, so a repeat corrects the row rather than adding one."""
    d = J()
    day = _valid_day(d.get("day") or today())
    if not day:
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    before = _down_row(day) or {"components": [], "coped": [], "note": ""}
    comps = before["components"]
    if "components" in d:
        comps = [c for c in (d.get("components") or []) if c in DOWN_COMPONENTS]
    coped = before["coped"]
    if "coped" in d:
        coped = [c for c in (d.get("coped") or []) if c in DOWN_COPED]
    nt = before["note"] if "note" not in d else note(d)
    db().execute(
        "INSERT INTO down_days(day,components,coped,note,created) VALUES(?,?,?,?,?) "
        "ON CONFLICT(day) DO UPDATE SET components=excluded.components, "
        "coped=excluded.coped, note=excluded.note",
        (day, "|".join(comps), "|".join(coped), nt, now_s()))
    db().commit()

    # A medicine chip writes a real doses row -- once. Only chips that were
    # not already on the stored row are logged, so correcting the row cannot
    # record the same tablet twice.
    new_meds = [c for c in coped if c in PAIN_ANALGESICS and c not in before["coped"]]
    hm = now_hm() if day == today() else "12:00"
    doses, mirrored, missed, linked = _log_analgesics(
        new_meds, day, hm, "Down day", None, "gutlog-down-" + day)

    # Temperature is a vitals row, never a column here.
    temp_saved = None
    if d.get("temp") not in (None, ""):
        try:
            tv = float(d.get("temp"))
        except (TypeError, ValueError):
            tv = None
        if tv is not None and 30 <= tv <= 115:
            vt = _valid_hm(d.get("vtime")) or (now_hm() if day == today() else "12:00")
            insert("vitals", ["day", "vtime", "sys", "dia", "pulse", "weight", "waist",
                              "temp", "notes"],
                   [day, vt, None, None, None, None, None, tv, "Down day"])
            temp_saved = tv
    st = _down_state(day)
    st.update(ok=True, doses=doses, mirrored=mirrored, not_mirrored=missed,
              linked=linked, temp_saved=temp_saved)
    return jsonify(**st)


@app.route("/api/downday/unmark", methods=["POST"])
@login_required
def api_downday_unmark():
    d = J()
    day = _valid_day(d.get("day") or today())
    if not day:
        return jsonify(ok=False, err="Pick a real date."), 400
    db().execute("DELETE FROM down_days WHERE day=?", (day,))
    db().commit()
    return jsonify(ok=True, **_down_state(day))


@app.route("/api/feed/downdays")
@feed_required
def api_feed_downdays():
    """Read-only, for FitLog: which days were down days, so its trend can
    leave them out instead of reading them as non-adherence."""
    since = _feed_since(60)
    rows = [dict(day=r["day"], components=_split_pipe(r["components"]),
                 coped=_split_pipe(r["coped"]))
            for r in db().execute(
                "SELECT day, components, coped FROM down_days WHERE day>=? ORDER BY day",
                (since,)).fetchall()]
    return jsonify(ok=True, app="gutlog", since=since, days=rows,
                   runs=_down_runs([r["day"] for r in rows]))


def _act_minutes_by_day(since):
    marks = ",".join("?" for _ in LOAD_KINDS)
    rows = db().execute(
        "SELECT day, SUM(minutes) AS m FROM activities "
        "WHERE kind NOT IN (" + marks + ") AND day>=? GROUP BY day",
        tuple(LOAD_KINDS) + (since,)).fetchall()
    return dict((r["day"], float(r["m"] or 0)) for r in rows)


@app.route("/api/downdays")
@login_required
def api_downdays():
    """The view. Everything here is read from rows that already exist; the
    down-day marks are the only new fact, and they make the rest comparable."""
    try:
        days_n = max(7, min(365, int(request.args.get("days") or 90)))
    except (TypeError, ValueError):
        days_n = 90
    since = (date.today() - timedelta(days=days_n)).isoformat()
    # one day earlier than the window, so the oldest down day has a "before"
    since_b = (date.today() - timedelta(days=days_n + 1)).isoformat()
    marked = [dict(day=r["day"], components=_split_pipe(r["components"]),
                   coped=_split_pipe(r["coped"]), note=r["note"] or "")
              for r in db().execute(
                  "SELECT day, components, coped, note FROM down_days "
                  "WHERE day>=? ORDER BY day", (since,)).fetchall()]
    mdays = [m["day"] for m in marked]
    runs = _down_runs(mdays)

    # ---- what already exists for every day in the window ----------------
    feed = _link_get(FITLOG_URL + "/api/feed/watch?days=" + str(min(180, days_n + 2)),
                     ttl=60)
    linked = bool((feed or {}).get("ok"))
    by_date = dict((x.get("date"), x) for x in ((feed or {}).get("daily") or []))
    epochs = (feed or {}).get("epochs") or []
    load = _watch_load_by_day(since_b)
    act = _act_minutes_by_day(since_b)
    sleep = dict((r["day"], r["sleep"]) for r in db().execute(
        "SELECT day, sleep FROM days WHERE day>=?", (since_b,)).fetchall())
    doses = dict((r["day"], r["n"]) for r in db().execute(
        "SELECT day, COUNT(*) AS n FROM doses WHERE day>=? "
        "AND COALESCE(status,'')<>'SKIPPED' GROUP BY day", (since_b,)).fetchall())
    temps = {}
    for r in db().execute(
            "SELECT day, temp, vtime FROM vitals WHERE day>=? AND temp IS NOT NULL "
            "ORDER BY day, vtime", (since_b,)).fetchall():
        temps[r["day"]] = {"value": r["temp"], "vtime": r["vtime"] or ""}

    def epoch_of(d):
        for e in epochs:
            s, en = e.get("date_start") or "", e.get("date_end") or ""
            if s and d < s:
                continue
            if en and d > en:
                continue
            return e.get("label") or ""
        return ""

    def facts(d):
        m = (by_date.get(d) or {}).get("metrics") or {}
        st = m.get("steps") or {}
        sl = m.get("sleep_hours") or {}
        return {"day": d,
                "steps": st.get("value"),
                "load_h": (round(load[d] / 60.0, 1) if d in load else None),
                "act_min": (round(act[d]) if d in act else None),
                "sleep": (sl.get("value") if sl.get("value") is not None
                          else (sleep.get(d) or None)),
                "doses": doses.get(d, 0),
                "temp": (temps.get(d) or {}).get("value"),
                "epoch": epoch_of(d)}

    pairs = []
    for m in marked:
        b = (date.fromisoformat(m["day"]) - timedelta(days=1)).isoformat()
        pairs.append({"day": m["day"], "before": b, "d": facts(m["day"]),
                      "b": facts(b), "components": m["components"],
                      "coped": m["coped"]})

    # ---- per month --------------------------------------------------------
    months = {}
    for m in marked:
        months.setdefault(m["day"][:7], {"month": m["day"][:7], "n": 0, "runs": []})
        months[m["day"][:7]]["n"] += 1
    for r in runs:
        months.setdefault(r["start"][:7], {"month": r["start"][:7], "n": 0, "runs": []})
        months[r["start"][:7]]["runs"].append(r["length"])
    months = [months[k] for k in sorted(months)]

    # ---- co-occurrence ----------------------------------------------------
    ccount = {}
    pcount = {}
    for m in marked:
        cs = sorted(set(m["components"]))
        for c in cs:
            ccount[c] = ccount.get(c, 0) + 1
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                k = cs[i] + " + " + cs[j]
                pcount[k] = pcount.get(k, 0) + 1
    comps = sorted(ccount.items(), key=lambda kv: (-kv[1], kv[0]))
    cpairs = sorted(pcount.items(), key=lambda kv: (-kv[1], kv[0]))[:6]

    # ---- temperature: the one active ask, so its absence is reported -------
    with_t = [{"day": m["day"], "value": temps[m["day"]]["value"],
               "vtime": temps[m["day"]]["vtime"]} for m in marked if m["day"] in temps]

    # ---- what he did, against the run length: an observation with its n ----
    def runs_where(tag):
        ls = [r["length"] for r in runs
              if any(tag in (x["coped"]) for x in marked if x["day"] in r["days"])]
        return {"n": len(ls), "mean_len": (round(sum(ls) / float(len(ls)), 1) if ls else None)}
    coped_runs = {"kept_moving": runs_where(DOWN_MOVED), "rested": runs_where(DOWN_RESTED),
                  "note": "An observation over the runs marked so far, not a recommendation."}

    return jsonify(ok=True, days=days_n, since=since, link=linked,
                   marked=marked, runs=runs, months=months, pairs=pairs,
                   components=[{"name": k, "n": v} for k, v in comps],
                   pairs_cooccur=[{"pair": k, "n": v} for k, v in cpairs],
                   temps={"with": len(with_t), "of": len(marked), "values": with_t},
                   coped_runs=coped_runs,
                   err="" if linked else "FitLog is not answering, so steps and "
                                          "sleep are not available for these days.")


''' + ROUTE_ANCHOR

# --------------------------------------------------------------------------
# watch row lane + day view entry
# --------------------------------------------------------------------------
LOAD_ANCHOR = "    load = _watch_load_by_day(since)\n"
LOAD_NEW = LOAD_ANCHOR + (
    "    down = set(r[\"day\"] for r in db().execute(\n"
    "        \"SELECT day FROM down_days WHERE day>=?\", (since,)).fetchall())\n"
)
OT_ROW_ANCHOR = '                    "ot": d in load,\n'
OT_ROW_NEW = OT_ROW_ANCHOR + '                    "down": d in down,\n'

DV_ANCHOR = ('    for r in db().execute(\n'
             '            "SELECT id, mtime, slot, items, protein FROM meals WHERE day=?",\n')
DV_NEW = (
    "    dd = _down_row(day)\n"
    "    if dd:\n"
    "        n, run = _down_run_of(day)\n"
    "        sub = \", \".join(dd[\"components\"]) or \"no components noted\"\n"
    "        if run and run[\"length\"] > 1:\n"
    "            sub = \"day \" + str(n) + \" of \" + str(run[\"length\"]) + \" \\u00b7 \" + sub\n"
    "        add(\"down_days\", 0, \"\", \"Down day\", \"Down day\", sub, down=True)\n"
) + DV_ANCHOR

# --------------------------------------------------------------------------
# tokens + css
# --------------------------------------------------------------------------
LIGHT_MK_OLD = "--mkpain:#B3372A;--mkot:#6A3FA8;--mkep:#B57B08;\n"
LIGHT_MK_NEW = "--mkpain:#B3372A;--mkot:#6A3FA8;--mkep:#B57B08;--mkdown:#0A93B0;\n"
DARK_MK_OLD = "--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;"
DARK_MK_NEW = "--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;"
DARK_MK_N = 8

CSS_ANCHOR = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n"
CSS_NEW = """/* GUTLOG_V3170_DOWN -- the down-day card: one tap, everything else behind it.
   The lane marker is a SQUARE in a fourth colour validated with the other
   three: light #0A93B0 on #FCFCF9, dark #3D9BE0 on #18211F. */
#nowDown .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}
#nowDown .dwtop .q{margin:0;flex:1}
#nowDown .dwtop .fs{font-size:15px;font-weight:700;color:var(--muted)}
#nowDown.on .dwtop .fs{color:var(--teal)}
#nowDown .dwmore{margin-top:4px}
.dwtemp{display:flex;gap:8px;align-items:center;margin-top:14px;flex-wrap:wrap}
.dwtemp .lbl{margin:0;flex:1 1 100%;}
.dwtemp input{flex:0 0 120px;padding:11px 10px;border:1.5px solid var(--line);border-radius:10px;
  font-size:16px;background:var(--card);color:var(--ink)}
.dwtemp .btn{margin:0;padding:11px 14px}
.dwnote{font-size:15px;line-height:1.5;color:var(--ink);background:var(--chip);
  border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:14px 0 0}
.wklane.down .mk.on::after{background:var(--mkdown);border-radius:1px}
.wkkey i.down{background:var(--mkdown);border-radius:1px}
.tag.k-down{background:var(--chip);color:var(--teal)}
/* every caption on these surfaces is 14px or more; .lbl elsewhere is 13.5px */
#nowDown .lbl,#ddBody .lbl,.dwtemp .lbl{font-size:14px}
#nowDown .btn.tiny,#dvDown{font-size:14px}
.ddwrap{margin-top:6px}
.ddtab{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.ddtab th,.ddtab td{padding:7px 5px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top;line-height:1.45}
.ddtab td:first-child{white-space:nowrap;padding-right:8px}
.ddtab td.fx{white-space:normal;color:var(--ink)}
.ddtab th{color:var(--muted);font-weight:700}
.ddtab tr.dn td{font-weight:700}
.ddtab tr.dn td:first-child{color:var(--teal)}
.ddtab tr.bf td:first-child{color:var(--muted)}
.ddobs{font-size:15px;line-height:1.5;margin:8px 2px 0}
""" + CSS_ANCHOR

# --------------------------------------------------------------------------
# markup
# --------------------------------------------------------------------------
NOW_ANCHOR = '  <div class="card fold" id="nowWatch">\n'
NOW_NEW = '''  <div class="card" id="nowDown">
    <div class="dwtop"><p class="q">Down day</p><span class="fs" id="downSum"></span></div>
    <button type="button" class="btn primary" id="downMark">Mark today as a down day</button>
    <p class="hint" id="downHint" style="margin:8px 2px 0">One tap. Nothing else is asked.</p>
    <div class="dwmore" id="downMore" hidden>
      <p class="lbl">What it is today &mdash; optional, saves as you tap</p>
      <div class="chips" id="downComp"></div>
      <p class="lbl" style="margin-top:12px">Coped with &mdash; optional</p>
      <div class="chips" id="downCoped"></div>
      <div id="downTemp"></div>
      <p class="dwnote" id="downProto" hidden></p>
      <button type="button" class="btn tiny ghost" id="downUnmark" style="margin-top:14px">Not a down day after all</button>
    </div>
  </div>

''' + NOW_ANCHOR

RV_ANCHOR = '  <div class="card" id="vitalsLog">\n'
RV_NEW = '''  <div class="card fold" id="downView">
    <button type="button" class="fold-h">
      <span class="ft">Down days</span><span class="fs" id="ddSum">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody" id="ddBody"></div>
  </div>
''' + RV_ANCHOR

DV_HINT_ANCHOR = ('    <p class="hint" style="margin:8px 2px 4px"><span id="dvSum"></span>'
                  ' &middot; tap an entry to fix its time</p>\n')
DV_HINT_NEW = DV_HINT_ANCHOR + (
    '    <div class="btnrow" style="margin:4px 0 8px"><button type="button" class="btn tiny ghost" '
    'id="dvDown">Mark this day as a down day</button></div>\n')

# --------------------------------------------------------------------------
# js
# --------------------------------------------------------------------------
DVTAG_OLD = "Pain:'pain',Load:'load'};"
DVTAG_NEW = "Pain:'pain',Load:'load','Down day':'down'};"
DVSUM_ANCHOR = "  $('#dvSum').textContent=v.entries.length?(v.entries.length+' logged'):'nothing logged';\n"
DVSUM_NEW = DVSUM_ANCHOR + "  loadDvDown();\n"
LOADNOW_OLD = "  loadPain();\n  loadWatch();\n"
LOADNOW_NEW = "  loadPain();\n  loadDown();\n  loadWatch();\n"
LANES_OLD = "  [['pain','pain'],['ot','ot']].forEach(p=>{"
LANES_NEW = "  [['pain','pain'],['ot','ot'],['down','down']].forEach(p=>{"
BIT_OLD = "    if(r.pain)bits.push('pain logged');\n"
BIT_NEW = BIT_OLD + "    if(r.down)bits.push('down day');\n"
KEY_OLD = "    '<span><i class=\"ot\"></i>operating day</span>'+\n"
KEY_NEW = KEY_OLD + "    '<span><i class=\"down\"></i>down day</span>'+\n"
RVLOAD_OLD = "async function loadReview(){\n  loadDayView();\n"
RVLOAD_NEW = "async function loadReview(){\n  loadDayView();\n  loadDownView();\n"

JS_ANCHOR = "/* GUTLOG_V3130_WATCH -- the watch screen."
JS_NEW = r'''/* GUTLOG_V3170_DOWN -- a down day is a calendar day, marked with one tap.
   Everything else on the card is optional and saves as it is tapped, so
   there is no form to leave half-filled on a day with a heavy head. The one
   active prompt is a temperature; "not now" is remembered for the day. */
let downState=null;
function downTempSkipped(){
  try{ return localStorage.getItem('gl_temp_skip')===todayISO; }catch(e){ return false; }
}
async function loadDown(){
  const card=$('#nowDown');
  if(!card)return;
  const j=await jget('/api/downday?day='+todayISO);
  downState=j;
  renderDown(j);
}
function renderDown(j){
  const card=$('#nowDown'),sum=$('#downSum'),mark=$('#downMark'),
        more=$('#downMore'),hint=$('#downHint');
  card.classList.toggle('on',!!j.marked);
  if(!j.marked){
    sum.textContent='';
    mark.hidden=false;hint.hidden=false;more.hidden=true;
    return;
  }
  mark.hidden=true;hint.hidden=true;more.hidden=false;
  sum.textContent=(j.run&&j.run.n>1)?('day '+j.run.n+' of this run'):'marked today';
  downChips($('#downComp'),j.components_all,j.components,'components');
  downChips($('#downCoped'),j.coped_all,j.coped,'coped');
  renderDownTemp(j);
  const p=$('#downProto');
  if(j.protocol){ p.textContent=j.protocol;p.hidden=false; }
  else{ p.hidden=true; }
}
function downChips(box,all,sel,key){
  box.innerHTML='';
  (all||[]).forEach(v=>{
    const b=document.createElement('div');
    b.className='chip'+((sel||[]).indexOf(v)>=0?' sel':'');
    b.textContent=v;
    b.onclick=async()=>{
      if(b.dataset.busy)return;
      b.dataset.busy='1';
      const cur=(downState[key]||[]).slice();
      const i=cur.indexOf(v);
      if(i>=0)cur.splice(i,1); else cur.push(v);
      const body={day:todayISO};
      body[key]=cur;
      try{
        const r=await post('/api/downday',body);
        if(r.doses&&r.doses.length)toast(r.doses.join(' + ')+' recorded');
        if(r.not_mirrored&&r.not_mirrored.length)toast('FitLog did not take '+r.not_mirrored.join(', '));
        await loadDown();
      }catch(err){ toast(err.message); }
      b.dataset.busy='';
    };
    box.appendChild(b);
  });
}
function renderDownTemp(j){
  const box=$('#downTemp');
  box.innerHTML='';
  if(j.temp){
    const p=document.createElement('p');
    p.className='hint';
    p.style.margin='12px 2px 0';
    p.textContent='Temperature '+j.temp.value+'° at '+j.temp.vtime+', in your vitals.';
    box.appendChild(p);
    return;
  }
  if(downTempSkipped())return;
  const w=document.createElement('div');
  w.className='dwtemp';
  w.innerHTML='<p class="lbl">Temperature now? The one number worth taking.</p>'+
    '<input type="number" step="0.1" min="30" max="115" inputmode="decimal" placeholder="37.0" aria-label="Temperature">'+
    '<button type="button" class="btn primary go">Save</button>'+
    '<button type="button" class="btn nn">Not now</button>';
  w.querySelector('.go').onclick=async()=>{
    const v=parseFloat(w.querySelector('input').value);
    if(!(v>=30&&v<=115)){ toast('Enter a temperature'); return; }
    try{
      await post('/api/downday',{day:todayISO,temp:v});
      toast('Temperature saved to your vitals');
      loadDown();
    }catch(err){ toast(err.message); }
  };
  w.querySelector('.nn').onclick=()=>{
    try{ localStorage.setItem('gl_temp_skip',todayISO); }catch(e){ }
    box.innerHTML='';
  };
  box.appendChild(w);
}
(function(){
  const m=$('#downMark');
  if(m)m.onclick=async()=>{
    if(m.dataset.busy)return;
    m.dataset.busy='1';
    try{
      await post('/api/downday',{day:todayISO});
      toast('Marked as a down day');
      await loadDown();
      loadWatch();
    }catch(err){ toast(err.message); }
    m.dataset.busy='';
  };
  const u=$('#downUnmark');
  if(u)u.onclick=async()=>{
    try{
      await post('/api/downday/unmark',{day:todayISO});
      toast('Not a down day');
      await loadDown();
      loadWatch();
    }catch(err){ toast(err.message); }
  };
})();
/* Day by day: mark or unmark the day being viewed -- the backfill path. */
async function loadDvDown(){
  const b=$('#dvDown');
  if(!b)return;
  const j=await jget('/api/downday?day='+dvDay);
  if(j.marked){
    const r=j.run||{n:1,length:1};
    b.textContent='Down day'+(r.length>1?(' · day '+r.n+' of '+r.length):'')+' · tap to unmark';
  }else{
    b.textContent='Mark this day as a down day';
  }
  b.onclick=async()=>{
    try{
      await post(j.marked?'/api/downday/unmark':'/api/downday',{day:dvDay});
      toast(j.marked?'Unmarked':'Marked as a down day');
      loadDayView();
      if(dvDay===todayISO)loadDown();
      loadWatch();
    }catch(err){ toast(err.message); }
  };
}
/* The view: every down day beside the day before it, from rows that already
   exist. Observations carry their n; nothing here is advice. */
function ddEsc(s){
  return String(s===null||s===undefined?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function ddNum(v,dp){
  if(v===null||v===undefined||v==='')return '—';
  if(typeof v==='number')return (dp?v.toFixed(dp):Math.round(v).toLocaleString('en-IN'));
  return ddEsc(v);
}
/* Seven facts per day would be eight columns, which is 466px on a 368px
   card. Each fact carries its own label instead, so the pair reads as two
   lines in the same order and nothing has to scroll sideways at 360px. */
function ddRow(label,f,down){
  const bits=['steps '+ddNum(f.steps),'on legs '+ddNum(f.load_h,1)+(f.load_h!==null&&f.load_h!==undefined?' h':''),
    'exercise '+ddNum(f.act_min)+(f.act_min!==null&&f.act_min!==undefined?' min':''),
    'sleep '+ddNum(f.sleep),'doses '+ddNum(f.doses),
    'temp '+ddNum(f.temp,1)+(f.temp!==null&&f.temp!==undefined?'°':'')];
  if(f.epoch)bits.push(ddEsc(f.epoch));
  return '<tr class="'+(down?'dn':'bf')+'"><td>'+ddEsc(label)+'</td><td class="fx">'+bits.join(' · ')+'</td></tr>';
}
async function loadDownView(){
  const body=$('#ddBody');
  if(!body)return;
  const j=await jget('/api/downdays?days='+rvDays);
  const n=(j.marked||[]).length;
  $('#ddSum').textContent=n?(n+' in '+rvDays+' days'):'none marked';
  if(!n){
    body.innerHTML='<p class="hint" style="margin:0">No down days marked in this range. '+
      'Mark today from Now, or a past day from Day by day above.</p>';
    return;
  }
  let h='';
  h+='<p class="lbl">By month</p><div class="ddwrap"><table class="ddtab">'+
     '<tr><th>Month</th><th>Down days</th><th>Run lengths</th></tr>';
  (j.months||[]).forEach(m=>{
    h+='<tr><td>'+ddEsc(m.month)+'</td><td>'+m.n+'</td><td>'+(m.runs.length?m.runs.join(', '):'—')+'</td></tr>';
  });
  h+='</table></div>';
  h+='<p class="lbl" style="margin-top:14px">Each down day beside the day before it</p>';
  h+='<div class="ddwrap"><table class="ddtab">';
  (j.pairs||[]).forEach(p=>{
    h+=ddRow('before · '+wkDay(p.before),p.b,false);
    h+=ddRow('down · '+wkDay(p.day),p.d,true);
  });
  h+='</table></div>';
  if(!j.link)h+='<p class="hint" style="margin:6px 2px 0">'+ddEsc(j.err)+'</p>';
  h+='<p class="lbl" style="margin-top:14px">What came together</p>';
  if((j.components||[]).length){
    h+='<p class="ddobs">'+(j.components.map(c=>ddEsc(c.name)+' · '+c.n).join('<br>'))+'</p>';
    if((j.pairs_cooccur||[]).length){
      h+='<p class="hint" style="margin:8px 2px 0">Together: '+
         j.pairs_cooccur.map(c=>ddEsc(c.pair)+' ('+c.n+')').join('; ')+'</p>';
    }
  }else{
    h+='<p class="hint" style="margin:0">No components noted yet.</p>';
  }
  h+='<p class="lbl" style="margin-top:14px">Temperature</p>';
  if(j.temps&&j.temps.with){
    h+='<p class="ddobs">A reading on '+j.temps.with+' of '+j.temps.of+' down days: '+
       j.temps.values.map(t=>ddNum(t.value,1)+'° ('+wkDay(t.day)+')').join(', ')+'</p>';
  }else{
    h+='<p class="ddobs">No temperature on any of these '+j.temps.of+' down days yet. '+
       'That is the one number this card asks for.</p>';
  }
  h+='<p class="lbl" style="margin-top:14px">What you did, against how long it lasted</p>';
  const cr=j.coped_runs||{};
  const km=cr.kept_moving||{},rs=cr.rested||{};
  const bits=[];
  if(km.n)bits.push('runs where you kept moving indoors: n='+km.n+', mean '+km.mean_len+' days');
  if(rs.n)bits.push('runs where you rested: n='+rs.n+', mean '+rs.mean_len+' days');
  h+='<p class="ddobs">'+(bits.length?bits.join('<br>'):'Nothing marked under coped with yet.')+
     '<br><span class="hint" style="margin:0">'+ddEsc(cr.note||'')+'</span></p>';
  body.innerHTML=h;
}

''' + JS_ANCHOR


def build_edits():
    E = []
    E.append(("version marker", PREV + "\n", PREV + " " + MARKER + "\n"))
    E.append(("schema version", VER_ANCHOR, VER_NEW))
    E.append(("ddl", DDL_ANCHOR, DDL_NEW))
    E.append(("migrate", MIG_ANCHOR, MIG_NEW))
    E.append(("constants + helpers", PY_CONST_ANCHOR, PY_CONST_NEW))
    E.append(("routes", ROUTE_ANCHOR, ROUTE_NEW))
    E.append(("watch down set", LOAD_ANCHOR, LOAD_NEW))
    E.append(("watch row lane", OT_ROW_ANCHOR, OT_ROW_NEW))
    E.append(("day view entry", DV_ANCHOR, DV_NEW))
    E.append(("light token", LIGHT_MK_OLD, LIGHT_MK_NEW))
    E.append(("css", CSS_ANCHOR, CSS_NEW))
    E.append(("now card", NOW_ANCHOR, NOW_NEW))
    E.append(("review card", RV_ANCHOR, RV_NEW))
    E.append(("day view button", DV_HINT_ANCHOR, DV_HINT_NEW))
    E.append(("js dv tag", DVTAG_OLD, DVTAG_NEW))
    E.append(("js dv load", DVSUM_ANCHOR, DVSUM_NEW))
    E.append(("js loadNow", LOADNOW_OLD, LOADNOW_NEW))
    E.append(("js lanes", LANES_OLD, LANES_NEW))
    E.append(("js tap text", BIT_OLD, BIT_NEW))
    E.append(("js key", KEY_OLD, KEY_NEW))
    E.append(("js loadReview", RVLOAD_OLD, RVLOAD_NEW))
    E.append(("js block", JS_ANCHOR, JS_NEW))
    return E


def pain_loop_edit(src):
    """The one edit that is a slice, not a literal: api_pain's own copy of the
    analgesic loop, replaced by the shared helper."""
    i = src.index(PAIN_LOOP_OLD_START)
    j = src.index(PAIN_LOOP_OLD_END, i) + len(PAIN_LOOP_OLD_END)
    return src[i:j]


MUST_DEFINE_PY = ["_log_analgesics", "_down_runs", "_down_run_of", "_down_state",
                  "api_downday", "api_downday_set", "api_downday_unmark",
                  "api_feed_downdays", "api_downdays"]
MUST_DEFINE_JS = ["loadDown", "renderDown", "downChips", "renderDownTemp",
                  "loadDvDown", "loadDownView", "ddRow", "loadWatch", "wkChart",
                  "loadPain", "loadNow", "loadReview", "loadDayView"]

BANNED = re.compile(r"\b(his|him|he)\b", re.I)


def read(path):
    fh = open(path, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(path, text):
    fh = open(path, "w", encoding="utf-8", newline="")
    try:
        fh.write(text)
    finally:
        fh.close()


def all_edits(src):
    E = build_edits()
    E.append(("api_pain shared loop", pain_loop_edit(src), PAIN_LOOP_NEW))
    return E


def reverse(path, out_path):
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    # forward edits, keyed on their NEW text; the pain-loop edit's old text is
    # reconstructed from what api_pain looked like before
    E = build_edits()
    old_loop = (PAIN_LOOP_OLD_START
                + "        if t not in PAIN_ANALGESICS:\n"
                "            continue\n"
                "        mol, fallback = PAIN_ANALGESICS[t]\n"
                "        mid, name = _pain_med(mol, fallback)\n"
                "        db().execute(\n"
                "            \"INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,\"\n"
                "            \"status,med_id,sched_id,dose_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)\",\n"
                "            (day, etime, name, meta[1], None, \"\", now_s(), \"EXTRA\", mid, None, \"\"))\n"
                "        db().commit()\n"
                "        doses.append(name)\n"
                "        if not linked:\n"
                "            continue\n"
                "        ans = _link_post(FITLOG_URL + \"/api/analgesic\",\n"
                "                         {\"dt\": day + \"T\" + etime, \"molecule\": mol, \"name\": name,\n"
                "                          \"dose_label\": name, \"pain_at_time\": score,\n"
                "                          \"notes\": \"GutLog: \" + meta[1], \"source\": \"gutlog\",\n"
                "                          \"ref\": \"gutlog-episode-\" + str(eid)})\n"
                + PAIN_LOOP_OLD_END)
    E.append(("api_pain shared loop", old_loop, PAIN_LOOP_NEW))
    bad = []
    for label, anchor, new in E:
        if src.count(new) != 1:
            bad.append("  " + label + ": new text appears " + str(src.count(new)) + " times, need 1")
    if src.count(DARK_MK_NEW) != DARK_MK_N:
        bad.append("  dark token: " + str(src.count(DARK_MK_NEW)) + ", need " + str(DARK_MK_N))
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    out = src
    for label, anchor, new in E:
        out = out.replace(new, anchor, 1)
    out = out.replace(DARK_MK_NEW, DARK_MK_OLD)
    if MARKER in out or PREV not in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed " + PREV + " -> " + out_path + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT",
                    help="reconstruct v3.16.0 from a patched file (for the negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("GutLog Phase M: down days -> v3.17.0")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.16.0. Apply that first.")
        return 1
    try:
        edits = all_edits(src)
    except ValueError as exc:
        print("FATAL: could not locate a block to replace: " + str(exc))
        return 1

    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    n = src.count(DARK_MK_OLD)
    if n != DARK_MK_N:
        problems.append("  dark token: found " + str(n) + ", need " + str(DARK_MK_N))
    print("anchors: " + str(len(edits) + 1 - len(problems)) + "/" + str(len(edits) + 1) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1

    # rule 5b -- the page is a Jinja template
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2

    # voice gate: every quoted string this patch writes is second person
    for label, anchor, new in edits:
        for lit in re.findall(r"'((?:[^'\\]|\\.)*)'", new) + re.findall(r'"((?:[^"\\]|\\.)*)"', new):
            if BANNED.search(lit):
                print("VOICE: third person in " + label + ": " + lit)
                return 2

    if args.check:
        print("All anchors OK; Jinja-safe; strings are second person.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    out = out.replace(DARK_MK_OLD, DARK_MK_NEW)

    missing = [f for f in MUST_DEFINE_PY if not re.search(r"\ndef " + f + r"\(", out)]
    missing += [f for f in MUST_DEFINE_JS if not re.search(r"function\s+" + f + r"\s*\(", out)]
    if missing:
        print("DEFINITION CHECK FAILED, nothing written: " + ", ".join(missing))
        return 2

    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    write(tmpf, out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v3170-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits + " + str(DARK_MK_N) + " dark tokens")
    print("-" * 66)
    print("Next:  python3 test_phase_m.py && python3 test_ui_now.py app.py")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

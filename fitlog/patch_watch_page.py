#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: read-only Apple Watch view at /watch.

The Watch is now the only active wearable. Health Connect is parked - its
rows stay, its code path stays, nothing is deleted - so the page labels it
"parked" rather than hiding it.

Read-only by construction: the route runs SELECTs and renders. No F-rule
consumes an ingested metric, and this patch does not touch the rule engine,
the verdict path, or any knowledge file.

Style note: every string the page builds is plain concatenation, never an
f-string. The server runs Python 3.9 (no PEP 701), and CSS/SVG braces
inside an f-string are a live hazard in this codebase.

Compile-checks before writing, takes a .bak, idempotent. Python 3.9.

Usage:
    python3 patch_watch_page.py --dry-run
    python3 patch_watch_page.py
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/app.py"

# ------------------------------------------------------------- 1. styles

C1_OLD = '''.msg{background:#e5f5ec;border:1px solid #79c99a;border-radius:8px;padding:8px;margin:8px 0;font-size:14px}
"""'''

C1_NEW = '''.msg{background:#e5f5ec;border:1px solid #79c99a;border-radius:8px;padding:8px;margin:8px 0;font-size:14px}
.wrings{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}
.wring{display:flex;align-items:center;gap:8px;min-width:158px}
.wring-l{font-size:13px;line-height:1.35}.wring-v{font-size:17px;font-weight:700}
.wscroll{overflow-x:auto}.wscroll table{min-width:520px}
.wr,.wc{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;border-radius:4px;font-size:10px;font-weight:700}
.wr{background:#0b6e6e;color:#fff}.wc{background:#e7eef2;color:#5a6b78}
.wbars{display:flex;align-items:flex-end;gap:2px;height:82px;overflow-x:auto;margin:6px 0}
.wbar{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;min-width:11px}
.wbar i{display:block;width:9px;background:#0b6e6e;border-radius:2px 2px 0 0}
.wbar u{font-size:9px;color:#8a9aa8;text-decoration:none;margin-top:2px}
"""'''

# ---------------------------------------------------------------- 2. nav

C2_OLD = '''        <a href="/events">Events</a> <a href="/meds">Meds</a> <a href="/tests">Tests</a> <a href="/history">Log</a>'''

C2_NEW = '''        <a href="/watch">Watch</a> <a href="/events">Events</a> <a href="/meds">Meds</a> <a href="/tests">Tests</a> <a href="/history">Log</a>'''

# -------------------------------------------------------------- 3. route

C3_OLD = '''@app.route("/health")
def health():
    return {"app": "fitlog", "version": "1.2.0", "ok": True}'''

C3_NEW = '''# ---------------- Apple Watch view (read-only) ----------------
# Renders what the ingest layer already stored. Writes nothing, decides
# nothing. RULE_BEARING is imported rather than restated so the page can
# never disagree with the engine about which metrics a rule may read.
from health_ingest import RULE_BEARING as W_RULE_BEARING

W_SRC = "applewatch"
W_TREND_DAYS = 28
W_RECENT_DAYS = 14
W_CIRC = 263.9  # 2 * pi * r, r = 42

# metric key, label, unit suffix, decimal places
W_DAILY = (
    ("steps", "Steps", "", 0),
    ("active_energy_kcal", "Active", "kcal", 0),
    ("exercise_minutes", "Exercise", "min", 0),
    ("stand_hours", "Stand", "h", 0),
    ("resting_hr", "Rest HR", "bpm", 0),
    ("hrv_ms", "HRV", "ms", 1),
    ("sleep_hours", "Sleep", "h", 1),
)

# label, achieved metric, goal metric, unit, ring colour
W_RINGS = (
    ("Move", "move_energy_kcal", "move_goal_kcal", "kcal", "#e0245e"),
    ("Exercise", "exercise_minutes", "exercise_goal_min", "min", "#9bd430"),
    ("Stand", "stand_hours", "stand_goal_hours", "h", "#38d6e0"),
)

W_DASH = "\\u2014"


def w_esc(v):
    s = str(v)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;")


def w_fmt(v, places):
    if v is None:
        return W_DASH
    try:
        f = float(v)
    except (TypeError, ValueError):
        return W_DASH
    if places == 0:
        return str(int(round(f)))
    return str(round(f, places))


def w_metrics(days):
    """Return {date: {metric: value}} for the Watch over the last N days."""
    cut = (date.today() - timedelta(days=days - 1)).isoformat()
    out = {}
    rows = db().execute(
        "SELECT date, metric, value FROM health_metrics "
        "WHERE source = ? AND date >= ? ORDER BY date DESC",
        (W_SRC, cut)).fetchall()
    for r in rows:
        out.setdefault(r["date"], {})[r["metric"]] = r["value"]
    return out


def w_ring_svg(pct, colour):
    filled = W_CIRC * min(max(pct, 0.0), 1.0)
    rest = W_CIRC - filled
    out = ['<svg viewBox="0 0 100 100" width="74" height="74" aria-hidden="true">']
    out.append('<circle cx="50" cy="50" r="42" fill="none" stroke="#e7eef2" '
               'stroke-width="13"/>')
    if filled > 0:
        out.append('<circle cx="50" cy="50" r="42" fill="none" stroke="' + colour +
                   '" stroke-width="13" stroke-linecap="round" stroke-dasharray="' +
                   str(round(filled, 1)) + " " + str(round(rest, 1)) +
                   '" transform="rotate(-90 50 50)"/>')
    out.append("</svg>")
    return "".join(out)


def w_rings_card(rows):
    day = None
    for d in sorted(rows.keys(), reverse=True):
        for spec in W_RINGS:
            if rows[d].get(spec[1]) is not None:
                day = d
                break
        if day:
            break
    if day is None:
        return ""
    cells = []
    for label, got_key, goal_key, unit, colour in W_RINGS:
        got = rows[day].get(got_key)
        goal = rows[day].get(goal_key)
        pct = 0.0
        if got is not None and goal:
            pct = float(got) / float(goal)
        pct_txt = str(int(round(pct * 100))) + "%" if goal else "no goal on file"
        goal_txt = w_fmt(goal, 0) if goal else W_DASH
        cells.append('<div class="wring">' + w_ring_svg(pct, colour) +
                     '<div class="wring-l"><b>' + label + "</b><br>" +
                     '<span class="wring-v">' + w_fmt(got, 0) + "</span> / " +
                     goal_txt + " " + unit + '<br><span class="small">' +
                     pct_txt + "</span></div></div>")
    return ("<h2>Activity rings</h2>" +
            '<div class="card"><div class="small">' + w_esc(day) +
            ' &middot; source: Apple Watch</div><div class="wrings">' +
            "".join(cells) + "</div>" +
            '<div class="small">Goals are read from the Watch itself and are '
            "context-only: a target is not a measurement, and no rule reads "
            "one.</div></div>")


def w_recent_card(rows):
    days = sorted(rows.keys(), reverse=True)[:W_RECENT_DAYS]
    if not days:
        return ""
    head = ["<tr><th>Date</th>"]
    for key, label, unit, places in W_DAILY:
        mark = "R" if key in W_RULE_BEARING else "C"
        cls = "wr" if key in W_RULE_BEARING else "wc"
        unit_txt = '<br><span class="small">' + unit + "</span>" if unit else ""
        head.append('<th><span class="' + cls + '" title="' +
                    ("rule-bearing" if cls == "wr" else "context-only") +
                    '">' + mark + "</span> " + label + unit_txt + "</th>")
    head.append("</tr>")
    body = []
    for d in days:
        body.append("<tr><td>" + w_esc(d[5:]) + "</td>")
        for key, label, unit, places in W_DAILY:
            body.append("<td>" + w_fmt(rows[d].get(key), places) + "</td>")
        body.append("</tr>")
    return ("<h2>Recent days</h2>" +
            '<div class="card"><div class="wscroll"><table>' +
            "".join(head) + "".join(body) + "</table></div>" +
            '<div class="small">Every value shown is Apple Watch. A blank cell '
            "means the Watch sent nothing for that day, not a zero.</div></div>")


def w_trend_card(rows):
    days = sorted(rows.keys())
    if not days:
        return ""
    series = [(d, rows[d].get("steps")) for d in days]
    vals = [v for d, v in series if v is not None]
    if not vals:
        return ""
    top = max(float(v) for v in vals)
    bars = []
    for d, v in series:
        h = 0
        if v is not None and top > 0:
            h = int(round(58.0 * float(v) / top))
            if h < 1:
                h = 1
        bars.append('<div class="wbar" title="' + w_esc(d) + ": " +
                    w_fmt(v, 0) + ' steps"><i style="height:' + str(h) +
                    'px"></i><u>' + w_esc(d[8:]) + "</u></div>")
    stats = []
    for key, label, unit, places in W_DAILY:
        got = [rows[d].get(key) for d in days if rows[d].get(key) is not None]
        if not got:
            continue
        nums = [float(x) for x in got]
        mark = "R" if key in W_RULE_BEARING else "C"
        cls = "wr" if key in W_RULE_BEARING else "wc"
        suffix = (" " + unit) if unit else ""
        stats.append('<tr><td><span class="' + cls + '">' + mark + "</span> " +
                     label + "</td><td>" +
                     w_fmt(sum(nums) / len(nums), places) + suffix + "</td><td>" +
                     w_fmt(min(nums), places) + suffix + "</td><td>" +
                     w_fmt(max(nums), places) + suffix + "</td><td>" +
                     str(len(nums)) + "</td></tr>")
    return ("<h2>Trend " + W_DASH + " last " + str(W_TREND_DAYS) + " days</h2>" +
            '<div class="card"><div class="small">Daily steps</div>' +
            '<div class="wbars">' + "".join(bars) + "</div>" +
            "<table><tr><th>Metric</th><th>Mean</th><th>Low</th><th>High</th>" +
            "<th>Days</th></tr>" + "".join(stats) + "</table>" +
            '<div class="small">Mean, low and high are over the days the Watch '
            "actually reported, shown in the Days column.</div></div>")


def w_workouts_card():
    cut = (date.today() - timedelta(days=W_TREND_DAYS - 1)).isoformat()
    rows = db().execute(
        "SELECT date, start_ts, wtype, duration_s, distance_km, energy_kcal "
        "FROM health_workouts WHERE source = ? AND date >= ? "
        "ORDER BY start_ts DESC LIMIT 40", (W_SRC, cut)).fetchall()
    if not rows:
        return ("<h2>Workouts</h2>" +
                '<div class="card"><div class="small">No Watch workouts in the '
                "last " + str(W_TREND_DAYS) + " days.</div></div>")
    out = ["<tr><th>Date</th><th>Type</th><th>Time</th><th>Distance</th>"
           "<th>Energy</th></tr>"]
    for r in rows:
        secs = r["duration_s"]
        mins = W_DASH if secs is None else str(int(round(float(secs) / 60.0))) + " min"
        km = r["distance_km"]
        km_txt = W_DASH if km is None else str(round(float(km), 2)) + " km"
        kc = r["energy_kcal"]
        kc_txt = W_DASH if kc is None else str(int(round(float(kc)))) + " kcal"
        out.append("<tr><td>" + w_esc(r["date"][5:]) + "</td><td>" +
                   w_esc(str(r["wtype"] or "unknown").title()) + "</td><td>" +
                   mins + "</td><td>" + km_txt + "</td><td>" + kc_txt +
                   "</td></tr>")
    return ("<h2>Workouts</h2>" +
            '<div class="card"><table>' + "".join(out) + "</table>" +
            '<div class="small">Source: Apple Watch. Context-only ' + W_DASH +
            " no rule reads a workout.</div></div>")


def w_sources_card():
    rows = db().execute(
        "SELECT source, COUNT(*) AS n, MIN(date) AS d0, MAX(date) AS d1 "
        "FROM health_metrics GROUP BY source ORDER BY source").fetchall()
    out = ["<tr><th>Source</th><th>Rows</th><th>From</th><th>To</th>"
           "<th>State</th></tr>"]
    for r in rows:
        src = r["source"]
        if src == W_SRC:
            state = '<b style="color:#1d8a4e">active</b>'
        elif src == "healthconnect":
            state = '<span class="small">parked ' + W_DASH + " kept, not fed</span>"
        else:
            state = '<span class="small">manual entry</span>'
        out.append("<tr><td>" + w_esc(src) + "</td><td>" + str(r["n"]) +
                   "</td><td>" + w_esc(r["d0"] or W_DASH) + "</td><td>" +
                   w_esc(r["d1"] or W_DASH) + "</td><td>" + state + "</td></tr>")
    return ("<h2>Sources</h2>" +
            '<div class="card"><table>' + "".join(out) + "</table>" +
            '<div class="small">S01 source precedence: Apple Watch &gt; Health '
            "Connect &gt; manual, per metric per date. Values are never summed "
            "or averaged across sources " + W_DASH + " Health Connect can only "
            "fill a date the Watch left silent. Samsung rows are retained "
            "deliberately; nothing healthconnect has been deleted.</div></div>")


@app.route("/watch")
@login_required
def watch_view():
    rows = w_metrics(W_TREND_DAYS)
    if not rows:
        body = ("<h1>Apple Watch</h1>" +
                '<div class="card">No Watch data in the last ' +
                str(W_TREND_DAYS) + " days. The feed posts to "
                "<code>/api/ingest?source=applewatch</code> with the bearer "
                "token.</div>" + w_sources_card())
        return page("Watch", body)
    parts = ["<h1>Apple Watch</h1>"]
    parts.append(w_rings_card(rows))
    parts.append(w_recent_card(rows))
    parts.append(w_trend_card(rows))
    parts.append(w_workouts_card())
    parts.append(w_sources_card())
    parts.append('<div class="card"><b>How to read this page</b>'
                 '<div class="small"><span class="wr">R</span> rule-bearing: '
                 "the rule engine is permitted to read this metric. "
                 '<span class="wc">C</span> context-only: shown so you can '
                 "interpret a day, never read by a rule.<br><br>"
                 "Rule-bearing metrics: " + w_esc(", ".join(W_RULE_BEARING)) +
                 ".<br>Heart rate and HRV are context-only on purpose "
                 "" + W_DASH + " HR is unreliable during medication titration, "
                 "so aerobic intensity stays governed by the talk test."
                 "<br><br>This page is read-only. No F-rule consumes an "
                 "ingested metric; verdict logic is unchanged by anything "
                 "shown here.</div></div>")
    parts.append('<p class=small><a href="/">Home</a> &middot; '
                 '<a href="/history">Log</a></p>')
    return page("Watch", "".join(parts))


@app.route("/health")
def health():
    return {"app": "fitlog", "version": "1.3.0", "ok": True}'''

EDITS = [
    ("watch styles", C1_OLD, C1_NEW),
    ("nav link", C2_OLD, C2_NEW),
    ("watch route", C3_OLD, C3_NEW),
]


def main():
    dry = "--dry-run" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = positional[0] if positional else TARGET

    if not target.endswith(".py"):
        print("FAIL: target must be a .py file, got: " + target)
        return 2
    if not os.path.exists(target):
        print("FAIL: not found: " + target)
        return 2
    print("Target  : " + target)

    with open(target, "r") as fh:
        src = fh.read()

    if "def watch_view" in src:
        print("SKIP: watch page already present.")
        return 0

    patched = src
    for name, old, new in EDITS:
        count = patched.count(old)
        if count != 1:
            print("FAIL: anchor '" + name + "' matched " + str(count) + " times.")
            print("Nothing written.")
            return 1
        patched = patched.replace(old, new, 1)
        print("  anchored: " + name)

    try:
        compile(patched, target, "exec")
    except SyntaxError as exc:
        print("FAIL: does not compile: " + str(exc))
        return 1
    print("Compile : ok")

    if dry:
        print("Dry run. Nothing written.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    bak = target + ".bak_" + stamp
    shutil.copy2(target, bak)
    print("Backup  : " + bak)
    with open(target, "w") as fh:
        fh.write(patched)
    print("Patched : ok")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

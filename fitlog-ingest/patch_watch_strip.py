#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: live Apple Watch progress strip on the
main vitals page.

A compact "Today so far" block at the top of /, the page shown on login
where the morning check-in happens. TODAY, not yesterday: progress during
the day.

Placement
---------
Immediately after the safety flags and immediately before the check-in
form. The flags stay first on purpose - demoting an F-rule warning below
a data strip would be a regression in a health system - and the strip is
built to one card of roughly 130px so the Sleep scale stays on the first
screen. Logging remains the primary action.

Freshness
---------
Every populated strip carries "as of HH:MM IST" taken from
MAX(ingested_at) for today's Watch rows, plus a relative age. Past three
hours the stamp turns amber. A figure can never be read as live when it
is not.

Empty day
---------
If nothing has arrived today the strip says so in words and names the
last reading on file. It never renders a zero the Watch did not send.

Read-only: SELECTs and rendering only. RULE_BEARING is imported from
health_ingest (already aliased W_RULE_BEARING by patch_watch_page.py),
never restated, so the R/C badges cannot drift from the engine.

Every string is plain concatenation, never an f-string: Python 3.9 has no
PEP 701 and CSS/SVG braces inside an f-string break this codebase.

Compile-checks before writing, .bak with a microsecond stamp, idempotent.

Usage:
    python3 patch_watch_strip.py --dry-run
    python3 patch_watch_strip.py [target_app.py]
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/app.py"

# ------------------------------------------------------- 1. strip styles

S1_OLD = '''.wbar u{font-size:9px;color:#8a9aa8;text-decoration:none;margin-top:2px}
"""'''

S1_NEW = '''.wbar u{font-size:9px;color:#8a9aa8;text-decoration:none;margin-top:2px}
.wstrip{padding:10px 12px}
.wstrip-h{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}
.wstrip-h b{font-size:14px}
.wfresh{font-size:11px;color:#5a6b78;margin-left:auto;white-space:nowrap}
.wfresh.stale{color:#a56a00;font-weight:600}
.wstrip-h a{font-size:11px;white-space:nowrap}
.wts{display:flex;gap:6px;align-items:flex-end}
.wt{flex:1;text-align:center;min-width:0}
.wt-n{font-size:12px;font-weight:700;margin-top:-3px}
.wt-big{font-size:23px;font-weight:700;line-height:46px}
.wt-l{font-size:10px;color:#5a6b78;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wt-l .wr,.wt-l .wc{width:12px;height:12px;line-height:12px;font-size:9px;border-radius:3px}
.wctx{margin-top:6px}.wctx .wc{width:12px;height:12px;line-height:12px;font-size:9px;border-radius:3px}
"""'''

# --------------------------------------- 2. ring svg gains a size option

S2_OLD = '''def w_ring_svg(pct, colour):
    filled = W_CIRC * min(max(pct, 0.0), 1.0)
    rest = W_CIRC - filled
    out = ['<svg viewBox="0 0 100 100" width="74" height="74" aria-hidden="true">']'''

S2_NEW = '''def w_ring_svg(pct, colour, size=74):
    filled = W_CIRC * min(max(pct, 0.0), 1.0)
    rest = W_CIRC - filled
    out = ['<svg viewBox="0 0 100 100" width="' + str(size) + '" height="' +
           str(size) + '" aria-hidden="true">']'''

# ------------------------------------------------- 3. the strip renderer

S3_OLD = '''@app.route("/watch")
@login_required
def watch_view():'''

S3_NEW = '''# label, value keys in preference order, goal key, unit, ring colour
W_STRIP = (
    ("Move", ("move_energy_kcal", "active_energy_kcal"), "move_goal_kcal",
     "kcal", "#e0245e"),
    ("Exercise", ("exercise_minutes",), "exercise_goal_min", "min", "#9bd430"),
    ("Stand", ("stand_hours",), "stand_goal_hours", "h", "#38d6e0"),
)

W_STALE_MINUTES = 180


def w_age_mins(stamp):
    """Minutes since an 'YYYY-MM-DD HH:MM:SS' local stamp, or None."""
    try:
        then = datetime.strptime(str(stamp), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    delta = (datetime.now() - then).total_seconds()
    if delta < 0:
        return 0
    return int(delta // 60)


def w_age_txt(mins):
    if mins is None:
        return ""
    if mins < 1:
        return "just now"
    if mins < 60:
        return str(mins) + " min ago"
    if mins < 1440:
        return str(mins // 60) + " h ago"
    return str(mins // 1440) + " d ago"


def watch_today_strip(t):
    """
    Compact live Watch progress for today, for the top of the vitals page.

    Read-only. No rule reads any value shown here; the R/C badges come
    from the engine's own RULE_BEARING, never from a local copy.
    """
    rows = db().execute(
        "SELECT metric, value, ingested_at FROM health_metrics "
        "WHERE source = ? AND date = ?", (W_SRC, t)).fetchall()
    vals = {}
    stamp = None
    for r in rows:
        vals[r["metric"]] = r["value"]
        if stamp is None or str(r["ingested_at"]) > stamp:
            stamp = str(r["ingested_at"])

    link = '<a href="/watch">All Watch data &rarr;</a>'
    shown = ("steps", "exercise_minutes", "stand_hours", "active_energy_kcal",
             "move_energy_kcal", "resting_hr", "hrv_ms")
    has_data = False
    for k in shown:
        if vals.get(k) is not None:
            has_data = True
            break

    if not has_data:
        last = db().execute(
            "SELECT date, MAX(ingested_at) AS at FROM health_metrics "
            "WHERE source = ? GROUP BY date ORDER BY date DESC LIMIT 1",
            (W_SRC,)).fetchone()
        if last and last["at"]:
            tail = ("Last reading was " + w_esc(last["date"]) + " at " +
                    w_esc(str(last["at"])[11:16]) + " IST.")
        else:
            tail = "No Watch data on file yet."
        return ('<div class="card wstrip" id="wstrip">'
                '<div class="wstrip-h"><b>\\u231a Today so far</b>' + link +
                '</div><div class="small">Nothing has arrived from the Watch '
                "yet today. " + tail + "</div></div>")

    mins = w_age_mins(stamp)
    age = w_age_txt(mins)
    cls = "wfresh stale" if (mins is not None and mins >= W_STALE_MINUTES) else "wfresh"
    hhmm = w_esc(str(stamp)[11:16]) if stamp else "\\u2014"
    fresh = ('<span class="' + cls + '">as of ' + hhmm + " IST" +
             ((" \\u00b7 " + age) if age else "") + "</span>")

    steps_mark = "wr" if "steps" in W_RULE_BEARING else "wc"
    tiles = ['<div class="wt"><div class="wt-big">' + w_fmt(vals.get("steps"), 0) +
             '</div><div class="wt-l"><span class="' + steps_mark + '">' +
             ("R" if steps_mark == "wr" else "C") + "</span> Steps</div></div>"]

    for label, keys, goal_key, unit, colour in W_STRIP:
        got = None
        for k in keys:
            if vals.get(k) is not None:
                got = vals.get(k)
                break
        goal = vals.get(goal_key)
        pct = 0.0
        if got is not None and goal:
            pct = float(got) / float(goal)
        cap = w_fmt(got, 0)
        if goal:
            cap = cap + "/" + w_fmt(goal, 0)
        rb = keys[0] in W_RULE_BEARING
        badge = "wr" if rb else "wc"
        tiles.append('<div class="wt">' + w_ring_svg(pct, colour, 46) +
                     '<div class="wt-n">' + cap + " " + unit + "</div>" +
                     '<div class="wt-l"><span class="' + badge + '">' +
                     ("R" if rb else "C") + "</span> " + label + "</div></div>")

    ctx = []
    if vals.get("resting_hr") is not None:
        ctx.append("Rest HR " + w_fmt(vals.get("resting_hr"), 0) + " bpm")
    if vals.get("hrv_ms") is not None:
        ctx.append("HRV " + w_fmt(vals.get("hrv_ms"), 1) + " ms")
    ctx_html = ""
    if ctx:
        ctx_html = ('<div class="small wctx"><span class="wc">C</span> ' +
                    " \\u00b7 ".join(ctx) + " \\u2014 context only, no rule "
                    "reads these</div>")

    return ('<div class="card wstrip" id="wstrip"><div class="wstrip-h">'
            '<b>\\u231a Today so far</b>' + fresh + link + "</div>" +
            '<div class="wts">' + "".join(tiles) + "</div>" + ctx_html +
            "</div>")


@app.route("/watch")
@login_required
def watch_view():'''

# ----------------------------------------- 4. wire it into the home page

S4_OLD = '''    for fid, txt in compute_flags():
        body += f'<div class="flag">\\u2691 <b>{fid}</b> {txt}</div>'
    if not ci:'''

S4_NEW = '''    for fid, txt in compute_flags():
        body += f'<div class="flag">\\u2691 <b>{fid}</b> {txt}</div>'
    # Live Watch progress for today. Read-only, and deliberately below the
    # safety flags and above the check-in form: logging stays the primary
    # action on this page.
    body += watch_today_strip(t)
    if not ci:'''

EDITS = [
    ("strip styles", S1_OLD, S1_NEW),
    ("ring size option", S2_OLD, S2_NEW),
    ("strip renderer", S3_OLD, S3_NEW),
    ("wire into home", S4_OLD, S4_NEW),
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

    if "watch_today_strip" in src:
        print("SKIP: today strip already present.")
        return 0
    if "def watch_view" not in src:
        print("FAIL: run patch_watch_page.py first.")
        return 1
    if "W_RULE_BEARING" not in src:
        print("FAIL: RULE_BEARING alias missing; run patch_watch_page.py first.")
        return 1

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

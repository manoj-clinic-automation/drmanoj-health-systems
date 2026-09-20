#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
FitLog :: FITLOG_SLEEP_P2_PAGE -- the night, on the page, never scored.

Puts what FITLOG_SLEEP_P2 stores in front of him:

  * a Sleep card on /watch: the night's clock times in IST, asleep against
    time in bed, the stage split drawn to scale, the breaks between recorded
    blocks and the measured awake time kept separate, and the overnight
    figures each labelled with the basis it was worked out on
  * the last fourteen nights as a table, with HIS OWN rolling median and the
    number of nights it rests on
  * resting HR and HRV relabelled "Rest HR overnight" / "HRV overnight"
    wherever they appear, and the Today-so-far strip saying they are derived
    overnight by the Watch. He has been reading them as cardiac figures; they
    are sleep figures as much as cardiac ones and nothing said so.
  * the watch feed carries the whole night, so GutLog can put it beside a day

THE ONE RULE THIS PAGE EXISTS UNDER
-----------------------------------
**It never scores a night.** No readiness figure, no recovery percentage, no
"poor night", no streak, no target he can fail. He has post-discontinuation
insomnia, and a device that grades sleep every morning makes insomnia worse --
the anxiety about the number becomes its own cause. Where a comparison is
unavoidable it is against his own recent median over the nights the Watch
actually recorded, never against a population figure. He has been an early
riser his whole life; a 05:00 wake is his baseline and nothing here may imply
otherwise.

The card says all of that in plain words, because a constraint that lives only
in a patch header is one refactor away from being gone.

No type below 14px in anything added here.

Requires FITLOG_V160_DOWNDAYS in app.py and FITLOG_SLEEP_P2 in health_ingest.
Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, reversible. Python 3.9.
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
PREV = "FITLOG_V160_DOWNDAYS"
MARKER = "FITLOG_SLEEP_P2_PAGE"

# ---------------------------------------------------------------- edit 1
HDR_OLD = "FITLOG_V160_DOWNDAYS -- FitLog v1.6.0 trend excludes GutLog's down days.\n"
HDR_NEW = HDR_OLD + (
    "FITLOG_SLEEP_P2_PAGE -- the sleep record on /watch. Measured, never scored.\n")

# ---------------------------------------------------------------- edit 2
CSS_OLD = ".wctx{margin-top:6px}.wctx .wc{width:12px;height:12px;line-height:12px;font-size:9px;border-radius:3px}\n"
CSS_NEW = CSS_OLD + (
    ".wsleep-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;font-size:15px}\n"
    ".wsleep-h em{color:#5a6b78;font-size:14px;font-style:normal}\n"
    ".wsleep-big{font-size:28px;font-weight:700;line-height:1.2;margin:6px 0 2px}\n"
    ".wsleep-sub{font-size:14px;color:#3d4d59;line-height:1.55}\n"
    ".wstage{display:flex;height:18px;border-radius:9px;overflow:hidden;margin:12px 0 8px;background:#e7eef2}\n"
    ".wstage i{display:block;height:18px}\n"
    ".wstage-l{display:flex;flex-wrap:wrap;gap:12px;font-size:14px;line-height:1.6}\n"
    ".wstage-l span{white-space:nowrap}\n"
    ".wstage-l u{display:inline-block;width:11px;height:11px;border-radius:3px;text-decoration:none;margin-right:5px}\n"
    ".wbasis{font-size:14px;color:#5a6b78;line-height:1.5}\n"
    ".wover{margin:10px 0 4px}\n"
    ".wover-r{padding:9px 0;border-bottom:1px solid #eef2f5;font-size:15px}\n"
    ".wover-r:last-child{border-bottom:0}\n"
    ".wover-v{font-weight:700;white-space:nowrap}\n"
    ".wnote{font-size:14px;color:#3d4d59;line-height:1.6;margin-top:12px}\n"
    ".wnote b{color:#1a2733}\n"
    ".wmed{font-size:14px;color:#3d4d59;line-height:1.6;margin-top:8px}\n"
)

# ---------------------------------------------------------------- edit 3
DAILY_OLD = '''    ("resting_hr", "Rest HR", "bpm", 0),
    ("hrv_ms", "HRV", "ms", 1),
    ("sleep_hours", "Sleep", "h", 1),
)
'''

DAILY_NEW = '''    # FITLOG_SLEEP_P2_PAGE. Named for what they are. The Watch derives both
    # of these while he is asleep: they are sleep figures as much as cardiac
    # ones, and for a week nothing on this page said so.
    ("resting_hr", "Rest HR overnight", "bpm", 0),
    ("hrv_ms", "HRV overnight", "ms", 1),
    ("sleep_hours", "Asleep", "h", 1),
)
'''

# ---------------------------------------------------------------- edit 4
# app.py spells these two characters as · and — escapes in its own
# source. The anchor has to carry the BACKSLASH, not the character, so it is
# built from chr(92) rather than typed -- an editor that helpfully resolves
# the escape on the way in would make this anchor silently match nothing.
_BS = chr(92)
_MID = '" ' + _BS + 'u00b7 ".join(ctx) + " ' + _BS + 'u2014 '

STRIP_OLD = ('                    ' + _MID + 'context only, no rule "\n'
             '                    "reads these</div>")\n')

STRIP_NEW = ('                    ' + _MID + 'derived overnight by "\n'
             '                    "the Watch; context only, no rule reads '
             'these</div>")\n')

# ---------------------------------------------------------------- edit 5
CARD_OLD = '''@app.route("/watch")
@login_required
def watch_view():
'''

CARD_NEW = r'''# ---------------- the sleep record (FITLOG_SLEEP_P2_PAGE) ----------------
# NEVER SCORE A NIGHT. No readiness figure, no recovery percentage, no
# "poor night", no streak, no target. He has post-discontinuation insomnia
# and a number graded every morning becomes its own cause. Everything here
# is a measurement or a statement about how a measurement was made. Where a
# comparison is unavoidable it is against HIS OWN recent median over the
# nights the Watch actually recorded, never a population figure.
W_SLEEP_NIGHTS = 14

# key, label, colour. Apple's four stages, in the order they are stacked.
W_STAGES = (
    ("rem_h", "REM", "#5b8def"),
    ("core_h", "Core", "#3aa6a6"),
    ("deep_h", "Deep", "#27406f"),
    ("awake_h", "Awake", "#c9803a"),
)

# key, label, unit, decimal places
W_OVERNIGHT = (
    ("wrist_temp_c", "Wrist temperature", "°C", 2),
    ("resp_rate", "Respiratory rate", "/min", 1),
    ("spo2_pct", "Blood oxygen", "%", 0),
    ("hrv_ms", "HRV", "ms", 1),
    ("resting_hr", "Resting heart rate", "bpm", 0),
)


def w_hm(hours):
    """'5 h 41 m'. A night is read in hours and minutes, not in 5.68."""
    if hours is None:
        return W_DASH
    try:
        total = int(round(float(hours) * 60))
    except (TypeError, ValueError):
        return W_DASH
    if total < 0:
        return W_DASH
    h, m = divmod(total, 60)
    if h and m:
        return str(h) + " h " + str(m) + " m"
    if h:
        return str(h) + " h"
    return str(m) + " m"


def w_clock(ts):
    """HH:MM out of a stored stamp. Stored IST, shown IST, never converted
    here -- the conversion happened once, at ingest."""
    text = str(ts or "")
    if len(text) < 16:
        return W_DASH
    return text[11:16]


def w_median(vals):
    got = sorted(float(v) for v in vals if v is not None)
    if not got:
        return None
    mid = len(got) // 2
    if len(got) % 2:
        return got[mid]
    return (got[mid - 1] + got[mid]) / 2.0


def w_nights(days):
    """{date: night} over the last N days, newest first. Never raises."""
    import health_ingest as hi
    conn = db()
    out = {}
    try:
        hi._ensure_sleep_block_table(conn)
        cut = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = conn.execute(
            "SELECT DISTINCT date FROM health_sleep_blocks "
            "WHERE source = ? AND date >= ? ORDER BY date DESC",
            (W_SRC, cut)).fetchall()
        for r in rows:
            night = hi.sleep_night(conn, r["date"], W_SRC)
            if night:
                out[r["date"]] = night
    except Exception:
        return {}
    return out


def w_stage_bar(night):
    parts = []
    legend = []
    total = 0.0
    for key, label, colour in W_STAGES:
        val = night.get(key)
        if val:
            total += float(val)
    for key, label, colour in W_STAGES:
        val = night.get(key)
        if val is None:
            continue
        pct = (100.0 * float(val) / total) if total > 0 else 0.0
        if pct > 0:
            parts.append('<i style="width:' + str(round(pct, 2)) +
                         "%;background:" + colour + '"></i>')
        legend.append('<span><u style="background:' + colour + '"></u>' +
                      w_esc(label) + " " + w_hm(val) + "</span>")
    if not parts:
        return ""
    return ('<div class="wstage">' + "".join(parts) + "</div>" +
            '<div class="wstage-l">' + "".join(legend) + "</div>")


def w_overnight_rows(night):
    over = night.get("overnight") or {}
    rows = []
    for key, label, unit, places in W_OVERNIGHT:
        got = over.get(key)
        if not got or got.get("value") is None:
            continue
        if got.get("basis") == "night":
            n = got.get("n")
            basis = "over the night"
            if n:
                basis = basis + " · " + str(n) + (
                    " reading" if n == 1 else " readings")
            if got.get("min") is not None and got.get("max") is not None \
                    and got["min"] != got["max"]:
                basis = basis + " · " + w_fmt(got["min"], places) + \
                    "–" + w_fmt(got["max"], places)
        else:
            # Said out loud. Apple stamps wrist temperature at midnight, so
            # it often falls outside the sleep span; calling that an
            # overnight figure would be a small lie told every morning.
            basis = "the day's figure, not measured inside the night"
        # A list rather than a table: at 375px a three-column table pushed
        # the basis text off the right edge of the card, which is where the
        # honesty lives.
        rows.append('<div class="wover-r">' + w_esc(label) + " " +
                    '<span class="wover-v">' + w_fmt(got["value"], places) +
                    " " + w_esc(unit) + "</span><br>" +
                    '<span class="wbasis">' + basis + "</span></div>")
    if not rows:
        return ""
    return '<div class="wover">' + "".join(rows) + "</div>"


def w_sleep_card(rows):
    nights = w_nights(W_SLEEP_NIGHTS)
    if not nights:
        return ("<h2>Sleep</h2>" +
                '<div class="card"><div class="wsleep-sub">No night has '
                "reached the server yet. Health Auto Export has to be sending "
                "Sleep, and its export window has to cover the whole night "
                "— a window that starts at midnight clips everything "
                "before it.</div></div>")

    days = sorted(nights.keys(), reverse=True)
    latest = nights[days[0]]

    head = ('<div class="wsleep-h"><b>Night of ' + w_esc(days[0]) + "</b>" +
            ('<em>' + w_esc(latest.get("device") or "") + "</em>"
             if latest.get("device") else "") + "</div>")

    big = ('<div class="wsleep-big">' + w_hm(latest.get("asleep_h")) +
           " asleep</div>")

    breaks = latest.get("awakenings") or 0
    sub = ('<div class="wsleep-sub">' +
           w_clock(latest.get("start_ts")) + " → " +
           w_clock(latest.get("end_ts")) + " IST" +
           " · " + w_hm(latest.get("in_bed_h")) + " in bed" +
           " · " + w_hm(latest.get("awake_h")) + " awake in the night" +
           " · " + str(breaks) +
           (" break" if breaks == 1 else " breaks") +
           " between recorded sleep blocks</div>")

    # His own recent median, over the nights the Watch actually recorded.
    vals = [nights[d].get("asleep_h") for d in days]
    med = w_median(vals)
    n_med = len([v for v in vals if v is not None])
    med_txt = ""
    if med is not None:
        med_txt = ('<div class="wmed">Your own last ' + str(len(days)) +
                   (" night" if len(days) == 1 else " nights") +
                   " on record: median <b>" + w_hm(med) + "</b> asleep, over "
                   + str(n_med) + (" night" if n_med == 1 else " nights") +
                   " with data. This is the only thing on this page anything "
                   "is compared with.</div>")

    # Recent nights.
    trows = []
    for d in days:
        n = nights[d]
        trows.append("<tr><td>" + w_esc(d[5:]) + "</td><td>" +
                     w_clock(n.get("start_ts")) + "</td><td>" +
                     w_clock(n.get("end_ts")) + "</td><td>" +
                     w_hm(n.get("asleep_h")) + "</td><td>" +
                     w_hm(n.get("in_bed_h")) + "</td><td>" +
                     w_hm(n.get("awake_h")) + "</td><td>" +
                     str(n.get("awakenings") or 0) + "</td></tr>")
    table = ('<div class="wscroll"><table>'
             "<tr><th>Date</th><th>From</th><th>To</th><th>Asleep</th>"
             "<th>In bed</th><th>Awake</th><th>Breaks</th></tr>" +
             "".join(trows) + "</table></div>")

    note = ('<div class="wnote"><b>Measured, not scored.</b> There is no sleep '
            "score on this page, no readiness or recovery figure, and no "
            "target. Nothing here says a night was good or bad. Where "
            "anything is compared, it is with your own recent median over the "
            "nights the Watch actually recorded — never with a "
            "population figure.<br><br>"
            "<b>Breaks</b> counts the gaps between sleep blocks the Watch "
            "recorded. It is a floor, not a count of times you woke: the "
            "export carries no awakening count. <b>Awake</b> is the measured "
            "time awake inside the night and is exact.<br><br>"
            "Resting heart rate and HRV are derived by the Watch while you "
            "are asleep. They are sleep figures as much as cardiac ones, "
            "which is why they appear here as well as in the tables above."
            "</div>")

    return ("<h2>Sleep</h2>" + '<div class="card">' + head + big + sub +
            w_stage_bar(latest) + w_overnight_rows(latest) + table +
            med_txt + note + "</div>")


@app.route("/watch")
@login_required
def watch_view():
'''

# ---------------------------------------------------------------- edit 6
WIRE_OLD = '''    parts.append(w_rings_card(rows))
    parts.append(w_recent_card(rows))
'''

WIRE_NEW = '''    parts.append(w_rings_card(rows))
    parts.append(w_sleep_card(rows))
    parts.append(w_recent_card(rows))
'''

# ---------------------------------------------------------------- edit 7
HOW_OLD = '''                 "Rule-bearing metrics: " + w_esc(", ".join(W_RULE_BEARING)) +
                 ".<br>Heart rate and HRV are context-only on purpose "
                 "" + W_DASH + " HR is unreliable during medication titration, "
                 "so aerobic intensity stays governed by the talk test."
'''

HOW_NEW = '''                 "Rule-bearing metrics: " + w_esc(", ".join(W_RULE_BEARING)) +
                 ".<br>Heart rate and HRV are context-only on purpose "
                 "" + W_DASH + " HR is unreliable during medication titration, "
                 "so aerobic intensity stays governed by the talk test. "
                 "Both are derived by the Watch overnight, which is why they "
                 "are labelled that way and appear on the Sleep card too."
                 "<br><br>Nothing on this page scores a night. There is no "
                 "readiness or recovery figure and no target, by design."
'''

# ---------------------------------------------------------------- edit 8
FEEDM_OLD = '''WATCH_METRICS = ("steps", "exercise_minutes", "resting_hr", "hrv_ms",
                 "distance_km", "flights", "active_energy_kcal",
                 "stand_hours", "walking_hr_avg", "spo2_pct",
                 "sleep_hours")
'''

FEEDM_NEW = '''WATCH_METRICS = ("steps", "exercise_minutes", "resting_hr", "hrv_ms",
                 "distance_km", "flights", "active_energy_kcal",
                 "stand_hours", "walking_hr_avg", "spo2_pct",
                 "sleep_hours",
                 # FITLOG_SLEEP_P2_PAGE. Temperature and respiratory rate
                 # travel to GutLog so a night can be put beside a day of
                 # feverishness. Context-only on both sides.
                 "resp_rate", "wrist_temp_c", "body_temp_c")
'''

# ---------------------------------------------------------------- edit 9
DAY_OLD = '''    return {"date": d, "metrics": metrics, "has_data": bool(metrics)}
'''

DAY_NEW = '''    # FITLOG_SLEEP_P2_PAGE. The whole night, not just its total, so the
    # other side can show clock times and stages without re-deriving them.
    # None when nothing was recorded -- never an empty shell that reads as
    # a night of no sleep.
    night = None
    try:
        night = hi.sleep_night(conn, d)
    except Exception:
        night = None
    return {"date": d, "metrics": metrics, "has_data": bool(metrics),
            "sleep": night}
'''

EDITS = [
    ("header marker", HDR_OLD, HDR_NEW),
    ("sleep card styles", CSS_OLD, CSS_NEW),
    ("name the overnight metrics", DAILY_OLD, DAILY_NEW),
    ("the strip says where they come from", STRIP_OLD, STRIP_NEW),
    ("the sleep card", CARD_OLD, CARD_NEW),
    ("wire it into /watch", WIRE_OLD, WIRE_NEW),
    ("how to read this page", HOW_OLD, HOW_NEW),
    ("feed carries temperature", FEEDM_OLD, FEEDM_NEW),
    ("feed carries the night", DAY_OLD, DAY_NEW),
]

DEFS = ("w_hm", "w_clock", "w_median", "w_nights", "w_stage_bar",
        "w_overnight_rows", "w_sleep_card")


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


def reverse(path, out_path):
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    bad = ["  " + label + ": " + str(src.count(new)) + " (need 1)"
           for label, old, new in EDITS if src.count(new) != 1]
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    out = src
    for label, old, new in EDITS:
        out = out.replace(new, old, 1)
    if MARKER in out or PREV not in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    for name in DEFS:
        if re.search(r"\ndef " + name + r"\(", out):
            print("REVERSE FAILED: " + name + " survived, nothing written.")
            return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed the pre-" + MARKER + " build -> " + out_path
          + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)

    print("=" * 66)
    print("FitLog " + MARKER + ": the night, on the page, never scored")
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
        print("FATAL: this file is not at v1.6.0.")
        return 1

    problems = ["  " + label + ": found " + str(src.count(old))
                + " times, need 1"
                for label, old, new in EDITS if src.count(old) != 1]
    print("anchors: " + str(len(EDITS) - len(problems)) + "/"
          + str(len(EDITS)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, old, new in EDITS:
        out = out.replace(old, new, 1)
    for name in DEFS:
        if not re.search(r"\ndef " + name + r"\(", out):
            print("DEFINITION CHECK FAILED: " + name)
            return 2
    # Rule 5b is GutLog's, but a stray Jinja token here would be just as
    # silent: this app renders through the same template machinery.
    for bad_tok in ("{{", "{%", "{#"):
        if bad_tok in CSS_NEW or bad_tok in CARD_NEW:
            print("TEMPLATE TOKEN CHECK FAILED: " + bad_tok)
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
    bak = args.file + ".bak-sleepp2page-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(EDITS)) + " edits")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.5.0 -> v1.6.0  ::  FITLOG_V160_DOWNDAYS -- the trend leaves down days out

GutLog now marks DOWN DAYS (Phase M): the recurring cluster of hip and thigh
ache, left abdominal pain, fatigue, feverishness and a broken night. On such
a day steps fall to near nothing, and an unmarked one reads on the Trend
card as non-adherence -- which would quietly corrupt the one question the
fitness plan exists to answer.

  * gutlog_downdays(since) reads GutLog's read-only /api/feed/downdays with
    the shared feed token, cached 60 s, never raises, and follows the live
    database like every other feed reader here (a scratch DB never reads it).
  * w_trend_card marks those bars (hatched, class "down", named in the
    title) and EXCLUDES them from mean / low / high, saying how many it left
    out. Not hidden: a down day is a fact about the day, and the bar stays
    on the chart so it can be seen for what it is.
  * /api/feed/watch: the day window is capped at 180 instead of 60, so
    GutLog's down-days view can put months of down days beside the day
    before each one; and sleep_hours joins the metrics it reports, so that
    view can show sleep from the Watch where GutLog's own log has none.

Requires v1.5.0 (FITLOG_V150_WATCHFEED). Anchor-verified, idempotent,
compile-checked, .bak before write, self-restoring, reversible. Python 3.9.
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
PREV = "FITLOG_V150_WATCHFEED"
MARKER = "FITLOG_V160_DOWNDAYS"

HDR_OLD = "FITLOG_V150_WATCHFEED -- FitLog v1.5.0 read-only watch feed for GutLog.\n"
HDR_NEW = HDR_OLD + "FITLOG_V160_DOWNDAYS -- FitLog v1.6.0 trend excludes GutLog's down days.\n"

READER_ANCHOR = (
    "def gutlog_days(category, since):\n"
    "    events, _err = gutlog_events(since)\n"
    "    cat = gutlog_catmap()\n"
    "    return set(e.get(\"day\") for e in events if e.get(\"day\") and cat(e.get(\"molecule\")) == category)\n"
)
READER_NEW = READER_ANCHOR + '''

# ---------------- down days (FITLOG_V160_DOWNDAYS) ----------------
_GD_CACHE = {}


def gutlog_downdays(since):
    """Days GutLog has marked as down days, on or after `since`. Same rules as
    the dose feed: follows the live database, 60 s cache, never raises, and
    an unreachable GutLog is an empty set plus a reason, not an exception."""
    import time
    import urllib.request
    if not gutlog_feed_enabled():
        return set(), "off"
    hit = _GD_CACHE.get(since)
    if hit and time.time() - hit[0] < 60:
        return hit[1], hit[2]
    days, err = set(), ""
    try:
        with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
        req = urllib.request.Request(
            GUTLOG_FEED_URL.rstrip("/") + "/api/feed/downdays?since=" + since,
            headers={"Authorization": "Bearer " + tok})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("ok"):
            days = set(d.get("day") for d in (data.get("days") or []) if d.get("day"))
        else:
            err = "GutLog returned an unexpected answer"
    except Exception as e:
        err = "GutLog not reachable (" + type(e).__name__ + ")"
    _GD_CACHE[since] = (time.time(), days, err)
    return days, err
'''

TREND_OLD_START = "def w_trend_card(rows):\n"
TREND_OLD_END = ("            '<div class=\"small\">Mean, low and high are over the days the Watch '\n"
                 "            \"actually reported, shown in the Days column.</div></div>\")\n")
TREND_NEW = '''def w_trend_card(rows):
    days = sorted(rows.keys())
    if not days:
        return ""
    # FITLOG_V160_DOWNDAYS -- a down day marked in GutLog is drawn, named and
    # left out of the summary. Reading it as a low-steps day would turn a bad
    # day into an adherence failure.
    down, derr = gutlog_downdays(days[0])
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
        isdown = d in down
        bars.append('<div class="wbar' + (' down' if isdown else '') + '" title="' + w_esc(d) + ": " +
                    w_fmt(v, 0) + ' steps' + (' \\u00b7 down day (GutLog)' if isdown else '') +
                    '"><i style="height:' + str(h) +
                    'px"></i><u>' + w_esc(d[8:]) + "</u></div>")
    kept = [d for d in days if d not in down]
    left_out = len([d for d in days if d in down])
    stats = []
    for key, label, unit, places in W_DAILY:
        got = [rows[d].get(key) for d in kept if rows[d].get(key) is not None]
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
    if left_out:
        note = ("Mean, low and high are over the days the Watch actually reported, "
                "shown in the Days column, leaving out " + str(left_out) +
                (" down day" if left_out == 1 else " down days") +
                " marked in GutLog (hatched above).")
    elif derr and derr != "off":
        note = ("Mean, low and high are over the days the Watch actually reported, "
                "shown in the Days column. Down days could not be read from GutLog "
                "just now, so none are left out.")
    else:
        note = ("Mean, low and high are over the days the Watch actually reported, "
                "shown in the Days column.")
    return ("<h2>Trend " + W_DASH + " last " + str(W_TREND_DAYS) + " days</h2>" +
            '<div class="card"><div class="small">Daily steps</div>' +
            '<div class="wbars">' + "".join(bars) + "</div>" +
            "<table><tr><th>Metric</th><th>Mean</th><th>Low</th><th>High</th>" +
            "<th>Days</th></tr>" + "".join(stats) + "</table>" +
            '<div class="small">' + note + "</div></div>")
'''

CSS_OLD = ".wbar u{font-size:9px;color:#8a9aa8;text-decoration:none;margin-top:2px}\n"
CSS_NEW = CSS_OLD + (
    ".wbar.down i{background:repeating-linear-gradient(45deg,#c0392b,#c0392b 2px,#f4f6f8 2px,#f4f6f8 5px);"
    "border:1px solid #c0392b}\n"
    ".wbar.down u{color:#c0392b;font-weight:700}\n"
)

CLAMP_OLD = "    days = max(1, min(60, days))\n    until = today()\n"
CLAMP_NEW = "    days = max(1, min(180, days))\n    until = today()\n"

METRICS_OLD = ('WATCH_METRICS = ("steps", "exercise_minutes", "resting_hr", "hrv_ms",\n'
               '                 "distance_km", "flights", "active_energy_kcal",\n'
               '                 "stand_hours", "walking_hr_avg", "spo2_pct")\n')
METRICS_NEW = ('WATCH_METRICS = ("steps", "exercise_minutes", "resting_hr", "hrv_ms",\n'
               '                 "distance_km", "flights", "active_energy_kcal",\n'
               '                 "stand_hours", "walking_hr_avg", "spo2_pct",\n'
               '                 "sleep_hours")\n')


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


def trend_slice(src):
    i = src.index(TREND_OLD_START)
    j = src.index(TREND_OLD_END, i) + len(TREND_OLD_END)
    return src[i:j]


def build_edits(src):
    return [
        ("header marker", HDR_OLD, HDR_NEW),
        ("down-day reader", READER_ANCHOR, READER_NEW),
        ("trend card", trend_slice(src), TREND_NEW),
        ("css", CSS_OLD, CSS_NEW),
        ("feed window", CLAMP_OLD, CLAMP_NEW),
        ("feed metrics", METRICS_OLD, METRICS_NEW),
    ]


def reverse(path, out_path):
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    # the trend card's old text is reconstructed from the v1.5.0 function
    old_trend = (
        "def w_trend_card(rows):\n"
        "    days = sorted(rows.keys())\n"
        "    if not days:\n"
        "        return \"\"\n"
        "    series = [(d, rows[d].get(\"steps\")) for d in days]\n"
        "    vals = [v for d, v in series if v is not None]\n"
        "    if not vals:\n"
        "        return \"\"\n"
        "    top = max(float(v) for v in vals)\n"
        "    bars = []\n"
        "    for d, v in series:\n"
        "        h = 0\n"
        "        if v is not None and top > 0:\n"
        "            h = int(round(58.0 * float(v) / top))\n"
        "            if h < 1:\n"
        "                h = 1\n"
        "        bars.append('<div class=\"wbar\" title=\"' + w_esc(d) + \": \" +\n"
        "                    w_fmt(v, 0) + ' steps\"><i style=\"height:' + str(h) +\n"
        "                    'px\"></i><u>' + w_esc(d[8:]) + \"</u></div>\")\n"
        "    stats = []\n"
        "    for key, label, unit, places in W_DAILY:\n"
        "        got = [rows[d].get(key) for d in days if rows[d].get(key) is not None]\n"
        "        if not got:\n"
        "            continue\n"
        "        nums = [float(x) for x in got]\n"
        "        mark = \"R\" if key in W_RULE_BEARING else \"C\"\n"
        "        cls = \"wr\" if key in W_RULE_BEARING else \"wc\"\n"
        "        suffix = (\" \" + unit) if unit else \"\"\n"
        "        stats.append('<tr><td><span class=\"' + cls + '\">' + mark + \"</span> \" +\n"
        "                     label + \"</td><td>\" +\n"
        "                     w_fmt(sum(nums) / len(nums), places) + suffix + \"</td><td>\" +\n"
        "                     w_fmt(min(nums), places) + suffix + \"</td><td>\" +\n"
        "                     w_fmt(max(nums), places) + suffix + \"</td><td>\" +\n"
        "                     str(len(nums)) + \"</td></tr>\")\n"
        "    return (\"<h2>Trend \" + W_DASH + \" last \" + str(W_TREND_DAYS) + \" days</h2>\" +\n"
        "            '<div class=\"card\"><div class=\"small\">Daily steps</div>' +\n"
        "            '<div class=\"wbars\">' + \"\".join(bars) + \"</div>\" +\n"
        "            \"<table><tr><th>Metric</th><th>Mean</th><th>Low</th><th>High</th>\" +\n"
        "            \"<th>Days</th></tr>\" + \"\".join(stats) + \"</table>\" +\n"
        + TREND_OLD_END)
    E = [("header marker", HDR_OLD, HDR_NEW),
         ("down-day reader", READER_ANCHOR, READER_NEW),
         ("trend card", old_trend, TREND_NEW),
         ("css", CSS_OLD, CSS_NEW),
         ("feed window", CLAMP_OLD, CLAMP_NEW),
         ("feed metrics", METRICS_OLD, METRICS_NEW)]
    bad = ["  " + l + ": " + str(src.count(n)) + " (need 1)" for l, a, n in E if src.count(n) != 1]
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    out = src
    for l, a, n in E:
        out = out.replace(n, a, 1)
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
    ap.add_argument("--reverse", metavar="OUT")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("FitLog v1.6.0: the trend leaves GutLog's down days out")
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
        print("FATAL: this file is not at v1.5.0.")
        return 1
    try:
        edits = build_edits(src)
    except ValueError as exc:
        print("FATAL: could not locate a block: " + str(exc))
        return 1
    problems = ["  " + l + ": found " + str(src.count(a)) + " times, need 1"
                for l, a, n in edits if src.count(a) != 1]
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        return 1
    if args.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, a, n in edits:
        out = out.replace(a, n, 1)
    for f in ("gutlog_downdays", "w_trend_card"):
        if not re.search(r"\ndef " + f + r"\(", out):
            print("DEFINITION CHECK FAILED: " + f)
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
    bak = args.file + ".bak-v160-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

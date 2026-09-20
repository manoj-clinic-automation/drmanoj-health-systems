#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog :: FITLOG_SLEEP_P2 -- carry the whole night, not one number.

Phase 1 made the night's SPAN and STAGES survive the trip into the database.
This carries what the Watch measured *during* it.

WRIST TEMPERATURE IS THE POINT OF THIS PATCH
--------------------------------------------
He has had subjective feverishness for over two years with, until 14-Sep, not
one documented temperature. His Watch has been measuring wrist temperature
every night and throwing it away, because nothing in METRIC_MAP claimed it. A
census of all 212 stored bodies on 2026-09-15 found no temperature metric of
any name -- Health Auto Export is not exporting it at all yet. So this patch
does two things and is honest about which is which:

  * it maps every spelling of the metric that Auto Export is known to use, so
    the value lands the moment the export is switched on, and
  * it leaves the existing `skipped_metrics` reporting alone, so if the export
    arrives under a name not mapped here it will show up by name in the ingest
    response instead of being silently dropped. CHECK THAT after enabling it.

`body_temperature` is mapped too. 14-Sep was the first documented temperature
in two years; a thermometer reading that reaches Health should not be the one
thing this record cannot hold.

Fahrenheit is converted. A canonical name ending `_c` that quietly held 98.6
would be worse than not storing it.

OVERNIGHT, NOT DAILY
--------------------
`overnight_metrics()` reads the samples whose own timestamps fall inside the
sleep span, so respiratory rate, blood oxygen, HRV and resting heart rate are
reported over the night rather than over the calendar day. Each comes back
with a `basis`: "night" when it was averaged over samples inside the span,
"day" when no sample carried a time inside it and the day's figure is shown
instead -- said out loud rather than passed off as an overnight figure. Apple
reports wrist temperature once a night at midnight, which is exactly that case.

  "Shown as inputs, not conclusions."

Nothing here scores a night, ranks one, or compares him with anybody else.

Requires FITLOG_SLEEP_P1. Anchor-verified, idempotent, compile-checked, .bak
before write, self-restoring, reversible. Python 3.9.

    python3 patch_fitlog_sleep_p2.py --check
    python3 patch_fitlog_sleep_p2.py --file /root/fitlog/health_ingest.py
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
TARGET = os.path.join(HERE, "health_ingest.py")
PREV = "FITLOG_SLEEP_P1"
MARKER = "FITLOG_SLEEP_P2"

# ---------------------------------------------------------------- edit 1
DOC_OLD = "Deterministic. No LLM in the path. Python 3.9 compatible.\n"

DOC_NEW = '''    S04 Overnight Basis  (FITLOG_SLEEP_P2)
      - a metric reported "overnight" is averaged over the samples whose
        own timestamps fall inside the sleep span, and says so
      - where no sample carried a time inside the span, the day's figure
        is shown and labelled a day figure, never passed off as one
        measured over the night
      - resting HR and HRV are derived overnight by the Watch: they are
        sleep figures as much as cardiac ones, and are shown as inputs,
        not conclusions

Deterministic. No LLM in the path. Python 3.9 compatible.
'''

# ---------------------------------------------------------------- edit 2
MAP_OLD = '''    "mindful_minutes": "mindful_min",
    "mindful_session": "mindful_min",
'''

MAP_NEW = '''    "mindful_minutes": "mindful_min",
    "mindful_session": "mindful_min",
    # FITLOG_SLEEP_P2. Wrist temperature is measured by the Watch every
    # night and was being dropped on the floor: no name here claimed it.
    # He has had subjective feverishness for over two years with, until
    # 2026-09-14, no documented temperature at all.
    #
    # As of the 2026-09-15 census of all 212 stored bodies, Auto Export is
    # not sending ANY of these yet -- it has to be switched on in the app.
    # Several spellings are claimed so the value lands whichever one it
    # arrives under; anything else still shows up by name under
    # skipped_metrics rather than being dropped silently.
    "apple_sleeping_wrist_temperature": "wrist_temp_c",
    "sleeping_wrist_temperature": "wrist_temp_c",
    "wrist_temperature": "wrist_temp_c",
    # A thermometer reading that reaches Health. Distinct from the wrist
    # sensor and never averaged together with it.
    "body_temperature": "body_temp_c",
    "basal_body_temperature": "basal_body_temp_c",
'''

# ---------------------------------------------------------------- edit 3
CONV_OLD = '''        if u in _M_UNITS:
            return value / 1000.0, "km"
    return value, unit
'''

CONV_NEW = '''        if u in _M_UNITS:
            return value / 1000.0, "km"
    if canonical in _TEMP_METRICS:
        if u in _F_UNITS:
            return (value - 32.0) * 5.0 / 9.0, "degC"
        if u in _C_UNITS:
            return value, "degC"
        # A temperature in a unit this cannot convert is DROPPED, not
        # guessed at and not stored under its own unit. A canonical name
        # ending _c quietly holding 98.6 is a lie in a health record, and
        # a unit column nobody reads is not a defence. The caller skips
        # a None.
        return None, unit
    return value, unit
'''

# ---------------------------------------------------------------- edit 4
UNITS_OLD = '''_KM_PER_MILE = 1.609344
'''

UNITS_NEW = '''_KM_PER_MILE = 1.609344

# FITLOG_SLEEP_P2. Temperature, converted rather than relabelled.
_TEMP_METRICS = ("wrist_temp_c", "body_temp_c", "basal_body_temp_c")
_C_UNITS = ("degc", "\\u00b0c", "c", "celsius", "centigrade")
_F_UNITS = ("degf", "\\u00b0f", "f", "fahrenheit")
'''

# ---------------------------------------------------------------- edit 5
CTX_OLD = '''CONTEXT_ONLY = (
    "resting_hr",
    "walking_hr_avg",
    "hrv_ms",
    "spo2_pct",
    "resp_rate",
)
'''

CTX_NEW = '''CONTEXT_ONLY = (
    "resting_hr",
    "walking_hr_avg",
    "hrv_ms",
    "spo2_pct",
    "resp_rate",
    # FITLOG_SLEEP_P2. Temperature is context, never a rule input. It is
    # here to be LOOKED AT beside a night and a symptom, not to make the
    # engine decide anything.
    "wrist_temp_c",
    "body_temp_c",
    "basal_body_temp_c",
)
'''

# ---------------------------------------------------------------- edit 6
MEAN_OLD = '''_APPLE_MEAN = (
    "resting_hr", "walking_hr_avg", "hrv_ms", "resp_rate",
    "spo2_pct", "weight_kg",
)
'''

MEAN_NEW = '''_APPLE_MEAN = (
    "resting_hr", "walking_hr_avg", "hrv_ms", "resp_rate",
    "spo2_pct", "weight_kg",
    # Levels, never totals. Adding two temperatures together would be
    # nonsense, and the default is the mean anyway -- they are named here
    # so that nobody has to check the default to know that.
    "wrist_temp_c", "body_temp_c", "basal_body_temp_c",
)
'''

# ---------------------------------------------------------------- edit 6b
DROP_OLD = '''            value, point_unit = _apple_convert(canonical, value, unit)
            samples.append((date, tod, canonical, value, point_unit))
'''

DROP_NEW = '''            value, point_unit = _apple_convert(canonical, value, unit)
            if value is None:
                # FITLOG_SLEEP_P2: a temperature in a unit the converter
                # does not know. Dropped rather than filed under a name
                # that promises Celsius.
                continue
            samples.append((date, tod, canonical, value, point_unit))
'''

# ---------------------------------------------------------------- edit 7
NIGHT_OLD = '''def sleep_night(conn, date, source="applewatch"):
'''

NIGHT_NEW = '''# Metrics worth reading over the NIGHT rather than over the day.
# Deliberately all context-only: this is a record to look at, not an
# input to any rule.
OVERNIGHT_METRICS = ("wrist_temp_c", "resp_rate", "spo2_pct", "hrv_ms",
                     "resting_hr")


def _hae_record_time(record_key):
    """
    The time of day out of a FEED_HAE record key.

    The key is built in parse_apple_records as
    feed|metric|grain|date|HH:MM:SS and this is the only place that reads
    it back; the two belong together. Valid for hae rows ONLY -- the HC
    and retired ios keys are a different shape, which is why the caller
    filters on feed. Returns "" rather than guessing.
    """
    parts = (record_key or "").split("|")
    if len(parts) != 5 or parts[0] != FEED_HAE:
        return ""
    tod = parts[4]
    if len(tod) == 8 and tod[2] == ":" and tod[5] == ":":
        return tod
    return ""


def overnight_metrics(conn, date, source="applewatch",
                      start_ts=None, end_ts=None, metrics=None):
    """
    What the Watch measured DURING the night. Rule S04 Overnight Basis.

    Shown as inputs, not conclusions. Nothing here is scored, ranked, or
    compared with a population figure.

    Each metric comes back with a `basis`:
      "night" -- the mean of the samples whose own timestamps fall inside
                 the sleep span, with n, min and max so the reader can see
                 how much it rests on
      "day"   -- no sample carried a time inside the span, so the day's
                 stored figure is shown AND SAID TO BE a day figure.
                 Apple reports wrist temperature once a night stamped at
                 midnight, which is exactly this case.

    A metric with neither is absent, not zero.
    """
    names = tuple(metrics) if metrics else OVERNIGHT_METRICS
    out = {}

    if start_ts and end_ts:
        days = sorted(set([start_ts[:10], end_ts[:10]]))
        marks = ",".join(["?"] * len(days))
        holes = ",".join(["?"] * len(names))
        rows = conn.execute(
            "SELECT record_key, date, metric, value, unit "
            "FROM health_hc_records "
            "WHERE source = ? AND feed = ? AND date IN (" + marks + ") "
            "AND metric IN (" + holes + ")",
            tuple([source, FEED_HAE] + days + list(names))).fetchall()
        bucket = {}
        for record_key, rdate, metric, value, unit in rows:
            if value is None:
                continue
            tod = _hae_record_time(record_key)
            if not tod:
                continue
            stamp = rdate + " " + tod
            if stamp < start_ts or stamp > end_ts:
                continue
            bucket.setdefault(metric, []).append((float(value), unit))
        for metric in names:
            got = bucket.get(metric)
            if not got:
                continue
            nums = [v for v, u in got]
            out[metric] = {
                "value": sum(nums) / len(nums),
                "n": len(nums),
                "min": min(nums),
                "max": max(nums),
                "unit": got[-1][1],
                "basis": "night",
            }

    missing = [m for m in names if m not in out]
    if missing:
        holes = ",".join(["?"] * len(missing))
        rows = conn.execute(
            "SELECT metric, value, unit FROM health_metrics "
            "WHERE date = ? AND source = ? AND metric IN (" + holes + ")",
            tuple([date, source] + list(missing))).fetchall()
        for metric, value, unit in rows:
            if value is None:
                continue
            out[metric] = {
                "value": float(value),
                "n": None,
                "min": None,
                "max": None,
                "unit": unit,
                "basis": "day",
            }
    return out


def sleep_night(conn, date, source="applewatch"):
'''

# ---------------------------------------------------------------- edit 8
RET_OLD = '''        "in_bed_h": (lambda m: None if m is None else m / 60.0)(
            _span_minutes(bed_start, bed_end)),
        "device": (blocks[0].get("device") or "") if blocks else "",
    }
'''

RET_NEW = '''        # Summed over the stretches, NOT the span from first to last.
        # See _in_bed_hours: spanning a break would count time he may
        # have spent out of bed as time in bed.
        "in_bed_h": _in_bed_hours(clusters),
        "device": (blocks[0].get("device") or "") if blocks else "",
        # S04. Measured during the night, each carrying the basis it was
        # worked out on. Never a verdict about the night.
        "overnight": overnight_metrics(conn, date, source, start_ts, end_ts),
    }
'''

# ---------------------------------------------------------------- edit 9
BED_OLD = '''def _sum_stage(clusters, key):
'''

BED_NEW = '''def _in_bed_hours(clusters):
    """
    Time in bed, summed over the stretches of the night.

    NOT the span from the first stretch to the last. A night that runs
    23:00 to 01:30, breaks, and resumes 02:10 to 05:00 SPANS six hours
    and holds five hours twenty of recorded bed time. Spanning the gap
    would put forty minutes he may well have spent out of bed into the
    figure, and "in bed" would then read longer than anything the Watch
    actually recorded -- on a record built for someone with insomnia,
    overstating time in bed is precisely the wrong way to be wrong.

    The in-bed stamps are preferred; a stretch that carries none falls
    back to its sleep span.
    """
    total = 0.0
    seen = False
    for cur in clusters:
        win = cur.get("winner") or {}
        mins = _span_minutes(win.get("in_bed_start"), win.get("in_bed_end"))
        if mins is None:
            mins = _span_minutes(win.get("start_ts"), win.get("end_ts"))
        if mins is None:
            continue
        total = total + mins
        seen = True
    if not seen:
        return None
    return total / 60.0


def _sum_stage(clusters, key):
'''

EDITS = [
    ("S04 in the rule block", DOC_OLD, DOC_NEW),
    ("temperature units", UNITS_OLD, UNITS_NEW),
    ("map wrist and body temperature", MAP_OLD, MAP_NEW),
    ("convert Fahrenheit", CONV_OLD, CONV_NEW),
    ("temperature is context-only", CTX_OLD, CTX_NEW),
    ("temperature is a level, not a total", MEAN_OLD, MEAN_NEW),
    ("drop a temperature that cannot be converted", DROP_OLD, DROP_NEW),
    ("overnight basis", NIGHT_OLD, NIGHT_NEW),
    ("time in bed sums the stretches", BED_OLD, BED_NEW),
    ("the night carries what was measured in it", RET_OLD, RET_NEW),
]

DEFS = ("_hae_record_time", "overnight_metrics", "_in_bed_hours")


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
    print("reconstructed " + PREV + " -> " + out_path
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
    print("FitLog " + MARKER + ": carry the whole night")
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
        print("FATAL: this file is not at " + PREV
              + ". Run patch_fitlog_sleep_p1.py first.")
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
    bak = args.file + ".bak-sleepp2-" + stamp
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

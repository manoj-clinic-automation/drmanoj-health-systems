#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: Health Auto Export units and same-day
aggregation.

Two defects in parse_payload(), both found on 2026-09-11 when the iPhone
switched from Health Webhook (ios snake_case) to Health Auto Export
(Apple data.metrics).

1. ENERGY ARRIVES IN KILOJOULES.
   Auto Export sends active_energy and basal_energy_burned with
   units "kJ". The canonical names end in _kcal, and the parser stored
   qty verbatim with the source unit string, so every energy figure was
   4.184x too large and labelled kJ. Confirmed against the ios feed:
   521.8159 kJ on 2026-09-09 divided by 4.184 is 124.717, exactly the
   move_kilocalories the ios activity ring reported for that day.

2. SAME-DAY SAMPLES OVERWROTE EACH OTHER.
   parse_payload emitted one row per sample and store() upserts on
   (date, metric, source), so the LAST sample won instead of the day's
   total. Replaying the real per-hour export (health_raw id 22) through
   the unpatched parser stored, for 2026-09-11:

       steps            4.61  instead of 4914
       stand_hours      1     instead of 19
       exercise_minutes 1     instead of 17
       active_energy    2.03  instead of 1845 kJ

   This did not surface only because a daily-rollup export arrived three
   minutes after the per-hour one and overwrote the damage. Any per-hour
   export not followed by a rollup destroys the day.

NOT a defect: stand_hours = 19 for 2026-09-11. apple_stand_hour carries
no stood/idle flag - every per-hour sample is qty 1 - and the 19 hours
05:00..23:00 are real. The ios ring's 12 was a 16:56 snapshot, which is
exactly hours 05..16.

Compile-checks before writing, .bak with a microsecond stamp, idempotent.
Python 3.9 compatible.

Usage:
    python3 patch_apple_aggregation.py --dry-run
    python3 patch_apple_aggregation.py [target]
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

# ---------------------------------------------------------------- anchors

A1_OLD = '''def parse_payload(payload):'''

A1_NEW = '''# Health Auto Export reports energy in kilojoules. Canonical names end
# in _kcal, so the value has to be converted, not just relabelled.
_KJ_PER_KCAL = 4.184

_ENERGY_METRICS = ("active_energy_kcal", "basal_energy_kcal")

_KJ_UNITS = ("kj", "kilojoule", "kilojoules")
_KCAL_UNITS = ("kcal", "cal", "calorie", "calories", "kilocalorie",
               "kilocalories")


def _apple_convert(canonical, value, unit):
    """Normalise a sample to the unit its canonical name promises."""
    u = str(unit or "").strip().lower()
    if canonical in _ENERGY_METRICS:
        if u in _KJ_UNITS:
            return value / _KJ_PER_KCAL, "kcal"
        if u in _KCAL_UNITS:
            return value, "kcal"
    return value, unit


# A day's samples must be combined, not overwritten. Counts and durations
# add up; levels are averaged. Every canonical name in METRIC_MAP appears
# in one of these two lists.
_APPLE_SUM = (
    "steps", "active_energy_kcal", "basal_energy_kcal",
    "exercise_minutes", "stand_hours", "flights",
    "mindful_min", "sleep_hours", "distance_km",
)

_APPLE_MEAN = (
    "resting_hr", "walking_hr_avg", "hrv_ms", "resp_rate",
    "spo2_pct", "weight_kg",
)


def _apple_aggregate(rows):
    """
    Collapse one calendar day's samples per metric.

    Anything not named in either list defaults to the mean, which fails
    far less loudly than a silent overwrite. Insertion order is kept so
    the result is deterministic.
    """
    order = []
    bucket = {}
    for date, metric, value, unit in rows:
        key = (date, metric)
        if key not in bucket:
            bucket[key] = []
            order.append(key)
        bucket[key].append((value, unit))

    out = []
    for key in order:
        date, metric = key
        samples = bucket[key]
        nums = [v for v, u in samples]
        unit = samples[-1][1]
        if metric in _APPLE_SUM:
            value = sum(nums)
        else:
            value = sum(nums) / len(nums)
        out.append((date, metric, value, unit))
    return out


def parse_payload(payload):'''

A2_OLD = '''            if value is None:
                continue
            metrics_out.append((date, canonical, value, unit))

    for wk in data.get("workouts") or []:'''

A2_NEW = '''            if value is None:
                continue
            # Bind to a fresh name: `unit` is the entry-level unit and is
            # reused by every point in this entry. Rebinding it converts
            # the first sample, then makes every later sample look like
            # it is already in kcal and pass through unconverted.
            value, point_unit = _apple_convert(canonical, value, unit)
            metrics_out.append((date, canonical, value, point_unit))

    # Without this the last sample of the day silently wins.
    metrics_out = _apple_aggregate(metrics_out)

    for wk in data.get("workouts") or []:'''

EDITS = [
    ("unit conversion + aggregation helpers", A1_OLD, A1_NEW),
    ("apply them in parse_payload", A2_OLD, A2_NEW),
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

    if "_apple_aggregate" in src:
        print("SKIP: aggregation fix already present.")
        return 0
    if "def parse_payload" not in src:
        print("FAIL: parse_payload not found; wrong target?")
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

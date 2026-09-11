#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: capture Apple activity ring GOALS.

The iOS activity_rings array carries both the achieved value and the goal
it was measured against:

    {"date": "2026-09-09", "stand_hours": 7, "stand_goal_hours": 12,
     "exercise_minutes": 0, "exercise_goal_minutes": 30,
     "move_kilocalories": 124.7, "move_goal_kilocalories": 300}

parse_ios_payload stored only the achieved side, so a rings view had
nothing to draw against. This stores the goals too.

Goals are deliberately NOT added to RULE_BEARING. A target is not a
measurement, and no F-rule may read one.

Compile-checks before writing, takes a .bak, idempotent. Python 3.9.

Usage:
    python3 patch_ios_ring_goals.py --dry-run
    python3 patch_ios_ring_goals.py
"""

import os
import shutil
import sys
from datetime import datetime

TARGET = "/root/fitlog/health_ingest.py"

G1_OLD = '''        for field, metric, unit in (
            ("stand_hours", "stand_hours", "count"),
            ("exercise_minutes", "exercise_minutes", "min"),
            ("move_kilocalories", "move_energy_kcal", "kcal"),
        ):'''

G1_NEW = '''        for field, metric, unit in (
            ("stand_hours", "stand_hours", "count"),
            ("exercise_minutes", "exercise_minutes", "min"),
            ("move_kilocalories", "move_energy_kcal", "kcal"),
            # The goal each ring is measured against. Context-only by
            # construction: a target is not a measurement, and no F-rule
            # may read one. Absent from RULE_BEARING on purpose.
            ("stand_goal_hours", "stand_goal_hours", "count"),
            ("exercise_goal_minutes", "exercise_goal_min", "min"),
            ("move_goal_kilocalories", "move_goal_kcal", "kcal"),
        ):'''

EDITS = [("ring goals", G1_OLD, G1_NEW)]


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

    if "move_goal_kcal" in src:
        print("SKIP: ring goals already captured.")
        return 0
    if "parse_ios_payload" not in src:
        print("FAIL: run patch_ios_payload.py first.")
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

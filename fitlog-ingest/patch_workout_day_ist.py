#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - the calendar day a workout is filed under, in health_ingest.py.

THE DEFECT
----------
_apple_samples() dates an Auto Export workout with

    date = _parse_date(start_raw)          # raw.strip()[:10]

which is the first ten characters of the stamp. For a local '+0530'
stamp that is right. For a UTC 'Z' stamp it is the UTC date, and IST is
UTC+5:30, so anything starting before 05:30 IST is filed to the PREVIOUS
day. His walks are 05:00-07:30, squarely inside that window.

The ios path never had this: parse_ios_payload() already converts with
_to_ist_date(). Only the Auto Export workout loop slices.

SCOPE - forward-looking only
----------------------------
Audited on the live database 2026-09-13 before writing this:

  * health_workouts holds 2 rows, both from the retired ios feed, both
    correctly dated. ZERO existing rows are mis-dated.
  * 8 Auto Export bodies are stored and NONE carries a workouts array,
    so no workout has ever come through the slicing path.

So this is a latent defect, not an active one, and there is no history
to repair. Nothing is rewritten. If Auto Export ever does start sending
workouts with UTC stamps, recompute_apple_daily.py is the place to offer
a backfill - deliberately not done here.

THE FIX
-------
Route the workout date through _to_ist_date(), which the ios path
already uses. It converts a 'Z' stamp to the IST calendar date and hands
anything else straight to _parse_date(), so a '+0530' stamp behaves
exactly as it does today. start_ts keeps the stamp as delivered - the
raw record of what arrived - and the read path converts for display via
_ist() in app.py.

Anchor-verified, compile-checked, timestamped .bak, idempotent.
Python 3.9 compatible.

Usage:
    FITLOG_INGEST=/root/fitlog/health_ingest.py python3 patch_workout_day_ist.py
    python3 patch_workout_day_ist.py --dry-run
"""
import os
import shutil
import sys
import time

TARGET = os.environ.get("FITLOG_INGEST", "/root/fitlog/health_ingest.py")

MARKER = "# Workouts are dated in IST"

ANCHOR = '''        start_raw = wk.get("start") or wk.get("startDate")
        date = _parse_date(start_raw)'''

NEW = '''        start_raw = wk.get("start") or wk.get("startDate")
        # Workouts are dated in IST, never by slicing the stamp. A 'Z'
        # stamp is UTC and IST is UTC+5:30, so [:10] files anything
        # starting before 05:30 IST under the previous day - which is
        # exactly when he walks. _to_ist_date converts a Z stamp and
        # passes a '+0530' stamp straight through to _parse_date, so the
        # Auto Export shape in use today is unaffected.
        date = _to_ist_date(start_raw)'''


def fail(msg):
    print("FAIL: " + msg)
    print("Nothing was written.")
    return 1


def main():
    dry = "--dry-run" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = positional[0] if positional else TARGET

    if not target.endswith(".py"):
        return fail("target must be a .py file, got: " + target)
    if not os.path.exists(target):
        return fail("not found: " + target)
    print("Target  : " + target)

    # newline="" on both read and write: without it, running this on
    # Windows against an LF file rewrites every line ending to CRLF. The
    # content would be identical and every test would still pass, but the
    # repo copy would no longer be byte-identical to the server's, which
    # is the invariant the sync rule in CLAUDE.md rests on.
    src = open(target, "r", encoding="utf-8", newline="").read()

    if MARKER in src:
        print("SKIP: already applied - nothing to do")
        return 0
    if "def _to_ist_date" not in src:
        return fail("_to_ist_date missing; run patch_ios_payload.py first")

    n = src.count(ANCHOR)
    if n != 1:
        return fail("anchor 'workout date' expected once, found " + str(n))
    print("  anchored: workout date")

    out = src.replace(ANCHOR, NEW, 1)

    try:
        compile(out, target, "exec")
    except SyntaxError as exc:
        return fail("patched source does not compile: " + str(exc))
    print("Compile : ok")

    if dry:
        print("Dry run. Nothing written.")
        return 0

    bak = target + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(target, bak)
    print("Backup  : " + bak)

    tmp = target + ".tmp"
    fh = open(tmp, "w", encoding="utf-8", newline="")
    fh.write(out)
    fh.close()
    os.replace(tmp, target)

    if MARKER not in open(target, "r", encoding="utf-8", newline="").read():
        shutil.copy2(bak, target)
        return fail("read-back mismatch. Rolled back from " + bak)
    print("Read-back: ok")
    print("PATCH OK")
    print("Rollback: cp " + bak + " " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

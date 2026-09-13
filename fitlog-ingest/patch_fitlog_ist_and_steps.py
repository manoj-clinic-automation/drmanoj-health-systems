#!/usr/bin/env python3
"""
FitLog - two fixes inside watch_activity() in /root/fitlog/app.py

1. Workout times were shown in UTC.
   The watch sends '2026-09-11T01:41:29.553Z'. The old code did [:19], which
   silently dropped the 'Z', and GutLog then displayed 01:41 as if it were
   local. That walk actually happened at 07:11 IST. Now converted properly.

2. Step counts: a day the watch was barely worn overrode a fuller count from
   the phone, because SOURCE_PRECEDENCE puts applewatch first for everything.
   On 12-Sep that showed 301 steps while healthconnect held 2,430. Steps are a
   coverage metric, not a sensor-quality one, so the largest count for the day
   now wins. Every other metric keeps the existing precedence untouched.

Safe by construction: every anchor must match exactly once, the result is
compiled before writing, a timestamped .bak is kept, and running it twice does
nothing. Nothing is written unless all three anchors match.
"""
import os
import shutil
import sys
import time

APP = os.environ.get("FITLOG_APP", "/root/fitlog/app.py")

HELPER_NAME = "def _ist("

ANCHOR_HELPER = "def watch_activity(day):"
HELPER = '''def _ist(ts):
    """Apple Health sends UTC with a trailing Z. The old code sliced the Z off
    and the time was then read as local. Convert to IST. Anything that is not
    a Z-stamp is passed through unchanged."""
    if not ts:
        return ""
    s = str(ts)
    if s.endswith("Z"):
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
            return (dt + timedelta(hours=5, minutes=30)).strftime(
                "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            pass
    return s[:19]


def watch_activity(day):'''

ANCHOR_TIMES = ('                "start": (r["start_ts"] or "")[:19], '
                '"end": (r["end_ts"] or "")[:19],')
NEW_TIMES = '                "start": _ist(r["start_ts"]), "end": _ist(r["end_ts"]),'

ANCHOR_STEPS = '''        m = hi.resolve_daily(conn, day)
        for k in ("steps", "exercise_minutes", "mindful_min"):
            if k in m and m[k]["value"] is not None:
                out[k] = round(float(m[k]["value"]), 1)
'''
NEW_STEPS = ANCHOR_STEPS + '''        # Steps are a coverage metric, not a sensor-quality one: a day the
        # watch was barely worn must not override a fuller count from the
        # phone. Every other metric keeps the source precedence above.
        try:
            mx = conn.execute(
                "SELECT MAX(value) FROM health_metrics "
                "WHERE date=? AND metric='steps'", (day,)).fetchone()[0]
            if mx is not None and float(mx) > float(out.get("steps") or 0):
                out["steps"] = round(float(mx), 1)
        except Exception:
            pass
'''


def fail(msg):
    print("FAIL: " + msg)
    print("Nothing was written.")
    sys.exit(1)


def once(src, anchor, label):
    n = src.count(anchor)
    if n != 1:
        fail("anchor '" + label + "' expected once, found " + str(n))
    print("anchor OK: " + label)


def main():
    if not os.path.exists(APP):
        fail("app not found at " + APP)
    # newline="" on every read and write below. Without it, running this on
    # Windows against an LF file rewrites every line ending to CRLF. The
    # content is identical and every test still passes, but the repo copy
    # stops being byte-identical to the server's -- the invariant the sync
    # rule in CLAUDE.md rests on.
    src = open(APP, "r", encoding="utf-8", newline="").read()

    done_helper = HELPER_NAME in src
    done_times = NEW_TIMES in src
    done_steps = "coverage metric, not a sensor-quality one" in src
    if done_helper and done_times and done_steps:
        print("already applied - nothing to do")
        return 0
    if done_helper or done_times or done_steps:
        fail("file is half-patched (helper=" + str(done_helper)
             + " times=" + str(done_times) + " steps=" + str(done_steps)
             + "). Restore the newest app.py.bak.* and re-run.")

    once(src, ANCHOR_HELPER, "watch_activity definition")
    once(src, ANCHOR_TIMES, "workout start/end slice")
    once(src, ANCHOR_STEPS, "resolve_daily metric loop")

    out = src.replace(ANCHOR_HELPER, HELPER)
    out = out.replace(ANCHOR_TIMES, NEW_TIMES)
    out = out.replace(ANCHOR_STEPS, NEW_STEPS)

    try:
        compile(out, APP, "exec")
    except SyntaxError as e:
        fail("patched source does not compile: " + str(e))
    print("compile OK")

    bak = APP + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(APP, bak)
    tmp = APP + ".tmp"
    f = open(tmp, "w", encoding="utf-8", newline="")
    f.write(out)
    f.close()
    os.replace(tmp, APP)
    print("written; rollback copy at " + bak)

    check = open(APP, "r", encoding="utf-8", newline="").read()
    if HELPER_NAME not in check or NEW_TIMES not in check:
        fail("read-back mismatch - restore with: cp " + bak + " " + APP)
    print("read-back OK")
    print("PATCH OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

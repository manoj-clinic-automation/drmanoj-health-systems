#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run a test suite as if the clock said something else.

Why this exists
---------------
2026-09-14: `gutlog/test_phase_i.py` and `fitlog/test_analgesic_mirror.py`
were written with literal times -- 08:00, 08:30, 18:00. GutLog correctly
refuses a time that has not come yet, so both suites were green after 18:00
and red at 03:48. A suite whose result depends on the hour it is run proves
nothing, and this diary is used at 5am by design.

The fix was to derive every time from the clock. This is what checks the fix:
it patches `datetime.datetime` **before** anything imports it, so the suite and
the application under test share one faked clock. Patching only the suite makes
the two disagree, and the resulting failures are artefacts of the harness
rather than findings -- that mistake cost a round of confusion, so it is
written down here.

Run a new suite through this at 00:00 and 23:58 before trusting it. The
midnight boundary is where derived times clamp and collide.

  python3 tools/RUN_AT_TIME.py 00 02 gutlog/test_phase_i.py gutlog/app.py
  python3 tools/RUN_AT_TIME.py 23 58 rxguard/test_reconcile.py gutlog/app.py

Only the local clock is faked. Nothing else is touched, and the suite still
builds its own scratch database. Python 3.9.
"""
import datetime as _dt
import importlib.util
import os
import sys

USAGE = "usage: RUN_AT_TIME.py HH MM path/to/suite.py [suite args...]"


def main():
    if len(sys.argv) < 4:
        print(USAGE)
        return 2
    try:
        hh, mm = int(sys.argv[1]), int(sys.argv[2])
    except ValueError:
        print(USAGE)
        return 2
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        print("HH must be 0-23 and MM 0-59.")
        return 2
    suite = sys.argv[3]
    if not os.path.exists(suite):
        print("no such suite: " + suite)
        return 2

    real = _dt.datetime

    class FakeDT(real):
        @classmethod
        def now(cls, tz=None):
            r = real.now(tz)
            return cls(r.year, r.month, r.day, hh, mm, r.second, r.microsecond)

    # Before the suite -- and therefore before the application it loads --
    # does `from datetime import datetime`. Both then get the same clock.
    _dt.datetime = FakeDT

    print("-- running " + os.path.basename(suite) + " as if it were "
          + ("%02d:%02d" % (hh, mm)) + " --")
    sys.path.insert(0, os.path.dirname(os.path.abspath(suite)))
    spec = importlib.util.spec_from_file_location("suite_under_fake_clock", suite)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.argv = [suite] + sys.argv[4:]
    if not hasattr(mod, "main"):
        print("that suite has no main(); run it directly.")
        return 2
    return mod.main()


if __name__ == "__main__":
    sys.exit(main())

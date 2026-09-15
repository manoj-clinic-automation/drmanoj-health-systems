#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.18.0 -- /api/watch sends no empty tiles (GUTLOG_V3180_HONEST).

The Watch card used to draw a label with nothing under it whenever a metric
came back empty: `load_hours` arrived n=0 with every field null, and
`resting_hr` / `hrv_ms` arrived with a six-day median and no reading today
and were drawn as an absence, throwing the median away. The decision about
what has something to say is made once, on the server, and this suite asserts
it there. How the card DRAWS what it is given is asserted in test_ui_now.py,
in a real browser.

Scratch database, no outward links, FitLog's feed supplied directly rather
than stood up -- the question here is what /api/watch does with an answer.
Every assertion is declared in new_assertions_v3180.json. Python 3.9.

  python3 test_watch_tiles.py [path/to/gutlog/app.py]
"""
import importlib.util
import os
import sys
import tempfile
from datetime import date, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 \
        else os.path.join(here, "app.py")
    work = tempfile.mkdtemp()
    os.environ.update(GUTLOG_DB=os.path.join(work, "g.db"),
                      GUTLOG_UPLOADS=os.path.join(work, "up"),
                      GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real",
                      GUTLOG_ICONS=os.path.dirname(app_path),
                      GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"))
    sys.path.insert(0, os.path.dirname(app_path))
    gm = load("gutlog_tiles", app_path)
    gc = gm.app.test_client()
    gc.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
            follow_redirects=True)

    T = date.today()
    D = dict((n, (T - timedelta(days=n)).isoformat()) for n in range(0, 20))

    # FitLog's answer, supplied straight to the reader rather than served:
    #   steps         a figure on every complete day  -> a tile with a value
    #   resting_hr    six older days, nothing today   -> a tile with a median
    #   hrv_ms        nothing at all                  -> no tile
    #   exercise_minutes  absent from the feed        -> no tile
    #   load_hours    no operating day logged here    -> no tile
    daily = []
    for n in range(13, -1, -1):
        mets = {}
        if n > 0:
            mets["steps"] = {"value": 4000 + n * 10, "source": "applewatch"}
        if 2 <= n <= 7:
            mets["resting_hr"] = {"value": 88 + (n % 3), "source": "applewatch"}
        daily.append({"date": D[n], "has_data": bool(mets), "metrics": mets})
    FEED = {"ok": True, "daily": daily, "epochs": [], "workouts": []}
    gm._link_get = lambda *a, **k: FEED

    def watch():
        r = gc.get("/api/watch?days=14")
        assert r.status_code == 200, "status " + str(r.status_code)
        return r.get_json()

    ctx = {}

    def t01():
        note = gm.WATCH_NOTE
        for word in ("rhythm", "Heart-rate", "wrist sensor", "accuracy"):
            assert word not in note, \
                "the footnote still argues from the rhythm: " + note
        assert note == ("Shown as inputs, not conclusions. No readiness, recovery "
                        "or fitness score is derived from them."), note
        return note
    check("01 the footnote gives the honest reason and claims nothing about rhythm", t01)

    def t02():
        j = watch()
        ctx["j"] = j
        s = j["strip"]
        empty = [k for k, t in s.items()
                 if t.get("value") is None and t.get("median") is None
                 and not t.get("today")]
        assert not empty, "tiles with no value and no median were sent: " + ", ".join(empty)
        return "%d tile(s) sent, none of them empty: %s" % (len(s), ", ".join(sorted(s)))
    check("02 a tile with no figure, no median and nothing running is not sent", t02)

    def t03():
        s = ctx["j"]["strip"]
        assert "load_hours" not in s, \
            "load_hours was sent with " + str(s.get("load_hours"))
        assert "hrv_ms" not in s, "hrv_ms was sent with " + str(s.get("hrv_ms"))
        assert "exercise_minutes" not in s, "exercise_minutes was sent empty"
        return "load_hours, hrv_ms and exercise_minutes all withheld"
    check("03 the three metrics with nothing behind them are withheld by name", t03)

    def t04():
        s = ctx["j"]["strip"]
        assert "resting_hr" in s, "the median-only metric was withheld too"
        rh = s["resting_hr"]
        assert rh["value"] is None and rh["day"] == "", \
            "resting_hr claims a reading today: " + str(rh)
        assert rh["median"] is not None and rh["n"] == 6, \
            "resting_hr carries no usable median: " + str(rh)
        return "resting_hr kept: median %s over %d days, no reading today" \
               % (rh["median"], rh["n"])
    check("04 a metric with a median but no reading today is still sent, with its window", t04)

    def t05():
        s = ctx["j"]["strip"]
        st = s.get("steps")
        assert st and st["value"] is not None, "the steps tile lost its figure"
        assert st["day"] and st["day"] < gm.today(), \
            "the steps headline is not the last complete day: " + str(st)
        assert ctx["j"]["row"] and len(ctx["j"]["row"]) == 14, "the chart lost its days"
        return "steps still headlines the last complete day and the chart still has 14 days"
    check("05 withholding empty tiles leaves the tiles that have something alone", t05)

    def t06():
        # The one that has to move. "load_hours appears once an operating day
        # is logged" was true of v3.17.0 as well -- it appeared whether or not
        # anything was logged -- so the pair is asserted, not the end state.
        before = "load_hours" in ctx["j"]["strip"]
        gc.post("/api/activity", data='{"kind":"ot_day","minutes":480,'
                                      '"day":"' + D[2] + '","atime":"08:00"}',
                content_type="application/json")
        j2 = watch()
        after = j2["strip"].get("load_hours")
        assert before is False, "load_hours was already there before anything was logged"
        assert after and after["value"] == 8.0, \
            "load_hours did not come back with the logged hours: " + str(after)
        return "absent with nothing logged, 8.0 h once an operating day exists"
    check("06 logging an operating day is what brings the load tile back", t06)

    def t07():
        # A guard over behaviour v3.17.0 already had, so it is controlled by
        # mutation rather than by version.
        r = gm.app.test_client().get("/api/watch?days=14")
        assert r.status_code in (302, 401), \
            "/api/watch answered %d without a login" % r.status_code
        return "/api/watch still needs a login"
    check("07 the watch feed still refuses to answer without a login", t07)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("GutLog v3.18.0 -- /api/watch sends no empty tiles")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

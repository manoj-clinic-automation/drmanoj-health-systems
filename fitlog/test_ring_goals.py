#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.7.0 -- the rings get their goals back (FITLOG_V170_RINGGOALS).

Throwaway database; renders the real pages.

  python3 test_ring_goals.py [path/to/fitlog/app.py]
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


here = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
tmp = tempfile.mkdtemp(prefix="fitlog_rg_")
DB = os.path.join(tmp, "t.db")
ENV = os.path.join(tmp, "e.env")
open(ENV, "w").write("FITLOG_INGEST_TOKEN=rg-smoke\nFITLOG_HC_TOKEN=rg-hc\n")
os.environ["FITLOG_DB"] = DB
os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ["FITLOG_GUTLOG_FEED"] = "0"
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(APP))
import migrate_health_ingest  # noqa: E402
sys.argv = ["m", DB]
migrate_health_ingest.main()
spec = importlib.util.spec_from_file_location("fitlog_rg", APP)
fl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fl)
with fl.app.app_context():
    fl.set_setting("password_hash", fl.sha("pw"))
    fl.set_setting("owner_hash", fl.sha("ok"))
fl.app.config["TESTING"] = True
cl = fl.app.test_client()
with cl.session_transaction() as s:
    s["auth"] = True

T = date.today().isoformat()
D = lambda n: (date.today() - timedelta(days=n)).isoformat()
NOW = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")


def wipe():
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM health_metrics WHERE source='applewatch'")
    c.commit()
    c.close()


def seed(day, pairs):
    c = sqlite3.connect(DB)
    for m, v, u in pairs:
        c.execute("INSERT INTO health_metrics (date, metric, value, unit, source, ingested_at) "
                  "VALUES (?,?,?,?,'applewatch',?)", (day, m, v, u, NOW))
    c.commit()
    c.close()


GOALS = [("move_goal_kcal", 300, "kcal"), ("exercise_goal_min", 30, "min"),
         ("stand_goal_hours", 12, "count")]
TODAY_NO_GOALS = [("steps", 2403, "count"), ("active_energy_kcal", 226, "kcal"),
                  ("exercise_minutes", 12, "min"), ("stand_hours", 10, "count")]


def home():
    return cl.get("/").get_data(as_text=True)


def watch():
    return cl.get("/watch").get_data(as_text=True)


def old_label(n):
    return (date.today() - timedelta(days=n)).strftime("%d %b").lstrip("0")


def t01():
    wipe()
    seed(D(7), GOALS)
    seed(T, TODAY_NO_GOALS)
    h = home()
    assert "226/300 kcal" in h, "move not drawn against the last goal"
    assert "12/30 min" in h and "10/12 h" in h, "exercise/stand not drawn against goals"
    assert 'stroke="#e0245e"' in h and 'stroke="#38d6e0"' in h, "no coloured arc"
    return "226/300, 12/30, 10/12 with coloured arcs"
check("01 today's strip fills against the last goals the Watch sent", t01)


def t02():
    h = home()
    assert "goals as last sent by the Watch on " + old_label(7) in h, "carried goal not dated"
    w = watch()
    assert "goals as last sent by the Watch on " + old_label(7) in w, "watch page not dated"
    return "named with the date they were sent"
check("02 a carried-forward goal is always named with its date", t02)


def t03():
    w = watch()
    assert "226 / 300 kcal" in w.replace("</span>", ""), "watch Move ring has no value"
    assert "75%" in w, "move percentage missing"
    assert "no goal on file" not in w, "still says no goal"
    return "Move 226 / 300 kcal, 75%"
check("03 the /watch rings fill; Move falls back to active energy", t03)


def t04():
    wipe()
    seed(D(9), [("move_goal_kcal", 500, "kcal")])
    seed(D(3), [("move_goal_kcal", 400, "kcal")])
    seed(D(-2), [("move_goal_kcal", 900, "kcal")])
    seed(T, TODAY_NO_GOALS)
    h = home()
    assert "226/400 kcal" in h, "not the newest goal on or before today"
    return "newest earlier goal wins; a future-dated one is ignored"
check("04 the newest goal on or before the day is used", t04)


def t05():
    wipe()
    seed(D(7), GOALS)
    seed(T, TODAY_NO_GOALS + [("move_goal_kcal", 350, "kcal")])
    h = home()
    assert "226/350 kcal" in h, "today's own goal not preferred"
    assert "12/30 min" in h, "other rings lost their carried goal"
    return "today's own goal first; the rest carried"
check("05 a goal sent today wins over an older one", t05)


def t06():
    wipe()
    seed(T, TODAY_NO_GOALS)
    h = home()
    w = watch()
    assert 'stroke="#e0245e"' not in h, "a ring coloured with no goal anywhere"
    assert "no goal on file" in w, "missing-goal wording lost"
    assert "goals as last sent" not in h + w, "a note with nothing carried"
    return "no goal ever sent: grey ring, says so"
check("06 with no goal ever sent, nothing is invented", t06)


def t07():
    import health_ingest
    for k in ("move_goal_kcal", "exercise_goal_min", "stand_goal_hours"):
        assert k not in health_ingest.RULE_BEARING, k + " became rule-bearing"
    return "goals stay context-only"
check("07 no rule reads a goal", t07)

ok = all(r[0] for r in RESULTS)
print("=" * 72)
print("FitLog v1.7.0 -- rings use the last goals the Watch sent")
print("=" * 72)
for good, name, msg in RESULTS:
    print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
print("-" * 72)
print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - smoke test for the live Apple Watch "Today so far" strip on the
main vitals page (/).

Renders the real page and asserts on the HTML. Covers the three things
that make this block trustworthy rather than decorative:

  * the empty day says so in words and never renders a zero,
  * the freshness stamp matches the actual latest ingest for today and
    turns stale when it is old,
  * the strip sits above the check-in form but stays small enough that
    logging is still the first thing you can do.

Runs against a throwaway temp database. Python 3.9 compatible.
"""
import os, re, sqlite3, sys, tempfile
from datetime import date, datetime, timedelta

PASS = 0; FAIL = 0; FAILURES = []
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   " + name)
    else:
        FAIL += 1; FAILURES.append(name); print("  FAIL " + name + ((" :: " + detail) if detail else ""))

tmp = tempfile.mkdtemp(prefix="fitlog_strip_")
DB = os.path.join(tmp, "t.db"); ENV = os.path.join(tmp, "e.env")
open(ENV, "w").write("FITLOG_INGEST_TOKEN=strip-smoke\nFITLOG_HC_TOKEN=hc-smoke\n")
os.environ["FITLOG_DB"] = DB; os.environ["FITLOG_INGEST_ENV"] = ENV
os.environ.pop("FITLOG_INGEST_TOKEN", None); os.environ.pop("FITLOG_HC_TOKEN", None)
os.environ["FITLOG_GUTLOG_FEED"] = "0"
sqlite3.connect(DB).close()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrate_health_ingest
sys.argv = ["m", DB]; migrate_health_ingest.main()

TODAY = date.today().isoformat()
YEST = (date.today() - timedelta(days=1)).isoformat()

def wipe_watch():
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM health_metrics WHERE source='applewatch'")
    c.commit(); c.close()

def seed(day, pairs, ingested_at):
    c = sqlite3.connect(DB)
    for metric, value, unit in pairs:
        c.execute("INSERT INTO health_metrics (date, metric, value, unit, source, ingested_at) "
                  "VALUES (?,?,?,?,'applewatch',?)", (day, metric, value, unit, ingested_at))
    c.commit(); c.close()

FULL = [("steps", 3874, "count"), ("exercise_minutes", 17, "min"),
        ("stand_hours", 12, "count"), ("active_energy_kcal", 319.157, "kcal"),
        ("move_energy_kcal", 319.157, "kcal"), ("move_goal_kcal", 300, "kcal"),
        ("exercise_goal_min", 30, "min"), ("stand_goal_hours", 12, "count"),
        ("resting_hr", 90, "count/min"), ("hrv_ms", 24.3764, "ms")]

def stamp_ago(minutes):
    return (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")

import app as fitlog
if not hasattr(fitlog, "watch_today_strip"):
    print("FAIL: strip not patched in. Run patch_watch_strip.py"); sys.exit(1)
with fitlog.app.app_context():
    fitlog.set_setting("password_hash", fitlog.sha("pw"))
    fitlog.set_setting("owner_hash", fitlog.sha("ok"))
fitlog.app.config["TESTING"] = True
cl = fitlog.app.test_client()

def counts():
    c = sqlite3.connect(DB)
    out = tuple(c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                for t in ("health_metrics", "health_workouts", "health_raw", "checkins"))
    c.close()
    return out

def home_html():
    return cl.get("/").get_data(as_text=True)

print("\n[1] auth gate")
r = cl.get("/")
check("unauthenticated is redirected", r.status_code == 302, str(r.status_code))
with cl.session_transaction() as s:
    s["auth"] = True

print("\n[2] no Watch data at all")
html = home_html()
check("home still renders", "<h1>Today" in html)
check("strip present", 'id="wstrip"' in html)
check("says nothing arrived", "Nothing has arrived from the Watch yet today" in html)
check("names the empty state", "No Watch data on file yet" in html)
check("no zero steps rendered", 'class="wt-big"' not in html, "must not show a 0 tile")
check("links to /watch", 'href="/watch"' in html)

print("\n[3] data on file, but none for today")
seed(YEST, [("steps", 2305, "count")], YEST + " 21:14:07")
html = home_html()
check("still says nothing today", "Nothing has arrived from the Watch yet today" in html)
check("names last reading date", YEST in html, "should cite the last reading")
check("names last reading time", "21:14" in html)
check("still no zero tile", 'class="wt-big"' not in html)

print("\n[4] today's data present")
wipe_watch()
FRESH = stamp_ago(12)
seed(TODAY, FULL, FRESH)
html = home_html()
check("steps shown", ">3874<" in html)
check("move ring vs goal", "319/300 kcal" in html)
check("exercise ring vs goal", "17/30 min" in html)
check("stand ring vs goal", "12/12 h" in html)
check("ring svg drawn", "stroke-dasharray=" in html)

print("\n[5] freshness stamp")
check("as of HH:MM IST present", "as of " + FRESH[11:16] + " IST" in html,
      "want " + FRESH[11:16])
check("relative age shown", "12 min ago" in html)
check("not marked stale", 'class="wfresh"' in html and "wfresh stale" not in html)

print("\n[6] stale stamp is marked")
wipe_watch()
OLD = stamp_ago(260)
seed(TODAY, FULL, OLD)
html = home_html()
check("stale class applied", 'class="wfresh stale"' in html)
check("stale stamp still shows the time", "as of " + OLD[11:16] + " IST" in html)
check("age in hours", "4 h ago" in html)

print("\n[7] context figures")
wipe_watch(); seed(TODAY, FULL, FRESH)
html = home_html()
check("resting hr shown", "Rest HR 90 bpm" in html)
check("hrv shown to 1dp", "HRV 24.4 ms" in html)
check("declared context only", "context only, no rule reads these" in html)

print("\n[8] rule-bearing badges come from the engine")
import health_ingest
check("steps is rule-bearing upstream", "steps" in health_ingest.RULE_BEARING)
check("move energy is not rule-bearing upstream",
      "move_energy_kcal" not in health_ingest.RULE_BEARING)
def strip_of(page_html):
    """The strip card only - up to wherever the next card begins."""
    i = page_html.index('<div class="card wstrip"')
    j = page_html.find('<div class="card"', i)
    if j < 0:
        j = page_html.index("Morning check-in")
    return page_html[i:j]

strip = strip_of(html)
check("steps tile carries R", re.search(r'wr">R</span> Steps', strip) is not None, strip[:200])
check("move tile carries C", re.search(r'wc">C</span> Move', strip) is not None)
check("exercise tile carries R", re.search(r'wr">R</span> Exercise', strip) is not None)
check("stand tile carries R", re.search(r'wr">R</span> Stand', strip) is not None)

print("\n[9] logging stays the primary action")
check("strip is above the check-in form",
      html.index('id="wstrip"') < html.index("Morning check-in"))
check("flags region is above the strip",
      html.index("<h1>Today") < html.index('id="wstrip"'))
# Compactness is about rendered height, so assert the SHAPE of the block
# rather than its byte count: one row of four tiles, one context line, and
# none of the heavy furniture that makes /watch a full page. The byte
# ceiling is only a regression tripwire.
check("exactly four tiles", strip.count('<div class="wt">') == 4,
      str(strip.count('<div class="wt">')) + " tiles")
check("strip ends before the check-in card",
      "Morning check-in" not in strip)
check("single tile row", strip.count('<div class="wts">') == 1)
check("at most one context line", strip.count('class="small wctx"') <= 1)
check("no table in the strip", "<table" not in strip)
check("no section heading in the strip", "<h2" not in strip)
check("no list in the strip", "<ul" not in strip)
check("no extra card nested", strip.count('class="card') == 1,
      str(strip.count('class="card')))
check("strip stays small", len(strip) < 2200, "strip is " + str(len(strip)) + " chars")
check("check-in form still rendered", 'action="/checkin"' in html)
check("mission button still there", "Get today" in html)

print("\n[10] read-only")
before = counts()
home_html(); home_html()
check("rendering writes nothing", counts() == before, str(before) + " -> " + str(counts()))

print("\n[11] /watch untouched")
r = cl.get("/watch")
w = r.get_data(as_text=True)
check("/watch still 200", r.status_code == 200, str(r.status_code))
for section in ("Activity rings", "Recent days", "Trend", "Workouts", "Sources"):
    check("/watch keeps " + section, section in w)
check("/watch has no strip block", 'id="wstrip"' not in w)

total = PASS + FAIL
print("\n" + "="*52); print("WATCH STRIP RESULT: " + str(PASS) + "/" + str(total) + " passed")
for f in FAILURES: print("  - " + f)
print("="*52)
sys.exit(0 if FAIL == 0 else 1)

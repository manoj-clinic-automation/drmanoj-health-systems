#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.1.0 -> v1.2.0  ::  activity feed for the GutLog Activity card

health_ingest.py
  - Apple Watch mindful minutes are kept (metric mindful_min, context only,
    never rule-bearing).
  - A workout the watch marks indoor is stored as "... (indoor)", so an
    indoor walk reads as treadmill and indoor cycling as static cycling.
  - classify_workout(): walk / treadmill / cycle_road / cycle_static /
    meditation / other.
app.py
  - /api/feed/activity?day=  (GutLog's feed token, read-only): steps,
    exercise minutes, mindful minutes and watch workouts for one day.
  - Home: an "Activity today" card -- watch workouts, steps, and what you
    tapped in GutLog (a tap the watch also recorded shows once).

The GutLog read follows the live database rule (FITLOG_GUTLOG_FEED=1/0).
Requires v1.1.0. Anchor-verified, idempotent, compile-checked, .bak before
write, self-restoring (both files or neither). Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys

DIR = "/root/fitlog"
MARKER = "FITLOG_V120_ACTIVITY"
PREV = "FITLOG_V110_GUTLOG_FEED"

HI_HELPERS = r'''# FITLOG_V120_ACTIVITY -- workout names and kinds
def _wname(wk):
    """Workout name; '(indoor)' added when the watch marks it indoor."""
    name = str(wk.get("name") or wk.get("workoutActivityType") or "unknown").strip()
    loc = str(wk.get("location") or "").strip().lower()
    indoor = wk.get("isIndoor") in (True, 1, "true", "True", "1") or loc == "indoor"
    if indoor and "indoor" not in name.lower():
        name += " (indoor)"
    return name


def classify_workout(wtype):
    """walk / treadmill / cycle_road / cycle_static / meditation / other."""
    w = (wtype or "").lower()
    indoor = "indoor" in w
    if "treadmill" in w:
        return "treadmill"
    if "cycl" in w or "bik" in w or "cycle" in w:
        return "cycle_static" if (indoor or "stationary" in w or "spin" in w) else "cycle_road"
    if "walk" in w or "hik" in w:
        return "treadmill" if indoor else "walk"
    if "mind" in w or "meditat" in w or "breath" in w:
        return "meditation"
    return "other"


'''

APP_PY = r'''# ---------------- Activity feed (FITLOG_V120_ACTIVITY) ----------------
# GutLog's Activity card reads the watch side from here; the Home card reads
# the tapped side from GutLog. Both use GutLog's read-only feed token.
_GA_CACHE = {}


def _gutlog_token():
    try:
        with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _feed_authorised(req):
    import hmac
    tok = _gutlog_token()
    h = req.headers.get("Authorization") or ""
    return bool(tok) and h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip(), tok)


def watch_activity(day):
    """Steps, exercise and mindful minutes, and workouts for one day, from
    the wearable tables (best source only). Never raises."""
    import health_ingest as hi
    out = {"steps": 0, "exercise_minutes": 0, "mindful_min": 0, "workouts": []}
    try:
        conn = hi._connect()
    except Exception:
        return out
    try:
        m = hi.resolve_daily(conn, day)
        for k in ("steps", "exercise_minutes", "mindful_min"):
            if k in m and m[k]["value"] is not None:
                out[k] = round(float(m[k]["value"]), 1)
        rows = conn.execute(
            "SELECT start_ts, end_ts, wtype, duration_s, distance_km, source FROM health_workouts "
            "WHERE date=? ORDER BY start_ts", (day,)).fetchall()
        srcs = [r["source"] for r in rows if r["source"] in hi.SOURCE_PRECEDENCE]
        best = min(srcs, key=hi.SOURCE_PRECEDENCE.index) if srcs else None
        for r in rows:
            if r["source"] != best:
                continue
            out["workouts"].append({
                "kind": hi.classify_workout(r["wtype"]), "wtype": r["wtype"] or "",
                "start": (r["start_ts"] or "")[:19], "end": (r["end_ts"] or "")[:19],
                "minutes": round((r["duration_s"] or 0) / 60.0, 1),
                "distance_km": round(r["distance_km"], 2) if r["distance_km"] else None})
    except Exception:
        pass
    finally:
        conn.close()
    return out


@app.route("/api/feed/activity")
def api_feed_activity():
    if not _feed_authorised(request):
        return {"ok": False, "error": "unauthorised"}, 401
    day = (request.args.get("day") or "")[:10]
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        day = today()
    d = watch_activity(day)
    d.update(ok=True, app="fitlog", day=day)
    return d


def gutlog_activities(day):
    """Activities tapped in GutLog on `day`: (rows, error). Cached 60 s."""
    import time
    import urllib.request
    if not gutlog_feed_enabled():
        return [], "off"
    hit = _GA_CACHE.get(day)
    if hit and time.time() - hit[0] < 60:
        return hit[1], hit[2]
    rows, err = [], ""
    try:
        req = urllib.request.Request(
            GUTLOG_FEED_URL.rstrip("/") + "/api/feed/activities?since=" + day,
            headers={"Authorization": "Bearer " + _gutlog_token()})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("ok"):
            rows = [a for a in data.get("activities") or [] if a.get("day") == day]
        else:
            err = "GutLog returned an unexpected answer"
    except Exception as e:
        err = "GutLog not reachable (" + type(e).__name__ + ")"
    _GA_CACHE[day] = (time.time(), rows, err)
    return rows, err


ACT_LABEL = {"walk": "Walk", "treadmill": "Treadmill", "cycle_road": "Cycling (road)",
             "cycle_static": "Cycling (static)", "meditation": "Meditation"}


def _mins(hm):
    try:
        h, m = hm[:5].split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def activity_card(day):
    import html as _html
    w = watch_activity(day)
    taps, err = gutlog_activities(day)
    taps = [dict(x) for x in taps]
    items = []
    for x in w["workouts"]:
        s0, e0 = _mins(x["start"][11:16]), _mins(x["end"][11:16])
        for t in taps:
            t0 = _mins(t.get("atime") or "")
            if (not t.get("_used") and t.get("kind") == x["kind"] and s0 is not None and t0 is not None
                    and s0 - 30 <= t0 <= (e0 if e0 is not None else s0) + 30):
                t["_used"] = True
                break
        bits = "⌚ " + ACT_LABEL.get(x["kind"], x["wtype"] or "Workout") + " " + str(int(round(x["minutes"]))) + " min"
        if x["distance_km"]:
            bits += " · " + str(round(x["distance_km"], 1)) + " km"
        items.append((x["start"][11:16], bits))
    for t in taps:
        if t.get("_used"):
            continue
        bits = ACT_LABEL.get(t.get("kind"), t.get("kind") or "") + " " + str(int(round(t.get("minutes") or 0))) + " min"
        if t.get("intensity"):
            bits += " · " + str(t["intensity"]).lower()
        items.append(((t.get("atime") or "")[:5], bits + " (GutLog)"))
    if w["mindful_min"] and not any("Meditation" in b for _, b in items):
        items.append(("", "⌚ Mindful minutes " + str(int(round(w["mindful_min"])))))
    items.sort(key=lambda i: (i[0] == "", i[0]))
    head = []
    if w["steps"]:
        head.append("{:,} steps".format(int(w["steps"])))
    if w["exercise_minutes"]:
        head.append(str(int(round(w["exercise_minutes"]))) + " exercise min")
    rows = "".join("<tr><td class=small>" + _html.escape(t) + "</td><td>" + _html.escape(b) + "</td></tr>"
                   for t, b in items)
    note = ""
    if err and err != "off":
        note = "<p class=small>" + _html.escape(err) + " — watch data only.</p>"
    if not rows and not head and not note:
        rows = "<tr><td class=small>Nothing yet today.</td></tr>"
    return ('<div class="card"><h2>Activity today</h2><p class=small>' + _html.escape(" · ".join(head)) +
            '</p><table>' + rows + '</table>' + note +
            '<p class=small><a href="https://health.dr-manoj.in/?open=act">Log an activity in GutLog →</a></p></div>')


'''


def edits_hi():
    E = []
    a = '    "blood_oxygen_saturation": "spo2_pct",\n'
    E.append(("mindful metric", a, a + '    "mindful_minutes": "mindful_min",\n    "mindful_session": "mindful_min",\n'))
    a = '            (wk.get("name") or wk.get("workoutActivityType") or "unknown").strip(),\n'
    E.append(("indoor name", a, "            _wname(wk),\n"))
    a = "def parse_payload(payload):\n"
    E.append(("helpers", a, HI_HELPERS + a))
    return E


def edits_app():
    E = []
    a = "FITLOG_V110_GUTLOG_FEED -- FitLog v1.1.0 reads doses from GutLog.\n"
    E.append(("marker", a, a + MARKER + " -- FitLog v1.2.0 activity feed + Home activity card.\n"))
    a = "def compute_flags():\n"
    E.append(("activity code", a, APP_PY + a))
    a = '        body += event_cards(t)\n    return page("Today", body, request.args.get("m", ""))\n'
    E.append(("home card", a, '        body += event_cards(t)\n    body += activity_card(t)\n'
                              '    return page("Today", body, request.args.get("m", ""))\n'))
    a = 'return {"app": "fitlog", "version": "1.0", "ok": True}'
    E.append(("version", a, 'return {"app": "fitlog", "version": "1.2.0", "ok": True}'))
    return E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DIR)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    fa = os.path.join(args.dir, "app.py")
    fh = os.path.join(args.dir, "health_ingest.py")
    print("=" * 60)
    print("FitLog activity feed -> v1.2.0")
    print("dir  : " + args.dir)
    print("=" * 60)
    for f in (fa, fh):
        if not os.path.exists(f):
            print("FATAL: not found: " + f)
            return 1
    sa = open(fa, encoding="utf-8").read()
    sh = open(fh, encoding="utf-8").read()
    if MARKER in sa and MARKER in sh:
        print("Already patched. Nothing to do.")
        return 0
    if MARKER in sa or MARKER in sh:
        print("FATAL: only one of the two files is patched -- unexpected state. Nothing written.")
        return 1
    if PREV not in sa:
        print("FATAL: app.py is not at v1.1.0. Apply that first.")
        return 1
    plan = [(fh, sh, edits_hi()), (fa, sa, edits_app())]
    bad = []
    n = 0
    for f, s, E in plan:
        for label, anchor, new in E:
            n += 1
            c = s.count(anchor)
            if c != 1:
                bad.append("  " + os.path.basename(f) + " / " + label + ": found " + str(c) + ", need 1")
    print("anchors: " + str(n - len(bad)) + "/" + str(n) + " matched")
    if bad:
        print("ANCHOR FAILURES:\n" + "\n".join(bad) + "\nRefusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0
    outs = []
    for f, s, E in plan:
        o = s
        for label, anchor, new in E:
            o = o.replace(anchor, new, 1)
        outs.append((f, o))
    import tempfile
    tmpd = tempfile.mkdtemp()
    try:
        for f, o in outs:
            t = os.path.join(tmpd, os.path.basename(f))
            open(t, "w", encoding="utf-8").write(o)
            py_compile.compile(t, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    baks = []
    for f, o in outs:
        b = f + ".bak-v120-" + stamp
        shutil.copy2(f, b)
        baks.append((f, b))
        print("backup : " + b)
    try:
        for f, o in outs:
            open(f, "w", encoding="utf-8").write(o)
            py_compile.compile(f, doraise=True)
    except Exception as exc:
        for f, b in baks:
            shutil.copy2(b, f)
        print("POST-WRITE FAILURE. Both files restored.\n" + str(exc))
        return 2
    print("applied: " + str(n) + " edits in 2 files")
    print("-" * 60)
    print("Next:  systemctl restart fitlog")
    print("Back:  " + " ; ".join("cp " + b + " " + f for f, b in baks))
    return 0


if __name__ == "__main__":
    sys.exit(main())

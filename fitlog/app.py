#!/usr/bin/env python3
"""
FITLOG_V110_GUTLOG_FEED -- FitLog v1.1.0 reads doses from GutLog.
FITLOG_V120_ACTIVITY -- FitLog v1.2.0 activity feed + Home activity card.
FitLog v1.0 — Personal physical capacity & recovery engine.
Dr. Manoj Agarwal | fit.dr-manoj.in | port 8040
Single-file Flask + SQLite. Deterministic rule engine (no LLM in decision path).
Knowledge: knowledge/rules.json, exercises.json, protocols.json, med_stack.json
"""
import os, json, sqlite3, hashlib, secrets, functools
from datetime import date, datetime, timedelta
from flask import Flask, request, redirect, session, g, url_for

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("FITLOG_DB", os.path.join(APP_DIR, "fitlog.db"))
KDIR = os.path.join(APP_DIR, "knowledge")

app = Flask(__name__)

# --- Phase 3.5: wearable ingest (Apple Watch / Health Connect) ---
from health_ingest import health_ingest_bp
app.register_blueprint(health_ingest_bp)
app.secret_key = os.environ.get("FITLOG_SECRET") or "fitlog-" + hashlib.sha256(DB_PATH.encode()).hexdigest()[:24]
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ---------------- knowledge ----------------
def load_kb(name):
    with open(os.path.join(KDIR, name), "r", encoding="utf-8") as f:
        return json.load(f)

RULES = load_kb("rules.json")
EXKB = load_kb("exercises.json")
PROTOKB = load_kb("protocols.json")
MEDSEED = load_kb("med_stack.json")
TH = RULES["thresholds"]
EX_BY_ID = {e["id"]: e for e in EXKB["exercises"]}

PAIN_SITES = ["Glute L", "Glute R", "Post hip", "Ant thigh", "Lumbar", "Other"]

# ---------------- db ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS checkins(
  id INTEGER PRIMARY KEY, date TEXT UNIQUE, sleep INTEGER, energy INTEGER,
  pain_max INTEGER, pain_sites TEXT, avail_min INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS exertion_events(
  id INTEGER PRIMARY KEY, type TEXT, date_start TEXT, date_end TEXT,
  intensity INTEGER, mode TEXT, leg_duration_hr REAL, notes TEXT,
  pre_status TEXT DEFAULT '', transit_status TEXT DEFAULT '', post_status TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, date TEXT UNIQUE, verdict TEXT, rules_fired TEXT,
  plan TEXT, status TEXT DEFAULT '', skip_reason TEXT DEFAULT '', minutes_actual INTEGER);
CREATE TABLE IF NOT EXISTS capacity_tests(
  id INTEGER PRIMARY KEY, date TEXT, bridge_sec INTEGER, plank_l INTEGER,
  plank_r INTEGER, walk_min INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS med_epochs(
  id INTEGER PRIMARY KEY, label TEXT, date_start TEXT, date_end TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS med_stack(
  id INTEGER PRIMARY KEY, kb_id TEXT, name TEXT, generic TEXT, strength TEXT,
  category TEXT, dose_options TEXT, route TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS analgesic_log(
  id INTEGER PRIMARY KEY, dt TEXT, med_id INTEGER, dose_label TEXT,
  context_event_id INTEGER, pain_at_time INTEGER, notes TEXT);
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    # seed med stack once
    n = con.execute("SELECT COUNT(*) FROM med_stack").fetchone()[0]
    if n == 0:
        for m in MEDSEED["meds"]:
            con.execute("INSERT INTO med_stack(kb_id,name,generic,strength,category,dose_options,route,active) VALUES(?,?,?,?,?,?,?,?)",
                        (m["id"], m["name"], m["generic"], m["strength"], m["category"], json.dumps(m["dose_options"]), m["route"], m["active"]))
    con.commit(); con.close()

init_db()

def setting(key, default=None):
    r = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def set_setting(key, value):
    db().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db().commit()

def sha(s): return hashlib.sha256(s.encode()).hexdigest()

# ---------------- auth ----------------
def login_required(f):
    @functools.wraps(f)
    def w(*a, **k):
        if not setting("password_hash"):
            return redirect(url_for("setup"))
        if not session.get("auth"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return w

# ---------------- helpers ----------------
def today(): return date.today().isoformat()

def events_on(day_iso):
    return db().execute(
        "SELECT * FROM exertion_events WHERE date_start <= ? AND date_end >= ? ORDER BY intensity DESC",
        (day_iso, day_iso)).fetchall()

def max_intensity(rows, types=None):
    m = 0
    for r in rows:
        if types and r["type"] not in types: continue
        m = max(m, r["intensity"] or 0)
    return m

def flight_leg_ge(rows, hrs):
    for r in rows:
        if r["type"] == "TRAVEL" and r["mode"] == "flight" and (r["leg_duration_hr"] or 0) >= hrs:
            return True
    return False

def in_travel(rows):
    return any(r["type"] == "TRAVEL" for r in rows)

# ---------------- engine ----------------
def evaluate(day_iso, sleep, energy, pain_max, avail_min):
    """Deterministic. Returns (verdict, fired[list], notes[list])."""
    d = date.fromisoformat(day_iso)
    y = (d - timedelta(days=1)).isoformat()
    t = (d + timedelta(days=1)).isoformat()
    ev_today, ev_y, ev_t = events_on(day_iso), events_on(y), events_on(t)
    fired, notes = [], []

    # F01 RECOVERY
    if pain_max >= TH["recovery_pain"] or (pain_max >= TH["recovery_pain_post_heavy"] and max_intensity(ev_y) >= 3):
        fired.append("F01"); return "RECOVERY", fired, notes
    # F02 RED
    if pain_max >= TH["red_pain"] or sleep <= TH["red_sleep"] or energy <= TH["red_energy"]:
        fired.append("F02"); verdict = "RED"
        if in_travel(ev_today): notes.append("Travel day: use portable RED set.")
        return verdict, fired, notes
    # F06/F10 travel
    if in_travel(ev_today):
        fired.append("F06")
        if flight_leg_ge(ev_today, TH["long_flight_hr"]):
            fired.append("F10"); notes.append("Long flight today — transit protocol replaces session.")
        return "TRAVEL", fired, notes
    # F07 DELOAD
    week_ago = (d - timedelta(days=7)).isoformat()
    r7 = db().execute("SELECT COALESCE(SUM(minutes_actual),0) m FROM sessions WHERE date>? AND date<=? AND status IN ('done','partial')",
                      (week_ago, day_iso)).fetchone()["m"]
    first = db().execute("SELECT MIN(date) d FROM sessions").fetchone()["d"]
    week_idx = ((d - date.fromisoformat(first)).days // 7 + 1) if first else 1
    deload = r7 > TH["deload_rolling7_min"] or (week_idx % TH["deload_week_interval"] == 0 and week_idx > 0)
    caps = []
    # F03
    if (TH["yellow_pain_lo"] <= pain_max <= TH["yellow_pain_hi"]) or sleep == TH["yellow_sleep"] or energy == TH["yellow_energy"]:
        caps.append("F03")
    # F04 (incl. yesterday long flight per F10)
    if max_intensity(ev_y) >= TH["exertion_cap_intensity"]:
        caps.append("F04")
    if flight_leg_ge(ev_y, TH["long_flight_hr"]) and "F04" not in caps:
        caps.append("F10")
    # F05
    if max_intensity(ev_t) >= TH["exertion_cap_intensity"]:
        caps.append("F05")
    if deload:
        fired.append("F07")
        fired.extend(caps)
        return ("YELLOW" if caps else "DELOAD"), fired, notes  # caps inside deload week -> yellow anyway
    if caps:
        fired.extend(caps); return "YELLOW", fired, notes
    fired.append("F09"); return "GREEN", fired, notes

def build_plan(verdict, day_iso, avail_min):
    """Deterministic session from templates; day-ordinal rotation, no randomness. F08 time-trim."""
    tpl = EXKB["templates"].get(verdict) or EXKB["templates"]["YELLOW"]
    ordn = date.fromisoformat(day_iso).toordinal()
    items = []
    if "checklist" in tpl:
        return {"type": "checklist", "items": tpl["checklist"]}
    if "fixed" in tpl:
        ids = tpl["fixed"][:tpl.get("pick", len(tpl["fixed"]))]
        for eid in ids:
            e = EX_BY_ID[eid]
            items.append({"id": eid, "name": e["name_en"], "dose": e.get("dose_r") or e.get("dose_y") or e.get("dose_g")})
        return {"type": "exercises", "items": items, "trimmed": False}
    tier = {"GREEN": "g", "YELLOW": "y", "DELOAD": "y"}.get(verdict, "y")
    used = set()
    for slot in tpl["slots"]:
        cats, count = slot[0].split("|"), slot[1]
        pool = [e for e in EXKB["exercises"] if e["category"] in cats and e["active"]
                and tier.upper() in e["tiers"] and e["id"] not in used]
        for i in range(count):
            if not pool: break
            pick = pool[(ordn + i) % len(pool)]
            pool.remove(pick); used.add(pick["id"])
            dose = pick.get("dose_" + tier) or pick.get("dose_g") or pick.get("dose_y")
            if verdict == "DELOAD": dose = dose + "  (deload: reduce ~30%)"
            items.append({"id": pick["id"], "name": pick["name_en"], "dose": dose})
    # F08 trim
    lo, hi = RULES["verdict_minutes"].get(verdict, [15, 20])
    trimmed = False
    if avail_min and avail_min < lo and len(items) > 3:
        items = items[:3]; trimmed = True
    return {"type": "exercises", "items": items, "trimmed": trimmed}

# ---------------- protocols ----------------
def proto_match(p, ev):
    c = p["condition"]
    if c == "always": return True
    ok = True
    for part in c.split("&"):
        part = part.strip()
        if part.startswith("mode="): ok &= (ev["mode"] == part.split("=")[1])
        elif part.startswith("leg>="): ok &= ((ev["leg_duration_hr"] or 0) >= float(part[5:]))
        elif part.startswith("leg<"): ok &= ((ev["leg_duration_hr"] or 0) < float(part[4:]))
        elif part.startswith("intensity>="): ok &= ((ev["intensity"] or 0) >= int(part[11:]))
    return ok

def protocols_for(ev):
    out = {}
    for phase in ("pre", "transit", "post"):
        best = None
        for p in PROTOKB["protocols"]:
            if p["event_type"] == ev["type"] and p["phase"] == phase and proto_match(p, ev):
                best = p  # later (more specific) entries override earlier generic ones
        if best: out[phase] = best
    return out

# ---------------- GutLog dose feed (FITLOG_V110_GUTLOG_FEED) ----------------
# Doses are logged in GutLog; W03 and the Meds page read them from its
# read-only feed and union them with FitLog's own log. Never raises.
GUTLOG_FEED_URL = os.environ.get("GUTLOG_FEED_URL", "http://127.0.0.1:8020")
GUTLOG_TOKEN_FILE = os.environ.get("GUTLOG_FEED_TOKEN_FILE", "/root/gutlog/feed.token")
_GL_CACHE = {}


def gutlog_feed_enabled():
    """Follows the live database: a scratch DB outside the app folder (the
    test suites) never reads real doses. FITLOG_GUTLOG_FEED=1/0 overrides."""
    v = os.environ.get("FITLOG_GUTLOG_FEED", "")
    if v in ("0", "1"):
        return v == "1"
    return os.path.dirname(os.path.abspath(DB_PATH)) == APP_DIR


def gutlog_events(since):
    """GutLog dose events on or after `since`: (events, error). Cached 60 s."""
    import time
    import urllib.request
    if not gutlog_feed_enabled():
        return [], "off"
    hit = _GL_CACHE.get(since)
    if hit and time.time() - hit[0] < 60:
        return hit[1], hit[2]
    events, err = [], ""
    try:
        with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
        req = urllib.request.Request(
            GUTLOG_FEED_URL.rstrip("/") + "/api/feed/doses?since=" + since,
            headers={"Authorization": "Bearer " + tok})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("ok"):
            events = data.get("events") or []
        else:
            err = "GutLog returned an unexpected answer"
    except Exception as e:
        err = "GutLog not reachable (" + type(e).__name__ + ")"
    _GL_CACHE[since] = (time.time(), events, err)
    return events, err


def gutlog_catmap():
    """molecule -> FitLog category, via the generic names in the stack."""
    stack = [((m["generic"] or "").lower(), m["category"]) for m in db().execute(
        "SELECT generic, category FROM med_stack WHERE active=1").fetchall()]
    memo = {}

    def cat(molecule):
        mol = (molecule or "").strip().lower()
        if not mol:
            return ""
        if mol not in memo:
            found = ""
            for gen, c in stack:
                if gen and mol in gen:
                    if c == "analgesic":
                        found = c
                        break
                    found = found or c
            memo[mol] = found
        return memo[mol]
    return cat


def gutlog_days(category, since):
    events, _err = gutlog_events(since)
    cat = gutlog_catmap()
    return set(e.get("day") for e in events if e.get("day") and cat(e.get("molecule")) == category)


# ---------------- warning flags ----------------
# ---------------- Activity feed (FITLOG_V120_ACTIVITY) ----------------
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


def compute_flags():
    flags = []
    rows = db().execute("SELECT date,pain_max,pain_sites FROM checkins ORDER BY date DESC LIMIT 30").fetchall()
    # W01: same site >= level for >= N consecutive check-ins (most recent streak)
    streak = {}
    for r in rows:  # newest first
        sites = json.loads(r["pain_sites"] or "[]")
        hit = set(sites) if r["pain_max"] >= TH["w01_pain_level"] else set()
        if not streak: streak = {s: 1 for s in hit}
        else:
            streak = {s: streak[s] + 1 for s in streak if s in hit}
        if not streak: break
    for s, n in (streak or {}).items():
        if n >= TH["w01_days"]:
            flags.append(("W01", f"{s} pain \u2265{TH['w01_pain_level']} for {n} consecutive check-ins \u2014 review"))
    # W02 adherence
    w = (date.today() - timedelta(days=TH["w02_window_days"])).isoformat()
    tot = db().execute("SELECT COUNT(*) c FROM sessions WHERE date>?", (w,)).fetchone()["c"]
    done = db().execute("SELECT COUNT(*) c FROM sessions WHERE date>? AND status IN ('done','partial')", (w,)).fetchone()["c"]
    if tot >= 7 and done * 100 < tot * TH["w02_adherence_pct"]:
        flags.append(("W02", f"Adherence {done}/{tot} in {TH['w02_window_days']}d \u2014 review friction, not willpower"))
    # W03 analgesic frequency
    w3 = (date.today() - timedelta(days=TH["w03_window_days"])).isoformat()
    local = set(r["d"] for r in db().execute("""SELECT DISTINCT substr(dt,1,10) d FROM analgesic_log l
                           JOIN med_stack m ON m.id=l.med_id
                           WHERE m.category='analgesic' AND substr(dt,1,10)>?""", (w3,)).fetchall())
    gl = gutlog_days("analgesic", (date.fromisoformat(w3) + timedelta(days=1)).isoformat())
    days = len(local | gl)
    src = " (incl. GutLog)" if gl - local else ""
    if days >= TH["w03_analgesic_days"]:
        flags.append(("W03", f"Analgesic use on {days} days in last {TH['w03_window_days']}{src} \u2014 minimum-effective-analgesia review"))
    return flags

# ---------------- html ----------------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,-apple-system,sans-serif;background:#f4f6f8;color:#1a2733;max-width:560px;margin:0 auto;padding:0 12px 60px}
a{color:#0b6e6e;text-decoration:none}.bar{display:flex;gap:8px;padding:10px 0;border-bottom:1px solid #dde3e8;margin-bottom:12px;font-size:14px;align-items:center}
.bar .app{padding:4px 10px;border-radius:14px;background:#e7eef2}.bar .cur{background:#0b6e6e;color:#fff}
h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:14px 0 6px}
.card{background:#fff;border:1px solid #dde3e8;border-radius:12px;padding:14px;margin:10px 0}
.verdict{font-size:26px;font-weight:700;padding:14px;border-radius:12px;text-align:center;margin:10px 0;color:#fff}
.v-GREEN{background:#1d8a4e}.v-YELLOW{background:#c98a00}.v-RED{background:#c0392b}.v-RECOVERY{background:#5b6ee1}.v-DELOAD{background:#6c7a89}.v-TRAVEL{background:#0b6e6e}
.small{font-size:13px;color:#5a6b78}.chip{display:inline-block;padding:6px 12px;margin:3px;border-radius:16px;border:1px solid #b9c6cf;background:#fff;cursor:pointer;font-size:14px}
input[type=checkbox].chk{display:none}input[type=checkbox].chk:checked+label{background:#0b6e6e;color:#fff;border-color:#0b6e6e}
input[type=radio].chk{display:none}input[type=radio].chk:checked+label{background:#0b6e6e;color:#fff;border-color:#0b6e6e}
input,select,textarea{font-size:16px;padding:8px;border:1px solid #b9c6cf;border-radius:8px;width:100%;margin:4px 0}
button,.btn{font-size:16px;padding:10px 16px;border:0;border-radius:10px;background:#0b6e6e;color:#fff;cursor:pointer;margin:6px 0;display:inline-block}
.btn2{background:#eef2f5;color:#1a2733;border:1px solid #b9c6cf}.btn-sm{font-size:13px;padding:6px 10px}
ul.plan li{padding:8px 4px;border-bottom:1px dashed #dde3e8}.flag{background:#fff4e5;border:1px solid #e8b26b;border-radius:10px;padding:10px;margin:8px 0;font-size:14px}
.rulechips span{display:inline-block;background:#eef2f5;border-radius:8px;padding:2px 8px;margin:2px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px 4px;border-bottom:1px solid #eee;text-align:left}
.msg{background:#e5f5ec;border:1px solid #79c99a;border-radius:8px;padding:8px;margin:8px 0;font-size:14px}
.wrings{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}
.wring{display:flex;align-items:center;gap:8px;min-width:158px}
.wring-l{font-size:13px;line-height:1.35}.wring-v{font-size:17px;font-weight:700}
.wscroll{overflow-x:auto}.wscroll table{min-width:520px}
.wr,.wc{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;border-radius:4px;font-size:10px;font-weight:700}
.wr{background:#0b6e6e;color:#fff}.wc{background:#e7eef2;color:#5a6b78}
.wbars{display:flex;align-items:flex-end;gap:2px;height:82px;overflow-x:auto;margin:6px 0}
.wbar{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;min-width:11px}
.wbar i{display:block;width:9px;background:#0b6e6e;border-radius:2px 2px 0 0}
.wbar u{font-size:9px;color:#8a9aa8;text-decoration:none;margin-top:2px}
.wstrip{padding:10px 12px}
.wstrip-h{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}
.wstrip-h b{font-size:14px}
.wfresh{font-size:11px;color:#5a6b78;margin-left:auto;white-space:nowrap}
.wfresh.stale{color:#a56a00;font-weight:600}
.wstrip-h a{font-size:11px;white-space:nowrap}
.wts{display:flex;gap:6px;align-items:flex-end}
.wt{flex:1;text-align:center;min-width:0}
.wt-n{font-size:12px;font-weight:700;margin-top:-3px}
.wt-big{font-size:23px;font-weight:700;line-height:46px}
.wt-l{font-size:10px;color:#5a6b78;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wt-l .wr,.wt-l .wc{width:12px;height:12px;line-height:12px;font-size:9px;border-radius:3px}
.wctx{margin-top:6px}.wctx .wc{width:12px;height:12px;line-height:12px;font-size:9px;border-radius:3px}
"""

def page(title, body, msg=""):
    nav = ""
    if setting("password_hash") and session.get("auth"):
        nav = """<div class="bar">
        <a class="app" href="https://rx.dr-manoj.in">RxGuard</a>
        <a class="app" href="https://health.dr-manoj.in">GutLog</a>
        <a class="app cur" href="/">FitLog</a>
        <span style="flex:1"></span>
        <a href="/watch">Watch</a> <a href="/events">Events</a> <a href="/meds">Meds</a> <a href="/tests">Tests</a> <a href="/history">Log</a>
        </div>"""
    m = f'<div class="msg">{msg}</div>' if msg else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} \u2014 FitLog</title>
<style>{CSS}</style></head><body>{nav}{m}{body}</body></html>"""

# ---------------- routes: auth ----------------
@app.route("/setup", methods=["GET", "POST"])
def setup():
    if setting("password_hash"):
        return redirect(url_for("login"))
    if request.method == "POST":
        pw, ok = request.form.get("pw", ""), request.form.get("okey", "")
        if len(pw) >= 6 and len(ok) >= 6:
            set_setting("password_hash", sha(pw)); set_setting("owner_hash", sha(ok))
            session["auth"] = True
            return redirect("/")
        return page("Setup", "<h1>Setup</h1><p>Both fields need \u22656 characters.</p><a href='/setup'>Back</a>")
    return page("Setup", """<h1>FitLog first-run setup</h1>
    <form method=post><label>Login password</label><input type=password name=pw>
    <label>Owner key (guards credential changes)</label><input type=password name=okey>
    <button>Save</button></form>""")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if sha(request.form.get("pw", "")) == setting("password_hash"):
            session["auth"] = True
            return redirect("/")
        return page("Login", "<h1>FitLog</h1><p>Wrong password.</p><a href='/login'>Retry</a>")
    return page("Login", "<h1>FitLog</h1><form method=post><input type=password name=pw placeholder='Password'><button>Login</button></form>")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------------- routes: home / checkin ----------------
@app.route("/")
@login_required
def home():
    t = today()
    ci = db().execute("SELECT * FROM checkins WHERE date=?", (t,)).fetchone()
    body = f"<h1>Today \u2014 {t}</h1>"
    for fid, txt in compute_flags():
        body += f'<div class="flag">\u2691 <b>{fid}</b> {txt}</div>'
    # Live Watch progress for today. Read-only, and deliberately below the
    # safety flags and above the check-in form: logging stays the primary
    # action on this page.
    body += watch_today_strip(t)
    if not ci:
        chips_pain = "".join(
            f'<input class="chk" type="checkbox" name="site" value="{s}" id="s{i}"><label class="chip" for="s{i}">{s}</label>'
            for i, s in enumerate(PAIN_SITES))
        def scale(name):
            return "".join(f'<input class="chk" type="radio" name="{name}" value="{v}" id="{name}{v}" {"checked" if v==3 else ""}><label class="chip" for="{name}{v}">{v}</label>' for v in range(1, 6))
        avail = "".join(f'<input class="chk" type="radio" name="avail" value="{v}" id="a{v}" {"checked" if v==20 else ""}><label class="chip" for="a{v}">{lbl}</label>'
                        for v, lbl in [(8, "<10"), (20, "15\u201320"), (35, "30\u201340")])
        pain = "".join(f'<input class="chk" type="radio" name="pain" value="{v}" id="p{v}" {"checked" if v==0 else ""}><label class="chip" for="p{v}">{v}</label>' for v in range(0, 11))
        body += f"""<div class="card"><h2>Morning check-in</h2><form method=post action="/checkin">
        <p class=small>Sleep</p>{scale('sleep')}
        <p class=small>Morning energy</p>{scale('energy')}
        <p class=small>Pain (max)</p>{pain}
        <p class=small>Pain sites (if any)</p>{chips_pain}
        <p class=small>Available time (min)</p>{avail}
        <button style="width:100%;margin-top:12px">Get today's mission</button></form></div>"""
        body += yesterday_quickadd(t)
    else:
        se = db().execute("SELECT * FROM sessions WHERE date=?", (t,)).fetchone()
        plan = json.loads(se["plan"]); fired = json.loads(se["rules_fired"])
        body += f'<div class="verdict v-{se["verdict"]}">{se["verdict"]}</div>'
        body += '<div class="rulechips">' + "".join(f"<span>{f}</span>" for f in fired) + "</div>"
        if plan["type"] == "checklist":
            body += '<div class="card"><h2>Recovery protocol</h2><ul class="plan">' + "".join(f"<li>{i}</li>" for i in plan["items"]) + "</ul></div>"
        else:
            trim = ' <span class="small">(time-trimmed \u2014 F08)</span>' if plan.get("trimmed") else ""
            body += f'<div class="card"><h2>Today\u2019s mission{trim}</h2><ul class="plan">' + \
                    "".join(f'<li><b>{i["name"]}</b><br><span class=small>{i["dose"]}</span></li>' for i in plan["items"]) + "</ul>"
            if not se["status"]:
                body += """<form method=post action="/session/close">
                <input type=hidden name=status value=done><button style="width:100%">START \u2192 mark DONE</button></form>
                <form method=post action="/session/close" style="display:flex;gap:6px">
                <button class="btn2" name=status value=partial style="flex:1">Partial</button>
                <button class="btn2" name=status value=skipped style="flex:1">Skip</button>
                <input name=skip_reason placeholder="reason (if skip)" style="flex:2"></form>"""
            else:
                body += f'<p class="msg">Session: <b>{se["status"]}</b></p>'
            body += "</div>"
        body += event_cards(t)
    body += activity_card(t)
    return page("Today", body, request.args.get("m", ""))

def yesterday_quickadd(t):
    y = (date.fromisoformat(t) - timedelta(days=1)).isoformat()
    if events_on(y):
        return ""
    return f"""<div class="card"><h2>Yesterday\u2019s exertion?</h2><p class=small>Nothing logged for {y}. One tap if there was:</p>
    <form method=post action="/events/quick"><input type=hidden name=date value="{y}">
    <div style="display:flex;gap:6px;flex-wrap:wrap">
    <button name=q value="OT,1" class="btn2 btn-sm">OT routine</button>
    <button name=q value="OT,2" class="btn2 btn-sm">OT long</button>
    <button name=q value="OT,3" class="btn2 btn-sm">OT heavy</button>
    <button name=q value="SOCIAL,2" class="btn2 btn-sm">Social</button>
    <button name=q value="none" class="btn2 btn-sm">Nothing</button></div></form></div>"""

def event_cards(t):
    out = ""
    for ev in events_on(t):
        pr = protocols_for(ev)
        for phase, p in pr.items():
            st = ev[phase + "_status"]
            steps = "".join(f"<li>{s}</li>" for s in p["steps"])
            btns = ""
            if not st:
                btns = f"""<form method=post action="/events/phase" style="display:flex;gap:6px">
                <input type=hidden name=eid value="{ev['id']}"><input type=hidden name=phase value="{phase}">
                <button name=st value=done class="btn-sm" style="flex:1">Done</button>
                <button name=st value=partial class="btn2 btn-sm" style="flex:1">Partial</button>
                <button name=st value=skipped class="btn2 btn-sm" style="flex:1">Skip</button></form>"""
            else:
                btns = f'<p class="msg">{phase.upper()}: {st}</p>'
            mid = (" \u00b7 " + (ev["mode"] or "")) if ev["type"] == "TRAVEL" and ev["mode"] else ""
            out += f'<div class="card"><h2>{p["title"]}</h2><p class=small>{ev["type"]} \u00b7 intensity {ev["intensity"]}{mid}</p><ul class="plan">{steps}</ul>{btns}</div>'
    return out

@app.route("/checkin", methods=["POST"])
@login_required
def checkin():
    t = today()
    sleep = int(request.form.get("sleep", 3)); energy = int(request.form.get("energy", 3))
    pain = int(request.form.get("pain", 0)); avail = int(request.form.get("avail", 20))
    sites = request.form.getlist("site")
    db().execute("INSERT OR REPLACE INTO checkins(date,sleep,energy,pain_max,pain_sites,avail_min,created_at) VALUES(?,?,?,?,?,?,?)",
                 (t, sleep, energy, pain, json.dumps(sites), avail, datetime.now().isoformat(timespec="seconds")))
    verdict, fired, notes = evaluate(t, sleep, energy, pain, avail)
    plan = build_plan(verdict, t, avail)
    if notes: plan["notes"] = notes
    db().execute("INSERT OR REPLACE INTO sessions(date,verdict,rules_fired,plan) VALUES(?,?,?,?)",
                 (t, verdict, json.dumps(fired), json.dumps(plan)))
    db().commit()
    return redirect("/")

@app.route("/session/close", methods=["POST"])
@login_required
def session_close():
    st = request.form.get("status", "done")
    reason = request.form.get("skip_reason", "")
    se = db().execute("SELECT plan,verdict FROM sessions WHERE date=?", (today(),)).fetchone()
    lo, hi = RULES["verdict_minutes"].get(se["verdict"], [15, 20]) if se else (15, 20)
    mins = {"done": hi, "partial": lo // 2 if lo > 1 else 5, "skipped": 0}[st] if st in ("done", "partial", "skipped") else 0
    if st == "partial": mins = lo
    db().execute("UPDATE sessions SET status=?,skip_reason=?,minutes_actual=? WHERE date=?", (st, reason, mins, today()))
    db().commit()
    return redirect("/?m=Logged.")

# ---------------- routes: events ----------------
@app.route("/events", methods=["GET", "POST"])
@login_required
def events():
    if request.method == "POST":
        typ = request.form["type"]
        ds = request.form["date_start"]; de = request.form.get("date_end") or ds
        if typ != "TRAVEL": de = ds
        db().execute("INSERT INTO exertion_events(type,date_start,date_end,intensity,mode,leg_duration_hr,notes) VALUES(?,?,?,?,?,?,?)",
                     (typ, ds, de, int(request.form.get("intensity", 2)),
                      request.form.get("mode", "") if typ == "TRAVEL" else "",
                      float(request.form.get("leg") or 0) if typ == "TRAVEL" else 0,
                      request.form.get("notes", "")))
        db().commit()
        return redirect("/events")
    rows = db().execute("SELECT * FROM exertion_events ORDER BY date_start DESC LIMIT 40").fetchall()
    lst = ""
    for r in rows:
        drange = r["date_start"] + (("\u2192" + r["date_end"]) if r["date_end"] != r["date_start"] else "")
        tmode = (" " + (r["mode"] or "")) if r["type"] == "TRAVEL" else ""
        leg = (" " + str(r["leg_duration_hr"]) + "h") if r["type"] == "TRAVEL" and r["leg_duration_hr"] else ""
        lst += f"""<tr><td>{drange}</td><td>{r['type']}{tmode}</td><td>I{r['intensity']}{leg}</td>
      <td><a href="/events/del/{r['id']}" onclick="return confirm('Delete event?')">\u2715</a></td></tr>"""
    body = f"""<h1>Exertion events</h1>
    <div class="card"><h2>Add (advance logging preferred)</h2><form method=post>
    <select name=type onchange="document.getElementById('trv').style.display=this.value=='TRAVEL'?'block':'none'">
      <option>OT</option><option>SOCIAL</option><option>TRAVEL</option></select>
    <label class=small>Start date</label><input type=date name=date_start value="{today()}">
    <div id="trv" style="display:none">
      <label class=small>End date (travel period)</label><input type=date name=date_end>
      <select name=mode><option value="car">Car</option><option value="flight">Flight</option></select>
      <label class=small>Longest leg duration (hours)</label><input type=number step=0.5 name=leg>
    </div>
    <label class=small>Intensity 1\u20133</label>
    <select name=intensity><option value=1>1 \u2014 routine</option><option value=2 selected>2 \u2014 long/complex</option><option value=3>3 \u2014 very heavy</option></select>
    <input name=notes placeholder="notes"><button>Add event</button></form></div>
    <div class="card"><table><tr><th>Date</th><th>Type</th><th>Load</th><th></th></tr>{lst}</table></div>"""
    return page("Events", body)

@app.route("/events/quick", methods=["POST"])
@login_required
def events_quick():
    q = request.form.get("q", "none"); d = request.form.get("date", today())
    if q != "none":
        typ, inten = q.split(",")
        db().execute("INSERT INTO exertion_events(type,date_start,date_end,intensity,mode,leg_duration_hr,notes) VALUES(?,?,?,?,'',0,'quick-add')",
                     (typ, d, d, int(inten)))
    else:
        db().execute("INSERT INTO exertion_events(type,date_start,date_end,intensity,mode,leg_duration_hr,notes) VALUES('NONE',?,?,0,'',0,'nothing')", (d, d))
    db().commit()
    return redirect("/")

@app.route("/events/phase", methods=["POST"])
@login_required
def events_phase():
    eid, phase, st = request.form["eid"], request.form["phase"], request.form["st"]
    if phase in ("pre", "transit", "post"):
        db().execute(f"UPDATE exertion_events SET {phase}_status=? WHERE id=?", (st, eid))
        db().commit()
    return redirect("/")

@app.route("/events/del/<int:eid>")
@login_required
def events_del(eid):
    db().execute("DELETE FROM exertion_events WHERE id=?", (eid,)); db().commit()
    return redirect("/events")

# ---------------- routes: meds ----------------
@app.route("/meds", methods=["GET", "POST"])
@login_required
def meds():
    if request.method == "POST":
        db().execute("INSERT INTO analgesic_log(dt,med_id,dose_label,context_event_id,pain_at_time,notes) VALUES(?,?,?,?,?,?)",
                     (datetime.now().isoformat(timespec="minutes"), int(request.form["med_id"]),
                      request.form["dose"], request.form.get("ctx") or None,
                      int(request.form["pain"]) if request.form.get("pain") else None,
                      request.form.get("notes", "")))
        db().commit()
        return redirect("/meds")
    stack = db().execute("SELECT * FROM med_stack WHERE active=1 ORDER BY category,name").fetchall()
    cards = ""
    for cat, label in (("analgesic", "Pain stack"), ("sleep", "Sleep stack")):
        rows = [m for m in stack if m["category"] == cat]
        inner = ""
        for m in rows:
            opts = json.loads(m["dose_options"])
            btns = "".join(f"""<form method=post style="display:inline"><input type=hidden name=med_id value="{m['id']}">
                    <input type=hidden name=dose value="{o}"><button class="btn2 btn-sm">{o}</button></form>""" for o in opts)
            inner += f"<div style='padding:8px 0;border-bottom:1px dashed #dde3e8'><b>{m['name']}</b> <span class=small>{m['strength']}</span><br>{btns}</div>"
        cards += f'<div class="card"><h2>{label}</h2>{inner}</div>'
    recent = db().execute("""SELECT l.dt,m.name,l.dose_label FROM analgesic_log l JOIN med_stack m ON m.id=l.med_id
                             ORDER BY l.dt DESC LIMIT 15""").fetchall()
    hist = "".join(f"<tr><td>{r['dt'][5:16].replace('T',' ')}</td><td>{r['name']}</td><td>{r['dose_label']}</td></tr>" for r in recent)
    w3 = (date.today() - timedelta(days=TH["w03_window_days"])).isoformat()
    import html as _html
    pairs = db().execute("""SELECT DISTINCT m.category c, substr(dt,1,10) d FROM analgesic_log l
                             JOIN med_stack m ON m.id=l.med_id WHERE substr(dt,1,10)>?""", (w3,)).fetchall()
    bycat = {}
    for r in pairs:
        bycat.setdefault(r["c"], set()).add(r["d"])
    gl_since = (date.fromisoformat(w3) + timedelta(days=1)).isoformat()
    gev, gerr = gutlog_events(gl_since)
    gcat = gutlog_catmap()
    grows = []
    for e in gev:
        c = gcat(e.get("molecule"))
        if c:
            bycat.setdefault(c, set()).add(e.get("day"))
            grows.append((e, c))
    counts = sorted(bycat.items())
    cs = " \u00b7 ".join(f"{c}: {len(d)} days/14" for c, d in counts) or "no use logged in 14 days"
    if gerr == "off":
        glcard = ""
    elif gerr:
        glcard = f'<div class="card"><h2>From GutLog</h2><p class=small>{_html.escape(gerr)} \u2014 counts above are FitLog only.</p></div>'
    else:
        gl_hist = "".join("<tr><td>" + _html.escape((e.get("day") or "")[5:] + " " + (e.get("time") or "")) +
                          "</td><td>" + _html.escape(e.get("name") or "") + "</td><td class=small>" + c + "</td></tr>"
                          for e, c in reversed(grows[-20:]))
        glcard = ('<div class="card"><h2>From GutLog, last 14 days</h2><p class=small>Doses logged in GutLog count here '
                  'automatically, matched by molecule to your stack.</p><table>' +
                  (gl_hist or "<tr><td class=small>No matching doses.</td></tr>") + "</table></div>")
    body = f"""<h1>Medication log</h1><p class=small>Tap drug dose = logged with timestamp. {cs}.
    <a href="/meds/manage">Manage stack \u2192</a></p>{cards}
    <div class="card"><h2>Recent</h2><table>{hist}</table></div>{glcard}"""
    return page("Meds", body)

@app.route("/meds/manage", methods=["GET", "POST"])
@login_required
def meds_manage():
    if request.method == "POST":
        opts = [o.strip() for o in request.form.get("dose_options", "").split(",") if o.strip()] or ["1 dose"]
        db().execute("INSERT INTO med_stack(kb_id,name,generic,strength,category,dose_options,route,active) VALUES('',?,?,?,?,?,?,1)",
                     (request.form["name"], request.form.get("generic", ""), request.form.get("strength", ""),
                      request.form.get("category", "analgesic"), json.dumps(opts), request.form.get("route", "oral")))
        db().commit()
        return redirect("/meds/manage")
    rows = db().execute("SELECT * FROM med_stack ORDER BY active DESC,category,name").fetchall()
    lst = "".join(f"""<tr><td>{m['name']}</td><td class=small>{m['strength']}</td><td class=small>{m['category']}</td>
        <td><a href="/meds/toggle/{m['id']}">{'disable' if m['active'] else 'enable'}</a></td></tr>""" for m in rows)
    body = f"""<h1>Manage stack</h1>
    <div class="card"><h2>Add medicine</h2><form method=post>
    <input name=name placeholder="Name (brand)" required><input name=generic placeholder="Generic (optional)">
    <input name=strength placeholder="Strength e.g. 50 mg">
    <select name=category><option value=analgesic>analgesic</option><option value=sleep>sleep</option><option value=other>other (PRN)</option></select>
    <input name=dose_options placeholder="Dose options, comma separated e.g. 1 tab, 2 tab">
    <select name=route><option>oral</option><option>topical</option><option>nasal</option><option>transdermal</option><option>other</option></select>
    <button>Add</button></form></div>
    <div class="card"><table><tr><th>Name</th><th>Strength</th><th>Cat</th><th></th></tr>{lst}</table></div>"""
    return page("Manage stack", body)

@app.route("/meds/toggle/<int:mid>")
@login_required
def meds_toggle(mid):
    db().execute("UPDATE med_stack SET active=1-active WHERE id=?", (mid,)); db().commit()
    return redirect("/meds/manage")

# ---------------- routes: capacity tests ----------------
@app.route("/tests", methods=["GET", "POST"])
@login_required
def tests():
    if request.method == "POST":
        db().execute("INSERT INTO capacity_tests(date,bridge_sec,plank_l,plank_r,walk_min,notes) VALUES(?,?,?,?,?,?)",
                     (request.form.get("date") or today(),
                      int(request.form.get("bridge") or 0), int(request.form.get("pl") or 0),
                      int(request.form.get("pr") or 0), int(request.form.get("walk") or 0),
                      request.form.get("notes", "")))
        db().commit()
        return redirect("/tests")
    rows = db().execute("SELECT * FROM capacity_tests ORDER BY date DESC LIMIT 20").fetchall()
    lst = "".join(f"<tr><td>{r['date']}</td><td>{r['bridge_sec']}s</td><td>{r['plank_l']}/{r['plank_r']}s</td><td>{r['walk_min']}m</td></tr>" for r in rows)
    due = ""
    if not rows:
        due = '<div class="flag">\u2691 Week-0 baseline not recorded yet \u2014 do it before the first training week.</div>'
    elif date.today().weekday() == 6 and rows[0]["date"] != today():
        due = '<div class="flag">\u2691 Sunday \u2014 weekly capacity re-test due.</div>'
    body = f"""<h1>Capacity tests</h1>{due}
    <div class="card"><h2>Record</h2><form method=post>
    <label class=small>Date</label><input type=date name=date value="{today()}">
    <label class=small>Bridge hold (sec)</label><input type=number name=bridge>
    <label class=small>Side plank L (sec)</label><input type=number name=pl>
    <label class=small>Side plank R (sec)</label><input type=number name=pr>
    <label class=small>Walk tolerance (min)</label><input type=number name=walk>
    <input name=notes placeholder="notes"><button>Save</button></form></div>
    <div class="card"><table><tr><th>Date</th><th>Bridge</th><th>Plank L/R</th><th>Walk</th></tr>{lst}</table></div>
    <div class="card"><h2>Medication epochs</h2>{epochs_block()}</div>"""
    return page("Tests", body)

def epochs_block():
    rows = db().execute("SELECT * FROM med_epochs ORDER BY date_start DESC").fetchall()
    lst = "".join(f"<tr><td>{r['label']}</td><td class=small>{r['date_start']} \u2192 {r['date_end'] or 'ongoing'}</td></tr>" for r in rows)
    return f"""<form method=post action="/epochs"><input name=label placeholder="e.g. medication taper" required>
    <div style="display:flex;gap:6px"><input type=date name=ds style="flex:1"><input type=date name=de style="flex:1"></div>
    <button class="btn-sm">Add epoch</button></form><table>{lst}</table>"""

@app.route("/epochs", methods=["POST"])
@login_required
def epochs():
    db().execute("INSERT INTO med_epochs(label,date_start,date_end,notes) VALUES(?,?,?,'')",
                 (request.form["label"], request.form.get("ds") or today(), request.form.get("de") or ""))
    db().commit()
    return redirect("/tests")

# ---------------- routes: history ----------------
@app.route("/history")
@login_required
def history():
    rows = db().execute("""SELECT c.date,c.sleep,c.energy,c.pain_max,s.verdict,s.status FROM checkins c
                           LEFT JOIN sessions s ON s.date=c.date ORDER BY c.date DESC LIMIT 30""").fetchall()
    lst = "".join(f"""<tr><td>{r['date'][5:]}</td><td>S{r['sleep']} E{r['energy']} P{r['pain_max']}</td>
        <td><b>{r['verdict'] or ''}</b></td><td>{r['status'] or ''}</td></tr>""" for r in rows)
    body = f"""<h1>Last 30 days</h1>
    <div class="card"><table><tr><th>Date</th><th>Check-in</th><th>Verdict</th><th>Session</th></tr>{lst}</table></div>
    <p class=small><a href="/logout">Logout</a></p>"""
    return page("History", body)

# ---------------- Apple Watch view (read-only) ----------------
# Renders what the ingest layer already stored. Writes nothing, decides
# nothing. RULE_BEARING is imported rather than restated so the page can
# never disagree with the engine about which metrics a rule may read.
from health_ingest import RULE_BEARING as W_RULE_BEARING

W_SRC = "applewatch"
W_TREND_DAYS = 28
W_RECENT_DAYS = 14
W_CIRC = 263.9  # 2 * pi * r, r = 42

# metric key, label, unit suffix, decimal places
W_DAILY = (
    ("steps", "Steps", "", 0),
    ("active_energy_kcal", "Active", "kcal", 0),
    ("exercise_minutes", "Exercise", "min", 0),
    ("stand_hours", "Stand", "h", 0),
    ("resting_hr", "Rest HR", "bpm", 0),
    ("hrv_ms", "HRV", "ms", 1),
    ("sleep_hours", "Sleep", "h", 1),
)

# label, achieved metric, goal metric, unit, ring colour
W_RINGS = (
    ("Move", "move_energy_kcal", "move_goal_kcal", "kcal", "#e0245e"),
    ("Exercise", "exercise_minutes", "exercise_goal_min", "min", "#9bd430"),
    ("Stand", "stand_hours", "stand_goal_hours", "h", "#38d6e0"),
)

W_DASH = "\u2014"


def w_esc(v):
    s = str(v)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;")


def w_fmt(v, places):
    if v is None:
        return W_DASH
    try:
        f = float(v)
    except (TypeError, ValueError):
        return W_DASH
    if places == 0:
        return str(int(round(f)))
    return str(round(f, places))


def w_metrics(days):
    """Return {date: {metric: value}} for the Watch over the last N days."""
    cut = (date.today() - timedelta(days=days - 1)).isoformat()
    out = {}
    rows = db().execute(
        "SELECT date, metric, value FROM health_metrics "
        "WHERE source = ? AND date >= ? ORDER BY date DESC",
        (W_SRC, cut)).fetchall()
    for r in rows:
        out.setdefault(r["date"], {})[r["metric"]] = r["value"]
    return out


def w_ring_svg(pct, colour, size=74):
    filled = W_CIRC * min(max(pct, 0.0), 1.0)
    rest = W_CIRC - filled
    out = ['<svg viewBox="0 0 100 100" width="' + str(size) + '" height="' +
           str(size) + '" aria-hidden="true">']
    out.append('<circle cx="50" cy="50" r="42" fill="none" stroke="#e7eef2" '
               'stroke-width="13"/>')
    if filled > 0:
        out.append('<circle cx="50" cy="50" r="42" fill="none" stroke="' + colour +
                   '" stroke-width="13" stroke-linecap="round" stroke-dasharray="' +
                   str(round(filled, 1)) + " " + str(round(rest, 1)) +
                   '" transform="rotate(-90 50 50)"/>')
    out.append("</svg>")
    return "".join(out)


def w_rings_card(rows):
    day = None
    for d in sorted(rows.keys(), reverse=True):
        for spec in W_RINGS:
            if rows[d].get(spec[1]) is not None:
                day = d
                break
        if day:
            break
    if day is None:
        return ""
    cells = []
    for label, got_key, goal_key, unit, colour in W_RINGS:
        got = rows[day].get(got_key)
        goal = rows[day].get(goal_key)
        pct = 0.0
        if got is not None and goal:
            pct = float(got) / float(goal)
        pct_txt = str(int(round(pct * 100))) + "%" if goal else "no goal on file"
        goal_txt = w_fmt(goal, 0) if goal else W_DASH
        cells.append('<div class="wring">' + w_ring_svg(pct, colour) +
                     '<div class="wring-l"><b>' + label + "</b><br>" +
                     '<span class="wring-v">' + w_fmt(got, 0) + "</span> / " +
                     goal_txt + " " + unit + '<br><span class="small">' +
                     pct_txt + "</span></div></div>")
    return ("<h2>Activity rings</h2>" +
            '<div class="card"><div class="small">' + w_esc(day) +
            ' &middot; source: Apple Watch</div><div class="wrings">' +
            "".join(cells) + "</div>" +
            '<div class="small">Goals are read from the Watch itself and are '
            "context-only: a target is not a measurement, and no rule reads "
            "one.</div></div>")


def w_recent_card(rows):
    days = sorted(rows.keys(), reverse=True)[:W_RECENT_DAYS]
    if not days:
        return ""
    head = ["<tr><th>Date</th>"]
    for key, label, unit, places in W_DAILY:
        mark = "R" if key in W_RULE_BEARING else "C"
        cls = "wr" if key in W_RULE_BEARING else "wc"
        unit_txt = '<br><span class="small">' + unit + "</span>" if unit else ""
        head.append('<th><span class="' + cls + '" title="' +
                    ("rule-bearing" if cls == "wr" else "context-only") +
                    '">' + mark + "</span> " + label + unit_txt + "</th>")
    head.append("</tr>")
    body = []
    for d in days:
        body.append("<tr><td>" + w_esc(d[5:]) + "</td>")
        for key, label, unit, places in W_DAILY:
            body.append("<td>" + w_fmt(rows[d].get(key), places) + "</td>")
        body.append("</tr>")
    return ("<h2>Recent days</h2>" +
            '<div class="card"><div class="wscroll"><table>' +
            "".join(head) + "".join(body) + "</table></div>" +
            '<div class="small">Every value shown is Apple Watch. A blank cell '
            "means the Watch sent nothing for that day, not a zero.</div></div>")


def w_trend_card(rows):
    days = sorted(rows.keys())
    if not days:
        return ""
    series = [(d, rows[d].get("steps")) for d in days]
    vals = [v for d, v in series if v is not None]
    if not vals:
        return ""
    top = max(float(v) for v in vals)
    bars = []
    for d, v in series:
        h = 0
        if v is not None and top > 0:
            h = int(round(58.0 * float(v) / top))
            if h < 1:
                h = 1
        bars.append('<div class="wbar" title="' + w_esc(d) + ": " +
                    w_fmt(v, 0) + ' steps"><i style="height:' + str(h) +
                    'px"></i><u>' + w_esc(d[8:]) + "</u></div>")
    stats = []
    for key, label, unit, places in W_DAILY:
        got = [rows[d].get(key) for d in days if rows[d].get(key) is not None]
        if not got:
            continue
        nums = [float(x) for x in got]
        mark = "R" if key in W_RULE_BEARING else "C"
        cls = "wr" if key in W_RULE_BEARING else "wc"
        suffix = (" " + unit) if unit else ""
        stats.append('<tr><td><span class="' + cls + '">' + mark + "</span> " +
                     label + "</td><td>" +
                     w_fmt(sum(nums) / len(nums), places) + suffix + "</td><td>" +
                     w_fmt(min(nums), places) + suffix + "</td><td>" +
                     w_fmt(max(nums), places) + suffix + "</td><td>" +
                     str(len(nums)) + "</td></tr>")
    return ("<h2>Trend " + W_DASH + " last " + str(W_TREND_DAYS) + " days</h2>" +
            '<div class="card"><div class="small">Daily steps</div>' +
            '<div class="wbars">' + "".join(bars) + "</div>" +
            "<table><tr><th>Metric</th><th>Mean</th><th>Low</th><th>High</th>" +
            "<th>Days</th></tr>" + "".join(stats) + "</table>" +
            '<div class="small">Mean, low and high are over the days the Watch '
            "actually reported, shown in the Days column.</div></div>")


def w_workouts_card():
    cut = (date.today() - timedelta(days=W_TREND_DAYS - 1)).isoformat()
    rows = db().execute(
        "SELECT date, start_ts, wtype, duration_s, distance_km, energy_kcal "
        "FROM health_workouts WHERE source = ? AND date >= ? "
        "ORDER BY start_ts DESC LIMIT 40", (W_SRC, cut)).fetchall()
    if not rows:
        return ("<h2>Workouts</h2>" +
                '<div class="card"><div class="small">No Watch workouts in the '
                "last " + str(W_TREND_DAYS) + " days.</div></div>")
    out = ["<tr><th>Date</th><th>Type</th><th>Time</th><th>Distance</th>"
           "<th>Energy</th></tr>"]
    for r in rows:
        secs = r["duration_s"]
        mins = W_DASH if secs is None else str(int(round(float(secs) / 60.0))) + " min"
        km = r["distance_km"]
        km_txt = W_DASH if km is None else str(round(float(km), 2)) + " km"
        kc = r["energy_kcal"]
        kc_txt = W_DASH if kc is None else str(int(round(float(kc)))) + " kcal"
        out.append("<tr><td>" + w_esc(r["date"][5:]) + "</td><td>" +
                   w_esc(str(r["wtype"] or "unknown").title()) + "</td><td>" +
                   mins + "</td><td>" + km_txt + "</td><td>" + kc_txt +
                   "</td></tr>")
    return ("<h2>Workouts</h2>" +
            '<div class="card"><table>' + "".join(out) + "</table>" +
            '<div class="small">Source: Apple Watch. Context-only ' + W_DASH +
            " no rule reads a workout.</div></div>")


def w_sources_card():
    rows = db().execute(
        "SELECT source, COUNT(*) AS n, MIN(date) AS d0, MAX(date) AS d1 "
        "FROM health_metrics GROUP BY source ORDER BY source").fetchall()
    out = ["<tr><th>Source</th><th>Rows</th><th>From</th><th>To</th>"
           "<th>State</th></tr>"]
    for r in rows:
        src = r["source"]
        if src == W_SRC:
            state = '<b style="color:#1d8a4e">active</b>'
        elif src == "healthconnect":
            state = '<span class="small">parked ' + W_DASH + " kept, not fed</span>"
        else:
            state = '<span class="small">manual entry</span>'
        out.append("<tr><td>" + w_esc(src) + "</td><td>" + str(r["n"]) +
                   "</td><td>" + w_esc(r["d0"] or W_DASH) + "</td><td>" +
                   w_esc(r["d1"] or W_DASH) + "</td><td>" + state + "</td></tr>")
    return ("<h2>Sources</h2>" +
            '<div class="card"><table>' + "".join(out) + "</table>" +
            '<div class="small">S01 source precedence: Apple Watch &gt; Health '
            "Connect &gt; manual, per metric per date. Values are never summed "
            "or averaged across sources " + W_DASH + " Health Connect can only "
            "fill a date the Watch left silent. Samsung rows are retained "
            "deliberately; nothing healthconnect has been deleted.</div></div>")


# label, value keys in preference order, goal key, unit, ring colour
W_STRIP = (
    ("Move", ("move_energy_kcal", "active_energy_kcal"), "move_goal_kcal",
     "kcal", "#e0245e"),
    ("Exercise", ("exercise_minutes",), "exercise_goal_min", "min", "#9bd430"),
    ("Stand", ("stand_hours",), "stand_goal_hours", "h", "#38d6e0"),
)

W_STALE_MINUTES = 180


def w_age_mins(stamp):
    """Minutes since an 'YYYY-MM-DD HH:MM:SS' local stamp, or None."""
    try:
        then = datetime.strptime(str(stamp), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    delta = (datetime.now() - then).total_seconds()
    if delta < 0:
        return 0
    return int(delta // 60)


def w_age_txt(mins):
    if mins is None:
        return ""
    if mins < 1:
        return "just now"
    if mins < 60:
        return str(mins) + " min ago"
    if mins < 1440:
        return str(mins // 60) + " h ago"
    return str(mins // 1440) + " d ago"


def watch_today_strip(t):
    """
    Compact live Watch progress for today, for the top of the vitals page.

    Read-only. No rule reads any value shown here; the R/C badges come
    from the engine's own RULE_BEARING, never from a local copy.
    """
    rows = db().execute(
        "SELECT metric, value, ingested_at FROM health_metrics "
        "WHERE source = ? AND date = ?", (W_SRC, t)).fetchall()
    vals = {}
    stamp = None
    for r in rows:
        vals[r["metric"]] = r["value"]
        if stamp is None or str(r["ingested_at"]) > stamp:
            stamp = str(r["ingested_at"])

    link = '<a href="/watch">All Watch data &rarr;</a>'
    shown = ("steps", "exercise_minutes", "stand_hours", "active_energy_kcal",
             "move_energy_kcal", "resting_hr", "hrv_ms")
    has_data = False
    for k in shown:
        if vals.get(k) is not None:
            has_data = True
            break

    if not has_data:
        last = db().execute(
            "SELECT date, MAX(ingested_at) AS at FROM health_metrics "
            "WHERE source = ? GROUP BY date ORDER BY date DESC LIMIT 1",
            (W_SRC,)).fetchone()
        if last and last["at"]:
            tail = ("Last reading was " + w_esc(last["date"]) + " at " +
                    w_esc(str(last["at"])[11:16]) + " IST.")
        else:
            tail = "No Watch data on file yet."
        return ('<div class="card wstrip" id="wstrip">'
                '<div class="wstrip-h"><b>\u231a Today so far</b>' + link +
                '</div><div class="small">Nothing has arrived from the Watch '
                "yet today. " + tail + "</div></div>")

    mins = w_age_mins(stamp)
    age = w_age_txt(mins)
    cls = "wfresh stale" if (mins is not None and mins >= W_STALE_MINUTES) else "wfresh"
    hhmm = w_esc(str(stamp)[11:16]) if stamp else "\u2014"
    fresh = ('<span class="' + cls + '">as of ' + hhmm + " IST" +
             ((" \u00b7 " + age) if age else "") + "</span>")

    steps_mark = "wr" if "steps" in W_RULE_BEARING else "wc"
    tiles = ['<div class="wt"><div class="wt-big">' + w_fmt(vals.get("steps"), 0) +
             '</div><div class="wt-l"><span class="' + steps_mark + '">' +
             ("R" if steps_mark == "wr" else "C") + "</span> Steps</div></div>"]

    for label, keys, goal_key, unit, colour in W_STRIP:
        got = None
        for k in keys:
            if vals.get(k) is not None:
                got = vals.get(k)
                break
        goal = vals.get(goal_key)
        pct = 0.0
        if got is not None and goal:
            pct = float(got) / float(goal)
        cap = w_fmt(got, 0)
        if goal:
            cap = cap + "/" + w_fmt(goal, 0)
        rb = keys[0] in W_RULE_BEARING
        badge = "wr" if rb else "wc"
        tiles.append('<div class="wt">' + w_ring_svg(pct, colour, 46) +
                     '<div class="wt-n">' + cap + " " + unit + "</div>" +
                     '<div class="wt-l"><span class="' + badge + '">' +
                     ("R" if rb else "C") + "</span> " + label + "</div></div>")

    ctx = []
    if vals.get("resting_hr") is not None:
        ctx.append("Rest HR " + w_fmt(vals.get("resting_hr"), 0) + " bpm")
    if vals.get("hrv_ms") is not None:
        ctx.append("HRV " + w_fmt(vals.get("hrv_ms"), 1) + " ms")
    ctx_html = ""
    if ctx:
        ctx_html = ('<div class="small wctx"><span class="wc">C</span> ' +
                    " \u00b7 ".join(ctx) + " \u2014 context only, no rule "
                    "reads these</div>")

    return ('<div class="card wstrip" id="wstrip"><div class="wstrip-h">'
            '<b>\u231a Today so far</b>' + fresh + link + "</div>" +
            '<div class="wts">' + "".join(tiles) + "</div>" + ctx_html +
            "</div>")


@app.route("/watch")
@login_required
def watch_view():
    rows = w_metrics(W_TREND_DAYS)
    if not rows:
        body = ("<h1>Apple Watch</h1>" +
                '<div class="card">No Watch data in the last ' +
                str(W_TREND_DAYS) + " days. The feed posts to "
                "<code>/api/ingest?source=applewatch</code> with the bearer "
                "token.</div>" + w_sources_card())
        return page("Watch", body)
    parts = ["<h1>Apple Watch</h1>"]
    parts.append(w_rings_card(rows))
    parts.append(w_recent_card(rows))
    parts.append(w_trend_card(rows))
    parts.append(w_workouts_card())
    parts.append(w_sources_card())
    parts.append('<div class="card"><b>How to read this page</b>'
                 '<div class="small"><span class="wr">R</span> rule-bearing: '
                 "the rule engine is permitted to read this metric. "
                 '<span class="wc">C</span> context-only: shown so you can '
                 "interpret a day, never read by a rule.<br><br>"
                 "Rule-bearing metrics: " + w_esc(", ".join(W_RULE_BEARING)) +
                 ".<br>Heart rate and HRV are context-only on purpose "
                 "" + W_DASH + " HR is unreliable during medication titration, "
                 "so aerobic intensity stays governed by the talk test."
                 "<br><br>This page is read-only. No F-rule consumes an "
                 "ingested metric; verdict logic is unchanged by anything "
                 "shown here.</div></div>")
    parts.append('<p class=small><a href="/">Home</a> &middot; '
                 '<a href="/history">Log</a></p>')
    return page("Watch", "".join(parts))


@app.route("/health")
def health():
    return {"app": "fitlog", "version": "1.3.1", "ok": True}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8040, debug=False)

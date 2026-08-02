#!/usr/bin/env python3
"""
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

# ---------------- warning flags ----------------
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
    days = db().execute("""SELECT COUNT(DISTINCT substr(dt,1,10)) c FROM analgesic_log l
                           JOIN med_stack m ON m.id=l.med_id
                           WHERE m.category='analgesic' AND substr(dt,1,10)>?""", (w3,)).fetchone()["c"]
    if days >= TH["w03_analgesic_days"]:
        flags.append(("W03", f"Analgesic use on {days} days in last {TH['w03_window_days']} \u2014 minimum-effective-analgesia review"))
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
"""

def page(title, body, msg=""):
    nav = ""
    if setting("password_hash") and session.get("auth"):
        nav = """<div class="bar">
        <a class="app" href="https://rx.dr-manoj.in">RxGuard</a>
        <a class="app" href="https://health.dr-manoj.in">GutLog</a>
        <a class="app cur" href="/">FitLog</a>
        <span style="flex:1"></span>
        <a href="/events">Events</a> <a href="/meds">Meds</a> <a href="/tests">Tests</a> <a href="/history">Log</a>
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
    counts = db().execute("""SELECT m.category,COUNT(DISTINCT substr(dt,1,10)) d FROM analgesic_log l
                             JOIN med_stack m ON m.id=l.med_id WHERE substr(dt,1,10)>? GROUP BY m.category""", (w3,)).fetchall()
    cs = " \u00b7 ".join(f"{r['category']}: {r['d']} days/14" for r in counts) or "no use logged in 14 days"
    body = f"""<h1>Medication log</h1><p class=small>Tap drug dose = logged with timestamp. {cs}.
    <a href="/meds/manage">Manage stack \u2192</a></p>{cards}
    <div class="card"><h2>Recent</h2><table>{hist}</table></div>"""
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
    return f"""<form method=post action="/epochs"><input name=label placeholder="e.g. Nortriptyline taper" required>
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

@app.route("/health")
def health():
    return {"app": "fitlog", "version": "1.0", "ok": True}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8040, debug=False)

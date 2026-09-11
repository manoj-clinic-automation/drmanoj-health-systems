#!/usr/bin/env python3
"""
GutLog v3 - single-file personal health logger. Fresh schema (no v2 migration).
Tabs: Log (day / episode / vitals) . Meals (library-backed picker, food tests) .
Meds (per-dose PRN ledger, courses, transdermal patch) . Files (vault, labs,
consults) . Review (trends, FODMAP load, dose overlay, exports).
Run:  gunicorn -w 2 -b 127.0.0.1:8020 app:app
"""
import os, csv, io, json, time, sqlite3, secrets, uuid
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (Flask, request, session, redirect, url_for, g,
                   render_template_string, jsonify, Response, abort,
                   send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GUTLOG_DB", os.path.join(BASE, "health3.db"))
UPLOAD_DIR = os.environ.get("GUTLOG_UPLOADS", os.path.join(BASE, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_MB = 12

def _secret():
    env = os.environ.get("GUTLOG_SECRET")
    if env: return env
    path = DB_PATH + ".secret"
    if os.path.exists(path): return open(path).read().strip()
    s = secrets.token_hex(32)
    with open(path, "w") as f: f.write(s)
    os.chmod(path, 0o600)
    return s

app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret(),
    MAX_CONTENT_LENGTH=MAX_FILE_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("GUTLOG_INSECURE") != "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
_failed = {"count": 0, "until": 0.0}

# ------------------------------------------------------------------ pwa
# Manifest + service worker + icons live in pwa.py so this file's page
# template stays untouched. Guarded: a missing pwa.py must not take the
# app down, it only costs installability.
try:
    from pwa import pwa_bp, PWA_HEAD_SNIPPET
    app.register_blueprint(pwa_bp)
except Exception as _pwa_err:          # pragma: no cover
    PWA_HEAD_SNIPPET = ""
    import sys as _sys
    print("pwa layer unavailable: " + str(_pwa_err), file=_sys.stderr)


# ------------------------------------------------------------------ schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS days (
  day TEXT PRIMARY KEY, syms TEXT, pain INTEGER, pain_site TEXT, bristol TEXT,
  stools TEXT, tea INTEGER, coffee INTEGER, sleep TEXT, walk TEXT,
  treadmill TEXT, meditation TEXT, notes TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS library (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cat TEXT, item TEXT UNIQUE, portion TEXT,
  protein REAL, kcal REAL, fibre REAL, fodmap TEXT, status TEXT DEFAULT '',
  tags TEXT DEFAULT '', fav INTEGER DEFAULT 0, note TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS meals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, mtime TEXT, slot TEXT,
  items TEXT, protein REAL, kcal REAL, fibre REAL, fscore REAL,
  notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS doses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, dtime TEXT, medicine TEXT,
  reason TEXT, effect TEXT, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS prnmeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, sort INTEGER DEFAULT 99);
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, drug TEXT, start_day TEXT, end_day TEXT,
  response TEXT, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS patches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, strength TEXT, day_on TEXT, time_on TEXT,
  day_off TEXT, time_off TEXT, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, etime TEXT, category TEXT,
  etype TEXT, side TEXT, severity INTEGER, duration TEXT, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS vitals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, vtime TEXT, sys INTEGER,
  dia INTEGER, pulse INTEGER, weight REAL, waist REAL, temp REAL, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS foodtests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, food TEXT, portion TEXT,
  symptoms TEXT, severity TEXT, verdict TEXT, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS consults (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, doctor TEXT, reason TEXT,
  advice TEXT, changes TEXT, next_visit TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS doctors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS labs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, analyte TEXT, value REAL, created TEXT);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ftype TEXT, label TEXT,
  stored TEXT, orig TEXT, size INTEGER, created TEXT);
CREATE TABLE IF NOT EXISTS med_schedule (
  id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER NOT NULL, slot TEXT NOT NULL,
  dose_text TEXT DEFAULT '', with_food TEXT DEFAULT 'ANY', valid_from TEXT NOT NULL,
  valid_to TEXT DEFAULT '', epoch INTEGER DEFAULT 1, notes TEXT DEFAULT '', created TEXT,
  variants TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, rid INTEGER, old_day TEXT,
  old_time TEXT, new_day TEXT, new_time TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS stock_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER NOT NULL, kind TEXT NOT NULL,
  qty REAL NOT NULL, at TEXT NOT NULL, note TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS stock_meds (
  med_id INTEGER PRIMARY KEY, mode TEXT DEFAULT '');
"""

ANALYTES = {  # name: (unit, repeat-months or None)
    "HbA1c": ("%", 6), "FBS": ("mg/dL", 6), "Creatinine": ("mg/dL", 6),
    "Hb": ("g/dL", 12), "TSH": ("mIU/L", 12), "Vitamin B12": ("pg/mL", 12),
    "Vitamin D": ("ng/mL", 12), "Ferritin": ("ng/mL", 12),
    "LDL": ("mg/dL", 12), "Triglycerides": ("mg/dL", 12), "CRP": ("mg/L", None),
}
FMAP = {"L": 0.0, "L-M": 0.5, "M": 1.0, "M-H": 1.5, "H": 2.0}
PROTEIN_TARGET = 57

# (cat, item, portion, protein, kcal, fibre, fodmap, status, fav, tags, note)
LIBRARY_SEED = [
 ("A","Jowar roti","1 roti (~30 g flour)",3,105,2.9,"L","",1,"","Low-FODMAP base grain"),
 ("A","Wheat chapati","1 chapati (~30 g atta)",3.4,102,3.3,"M","",1,"","Fructans; 1 = M, 2+ = high - the wheat-day variable"),
 ("A","Parantha (plain)","1 (~45 g atta + oil)",5,180,3.5,"M","",0,"","kcal rises with ghee"),
 ("A","Multigrain bread","2 slices (~50 g)",5,130,3,"M","cleared",0,"","Cleared in registry"),
 ("A","White rice (cooked)","1 katori (~150 g)",2.7,170,0.6,"L","",1,"","Safest carb in IBS"),
 ("A","Poha","1 plate (~1.5 katori)",3,180,2,"L","",0,"","Low-FODMAP breakfast option"),
 ("A","Ragi roti / porridge","1 roti (~30 g flour)",2.2,100,3.4,"L","",0,"","Best calcium grain (344 mg Ca/80 g)"),
 ("A","Bajra roti","1 roti (~30 g flour)",3.5,105,3.4,"L","",0,"","Alternate with jowar on wheat-free days"),
 ("A","Oats (plain, cooked)","1/2 cup dry (~40 g)",5,150,4,"L","",0,"","Soluble fibre"),
 ("A","Sooji / upma","1 katori cooked",3.5,130,1,"M","",0,"","Wheat-derived; keep small"),
 ("A","Rusk + Amul butter","1 rusk + 1 tsp butter",1.5,90,0.5,"M","",0,"comfort","Maida rusk + butter; fructan at 2+"),
 ("A","Parle-G Gold","2 biscuits (~18 g)",1.2,85,0.3,"M","",0,"","2 = M, half-pack = high"),
 ("B","Moong dal (dhuli)","1 katori cooked",7,105,4,"L-M","",1,"","Hulled + split = lowest GOS"),
 ("B","Masoor dhuli","1 katori cooked",7.5,110,4,"L-M","",1,"","With moong, the safest dal pair"),
 ("B","Arhar / toor dal (dhuli)","1/2 katori cooked",4.5,70,2.5,"M","",0,"","1/2 katori is the safe zone"),
 ("B","Urad dhuli","1/2 katori cooked",5,75,3,"M","",0,"","Idli/dosa ferment lowers it further"),
 ("B","Urad black (sabut)","1/4 katori cooked",3,55,3.5,"M-H","test",0,"","Highest-GOS of your dals; start 1/4 katori"),
 ("B","Chickpea / chana (boiled)","1/2 katori (~75 g)",5,90,5,"M-H","test",0,"","GOS test item"),
 ("B","Chana dal (Bengal gram)","1/2 katori",6,100,5,"M-H","test",0,"","GOS test item"),
 ("B","Lobia","1/2 katori (~80 g)",5,90,4,"M-H","test",0,"","GOS test item"),
 ("B","Roasted chana","30 g",5,110,4,"M","",0,"","Portion-sensitive"),
 ("B","Sattu","30 g in water",7,110,5,"M-H","",0,"","Chana-based; treat like chana"),
 ("C","Soya chunks 10 g","10 g dry",5.2,33,1.3,"L","",1,"","Current titration step"),
 ("C","Soya chunks 15-20 g","15-20 g dry",9,58,2.2,"L-M","",0,"","Next titration step"),
 ("C","Soy isolate 5 g","5 g",4.5,18,0,"L","",1,"","Near-zero FODMAP"),
 ("C","Soy isolate 10 g","10 g",9,37,0,"L","",0,"","Up-titration goal"),
 ("C","Salted peanuts","30 g (~1 fistful)",7.5,170,2.5,"L","",0,"","Low-FODMAP protein snack"),
 ("C","Fish (rohu/katla, curry)","100 g cooked",19,160,0,"L","",0,"","Low if curry base has no onion/garlic"),
 ("D","Paneer 50 g","50 g",9,130,0,"L","cleared",1,"","Cleared < 50 g"),
 ("D","Paneer 75 g","75 g",13.5,195,0,"L-M","test",0,"","Current test portion"),
 ("D","Curd / dahi","1 katori (~150 g)",5,90,0,"L-M","",1,"","Fermented - gentler than milk"),
 ("D","Mattha / buttermilk","1 glass (~200 ml)",3,60,0,"L","",0,"","Lowest-lactose dairy"),
 ("D","Milk in tea","~100 ml per cup",3,60,0,"M","",0,"","Lactose stacks with cup count"),
 ("D","Coconut milk (homemade)","100 ml",1,75,0.5,"L","",0,"","Low at 100 ml, moderate at 200+"),
 ("D","Amul butter","1 tsp (~5 g)",0,36,0,"L","",0,"","Pure fat, no FODMAP"),
 ("E","Tinda","1 katori",1,65,2,"L","",0,"","Gourd family"),
 ("E","Parval","1 katori",2,65,3,"L","",0,"","Gourd"),
 ("E","Lauki","1 katori",0.6,55,1.5,"L","",1,"",""),
 ("E","Torai","1 katori",0.5,55,2,"L","",0,"",""),
 ("E","Raw banana sabzi","1 katori",1.3,95,3,"L","",0,"","Starchy"),
 ("E","Potato","1 katori",1.6,110,2,"L","",0,"","kcal up if fried"),
 ("E","Bhindi","1 katori",2,70,3.5,"L-M","",0,"",""),
 ("E","Kaddu","1 katori",1,60,1.5,"L-M","",0,"","Mannitol at larger serve"),
 ("E","Cucumber","1 katori raw",0.6,25,1,"L","",0,"",""),
 ("E","Capsicum (green)","1 katori",1.3,60,2,"L","",0,"",""),
 ("E","Paneer-dry (onion base)","1 katori",9,180,1.5,"M-H","",0,"","The ONION base is the high part"),
 ("E","Gwar ki fali","1 katori",3,70,3,"M-H","",0,"","Cluster bean = GOS"),
 ("E","Arbi","1 katori",1.5,110,3,"L","",0,"","Starchy"),
 ("E","Carrot","1 katori",1,55,3,"L","",0,"","Soup/salad friendly"),
 ("E","Palak (spinach)","1 katori cooked",3,60,2.5,"L","",0,"","Palak-paneer without onion base = safe"),
 ("E","Sahjan / moringa patti","1/2 katori cooked",2,35,1,"L","",0,"","Micronutrient-dense"),
 ("E","French beans","1 katori",2,60,3,"L-M","",0,"","~15 beans low"),
 ("E","Onion (tarka base)","in sabzi",0,0,0,"H","",0,"","Hidden fructan - flag onion-base sabzis"),
 ("E","Tomato (base)","in sabzi",0,0,0,"L","",0,"","Base is fine"),
 ("E","Veg soup (homemade)","1 bowl (~250 ml)",2,80,2.5,"L","",1,"","LOW only with no onion/garlic in broth"),
 ("F","Makhana","30 g roasted",3,105,4,"L","cleared",1,"","Cleared"),
 ("F","Rajgira laddu","1 (~25 g)",2.5,110,2,"L","cleared",1,"","Cleared"),
 ("F","Til laddu","1 (~20 g)",2,95,1.5,"L-M","",0,"","Jaggery = sucrose, not a FODMAP"),
 ("F","Amla","1 fruit (~30 g)",0.3,30,2.4,"L","",0,"","Vitamin-C source"),
 ("F","Almonds (soaked)","5 peeled",1.3,35,0.8,"L","",1,"","<=10 is the low cut-off"),
 ("F","Walnuts (soaked)","2 halves",0.7,26,0.3,"L","",1,"","Soaking softens only; FODMAP same"),
 ("F","Mixed nuts/seeds","30 g",5,180,3,"M","",0,"","Cashew & pistachio are HIGH"),
 ("F","Flaxseed","1 tbsp (~10 g)",2,55,3,"H","trigger",0,"","YOUR TRIGGER - avoid"),
 ("F","Cauliflower","1 katori",2,50,3,"H","trigger",0,"","YOUR TRIGGER - mannitol"),
 ("F","Garlic pearl","1 capsule",0,5,0,"L","cleared",0,"","Oil form ~ no fructan"),
 ("F","Baskin Robbins choc bar","1 bar (~70 g)",3,220,1,"H","",0,"comfort","Lactose + sugar; occasional treat"),
 ("F","Cadbury Nutties","40 g",3.5,210,1,"M-H","",0,"comfort","Milk chocolate + peanut"),
 ("F","Lays chips (plain salted)","20 g",1.3,110,0.7,"L","",0,"comfort","Flavoured packs = H (onion/garlic)"),
 ("G","Papaya (ripe)","1 bowl (~150 g)",0.8,65,2.5,"L","",1,"","Breakfast anchor"),
 ("G","Banana (1/2 ripe)","1/2 medium",0.7,53,1.3,"L","",0,"","Whole ripe = M"),
 ("G","Guava (ripe)","1 medium (~100 g)",1,55,5.5,"L","",1,"","Best fibre-per-bite fruit"),
 ("G","Dates x1","1 date",0.4,25,0.7,"M","",0,"","1 = borderline"),
 ("G","Dates x2","2 dates",0.8,50,1.4,"M-H","",0,"","2 crosses into high; prefer 1"),
 ("G","Cherries x2","2 cherries",0.2,10,0.3,"M","",0,"","A handful = HIGH (sorbitol)"),
 ("G","Strawberries","5 medium",0.5,25,1.5,"L-M","",0,"","5 is the safe serve"),
 ("G","Blueberries","1/4 cup (~40 g)",0.3,23,1,"L-M","",0,"","1 cup = moderate"),
 ("G","Kiwi","1 medium",0.8,45,2,"L","",0,"","Mild laxative - useful in C-phase"),
 ("G","Orange / mausambi","1 medium",1,60,2.5,"L","",0,"","Whole fruit, not juice"),
 ("G","Pomegranate","1/4 cup arils",0.6,35,1.5,"L-M","",0,"","Half cup+ = moderate"),
 ("G","Apple (1/4 peeled)","1/4 apple",0.1,25,1,"H","test",0,"","Excess fructose + sorbitol; low expectation"),
 ("G","Mango","any usual serve",0.5,60,1,"H","trigger",0,"","YOUR TRIGGER - excess fructose"),
 ("H","Tea (your cup)","1 cup - 0.3 tsp sugar",1.5,65,0,"M","",0,"","Count tracked on the Day tab; taper 6-8 to 2-3"),
 ("H","Coffee (1/2 milk 1/2 water)","1 cup - no sugar",1.5,35,0,"L-M","",0,"","Count tracked on the Day tab"),
 ("H","Ginger in tea/soup","2-3 g",0,2,0,"L","",0,"","Prokinetic; safe flavour base"),
 ("H","Smoothie (your recipe)","coconut milk + 1/2 banana + 5 strawberries + 5 g isolate",6.4,185,3.6,"L","",1,"","All components low at these portions"),
 ("H","Khichdi (moong + rice)","1.5 katori",6.2,225,2.5,"L","",1,"","Flare-day meal"),
 ("H","Curd rice","1 katori rice + 3/4 katori curd",6.4,240,0.6,"L-M","",1,"","Gentle dinner alternative"),
]

def _course_chips():
    """
    Default chips for the 'Start a course' picker.

    These were four real drugs with doses, hardcoded in a public repository.
    They are a prefill convenience for a free-text field, so an empty default
    costs nothing but a little typing. Existing courses are unaffected: they
    live in the courses table, not here.
    """
    vals = _local_seed("course_chips")
    return "|".join(vals) if vals else "Other"


def _local_seed(key):
    """
    Seed lists for a BRAND-NEW database, read from regimen.local.json
    beside this file.

    These were literals here until 2026-09-10: fifteen real medicine names
    and two named doctors, in a public repository. The data moved out; the
    file is gitignored.

    Missing file returns an empty list on purpose. _seed() only runs when
    settings['seeded_v3'] is unset, so an existing database never reaches
    this code and is unaffected. A fresh install starts empty rather than
    with someone else's prescription -- which is the right default for a
    clone. Startup must not fail over an optional seed file.
    """
    try:
        with open(os.path.join(BASE, "regimen.local.json"), "r",
                  encoding="utf-8") as fh:
            val = json.load(fh).get(key)
            return val if isinstance(val, list) else []
    except (IOError, OSError, ValueError):
        return []

PRN_SEED = _local_seed("prn_seed")

DOCTOR_SEED = _local_seed("doctor_seed")

SCHEMA_VERSION = "3.3.2"   # GUTLOG_V330_PHASE_A GUTLOG_V332_VARIANTS GUTLOG_V333_ROWACT GUTLOG_V340_READABILITY GUTLOG_V341_PICKER GUTLOG_V342_PAINSITE GUTLOG_V350_PHASE_B GUTLOG_V360_PHASE_C

# slot -> (label, default clock time). Times are display hints only; the
# schedule is not time-enforced.
SLOTS = [("MORNING", "Morning", "08:00"), ("NOON", "Noon", "14:00"),
         ("EVENING", "Evening", "20:00"), ("NIGHT", "Night", "22:30")]

_V330_COLS = [
    ("prnmeds", "molecule", "TEXT DEFAULT ''"),
    ("prnmeds", "form", "TEXT DEFAULT ''"),
    ("prnmeds", "pack_size", "INTEGER DEFAULT 0"),
    ("prnmeds", "stock", "REAL DEFAULT 0"),
    ("prnmeds", "active", "INTEGER DEFAULT 1"),
    ("prnmeds", "scheduled", "INTEGER DEFAULT 0"),
    ("doses", "status", "TEXT DEFAULT 'TAKEN'"),
    ("doses", "med_id", "INTEGER"),
    ("doses", "sched_id", "INTEGER"),
    ("doses", "dose_text", "TEXT DEFAULT ''"),
    ("episodes", "bristol", "TEXT DEFAULT ''"),
    ("med_schedule", "variants", "TEXT DEFAULT ''"),
]

_V330_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sched_med ON med_schedule (med_id)",
    "CREATE INDEX IF NOT EXISTS idx_sched_open ON med_schedule (valid_to)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_one_open "
    "ON med_schedule (med_id, slot) WHERE valid_to = ''",
    "CREATE INDEX IF NOT EXISTS idx_doses_day ON doses (day)",
    "CREATE INDEX IF NOT EXISTS idx_doses_sched ON doses (day, sched_id)",
]

_V330_SETTINGS = [
    ("slot_window_min", "120"),
    ("bp_flag_sys", "140"),
    ("bp_flag_dia", "90"),
    ("med_epoch", "1"),
]


def _migrate(con):
    # Fast path: one SELECT per request once the schema is current.
    # Cheaper than the PRAGMA sweep this replaces.
    row = con.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()
    if row and row[0] == SCHEMA_VERSION:
        return

    # -- pre-v3.3.0: vitals.temp (kept from the original _migrate) -----
    cols = [r[1] for r in con.execute("PRAGMA table_info(vitals)").fetchall()]
    if cols and "temp" not in cols:
        con.execute("ALTER TABLE vitals ADD COLUMN temp REAL")

    # -- v3.3.0 columns: one PRAGMA per table, not per column ----------
    seen = {}
    for table, col, decl in _V330_COLS:
        if table not in seen:
            seen[table] = set(
                r[1] for r in con.execute(
                    "PRAGMA table_info(" + table + ")").fetchall())
        if not seen[table]:
            continue          # table absent; SCHEMA will create it
        if col not in seen[table]:
            con.execute("ALTER TABLE " + table + " ADD COLUMN "
                        + col + " " + decl)
            seen[table].add(col)

    for stmt in _V330_INDEXES:
        con.execute(stmt)

    # -- normalise legacy free-text dose rows to prnmeds ---------------
    con.execute("UPDATE doses SET status='TAKEN' "
                "WHERE status IS NULL OR status=''")
    con.execute("UPDATE doses SET med_id=("
                "SELECT p.id FROM prnmeds p WHERE p.name=doses.medicine) "
                "WHERE med_id IS NULL")

    for key, val in _V330_SETTINGS:
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (key, val))
    con.execute("INSERT OR REPLACE INTO settings(key,value) "
                "VALUES('schema_version',?)", (SCHEMA_VERSION,))
    con.commit()

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.executescript(SCHEMA)
        _migrate(g.db)
        _seed(g.db)
    return g.db

def _seed(con):
    if con.execute("SELECT 1 FROM settings WHERE key='seeded_v3'").fetchone():
        return
    now = datetime.now().isoformat(timespec="seconds")
    for i, r in enumerate(LIBRARY_SEED):
        con.execute("""INSERT OR IGNORE INTO library
            (cat,item,portion,protein,kcal,fibre,fodmap,status,fav,tags,note,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], now))
    for i, m in enumerate(PRN_SEED):
        con.execute("INSERT OR IGNORE INTO prnmeds(name,sort) VALUES(?,?)", (m, i))
    for d in DOCTOR_SEED:
        con.execute("INSERT OR IGNORE INTO doctors(name) VALUES(?)", (d,))
    con.execute("INSERT INTO settings(key,value) VALUES('seeded_v3','1')")
    con.commit()

@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d: d.close()

def setting(key):
    r = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None

def set_setting(key, value):
    db().execute("INSERT INTO settings(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db().commit()

def now_s(): return datetime.now().isoformat(timespec="seconds")
def now_hm(): return datetime.now().strftime("%H:%M")
def today(): return date.today().isoformat()

# ------------------------------------------------------------------ auth
# Session epoch: a token stored in settings and copied into each session on
# login. login_required requires the two to match, so rotating the token
# (rotate_epoch) instantly invalidates every existing session on every device
# WITHOUT changing SECRET_KEY or restarting. No data is touched.
def auth_epoch():
    ep = setting("auth_epoch")
    if not ep:
        ep = secrets.token_hex(16)
        set_setting("auth_epoch", ep)
    return ep

def rotate_epoch():
    """Sign out all devices by invalidating every existing session."""
    ep = secrets.token_hex(16)
    set_setting("auth_epoch", ep)
    return ep

def stamp_session():
    """Mark THIS session as logged in on the current epoch."""
    session.permanent = True
    session["ok"] = True
    session["ep"] = auth_epoch()

def owner_set():
    """True once an owner key has been created."""
    return bool(setting("owner_hash"))

def owner_ok(key):
    """True only for the correct owner key. Gates all Account controls."""
    h = setting("owner_hash")
    return bool(h) and check_password_hash(h, key or "")

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not setting("pw_hash"): return redirect(url_for("setup"))
        if not session.get("ok") or session.get("ep") != auth_epoch():
            return redirect(url_for("login"))
        return f(*a, **k)
    return w

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if setting("pw_hash"): return redirect(url_for("login"))
    err = ""
    if request.method == "POST":
        pw = request.form.get("pw", "")
        if len(pw) < 8: err = "Use at least 8 characters."
        elif pw != request.form.get("pw2", ""): err = "Passwords do not match."
        else:
            set_setting("pw_hash", generate_password_hash(pw))
            stamp_session()
            return redirect(url_for("home"))
    return render_template_string(AUTH_PAGE, mode="setup", err=err)

@app.route("/login", methods=["GET", "POST"])
def login():
    if not setting("pw_hash"): return redirect(url_for("setup"))
    err = ""
    if request.method == "POST":
        if time.time() < _failed["until"]:
            err = "Too many attempts. Wait a minute."
        elif check_password_hash(setting("pw_hash"), request.form.get("pw", "")):
            _failed.update(count=0, until=0.0)
            stamp_session()
            return redirect(url_for("home"))
        else:
            _failed["count"] += 1
            if _failed["count"] >= 5:
                _failed.update(count=0, until=time.time() + 60)
            err = "Wrong password."
    return render_template_string(AUTH_PAGE, mode="login", err=err)

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    msg = err = ""
    have_owner = owner_set()
    if request.method == "POST":
        action = request.form.get("action", "")

        # One-time: create the owner key (only possible while none exists).
        if action == "set_owner":
            if have_owner:
                err = "Owner key is already set."
            else:
                k, k2 = request.form.get("okey", ""), request.form.get("okey2", "")
                if len(k) < 8:
                    err = "Owner key must be at least 8 characters."
                elif k != k2:
                    err = "Owner keys do not match."
                else:
                    set_setting("owner_hash", generate_password_hash(k))
                    have_owner = True
                    msg = ("Owner key set. From now on only this key can change the "
                           "password or sign out devices.")

        # Every control below REQUIRES the owner key on each attempt.
        elif not have_owner:
            err = "Create the owner key first."
        elif not owner_ok(request.form.get("owner", "")):
            err = "Owner key is wrong."

        elif action == "password":
            new, new2 = request.form.get("new", ""), request.form.get("new2", "")
            also = request.form.get("signout_others")  # checkbox, on by default
            if len(new) < 8:
                err = "New password must be at least 8 characters."
            elif new != new2:
                err = "New passwords do not match."
            else:
                set_setting("pw_hash", generate_password_hash(new))
                if also:
                    rotate_epoch()          # invalidate every other device
                stamp_session()             # keep THIS device signed in
                msg = ("Password changed. All other devices have been signed out."
                       if also else "Password changed.")

        elif action == "logout_others":
            rotate_epoch()                  # drop all sessions...
            stamp_session()                 # ...except this one
            msg = "All other devices have been signed out."

        elif action == "change_owner":
            nk, nk2 = request.form.get("nokey", ""), request.form.get("nokey2", "")
            if len(nk) < 8:
                err = "New owner key must be at least 8 characters."
            elif nk != nk2:
                err = "New owner keys do not match."
            else:
                set_setting("owner_hash", generate_password_hash(nk))
                msg = "Owner key changed."

        else:
            err = "Unknown request."

    return render_template_string(ACCOUNT_PAGE, msg=msg, err=err, have_owner=owner_set())

# ------------------------------------------------------------------ helpers
def J(): return request.get_json(force=True)
def note(d, k="notes", n=500): return (d.get(k) or "").strip()[:n]

def insert(table, cols, vals):
    db().execute(f"INSERT INTO {table}({','.join(cols)},created) VALUES({','.join('?'*len(cols))},?)",
                 (*vals, now_s()))
    db().commit()

# ------------------------------------------------------------------ day log
@app.route("/api/day", methods=["POST"])
@login_required
def api_day():
    d = J(); day = d.get("day") or today()
    def iv(k, lo, hi):
        try: v = int(d.get(k))
        except (TypeError, ValueError): return None
        return v if lo <= v <= hi else None
    db().execute("""INSERT INTO days(day,syms,pain,pain_site,bristol,stools,tea,coffee,
        sleep,walk,treadmill,meditation,notes,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET syms=excluded.syms,pain=excluded.pain,
        pain_site=excluded.pain_site,bristol=excluded.bristol,stools=excluded.stools,
        tea=excluded.tea,coffee=excluded.coffee,sleep=excluded.sleep,walk=excluded.walk,
        treadmill=excluded.treadmill,meditation=excluded.meditation,
        notes=excluded.notes,updated=excluded.updated""",
        (day, "|".join(d.get("syms") or []), iv("pain", 0, 10), d.get("pain_site"),
         d.get("bristol"), d.get("stools"), iv("tea", 0, 20), iv("coffee", 0, 20),
         d.get("sleep"), d.get("walk"), d.get("treadmill"), d.get("meditation"),
         note(d), now_s()))
    db().commit(); return jsonify(ok=True)

@app.route("/api/day/<day>")
@login_required
def api_day_get(day):
    r = db().execute("SELECT * FROM days WHERE day=?", (day,)).fetchone()
    return jsonify(dict(r) if r else {})

@app.route("/api/summary/<day>")
@login_required
def api_summary(day):
    one = lambda sql: db().execute(sql, (day,)).fetchone()[0]
    streak = 0
    d = date.fromisoformat(day)
    while db().execute("SELECT 1 FROM days WHERE day=?", (d.isoformat(),)).fetchone():
        streak += 1; d -= timedelta(days=1)
    patch = db().execute("SELECT * FROM patches WHERE day_off IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    prot = db().execute("SELECT ROUND(SUM(protein),1) FROM meals WHERE day=?", (day,)).fetchone()[0]
    return jsonify(
        day_done=bool(db().execute("SELECT 1 FROM days WHERE day=?", (day,)).fetchone()),
        meals=one("SELECT COUNT(*) FROM meals WHERE day=?"),
        doses=one("SELECT COUNT(*) FROM doses WHERE day=?"),
        vitals=one("SELECT COUNT(*) FROM vitals WHERE day=?"),
        episodes=one("SELECT COUNT(*) FROM episodes WHERE day=?"),
        protein=prot or 0, target=PROTEIN_TARGET, streak=streak,
        patch=dict(patch) if patch else None)

# ------------------------------------------------------------------ library
@app.route("/api/library")
@login_required
def api_library():
    return jsonify([dict(r) for r in db().execute(
        "SELECT * FROM library ORDER BY cat, item")])

@app.route("/api/library", methods=["POST"])
@login_required
def api_library_add():
    d = J()
    item = (d.get("item") or "").strip()[:80]
    if not item: return jsonify(ok=False, err="Name the food."), 400
    if d.get("fodmap") not in FMAP: return jsonify(ok=False, err="Pick a FODMAP flag."), 400
    def num(k):
        try: return round(float(d.get(k) or 0), 1)
        except (TypeError, ValueError): return 0
    try:
        insert("library", ["cat","item","portion","protein","kcal","fibre","fodmap","status","fav","tags","note"],
               [(d.get("cat") or "F")[:1], item, (d.get("portion") or "")[:60],
                num("protein"), num("kcal"), num("fibre"), d["fodmap"],
                d.get("status") or "", 1 if d.get("fav") else 0,
                (d.get("tags") or "")[:40], note(d, "note", 200)])
    except sqlite3.IntegrityError:
        return jsonify(ok=False, err="That food already exists."), 400
    rid = db().execute("SELECT id FROM library WHERE item=?", (item,)).fetchone()["id"]
    return jsonify(ok=True, id=rid)

@app.route("/api/library/<int:lid>", methods=["POST"])
@login_required
def api_library_edit(lid):
    d = J()
    r = db().execute("SELECT * FROM library WHERE id=?", (lid,)).fetchone()
    if not r: abort(404)
    def num(k, cur):
        if k not in d: return cur
        try: return round(float(d.get(k) or 0), 1)
        except (TypeError, ValueError): return cur
    fodmap = d.get("fodmap", r["fodmap"])
    if fodmap not in FMAP: fodmap = r["fodmap"]
    db().execute("""UPDATE library SET item=?,portion=?,protein=?,kcal=?,fibre=?,
        fodmap=?,status=?,fav=?,tags=?,note=? WHERE id=?""",
        ((d.get("item") or r["item"]).strip()[:80], d.get("portion", r["portion"]),
         num("protein", r["protein"]), num("kcal", r["kcal"]), num("fibre", r["fibre"]),
         fodmap, d.get("status", r["status"]),
         (1 if d["fav"] else 0) if "fav" in d else r["fav"],
         d.get("tags", r["tags"]), d.get("note", r["note"]), lid))
    db().commit(); return jsonify(ok=True)

# ------------------------------------------------------------------ meals
@app.route("/api/meals", methods=["POST"])
@login_required
def api_meals():
    d = J()
    items = d.get("items") or []
    if not items: return jsonify(ok=False, err="Add at least one item."), 400
    clean, p, k, f, fs = [], 0.0, 0.0, 0.0, 0.0
    for it in items[:20]:
        try:
            q = max(0.25, min(10.0, float(it.get("q", 1))))
            ip, ik, ifb = float(it.get("p", 0)), float(it.get("k", 0)), float(it.get("f", 0))
        except (TypeError, ValueError):
            continue
        fm = it.get("fm") if it.get("fm") in FMAP else "M"
        n = (it.get("n") or "?").strip()[:80]
        clean.append({"n": n, "q": q, "p": ip, "k": ik, "f": ifb, "fm": fm})
        p += q * ip; k += q * ik; f += q * ifb; fs += q * FMAP[fm]
    if not clean: return jsonify(ok=False, err="Add at least one item."), 400
    insert("meals", ["day","mtime","slot","items","protein","kcal","fibre","fscore","notes"],
           [d.get("day") or today(), d.get("mtime") or now_hm(), d.get("slot") or "Meal",
            json.dumps(clean), round(p,1), round(k), round(f,1), round(fs,2), note(d)])
    return jsonify(ok=True, protein=round(p,1))

@app.route("/api/meals/today/<day>")
@login_required
def api_meals_today(day):
    rows = [dict(r) for r in db().execute(
        "SELECT * FROM meals WHERE day=? ORDER BY mtime", (day,))]
    for r in rows: r["items"] = json.loads(r["items"] or "[]")
    return jsonify(rows)

# ------------------------------------------------------------------ PRN doses
@app.route("/api/doses", methods=["POST"])
@login_required
def api_doses():
    d = J()
    meds = [m.strip()[:80] for m in (d.get("meds") or []) if m and m.strip()]
    if not meds: return jsonify(ok=False, err="Pick at least one medicine."), 400
    day, t = d.get("day") or today(), d.get("dtime") or now_hm()
    for m in meds[:10]:
        insert("doses", ["day","dtime","medicine","reason","effect","notes"],
               [day, t, m, d.get("reason"), d.get("effect"), note(d)])
    return jsonify(ok=True, n=len(meds))

@app.route("/api/doses/plus", methods=["POST"])
@login_required
def api_dose_plus():
    d = J()
    m = (d.get("medicine") or "").strip()[:80]
    if not m: return jsonify(ok=False, err="No medicine."), 400
    insert("doses", ["day","dtime","medicine","reason","effect","notes"],
           [today(), now_hm(), m, d.get("reason"), None, ""])
    return jsonify(ok=True)

@app.route("/api/doses/today/<day>")
@login_required
def api_doses_today(day):
    rows = db().execute("SELECT medicine, dtime FROM doses WHERE day=? ORDER BY dtime", (day,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["medicine"], []).append(r["dtime"] or "")
    return jsonify([{"medicine": m, "times": ts} for m, ts in out.items()])

@app.route("/api/prnmeds", methods=["GET", "POST"])
@login_required
def api_prnmeds():
    if request.method == "POST":
        name = (J().get("name") or "").strip()[:80]
        if not name: return jsonify(ok=False, err="Empty name."), 400
        db().execute("INSERT OR IGNORE INTO prnmeds(name,sort) VALUES(?,99)", (name,))
        db().commit()
    return jsonify([r["name"] for r in db().execute("SELECT name FROM prnmeds ORDER BY sort, id")])


@app.route("/api/prnmeds/full")
@login_required
def api_prnmeds_full():
    return jsonify([dict(r) for r in db().execute(
        "SELECT id, name, sort, active FROM prnmeds WHERE active=1 "
        "ORDER BY sort, id")])

# ------------------------------------------------------------ now / schedule
def _slot_meta():
    return [{"slot": s, "label": lb, "time": tm} for s, lb, tm in SLOTS]


def _bump_epoch():
    cur = setting("med_epoch") or "1"
    try:
        nxt = str(int(cur) + 1)
    except ValueError:
        nxt = "1"
    set_setting("med_epoch", nxt)
    return nxt


def _refresh_scheduled_flags():
    db().execute("UPDATE prnmeds SET scheduled=0")
    db().execute("UPDATE prnmeds SET scheduled=1 WHERE id IN "
                 "(SELECT DISTINCT med_id FROM med_schedule WHERE valid_to='')")
    db().commit()


@app.route("/api/now")
@login_required
def api_now():
    """Expected doses for a day, left-joined to what was actually logged.

    Nothing is materialised in advance: expected rows are computed from
    the effective-dated schedule at read time. No nightly job to fail
    silently, and no phantom 'missed' rows for days the app never opened.
    """
    day = request.args.get("day") or today()
    rows = db().execute(
        "SELECT s.id AS sched_id, s.med_id, s.slot, s.dose_text, s.with_food, s.variants, "
        "       p.name AS name, "
        "       d.id AS dose_id, d.status AS status, d.dtime AS dtime, "
        "       d.dose_text AS logged_dose "
        "FROM med_schedule s "
        "JOIN prnmeds p ON p.id = s.med_id "
        "LEFT JOIN doses d ON d.sched_id = s.id AND d.day = ? "
        "WHERE s.valid_from <= ? AND (s.valid_to = '' OR s.valid_to >= ?) "
        "ORDER BY s.slot, p.sort, p.id", (day, day, day)).fetchall()

    by_slot = {}
    for r in rows:
        by_slot.setdefault(r["slot"], []).append(dict(r))

    slots = []
    for meta in _slot_meta():
        items = by_slot.get(meta["slot"], [])
        if not items:
            continue
        done = len([i for i in items if i["status"] in ("TAKEN", "SKIPPED")])
        meta = dict(meta)
        meta["rows"] = items
        meta["done"] = done
        meta["total"] = len(items)
        slots.append(meta)

    extras = [dict(r) for r in db().execute(
        "SELECT d.id, d.medicine, d.dtime, d.status, d.reason "
        "FROM doses d WHERE d.day=? AND d.sched_id IS NULL "
        "ORDER BY d.dtime", (day,)).fetchall()]

    # An expected dose belongs on the dose card. Listing a
    # scheduled medicine here too invites logging it twice, or in
    # the wrong place. meds_all backs the "Show all" button for
    # the rare unplanned dose of a regular medicine.
    meds = [dict(r) for r in db().execute(
        "SELECT id, name, sort FROM prnmeds WHERE active=1 "
        "AND COALESCE(scheduled,0)=0 ORDER BY sort, id").fetchall()]
    meds_all = [dict(r) for r in db().execute(
        "SELECT id, name, sort FROM prnmeds WHERE active=1 "
        "ORDER BY sort, id").fetchall()]

    return jsonify(day=day, slots=slots, extras=extras, meds=meds,
                   meds_all=meds_all, has_schedule=bool(rows))


@app.route("/api/now/dose", methods=["POST"])
@login_required
def api_now_dose():
    d = J()
    status = (d.get("status") or "TAKEN").upper()
    if status not in ("TAKEN", "SKIPPED", "EXTRA"):
        return jsonify(ok=False, err="Bad status."), 400

    med_id = d.get("med_id")
    name = (d.get("medicine") or "").strip()[:80]
    if med_id:
        r = db().execute("SELECT name FROM prnmeds WHERE id=?",
                         (med_id,)).fetchone()
        if not r:
            return jsonify(ok=False, err="Unknown medicine."), 400
        name = r["name"]
    if not name:
        return jsonify(ok=False, err="No medicine."), 400

    day = d.get("day") or today()
    dtime = d.get("dtime") or now_hm()
    sched_id = d.get("sched_id")
    # GUTLOG_V350_PHASE_B -- backfill arrives with a day and time; refuse the future
    if not _valid_day(day):
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if not _valid_hm(dtime):
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    if day == today() and dtime > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400

    # Re-tapping a scheduled row corrects it rather than duplicating.
    if sched_id:
        ex = db().execute("SELECT id FROM doses WHERE sched_id=? AND day=?",
                          (sched_id, day)).fetchone()
        if ex:
            db().execute(
                "UPDATE doses SET status=?, dtime=?, reason=?, medicine=?, "
                "med_id=?, dose_text=? WHERE id=?",
                (status, dtime, d.get("reason"), name, med_id,
                 (d.get("dose_text") or "")[:40], ex["id"]))
            db().commit()
            return jsonify(ok=True, id=ex["id"], updated=True)

    db().execute(
        "INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,"
        "status,med_id,sched_id,dose_text) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (day, dtime, name, d.get("reason"), None, note(d), now_s(),
         status, med_id, sched_id, (d.get("dose_text") or "")[:40]))
    db().commit()
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    return jsonify(ok=True, id=rid)


@app.route("/api/now/undo/<int:did>", methods=["POST"])
@login_required
def api_now_undo(did):
    """Mistap recovery. Deletes one dose row."""
    db().execute("DELETE FROM doses WHERE id=?", (did,))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/schedule")
@login_required
def api_schedule():
    rows = db().execute(
        "SELECT s.*, p.name AS name FROM med_schedule s "
        "JOIN prnmeds p ON p.id=s.med_id "
        "WHERE s.valid_to='' ORDER BY s.slot, p.sort, p.id").fetchall()
    return jsonify(slots=_slot_meta(), rows=[dict(r) for r in rows])


@app.route("/api/schedule", methods=["POST"])
@login_required
def api_schedule_post():
    """Add or change one regimen line.

    Effective-dated: a change closes the open row and opens a new one, so
    history stays interpretable. Rows are never edited in place.
    """
    d = J()
    med_id = d.get("med_id")
    slot = (d.get("slot") or "").upper()
    if not med_id or slot not in [s for s, _, _ in SLOTS]:
        return jsonify(ok=False, err="Pick a medicine and a slot."), 400
    if not db().execute("SELECT 1 FROM prnmeds WHERE id=?",
                        (med_id,)).fetchone():
        return jsonify(ok=False, err="Unknown medicine."), 400

    day = d.get("valid_from") or today()
    yday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()

    open_row = db().execute(
        "SELECT id FROM med_schedule WHERE med_id=? AND slot=? AND valid_to=''",
        (med_id, slot)).fetchone()
    if open_row:
        db().execute("UPDATE med_schedule SET valid_to=? WHERE id=?",
                     (yday, open_row["id"]))

    epoch = _bump_epoch()
    db().execute(
        "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,"
        "valid_to,epoch,notes,created) VALUES(?,?,?,?,?,'',?,?,?)",
        (med_id, slot, (d.get("dose_text") or "")[:40],
         (d.get("with_food") or "ANY")[:10], day, int(epoch), note(d), now_s()))
    if d.get("variants") is not None:
        db().execute(
            "UPDATE med_schedule SET variants=? WHERE id="
            "(SELECT MAX(id) FROM med_schedule)",
            ((d.get("variants") or "")[:120],))
        db().commit()
    db().commit()
    _refresh_scheduled_flags()
    return jsonify(ok=True, epoch=epoch)


@app.route("/api/schedule/close/<int:sid>", methods=["POST"])
@login_required
def api_schedule_close(sid):
    """Stop a medicine. Closes the row; never deletes it."""
    d = J()
    day = d.get("valid_to") or today()
    db().execute("UPDATE med_schedule SET valid_to=? WHERE id=? AND valid_to=''",
                 (day, sid))
    db().commit()
    _bump_epoch()
    _refresh_scheduled_flags()
    return jsonify(ok=True)


# ------------------------------------------------------------------ patch
@app.route("/api/patch")
@login_required
def api_patch():
    r = db().execute("SELECT * FROM patches WHERE day_off IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return jsonify(dict(r) if r else {})

@app.route("/api/patch", methods=["POST"])
@login_required
def api_patch_post():
    d = J()
    active = db().execute("SELECT id FROM patches WHERE day_off IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if d.get("action") == "on":
        if active: return jsonify(ok=False, err="A patch is already on - remove it first."), 400
        insert("patches", ["strength","day_on","time_on","day_off","time_off","notes"],
               [(d.get("strength") or "5 mcg/hr")[:30], d.get("day") or today(),
                d.get("dtime") or now_hm(), None, None, note(d)])
    elif d.get("action") == "off":
        if not active: return jsonify(ok=False, err="No patch is on."), 400
        db().execute("UPDATE patches SET day_off=?, time_off=?, notes=COALESCE(NULLIF(?,''),notes) WHERE id=?",
                     (d.get("day") or today(), d.get("dtime") or now_hm(), note(d), active["id"]))
        db().commit()
    else:
        return jsonify(ok=False, err="Unknown action."), 400
    return jsonify(ok=True)

# ------------------------------------------------------------------ courses
@app.route("/api/courses", methods=["POST"])
@login_required
def api_courses():
    d = J()
    if not d.get("drug"): return jsonify(ok=False, err="Pick a drug."), 400
    insert("courses", ["drug","start_day","end_day","response","notes"],
           [d["drug"].strip()[:80], d.get("start_day") or today(), None, None, note(d)])
    return jsonify(ok=True)

@app.route("/api/courses/end/<int:cid>", methods=["POST"])
@login_required
def api_course_end(cid):
    d = J()
    db().execute("UPDATE courses SET end_day=?, response=? WHERE id=?",
                 (d.get("end_day") or today(), d.get("response"), cid))
    db().commit(); return jsonify(ok=True)

@app.route("/api/courses/active")
@login_required
def api_courses_active():
    return jsonify([dict(r) for r in db().execute(
        "SELECT * FROM courses WHERE end_day IS NULL ORDER BY start_day")])

# ------------------------------------------------------------------ episodes / vitals
@app.route("/api/episodes", methods=["POST"])
@login_required
def api_episodes():
    d = J()
    if not d.get("etype"): return jsonify(ok=False, err="Pick an episode type."), 400
    insert("episodes", ["day","etime","category","etype","side","severity","duration","notes","bristol"],
           [d.get("day") or today(), d.get("etime") or now_hm(),
            d.get("category"), d["etype"], d.get("side"), d.get("severity"),
            d.get("duration"), note(d), (d.get("bristol") or "")[:4]])
    return jsonify(ok=True)

@app.route("/api/vitals", methods=["POST"])
@login_required
def api_vitals():
    d = J()
    def num(k, lo, hi, integer=True):
        v = d.get(k)
        if v in (None, ""): return None
        try: v = int(v) if integer else float(v)
        except ValueError: return None
        return v if lo <= v <= hi else None
    insert("vitals", ["day","vtime","sys","dia","pulse","weight","waist","temp","notes"],
           [d.get("day") or today(), d.get("vtime") or now_hm(), num("sys",70,260),
            num("dia",40,160), num("pulse",30,200), num("weight",40,150,False),
            num("waist",50,160,False), num("temp",30,115,False), note(d)])
    return jsonify(ok=True)

# ------------------------------------------------------------------ food tests
@app.route("/api/foodtests", methods=["POST"])
@login_required
def api_foodtests():
    d = J()
    if not d.get("food"): return jsonify(ok=False, err="Pick a food."), 400
    insert("foodtests", ["day","food","portion","symptoms","severity","verdict","notes"],
           [d.get("day") or today(), d["food"], d.get("portion"), d.get("symptoms"),
            d.get("severity"), d.get("verdict"), note(d)])
    v = d.get("verdict") or ""
    new_status = "cleared" if v.startswith("Tolerated") else \
                 "trigger" if v.startswith("Trigger") else None
    if new_status:
        db().execute("UPDATE library SET status=? WHERE item=?", (new_status, d["food"]))
        db().commit()
    return jsonify(ok=True)

# ------------------------------------------------------------------ consults / doctors / labs
@app.route("/api/consults", methods=["POST"])
@login_required
def api_consults():
    d = J()
    if not d.get("doctor"): return jsonify(ok=False, err="Pick a doctor."), 400
    insert("consults", ["day","doctor","reason","advice","changes","next_visit"],
           [d.get("day") or today(), d["doctor"], d.get("reason"),
            (d.get("advice") or "").strip()[:800], d.get("changes"), d.get("next_visit")])
    return jsonify(ok=True)

@app.route("/api/doctors", methods=["GET", "POST"])
@login_required
def api_doctors():
    if request.method == "POST":
        name = (J().get("name") or "").strip()[:80]
        if not name: return jsonify(ok=False, err="Empty name."), 400
        db().execute("INSERT OR IGNORE INTO doctors(name) VALUES(?)", (name,))
        db().commit()
    return jsonify([r["name"] for r in db().execute("SELECT name FROM doctors ORDER BY id")])

@app.route("/api/labs", methods=["POST"])
@login_required
def api_labs():
    d = J()
    if d.get("analyte") not in ANALYTES: return jsonify(ok=False, err="Unknown analyte."), 400
    try: v = float(d.get("value"))
    except (TypeError, ValueError): return jsonify(ok=False, err="Enter a number."), 400
    insert("labs", ["day","analyte","value"], [d.get("day") or today(), d["analyte"], v])
    return jsonify(ok=True)

@app.route("/api/labs/status")
@login_required
def api_labs_status():
    out = []
    for name, (unit, months) in ANALYTES.items():
        r = db().execute("SELECT day,value FROM labs WHERE analyte=? ORDER BY day DESC LIMIT 1", (name,)).fetchone()
        hist = [dict(x) for x in db().execute(
            "SELECT day,value FROM labs WHERE analyte=? ORDER BY day", (name,)).fetchall()]
        due = None
        if r and months:
            y, m, dd = map(int, r["day"].split("-"))
            m += months; y += (m - 1) // 12; m = (m - 1) % 12 + 1
            due = f"{y:04d}-{m:02d}-{min(dd,28):02d}"
        out.append(dict(analyte=name, unit=unit, last_day=r["day"] if r else None,
                        last_value=r["value"] if r else None, next_due=due,
                        flare_only=months is None, history=hist))
    return jsonify(out)

# ------------------------------------------------------------------ files
@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename: return jsonify(ok=False, err="No file chosen."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify(ok=False, err="Only PDF / JPG / PNG allowed."), 400
    stored = uuid.uuid4().hex + ext
    path = os.path.join(UPLOAD_DIR, stored)
    f.save(path)
    size = os.path.getsize(path)
    insert("files", ["day","ftype","label","stored","orig","size"],
           [request.form.get("day") or today(), request.form.get("ftype") or "Other",
            (request.form.get("label") or f.filename)[:120], stored,
            secure_filename(f.filename)[:120], size])
    return jsonify(ok=True)

@app.route("/file/<int:fid>")
@login_required
def get_file(fid):
    r = db().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if not r: abort(404)
    return send_from_directory(UPLOAD_DIR, r["stored"], download_name=r["orig"] or r["stored"])

@app.route("/api/delete/<table>/<int:rid>", methods=["POST"])
@login_required
def api_delete(table, rid):
    if table not in ("doses","foodtests","vitals","episodes","meals","consults",
                     "labs","courses","files","patches","library"):
        abort(404)
    if table == "files":
        r = db().execute("SELECT stored FROM files WHERE id=?", (rid,)).fetchone()
        if r:
            try: os.remove(os.path.join(UPLOAD_DIR, r["stored"]))
            except OSError: pass
    db().execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    db().commit(); return jsonify(ok=True)

# ------------------------------------------------------------------ day view / retime
# GUTLOG_V350_PHASE_B -- one day, every stream, in time order; entries can
# be moved to the time they actually happened. Every move is kept in
# `edits`, so a changed time is visible rather than silent.
_TIME_COL = {"doses": "dtime", "episodes": "etime", "vitals": "vtime", "meals": "mtime"}


def _valid_hm(s):
    s = (s or "").strip()
    if len(s) != 5 or s[2] != ":" or not (s[:2] + s[3:]).isdigit():
        return None
    if int(s[:2]) > 23 or int(s[3:]) > 59:
        return None
    return s


def _valid_day(s):
    try:
        d = date.fromisoformat((s or "").strip())
    except ValueError:
        return None
    if d > date.today() or d.year < 2020:
        return None
    return d.isoformat()


@app.route("/api/dayview")
@login_required
def api_dayview():
    day = _valid_day(request.args.get("day")) or today()
    edited = set((r["tbl"], r["rid"]) for r in db().execute(
        "SELECT DISTINCT tbl, rid FROM edits").fetchall())
    out = []

    def add(tbl, rid, t, kind, title, sub):
        out.append({"tbl": tbl, "id": rid, "time": t or "", "kind": kind,
                    "title": title or "", "sub": sub,
                    "edited": (tbl, rid) in edited})

    for r in db().execute(
            "SELECT id, dtime, medicine, status, sched_id, dose_text "
            "FROM doses WHERE day=?", (day,)).fetchall():
        if r["status"] == "SKIPPED":
            add("doses", r["id"], r["dtime"], "Skipped", r["medicine"], "")
        else:
            add("doses", r["id"], r["dtime"],
                "Dose" if r["sched_id"] else "Extra",
                r["medicine"], r["dose_text"] or "")
    for r in db().execute(
            "SELECT id, etime, etype, side, severity, bristol "
            "FROM episodes WHERE day=?", (day,)).fetchall():
        parts = []
        if r["severity"] not in (None, ""):
            parts.append(str(r["severity"]) + "/10")
        if r["bristol"]:
            parts.append("Bristol " + str(r["bristol"]))
        if r["side"]:
            parts.append(str(r["side"]))
        add("episodes", r["id"], r["etime"], "Symptom", r["etype"], " · ".join(parts))
    for r in db().execute(
            "SELECT id, vtime, sys, dia, pulse, weight, temp "
            "FROM vitals WHERE day=?", (day,)).fetchall():
        parts = []
        if r["pulse"]:
            parts.append("pulse " + str(r["pulse"]))
        if r["weight"]:
            parts.append(str(r["weight"]) + " kg")
        if r["temp"]:
            parts.append(str(r["temp"]) + "°")
        if r["sys"] or r["dia"]:
            add("vitals", r["id"], r["vtime"], "BP",
                str(r["sys"] or "-") + "/" + str(r["dia"] or "-"), " · ".join(parts))
        else:
            add("vitals", r["id"], r["vtime"], "Vitals", "Vitals", " · ".join(parts))
    for r in db().execute(
            "SELECT id, mtime, slot, items, protein FROM meals WHERE day=?",
            (day,)).fetchall():
        try:
            names = [str(i.get("n", "")) for i in json.loads(r["items"] or "[]")]
        except (ValueError, AttributeError):
            names = []
        sub = ", ".join(n for n in names if n)[:90]
        if r["protein"]:
            sub = (sub + " · " if sub else "") + str(r["protein"]) + " g protein"
        add("meals", r["id"], r["mtime"], "Meal", r["slot"] or "Meal", sub)

    out.sort(key=lambda e: (e["time"] == "", e["time"], e["tbl"], e["id"]))
    return jsonify(day=day, today=today(), entries=out)


@app.route("/api/retime", methods=["POST"])
@login_required
def api_retime():
    d = J()
    tbl = d.get("table")
    col = _TIME_COL.get(tbl)
    if not col:
        return jsonify(ok=False, err="Unknown entry type."), 400
    try:
        rid = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Bad entry."), 400
    new_t = _valid_hm(d.get("time"))
    if not new_t:
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    row = db().execute("SELECT * FROM " + tbl + " WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify(ok=False, err="Entry not found."), 404
    new_d = _valid_day(d.get("day") or row["day"])
    if not new_d:
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if new_d == today() and new_t > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400

    old_d, old_t = row["day"], (row[col] or "")
    if old_d == new_d and old_t == new_t:
        return jsonify(ok=True, unchanged=True)

    if tbl == "doses" and row["sched_id"] and new_d != old_d:
        on = db().execute(
            "SELECT 1 FROM med_schedule WHERE id=? AND valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)",
            (row["sched_id"], new_d, new_d)).fetchone()
        if not on:
            return jsonify(ok=False, err="That medicine was not scheduled on " + new_d + "."), 400
        clash = db().execute(
            "SELECT 1 FROM doses WHERE sched_id=? AND day=? AND id<>?",
            (row["sched_id"], new_d, rid)).fetchone()
        if clash:
            return jsonify(ok=False, err="Already logged on " + new_d + " - undo that one first."), 409

    db().execute("UPDATE " + tbl + " SET day=?, " + col + "=? WHERE id=?",
                 (new_d, new_t, rid))
    db().execute(
        "INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
        "VALUES(?,?,?,?,?,?,?)", (tbl, rid, old_d, old_t, new_d, new_t, now_s()))
    db().commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ stock / feed
# GUTLOG_V360_PHASE_C -- stock is derived from events at read time; the
# feed gives RxGuard and FitLog read-only access to what was taken.
_STRENGTH_UNITS = ("mg", "mcg", "µg", "ug", "g", "iu", "%")


def _units(dose_text):
    """Tablets (or puffs, sachets) per dose from free text. '1 tab' -> 1,
    '2 caps' -> 2, '1/2' or a half sign -> 0.5. A strength ('40 mg') is not
    a count, so it reads as 1."""
    s = (dose_text or "").strip().lower().replace("½", "0.5")
    if not s:
        return 1.0
    parts = s.split()
    if len(parts) > 1 and parts[1] in _STRENGTH_UNITS:
        return 1.0
    tok = parts[0]
    try:
        if "/" in tok:
            a, b = tok.split("/", 1)
            v = float(a) / float(b)
        else:
            v = float(tok)
    except (ValueError, ZeroDivisionError):
        return 1.0
    return v if 0 < v <= 20 else 1.0


def _now_at():
    return today() + " " + now_hm()


def _stock_rows():
    con = db()
    tday = today()
    since14 = (date.fromisoformat(tday) - timedelta(days=13)).isoformat()
    meds = con.execute(
        "SELECT id, name, pack_size FROM prnmeds WHERE active=1 ORDER BY sort, id").fetchall()
    name_to_id = dict((r["name"], r["id"]) for r in con.execute(
        "SELECT id, name FROM prnmeds").fetchall())
    sched = {}
    for l in con.execute(
            "SELECT med_id, dose_text, variants FROM med_schedule WHERE valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)", (tday, tday)).fetchall():
        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False})
        if (l["variants"] or "").strip():
            s["variants"] = True
        else:
            s["units"] += _units(l["dose_text"])
    modes = dict((r["med_id"], r["mode"]) for r in con.execute(
        "SELECT med_id, mode FROM stock_meds").fetchall())
    ev = {}
    for e in con.execute(
            "SELECT id, med_id, kind, qty, at FROM stock_events ORDER BY at, id").fetchall():
        ev.setdefault(e["med_id"], []).append(e)
    first = [v[0]["at"][:10] for v in ev.values() if v]
    lo = min(first + [since14])
    by_med = {}
    for d in con.execute(
            "SELECT med_id, medicine, day, dtime, status, sched_id, dose_text FROM doses "
            "WHERE day>=? AND COALESCE(status,'')<>'SKIPPED'", (lo,)).fetchall():
        mid = d["med_id"] or name_to_id.get(d["medicine"])
        if mid:
            by_med.setdefault(mid, []).append(d)

    rows = []
    for m in meds:
        mid = m["id"]
        s = sched.get(mid)
        fixed = bool(s and s["units"] > 0)
        mode = modes.get(mid) or ("pillbox" if fixed else "per_dose")
        if mode == "pillbox" and not fixed:
            mode = "per_dose"
        r = {"med_id": mid, "name": m["name"], "mode": mode, "can_pillbox": fixed,
             "pack_size": m["pack_size"] or 0, "tracked": False,
             "trackable": not (s and s["variants"]), "current": None,
             "per_day": 0.0, "days_left": None, "level": "", "why": "", "counted_at": ""}
        if s and s["variants"]:
            r["why"] = "strengths vary - not tracked"
            rows.append(r)
            continue
        doses = by_med.get(mid, [])
        uses = [d for d in doses if mode == "per_dose" or not d["sched_id"]]
        if mode == "pillbox":
            per_day = s["units"]
        else:
            per_day = sum(_units(d["dose_text"]) for d in uses if d["day"] >= since14) / 14.0
        r["per_day"] = round(per_day, 2)
        evs = ev.get(mid, [])
        counts = [e for e in evs if e["kind"] == "COUNT"]
        if not counts:
            rows.append(r)
            continue
        c = counts[-1]
        cur = float(c["qty"])
        for e in evs:
            if (e["at"], e["id"]) <= (c["at"], c["id"]):
                continue
            if e["kind"] == "ADD":
                cur += e["qty"]
            elif e["kind"] == "FILL":
                cur -= e["qty"]
        for d in uses:
            if ((d["day"] or "") + " " + (d["dtime"] or "00:00")) > c["at"]:
                cur -= _units(d["dose_text"])
        r.update(tracked=True, current=round(cur, 1), counted_at=c["at"])
        dl = (cur / per_day) if per_day > 0 else None
        if dl is not None:
            r["days_left"] = round(max(dl, 0), 1)
        if mode == "pillbox":
            need = per_day * 7
            if cur <= 0:
                r["level"], r["why"] = "RED", "none left for the next fill"
            elif cur < need:
                r["level"], r["why"] = "RED", "will not cover the next 7-day fill"
            elif cur < 2 * need:
                r["level"], r["why"] = "AMBER", "enough for one more fill"
        else:
            if cur <= 0:
                r["level"] = "RED" if per_day > 0 else "AMBER"
                r["why"] = "none left"
            elif dl is not None and dl < 3:
                r["level"], r["why"] = "RED", "about %d days left" % int(dl + 0.5)
            elif dl is not None and dl < 7:
                r["level"], r["why"] = "AMBER", "about %d days left" % int(dl + 0.5)
        rows.append(r)
    return rows


def _stock_qty(d, lo, hi):
    try:
        v = float(d.get("qty"))
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


@app.route("/api/stock")
@login_required
def api_stock():
    rows = _stock_rows()
    order = {"RED": 0, "AMBER": 1, "": 2}
    rows.sort(key=lambda r: (order.get(r["level"], 2), not r["tracked"], not r["trackable"]))
    lf = db().execute("SELECT at FROM stock_events WHERE kind='FILL' "
                      "ORDER BY at DESC, id DESC LIMIT 1").fetchone()
    preview = [{"med_id": r["med_id"], "name": r["name"], "per_day": r["per_day"]}
               for r in rows if r["tracked"] and r["mode"] == "pillbox" and r["per_day"] > 0]
    return jsonify(rows=rows, last_fill=lf["at"] if lf else "", fill_preview=preview)


def _stock_med(d):
    try:
        mid = int(d.get("med_id"))
    except (TypeError, ValueError):
        return None
    r = db().execute("SELECT id FROM prnmeds WHERE id=?", (mid,)).fetchone()
    return mid if r else None


@app.route("/api/stock/count", methods=["POST"])
@login_required
def api_stock_count():
    d = J()
    mid = _stock_med(d)
    q = _stock_qty(d, 0, 100000)
    if not mid or q is None:
        return jsonify(ok=False, err="Enter how many are left."), 400
    db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) VALUES(?,?,?,?,?,?)",
                 (mid, "COUNT", q, _now_at(), "", now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/stock/add", methods=["POST"])
@login_required
def api_stock_add():
    d = J()
    mid = _stock_med(d)
    q = _stock_qty(d, 0.5, 100000)
    if not mid or q is None:
        return jsonify(ok=False, err="Enter how many were bought."), 400
    if not db().execute("SELECT 1 FROM stock_events WHERE med_id=? AND kind='COUNT'",
                        (mid,)).fetchone():
        return jsonify(ok=False, err="Count what is left first, then add purchases."), 400
    db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) VALUES(?,?,?,?,?,?)",
                 (mid, "ADD", q, _now_at(), "", now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/stock/mode", methods=["POST"])
@login_required
def api_stock_mode():
    d = J()
    mid = _stock_med(d)
    mode = d.get("mode")
    if not mid or mode not in ("pillbox", "per_dose"):
        return jsonify(ok=False, err="Bad mode."), 400
    db().execute("INSERT INTO stock_meds(med_id, mode) VALUES(?,?) "
                 "ON CONFLICT(med_id) DO UPDATE SET mode=excluded.mode", (mid, mode))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/stock/fill", methods=["POST"])
@login_required
def api_stock_fill():
    d = J()
    try:
        days = int(d.get("days") or 7)
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= 31:
        return jsonify(ok=False, err="Days must be 1 to 31."), 400
    items = [r for r in _stock_rows()
             if r["tracked"] and r["mode"] == "pillbox" and r["per_day"] > 0]
    if not items:
        return jsonify(ok=False, err="No counted pillbox medicines - count them first."), 400
    at, batch = _now_at(), "fill:" + now_s() + ":" + secrets.token_hex(4)
    for r in items:
        db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) "
                     "VALUES(?,?,?,?,?,?)",
                     (r["med_id"], "FILL", round(r["per_day"] * days, 2), at, batch, now_s()))
    db().commit()
    return jsonify(ok=True, n=len(items), days=days)


@app.route("/api/stock/fill/undo", methods=["POST"])
@login_required
def api_stock_fill_undo():
    r = db().execute("SELECT note FROM stock_events WHERE kind='FILL' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return jsonify(ok=False, err="No fill to undo."), 400
    db().execute("DELETE FROM stock_events WHERE kind='FILL' AND note=?", (r["note"],))
    db().commit()
    return jsonify(ok=True)


# ---- feed: read-only, bearer-gated, loopback consumers (RxGuard, FitLog)
def _feed_token():
    p = os.environ.get("GUTLOG_FEED_TOKEN_FILE") or os.path.join(BASE, "feed.token")
    try:
        with open(p, "r") as f:
            t = f.read().strip()
        if len(t) >= 32:
            return t
    except OSError:
        pass
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            with open(p, "r") as f:
                t = f.read().strip()
            return t if len(t) >= 32 else None
        except OSError:
            return None
    except OSError:
        return None
    t = secrets.token_hex(32)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


_FEED_TOKEN = _feed_token()


def feed_required(fn):
    import hmac

    @wraps(fn)
    def w(*a, **k):
        tok = _FEED_TOKEN
        got = (request.headers.get("Authorization") or "")
        got = got[7:].strip() if got.startswith("Bearer ") else ""
        if not tok or not got or not hmac.compare_digest(tok, got):
            return jsonify(ok=False, err="unauthorised"), 401
        return fn(*a, **k)
    return w


def _feed_since(default_days):
    s = _valid_day(request.args.get("since"))
    floor = (date.today() - timedelta(days=180)).isoformat()
    if not s:
        s = (date.today() - timedelta(days=default_days - 1)).isoformat()
    return max(s, floor)


def _feed_events(since):
    meds = dict((r["id"], r) for r in db().execute(
        "SELECT id, name, molecule FROM prnmeds").fetchall())
    by_name = dict((r["name"], r) for r in meds.values())
    out = []
    for d in db().execute(
            "SELECT day, dtime, medicine, med_id, status, sched_id, dose_text FROM doses "
            "WHERE day>=? AND COALESCE(status,'')<>'SKIPPED' ORDER BY day, dtime",
            (since,)).fetchall():
        m = meds.get(d["med_id"]) or by_name.get(d["medicine"])
        out.append({"day": d["day"], "time": d["dtime"] or "",
                    "name": m["name"] if m else d["medicine"],
                    "molecule": (m["molecule"] or "") if m else "",
                    "med_id": m["id"] if m else None,
                    "scheduled": bool(d["sched_id"]),
                    "dose_text": d["dose_text"] or ""})
    return out


@app.route("/api/feed/doses")
@feed_required
def api_feed_doses():
    since = _feed_since(14)
    return jsonify(ok=True, app="gutlog", since=since, events=_feed_events(since))


@app.route("/api/feed/stack")
@feed_required
def api_feed_stack():
    try:
        days = max(1, min(90, int(request.args.get("days") or 14)))
    except ValueError:
        days = 14
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    tday = today()
    regimen = [dict(r) for r in db().execute(
        "SELECT p.id AS med_id, p.name, p.molecule, s.slot, s.dose_text, s.variants "
        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id WHERE s.valid_from<=? "
        "AND (s.valid_to='' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",
        (tday, tday)).fetchall()]
    taken = {}
    for e in _feed_events(since):
        k = e["med_id"] or e["name"]
        t = taken.setdefault(k, {"med_id": e["med_id"], "name": e["name"],
                                 "molecule": e["molecule"], "doses": 0, "days": set(),
                                 "last_day": "", "scheduled": False})
        t["doses"] += 1
        t["days"].add(e["day"])
        t["last_day"] = max(t["last_day"], e["day"])
        t["scheduled"] = t["scheduled"] or e["scheduled"]
    out = []
    for t in taken.values():
        t["days"] = len(t["days"])
        out.append(t)
    out.sort(key=lambda t: (-t["days"], t["name"]))
    return jsonify(ok=True, app="gutlog", since=since, days=days, today=tday,
                   regimen=regimen, taken=out)


# ------------------------------------------------------------------ review
@app.route("/api/review")
@login_required
def api_review():
    days_n = int(request.args.get("days", 30))
    since = (date.today() - timedelta(days=days_n)).isoformat()
    q = lambda sql: [dict(r) for r in db().execute(sql, (since,)).fetchall()]
    meals = q("SELECT * FROM meals WHERE day>=? ORDER BY day DESC, mtime DESC")
    for m in meals: m["items"] = json.loads(m["items"] or "[]")
    daily = [dict(r) for r in db().execute("""
        SELECT day, ROUND(SUM(protein),1) protein, ROUND(SUM(fscore),2) fscore
        FROM meals WHERE day>=? GROUP BY day ORDER BY day""", (since,))]
    dosecount = [dict(r) for r in db().execute(
        "SELECT day, COUNT(*) n FROM doses WHERE day>=? GROUP BY day", (since,))]
    registry = [dict(r) for r in db().execute(
        "SELECT item, status FROM library WHERE status IN ('cleared','trigger') ORDER BY status, item")]
    return jsonify(
        days=q("SELECT * FROM days WHERE day>=? ORDER BY day"),
        episodes=q("SELECT * FROM episodes WHERE day>=? ORDER BY day DESC, etime DESC"),
        meals=meals,
        doses=q("SELECT * FROM doses WHERE day>=? ORDER BY day DESC, dtime DESC"),
        vitals=q("SELECT * FROM vitals WHERE day>=? ORDER BY day DESC, id DESC"),
        foodtests=q("SELECT * FROM foodtests WHERE day>=? ORDER BY day DESC, id DESC"),
        patches=[dict(r) for r in db().execute("SELECT * FROM patches ORDER BY day_on DESC")],
        consults=[dict(r) for r in db().execute("SELECT * FROM consults ORDER BY day DESC")],
        courses=[dict(r) for r in db().execute("SELECT * FROM courses ORDER BY start_day DESC")],
        files=[dict(r) for r in db().execute("SELECT id,day,ftype,label,orig,size FROM files ORDER BY day DESC, id DESC")],
        daily=daily, dosecount=dosecount, registry=registry, target=PROTEIN_TARGET)

# ------------------------------------------------------------------ export
@app.route("/export/<table>.csv")
@login_required
def export_csv(table):
    cols = {
        "days": "day,syms,pain,pain_site,bristol,stools,tea,coffee,sleep,walk,treadmill,meditation,notes",
        "meals": "day,mtime,slot,items,protein,kcal,fibre,fscore,notes",
        "doses": "day,dtime,medicine,reason,effect,notes",
        "patches": "strength,day_on,time_on,day_off,time_off,notes",
        "episodes": "day,etime,category,etype,side,severity,duration,notes",
        "vitals": "day,vtime,sys,dia,pulse,temp,weight,waist,notes",
        "foodtests": "day,food,portion,symptoms,severity,verdict,notes",
        "courses": "drug,start_day,end_day,response,notes",
        "consults": "day,doctor,reason,advice,changes,next_visit",
        "labs": "day,analyte,value",
        "library": "cat,item,portion,protein,kcal,fibre,fodmap,status,tags,fav,note",
    }.get(table)
    if not cols: abort(404)
    order = {"courses": "start_day", "patches": "day_on", "library": "cat,item"}.get(table, "day")
    rows = db().execute(f"SELECT {cols} FROM {table} ORDER BY {order}").fetchall()
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(cols.split(","))
    for r in rows:
        vals = [r[c] for c in cols.split(",")]
        if table == "meals":
            i = cols.split(",").index("items")
            try:
                vals[i] = "; ".join(f'{it["n"]} x{it["q"]:g}' for it in json.loads(vals[i] or "[]"))
            except (ValueError, KeyError, TypeError): pass
        w.writerow(vals)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={table}_{today()}.csv"})

@app.route("/")
@login_required
def home():
    return render_template_string(APP_PAGE, course_chips=_course_chips())

# ------------------------------------------------------------------ auth page
AUTH_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GutLog</title>
<style>
:root{--ink:#1D2F33;--teal:#0B6E6E;--bg:#EFF5F2;--card:#fff;--line:#D5E3DD;--err:#B3372A;--amber:#C8860A}
*{box-sizing:border-box}body{margin:0;color:var(--ink);
background:radial-gradient(1200px 600px at 50% -10%,#DFF0EA,var(--bg));
font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;display:grid;place-items:center;min-height:100vh}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:30px;width:min(92vw,370px);
box-shadow:0 18px 50px rgba(11,110,110,.14)}
.mark{width:52px;height:52px;border-radius:15px;display:grid;place-items:center;font-size:27px;
background:linear-gradient(135deg,#0B6E6E,#12907C);margin:0 0 14px;box-shadow:0 6px 16px rgba(11,110,110,.35)}
h1{font-size:24px;margin:0 0 2px;letter-spacing:-.4px}h1 b{color:var(--teal)}
p{margin:4px 0 18px;color:#5B7370;font-size:14px}
input{width:100%;padding:13px 14px;font-size:16px;border:1.5px solid var(--line);border-radius:12px;margin-bottom:12px}
input:focus{outline:2px solid var(--teal);border-color:var(--teal)}
button{width:100%;padding:14px;font-size:16px;font-weight:700;border:0;border-radius:12px;
background:linear-gradient(135deg,#0B6E6E,#12907C);color:#fff;cursor:pointer}
.err{color:var(--err);font-size:14px;margin:0 0 12px}
</style></head><body><form class="card" method="post">
<div class="mark">&#127807;</div>
<h1>Gut<b>Log</b> <span style="font-size:13px;color:var(--amber);font-weight:700">v3</span></h1>
{% if mode=='setup' %}<p>First run - create the password for this diary.</p>
{% if err %}<p class="err">{{err}}</p>{% endif %}
<input type="password" name="pw" placeholder="New password (8+ characters)" autofocus>
<input type="password" name="pw2" placeholder="Repeat password">
<button>Create password</button>
{% else %}<p>Personal health diary - Dr. Manoj Agarwal</p>
{% if err %}<p class="err">{{err}}</p>{% endif %}
<input type="password" name="pw" placeholder="Password" autofocus>
<button>Unlock</button>{% endif %}
</form></body></html>"""

# ------------------------------------------------------------------ account page
ACCOUNT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0B6E6E"><title>GutLog - Account</title>
<style>
:root{--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;--bg:#EFF5F2;--card:#fff;
--line:#D5E3DD;--muted:#5B7370;--err:#B3372A;--ok:#2E7D32;--amber:#C8860A;
--grad:linear-gradient(135deg,#0B6E6E,#12907C)}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--bg);
font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;
padding-bottom:calc(24px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;background:var(--grad);color:#fff;
padding:calc(12px + env(safe-area-inset-top)) 16px 12px;display:flex;align-items:center;gap:10px;
box-shadow:0 2px 14px rgba(8,79,79,.25)}
header h1{font-size:19px;margin:0;font-weight:800;letter-spacing:-.4px}
header h1 b{color:#FFD98A}
header a{margin-left:auto;color:#fff;opacity:.9;font-size:13.5px;text-decoration:none}
main{padding:16px 14px 0;max-width:520px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin:0 0 14px;
box-shadow:0 1px 2px rgba(29,47,51,.05)}
h2{font-size:16px;margin:0 0 4px;letter-spacing:-.2px}
.sub{margin:0 0 14px;color:var(--muted);font-size:13.5px}
label{display:block;font-size:12.5px;font-weight:700;color:var(--muted);
text-transform:uppercase;letter-spacing:.6px;margin:0 0 6px}
input[type=password]{width:100%;padding:12px 13px;font-size:16px;border:1.5px solid var(--line);
border-radius:11px;margin-bottom:12px}
input:focus{outline:2px solid var(--teal);border-color:var(--teal)}
.chk{display:flex;align-items:flex-start;gap:9px;margin:2px 0 14px;font-size:14px;color:var(--ink)}
.chk input{width:18px;height:18px;margin-top:2px}
button{width:100%;padding:13px;font-size:15.5px;font-weight:700;border:0;border-radius:11px;
background:var(--grad);color:#fff;cursor:pointer}
button.warn{background:#fff;color:var(--err);border:1.5px solid var(--err)}
.msg{padding:11px 13px;border-radius:11px;font-size:14px;margin:0 0 14px}
.msg.ok{background:#E6F3E9;color:var(--ok);border:1px solid #BFE0C7}
.msg.err{background:#FBEDEC;color:var(--err);border:1px solid #F0CDC9}
.note{font-size:12.5px;color:var(--muted);margin:10px 0 0;line-height:1.45}
</style></head><body>
<div style="display:flex;gap:8px;padding:8px 12px 4px;font-size:13.5px">
<a href="https://rx.dr-manoj.in" style="padding:4px 12px;border-radius:14px;background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">RxGuard</a>
<a href="/" style="padding:4px 12px;border-radius:14px;background:#0B6E6E;color:#fff;text-decoration:none;font-weight:700">GutLog</a>
<a href="https://fit.dr-manoj.in" style="padding:4px 12px;border-radius:14px;background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">FitLog</a>
</div>
<header><h1>Gut<b>Log</b> - Account</h1><a href="/">&larr; Back to diary</a></header>
<main>
{% if msg %}<div class="msg ok">{{msg}}</div>{% endif %}
{% if err %}<div class="msg err">{{err}}</div>{% endif %}

{% if not have_owner %}
<form class="card" method="post">
  <input type="hidden" name="action" value="set_owner">
  <h2>Create your owner key</h2>
  <p class="sub">The owner key is a second, private secret - separate from the
    login password. From now on, only someone with this key can change the
    password or sign out devices. Set it now, keep it to yourself, and don't
    share it. This is a one-time step.</p>
  <label>Owner key</label>
  <input type="password" name="okey" autocomplete="new-password" placeholder="Owner key (8+ characters)">
  <label>Repeat owner key</label>
  <input type="password" name="okey2" autocomplete="new-password" placeholder="Repeat owner key">
  <button type="submit">Set owner key</button>
  <p class="note">Make it different from the diary's login password. If you ever
    forget it, it can only be reset from the server.</p>
</form>
{% else %}

<form class="card" method="post">
  <input type="hidden" name="action" value="password">
  <h2>Change password</h2>
  <p class="sub">Sets a new unlock password for the diary. Requires your owner key.</p>
  <label>Owner key</label>
  <input type="password" name="owner" autocomplete="off" placeholder="Your owner key">
  <label>New password</label>
  <input type="password" name="new" autocomplete="new-password" placeholder="New password (8+ characters)">
  <label>Repeat new password</label>
  <input type="password" name="new2" autocomplete="new-password" placeholder="Repeat new password">
  <div class="chk">
    <input type="checkbox" name="signout_others" id="so" value="1" checked>
    <label for="so" style="text-transform:none;letter-spacing:0;font-weight:500;color:var(--ink);margin:0">
      Also sign out all other devices (recommended)</label>
  </div>
  <button type="submit">Change password</button>
</form>

<form class="card" method="post">
  <input type="hidden" name="action" value="logout_others">
  <h2>Sign out other devices</h2>
  <p class="sub">Signs out every phone, tablet, or browser currently logged in -
    except the one you are using now. No password change, nothing lost.
    Requires your owner key.</p>
  <label>Owner key</label>
  <input type="password" name="owner" autocomplete="off" placeholder="Your owner key">
  <button type="submit" class="warn">Sign out all other devices</button>
  <p class="note">Use this if you handed the URL to someone, lost a device, or
    just want a clean slate. Everyone else will need the password to get back in.</p>
</form>

<form class="card" method="post">
  <input type="hidden" name="action" value="change_owner">
  <h2>Change owner key</h2>
  <p class="sub">Rotate the owner key itself. Requires the current one.</p>
  <label>Current owner key</label>
  <input type="password" name="owner" autocomplete="off" placeholder="Current owner key">
  <label>New owner key</label>
  <input type="password" name="nokey" autocomplete="new-password" placeholder="New owner key (8+ characters)">
  <label>Repeat new owner key</label>
  <input type="password" name="nokey2" autocomplete="new-password" placeholder="Repeat new owner key">
  <button type="submit">Change owner key</button>
</form>
{% endif %}
</main></body></html>"""

# ------------------------------------------------------------------ app page
APP_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0B6E6E"><meta name="apple-mobile-web-app-capable" content="yes">
<title>GutLog</title>
""" + PWA_HEAD_SNIPPET + r"""

<style>
:root{--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;--teal-d:#084F4F;--bg:#EFF5F2;--card:#fff;
--line:#D5E3DD;--muted:#5B7370;--chip:#E6F0EC;--err:#B3372A;--ok:#2E7D32;--amber:#C8860A;
--amber-bg:#FBF1DC;--hip:#7A4FBF;--patch:#EFE7FB;
--fmL:#2E7D32;--fmLM:#7CA53A;--fmM:#C8860A;--fmMH:#C2622B;--fmH:#B3372A;
--grad:linear-gradient(135deg,#0B6E6E,#12907C)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif;
padding-bottom:calc(80px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;z-index:5;background:var(--grad);color:#fff;
padding:calc(10px + env(safe-area-inset-top)) 16px 10px;display:flex;align-items:center;gap:10px;
box-shadow:0 2px 14px rgba(8,79,79,.25)}
header h1{font-size:20px;margin:0;font-weight:800;letter-spacing:-.4px}
header h1 b{color:#FFD98A}
header .v{font-size:11px;background:rgba(255,255,255,.18);padding:2px 8px;border-radius:999px}
header .day{margin-left:auto;font-size:12.5px;opacity:.92;text-align:right;line-height:1.25}
header .streak{font-weight:700;color:#FFD98A}
header a{color:#fff;opacity:.85;font-size:13px;text-decoration:none;margin-left:12px}
main{padding:12px 14px 0;max-width:640px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin:0 0 12px;
box-shadow:0 1px 2px rgba(29,47,51,.04)}
.q{font-size:12.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin:0 0 8px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{border:1.5px solid var(--line);background:var(--chip);color:var(--ink);border-radius:999px;
padding:9px 14px;font-size:15px;cursor:pointer;user-select:none;transition:transform .06s}
.chip:active{transform:scale(.96)}
.chip.sel{background:var(--teal);border-color:var(--teal);color:#fff;font-weight:600}
.chip.trigger{border-color:var(--err);color:var(--err);background:#FBEDEC}
.chip:focus-visible{outline:2px solid var(--teal-d);outline-offset:2px}
.symgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sym{border:1.5px solid var(--line);background:var(--chip);border-radius:13px;padding:10px 10px;
display:flex;align-items:center;gap:9px;font-size:14.5px;cursor:pointer;user-select:none}
.sym i{font-style:normal;font-size:19px}
.sym.sel{background:var(--teal);border-color:var(--teal);color:#fff;font-weight:600}
.bristol{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.btile{border:1.5px solid var(--line);background:var(--chip);border-radius:10px;padding:8px 2px;text-align:center;cursor:pointer}
.btile.sel{background:var(--teal);border-color:var(--teal);color:#fff}
.btile b{display:block;font-size:17px}.btile span{font-size:10px;line-height:1.1;display:block}
.scale{display:grid;grid-template-columns:repeat(11,1fr);gap:5px}
.scale .chip{padding:9px 0;text-align:center;border-radius:9px}
.step{display:flex;align-items:center;gap:14px}
.step button{width:44px;height:44px;border-radius:13px;border:1.5px solid var(--line);background:var(--chip);
font-size:22px;font-weight:700;color:var(--teal);cursor:pointer}
.step b{font-size:26px;min-width:34px;text-align:center}
.step .cap{font-size:12px;color:var(--muted);line-height:1.3}
.step b.warnA{color:var(--amber)}.step b.warnH{color:var(--err)}
textarea,input[type=number],input[type=date],input[type=time],input[type=text],select{
width:100%;padding:11px 12px;font-size:16px;border:1.5px solid var(--line);border-radius:11px;background:#fff;color:var(--ink)}
textarea{resize:vertical;min-height:44px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.lbl{font-size:12px;color:var(--muted);margin:0 0 4px}
.seg{display:flex;gap:6px;margin:0 0 12px;background:var(--chip);padding:4px;border-radius:13px}
.seg button{flex:1;border:0;border-radius:10px;padding:9px 4px;font-size:13.5px;font-weight:700;background:none;color:var(--muted);cursor:pointer}
.seg button.sel{background:#fff;color:var(--teal);box-shadow:0 1px 3px rgba(0,0,0,.12)}
.sub{display:none}.sub.sel{display:block}
.save{position:fixed;left:0;right:0;bottom:calc(60px + env(safe-area-inset-bottom));display:flex;justify-content:center;pointer-events:none;z-index:6}
.save button{pointer-events:auto;border:0;border-radius:999px;background:var(--grad);color:#fff;font-size:16px;font-weight:800;
padding:14px 36px;box-shadow:0 8px 22px rgba(11,110,110,.4);cursor:pointer}
.save button.done{background:var(--ok)}
nav{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);display:flex;
padding-bottom:env(safe-area-inset-bottom);z-index:7}
nav button{flex:1;border:0;background:none;padding:8px 0 7px;font-size:10.5px;color:var(--muted);cursor:pointer;font-weight:600}
nav button i{display:grid;place-items:center;font-style:normal;font-size:19px;margin:0 auto 2px;
width:44px;height:28px;border-radius:999px}
nav button.sel{color:var(--teal-d)}
nav button.sel i{background:#DCEBE4}
.tab{display:none}.tab.sel{display:block}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.doserow{display:flex;align-items:center;gap:10px;padding:11px 12px;border:1.5px solid var(--line);
background:#fff;border-radius:13px;margin:0 0 8px;cursor:pointer;transition:transform .06s}
.doserow:active{transform:scale(.985)}
.doserow .nm{flex:1;min-width:0}
.doserow .nm b{display:block;font-size:15.5px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.doserow .nm span{font-size:12px;color:var(--muted)}
.doserow .tick{width:30px;height:30px;border-radius:50%;border:2px solid var(--line);display:grid;
place-items:center;font-size:15px;color:transparent;flex:0 0 auto}
.doserow.done{background:#F2F8F5;border-color:#BEDCCB}
.doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#fff}
.doserow.skip{background:#FBF1DC;border-color:#EAD3A0}
.doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#fff;font-size:13px}
.doserow .sk{border:0;background:none;color:var(--muted);font-size:12px;padding:6px 4px;
text-decoration:underline;cursor:pointer;flex:0 0 auto}
.slothd{display:flex;align-items:center;gap:8px;margin:0 0 8px}
.slothd .q{margin:0}
.slothd .cnt{margin-left:auto;font-size:12px;color:var(--muted);font-weight:700}
.exrow{display:flex;gap:8px;align-items:center;font-size:13.5px;padding:5px 0;border-bottom:1px dashed var(--line)}
.exrow:last-child{border-bottom:0}
.exrow .t{color:var(--muted);font-size:12px;flex:0 0 auto}
.exrow .u{margin-left:auto;border:0;background:none;color:var(--err);font-size:12px;cursor:pointer;text-decoration:underline}
.schrow{display:flex;gap:8px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line);font-size:14.5px}
.schrow:last-child{border-bottom:0}
.schrow .s{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;flex:0 0 62px}
.schrow .x{margin-left:auto;border:0;background:none;color:var(--err);font-size:12px;cursor:pointer;text-decoration:underline}
/* GUTLOG_V331_LAYOUT -- grid items default to min-width:auto, which for an
   <input> is its size-attribute min-content width, not its CSS width. That
   pushed .row3 past the viewport and clipped the page. Root-cause fix; no
   overflow-x:hidden, which would only hide the next one of these. */
.varpick{padding:10px 12px;border:1.5px dashed var(--teal);border-radius:13px;margin:0 0 8px;background:#F7FBF9}
.varpick .vt{font-size:12.5px;color:var(--muted);margin:0 0 8px;font-weight:600}
.varpick .vrow{display:flex;flex-wrap:wrap;gap:7px}
.varpick .vb{display:flex;gap:8px;margin-top:10px}
.varpick .vb button{flex:1;padding:10px;border-radius:11px;border:1.5px solid var(--line);background:#fff;font-size:14.5px;font-weight:600;cursor:pointer}
.varpick .vb button.go{background:var(--teal);color:#fff;border-color:var(--teal)}
.varpick .vb button.danger{background:var(--err);color:#fff;border-color:var(--err)}
.varpick .vb.three button{font-size:13.5px;padding:10px 4px}
.doserow .sk{padding:8px 6px;font-size:12.5px}
.row2,.row3{min-width:0}
.row2>*,.row3>*{min-width:0}
.row2 input,.row3 input,.row2 select,.row3 select{min-width:0}
/* GUTLOG_V340_READABILITY -- softer ground, heavier text, real buttons.
   The previous pairing was a bright cool mint behind pure white cards:
   high contrast, and tiring for something opened at 5am and again late. */
body{background:#E9EDE7;font-size:17px;line-height:1.5}
main{padding:14px 14px 0}
.card{background:#FCFCF9;border:1px solid #CFDBD3;border-radius:18px;padding:16px;
margin:0 0 14px;box-shadow:0 1px 3px rgba(29,47,51,.06)}
.q{font-size:15px;font-weight:800;color:var(--ink);text-transform:none;
letter-spacing:-.1px;margin:0 0 12px}
.lbl{font-size:13.5px;font-weight:600;color:var(--muted);margin:0 0 7px}
.hint{font-size:13.5px}
.chip{padding:11px 16px;font-size:16px;font-weight:600;border-width:2px;
background:#EDF3EF;border-color:#CBDCD3}
.chip.num{min-width:46px;text-align:center;padding:11px 8px}
.chip.sel{background:var(--teal);border-color:var(--teal);color:#fff;font-weight:700}
.chip.just{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:700}
.chip.just::after{content:" \2713"}

/* buttons that look like buttons */
.btn{border:2px solid var(--line);background:#fff;color:var(--ink);
border-radius:13px;padding:13px 18px;font-size:16px;font-weight:700;
cursor:pointer;font-family:inherit}
.btn.primary{background:var(--teal);border-color:var(--teal);color:#fff;width:100%}
.btn.ghost{background:#fff;color:var(--teal);border-color:#BAD2C8}
.btn.tiny{padding:7px 13px;font-size:13.5px;border-radius:10px;
color:var(--err);border-color:#E4C3BE}
.btn:active{transform:scale(.98)}
.btnrow{display:flex;gap:9px;margin-top:12px;flex-wrap:wrap}
.btnrow .btn{flex:1;min-width:140px}

/* collapsible cards */
.card.fold{padding:0;overflow:hidden}
.card.fold .fold-h{width:100%;display:flex;align-items:center;gap:10px;
padding:16px;border:0;background:none;font-family:inherit;font-size:16px;
font-weight:800;color:var(--ink);cursor:pointer;text-align:left}
.card.fold .ft{flex:0 0 auto}
.card.fold .fs{margin-left:auto;font-size:13.5px;font-weight:600;color:var(--muted)}
.card.fold .fc{width:11px;height:11px;border-right:2.5px solid var(--muted);
border-bottom:2.5px solid var(--muted);transform:rotate(45deg) translate(-3px,-3px);
transition:transform .18s;flex:0 0 auto}
.card.fold.open .fc{transform:rotate(-135deg) translate(-3px,-3px)}
.card.fold .cbody{display:none;padding:0 16px 16px}
.card.fold.open .cbody{display:block}
.card.fold.pending .fs{color:var(--amber);font-weight:700}

/* dose rows */
.doserow{padding:13px 14px;border-width:2px;border-radius:14px;margin-bottom:9px}
.doserow .nm b{font-size:16.5px;font-weight:700}
.doserow .nm span{font-size:13px}
.doserow .tick{width:32px;height:32px;border-width:2.5px}
.doserow .sk{font-size:13px;font-weight:600;padding:9px 6px}
.slothd{margin:14px 0 9px;align-items:baseline}
.slothd .sl{font-size:14px;font-weight:800;color:var(--teal-d);
text-transform:uppercase;letter-spacing:.6px}
.slothd .cnt{margin-left:auto;font-size:13.5px;font-weight:700;color:var(--muted)}
.exrow{padding:9px 0;font-size:15px;gap:10px}
.exrow .t{font-size:13px;font-weight:600}
.exrow .m{font-weight:600}
.exrow .u{margin-left:auto}
@media (max-width:430px){
  body{font-size:16.5px}
  .card{padding:14px}
  .card.fold .fold-h{padding:14px}
  .card.fold .cbody{padding:0 14px 14px}
  .chip{padding:10px 14px;font-size:15.5px}
  .btnrow .btn{min-width:0}
}
@media (max-width:430px){
  main{padding:10px 10px 0}
  .card{padding:11px;border-radius:14px;margin-bottom:10px}
  .chip{padding:8px 12px;font-size:14.5px}
  #n_symSev .chip,#n_symBristol .chip{padding:8px 0;min-width:34px;text-align:center}
  .row3{gap:7px}
  .row3 .lbl{font-size:11px}
  .doserow{padding:10px}
  .doserow .nm b{font-size:15px}
  nav button{font-size:10px}
}
.hint{font-size:13px;color:var(--muted);margin:2px 2px 12px}
/* completion ring strip */
.rings{display:flex;justify-content:space-between;background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:12px 10px;margin:0 0 12px;box-shadow:0 1px 2px rgba(29,47,51,.04)}
.ring{display:grid;justify-items:center;gap:3px;border:0;background:none;cursor:pointer;padding:0 4px;font:inherit;color:inherit}
.ring svg{width:46px;height:46px}
.ring .rl{font-size:10.5px;color:var(--muted);font-weight:700}
.ring .ic{font-size:15px}
/* fodmap dots + meter */
.fd{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:0;margin-right:6px}
.meter{height:9px;border-radius:999px;background:linear-gradient(90deg,var(--fmL),var(--fmM) 50%,var(--fmH));position:relative;margin:7px 0 3px}
.meter i{position:absolute;top:-4px;width:17px;height:17px;border-radius:50%;background:#fff;border:3px solid var(--ink);transform:translateX(-50%)}
.pbar{height:9px;border-radius:999px;background:var(--chip);overflow:hidden;margin:7px 0 3px}
.pbar i{display:block;height:100%;background:var(--grad);border-radius:999px;transition:width .25s}
.tot{font-size:13px;color:var(--muted)}
.tot b{color:var(--ink)}
/* meal basket */
.bk{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px dashed var(--line);font-size:14.5px}
.bk .nm{flex:1}.bk .nm small{display:block;color:var(--muted);font-size:11.5px}
.bk button{width:32px;height:32px;border-radius:9px;border:1.5px solid var(--line);background:var(--chip);
font-size:17px;font-weight:700;color:var(--teal);cursor:pointer}
.bk .qv{min-width:26px;text-align:center;font-weight:700}
.bk .rm{color:var(--err);border-color:#EAC7C2;background:#FBEDEC}
.badge{display:inline-block;font-size:11.5px;padding:3px 9px;border-radius:999px;font-weight:700}
.b-ok{background:#E7F2E8;color:var(--ok)}.b-bad{background:#FBEDEC;color:var(--err)}
.b-sus{background:var(--amber-bg);color:var(--amber)}.b-cmf{background:#F3E8FB;color:var(--hip)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.del{color:var(--err);background:none;border:0;font-size:13px;cursor:pointer;padding:2px 6px}
.exp{display:inline-block;margin:4px 8px 4px 0;font-size:13px;color:var(--teal);font-weight:700;text-decoration:none;
border:1.5px solid var(--teal);border-radius:999px;padding:7px 14px}
svg.chart{width:100%;height:auto;display:block}
.legend{font-size:12px;color:var(--muted)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 10px;vertical-align:1px}
.toast{position:fixed;top:calc(10px + env(safe-area-inset-top));left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;
padding:10px 20px;border-radius:999px;font-size:14px;opacity:0;transition:opacity .25s;z-index:9;pointer-events:none}
.toast.show{opacity:1}
.course{border:1.5px solid var(--amber);background:var(--amber-bg);border-radius:13px;padding:10px 12px;margin:0 0 10px;
display:flex;align-items:center;gap:10px;font-size:14px}
.course b{color:var(--amber)}
.course button{margin-left:auto;border:1.5px solid var(--amber);background:none;color:var(--amber);
border-radius:999px;padding:6px 14px;font-weight:700;font-size:13px;cursor:pointer}
.patchcard{border:1.5px solid var(--hip);background:var(--patch);border-radius:13px;padding:11px 12px;margin:0 0 12px;
display:flex;align-items:center;gap:10px;font-size:14px}
.patchcard b{color:var(--hip)}
.patchcard button{margin-left:auto;border:1.5px solid var(--hip);background:none;color:var(--hip);
border-radius:999px;padding:6px 14px;font-weight:700;font-size:13px;cursor:pointer}
.todayrow{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px dashed var(--line);font-size:14px}
.todayrow .tms{color:var(--muted);font-size:12px;flex:1}
.todayrow .p1{border:1.5px solid var(--teal);color:var(--teal);background:none;border-radius:999px;
padding:5px 13px;font-weight:800;font-size:13px;cursor:pointer}
.reg{display:flex;flex-wrap:wrap;gap:6px}
.filerow{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}
.filerow a{color:var(--teal);font-weight:700;text-decoration:none}
.filerow .meta{color:var(--muted);font-size:12px}
.addbtn{border:1.5px dashed #B9CEC6;background:none;color:var(--muted);border-radius:999px;padding:8px 14px;font-size:14px;cursor:pointer}
.labgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.labcard{border:1px solid var(--line);border-radius:11px;padding:9px 10px}
.labcard .nm{font-size:12px;font-weight:700;color:var(--muted)}
.labcard .vl{font-size:17px;font-weight:800}
.labcard .du{font-size:11px;color:var(--muted)}
.labcard .du.over{color:var(--err);font-weight:800}
.librow{display:flex;align-items:center;gap:8px;padding:8px 2px;border-bottom:1px solid var(--line);font-size:14px;cursor:pointer}
.librow .nm{flex:1}.librow .nm small{display:block;color:var(--muted);font-size:11.5px}
.librow .star{font-size:17px;border:0;background:none;cursor:pointer;filter:grayscale(1);opacity:.45}
.librow .star.on{filter:none;opacity:1}
.cathead{font-size:12px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin:14px 0 2px}
.backlink{display:inline-block;font-size:14px;color:var(--teal);font-weight:700;text-decoration:none;margin:0 0 10px;background:none;border:0;cursor:pointer}
.mini{font-size:13px;color:var(--teal);font-weight:700;background:none;border:0;cursor:pointer;padding:4px 0}
/* GUTLOG_V342_PAINSITE -- pain-by-site tiles */
.ptile{border:2px solid var(--line);border-radius:13px;margin:0 0 8px;background:var(--chip)}
.ptile .ph{display:flex;width:100%;align-items:center;gap:10px;background:none;border:0;padding:12px 14px;font-size:16px;font-weight:600;color:var(--ink);cursor:pointer;text-align:left}
.ptile .pv{margin-left:auto;font-size:14px;font-weight:700;color:var(--teal-d)}
.ptile .pscore{display:none;padding:0 12px 12px}
.ptile.open{border-color:var(--teal);background:#F7FBF9}
.ptile.open .pscore{display:flex}
@media (max-width:430px){ #n_painSites .chip{padding:8px 0;min-width:34px;text-align:center}}
/* GUTLOG_V350_PHASE_B -- time row, day view, backfill rows */
.vtm{display:flex;gap:8px;align-items:center;margin-top:10px}
.vtm .lb{font-size:13px;font-weight:700;color:var(--muted)}
.vtm input{flex:1;min-width:0;padding:9px;border:1.5px solid var(--line);border-radius:10px;font-size:15px;background:#fff;color:var(--ink)}
.vtm .btn{flex:0 0 auto;margin:0}
.vtm .btn.go,.vtm .btn.tm{background:var(--teal);border-color:var(--teal);color:#fff}
.vtm .btn.sk{background:#fff;color:var(--teal);border-color:#BAD2C8}
.dvnav{display:flex;gap:8px;align-items:center}
.dvnav input{flex:1;min-width:0;text-align:center;padding:9px;border:1.5px solid var(--line);border-radius:10px;font-size:15.5px;font-weight:700;background:#fff;color:var(--ink)}
.dvnav .btn{flex:0 0 auto;min-width:48px;font-size:22px;line-height:1;padding:7px 12px;margin:0}
.dvnav .btn:disabled{opacity:.35}
.dvrow{display:flex;gap:10px;align-items:flex-start;padding:11px 2px;border-top:1px solid var(--line);cursor:pointer}
.dvrow .t{flex:0 0 44px;font-size:14px;font-weight:700;color:var(--ink);padding-top:2px}
.dvrow .tag{flex:0 0 auto;font-size:10.5px;font-weight:800;letter-spacing:.4px;text-transform:uppercase;padding:4px 7px;border-radius:7px;background:var(--chip);color:var(--muted)}
.dvrow .x{min-width:0}
.dvrow .x b{display:block;font-size:15px}
.dvrow .x span{font-size:13px;color:var(--muted)}
.tag.k-dose{background:#E3F1EC;color:var(--teal)}
.tag.k-extra{background:#EFE7F8;color:#6A3FA8}
.tag.k-skip{background:#ECECEC;color:#666}
.tag.k-sym{background:#FBEDEC;color:var(--err)}
.tag.k-bp{background:#FFF3DC;color:#8A5A00}
.tag.k-meal{background:#E8F0FA;color:#2F5C8A}
.dvmiss{padding:10px 12px;border:1.5px dashed var(--line);border-radius:13px;margin:0 0 8px}
.dvmiss .mh{display:flex;gap:8px;align-items:baseline}
.dvmiss .sl{font-size:11.5px;font-weight:800;color:var(--teal-d);text-transform:uppercase;letter-spacing:.4px}
.dvmiss .vrow{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}
/* GUTLOG_V360_PHASE_C -- stock rows, refill banner, vitals table */
.stockalert{display:flex;gap:10px;align-items:baseline;padding:11px 14px;border-radius:13px;margin:0 0 10px;
  font-size:14px;cursor:pointer;border:1.5px solid}
.stockalert b{font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.stockalert.red{background:#FBEDEC;border-color:#E4B9B3;color:var(--err)}
.stockalert.amber{background:#FFF3DC;border-color:#EBCB8B;color:#7A5200}
.strow{background:var(--card);border:1.5px solid var(--line);border-radius:13px;padding:11px 13px;margin:0 0 8px}
.strow .sh{display:flex;gap:10px;align-items:baseline}
.strow .sh b{font-size:15.5px}
.strow .sq{margin-left:auto;font-weight:800;font-size:15px;color:var(--teal-d)}
.strow .ss{font-size:13px;color:var(--muted);margin-top:2px}
.strow .sb{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}
.strow .sb .btn{margin:0;background:#fff;color:var(--teal);border-color:#BAD2C8}
.strow .sf[hidden]{display:none}
.strow.untracked{opacity:.8}
.strow.lv-red{border-color:#E4B9B3;background:#FDF5F4}
.strow.lv-red .sq{color:var(--err)}
.strow.lv-amber{border-color:#EBCB8B;background:#FFFAF0}
.strow.lv-amber .sq{color:#8A5A00}
.vtwrap{overflow-x:auto;margin-top:8px}
.vtwrap table{font-size:13.5px}
#vitalsLog .tot{margin:4px 2px}
@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}
</style></head><body>
<div style="display:flex;gap:8px;padding:8px 12px 4px;font-size:13.5px">
<a href="https://rx.dr-manoj.in" style="padding:4px 12px;border-radius:14px;background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">RxGuard</a>
<a href="/" style="padding:4px 12px;border-radius:14px;background:#0B6E6E;color:#fff;text-decoration:none;font-weight:700">GutLog</a>
<a href="https://fit.dr-manoj.in" style="padding:4px 12px;border-radius:14px;background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">FitLog</a>
</div>
<header><h1>Gut<b>Log</b></h1><span class="v">v3</span>
<span class="day"><span id="hdrDay"></span><br><span class="streak" id="hdrStreak"></span></span>
<a href="/account">Account</a><a href="/logout">Lock</a></header>
<div class="toast" id="toast" role="status"></div>
<main>

<!-- ============ LOG ============ -->
<!-- ============ NOW ============ -->
<section class="tab sel" id="tab-now">
  <div id="nowStock"></div>
  <div class="card" id="nowBP">
    <p class="q">Blood pressure</p>
    <div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="—"></div>
      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="—"></div>
      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="—"></div>
    </div>
    <button type="button" class="btn primary" id="n_bpSave">Save reading</button>
    <p class="hint" id="n_bpLast" style="margin:10px 0 0"></p>
  </div>

  <div class="card fold" id="nowDoses">
    <button type="button" class="fold-h">
      <span class="ft">Today&rsquo;s doses</span><span class="fs" id="doseSum"></span><span class="fc"></span>
    </button>
    <div class="cbody"><div id="nowSched"></div></div>
  </div>

  <div class="card fold" id="nowExtraCard">
    <button type="button" class="fold-h">
      <span class="ft">Extra dose</span><span class="fs" id="exSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div class="chips" id="nowExtras"></div>
      <div id="nowExtraList"></div>
      <div class="btnrow">
        <button type="button" class="btn ghost" id="nowShowAll">Show all medicines</button>
        <button type="button" class="btn ghost" id="nowAddMed">Add medicine</button>
      </div>
      <p class="hint" style="margin:10px 0 0">One tap logs it at the current time.</p>
    </div>
  </div>

  <div class="card fold" id="nowSym">
    <button type="button" class="fold-h">
      <span class="ft">Symptom now</span><span class="fs">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody">
      <p class="lbl">What &mdash; pick one or more</p>
      <div class="chips" id="n_symType"></div>
      <p class="lbl" style="margin-top:14px">Severity</p>
      <div class="chips" id="n_symSev"></div>
      <p class="lbl" style="margin-top:14px">Pain by site &mdash; tap to score</p>
      <div id="n_painSites"></div>
      <p class="lbl" style="margin-top:14px">Bristol (optional)</p>
      <div class="chips" id="n_symBristol"></div>
      <button type="button" class="btn primary" id="n_symSave" style="margin-top:14px">Save episode</button>
    </div>
  </div>
</section>

<section class="tab" id="tab-log">
  <div class="rings" id="rings"></div>
  <div class="seg" data-seg="log">
    <button data-s="day" class="sel">Day</button><button data-s="episode">+ Episode</button><button data-s="vitals">Vitals</button>
  </div>

  <div class="sub sel" id="log-day">
    <p class="hint">Tap everything that was true today, then Save. Nothing tapped = a clean day.</p>
    <div class="card"><p class="q">Date</p><input type="date" id="s_day"></div>
    <div class="card"><p class="q">Symptoms today - tap all that apply</p>
      <div class="symgrid" id="symgrid"></div></div>
    <div class="card"><p class="q">&#128293; Gut pain (0-10)</p>
      <div class="chips scale" data-f="pain" data-sec="log" data-v="0|1|2|3|4|5|6|7|8|9|10"></div>
      <div id="painsite" style="display:none;margin-top:10px"><p class="q">Where?</p>
        <div class="chips" data-f="pain_site" data-sec="log" data-v="Left iliac fossa|Right iliac fossa|Hypogastrium|Left flank|Right flank|Umbilical|Diffuse|Other"></div></div></div>
    <div class="card"><p class="q">&#128169; Stools &amp; Bristol</p>
      <div class="chips" data-f="stools" data-sec="log" data-v="0|1|2|3|4|5+"></div>
      <div class="bristol" id="bristol" style="margin-top:9px"></div></div>
    <div class="card"><p class="q">&#127861; Tea today</p>
      <div class="step"><button type="button" id="teaMinus">&minus;</button><b id="teaN">0</b>
        <button type="button" id="teaPlus">+</button>
        <span class="cap">your cup &middot; 0.3 tsp sugar<br>taper target 2-3 / day</span></div></div>
    <div class="card"><p class="q">&#9749; Coffee today</p>
      <div class="step"><button type="button" id="cofMinus">&minus;</button><b id="cofN">0</b>
        <button type="button" id="cofPlus">+</button>
        <span class="cap">&frac12; cow milk &middot; &frac12; water &middot; no sugar</span></div></div>
    <div class="card"><p class="q">&#128564; Sleep last night</p>
      <div class="chips" data-f="sleep" data-sec="log" data-v="Good|Fair|Poor"></div></div>
    <div class="card"><p class="q">&#128694; Movement (min)</p>
      <p class="lbl">Walk</p><div class="chips" data-f="walk" data-sec="log" data-v="0|10|20|30|45|60+"></div>
      <p class="lbl" style="margin-top:9px">Treadmill</p><div class="chips" data-f="treadmill" data-sec="log" data-v="0|10|20|30|45|60+"></div>
      <p class="lbl" style="margin-top:9px">Meditation</p><div class="chips" data-f="meditation" data-sec="log" data-v="0|10|20|30|45|60+"></div></div>
    <div class="card"><p class="q">Notes (optional)</p><textarea id="s_notes" maxlength="500"></textarea></div>
  </div>

  <div class="sub" id="log-episode">
    <p class="hint">Within-day event - gut, neck or hip. Time is pre-filled; adjust if logging later.</p>
    <div class="card"><div class="row2">
      <div><p class="lbl">Date</p><input type="date" id="e_day"></div>
      <div><p class="lbl">Time</p><input type="time" id="e_time"></div></div></div>
    <div class="card"><p class="q">&#129440; Gut</p><div class="chips" data-f="etype" data-sec="episode" data-cat="Gut" data-v="Pain spike|Urgency|Cramping|Bloating wave"></div>
      <p class="q" style="margin-top:12px">&#129460; Neck</p><div class="chips" data-f="etype" data-sec="episode" data-cat="Neck" data-v="Neck pain|Neck + radiculopathy"></div>
      <p class="q" style="margin-top:12px">&#129470; Hip</p><div class="chips" data-f="etype" data-sec="episode" data-cat="Hip" data-v="Hip pain"></div>
      <p class="q" style="margin-top:12px">&#129485; Back</p><div class="chips" data-f="etype" data-sec="episode" data-cat="Back" data-v="Back pain|Low back pain"></div></div>
    <div class="card" id="e_sidecard" style="display:none"><p class="q">Hip side <span style="text-transform:none;font-weight:400">(right = THR side)</span></p>
      <div class="chips" data-f="side" data-sec="episode" data-v="Left|Right|Both"></div></div>
    <div class="card"><p class="q">Severity (0-10)</p><div class="chips scale" data-f="severity" data-sec="episode" data-v="0|1|2|3|4|5|6|7|8|9|10"></div></div>
    <div class="card"><p class="q">Duration</p><div class="chips" data-f="duration" data-sec="episode" data-v="<15 min|15-60 min|1-3 h|>3 h"></div></div>
    <div class="card"><p class="q">Trigger / context (optional)</p><textarea id="e_notes" maxlength="500" placeholder="e.g. 2 h after lunch, long OT posture..."></textarea></div>
  </div>

  <div class="sub" id="log-vitals">
    <p class="hint">BP weekly (seated, rested 5 min); weight &amp; waist monthly.</p>
    <div class="card"><div class="row2">
      <div><p class="lbl">Date</p><input type="date" id="v_day"></div>
      <div><p class="lbl">Time</p><input type="time" id="v_time"></div></div></div>
    <div class="card"><p class="q">&#129657; Blood pressure &amp; pulse</p><div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" id="v_sys" min="70" max="260" inputmode="numeric" placeholder="128"></div>
      <div><p class="lbl">Diastolic</p><input type="number" id="v_dia" min="40" max="160" inputmode="numeric" placeholder="82"></div>
      <div><p class="lbl">Pulse</p><input type="number" id="v_pulse" min="30" max="200" inputmode="numeric" placeholder="74"></div></div>
      <p class="lbl" style="margin-top:9px">Temperature (&deg;F)</p><input type="number" id="v_temp" min="90" max="110" step="0.1" inputmode="decimal" placeholder="98.6"></div>
    <div class="card"><p class="q">&#9878;&#65039; Body</p><div class="row2">
      <div><p class="lbl">Weight kg</p><input type="number" id="v_weight" min="40" max="150" step="0.1" inputmode="decimal" placeholder="69.0"></div>
      <div><p class="lbl">Waist cm</p><input type="number" id="v_waist" min="50" max="160" step="0.5" inputmode="decimal"></div></div></div>
    <div class="card"><p class="q">Notes (optional)</p><textarea id="v_notes" maxlength="500"></textarea></div>
  </div>
</section>

<!-- ============ MEALS ============ -->
<section class="tab" id="tab-meals">
  <div class="seg" data-seg="meals">
    <button data-s="meal" class="sel">Meal</button><button data-s="test">Food test</button>
  </div>

  <div class="sub sel" id="meals-meal">
    <div class="card" style="padding:11px 14px">
      <div class="tot" id="dayTotals"></div>
      <div class="pbar"><i id="dayPbar" style="width:0%"></i></div>
      <div class="tot" id="dayFmap"></div>
    </div>
    <div class="card"><div class="row2">
      <div><p class="lbl">Date</p><input type="date" id="ml_day"></div>
      <div><p class="lbl">Time</p><input type="time" id="ml_time"></div></div>
      <p class="q" style="margin-top:10px">Slot</p>
      <div class="chips" data-f="slot" data-sec="meal" data-v="Breakfast|Lunch|Dinner|Snack"></div></div>
    <div class="card"><p class="q">&#128269; What did you eat?</p>
      <input type="text" id="ml_search" placeholder="Search your foods... (dal, roti, guava)" autocomplete="off">
      <div class="chips" id="ml_results" style="margin-top:9px"></div>
      <div id="ml_newfood" style="display:none;margin-top:10px;border-top:1px dashed var(--line);padding-top:10px">
        <p class="q">&#10133; New food</p>
        <div class="row2"><div><p class="lbl">Name</p><input type="text" id="nf_item"></div>
          <div><p class="lbl">Portion</p><input type="text" id="nf_portion" placeholder="1 katori"></div></div>
        <div class="row3" style="margin-top:8px">
          <div><p class="lbl">Protein g</p><input type="number" id="nf_p" step="0.1" inputmode="decimal"></div>
          <div><p class="lbl">kcal</p><input type="number" id="nf_k" inputmode="numeric"></div>
          <div><p class="lbl">Fibre g</p><input type="number" id="nf_f" step="0.1" inputmode="decimal"></div></div>
        <p class="lbl" style="margin-top:8px">FODMAP</p>
        <div class="chips" id="nf_fm"></div>
        <button type="button" class="mini" id="nf_save" style="margin-top:6px">Save food &amp; add to meal</button>
      </div></div>
    <div class="card"><p class="q">&#128722; This meal</p>
      <div id="ml_basket"><p class="hint" style="margin:0">Nothing added yet - search above or tap a favourite.</p></div>
      <div class="tot" id="ml_tot" style="margin-top:9px"></div>
      <div class="meter" id="ml_meter" style="display:none"><i id="ml_pin"></i></div>
      <div class="tot" id="ml_fmw"></div></div>
    <div class="card"><p class="q">Notes (optional)</p><textarea id="ml_notes" maxlength="500"></textarea></div>
    <button type="button" class="mini" id="openFoods">&#9881;&#65039; Manage food library &rarr;</button>
  </div>

  <div class="sub" id="meals-foods">
    <button type="button" class="backlink" id="closeFoods">&larr; Back to meals</button>
    <p class="hint">The full library - 87 foods and growing. Tap a row to edit; star = favourite in the meal picker.</p>
    <input type="text" id="lib_search" placeholder="Filter..." autocomplete="off" style="margin-bottom:6px">
    <div class="chips" id="lib_filters" style="margin-bottom:4px"></div>
    <div class="card" id="lib_list"></div>
    <div class="card" id="lib_edit" style="display:none"></div>
    <button type="button" class="addbtn" id="lib_addnew">&#10133; Add a new food</button>
  </div>

  <div class="sub" id="meals-test">
    <p class="hint">Only on deliberate test days - one new food, symptom-free day, judge over 24 h. The verdict updates the library automatically.</p>
    <div class="card"><p class="q">Your food map so far</p><div class="reg" id="registry"></div></div>
    <div class="card"><p class="q">Date</p><input type="date" id="f_day"></div>
    <div class="card"><p class="q">Food tested</p><div class="chips" id="f_foods"></div></div>
    <div class="card" id="f_portioncard"><p class="q">Portion</p><div class="chips" id="f_portions"></div></div>
    <div class="card"><p class="q">Symptoms within 24 h</p><div class="chips" data-f="symptoms" data-sec="test"
      data-v="None|Bloating|Pain|Flatulence|Urgency|Loose stools|Constipation|Mixed"></div></div>
    <div class="card"><p class="q">Severity</p><div class="chips" data-f="severity" data-sec="test" data-v="None|Mild|Moderate|Severe"></div></div>
    <div class="card"><p class="q">Verdict</p><div class="chips" data-f="verdict" data-sec="test" data-v="Tolerated|Suspect - retest|Trigger - avoid|Not sure"></div></div>
    <div class="card"><p class="q">Notes (optional)</p><textarea id="f_notes" maxlength="500"></textarea></div>
  </div>
</section>

<!-- ============ MEDS ============ -->
<section class="tab" id="tab-meds">
  <div class="seg" data-seg="meds">
    <button data-s="prn" class="sel">PRN dose</button><button data-s="course">Courses</button><button data-s="sched">Schedule</button><button data-s="stock">Stock</button>
  </div>

  <div class="sub sel" id="meds-prn">
    <div id="patchbox"></div>
    <div class="card"><p class="q">&#128138; Today so far</p><div id="prnToday"><p class="hint" style="margin:0">No doses logged today.</p></div></div>
    <p class="hint">Tap every medicine taken now (multi-select), set the time, log once. Repeat doses later with +1.</p>
    <div class="card"><div class="row2">
      <div><p class="lbl">Date</p><input type="date" id="m_day"></div>
      <div><p class="lbl">Time taken</p><input type="time" id="m_time"></div></div></div>
    <div class="card"><p class="q">Medicines - tap all taken at this time</p>
      <div class="chips" id="prnGrid"></div>
      <button type="button" class="addbtn" id="prnAdd" style="margin-top:9px">&#10133; Add medicine</button></div>
    <div class="card"><p class="q">Reason (optional, shared)</p><div class="chips" data-f="reason" data-sec="prn"
      data-v="Abdominal pain|Spasm|Bloating/gas|Constipation|Loose stools|Headache/body ache|Allergy/nose|Feverish|Sleep|Neck|Hip|Other"></div></div>
    <div class="card"><p class="q">Effect (optional - can log later dose instead)</p><div class="chips" data-f="effect" data-sec="prn"
      data-v="Helped fully|Helped partly|No effect|Worse/side effect"></div></div>
    <div class="card"><p class="q">Notes (optional)</p><textarea id="m_notes" maxlength="500"></textarea></div>
  </div>


  <div class="sub" id="meds-sched">
    <p class="hint">Your regular regimen. Changing a line closes the old one and opens a new one on today's date, so past logs stay readable.</p>
    <div id="schedList"></div>
    <div class="card"><p class="q">Add a regular medicine</p>
      <p class="lbl">Medicine</p><select id="sc_med"></select>
      <p class="lbl" style="margin-top:10px">Slot</p>
      <div class="chips" id="sc_slot"></div>
      <div class="row2" style="margin-top:10px">
        <div><p class="lbl">Dose</p><input type="text" id="sc_dose" maxlength="40" placeholder="1 tab"></div>
        <div><p class="lbl">Food</p><select id="sc_food">
          <option value="ANY">Any</option><option value="BEFORE">Before</option><option value="AFTER">After</option>
        </select></div>
      </div>
      <button type="button" class="addbtn" id="sc_add" style="margin-top:10px">Add to regimen</button>
    </div>
  </div>

  <div class="sub" id="meds-stock">
    <div class="card" id="stPill">
      <p class="q">Pillbox</p>
      <p class="hint st-last" style="margin:0 2px 8px"></p>
      <div class="vtm"><span class="lb">Days</span><input type="number" id="stDays" min="1" max="31" value="7">
        <button type="button" class="btn tiny go" id="stFill">Pillbox filled</button></div>
      <p class="hint st-prev" style="margin:10px 2px 0"></p>
      <button type="button" class="mini" id="stFillUndo" style="margin-top:6px">Undo last fill</button>
    </div>
    <p class="hint">Count = what is left in the strips or bottle, not in the pillbox. Pillbox medicines come
      out of stock when you fill it; the others per logged dose.</p>
    <div id="stList"></div>
  </div>

  <div class="sub" id="meds-course">
    <p class="hint">Multi-day drugs with a live day counter. Switches and tapers run here.</p>
    <div id="activeCourses"></div>
    <div class="card"><p class="q">Start a course</p><div class="chips" data-f="drug" data-sec="course"
      data-v="{{ course_chips }}"></div>
      <p class="lbl" style="margin-top:10px">Start date</p><input type="date" id="c_day">
      <p class="lbl" style="margin-top:10px">Notes (dose plan etc.)</p><textarea id="c_notes" maxlength="500"></textarea></div>
  </div>
</section>

<!-- ============ FILES ============ -->
<section class="tab" id="tab-files">
  <div class="seg" data-seg="files">
    <button data-s="vault" class="sel">Vault</button><button data-s="labs">Labs</button><button data-s="consults">Consults</button>
  </div>

  <div class="sub sel" id="files-vault">
    <p class="hint">Reports, prescriptions, scan photos. PDF / JPG / PNG, up to 12 MB.</p>
    <div class="card"><p class="q">Upload</p>
      <div class="row2"><div><p class="lbl">Date</p><input type="date" id="u_day"></div>
        <div><p class="lbl">Type</p><select id="u_ftype">
          <option>Lab report</option><option>Prescription</option><option>Scan / X-ray</option>
          <option>Discharge</option><option>Photo</option><option>Other</option></select></div></div>
      <p class="lbl" style="margin-top:8px">Label</p><input type="text" id="u_label" placeholder="e.g. CBC 14 Jul">
      <input type="file" id="u_file" accept=".pdf,.jpg,.jpeg,.png" style="margin-top:9px;border:0;padding-left:0">
      <button type="button" class="mini" id="u_btn" style="margin-top:6px">Upload file</button></div>
    <div class="card"><p class="q">Stored files</p><div id="fileList"></div></div>
  </div>

  <div class="sub" id="files-labs">
    <p class="hint">Latest value per test with the next-due date. Red = overdue.</p>
    <div class="card"><p class="q">Add a result</p>
      <div class="row2"><div><p class="lbl">Test</p><select id="l_analyte"></select></div>
        <div><p class="lbl">Value</p><input type="number" id="l_value" step="0.01" inputmode="decimal"></div></div>
      <p class="lbl" style="margin-top:8px">Date</p><input type="date" id="l_day">
      <button type="button" class="mini" id="l_btn" style="margin-top:6px">Save result</button></div>
    <div class="labgrid" id="labGrid"></div>
  </div>

  <div class="sub" id="files-consults">
    <p class="hint">What each doctor said, and what changed. Ahuja &amp; Srivastav are pre-loaded.</p>
    <div class="card"><p class="q">Log a visit</p>
      <p class="lbl">Date</p><input type="date" id="k_day">
      <p class="q" style="margin-top:10px">Doctor</p><div class="chips" id="k_doctors"></div>
      <button type="button" class="addbtn" id="k_adddoc" style="margin-top:8px">&#10133; Add doctor</button>
      <p class="lbl" style="margin-top:10px">Reason</p><input type="text" id="k_reason">
      <p class="lbl" style="margin-top:8px">Advice / notes</p><textarea id="k_advice" maxlength="800"></textarea>
      <p class="lbl" style="margin-top:8px">Med changes</p><input type="text" id="k_changes">
      <p class="lbl" style="margin-top:8px">Next visit</p><input type="date" id="k_next"></div>
  </div>
</section>

<!-- ============ REVIEW ============ -->
<section class="tab" id="tab-review">
  <div class="card" id="dayView">
    <p class="q">Day by day</p>
    <div class="dvnav"><button type="button" class="btn ghost" id="dvPrev">&lsaquo;</button>
      <input type="date" id="dvDate"><button type="button" class="btn ghost" id="dvNext">&rsaquo;</button></div>
    <p class="hint" style="margin:8px 2px 4px"><span id="dvSum"></span> &middot; tap an entry to fix its time</p>
    <div id="dvList"></div>
    <p class="lbl" id="dvMissHd" style="margin-top:14px">Scheduled but not logged &mdash; enter the time it was taken</p>
    <div id="dvMiss"></div>
  </div>
  <div class="card" id="vitalsLog">
    <p class="q">Vitals log</p>
    <div id="vtChart"></div>
    <p class="legend"><span class="dot" style="background:#B3372A"></span><b>Systolic</b>
      <span class="dot" style="background:#0B6E6E"></span><b>Diastolic</b>
      <span class="dot" style="background:#C8860A"></span><b>Pulse</b></p>
    <div id="vtSum"></div>
    <div id="vtTable" class="vtwrap"></div>
    <a class="exp" href="/export/vitals.csv">Download vitals.csv</a>
  </div>
  <p class="hint">Last <span id="rvDaysN">30</span> days. Show at clinic visits, or export any stream as CSV.</p>
  <div class="seg" id="rvRange"><button data-d="30" class="sel">30 d</button><button data-d="90">90 d</button><button data-d="180">6 mo</button></div>
  <div class="card"><p class="q">Pain, tea &amp; coffee</p><div id="chartMain"></div>
    <p class="legend"><span class="dot" style="background:#B3372A"></span><b>Pain</b>
      <span class="dot" style="background:#C8860A"></span><b>Tea</b>
      <span class="dot" style="background:#8A5A2B"></span><b>Coffee</b></p></div>
  <div class="card"><p class="q">FODMAP load vs symptom days</p><div id="chartFmap"></div>
    <p class="legend"><span class="dot" style="background:#0B6E6E"></span><b>Daily FODMAP load</b>
      <span class="dot" style="background:#B3372A"></span><b>Symptom-day mark</b></p></div>
  <div class="card"><p class="q">PRN doses per day</p><div id="chartDose"></div></div>
  <div class="card"><p class="q">Protein target hit-rate</p><div id="proteinHit"></div></div>
  <div class="card"><p class="q">Recent PRN doses</p><div id="rvDoses"></div></div>
  <div class="card"><p class="q">Episodes</p><div id="rvEpisodes"></div></div>
  <div class="card"><p class="q">Transdermal patch history</p><div id="rvPatch"></div></div>
  <div class="card"><p class="q">Food map</p><div class="reg" id="rvRegistry"></div></div>
  <div class="card"><p class="q">Export for your doctor / analytics</p>
    <div id="expLinks"></div>
    <p class="hint" style="margin-top:10px">Every stream is one row per event with timestamps - analytics-ready. Back up <code>health3.db</code> periodically; it is the whole diary.</p></div>
</section>
</main>

<div class="save"><button id="saveBtn">Save</button></div>
<nav id="nav">
  <button data-t="now" class="sel"><i>&#9889;</i>Now</button>
  <button data-t="log"><i>&#9998;</i>Log</button>
  <button data-t="meals"><i>&#127860;</i>Meals</button>
  <button data-t="meds"><i>&#128138;</i>Meds</button>
  <button data-t="files"><i>&#128194;</i>Files</button>
  <button data-t="review"><i>&#128202;</i>Review</button>
</nav>

<script>
const $=q=>document.querySelector(q), $$=q=>[...document.querySelectorAll(q)];
const todayISO=new Date().toLocaleDateString('en-CA');
/* GUTLOG_V350_PHASE_B -- 'today' is fixed at load; a page left open overnight
   would log the morning dose against yesterday. Reload on a new day. */
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden&&new Date().toLocaleDateString('en-CA')!==todayISO)location.reload();
});
const nowHM=()=>new Date().toTimeString().slice(0,5);
const S={log:{},episode:{},vitals:{},meal:{},test:{},prn:{}};
const FMAP={L:0,'L-M':0.5,M:1,'M-H':1.5,H:2};
const FMCOL={L:'var(--fmL)','L-M':'var(--fmLM)',M:'var(--fmM)','M-H':'var(--fmMH)',H:'var(--fmH)'};
let tab='now'; const seg={log:'day',meals:'meal',meds:'prn',files:'vault'};
let LIB=[], PRN=[], basket=[], libFilter='all', dayProtein=0;
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1700);}
async function jget(u){const r=await fetch(u);return r.json();}
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
  if(!r.ok){const j=await r.json().catch(()=>({}));throw new Error(j.err||'Save failed');}return r.json();}

/* generic single-select chips */
function buildChips(){
  $$('.chips[data-f]').forEach(box=>{
    if(box.dataset.built||!box.dataset.v)return; box.dataset.built=1;
    const f=box.dataset.f, sec=box.dataset.sec, cat=box.dataset.cat;
    box.dataset.v.split('|').forEach(v=>{
      const b=document.createElement('button');b.type='button';b.className='chip';b.textContent=v;
      b.onclick=()=>{
        const scope = f==='etype' ? $$('[data-f="etype"] .chip') : [...box.querySelectorAll('.chip')];
        scope.forEach(c=>c.classList.remove('sel')); b.classList.add('sel');
        S[sec][f]=v; if(cat)S[sec].category=cat;
        if(f==='pain'){$('#painsite').style.display=(v!=='0')?'block':'none';}
        if(f==='etype'){$('#e_sidecard').style.display=(cat==='Hip')?'block':'none';}
      };
      box.appendChild(b);
    });
  });
}
function resetChips(sec){$$(`.chips[data-sec="${sec}"] .chip.sel`).forEach(c=>c.classList.remove('sel'));S[sec]={};}

/* Bristol */
const BR=[['1','hard'],['2','lumpy'],['3','cracked'],['4','normal'],['5','soft'],['6','mushy'],['7','watery']];
(function(){const box=$('#bristol');BR.forEach(([n,d])=>{const t=document.createElement('button');t.type='button';t.className='btile';
  t.innerHTML=`<b>${n}</b><span>${d}</span>`;t.onclick=()=>{box.querySelectorAll('.btile').forEach(x=>x.classList.remove('sel'));t.classList.add('sel');S.log.bristol=n;};box.appendChild(t);});})();

/* multi-symptom grid */
const SYMS=[['Bloating','&#127774;'],['Gas / flatulence','&#128168;'],['Urgency','&#9203;'],
 ['Loose stools','&#127754;'],['Incomplete evac.','&#128260;'],['Constipation','&#129704;'],
 ['Nausea','&#129326;'],['Fatigue','&#128564;'],['Feverish','&#127777;&#65039;'],
 ['Eye burn/water','&#128065;&#65039;'],['Low mood','&#127785;&#65039;'],['High stress','&#127786;&#65039;']];
(function(){const g=$('#symgrid');SYMS.forEach(([n,ic])=>{const b=document.createElement('button');b.type='button';b.className='sym';
  b.innerHTML=`<i>${ic}</i><span>${n}</span>`;b.dataset.s=n;
  b.onclick=()=>{b.classList.toggle('sel');};g.appendChild(b);});})();
function selectedSyms(){return $$('#symgrid .sym.sel').map(b=>b.dataset.s);}

/* steppers */
function stepper(minusId,plusId,nId,key,warn){
  let n=0; const el=$('#'+nId);
  const upd=()=>{el.textContent=n; S.log[key]=n; if(warn){el.className=n>=warn[1]?'warnH':n>=warn[0]?'warnA':'';}};
  $('#'+minusId).onclick=()=>{if(n>0)n--;upd();};
  $('#'+plusId).onclick=()=>{if(n<20)n++;upd();};
  return {set:v=>{n=+v||0;upd();}};
}
const teaCtl=stepper('teaMinus','teaPlus','teaN','tea',[4,6]);
const cofCtl=stepper('cofMinus','cofPlus','cofN','coffee',[3,5]);

/* ---------- rings / completion strip ---------- */
function ring(pct,ic,label,done){
  const R=16,C=2*Math.PI*R,off=C*(1-pct);
  const col=done?'var(--teal)':'#C9D9D3';
  return `<button class="ring" data-go="${label}">
   <svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="${R}" fill="none" stroke="#E6F0EC" stroke-width="4"/>
   <circle cx="20" cy="20" r="${R}" fill="none" stroke="${col}" stroke-width="4" stroke-linecap="round"
    stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 20 20)"/>
   <text x="20" y="25" text-anchor="middle" font-size="15">${ic}</text></svg>
   <span class="rl">${label}</span></button>`;
}
async function loadRings(){
  const s=await jget('/api/summary/'+todayISO);
  dayProtein=s.protein||0;
  $('#hdrStreak').textContent=s.streak>0?('&#128293; '+s.streak+'d').replace('&#128293;','🔥'):'';
  const pPct=Math.min(1,(s.protein||0)/(s.target||57));
  const strip=[
    ring(s.day_done?1:0,'📝','Day',s.day_done),
    ring(Math.min(1,s.meals/3),'🍽️','Meals',s.meals>0),
    ring(pPct,'💪','Protein',pPct>=1),
    ring(s.doses>0?1:0.05,'💊','Meds',true),
    ring(s.vitals>0?1:0.05,'🩺','Vitals',true),
  ];
  $('#rings').innerHTML=strip.join('');
  $$('#rings .ring').forEach(b=>b.onclick=()=>{
    const go=b.dataset.go;
    if(go==='Meals'){switchTab('meals');}
    else if(go==='Protein'){switchTab('meals');}
    else if(go==='Meds'){switchTab('meds');}
    else if(go==='Vitals'){switchTab('log');setSeg('log','vitals');}
    else {switchTab('log');setSeg('log','day');}
  });
}

/* ---------- library + meal picker ---------- */
async function loadLib(){LIB=await jget('/api/library');renderResults('');renderLibList();}
function libItem(name){return LIB.find(x=>x.item===name);}
function renderResults(qstr){
  const box=$('#ml_results');box.innerHTML='';
  const q=qstr.trim().toLowerCase();
  let list;
  if(!q){list=LIB.filter(x=>x.fav).slice(0,10); if(!list.length)list=LIB.slice(0,8);}
  else list=LIB.filter(x=>x.item.toLowerCase().includes(q)||(x.tags||'').includes(q)).slice(0,12);
  list.forEach(x=>{
    const b=document.createElement('button');b.type='button';b.className='chip';
    b.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span>${x.item}`;
    b.onclick=()=>addToBasket(x);
    box.appendChild(b);
  });
  $('#ml_newfood').style.display=(q&&!list.length)?'block':'none';
  if(q&&!list.length){$('#nf_item').value=qstr.trim();}
}
function addToBasket(x){
  const ex=basket.find(b=>b.item===x.item);
  if(ex)ex.q++; else basket.push({item:x.item,portion:x.portion,q:1,p:x.protein,k:x.kcal,f:x.fibre,fm:x.fodmap});
  renderBasket();
}
function renderBasket(){
  const box=$('#ml_basket');
  if(!basket.length){box.innerHTML='<p class="hint" style="margin:0">Nothing added yet - search above or tap a favourite.</p>';
    $('#ml_tot').textContent='';$('#ml_meter').style.display='none';$('#ml_fmw').textContent='';return;}
  box.innerHTML='';
  let p=0,k=0,f=0,fs=0;
  basket.forEach((b,i)=>{
    p+=b.q*b.p;k+=b.q*b.k;f+=b.q*b.f;fs+=b.q*FMAP[b.fm];
    const row=document.createElement('div');row.className='bk';
    row.innerHTML=`<span class="fd" style="background:${FMCOL[b.fm]}"></span>
      <span class="nm">${b.item}<small>${b.portion||''}</small></span>
      <button type="button">&minus;</button><span class="qv">${b.q}</span>
      <button type="button">+</button><button type="button" class="rm">&times;</button>`;
    const[minus,,plus,rm]=row.querySelectorAll('button, .qv, button');
    const btns=row.querySelectorAll('button');
    btns[0].onclick=()=>{b.q--;if(b.q<=0)basket.splice(i,1);renderBasket();};
    btns[1].onclick=()=>{b.q++;renderBasket();};
    btns[2].onclick=()=>{basket.splice(i,1);renderBasket();};
    box.appendChild(row);
  });
  const proj=dayProtein+p;
  $('#ml_tot').innerHTML=`This meal: <b>${p.toFixed(1)} g protein</b> &middot; ${Math.round(k)} kcal &middot; ${f.toFixed(1)} g fibre`;
  const avg=basket.length?fs/basket.reduce((a,b)=>a+b.q,0):0;
  $('#ml_meter').style.display='block';
  $('#ml_pin').style.left=(avg/2*100)+'%';
  const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
  $('#ml_fmw').innerHTML=`FODMAP load: <b>${lab}</b> &middot; day protein would reach <b>${proj.toFixed(0)}/57 g</b>`;
}
$('#ml_search').oninput=e=>renderResults(e.target.value);
/* new food inline */
(function(){const box=$('#nf_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip';
  b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;
  b.onclick=()=>{box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');box.dataset.v=k;};box.appendChild(b);});})();
$('#nf_save').onclick=async()=>{
  const item=$('#nf_item').value.trim();if(!item)return toast('Name the food.');
  const fm=$('#nf_fm').dataset.v;if(!fm)return toast('Pick a FODMAP flag.');
  try{await post('/api/library',{item,portion:$('#nf_portion').value,protein:$('#nf_p').value,
    kcal:$('#nf_k').value,fibre:$('#nf_f').value,fodmap:fm,cat:'F'});
    await loadLib();const x=libItem(item);if(x)addToBasket(x);
    $('#ml_newfood').style.display='none';$('#ml_search').value='';renderResults('');
    ['nf_item','nf_portion','nf_p','nf_k','nf_f'].forEach(id=>$('#'+id).value='');$('#nf_fm').dataset.v='';
    $$('#nf_fm .chip').forEach(c=>c.classList.remove('sel'));toast('Food added');
  }catch(e){toast(e.message);}
};
$('#openFoods').onclick=()=>setSeg('meals','foods');
$('#closeFoods').onclick=()=>setSeg('meals','meal');

/* library manager */
function renderLibList(){
  const q=($('#lib_search').value||'').trim().toLowerCase();
  const box=$('#lib_list');box.innerHTML='';
  const CATN={A:'Grains & breads',B:'Dals & legumes',C:'Soy & protein',D:'Dairy & fats',E:'Sabzis',F:'Snacks & nuts',G:'Fruit',H:'Drinks & composites'};
  let list=LIB.filter(x=>{
    if(libFilter==='fav'&&!x.fav)return false;
    if(libFilter==='comfort'&&!(x.tags||'').includes('comfort'))return false;
    if(libFilter==='trigger'&&x.status!=='trigger')return false;
    if(q&&!x.item.toLowerCase().includes(q))return false;
    return true;});
  let cur='';
  list.forEach(x=>{
    if(x.cat!==cur){cur=x.cat;const h=document.createElement('p');h.className='cathead';h.textContent=CATN[cur]||cur;box.appendChild(h);}
    const row=document.createElement('div');row.className='librow';
    const badge=x.status==='cleared'?'<span class="badge b-ok">cleared</span>':
      x.status==='trigger'?'<span class="badge b-bad">trigger</span>':
      x.status==='test'?'<span class="badge b-sus">test</span>':
      (x.tags||'').includes('comfort')?'<span class="badge b-cmf">comfort</span>':'';
    row.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span>
      <span class="nm">${x.item} ${badge}<small>${x.portion||''} &middot; ${x.protein}g P &middot; ${x.kcal} kcal &middot; ${x.fodmap}</small></span>
      <button type="button" class="star ${x.fav?'on':''}">&#9733;</button>`;
    row.querySelector('.star').onclick=async(ev)=>{ev.stopPropagation();await post('/api/library/'+x.id,{fav:!x.fav});await loadLib();};
    row.onclick=()=>editFood(x);
    box.appendChild(row);
  });
  if(!list.length)box.innerHTML='<p class="hint" style="margin:0">No foods match.</p>';
}
$('#lib_search').oninput=renderLibList;
(function(){const box=$('#lib_filters');[['all','All'],['fav','★ Favourites'],['comfort','Comfort'],['trigger','Triggers']].forEach(([k,l])=>{
  const b=document.createElement('button');b.type='button';b.className='chip'+(k==='all'?' sel':'');b.textContent=l;
  b.onclick=()=>{libFilter=k;box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');renderLibList();};box.appendChild(b);});})();
function editFood(x){
  const e=$('#lib_edit');e.style.display='block';$('#lib_list').style.display='none';$('#lib_addnew').style.display='none';
  e.innerHTML=`<p class="q">Edit &middot; ${x.item}</p>
   <div class="row2"><div><p class="lbl">Name</p><input type="text" id="ed_item"></div>
     <div><p class="lbl">Portion</p><input type="text" id="ed_portion"></div></div>
   <div class="row3" style="margin-top:8px"><div><p class="lbl">Protein</p><input type="number" id="ed_p" step="0.1"></div>
     <div><p class="lbl">kcal</p><input type="number" id="ed_k"></div>
     <div><p class="lbl">Fibre</p><input type="number" id="ed_f" step="0.1"></div></div>
   <p class="lbl" style="margin-top:8px">FODMAP</p><div class="chips" id="ed_fm"></div>
   <p class="lbl" style="margin-top:8px">Status</p><div class="chips" id="ed_st"></div>
   <div style="display:flex;gap:8px;margin-top:12px">
     <button type="button" class="mini" id="ed_save">Save changes</button>
     <button type="button" class="del" id="ed_del">Delete food</button>
     <button type="button" class="backlink" id="ed_cancel" style="margin:0 0 0 auto">Cancel</button></div>`;
  $('#ed_item').value=x.item;$('#ed_portion').value=x.portion||'';$('#ed_p').value=x.protein;$('#ed_k').value=x.kcal;$('#ed_f').value=x.fibre;
  let fm=x.fodmap,st=x.status||'';
  const fb=$('#ed_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===fm?' sel':'');
    b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;b.onclick=()=>{fm=k;fb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fb.appendChild(b);});
  const sb=$('#ed_st');[['','library'],['cleared','cleared'],['trigger','trigger'],['test','test']].forEach(([k,l])=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===st?' sel':'');
    b.textContent=l;b.onclick=()=>{st=k;sb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};sb.appendChild(b);});
  $('#ed_save').onclick=async()=>{try{await post('/api/library/'+x.id,{item:$('#ed_item').value,portion:$('#ed_portion').value,
    protein:$('#ed_p').value,kcal:$('#ed_k').value,fibre:$('#ed_f').value,fodmap:fm,status:st});closeEdit();await loadLib();toast('Saved');}catch(er){toast(er.message);}};
  $('#ed_del').onclick=async()=>{if(!confirm('Delete '+x.item+' from the library?'))return;await post('/api/delete/library/'+x.id,{});closeEdit();await loadLib();toast('Deleted');};
  $('#ed_cancel').onclick=closeEdit;
}
function closeEdit(){$('#lib_edit').style.display='none';$('#lib_list').style.display='block';$('#lib_addnew').style.display='inline-block';}
$('#lib_addnew').onclick=()=>editFood({id:0,item:'',portion:'',protein:'',kcal:'',fibre:'',fodmap:'M',status:''});

/* fix add-new-food save (id 0 -> create) */
function editFood(x){
  const isNew=!x.id;
  const e=$('#lib_edit');e.style.display='block';$('#lib_list').style.display='none';$('#lib_addnew').style.display='none';$('#lib_filters').style.display='none';$('#lib_search').style.display='none';
  const CATS=[['A','Grains'],['B','Dals'],['C','Soy/protein'],['D','Dairy'],['E','Sabzi'],['F','Snacks'],['G','Fruit'],['H','Drinks']];
  e.innerHTML=`<p class="q">${isNew?'Add a food':'Edit &middot; '+x.item}</p>
   <div class="row2"><div><p class="lbl">Name</p><input type="text" id="ed_item"></div>
     <div><p class="lbl">Portion</p><input type="text" id="ed_portion"></div></div>
   ${isNew?'<p class="lbl" style="margin-top:8px">Group</p><div class="chips" id="ed_cat"></div>':''}
   <div class="row3" style="margin-top:8px"><div><p class="lbl">Protein</p><input type="number" id="ed_p" step="0.1"></div>
     <div><p class="lbl">kcal</p><input type="number" id="ed_k"></div>
     <div><p class="lbl">Fibre</p><input type="number" id="ed_f" step="0.1"></div></div>
   <p class="lbl" style="margin-top:8px">FODMAP</p><div class="chips" id="ed_fm"></div>
   <p class="lbl" style="margin-top:8px">Status</p><div class="chips" id="ed_st"></div>
   <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
     <button type="button" class="mini" id="ed_save">${isNew?'Add food':'Save changes'}</button>
     ${isNew?'':'<button type="button" class="del" id="ed_del">Delete</button>'}
     <button type="button" class="backlink" id="ed_cancel" style="margin:0 0 0 auto">Cancel</button></div>`;
  $('#ed_item').value=x.item||'';$('#ed_portion').value=x.portion||'';$('#ed_p').value=x.protein;$('#ed_k').value=x.kcal;$('#ed_f').value=x.fibre;
  let fm=x.fodmap||'M',st=x.status||'',cat='F';
  if(isNew){const cb=$('#ed_cat');CATS.forEach(([k,l])=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===cat?' sel':'');
    b.textContent=l;b.onclick=()=>{cat=k;cb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};cb.appendChild(b);});}
  const fb=$('#ed_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===fm?' sel':'');
    b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;b.onclick=()=>{fm=k;fb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fb.appendChild(b);});
  const sb=$('#ed_st');[['','library'],['cleared','cleared'],['trigger','trigger'],['test','test']].forEach(([k,l])=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===st?' sel':'');
    b.textContent=l;b.onclick=()=>{st=k;sb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};sb.appendChild(b);});
  $('#ed_save').onclick=async()=>{
    const item=$('#ed_item').value.trim();if(!item)return toast('Name the food.');
    const body={item,portion:$('#ed_portion').value,protein:$('#ed_p').value,kcal:$('#ed_k').value,fibre:$('#ed_f').value,fodmap:fm,status:st};
    try{if(isNew){body.cat=cat;await post('/api/library',body);}else{await post('/api/library/'+x.id,body);}
      closeEdit();await loadLib();toast('Saved');}catch(er){toast(er.message);}};
  if(!isNew)$('#ed_del').onclick=async()=>{if(!confirm('Delete '+x.item+'?'))return;await post('/api/delete/library/'+x.id,{});closeEdit();await loadLib();toast('Deleted');};
  $('#ed_cancel').onclick=closeEdit;
}
function closeEdit(){$('#lib_edit').style.display='none';$('#lib_list').style.display='block';$('#lib_addnew').style.display='inline-block';$('#lib_filters').style.display='flex';$('#lib_search').style.display='block';}

/* ---------- MEDS: multi-PRN + today + patch ---------- */
async function loadPRN(){PRN=await jget('/api/prnmeds');renderPRNGrid();}
function renderPRNGrid(){
  const g=$('#prnGrid');g.innerHTML='';S.prn.meds=S.prn.meds||[];
  PRN.forEach(m=>{const b=document.createElement('button');b.type='button';b.className='chip'+(S.prn.meds.includes(m)?' sel':'');b.textContent=m;
    b.onclick=()=>{const i=S.prn.meds.indexOf(m);if(i>=0)S.prn.meds.splice(i,1);else S.prn.meds.push(m);b.classList.toggle('sel');};g.appendChild(b);});
}
$('#prnAdd').onclick=async()=>{const n=prompt('Medicine name & strength, e.g. "Ondansetron 4"');if(!n)return;
  try{await post('/api/prnmeds',{name:n});await loadPRN();toast('Added');}catch(e){toast(e.message);}};
async function loadPRNToday(){
  const rows=await jget('/api/doses/today/'+todayISO);const box=$('#prnToday');
  if(!rows.length){box.innerHTML='<p class="hint" style="margin:0">No doses logged today.</p>';return;}
  box.innerHTML='';rows.forEach(r=>{const d=document.createElement('div');d.className='todayrow';
    d.innerHTML=`<b>${r.medicine}</b> &times;${r.times.length}<span class="tms">${r.times.join(', ')}</span>
      <button type="button" class="p1">+1 dose</button>`;
    d.querySelector('.p1').onclick=async()=>{await post('/api/doses/plus',{medicine:r.medicine});await loadPRNToday();await loadRings();toast('+1 '+r.medicine);};
    box.appendChild(d);});
}
async function loadPatch(){
  const p=await jget('/api/patch');const box=$('#patchbox');
  if(p&&p.id){const on=new Date(p.day_on);const days=Math.floor((new Date(todayISO)-on)/86400000)+1;
    box.innerHTML=`<div class="patchcard"><span style="font-size:20px">&#129527;</span>
      <div><b>Patch on</b> &middot; ${p.strength}<br>
      <small style="color:var(--muted)">since ${p.day_on} ${p.time_on||''} &middot; day ${days}</small></div>
      <button type="button" id="patchOff">Remove now</button></div>`;
    $('#patchOff').onclick=async()=>{await post('/api/patch',{action:'off'});await loadPatch();await loadRings();toast('Patch removed');};
  } else {
    box.innerHTML=`<div class="patchcard"><span style="font-size:20px">&#129527;</span>
      <div><b>Transdermal patch</b><br><small style="color:var(--muted)">occasional</small></div>
      <button type="button" id="patchOn">Apply now</button></div>`;
    $('#patchOn').onclick=async()=>{await post('/api/patch',{action:'on',strength:'5 mcg/hr'});await loadPatch();toast('Patch applied');};
  }
}

/* active courses with day counter */
async function loadCourses(){
  const cs=await jget('/api/courses/active');const box=$('#activeCourses');box.innerHTML='';
  if(!cs.length){box.innerHTML='<p class="hint">No active courses.</p>';}
  cs.forEach(c=>{const days=Math.floor((new Date(todayISO)-new Date(c.start_day))/86400000)+1;
    const div=document.createElement('div');div.className='course';
    div.innerHTML=`<span style="font-size:18px">&#128197;</span><div><b>${c.drug}</b><br>
      <small style="color:var(--muted)">day ${days} &middot; from ${c.start_day}</small></div>`;
    const btn=document.createElement('button');btn.type='button';btn.textContent='End';
    btn.onclick=async()=>{const resp=prompt('Response? full / partial / none','partial');if(resp===null)return;
      const map={full:'Helped fully',partial:'Helped partly',none:'No effect'};
      await post('/api/courses/end/'+c.id,{response:map[resp]||resp});await loadCourses();toast('Course ended');};
    div.appendChild(btn);box.appendChild(div);});
}

/* ---------- FOOD TEST ---------- */
function renderTestFoods(){
  const box=$('#f_foods');box.innerHTML='';
  const tests=LIB.filter(x=>x.status==='test').map(x=>x.item);
  ['(new food not in list)'].concat(tests).forEach(v=>{
    const b=document.createElement('button');b.type='button';b.className='chip';b.textContent=v;
    b.onclick=()=>{box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');
      S.test.food=(v==='(new food not in list)')?(prompt('Food name?')||''):v;
      if(v==='(new food not in list)')b.textContent=S.test.food||v;};
    box.appendChild(b);});
}
async function loadRegistry(){
  const r=await jget('/api/library');const map=r.filter(x=>x.status==='cleared'||x.status==='trigger');
  const box=$('#registry');box.innerHTML='';
  map.forEach(x=>{const s=document.createElement('span');s.className='badge '+(x.status==='cleared'?'b-ok':'b-bad');s.textContent=x.item;box.appendChild(s);});
  if(!map.length)box.innerHTML='<p class="hint" style="margin:0">Nothing cleared or flagged yet.</p>';
}

/* ---------- FILES / LABS / CONSULTS ---------- */
$('#u_btn').onclick=async()=>{
  const f=$('#u_file').files[0];if(!f)return toast('Choose a file first.');
  const fd=new FormData();fd.append('file',f);fd.append('day',$('#u_day').value||todayISO);
  fd.append('ftype',$('#u_ftype').value);fd.append('label',$('#u_label').value||f.name);
  try{const r=await fetch('/api/upload',{method:'POST',body:fd});const j=await r.json();
    if(!j.ok)throw new Error(j.err||'Upload failed');
    $('#u_file').value='';$('#u_label').value='';loadFiles();toast('Uploaded');}catch(e){toast(e.message);}
};
async function loadFiles(){
  const rv=await jget('/api/review?days=3650');const box=$('#fileList');box.innerHTML='';
  if(!rv.files.length){box.innerHTML='<p class="hint" style="margin:0">No files yet.</p>';return;}
  rv.files.forEach(f=>{const row=document.createElement('div');row.className='filerow';
    row.innerHTML=`<span>&#128196;</span><a href="/file/${f.id}" target="_blank">${f.label}</a>
      <span class="meta">${f.ftype} &middot; ${f.day} &middot; ${(f.size/1024).toFixed(0)} KB</span>
      <button type="button" class="del" style="margin-left:auto">&times;</button>`;
    row.querySelector('.del').onclick=async()=>{if(!confirm('Delete this file?'))return;await post('/api/delete/files/'+f.id,{});loadFiles();toast('Deleted');};
    box.appendChild(row);});
}
(function(){const sel=$('#l_analyte');['HbA1c','FBS','Creatinine','Hb','TSH','Vitamin B12','Vitamin D','Ferritin','LDL','Triglycerides','CRP'].forEach(a=>{const o=document.createElement('option');o.textContent=a;sel.appendChild(o);});})();
$('#l_btn').onclick=async()=>{try{await post('/api/labs',{analyte:$('#l_analyte').value,value:$('#l_value').value,day:$('#l_day').value||todayISO});
  $('#l_value').value='';loadLabs();toast('Result saved');}catch(e){toast(e.message);}};
async function loadLabs(){
  const rows=await jget('/api/labs/status');const g=$('#labGrid');g.innerHTML='';
  rows.forEach(r=>{const overdue=r.next_due&&r.next_due<todayISO;
    const c=document.createElement('div');c.className='labcard';
    c.innerHTML=`<div class="nm">${r.analyte}</div>
      <div class="vl">${r.last_value!=null?r.last_value+' <span style="font-size:11px;color:var(--muted)">'+r.unit+'</span>':'&mdash;'}</div>
      <div class="du ${overdue?'over':''}">${r.last_day?('last '+r.last_day):'no data'}${r.flare_only?' &middot; on flare':r.next_due?(' &middot; due '+r.next_due):''}</div>`;
    g.appendChild(c);});
}
let kDoctor='';
async function loadDoctors(){
  const ds=await jget('/api/doctors');const box=$('#k_doctors');box.innerHTML='';
  ds.forEach(d=>{const b=document.createElement('button');b.type='button';b.className='chip';b.textContent=d;
    b.onclick=()=>{box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');kDoctor=d;};box.appendChild(b);});
}
$('#k_adddoc').onclick=async()=>{const n=prompt('Doctor name / specialty');if(!n)return;await post('/api/doctors',{name:n});await loadDoctors();toast('Added');};

/* ---------- REVIEW ---------- */
let rvDays=30;
function svgLine(data,keys,cols,h=120){
  if(!data.length)return '<p class="hint" style="margin:0">Not enough data yet.</p>';
  const W=320,P=6,n=data.length;const xs=i=>P+(W-2*P)*(n<2?0.5:i/(n-1));
  let max=1;keys.forEach(k=>data.forEach(d=>{if(d[k]!=null&&d[k]>max)max=d[k];}));
  const ys=v=>h-P-(h-2*P)*(v/max);
  let s=`<svg class="chart" viewBox="0 0 ${W} ${h}">`;
  keys.forEach((k,ki)=>{let path='',pts='';data.forEach((d,i)=>{if(d[k]==null)return;
    const x=xs(i).toFixed(1),y=ys(d[k]).toFixed(1);path+=(path?'L':'M')+x+' '+y+' ';pts+=`<circle cx="${x}" cy="${y}" r="2" fill="${cols[ki]}"/>`;});
    s+=`<path d="${path}" fill="none" stroke="${cols[ki]}" stroke-width="2"/>${pts}`;});
  s+=`<text x="${P}" y="10" font-size="9" fill="#5B7370">max ${max}</text></svg>`;
  return s;
}
function svgBars(data,key,col,h=110){
  if(!data.length)return '<p class="hint" style="margin:0">No data yet.</p>';
  const W=320,P=6,n=data.length,bw=Math.max(2,(W-2*P)/n-2);let max=1;data.forEach(d=>{if(d[key]>max)max=d[key];});
  let s=`<svg class="chart" viewBox="0 0 ${W} ${h}">`;
  data.forEach((d,i)=>{const x=P+(W-2*P)*i/n,bh=(h-2*P)*(d[key]/max);
    s+=`<rect x="${x.toFixed(1)}" y="${(h-P-bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${col}"/>`;});
  s+=`<text x="${P}" y="10" font-size="9" fill="#5B7370">max ${max}</text></svg>`;return s;
}
/* ---------- STOCK (GUTLOG_V360_PHASE_C) ---------- */
function fmtQ(v){return String(Math.round(v*10)/10);}
async function loadStockAlerts(){
  const box=$('#nowStock');if(!box)return;
  try{
    const j=await jget('/api/stock');
    const al=j.rows.filter(r=>r.level);
    box.innerHTML='';
    if(!al.length)return;
    const d=document.createElement('div');
    d.className='stockalert '+(al.some(r=>r.level==='RED')?'red':'amber');
    d.innerHTML='<b>Refill</b><span></span>';
    d.querySelector('span').textContent=al.map(r=>r.name+' - '+r.why).join(' · ');
    d.onclick=()=>{switchTab('meds');setSeg('meds','stock');};
    box.appendChild(d);
  }catch(e){}
}
async function loadStock(){
  const j=await jget('/api/stock');
  const pb=$('#stPill');
  pb.querySelector('.st-last').textContent=j.last_fill?('Last filled '+j.last_fill):'Not filled yet.';
  const days=()=>Math.max(1,Math.min(31,parseInt($('#stDays').value||'7',10)||7));
  const prev=()=>{
    const pv=pb.querySelector('.st-prev');
    pv.textContent=j.fill_preview.length
      ?('Filling '+days()+' days takes: '+j.fill_preview.map(x=>x.name+' '+fmtQ(x.per_day*days())).join(' · '))
      :'No counted pillbox medicines yet - count them below first.';
  };
  prev();
  $('#stDays').oninput=prev;
  $('#stFill').onclick=async()=>{
    try{const r=await post('/api/stock/fill',{days:days()});
      toast('Pillbox filled: '+r.n+' medicines, '+r.days+' days');loadStock();loadStockAlerts();}
    catch(err){toast(err.message);}
  };
  $('#stFillUndo').onclick=async()=>{
    if(!confirm('Undo the last pillbox fill?'))return;
    try{await post('/api/stock/fill/undo',{});toast('Last fill undone');loadStock();loadStockAlerts();}
    catch(err){toast(err.message);}
  };
  const list=$('#stList');list.innerHTML='';
  j.rows.forEach(r=>list.appendChild(stockRow(r)));
}
function stockRow(r){
  const w=document.createElement('div');
  w.className='strow'+(r.level?(' lv-'+r.level.toLowerCase()):'')+(r.tracked?'':' untracked');
  w.innerHTML='<div class="sh"><b></b><span class="sq"></span></div><div class="ss"></div>'+
    '<div class="sb"></div><div class="vtm sf" hidden><input type="number" step="0.5" min="0" inputmode="decimal">'+
    '<button type="button" class="btn tiny go">Save</button></div>';
  w.querySelector('.sh b').textContent=r.name;
  const sq=w.querySelector('.sq'),ss=w.querySelector('.ss'),sb=w.querySelector('.sb');
  const modeTxt=r.mode==='pillbox'?'pillbox':'per dose';
  if(!r.trackable){sq.textContent='';ss.textContent=r.why;w.querySelector('.sf').remove();return w;}
  if(r.tracked){
    sq.textContent=fmtQ(r.current)+' left';
    const bits=[modeTxt];
    if(r.per_day>0)bits.push(fmtQ(r.per_day)+'/day');
    if(r.why)bits.push(r.why);
    else if(r.days_left!=null)bits.push('about '+Math.floor(r.days_left)+' days');
    ss.textContent=bits.join(' · ');
  }else{
    sq.textContent='';
    ss.textContent=modeTxt+' · not counted yet';
  }
  const sf=w.querySelector('.sf'),inp=sf.querySelector('input'),go=sf.querySelector('button');
  let act='';
  const open=(a,val,ph)=>{act=a;sf.hidden=false;inp.value=val;inp.placeholder=ph;inp.focus();};
  const mk=(txt,fn)=>{const b=document.createElement('button');b.type='button';b.className='btn tiny ghost';
    b.textContent=txt;b.onclick=fn;sb.appendChild(b);};
  mk(r.tracked?'Count':'Set count',()=>open('count','','how many left'));
  if(r.tracked)mk('Bought',()=>open('add',r.pack_size||'','how many bought'));
  if(r.can_pillbox)mk(r.mode==='pillbox'?'Make per dose':'Make pillbox',async()=>{
    try{await post('/api/stock/mode',{med_id:r.med_id,mode:r.mode==='pillbox'?'per_dose':'pillbox'});
      loadStock();loadStockAlerts();}catch(err){toast(err.message);}
  });
  go.onclick=async()=>{
    if(inp.value===''){toast('Enter a number');return;}
    try{
      await post(act==='add'?'/api/stock/add':'/api/stock/count',{med_id:r.med_id,qty:inp.value});
      toast(act==='add'?'Added':'Count saved');loadStock();loadStockAlerts();
    }catch(err){toast(err.message);}
  };
  return w;
}

/* ---------- VITALS LOG (GUTLOG_V360_PHASE_C) ---------- */
function vtEsc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);}
function vitalsChart(rows){
  const bp=rows.filter(v=>v.sys||v.dia).slice().reverse();
  if(bp.length<2)return '<p class="hint" style="margin:0">The chart appears after two readings.</p>';
  const W=320,H=150,P=24;
  let lo=1e9,hi=0;
  bp.forEach(v=>[v.sys,v.dia,v.pulse].forEach(x=>{
    if(x){if(x<lo)lo=x;if(x>hi)hi=x;}
  }));
  lo=Math.min(lo,90)-8;hi=Math.max(hi,140)+8;
  const xs=i=>P+(W-P-6)*(i/(bp.length-1));
  const ys=v=>H-16-(H-26)*((v-lo)/(hi-lo));
  let s='<svg class="chart" viewBox="0 0 '+W+' '+H+'">';
  [140,90].forEach(gl=>{const y=ys(gl).toFixed(1);
    s+='<line x1="'+P+'" x2="'+W+'" y1="'+y+'" y2="'+y+'" stroke="#C9D6D0" stroke-dasharray="3 3"/>'+
       '<text x="2" y="'+(parseFloat(y)+3)+'" font-size="9" fill="#5B7370">'+gl+'</text>';});
  [['sys','#B3372A'],['dia','#0B6E6E'],['pulse','#C8860A']].forEach(k=>{
    let p='',dots='';
    bp.forEach((v,i)=>{if(!v[k[0]])return;const x=xs(i).toFixed(1),y=ys(v[k[0]]).toFixed(1);
      p+=(p?'L':'M')+x+' '+y+' ';dots+='<circle cx="'+x+'" cy="'+y+'" r="2" fill="'+k[1]+'"/>';});
    if(p)s+='<path d="'+p+'" fill="none" stroke="'+k[1]+'" stroke-width="2"/>'+dots;
  });
  s+='<text x="'+P+'" y="'+(H-3)+'" font-size="9" fill="#5B7370">'+vtEsc(bp[0].day)+'</text>'+
     '<text x="'+(W-58)+'" y="'+(H-3)+'" font-size="9" fill="#5B7370">'+vtEsc(bp[bp.length-1].day)+'</text></svg>';
  return s;
}
function renderVitals(rows){
  const tb=$('#vtTable');if(!tb)return;
  rows=rows||[];
  $('#vtChart').innerHTML=vitalsChart(rows);
  const bp=rows.filter(v=>v.sys&&v.dia);
  const av=a=>Math.round(a.reduce((x,y)=>x+y,0)/a.length);
  const avBP=a=>a.length?(av(a.map(v=>v.sys))+'/'+av(a.map(v=>v.dia))):'';
  const sum=$('#vtSum');sum.innerHTML='';
  const line=t=>{const p=document.createElement('p');p.className='tot';p.textContent=t;sum.appendChild(p);};
  if(bp.length){
    const pl=bp.filter(v=>v.pulse).map(v=>v.pulse);
    line(bp.length+' readings · average '+avBP(bp)+(pl.length?(' · pulse '+av(pl)):''));
    const am=bp.filter(v=>(v.vtime||'')<'12:00'),pm=bp.filter(v=>(v.vtime||'')>='17:00');
    if(am.length&&pm.length)line('Morning '+avBP(am)+' · evening '+avBP(pm));
    const hiR=bp.reduce((a,b)=>b.sys>a.sys?b:a),loR=bp.reduce((a,b)=>b.sys<a.sys?b:a);
    line('Highest '+hiR.sys+'/'+hiR.dia+' ('+hiR.day+' '+(hiR.vtime||'')+') · lowest '+loR.sys+'/'+loR.dia);
  }else line('No blood pressure readings in this range.');
  if(!rows.length){tb.innerHTML='';return;}
  let t='<table><tr><th>Day</th><th>Time</th><th>BP</th><th>Pulse</th><th>Wt</th><th>Temp</th></tr>';
  rows.slice(0,300).forEach(v=>{
    t+='<tr><td>'+vtEsc(v.day)+'</td><td>'+vtEsc(v.vtime)+'</td><td><b>'+
      ((v.sys||v.dia)?(vtEsc(v.sys||'-')+'/'+vtEsc(v.dia||'-')):'')+'</b></td><td>'+vtEsc(v.pulse||'')+
      '</td><td>'+vtEsc(v.weight||'')+'</td><td>'+vtEsc(v.temp||'')+'</td></tr>';
  });
  tb.innerHTML=t+'</table>';
}

/* ---------- DAY BY DAY (GUTLOG_V350_PHASE_B) ----------
   Everything logged on one day, every stream, in time order. Tap an
   entry to move it to the time (or day) it really happened, or delete it.
   Below: that day's scheduled doses never logged, for backfilling. */
let dvDay=todayISO;
const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',BP:'bp',Vitals:'bp',Meal:'meal'};
function dvShift(n){
  const d=new Date(dvDay+'T12:00:00');d.setDate(d.getDate()+n);
  const s=d.toLocaleDateString('en-CA');
  if(s>todayISO)return;
  dvDay=s;loadDayView();
}
async function loadDayView(){
  const di=$('#dvDate');if(!di)return;
  di.value=dvDay;di.max=todayISO;
  $('#dvNext').disabled=(dvDay>=todayISO);
  const both=await Promise.all([jget('/api/dayview?day='+dvDay),jget('/api/now?day='+dvDay)]);
  const v=both[0],n=both[1];
  const box=$('#dvList');box.innerHTML='';
  $('#dvSum').textContent=v.entries.length?(v.entries.length+' logged'):'nothing logged';
  v.entries.forEach(e=>{
    const r=document.createElement('div');r.className='dvrow';
    r.innerHTML='<span class="t"></span><span class="tag"></span><div class="x"><b></b><span></span></div>';
    r.querySelector('.t').textContent=e.time||'--:--';
    const tg=r.querySelector('.tag');tg.textContent=e.kind;tg.classList.add('k-'+(DV_TAG[e.kind]||'x'));
    r.querySelector('.x b').textContent=e.title;
    r.querySelector('.x span').textContent=[e.sub,e.edited?'time edited':''].filter(Boolean).join(' · ');
    r.onclick=()=>dvEdit(r,e);
    box.appendChild(r);
  });
  const miss=[];
  (n.slots||[]).forEach(s=>s.rows.forEach(x=>{if(!x.status)miss.push([s,x]);}));
  const mb=$('#dvMiss');mb.innerHTML='';
  $('#dvMissHd').style.display=miss.length?'':'none';
  miss.forEach(p=>mb.appendChild(dvMissRow(p[0],p[1])));
}
function dvEdit(rowEl,e){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick';
  box.innerHTML='<p class="vt"></p><div class="vtm"><input type="date" class="dd"><input type="time" class="tt"></div>'+
    '<div class="vb three"><button type="button" class="cx">Cancel</button>'+
    '<button type="button" class="danger dl">Delete</button><button type="button" class="go">Save</button></div>';
  box.querySelector('.vt').textContent=e.title+' - set the real time';
  const dd=box.querySelector('.dd'),tt=box.querySelector('.tt');
  dd.value=dvDay;dd.max=todayISO;tt.value=e.time||'';
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!tt.value||!dd.value){toast('Pick a day and time');return;}
    try{
      const r=await post('/api/retime',{table:e.tbl,id:e.id,day:dd.value,time:tt.value});
      toast(r.unchanged?'No change':('Moved to '+tt.value+(dd.value!==dvDay?(' on '+dd.value):'')));
      loadDayView();
    }catch(err){toast(err.message);}
  };
  box.querySelector('.dl').onclick=async()=>{
    if(!confirm('Delete this entry?'))return;
    try{await post('/api/delete/'+e.tbl+'/'+e.id,{});toast('Deleted');loadDayView();}
    catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function dvMissRow(s,x){
  const w=document.createElement('div');w.className='dvmiss';
  w.innerHTML='<div class="mh"><span class="sl"></span><b></b></div><div class="vrow"></div>'+
    '<div class="vtm"><input type="time" class="tt"><button type="button" class="btn tiny ghost sk">Skipped</button>'+
    '<button type="button" class="btn tiny go">Taken</button></div>';
  w.querySelector('.sl').textContent=s.label;
  w.querySelector('b').textContent=x.name+(x.dose_text?(' '+x.dose_text):'');
  const tt=w.querySelector('.tt');
  tt.value=(dvDay===todayISO&&s.time>nowHM())?nowHM():s.time;
  const picked=[],vr=w.querySelector('.vrow');
  if(x.variants){
    x.variants.split('|').map(v=>v.trim()).filter(Boolean).forEach(v=>{
      const b=document.createElement('div');b.className='chip';b.textContent=v;
      b.onclick=()=>{const i=picked.indexOf(v);if(i>=0)picked.splice(i,1);else picked.push(v);
        b.classList.toggle('sel',picked.indexOf(v)>=0);};
      vr.appendChild(b);
    });
  }else vr.remove();
  const send=async st=>{
    if(!tt.value){toast('Pick a time');return;}
    if(st==='TAKEN'&&x.variants&&!picked.length){toast('Pick a dose');return;}
    try{
      await post('/api/now/dose',{med_id:x.med_id,sched_id:x.sched_id,status:st,day:dvDay,
        dtime:tt.value,dose_text:x.variants?picked.join(' + '):x.dose_text});
      toast((st==='TAKEN'?'Logged ':'Skipped ')+x.name+' at '+tt.value);loadDayView();
    }catch(err){toast(err.message);}
  };
  w.querySelector('.go').onclick=()=>send('TAKEN');
  w.querySelector('.sk').onclick=()=>send('SKIPPED');
  return w;
}
$('#dvPrev').onclick=()=>dvShift(-1);
$('#dvNext').onclick=()=>dvShift(1);
$('#dvDate').onchange=ev=>{
  const v=ev.target.value;
  if(v&&v<=todayISO){dvDay=v;loadDayView();}
};

async function loadReview(){
  loadDayView();
  $('#rvDaysN').textContent=rvDays;
  const rv=await jget('/api/review?days='+rvDays);
  renderVitals(rv.vitals);
  const dmap={};rv.days.forEach(d=>dmap[d.day=d.day]=d);
  $('#chartMain').innerHTML=svgLine(rv.days,['pain','tea','coffee'],['#B3372A','#C8860A','#8A5A2B']);
  const fmapData=rv.daily.map(d=>({day:d.day,fscore:d.fscore,sym:0}));
  const symSet=new Set(rv.days.filter(d=>(d.syms&&d.syms.length)||(d.pain>0)).map(d=>d.day));
  fmapData.forEach(d=>d.sym=symSet.has(d.day)?d.fscore:null);
  $('#chartFmap').innerHTML=svgLine(rv.daily.length?rv.daily:[{fscore:0}],['fscore'],['#0B6E6E']);
  $('#chartDose').innerHTML=svgBars(rv.dosecount,'n','#7A4FBF');
  const hit=rv.daily.filter(d=>d.protein>=rv.target).length,tot=rv.daily.length||1;
  $('#proteinHit').innerHTML=`<div class="pbar"><i style="width:${(hit/tot*100).toFixed(0)}%"></i></div>
    <p class="tot"><b>${hit}</b> of ${tot} logged days hit ${rv.target} g protein</p>`;
  /* recent doses table */
  const dt=$('#rvDoses');
  if(rv.doses.length){let t='<table><tr><th>Day</th><th>Time</th><th>Medicine</th><th>Effect</th><th></th></tr>';
    rv.doses.slice(0,40).forEach(d=>{t+=`<tr><td>${d.day}</td><td>${d.dtime||''}</td><td>${d.medicine}</td><td>${d.effect||''}</td>
      <td><button class="del" data-t="doses" data-i="${d.id}">&times;</button></td></tr>`;});
    dt.innerHTML=t+'</table>';}else dt.innerHTML='<p class="hint" style="margin:0">No doses in range.</p>';
  /* episodes */
  const et=$('#rvEpisodes');
  if(rv.episodes.length){let t='<table><tr><th>Day</th><th>Time</th><th>Type</th><th>Sev</th><th></th></tr>';
    rv.episodes.slice(0,30).forEach(d=>{t+=`<tr><td>${d.day}</td><td>${d.etime||''}</td><td>${d.etype}${d.side?' ('+d.side+')':''}</td><td>${d.severity??''}</td>
      <td><button class="del" data-t="episodes" data-i="${d.id}">&times;</button></td></tr>`;});
    et.innerHTML=t+'</table>';}else et.innerHTML='<p class="hint" style="margin:0">No episodes in range.</p>';
  /* patch history */
  const pt=$('#rvPatch');
  if(rv.patches.length){let t='<table><tr><th>On</th><th>Off</th><th>Days</th><th></th></tr>';
    rv.patches.forEach(p=>{const days=p.day_off?Math.floor((new Date(p.day_off)-new Date(p.day_on))/86400000)+1:'on';
      t+=`<tr><td>${p.day_on} ${p.time_on||''}</td><td>${p.day_off||'<b style="color:var(--hip)">worn</b>'}</td><td>${days}</td>
      <td><button class="del" data-t="patches" data-i="${p.id}">&times;</button></td></tr>`;});
    pt.innerHTML=t+'</table>';}else pt.innerHTML='<p class="hint" style="margin:0">No patch records.</p>';
  /* registry */
  const rg=$('#rvRegistry');rg.innerHTML='';
  rv.registry.forEach(x=>{const s=document.createElement('span');s.className='badge '+(x.status==='cleared'?'b-ok':'b-bad');s.textContent=x.item;rg.appendChild(s);});
  if(!rv.registry.length)rg.innerHTML='<p class="hint" style="margin:0">Empty.</p>';
  /* export links */
  const ex=['days','meals','doses','episodes','vitals','foodtests','courses','patches','consults','labs','library'];
  $('#expLinks').innerHTML=ex.map(t=>`<a class="exp" href="/export/${t}.csv">${t}.csv</a>`).join('');
  /* wire delete buttons */
  $$('#tab-review .del').forEach(b=>b.onclick=async()=>{if(!confirm('Delete this row?'))return;
    await post('/api/delete/'+b.dataset.t+'/'+b.dataset.i,{});loadReview();toast('Deleted');});
}
$$('#rvRange button').forEach(b=>b.onclick=()=>{$$('#rvRange button').forEach(x=>x.classList.remove('sel'));b.classList.add('sel');rvDays=+b.dataset.d;loadReview();});

/* ---------- SAVE dispatcher ---------- */
async function saveDay(){
  await post('/api/day',{day:$('#s_day').value||todayISO,syms:selectedSyms(),
    pain:S.log.pain,pain_site:S.log.pain_site,bristol:S.log.bristol,stools:S.log.stools,
    tea:S.log.tea||0,coffee:S.log.coffee||0,sleep:S.log.sleep,walk:S.log.walk,
    treadmill:S.log.treadmill,meditation:S.log.meditation,notes:$('#s_notes').value});
  await loadRings();
}
async function saveMeal(){
  if(!basket.length)throw new Error('Add at least one food.');
  const r=await post('/api/meals',{day:$('#ml_day').value||todayISO,mtime:$('#ml_time').value||nowHM(),
    slot:S.meal.slot||'Meal',notes:$('#ml_notes').value,
    items:basket.map(b=>({n:b.item,q:b.q,p:b.p,k:b.k,f:b.f,fm:b.fm}))});
  basket=[];renderBasket();$('#ml_notes').value='';await loadRings();await loadMealTotals();
}
async function loadMealTotals(){
  const rows=await jget('/api/meals/today/'+ (($('#ml_day').value)||todayISO));
  let p=0,k=0,f=0,fs=0,nq=0;rows.forEach(m=>{p+=m.protein;k+=m.kcal;f+=m.fibre;fs+=m.fscore;
    m.items.forEach(it=>nq+=it.q);});
  $('#dayTotals').innerHTML=`Today: <b>${p.toFixed(1)} g protein</b> &middot; ${Math.round(k)} kcal &middot; ${f.toFixed(1)} g fibre &middot; ${rows.length} meal(s)`;
  $('#dayPbar').style.width=Math.min(100,p/57*100)+'%';
  const avg=nq?fs/nq:0;const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
  $('#dayFmap').innerHTML=rows.length?`FODMAP load today: <b>${lab}</b>`:'';
}
async function saveEpisode(){
  if(!S.episode.etype)throw new Error('Pick an episode type.');
  await post('/api/episodes',{day:$('#e_day').value||todayISO,etime:$('#e_time').value||nowHM(),
    category:S.episode.category,etype:S.episode.etype,side:S.episode.side,
    severity:S.episode.severity,duration:S.episode.duration,notes:$('#e_notes').value});
  resetChips('episode');$('#e_notes').value='';await loadRings();
}
async function saveVitals(){
  await post('/api/vitals',{day:$('#v_day').value||todayISO,vtime:$('#v_time').value||nowHM(),
    sys:$('#v_sys').value,dia:$('#v_dia').value,pulse:$('#v_pulse').value,temp:$('#v_temp').value,
    weight:$('#v_weight').value,waist:$('#v_waist').value,notes:$('#v_notes').value});
  ['v_sys','v_dia','v_pulse','v_temp','v_weight','v_waist','v_notes'].forEach(id=>$('#'+id).value='');await loadRings();
}
async function saveDose(){
  if(!S.prn.meds||!S.prn.meds.length)throw new Error('Tap at least one medicine.');
  await post('/api/doses',{day:$('#m_day').value||todayISO,dtime:$('#m_time').value||nowHM(),
    meds:S.prn.meds,reason:S.prn.reason,effect:S.prn.effect,notes:$('#m_notes').value});
  S.prn={};renderPRNGrid();resetChips('prn');$('#m_notes').value='';await loadPRNToday();await loadRings();
}
async function saveCourse(){
  if(!S.course||!S.course.drug)throw new Error('Pick a drug.');
  await post('/api/courses',{drug:S.course.drug,start_day:$('#c_day').value||todayISO,notes:$('#c_notes').value});
  S.course={};resetChips('course');$('#c_notes').value='';await loadCourses();
}
async function saveTest(){
  if(!S.test.food)throw new Error('Pick a food.');
  await post('/api/foodtests',{day:$('#f_day').value||todayISO,food:S.test.food,portion:S.test.portion,
    symptoms:S.test.symptoms,severity:S.test.severity,verdict:S.test.verdict,notes:$('#f_notes').value});
  resetChips('test');$('#f_notes').value='';await loadLib();renderTestFoods();loadRegistry();
}
async function saveConsult(){
  if(!kDoctor)throw new Error('Pick a doctor.');
  await post('/api/consults',{day:$('#k_day').value||todayISO,doctor:kDoctor,reason:$('#k_reason').value,
    advice:$('#k_advice').value,changes:$('#k_changes').value,next_visit:$('#k_next').value});
  ['k_reason','k_advice','k_changes'].forEach(id=>$('#'+id).value='');toast('Visit logged');
}
const SAVE={
  'log:day':saveDay,'log:episode':saveEpisode,'log:vitals':saveVitals,
  'meals:meal':saveMeal,'meals:test':saveTest,'meals:foods':null,
  'meds:prn':saveDose,'meds:course':saveCourse,
  'files:vault':null,'files:labs':null,'files:consults':saveConsult,
};
$('#saveBtn').onclick=async()=>{
  const key=tab+':'+seg[tab];const fn=SAVE[key];
  if(!fn){toast('Use the button in this section.');return;}
  const btn=$('#saveBtn');btn.disabled=true;const old=btn.textContent;
  try{await fn();btn.textContent='Saved \u2713';btn.classList.add('done');
    setTimeout(()=>{btn.textContent=old;btn.classList.remove('done');},1200);}
  catch(e){toast(e.message);}finally{btn.disabled=false;}
};

/* ---------- tab + segment switching ---------- */
function saveBtnVisible(){
  const hide=(tab==='review')||(tab==='meals'&&seg.meals==='foods')||
    (tab==='files'&&(seg.files==='vault'||seg.files==='labs'))||(tab==='meds'&&seg.meds==='stock');
  $('.save').style.display=hide?'none':'flex';
  const L={'log:day':'Save day','log:episode':'Save episode','log:vitals':'Save vitals',
    'meals:meal':'Save meal','meals:test':'Save food test','meds:prn':'Log dose',
    'meds:course':'Start course','files:consults':'Save visit'};
  const sb=$('#saveBtn');
  if(tab==='now'){sb.style.display='none';return;}
  sb.style.display='';
  $('#saveBtn').textContent=L[tab+':'+seg[tab]]||'Save';
}
function switchTab(t){
  tab=t;$$('#nav button').forEach(b=>b.classList.toggle('sel',b.dataset.t===t));
  $$('.tab').forEach(s=>s.classList.toggle('sel',s.id==='tab-'+t));
  saveBtnVisible();
  if(t==='review')loadReview();
  if(t==='files'){loadFiles();loadLabs();loadDoctors();}
  if(t==='meals'){loadMealTotals();loadRegistry();renderTestFoods();}
  if(t==='now')loadNow();
  if(t==='meds'){loadPRNToday();loadPatch();loadCourses();}
  if(t==='meds'&&seg.meds==='sched'){loadSchedMeds();loadSchedule();}
  if(t==='meds'&&seg.meds==='stock')loadStock();
  window.scrollTo(0,0);
}
function setSeg(section,s){
  seg[section]=s;
  if(section==='meds'&&s==='stock')loadStock();
  if(section==='meds'&&s==='sched'){loadSchedMeds();loadSchedule();}
  $$(`.seg[data-seg="${section}"] button`).forEach(b=>b.classList.toggle('sel',b.dataset.s===s));
  $$(`#tab-${section} .sub`).forEach(el=>el.classList.remove('sel'));
  const el=$(`#${section}-${s}`);if(el)el.classList.add('sel');
  saveBtnVisible();
}
$$('#nav button').forEach(b=>b.onclick=()=>switchTab(b.dataset.t));
$$('.seg').forEach(box=>{const section=box.dataset.seg;
  box.querySelectorAll('button').forEach(b=>b.onclick=()=>setSeg(section,b.dataset.s));});

/* segment chips that set state (slot, drug, reason, etc via generic buildChips already handle S) */
buildChips();


/* ---------- NOW tab ---------- */
const SEVS=['1','2','3','4','5','6','7','8','9','10'];
const SYMTYPES=['Abdominal pain','Cramp','Bloating','Urgency','Loose stool','Constipation','Nausea','Reflux','Other'];
let nowData=null, nSym={types:[],sev:null,bristol:null}, showAllMeds=false;
/* GUTLOG_V342_PAINSITE -- each site carries its own score; present in
   nPain = selected, value '' = selected but not yet scored */
const PAIN_SITES=['Left iliac pain','Hypogastrium pain'];
let nPain={};

/* Collapsible cards. Only blood pressure stays open; the rest carry their
   state in the header so the summary is readable without expanding. */
function bindFolds(){
  $$('.card.fold .fold-h').forEach(h=>{
    h.onclick=()=>{
      const card=h.closest('.card');
      card.classList.toggle('open');
      if(card.classList.contains('open'))
        setTimeout(()=>card.scrollIntoView({behavior:'smooth',block:'nearest'}),80);
    };
  });
}

function nowRow(r){
  const st=r.status||'';
  const cls=st==='TAKEN'?'done':(st==='SKIPPED'?'skip':'');
  const mark=st==='TAKEN'?'&#10003;':(st==='SKIPPED'?'&#8212;':'');
  const shown=(st==='TAKEN'&&r.logged_dose)?r.logged_dose:(r.variants&&!st?'dose varies':(r.dose_text||''));
  const sub=[shown,r.with_food&&r.with_food!=='ANY'?r.with_food.toLowerCase()+' food':'',
             st?(st==='TAKEN'?'taken '+(r.dtime||''):'skipped'):'',
             st?'tap to change':''].filter(Boolean).join(' \u00b7 ');
  const d=document.createElement('div');
  d.className='doserow '+cls;
  d.innerHTML='<div class="tick">'+mark+'</div><div class="nm"><b></b><span></span></div>'+
              (st?'<button type="button" class="sk">undo</button>':'<button type="button" class="sk">skip</button>');
  d.querySelector('.nm b').textContent=r.name;
  d.querySelector('.nm span').textContent=sub;
  d.onclick=async e=>{
    if(e.target.classList.contains('sk'))return;
    if(st){openRowActions(d,r,st);return;}
    if(r.variants){openVariantPicker(d,r);return;}
    try{await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,status:'TAKEN',
      day:nowData.day,dose_text:r.dose_text});toast('Logged '+r.name);loadNow();}
    catch(err){toast(err.message);}
  };
  d.querySelector('.sk').onclick=async ev=>{
    ev.stopPropagation();
    try{
      if(st&&r.dose_id){await post('/api/now/undo/'+r.dose_id,{});toast('Undone');}
      else{await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,status:'SKIPPED',
        day:nowData.day,dose_text:r.dose_text});toast('Marked skipped');}
      loadNow();
    }catch(err){toast(err.message);}
  };
  return d;
}

/* Tapping a row that is already logged. The instinct is to tap the thing
   again, so that has to do something -- but an accidental second tap must
   not silently delete a medication record, hence a strip rather than an
   immediate toggle. */
function openRowActions(rowEl,r,st){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');
  box.className='varpick';
  const canChange=!!r.variants;
  let html='<p class="vt"></p><div class="vb'+(canChange?' three':'')+'">'+
    '<button type="button" class="cx">Cancel</button>';
  if(canChange)html+='<button type="button" class="ch">Change dose</button>';
  if(st==='TAKEN')html+='<button type="button" class="sp">Skip</button>';
  html+='<button type="button" class="danger un">Undo</button></div>';
  if(r.dose_id)html+='<div class="vtm"><span class="lb">Time</span><input type="time" class="tt"><button type="button" class="btn tiny tm">Save time</button></div>';
  box.innerHTML=html;
  const was=st==='TAKEN'?('taken'+(r.logged_dose?' '+r.logged_dose:'')):'skipped';
  box.querySelector('.vt').textContent=r.name+' - '+was;
  const tt=box.querySelector('.tt');
  if(tt){
    tt.value=r.dtime||'';
    box.querySelector('.tm').onclick=async()=>{
      if(!tt.value){toast('Pick a time');return;}
      try{await post('/api/retime',{table:'doses',id:r.dose_id,day:nowData.day,time:tt.value});
        toast('Time set to '+tt.value);box.remove();loadNow();}
      catch(err){toast(err.message);}
    };
  }
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.un').onclick=async()=>{
    try{
      if(r.dose_id)await post('/api/now/undo/'+r.dose_id,{});
      toast('Undone');box.remove();loadNow();
    }catch(err){toast(err.message);}
  };
  const ch=box.querySelector('.ch');
  if(ch)ch.onclick=()=>{box.remove();openVariantPicker(rowEl,r);};
  const sp=box.querySelector('.sp');
  if(sp)sp.onclick=async()=>{
    try{
      await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,
        status:'SKIPPED',day:nowData.day});
      toast('Marked skipped');box.remove();loadNow();
    }catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

/* A scheduled medicine whose dose varies. Multi-select, because a
   combination such as 145 + 72 is one dose, not two. */
function openVariantPicker(rowEl,r){
  if(document.querySelector('.varpick'))document.querySelector('.varpick').remove();
  const opts=r.variants.split('|').map(s=>s.trim()).filter(Boolean);
  const picked=[];
  const box=document.createElement('div');
  box.className='varpick';
  box.innerHTML='<p class="vt"></p><div class="vrow"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button>'+
    '<button type="button" class="go">Log</button></div>';
  box.querySelector('.vt').textContent=r.name+' - which dose?';
  const vrow=box.querySelector('.vrow');
  opts.forEach(v=>{
    const b=document.createElement('div');b.className='chip';b.textContent=v;
    b.onclick=()=>{
      const i=picked.indexOf(v);
      if(i>=0)picked.splice(i,1);else picked.push(v);
      b.classList.toggle('sel',picked.indexOf(v)>=0);
    };
    vrow.appendChild(b);
  });
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!picked.length){toast('Pick a dose');return;}
    const txt=picked.join(' + ');
    try{
      await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,
        status:'TAKEN',day:nowData.day,dose_text:txt,
        dtime:(r.status==='TAKEN'&&r.dtime)?r.dtime:undefined});
      toast('Logged '+r.name+' '+txt);box.remove();loadNow();
    }catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

async function loadNow(){
  loadStockAlerts();
  nowData=await jget('/api/now?day='+todayISO);
  const box=$('#nowSched');box.innerHTML='';
  let done=0,total=0;
  if(!nowData.has_schedule){
    box.innerHTML='<p class="hint" style="margin:0">No regular medicines yet. '+
      'Add them under <b>Meds &rarr; Schedule</b>.</p>';
    $('#doseSum').textContent='none set';
  }else{
    nowData.slots.forEach(s=>{
      done+=s.done;total+=s.total;
      const hd=document.createElement('div');hd.className='slothd';
      hd.innerHTML='<span class="sl"></span><span class="cnt"></span>';
      hd.querySelector('.sl').textContent=s.label;
      hd.querySelector('.cnt').textContent=s.done+'/'+s.total;
      box.appendChild(hd);
      s.rows.forEach(r=>box.appendChild(nowRow(r)));
    });
    $('#doseSum').textContent=done+' of '+total+' taken';
    const card=$('#nowDoses');
    if(done<total)card.classList.add('pending');else card.classList.remove('pending');
  }

  /* extras: scheduled medicines are hidden, since an expected dose belongs
     on the dose card, not here. Show all reveals them for the rare
     unplanned dose of a regular medicine. */
  const ex=$('#nowExtras');ex.innerHTML='';
  const list=showAllMeds?(nowData.meds_all||nowData.meds):nowData.meds;
  list.forEach(m=>{
    const b=document.createElement('div');b.className='chip';b.textContent=m.name;
    b.onclick=async()=>{
      if(b.dataset.busy)return; b.dataset.busy=1;
      b.classList.add('just');
      try{await post('/api/now/dose',{med_id:m.id,status:'EXTRA',day:nowData.day});
        toast('Logged '+m.name);setTimeout(()=>loadNow(),320);}
      catch(err){b.classList.remove('just');b.dataset.busy='';toast(err.message);}
    };
    ex.appendChild(b);
  });
  const sa=$('#nowShowAll');
  if(sa)sa.textContent=showAllMeds?'Show fewer':'Show all medicines';

  const el=$('#nowExtraList');el.innerHTML='';
  const extras=nowData.extras||[];
  $('#exSum').textContent=extras.length?(extras.length+' logged today'):'none yet';
  extras.forEach(e=>{
    const row=document.createElement('div');row.className='exrow';
    row.innerHTML='<span class="t"></span><span class="m"></span>'+
      '<button type="button" class="btn tiny u">Undo</button>';
    row.querySelector('.t').textContent=e.dtime||'';
    row.querySelector('.m').textContent=e.medicine;
    row.querySelector('.u').onclick=async()=>{
      await post('/api/now/undo/'+e.id,{});toast('Removed');loadNow();};
    el.appendChild(row);
  });
}

function buildNowStatics(){
  bindFolds();

  const t=$('#n_symType');
  SYMTYPES.forEach(v=>{const b=document.createElement('div');b.className='chip';b.textContent=v;
    b.onclick=()=>{
      const i=nSym.types.indexOf(v);
      if(i>=0)nSym.types.splice(i,1);else nSym.types.push(v);
      b.classList.toggle('sel',nSym.types.indexOf(v)>=0);
    };t.appendChild(b);});

  /* Pain by site: tap the tile to select it and open its score row,
     tap the name again to clear it. */
  const ps=$('#n_painSites');
  PAIN_SITES.forEach(site=>{
    const w=document.createElement('div');w.className='ptile';
    w.innerHTML='<button type="button" class="ph"><span class="pn"></span><span class="pv"></span></button>'+
                '<div class="chips pscore"></div>';
    w.querySelector('.pn').textContent=site;
    const pv=w.querySelector('.pv'), row=w.querySelector('.pscore');
    SEVS.forEach(v=>{const b=document.createElement('div');b.className='chip num';b.textContent=v;
      b.onclick=()=>{nPain[site]=v;pv.textContent=v+'/10';
        [...row.children].forEach(c=>c.classList.toggle('sel',c===b));};
      row.appendChild(b);});
    w.querySelector('.ph').onclick=()=>{
      if(site in nPain){delete nPain[site];w.classList.remove('open');pv.textContent='';
        [...row.children].forEach(c=>c.classList.remove('sel'));}
      else{nPain[site]='';w.classList.add('open');pv.textContent='score?';}
    };
    ps.appendChild(w);
  });

  const s=$('#n_symSev');
  SEVS.forEach(v=>{const b=document.createElement('div');b.className='chip num';b.textContent=v;
    b.onclick=()=>{nSym.sev=v;[...s.children].forEach(c=>c.classList.toggle('sel',c===b));};s.appendChild(b);});

  const br=$('#n_symBristol');
  ['1','2','3','4','5','6','7'].forEach(v=>{const b=document.createElement('div');b.className='chip num';b.textContent=v;
    b.onclick=()=>{nSym.bristol=(nSym.bristol===v?null:v);
      [...br.children].forEach(c=>c.classList.toggle('sel',c.textContent===nSym.bristol));};br.appendChild(b);});

  $('#n_bpSave').onclick=async()=>{
    const sys=$('#n_sys').value,dia=$('#n_dia').value,pulse=$('#n_pulse').value;
    if(!sys&&!dia&&!pulse){toast('Nothing to save');return;}
    try{await post('/api/vitals',{day:todayISO,vtime:nowHM(),sys:sys,dia:dia,pulse:pulse});
      $('#n_bpLast').textContent='Saved '+(sys||'-')+'/'+(dia||'-')+(pulse?', pulse '+pulse:'')+' at '+nowHM();
      $('#n_sys').value='';$('#n_dia').value='';$('#n_pulse').value='';toast('Reading saved');}
    catch(err){toast(err.message);}
  };

  /* one episode per symptom chosen, sharing time, severity and Bristol --
     two symptoms at once are two findings, not one blended row */
  $('#n_symSave').onclick=async()=>{
    const sites=Object.keys(nPain);
    if(!nSym.types.length&&!sites.length){toast('Pick a symptom');return;}
    const unscored=sites.filter(k=>!nPain[k]);
    if(unscored.length){toast('Give '+unscored[0].toLowerCase()+' a score');return;}
    const t=nowHM();
    try{
      for(const ty of nSym.types){
        await post('/api/episodes',{day:todayISO,etime:t,category:'GI',etype:ty,
          severity:nSym.sev,bristol:nSym.bristol});
      }
      for(const k of sites){
        await post('/api/episodes',{day:todayISO,etime:t,category:'GI',etype:k,
          severity:nPain[k],bristol:nSym.bristol});
      }
      const n=nSym.types.length+sites.length;
      toast(n>1?(n+' episodes saved'):'Episode saved');
      nSym={types:[],sev:null,bristol:null};nPain={};
      $$('#n_symType .chip,#n_symSev .chip,#n_symBristol .chip,#n_painSites .chip').forEach(c=>c.classList.remove('sel'));
      $$('#n_painSites .ptile').forEach(w=>{w.classList.remove('open');w.querySelector('.pv').textContent='';});
    }catch(err){toast(err.message);}
  };

  $('#nowAddMed').onclick=async()=>{
    const n=prompt('Medicine name (include strength)');if(!n)return;
    try{await post('/api/prnmeds',{name:n});toast('Added');loadNow();loadSchedMeds();}
    catch(err){toast(err.message);}
  };
  const sa=$('#nowShowAll');
  if(sa)sa.onclick=()=>{showAllMeds=!showAllMeds;loadNow();};
}

/* ---------- Schedule editor ---------- */
let schedSlot=null;
async function loadSchedMeds(){
  const meds=await jget('/api/prnmeds/full');
  const sel=$('#sc_med');if(!sel)return;
  sel.innerHTML='';
  meds.forEach(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=m.name;sel.appendChild(o);});
}
async function loadSchedule(){
  const d=await jget('/api/schedule');
  const sl=$('#sc_slot');
  if(sl&&!sl.dataset.built){sl.dataset.built=1;
    d.slots.forEach(s=>{const b=document.createElement('div');b.className='chip';b.textContent=s.label;
      b.onclick=()=>{schedSlot=s.slot;[...sl.children].forEach(c=>c.classList.toggle('sel',c===b));};
      sl.appendChild(b);});}
  const box=$('#schedList');box.innerHTML='';
  if(!d.rows.length){
    box.innerHTML='<div class="card"><p class="hint" style="margin:0">No regular medicines yet.</p></div>';
    return;
  }
  const c=document.createElement('div');c.className='card';
  c.innerHTML='<p class="q">Current regimen</p>';
  d.rows.forEach(r=>{
    const row=document.createElement('div');row.className='schrow';
    row.innerHTML='<span class="s"></span><span class="n"></span><button type="button" class="x">stop</button>';
    row.querySelector('.s').textContent=r.slot.slice(0,3);
    row.querySelector('.n').textContent=r.name+(r.dose_text?' \u00b7 '+r.dose_text:'');
    row.querySelector('.x').onclick=async()=>{
      if(!confirm('Stop '+r.name+' ('+r.slot.toLowerCase()+')? Past logs are kept.'))return;
      await post('/api/schedule/close/'+r.id,{});toast('Stopped');loadSchedule();loadNow();};
    c.appendChild(row);
  });
  box.appendChild(c);
}
function bindSchedule(){
  const add=$('#sc_add');if(!add)return;
  add.onclick=async()=>{
    if(!schedSlot){toast('Pick a slot');return;}
    try{await post('/api/schedule',{med_id:parseInt($('#sc_med').value,10),slot:schedSlot,
      dose_text:$('#sc_dose').value,with_food:$('#sc_food').value});
      toast('Added to regimen');$('#sc_dose').value='';loadSchedule();loadNow();}
    catch(err){toast(err.message);}
  };
}

/* deep links from the home-screen shortcuts */
function nowDeepLink(){
  const p=new URLSearchParams(location.search).get('open');
  if(!p)return;
  switchTab('now');
  const map={bp:'#nowBP',sym:'#nowSym',meds:'#nowSched'};
  const el=$(map[p]||'#nowSched');
  if(el)setTimeout(()=>el.scrollIntoView({behavior:'smooth',block:'start'}),120);
}

/* ---------- boot ---------- */
function initDates(){
  $('#hdrDay').textContent=new Date().toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short'});
  ['s_day','e_day','v_day','ml_day','m_day','f_day','u_day','l_day','k_day','c_day'].forEach(id=>{const el=$('#'+id);if(el)el.value=todayISO;});
  ['e_time','v_time','ml_time','m_time'].forEach(id=>{const el=$('#'+id);if(el)el.value=nowHM();});
}
(async function(){
  initDates();
  buildNowStatics();
  bindSchedule();
  await loadLib();
  await loadPRN();
  await loadRings();
  renderTestFoods();loadRegistry();
  await loadNow();
  saveBtnVisible();
  nowDeepLink();
})();
</script>
</body></html>"""

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8020)),
            debug=os.environ.get("GUTLOG_INSECURE") == "1")

#!/usr/bin/env python3
"""
GutLog v3 - single-file personal health logger. Fresh schema (no v2 migration).
Tabs: Log (day / episode / vitals) . Meals (library-backed picker, food tests) .
Meds (per-dose PRN ledger, courses, transdermal patch) . Files (vault, labs,
consults) . Review (trends, FODMAP load, dose overlay, exports).
Run:  gunicorn -w 2 -b 127.0.0.1:8020 app:app
GUTLOG_V3280_PLANS -- /plans holds and shares a dated plan document.
GUTLOG_V3290_NUTRITION -- /nutrition, and a Meals tab with a day stepper.
GUTLOG_V3300_MIRRORSTALE -- the Now tab says when the Drive mirror is stale.
GUTLOG_V3310_FOODLIB -- foods by weight with a sourced table; every time editable.
"""
import os, csv, io, json, time, sqlite3, secrets, uuid
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (Flask, request, session, redirect, url_for, g,
                   render_template_string, jsonify, Response, abort,
                   send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sys as _sso_sys  # HEALTH_SSO_V1
_sso_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health_sso  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GUTLOG_DB", os.path.join(BASE, "health3.db"))
UPLOAD_DIR = os.environ.get("GUTLOG_UPLOADS", os.path.join(BASE, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
# GUTLOG_V3280_PLANS -- plan documents sit beside the live database and
# never in the repository. They are the health record (CLAUDE.md 5d).
PLANS_DIR = os.environ.get("GUTLOG_PLANS", os.path.join(BASE, "plans_files"))
os.makedirs(PLANS_DIR, exist_ok=True)
PLAN_MAX_MB = 20
PLAN_STATUSES = ("Draft", "Active", "Closed")
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_MB = 25   # v3.11.0: report pages are saved at ~220dpi now, not ~110
# GUTLOG_V3272_HEALTHZ -- one place that states the running version.
APP_VERSION = "3.31.0"   # GUTLOG_V3310_FOODLIB GUTLOG_V3300_MIRRORSTALE GUTLOG_V3290_NUTRITION GUTLOG_V3280_PLANS GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS

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
  tags TEXT DEFAULT '', fav INTEGER DEFAULT 0, note TEXT DEFAULT '', created TEXT,
  portion_qty REAL, portion_unit TEXT DEFAULT '', basis_qty REAL DEFAULT 100,
  basis_unit TEXT DEFAULT '', b_protein REAL, b_kcal REAL, b_fibre REAL,
  weighed_dry INTEGER DEFAULT 0, portion_est INTEGER DEFAULT 0,
  source TEXT DEFAULT '', source_date TEXT DEFAULT '', source_ref TEXT DEFAULT '');
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
CREATE TABLE IF NOT EXISTS stock_order_cfg (
  med_id INTEGER PRIMARY KEY, pack_type TEXT DEFAULT '', keep_units REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, status TEXT DEFAULT 'OPEN',
  created TEXT, received_at TEXT DEFAULT '', lines TEXT DEFAULT '[]', text TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS trials (
  id INTEGER PRIMARY KEY AUTOINCREMENT, food TEXT, match TEXT DEFAULT '', amount TEXT DEFAULT '',
  freq TEXT DEFAULT '', start TEXT, days INTEGER DEFAULT 14, ended TEXT DEFAULT '',
  status TEXT DEFAULT 'active', verdict TEXT DEFAULT '', verdict_note TEXT DEFAULT '',
  note TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, name TEXT, grp TEXT DEFAULT '',
  stage TEXT DEFAULT 'new', stage_note TEXT DEFAULT '', data TEXT DEFAULT '{}', updated TEXT);
CREATE TABLE IF NOT EXISTS day_context (
  day TEXT NOT NULL, tag TEXT NOT NULL, created TEXT, PRIMARY KEY (day, tag));
CREATE TABLE IF NOT EXISTS day_context_note (day TEXT PRIMARY KEY, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS meal_meta (
  meal_id INTEGER PRIMARY KEY, card TEXT DEFAULT '', choices TEXT DEFAULT '{}',
  onion INTEGER DEFAULT 0, extra TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS stock_links (
  med_id INTEGER NOT NULL, variant TEXT NOT NULL, stock_med_id INTEGER NOT NULL,
  units REAL DEFAULT 1, PRIMARY KEY (med_id, variant));
CREATE TABLE IF NOT EXISTS med_salts (
  med_id INTEGER PRIMARY KEY, strength TEXT DEFAULT '', no_salt INTEGER DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, atime TEXT, kind TEXT NOT NULL,
  minutes REAL, intensity TEXT DEFAULT '', notes TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS rec_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, kind TEXT, title TEXT, source TEXT,
  finding TEXT, stored TEXT DEFAULT '', orig TEXT DEFAULT '', sha TEXT UNIQUE, status TEXT DEFAULT 'filed', created TEXT,
  origin TEXT DEFAULT '', checked INTEGER DEFAULT 1, file_id INTEGER);
CREATE TABLE IF NOT EXISTS rec_labs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, test TEXT, section TEXT, value TEXT, num REAL,
  unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, origin TEXT DEFAULT '', doc_id INTEGER,
  UNIQUE(day, test, lab));
CREATE TABLE IF NOT EXISTS down_days (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT UNIQUE, components TEXT DEFAULT '',
  coped TEXT DEFAULT '', note TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS rec_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pos INTEGER, test TEXT UNIQUE, why TEXT, timing TEXT,
  status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '');
-- GUTLOG_V3280_PLANS. started_on and notes are unused in v3.28.0 and are here
-- because the next version dates the regimen steps a plan asks for. plan_files
-- is a table rather than columns on plans for the same reason: revised versions
-- of a document, newest first, which is what the index is for.
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  first_considered TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Draft',
  started_on TEXT, notes TEXT, archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS plan_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL,
  stored_name TEXT NOT NULL, original_name TEXT NOT NULL,
  bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, uploaded_at TEXT);
CREATE INDEX IF NOT EXISTS ix_plan_files_plan ON plan_files(plan_id, id DESC);
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

SCHEMA_VERSION = "3.3.5"   # GUTLOG_V3310_FOODLIB GUTLOG_V330_PHASE_A GUTLOG_V332_VARIANTS GUTLOG_V333_ROWACT GUTLOG_V340_READABILITY GUTLOG_V341_PICKER GUTLOG_V342_PAINSITE GUTLOG_V350_PHASE_B GUTLOG_V360_PHASE_C GUTLOG_V370_SALTS_ACTIVITY GUTLOG_V380_RECORDS GUTLOG_V390_SCAN GUTLOG_V3100_AUTOREAD GUTLOG_V3110_SCANQ GUTLOG_V3120_PAIN GUTLOG_V3130_WATCH GUTLOG_V3140_FALLBACK GUTLOG_V3150_READ GUTLOG_V3160_DARK GUTLOG_V3170_DOWN GUTLOG_V3180_HONEST GUTLOG_V3190_ORDER GUTLOG_V3200_PIPES GUTLOG_V3210_ONEDOSE GUTLOG_V3220_MEALS GUTLOG_V3230_CONTEXT GUTLOG_V3240_RECIPES GUTLOG_V3250_PLAN GUTLOG_V3260_TRIALS GUTLOG_V3270_TIMEPICK GUTLOG_V3271_TARGETJS

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
    ("episodes", "treatments", "TEXT DEFAULT ''"),
    ("episodes", "radiates", "INTEGER DEFAULT 0"),
    ("med_schedule", "variants", "TEXT DEFAULT ''"),
    ("files", "sha", "TEXT DEFAULT ''"),
    ("files", "ocr_status", "TEXT DEFAULT ''"),
    ("files", "ocr_note", "TEXT DEFAULT ''"),
    ("files", "ocr_tries", "INTEGER DEFAULT 0"),
    ("rec_docs", "origin", "TEXT DEFAULT ''"),
    ("rec_docs", "checked", "INTEGER DEFAULT 1"),
    ("rec_docs", "file_id", "INTEGER"),
    ("rec_labs", "origin", "TEXT DEFAULT ''"),
    ("rec_labs", "doc_id", "INTEGER"),
    # GUTLOG_V3310_FOODLIB -- a numeric basis for every food. Also in SCHEMA,
    # so a new database has them without this step.
    ("library", "portion_qty", "REAL"),
    ("library", "portion_unit", "TEXT DEFAULT ''"),
    ("library", "basis_qty", "REAL DEFAULT 100"),
    ("library", "basis_unit", "TEXT DEFAULT ''"),
    ("library", "b_protein", "REAL"),
    ("library", "b_kcal", "REAL"),
    ("library", "b_fibre", "REAL"),
    ("library", "weighed_dry", "INTEGER DEFAULT 0"),
    ("library", "portion_est", "INTEGER DEFAULT 0"),
    ("library", "source", "TEXT DEFAULT ''"),
    ("library", "source_date", "TEXT DEFAULT ''"),
    ("library", "source_ref", "TEXT DEFAULT ''"),
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

    # -- GUTLOG_V3170_DOWN: a down day is a calendar day, so it gets its
    # own table rather than an episodes row. Idempotent, like the rest.
    con.execute("CREATE TABLE IF NOT EXISTS down_days ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT UNIQUE, "
                "components TEXT DEFAULT '', coped TEXT DEFAULT '', "
                "note TEXT DEFAULT '', created TEXT)")

    # -- normalise legacy free-text dose rows to prnmeds ---------------
    con.execute("UPDATE doses SET status='TAKEN' "
                "WHERE status IS NULL OR status=''")
    con.execute("UPDATE doses SET med_id=("
                "SELECT p.id FROM prnmeds p WHERE p.name=doses.medicine) "
                "WHERE med_id IS NULL")

    # GUTLOG_V3310_FOODLIB -- the library gets its weights. Library only;
    # meals keep the values recorded when they were logged.
    lib_backfill(con)

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
    lib_backfill(con)   # GUTLOG_V3310_FOODLIB
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
    session.pop(health_sso.HOLD, None)  # HEALTH_SSO_V1

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
            # HEALTH_SSO_V1 -- signed in to RxGuard or FitLog is signed in here
            if health_sso.wants_bounce(request, session):
                return redirect(health_sso.bounce_url("gutlog", health_sso.here(request)))
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
    session.clear()
    session[health_sso.HOLD] = True  # HEALTH_SSO_V1 -- Lock stays locked
    return redirect(url_for("login"))

# GUTLOG_V3272_HEALTHZ --------------------------------------------------
# Plain text, no login, no database, no session. It says the process is
# up and which build it is, and nothing else -- deliberately nothing the
# record could leak through, because it is the one route with no auth.
@app.route("/healthz")
def healthz():
    return Response("ok %s" % APP_VERSION, mimetype="text/plain")

# HEALTH_SSO_V1 ---------------------------------------------------------
@app.route("/sso/vouch")
def sso_vouch():
    ok = bool(setting("pw_hash")) and bool(session.get("ok")) and \
        session.get("ep") == auth_epoch()
    u = health_sso.vouch_url("gutlog", ok, request.args.get("to"),
                             request.args.get("next"), request.args.get("hops"))
    return redirect(u) if u else ("Not found", 404)

@app.route("/sso/in")
def sso_in():
    if not setting("pw_hash"):
        return redirect(url_for("setup"))
    ok, nxt = health_sso.accept("gutlog", request, db())
    if not ok:
        return redirect(url_for("login", sso="0"))
    stamp_session()
    return redirect(nxt)

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
        protein=prot or 0, target=_protein_target(), streak=streak,
        patch=dict(patch) if patch else None)

# ------------------------------------------------------------------ library
@app.route("/api/library")
@login_required
def api_library():
    # GUTLOG_V3310_FOODLIB -- a food added by any route (a new dish, a recipe,
    # the seeder) gets its weight filled in the same way before it is shown.
    lib_backfill(db())
    db().commit()
    uses = lib_uses()
    out = []
    for r in db().execute("SELECT * FROM library ORDER BY cat, item"):
        x = dict(r)
        x["uses"] = uses.get(x["item"], 0)
        out.append(x)
    return jsonify(out)

@app.route("/api/library", methods=["POST"])
@login_required
def api_library_add():
    d = J()
    item = (d.get("item") or "").strip()[:80]
    if not item: return jsonify(ok=False, err="Name the food."), 400
    if d.get("fodmap") not in FMAP: return jsonify(ok=False, err="Pick a FODMAP flag."), 400
    f, err = lib_apply(d, None)
    if err: return jsonify(ok=False, err=err), 400
    keys = sorted(f)
    try:
        insert("library", ["cat","item","portion","fodmap","status","fav","tags","note"] + keys,
               [(d.get("cat") or "F")[:1], item, (d.get("portion") or "")[:60], d["fodmap"],
                d.get("status") or "", 1 if d.get("fav") else 0,
                (d.get("tags") or "")[:40], note(d, "note", 200)] + [f[k] for k in keys])
    except sqlite3.IntegrityError:
        return jsonify(ok=False, err="That food already exists."), 400
    rid = db().execute("SELECT id FROM library WHERE item=?", (item,)).fetchone()["id"]
    return jsonify(ok=True, id=rid, source=f.get("source"))

@app.route("/api/library/<int:lid>", methods=["POST"])
@login_required
def api_library_edit(lid):
    d = J()
    r = db().execute("SELECT * FROM library WHERE id=?", (lid,)).fetchone()
    if not r: abort(404)
    f, err = lib_apply(d, r)
    if err: return jsonify(ok=False, err=err), 400
    fodmap = d.get("fodmap", r["fodmap"])
    if fodmap not in FMAP: fodmap = r["fodmap"]
    db().execute("""UPDATE library SET item=?,portion=?,
        fodmap=?,status=?,fav=?,tags=?,note=? WHERE id=?""",
        ((d.get("item") or r["item"]).strip()[:80], (d.get("portion", r["portion"]) or "")[:60],
         fodmap, d.get("status", r["status"]),
         (1 if d["fav"] else 0) if "fav" in d else r["fav"],
         d.get("tags", r["tags"]), d.get("note", r["note"]), lid))
    if f:
        keys = sorted(f)
        db().execute("UPDATE library SET " + ", ".join(k + "=?" for k in keys) + " WHERE id=?",
                     [f[k] for k in keys] + [lid])
    db().commit()
    x = db().execute("SELECT source FROM library WHERE id=?", (lid,)).fetchone()
    return jsonify(ok=True, source=x["source"])

# ------------------------------------------------------------------ meals
@app.route("/api/meals", methods=["POST"])
@login_required
def api_meals():
    d = J()
    items = d.get("items") or []
    if not items: return jsonify(ok=False, err="Add at least one item."), 400
    # GUTLOG_V3310_FOODLIB -- the date and time are checked as /api/retime
    # checks them, and an item sent by weight is worked out HERE from the
    # food's per-100 basis; the page never supplies a weighed item's numbers.
    day = d.get("day") or today()
    mtime = d.get("mtime") or now_hm()
    if not _valid_day(day) or not _valid_hm(mtime):
        return jsonify(ok=False, err="Pick a real date and time."), 400
    if day == today() and mtime > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400
    lib = None
    clean, p, k, f, fs = [], 0.0, 0.0, 0.0, 0.0
    for it in items[:20]:
        if it.get("g") not in (None, "", 0, "0"):
            if lib is None:
                lib = _lib_map()
            n = (it.get("n") or "").strip()[:80]
            row = lib.get(n)
            w = lib_weigh(row, it.get("g")) if row else None
            if not w:
                return jsonify(ok=False, err="No weight is set for " + (n or "that food")
                               + " -- set it in the food list, or log it by portion."), 400
            clean.append(w)
            p += w["q"] * w["p"]; k += w["q"] * w["k"]; f += w["q"] * w["f"]
            fs += w["q"] * FMAP[w["fm"]]
            continue
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
           [day, mtime, d.get("slot") or "Meal",
            json.dumps(clean), round(p,1), round(k), round(f,1), round(fs,2), note(d)])
    return jsonify(ok=True, protein=round(p,1))

@app.route("/api/meals/today/<day>")
@login_required
def api_meals_today(day):
    rows = [dict(r) for r in db().execute(
        "SELECT * FROM meals WHERE day=? ORDER BY mtime", (day,))]
    for r in rows: r["items"] = json.loads(r["items"] or "[]")
    return jsonify(rows)

# ------------------------------------------------------------------ meal cards
# GUTLOG_V3220_MEALS -- his usual day as cards on the Now tab. Each card opens
# set to what he had last time, so an unchanged meal is one tap; a variation
# is one chip. The cards are CONFIG (meals.local.json beside this file, never
# committed -- it describes his diet), the meals they write are ordinary
# `meals` rows, and which card and choices produced a row is kept beside it
# in meal_meta, so Edit can reopen the card exactly as it was logged.
MEAL_KINDS = {
    # kind: (label, protein, kcal, fibre) for ONE medium serving -- typical
    # values for the kind of dish, used only when a new dish has nothing
    # better. Every item made from these is tagged "estimated".
    "bread": ("Bread / baked", 8, 250, 2.5),
    "rice": ("Rice / grain dish", 6, 300, 2),
    "curry": ("Dal / curry", 7, 150, 4),
    "sabzi": ("Dry sabzi", 2.5, 110, 3),
    "fried": ("Fried snack", 4, 260, 2),
    "sweet": ("Sweet", 3, 200, 0.8),
    "drink": ("Drink", 3, 100, 0),
    "salad": ("Salad / raw", 2, 60, 2.5),
    "protein": ("Egg / paneer / fish dish", 14, 220, 1),
}
MEAL_SIZES = {"small": 0.75, "medium": 1.0, "large": 1.5}


def _meal_cfg():
    try:
        path = os.environ.get("GUTLOG_MEALS_FILE") or os.path.join(BASE, "meals.local.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"cards": []}


def _lib_map():
    return dict((r["item"], dict(r)) for r in db().execute("SELECT * FROM library").fetchall())


def _meal_items(pairs, lib):
    """[(library item, quantity)] -> (meal items as /api/meals stores them,
    names not in the library). Nutrition is copied at log time, like every
    other meal row, so a later library edit never rewrites the past."""
    out, missing = [], []
    for pr in pairs:
        name, q = pr[0], pr[1]
        r = lib.get(name)
        if not r:
            missing.append(name)
            continue
        # GUTLOG_V3310_FOODLIB -- an item with a weight is worked out from the
        # food's per-100 basis; one without stays a multiple of the portion.
        if len(pr) > 2 and pr[2] not in (None, "", 0, "0"):
            w = lib_weigh(r, pr[2])
            if w:
                out.append(w)
            else:
                missing.append(name + " (no weight set)")
            continue
        try:
            q = max(0.25, min(10.0, float(q)))
        except (TypeError, ValueError):
            continue
        out.append({"n": r["item"], "q": q, "p": r["protein"] or 0, "k": r["kcal"] or 0,
                    "f": r["fibre"] or 0, "fm": r["fodmap"] if r["fodmap"] in FMAP else "M"})
    return out, missing


def _card_pairs(card, choices, onion):
    """What a card's choices mean in library items."""
    pairs, counts = [], {}
    rows = card.get("rows") or []
    for i, row in enumerate(rows):
        if row.get("kind") == "count":
            try:
                counts[row.get("label")] = float(choices.get(str(i), row.get("q", 1)))
            except (TypeError, ValueError):
                counts[row.get("label")] = float(row.get("q", 1))
    for i, row in enumerate(rows):
        c = choices.get(str(i))
        kind = row.get("kind")
        if kind in ("fixed", "opt"):
            on = row.get("on", kind == "fixed") if c is None else bool(c)
            if on:
                pairs += [(n, q) for n, q in row.get("items") or []]
        elif kind == "count":
            if row.get("items"):
                pairs += [(n, q * counts.get(row.get("label"), 1)) for n, q in row["items"]]
        elif kind == "pick":
            opts = row.get("options") or []
            try:
                j = int(row.get("sel", 0) if c is None else c)
            except (TypeError, ValueError):
                j = -1
            if 0 <= j < len(opts):
                for n, q in opts[j].get("items") or []:
                    if isinstance(q, str) and q.startswith("@"):
                        q = counts.get(q[1:], 1)
                    pairs.append((n, q))
    if card.get("onion") and onion:
        pairs.append((card.get("onion_item") or "Onion (tarka base)", 1))
    return pairs


def _meal_row_json(r):
    d = dict(r)
    d["items"] = json.loads(d["items"] or "[]")
    m = db().execute("SELECT card, choices, onion, extra FROM meal_meta WHERE meal_id=?",
                     (d["id"],)).fetchone()
    d["card"] = m["card"] if m else ""
    d["choices"] = json.loads(m["choices"] or "{}") if m else {}
    d["onion"] = bool(m["onion"]) if m else False
    d["extra"] = json.loads(m["extra"] or "[]") if m else []
    return d


@app.route("/api/mealcards")
@login_required
def api_mealcards():
    day = _valid_day(request.args.get("day")) or today()
    cfg = _meal_cfg()
    lib = _lib_map()
    cards, missing = [], set()
    for c in cfg.get("cards") or []:
        last = db().execute(
            "SELECT m.choices, m.onion FROM meal_meta m JOIN meals x ON x.id=m.meal_id "
            "WHERE m.card=? ORDER BY x.day DESC, x.mtime DESC, x.id DESC LIMIT 1",
            (c.get("name"),)).fetchone()
        rows = []
        for row in c.get("rows") or []:
            row = dict(row)
            for n, _q in (row.get("items") or []):
                if n not in lib:
                    missing.add(n)
            for o in row.get("options") or []:
                for n, _q in (o.get("items") or []):
                    if n not in lib:
                        missing.add(n)
            rows.append(row)
        cards.append({"name": c.get("name"), "from": c.get("from", "00:00"),
                      "onion": bool(c.get("onion")), "rows": rows,
                      "last": json.loads(last["choices"] or "{}") if last else {},
                      "last_onion": bool(last["onion"]) if last else False})
    done = [r["slot"] for r in db().execute("SELECT slot FROM meals WHERE day=?", (day,))]
    todays = [_meal_row_json(r) for r in db().execute(
        "SELECT * FROM meals WHERE day=? ORDER BY mtime, id", (day,)).fetchall()]
    nut = dict((n, dict({"p": r["protein"] or 0, "k": r["kcal"] or 0, "f": r["fibre"] or 0,
                         "fm": r["fodmap"]}, **lib_wt(r))) for n, r in lib.items())
    return jsonify(day=day, cards=cards, done=done, today=todays, lib=nut,
                   missing=sorted(missing), kinds=dict((k, v[0]) for k, v in MEAL_KINDS.items()),
                   protein_target=_protein_target())


# GUTLOG_V3270_TIMEPICK -- one protein target everywhere: the diet plan's when
# there is one. The meal card read "62 of 57 g" beside a plan card reading
# "62 / 100 g"; the fixed 57 g is now only the fallback.
def _protein_target():
    cfg = _plan_cfg()
    try:
        v = float(((cfg or {}).get("targets") or {}).get("protein"))
    except (TypeError, ValueError):
        return PROTEIN_TARGET
    if v <= 0:
        return PROTEIN_TARGET
    return int(v) if v == int(v) else v


def _log_meal(d, replace_id=None):
    """Shared by log and edit. A card meal or an 'other' meal, plus any
    extra items. Returns (response dict, status)."""
    lib = _lib_map()
    card_name = (d.get("card") or "").strip()
    choices = d.get("choices") or {}
    onion = bool(d.get("onion"))
    pairs = []
    if card_name:
        card = next((c for c in _meal_cfg().get("cards") or [] if c.get("name") == card_name), None)
        if not card:
            return {"ok": False, "err": "That meal card no longer exists."}, 400
        pairs = _card_pairs(card, choices, onion)
    extra = [(x.get("n"), x.get("q", 1), x.get("g")) for x in (d.get("extra") or [])
             if x.get("n")][:20]
    items, missing = _meal_items(pairs + extra, lib)
    if not items:
        return {"ok": False, "err": "Nothing to log." + (
            " Not in the food list: " + ", ".join(missing) if missing else "")}, 400
    # GUTLOG_V3310_FOODLIB -- an edited meal keeps its own day unless told
    # otherwise, so its time is checked against THAT day, not today's clock.
    prior = None
    if replace_id:
        prior = db().execute("SELECT * FROM meals WHERE id=?", (replace_id,)).fetchone()
    day = d.get("day") or (prior["day"] if prior else today())
    mtime = d.get("mtime") or ((prior["mtime"] or now_hm()) if prior else now_hm())
    if not _valid_day(day) or not _valid_hm(mtime):
        return {"ok": False, "err": "Pick a real date and time."}, 400
    if day == today() and mtime > now_hm():
        return {"ok": False, "err": "That time has not come yet today."}, 400
    slot = card_name or (d.get("slot") or "Meal")[:30]
    p = sum(i["q"] * i["p"] for i in items)
    k = sum(i["q"] * i["k"] for i in items)
    f = sum(i["q"] * i["f"] for i in items)
    fs = sum(i["q"] * FMAP[i["fm"]] for i in items)
    if replace_id:
        old = db().execute("SELECT * FROM meals WHERE id=?", (replace_id,)).fetchone()
        if not old:
            return {"ok": False, "err": "That meal is gone."}, 404
        db().execute("UPDATE meals SET day=?, mtime=?, slot=?, items=?, protein=?, kcal=?, "
                     "fibre=?, fscore=? WHERE id=?",
                     (d.get("day") or old["day"], d.get("mtime") or old["mtime"], slot,
                      json.dumps(items), round(p, 1), round(k), round(f, 1), round(fs, 2),
                      replace_id))
        mid = replace_id
        # GUTLOG_V3310_FOODLIB -- a time changed from the meal card is a
        # retime like any other, and is recorded the same way.
        if (day, mtime) != (old["day"], old["mtime"] or ""):
            db().execute(
                "INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("meals", replace_id, old["day"], old["mtime"] or "", day, mtime, now_s()))
    else:
        insert("meals", ["day", "mtime", "slot", "items", "protein", "kcal", "fibre",
                         "fscore", "notes"],
               [day, mtime, slot, json.dumps(items), round(p, 1), round(k), round(f, 1),
                round(fs, 2), note(d)])
        mid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    db().execute("INSERT OR REPLACE INTO meal_meta(meal_id, card, choices, onion, extra) "
                 "VALUES(?,?,?,?,?)",
                 (mid, card_name, json.dumps(choices), 1 if onion else 0,
                  json.dumps([dict({"n": x[0], "q": x[1]}, **({"g": x[2]} if x[2] else {}))
                              for x in extra])))
    db().commit()
    return {"ok": True, "id": mid, "protein": round(p, 1), "missing": missing}, 200


@app.route("/api/mealcards/log", methods=["POST"])
@login_required
def api_mealcards_log():
    body, code = _log_meal(J())
    return jsonify(**body), code


@app.route("/api/meals/<int:mid>/replace", methods=["POST"])
@login_required
def api_meal_replace(mid):
    body, code = _log_meal(J(), replace_id=mid)
    return jsonify(**body), code


@app.route("/api/meals/<int:mid>/delete", methods=["POST"])
@login_required
def api_meal_delete(mid):
    db().execute("DELETE FROM meal_meta WHERE meal_id=?", (mid,))
    db().execute("DELETE FROM meals WHERE id=?", (mid,))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/meals/<int:mid>/again", methods=["POST"])
@login_required
def api_meal_again(mid):
    r = db().execute("SELECT * FROM meals WHERE id=?", (mid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="That meal is gone."), 404
    insert("meals", ["day", "mtime", "slot", "items", "protein", "kcal", "fibre", "fscore",
                     "notes"],
           [today(), now_hm(), r["slot"], r["items"], r["protein"], r["kcal"], r["fibre"],
            r["fscore"], r["notes"] or ""])
    nid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    m = db().execute("SELECT * FROM meal_meta WHERE meal_id=?", (mid,)).fetchone()
    if m:
        db().execute("INSERT OR REPLACE INTO meal_meta(meal_id, card, choices, onion, extra) "
                     "VALUES(?,?,?,?,?)", (nid, m["card"], m["choices"], m["onion"], m["extra"]))
        db().commit()
    return jsonify(ok=True, id=nid)


@app.route("/api/foods/search")
@login_required
def api_foods_search():
    """Recent first, then favourites, then the rest; a query narrows all three."""
    qy = (request.args.get("q") or "").strip().lower()
    recent = []
    for r in db().execute("SELECT items FROM meals ORDER BY day DESC, mtime DESC LIMIT 40"):
        for it in json.loads(r["items"] or "[]"):
            if it.get("n") and it["n"] not in recent:
                recent.append(it["n"])
    lib = _lib_map()
    names = [n for n in recent if n in lib] + sorted(
        [n for n, r in lib.items() if r["fav"] and n not in recent]) + sorted(
        [n for n, r in lib.items() if not r["fav"] and n not in recent])
    if qy:
        words = qy.split()
        names = [n for n in names
                 if all(w in (n + " " + (lib[n]["tags"] or "")).lower() for w in words)]
    return jsonify(foods=[dict({"n": n, "portion": lib[n]["portion"], "p": lib[n]["protein"],
                                "k": lib[n]["kcal"], "est": "estimated" in (lib[n]["tags"] or ""),
                                "recent": n in recent}, **lib_wt(lib[n])) for n in names[:30]])


def _guess_kind(name):
    t = name.lower()
    for kind, words in (("bread", "pizza sandwich toast bread burger pav bun roll wrap"),
                        ("fried", "samosa pakora kachori bhatura puri chips fries tikki vada"),
                        ("rice", "biryani pulao rice khichdi noodle pasta upma poha"),
                        ("sweet", "cake laddu halwa kheer barfi ice sweet pastry dessert gulab"),
                        ("drink", "juice shake lassi coffee tea soup smoothie"),
                        ("salad", "salad fruit raita"),
                        ("protein", "egg paneer fish chicken tofu omelette"),
                        ("curry", "dal curry chole rajma kadhi gravy")):
        if any(w in t for w in words.split()):
            return kind
    return "sabzi"


@app.route("/api/foods/new", methods=["POST"])
@login_required
def api_foods_new():
    """A new dish by name only. Values come from its kind and size, tagged
    'estimated' so every screen can say so; editable later in the library."""
    d = J()
    name = (d.get("name") or "").strip()[:80]
    if not name:
        return jsonify(ok=False, err="Name the dish."), 400
    kind = d.get("kind") if d.get("kind") in MEAL_KINDS else _guess_kind(name)
    lab, p, k, f = MEAL_KINDS[kind]
    ex = db().execute("SELECT item FROM library WHERE LOWER(item)=LOWER(?)", (name,)).fetchone()
    if ex:
        r = db().execute("SELECT * FROM library WHERE item=?", (ex["item"],)).fetchone()
        return jsonify(ok=True, n=r["item"], existed=True, kind=kind, p=r["protein"] or 0,
                       k=r["kcal"] or 0, f=r["fibre"] or 0, fm=r["fodmap"])
    insert("library", ["cat", "item", "portion", "protein", "kcal", "fibre", "fodmap", "status",
                       "fav", "tags", "note"],
           ["H", name, "1 medium serving", p, k, f, "M", "", 0, "estimated " + kind,
            "Estimated from a typical " + lab.lower() + " serving; FODMAP not known. Edit when known."])
    return jsonify(ok=True, n=name, existed=False, kind=kind, p=p, k=k, f=f, fm="M")


@app.route("/api/foods/guess")
@login_required
def api_foods_guess():
    return jsonify(kind=_guess_kind(request.args.get("name") or ""))


# ------------------------------------------------------------ food library
# GUTLOG_V3310_FOODLIB. A numeric basis for every food, a bundled table to
# fill it from, and the one place that decides where a food's numbers came
# from. The per-portion columns (protein, kcal, fibre) stay what every meal
# reader uses; lib_apply() works them out from the per-100 basis whenever a
# weight is known, so there is no second calculation to drift.
import re as _re

LIB_UNITS = ("g", "ml")
LIB_B = ("b_protein", "b_kcal", "b_fibre")
LIB_P = ("protein", "kcal", "fibre")
LIB_NUT_KEYS = LIB_B + LIB_P + ("portion_qty", "fdc", "portion_unit", "weighed_dry", "weight_ok")
LIB_SOURCES = ("own", "USDA", "estimated")
# Household measures -> grams or ml. ONLY used to put a first weight on an
# old portion text, and every food filled from here is marked estimated so
# the list says so and he can correct it.
LIB_HOUSEHOLD = (("katori", 150.0, "g"), ("roti", 40.0, "g"), ("chapati", 40.0, "g"),
                 ("slice", 30.0, "g"), ("egg", 50.0, "g"), ("glass", 250.0, "ml"),
                 ("tbsp", 15.0, "g"), ("tsp", 5.0, "g"))
_LIB_NUM = r"(\d+(?:\.\d+)?|\d+/\d+|½|¼|¾)"


def _lib_num(s):
    s = (s or "").strip()
    if not s:
        return 1.0
    if s in ("½", "¼", "¾"):
        return {"½": 0.5, "¼": 0.25, "¾": 0.75}[s]
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b) if float(b) else 1.0
    return float(s)


def lib_parse_portion(text):
    """(qty, unit, estimated) from an old portion text. A text that STARTS
    with a number and a unit is his own number and is exact; a "~150 g"
    inside the text or a household word gives an estimate; anything else
    gives no weight at all rather than a guess."""
    t = (text or "").strip().lower()
    m = _re.match(r"^" + _LIB_NUM + r"\s*(g|gm|gms|gram|grams|ml)\b", t)
    if m:
        return _lib_num(m.group(1)), ("ml" if m.group(2) == "ml" else "g"), 0
    m = _re.search(r"~\s*" + _LIB_NUM + r"\s*(g|ml)\b", t)
    if m:
        return _lib_num(m.group(1)), m.group(2), 1
    for word, grams, unit in LIB_HOUSEHOLD:
        m = _re.search(r"(?:^|[\s(])(?:" + _LIB_NUM + r"\s*)?" + word + r"s?\b", t)
        if m:
            return _lib_num(m.group(1)) * grams, unit, 1
    return None, "", 1


def lib_backfill(con):
    """The migration, library only, idempotent: it touches only rows whose
    source is still empty, and it never changes their per-portion values.
    Meals are not read or written here."""
    rows = con.execute("SELECT id, portion, protein, kcal, fibre FROM library "
                       "WHERE COALESCE(source,'')=''").fetchall()
    if not rows:
        return None
    n = {"exact": 0, "estimated": 0, "no_weight": 0}
    stamp = today()
    for r in rows:
        qty, unit, est = lib_parse_portion(r[1])
        if qty and qty > 0:
            b = [round(float(v) * 100.0 / qty, 2) if v is not None else None
                 for v in (r[2], r[3], r[4])]
            con.execute("UPDATE library SET portion_qty=?, portion_unit=?, basis_qty=100, "
                        "basis_unit=?, b_protein=?, b_kcal=?, b_fibre=?, portion_est=?, "
                        "source='estimated', source_date=? WHERE id=?",
                        (round(qty, 1), unit, unit, b[0], b[1], b[2], est, stamp, r[0]))
            n["estimated" if est else "exact"] += 1
        else:
            con.execute("UPDATE library SET portion_est=1, source='estimated', "
                        "source_date=? WHERE id=?", (stamp, r[0]))
            n["no_weight"] += 1
    prev = con.execute("SELECT value FROM settings WHERE key='lib_backfill_v3310'").fetchone()
    try:
        tot = json.loads(prev[0]) if prev else {}
    except ValueError:
        tot = {}
    for k, v in n.items():
        tot[k] = int(tot.get(k, 0)) + v
    tot["at"] = now_s()
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('lib_backfill_v3310',?)",
                (json.dumps(tot),))
    return n


def lib_uses(days=90):
    """How often each food was logged lately, for 'most used first'."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out = {}
    for r in db().execute("SELECT items FROM meals WHERE day>=?", (since,)):
        try:
            for it in json.loads(r["items"] or "[]"):
                if it.get("n"):
                    out[it["n"]] = out.get(it["n"], 0) + 1
        except (ValueError, AttributeError):
            pass
    return out


def lib_wt(r):
    """What a page needs to log a food by weight."""
    pq = r["portion_qty"]
    b = [r["b_protein"], r["b_kcal"], r["b_fibre"]]
    ok = bool(pq and pq > 0 and any(v is not None for v in b))
    return {"w": ok, "pq": pq, "u": r["portion_unit"] or "g", "dry": bool(r["weighed_dry"]),
            "bp": b[0] or 0, "bk": b[1] or 0, "bf": b[2] or 0}


def lib_weigh(r, g):
    """One meal item as grams (or ml) of a library food, from its per-100
    basis. None when the food has no weight set -- never a guess. q stays
    the multiple of the portion, so q*p is exactly basis*grams/100."""
    try:
        g = float(g)
    except (TypeError, ValueError):
        return None
    pq = r["portion_qty"]
    bq = r["basis_qty"] or 100.0
    b = [r["b_protein"], r["b_kcal"], r["b_fibre"]]
    if not (0 < g <= 5000) or not pq or pq <= 0 or all(v is None for v in b):
        return None
    b = [float(v or 0) for v in b]
    it = {"n": r["item"], "q": round(g / pq, 4), "g": round(g, 1),
          "u": r["portion_unit"] or "g",
          "p": b[0] * pq / bq, "k": b[1] * pq / bq, "f": b[2] * pq / bq,
          "fm": r["fodmap"] if r["fodmap"] in FMAP else "M"}
    if r["weighed_dry"]:
        it["dry"] = 1
    return it


def _lnum(v, hi):
    if v is None or str(v).strip() == "":
        return None
    x = float(v)
    if x < 0 or x > hi:
        raise ValueError("out of range")
    return x


def _lclose(a, b, tol):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def lib_apply(d, cur=None):
    """The one place that turns a save into library columns: the portion
    weight, the per-100 basis, the per-portion values every meal reads, and
    the source. Returns (fields, error). No nutritional key sent (a star
    tap) means nothing nutritional changes.

    Source: values equal to the table row he picked are 'USDA'; values that
    differ from what was stored are 'own'; a save that changes no value
    keeps the source it had, so a later lookup can never turn 'own' back."""
    if cur is not None and not any(k in d for k in LIB_NUT_KEYS):
        return {}, None
    try:
        if cur is None or "portion_qty" in d:
            pq = _lnum(d.get("portion_qty"), 5000.0)
        else:
            pq = cur["portion_qty"]
        if pq is not None and pq <= 0:
            pq = None
        unit = d.get("portion_unit")
        if unit not in LIB_UNITS:
            unit = ((cur["portion_unit"] if cur is not None else "") or "g")
        if pq:
            b = []
            for k in LIB_B:
                if k in d:
                    b.append(_lnum(d.get(k), 1000.0))
                elif cur is not None and cur[k] is not None:
                    b.append(cur[k])
                elif cur is not None and cur["portion_qty"] and cur[LIB_P[LIB_B.index(k)]] is not None:
                    b.append(round(cur[LIB_P[LIB_B.index(k)]] * 100.0 / cur["portion_qty"], 2))
                else:
                    b.append(None)
            per = [round(v * pq / 100.0, 2) if v is not None else None for v in b]
        else:
            b = [None, None, None]
            per = [(_lnum(d.get(k), 5000.0) if k in d else (cur[k] if cur is not None else None))
                   for k in LIB_P]
    except (TypeError, ValueError):
        return None, "Numbers only, and not negative."
    f = {"portion_qty": pq, "portion_unit": unit if pq else "", "basis_qty": 100.0,
         "basis_unit": unit if pq else "", "b_protein": b[0], "b_kcal": b[1], "b_fibre": b[2],
         "protein": per[0], "kcal": per[1], "fibre": per[2]}
    if "weighed_dry" in d:
        f["weighed_dry"] = 1 if d.get("weighed_dry") else 0
    if cur is None:
        f["portion_est"] = 0 if pq else 1
    elif (("portion_qty" in d and not _lclose(pq, cur["portion_qty"], 0.001))
          or d.get("weight_ok")):
        f["portion_est"] = 0 if pq else 1

    t = None
    if d.get("fdc") not in (None, ""):
        try:
            t = food_table()[1].get(int(d.get("fdc")))
        except (TypeError, ValueError):
            t = None
    stamp = today()
    if t is not None and pq and all(
            (b[i] is None) if t[2 + i] is None else _lclose(b[i], t[2 + i], 0.051)
            for i in range(3)):
        f.update(source="USDA", source_date=stamp,
                 source_ref="USDA SR Legacy #%d: %s" % (t[0], t[1]))
    elif cur is None:
        f.update(source="own", source_date=stamp, source_ref="")
    else:
        had_b = any(cur[k] is not None for k in LIB_B) and cur["portion_qty"]
        if pq and had_b:
            same = all(_lclose(b[i], cur[LIB_B[i]], 0.0051) for i in range(3))
        else:
            same = all(_lclose(per[i], cur[LIB_P[i]], 0.06) for i in range(3))
        if not same:
            f.update(source="own", source_date=stamp, source_ref="")
    return f, None


def _export_item(it):
    if it.get("g"):
        return "%s %g %s%s" % (it["n"], it["g"], it.get("u") or "g", " dry" if it.get("dry") else "")
    return "%s x%g" % (it["n"], it["q"])


# ------------------------------------------------------------ the table
FOOD_TABLE_PATH = (os.environ.get("GUTLOG_FOOD_TABLE")
                   or os.path.join(BASE, "food_table_usda.json"))
_FOOD_TABLE = {}
# Indian names -> the words the table uses. Phrases first ("moong dal" is
# mung, not lentils), then single words. Unknown words pass through as they
# are, so an English name finds itself.
FOOD_PHRASES = (("moong dal", "mung"), ("mung dal", "mung"), ("chana dal", "chickpeas"),
                ("urad dal", "mungo"), ("toor dal", "pigeonpeas"), ("arhar dal", "pigeonpeas"),
                ("masoor dal", "lentils"), ("kabuli chana", "chickpeas"),
                ("whole wheat", "wheat whole"))
FOOD_WORDS = {"dal": "lentils", "daal": "lentils", "masoor": "lentils", "moong": "mung",
              "urad": "mungo", "toor": "pigeonpeas", "arhar": "pigeonpeas",
              "chana": "chickpeas", "chole": "chickpeas", "rajma": "kidney beans",
              "atta": "wheat flour whole", "jowar": "sorghum", "bajra": "millet",
              "dahi": "yogurt", "curd": "yogurt", "chawal": "rice", "anda": "egg",
              "doodh": "milk", "badam": "almonds", "kaju": "cashew", "palak": "spinach",
              "aloo": "potatoes", "gobhi": "cauliflower", "bhindi": "okra",
              "tamatar": "tomatoes", "pyaz": "onions", "kheera": "cucumber",
              "amrood": "guavas", "kela": "bananas", "papita": "papaya", "seb": "apples"}
FOOD_DROP = frozenset("a an the of and with in on katori bowl plate glass cup homemade home "
                      "made plain fresh my small medium large".split())
FOOD_NOISE = ("babyfood", "candies", "restaurant", "fast foods", "snacks", "cereals ready-to-eat",
              "infant formula", "puddings", "pastry", "beverages", "soup", "sauce",
              "salad dressing", "frozen novelties", "cookies", "crackers", "formulated bar",
              "breakfast bars", "potatoes, mashed", "school lunch", "meatless", "fruit salad")


def _fstem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def food_table():
    """(document, rows by fdc id), read once per worker. No file means no
    lookups -- the editor then says so and the values stay his to type."""
    if "doc" not in _FOOD_TABLE:
        try:
            with open(FOOD_TABLE_PATH, encoding="utf-8") as fh:
                doc = json.load(fh)
            rows = doc.get("foods") or []
            _FOOD_TABLE["byid"] = dict((int(r[0]), r) for r in rows)
            _FOOD_TABLE["words"] = [frozenset(_fstem(w) for w in _re.findall(r"[a-z]+", r[1].lower()))
                                    for r in rows]
            _FOOD_TABLE["doc"] = doc
        except (OSError, ValueError, TypeError, IndexError):
            _FOOD_TABLE.update(doc=None, byid={}, words=[])
    return _FOOD_TABLE["doc"], _FOOD_TABLE["byid"]


def food_lookup(q, dry=False, limit=8):
    """Deterministic search of the bundled table: every query word must be
    in the name for a full match; brand, restaurant and snack rows sort
    last; a food whose name STARTS with the word sorts first; for a food
    weighed dry the raw row comes first."""
    doc, _ = food_table()
    if not doc:
        return []
    s = " " + " ".join(_re.findall(r"[a-z]+", (q or "").lower())) + " "
    for a, b in FOOD_PHRASES:
        s = s.replace(" " + a + " ", " " + b + " ")
    toks = []
    for w in s.split():
        if w in FOOD_DROP:
            continue
        for x in FOOD_WORDS.get(w, w).split():
            x = _fstem(x)
            if x not in toks:
                toks.append(x)
    if not toks:
        return []
    found = []
    for r, ws in zip(doc["foods"], _FOOD_TABLE["words"]):
        m = sum(1 for t in toks if t in ws)
        if not m:
            continue
        low = r[1].lower()
        noise = 1 if (low.startswith(FOOD_NOISE)
                      or _re.search(r"\b(?!USDA\b)[A-Z]{3,}\b", r[1])) else 0
        first = _re.findall(r"[a-z]+", low)
        primary = 0 if (first and _fstem(first[0]) in toks) else 1
        rawp = (0 if "raw" in ws else 1) if dry else 0
        found.append(((-m, noise, primary, rawp, len(low)), r, m == len(toks)))
    found.sort(key=lambda x: x[0])
    return [{"fdc": r[0], "desc": r[1], "protein": r[2], "kcal": r[3], "fibre": r[4],
             "full": full} for _, r, full in found[:limit]]


@app.route("/api/foodtable")
@login_required
def api_foodtable():
    doc, _ = food_table()
    q = (request.args.get("q") or "").strip()[:80]
    dry = request.args.get("dry") in ("1", "true")
    if not doc:
        return jsonify(available=False, matches=[],
                       text="No food table on this server; type the values.")
    return jsonify(available=True, name=doc.get("name"), licence=doc.get("licence"),
                   basis=doc.get("basis"), matches=food_lookup(q, dry) if q else [])


# ------------------------------------------------------------------ recipes
# GUTLOG_V3240_RECIPES -- his recipe cards, browsable in the Meals tab: the
# ingredients, method, per-serving estimate, plant points, gut flags and the
# onion-free version beside the original; a stage he sets (not tried, on
# trial, in rotation, paused, avoid); one tap to log a serving; a clean copy
# to send the cook. The cards live in the database (table recipes), loaded
# by seed_recipes.py from his gitignored card file -- nothing here names a
# dish. A logged serving is an ordinary meal whose item is the recipe, so
# its ingredients stay one join away for the food-symptom comparison.
RECIPE_STAGES = [("new", "Not tried"), ("trial", "On trial"), ("rotation", "In rotation"),
                 ("paused", "Paused"), ("avoid", "Avoid")]
RECIPE_STAGE_KEYS = dict(RECIPE_STAGES)
RECIPE_GROUPS = {"A": "Fits now", "B": "Has an onion-free version", "C": "Occasional"}


def _recipe_row(r, full=False):
    d = json.loads(r["data"] or "{}")
    ps = d.get("per_serving") or {}
    fl = d.get("flags") or {}
    out = {"slug": r["slug"], "name": r["name"], "group": r["grp"],
           "group_label": RECIPE_GROUPS.get(r["grp"], r["grp"]), "stage": r["stage"],
           "stage_label": RECIPE_STAGE_KEYS.get(r["stage"], r["stage"]),
           "stage_note": r["stage_note"] or "", "serving": d.get("serving") or "",
           "kcal": ps.get("kcal"), "protein": ps.get("protein"), "fibre": ps.get("fibre"),
           "fat": ps.get("fat"), "plant_points": d.get("plant_points"),
           "onion": bool(fl.get("onion")), "garlic": bool(fl.get("garlic")),
           "high_fodmap": fl.get("high_fodmap") or [], "has_onion_free": bool(d.get("onion_free"))}
    if full:
        out.update(serves=d.get("serves"), ing=d.get("ing") or [], method=d.get("method") or [],
                   notes=d.get("notes") or [], onion_free=d.get("onion_free") or [],
                   plants=d.get("plants") or [], source=d.get("source") or "",
                   yield_est=bool(d.get("yield_est")))
    return out


@app.route("/api/recipes")
@login_required
def api_recipes():
    rows = [_recipe_row(r) for r in db().execute(
        "SELECT * FROM recipes ORDER BY grp, name").fetchall()]
    return jsonify(recipes=rows, stages=[{"key": k, "label": l} for k, l in RECIPE_STAGES],
                   groups=[{"key": k, "label": v} for k, v in sorted(RECIPE_GROUPS.items())])


@app.route("/api/recipes/<slug>")
@login_required
def api_recipe(slug):
    r = db().execute("SELECT * FROM recipes WHERE slug=?", (slug,)).fetchone()
    if not r:
        return jsonify(ok=False, err="No such recipe."), 404
    out = _recipe_row(r, full=True)
    out["logged"] = [dict(x) for x in db().execute(
        "SELECT day, mtime FROM meals WHERE items LIKE ? ORDER BY day DESC, mtime DESC LIMIT 5",
        ('%"n": ' + json.dumps(r["name"]) + '%',)).fetchall()]
    return jsonify(ok=True, recipe=out)


@app.route("/api/recipes/<slug>/stage", methods=["POST"])
@login_required
def api_recipe_stage(slug):
    d = J()
    st = d.get("stage")
    if st not in RECIPE_STAGE_KEYS:
        return jsonify(ok=False, err="Unknown stage."), 400
    cur = db().execute("SELECT stage FROM recipes WHERE slug=?", (slug,)).fetchone()
    if not cur:
        return jsonify(ok=False, err="No such recipe."), 404
    db().execute("UPDATE recipes SET stage=?, stage_note=?, updated=? WHERE slug=?",
                 (st, note(d, "note", 200), now_s(), slug))
    db().commit()
    return jsonify(ok=True, stage=st)


@app.route("/api/recipes/<slug>/log", methods=["POST"])
@login_required
def api_recipe_log(slug):
    """One serving (or ½, 2) of a recipe as a meal, now. The library item
    carries its per-serving estimate; it is created from the card if absent."""
    r = db().execute("SELECT * FROM recipes WHERE slug=?", (slug,)).fetchone()
    if not r:
        return jsonify(ok=False, err="No such recipe."), 404
    d = J()
    try:
        q = float(d.get("q") or 1)
    except (TypeError, ValueError):
        q = 1.0
    if not db().execute("SELECT 1 FROM library WHERE item=?", (r["name"],)).fetchone():
        x = _recipe_row(r)
        fm = "H" if (x["onion"] or x["garlic"]) else ("M-H" if x["high_fodmap"] else "L-M")
        insert("library", ["cat", "item", "portion", "protein", "kcal", "fibre", "fodmap", "status",
                           "fav", "tags", "note"],
               ["H", r["name"][:80], (x["serving"] or "1 serving")[:60], x["protein"] or 0,
                x["kcal"] or 0, x["fibre"] or 0, fm, "", 0, "recipe estimated",
                "From your recipe card, per serving; values estimated."])
    body, code = _log_meal({"card": "", "slot": (d.get("slot") or "Meal")[:30],
                            "day": d.get("day") or today(), "mtime": d.get("mtime") or now_hm(),
                            "extra": [{"n": r["name"], "q": q}]})
    return jsonify(**body), code


# ------------------------------------------------------------------ diet plan
# GUTLOG_V3250_PLAN -- the diet plan, checking itself. Targets, rotation rules
# and a food map (plants, calcium, tags) come from diet_plan.local.json beside
# this file (his diet -- never committed). Everything is computed at read
# time from the meals he logged: today's totals against the targets, this
# week's plant count, each rotation rule's standing, and a few plain
# suggestions for the next meal. Advisory only: nothing is blocked, nothing
# is written. No file = feature off.
def _plan_cfg():
    try:
        path = os.environ.get("GUTLOG_PLAN_FILE") or os.path.join(BASE, "diet_plan.local.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _plan_food(name, cfg, rec):
    f = (cfg.get("foods") or {}).get(name) or {}
    plants = f.get("plants")
    if plants is None:
        plants = rec.get(name, [])
    return {"plants": [p.lower() for p in plants], "ca": f.get("ca"), "tags": f.get("tags") or []}


def _plan_days(start, end):
    """{day: [meal rows with items]} for start..end inclusive."""
    out = {}
    for r in db().execute("SELECT day, mtime, slot, items, protein, kcal, fibre FROM meals "
                          "WHERE day>=? AND day<=? ORDER BY day, mtime", (start, end)).fetchall():
        d = dict(r)
        d["items"] = json.loads(d["items"] or "[]")
        out.setdefault(d["day"], []).append(d)
    return out


def _tags_of(meals, cfg, rec):
    s = set()
    for m in meals:
        for it in m["items"]:
            if (it.get("q") or 0) > 0:
                s.update(_plan_food(it.get("n") or "", cfg, rec)["tags"])
    return s


def _has(tags, prefix):
    return sorted(t for t in tags if t == prefix or t.startswith(prefix))


def _short(tag):
    return tag.split(":", 1)[1] if ":" in tag else tag


def plan_view(day):
    cfg = _plan_cfg()
    if cfg is None:
        return {"on": False}
    rec = {}
    try:
        for r in db().execute("SELECT name, data FROM recipes").fetchall():
            rec[r["name"]] = (json.loads(r["data"] or "{}").get("plants") or [])
    except sqlite3.OperationalError:
        rec = {}
    tg = cfg.get("targets") or {}
    d0 = date.fromisoformat(day)
    monday = d0 - timedelta(days=d0.weekday())
    yday = (d0 - timedelta(days=1)).isoformat()
    days = _plan_days(min(monday, d0 - timedelta(days=1)).isoformat(), day)
    todays = days.get(day, [])

    # today's totals
    ca, ca_missing, sweets = 0.0, [], 0
    mains = dict((s, 0.0) for s in cfg.get("main_meals") or [])
    for m in todays:
        if m["slot"] in mains:
            mains[m["slot"]] += m["protein"] or 0
        has_sweet = False
        for it in m["items"]:
            info = _plan_food(it.get("n") or "", cfg, rec)
            if info["ca"] is None:
                if it.get("n") not in ca_missing:
                    ca_missing.append(it.get("n"))
            else:
                ca += float(info["ca"]) * float(it.get("q") or 0)
            if _has(info["tags"], "sweet"):
                has_sweet = True
        sweets += 1 if has_sweet else 0
    today = {"kcal": round(sum(m["kcal"] or 0 for m in todays)),
             "protein": round(sum(m["protein"] or 0 for m in todays), 1),
             "fibre": round(sum(m["fibre"] or 0 for m in todays), 1),
             "calcium": round(ca), "calcium_missing": ca_missing, "sweets": sweets,
             "mains": [{"slot": k, "protein": round(v, 1), "logged": any(m["slot"] == k for m in todays)}
                       for k, v in mains.items()], "meals": len(todays)}

    # this week's plants
    quarter = set(p.lower() for p in cfg.get("quarter") or [])
    plants = {}
    for dd, ms in days.items():
        if dd < monday.isoformat():
            continue
        for m in ms:
            for it in m["items"]:
                for p in _plan_food(it.get("n") or "", cfg, rec)["plants"]:
                    plants[p] = 0.25 if p in quarter else 1.0
    points = sum(plants.values())
    easy = [p for p in cfg.get("easy_adds") or [] if p.lower() not in plants]

    # rules
    week_days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    left = 6 - d0.weekday()
    tags_by_day = dict((dd, _tags_of(days.get(dd, []), cfg, rec)) for dd in week_days + [yday])
    rules, tips = [], []
    # a week he only started logging late is not "short" -- too little record to say
    logged_days = sum(1 for dd in week_days if days.get(dd))
    for r in cfg.get("rules") or []:
        t, pre, lab = r.get("type"), r.get("tag", ""), r.get("label", "")
        row = {"id": r.get("id"), "label": lab, "status": "ok", "detail": ""}
        if t == "no_repeat_days":
            both = sorted(set(_has(tags_by_day[yday], pre)) & set(_has(tags_by_day[day], pre)))
            prev = _has(tags_by_day[yday], pre)
            if both:
                row.update(status="over", detail="%s yesterday and today" % ", ".join(_short(x) for x in both))
            elif prev:
                row["detail"] = "yesterday: " + ", ".join(_short(x) for x in prev)
                if not _has(tags_by_day[day], pre):
                    tips.append("%s: not %s again today" % (lab.replace("Same ", "").replace(" two days running", "").capitalize(),
                                                          " or ".join(_short(x) for x in prev)))
        elif t == "max_each":
            cnt = {}
            for dd in week_days:
                for x in _has(tags_by_day.get(dd, set()), pre):
                    cnt[x] = cnt.get(x, 0) + 1
            over = sorted(x for x, n in cnt.items() if n > r.get("max", 2))
            full = sorted(x for x, n in cnt.items() if n == r.get("max", 2))
            if over:
                row.update(status="over", detail=", ".join("%s %d times" % (_short(x), cnt[x]) for x in over))
            elif full:
                row.update(status="full", detail="%s: %d this week, no more" % (", ".join(_short(x) for x in full), r.get("max", 2)))
        elif t in ("min_days", "max_days"):
            n = sum(1 for dd in week_days if _has(tags_by_day.get(dd, set()), pre))
            lo, hi = r.get("min"), r.get("max")
            row["detail"] = ("%d of %d this week" % (n, lo) if lo else "%d this week" % n) + (" (max %d)" % hi if hi and not lo else "")
            if hi is not None and n > hi:
                row["status"] = "over"
            elif hi is not None and n == hi and lo is None:
                row["status"] = "full"
                tips.append("%s: %d this week, none more" % (lab, n))
            elif lo is not None and n < lo:
                today_has = bool(_has(tags_by_day[day], pre))
                need = lo - n
                avail = left + (0 if today_has else 1)
                row["status"] = "due" if need <= avail else "short"
                row["detail"] += " · %d day%s left" % (avail, "" if avail == 1 else "s")
                if need > avail and logged_days >= 3:
                    tips.append("%s: %d of %d this week — short; start early next week" % (lab, n, lo))
                elif need == avail and not today_has:
                    tips.append("%s: %d of %d this week — have it today" % (lab, n, lo))
        elif t == "max_per_day":
            n = sweets if pre == "sweet" else 0
            if n > r.get("max", 1):
                row.update(status="over", detail="%d today" % n)
            elif n == r.get("max", 1):
                row.update(status="full", detail="done for today")
                tips.append("Sweet: done for today")
        rules.append(row)
    if points < (tg.get("plants_week") or 25) and easy:
        tips.append("Plants: %g of %d this week — easy adds: %s" % (points, tg.get("plants_week") or 25, ", ".join(easy[:4])))
    return {"on": True, "day": day, "targets": tg, "today": today,
            "week": {"start": monday.isoformat(), "plants": sorted(plants), "points": points,
                     "target": tg.get("plants_week") or 25, "easy": easy},
            "rules": rules, "tips": tips[:5]}


@app.route("/api/plan")
@login_required
def api_plan():
    day = _valid_day(request.args.get("day")) or today()
    return jsonify(plan_view(day))


# ------------------------------------------------------------------ food trials
# GUTLOG_V3260_TRIALS -- a food trial is a PERIOD, not a one-day entry: a food,
# how much, how often, a start and a planned length. Meals eaten in that
# period that contain the food are linked to it by name automatically -- no
# separate daily test entry. The comparison is deterministic and labelled:
# the trial days against the 14 days before it, days with a Day-context mark
# and days with nothing logged at all set aside, and a signal word only once there are 8 counted days on each
# side. He gives the verdict; the verdict updates the food map (library
# status) and, when the food is one of his recipes, the recipe's stage.
TRIAL_BASELINE_DAYS = 14
TRIAL_MIN_DAYS = 8
TRIAL_VERDICTS = {"tolerated": ("Tolerated", "cleared", "rotation"),
                  "not_tolerated": ("Not tolerated", "trigger", "avoid"),
                  "unsure": ("Not sure", None, "paused")}


def _trial_terms(t):
    return [x.strip().lower() for x in (t["match"] or "").split("|") if x.strip()]


def _trial_hits(items, terms):
    return sorted(set(it.get("n") or "" for it in items
                      if (it.get("q") or 0) > 0 and any(w in (it.get("n") or "").lower() for w in terms)))


def _day_outcome(day):
    gi = db().execute("SELECT MAX(COALESCE(severity,0)) AS m, COUNT(*) AS n FROM episodes "
                      "WHERE day=? AND category='GI'", (day,)).fetchone()
    down = db().execute("SELECT 1 FROM down_days WHERE day=?", (day,)).fetchone()
    dd = db().execute("SELECT syms FROM days WHERE day=?", (day,)).fetchone()
    syms = bool(dd and (dd["syms"] or "").strip())
    # a day counts only if the app was in use that day -- an empty day is not a good day
    used = bool(gi["n"]) or bool(down) or bool(dd) or bool(
        db().execute("SELECT 1 FROM doses WHERE day=? LIMIT 1", (day,)).fetchone()) or bool(
        db().execute("SELECT 1 FROM meals WHERE day=? LIMIT 1", (day,)).fetchone())
    return {"symptom": bool(gi["n"]) or bool(down) or syms, "pain": int(gi["m"] or 0), "used": used}


def _signal(a, b):
    """a, b: (counted days, symptom days). Words, never a number dressed as certainty."""
    if a[0] < TRIAL_MIN_DAYS or b[0] < TRIAL_MIN_DAYS:
        return "not enough days yet"
    d = 100.0 * a[1] / a[0] - 100.0 * b[1] / b[0]
    if d >= 30:
        return "likely worse"
    if d >= 15:
        return "possibly worse"
    if d <= -15:
        return "possibly better"
    return "no signal"


def trial_view(t, on_day=None):
    on_day = on_day or today()
    terms = _trial_terms(t)
    start = date.fromisoformat(t["start"])
    planned_end = start + timedelta(days=int(t["days"] or 14) - 1)
    stop = min(planned_end, date.fromisoformat(t["ended"] or on_day), date.fromisoformat(on_day))
    base_from = start - timedelta(days=TRIAL_BASELINE_DAYS)
    meals = {}
    for r in db().execute("SELECT day, items FROM meals WHERE day>=? AND day<=?",
                          (base_from.isoformat(), stop.isoformat())).fetchall():
        meals.setdefault(r["day"], []).extend(json.loads(r["items"] or "[]"))
    ctx = set(r["day"] for r in db().execute(
        "SELECT DISTINCT day FROM day_context WHERE day>=? AND day<=?",
        (base_from.isoformat(), stop.isoformat())).fetchall())
    days, styles = [], {}
    d = base_from
    while d <= stop:
        k = d.isoformat()
        hits = _trial_hits(meals.get(k, []), terms)
        for h in hits:
            if k >= t["start"]:
                styles[h] = styles.get(h, 0) + 1
        o = _day_outcome(k)
        days.append({"day": k, "phase": "trial" if k >= t["start"] else "before",
                     "ate": bool(hits), "logged": o["used"], "context": k in ctx,
                     "symptom": o["symptom"], "pain": o["pain"]})
        d += timedelta(days=1)
    # "exposed" = eaten that day or the day before (the 48 h the plan names)
    for i, x in enumerate(days):
        x["exposed"] = x["ate"] or (i > 0 and days[i - 1]["ate"])

    def side(rows):
        rows = [x for x in rows if not x["context"] and x["logged"]]
        n = len(rows)
        s = sum(1 for x in rows if x["symptom"])
        pain = round(sum(x["pain"] for x in rows) / float(n), 1) if n else None
        return {"days": n, "symptom_days": s, "pct": round(100.0 * s / n) if n else None,
                "avg_gi_pain": pain}
    tr = side([x for x in days if x["phase"] == "trial"])
    bf = side([x for x in days if x["phase"] == "before"])
    ex = side([x for x in days if x["exposed"]])
    nx = side([x for x in days if not x["exposed"] and x["logged"]])
    trial_days = [x for x in days if x["phase"] == "trial"]
    return {"id": t["id"], "food": t["food"], "match": terms, "amount": t["amount"] or "",
            "freq": t["freq"] or "", "start": t["start"], "planned_days": int(t["days"] or 14),
            "planned_end": planned_end.isoformat(), "status": t["status"], "ended": t["ended"] or "",
            "verdict": t["verdict"] or "", "verdict_note": t["verdict_note"] or "", "note": t["note"] or "",
            "day_no": (min(stop, planned_end) - start).days + 1 if stop >= start else 0,
            "ate_days": sum(1 for x in trial_days if x["ate"]),
            "set_aside": sum(1 for x in trial_days if x["context"]),
            "styles": sorted(styles.items(), key=lambda kv: -kv[1]),
            "trial": tr, "before": bf, "signal": _signal((tr["days"], tr["symptom_days"]),
                                                         (bf["days"], bf["symptom_days"])),
            "exposed": ex, "not_exposed": nx,
            "exposure_signal": _signal((ex["days"], ex["symptom_days"]),
                                       (nx["days"], nx["symptom_days"])),
            "min_days": TRIAL_MIN_DAYS, "baseline_days": TRIAL_BASELINE_DAYS}


@app.route("/api/trials")
@login_required
def api_trials():
    rows = db().execute("SELECT * FROM trials ORDER BY status='active' DESC, start DESC").fetchall()
    return jsonify(trials=[trial_view(r) for r in rows],
                   verdicts=[{"key": k, "label": v[0]} for k, v in TRIAL_VERDICTS.items()])


@app.route("/api/trials", methods=["POST"])
@login_required
def api_trial_start():
    d = J()
    food = (d.get("food") or "").strip()[:80]
    if not food:
        return jsonify(ok=False, err="Pick the food to trial."), 400
    start = _valid_day(d.get("start") or today())
    if not start:
        return jsonify(ok=False, err="Pick a real start date, not in the future."), 400
    try:
        n = int(d.get("days") or 14)
    except (TypeError, ValueError):
        n = 14
    if not 3 <= n <= 90:
        return jsonify(ok=False, err="A trial runs 3 to 90 days."), 400
    if db().execute("SELECT 1 FROM trials WHERE status='active' AND LOWER(food)=LOWER(?)",
                    (food,)).fetchone():
        return jsonify(ok=False, err="A trial of that food is already running."), 400
    match = (d.get("match") or food).strip().lower()[:200]
    insert("trials", ["food", "match", "amount", "freq", "start", "days", "ended", "status",
                      "verdict", "verdict_note", "note"],
           [food, match, (d.get("amount") or "")[:60], (d.get("freq") or "")[:40], start, n, "",
            "active", "", "", note(d, "note", 300)])
    tid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    try:
        db().execute("UPDATE recipes SET stage='trial', updated=? WHERE name=?", (now_s(), food))
        db().commit()
    except sqlite3.OperationalError:
        pass
    return jsonify(ok=True, id=tid)


@app.route("/api/trials/<int:tid>/end", methods=["POST"])
@login_required
def api_trial_end(tid):
    d = J()
    t = db().execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone()
    if not t:
        return jsonify(ok=False, err="No such trial."), 404
    v = d.get("verdict")
    if v not in TRIAL_VERDICTS:
        return jsonify(ok=False, err="Pick a verdict."), 400
    label, lib_status, stage = TRIAL_VERDICTS[v]
    db().execute("UPDATE trials SET status='ended', ended=?, verdict=?, verdict_note=? WHERE id=?",
                 (t["ended"] or today(), label, note(d, "note", 300), tid))
    changed = []
    if lib_status:
        for r in db().execute("SELECT id, item FROM library").fetchall():
            if any(w in r["item"].lower() for w in _trial_terms(t)):
                db().execute("UPDATE library SET status=? WHERE id=?", (lib_status, r["id"]))
                changed.append(r["item"])
    try:
        db().execute("UPDATE recipes SET stage=?, updated=? WHERE name=?", (stage, now_s(), t["food"]))
    except sqlite3.OperationalError:
        pass
    db().commit()
    return jsonify(ok=True, verdict=label, library=changed)


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
    rows = db().execute("SELECT id, medicine, dtime FROM doses WHERE day=? ORDER BY dtime",
                        (day,)).fetchall()
    out, ids = {}, {}
    for r in rows:
        out.setdefault(r["medicine"], []).append(r["dtime"] or "")
        ids.setdefault(r["medicine"], []).append(r["id"])   # GUTLOG_V3310_FOODLIB
    return jsonify([{"medicine": m, "times": ts, "ids": ids[m], "day": day}
                    for m, ts in out.items()])

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
    tr = d.get("treatments")
    tr = "|".join(tr) if isinstance(tr, list) else (tr or "")
    insert("episodes", ["day","etime","category","etype","side","severity","duration","notes","bristol","treatments","radiates"],
           [d.get("day") or today(), d.get("etime") or now_hm(),
            d.get("category"), d["etype"], d.get("side"), d.get("severity"),
            d.get("duration"), note(d), (d.get("bristol") or "")[:4],
            tr[:200], 1 if d.get("radiates") else 0])
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
    _after_upload(stored)
    _spawn_reader()
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
                     "labs","courses","files","patches","library","activities"):
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
_TIME_COL = {"doses": "dtime", "episodes": "etime", "vitals": "vtime", "meals": "mtime", "activities": "atime"}


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

    def add(tbl, rid, t, kind, title, sub, **extra):
        e = {"tbl": tbl, "id": rid, "time": t or "", "kind": kind,
             "title": title or "", "sub": sub,
             "edited": (tbl, rid) in edited}
        e.update(extra)
        out.append(e)

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
            "SELECT id, etime, etype, side, severity, bristol, category, duration, "
            "COALESCE(treatments,'') AS treatments, COALESCE(radiates,0) AS radiates "
            "FROM episodes WHERE day=?", (day,)).fetchall():
        parts = []
        if r["severity"] not in (None, ""):
            parts.append(str(r["severity"]) + "/10")
        if r["bristol"]:
            parts.append("Bristol " + str(r["bristol"]))
        if r["side"]:
            parts.append(str(r["side"]))
        pain = (r["category"] or "") == "pain"
        title = r["etype"]
        if pain:
            meta = PAIN_SITE_MAP.get(r["etype"])
            title = meta[1] if meta else r["etype"]
            if r["radiates"]:
                parts.append("below the knee")
            if r["treatments"]:
                parts.append(r["treatments"].replace("|", ", "))
            if r["duration"]:
                parts.append("eased after " + str(r["duration"]))
        add("episodes", r["id"], r["etime"], "Pain" if pain else "Symptom",
            title, " · ".join(parts), pain=pain, eased=bool(r["duration"]))
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
    dd = _down_row(day)
    if dd:
        n, run = _down_run_of(day)
        sub = ", ".join(dd["components"]) or "no components noted"
        if run and run["length"] > 1:
            sub = "day " + str(n) + " of " + str(run["length"]) + " \u00b7 " + sub
        add("down_days", 0, "", "Down day", "Down day", sub, down=True)
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

    for r in db().execute("SELECT id, atime, kind, minutes, intensity FROM activities WHERE day=?",
                          (day,)).fetchall():
        mins = int(round(r["minutes"] or 0))
        if r["kind"] in LOAD_KINDS:
            add("activities", r["id"], r["atime"], "Load",
                ACT_KINDS.get(r["kind"], r["kind"]) + " " + str(round(mins / 60.0, 1)) + " h",
                "load, not exercise", load=True)
        else:
            add("activities", r["id"], r["atime"], "Activity",
                ACT_KINDS.get(r["kind"], r["kind"]) + " " + str(mins) + " min",
                (r["intensity"] or "").lower())
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
        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False, "labels": []})
        if (l["variants"] or "").strip():
            s["variants"] = True
            s["labels"] += [v.strip() for v in l["variants"].split("|") if v.strip()]
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

    # GUTLOG_V3200_PIPES -- a dose logged on a medicine whose strengths vary
    # comes out of the stock of the product each strength is linked to:
    # "145 + 72" takes one from each linked pack. The medicine itself stays
    # untracked; its products are counted, alerted and ordered.
    links = _stock_links(con)
    all_names = dict((r["id"], r["name"]) for r in con.execute(
        "SELECT id, name FROM prnmeds").fetchall())
    linked_from = {}
    for vmid, lk in links.items():
        for t in lk.values():
            linked_from[t[0]] = all_names.get(vmid, "")
        for d in list(by_med.get(vmid, [])):
            for part in (d["dose_text"] or "").split("+"):
                t = lk.get(part.strip().lower())
                if not t:
                    continue
                by_med.setdefault(t[0], []).append({
                    "med_id": t[0], "medicine": "", "day": d["day"], "dtime": d["dtime"],
                    "status": d["status"], "sched_id": None, "dose_text": "%g" % t[1]})

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
        r["linked_from"] = linked_from.get(mid, "")
        if s and s["variants"]:
            lk = links.get(mid, {})
            r["variants"] = list(dict((v.lower(), v) for v in s["labels"]).values())
            r["links"] = [{"variant": v, "stock_med_id": t[0], "units": t[1],
                           "name": all_names.get(t[0], "")} for v, t in sorted(lk.items())]
            r["why"] = ("strengths vary - counted on " + ", ".join(
                sorted(set(x["name"] for x in r["links"])))) if lk else \
                "strengths vary - not tracked until its strengths are linked"
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
    # GUTLOG_V3190_ORDER -- the pack details ride along for the Pack form
    cfg = _order_cfg()
    for r in rows:
        c = cfg.get(r["med_id"])
        r["pack_type"] = (c["pack_type"] if c else "") or ""
        r["keep_units"] = float(c["keep_units"] or 0) if c else 0.0
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


# ---- GUTLOG_V3190_ORDER -- the monthly medicine order.
# He orders in the last week of each month for the month after, and wants a
# buffer of ten days on top of the month, so stock is TOPPED UP to a target of
# ORDER_DAYS days (default 40) -- never a flat 40 days bought every month,
# which would pile up. The target is judged on the stock expected on the 1st
# of the ordering month, not on today's count: a week of doses still comes
# out before the new month starts, and what already sits in a filled pillbox
# has left stock but has not been swallowed yet.
#
#   target  scheduled medicine     -> schedule units a day x ORDER_DAYS
#           keep-on-hand set       -> that many units (SOS medicines)
#           otherwise              -> 14-day average use x ORDER_DAYS
#   order   target - expected-on-the-1st, rounded UP to whole packs
#
# Nothing here is stored except the order he sends and the pack details he
# types; the plan itself is computed at read time, like every stock figure.
import math

ORDER_DAYS_DEFAULT = 40
PACK_TYPES = ("strip", "bottle", "pouch", "box", "tube", "sachet", "vial", "pack")
_PACK_PLURAL = {"box": "boxes", "pouch": "pouches"}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _order_days():
    try:
        v = int(setting("order_days") or ORDER_DAYS_DEFAULT)
    except (TypeError, ValueError):
        v = ORDER_DAYS_DEFAULT
    return v if 7 <= v <= 120 else ORDER_DAYS_DEFAULT


def _month_after(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _pack_word(ptype, n):
    ptype = ptype or "pack"
    if n == 1:
        return ptype
    return _PACK_PLURAL.get(ptype, ptype + "s")


def _order_cfg():
    return dict((r["med_id"], r) for r in db().execute(
        "SELECT med_id, pack_type, keep_units FROM stock_order_cfg").fetchall())


def _last_fills(con):
    """The most recent pillbox fill of each medicine. A fill of 7 days made
    2 days ago still holds 5 days of doses that have left stock."""
    out = {}
    for r in con.execute(
            "SELECT med_id, qty, at FROM stock_events WHERE kind='FILL' "
            "ORDER BY at, id").fetchall():
        out[r["med_id"]] = r
    return out


def _stock_links(con):
    """GUTLOG_V3200_PIPES -- {medicine whose strengths vary: {strength label:
    (stock medicine id, units per label)}}. A label of two capsules of one
    product is that product with units 2."""
    out = {}
    for r in con.execute("SELECT med_id, variant, stock_med_id, units FROM stock_links").fetchall():
        out.setdefault(r["med_id"], {})[(r["variant"] or "").strip().lower()] = (
            r["stock_med_id"], float(r["units"] or 1))
    return out


def _regular_ids(con, on_day):
    """Medicines on the monthly (daily) pipeline for a given day: those with a
    fixed schedule, and the stock products a variant schedule is linked to.
    Everything else is SOS and goes by its keep-on-hand figure instead."""
    fixed, linked = {}, set()
    links = _stock_links(con)
    for l in con.execute(
            "SELECT med_id, dose_text, variants FROM med_schedule WHERE valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)", (on_day, on_day)).fetchall():
        if (l["variants"] or "").strip():
            for t in links.get(l["med_id"], {}).values():
                linked.add(t[0])
        else:
            fixed[l["med_id"]] = fixed.get(l["med_id"], 0.0) + _units(l["dose_text"])
    return fixed, linked


def _pack_qty(units, ps, ptype):
    packs = int(math.ceil(units / float(ps))) if ps > 0 else 0
    if packs:
        return packs * ps, packs, "%d %s of %d" % (packs, _pack_word(ptype or "pack", packs), ps)
    return units, 0, "%d units" % units


def _order_plan(tday=None):
    """GUTLOG_V3200_PIPES -- two pipelines. The MONTHLY order carries only the
    daily medicines (fixed schedule, or the product a variant schedule is
    linked to) and tops them up to ORDER_DAYS from the stock expected on the
    1st. SOS medicines never ride the monthly order; they come up on their
    own list (_sos_plan) whenever they run low."""
    con = db()
    tday = tday or date.fromisoformat(today())
    first = _month_after(tday)
    gap = (first - tday).days
    days = _order_days()
    cfg = _order_cfg()
    fills = _last_fills(con)
    sched_units, linked = _regular_ids(con, first.isoformat())
    _now_fixed, now_linked = _regular_ids(con, tday.isoformat())
    lines, skipped = [], []
    for r in _stock_rows():
        c = cfg.get(r["med_id"])
        ptype = (c["pack_type"] if c else "") or ""
        keep = float(c["keep_units"] or 0) if c else 0.0
        if not r["trackable"]:
            if not r.get("links"):
                skipped.append({"name": r["name"],
                                "why": "strengths vary - link its strengths to stock"})
            continue
        su = sched_units.get(r["med_id"], 0.0)
        if (r["can_pillbox"] and su <= 0) or \
                (r["med_id"] in now_linked and r["med_id"] not in linked and su <= 0):
            skipped.append({"name": r["name"], "why": "schedule ends before the 1st"})
            continue
        if su > 0:
            target, basis, use = su * days, "schedule", su
        elif r["med_id"] in linked:
            target, basis, use = max(r["per_day"] * days, keep), "linked", r["per_day"]
            if target <= 0:
                skipped.append({"name": r["name"], "why": "no use logged yet - set a keep figure"})
                continue
        else:
            continue
        if not r["tracked"]:
            skipped.append({"name": r["name"], "why": "not counted yet"})
            continue
        # A filled pillbox has already left stock but not been swallowed:
        # the days it still holds are added back before the month's use is
        # taken off, so filling the box never changes the order.
        in_box = 0.0
        f = fills.get(r["med_id"])
        if r["mode"] == "pillbox" and f and use > 0:
            filled_on = date.fromisoformat(f["at"][:10])
            in_box = max(0.0, float(f["qty"]) / use - (tday - filled_on).days)
        expected = r["current"] + use * in_box - use * gap
        need = target - expected
        if need < 0.5:
            continue
        ps = int(r["pack_size"] or 0)
        units, packs, qty_txt = _pack_qty(int(math.ceil(need - 1e-9)), ps, ptype)
        lines.append({"med_id": r["med_id"], "name": r["name"], "basis": basis,
                      "current": r["current"], "expected": round(expected, 1),
                      "target": round(target, 1), "units": units, "packs": packs,
                      "pack_size": ps, "pack_type": ptype, "qty": qty_txt})
    label = _MONTHS[first.month - 1] + " " + str(first.year)
    text = "Medicines order - " + label + "\n" + "\n".join(
        "%d. %s - %s" % (i + 1, l["name"], l["qty"]) for i, l in enumerate(lines))
    dim = (first - date(tday.year, tday.month, 1)).days
    return {"month": first.strftime("%Y-%m"), "label": label, "days": days,
            "gap": gap, "lines": lines, "skipped": skipped,
            "text": text if lines else "", "last_week": tday.day > dim - 7}


SOS_MONTH = "SOS"


def _sos_reorder_at(keep):
    """Low once stock falls below a third of the keep figure (never below 1):
    keep 15 -> low under 5, keep 4 -> under 2, keep 1 -> at 0."""
    return max(1, int(math.ceil(keep / 3.0)))


def _sos_plan():
    """SOS medicines, judged on stock NOW against their keep-on-hand figure.
    A medicine already in an open SOS order is shown as ordered, not again."""
    con = db()
    cfg = _order_cfg()
    fixed, linked = _regular_ids(con, today())
    s = _order_saved(SOS_MONTH)
    on_order = set()
    if s and s["status"] == "OPEN":
        on_order = set(l["med_id"] for l in s["lines"])
    lines, ordered, nokeep, uncounted = [], [], [], []
    for r in _stock_rows():
        mid = r["med_id"]
        if not r["trackable"] or mid in fixed or mid in linked:
            continue
        c = cfg.get(mid)
        keep = float(c["keep_units"] or 0) if c else 0.0
        ptype = (c["pack_type"] if c else "") or ""
        if keep <= 0:
            if r["tracked"]:
                nokeep.append(r["name"])
            continue
        if not r["tracked"]:
            uncounted.append(r["name"])
            continue
        at = _sos_reorder_at(keep)
        if r["current"] >= at:
            continue
        if mid in on_order:
            ordered.append(r["name"])
            continue
        ps = int(r["pack_size"] or 0)
        units, packs, qty_txt = _pack_qty(int(math.ceil(keep - r["current"] - 1e-9)), ps, ptype)
        lines.append({"med_id": mid, "name": r["name"], "basis": "keep",
                      "current": r["current"], "keep": keep, "reorder_at": at,
                      "units": units, "packs": packs, "pack_size": ps,
                      "pack_type": ptype, "qty": qty_txt})
    text = "Medicines needed (running low)\n" + "\n".join(
        "%d. %s - %s" % (i + 1, l["name"], l["qty"]) for i, l in enumerate(lines))
    return {"lines": lines, "ordered": ordered, "nokeep": nokeep, "uncounted": uncounted,
            "text": text if lines else "", "saved": s}


def _order_saved(month):
    r = db().execute("SELECT id, month, status, created, received_at, lines, text "
                     "FROM stock_orders WHERE month=? ORDER BY id DESC LIMIT 1",
                     (month,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "month": r["month"], "status": r["status"],
            "created": r["created"], "received_at": r["received_at"] or "",
            "lines": json.loads(r["lines"] or "[]"), "text": r["text"]}


@app.route("/api/order")
@login_required
def api_order():
    p = _order_plan()
    p["saved"] = _order_saved(p["month"])
    p["due"] = bool(p["last_week"] and not p["saved"] and p["lines"])
    p["pack_types"] = list(PACK_TYPES)
    p["sos"] = _sos_plan()  # GUTLOG_V3200_PIPES
    return jsonify(p)


@app.route("/api/order/save", methods=["POST"])
@login_required
def api_order_save():
    p = _order_plan()
    if not p["lines"]:
        return jsonify(ok=False, err="Nothing to order - stock covers the target."), 400
    s = _order_saved(p["month"])
    if s and s["status"] == "RECEIVED":
        return jsonify(ok=False, err="This month's order is already received. Undo that first."), 400
    blob = json.dumps(p["lines"])
    if s:
        db().execute("UPDATE stock_orders SET lines=?, text=?, created=? WHERE id=?",
                     (blob, p["text"], now_s(), s["id"]))
        oid = s["id"]
    else:
        cur = db().execute("INSERT INTO stock_orders(month, status, created, lines, text) "
                           "VALUES(?,?,?,?,?)", (p["month"], "OPEN", now_s(), blob, p["text"]))
        oid = cur.lastrowid
    db().commit()
    return jsonify(ok=True, id=oid, text=p["text"], n=len(p["lines"]))


def _order_by_id(d):
    try:
        oid = int(d.get("id"))
    except (TypeError, ValueError):
        return None
    return db().execute("SELECT * FROM stock_orders WHERE id=?", (oid,)).fetchone()


@app.route("/api/order/received", methods=["POST"])
@login_required
def api_order_received():
    o = _order_by_id(J())
    if not o:
        return jsonify(ok=False, err="No such order."), 400
    if o["status"] != "OPEN":
        return jsonify(ok=False, err="Already marked received."), 400
    at, n = _now_at(), 0
    for l in json.loads(o["lines"] or "[]"):
        q = float(l.get("units") or 0)
        if q <= 0 or not _stock_med({"med_id": l.get("med_id")}):
            continue
        db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) "
                     "VALUES(?,?,?,?,?,?)",
                     (l["med_id"], "ADD", q, at, "order:" + str(o["id"]), now_s()))
        n += 1
    db().execute("UPDATE stock_orders SET status='RECEIVED', received_at=? WHERE id=?",
                 (at, o["id"]))
    db().commit()
    return jsonify(ok=True, n=n)


@app.route("/api/order/received/undo", methods=["POST"])
@login_required
def api_order_received_undo():
    o = _order_by_id(J())
    if not o or o["status"] != "RECEIVED":
        return jsonify(ok=False, err="That order is not marked received."), 400
    db().execute("DELETE FROM stock_events WHERE kind='ADD' AND note=?",
                 ("order:" + str(o["id"]),))
    db().execute("UPDATE stock_orders SET status='OPEN', received_at='' WHERE id=?", (o["id"],))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/order/days", methods=["POST"])
@login_required
def api_order_days():
    try:
        v = int(J().get("days"))
    except (TypeError, ValueError):
        v = 0
    if not 7 <= v <= 120:
        return jsonify(ok=False, err="Days must be 7 to 120."), 400
    set_setting("order_days", str(v))
    return jsonify(ok=True, days=v)


@app.route("/api/stock/pack", methods=["POST"])
@login_required
def api_stock_pack():
    d = J()
    mid = _stock_med(d)
    try:
        ps = int(float(d.get("pack_size") or 0))
        keep = float(d.get("keep") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Pack size and keep must be numbers."), 400
    ptype = (d.get("pack_type") or "").strip().lower()
    if not mid or not 0 <= ps <= 1000 or not 0 <= keep <= 10000 or \
            (ptype and ptype not in PACK_TYPES):
        return jsonify(ok=False, err="Check the pack details."), 400
    db().execute("UPDATE prnmeds SET pack_size=? WHERE id=?", (ps, mid))
    db().execute("INSERT INTO stock_order_cfg(med_id, pack_type, keep_units) VALUES(?,?,?) "
                 "ON CONFLICT(med_id) DO UPDATE SET pack_type=excluded.pack_type, "
                 "keep_units=excluded.keep_units", (mid, ptype, keep))
    db().commit()
    return jsonify(ok=True)


# GUTLOG_V3200_PIPES -- the SOS list and the strength links
@app.route("/api/order/sos/save", methods=["POST"])
@login_required
def api_order_sos_save():
    p = _sos_plan()
    if not p["lines"]:
        return jsonify(ok=False, err="No SOS medicine is running low."), 400
    s = p["saved"]
    if s and s["status"] == "OPEN":
        lines = s["lines"] + p["lines"]
    else:
        lines = p["lines"]
    text = "Medicines needed (running low)\n" + "\n".join(
        "%d. %s - %s" % (i + 1, l["name"], l["qty"]) for i, l in enumerate(lines))
    blob = json.dumps(lines)
    if s and s["status"] == "OPEN":
        db().execute("UPDATE stock_orders SET lines=?, text=?, created=? WHERE id=?",
                     (blob, text, now_s(), s["id"]))
        oid = s["id"]
    else:
        cur = db().execute("INSERT INTO stock_orders(month, status, created, lines, text) "
                           "VALUES(?,?,?,?,?)", (SOS_MONTH, "OPEN", now_s(), blob, text))
        oid = cur.lastrowid
    db().commit()
    return jsonify(ok=True, id=oid, text=p["text"], n=len(p["lines"]))


def _variant_labels(con, mid):
    labels = []
    for l in con.execute(
            "SELECT variants FROM med_schedule WHERE med_id=? AND variants<>'' "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)", (mid, today())).fetchall():
        for v in (l["variants"] or "").split("|"):
            v = v.strip()
            if v and v.lower() not in [x.lower() for x in labels]:
                labels.append(v)
    return labels


@app.route("/api/stock/link", methods=["POST"])
@login_required
def api_stock_link():
    d = J()
    con = db()
    mid = _stock_med(d)
    labels = _variant_labels(con, mid) if mid else []
    if not labels:
        return jsonify(ok=False, err="That medicine has no strengths to link."), 400
    want = [x.lower() for x in labels]
    rows = []
    for l in d.get("links") or []:
        v = str(l.get("variant") or "").strip().lower()
        if not l.get("stock_med_id"):
            continue
        tid = _stock_med({"med_id": l.get("stock_med_id")})
        try:
            u = float(l.get("units") or 1)
        except (TypeError, ValueError):
            u = 0
        if v not in want or not tid or tid == mid or not 0.25 <= u <= 10 or _variant_labels(con, tid):
            return jsonify(ok=False, err="Check the links: each strength to another medicine, 0.25 to 10 units."), 400
        rows.append((mid, v, tid, u))
    con.execute("DELETE FROM stock_links WHERE med_id=?", (mid,))
    for r in rows:
        con.execute("INSERT INTO stock_links(med_id, variant, stock_med_id, units) VALUES(?,?,?,?)", r)
    con.commit()
    return jsonify(ok=True, n=len(rows))


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
        "SELECT p.id AS med_id, p.name, p.molecule, COALESCE(ms.strength,'') AS strength, "
        "s.slot, s.dose_text, s.variants, s.valid_from "
        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "
        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE s.valid_from<=? "
        "AND (s.valid_to='' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",
        (tday, tday)).fetchall()]
    # GUTLOG_V3120_PAIN -- schedules that have ENDED, with the date they
    # ended. api_feed_stack drops them from `regimen`, correctly; but a
    # consumer that wants to stop its own record needs the date GutLog
    # ended it, not today.
    floor = (date.today() - timedelta(days=180)).isoformat()
    ended = [dict(r) for r in db().execute(
        "SELECT p.id AS med_id, p.name, p.molecule, s.slot, MAX(s.valid_to) AS valid_to "
        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "
        "WHERE COALESCE(s.valid_to,'')<>'' AND s.valid_to<? AND s.valid_to>=? "
        "AND p.id NOT IN (SELECT med_id FROM med_schedule "
        "WHERE valid_to='' OR valid_to IS NULL OR valid_to>=?) "
        "GROUP BY p.id ORDER BY valid_to DESC",
        (tday, floor, tday)).fetchall()]
    taken = {}
    for e in _feed_events(since):
        k = e["med_id"] or e["name"]
        t = taken.setdefault(k, {"med_id": e["med_id"], "name": e["name"],
                                 "molecule": e["molecule"], "strength": _salt_strength(e["med_id"]),
                                 "doses": 0, "days": set(),
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
                   regimen=regimen, ended=ended, taken=out)


# ------------------------------------------------------------------ salts, status, activity
# GUTLOG_V370_SALTS_ACTIVITY
import re
RXGUARD_URL = os.environ.get("GUTLOG_RXGUARD_URL", "http://127.0.0.1:8031")
FITLOG_URL = os.environ.get("GUTLOG_FITLOG_URL", "http://127.0.0.1:8040")
RXNAV_URL = os.environ.get("GUTLOG_RXNAV_URL", "https://rxnav.nlm.nih.gov")
_LINK_CACHE = {}


def _links_enabled():
    """Outward calls only from the live database; a scratch database (every
    test suite) never reaches out. GUTLOG_LINKS=1/0 overrides."""
    v = os.environ.get("GUTLOG_LINKS", "")
    if v in ("0", "1"):
        return v == "1"
    return os.path.dirname(os.path.abspath(DB_PATH)) == BASE


def _link_get(url, ttl=300, timeout=3, token=True):
    """JSON from a companion app (with the feed token) or NLM (without).
    Cached; never raises; None on any failure."""
    import time as _t
    import urllib.request
    if not _links_enabled():
        return None
    hit = _LINK_CACHE.get(url)
    if hit and _t.time() - hit[0] < ttl:
        return hit[1]
    res = None
    try:
        hdr = {"Authorization": "Bearer " + _FEED_TOKEN} if (token and _FEED_TOKEN) else {}
        local = url.startswith("http://127.")
        op = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local \
            else urllib.request.build_opener()
        with op.open(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception:
        res = None
    _LINK_CACHE[url] = (_t.time(), res)
    return res


def _salt_rows():
    return db().execute(
        "SELECT p.id, p.name, COALESCE(p.molecule,'') AS molecule, "
        "COALESCE(ms.strength,'') AS strength, COALESCE(ms.no_salt,0) AS no_salt, "
        "COALESCE(p.scheduled,0) AS scheduled FROM prnmeds p "
        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE p.active=1 ORDER BY p.sort, p.id").fetchall()


def _salt_strength(mid):
    if not mid:
        return ""
    r = db().execute("SELECT strength FROM med_salts WHERE med_id=?", (mid,)).fetchone()
    return (r["strength"] or "") if r else ""


@app.route("/api/salts")
@login_required
def api_salts():
    out = []
    for r in _salt_rows():
        d = dict(r)
        d["needs"] = (not d["molecule"].strip()) and not d["no_salt"]
        out.append(d)
    out.sort(key=lambda d: (not d["needs"], not d["scheduled"]))
    return jsonify(meds=out, need=sum(1 for d in out if d["needs"]))


_SALT_OK = re.compile(r"^[a-z0-9 ,+().\-/']*$")


@app.route("/api/salt", methods=["POST"])
@login_required
def api_salt():
    d = J()
    try:
        mid = int(d.get("med_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Bad medicine."), 400
    if not db().execute("SELECT 1 FROM prnmeds WHERE id=?", (mid,)).fetchone():
        return jsonify(ok=False, err="Unknown medicine."), 400
    mol = re.sub(r"\s+", " ", (d.get("molecule") or "").strip().lower())
    mol = re.sub(r"\s*\+\s*", " + ", mol)
    strength = re.sub(r"\s+", " ", (d.get("strength") or "").strip())[:40]
    no_salt = 1 if d.get("no_salt") else 0
    if len(mol) > 120 or not _SALT_OK.match(mol):
        return jsonify(ok=False, err="Salt: letters, numbers and + only."), 400
    if no_salt:
        mol = ""
    db().execute("UPDATE prnmeds SET molecule=? WHERE id=?", (mol, mid))
    db().execute("INSERT INTO med_salts(med_id, strength, no_salt, updated) VALUES(?,?,?,?) "
                 "ON CONFLICT(med_id) DO UPDATE SET strength=excluded.strength, "
                 "no_salt=excluded.no_salt, updated=excluded.updated",
                 (mid, strength, no_salt, now_s()))
    db().commit()
    return jsonify(ok=True, molecule=mol)


@app.route("/api/salt/suggest")
@login_required
def api_salt_suggest():
    import urllib.parse
    q = (request.args.get("q") or "").strip()
    if len(q) < 3 or not _SALT_OK.match(q.lower()):
        return jsonify(suggestions=[])
    j = _link_get(RXNAV_URL + "/REST/spellingsuggestions.json?name=" + urllib.parse.quote(q),
                  ttl=86400, timeout=4, token=False)
    sug = (((j or {}).get("suggestionGroup") or {}).get("suggestionList") or {}).get("suggestion") or []
    return jsonify(suggestions=[s.lower() for s in sug[:8]])


@app.route("/api/medstatus")
@login_required
def api_medstatus():
    need = sum(1 for r in _salt_rows() if not r["molecule"].strip() and not r["no_salt"])
    rx = _link_get(RXGUARD_URL + "/api/feed/status", ttl=300)
    return jsonify(need_salt=need, rx=rx if (rx or {}).get("ok") else None)


ACT_KINDS = {"walk": "Walk", "treadmill": "Treadmill", "cycle_road": "Cycling (road)",
             "cycle_static": "Cycling (static)", "meditation": "Meditation",
             "ot_day": "Operating day"}
# GUTLOG_V3120_PAIN -- hours on his legs are LOAD, not training. The same
# hip/glute/thigh complex appears on long operating days, so the hours
# have to be recorded or every walking-versus-pain comparison is
# confounded by his work. Never counted as exercise minutes.
LOAD_KINDS = ("ot_day",)


@app.route("/api/activity", methods=["POST"])
@login_required
def api_activity_add():
    d = J()
    kind = d.get("kind")
    if kind not in ACT_KINDS:
        return jsonify(ok=False, err="Pick an activity."), 400
    try:
        minutes = float(d.get("minutes"))
    except (TypeError, ValueError):
        minutes = 0
    if not 1 <= minutes <= 600:
        return jsonify(ok=False, err="Minutes must be 1 to 600."), 400
    day = d.get("day") or today()
    atime = d.get("atime") or now_hm()
    if not _valid_day(day):
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if not _valid_hm(atime):
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    if day == today() and atime > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400
    intensity = d.get("intensity") if d.get("intensity") in ("Easy", "Moderate", "Hard") else ""
    db().execute("INSERT INTO activities(day, atime, kind, minutes, intensity, notes, created) "
                 "VALUES(?,?,?,?,?,?,?)", (day, atime, kind, minutes, intensity, note(d), now_s()))
    db().commit()
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    return jsonify(ok=True, id=rid)


@app.route("/api/activity/undo/<int:aid>", methods=["POST"])
@login_required
def api_activity_undo(aid):
    db().execute("DELETE FROM activities WHERE id=?", (aid,))
    db().commit()
    return jsonify(ok=True)


def _hm(s):
    m = re.search(r"(\d{1,2}):(\d{2})", s or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _wtime(ts):
    m = re.search(r"\d{4}-\d{2}-\d{2}[ T](\d{2}:\d{2})", ts or "")
    return m.group(1) if m else ""


def merge_activity(manual, watch):
    """One list: watch workouts (confirming a tapped entry of the same kind
    within 30 minutes of the session) + remaining tapped entries."""
    items, used = [], set()
    for w in (watch or {}).get("workouts") or []:
        k = w.get("kind") or "other"
        st, en = _wtime(w.get("start")), _wtime(w.get("end"))
        s0, e0 = _hm(st), _hm(en) if en else None
        match = None
        for m in manual:
            if m["id"] in used or m["kind"] != k or s0 is None:
                continue
            t = _hm(m["atime"])
            hi = (e0 if e0 is not None else s0) + 30
            if t is not None and s0 - 30 <= t <= hi:
                match = m
                break
        if match:
            used.add(match["id"])
        items.append({"time": st or (match or {}).get("atime", ""), "kind": k,
                      "label": ACT_KINDS.get(k, w.get("wtype") or "Workout"),
                      "minutes": int(round(w.get("minutes") or 0)),
                      "distance_km": w.get("distance_km"),
                      "intensity": (match or {}).get("intensity", ""), "source": "watch",
                      "confirmed": bool(match), "id": (match or {}).get("id")})
    for m in manual:
        if m["id"] in used:
            continue
        items.append({"time": m["atime"] or "", "kind": m["kind"], "label": ACT_KINDS.get(m["kind"], m["kind"]),
                      "minutes": int(round(m["minutes"] or 0)), "distance_km": None,
                      "intensity": m["intensity"] or "", "source": "manual", "confirmed": False,
                      "id": m["id"]})
    mm = (watch or {}).get("mindful_min")
    if mm and not any(i["kind"] == "meditation" for i in items):
        items.append({"time": "", "kind": "meditation", "label": "Mindful minutes",
                      "minutes": int(round(mm)), "distance_km": None, "intensity": "",
                      "source": "watch", "confirmed": False, "id": None})
    for i in items:
        i["load"] = i.get("kind") in LOAD_KINDS
    items.sort(key=lambda i: (i["time"] == "", i["time"]))
    return items


@app.route("/api/activity")
@login_required
def api_activity():
    day = _valid_day(request.args.get("day")) or today()
    manual = [dict(r) for r in db().execute(
        "SELECT id, day, atime, kind, minutes, intensity FROM activities WHERE day=? "
        "ORDER BY atime, id", (day,)).fetchall()]
    watch = _link_get(FITLOG_URL + "/api/feed/activity?day=" + day, ttl=60)
    items = merge_activity(manual, watch if (watch or {}).get("ok") else None)
    steps = int((watch or {}).get("steps") or 0) if (watch or {}).get("ok") else 0
    return jsonify(day=day, items=items,
                   watch=({"ok": bool((watch or {}).get("ok"))} if _links_enabled() else None),
                   summary={"minutes": sum(i["minutes"] for i in items
                                           if i["kind"] not in LOAD_KINDS),
                            "load_minutes": sum(i["minutes"] for i in items
                                                if i["kind"] in LOAD_KINDS),
                            "steps": steps})


@app.route("/api/feed/activities")
@feed_required
def api_feed_activities():
    since = _feed_since(14)
    rows = [dict(r) for r in db().execute(
        "SELECT day, atime, kind, minutes, intensity FROM activities WHERE day>=? "
        "ORDER BY day, atime", (since,)).fetchall()]
    return jsonify(ok=True, app="gutlog", since=since, activities=rows)


# ------------------------------------------------------------------ pain
# GUTLOG_V3120_PAIN -- musculoskeletal pain goes into `episodes`, the table
# that already carries every within-day event. There is no second pain table
# and no second medicine record: an analgesic tapped on a pain tile writes a
# real `doses` row exactly as an ad-hoc dose does, and the same event is
# mirrored to FitLog's analgesic_log carrying the score that was just entered.
# A medicine logged in two places is a record that disagrees with itself.
#
# Sides are never averaged: right is the THR side, left is the native
# arthritic hip, and the tiles keep them apart by construction.
PAIN_SITES_MSK = [
    ("hip_thigh_both", "Both hips + anterior thighs", "both", 1),
    ("hip_thigh_r", "Hip / thigh - R", "R", 1),
    ("hip_thigh_l", "Hip / thigh - L", "L", 1),
    ("glute_both", "Glutes - both", "both", 1),
    ("glute_r", "Glute - R", "R", 1),
    ("glute_l", "Glute - L", "L", 1),
    ("low_back", "Low back", "", 0),
    ("neck_arm_r", "Neck to R arm", "R", 0),
    ("neck_arm_l", "Neck to L arm", "L", 0),
]
PAIN_SITE_MAP = dict((s[0], s) for s in PAIN_SITES_MSK)

# The non-medicine half of the treatment chips. Safe to keep here: nobody's
# private record is a hot shower.
PAIN_TREATMENTS_BASE = ["Heat pad", "Hot shower", "NormaTec", "Rest"]

# The analgesic chips are medicine names, and this repository is public, so
# they live in regimen.local.json beside app.py with every other medicine name
# (see _local_seed, 2026-09-10). Each entry is [chip label, molecule]. A clone
# without that file gets the four physical measures and no drug chips, which
# is the right default for someone else's pain.
PAIN_ANALGESICS = {}
PAIN_TREATMENTS = list(PAIN_TREATMENTS_BASE)
for _pa in _local_seed("pain_analgesics"):
    if isinstance(_pa, list) and len(_pa) >= 2 and _pa[0]:
        # chip label -> (molecule, name to fall back on when GutLog carries
        # no such medicine; the dose is still recorded, under its label)
        PAIN_ANALGESICS[_pa[0]] = (str(_pa[1]).strip().lower(), _pa[0])
        PAIN_TREATMENTS.append(_pa[0])


# GUTLOG_V3170_DOWN -- the down-day cluster as the record describes it, not a
# generic symptom list, and what was done about it. The two medicine chips
# are the analgesic labels from regimen.local.json: this file names no
# medicine.
DOWN_COMPONENTS = ["Hip / thigh ache", "Left abdominal pain", "Fatigue",
                   "Feverishness", "Heavy head / headache",
                   "Eyes burning or watering", "Broken sleep", "Low mood"]
DOWN_COPED_BASE = ["Kept moving indoors", "Rested", "Skipped exercise",
                   "Worked anyway", "Heat pad", "Hot shower", "NormaTec"]
DOWN_COPED = DOWN_COPED_BASE + list(PAIN_ANALGESICS.keys())
DOWN_MOVED, DOWN_RESTED = "Kept moving indoors", "Rested"

# Verbatim from the Action Plan's flare protocol. A note, not an alarm, and
# not advice: it repeats what his own plan already says, on the day it says
# to do it.
DOWN_PROTOCOL = ("Third day of this run; your flare protocol asks for "
                 "calprotectin and ESR/CRP within 48 hours.")


# GUTLOG_V3210_ONEDOSE -- a chip on a symptom says what was USED for it.
# One tablet entered against three symptoms is one dose, so a chip whose
# medicine (or a product carrying all its ingredients) was logged shortly
# before is linked to that dose instead of writing another row.
SAME_DOSE_BEFORE_MIN = 360
SAME_DOSE_AFTER_MIN = 30


def _mol_set(mol):
    return set(p.strip().lower() for p in re.split(r"[+,]", mol or "") if p.strip())


def _hm_min(hm):
    try:
        h, m = (hm or "")[:5].split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def _recent_same_dose(mol, mid, name, day, hm):
    """The latest dose on `day` from SAME_DOSE_BEFORE_MIN before `hm` to
    SAME_DOSE_AFTER_MIN after it, of the same medicine or of a product whose
    ingredients include every ingredient of this one. None if there is none."""
    want = _mol_set(mol)
    at = _hm_min(hm)
    if at is None:
        return None
    best = None
    for r in db().execute(
            "SELECT d.id, d.dtime, d.medicine, d.med_id, COALESCE(p.molecule,'') AS molecule "
            "FROM doses d LEFT JOIN prnmeds p ON p.id=d.med_id WHERE d.day=? "
            "AND COALESCE(d.status,'')<>'SKIPPED' ORDER BY d.dtime, d.id", (day,)).fetchall():
        t = _hm_min(r["dtime"])
        if t is None or not (at - SAME_DOSE_BEFORE_MIN <= t <= at + SAME_DOSE_AFTER_MIN):
            continue
        same = (mid is not None and r["med_id"] == mid) or \
            (r["med_id"] is None and (r["medicine"] or "") == name) or \
            (bool(want) and want <= _mol_set(r["molecule"]))
        if same and (best is None or (r["dtime"] or "") >= (best["dtime"] or "")):
            best = r
    return best


def _log_analgesics(labels, day, hm, reason, score, ref):
    """Write one doses row per analgesic chip and mirror each to FitLog with
    the score attached. Shared by the pain tiles and the down-day card, so a
    medicine is recorded the same way whichever surface it was tapped on.
    GUTLOG_V3210_ONEDOSE: a chip already covered by a recent dose is linked,
    not written. Returns (doses, mirrored, not_mirrored, linked, same_dose)."""
    doses, mirrored, missed, same_dose = [], [], [], []
    linked = _links_enabled()
    for t in labels:
        if t not in PAIN_ANALGESICS:
            continue
        mol, fallback = PAIN_ANALGESICS[t]
        mid, name = _pain_med(mol, fallback)
        prior = _recent_same_dose(mol, mid, name, day, hm)
        if prior is not None:
            same_dose.append({"label": t, "med_id": mid, "name": name,
                              "dose_id": prior["id"], "dose_name": prior["medicine"],
                              "time": prior["dtime"] or "", "reason": reason})
            continue
        db().execute(
            "INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,"
            "status,med_id,sched_id,dose_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (day, hm, name, reason, None, "", now_s(), "EXTRA", mid, None, ""))
        db().commit()
        doses.append(name)
        if not linked:
            continue
        ans = _link_post(FITLOG_URL + "/api/analgesic",
                         {"dt": day + "T" + hm, "molecule": mol, "name": name,
                          "dose_label": name, "pain_at_time": score,
                          "notes": "GutLog: " + reason, "source": "gutlog",
                          "ref": ref + "-" + t.lower().replace(" ", "-")})
        (mirrored if (ans or {}).get("ok") else missed).append(name)
    return doses, mirrored, missed, linked, same_dose


def _down_runs(days):
    """Consecutive calendar days grouped into runs. Computed every time it is
    read: date arithmetic on the stored days, nothing stored about the run."""
    out = []
    for d in sorted(set(days)):
        try:
            cur = date.fromisoformat(d)
        except ValueError:
            continue
        if out and (cur - date.fromisoformat(out[-1]["end"])).days == 1:
            out[-1]["end"] = d
            out[-1]["days"].append(d)
        else:
            out.append({"start": d, "end": d, "days": [d]})
    for r in out:
        r["length"] = len(r["days"])
    return out


def _down_all_days():
    return [r["day"] for r in db().execute(
        "SELECT day FROM down_days ORDER BY day").fetchall()]


def _down_run_of(day):
    """(position in its run, the run) for a marked day; (0, None) otherwise."""
    for r in _down_runs(_down_all_days()):
        if day in r["days"]:
            return r["days"].index(day) + 1, r
    return 0, None


def _split_pipe(s):
    return [x for x in (s or "").split("|") if x]


def _down_row(day):
    r = db().execute("SELECT day, components, coped, note FROM down_days WHERE day=?",
                     (day,)).fetchone()
    if not r:
        return None
    return {"day": r["day"], "components": _split_pipe(r["components"]),
            "coped": _split_pipe(r["coped"]), "note": r["note"] or ""}


def _temp_on(day):
    r = db().execute("SELECT temp, vtime FROM vitals WHERE day=? AND temp IS NOT NULL "
                     "ORDER BY vtime DESC, id DESC LIMIT 1", (day,)).fetchone()
    return {"value": r["temp"], "vtime": r["vtime"] or ""} if r else None


def _link_post(url, payload, timeout=3):
    """POST JSON to a companion app carrying the feed token. Never raises;
    returns the decoded answer, or None when links are off or the other app
    refused. The write that matters has already happened locally."""
    import urllib.request
    if not _links_enabled():
        return None
    hdr = {"Content-Type": "application/json"}
    if _FEED_TOKEN:
        hdr["Authorization"] = "Bearer " + _FEED_TOKEN
    try:
        local = url.startswith("http://127.")
        op = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local \
            else urllib.request.build_opener()
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=hdr, method="POST")
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _pain_med(molecule, fallback):
    """The prnmeds row for an analgesic chip: by molecule first, by name
    second. Returns (med_id, name). med_id is None when GutLog carries no such
    medicine -- the dose is still recorded under its label rather than lost."""
    mol = (molecule or "").strip().lower()
    r = db().execute("SELECT id, name FROM prnmeds WHERE active=1 AND "
                     "LOWER(COALESCE(molecule,''))=? ORDER BY sort, id",
                     (mol,)).fetchone()
    if not r:
        r = db().execute("SELECT id, name FROM prnmeds WHERE active=1 AND "
                         "LOWER(name) LIKE ? ORDER BY sort, id",
                         (mol + "%",)).fetchone()
    if r:
        return r["id"], r["name"]
    return None, fallback


def _dur_text(mins):
    if mins < 60:
        return str(mins) + " min"
    return str(mins // 60) + " h " + ("%02d" % (mins % 60)) + " min"


@app.route("/api/pain", methods=["GET", "POST"])
@login_required
def api_pain():
    if request.method == "GET":
        day = _valid_day(request.args.get("day")) or today()
        rows = []
        for r in db().execute(
                "SELECT id, etime, etype, side, severity, duration, "
                "COALESCE(treatments,'') AS treatments, COALESCE(radiates,0) AS radiates "
                "FROM episodes WHERE day=? AND category='pain' ORDER BY etime, id",
                (day,)).fetchall():
            d = dict(r)
            meta = PAIN_SITE_MAP.get(d["etype"])
            d["label"] = meta[1] if meta else d["etype"]
            rows.append(d)
        return jsonify(day=day, rows=rows, sites=[
            {"slug": s[0], "label": s[1], "side": s[2], "radiates": bool(s[3])}
            for s in PAIN_SITES_MSK], treatments=PAIN_TREATMENTS)

    d = J()
    meta = PAIN_SITE_MAP.get((d.get("site") or "").strip())
    if not meta:
        return jsonify(ok=False, err="Pick a site."), 400
    try:
        score = int(d.get("score"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Give it a score out of 10."), 400
    if not 0 <= score <= 10:
        return jsonify(ok=False, err="Score must be 0 to 10."), 400
    treats = [t for t in (d.get("treatments") or []) if t in PAIN_TREATMENTS]
    radiates = 1 if (meta[3] and d.get("radiates")) else 0
    day = d.get("day") or today()
    etime = d.get("etime") or now_hm()
    if not _valid_day(day):
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if not _valid_hm(etime):
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    if day == today() and etime > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400

    # duration is left empty on purpose: nothing is asked at the moment of
    # pain, and the "eased" tap stamps it later from etime to then.
    insert("episodes",
           ["day", "etime", "category", "etype", "side", "severity", "duration",
            "notes", "bristol", "treatments", "radiates"],
           [day, etime, "pain", meta[0], meta[2], score, "", note(d), "",
            "|".join(treats), radiates])
    eid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    doses, mirrored, missed, linked, same = _log_analgesics(
        treats, day, etime, meta[1], score, "gutlog-episode-" + str(eid))
    return jsonify(ok=True, id=eid, site=meta[1], side=meta[2], radiates=radiates,
                   doses=doses, mirrored=mirrored, not_mirrored=missed, linked=linked,
                   same_dose=same)


# ------------------------------------------------------------------ down days
# GUTLOG_V3170_DOWN
def _down_state(day):
    row = _down_row(day)
    n, run = _down_run_of(day) if row else (0, None)
    proto = ""
    if run and n >= 3:
        proto = DOWN_PROTOCOL if n == 3 else (
            "Day " + str(n) + " of this run; your flare protocol asks for "
            "calprotectin and ESR/CRP within 48 hours.")
    return {"day": day, "marked": bool(row),
            "components": row["components"] if row else [],
            "coped": row["coped"] if row else [],
            "note": row["note"] if row else "",
            "run": ({"n": n, "length": run["length"], "start": run["start"],
                     "end": run["end"]} if run else None),
            "protocol": proto, "temp": _temp_on(day),
            "components_all": DOWN_COMPONENTS, "coped_all": DOWN_COPED}


@app.route("/api/downday")
@login_required
def api_downday():
    day = _valid_day(request.args.get("day")) or today()
    return jsonify(ok=True, **_down_state(day))


@app.route("/api/downday", methods=["POST"])
@login_required
def api_downday_set():
    """Mark a day. With no body beyond the day it is one tap; components,
    coped and temp are each optional and each may arrive on its own later.
    day is UNIQUE, so a repeat corrects the row rather than adding one."""
    d = J()
    day = _valid_day(d.get("day") or today())
    if not day:
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    before = _down_row(day) or {"components": [], "coped": [], "note": ""}
    comps = before["components"]
    if "components" in d:
        comps = [c for c in (d.get("components") or []) if c in DOWN_COMPONENTS]
    coped = before["coped"]
    if "coped" in d:
        coped = [c for c in (d.get("coped") or []) if c in DOWN_COPED]
    nt = before["note"] if "note" not in d else note(d)
    db().execute(
        "INSERT INTO down_days(day,components,coped,note,created) VALUES(?,?,?,?,?) "
        "ON CONFLICT(day) DO UPDATE SET components=excluded.components, "
        "coped=excluded.coped, note=excluded.note",
        (day, "|".join(comps), "|".join(coped), nt, now_s()))
    db().commit()

    # A medicine chip writes a real doses row -- once. Only chips that were
    # not already on the stored row are logged, so correcting the row cannot
    # record the same tablet twice.
    new_meds = [c for c in coped if c in PAIN_ANALGESICS and c not in before["coped"]]
    hm = now_hm() if day == today() else "12:00"
    doses, mirrored, missed, linked, same = _log_analgesics(
        new_meds, day, hm, "Down day", None, "gutlog-down-" + day)

    # Temperature is a vitals row, never a column here.
    temp_saved = None
    if d.get("temp") not in (None, ""):
        try:
            tv = float(d.get("temp"))
        except (TypeError, ValueError):
            tv = None
        if tv is not None and 30 <= tv <= 115:
            vt = _valid_hm(d.get("vtime")) or (now_hm() if day == today() else "12:00")
            insert("vitals", ["day", "vtime", "sys", "dia", "pulse", "weight", "waist",
                              "temp", "notes"],
                   [day, vt, None, None, None, None, None, tv, "Down day"])
            temp_saved = tv
    st = _down_state(day)
    st.update(ok=True, doses=doses, mirrored=mirrored, not_mirrored=missed,
              linked=linked, temp_saved=temp_saved, same_dose=same)
    return jsonify(**st)


@app.route("/api/downday/unmark", methods=["POST"])
@login_required
def api_downday_unmark():
    d = J()
    day = _valid_day(d.get("day") or today())
    if not day:
        return jsonify(ok=False, err="Pick a real date."), 400
    db().execute("DELETE FROM down_days WHERE day=?", (day,))
    db().commit()
    return jsonify(ok=True, **_down_state(day))


@app.route("/api/feed/downdays")
@feed_required
def api_feed_downdays():
    """Read-only, for FitLog: which days were down days, so its trend can
    leave them out instead of reading them as non-adherence."""
    since = _feed_since(60)
    rows = [dict(day=r["day"], components=_split_pipe(r["components"]),
                 coped=_split_pipe(r["coped"]))
            for r in db().execute(
                "SELECT day, components, coped FROM down_days WHERE day>=? ORDER BY day",
                (since,)).fetchall()]
    return jsonify(ok=True, app="gutlog", since=since, days=rows,
                   runs=_down_runs([r["day"] for r in rows]))


# ------------------------------------------------------------------ day context
# GUTLOG_V3230_CONTEXT -- what else was going on that day: heavy exertion,
# poor sleep, travel, unwell, stress, ate out. Two taps (open, tap), stored
# one row per day+tag, so a food trial or a symptom comparison can set these
# days aside instead of blaming whatever was eaten. Separate from a down day:
# a down day is his symptom cluster; context is the circumstance around it.
DAY_CONTEXT = [
    ("exertion", "Heavy exertion"),
    ("poor_sleep", "Poor sleep"),
    ("travel", "Travel"),
    ("unwell", "Unwell"),
    ("stress", "Stress"),
    ("ate_out", "Ate out"),
]
DAY_CONTEXT_KEYS = dict(DAY_CONTEXT)


def _ctx_for(day):
    return [r["tag"] for r in db().execute(
        "SELECT tag FROM day_context WHERE day=? ORDER BY created", (day,)).fetchall()]


@app.route("/api/daycontext")
@login_required
def api_daycontext():
    day = _valid_day(request.args.get("day")) or today()
    note_row = db().execute("SELECT note FROM day_context_note WHERE day=?", (day,)).fetchone()
    return jsonify(day=day, tags=_ctx_for(day), note=note_row["note"] if note_row else "",
                   options=[{"key": k, "label": l} for k, l in DAY_CONTEXT])


@app.route("/api/daycontext", methods=["POST"])
@login_required
def api_daycontext_set():
    """{day, tag, on} toggles one tag; {day, note} sets the day's note.
    Idempotent: turning on a tag that is on changes nothing."""
    d = J()
    day = _valid_day(d.get("day") or today())
    if not day:
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if "tag" in d:
        tag = d.get("tag")
        if tag not in DAY_CONTEXT_KEYS:
            return jsonify(ok=False, err="Unknown tag."), 400
        if d.get("on"):
            db().execute("INSERT OR IGNORE INTO day_context(day, tag, created) VALUES(?,?,?)",
                         (day, tag, now_s()))
        else:
            db().execute("DELETE FROM day_context WHERE day=? AND tag=?", (day, tag))
    if "note" in d:
        nt = note(d, "note", 200)
        if nt:
            db().execute("INSERT INTO day_context_note(day, note) VALUES(?,?) "
                         "ON CONFLICT(day) DO UPDATE SET note=excluded.note", (day, nt))
        else:
            db().execute("DELETE FROM day_context_note WHERE day=?", (day,))
    db().commit()
    return jsonify(ok=True, day=day, tags=_ctx_for(day))


@app.route("/api/feed/daycontext")
@feed_required
def api_feed_daycontext():
    """Read-only: which days carry which circumstances, for any consumer that
    compares days (food trials, FitLog trends)."""
    since = _feed_since(90)
    out = {}
    for r in db().execute("SELECT day, tag FROM day_context WHERE day>=? ORDER BY day",
                          (since,)).fetchall():
        out.setdefault(r["day"], []).append(r["tag"])
    return jsonify(ok=True, app="gutlog", since=since,
                   days=[{"day": k, "tags": v} for k, v in sorted(out.items())])


def _act_minutes_by_day(since):
    marks = ",".join("?" for _ in LOAD_KINDS)
    rows = db().execute(
        "SELECT day, SUM(minutes) AS m FROM activities "
        "WHERE kind NOT IN (" + marks + ") AND day>=? GROUP BY day",
        tuple(LOAD_KINDS) + (since,)).fetchall()
    return dict((r["day"], float(r["m"] or 0)) for r in rows)


@app.route("/api/downdays")
@login_required
def api_downdays():
    """The view. Everything here is read from rows that already exist; the
    down-day marks are the only new fact, and they make the rest comparable."""
    try:
        days_n = max(7, min(365, int(request.args.get("days") or 90)))
    except (TypeError, ValueError):
        days_n = 90
    since = (date.today() - timedelta(days=days_n)).isoformat()
    # one day earlier than the window, so the oldest down day has a "before"
    since_b = (date.today() - timedelta(days=days_n + 1)).isoformat()
    marked = [dict(day=r["day"], components=_split_pipe(r["components"]),
                   coped=_split_pipe(r["coped"]), note=r["note"] or "")
              for r in db().execute(
                  "SELECT day, components, coped, note FROM down_days "
                  "WHERE day>=? ORDER BY day", (since,)).fetchall()]
    mdays = [m["day"] for m in marked]
    runs = _down_runs(mdays)

    # ---- what already exists for every day in the window ----------------
    feed = _link_get(FITLOG_URL + "/api/feed/watch?days=" + str(min(180, days_n + 2)),
                     ttl=60)
    linked = bool((feed or {}).get("ok"))
    by_date = dict((x.get("date"), x) for x in ((feed or {}).get("daily") or []))
    epochs = (feed or {}).get("epochs") or []
    load = _watch_load_by_day(since_b)
    act = _act_minutes_by_day(since_b)
    sleep = dict((r["day"], r["sleep"]) for r in db().execute(
        "SELECT day, sleep FROM days WHERE day>=?", (since_b,)).fetchall())
    doses = dict((r["day"], r["n"]) for r in db().execute(
        "SELECT day, COUNT(*) AS n FROM doses WHERE day>=? "
        "AND COALESCE(status,'')<>'SKIPPED' GROUP BY day", (since_b,)).fetchall())
    temps = {}
    for r in db().execute(
            "SELECT day, temp, vtime FROM vitals WHERE day>=? AND temp IS NOT NULL "
            "ORDER BY day, vtime", (since_b,)).fetchall():
        temps[r["day"]] = {"value": r["temp"], "vtime": r["vtime"] or ""}

    def epoch_of(d):
        for e in epochs:
            s, en = e.get("date_start") or "", e.get("date_end") or ""
            if s and d < s:
                continue
            if en and d > en:
                continue
            return e.get("label") or ""
        return ""

    def facts(d):
        m = (by_date.get(d) or {}).get("metrics") or {}
        st = m.get("steps") or {}
        sl = m.get("sleep_hours") or {}
        return {"day": d,
                "steps": st.get("value"),
                "load_h": (round(load[d] / 60.0, 1) if d in load else None),
                "act_min": (round(act[d]) if d in act else None),
                "sleep": (sl.get("value") if sl.get("value") is not None
                          else (sleep.get(d) or None)),
                "doses": doses.get(d, 0),
                "temp": (temps.get(d) or {}).get("value"),
                "epoch": epoch_of(d)}

    pairs = []
    for m in marked:
        b = (date.fromisoformat(m["day"]) - timedelta(days=1)).isoformat()
        pairs.append({"day": m["day"], "before": b, "d": facts(m["day"]),
                      "b": facts(b), "components": m["components"],
                      "coped": m["coped"]})

    # ---- per month --------------------------------------------------------
    months = {}
    for m in marked:
        months.setdefault(m["day"][:7], {"month": m["day"][:7], "n": 0, "runs": []})
        months[m["day"][:7]]["n"] += 1
    for r in runs:
        months.setdefault(r["start"][:7], {"month": r["start"][:7], "n": 0, "runs": []})
        months[r["start"][:7]]["runs"].append(r["length"])
    months = [months[k] for k in sorted(months)]

    # ---- co-occurrence ----------------------------------------------------
    ccount = {}
    pcount = {}
    for m in marked:
        cs = sorted(set(m["components"]))
        for c in cs:
            ccount[c] = ccount.get(c, 0) + 1
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                k = cs[i] + " + " + cs[j]
                pcount[k] = pcount.get(k, 0) + 1
    comps = sorted(ccount.items(), key=lambda kv: (-kv[1], kv[0]))
    cpairs = sorted(pcount.items(), key=lambda kv: (-kv[1], kv[0]))[:6]

    # ---- temperature: the one active ask, so its absence is reported -------
    with_t = [{"day": m["day"], "value": temps[m["day"]]["value"],
               "vtime": temps[m["day"]]["vtime"]} for m in marked if m["day"] in temps]

    # ---- what he did, against the run length: an observation with its n ----
    def runs_where(tag):
        ls = [r["length"] for r in runs
              if any(tag in (x["coped"]) for x in marked if x["day"] in r["days"])]
        return {"n": len(ls), "mean_len": (round(sum(ls) / float(len(ls)), 1) if ls else None)}
    coped_runs = {"kept_moving": runs_where(DOWN_MOVED), "rested": runs_where(DOWN_RESTED),
                  "note": "An observation over the runs marked so far, not a recommendation."}

    return jsonify(ok=True, days=days_n, since=since, link=linked,
                   marked=marked, runs=runs, months=months, pairs=pairs,
                   components=[{"name": k, "n": v} for k, v in comps],
                   pairs_cooccur=[{"pair": k, "n": v} for k, v in cpairs],
                   temps={"with": len(with_t), "of": len(marked), "values": with_t},
                   coped_runs=coped_runs,
                   err="" if linked else "FitLog is not answering, so steps and "
                                          "sleep are not available for these days.")


@app.route("/api/episode/eased/<int:eid>", methods=["POST"])
@login_required
def api_episode_eased(eid):
    """One tap, hours later. Duration is measured from etime to now instead of
    being guessed from a bucket chosen while it still hurts."""
    r = db().execute("SELECT id, day, etime, duration FROM episodes WHERE id=?",
                     (eid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="Entry not found."), 404
    start = _valid_hm(r["etime"])
    if not start:
        return jsonify(ok=False, err="That entry has no start time."), 400
    try:
        t0 = datetime.strptime((r["day"] or today()) + " " + start, "%Y-%m-%d %H:%M")
    except ValueError:
        return jsonify(ok=False, err="That entry has no usable time."), 400
    mins = int(round((datetime.now() - t0).total_seconds() / 60.0))
    if mins < 0:
        return jsonify(ok=False, err="That entry starts in the future."), 400
    txt = _dur_text(mins)
    db().execute("UPDATE episodes SET duration=? WHERE id=?", (txt, eid))
    db().commit()
    return jsonify(ok=True, id=eid, minutes=mins, duration=txt)


# ------------------------------------------------------------------ watch
# GUTLOG_V3130_WATCH -- the watch display. Everything below is read: FitLog
# holds the data, GutLog holds the pain and the operating days, and this is
# the only place the two are put side by side.
#
# No verdict is computed here and none should be added. Every number on this
# screen is an input to a judgement, never a judgement. The footnote says so
# once, quietly.
#
# GUTLOG_V3180_HONEST -- that footnote used to justify itself with a claim
# about the wearer's cardiac rhythm, carried forward from an old
# investigation. A current one says otherwise, so the claim was false as
# written. The reason for showing inputs rather than scores never rested on
# it, so the claim is gone and the reason stands on its own. The clinical
# basis is in the health record on the server; it does not belong in a public
# repository and is deliberately not restated here (CLAUDE.md rule 5d).
WATCH_STRIP = ("steps", "exercise_minutes", "resting_hr", "hrv_ms")
# GUTLOG_V3150_READ -- what KIND of quantity each tile holds, in one place.
#
# cumulative: it accumulates through the day. A part-day total compared with
#   whole-day medians is not a comparison -- it points down every morning by
#   construction. So the headline is the last COMPLETE day and today's running
#   figure is shown separately, with no arrow on it.
# settled: it is a reading rather than a total. Today's stands as soon as it
#   exists, and falls back to yesterday when it does not.
#
# The distinction is in the shape of the quantity. Nothing here looks at the
# clock, and nothing here should start to.
WATCH_KIND = {"steps": "cumulative", "exercise_minutes": "cumulative",
              "load_hours": "cumulative",
              "resting_hr": "settled", "hrv_ms": "settled"}
WATCH_NOTE = ("Shown as inputs, not conclusions. No readiness, recovery or "
              "fitness score is derived from them.")


def _median(xs):
    s = sorted(v for v in xs if v is not None)
    n = len(s)
    if not n:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _direction(value, history, tol=0.05):
    """Against his own trailing median, never against a target.

    Returns (direction, median, n). direction is up / down / level, or None
    when there is nothing to compare against -- fewer than three days of
    history, or no figure today. None means "not enough to say", which is a
    different statement from "level" and must not be drawn as one.
    """
    med = _median(history)
    if value is None or med is None or len(history) < 3 or med == 0:
        return None, med, len(history)
    delta = (value - med) / float(abs(med))
    if delta > tol:
        return "up", med, len(history)
    if delta < -tol:
        return "down", med, len(history)
    return "level", med, len(history)


def _watch_load_by_day(since):
    """Operating hours per day, from GutLog's own activities. Load, never
    exercise: it is not added to exercise minutes here or anywhere."""
    marks = ",".join("?" for _ in LOAD_KINDS)
    rows = db().execute(
        "SELECT day, SUM(minutes) AS m FROM activities "
        "WHERE kind IN (" + marks + ") AND day>=? GROUP BY day",
        tuple(LOAD_KINDS) + (since,)).fetchall()
    return dict((r["day"], float(r["m"] or 0)) for r in rows)


@app.route("/api/watch")
@login_required
def api_watch():
    try:
        days = max(7, min(30, int(request.args.get("days") or 14)))
    except (TypeError, ValueError):
        days = 14
    tday = today()
    yday = (date.today() - timedelta(days=1)).isoformat()
    since = (date.today() - timedelta(days=days - 1)).isoformat()

    feed = _link_get(FITLOG_URL + "/api/feed/watch?days=" + str(days), ttl=60)
    linked = bool((feed or {}).get("ok"))
    daily = (feed or {}).get("daily") or []
    by_date = dict((d.get("date"), d) for d in daily)
    epochs = (feed or {}).get("epochs") or []

    pain = set(r["day"] for r in db().execute(
        "SELECT DISTINCT day FROM episodes WHERE category='pain' AND day>=?",
        (since,)).fetchall())
    load = _watch_load_by_day(since)
    down = set(r["day"] for r in db().execute(
        "SELECT day FROM down_days WHERE day>=?", (since,)).fetchall())

    def epoch_of(d):
        for e in epochs:
            s, en = e.get("date_start") or "", e.get("date_end") or ""
            if s and d < s:
                continue
            if en and d > en:
                continue
            return e.get("label") or ""
        return ""

    row = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        info = by_date.get(d) or {}
        mets = info.get("metrics") or {}
        st = mets.get("steps") or {}
        row.append({"date": d,
                    "steps": st.get("value"),
                    "source": st.get("source") or "",
                    "has_data": bool(info.get("has_data")),
                    "pain": d in pain,
                    "ot": d in load,
                    "down": d in down,
                    "ot_hours": (round(load[d] / 60.0, 1) if d in load else None),
                    "epoch": epoch_of(d)})

    # GUTLOG_V3140_FALLBACK -- one series per tile, the watch metrics from
    # the feed and standing load from GutLog's own rows, so both go through
    # the same fallback and the same median.
    series = {}
    for k in WATCH_STRIP:
        s = {}
        for r in daily:
            m = (r.get("metrics") or {}).get(k)
            if m and m.get("value") is not None:
                s[r.get("date")] = m
        series[k] = s
    series["load_hours"] = dict(
        (d, {"value": round(v / 60.0, 1), "source": "gutlog"})
        for d, v in load.items())

    strip = {}
    for k in WATCH_STRIP + ("load_hours",):
        s = series.get(k) or {}
        kind = WATCH_KIND.get(k, "settled")
        if kind == "cumulative":
            # The headline is the last COMPLETE day. Today is still running,
            # so it cannot be set against whole-day medians without pointing
            # down every morning; it is reported separately below.
            cands = [d2 for d2 in s if d2 < tday]
            day = max(cands) if cands else None
        else:
            # A reading, not a total: today's is valid the moment it exists.
            # Falling back one day is always labelled, never silent -- the
            # phone syncs after the hour this screen is actually read.
            day = None
            for cand in (tday, yday):
                if s.get(cand) and s[cand].get("value") is not None:
                    day = cand
                    break
        cur = s.get(day) or {}
        v = float(cur["value"]) if cur.get("value") is not None else None
        # The median excludes the day being shown. Leave it in and the figure
        # is compared against a median it is itself inside, which on a
        # fallback day reads "level" every time.
        hist = [float(x["value"]) for d2, x in s.items()
                if d2 != day and x and x.get("value") is not None]
        d_, med, n = _direction(v, hist)
        run = None
        if kind == "cumulative":
            t = s.get(tday) or {}
            if t.get("value") is not None:
                run = {"value": float(t["value"]), "day": tday,
                       "source": t.get("source") or ""}
        # GUTLOG_V3180_HONEST -- a tile with no figure, no median and nothing
        # running today has nothing to say, and it said it: a label with an
        # empty value under it, which reads as a fault in the app rather than
        # as an absence of data. It is not sent at all. The decision is made
        # once, here, rather than in the drawing code, so the card can go on
        # drawing whatever it is handed.
        if v is None and med is None and run is None:
            continue
        strip[k] = {"kind": kind, "value": v, "source": cur.get("source") or "",
                    "day": day or "", "stale": bool(day) and day != tday,
                    "dir": d_, "median": med, "n": n, "today": run}

    wk = [w for w in ((feed or {}).get("workouts") or []) if w.get("date", "") >= since]
    wk.sort(key=lambda w: (w.get("date", ""), w.get("start_hm", "")), reverse=True)
    return jsonify(ok=True, day=tday, days=days, since=since, link=linked,
                   strip=strip, row=row, workouts=wk[:8], epochs=epochs,
                   note=WATCH_NOTE,
                   err="" if linked else "FitLog is not answering, so the watch "
                                          "figures are not available right now.")


# ------------------------------------------------------------------ records
# GUTLOG_V380_RECORDS -- the health record inside GutLog: documents, lab
# trends, a readable summary and the investigation plan. Clinical content
# (records_profile.local.json, the imported documents and values) lives only
# on this server; the code carries none of it.
PROFILE_FILE = os.path.join(BASE, "records_profile.local.json")
REC_KEY_TESTS = ["ESR (Erythrocyte Sedimentation Rate)", "C-Reactive Protein (Quantitative)",
                 "FAECAL CALPROTECTIN (stool)", "Haemoglobin", "Platelet Count", "MPV", "Serum Sodium",
                 "Serum Ionic Calcium", "HbA1c (Glycosylated Haemoglobin)", "Serum Creatinine",
                 "SGOT (AST)", "SGPT (ALT)", "Total Cholesterol", "LDL Cholesterol", "Triglycerides",
                 "25-Hydroxy Vitamin D", "Vitamin B12 (Total)", "TSH (ultrasensitive)"]


def rec_profile():
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as fh:
            p = json.load(fh)
        return p if isinstance(p, dict) else {}
    except (OSError, ValueError):
        return {}


@app.route("/api/records/docs")
@login_required
def api_rec_docs():
    rows = [dict(r) for r in db().execute(
        "SELECT id, day, kind, title, source, finding, status, (stored<>'') AS has_file, "
        "COALESCE(origin,'') AS origin, COALESCE(checked,1) AS checked "
        "FROM rec_docs ORDER BY day DESC, id DESC").fetchall()]
    ups = [{"id": r["id"], "day": r["day"], "kind": "Uploaded", "title": r["label"],
            "source": r["ftype"], "finding": _upload_note(r),
            "status": "inbox", "has_file": 1, "vault": True, "ocr": r["ocr_status"] or ""}
           for r in db().execute("SELECT id, day, ftype, label, ocr_status, ocr_note FROM files WHERE COALESCE(sha,'')='' OR sha NOT IN "
                             "(SELECT sha FROM rec_docs WHERE sha IS NOT NULL) ORDER BY day DESC, id DESC").fetchall()]
    return jsonify(docs=rows, uploads=ups)


@app.route("/rec/doc/<int:did>")
@login_required
def rec_doc_file(did):
    r = db().execute("SELECT stored, orig FROM rec_docs WHERE id=?", (did,)).fetchone()
    if not r or not r["stored"]:
        abort(404)
    return send_from_directory(UPLOAD_DIR, r["stored"], download_name=r["orig"] or r["stored"])


def _rec_series(test):
    return [dict(r) for r in db().execute(
        "SELECT day, value, num, unit, ref, flag, lab, COALESCE(origin,'') AS origin FROM rec_labs "
        "WHERE test=? ORDER BY day, id",
        (test,)).fetchall()]


@app.route("/api/records/labs")
@login_required
def api_rec_labs():
    t = request.args.get("test")
    if t:
        return jsonify(test=t, series=_rec_series(t))
    key = rec_profile().get("key_tests") or REC_KEY_TESTS
    out = []
    for r in db().execute(
            "SELECT test, section, COUNT(*) AS n, MAX(day) AS last FROM rec_labs "
            "GROUP BY test ORDER BY section, test").fetchall():
        s = _rec_series(r["test"])
        last = s[-1] if s else {}
        out.append({"test": r["test"], "section": r["section"], "n": r["n"], "key": r["test"] in key,
                    "last_day": last.get("day"), "last_value": last.get("value"), "last_flag": last.get("flag"),
                    "unit": last.get("unit"), "ref": last.get("ref"),
                    "points": [[x["day"], x["num"], x["flag"]] for x in s if x["num"] is not None]})
    out.sort(key=lambda x: (not x["key"], key.index(x["test"]) if x["key"] else 0, x["section"], x["test"]))
    return jsonify(tests=out)


@app.route("/api/records/summary")
@login_required
def api_rec_summary():
    p = rec_profile()
    tday = today()
    meds = [dict(r) for r in db().execute(
        "SELECT p.name, COALESCE(p.molecule,'') AS molecule, COALESCE(ms.strength,'') AS strength, "
        "s.slot, s.dose_text FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "
        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE s.valid_from<=? "
        "AND (s.valid_to='' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",
        (tday, tday)).fetchall()]
    since = (date.today() - timedelta(days=30)).isoformat()
    prn = [dict(r) for r in db().execute(
        "SELECT medicine AS name, COUNT(*) AS n FROM doses WHERE day>=? AND status='EXTRA' "
        "GROUP BY medicine ORDER BY n DESC", (since,)).fetchall()]
    vit = [dict(r) for r in db().execute(
        "SELECT day, vtime, sys, dia, pulse, weight, temp FROM vitals ORDER BY day DESC, vtime DESC "
        "LIMIT 6").fetchall()]
    key = p.get("key_tests") or REC_KEY_TESTS
    labs = []
    for t in key:
        s = _rec_series(t)
        if s:
            labs.append({"test": t, "day": s[-1]["day"], "value": s[-1]["value"], "flag": s[-1]["flag"],
                         "unit": s[-1]["unit"], "ref": s[-1]["ref"]})
    plan = [dict(r) for r in db().execute("SELECT status FROM rec_plan").fetchall()]
    ndocs = db().execute("SELECT COUNT(*) AS n FROM rec_docs").fetchone()["n"]
    master = db().execute("SELECT id, title FROM rec_docs WHERE kind='Summary' ORDER BY day DESC, id DESC "
                          "LIMIT 3").fetchall()
    return jsonify(profile={k: p.get(k) for k in ("updated", "problems", "resolved", "precautions",
                                                  "missing", "note")},
                   meds=meds, prn=prn, vitals=vit, labs=labs, docs=ndocs,
                   plan={"total": len(plan), "done": sum(1 for x in plan if x["status"] == "done")},
                   unchecked=db().execute("SELECT COUNT(*) AS n FROM rec_docs WHERE "
                                          "origin='auto' AND checked=0").fetchone()["n"],
                   master=[dict(r) for r in master])


@app.route("/api/records/plan")
@login_required
def api_rec_plan():
    return jsonify(items=[dict(r) for r in db().execute(
        "SELECT id, pos, test, why, timing, status, done_day, note FROM rec_plan ORDER BY pos, id").fetchall()])


@app.route("/api/records/plan/<int:pid>", methods=["POST"])
@login_required
def api_rec_plan_set(pid):
    d = J()
    st = d.get("status")
    if st not in ("planned", "done"):
        return jsonify(ok=False, err="Bad status."), 400
    day = _valid_day(d.get("done_day") or today()) if st == "done" else ""
    if st == "done" and not day:
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    note = (d.get("note") or "")[:300]
    cur = db().execute("UPDATE rec_plan SET status=?, done_day=?, note=? WHERE id=?", (st, day or "", note, pid))
    db().commit()
    if not cur.rowcount:
        return jsonify(ok=False, err="Not found."), 404
    return jsonify(ok=True)


@app.route("/api/feed/profile")
@feed_required
def api_feed_profile():
    """Condition codes only - never text - for RxGuard's checks."""
    codes = [c for c in (rec_profile().get("conditions") or []) if isinstance(c, str) and
             re.match(r"^[a-z_]{3,40}$", c)]
    return jsonify(ok=True, app="gutlog", conditions=codes)


# ------------------------------------------------------------------ scanner
# GUTLOG_V390_SCAN -- the clinic's document scanner (scanner_widget v2.3, the
# same file the Asset Register and finance screens use) inside GutLog. Every
# upload, scanned or chosen, also lands in uploads/inbox/ under a readable
# name, so a batch can be taken to the PC for processing in one drag.
import hashlib
import shutil
SCANNER_JS = os.path.join(BASE, "scanner_widget.js")
INBOX_DIR = os.path.join(UPLOAD_DIR, "inbox")


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _after_upload(stored):
    """Fingerprint the new file and drop a readable copy into the inbox.
    Never fails the upload."""
    try:
        r = db().execute("SELECT id, day, label FROM files WHERE stored=?", (stored,)).fetchone()
        if not r:
            return
        src = os.path.join(UPLOAD_DIR, stored)
        db().execute("UPDATE files SET sha=? WHERE id=?", (_sha_file(src), r["id"]))
        db().commit()
        os.makedirs(INBOX_DIR, exist_ok=True)
        base = secure_filename(os.path.splitext(r["label"] or "")[0])[:60] or "report"
        shutil.copy2(src, os.path.join(INBOX_DIR, "%s_%s_%d%s" % (r["day"], base, r["id"],
                                                                   os.path.splitext(stored)[1])))
    except Exception:
        pass


@app.route("/scanner_widget.js")
@login_required
def scanner_widget_js():
    if not os.path.exists(SCANNER_JS):
        abort(404)
    with open(SCANNER_JS, "rb") as fh:
        body = fh.read()
    return Response(body, mimetype="application/javascript", headers={"Cache-Control": "no-cache"})


SCAN_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scan a report - GutLog</title>
<script>(function(){ try{ var t=localStorage.getItem('gl_theme');if(t==='dark'||t==='light'){ document.documentElement.setAttribute('data-theme',t); } }catch(e){ } })();</script>
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;margin:0;background:#F3F6F5;color:#1B2B28}
header{background:#0F6B5C;color:#fff;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:9}
header a{color:#fff;text-decoration:none;font-weight:700;padding:6px 10px;border:1.5px solid rgba(255,255,255,.7);border-radius:9px}
main{padding:12px 16px;max-width:720px;margin:0 auto}
.card{background:#fff;border-radius:14px;padding:14px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.row{display:flex;gap:10px;flex-wrap:wrap}
label.f{flex:1;min-width:140px;font-size:13px;color:#5B6B67;font-weight:700}
label.f select,label.f input{display:block;width:100%;margin-top:4px;font-size:16px;padding:10px;border:1px solid #CFD8D5;border-radius:10px;box-sizing:border-box;background:#fff;color:#1B2B28}
#scanroot .btn,#scanroot button{background:#0F6B5C;color:#fff;border:0}
.muted{color:#5B6B67;font-size:13px}

/* GUTLOG_V3160_DARK -- dark mode, selected and measured.
   Tokens chosen against the dark surface, not flipped from light.
   94 text/background pairs measured across the four pages: worst
   in dark 6.24:1, worst in light 4.48:1 (large text, needs 3:1).
   Marker set re-stepped for dark and validated: worst all-pairs
   deutan delta-E 11.2, tritan 15.9, normal-vision 18.5, all >= 3:1. */
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) body{background:var(--bg);color:var(--ink)}
:root:not([data-theme="light"]) .card{background:var(--card);border-color:var(--line)}
:root:not([data-theme="light"]) .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root:not([data-theme="light"]) input,:root:not([data-theme="light"]) select,:root:not([data-theme="light"]) textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) ::placeholder{color:var(--muted);opacity:1}
:root:not([data-theme="light"]) .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) .btn.ghost,:root:not([data-theme="light"]) .vtm .btn.sk,:root:not([data-theme="light"]) .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root:not([data-theme="light"]) .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root:not([data-theme="light"]) button.warn{background:var(--card)}
:root:not([data-theme="light"]) .addbtn{border-color:#356158}
:root:not([data-theme="light"]) .varpick .vb button{background:var(--card);color:var(--ink)}
:root:not([data-theme="light"]) .btn.primary,:root:not([data-theme="light"]) .chip.sel,:root:not([data-theme="light"]) .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) .chip.just,:root:not([data-theme="light"]) .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root:not([data-theme="light"]) .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root:not([data-theme="light"]) .seg{background:var(--chip)}
:root:not([data-theme="light"]) .seg button.sel{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) .doserow,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row{background:var(--card)}
:root:not([data-theme="light"]) #tab-files .rs-card .btn.tiny,:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny,:root:not([data-theme="light"]) #rsPrint,:root:not([data-theme="light"]) #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) nav{background:var(--card)}
:root:not([data-theme="light"]) nav button.sel i{background:#2A4A42}
:root:not([data-theme="light"]) .toast{color:#0E1513}
:root:not([data-theme="light"]) .varpick,:root:not([data-theme="light"]) .ptile.open{background:#142623}
:root:not([data-theme="light"]) .ptile{background:var(--chip)}
:root:not([data-theme="light"]) .ptile.pain.hero{border-color:#356158}
:root:not([data-theme="light"]) .msg.ok{background:#16261A;border-color:#274A2C}
:root:not([data-theme="light"]) .msg.err{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .doserow.done{background:#132016;border-color:#274A2C}
:root:not([data-theme="light"]) .doserow.skip{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .b-ok{background:#16261A}
:root:not([data-theme="light"]) .b-bad,:root:not([data-theme="light"]) .chip.trigger,:root:not([data-theme="light"]) .tag.k-sym{background:#2E1A18}
:root:not([data-theme="light"]) .b-cmf{background:#221B33}
:root:not([data-theme="light"]) .bk .rm{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .tag.k-dose{background:#142422}
:root:not([data-theme="light"]) .tag.k-extra,:root:not([data-theme="light"]) .tag.k-load{background:#221B33;color:var(--hip)}
:root:not([data-theme="light"]) .tag.k-skip{background:#262B29;color:#B3BEBA}
:root:not([data-theme="light"]) .tag.k-bp{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .tag.k-meal{background:#16223A;color:#8FB4E8}
:root:not([data-theme="light"]) .tag.k-act{background:#16261A;color:var(--ok)}
:root:not([data-theme="light"]) .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root:not([data-theme="light"]) .strow.lv-red{background:#241614;border-color:#4A2622}
:root:not([data-theme="light"]) .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .strow.lv-amber .sq{color:var(--amber)}
:root:not([data-theme="light"]) .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root:not([data-theme="light"]) .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rs-hi{color:var(--err)}
:root:not([data-theme="light"]) .rs-auto{background:#241D10}
:root:not([data-theme="light"]) .rd-auto{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rd-row.inbox{border-left-color:var(--mkep)}
:root:not([data-theme="light"]) .ptile.pain .chip.rad{border-color:#4C3C18}
:root:not([data-theme="light"]) .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root:not([data-theme="light"]) .meter i{background:var(--card)}
:root:not([data-theme="light"]) .wkcol.nodata .bar,:root:not([data-theme="light"]) .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root:not([data-theme="light"]) .mark,:root:not([data-theme="light"]) form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) header{background:#0B4F4F}
:root:not([data-theme="light"]) #scanroot .btn,:root:not([data-theme="light"]) #scanroot button{background:#0B4F4F;color:#fff}
:root:not([data-theme="light"]) .muted,:root:not([data-theme="light"]) label.f{color:var(--muted)}
:root:not([data-theme="light"]) svg text{fill:var(--muted)}
:root:not([data-theme="light"]) .ring svg text{fill:inherit}
}
:root[data-theme="dark"]{color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] body{background:var(--bg);color:var(--ink)}
:root[data-theme="dark"] .card{background:var(--card);border-color:var(--line)}
:root[data-theme="dark"] .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root[data-theme="dark"] input,:root[data-theme="dark"] select,:root[data-theme="dark"] textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] ::placeholder{color:var(--muted);opacity:1}
:root[data-theme="dark"] .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] .btn.ghost,:root[data-theme="dark"] .vtm .btn.sk,:root[data-theme="dark"] .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root[data-theme="dark"] .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root[data-theme="dark"] button.warn{background:var(--card)}
:root[data-theme="dark"] .addbtn{border-color:#356158}
:root[data-theme="dark"] .varpick .vb button{background:var(--card);color:var(--ink)}
:root[data-theme="dark"] .btn.primary,:root[data-theme="dark"] .chip.sel,:root[data-theme="dark"] .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root[data-theme="dark"] .chip.just,:root[data-theme="dark"] .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root[data-theme="dark"] .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root[data-theme="dark"] .seg{background:var(--chip)}
:root[data-theme="dark"] .seg button.sel{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] .doserow,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row{background:var(--card)}
:root[data-theme="dark"] #tab-files .rs-card .btn.tiny,:root[data-theme="dark"] #tab-files .rp-row .btn.tiny,:root[data-theme="dark"] #rsPrint,:root[data-theme="dark"] #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root[data-theme="dark"] nav{background:var(--card)}
:root[data-theme="dark"] nav button.sel i{background:#2A4A42}
:root[data-theme="dark"] .toast{color:#0E1513}
:root[data-theme="dark"] .varpick,:root[data-theme="dark"] .ptile.open{background:#142623}
:root[data-theme="dark"] .ptile{background:var(--chip)}
:root[data-theme="dark"] .ptile.pain.hero{border-color:#356158}
:root[data-theme="dark"] .msg.ok{background:#16261A;border-color:#274A2C}
:root[data-theme="dark"] .msg.err{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .doserow.done{background:#132016;border-color:#274A2C}
:root[data-theme="dark"] .doserow.skip{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .b-ok{background:#16261A}
:root[data-theme="dark"] .b-bad,:root[data-theme="dark"] .chip.trigger,:root[data-theme="dark"] .tag.k-sym{background:#2E1A18}
:root[data-theme="dark"] .b-cmf{background:#221B33}
:root[data-theme="dark"] .bk .rm{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .tag.k-dose{background:#142422}
:root[data-theme="dark"] .tag.k-extra,:root[data-theme="dark"] .tag.k-load{background:#221B33;color:var(--hip)}
:root[data-theme="dark"] .tag.k-skip{background:#262B29;color:#B3BEBA}
:root[data-theme="dark"] .tag.k-bp{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .tag.k-meal{background:#16223A;color:#8FB4E8}
:root[data-theme="dark"] .tag.k-act{background:#16261A;color:var(--ok)}
:root[data-theme="dark"] .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root[data-theme="dark"] .strow.lv-red{background:#241614;border-color:#4A2622}
:root[data-theme="dark"] .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .strow.lv-amber .sq{color:var(--amber)}
:root[data-theme="dark"] .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root[data-theme="dark"] .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rs-hi{color:var(--err)}
:root[data-theme="dark"] .rs-auto{background:#241D10}
:root[data-theme="dark"] .rd-auto{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rd-row.inbox{border-left-color:var(--mkep)}
:root[data-theme="dark"] .ptile.pain .chip.rad{border-color:#4C3C18}
:root[data-theme="dark"] .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root[data-theme="dark"] .meter i{background:var(--card)}
:root[data-theme="dark"] .wkcol.nodata .bar,:root[data-theme="dark"] .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root[data-theme="dark"] .mark,:root[data-theme="dark"] form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] header{background:#0B4F4F}
:root[data-theme="dark"] #scanroot .btn,:root[data-theme="dark"] #scanroot button{background:#0B4F4F;color:#fff}
:root[data-theme="dark"] .muted,:root[data-theme="dark"] label.f{color:var(--muted)}
:root[data-theme="dark"] svg text{fill:var(--muted)}
:root[data-theme="dark"] .ring svg text{fill:inherit}
@media print{
:root:not([data-theme="light"]),:root[data-theme="dark"]{color-scheme:light}
:root:not([data-theme="light"]) body{background:#fff;color:#000}
:root:not([data-theme="light"]) .card,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row,:root:not([data-theme="light"]) .doserow{background:#fff;color:#000;border-color:#ccc}
:root[data-theme="dark"] body{background:#fff;color:#000}
:root[data-theme="dark"] .card,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row,:root[data-theme="dark"] .doserow{background:#fff;color:#000;border-color:#ccc}
}
</style></head><body>
<header><b>Scan a report</b><a href="/?open=records">Done</a></header>
<main>
<div class="card"><div class="row">
<label class="f">Type<select id="s_type"><option>Lab report</option><option>Imaging report</option>
<option>Prescription</option><option>Consultation note</option><option>Discharge summary</option><option>Other</option></select></label>
<label class="f">Report date<input type="date" id="s_day" value="__DAY__" max="__DAY__"></label>
</div><p class="muted">Each saved PDF goes to Records, Reports, where it waits to be processed into your record.
Batch mode saves every page as its own file.</p></div>
<div class="card"><div id="scanroot"></div></div>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<script>
window.SCANNER_CONFIG = {title: "Scan a report", uploadUrl: "/api/upload", fileField: "file",
  uploadFields: {ftype: "Lab report", day: "__DAY__"}, nameBase: "Lab_report", backUrl: "/?open=records",
  allowIdCard: false, allowBatch: true,
  /* v3.11.0 -- a report is not a bill.
     captureMax/warpMax  1400/1600 put an A4 page at ~110dpi, which is where an
                         OCR starts guessing at 8pt print. 2600 is ~220dpi.
     wholePageFirst      the whole page is what a report scan is FOR, so it is
                         the big button and the crop is the small one; and when
                         the edges are not obvious the whole photo is kept
                         instead of an 8% inset that cuts into the print. */
  captureMax: 2600, warpMax: 2600, jpegQuality: 0.92, wholePageFirst: true};
(function () {
  var C = window.SCANNER_CONFIG;
  document.getElementById("s_type").onchange = function () {
    C.uploadFields.ftype = this.value; C.nameBase = this.value.replace(/\s+/g, "_");
  };
  document.getElementById("s_day").onchange = function () { C.uploadFields.day = this.value || "__DAY__"; };
})();
</script>
<script src="/scanner_widget.js?v=__V__"></script>
</body></html>
"""


@app.route("/scan")
@login_required
def scan_page():
    try:
        v = str(int(os.path.getmtime(SCANNER_JS)))
    except OSError:
        v = "0"
    html = SCAN_PAGE.replace("__DAY__", today()).replace("__V__", v)
    return Response(html, mimetype="text/html")


PLANS_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Plans - GutLog</title>
<style>
:root{--bg:#F4F6F5;--card:#fff;--ink:#17201D;--muted:#55645E;--line:#DCE4E0;
      --teal:#2E7D6B;--teal-d:#1F5F51;--warn:#A32E22;--chip:#EDF3F0}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;--line:#2C3733;
  --teal:#5FB49C;--teal-d:#8ED3BD;--warn:#F09284;--chip:#243029}}
:root[data-theme="dark"]{--bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;
  --line:#2C3733;--teal:#5FB49C;--teal-d:#8ED3BD;--warn:#F09284;--chip:#243029}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;
       background:var(--card);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;font-weight:700;flex:1}
header a{color:var(--teal-d);text-decoration:none;font-size:13px;font-weight:600}
main{padding:16px;max-width:760px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:14px;margin:0 0 12px}
.ttl{font-weight:700;margin:0 0 3px}
.sub{color:var(--muted);font-size:13px;margin:0 0 10px}
.badge{display:inline-block;font-size:11.5px;font-weight:700;padding:2px 8px;
       border-radius:999px;background:var(--chip);color:var(--teal-d);margin-left:6px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
button,.btn{font:inherit;font-weight:600;border-radius:9px;padding:9px 14px;
       border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.go,.btn.go{background:var(--teal);border-color:var(--teal);color:#fff}
a.btn{text-decoration:none;display:inline-block}
label{display:block;font-size:12.5px;color:var(--muted);font-weight:600;margin:10px 0 4px}
input[type=text],select,input[type=file]{width:100%;font:inherit;padding:9px;
       border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--ink)}
.dmy{display:flex;gap:6px}
.dmy select{flex:1;min-width:0}
.msg{margin-top:10px;font-size:13.5px;min-height:1.2em}
.msg.bad{color:var(--warn)}
.empty{color:var(--muted)}
.hint{color:var(--muted);font-size:12.5px;margin-top:8px}
</style></head><body>
<header><h1>Plans</h1><a href="/">Back to GutLog</a></header>
<main>
<div id="list">__ROWS__</div>

<div class="card">
  <p class="ttl">Add a plan</p>
  <label for="t">Title</label>
  <input type="text" id="t" maxlength="200" placeholder="What the plan is">
  <label>First considered</label>
  <div class="dmy">
    <select id="dd" aria-label="Day"></select>
    <select id="mm" aria-label="Month"></select>
    <select id="yy" aria-label="Year"></select>
  </div>
  <label for="st">Status</label>
  <select id="st"><option>Draft</option><option>Active</option><option>Closed</option></select>
  <label for="f">PDF</label>
  <input type="file" id="f" accept="application/pdf">
  <div class="row"><button class="go" id="add">Add plan</button></div>
  <p class="hint">PDF only, up to 20 MB. The file stays behind your login.</p>
  <p class="msg" id="msg"></p>
</div>
</main>
<script>
var MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
var TODAY="__TODAY__";
function $(q){return document.querySelector(q);}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;
  if(x!==undefined)e.textContent=x;return e;}
function pad(n){return (n<10?"0":"")+n;}

/* Three lists, not a native date box: on the folded phone the system dialog
   hides its own confirm button. Same lesson as the time lists in v3.27.0. */
function fillDate(){
  var d=$("#dd"),m=$("#mm"),y=$("#yy"),i;
  for(i=1;i<=31;i++)d.add(new Option(pad(i),pad(i)));
  for(i=0;i<12;i++)m.add(new Option(MON[i],pad(i+1)));
  var ty=parseInt(TODAY.slice(0,4),10);
  for(i=ty+1;i>=ty-6;i--)y.add(new Option(String(i),String(i)));
  d.value=TODAY.slice(8,10);m.value=TODAY.slice(5,7);y.value=TODAY.slice(0,4);
}
function pickedDate(){return $("#yy").value+"-"+$("#mm").value+"-"+$("#dd").value;}
function say(t,bad){var m=$("#msg");m.textContent=t;m.className="msg"+(bad?" bad":"");}

/* The list above is already on the page, rendered by the server. Everything
   below only ACTS on it, so the page still reads correctly with no script. */
function share(id,name,title){
  fetch("/plan/"+id+"/file",{credentials:"same-origin"}).then(function(r){
    if(!r.ok)throw new Error("not available");
    return r.blob();
  }).then(function(b){
    var fname=name||("plan-"+id+".pdf");
    var file=new File([b],fname,{type:"application/pdf"});
    if(navigator.canShare&&navigator.canShare({files:[file]})&&navigator.share){
      return navigator.share({files:[file],title:title||fname});
    }
    /* No file sharing here, so hand him the file. There is deliberately no
       public URL to fall back to. */
    var a=document.createElement("a");
    a.href=URL.createObjectURL(b);a.download=fname;document.body.appendChild(a);
    a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},4000);
  }).catch(function(e){
    if(e&&e.name==="AbortError")return;
    say("Could not share that file.",true);
  });
}

function post(pid,body){
  return fetch("/api/plans/"+pid,{method:"POST",credentials:"same-origin",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
    .then(function(r){return r.json();})
    .then(function(j){
      if(!j.ok){say(j.err||"Could not save that.",true);return;}
      location.reload();
    }).catch(function(){say("Could not save that.",true);});
}

function bind(){
  [].forEach.call(document.querySelectorAll(".shr"),function(b){
    b.onclick=function(){share(b.dataset.id,b.dataset.name,b.dataset.title);};});
  [].forEach.call(document.querySelectorAll(".stsel"),function(s){
    s.style.width="auto";
    s.onchange=function(){post(s.dataset.id,{status:s.value});};});
  [].forEach.call(document.querySelectorAll(".rnm"),function(b){
    b.onclick=function(){
      var t=prompt("Title",b.dataset.title);
      if(t===null)return;
      post(b.dataset.id,{title:t});};});
  [].forEach.call(document.querySelectorAll(".arc"),function(b){
    b.onclick=function(){
      if(!confirm("Hide this plan from the list? The file is kept."))return;
      post(b.dataset.id,{archived:1});};});
}

$("#add").onclick=function(){
  var t=$("#t").value.trim();
  if(!t){say("Give the plan a title.",true);return;}
  var fd=new FormData();
  fd.append("title",t);
  fd.append("first_considered",pickedDate());
  fd.append("status",$("#st").value);
  if($("#f").files[0])fd.append("file",$("#f").files[0]);
  say("Saving...");
  fetch("/api/plans",{method:"POST",credentials:"same-origin",body:fd})
    .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(x){
      if(!x.ok||!x.j.ok){say((x.j&&x.j.err)||"Could not save that.",true);return;}
      say("Added.");
      location.reload();
    }).catch(function(){say("Could not save that.",true);});
};

fillDate();bind();
</script></body></html>
"""


NUTRITION_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Nutrition history - GutLog</title>
<style>
:root{--bg:#F4F6F5;--card:#fff;--ink:#17201D;--muted:#55645E;--line:#DCE4E0;
      --teal:#2E7D6B;--teal-d:#1F5F51;--chip:#EDF3F0;--amber:#8A5A00}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;--line:#2C3733;
  --teal:#5FB49C;--teal-d:#8ED3BD;--chip:#243029;--amber:#E8B86A}}
:root[data-theme="dark"]{--bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;
  --line:#2C3733;--teal:#5FB49C;--teal-d:#8ED3BD;--chip:#243029;--amber:#E8B86A}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;flex-wrap:wrap;
       background:var(--card);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;font-weight:700;flex:1;white-space:nowrap}
header a{color:var(--teal-d);text-decoration:none;font-size:13px;font-weight:600}
main{padding:14px;max-width:760px;margin:0 auto}
.lead{color:var(--muted);font-size:13px;margin:0 0 12px}
.nrow{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;
      background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:11px 13px;margin:0 0 8px;text-decoration:none;color:var(--ink)}
.nd{font-weight:700;flex:1 0 auto;min-width:110px}
.nv{font-size:13.5px;color:var(--muted);white-space:nowrap}
.nv.off{color:var(--muted);font-style:italic}
.nrow.off{background:transparent;border-style:dashed}
.tag{font-style:normal;font-size:11px;font-weight:700;margin-left:7px;
     padding:1px 7px;border-radius:999px;background:var(--chip);color:var(--amber)}
.btn{display:inline-block;font-weight:600;border-radius:9px;padding:9px 14px;
     border:1px solid var(--line);background:var(--card);color:var(--ink);
     text-decoration:none}
.foot{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.hint{color:var(--muted);font-size:12.5px;margin:10px 2px 0}
</style></head><body>
<header><h1>Nutrition history</h1><a href="/?open=meals">Meals</a><a href="/">GutLog</a></header>
<main>
<p class="lead">__LEAD__ Tap a day to open it.</p>
__ROWS__
<div class="foot">__MORE__</div>
<p class="hint">Values are estimated, as they are everywhere else in the app.
Targets are your own plan's. A day with nothing logged says so rather than
showing zero, and a day with fewer meals than usual is marked partial.</p>
</main></body></html>
"""


# ------------------------------------------------------------ mirror state
# GUTLOG_V3300_MIRRORSTALE. The Health Mirror writes last_success.json ONLY
# after an upload that worked. If that stamp is missing or old, the copy the
# Claude app reads on his phone is not today's -- and a silently stale mirror
# is worse than none, because it answers instead of asking.
MIRROR_STAMP = os.environ.get("GUTLOG_MIRROR_STAMP",
                              "/root/health_mirror/last_success.json")
MIRROR_STALE_HOURS = 36


def mirror_state():
    """{ok, hours, text}. Never reads the record -- one timestamp only."""
    try:
        with open(MIRROR_STAMP, encoding="utf-8") as fh:
            j = json.load(fh)
        when = datetime.fromtimestamp(float(j.get("epoch") or 0))
    except (OSError, ValueError, TypeError):
        return {"ok": False, "hours": None, "never": True,
                "text": "The health mirror has never finished. Claude on your "
                        "phone is reading nothing, or something older."}
    hours = (datetime.now() - when).total_seconds() / 3600.0
    if hours <= MIRROR_STALE_HOURS:
        return {"ok": True, "hours": round(hours, 1), "never": False,
                "text": "Mirror updated %s." % when.strftime("%d %b, %H:%M")}
    return {"ok": False, "hours": round(hours, 1), "never": False,
            "text": "The health mirror last updated %s, about %d hours ago. "
                    "Anything Claude tells you from it is that old."
                    % (when.strftime("%d %b, %H:%M"), int(hours))}


@app.route("/api/mirror")
@login_required
def api_mirror():
    return jsonify(mirror_state())


# -------------------------------------------------------------- nutrition
# GUTLOG_V3290_NUTRITION. One place computes a day's totals. The day card on
# the Meals tab, the history page and the API all read nut_day(), so they
# cannot disagree -- there is no second calculation left to drift. The meals
# table already holds per-meal protein/kcal/fibre worked out on the server at
# insert, so a day is a plain SUM over that day.
NUT_FIBRE_TARGET = 30.0


def nut_targets():
    """(protein, fibre) from his own plan, with the standing fallbacks."""
    fib = NUT_FIBRE_TARGET
    try:
        v = float(((_plan_cfg() or {}).get("targets") or {}).get("fibre"))
        if v > 0:
            fib = v
    except (TypeError, ValueError):
        pass
    return _protein_target(), fib


def nut_usual_meals():
    """How many meals a full day usually has -- the plan's main meals when it
    says so, else 3. Used ONLY to label a thin day, never to score one."""
    mm = (_plan_cfg() or {}).get("main_meals")
    if isinstance(mm, list) and mm:
        return len(mm)
    return 3


def nut_day(day):
    """Totals for one IST day. logged=False means nothing was recorded, which
    is not the same as a day of no intake, and the page must not print 0."""
    r = db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(protein),0) AS p, "
        "COALESCE(SUM(kcal),0) AS k, COALESCE(SUM(fibre),0) AS f, "
        "COALESCE(SUM(fscore),0) AS fs FROM meals WHERE day=?", (day,)).fetchone()
    n = int(r["n"] or 0)
    usual = nut_usual_meals()
    return {"day": day, "date_text": plan_dmy(day), "meals": n,
            "protein": round(float(r["p"] or 0), 1),
            "kcal": int(round(float(r["k"] or 0))),
            "fibre": round(float(r["f"] or 0), 1),
            "fscore": round(float(r["fs"] or 0), 2),
            "logged": n > 0, "partial": 0 < n < usual, "usual_meals": usual}


def nut_history(days):
    """Newest first, every calendar day in the window -- including the ones
    with nothing on them, which are the point of looking."""
    days = max(1, min(120, int(days)))
    end = date.today()
    return [nut_day((end - timedelta(days=i)).isoformat()) for i in range(days)]


@app.route("/api/nutrition/day/<day>")
@login_required
def api_nut_day(day):
    t = nut_day(day)
    t["rows"] = [_meal_row_json(r) for r in db().execute(
        "SELECT * FROM meals WHERE day=? ORDER BY mtime, id", (day,)).fetchall()]
    prot, fib = nut_targets()
    t["protein_target"] = prot
    t["fibre_target"] = fib
    t["today"] = today()
    return jsonify(t)


@app.route("/api/nutrition/history")
@login_required
def api_nut_history():
    try:
        n = int(request.args.get("days") or 14)
    except ValueError:
        n = 14
    prot, fib = nut_targets()
    return jsonify(days=nut_history(n), protein_target=prot, fibre_target=fib)


def nut_history_html(rows, prot, fib):
    out = []
    for d in rows:
        cls = "nrow" + ("" if d["logged"] else " off")
        out.append('<a class="%s" href="/?open=meals&amp;day=%s">'
                   % (cls, plan_esc(d["day"])))
        out.append('<span class="nd">' + plan_esc(d["date_text"]))
        if d["partial"]:
            out.append('<i class="tag part">partial</i>')
        out.append('</span>')
        if not d["logged"]:
            out.append('<span class="nv off">not logged</span>')
        else:
            out.append('<span class="nv">%d kcal</span>' % d["kcal"])
            out.append('<span class="nv">%s of %s g protein</span>'
                       % (_nut_num(d["protein"]), _nut_num(prot)))
            out.append('<span class="nv">%s of %s g fibre</span>'
                       % (_nut_num(d["fibre"]), _nut_num(fib)))
            out.append('<span class="nv">%d meal%s</span>'
                       % (d["meals"], "" if d["meals"] == 1 else "s"))
        out.append('</a>')
    return "".join(out)


def _nut_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return plan_esc(v)
    return str(int(f)) if f == int(f) else ("%.1f" % f)


@app.route("/nutrition")
@login_required
def nutrition_page():
    try:
        n = int(request.args.get("days") or 14)
    except ValueError:
        n = 14
    n = 30 if n > 14 else 14
    prot, fib = nut_targets()
    rows = nut_history(n)
    other = 30 if n == 14 else 14
    more = ('<a class="btn" href="/nutrition?days=%d">Show %d days</a>' % (other, other))
    logged = len([d for d in rows if d["logged"]])
    lead = ("%d of the last %d days have meals logged." % (logged, n))
    html = (NUTRITION_PAGE.replace("__ROWS__", nut_history_html(rows, prot, fib))
            .replace("__MORE__", more).replace("__LEAD__", plan_esc(lead))
            .replace("__N__", str(n)))
    return Response(html, mimetype="text/html")


# ------------------------------------------------------------------ plans
# GUTLOG_V3280_PLANS. A plan is a dated document he can open and share. The
# file never leaves the login: Share hands the BYTES to the system sheet, so
# there is no link to leak and nothing to revoke.
#
# The page below is a standalone string with __TOKENS__ replaced, returned as
# a Response, exactly like /scan. It is NOT rendered through Jinja, so the CSS
# and JS braces in it are safe -- CLAUDE.md 5b is about the main template.
import hashlib as _plan_hashlib

_PLAN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def plan_dmy(iso):
    """2026-09-22 -> 22-Sep-2026. Anything unparseable comes back unchanged."""
    try:
        y, m, d = str(iso).split("-")
        return "%02d-%s-%s" % (int(d), _PLAN_MONTHS[int(m) - 1], y)
    except (ValueError, IndexError, TypeError):
        return str(iso or "")


def plan_rows(include_archived=False):
    sql = ("SELECT p.*, f.id AS file_id, f.original_name, f.bytes "
           "FROM plans p LEFT JOIN plan_files f ON f.id = ("
           "SELECT id FROM plan_files WHERE plan_id = p.id ORDER BY id DESC LIMIT 1) ")
    if not include_archived:
        sql += "WHERE p.archived = 0 "
    sql += "ORDER BY p.first_considered DESC, p.id DESC"
    out = []
    for r in db().execute(sql).fetchall():
        d = dict(r)
        d["first_considered_text"] = plan_dmy(d.get("first_considered"))
        d["has_file"] = bool(d.get("file_id"))
        out.append(d)
    return out


def plan_esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def plan_list_html(rows):
    """The list, rendered on the server. See the patcher docstring: a list
    only the browser can draw is a list no server suite can check."""
    if not rows:
        return '<p class="empty">No plans yet.</p>'
    out = []
    for p in rows:
        pid = int(p["id"])
        out.append('<div class="card">')
        out.append('<p class="ttl">' + plan_esc(p["title"])
                   + '<span class="badge">' + plan_esc(p["status"]) + '</span></p>')
        out.append('<p class="sub">First considered: '
                   + plan_esc(p["first_considered_text"]) + '</p>')
        out.append('<div class="row">')
        if p["has_file"]:
            out.append('<a class="btn go" href="/plan/%d/file" target="_blank" '
                       'rel="noopener">Open</a>' % pid)
            out.append('<button type="button" class="shr" data-id="%d" data-name="%s" '
                       'data-title="%s">Share</button>'
                       % (pid, plan_esc(p.get("original_name") or ""),
                          plan_esc(p["title"])))
        else:
            out.append('<span class="empty">No file on this plan.</span>')
        opts = []
        for s in PLAN_STATUSES:
            sel = " selected" if s == p["status"] else ""
            opts.append("<option" + sel + ">" + s + "</option>")
        out.append('<select class="stsel" aria-label="Status" data-id="%d">%s</select>'
                   % (pid, "".join(opts)))
        out.append('<button type="button" class="rnm" data-id="%d" data-title="%s">'
                   'Rename</button>' % (pid, plan_esc(p["title"])))
        out.append('<button type="button" class="arc" data-id="%d">Archive</button>' % pid)
        out.append('</div></div>')
    return "".join(out)


def plan_is_pdf(fs):
    """The magic bytes, not the extension. Leaves the stream where it found it."""
    if not fs or not fs.filename:
        return False
    pos = fs.stream.tell()
    head = fs.stream.read(5)
    fs.stream.seek(pos)
    return head == b"%PDF-"


def plan_store(plan_id, fs):
    """Save an uploaded PDF against a plan. Returns (ok, message_or_name)."""
    if not plan_is_pdf(fs):
        return False, "That is not a PDF. Only PDF files can be added here."
    cap = PLAN_MAX_MB * 1024 * 1024
    raw = fs.stream.read(cap + 1)
    if len(raw) > cap:
        return False, "That file is larger than %d MB." % PLAN_MAX_MB
    sha = _plan_hashlib.sha256(raw).hexdigest()
    stored = sha + ".pdf"
    path = os.path.join(PLANS_DIR, stored)
    if not os.path.exists(path):
        tmp = path + ".part"
        fh = open(tmp, "wb")
        try:
            fh.write(raw)
        finally:
            fh.close()
        os.replace(tmp, path)
    db().execute("INSERT INTO plan_files(plan_id, stored_name, original_name, "
                 "bytes, sha256, uploaded_at) VALUES(?,?,?,?,?,?)",
                 (plan_id, stored, secure_filename(fs.filename)[:160] or "plan.pdf",
                  len(raw), sha, now_s()))
    db().commit()
    return True, stored


@app.route("/plans")
@login_required
def plans_page():
    arch = request.args.get("archived") == "1"
    html = (PLANS_PAGE.replace("__ROWS__", plan_list_html(plan_rows(include_archived=arch)))
            .replace("__TODAY__", today()))
    return Response(html, mimetype="text/html")


@app.route("/api/plans")
@login_required
def api_plans():
    arch = request.args.get("archived") == "1"
    rows = []
    for d in plan_rows(include_archived=arch):
        rows.append(dict(id=d["id"], title=d["title"], status=d["status"],
                         first_considered=d["first_considered"],
                         first_considered_text=d["first_considered_text"],
                         archived=d["archived"], has_file=d["has_file"],
                         original_name=d.get("original_name") or "",
                         bytes=d.get("bytes") or 0))
    return jsonify(rows)


@app.route("/api/plans", methods=["POST"])
@login_required
def api_plans_add():
    title = (request.form.get("title") or "").strip()
    when = (request.form.get("first_considered") or "").strip() or today()
    status = (request.form.get("status") or "Draft").strip()
    if not title:
        return jsonify(ok=False, err="Give the plan a title."), 400
    if status not in PLAN_STATUSES:
        return jsonify(ok=False, err="Unknown status."), 400
    try:
        datetime.strptime(when, "%Y-%m-%d")
    except ValueError:
        return jsonify(ok=False, err="That date is not a real date."), 400
    fs = request.files.get("file")
    # Refused BEFORE the row is written, so a rejected upload leaves nothing.
    if fs and fs.filename and not plan_is_pdf(fs):
        return jsonify(ok=False,
                       err="That is not a PDF. Only PDF files can be added here."), 400
    cur = db().execute("INSERT INTO plans(title, first_considered, status, archived, "
                       "created_at) VALUES(?,?,?,0,?)",
                       (title[:200], when, status, now_s()))
    pid = cur.lastrowid
    db().commit()
    if fs and fs.filename:
        ok, msg = plan_store(pid, fs)
        if not ok:
            db().execute("DELETE FROM plans WHERE id=?", (pid,))
            db().commit()
            return jsonify(ok=False, err=msg), 400
    return jsonify(ok=True, id=pid)


@app.route("/api/plans/<int:pid>", methods=["POST"])
@login_required
def api_plans_edit(pid):
    r = db().execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="No such plan."), 404
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or r["title"]).strip()
    status = (body.get("status") or r["status"]).strip()
    if not title:
        return jsonify(ok=False, err="A plan needs a title."), 400
    if status not in PLAN_STATUSES:
        return jsonify(ok=False, err="Unknown status."), 400
    arch = r["archived"]
    if "archived" in body:
        arch = 1 if body.get("archived") else 0
    db().execute("UPDATE plans SET title=?, status=?, archived=? WHERE id=?",
                 (title[:200], status, arch, pid))
    db().commit()
    return jsonify(ok=True)


@app.route("/plan/<int:pid>/file")
@login_required
def plan_file(pid):
    r = db().execute("SELECT * FROM plan_files WHERE plan_id=? "
                     "ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if not r:
        abort(404)
    path = os.path.join(PLANS_DIR, r["stored_name"])
    if not os.path.exists(path):
        abort(404)
    fh = open(path, "rb")
    try:
        data = fh.read()
    finally:
        fh.close()
    disp = "attachment" if request.args.get("dl") == "1" else "inline"
    name = (r["original_name"] or "plan.pdf").replace('"', "").replace("\n", "")
    resp = Response(data, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = disp + '; filename="' + name + '"'
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ------------------------------------------------------------------ auto-read
# GUTLOG_V3100_AUTOREAD -- start the report reader the moment a file lands.
import subprocess
import sys as _sys
WORKER = os.path.join(BASE, "records_worker.py")


def _spawn_reader():
    """Start records_worker.py in the background. Live database only (tests
    and scratch copies never reach out); GUTLOG_NOSPAWN=1 disables."""
    if os.environ.get("GUTLOG_NOSPAWN") == "1" or not _links_enabled() or not os.path.exists(WORKER):
        return False
    try:
        logf = open(os.path.join(BASE, "records_worker.log"), "a")
        subprocess.Popen([_sys.executable, WORKER], cwd=BASE, stdout=logf, stderr=logf, start_new_session=True)
        return True
    except Exception:
        return False


def _upload_note(r):
    st, note = (r["ocr_status"] or ""), (r["ocr_note"] or "")
    if st in ("", "reading"):
        return "Being read automatically - it joins your record in a minute or two."
    if st == "retry":
        return "Could not be read yet (%s) - trying again shortly." % note
    if st == "failed":
        return "Could not be read automatically (%s). It stays here as a file." % note
    if st == "skipped":
        return "Kept as a file (%s)." % note
    return "Uploaded by you."


@app.route("/api/records/doc/<int:did>/checked", methods=["POST"])
@login_required
def api_rec_checked(did):
    cur = db().execute("UPDATE rec_docs SET checked=1 WHERE id=?", (did,))
    db().commit()
    if not cur.rowcount:
        return jsonify(ok=False, err="Not found."), 404
    return jsonify(ok=True)


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
        daily=daily, dosecount=dosecount, registry=registry, target=_protein_target())

# ------------------------------------------------------------------ export
@app.route("/export/<table>.csv")
@login_required
def export_csv(table):
    cols = {
        "days": "day,syms,pain,pain_site,bristol,stools,tea,coffee,sleep,walk,treadmill,meditation,notes",
        "meals": "day,mtime,slot,items,protein,kcal,fibre,fscore,notes",
        "doses": "day,dtime,medicine,status,reason,effect,notes",
        "patches": "strength,day_on,time_on,day_off,time_off,notes",
        "episodes": "day,etime,category,etype,side,severity,duration,treatments,radiates,notes",
        "vitals": "day,vtime,sys,dia,pulse,temp,weight,waist,notes",
        "foodtests": "day,food,portion,symptoms,severity,verdict,notes",
        "courses": "drug,start_day,end_day,response,notes",
        "consults": "day,doctor,reason,advice,changes,next_visit",
        "labs": "day,analyte,value",
        "library": "cat,item,portion,protein,kcal,fibre,fodmap,status,tags,fav,note,"
                   "portion_qty,portion_unit,b_protein,b_kcal,b_fibre,weighed_dry,portion_est,"
                   "source,source_date,source_ref",
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
                vals[i] = "; ".join(_export_item(it) for it in json.loads(vals[i] or "[]"))
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
<script>(function(){ try{ var t=localStorage.getItem('gl_theme');if(t==='dark'||t==='light'){ document.documentElement.setAttribute('data-theme',t); } }catch(e){ } })();</script>
<style>
:root{color-scheme:light;--ink:#1D2F33;--teal:#0B6E6E;--bg:#EFF5F2;--card:#fff;--line:#D5E3DD;--muted:#556C69;--err:#B3372A;--amber:#8A5A00}
*{box-sizing:border-box}body{margin:0;color:var(--ink);
background:radial-gradient(1200px 600px at 50% -10%,#DFF0EA,var(--bg));
font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;display:grid;place-items:center;min-height:100vh}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:30px;width:min(92vw,370px);
box-shadow:0 18px 50px rgba(11,110,110,.14)}
.mark{width:52px;height:52px;border-radius:15px;display:grid;place-items:center;font-size:27px;
background:linear-gradient(135deg,#0B6E6E,#12907C);margin:0 0 14px;box-shadow:0 6px 16px rgba(11,110,110,.35)}
h1{font-size:24px;margin:0 0 2px;letter-spacing:-.4px}h1 b{color:var(--teal)}
p{margin:4px 0 18px;color:var(--muted);font-size:14px}
input{width:100%;padding:13px 14px;font-size:16px;border:1.5px solid var(--line);border-radius:12px;margin-bottom:12px}
input:focus{outline:2px solid var(--teal);border-color:var(--teal)}
button{width:100%;padding:14px;font-size:16px;font-weight:700;border:0;border-radius:12px;
background:linear-gradient(135deg,#0B6E6E,#12907C);color:#fff;cursor:pointer}
.err{color:var(--err);font-size:14px;margin:0 0 12px}

/* GUTLOG_V3160_DARK -- dark mode, selected and measured.
   Tokens chosen against the dark surface, not flipped from light.
   94 text/background pairs measured across the four pages: worst
   in dark 6.24:1, worst in light 4.48:1 (large text, needs 3:1).
   Marker set re-stepped for dark and validated: worst all-pairs
   deutan delta-E 11.2, tritan 15.9, normal-vision 18.5, all >= 3:1. */
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) body{background:var(--bg);color:var(--ink)}
:root:not([data-theme="light"]) .card{background:var(--card);border-color:var(--line)}
:root:not([data-theme="light"]) .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root:not([data-theme="light"]) input,:root:not([data-theme="light"]) select,:root:not([data-theme="light"]) textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) ::placeholder{color:var(--muted);opacity:1}
:root:not([data-theme="light"]) .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) .btn.ghost,:root:not([data-theme="light"]) .vtm .btn.sk,:root:not([data-theme="light"]) .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root:not([data-theme="light"]) .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root:not([data-theme="light"]) button.warn{background:var(--card)}
:root:not([data-theme="light"]) .addbtn{border-color:#356158}
:root:not([data-theme="light"]) .varpick .vb button{background:var(--card);color:var(--ink)}
:root:not([data-theme="light"]) .btn.primary,:root:not([data-theme="light"]) .chip.sel,:root:not([data-theme="light"]) .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) .chip.just,:root:not([data-theme="light"]) .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root:not([data-theme="light"]) .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root:not([data-theme="light"]) .seg{background:var(--chip)}
:root:not([data-theme="light"]) .seg button.sel{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) .doserow,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row{background:var(--card)}
:root:not([data-theme="light"]) #tab-files .rs-card .btn.tiny,:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny,:root:not([data-theme="light"]) #rsPrint,:root:not([data-theme="light"]) #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) nav{background:var(--card)}
:root:not([data-theme="light"]) nav button.sel i{background:#2A4A42}
:root:not([data-theme="light"]) .toast{color:#0E1513}
:root:not([data-theme="light"]) .varpick,:root:not([data-theme="light"]) .ptile.open{background:#142623}
:root:not([data-theme="light"]) .ptile{background:var(--chip)}
:root:not([data-theme="light"]) .ptile.pain.hero{border-color:#356158}
:root:not([data-theme="light"]) .msg.ok{background:#16261A;border-color:#274A2C}
:root:not([data-theme="light"]) .msg.err{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .doserow.done{background:#132016;border-color:#274A2C}
:root:not([data-theme="light"]) .doserow.skip{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .b-ok{background:#16261A}
:root:not([data-theme="light"]) .b-bad,:root:not([data-theme="light"]) .chip.trigger,:root:not([data-theme="light"]) .tag.k-sym{background:#2E1A18}
:root:not([data-theme="light"]) .b-cmf{background:#221B33}
:root:not([data-theme="light"]) .bk .rm{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .tag.k-dose{background:#142422}
:root:not([data-theme="light"]) .tag.k-extra,:root:not([data-theme="light"]) .tag.k-load{background:#221B33;color:var(--hip)}
:root:not([data-theme="light"]) .tag.k-skip{background:#262B29;color:#B3BEBA}
:root:not([data-theme="light"]) .tag.k-bp{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .tag.k-meal{background:#16223A;color:#8FB4E8}
:root:not([data-theme="light"]) .tag.k-act{background:#16261A;color:var(--ok)}
:root:not([data-theme="light"]) .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root:not([data-theme="light"]) .strow.lv-red{background:#241614;border-color:#4A2622}
:root:not([data-theme="light"]) .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .strow.lv-amber .sq{color:var(--amber)}
:root:not([data-theme="light"]) .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root:not([data-theme="light"]) .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rs-hi{color:var(--err)}
:root:not([data-theme="light"]) .rs-auto{background:#241D10}
:root:not([data-theme="light"]) .rd-auto{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rd-row.inbox{border-left-color:var(--mkep)}
:root:not([data-theme="light"]) .ptile.pain .chip.rad{border-color:#4C3C18}
:root:not([data-theme="light"]) .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root:not([data-theme="light"]) .meter i{background:var(--card)}
:root:not([data-theme="light"]) .wkcol.nodata .bar,:root:not([data-theme="light"]) .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root:not([data-theme="light"]) .mark,:root:not([data-theme="light"]) form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) header{background:#0B4F4F}
:root:not([data-theme="light"]) #scanroot .btn,:root:not([data-theme="light"]) #scanroot button{background:#0B4F4F;color:#fff}
:root:not([data-theme="light"]) .muted,:root:not([data-theme="light"]) label.f{color:var(--muted)}
:root:not([data-theme="light"]) svg text{fill:var(--muted)}
:root:not([data-theme="light"]) .ring svg text{fill:inherit}
:root:not([data-theme="light"]) body{background:radial-gradient(1200px 600px at 50% -10%,#16302B,var(--bg))}
}
:root[data-theme="dark"]{color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] body{background:var(--bg);color:var(--ink)}
:root[data-theme="dark"] .card{background:var(--card);border-color:var(--line)}
:root[data-theme="dark"] .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root[data-theme="dark"] input,:root[data-theme="dark"] select,:root[data-theme="dark"] textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] ::placeholder{color:var(--muted);opacity:1}
:root[data-theme="dark"] .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] .btn.ghost,:root[data-theme="dark"] .vtm .btn.sk,:root[data-theme="dark"] .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root[data-theme="dark"] .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root[data-theme="dark"] button.warn{background:var(--card)}
:root[data-theme="dark"] .addbtn{border-color:#356158}
:root[data-theme="dark"] .varpick .vb button{background:var(--card);color:var(--ink)}
:root[data-theme="dark"] .btn.primary,:root[data-theme="dark"] .chip.sel,:root[data-theme="dark"] .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root[data-theme="dark"] .chip.just,:root[data-theme="dark"] .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root[data-theme="dark"] .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root[data-theme="dark"] .seg{background:var(--chip)}
:root[data-theme="dark"] .seg button.sel{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] .doserow,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row{background:var(--card)}
:root[data-theme="dark"] #tab-files .rs-card .btn.tiny,:root[data-theme="dark"] #tab-files .rp-row .btn.tiny,:root[data-theme="dark"] #rsPrint,:root[data-theme="dark"] #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root[data-theme="dark"] nav{background:var(--card)}
:root[data-theme="dark"] nav button.sel i{background:#2A4A42}
:root[data-theme="dark"] .toast{color:#0E1513}
:root[data-theme="dark"] .varpick,:root[data-theme="dark"] .ptile.open{background:#142623}
:root[data-theme="dark"] .ptile{background:var(--chip)}
:root[data-theme="dark"] .ptile.pain.hero{border-color:#356158}
:root[data-theme="dark"] .msg.ok{background:#16261A;border-color:#274A2C}
:root[data-theme="dark"] .msg.err{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .doserow.done{background:#132016;border-color:#274A2C}
:root[data-theme="dark"] .doserow.skip{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .b-ok{background:#16261A}
:root[data-theme="dark"] .b-bad,:root[data-theme="dark"] .chip.trigger,:root[data-theme="dark"] .tag.k-sym{background:#2E1A18}
:root[data-theme="dark"] .b-cmf{background:#221B33}
:root[data-theme="dark"] .bk .rm{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .tag.k-dose{background:#142422}
:root[data-theme="dark"] .tag.k-extra,:root[data-theme="dark"] .tag.k-load{background:#221B33;color:var(--hip)}
:root[data-theme="dark"] .tag.k-skip{background:#262B29;color:#B3BEBA}
:root[data-theme="dark"] .tag.k-bp{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .tag.k-meal{background:#16223A;color:#8FB4E8}
:root[data-theme="dark"] .tag.k-act{background:#16261A;color:var(--ok)}
:root[data-theme="dark"] .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root[data-theme="dark"] .strow.lv-red{background:#241614;border-color:#4A2622}
:root[data-theme="dark"] .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .strow.lv-amber .sq{color:var(--amber)}
:root[data-theme="dark"] .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root[data-theme="dark"] .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rs-hi{color:var(--err)}
:root[data-theme="dark"] .rs-auto{background:#241D10}
:root[data-theme="dark"] .rd-auto{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rd-row.inbox{border-left-color:var(--mkep)}
:root[data-theme="dark"] .ptile.pain .chip.rad{border-color:#4C3C18}
:root[data-theme="dark"] .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root[data-theme="dark"] .meter i{background:var(--card)}
:root[data-theme="dark"] .wkcol.nodata .bar,:root[data-theme="dark"] .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root[data-theme="dark"] .mark,:root[data-theme="dark"] form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] header{background:#0B4F4F}
:root[data-theme="dark"] #scanroot .btn,:root[data-theme="dark"] #scanroot button{background:#0B4F4F;color:#fff}
:root[data-theme="dark"] .muted,:root[data-theme="dark"] label.f{color:var(--muted)}
:root[data-theme="dark"] svg text{fill:var(--muted)}
:root[data-theme="dark"] .ring svg text{fill:inherit}
:root[data-theme="dark"] body{background:radial-gradient(1200px 600px at 50% -10%,#16302B,var(--bg))}
@media print{
:root:not([data-theme="light"]),:root[data-theme="dark"]{color-scheme:light}
:root:not([data-theme="light"]) body{background:#fff;color:#000}
:root:not([data-theme="light"]) .card,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row,:root:not([data-theme="light"]) .doserow{background:#fff;color:#000;border-color:#ccc}
:root[data-theme="dark"] body{background:#fff;color:#000}
:root[data-theme="dark"] .card,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row,:root[data-theme="dark"] .doserow{background:#fff;color:#000;border-color:#ccc}
}
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
<script>(function(){ try{ var t=localStorage.getItem('gl_theme');if(t==='dark'||t==='light'){ document.documentElement.setAttribute('data-theme',t); } }catch(e){ } })();</script>
<style>
:root{color-scheme:light;--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;
--bg:#EFF5F2;--card:#fff;--line:#D5E3DD;--muted:#556C69;--err:#B3372A;
--ok:#2E7D32;--amber:#8A5A00;--swbg:#EAF2F1;--swink:#1F2D2B;--onsw:#fff;
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
.appsw{display:flex;gap:8px;padding:8px 12px 4px;font-size:14px;align-items:center;flex-wrap:wrap}
.appsw a{padding:4px 12px;border-radius:14px;background:var(--swbg);color:var(--swink);text-decoration:none;font-weight:700}
.appsw a.on{background:var(--teal);color:var(--onsw)}
.appsw .thm{margin-left:auto;padding:4px 12px;border-radius:14px;border:1.5px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:14px;font-weight:700;cursor:pointer;min-height:32px}

/* GUTLOG_V3160_DARK -- dark mode, selected and measured.
   Tokens chosen against the dark surface, not flipped from light.
   94 text/background pairs measured across the four pages: worst
   in dark 6.24:1, worst in light 4.48:1 (large text, needs 3:1).
   Marker set re-stepped for dark and validated: worst all-pairs
   deutan delta-E 11.2, tritan 15.9, normal-vision 18.5, all >= 3:1. */
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) body{background:var(--bg);color:var(--ink)}
:root:not([data-theme="light"]) .card{background:var(--card);border-color:var(--line)}
:root:not([data-theme="light"]) .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root:not([data-theme="light"]) input,:root:not([data-theme="light"]) select,:root:not([data-theme="light"]) textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) ::placeholder{color:var(--muted);opacity:1}
:root:not([data-theme="light"]) .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) .btn.ghost,:root:not([data-theme="light"]) .vtm .btn.sk,:root:not([data-theme="light"]) .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root:not([data-theme="light"]) .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root:not([data-theme="light"]) button.warn{background:var(--card)}
:root:not([data-theme="light"]) .addbtn{border-color:#356158}
:root:not([data-theme="light"]) .varpick .vb button{background:var(--card);color:var(--ink)}
:root:not([data-theme="light"]) .btn.primary,:root:not([data-theme="light"]) .chip.sel,:root:not([data-theme="light"]) .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) .chip.just,:root:not([data-theme="light"]) .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root:not([data-theme="light"]) .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root:not([data-theme="light"]) .seg{background:var(--chip)}
:root:not([data-theme="light"]) .seg button.sel{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) .doserow,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row{background:var(--card)}
:root:not([data-theme="light"]) #tab-files .rs-card .btn.tiny,:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny,:root:not([data-theme="light"]) #rsPrint,:root:not([data-theme="light"]) #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) nav{background:var(--card)}
:root:not([data-theme="light"]) nav button.sel i{background:#2A4A42}
:root:not([data-theme="light"]) .toast{color:#0E1513}
:root:not([data-theme="light"]) .varpick,:root:not([data-theme="light"]) .ptile.open{background:#142623}
:root:not([data-theme="light"]) .ptile{background:var(--chip)}
:root:not([data-theme="light"]) .ptile.pain.hero{border-color:#356158}
:root:not([data-theme="light"]) .msg.ok{background:#16261A;border-color:#274A2C}
:root:not([data-theme="light"]) .msg.err{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .doserow.done{background:#132016;border-color:#274A2C}
:root:not([data-theme="light"]) .doserow.skip{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .b-ok{background:#16261A}
:root:not([data-theme="light"]) .b-bad,:root:not([data-theme="light"]) .chip.trigger,:root:not([data-theme="light"]) .tag.k-sym{background:#2E1A18}
:root:not([data-theme="light"]) .b-cmf{background:#221B33}
:root:not([data-theme="light"]) .bk .rm{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .tag.k-dose{background:#142422}
:root:not([data-theme="light"]) .tag.k-extra,:root:not([data-theme="light"]) .tag.k-load{background:#221B33;color:var(--hip)}
:root:not([data-theme="light"]) .tag.k-skip{background:#262B29;color:#B3BEBA}
:root:not([data-theme="light"]) .tag.k-bp{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .tag.k-meal{background:#16223A;color:#8FB4E8}
:root:not([data-theme="light"]) .tag.k-act{background:#16261A;color:var(--ok)}
:root:not([data-theme="light"]) .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root:not([data-theme="light"]) .strow.lv-red{background:#241614;border-color:#4A2622}
:root:not([data-theme="light"]) .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .strow.lv-amber .sq{color:var(--amber)}
:root:not([data-theme="light"]) .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root:not([data-theme="light"]) .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rs-hi{color:var(--err)}
:root:not([data-theme="light"]) .rs-auto{background:#241D10}
:root:not([data-theme="light"]) .rd-auto{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rd-row.inbox{border-left-color:var(--mkep)}
:root:not([data-theme="light"]) .ptile.pain .chip.rad{border-color:#4C3C18}
:root:not([data-theme="light"]) .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root:not([data-theme="light"]) .meter i{background:var(--card)}
:root:not([data-theme="light"]) .wkcol.nodata .bar,:root:not([data-theme="light"]) .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root:not([data-theme="light"]) .mark,:root:not([data-theme="light"]) form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) header{background:#0B4F4F}
:root:not([data-theme="light"]) #scanroot .btn,:root:not([data-theme="light"]) #scanroot button{background:#0B4F4F;color:#fff}
:root:not([data-theme="light"]) .muted,:root:not([data-theme="light"]) label.f{color:var(--muted)}
:root:not([data-theme="light"]) svg text{fill:var(--muted)}
:root:not([data-theme="light"]) .ring svg text{fill:inherit}
}
:root[data-theme="dark"]{color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] body{background:var(--bg);color:var(--ink)}
:root[data-theme="dark"] .card{background:var(--card);border-color:var(--line)}
:root[data-theme="dark"] .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root[data-theme="dark"] input,:root[data-theme="dark"] select,:root[data-theme="dark"] textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] ::placeholder{color:var(--muted);opacity:1}
:root[data-theme="dark"] .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] .btn.ghost,:root[data-theme="dark"] .vtm .btn.sk,:root[data-theme="dark"] .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root[data-theme="dark"] .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root[data-theme="dark"] button.warn{background:var(--card)}
:root[data-theme="dark"] .addbtn{border-color:#356158}
:root[data-theme="dark"] .varpick .vb button{background:var(--card);color:var(--ink)}
:root[data-theme="dark"] .btn.primary,:root[data-theme="dark"] .chip.sel,:root[data-theme="dark"] .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root[data-theme="dark"] .chip.just,:root[data-theme="dark"] .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root[data-theme="dark"] .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root[data-theme="dark"] .seg{background:var(--chip)}
:root[data-theme="dark"] .seg button.sel{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] .doserow,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row{background:var(--card)}
:root[data-theme="dark"] #tab-files .rs-card .btn.tiny,:root[data-theme="dark"] #tab-files .rp-row .btn.tiny,:root[data-theme="dark"] #rsPrint,:root[data-theme="dark"] #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root[data-theme="dark"] nav{background:var(--card)}
:root[data-theme="dark"] nav button.sel i{background:#2A4A42}
:root[data-theme="dark"] .toast{color:#0E1513}
:root[data-theme="dark"] .varpick,:root[data-theme="dark"] .ptile.open{background:#142623}
:root[data-theme="dark"] .ptile{background:var(--chip)}
:root[data-theme="dark"] .ptile.pain.hero{border-color:#356158}
:root[data-theme="dark"] .msg.ok{background:#16261A;border-color:#274A2C}
:root[data-theme="dark"] .msg.err{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .doserow.done{background:#132016;border-color:#274A2C}
:root[data-theme="dark"] .doserow.skip{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .b-ok{background:#16261A}
:root[data-theme="dark"] .b-bad,:root[data-theme="dark"] .chip.trigger,:root[data-theme="dark"] .tag.k-sym{background:#2E1A18}
:root[data-theme="dark"] .b-cmf{background:#221B33}
:root[data-theme="dark"] .bk .rm{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .tag.k-dose{background:#142422}
:root[data-theme="dark"] .tag.k-extra,:root[data-theme="dark"] .tag.k-load{background:#221B33;color:var(--hip)}
:root[data-theme="dark"] .tag.k-skip{background:#262B29;color:#B3BEBA}
:root[data-theme="dark"] .tag.k-bp{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .tag.k-meal{background:#16223A;color:#8FB4E8}
:root[data-theme="dark"] .tag.k-act{background:#16261A;color:var(--ok)}
:root[data-theme="dark"] .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root[data-theme="dark"] .strow.lv-red{background:#241614;border-color:#4A2622}
:root[data-theme="dark"] .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .strow.lv-amber .sq{color:var(--amber)}
:root[data-theme="dark"] .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root[data-theme="dark"] .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rs-hi{color:var(--err)}
:root[data-theme="dark"] .rs-auto{background:#241D10}
:root[data-theme="dark"] .rd-auto{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rd-row.inbox{border-left-color:var(--mkep)}
:root[data-theme="dark"] .ptile.pain .chip.rad{border-color:#4C3C18}
:root[data-theme="dark"] .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root[data-theme="dark"] .meter i{background:var(--card)}
:root[data-theme="dark"] .wkcol.nodata .bar,:root[data-theme="dark"] .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root[data-theme="dark"] .mark,:root[data-theme="dark"] form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] header{background:#0B4F4F}
:root[data-theme="dark"] #scanroot .btn,:root[data-theme="dark"] #scanroot button{background:#0B4F4F;color:#fff}
:root[data-theme="dark"] .muted,:root[data-theme="dark"] label.f{color:var(--muted)}
:root[data-theme="dark"] svg text{fill:var(--muted)}
:root[data-theme="dark"] .ring svg text{fill:inherit}
@media print{
:root:not([data-theme="light"]),:root[data-theme="dark"]{color-scheme:light}
:root:not([data-theme="light"]) body{background:#fff;color:#000}
:root:not([data-theme="light"]) .card,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row,:root:not([data-theme="light"]) .doserow{background:#fff;color:#000;border-color:#ccc}
:root[data-theme="dark"] body{background:#fff;color:#000}
:root[data-theme="dark"] .card,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row,:root[data-theme="dark"] .doserow{background:#fff;color:#000;border-color:#ccc}
}
</style></head><body>
<div class="appsw">
<a href="https://rx.dr-manoj.in">RxGuard</a>
<a href="/" class="on">GutLog</a>
<a href="https://fit.dr-manoj.in">FitLog</a>
<button type="button" id="thmBtn" class="thm" aria-live="polite">System</button>
</div>
<script>
function thmGet(){ try{ var t=localStorage.getItem('gl_theme');return (t==='dark'||t==='light')?t:'system'; }catch(e){ return 'system'; } }
function thmName(t){ return t==='dark'?'Dark':(t==='light'?'Light':'System'); }
function thmApply(t){
  var r=document.documentElement;
  if(t==='dark'||t==='light'){ r.setAttribute('data-theme',t); }
  else { r.removeAttribute('data-theme'); }
  var dark = t==='dark' || (t==='system' && window.matchMedia && window.matchMedia('(prefers-color-scheme:dark)').matches);
  var m=document.querySelector('meta[name="theme-color"]');
  if(m){ m.setAttribute('content', dark?'#0B4F4F':'#0B6E6E'); }
  var b=document.getElementById('thmBtn');
  if(b){ b.textContent=thmName(t);
    b.setAttribute('aria-label','Theme: '+thmName(t)+'. Tap to change.'); }
}
function thmCycle(){
  var o=thmGet(), n = o==='system'?'light':(o==='light'?'dark':'system');
  try{ if(n==='system'){ localStorage.removeItem('gl_theme'); }
       else { localStorage.setItem('gl_theme',n); } }catch(e){ }
  thmApply(n);
}
(function(){
  var b=document.getElementById('thmBtn');
  if(b){ b.addEventListener('click',thmCycle); }
  thmApply(thmGet());
  if(window.matchMedia){
    var mq=window.matchMedia('(prefers-color-scheme:dark)');
    var f=function(){ if(thmGet()==='system'){ thmApply('system'); } };
    if(mq.addEventListener){ mq.addEventListener('change',f); }
  }
})();
</script>
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

<script>(function(){ try{ var t=localStorage.getItem('gl_theme');if(t==='dark'||t==='light'){ document.documentElement.setAttribute('data-theme',t); } }catch(e){ } })();</script>
<style>
:root{color-scheme:light;
--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;--teal-d:#084F4F;--bg:#EFF5F2;--card:#fff;
--line:#D5E3DD;--muted:#556C69;--chip:#E6F0EC;--err:#B3372A;--ok:#2E7D32;--amber:#8A5A00;
--amber-bg:#FBF1DC;--hip:#7A4FBF;--patch:#EFE7FB;
--swbg:#EAF2F1;--swink:#1F2D2B;--onsw:#fff;--rtrk:#E6F0EC;--rtrk2:#C9D9D3;
/* one validated categorical set: pain / operating day / epoch, and the
   same three for the pain-tea-coffee line. Light steps on #FCFCF9 pass
   every check: deutan 11.1, tritan 14.1, normal-vision 16.0, all >=3:1. */
--mkpain:#B3372A;--mkot:#6A3FA8;--mkep:#B57B08;--mkdown:#0A93B0;
--fmL:#2E7D32;--fmLM:#7CA53A;--fmM:#C8860A;--fmMH:#C2622B;--fmH:#B3372A;
--grad:linear-gradient(135deg,#0B6E6E,#12907C)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif;
padding-bottom:calc(80px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;z-index:5;background:var(--grad);color:#fff;
padding:calc(10px + env(safe-area-inset-top)) 16px 10px;display:flex;align-items:center;gap:10px;
/* GUTLOG_V3280_PLANS -- a third link does not fit on the folded
   screen, so the bar wraps there rather than scrolling the page. */
flex-wrap:wrap;
box-shadow:0 2px 14px rgba(8,79,79,.25)}
header h1{font-size:20px;margin:0;font-weight:800;letter-spacing:-.4px}
header h1 b{color:#FFD98A}
header .v{font-size:11px;background:rgba(255,255,255,.18);padding:2px 8px;border-radius:999px}
header .day{margin-left:auto;font-size:12.5px;opacity:.92;text-align:right;line-height:1.25}
header .streak{font-weight:700;color:#FFD98A}
header a{color:#fff;opacity:.85;font-size:13px;text-decoration:none;margin-left:10px;white-space:nowrap}
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
.hint{font-size:14px}
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
/* GUTLOG_V3220_MEALS */
#mealTabs{margin:4px 0 6px}
.mrow{padding:10px 0;border-top:1px solid var(--line)}
.mstep{display:flex;align-items:center;gap:10px}
.mq{min-width:74px;text-align:center;font-weight:700;font-size:16px}
.mx{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:6px 0;font-size:15px}
.mtd{display:flex;justify-content:space-between;gap:8px;padding:10px 0;border-top:1px solid var(--line);font-size:15px}
.mtd small{display:block;color:var(--muted);font-size:14px;margin-top:2px}
.macts{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;flex:none;max-width:50%}
.macts .chip{padding:7px 11px;font-size:14px}
.mnew{margin-top:10px;padding:10px;border:1.5px dashed var(--line);border-radius:12px}
/* GUTLOG_V3240_RECIPES */
.rcrow{display:block;width:100%;text-align:left;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:0 0 8px;font-size:16px}
.rcrow b{display:block;font-size:16px}
.rcrow small{display:block;color:var(--muted);font-size:14px;margin-top:3px}
.rctag{display:inline-block;font-size:13px;font-weight:700;border-radius:999px;padding:2px 9px;margin:6px 6px 0 0;border:1.5px solid var(--line);color:var(--muted)}
.rctag.warn{border-color:var(--amber);color:var(--amber)}
.rctag.ok{border-color:var(--teal);color:var(--teal)}
.rcnum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin:10px 0}
.rcnum div{border:1px solid var(--line);border-radius:12px;padding:8px 4px;text-align:center}
.rcnum b{display:block;font-size:18px}.rcnum span{font-size:13px;color:var(--muted)}
.rcing{margin:0;padding:0;list-style:none}
.rcing li{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:15px}
.rcing li span:last-child{color:var(--muted);text-align:right}
.rcing li.hi span:first-child{color:var(--amber);font-weight:700}
.rcsteps{margin:0;padding-left:22px;font-size:15px}.rcsteps li{margin:0 0 7px}
.rcof{border:1.5px solid var(--teal);border-radius:12px;padding:10px 12px;margin:10px 0}
.rcof ul{margin:6px 0 0;padding-left:20px;font-size:15px}
/* GUTLOG_V3250_PLAN */
#nowPlan .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}
#nowPlan .dwtop .q{margin:0}#nowPlan .fs{margin-left:auto;font-size:14px;color:var(--muted)}
.pbar2{margin:0 0 9px}.pb2t{display:flex;justify-content:space-between;font-size:15px;margin:0 0 4px}
.pb2t span:last-child{color:var(--muted)}
.pb2b{height:8px;border-radius:99px;background:var(--line);overflow:hidden}
.pb2b i{display:block;height:100%;background:var(--teal);border-radius:99px}.pb2b i.over{background:var(--amber)}
.plantips{margin:4px 0 0;padding-left:20px;font-size:15px}.plantips li{margin:0 0 5px}
.planrules{list-style:none;margin:8px 0;padding:0}
.planrules li{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:15px}
.planrules li span:last-child{color:var(--muted);text-align:right}
.planrules li.pr-over span:last-child,.planrules li.pr-short span:last-child,.planrules li.pr-due span:last-child{color:var(--amber);font-weight:700}
/* GUTLOG_V3260_TRIALS */
.trow{border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:0 0 10px}
.trow b{font-size:16px}.trow .tsig{font-weight:700}
.trow .tsig.warn{color:var(--amber)}.trow .tsig.ok{color:var(--teal)}
.ttab{width:100%;border-collapse:collapse;font-size:15px;margin:8px 0}
.ttab th,.ttab td{text-align:right;padding:6px 4px;border-top:1px solid var(--line)}
.ttab th:first-child,.ttab td:first-child{text-align:left}
.ttab th{font-size:13px;color:var(--muted);font-weight:700}
/* GUTLOG_V3270_TIMEPICK */
.tpick{display:inline-flex;align-items:center;gap:6px;font-size:18px;font-weight:700}
.tpick select{width:auto;min-width:72px;padding:10px 8px;font-size:18px}
.exrow.extime .t,.exrow.extime .m{cursor:pointer}
.exrow.extime .t{text-decoration:underline dotted}
.exago{margin:6px 0 8px}
/* GUTLOG_V3230_CONTEXT */
#nowCtx .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}
#nowCtx .dwtop .q{margin:0}
#nowCtx .fs{margin-left:auto;font-size:14px;color:var(--muted);text-align:right}
.mpick{margin-top:10px}

/* collapsible cards */
.card.fold{padding:0;overflow:hidden}
.card.fold .fold-h{width:100%;display:flex;align-items:center;gap:10px;
padding:16px;border:0;background:none;font-family:inherit;font-size:16px;
font-weight:800;color:var(--ink);cursor:pointer;text-align:left}
.card.fold .ft{flex:0 0 auto}
.card.fold .fs{margin-left:auto;font-size:15px;font-weight:600;color:var(--muted)}
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
.hint{font-size:14px;color:var(--muted);margin:2px 2px 12px}
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
.mirrorwarn{border-color:var(--amber,#B57B08)}
.mirrorwarn .q{color:var(--amber,#B57B08)}
/* GUTLOG_V3290_NUTRITION -- the day stepper. Sized to sit inside 300px. */
.mlstep{display:flex;align-items:center;gap:6px;margin:0 0 8px;flex-wrap:wrap}
.mlstep button{border:1px solid var(--line);background:var(--card);color:var(--ink);
  border-radius:9px;padding:6px 10px;font:inherit;font-weight:700;cursor:pointer}
.mlstep button[disabled]{opacity:.38;cursor:default}
.mlstep .mldate{flex:1 1 auto;min-width:0;font-size:13.5px;text-align:center}
.mlstep .mlhist{margin-left:auto;font-size:12.5px;font-weight:700;padding:6px 10px;
  border:1px solid var(--teal);border-radius:9px;color:var(--teal);text-decoration:none}
.mldmy{display:flex;gap:5px;margin:0 0 8px}
.mldmy select{flex:1 1 auto;min-width:0;font:inherit;padding:6px;border-radius:8px;
  border:1px solid var(--line);background:var(--card);color:var(--ink)}
.mldmy button{border:1px solid var(--teal);background:var(--teal);color:#fff;
  border-radius:8px;padding:6px 11px;font:inherit;font-weight:700;cursor:pointer}
.mlmeal{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;
  padding:9px 0;border-top:1px solid var(--line)}
.mlmeal:first-child{border-top:0}
.mlmeal b{font-size:13.5px}
.mlmeal small{color:var(--muted);flex:1 1 100%}
.mlmeal .macts{display:flex;gap:5px;margin-left:auto}
/* GUTLOG_V3310_FOODLIB -- weight fields, the table lookup, time buttons.
   Sized to sit inside 300px, like everything else on this page. */
.bk{flex-wrap:wrap}
.bkw{flex:1 0 100%;display:flex;align-items:center;gap:6px;flex-wrap:wrap;
  font-size:12.5px;color:var(--muted);padding:3px 0 0 18px}
.mrow .bkw{padding-left:0;margin:0 0 6px}
.bkw input.bkg{width:76px;flex:0 0 76px;padding:6px 8px;font-size:15px}
.dryw{color:var(--ink);font-size:12.5px}
.chip.tbtn{padding:4px 10px;font-size:13.5px;font-weight:700;font-variant-numeric:tabular-nums}
.chip.rvt{padding:2px 7px;font-size:12.5px}
.mth{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mlmeal .chip.tbtn{margin-right:2px}
.todayrow{flex-wrap:wrap}.todayrow .tms{display:flex;flex-wrap:wrap;gap:5px}
.lfed input[type=text],.lfed input[type=number]{margin-bottom:8px}
.lfed .row3 input{margin-bottom:0}
.lfw{display:flex;gap:8px;align-items:center;margin-bottom:4px}
.lfw input{flex:1 1 auto;min-width:0;margin:0 !important}
.lfw .chips{flex:0 0 auto;flex-wrap:nowrap;gap:5px}
.lfw .chip{padding:7px 11px}
.lfchk{display:flex;gap:8px;align-items:center;font-size:13.5px;margin:6px 0}
.lfchk input{width:auto;margin:0}
.lfhit{display:block;width:100%;text-align:left;border:1px solid var(--line);
  background:var(--card);color:var(--ink);border-radius:10px;padding:8px 10px;
  margin:0 0 6px;font:inherit;font-size:13.5px;cursor:pointer}
.lfhit small{display:block;color:var(--muted);font-size:12px}
.lfbtn{display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap}
.lfbtn .backlink{margin:0 0 0 auto}
.lf_per,.lf_src{margin:6px 0 0}
.mdt{display:flex;flex-wrap:wrap;gap:10px}
.mdt>div{flex:1 1 130px;min-width:0}
.tot b{color:var(--ink)}
/* meal basket */
.bk{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px dashed var(--line);font-size:14.5px}
.bk .nm{flex:1}.bk .nm small{display:block;color:var(--muted);font-size:11.5px}
.bk button{width:32px;height:32px;border-radius:9px;border:1.5px solid var(--line);background:var(--chip);
font-size:17px;font-weight:700;color:var(--teal);cursor:pointer}
.bk .qv{min-width:26px;text-align:center;font-weight:700}
.bk .rm{color:var(--err);border-color:#EAC7C2;background:#FBEDEC}
.badge{display:inline-block;font-size:11.5px;padding:3px 9px;border-radius:999px;font-weight:700}
.b-ok{background:#EAF4EB;color:var(--ok)}.b-bad{background:#FBEDEC;color:var(--err)}
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
.samedose{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(84px + env(safe-area-inset-bottom));width:min(92vw,440px);z-index:9;background:var(--card);color:var(--ink);border:1.5px solid var(--amber);border-radius:13px;padding:10px 12px;font-size:15px;display:none;box-shadow:0 4px 18px rgba(0,0,0,.18)}
.samedose.show{display:block}
.samedose .sdrow{display:flex;gap:8px;align-items:center;justify-content:space-between;margin:4px 0}
.samedose button{font-size:14px;min-height:40px;padding:6px 12px;border-radius:999px;border:1.5px solid var(--teal);background:transparent;color:var(--teal);white-space:nowrap}
.samedose .sdx{border-color:var(--line);color:var(--muted)}
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
/* GUTLOG_V370_SALTS_ACTIVITY */
#actTiles .ptile.open .pscore{display:block}
#actTiles .pscore .chips{margin-bottom:8px}
#actTiles .pscore .btn.primary{margin-top:4px}
.stockalert.info{background:var(--chip);border-color:var(--line);color:var(--ink)}
.stockalert.medstat{flex-wrap:wrap}
.stockalert .ml{display:flex;flex-wrap:wrap;gap:6px 12px}
.stockalert .mlink{background:none;border:0;padding:0;font:inherit;color:inherit;text-decoration:underline;cursor:pointer;text-align:left}
#saltList .vtm .st{flex:0 0 34% }
/* GUTLOG_V3190_ORDER -- monthly order card and the pack form */
.ordl{padding:8px 0;border-bottom:1px solid var(--line)}
.ordl b{font-size:15px}
.ordl .oq{float:right;font-weight:800;color:var(--teal-d);margin-left:8px}
.ordl .od{clear:both;font-size:12.5px;color:var(--muted);margin-top:2px}
.ordbtns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.ordbtns .btn{margin:0}
.ordbtns [hidden]{display:none}
.strow .pk{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
.strow .pk .btn{margin-top:8px}
#ordCard .btn.go,#sosCard .btn.go,#stList .pk .btn{background:var(--teal);border-color:var(--teal);color:var(--card)}
#ordCard .btn.ghost,#sosCard .btn.ghost{background:var(--card);color:var(--teal);border-color:var(--line)}
.pk .lkv{font-weight:800;font-size:15px;margin:10px 0 0}
.pk .lk+.lk{margin-top:6px}
.tag.k-act{background:#E6F4EA;color:#2E7D32}
/* GUTLOG_V380_RECORDS */
.seg[data-seg="files"]+.seg[data-seg="files"]{margin-top:-6px}
.rs-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.rs-card .q{margin-bottom:6px}
.rs-row{display:flex;flex-direction:column;padding:6px 0;border-bottom:1px solid var(--line,#E4E7E5)}
.rs-row:last-child{border-bottom:0}
.rs-meta{color:var(--muted);font-size:13px;margin:2px 0 0}
.rs-prec{padding:7px 0;border-bottom:1px solid var(--line,#E4E7E5)}
.rs-prec:last-child{border-bottom:0}
.rs-flag{display:inline-block;font-size:11px;font-weight:800;padding:1px 6px;border-radius:6px;margin-right:6px}
.rs-flag.RED{background:#FBEDEC;color:#B3372A}.rs-flag.AMBER{background:#FFF3DC;color:#8A5A00}
.rs-tab{width:100%;border-collapse:collapse;font-size:13.5px}
.rs-tab td{padding:5px 4px;border-bottom:1px solid var(--line,#E4E7E5);vertical-align:top}
.rs-hi{color:#B3372A;font-weight:700}
.rs-link{display:inline-block;margin-top:6px;font-weight:700;color:var(--teal)}
.rd-year{font-weight:800;color:var(--muted);margin:14px 2px 6px;font-size:13px;letter-spacing:.04em}
.rd-row{background:#fff;border-radius:12px;padding:10px 12px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.rd-row.inbox{border-left:4px solid #C08A00}
.rd-top{display:flex;gap:8px;align-items:center;font-size:12.5px;color:var(--muted)}
.rd-kind{background:var(--chip);border-radius:6px;padding:1px 6px;font-weight:700}
.rd-title{display:block;margin:3px 0 1px}
.rd-find{font-size:13.5px;margin:4px 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
.rd-find.open{display:block}
.rt-row{background:#fff;border-radius:12px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.rt-head{display:flex;align-items:center;gap:8px;padding:9px 12px;cursor:pointer}
.rt-name{flex:1;min-width:0;display:flex;flex-direction:column}
.rt-spark{width:92px;height:30px;flex:0 0 auto}
.rt-last{min-width:64px;text-align:right;font-weight:700;font-size:13.5px}
.rt-det{padding:0 12px 10px}
.rp-head{display:flex;gap:8px;align-items:baseline}
.rp-n{font-weight:800;color:var(--teal)}
.rp-when{font-size:13px;margin:4px 0 8px}
.rp-row.done{opacity:.7}
#tab-files .rs-card .btn.tiny,#tab-files .rp-row .btn.tiny,#rsPrint{border:1.5px solid var(--teal);color:var(--teal);background:#fff}
#tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#fff}
@media print{
  header,#nav,.save,.seg,.toast,#rsPrint{display:none!important}
  .tab{display:none!important}#tab-files{display:block!important}
  #tab-files .sub{display:none!important}#files-summary{display:block!important}
  .card{box-shadow:none;border:1px solid #ccc;break-inside:avoid}
  body{background:#fff}
}
/* GUTLOG_V390_SCAN */
.scanbtn{display:block;text-align:center;background:var(--teal);color:#fff;font-weight:800;font-size:16px;
  padding:14px 12px;border-radius:13px;text-decoration:none;margin:0 0 10px;box-shadow:0 1px 3px rgba(0,0,0,.12)}
/* GUTLOG_V3100_AUTOREAD */
.rd-auto{background:#FFF3DC;color:#8A5A00;border-radius:6px;padding:1px 6px;font-weight:700}
#tab-files .rd-ok{margin-top:8px;margin-right:10px;border:1.5px solid var(--teal);color:var(--teal);background:#fff}
.rs-auto{background:#FFF8EA;border-radius:10px;padding:8px 10px;margin:0 0 10px}
/* GUTLOG_V3120_PAIN -- musculoskeletal pain tiles, operating-day load */
.ptile.pain .pscore{display:none;padding:0 12px 12px}
.ptile.pain.open .pscore{display:block}
.ptile.pain.hero .ph{font-size:17.5px;font-weight:800;padding:16px 14px}
.ptile.pain.hero{border-color:#BAD2C8}
.ptile.pain .chips{display:flex;flex-wrap:wrap;gap:7px}
.ptile.pain .lbl{margin:0 0 6px}
.ptile.pain .prad{margin-top:12px}
.ptile.pain .chip.rad{border-color:#EBCB8B}
.ptile.pain .chip.rad.sel{background:#FFF3DC;color:#7A5200;border-color:#EBCB8B}
.ptile.act.load{border-style:dashed}
#painList .exrow.load,.exrow.load{opacity:.95}
.tag.k-pain{background:#FBEDEC;color:var(--err)}
.tag.k-load{background:#EFE7F8;color:#6A3FA8}
@media (max-width:430px){ #n_msk .chip.num{padding:8px 0;min-width:30px;text-align:center} }
/* GUTLOG_V3150_READ -- Watch card, read on a phone at 5am.
   Nothing below 14px. The old strip was five 116px tiles plus gaps = 612px
   inside a 368px box, so it overflowed; block + a two-column grid cannot.
   Every figure uses tabular-nums so digits stop jittering between refreshes. */
.wkstrip{display:block}
.wkgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.wktile{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px;min-width:0}
.wktile .wl{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  font-size:14px;font-weight:600;color:var(--muted);margin:0}
.wktile .wl .wday{font-weight:500;white-space:nowrap}
.wktile .wv{font-size:24px;font-weight:700;line-height:1.2;margin:4px 0 0;
  font-variant-numeric:tabular-nums}
.wktile.hero .wv{font-size:32px}
.wktile .wv.none{font-size:16px;font-weight:600;color:var(--muted)}
.wktile .wv.med{color:var(--muted)}
.wktile .wc{font-size:15px;font-weight:500;color:var(--ink);margin:4px 0 0;
  font-variant-numeric:tabular-nums}
.wktile .wp{font-size:14px;color:var(--muted);margin:2px 0 0}
.wktile .wr{font-size:14px;color:var(--muted);margin:6px 0 0;
  font-variant-numeric:tabular-nums}
/* the chart. 104px so fourteen bars have a readable shape; 2px between
   adjacent bars; 4px rounded data-ends, square to the baseline. */
.wkrow{display:flex;gap:2px;align-items:flex-end;margin:14px 0 0;height:104px;
  border-bottom:1px solid var(--line)}
.wkcol{flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;
  height:100%;min-width:0;cursor:pointer;background:none;border:0;padding:0}
.wkcol .bar{background:var(--teal);border-radius:4px 4px 0 0;min-height:3px}
.wkcol.nodata .bar{height:100%;border-radius:0;
  background:repeating-linear-gradient(45deg,#E4ECE9,#E4ECE9 2px,transparent 2px,transparent 5px)}
.wkcol.today .bar{background:var(--teal2)}
.wkcol.sel .bar{outline:2px solid var(--ink);outline-offset:1px}
.wkep{display:flex;gap:2px;height:6px;margin-top:4px}
.wkep .seg{flex:1 1 0;border-radius:2px;background:transparent}
.wkep .seg.on{background:var(--mkep)}
/* lanes: 10px marks, and a SHAPE as well as a colour -- pain is a disc,
   an operating day is a diamond, so neither is colour-alone */
.wklane{display:flex;gap:2px;height:12px;margin-top:4px;align-items:center}
.wklane .mk{flex:1 1 0;height:10px;position:relative}
.wklane .mk.on::after{content:"";position:absolute;left:50%;top:50%;
  width:10px;height:10px;margin:-5px 0 0 -5px}
.wklane.pain .mk.on::after{background:var(--mkpain);border-radius:50%;}
.wklane.ot .mk.on::after{background:var(--mkot);transform:rotate(45deg);border-radius:1px}
.wkkey{display:flex;flex-wrap:wrap;gap:12px;font-size:14px;color:var(--muted);margin-top:10px}
.wkkey i{display:inline-block;width:10px;height:10px;margin-right:5px;vertical-align:-1px}
.wkkey i.pain{background:var(--mkpain);border-radius:50%;}
.wkkey i.ot{background:var(--mkot);transform:rotate(45deg);border-radius:1px}
.wkkey i.ep{background:var(--mkep);border-radius:2px}
.wkkey i.nd{background:repeating-linear-gradient(45deg,#E4ECE9,#E4ECE9 2px,transparent 2px,transparent 5px);
  border:1px solid var(--line)}
.wkdates{display:flex;justify-content:space-between;font-size:14px;color:var(--muted);margin-top:6px}
.wkpick{font-size:15px;color:var(--ink);margin:8px 0 0;min-height:21px;
  font-variant-numeric:tabular-nums}
.wknote{font-size:14px;color:var(--muted);margin:12px 0 0;line-height:1.5}
.wkwo{display:flex;gap:10px;padding:10px 2px;border-top:1px solid var(--line);font-size:15px}
.wkwo .wt{flex:0 0 96px;color:var(--muted);font-size:14px;font-variant-numeric:tabular-nums}
/* GUTLOG_V3170_DOWN -- the down-day card: one tap, everything else behind it.
   The lane marker is a SQUARE in a fourth colour validated with the other
   three: light #0A93B0 on #FCFCF9, dark #3D9BE0 on #18211F. */
#nowDown .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}
#nowDown .dwtop .q{margin:0;flex:1}
#nowDown .dwtop .fs{font-size:15px;font-weight:700;color:var(--muted)}
#nowDown.on .dwtop .fs{color:var(--teal)}
#nowDown .dwmore{margin-top:4px}
.dwtemp{display:flex;gap:8px;align-items:center;margin-top:14px;flex-wrap:wrap}
.dwtemp .lbl{margin:0;flex:1 1 100%;}
.dwtemp input{flex:0 0 120px;padding:11px 10px;border:1.5px solid var(--line);border-radius:10px;
  font-size:16px;background:var(--card);color:var(--ink)}
.dwtemp .btn{margin:0;padding:11px 14px}
.dwnote{font-size:15px;line-height:1.5;color:var(--ink);background:var(--chip);
  border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:14px 0 0}
.wklane.down .mk.on::after{background:var(--mkdown);border-radius:1px}
.wkkey i.down{background:var(--mkdown);border-radius:1px}
.tag.k-down{background:var(--chip);color:var(--teal)}
/* every caption on these surfaces is 14px or more; .lbl elsewhere is 13.5px */
#nowDown .lbl,#ddBody .lbl,.dwtemp .lbl{font-size:14px}
#nowDown .btn.tiny,#dvDown{font-size:14px}
.ddwrap{margin-top:6px}
.ddtab{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.ddtab th,.ddtab td{padding:7px 5px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top;line-height:1.45}
.ddtab td:first-child{white-space:nowrap;padding-right:8px}
.ddtab td.fx{white-space:normal;color:var(--ink)}
.ddtab th{color:var(--muted);font-weight:700}
.ddtab tr.dn td{font-weight:700}
.ddtab tr.dn td:first-child{color:var(--teal)}
.ddtab tr.bf td:first-child{color:var(--muted)}
.ddobs{font-size:15px;line-height:1.5;margin:8px 2px 0}
@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}
.appsw{display:flex;gap:8px;padding:8px 12px 4px;font-size:14px;align-items:center;flex-wrap:wrap}
.appsw a{padding:4px 12px;border-radius:14px;background:var(--swbg);color:var(--swink);text-decoration:none;font-weight:700}
.appsw a.on{background:var(--teal);color:var(--onsw)}
.appsw .thm{margin-left:auto;padding:4px 12px;border-radius:14px;border:1.5px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:14px;font-weight:700;cursor:pointer;min-height:32px}

/* GUTLOG_V3160_DARK -- dark mode, selected and measured.
   Tokens chosen against the dark surface, not flipped from light.
   94 text/background pairs measured across the four pages: worst
   in dark 6.24:1, worst in light 4.48:1 (large text, needs 3:1).
   Marker set re-stepped for dark and validated: worst all-pairs
   deutan delta-E 11.2, tritan 15.9, normal-vision 18.5, all >= 3:1. */
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) body{background:var(--bg);color:var(--ink)}
:root:not([data-theme="light"]) .card{background:var(--card);border-color:var(--line)}
:root:not([data-theme="light"]) .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root:not([data-theme="light"]) input,:root:not([data-theme="light"]) select,:root:not([data-theme="light"]) textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) ::placeholder{color:var(--muted);opacity:1}
:root:not([data-theme="light"]) .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root:not([data-theme="light"]) .btn.ghost,:root:not([data-theme="light"]) .vtm .btn.sk,:root:not([data-theme="light"]) .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root:not([data-theme="light"]) .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root:not([data-theme="light"]) button.warn{background:var(--card)}
:root:not([data-theme="light"]) .addbtn{border-color:#356158}
:root:not([data-theme="light"]) .varpick .vb button{background:var(--card);color:var(--ink)}
:root:not([data-theme="light"]) .btn.primary,:root:not([data-theme="light"]) .chip.sel,:root:not([data-theme="light"]) .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) .chip.just,:root:not([data-theme="light"]) .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root:not([data-theme="light"]) .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root:not([data-theme="light"]) .seg{background:var(--chip)}
:root:not([data-theme="light"]) .seg button.sel{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) .doserow,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row{background:var(--card)}
:root:not([data-theme="light"]) #tab-files .rs-card .btn.tiny,:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny,:root:not([data-theme="light"]) #rsPrint,:root:not([data-theme="light"]) #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root:not([data-theme="light"]) #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root:not([data-theme="light"]) nav{background:var(--card)}
:root:not([data-theme="light"]) nav button.sel i{background:#2A4A42}
:root:not([data-theme="light"]) .toast{color:#0E1513}
:root:not([data-theme="light"]) .varpick,:root:not([data-theme="light"]) .ptile.open{background:#142623}
:root:not([data-theme="light"]) .ptile{background:var(--chip)}
:root:not([data-theme="light"]) .ptile.pain.hero{border-color:#356158}
:root:not([data-theme="light"]) .msg.ok{background:#16261A;border-color:#274A2C}
:root:not([data-theme="light"]) .msg.err{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .doserow.done{background:#132016;border-color:#274A2C}
:root:not([data-theme="light"]) .doserow.skip{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .b-ok{background:#16261A}
:root:not([data-theme="light"]) .b-bad,:root:not([data-theme="light"]) .chip.trigger,:root:not([data-theme="light"]) .tag.k-sym{background:#2E1A18}
:root:not([data-theme="light"]) .b-cmf{background:#221B33}
:root:not([data-theme="light"]) .bk .rm{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .tag.k-dose{background:#142422}
:root:not([data-theme="light"]) .tag.k-extra,:root:not([data-theme="light"]) .tag.k-load{background:#221B33;color:var(--hip)}
:root:not([data-theme="light"]) .tag.k-skip{background:#262B29;color:#B3BEBA}
:root:not([data-theme="light"]) .tag.k-bp{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .tag.k-meal{background:#16223A;color:#8FB4E8}
:root:not([data-theme="light"]) .tag.k-act{background:#16261A;color:var(--ok)}
:root:not([data-theme="light"]) .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root:not([data-theme="light"]) .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root:not([data-theme="light"]) .strow.lv-red{background:#241614;border-color:#4A2622}
:root:not([data-theme="light"]) .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root:not([data-theme="light"]) .strow.lv-amber .sq{color:var(--amber)}
:root:not([data-theme="light"]) .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root:not([data-theme="light"]) .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rs-hi{color:var(--err)}
:root:not([data-theme="light"]) .rs-auto{background:#241D10}
:root:not([data-theme="light"]) .rd-auto{background:#2E2413;color:var(--amber)}
:root:not([data-theme="light"]) .rd-row.inbox{border-left-color:var(--mkep)}
:root:not([data-theme="light"]) .ptile.pain .chip.rad{border-color:#4C3C18}
:root:not([data-theme="light"]) .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root:not([data-theme="light"]) .meter i{background:var(--card)}
:root:not([data-theme="light"]) .wkcol.nodata .bar,:root:not([data-theme="light"]) .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root:not([data-theme="light"]) .mark,:root:not([data-theme="light"]) form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root:not([data-theme="light"]) header{background:#0B4F4F}
:root:not([data-theme="light"]) #scanroot .btn,:root:not([data-theme="light"]) #scanroot button{background:#0B4F4F;color:#fff}
:root:not([data-theme="light"]) .muted,:root:not([data-theme="light"]) label.f{color:var(--muted)}
:root:not([data-theme="light"]) svg text{fill:var(--muted)}
:root:not([data-theme="light"]) .ring svg text{fill:inherit}
}
:root[data-theme="dark"]{color-scheme:dark;--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;--hip:#B9A0F0;--patch:#2A2140;--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;--mkdown:#3D9BE0;--rtrk:#2A3734;--rtrk2:#2A3734;--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] body{background:var(--bg);color:var(--ink)}
:root[data-theme="dark"] .card{background:var(--card);border-color:var(--line)}
:root[data-theme="dark"] .chip{background:var(--chip);border-color:var(--line);color:var(--ink)}
:root[data-theme="dark"] input,:root[data-theme="dark"] select,:root[data-theme="dark"] textarea{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] ::placeholder{color:var(--muted);opacity:1}
:root[data-theme="dark"] .btn{background:var(--card);color:var(--ink);border-color:var(--line)}
:root[data-theme="dark"] .btn.ghost,:root[data-theme="dark"] .vtm .btn.sk,:root[data-theme="dark"] .strow .sb .btn{background:var(--card);color:var(--teal);border-color:#356158}
:root[data-theme="dark"] .btn.tiny{color:var(--err);border-color:#5B2F2A}
:root[data-theme="dark"] button.warn{background:var(--card)}
:root[data-theme="dark"] .addbtn{border-color:#356158}
:root[data-theme="dark"] .varpick .vb button{background:var(--card);color:var(--ink)}
:root[data-theme="dark"] .btn.primary,:root[data-theme="dark"] .chip.sel,:root[data-theme="dark"] .scanbtn{background:var(--teal);border-color:var(--teal);color:#0E1513}
:root[data-theme="dark"] .chip.just,:root[data-theme="dark"] .doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#0E1513}
:root[data-theme="dark"] .doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#0E1513}
:root[data-theme="dark"] .seg{background:var(--chip)}
:root[data-theme="dark"] .seg button.sel{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] .doserow,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row{background:var(--card)}
:root[data-theme="dark"] #tab-files .rs-card .btn.tiny,:root[data-theme="dark"] #tab-files .rp-row .btn.tiny,:root[data-theme="dark"] #rsPrint,:root[data-theme="dark"] #tab-files .rd-ok{background:var(--card);color:var(--teal)}
:root[data-theme="dark"] #tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#0E1513}
:root[data-theme="dark"] nav{background:var(--card)}
:root[data-theme="dark"] nav button.sel i{background:#2A4A42}
:root[data-theme="dark"] .toast{color:#0E1513}
:root[data-theme="dark"] .varpick,:root[data-theme="dark"] .ptile.open{background:#142623}
:root[data-theme="dark"] .ptile{background:var(--chip)}
:root[data-theme="dark"] .ptile.pain.hero{border-color:#356158}
:root[data-theme="dark"] .msg.ok{background:#16261A;border-color:#274A2C}
:root[data-theme="dark"] .msg.err{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .doserow.done{background:#132016;border-color:#274A2C}
:root[data-theme="dark"] .doserow.skip{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .b-ok{background:#16261A}
:root[data-theme="dark"] .b-bad,:root[data-theme="dark"] .chip.trigger,:root[data-theme="dark"] .tag.k-sym{background:#2E1A18}
:root[data-theme="dark"] .b-cmf{background:#221B33}
:root[data-theme="dark"] .bk .rm{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .tag.k-dose{background:#142422}
:root[data-theme="dark"] .tag.k-extra,:root[data-theme="dark"] .tag.k-load{background:#221B33;color:var(--hip)}
:root[data-theme="dark"] .tag.k-skip{background:#262B29;color:#B3BEBA}
:root[data-theme="dark"] .tag.k-bp{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .tag.k-meal{background:#16223A;color:#8FB4E8}
:root[data-theme="dark"] .tag.k-act{background:#16261A;color:var(--ok)}
:root[data-theme="dark"] .stockalert.red{background:#2E1A18;border-color:#4A2622}
:root[data-theme="dark"] .stockalert.amber{background:#2E2413;border-color:#4C3C18;color:var(--amber)}
:root[data-theme="dark"] .strow.lv-red{background:#241614;border-color:#4A2622}
:root[data-theme="dark"] .strow.lv-amber{background:#241D10;border-color:#4C3C18}
:root[data-theme="dark"] .strow.lv-amber .sq{color:var(--amber)}
:root[data-theme="dark"] .rs-flag.RED{background:#2E1A18;color:var(--err)}
:root[data-theme="dark"] .rs-flag.AMBER{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rs-hi{color:var(--err)}
:root[data-theme="dark"] .rs-auto{background:#241D10}
:root[data-theme="dark"] .rd-auto{background:#2E2413;color:var(--amber)}
:root[data-theme="dark"] .rd-row.inbox{border-left-color:var(--mkep)}
:root[data-theme="dark"] .ptile.pain .chip.rad{border-color:#4C3C18}
:root[data-theme="dark"] .ptile.pain .chip.rad.sel{background:#2E2413;color:var(--amber);border-color:#4C3C18}
:root[data-theme="dark"] .meter i{background:var(--card)}
:root[data-theme="dark"] .wkcol.nodata .bar,:root[data-theme="dark"] .wkkey i.nd{background:repeating-linear-gradient(45deg,var(--line),var(--line) 2px,transparent 2px,transparent 5px)}
:root[data-theme="dark"] .mark,:root[data-theme="dark"] form.card button{background:linear-gradient(135deg,#0B4F4F,#0E6B5E)}
:root[data-theme="dark"] header{background:#0B4F4F}
:root[data-theme="dark"] #scanroot .btn,:root[data-theme="dark"] #scanroot button{background:#0B4F4F;color:#fff}
:root[data-theme="dark"] .muted,:root[data-theme="dark"] label.f{color:var(--muted)}
:root[data-theme="dark"] svg text{fill:var(--muted)}
:root[data-theme="dark"] .ring svg text{fill:inherit}
@media print{
:root:not([data-theme="light"]),:root[data-theme="dark"]{color-scheme:light}
:root:not([data-theme="light"]) body{background:#fff;color:#000}
:root:not([data-theme="light"]) .card,:root:not([data-theme="light"]) .rd-row,:root:not([data-theme="light"]) .rt-row,:root:not([data-theme="light"]) .doserow{background:#fff;color:#000;border-color:#ccc}
:root[data-theme="dark"] body{background:#fff;color:#000}
:root[data-theme="dark"] .card,:root[data-theme="dark"] .rd-row,:root[data-theme="dark"] .rt-row,:root[data-theme="dark"] .doserow{background:#fff;color:#000;border-color:#ccc}
}
</style></head><body>
<div class="appsw">
<a href="https://rx.dr-manoj.in">RxGuard</a>
<a href="/" class="on">GutLog</a>
<a href="https://fit.dr-manoj.in">FitLog</a>
<button type="button" id="thmBtn" class="thm" aria-live="polite">System</button>
</div>
<script>
function thmGet(){ try{ var t=localStorage.getItem('gl_theme');return (t==='dark'||t==='light')?t:'system'; }catch(e){ return 'system'; } }
function thmName(t){ return t==='dark'?'Dark':(t==='light'?'Light':'System'); }
function thmApply(t){
  var r=document.documentElement;
  if(t==='dark'||t==='light'){ r.setAttribute('data-theme',t); }
  else { r.removeAttribute('data-theme'); }
  var dark = t==='dark' || (t==='system' && window.matchMedia && window.matchMedia('(prefers-color-scheme:dark)').matches);
  var m=document.querySelector('meta[name="theme-color"]');
  if(m){ m.setAttribute('content', dark?'#0B4F4F':'#0B6E6E'); }
  var b=document.getElementById('thmBtn');
  if(b){ b.textContent=thmName(t);
    b.setAttribute('aria-label','Theme: '+thmName(t)+'. Tap to change.'); }
}
function thmCycle(){
  var o=thmGet(), n = o==='system'?'light':(o==='light'?'dark':'system');
  try{ if(n==='system'){ localStorage.removeItem('gl_theme'); }
       else { localStorage.setItem('gl_theme',n); } }catch(e){ }
  thmApply(n);
}
(function(){
  var b=document.getElementById('thmBtn');
  if(b){ b.addEventListener('click',thmCycle); }
  thmApply(thmGet());
  if(window.matchMedia){
    var mq=window.matchMedia('(prefers-color-scheme:dark)');
    var f=function(){ if(thmGet()==='system'){ thmApply('system'); } };
    if(mq.addEventListener){ mq.addEventListener('change',f); }
  }
})();
</script>
<header><h1>Gut<b>Log</b></h1><span class="v">v3</span>
<span class="day"><span id="hdrDay"></span><br><span class="streak" id="hdrStreak"></span></span>
<a href="/plans">Plans</a><a href="/account">Account</a><a href="/logout">Lock</a></header>
<div class="toast" id="toast" role="status"></div>
<div class="samedose" id="samedose" role="status"></div>
<main>

<!-- ============ LOG ============ -->
<!-- ============ NOW ============ -->
<section class="tab sel" id="tab-now">
  <div id="nowMirror"></div>
  <div id="nowStock"></div>
  <div id="nowOrder"></div>
  <div id="nowMedStatus"></div>
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

  <div class="card" id="nowMeal">
    <div class="dwtop"><p class="q">Meal</p><span class="fs" id="mealSum"></span></div>
    <div class="chips" id="mealTabs"></div>
    <div id="mealBody"></div>
    <div id="mealToday"></div>
  </div>
  <div class="card" id="nowPlan" style="display:none">
    <div class="dwtop"><p class="q">Today against the plan</p><span class="fs" id="planSum"></span></div>
    <div id="planBars"></div>
    <div id="planTips"></div>
    <button type="button" class="btn ghost" id="planMore" style="margin-top:8px">This week</button>
    <div id="planWeek" style="display:none"></div>
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

  <div class="card fold" id="nowAct">
    <button type="button" class="fold-h">
      <span class="ft">Activity</span><span class="fs" id="actSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div id="actTiles"></div>
      <div id="actList"></div>
      <p class="hint" id="actWatch" style="margin:8px 2px 0"></p>
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

  <div class="card fold" id="nowPain">
    <button type="button" class="fold-h">
      <span class="ft">Pain now</span><span class="fs" id="painSum">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody">
      <p class="hint" style="margin:0 0 10px">Tap the site, score it, say what you did.
        Tap <b>eased</b> when it settles and the duration is measured, not guessed.</p>
      <div id="n_msk"></div>
      <div id="painList"></div>
    </div>
  </div>

  <div class="card" id="nowCtx">
    <div class="dwtop"><p class="q">Day context</p><span class="fs" id="ctxSum"></span></div>
    <div class="chips" id="ctxDay"></div>
    <div class="chips" id="ctxTags" style="margin-top:10px"></div>
    <p class="hint" style="margin:10px 2px 0">Anything unusual about the day. These days are set aside when foods and symptoms are compared.</p>
  </div>

  <div class="card" id="nowDown">
    <div class="dwtop"><p class="q">Down day</p><span class="fs" id="downSum"></span></div>
    <button type="button" class="btn primary" id="downMark">Mark today as a down day</button>
    <p class="hint" id="downHint" style="margin:8px 2px 0">One tap. Nothing else is asked.</p>
    <div class="dwmore" id="downMore" hidden>
      <p class="lbl">What it is today &mdash; optional, saves as you tap</p>
      <div class="chips" id="downComp"></div>
      <p class="lbl" style="margin-top:12px">Coped with &mdash; optional</p>
      <div class="chips" id="downCoped"></div>
      <div id="downTemp"></div>
      <p class="dwnote" id="downProto" hidden></p>
      <button type="button" class="btn tiny ghost" id="downUnmark" style="margin-top:14px">Not a down day after all</button>
    </div>
  </div>

  <div class="card fold" id="nowWatch">
    <button type="button" class="fold-h">
      <span class="ft">Watch</span><span class="fs" id="wkSum">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div id="wkStrip" class="wkstrip"></div>
      <div id="wkChart"></div>
      <div id="wkWork"></div>
      <p class="wknote" id="wkNote"></p>
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
    <button data-s="meal" class="sel">Meal</button><button data-s="test">Food test</button><button data-s="recipes">Recipes</button>
  </div>

  <div class="sub sel" id="meals-meal">
    <div class="card" style="padding:11px 14px">
      <div class="mlstep">
        <button type="button" id="mlPrev" aria-label="Previous day">&#9664;</button>
        <button type="button" id="mlPick" class="mldate" aria-label="Choose a day"></button>
        <button type="button" id="mlNext" aria-label="Next day">&#9654;</button>
        <a class="mlhist" href="/nutrition">History</a>
      </div>
      <div class="mldmy" id="mlDmy" style="display:none">
        <select id="mlD" aria-label="Day"></select>
        <select id="mlM" aria-label="Month"></select>
        <select id="mlY" aria-label="Year"></select>
        <button type="button" id="mlGo">Go</button>
      </div>
      <div class="tot" id="dayTotals"></div>
      <div class="pbar"><i id="dayPbar" style="width:0%"></i></div>
      <div class="tot" id="dayFmap"></div>
    </div>
    <div class="card">
      <input type="date" id="ml_day" style="display:none">
      <div><p class="lbl">Time</p><input type="time" id="ml_time"></div>
      <p class="q" style="margin-top:10px">Slot</p>
      <div class="chips" data-f="slot" data-sec="meal" data-v="Breakfast|Lunch|Dinner|Snack"></div></div>
    <div class="card" id="mlDayCard"><p class="q" id="mlDayHead">Meals</p>
      <div id="mlDayMeals"></div></div>
    <div class="card"><p class="q">&#128269; What did you eat?</p>
      <input type="text" id="ml_search" placeholder="Search your foods... (dal, roti, guava)" autocomplete="off">
      <div class="chips" id="ml_results" style="margin-top:9px"></div>
      <div id="ml_newfood" style="display:none;margin-top:10px;border-top:1px dashed var(--line);padding-top:10px"></div></div>
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
    <p class="hint">Your foods, most used first. Each row shows one portion with its weight and values, the values per 100 g, and where the numbers came from. Tap a row to edit; star = favourite in the meal picker.</p>
    <input type="text" id="lib_search" placeholder="Filter..." autocomplete="off" style="margin-bottom:6px">
    <div class="chips" id="lib_filters" style="margin-bottom:4px"></div>
    <div class="card" id="lib_list"></div>
    <div class="card" id="lib_edit" style="display:none"></div>
    <button type="button" class="addbtn" id="lib_addnew">&#10133; Add a new food</button>
  </div>

  <div class="sub" id="meals-recipes">
    <div id="rcList">
      <input type="text" id="rc_q" placeholder="Search your recipes" autocomplete="off">
      <div class="chips" id="rc_groups" style="margin-top:8px"></div>
      <div class="chips" id="rc_stages" style="margin-top:8px"></div>
      <div id="rc_rows" style="margin-top:10px"></div>
    </div>
    <div id="rcOne" style="display:none"></div>
  </div>

  <div class="sub" id="meals-test">
    <div class="card" id="trialCard">
      <p class="q">Food trials</p>
      <p class="hint" style="margin:0 0 8px">A trial runs over days or weeks. Meals with the food are linked to it
        automatically; days you marked in Day context are set aside; it is compared with the 14 days before.</p>
      <div id="trialList"></div>
      <button type="button" class="btn ghost" id="trialNewBtn">Start a trial</button>
      <div id="trialNew" style="display:none"></div>
    </div>
    <p class="q" style="margin:14px 2px 6px">One-day test (the older way)</p>
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
    <button data-s="prn" class="sel">PRN dose</button><button data-s="course">Courses</button><button data-s="sched">Schedule</button><button data-s="stock">Stock</button><button data-s="salts">Salts</button>
  </div>

  <div class="sub sel" id="meds-prn">
    <div id="patchbox"></div>
    <div class="card"><p class="q">&#128138; Today so far</p><div id="prnToday"><p class="hint" style="margin:0">No doses logged today.</p></div></div>
    <p class="hint">Tap every medicine taken now (multi-select), set the time, log once. Repeat doses later with +1.</p>
    <div class="card"><div class="mdt">
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

  <div class="sub" id="meds-salts">
    <p class="hint">The salt and strength of each medicine let RxGuard check it. Suggestions come from the US
      National Library of Medicine. Mark mixtures and supplements as "Not a single drug".</p>
    <div id="saltList"></div>
    <datalist id="saltSug"></datalist>
  </div>

  <div class="sub" id="meds-stock">
    <div class="card" id="ordCard">
      <p class="q">Monthly order - daily medicines</p>
      <p class="hint ord-sub" style="margin:0 2px 8px"></p>
      <div id="ordLines"></div>
      <div class="ordbtns">
        <button type="button" class="btn tiny go" id="ordSend" hidden>Send on WhatsApp</button>
        <button type="button" class="btn tiny ghost" id="ordCopy" hidden>Copy</button>
        <button type="button" class="btn tiny go" id="ordRecv" hidden>Order received</button>
        <button type="button" class="mini" id="ordRecvUndo" hidden>Undo received</button>
      </div>
      <p class="hint" id="ordSkip" style="margin:8px 2px 0"></p>
      <div class="vtm"><span class="lb">Keep stock for</span><input type="number" id="ordDays" min="7" max="120" inputmode="numeric">
        <span class="lb">days</span><button type="button" class="btn tiny ghost" id="ordDaysSave">Set</button></div>
    </div>
    <div class="card" id="sosCard">
      <p class="q">Running low - SOS medicines</p>
      <p class="hint sos-sub" style="margin:0 2px 8px"></p>
      <div id="sosLines"></div>
      <div class="ordbtns">
        <button type="button" class="btn tiny go" id="sosSend" hidden>Send on WhatsApp</button>
        <button type="button" class="btn tiny ghost" id="sosCopy" hidden>Copy</button>
        <button type="button" class="btn tiny go" id="sosRecv" hidden>SOS order received</button>
        <button type="button" class="mini" id="sosRecvUndo" hidden>Undo received</button>
      </div>
      <p class="hint" id="sosSkip" style="margin:8px 2px 0"></p>
    </div>
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
    <button data-s="summary" class="sel">Summary</button><button data-s="reports">Reports</button><button data-s="trends">Trends</button><button data-s="plan">Plan</button>
  </div>
  <div class="seg" data-seg="files">
    <button data-s="vault">Upload</button><button data-s="labs">Labs</button><button data-s="consults">Consults</button>
  </div>

  <div class="sub sel" id="files-summary">
    <div class="card"><div class="rs-head"><p class="q" style="margin:0">Health summary</p>
      <button type="button" class="btn tiny ghost" id="rsPrint">Print / PDF</button></div>
      <p class="hint" id="rsUpd" style="margin:4px 0 0"></p></div>
    <div id="rsBody"></div>
  </div>

  <div class="sub" id="files-reports">
    <a class="scanbtn" href="/scan">&#128247; Scan a report</a>
    <div class="chips" id="rdKinds"></div>
    <div id="rdList"></div>
  </div>

  <div class="sub" id="files-trends">
    <p class="hint">Values exactly as printed. A red dot is a value the laboratory flagged. Tap a test for every result.</p>
    <div id="rtList"></div>
  </div>

  <div class="sub" id="files-plan">
    <p class="hint">Investigations chosen for the way forward. Mark each one done when the report is in.</p>
    <div id="rpList"></div>
  </div>

  <div class="sub" id="files-vault">
        <a class="scanbtn" href="/scan">&#128247; Scan a report with the camera</a>
    <p class="hint" style="margin-top:0">Autocrop, flattening and multi-page PDF, the same scanner the clinic uses.</p>
<p class="hint">New reports, prescriptions, scan photos. PDF / JPG / PNG, up to 12 MB. They appear under Reports and are processed into your record.</p>
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
    <div class="btnrow" style="margin:4px 0 8px"><button type="button" class="btn tiny ghost" id="dvDown">Mark this day as a down day</button></div>
    <div id="dvList"></div>
    <p class="lbl" id="dvMissHd" style="margin-top:14px">Scheduled but not logged &mdash; enter the time it was taken</p>
    <div id="dvMiss"></div>
  </div>
  <div class="card fold" id="downView">
    <button type="button" class="fold-h">
      <span class="ft">Down days</span><span class="fs" id="ddSum">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody" id="ddBody"></div>
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
    <p class="legend"><span class="dot" style="background:var(--mkpain)"></span><b>Pain</b>
      <span class="dot" style="background:var(--mkep)"></span><b>Tea</b>
      <span class="dot" style="background:var(--mkot)"></span><b>Coffee</b></p></div>
  <div class="card"><p class="q">FODMAP load vs symptom days</p><div id="chartFmap"></div>
    <p class="legend"><span class="dot" style="background:var(--teal)"></span><b>Daily FODMAP load</b>
      <span class="dot" style="background:var(--mkpain)"></span><b>Symptom-day mark</b></p></div>
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
  <button data-t="files"><i>&#128194;</i>Records</button>
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
let tab='now'; const seg={log:'day',meals:'meal',meds:'prn',files:'summary'};
let LIB=[], PRN=[], basket=[], libFilter='all', dayProtein=0;
/* GUTLOG_V3271_TARGETJS -- one protein target on the page too. The
   server decides it (the diet plan's, when there is one); 57 is only
   the fallback until the first answer arrives. */
let PROT_TGT=57;
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1700);}
// GUTLOG_V3210_ONEDOSE -- a chip linked to a dose already logged says so,
// and one tap overrules it when it really was a second tablet.
function showSame(list){
  const box=$('#samedose');
  if(!box||!list||!list.length)return;
  box.innerHTML='';
  list.forEach(s=>{
    const row=document.createElement('div');row.className='sdrow';
    const t=document.createElement('span');
    t.textContent=s.label+' \u2014 counted with the '+s.dose_name+' dose at '+s.time;
    const b=document.createElement('button');b.textContent='It was a new dose';
    b.onclick=async()=>{
      try{
        await post('/api/now/dose',{med_id:s.med_id,medicine:s.name,status:'EXTRA',
          reason:s.reason,day:todayISO});
        toast(s.name+' recorded as a new dose');row.remove();
        if(!box.querySelector('.sdrow'))box.classList.remove('show');
        loadNow();
      }catch(err){toast(err.message);}
    };
    row.appendChild(t);row.appendChild(b);box.appendChild(row);
  });
  const x=document.createElement('button');x.className='sdx';x.textContent='OK';
  x.onclick=()=>box.classList.remove('show');
  box.appendChild(x);
  box.classList.add('show');
}
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
  const col=done?'var(--teal)':'var(--rtrk2)';
  return `<button class="ring" data-go="${label}">
   <svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="${R}" fill="none" stroke="var(--rtrk)" stroke-width="4"/>
   <circle cx="20" cy="20" r="${R}" fill="none" stroke="${col}" stroke-width="4" stroke-linecap="round"
    stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 20 20)"/>
   <text x="20" y="25" text-anchor="middle" font-size="15">${ic}</text></svg>
   <span class="rl">${label}</span></button>`;
}
async function loadRings(){
  const s=await jget('/api/summary/'+todayISO);
  dayProtein=s.protein||0;
  if(s.target)PROT_TGT=s.target;
  $('#hdrStreak').textContent=s.streak>0?('&#128293; '+s.streak+'d').replace('&#128293;','🔥'):'';
  const pPct=Math.min(1,(s.protein||0)/(s.target||PROT_TGT));
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
  /* GUTLOG_V3310_FOODLIB -- a food that is not in the list is added with its
     weight, in the same editor the food list uses, and lands in the meal. */
  const nf=$('#ml_newfood');
  if(nf.dataset.open)return;
  nf.innerHTML='';
  nf.style.display=(q&&!list.length)?'block':'none';
  if(q&&!list.length){
    const a=el('button','btn ghost','Add “'+qstr.trim()+'” to your foods, with its weight');
    a.type='button';a.id='nfOpen';
    a.onclick=()=>{nf.dataset.open='1';
      lfEditor(nf,{id:0,item:qstr.trim(),fodmap:'M'},
        async name=>{nf.dataset.open='';$('#ml_search').value='';await loadLib();
          const x=libItem(name);if(x)addToBasket(x);toast('Food added');},
        ()=>{nf.dataset.open='';renderResults($('#ml_search').value||'');});};
    nf.appendChild(a);
  }
}
function addToBasket(x){
  const ex=basket.find(b=>b.item===x.item);
  if(ex){if(ex.g>0){ex.g=0;ex.q=1;}else ex.q++;}
  else basket.push({item:x.item,portion:lfPortionText(x),q:1,g:0,p:x.protein||0,k:x.kcal||0,
    f:x.fibre||0,fm:x.fodmap,w:lfCanWeigh(x),u:x.portion_unit||'g',dry:!!x.weighed_dry,
    pq:x.portion_qty||0,bp:x.b_protein||0,bk:x.b_kcal||0,bf:x.b_fibre||0});
  renderBasket();
}
/* GUTLOG_V3310_FOODLIB -- a row is either a count of portions or a weight.
   A weight is worked out from the per-100 basis, here for the preview and
   again on the server for the record. */
function bkVals(b){
  if(b.g>0)return [b.bp*b.g/100,b.bk*b.g/100,b.bf*b.g/100,b.pq>0?b.g/b.pq:1];
  return [b.q*b.p,b.q*b.k,b.q*b.f,b.q];
}
function renderBasket(){
  const box=$('#ml_basket');
  if(!basket.length){box.innerHTML='<p class="hint" style="margin:0">Nothing added yet - search above or tap a favourite.</p>';
    $('#ml_tot').textContent='';$('#ml_meter').style.display='none';$('#ml_fmw').textContent='';return;}
  box.innerHTML='';
  basket.forEach((b,i)=>{
    const row=document.createElement('div');row.className='bk';
    row.innerHTML=`<span class="fd" style="background:${FMCOL[b.fm]}"></span>
      <span class="nm"></span>
      <button type="button">&minus;</button><span class="qv"></span>
      <button type="button">+</button><button type="button" class="rm">&times;</button>`;
    const nm=row.querySelector('.nm');nm.textContent=b.item;nm.appendChild(el('small','',b.portion||''));
    const qv=row.querySelector('.qv');qv.textContent=b.g>0?'–':String(b.q);
    const btns=row.querySelectorAll('button');
    btns[0].onclick=()=>{if(b.g>0){b.g=0;}else{b.q--;if(b.q<=0)basket.splice(i,1);}renderBasket();};
    btns[1].onclick=()=>{if(b.g>0){b.g=0;}else b.q++;renderBasket();};
    btns[2].onclick=()=>{basket.splice(i,1);renderBasket();};
    if(b.w)row.appendChild(wtLine(b,b.u,b.dry,b.item,()=>{qv.textContent=b.g>0?'–':String(b.q);bkTotals();}));
    else row.appendChild(el('div','bkw','No weight set for this food — by portion only.'));
    box.appendChild(row);
  });
  bkTotals();
}
function bkTotals(){
  let p=0,k=0,f=0,fs=0,nq=0;
  basket.forEach(b=>{const v=bkVals(b);p+=v[0];k+=v[1];f+=v[2];fs+=v[3]*FMAP[b.fm];nq+=v[3];});
  const proj=dayProtein+p;
  $('#ml_tot').innerHTML=`This meal: <b>${p.toFixed(1)} g protein</b> &middot; ${Math.round(k)} kcal &middot; ${f.toFixed(1)} g fibre`;
  const avg=nq?fs/nq:0;
  $('#ml_meter').style.display='block';
  $('#ml_pin').style.left=(avg/2*100)+'%';
  const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
  $('#ml_fmw').innerHTML=`FODMAP load: <b>${lab}</b> &middot; day protein would reach <b>${proj.toFixed(0)}/${PROT_TGT} g</b>`;
}
$('#ml_search').oninput=e=>renderResults(e.target.value);
$('#openFoods').onclick=()=>setSeg('meals','foods');
$('#closeFoods').onclick=()=>setSeg('meals','meal');

/* library manager -- GUTLOG_V3310_FOODLIB. Most used first; each row shows
   one portion with its weight and the values for it, the per-100 basis,
   and where the numbers came from. The editor is lfEditor(), shared with
   the Meals tab's "add a food". */
function lfFmt(v){if(v===null||v===undefined||v==='')return '–';const n=Number(v);
  if(!isFinite(n))return '–';return String(Math.round(n*10)/10);}
function lfIn(v){if(v===null||v===undefined||v==='')return '';const n=Number(v);
  return isFinite(n)?String(Math.round(n*100)/100):'';}
function lfCanWeigh(x){return !!(x&&x.portion_qty>0&&(x.b_protein!=null||x.b_kcal!=null||x.b_fibre!=null));}
function lfPortionText(x){
  const lab=(x.portion||'').trim();
  if(!(x.portion_qty>0))return (lab?lab+' · ':'')+'no weight set';
  const w=lfFmt(x.portion_qty)+' '+(x.portion_unit||'g')+(x.weighed_dry?' dry':'')+(x.portion_est?' (est.)':'');
  return (lab&&!/^[\d.]+\s*(g|ml)$/i.test(lab))?(lab+' · '+w):w;
}
const LF_SRC={USDA:'USDA table',own:'your values',estimated:'estimated'};
function lfSourceText(x){return LF_SRC[x.source]||'not set';}
function renderLibList(){
  const q=($('#lib_search').value||'').trim().toLowerCase();
  const box=$('#lib_list');box.innerHTML='';
  const CATN={A:'Grains & breads',B:'Dals & legumes',C:'Soy & protein',D:'Dairy & fats',E:'Sabzis',F:'Snacks & nuts',G:'Fruit',H:'Drinks & composites'};
  const list=LIB.filter(x=>{
    if(libFilter==='fav'&&!x.fav)return false;
    if(libFilter==='comfort'&&!(x.tags||'').includes('comfort'))return false;
    if(libFilter==='trigger'&&x.status!=='trigger')return false;
    if(q&&!x.item.toLowerCase().includes(q))return false;
    return true;});
  const head=t=>box.appendChild(el('p','cathead',t));
  const used=list.filter(x=>x.uses>0).sort((a,b)=>b.uses-a.uses||a.item.localeCompare(b.item));
  if(used.length){head('Most used');used.forEach(x=>box.appendChild(lfRow(x)));}
  let cur='';
  list.filter(x=>!(x.uses>0)).forEach(x=>{
    if(x.cat!==cur){cur=x.cat;head(CATN[cur]||cur);}
    box.appendChild(lfRow(x));});
  if(!list.length)box.innerHTML='<p class="hint" style="margin:0">No foods match.</p>';
}
function lfRow(x){
  const row=document.createElement('div');row.className='librow';row.dataset.item=x.item;
  const badge=x.status==='cleared'?'<span class="badge b-ok">cleared</span>':
    x.status==='trigger'?'<span class="badge b-bad">trigger</span>':
    x.status==='test'?'<span class="badge b-sus">test</span>':
    (x.tags||'').includes('comfort')?'<span class="badge b-cmf">comfort</span>':'';
  row.innerHTML=`<span class="fd" style="background:${FMCOL[x.fodmap]}"></span><span class="nm"></span>
    <button type="button" class="star ${x.fav?'on':''}">&#9733;</button>`;
  const nm=row.querySelector('.nm');nm.appendChild(document.createTextNode(x.item+' '));
  if(badge)nm.insertAdjacentHTML('beforeend',badge);
  nm.appendChild(el('small','lfport',lfPortionText(x)+' · '+lfFmt(x.protein)+' g protein · '+
    lfFmt(x.kcal)+' kcal · '+lfFmt(x.fibre)+' g fibre'));
  const u=x.portion_unit||'g';
  nm.appendChild(el('small','lfbasis',(lfCanWeigh(x)?('per 100 '+u+(x.weighed_dry?' dry':'')+': '+
    lfFmt(x.b_protein)+' g protein · '+lfFmt(x.b_kcal)+' kcal · '+lfFmt(x.b_fibre)+' g fibre'):
    'no per-100 values yet')+' · '+lfSourceText(x)));
  row.querySelector('.star').onclick=async(ev)=>{ev.stopPropagation();await post('/api/library/'+x.id,{fav:!x.fav});await loadLib();};
  row.onclick=()=>editFood(x);
  return row;
}
$('#lib_search').oninput=renderLibList;
(function(){const box=$('#lib_filters');[['all','All'],['fav','★ Favourites'],['comfort','Comfort'],['trigger','Triggers']].forEach(([k,l])=>{
  const b=document.createElement('button');b.type='button';b.className='chip'+(k==='all'?' sel':'');b.textContent=l;
  b.onclick=()=>{libFilter=k;box.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');renderLibList();};box.appendChild(b);});})();
function editFood(x){
  const e=$('#lib_edit');e.style.display='block';$('#lib_list').style.display='none';$('#lib_addnew').style.display='none';
  $('#lib_filters').style.display='none';$('#lib_search').style.display='none';
  lfEditor(e,x,async()=>{closeEdit();await loadLib();toast('Saved');},closeEdit);
}
function closeEdit(){$('#lib_edit').style.display='none';$('#lib_edit').innerHTML='';$('#lib_list').style.display='block';$('#lib_addnew').style.display='inline-block';$('#lib_filters').style.display='flex';$('#lib_search').style.display='block';}
$('#lib_addnew').onclick=()=>editFood({id:0,item:'',portion:'',fodmap:'M',status:''});
/* One editor for a food, new or old. Weight first, then the values PER 100
   (per 100 g DRY when weighed dry), so a weighed meal scales exactly. A
   food saved before weights existed opens per portion until a weight is set.
   The table fills values only on a tap -- or, for a new food whose name it
   knows and whose values are still blank, once, with the match shown -- and
   any number he changes makes the values his. */
function lfEditor(box,x,done,cancel){
  const isNew=!x.id;
  box.innerHTML='';
  box.appendChild(el('p','q',isNew?'Add a food':'Edit · '+x.item));
  const f=el('div','lfed');box.appendChild(f);
  f.innerHTML='<p class="lbl">Name</p><input type="text" class="lf_item" maxlength="80">'+
    '<p class="lbl">Portion name (optional)</p><input type="text" class="lf_label" maxlength="60" placeholder="1 katori">'+
    '<p class="lbl">Weight of one portion</p><div class="lfw"><input type="number" class="lf_pq" min="0" step="1" inputmode="decimal" aria-label="Weight of one portion"><div class="chips lf_unit"></div></div>'+
    '<label class="lfchk"><input type="checkbox" class="lf_dry"> Weighed dry (dal, rice, poha, oats)</label>'+
    '<label class="lfchk lf_okw" style="display:none"><input type="checkbox" class="lf_ok"> This weight is right (it was estimated)</label>'+
    '<p class="lbl lf_bh"></p>'+
    '<div class="row3"><div><p class="lbl">Protein g</p><input type="number" class="lf_p" min="0" step="0.1" inputmode="decimal"></div>'+
    '<div><p class="lbl">kcal</p><input type="number" class="lf_k" min="0" step="1" inputmode="decimal"></div>'+
    '<div><p class="lbl">Fibre g</p><input type="number" class="lf_f" min="0" step="0.1" inputmode="decimal"></div></div>'+
    '<p class="hint lf_per"></p><p class="hint lf_src"></p>'+
    '<p class="lbl" style="margin-top:10px">Look it up in the food table</p>'+
    '<input type="text" class="lf_q" placeholder="e.g. masoor dal, oats, guava"><div class="lf_hits"></div>'+
    (isNew?'<p class="lbl">Group</p><div class="chips lf_cat"></div>':'')+
    '<p class="lbl" style="margin-top:8px">FODMAP</p><div class="chips lf_fm"></div>'+
    '<p class="lbl" style="margin-top:8px">Status</p><div class="chips lf_st"></div>'+
    '<div class="lfbtn"><button type="button" class="mini lf_save"></button>'+
    (isNew?'':'<button type="button" class="del lf_del">Delete</button>')+
    '<button type="button" class="backlink lf_cx">Cancel</button></div>';
  const q1=s=>f.querySelector(s);
  const I={item:q1('.lf_item'),label:q1('.lf_label'),pq:q1('.lf_pq'),dry:q1('.lf_dry'),ok:q1('.lf_ok'),
    p:q1('.lf_p'),k:q1('.lf_k'),fb:q1('.lf_f'),q:q1('.lf_q')};
  I.item.value=x.item||'';I.label.value=x.portion||'';
  let unit=x.portion_unit||'g',fm=x.fodmap||'M',st=x.status||'',cat='F';
  let fdc=null,fdcDesc='',auto=false,touched=false;
  let mode=(isNew||x.portion_qty>0)?'b':'p';
  I.pq.value=x.portion_qty>0?lfIn(x.portion_qty):'';
  I.dry.checked=!!x.weighed_dry;
  if(x.portion_est&&x.portion_qty>0)q1('.lf_okw').style.display='flex';
  const setVals=v=>{I.p.value=lfIn(v[0]);I.k.value=lfIn(v[1]);I.fb.value=lfIn(v[2]);};
  const rd=()=>[I.p.value,I.k.value,I.fb.value].map(v=>v===''?null:Number(v));
  if(mode==='b'){
    let b=[x.b_protein,x.b_kcal,x.b_fibre];
    if(b.every(v=>v==null)&&x.portion_qty>0)b=[x.protein,x.kcal,x.fibre].map(v=>v==null?null:v*100/x.portion_qty);
    setVals(b);
  }else setVals([x.protein,x.kcal,x.fibre]);
  const draw=()=>{
    const pq=Number(I.pq.value)||0,dry=I.dry.checked?' dry':'';
    q1('.lf_bh').textContent=mode==='b'?('Nutrition per 100 '+unit+dry):
      ('Nutrition per portion'+(pq>0?' — saved per 100 '+unit+' using the weight':''));
    const v=rd();
    q1('.lf_per').textContent=(mode==='b'&&pq>0&&v.some(a=>a!=null))?('One portion ('+lfFmt(pq)+' '+unit+dry+'): '+
      lfFmt(v[0]==null?null:v[0]*pq/100)+' g protein · '+lfFmt(v[1]==null?null:v[1]*pq/100)+' kcal · '+
      lfFmt(v[2]==null?null:v[2]*pq/100)+' g fibre'):'';
    q1('.lf_src').textContent=fdc?('From the USDA table: '+fdcDesc+'. Change any number to make it yours.'):
      (touched?'Your values.':('Source: '+lfSourceText(x)+(x.source_ref?(' — '+x.source_ref):'')));
  };
  const drawUnit=()=>{const u=q1('.lf_unit');u.innerHTML='';['g','ml'].forEach(k=>{
    const c=el('button','chip'+(k===unit?' sel':''),k);c.type='button';c.onclick=()=>{unit=k;drawUnit();draw();};u.appendChild(c);});};
  const hits=q1('.lf_hits');
  const useHit=m=>{mode='b';setVals([m.protein,m.kcal,m.fibre]);fdc=m.fdc;fdcDesc=m.desc;touched=false;auto=true;draw();};
  let seq=0;
  const lookup=async(q,autofill)=>{
    const my=++seq;
    if(!q.trim()){hits.innerHTML='';return;}
    let j;try{j=await jget('/api/foodtable?q='+encodeURIComponent(q)+'&dry='+(I.dry.checked?1:0));}catch(e){return;}
    if(my!==seq)return;
    hits.innerHTML='';
    if(!j.available){hits.appendChild(el('p','hint',j.text||'No food table on this server.'));return;}
    if(!j.matches.length){hits.appendChild(el('p','hint','Nothing in the table by that name — type the values.'));return;}
    j.matches.slice(0,6).forEach(m=>{const b=el('button','lfhit');b.type='button';b.appendChild(el('span','',m.desc));
      b.appendChild(el('small','',lfFmt(m.protein)+' g protein · '+lfFmt(m.kcal)+' kcal · '+
        (m.fibre==null?'fibre not listed':lfFmt(m.fibre)+' g fibre')+' per 100 g'));
      b.onclick=()=>useHit(m);hits.appendChild(b);});
    hits.appendChild(el('p','hint',j.name+' · '+j.licence));
    const top=j.matches[0];
    if(autofill&&top&&top.full&&!touched&&(auto||rd().every(v=>v==null)))useHit(top);
  };
  let tmr=null;
  const later=(fn)=>{clearTimeout(tmr);tmr=setTimeout(fn,250);};
  I.item.oninput=()=>{if(isNew)later(()=>lookup(I.item.value,true));};
  I.q.oninput=()=>later(()=>lookup(I.q.value,false));
  I.pq.oninput=draw;
  I.dry.onchange=()=>{draw();const q=I.q.value||I.item.value;if(q)lookup(q,isNew);};
  [I.p,I.k,I.fb].forEach(i=>i.oninput=()=>{fdc=null;fdcDesc='';touched=true;auto=false;draw();});
  if(isNew){const cb=q1('.lf_cat');[['A','Grains'],['B','Dals'],['C','Soy/protein'],['D','Dairy'],['E','Sabzi'],['F','Snacks'],['G','Fruit'],['H','Drinks']].forEach(([k,l])=>{
    const b=el('button','chip'+(k===cat?' sel':''),l);b.type='button';
    b.onclick=()=>{cat=k;cb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};cb.appendChild(b);});}
  const fb=q1('.lf_fm');Object.keys(FMAP).forEach(k=>{const b=document.createElement('button');b.type='button';b.className='chip'+(k===fm?' sel':'');
    b.innerHTML=`<span class="fd" style="background:${FMCOL[k]}"></span>${k}`;b.onclick=()=>{fm=k;fb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fb.appendChild(b);});
  const sb=q1('.lf_st');[['','library'],['cleared','cleared'],['trigger','trigger'],['test','test']].forEach(([k,l])=>{
    const b=el('button','chip'+(k===st?' sel':''),l);b.type='button';
    b.onclick=()=>{st=k;sb.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};sb.appendChild(b);});
  const sv=q1('.lf_save');sv.textContent=isNew?'Add food':'Save changes';
  sv.onclick=async()=>{
    const item=I.item.value.trim();if(!item)return toast('Name the food.');
    const pq=Number(I.pq.value)||0;
    if(isNew&&!(pq>0))return toast('Set its weight — grams or ml for one portion.');
    const v=rd();
    if(v.some(a=>a!=null&&(!isFinite(a)||a<0)))return toast('Numbers only, and not negative.');
    const body={item,portion:I.label.value.trim(),fodmap:fm,status:st,portion_qty:pq>0?pq:'',
      portion_unit:unit,weighed_dry:I.dry.checked,weight_ok:I.ok.checked};
    if(pq>0){const b=mode==='b'?v:v.map(a=>a==null?null:Math.round(a*100/pq*100)/100);
      body.b_protein=b[0];body.b_kcal=b[1];body.b_fibre=b[2];}
    else{body.protein=v[0];body.kcal=v[1];body.fibre=v[2];}
    if(fdc)body.fdc=fdc;
    try{
      if(isNew){body.cat=cat;await post('/api/library',body);}else await post('/api/library/'+x.id,body);
      await done(item);
    }catch(er){toast(er.message);}
  };
  if(!isNew)q1('.lf_del').onclick=async()=>{if(!confirm('Delete '+x.item+'?'))return;
    await post('/api/delete/library/'+x.id,{});cancel();await loadLib();toast('Deleted');};
  q1('.lf_cx').onclick=()=>cancel();
  drawUnit();draw();
  if(x.item)lookup(x.item,isNew);
}

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
    d.innerHTML=`<b></b> &times;${r.times.length}<span class="tms"></span>
      <button type="button" class="p1">+1 dose</button>`;
    d.querySelector('b').textContent=r.medicine;
    /* GUTLOG_V3310_FOODLIB -- each time is a button: tap it to change it. */
    const tms=d.querySelector('.tms');
    r.times.forEach((t,i)=>tms.appendChild(teBtn({table:'doses',id:(r.ids||[])[i],day:r.day||todayISO,
      time:t,title:r.medicine,row:d,after:loadPRNToday})));
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
  s+=`<text x="${P}" y="10" font-size="9" fill="var(--muted)">max ${max}</text></svg>`;
  return s;
}
function svgBars(data,key,col,h=110){
  if(!data.length)return '<p class="hint" style="margin:0">No data yet.</p>';
  const W=320,P=6,n=data.length,bw=Math.max(2,(W-2*P)/n-2);let max=1;data.forEach(d=>{if(d[key]>max)max=d[key];});
  let s=`<svg class="chart" viewBox="0 0 ${W} ${h}">`;
  data.forEach((d,i)=>{const x=P+(W-2*P)*i/n,bh=(h-2*P)*(d[key]/max);
    s+=`<rect x="${x.toFixed(1)}" y="${(h-P-bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${col}"/>`;});
  s+=`<text x="${P}" y="10" font-size="9" fill="var(--muted)">max ${max}</text></svg>`;return s;
}
/* ---------- RECORDS (GUTLOG_V380_RECORDS) ---------- */
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
function fmtDay(d){if(!d)return '';const p=d.split('-');const M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return p.length===3?(p[2]+'-'+M[+p[1]-1]+'-'+p[0]):d;}
function spark(points,w,h){
  const nums=points.filter(p=>p[1]!==null&&p[1]!==undefined);
  if(nums.length<2)return null;
  const xs=nums.map(p=>Date.parse(p[0])),ys=nums.map(p=>p[1]);
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const X=v=>4+(x1===x0?0:(v-x0)/(x1-x0))*(w-8),Y=v=>h-4-(y1===y0?0.5:(v-y0)/(y1-y0))*(h-8);
  const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 '+w+' '+h);svg.setAttribute('class','rt-spark');
  const pl=document.createElementNS(ns,'polyline');
  pl.setAttribute('points',nums.map((p,i)=>X(xs[i]).toFixed(1)+','+Y(ys[i]).toFixed(1)).join(' '));
  pl.setAttribute('fill','none');pl.setAttribute('stroke','var(--teal)');pl.setAttribute('stroke-width','1.6');svg.appendChild(pl);
  nums.forEach((p,i)=>{const c=document.createElementNS(ns,'circle');c.setAttribute('cx',X(xs[i]).toFixed(1));
    c.setAttribute('cy',Y(ys[i]).toFixed(1));c.setAttribute('r',p[2]?'3':'2');c.setAttribute('fill',p[2]?'var(--mkpain)':'var(--teal)');svg.appendChild(c);});
  return svg;
}
async function loadRecSummary(){
  const j=await jget('/api/records/summary');const box=$('#rsBody');box.innerHTML='';
  const P=j.profile||{};
  $('#rsUpd').textContent=(P.updated?('Record reviewed '+fmtDay(P.updated)+' · '):'')+j.docs+' reports on file · medicines and vitals are live from GutLog';
  if(j.unchecked)box.appendChild(el('p','hint rs-auto',j.unchecked+' report'+(j.unchecked>1?'s were':' was')+' read automatically - a glance under Reports confirms '+(j.unchecked>1?'them.':'it.')));
  function card(title){const c=el('div','card rs-card');c.appendChild(el('p','q',title));box.appendChild(c);return c;}
  const m=card('Medicines now');
  if(!j.meds.length)m.appendChild(el('p','hint','No scheduled medicines.'));
  j.meds.forEach(x=>{const r=el('div','rs-row');r.appendChild(el('b',null,x.name));
    r.appendChild(el('span','rs-meta',[x.molecule,x.strength,x.dose_text,x.slot.toLowerCase()].filter(Boolean).join(' · ')));m.appendChild(r);});
  if(j.prn.length)m.appendChild(el('p','hint','As needed, last 30 days: '+j.prn.map(x=>x.name+' ×'+x.n).join(', ')));
  if((P.precautions||[]).length){const c=card('Precautions that apply to you');
    P.precautions.forEach(x=>{const r=el('div','rs-prec '+(x.flag||''));
      if(x.flag)r.appendChild(el('span','rs-flag '+x.flag,x.flag));r.appendChild(el('b',null,x.title));
      r.appendChild(el('p','rs-meta',x.text));c.appendChild(r);});}
  if(j.vitals.length){const c=card('Recent vitals');const t=el('table','rs-tab');
    j.vitals.forEach(v=>{const tr=el('tr');[fmtDay(v.day)+' '+(v.vtime||''),(v.sys?v.sys+'/'+v.dia:''),(v.pulse?'pulse '+v.pulse:''),
      (v.temp?v.temp+'°':''),(v.weight?v.weight+' kg':'')].forEach(s=>tr.appendChild(el('td',null,s)));t.appendChild(tr);});c.appendChild(t);}
  if(j.labs.length){const c=card('Latest key results');const t=el('table','rs-tab');
    j.labs.forEach(x=>{const tr=el('tr'+'');const a=el('td',null,x.test);const b=el('td',x.flag?'rs-hi':null,x.value+(x.unit?' '+x.unit:''));
      const d=el('td','rs-meta',fmtDay(x.day));tr.appendChild(a);tr.appendChild(b);tr.appendChild(d);t.appendChild(tr);});
    c.appendChild(t);c.appendChild(el('p','hint','Red = flagged by the laboratory. Full series under Trends.'));}
  if((P.problems||[]).length){const c=card('Active problems');
    P.problems.forEach(x=>{const r=el('div','rs-row');r.appendChild(el('b',null,x.name));
      r.appendChild(el('span','rs-meta',[x.since,x.status].filter(Boolean).join(' · ')));c.appendChild(r);});}
  if((P.resolved||[]).length){const c=card('Resolved or excluded');
    P.resolved.forEach(x=>{const r=el('div','rs-row');r.appendChild(el('b',null,x.name));r.appendChild(el('span','rs-meta',x.status||''));c.appendChild(r);});}
  const pc=card('Investigation plan');pc.appendChild(el('p',null,j.plan.done+' of '+j.plan.total+' done.'));
  const pb=el('button','btn tiny ghost','Open the plan');pb.type='button';pb.onclick=()=>setSeg('files','plan');pc.appendChild(pb);
  if((P.missing||[]).length){const c=card('Documents still missing');c.appendChild(el('p','rs-meta',P.missing.join(' · ')));}
  if(j.master.length){const c=card('Full narrative record');j.master.forEach(x=>{const a=el('a','rs-link',x.title);
    a.href='/rec/doc/'+x.id;a.target='_blank';c.appendChild(a);});}
  if(P.note)box.appendChild(el('p','hint',P.note));
}
let recKind='All';
async function loadRecDocs(){
  const j=await jget('/api/records/docs');const all=j.uploads.concat(j.docs);
  const kinds=['All'];all.forEach(d=>{if(kinds.indexOf(d.kind)<0)kinds.push(d.kind);});
  const kb=$('#rdKinds');kb.innerHTML='';
  kinds.forEach(k=>{const c=el('div','chip'+(k===recKind?' sel':''),k);c.onclick=()=>{recKind=k;loadRecDocs();};kb.appendChild(c);});
  const box=$('#rdList');box.innerHTML='';let year='';
  const list=all.filter(d=>recKind==='All'||d.kind===recKind).sort((a,b)=>(b.day||'').localeCompare(a.day||''));
  if(!list.length){box.appendChild(el('p','hint','Nothing here yet.'));return;}
  list.forEach(d=>{const y=(d.day||'').slice(0,4);
    if(y!==year){year=y;box.appendChild(el('p','rd-year',y||'Undated'));}
    const r=el('div','rd-row'+(d.status==='inbox'?' inbox':''));
    const top=el('div','rd-top');top.appendChild(el('span','rd-day',fmtDay(d.day)));top.appendChild(el('span','rd-kind',d.kind));
    r.appendChild(top);r.appendChild(el('b','rd-title',d.title));
    if(d.source)r.appendChild(el('span','rs-meta',d.source));
    if(d.finding){const f=el('p','rd-find',d.finding);f.onclick=()=>f.classList.toggle('open');r.appendChild(f);}
    if(d.origin==='auto'&&!d.checked){top.appendChild(el('span','rd-auto','machine-read'));
      const ok=el('button','btn tiny rd-ok','Looks right');ok.type='button';
      ok.onclick=async()=>{try{await post('/api/records/doc/'+d.id+'/checked',{});loadRecDocs();}catch(e){toast(e.message);} };
      r.appendChild(ok);}
    if(d.has_file){const a=el('a','rs-link','Open report');a.href=d.vault?('/file/'+d.id):('/rec/doc/'+d.id);a.target='_blank';r.appendChild(a);}
    box.appendChild(r);});
}
async function loadRecTrends(){
  const j=await jget('/api/records/labs');const box=$('#rtList');box.innerHTML='';
  if(!j.tests.length){box.appendChild(el('p','hint','No results imported yet.'));return;}
  let sec='';let keyDone=false;
  j.tests.forEach(t=>{
    const label=t.key?'Key results':t.section;
    if(label!==sec){sec=label;box.appendChild(el('p','rd-year',label));}
    const r=el('div','rt-row');const head=el('div','rt-head');
    const nm=el('div','rt-name');nm.appendChild(el('b',null,t.test));
    nm.appendChild(el('span','rs-meta',t.n+' result'+(t.n>1?'s':'')+' · last '+fmtDay(t.last_day)));
    head.appendChild(nm);const sv=spark(t.points,92,30);if(sv)head.appendChild(sv);
    head.appendChild(el('span','rt-last'+(t.last_flag?' rs-hi':''),t.last_value));
    r.appendChild(head);
    const det=el('div','rt-det');det.hidden=true;r.appendChild(det);
    head.onclick=async()=>{if(!det.hidden){det.hidden=true;return;}
      const s=await jget('/api/records/labs?test='+encodeURIComponent(t.test));det.innerHTML='';
      const tb=el('table','rs-tab');s.series.slice().reverse().forEach(x=>{const tr=el('tr');
        tr.appendChild(el('td',null,fmtDay(x.day)));tr.appendChild(el('td',x.flag?'rs-hi':null,x.value));
        tr.appendChild(el('td','rs-meta',(x.lab||'')+(x.origin==='auto'?' · auto':'')));tb.appendChild(tr);});
      det.appendChild(tb);if(t.ref||t.unit)det.appendChild(el('p','hint',[t.unit,t.ref?('ref '+t.ref):''].filter(Boolean).join(' · ')));
      det.hidden=false;};
    box.appendChild(r);});
}
async function loadRecPlan(){
  const j=await jget('/api/records/plan');const box=$('#rpList');box.innerHTML='';
  if(!j.items.length){box.appendChild(el('p','hint','No plan loaded yet.'));return;}
  j.items.forEach(x=>{const r=el('div','card rp-row'+(x.status==='done'?' done':''));
    const h=el('div','rp-head');h.appendChild(el('span','rp-n',String(x.pos)));h.appendChild(el('b',null,x.test));r.appendChild(h);
    if(x.why)r.appendChild(el('p','rs-meta',x.why));
    if(x.timing)r.appendChild(el('p','rp-when',x.timing));
    const b=el('button','btn tiny'+(x.status==='done'?' ghost':''),x.status==='done'?('Done '+fmtDay(x.done_day)+' · undo'):'Mark done');b.type='button';
    b.onclick=async()=>{try{await post('/api/records/plan/'+x.id,{status:x.status==='done'?'planned':'done',done_day:todayISO});
      loadRecPlan();}catch(e){toast(e.message);} };
    r.appendChild(b);box.appendChild(r);});
}
function loadRecords(s){
  if(s==='summary')loadRecSummary();
  if(s==='reports')loadRecDocs();
  if(s==='trends')loadRecTrends();
  if(s==='plan')loadRecPlan();
}
$('#rsPrint').onclick=()=>window.print();

/* ---------- SALTS, MEDICINE STATUS, ACTIVITY (GUTLOG_V370) ---------- */
async function loadMedStatus(){
  const box=$('#nowMedStatus');if(!box)return;
  try{
    const j=await jget('/api/medstatus');
    box.innerHTML='';
    const parts=[];
    if(j.need_salt)parts.push([j.need_salt+(j.need_salt>1?' medicines need':' medicine needs')+' a salt',()=>{switchTab('meds');setSeg('meds','salts');}]);
    if(j.rx&&j.rx.drafts)parts.push([j.rx.drafts+' waiting for your review in RxGuard',()=>window.open('https://rx.dr-manoj.in/kb','_blank')]);
    if(j.rx&&j.rx.pairs)parts.push([j.rx.pairs+' interaction'+(j.rx.pairs>1?'s':'')+' to review in RxGuard',()=>window.open('https://rx.dr-manoj.in/kb','_blank')]);
    if(j.rx&&j.rx.red)parts.push(['RxGuard shows '+j.rx.red+' RED',()=>window.open('https://rx.dr-manoj.in/astaken','_blank')]);
    if(!parts.length)return;
    const d=document.createElement('div');
    /* GUTLOG_V3180_HONEST -- red only when RxGuard has a RED it can stand
       behind, which from RxGuard v1.7.0 means one built out of medicines
       actually taken. Everything else this banner carries is housekeeping --
       a salt to fill in, a draft to review -- and housekeeping painted amber
       every morning is how a real alert stops being seen. */
    d.className='stockalert medstat '+(j.rx&&j.rx.red?'red':'info');
    d.innerHTML='<b>Medicines</b><span class="ml"></span>';
    const ml=d.querySelector('.ml');
    parts.forEach(p=>{const a=document.createElement('button');a.type='button';a.className='mlink';
      a.textContent=p[0];a.onclick=p[1];ml.appendChild(a);});
    box.appendChild(d);
  }catch(e){}
}
function saltGuess(name){
  /* "Brand (salt 135)" -> salt, 135 mg;  "Salt 20" -> salt, 20 mg. A guess only: shown, never saved unasked. */
  let m=/\(([a-z][a-z \-]+?)\s*([0-9.\/]+)?\s*(mg|mcg|g)?\)/i.exec(name);
  if(!m)m=/^([a-z][a-z\-]+)\s+([0-9.\/]+)\s*(mg|mcg|g)?$/i.exec(name.trim());
  if(!m)return ['',''];
  return [m[1].trim().toLowerCase(), m[2]?(m[2]+' '+(m[3]||'mg')):''];
}
async function loadSalts(){
  const j=await jget('/api/salts');
  const box=$('#saltList');box.innerHTML='';
  j.meds.forEach(m=>{
    const w=document.createElement('div');w.className='strow'+(m.needs?' lv-amber':'');
    w.innerHTML='<div class="sh"><b></b><span class="sq"></span></div>'+
      '<div class="vtm"><input class="sm" placeholder="salt, e.g. loratadine" list="saltSug" autocomplete="off">'+
      '<input class="st" placeholder="strength"></div>'+
      '<div class="sb"><button type="button" class="btn tiny go">Save</button>'+
      '<button type="button" class="btn tiny ghost ns"></button></div>';
    w.querySelector('.sh b').textContent=m.name;
    w.querySelector('.sq').textContent=m.no_salt?'not a single drug':(m.needs?'needs a salt':'');
    const sm=w.querySelector('.sm'),st=w.querySelector('.st');
    sm.value=m.molecule||'';st.value=m.strength||'';
    if(m.needs&&!m.molecule){const g=saltGuess(m.name);if(g[0]){sm.value=g[0];if(!st.value)st.value=g[1];
      w.querySelector('.sq').textContent='check the guess, then Save';} }
    let tmr=null;
    sm.oninput=()=>{
      clearTimeout(tmr);const q=sm.value.split('+').pop().trim();if(q.length<3)return;
      tmr=setTimeout(async()=>{
        try{const s=await jget('/api/salt/suggest?q='+encodeURIComponent(q));
          const dl=$('#saltSug');dl.innerHTML='';
          (s.suggestions||[]).forEach(x=>{const o=document.createElement('option');o.value=x;dl.appendChild(o);});
        }catch(e){}
      },350);
    };
    const ns=w.querySelector('.ns');ns.textContent=m.no_salt?'It is a single drug':'Not a single drug';
    w.querySelector('.go').onclick=async()=>{
      try{await post('/api/salt',{med_id:m.id,molecule:sm.value,strength:st.value,no_salt:0});
        toast('Saved '+m.name);loadSalts();loadMedStatus();}
      catch(err){toast(err.message);}
    };
    ns.onclick=async()=>{
      try{await post('/api/salt',{med_id:m.id,molecule:sm.value,strength:st.value,no_salt:m.no_salt?0:1});
        loadSalts();loadMedStatus();}
      catch(err){toast(err.message);}
    };
    box.appendChild(w);
  });
}
const ACT=[['walk','Walk'],['treadmill','Treadmill'],['cycle_road','Cycling (road)'],
           ['cycle_static','Cycling (static)'],['meditation','Meditation'],
           ['ot_day','Operating day']];
const ACT_LOAD=['ot_day'];
const ACT_HOURS=[2,4,6,8,10];
function buildActTiles(){
  const box=$('#actTiles');if(!box||box.dataset.built)return;box.dataset.built='1';
  ACT.forEach(a=>{
    const k=a[0],label=a[1];
    const isLoad=ACT_LOAD.indexOf(k)>=0;
    const w=document.createElement('div');w.className='ptile act'+(isLoad?' load':'');w.dataset.k=k;
    w.innerHTML='<button type="button" class="ph"><span class="pn"></span><span class="pv"></span></button>'+
      '<div class="pscore"><div class="chips am"></div><div class="chips ai"></div>'+
      '<button type="button" class="btn primary as">Save</button></div>';
    w.querySelector('.pn').textContent=label;
    const st={min:null,int:''};
    const am=w.querySelector('.am'),ai=w.querySelector('.ai');
    (isLoad?ACT_HOURS:[10,15,20,30,45,60]).forEach(v=>{
      const b=document.createElement('div');b.className='chip num';
      b.textContent=isLoad?(v+' h'):v;
      b.onclick=()=>{st.min=isLoad?v*60:v;[...am.children].forEach(c=>c.classList.toggle('sel',c===b));
        w.querySelector('.pv').textContent=isLoad?(v+' h on your legs'):(v+' min');};
      am.appendChild(b);
    });
    if(k!=='meditation'&&!isLoad){
      ['Easy','Moderate','Hard'].forEach(v=>{
        const b=document.createElement('div');b.className='chip';b.textContent=v;
        b.onclick=()=>{st.int=(st.int===v?'':v);[...ai.children].forEach(c=>c.classList.toggle('sel',c.textContent===st.int));};
        ai.appendChild(b);
      });
    }else ai.remove();
    w.querySelector('.ph').onclick=()=>w.classList.toggle('open');
    w.querySelector('.as').onclick=async()=>{
      if(!st.min){toast('Pick the minutes');return;}
      try{
        await post('/api/activity',{kind:k,minutes:st.min,intensity:st.int,day:todayISO});
        toast(isLoad?('Operating day logged, '+(st.min/60)+' h on your legs')
                    :('Logged '+label.toLowerCase()+', '+st.min+' min'));
        st.min=null;st.int='';w.classList.remove('open');w.querySelector('.pv').textContent='';
        w.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));
        loadActivity();
      }catch(err){toast(err.message);}
    };
    box.appendChild(w);
  });
}
async function loadActivity(){
  buildActTiles();
  const el=$('#actList');if(!el)return;
  const j=await jget('/api/activity?day='+todayISO);
  const s=j.summary||{};
  const lh=s.load_minutes?(Math.round(s.load_minutes/6)/10):0;
  $('#actSum').textContent=(s.minutes?(s.minutes+' min'):(lh?'no exercise':'none yet'))+
    (lh?(' · '+lh+' h on legs'):'')+
    (s.steps?(' · '+Number(s.steps).toLocaleString('en-IN')+' steps'):'');
  el.innerHTML='';
  (j.items||[]).forEach(i=>{
    const row=document.createElement('div');row.className='exrow'+(i.load?' load':'');
    row.innerHTML='<span class="t"></span><span class="m"></span>';
    row.querySelector('.t').textContent=i.time||'';
    const bits=[];
    if(i.load){
      bits.push(i.label+' '+(Math.round(i.minutes/6)/10)+' h on your legs');
      bits.push('load, not exercise');
    } else {
      bits.push((i.source==='watch'?'⌚ ':'')+i.label+' '+i.minutes+' min');
    }
    if(i.distance_km)bits.push((Math.round(i.distance_km*10)/10)+' km');
    if(i.intensity)bits.push(i.intensity.toLowerCase());
    if(i.confirmed)bits.push('watch-confirmed');
    row.querySelector('.m').textContent=bits.join(' · ');
    if(i.source==='manual'&&i.id){
      const u=document.createElement('button');u.type='button';u.className='btn tiny u';u.textContent='Undo';
      u.onclick=async()=>{await post('/api/activity/undo/'+i.id,{});toast('Removed');loadActivity();};
      row.appendChild(u);
    }
    el.appendChild(row);
  });
  const wt=$('#actWatch');
  wt.textContent=j.watch?(j.watch.ok?'Watch data from FitLog is included.':'Watch data not reachable right now.'):'';
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
  loadOrder();
  stockCache=j.rows;
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
  if(!r.trackable){sq.textContent='';ss.textContent=r.why;w.querySelector('.sf').remove();
    if(r.variants&&r.variants.length){const b=document.createElement('button');b.type='button';
      b.className='btn tiny ghost';b.textContent='Link strengths';b.onclick=()=>linkForm(w,r);
      w.querySelector('.sb').appendChild(b);}
    return w;}
  if(r.tracked){
    sq.textContent=fmtQ(r.current)+' left';
    const bits=[modeTxt];
    if(r.per_day>0)bits.push(fmtQ(r.per_day)+'/day');
    if(r.why)bits.push(r.why);
    else if(r.days_left!=null)bits.push('about '+Math.floor(r.days_left)+' days');
    if(packTxt(r))bits.push(packTxt(r));
    if(r.linked_from)bits.push('from '+r.linked_from);
    ss.textContent=bits.join(' · ');
  }else{
    sq.textContent='';
    ss.textContent=[modeTxt,'not counted yet',packTxt(r)].filter(Boolean).join(' · ');
  }
  const sf=w.querySelector('.sf'),inp=sf.querySelector('input'),go=sf.querySelector('button');
  let act='';
  const open=(a,val,ph)=>{act=a;sf.hidden=false;inp.value=val;inp.placeholder=ph;inp.focus();};
  const mk=(txt,fn)=>{const b=document.createElement('button');b.type='button';b.className='btn tiny ghost';
    b.textContent=txt;b.onclick=fn;sb.appendChild(b);};
  mk(r.tracked?'Count':'Set count',()=>open('count','','how many left'));
  mk('Pack',()=>packForm(w,r));
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

/* ---------- MONTHLY ORDER (GUTLOG_V3190_ORDER) ----------
   The order tops stock up to the target, counted from what is expected to
   be left on the 1st. Send and copy act on the text already on screen, in
   the tap itself -- a phone drops clipboard and new-window rights once an
   await has passed -- and save the order behind it. */
let ordData=null;
function ordEsc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);}
function packTxt(r){
  const bits=[];
  if(r.pack_size>0)bits.push((r.pack_type||'pack')+' of '+r.pack_size);
  if(r.keep_units>0)bits.push('keep '+fmtQ(r.keep_units));
  return bits.join(', ');
}
async function loadOrderDue(){
  const box=$('#nowOrder');if(!box)return;
  try{
    const j=await jget('/api/order');
    box.innerHTML='';
    const add=(lb,txt)=>{const d=document.createElement('div');
      d.className='stockalert amber';d.innerHTML='<b></b><span></span>';
      d.querySelector('b').textContent=lb;d.querySelector('span').textContent=txt;
      d.onclick=()=>{switchTab('meds');setSeg('meds','stock');};box.appendChild(d);};
    if(j.due)add('Order',j.label+' order is due: '+j.lines.length+
      (j.lines.length===1?' medicine':' medicines')+'. Tap to send.');
    const sl=(j.sos&&j.sos.lines)||[];
    if(sl.length)add('Low','Running low: '+sl.map(l=>l.name).join(', ')+'. Tap to send.');
  }catch(e){}
}
function ordCopyText(t){
  if(navigator.clipboard&&window.isSecureContext){
    return navigator.clipboard.writeText(t).then(()=>true,()=>ordCopyOld(t));
  }
  return Promise.resolve(ordCopyOld(t));
}
function ordCopyOld(t){
  const a=document.createElement('textarea');a.value=t;a.setAttribute('readonly','');
  a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();
  let ok=false;try{ok=document.execCommand('copy');}catch(e){}
  a.remove();return ok;
}
async function ordSave(){
  try{await post('/api/order/save',{});}catch(err){toast(err.message);}
  loadOrder();loadOrderDue();
}
async function loadOrder(){
  const card=$('#ordCard');if(!card)return;
  let j;try{j=await jget('/api/order');}catch(e){return;}
  ordData=j;
  loadSos(j);
  const s=j.saved,got=s&&s.status==='RECEIVED';
  $('#ordDays').value=j.days;
  const sub=card.querySelector('.ord-sub');
  if(got)sub.textContent=j.label+' order received '+s.received_at+' and added to stock.';
  else if(s)sub.textContent=j.label+' order sent '+s.created.slice(0,10)+
    '. Sending again replaces it. Tap Order received when it arrives.';
  else if(j.lines.length)sub.textContent='For '+j.label+': tops each medicine up to '+j.days+
    ' days, counted from what will be left on the 1st.';
  else sub.textContent='Nothing to order for '+j.label+'. Stock covers '+j.days+' days.';
  const lines=got?s.lines:j.lines;
  $('#ordLines').innerHTML=lines.map((l,i)=>'<div class="ordl"><b>'+(i+1)+'. '+ordEsc(l.name)+
    '</b><span class="oq">'+ordEsc(l.qty)+'</span><div class="od">on the 1st about '+
    fmtQ(Math.max(0,l.expected))+', target '+fmtQ(l.target)+
    (l.basis==='keep'?' (keep on hand)':(l.basis==='usage'?' (from use)':''))+
    (l.pack_size>0?'':' - set its pack size')+'</div></div>').join('');
  const can=!got&&j.lines.length>0;
  $('#ordSend').hidden=!can;$('#ordCopy').hidden=!can;
  $('#ordRecv').hidden=!(s&&s.status==='OPEN');
  $('#ordRecvUndo').hidden=!got;
  $('#ordSkip').textContent=j.skipped.length?('Not in the order: '+
    j.skipped.map(x=>x.name+' ('+x.why+')').join(' · ')):'';
  $('#ordSend').onclick=()=>{
    window.open('https://wa.me/?text='+encodeURIComponent(ordData.text),'_blank');
    ordSave();
  };
  $('#ordCopy').onclick=()=>{
    ordCopyText(ordData.text).then(ok=>toast(ok?'Copied. Paste it in WhatsApp.':'Copy failed - use Send on WhatsApp'));
    ordSave();
  };
  $('#ordRecv').onclick=async()=>{
    if(!confirm('Add everything in this order to stock?'))return;
    try{const r=await post('/api/order/received',{id:s.id});
      toast('Added to stock: '+r.n+(r.n===1?' medicine':' medicines'));
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
  $('#ordRecvUndo').onclick=async()=>{
    if(!confirm('Take this order back out of stock?'))return;
    try{await post('/api/order/received/undo',{id:s.id});toast('Received undone');
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
  $('#ordDaysSave').onclick=async()=>{
    try{await post('/api/order/days',{days:$('#ordDays').value});toast('Target saved');loadOrder();loadOrderDue();}
    catch(err){toast(err.message);}
  };
}
function packForm(w,r){
  const old=w.querySelector('.pk');if(old){old.remove();return;}
  const types=(ordData&&ordData.pack_types)||['strip','bottle','pouch','box','tube','sachet','vial','pack'];
  const f=document.createElement('div');f.className='pk';
  f.innerHTML='<div class="row3"><div><p class="lbl">Pack of</p><input type="number" min="0" max="1000" inputmode="numeric" class="pk-n"></div>'+
    '<div><p class="lbl">Type</p><select class="pk-t"><option value="">-</option>'+
    types.map(t=>'<option value="'+t+'">'+t+'</option>').join('')+'</select></div>'+
    '<div><p class="lbl">Keep (SOS)</p><input type="number" min="0" step="1" inputmode="numeric" class="pk-k"></div></div>'+
    '<p class="hint" style="margin:6px 2px 0">Keep = how many to hold for a medicine taken only when needed. Leave 0 for a daily one.</p>'+
    '<button type="button" class="btn tiny go">Save pack</button>';
  f.querySelector('.pk-n').value=r.pack_size||'';
  f.querySelector('.pk-t').value=r.pack_type||'';
  f.querySelector('.pk-k').value=r.keep_units||'';
  f.querySelector('button').onclick=async()=>{
    try{await post('/api/stock/pack',{med_id:r.med_id,pack_size:f.querySelector('.pk-n').value||0,
      pack_type:f.querySelector('.pk-t').value,keep:f.querySelector('.pk-k').value||0});
      toast('Pack saved');loadStock();}
    catch(err){toast(err.message);}
  };
  w.appendChild(f);
}

/* ---------- TWO PIPELINES (GUTLOG_V3200_PIPES) ----------
   SOS medicines have their own list, raised whenever one falls below a
   third of its keep figure; the monthly card carries daily medicines only.
   A medicine whose strengths vary is linked, strength by strength, to the
   products it is taken from, so those products are counted and ordered. */
let stockCache=[];
function loadSos(j){
  const card=$('#sosCard');if(!card)return;
  const p=j.sos||{lines:[],ordered:[],nokeep:[],uncounted:[]};
  const s=p.saved,open=s&&s.status==='OPEN',got=s&&s.status==='RECEIVED';
  const sub=card.querySelector('.sos-sub');
  const bits=[];
  if(p.lines.length)bits.push(p.lines.length+(p.lines.length===1?' medicine is':' medicines are')+' below a third of its keep figure.');
  else bits.push('Nothing running low.');
  if(open)bits.push('On order since '+s.created.slice(0,10)+': '+s.lines.map(l=>l.name).join(', ')+'.');
  sub.textContent=bits.join(' ');
  $('#sosLines').innerHTML=p.lines.map((l,i)=>'<div class="ordl"><b>'+(i+1)+'. '+ordEsc(l.name)+
    '</b><span class="oq">'+ordEsc(l.qty)+'</span><div class="od">'+fmtQ(l.current)+' left, keep '+
    fmtQ(l.keep)+(l.pack_size>0?'':' - set its pack size')+'</div></div>').join('');
  $('#sosSend').hidden=!p.lines.length;$('#sosCopy').hidden=!p.lines.length;
  $('#sosRecv').hidden=!open;$('#sosRecvUndo').hidden=!got;
  const sk=[];
  if(p.uncounted.length)sk.push('Not counted yet: '+p.uncounted.join(', '));
  if(p.nokeep.length)sk.push('No keep figure (tap Pack): '+p.nokeep.join(', '));
  $('#sosSkip').textContent=sk.join(' · ');
  const save=async()=>{try{await post('/api/order/sos/save',{});}catch(err){toast(err.message);}
    loadOrder();loadOrderDue();};
  $('#sosSend').onclick=()=>{window.open('https://wa.me/?text='+encodeURIComponent(p.text),'_blank');save();};
  $('#sosCopy').onclick=()=>{
    ordCopyText(p.text).then(ok=>toast(ok?'Copied. Paste it in WhatsApp.':'Copy failed - use Send on WhatsApp'));
    save();
  };
  $('#sosRecv').onclick=async()=>{
    if(!confirm('Add everything on the SOS order to stock?'))return;
    try{const r=await post('/api/order/received',{id:s.id});
      toast('Added to stock: '+r.n+(r.n===1?' medicine':' medicines'));
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
  $('#sosRecvUndo').onclick=async()=>{
    if(!confirm('Take the last SOS order back out of stock?'))return;
    try{await post('/api/order/received/undo',{id:s.id});toast('Received undone');
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
}
function linkForm(w,r){
  const old=w.querySelector('.pk');if(old){old.remove();return;}
  const opts=stockCache.filter(x=>x.trackable&&x.med_id!==r.med_id);
  const cur={};(r.links||[]).forEach(l=>{cur[String(l.variant).toLowerCase()]=l;});
  const f=document.createElement('div');f.className='pk';
  f.innerHTML='<p class="hint" style="margin:0 2px 6px">Which pack each strength comes out of. '+
    'Two capsules of one pack = units 2.</p>'+
    r.variants.map((v,i)=>'<div class="row3 lk" data-v="'+ordEsc(v)+'"><div><p class="lbl">Strength</p><p class="lkv">'+ordEsc(v)+'</p></div>'+
      '<div><p class="lbl">Taken from</p><select class="lk-m"><option value="">-</option>'+
      opts.map(o=>'<option value="'+o.med_id+'">'+ordEsc(o.name)+'</option>').join('')+'</select></div>'+
      '<div><p class="lbl">Units</p><input type="number" min="0.25" max="10" step="0.25" class="lk-u"></div></div>').join('')+
    '<button type="button" class="btn tiny go">Save links</button>';
  f.querySelectorAll('.lk').forEach(row=>{
    const l=cur[row.dataset.v.toLowerCase()];
    row.querySelector('.lk-m').value=l?String(l.stock_med_id):'';
    row.querySelector('.lk-u').value=l?l.units:1;
  });
  f.querySelector('button').onclick=async()=>{
    const links=[...f.querySelectorAll('.lk')].map(row=>({variant:row.dataset.v,
      stock_med_id:row.querySelector('.lk-m').value,units:row.querySelector('.lk-u').value||1}));
    try{await post('/api/stock/link',{med_id:r.med_id,links:links});toast('Links saved');loadStock();}
    catch(err){toast(err.message);}
  };
  w.appendChild(f);
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
    s+='<line x1="'+P+'" x2="'+W+'" y1="'+y+'" y2="'+y+'" stroke="var(--line)" stroke-dasharray="3 3"/>'+
       '<text x="2" y="'+(parseFloat(y)+3)+'" font-size="9" fill="var(--muted)">'+gl+'</text>';});
  [['sys','#B3372A'],['dia','#0B6E6E'],['pulse','#C8860A']].forEach(k=>{
    let p='',dots='';
    bp.forEach((v,i)=>{if(!v[k[0]])return;const x=xs(i).toFixed(1),y=ys(v[k[0]]).toFixed(1);
      p+=(p?'L':'M')+x+' '+y+' ';dots+='<circle cx="'+x+'" cy="'+y+'" r="2" fill="'+k[1]+'"/>';});
    if(p)s+='<path d="'+p+'" fill="none" stroke="'+k[1]+'" stroke-width="2"/>'+dots;
  });
  s+='<text x="'+P+'" y="'+(H-3)+'" font-size="9" fill="var(--muted)">'+vtEsc(bp[0].day)+'</text>'+
     '<text x="'+(W-58)+'" y="'+(H-3)+'" font-size="9" fill="var(--muted)">'+vtEsc(bp[bp.length-1].day)+'</text></svg>';
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
const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',BP:'bp',Vitals:'bp',Meal:'meal',Activity:'act',Pain:'pain',Load:'load','Down day':'down'};
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
  loadDvDown();
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
  if(e.pain&&!e.eased){
    const eb=document.createElement('button');
    eb.type='button';
    eb.className='ea';
    eb.textContent='Eased now';
    eb.onclick=async()=>{
      try{
        const a=await post('/api/episode/eased/'+e.id,{});
        toast('Lasted '+a.duration);
        box.remove();
        loadDayView();
      }catch(err){toast(err.message);}
    };
    box.querySelector('.vb').insertBefore(eb,box.querySelector('.go'));
  }
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
  loadDownView();
  $('#rvDaysN').textContent=rvDays;
  const rv=await jget('/api/review?days='+rvDays);
  renderVitals(rv.vitals);
  const dmap={};rv.days.forEach(d=>dmap[d.day=d.day]=d);
  $('#chartMain').innerHTML=svgLine(rv.days,['pain','tea','coffee'],['var(--mkpain)','var(--mkep)','var(--mkot)']);
  const fmapData=rv.daily.map(d=>({day:d.day,fscore:d.fscore,sym:0}));
  const symSet=new Set(rv.days.filter(d=>(d.syms&&d.syms.length)||(d.pain>0)).map(d=>d.day));
  fmapData.forEach(d=>d.sym=symSet.has(d.day)?d.fscore:null);
  $('#chartFmap').innerHTML=svgLine(rv.daily.length?rv.daily:[{fscore:0}],['fscore'],['#0B6E6E']);
  $('#chartDose').innerHTML=svgBars(rv.dosecount,'n','var(--hip)');
  const hit=rv.daily.filter(d=>d.protein>=rv.target).length,tot=rv.daily.length||1;
  $('#proteinHit').innerHTML=`<div class="pbar"><i style="width:${(hit/tot*100).toFixed(0)}%"></i></div>
    <p class="tot"><b>${hit}</b> of ${tot} logged days hit ${rv.target} g protein</p>`;
  /* recent doses table */
  const dt=$('#rvDoses');
  if(rv.doses.length){let t='<table><tr><th>Day</th><th>Time</th><th>Medicine</th><th>Effect</th><th></th></tr>';
    rv.doses.slice(0,40).forEach(d=>{t+=`<tr><td>${d.day}</td><td><button type="button" class="chip tbtn rvt" data-i="${d.id}" data-day="${d.day}" data-tm="${d.dtime||''}">${d.dtime||'--:--'}</button></td><td>${d.medicine}</td><td>${d.effect||''}</td>
      <td><button class="del" data-t="doses" data-i="${d.id}">&times;</button></td></tr>`;});
    dt.innerHTML=t+'</table>';
    /* GUTLOG_V3310_FOODLIB -- a dose's time is changed where it is shown. */
    dt.querySelectorAll('.rvt').forEach(b=>b.onclick=()=>teOpen(dt,{table:'doses',id:+b.dataset.i,
      day:b.dataset.day,time:b.dataset.tm,title:b.closest('tr').children[2].textContent,after:loadReview}));
  }else dt.innerHTML='<p class="hint" style="margin:0">No doses in range.</p>';
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
    items:basket.map(b=>b.g>0?{n:b.item,g:b.g}:{n:b.item,q:b.q,p:b.p,k:b.k,f:b.f,fm:b.fm})});
  basket=[];renderBasket();$('#ml_notes').value='';tmReset('ml_time');await loadRings();await loadMealTotals();
}
/* GUTLOG_V3290_NUTRITION. The numbers come from the server now -- the same
   nut_day() the history page reads -- so the card and the history cannot
   disagree. This used to sum the rows in JavaScript, and the date box it
   read had no change listener, so it only ever showed today. */
function mlDay(){return ($('#ml_day').value)||todayISO;}
function mlPad(n){return (n<10?'0':'')+n;}
function mlDmyText(iso){
  const M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const p=String(iso).split('-');
  if(p.length!==3)return String(iso);
  return p[2]+'-'+M[parseInt(p[1],10)-1]+'-'+p[0];
}
function mlShift(iso,n){
  const p=String(iso).split('-').map(Number);
  const d=new Date(p[0],p[1]-1,p[2]);d.setDate(d.getDate()+n);
  return d.getFullYear()+'-'+mlPad(d.getMonth()+1)+'-'+mlPad(d.getDate());
}
async function mlSetDay(iso){
  if(iso>todayISO)iso=todayISO;
  $('#ml_day').value=iso;
  mlRenderStep();
  await loadMealTotals();
}
function mlRenderStep(){
  const d=mlDay();
  const t=$('#ml_day_text');if(t)t.textContent=mlDmyText(d);
  const pick=$('#mlPick');if(pick)pick.textContent=mlDmyText(d)+(d===todayISO?' (today)':'');
  const nx=$('#mlNext');if(nx)nx.disabled=(d>=todayISO);
}
function mlFillDmy(){
  const D=$('#mlD'),M=$('#mlM'),Y=$('#mlY');
  if(!D||D.options.length)return;
  const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  for(let i=1;i<=31;i++)D.add(new Option(mlPad(i),mlPad(i)));
  for(let i=0;i<12;i++)M.add(new Option(MON[i],mlPad(i+1)));
  const ty=parseInt(todayISO.slice(0,4),10);
  for(let i=ty;i>=ty-6;i--)Y.add(new Option(String(i),String(i)));
}
async function loadMealTotals(){
  const d=mlDay();
  const j=await jget('/api/nutrition/day/'+d);
  const when=(d===todayISO)?'Today':mlDmyText(d);
  if(!j.logged){
    $('#dayTotals').innerHTML=`${when}: <b>not logged</b>`;
    $('#dayPbar').style.width='0%';
    $('#dayFmap').innerHTML='';
  }else{
    const part=j.partial?' &middot; <b>partial</b>':'';
    $('#dayTotals').innerHTML=`${when}: <b>${j.protein.toFixed(1)} g protein</b> of ${j.protein_target} &middot; `+
      `${j.kcal} kcal &middot; ${j.fibre.toFixed(1)} g fibre of ${j.fibre_target} &middot; `+
      `${j.meals} meal(s)${part}`;
    $('#dayPbar').style.width=Math.min(100,j.protein/j.protein_target*100)+'%';
    let nq=0;(j.rows||[]).forEach(m=>(m.items||[]).forEach(it=>nq+=it.q));
    const avg=nq?j.fscore/nq:0;
    const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
    $('#dayFmap').innerHTML=`FODMAP load: <b>${lab}</b>`;
  }
  mlDayMeals(j);
}
function mlDayMeals(j){
  const box=$('#mlDayMeals');if(!box)return;
  const d=j.day;
  $('#mlDayHead').textContent=(d===todayISO?'Meals today':'Meals on '+mlDmyText(d));
  box.innerHTML='';
  const rows=j.rows||[];
  if(!rows.length){box.appendChild(el('p','hint','Nothing logged on this day.'));return;}
  rows.forEach(m=>{
    const line=el('div','mlmeal');
    /* GUTLOG_V3310_FOODLIB -- the time is a button: tap it to change it. */
    line.appendChild(teBtn({table:'meals',id:m.id,day:m.day,time:m.mtime,title:m.slot,row:line,
      after:async()=>{await loadMealTotals();loadMeals();}}));
    line.appendChild(el('b','',m.slot));
    const acts=el('div','macts');
    const ed=el('button','chip','Edit');ed.type='button';
    ed.onclick=()=>mlEditMeal(m);
    const ag=el('button','chip','Again');ag.type='button';
    ag.onclick=async()=>{try{await post('/api/meals/'+m.id+'/again',{});
      toast(m.slot+' logged again, now');await mlSetDay(todayISO);loadMeals();}
      catch(err){toast(err.message);}};
    const de=el('button','chip',mlDel===m.id?'Sure?':'Delete');de.type='button';
    de.onclick=async()=>{if(mlDel!==m.id){mlDel=m.id;loadMealTotals();return;}
      try{await post('/api/meals/'+m.id+'/delete',{});mlDel=null;toast('Deleted');
        await loadMealTotals();loadMeals();}catch(err){toast(err.message);}};
    acts.appendChild(ed);acts.appendChild(ag);acts.appendChild(de);
    line.appendChild(acts);
    line.appendChild(el('small','',mealItemsText(m.items)+
      ' · '+Math.round(m.protein||0)+' g protein · estimated'));
    box.appendChild(line);
  });
}
/* Editing a past meal opens the same card editor the Now tab uses, loaded
   for THAT day. The nav resets the card day to today, so stepping back here
   cannot leave tomorrow's breakfast filed under last Sunday. */
async function mlEditMeal(m){
  mcDay=m.day;
  await loadMeals();
  mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;mcTimeV=null;
  const ci=(MC.cards||[]).findIndex(c=>c.name===m.card);
  if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
  else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,
        extra:(m.items||[]).map(i=>({n:i.n,q:i.q,g:i.g||0}))};
    (m.items||[]).forEach(i=>{if(!MC.lib[i.n])MC.lib[i.n]={p:i.p,k:i.k};});}
  switchTab('now');mcRender();
  const nm=$('#nowMeal');if(nm)nm.scrollIntoView({behavior:'smooth'});
}
/* Bound once, here, because the markup above is already parsed by the time
   this script runs. The first version of this patch defined every stepper
   function and wired none of them: the arrows drew, did nothing, and the
   suite caught it -- which is the same fault as the date box this release
   exists to fix. */
(function mlBindStep(){
  const pv=$('#mlPrev'),nx=$('#mlNext'),pk=$('#mlPick'),go=$('#mlGo'),dmy=$('#mlDmy');
  if(!pv||!nx||!pk||!go||!dmy)return;
  pv.onclick=()=>mlSetDay(mlShift(mlDay(),-1));
  nx.onclick=()=>{if(mlDay()<todayISO)mlSetDay(mlShift(mlDay(),1));};
  pk.onclick=()=>{
    mlFillDmy();
    const d=mlDay();
    $('#mlD').value=d.slice(8,10);$('#mlM').value=d.slice(5,7);$('#mlY').value=d.slice(0,4);
    dmy.style.display=(dmy.style.display==='none'?'flex':'none');};
  go.onclick=()=>{
    const v=$('#mlY').value+'-'+$('#mlM').value+'-'+$('#mlD').value;
    dmy.style.display='none';mlSetDay(v);};
})();
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
  S.prn={};renderPRNGrid();resetChips('prn');$('#m_notes').value='';tmReset('m_time');await loadPRNToday();await loadRings();
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
  const hide=(tab==='review')||(tab==='meals'&&(seg.meals==='foods'||seg.meals==='recipes'))||
    (tab==='files'&&seg.files!=='consults')||(tab==='meds'&&(seg.meds==='stock'||seg.meds==='salts'));
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
  if(t==='files'){loadFiles();loadLabs();loadDoctors();loadRecords(seg.files);}
  if(t==='meals'){tmNow('ml_time');mlRenderStep();loadMealTotals();loadRegistry();renderTestFoods();}
  if(t==='now')loadNow();
  if(t==='meds'){tmNow('m_time');loadPRNToday();loadPatch();loadCourses();}
  if(t==='meds'&&seg.meds==='sched'){loadSchedMeds();loadSchedule();}
  if(t==='meds'&&seg.meds==='stock')loadStock();
  if(t==='meds'&&seg.meds==='salts')loadSalts();
  window.scrollTo(0,0);
}
function setSeg(section,s){
  seg[section]=s;
  if(section==='meds'&&s==='stock')loadStock();
  if(section==='meds'&&s==='salts')loadSalts();
  if(section==='files')loadRecords(s);
  if(section==='meals'&&s==='recipes')loadRecipes();
  if(section==='meals'&&s==='test')loadTrials();
  if(section==='meds'&&s==='sched'){loadSchedMeds();loadSchedule();}
  $$(`.seg[data-seg="${section}"] button`).forEach(b=>b.classList.toggle('sel',b.dataset.s===s));
  $$(`#tab-${section} .sub`).forEach(el=>el.classList.remove('sel'));
  const el=$(`#${section}-${s}`);if(el)el.classList.add('sel');
  saveBtnVisible();
}
/* GUTLOG_V3290_NUTRITION -- tapping Now always means today, whatever day
   the Meals tab or a past-meal edit last looked at. */
let mcDay=null, mlDel=null;
$$('#nav button').forEach(b=>b.onclick=()=>{
  if(b.dataset.t==='now')mcDay=todayISO;
  switchTab(b.dataset.t);});
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

/* GUTLOG_V3220_MEALS -- the meal card on the Now tab. Opens on the card for
   the time of day, set to what was had last time: an unchanged meal is one
   tap. Other meal takes anything, including a dish never seen before. */
let MC=null,mcCur=0,mcSel=null,mcEdit=null,mcDel=null,mcPick=false;
const MC_SIZES=[['small',0.75,'Small'],['medium',1,'Medium'],['large',1.5,'Large']];
function mcNowHM(){return new Date().toTimeString().slice(0,5);}
function mcAuto(){
  const now=mcNowHM();let best=-1,any=-1;
  MC.cards.forEach((c,i)=>{if((c.from||'00:00')<=now){any=i;if(MC.done.indexOf(c.name)<0)best=i;}});
  return best>=0?best:(any>=0?any:0);
}
function mcFresh(i,from){
  const c=MC.cards[i];const src=from?from.choices:(c.last||{});const ch={};
  c.rows.forEach((r,j)=>{const k=String(j);
    if(src[k]!==undefined)ch[k]=src[k];
    else if(r.kind==='fixed')ch[k]=true;
    else if(r.kind==='opt')ch[k]=!!r.on;
    else if(r.kind==='count')ch[k]=r.q||1;
    else if(r.kind==='pick')ch[k]=(r.sel===undefined?0:r.sel);});
  return {choices:ch,onion:from?!!from.onion:!!c.last_onion,
    extra:(from&&from.extra?from.extra:[]).map(x=>({n:x.n,q:x.q,g:x.g||0}))};
}
function mcPairs(i,sel){
  const out=[];if(i<0)return out;const c=MC.cards[i];const counts={};
  c.rows.forEach((r,j)=>{if(r.kind==='count')counts[r.label]=Number(sel.choices[String(j)]||r.q||1);});
  c.rows.forEach((r,j)=>{const v=sel.choices[String(j)];
    if((r.kind==='fixed'||r.kind==='opt')&&v)(r.items||[]).forEach(x=>out.push([x[0],x[1]]));
    else if(r.kind==='count'&&r.items)r.items.forEach(x=>out.push([x[0],x[1]*counts[r.label]]));
    else if(r.kind==='pick'){const o=(r.options||[])[Number(v)];
      if(o)(o.items||[]).forEach(x=>out.push([x[0],(typeof x[1]==='string'&&x[1][0]==='@')?(counts[x[1].slice(1)]||1):x[1]]));}});
  if(c.onion&&sel.onion)out.push(['Onion (tarka base)',1]);
  return out;
}
function mcEst(pairs){let p=0,k=0;pairs.forEach(x=>{const n=MC.lib[x[0]];if(n){p+=n.p*x[1];k+=n.k*x[1];}});return [p,k];}
function mcSame(){
  if(mcEdit||mcCur<0||mcSel.extra.length)return false;
  const c=MC.cards[mcCur];if(!c.last||!Object.keys(c.last).length)return false;
  return JSON.stringify(mcFresh(mcCur).choices)===JSON.stringify(mcSel.choices)&&!!c.last_onion===!!mcSel.onion;
}
async function loadMeals(){
  try{MC=await jget('/api/mealcards?day='+(mcDay||todayISO));}catch(e){return;}
  if(!MC.cards.length){$('#nowMeal').style.display='none';loadPlan();return;}
  if(!mcEdit){mcCur=mcAuto();mcSel=mcFresh(mcCur);}
  mcRender();
  loadPlan();
}
function mcTab(label,on,fn){const b=el('button','chip'+(on?' sel':''),label);b.type='button';b.onclick=fn;return b;}
function mcRender(){
  const tabs=$('#mealTabs');tabs.innerHTML='';
  MC.cards.forEach((c,i)=>tabs.appendChild(mcTab(c.name+(MC.done.indexOf(c.name)>=0?' ✓':''),i===mcCur,()=>{mcCur=i;mcEdit=null;mcPick=false;mcSel=mcFresh(i);mcRender();})));
  tabs.appendChild(mcTab('Other meal',mcCur<0,()=>{mcCur=-1;mcEdit=null;mcPick=true;mcSel={choices:{},onion:false,extra:[],slot:mcSlotGuess()};mcRender();}));
  const b=$('#mealBody');b.innerHTML='';
  if(mcEdit){const e=el('p','hint','Editing the '+mcEdit.slot+' logged at '+mcEdit.mtime+'. Its time is below — change it if it was different.');e.style.margin='8px 2px';b.appendChild(e);}
  if(mcCur>=0){
    const c=MC.cards[mcCur];
    c.rows.forEach((r,j)=>{const k=String(j);const row=el('div','mrow');
      if(r.kind==='fixed'||r.kind==='opt'){
        const on=!!mcSel.choices[k];const t=el('button','chip'+(on?' sel':''),(on?'✓ ':'')+(r.text||r.label));t.type='button';
        t.onclick=()=>{mcSel.choices[k]=!on;mcRender();};row.appendChild(t);}
      else if(r.kind==='count'){
        const q=Number(mcSel.choices[k]||1);const lab=el('p','lbl',r.label);row.appendChild(lab);
        const st=el('div','mstep');const mi=el('button','chip num','−');mi.type='button';const pl=el('button','chip num','+');pl.type='button';
        const v=el('span','mq',(q%1?q:q)+' '+(r.unit||'')+(q===1?'':'s'));
        mi.onclick=()=>{mcSel.choices[k]=Math.max(0.5,q<=1?q-0.5:q-1);mcRender();};
        pl.onclick=()=>{mcSel.choices[k]=q<1?1:q+1;mcRender();};
        st.appendChild(mi);st.appendChild(v);st.appendChild(pl);row.appendChild(st);}
      else if(r.kind==='pick'){
        row.appendChild(el('p','lbl',r.label));const ch=el('div','chips');
        (r.options||[]).forEach((o,oi)=>{const on=Number(mcSel.choices[k])===oi;const t=el('button','chip'+(on?' sel':''),o.t);t.type='button';
          t.onclick=()=>{mcSel.choices[k]=on?-1:oi;mcRender();};ch.appendChild(t);});
        row.appendChild(ch);}
      b.appendChild(row);});
    if(c.onion){const row=el('div','mrow');row.appendChild(el('p','lbl','Onion in today’s cooking'));const ch=el('div','chips');
      [['No onion',false],['Onion',true]].forEach(x=>{const t=el('button','chip'+(!!mcSel.onion===x[1]?' sel':''),x[0]);t.type='button';t.onclick=()=>{mcSel.onion=x[1];mcRender();};ch.appendChild(t);});
      row.appendChild(ch);b.appendChild(row);}
  }else{
    const row=el('div','mrow');row.appendChild(el('p','lbl','Which meal'));const ch=el('div','chips');
    ['Breakfast','Lunch','Snack','Dinner','Eating out'].forEach(s=>{const t=el('button','chip'+(mcSel.slot===s?' sel':''),s);t.type='button';t.onclick=()=>{mcSel.slot=s;mcRender();};ch.appendChild(t);});
    row.appendChild(ch);b.appendChild(row);
  }
  if(mcSel.extra.length){const row=el('div','mrow');row.appendChild(el('p','lbl',mcCur>=0?'Also had':'Had'));
    mcSel.extra.forEach((x,xi)=>{const L=MC.lib[x.n]||{};const line=el('div','mx');const nm=el('span','',x.n+(L.est?' (estimated)':''));
      const st=el('div','mstep');const mi=el('button','chip num','−');mi.type='button';const pl=el('button','chip num','+');pl.type='button';
      const v=el('span','mq',x.g>0?'by weight':'×'+x.q);
      mi.onclick=()=>{if(x.g>0){x.g=0;}else if(x.q<=0.5){mcSel.extra.splice(xi,1);}else{x.q=x.q<=1?0.5:x.q-1;}mcRender();};
      pl.onclick=()=>{if(x.g>0){x.g=0;}else{x.q=x.q<1?1:x.q+1;}mcRender();};
      st.appendChild(mi);st.appendChild(v);st.appendChild(pl);line.appendChild(nm);line.appendChild(st);row.appendChild(line);
      /* GUTLOG_V3310_FOODLIB -- the same weight field as the Meals tab. */
      if(L.w)row.appendChild(wtLine(x,L.u||'g',L.dry,x.n,()=>{v.textContent=x.g>0?'by weight':'×'+x.q;mcEstDraw();}));});
    b.appendChild(row);}
  if(mcPick)b.appendChild(mcPicker());
  else{const a=el('button','btn ghost','+ Something else');a.type='button';a.style.marginTop='10px';a.onclick=()=>{mcPick=true;mcRender();};b.appendChild(a);}
  const eh=el('p','hint');eh.id='mcEst';b.appendChild(eh);
  if(MC.missing&&MC.missing.length&&mcCur>=0){const w=el('p','hint','Not in the food list yet, left out: '+MC.missing.join(', '));b.appendChild(w);}
  /* GUTLOG_V3310_FOODLIB -- when this meal was eaten. Default now; an edited
     meal opens at its own time. The box is shown as the two lists every
     time box on this page uses. */
  const tr=el('div','vtm mctime');tr.appendChild(el('span','lb','Time eaten'));
  const ti=document.createElement('input');ti.type='time';ti.id='mcTime';
  ti.value=mcTimeV||(mcEdit?mcEdit.mtime:mcNowHM());
  ti.addEventListener('change',()=>{mcTimeV=ti.value;});
  tr.appendChild(ti);b.appendChild(tr);
  const name=mcCur>=0?MC.cards[mcCur].name:(mcSel.slot||'Meal');
  const go=el('button','btn primary',mcEdit?'Save changes':(mcSame()?('Log '+name.toLowerCase()+' — same as last time'):('Log '+name.toLowerCase())));
  go.type='button';go.id='mcGo';
  go.onclick=async()=>{if(go.dataset.busy)return;go.dataset.busy=1;
    const mt=($('#mcTime')&&$('#mcTime').value)||mcNowHM();
    const body={card:mcCur>=0?MC.cards[mcCur].name:'',choices:mcSel.choices,onion:!!mcSel.onion,extra:mcSel.extra,slot:name,mtime:mt};
    try{
      if(mcEdit){await post('/api/meals/'+mcEdit.id+'/replace',body);toast(name+' updated');}
      else{body.day=todayISO;const r=await post('/api/mealcards/log',body);
        toast(name+' logged at '+mt+(r.missing&&r.missing.length?' · left out: '+r.missing.join(', '):''));}
      mcEdit=null;mcPick=false;mcTimeV=null;await loadMeals();
    }catch(err){go.dataset.busy='';toast(err.message);}};
  b.appendChild(go);mcEstDraw();
  if(mcEdit){const cn=el('button','btn ghost','Cancel');cn.type='button';cn.style.marginTop='8px';cn.onclick=()=>{mcEdit=null;loadMeals();};b.appendChild(cn);}
  mcToday();
}
function mcSlotGuess(){const h=Number(mcNowHM().slice(0,2));return h<11?'Breakfast':h<16?'Lunch':h<19?'Snack':'Dinner';}
function mcPicker(){
  const box=el('div','mpick');const inp=document.createElement('input');inp.type='text';inp.placeholder='Search food or type a new dish';inp.id='mcQ';
  const res=el('div','chips');res.style.marginTop='8px';const nd=el('div','');
  box.appendChild(inp);box.appendChild(res);box.appendChild(nd);
  let tm=null;
  const run=async()=>{const q=inp.value.trim();let j;try{j=await jget('/api/foods/search?q='+encodeURIComponent(q));}catch(e){return;}
    res.innerHTML='';nd.innerHTML='';
    j.foods.forEach(f=>{const t=el('button','chip',f.n+(f.est?' *':''));t.type='button';t.title=f.portion||'';
      t.onclick=()=>{MC.lib[f.n]={p:f.p||0,k:f.k||0,est:f.est,w:f.w,pq:f.pq,u:f.u,dry:f.dry,bp:f.bp,bk:f.bk,bf:f.bf};mcSel.extra.push({n:f.n,q:1,g:0});mcPick=false;mcRender();};res.appendChild(t);});
    if(q.length>=2&&!j.foods.some(f=>f.n.toLowerCase()===q.toLowerCase()))nd.appendChild(mcNewDish(q));};
  inp.oninput=()=>{clearTimeout(tm);tm=setTimeout(run,220);};
  setTimeout(()=>{run();},0);
  const cl=el('button','btn ghost','Close');cl.type='button';cl.style.marginTop='8px';cl.onclick=()=>{mcPick=false;mcRender();};box.appendChild(cl);
  return box;
}
function mcNewDish(name){
  const w=el('div','mnew');w.appendChild(el('p','lbl','New dish: “'+name+'” — how big, and what kind?'));
  let size=1,kind=null;const sz=el('div','chips');const kd=el('div','chips');kd.style.marginTop='8px';
  MC_SIZES.forEach(s=>{const t=el('button','chip'+(s[1]===1?' sel':''),s[2]);t.type='button';t.onclick=()=>{size=s[1];sz.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));t.classList.add('sel');};sz.appendChild(t);});
  const drawKinds=()=>{kd.innerHTML='';Object.keys(MC.kinds).forEach(kk=>{const t=el('button','chip'+(kk===kind?' sel':''),MC.kinds[kk]);t.type='button';t.onclick=()=>{kind=kk;drawKinds();};kd.appendChild(t);});};
  jget('/api/foods/guess?name='+encodeURIComponent(name)).then(j=>{kind=j.kind;drawKinds();}).catch(()=>drawKinds());
  w.appendChild(sz);w.appendChild(kd);
  const add=el('button','btn primary','Add “'+name+'”');add.type='button';add.style.marginTop='10px';
  add.onclick=async()=>{try{const r=await post('/api/foods/new',{name:name,kind:kind});
      MC.lib[r.n]={p:r.p,k:r.k,est:!r.existed};mcSel.extra.push({n:r.n,q:size});mcPick=false;
      toast(r.existed?r.n+' was already in your foods':r.n+' added — values estimated');mcRender();}
    catch(err){toast(err.message);}};
  w.appendChild(add);
  w.appendChild(el('p','hint','Values are estimated from a typical dish of that kind and marked as such; edit them later in the food list if you want.'));
  return w;
}
function mcToday(){
  const box=$('#mealToday');box.innerHTML='';const t=MC.today||[];
  let p=0;t.forEach(m=>p+=m.protein||0);
  $('#mealSum').textContent=t.length?(t.length+' logged · '+Math.round(p)+' of '+MC.protein_target+' g protein'):'nothing yet today';
  if(!t.length)return;
  box.appendChild(el('p','lbl','Today'));
  t.forEach(m=>{const line=el('div','mtd');
    /* GUTLOG_V3310_FOODLIB -- the time is a button: tap it to change it. */
    const txt=el('div','');const hd=el('div','mth');
    hd.appendChild(teBtn({table:'meals',id:m.id,day:MC.day||todayISO,time:m.mtime,title:m.slot,row:line,after:loadMeals}));
    hd.appendChild(el('b','',m.slot));txt.appendChild(hd);
    txt.appendChild(el('small','',mealItemsText(m.items)+' · '+Math.round(m.protein||0)+' g protein'));
    const acts=el('div','macts');
    const ed=el('button','chip','Edit');ed.type='button';
    ed.onclick=()=>{mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;mcTimeV=null;
      const ci=MC.cards.findIndex(c=>c.name===m.card);
      if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
      else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,extra:(m.card?[]:m.items.map(i=>({n:i.n,q:i.q,g:i.g||0}))).concat(m.card?[]:[])};
        if(!m.card){m.items.forEach(i=>{if(!MC.lib[i.n])MC.lib[i.n]={p:i.p,k:i.k};});}}
      mcRender();$('#nowMeal').scrollIntoView({behavior:'smooth'});};
    const ag=el('button','chip','Again');ag.type='button';
    ag.onclick=async()=>{try{await post('/api/meals/'+m.id+'/again',{});toast(m.slot+' logged again, now');loadMeals();}catch(err){toast(err.message);}};
    const de=el('button','chip',mcDel===m.id?'Sure?':'Delete');de.type='button';
    de.onclick=async()=>{if(mcDel!==m.id){mcDel=m.id;mcToday();return;}
      try{await post('/api/meals/'+m.id+'/delete',{});mcDel=null;toast('Deleted');loadMeals();}catch(err){toast(err.message);}};
    acts.appendChild(ed);acts.appendChild(ag);acts.appendChild(de);
    line.appendChild(txt);line.appendChild(acts);box.appendChild(line);});
}
/* GUTLOG_V3300_MIRRORSTALE -- one line, and only when there is something
   wrong. A warning that is always on teaches you to ignore warnings. */
async function loadMirror(){
  const box=$('#nowMirror');if(!box)return;
  let j;try{j=await jget('/api/mirror');}catch(e){return;}
  box.innerHTML='';
  if(j.ok)return;
  const c=el('div','card mirrorwarn');
  c.appendChild(el('p','q','\u26A0 Health mirror'));
  c.appendChild(el('p','hint',j.text));
  box.appendChild(c);
}
async function loadNow(){
  loadMirror();
  loadMeals();
  loadStockAlerts();
  loadOrderDue();
  loadMedStatus();
  loadActivity();
  loadPain();
  loadDown();
  loadCtx();
  loadWatch();
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
    row.querySelector('.t').textContent=e.dtime||'--:--';
    row.querySelector('.m').textContent=e.medicine;
    row.querySelector('.u').onclick=async()=>{
      await post('/api/now/undo/'+e.id,{});toast('Removed');loadNow();};
    row.classList.add('extime');row.title='Tap to change the time';
    row.querySelector('.t').onclick=()=>exTimeEdit(row,e);
    row.querySelector('.m').onclick=()=>exTimeEdit(row,e);
    el.appendChild(row);
  });
}

/* GUTLOG_V3120_PAIN -- pain tiles. One tile, one tap, three questions and
   no more: how bad, what was done, and whether it goes below the knee. The
   tile writes one episodes row; an analgesic chip additionally writes a real
   dose row and mirrors it to FitLog with the score.
   The sites and the chips come from the server, so the medicine names stay in
   regimen.local.json with every other medicine name and never reach this
   page. */
const PAIN_SCORE=['0','1','2','3','4','5','6','7','8','9','10'];

function buildPainTiles(sites,treats){
  const box=$('#n_msk');
  if(!box||box.dataset.built||!sites||!sites.length)return;
  box.dataset.built='1';
  sites.forEach((t,idx)=>{
    const slug=t.slug,label=t.label,canRad=t.radiates,hero=(idx===0);
    const w=document.createElement('div');
    w.className='ptile pain'+(hero?' hero':'');
    w.dataset.k=slug;
    w.innerHTML='<button type="button" class="ph"><span class="pn"></span><span class="pv"></span></button>'+
      '<div class="pscore"><p class="lbl">Score 0-10</p><div class="chips ps"></div>'+
      '<p class="lbl" style="margin-top:12px">What did you do</p><div class="chips pt"></div>'+
      '<div class="prad"></div>'+
      '<button type="button" class="btn primary pgo" style="margin-top:12px">Save</button></div>';
    w.querySelector('.pn').textContent=label;
    const st={score:null,treats:[],rad:0};
    const pv=w.querySelector('.pv'),ps=w.querySelector('.ps'),pt=w.querySelector('.pt');
    PAIN_SCORE.forEach(v=>{
      const b=document.createElement('div');
      b.className='chip num';
      b.textContent=v;
      b.onclick=()=>{
        st.score=v;
        pv.textContent=v+'/10';
        [...ps.children].forEach(c=>c.classList.toggle('sel',c===b));
      };
      ps.appendChild(b);
    });
    (treats||[]).forEach(v=>{
      const b=document.createElement('div');
      b.className='chip';
      b.textContent=v;
      b.onclick=()=>{
        const i=st.treats.indexOf(v);
        if(i>=0)st.treats.splice(i,1); else st.treats.push(v);
        b.classList.toggle('sel',st.treats.indexOf(v)>=0);
      };
      pt.appendChild(b);
    });
    if(canRad){
      const rb=document.createElement('div');
      rb.className='chip rad';
      rb.textContent='goes below the knee';
      rb.onclick=()=>{
        st.rad=st.rad?0:1;
        rb.classList.toggle('sel',!!st.rad);
      };
      w.querySelector('.prad').appendChild(rb);
    }
    w.querySelector('.ph').onclick=()=>w.classList.toggle('open');
    w.querySelector('.pgo').onclick=async()=>{
      if(st.score===null){toast('Give it a score');return;}
      try{
        const r=await post('/api/pain',{site:slug,score:st.score,
          treatments:st.treats,radiates:st.rad,day:todayISO});
        let msg=label+' '+st.score+'/10 logged';
        if(r.doses&&r.doses.length)msg+=' \u00b7 '+r.doses.join(' + ')+' recorded';
        toast(msg);
        showSame(r.same_dose);
        if(r.not_mirrored&&r.not_mirrored.length)
          toast('FitLog did not take '+r.not_mirrored.join(', '));
        st.score=null;st.treats=[];st.rad=0;
        w.classList.remove('open');
        pv.textContent='';
        w.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));
        loadPain();
        loadNow();
      }catch(err){toast(err.message);}
    };
    box.appendChild(w);
  });
}

/* Today's pain, and the one tap that turns a start time into a duration. */
async function loadPain(){
  const el=$('#painList');
  if(!el)return;
  const j=await jget('/api/pain?day='+todayISO);
  buildPainTiles(j.sites,j.treatments);
  const rows=j.rows||[];
  const open=rows.filter(r=>!r.duration).length;
  $('#painSum').textContent=rows.length?
    (rows.length+' logged today'+(open?(' \u00b7 '+open+' unresolved'):'')):'tap to open';
  el.innerHTML='';
  rows.forEach(r=>{
    const row=document.createElement('div');
    row.className='exrow';
    row.innerHTML='<span class="t"></span><span class="m"></span>';
    row.querySelector('.t').textContent=r.etime||'';
    const bits=[r.label+' '+r.severity+'/10'];
    if(r.radiates)bits.push('below the knee');
    if(r.treatments)bits.push(r.treatments.split('|').join(', '));
    if(r.duration)bits.push('eased after '+r.duration);
    row.querySelector('.m').textContent=bits.join(' \u00b7 ');
    if(!r.duration){
      const e=document.createElement('button');
      e.type='button';
      e.className='btn tiny u';
      e.textContent='eased';
      e.onclick=async()=>{
        try{
          const a=await post('/api/episode/eased/'+r.id,{});
          toast('Lasted '+a.duration);
          loadPain();
        }catch(err){toast(err.message);}
      };
      row.appendChild(e);
    }
    el.appendChild(row);
  });
}

/* GUTLOG_V3170_DOWN -- a down day is a calendar day, marked with one tap.
   Everything else on the card is optional and saves as it is tapped, so
   there is no form to leave half-filled on a day with a heavy head. The one
   active prompt is a temperature; "not now" is remembered for the day. */
let downState=null;
function downTempSkipped(){
  try{ return localStorage.getItem('gl_temp_skip')===todayISO; }catch(e){ return false; }
}
/* GUTLOG_V3230_CONTEXT -- one tap marks a circumstance on today (or
   yesterday, for last night's sleep noticed this morning); tap again clears. */
let ctxDay=null;
function ctxYesterday(){const d=new Date(todayISO+'T12:00:00');d.setDate(d.getDate()-1);return d.toISOString().slice(0,10);}
async function loadCtx(){
  const card=$('#nowCtx');if(!card)return;
  if(!ctxDay)ctxDay=todayISO;
  let j;try{j=await jget('/api/daycontext?day='+ctxDay);}catch(e){return;}
  const dd=$('#ctxDay');dd.innerHTML='';
  [['Today',todayISO],['Yesterday',ctxYesterday()]].forEach(x=>{
    const b=el('button','chip'+(ctxDay===x[1]?' sel':''),x[0]);b.type='button';
    b.onclick=()=>{ctxDay=x[1];loadCtx();};dd.appendChild(b);});
  const box=$('#ctxTags');box.innerHTML='';
  j.options.forEach(o=>{const on=j.tags.indexOf(o.key)>=0;
    const b=el('button','chip'+(on?' sel':''),(on?'✓ ':'')+o.label);b.type='button';
    b.onclick=async()=>{if(b.dataset.busy)return;b.dataset.busy=1;
      try{await post('/api/daycontext',{day:ctxDay,tag:o.key,on:!on});
        toast((on?'Cleared: ':'Marked: ')+o.label+(ctxDay===todayISO?'':' (yesterday)'));loadCtx();}
      catch(err){b.dataset.busy='';toast(err.message);}};
    box.appendChild(b);});
  const labels=j.options.filter(o=>j.tags.indexOf(o.key)>=0).map(o=>o.label);
  $('#ctxSum').textContent=labels.length?labels.join(' · '):'nothing marked';
  card.classList.toggle('on',labels.length>0);
}
/* GUTLOG_V3250_PLAN -- today's totals against the plan, this week's plants,
   the rotation rules and a few suggestions. Read-only; advisory. */
function planBar(label,val,lo,hi,unit){
  const w=el('div','pbar2');const top=el('div','pb2t');
  top.appendChild(el('span','',label));
  top.appendChild(el('span','',Math.round(val)+(hi&&hi!==lo?(' / '+lo+'–'+hi):(' / '+lo))+' '+unit));
  w.appendChild(top);const bar=el('div','pb2b');const i=el('i','');
  i.style.width=Math.min(100,Math.round(100*val/(lo||1)))+'%';if(hi&&val>hi)i.className='over';
  bar.appendChild(i);w.appendChild(bar);return w;
}
/* GUTLOG_V3270_TIMEPICK -- no phone time dialog. On the folded phone the
   system time dialog hid its Set button, so every time box on the page is
   shown as two plain lists instead (hour, minute). The real box stays,
   hidden, and keeps its value, so nothing that reads .value changes. */
function tpPad(n){return (n<10?'0':'')+n;}
const TP_DESC=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
function tpEnhance(inp){
  if(!inp||inp.dataset.tp||!inp.parentNode)return;
  inp.dataset.tp='1';
  const w=document.createElement('span');w.className='tpick';
  const h=document.createElement('select'),m=document.createElement('select');
  h.className='tph';m.className='tpm';
  h.setAttribute('aria-label','Hour');m.setAttribute('aria-label','Minute');
  h.add(new Option('--',''));m.add(new Option('--',''));
  for(let i=0;i<24;i++)h.add(new Option(tpPad(i),tpPad(i)));
  for(let i=0;i<60;i++)m.add(new Option(tpPad(i),tpPad(i)));
  w.appendChild(h);w.appendChild(document.createTextNode(':'));w.appendChild(m);
  const show=v=>{const ok=/^\d\d:\d\d$/.test(v||'');h.value=ok?v.slice(0,2):'';m.value=ok?v.slice(3,5):'';};
  Object.defineProperty(inp,'value',{configurable:true,
    get(){return TP_DESC.get.call(inp);},
    set(v){TP_DESC.set.call(inp,v);show(TP_DESC.get.call(inp));}});
  const sync=()=>{
    if(h.value&&!m.value)m.value='00';
    if(!h.value)m.value='';
    TP_DESC.set.call(inp,h.value?(h.value+':'+m.value):'');
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new Event('change',{bubbles:true}));
  };
  h.onchange=sync;m.onchange=sync;
  inp.style.display='none';
  inp.parentNode.insertBefore(w,inp.nextSibling);
  show(TP_DESC.get.call(inp));
}
function tpScan(root){(root||document).querySelectorAll('input[type=time]').forEach(tpEnhance);}
new MutationObserver(ms=>ms.forEach(x=>x.addedNodes.forEach(n=>{
  if(n.nodeType!==1)return;
  if(n.matches&&n.matches('input[type=time]'))tpEnhance(n);
  else if(n.querySelectorAll)tpScan(n);
}))).observe(document.documentElement,{childList:true,subtree:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>tpScan(document));
else tpScan(document);

/* An extra dose logged late gets its real time here. */
function tpAgo(mins){const d=new Date(Date.now()-mins*60000);return tpPad(d.getHours())+':'+tpPad(d.getMinutes());}
function exTimeEdit(rowEl,e){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick';
  box.innerHTML='<p class="vt"></p><div class="chips exago"></div>'+
    '<div class="vtm"><span class="lb">Time</span><input type="time" class="tt"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button>'+
    '<button type="button" class="go">Save time</button></div>';
  box.querySelector('.vt').textContent=e.medicine+' - when was it taken?';
  const tt=box.querySelector('.tt');tt.value=e.dtime||'';
  const ago=box.querySelector('.exago');
  const today=(nowData&&nowData.day)===todayISO;
  [[15,'15 min ago'],[30,'30 min ago'],[60,'1 h ago'],[120,'2 h ago']].forEach(a=>{
    if(!today)return;
    const b=document.createElement('button');b.type='button';b.className='chip';b.textContent=a[1];
    b.onclick=()=>{tt.value=tpAgo(a[0]);ago.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};
    ago.appendChild(b);
  });
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!tt.value){toast('Pick a time');return;}
    try{const r=await post('/api/retime',{table:'doses',id:e.id,day:nowData.day,time:tt.value});
      toast(r.unchanged?'No change':('Time set to '+tt.value));box.remove();loadNow();}
    catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
/* GUTLOG_V3260_TRIALS -- food trials as periods. */
let TR=null,trOpen=null;
async function loadTrials(){
  try{TR=await jget('/api/trials');}catch(e){return;}
  const box=$('#trialList');if(!box)return;box.innerHTML='';
  if(!TR.trials.length)box.appendChild(el('p','hint','No trial yet.'));
  TR.trials.forEach(t=>box.appendChild(trialRow(t)));
  $('#trialNewBtn').onclick=()=>{const n=$('#trialNew');const o=n.style.display==='none';n.style.display=o?'':'none';if(o)trialForm();};
}
function trSig(s){return el('span','tsig'+(/worse/.test(s)?' warn':(/better|no signal/.test(s)?' ok':'')),s);}
function trialRow(t){
  const w=el('div','trow');
  w.appendChild(el('b','',t.food));
  const act=t.status==='active';
  w.appendChild(el('p','hint',(act?('Day '+t.day_no+' of '+t.planned_days):('Ended '+t.ended+' · '+t.verdict))+
    ' · had it on '+t.ate_days+' day'+(t.ate_days===1?'':'s')+(t.set_aside?' · '+t.set_aside+' set aside':'')+
    (t.amount?' · '+t.amount:'')+(t.freq?' · '+t.freq:'')));
  const s=el('p','');s.style.margin='4px 0';s.appendChild(el('span','','Compared with before: '));s.appendChild(trSig(t.signal));w.appendChild(s);
  const more=el('button','chip',trOpen===t.id?'Hide details':'Details');more.type='button';
  more.onclick=()=>{trOpen=trOpen===t.id?null:t.id;loadTrials();};w.appendChild(more);
  if(trOpen===t.id)w.appendChild(trialDetail(t));
  return w;
}
function trTable(rows){
  const tb=el('table','ttab');const h=el('tr','');['','Days','Symptom days','Avg gut pain'].forEach(x=>h.appendChild(el('th','',x)));tb.appendChild(h);
  rows.forEach(r=>{const tr=el('tr','');tr.appendChild(el('td','',r[0]));
    [r[1].days,r[1].days?(r[1].symptom_days+' ('+r[1].pct+'%)'):'–',r[1].avg_gi_pain==null?'–':r[1].avg_gi_pain].forEach(x=>tr.appendChild(el('td','',String(x))));tb.appendChild(tr);});
  return tb;
}
function trialDetail(t){
  const d=el('div','');d.style.marginTop='10px';
  d.appendChild(trTable([['14 days before',t.before],['During the trial',t.trial]]));
  d.appendChild(el('p','hint','Days with a Day-context mark are left out of both. A word appears once each side has '+t.min_days+' counted days.'));
  const s2=el('p','');s2.appendChild(el('span','','Days it was eaten (or the day after) vs other days: '));s2.appendChild(trSig(t.exposure_signal));d.appendChild(s2);
  d.appendChild(trTable([['Eaten / day after',t.exposed],['Other logged days',t.not_exposed]]));
  if(t.styles.length)d.appendChild(el('p','hint','Forms eaten: '+t.styles.map(x=>x[0]+' ×'+x[1]).join(', ')));
  if(t.note)d.appendChild(el('p','hint',t.note));
  if(t.status==='active'){
    d.appendChild(el('p','lbl','End the trial — your verdict'));
    const ch=el('div','chips');let v=null;
    TR.verdicts.forEach(x=>{const b=el('button','chip',x.label);b.type='button';b.onclick=()=>{v=x.key;ch.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};ch.appendChild(b);});
    d.appendChild(ch);
    const nt=document.createElement('input');nt.type='text';nt.placeholder='Note (optional)';nt.style.marginTop='8px';d.appendChild(nt);
    const go=el('button','btn primary','End trial');go.type='button';
    go.onclick=async()=>{if(!v){toast('Pick a verdict');return;}
      try{const r=await post('/api/trials/'+t.id+'/end',{verdict:v,note:nt.value});
        toast(t.food+': '+r.verdict+(r.library.length?' · food map updated':''));trOpen=null;loadTrials();
        if(typeof renderTestFoods==='function')try{loadRegistry();}catch(e){}}
      catch(err){toast(err.message);}};
    d.appendChild(go);
  }else if(t.verdict_note)d.appendChild(el('p','hint','Note: '+t.verdict_note));
  return d;
}
function trialForm(){
  const n=$('#trialNew');n.innerHTML='';const st={food:null,freq:'Daily',days:14};
  const inp=document.createElement('input');inp.type='text';inp.placeholder='Search the food or recipe';n.appendChild(inp);
  const res=el('div','chips');res.style.marginTop='8px';n.appendChild(res);
  const picked=el('p','hint','');n.appendChild(picked);
  let tm=null;const run=async()=>{let j;try{j=await jget('/api/foods/search?q='+encodeURIComponent(inp.value.trim()));}catch(e){return;}
    res.innerHTML='';j.foods.slice(0,12).forEach(f=>{const b=el('button','chip'+(st.food===f.n?' sel':''),f.n);b.type='button';
      b.onclick=()=>{st.food=f.n;picked.textContent='Trial of: '+f.n;run();};res.appendChild(b);});};
  inp.oninput=()=>{clearTimeout(tm);tm=setTimeout(run,220);};run();
  const am=document.createElement('input');am.type='text';am.placeholder='How much each time (e.g. 2 eggs)';am.style.marginTop='8px';n.appendChild(am);
  n.appendChild(el('p','lbl','How often'));const fq=el('div','chips');
  ['Daily','Every other day','3 times a week','Weekly'].forEach(x=>{const b=el('button','chip'+(x===st.freq?' sel':''),x);b.type='button';b.onclick=()=>{st.freq=x;fq.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fq.appendChild(b);});
  n.appendChild(fq);
  n.appendChild(el('p','lbl','For how long'));const ln=el('div','chips');
  [[7,'1 week'],[14,'2 weeks'],[21,'3 weeks'],[30,'1 month']].forEach(x=>{const b=el('button','chip'+(x[0]===st.days?' sel':''),x[1]);b.type='button';b.onclick=()=>{st.days=x[0];ln.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};ln.appendChild(b);});
  n.appendChild(ln);
  const go=el('button','btn primary','Start trial today');go.type='button';go.style.marginTop='10px';
  go.onclick=async()=>{if(!st.food){toast('Pick the food');return;}
    try{await post('/api/trials',{food:st.food,amount:am.value,freq:st.freq,days:st.days,start:todayISO});
      toast('Trial started: '+st.food);n.style.display='none';loadTrials();}catch(err){toast(err.message);}};
  n.appendChild(go);
}
async function loadPlan(){
  const card=$('#nowPlan');if(!card)return;
  let j;try{j=await jget('/api/plan?day='+todayISO);}catch(e){return;}
  if(!j.on){card.style.display='none';return;}
  card.style.display='';const t=j.today,g=j.targets;
  $('#planSum').textContent=t.meals?(t.meals+' meal'+(t.meals===1?'':'s')+' logged'):'nothing logged yet';
  const b=$('#planBars');b.innerHTML='';
  b.appendChild(planBar('Protein',t.protein,g.protein,null,'g'));
  b.appendChild(planBar('Calcium',t.calcium,(g.calcium||[1000])[0],(g.calcium||[])[1],'mg'));
  b.appendChild(planBar('Fibre',t.fibre,(g.fibre||[30])[0],(g.fibre||[])[1],'g'));
  b.appendChild(planBar('Energy',t.kcal,(g.kcal||[1850])[0],(g.kcal||[])[1],'kcal'));
  const mm=t.mains.filter(m=>m.logged).map(m=>m.slot+' '+Math.round(m.protein)+' g');
  if(mm.length)b.appendChild(el('p','hint','Protein by meal (aim '+g.protein_meal+'+ g): '+mm.join(' · ')));
  if(t.calcium_missing.length)b.appendChild(el('p','hint','No calcium value yet for: '+t.calcium_missing.join(', ')));
  b.appendChild(el('p','hint','This week: '+j.week.points+' of '+j.week.target+' plants'));
  const tp=$('#planTips');tp.innerHTML='';
  if(j.tips.length){tp.appendChild(el('p','lbl','For the next meal'));const u=el('ul','plantips');j.tips.forEach(x=>u.appendChild(el('li','',x)));tp.appendChild(u);}
  const wk=$('#planWeek');wk.innerHTML='';
  const ul=el('ul','planrules');
  j.rules.forEach(r=>{const li=el('li','pr-'+r.status);li.appendChild(el('span','',r.label));
    li.appendChild(el('span','',({ok:'on track',due:'due',short:'short',full:'at limit',over:'over'})[r.status]+(r.detail?' · '+r.detail:'')));ul.appendChild(li);});
  wk.appendChild(ul);
  wk.appendChild(el('p','hint','Plants this week: '+(j.week.plants.join(', ')||'none yet')));
  $('#planMore').onclick=()=>{const o=wk.style.display==='none';wk.style.display=o?'':'none';$('#planMore').textContent=o?'Hide the week':'This week';};
}
/* GUTLOG_V3240_RECIPES -- the recipe book in the Meals tab. */
let RC=null,rcGroup='',rcStage='',rcOpen=null;
async function loadRecipes(){
  try{RC=await jget('/api/recipes');}catch(e){return;}
  $('#rc_q').oninput=rcList;
  if(rcOpen){openRecipe(rcOpen);return;}
  rcList();
}
function rcChips(box,items,cur,set){
  box.innerHTML='';
  [{key:'',label:'All'}].concat(items).forEach(o=>{
    const b=el('button','chip'+(cur===o.key?' sel':''),o.label);b.type='button';
    b.onclick=()=>{set(o.key);rcList();};box.appendChild(b);});
}
function rcList(){
  $('#rcOne').style.display='none';$('#rcList').style.display='';
  rcChips($('#rc_groups'),RC.groups,rcGroup,v=>rcGroup=v);
  rcChips($('#rc_stages'),RC.stages,rcStage,v=>rcStage=v);
  const q=($('#rc_q').value||'').trim().toLowerCase();
  const box=$('#rc_rows');box.innerHTML='';
  const rows=RC.recipes.filter(r=>(!rcGroup||r.group===rcGroup)&&(!rcStage||r.stage===rcStage)&&
    (!q||q.split(/\s+/).every(w=>r.name.toLowerCase().indexOf(w)>=0)));
  if(!rows.length){box.appendChild(el('p','hint',RC.recipes.length?'No recipe matches.':'No recipes loaded yet.'));return;}
  rows.forEach(r=>{
    const b=el('button','rcrow');b.type='button';
    b.appendChild(el('b','',r.name));
    b.appendChild(el('small','',[r.serving,r.protein!=null?Math.round(r.protein)+' g protein':'',r.kcal!=null?Math.round(r.kcal)+' kcal':''].filter(Boolean).join(' · ')));
    const t=el('div','');t.appendChild(el('span','rctag'+(r.stage==='rotation'?' ok':(r.stage==='avoid'?' warn':'')),r.stage_label));
    if(r.onion||r.garlic)t.appendChild(el('span','rctag warn',r.has_onion_free?'onion/garlic · onion-free version':'onion/garlic'));
    b.appendChild(t);b.onclick=()=>openRecipe(r.slug);box.appendChild(b);});
}
function rcShareText(r){
  const L=[r.name,'For '+(r.serves||'?')+' · serving: '+r.serving,'','Ingredients:'];
  r.ing.forEach(i=>L.push('- '+i[0]+(i[2]?' — '+i[2]:(i[1]?' — '+i[1]+' g':''))));
  if(r.onion_free.length){L.push('','Without onion/garlic:');r.onion_free.forEach(s=>L.push('- '+s));}
  L.push('','Method:');r.method.forEach((s,i)=>L.push((i+1)+'. '+s));
  return L.join('\n');
}
async function openRecipe(slug){
  let j;try{j=await jget('/api/recipes/'+encodeURIComponent(slug));}catch(e){toast('Could not open');return;}
  if(!j.ok){toast(j.err||'Could not open');return;}
  const r=j.recipe;rcOpen=slug;
  $('#rcList').style.display='none';const box=$('#rcOne');box.style.display='';box.innerHTML='';
  const back=el('button','btn ghost','← All recipes');back.type='button';
  back.onclick=()=>{rcOpen=null;rcList();};box.appendChild(back);
  const c=el('div','card');c.style.marginTop='10px';box.appendChild(c);
  c.appendChild(el('p','q',r.name));
  c.appendChild(el('p','hint','Serves '+(r.serves||'?')+' · one serving: '+r.serving+' · '+r.group_label+(r.yield_est?' · yield estimated':'')));
  const n=el('div','rcnum');
  [['kcal',r.kcal,''],['protein',r.protein,' g'],['fibre',r.fibre,' g'],['fat',r.fat,' g']].forEach(x=>{
    const d=el('div','');d.appendChild(el('b','',x[1]==null?'–':(Math.round(x[1]*10)/10)+x[2]));d.appendChild(el('span','',x[0]));n.appendChild(d);});
  c.appendChild(n);
  c.appendChild(el('p','hint','Per serving, estimated from Indian food tables. '+(r.plant_points||0)+' plant points.'));
  if(r.onion||r.garlic||r.high_fodmap.length)c.appendChild(el('span','rctag warn','High-FODMAP: '+(r.high_fodmap.join(', ')||'onion/garlic')));
  else c.appendChild(el('span','rctag ok','No onion or garlic'));
  if(r.onion_free.length){const o=el('div','rcof');o.appendChild(el('b','','Onion-free version'));
    const u=el('ul','');r.onion_free.forEach(s=>u.appendChild(el('li','',s)));o.appendChild(u);c.appendChild(o);}
  const st=el('div','card');box.appendChild(st);st.appendChild(el('p','lbl','Where it stands'));
  const sc=el('div','chips');RC.stages.forEach(s=>{const b=el('button','chip'+(r.stage===s.key?' sel':''),s.label);b.type='button';
    b.onclick=async()=>{try{await post('/api/recipes/'+encodeURIComponent(slug)+'/stage',{stage:s.key});toast(r.name+': '+s.label);
      const x=RC.recipes.find(y=>y.slug===slug);if(x){x.stage=s.key;x.stage_label=s.label;}openRecipe(slug);}catch(err){toast(err.message);}};
    sc.appendChild(b);});st.appendChild(sc);
  const lg=el('div','card');box.appendChild(lg);lg.appendChild(el('p','lbl','Had it? Log it now'));
  let q=1;const qc=el('div','chips');
  [[0.5,'½ serving'],[1,'1 serving'],[1.5,'1½'],[2,'2 servings']].forEach(x=>{const b=el('button','chip'+(x[0]===q?' sel':''),x[1]);b.type='button';
    b.onclick=()=>{q=x[0];qc.querySelectorAll('.chip').forEach(y=>y.classList.remove('sel'));b.classList.add('sel');};qc.appendChild(b);});
  lg.appendChild(qc);
  const go=el('button','btn primary','Log it');go.type='button';go.style.marginTop='10px';
  go.onclick=async()=>{if(go.dataset.busy)return;go.dataset.busy=1;
    try{const h=Number(mcNowHM().slice(0,2));const slot=h<11?'Breakfast':h<16?'Lunch':h<19?'Snack':'Dinner';
      await post('/api/recipes/'+encodeURIComponent(slug)+'/log',{q:q,slot:slot,day:todayISO,mtime:mcNowHM()});
      toast(r.name+' logged as '+slot.toLowerCase());openRecipe(slug);}
    catch(err){go.dataset.busy='';toast(err.message);}};
  lg.appendChild(go);
  if(j.recipe.logged&&j.recipe.logged.length)lg.appendChild(el('p','hint','Last had: '+j.recipe.logged.map(x=>x.day+' '+x.mtime).join(', ')));
  const ic=el('div','card');box.appendChild(ic);ic.appendChild(el('p','lbl','Ingredients (whole pot)'));
  const ul=el('ul','rcing');const hi=r.high_fodmap.map(x=>x.toLowerCase());
  r.ing.forEach(i=>{const li=el('li',hi.indexOf(String(i[0]).toLowerCase())>=0?'hi':'');
    li.appendChild(el('span','',i[0]));li.appendChild(el('span','',i[2]||(i[1]?i[1]+' g':'')));ul.appendChild(li);});
  ic.appendChild(ul);
  const mc=el('div','card');box.appendChild(mc);mc.appendChild(el('p','lbl','Method'));
  const ol=el('ol','rcsteps');r.method.forEach(s=>ol.appendChild(el('li','',s)));mc.appendChild(ol);
  if(r.notes.length){mc.appendChild(el('p','lbl','Notes'));const nl=el('ul','rcsteps');r.notes.forEach(s=>nl.appendChild(el('li','',s)));mc.appendChild(nl);}
  const sh=el('button','btn ghost','Send to the cook on WhatsApp');sh.type='button';sh.style.marginTop='6px';
  sh.onclick=()=>{window.open('https://wa.me/?text='+encodeURIComponent(rcShareText(r)),'_blank');};
  box.appendChild(sh);
  window.scrollTo(0,0);
}
async function loadDown(){
  const card=$('#nowDown');
  if(!card)return;
  const j=await jget('/api/downday?day='+todayISO);
  downState=j;
  renderDown(j);
}
function renderDown(j){
  const card=$('#nowDown'),sum=$('#downSum'),mark=$('#downMark'),
        more=$('#downMore'),hint=$('#downHint');
  card.classList.toggle('on',!!j.marked);
  if(!j.marked){
    sum.textContent='';
    mark.hidden=false;hint.hidden=false;more.hidden=true;
    return;
  }
  mark.hidden=true;hint.hidden=true;more.hidden=false;
  sum.textContent=(j.run&&j.run.n>1)?('day '+j.run.n+' of this run'):'marked today';
  downChips($('#downComp'),j.components_all,j.components,'components');
  downChips($('#downCoped'),j.coped_all,j.coped,'coped');
  renderDownTemp(j);
  const p=$('#downProto');
  if(j.protocol){ p.textContent=j.protocol;p.hidden=false; }
  else{ p.hidden=true; }
}
function downChips(box,all,sel,key){
  box.innerHTML='';
  (all||[]).forEach(v=>{
    const b=document.createElement('div');
    b.className='chip'+((sel||[]).indexOf(v)>=0?' sel':'');
    b.textContent=v;
    b.onclick=async()=>{
      if(b.dataset.busy)return;
      b.dataset.busy='1';
      const cur=(downState[key]||[]).slice();
      const i=cur.indexOf(v);
      if(i>=0)cur.splice(i,1); else cur.push(v);
      const body={day:todayISO};
      body[key]=cur;
      try{
        const r=await post('/api/downday',body);
        if(r.doses&&r.doses.length)toast(r.doses.join(' + ')+' recorded');
        if(r.not_mirrored&&r.not_mirrored.length)toast('FitLog did not take '+r.not_mirrored.join(', '));
        showSame(r.same_dose);
        await loadDown();
      }catch(err){ toast(err.message); }
      b.dataset.busy='';
    };
    box.appendChild(b);
  });
}
function renderDownTemp(j){
  const box=$('#downTemp');
  box.innerHTML='';
  if(j.temp){
    const p=document.createElement('p');
    p.className='hint';
    p.style.margin='12px 2px 0';
    p.textContent='Temperature '+j.temp.value+'° at '+j.temp.vtime+', in your vitals.';
    box.appendChild(p);
    return;
  }
  if(downTempSkipped())return;
  const w=document.createElement('div');
  w.className='dwtemp';
  w.innerHTML='<p class="lbl">Temperature now? The one number worth taking.</p>'+
    '<input type="number" step="0.1" min="30" max="115" inputmode="decimal" placeholder="37.0" aria-label="Temperature">'+
    '<button type="button" class="btn primary go">Save</button>'+
    '<button type="button" class="btn nn">Not now</button>';
  w.querySelector('.go').onclick=async()=>{
    const v=parseFloat(w.querySelector('input').value);
    if(!(v>=30&&v<=115)){ toast('Enter a temperature'); return; }
    try{
      await post('/api/downday',{day:todayISO,temp:v});
      toast('Temperature saved to your vitals');
      loadDown();
    }catch(err){ toast(err.message); }
  };
  w.querySelector('.nn').onclick=()=>{
    try{ localStorage.setItem('gl_temp_skip',todayISO); }catch(e){ }
    box.innerHTML='';
  };
  box.appendChild(w);
}
(function(){
  const m=$('#downMark');
  if(m)m.onclick=async()=>{
    if(m.dataset.busy)return;
    m.dataset.busy='1';
    try{
      await post('/api/downday',{day:todayISO});
      toast('Marked as a down day');
      await loadDown();
      loadWatch();
    }catch(err){ toast(err.message); }
    m.dataset.busy='';
  };
  const u=$('#downUnmark');
  if(u)u.onclick=async()=>{
    try{
      await post('/api/downday/unmark',{day:todayISO});
      toast('Not a down day');
      await loadDown();
      loadWatch();
    }catch(err){ toast(err.message); }
  };
})();
/* Day by day: mark or unmark the day being viewed -- the backfill path. */
async function loadDvDown(){
  const b=$('#dvDown');
  if(!b)return;
  const j=await jget('/api/downday?day='+dvDay);
  if(j.marked){
    const r=j.run||{n:1,length:1};
    b.textContent='Down day'+(r.length>1?(' · day '+r.n+' of '+r.length):'')+' · tap to unmark';
  }else{
    b.textContent='Mark this day as a down day';
  }
  b.onclick=async()=>{
    try{
      await post(j.marked?'/api/downday/unmark':'/api/downday',{day:dvDay});
      toast(j.marked?'Unmarked':'Marked as a down day');
      loadDayView();
      if(dvDay===todayISO)loadDown();
      loadWatch();
    }catch(err){ toast(err.message); }
  };
}
/* The view: every down day beside the day before it, from rows that already
   exist. Observations carry their n; nothing here is advice. */
function ddEsc(s){
  return String(s===null||s===undefined?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function ddNum(v,dp){
  if(v===null||v===undefined||v==='')return '—';
  if(typeof v==='number')return (dp?v.toFixed(dp):Math.round(v).toLocaleString('en-IN'));
  return ddEsc(v);
}
/* Seven facts per day would be eight columns, which is 466px on a 368px
   card. Each fact carries its own label instead, so the pair reads as two
   lines in the same order and nothing has to scroll sideways at 360px. */
function ddRow(label,f,down){
  const bits=['steps '+ddNum(f.steps),'on legs '+ddNum(f.load_h,1)+(f.load_h!==null&&f.load_h!==undefined?' h':''),
    'exercise '+ddNum(f.act_min)+(f.act_min!==null&&f.act_min!==undefined?' min':''),
    'sleep '+ddNum(f.sleep),'doses '+ddNum(f.doses),
    'temp '+ddNum(f.temp,1)+(f.temp!==null&&f.temp!==undefined?'°':'')];
  if(f.epoch)bits.push(ddEsc(f.epoch));
  return '<tr class="'+(down?'dn':'bf')+'"><td>'+ddEsc(label)+'</td><td class="fx">'+bits.join(' · ')+'</td></tr>';
}
async function loadDownView(){
  const body=$('#ddBody');
  if(!body)return;
  const j=await jget('/api/downdays?days='+rvDays);
  const n=(j.marked||[]).length;
  $('#ddSum').textContent=n?(n+' in '+rvDays+' days'):'none marked';
  if(!n){
    body.innerHTML='<p class="hint" style="margin:0">No down days marked in this range. '+
      'Mark today from Now, or a past day from Day by day above.</p>';
    return;
  }
  let h='';
  h+='<p class="lbl">By month</p><div class="ddwrap"><table class="ddtab">'+
     '<tr><th>Month</th><th>Down days</th><th>Run lengths</th></tr>';
  (j.months||[]).forEach(m=>{
    h+='<tr><td>'+ddEsc(m.month)+'</td><td>'+m.n+'</td><td>'+(m.runs.length?m.runs.join(', '):'—')+'</td></tr>';
  });
  h+='</table></div>';
  h+='<p class="lbl" style="margin-top:14px">Each down day beside the day before it</p>';
  h+='<div class="ddwrap"><table class="ddtab">';
  (j.pairs||[]).forEach(p=>{
    h+=ddRow('before · '+wkDay(p.before),p.b,false);
    h+=ddRow('down · '+wkDay(p.day),p.d,true);
  });
  h+='</table></div>';
  if(!j.link)h+='<p class="hint" style="margin:6px 2px 0">'+ddEsc(j.err)+'</p>';
  h+='<p class="lbl" style="margin-top:14px">What came together</p>';
  if((j.components||[]).length){
    h+='<p class="ddobs">'+(j.components.map(c=>ddEsc(c.name)+' · '+c.n).join('<br>'))+'</p>';
    if((j.pairs_cooccur||[]).length){
      h+='<p class="hint" style="margin:8px 2px 0">Together: '+
         j.pairs_cooccur.map(c=>ddEsc(c.pair)+' ('+c.n+')').join('; ')+'</p>';
    }
  }else{
    h+='<p class="hint" style="margin:0">No components noted yet.</p>';
  }
  h+='<p class="lbl" style="margin-top:14px">Temperature</p>';
  if(j.temps&&j.temps.with){
    h+='<p class="ddobs">A reading on '+j.temps.with+' of '+j.temps.of+' down days: '+
       j.temps.values.map(t=>ddNum(t.value,1)+'° ('+wkDay(t.day)+')').join(', ')+'</p>';
  }else{
    h+='<p class="ddobs">No temperature on any of these '+j.temps.of+' down days yet. '+
       'That is the one number this card asks for.</p>';
  }
  h+='<p class="lbl" style="margin-top:14px">What you did, against how long it lasted</p>';
  const cr=j.coped_runs||{};
  const km=cr.kept_moving||{},rs=cr.rested||{};
  const bits=[];
  if(km.n)bits.push('runs where you kept moving indoors: n='+km.n+', mean '+km.mean_len+' days');
  if(rs.n)bits.push('runs where you rested: n='+rs.n+', mean '+rs.mean_len+' days');
  h+='<p class="ddobs">'+(bits.length?bits.join('<br>'):'Nothing marked under coped with yet.')+
     '<br><span class="hint" style="margin:0">'+ddEsc(cr.note||'')+'</span></p>';
  body.innerHTML=h;
}

/* GUTLOG_V3130_WATCH -- the watch screen. Reading only: every figure comes
   from FitLog's feed or from GutLog's own pain and operating-day rows, and
   nothing here computes a verdict. Arrows are against his own trailing
   median, so they say "more than usual for you", not "short of a target". */
const WK_LABEL={steps:'Steps',exercise_minutes:'Exercise',
  load_hours:'On your legs',resting_hr:'Resting HR',hrv_ms:'HRV'};
const WK_UNIT={steps:'',exercise_minutes:' min',load_hours:' h',
  resting_hr:' bpm',hrv_ms:' ms'};
const WK_ARROW={up:'\u2191',down:'\u2193',level:'\u2192'};
/* sources in words. A slug on a screen is a note to the person who wrote it */
const WK_SOURCE={applewatch:'Apple Watch',healthconnect:'Health Connect',
  gutlog:'logged'};

function wkNum(v,k){
  if(v===null||v===undefined)return '';
  if(k==='steps')return Number(Math.round(v)).toLocaleString('en-IN');
  if(k==='load_hours')return String(Math.round(v*10)/10);
  return String(Math.round(v));
}

/* every figure names its day; signalling today by the absence of a label is
   how '62 steps - 19 min - yesterday' came to read as all yesterday's */
function wkDay(d){
  if(!d)return '';
  if(d===todayISO)return 'today';
  const y=new Date(todayISO+'T12:00:00');
  y.setDate(y.getDate()-1);
  if(d===y.toLocaleDateString('en-CA'))return 'yesterday';
  return new Date(d+'T12:00:00').toLocaleDateString('en-GB',
    {weekday:'short',day:'numeric',month:'short'});
}

function wkSrc(s){
  if(!s)return '';
  return WK_SOURCE[s]||s;
}

function wkTile(k,d,hero){
  const w=document.createElement('div');
  w.className='wktile'+(hero?' hero':'');
  w.dataset.k=k;
  w.dataset.kind=d.kind||'';
  w.innerHTML='<p class="wl"><span class="wn"></span><span class="wday"></span></p>'+
    '<p class="wv"></p><p class="wc"></p><p class="wp"></p><p class="wr"></p>';
  w.querySelector('.wn').textContent=WK_LABEL[k]||k;
  const has=(d.value!==null&&d.value!==undefined);
  const med=(d.median!==null&&d.median!==undefined)?d.median:null;
  /* GUTLOG_V3180_HONEST -- a settled metric can arrive with no reading today
     and several days of median behind it. Printing 'no data' over that threw
     the median away, and the label with an empty value under it read as a
     broken tile rather than as an absence. The median becomes the figure and
     the line beneath states the window it came from, so it can never be
     mistaken for a reading taken today. */
  const medonly=(!has&&med!==null&&d.n>0);
  w.querySelector('.wday').textContent=has?wkDay(d.day):'';
  const vv=w.querySelector('.wv');
  if(has){
    vv.textContent=wkNum(d.value,k)+(WK_UNIT[k]||'');
  }else if(medonly){
    vv.textContent=wkNum(med,k)+(WK_UNIT[k]||'');
    vv.classList.add('med');
  }else{
    vv.textContent='no data';
    vv.classList.add('none');
  }
  /* line 1: the comparison, in ink. Direction is never colour-alone -- the
     glyph carries it and the text stays ink. */
  const cmp=w.querySelector('.wc');
  if(has&&d.dir&&med!==null){
    cmp.textContent=WK_ARROW[d.dir]+' '+wkNum(d.value,k)+
      ' \u00b7 typical '+wkNum(med,k);
  }else if(has){
    cmp.textContent=d.n?('only '+d.n+' days to compare with'):'nothing yet to compare with';
  }else if(medonly){
    cmp.textContent=d.n+'-day median, no reading today';
  }
  /* line 2: where it came from, and how thin the baseline is */
  const prov=[];
  if(has&&d.n)prov.push(d.n+(d.n===1?' day':' days'));
  if(has&&d.source)prov.push(wkSrc(d.source));
  if(k==='load_hours')prov.push('load, not exercise');
  w.querySelector('.wp').textContent=prov.join(' \u00b7 ');
  /* today's running total, under the settled headline, with NO arrow: a
     part-day figure has nothing valid to be compared against */
  const run=w.querySelector('.wr');
  if(d.kind==='cumulative'&&d.today&&d.today.value!==null&&
     d.today.value!==undefined){
    run.textContent=wkNum(d.today.value,k)+(WK_UNIT[k]||'')+' so far today';
  }
  return w;
}

function wkChart(row){
  const box=document.createElement('div');
  const vals=row.filter(r=>r.has_data&&r.steps).map(r=>r.steps);
  const top=vals.length?Math.max.apply(null,vals):0;
  const bars=document.createElement('div');bars.className='wkrow';
  const pick=document.createElement('p');pick.className='wkpick';
  pick.textContent='Tap a bar for that day.';
  row.forEach(r=>{
    const c=document.createElement('button');
    c.type='button';
    c.className='wkcol'+(r.has_data?'':' nodata')+(r.date===todayISO?' today':'');
    c.dataset.d=r.date;
    const b=document.createElement('div');b.className='bar';
    if(r.has_data&&top>0&&r.steps){
      b.style.height=Math.max(4,Math.round(r.steps/top*100))+'%';
    }
    c.appendChild(b);
    const bits=[wkDay(r.date)];
    bits.push(r.has_data?((r.steps?wkNum(r.steps,'steps'):'0')+' steps'+
      (r.source?(' \u00b7 '+wkSrc(r.source)):'')):'no data');
    if(r.ot)bits.push(r.ot_hours+' h on your legs');
    if(r.pain)bits.push('pain logged');
    if(r.down)bits.push('down day');
    if(r.epoch)bits.push(r.epoch);
    const txt=bits.join(' \u00b7 ');
    c.title=txt;
    c.setAttribute('aria-label',txt);
    /* title= does nothing on a phone, so the day is tapped, not hovered */
    c.onclick=()=>{
      [...bars.children].forEach(x=>x.classList.remove('sel'));
      c.classList.add('sel');
      pick.textContent=txt;
    };
    bars.appendChild(c);
  });
  box.appendChild(bars);
  const ep=document.createElement('div');ep.className='wkep';
  row.forEach(r=>{
    const s=document.createElement('div');
    s.className='seg'+(r.epoch?' on':'');
    if(r.epoch)s.title=r.epoch;
    ep.appendChild(s);
  });
  box.appendChild(ep);
  [['pain','pain'],['ot','ot'],['down','down']].forEach(p=>{
    const lane=document.createElement('div');lane.className='wklane '+p[1];
    row.forEach(r=>{
      const m=document.createElement('div');
      m.className='mk'+(r[p[0]]?' on':'');
      lane.appendChild(m);
    });
    box.appendChild(lane);
  });
  const dd=document.createElement('div');dd.className='wkdates';
  dd.innerHTML='<span></span><span></span>';
  dd.children[0].textContent=row.length?wkDay(row[0].date):'';
  dd.children[1].textContent=row.length?'today':'';
  box.appendChild(dd);
  box.appendChild(pick);
  /* one bar series, so the title names it and it needs no legend; the lanes
     and the band are separate encodings and do need one. Shape as well as
     colour on every marker. */
  const key=document.createElement('div');key.className='wkkey';
  key.innerHTML='<span><i class="pain"></i>pain logged</span>'+
    '<span><i class="ot"></i>operating day</span>'+
    '<span><i class="down"></i>down day</span>'+
    '<span><i class="ep"></i>medication epoch</span>'+
    '<span><i class="nd"></i>no data</span>';
  box.appendChild(key);
  return box;
}

async function loadWatch(){
  const st=$('#wkStrip');
  if(!st)return;
  const j=await jget('/api/watch?days=14');
  st.innerHTML='';
  $('#wkChart').innerHTML='';
  $('#wkWork').innerHTML='';
  if(!j.link){
    $('#wkSum').textContent='not reachable';
    st.innerHTML='<p class="hint" style="margin:0"></p>';
    st.querySelector('.hint').textContent=j.err||'FitLog is not answering.';
    $('#wkNote').textContent=j.note||'';
    return;
  }
  const s=j.strip||{};
  /* GUTLOG_V3180_HONEST -- the server withholds a tile that holds nothing and
     the card refuses to draw one anyway. Two guards on purpose: this is the
     last line before the screen, and an answer cached by the service worker
     from an older build would otherwise put an empty label back in front of
     him with nothing in the server to stop it. */
  const wkHas=d=>!!d&&((d.value!==null&&d.value!==undefined)||
    (d.median!==null&&d.median!==undefined&&d.n>0)||
    (d.today&&d.today.value!==null&&d.today.value!==undefined));
  /* one hero, then two per row. Five across overflowed a 368px strip. */
  if(wkHas(s.steps))st.appendChild(wkTile('steps',s.steps,true));
  const grid=document.createElement('div');grid.className='wkgrid';
  ['exercise_minutes','load_hours','resting_hr','hrv_ms'].forEach(k=>{
    if(wkHas(s[k]))grid.appendChild(wkTile(k,s[k],false));
  });
  st.appendChild(grid);
  /* the header names the day of every figure it carries */
  const sum=[];
  const hd=(s.steps&&s.steps.day)||'';
  if(s.steps&&s.steps.value!==null&&s.steps.value!==undefined)
    sum.push(wkDay(hd)+' '+wkNum(s.steps.value,'steps')+' steps');
  if(s.exercise_minutes&&s.exercise_minutes.value&&
     s.exercise_minutes.day===hd)
    sum.push(Math.round(s.exercise_minutes.value)+' min');
  if(s.steps&&s.steps.today&&s.steps.today.value!==null&&
     s.steps.today.value!==undefined)
    sum.push('today '+wkNum(s.steps.today.value,'steps'));
  $('#wkSum').textContent=sum.length?sum.join(' \u00b7 '):'no data yet';
  $('#wkChart').appendChild(wkChart(j.row||[]));
  const ep=(j.epochs||[]).map(e=>e.label).filter(Boolean);
  if(ep.length){
    const p=document.createElement('p');
    p.className='wknote';
    p.textContent='Medication epoch in this window: '+ep.join('; ')+
      '. Resting HR and HRV inside a drug change are artefacts of the change.';
    $('#wkChart').appendChild(p);
  }
  const wl=$('#wkWork');
  (j.workouts||[]).forEach(w=>{
    const r=document.createElement('div');r.className='wkwo';
    r.innerHTML='<span class="wt"></span><span class="wm"></span>';
    r.querySelector('.wt').textContent=wkDay(w.date)+' '+(w.start_hm||'');
    const bits=[(w.kind||w.wtype||'workout')+' '+w.minutes+' min'];
    if(w.distance_km)bits.push(w.distance_km+' km');
    r.querySelector('.wm').textContent=bits.join(' \u00b7 ');
    wl.appendChild(r);
  });
  $('#wkNote').textContent=j.note||'';
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
    try{await post('/api/prnmeds',{name:n});loadNow();loadSchedMeds();
      switchTab('meds');setSeg('meds','salts');toast('Added. Now give its salt and strength.');}
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
  const q=new URLSearchParams(location.search);
  const p=q.get('open');
  if(!p)return;
  /* GUTLOG_V3290_NUTRITION -- a row on /nutrition opens that day here. */
  if(p==='meals'){
    switchTab('meals');setSeg('meals','meal');
    const d=q.get('day');
    mlFillDmy();
    if(d&&/^\d{4}-\d\d-\d\d$/.test(d)&&d<=todayISO)mlSetDay(d);else mlRenderStep();
    return;
  }
  if(p==='records'){switchTab('files');setSeg('files','reports');return;}
  switchTab('now');
  const map={bp:'#nowBP',sym:'#nowSym',meds:'#nowSched',act:'#nowAct'};
  const el=$(map[p]||'#nowSched');
  if(el)setTimeout(()=>el.scrollIntoView({behavior:'smooth',block:'start'}),120);
}

/* GUTLOG_V3310_FOODLIB -- times. One way to change the time of anything
   logged: the same two lists every time box on this page uses (v3.27.0),
   never the phone's own dialog, saving through /api/retime so every change
   is recorded in `edits` like the Day by day card's. */
let mcTimeV=null;
function teOpen(rowEl,o){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick tedit';
  box.innerHTML='<p class="vt"></p><div class="vtm"><span class="lb">Time</span><input type="time" class="tt"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button><button type="button" class="go">Save time</button></div>';
  box.querySelector('.vt').textContent=(o.title||'Entry')+' · '+(o.day===todayISO?'today':mlDmyText(o.day));
  const tt=box.querySelector('.tt');tt.value=o.time||'';
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!tt.value){toast('Pick a time');return;}
    try{const r=await post('/api/retime',{table:o.table,id:o.id,day:o.day,time:tt.value});
      toast(r.unchanged?'No change':('Time set to '+tt.value));box.remove();if(o.after)await o.after();}
    catch(err){toast(err.message);}};
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
  return box;
}
function teBtn(o){
  const b=el('button','chip tbtn',o.time||'--:--');b.type='button';
  b.setAttribute('aria-label','Time '+(o.time||'not set')+', tap to change');
  b.onclick=ev=>{ev.stopPropagation();teOpen(o.row||b.parentNode,o);};
  return b;
}
function tmNow(id){const i=$('#'+id);if(i&&!i.dataset.set)i.value=nowHM();}
function tmReset(id){const i=$('#'+id);if(i){delete i.dataset.set;i.value=nowHM();}}
['ml_time','m_time'].forEach(id=>{const i=$('#'+id);if(i)i.addEventListener('change',()=>{i.dataset.set='1';});});
function mealItemsText(items){
  return (items||[]).map(i=>i.n+(i.g>0?(' '+lfFmt(i.g)+' '+(i.u||'g')+(i.dry?' dry':'')):
    (i.q!==1?' ×'+i.q:''))).join(', ');
}
/* A weight field for one row of a meal. dry says "dry weight" beside it. */
function wtLine(o,unit,dry,label,onChange){
  const w=el('div','bkw');w.appendChild(el('span','','or by weight'));
  const i=document.createElement('input');i.type='number';i.className='bkg';i.min='0';i.step='1';
  i.inputMode='decimal';i.setAttribute('aria-label',label+', weight in '+unit);
  if(o.g>0)i.value=o.g;
  i.oninput=()=>{const n=Number(i.value);o.g=(isFinite(n)&&n>0)?n:0;onChange();};
  w.appendChild(i);w.appendChild(el('span','',unit));
  if(dry)w.appendChild(el('b','dryw','dry weight'));
  return w;
}
function mcQ(x){const L=MC.lib[x.n];return (x.g>0&&L&&L.pq>0)?x.g/L.pq:x.q;}
function mcEstDraw(){
  const pairs=mcPairs(mcCur,mcSel).concat(mcSel.extra.map(x=>[x.n,mcQ(x)]));
  const e=mcEst(pairs);const h=$('#mcEst');
  if(h)h.textContent=pairs.length?('About '+e[0].toFixed(0)+' g protein · '+Math.round(e[1])+' kcal (estimated)'):'Add what you had.';
  const go=$('#mcGo');if(go)go.disabled=!pairs.length;
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

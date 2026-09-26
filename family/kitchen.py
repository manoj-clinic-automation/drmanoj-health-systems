#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kitchen.py -- the Family Kitchen: one shared pool of recipes and ratings.

FAMILY_EDITION_V1 (Phase C; kitchen members in 1.1.0). Runs as its own Linux
user (fam_kitchen) at https://family.dr-manoj.in/kitchen/, database
/srv/family/kitchen/kitchen.db.

WHAT IS IN THE POOL, AND WHAT NEVER IS
  Recipes (name, group, servings, ingredients with amounts, method, source,
  an attachment, who added it, a status), ratings 1-5, "made it", "would
  make again", a short note; and kitchen members (a slug, a display name,
  plain food preferences such as "no onion-garlic"). That is all. No
  condition, medicine, lab, symptom, weight or anything else about a person
  has a column here, and test_family_c.py / test_family_d.py fail if one
  ever appears (SCHEMA_FORBIDDEN). Everything personal -- the adjusted card,
  the portion, the nutrition for THIS person, the "modified" badges -- is
  worked out inside each full member's own GutLog from their own record. The
  shared recipe is never altered.

WHO CAN CALL IT
  * each copy's GutLog, server to server, with that copy's API token
    (tokens.json "api": {slug: {token, name}}); the token decides who is
    acting, so a copy cannot rate or confirm as someone else;
  * the iPhone Share shortcut, with a CAPTURE token, at /capture/<slug>
    only: a capture token can add a draft for its own slug and do nothing
    else -- not read, not confirm, not capture for anyone else;
  * a KITCHEN MEMBER (slug k1, k2 ...: an account inside this service, no
    Linux user, no other database), through the pages under /<kslug>/
    (kitchen_members.py) with the family PIN sign-in. A kitchen member's
    session never satisfies the bearer routes above, and the bearer routes
    never read a session.

WHO OWNS A RECIPE
  The person who published it (added_slug). Only they can edit or unpublish
  it; everyone else can rate, mark made / again, and add a note. The owner
  ('owner') can HIDE a recipe from the pool (never delete); a hidden or
  unpublished recipe stays visible to its contributor, with a note. Every
  capture lands as a draft for its sender and reaches the pool only when
  that person publishes it, after a duplicate check ("publish as <Name>'s
  version" keeps the two side by side, linked as versions).

Names are resolved when read (tokens.json api names for GutLog copies,
kmembers for kitchen members), so a rename shows everywhere at once.
Python 3.9.
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Flask, Response, abort, g, jsonify, request, send_from_directory

MARKER = "FAMILY_EDITION_V1"
APP_VERSION = "1.2.0"   # 1.2.0 (26-Sep-2026): finding recipes -- person/category views, one search box with aliases, filters
KDIR = os.environ.get("KITCHEN_DIR", "/srv/family/kitchen")
DB_PATH = os.path.join(KDIR, "kitchen.db")
ATTACH = os.path.join(KDIR, "attach")
TOKENS = os.environ.get("KITCHEN_TOKENS", os.path.join(KDIR, "tokens.json"))
MAX_MB = 20
ALLOWED = {".jpg", ".jpeg", ".png", ".pdf", ".heic", ".webp", ".txt"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
GROUPS = ["Dal & curry", "Sabzi", "Rice & roti", "Breakfast", "Snack", "Sweet", "Drink", "Salad",
          "Egg, paneer & fish", "Other"]
SLUG_RX = re.compile(r"^(m[0-9]{1,3}|k[0-9]{1,3}|owner)$")
KSLUG_RX = re.compile(r"^k[0-9]{1,3}$")
STATUSES = ("published", "unpublished", "hidden")
# A pool column may never be named for health data. The schema test reads this.
SCHEMA_FORBIDDEN = ("condition", "medicine", "medic", "drug", "dose", "lab", "symptom", "pain", "weight",
                    "bmi", "diagnos", "allerg", "bp", "sugar", "hba1c", "sodium", "health", "patient")
# Food preferences (kitchen members): plain words about food, filter only.
PREFS = (("veg", "Vegetarian"), ("egg", "Eggetarian"), ("noonion", "No onion-garlic"), ("jain", "Jain"))
PREF_WORDS = {
    "nonveg": ("chicken", "mutton", "lamb", "goat", "fish", "prawn", "prawns", "shrimp", "crab", "meat",
               "beef", "pork", "keema", "murgh", "machli", "gosht"),
    "egg": ("egg", "eggs", "anda", "omelette", "omelet"),
    "onion": ("onion", "onions", "garlic", "pyaz", "lehsun", "lasun", "lahsun", "spring onion", "shallot"),
    "root": ("potato", "potatoes", "aloo", "carrot", "carrots", "gajar", "radish", "mooli", "beetroot",
             "beet", "ginger", "adrak", "turnip", "shalgam", "sweet potato", "shakarkandi", "yam", "arbi",
             "colocasia", "mushroom", "mushrooms"),
}
PREF_EXCLUDES = {"veg": ("nonveg", "egg"), "egg": ("nonveg",), "noonion": ("onion",),
                 "jain": ("nonveg", "egg", "onion", "root")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, name TEXT NOT NULL, grp TEXT DEFAULT 'Other',
  servings REAL DEFAULT 2, serving_text TEXT DEFAULT '', ingredients TEXT NOT NULL DEFAULT '[]',
  method TEXT NOT NULL DEFAULT '[]', notes TEXT DEFAULT '[]', source TEXT DEFAULT '',
  attach TEXT DEFAULT '', added_by TEXT DEFAULT '', added_slug TEXT DEFAULT '',
  added_at TEXT, updated TEXT, variant_of INTEGER, variant_label TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ratings (
  recipe_id INTEGER NOT NULL, who TEXT NOT NULL, stars INTEGER, made INTEGER DEFAULT 0,
  again INTEGER DEFAULT 0, note TEXT DEFAULT '', at TEXT, PRIMARY KEY (recipe_id, who));
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, who TEXT NOT NULL, kind TEXT NOT NULL,
  text TEXT DEFAULT '', url TEXT DEFAULT '', attach TEXT DEFAULT '', status TEXT DEFAULT 'new',
  extracted TEXT DEFAULT '', flags TEXT DEFAULT '[]', note TEXT DEFAULT '', tries INTEGER DEFAULT 0,
  created TEXT, updated TEXT, recipe_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_drafts_who ON drafts(who, status);
CREATE TABLE IF NOT EXISTS kmembers (
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER DEFAULT 1, created TEXT,
  last_seen TEXT DEFAULT '', food_prefs TEXT DEFAULT '');
"""
# Columns added after 1.0.0 -- guarded ALTERs, run on any connection (migrate()).
ADDED_COLUMNS = (("recipes", "status", "TEXT DEFAULT 'published'"),
                 ("recipes", "hidden_note", "TEXT DEFAULT ''"),
                 ("recipes", "minutes", "INTEGER"))   # 1.2.0: time to make, when the contributor states it
# 1.2.0 -- finding recipes. A category backbone (GROUPS) and, over it, a person
# view, a category view, one search box with Hindi / English aliases
# (food_aliases.json beside this file), and filters that combine with any
# view. Meal chips map onto the groups; the numbers are counts, nothing else.
MEAL_OF_GROUP = {"Breakfast": ("breakfast",), "Snack": ("snack",), "Sweet": ("sweet",),
                 "Drink": ("breakfast", "snack"), "Salad": ("lunch", "dinner"), "Dal & curry": ("lunch", "dinner"),
                 "Sabzi": ("lunch", "dinner"), "Rice & roti": ("lunch", "dinner"),
                 "Egg, paneer & fish": ("breakfast", "lunch", "dinner"),
                 "Other": ("breakfast", "lunch", "dinner", "snack", "sweet")}
MEALS = ("breakfast", "lunch", "dinner", "snack", "sweet")
HIGH_PROTEIN_G = 10.0        # per serving
QUICK_MIN = 20               # minutes, where a time is known
ALIASES_FILE = os.environ.get("KITCHEN_ALIASES", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                             "food_aliases.json"))
_ALIASES = {}
_NUTR_CACHE = {}
MIN_RX = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes)\b", re.I)
HOUR_RX = re.compile(r"\b(\d(?:\.\d)?)\s*(?:hour|hours|hr|hrs)\b", re.I)


def aliases():
    """word -> the set of words that mean the same food, from the data file.
    Missing file = no aliases (search still works on the words as typed)."""
    if "map" not in _ALIASES:
        m = {}
        try:
            with open(ALIASES_FILE, encoding="utf-8") as fh:
                doc = json.load(fh)
            for grp in doc.get("groups") or []:
                words = [norm_name(w) for w in grp if norm_name(w)]
                for w in words:
                    m.setdefault(w, set()).update(words)
        except (OSError, ValueError):
            pass
        _ALIASES["map"] = m
    return _ALIASES["map"]


def query_terms(q):
    """The search as typed, one term per word (a multi-word alias such as
    'bottle gourd' is matched as a phrase first), each with its alternatives."""
    qn = norm_name(q)
    if not qn:
        return []
    al = aliases()
    terms, words = [], qn.split()
    i = 0
    while i < len(words):
        took = False
        for n in (3, 2):
            phrase = " ".join(words[i:i + n])
            if n > 1 and phrase in al:
                terms.append(sorted(al[phrase] | {phrase}))
                i += n
                took = True
                break
        if not took:
            w = words[i]
            terms.append(sorted((al.get(w) or set()) | {w}))
            i += 1
    return terms


def recipe_minutes(r):
    """The time to make: the stated minutes, else the largest 'N min' /
    'N hours' figure in the method or notes; None when nothing is known."""
    try:
        if r["minutes"] not in (None, "", 0):
            return int(r["minutes"])
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    text = " ".join(json.loads(r["method"] or "[]") + json.loads(r["notes"] or "[]"))
    found = [int(x) for x in MIN_RX.findall(text)] + [int(float(x) * 60) for x in HOUR_RX.findall(text)]
    return max(found) if found else None


def recipe_protein(r):
    """Protein per serving from the Kitchen's own sum (the bundled table),
    cached by id and last change; None when nothing matched."""
    key = (r["id"], r["updated"])
    if key not in _NUTR_CACHE:
        try:
            import kitchen_nutrition as N
            n = N.recipe_nutrition(json.loads(r["ingredients"] or "[]"), r["servings"])
            _NUTR_CACHE[key] = (n.get("per_serving") or {}).get("protein")
        except Exception:
            _NUTR_CACHE[key] = None
    return _NUTR_CACHE[key]


def recipe_text(r, names):
    """What the one search box looks in: name, ingredients, category, contributor."""
    ings = json.loads(r["ingredients"] or "[]")
    parts = [r["name"], r["grp"] or "", name_of(r["added_slug"], r["added_by"], names)]
    parts += [str(i.get("item") or "") for i in ings if isinstance(i, dict)]
    return " " + norm_name(" ".join(parts)) + " "


def matches_query(text, terms):
    """Every term (or one of its aliases) is a whole word, or the start of a
    word when it is three letters or more -- 'bhind' finds bhindi, 'b' finds
    nothing but a word 'b'. Never a bare substring."""
    def hit(w):
        return (" " + w + " ") in text or (len(w) >= 3 and (" " + w) in text)
    return all(any(hit(w) for w in alts) for alts in terms)


def browse(who, args, prefs=()):
    """One answer for every way of finding a recipe: the list under the
    current view and filters, and the counts of the OTHER axis (people when a
    category is chosen, categories when a person is), all with the same
    filters applied -- so a person's chips show only the categories they have.
    args: q, by (slug or 'me'), grp, meal, protein, quick, top, new, mine,
    pref (comma list), show=hidden (owner only)."""
    q = args.get("q") or ""
    by = args.get("by") or ""
    grp = args.get("grp") or ""
    meal = (args.get("meal") or "").lower()
    want = dict((k, (args.get(k) or "") in ("1", "true", "yes", "on")) for k in ("protein", "quick", "top", "new", "mine"))
    if want["mine"]:
        by = who
    extra = [p for p in (args.get("pref") or "").split(",") if p in dict(PREFS)]
    prefs = list(dict.fromkeys(list(prefs) + extra))
    show_hidden = args.get("show") == "hidden" and who == "owner"
    names = people()
    terms = query_terms(q)
    rows = db().execute("SELECT * FROM recipes ORDER BY name").fetchall()
    base = []
    for r in rows:
        if r["status"] != "published":
            if not ((by == who and r["added_slug"] == who) or (show_hidden and r["status"] == "hidden")):
                continue
        elif show_hidden:
            continue
        ings = json.loads(r["ingredients"] or "[]")
        if terms and not matches_query(recipe_text(r, names), terms):
            continue
        if prefs and not fits_prefs(food_flags(ings), prefs):
            continue
        if meal in MEALS and meal not in MEAL_OF_GROUP.get(r["grp"] or "Other", MEAL_OF_GROUP["Other"]):
            continue
        if want["quick"]:
            m = recipe_minutes(r)
            if m is None or m > QUICK_MIN:
                continue
        if want["protein"]:
            p = recipe_protein(r)
            if p is None or p < HIGH_PROTEIN_G:
                continue
        d = recipe_json(r, who, names=names)
        d["minutes"] = recipe_minutes(r)
        if want["top"] and not (d["rating"]["n"] and (d["rating"]["avg"] or 0) >= 4):
            continue
        if want["new"] and not d["new"]:
            continue
        base.append(d)
    # counts of the other axis, under the same filters
    people_n, groups_n = {}, {}
    for d in base:
        if not grp or d["grp"] == grp:
            people_n[d["added_slug"]] = people_n.get(d["added_slug"], 0) + 1
        if not by or d["added_slug"] == by:
            groups_n[d["grp"]] = groups_n.get(d["grp"], 0) + 1
    out = [d for d in base if (not by or d["added_slug"] == by) and (not grp or d["grp"] == grp)]
    if want["top"]:
        out.sort(key=lambda x: (-(x["rating"]["avg"] or 0), -x["rating"]["n"], x["name"]))
    elif want["new"]:
        out.sort(key=lambda x: x.get("added_at") or "", reverse=True)
    ppl = [{"slug": s, "name": name_of(s, "someone", names), "n": n} for s, n in people_n.items() if s]
    ppl.sort(key=lambda x: (-x["n"], x["name"]))
    grps = [{"grp": g, "n": groups_n.get(g, 0)} for g in GROUPS if groups_n.get(g)]
    on = [k for k, v in want.items() if v] + (["meal:" + meal] if meal in MEALS else []) + \
         (["q"] if terms else []) + ["pref:" + p for p in prefs]
    hint = ""
    if not out:
        loosen = []
        if terms:
            loosen.append("the search words")
        if meal in MEALS:
            loosen.append("the meal (%s)" % meal)
        for k, lab in (("protein", "high-protein"), ("quick", "quick"), ("top", "top-rated"), ("new", "new this week"),
                       ("mine", "made by me")):
            if want[k]:
                loosen.append(lab)
        if prefs:
            loosen.append("the food preference" + ("s" if len(prefs) > 1 else ""))
        if by and by != who:
            loosen.append("the person")
        if grp:
            loosen.append("the category")
        hint = ("Nothing matches. Try loosening: " + ", ".join(loosen) + ".") if loosen else "Nothing here yet."
    return {"recipes": out, "people": ppl, "groups": grps, "total": len(base), "shown": len(out), "by": by,
            "grp": grp, "filters_on": on, "hint": hint, "prefs": prefs, "everyone": sum(people_n.values())}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024


def now_s():
    return datetime.now().isoformat(timespec="seconds")


def migrate(con):
    """The schema, and the columns 1.1.0 added to a 1.0.0 database. Safe to
    run every time; the stamp tool runs it on its own connection too."""
    con.executescript(SCHEMA)
    for table, col, decl in ADDED_COLUMNS:
        have = [r[1] for r in con.execute('PRAGMA table_info("%s")' % table).fetchall()]
        if col not in have:
            con.execute('ALTER TABLE "%s" ADD COLUMN %s %s' % (table, col, decl))
    con.commit()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        migrate(g.db)
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d:
        d.close()


# ------------------------------------------------------------------ tokens
def tokens():
    try:
        with open(TOKENS, encoding="utf-8") as fh:
            t = json.load(fh)
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError):
        return {}


def _bearer():
    h = request.headers.get("Authorization") or ""
    return h[7:].strip() if h.startswith("Bearer ") else ""


def api_who():
    """(slug, display name) for a valid API token, else (None, None). Reads
    the Authorization header and nothing else -- never a session."""
    got = _bearer()
    if len(got) < 32:
        return None, None
    for slug, v in (tokens().get("api") or {}).items():
        if isinstance(v, dict) and hmac.compare_digest(str(v.get("token", "")), got):
            return slug, str(v.get("name") or slug)
    return None, None


def need_api():
    slug, name = api_who()
    if not slug:
        abort(Response('{"ok": false, "err": "unauthorised"}', status=401, mimetype="application/json"))
    return slug, name


def capture_ok(slug):
    got = _bearer()
    v = (tokens().get("capture") or {}).get(slug) or {}
    return len(got) >= 32 and isinstance(v, dict) and hmac.compare_digest(str(v.get("token", "")), got)


# ------------------------------------------------------------------ people
def people():
    """slug -> display name, for everyone who can act here: GutLog copies
    (tokens.json api names) and kitchen members (kmembers). Resolved when
    read, so a rename shows on every card at once."""
    out = {}
    for s, v in (tokens().get("api") or {}).items():
        if isinstance(v, dict):
            out[s] = str(v.get("name") or s)
    for r in db().execute("SELECT slug, name FROM kmembers").fetchall():
        out[r["slug"]] = r["name"]
    return out


def name_of(slug, fallback="someone", names=None):
    names = names if names is not None else people()
    return names.get(slug) or fallback or slug


def kmember(slug):
    if not KSLUG_RX.match(slug or ""):
        return None
    r = db().execute("SELECT * FROM kmembers WHERE slug=?", (slug,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ helpers
def norm_name(s):
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def ing_words(ings):
    out = set()
    for i in ings or []:
        for w in norm_name(i.get("item") if isinstance(i, dict) else str(i)).split():
            if len(w) > 2:
                out.add(w)
    return out


def find_duplicate(name, ings, exclude=None):
    """A recipe already in the pool that this one repeats: the same name once
    punctuation and case are set aside, or 70% of the same ingredient words
    with a name that shares a word. Returns {id, name, why, by} or None."""
    n = norm_name(name)
    w = ing_words(ings)
    names = people()
    for r in db().execute("SELECT id, name, ingredients, added_slug, added_by FROM recipes "
                          "WHERE status='published'").fetchall():
        if exclude and r["id"] == exclude:
            continue
        by = name_of(r["added_slug"], r["added_by"], names)
        if norm_name(r["name"]) == n and n:
            return {"id": r["id"], "name": r["name"], "why": "same name", "by": by}
        other = ing_words(json.loads(r["ingredients"] or "[]"))
        if w and other:
            j = len(w & other) / float(len(w | other))
            if j >= 0.7 and set(n.split()) & set(norm_name(r["name"]).split()):
                return {"id": r["id"], "name": r["name"], "why": "%d%% of the same ingredients" % int(j * 100),
                        "by": by}
    return None


def clean_ings(ings):
    out = []
    for i in (ings or [])[:60]:
        if not isinstance(i, dict) or not str(i.get("item") or "").strip():
            continue
        q = i.get("qty")
        try:
            q = float(q) if q not in (None, "") else None
        except (TypeError, ValueError):
            q = None
        gms = i.get("grams")
        try:
            gms = float(gms) if gms not in (None, "") else None
        except (TypeError, ValueError):
            gms = None
        out.append({"item": str(i["item"]).strip()[:80], "qty": q, "unit": str(i.get("unit") or "").strip()[:20],
                    "grams": gms, "text": str(i.get("text") or "").strip()[:120]})
    return out


def clean_minutes(v):
    try:
        m = int(float(v))
    except (TypeError, ValueError):
        return None
    return m if 1 <= m <= 1440 else None


def clean_lines(v, n=40, width=400):
    if isinstance(v, str):
        v = [x for x in re.split(r"\n+", v) if x.strip()]
    return [str(x).strip()[:width] for x in (v or [])[:n] if str(x).strip()]


def _kw_any(text, words):
    t = (text or "").lower()
    return any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words)


def food_flags(ings):
    """Which plain food groups a recipe carries, from its ingredient names:
    nonveg, egg, onion (onion or garlic), root. Words, nothing else."""
    out = set()
    items = [str(i.get("item") or "") for i in ings or [] if isinstance(i, dict)]
    for k, words in PREF_WORDS.items():
        if any(_kw_any(it, words) for it in items):
            out.add(k)
    return out


def prefs_of(text):
    return [p for p in (text or "").split(",") if p in dict(PREFS)]


def fits_prefs(flags, prefs):
    for p in prefs:
        if flags & set(PREF_EXCLUDES.get(p, ())):
            return False
    return True


def source_bits(source):
    """('site name', url) for a web source, else ('', '')."""
    s = (source or "").strip()
    if not s.startswith(("http://", "https://")):
        return "", ""
    host = (urlparse(s).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    site = {"youtube.com": "YouTube", "youtu.be": "YouTube", "instagram.com": "Instagram",
            "facebook.com": "Facebook"}.get(host, host)
    return site, s


def root_of(r):
    return r["variant_of"] or r["id"]


def versions_of(rid, names):
    """Every published recipe that is a version of the same dish (the root and
    its variants), oldest first: [{id, name, by, label}]."""
    r = db().execute("SELECT id, variant_of FROM recipes WHERE id=?", (rid,)).fetchone()
    if not r:
        return []
    root = root_of(r)
    rows = db().execute("SELECT id, name, added_slug, added_by, variant_label FROM recipes "
                        "WHERE (id=? OR variant_of=?) AND status='published' ORDER BY id", (root, root)).fetchall()
    return [{"id": x["id"], "name": x["name"], "by": name_of(x["added_slug"], x["added_by"], names),
             "label": x["variant_label"] or ""} for x in rows]


def can_see(r, who):
    return r["status"] == "published" or (who is not None and r["added_slug"] == who)


def recipe_json(r, who=None, full=False, names=None):
    names = names if names is not None else people()
    d = dict(r)
    for k in ("ingredients", "method", "notes"):
        d[k] = json.loads(d[k] or "[]")
    d["added_by"] = name_of(d.get("added_slug"), d.get("added_by"), names)
    d["added_date"] = (d.get("added_at") or "")[:10]
    d["source_site"], d["source_url"] = source_bits(d.get("source"))
    d["has_photo"] = bool(d.get("attach")) and str(d["attach"]).lower().endswith(IMAGE_EXT)
    d["mine"] = None
    d["own"] = who is not None and d.get("added_slug") == who
    d["food_flags"] = sorted(food_flags(d["ingredients"]))
    rt = db().execute("SELECT COUNT(stars) AS n, AVG(stars) AS avg, SUM(made) AS made, SUM(again) AS again "
                      "FROM ratings WHERE recipe_id=?", (r["id"],)).fetchone()
    d["rating"] = {"n": rt["n"] or 0, "avg": round(rt["avg"], 1) if rt["avg"] else None,
                   "made": rt["made"] or 0, "again": rt["again"] or 0}
    d["new"] = (d.get("added_at") or "") >= (datetime.now() - timedelta(days=7)).isoformat()
    d["minutes"] = recipe_minutes(r)
    vs = versions_of(r["id"], names)
    d["versions"] = vs if len(vs) > 1 else []
    if who:
        mine = db().execute("SELECT stars, made, again, note FROM ratings WHERE recipe_id=? AND who=?",
                            (r["id"], who)).fetchone()
        d["mine"] = dict(mine) if mine else None
    if full:
        d["ratings"] = [dict(x, who=name_of(x["who"], "someone", names)) for x in db().execute(
            "SELECT who, stars, made, again, note, at FROM ratings WHERE recipe_id=? ORDER BY at DESC",
            (r["id"],)).fetchall()]
        d["variants"] = [dict(x, by=name_of(x["added_slug"], x["added_by"], names)) for x in db().execute(
            "SELECT id, name, variant_label, added_slug, added_by FROM recipes WHERE variant_of=? "
            "AND status='published' ORDER BY id", (r["id"],)).fetchall()]
        d["can_hide"] = who == "owner"
    else:
        d.pop("method", None)
        d.pop("notes", None)
    return d


def list_recipes(who, args, prefs=()):
    """The pool as one person sees it. q / grp / sort / by, as before; plus
    'show=hidden' (owner only) and the person's food preferences."""
    q = norm_name(args.get("q"))
    grp = args.get("grp") or ""
    sort = args.get("sort") or "az"
    by = args.get("by") or ""
    show_hidden = args.get("show") == "hidden" and who == "owner"
    names = people()
    rows = db().execute("SELECT * FROM recipes ORDER BY name").fetchall()
    out = []
    for r in rows:
        if r["status"] != "published":
            # a contributor sees their own unpublished / hidden cards under their name
            if not ((by == who and r["added_slug"] == who) or (show_hidden and r["status"] == "hidden")):
                continue
        elif show_hidden:
            continue
        if grp and r["grp"] != grp:
            continue
        if by and r["added_slug"] != by:
            continue
        ings = json.loads(r["ingredients"] or "[]")
        if q and q not in norm_name(r["name"]) and not any(q in norm_name(i.get("item")) for i in ings):
            continue
        if prefs and not fits_prefs(food_flags(ings), prefs):
            continue
        out.append(recipe_json(r, who, names=names))
    if sort == "top":
        out = [x for x in out if x["rating"]["n"]]
        out.sort(key=lambda x: (-(x["rating"]["avg"] or 0), -x["rating"]["n"], x["name"]))
    elif sort == "new":
        out = [x for x in out if x["new"]]
        out.sort(key=lambda x: x.get("added_at") or "", reverse=True)
    elif sort == "fav":
        out = [x for x in out if x["rating"]["again"] >= 2 or (x["rating"]["made"] >= 2 and (x["rating"]["avg"] or 0) >= 4)]
        out.sort(key=lambda x: (-x["rating"]["again"], -x["rating"]["made"], x["name"]))
    return out


def people_list():
    """Everyone with a published recipe, most recipes first: [{slug, name, n}]."""
    names = people()
    counts = dict((r["added_slug"], r["n"]) for r in db().execute(
        "SELECT added_slug, COUNT(*) AS n FROM recipes WHERE status='published' GROUP BY added_slug").fetchall())
    out = [{"slug": s, "name": name_of(s, "someone", names), "n": n} for s, n in counts.items() if s]
    out.sort(key=lambda x: (-x["n"], x["name"]))
    return out


# ------------------------------------------------------------------ routes
@app.route("/healthz")
def healthz():
    return Response("ok %s" % APP_VERSION, mimetype="text/plain")


@app.route("/api/recipes", methods=["GET", "POST"])
def api_recipes():
    who, name = need_api()
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        return add_recipe(d, who, name, force=bool(d.get("force")))
    return jsonify(ok=True, recipes=list_recipes(who, request.args), groups=GROUPS, can_hide=who == "owner")


@app.route("/api/people")
def api_people():
    need_api()
    return jsonify(ok=True, people=people_list())


@app.route("/api/browse")
def api_browse():
    """1.2.0 -- the person / category views, the search box and the filters,
    as one answer (see browse())."""
    who, _n = need_api()
    return jsonify(ok=True, all_groups=GROUPS, meals=list(MEALS), prefs_all=[list(p) for p in PREFS],
                   can_hide=who == "owner", **browse(who, request.args))


def add_recipe(d, who, name, force=False, draft_id=None):
    nm = str(d.get("name") or "").strip()[:120]
    ings = clean_ings(d.get("ingredients"))
    method = clean_lines(d.get("method"))
    if not nm or not ings or not method:
        return jsonify(ok=False, err="A recipe needs a name, its ingredients and its method."), 400
    dup = find_duplicate(nm, ings)
    if dup and not force:
        return jsonify(ok=False, err="This looks like a recipe already in the Kitchen.", duplicate=dup), 409
    grp = d.get("grp") if d.get("grp") in GROUPS else "Other"
    try:
        serv = max(1.0, min(40.0, float(d.get("servings") or 2)))
    except (TypeError, ValueError):
        serv = 2.0
    slug = re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-")[:60] + "-" + uuid.uuid4().hex[:6]
    variant_of = d.get("variant_of")
    label = str(d.get("variant_label") or "")[:40]
    try:
        variant_of = int(variant_of) if variant_of not in (None, "") else None
    except (TypeError, ValueError):
        variant_of = None
    if variant_of is None and dup and force and d.get("as_version"):
        # "Publish as <Name>'s version": kept beside the one it repeats.
        variant_of, label = dup["id"], label or "version"
    if variant_of is not None:
        base = db().execute("SELECT id, variant_of FROM recipes WHERE id=?", (variant_of,)).fetchone()
        variant_of = root_of(base) if base else None
    db().execute("INSERT INTO recipes(slug, name, grp, servings, serving_text, ingredients, method, notes, "
                 "source, attach, added_by, added_slug, added_at, updated, variant_of, variant_label, status, "
                 "minutes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'published',?)",
                 (slug, nm, grp, serv, str(d.get("serving_text") or "")[:80], json.dumps(ings),
                  json.dumps(method), json.dumps(clean_lines(d.get("notes"), 20)),
                  str(d.get("source") or "")[:200], str(d.get("attach") or "")[:80], name, who,
                  now_s(), now_s(), variant_of, label, clean_minutes(d.get("minutes"))))
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    if draft_id:
        db().execute("UPDATE drafts SET status='confirmed', recipe_id=?, updated=? WHERE id=?",
                     (rid, now_s(), draft_id))
    db().commit()
    return jsonify(ok=True, id=rid, duplicate=dup)


def _recipe_or_404(rid, who):
    r = db().execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    if not r or not can_see(r, who):
        abort(Response('{"ok": false, "err": "Not found."}', status=404, mimetype="application/json"))
    return r


def _own_recipe(rid, who):
    """The contributor's own recipe, or 403: nobody else edits or unpublishes it."""
    r = db().execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(Response('{"ok": false, "err": "Not found."}', status=404, mimetype="application/json"))
    if r["added_slug"] != who:
        abort(Response('{"ok": false, "err": "Only the person who added a recipe can change it."}',
                       status=403, mimetype="application/json"))
    return r


def edit_recipe(rid, who, d):
    r = _own_recipe(rid, who)
    nm = str(d.get("name") or r["name"]).strip()[:120]
    ings = clean_ings(d["ingredients"]) if "ingredients" in d else json.loads(r["ingredients"] or "[]")
    method = clean_lines(d["method"]) if "method" in d else json.loads(r["method"] or "[]")
    if not nm or not ings or not method:
        return jsonify(ok=False, err="A recipe needs a name, its ingredients and its method."), 400
    grp = d.get("grp") if d.get("grp") in GROUPS else r["grp"]
    try:
        serv = max(1.0, min(40.0, float(d.get("servings") or r["servings"] or 2)))
    except (TypeError, ValueError):
        serv = r["servings"] or 2.0
    notes = clean_lines(d["notes"], 20) if "notes" in d else json.loads(r["notes"] or "[]")
    minutes = clean_minutes(d["minutes"]) if "minutes" in d else r["minutes"]
    db().execute("UPDATE recipes SET name=?, grp=?, servings=?, serving_text=?, ingredients=?, method=?, "
                 "notes=?, updated=?, minutes=? WHERE id=?",
                 (nm, grp, serv, str(d.get("serving_text", r["serving_text"]) or "")[:80], json.dumps(ings),
                  json.dumps(method), json.dumps(notes), now_s(), minutes, rid))
    db().commit()
    return jsonify(ok=True, id=rid)


def set_published(rid, who, publish):
    r = _own_recipe(rid, who)
    if r["status"] == "hidden":
        return jsonify(ok=False, err="This recipe was hidden by the owner; ask them to show it again."), 400
    db().execute("UPDATE recipes SET status=?, updated=? WHERE id=?",
                 ("published" if publish else "unpublished", now_s(), rid))
    db().commit()
    return jsonify(ok=True, status="published" if publish else "unpublished")


def set_hidden(rid, who, hide, note):
    """The owner's safety valve: hide from the pool, never delete. Nobody else."""
    if who != "owner":
        return jsonify(ok=False, err="Only the owner can hide a recipe."), 403
    r = db().execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="Not found."), 404
    if hide:
        db().execute("UPDATE recipes SET status='hidden', hidden_note=?, updated=? WHERE id=?",
                     (str(note or "")[:200], now_s(), rid))
    else:
        db().execute("UPDATE recipes SET status='published', hidden_note='', updated=? WHERE id=?", (now_s(), rid))
    db().commit()
    return jsonify(ok=True, status="hidden" if hide else "published")


def rate_recipe(rid, who, d):
    if not db().execute("SELECT 1 FROM recipes WHERE id=? AND status='published'", (rid,)).fetchone():
        return jsonify(ok=False, err="Not found."), 404
    cur = db().execute("SELECT stars, made, again, note FROM ratings WHERE recipe_id=? AND who=?",
                       (rid, who)).fetchone()
    stars = d.get("stars", cur["stars"] if cur else None)
    try:
        stars = int(stars) if stars not in (None, "") else None
    except (TypeError, ValueError):
        stars = None
    if stars is not None and not 1 <= stars <= 5:
        return jsonify(ok=False, err="Stars are 1 to 5."), 400
    made = 1 if d.get("made", cur["made"] if cur else 0) else 0
    again = 1 if d.get("again", cur["again"] if cur else 0) else 0
    note = str(d.get("note", cur["note"] if cur else "") or "")[:200]
    db().execute("INSERT INTO ratings(recipe_id, who, stars, made, again, note, at) VALUES(?,?,?,?,?,?,?) "
                 "ON CONFLICT(recipe_id, who) DO UPDATE SET stars=excluded.stars, made=excluded.made, "
                 "again=excluded.again, note=excluded.note, at=excluded.at",
                 (rid, who, stars, made, again, note, now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/recipes/<int:rid>")
def api_recipe(rid):
    who, _n = need_api()
    r = _recipe_or_404(rid, who)
    return jsonify(ok=True, recipe=recipe_json(r, who, full=True))


@app.route("/api/recipes/<int:rid>/rate", methods=["POST"])
def api_rate(rid):
    who, _n = need_api()
    return rate_recipe(rid, who, request.get_json(silent=True) or {})


@app.route("/api/recipes/<int:rid>/edit", methods=["POST"])
def api_edit(rid):
    who, _n = need_api()
    return edit_recipe(rid, who, request.get_json(silent=True) or {})


@app.route("/api/recipes/<int:rid>/unpublish", methods=["POST"])
def api_unpublish(rid):
    who, _n = need_api()
    d = request.get_json(silent=True) or {}
    return set_published(rid, who, bool(d.get("publish")))


@app.route("/api/recipes/<int:rid>/hide", methods=["POST"])
def api_hide(rid):
    who, _n = need_api()
    d = request.get_json(silent=True) or {}
    return set_hidden(rid, who, d.get("hide", True), d.get("note"))


@app.route("/api/members")
def api_members():
    """Kitchen members for the owner's Family page: name, last visit, recipes
    added. The owner's token only; nothing else about them exists here."""
    who, _n = need_api()
    if who != "owner":
        return jsonify(ok=False, err="owner only"), 403
    counts = dict((r["added_slug"], r["n"]) for r in db().execute(
        "SELECT added_slug, COUNT(*) AS n FROM recipes WHERE status='published' GROUP BY added_slug").fetchall())
    out = [{"slug": r["slug"], "name": r["name"], "enabled": bool(r["enabled"]), "created": r["created"],
            "last_seen": r["last_seen"] or "", "recipes": counts.get(r["slug"], 0)}
           for r in db().execute("SELECT * FROM kmembers ORDER BY slug").fetchall()]
    return jsonify(ok=True, members=out)


# ------------------------------------------------------------------ drafts
def _save_upload(f):
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in ALLOWED:
        return None, "Only photos, PDFs and text files."
    os.makedirs(ATTACH, exist_ok=True)
    stored = uuid.uuid4().hex + ext
    f.save(os.path.join(ATTACH, stored))
    return stored, ""


def new_draft(who, text="", url="", attach="", kind=None):
    kind = kind or ("image" if attach and not attach.endswith((".pdf", ".txt")) else
                    "pdf" if attach.endswith(".pdf") else "link" if url else "text")
    db().execute("INSERT INTO drafts(who, kind, text, url, attach, status, created, updated) "
                 "VALUES(?,?,?,?,?,'new',?,?)", (who, kind, text[:20000], url[:600], attach, now_s(), now_s()))
    db().commit()
    return db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]


URL_RX = re.compile(r"https?://\S+")


def capture_files(who, files, text):
    """Files from a Share or an upload, each a draft; shared text arrives as
    a .txt file too -- read it, never file it. (ids, text, error)."""
    ids = []
    for f in files[:10]:
        if f and f.filename:
            if os.path.splitext(f.filename)[1].lower() == ".txt" or (f.mimetype or "").startswith("text/"):
                if not text:
                    text = f.read(200000).decode("utf-8", "replace").strip()
                continue
            stored, err = _save_upload(f)
            if not stored:
                return ids, text, err
            ids.append(new_draft(who, attach=stored))
    return ids, text, ""


@app.route("/capture/<slug>", methods=["POST"])
def capture(slug):
    """The Share shortcut. Text, a link, a photo or a PDF -- lands as a draft."""
    if not SLUG_RX.match(slug) or not capture_ok(slug):
        return jsonify(ok=False, err="unauthorised"), 401
    if KSLUG_RX.match(slug) and not (kmember(slug) or {}).get("enabled"):
        return jsonify(ok=False, err="unauthorised"), 401
    text = (request.form.get("text") or "").strip()
    ids, text, err = capture_files(slug, request.files.getlist("file"), text)
    if err:
        return jsonify(ok=False, err=err), 400
    if not text and request.is_json:
        text = str((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text and not ids and request.data and not request.form:
        text = request.data.decode("utf-8", "replace").strip()
    if ids and re.match(r"^[\w .()-]+\.(jpe?g|png|heic|webp|pdf)$", text, re.I):
        text = ""   # an image's file name, sent in the text field alongside the image itself
    url = (request.form.get("url") or "").strip()
    if not url:
        m = URL_RX.search(text)
        if m and len(text) - len(m.group(0)) < 40:   # a shared link, perhaps with a title
            url, text = m.group(0), text.replace(m.group(0), "").strip()
    if text or url:
        ids.append(new_draft(slug, text=text, url=url))
    if not ids:
        return jsonify(ok=False, err="Nothing was shared."), 400
    return jsonify(ok=True, drafts=ids, message="Saved to your Recipe Inbox.")


def drafts_of(who):
    rows = db().execute("SELECT id, kind, url, attach, status, note, created, updated, extracted, flags "
                        "FROM drafts WHERE who=? AND status NOT IN ('confirmed','discarded') "
                        "ORDER BY id DESC", (who,)).fetchall()
    out = []
    for r in rows:
        x = dict(r)
        x["extracted"] = json.loads(x["extracted"] or "null")
        x["flags"] = json.loads(x["flags"] or "[]")
        out.append(x)
    return out


@app.route("/api/drafts", methods=["GET", "POST"])
def api_drafts():
    who, _n = need_api()
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        text = str(d.get("text") or "").strip()
        url = str(d.get("url") or "").strip()
        if not text and not url:
            return jsonify(ok=False, err="Paste a recipe or a link."), 400
        return jsonify(ok=True, id=new_draft(who, text=text, url=url))
    return jsonify(ok=True, drafts=drafts_of(who))


def _own_draft(did, who):
    r = db().execute("SELECT * FROM drafts WHERE id=? AND who=?", (did, who)).fetchone()
    if not r:
        abort(Response('{"ok": false, "err": "Not found."}', status=404, mimetype="application/json"))
    return r


def draft_json(did, who):
    r = dict(_own_draft(did, who))
    r["extracted"] = json.loads(r["extracted"] or "null")
    r["flags"] = json.loads(r["flags"] or "[]")
    if r["extracted"]:
        r["duplicate"] = find_duplicate(r["extracted"].get("name"), r["extracted"].get("ingredients"))
    return r


@app.route("/api/drafts/<int:did>")
def api_draft(did):
    who, _n = need_api()
    return jsonify(ok=True, draft=draft_json(did, who))


@app.route("/api/drafts/<int:did>/attach")
def api_draft_attach(did):
    who, _n = need_api()
    r = _own_draft(did, who)
    if not r["attach"]:
        abort(404)
    return send_from_directory(ATTACH, r["attach"])


@app.route("/api/recipes/<int:rid>/attach")
def api_recipe_attach(rid):
    who, _n = need_api()
    r = _recipe_or_404(rid, who)
    if not r["attach"]:
        abort(404)
    return send_from_directory(ATTACH, r["attach"])


def publish_draft(did, who, name, d):
    """A person has read the draft and says this is the recipe. Only now does
    it enter the pool -- after a duplicate check, which 'force' overrides."""
    r = _own_draft(did, who)
    if r["status"] in ("confirmed", "discarded"):
        return jsonify(ok=False, err="Already %s." % r["status"]), 400
    d = dict(d or {})
    d.setdefault("source", r["url"] or ("shared %s" % r["kind"]))
    d.setdefault("attach", r["attach"])
    return add_recipe(d, who, name, force=bool(d.get("force")), draft_id=did)


@app.route("/api/drafts/<int:did>/confirm", methods=["POST"])
def api_draft_confirm(did):
    who, name = need_api()
    return publish_draft(did, who, name, request.get_json(silent=True) or {})


def discard_draft(did, who):
    _own_draft(did, who)
    db().execute("UPDATE drafts SET status='discarded', updated=? WHERE id=?", (now_s(), did))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/drafts/<int:did>/discard", methods=["POST"])
def api_draft_discard(did):
    who, _n = need_api()
    return discard_draft(did, who)


@app.route("/help")
def help_page():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kitchen_shortcut.html")
    try:
        with open(p, encoding="utf-8") as fh:
            return Response(fh.read(), mimetype="text/html")
    except OSError:
        abort(404)


def make_app():
    os.makedirs(ATTACH, exist_ok=True)
    import kitchen_members
    kitchen_members.install(app)
    return app

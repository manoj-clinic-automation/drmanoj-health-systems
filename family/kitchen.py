#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kitchen.py -- the Family Kitchen: one shared pool of recipes and ratings.

FAMILY_EDITION_V1 (Phase C). Runs as its own Linux user (fam_kitchen) at
https://family.dr-manoj.in/kitchen/, database /srv/family/kitchen/kitchen.db.

WHAT IS IN THE POOL, AND WHAT NEVER IS
  Recipes (name, group, servings, ingredients with amounts, method, source,
  an attachment, who added it -- a display name), ratings 1-5, "made it",
  "would make again", a short note. That is all. No condition, medicine,
  lab, symptom, weight or anything else about a person has a column here, and
  test_family_c.py fails if one ever appears (SCHEMA_FORBIDDEN).
  Everything personal -- the adjusted card, the portion, the nutrition for
  THIS person, the "modified" badges -- is worked out inside each person's
  own GutLog from their own record. The shared recipe is never altered.

WHO CAN CALL IT
  * each copy's GutLog, server to server, with that copy's API token
    (tokens.json "api": {slug: {token, name}}); the token decides who is
    acting, so a copy cannot rate or confirm as someone else;
  * the iPhone Share shortcut, with a CAPTURE token, at /capture/<slug>
    only: a capture token can add a draft for its own slug and do nothing
    else -- not read, not confirm, not capture for anyone else.
  Drafts are private to their slug until confirmed. Nothing in a draft
  reaches the pool (and so nutrition, adjustments or RxGuard) until a person
  confirms it; confirmation checks for a duplicate first.

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

from flask import Flask, Response, abort, g, jsonify, request, send_from_directory

MARKER = "FAMILY_EDITION_V1"
APP_VERSION = "1.0.0"
KDIR = os.environ.get("KITCHEN_DIR", "/srv/family/kitchen")
DB_PATH = os.path.join(KDIR, "kitchen.db")
ATTACH = os.path.join(KDIR, "attach")
TOKENS = os.environ.get("KITCHEN_TOKENS", os.path.join(KDIR, "tokens.json"))
MAX_MB = 20
ALLOWED = {".jpg", ".jpeg", ".png", ".pdf", ".heic", ".webp", ".txt"}
GROUPS = ["Dal & curry", "Sabzi", "Rice & roti", "Breakfast", "Snack", "Sweet", "Drink", "Salad",
          "Egg, paneer & fish", "Other"]
# A pool column may never be named for health data. The schema test reads this.
SCHEMA_FORBIDDEN = ("condition", "medicine", "medic", "drug", "dose", "lab", "symptom", "pain", "weight",
                    "bmi", "diagnos", "allerg", "bp", "sugar", "hba1c", "sodium", "health", "patient")

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
"""

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024


def now_s():
    return datetime.now().isoformat(timespec="seconds")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.executescript(SCHEMA)
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
    """(slug, display name) for a valid API token, else (None, None)."""
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
    with a name that shares a word. Returns {id, name, why} or None."""
    n = norm_name(name)
    w = ing_words(ings)
    for r in db().execute("SELECT id, name, ingredients FROM recipes").fetchall():
        if exclude and r["id"] == exclude:
            continue
        if norm_name(r["name"]) == n and n:
            return {"id": r["id"], "name": r["name"], "why": "same name"}
        other = ing_words(json.loads(r["ingredients"] or "[]"))
        if w and other:
            j = len(w & other) / float(len(w | other))
            if j >= 0.7 and set(n.split()) & set(norm_name(r["name"]).split()):
                return {"id": r["id"], "name": r["name"], "why": "%d%% of the same ingredients" % int(j * 100)}
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


def clean_lines(v, n=40, width=400):
    if isinstance(v, str):
        v = [x for x in re.split(r"\n+", v) if x.strip()]
    return [str(x).strip()[:width] for x in (v or [])[:n] if str(x).strip()]


def recipe_json(r, who=None, full=False):
    d = dict(r)
    for k in ("ingredients", "method", "notes"):
        d[k] = json.loads(d[k] or "[]")
    rt = db().execute("SELECT COUNT(stars) AS n, AVG(stars) AS avg, SUM(made) AS made, SUM(again) AS again "
                      "FROM ratings WHERE recipe_id=?", (r["id"],)).fetchone()
    d["rating"] = {"n": rt["n"] or 0, "avg": round(rt["avg"], 1) if rt["avg"] else None,
                   "made": rt["made"] or 0, "again": rt["again"] or 0}
    d["new"] = (d.get("added_at") or "") >= (datetime.now() - timedelta(days=7)).isoformat()
    if who:
        mine = db().execute("SELECT stars, made, again, note FROM ratings WHERE recipe_id=? AND who=?",
                            (r["id"], who)).fetchone()
        d["mine"] = dict(mine) if mine else None
    if full:
        names = dict((s, (v or {}).get("name", s)) for s, v in (tokens().get("api") or {}).items())
        d["ratings"] = [dict(x, who=names.get(x["who"], "someone")) for x in db().execute(
            "SELECT who, stars, made, again, note, at FROM ratings WHERE recipe_id=? ORDER BY at DESC",
            (r["id"],)).fetchall()]
        d["variants"] = [dict(x) for x in db().execute(
            "SELECT id, name, variant_label FROM recipes WHERE variant_of=? ORDER BY id", (r["id"],)).fetchall()]
    else:
        d.pop("method", None)
        d.pop("notes", None)
    return d


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
    q = norm_name(request.args.get("q"))
    grp = request.args.get("grp") or ""
    sort = request.args.get("sort") or "az"
    rows = db().execute("SELECT * FROM recipes ORDER BY name").fetchall()
    out = []
    for r in rows:
        if grp and r["grp"] != grp:
            continue
        if q and q not in norm_name(r["name"]) and not any(q in norm_name(i.get("item"))
                                                           for i in json.loads(r["ingredients"] or "[]")):
            continue
        out.append(recipe_json(r, who))
    if sort == "top":
        out = [x for x in out if x["rating"]["n"]]
        out.sort(key=lambda x: (-(x["rating"]["avg"] or 0), -x["rating"]["n"], x["name"]))
    elif sort == "new":
        out = [x for x in out if x["new"]]
        out.sort(key=lambda x: x.get("added_at") or "", reverse=True)
    elif sort == "fav":
        out = [x for x in out if x["rating"]["again"] >= 2 or (x["rating"]["made"] >= 2 and (x["rating"]["avg"] or 0) >= 4)]
        out.sort(key=lambda x: (-x["rating"]["again"], -x["rating"]["made"], x["name"]))
    return jsonify(ok=True, recipes=out, groups=GROUPS)


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
    try:
        variant_of = int(variant_of) if variant_of not in (None, "") else None
    except (TypeError, ValueError):
        variant_of = None
    db().execute("INSERT INTO recipes(slug, name, grp, servings, serving_text, ingredients, method, notes, "
                 "source, attach, added_by, added_slug, added_at, updated, variant_of, variant_label) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (slug, nm, grp, serv, str(d.get("serving_text") or "")[:80], json.dumps(ings),
                  json.dumps(method), json.dumps(clean_lines(d.get("notes"), 20)),
                  str(d.get("source") or "")[:200], str(d.get("attach") or "")[:80], name, who,
                  now_s(), now_s(), variant_of, str(d.get("variant_label") or "")[:40]))
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    if draft_id:
        db().execute("UPDATE drafts SET status='confirmed', recipe_id=?, updated=? WHERE id=?",
                     (rid, now_s(), draft_id))
    db().commit()
    return jsonify(ok=True, id=rid, duplicate=dup)


@app.route("/api/recipes/<int:rid>")
def api_recipe(rid):
    who, _n = need_api()
    r = db().execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="Not found."), 404
    return jsonify(ok=True, recipe=recipe_json(r, who, full=True))


@app.route("/api/recipes/<int:rid>/rate", methods=["POST"])
def api_rate(rid):
    who, _n = need_api()
    if not db().execute("SELECT 1 FROM recipes WHERE id=?", (rid,)).fetchone():
        return jsonify(ok=False, err="Not found."), 404
    d = request.get_json(silent=True) or {}
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


@app.route("/capture/<slug>", methods=["POST"])
def capture(slug):
    """The Share shortcut. Text, a link, a photo or a PDF -- lands as a draft."""
    if not re.match(r"^(m[0-9]{1,3}|owner)$", slug) or not capture_ok(slug):
        return jsonify(ok=False, err="unauthorised"), 401
    ids = []
    text = (request.form.get("text") or "").strip()
    for f in request.files.getlist("file")[:10]:
        if f and f.filename:
            if os.path.splitext(f.filename)[1].lower() == ".txt" or (f.mimetype or "").startswith("text/"):
                # Shared text arrives as a .txt file too; read it, never file it.
                if not text:
                    text = f.read(200000).decode("utf-8", "replace").strip()
                continue
            stored, err = _save_upload(f)
            if not stored:
                return jsonify(ok=False, err=err), 400
            ids.append(new_draft(slug, attach=stored))
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
    rows = db().execute("SELECT id, kind, url, attach, status, note, created, updated, extracted, flags "
                        "FROM drafts WHERE who=? AND status NOT IN ('confirmed','discarded') "
                        "ORDER BY id DESC", (who,)).fetchall()
    out = []
    for r in rows:
        x = dict(r)
        x["extracted"] = json.loads(x["extracted"] or "null")
        x["flags"] = json.loads(x["flags"] or "[]")
        out.append(x)
    return jsonify(ok=True, drafts=out)


def _own_draft(did, who):
    r = db().execute("SELECT * FROM drafts WHERE id=? AND who=?", (did, who)).fetchone()
    if not r:
        abort(Response('{"ok": false, "err": "Not found."}', status=404, mimetype="application/json"))
    return r


@app.route("/api/drafts/<int:did>")
def api_draft(did):
    who, _n = need_api()
    r = dict(_own_draft(did, who))
    r["extracted"] = json.loads(r["extracted"] or "null")
    r["flags"] = json.loads(r["flags"] or "[]")
    if r["extracted"]:
        r["duplicate"] = find_duplicate(r["extracted"].get("name"), r["extracted"].get("ingredients"))
    return jsonify(ok=True, draft=r)


@app.route("/api/drafts/<int:did>/attach")
def api_draft_attach(did):
    who, _n = need_api()
    r = _own_draft(did, who)
    if not r["attach"]:
        abort(404)
    return send_from_directory(ATTACH, r["attach"])


@app.route("/api/recipes/<int:rid>/attach")
def api_recipe_attach(rid):
    need_api()
    r = db().execute("SELECT attach FROM recipes WHERE id=?", (rid,)).fetchone()
    if not r or not r["attach"]:
        abort(404)
    return send_from_directory(ATTACH, r["attach"])


@app.route("/api/drafts/<int:did>/confirm", methods=["POST"])
def api_draft_confirm(did):
    """A person has read the draft and says this is the recipe. Only now does
    it enter the pool -- after a duplicate check, which 'force' overrides."""
    who, name = need_api()
    r = _own_draft(did, who)
    if r["status"] in ("confirmed", "discarded"):
        return jsonify(ok=False, err="Already %s." % r["status"]), 400
    d = request.get_json(silent=True) or {}
    d.setdefault("source", r["url"] or ("shared %s" % r["kind"]))
    d.setdefault("attach", r["attach"])
    return add_recipe(d, who, name, force=bool(d.get("force")), draft_id=did)


@app.route("/api/drafts/<int:did>/discard", methods=["POST"])
def api_draft_discard(did):
    who, _n = need_api()
    _own_draft(did, who)
    db().execute("UPDATE drafts SET status='discarded', updated=? WHERE id=?", (now_s(), did))
    db().commit()
    return jsonify(ok=True)


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
    return app

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.37.0 -> v3.38.0  ::  GUTLOG_V3380_KITCHEN -- the Family Kitchen.

WHY: the family shares recipes. One pool (the Family Kitchen service,
family/kitchen.py) holds recipes and ratings only; each GutLog -- the owner's
and every family copy -- reads it and shows each person THEIR card: the same
recipe, with their own adjustments and their own portion, worked out from
their own record, inside their own copy. The shared recipe is never changed.

WHAT
  * /kitchen: browse (groups, search, top-rated, new this week, family
    favourites), a recipe card, Recipe Inbox, paste / type a recipe, and the
    Share-shortcut key for this person.
  * Nutrition per serving from the ingredients: kcal, protein, fibre (fat,
    carbs, calcium where a food carries them), each ingredient turned into
    grams by kitchen_measures.json (katori, cup, tbsp, tsp, pinch, sizes), and
    matched to this person's own food library first, then the bundled USDA
    table. An ingredient that cannot be turned into grams or matched is listed
    as unmatched -- never guessed.
  * Personal adjustments from kitchen_rules.json (low-FODMAP swaps and the
    Food Test's pulse limit, glucose, sodium, lipids, weight, medicine-food
    from this person's RxGuard), each with a "modified" badge and its reason.
  * One tap logs a serving as a meal, with the ADJUSTED portion's nutrition.
  * Recipe Inbox: captured drafts with the original attachment beside the
    fields, uncertain items highlighted, ingredients not in the food table
    marked; confirm (after a duplicate check) or discard. A draft is never
    given nutrition or adjustments -- only a confirmed recipe is.
  * One line on the Now tab: "Family Kitchen", when the pool is reachable.

No schema change. Anchor-verified, idempotent, compile-checked, .bak,
self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3380_KITCHEN"
PREV = "GUTLOG_V3370_JOINT"
VERSION = "3.38.0"

E = []
E.append(("header",
          'GUTLOG_V3370_JOINT -- joint pain, pain-medicine totals, steps against pain, lipid checks.\n',
          'GUTLOG_V3370_JOINT -- joint pain, pain-medicine totals, steps against pain, lipid checks.\n'
          'GUTLOG_V3380_KITCHEN -- the Family Kitchen: shared recipes, personal cards, Recipe Inbox.\n'))
E.append(("version", 'APP_VERSION = "3.37.0"   # GUTLOG_V3370_JOINT ',
          'APP_VERSION = "3.38.0"   # GUTLOG_V3380_KITCHEN GUTLOG_V3370_JOINT '))

KITCHEN_PY = r'''
# ------------------------------------------------------------------ kitchen
# GUTLOG_V3380_KITCHEN. The pool (family/kitchen.py) holds recipes and ratings
# only. Everything personal is worked out HERE, from this copy's own record:
# nutrition per serving, the adjustments and their reasons, the portion. The
# pool is only ever sent recipes a person confirmed, ratings, and drafts.
KITCHEN_URL = os.environ.get("GUTLOG_KITCHEN_URL", "http://127.0.0.1:8199/kitchen")
KITCHEN_TOKEN_FILE = os.environ.get("GUTLOG_KITCHEN_TOKEN_FILE", "/root/family/kitchen/owner.token")
KITCHEN_CAPTURE_FILE = os.environ.get("GUTLOG_KITCHEN_CAPTURE_FILE", "/root/family/kitchen/owner.capture")
KITCHEN_CAPTURE_URL = os.environ.get("GUTLOG_KITCHEN_CAPTURE_URL",
                                     "https://family.dr-manoj.in/kitchen/capture/owner")
KITCHEN_HELP_URL = os.environ.get("GUTLOG_KITCHEN_HELP_URL", "https://family.dr-manoj.in/kitchen/help")
KITCHEN_NUTR = ("kcal", "protein", "fibre", "fat", "carbs", "calcium")
_KITCHEN_DATA = {}


def _secret_file(p):
    try:
        with open(p, encoding="utf-8") as fh:
            v = fh.read().strip()
        return v if len(v) >= 32 else None
    except OSError:
        return None


def kitchen_call(method, path, body=None, raw=False, timeout=8):
    """(status, json or (bytes, content type)). Never raises."""
    import urllib.request
    import urllib.error
    tok = _secret_file(KITCHEN_TOKEN_FILE)
    if not tok:
        return 0, {"ok": False, "err": "The Family Kitchen is not connected to this diary."}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(KITCHEN_URL.rstrip("/") + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            b, st, ct = r.read(), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        b, st, ct = e.read(), e.code, e.headers.get("Content-Type", "")
    except Exception:
        return 0, {"ok": False, "err": "The Family Kitchen could not be reached just now."}
    if raw:
        return st, (b, ct)
    try:
        return st, json.loads(b.decode("utf-8"))
    except ValueError:
        return st, {"ok": False, "err": "The Family Kitchen gave an unexpected answer."}


def kitchen_data(name):
    if name not in _KITCHEN_DATA:
        try:
            with open(os.path.join(BASE, name), encoding="utf-8") as fh:
                _KITCHEN_DATA[name] = json.load(fh)
        except (OSError, ValueError):
            _KITCHEN_DATA[name] = {}
    return _KITCHEN_DATA[name]


def _kw(text, words):
    t = (text or "").lower()
    return next((w for w in words if re.search(r"\b" + re.escape(w.lower()) + r"\b", t)), None)


def ing_grams(ing):
    """(grams, how) -- or (None, why). Grams as written win; then mass, a
    pinch, a volume through the food's density, a size through piece weights."""
    m = kitchen_data("kitchen_measures.json")
    item = (ing.get("item") or "").lower()
    if ing.get("grams"):
        try:
            return float(ing["grams"]), "as written"
        except (TypeError, ValueError):
            pass
    unit = (ing.get("unit") or "").lower().strip()
    q = ing.get("qty")
    if unit == "pinch":
        return float(m.get("pinch_g") or 0.3) * float(q or 1), "a pinch"
    if q in (None, ""):
        return None, "no amount given"
    try:
        q = float(q)
    except (TypeError, ValueError):
        return None, "no amount given"
    if unit in (m.get("mass_g") or {}):
        return q * m["mass_g"][unit], unit
    if unit in (m.get("volume_ml") or {}):
        dens = next((d for k, d in (m.get("density") or []) if _kw(item, [k])),
                    m.get("density_default") or 1.0)
        return q * m["volume_ml"][unit] * dens, "%s at %s g/ml" % (unit, dens)
    size = unit if unit in ("small", "medium", "large") else "medium" if unit in ("", "piece") else None
    if size:
        pw = next((p for k, p in (m.get("piece_g") or []) if _kw(item, [k])), None)
        if pw and pw.get(size):
            return q * pw[size], "%s %s" % (size, "piece")
    return None, "the measure '%s' is not in the table" % (unit or "piece")


def ing_food(item):
    """(per-100 g values, where they came from) -- this person's own food
    library first, then the bundled table's full match. None when neither."""
    for r in db().execute("SELECT * FROM library WHERE lower(item)=lower(?)", (item.strip(),)).fetchall():
        if r["b_kcal"] is not None or r["b_protein"] is not None:
            bq = r["basis_qty"] or 100.0
            f = 100.0 / bq
            return ({"kcal": (r["b_kcal"] or 0) * f, "protein": (r["b_protein"] or 0) * f,
                     "fibre": (r["b_fibre"] or 0) * f}, "your food list")
    hits = [h for h in food_lookup(item, limit=3) if h.get("full")]
    if hits:
        h = hits[0]
        return {"kcal": h["kcal"], "protein": h["protein"], "fibre": h["fibre"]}, "USDA: " + h["desc"]
    return None, None


def recipe_nutrition(ings, servings, portion=1.0):
    tot = dict((k, 0.0) for k in KITCHEN_NUTR)
    have = dict((k, False) for k in KITCHEN_NUTR)
    unmatched, matched = [], []
    for i in ings:
        g, how = ing_grams(i)
        f, src = ing_food(i.get("item") or "") if g else (None, None)
        if g is None or f is None:
            unmatched.append({"item": i.get("item"), "why": how if g is None else "not in the food table"})
            continue
        for k in KITCHEN_NUTR:
            if f.get(k) is not None:
                tot[k] += g * float(f[k]) / 100.0
                have[k] = True
        matched.append({"item": i.get("item"), "grams": round(g, 1), "how": how, "from": src})
    try:
        s = max(1.0, float(servings or 1))
    except (TypeError, ValueError):
        s = 1.0
    per = dict((k, (round(tot[k] / s * portion, 1) if have[k] else None)) for k in KITCHEN_NUTR)
    return {"per_serving": per, "unmatched": unmatched, "matched": matched}


def _lab_latest(pattern):
    rx_ = re.compile(pattern, re.I)
    best = None
    for d, t, v in [(r["day"], r["test"], r["num"]) for r in db().execute(
            "SELECT day, test, num FROM rec_labs WHERE num IS NOT NULL").fetchall()] + \
            [(r["day"], r["analyte"], r["value"]) for r in db().execute(
                "SELECT day, analyte, value FROM labs WHERE value IS NOT NULL").fetchall()]:
        if t and rx_.search(t) and (best is None or d > best[0]):
            best = (d, float(v))
    return best[1] if best else None


def kitchen_context():
    """This person's own record, as the rules need it. Read here, never sent."""
    conds = set(c for c in (rec_profile().get("conditions") or []) if isinstance(c, str))
    try:
        mp = json.loads(setting("member_profile") or "{}")
    except ValueError:
        mp = {}
    conds |= set(c for c in (mp.get("conditions") or []) if isinstance(c, str))
    limits = []
    groups = dict((it.get("slug"), it.get("group")) for it in ft_items())
    for r in db().execute("SELECT slug, outcome, limit_g FROM ft_outcome").fetchall():
        if r["outcome"] == "limit" and r["limit_g"]:
            limits.append((groups.get(r["slug"]) or "", float(r["limit_g"])))
    rx = _link_get(RXGUARD_URL + "/api/feed/dose?q=classes", ttl=300) or {}
    bmi = None
    try:
        h = float(mp.get("height_cm") or 0)
        w = db().execute("SELECT weight FROM vitals WHERE weight IS NOT NULL ORDER BY day DESC, vtime DESC "
                         "LIMIT 1").fetchone()
        w = float(w["weight"]) if w else float(mp.get("weight_kg") or 0)
        if h > 0 and w > 0:
            bmi = round(w / ((h / 100.0) ** 2), 1)
    except (TypeError, ValueError):
        bmi = None
    return {"conditions": conds, "ft_limits": limits, "bmi": bmi,
            "labs": {"hba1c": _lab_latest(r"hba1c|glycosylated|glycated"),
                     "sodium": _lab_latest(r"\bsodium\b"), "ldl": _lab_latest(r"\bldl\b")},
            "med_classes": [c for c in rx.get("med_classes") or []],
            "cyp3a4": [m for m in rx.get("cyp3a4_major") or []]}


def _when(w, ctx):
    if not w:
        return True
    if "any" in w:
        return any(_when(x, ctx) for x in w["any"])
    if "conditions_any" in w:
        return bool(set(w["conditions_any"]) & ctx["conditions"])
    if "lab" in w:
        v = ctx["labs"].get(w["lab"])
        if v is None:
            return False
        return (v >= w["gte"]) if "gte" in w else (v < w["lt"]) if "lt" in w else False
    if "ft_limit_group" in w:
        return any(g.lower() == w["ft_limit_group"].lower() for g, _l in ctx["ft_limits"])
    if "med_class_any" in w:
        have = [c.lower() for c in ctx["med_classes"]]
        return any(any(x.lower() in c for c in have) for x in w["med_class_any"])
    if "cyp3a4_major" in w:
        return bool(ctx["cyp3a4"]) == bool(w["cyp3a4_major"])
    if "bmi_gte" in w:
        return ctx["bmi"] is not None and ctx["bmi"] >= w["bmi_gte"]
    return False


def adjust_recipe(recipe, ctx):
    """(ingredients for THIS person, portion factor, badges). The recipe dict
    passed in is copied, never changed -- the pool keeps the original."""
    ings = [dict(i) for i in recipe.get("ingredients") or []]
    for i in ings:
        i["was"] = None
    portion, badges = 1.0, []
    serv = float(recipe.get("servings") or 1)
    for rule in (kitchen_data("kitchen_rules.json").get("rules") or []):
        if not _when(rule.get("when"), ctx):
            continue
        act = rule.get("action") or {}
        words = (rule.get("match") or {}).get("items_any")
        hit = [i for i in ings if words and _kw(i.get("item"), words)] if words else []
        if words and not hit:
            continue
        t = act.get("type")
        fill = {"limit": "", "classes": "", "meds": "", "bmi": ctx.get("bmi") or ""}
        if t == "portion":
            portion *= float(act.get("factor") or 1)
        elif t == "scale":
            for i in hit:
                g, _h = ing_grams(i)
                i["was"] = i.get("text") or i.get("item")
                if g:
                    i["grams"] = round(g * float(act["factor"]), 1)
                elif i.get("qty"):
                    i["qty"] = round(float(i["qty"]) * float(act["factor"]), 2)
                i["text"] = "%s (%s)" % (i.get("item"), "reduced")
        elif t == "swap":
            to = act.get("to") or {}
            for i in hit:
                i["was"] = i.get("text") or i.get("item")
                i.update({"item": to.get("item"), "qty": to.get("qty"), "unit": to.get("unit") or "",
                          "grams": None, "text": to.get("text") or to.get("item")})
        elif t == "remove":
            for i in hit:
                i["was"] = i.get("text") or i.get("item")
                i["removed"] = True
        elif t == "cap_grams_per_serving":
            lim = min([l_ for g_, l_ in ctx["ft_limits"]
                       if g_.lower() == ((rule.get("when") or {}).get("ft_limit_group") or "").lower()] or [0])
            if not lim:
                continue
            fill["limit"] = ("%g" % lim)
            cap = lim * serv
            capped = False
            for i in hit:
                g, _h = ing_grams(i)
                if g and g > cap:
                    i["was"] = i.get("text") or i.get("item")
                    i["grams"] = round(cap, 1)
                    i["text"] = "%s (%s g a serving)" % (i.get("item"), fill["limit"])
                    capped = True
            if not capped:
                continue
        if "{classes}" in (rule.get("reason") or ""):
            fill["classes"] = ", ".join(c for c in ctx["med_classes"] if any(
                x.lower() in c.lower() for x in (rule.get("when") or {}).get("med_class_any") or []))
        fill["meds"] = ", ".join(ctx["cyp3a4"])
        reason = rule.get("reason") or ""
        for k, v in fill.items():
            reason = reason.replace("{" + k + "}", str(v))
        badges.append({"rule": rule.get("id"), "family": rule.get("family"), "reason": reason})
    ings = [i for i in ings if not i.get("removed")] + [i for i in ings if i.get("removed")]
    return ings, portion, badges


def kitchen_card(recipe):
    ctx = kitchen_context()
    ings, portion, badges = adjust_recipe(recipe, ctx)
    live = [i for i in ings if not i.get("removed")]
    nut = recipe_nutrition(live, recipe.get("servings"), portion)
    return {"recipe": recipe, "ingredients": ings, "portion": portion, "badges": badges,
            "modified": bool(badges), "nutrition": nut}


@app.route("/kitchen")
@login_required
def kitchen_page():
    return Response(KITCHEN_PAGE, mimetype="text/html")


@app.route("/api/kitchen/ping")
@login_required
def api_kitchen_ping():
    st, j = kitchen_call("GET", "/api/recipes?sort=new")
    return jsonify(ok=st == 200, n_new=len(j.get("recipes") or []) if st == 200 else 0)


@app.route("/api/kitchen/recipes")
@login_required
def api_kitchen_recipes():
    from urllib.parse import urlencode
    q = dict((k, request.args.get(k)) for k in ("q", "grp", "sort") if request.args.get(k))
    st, j = kitchen_call("GET", "/api/recipes" + ("?" + urlencode(q) if q else ""))
    return jsonify(j), (st if st else 502)


@app.route("/api/kitchen/recipe/<int:rid>")
@login_required
def api_kitchen_recipe(rid):
    st, j = kitchen_call("GET", "/api/recipes/%d" % rid)
    if st != 200:
        return jsonify(j), (st or 502)
    return jsonify(ok=True, **kitchen_card(j["recipe"]))


@app.route("/api/kitchen/recipe/<int:rid>/rate", methods=["POST"])
@login_required
def api_kitchen_rate(rid):
    d = J()
    body = dict((k, d[k]) for k in ("stars", "made", "again", "note") if k in d)
    st, j = kitchen_call("POST", "/api/recipes/%d/rate" % rid, body)
    return jsonify(j), (st or 502)


@app.route("/api/kitchen/recipe/<int:rid>/log", methods=["POST"])
@login_required
def api_kitchen_log(rid):
    """One tap: a serving (or what was eaten) of THIS person's card, as a meal."""
    st, j = kitchen_call("GET", "/api/recipes/%d" % rid)
    if st != 200:
        return jsonify(j), (st or 502)
    card = kitchen_card(j["recipe"])
    try:
        n = max(0.25, min(4.0, float(J().get("servings") or 1)))
    except (TypeError, ValueError):
        n = 1.0
    per = card["nutrition"]["per_serving"]
    item = {"n": ("Family Kitchen: " + j["recipe"]["name"])[:80], "q": n,
            "p": per.get("protein") or 0, "k": per.get("kcal") or 0, "f": per.get("fibre") or 0,
            "fm": "L-M" if any(b["family"] == "low-FODMAP" for b in card["badges"]) else "M"}
    day, mtime = today(), now_hm()
    slot = meal_slot_guess(day, mtime)
    insert("meals", ["day", "mtime", "slot", "items", "protein", "kcal", "fibre", "fscore", "notes"],
           [day, mtime, slot, json.dumps([item]), round(n * item["p"], 1), round(n * item["k"]),
            round(n * item["f"], 1), round(n * FMAP[item["fm"]], 2),
            ("modified: " + "; ".join(b["rule"] for b in card["badges"])) if card["badges"] else ""])
    mid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    ft_dinner_sync(day)
    return jsonify(ok=True, id=mid, slot=slot, kcal=round(n * item["k"]), protein=round(n * item["p"], 1))


@app.route("/api/kitchen/drafts", methods=["GET", "POST"])
@login_required
def api_kitchen_drafts():
    if request.method == "POST":
        d = J()
        st, j = kitchen_call("POST", "/api/drafts", {"text": (d.get("text") or "")[:20000],
                                                     "url": (d.get("url") or "")[:600]})
        return jsonify(j), (st or 502)
    st, j = kitchen_call("GET", "/api/drafts")
    return jsonify(j), (st or 502)


@app.route("/api/kitchen/drafts/<int:did>")
@login_required
def api_kitchen_draft(did):
    """A draft as captured, with its flags -- and which ingredient names the
    food table does not know. Never nutrition or adjustments: those are for a
    confirmed recipe only."""
    st, j = kitchen_call("GET", "/api/drafts/%d" % did)
    if st != 200:
        return jsonify(j), (st or 502)
    ex = (j.get("draft") or {}).get("extracted") or {}
    unknown = [i.get("item") for i in ex.get("ingredients") or []
               if i.get("item") and ing_food(i["item"])[0] is None]
    return jsonify(ok=True, draft=j["draft"], unknown=unknown)


@app.route("/api/kitchen/drafts/<int:did>/attach")
@login_required
def api_kitchen_draft_attach(did):
    st, (b, ct) = kitchen_call("GET", "/api/drafts/%d/attach" % did, raw=True) \
        if _secret_file(KITCHEN_TOKEN_FILE) else (0, (b"", ""))
    if st != 200:
        abort(404)
    r = Response(b, mimetype=(ct or "application/octet-stream").split(";")[0])
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@app.route("/api/kitchen/drafts/<int:did>/<act>", methods=["POST"])
@login_required
def api_kitchen_draft_act(did, act):
    if act not in ("confirm", "discard"):
        abort(404)
    st, j = kitchen_call("POST", "/api/drafts/%d/%s" % (did, act), J() if act == "confirm" else {})
    return jsonify(j), (st or 502)


@app.route("/api/kitchen/capture")
@login_required
def api_kitchen_capture():
    tok = _secret_file(KITCHEN_CAPTURE_FILE)
    return jsonify(ok=bool(tok), url=KITCHEN_CAPTURE_URL, token=tok or "", help=KITCHEN_HELP_URL)

'''
E.append(("kitchen block",
          "# ------------------------------------------------------------------ joints\n"
          "# GUTLOG_V3370_JOINT. For the Family Edition's `joint` profile, shown only\n",
          KITCHEN_PY.lstrip("\n") + "\n"
          "# ------------------------------------------------------------------ joints\n"
          "# GUTLOG_V3370_JOINT. For the Family Edition's `joint` profile, shown only\n"))

PAGE = r'''
KITCHEN_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Family Kitchen</title>
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--mod:#8a4b00;--bad:#a4262c}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--mod:#f0b35a;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:36rem;margin:0 auto;padding:12px 14px 40px}h1{font-size:21px;margin:4px 0 10px}
.tabs,.chips{display:flex;flex-wrap:wrap;gap:6px}.tab,.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:7px 12px;font:inherit;font-size:15px;cursor:pointer}
.tab.sel,.chip.sel{background:var(--acc);color:#fff;border-color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0;overflow-wrap:anywhere}
.row{display:flex;justify-content:space-between;gap:8px}.mut{color:var(--mut);font-size:15px}
.badge{display:inline-block;color:var(--mod);border:1px solid var(--mod);border-radius:6px;padding:0 6px;font-size:13px;font-weight:600}
.flag{color:var(--bad)}.was{text-decoration:line-through;color:var(--mut)}
input,textarea,select{width:100%;font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
button.btn{font:inherit;font-weight:600;padding:10px 14px;border-radius:10px;border:0;background:var(--acc);color:#fff;margin:8px 6px 0 0}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc)}
.ir{display:grid;grid-template-columns:1fr 64px 76px;gap:4px;margin:4px 0}img.att{max-width:100%;border-radius:8px}
a{color:var(--acc)}
</style></head><body><main>
<p><a href="/">&larr; Back</a></p><h1>Family Kitchen</h1>
<div class="tabs" id="tabs"></div><div id="view"></div>
<div id="toast" class="mut" style="position:fixed;bottom:12px;left:12px;right:12px;text-align:center"></div>
</main>
<script>
/* GUTLOG_V3380_KITCHEN */
const $=q=>document.querySelector(q);
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;}
function toast(m){const t=$('#toast');t.textContent=m;setTimeout(()=>{t.textContent='';},2200);}
async function jget(u){const r=await fetch(u,{credentials:'same-origin'});return r.json();}
async function post(u,b){const r=await fetch(u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const j=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(j.err||'Save failed');e.j=j;e.status=r.status;throw e;}return j;}
const TABS=[['browse','Recipes'],['inbox','Inbox'],['add','Add a recipe'],['share','Share shortcut']];
let ST={tab:'browse',grp:'',sort:'az',q:''};
function tabs(){const b=$('#tabs');b.innerHTML='';TABS.forEach(([k,l])=>{const x=el('button','tab'+(ST.tab===k?' sel':''),l);x.onclick=()=>{ST.tab=k;show();};b.appendChild(x);});}
function show(){tabs();({browse,inbox,add,share})[ST.tab]();}
async function browse(){
  const v=$('#view');v.innerHTML='';
  const s=el('input');s.placeholder='Search a dish or an ingredient';s.value=ST.q;
  s.onchange=()=>{ST.q=s.value;browse();};v.appendChild(s);
  const so=el('div','chips');so.style.marginTop='8px';
  [['az','All'],['top','Top rated'],['new','New this week'],['fav','Family favourites']].forEach(([k,l])=>{
    const c=el('button','chip'+(ST.sort===k?' sel':''),l);c.onclick=()=>{ST.sort=k;browse();};so.appendChild(c);});
  v.appendChild(so);
  let j;try{j=await jget('/api/kitchen/recipes?sort='+ST.sort+'&q='+encodeURIComponent(ST.q)+'&grp='+encodeURIComponent(ST.grp));}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}
  const g=el('div','chips');g.style.marginTop='8px';
  ['',...(j.groups||[])].forEach(x=>{const c=el('button','chip'+(ST.grp===x?' sel':''),x||'Every group');c.onclick=()=>{ST.grp=x;browse();};g.appendChild(c);});
  v.appendChild(g);
  if(!(j.recipes||[]).length)v.appendChild(el('p','mut','Nothing here yet.'));
  (j.recipes||[]).forEach(r=>{
    const c=el('div','card');c.style.cursor='pointer';
    const h=el('div','row');h.appendChild(el('b','',r.name));
    h.appendChild(el('span','mut',r.rating.avg?('★ '+r.rating.avg+' ('+r.rating.n+')'):''));c.appendChild(h);
    c.appendChild(el('p','mut',r.grp+' · added by '+(r.added_by||'someone')+(r.new?' · new':'')+
      (r.rating.made?(' · made '+r.rating.made+'×'):'')));
    c.onclick=()=>recipe(r.id);v.appendChild(c);});
}
async function recipe(id){
  const v=$('#view');v.innerHTML='';
  let j;try{j=await jget('/api/kitchen/recipe/'+id);}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'Not found.'));return;}
  const r=j.recipe,c=el('div','card');
  c.appendChild(el('h2','',r.name));
  c.appendChild(el('p','mut',r.grp+' · serves '+r.servings+(r.serving_text?(' · '+r.serving_text):'')+' · added by '+r.added_by));
  if(j.modified){const b=el('p','');b.appendChild(el('span','badge','modified for you'));c.appendChild(b);
    j.badges.forEach(x=>c.appendChild(el('p','mut','• '+x.reason)));}
  if(j.portion!==1)c.appendChild(el('p','','Your portion: '+j.portion+' of a serving.'));
  c.appendChild(el('h3','','Ingredients'));
  j.ingredients.forEach(i=>{const p=el('p','');p.style.margin='2px 0';
    if(i.removed){p.className='was';p.textContent=i.was;}
    else{p.textContent=i.text||(((i.qty!=null?i.qty+' ':'')+(i.unit||'')+' '+i.item).trim());
      if(i.was){p.appendChild(el('span','mut',' (was: '+i.was+')'));}}
    c.appendChild(p);});
  c.appendChild(el('h3','','Method'));
  (r.method||[]).forEach((m,k)=>c.appendChild(el('p','',(k+1)+'. '+m)));
  (r.notes||[]).forEach(m=>c.appendChild(el('p','mut',m)));
  const n=j.nutrition.per_serving,nu=el('div','card');
  nu.appendChild(el('b','','Per '+(j.portion!==1?'your ':'')+'serving'));
  const f=[['kcal','kcal',''],['protein','protein',' g'],['fibre','fibre',' g'],['fat','fat',' g'],['carbs','carbs',' g'],['calcium','calcium',' mg']];
  nu.appendChild(el('p','',f.map(([k,l,u])=>l+' '+(n[k]==null?'—':n[k]+u)).join(' · ')));
  if(j.nutrition.unmatched.length){nu.appendChild(el('p','flag','Not counted: '+j.nutrition.unmatched.map(x=>x.item+' ('+x.why+')').join('; ')));}
  c.appendChild(nu);
  const lg=el('button','btn','Log a serving as a meal');
  lg.onclick=async()=>{try{const x=await post('/api/kitchen/recipe/'+id+'/log',{servings:1});toast('Logged as '+x.slot+' · '+x.kcal+' kcal');}catch(e){toast(e.message);}};
  c.appendChild(lg);
  const rt=el('div','chips');rt.style.marginTop='10px';
  [1,2,3,4,5].forEach(s=>{const b=el('button','chip'+((r.mine&&r.mine.stars===s)?' sel':''),'★'.repeat(s));
    b.onclick=async()=>{await post('/api/kitchen/recipe/'+id+'/rate',{stars:s});recipe(id);};rt.appendChild(b);});
  c.appendChild(rt);
  const mk=el('div','chips');mk.style.marginTop='6px';
  [['made','I made it'],['again','Would make again']].forEach(([k,l])=>{const on=r.mine&&r.mine[k];
    const b=el('button','chip'+(on?' sel':''),l);b.onclick=async()=>{const o={};o[k]=!on;await post('/api/kitchen/recipe/'+id+'/rate',o);recipe(id);};mk.appendChild(b);});
  c.appendChild(mk);
  const note=el('input');note.placeholder='A short note for the family';note.value=(r.mine&&r.mine.note)||'';
  note.onchange=async()=>{await post('/api/kitchen/recipe/'+id+'/rate',{note:note.value});toast('Saved');};
  note.style.marginTop='8px';c.appendChild(note);
  (r.ratings||[]).filter(x=>x.note).forEach(x=>c.appendChild(el('p','mut',x.who+': '+x.note)));
  const bk=el('button','btn ghost','Back to recipes');bk.onclick=browse;c.appendChild(bk);
  v.appendChild(c);
}
async function inbox(){
  const v=$('#view');v.innerHTML='';
  let j;try{j=await jget('/api/kitchen/drafts');}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}
  if(!(j.drafts||[]).length)v.appendChild(el('p','mut','Nothing waiting. Shared recipes land here to be checked.'));
  (j.drafts||[]).forEach(d=>{const c=el('div','card');c.style.cursor='pointer';
    c.appendChild(el('b','',(d.extracted&&d.extracted.name)||('A shared '+d.kind)));
    c.appendChild(el('p','mut',d.status==='new'||d.status==='reading'?'Being read …':(d.note||d.status)));
    if((d.flags||[]).length)c.appendChild(el('p','flag',d.flags.length+' thing'+(d.flags.length>1?'s':'')+' to check'));
    c.onclick=()=>review(d.id);v.appendChild(c);});
}
function irow(box,i){const r=el('div','ir');
  const a=el('input');a.value=i.item||'';a.placeholder='ingredient';
  const q=el('input');q.value=i.qty==null?'':i.qty;q.placeholder='amount';q.inputMode='decimal';
  const u=el('input');u.value=i.unit||'';u.placeholder='unit';
  if(i.qty==null&&i.unit!=='pinch')q.style.borderColor='var(--bad)';
  if(i.unknown)a.style.borderColor='var(--mod)';
  [a,q,u].forEach(x=>r.appendChild(x));r._get=()=>({item:a.value.trim(),qty:q.value===''?null:parseFloat(q.value),unit:u.value.trim(),text:i.text||''});
  box.appendChild(r);return r;}
async function review(id){
  const v=$('#view');v.innerHTML='';
  const j=await jget('/api/kitchen/drafts/'+id);if(!j.ok){v.appendChild(el('p','mut',j.err||'Not found.'));return;}
  const d=j.draft,ex=d.extracted||{name:'',servings:null,ingredients:[],method:[]};
  const c=el('div','card');
  if(d.attach){if(/\.(jpe?g|png|webp)$/i.test(d.attach)){const im=el('img','att');im.src='/api/kitchen/drafts/'+id+'/attach';c.appendChild(im);}
    else{const a=el('a','','Open the shared file');a.href='/api/kitchen/drafts/'+id+'/attach';a.target='_blank';c.appendChild(a);}}
  if(d.url){const a=el('a','',d.url);a.href=d.url;a.target='_blank';a.rel='noopener';c.appendChild(el('p','')).appendChild(a);}
  if(d.text&&!ex.name)c.appendChild(el('p','mut',d.text.slice(0,600)));
  (d.flags||[]).forEach(f=>c.appendChild(el('p','flag','⚠ '+f.text)));
  if((j.unknown||[]).length)c.appendChild(el('p','mut','Not in the food table (their nutrition will not count): '+j.unknown.join(', ')));
  if(j.draft.duplicate)c.appendChild(el('p','flag','Looks like "'+j.draft.duplicate.name+'" already in the Kitchen ('+j.draft.duplicate.why+').'));
  const nm=el('input');nm.value=ex.name||'';nm.placeholder='Dish name';c.appendChild(el('p','','Name'));c.appendChild(nm);
  const gp=el('select');['Dal & curry','Sabzi','Rice & roti','Breakfast','Snack','Sweet','Drink','Salad','Egg, paneer & fish','Other'].forEach(x=>{const o=el('option','',x);o.value=x;gp.appendChild(o);});
  gp.value='Other';c.appendChild(el('p','','Group'));c.appendChild(gp);
  const sv=el('input');sv.value=ex.servings||'';sv.inputMode='decimal';c.appendChild(el('p','','Serves'));c.appendChild(sv);
  c.appendChild(el('p','','Ingredients'));const ib=el('div');c.appendChild(ib);
  const unk=new Set(j.unknown||[]);let rows=(ex.ingredients||[]).map(i=>irow(ib,Object.assign({},i,{unknown:unk.has(i.item)})));
  const ad=el('button','btn ghost','+ ingredient');ad.onclick=()=>rows.push(irow(ib,{}));c.appendChild(ad);
  const me=el('textarea');me.rows=6;me.value=(ex.method||[]).join('\n');c.appendChild(el('p','','Method (one step a line)'));c.appendChild(me);
  const go=async(force)=>{try{
      const x=await post('/api/kitchen/drafts/'+id+'/confirm',{name:nm.value,grp:gp.value,servings:sv.value,force:!!force,
        ingredients:rows.map(r=>r._get()).filter(i=>i.item),method:me.value.split('\n').filter(s=>s.trim())});
      toast('In the Family Kitchen now');ST.tab='browse';show();}
    catch(e){if(e.status===409&&e.j&&e.j.duplicate){if(confirm('"'+e.j.duplicate.name+'" is already in the Kitchen ('+e.j.duplicate.why+'). Add this one anyway?'))go(true);}else toast(e.message);}};
  const ok=el('button','btn','Confirm — add to the Kitchen');ok.onclick=()=>go(false);c.appendChild(ok);
  const no=el('button','btn ghost','Discard');no.onclick=async()=>{await post('/api/kitchen/drafts/'+id+'/discard');inbox();};c.appendChild(no);
  v.appendChild(c);
}
function add(){
  const v=$('#view');v.innerHTML='';const c=el('div','card');
  c.appendChild(el('p','','Paste a recipe, or a link to one. It goes to your Inbox to be checked before it joins the Kitchen.'));
  const t=el('textarea');t.rows=8;c.appendChild(t);
  const b=el('button','btn','Send to my Inbox');b.onclick=async()=>{const s=t.value.trim();if(!s)return;
    const m=s.match(/^https?:\/\/\S+$/);try{await post('/api/kitchen/drafts',m?{url:s}:{text:s});toast('In your Inbox');t.value='';}catch(e){toast(e.message);}};
  c.appendChild(b);v.appendChild(c);
}
async function share(){
  const v=$('#view');v.innerHTML='';const c=el('div','card');
  const j=await jget('/api/kitchen/capture');
  if(!j.ok){c.appendChild(el('p','mut','Sharing is not set up for this diary yet.'));v.appendChild(c);return;}
  c.appendChild(el('p','','The iPhone Share button can send a recipe straight to your Inbox — from WhatsApp, a web page, a photo or a PDF. When the shortcut asks, paste these two:'));
  c.appendChild(el('p','mut','Address'));const a=el('input');a.readOnly=true;a.value=j.url;c.appendChild(a);
  c.appendChild(el('p','mut','Key (keep it private)'));const k=el('input');k.readOnly=true;k.value=j.token;c.appendChild(k);
  const h=el('a','','The 10-minute setup guide');h.href=j.help;h.target='_blank';c.appendChild(el('p','')).appendChild(h);
  v.appendChild(c);
}
show();
</script></body></html>"""

'''
E.append(("kitchen page", "\ndef kitchen_page():\n", "\ndef kitchen_page():\n"))   # placeholder, replaced below

# The page is a module constant; put it right after the kitchen block's routes.
E[-1] = ("kitchen page",
         "@app.route(\"/api/kitchen/capture\")\n",
         PAGE.lstrip("\n") + "\n@app.route(\"/api/kitchen/capture\")\n")

E.append(("now kitchen div",
          '  <div id="nowFamily"></div><!-- GUTLOG_V3360_FAMILY -->\n',
          '  <div id="nowFamily"></div><!-- GUTLOG_V3360_FAMILY -->\n'
          '  <div id="nowKitchen"></div><!-- GUTLOG_V3380_KITCHEN -->\n'))
E.append(("now kitchen js",
          "/* GUTLOG_V3360_FAMILY -- one line, only when there are members. */\n",
          "/* GUTLOG_V3380_KITCHEN -- one line, only when the Kitchen answers. */\n"
          "async function loadKitchenLink(){\n"
          "  const box=$('#nowKitchen');if(!box)return;\n"
          "  let j;try{j=await jget('/api/kitchen/ping');}catch(e){return;}\n"
          "  box.innerHTML='';\n"
          "  if(!j||!j.ok)return;\n"
          "  const p=el('p','hint');p.style.margin='0 2px 10px';\n"
          "  const a=el('a','','Family Kitchen'+(j.n_new?(' \\u00b7 '+j.n_new+' new this week'):''));a.href='/kitchen';\n"
          "  p.appendChild(a);box.appendChild(p);\n"
          "}\n"
          "/* GUTLOG_V3360_FAMILY -- one line, only when there are members. */\n"))
E.append(("loadNow kitchen",
          "  loadJoint();   /* GUTLOG_V3370_JOINT */\n",
          "  loadJoint();   /* GUTLOG_V3370_JOINT */\n  loadKitchenLink();   /* GUTLOG_V3380_KITCHEN */\n"))

EDITS = E
JINJA = ("{{", "{%", "{#")


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("==================================================================")
    print("GutLog Family Kitchen -> v" + VERSION)
    print("file : " + a.file)
    print("==================================================================")
    for label, old, new in EDITS:
        for tok in JINJA:
            if new.count(tok) > old.count(tok):
                print("FATAL: %s adds the Jinja token %r. Nothing written." % (label, tok))
                return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    for need in ("kitchen_measures.json", "kitchen_rules.json"):
        if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(a.file)), need)):
            print("FATAL: %s must sit beside app.py first." % need)
            return 1
    # The kitchen block and the page are applied in order: the page's anchor
    # lives inside the block.
    out = src
    for l, o, n in EDITS:
        if out.count(o) != 1:
            print("anchor %s: found %d times, need 1. Nothing written." % (l, out.count(o)))
            return 1
        out = out.replace(o, n, 1)
    print("anchors: %d/%d matched" % (len(EDITS), len(EDITS)))
    if a.check:
        print("All anchors OK.")
        return 0
    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    bak = a.file + ".bak-v3380-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    print("backup : " + bak)
    write(a.file, out)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, a.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(EDITS))
    print("Next:  python3 test_family_c.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

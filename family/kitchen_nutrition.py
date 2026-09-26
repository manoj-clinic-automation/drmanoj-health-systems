#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kitchen_nutrition.py -- nutrition per serving for a kitchen member's card.

FAMILY_EDITION_V1 (kitchen members). A full member's GutLog works nutrition
out inside their own copy (their food list first, then the bundled USDA
table). A kitchen member has no GutLog, so the Kitchen service does the same
sum for them from the bundled table alone: the same measures file, the same
word rules, the same "unmatched, never guessed" answer. Both files are read
from the family code tree beside this folder (<tree>/gutlog/), where
build_code.py puts them; KITCHEN_GUT_DIR points elsewhere for a test.

Nothing personal is involved: no adjustments, no portion, no record.
Python 3.9.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GUT_DIR = os.environ.get("KITCHEN_GUT_DIR") or os.path.join(os.path.dirname(HERE), "gutlog")
NUTR = ("kcal", "protein", "fibre", "fat", "carbs", "calcium")
_CACHE = {}

# The same word rules GutLog's food_lookup uses (kept in step by test_family_d).
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


def _load(name):
    if name not in _CACHE:
        try:
            with open(os.path.join(GUT_DIR, name), encoding="utf-8") as fh:
                _CACHE[name] = json.load(fh)
        except (OSError, ValueError):
            _CACHE[name] = {}
    return _CACHE[name]


def food_table():
    if "words" not in _CACHE:
        doc = _load("food_table_usda.json")
        rows = doc.get("foods") or [] if isinstance(doc, dict) else []
        _CACHE["rows"] = rows
        _CACHE["words"] = [frozenset(_fstem(w) for w in re.findall(r"[a-z]+", str(r[1]).lower()))
                           for r in rows]
    return _CACHE["rows"], _CACHE["words"]


def food_lookup(q, limit=3):
    """The bundled table's full matches only (every query word in the name)."""
    rows, words = food_table()
    if not rows:
        return []
    s = " " + " ".join(re.findall(r"[a-z]+", (q or "").lower())) + " "
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
    for r, ws in zip(rows, words):
        m = sum(1 for t in toks if t in ws)
        if m != len(toks):
            continue
        low = str(r[1]).lower()
        noise = 1 if (low.startswith(FOOD_NOISE) or re.search(r"\b(?!USDA\b)[A-Z]{3,}\b", str(r[1]))) else 0
        first = re.findall(r"[a-z]+", low)
        primary = 0 if (first and _fstem(first[0]) in toks) else 1
        found.append(((-m, noise, primary, len(low)), r))
    found.sort(key=lambda x: x[0])
    # 26-Sep-2026: the refreshed table carries fat, carbs and calcium as columns 5-7.
    return [{"fdc": r[0], "desc": r[1], "protein": r[2], "kcal": r[3], "fibre": r[4],
             "fat": r[5] if len(r) > 5 else None, "carbs": r[6] if len(r) > 6 else None,
             "calcium": r[7] if len(r) > 7 else None} for _, r in found[:limit]]


def _kw(text, words):
    t = (text or "").lower()
    return next((w for w in words if re.search(r"\b" + re.escape(w.lower()) + r"\b", t)), None)


def ing_grams(ing):
    """(grams, how) -- or (None, why). Grams as written win; then mass, a
    pinch, a volume through the food's density, a size through piece weights."""
    m = _load("kitchen_measures.json")
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
    hits = food_lookup(item, limit=3)
    if hits:
        h = hits[0]
        return {"kcal": h["kcal"], "protein": h["protein"], "fibre": h["fibre"], "fat": h.get("fat"),
                "carbs": h.get("carbs"), "calcium": h.get("calcium")}, "USDA: " + str(h["desc"])
    return None, None


def recipe_nutrition(ings, servings):
    """Per serving of the recipe as shared -- kcal, protein, fibre (fat, carbs,
    calcium where the table carries them); ingredients it cannot weigh or
    find are listed as unmatched, never guessed."""
    tot = dict((k, 0.0) for k in NUTR)
    have = dict((k, False) for k in NUTR)
    unmatched, matched = [], []
    for i in ings or []:
        g, how = ing_grams(i)
        f, src = ing_food(i.get("item") or "") if g else (None, None)
        if g is None or f is None:
            unmatched.append({"item": i.get("item"), "why": how if g is None else "not in the food table"})
            continue
        for k in NUTR:
            if f.get(k) is not None:
                tot[k] += g * float(f[k]) / 100.0
                have[k] = True
        matched.append({"item": i.get("item"), "grams": round(g, 1), "how": how, "from": src})
    try:
        s = max(1.0, float(servings or 1))
    except (TypeError, ValueError):
        s = 1.0
    per = dict((k, (round(tot[k] / s, 1) if have[k] else None)) for k in NUTR)
    return {"per_serving": per, "unmatched": unmatched, "matched": matched}

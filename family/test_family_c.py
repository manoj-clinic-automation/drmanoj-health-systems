#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_c.py -- Family Edition Phase C: the Family Kitchen.

    python3 -B family/test_family_c.py gutlog/app.py

The scratch family (famtest.Rig) with the Kitchen running from the same code
tree, tokens issued by the real stamp tool. Synthetic recipes and names; the
medicine classes RxGuard reports are chosen from its knowledge at run time,
so this file names no medicine. No network call leaves the machine: the
reader is tested against a local page, with no model key present.
Python 3.9.
"""
import base64
import http.server
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")
FORBIDDEN_COLS = ("condition", "medicine", "medic", "drug", "dose", "lab", "symptom", "pain", "weight",
                  "bmi", "diagnos", "allerg", "sugar", "hba1c", "sodium", "health", "patient")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def load(path, name, env):
    old = dict(os.environ)
    os.environ.update(env)
    sys.path.insert(0, os.path.dirname(path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)
        os.environ.clear()
        os.environ.update(old)


def kdb(rig):
    c = sqlite3.connect(os.path.join(rig.kitchen_dir, "kitchen.db"))
    c.row_factory = sqlite3.Row
    return c


def pool_text(rig):
    c = kdb(rig)
    out = []
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in c.execute('SELECT * FROM "%s"' % t).fetchall():
            out.append(" ".join(str(x) for x in row if x is not None))
    c.close()
    return "\n".join(out)


def capture(rig, slug, token, data=None, files=None):
    import urllib.request
    import urllib.error
    bnd = "famtestboundary"
    parts = []
    for k, v in (data or {}).items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (bnd, k, v)).encode())
    for fn, blob, ct in files or []:
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                      "Content-Type: %s\r\n\r\n" % (bnd, fn, ct)).encode() + blob + b"\r\n")
    body = b"".join(parts) + ("--%s--\r\n" % bnd).encode()
    h = {"Content-Type": "multipart/form-data; boundary=" + bnd}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(rig.front_url + "/kitchen/capture/" + slug, data=body, headers=h, method="POST")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {}


def add_med(c, slug, name, molecule):
    c.post("/%s/api/prnmeds" % slug, {"name": name}, ctype="json")
    mid = next(m["id"] for m in c.get("/%s/api/prnmeds/full" % slug).json() if m["name"] == name)
    c.post("/%s/api/salt" % slug, {"med_id": mid, "molecule": molecule, "strength": "10 mg"}, ctype="json")
    c.post("/%s/api/schedule" % slug, {"med_id": mid, "slot": "MORNING"}, ctype="json")


def confirm_text(cl, slug, name, ings, method, servings=2, grp="Other", force=False):
    r = cl.post("/%s/api/kitchen/drafts" % slug, {"text": "a recipe"}, ctype="json")
    did = (r.json() or {}).get("id")
    r = cl.post("/%s/api/kitchen/drafts/%s/confirm" % (slug, did),
                {"name": name, "grp": grp, "servings": servings, "ingredients": ings,
                 "method": method, "force": force}, ctype="json")
    return did, r


def run(rig):
    with open(os.path.join(famtest.app_src("rx"), "knowledge", "drugs.json"), encoding="utf-8") as fh:
        drugs = json.load(fh)["drugs"]
    ace = next(k for k, d in sorted(drugs.items()) if "ace inhibitor" in (d.get("class") or "").lower())
    c3a4 = next(k for k, d in sorted(drugs.items())
                if ((d.get("cyp") or {}).get("substrate") or {}).get("CYP3A4") == "major"
                and (d.get("burden") or {}).get("sedation", 0) == 0)
    owner = rig.owner_client()
    m1, m2 = rig.member_client("m1"), rig.member_client("m2")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs", "height_cm": "160"})
    m2.post("/m2/welcome", {"name": "Member B", "age": "68", "cond": ["knee_oa", "high_cholesterol"],
                            "height_cm": "155", "weight_kg": "70"})
    add_med(m2, "m2", "Medicine P", ace)
    add_med(m2, "m2", "Medicine Q", c3a4)
    tok = rig.kitchen_tokens()

    # ---------------------------------------------------------------- C10 seeding
    oc = sqlite3.connect(rig.owner_env["GUTLOG_DB"])
    cards = [
        {"id": "test-dal", "name": "Test onion dal", "group": "A", "source": "card", "serves": 2,
         "serving": "1 katori", "ing": [["moong dal", 100, "100 g moong"], ["onion", 80, "1 onion"],
                                          ["ghee", 10, "2 tsp ghee"]],
         "method": ["Cook the dal until soft, then add what you like."], "notes": ["Keeps two days."],
         "flags": {"onion": True, "garlic": False}, "onion_free": ["Temper with hing instead.",
                                                                     "Start at your trial dose of 20 g."]},
        {"id": "test-poha", "name": "Test lemon poha", "group": "A", "source": "card", "serves": 1,
         "serving": "1 plate", "ing": [["poha", 60, "60 g"], ["peanuts", 10, "10 g"]], "method": ["Soak, temper."],
         "notes": [], "flags": {"onion": False, "garlic": False}, "onion_free": []}]
    for c_ in cards:
        oc.execute("INSERT INTO recipes(slug, name, grp, stage, data, updated) VALUES(?,?,?,?,?,?)",
                   (c_["id"], c_["name"], c_["group"], "tried", json.dumps(c_), "2026-09-01"))
    oc.commit()
    oc.close()
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "seed_kitchen.py"),
                        "--gutlog-db", rig.owner_env["GUTLOG_DB"], "--kitchen-db",
                        os.path.join(rig.kitchen_dir, "kitchen.db"), "--name", "Owner A", "--apply"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode()
    c = kdb(rig)
    seeded = dict((x["slug"], dict(x)) for x in c.execute("SELECT * FROM recipes").fetchall())
    c.close()
    var = seeded.get("owner-test-dal-onion-free") or {}
    vi = [i["item"] for i in json.loads(var.get("ingredients") or "[]")]
    check("C10 the owner's cards seed the pool, with the onion-free version beside, personal lines left out",
          r.returncode == 0 and "owner-test-dal" in seeded and "owner-test-poha" in seeded and var
          and "onion" not in vi and any("hing" in x for x in vi) and var.get("variant_of")
          and "trial dose" not in pool_text(rig) and "1 personal note line" in out
          and seeded["owner-test-dal"]["added_by"] == "Owner A"
          and "what you like" in seeded["owner-test-dal"]["method"], out[-300:])
    r2 = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "seed_kitchen.py"),
                         "--gutlog-db", rig.owner_env["GUTLOG_DB"], "--kitchen-db",
                         os.path.join(rig.kitchen_dir, "kitchen.db"), "--apply"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    check("C10 seeding twice adds nothing", "0 card(s) to add" in r2.stdout.decode(), r2.stdout.decode()[-200:])

    # ---------------------------------------------------------------- C04 capture
    ctok = dict((s, v["token"]) for s, v in tok["capture"].items())
    before = len(seeded)
    res = [capture(rig, "m1", ctok["m1"], {"text": "Aloo sabzi\n2 potatoes\nfry"}),
           capture(rig, "m1", ctok["m1"], {"text": "https://example.invalid/recipe/aloo"}),
           capture(rig, "m1", ctok["m1"], {"text": "IMG_0001.jpeg"}, [("IMG_0001.jpeg", PNG, "image/jpeg")]),
           capture(rig, "m1", ctok["m1"], None, [("card.pdf", PDF, "application/pdf")])]
    no_tok = capture(rig, "m1", None, {"text": "x"})
    other = capture(rig, "m1", ctok["m2"], {"text": "x"})
    api_with_cap = m1.get(rig.front_url + "/kitchen/api/recipes",
                          headers={"Authorization": "Bearer " + ctok["m1"]})
    c = kdb(rig)
    kinds = [x["kind"] for x in c.execute("SELECT kind FROM drafts WHERE who='m1' ORDER BY id").fetchall()]
    n_rec = c.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    c.close()
    check("C04 the capture endpoint takes text, a link, a photo and a PDF, as drafts only",
          all(s == 200 for s, _j in res) and kinds == ["text", "link", "image", "pdf"] and n_rec == before,
          "%s kinds %s recipes %d->%d" % ([s for s, _ in res], kinds, before, n_rec))
    check("C04 the capture endpoint refuses no token, another member's token, and reads nothing",
          no_tok[0] == 401 and other[0] == 401 and api_with_cap.status == 401,
          "%s %s %s" % (no_tok[0], other[0], api_with_cap.status))

    # ---------------------------------------------------------------- C05 / C07 inbox
    d = m1.get("/m1/api/kitchen/drafts").json() or {}
    ids = [x["id"] for x in d.get("drafts") or []]
    dd = m1.get("/m1/api/kitchen/drafts/%d" % ids[-1]).json() or {} if ids else {}
    check("C07 an unconfirmed draft never reaches nutrition or adjustments",
          dd.get("ok") and set(dd) <= {"ok", "draft", "unknown"} and "nutrition" not in json.dumps(dd)
          and "badges" not in json.dumps(dd), sorted(dd))
    other_draft = m2.get("/m2/api/kitchen/drafts/%d" % ids[-1]) if ids else None
    check("C05 one member cannot open another member's draft",
          other_draft is not None and other_draft.status == 404, other_draft.status if other_draft else None)
    ings = [{"item": "rice", "qty": 1, "unit": "cup"}, {"item": "ghee", "qty": 1, "unit": "tbsp"},
            {"item": "salt", "qty": None, "unit": "pinch"}, {"item": "zzq leaf", "qty": 2, "unit": "g"},
            {"item": "pepper", "qty": None, "unit": ""}]
    _d, r = confirm_text(m1, "m1", "Test ghee rice", ings, ["Cook the rice.", "Add ghee."], servings=2)
    rid_rice = (r.json() or {}).get("id")
    _d2, dup = confirm_text(m1, "m1", "Test Ghee Rice!", ings, ["Cook."], servings=2)
    _d3, forced = confirm_text(m1, "m1", "Test Ghee Rice!", ings, ["Cook."], servings=2, force=True)
    check("C05 Inbox confirm moves a draft into the pool; a duplicate is caught, and can be added knowingly",
          r.status == 200 and rid_rice and dup.status == 409 and (dup.json() or {}).get("duplicate", {}).get("id") == rid_rice
          and forced.status == 200, "%s %s %s" % (r.status, dup.status, forced.status))

    # ---------------------------------------------------------------- C02 nutrition
    gut = load(os.path.join(famtest.app_src("gut"), "app.py"), "gut_c",
               {"GUTLOG_DB": os.path.join(tempfile.mkdtemp(prefix="famc_"), "g.db"), "GUTLOG_NOSPAWN": "1",
                "GUTLOG_LINKS": "0", "GUTLOG_INSECURE": "1"})
    per100 = {}
    for item in ("rice", "ghee", "salt"):
        hit = [h for h in gut.food_lookup(item, limit=3) if h.get("full")][0]
        per100[item] = hit
    grams = {"rice": 240 * 0.78, "ghee": 15 * 0.91, "salt": 0.3}
    exp_k = round(sum(grams[i] * per100[i]["kcal"] / 100.0 for i in grams) / 2, 1)
    exp_p = round(sum(grams[i] * per100[i]["protein"] / 100.0 for i in grams) / 2, 1)
    card = m1.get("/m1/api/kitchen/recipe/%s" % rid_rice).json() or {}
    nut = card.get("nutrition") or {}
    um = [u["item"] for u in nut.get("unmatched") or []]
    check("C02 nutrition per serving from the ingredients, household measures converted to grams",
          (nut.get("per_serving") or {}).get("kcal") == exp_k and (nut.get("per_serving") or {}).get("protein") == exp_p,
          "got %s want kcal %s protein %s" % (nut.get("per_serving"), exp_k, exp_p))
    # 26-Sep-2026: the refreshed table carries fat, so a matched card now has a fat figure
    # (it used to be None); what is asserted here is the unmatched list.
    check("C02 unmatched ingredients are shown, never guessed",
          set(um) == {"zzq leaf", "pepper"} and (nut.get("per_serving") or {}).get("fat") is not None,
          nut.get("unmatched"))

    # ---------------------------------------------------------------- C03 adjustments
    c = kdb(rig)
    dal_id = c.execute("SELECT id FROM recipes WHERE slug='owner-test-dal'").fetchone()[0]
    dal_row = dict(c.execute("SELECT * FROM recipes WHERE id=?", (dal_id,)).fetchone())
    c.close()
    gc = sqlite3.connect(rig.db("m1", "gut"))
    gc.execute("INSERT INTO ft_items(slug, pos, data, seeded) VALUES('pulse-a', 1, ?, '2026-09-01')",
               (json.dumps({"slug": "pulse-a", "group": "GOS", "label": "Pulse A"}),))
    gc.execute("INSERT INTO ft_outcome(slug, outcome, limit_g, set_at) VALUES('pulse-a','limit',30,'2026-09-10')")
    gc.commit()
    gc.close()
    a1 = m1.get("/m1/api/kitchen/recipe/%d" % dal_id).json() or {}
    a2 = m2.get("/m2/api/kitchen/recipe/%d" % dal_id).json() or {}
    rules1 = [b["rule"] for b in a1.get("badges") or []]
    rules2 = [b["rule"] for b in a2.get("badges") or []]
    it1 = dict((i["item"], i) for i in a1.get("ingredients") or [])
    it2 = dict((i["item"], i) for i in a2.get("ingredients") or [])
    check("C03 low-FODMAP: onion swapped for hing and pulses capped at the Food Test limit, for IBS only",
          rules1 == ["KR01", "KR02"] and "onion" not in it1 and "hing (asafoetida)" in it1
          and it1.get("moong dal", {}).get("grams") == 60 and "30 g a serving" in json.dumps(a1.get("badges"))
          and "KR01" not in rules2 and "onion" in it2, "m1 %s / m2 %s" % (rules1, rules2))
    check("C03 lipids and weight: ghee halved and a smaller portion, with their reasons, only where recorded",
          "KR06" in rules2 and "KR07" in rules2 and it2.get("ghee", {}).get("grams") == 5
          and a2.get("portion") == 0.8 and "KR06" not in rules1 and "KR07" not in rules1
          and all(b.get("reason") for b in a2.get("badges") or []), "m2 %s portion %s" % (rules2, a2.get("portion")))
    _d, rr = confirm_text(m2, "m2", "Test citrus salad", [{"item": "grapefruit", "qty": 1, "unit": "medium"},
                                                          {"item": "low sodium salt", "qty": None, "unit": "pinch"},
                                                          {"item": "rice", "qty": 50, "unit": "g"}], ["Mix."], 1)
    sid = (rr.json() or {}).get("id")
    s2 = m2.get("/m2/api/kitchen/recipe/%s" % sid).json() or {}
    s1 = m1.get("/m1/api/kitchen/recipe/%s" % sid).json() or {}
    b2 = dict((b["rule"], b["reason"]) for b in s2.get("badges") or [])
    check("C03 medicine-food: grapefruit removed and potassium salt swapped, from the member's own RxGuard",
          "KR08" in b2 and "KR05" in b2 and "CYP3A4" in b2["KR08"] and "ACE" in b2["KR05"]
          and not [b for b in s1.get("badges") or [] if b["rule"] in ("KR05", "KR08")],
          "m2 %s / m1 %s" % (b2, [b["rule"] for b in s1.get("badges") or []]))
    oc = sqlite3.connect(rig.owner_env["GUTLOG_DB"])
    old = (date.today() - timedelta(days=10)).isoformat()
    oc.execute("INSERT INTO rec_labs(day, test, value, num, unit, lab, created) VALUES(?,?,?,?,?,?,?)",
               (old, "HbA1c (Glycosylated Haemoglobin)", "7.1", 7.1, "%", "Lab A", old))
    oc.execute("INSERT INTO rec_labs(day, test, value, num, unit, lab, created) VALUES(?,?,?,?,?,?,?)",
               (old, "Serum Sodium", "131", 131, "mmol/L", "Lab A", old))
    oc.commit()
    oc.close()
    ow = owner.get("/api/kitchen/recipe/%s" % rid_rice).json() or {}
    orules = [b["rule"] for b in ow.get("badges") or []]
    olist = owner.get("/api/kitchen/recipes").json() or {}
    check("C06 the owner's GutLog shows the pool with his own adjustments",
          len(olist.get("recipes") or []) >= 4 and "KR03" in orules and "KR04" in orules
          and "KR01" not in orules, orules)
    c = kdb(rig)
    after = dict(c.execute("SELECT * FROM recipes WHERE id=?", (dal_id,)).fetchone())
    c.close()
    check("C03 the shared recipe is never changed by anyone's adjustments", after == dal_row, "")

    # ---------------------------------------------------------------- C08 log
    lg = m2.post("/m2/api/kitchen/recipe/%d/log" % dal_id, {"servings": 1}, ctype="json")
    gc = sqlite3.connect(rig.db("m2", "gut"))
    row = gc.execute("SELECT kcal, items FROM meals WHERE id=?", ((lg.json() or {}).get("id"),)).fetchone()
    gc.close()
    want = round((a2.get("nutrition") or {}).get("per_serving", {}).get("kcal") or 0)
    check("C08 one tap logs a serving as a meal, with the adjusted portion's nutrition",
          lg.status == 200 and row and row[0] == want and "Family Kitchen" in row[1], "%s want %s" % (row, want))
    m1.post("/m1/api/kitchen/recipe/%d/rate" % dal_id, {"stars": 5, "made": True, "note": "Lovely"}, ctype="json")
    m2.post("/m2/api/kitchen/recipe/%d/rate" % dal_id, {"stars": 4, "again": True}, ctype="json")
    top = (m1.get("/m1/api/kitchen/recipes?sort=top").json() or {}).get("recipes") or []
    new = (m1.get("/m1/api/kitchen/recipes?sort=new").json() or {}).get("recipes") or []
    check("C05 browsing: top-rated, new this week and search work over the shared pool",
          top and top[0]["id"] == dal_id and top[0]["rating"]["avg"] == 4.5 and len(new) >= 4
          and any(x["id"] == rid_rice for x in (m1.get("/m1/api/kitchen/recipes?q=ghee").json() or {}).get("recipes", [])),
          "top %s" % [(x["name"], x["rating"]) for x in top[:2]])

    # ---------------------------------------------------------------- C01 schema + leak
    c = kdb(rig)
    cols = []
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols += [(t, r[1]) for r in c.execute('PRAGMA table_info("%s")' % t).fetchall()]
    c.close()
    # Whole parts of the name ("variant_label" is not "lab"); a prefix for the stems.
    bad = [tc for tc in cols if any(p == f or (len(f) > 4 and p.startswith(f))
                                    for p in tc[1].lower().split("_") for f in FORBIDDEN_COLS)]
    check("C01 the pool holds no health fields (schema)", cols and not bad, bad)
    text = pool_text(rig).lower()
    leaks = [w for w in ("ibs", "irritable", "cholesterol", "hba1c", "sodium is", "knee", "medicine p",
                         "medicine q", ace.lower(), c3a4.lower(), "is recorded", "trial dose")
             if re.search(r"\b" + re.escape(w) + r"\b", text)]
    check("C01 nothing about a member's health reaches the pool (conditions, medicines, reasons)", not leaks, leaks)

    # ---------------------------------------------------------------- C09 the reader
    kr = load(os.path.join(famtest.fam_src(), "kitchen_reader.py"), "kreader",
              {"KITCHEN_KEYS_ENV": os.path.join(rig.work, "no-keys.env"), "ANTHROPIC_API_KEY": "",
               "SARVAM_API_KEY": ""})
    os.environ.pop("ANTHROPIC_API_KEY", None)
    page = ('<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":['
            '{"@type":"Recipe","name":"Page khichdi","recipeYield":"4 servings","recipeIngredient":'
            '["1 cup rice","1/2 cup moong dal","1 tsp ghee","salt"],"recipeInstructions":[{"@type":"HowToStep",'
            '"text":"Wash."},{"@type":"HowToStep","text":"Pressure cook."}]}]}</script></head><body>x</body></html>')

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            b = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/r" % srv.server_address[1]
    ex, flags, note = kr.read_draft({"attach": "", "kind": "link", "text": "", "url": url})
    ex2, flags2, _n = kr.read_draft({"attach": "", "kind": "link", "text": "", "url": "https://www.instagram.com/p/x"})
    ex3, flags3, n3 = kr.read_draft({"attach": "", "kind": "text", "text": "some recipe text", "url": ""})
    srv.shutdown()
    ing = dict((i["item"], i) for i in (ex or {}).get("ingredients") or [])
    check("C09 a web page's own recipe data is read without a model; a missing amount is flagged",
          ex and ex["name"] == "Page khichdi" and ex["servings"] == 4.0 and ing.get("rice", {}).get("qty") == 1
          and ing.get("rice", {}).get("unit") == "cup" and ing.get("moong dal", {}).get("qty") == 0.5
          and ex["method"] == ["Wash.", "Pressure cook."]
          and any(f["kind"] == "quantity" and "salt" in f["text"] for f in flags), (ex, flags))
    check("C09 Instagram keeps the link and asks for a screenshot; with no model key the draft is left to a person",
          ex2 is None and flags2 and flags2[0]["kind"] == "screenshot" and ex3 is None
          and flags3 and flags3[0]["kind"] == "manual" and "by hand" in n3, (flags2, flags3, n3))


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("C00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.23.0 -> v3.24.0  ::  Recipes (GUTLOG_V3240_RECIPES)

His recipe cards, browsable in the Meals tab (third segment, "Recipes"):
search; filter by group (fits now / onion-free version / occasional) and by
stage; a card with the per-serving estimate, plant points, gut flags, the
onion-free version beside the original, whole-pot ingredients (high-FODMAP
ones marked), method and notes; the stage he sets (not tried, on trial, in
rotation, paused, avoid); "Log it" for 1/2, 1, 1 1/2 or 2 servings now; and
"Send to the cook on WhatsApp".

Table `recipes` via SCHEMA (no migration, no schema_version bump), filled by
seed_recipes.py from his gitignored card file. A logged serving is an
ordinary meal row whose item is the recipe (same path as the meal cards).

Not in this build (later, per PLAN_Recipes_Section_v1): adding a recipe by
paste/photo/voice, versions, micronutrients, trials linked to meals.

Requires v3.23.0 (GUTLOG_V3230_CONTEXT). Anchor-verified, idempotent,
compile-checked, refuses Jinja tokens in new page text, .bak before write,
self-restoring, --reverse. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3240_RECIPES"
PREV = "GUTLOG_V3230_CONTEXT"

SERVER = '# ------------------------------------------------------------------ recipes\n# GUTLOG_V3240_RECIPES -- his recipe cards, browsable in the Meals tab: the\n# ingredients, method, per-serving estimate, plant points, gut flags and the\n# onion-free version beside the original; a stage he sets (not tried, on\n# trial, in rotation, paused, avoid); one tap to log a serving; a clean copy\n# to send the cook. The cards live in the database (table recipes), loaded\n# by seed_recipes.py from his gitignored card file -- nothing here names a\n# dish. A logged serving is an ordinary meal whose item is the recipe, so\n# its ingredients stay one join away for the food-symptom comparison.\nRECIPE_STAGES = [("new", "Not tried"), ("trial", "On trial"), ("rotation", "In rotation"),\n                 ("paused", "Paused"), ("avoid", "Avoid")]\nRECIPE_STAGE_KEYS = dict(RECIPE_STAGES)\nRECIPE_GROUPS = {"A": "Fits now", "B": "Has an onion-free version", "C": "Occasional"}\n\n\ndef _recipe_row(r, full=False):\n    d = json.loads(r["data"] or "{}")\n    ps = d.get("per_serving") or {}\n    fl = d.get("flags") or {}\n    out = {"slug": r["slug"], "name": r["name"], "group": r["grp"],\n           "group_label": RECIPE_GROUPS.get(r["grp"], r["grp"]), "stage": r["stage"],\n           "stage_label": RECIPE_STAGE_KEYS.get(r["stage"], r["stage"]),\n           "stage_note": r["stage_note"] or "", "serving": d.get("serving") or "",\n           "kcal": ps.get("kcal"), "protein": ps.get("protein"), "fibre": ps.get("fibre"),\n           "fat": ps.get("fat"), "plant_points": d.get("plant_points"),\n           "onion": bool(fl.get("onion")), "garlic": bool(fl.get("garlic")),\n           "high_fodmap": fl.get("high_fodmap") or [], "has_onion_free": bool(d.get("onion_free"))}\n    if full:\n        out.update(serves=d.get("serves"), ing=d.get("ing") or [], method=d.get("method") or [],\n                   notes=d.get("notes") or [], onion_free=d.get("onion_free") or [],\n                   plants=d.get("plants") or [], source=d.get("source") or "",\n                   yield_est=bool(d.get("yield_est")))\n    return out\n\n\n@app.route("/api/recipes")\n@login_required\ndef api_recipes():\n    rows = [_recipe_row(r) for r in db().execute(\n        "SELECT * FROM recipes ORDER BY grp, name").fetchall()]\n    return jsonify(recipes=rows, stages=[{"key": k, "label": l} for k, l in RECIPE_STAGES],\n                   groups=[{"key": k, "label": v} for k, v in sorted(RECIPE_GROUPS.items())])\n\n\n@app.route("/api/recipes/<slug>")\n@login_required\ndef api_recipe(slug):\n    r = db().execute("SELECT * FROM recipes WHERE slug=?", (slug,)).fetchone()\n    if not r:\n        return jsonify(ok=False, err="No such recipe."), 404\n    out = _recipe_row(r, full=True)\n    out["logged"] = [dict(x) for x in db().execute(\n        "SELECT day, mtime FROM meals WHERE items LIKE ? ORDER BY day DESC, mtime DESC LIMIT 5",\n        (\'%"n": \' + json.dumps(r["name"]) + \'%\',)).fetchall()]\n    return jsonify(ok=True, recipe=out)\n\n\n@app.route("/api/recipes/<slug>/stage", methods=["POST"])\n@login_required\ndef api_recipe_stage(slug):\n    d = J()\n    st = d.get("stage")\n    if st not in RECIPE_STAGE_KEYS:\n        return jsonify(ok=False, err="Unknown stage."), 400\n    cur = db().execute("SELECT stage FROM recipes WHERE slug=?", (slug,)).fetchone()\n    if not cur:\n        return jsonify(ok=False, err="No such recipe."), 404\n    db().execute("UPDATE recipes SET stage=?, stage_note=?, updated=? WHERE slug=?",\n                 (st, note(d, "note", 200), now_s(), slug))\n    db().commit()\n    return jsonify(ok=True, stage=st)\n\n\n@app.route("/api/recipes/<slug>/log", methods=["POST"])\n@login_required\ndef api_recipe_log(slug):\n    """One serving (or ½, 2) of a recipe as a meal, now. The library item\n    carries its per-serving estimate; it is created from the card if absent."""\n    r = db().execute("SELECT * FROM recipes WHERE slug=?", (slug,)).fetchone()\n    if not r:\n        return jsonify(ok=False, err="No such recipe."), 404\n    d = J()\n    try:\n        q = float(d.get("q") or 1)\n    except (TypeError, ValueError):\n        q = 1.0\n    if not db().execute("SELECT 1 FROM library WHERE item=?", (r["name"],)).fetchone():\n        x = _recipe_row(r)\n        fm = "H" if (x["onion"] or x["garlic"]) else ("M-H" if x["high_fodmap"] else "L-M")\n        insert("library", ["cat", "item", "portion", "protein", "kcal", "fibre", "fodmap", "status",\n                           "fav", "tags", "note"],\n               ["H", r["name"][:80], (x["serving"] or "1 serving")[:60], x["protein"] or 0,\n                x["kcal"] or 0, x["fibre"] or 0, fm, "", 0, "recipe estimated",\n                "From your recipe card, per serving; values estimated."])\n    body, code = _log_meal({"card": "", "slot": (d.get("slot") or "Meal")[:30],\n                            "day": d.get("day") or today(), "mtime": d.get("mtime") or now_hm(),\n                            "extra": [{"n": r["name"], "q": q}]})\n    return jsonify(**body), code\n\n\n'
JS = "/* GUTLOG_V3240_RECIPES -- the recipe book in the Meals tab. */\nlet RC=null,rcGroup='',rcStage='',rcOpen=null;\nasync function loadRecipes(){\n  try{RC=await jget('/api/recipes');}catch(e){return;}\n  $('#rc_q').oninput=rcList;\n  if(rcOpen){openRecipe(rcOpen);return;}\n  rcList();\n}\nfunction rcChips(box,items,cur,set){\n  box.innerHTML='';\n  [{key:'',label:'All'}].concat(items).forEach(o=>{\n    const b=el('button','chip'+(cur===o.key?' sel':''),o.label);b.type='button';\n    b.onclick=()=>{set(o.key);rcList();};box.appendChild(b);});\n}\nfunction rcList(){\n  $('#rcOne').style.display='none';$('#rcList').style.display='';\n  rcChips($('#rc_groups'),RC.groups,rcGroup,v=>rcGroup=v);\n  rcChips($('#rc_stages'),RC.stages,rcStage,v=>rcStage=v);\n  const q=($('#rc_q').value||'').trim().toLowerCase();\n  const box=$('#rc_rows');box.innerHTML='';\n  const rows=RC.recipes.filter(r=>(!rcGroup||r.group===rcGroup)&&(!rcStage||r.stage===rcStage)&&\n    (!q||q.split(/\\s+/).every(w=>r.name.toLowerCase().indexOf(w)>=0)));\n  if(!rows.length){box.appendChild(el('p','hint',RC.recipes.length?'No recipe matches.':'No recipes loaded yet.'));return;}\n  rows.forEach(r=>{\n    const b=el('button','rcrow');b.type='button';\n    b.appendChild(el('b','',r.name));\n    b.appendChild(el('small','',[r.serving,r.protein!=null?Math.round(r.protein)+' g protein':'',r.kcal!=null?Math.round(r.kcal)+' kcal':''].filter(Boolean).join(' · ')));\n    const t=el('div','');t.appendChild(el('span','rctag'+(r.stage==='rotation'?' ok':(r.stage==='avoid'?' warn':'')),r.stage_label));\n    if(r.onion||r.garlic)t.appendChild(el('span','rctag warn',r.has_onion_free?'onion/garlic · onion-free version':'onion/garlic'));\n    b.appendChild(t);b.onclick=()=>openRecipe(r.slug);box.appendChild(b);});\n}\nfunction rcShareText(r){\n  const L=[r.name,'For '+(r.serves||'?')+' · serving: '+r.serving,'','Ingredients:'];\n  r.ing.forEach(i=>L.push('- '+i[0]+(i[2]?' — '+i[2]:(i[1]?' — '+i[1]+' g':''))));\n  if(r.onion_free.length){L.push('','Without onion/garlic:');r.onion_free.forEach(s=>L.push('- '+s));}\n  L.push('','Method:');r.method.forEach((s,i)=>L.push((i+1)+'. '+s));\n  return L.join('\\n');\n}\nasync function openRecipe(slug){\n  let j;try{j=await jget('/api/recipes/'+encodeURIComponent(slug));}catch(e){toast('Could not open');return;}\n  if(!j.ok){toast(j.err||'Could not open');return;}\n  const r=j.recipe;rcOpen=slug;\n  $('#rcList').style.display='none';const box=$('#rcOne');box.style.display='';box.innerHTML='';\n  const back=el('button','btn ghost','← All recipes');back.type='button';\n  back.onclick=()=>{rcOpen=null;rcList();};box.appendChild(back);\n  const c=el('div','card');c.style.marginTop='10px';box.appendChild(c);\n  c.appendChild(el('p','q',r.name));\n  c.appendChild(el('p','hint','Serves '+(r.serves||'?')+' · one serving: '+r.serving+' · '+r.group_label+(r.yield_est?' · yield estimated':'')));\n  const n=el('div','rcnum');\n  [['kcal',r.kcal,''],['protein',r.protein,' g'],['fibre',r.fibre,' g'],['fat',r.fat,' g']].forEach(x=>{\n    const d=el('div','');d.appendChild(el('b','',x[1]==null?'–':(Math.round(x[1]*10)/10)+x[2]));d.appendChild(el('span','',x[0]));n.appendChild(d);});\n  c.appendChild(n);\n  c.appendChild(el('p','hint','Per serving, estimated from Indian food tables. '+(r.plant_points||0)+' plant points.'));\n  if(r.onion||r.garlic||r.high_fodmap.length)c.appendChild(el('span','rctag warn','High-FODMAP: '+(r.high_fodmap.join(', ')||'onion/garlic')));\n  else c.appendChild(el('span','rctag ok','No onion or garlic'));\n  if(r.onion_free.length){const o=el('div','rcof');o.appendChild(el('b','','Onion-free version'));\n    const u=el('ul','');r.onion_free.forEach(s=>u.appendChild(el('li','',s)));o.appendChild(u);c.appendChild(o);}\n  const st=el('div','card');box.appendChild(st);st.appendChild(el('p','lbl','Where it stands'));\n  const sc=el('div','chips');RC.stages.forEach(s=>{const b=el('button','chip'+(r.stage===s.key?' sel':''),s.label);b.type='button';\n    b.onclick=async()=>{try{await post('/api/recipes/'+encodeURIComponent(slug)+'/stage',{stage:s.key});toast(r.name+': '+s.label);\n      const x=RC.recipes.find(y=>y.slug===slug);if(x){x.stage=s.key;x.stage_label=s.label;}openRecipe(slug);}catch(err){toast(err.message);}};\n    sc.appendChild(b);});st.appendChild(sc);\n  const lg=el('div','card');box.appendChild(lg);lg.appendChild(el('p','lbl','Had it? Log it now'));\n  let q=1;const qc=el('div','chips');\n  [[0.5,'½ serving'],[1,'1 serving'],[1.5,'1½'],[2,'2 servings']].forEach(x=>{const b=el('button','chip'+(x[0]===q?' sel':''),x[1]);b.type='button';\n    b.onclick=()=>{q=x[0];qc.querySelectorAll('.chip').forEach(y=>y.classList.remove('sel'));b.classList.add('sel');};qc.appendChild(b);});\n  lg.appendChild(qc);\n  const go=el('button','btn primary','Log it');go.type='button';go.style.marginTop='10px';\n  go.onclick=async()=>{if(go.dataset.busy)return;go.dataset.busy=1;\n    try{const h=Number(mcNowHM().slice(0,2));const slot=h<11?'Breakfast':h<16?'Lunch':h<19?'Snack':'Dinner';\n      await post('/api/recipes/'+encodeURIComponent(slug)+'/log',{q:q,slot:slot,day:todayISO,mtime:mcNowHM()});\n      toast(r.name+' logged as '+slot.toLowerCase());openRecipe(slug);}\n    catch(err){go.dataset.busy='';toast(err.message);}};\n  lg.appendChild(go);\n  if(j.recipe.logged&&j.recipe.logged.length)lg.appendChild(el('p','hint','Last had: '+j.recipe.logged.map(x=>x.day+' '+x.mtime).join(', ')));\n  const ic=el('div','card');box.appendChild(ic);ic.appendChild(el('p','lbl','Ingredients (whole pot)'));\n  const ul=el('ul','rcing');const hi=r.high_fodmap.map(x=>x.toLowerCase());\n  r.ing.forEach(i=>{const li=el('li',hi.indexOf(String(i[0]).toLowerCase())>=0?'hi':'');\n    li.appendChild(el('span','',i[0]));li.appendChild(el('span','',i[2]||(i[1]?i[1]+' g':'')));ul.appendChild(li);});\n  ic.appendChild(ul);\n  const mc=el('div','card');box.appendChild(mc);mc.appendChild(el('p','lbl','Method'));\n  const ol=el('ol','rcsteps');r.method.forEach(s=>ol.appendChild(el('li','',s)));mc.appendChild(ol);\n  if(r.notes.length){mc.appendChild(el('p','lbl','Notes'));const nl=el('ul','rcsteps');r.notes.forEach(s=>nl.appendChild(el('li','',s)));mc.appendChild(nl);}\n  const sh=el('button','btn ghost','Send to the cook on WhatsApp');sh.type='button';sh.style.marginTop='6px';\n  sh.onclick=()=>{window.open('https://wa.me/?text='+encodeURIComponent(rcShareText(r)),'_blank');};\n  box.appendChild(sh);\n  window.scrollTo(0,0);\n}\n"
CSS = '/* GUTLOG_V3240_RECIPES */\n.rcrow{display:block;width:100%;text-align:left;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:0 0 8px;font-size:16px}\n.rcrow b{display:block;font-size:16px}\n.rcrow small{display:block;color:var(--muted);font-size:14px;margin-top:3px}\n.rctag{display:inline-block;font-size:13px;font-weight:700;border-radius:999px;padding:2px 9px;margin:6px 6px 0 0;border:1.5px solid var(--line);color:var(--muted)}\n.rctag.warn{border-color:var(--amber);color:var(--amber)}\n.rctag.ok{border-color:var(--teal);color:var(--teal)}\n.rcnum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin:10px 0}\n.rcnum div{border:1px solid var(--line);border-radius:12px;padding:8px 4px;text-align:center}\n.rcnum b{display:block;font-size:18px}.rcnum span{font-size:13px;color:var(--muted)}\n.rcing{margin:0;padding:0;list-style:none}\n.rcing li{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:15px}\n.rcing li span:last-child{color:var(--muted);text-align:right}\n.rcing li.hi span:first-child{color:var(--amber);font-weight:700}\n.rcsteps{margin:0;padding-left:22px;font-size:15px}.rcsteps li{margin:0 0 7px}\n.rcof{border:1.5px solid var(--teal);border-radius:12px;padding:10px 12px;margin:10px 0}\n.rcof ul{margin:6px 0 0;padding-left:20px;font-size:15px}\n'
HTML = '  <div class="sub" id="meals-recipes">\n    <div id="rcList">\n      <input type="text" id="rc_q" placeholder="Search your recipes" autocomplete="off">\n      <div class="chips" id="rc_groups" style="margin-top:8px"></div>\n      <div class="chips" id="rc_stages" style="margin-top:8px"></div>\n      <div id="rc_rows" style="margin-top:10px"></div>\n    </div>\n    <div id="rcOne" style="display:none"></div>\n  </div>\n\n'

EDITS = [
    ("version", "GUTLOG_V3220_MEALS GUTLOG_V3230_CONTEXT\n",
     "GUTLOG_V3220_MEALS GUTLOG_V3230_CONTEXT " + MARKER + "\n"),
    ("schema", "CREATE TABLE IF NOT EXISTS day_context (",
     "CREATE TABLE IF NOT EXISTS recipes (\n"
     "  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, name TEXT, grp TEXT DEFAULT '',\n"
     "  stage TEXT DEFAULT 'new', stage_note TEXT DEFAULT '', data TEXT DEFAULT '{}', updated TEXT);\n"
     "CREATE TABLE IF NOT EXISTS day_context ("),
    ("server", "# ------------------------------------------------------------------ PRN doses\n",
     SERVER + "# ------------------------------------------------------------------ PRN doses\n"),
    ("css", ".mnew{margin-top:10px;padding:10px;border:1.5px dashed var(--line);border-radius:12px}\n",
     ".mnew{margin-top:10px;padding:10px;border:1.5px dashed var(--line);border-radius:12px}\n" + CSS),
    ("seg", '<button data-s="meal" class="sel">Meal</button><button data-s="test">Food test</button>',
     '<button data-s="meal" class="sel">Meal</button><button data-s="test">Food test</button>'
     '<button data-s="recipes">Recipes</button>'),
    ("html", '  <div class="sub" id="meals-test">\n', HTML + '  <div class="sub" id="meals-test">\n'),
    ("js fn", "async function loadDown(){\n", JS + "async function loadDown(){\n"),
    ("js seg", "  if(section==='files')loadRecords(s);\n",
     "  if(section==='files')loadRecords(s);\n  if(section==='meals'&&s==='recipes')loadRecipes();\n"),
    ("save hidden", "(tab==='meals'&&seg.meals==='foods')||",
     "(tab==='meals'&&(seg.meals==='foods'||seg.meals==='recipes'))||"),
]
PAGE_TEXT = [JS, CSS, HTML]


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
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("=" * 66)
    print("GutLog recipes in the Meals tab -> v3.24.0")
    print("file : " + a.file)
    print("=" * 66)
    for t in PAGE_TEXT:
        if re.search(r"\{[{%#]", t):
            print("FATAL: a Jinja token in new page text (CLAUDE.md 5b). Nothing written.")
            return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.23.0.")
        return 1
    bad = [l for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + ". Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cand.py")
    write(f, out)
    try:
        py_compile.compile(f, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
    bak = a.file + ".bak-v3240-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.40.0 -> v3.41.0  ::  GUTLOG_V3410_KITCHENFIND -- finding recipes; the fuller food table.

WHY: the Family Kitchen now has fifty-odd cards from several people. Finding
one meant scrolling. Kitchen 1.2.0 answers one `browse` call with a person
view, a category view, one search box with Hindi / English aliases and
filters that combine; this page (GutLog's Family Kitchen page, for the owner
and the full members) gets the same screen as the kitchen-only book. And the
refreshed food table (24-Sep approval) carries fat, carbohydrate and calcium,
so the per-serving line reads a figure instead of "--".

WHAT
  * /api/kitchen/browse -- a proxy of the Kitchen's /api/browse with this
    copy's token (q, by, grp, meal, protein, quick, top, new, mine, pref, show).
  * The Kitchen page's Recipes tab: Person -> Category -> Recipe and Category
    -> Person -> Recipe (chips with counts), one search box, filter chips
    (Vegetarian / Eggetarian / No onion-garlic / Jain; Breakfast / Lunch /
    Dinner / Snack / Sweet; High-protein; Quick; Top-rated; New this week;
    Made it by me; Hidden for the owner), results grouped by category with
    "Recipe by <Name>", the last person / category remembered on this phone,
    an empty result that says what to loosen. The Inbox review form gains
    "Time to make (minutes)".
  * food_lookup() returns fat, carbs and calcium from the table's columns
    5-7 when present; ing_food() carries them for a table match and, for a
    food in this person's own list that came from the table, from that
    table row (source_ref "USDA SR Legacy #id"). recipe_nutrition() says
    which matched items had no value for a nutrient (partial), so a figure
    is never silently short.

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
MARKER = "GUTLOG_V3410_KITCHENFIND"
PREV = "GUTLOG_V3400_WEIGHT"
VERSION = "3.41.0"

BROWSE_JS = r"""/* GUTLOG_V3410_KITCHENFIND -- finding recipes: a person view and a category
   view, one search box (aliases on the Kitchen), filters that combine; the
   last person / category opened is remembered on this phone. */
let ST={tab:'browse',q:'',view:'person',by:'',grp:'',f:{pref:{},meal:'',protein:false,quick:false,top:false,new:false,mine:false,hidden:false},canHide:false};
const LSK='kitchen_last_gutlog';
function remember(){try{localStorage.setItem(LSK,JSON.stringify({view:ST.view,by:ST.by,grp:ST.grp}));}catch(e){}}
function recall(){try{const j=JSON.parse(localStorage.getItem(LSK)||'{}');if(j.view)ST.view=j.view;if(j.by)ST.by=j.by;if(j.grp)ST.grp=j.grp;}catch(e){}}
function browseParams(){const p=new URLSearchParams();p.set('q',ST.q);p.set('by',ST.by);p.set('grp',ST.grp);
  if(ST.f.meal)p.set('meal',ST.f.meal);['protein','quick','top','new','mine'].forEach(k=>{if(ST.f[k])p.set(k,'1');});
  p.set('pref',Object.keys(ST.f.pref).filter(k=>ST.f.pref[k]).join(','));if(ST.f.hidden)p.set('show','hidden');return p.toString();}
function chip(box,label,on,fn){const c=el('button','chip'+(on?' sel':''),label);c.onclick=fn;box.appendChild(c);return c;}
function tabs(){const b=$('#tabs');b.innerHTML='';TABS.forEach(([k,l])=>{const x=el('button','tab'+(ST.tab===k?' sel':''),l);x.onclick=()=>{ST.tab=k;show();};b.appendChild(x);});}
function show(){tabs();({browse,inbox,add,share})[ST.tab]();}
function listCard(r){const c=el('div','card');c.style.cursor='pointer';
  const h=el('div','row');h.appendChild(el('b','',r.name));
  h.appendChild(el('span','mut',r.rating.avg?('★ '+r.rating.avg+' ('+r.rating.n+')'):''));c.appendChild(h);
  c.appendChild(el('p','by','Recipe by '+(r.added_by||'someone')));
  c.appendChild(el('p','mut',r.grp+' · added '+(r.added_date||'')+(r.new?' · new':'')+
    (r.rating.made?(' · made '+r.rating.made+'×'):'')+(r.minutes?(' · '+r.minutes+' min'):'')));
  if(r.versions&&r.versions.length>1)c.appendChild(el('p','mut',r.versions.length+' versions — by '+r.versions.map(x=>x.by).join(', ')));
  if(r.status&&r.status!=='published')c.appendChild(el('p','flag',r.status==='hidden'?'Hidden from the Kitchen':'Unpublished'));
  c.onclick=()=>recipe(r.id);return c;}
let RENDER=0;   /* two taps in quick succession: only the latest answer is drawn */
async function browse(){
  const my=++RENDER;
  const v=$('#view');v.innerHTML='';
  const s=el('input');s.placeholder='Search a dish, an ingredient, a category or a name';s.value=ST.q;s.id='q';s.setAttribute('aria-label','Search');s.enterKeyHint='search';
  s.onchange=()=>{if(s.value!==ST.q){ST.q=s.value;browse();}};s.onkeydown=(e)=>{if(e.key==='Enter'){e.preventDefault();ST.q=s.value;browse();}};v.appendChild(s);
  let j;try{j=await jget('/api/kitchen/browse?'+browseParams());}catch(e){if(my===RENDER)v.appendChild(el('p','mut','Could not load.'));return;}
  if(my!==RENDER)return;
  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}
  if(!!j.can_hide!==ST.canHide){ST.canHide=!!j.can_hide;}
  const vt=el('div','chips');vt.style.marginTop='8px';vt.id='views';
  [['person','Person → Category'],['category','Category → Person']].forEach(([k,l])=>chip(vt,l,ST.view===k,()=>{ST.view=k;remember();browse();}));
  v.appendChild(vt);
  const ft=el('div','chips');ft.style.marginTop='6px';ft.id='filters';
  (j.prefs_all||[]).forEach(([k,l])=>chip(ft,l,!!ST.f.pref[k],()=>{ST.f.pref[k]=!ST.f.pref[k];browse();}));
  (j.meals||[]).forEach(m=>chip(ft,m.charAt(0).toUpperCase()+m.slice(1),ST.f.meal===m,()=>{ST.f.meal=ST.f.meal===m?'':m;browse();}));
  [['protein','High-protein'],['quick','Quick'],['top','Top-rated'],['new','New this week'],['mine','Made it by me']].forEach(([k,l])=>chip(ft,l,!!ST.f[k],()=>{ST.f[k]=!ST.f[k];browse();}));
  if(ST.canHide)chip(ft,'Hidden',ST.f.hidden,()=>{ST.f.hidden=!ST.f.hidden;browse();});
  v.appendChild(ft);
  const l1=el('div','chips');l1.style.marginTop='10px';l1.id='level1';const l2=el('div','chips');l2.style.marginTop='6px';l2.id='level2';
  const people=(box)=>{chip(box,'Everyone ('+(j.everyone||0)+')',!ST.by,()=>{ST.by='';remember();browse();});
    (j.people||[]).forEach(x=>chip(box,x.name+' ('+x.n+')',ST.by===x.slug,()=>{ST.by=ST.by===x.slug?'':x.slug;remember();browse();}));};
  const groups=(box)=>{const n=(j.groups||[]).reduce((a,g)=>a+g.n,0);chip(box,'Every category ('+n+')',!ST.grp,()=>{ST.grp='';remember();browse();});
    (j.groups||[]).forEach(x=>chip(box,x.grp+' ('+x.n+')',ST.grp===x.grp,()=>{ST.grp=ST.grp===x.grp?'':x.grp;remember();browse();}));};
  if(ST.view==='person'){people(l1);groups(l2);}else{groups(l1);people(l2);}
  v.appendChild(l1);v.appendChild(l2);
  if(!(j.recipes||[]).length){v.appendChild(el('p','mut',j.hint||'Nothing here yet.'));return;}
  const byg={};(j.recipes||[]).forEach(r=>{(byg[r.grp]=byg[r.grp]||[]).push(r);});
  (j.groups||[]).map(g=>g.grp).concat(Object.keys(byg)).filter((g,i,a)=>byg[g]&&a.indexOf(g)===i).forEach(g=>{
    v.appendChild(el('h3','',g+' ('+byg[g].length+')'));byg[g].forEach(r=>v.appendChild(listCard(r)));});
}
"""

OLD_BROWSE_JS = r"""let ST={tab:'browse',grp:'',sort:'az',q:'',by:'',canHide:false};
function tabs(){const b=$('#tabs');b.innerHTML='';TABS.forEach(([k,l])=>{const x=el('button','tab'+(ST.tab===k?' sel':''),l);x.onclick=()=>{ST.tab=k;show();};b.appendChild(x);});}
function show(){tabs();({browse,inbox,add,share})[ST.tab]();}
async function browse(){
  const v=$('#view');v.innerHTML='';
  const s=el('input');s.placeholder='Search a dish or an ingredient';s.value=ST.q;
  s.onchange=()=>{ST.q=s.value;browse();};v.appendChild(s);
  const so=el('div','chips');so.style.marginTop='8px';
  /* GUTLOG_V3390_KITCHENBY -- By person; Hidden (owner only) */
  [['az','All'],['top','Top rated'],['new','New this week'],['fav','Family favourites'],['by','By person']].concat(ST.canHide?[['hidden','Hidden']]:[]).forEach(([k,l])=>{
    const c=el('button','chip'+(ST.sort===k?' sel':''),l);c.onclick=()=>{ST.sort=k;if(k!=='by')ST.by='';browse();};so.appendChild(c);});
  v.appendChild(so);
  if(ST.sort==='by'){let p;try{p=await jget('/api/kitchen/people');}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
    const pc=el('div','chips');pc.style.marginTop='8px';
    (p.people||[]).forEach(x=>{const c=el('button','chip'+(ST.by===x.slug?' sel':''),x.name+' ('+x.n+')');c.onclick=()=>{ST.by=x.slug;browse();};pc.appendChild(c);});
    v.appendChild(pc);if(!ST.by){v.appendChild(el('p','mut','Tap a name to see their recipes.'));return;}}
  const sort=(ST.sort==='by')?'az':(ST.sort==='hidden'?'az':ST.sort),show=ST.sort==='hidden'?'hidden':'';
  let j;try{j=await jget('/api/kitchen/recipes?sort='+sort+'&q='+encodeURIComponent(ST.q)+'&grp='+encodeURIComponent(ST.grp)+'&by='+encodeURIComponent(ST.by)+'&show='+show);}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}
  if(!!j.can_hide!==ST.canHide){ST.canHide=!!j.can_hide;tabs();}
  const g=el('div','chips');g.style.marginTop='8px';
  ['',...(j.groups||[])].forEach(x=>{const c=el('button','chip'+(ST.grp===x?' sel':''),x||'Every group');c.onclick=()=>{ST.grp=x;browse();};g.appendChild(c);});
  v.appendChild(g);
  if(!(j.recipes||[]).length)v.appendChild(el('p','mut','Nothing here yet.'));
  (j.recipes||[]).forEach(r=>{
    const c=el('div','card');c.style.cursor='pointer';
    const h=el('div','row');h.appendChild(el('b','',r.name));
    h.appendChild(el('span','mut',r.rating.avg?('★ '+r.rating.avg+' ('+r.rating.n+')'):''));c.appendChild(h);
    c.appendChild(el('p','by','Recipe by '+(r.added_by||'someone')));
    c.appendChild(el('p','mut',r.grp+' · added '+(r.added_date||'')+(r.new?' · new':'')+
      (r.rating.made?(' · made '+r.rating.made+'×'):'')));
    if(r.versions&&r.versions.length>1)c.appendChild(el('p','mut',r.versions.length+' versions — by '+r.versions.map(x=>x.by).join(', ')));
    if(r.status&&r.status!=='published')c.appendChild(el('p','flag',r.status==='hidden'?'Hidden from the Kitchen':'Unpublished'));
    c.onclick=()=>recipe(r.id);v.appendChild(c);});
}
"""

E = []
E.append(("header",
          'GUTLOG_V3400_WEIGHT -- the weight profile: a WEEKLY medicine slot, meal windows, check-ins, '
          'a weight chart with milestones, a This-week card.\n',
          'GUTLOG_V3400_WEIGHT -- the weight profile: a WEEKLY medicine slot, meal windows, check-ins, '
          'a weight chart with milestones, a This-week card.\n'
          'GUTLOG_V3410_KITCHENFIND -- finding recipes (person / category views, one search box with aliases, '
          'filters); fat, carbs and calcium from the refreshed food table.\n'))
E.append(("version", 'APP_VERSION = "3.40.0"   # GUTLOG_V3400_WEIGHT ',
          'APP_VERSION = "3.41.0"   # GUTLOG_V3410_KITCHENFIND GUTLOG_V3400_WEIGHT '))
E.append(("food_lookup columns",
          '    return [{"fdc": r[0], "desc": r[1], "protein": r[2], "kcal": r[3], "fibre": r[4],\n'
          '             "full": full} for _, r, full in found[:limit]]\n',
          '    # GUTLOG_V3410_KITCHENFIND -- the refreshed table carries fat, carbs and calcium (columns 5-7).\n'
          '    return [{"fdc": r[0], "desc": r[1], "protein": r[2], "kcal": r[3], "fibre": r[4],\n'
          '             "fat": r[5] if len(r) > 5 else None, "carbs": r[6] if len(r) > 6 else None,\n'
          '             "calcium": r[7] if len(r) > 7 else None,\n'
          '             "full": full} for _, r, full in found[:limit]]\n'))
E.append(("ing_food columns",
          '    for r in db().execute("SELECT * FROM library WHERE lower(item)=lower(?)", (item.strip(),)).fetchall():\n'
          '        if r["b_kcal"] is not None or r["b_protein"] is not None:\n'
          '            bq = r["basis_qty"] or 100.0\n'
          '            f = 100.0 / bq\n'
          '            return ({"kcal": (r["b_kcal"] or 0) * f, "protein": (r["b_protein"] or 0) * f,\n'
          '                     "fibre": (r["b_fibre"] or 0) * f}, "your food list")\n'
          '    hits = [h for h in food_lookup(item, limit=3) if h.get("full")]\n'
          '    if hits:\n'
          '        h = hits[0]\n'
          '        return {"kcal": h["kcal"], "protein": h["protein"], "fibre": h["fibre"]}, "USDA: " + h["desc"]\n'
          '    return None, None\n',
          '    for r in db().execute("SELECT * FROM library WHERE lower(item)=lower(?)", (item.strip(),)).fetchall():\n'
          '        if r["b_kcal"] is not None or r["b_protein"] is not None:\n'
          '            bq = r["basis_qty"] or 100.0\n'
          '            f = 100.0 / bq\n'
          '            out = {"kcal": (r["b_kcal"] or 0) * f, "protein": (r["b_protein"] or 0) * f,\n'
          '                   "fibre": (r["b_fibre"] or 0) * f}\n'
          '            # GUTLOG_V3410_KITCHENFIND -- a food that came from the table keeps its row\'s\n'
          '            # fat, carbs and calcium (per 100 g, the table\'s own basis); a typed food has none.\n'
          '            m = re.search(r"USDA SR Legacy #(\\d+)", r["source_ref"] or "")\n'
          '            row = food_table()[1].get(int(m.group(1))) if m else None\n'
          '            if row and len(row) > 7:\n'
          '                out.update(fat=row[5], carbs=row[6], calcium=row[7])\n'
          '            return out, "your food list"\n'
          '    hits = [h for h in food_lookup(item, limit=3) if h.get("full")]\n'
          '    if hits:\n'
          '        h = hits[0]\n'
          '        return {"kcal": h["kcal"], "protein": h["protein"], "fibre": h["fibre"], "fat": h.get("fat"),\n'
          '                "carbs": h.get("carbs"), "calcium": h.get("calcium")}, "USDA: " + h["desc"]\n'
          '    return None, None\n'))
E.append(("recipe_nutrition partial",
          '    tot = dict((k, 0.0) for k in KITCHEN_NUTR)\n'
          '    have = dict((k, False) for k in KITCHEN_NUTR)\n'
          '    unmatched, matched = [], []\n'
          '    for i in ings:\n'
          '        g, how = ing_grams(i)\n'
          '        f, src = ing_food(i.get("item") or "") if g else (None, None)\n'
          '        if g is None or f is None:\n'
          '            unmatched.append({"item": i.get("item"), "why": how if g is None else "not in the food table"})\n'
          '            continue\n'
          '        for k in KITCHEN_NUTR:\n'
          '            if f.get(k) is not None:\n'
          '                tot[k] += g * float(f[k]) / 100.0\n'
          '                have[k] = True\n'
          '        matched.append({"item": i.get("item"), "grams": round(g, 1), "how": how, "from": src})\n',
          '    tot = dict((k, 0.0) for k in KITCHEN_NUTR)\n'
          '    have = dict((k, False) for k in KITCHEN_NUTR)\n'
          '    lack = dict((k, []) for k in KITCHEN_NUTR)   # GUTLOG_V3410_KITCHENFIND -- matched, but no value for k\n'
          '    unmatched, matched = [], []\n'
          '    for i in ings:\n'
          '        g, how = ing_grams(i)\n'
          '        f, src = ing_food(i.get("item") or "") if g else (None, None)\n'
          '        if g is None or f is None:\n'
          '            unmatched.append({"item": i.get("item"), "why": how if g is None else "not in the food table"})\n'
          '            continue\n'
          '        for k in KITCHEN_NUTR:\n'
          '            if f.get(k) is not None:\n'
          '                tot[k] += g * float(f[k]) / 100.0\n'
          '                have[k] = True\n'
          '            else:\n'
          '                lack[k].append(i.get("item"))\n'
          '        matched.append({"item": i.get("item"), "grams": round(g, 1), "how": how, "from": src})\n'))
E.append(("recipe_nutrition return",
          '    per = dict((k, (round(tot[k] / s * portion, 1) if have[k] else None)) for k in KITCHEN_NUTR)\n'
          '    return {"per_serving": per, "unmatched": unmatched, "matched": matched}\n',
          '    per = dict((k, (round(tot[k] / s * portion, 1) if have[k] else None)) for k in KITCHEN_NUTR)\n'
          '    partial = dict((k, v) for k, v in lack.items() if v and have[k])\n'
          '    return {"per_serving": per, "unmatched": unmatched, "matched": matched, "partial": partial}\n'))
E.append(("browse proxy",
          '@app.route("/api/kitchen/people")\n@login_required\ndef api_kitchen_people():\n',
          '@app.route("/api/kitchen/browse")\n@login_required\ndef api_kitchen_browse():\n'
          '    """GUTLOG_V3410_KITCHENFIND -- the Kitchen\'s one browse answer: person / category counts,\n'
          '    the search (aliases on the Kitchen side) and the filters, with this copy\'s token."""\n'
          '    from urllib.parse import urlencode\n'
          '    keys = ("q", "by", "grp", "meal", "protein", "quick", "top", "new", "mine", "pref", "show")\n'
          '    q = dict((k, request.args.get(k)) for k in keys if request.args.get(k))\n'
          '    st, j = kitchen_call("GET", "/api/browse" + ("?" + urlencode(q) if q else ""))\n'
          '    return jsonify(j), (st or 502)\n\n\n'
          '@app.route("/api/kitchen/people")\n@login_required\ndef api_kitchen_people():\n'))
E.append(("kitchen page js", OLD_BROWSE_JS, BROWSE_JS))
E.append(("kitchen page recall",
          "  v.appendChild(c);\n}\nshow();",
          "  v.appendChild(c);\n}\nrecall();\nshow();"))
E.append(("card minutes and partial",
          "  c.appendChild(el('p','mut','Added '+(r.added_date||'')+' · '+r.grp+' · serves '+r.servings+(r.serving_text?(' · '+r.serving_text):'')));\n",
          "  c.appendChild(el('p','mut','Added '+(r.added_date||'')+' · '+r.grp+' · serves '+r.servings+(r.serving_text?(' · '+r.serving_text):'')+(r.minutes?(' · about '+r.minutes+' min'):'')));\n"))
E.append(("nutrition partial line",
          "  if(j.nutrition.unmatched.length){nu.appendChild(el('p','flag','Not counted: '+j.nutrition.unmatched.map(x=>x.item+' ('+x.why+')').join('; ')));}\n"
          "  c.appendChild(nu);\n"
          "  const lg=el('button','btn','Log a serving as a meal');\n",
          "  if(j.nutrition.unmatched.length){nu.appendChild(el('p','flag','Not counted: '+j.nutrition.unmatched.map(x=>x.item+' ('+x.why+')').join('; ')));}\n"
          "  const pt=j.nutrition.partial||{};Object.keys(pt).forEach(k=>nu.appendChild(el('p','mut',k+' leaves out: '+pt[k].join(', '))));\n"
          "  c.appendChild(nu);\n"
          "  const lg=el('button','btn','Log a serving as a meal');\n"))
E.append(("review form minutes",
          "  const sv=el('input');sv.value=ex.servings||'';sv.inputMode='decimal';c.appendChild(el('p','','Serves'));c.appendChild(sv);\n"
          "  c.appendChild(el('p','','Ingredients'));const ib=el('div');c.appendChild(ib);\n",
          "  const sv=el('input');sv.value=ex.servings||'';sv.inputMode='decimal';c.appendChild(el('p','','Serves'));c.appendChild(sv);\n"
          "  const mn=el('input');mn.inputMode='numeric';mn.placeholder='optional';c.appendChild(el('p','','Time to make (minutes)'));c.appendChild(mn);\n"
          "  c.appendChild(el('p','','Ingredients'));const ib=el('div');c.appendChild(ib);\n"))
E.append(("review confirm minutes",
          "      const x=await post('/api/kitchen/drafts/'+id+'/confirm',{name:nm.value,grp:gp.value,servings:sv.value,force:!!force,\n",
          "      const x=await post('/api/kitchen/drafts/'+id+'/confirm',{name:nm.value,grp:gp.value,servings:sv.value,minutes:mn.value,force:!!force,\n"))
E.append(("confirm passes minutes",
          '    st, j = kitchen_call("POST", "/api/drafts/%d/%s" % (did, act), J() if act == "confirm" else {})\n',
          '    st, j = kitchen_call("POST", "/api/drafts/%d/%s" % (did, act), J() if act == "confirm" else {})   # minutes rides in J()\n'))

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
    print("GutLog: finding recipes; the fuller food table -> v" + VERSION)
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
    bak = a.file + ".bak-v3410-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 family/test_family_f.py gutlog/app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.28.0 -> v3.29.0  ::  GUTLOG_V3290_NUTRITION -- the history he could
not reach, and a Meals tab that answers to a date.

WHAT WAS ALREADY THERE, because the brief asked first and the answer matters:

  * The day totals card on the Meals tab read /api/meals/today/<day> and
    summed it IN JAVASCRIPT.
  * Beside it sat #ml_day, an input[type=date] -- which had NO change
    listener anywhere in the file. It was never a day chooser. It was only
    the date a NEW meal would be filed under, and changing it refreshed
    nothing at all.
  * The meal list with Edit / Again / Delete lives on the NOW tab and was
    hardcoded to today (`/api/mealcards?day='+todayISO`).
  * There was no history view of any kind.

So a past day could be COMPUTED but never SEEN, and the one control that
looked like it should show you a past day silently did nothing. That is what
"cannot navigate to the place where I can see this history" was.

WHAT THIS DOES

  * Totals move to the server -- nut_day() -- and the day card, the history
    page and the API all read that one function. They cannot disagree,
    because there is no second calculation left. The meals table already
    stores per-meal protein/kcal/fibre computed on the server at insert, so
    a day total is a plain SUM; nothing is worked out a second way.
  * Meals tab gets a day stepper: previous / next either side of the date,
    next disabled on today, and the date itself opens three lists
    (day / month / year), not a native picker -- the Fold cover screen hides
    that dialog's own button. #ml_day stays in the DOM, hidden, as the value
    the logging form reads, exactly as v3.27.0 does with time boxes.
  * The totals card and a per-day meal list follow the chosen day, and every
    meal keeps Edit / Again / Delete on any day.
  * /nutrition -- a history page, server-rendered, newest first, 14 days with
    a link for 30. Per row: the date, kcal, protein against the plan's
    target, fibre against 30 g, and the number of meals. A day with nothing
    logged says "not logged" and NEVER 0 kcal; a thin day is labelled
    "partial", so a low total is not read as a low-intake day.
  * Tapping a row opens that day in the stepper (/?open=meals&day=...).

Values stay marked estimated, as they already were. Nothing here scores a
day or compares him to anyone: the targets shown are his own plan's.

Anchor-verified, idempotent, compile-checked, .bak before write,
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
MARKER = "GUTLOG_V3290_NUTRITION"
PREV = "GUTLOG_V3280_PLANS"
VERSION = "3.29.0"

# ---------------------------------------------------------------- 1. header
HEAD_OLD = 'GUTLOG_V3280_PLANS -- /plans holds and shares a dated plan document.\n'
HEAD_NEW = ('GUTLOG_V3280_PLANS -- /plans holds and shares a dated plan document.\n'
            'GUTLOG_V3290_NUTRITION -- /nutrition, and a Meals tab with a day stepper.\n')

# --------------------------------------------------------------- 2. version
VER_OLD = ('APP_VERSION = "3.28.0"   # GUTLOG_V3280_PLANS GUTLOG_V3272_HEALTHZ '
           'GUTLOG_V3271_TARGETJS GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS\n')
VER_NEW = ('APP_VERSION = "3.29.0"   # GUTLOG_V3290_NUTRITION GUTLOG_V3280_PLANS '
           'GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS GUTLOG_V3270_TIMEPICK '
           'GUTLOG_V3260_TRIALS\n')

# --------------------------------------------------- 3. server side + page
NUT_CODE = r'''# -------------------------------------------------------------- nutrition
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


'''

NUT_PAGE = r'''NUTRITION_PAGE = """<!doctype html>
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


'''

CODE_OLD = '# ------------------------------------------------------------------ plans\n'
CODE_NEW = NUT_PAGE + NUT_CODE + CODE_OLD

# ------------------------------------------------------- 4. the Meals tab
TAB_OLD = ('''  <div class="sub sel" id="meals-meal">
    <div class="card" style="padding:11px 14px">
      <div class="tot" id="dayTotals"></div>
''')
TAB_NEW = ('''  <div class="sub sel" id="meals-meal">
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
''')

# ------------------------------------------------ 5. the day's meals list
LIST_OLD = ('''      <p class="q" style="margin-top:10px">Slot</p>
      <div class="chips" data-f="slot" data-sec="meal" data-v="Breakfast|Lunch|Dinner|Snack"></div></div>
''')
LIST_NEW = ('''      <p class="q" style="margin-top:10px">Slot</p>
      <div class="chips" data-f="slot" data-sec="meal" data-v="Breakfast|Lunch|Dinner|Snack"></div></div>
    <div class="card" id="mlDayCard"><p class="q" id="mlDayHead">Meals</p>
      <div id="mlDayMeals"></div></div>
''')

# ------------------------------------------------- 6. the hidden date box
# The stepper above states the day, so the form's own Date cell is gone.
# #ml_day stays in the DOM, hidden, because it is still the value saveMeal()
# reads -- the same trick v3.27.0 uses for time boxes.
#
# The .row2 grid is the reason the cell goes rather than just being relabelled.
# Its columns are 1fr 1fr and grid items will not shrink below their content,
# so a bold "22-Sep-2026" in the first cell pushed the Time cell's two
# 72px-minimum selects off the folded screen: 316px against 300. Measured,
# after the first attempt put a text span there and the v3.27.0 folded-width
# assertion caught it.
DATE_OLD = ('''    <div class="card"><div class="row2">
      <div><p class="lbl">Date</p><input type="date" id="ml_day"></div>
      <div><p class="lbl">Time</p><input type="time" id="ml_time"></div></div>
''')
DATE_NEW = ('''    <div class="card">
      <input type="date" id="ml_day" style="display:none">
      <div><p class="lbl">Time</p><input type="time" id="ml_time"></div>
''')

# ------------------------------------------------------------ 7. the CSS
CSS_OLD = '.tot{font-size:13px;color:var(--muted)}\n'
CSS_NEW = ('.tot{font-size:13px;color:var(--muted)}\n'
           '/* GUTLOG_V3290_NUTRITION -- the day stepper. Sized to sit inside 300px. */\n'
           '.mlstep{display:flex;align-items:center;gap:6px;margin:0 0 8px;flex-wrap:wrap}\n'
           '.mlstep button{border:1px solid var(--line);background:var(--card);color:var(--ink);\n'
           '  border-radius:9px;padding:6px 10px;font:inherit;font-weight:700;cursor:pointer}\n'
           '.mlstep button[disabled]{opacity:.38;cursor:default}\n'
           '.mlstep .mldate{flex:1 1 auto;min-width:0;font-size:13.5px;text-align:center}\n'
           '.mlstep .mlhist{margin-left:auto;font-size:12.5px;font-weight:700;padding:6px 10px;\n'
           '  border:1px solid var(--teal);border-radius:9px;color:var(--teal);text-decoration:none}\n'
           '.mldmy{display:flex;gap:5px;margin:0 0 8px}\n'
           '.mldmy select{flex:1 1 auto;min-width:0;font:inherit;padding:6px;border-radius:8px;\n'
           '  border:1px solid var(--line);background:var(--card);color:var(--ink)}\n'
           '.mldmy button{border:1px solid var(--teal);background:var(--teal);color:#fff;\n'
           '  border-radius:8px;padding:6px 11px;font:inherit;font-weight:700;cursor:pointer}\n'
           '.mlmeal{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;\n'
           '  padding:9px 0;border-top:1px solid var(--line)}\n'
           '.mlmeal:first-child{border-top:0}\n'
           '.mlmeal b{font-size:13.5px}\n'
           '.mlmeal small{color:var(--muted);flex:1 1 100%}\n'
           '.mlmeal .macts{display:flex;gap:5px;margin-left:auto}\n')

# -------------------------------------------------------- 8. the tab hook
HOOK_OLD = "  if(t==='meals'){loadMealTotals();loadRegistry();renderTestFoods();}\n"
HOOK_NEW = "  if(t==='meals'){mlRenderStep();loadMealTotals();loadRegistry();renderTestFoods();}\n"

# --------------------------------------------------- 9. totals + the list
TOT_OLD = '''async function loadMealTotals(){
  const rows=await jget('/api/meals/today/'+ (($('#ml_day').value)||todayISO));
  let p=0,k=0,f=0,fs=0,nq=0;rows.forEach(m=>{p+=m.protein;k+=m.kcal;f+=m.fibre;fs+=m.fscore;
    m.items.forEach(it=>nq+=it.q);});
  $('#dayTotals').innerHTML=`Today: <b>${p.toFixed(1)} g protein</b> &middot; ${Math.round(k)} kcal &middot; ${f.toFixed(1)} g fibre &middot; ${rows.length} meal(s)`;
  $('#dayPbar').style.width=Math.min(100,p/PROT_TGT*100)+'%';
  const avg=nq?fs/nq:0;const lab=avg<0.4?'low':avg<0.9?'low-moderate':avg<1.3?'moderate':'high';
  $('#dayFmap').innerHTML=rows.length?`FODMAP load today: <b>${lab}</b>`:'';
}
'''
TOT_NEW = r'''/* GUTLOG_V3290_NUTRITION. The numbers come from the server now -- the same
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
    line.appendChild(el('b','',m.mtime+' · '+m.slot));
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
    line.appendChild(el('small','',(m.items||[]).map(i=>i.n+(i.q!==1?' ×'+i.q:'')).join(', ')+
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
  mcEdit={id:m.id,slot:m.slot,mtime:m.mtime};mcPick=false;
  const ci=(MC.cards||[]).findIndex(c=>c.name===m.card);
  if(m.card&&ci>=0){mcCur=ci;mcSel=mcFresh(ci,m);}
  else{mcCur=-1;mcSel={choices:{},onion:false,slot:m.slot,
        extra:(m.items||[]).map(i=>({n:i.n,q:i.q}))};
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
'''

# ------------------------------------------------- 10. the card-day global
MCDAY_OLD = "async function loadMeals(){\n  try{MC=await jget('/api/mealcards?day='+todayISO);}catch(e){return;}\n"
MCDAY_NEW = ("async function loadMeals(){\n"
             "  try{MC=await jget('/api/mealcards?day='+(mcDay||todayISO));}catch(e){return;}\n")

# ------------------------------------------------------- 11. the nav hook
NAV_OLD = "$$('#nav button').forEach(b=>b.onclick=()=>switchTab(b.dataset.t));\n"
NAV_NEW = ("/* GUTLOG_V3290_NUTRITION -- tapping Now always means today, whatever day\n"
           "   the Meals tab or a past-meal edit last looked at. */\n"
           "let mcDay=null, mlDel=null;\n"
           "$$('#nav button').forEach(b=>b.onclick=()=>{\n"
           "  if(b.dataset.t==='now')mcDay=todayISO;\n"
           "  switchTab(b.dataset.t);});\n")

# ------------------------------------------------------- 12. the deep link
DEEP_OLD = ('''function nowDeepLink(){
  const p=new URLSearchParams(location.search).get('open');
  if(!p)return;
  if(p==='records'){switchTab('files');setSeg('files','reports');return;}
''')
DEEP_NEW = ('''function nowDeepLink(){
  const q=new URLSearchParams(location.search);
  const p=q.get('open');
  if(!p)return;
  /* GUTLOG_V3290_NUTRITION -- a row on /nutrition opens that day here. */
  if(p==='meals'){
    switchTab('meals');setSeg('meals','meal');
    const d=q.get('day');
    mlFillDmy();
    if(d&&/^\\d{4}-\\d\\d-\\d\\d$/.test(d)&&d<=todayISO)mlSetDay(d);else mlRenderStep();
    return;
  }
  if(p==='records'){switchTab('files');setSeg('files','reports');return;}
''')

EDITS = [
    ("header", HEAD_OLD, HEAD_NEW),
    ("version", VER_OLD, VER_NEW),
    ("nutrition page and code", CODE_OLD, CODE_NEW),
    ("meals tab stepper", TAB_OLD, TAB_NEW),
    ("day meal list", LIST_OLD, LIST_NEW),
    ("hidden date box", DATE_OLD, DATE_NEW),
    ("stepper css", CSS_OLD, CSS_NEW),
    ("tab hook", HOOK_OLD, HOOK_NEW),
    ("totals from the server", TOT_OLD, TOT_NEW),
    ("card day", MCDAY_OLD, MCDAY_NEW),
    ("nav hook", NAV_OLD, NAV_NEW),
    ("deep link", DEEP_OLD, DEEP_NEW),
]

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
    print("GutLog nutrition history and a Meals tab with a date -> v" + VERSION)
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

    bad = [(l, src.count(o)) for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        for l, c in bad:
            print("  %s: found %d times, need 1" % (l, c))
        print("Refusing to patch. Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0

    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)

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

    bak = a.file + ".bak-v3290-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3290_nutrition.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

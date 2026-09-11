#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.6.0 -> v3.7.0  ::  salts & strengths, medicine status, Activity card

 1. SALTS (Meds -> Salts). Every medicine gets its salt(s) and strength --
    what RxGuard needs to check it. Spelling suggestions come from the US
    National Library of Medicine (RxNorm) as you type. "Not a single drug"
    marks mixtures and supplements so they stop asking. Adding a medicine
    from the Now tab now lands on its salt row.
 2. MEDICINE STATUS BANNER (Now tab): "N need a salt", "N waiting for your
    review in RxGuard", "RxGuard shows N RED" -- each line opens the place
    to act. RxGuard counts come from its token-gated status feed.
 3. ACTIVITY CARD (Now tab): Walk, Treadmill, Cycling (road), Cycling
    (static), Meditation. Tap a tile, pick minutes (and talk-test
    intensity), Save. Several a day, each with Undo, retimeable in Day by
    day. Watch data from FitLog (steps, workouts, mindful minutes) joins the
    card by itself; a watch workout matching a tapped entry shows once, as
    watch-confirmed, with the watch's minutes.
 4. FEED: the stack feed now carries strength; /api/feed/activities gives
    FitLog the tapped activities.

Outward calls (RxGuard, FitLog, NLM) are made only from the live database --
a scratch database, i.e. every test suite, never reaches out
(GUTLOG_LINKS=1/0 overrides). Only molecule names ever go to NLM.

Requires v3.6.0. Anchor-verified, idempotent, compile-checked, Jinja-safe,
.bak before write, self-restoring. Python 3.9.
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
MARKER = "GUTLOG_V370_SALTS_ACTIVITY"
PREV = "GUTLOG_V360_PHASE_C"

SCHEMA_ADD = '''CREATE TABLE IF NOT EXISTS med_salts (
  med_id INTEGER PRIMARY KEY, strength TEXT DEFAULT '', no_salt INTEGER DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, atime TEXT, kind TEXT NOT NULL,
  minutes REAL, intensity TEXT DEFAULT '', notes TEXT DEFAULT '', created TEXT);
'''

PY = r'''# ------------------------------------------------------------------ salts, status, activity
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
             "cycle_static": "Cycling (static)", "meditation": "Meditation"}


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
                   summary={"minutes": sum(i["minutes"] for i in items), "steps": steps})


@app.route("/api/feed/activities")
@feed_required
def api_feed_activities():
    since = _feed_since(14)
    rows = [dict(r) for r in db().execute(
        "SELECT day, atime, kind, minutes, intensity FROM activities WHERE day>=? "
        "ORDER BY day, atime", (since,)).fetchall()]
    return jsonify(ok=True, app="gutlog", since=since, activities=rows)


'''

HTML_ACT = r'''  <div class="card fold" id="nowAct">
    <button type="button" class="fold-h">
      <span class="ft">Activity</span><span class="fs" id="actSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div id="actTiles"></div>
      <div id="actList"></div>
      <p class="hint" id="actWatch" style="margin:8px 2px 0"></p>
    </div>
  </div>

'''

HTML_SALTS = r'''  <div class="sub" id="meds-salts">
    <p class="hint">The salt and strength of each medicine let RxGuard check it. Suggestions come from the US
      National Library of Medicine. Mark mixtures and supplements as "Not a single drug".</p>
    <div id="saltList"></div>
    <datalist id="saltSug"></datalist>
  </div>

'''

JS = r'''/* ---------- SALTS, MEDICINE STATUS, ACTIVITY (GUTLOG_V370) ---------- */
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
    d.className='stockalert medstat '+(j.rx&&j.rx.red?'red':'amber');
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
           ['cycle_static','Cycling (static)'],['meditation','Meditation']];
function buildActTiles(){
  const box=$('#actTiles');if(!box||box.dataset.built)return;box.dataset.built='1';
  ACT.forEach(a=>{
    const k=a[0],label=a[1];
    const w=document.createElement('div');w.className='ptile act';w.dataset.k=k;
    w.innerHTML='<button type="button" class="ph"><span class="pn"></span><span class="pv"></span></button>'+
      '<div class="pscore"><div class="chips am"></div><div class="chips ai"></div>'+
      '<button type="button" class="btn primary as">Save</button></div>';
    w.querySelector('.pn').textContent=label;
    const st={min:null,int:''};
    const am=w.querySelector('.am'),ai=w.querySelector('.ai');
    [10,15,20,30,45,60].forEach(v=>{
      const b=document.createElement('div');b.className='chip num';b.textContent=v;
      b.onclick=()=>{st.min=v;[...am.children].forEach(c=>c.classList.toggle('sel',c===b));
        w.querySelector('.pv').textContent=v+' min';};
      am.appendChild(b);
    });
    if(k!=='meditation'){
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
        toast('Logged '+label.toLowerCase()+', '+st.min+' min');
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
  $('#actSum').textContent=(s.minutes?(s.minutes+' min'):'none yet')+
    (s.steps?(' · '+Number(s.steps).toLocaleString('en-IN')+' steps'):'');
  el.innerHTML='';
  (j.items||[]).forEach(i=>{
    const row=document.createElement('div');row.className='exrow';
    row.innerHTML='<span class="t"></span><span class="m"></span>';
    row.querySelector('.t').textContent=i.time||'';
    const bits=[(i.source==='watch'?'⌚ ':'')+i.label+' '+i.minutes+' min'];
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

'''

CSS = r'''/* GUTLOG_V370_SALTS_ACTIVITY */
#actTiles .ptile.open .pscore{display:block}
#actTiles .pscore .chips{margin-bottom:8px}
#actTiles .pscore .btn.primary{margin-top:4px}
.stockalert.medstat{flex-wrap:wrap}
.stockalert .ml{display:flex;flex-wrap:wrap;gap:6px 12px}
.stockalert .mlink{background:none;border:0;padding:0;font:inherit;color:inherit;text-decoration:underline;cursor:pointer;text-align:left}
#saltList .vtm .st{flex:0 0 34% }
.tag.k-act{background:#E6F4EA;color:#2E7D32}
'''


def build_edits():
    E = []
    a = "GUTLOG_V350_PHASE_B " + PREV
    E.append(("version", a, a + " " + MARKER))
    a = "CREATE TABLE IF NOT EXISTS stock_meds (\n  med_id INTEGER PRIMARY KEY, mode TEXT DEFAULT '');\n"
    E.append(("schema", a, a + SCHEMA_ADD))
    a = "# ------------------------------------------------------------------ review\n"
    E.append(("python", a, PY + a))
    a = '<section class="tab sel" id="tab-now">\n  <div id="nowStock"></div>\n'
    E.append(("status banner", a, a + '  <div id="nowMedStatus"></div>\n'))
    a = '  <div class="card fold" id="nowSym">\n'
    E.append(("activity card", a, HTML_ACT + a))
    a = '<button data-s="sched">Schedule</button><button data-s="stock">Stock</button>'
    E.append(("salts seg button", a, a + '<button data-s="salts">Salts</button>'))
    a = '  <div class="sub" id="meds-stock">\n'
    E.append(("salts seg body", a, HTML_SALTS + a))
    a = "/* ---------- STOCK (GUTLOG_V360_PHASE_C) ---------- */"
    E.append(("js", a, JS + a))
    a = "async function loadNow(){\n  loadStockAlerts();\n"
    E.append(("now hooks", a, a + "  loadMedStatus();\n  loadActivity();\n"))
    a = "  if(section==='meds'&&s==='stock')loadStock();\n"
    E.append(("setSeg salts", a, a + "  if(section==='meds'&&s==='salts')loadSalts();\n"))
    a = "  if(t==='meds'&&seg.meds==='stock')loadStock();\n"
    E.append(("switchTab salts", a, a + "  if(t==='meds'&&seg.meds==='salts')loadSalts();\n"))
    a = "(tab==='meds'&&seg.meds==='stock');"
    E.append(("save hidden", a, "(tab==='meds'&&(seg.meds==='stock'||seg.meds==='salts'));"))
    a = "    try{await post('/api/prnmeds',{name:n});toast('Added');loadNow();loadSchedMeds();}"
    n = ("    try{await post('/api/prnmeds',{name:n});loadNow();loadSchedMeds();\n"
         "      switchTab('meds');setSeg('meds','salts');toast('Added. Now give its salt and strength.');}")
    E.append(("add med -> salts", a, n))
    a = "  const map={bp:'#nowBP',sym:'#nowSym',meds:'#nowSched'};"
    E.append(("deep link", a, "  const map={bp:'#nowBP',sym:'#nowSym',meds:'#nowSched',act:'#nowAct'};"))
    a = '_TIME_COL = {"doses": "dtime", "episodes": "etime", "vitals": "vtime", "meals": "mtime"}'
    E.append(("retime activities", a, a[:-1] + ', "activities": "atime"}'))
    a = '    out.sort(key=lambda e: (e["time"] == "", e["time"], e["tbl"], e["id"]))\n'
    n = ('    for r in db().execute("SELECT id, atime, kind, minutes, intensity FROM activities WHERE day=?",\n'
         '                          (day,)).fetchall():\n'
         '        add("activities", r["id"], r["atime"], "Activity",\n'
         '            ACT_KINDS.get(r["kind"], r["kind"]) + " " + str(int(round(r["minutes"] or 0))) + " min",\n'
         '            (r["intensity"] or "").lower())\n' + a)
    E.append(("day view activities", a, n))
    a = "const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',BP:'bp',Vitals:'bp',Meal:'meal'};"
    E.append(("day view tag", a, a[:-2] + ",Activity:'act'};"))
    a = '    if table not in ("doses","foodtests","vitals","episodes","meals","consults",\n                     "labs","courses","files","patches","library"):'
    n = '    if table not in ("doses","foodtests","vitals","episodes","meals","consults",\n                     "labs","courses","files","patches","library","activities"):'
    E.append(("delete activities", a, n))
    a = '"SELECT p.id AS med_id, p.name, p.molecule, s.slot, s.dose_text, s.variants "'
    E.append(("feed strength sel", a,
              '"SELECT p.id AS med_id, p.name, p.molecule, COALESCE(ms.strength,\'\') AS strength, "\n'
              '        "s.slot, s.dose_text, s.variants "'))
    a = '"FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id WHERE s.valid_from<=? "'
    E.append(("feed strength join", a,
              '"FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "\n'
              '        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE s.valid_from<=? "'))
    a = '"molecule": e["molecule"], "doses": 0, "days": set(),'
    E.append(("feed taken strength", a, '"molecule": e["molecule"], "strength": _salt_strength(e["med_id"]),\n'
                                        '                                 "doses": 0, "days": set(),'))
    a = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n</style></head><body>"
    E.append(("css", a, CSS + a))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow", "buildNowStatics",
               "bindFolds", "loadDayView", "dvEdit", "dvMissRow", "loadReview", "loadStock",
               "loadStockAlerts", "stockRow", "renderVitals", "vitalsChart", "loadMedStatus",
               "loadSalts", "saltGuess", "buildActTiles", "loadActivity"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("GutLog salts + activity -> v3.7.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.6.0. Apply that first.")
        return 1
    if "def api_salts" in src or "CREATE TABLE IF NOT EXISTS activities" in src:
        print("FATAL: v3.7.0 pieces already present -- unexpected state. Nothing written.")
        return 1
    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2
    if args.check:
        print("All anchors OK.")
        return 0
    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    missing = [f for f in MUST_DEFINE if not re.search(r"function\s+" + f + r"\s*\(", out)]
    if missing:
        print("DEFINITION CHECK FAILED, nothing written: " + ", ".join(missing))
        return 2
    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    with open(tmpf, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v370-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("-" * 60)
    print("Next:  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

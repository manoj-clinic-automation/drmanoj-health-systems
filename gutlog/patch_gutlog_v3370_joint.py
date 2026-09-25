#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.36.0 -> v3.37.0  ::  GUTLOG_V3370_JOINT -- the joint focus.

WHY: the Family Edition's `joint` profile -- knee and ankle arthritis, pain
medicines, a lipid-lowering medicine, a night tablet -- needs its own cards.
They are built into the shared code, so any copy can use them, and shown only
when settings.now_profile is 'joint'. The owner has no profile set, so his Now
page is exactly as it was.

WHAT (all generic -- no medicine or person is named in this file)
  * Joint pain log: joint (knee / ankle L-R first, hip, back), score 0-10, start
    time, trigger chips (walking, stairs, standing long, getting up, night,
    weather), morning stiffness minutes, walking tolerance ("could walk N
    minutes before pain"). Table joint_log.
  * Knee and ankle tiles lead the musculoskeletal pain sites under `joint`.
  * Pain medicines today: the member's own RxGuard (/api/feed/dose) -- total
    per ingredient against its ceiling, combinations split into their
    ingredients, a gel kept apart from a tablet, stomach- and kidney-risk loads.
  * Steps and joint pain: FitLog's steps and walking minutes per day beside
    that day's worst joint score, and one stated comparison -- next-day pain
    after the top third of days by steps, against after the other days.
    Counts and averages, no verdict. Weight trend on the same card.
  * Lipid-therapy checks: LDL, total and HDL cholesterol, triglycerides, ALT,
    AST and CK from rec_labs and labs, each with its last value and the date
    it is next due by a stated rule; a weekly muscle-ache question when the
    member's RxGuard lists a medicine of the statin class. Table muscle_check.

Schema 3.3.7 -> 3.3.8 (two tables in SCHEMA). Anchor-verified, idempotent,
compile-checked, .bak, self-restoring, --reverse, refuses Jinja tokens.
Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3370_JOINT"
PREV = "GUTLOG_V3360_FAMILY"
VERSION = "3.37.0"

E = []
E.append(("header",
          'GUTLOG_V3360_FAMILY -- the Family page: members at a glance, open as caretaker.\n',
          'GUTLOG_V3360_FAMILY -- the Family page: members at a glance, open as caretaker.\n'
          'GUTLOG_V3370_JOINT -- joint pain, pain-medicine totals, steps against pain, lipid checks.\n'))
E.append(("version", 'APP_VERSION = "3.36.0"   # GUTLOG_V3360_FAMILY ',
          'APP_VERSION = "3.37.0"   # GUTLOG_V3370_JOINT GUTLOG_V3360_FAMILY '))
E.append(("schema version", 'SCHEMA_VERSION = "3.3.7"   # GUTLOG_V3350_SNACKS ',
          'SCHEMA_VERSION = "3.3.8"   # GUTLOG_V3370_JOINT GUTLOG_V3350_SNACKS '))
E.append(("schema tables",
          "CREATE TABLE IF NOT EXISTS ft_outcome (\n"
          "  slug TEXT PRIMARY KEY, outcome TEXT NOT NULL, limit_g REAL, set_at TEXT);\n",
          "CREATE TABLE IF NOT EXISTS ft_outcome (\n"
          "  slug TEXT PRIMARY KEY, outcome TEXT NOT NULL, limit_g REAL, set_at TEXT);\n"
          "-- GUTLOG_V3370_JOINT\n"
          "CREATE TABLE IF NOT EXISTS joint_log (\n"
          "  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, jtime TEXT DEFAULT '',\n"
          "  site TEXT NOT NULL, score INTEGER, triggers TEXT DEFAULT '[]', stiff_min INTEGER,\n"
          "  walk_min INTEGER, notes TEXT DEFAULT '', created TEXT);\n"
          "CREATE INDEX IF NOT EXISTS ix_joint_log_day ON joint_log(day);\n"
          "CREATE TABLE IF NOT EXISTS muscle_check (\n"
          "  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, answer TEXT NOT NULL,\n"
          "  created TEXT);\n"))
E.append(("analytes",
          '    "LDL": ("mg/dL", 12), "Triglycerides": ("mg/dL", 12), "CRP": ("mg/L", None),\n}\n',
          '    "LDL": ("mg/dL", 12), "Triglycerides": ("mg/dL", 12), "CRP": ("mg/L", None),\n'
          '    # GUTLOG_V3370_JOINT -- the rest of a lipid-therapy check.\n'
          '    "Total cholesterol": ("mg/dL", 12), "HDL": ("mg/dL", 12),\n'
          '    "ALT (SGPT)": ("U/L", 12), "AST (SGOT)": ("U/L", 12), "CK": ("U/L", None),\n}\n'))
E.append(("joint pain sites",
          "PAIN_SITE_MAP = dict((s[0], s) for s in PAIN_SITES_MSK)\n",
          "# GUTLOG_V3370_JOINT -- knees and ankles, first under the joint profile only.\n"
          "PAIN_SITES_JOINT = [\n"
          '    ("knee_l", "Knee - L", "L", 0),\n'
          '    ("knee_r", "Knee - R", "R", 0),\n'
          '    ("ankle_l", "Ankle - L", "L", 0),\n'
          '    ("ankle_r", "Ankle - R", "R", 0),\n'
          "]\n"
          "PAIN_SITE_MAP = dict((s[0], s) for s in PAIN_SITES_MSK + PAIN_SITES_JOINT)\n"))
E.append(("pain sites by profile",
          '            for s in PAIN_SITES_MSK], treatments=PAIN_TREATMENTS)\n',
          '            for s in msk_sites()], treatments=PAIN_TREATMENTS)   # GUTLOG_V3370_JOINT\n'))

JOINT_PY = r'''
# ------------------------------------------------------------------ joints
# GUTLOG_V3370_JOINT. For the Family Edition's `joint` profile, shown only
# when settings.now_profile says so -- the owner's Now page is unchanged.
# Generic by construction: which medicine CLASS a person takes is asked of
# their own RxGuard (/api/feed/dose); no medicine is named in this file.
JOINT_SITES = ["Knee - L", "Knee - R", "Ankle - L", "Ankle - R", "Hip - L", "Hip - R", "Back"]
JOINT_TRIGGERS = ["walking", "stairs", "standing long", "getting up", "night", "weather"]
JOINT_EP_SITES = ("knee_l", "knee_r", "ankle_l", "ankle_r")
JOINT_WINDOW_DAYS = 28
JOINT_HIGH_SHARE = 1.0 / 3          # "high-step days": the top third by steps
MUSCLE_ASK_DAYS = 7
LIPID_GROUPS = [   # (label, test-name pattern, repeat after N months or None)
    ("LDL cholesterol", r"\bldl\b", 6),
    ("Total cholesterol", r"total\s*cholesterol|^cholesterol\b", 6),
    ("HDL cholesterol", r"\bhdl\b", 6),
    ("Triglycerides", r"triglycer", 6),
    ("ALT (SGPT)", r"\balt\b|sgpt", 12),
    ("AST (SGOT)", r"\bast\b|sgot", 12),
    ("CK", r"\bck\b|\bcpk\b|creatine\s*(phospho)?kinase", None),
]


def now_profile():
    v = setting("now_profile") or "gut"
    return v if v in ("gut", "joint", "general") else "gut"


def msk_sites():
    return (PAIN_SITES_JOINT + PAIN_SITES_MSK) if now_profile() == "joint" else PAIN_SITES_MSK


def _int_in(v, lo, hi):
    try:
        i = int(float(v))
    except (TypeError, ValueError):
        return None
    return i if lo <= i <= hi else None


@app.route("/api/joint/cfg")
@login_required
def api_joint_cfg():
    return jsonify(profile=now_profile(), show=now_profile() == "joint",
                   sites=JOINT_SITES, triggers=JOINT_TRIGGERS)


@app.route("/api/joint", methods=["GET", "POST"])
@login_required
def api_joint():
    if request.method == "POST":
        d = J()
        site = d.get("site")
        if site not in JOINT_SITES:
            return jsonify(ok=False, err="Pick the joint."), 400
        score = _int_in(d.get("score"), 0, 10)
        if score is None:
            return jsonify(ok=False, err="Pick a score from 0 to 10."), 400
        day = _valid_day(d.get("day") or today())
        start = _valid_hm(d.get("start")) if d.get("start") else now_hm()
        if not day or not start:
            return jsonify(ok=False, err="Pick a real date and time."), 400
        if day == today() and start > now_hm():
            return jsonify(ok=False, err="That time has not come yet today."), 400
        trig = [t for t in (d.get("triggers") or []) if t in JOINT_TRIGGERS]
        stiff = _int_in(d.get("stiff_min"), 0, 600) if d.get("stiff_min") not in (None, "") else None
        walk = _int_in(d.get("walk_min"), 0, 600) if d.get("walk_min") not in (None, "") else None
        db().execute("INSERT INTO joint_log(day, jtime, site, score, triggers, stiff_min, walk_min, "
                     "notes, created) VALUES(?,?,?,?,?,?,?,?,?)",
                     (day, start, site, score, json.dumps(trig), stiff, walk,
                      (d.get("notes") or "")[:300], now_s()))
        db().commit()
        return jsonify(ok=True, id=db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"])
    try:
        days = max(1, min(90, int(request.args.get("days") or 14)))
    except ValueError:
        days = 14
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = []
    for r in db().execute("SELECT id, day, jtime, site, score, triggers, stiff_min, walk_min, notes "
                          "FROM joint_log WHERE day>=? ORDER BY day DESC, jtime DESC, id DESC",
                          (since,)).fetchall():
        x = dict(r)
        try:
            x["triggers"] = json.loads(x["triggers"] or "[]")
        except ValueError:
            x["triggers"] = []
        rows.append(x)
    return jsonify(rows=rows)


@app.route("/api/joint/<int:jid>/delete", methods=["POST"])
@login_required
def api_joint_delete(jid):
    cur = db().execute("DELETE FROM joint_log WHERE id=?", (jid,))
    db().commit()
    return jsonify(ok=bool(cur.rowcount))


def joint_pain_by_day(since):
    """{day: worst joint score}, from the joint log and the knee / ankle pain tiles."""
    out = {}
    for r in db().execute("SELECT day, MAX(score) AS s FROM joint_log WHERE day>=? GROUP BY day",
                          (since,)).fetchall():
        if r["s"] is not None:
            out[r["day"]] = int(r["s"])
    q = ("SELECT day, MAX(severity) AS s FROM episodes WHERE category='pain' AND etype IN (%s) "
         "AND day>=? GROUP BY day" % ",".join("?" * len(JOINT_EP_SITES)))
    for r in db().execute(q, JOINT_EP_SITES + (since,)).fetchall():
        if r["s"] is not None:
            out[r["day"]] = max(out.get(r["day"], 0), int(r["s"]))
    return out


def step_pain_compare(steps, pain):
    """The stated comparison. High-step days are the top third of the days that
    have steps; for each group, the worst joint score on the NEXT day, where one
    was logged. Counts and averages only -- never a verdict."""
    days = sorted(d for d, n in steps.items() if n)
    if len(days) < 3:
        return {"enough": False, "days": len(days)}
    ranked = sorted(days, key=lambda d: (-steps[d], d))
    k = max(1, int(round(len(days) * JOINT_HIGH_SHARE)))
    high = set(ranked[:k])

    def nxt(d):
        return (date.fromisoformat(d) + timedelta(days=1)).isoformat()

    def side(sel):
        vals = [pain[nxt(d)] for d in sel if nxt(d) in pain]
        return {"days": len(sel), "with_pain_logged": len(vals),
                "avg_next_day_pain": round(float(sum(vals)) / len(vals), 1) if vals else None}
    return {"enough": True, "threshold": steps[ranked[k - 1]], "high_days": sorted(high),
            "high": side(sorted(high)), "other": side([d for d in days if d not in high])}


@app.route("/api/joint/watch")
@login_required
def api_joint_watch():
    n = JOINT_WINDOW_DAYS
    since = (date.today() - timedelta(days=n - 1)).isoformat()
    feed = _link_get(FITLOG_URL + "/api/feed/watch?days=" + str(n + 1), ttl=60)
    steps, walk = {}, {}
    ok = bool(feed and feed.get("ok"))
    if ok:
        for dd in feed.get("daily") or []:
            v = ((dd.get("metrics") or {}).get("steps") or {}).get("value")
            if v is not None and (dd.get("date") or "") >= since:
                steps[dd["date"]] = int(round(float(v)))
        for w in feed.get("workouts") or []:
            if (w.get("kind") or "") in ("walk", "treadmill") and (w.get("date") or "") >= since:
                walk[w["date"]] = walk.get(w["date"], 0) + int(round(float(w.get("minutes") or 0)))
    pain = joint_pain_by_day(since)
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    rows = [{"day": d, "steps": steps.get(d), "walk_min": walk.get(d), "pain": pain.get(d)} for d in days]
    wt = [[r["day"], r["weight"]] for r in db().execute(
        "SELECT day, weight FROM vitals WHERE weight IS NOT NULL AND day>=? ORDER BY day, vtime",
        ((date.today() - timedelta(days=180)).isoformat(),)).fetchall()]
    return jsonify(connected=ok, rows=rows, compare=step_pain_compare(steps, pain), weight=wt)


@app.route("/api/painmeds")
@login_required
def api_painmeds():
    j = _link_get(RXGUARD_URL + "/api/feed/dose", ttl=60)
    if not j or not j.get("ok"):
        return jsonify(ok=False, err="RxGuard could not be read just now.")
    return jsonify(ok=True, on=bool(j.get("on")), err=j.get("err") or "",
                   rows=j.get("rows") or [], outside=j.get("outside") or [],
                   classes=j.get("classes") or [], findings=j.get("findings") or [])


def lipid_series():
    rows = [(r["day"], r["test"], r["num"], r["unit"] or "") for r in db().execute(
        "SELECT day, test, num, unit FROM rec_labs WHERE num IS NOT NULL").fetchall()]
    rows += [(r["day"], r["analyte"], r["value"], "") for r in db().execute(
        "SELECT day, analyte, value FROM labs WHERE value IS NOT NULL").fetchall()]
    out = []
    for label, pat, months in LIPID_GROUPS:
        rx_ = re.compile(pat, re.I)
        pts = sorted(set((d[:10], float(v)) for d, t, v, _u in rows if d and t and rx_.search(t)))
        unit = next((u for d, t, v, u in rows if u and t and rx_.search(t)), "")
        g = {"label": label, "unit": unit, "months": months, "points": [list(p) for p in pts],
             "last": None, "due": None, "overdue": False}
        if pts:
            g["last"] = {"day": pts[-1][0], "value": pts[-1][1]}
            if months:
                due = date.fromisoformat(pts[-1][0]) + timedelta(days=int(round(months * 30.44)))
                g["due"] = due.isoformat()
                g["overdue"] = due <= date.today()
        out.append(g)
    return out


def _statin_listed():
    # Its own cache entry (the query is ignored by RxGuard), so a medicine just
    # added is seen within a minute rather than whenever the pain card last read.
    j = _link_get(RXGUARD_URL + "/api/feed/dose?q=classes", ttl=60)
    return bool(j and any("statin" in (c or "").lower() for c in j.get("med_classes") or []))


@app.route("/api/joint/lipids")
@login_required
def api_joint_lipids():
    statin = _statin_listed()
    last = db().execute("SELECT day, answer FROM muscle_check ORDER BY day DESC, id DESC LIMIT 1").fetchone()
    ask = statin and (not last or last["day"] <= (date.today() - timedelta(days=MUSCLE_ASK_DAYS)).isoformat())
    return jsonify(groups=lipid_series(), statin=statin, ask_muscle=bool(ask),
                   last_muscle=dict(last) if last else None)


@app.route("/api/joint/muscle", methods=["POST"])
@login_required
def api_joint_muscle():
    a = (J().get("answer") or "").strip().lower()
    if a not in ("no", "yes"):
        return jsonify(ok=False, err="Answer yes or no."), 400
    db().execute("INSERT INTO muscle_check(day, answer, created) VALUES(?,?,?)", (today(), a, now_s()))
    db().commit()
    return jsonify(ok=True)

'''
E.append(("joint block",
          "# ------------------------------------------------------------------ family\n"
          "# GUTLOG_V3360_FAMILY. The Family Edition runs a separate copy of GutLog,\n",
          JOINT_PY.lstrip("\n") + "\n"
          "# ------------------------------------------------------------------ family\n"
          "# GUTLOG_V3360_FAMILY. The Family Edition runs a separate copy of GutLog,\n"))

CARDS = '''  <div id="nowFT"></div>
  <!-- GUTLOG_V3370_JOINT -- shown under the joint profile only. -->
  <div class="card" id="nowJoint" style="display:none">
    <p class="q">Joint pain</p>
    <div class="chips" id="jSite"></div>
    <p class="lbl" style="margin-top:12px">Score 0-10</p>
    <div class="chips" id="jScore"></div>
    <div class="vtm nstart"><span class="lb">Started at</span><input type="time" id="jStart"></div>
    <p class="lbl" style="margin-top:12px">What brought it on</p>
    <div class="chips" id="jTrig"></div>
    <div class="row3" style="margin-top:12px">
      <div><p class="lbl">Morning stiffness (min)</p><input type="number" inputmode="numeric" id="jStiff" placeholder="&mdash;"></div>
      <div><p class="lbl">Could walk (min) before pain</p><input type="number" inputmode="numeric" id="jWalk" placeholder="&mdash;"></div>
    </div>
    <button type="button" class="btn primary" id="jSave" style="margin-top:12px">Save</button>
    <div id="jList" style="margin-top:10px"></div>
  </div>
  <div class="card" id="nowJointWatch" style="display:none">
    <p class="q">Steps and joint pain</p>
    <div id="jwBody"></div>
  </div>
  <div class="card" id="nowPainMeds" style="display:none">
    <p class="q">Pain medicines today</p>
    <div id="pmBody"></div>
  </div>
  <div class="card" id="nowLipid" style="display:none">
    <p class="q">Cholesterol-medicine checks</p>
    <div id="lpBody"></div>
  </div>
'''
E.append(("cards", '  <div id="nowFT"></div>\n', CARDS))

JOINT_JS = r'''/* GUTLOG_V3370_JOINT -- joint pain, pain-medicine totals, steps against pain,
   lipid checks. Shown only when /api/joint/cfg says the profile is joint. */
const JST={site:'',score:null,trig:[]};
const JOINT_CARDS=['nowJoint','nowJointWatch','nowPainMeds','nowLipid'];
async function loadJoint(){
  const c=$('#nowJoint');if(!c)return;
  let cfg;try{cfg=await jget('/api/joint/cfg');}catch(e){return;}
  JOINT_CARDS.forEach(i=>{const x=document.getElementById(i);if(x)x.style.display=cfg.show?'':'none';});
  if(!cfg.show)return;
  jBuild(cfg);jList();loadJointWatch();loadPainMeds();loadLipids();
}
function jPick(box,b){[...box.children].forEach(x=>x.classList.toggle('sel',x===b));}
function jBuild(cfg){
  const s=$('#jSite');if(!s||s.dataset.built)return;s.dataset.built='1';
  cfg.sites.forEach(v=>{const b=el('div','chip',v);b.onclick=()=>{JST.site=v;jPick(s,b);};s.appendChild(b);});
  const sc=$('#jScore');
  for(let i=0;i<=10;i++){const b=el('div','chip num',String(i));b.onclick=()=>{JST.score=i;jPick(sc,b);};sc.appendChild(b);}
  const t=$('#jTrig');
  cfg.triggers.forEach(v=>{const b=el('div','chip',v);b.onclick=()=>{
    const k=JST.trig.indexOf(v);if(k>=0)JST.trig.splice(k,1);else JST.trig.push(v);
    b.classList.toggle('sel',JST.trig.indexOf(v)>=0);};t.appendChild(b);});
  $('#jSave').onclick=async()=>{
    if(!JST.site||JST.score===null){toast('Pick the joint and a score');return;}
    try{
      await post('/api/joint',{site:JST.site,score:JST.score,start:$('#jStart').value||'',
        triggers:JST.trig,stiff_min:$('#jStiff').value,walk_min:$('#jWalk').value});
      toast('Saved');JST.site='';JST.score=null;JST.trig=[];
      ['#jSite','#jScore','#jTrig'].forEach(q=>[...$(q).children].forEach(x=>x.classList.remove('sel')));
      $('#jStiff').value='';$('#jWalk').value='';jList();loadJointWatch();
    }catch(e){toast(e.message);}
  };
}
async function jList(){
  const box=$('#jList');if(!box)return;
  let j;try{j=await jget('/api/joint?days=7');}catch(e){return;}
  box.innerHTML='';
  (j.rows||[]).slice(0,6).forEach(r=>{
    const p=el('p','hint');p.style.margin='4px 2px';
    let t=r.day.slice(5)+' '+(r.jtime||'')+' - '+r.site+' '+r.score+'/10';
    if(r.triggers&&r.triggers.length)t+=' - '+r.triggers.join(', ');
    if(r.stiff_min!=null)t+=' - stiff '+r.stiff_min+' min';
    if(r.walk_min!=null)t+=' - walked '+r.walk_min+' min';
    p.textContent=t;box.appendChild(p);});
}
async function loadJointWatch(){
  const box=$('#jwBody');if(!box)return;
  let j;try{j=await jget('/api/joint/watch');}catch(e){return;}
  box.innerHTML='';
  if(!j.connected)box.appendChild(el('p','hint','The watch data (FitLog) is not connected yet.'));
  const cm=j.compare||{};
  if(cm.enough){
    const a=cm.high,b=cm.other;
    const f=x=>x.avg_next_day_pain==null?'no pain logged the next day':('next-day pain '+x.avg_next_day_pain+' (from '+x.with_pain_logged+' day'+(x.with_pain_logged===1?'':'s')+')');
    box.appendChild(el('p','',a.days+' high-step day'+(a.days===1?'':'s')+' ('+cm.threshold+' steps or more): '+f(a)+'.'));
    box.appendChild(el('p','',b.days+' other day'+(b.days===1?'':'s')+': '+f(b)+'.'));
    box.appendChild(el('p','hint','High-step days are the top third of the last 28 days by steps. Counts only.'));
  }else{box.appendChild(el('p','hint','Not enough days with steps yet to compare.'));}
  const tb=el('div');tb.style.cssText='margin-top:8px;font-size:15px';
  (j.rows||[]).slice(-7).reverse().forEach(r=>{
    const p=el('p','hint');p.style.margin='2px';
    p.textContent=r.day.slice(5)+': '+(r.steps==null?'- steps':r.steps+' steps')+
      (r.walk_min?(', walked '+r.walk_min+' min'):'')+(r.pain==null?'':(', joint pain '+r.pain+'/10'));
    tb.appendChild(p);});
  box.appendChild(tb);
  const w=j.weight||[];
  if(w.length){const last=w[w.length-1],first=w[0];
    box.appendChild(el('p','hint','Weight: '+last[1]+' kg on '+last[0].slice(5)+
      (w.length>1?(' (was '+first[1]+' kg on '+first[0].slice(5)+')'):'')));}
}
async function loadPainMeds(){
  const box=$('#pmBody');if(!box)return;
  let j;try{j=await jget('/api/painmeds');}catch(e){return;}
  box.innerHTML='';
  if(!j.ok||!j.on){box.appendChild(el('p','hint',j.err||'Daily totals are not set up in RxGuard.'));return;}
  if(!(j.rows||[]).length&&!(j.outside||[]).length)box.appendChild(el('p','hint','No pain medicine logged in the last 24 hours.'));
  (j.rows||[]).forEach(r=>{
    const p=el('p','');p.style.margin='4px 0';
    const lim=r.ceiling==null?'no ceiling':(' of '+r.ceiling+' '+r.unit);
    p.textContent=r.name+': '+r.total+' '+r.unit+lim+(r.state==='over'?' - OVER':'')+
      ((r.products||[]).length?(' ('+r.products.join(', ')+')'):'');
    if(r.state==='over')p.style.fontWeight='700';
    box.appendChild(p);});
  (j.outside||[]).forEach(r=>{box.appendChild(el('p','hint',r.name+': '+r.total+' '+r.unit+
    ' - kept apart, not added to the tablet total'));});
  (j.classes||[]).forEach(c=>{box.appendChild(el('p','hint',c.label+': '+c.count+
    (c.count?(' ('+c.taken.join(', ')+')'):'')+(c.count>c.limit?' - more than '+c.limit:'')));});
  (j.findings||[]).filter(f=>f.flag==='RED'||f.flag==='AMBER').forEach(f=>{
    const p=el('p','',f.flag+' '+f.rule_id+': '+f.title);p.style.fontWeight='600';box.appendChild(p);});
}
async function loadLipids(){
  const box=$('#lpBody');if(!box)return;
  let j;try{j=await jget('/api/joint/lipids');}catch(e){return;}
  box.innerHTML='';
  if(j.ask_muscle){
    const q=el('div');q.appendChild(el('p','','Any new muscle aches, cramps or weakness this week?'));
    const r=el('div','btnrow');
    ['no','yes'].forEach(a=>{const b=el('button','btn ghost',a==='no'?'No':'Yes');b.type='button';
      b.onclick=async()=>{try{await post('/api/joint/muscle',{answer:a});toast('Noted');loadLipids();}catch(e){toast(e.message);}};
      r.appendChild(b);});
    q.appendChild(r);box.appendChild(q);
  }
  (j.groups||[]).forEach(g=>{
    const p=el('p','');p.style.margin='4px 0';
    if(!g.last){p.className='hint';p.textContent=g.label+': no result yet';}
    else{p.textContent=g.label+': '+g.last.value+(g.unit?' '+g.unit:'')+' on '+g.last.day+
      (g.due?((g.overdue?' - repeat due since ':' - repeat by ')+g.due):'')+
      (g.points.length>1?(' ('+g.points.length+' results)'):'');
      if(g.overdue)p.style.fontWeight='700';}
    box.appendChild(p);});
  box.appendChild(el('p','hint','Repeat dates follow a stated rule: lipids every 6 months, liver tests every 12, CK when muscles ache.'));
}
/* GUTLOG_V3360_FAMILY -- one line, only when there are members. */
'''
E.append(("joint js", "/* GUTLOG_V3360_FAMILY -- one line, only when there are members. */\n", JOINT_JS))
E.append(("loadNow joint",
          "async function loadNow(){\n  loadMirror();\n  loadFamilyLink();\n",
          "async function loadNow(){\n  loadMirror();\n  loadFamilyLink();\n  loadJoint();   /* GUTLOG_V3370_JOINT */\n"))

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
    print("GutLog joint focus -> v" + VERSION)
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
    bak = a.file + ".bak-v3370-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_family_b.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

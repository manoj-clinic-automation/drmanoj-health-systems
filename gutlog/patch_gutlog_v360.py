#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.5.0 -> v3.6.0  ::  Phase C (GutLog side) -- stock, vitals log, feed

 1. STOCK AND REFILL (Meds -> Stock). Stock is computed at read time from
    events, never stored as a running number, so an undone dose puts its
    tablet back by itself.
      * Pillbox medicines (scheduled, fixed dose) come out of stock when the
        pillbox is filled -- "Pillbox filled" deducts 7 days (editable) of
        every counted pillbox medicine. Doses taken from the pillbox do not
        deduct again.
      * Per-dose medicines (extras, PRN) come out of stock per logged dose.
        An extra dose of a pillbox medicine also deducts, since it did not
        come from the pillbox.
      * Count = what is in the strips/bottle now (not the pillbox).
        Bought = add a pack. Either mode can be switched per medicine.
      * Variant-strength medicines are not tracked: one count cannot stand
        for three strengths, and a wrong count makes the alert noise.
    Alerts: a pillbox medicine turns red when stock will not cover the next
    7-day fill, amber when it covers only one more. A per-dose medicine
    turns red under 3 days at its 14-day average use, amber under 7, and
    amber at zero even if rarely used. A banner on the Now tab carries them.

 2. VITALS LOG (Review tab, second card). BP / pulse chart with faint 140
    and 90 guides, averages (all, morning, evening), highest and lowest,
    every reading in a table, and a vitals.csv download. Follows the
    30 d / 90 d / 6 mo selector.

 3. FEED FOR RXGUARD AND FITLOG. Two read-only endpoints, bearer-gated:
    /api/feed/stack (regimen + what was actually taken) and
    /api/feed/doses (dose events since a date). The token lives in
    feed.token beside app.py, mode 600, created on first run -- the
    companion apps read the same file, so there is no token to copy.

Requires v3.5.0. Anchor-verified, idempotent, compile-checked, Jinja-safe,
.bak before write, self-restoring on post-write failure. Python 3.9.
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
MARKER = "GUTLOG_V360_PHASE_C"
PREV = "GUTLOG_V350_PHASE_B"

SCHEMA_ADD = '''CREATE TABLE IF NOT EXISTS stock_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER NOT NULL, kind TEXT NOT NULL,
  qty REAL NOT NULL, at TEXT NOT NULL, note TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS stock_meds (
  med_id INTEGER PRIMARY KEY, mode TEXT DEFAULT '');
'''

PY_ROUTES = r'''# ------------------------------------------------------------------ stock / feed
# GUTLOG_V360_PHASE_C -- stock is derived from events at read time; the
# feed gives RxGuard and FitLog read-only access to what was taken.
_STRENGTH_UNITS = ("mg", "mcg", "µg", "ug", "g", "iu", "%")


def _units(dose_text):
    """Tablets (or puffs, sachets) per dose from free text. '1 tab' -> 1,
    '2 caps' -> 2, '1/2' or a half sign -> 0.5. A strength ('40 mg') is not
    a count, so it reads as 1."""
    s = (dose_text or "").strip().lower().replace("½", "0.5")
    if not s:
        return 1.0
    parts = s.split()
    if len(parts) > 1 and parts[1] in _STRENGTH_UNITS:
        return 1.0
    tok = parts[0]
    try:
        if "/" in tok:
            a, b = tok.split("/", 1)
            v = float(a) / float(b)
        else:
            v = float(tok)
    except (ValueError, ZeroDivisionError):
        return 1.0
    return v if 0 < v <= 20 else 1.0


def _now_at():
    return today() + " " + now_hm()


def _stock_rows():
    con = db()
    tday = today()
    since14 = (date.fromisoformat(tday) - timedelta(days=13)).isoformat()
    meds = con.execute(
        "SELECT id, name, pack_size FROM prnmeds WHERE active=1 ORDER BY sort, id").fetchall()
    name_to_id = dict((r["name"], r["id"]) for r in con.execute(
        "SELECT id, name FROM prnmeds").fetchall())
    sched = {}
    for l in con.execute(
            "SELECT med_id, dose_text, variants FROM med_schedule WHERE valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)", (tday, tday)).fetchall():
        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False})
        if (l["variants"] or "").strip():
            s["variants"] = True
        else:
            s["units"] += _units(l["dose_text"])
    modes = dict((r["med_id"], r["mode"]) for r in con.execute(
        "SELECT med_id, mode FROM stock_meds").fetchall())
    ev = {}
    for e in con.execute(
            "SELECT id, med_id, kind, qty, at FROM stock_events ORDER BY at, id").fetchall():
        ev.setdefault(e["med_id"], []).append(e)
    first = [v[0]["at"][:10] for v in ev.values() if v]
    lo = min(first + [since14])
    by_med = {}
    for d in con.execute(
            "SELECT med_id, medicine, day, dtime, status, sched_id, dose_text FROM doses "
            "WHERE day>=? AND COALESCE(status,'')<>'SKIPPED'", (lo,)).fetchall():
        mid = d["med_id"] or name_to_id.get(d["medicine"])
        if mid:
            by_med.setdefault(mid, []).append(d)

    rows = []
    for m in meds:
        mid = m["id"]
        s = sched.get(mid)
        fixed = bool(s and s["units"] > 0)
        mode = modes.get(mid) or ("pillbox" if fixed else "per_dose")
        if mode == "pillbox" and not fixed:
            mode = "per_dose"
        r = {"med_id": mid, "name": m["name"], "mode": mode, "can_pillbox": fixed,
             "pack_size": m["pack_size"] or 0, "tracked": False,
             "trackable": not (s and s["variants"]), "current": None,
             "per_day": 0.0, "days_left": None, "level": "", "why": "", "counted_at": ""}
        if s and s["variants"]:
            r["why"] = "strengths vary - not tracked"
            rows.append(r)
            continue
        doses = by_med.get(mid, [])
        uses = [d for d in doses if mode == "per_dose" or not d["sched_id"]]
        if mode == "pillbox":
            per_day = s["units"]
        else:
            per_day = sum(_units(d["dose_text"]) for d in uses if d["day"] >= since14) / 14.0
        r["per_day"] = round(per_day, 2)
        evs = ev.get(mid, [])
        counts = [e for e in evs if e["kind"] == "COUNT"]
        if not counts:
            rows.append(r)
            continue
        c = counts[-1]
        cur = float(c["qty"])
        for e in evs:
            if (e["at"], e["id"]) <= (c["at"], c["id"]):
                continue
            if e["kind"] == "ADD":
                cur += e["qty"]
            elif e["kind"] == "FILL":
                cur -= e["qty"]
        for d in uses:
            if ((d["day"] or "") + " " + (d["dtime"] or "00:00")) > c["at"]:
                cur -= _units(d["dose_text"])
        r.update(tracked=True, current=round(cur, 1), counted_at=c["at"])
        dl = (cur / per_day) if per_day > 0 else None
        if dl is not None:
            r["days_left"] = round(max(dl, 0), 1)
        if mode == "pillbox":
            need = per_day * 7
            if cur <= 0:
                r["level"], r["why"] = "RED", "none left for the next fill"
            elif cur < need:
                r["level"], r["why"] = "RED", "will not cover the next 7-day fill"
            elif cur < 2 * need:
                r["level"], r["why"] = "AMBER", "enough for one more fill"
        else:
            if cur <= 0:
                r["level"] = "RED" if per_day > 0 else "AMBER"
                r["why"] = "none left"
            elif dl is not None and dl < 3:
                r["level"], r["why"] = "RED", "about %d days left" % int(dl + 0.5)
            elif dl is not None and dl < 7:
                r["level"], r["why"] = "AMBER", "about %d days left" % int(dl + 0.5)
        rows.append(r)
    return rows


def _stock_qty(d, lo, hi):
    try:
        v = float(d.get("qty"))
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


@app.route("/api/stock")
@login_required
def api_stock():
    rows = _stock_rows()
    order = {"RED": 0, "AMBER": 1, "": 2}
    rows.sort(key=lambda r: (order.get(r["level"], 2), not r["tracked"], not r["trackable"]))
    lf = db().execute("SELECT at FROM stock_events WHERE kind='FILL' "
                      "ORDER BY at DESC, id DESC LIMIT 1").fetchone()
    preview = [{"med_id": r["med_id"], "name": r["name"], "per_day": r["per_day"]}
               for r in rows if r["tracked"] and r["mode"] == "pillbox" and r["per_day"] > 0]
    return jsonify(rows=rows, last_fill=lf["at"] if lf else "", fill_preview=preview)


def _stock_med(d):
    try:
        mid = int(d.get("med_id"))
    except (TypeError, ValueError):
        return None
    r = db().execute("SELECT id FROM prnmeds WHERE id=?", (mid,)).fetchone()
    return mid if r else None


@app.route("/api/stock/count", methods=["POST"])
@login_required
def api_stock_count():
    d = J()
    mid = _stock_med(d)
    q = _stock_qty(d, 0, 100000)
    if not mid or q is None:
        return jsonify(ok=False, err="Enter how many are left."), 400
    db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) VALUES(?,?,?,?,?,?)",
                 (mid, "COUNT", q, _now_at(), "", now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/stock/add", methods=["POST"])
@login_required
def api_stock_add():
    d = J()
    mid = _stock_med(d)
    q = _stock_qty(d, 0.5, 100000)
    if not mid or q is None:
        return jsonify(ok=False, err="Enter how many were bought."), 400
    if not db().execute("SELECT 1 FROM stock_events WHERE med_id=? AND kind='COUNT'",
                        (mid,)).fetchone():
        return jsonify(ok=False, err="Count what is left first, then add purchases."), 400
    db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) VALUES(?,?,?,?,?,?)",
                 (mid, "ADD", q, _now_at(), "", now_s()))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/stock/mode", methods=["POST"])
@login_required
def api_stock_mode():
    d = J()
    mid = _stock_med(d)
    mode = d.get("mode")
    if not mid or mode not in ("pillbox", "per_dose"):
        return jsonify(ok=False, err="Bad mode."), 400
    db().execute("INSERT INTO stock_meds(med_id, mode) VALUES(?,?) "
                 "ON CONFLICT(med_id) DO UPDATE SET mode=excluded.mode", (mid, mode))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/stock/fill", methods=["POST"])
@login_required
def api_stock_fill():
    d = J()
    try:
        days = int(d.get("days") or 7)
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= 31:
        return jsonify(ok=False, err="Days must be 1 to 31."), 400
    items = [r for r in _stock_rows()
             if r["tracked"] and r["mode"] == "pillbox" and r["per_day"] > 0]
    if not items:
        return jsonify(ok=False, err="No counted pillbox medicines - count them first."), 400
    at, batch = _now_at(), "fill:" + now_s() + ":" + secrets.token_hex(4)
    for r in items:
        db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) "
                     "VALUES(?,?,?,?,?,?)",
                     (r["med_id"], "FILL", round(r["per_day"] * days, 2), at, batch, now_s()))
    db().commit()
    return jsonify(ok=True, n=len(items), days=days)


@app.route("/api/stock/fill/undo", methods=["POST"])
@login_required
def api_stock_fill_undo():
    r = db().execute("SELECT note FROM stock_events WHERE kind='FILL' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return jsonify(ok=False, err="No fill to undo."), 400
    db().execute("DELETE FROM stock_events WHERE kind='FILL' AND note=?", (r["note"],))
    db().commit()
    return jsonify(ok=True)


# ---- feed: read-only, bearer-gated, loopback consumers (RxGuard, FitLog)
def _feed_token():
    p = os.environ.get("GUTLOG_FEED_TOKEN_FILE") or os.path.join(BASE, "feed.token")
    try:
        with open(p, "r") as f:
            t = f.read().strip()
        if len(t) >= 32:
            return t
    except OSError:
        pass
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            with open(p, "r") as f:
                t = f.read().strip()
            return t if len(t) >= 32 else None
        except OSError:
            return None
    except OSError:
        return None
    t = secrets.token_hex(32)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


_FEED_TOKEN = _feed_token()


def feed_required(fn):
    import hmac

    @wraps(fn)
    def w(*a, **k):
        tok = _FEED_TOKEN
        got = (request.headers.get("Authorization") or "")
        got = got[7:].strip() if got.startswith("Bearer ") else ""
        if not tok or not got or not hmac.compare_digest(tok, got):
            return jsonify(ok=False, err="unauthorised"), 401
        return fn(*a, **k)
    return w


def _feed_since(default_days):
    s = _valid_day(request.args.get("since"))
    floor = (date.today() - timedelta(days=180)).isoformat()
    if not s:
        s = (date.today() - timedelta(days=default_days - 1)).isoformat()
    return max(s, floor)


def _feed_events(since):
    meds = dict((r["id"], r) for r in db().execute(
        "SELECT id, name, molecule FROM prnmeds").fetchall())
    by_name = dict((r["name"], r) for r in meds.values())
    out = []
    for d in db().execute(
            "SELECT day, dtime, medicine, med_id, status, sched_id, dose_text FROM doses "
            "WHERE day>=? AND COALESCE(status,'')<>'SKIPPED' ORDER BY day, dtime",
            (since,)).fetchall():
        m = meds.get(d["med_id"]) or by_name.get(d["medicine"])
        out.append({"day": d["day"], "time": d["dtime"] or "",
                    "name": m["name"] if m else d["medicine"],
                    "molecule": (m["molecule"] or "") if m else "",
                    "med_id": m["id"] if m else None,
                    "scheduled": bool(d["sched_id"]),
                    "dose_text": d["dose_text"] or ""})
    return out


@app.route("/api/feed/doses")
@feed_required
def api_feed_doses():
    since = _feed_since(14)
    return jsonify(ok=True, app="gutlog", since=since, events=_feed_events(since))


@app.route("/api/feed/stack")
@feed_required
def api_feed_stack():
    try:
        days = max(1, min(90, int(request.args.get("days") or 14)))
    except ValueError:
        days = 14
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    tday = today()
    regimen = [dict(r) for r in db().execute(
        "SELECT p.id AS med_id, p.name, p.molecule, s.slot, s.dose_text, s.variants "
        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id WHERE s.valid_from<=? "
        "AND (s.valid_to='' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",
        (tday, tday)).fetchall()]
    taken = {}
    for e in _feed_events(since):
        k = e["med_id"] or e["name"]
        t = taken.setdefault(k, {"med_id": e["med_id"], "name": e["name"],
                                 "molecule": e["molecule"], "doses": 0, "days": set(),
                                 "last_day": "", "scheduled": False})
        t["doses"] += 1
        t["days"].add(e["day"])
        t["last_day"] = max(t["last_day"], e["day"])
        t["scheduled"] = t["scheduled"] or e["scheduled"]
    out = []
    for t in taken.values():
        t["days"] = len(t["days"])
        out.append(t)
    out.sort(key=lambda t: (-t["days"], t["name"]))
    return jsonify(ok=True, app="gutlog", since=since, days=days, today=tday,
                   regimen=regimen, taken=out)


'''

HTML_STOCK_SEG = ('<button data-s="prn" class="sel">PRN dose</button><button data-s="course">Courses</button>'
                  '<button data-s="sched">Schedule</button><button data-s="stock">Stock</button>')

HTML_STOCK_SUB = r'''  <div class="sub" id="meds-stock">
    <div class="card" id="stPill">
      <p class="q">Pillbox</p>
      <p class="hint st-last" style="margin:0 2px 8px"></p>
      <div class="vtm"><span class="lb">Days</span><input type="number" id="stDays" min="1" max="31" value="7">
        <button type="button" class="btn tiny go" id="stFill">Pillbox filled</button></div>
      <p class="hint st-prev" style="margin:10px 2px 0"></p>
      <button type="button" class="mini" id="stFillUndo" style="margin-top:6px">Undo last fill</button>
    </div>
    <p class="hint">Count = what is left in the strips or bottle, not in the pillbox. Pillbox medicines come
      out of stock when you fill it; the others per logged dose.</p>
    <div id="stList"></div>
  </div>

'''

HTML_VITALS = r'''    <div id="dvMiss"></div>
  </div>
  <div class="card" id="vitalsLog">
    <p class="q">Vitals log</p>
    <div id="vtChart"></div>
    <p class="legend"><span class="dot" style="background:#B3372A"></span><b>Systolic</b>
      <span class="dot" style="background:#0B6E6E"></span><b>Diastolic</b>
      <span class="dot" style="background:#C8860A"></span><b>Pulse</b></p>
    <div id="vtSum"></div>
    <div id="vtTable" class="vtwrap"></div>
    <a class="exp" href="/export/vitals.csv">Download vitals.csv</a>
  </div>
'''

JS_ADD = r'''/* ---------- STOCK (GUTLOG_V360_PHASE_C) ---------- */
function fmtQ(v){return String(Math.round(v*10)/10);}
async function loadStockAlerts(){
  const box=$('#nowStock');if(!box)return;
  try{
    const j=await jget('/api/stock');
    const al=j.rows.filter(r=>r.level);
    box.innerHTML='';
    if(!al.length)return;
    const d=document.createElement('div');
    d.className='stockalert '+(al.some(r=>r.level==='RED')?'red':'amber');
    d.innerHTML='<b>Refill</b><span></span>';
    d.querySelector('span').textContent=al.map(r=>r.name+' - '+r.why).join(' · ');
    d.onclick=()=>{switchTab('meds');setSeg('meds','stock');};
    box.appendChild(d);
  }catch(e){}
}
async function loadStock(){
  const j=await jget('/api/stock');
  const pb=$('#stPill');
  pb.querySelector('.st-last').textContent=j.last_fill?('Last filled '+j.last_fill):'Not filled yet.';
  const days=()=>Math.max(1,Math.min(31,parseInt($('#stDays').value||'7',10)||7));
  const prev=()=>{
    const pv=pb.querySelector('.st-prev');
    pv.textContent=j.fill_preview.length
      ?('Filling '+days()+' days takes: '+j.fill_preview.map(x=>x.name+' '+fmtQ(x.per_day*days())).join(' · '))
      :'No counted pillbox medicines yet - count them below first.';
  };
  prev();
  $('#stDays').oninput=prev;
  $('#stFill').onclick=async()=>{
    try{const r=await post('/api/stock/fill',{days:days()});
      toast('Pillbox filled: '+r.n+' medicines, '+r.days+' days');loadStock();loadStockAlerts();}
    catch(err){toast(err.message);}
  };
  $('#stFillUndo').onclick=async()=>{
    if(!confirm('Undo the last pillbox fill?'))return;
    try{await post('/api/stock/fill/undo',{});toast('Last fill undone');loadStock();loadStockAlerts();}
    catch(err){toast(err.message);}
  };
  const list=$('#stList');list.innerHTML='';
  j.rows.forEach(r=>list.appendChild(stockRow(r)));
}
function stockRow(r){
  const w=document.createElement('div');
  w.className='strow'+(r.level?(' lv-'+r.level.toLowerCase()):'')+(r.tracked?'':' untracked');
  w.innerHTML='<div class="sh"><b></b><span class="sq"></span></div><div class="ss"></div>'+
    '<div class="sb"></div><div class="vtm sf" hidden><input type="number" step="0.5" min="0" inputmode="decimal">'+
    '<button type="button" class="btn tiny go">Save</button></div>';
  w.querySelector('.sh b').textContent=r.name;
  const sq=w.querySelector('.sq'),ss=w.querySelector('.ss'),sb=w.querySelector('.sb');
  const modeTxt=r.mode==='pillbox'?'pillbox':'per dose';
  if(!r.trackable){sq.textContent='';ss.textContent=r.why;w.querySelector('.sf').remove();return w;}
  if(r.tracked){
    sq.textContent=fmtQ(r.current)+' left';
    const bits=[modeTxt];
    if(r.per_day>0)bits.push(fmtQ(r.per_day)+'/day');
    if(r.why)bits.push(r.why);
    else if(r.days_left!=null)bits.push('about '+Math.floor(r.days_left)+' days');
    ss.textContent=bits.join(' · ');
  }else{
    sq.textContent='';
    ss.textContent=modeTxt+' · not counted yet';
  }
  const sf=w.querySelector('.sf'),inp=sf.querySelector('input'),go=sf.querySelector('button');
  let act='';
  const open=(a,val,ph)=>{act=a;sf.hidden=false;inp.value=val;inp.placeholder=ph;inp.focus();};
  const mk=(txt,fn)=>{const b=document.createElement('button');b.type='button';b.className='btn tiny ghost';
    b.textContent=txt;b.onclick=fn;sb.appendChild(b);};
  mk(r.tracked?'Count':'Set count',()=>open('count','','how many left'));
  if(r.tracked)mk('Bought',()=>open('add',r.pack_size||'','how many bought'));
  if(r.can_pillbox)mk(r.mode==='pillbox'?'Make per dose':'Make pillbox',async()=>{
    try{await post('/api/stock/mode',{med_id:r.med_id,mode:r.mode==='pillbox'?'per_dose':'pillbox'});
      loadStock();loadStockAlerts();}catch(err){toast(err.message);}
  });
  go.onclick=async()=>{
    if(inp.value===''){toast('Enter a number');return;}
    try{
      await post(act==='add'?'/api/stock/add':'/api/stock/count',{med_id:r.med_id,qty:inp.value});
      toast(act==='add'?'Added':'Count saved');loadStock();loadStockAlerts();
    }catch(err){toast(err.message);}
  };
  return w;
}

/* ---------- VITALS LOG (GUTLOG_V360_PHASE_C) ---------- */
function vtEsc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);}
function vitalsChart(rows){
  const bp=rows.filter(v=>v.sys||v.dia).slice().reverse();
  if(bp.length<2)return '<p class="hint" style="margin:0">The chart appears after two readings.</p>';
  const W=320,H=150,P=24;
  let lo=1e9,hi=0;
  bp.forEach(v=>[v.sys,v.dia,v.pulse].forEach(x=>{
    if(x){if(x<lo)lo=x;if(x>hi)hi=x;}
  }));
  lo=Math.min(lo,90)-8;hi=Math.max(hi,140)+8;
  const xs=i=>P+(W-P-6)*(i/(bp.length-1));
  const ys=v=>H-16-(H-26)*((v-lo)/(hi-lo));
  let s='<svg class="chart" viewBox="0 0 '+W+' '+H+'">';
  [140,90].forEach(gl=>{const y=ys(gl).toFixed(1);
    s+='<line x1="'+P+'" x2="'+W+'" y1="'+y+'" y2="'+y+'" stroke="#C9D6D0" stroke-dasharray="3 3"/>'+
       '<text x="2" y="'+(parseFloat(y)+3)+'" font-size="9" fill="#5B7370">'+gl+'</text>';});
  [['sys','#B3372A'],['dia','#0B6E6E'],['pulse','#C8860A']].forEach(k=>{
    let p='',dots='';
    bp.forEach((v,i)=>{if(!v[k[0]])return;const x=xs(i).toFixed(1),y=ys(v[k[0]]).toFixed(1);
      p+=(p?'L':'M')+x+' '+y+' ';dots+='<circle cx="'+x+'" cy="'+y+'" r="2" fill="'+k[1]+'"/>';});
    if(p)s+='<path d="'+p+'" fill="none" stroke="'+k[1]+'" stroke-width="2"/>'+dots;
  });
  s+='<text x="'+P+'" y="'+(H-3)+'" font-size="9" fill="#5B7370">'+vtEsc(bp[0].day)+'</text>'+
     '<text x="'+(W-58)+'" y="'+(H-3)+'" font-size="9" fill="#5B7370">'+vtEsc(bp[bp.length-1].day)+'</text></svg>';
  return s;
}
function renderVitals(rows){
  const tb=$('#vtTable');if(!tb)return;
  rows=rows||[];
  $('#vtChart').innerHTML=vitalsChart(rows);
  const bp=rows.filter(v=>v.sys&&v.dia);
  const av=a=>Math.round(a.reduce((x,y)=>x+y,0)/a.length);
  const avBP=a=>a.length?(av(a.map(v=>v.sys))+'/'+av(a.map(v=>v.dia))):'';
  const sum=$('#vtSum');sum.innerHTML='';
  const line=t=>{const p=document.createElement('p');p.className='tot';p.textContent=t;sum.appendChild(p);};
  if(bp.length){
    const pl=bp.filter(v=>v.pulse).map(v=>v.pulse);
    line(bp.length+' readings · average '+avBP(bp)+(pl.length?(' · pulse '+av(pl)):''));
    const am=bp.filter(v=>(v.vtime||'')<'12:00'),pm=bp.filter(v=>(v.vtime||'')>='17:00');
    if(am.length&&pm.length)line('Morning '+avBP(am)+' · evening '+avBP(pm));
    const hiR=bp.reduce((a,b)=>b.sys>a.sys?b:a),loR=bp.reduce((a,b)=>b.sys<a.sys?b:a);
    line('Highest '+hiR.sys+'/'+hiR.dia+' ('+hiR.day+' '+(hiR.vtime||'')+') · lowest '+loR.sys+'/'+loR.dia);
  }else line('No blood pressure readings in this range.');
  if(!rows.length){tb.innerHTML='';return;}
  let t='<table><tr><th>Day</th><th>Time</th><th>BP</th><th>Pulse</th><th>Wt</th><th>Temp</th></tr>';
  rows.slice(0,300).forEach(v=>{
    t+='<tr><td>'+vtEsc(v.day)+'</td><td>'+vtEsc(v.vtime)+'</td><td><b>'+
      ((v.sys||v.dia)?(vtEsc(v.sys||'-')+'/'+vtEsc(v.dia||'-')):'')+'</b></td><td>'+vtEsc(v.pulse||'')+
      '</td><td>'+vtEsc(v.weight||'')+'</td><td>'+vtEsc(v.temp||'')+'</td></tr>';
  });
  tb.innerHTML=t+'</table>';
}

'''

CSS_ADD = r'''/* GUTLOG_V360_PHASE_C -- stock rows, refill banner, vitals table */
.stockalert{display:flex;gap:10px;align-items:baseline;padding:11px 14px;border-radius:13px;margin:0 0 10px;
  font-size:14px;cursor:pointer;border:1.5px solid}
.stockalert b{font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.stockalert.red{background:#FBEDEC;border-color:#E4B9B3;color:var(--err)}
.stockalert.amber{background:#FFF3DC;border-color:#EBCB8B;color:#7A5200}
.strow{background:var(--card);border:1.5px solid var(--line);border-radius:13px;padding:11px 13px;margin:0 0 8px}
.strow .sh{display:flex;gap:10px;align-items:baseline}
.strow .sh b{font-size:15.5px}
.strow .sq{margin-left:auto;font-weight:800;font-size:15px;color:var(--teal-d)}
.strow .ss{font-size:13px;color:var(--muted);margin-top:2px}
.strow .sb{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}
.strow .sb .btn{margin:0;background:#fff;color:var(--teal);border-color:#BAD2C8}
.strow .sf[hidden]{display:none}
.strow.untracked{opacity:.8}
.strow.lv-red{border-color:#E4B9B3;background:#FDF5F4}
.strow.lv-red .sq{color:var(--err)}
.strow.lv-amber{border-color:#EBCB8B;background:#FFFAF0}
.strow.lv-amber .sq{color:#8A5A00}
.vtwrap{overflow-x:auto;margin-top:8px}
.vtwrap table{font-size:13.5px}
#vitalsLog .tot{margin:4px 2px}
'''


def build_edits():
    E = []
    a = "GUTLOG_V342_PAINSITE " + PREV
    E.append(("version marker", a, a + " " + MARKER))

    a = "  old_time TEXT, new_day TEXT, new_time TEXT, at TEXT);\n\"\"\""
    n = "  old_time TEXT, new_day TEXT, new_time TEXT, at TEXT);\n" + SCHEMA_ADD + "\"\"\""
    E.append(("schema stock tables", a, n))

    a = "# ------------------------------------------------------------------ review\n"
    E.append(("python routes", a, PY_ROUTES + a))

    a = ('<button data-s="prn" class="sel">PRN dose</button><button data-s="course">Courses</button>'
         '<button data-s="sched">Schedule</button>')
    E.append(("stock segment button", a, HTML_STOCK_SEG))

    a = '  <div class="sub" id="meds-course">\n'
    E.append(("stock segment body", a, HTML_STOCK_SUB + a))

    a = '<section class="tab sel" id="tab-now">\n'
    E.append(("now refill banner", a, a + '  <div id="nowStock"></div>\n'))

    a = '    <div id="dvMiss"></div>\n  </div>\n'
    E.append(("vitals card", a, HTML_VITALS))

    a = "  const rv=await jget('/api/review?days='+rvDays);\n"
    E.append(("vitals render hook", a, a + "  renderVitals(rv.vitals);\n"))

    a = "/* ---------- DAY BY DAY (GUTLOG_V350_PHASE_B) ----------"
    E.append(("stock + vitals js", a, JS_ADD + a))

    a = "async function loadNow(){\n"
    E.append(("now alerts hook", a, a + "  loadStockAlerts();\n"))

    a = "  seg[section]=s;\n"
    E.append(("setSeg stock hook", a, a + "  if(section==='meds'&&s==='stock')loadStock();\n"))

    a = "  if(t==='meds'&&seg.meds==='sched'){loadSchedMeds();loadSchedule();}\n"
    E.append(("switchTab stock hook", a, a + "  if(t==='meds'&&seg.meds==='stock')loadStock();\n"))

    a = "(tab==='files'&&(seg.files==='vault'||seg.files==='labs'));"
    n = "(tab==='files'&&(seg.files==='vault'||seg.files==='labs'))||(tab==='meds'&&seg.meds==='stock');"
    E.append(("hide save on stock", a, n))

    a = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n</style></head><body>"
    E.append(("css", a, CSS_ADD + a))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow",
               "buildNowStatics", "bindFolds", "loadDayView", "dvEdit", "dvMissRow",
               "loadReview", "loadStock", "loadStockAlerts", "stockRow",
               "renderVitals", "vitalsChart"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("GutLog Phase C -> v3.6.0")
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
        print("FATAL: this file is not at v3.5.0. Apply that first.")
        return 1
    if "def api_stock" in src or "stock_events" in src:
        print("FATAL: Phase C pieces already present -- unexpected state. Nothing written.")
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

    missing = [f for f in MUST_DEFINE
               if not re.search(r"function\s+" + f + r"\s*\(", out)]
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
    bak = args.file + ".bak-v360-" + stamp
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

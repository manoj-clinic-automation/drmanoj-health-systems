#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.7.0 -> v3.8.0  ::  Records -- your health record inside GutLog

The Files tab becomes Records:
  Summary  -- a readable health summary: medicines and vitals live from
              GutLog, latest key results, active and resolved problems,
              precautions, missing documents; Print / PDF.
  Reports  -- every report on one timeline by year, filter by kind, tap to
              open the original. Uploads wait here as "to be processed".
  Trends   -- every laboratory value as printed, lab flags in red, a small
              chart per test, tap for the full series.
  Plan     -- the investigation plan, each item marked done when in.
  Upload / Labs / Consults -- unchanged.
New read-only feed /api/feed/profile gives RxGuard condition codes only.
The clinical content arrives separately (import_records.py, run once on the
server from a manifest kept outside the code repository).

Requires v3.7.0. Anchor-verified, idempotent, compile-checked, Jinja-safe,
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
MARKER = "GUTLOG_V380_RECORDS"
PREV = "GUTLOG_V370_SALTS_ACTIVITY"

SCHEMA_ADD = "CREATE TABLE IF NOT EXISTS rec_docs (\n  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, kind TEXT, title TEXT, source TEXT,\n  finding TEXT, stored TEXT DEFAULT '', orig TEXT DEFAULT '', sha TEXT UNIQUE, status TEXT DEFAULT 'filed', created TEXT);\nCREATE TABLE IF NOT EXISTS rec_labs (\n  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, test TEXT, section TEXT, value TEXT, num REAL,\n  unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, UNIQUE(day, test, lab));\nCREATE TABLE IF NOT EXISTS rec_plan (\n  id INTEGER PRIMARY KEY AUTOINCREMENT, pos INTEGER, test TEXT UNIQUE, why TEXT, timing TEXT,\n  status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '');\n"
PY = '# ------------------------------------------------------------------ records\n# GUTLOG_V380_RECORDS -- the health record inside GutLog: documents, lab\n# trends, a readable summary and the investigation plan. Clinical content\n# (records_profile.local.json, the imported documents and values) lives only\n# on this server; the code carries none of it.\nPROFILE_FILE = os.path.join(BASE, "records_profile.local.json")\nREC_KEY_TESTS = ["ESR (Erythrocyte Sedimentation Rate)", "C-Reactive Protein (Quantitative)",\n                 "FAECAL CALPROTECTIN (stool)", "Haemoglobin", "Platelet Count", "MPV", "Serum Sodium",\n                 "Serum Ionic Calcium", "HbA1c (Glycosylated Haemoglobin)", "Serum Creatinine",\n                 "SGOT (AST)", "SGPT (ALT)", "Total Cholesterol", "LDL Cholesterol", "Triglycerides",\n                 "25-Hydroxy Vitamin D", "Vitamin B12 (Total)", "TSH (ultrasensitive)"]\n\n\ndef rec_profile():\n    try:\n        with open(PROFILE_FILE, "r", encoding="utf-8") as fh:\n            p = json.load(fh)\n        return p if isinstance(p, dict) else {}\n    except (OSError, ValueError):\n        return {}\n\n\n@app.route("/api/records/docs")\n@login_required\ndef api_rec_docs():\n    rows = [dict(r) for r in db().execute(\n        "SELECT id, day, kind, title, source, finding, status, (stored<>\'\') AS has_file "\n        "FROM rec_docs ORDER BY day DESC, id DESC").fetchall()]\n    ups = [{"id": r["id"], "day": r["day"], "kind": "Uploaded", "title": r["label"],\n            "source": r["ftype"], "finding": "Uploaded by you - waiting to be processed into the record.",\n            "status": "inbox", "has_file": 1, "vault": True}\n           for r in db().execute("SELECT id, day, ftype, label FROM files ORDER BY day DESC, id DESC").fetchall()]\n    return jsonify(docs=rows, uploads=ups)\n\n\n@app.route("/rec/doc/<int:did>")\n@login_required\ndef rec_doc_file(did):\n    r = db().execute("SELECT stored, orig FROM rec_docs WHERE id=?", (did,)).fetchone()\n    if not r or not r["stored"]:\n        abort(404)\n    return send_from_directory(UPLOAD_DIR, r["stored"], download_name=r["orig"] or r["stored"])\n\n\ndef _rec_series(test):\n    return [dict(r) for r in db().execute(\n        "SELECT day, value, num, unit, ref, flag, lab FROM rec_labs WHERE test=? ORDER BY day, id",\n        (test,)).fetchall()]\n\n\n@app.route("/api/records/labs")\n@login_required\ndef api_rec_labs():\n    t = request.args.get("test")\n    if t:\n        return jsonify(test=t, series=_rec_series(t))\n    key = rec_profile().get("key_tests") or REC_KEY_TESTS\n    out = []\n    for r in db().execute(\n            "SELECT test, section, COUNT(*) AS n, MAX(day) AS last FROM rec_labs "\n            "GROUP BY test ORDER BY section, test").fetchall():\n        s = _rec_series(r["test"])\n        last = s[-1] if s else {}\n        out.append({"test": r["test"], "section": r["section"], "n": r["n"], "key": r["test"] in key,\n                    "last_day": last.get("day"), "last_value": last.get("value"), "last_flag": last.get("flag"),\n                    "unit": last.get("unit"), "ref": last.get("ref"),\n                    "points": [[x["day"], x["num"], x["flag"]] for x in s if x["num"] is not None]})\n    out.sort(key=lambda x: (not x["key"], key.index(x["test"]) if x["key"] else 0, x["section"], x["test"]))\n    return jsonify(tests=out)\n\n\n@app.route("/api/records/summary")\n@login_required\ndef api_rec_summary():\n    p = rec_profile()\n    tday = today()\n    meds = [dict(r) for r in db().execute(\n        "SELECT p.name, COALESCE(p.molecule,\'\') AS molecule, COALESCE(ms.strength,\'\') AS strength, "\n        "s.slot, s.dose_text FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "\n        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE s.valid_from<=? "\n        "AND (s.valid_to=\'\' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",\n        (tday, tday)).fetchall()]\n    since = (date.today() - timedelta(days=30)).isoformat()\n    prn = [dict(r) for r in db().execute(\n        "SELECT medicine AS name, COUNT(*) AS n FROM doses WHERE day>=? AND status=\'EXTRA\' "\n        "GROUP BY medicine ORDER BY n DESC", (since,)).fetchall()]\n    vit = [dict(r) for r in db().execute(\n        "SELECT day, vtime, sys, dia, pulse, weight, temp FROM vitals ORDER BY day DESC, vtime DESC "\n        "LIMIT 6").fetchall()]\n    key = p.get("key_tests") or REC_KEY_TESTS\n    labs = []\n    for t in key:\n        s = _rec_series(t)\n        if s:\n            labs.append({"test": t, "day": s[-1]["day"], "value": s[-1]["value"], "flag": s[-1]["flag"],\n                         "unit": s[-1]["unit"], "ref": s[-1]["ref"]})\n    plan = [dict(r) for r in db().execute("SELECT status FROM rec_plan").fetchall()]\n    ndocs = db().execute("SELECT COUNT(*) AS n FROM rec_docs").fetchone()["n"]\n    master = db().execute("SELECT id, title FROM rec_docs WHERE kind=\'Summary\' ORDER BY day DESC, id DESC "\n                          "LIMIT 3").fetchall()\n    return jsonify(profile={k: p.get(k) for k in ("updated", "problems", "resolved", "precautions",\n                                                  "missing", "note")},\n                   meds=meds, prn=prn, vitals=vit, labs=labs, docs=ndocs,\n                   plan={"total": len(plan), "done": sum(1 for x in plan if x["status"] == "done")},\n                   master=[dict(r) for r in master])\n\n\n@app.route("/api/records/plan")\n@login_required\ndef api_rec_plan():\n    return jsonify(items=[dict(r) for r in db().execute(\n        "SELECT id, pos, test, why, timing, status, done_day, note FROM rec_plan ORDER BY pos, id").fetchall()])\n\n\n@app.route("/api/records/plan/<int:pid>", methods=["POST"])\n@login_required\ndef api_rec_plan_set(pid):\n    d = J()\n    st = d.get("status")\n    if st not in ("planned", "done"):\n        return jsonify(ok=False, err="Bad status."), 400\n    day = _valid_day(d.get("done_day") or today()) if st == "done" else ""\n    if st == "done" and not day:\n        return jsonify(ok=False, err="Pick a real date, not in the future."), 400\n    note = (d.get("note") or "")[:300]\n    cur = db().execute("UPDATE rec_plan SET status=?, done_day=?, note=? WHERE id=?", (st, day or "", note, pid))\n    db().commit()\n    if not cur.rowcount:\n        return jsonify(ok=False, err="Not found."), 404\n    return jsonify(ok=True)\n\n\n@app.route("/api/feed/profile")\n@feed_required\ndef api_feed_profile():\n    """Condition codes only - never text - for RxGuard\'s checks."""\n    codes = [c for c in (rec_profile().get("conditions") or []) if isinstance(c, str) and\n             re.match(r"^[a-z_]{3,40}$", c)]\n    return jsonify(ok=True, app="gutlog", conditions=codes)\n\n\n'
SEGS = '  <div class="seg" data-seg="files">\n    <button data-s="summary" class="sel">Summary</button><button data-s="reports">Reports</button><button data-s="trends">Trends</button><button data-s="plan">Plan</button>\n  </div>\n  <div class="seg" data-seg="files">\n    <button data-s="vault">Upload</button><button data-s="labs">Labs</button><button data-s="consults">Consults</button>\n  </div>\n\n  <div class="sub sel" id="files-summary">\n    <div class="card"><div class="rs-head"><p class="q" style="margin:0">Health summary</p>\n      <button type="button" class="btn tiny ghost" id="rsPrint">Print / PDF</button></div>\n      <p class="hint" id="rsUpd" style="margin:4px 0 0"></p></div>\n    <div id="rsBody"></div>\n  </div>\n\n  <div class="sub" id="files-reports">\n    <div class="chips" id="rdKinds"></div>\n    <div id="rdList"></div>\n  </div>\n\n  <div class="sub" id="files-trends">\n    <p class="hint">Values exactly as printed. A red dot is a value the laboratory flagged. Tap a test for every result.</p>\n    <div id="rtList"></div>\n  </div>\n\n  <div class="sub" id="files-plan">\n    <p class="hint">Investigations chosen for the way forward. Mark each one done when the report is in.</p>\n    <div id="rpList"></div>\n  </div>\n\n'
JS = "/* ---------- RECORDS (GUTLOG_V380_RECORDS) ---------- */\nfunction el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}\nfunction fmtDay(d){if(!d)return '';const p=d.split('-');const M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];\n  return p.length===3?(p[2]+'-'+M[+p[1]-1]+'-'+p[0]):d;}\nfunction spark(points,w,h){\n  const nums=points.filter(p=>p[1]!==null&&p[1]!==undefined);\n  if(nums.length<2)return null;\n  const xs=nums.map(p=>Date.parse(p[0])),ys=nums.map(p=>p[1]);\n  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);\n  const X=v=>4+(x1===x0?0:(v-x0)/(x1-x0))*(w-8),Y=v=>h-4-(y1===y0?0.5:(v-y0)/(y1-y0))*(h-8);\n  const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');\n  svg.setAttribute('viewBox','0 0 '+w+' '+h);svg.setAttribute('class','rt-spark');\n  const pl=document.createElementNS(ns,'polyline');\n  pl.setAttribute('points',nums.map((p,i)=>X(xs[i]).toFixed(1)+','+Y(ys[i]).toFixed(1)).join(' '));\n  pl.setAttribute('fill','none');pl.setAttribute('stroke','#0F6B5C');pl.setAttribute('stroke-width','1.6');svg.appendChild(pl);\n  nums.forEach((p,i)=>{const c=document.createElementNS(ns,'circle');c.setAttribute('cx',X(xs[i]).toFixed(1));\n    c.setAttribute('cy',Y(ys[i]).toFixed(1));c.setAttribute('r',p[2]?'3':'2');c.setAttribute('fill',p[2]?'#B3372A':'#0F6B5C');svg.appendChild(c);});\n  return svg;\n}\nasync function loadRecSummary(){\n  const j=await jget('/api/records/summary');const box=$('#rsBody');box.innerHTML='';\n  const P=j.profile||{};\n  $('#rsUpd').textContent=(P.updated?('Record reviewed '+fmtDay(P.updated)+' · '):'')+j.docs+' reports on file · medicines and vitals are live from GutLog';\n  function card(title){const c=el('div','card rs-card');c.appendChild(el('p','q',title));box.appendChild(c);return c;}\n  const m=card('Medicines now');\n  if(!j.meds.length)m.appendChild(el('p','hint','No scheduled medicines.'));\n  j.meds.forEach(x=>{const r=el('div','rs-row');r.appendChild(el('b',null,x.name));\n    r.appendChild(el('span','rs-meta',[x.molecule,x.strength,x.dose_text,x.slot.toLowerCase()].filter(Boolean).join(' · ')));m.appendChild(r);});\n  if(j.prn.length)m.appendChild(el('p','hint','As needed, last 30 days: '+j.prn.map(x=>x.name+' ×'+x.n).join(', ')));\n  if((P.precautions||[]).length){const c=card('Precautions that apply to you');\n    P.precautions.forEach(x=>{const r=el('div','rs-prec '+(x.flag||''));\n      if(x.flag)r.appendChild(el('span','rs-flag '+x.flag,x.flag));r.appendChild(el('b',null,x.title));\n      r.appendChild(el('p','rs-meta',x.text));c.appendChild(r);});}\n  if(j.vitals.length){const c=card('Recent vitals');const t=el('table','rs-tab');\n    j.vitals.forEach(v=>{const tr=el('tr');[fmtDay(v.day)+' '+(v.vtime||''),(v.sys?v.sys+'/'+v.dia:''),(v.pulse?'pulse '+v.pulse:''),\n      (v.temp?v.temp+'°':''),(v.weight?v.weight+' kg':'')].forEach(s=>tr.appendChild(el('td',null,s)));t.appendChild(tr);});c.appendChild(t);}\n  if(j.labs.length){const c=card('Latest key results');const t=el('table','rs-tab');\n    j.labs.forEach(x=>{const tr=el('tr'+'');const a=el('td',null,x.test);const b=el('td',x.flag?'rs-hi':null,x.value+(x.unit?' '+x.unit:''));\n      const d=el('td','rs-meta',fmtDay(x.day));tr.appendChild(a);tr.appendChild(b);tr.appendChild(d);t.appendChild(tr);});\n    c.appendChild(t);c.appendChild(el('p','hint','Red = flagged by the laboratory. Full series under Trends.'));}\n  if((P.problems||[]).length){const c=card('Active problems');\n    P.problems.forEach(x=>{const r=el('div','rs-row');r.appendChild(el('b',null,x.name));\n      r.appendChild(el('span','rs-meta',[x.since,x.status].filter(Boolean).join(' · ')));c.appendChild(r);});}\n  if((P.resolved||[]).length){const c=card('Resolved or excluded');\n    P.resolved.forEach(x=>{const r=el('div','rs-row');r.appendChild(el('b',null,x.name));r.appendChild(el('span','rs-meta',x.status||''));c.appendChild(r);});}\n  const pc=card('Investigation plan');pc.appendChild(el('p',null,j.plan.done+' of '+j.plan.total+' done.'));\n  const pb=el('button','btn tiny ghost','Open the plan');pb.type='button';pb.onclick=()=>setSeg('files','plan');pc.appendChild(pb);\n  if((P.missing||[]).length){const c=card('Documents still missing');c.appendChild(el('p','rs-meta',P.missing.join(' · ')));}\n  if(j.master.length){const c=card('Full narrative record');j.master.forEach(x=>{const a=el('a','rs-link',x.title);\n    a.href='/rec/doc/'+x.id;a.target='_blank';c.appendChild(a);});}\n  if(P.note)box.appendChild(el('p','hint',P.note));\n}\nlet recKind='All';\nasync function loadRecDocs(){\n  const j=await jget('/api/records/docs');const all=j.uploads.concat(j.docs);\n  const kinds=['All'];all.forEach(d=>{if(kinds.indexOf(d.kind)<0)kinds.push(d.kind);});\n  const kb=$('#rdKinds');kb.innerHTML='';\n  kinds.forEach(k=>{const c=el('div','chip'+(k===recKind?' sel':''),k);c.onclick=()=>{recKind=k;loadRecDocs();};kb.appendChild(c);});\n  const box=$('#rdList');box.innerHTML='';let year='';\n  const list=all.filter(d=>recKind==='All'||d.kind===recKind).sort((a,b)=>(b.day||'').localeCompare(a.day||''));\n  if(!list.length){box.appendChild(el('p','hint','Nothing here yet.'));return;}\n  list.forEach(d=>{const y=(d.day||'').slice(0,4);\n    if(y!==year){year=y;box.appendChild(el('p','rd-year',y||'Undated'));}\n    const r=el('div','rd-row'+(d.status==='inbox'?' inbox':''));\n    const top=el('div','rd-top');top.appendChild(el('span','rd-day',fmtDay(d.day)));top.appendChild(el('span','rd-kind',d.kind));\n    r.appendChild(top);r.appendChild(el('b','rd-title',d.title));\n    if(d.source)r.appendChild(el('span','rs-meta',d.source));\n    if(d.finding){const f=el('p','rd-find',d.finding);f.onclick=()=>f.classList.toggle('open');r.appendChild(f);}\n    if(d.has_file){const a=el('a','rs-link','Open report');a.href=d.vault?('/file/'+d.id):('/rec/doc/'+d.id);a.target='_blank';r.appendChild(a);}\n    box.appendChild(r);});\n}\nasync function loadRecTrends(){\n  const j=await jget('/api/records/labs');const box=$('#rtList');box.innerHTML='';\n  if(!j.tests.length){box.appendChild(el('p','hint','No results imported yet.'));return;}\n  let sec='';let keyDone=false;\n  j.tests.forEach(t=>{\n    const label=t.key?'Key results':t.section;\n    if(label!==sec){sec=label;box.appendChild(el('p','rd-year',label));}\n    const r=el('div','rt-row');const head=el('div','rt-head');\n    const nm=el('div','rt-name');nm.appendChild(el('b',null,t.test));\n    nm.appendChild(el('span','rs-meta',t.n+' result'+(t.n>1?'s':'')+' · last '+fmtDay(t.last_day)));\n    head.appendChild(nm);const sv=spark(t.points,92,30);if(sv)head.appendChild(sv);\n    head.appendChild(el('span','rt-last'+(t.last_flag?' rs-hi':''),t.last_value));\n    r.appendChild(head);\n    const det=el('div','rt-det');det.hidden=true;r.appendChild(det);\n    head.onclick=async()=>{if(!det.hidden){det.hidden=true;return;}\n      const s=await jget('/api/records/labs?test='+encodeURIComponent(t.test));det.innerHTML='';\n      const tb=el('table','rs-tab');s.series.slice().reverse().forEach(x=>{const tr=el('tr');\n        tr.appendChild(el('td',null,fmtDay(x.day)));tr.appendChild(el('td',x.flag?'rs-hi':null,x.value));\n        tr.appendChild(el('td','rs-meta',x.lab||''));tb.appendChild(tr);});\n      det.appendChild(tb);if(t.ref||t.unit)det.appendChild(el('p','hint',[t.unit,t.ref?('ref '+t.ref):''].filter(Boolean).join(' · ')));\n      det.hidden=false;};\n    box.appendChild(r);});\n}\nasync function loadRecPlan(){\n  const j=await jget('/api/records/plan');const box=$('#rpList');box.innerHTML='';\n  if(!j.items.length){box.appendChild(el('p','hint','No plan loaded yet.'));return;}\n  j.items.forEach(x=>{const r=el('div','card rp-row'+(x.status==='done'?' done':''));\n    const h=el('div','rp-head');h.appendChild(el('span','rp-n',String(x.pos)));h.appendChild(el('b',null,x.test));r.appendChild(h);\n    if(x.why)r.appendChild(el('p','rs-meta',x.why));\n    if(x.timing)r.appendChild(el('p','rp-when',x.timing));\n    const b=el('button','btn tiny'+(x.status==='done'?' ghost':''),x.status==='done'?('Done '+fmtDay(x.done_day)+' · undo'):'Mark done');b.type='button';\n    b.onclick=async()=>{try{await post('/api/records/plan/'+x.id,{status:x.status==='done'?'planned':'done',done_day:todayISO});\n      loadRecPlan();}catch(e){toast(e.message);} };\n    r.appendChild(b);box.appendChild(r);});\n}\nfunction loadRecords(s){\n  if(s==='summary')loadRecSummary();\n  if(s==='reports')loadRecDocs();\n  if(s==='trends')loadRecTrends();\n  if(s==='plan')loadRecPlan();\n}\n$('#rsPrint').onclick=()=>window.print();\n\n"
CSS = '/* GUTLOG_V380_RECORDS */\n.seg[data-seg="files"]+.seg[data-seg="files"]{margin-top:-6px}\n.rs-head{display:flex;justify-content:space-between;align-items:center;gap:8px}\n.rs-card .q{margin-bottom:6px}\n.rs-row{display:flex;flex-direction:column;padding:6px 0;border-bottom:1px solid var(--line,#E4E7E5)}\n.rs-row:last-child{border-bottom:0}\n.rs-meta{color:var(--muted);font-size:13px;margin:2px 0 0}\n.rs-prec{padding:7px 0;border-bottom:1px solid var(--line,#E4E7E5)}\n.rs-prec:last-child{border-bottom:0}\n.rs-flag{display:inline-block;font-size:11px;font-weight:800;padding:1px 6px;border-radius:6px;margin-right:6px}\n.rs-flag.RED{background:#FBEDEC;color:#B3372A}.rs-flag.AMBER{background:#FFF3DC;color:#8A5A00}\n.rs-tab{width:100%;border-collapse:collapse;font-size:13.5px}\n.rs-tab td{padding:5px 4px;border-bottom:1px solid var(--line,#E4E7E5);vertical-align:top}\n.rs-hi{color:#B3372A;font-weight:700}\n.rs-link{display:inline-block;margin-top:6px;font-weight:700;color:var(--teal)}\n.rd-year{font-weight:800;color:var(--muted);margin:14px 2px 6px;font-size:13px;letter-spacing:.04em}\n.rd-row{background:#fff;border-radius:12px;padding:10px 12px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}\n.rd-row.inbox{border-left:4px solid #C08A00}\n.rd-top{display:flex;gap:8px;align-items:center;font-size:12.5px;color:var(--muted)}\n.rd-kind{background:var(--chip);border-radius:6px;padding:1px 6px;font-weight:700}\n.rd-title{display:block;margin:3px 0 1px}\n.rd-find{font-size:13.5px;margin:4px 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}\n.rd-find.open{display:block}\n.rt-row{background:#fff;border-radius:12px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}\n.rt-head{display:flex;align-items:center;gap:8px;padding:9px 12px;cursor:pointer}\n.rt-name{flex:1;min-width:0;display:flex;flex-direction:column}\n.rt-spark{width:92px;height:30px;flex:0 0 auto}\n.rt-last{min-width:64px;text-align:right;font-weight:700;font-size:13.5px}\n.rt-det{padding:0 12px 10px}\n.rp-head{display:flex;gap:8px;align-items:baseline}\n.rp-n{font-weight:800;color:var(--teal)}\n.rp-when{font-size:13px;margin:4px 0 8px}\n.rp-row.done{opacity:.7}\n#tab-files .rs-card .btn.tiny,#tab-files .rp-row .btn.tiny,#rsPrint{border:1.5px solid var(--teal);color:var(--teal);background:#fff}\n#tab-files .rp-row .btn.tiny:not(.ghost){background:var(--teal);color:#fff}\n@media print{\n  header,#nav,.save,.seg,.toast,#rsPrint{display:none!important}\n  .tab{display:none!important}#tab-files{display:block!important}\n  #tab-files .sub{display:none!important}#files-summary{display:block!important}\n  .card{box-shadow:none;border:1px solid #ccc;break-inside:avoid}\n  body{background:#fff}\n}\n'


def build_edits():
    E = []
    a = "GUTLOG_V360_PHASE_C GUTLOG_V370_SALTS_ACTIVITY"
    E.append(("version", a, a + " " + MARKER))
    a = "  minutes REAL, intensity TEXT DEFAULT '', notes TEXT DEFAULT '', created TEXT);\n"
    E.append(("schema", a, a + SCHEMA_ADD))
    a = "# ------------------------------------------------------------------ review\n"
    E.append(("python", a, PY + a))
    a = ('  <div class="seg" data-seg="files">\n'
         '    <button data-s="vault" class="sel">Vault</button><button data-s="labs">Labs</button>'
         '<button data-s="consults">Consults</button>\n  </div>\n\n  <div class="sub sel" id="files-vault">\n')
    E.append(("records segments", a, SEGS + '  <div class="sub" id="files-vault">\n'))
    a = '<p class="hint">Reports, prescriptions, scan photos. PDF / JPG / PNG, up to 12 MB.</p>'
    E.append(("upload hint", a, '<p class="hint">New reports, prescriptions, scan photos. PDF / JPG / PNG, up to 12 MB. '
                                'They appear under Reports and are processed into your record.</p>'))
    a = "const seg={log:'day',meals:'meal',meds:'prn',files:'vault'};"
    E.append(("default seg", a, "const seg={log:'day',meals:'meal',meds:'prn',files:'summary'};"))
    a = "  if(section==='meds'&&s==='salts')loadSalts();\n"
    E.append(("setSeg records", a, a + "  if(section==='files')loadRecords(s);\n"))
    a = "  if(t==='files'){loadFiles();loadLabs();loadDoctors();}"
    E.append(("switchTab records", a, "  if(t==='files'){loadFiles();loadLabs();loadDoctors();loadRecords(seg.files);}"))
    a = "(tab==='files'&&(seg.files==='vault'||seg.files==='labs'))"
    E.append(("save hidden", a, "(tab==='files'&&seg.files!=='consults')"))
    a = '<button data-t="files"><i>&#128194;</i>Files</button>'
    E.append(("nav label", a, '<button data-t="files"><i>&#128194;</i>Records</button>'))
    a = "/* ---------- SALTS, MEDICINE STATUS, ACTIVITY (GUTLOG_V370) ---------- */"
    E.append(("js", a, JS + a))
    a = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n</style></head><body>"
    E.append(("css", a, CSS + a))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow", "buildNowStatics",
               "bindFolds", "loadDayView", "dvEdit", "dvMissRow", "loadReview", "loadStock",
               "loadStockAlerts", "stockRow", "renderVitals", "vitalsChart", "loadMedStatus",
               "loadSalts", "saltGuess", "buildActTiles", "loadActivity",
               "loadRecSummary", "loadRecDocs", "loadRecTrends", "loadRecPlan", "loadRecords", "spark"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("GutLog records -> v3.8.0")
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
        print("FATAL: this file is not at v3.7.0. Apply that first.")
        return 1
    if "def api_rec_docs" in src or "CREATE TABLE IF NOT EXISTS rec_docs" in src:
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
    bak = args.file + ".bak-v380-" + stamp
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

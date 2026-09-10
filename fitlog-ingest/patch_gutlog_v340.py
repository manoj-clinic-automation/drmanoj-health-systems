#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.3.3 -> v3.4.0  ::  readability and structure pass

Changes, all from using the thing:

 1. EXTRA DOSE no longer lists scheduled medicines. An expected dose
    belongs on the dose card; showing it twice invites logging it in the
    wrong place. A "Show all medicines" button reveals them for the rare
    unplanned dose of a regular medicine.

 2. EXTRA CHIPS reordered by how they are actually used -- gut, pain,
    bowel, rehydration, allergy, sleep -- rather than the order they
    happened to be entered. (Ordering itself is data; see tidy_extras.py.)

 3. TAP FEEDBACK. An extra chip now turns green and holds that state
    until the list refreshes, and each logged extra carries a real Undo
    button rather than small underlined text.

 4. SYMPTOMS are multi-select. Two symptoms at once are written as two
    episodes sharing a timestamp, severity and Bristol -- two findings,
    not one blended row.

 5. COLLAPSIBLE SECTIONS. Blood pressure stays open at the top. Doses,
    extras and symptoms fold, each showing a summary in its header
    ("3 of 8 taken", "2 logged today") so the state is readable without
    expanding anything.

 6. READABILITY. Larger base text, heavier weights, real buttons instead
    of dashed outlines, and a softer background. The old #EFF5F2 against
    white cards is a bright, cool, high-contrast pairing; this moves to a
    warmer, slightly deeper ground with off-white cards, which is easier
    on the eye for something opened at 5am and again at 11pm.

Anchor-verified, idempotent, compile-checked, self-restoring.
Python 3.9 compatible.
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V340_READABILITY"
HERE = os.path.dirname(os.path.abspath(__file__))

NEW_TAB = '''<!-- ============ NOW ============ -->
<section class="tab sel" id="tab-now">
  <div class="card" id="nowBP">
    <p class="q">Blood pressure</p>
    <div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="\u2014"></div>
      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="\u2014"></div>
      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="\u2014"></div>
    </div>
    <button type="button" class="btn primary" id="n_bpSave">Save reading</button>
    <p class="hint" id="n_bpLast" style="margin:10px 0 0"></p>
  </div>

  <div class="card fold" id="nowDoses">
    <button type="button" class="fold-h">
      <span class="ft">Today&rsquo;s doses</span><span class="fs" id="doseSum"></span><span class="fc"></span>
    </button>
    <div class="cbody"><div id="nowSched"></div></div>
  </div>

  <div class="card fold" id="nowExtraCard">
    <button type="button" class="fold-h">
      <span class="ft">Extra dose</span><span class="fs" id="exSum"></span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div class="chips" id="nowExtras"></div>
      <div id="nowExtraList"></div>
      <div class="btnrow">
        <button type="button" class="btn ghost" id="nowShowAll">Show all medicines</button>
        <button type="button" class="btn ghost" id="nowAddMed">Add medicine</button>
      </div>
      <p class="hint" style="margin:10px 0 0">One tap logs it at the current time.</p>
    </div>
  </div>

  <div class="card fold" id="nowSym">
    <button type="button" class="fold-h">
      <span class="ft">Symptom now</span><span class="fs">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody">
      <p class="lbl">What &mdash; pick one or more</p>
      <div class="chips" id="n_symType"></div>
      <p class="lbl" style="margin-top:14px">Severity</p>
      <div class="chips" id="n_symSev"></div>
      <p class="lbl" style="margin-top:14px">Bristol (optional)</p>
      <div class="chips" id="n_symBristol"></div>
      <button type="button" class="btn primary" id="n_symSave" style="margin-top:14px">Save episode</button>
    </div>
  </div>
</section>

'''

CSS_ADD = r'''/* ''' + MARKER + r''' -- softer ground, heavier text, real buttons.
   The previous pairing was a bright cool mint behind pure white cards:
   high contrast, and tiring for something opened at 5am and again late. */
body{background:#E9EDE7;font-size:17px;line-height:1.5}
main{padding:14px 14px 0}
.card{background:#FCFCF9;border:1px solid #CFDBD3;border-radius:18px;padding:16px;
margin:0 0 14px;box-shadow:0 1px 3px rgba(29,47,51,.06)}
.q{font-size:15px;font-weight:800;color:var(--ink);text-transform:none;
letter-spacing:-.1px;margin:0 0 12px}
.lbl{font-size:13.5px;font-weight:600;color:var(--muted);margin:0 0 7px}
.hint{font-size:13.5px}
.chip{padding:11px 16px;font-size:16px;font-weight:600;border-width:2px;
background:#EDF3EF;border-color:#CBDCD3}
.chip.num{min-width:46px;text-align:center;padding:11px 8px}
.chip.sel{background:var(--teal);border-color:var(--teal);color:#fff;font-weight:700}
.chip.just{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:700}
.chip.just::after{content:" \2713"}

/* buttons that look like buttons */
.btn{border:2px solid var(--line);background:#fff;color:var(--ink);
border-radius:13px;padding:13px 18px;font-size:16px;font-weight:700;
cursor:pointer;font-family:inherit}
.btn.primary{background:var(--teal);border-color:var(--teal);color:#fff;width:100%}
.btn.ghost{background:#fff;color:var(--teal);border-color:#BAD2C8}
.btn.tiny{padding:7px 13px;font-size:13.5px;border-radius:10px;
color:var(--err);border-color:#E4C3BE}
.btn:active{transform:scale(.98)}
.btnrow{display:flex;gap:9px;margin-top:12px;flex-wrap:wrap}
.btnrow .btn{flex:1;min-width:140px}

/* collapsible cards */
.card.fold{padding:0;overflow:hidden}
.card.fold .fold-h{width:100%;display:flex;align-items:center;gap:10px;
padding:16px;border:0;background:none;font-family:inherit;font-size:16px;
font-weight:800;color:var(--ink);cursor:pointer;text-align:left}
.card.fold .ft{flex:0 0 auto}
.card.fold .fs{margin-left:auto;font-size:13.5px;font-weight:600;color:var(--muted)}
.card.fold .fc{width:11px;height:11px;border-right:2.5px solid var(--muted);
border-bottom:2.5px solid var(--muted);transform:rotate(45deg) translate(-3px,-3px);
transition:transform .18s;flex:0 0 auto}
.card.fold.open .fc{transform:rotate(-135deg) translate(-3px,-3px)}
.card.fold .cbody{display:none;padding:0 16px 16px}
.card.fold.open .cbody{display:block}
.card.fold.pending .fs{color:var(--amber);font-weight:700}

/* dose rows */
.doserow{padding:13px 14px;border-width:2px;border-radius:14px;margin-bottom:9px}
.doserow .nm b{font-size:16.5px;font-weight:700}
.doserow .nm span{font-size:13px}
.doserow .tick{width:32px;height:32px;border-width:2.5px}
.doserow .sk{font-size:13px;font-weight:600;padding:9px 6px}
.slothd{margin:14px 0 9px;align-items:baseline}
.slothd .sl{font-size:14px;font-weight:800;color:var(--teal-d);
text-transform:uppercase;letter-spacing:.6px}
.slothd .cnt{margin-left:auto;font-size:13.5px;font-weight:700;color:var(--muted)}
.exrow{padding:9px 0;font-size:15px;gap:10px}
.exrow .t{font-size:13px;font-weight:600}
.exrow .m{font-weight:600}
.exrow .u{margin-left:auto}
@media (max-width:430px){
  body{font-size:16.5px}
  .card{padding:14px}
  .card.fold .fold-h{padding:14px}
  .card.fold .cbody{padding:0 14px 14px}
  .chip{padding:10px 14px;font-size:15.5px}
  .btnrow .btn{min-width:0}
}'''


OLD_TAB = '<!-- ============ NOW ============ -->\n<section class="tab sel" id="tab-now">\n  <div class="card" id="nowBP">\n    <p class="q">&#129656; Blood pressure</p>\n    <div class="row3">\n      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="—"></div>\n      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="—"></div>\n      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="—"></div>\n    </div>\n    <button type="button" class="addbtn" id="n_bpSave" style="margin-top:10px">Save reading</button>\n    <p class="hint" id="n_bpLast" style="margin:8px 0 0"></p>\n  </div>\n\n  <div id="nowSched"></div>\n\n  <div class="card" id="nowExtraCard">\n    <p class="q">&#10133; Extra dose</p>\n    <div class="chips" id="nowExtras"></div>\n    <button type="button" class="addbtn" id="nowAddMed" style="margin-top:9px">&#10133; Add medicine</button>\n    <p class="hint" style="margin:8px 0 0">One tap logs it at the current time.</p>\n  </div>\n\n\n  <div class="card" id="nowSym">\n    <p class="q">&#129504; Symptom now</p>\n    <div class="chips" id="n_symType"></div>\n    <p class="lbl" style="margin-top:10px">Severity</p>\n    <div class="chips" id="n_symSev"></div>\n    <p class="lbl" style="margin-top:10px">Bristol (optional)</p>\n    <div class="chips" id="n_symBristol"></div>\n    <button type="button" class="addbtn" id="n_symSave" style="margin-top:10px">Save episode</button>\n  </div>\n</section>\n\n'

OLD_JS = '/* ---------- NOW tab ---------- */\nconst SEVS=[\'1\',\'2\',\'3\',\'4\',\'5\',\'6\',\'7\',\'8\',\'9\',\'10\'];\nconst SYMTYPES=[\'Abdominal pain\',\'Cramp\',\'Bloating\',\'Urgency\',\'Loose stool\',\'Constipation\',\'Nausea\',\'Reflux\',\'Other\'];\nlet nowData=null, nSym={type:null,sev:null,bristol:null};\n\nfunction nowRow(r){\n  const st=r.status||\'\';\n  const cls=st===\'TAKEN\'?\'done\':(st===\'SKIPPED\'?\'skip\':\'\');\n  const mark=st===\'TAKEN\'?\'&#10003;\':(st===\'SKIPPED\'?\'&#8212;\':\'\');\n  const shown=(st===\'TAKEN\'&&r.logged_dose)?r.logged_dose:(r.variants&&!st?\'dose varies\':(r.dose_text||\'\'));\n  /* a logged row is tappable again -- say so, or the affordance\n     is invisible and the hand has to guess */\n  const sub=[shown,r.with_food&&r.with_food!==\'ANY\'?r.with_food.toLowerCase()+\' food\':\'\',\n             st?(st===\'TAKEN\'?\'taken \'+(r.dtime||\'\'):\'skipped\'):\'\',\n             st?\'tap to change\':\'\'].filter(Boolean).join(\' \\u00b7 \');\n  const d=document.createElement(\'div\');\n  d.className=\'doserow \'+cls;\n  d.innerHTML=\'<div class="tick">\'+mark+\'</div><div class="nm"><b></b><span></span></div>\'+\n              (st?\'<button type="button" class="sk">undo</button>\':\'<button type="button" class="sk">skip</button>\');\n  d.querySelector(\'.nm b\').textContent=r.name;\n  d.querySelector(\'.nm span\').textContent=sub;\n  d.onclick=async e=>{\n    if(e.target.classList.contains(\'sk\'))return;\n    if(st){openRowActions(d,r,st);return;}\n    if(r.variants){openVariantPicker(d,r);return;}\n    try{await post(\'/api/now/dose\',{med_id:r.med_id,sched_id:r.sched_id,status:\'TAKEN\',\n      day:nowData.day,dose_text:r.dose_text});toast(\'Logged \'+r.name);loadNow();}\n    catch(err){toast(err.message);}\n  };\n  d.querySelector(\'.sk\').onclick=async ev=>{\n    ev.stopPropagation();\n    try{\n      if(st&&r.dose_id){await post(\'/api/now/undo/\'+r.dose_id,{});toast(\'Undone\');}\n      else{await post(\'/api/now/dose\',{med_id:r.med_id,sched_id:r.sched_id,status:\'SKIPPED\',\n        day:nowData.day,dose_text:r.dose_text});toast(\'Marked skipped\');}\n      loadNow();\n    }catch(err){toast(err.message);}\n  };\n  return d;\n}\n\n/* Tapping a row that is already logged. The instinct is to tap the thing\n   again, so that has to do something -- but an accidental second tap must\n   not silently delete a medication record, hence a strip rather than an\n   immediate toggle. */\nfunction openRowActions(rowEl,r,st){\n  const old=document.querySelector(\'.varpick\');if(old)old.remove();\n  const box=document.createElement(\'div\');\n  box.className=\'varpick\';\n  const canChange=!!r.variants;\n  let html=\'<p class="vt"></p><div class="vb\'+(canChange?\' three\':\'\')+\'">\'+\n    \'<button type="button" class="cx">Cancel</button>\';\n  if(canChange)html+=\'<button type="button" class="ch">Change dose</button>\';\n  if(st===\'TAKEN\')html+=\'<button type="button" class="sp">Skip</button>\';\n  html+=\'<button type="button" class="danger un">Undo</button></div>\';\n  box.innerHTML=html;\n  const was=st===\'TAKEN\'?(\'taken\'+(r.logged_dose?\' \'+r.logged_dose:\'\')):\'skipped\';\n  box.querySelector(\'.vt\').textContent=r.name+\' - \'+was;\n  box.querySelector(\'.cx\').onclick=()=>box.remove();\n  box.querySelector(\'.un\').onclick=async()=>{\n    try{\n      if(r.dose_id)await post(\'/api/now/undo/\'+r.dose_id,{});\n      toast(\'Undone\');box.remove();loadNow();\n    }catch(err){toast(err.message);}\n  };\n  const ch=box.querySelector(\'.ch\');\n  if(ch)ch.onclick=()=>{box.remove();openVariantPicker(rowEl,r);};\n  const sp=box.querySelector(\'.sp\');\n  if(sp)sp.onclick=async()=>{\n    try{\n      await post(\'/api/now/dose\',{med_id:r.med_id,sched_id:r.sched_id,\n        status:\'SKIPPED\',day:nowData.day});\n      toast(\'Marked skipped\');box.remove();loadNow();\n    }catch(err){toast(err.message);}\n  };\n  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);\n  box.scrollIntoView({behavior:\'smooth\',block:\'nearest\'});\n}\n\n/* A scheduled medicine whose dose varies. Multi-select, because a\n   combination such as 145 + 72 is one dose, not two. */\nfunction openVariantPicker(rowEl,r){\n  if(document.querySelector(\'.varpick\'))document.querySelector(\'.varpick\').remove();\n  const opts=r.variants.split(\'|\').map(s=>s.trim()).filter(Boolean);\n  const picked=[];\n  const box=document.createElement(\'div\');\n  box.className=\'varpick\';\n  box.innerHTML=\'<p class="vt"></p><div class="vrow"></div>\'+\n    \'<div class="vb"><button type="button" class="cx">Cancel</button>\'+\n    \'<button type="button" class="go">Log</button></div>\';\n  box.querySelector(\'.vt\').textContent=r.name+\' - which dose?\';\n  const vrow=box.querySelector(\'.vrow\');\n  opts.forEach(v=>{\n    const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=v;\n    b.onclick=()=>{\n      const i=picked.indexOf(v);\n      if(i>=0)picked.splice(i,1);else picked.push(v);\n      b.classList.toggle(\'sel\',picked.indexOf(v)>=0);\n    };\n    vrow.appendChild(b);\n  });\n  box.querySelector(\'.cx\').onclick=()=>box.remove();\n  box.querySelector(\'.go\').onclick=async()=>{\n    if(!picked.length){toast(\'Pick a dose\');return;}\n    const txt=picked.join(\' + \');\n    try{\n      await post(\'/api/now/dose\',{med_id:r.med_id,sched_id:r.sched_id,\n        status:\'TAKEN\',day:nowData.day,dose_text:txt});\n      toast(\'Logged \'+r.name+\' \'+txt);box.remove();loadNow();\n    }catch(err){toast(err.message);}\n  };\n  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);\n  box.scrollIntoView({behavior:\'smooth\',block:\'nearest\'});\n}\n\nasync function loadNow(){\n  nowData=await jget(\'/api/now?day=\'+todayISO);\n  const box=$(\'#nowSched\');box.innerHTML=\'\';\n  if(!nowData.has_schedule){\n    const c=document.createElement(\'div\');c.className=\'card\';\n    c.innerHTML=\'<p class="q">&#128138; Today\\u2019s doses</p><p class="hint" style="margin:0">\'+\n      \'No regular medicines set up yet. Add them under \'+\n      \'<b>Meds &rarr; Schedule</b> and they\\u2019ll appear here as one-tap rows.</p>\';\n    box.appendChild(c);\n  }else{\n    nowData.slots.forEach(s=>{\n      const c=document.createElement(\'div\');c.className=\'card\';\n      const hd=document.createElement(\'div\');hd.className=\'slothd\';\n      hd.innerHTML=\'<p class="q">\'+s.label+\'</p><span class="cnt">\'+s.done+\'/\'+s.total+\'</span>\';\n      c.appendChild(hd);\n      s.rows.forEach(r=>c.appendChild(nowRow(r)));\n      box.appendChild(c);\n    });\n  }\n  /* extras */\n  const ex=$(\'#nowExtras\');ex.innerHTML=\'\';\n  (nowData.meds||[]).forEach(m=>{\n    const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=m.name;\n    b.onclick=async()=>{try{await post(\'/api/now/dose\',{med_id:m.id,status:\'EXTRA\',day:nowData.day});\n      toast(\'Logged \'+m.name);loadNow();}catch(err){toast(err.message);}};\n    ex.appendChild(b);\n  });\n  let list=$(\'#nowExtraList\');\n  if(!list){list=document.createElement(\'div\');list.id=\'nowExtraList\';list.style.marginTop=\'10px\';\n    $(\'#nowExtraCard\').appendChild(list);}\n  list.innerHTML=\'\';\n  (nowData.extras||[]).forEach(e=>{\n    const row=document.createElement(\'div\');row.className=\'exrow\';\n    row.innerHTML=\'<span class="t"></span><span class="m"></span><button type="button" class="u">undo</button>\';\n    row.querySelector(\'.t\').textContent=e.dtime||\'\';\n    row.querySelector(\'.m\').textContent=e.medicine;\n    row.querySelector(\'.u\').onclick=async()=>{await post(\'/api/now/undo/\'+e.id,{});toast(\'Removed\');loadNow();};\n    list.appendChild(row);\n  });\n}\n\nfunction buildNowStatics(){\n  const t=$(\'#n_symType\');\n  SYMTYPES.forEach(v=>{const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=v;\n    b.onclick=()=>{nSym.type=v;[...t.children].forEach(c=>c.classList.toggle(\'sel\',c===b));};t.appendChild(b);});\n  const s=$(\'#n_symSev\');\n  SEVS.forEach(v=>{const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=v;\n    b.onclick=()=>{nSym.sev=v;[...s.children].forEach(c=>c.classList.toggle(\'sel\',c===b));};s.appendChild(b);});\n  const br=$(\'#n_symBristol\');\n  [\'1\',\'2\',\'3\',\'4\',\'5\',\'6\',\'7\'].forEach(v=>{const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=v;\n    b.onclick=()=>{nSym.bristol=(nSym.bristol===v?null:v);\n      [...br.children].forEach(c=>c.classList.toggle(\'sel\',c.textContent===nSym.bristol));};br.appendChild(b);});\n\n  $(\'#n_bpSave\').onclick=async()=>{\n    const sys=$(\'#n_sys\').value,dia=$(\'#n_dia\').value,pulse=$(\'#n_pulse\').value;\n    if(!sys&&!dia&&!pulse){toast(\'Nothing to save\');return;}\n    try{await post(\'/api/vitals\',{day:todayISO,vtime:nowHM(),sys:sys,dia:dia,pulse:pulse});\n      $(\'#n_bpLast\').textContent=\'Saved \'+(sys||\'-\')+\'/\'+(dia||\'-\')+(pulse?\', pulse \'+pulse:\'\')+\' at \'+nowHM();\n      $(\'#n_sys\').value=\'\';$(\'#n_dia\').value=\'\';$(\'#n_pulse\').value=\'\';toast(\'Reading saved\');}\n    catch(err){toast(err.message);}\n  };\n  $(\'#n_symSave\').onclick=async()=>{\n    if(!nSym.type){toast(\'Pick a symptom\');return;}\n    try{await post(\'/api/episodes\',{day:todayISO,etime:nowHM(),category:\'GI\',etype:nSym.type,\n      severity:nSym.sev,bristol:nSym.bristol});\n      toast(\'Episode saved\');nSym={type:null,sev:null,bristol:null};\n      $$(\'#n_symType .chip,#n_symSev .chip,#n_symBristol .chip\').forEach(c=>c.classList.remove(\'sel\'));}\n    catch(err){toast(err.message);}\n  };\n  $(\'#nowAddMed\').onclick=async()=>{\n    const n=prompt(\'Medicine name (include strength)\');if(!n)return;\n    try{await post(\'/api/prnmeds\',{name:n});toast(\'Added\');loadNow();loadSchedMeds();}\n    catch(err){toast(err.message);}\n  };\n}\n\n'

NEW_JS = '/* ---------- NOW tab ---------- */\nconst SEVS=[\'1\',\'2\',\'3\',\'4\',\'5\',\'6\',\'7\',\'8\',\'9\',\'10\'];\nconst SYMTYPES=[\'Abdominal pain\',\'Cramp\',\'Bloating\',\'Urgency\',\'Loose stool\',\'Constipation\',\'Nausea\',\'Reflux\',\'Other\'];\nlet nowData=null, nSym={types:[],sev:null,bristol:null}, showAllMeds=false;\n\n/* Collapsible cards. Only blood pressure stays open; the rest carry their\n   state in the header so the summary is readable without expanding. */\nfunction bindFolds(){\n  $$(\'.card.fold .fold-h\').forEach(h=>{\n    h.onclick=()=>{\n      const card=h.closest(\'.card\');\n      card.classList.toggle(\'open\');\n      if(card.classList.contains(\'open\'))\n        setTimeout(()=>card.scrollIntoView({behavior:\'smooth\',block:\'nearest\'}),80);\n    };\n  });\n}\n\nfunction nowRow(r){\n  const st=r.status||\'\';\n  const cls=st===\'TAKEN\'?\'done\':(st===\'SKIPPED\'?\'skip\':\'\');\n  const mark=st===\'TAKEN\'?\'&#10003;\':(st===\'SKIPPED\'?\'&#8212;\':\'\');\n  const shown=(st===\'TAKEN\'&&r.logged_dose)?r.logged_dose:(r.variants&&!st?\'dose varies\':(r.dose_text||\'\'));\n  const sub=[shown,r.with_food&&r.with_food!==\'ANY\'?r.with_food.toLowerCase()+\' food\':\'\',\n             st?(st===\'TAKEN\'?\'taken \'+(r.dtime||\'\'):\'skipped\'):\'\',\n             st?\'tap to change\':\'\'].filter(Boolean).join(\' \\u00b7 \');\n  const d=document.createElement(\'div\');\n  d.className=\'doserow \'+cls;\n  d.innerHTML=\'<div class="tick">\'+mark+\'</div><div class="nm"><b></b><span></span></div>\'+\n              (st?\'<button type="button" class="sk">undo</button>\':\'<button type="button" class="sk">skip</button>\');\n  d.querySelector(\'.nm b\').textContent=r.name;\n  d.querySelector(\'.nm span\').textContent=sub;\n  d.onclick=async e=>{\n    if(e.target.classList.contains(\'sk\'))return;\n    if(st){openRowActions(d,r,st);return;}\n    if(r.variants){openVariantPicker(d,r);return;}\n    try{await post(\'/api/now/dose\',{med_id:r.med_id,sched_id:r.sched_id,status:\'TAKEN\',\n      day:nowData.day,dose_text:r.dose_text});toast(\'Logged \'+r.name);loadNow();}\n    catch(err){toast(err.message);}\n  };\n  d.querySelector(\'.sk\').onclick=async ev=>{\n    ev.stopPropagation();\n    try{\n      if(st&&r.dose_id){await post(\'/api/now/undo/\'+r.dose_id,{});toast(\'Undone\');}\n      else{await post(\'/api/now/dose\',{med_id:r.med_id,sched_id:r.sched_id,status:\'SKIPPED\',\n        day:nowData.day,dose_text:r.dose_text});toast(\'Marked skipped\');}\n      loadNow();\n    }catch(err){toast(err.message);}\n  };\n  return d;\n}\n\nasync function loadNow(){\n  nowData=await jget(\'/api/now?day=\'+todayISO);\n  const box=$(\'#nowSched\');box.innerHTML=\'\';\n  let done=0,total=0;\n  if(!nowData.has_schedule){\n    box.innerHTML=\'<p class="hint" style="margin:0">No regular medicines yet. \'+\n      \'Add them under <b>Meds &rarr; Schedule</b>.</p>\';\n    $(\'#doseSum\').textContent=\'none set\';\n  }else{\n    nowData.slots.forEach(s=>{\n      done+=s.done;total+=s.total;\n      const hd=document.createElement(\'div\');hd.className=\'slothd\';\n      hd.innerHTML=\'<span class="sl"></span><span class="cnt"></span>\';\n      hd.querySelector(\'.sl\').textContent=s.label;\n      hd.querySelector(\'.cnt\').textContent=s.done+\'/\'+s.total;\n      box.appendChild(hd);\n      s.rows.forEach(r=>box.appendChild(nowRow(r)));\n    });\n    $(\'#doseSum\').textContent=done+\' of \'+total+\' taken\';\n    const card=$(\'#nowDoses\');\n    if(done<total)card.classList.add(\'pending\');else card.classList.remove(\'pending\');\n  }\n\n  /* extras: scheduled medicines are hidden, since an expected dose belongs\n     on the dose card, not here. Show all reveals them for the rare\n     unplanned dose of a regular medicine. */\n  const ex=$(\'#nowExtras\');ex.innerHTML=\'\';\n  const list=showAllMeds?(nowData.meds_all||nowData.meds):nowData.meds;\n  list.forEach(m=>{\n    const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=m.name;\n    b.onclick=async()=>{\n      if(b.dataset.busy)return; b.dataset.busy=1;\n      b.classList.add(\'just\');\n      try{await post(\'/api/now/dose\',{med_id:m.id,status:\'EXTRA\',day:nowData.day});\n        toast(\'Logged \'+m.name);setTimeout(()=>loadNow(),320);}\n      catch(err){b.classList.remove(\'just\');b.dataset.busy=\'\';toast(err.message);}\n    };\n    ex.appendChild(b);\n  });\n  const sa=$(\'#nowShowAll\');\n  if(sa)sa.textContent=showAllMeds?\'Show fewer\':\'Show all medicines\';\n\n  const el=$(\'#nowExtraList\');el.innerHTML=\'\';\n  const extras=nowData.extras||[];\n  $(\'#exSum\').textContent=extras.length?(extras.length+\' logged today\'):\'none yet\';\n  extras.forEach(e=>{\n    const row=document.createElement(\'div\');row.className=\'exrow\';\n    row.innerHTML=\'<span class="t"></span><span class="m"></span>\'+\n      \'<button type="button" class="btn tiny u">Undo</button>\';\n    row.querySelector(\'.t\').textContent=e.dtime||\'\';\n    row.querySelector(\'.m\').textContent=e.medicine;\n    row.querySelector(\'.u\').onclick=async()=>{\n      await post(\'/api/now/undo/\'+e.id,{});toast(\'Removed\');loadNow();};\n    el.appendChild(row);\n  });\n}\n\nfunction buildNowStatics(){\n  bindFolds();\n\n  const t=$(\'#n_symType\');\n  SYMTYPES.forEach(v=>{const b=document.createElement(\'div\');b.className=\'chip\';b.textContent=v;\n    b.onclick=()=>{\n      const i=nSym.types.indexOf(v);\n      if(i>=0)nSym.types.splice(i,1);else nSym.types.push(v);\n      b.classList.toggle(\'sel\',nSym.types.indexOf(v)>=0);\n    };t.appendChild(b);});\n\n  const s=$(\'#n_symSev\');\n  SEVS.forEach(v=>{const b=document.createElement(\'div\');b.className=\'chip num\';b.textContent=v;\n    b.onclick=()=>{nSym.sev=v;[...s.children].forEach(c=>c.classList.toggle(\'sel\',c===b));};s.appendChild(b);});\n\n  const br=$(\'#n_symBristol\');\n  [\'1\',\'2\',\'3\',\'4\',\'5\',\'6\',\'7\'].forEach(v=>{const b=document.createElement(\'div\');b.className=\'chip num\';b.textContent=v;\n    b.onclick=()=>{nSym.bristol=(nSym.bristol===v?null:v);\n      [...br.children].forEach(c=>c.classList.toggle(\'sel\',c.textContent===nSym.bristol));};br.appendChild(b);});\n\n  $(\'#n_bpSave\').onclick=async()=>{\n    const sys=$(\'#n_sys\').value,dia=$(\'#n_dia\').value,pulse=$(\'#n_pulse\').value;\n    if(!sys&&!dia&&!pulse){toast(\'Nothing to save\');return;}\n    try{await post(\'/api/vitals\',{day:todayISO,vtime:nowHM(),sys:sys,dia:dia,pulse:pulse});\n      $(\'#n_bpLast\').textContent=\'Saved \'+(sys||\'-\')+\'/\'+(dia||\'-\')+(pulse?\', pulse \'+pulse:\'\')+\' at \'+nowHM();\n      $(\'#n_sys\').value=\'\';$(\'#n_dia\').value=\'\';$(\'#n_pulse\').value=\'\';toast(\'Reading saved\');}\n    catch(err){toast(err.message);}\n  };\n\n  /* one episode per symptom chosen, sharing time, severity and Bristol --\n     two symptoms at once are two findings, not one blended row */\n  $(\'#n_symSave\').onclick=async()=>{\n    if(!nSym.types.length){toast(\'Pick a symptom\');return;}\n    const t=nowHM();\n    try{\n      for(const ty of nSym.types){\n        await post(\'/api/episodes\',{day:todayISO,etime:t,category:\'GI\',etype:ty,\n          severity:nSym.sev,bristol:nSym.bristol});\n      }\n      toast(nSym.types.length>1?(nSym.types.length+\' episodes saved\'):\'Episode saved\');\n      nSym={types:[],sev:null,bristol:null};\n      $$(\'#n_symType .chip,#n_symSev .chip,#n_symBristol .chip\').forEach(c=>c.classList.remove(\'sel\'));\n    }catch(err){toast(err.message);}\n  };\n\n  $(\'#nowAddMed\').onclick=async()=>{\n    const n=prompt(\'Medicine name (include strength)\');if(!n)return;\n    try{await post(\'/api/prnmeds\',{name:n});toast(\'Added\');loadNow();loadSchedMeds();}\n    catch(err){toast(err.message);}\n  };\n  const sa=$(\'#nowShowAll\');\n  if(sa)sa.onclick=()=>{showAllMeds=!showAllMeds;loadNow();};\n}\n\n'

def build_edits():
    E = []

    a = ('SCHEMA_VERSION = "3.3.2"   # GUTLOG_V330_PHASE_A '
         'GUTLOG_V332_VARIANTS GUTLOG_V333_ROWACT')
    E.append(("version marker", a, a + " " + MARKER))

    # extras exclude scheduled medicines; full list offered separately
    a = ('    meds = [dict(r) for r in db().execute(\n'
         '        "SELECT id, name, sort FROM prnmeds WHERE active=1 "\n'
         '        "ORDER BY sort, id").fetchall()]')
    new = ('    # An expected dose belongs on the dose card. Listing a\n'
           '    # scheduled medicine here too invites logging it twice, or in\n'
           '    # the wrong place. meds_all backs the "Show all" button for\n'
           '    # the rare unplanned dose of a regular medicine.\n'
           '    meds = [dict(r) for r in db().execute(\n'
           '        "SELECT id, name, sort FROM prnmeds WHERE active=1 "\n'
           '        "AND COALESCE(scheduled,0)=0 ORDER BY sort, id").fetchall()]\n'
           '    meds_all = [dict(r) for r in db().execute(\n'
           '        "SELECT id, name, sort FROM prnmeds WHERE active=1 "\n'
           '        "ORDER BY sort, id").fetchall()]')
    E.append(("extras exclude scheduled meds", a, new))

    a = ('    return jsonify(day=day, slots=slots, extras=extras, meds=meds,\n'
         '                   has_schedule=bool(rows))')
    E.append(("/api/now returns meds_all",
              a,
              '    return jsonify(day=day, slots=slots, extras=extras, '
              'meds=meds,\n'
              '                   meds_all=meds_all, has_schedule=bool(rows))'))

    # BUG, found while checking this patch: the episodes endpoint never
    # wrote bristol. The column was added in v3.3.0 and the UI has offered
    # the 1-7 chips since, but the value was dropped on the floor every
    # time. Collecting something and discarding it is worse than not
    # asking, so this is fixed here.
    a = ('    insert("episodes", ["day","etime","category","etype","side",'
         '"severity","duration","notes"],\n'
         '           [d.get("day") or today(), d.get("etime") or now_hm(),\n'
         '            d.get("category"), d["etype"], d.get("side"), '
         'd.get("severity"),\n'
         '            d.get("duration"), note(d)])')
    new = ('    insert("episodes", ["day","etime","category","etype","side",'
           '"severity","duration","notes","bristol"],\n'
           '           [d.get("day") or today(), d.get("etime") or now_hm(),\n'
           '            d.get("category"), d["etype"], d.get("side"), '
           'd.get("severity"),\n'
           '            d.get("duration"), note(d), '
           '(d.get("bristol") or "")[:4]])')
    E.append(("episodes endpoint stores bristol", a, new))

    # markup
    E.append(("Now tab markup", OLD_TAB, NEW_TAB))

    # css
    a = "@media (max-width:430px){\n  main{padding:10px 10px 0}"
    E.append(("readability CSS",
              a, CSS_ADD + "\n" + a))

    # js
    E.append(("Now tab script", OLD_JS, NEW_JS))

    return E


def verify(src, edits):
    out = []
    for label, anchor, _new in edits:
        n = src.count(anchor)
        if n != 1:
            out.append("  " + label + ": anchor found " + str(n)
                       + " times (need exactly 1)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("GutLog readability patch -> v3.4.0")
    print("file : " + args.file)
    print("=" * 60)

    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1

    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if "GUTLOG_V333_ROWACT" not in src:
        print("FATAL: this file is not at v3.3.3. Apply that first.")
        return 1

    edits = build_edits()
    problems = verify(src, edits)
    print("anchors: " + str(len(edits) - len(problems)) + "/"
          + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)

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
    bak = args.file + ".bak-v340-" + stamp
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

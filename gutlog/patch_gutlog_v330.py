#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.2.0 -> v3.3.0  ::  Phase A patcher

Adds the quick-log surface:
  - med_schedule table (effective-dated regimen)
  - status-bearing dose events, event-level Bristol
  - /api/now, /api/now/dose, /api/now/undo, /api/schedule
  - a NOW tab (default landing) with dose card, extras, BP, symptom
  - a Schedule segment under the Meds tab
  - PWA registration (manifest, service worker, icons) via pwa.py

PATTERN
  Anchor-verified in-place patch. Every edit locates an exact, unique
  anchor string; if any anchor is missing or ambiguous the patcher
  aborts before writing a single byte. Idempotent: re-running detects
  the v3.3.0 marker and exits clean.

  Writes app.py.bak-v330-<stamp> before touching anything, and
  compile-checks the result. A failed compile restores the backup
  automatically.

USAGE
  python3 patch_gutlog_v330.py --check      # verify anchors only
  python3 patch_gutlog_v330.py --dry-run    # report, write nothing
  python3 patch_gutlog_v330.py              # apply

Python 3.9.25 compatible. No PEP 701 f-strings anywhere, in this file
or in the injected code.
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V330_PHASE_A"


# ======================================================================
# Injected fragments
# ======================================================================

SCHEMA_ADD = '''CREATE TABLE IF NOT EXISTS med_schedule (
  id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER NOT NULL, slot TEXT NOT NULL,
  dose_text TEXT DEFAULT '', with_food TEXT DEFAULT 'ANY', valid_from TEXT NOT NULL,
  valid_to TEXT DEFAULT '', epoch INTEGER DEFAULT 1, notes TEXT DEFAULT '', created TEXT);
'''

MIGRATE_NEW = '''SCHEMA_VERSION = "3.3.0"   # ''' + MARKER + '''

# slot -> (label, default clock time). Times are display hints only; the
# schedule is not time-enforced.
SLOTS = [("MORNING", "Morning", "08:00"), ("NOON", "Noon", "14:00"),
         ("EVENING", "Evening", "20:00"), ("NIGHT", "Night", "22:30")]

_V330_COLS = [
    ("prnmeds", "molecule", "TEXT DEFAULT ''"),
    ("prnmeds", "form", "TEXT DEFAULT ''"),
    ("prnmeds", "pack_size", "INTEGER DEFAULT 0"),
    ("prnmeds", "stock", "REAL DEFAULT 0"),
    ("prnmeds", "active", "INTEGER DEFAULT 1"),
    ("prnmeds", "scheduled", "INTEGER DEFAULT 0"),
    ("doses", "status", "TEXT DEFAULT 'TAKEN'"),
    ("doses", "med_id", "INTEGER"),
    ("doses", "sched_id", "INTEGER"),
    ("doses", "dose_text", "TEXT DEFAULT ''"),
    ("episodes", "bristol", "TEXT DEFAULT ''"),
]

_V330_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sched_med ON med_schedule (med_id)",
    "CREATE INDEX IF NOT EXISTS idx_sched_open ON med_schedule (valid_to)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_one_open "
    "ON med_schedule (med_id, slot) WHERE valid_to = ''",
    "CREATE INDEX IF NOT EXISTS idx_doses_day ON doses (day)",
    "CREATE INDEX IF NOT EXISTS idx_doses_sched ON doses (day, sched_id)",
]

_V330_SETTINGS = [
    ("slot_window_min", "120"),
    ("bp_flag_sys", "140"),
    ("bp_flag_dia", "90"),
    ("med_epoch", "1"),
]


def _migrate(con):
    # Fast path: one SELECT per request once the schema is current.
    # Cheaper than the PRAGMA sweep this replaces.
    row = con.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()
    if row and row[0] == SCHEMA_VERSION:
        return

    # -- pre-v3.3.0: vitals.temp (kept from the original _migrate) -----
    cols = [r[1] for r in con.execute("PRAGMA table_info(vitals)").fetchall()]
    if cols and "temp" not in cols:
        con.execute("ALTER TABLE vitals ADD COLUMN temp REAL")

    # -- v3.3.0 columns: one PRAGMA per table, not per column ----------
    seen = {}
    for table, col, decl in _V330_COLS:
        if table not in seen:
            seen[table] = set(
                r[1] for r in con.execute(
                    "PRAGMA table_info(" + table + ")").fetchall())
        if not seen[table]:
            continue          # table absent; SCHEMA will create it
        if col not in seen[table]:
            con.execute("ALTER TABLE " + table + " ADD COLUMN "
                        + col + " " + decl)
            seen[table].add(col)

    for stmt in _V330_INDEXES:
        con.execute(stmt)

    # -- normalise legacy free-text dose rows to prnmeds ---------------
    con.execute("UPDATE doses SET status='TAKEN' "
                "WHERE status IS NULL OR status=''")
    con.execute("UPDATE doses SET med_id=("
                "SELECT p.id FROM prnmeds p WHERE p.name=doses.medicine) "
                "WHERE med_id IS NULL")

    for key, val in _V330_SETTINGS:
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (key, val))
    con.execute("INSERT OR REPLACE INTO settings(key,value) "
                "VALUES('schema_version',?)", (SCHEMA_VERSION,))
    con.commit()
'''

API_ADD = '''# ------------------------------------------------------------ now / schedule
def _slot_meta():
    return [{"slot": s, "label": lb, "time": tm} for s, lb, tm in SLOTS]


def _bump_epoch():
    cur = setting("med_epoch") or "1"
    try:
        nxt = str(int(cur) + 1)
    except ValueError:
        nxt = "1"
    set_setting("med_epoch", nxt)
    return nxt


def _refresh_scheduled_flags():
    db().execute("UPDATE prnmeds SET scheduled=0")
    db().execute("UPDATE prnmeds SET scheduled=1 WHERE id IN "
                 "(SELECT DISTINCT med_id FROM med_schedule WHERE valid_to='')")
    db().commit()


@app.route("/api/now")
@login_required
def api_now():
    """Expected doses for a day, left-joined to what was actually logged.

    Nothing is materialised in advance: expected rows are computed from
    the effective-dated schedule at read time. No nightly job to fail
    silently, and no phantom 'missed' rows for days the app never opened.
    """
    day = request.args.get("day") or today()
    rows = db().execute(
        "SELECT s.id AS sched_id, s.med_id, s.slot, s.dose_text, s.with_food, "
        "       p.name AS name, "
        "       d.id AS dose_id, d.status AS status, d.dtime AS dtime "
        "FROM med_schedule s "
        "JOIN prnmeds p ON p.id = s.med_id "
        "LEFT JOIN doses d ON d.sched_id = s.id AND d.day = ? "
        "WHERE s.valid_from <= ? AND (s.valid_to = '' OR s.valid_to >= ?) "
        "ORDER BY s.slot, p.sort, p.id", (day, day, day)).fetchall()

    by_slot = {}
    for r in rows:
        by_slot.setdefault(r["slot"], []).append(dict(r))

    slots = []
    for meta in _slot_meta():
        items = by_slot.get(meta["slot"], [])
        if not items:
            continue
        done = len([i for i in items if i["status"] in ("TAKEN", "SKIPPED")])
        meta = dict(meta)
        meta["rows"] = items
        meta["done"] = done
        meta["total"] = len(items)
        slots.append(meta)

    extras = [dict(r) for r in db().execute(
        "SELECT d.id, d.medicine, d.dtime, d.status, d.reason "
        "FROM doses d WHERE d.day=? AND d.sched_id IS NULL "
        "ORDER BY d.dtime", (day,)).fetchall()]

    meds = [dict(r) for r in db().execute(
        "SELECT id, name, sort FROM prnmeds WHERE active=1 "
        "ORDER BY sort, id").fetchall()]

    return jsonify(day=day, slots=slots, extras=extras, meds=meds,
                   has_schedule=bool(rows))


@app.route("/api/now/dose", methods=["POST"])
@login_required
def api_now_dose():
    d = J()
    status = (d.get("status") or "TAKEN").upper()
    if status not in ("TAKEN", "SKIPPED", "EXTRA"):
        return jsonify(ok=False, err="Bad status."), 400

    med_id = d.get("med_id")
    name = (d.get("medicine") or "").strip()[:80]
    if med_id:
        r = db().execute("SELECT name FROM prnmeds WHERE id=?",
                         (med_id,)).fetchone()
        if not r:
            return jsonify(ok=False, err="Unknown medicine."), 400
        name = r["name"]
    if not name:
        return jsonify(ok=False, err="No medicine."), 400

    day = d.get("day") or today()
    dtime = d.get("dtime") or now_hm()
    sched_id = d.get("sched_id")

    # Re-tapping a scheduled row corrects it rather than duplicating.
    if sched_id:
        ex = db().execute("SELECT id FROM doses WHERE sched_id=? AND day=?",
                          (sched_id, day)).fetchone()
        if ex:
            db().execute(
                "UPDATE doses SET status=?, dtime=?, reason=?, medicine=?, "
                "med_id=?, dose_text=? WHERE id=?",
                (status, dtime, d.get("reason"), name, med_id,
                 (d.get("dose_text") or "")[:40], ex["id"]))
            db().commit()
            return jsonify(ok=True, id=ex["id"], updated=True)

    db().execute(
        "INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,"
        "status,med_id,sched_id,dose_text) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (day, dtime, name, d.get("reason"), None, note(d), now_s(),
         status, med_id, sched_id, (d.get("dose_text") or "")[:40]))
    db().commit()
    rid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    return jsonify(ok=True, id=rid)


@app.route("/api/now/undo/<int:did>", methods=["POST"])
@login_required
def api_now_undo(did):
    """Mistap recovery. Deletes one dose row."""
    db().execute("DELETE FROM doses WHERE id=?", (did,))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/schedule")
@login_required
def api_schedule():
    rows = db().execute(
        "SELECT s.*, p.name AS name FROM med_schedule s "
        "JOIN prnmeds p ON p.id=s.med_id "
        "WHERE s.valid_to='' ORDER BY s.slot, p.sort, p.id").fetchall()
    return jsonify(slots=_slot_meta(), rows=[dict(r) for r in rows])


@app.route("/api/schedule", methods=["POST"])
@login_required
def api_schedule_post():
    """Add or change one regimen line.

    Effective-dated: a change closes the open row and opens a new one, so
    history stays interpretable. Rows are never edited in place.
    """
    d = J()
    med_id = d.get("med_id")
    slot = (d.get("slot") or "").upper()
    if not med_id or slot not in [s for s, _, _ in SLOTS]:
        return jsonify(ok=False, err="Pick a medicine and a slot."), 400
    if not db().execute("SELECT 1 FROM prnmeds WHERE id=?",
                        (med_id,)).fetchone():
        return jsonify(ok=False, err="Unknown medicine."), 400

    day = d.get("valid_from") or today()
    yday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()

    open_row = db().execute(
        "SELECT id FROM med_schedule WHERE med_id=? AND slot=? AND valid_to=''",
        (med_id, slot)).fetchone()
    if open_row:
        db().execute("UPDATE med_schedule SET valid_to=? WHERE id=?",
                     (yday, open_row["id"]))

    epoch = _bump_epoch()
    db().execute(
        "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,"
        "valid_to,epoch,notes,created) VALUES(?,?,?,?,?,'',?,?,?)",
        (med_id, slot, (d.get("dose_text") or "")[:40],
         (d.get("with_food") or "ANY")[:10], day, int(epoch), note(d), now_s()))
    db().commit()
    _refresh_scheduled_flags()
    return jsonify(ok=True, epoch=epoch)


@app.route("/api/schedule/close/<int:sid>", methods=["POST"])
@login_required
def api_schedule_close(sid):
    """Stop a medicine. Closes the row; never deletes it."""
    d = J()
    day = d.get("valid_to") or today()
    db().execute("UPDATE med_schedule SET valid_to=? WHERE id=? AND valid_to=''",
                 (day, sid))
    db().commit()
    _bump_epoch()
    _refresh_scheduled_flags()
    return jsonify(ok=True)


'''

PWA_REG = '''
# ------------------------------------------------------------------ pwa
# Manifest + service worker + icons live in pwa.py so this file's page
# template stays untouched. Guarded: a missing pwa.py must not take the
# app down, it only costs installability.
try:
    from pwa import pwa_bp, PWA_HEAD_SNIPPET
    app.register_blueprint(pwa_bp)
except Exception as _pwa_err:          # pragma: no cover
    PWA_HEAD_SNIPPET = ""
    import sys as _sys
    print("pwa layer unavailable: " + str(_pwa_err), file=_sys.stderr)
'''

HEAD_ADD = '""" + PWA_HEAD_SNIPPET + r"""\n'

NAV_ADD = '''  <button data-t="now" class="sel"><i>&#9889;</i>Now</button>\n'''

TAB_NOW = r'''<!-- ============ NOW ============ -->
<section class="tab sel" id="tab-now">
  <div id="nowSched"></div>

  <div class="card" id="nowExtraCard">
    <p class="q">&#10133; Extra dose</p>
    <div class="chips" id="nowExtras"></div>
    <button type="button" class="addbtn" id="nowAddMed" style="margin-top:9px">&#10133; Add medicine</button>
    <p class="hint" style="margin:8px 0 0">One tap logs it at the current time.</p>
  </div>

  <div class="card" id="nowBP">
    <p class="q">&#129656; Blood pressure</p>
    <div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="—"></div>
      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="—"></div>
      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="—"></div>
    </div>
    <button type="button" class="addbtn" id="n_bpSave" style="margin-top:10px">Save reading</button>
    <p class="hint" id="n_bpLast" style="margin:8px 0 0"></p>
  </div>

  <div class="card" id="nowSym">
    <p class="q">&#129504; Symptom now</p>
    <div class="chips" id="n_symType"></div>
    <p class="lbl" style="margin-top:10px">Severity</p>
    <div class="chips" id="n_symSev"></div>
    <p class="lbl" style="margin-top:10px">Bristol (optional)</p>
    <div class="chips" id="n_symBristol"></div>
    <button type="button" class="addbtn" id="n_symSave" style="margin-top:10px">Save episode</button>
  </div>
</section>

'''

SCHED_SEG_BTN = '''<button data-s="sched">Schedule</button>'''

SCHED_SUB = r'''
  <div class="sub" id="meds-sched">
    <p class="hint">Your regular regimen. Changing a line closes the old one and opens a new one on today's date, so past logs stay readable.</p>
    <div id="schedList"></div>
    <div class="card"><p class="q">Add a regular medicine</p>
      <p class="lbl">Medicine</p><select id="sc_med"></select>
      <p class="lbl" style="margin-top:10px">Slot</p>
      <div class="chips" id="sc_slot"></div>
      <div class="row2" style="margin-top:10px">
        <div><p class="lbl">Dose</p><input type="text" id="sc_dose" maxlength="40" placeholder="1 tab"></div>
        <div><p class="lbl">Food</p><select id="sc_food">
          <option value="ANY">Any</option><option value="BEFORE">Before</option><option value="AFTER">After</option>
        </select></div>
      </div>
      <button type="button" class="addbtn" id="sc_add" style="margin-top:10px">Add to regimen</button>
    </div>
  </div>
'''

CSS_ADD = r'''.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.doserow{display:flex;align-items:center;gap:10px;padding:11px 12px;border:1.5px solid var(--line);
background:#fff;border-radius:13px;margin:0 0 8px;cursor:pointer;transition:transform .06s}
.doserow:active{transform:scale(.985)}
.doserow .nm{flex:1;min-width:0}
.doserow .nm b{display:block;font-size:15.5px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.doserow .nm span{font-size:12px;color:var(--muted)}
.doserow .tick{width:30px;height:30px;border-radius:50%;border:2px solid var(--line);display:grid;
place-items:center;font-size:15px;color:transparent;flex:0 0 auto}
.doserow.done{background:#F2F8F5;border-color:#BEDCCB}
.doserow.done .tick{background:var(--ok);border-color:var(--ok);color:#fff}
.doserow.skip{background:#FBF1DC;border-color:#EAD3A0}
.doserow.skip .tick{background:var(--amber);border-color:var(--amber);color:#fff;font-size:13px}
.doserow .sk{border:0;background:none;color:var(--muted);font-size:12px;padding:6px 4px;
text-decoration:underline;cursor:pointer;flex:0 0 auto}
.slothd{display:flex;align-items:center;gap:8px;margin:0 0 8px}
.slothd .q{margin:0}
.slothd .cnt{margin-left:auto;font-size:12px;color:var(--muted);font-weight:700}
.exrow{display:flex;gap:8px;align-items:center;font-size:13.5px;padding:5px 0;border-bottom:1px dashed var(--line)}
.exrow:last-child{border-bottom:0}
.exrow .t{color:var(--muted);font-size:12px;flex:0 0 auto}
.exrow .u{margin-left:auto;border:0;background:none;color:var(--err);font-size:12px;cursor:pointer;text-decoration:underline}
.schrow{display:flex;gap:8px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line);font-size:14.5px}
.schrow:last-child{border-bottom:0}
.schrow .s{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;flex:0 0 62px}
.schrow .x{margin-left:auto;border:0;background:none;color:var(--err);font-size:12px;cursor:pointer;text-decoration:underline}
'''

JS_ADD = r'''
/* ---------- NOW tab ---------- */
const SEVS=['1','2','3','4','5','6','7','8','9','10'];
const SYMTYPES=['Abdominal pain','Cramp','Bloating','Urgency','Loose stool','Constipation','Nausea','Reflux','Other'];
let nowData=null, nSym={type:null,sev:null,bristol:null};

function nowRow(r){
  const st=r.status||'';
  const cls=st==='TAKEN'?'done':(st==='SKIPPED'?'skip':'');
  const mark=st==='TAKEN'?'&#10003;':(st==='SKIPPED'?'&#8212;':'');
  const sub=[r.dose_text||'',r.with_food&&r.with_food!=='ANY'?r.with_food.toLowerCase()+' food':'',
             st?(st==='TAKEN'?'taken '+(r.dtime||''):'skipped'):''].filter(Boolean).join(' \u00b7 ');
  const d=document.createElement('div');
  d.className='doserow '+cls;
  d.innerHTML='<div class="tick">'+mark+'</div><div class="nm"><b></b><span></span></div>'+
              (st?'<button type="button" class="sk">undo</button>':'<button type="button" class="sk">skip</button>');
  d.querySelector('.nm b').textContent=r.name;
  d.querySelector('.nm span').textContent=sub;
  d.onclick=async e=>{
    if(e.target.classList.contains('sk'))return;
    if(st==='TAKEN')return;
    try{await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,status:'TAKEN',
      day:nowData.day,dose_text:r.dose_text});toast('Logged '+r.name);loadNow();}
    catch(err){toast(err.message);}
  };
  d.querySelector('.sk').onclick=async ev=>{
    ev.stopPropagation();
    try{
      if(st&&r.dose_id){await post('/api/now/undo/'+r.dose_id,{});toast('Undone');}
      else{await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,status:'SKIPPED',
        day:nowData.day,dose_text:r.dose_text});toast('Marked skipped');}
      loadNow();
    }catch(err){toast(err.message);}
  };
  return d;
}

async function loadNow(){
  nowData=await jget('/api/now?day='+todayISO);
  const box=$('#nowSched');box.innerHTML='';
  if(!nowData.has_schedule){
    const c=document.createElement('div');c.className='card';
    c.innerHTML='<p class="q">&#128138; Today\u2019s doses</p><p class="hint" style="margin:0">'+
      'No regular medicines set up yet. Add them under '+
      '<b>Meds &rarr; Schedule</b> and they\u2019ll appear here as one-tap rows.</p>';
    box.appendChild(c);
  }else{
    nowData.slots.forEach(s=>{
      const c=document.createElement('div');c.className='card';
      const hd=document.createElement('div');hd.className='slothd';
      hd.innerHTML='<p class="q">'+s.label+'</p><span class="cnt">'+s.done+'/'+s.total+'</span>';
      c.appendChild(hd);
      s.rows.forEach(r=>c.appendChild(nowRow(r)));
      box.appendChild(c);
    });
  }
  /* extras */
  const ex=$('#nowExtras');ex.innerHTML='';
  (nowData.meds||[]).forEach(m=>{
    const b=document.createElement('div');b.className='chip';b.textContent=m.name;
    b.onclick=async()=>{try{await post('/api/now/dose',{med_id:m.id,status:'EXTRA',day:nowData.day});
      toast('Logged '+m.name);loadNow();}catch(err){toast(err.message);}};
    ex.appendChild(b);
  });
  let list=$('#nowExtraList');
  if(!list){list=document.createElement('div');list.id='nowExtraList';list.style.marginTop='10px';
    $('#nowExtraCard').appendChild(list);}
  list.innerHTML='';
  (nowData.extras||[]).forEach(e=>{
    const row=document.createElement('div');row.className='exrow';
    row.innerHTML='<span class="t"></span><span class="m"></span><button type="button" class="u">undo</button>';
    row.querySelector('.t').textContent=e.dtime||'';
    row.querySelector('.m').textContent=e.medicine;
    row.querySelector('.u').onclick=async()=>{await post('/api/now/undo/'+e.id,{});toast('Removed');loadNow();};
    list.appendChild(row);
  });
}

function buildNowStatics(){
  const t=$('#n_symType');
  SYMTYPES.forEach(v=>{const b=document.createElement('div');b.className='chip';b.textContent=v;
    b.onclick=()=>{nSym.type=v;[...t.children].forEach(c=>c.classList.toggle('sel',c===b));};t.appendChild(b);});
  const s=$('#n_symSev');
  SEVS.forEach(v=>{const b=document.createElement('div');b.className='chip';b.textContent=v;
    b.onclick=()=>{nSym.sev=v;[...s.children].forEach(c=>c.classList.toggle('sel',c===b));};s.appendChild(b);});
  const br=$('#n_symBristol');
  ['1','2','3','4','5','6','7'].forEach(v=>{const b=document.createElement('div');b.className='chip';b.textContent=v;
    b.onclick=()=>{nSym.bristol=(nSym.bristol===v?null:v);
      [...br.children].forEach(c=>c.classList.toggle('sel',c.textContent===nSym.bristol));};br.appendChild(b);});

  $('#n_bpSave').onclick=async()=>{
    const sys=$('#n_sys').value,dia=$('#n_dia').value,pulse=$('#n_pulse').value;
    if(!sys&&!dia&&!pulse){toast('Nothing to save');return;}
    try{await post('/api/vitals',{day:todayISO,vtime:nowHM(),sys:sys,dia:dia,pulse:pulse});
      $('#n_bpLast').textContent='Saved '+(sys||'-')+'/'+(dia||'-')+(pulse?', pulse '+pulse:'')+' at '+nowHM();
      $('#n_sys').value='';$('#n_dia').value='';$('#n_pulse').value='';toast('Reading saved');}
    catch(err){toast(err.message);}
  };
  $('#n_symSave').onclick=async()=>{
    if(!nSym.type){toast('Pick a symptom');return;}
    try{await post('/api/episodes',{day:todayISO,etime:nowHM(),category:'GI',etype:nSym.type,
      severity:nSym.sev,bristol:nSym.bristol});
      toast('Episode saved');nSym={type:null,sev:null,bristol:null};
      $$('#n_symType .chip,#n_symSev .chip,#n_symBristol .chip').forEach(c=>c.classList.remove('sel'));}
    catch(err){toast(err.message);}
  };
  $('#nowAddMed').onclick=async()=>{
    const n=prompt('Medicine name (include strength)');if(!n)return;
    try{await post('/api/prnmeds',{name:n});toast('Added');loadNow();loadSchedMeds();}
    catch(err){toast(err.message);}
  };
}

/* ---------- Schedule editor ---------- */
let schedSlot=null;
async function loadSchedMeds(){
  const meds=await jget('/api/prnmeds/full');
  const sel=$('#sc_med');if(!sel)return;
  sel.innerHTML='';
  meds.forEach(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=m.name;sel.appendChild(o);});
}
async function loadSchedule(){
  const d=await jget('/api/schedule');
  const sl=$('#sc_slot');
  if(sl&&!sl.dataset.built){sl.dataset.built=1;
    d.slots.forEach(s=>{const b=document.createElement('div');b.className='chip';b.textContent=s.label;
      b.onclick=()=>{schedSlot=s.slot;[...sl.children].forEach(c=>c.classList.toggle('sel',c===b));};
      sl.appendChild(b);});}
  const box=$('#schedList');box.innerHTML='';
  if(!d.rows.length){
    box.innerHTML='<div class="card"><p class="hint" style="margin:0">No regular medicines yet.</p></div>';
    return;
  }
  const c=document.createElement('div');c.className='card';
  c.innerHTML='<p class="q">Current regimen</p>';
  d.rows.forEach(r=>{
    const row=document.createElement('div');row.className='schrow';
    row.innerHTML='<span class="s"></span><span class="n"></span><button type="button" class="x">stop</button>';
    row.querySelector('.s').textContent=r.slot.slice(0,3);
    row.querySelector('.n').textContent=r.name+(r.dose_text?' \u00b7 '+r.dose_text:'');
    row.querySelector('.x').onclick=async()=>{
      if(!confirm('Stop '+r.name+' ('+r.slot.toLowerCase()+')? Past logs are kept.'))return;
      await post('/api/schedule/close/'+r.id,{});toast('Stopped');loadSchedule();loadNow();};
    c.appendChild(row);
  });
  box.appendChild(c);
}
function bindSchedule(){
  const add=$('#sc_add');if(!add)return;
  add.onclick=async()=>{
    if(!schedSlot){toast('Pick a slot');return;}
    try{await post('/api/schedule',{med_id:parseInt($('#sc_med').value,10),slot:schedSlot,
      dose_text:$('#sc_dose').value,with_food:$('#sc_food').value});
      toast('Added to regimen');$('#sc_dose').value='';loadSchedule();loadNow();}
    catch(err){toast(err.message);}
  };
}

/* deep links from the home-screen shortcuts */
function nowDeepLink(){
  const p=new URLSearchParams(location.search).get('open');
  if(!p)return;
  switchTab('now');
  const map={bp:'#nowBP',sym:'#nowSym',meds:'#nowSched'};
  const el=$(map[p]||'#nowSched');
  if(el)setTimeout(()=>el.scrollIntoView({behavior:'smooth',block:'start'}),120);
}
'''


# ======================================================================
# Edits: (label, anchor, replacement-builder)
# ======================================================================

def build_edits():
    """Each edit is (label, anchor, new_text). Anchor must occur once."""
    E = []

    # 1. SCHEMA: add med_schedule
    a = ('CREATE TABLE IF NOT EXISTS files (\n'
         '  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ftype TEXT, label TEXT,\n'
         '  stored TEXT, orig TEXT, size INTEGER, created TEXT);\n"""')
    E.append(("SCHEMA += med_schedule",
              a,
              a.replace('created TEXT);\n"""',
                        'created TEXT);\n' + SCHEMA_ADD + '"""')))

    # 2. _migrate: replace wholesale
    a = ('def _migrate(con):\n'
         '    # idempotent column adds for DBs created by an earlier v3 build\n'
         '    cols = [r[1] for r in con.execute("PRAGMA table_info(vitals)").fetchall()]\n'
         '    if cols and "temp" not in cols:\n'
         '        con.execute("ALTER TABLE vitals ADD COLUMN temp REAL")\n'
         '        con.commit()')
    E.append(("_migrate -> v3.3.0", a, MIGRATE_NEW.rstrip("\n")))

    # 3. new API block before the patch section
    a = "# ------------------------------------------------------------------ patch"
    E.append(("API: /api/now, /api/schedule", a, API_ADD + a))

    # 4. prnmeds full listing (id + name) for the schedule editor
    a = ('    return jsonify([r["name"] for r in db().execute('
         '"SELECT name FROM prnmeds ORDER BY sort, id")])')
    E.append(("API: /api/prnmeds/full", a, a + '''


@app.route("/api/prnmeds/full")
@login_required
def api_prnmeds_full():
    return jsonify([dict(r) for r in db().execute(
        "SELECT id, name, sort, active FROM prnmeds WHERE active=1 "
        "ORDER BY sort, id")])'''))

    # 5. PWA blueprint registration
    a = '_failed = {"count": 0, "until": 0.0}'
    E.append(("register pwa blueprint", a, a + "\n" + PWA_REG))

    # 6. inject PWA head into APP_PAGE (r-string, so close/reopen it)
    a = ('<meta name="theme-color" content="#0B6E6E">'
         '<meta name="apple-mobile-web-app-capable" content="yes">\n'
         '<title>GutLog</title>')
    E.append(("APP_PAGE head: manifest + sw",
              a, a + "\n" + HEAD_ADD))

    # 7. CSS
    a = ".tab{display:none}.tab.sel{display:block}"
    E.append(("CSS for now tab", a, a + "\n" + CSS_ADD.rstrip("\n")))

    # 8. nav: add Now first, drop sel from Log
    a = ('<nav id="nav">\n'
         '  <button data-t="log" class="sel"><i>&#9998;</i>Log</button>')
    E.append(("nav: Now tab first",
              a,
              '<nav id="nav">\n' + NAV_ADD +
              '  <button data-t="log"><i>&#9998;</i>Log</button>'))

    # 9. tab-now section before tab-log; tab-log loses default
    a = '<section class="tab sel" id="tab-log">'
    E.append(("insert tab-now section",
              a, TAB_NOW + '<section class="tab" id="tab-log">'))

    # 10. Meds tab: Schedule segment button
    a = ('<button data-s="prn" class="sel">PRN dose</button>'
         '<button data-s="course">Courses</button>')
    E.append(("Meds: Schedule segment button", a, a + SCHED_SEG_BTN))

    # 11. Meds tab: schedule sub-panel
    a = ('  <div class="sub" id="meds-course">')
    E.append(("Meds: schedule sub-panel", a, SCHED_SUB + "\n" + a))

    # 12. default tab -> now
    a = ("let tab='log'; const seg={log:'day',meals:'meal',"
         "meds:'prn',files:'vault'};")
    E.append(("default tab = now",
              a,
              "let tab='now'; const seg={log:'day',meals:'meal',"
              "meds:'prn',files:'vault'};"))

    # 13. JS block
    a = "/* ---------- boot ---------- */"
    E.append(("JS: now + schedule", a, JS_ADD + "\n" + a))

    # 14. switchTab hooks
    a = "  if(t==='meds'){loadPRNToday();loadPatch();loadCourses();}"
    E.append(("switchTab hooks",
              a,
              "  if(t==='now')loadNow();\n" + a +
              "\n  if(t==='meds'&&seg.meds==='sched'){loadSchedMeds();loadSchedule();}"))

    # 15. setSeg hook so the Schedule panel loads on first open
    a = ("function setSeg(section,s){\n  seg[section]=s;")
    E.append(("setSeg hook for schedule",
              a,
              a + "\n  if(section==='meds'&&s==='sched'){loadSchedMeds();loadSchedule();}"))

    # 16. boot
    a = ("(async function(){\n  initDates();\n  await loadLib();")
    E.append(("boot: now tab",
              a,
              "(async function(){\n  initDates();\n  buildNowStatics();\n"
              "  bindSchedule();\n  await loadLib();"))

    a = ("  renderTestFoods();loadRegistry();\n  saveBtnVisible();")
    E.append(("boot: load now + deep link",
              a,
              "  renderTestFoods();loadRegistry();\n  await loadNow();\n"
              "  saveBtnVisible();\n  nowDeepLink();"))

    # 17. save button label map (Now tab has no global save)
    a = ("  $('#saveBtn').textContent=L[tab+':'+seg[tab]]||'Save';")
    E.append(("hide global save on Now",
              a,
              "  const sb=$('#saveBtn');\n"
              "  if(tab==='now'){sb.style.display='none';return;}\n"
              "  sb.style.display='';\n" + a))

    return E


# ======================================================================
# Engine
# ======================================================================

def verify(src, edits):
    problems = []
    for label, anchor, _new in edits:
        n = src.count(anchor)
        if n != 1:
            problems.append("  " + label + ": anchor found " + str(n)
                            + " times (need exactly 1)")
    return problems


def apply_edits(src, edits):
    for label, anchor, new in edits:
        if src.count(anchor) != 1:
            raise RuntimeError("anchor lost mid-patch: " + label)
        src = src.replace(anchor, new, 1)
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true",
                    help="verify anchors only")
    ap.add_argument("--dry-run", action="store_true",
                    help="verify and compile-test, write nothing")
    args = ap.parse_args()

    print("=" * 62)
    print("GutLog Phase A patcher -> v3.3.0")
    print("file : " + args.file)
    print("=" * 62)

    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1

    src = open(args.file, "r", encoding="utf-8").read()

    if MARKER in src:
        print("Already patched (marker " + MARKER + " present). Nothing to do.")
        return 0

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

    out = apply_edits(src, edits)

    # compile-check the result before it ever reaches disk
    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "app_candidate.py")
    with open(tmpf, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:")
        print(str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)

    if args.dry_run:
        print("DRY RUN: " + str(len(edits))
              + " edits verified and compile-clean. Nothing written.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v330-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)

    with open(args.file, "w", encoding="utf-8") as f:
        f.write(out)

    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.")
        print(str(exc))
        return 2

    print("applied: " + str(len(edits)) + " edits")
    print("-" * 62)
    print("Next:")
    print("  1. ensure pwa.py, icon-192.png, icon-512.png are in the same dir")
    print("  2. python3 test_migration_v330.py")
    print("  3. systemctl restart gutlog")
    print("  4. rollback if needed:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.3.1 -> v3.3.2  ::  dose variants on a scheduled medicine

THE PROBLEM
-----------
Linaclotide is taken every morning, but the dose varies across 72, 145 and
290 mcg, and is sometimes a combination such as 145 + 72.

None of the three obvious models works:
  - three scheduled rows, one per strength: one is taken, so the day sits
    permanently at 1/3 and collects two false misses every morning;
  - one scheduled row at a fixed strength: the wrong dose is recorded on
    every day the strength differs;
  - unscheduled chips: no morning reminder, and a genuinely regular
    medicine stops looking regular.

THE FIX
-------
A scheduled row may carry a list of dose variants. Tapping such a row does
not log immediately; it opens a small multi-select of the strengths. Pick
one or several, tap Log, and the actual dose is written to the dose row.
Everything else about the row is unchanged.

  med_schedule.variants   '72|145|290'   pipe-separated, blank = normal row
  doses.dose_text         '145 + 72'     what was actually taken

Multi-select rather than single, because a combination is a real dose, not
two doses. One tap on the row, one or two on strengths, one on Log.

Anchor-verified, idempotent, compile-checked, self-restoring.
Python 3.9 compatible.

  python3 patch_gutlog_v332.py --check
  python3 patch_gutlog_v332.py
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V332_VARIANTS"


def build_edits():
    E = []

    # 1. schema version bump so _migrate re-runs on the live DB
    a = 'SCHEMA_VERSION = "3.3.0"   # GUTLOG_V330_PHASE_A'
    E.append(("schema version -> 3.3.2",
              a,
              'SCHEMA_VERSION = "3.3.2"   # GUTLOG_V330_PHASE_A '
              + MARKER))

    # 2. the new column, added by the same idempotent mechanism
    a = '    ("episodes", "bristol", "TEXT DEFAULT \'\'"),\n]'
    E.append(("med_schedule.variants column",
              a,
              '    ("episodes", "bristol", "TEXT DEFAULT \'\'"),\n'
              '    ("med_schedule", "variants", "TEXT DEFAULT \'\'"),\n]'))

    # 3. fresh databases get it from SCHEMA too
    a = ("  valid_to TEXT DEFAULT '', epoch INTEGER DEFAULT 1, "
         "notes TEXT DEFAULT '', created TEXT);")
    E.append(("SCHEMA: variants on med_schedule",
              a,
              "  valid_to TEXT DEFAULT '', epoch INTEGER DEFAULT 1, "
              "notes TEXT DEFAULT '', created TEXT,\n"
              "  variants TEXT DEFAULT '');"))

    # 4. /api/now must return it
    a = ("        \"SELECT s.id AS sched_id, s.med_id, s.slot, s.dose_text, "
         "s.with_food, \"")
    E.append(("/api/now returns variants",
              a,
              "        \"SELECT s.id AS sched_id, s.med_id, s.slot, "
              "s.dose_text, s.with_food, s.variants, \""))

    # 5. /api/schedule accepts it
    a = ('        (med_id, slot, (d.get("dose_text") or "")[:40],\n'
         '         (d.get("with_food") or "ANY")[:10], day, int(epoch), '
         'note(d), now_s()))')
    E.append(("/api/schedule accepts variants",
              a,
              '        (med_id, slot, (d.get("dose_text") or "")[:40],\n'
              '         (d.get("with_food") or "ANY")[:10], day, int(epoch), '
              'note(d), now_s()))\n'
              '    if d.get("variants") is not None:\n'
              '        db().execute(\n'
              '            "UPDATE med_schedule SET variants=? WHERE id="\n'
              '            "(SELECT MAX(id) FROM med_schedule)",\n'
              '            ((d.get("variants") or "")[:120],))\n'
              '        db().commit()'))

    # 6. INSERT must name the column
    a = ('        "INSERT INTO med_schedule(med_id,slot,dose_text,with_food,'
         'valid_from,"\n        "valid_to,epoch,notes,created) '
         'VALUES(?,?,?,?,?,\'\',?,?,?)",')
    E.append(("schedule INSERT unchanged, variants set after",
              a, a))

    # 7. CSS for the picker
    a = ".row2,.row3{min-width:0}"
    E.append(("CSS: variant picker",
              a,
              ".varpick{padding:10px 12px;border:1.5px dashed var(--teal);"
              "border-radius:13px;margin:0 0 8px;background:#F7FBF9}\n"
              ".varpick .vt{font-size:12.5px;color:var(--muted);"
              "margin:0 0 8px;font-weight:600}\n"
              ".varpick .vrow{display:flex;flex-wrap:wrap;gap:7px}\n"
              ".varpick .vb{display:flex;gap:8px;margin-top:10px}\n"
              ".varpick .vb button{flex:1;padding:10px;border-radius:11px;"
              "border:1.5px solid var(--line);background:#fff;font-size:14.5px;"
              "font-weight:600;cursor:pointer}\n"
              ".varpick .vb button.go{background:var(--teal);color:#fff;"
              "border-color:var(--teal)}\n" + a))

    # 8. the JS: tapping a variant row opens the picker
    a = """  d.onclick=async e=>{
    if(e.target.classList.contains('sk'))return;
    if(st==='TAKEN')return;
    try{await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,status:'TAKEN',
      day:nowData.day,dose_text:r.dose_text});toast('Logged '+r.name);loadNow();}
    catch(err){toast(err.message);}
  };"""
    new = """  d.onclick=async e=>{
    if(e.target.classList.contains('sk'))return;
    if(st==='TAKEN')return;
    if(r.variants){openVariantPicker(d,r);return;}
    try{await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,status:'TAKEN',
      day:nowData.day,dose_text:r.dose_text});toast('Logged '+r.name);loadNow();}
    catch(err){toast(err.message);}
  };"""
    E.append(("dose row opens picker when variants exist", a, new))

    # 9. the picker itself
    a = "async function loadNow(){"
    new = """/* A scheduled medicine whose dose varies. Multi-select, because a
   combination such as 145 + 72 is one dose, not two. */
function openVariantPicker(rowEl,r){
  if(document.querySelector('.varpick'))document.querySelector('.varpick').remove();
  const opts=r.variants.split('|').map(s=>s.trim()).filter(Boolean);
  const picked=[];
  const box=document.createElement('div');
  box.className='varpick';
  box.innerHTML='<p class="vt"></p><div class="vrow"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button>'+
    '<button type="button" class="go">Log</button></div>';
  box.querySelector('.vt').textContent=r.name+' - which dose?';
  const vrow=box.querySelector('.vrow');
  opts.forEach(v=>{
    const b=document.createElement('div');b.className='chip';b.textContent=v;
    b.onclick=()=>{
      const i=picked.indexOf(v);
      if(i>=0)picked.splice(i,1);else picked.push(v);
      b.classList.toggle('sel',picked.indexOf(v)>=0);
    };
    vrow.appendChild(b);
  });
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!picked.length){toast('Pick a dose');return;}
    const txt=picked.join(' + ');
    try{
      await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,
        status:'TAKEN',day:nowData.day,dose_text:txt});
      toast('Logged '+r.name+' '+txt);box.remove();loadNow();
    }catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

async function loadNow(){"""
    E.append(("variant picker function", a, new))

    # 10. show the recorded dose on a logged row
    a = ("  const sub=[r.dose_text||'',r.with_food&&r.with_food!=='ANY'?"
         "r.with_food.toLowerCase()+' food':'',")
    new = ("  const shown=(st==='TAKEN'&&r.logged_dose)?r.logged_dose:"
           "(r.variants&&!st?'dose varies':(r.dose_text||''));\n"
           "  const sub=[shown,r.with_food&&r.with_food!=='ANY'?"
           "r.with_food.toLowerCase()+' food':'',")
    E.append(("row subtitle shows the dose actually taken", a, new))

    # 11. /api/now must return the logged dose text
    a = ('        "       d.id AS dose_id, d.status AS status, '
         'd.dtime AS dtime "')
    E.append(("/api/now returns logged dose",
              a,
              '        "       d.id AS dose_id, d.status AS status, '
              'd.dtime AS dtime, "\n'
              '        "       d.dose_text AS logged_dose "'))

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
    print("GutLog dose-variants patch -> v3.3.2")
    print("file : " + args.file)
    print("=" * 60)

    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1

    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if "GUTLOG_V331_LAYOUT" not in src:
        print("FATAL: this file is not at v3.3.1. Apply that first.")
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
    bak = args.file + ".bak-v332-" + stamp
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

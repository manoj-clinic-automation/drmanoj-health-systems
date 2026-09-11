#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.4.0 -> v3.4.1  ::  restore the dose picker and row actions

THE BUG
  Tapping a scheduled row that carries dose variants (Lintide) did nothing.
  Tapping any already-logged row (to Undo / Change dose / Skip) also did
  nothing.

ROOT CAUSE
  patch_gutlog_v340.py replaced one large block of the Now-tab script.
  Its OLD block contained openRowActions() and openVariantPicker(); its NEW
  block kept the calls to both but not the functions themselves. The tap
  handler is async, so the ReferenceError died silently inside a promise --
  no toast, no error on screen. test_phase_a.py is server-side only and
  never runs the page's JavaScript, so it stayed 18/18.

FIX
  Re-insert both functions verbatim from their v3.3.3 form (taken from
  v340's own OLD block), immediately before loadNow(). No other line
  changes. After writing, the patcher checks that every Now-tab function
  that is CALLED is also DEFINED, so this class of loss cannot pass again.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring on post-write failure. Python 3.9 compatible.
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
MARKER = "GUTLOG_V341_PICKER"
PREV = "GUTLOG_V340_READABILITY"

ANCHOR_JS = "async function loadNow(){"
ANCHOR_VER = "# GUTLOG_V330_PHASE_A GUTLOG_V332_VARIANTS GUTLOG_V333_ROWACT GUTLOG_V340_READABILITY"

RESTORE_JS = r'''/* Tapping a row that is already logged. The instinct is to tap the thing
   again, so that has to do something -- but an accidental second tap must
   not silently delete a medication record, hence a strip rather than an
   immediate toggle. */
function openRowActions(rowEl,r,st){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');
  box.className='varpick';
  const canChange=!!r.variants;
  let html='<p class="vt"></p><div class="vb'+(canChange?' three':'')+'">'+
    '<button type="button" class="cx">Cancel</button>';
  if(canChange)html+='<button type="button" class="ch">Change dose</button>';
  if(st==='TAKEN')html+='<button type="button" class="sp">Skip</button>';
  html+='<button type="button" class="danger un">Undo</button></div>';
  box.innerHTML=html;
  const was=st==='TAKEN'?('taken'+(r.logged_dose?' '+r.logged_dose:'')):'skipped';
  box.querySelector('.vt').textContent=r.name+' - '+was;
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.un').onclick=async()=>{
    try{
      if(r.dose_id)await post('/api/now/undo/'+r.dose_id,{});
      toast('Undone');box.remove();loadNow();
    }catch(err){toast(err.message);}
  };
  const ch=box.querySelector('.ch');
  if(ch)ch.onclick=()=>{box.remove();openVariantPicker(rowEl,r);};
  const sp=box.querySelector('.sp');
  if(sp)sp.onclick=async()=>{
    try{
      await post('/api/now/dose',{med_id:r.med_id,sched_id:r.sched_id,
        status:'SKIPPED',day:nowData.day});
      toast('Marked skipped');box.remove();loadNow();
    }catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

/* A scheduled medicine whose dose varies. Multi-select, because a
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

'''

# every Now-tab function the page calls must exist in the page
MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow",
               "buildNowStatics", "bindFolds"]


def definitions_ok(src):
    missing = []
    for fn in MUST_DEFINE:
        if not re.search(r"function\s+" + fn + r"\s*\(", src):
            missing.append(fn)
    return missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("GutLog dose-picker restore -> v3.4.1")
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
        print("FATAL: this file is not at v3.4.0. Apply that first.")
        return 1
    if "function openVariantPicker(" in src or "function openRowActions(" in src:
        print("FATAL: a picker function is already defined -- unexpected state.")
        print("Nothing written. Report this rather than forcing.")
        return 1

    problems = []
    for label, a in (("js anchor", ANCHOR_JS), ("version anchor", ANCHOR_VER)):
        n = src.count(a)
        if n != 1:
            problems.append("  " + label + ": found " + str(n) + " times, need 1")
    print("anchors: " + str(2 - len(problems)) + "/2 matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src.replace(ANCHOR_JS, RESTORE_JS + ANCHOR_JS, 1)
    out = out.replace(ANCHOR_VER, ANCHOR_VER + " " + MARKER, 1)

    missing = definitions_ok(out)
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
    bak = args.file + ".bak-v341-" + stamp
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

    print("definitions: all " + str(len(MUST_DEFINE)) + " Now-tab functions present")
    print("applied: 2 edits")
    print("-" * 60)
    print("Next:  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

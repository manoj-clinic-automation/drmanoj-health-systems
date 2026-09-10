#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.3.2 -> v3.3.3  ::  a logged row can be tapped back

THE PROBLEM, found in use
-------------------------
Tapping a dose row that was already logged did nothing. The handler
returned early on status TAKEN. There was an 'undo' link at the right
edge, but it is small grey underlined text, easy to miss on a narrow
screen, and it is not where the instinct goes: you tap the thing again.

So the row could be checked and, as far as the hand was concerned, never
unchecked.

THE FIX
-------
Tapping a logged row opens a short action strip under it:

    Undo        remove the dose row entirely
    Change dose only on a row with dose variants
    Skip        switch a TAKEN row to SKIPPED
    Cancel

A strip rather than an immediate toggle, because an accidental second tap
should not silently delete a real medication record. One extra tap is the
right price for that.

The right-edge link stays for people who have learned it.

Anchor-verified, idempotent, compile-checked, self-restoring.
Python 3.9 compatible.

  python3 patch_gutlog_v333.py --check
  python3 patch_gutlog_v333.py
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V333_ROWACT"


def build_edits():
    E = []

    # 1. marker
    a = 'SCHEMA_VERSION = "3.3.2"   # GUTLOG_V330_PHASE_A GUTLOG_V332_VARIANTS'
    E.append(("version marker",
              a,
              'SCHEMA_VERSION = "3.3.2"   # GUTLOG_V330_PHASE_A '
              'GUTLOG_V332_VARIANTS ' + MARKER))

    # 2. CSS: reuse the picker look, add a danger button
    a = ".varpick .vb button.go{background:var(--teal);color:#fff;border-color:var(--teal)}"
    E.append(("CSS: row action strip",
              a,
              a + "\n"
              ".varpick .vb button.danger{background:var(--err);color:#fff;"
              "border-color:var(--err)}\n"
              ".varpick .vb.three button{font-size:13.5px;padding:10px 4px}\n"
              ".doserow .sk{padding:8px 6px;font-size:12.5px}"))

    # 3. tapping a logged row opens the strip instead of doing nothing
    a = """    if(e.target.classList.contains('sk'))return;
    if(st==='TAKEN')return;
    if(r.variants){openVariantPicker(d,r);return;}"""
    new = """    if(e.target.classList.contains('sk'))return;
    if(st){openRowActions(d,r,st);return;}
    if(r.variants){openVariantPicker(d,r);return;}"""
    E.append(("logged row opens action strip", a, new))

    # 4. the strip itself
    a = "/* A scheduled medicine whose dose varies."
    new = """/* Tapping a row that is already logged. The instinct is to tap the thing
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

/* A scheduled medicine whose dose varies."""
    E.append(("row action strip function", a, new))

    # 5. make the state readable at a glance: a logged row says so
    a = ("  const shown=(st==='TAKEN'&&r.logged_dose)?r.logged_dose:"
         "(r.variants&&!st?'dose varies':(r.dose_text||''));")
    new = ("  const shown=(st==='TAKEN'&&r.logged_dose)?r.logged_dose:"
           "(r.variants&&!st?'dose varies':(r.dose_text||''));\n"
           "  /* a logged row is tappable again -- say so, or the affordance\n"
           "     is invisible and the hand has to guess */")
    E.append(("comment the tappable state", a, new))

    # 6. subtitle hint on a logged row
    a = ("             st?(st==='TAKEN'?'taken '+(r.dtime||''):'skipped'):''"
         "].filter(Boolean).join(' \\u00b7 ');")
    new = ("             st?(st==='TAKEN'?'taken '+(r.dtime||''):'skipped'):'',"
           "\n             st?'tap to change':''"
           "].filter(Boolean).join(' \\u00b7 ');")
    E.append(("subtitle: tap to change", a, new))

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
    print("GutLog row-actions patch -> v3.3.3")
    print("file : " + args.file)
    print("=" * 60)

    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1

    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if "GUTLOG_V332_VARIANTS" not in src:
        print("FATAL: this file is not at v3.3.2. Apply that first.")
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
    bak = args.file + ".bak-v333-" + stamp
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

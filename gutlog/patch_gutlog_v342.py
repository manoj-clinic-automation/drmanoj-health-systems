#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.4.1 -> v3.4.2  ::  pain-by-site tiles with their own score

WHAT
  The Now-tab symptom card gains a "Pain by site" block with two tiles:
  Left iliac pain and Hypogastrium pain. Tapping a tile selects it and
  opens its own 1-10 score row underneath; tapping the name again clears
  it. Each tile saves as its own episode carrying its own score, sharing
  the time and Bristol with anything else saved in the same tap.

WHY OWN SCORE
  Two sites hurting at once rarely hurt equally. The shared Severity row
  still scores the ordinary symptom chips above it. A selected site with
  no score is refused with a toast rather than saved as a blank -- an
  unscored pain row is the one thing this block exists to prevent.

  Sites are a list (PAIN_SITES), so another site is a one-word change.

Requires v3.4.1 (patch_gutlog_v341.py) -- that restores the dose picker
and row actions this file sits beside.

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
MARKER = "GUTLOG_V342_PAINSITE"
PREV = "GUTLOG_V341_PICKER"


def build_edits():
    E = []

    a = "GUTLOG_V340_READABILITY " + PREV
    E.append(("version marker", a, a + " " + MARKER))

    a = ('      <div class="chips" id="n_symSev"></div>\n'
         '      <p class="lbl" style="margin-top:14px">Bristol (optional)</p>\n')
    n = ('      <div class="chips" id="n_symSev"></div>\n'
         '      <p class="lbl" style="margin-top:14px">Pain by site &mdash; tap to score</p>\n'
         '      <div id="n_painSites"></div>\n'
         '      <p class="lbl" style="margin-top:14px">Bristol (optional)</p>\n')
    E.append(("html pain block", a, n))

    a = "let nowData=null, nSym={types:[],sev:null,bristol:null}, showAllMeds=false;\n"
    n = a + (
        "/* " + MARKER + " -- each site carries its own score; present in\n"
        "   nPain = selected, value '' = selected but not yet scored */\n"
        "const PAIN_SITES=['Left iliac pain','Hypogastrium pain'];\n"
        "let nPain={};\n")
    E.append(("js state", a, n))

    a = "  const s=$('#n_symSev');\n"
    n = r"""  /* Pain by site: tap the tile to select it and open its score row,
     tap the name again to clear it. */
  const ps=$('#n_painSites');
  PAIN_SITES.forEach(site=>{
    const w=document.createElement('div');w.className='ptile';
    w.innerHTML='<button type="button" class="ph"><span class="pn"></span><span class="pv"></span></button>'+
                '<div class="chips pscore"></div>';
    w.querySelector('.pn').textContent=site;
    const pv=w.querySelector('.pv'), row=w.querySelector('.pscore');
    SEVS.forEach(v=>{const b=document.createElement('div');b.className='chip num';b.textContent=v;
      b.onclick=()=>{nPain[site]=v;pv.textContent=v+'/10';
        [...row.children].forEach(c=>c.classList.toggle('sel',c===b));};
      row.appendChild(b);});
    w.querySelector('.ph').onclick=()=>{
      if(site in nPain){delete nPain[site];w.classList.remove('open');pv.textContent='';
        [...row.children].forEach(c=>c.classList.remove('sel'));}
      else{nPain[site]='';w.classList.add('open');pv.textContent='score?';}
    };
    ps.appendChild(w);
  });

""" + a
    E.append(("js pain tiles", a, n))

    a = r"""  $('#n_symSave').onclick=async()=>{
    if(!nSym.types.length){toast('Pick a symptom');return;}
    const t=nowHM();
    try{
      for(const ty of nSym.types){
        await post('/api/episodes',{day:todayISO,etime:t,category:'GI',etype:ty,
          severity:nSym.sev,bristol:nSym.bristol});
      }
      toast(nSym.types.length>1?(nSym.types.length+' episodes saved'):'Episode saved');
      nSym={types:[],sev:null,bristol:null};
      $$('#n_symType .chip,#n_symSev .chip,#n_symBristol .chip').forEach(c=>c.classList.remove('sel'));
    }catch(err){toast(err.message);}
  };
"""
    n = r"""  $('#n_symSave').onclick=async()=>{
    const sites=Object.keys(nPain);
    if(!nSym.types.length&&!sites.length){toast('Pick a symptom');return;}
    const unscored=sites.filter(k=>!nPain[k]);
    if(unscored.length){toast('Give '+unscored[0].toLowerCase()+' a score');return;}
    const t=nowHM();
    try{
      for(const ty of nSym.types){
        await post('/api/episodes',{day:todayISO,etime:t,category:'GI',etype:ty,
          severity:nSym.sev,bristol:nSym.bristol});
      }
      for(const k of sites){
        await post('/api/episodes',{day:todayISO,etime:t,category:'GI',etype:k,
          severity:nPain[k],bristol:nSym.bristol});
      }
      const n=nSym.types.length+sites.length;
      toast(n>1?(n+' episodes saved'):'Episode saved');
      nSym={types:[],sev:null,bristol:null};nPain={};
      $$('#n_symType .chip,#n_symSev .chip,#n_symBristol .chip,#n_painSites .chip').forEach(c=>c.classList.remove('sel'));
      $$('#n_painSites .ptile').forEach(w=>{w.classList.remove('open');w.querySelector('.pv').textContent='';});
    }catch(err){toast(err.message);}
  };
"""
    E.append(("js save handler", a, n))

    a = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n</style></head><body>"
    n = ("/* " + MARKER + " -- pain-by-site tiles */\n"
         ".ptile{border:2px solid var(--line);border-radius:13px;margin:0 0 8px;background:var(--chip)}\n"
         ".ptile .ph{display:flex;width:100%;align-items:center;gap:10px;background:none;border:0;"
         "padding:12px 14px;font-size:16px;font-weight:600;color:var(--ink);cursor:pointer;text-align:left}\n"
         ".ptile .pv{margin-left:auto;font-size:14px;font-weight:700;color:var(--teal-d)}\n"
         ".ptile .pscore{display:none;padding:0 12px 12px}\n"
         ".ptile.open{border-color:var(--teal);background:#F7FBF9}\n"
         ".ptile.open .pscore{display:flex}\n"
         "@media (max-width:430px){ #n_painSites .chip{padding:8px 0;min-width:34px;text-align:center}}\n"
         + a)
    E.append(("css", a, n))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow",
               "buildNowStatics", "bindFolds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("GutLog pain-by-site tiles -> v3.4.2")
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
        print("FATAL: this file is not at v3.4.1. Run patch_gutlog_v341.py first.")
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
    if args.check:
        print("All anchors OK.")
        return 0

    # APP_PAGE is a Jinja template: a "{#" in new CSS/JS opens a comment
    # and breaks the whole page at render time, which compile cannot see.
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if tok in new and tok not in anchor:
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2

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
    bak = args.file + ".bak-v342-" + stamp
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

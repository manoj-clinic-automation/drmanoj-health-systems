#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.26.0 -> v3.27.0  ::  GUTLOG_V3270_TIMEPICK

Three things he met on 20-Sep-2026:

  1. The Meals card said "62 of 57 g protein" while the plan card said
     "62 / 100 g". The meal log itself was right; the old fixed 57 g target
     had never been replaced. Every protein target now comes from the diet
     plan when there is one (the fixed value stays as the fallback).
  2. An extra (as-needed) dose could only be undone, never re-timed, so a
     dose logged late kept the wrong time. Tapping its row now opens a time
     strip with one-tap "15 min / 30 min / 1 h / 2 h ago" and a free time.
  3. On the folded phone's narrow screen, the phone's own time dialog hid
     its Set button. No page uses that dialog any more: every time box is
     shown as two plain lists (hour, minute), which fit any screen. The
     original box stays underneath, so every reader of .value is unchanged.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse, refuses Jinja tokens in new page text.
Python 3.9.
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
MARKER = "GUTLOG_V3270_TIMEPICK"
PREV = "GUTLOG_V3260_TRIALS"

SERVER = '''# GUTLOG_V3270_TIMEPICK -- one protein target everywhere: the diet plan's when
# there is one. The meal card read "62 of 57 g" beside a plan card reading
# "62 / 100 g"; the fixed 57 g is now only the fallback.
def _protein_target():
    cfg = _plan_cfg()
    try:
        v = float(((cfg or {}).get("targets") or {}).get("protein"))
    except (TypeError, ValueError):
        return PROTEIN_TARGET
    if v <= 0:
        return PROTEIN_TARGET
    return int(v) if v == int(v) else v


'''

JS = r"""/* GUTLOG_V3270_TIMEPICK -- no phone time dialog. On the folded phone the
   system time dialog hid its Set button, so every time box on the page is
   shown as two plain lists instead (hour, minute). The real box stays,
   hidden, and keeps its value, so nothing that reads .value changes. */
function tpPad(n){return (n<10?'0':'')+n;}
const TP_DESC=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
function tpEnhance(inp){
  if(!inp||inp.dataset.tp||!inp.parentNode)return;
  inp.dataset.tp='1';
  const w=document.createElement('span');w.className='tpick';
  const h=document.createElement('select'),m=document.createElement('select');
  h.className='tph';m.className='tpm';
  h.setAttribute('aria-label','Hour');m.setAttribute('aria-label','Minute');
  h.add(new Option('--',''));m.add(new Option('--',''));
  for(let i=0;i<24;i++)h.add(new Option(tpPad(i),tpPad(i)));
  for(let i=0;i<60;i++)m.add(new Option(tpPad(i),tpPad(i)));
  w.appendChild(h);w.appendChild(document.createTextNode(':'));w.appendChild(m);
  const show=v=>{const ok=/^\d\d:\d\d$/.test(v||'');h.value=ok?v.slice(0,2):'';m.value=ok?v.slice(3,5):'';};
  Object.defineProperty(inp,'value',{configurable:true,
    get(){return TP_DESC.get.call(inp);},
    set(v){TP_DESC.set.call(inp,v);show(TP_DESC.get.call(inp));}});
  const sync=()=>{
    if(h.value&&!m.value)m.value='00';
    if(!h.value)m.value='';
    TP_DESC.set.call(inp,h.value?(h.value+':'+m.value):'');
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new Event('change',{bubbles:true}));
  };
  h.onchange=sync;m.onchange=sync;
  inp.style.display='none';
  inp.parentNode.insertBefore(w,inp.nextSibling);
  show(TP_DESC.get.call(inp));
}
function tpScan(root){(root||document).querySelectorAll('input[type=time]').forEach(tpEnhance);}
new MutationObserver(ms=>ms.forEach(x=>x.addedNodes.forEach(n=>{
  if(n.nodeType!==1)return;
  if(n.matches&&n.matches('input[type=time]'))tpEnhance(n);
  else if(n.querySelectorAll)tpScan(n);
}))).observe(document.documentElement,{childList:true,subtree:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>tpScan(document));
else tpScan(document);

/* An extra dose logged late gets its real time here. */
function tpAgo(mins){const d=new Date(Date.now()-mins*60000);return tpPad(d.getHours())+':'+tpPad(d.getMinutes());}
function exTimeEdit(rowEl,e){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick';
  box.innerHTML='<p class="vt"></p><div class="chips exago"></div>'+
    '<div class="vtm"><span class="lb">Time</span><input type="time" class="tt"></div>'+
    '<div class="vb"><button type="button" class="cx">Cancel</button>'+
    '<button type="button" class="go">Save time</button></div>';
  box.querySelector('.vt').textContent=e.medicine+' - when was it taken?';
  const tt=box.querySelector('.tt');tt.value=e.dtime||'';
  const ago=box.querySelector('.exago');
  const today=(nowData&&nowData.day)===todayISO;
  [[15,'15 min ago'],[30,'30 min ago'],[60,'1 h ago'],[120,'2 h ago']].forEach(a=>{
    if(!today)return;
    const b=document.createElement('button');b.type='button';b.className='chip';b.textContent=a[1];
    b.onclick=()=>{tt.value=tpAgo(a[0]);ago.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};
    ago.appendChild(b);
  });
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!tt.value){toast('Pick a time');return;}
    try{const r=await post('/api/retime',{table:'doses',id:e.id,day:nowData.day,time:tt.value});
      toast(r.unchanged?'No change':('Time set to '+tt.value));box.remove();loadNow();}
    catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
"""

CSS = """/* GUTLOG_V3270_TIMEPICK */
.tpick{display:inline-flex;align-items:center;gap:6px;font-size:18px;font-weight:700}
.tpick select{width:auto;min-width:72px;padding:10px 8px;font-size:18px}
.exrow.extime .t,.exrow.extime .m{cursor:pointer}
.exrow.extime .t{text-decoration:underline dotted}
.exago{margin:6px 0 8px}
"""

EXTRAS_OLD = """    row.querySelector('.t').textContent=e.dtime||'';
    row.querySelector('.m').textContent=e.medicine;
    row.querySelector('.u').onclick=async()=>{
      await post('/api/now/undo/'+e.id,{});toast('Removed');loadNow();};
    el.appendChild(row);
"""
EXTRAS_NEW = """    row.querySelector('.t').textContent=e.dtime||'--:--';
    row.querySelector('.m').textContent=e.medicine;
    row.querySelector('.u').onclick=async()=>{
      await post('/api/now/undo/'+e.id,{});toast('Removed');loadNow();};
    row.classList.add('extime');row.title='Tap to change the time';
    row.querySelector('.t').onclick=()=>exTimeEdit(row,e);
    row.querySelector('.m').onclick=()=>exTimeEdit(row,e);
    el.appendChild(row);
"""

EDITS = [
    ("version", "GUTLOG_V3250_PLAN GUTLOG_V3260_TRIALS\n",
     "GUTLOG_V3250_PLAN GUTLOG_V3260_TRIALS " + MARKER + "\n"),
    ("target helper", "def _log_meal(d, replace_id=None):\n",
     SERVER + "def _log_meal(d, replace_id=None):\n"),
    ("target today", "protein=prot or 0, target=PROTEIN_TARGET, streak=streak,",
     "protein=prot or 0, target=_protein_target(), streak=streak,"),
    ("target cards", "                   protein_target=PROTEIN_TARGET)",
     "                   protein_target=_protein_target())"),
    ("target review", "daily=daily, dosecount=dosecount, registry=registry, target=PROTEIN_TARGET)",
     "daily=daily, dosecount=dosecount, registry=registry, target=_protein_target())"),
    ("css", ".ttab th{font-size:13px;color:var(--muted);font-weight:700}\n",
     ".ttab th{font-size:13px;color:var(--muted);font-weight:700}\n" + CSS),
    ("js", "/* GUTLOG_V3260_TRIALS -- food trials as periods. */\n",
     JS + "/* GUTLOG_V3260_TRIALS -- food trials as periods. */\n"),
    ("extras rows", EXTRAS_OLD, EXTRAS_NEW),
]
PAGE_TEXT = [JS, CSS, EXTRAS_NEW]


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
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("=" * 66)
    print("GutLog one protein target, re-timed extras, no phone time dialog -> v3.27.0")
    print("file : " + a.file)
    print("=" * 66)
    for t in PAGE_TEXT:
        if re.search(r"\{[{%#]", t):
            print("FATAL: a Jinja token in new page text (CLAUDE.md 5b). Nothing written.")
            return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.26.0.")
        return 1
    bad = [l for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + ". Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cand.py")
    write(f, out)
    try:
        py_compile.compile(f, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
    bak = a.file + ".bak-v3270-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3270.py app.py   then the regression suites")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

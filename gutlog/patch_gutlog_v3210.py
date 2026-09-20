#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.20.0 -> v3.21.0  ::  one tablet is one dose, however many symptoms

THE DEFECT (his report, 20-Sep-2026)
------------------------------------
Every pain tile and the down-day card ask what was taken for it, and each
answer wrote a NEW dose row. One tablet, entered against the evening's
down day and then against a pain site three minutes later, became two doses;
a combination tablet with no chip of its own was entered as its two single
ingredients, so each entry became two rows. RxGuard's new daily-dose total
then read one tablet as three times the ceiling.

WHAT CHANGES
  1. A medicine chip on a pain tile or the down-day card is what was USED for
     the symptom. If a dose of that medicine -- or of any product containing
     all its ingredients -- is already logged in the 6 hours before (to 30
     minutes after) the entry, the chip is linked to that dose and no row is
     written. The response carries `same_dose` so the page can say so.
  2. The page shows a strip: "<medicine> - counted with the dose at HH:MM",
     with one button, "It was a new dose", which logs it for real. Nothing is
     silently dropped; the owner can always overrule.
  3. The Now tab is unchanged: each tap there is one tablet, as before (two
     taps can be two tablets on purpose; the suites rely on it).
  4. A combination tablet gets its own chip through regimen.local.json
     (server config, not code): nothing in this file names a medicine.

Requires v3.20.0 (GUTLOG_V3200_PIPES). Anchor-verified, idempotent,
compile-checked, refuses Jinja tokens in new page text, .bak before write,
self-restoring, --reverse for the negative control. Python 3.9.
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
MARKER = "GUTLOG_V3210_ONEDOSE"
PREV = "GUTLOG_V3200_PIPES"

VERSION_OLD = "GUTLOG_V3190_ORDER GUTLOG_V3200_PIPES\n"
VERSION_NEW = "GUTLOG_V3190_ORDER GUTLOG_V3200_PIPES " + MARKER + "\n"

LOG_OLD = '''def _log_analgesics(labels, day, hm, reason, score, ref):
    """Write one doses row per analgesic chip and mirror each to FitLog with
    the score attached. Shared by the pain tiles and the down-day card, so a
    medicine is recorded the same way whichever surface it was tapped on.
    Returns (doses, mirrored, not_mirrored, linked)."""
    doses, mirrored, missed = [], [], []
    linked = _links_enabled()
    for t in labels:
        if t not in PAIN_ANALGESICS:
            continue
        mol, fallback = PAIN_ANALGESICS[t]
        mid, name = _pain_med(mol, fallback)
'''
LOG_NEW = '''# GUTLOG_V3210_ONEDOSE -- a chip on a symptom says what was USED for it.
# One tablet entered against three symptoms is one dose, so a chip whose
# medicine (or a product carrying all its ingredients) was logged shortly
# before is linked to that dose instead of writing another row.
SAME_DOSE_BEFORE_MIN = 360
SAME_DOSE_AFTER_MIN = 30


def _mol_set(mol):
    return set(p.strip().lower() for p in re.split(r"[+,]", mol or "") if p.strip())


def _hm_min(hm):
    try:
        h, m = (hm or "")[:5].split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def _recent_same_dose(mol, mid, name, day, hm):
    """The latest dose on `day` from SAME_DOSE_BEFORE_MIN before `hm` to
    SAME_DOSE_AFTER_MIN after it, of the same medicine or of a product whose
    ingredients include every ingredient of this one. None if there is none."""
    want = _mol_set(mol)
    at = _hm_min(hm)
    if at is None:
        return None
    best = None
    for r in db().execute(
            "SELECT d.id, d.dtime, d.medicine, d.med_id, COALESCE(p.molecule,'') AS molecule "
            "FROM doses d LEFT JOIN prnmeds p ON p.id=d.med_id WHERE d.day=? "
            "AND COALESCE(d.status,'')<>'SKIPPED' ORDER BY d.dtime, d.id", (day,)).fetchall():
        t = _hm_min(r["dtime"])
        if t is None or not (at - SAME_DOSE_BEFORE_MIN <= t <= at + SAME_DOSE_AFTER_MIN):
            continue
        same = (mid is not None and r["med_id"] == mid) or \\
            (r["med_id"] is None and (r["medicine"] or "") == name) or \\
            (bool(want) and want <= _mol_set(r["molecule"]))
        if same and (best is None or (r["dtime"] or "") >= (best["dtime"] or "")):
            best = r
    return best


def _log_analgesics(labels, day, hm, reason, score, ref):
    """Write one doses row per analgesic chip and mirror each to FitLog with
    the score attached. Shared by the pain tiles and the down-day card, so a
    medicine is recorded the same way whichever surface it was tapped on.
    GUTLOG_V3210_ONEDOSE: a chip already covered by a recent dose is linked,
    not written. Returns (doses, mirrored, not_mirrored, linked, same_dose)."""
    doses, mirrored, missed, same_dose = [], [], [], []
    linked = _links_enabled()
    for t in labels:
        if t not in PAIN_ANALGESICS:
            continue
        mol, fallback = PAIN_ANALGESICS[t]
        mid, name = _pain_med(mol, fallback)
        prior = _recent_same_dose(mol, mid, name, day, hm)
        if prior is not None:
            same_dose.append({"label": t, "med_id": mid, "name": name,
                              "dose_id": prior["id"], "dose_name": prior["medicine"],
                              "time": prior["dtime"] or "", "reason": reason})
            continue
'''

LOG_RET_OLD = '''        (mirrored if (ans or {}).get("ok") else missed).append(name)
    return doses, mirrored, missed, linked
'''
LOG_RET_NEW = '''        (mirrored if (ans or {}).get("ok") else missed).append(name)
    return doses, mirrored, missed, linked, same_dose
'''

PAIN_OLD = '''    doses, mirrored, missed, linked = _log_analgesics(
        treats, day, etime, meta[1], score, "gutlog-episode-" + str(eid))
    return jsonify(ok=True, id=eid, site=meta[1], side=meta[2], radiates=radiates,
                   doses=doses, mirrored=mirrored, not_mirrored=missed, linked=linked)
'''
PAIN_NEW = '''    doses, mirrored, missed, linked, same = _log_analgesics(
        treats, day, etime, meta[1], score, "gutlog-episode-" + str(eid))
    return jsonify(ok=True, id=eid, site=meta[1], side=meta[2], radiates=radiates,
                   doses=doses, mirrored=mirrored, not_mirrored=missed, linked=linked,
                   same_dose=same)
'''

DOWN_OLD = '''    doses, mirrored, missed, linked = _log_analgesics(
        new_meds, day, hm, "Down day", None, "gutlog-down-" + day)
'''
DOWN_NEW = '''    doses, mirrored, missed, linked, same = _log_analgesics(
        new_meds, day, hm, "Down day", None, "gutlog-down-" + day)
'''
DOWN_RET_OLD = '''    st.update(ok=True, doses=doses, mirrored=mirrored, not_mirrored=missed,
              linked=linked, temp_saved=temp_saved)
'''
DOWN_RET_NEW = '''    st.update(ok=True, doses=doses, mirrored=mirrored, not_mirrored=missed,
              linked=linked, temp_saved=temp_saved, same_dose=same)
'''

CSS_OLD = ".toast.show{opacity:1}\n"
CSS_NEW = CSS_OLD + (
    ".samedose{position:fixed;left:50%;transform:translateX(-50%);"
    "bottom:calc(84px + env(safe-area-inset-bottom));width:min(92vw,440px);z-index:9;"
    "background:var(--card);color:var(--ink);border:1.5px solid var(--amber);"
    "border-radius:13px;padding:10px 12px;font-size:15px;display:none;"
    "box-shadow:0 4px 18px rgba(0,0,0,.18)}\n"
    ".samedose.show{display:block}\n"
    ".samedose .sdrow{display:flex;gap:8px;align-items:center;justify-content:space-between;"
    "margin:4px 0}\n"
    ".samedose button{font-size:14px;min-height:40px;padding:6px 12px;border-radius:999px;"
    "border:1.5px solid var(--teal);background:transparent;color:var(--teal);white-space:nowrap}\n"
    ".samedose .sdx{border-color:var(--line);color:var(--muted)}\n")

DIV_OLD = '<div class="toast" id="toast" role="status"></div>\n'
DIV_NEW = DIV_OLD + '<div class="samedose" id="samedose" role="status"></div>\n'

JS_FN_OLD = ("function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');"
             "setTimeout(()=>t.classList.remove('show'),1700);}\n")
JS_FN_NEW = JS_FN_OLD + '''// GUTLOG_V3210_ONEDOSE -- a chip linked to a dose already logged says so,
// and one tap overrules it when it really was a second tablet.
function showSame(list){
  const box=$('#samedose');
  if(!box||!list||!list.length)return;
  box.innerHTML='';
  list.forEach(s=>{
    const row=document.createElement('div');row.className='sdrow';
    const t=document.createElement('span');
    t.textContent=s.label+' \\u2014 counted with the '+s.dose_name+' dose at '+s.time;
    const b=document.createElement('button');b.textContent='It was a new dose';
    b.onclick=async()=>{
      try{
        await post('/api/now/dose',{med_id:s.med_id,medicine:s.name,status:'EXTRA',
          reason:s.reason,day:todayISO});
        toast(s.name+' recorded as a new dose');row.remove();
        if(!box.querySelector('.sdrow'))box.classList.remove('show');
        loadNow();
      }catch(err){toast(err.message);}
    };
    row.appendChild(t);row.appendChild(b);box.appendChild(row);
  });
  const x=document.createElement('button');x.className='sdx';x.textContent='OK';
  x.onclick=()=>box.classList.remove('show');
  box.appendChild(x);
  box.classList.add('show');
}
'''

JS_PAIN_OLD = '''        if(r.doses&&r.doses.length)msg+=' \\u00b7 '+r.doses.join(' + ')+' recorded';
        toast(msg);
'''
JS_PAIN_NEW = '''        if(r.doses&&r.doses.length)msg+=' \\u00b7 '+r.doses.join(' + ')+' recorded';
        toast(msg);
        showSame(r.same_dose);
'''
JS_DOWN_OLD = '''        if(r.doses&&r.doses.length)toast(r.doses.join(' + ')+' recorded');
        if(r.not_mirrored&&r.not_mirrored.length)toast('FitLog did not take '+r.not_mirrored.join(', '));
        await loadDown();
'''
JS_DOWN_NEW = '''        if(r.doses&&r.doses.length)toast(r.doses.join(' + ')+' recorded');
        if(r.not_mirrored&&r.not_mirrored.length)toast('FitLog did not take '+r.not_mirrored.join(', '));
        showSame(r.same_dose);
        await loadDown();
'''

PAGE_TEXT = [CSS_NEW, DIV_NEW, JS_FN_NEW, JS_PAIN_NEW, JS_DOWN_NEW]


def build_edits():
    return [
        ("version", VERSION_OLD, VERSION_NEW),
        ("same-dose helpers + _log_analgesics head", LOG_OLD, LOG_NEW),
        ("_log_analgesics return", LOG_RET_OLD, LOG_RET_NEW),
        ("pain caller", PAIN_OLD, PAIN_NEW),
        ("down-day caller", DOWN_OLD, DOWN_NEW),
        ("down-day return", DOWN_RET_OLD, DOWN_RET_NEW),
        ("css", CSS_OLD, CSS_NEW),
        ("strip", DIV_OLD, DIV_NEW),
        ("js showSame", JS_FN_OLD, JS_FN_NEW),
        ("js pain", JS_PAIN_OLD, JS_PAIN_NEW),
        ("js down", JS_DOWN_OLD, JS_DOWN_NEW),
    ]


def read(path):
    fh = open(path, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(path, text):
    fh = open(path, "w", encoding="utf-8", newline="")
    try:
        fh.write(text)
    finally:
        fh.close()


def reverse(path, out_path):
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    out = src
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            print("REVERSE FAILED, nothing written: " + label + " found " + str(c) + " times")
            return 1
        out = out.replace(new, anchor, 1)
    if MARKER in out or PREV not in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed " + PREV + " -> " + out_path + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("GutLog: one tablet is one dose, however many symptoms -> v3.21.0")
    print("file : " + args.file)
    print("=" * 66)
    for t in PAGE_TEXT:
        if re.search(r"\{[{%#]", t):
            print("FATAL: a Jinja token in new page text (CLAUDE.md 5b). Nothing written.")
            return 1
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.20.0. Apply that first.")
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
    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    write(tmpf, out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v3210-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("Next:  python3 test_one_dose.py app.py ; then the usual suites; restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

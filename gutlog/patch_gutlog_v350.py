#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.4.2 -> v3.5.0  ::  Phase B -- fix the time, fill the gaps, see the day

 1. RETIME FROM THE NOW TAB. Tapping a logged dose opens its action strip;
    the strip now carries the time it was logged, editable, with Save time.
    For the tick that went in at 09:00 for a dose taken at 06:00.

 2. DAY BY DAY (Review tab, first card). One day at a time, everything
    logged -- scheduled doses, extras, skips, symptoms, BP, meals -- in
    time order, tagged by kind. Tap any entry to move it to the right time
    or day, or delete it. Arrows and a date box move between days.

 3. BACKFILL. Under the day's list: the scheduled doses that day which
    were never logged, each with a time box (defaulting to the slot time)
    and Taken / Skipped. Dose-variant medicines show their strength chips.

 4. EVERY MOVE IS RECORDED. A new `edits` table keeps old and new day/time
    for each retime, and the day view marks such entries "time edited".
    A diary entry whose time silently changed cannot be trusted later.

 5. GUARDS, server-side: no future day, no time later than now for today,
    HH:MM only. A scheduled dose can only move to a day its regimen line
    covered, and never onto a day where it is already logged.

 6. CHANGE DOSE KEEPS THE TIME. Picking a different strength on a taken
    row used to restamp it with the current time -- which would undo a
    retime. It now keeps the logged time.

 7. MIDNIGHT. The page takes "today" once, at load. A phone left with the
    app open overnight would log the 5 am dose against yesterday. The page
    now reloads itself when it is brought back on a new calendar day.

Requires v3.4.2. Anchor-verified, idempotent, compile-checked, Jinja-safe,
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
MARKER = "GUTLOG_V350_PHASE_B"
PREV = "GUTLOG_V342_PAINSITE"

PY_ROUTES = r'''# ------------------------------------------------------------------ day view / retime
# GUTLOG_V350_PHASE_B -- one day, every stream, in time order; entries can
# be moved to the time they actually happened. Every move is kept in
# `edits`, so a changed time is visible rather than silent.
_TIME_COL = {"doses": "dtime", "episodes": "etime", "vitals": "vtime", "meals": "mtime"}


def _valid_hm(s):
    s = (s or "").strip()
    if len(s) != 5 or s[2] != ":" or not (s[:2] + s[3:]).isdigit():
        return None
    if int(s[:2]) > 23 or int(s[3:]) > 59:
        return None
    return s


def _valid_day(s):
    try:
        d = date.fromisoformat((s or "").strip())
    except ValueError:
        return None
    if d > date.today() or d.year < 2020:
        return None
    return d.isoformat()


@app.route("/api/dayview")
@login_required
def api_dayview():
    day = _valid_day(request.args.get("day")) or today()
    edited = set((r["tbl"], r["rid"]) for r in db().execute(
        "SELECT DISTINCT tbl, rid FROM edits").fetchall())
    out = []

    def add(tbl, rid, t, kind, title, sub):
        out.append({"tbl": tbl, "id": rid, "time": t or "", "kind": kind,
                    "title": title or "", "sub": sub,
                    "edited": (tbl, rid) in edited})

    for r in db().execute(
            "SELECT id, dtime, medicine, status, sched_id, dose_text "
            "FROM doses WHERE day=?", (day,)).fetchall():
        if r["status"] == "SKIPPED":
            add("doses", r["id"], r["dtime"], "Skipped", r["medicine"], "")
        else:
            add("doses", r["id"], r["dtime"],
                "Dose" if r["sched_id"] else "Extra",
                r["medicine"], r["dose_text"] or "")
    for r in db().execute(
            "SELECT id, etime, etype, side, severity, bristol "
            "FROM episodes WHERE day=?", (day,)).fetchall():
        parts = []
        if r["severity"] not in (None, ""):
            parts.append(str(r["severity"]) + "/10")
        if r["bristol"]:
            parts.append("Bristol " + str(r["bristol"]))
        if r["side"]:
            parts.append(str(r["side"]))
        add("episodes", r["id"], r["etime"], "Symptom", r["etype"], " · ".join(parts))
    for r in db().execute(
            "SELECT id, vtime, sys, dia, pulse, weight, temp "
            "FROM vitals WHERE day=?", (day,)).fetchall():
        parts = []
        if r["pulse"]:
            parts.append("pulse " + str(r["pulse"]))
        if r["weight"]:
            parts.append(str(r["weight"]) + " kg")
        if r["temp"]:
            parts.append(str(r["temp"]) + "°")
        if r["sys"] or r["dia"]:
            add("vitals", r["id"], r["vtime"], "BP",
                str(r["sys"] or "-") + "/" + str(r["dia"] or "-"), " · ".join(parts))
        else:
            add("vitals", r["id"], r["vtime"], "Vitals", "Vitals", " · ".join(parts))
    for r in db().execute(
            "SELECT id, mtime, slot, items, protein FROM meals WHERE day=?",
            (day,)).fetchall():
        try:
            names = [str(i.get("n", "")) for i in json.loads(r["items"] or "[]")]
        except (ValueError, AttributeError):
            names = []
        sub = ", ".join(n for n in names if n)[:90]
        if r["protein"]:
            sub = (sub + " · " if sub else "") + str(r["protein"]) + " g protein"
        add("meals", r["id"], r["mtime"], "Meal", r["slot"] or "Meal", sub)

    out.sort(key=lambda e: (e["time"] == "", e["time"], e["tbl"], e["id"]))
    return jsonify(day=day, today=today(), entries=out)


@app.route("/api/retime", methods=["POST"])
@login_required
def api_retime():
    d = J()
    tbl = d.get("table")
    col = _TIME_COL.get(tbl)
    if not col:
        return jsonify(ok=False, err="Unknown entry type."), 400
    try:
        rid = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Bad entry."), 400
    new_t = _valid_hm(d.get("time"))
    if not new_t:
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    row = db().execute("SELECT * FROM " + tbl + " WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify(ok=False, err="Entry not found."), 404
    new_d = _valid_day(d.get("day") or row["day"])
    if not new_d:
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if new_d == today() and new_t > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400

    old_d, old_t = row["day"], (row[col] or "")
    if old_d == new_d and old_t == new_t:
        return jsonify(ok=True, unchanged=True)

    if tbl == "doses" and row["sched_id"] and new_d != old_d:
        on = db().execute(
            "SELECT 1 FROM med_schedule WHERE id=? AND valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)",
            (row["sched_id"], new_d, new_d)).fetchone()
        if not on:
            return jsonify(ok=False, err="That medicine was not scheduled on " + new_d + "."), 400
        clash = db().execute(
            "SELECT 1 FROM doses WHERE sched_id=? AND day=? AND id<>?",
            (row["sched_id"], new_d, rid)).fetchone()
        if clash:
            return jsonify(ok=False, err="Already logged on " + new_d + " - undo that one first."), 409

    db().execute("UPDATE " + tbl + " SET day=?, " + col + "=? WHERE id=?",
                 (new_d, new_t, rid))
    db().execute(
        "INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
        "VALUES(?,?,?,?,?,?,?)", (tbl, rid, old_d, old_t, new_d, new_t, now_s()))
    db().commit()
    return jsonify(ok=True)


'''

HTML_CARD = r'''<section class="tab" id="tab-review">
  <div class="card" id="dayView">
    <p class="q">Day by day</p>
    <div class="dvnav"><button type="button" class="btn ghost" id="dvPrev">&lsaquo;</button>
      <input type="date" id="dvDate"><button type="button" class="btn ghost" id="dvNext">&rsaquo;</button></div>
    <p class="hint" style="margin:8px 2px 4px"><span id="dvSum"></span> &middot; tap an entry to fix its time</p>
    <div id="dvList"></div>
    <p class="lbl" id="dvMissHd" style="margin-top:14px">Scheduled but not logged &mdash; enter the time it was taken</p>
    <div id="dvMiss"></div>
  </div>
'''

JS_DAYVIEW = r'''/* ---------- DAY BY DAY (GUTLOG_V350_PHASE_B) ----------
   Everything logged on one day, every stream, in time order. Tap an
   entry to move it to the time (or day) it really happened, or delete it.
   Below: that day's scheduled doses never logged, for backfilling. */
let dvDay=todayISO;
const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',BP:'bp',Vitals:'bp',Meal:'meal'};
function dvShift(n){
  const d=new Date(dvDay+'T12:00:00');d.setDate(d.getDate()+n);
  const s=d.toLocaleDateString('en-CA');
  if(s>todayISO)return;
  dvDay=s;loadDayView();
}
async function loadDayView(){
  const di=$('#dvDate');if(!di)return;
  di.value=dvDay;di.max=todayISO;
  $('#dvNext').disabled=(dvDay>=todayISO);
  const both=await Promise.all([jget('/api/dayview?day='+dvDay),jget('/api/now?day='+dvDay)]);
  const v=both[0],n=both[1];
  const box=$('#dvList');box.innerHTML='';
  $('#dvSum').textContent=v.entries.length?(v.entries.length+' logged'):'nothing logged';
  v.entries.forEach(e=>{
    const r=document.createElement('div');r.className='dvrow';
    r.innerHTML='<span class="t"></span><span class="tag"></span><div class="x"><b></b><span></span></div>';
    r.querySelector('.t').textContent=e.time||'--:--';
    const tg=r.querySelector('.tag');tg.textContent=e.kind;tg.classList.add('k-'+(DV_TAG[e.kind]||'x'));
    r.querySelector('.x b').textContent=e.title;
    r.querySelector('.x span').textContent=[e.sub,e.edited?'time edited':''].filter(Boolean).join(' · ');
    r.onclick=()=>dvEdit(r,e);
    box.appendChild(r);
  });
  const miss=[];
  (n.slots||[]).forEach(s=>s.rows.forEach(x=>{if(!x.status)miss.push([s,x]);}));
  const mb=$('#dvMiss');mb.innerHTML='';
  $('#dvMissHd').style.display=miss.length?'':'none';
  miss.forEach(p=>mb.appendChild(dvMissRow(p[0],p[1])));
}
function dvEdit(rowEl,e){
  const old=document.querySelector('.varpick');if(old)old.remove();
  const box=document.createElement('div');box.className='varpick';
  box.innerHTML='<p class="vt"></p><div class="vtm"><input type="date" class="dd"><input type="time" class="tt"></div>'+
    '<div class="vb three"><button type="button" class="cx">Cancel</button>'+
    '<button type="button" class="danger dl">Delete</button><button type="button" class="go">Save</button></div>';
  box.querySelector('.vt').textContent=e.title+' - set the real time';
  const dd=box.querySelector('.dd'),tt=box.querySelector('.tt');
  dd.value=dvDay;dd.max=todayISO;tt.value=e.time||'';
  box.querySelector('.cx').onclick=()=>box.remove();
  box.querySelector('.go').onclick=async()=>{
    if(!tt.value||!dd.value){toast('Pick a day and time');return;}
    try{
      const r=await post('/api/retime',{table:e.tbl,id:e.id,day:dd.value,time:tt.value});
      toast(r.unchanged?'No change':('Moved to '+tt.value+(dd.value!==dvDay?(' on '+dd.value):'')));
      loadDayView();
    }catch(err){toast(err.message);}
  };
  box.querySelector('.dl').onclick=async()=>{
    if(!confirm('Delete this entry?'))return;
    try{await post('/api/delete/'+e.tbl+'/'+e.id,{});toast('Deleted');loadDayView();}
    catch(err){toast(err.message);}
  };
  rowEl.parentNode.insertBefore(box,rowEl.nextSibling);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function dvMissRow(s,x){
  const w=document.createElement('div');w.className='dvmiss';
  w.innerHTML='<div class="mh"><span class="sl"></span><b></b></div><div class="vrow"></div>'+
    '<div class="vtm"><input type="time" class="tt"><button type="button" class="btn tiny ghost sk">Skipped</button>'+
    '<button type="button" class="btn tiny go">Taken</button></div>';
  w.querySelector('.sl').textContent=s.label;
  w.querySelector('b').textContent=x.name+(x.dose_text?(' '+x.dose_text):'');
  const tt=w.querySelector('.tt');
  tt.value=(dvDay===todayISO&&s.time>nowHM())?nowHM():s.time;
  const picked=[],vr=w.querySelector('.vrow');
  if(x.variants){
    x.variants.split('|').map(v=>v.trim()).filter(Boolean).forEach(v=>{
      const b=document.createElement('div');b.className='chip';b.textContent=v;
      b.onclick=()=>{const i=picked.indexOf(v);if(i>=0)picked.splice(i,1);else picked.push(v);
        b.classList.toggle('sel',picked.indexOf(v)>=0);};
      vr.appendChild(b);
    });
  }else vr.remove();
  const send=async st=>{
    if(!tt.value){toast('Pick a time');return;}
    if(st==='TAKEN'&&x.variants&&!picked.length){toast('Pick a dose');return;}
    try{
      await post('/api/now/dose',{med_id:x.med_id,sched_id:x.sched_id,status:st,day:dvDay,
        dtime:tt.value,dose_text:x.variants?picked.join(' + '):x.dose_text});
      toast((st==='TAKEN'?'Logged ':'Skipped ')+x.name+' at '+tt.value);loadDayView();
    }catch(err){toast(err.message);}
  };
  w.querySelector('.go').onclick=()=>send('TAKEN');
  w.querySelector('.sk').onclick=()=>send('SKIPPED');
  return w;
}
$('#dvPrev').onclick=()=>dvShift(-1);
$('#dvNext').onclick=()=>dvShift(1);
$('#dvDate').onchange=ev=>{
  const v=ev.target.value;
  if(v&&v<=todayISO){dvDay=v;loadDayView();}
};

'''

CSS_ADD = r'''/* GUTLOG_V350_PHASE_B -- time row, day view, backfill rows */
.vtm{display:flex;gap:8px;align-items:center;margin-top:10px}
.vtm .lb{font-size:13px;font-weight:700;color:var(--muted)}
.vtm input{flex:1;min-width:0;padding:9px;border:1.5px solid var(--line);border-radius:10px;font-size:15px;background:#fff;color:var(--ink)}
.vtm .btn{flex:0 0 auto;margin:0}
.vtm .btn.go,.vtm .btn.tm{background:var(--teal);border-color:var(--teal);color:#fff}
.vtm .btn.sk{background:#fff;color:var(--teal);border-color:#BAD2C8}
.dvnav{display:flex;gap:8px;align-items:center}
.dvnav input{flex:1;min-width:0;text-align:center;padding:9px;border:1.5px solid var(--line);border-radius:10px;font-size:15.5px;font-weight:700;background:#fff;color:var(--ink)}
.dvnav .btn{flex:0 0 auto;min-width:48px;font-size:22px;line-height:1;padding:7px 12px;margin:0}
.dvnav .btn:disabled{opacity:.35}
.dvrow{display:flex;gap:10px;align-items:flex-start;padding:11px 2px;border-top:1px solid var(--line);cursor:pointer}
.dvrow .t{flex:0 0 44px;font-size:14px;font-weight:700;color:var(--ink);padding-top:2px}
.dvrow .tag{flex:0 0 auto;font-size:10.5px;font-weight:800;letter-spacing:.4px;text-transform:uppercase;padding:4px 7px;border-radius:7px;background:var(--chip);color:var(--muted)}
.dvrow .x{min-width:0}
.dvrow .x b{display:block;font-size:15px}
.dvrow .x span{font-size:13px;color:var(--muted)}
.tag.k-dose{background:#E3F1EC;color:var(--teal)}
.tag.k-extra{background:#EFE7F8;color:#6A3FA8}
.tag.k-skip{background:#ECECEC;color:#666}
.tag.k-sym{background:#FBEDEC;color:var(--err)}
.tag.k-bp{background:#FFF3DC;color:#8A5A00}
.tag.k-meal{background:#E8F0FA;color:#2F5C8A}
.dvmiss{padding:10px 12px;border:1.5px dashed var(--line);border-radius:13px;margin:0 0 8px}
.dvmiss .mh{display:flex;gap:8px;align-items:baseline}
.dvmiss .sl{font-size:11.5px;font-weight:800;color:var(--teal-d);text-transform:uppercase;letter-spacing:.4px}
.dvmiss .vrow{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}
'''


def build_edits():
    E = []
    a = "GUTLOG_V341_PICKER " + PREV
    E.append(("version marker", a, a + " " + MARKER))

    a = "  variants TEXT DEFAULT '');\n\"\"\""
    n = ("  variants TEXT DEFAULT '');\n"
         "CREATE TABLE IF NOT EXISTS edits (\n"
         "  id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, rid INTEGER, old_day TEXT,\n"
         "  old_time TEXT, new_day TEXT, new_time TEXT, at TEXT);\n\"\"\"")
    E.append(("schema edits table", a, n))

    a = "# ------------------------------------------------------------------ review\n"
    E.append(("python routes", a, PY_ROUTES + a))

    a = ('    day = d.get("day") or today()\n'
         '    dtime = d.get("dtime") or now_hm()\n'
         '    sched_id = d.get("sched_id")\n')
    n = ('    day = d.get("day") or today()\n'
         '    dtime = d.get("dtime") or now_hm()\n'
         '    sched_id = d.get("sched_id")\n'
         '    # ' + MARKER + ' -- backfill arrives with a day and time; refuse the future\n'
         '    if not _valid_day(day):\n'
         '        return jsonify(ok=False, err="Pick a real date, not in the future."), 400\n'
         '    if not _valid_hm(dtime):\n'
         '        return jsonify(ok=False, err="Time must be HH:MM."), 400\n'
         '    if day == today() and dtime > now_hm():\n'
         '        return jsonify(ok=False, err="That time has not come yet today."), 400\n')
    E.append(("now/dose guards", a, n))

    a = '<section class="tab" id="tab-review">\n'
    E.append(("review day card", a, HTML_CARD))

    a = "  html+='<button type=\"button\" class=\"danger un\">Undo</button></div>';\n"
    n = a + ("  if(r.dose_id)html+='<div class=\"vtm\"><span class=\"lb\">Time</span>"
             "<input type=\"time\" class=\"tt\"><button type=\"button\" class=\"btn tiny tm\">Save time</button></div>';\n")
    E.append(("strip time row", a, n))

    a = "  box.querySelector('.vt').textContent=r.name+' - '+was;\n"
    n = a + r"""  const tt=box.querySelector('.tt');
  if(tt){
    tt.value=r.dtime||'';
    box.querySelector('.tm').onclick=async()=>{
      if(!tt.value){toast('Pick a time');return;}
      try{await post('/api/retime',{table:'doses',id:r.dose_id,day:nowData.day,time:tt.value});
        toast('Time set to '+tt.value);box.remove();loadNow();}
      catch(err){toast(err.message);}
    };
  }
"""
    E.append(("strip time handler", a, n))

    a = "        status:'TAKEN',day:nowData.day,dose_text:txt});"
    n = ("        status:'TAKEN',day:nowData.day,dose_text:txt,\n"
         "        dtime:(r.status==='TAKEN'&&r.dtime)?r.dtime:undefined});")
    E.append(("change dose keeps time", a, n))

    a = "async function loadReview(){\n"
    E.append(("day view js", a, JS_DAYVIEW + a + "  loadDayView();\n"))

    a = "const todayISO=new Date().toLocaleDateString('en-CA');\n"
    n = a + ("/* " + MARKER + " -- 'today' is fixed at load; a page left open overnight\n"
             "   would log the morning dose against yesterday. Reload on a new day. */\n"
             "document.addEventListener('visibilitychange',()=>{\n"
             "  if(!document.hidden&&new Date().toLocaleDateString('en-CA')!==todayISO)location.reload();\n"
             "});\n")
    E.append(("midnight reload", a, n))

    a = "@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n</style></head><body>"
    E.append(("css", a, CSS_ADD + a))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow",
               "buildNowStatics", "bindFolds", "loadDayView", "dvEdit",
               "dvMissRow", "loadReview"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("GutLog Phase B -> v3.5.0")
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
        print("FATAL: this file is not at v3.4.2. Apply that first.")
        return 1
    if "def api_retime" in src or "CREATE TABLE IF NOT EXISTS edits" in src:
        print("FATAL: Phase B pieces already present -- unexpected state. Nothing written.")
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

    # APP_PAGE is a Jinja template: "{#", "{{" or "{%" in new text breaks
    # the whole page at render time, which a compile check cannot see.
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
    bak = args.file + ".bak-v350-" + stamp
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

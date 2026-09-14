#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.12.0 -> v3.13.0  ::  Phase J -- the watch display

The data was all arriving and being shown as one line: "10 min - 1,520 steps".
This is a reading problem, so nothing here ingests anything. A "Watch" card on
the Now screen reads FitLog's new read-only feed and puts the pieces where
they can be read against each other:

  A  TODAY strip -- steps, exercise minutes, standing load (hours, kept
     separate and never added to exercise), resting HR, HRV. Each carries a
     direction against HIS OWN trailing median over the window, not a generic
     goal: a ring target a 58-year-old with a replaced hip cannot meet is
     noise, and noise on a health screen teaches you to ignore it.
  B  FOURTEEN-DAY ROW -- one bar per day, steps. Under it two thin lanes: one
     marking every day carrying a logged pain entry, one marking every
     operating day. Load and pain are the comparison the whole screen exists
     to make, and they were in different applications.
  C  WORKOUTS -- real IST times, taken from the feed's own HH:MM field. No
     slicing of a timestamp anywhere: that was the 2026-09-13 bug.
  D  EPOCH BAND -- medication epochs drawn across the same row, because
     resting HR and HRV inside a drug change are artefacts of the change.
     Visible on the chart rather than left to memory.
  E  SOURCE HONESTY -- steps arrive from two feeds and the larger wins, so
     the day's figure says quietly which feed answered. A day with no data
     reads "no data", never zero.

Deliberately absent: readiness, recovery, body battery, fitness age, or any
other single number pretending to summarise a body. Wrist-sensor HR and HRV
accuracy depends on the underlying rhythm, so the screen shows inputs and
carries one quiet footnote. No banner, no verdict.

Requires v3.12.0 (GUTLOG_V3120_PAIN) and FitLog v1.5.0 for the feed; degrades
to a message when FitLog is unreachable. Anchor-verified, idempotent,
compile-checked, Jinja-safe, .bak before write, self-restoring. Python 3.9.
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
MARKER = "GUTLOG_V3130_WATCH"
PREV = "GUTLOG_V3120_PAIN"

PY = '''# ------------------------------------------------------------------ watch
# GUTLOG_V3130_WATCH -- the watch display. Everything below is read: FitLog
# holds the data, GutLog holds the pain and the operating days, and this is
# the only place the two are put side by side.
#
# No verdict is computed here and none should be added. A wrist sensor's HR
# and HRV accuracy depends on the underlying rhythm, so every number on this
# screen is an input. The footnote says so once, quietly.
WATCH_STRIP = ("steps", "exercise_minutes", "resting_hr", "hrv_ms")
WATCH_NOTE = ("Shown as inputs, not conclusions. Heart-rate and HRV figures "
              "from a wrist sensor depend on the underlying rhythm for their "
              "accuracy, so no readiness, recovery or fitness score is "
              "derived from them here.")


def _median(xs):
    s = sorted(v for v in xs if v is not None)
    n = len(s)
    if not n:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _direction(value, history, tol=0.05):
    """Against his own trailing median, never against a target.

    Returns (direction, median, n). direction is up / down / level, or None
    when there is nothing to compare against -- fewer than three days of
    history, or no figure today. None means "not enough to say", which is a
    different statement from "level" and must not be drawn as one.
    """
    med = _median(history)
    if value is None or med is None or len(history) < 3 or med == 0:
        return None, med, len(history)
    delta = (value - med) / float(abs(med))
    if delta > tol:
        return "up", med, len(history)
    if delta < -tol:
        return "down", med, len(history)
    return "level", med, len(history)


def _watch_load_by_day(since):
    """Operating hours per day, from GutLog's own activities. Load, never
    exercise: it is not added to exercise minutes here or anywhere."""
    marks = ",".join("?" for _ in LOAD_KINDS)
    rows = db().execute(
        "SELECT day, SUM(minutes) AS m FROM activities "
        "WHERE kind IN (" + marks + ") AND day>=? GROUP BY day",
        tuple(LOAD_KINDS) + (since,)).fetchall()
    return dict((r["day"], float(r["m"] or 0)) for r in rows)


@app.route("/api/watch")
@login_required
def api_watch():
    try:
        days = max(7, min(30, int(request.args.get("days") or 14)))
    except (TypeError, ValueError):
        days = 14
    tday = today()
    since = (date.today() - timedelta(days=days - 1)).isoformat()

    feed = _link_get(FITLOG_URL + "/api/feed/watch?days=" + str(days), ttl=60)
    linked = bool((feed or {}).get("ok"))
    daily = (feed or {}).get("daily") or []
    by_date = dict((d.get("date"), d) for d in daily)
    epochs = (feed or {}).get("epochs") or []

    pain = set(r["day"] for r in db().execute(
        "SELECT DISTINCT day FROM episodes WHERE category='pain' AND day>=?",
        (since,)).fetchall())
    load = _watch_load_by_day(since)

    def epoch_of(d):
        for e in epochs:
            s, en = e.get("date_start") or "", e.get("date_end") or ""
            if s and d < s:
                continue
            if en and d > en:
                continue
            return e.get("label") or ""
        return ""

    row = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        info = by_date.get(d) or {}
        mets = info.get("metrics") or {}
        st = mets.get("steps") or {}
        row.append({"date": d,
                    "steps": st.get("value"),
                    "source": st.get("source") or "",
                    "has_data": bool(info.get("has_data")),
                    "pain": d in pain,
                    "ot": d in load,
                    "ot_hours": (round(load[d] / 60.0, 1) if d in load else None),
                    "epoch": epoch_of(d)})

    def history(metric):
        out = []
        for r in daily:
            if r.get("date") == tday:
                continue
            m = (r.get("metrics") or {}).get(metric) or {}
            if m.get("value") is not None:
                out.append(float(m["value"]))
        return out

    tmets = (by_date.get(tday) or {}).get("metrics") or {}
    strip = {}
    for k in WATCH_STRIP:
        cur = tmets.get(k) or {}
        v = float(cur["value"]) if cur.get("value") is not None else None
        d_, med, n = _direction(v, history(k))
        strip[k] = {"value": v, "source": cur.get("source") or "",
                    "dir": d_, "median": med, "n": n}
    lh = load.get(tday)
    d_, med, n = _direction(
        (lh / 60.0) if lh is not None else None,
        [v / 60.0 for k2, v in load.items() if k2 != tday])
    strip["load_hours"] = {"value": (round(lh / 60.0, 1) if lh is not None else None),
                           "source": "gutlog", "dir": d_, "median": med, "n": n}

    wk = [w for w in ((feed or {}).get("workouts") or []) if w.get("date", "") >= since]
    wk.sort(key=lambda w: (w.get("date", ""), w.get("start_hm", "")), reverse=True)
    return jsonify(ok=True, day=tday, days=days, since=since, link=linked,
                   strip=strip, row=row, workouts=wk[:8], epochs=epochs,
                   note=WATCH_NOTE,
                   err="" if linked else "FitLog is not answering, so the watch "
                                          "figures are not available right now.")


'''

PY_ANCHOR = "# ------------------------------------------------------------------ records\n"

CSS = """/* GUTLOG_V3130_WATCH -- today strip, fourteen-day row, epoch band */
.wkstrip{display:flex;flex-wrap:wrap;gap:8px}
.wktile{flex:1 1 30%;min-width:96px;border:1px solid var(--line);border-radius:11px;padding:8px 9px}
.wktile .wl{font-size:11px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.wktile .wv{font-size:19px;font-weight:800;line-height:1.15}
.wktile .wv.none{font-size:14px;font-weight:600;color:var(--muted)}
.wktile .wd{font-size:11.5px;color:var(--muted);margin-top:1px}
.wktile .ar{font-weight:800}
.wktile.load{border-style:dashed}
.wkrow{display:flex;gap:3px;align-items:flex-end;margin:10px 0 0;height:64px}
.wkcol{flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;height:100%;position:relative}
.wkcol .bar{background:var(--teal);border-radius:3px 3px 0 0;min-height:2px}
.wkcol.nodata .bar{background:repeating-linear-gradient(45deg,#DCE5E2,#DCE5E2 3px,transparent 3px,transparent 6px);
  height:100%;border-radius:3px;opacity:.8}
.wkcol.today .bar{background:var(--teal-d)}
.wkep{display:flex;gap:3px;height:5px;margin-top:3px}
.wkep .seg{flex:1 1 0;border-radius:2px;background:transparent}
.wkep .seg.on{background:#C9A227}
.wklane{display:flex;gap:3px;height:9px;margin-top:3px;align-items:center}
.wklane .mk{flex:1 1 0;height:7px;border-radius:2px;background:transparent}
.wklane.pain .mk.on{background:var(--err)}
.wklane.ot .mk.on{background:#6A3FA8}
.wkkey{display:flex;flex-wrap:wrap;gap:10px;font-size:11.5px;color:var(--muted);margin-top:7px}
.wkkey i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.wkdates{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:3px}
.wknote{font-size:11.5px;color:var(--muted);margin:10px 0 0;line-height:1.45}
.wkwo{display:flex;gap:8px;padding:7px 2px;border-top:1px solid var(--line);font-size:13.5px}
.wkwo .wt{flex:0 0 84px;color:var(--muted);font-size:12.5px}
"""

CSS_ANCHOR = ("@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n"
              "</style></head><body>")

HTML = """
  <div class="card fold" id="nowWatch">
    <button type="button" class="fold-h">
      <span class="ft">Watch</span><span class="fs" id="wkSum">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody">
      <div id="wkStrip" class="wkstrip"></div>
      <div id="wkChart"></div>
      <div id="wkWork"></div>
      <p class="wknote" id="wkNote"></p>
    </div>
  </div>
"""

HTML_ANCHOR = ('      <p class="hint" style="margin:0 0 10px">Tap the site, score it, say what you did.\n'
               '        Tap <b>eased</b> when it settles and the duration is measured, not guessed.</p>\n'
               '      <div id="n_msk"></div>\n'
               '      <div id="painList"></div>\n'
               '    </div>\n'
               '  </div>\n')

JS = """/* GUTLOG_V3130_WATCH -- the watch screen. Reading only: every figure comes
   from FitLog's feed or from GutLog's own pain and operating-day rows, and
   nothing here computes a verdict. Arrows are against his own trailing
   median, so they say "more than usual for you", not "short of a target". */
const WK_LABEL={steps:'Steps',exercise_minutes:'Exercise',
  load_hours:'On his legs',resting_hr:'Resting HR',hrv_ms:'HRV'};
const WK_UNIT={steps:'',exercise_minutes:' min',load_hours:' h',
  resting_hr:' bpm',hrv_ms:' ms'};
const WK_ARROW={up:'\\u2191',down:'\\u2193',level:'\\u2192'};

function wkNum(v,k){
  if(v===null||v===undefined)return '';
  if(k==='steps')return Number(Math.round(v)).toLocaleString('en-IN');
  if(k==='load_hours')return String(Math.round(v*10)/10);
  return String(Math.round(v));
}

function wkTile(k,d){
  const w=document.createElement('div');
  w.className='wktile'+(k==='load_hours'?' load':'');
  w.dataset.k=k;
  w.innerHTML='<p class="wl"></p><p class="wv"></p><p class="wd"></p>';
  w.querySelector('.wl').textContent=WK_LABEL[k]||k;
  const vv=w.querySelector('.wv');
  if(d.value===null||d.value===undefined){
    vv.textContent='no data';
    vv.classList.add('none');
  }else{
    vv.textContent=wkNum(d.value,k)+(WK_UNIT[k]||'');
  }
  const bits=[];
  if(d.dir&&d.median!==null&&d.median!==undefined){
    bits.push(WK_ARROW[d.dir]+' vs '+wkNum(d.median,k)+' median of '+d.n+'d');
  }else if(d.value!==null&&d.value!==undefined){
    bits.push('not enough history to compare');
  }
  if(d.source&&d.source!=='gutlog'&&d.value!==null&&d.value!==undefined){
    bits.push(d.source==='applewatch'?'watch':d.source);
  }
  if(k==='load_hours')bits.push('load, not exercise');
  w.querySelector('.wd').textContent=bits.join(' \\u00b7 ');
  return w;
}

function wkChart(row){
  const box=document.createElement('div');
  const vals=row.filter(r=>r.has_data&&r.steps).map(r=>r.steps);
  const top=vals.length?Math.max.apply(null,vals):0;
  const bars=document.createElement('div');bars.className='wkrow';
  row.forEach(r=>{
    const c=document.createElement('div');
    c.className='wkcol'+(r.has_data?'':' nodata')+(r.date===todayISO?' today':'');
    c.dataset.d=r.date;
    const b=document.createElement('div');b.className='bar';
    if(r.has_data&&top>0&&r.steps){
      b.style.height=Math.max(3,Math.round(r.steps/top*100))+'%';
    }
    c.appendChild(b);
    const bits=[r.date];
    bits.push(r.has_data?((r.steps?wkNum(r.steps,'steps'):'0')+' steps'+
      (r.source?(' \\u00b7 '+(r.source==='applewatch'?'watch':r.source)):'')):'no data');
    if(r.ot)bits.push(r.ot_hours+' h on his legs');
    if(r.pain)bits.push('pain logged');
    if(r.epoch)bits.push(r.epoch);
    c.title=bits.join(' \\u00b7 ');
    bars.appendChild(c);
  });
  box.appendChild(bars);
  const ep=document.createElement('div');ep.className='wkep';
  row.forEach(r=>{
    const s=document.createElement('div');
    s.className='seg'+(r.epoch?' on':'');
    if(r.epoch)s.title=r.epoch;
    ep.appendChild(s);
  });
  box.appendChild(ep);
  [['pain','pain'],['ot','ot']].forEach(p=>{
    const lane=document.createElement('div');lane.className='wklane '+p[1];
    row.forEach(r=>{
      const m=document.createElement('div');
      m.className='mk'+(r[p[0]]?' on':'');
      lane.appendChild(m);
    });
    box.appendChild(lane);
  });
  const dd=document.createElement('div');dd.className='wkdates';
  dd.innerHTML='<span></span><span></span>';
  dd.children[0].textContent=row.length?row[0].date.slice(5):'';
  dd.children[1].textContent=row.length?'today':'';
  box.appendChild(dd);
  const key=document.createElement('div');key.className='wkkey';
  key.innerHTML='<span><i style="background:var(--err)"></i>pain logged</span>'+
    '<span><i style="background:#6A3FA8"></i>operating day</span>'+
    '<span><i style="background:#C9A227"></i>medication epoch</span>'+
    '<span><i style="background:#DCE5E2"></i>no data</span>';
  box.appendChild(key);
  return box;
}

async function loadWatch(){
  const st=$('#wkStrip');
  if(!st)return;
  const j=await jget('/api/watch?days=14');
  st.innerHTML='';
  $('#wkChart').innerHTML='';
  $('#wkWork').innerHTML='';
  if(!j.link){
    $('#wkSum').textContent='not reachable';
    st.innerHTML='<p class="hint" style="margin:0"></p>';
    st.querySelector('.hint').textContent=j.err||'FitLog is not answering.';
    $('#wkNote').textContent=j.note||'';
    return;
  }
  ['steps','exercise_minutes','load_hours','resting_hr','hrv_ms'].forEach(k=>{
    if(j.strip&&j.strip[k])st.appendChild(wkTile(k,j.strip[k]));
  });
  const s=j.strip||{};
  const sum=[];
  if(s.steps&&s.steps.value!==null&&s.steps.value!==undefined)
    sum.push(wkNum(s.steps.value,'steps')+' steps');
  if(s.exercise_minutes&&s.exercise_minutes.value)
    sum.push(Math.round(s.exercise_minutes.value)+' min');
  if(s.load_hours&&s.load_hours.value)sum.push(s.load_hours.value+' h on legs');
  $('#wkSum').textContent=sum.length?sum.join(' \\u00b7 '):'no data today';
  $('#wkChart').appendChild(wkChart(j.row||[]));
  const ep=(j.epochs||[]).map(e=>e.label).filter(Boolean);
  if(ep.length){
    const p=document.createElement('p');
    p.className='wknote';
    p.textContent='Medication epoch in this window: '+ep.join('; ')+
      '. Resting HR and HRV inside a drug change are artefacts of the change.';
    $('#wkChart').appendChild(p);
  }
  const wl=$('#wkWork');
  (j.workouts||[]).forEach(w=>{
    const r=document.createElement('div');r.className='wkwo';
    r.innerHTML='<span class="wt"></span><span class="wm"></span>';
    r.querySelector('.wt').textContent=w.date.slice(5)+' '+(w.start_hm||'');
    const bits=[(w.kind||w.wtype||'workout')+' '+w.minutes+' min'];
    if(w.distance_km)bits.push(w.distance_km+' km');
    r.querySelector('.wm').textContent=bits.join(' \\u00b7 ');
    wl.appendChild(r);
  });
  $('#wkNote').textContent=j.note||'';
}

"""

JS_ANCHOR = "function buildNowStatics(){\n  bindFolds();\n"


def build_edits():
    E = []
    E.append(("version marker", PREV + "\n", PREV + " " + MARKER + "\n"))
    E.append(("python", PY_ANCHOR, PY + PY_ANCHOR))
    E.append(("css", CSS_ANCHOR, CSS + CSS_ANCHOR))
    E.append(("html", HTML_ANCHOR, HTML_ANCHOR + HTML))
    E.append(("js", JS_ANCHOR, JS + JS_ANCHOR))
    a = "  loadStockAlerts();\n  loadMedStatus();\n  loadActivity();\n  loadPain();\n"
    E.append(("loadNow calls loadWatch", a, a + "  loadWatch();\n"))
    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow", "buildNowStatics",
               "bindFolds", "loadDayView", "dvEdit", "dvMissRow", "loadReview", "loadStock",
               "loadStockAlerts", "stockRow", "renderVitals", "vitalsChart", "loadMedStatus",
               "loadSalts", "saltGuess", "buildActTiles", "loadActivity",
               "buildPainTiles", "loadPain", "loadWatch", "wkChart", "wkTile", "wkNum",
               "loadRecSummary", "loadRecDocs", "loadRecTrends", "loadRecPlan", "loadRecords", "spark"]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("GutLog Phase J: the watch display -> v3.13.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.12.0. Apply that first.")
        return 1
    if "def api_watch" in src:
        print("FATAL: watch pieces already present. Nothing written.")
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

    # Rule 5b: the page is a Jinja template. A stray {{ {% {# in new CSS or JS
    # breaks the whole page at render and still passes py_compile.
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
    bak = args.file + ".bak-v3130-" + stamp
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
    print("-" * 60)
    print("Next:  python3 test_phase_j.py app.py   then  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

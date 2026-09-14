#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.14.0 -> v3.15.0  ::  Phase K -- Watch card, correctness then readability

THREE CORRECTNESS FIXES

  1. VOICE. 'On his legs' was third person: it came from a brief written ABOUT
     him, not to him. Every user-facing string is now second person, and
     test_phase_j fails if a third-person pronoun reaches a rendered string.
     The briefs will keep that voice, so the guard belongs in the code.

  2. CUMULATIVE vs SETTLED, marked in ONE place (WATCH_KIND).
     A part-day total measured against whole-day medians is not a comparison:
     it points down every morning by construction, which is how "62 steps,
     down against a median of 1,214" got onto the screen at 07:53.
       cumulative (steps, exercise minutes, standing load)
         -> headline is the last COMPLETE day, with its arrow and a median
            that excludes that day; today's running figure sits underneath,
            labelled "so far today", with NO arrow.
       settled (resting HR, HRV)
         -> a reading, not a total: today when it exists, else yesterday.
     No time-of-day threshold anywhere. The distinction is in the shape of the
     quantity, not in the clock.

  3. EVERY FIGURE NAMES ITS DAY. Signalling "today" by the absence of a label
     meant '62 steps - 19 min - yesterday' read as though both were yesterday's.
     Each tile carries its own day, and the header names the day of every
     figure it shows.

READABILITY -- the root cause was measured, not felt: #wkStrip was five
116px tiles plus gaps = 612px inside a 368px strip, so it overflowed. One
full-width hero (steps) over a two-column grid gives every cell ~176px and
cannot scroll horizontally. Nothing below 14px; the four-fact meta string is
split into a comparison line and a provenance line; sources are named in
words; tiles get a real surface so they read as objects. Chart 64px -> 104px,
2px bar gaps, 4px rounded data-ends, a recessive baseline, tap-to-reveal per
bar because title= does nothing on a phone, and lane markers at 10px carrying
a SHAPE as well as a colour.

Colour was validated, not eyeballed: the marker set
(pain / operating day / epoch) passes every check of the data-viz validator on
the card surface -- lightness band, chroma floor, CVD separation (worst
all-pairs delta-E 15.6 deutan), normal-vision floor 19.1, contrast >= 3:1.
The palette is otherwise untouched. Direction is never colour-alone: the arrow
glyph carries it and the text stays ink.

DARK MODE IS NOT IN THIS PASS -- see the DOSSIER. Flipping these marks onto a
dark ground FAILS the validator (two outside the dark lightness band, three
below 3:1), and the card shares one :root palette with every other screen, so
a real dark mode is an app-wide change with its own validation. Phase L.

Requires v3.14.0 (GUTLOG_V3140_FALLBACK). Anchor-verified, idempotent,
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
MARKER = "GUTLOG_V3150_READ"
PREV = "GUTLOG_V3140_FALLBACK"

# --------------------------------------------------------------- server side
PY_KIND_ANCHOR = '''WATCH_STRIP = ("steps", "exercise_minutes", "resting_hr", "hrv_ms")
'''

PY_KIND = '''WATCH_STRIP = ("steps", "exercise_minutes", "resting_hr", "hrv_ms")
# GUTLOG_V3150_READ -- what KIND of quantity each tile holds, in one place.
#
# cumulative: it accumulates through the day. A part-day total compared with
#   whole-day medians is not a comparison -- it points down every morning by
#   construction. So the headline is the last COMPLETE day and today's running
#   figure is shown separately, with no arrow on it.
# settled: it is a reading rather than a total. Today's stands as soon as it
#   exists, and falls back to yesterday when it does not.
#
# The distinction is in the shape of the quantity. Nothing here looks at the
# clock, and nothing here should start to.
WATCH_KIND = {"steps": "cumulative", "exercise_minutes": "cumulative",
              "load_hours": "cumulative",
              "resting_hr": "settled", "hrv_ms": "settled"}
'''

PY_OLD = '''    strip = {}
    for k in WATCH_STRIP + ("load_hours",):
        s = series.get(k) or {}
        # Today if it has a figure, else yesterday. He opens this between 5
        # and 7am; the phone syncs later, so at the hour he actually looks
        # today is usually still empty and a blank strip is correct and
        # useless. Falling back is always labelled, never silent.
        day = None
        for cand in (tday, yday):
            if s.get(cand) and s[cand].get("value") is not None:
                day = cand
                break
        cur = s.get(day) or {}
        v = float(cur["value"]) if cur.get("value") is not None else None
        # The median excludes the day being shown. Leave it in and the figure
        # is compared against a median it is itself inside, which on a
        # fallback day reads "level" every time.
        hist = [float(x["value"]) for d2, x in s.items()
                if d2 != day and x and x.get("value") is not None]
        d_, med, n = _direction(v, hist)
        strip[k] = {"value": v, "source": cur.get("source") or "",
                    "day": day or "", "stale": bool(day) and day != tday,
                    "dir": d_, "median": med, "n": n}
'''

PY_NEW = '''    strip = {}
    for k in WATCH_STRIP + ("load_hours",):
        s = series.get(k) or {}
        kind = WATCH_KIND.get(k, "settled")
        if kind == "cumulative":
            # The headline is the last COMPLETE day. Today is still running,
            # so it cannot be set against whole-day medians without pointing
            # down every morning; it is reported separately below.
            cands = [d2 for d2 in s if d2 < tday]
            day = max(cands) if cands else None
        else:
            # A reading, not a total: today's is valid the moment it exists.
            # Falling back one day is always labelled, never silent -- the
            # phone syncs after the hour this screen is actually read.
            day = None
            for cand in (tday, yday):
                if s.get(cand) and s[cand].get("value") is not None:
                    day = cand
                    break
        cur = s.get(day) or {}
        v = float(cur["value"]) if cur.get("value") is not None else None
        # The median excludes the day being shown. Leave it in and the figure
        # is compared against a median it is itself inside, which on a
        # fallback day reads "level" every time.
        hist = [float(x["value"]) for d2, x in s.items()
                if d2 != day and x and x.get("value") is not None]
        d_, med, n = _direction(v, hist)
        run = None
        if kind == "cumulative":
            t = s.get(tday) or {}
            if t.get("value") is not None:
                run = {"value": float(t["value"]), "day": tday,
                       "source": t.get("source") or ""}
        strip[k] = {"kind": kind, "value": v, "source": cur.get("source") or "",
                    "day": day or "", "stale": bool(day) and day != tday,
                    "dir": d_, "median": med, "n": n, "today": run}
'''

# ---------------------------------------------------------------------- css
CSS_OLD = """/* GUTLOG_V3130_WATCH -- today strip, fourteen-day row, epoch band */
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

CSS_NEW = """/* GUTLOG_V3150_READ -- Watch card, read on a phone at 5am.
   Nothing below 14px. The old strip was five 116px tiles plus gaps = 612px
   inside a 368px box, so it overflowed; block + a two-column grid cannot.
   Every figure uses tabular-nums so digits stop jittering between refreshes. */
.wkstrip{display:block}
.wkgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.wktile{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px;min-width:0}
.wktile .wl{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  font-size:14px;font-weight:600;color:var(--muted);margin:0}
.wktile .wl .wday{font-weight:500;white-space:nowrap}
.wktile .wv{font-size:24px;font-weight:700;line-height:1.2;margin:4px 0 0;
  font-variant-numeric:tabular-nums}
.wktile.hero .wv{font-size:32px}
.wktile .wv.none{font-size:16px;font-weight:600;color:var(--muted)}
.wktile .wc{font-size:15px;font-weight:500;color:var(--ink);margin:4px 0 0;
  font-variant-numeric:tabular-nums}
.wktile .wp{font-size:14px;color:var(--muted);margin:2px 0 0}
.wktile .wr{font-size:14px;color:var(--muted);margin:6px 0 0;
  font-variant-numeric:tabular-nums}
/* the chart. 104px so fourteen bars have a readable shape; 2px between
   adjacent bars; 4px rounded data-ends, square to the baseline. */
.wkrow{display:flex;gap:2px;align-items:flex-end;margin:14px 0 0;height:104px;
  border-bottom:1px solid var(--line)}
.wkcol{flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;
  height:100%;min-width:0;cursor:pointer;background:none;border:0;padding:0}
.wkcol .bar{background:var(--teal);border-radius:4px 4px 0 0;min-height:3px}
.wkcol.nodata .bar{height:100%;border-radius:0;
  background:repeating-linear-gradient(45deg,#E4ECE9,#E4ECE9 2px,transparent 2px,transparent 5px)}
.wkcol.today .bar{background:var(--teal2)}
.wkcol.sel .bar{outline:2px solid var(--ink);outline-offset:1px}
.wkep{display:flex;gap:2px;height:6px;margin-top:4px}
.wkep .seg{flex:1 1 0;border-radius:2px;background:transparent}
.wkep .seg.on{background:#C8860A}
/* lanes: 10px marks, and a SHAPE as well as a colour -- pain is a disc,
   an operating day is a diamond, so neither is colour-alone */
.wklane{display:flex;gap:2px;height:12px;margin-top:4px;align-items:center}
.wklane .mk{flex:1 1 0;height:10px;position:relative}
.wklane .mk.on::after{content:"";position:absolute;left:50%;top:50%;
  width:10px;height:10px;margin:-5px 0 0 -5px}
.wklane.pain .mk.on::after{background:var(--err);border-radius:50%;}
.wklane.ot .mk.on::after{background:#6A3FA8;transform:rotate(45deg);border-radius:1px}
.wkkey{display:flex;flex-wrap:wrap;gap:12px;font-size:14px;color:var(--muted);margin-top:10px}
.wkkey i{display:inline-block;width:10px;height:10px;margin-right:5px;vertical-align:-1px}
.wkkey i.pain{background:var(--err);border-radius:50%;}
.wkkey i.ot{background:#6A3FA8;transform:rotate(45deg);border-radius:1px}
.wkkey i.ep{background:#C8860A;border-radius:2px}
.wkkey i.nd{background:repeating-linear-gradient(45deg,#E4ECE9,#E4ECE9 2px,transparent 2px,transparent 5px);
  border:1px solid var(--line)}
.wkdates{display:flex;justify-content:space-between;font-size:14px;color:var(--muted);margin-top:6px}
.wkpick{font-size:15px;color:var(--ink);margin:8px 0 0;min-height:21px;
  font-variant-numeric:tabular-nums}
.wknote{font-size:14px;color:var(--muted);margin:12px 0 0;line-height:1.5}
.wkwo{display:flex;gap:10px;padding:10px 2px;border-top:1px solid var(--line);font-size:15px}
.wkwo .wt{flex:0 0 96px;color:var(--muted);font-size:14px;font-variant-numeric:tabular-nums}
"""

# ----------------------------------------------------------------------- js
JS_OLD_START = "const WK_LABEL={steps:'Steps',exercise_minutes:'Exercise',"

JS_NEW = """const WK_LABEL={steps:'Steps',exercise_minutes:'Exercise',
  load_hours:'On your legs',resting_hr:'Resting HR',hrv_ms:'HRV'};
const WK_UNIT={steps:'',exercise_minutes:' min',load_hours:' h',
  resting_hr:' bpm',hrv_ms:' ms'};
const WK_ARROW={up:'\\u2191',down:'\\u2193',level:'\\u2192'};
/* sources in words. A slug on a screen is a note to the person who wrote it */
const WK_SOURCE={applewatch:'Apple Watch',healthconnect:'Health Connect',
  gutlog:'logged'};

function wkNum(v,k){
  if(v===null||v===undefined)return '';
  if(k==='steps')return Number(Math.round(v)).toLocaleString('en-IN');
  if(k==='load_hours')return String(Math.round(v*10)/10);
  return String(Math.round(v));
}

/* every figure names its day; signalling today by the absence of a label is
   how '62 steps - 19 min - yesterday' came to read as all yesterday's */
function wkDay(d){
  if(!d)return '';
  if(d===todayISO)return 'today';
  const y=new Date(todayISO+'T12:00:00');
  y.setDate(y.getDate()-1);
  if(d===y.toLocaleDateString('en-CA'))return 'yesterday';
  return new Date(d+'T12:00:00').toLocaleDateString('en-GB',
    {weekday:'short',day:'numeric',month:'short'});
}

function wkSrc(s){
  if(!s)return '';
  return WK_SOURCE[s]||s;
}

function wkTile(k,d,hero){
  const w=document.createElement('div');
  w.className='wktile'+(hero?' hero':'');
  w.dataset.k=k;
  w.dataset.kind=d.kind||'';
  w.innerHTML='<p class="wl"><span class="wn"></span><span class="wday"></span></p>'+
    '<p class="wv"></p><p class="wc"></p><p class="wp"></p><p class="wr"></p>';
  w.querySelector('.wn').textContent=WK_LABEL[k]||k;
  const has=(d.value!==null&&d.value!==undefined);
  w.querySelector('.wday').textContent=has?wkDay(d.day):'';
  const vv=w.querySelector('.wv');
  if(has){
    vv.textContent=wkNum(d.value,k)+(WK_UNIT[k]||'');
  }else{
    vv.textContent='no data';
    vv.classList.add('none');
  }
  /* line 1: the comparison, in ink. Direction is never colour-alone -- the
     glyph carries it and the text stays ink. */
  const cmp=w.querySelector('.wc');
  if(has&&d.dir&&d.median!==null&&d.median!==undefined){
    cmp.textContent=WK_ARROW[d.dir]+' '+wkNum(d.value,k)+
      ' \\u00b7 typical '+wkNum(d.median,k);
  }else if(has){
    cmp.textContent=d.n?('only '+d.n+' days to compare with'):'nothing yet to compare with';
  }
  /* line 2: where it came from, and how thin the baseline is */
  const prov=[];
  if(has&&d.n)prov.push(d.n+(d.n===1?' day':' days'));
  if(has&&d.source)prov.push(wkSrc(d.source));
  if(k==='load_hours')prov.push('load, not exercise');
  w.querySelector('.wp').textContent=prov.join(' \\u00b7 ');
  /* today's running total, under the settled headline, with NO arrow: a
     part-day figure has nothing valid to be compared against */
  const run=w.querySelector('.wr');
  if(d.kind==='cumulative'&&d.today&&d.today.value!==null&&
     d.today.value!==undefined){
    run.textContent=wkNum(d.today.value,k)+(WK_UNIT[k]||'')+' so far today';
  }
  return w;
}
"""

JS_CHART_OLD_START = "function wkChart(row){"

JS_CHART_NEW = """function wkChart(row){
  const box=document.createElement('div');
  const vals=row.filter(r=>r.has_data&&r.steps).map(r=>r.steps);
  const top=vals.length?Math.max.apply(null,vals):0;
  const bars=document.createElement('div');bars.className='wkrow';
  const pick=document.createElement('p');pick.className='wkpick';
  pick.textContent='Tap a bar for that day.';
  row.forEach(r=>{
    const c=document.createElement('button');
    c.type='button';
    c.className='wkcol'+(r.has_data?'':' nodata')+(r.date===todayISO?' today':'');
    c.dataset.d=r.date;
    const b=document.createElement('div');b.className='bar';
    if(r.has_data&&top>0&&r.steps){
      b.style.height=Math.max(4,Math.round(r.steps/top*100))+'%';
    }
    c.appendChild(b);
    const bits=[wkDay(r.date)];
    bits.push(r.has_data?((r.steps?wkNum(r.steps,'steps'):'0')+' steps'+
      (r.source?(' \\u00b7 '+wkSrc(r.source)):'')):'no data');
    if(r.ot)bits.push(r.ot_hours+' h on your legs');
    if(r.pain)bits.push('pain logged');
    if(r.epoch)bits.push(r.epoch);
    const txt=bits.join(' \\u00b7 ');
    c.title=txt;
    c.setAttribute('aria-label',txt);
    /* title= does nothing on a phone, so the day is tapped, not hovered */
    c.onclick=()=>{
      [...bars.children].forEach(x=>x.classList.remove('sel'));
      c.classList.add('sel');
      pick.textContent=txt;
    };
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
  dd.children[0].textContent=row.length?wkDay(row[0].date):'';
  dd.children[1].textContent=row.length?'today':'';
  box.appendChild(dd);
  box.appendChild(pick);
  /* one bar series, so the title names it and it needs no legend; the lanes
     and the band are separate encodings and do need one. Shape as well as
     colour on every marker. */
  const key=document.createElement('div');key.className='wkkey';
  key.innerHTML='<span><i class="pain"></i>pain logged</span>'+
    '<span><i class="ot"></i>operating day</span>'+
    '<span><i class="ep"></i>medication epoch</span>'+
    '<span><i class="nd"></i>no data</span>';
  box.appendChild(key);
  return box;
}
"""

JS_LOAD_OLD_START = "async function loadWatch(){"

JS_LOAD_NEW = """async function loadWatch(){
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
  const s=j.strip||{};
  /* one hero, then two per row. Five across overflowed a 368px strip. */
  if(s.steps)st.appendChild(wkTile('steps',s.steps,true));
  const grid=document.createElement('div');grid.className='wkgrid';
  ['exercise_minutes','load_hours','resting_hr','hrv_ms'].forEach(k=>{
    if(s[k])grid.appendChild(wkTile(k,s[k],false));
  });
  st.appendChild(grid);
  /* the header names the day of every figure it carries */
  const sum=[];
  const hd=(s.steps&&s.steps.day)||'';
  if(s.steps&&s.steps.value!==null&&s.steps.value!==undefined)
    sum.push(wkDay(hd)+' '+wkNum(s.steps.value,'steps')+' steps');
  if(s.exercise_minutes&&s.exercise_minutes.value&&
     s.exercise_minutes.day===hd)
    sum.push(Math.round(s.exercise_minutes.value)+' min');
  if(s.steps&&s.steps.today&&s.steps.today.value!==null&&
     s.steps.today.value!==undefined)
    sum.push('today '+wkNum(s.steps.today.value,'steps'));
  $('#wkSum').textContent=sum.length?sum.join(' \\u00b7 '):'no data yet';
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
    r.querySelector('.wt').textContent=wkDay(w.date)+' '+(w.start_hm||'');
    const bits=[(w.kind||w.wtype||'workout')+' '+w.minutes+' min'];
    if(w.distance_km)bits.push(w.distance_km+' km');
    r.querySelector('.wm').textContent=bits.join(' \\u00b7 ');
    wl.appendChild(r);
  });
  $('#wkNote').textContent=j.note||'';
}
"""


def slice_between(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def build_edits(src):
    E = []
    E.append(("version marker", PREV + "\n", PREV + " " + MARKER + "\n"))
    E.append(("watch kind", PY_KIND_ANCHOR, PY_KIND))
    E.append(("strip builder", PY_OLD, PY_NEW))
    E.append(("css", CSS_OLD, CSS_NEW))
    E.append(("js tiles", slice_between(src, JS_OLD_START, JS_CHART_OLD_START), JS_NEW + "\n"))
    E.append(("js chart", slice_between(src, JS_CHART_OLD_START, JS_LOAD_OLD_START),
              JS_CHART_NEW + "\n"))
    E.append(("js loadWatch", slice_between(src, JS_LOAD_OLD_START, "\nfunction buildNowStatics"),
              JS_LOAD_NEW.rstrip("\n")))
    a = ('      <div id="wkStrip" class="wkstrip"></div>\n'
         '      <div id="wkChart"></div>\n')
    E.append(("aria", a, a))
    return E


MUST_DEFINE = ["loadWatch", "wkChart", "wkTile", "wkNum", "wkDay", "wkSrc",
               "loadNow", "buildNowStatics", "loadPain", "buildActTiles",
               "loadActivity"]

BANNED = re.compile(r"\b(his|him|he)\b", re.I)


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
    print("GutLog Phase K: Watch card, correctness then readability -> v3.15.0")
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
        print("FATAL: this file is not at v3.14.0. Apply that first.")
        return 1

    try:
        edits = build_edits(src)
    except ValueError as exc:
        print("FATAL: could not locate a block to replace: " + str(exc))
        return 1

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

    # rule 5b -- the page is a Jinja template
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2

    # the voice gate, applied to what this patch itself writes: every quoted
    # string in the new CSS/JS must be second person
    for label, anchor, new in edits:
        if not label.startswith("js"):
            continue
        for lit in re.findall(r"'((?:[^'\\]|\\.)*)'", new):
            if BANNED.search(lit):
                print("VOICE: third person in a " + label + " string: " + lit)
                return 2

    if args.check:
        print("All anchors OK; new strings are second person.")
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
    bak = args.file + ".bak-v3150-" + stamp
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
    print("Next:  python3 test_phase_j.py   then  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

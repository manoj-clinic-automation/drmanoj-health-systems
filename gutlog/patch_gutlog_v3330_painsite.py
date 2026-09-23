#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.32.0 -> v3.33.0  ::  GUTLOG_V3330_PAINSITE -- where the pain was,
when it started, and one timeline in true time.

WHY: on 23-Sep a left-upper pain began before dinner, and the app could
record neither fact. The Symptom card had two pain tiles, and every episode
was stamped with the moment Save was pressed. The question for his
gastroenterologist is whether episodes start before or after meals.

WHAT
  * Symptom now: the nine abdominal regions as a 3 x 3 grid, as seen facing
    him ("your right | your left"), plus "Diffuse / whole abdomen". The list
    is ONE server constant, ABD_SITES, written into the page at render time
    -- not two copies. The two old labels are kept exactly, so history stays
    continuous. Tap a region: its 1-10 score row opens BELOW the grid; tap
    again: cleared. One episode per region, as before.
  * "Started at": a time box (the two lists every time box uses), default
    now, with Now / 15 min / 30 min / 1 h ago. It is saved as the episode's
    etime -- the time it happened. The save moment is already in `created`.
  * meal_relation(): for a GI episode, the nearest meal that day within 4 h
    -- "20 min before dinner", "1 h 10 min after dinner", "no meal logged
    within 4 h". Computed, never asked. Shown in Day by day and in the Food
    Test results; the Food Test page also counts this week's GI episodes as
    before a meal / after (0-3 h) / unrelated. Counts only.
  * The evening score: when pain > 0, the same grid (multi-select, no
    per-site score) and a "Started at" box -- both optional. Two columns on
    ft_score, through _migrate() AND SCHEMA (schema 3.3.5 -> 3.3.6).
  * Day by day becomes one timeline: Food Test steps at their time, scores
    at theirs, and a separate "Pain onset" entry at the onset. All retime
    from the same box, audited in `edits`; the Food Test ones go through the
    same code as /api/ft/retime (one-per-day rules, the dose's meal moving
    with it), and the onset row writes ft_score.onset only.

Nothing is backfilled. Anchor-verified, idempotent, compile-checked, .bak,
self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3330_PAINSITE"
PREV = "GUTLOG_V3320_FOODTEST"
VERSION = "3.33.0"

E = []

E.append(("header",
          'GUTLOG_V3320_FOODTEST -- the FODMAP reintroduction, run from the Now tab.\n',
          'GUTLOG_V3320_FOODTEST -- the FODMAP reintroduction, run from the Now tab.\n'
          'GUTLOG_V3330_PAINSITE -- nine-region pain site, onset time, one true-time day.\n'))
E.append(("version", 'APP_VERSION = "3.32.0"   # GUTLOG_V3320_FOODTEST ',
          'APP_VERSION = "3.33.0"   # GUTLOG_V3330_PAINSITE GUTLOG_V3320_FOODTEST '))
E.append(("schema version", 'SCHEMA_VERSION = "3.3.5"   # GUTLOG_V3310_FOODLIB ',
          'SCHEMA_VERSION = "3.3.6"   # GUTLOG_V3330_PAINSITE GUTLOG_V3310_FOODLIB '))
E.append(("ft_score schema",
          "  clear INTEGER DEFAULT 0, created TEXT, updated TEXT);\n",
          "  clear INTEGER DEFAULT 0, created TEXT, updated TEXT,\n"
          "  pain_sites TEXT DEFAULT '', onset TEXT DEFAULT '');\n"))
E.append(("ft_score columns",
          '    ("library", "source_ref", "TEXT DEFAULT \'\'"),\n]\n',
          '    ("library", "source_ref", "TEXT DEFAULT \'\'"),\n'
          '    # GUTLOG_V3330_PAINSITE -- where the evening pain was, and when it began.\n'
          '    ("ft_score", "pain_sites", "TEXT DEFAULT \'\'"),\n'
          '    ("ft_score", "onset", "TEXT DEFAULT \'\'"),\n]\n'))

# ------------------------------------------------ the shared retime core
E.append(("ft retime core",
          '''def api_ft_retime():
    """Change the day or time of a logged step or score. A dose's meal moves
    with it. Every change is recorded in `edits`, like /api/retime."""
    d = J()
    tbl = {"log": ("ft_log", "ltime"), "score": ("ft_score", "stime")}.get(d.get("table"))
''',
          '''def api_ft_retime():
    """Change the day or time of a logged step or score. A dose's meal moves
    with it. Every change is recorded in `edits`, like /api/retime."""
    return ft_retime_core(J())


def ft_retime_core(d):
    """The body of /api/ft/retime. GUTLOG_V3330_PAINSITE: Day by day's
    /api/retime calls this too for Food Test rows, so the one-per-day rules
    and the dose's meal moving with it cannot differ between the two."""
    tbl = {"log": ("ft_log", "ltime"), "score": ("ft_score", "stime")}.get(d.get("table"))
'''))

# ------------------------------------------------ score: sites and onset
E.append(("score fields",
          '''    clear = 1 if d.get("clear") else 0
    ex = db().execute("SELECT id FROM ft_score WHERE day=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    if ex:
        db().execute("UPDATE ft_score SET stime=?, pain=?, bloating=?, urgency=?, bristol=?, "
                     "clear=?, updated=? WHERE id=?",
                     (stime, vals["pain"], vals["bloating"], vals["urgency"], vals["bristol"],
                      clear, now_s(), ex["id"]))
        db().commit()
        sid = ex["id"]
    else:
        insert("ft_score", ["day", "stime", "pain", "bloating", "urgency", "bristol", "clear", "updated"],
               [day, stime, vals["pain"], vals["bloating"], vals["urgency"], vals["bristol"],
                clear, now_s()])
''',
          '''    clear = 1 if d.get("clear") else 0
    # GUTLOG_V3330_PAINSITE -- where and when, both optional, only with pain.
    sites, onset = "", ""
    if vals["pain"]:
        sites = "|".join(s for s in ABD_LABELS if s in (d.get("pain_sites") or []))
        onset = _valid_hm(d.get("onset")) or ""
    ex = db().execute("SELECT id FROM ft_score WHERE day=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    if ex:
        db().execute("UPDATE ft_score SET stime=?, pain=?, bloating=?, urgency=?, bristol=?, "
                     "clear=?, updated=?, pain_sites=?, onset=? WHERE id=?",
                     (stime, vals["pain"], vals["bloating"], vals["urgency"], vals["bristol"],
                      clear, now_s(), sites, onset, ex["id"]))
        db().commit()
        sid = ex["id"]
    else:
        insert("ft_score", ["day", "stime", "pain", "bloating", "urgency", "bristol", "clear", "updated",
                            "pain_sites", "onset"],
               [day, stime, vals["pain"], vals["bloating"], vals["urgency"], vals["bristol"],
                clear, now_s(), sites, onset])
'''))

E.append(("score text",
          '''def _ft_score_text(s):
    if not s:
        return "no score"
    return "pain %s · bloating %s · urgency %s · Bristol %s%s" % (
        s["pain"], "yes" if s["bloating"] else "no", "yes" if s["urgency"] else "no",
        s["bristol"], " · clear symptoms" if s["clear"] else "")
''',
          '''def _ft_score_text(s):
    if not s:
        return "no score"
    t = "pain %s · bloating %s · urgency %s · Bristol %s%s" % (
        s["pain"], "yes" if s["bloating"] else "no", "yes" if s["urgency"] else "no",
        s["bristol"], " · clear symptoms" if s["clear"] else "")
    # GUTLOG_V3330_PAINSITE -- where and when, when he said.
    sites = [abd_short(x) for x in (s.get("pain_sites") or "").split("|") if x]
    if sites:
        t += " · " + ", ".join(sites)
    if s.get("onset"):
        t += " · started " + s["onset"]
    return t
'''))

E.append(("results pain lines",
          '''            if r.get("score"):
                out.append('<div class="sced">score at %s %s</div>'
                           % (plan_esc(r["score"]["stime"] or ""),
                              _ft_edit("score", r["score"]["id"], r["day"], r["score"]["stime"] or "")))
            out.append('</div>')
''',
          '''            if r.get("score"):
                out.append('<div class="sced">score at %s %s</div>'
                           % (plan_esc(r["score"]["stime"] or ""),
                              _ft_edit("score", r["score"]["id"], r["day"], r["score"]["stime"] or "")))
            for ln in ft_day_pain_lines(r["day"], r.get("score")):   # GUTLOG_V3330_PAINSITE
                out.append('<div class="rel">' + plan_esc(ln) + '</div>')
            out.append('</div>')
'''))
E.append(("results summary",
          '    labels = dict(FT_OUTCOMES)\n    for b in blocks:\n',
          '    out.append(ft_onset_summary_html())   # GUTLOG_V3330_PAINSITE\n'
          '    labels = dict(FT_OUTCOMES)\n    for b in blocks:\n'))
E.append(("results css",
          '.sced{color:var(--muted);font-size:12.5px}\n',
          '.sced{color:var(--muted);font-size:12.5px}\n'
          '.rel{font-size:12.5px;color:var(--ink);margin-top:3px}\n'
          '.onsum{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 12px;'
          'margin:0 0 10px;font-size:13.5px}\n'
          '.onsum b{font-weight:700}\n'))

# ------------------------------------------------ Day by day
E.append(("time cols",
          '_TIME_COL = {"doses": "dtime", "episodes": "etime", "vitals": "vtime", "meals": "mtime", "activities": "atime"}\n',
          '_TIME_COL = {"doses": "dtime", "episodes": "etime", "vitals": "vtime", "meals": "mtime", "activities": "atime",\n'
          '             # GUTLOG_V3330_PAINSITE -- the Food Test rows in Day by day\n'
          '             "ft_log": "ltime", "ft_score": "stime", "ft_score_onset": "onset"}\n'))
E.append(("retime routes ft",
          '''    col = _TIME_COL.get(tbl)
    if not col:
        return jsonify(ok=False, err="Unknown entry type."), 400
''',
          '''    col = _TIME_COL.get(tbl)
    if not col:
        return jsonify(ok=False, err="Unknown entry type."), 400
    # GUTLOG_V3330_PAINSITE -- Food Test rows keep their own rules.
    if tbl in ("ft_log", "ft_score"):
        return ft_retime_core(dict(d, table="log" if tbl == "ft_log" else "score"))
    if tbl == "ft_score_onset":
        return ft_onset_retime(d)
'''))
E.append(("dayview meal relation",
          '''        add("episodes", r["id"], r["etime"], "Pain" if pain else "Symptom",
            title, " · ".join(parts), pain=pain, eased=bool(r["duration"]))
''',
          '''        if (r["category"] or "") in GI_CATEGORIES:   # GUTLOG_V3330_PAINSITE
            parts.append(meal_relation(day, r["etime"])[3])
        add("episodes", r["id"], r["etime"], "Pain" if pain else "Symptom",
            title, " · ".join(parts), pain=pain, eased=bool(r["duration"]))
'''))
E.append(("dayview food test rows",
          '    out.sort(key=lambda e: (e["time"] == "", e["time"], e["tbl"], e["id"]))\n'
          '    return jsonify(day=day, today=today(), entries=out)\n',
          '''    # GUTLOG_V3330_PAINSITE -- the Food Test in the same timeline.
    for r in db().execute("SELECT * FROM ft_log WHERE day=?", (day,)).fetchall():
        add("ft_log", r["id"], r["ltime"], "Food test", ft_log_title(r), "")
    for r in db().execute("SELECT * FROM ft_score WHERE day=?", (day,)).fetchall():
        add("ft_score", r["id"], r["stime"], "Score", "Evening score", _ft_score_text(dict(r)))
        if r["onset"]:
            sites = [abd_short(x) for x in (r["pain_sites"] or "").split("|") if x]
            add("ft_score_onset", r["id"], r["onset"], "Pain onset",
                ", ".join(sites) or "Pain (site not given)", meal_relation(day, r["onset"])[3])
    out.sort(key=lambda e: (e["time"] == "", e["time"], e["tbl"], e["id"]))
    return jsonify(day=day, today=today(), entries=out)
'''))

# ------------------------------------------------ the server code block
CODE = r'''# ------------------------------------------------------------ pain site
# GUTLOG_V3330_PAINSITE. The nine abdominal regions, as seen facing him, in
# grid order, plus diffuse. ONE list: the page gets it at render time. The
# first element is what is stored; the two old labels are kept exactly.
ABD_SITES = [("Right upper pain", "Right upper"), ("Epigastric pain", "Epigastrium"),
             ("Left upper pain", "Left upper"),
             ("Right flank pain", "Right flank"), ("Umbilical pain", "Umbilical"),
             ("Left flank pain", "Left flank"),
             ("Right iliac pain", "Right iliac"), ("Hypogastrium pain", "Hypogastrium"),
             ("Left iliac pain", "Left iliac"),
             ("Diffuse abdominal pain", "Diffuse / whole abdomen")]
ABD_LABELS = [a for a, _ in ABD_SITES]
GI_CATEGORIES = ("GI", "Gut")
MEAL_WINDOW_MIN = 240


def abd_short(label):
    return dict(ABD_SITES).get(label, label)


def _dur_text(mins):
    h, m = divmod(int(mins), 60)
    if not h:
        return "%d min" % m
    return ("%d h" % h) if not m else ("%d h %d min" % (h, m))


def meal_relation(day, hm, meals=None):
    """(kind, minutes, slot, text) -- the nearest meal that day within 4 h.
    kind is 'before' (the episode came first), 'after', 'with' or 'none'.
    Computed from what was logged; nothing here is asked or inferred."""
    t = _hm_min(hm) if _valid_hm(hm) else None
    if t is None:
        return ("none", None, "", "no time recorded")
    if meals is None:
        meals = [(r["mtime"], r["slot"]) for r in db().execute(
            "SELECT mtime, slot FROM meals WHERE day=?", (day,)).fetchall()]
    best = None
    for mt, slot in meals:
        m = _hm_min(mt) if _valid_hm(mt) else None
        if m is None or abs(m - t) > MEAL_WINDOW_MIN:
            continue
        if best is None or abs(m - t) < abs(best[0]):
            best = (m - t, slot)
    if best is None:
        return ("none", None, "", "no meal logged within 4 h")
    gap, slot = best
    s = (slot or "meal").lower()
    if gap > 0:
        return ("before", gap, s, "%s before %s" % (_dur_text(gap), s))
    if gap == 0:
        return ("with", 0, s, "at %s" % s)
    return ("after", -gap, s, "%s after %s" % (_dur_text(-gap), s))


def gi_episodes(day_from, day_to):
    return [dict(r) for r in db().execute(
        "SELECT id, day, etime, etype, severity FROM episodes WHERE day BETWEEN ? AND ? "
        "AND category IN ('GI','Gut') ORDER BY day, etime", (day_from, day_to)).fetchall()]


def ft_day_pain_lines(day, score=None):
    """The day's GI episodes and the evening score's onset, each with its
    relation to the nearest meal -- the lines under a Food Test result day."""
    out = []
    for e in gi_episodes(day, day):
        sev = (" %s/10" % e["severity"]) if e["severity"] not in (None, "") else ""
        out.append("%s%s at %s — %s" % (e["etype"], sev, e["etime"] or "?",
                                              meal_relation(day, e["etime"])[3]))
    if score and score.get("onset"):
        out.append("evening-score pain started %s — %s"
                   % (score["onset"], meal_relation(day, score["onset"])[3]))
    return out


def ft_onset_counts(day_from, day_to):
    """GI episodes starting before a meal / after one (0-3 h) / neither.
    Counts only."""
    n = {"before": 0, "after": 0, "unrelated": 0}
    for e in gi_episodes(day_from, day_to):
        kind, mins, _s, _t = meal_relation(e["day"], e["etime"])
        if kind == "before":
            n["before"] += 1
        elif kind in ("after", "with") and mins is not None and mins <= 180:
            n["after"] += 1
        else:
            n["unrelated"] += 1
    return n


def ft_onset_summary_html():
    """This week's counts on the Food Test page: the current week's first
    logged step to today, or the last seven days before one exists."""
    it, cur, steps, rows, need, wait = ft_current()
    started = [r["day"] for r in rows if r["kind"] in FT_STEP_KINDS] if it is not None else []
    start = min(started) if started else (date.today() - timedelta(days=6)).isoformat()
    n = ft_onset_counts(start, today())
    return ('<div class="onsum"><b>Onset vs meals</b> since %s: before a meal <b>%d</b> · '
            'after a meal (0–3 h) <b>%d</b> · unrelated <b>%d</b></div>'
            % (plan_esc(plan_dmy(start)), n["before"], n["after"], n["unrelated"]))


def ft_log_title(r):
    it = [x for x in ft_items() if x["slug"] == r["slug"]]
    label = it[0].get("label") if it else (r["slug"] or "Food test")
    k = r["kind"]
    if k == "dose":
        return "%s %s %s" % (label, _nut_num(r["amount"]), r["unit"] or "g")
    if k == "washout":
        return "%s — washout day" % label
    if k == "dinner":
        return "Week 0 dinner" + ((" · " + r["size"]) if r["size"] else "") + \
               (" · cramp" if r["cramp"] else "")
    if k == "decline":
        return "%s — not clean, to washout" % label
    if k == "skip":
        return "Skipped" + ((" (" + r["reason"] + ")") if r["reason"] else "")
    if k == "pause":
        return "Paused" + ((" (" + r["reason"] + ")") if r["reason"] else "")
    if k == "resume":
        return "Resumed"
    if k == "stop":
        return "%s — stopped, limit %s %s" % (label, _nut_num(r["amount"]), r["unit"] or "g")
    return label


def ft_onset_retime(d):
    """Move the evening score's pain onset. It stays on the score's day, and
    the change is recorded in `edits` like every other retime."""
    try:
        rid = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Bad entry."), 400
    row = db().execute("SELECT * FROM ft_score WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify(ok=False, err="Entry not found."), 404
    t = _valid_hm(d.get("time"))
    if not t:
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    if (d.get("day") or row["day"]) != row["day"]:
        return jsonify(ok=False, err="The pain onset stays on its score's day."), 400
    if row["day"] == today() and t > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400
    old = row["onset"] or ""
    if old == t:
        return jsonify(ok=True, unchanged=True)
    db().execute("UPDATE ft_score SET onset=? WHERE id=?", (t, rid))
    db().execute("INSERT INTO edits(tbl, rid, old_day, old_time, new_day, new_time, at) "
                 "VALUES(?,?,?,?,?,?,?)", ("ft_score_onset", rid, row["day"], old, row["day"], t, now_s()))
    db().commit()
    return jsonify(ok=True)


'''
E.append(("pain site code", '# ------------------------------------------------------------------ diet plan\n',
          CODE + '# ------------------------------------------------------------------ diet plan\n'))

E.append(("page gets the list",
          '    return render_template_string(APP_PAGE, course_chips=_course_chips())\n',
          '    # GUTLOG_V3330_PAINSITE -- the one region list, written in before render.\n'
          '    return render_template_string(APP_PAGE.replace("__ABD_SITES__", json.dumps(ABD_SITES)),\n'
          '                                  course_chips=_course_chips())\n'))

# ================================================================== the page
E.append(("symptom card",
          '''      <p class="lbl" style="margin-top:14px">Pain by site &mdash; tap to score</p>
      <div id="n_painSites"></div>
      <p class="lbl" style="margin-top:14px">Bristol (optional)</p>
      <div class="chips" id="n_symBristol"></div>
      <button type="button" class="btn primary" id="n_symSave" style="margin-top:14px">Save episode</button>
''',
          '''      <p class="lbl" style="margin-top:14px">Pain by site &mdash; tap a region, then score it</p>
      <div id="n_painSites"></div>
      <p class="lbl" style="margin-top:14px">Bristol (optional)</p>
      <div class="chips" id="n_symBristol"></div>
      <div class="vtm nstart"><span class="lb">Started at</span><input type="time" id="n_symStart"></div>
      <div class="chips" id="n_symAgo" style="margin-top:8px"></div>
      <button type="button" class="btn primary" id="n_symSave" style="margin-top:14px">Save episode</button>
'''))
E.append(("site list", "const PAIN_SITES=['Left iliac pain','Hypogastrium pain'];\n",
          "/* GUTLOG_V3330_PAINSITE -- written in by the server from ABD_SITES. */\n"
          "const ABD_SITES=__ABD_SITES__;\n"
          "const PAIN_SITES=ABD_SITES.map(x=>x[0]);\n"))
E.append(("grid builder",
          r'''  /* Pain by site: tap the tile to select it and open its score row,
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
''',
          r'''  /* GUTLOG_V3330_PAINSITE -- the nine regions as a grid, score rows below
     it; and "Started at", default now, which becomes the episode's time. */
  nPainDraw();
  const ag=$('#n_symAgo'),st=$('#n_symStart');
  st.value=nowHM();
  st.addEventListener('change',()=>{st.dataset.set='1';});
  [[0,'Now'],[15,'15 min ago'],[30,'30 min ago'],[60,'1 h ago']].forEach(a=>{
    const b=el('button','chip',a[1]);b.type='button';
    b.onclick=()=>{st.value=tpAgo(a[0]);st.dataset.set='1';
      ag.querySelectorAll('.chip').forEach(c=>c.classList.toggle('sel',c===b));};
    ag.appendChild(b);});
  const fh=$('#nowSym .fold-h');
  if(fh)fh.addEventListener('click',()=>{if(!st.dataset.set)st.value=nowHM();});
'''))
E.append(("save uses onset",
          r'''    const t=nowHM();
    try{
      for(const ty of nSym.types){
''',
          r'''    const t=($('#n_symStart')&&$('#n_symStart').value)||nowHM();   /* GUTLOG_V3330_PAINSITE */
    try{
      for(const ty of nSym.types){
'''))
E.append(("save resets",
          r'''      $$('#n_painSites .ptile').forEach(w=>{w.classList.remove('open');w.querySelector('.pv').textContent='';});
''',
          r'''      nPainDraw();
      const st=$('#n_symStart');if(st){delete st.dataset.set;st.value=nowHM();}
      $$('#n_symAgo .chip').forEach(c=>c.classList.remove('sel'));
'''))

E.append(("pain chip hook",
          "    b.onclick=()=>{ftSc[key]=o[0];w.querySelectorAll('.chip').forEach(x=>x.classList.toggle('sel',x===b));};\n",
          "    b.onclick=()=>{ftSc[key]=o[0];w.querySelectorAll('.chip').forEach(x=>x.classList.toggle('sel',x===b));\n"
          "      if(key==='pain')ftPainToggle();};\n"))
E.append(("score pain extra",
          "  w.appendChild(el('p','lbl','Pain'));w.appendChild(ftChips('pain',[0,1,2,3,4,5,6,7,8,9,10].map(i=>[i,String(i)])));\n",
          "  w.appendChild(el('p','lbl','Pain'));w.appendChild(ftChips('pain',[0,1,2,3,4,5,6,7,8,9,10].map(i=>[i,String(i)])));\n"
          "  w.appendChild(ftPainExtra());\n"))
E.append(("score save fields",
          r"""    try{const r=await post('/api/ft/score',{day:todayISO,time:$('#ftScTime').value||nowHM(),pain:ftSc.pain,
        bloating:ftSc.bloating,urgency:ftSc.urgency,bristol:ftSc.bristol,clear:ftSc.clear===1});""",
          r"""    try{const r=await post('/api/ft/score',{day:todayISO,time:$('#ftScTime').value||nowHM(),pain:ftSc.pain,
        bloating:ftSc.bloating,urgency:ftSc.urgency,bristol:ftSc.bristol,clear:ftSc.clear===1,
        pain_sites:ftSc.pain>0?(ftSc.sites||[]):[],onset:ftSc.pain>0?(($('#ftOnset')&&$('#ftOnset').value)||''):''});"""))
E.append(("score edit reopens",
          "    ed.onclick=()=>{ftScoreOpen=true;ftSc={pain:s.pain,bloating:s.bloating,urgency:s.urgency,bristol:s.bristol,clear:s.clear,stime:s.stime};loadFT();};\n",
          "    ed.onclick=()=>{ftScoreOpen=true;ftSc={pain:s.pain,bloating:s.bloating,urgency:s.urgency,bristol:s.bristol,clear:s.clear,stime:s.stime,\n"
          "      sites:(s.pain_sites||'').split('|').filter(Boolean),onset:s.onset||''};loadFT();};\n"))
E.append(("score summary",
          r"""      ' · urgency '+(s.urgency?'yes':'no')+' · Bristol '+s.bristol+(s.clear?' · clear symptoms':'')+' at'));""",
          r"""      ' · urgency '+(s.urgency?'yes':'no')+' · Bristol '+s.bristol+(s.clear?' · clear symptoms':'')+
      abdScoreText(s)+' at'));"""))

E.append(("dv tags",
          "const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',BP:'bp',Vitals:'bp',Meal:'meal',Activity:'act',Pain:'pain',Load:'load','Down day':'down'};\n",
          "const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',BP:'bp',Vitals:'bp',Meal:'meal',Activity:'act',Pain:'pain',Load:'load','Down day':'down',\n"
          "  'Food test':'ft',Score:'score','Pain onset':'onset'};   /* GUTLOG_V3330_PAINSITE */\n"))

E.append(("css",
          '.ftcard summary{cursor:pointer;color:var(--teal);font-weight:600}\n',
          '.ftcard summary{cursor:pointer;color:var(--teal);font-weight:600}\n'
          '/* GUTLOG_V3330_PAINSITE -- the region grid. Three columns inside 300px. */\n'
          '.abd{margin:4px 0 8px}\n'
          '.abdhd{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin:0 2px 4px}\n'
          '.abdgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}\n'
          '.abdt{border:1.5px solid var(--line);background:var(--chip);color:var(--ink);border-radius:10px;\n'
          '  padding:9px 3px;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;min-width:0;\n'
          '  display:flex;flex-direction:column;align-items:center;gap:2px;line-height:1.15;text-align:center}\n'
          '.abdt.diff{grid-column:1/-1}\n'
          '.abdt.sel{background:var(--teal);border-color:var(--teal);color:#fff}\n'
          '.abdt .pv{font-size:11.5px;font-weight:700}\n'
          '.abdscores .abdrow{margin-top:8px}\n'
          '.abdscores .lbl{margin:0 0 4px;font-weight:700;color:var(--ink)}\n'
          '.abdscores .chips{gap:5px}\n'
          '.abdscores .chip{padding:7px 0;min-width:30px;text-align:center}\n'
          '.nstart{margin-top:12px}\n'
          '.tag.k-ft{background:#E3F1EC;color:var(--teal)}\n'
          '.tag.k-score{background:var(--chip);color:var(--ink)}\n'
          '.tag.k-onset{background:#FBEDEC;color:var(--err)}\n'))

JS = r'''/* GUTLOG_V3330_PAINSITE -- the nine abdominal regions, one builder for the
   Symptom card and the evening score. isSel(site) says which are chosen;
   onTap(site) changes that and redraws. */
function abdGrid(isSel,onTap){
  const w=el('div','abd');
  const hd=el('div','abdhd');hd.appendChild(el('span','','your right'));hd.appendChild(el('span','','your left'));
  w.appendChild(hd);
  const g=el('div','abdgrid');
  ABD_SITES.forEach((x,i)=>{const b=document.createElement('button');b.type='button';
    b.className='abdt'+(i===ABD_SITES.length-1?' diff':'')+(isSel(x[0])?' sel':'');b.dataset.site=x[0];
    b.appendChild(el('span','pn',x[1]));b.appendChild(el('span','pv',''));
    b.onclick=()=>onTap(x[0]);g.appendChild(b);});
  w.appendChild(g);return w;
}
function abdShort(site){const x=ABD_SITES.find(a=>a[0]===site);return x?x[1]:site;}
/* The Symptom card: a region tapped opens its 1-10 row BELOW the grid. */
function nPainDraw(){
  const ps=$('#n_painSites');if(!ps)return;ps.innerHTML='';
  ps.appendChild(abdGrid(s=>s in nPain,s=>{if(s in nPain)delete nPain[s];else nPain[s]='';nPainDraw();}));
  ps.querySelectorAll('.abdt').forEach(b=>{const s=b.dataset.site;
    if(s in nPain)b.querySelector('.pv').textContent=nPain[s]?(nPain[s]+'/10'):'score?';});
  const sc=el('div','abdscores');
  Object.keys(nPain).forEach(s=>{const row=el('div','abdrow');row.dataset.site=s;
    row.appendChild(el('p','lbl',abdShort(s)));
    const ch=el('div','chips pscore');
    SEVS.forEach(v=>{const b=el('div','chip num'+(nPain[s]===v?' sel':''),v);
      b.onclick=()=>{nPain[s]=v;nPainDraw();};ch.appendChild(b);});
    row.appendChild(ch);sc.appendChild(row);});
  ps.appendChild(sc);
}
/* The evening score: with pain, where (several, no score each) and when. */
function ftPainExtra(){
  const w=el('div','ftpx');w.id='ftPainX';w.style.display=(ftSc.pain>0)?'':'none';
  if(!Array.isArray(ftSc.sites))ftSc.sites=[];
  w.appendChild(el('p','lbl','Where (optional)'));
  const tr=el('div','ftrow');tr.appendChild(el('span','lb','Started at (optional)'));
  const ti=document.createElement('input');ti.type='time';ti.id='ftOnset';ti.value=ftSc.onset||'';
  ti.addEventListener('change',()=>{ftSc.onset=ti.value;});tr.appendChild(ti);
  const draw=()=>{const g=abdGrid(s=>ftSc.sites.indexOf(s)>=0,s=>{
      const i=ftSc.sites.indexOf(s);if(i>=0)ftSc.sites.splice(i,1);else ftSc.sites.push(s);draw();});
    const old=w.querySelector('.abd');if(old)w.replaceChild(g,old);else w.insertBefore(g,tr);};
  w.appendChild(tr);draw();
  return w;
}
function ftPainToggle(){const x=$('#ftPainX');if(x)x.style.display=(ftSc.pain>0)?'':'none';}
function abdScoreText(s){
  const sites=(s.pain_sites||'').split('|').filter(Boolean).map(abdShort);
  return (sites.length?(' · '+sites.join(', ')):'')+(s.onset?(' · started '+s.onset):'');
}

'''
E.append(("page code", '/* ---------- boot ---------- */\n', JS + '/* ---------- boot ---------- */\n'))

EDITS = E
JINJA = ("{{", "{%", "{#")


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
    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("==================================================================")
    print("GutLog pain site, onset and one true-time day -> v" + VERSION)
    print("file : " + a.file)
    print("==================================================================")
    for label, old, new in EDITS:
        for tok in JINJA:
            if new.count(tok) > old.count(tok):
                print("FATAL: %s adds the Jinja token %r. Nothing written." % (label, tok))
                return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    bad = [(l, src.count(o)) for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        for l, c in bad:
            print("  %s: found %d times, need 1" % (l, c))
        print("Refusing to patch. Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    bak = a.file + ".bak-v3330-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3330_painsite.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

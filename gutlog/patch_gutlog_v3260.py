#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.25.0 -> v3.26.0  ::  food trials as periods (GUTLOG_V3260_TRIALS)

A food trial becomes a period: food, amount, how often, start, planned
length (1 week to 1 month). Meals in that period containing the food are
linked by name automatically. Details compare the trial days with the 14
days before (symptom days = any gut episode, down day or daily symptom;
average gut pain), with Day-context days and days with nothing logged set aside, and separately the days
it was eaten or the day after against other logged days. A word -- no
signal / possibly better / possibly worse / likely worse -- appears only
when each side has 8 counted days; until then "not enough days yet". The
forms eaten (e.g. boiled vs half-fried) are listed. He ends it with a
verdict (Tolerated / Not tolerated / Not sure) + note; Tolerated/Not
tolerated update the food map (library status of the matching foods) and a
recipe's stage (rotation / avoid / paused). Starting a trial of a recipe
sets its stage to "On trial".

UI: top of Meals -> Food test; the older one-day test stays below, as is.
Table `trials` via SCHEMA (no migration, no schema_version bump).
migrate_trials.py moves an owner-described existing test into a trial.

Requires v3.25.0 (GUTLOG_V3250_PLAN). Anchor-verified, idempotent,
compile-checked, refuses Jinja tokens in new page text, .bak before write,
self-restoring, --reverse. Python 3.9.
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
MARKER = "GUTLOG_V3260_TRIALS"
PREV = "GUTLOG_V3250_PLAN"

SERVER = '# ------------------------------------------------------------------ food trials\n# GUTLOG_V3260_TRIALS -- a food trial is a PERIOD, not a one-day entry: a food,\n# how much, how often, a start and a planned length. Meals eaten in that\n# period that contain the food are linked to it by name automatically -- no\n# separate daily test entry. The comparison is deterministic and labelled:\n# the trial days against the 14 days before it, days with a Day-context mark\n# and days with nothing logged at all set aside, and a signal word only once there are 8 counted days on each\n# side. He gives the verdict; the verdict updates the food map (library\n# status) and, when the food is one of his recipes, the recipe\'s stage.\nTRIAL_BASELINE_DAYS = 14\nTRIAL_MIN_DAYS = 8\nTRIAL_VERDICTS = {"tolerated": ("Tolerated", "cleared", "rotation"),\n                  "not_tolerated": ("Not tolerated", "trigger", "avoid"),\n                  "unsure": ("Not sure", None, "paused")}\n\n\ndef _trial_terms(t):\n    return [x.strip().lower() for x in (t["match"] or "").split("|") if x.strip()]\n\n\ndef _trial_hits(items, terms):\n    return sorted(set(it.get("n") or "" for it in items\n                      if (it.get("q") or 0) > 0 and any(w in (it.get("n") or "").lower() for w in terms)))\n\n\ndef _day_outcome(day):\n    gi = db().execute("SELECT MAX(COALESCE(severity,0)) AS m, COUNT(*) AS n FROM episodes "\n                      "WHERE day=? AND category=\'GI\'", (day,)).fetchone()\n    down = db().execute("SELECT 1 FROM down_days WHERE day=?", (day,)).fetchone()\n    dd = db().execute("SELECT syms FROM days WHERE day=?", (day,)).fetchone()\n    syms = bool(dd and (dd["syms"] or "").strip())\n    # a day counts only if the app was in use that day -- an empty day is not a good day\n    used = bool(gi["n"]) or bool(down) or bool(dd) or bool(\n        db().execute("SELECT 1 FROM doses WHERE day=? LIMIT 1", (day,)).fetchone()) or bool(\n        db().execute("SELECT 1 FROM meals WHERE day=? LIMIT 1", (day,)).fetchone())\n    return {"symptom": bool(gi["n"]) or bool(down) or syms, "pain": int(gi["m"] or 0), "used": used}\n\n\ndef _signal(a, b):\n    """a, b: (counted days, symptom days). Words, never a number dressed as certainty."""\n    if a[0] < TRIAL_MIN_DAYS or b[0] < TRIAL_MIN_DAYS:\n        return "not enough days yet"\n    d = 100.0 * a[1] / a[0] - 100.0 * b[1] / b[0]\n    if d >= 30:\n        return "likely worse"\n    if d >= 15:\n        return "possibly worse"\n    if d <= -15:\n        return "possibly better"\n    return "no signal"\n\n\ndef trial_view(t, on_day=None):\n    on_day = on_day or today()\n    terms = _trial_terms(t)\n    start = date.fromisoformat(t["start"])\n    planned_end = start + timedelta(days=int(t["days"] or 14) - 1)\n    stop = min(planned_end, date.fromisoformat(t["ended"] or on_day), date.fromisoformat(on_day))\n    base_from = start - timedelta(days=TRIAL_BASELINE_DAYS)\n    meals = {}\n    for r in db().execute("SELECT day, items FROM meals WHERE day>=? AND day<=?",\n                          (base_from.isoformat(), stop.isoformat())).fetchall():\n        meals.setdefault(r["day"], []).extend(json.loads(r["items"] or "[]"))\n    ctx = set(r["day"] for r in db().execute(\n        "SELECT DISTINCT day FROM day_context WHERE day>=? AND day<=?",\n        (base_from.isoformat(), stop.isoformat())).fetchall())\n    days, styles = [], {}\n    d = base_from\n    while d <= stop:\n        k = d.isoformat()\n        hits = _trial_hits(meals.get(k, []), terms)\n        for h in hits:\n            if k >= t["start"]:\n                styles[h] = styles.get(h, 0) + 1\n        o = _day_outcome(k)\n        days.append({"day": k, "phase": "trial" if k >= t["start"] else "before",\n                     "ate": bool(hits), "logged": o["used"], "context": k in ctx,\n                     "symptom": o["symptom"], "pain": o["pain"]})\n        d += timedelta(days=1)\n    # "exposed" = eaten that day or the day before (the 48 h the plan names)\n    for i, x in enumerate(days):\n        x["exposed"] = x["ate"] or (i > 0 and days[i - 1]["ate"])\n\n    def side(rows):\n        rows = [x for x in rows if not x["context"] and x["logged"]]\n        n = len(rows)\n        s = sum(1 for x in rows if x["symptom"])\n        pain = round(sum(x["pain"] for x in rows) / float(n), 1) if n else None\n        return {"days": n, "symptom_days": s, "pct": round(100.0 * s / n) if n else None,\n                "avg_gi_pain": pain}\n    tr = side([x for x in days if x["phase"] == "trial"])\n    bf = side([x for x in days if x["phase"] == "before"])\n    ex = side([x for x in days if x["exposed"]])\n    nx = side([x for x in days if not x["exposed"] and x["logged"]])\n    trial_days = [x for x in days if x["phase"] == "trial"]\n    return {"id": t["id"], "food": t["food"], "match": terms, "amount": t["amount"] or "",\n            "freq": t["freq"] or "", "start": t["start"], "planned_days": int(t["days"] or 14),\n            "planned_end": planned_end.isoformat(), "status": t["status"], "ended": t["ended"] or "",\n            "verdict": t["verdict"] or "", "verdict_note": t["verdict_note"] or "", "note": t["note"] or "",\n            "day_no": (min(stop, planned_end) - start).days + 1 if stop >= start else 0,\n            "ate_days": sum(1 for x in trial_days if x["ate"]),\n            "set_aside": sum(1 for x in trial_days if x["context"]),\n            "styles": sorted(styles.items(), key=lambda kv: -kv[1]),\n            "trial": tr, "before": bf, "signal": _signal((tr["days"], tr["symptom_days"]),\n                                                         (bf["days"], bf["symptom_days"])),\n            "exposed": ex, "not_exposed": nx,\n            "exposure_signal": _signal((ex["days"], ex["symptom_days"]),\n                                       (nx["days"], nx["symptom_days"])),\n            "min_days": TRIAL_MIN_DAYS, "baseline_days": TRIAL_BASELINE_DAYS}\n\n\n@app.route("/api/trials")\n@login_required\ndef api_trials():\n    rows = db().execute("SELECT * FROM trials ORDER BY status=\'active\' DESC, start DESC").fetchall()\n    return jsonify(trials=[trial_view(r) for r in rows],\n                   verdicts=[{"key": k, "label": v[0]} for k, v in TRIAL_VERDICTS.items()])\n\n\n@app.route("/api/trials", methods=["POST"])\n@login_required\ndef api_trial_start():\n    d = J()\n    food = (d.get("food") or "").strip()[:80]\n    if not food:\n        return jsonify(ok=False, err="Pick the food to trial."), 400\n    start = _valid_day(d.get("start") or today())\n    if not start:\n        return jsonify(ok=False, err="Pick a real start date, not in the future."), 400\n    try:\n        n = int(d.get("days") or 14)\n    except (TypeError, ValueError):\n        n = 14\n    if not 3 <= n <= 90:\n        return jsonify(ok=False, err="A trial runs 3 to 90 days."), 400\n    if db().execute("SELECT 1 FROM trials WHERE status=\'active\' AND LOWER(food)=LOWER(?)",\n                    (food,)).fetchone():\n        return jsonify(ok=False, err="A trial of that food is already running."), 400\n    match = (d.get("match") or food).strip().lower()[:200]\n    insert("trials", ["food", "match", "amount", "freq", "start", "days", "ended", "status",\n                      "verdict", "verdict_note", "note"],\n           [food, match, (d.get("amount") or "")[:60], (d.get("freq") or "")[:40], start, n, "",\n            "active", "", "", note(d, "note", 300)])\n    tid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]\n    try:\n        db().execute("UPDATE recipes SET stage=\'trial\', updated=? WHERE name=?", (now_s(), food))\n        db().commit()\n    except sqlite3.OperationalError:\n        pass\n    return jsonify(ok=True, id=tid)\n\n\n@app.route("/api/trials/<int:tid>/end", methods=["POST"])\n@login_required\ndef api_trial_end(tid):\n    d = J()\n    t = db().execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone()\n    if not t:\n        return jsonify(ok=False, err="No such trial."), 404\n    v = d.get("verdict")\n    if v not in TRIAL_VERDICTS:\n        return jsonify(ok=False, err="Pick a verdict."), 400\n    label, lib_status, stage = TRIAL_VERDICTS[v]\n    db().execute("UPDATE trials SET status=\'ended\', ended=?, verdict=?, verdict_note=? WHERE id=?",\n                 (t["ended"] or today(), label, note(d, "note", 300), tid))\n    changed = []\n    if lib_status:\n        for r in db().execute("SELECT id, item FROM library").fetchall():\n            if any(w in r["item"].lower() for w in _trial_terms(t)):\n                db().execute("UPDATE library SET status=? WHERE id=?", (lib_status, r["id"]))\n                changed.append(r["item"])\n    try:\n        db().execute("UPDATE recipes SET stage=?, updated=? WHERE name=?", (stage, now_s(), t["food"]))\n    except sqlite3.OperationalError:\n        pass\n    db().commit()\n    return jsonify(ok=True, verdict=label, library=changed)\n\n\n'
JS = "/* GUTLOG_V3260_TRIALS -- food trials as periods. */\nlet TR=null,trOpen=null;\nasync function loadTrials(){\n  try{TR=await jget('/api/trials');}catch(e){return;}\n  const box=$('#trialList');if(!box)return;box.innerHTML='';\n  if(!TR.trials.length)box.appendChild(el('p','hint','No trial yet.'));\n  TR.trials.forEach(t=>box.appendChild(trialRow(t)));\n  $('#trialNewBtn').onclick=()=>{const n=$('#trialNew');const o=n.style.display==='none';n.style.display=o?'':'none';if(o)trialForm();};\n}\nfunction trSig(s){return el('span','tsig'+(/worse/.test(s)?' warn':(/better|no signal/.test(s)?' ok':'')),s);}\nfunction trialRow(t){\n  const w=el('div','trow');\n  w.appendChild(el('b','',t.food));\n  const act=t.status==='active';\n  w.appendChild(el('p','hint',(act?('Day '+t.day_no+' of '+t.planned_days):('Ended '+t.ended+' · '+t.verdict))+\n    ' · had it on '+t.ate_days+' day'+(t.ate_days===1?'':'s')+(t.set_aside?' · '+t.set_aside+' set aside':'')+\n    (t.amount?' · '+t.amount:'')+(t.freq?' · '+t.freq:'')));\n  const s=el('p','');s.style.margin='4px 0';s.appendChild(el('span','','Compared with before: '));s.appendChild(trSig(t.signal));w.appendChild(s);\n  const more=el('button','chip',trOpen===t.id?'Hide details':'Details');more.type='button';\n  more.onclick=()=>{trOpen=trOpen===t.id?null:t.id;loadTrials();};w.appendChild(more);\n  if(trOpen===t.id)w.appendChild(trialDetail(t));\n  return w;\n}\nfunction trTable(rows){\n  const tb=el('table','ttab');const h=el('tr','');['','Days','Symptom days','Avg gut pain'].forEach(x=>h.appendChild(el('th','',x)));tb.appendChild(h);\n  rows.forEach(r=>{const tr=el('tr','');tr.appendChild(el('td','',r[0]));\n    [r[1].days,r[1].days?(r[1].symptom_days+' ('+r[1].pct+'%)'):'–',r[1].avg_gi_pain==null?'–':r[1].avg_gi_pain].forEach(x=>tr.appendChild(el('td','',String(x))));tb.appendChild(tr);});\n  return tb;\n}\nfunction trialDetail(t){\n  const d=el('div','');d.style.marginTop='10px';\n  d.appendChild(trTable([['14 days before',t.before],['During the trial',t.trial]]));\n  d.appendChild(el('p','hint','Days with a Day-context mark are left out of both. A word appears once each side has '+t.min_days+' counted days.'));\n  const s2=el('p','');s2.appendChild(el('span','','Days it was eaten (or the day after) vs other days: '));s2.appendChild(trSig(t.exposure_signal));d.appendChild(s2);\n  d.appendChild(trTable([['Eaten / day after',t.exposed],['Other logged days',t.not_exposed]]));\n  if(t.styles.length)d.appendChild(el('p','hint','Forms eaten: '+t.styles.map(x=>x[0]+' ×'+x[1]).join(', ')));\n  if(t.note)d.appendChild(el('p','hint',t.note));\n  if(t.status==='active'){\n    d.appendChild(el('p','lbl','End the trial — your verdict'));\n    const ch=el('div','chips');let v=null;\n    TR.verdicts.forEach(x=>{const b=el('button','chip',x.label);b.type='button';b.onclick=()=>{v=x.key;ch.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};ch.appendChild(b);});\n    d.appendChild(ch);\n    const nt=document.createElement('input');nt.type='text';nt.placeholder='Note (optional)';nt.style.marginTop='8px';d.appendChild(nt);\n    const go=el('button','btn primary','End trial');go.type='button';\n    go.onclick=async()=>{if(!v){toast('Pick a verdict');return;}\n      try{const r=await post('/api/trials/'+t.id+'/end',{verdict:v,note:nt.value});\n        toast(t.food+': '+r.verdict+(r.library.length?' · food map updated':''));trOpen=null;loadTrials();\n        if(typeof renderTestFoods==='function')try{loadRegistry();}catch(e){}}\n      catch(err){toast(err.message);}};\n    d.appendChild(go);\n  }else if(t.verdict_note)d.appendChild(el('p','hint','Note: '+t.verdict_note));\n  return d;\n}\nfunction trialForm(){\n  const n=$('#trialNew');n.innerHTML='';const st={food:null,freq:'Daily',days:14};\n  const inp=document.createElement('input');inp.type='text';inp.placeholder='Search the food or recipe';n.appendChild(inp);\n  const res=el('div','chips');res.style.marginTop='8px';n.appendChild(res);\n  const picked=el('p','hint','');n.appendChild(picked);\n  let tm=null;const run=async()=>{let j;try{j=await jget('/api/foods/search?q='+encodeURIComponent(inp.value.trim()));}catch(e){return;}\n    res.innerHTML='';j.foods.slice(0,12).forEach(f=>{const b=el('button','chip'+(st.food===f.n?' sel':''),f.n);b.type='button';\n      b.onclick=()=>{st.food=f.n;picked.textContent='Trial of: '+f.n;run();};res.appendChild(b);});};\n  inp.oninput=()=>{clearTimeout(tm);tm=setTimeout(run,220);};run();\n  const am=document.createElement('input');am.type='text';am.placeholder='How much each time (e.g. 2 eggs)';am.style.marginTop='8px';n.appendChild(am);\n  n.appendChild(el('p','lbl','How often'));const fq=el('div','chips');\n  ['Daily','Every other day','3 times a week','Weekly'].forEach(x=>{const b=el('button','chip'+(x===st.freq?' sel':''),x);b.type='button';b.onclick=()=>{st.freq=x;fq.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};fq.appendChild(b);});\n  n.appendChild(fq);\n  n.appendChild(el('p','lbl','For how long'));const ln=el('div','chips');\n  [[7,'1 week'],[14,'2 weeks'],[21,'3 weeks'],[30,'1 month']].forEach(x=>{const b=el('button','chip'+(x[0]===st.days?' sel':''),x[1]);b.type='button';b.onclick=()=>{st.days=x[0];ln.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));b.classList.add('sel');};ln.appendChild(b);});\n  n.appendChild(ln);\n  const go=el('button','btn primary','Start trial today');go.type='button';go.style.marginTop='10px';\n  go.onclick=async()=>{if(!st.food){toast('Pick the food');return;}\n    try{await post('/api/trials',{food:st.food,amount:am.value,freq:st.freq,days:st.days,start:todayISO});\n      toast('Trial started: '+st.food);n.style.display='none';loadTrials();}catch(err){toast(err.message);}};\n  n.appendChild(go);\n}\n"
CSS = '/* GUTLOG_V3260_TRIALS */\n.trow{border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:0 0 10px}\n.trow b{font-size:16px}.trow .tsig{font-weight:700}\n.trow .tsig.warn{color:var(--amber)}.trow .tsig.ok{color:var(--teal)}\n.ttab{width:100%;border-collapse:collapse;font-size:15px;margin:8px 0}\n.ttab th,.ttab td{text-align:right;padding:6px 4px;border-top:1px solid var(--line)}\n.ttab th:first-child,.ttab td:first-child{text-align:left}\n.ttab th{font-size:13px;color:var(--muted);font-weight:700}\n'
HTML = '    <div class="card" id="trialCard">\n      <p class="q">Food trials</p>\n      <p class="hint" style="margin:0 0 8px">A trial runs over days or weeks. Meals with the food are linked to it\n        automatically; days you marked in Day context are set aside; it is compared with the 14 days before.</p>\n      <div id="trialList"></div>\n      <button type="button" class="btn ghost" id="trialNewBtn">Start a trial</button>\n      <div id="trialNew" style="display:none"></div>\n    </div>\n    <p class="q" style="margin:14px 2px 6px">One-day test (the older way)</p>\n'

EDITS = [
    ("version", "GUTLOG_V3240_RECIPES GUTLOG_V3250_PLAN\n",
     "GUTLOG_V3240_RECIPES GUTLOG_V3250_PLAN " + MARKER + "\n"),
    ("schema", "CREATE TABLE IF NOT EXISTS recipes (",
     "CREATE TABLE IF NOT EXISTS trials (\n"
     "  id INTEGER PRIMARY KEY AUTOINCREMENT, food TEXT, match TEXT DEFAULT '', amount TEXT DEFAULT '',\n"
     "  freq TEXT DEFAULT '', start TEXT, days INTEGER DEFAULT 14, ended TEXT DEFAULT '',\n"
     "  status TEXT DEFAULT 'active', verdict TEXT DEFAULT '', verdict_note TEXT DEFAULT '',\n"
     "  note TEXT DEFAULT '', created TEXT);\n"
     "CREATE TABLE IF NOT EXISTS recipes ("),
    ("server", "# ------------------------------------------------------------------ PRN doses\n",
     SERVER + "# ------------------------------------------------------------------ PRN doses\n"),
    ("css", ".planrules li.pr-over span:last-child,.planrules li.pr-short span:last-child,.planrules li.pr-due span:last-child{color:var(--amber);font-weight:700}\n",
     ".planrules li.pr-over span:last-child,.planrules li.pr-short span:last-child,.planrules li.pr-due span:last-child{color:var(--amber);font-weight:700}\n" + CSS),
    ("html", '  <div class="sub" id="meals-test">\n',
     '  <div class="sub" id="meals-test">\n' + HTML),
    ("js fn", "async function loadPlan(){\n", JS + "async function loadPlan(){\n"),
    ("js seg", "  if(section==='meals'&&s==='recipes')loadRecipes();\n",
     "  if(section==='meals'&&s==='recipes')loadRecipes();\n  if(section==='meals'&&s==='test')loadTrials();\n"),
]
PAGE_TEXT = [JS, CSS, HTML]


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
    print("GutLog food trials as periods -> v3.26.0")
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
        print("FATAL: this file is not at v3.25.0.")
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
    bak = a.file + ".bak-v3260-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

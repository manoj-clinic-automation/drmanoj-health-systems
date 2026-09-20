#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.22.0 -> v3.23.0  ::  Day context (GUTLOG_V3230_CONTEXT)

His ask (19-Sep-2026): a place to mark one-off exertion, disturbed sleep,
travel or illness on a day, in a couple of taps -- without it a food trial
cannot be read honestly, because a heavy-strain day makes an innocent food
look guilty.

WHAT CHANGES
  1. A "Day context" card on the Now tab, above Down day: Today / Yesterday,
     and six toggle chips -- Heavy exertion, Poor sleep, Travel, Unwell,
     Stress, Ate out. One tap marks, a second clears. The summary shows what
     is marked.
  2. Tables day_context (day, tag; one row per day+tag) and day_context_note,
     created by SCHEMA -- no migration step, no schema_version bump.
  3. /api/daycontext (GET, POST) behind the login; /api/feed/daycontext behind
     the feed token, read-only, for the food-trial comparison and FitLog.
  Nothing existing changes. A down day stays its own thing: the symptom
  cluster; context is the circumstance around it.

Requires v3.22.0 (GUTLOG_V3220_MEALS). Anchor-verified, idempotent,
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
MARKER = "GUTLOG_V3230_CONTEXT"
PREV = "GUTLOG_V3220_MEALS"

SERVER = '# ------------------------------------------------------------------ day context\n# GUTLOG_V3230_CONTEXT -- what else was going on that day: heavy exertion,\n# poor sleep, travel, unwell, stress, ate out. Two taps (open, tap), stored\n# one row per day+tag, so a food trial or a symptom comparison can set these\n# days aside instead of blaming whatever was eaten. Separate from a down day:\n# a down day is his symptom cluster; context is the circumstance around it.\nDAY_CONTEXT = [\n    ("exertion", "Heavy exertion"),\n    ("poor_sleep", "Poor sleep"),\n    ("travel", "Travel"),\n    ("unwell", "Unwell"),\n    ("stress", "Stress"),\n    ("ate_out", "Ate out"),\n]\nDAY_CONTEXT_KEYS = dict(DAY_CONTEXT)\n\n\ndef _ctx_for(day):\n    return [r["tag"] for r in db().execute(\n        "SELECT tag FROM day_context WHERE day=? ORDER BY created", (day,)).fetchall()]\n\n\n@app.route("/api/daycontext")\n@login_required\ndef api_daycontext():\n    day = _valid_day(request.args.get("day")) or today()\n    note_row = db().execute("SELECT note FROM day_context_note WHERE day=?", (day,)).fetchone()\n    return jsonify(day=day, tags=_ctx_for(day), note=note_row["note"] if note_row else "",\n                   options=[{"key": k, "label": l} for k, l in DAY_CONTEXT])\n\n\n@app.route("/api/daycontext", methods=["POST"])\n@login_required\ndef api_daycontext_set():\n    """{day, tag, on} toggles one tag; {day, note} sets the day\'s note.\n    Idempotent: turning on a tag that is on changes nothing."""\n    d = J()\n    day = _valid_day(d.get("day") or today())\n    if not day:\n        return jsonify(ok=False, err="Pick a real date, not in the future."), 400\n    if "tag" in d:\n        tag = d.get("tag")\n        if tag not in DAY_CONTEXT_KEYS:\n            return jsonify(ok=False, err="Unknown tag."), 400\n        if d.get("on"):\n            db().execute("INSERT OR IGNORE INTO day_context(day, tag, created) VALUES(?,?,?)",\n                         (day, tag, now_s()))\n        else:\n            db().execute("DELETE FROM day_context WHERE day=? AND tag=?", (day, tag))\n    if "note" in d:\n        nt = note(d, "note", 200)\n        if nt:\n            db().execute("INSERT INTO day_context_note(day, note) VALUES(?,?) "\n                         "ON CONFLICT(day) DO UPDATE SET note=excluded.note", (day, nt))\n        else:\n            db().execute("DELETE FROM day_context_note WHERE day=?", (day,))\n    db().commit()\n    return jsonify(ok=True, day=day, tags=_ctx_for(day))\n\n\n@app.route("/api/feed/daycontext")\n@feed_required\ndef api_feed_daycontext():\n    """Read-only: which days carry which circumstances, for any consumer that\n    compares days (food trials, FitLog trends)."""\n    since = _feed_since(90)\n    out = {}\n    for r in db().execute("SELECT day, tag FROM day_context WHERE day>=? ORDER BY day",\n                          (since,)).fetchall():\n        out.setdefault(r["day"], []).append(r["tag"])\n    return jsonify(ok=True, app="gutlog", since=since,\n                   days=[{"day": k, "tags": v} for k, v in sorted(out.items())])\n\n\n'
JS = "/* GUTLOG_V3230_CONTEXT -- one tap marks a circumstance on today (or\n   yesterday, for last night's sleep noticed this morning); tap again clears. */\nlet ctxDay=null;\nfunction ctxYesterday(){const d=new Date(todayISO+'T12:00:00');d.setDate(d.getDate()-1);return d.toISOString().slice(0,10);}\nasync function loadCtx(){\n  const card=$('#nowCtx');if(!card)return;\n  if(!ctxDay)ctxDay=todayISO;\n  let j;try{j=await jget('/api/daycontext?day='+ctxDay);}catch(e){return;}\n  const dd=$('#ctxDay');dd.innerHTML='';\n  [['Today',todayISO],['Yesterday',ctxYesterday()]].forEach(x=>{\n    const b=el('button','chip'+(ctxDay===x[1]?' sel':''),x[0]);b.type='button';\n    b.onclick=()=>{ctxDay=x[1];loadCtx();};dd.appendChild(b);});\n  const box=$('#ctxTags');box.innerHTML='';\n  j.options.forEach(o=>{const on=j.tags.indexOf(o.key)>=0;\n    const b=el('button','chip'+(on?' sel':''),(on?'✓ ':'')+o.label);b.type='button';\n    b.onclick=async()=>{if(b.dataset.busy)return;b.dataset.busy=1;\n      try{await post('/api/daycontext',{day:ctxDay,tag:o.key,on:!on});\n        toast((on?'Cleared: ':'Marked: ')+o.label+(ctxDay===todayISO?'':' (yesterday)'));loadCtx();}\n      catch(err){b.dataset.busy='';toast(err.message);}};\n    box.appendChild(b);});\n  const labels=j.options.filter(o=>j.tags.indexOf(o.key)>=0).map(o=>o.label);\n  $('#ctxSum').textContent=labels.length?labels.join(' · '):'nothing marked';\n  card.classList.toggle('on',labels.length>0);\n}\n"
CSS = '/* GUTLOG_V3230_CONTEXT */\n#nowCtx .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}\n#nowCtx .dwtop .q{margin:0}\n#nowCtx .fs{margin-left:auto;font-size:14px;color:var(--muted);text-align:right}\n'
HTML = '  <div class="card" id="nowCtx">\n    <div class="dwtop"><p class="q">Day context</p><span class="fs" id="ctxSum"></span></div>\n    <div class="chips" id="ctxDay"></div>\n    <div class="chips" id="ctxTags" style="margin-top:10px"></div>\n    <p class="hint" style="margin:10px 2px 0">Anything unusual about the day. These days are set aside when foods and symptoms are compared.</p>\n  </div>\n\n'

EDITS = [
    ("version", "GUTLOG_V3210_ONEDOSE GUTLOG_V3220_MEALS\n",
     "GUTLOG_V3210_ONEDOSE GUTLOG_V3220_MEALS " + MARKER + "\n"),
    ("schema", "CREATE TABLE IF NOT EXISTS meal_meta (",
     "CREATE TABLE IF NOT EXISTS day_context (\n"
     "  day TEXT NOT NULL, tag TEXT NOT NULL, created TEXT, PRIMARY KEY (day, tag));\n"
     "CREATE TABLE IF NOT EXISTS day_context_note (day TEXT PRIMARY KEY, note TEXT DEFAULT '');\n"
     "CREATE TABLE IF NOT EXISTS meal_meta ("),
    ("server", "def _act_minutes_by_day(since):\n",
     SERVER + "def _act_minutes_by_day(since):\n"),
    ("css", ".mnew{margin-top:10px;padding:10px;border:1.5px dashed var(--line);border-radius:12px}\n",
     ".mnew{margin-top:10px;padding:10px;border:1.5px dashed var(--line);border-radius:12px}\n" + CSS),
    ("html", '  <div class="card" id="nowDown">\n', HTML + '  <div class="card" id="nowDown">\n'),
    ("js fn", "async function loadDown(){\n", JS + "async function loadDown(){\n"),
    ("js call", "  loadDown();\n  loadWatch();\n", "  loadDown();\n  loadCtx();\n  loadWatch();\n"),
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
    print("GutLog day context on the Now tab -> v3.23.0")
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
        print("FATAL: this file is not at v3.22.0.")
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
    bak = a.file + ".bak-v3230-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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

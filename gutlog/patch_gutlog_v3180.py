#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.17.0 -> v3.18.0  ::  the Watch card stops printing nothing, and the
                               medicines banner stops crying wolf

Three changes, none of them touching the sleep tile or the activity rings --
the Apple Watch has sent nothing since 2026-09-13 and there is no data to
verify those against.

  1. DEAD TILES. /api/watch sent a tile for every metric whether or not it
     held anything. `load_hours` came back n=0 with every field null and drew
     a label with an empty value under it; `resting_hr` and `hrv_ms` came back
     with a six-day median but no reading today and drew the same nothing,
     throwing the median away. A tile with no figure, no median and nothing
     running today is no longer sent at all. A tile that has only a median
     shows the median, with the window it came from stated on its face so it
     can never be read as today's reading.

  2. THE RHYTHM SENTENCE. The footnote justified showing inputs rather than
     scores with a claim about the wearer's cardiac rhythm, carried forward
     from an old investigation. A current one says otherwise, so the claim
     was false as written. The reason for showing inputs never depended on
     it, so the claim goes and the reason stands on its own. The clinical
     basis stays in the health record on the server and is deliberately not
     restated in this repository (CLAUDE.md rule 5d). FitLog's separate note
     about HR during a medication titration is a different claim, still true,
     and is untouched.

  3. THE MEDICINES BANNER. It painted itself amber for housekeeping -- a salt
     to fill in, a knowledge draft to review -- every single morning, and red
     for a RxGuard count that until RxGuard v1.7.0 included burdens built out
     of medicines that had not been taken. Red now means RxGuard has a RED it
     can stand behind; everything else is neutral. The banner is the only cue
     he sees daily and it should earn attention rather than spend it.

Requires v3.17.0 (GUTLOG_V3170_DOWN). Anchor-verified, idempotent, refuses
Jinja-breaking tokens in new CSS/JS, compile-checked, .bak before write,
self-restoring, --reverse for the negative control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
PREV = "GUTLOG_V3170_DOWN"
MARKER = "GUTLOG_V3180_HONEST"

# --------------------------------------------------------------------------
# 1. the footnote and the comment above it
# --------------------------------------------------------------------------
NOTE_OLD = '''# No verdict is computed here and none should be added. A wrist sensor's HR
# and HRV accuracy depends on the underlying rhythm, so every number on this
# screen is an input. The footnote says so once, quietly.
'''
NOTE_NEW = '''# No verdict is computed here and none should be added. Every number on this
# screen is an input to a judgement, never a judgement. The footnote says so
# once, quietly.
#
# GUTLOG_V3180_HONEST -- that footnote used to justify itself with a claim
# about the wearer's cardiac rhythm, carried forward from an old
# investigation. A current one says otherwise, so the claim was false as
# written. The reason for showing inputs rather than scores never rested on
# it, so the claim is gone and the reason stands on its own. The clinical
# basis is in the health record on the server; it does not belong in a public
# repository and is deliberately not restated here (CLAUDE.md rule 5d).
'''

WATCHNOTE_OLD = '''WATCH_NOTE = ("Shown as inputs, not conclusions. Heart-rate and HRV figures "
              "from a wrist sensor depend on the underlying rhythm for their "
              "accuracy, so no readiness, recovery or fitness score is "
              "derived from them here.")
'''
WATCHNOTE_NEW = '''WATCH_NOTE = ("Shown as inputs, not conclusions. No readiness, recovery or "
              "fitness score is derived from them.")
'''

# --------------------------------------------------------------------------
# 2. the server stops sending a tile that holds nothing
# --------------------------------------------------------------------------
STRIP_OLD = '''        strip[k] = {"kind": kind, "value": v, "source": cur.get("source") or "",
                    "day": day or "", "stale": bool(day) and day != tday,
                    "dir": d_, "median": med, "n": n, "today": run}
'''
STRIP_NEW = '''        # GUTLOG_V3180_HONEST -- a tile with no figure, no median and nothing
        # running today has nothing to say, and it said it: a label with an
        # empty value under it, which reads as a fault in the app rather than
        # as an absence of data. It is not sent at all. The decision is made
        # once, here, rather than in the drawing code, so the card can go on
        # drawing whatever it is handed.
        if v is None and med is None and run is None:
            continue
        strip[k] = {"kind": kind, "value": v, "source": cur.get("source") or "",
                    "day": day or "", "stale": bool(day) and day != tday,
                    "dir": d_, "median": med, "n": n, "today": run}
'''

# --------------------------------------------------------------------------
# 3. the tile draws a median-only reading as a figure, not as an absence
# --------------------------------------------------------------------------
TILE_OLD = '''  const has=(d.value!==null&&d.value!==undefined);
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
'''
TILE_NEW = '''  const has=(d.value!==null&&d.value!==undefined);
  const med=(d.median!==null&&d.median!==undefined)?d.median:null;
  /* GUTLOG_V3180_HONEST -- a settled metric can arrive with no reading today
     and several days of median behind it. Printing 'no data' over that threw
     the median away, and the label with an empty value under it read as a
     broken tile rather than as an absence. The median becomes the figure and
     the line beneath states the window it came from, so it can never be
     mistaken for a reading taken today. */
  const medonly=(!has&&med!==null&&d.n>0);
  w.querySelector('.wday').textContent=has?wkDay(d.day):'';
  const vv=w.querySelector('.wv');
  if(has){
    vv.textContent=wkNum(d.value,k)+(WK_UNIT[k]||'');
  }else if(medonly){
    vv.textContent=wkNum(med,k)+(WK_UNIT[k]||'');
    vv.classList.add('med');
  }else{
    vv.textContent='no data';
    vv.classList.add('none');
  }
  /* line 1: the comparison, in ink. Direction is never colour-alone -- the
     glyph carries it and the text stays ink. */
  const cmp=w.querySelector('.wc');
  if(has&&d.dir&&med!==null){
    cmp.textContent=WK_ARROW[d.dir]+' '+wkNum(d.value,k)+
      ' \\u00b7 typical '+wkNum(med,k);
  }else if(has){
    cmp.textContent=d.n?('only '+d.n+' days to compare with'):'nothing yet to compare with';
  }else if(medonly){
    cmp.textContent=d.n+'-day median, no reading today';
  }
'''

TILECSS_OLD = ".wktile .wv.none{font-size:16px;font-weight:600;color:var(--muted)}\n"
TILECSS_NEW = (".wktile .wv.none{font-size:16px;font-weight:600;color:var(--muted)}\n"
               ".wktile .wv.med{color:var(--muted)}\n")

# --------------------------------------------------------------------------
# 4. the medicines banner
# --------------------------------------------------------------------------
DRAW_OLD = """  const s=j.strip||{};
  /* one hero, then two per row. Five across overflowed a 368px strip. */
  if(s.steps)st.appendChild(wkTile('steps',s.steps,true));
  const grid=document.createElement('div');grid.className='wkgrid';
  ['exercise_minutes','load_hours','resting_hr','hrv_ms'].forEach(k=>{
    if(s[k])grid.appendChild(wkTile(k,s[k],false));
  });
"""
DRAW_NEW = """  const s=j.strip||{};
  /* GUTLOG_V3180_HONEST -- the server withholds a tile that holds nothing and
     the card refuses to draw one anyway. Two guards on purpose: this is the
     last line before the screen, and an answer cached by the service worker
     from an older build would otherwise put an empty label back in front of
     him with nothing in the server to stop it. */
  const wkHas=d=>!!d&&((d.value!==null&&d.value!==undefined)||
    (d.median!==null&&d.median!==undefined&&d.n>0)||
    (d.today&&d.today.value!==null&&d.today.value!==undefined));
  /* one hero, then two per row. Five across overflowed a 368px strip. */
  if(wkHas(s.steps))st.appendChild(wkTile('steps',s.steps,true));
  const grid=document.createElement('div');grid.className='wkgrid';
  ['exercise_minutes','load_hours','resting_hr','hrv_ms'].forEach(k=>{
    if(wkHas(s[k]))grid.appendChild(wkTile(k,s[k],false));
  });
"""

BANNER_OLD = """    const d=document.createElement('div');
    d.className='stockalert medstat '+(j.rx&&j.rx.red?'red':'amber');
"""
BANNER_NEW = """    const d=document.createElement('div');
    /* GUTLOG_V3180_HONEST -- red only when RxGuard has a RED it can stand
       behind, which from RxGuard v1.7.0 means one built out of medicines
       actually taken. Everything else this banner carries is housekeeping --
       a salt to fill in, a draft to review -- and housekeeping painted amber
       every morning is how a real alert stops being seen. */
    d.className='stockalert medstat '+(j.rx&&j.rx.red?'red':'info');
"""

BANNERCSS_OLD = ".stockalert.medstat{flex-wrap:wrap}\n"
BANNERCSS_NEW = (".stockalert.info{background:var(--chip);border-color:var(--line);color:var(--ink)}\n"
                 ".stockalert.medstat{flex-wrap:wrap}\n")


def build_edits():
    E = []
    a = ('SCHEMA_VERSION = "3.3.4"   # GUTLOG_V330_PHASE_A GUTLOG_V332_VARIANTS '
         'GUTLOG_V333_ROWACT GUTLOG_V340_READABILITY GUTLOG_V341_PICKER '
         'GUTLOG_V342_PAINSITE GUTLOG_V350_PHASE_B GUTLOG_V360_PHASE_C '
         'GUTLOG_V370_SALTS_ACTIVITY GUTLOG_V380_RECORDS GUTLOG_V390_SCAN '
         'GUTLOG_V3100_AUTOREAD GUTLOG_V3110_SCANQ GUTLOG_V3120_PAIN '
         'GUTLOG_V3130_WATCH GUTLOG_V3140_FALLBACK GUTLOG_V3150_READ '
         'GUTLOG_V3160_DARK GUTLOG_V3170_DOWN\n')
    E.append(("version markers", a, a.rstrip("\n") + " " + MARKER + "\n"))
    E.append(("watch comment", NOTE_OLD, NOTE_NEW))
    E.append(("watch footnote", WATCHNOTE_OLD, WATCHNOTE_NEW))
    E.append(("empty tiles dropped", STRIP_OLD, STRIP_NEW))
    E.append(("median-only tile", TILE_OLD, TILE_NEW))
    E.append(("tile css", TILECSS_OLD, TILECSS_NEW))
    E.append(("empty tiles not drawn", DRAW_OLD, DRAW_NEW))
    E.append(("banner class", BANNER_OLD, BANNER_NEW))
    E.append(("banner css", BANNERCSS_OLD, BANNERCSS_NEW))
    return E


# GutLog's page is a Jinja template rendered by render_template_string, so a
# "{{", "{%" or "{#" anywhere in new CSS or JS takes the WHOLE page down at
# render time -- and passes py_compile without a murmur. v3.4.0 shipped that
# way. Every patcher since refuses it before writing.
JINJA_TOKENS = ("{{", "{%", "{#")


def jinja_safe(edits):
    bad = []
    for label, _anchor, new in edits:
        for t in JINJA_TOKENS:
            if t in new:
                bad.append("  " + label + ": contains " + t)
    return bad


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
    """Reconstruct v3.17.0 for tools/NEGATIVE_CONTROL.py."""
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    out, bad = src, []
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            bad.append("  " + label + ": new text found " + str(c) + " times, need 1")
            break
        out = out.replace(new, anchor, 1)
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
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
    ap.add_argument("--reverse", metavar="OUT",
                    help="reconstruct v3.17.0 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("GutLog Watch tiles, the rhythm sentence, the banner -> v3.18.0")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.17.0. Apply that first.")
        return 1

    edits = build_edits()
    jb = jinja_safe(edits)
    if jb:
        print("REFUSING: new text carries a Jinja token, which would take the")
        print("whole page down at render time and pass py_compile:")
        for b in jb:
            print(b)
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
    if args.check:
        print("All anchors OK; no Jinja tokens in the new text.")
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
    bak = args.file + ".bak-v3180-" + stamp
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
    print("-" * 66)
    print("Next:  python3 test_watch_tiles.py app.py")
    print("       python3 test_ui_now.py app.py      (browser, service workers off)")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

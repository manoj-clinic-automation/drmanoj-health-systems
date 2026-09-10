#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.3.0 -> v3.3.1  ::  small-screen layout fix + vitals first

WHAT AND WHY
------------
1. HORIZONTAL OVERFLOW  (the actual bug in the screenshot)
   .row3 is a 3-column grid holding number inputs. Grid items default to
   min-width:auto, which resolves to the input's MIN-CONTENT width -- and
   an <input> reports a min-content width based on its `size` attribute
   (20 characters by default), not on the CSS width:100% it was given.
   So the three columns each demanded ~20ch, the grid grew past the
   viewport, and the whole page gained a horizontal scrollbar with the
   left edge clipped off screen.

   Fix: min-width:0 on the grid containers and their children. This is
   the root-cause fix. Deliberately NOT using overflow-x:hidden, which
   would hide this class of bug rather than fix it.

   .row2 had the same latent fault (date + time inputs) and is fixed too.

2. VITALS FIRST
   BP moves to the top of the Now tab, above the dose card.

3. SMALL-SCREEN DENSITY
   Under 430px: tighter chips, cards and severity scales, so the 1-10 row
   fits without wrapping awkwardly.

Anchor-verified, idempotent, compile-checked, self-restoring on failure.
Python 3.9 compatible.

  python3 patch_gutlog_v331.py --check
  python3 patch_gutlog_v331.py
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V331_LAYOUT"

# The BP card exactly as v3.3.0 wrote it.
BP_CARD = '''  <div class="card" id="nowBP">
    <p class="q">&#129656; Blood pressure</p>
    <div class="row3">
      <div><p class="lbl">Systolic</p><input type="number" inputmode="numeric" id="n_sys" placeholder="\u2014"></div>
      <div><p class="lbl">Diastolic</p><input type="number" inputmode="numeric" id="n_dia" placeholder="\u2014"></div>
      <div><p class="lbl">Pulse</p><input type="number" inputmode="numeric" id="n_pulse" placeholder="\u2014"></div>
    </div>
    <button type="button" class="addbtn" id="n_bpSave" style="margin-top:10px">Save reading</button>
    <p class="hint" id="n_bpLast" style="margin:8px 0 0"></p>
  </div>
'''

CSS_FIX = r'''/* ''' + MARKER + r''' -- grid items default to min-width:auto, which for an
   <input> is its size-attribute min-content width, not its CSS width. That
   pushed .row3 past the viewport and clipped the page. Root-cause fix; no
   overflow-x:hidden, which would only hide the next one of these. */
.row2,.row3{min-width:0}
.row2>*,.row3>*{min-width:0}
.row2 input,.row3 input,.row2 select,.row3 select{min-width:0}
@media (max-width:430px){
  main{padding:10px 10px 0}
  .card{padding:11px;border-radius:14px;margin-bottom:10px}
  .chip{padding:8px 12px;font-size:14.5px}
  #n_symSev .chip,#n_symBristol .chip{padding:8px 0;min-width:34px;text-align:center}
  .row3{gap:7px}
  .row3 .lbl{font-size:11px}
  .doserow{padding:10px}
  .doserow .nm b{font-size:15px}
  nav button{font-size:10px}
}'''


def build_edits():
    E = []

    # 1. CSS fix, appended to the v3.3.0 block
    a = (".schrow .x{margin-left:auto;border:0;background:none;"
         "color:var(--err);font-size:12px;cursor:pointer;"
         "text-decoration:underline}")
    E.append(("CSS: grid min-width fix + small-screen density",
              a, a + "\n" + CSS_FIX))

    # 2. remove BP card from its current position
    E.append(("remove BP card from mid-page", "\n" + BP_CARD, "\n"))

    # 3. re-insert it at the top of the Now tab
    a = ('<section class="tab sel" id="tab-now">\n'
         '  <div id="nowSched"></div>')
    E.append(("BP card to top of Now",
              a,
              '<section class="tab sel" id="tab-now">\n'
              + BP_CARD + '\n  <div id="nowSched"></div>'))

    return E


def verify(src, edits):
    out = []
    for label, anchor, _new in edits:
        n = src.count(anchor)
        if n != 1:
            out.append("  " + label + ": anchor found " + str(n)
                       + " times (need exactly 1)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("GutLog layout patch -> v3.3.1")
    print("file : " + args.file)
    print("=" * 60)

    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1

    src = open(args.file, "r", encoding="utf-8").read()

    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if "GUTLOG_V330_PHASE_A" not in src:
        print("FATAL: this file is not at v3.3.0. Apply Phase A first.")
        return 1

    edits = build_edits()
    problems = verify(src, edits)
    print("anchors: " + str(len(edits) - len(problems)) + "/"
          + str(len(edits)) + " matched")
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
    bak = args.file + ".bak-v331-" + stamp
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

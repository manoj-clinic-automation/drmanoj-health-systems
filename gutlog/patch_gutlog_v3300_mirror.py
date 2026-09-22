#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.29.0 -> v3.30.0  ::  GUTLOG_V3300_MIRRORSTALE -- say when the
Health Mirror has stopped being current.

The mirror copies his record into his own Drive so the Claude app on his
phone can read it. The danger is not that it breaks -- it is that it breaks
QUIETLY. Claude would go on answering from a file that looks current and is
a week old, confidently, with no sign anything is wrong. That is worse than
no mirror at all, because a missing file produces a question and a stale one
produces an answer.

So: `/api/mirror` reads the marker the mirror writes only after a SUCCESSFUL
upload, and the Now tab shows a line when the last success is more than 36
hours old, or when there has never been one.

Nothing here reads the record. It reads one timestamp.

Anchor-verified, idempotent, compile-checked, .bak before write,
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
MARKER = "GUTLOG_V3300_MIRRORSTALE"
PREV = "GUTLOG_V3290_NUTRITION"
VERSION = "3.30.0"

HEAD_OLD = 'GUTLOG_V3290_NUTRITION -- /nutrition, and a Meals tab with a day stepper.\n'
HEAD_NEW = ('GUTLOG_V3290_NUTRITION -- /nutrition, and a Meals tab with a day stepper.\n'
            'GUTLOG_V3300_MIRRORSTALE -- the Now tab says when the Drive mirror is stale.\n')

VER_OLD = ('APP_VERSION = "3.29.0"   # GUTLOG_V3290_NUTRITION GUTLOG_V3280_PLANS '
           'GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS GUTLOG_V3270_TIMEPICK '
           'GUTLOG_V3260_TRIALS\n')
VER_NEW = ('APP_VERSION = "3.30.0"   # GUTLOG_V3300_MIRRORSTALE GUTLOG_V3290_NUTRITION '
           'GUTLOG_V3280_PLANS GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS '
           'GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS\n')

CODE = r'''# ------------------------------------------------------------ mirror state
# GUTLOG_V3300_MIRRORSTALE. The Health Mirror writes last_success.json ONLY
# after an upload that worked. If that stamp is missing or old, the copy the
# Claude app reads on his phone is not today's -- and a silently stale mirror
# is worse than none, because it answers instead of asking.
MIRROR_STAMP = os.environ.get("GUTLOG_MIRROR_STAMP",
                              "/root/health_mirror/last_success.json")
MIRROR_STALE_HOURS = 36


def mirror_state():
    """{ok, hours, text}. Never reads the record -- one timestamp only."""
    try:
        with open(MIRROR_STAMP, encoding="utf-8") as fh:
            j = json.load(fh)
        when = datetime.fromtimestamp(float(j.get("epoch") or 0))
    except (OSError, ValueError, TypeError):
        return {"ok": False, "hours": None, "never": True,
                "text": "The health mirror has never finished. Claude on your "
                        "phone is reading nothing, or something older."}
    hours = (datetime.now() - when).total_seconds() / 3600.0
    if hours <= MIRROR_STALE_HOURS:
        return {"ok": True, "hours": round(hours, 1), "never": False,
                "text": "Mirror updated %s." % when.strftime("%d %b, %H:%M")}
    return {"ok": False, "hours": round(hours, 1), "never": False,
            "text": "The health mirror last updated %s, about %d hours ago. "
                    "Anything Claude tells you from it is that old."
                    % (when.strftime("%d %b, %H:%M"), int(hours))}


@app.route("/api/mirror")
@login_required
def api_mirror():
    return jsonify(mirror_state())


'''

CODE_OLD = '# -------------------------------------------------------------- nutrition\n'
CODE_NEW = CODE + CODE_OLD

TAB_OLD = '<section class="tab sel" id="tab-now">\n'
TAB_NEW = ('<section class="tab sel" id="tab-now">\n'
           '  <div id="nowMirror"></div>\n')

JS_OLD = 'async function loadNow(){\n'
JS_NEW = ('''/* GUTLOG_V3300_MIRRORSTALE -- one line, and only when there is something
   wrong. A warning that is always on teaches you to ignore warnings. */
async function loadMirror(){
  const box=$('#nowMirror');if(!box)return;
  let j;try{j=await jget('/api/mirror');}catch(e){return;}
  box.innerHTML='';
  if(j.ok)return;
  const c=el('div','card mirrorwarn');
  c.appendChild(el('p','q','\\u26A0 Health mirror'));
  c.appendChild(el('p','hint',j.text));
  box.appendChild(c);
}
async function loadNow(){
  loadMirror();
''')

CSS_OLD = '.tot{font-size:13px;color:var(--muted)}\n'
CSS_NEW = ('.tot{font-size:13px;color:var(--muted)}\n'
           '.mirrorwarn{border-color:var(--amber,#B57B08)}\n'
           '.mirrorwarn .q{color:var(--amber,#B57B08)}\n')

EDITS = [
    ("header", HEAD_OLD, HEAD_NEW),
    ("version", VER_OLD, VER_NEW),
    ("mirror state", CODE_OLD, CODE_NEW),
    ("now tab slot", TAB_OLD, TAB_NEW),
    ("load hook", JS_OLD, JS_NEW),
    ("warning css", CSS_OLD, CSS_NEW),
]

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
    print("GutLog say when the mirror is stale -> v" + VERSION)
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

    bak = a.file + ".bak-v3300-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 test_v3300_mirror.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

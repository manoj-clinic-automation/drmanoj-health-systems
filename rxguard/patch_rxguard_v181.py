#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.0 -> v1.8.1  ::  ceilings are label maxima, no confirming (RXGUARD_V181_LABELMAX)

His word (20-Sep-2026): the limits page should not tax him -- the maximum
daily doses are in verified sources, fetch them; and one of the as-needed
analgesics at twice its tablet in a day is an accepted dose, so do not flag
it. (The medicine is named in the rules file on the server, never here --
CLAUDE.md 5d, this repository is public.)

WHAT CHANGES
  1. Ceilings are the LABEL MAXIMUM daily doses, each with its source, in the
     rules file (server only). The "default" chip and the Confirm button are
     gone; findings no longer ask him to confirm anything.
  2. A limit he sets is shown as "your limit"; clearing the box returns to the
     label maximum (it used to mean "no ceiling").
  3. dose_ceiling.py gains DC010: a daily total above a course limit on more
     days running than the label allows -- for a dose that is licensed only
     as a short acute course, the run of days is what matters, not the
     single day. The ingredient, its figures and its sources are in the
     rules file on the server; this file names no medicine.
  4. The dose feed looks back 10 days instead of 4, so DC010 can see a run.

Requires v1.8.0 (RXGUARD_V180_DOSE) and the v1.8.1 dose_ceiling.py beside
app.py. Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V181_LABELMAX"
PREV = "RXGUARD_V180_DOSE"

EDITS_ONE = [('version', 'APP_VERSION = "1.8.0"   # ', 'APP_VERSION = "1.8.1"   # RXGUARD_V181_LABELMAX '), ('feed days', 'DOSE_FEED_DAYS = 4\n', 'DOSE_FEED_DAYS = 10\n'), ('post', '            now_s = dose_now().strftime("%Y-%m-%d %H:%M")\n            db = get_db()\n            db.execute("INSERT INTO dose_ceilings (ing, ceiling, confirmed, updated) "\n                       "VALUES (?, ?, ?, ?) ON CONFLICT(ing) DO UPDATE SET "\n                       "ceiling=excluded.ceiling, confirmed=excluded.confirmed, "\n                       "updated=excluded.updated", (ing, val, now_s[:10], now_s))\n            db.commit()\n            flash("Saved and confirmed: %s %s." % (\n                rules["ingredients"][ing]["label"],\n                ("%g %s" % (val, rules["ingredients"][ing]["unit"])) if val is not None\n                else "no ceiling"))\n            return redirect(url_for("dose_page"))\n', '            now_s = dose_now().strftime("%Y-%m-%d %H:%M")\n            db = get_db()\n            label = rules["ingredients"][ing]["label"]\n            if val is None:\n                # RXGUARD_V181_LABELMAX -- a cleared box goes back to the label maximum\n                db.execute("DELETE FROM dose_ceilings WHERE ing=?", (ing,))\n                db.commit()\n                flash("%s: back to the label maximum." % label)\n                return redirect(url_for("dose_page"))\n            db.execute("INSERT INTO dose_ceilings (ing, ceiling, confirmed, updated) "\n                       "VALUES (?, ?, ?, ?) ON CONFLICT(ing) DO UPDATE SET "\n                       "ceiling=excluded.ceiling, confirmed=excluded.confirmed, "\n                       "updated=excluded.updated", (ing, val, now_s[:10], now_s))\n            db.commit()\n            flash("Saved: your own limit for %s, %g %s." % (\n                label, val, rules["ingredients"][ing]["unit"]))\n            return redirect(url_for("dose_page"))\n'), ('ceilings', '        <h2>Ceilings</h2>\n        <p class="muted">A ceiling marked <span class="chip">default</span> came from the rules\n        file and has not been confirmed by you. Saving a row confirms it. Leave the box blank\n        for no ceiling.</p>\n        <table><tr><th>Ingredient</th><th>Window</th><th>Ceiling</th><th></th></tr>\n        {% for k, r in dv.rules.ingredients|dictsort %}{% if not r.class_only %}<tr>\n        <td>{{ r.label }}{% if r.consequence %}<br><span class="muted">{{ r.consequence }}</span>{% endif %}</td>\n        <td class="num">{{ r.window_h|int }} h{% if r.max_units is not none %}<br><span class="muted">max {{ \'%g\'|format(r.max_units) }} unit</span>{% endif %}</td>\n        <td><form method="post" style="display:flex;gap:6px;align-items:center">\n          <input type="hidden" name="ing" value="{{ k }}">\n          <input name="ceiling" inputmode="decimal" style="width:6em"\n            value="{{ \'%g\'|format(r.ceiling) if r.ceiling is not none else \'\' }}"> {{ r.unit }}\n          <button>{{ \'Save\' if r.confirmed else \'Confirm\' }}</button></form></td>\n        <td class="muted">{% if r.confirmed %}confirmed {{ r.confirmed }}{% else %}<span class="chip">default</span>{% endif %}</td>\n        </tr>{% endif %}{% endfor %}</table>\n', '        <h2>Ceilings</h2>\n        <p class="muted">Each ceiling is the label maximum daily dose, taken from the source shown.\n        Nothing here needs your confirmation. Change one only if your doctor has set you a\n        different limit; clear the box to go back to the label maximum.</p>\n        <table><tr><th>Ingredient</th><th>Ceiling</th><th>Source</th></tr>\n        {% for k, r in dv.rules.ingredients|dictsort %}{% if not r.class_only %}<tr>\n        <td>{{ r.label }}{% if r.consequence %}<br><span class="muted">{{ r.consequence }}</span>{% endif %}</td>\n        <td class="num">{% if r.ceiling is not none %}{{ \'%g\'|format(r.ceiling) }} {{ r.unit }}{% else %}<span class="flag UNKNOWN">not set</span>{% endif %}\n          <br><span class="muted">{{ r.window_h|int }} h{% if r.max_units is not none %} &middot; max {{ \'%g\'|format(r.max_units) }} unit{% endif %}{% if r.course_above is defined and r.course_above %} &middot; over {{ \'%g\'|format(r.course_above) }} {{ r.unit }}: {{ r.course_days }} days at most{% endif %}</span>\n          {% if r.confirmed %}<br><span class="chip">your limit</span>{% endif %}\n          <details><summary class="muted">Change</summary><form method="post" style="display:flex;gap:6px;align-items:center;margin-top:6px">\n          <input type="hidden" name="ing" value="{{ k }}">\n          <input name="ceiling" inputmode="decimal" style="width:6em"\n            value="{{ \'%g\'|format(r.ceiling) if r.confirmed and r.ceiling is not none else \'\' }}" placeholder="label"> {{ r.unit }}\n          <button>Save</button></form></details></td>\n        <td class="muted">{% if r.confirmed %}Your own limit, set {{ r.confirmed }}.{% else %}{{ r.source or \'Not recorded\' }}{% endif %}</td>\n        </tr>{% endif %}{% endfor %}</table>\n'), ('footer', 'DC001&ndash;DC009', 'DC001&ndash;DC010'), ('blank msg', 'flash("A ceiling must be a number, or blank for none.")', 'flash("A ceiling must be a number, or blank for the label maximum.")')]
EDITS_ALL = [('default chip (x2)', '{% if not r.confirmed %}<br><span class="chip">default</span>{% endif %}', '{% if r.confirmed %}<br><span class="chip">your limit</span>{% endif %}', 2)]


def build_edits():
    return [(l, o, n) for l, o, n in EDITS_ONE]


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
    """Reconstruct v1.8.0 for tools/NEGATIVE_CONTROL.py."""
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
    for label, anchor, new, cnt in EDITS_ALL:
        if out.count(new) != cnt:
            print("REVERSE FAILED, nothing written: " + label)
            return 1
        out = out.replace(new, anchor)
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
                    help="reconstruct v1.8.0 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("RxGuard Daily dose: label maxima, no confirming -> v1.8.1")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    engine = os.path.join(os.path.dirname(os.path.abspath(args.file)), "dose_ceiling.py")
    if not os.path.exists(engine) or "RXGUARD_V181_LABELMAX" not in read(engine):
        print("FATAL: the v1.8.1 dose_ceiling.py must sit beside app.py first: " + engine)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v1.8.0. Apply that first.")
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
    for label, anchor, new, cnt in EDITS_ALL:
        if src.count(anchor) != cnt:
            print("ANCHOR FAILURE: %s found %d times, need %d. Nothing written." % (label, src.count(anchor), cnt))
            return 1
    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    for label, anchor, new, cnt in EDITS_ALL:
        out = out.replace(anchor, new)
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
    bak = args.file + ".bak-v181-" + stamp
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
    print("Next:  python3 test_dose_ceiling.py app.py")
    print("       python3 smoke_test.py   then  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

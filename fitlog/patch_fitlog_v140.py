#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.3.3 -> v1.4.0  ::  Phase I -- the analgesic mirror and the load day

  * POST /api/analgesic -- the ONE inbound write path for analgesic_log.
    GutLog calls it the moment an analgesic chip is tapped on a pain tile,
    carrying pain_at_time = the score entered at the same tap. That score is
    the whole point: the dose feed can tell FitLog a drug was taken, but not
    how bad it was when he took it.
    Bearer-gated on GutLog's read-only feed token, machine-to-machine, never
    session-authenticated (CLAUDE.md rule 5). Idempotent: a retry after a
    timeout re-finds the row instead of doubling it.
  * kind `ot_day` (Operating day) from GutLog's activity feed is shown as
    STANDING LOAD, separately from exercise, and is never counted as exercise
    minutes. Hours on his legs are a load that must be recorded, or every
    walking-versus-pain comparison is confounded by his operating list.

Requires v1.2.0 (FITLOG_V120_ACTIVITY). Anchor-verified, idempotent,
compile-checked, .bak before write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/fitlog/app.py"
MARKER = "FITLOG_V140_PAIN"
PREV = "FITLOG_V120_ACTIVITY"

PY = '''# ---------------- analgesic mirror (FITLOG_V140_PAIN) ----------------
# GutLog owns the medicine record. When a pain tile there logs an analgesic it
# writes its own `doses` row AND calls this, so the same event exists here with
# the pain score attached. Nothing else may write analgesic_log from outside:
# a medicine logged in two places is a record that disagrees with itself.
def _stack_match(molecule, name):
    """The med_stack row for a molecule GutLog names: exact generic first, then
    a combination product carrying it as a whole component, then the display
    name. Components, never substrings -- a substring match would file a single
    molecule under the first combination whose name happens to contain those
    letters. None when the stack carries nothing for it: the caller is told,
    rather than a medicine being invented to hold the row."""
    rows = db().execute("SELECT id, name, generic, category FROM med_stack "
                        "WHERE active=1 ORDER BY id").fetchall()
    mol = (molecule or "").strip().lower()
    if mol:
        for r in rows:
            if (r["generic"] or "").strip().lower() == mol:
                return r
        for r in rows:
            parts = [p.strip() for p in
                     (r["generic"] or "").lower().replace("+", ",").split(",")]
            if mol in parts:
                return r
    nm = (name or "").strip().lower()
    if nm:
        for r in rows:
            if (r["name"] or "").strip().lower() == nm:
                return r
    return None


@app.route("/api/analgesic", methods=["POST"])
def api_analgesic():
    if not _feed_authorised(request):
        return {"ok": False, "error": "unauthorised"}, 401
    d = request.get_json(silent=True) or {}
    row = _stack_match(d.get("molecule"), d.get("name"))
    if not row:
        return {"ok": False, "error": "no matching medicine in the FitLog stack",
                "molecule": str(d.get("molecule") or "")}
    dt = str(d.get("dt") or "")[:16] or datetime.now().isoformat(timespec="minutes")
    try:
        pain = int(d.get("pain_at_time"))
    except (TypeError, ValueError):
        pain = None
    notes = str(d.get("notes") or "")[:200]
    ref = str(d.get("ref") or "")[:60]
    if ref:
        notes = (notes + " [" + ref + "]").strip()
    ex = db().execute("SELECT id FROM analgesic_log WHERE med_id=? AND dt=? "
                      "AND COALESCE(notes,'')=?", (row["id"], dt, notes)).fetchone()
    if ex:
        return {"ok": True, "id": ex["id"], "med": row["name"], "duplicate": True}
    db().execute("INSERT INTO analgesic_log(dt,med_id,dose_label,context_event_id,"
                 "pain_at_time,notes) VALUES(?,?,?,?,?,?)",
                 (dt, row["id"], str(d.get("dose_label") or row["name"])[:60],
                  None, pain, notes))
    db().commit()
    rid = db().execute("SELECT last_insert_rowid() i").fetchone()["i"]
    return {"ok": True, "id": rid, "med": row["name"], "pain_at_time": pain}


'''

PY_ANCHOR = "# ---------------- warning flags ----------------\n"


def build_edits():
    E = []

    a = "FITLOG_V120_ACTIVITY -- FitLog v1.2.0 activity feed + Home activity card.\n"
    E.append(("docstring marker", a,
              a + "FITLOG_V140_PAIN -- FitLog v1.4.0 analgesic mirror + operating-day load.\n"))

    E.append(("python", PY_ANCHOR, PY + PY_ANCHOR))

    a = ('ACT_LABEL = {"walk": "Walk", "treadmill": "Treadmill", "cycle_road": "Cycling (road)",\n'
         '             "cycle_static": "Cycling (static)", "meditation": "Meditation"}\n')
    n = ('ACT_LABEL = {"walk": "Walk", "treadmill": "Treadmill", "cycle_road": "Cycling (road)",\n'
         '             "cycle_static": "Cycling (static)", "meditation": "Meditation",\n'
         '             "ot_day": "Operating day"}\n'
         '# FITLOG_V140_PAIN -- load, not training. Never counted as exercise.\n'
         'LOAD_KINDS = ("ot_day",)\n')
    E.append(("ACT_LABEL", a, n))

    a = ('    for t in taps:\n'
         '        if t.get("_used"):\n'
         '            continue\n'
         '        bits = ACT_LABEL.get(t.get("kind"), t.get("kind") or "") + " " + str(int(round(t.get("minutes") or 0))) + " min"\n'
         '        if t.get("intensity"):\n'
         '            bits += " · " + str(t["intensity"]).lower()\n'
         '        items.append(((t.get("atime") or "")[:5], bits + " (GutLog)"))\n')
    n = ('    load = []\n'
         '    for t in taps:\n'
         '        if t.get("_used"):\n'
         '            continue\n'
         '        if t.get("kind") in LOAD_KINDS:\n'
         '            load.append(((t.get("atime") or "")[:5],\n'
         '                         ACT_LABEL.get(t["kind"], t["kind"]) + " · "\n'
         '                         + str(round((t.get("minutes") or 0) / 60.0, 1)) + " h on his legs"))\n'
         '            continue\n'
         '        bits = ACT_LABEL.get(t.get("kind"), t.get("kind") or "") + " " + str(int(round(t.get("minutes") or 0))) + " min"\n'
         '        if t.get("intensity"):\n'
         '            bits += " · " + str(t["intensity"]).lower()\n'
         '        items.append(((t.get("atime") or "")[:5], bits + " (GutLog)"))\n')
    E.append(("activity taps", a, n))

    a = ('    if not rows and not head and not note:\n'
         '        rows = "<tr><td class=small>Nothing yet today.</td></tr>"\n'
         '    return (\'<div class="card"><h2>Activity today</h2><p class=small>\' + _html.escape(" · ".join(head)) +\n'
         '            \'</p><table>\' + rows + \'</table>\' + note +\n')
    n = ('    if not rows and not head and not note and not load:\n'
         '        rows = "<tr><td class=small>Nothing yet today.</td></tr>"\n'
         '    loadrows = "".join("<tr><td class=small>" + _html.escape(t) + "</td><td>" +\n'
         '                       _html.escape(b) + "</td></tr>" for t, b in load)\n'
         '    loadblock = ""\n'
         '    if loadrows:\n'
         '        loadblock = (\'<p class=small style="margin-top:10px"><b>Standing load</b> &mdash; hours on \'\n'
         '                     \'his legs, not exercise, and never counted as exercise minutes. Recorded so \'\n'
         '                     \'a walking-versus-pain comparison is not confounded by the operating \'\n'
         '                     \'list.</p><table>\' + loadrows + "</table>")\n'
         '    return (\'<div class="card"><h2>Activity today</h2><p class=small>\' + _html.escape(" · ".join(head)) +\n'
         '            \'</p><table>\' + rows + \'</table>\' + loadblock + note +\n')
    E.append(("activity card", a, n))

    return E


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
    print("FitLog Phase I: analgesic mirror + operating day -> v1.4.0")
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
        print("FATAL: this file is not at v1.2.0 or later. Apply that first.")
        return 1
    if "def api_analgesic" in src:
        print("FATAL: /api/analgesic already present -- unexpected state. Nothing written.")
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
    bak = args.file + ".bak-v140-" + stamp
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
    print("Next:  python3 test_analgesic_mirror.py   then  systemctl restart fitlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.5.0 -> v1.6.0  ::  unresolved keys, PRN-aware reconciliation

Three changes, all from what v1.5.0 found on its first run against the real
list (2026-09-14).

  1. UNRESOLVED DRUG KEYS, FLAGGED LOUDLY. A `medications.drug_key` the
     knowledge base cannot resolve is not a tidiness problem: the drug is left
     out of every interaction, burden, QT and condition check, silently, and
     the screen looks exactly as it would if the drug were safe. One missing
     letter was enough. `unresolved_keys()` checks every row; the Dashboard --
     the first thing seen -- carries a banner at the top when any row is
     unresolved, and `show_reds.py` leads with the same list. Active and
     tapering rows are named as the safety gap they are; stopped rows are
     reported too, more quietly, because a record that misleads later is still
     a defect. A key containing "+" gets its own message: RxGuard holds one
     row per molecule, so a combination belongs on two lines.

  2. RECONCILIATION IS FOR CHRONIC DRUGS ONLY. "Not taken for 7 days" means
     something for a drug meant to be taken daily and nothing at all for an
     as-needed analgesic. Three of the four lines on the first run were PRNs.
     `medications.kind` already carries this, so the "still active here" test
     now skips anything that is not `chronic`.

  3. STALENESS SAYS WHICH KIND OF STALE. The same 14-day silence means two
     different things, and one sentence for both was wrong:
       chronic, absent from the regimen -> may have been stopped, reconcile;
       as-needed, not taken recently    -> the burden may be theoretical
                                           rather than current.
     A finding resting on both gets both sentences. Nothing is dropped either
     way -- a RED that may be about a stopped drug is still a RED until
     somebody decides otherwise.

Requires v1.5.0 (RXGUARD_V150_RECONCILE). Anchor-verified, idempotent,
compile-checked, .bak before write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V160_KEYCHECK"
PREV = "RXGUARD_V150_RECONCILE"

PY = '''# --------------------------------------------------------------------------
# Unresolved drug keys -- RXGUARD_V160_KEYCHECK
# A key the knowledge base cannot resolve contributes to NOTHING: no pairwise
# rule, no CYP derivation, no class duplication, no burden, no QT sum, no
# condition rule. The drug is absent from every check on every screen and the
# screen looks the same as it would if the drug were safe. That is the worst
# possible failure mode for this application, and on 2026-09-14 one missing
# letter in a molecule name had been doing it unnoticed.
# --------------------------------------------------------------------------
def unresolved_keys():
    """Every medications row whose drug_key the knowledge base cannot resolve.

    Live rows (active, tapering) are a safety gap. Stopped rows are a record
    that will mislead whoever reads it later. Both are reported; only the
    urgency differs.
    """
    rows = get_db().execute(
        "SELECT id, drug_key, raw_name, status, kind FROM medications "
        "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'tapering' THEN 1 "
        "ELSE 2 END, drug_key").fetchall()
    out = []
    for r in rows:
        key = (r["drug_key"] or "").strip()
        if key and get_drug(key):
            continue
        parts = [p for p in re.split(r"[+,]", key) if p.strip()]
        combo = len(parts) > 1
        if not key:
            why = "this row has no drug key at all"
        elif combo:
            why = ("a combination: RxGuard holds one row per molecule, so enter "
                   "each on its own line")
        else:
            why = "not a knowledge-base key -- check the spelling"
        out.append({"id": r["id"], "key": key, "raw": r["raw_name"] or "",
                    "status": r["status"] or "", "kind": r["kind"] or "",
                    "live": (r["status"] or "") in ("active", "tapering"),
                    "combination": combo, "why": why})
    return out


def _name_list(keys):
    """("a", False) / ("a and b", True) / ("a, b and c", True).

    Returns the flag as well as the text so the caller can make its verbs
    agree. "a, b, c is as-needed and has not been taken" is the kind of
    sentence that makes a clinical screen look unmaintained, which is not what
    you want from the screen you are trusting.
    """
    names = [display_name(k) for k in keys]
    if len(names) == 1:
        return names[0], False
    return ", ".join(names[:-1]) + " and " + names[-1], True


def unresolved_summary():
    """(live, stopped) counts, or None when the check cannot run."""
    try:
        u = unresolved_keys()
    except Exception:
        return None
    return {"live": sum(1 for x in u if x["live"]),
            "stopped": sum(1 for x in u if not x["live"]), "rows": u}


'''

PY_ANCHOR = "def astaken_summary():\n"

BANNER = """
        {% if unresolved %}
        <div class="card unresolved">
        <strong>{{ unresolved|length }} medication{{ '' if unresolved|length == 1 else 's' }}
        the knowledge base cannot resolve</strong>
        <p class="muted" style="margin:6px 0 0">A drug whose key does not resolve is left out of
        <em>every</em> check on every screen &mdash; interactions, CYP, duplication, burden, QT and
        conditions &mdash; silently. The screen then looks exactly as it would if the drug were
        safe. Fix the key, or the rest of this page is answering a question about a shorter list
        than you think.</p>
        <table><tr><th>On the list as</th><th>Key</th><th>Status</th><th>Why it does not resolve</th></tr>
        {% for u in unresolved %}<tr>
        <td>{{ u.raw or '&mdash;' }}</td>
        <td class="dose">{{ u.key or '&mdash;' }}</td>
        <td>{% if u.live %}<span class="flag RED">{{ u.status }}</span>{% else %}<span class="muted">{{ u.status }}</span>{% endif %}</td>
        <td class="muted">{{ u.why }}</td>
        </tr>{% endfor %}</table>
        <p class="muted" style="margin:6px 0 0">Correct them under
        <a href="{{ url_for('meds') }}">Medications</a>. <code>rxguard/fix_drug_keys.py</code>
        renames a key and its history together, dry-run by default.</p></div>
        {% endif %}"""

CSS = """.unresolved{border-left:3px solid var(--red)}
.unresolved table{margin-top:8px}
"""


def build_edits():
    E = []

    a = ('APP_VERSION = "1.5.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
         'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS RXGUARD_V150_RECONCILE')
    E.append(("version", a,
              'APP_VERSION = "1.6.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
              'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS RXGUARD_V150_RECONCILE '
              + MARKER))

    E.append(("python", PY_ANCHOR, PY + PY_ANCHOR))

    # ---- 2. reconciliation: chronic only ---------------------------------
    a = ('    for key in sorted(listed):\n'
         '        if key in reg:\n'
         '            continue\n'
         '        gap = _days_since(last.get(key, ""))\n'
         '        if gap is not None and gap < RECONCILE_GAP_DAYS:\n'
         '            continue\n'
         '        m = listed[key]\n')
    n = ('    for key in sorted(listed):\n'
         '        if key in reg:\n'
         '            continue\n'
         '        m = listed[key]\n'
         '        # RXGUARD_V160_KEYCHECK -- a PRN that has not been taken is not\n'
         '        # evidence of anything. "No dose in 7 days" means something for a\n'
         '        # drug meant to be taken every day and nothing at all for an\n'
         '        # as-needed analgesic, and on the first real run three of the four\n'
         '        # lines were PRNs. kind already records this.\n'
         '        if (m["kind"] or "chronic") != "chronic":\n'
         '            continue\n'
         '        gap = _days_since(last.get(key, ""))\n'
         '        if gap is not None and gap < RECONCILE_GAP_DAYS:\n'
         '            continue\n')
    E.append(("reconcile chronic only", a, n))

    a = ('    return {"stopped": stopped, "missing": missing,\n'
         '            "gap_days": RECONCILE_GAP_DAYS, "stale_days": GUT_STALE_DAYS}\n')
    n = ('    return {"stopped": stopped, "missing": missing,\n'
         '            "gap_days": RECONCILE_GAP_DAYS, "stale_days": GUT_STALE_DAYS,\n'
         '            "chronic_only": True}\n')
    E.append(("reconcile view payload", a, n))

    # ---- 3. staleness: two labels ----------------------------------------
    a = ('def mark_gut_stale(findings, data):\n'
         '    """Flag, never drop. A RED that may be about a stopped drug is still a RED\n'
         '    until someone decides otherwise; it just says so on its face."""\n'
         '    keys = gut_stale_keys(data)\n'
         '    n = 0\n'
         '    for f in findings:\n'
         '        if f["flag"] not in ("RED", "AMBER"):\n'
         '            continue\n'
         '        hits = sorted(set(f.get("involves") or []) & keys)\n'
         '        if not hits:\n'
         '            continue\n'
         '        f["gut_stale"] = (\n'
         '            "GutLog has no dose of %s in the last %d days and it is not in the regimen "\n'
         '            "there, so this finding may rest on a medicine already stopped. Reconcile it "\n'
         '            "above, or confirm it is still taken." %\n'
         '            (", ".join(display_name(k) for k in hits), GUT_STALE_DAYS))\n'
         '        n += 1\n'
         '    return n\n')
    n = ('def mark_gut_stale(findings, data):\n'
         '    """Flag, never drop. A RED that may be about a stopped drug is still a RED\n'
         '    until someone decides otherwise; it just says so on its face.\n'
         '\n'
         '    RXGUARD_V160_KEYCHECK -- the same 14-day silence means two different\n'
         '    things and must not be written up as one. A chronic drug missing from\n'
         '    the regimen may have been stopped, and the finding should be\n'
         '    reconciled. An as-needed drug simply has not been needed, and the\n'
         '    finding is about a burden that is theoretical rather than current. A\n'
         '    finding resting on both gets both sentences.\n'
         '    """\n'
         '    keys = gut_stale_keys(data)\n'
         '    kinds = {}\n'
         '    for m in active_meds():\n'
         '        kinds.setdefault(m["drug_key"], (m["kind"] or "chronic"))\n'
         '    n = 0\n'
         '    for f in findings:\n'
         '        if f["flag"] not in ("RED", "AMBER"):\n'
         '            continue\n'
         '        hits = sorted(set(f.get("involves") or []) & keys)\n'
         '        if not hits:\n'
         '            continue\n'
         '        # a key not on the list at all cannot be called as-needed, so it\n'
         '        # is treated as chronic: the stronger claim is the safer one\n'
         '        chronic = [k for k in hits if kinds.get(k, "chronic") == "chronic"]\n'
         '        prn = [k for k in hits if kinds.get(k, "chronic") != "chronic"]\n'
         '        parts, tags = [], []\n'
         '        if chronic:\n'
         '            names, many = _name_list(chronic)\n'
         '            parts.append(\n'
         '                "GutLog has no dose of %s in the last %d days and %s not in the "\n'
         '                "regimen there: %s may have been stopped -- reconcile above, or "\n'
         '                "confirm %s still taken." %\n'
         '                (names, GUT_STALE_DAYS,\n'
         '                 "they are" if many else "it is", "they" if many else "it",\n'
         '                 "they are" if many else "it is"))\n'
         '            tags.append("chronic")\n'
         '        if prn:\n'
         '            names, many = _name_list(prn)\n'
         '            parts.append(\n'
         '                "%s %s as-needed and %s not been taken in the last %d days, so this "\n'
         '                "burden may be theoretical rather than current." %\n'
         '                (names, "are" if many else "is", "have" if many else "has",\n'
         '                 GUT_STALE_DAYS))\n'
         '            tags.append("prn")\n'
         '        f["gut_stale"] = " ".join(parts)\n'
         '        f["gut_stale_kinds"] = tags\n'
         '        n += 1\n'
         '    return n\n')
    E.append(("staleness wording", a, n))

    # ---- 1. the dashboard banner -----------------------------------------
    a = ('        <h1>Dashboard</h1>\n'
         '        <p class="sub">{{ meds|length }} active · knowledge base {{ kbv }}</p>\n')
    E.append(("dashboard banner", a, a + BANNER + "\n"))

    a = ('        return page(body, nav="dash", title="Dashboard", meds=meds, conds=conds, ast=astaken_summary(),\n')
    E.append(("dashboard context", a,
              '        return page(body, nav="dash", title="Dashboard", meds=meds, conds=conds, ast=astaken_summary(),\n'
              '                    unresolved=unresolved_keys(),\n'))

    a = ".constraints{border-left:3px solid var(--ink)}\n"
    E.append(("css", a, CSS + a))

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
    print("RxGuard unresolved keys + PRN-aware reconciliation -> v1.6.0")
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
        print("FATAL: this file is not at v1.5.0. Apply that first.")
        return 1
    if "def unresolved_keys" in src:
        print("FATAL: the key check is already present. Nothing written.")
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
    bak = args.file + ".bak-v160-" + stamp
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
    print("Next:  python3 test_reconcile.py /root/gutlog/app.py")
    print("       python3 smoke_test.py     then  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

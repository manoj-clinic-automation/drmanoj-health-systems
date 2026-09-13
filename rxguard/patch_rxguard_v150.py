#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.4.0 -> v1.5.0  ::  closing the GutLog -> RxGuard disconnect

The failure this fixes, verified in the code: api_feed_stack correctly drops an
ended schedule from `regimen`, and astaken_view already computes the mismatch
(the in_gutlog / "not logged" column) -- but the engine runs on active_meds(),
i.e. RxGuard's own medications table, which nothing updates. In September 2026
a medicine ended in GutLog was still being flagged here five days later, until
the list was edited by hand.

It does NOT auto-write. RxGuard's status carries clinical meaning GutLog lacks
(tapering is not stopped) and a drug record that changes itself from a logging
action is untrustworthy. Instead:

  a) A reconciliation line whenever a medicine is active/tapering here AND
     absent from GutLog's regimen AND has had no dose for 7+ days. One tap
     sets status and stop_date from GutLog's valid_to -- not from today,
     because the day a mismatch is noticed is not the day the drug changed.
  b) The mirror case, inverted: in GutLog's regimen, unknown here. One tap adds
     it as active from GutLog's valid_from.
  c) Any RED or AMBER finding resting on a drug GutLog has not seen for 14+
     days is marked POSSIBLY STALE on the finding itself -- the same discipline
     the knowledge base already applies to its own review dates. The finding is
     never silently dropped.

Needs GutLog v3.12.0 for the `ended` list and `valid_from`; degrades to a line
without a one-tap stop date against an older GutLog.

Requires v1.4.0 (RXGUARD_V140_CONDITIONS). Anchor-verified, idempotent,
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
MARKER = "RXGUARD_V150_RECONCILE"
PREV = "RXGUARD_V140_CONDITIONS"

PY = '''# --------------------------------------------------------------------------
# Reconciliation -- RXGUARD_V150_RECONCILE
# GutLog knows what is being taken. RxGuard knows what it MEANS: tapering is
# not stopped, and "not logged" is not "not taken" for a patch or an eye drop.
# So nothing below writes by itself. It reports the disagreement and one
# deliberate tap applies it, carrying GutLog's own dates rather than today's.
# --------------------------------------------------------------------------
RECONCILE_GAP_DAYS = 7
GUT_STALE_DAYS = 14


def _gut_index(data):
    """(regimen key -> row, key -> last dose day, key -> schedule end date)."""
    reg, last, ended = {}, {}, {}
    for r in data.get("regimen") or []:
        for k in _split_molecules(r.get("molecule")):
            reg.setdefault(k, r)
    for t in data.get("taken") or []:
        for k in _split_molecules(t.get("molecule")):
            if (t.get("last_day") or "") > last.get(k, ""):
                last[k] = t.get("last_day") or ""
    for e in data.get("ended") or []:
        for k in _split_molecules(e.get("molecule")):
            if (e.get("valid_to") or "") > ended.get(k, ""):
                ended[k] = e.get("valid_to") or ""
    return reg, last, ended


def _days_since(day):
    try:
        return (date.today() - date.fromisoformat(day)).days
    except (ValueError, TypeError):
        return None


def reconcile_view(data):
    """The two directions of the disconnect, neither of them applied."""
    reg, last, ended = _gut_index(data)
    listed = dict((m["drug_key"], m) for m in active_meds())
    stopped, missing = [], []
    for key in sorted(listed):
        if key in reg:
            continue
        gap = _days_since(last.get(key, ""))
        if gap is not None and gap < RECONCILE_GAP_DAYS:
            continue
        m = listed[key]
        stopped.append({"med_id": m["id"], "key": key, "name": display_name(key),
                        "dose": m["dose"] or "", "status": m["status"],
                        "valid_to": ended.get(key, ""), "last_dose": last.get(key, ""),
                        "gap": gap})
    for key in sorted(reg):
        if key in listed:
            continue
        r = reg[key]
        missing.append({"key": key, "name": display_name(key),
                        "gut_name": r.get("name") or "",
                        "valid_from": r.get("valid_from") or "",
                        "known": bool(get_drug(key))})
    return {"stopped": stopped, "missing": missing,
            "gap_days": RECONCILE_GAP_DAYS, "stale_days": GUT_STALE_DAYS}


def gut_stale_keys(data):
    """Molecules GutLog has not seen for GUT_STALE_DAYS or more: absent from
    its current regimen and with no dose inside the feed window. A finding that
    rests on one of these may rest on a drug already stopped."""
    reg, last, _ended = _gut_index(data)
    out = set()
    for key in set(m["drug_key"] for m in active_meds()) | set(last):
        if key in reg:
            continue
        gap = _days_since(last.get(key, ""))
        if gap is None or gap >= GUT_STALE_DAYS:
            out.add(key)
    return out


def mark_gut_stale(findings, data):
    """Flag, never drop. A RED that may be about a stopped drug is still a RED
    until someone decides otherwise; it just says so on its face."""
    keys = gut_stale_keys(data)
    n = 0
    for f in findings:
        if f["flag"] not in ("RED", "AMBER"):
            continue
        hits = sorted(set(f.get("involves") or []) & keys)
        if not hits:
            continue
        f["gut_stale"] = (
            "GutLog has no dose of %s in the last %d days and it is not in the regimen "
            "there, so this finding may rest on a medicine already stopped. Reconcile it "
            "above, or confirm it is still taken." %
            (", ".join(display_name(k) for k in hits), GUT_STALE_DAYS))
        n += 1
    return n


'''

PY_ANCHOR = "def astaken_summary():\n"

REC_BLOCK = """
        {% if v.rec.stopped or v.rec.missing %}
        <h2>Reconciliation</h2>
        <p class="muted">GutLog and this list disagree. Nothing is changed automatically:
        status here carries clinical meaning GutLog does not have &mdash; tapering is not
        stopped &mdash; and a drug record that rewrites itself from a logging action is
        untrustworthy. One tap applies it, using GutLog's date.</p>
        {% for r in v.rec.stopped %}
        <div class="card"><strong>{{ r.name }}{% if r.dose %} {{ r.dose }}{% endif %}</strong>
        {% if r.valid_to %}&mdash; ended in GutLog on {{ r.valid_to }}, still
        <strong>{{ r.status }}</strong> here.
        <form method="post" action="{{ url_for('reconcile_apply') }}" style="display:inline">
        <input type="hidden" name="action" value="stop">
        <input type="hidden" name="drug_key" value="{{ r.key }}">
        <input type="hidden" name="days" value="{{ days }}">
        <button>Mark stopped on {{ r.valid_to }}</button></form>
        {% else %}&mdash; not in GutLog's regimen{% if r.last_dose %} and no dose since
        {{ r.last_dose }} ({{ r.gap }} days){% else %} and no dose in the last {{ v.days }}
        days{% endif %}, still <strong>{{ r.status }}</strong> here.
        <p class="muted" style="margin:6px 0 0">GutLog records no end date for it, so a stop
        date cannot be taken from there. Stop it on the
        <a href="{{ url_for('meds') }}">Medications</a> page if that is right.</p>{% endif %}
        </div>
        {% endfor %}
        {% for r in v.rec.missing %}
        <div class="card"><strong>{{ r.name }}</strong> &mdash; in GutLog's regimen{% if r.gut_name %}
        as {{ r.gut_name }}{% endif %}{% if r.valid_from %} since {{ r.valid_from }}{% endif %},
        not on your RxGuard list, so every check except this page leaves it out.
        <form method="post" action="{{ url_for('reconcile_apply') }}" style="display:inline">
        <input type="hidden" name="action" value="add">
        <input type="hidden" name="drug_key" value="{{ r.key }}">
        <input type="hidden" name="days" value="{{ days }}">
        <button>Add as active{% if r.valid_from %}, started {{ r.valid_from }}{% endif %}</button></form>
        {% if not r.known %}<p class="muted" style="margin:6px 0 0">Not in the knowledge base, so
        adding it here still will not make it checkable until it is in
        <span class="dose">knowledge/drugs.json</span>.</p>{% endif %}
        </div>
        {% endfor %}
        {% endif %}
"""

ROUTE = '''    # ------------------------------------------------ reconcile (v1.5.0)
    @app.route("/reconcile", methods=["POST"])
    @login_required
    def reconcile_apply():
        """One deliberate tap. The stop date comes from GutLog's valid_to and
        the start date from its valid_from, never from today: the day a
        mismatch is noticed is not the day the drug changed."""
        act = request.form.get("action", "")
        key = norm_key(request.form.get("drug_key", ""))
        try:
            days = int(request.form.get("days") or 14)
        except ValueError:
            days = 14
        data, err = gutlog_stack(days if days in (14, 30, 90) else 14)
        if err or not data:
            flash("GutLog is not answering, so nothing was changed. " + (err or ""))
            return redirect(url_for("astaken"))
        rec = reconcile_view(data)
        db = get_db()
        today_s = date.today().isoformat()
        if act == "stop":
            row = None
            for r in rec["stopped"]:
                if r["key"] == key:
                    row = r
            if not row:
                flash("That medicine no longer meets the reconciliation test. Nothing changed.")
            elif not row["valid_to"]:
                flash("GutLog records no end date for %s, so the stop date cannot come from "
                      "it. Stop it on the Medications page if that is right." % row["name"])
            else:
                db.execute("UPDATE medications SET status='stopped', stop_date=?, "
                           "last_change=? WHERE id=?",
                           (row["valid_to"], today_s, row["med_id"]))
                db.execute("INSERT INTO med_events (med_id, drug_key, event_date, action, "
                           "old_dose, new_dose, source, note) VALUES (?,?,?,?,?,?,?,?)",
                           (row["med_id"], key, row["valid_to"], "stop", row["dose"], "",
                            "gutlog", "Ended in GutLog on " + row["valid_to"]
                            + "; confirmed here on " + today_s + "."))
                db.commit()
                flash("%s marked stopped as of %s, the day GutLog ended it."
                      % (row["name"], row["valid_to"]))
        elif act == "add":
            row = None
            for r in rec["missing"]:
                if r["key"] == key:
                    row = r
            if not row:
                flash("That medicine is no longer missing from this list. Nothing changed.")
            else:
                start = row["valid_from"] or today_s
                cur = db.execute(
                    "INSERT INTO medications (drug_key, raw_name, dose, frequency, route, "
                    "indication, prescriber, specialty, kind, status, benefit, start_date, "
                    "last_change, last_reviewed_by_prescriber, notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, row["gut_name"] or row["name"], "", "", "oral", "", "", "",
                     "chronic", "active", "unknown", start, today_s, "",
                     "Added from GutLog's regimen."))
                db.execute("INSERT INTO med_events (med_id, drug_key, event_date, action, "
                           "old_dose, new_dose, source, note) VALUES (?,?,?,?,?,?,?,?)",
                           (cur.lastrowid, key, start, "start", "", "", "gutlog",
                            "In GutLog's regimen; added here on " + today_s + "."))
                db.commit()
                flash("%s added as active from %s. Give it a dose, a prescriber and a "
                      "benefit on the Medications page." % (row["name"], start))
        else:
            flash("Unknown action. Nothing changed.")
        return redirect(url_for("astaken"))

'''

ROUTE_ANCHOR = "    # ------------------------------------------------ your review (v1.3.0)\n"


def build_edits():
    E = []

    a = ('APP_VERSION = "1.4.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
         'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS')
    E.append(("version", a,
              'APP_VERSION = "1.5.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
              'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS ' + MARKER))

    E.append(("python", PY_ANCHOR, PY + PY_ANCHOR))

    a = ('    red = sum(1 for f in findings if f["flag"] == "RED")\n'
         '    amber = sum(1 for f in findings if f["flag"] == "AMBER")\n'
         '    return {"rows": rows, "findings": findings, "not_listed": not_listed,\n'
         '            "listed_not_taken": [display_name(k) for k in listed_not_taken],\n'
         '            "unknown": unknown, "unmapped": unmapped, "red": red, "amber": amber,\n'
         '            "since": data.get("since", ""), "days": data.get("days", "")}\n')
    n = ('    stale_n = mark_gut_stale(findings, data)\n'
         '    red = sum(1 for f in findings if f["flag"] == "RED")\n'
         '    amber = sum(1 for f in findings if f["flag"] == "AMBER")\n'
         '    return {"rows": rows, "findings": findings, "not_listed": not_listed,\n'
         '            "listed_not_taken": [display_name(k) for k in listed_not_taken],\n'
         '            "unknown": unknown, "unmapped": unmapped, "red": red, "amber": amber,\n'
         '            "rec": reconcile_view(data), "stale_findings": stale_n,\n'
         '            "since": data.get("since", ""), "days": data.get("days", "")}\n')
    E.append(("astaken_view", a, n))

    a = ('    v = astaken_view(data)\n'
         '    return {"err": "", "red": v["red"], "amber": v["amber"],\n'
         '            "not_listed": len(v["not_listed"]), "unknown": len(v["unknown"]) + len(v["unmapped"])}\n')
    n = ('    v = astaken_view(data)\n'
         '    return {"err": "", "red": v["red"], "amber": v["amber"],\n'
         '            "not_listed": len(v["not_listed"]), "unknown": len(v["unknown"]) + len(v["unmapped"]),\n'
         '            "reconcile": len(v["rec"]["stopped"]) + len(v["rec"]["missing"]),\n'
         '            "stale_findings": v["stale_findings"]}\n')
    E.append(("astaken_summary", a, n))

    a = ('    {% if f.source %}<dt>Source</dt><dd class="muted">{{ f.source }}\n'
         '      {% if f.reviewed %} · reviewed {{ f.reviewed }}{% endif %}\n'
         '      {% if f.stale %}<span class="stale">STALE</span>{% endif %}</dd>{% endif %}\n'
         '  </dl>\n')
    n = ('    {% if f.source %}<dt>Source</dt><dd class="muted">{{ f.source }}\n'
         '      {% if f.reviewed %} · reviewed {{ f.reviewed }}{% endif %}\n'
         '      {% if f.stale %}<span class="stale">STALE</span>{% endif %}</dd>{% endif %}\n'
         '    {% if f.gut_stale %}<dt>Possibly stale</dt>\n'
         '      <dd><span class="stale">CHECK</span> {{ f.gut_stale }}</dd>{% endif %}\n'
         '  </dl>\n')
    E.append(("finding block", a, n))

    a = "        <h2>Taken, and whether RxGuard can see it</h2>\n"
    E.append(("astaken reconcile section", a, REC_BLOCK + a))

    E.append(("reconcile route", ROUTE_ANCHOR, ROUTE + ROUTE_ANCHOR))

    a = ('        {{ ast.not_listed }} taken but not on this list &middot; {{ ast.unknown }} not checkable\n'
         '        &middot; <a href="{{ url_for(\'astaken\') }}">Open</a></div>\n')
    n = ('        {{ ast.not_listed }} taken but not on this list &middot; {{ ast.unknown }} not checkable\n'
         '        &middot; <a href="{{ url_for(\'astaken\') }}">Open</a>\n'
         '        {% if ast.reconcile %}<p class="muted" style="margin:6px 0 0">\n'
         '        <strong>{{ ast.reconcile }}</strong> medicine{{ \'\' if ast.reconcile == 1 else \'s\' }}\n'
         '        where GutLog and this list disagree{% if ast.stale_findings %},\n'
         '        and {{ ast.stale_findings }} finding{{ \'\' if ast.stale_findings == 1 else \'s\' }}\n'
         '        that may rest on a medicine already stopped{% endif %} &middot;\n'
         '        <a href="{{ url_for(\'astaken\') }}">Reconcile</a></p>{% endif %}</div>\n')
    E.append(("dashboard line", a, n))

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
    print("RxGuard reconciliation -> v1.5.0")
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
        print("FATAL: this file is not at v1.4.0. Apply that first.")
        return 1
    if "def reconcile_view" in src:
        print("FATAL: reconciliation pieces already present. Nothing written.")
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
    bak = args.file + ".bak-v150-" + stamp
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
    print("Next:  python3 test_reconcile.py /root/gutlog/app.py   then  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

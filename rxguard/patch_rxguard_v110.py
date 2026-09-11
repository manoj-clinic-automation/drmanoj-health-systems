#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.0.0 -> v1.1.0  ::  As taken (GutLog)

RxGuard checks the list you type into it. GutLog records what you actually
take. Until now nothing compared the two, so a medicine taken every day but
never added here was invisible to every check.

New page "As taken (GutLog)" (and a one-line summary on the Dashboard):

  * reads GutLog's read-only feed over loopback -- regimen plus every dose
    taken in the last 14 / 30 / 90 days;
  * shows, per medicine: its molecule, how often it was taken, whether it is
    on the RxGuard list, and whether the knowledge base covers it;
  * runs the existing engine across the whole as-taken stack (your list plus
    everything GutLog shows taken): named pairwise rules, CYP derivation
    (suppressed where a named rule covers the pair), class duplication,
    condition rules, cumulative burden and QT stacking. Findings that involve
    a medicine missing from your list are marked, because those are the ones
    no other screen here has ever shown;
  * molecules outside the knowledge base, and GutLog medicines with no
    molecule mapped, are reported as UNKNOWN -- never passed over;
  * lists what is on your RxGuard list but not logged in GutLog.

Nothing is written. There is still no GREEN. If GutLog is unreachable the
page says so and every other screen works exactly as before.

Feed: GUTLOG_FEED_URL (default http://127.0.0.1:8020) and the token file
GUTLOG_FEED_TOKEN_FILE (default /root/gutlog/feed.token, created by GutLog).

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring on post-write failure. Python 3.9.
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V110_ASTAKEN"

ENGINE = r'''# --------------------------------------------------------------------------
# As taken (GutLog) -- RXGUARD_V110_ASTAKEN
# --------------------------------------------------------------------------
# GutLog records what is actually taken; this compares it with the list
# typed in here and runs the engine across both. Read-only. A GutLog outage
# must never take RxGuard down, so every failure becomes a message.

GUTLOG_FEED_URL = os.environ.get("GUTLOG_FEED_URL", "http://127.0.0.1:8020")
GUTLOG_TOKEN_FILE = os.environ.get("GUTLOG_FEED_TOKEN_FILE", "/root/gutlog/feed.token")


def gutlog_feed_enabled():
    """The feed follows the live database. A scratch or test database (any
    path outside the app directory) never sees real doses, so a test suite
    can never be coloured by the owner's diary. RXGUARD_GUTLOG_FEED=1 / 0
    overrides."""
    v = os.environ.get("RXGUARD_GUTLOG_FEED", "")
    if v in ("0", "1"):
        return v == "1"
    try:
        return os.path.dirname(os.path.abspath(g.db_path)) == BASE_DIR
    except Exception:
        return False


def gutlog_stack(days=14):
    """(data, error). Never raises."""
    import urllib.request
    import urllib.error
    if not gutlog_feed_enabled():
        return None, "Not connected for this database (a test or scratch copy)."
    try:
        with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
    except OSError:
        return None, "GutLog feed token not found at %s." % GUTLOG_TOKEN_FILE
    url = GUTLOG_FEED_URL.rstrip("/") + "/api/feed/stack?days=%d" % int(days)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, "GutLog refused the request (HTTP %d)." % e.code
    except Exception as e:
        return None, "GutLog is not reachable (%s)." % type(e).__name__
    if not isinstance(data, dict) or not data.get("ok"):
        return None, "GutLog returned an unexpected answer."
    return data, None


def _split_molecules(mol):
    out = []
    for part in (mol or "").replace("+", ",").split(","):
        part = part.strip()
        if part:
            out.append(norm_key(part))
    return out


def astaken_view(data):
    listed = {}
    for m in active_meds():
        listed.setdefault(m["drug_key"], m)
    items, unmapped = {}, []

    def add(key, name, src, doses=0, days=0):
        it = items.setdefault(key, {"key": key, "names": [], "sources": set(),
                                    "doses": 0, "days": 0, "scheduled": False})
        if name and name not in it["names"]:
            it["names"].append(name)
        it["sources"].add(src)
        it["doses"] += doses
        it["days"] = max(it["days"], days)
        if src == "regimen":
            it["scheduled"] = True

    for r in data.get("regimen") or []:
        keys = _split_molecules(r.get("molecule"))
        if not keys:
            if r.get("name") and r["name"] not in unmapped:
                unmapped.append(r["name"])
            continue
        for k in keys:
            add(k, r.get("name"), "regimen")
    for t in data.get("taken") or []:
        keys = _split_molecules(t.get("molecule"))
        if not keys:
            if t.get("name") and t["name"] not in unmapped:
                unmapped.append(t["name"])
            continue
        for k in keys:
            add(k, t.get("name"), "taken", int(t.get("doses") or 0), int(t.get("days") or 0))
    gut_keys = set(items)
    for k in listed:
        if k not in items:
            add(k, "", "list")

    not_listed = sorted(k for k in gut_keys if k not in listed)
    listed_not_taken = sorted(k for k in listed if k not in gut_keys)
    unknown = sorted(k for k in items if not get_drug(k))
    keys = sorted(items)
    conditions = active_conditions()
    findings = stack_findings(keys, conditions, set(not_listed))

    if not_listed:
        findings.append(finding(
            "AMBER", "Reconciliation",
            "%d taken medicine%s not on your RxGuard list"
            % (len(not_listed), "" if len(not_listed) == 1 else "s"),
            mechanism="Taken according to GutLog: %s." % ", ".join(display_name(k) for k in not_listed),
            consequence="The Dashboard, Quick check and Full analysis only see the list typed in "
                        "here, so these medicines are left out of every check except this page.",
            action="Add them under Medications if they are part of the regimen.",
            source="GutLog feed, last %s days." % data.get("days", ""),
            reviewed=date.today().isoformat()))
    if unknown:
        findings.append(finding(
            "UNKNOWN", "Coverage",
            "%d molecule%s not in the knowledge base"
            % (len(unknown), "" if len(unknown) == 1 else "s"),
            mechanism="No property record for: %s." % ", ".join(display_name(k) for k in unknown),
            consequence="No interaction, burden or dosing check has been performed on these. "
                        "This is a gap in coverage, not a safety finding.",
            action="Add them to knowledge/drugs.json before relying on this page for them.",
            source="Coverage check.", reviewed=date.today().isoformat()))
    if unmapped:
        findings.append(finding(
            "UNKNOWN", "Coverage",
            "%d GutLog medicine%s with no molecule recorded"
            % (len(unmapped), "" if len(unmapped) == 1 else "s"),
            mechanism="In GutLog without a molecule: %s." % ", ".join(unmapped),
            consequence="They cannot be matched to anything here, so they are not checked at all.",
            action="Record the molecule for each in GutLog if it is a single molecule.",
            source="GutLog feed.", reviewed=date.today().isoformat()))
    findings.sort(key=lambda f: (FLAG_ORDER.get(f["flag"], 3), not f.get("unlisted"), f["category"]))

    rows = []
    for k in keys:
        it = items[k]
        rows.append({"key": k, "name": display_name(k), "gut": ", ".join(it["names"]),
                     "doses": it["doses"], "days": it["days"], "scheduled": it["scheduled"],
                     "listed": k in listed, "known": bool(get_drug(k)),
                     "in_gutlog": k in gut_keys})
    red = sum(1 for f in findings if f["flag"] == "RED")
    amber = sum(1 for f in findings if f["flag"] == "AMBER")
    return {"rows": rows, "findings": findings, "not_listed": not_listed,
            "listed_not_taken": [display_name(k) for k in listed_not_taken],
            "unknown": unknown, "unmapped": unmapped, "red": red, "amber": amber,
            "since": data.get("since", ""), "days": data.get("days", "")}


def stack_findings(keys, conditions, unlisted):
    """The engine across a whole list at once, each pair judged once."""
    known = [k for k in keys if get_drug(k)]
    totals, contributors = compute_burdens(known)
    rule_pairs = dict((r["id"], (r["a"], r["b"])) for r in PAIRWISE)
    out = []

    def keep(f, involved):
        f.pop("pair", None)
        f["involves"] = sorted(involved)
        f["unlisted"] = bool(set(involved) & unlisted)
        out.append(f)

    covered = set()
    for i, a in enumerate(known):
        later = known[i + 1:]
        for f in pairwise_findings(a, later):
            pr = rule_pairs.get(f["rule_id"], (a, ""))
            covered.add(frozenset(pr))
            keep(f, pr)
    for i, a in enumerate(known):
        later = known[i + 1:]
        for f in cyp_findings(a, later):
            pr = f.get("pair")
            if pr is not None and pr in covered:
                continue
            keep(f, tuple(pr) if pr is not None else (a,))
        da = get_drug(a)
        for b in later:
            db_ = get_drug(b)
            if da.get("class") and da.get("class") == db_.get("class"):
                keep(finding(
                    "AMBER", "Duplication",
                    "Same class taken together: %s and %s" % (display_name(a), display_name(b)),
                    mechanism="Both are classified as: %s." % da.get("class"),
                    consequence="Duplicate therapy, with additive class-specific adverse effects.",
                    source="Class comparison from drug property table.",
                    reviewed=da.get("reviewed", "")), (a, b))
    seen = {}
    for a in known:
        for f in condition_findings(a, conditions, totals):
            k = f.get("rule_id") or f["title"]
            if k in seen:
                seen[k]["involves"] = sorted(set(seen[k]["involves"]) | {a})
                seen[k]["unlisted"] = seen[k]["unlisted"] or a in unlisted
                continue
            keep(f, (a,))
            seen[k] = out[-1]
    names = dict((display_name(k), k) for k in known)
    for f in burden_findings(totals, contributors, conditions, None):
        bk = f["title"].split(" ")[0].lower()
        keep(f, tuple(names[n] for n, _v in contributors.get(bk, []) if n in names))
    for f in qt_findings(known, conditions, None):
        keep(f, tuple(k for k in known if get_drug(k).get("qt", "none") != "none"))
    return out


def astaken_summary():
    if not gutlog_feed_enabled():
        return None
    data, err = gutlog_stack(14)
    if err:
        return {"err": err}
    v = astaken_view(data)
    return {"err": "", "red": v["red"], "amber": v["amber"],
            "not_listed": len(v["not_listed"]), "unknown": len(v["unknown"]) + len(v["unmapped"])}


'''

ROUTE = r'''    # ------------------------------------------------ as taken (GutLog)
    @app.route("/astaken")
    @login_required
    def astaken():
        try:
            days = int(request.args.get("days") or 14)
        except ValueError:
            days = 14
        days = days if days in (14, 30, 90) else 14
        data, err = gutlog_stack(days)
        view = astaken_view(data) if data else None
        body = """
        <h1>As taken (GutLog)</h1>
        <p class="sub">What GutLog shows was actually taken, checked against this knowledge base.
        Read-only &middot;
        {% for d in (14, 30, 90) %}<a href="{{ url_for('astaken', days=d) }}"
          {% if d==days %}style="font-weight:700"{% endif %}>{{ d }} days</a>{% if not loop.last %} &middot; {% endif %}{% endfor %}</p>
        {% if err %}
        <div class="card"><strong>GutLog feed unavailable.</strong>
        <p class="muted" style="margin:6px 0 0">{{ err }} Every other screen works as usual;
        this page cannot compare anything until GutLog answers.</p></div>
        {% else %}
        <div class="card"><strong>{{ v.rows|length }} molecules</strong> across GutLog and your list
        &middot; <span class="flag RED">{{ v.red }} RED</span> <span class="flag AMBER">{{ v.amber }} AMBER</span>
        &middot; {{ v.not_listed|length }} taken but not on your list
        &middot; {{ v.unknown|length + v.unmapped|length }} not checkable
        <p class="muted" style="margin:6px 0 0">Absence of a flag means nothing was found in this knowledge
        base, not that the combination is safe.</p></div>
        <h2>Taken, and whether RxGuard can see it</h2>
        <table><tr><th>Molecule</th><th>In GutLog as</th><th>Taken</th><th>On your list</th><th>Covered</th></tr>
        {% for r in v.rows %}<tr>
        <td>{{ r.name }}</td><td class="muted">{{ r.gut or '-' }}</td>
        <td class="num">{% if r.in_gutlog %}{% if r.doses %}{{ r.doses }} dose{{ 's' if r.doses != 1 }}, {{ r.days }} d{% endif %}{% if r.scheduled %} <span class="chip">regimen</span>{% endif %}{% else %}<span class="muted">not logged</span>{% endif %}</td>
        <td>{% if r.listed %}yes{% else %}<span class="flag AMBER">NO</span>{% endif %}</td>
        <td>{% if r.known %}yes{% else %}<span class="flag UNKNOWN">NO</span>{% endif %}</td>
        </tr>{% endfor %}</table>
        <h2>Findings across the whole as-taken stack</h2>
        {% for f in v.findings %}{% if f.unlisted %}<p class="muted" style="margin:0 0 3px">
          <span class="chip">involves a medicine not on your list</span></p>{% endif %}
        {% set findings = [f] %}""" + FINDING_BLOCK + """{% else %}
        <p class="muted">Nothing raised from this knowledge base.</p>{% endfor %}
        {% if v.listed_not_taken %}
        <h2>On your list, not logged in GutLog</h2>
        <p class="muted">Stopped, or taken but not logged? Patches, drops and anything GutLog does not
        carry will always appear here.</p>
        <p>{% for n in v.listed_not_taken %}<span class="chip">{{ n }}</span>{% endfor %}</p>
        {% endif %}
        <p class="muted">GutLog feed since {{ v.since }} &middot; knowledge base {{ kbv }}</p>
        {% endif %}
        """
        return page(body, nav="astaken", title="As taken", err=err, v=view, days=days,
                    kbv="%s/%s" % (DRUGS_DOC["_meta"]["version"], RULES_DOC["_meta"]["version"]))

'''

DASH_CARD = r'''        {% if ast and not ast.err %}
        <div class="card"><strong>As taken (GutLog), last 14 days:</strong>
        {% if ast.red %}<span class="flag RED">{{ ast.red }} RED</span>{% endif %}
        {% if ast.amber %}<span class="flag AMBER">{{ ast.amber }} AMBER</span>{% endif %}
        {{ ast.not_listed }} taken but not on this list &middot; {{ ast.unknown }} not checkable
        &middot; <a href="{{ url_for('astaken') }}">Open</a></div>
        {% elif ast %}<p class="muted">As taken (GutLog): {{ ast.err }}</p>{% endif %}
'''


def build_edits():
    E = []
    a = 'APP_VERSION = "1.0.0"'
    E.append(("version", a, 'APP_VERSION = "1.1.0"   # ' + MARKER))
    a = "# --------------------------------------------------------------------------\n# Templates\n"
    E.append(("engine", a, ENGINE + a))
    a = ''' <a href="{{ url_for('card') }}" class="{{ 'on' if nav=='card' }}">One-page list</a>\n'''
    n = a + ''' <a href="{{ url_for('astaken') }}" class="{{ 'on' if nav=='astaken' }}">As taken (GutLog)</a>\n'''
    E.append(("nav link", a, n))
    a = "        <h2>Active medications</h2>\n"
    E.append(("dashboard card", a, DASH_CARD + a))
    a = '        return page(body, nav="dash", title="Dashboard", meds=meds, conds=conds,\n'
    n = '        return page(body, nav="dash", title="Dashboard", meds=meds, conds=conds, ast=astaken_summary(),\n'
    E.append(("dashboard data", a, n))
    a = '    @app.route("/healthz")\n'
    E.append(("route", a, ROUTE + a))
    return E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("RxGuard as-taken check -> v1.1.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if "def astaken" in src:
        print("FATAL: as-taken code already present -- unexpected state. Nothing written.")
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
    bak = args.file + ".bak-v110-" + stamp
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
    print("Next:  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

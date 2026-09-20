#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.7.0 -> v1.8.0  ::  Daily dose -- the total per ingredient

THE ASK (19-Sep-2026)
---------------------
Watch the cumulative daily dose of each ingredient, not only duplicates: a
component hidden inside a fixed-dose combination counts into the same pool
as the standalone product; one generic by two routes is one pool; classes
carry a load of their own.

WHAT CHANGES
  1. dose_ceiling.py (new file beside app.py) -- the engine. Pure functions,
     rule IDs DC001-DC009, no network, no database.
  2. knowledge/dose_rules.local.json (server only, gitignored) -- ceilings,
     classes and the few product overrides. Absent = feature off, no error.
  3. dose_ceilings table -- a ceiling the owner changes or confirms on the
     page overrides the file. Created by SCHEMA; no migration step.
  4. astaken_view() adds the dose findings to the as-taken findings, so the
     page, the Dashboard count and GutLog's banner all carry them with no
     change on the GutLog side.
  5. /dose -- the Daily dose page: each ingredient's total in its window,
     the ceiling, the room left and when it frees; a ceiling editor with
     Confirm; the class rules listed. Nav link under Apps.
  6. /astaken -- a short Daily dose card above the findings.

"Now" is IST, computed from UTC (RXGUARD_UTC_OFFSET_MIN, default 330), so
the window is right whatever the server clock's zone.

No molecule is named in this file or in the code it writes (CLAUDE.md 5d).
Requires v1.7.0. Anchor-verified, idempotent, compile-checked, .bak before
write, self-restoring, --reverse for the negative control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V180_DOSE"
PREV = "RXGUARD_V170_HONEST"

VERSION_OLD = ('APP_VERSION = "1.7.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
               'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS RXGUARD_V150_RECONCILE '
               'RXGUARD_V160_KEYCHECK RXGUARD_V170_HONEST')
VERSION_NEW = ('APP_VERSION = "1.8.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
               'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS RXGUARD_V150_RECONCILE '
               'RXGUARD_V160_KEYCHECK RXGUARD_V170_HONEST ' + MARKER)

SCHEMA_ANCHOR = "CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT);\n"
SCHEMA_NEW = SCHEMA_ANCHOR + (
    "CREATE TABLE IF NOT EXISTS dose_ceilings (\n"
    "  ing TEXT PRIMARY KEY, ceiling REAL, confirmed TEXT DEFAULT '', updated TEXT);\n")

HELPERS_ANCHOR = "def astaken_view(data):\n"
HELPERS = '''# --------------------------------------------------------------------------
# Daily dose -- RXGUARD_V180_DOSE
# The total of each ingredient over a rolling window, held against a ceiling.
# The engine is dose_ceiling.py; the rules name his medicines and live only
# on the server (knowledge/dose_rules.local.json). A ceiling typed on /dose
# wins over the file. Every failure is a message, never an exception.
# --------------------------------------------------------------------------
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import dose_ceiling  # noqa: E402

DOSE_RULES_PATH = os.environ.get("RXGUARD_DOSE_RULES",
                                 os.path.join(KNOWLEDGE_DIR, dose_ceiling.RULES_FILE))
DOSE_FEED_DAYS = 4


def dose_now():
    """IST, from UTC -- the doses are stamped in IST whatever the server's zone."""
    try:
        off = int(os.environ.get("RXGUARD_UTC_OFFSET_MIN", "330"))
    except ValueError:
        off = 330
    return datetime.utcnow() + timedelta(minutes=off)


def gutlog_doses(days=DOSE_FEED_DAYS):
    """(data, error) from GutLog /api/feed/doses. Never raises."""
    import urllib.request
    if not gutlog_feed_enabled():
        return None, "Not connected for this database (a test or scratch copy)."
    try:
        with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
    except OSError:
        return None, "GutLog feed token not found."
    since = (dose_now().date() - timedelta(days=days)).isoformat()
    url = GUTLOG_FEED_URL.rstrip("/") + "/api/feed/doses?since=" + since
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return None, "GutLog dose feed not reachable (%s)." % type(e).__name__
    if not isinstance(data, dict) or not data.get("ok"):
        return None, "GutLog dose feed returned an unexpected answer."
    return data, None


def dose_overrides():
    out = {}
    try:
        for r in get_db().execute("SELECT ing, ceiling, confirmed FROM dose_ceilings").fetchall():
            out[r["ing"]] = {"ceiling": r["ceiling"], "confirmed": r["confirmed"] or ""}
    except Exception:
        pass
    return out


def dose_rules():
    rules, err = dose_ceiling.load_rules(DOSE_RULES_PATH, norm_key)
    if rules is not None:
        dose_ceiling.apply_overrides(rules, dose_overrides())
    return rules, err


def dose_view(stack):
    """{on, err, rows, findings, version, rules}. Off (on=False) when no rules
    file is installed; err set when GutLog cannot be read."""
    out = {"on": False, "err": "", "rows": [], "findings": [], "version": "", "rules": None}
    try:
        rules, err = dose_rules()
        if rules is None:
            out["err"] = err
            return out
        out.update(on=True, rules=rules, version=rules["meta"].get("version", ""))
        ev, err = gutlog_doses()
        if err:
            out["err"] = err
            return out
        res = dose_ceiling.run(ev.get("events") or [], stack or {}, rules, dose_now(),
                               finding, norm_key)
    except Exception as e:
        out["err"] = "Daily dose could not be computed (%s)." % type(e).__name__
        return out
    for f in res["findings"]:
        f["theoretical"] = False
        f["involves"] = []
        f["unlisted"] = False
    out["rows"] = res["rows"]
    out["findings"] = res["findings"]
    return out


'''

VIEW_CALL_OLD = "    findings = stack_findings(keys, conditions, set(not_listed), dosed)\n"
VIEW_CALL_NEW = VIEW_CALL_OLD + (
    "    # RXGUARD_V180_DOSE -- the daily-dose findings count like any other\n"
    "    dose = dose_view(data)\n"
    "    findings.extend(dose[\"findings\"])\n")

VIEW_RET_OLD = '            "since": data.get("since", ""), "days": data.get("days", "")}\n'
VIEW_RET_NEW = ('            "since": data.get("since", ""), "days": data.get("days", ""),\n'
                '            "dose": dose}\n')

NAV_OLD = (" <a href=\"{{ url_for('astaken') }}\" class=\"{{ 'on' if nav=='astaken' }}\">"
           "As taken (GutLog)</a>\n")
NAV_NEW = NAV_OLD + (" <a href=\"{{ url_for('dose_page') }}\" class=\"{{ 'on' if nav=='dose' }}\">"
                     "Daily dose</a>\n")

DOSE_TABLE = """<table><tr><th>Ingredient</th><th>Taken</th><th>Ceiling</th><th>Room</th><th></th></tr>
        {% for r in rows %}<tr>
        <td>{{ r.name }}{% if r.products|length > 1 %}<br><span class="muted">{{ r.products|join(' + ') }}</span>{% endif %}</td>
        <td class="num">{{ '%g'|format(r.total) }} {{ r.unit }}<br><span class="muted">{{ r.window_h|int }} h</span></td>
        <td class="num">{% if r.ceiling is not none %}{{ '%g'|format(r.ceiling) }} {{ r.unit }}{% if not r.confirmed %}<br><span class="chip">default</span>{% endif %}{% else %}<span class="flag UNKNOWN">not set</span>{% endif %}</td>
        <td class="num">{% if r.room is not none %}{% if r.room >= 0 %}{{ '%g'|format(r.room) }} {{ r.unit }}{% else %}<span class="flag RED">over {{ '%g'|format(-r.room) }}</span>{% endif %}{% endif %}</td>
        <td class="muted">{% if r.state == 'over' %}under the ceiling at {{ r.frees }}{% elif r.state == 'at' %}at the ceiling until {{ r.frees }}{% endif %}</td>
        </tr>{% endfor %}</table>"""

ASTAKEN_OLD = "        <h2>Findings from what was actually taken</h2>\n"
ASTAKEN_NEW = ("""        {% if v.dose and v.dose.on %}
        <h2>Daily dose</h2>
        {% if v.dose.err %}<p class="muted">{{ v.dose.err }}</p>
        {% elif v.dose.rows %}{% set rows = v.dose.rows %}""" + DOSE_TABLE + """
        <p class="muted"><a href="{{ url_for('dose_page') }}">Ceilings and class rules &rarr;</a></p>
        {% else %}<p class="muted">Nothing with a ceiling taken in the last few days.
        <a href="{{ url_for('dose_page') }}">Ceilings &rarr;</a></p>{% endif %}
        {% endif %}
""" + ASTAKEN_OLD)

ROUTE_ANCHOR = '    @app.route("/api/feed/status")\n'
ROUTE_NEW = '''    # ------------------------------------------------ daily dose (v1.8.0)
    @app.route("/dose", methods=["GET", "POST"])
    @login_required
    def dose_page():
        """RXGUARD_V180_DOSE -- totals, the ceiling editor, the class rules."""
        if request.method == "POST":
            ing = norm_key(request.form.get("ing", ""))
            raw = (request.form.get("ceiling") or "").strip()
            try:
                val = float(raw) if raw else None
            except ValueError:
                flash("A ceiling must be a number, or blank for none.")
                return redirect(url_for("dose_page"))
            if val is not None and val <= 0:
                flash("A ceiling must be above zero, or blank for none.")
                return redirect(url_for("dose_page"))
            rules, _e = dose_rules()
            if not rules or ing not in rules["ingredients"]:
                flash("Unknown ingredient; nothing changed.")
                return redirect(url_for("dose_page"))
            now_s = dose_now().strftime("%Y-%m-%d %H:%M")
            db = get_db()
            db.execute("INSERT INTO dose_ceilings (ing, ceiling, confirmed, updated) "
                       "VALUES (?, ?, ?, ?) ON CONFLICT(ing) DO UPDATE SET "
                       "ceiling=excluded.ceiling, confirmed=excluded.confirmed, "
                       "updated=excluded.updated", (ing, val, now_s[:10], now_s))
            db.commit()
            flash("Saved and confirmed: %s %s." % (
                rules["ingredients"][ing]["label"],
                ("%g %s" % (val, rules["ingredients"][ing]["unit"])) if val is not None
                else "no ceiling"))
            return redirect(url_for("dose_page"))
        stack, err = gutlog_stack(14)
        dv = dose_view(stack) if stack else {"on": False, "err": err or "", "rows": [],
                                              "findings": [], "version": "", "rules": None}
        if stack is None:
            rules, _e = dose_rules()
            dv["on"] = rules is not None
            dv["rules"] = rules
            dv["version"] = rules["meta"].get("version", "") if rules else ""
        body = """
        <h1>Daily dose</h1>
        <p class="sub">Each ingredient totalled across every product that carries it &mdash;
        combinations, strengths and routes &mdash; over a rolling window, against your ceiling.
        No GREEN: a row without a flag is a total, not a clearance.</p>
        {% if not dv.rules %}
        <div class="card">Dose rules are not installed on this server
        (knowledge/dose_rules.local.json).{% if dv.err %} {{ dv.err }}{% endif %}</div>
        {% else %}
        {% if dv.err %}<div class="card"><strong>GutLog not readable.</strong>
        <p class="muted" style="margin:6px 0 0">{{ dv.err }}</p></div>{% endif %}
        {% if dv.rows %}{% set rows = dv.rows %}''' + DOSE_TABLE + '''{% endif %}
        {% for f in dv.findings %}{% set findings = [f] %}""" + ASTAKEN_FINDING_BLOCK + """{% endfor %}
        <h2>Ceilings</h2>
        <p class="muted">A ceiling marked <span class="chip">default</span> came from the rules
        file and has not been confirmed by you. Saving a row confirms it. Leave the box blank
        for no ceiling.</p>
        <table><tr><th>Ingredient</th><th>Window</th><th>Ceiling</th><th></th></tr>
        {% for k, r in dv.rules.ingredients|dictsort %}{% if not r.class_only %}<tr>
        <td>{{ r.label }}{% if r.consequence %}<br><span class="muted">{{ r.consequence }}</span>{% endif %}</td>
        <td class="num">{{ r.window_h|int }} h{% if r.max_units is not none %}<br><span class="muted">max {{ '%g'|format(r.max_units) }} unit</span>{% endif %}</td>
        <td><form method="post" style="display:flex;gap:6px;align-items:center">
          <input type="hidden" name="ing" value="{{ k }}">
          <input name="ceiling" inputmode="decimal" style="width:6em"
            value="{{ '%g'|format(r.ceiling) if r.ceiling is not none else '' }}"> {{ r.unit }}
          <button>{{ 'Save' if r.confirmed else 'Confirm' }}</button></form></td>
        <td class="muted">{% if r.confirmed %}confirmed {{ r.confirmed }}{% else %}<span class="chip">default</span>{% endif %}</td>
        </tr>{% endif %}{% endfor %}</table>
        <h2>Class rules</h2>
        {% for c in dv.rules.classes %}<div class="card"><strong>{{ c.label }}</strong>
        &middot; {{ c.members|map('replace', '_', ' ')|join(', ') }} &middot; {{ c.window_h|int }} h
        {% if c.consequence %}<p class="muted" style="margin:6px 0 0">{{ c.consequence }}</p>{% endif %}</div>
        {% endfor %}
        <p class="muted">Rules {{ dv.version }} &middot; DC001&ndash;DC009 &middot; the window
        is 20 hours for once-a-day ingredients so a dose taken a little earlier than yesterday's
        is not read as two.</p>
        {% endif %}
        """
        return page(body, nav="dose", title="Daily dose", dv=dv)

''' + ROUTE_ANCHOR


def build_edits():
    return [
        ("version", VERSION_OLD, VERSION_NEW),
        ("schema", SCHEMA_ANCHOR, SCHEMA_NEW),
        ("helpers", HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR),
        ("astaken_view call", VIEW_CALL_OLD, VIEW_CALL_NEW),
        ("astaken_view return", VIEW_RET_OLD, VIEW_RET_NEW),
        ("nav", NAV_OLD, NAV_NEW),
        ("astaken card", ASTAKEN_OLD, ASTAKEN_NEW),
        ("dose route", ROUTE_ANCHOR, ROUTE_NEW),
    ]


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
    """Reconstruct v1.7.0 for tools/NEGATIVE_CONTROL.py."""
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
                    help="reconstruct v1.7.0 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("RxGuard Daily dose: total per ingredient against a ceiling -> v1.8.0")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    engine = os.path.join(os.path.dirname(os.path.abspath(args.file)), "dose_ceiling.py")
    if not os.path.exists(engine):
        print("FATAL: dose_ceiling.py must sit beside app.py first: " + engine)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v1.7.0. Apply that first.")
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
    bak = args.file + ".bak-v180-" + stamp
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

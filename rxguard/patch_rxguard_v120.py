#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.1.0 -> v1.2.0  ::  knowledge from free, verifiable sources

  * Approved additions live in knowledge/drugs.local.json and
    knowledge/rules.local.json (gitignored), merged over the curated base at
    start and whenever they change. The curated base always wins a clash.
  * New page "Sources review" (/kb): drafts built by kb_sync.py for every
    medicine GutLog shows you on that RxGuard does not know -- identity
    (RxNorm), class (ATC), label facts quoted from the FDA label, CYP roles
    from the FDA table, interaction candidates from both drugs' labels and
    DDInter. Tick what to keep, Approve. Also: DDInter candidates between
    medicines already known, PvPI alert links, source status, Fetch now.
  * /api/feed/status (bearer, GutLog's token) -- counts for GutLog's banner.
Nothing enters the knowledge base without the owner's approval.

Needs kb_sources.py and kb_sync.py beside app.py. Anchor-verified,
idempotent, compile-checked, .bak before write, self-restoring. Python 3.9.
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V120_SOURCES"
PREV = "RXGUARD_V110_ASTAKEN"

OVERLAY = r'''
# --------------------------------------------------------------------------
# RXGUARD_V120_SOURCES -- owner-approved additions from free sources.
# The curated files above are the base and always win a clash; the
# .local.json overlay holds only what the owner approved on /kb.
# --------------------------------------------------------------------------
LOCAL_DRUGS = os.path.join(KNOWLEDGE_DIR, "drugs.local.json")
LOCAL_RULES = os.path.join(KNOWLEDGE_DIR, "rules.local.json")
_BASE_DRUGS = dict(DRUGS)
_BASE_SYN = dict(SYNONYMS)
_BASE_PAIRWISE = list(PAIRWISE)
_OVERLAY_MTIME = [None]


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def reload_overlay(force=False):
    m = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in (LOCAL_DRUGS, LOCAL_RULES))
    if not force and m == _OVERLAY_MTIME[0]:
        return
    _OVERLAY_MTIME[0] = m
    ld = _read_json(LOCAL_DRUGS, {})
    lr = _read_json(LOCAL_RULES, {})
    DRUGS.clear()
    DRUGS.update(_BASE_DRUGS)
    for k, v in (ld.get("drugs") or {}).items():
        if k not in _BASE_DRUGS:
            DRUGS[k] = v
    SYNONYMS.clear()
    SYNONYMS.update(_BASE_SYN)
    for k, v in (ld.get("synonyms") or {}).items():
        if k not in SYNONYMS and k not in DRUGS and v in DRUGS:
            SYNONYMS[k] = v
    base_pairs = set(frozenset((r["a"], r["b"])) for r in _BASE_PAIRWISE)
    PAIRWISE[:] = list(_BASE_PAIRWISE) + [
        r for r in (lr.get("pairwise") or [])
        if r.get("a") and r.get("b") and frozenset((r["a"], r["b"])) not in base_pairs]


reload_overlay(force=True)
'''

SCHEMA_ADD = '''
CREATE TABLE IF NOT EXISTS kb_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, term TEXT, strength TEXT,
    status TEXT DEFAULT 'pending', draft_json TEXT, created TEXT, decided TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS kb_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, kind TEXT, flag TEXT, quote TEXT,
    source TEXT, status TEXT DEFAULT 'pending', created TEXT, decided TEXT,
    UNIQUE(a, b, kind, source));

CREATE TABLE IF NOT EXISTS kb_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, url TEXT UNIQUE,
    status TEXT DEFAULT 'new', created TEXT);

CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT);
"""'''

ENGINE = r'''# --------------------------------------------------------------------------
# Sources review helpers -- RXGUARD_V120_SOURCES
# --------------------------------------------------------------------------
def draft_to_entry(d, chosen):
    e = {"class": "", "atc": "", "rxcui": (d.get("identity") or {}).get("rxcui", ""),
         "cyp": {"substrate": {}, "inhibitor": {}, "inducer": {}},
         "burden": dict((k, 0) for k in BURDEN_KEYS), "qt": "none", "hr": "none", "bp": "none"}
    for p in chosen:
        f, v = p["field"], p["value"]
        if f in ("class", "atc", "qt", "hr", "bp", "renal", "hepatic", "withdrawal_note"):
            e[f] = v
        elif f.startswith("burden."):
            try:
                e["burden"][f.split(".", 1)[1]] = int(v)
            except (ValueError, KeyError):
                pass
        elif f.startswith("cyp."):
            parts = f.split(".", 2)
            if len(parts) == 3 and parts[1] in e["cyp"]:
                e["cyp"][parts[1]][parts[2]] = v
    if not d.get("label_set_id"):
        e["no_us_label"] = True
    e["source"] = "; ".join(s["name"] for s in d.get("sources") or []) + \
        " -- drafted by RxGuard from free sources, approved by owner"
    e["reviewed"] = date.today().isoformat()
    e["evidence"] = chosen
    e["gaps"] = d.get("gaps") or []
    e["strength_logged"] = d.get("strength", "")
    e["notes"] = "Approved from sources review. Fields the sources did not settle " \
                 "are at their defaults and listed under gaps."
    return e


def _next_rule_id(rules, prefix):
    n = 0
    for r in rules:
        rid = r.get("id", "")
        if rid.startswith(prefix) and rid[len(prefix):].isdigit():
            n = max(n, int(rid[len(prefix):]))
    return "%s%03d" % (prefix, n + 1)


def approve_pairs(pairs):
    doc = _read_json(LOCAL_RULES, {"pairwise": []})
    rules = doc.setdefault("pairwise", [])
    have = set((r["a"], r["b"], r.get("source", "")) for r in rules)
    for p in pairs:
        k = (p["a"], p["b"], p["source"])
        if k in have or (p["b"], p["a"], p["source"]) in have:
            continue
        have.add(k)
        rules.append({
            "id": _next_rule_id(rules, "DD" if p.get("kind") == "ddinter" else "LB"),
            "a": p["a"], "b": p["b"], "flag": p["flag"],
            "title": "%s + %s: %s" % (display_name(p["a"]), display_name(p["b"]),
                                      "interaction rated %s by DDInter" % p["quote"].split(": ")[-1]
                                      if p.get("kind") == "ddinter" else "named in the FDA label"),
            "mechanism": p["quote"], "consequence": "", "source": p["source"],
            "reviewed": date.today().isoformat(), "origin": p.get("kind", "")})
    _write_json(LOCAL_RULES, doc)
    reload_overlay(force=True)


def approve_entry(key, entry, alias_terms):
    doc = _read_json(LOCAL_DRUGS, {"drugs": {}, "synonyms": {}})
    doc.setdefault("drugs", {})
    doc.setdefault("synonyms", {})
    if entry is not None:
        doc["drugs"][key] = entry
    for t in alias_terms:
        t = norm_key(t) if t else ""
        if t and t != key:
            doc["synonyms"][t] = key
    _write_json(LOCAL_DRUGS, doc)
    reload_overlay(force=True)


def kb_counts():
    db = get_db()
    q = lambda sql: db.execute(sql).fetchone()[0]
    return {"drafts": q("SELECT COUNT(*) FROM kb_drafts WHERE status IN ('pending','source_changed')"),
            "pairs": q("SELECT COUNT(*) FROM kb_pairs WHERE status='pending'"),
            "alerts": q("SELECT COUNT(*) FROM kb_alerts WHERE status='new'")}


'''

ROUTES = r'''    # ------------------------------------------------ sources review (v1.2.0)
    @app.route("/kb")
    @login_required
    def kb_review():
        db = get_db()
        drafts = []
        for r in db.execute("SELECT * FROM kb_drafts WHERE status IN ('pending','source_changed') "
                            "ORDER BY id").fetchall():
            d = json.loads(r["draft_json"] or "{}")
            drafts.append({"id": r["id"], "status": r["status"], "d": d})
        pairs = db.execute("SELECT * FROM kb_pairs WHERE status='pending' ORDER BY flag DESC, a, b").fetchall()
        alerts = db.execute("SELECT * FROM kb_alerts WHERE status='new' ORDER BY id").fetchall()
        row = db.execute("SELECT value FROM kb_meta WHERE key='sources'").fetchone()
        src = json.loads(row["value"]) if row else {}
        done = db.execute("SELECT status, COUNT(*) n FROM kb_drafts GROUP BY status").fetchall()
        body = """
        <h1>Sources review</h1>
        <p class="sub">Drafts built from free, verifiable sources for medicines GutLog shows you on.
        Nothing enters the knowledge base until you approve it. Every line shows where it came from.</p>
        <div class="card"><strong>Sources</strong>
        {% if src %}<p class="muted" style="margin:6px 0 0">Last run {{ src.run }} &middot;
        GutLog {{ src.gutlog }} &middot;
        FDA CYP table {% if src.fda_cyp and src.fda_cyp.ok %}{{ src.fda_cyp.rows }} rows ({{ src.fda_cyp.fetched }}){% else %}{{ (src.fda_cyp or {}).get('err','-') }}{% endif %} &middot;
        DDInter {% if src.ddinter and src.ddinter.ok %}{{ src.ddinter.pairs }} pairs ({{ src.ddinter.fetched }}){% else %}{{ (src.ddinter or {}).get('err','-') }}{% endif %} &middot;
        PvPI {{ src.pvpi }}</p>
        {% else %}<p class="muted" style="margin:6px 0 0">No sync has run yet.</p>{% endif %}
        <form method="post" action="{{ url_for('kb_fetch') }}" style="margin-top:8px">
        <button class="ghost">Fetch now</button></form></div>

        <h2>New medicines ({{ drafts|length }})</h2>
        {% for x in drafts %}{% set d = x.d %}
        <div class="card">
        <form method="post" action="{{ url_for('kb_decide', did=x.id) }}">
        {% if d.alias_of %}
          <strong>{{ d.display }}</strong> <span class="muted">(GutLog: {{ d.gutlog_name }})</span>
          <p>RxNorm identifies this as <strong>{{ d.alias_of.replace('_',' ') }}</strong>, which the knowledge
          base already covers. Approve to link the name.</p>
        {% else %}
          <strong>{{ d.display }}</strong>{% if d.strength %} <span class="dose">{{ d.strength }}</span>{% endif %}
          <span class="muted">(GutLog: {{ d.gutlog_name }}{% if x.status=='source_changed' %}; <b>its FDA label changed - re-review</b>{% endif %})</span>
          <p class="muted" style="margin:4px 0">{% for s in d.sources %}{{ s.name }}: {% if s.url %}<a href="{{ s.url }}" target="_blank" rel="noopener">{{ s.detail }}</a>{% else %}{{ s.detail }}{% endif %}{% if not loop.last %} &middot; {% endif %}{% endfor %}</p>
          {% if d.props %}<table><tr><th></th><th>Property</th><th>Value</th><th>Evidence</th></tr>
          {% for p in d.props %}<tr><td><input type="checkbox" name="prop" value="{{ loop.index0 }}" checked></td>
          <td>{{ p.field }}</td><td class="dose">{{ p.value if p.value|string|length < 40 else 'see quote' }}</td>
          <td class="muted">&ldquo;{{ p.quote }}&rdquo; <br><span class="cat">{{ p.source }}</span></td></tr>{% endfor %}</table>
          {% else %}<p class="muted">No properties could be drawn from the sources.</p>{% endif %}
          {% if d.gaps %}<p class="muted">Not settled by the sources (stay at defaults): {{ d.gaps|join('; ') }}</p>{% endif %}
          {% if d.pairs %}<h3 style="font-size:14px;margin:12px 0 4px">Interaction candidates with your current medicines</h3>
          <table>{% for p in d.pairs %}<tr><td><input type="checkbox" name="pair" value="{{ loop.index0 }}" checked></td>
          <td><span class="flag {{ p.flag }}">{{ p.flag }}</span></td><td>{{ p.b.replace('_',' ') }}</td>
          <td class="muted">&ldquo;{{ p.quote }}&rdquo;<br><span class="cat">{{ p.source }}</span></td></tr>{% endfor %}</table>{% endif %}
        {% endif %}
        <p style="margin-top:10px"><button name="action" value="approve">Approve ticked</button>
        <button name="action" value="reject" class="ghost">Reject</button></p>
        </form></div>
        {% else %}<p class="muted">Nothing waiting.</p>{% endfor %}

        <h2>Interactions between medicines already known ({{ pairs|length }})</h2>
        {% if pairs %}<form method="post" action="{{ url_for('kb_pairs_decide') }}"><table>
        {% for p in pairs %}<tr><td><input type="checkbox" name="pid" value="{{ p['id'] }}" checked></td>
        <td><span class="flag {{ p['flag'] }}">{{ p['flag'] }}</span></td>
        <td>{{ p['a'].replace('_',' ') }} + {{ p['b'].replace('_',' ') }}</td>
        <td class="muted">&ldquo;{{ p['quote'] }}&rdquo;<br><span class="cat">{{ p['source'] }}</span></td></tr>{% endfor %}</table>
        <p><button name="action" value="approve">Approve ticked</button>
        <button name="action" value="dismiss" class="ghost">Dismiss ticked</button></p></form>
        {% else %}<p class="muted">None waiting.</p>{% endif %}

        <h2>PvPI drug-safety alerts (India)</h2>
        {% if alerts %}<form method="post" action="{{ url_for('kb_alerts_read') }}"><ul>
        {% for a in alerts %}<li><a href="{{ a['url'] }}" target="_blank" rel="noopener">{{ a['title'] }}</a></li>{% endfor %}
        </ul><button class="ghost">Mark read</button></form>
        {% else %}<p class="muted">No new alerts.</p>{% endif %}
        <p class="muted">Decided so far: {% for r in done %}{{ r['status'] }} {{ r['n'] }}{% if not loop.last %} &middot; {% endif %}{% endfor %}.
        DDInter 2.0 data is used under CC BY-NC-SA 4.0 for personal, non-commercial use.</p>
        """
        return page(body, nav="kb_review", title="Sources review", drafts=drafts, pairs=pairs,
                    alerts=alerts, src=src, done=done)

    @app.route("/kb/draft/<int:did>", methods=["POST"])
    @login_required
    def kb_decide(did):
        db = get_db()
        r = db.execute("SELECT * FROM kb_drafts WHERE id=?", (did,)).fetchone()
        if not r:
            flash("Draft not found.")
            return redirect(url_for("kb_review"))
        d = json.loads(r["draft_json"] or "{}")
        action = request.form.get("action")
        if action == "approve":
            if d.get("alias_of"):
                approve_entry(d["alias_of"], None, [d.get("term", ""), d.get("key", "")])
            else:
                idx = set(int(i) for i in request.form.getlist("prop") if i.isdigit())
                chosen = [p for i, p in enumerate(d.get("props") or []) if i in idx]
                approve_entry(d["key"], draft_to_entry(d, chosen), [d.get("term", "")])
                pidx = set(int(i) for i in request.form.getlist("pair") if i.isdigit())
                approve_pairs([p for i, p in enumerate(d.get("pairs") or []) if i in pidx])
            db.execute("UPDATE kb_drafts SET status='approved', decided=? WHERE id=?",
                       (datetime.now().isoformat(timespec="minutes"), did))
            flash("Approved: %s. Checks now include it." % d.get("display", ""))
        elif action == "reject":
            db.execute("UPDATE kb_drafts SET status='rejected', decided=? WHERE id=?",
                       (datetime.now().isoformat(timespec="minutes"), did))
            flash("Rejected: %s. It stays not checkable." % d.get("display", ""))
        db.commit()
        return redirect(url_for("kb_review"))

    @app.route("/kb/pairs", methods=["POST"])
    @login_required
    def kb_pairs_decide():
        db = get_db()
        ids = [int(i) for i in request.form.getlist("pid") if i.isdigit()]
        action = request.form.get("action")
        rows = [db.execute("SELECT * FROM kb_pairs WHERE id=? AND status='pending'", (i,)).fetchone()
                for i in ids]
        rows = [r for r in rows if r]
        if action == "approve" and rows:
            approve_pairs([dict(r) for r in rows])
        for r in rows:
            db.execute("UPDATE kb_pairs SET status=?, decided=? WHERE id=?",
                       ("approved" if action == "approve" else "dismissed",
                        datetime.now().isoformat(timespec="minutes"), r["id"]))
        db.commit()
        flash("%d interaction%s %s." % (len(rows), "" if len(rows) == 1 else "s",
                                          "approved" if action == "approve" else "dismissed"))
        return redirect(url_for("kb_review"))

    @app.route("/kb/alerts/read", methods=["POST"])
    @login_required
    def kb_alerts_read():
        get_db().execute("UPDATE kb_alerts SET status='read' WHERE status='new'")
        get_db().commit()
        return redirect(url_for("kb_review"))

    @app.route("/kb/fetch", methods=["POST"])
    @login_required
    def kb_fetch():
        if os.environ.get("RXGUARD_KB_NOSPAWN") == "1":
            flash("Fetch requested (test mode: not started).")
            return redirect(url_for("kb_review"))
        import subprocess
        logf = open(os.path.join(BASE_DIR, "kb_sync.log"), "a")
        subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "kb_sync.py")], cwd=BASE_DIR,
                         stdout=logf, stderr=logf, start_new_session=True)
        flash("Fetching in the background. Refresh this page in a minute or two.")
        return redirect(url_for("kb_review"))

    @app.route("/api/feed/status")
    def api_feed_status():
        import hmac
        try:
            with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
                tok = fh.read().strip()
        except OSError:
            tok = ""
        got = request.headers.get("Authorization", "")
        got = got[7:].strip() if got.startswith("Bearer ") else ""
        if not tok or not got or not hmac.compare_digest(tok, got):
            return Response('{"ok": false}', status=401, mimetype="application/json")
        c = kb_counts()
        ast = astaken_summary() or {}
        c.update(ok=True, red=ast.get("red", 0), amber=ast.get("amber", 0),
                 not_checkable=ast.get("unknown", 0), url="https://rx.dr-manoj.in/kb")
        return Response(json.dumps(c), mimetype="application/json")

'''


def build_edits():
    E = []
    E.append(("version", 'APP_VERSION = "1.1.0"   # ' + PREV,
              'APP_VERSION = "1.2.0"   # ' + PREV + " " + MARKER))
    a = 'SYMPTOM_MAP = RULES_DOC["symptom_map"]\n'
    E.append(("overlay", a, a + OVERLAY))
    a = '''    mode TEXT, summary TEXT, changes TEXT, created TEXT);\n"""'''
    E.append(("schema", a, '''    mode TEXT, summary TEXT, changes TEXT, created TEXT);''' + SCHEMA_ADD))
    a = "# --------------------------------------------------------------------------\n# Templates\n"
    E.append(("engine", a, ENGINE + a))
    a = """ <a href="{{ url_for('reviews') }}" class="{{ 'on' if nav=='reviews' }}">Review queue</a>\n"""
    n = a + """ <a href="{{ url_for('kb_review') }}" class="{{ 'on' if nav=='kb_review' }}">Sources review</a>\n"""
    E.append(("nav", a, n))
    a = "        g.db_path = app.config[\"DB_PATH\"]\n"
    E.append(("reload hook", a, a + "        reload_overlay()\n"))
    a = '    @app.route("/healthz")\n'
    E.append(("routes", a, ROUTES + a))
    a = "import json\nimport os\nimport sqlite3\nimport secrets\n"
    E.append(("import sys", a, a + "import sys\n"))
    return E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("RxGuard sources review -> v1.2.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    here = os.path.dirname(os.path.abspath(args.file))
    for need in ("kb_sources.py", "kb_sync.py"):
        if not os.path.exists(os.path.join(here, need)):
            print("FATAL: " + need + " must be beside app.py first. Nothing written.")
            return 1
    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v1.1.0. Nothing written.")
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
    bak = args.file + ".bak-v120-" + stamp
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.2.0 -> v1.3.0  ::  "Your review" replaces the Sources review page

The page leads with what bears on the medicines you take now:
  * interactions, one card per pair (RED first), in plain words -- "both slow
    the heart rate", "X blocks CYP3A4, which clears Y" -- with the source
    wording one tap away;
  * each new medicine as plain chips: what bears on your current medicines
    (and with which), what is kept quietly for future checks;
  * reference-only lines (kidney/liver notes with no condition recorded,
    stopping notes, gaps) as a small footnote.
One button accepts everything recommended; each medicine can still be
accepted, narrowed or rejected. Kidney/liver notes that ask for no action
("no dose adjustment", "not expected to affect") are stored as reference and
no longer drive a dose-review finding. Nothing else changes.

Requires v1.2.0. Anchor-verified, idempotent, compile-checked, .bak before
write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V130_REVIEW"
PREV = "RXGUARD_V120_SOURCES"

HELPERS = '\n\nimport re\n\n# --------------------------------------------------------------------------\n# Your review -- RXGUARD_V130_REVIEW. Sorts every draft line into what bears\n# on the medicines you take now, what is kept quietly for later, and what is\n# reference only. Plain words first; the source wording one tap away.\n# --------------------------------------------------------------------------\n_ACTIONABLE = re.compile(r"reduc|adjust|avoid|not recommended|contraindicat|lower (starting )?dose|"\n                         r"caution|\\d+(\\.\\d+)?-fold|by \\d+ ?%|accumulat", re.I)\n_REASSURING = re.compile(r"no dose adjustment|not necessary|not expected|no clinically (relevant|significant)|"\n                         r"may be used|not affect|unchanged|no (meaningful|significant) (effect|change)", re.I)\n\n\ndef organ_note_actionable(sentence):\n    """A renal/hepatic sentence only drives a check when it asks for action."""\n    s = sentence or ""\n    return bool(_ACTIONABLE.search(s)) and not _REASSURING.search(s)\n\n\nPLAIN_PROP = {\n    "hr": "slows the heart rate", "bp": "lowers blood pressure",\n    "burden.sedation": "causes drowsiness", "burden.serotonergic": "adds to serotonin effects",\n    "burden.anticholinergic": "anticholinergic (dry mouth, constipation, confusion)",\n    "burden.bleeding": "raises bleeding risk", "burden.nephrotoxic": "can strain the kidneys",\n    "burden.seizure": "can lower the seizure threshold", "burden.constipating": "constipates",\n    "qt": "can prolong the QT interval",\n}\nENGINE_FIELDS = ("qt", "hr", "bp")\n\n\ndef plain_prop(p):\n    f, v = p["field"], p["value"]\n    if f.startswith("cyp."):\n        _c, role, enz = (f.split(".", 2) + ["", ""])[:3]\n        if role == "substrate":\n            return "broken down by %s%s" % (enz, " (mainly)" if v == "major" else "")\n        if role == "inhibitor":\n            return "blocks %s (%s) - raises levels of drugs it clears" % (enz, v)\n        if role == "inducer":\n            return "speeds up %s (%s) - lowers levels of drugs it clears" % (enz, v)\n    if f == "hr" and v != "decrease":\n        return "raises the heart rate"\n    if f == "bp" and v != "decrease":\n        return "raises blood pressure"\n    return PLAIN_PROP.get(f, "")\n\n\ndef _profile(entry):\n    """(hr, bp, burden dict, cyp dict, qt) from a knowledge entry."""\n    e = entry or {}\n    return (e.get("hr") or "none", e.get("bp") or "none", e.get("burden") or {},\n            e.get("cyp") or {}, e.get("qt") or "none")\n\n\ndef pair_reasons(ka, ea, kb, eb):\n    """Plain reasons two medicines interact, from their properties."""\n    ha, ba, bua, ca, qa = _profile(ea)\n    hb, bb, bub, cb, qb = _profile(eb)\n    na, nb = display_name(ka), display_name(kb)\n    out = []\n    if ha == "decrease" and hb == "decrease":\n        out.append("Both slow the heart rate - together more bradycardia and slowed conduction.")\n    if ba in ("decrease", "both") and bb in ("decrease", "both"):\n        out.append("Both lower blood pressure - the fall adds up (dizziness on standing).")\n    for bk, txt in (("sedation", "Drowsiness adds up."), ("serotonergic", "Serotonin effects add up."),\n                    ("bleeding", "Bleeding risk adds up."), ("nephrotoxic", "Kidney strain adds up."),\n                    ("anticholinergic", "Anticholinergic load adds up."), ("constipating", "Constipation adds up.")):\n        if (bua.get(bk) or 0) > 0 and (bub.get(bk) or 0) > 0:\n            out.append(txt)\n    for (x, ex, nx), (y, ey, ny) in (((ka, ca, na), (kb, cb, nb)), ((kb, cb, nb), (ka, ca, na))):\n        for enz in (ex.get("substrate") or {}):\n            lv = (ey.get("inhibitor") or {}).get(enz)\n            if lv:\n                out.append("%s blocks %s, which clears %s - %s levels can rise." % (ny, enz, nx, nx))\n            lv = (ey.get("inducer") or {}).get(enz)\n            if lv:\n                out.append("%s speeds up %s, which clears %s - %s levels can fall." % (ny, enz, nx, nx))\n    if qa not in ("none", "") and qb not in ("none", ""):\n        out.append("Both can prolong QT.")\n    return out\n\n\ndef _cap(s):\n    return (s[:1].upper() + s[1:]) if s else s\n\n\ndef _current_keys():\n    keys = set(m["drug_key"] for m in active_meds())\n    data, _err = gutlog_stack(30)\n    if data:\n        for r in (data.get("regimen") or []) + (data.get("taken") or []):\n            for k in _split_molecules(r.get("molecule")):\n                keys.add(k)\n    return keys\n\n\ndef recommended_props(d):\n    """Everything the checks can use is recommended; organ notes that ask\n    for no action and withdrawal wording are kept as reference, not scored."""\n    return list(d.get("props") or [])\n\n\ndef kb_view():\n    db = get_db()\n    conds = active_conditions()\n    egfr_low = False\n    try:\n        egfr_low = float(profile_value("egfr", "")) < 60\n    except (TypeError, ValueError):\n        egfr_low = False\n    current = _current_keys()\n    drafts, entries = [], {}\n    for r in db.execute("SELECT * FROM kb_drafts WHERE status IN (\'pending\',\'source_changed\') "\n                        "ORDER BY id").fetchall():\n        d = json.loads(r["draft_json"] or "{}")\n        drafts.append((r, d))\n        if not d.get("alias_of"):\n            entries[d["key"]] = draft_to_entry(d, d.get("props") or [])\n            current.add(d["key"])\n            for p in d.get("pairs") or []:        # pairs were built against what GutLog shows current\n                current.add(p["b"])\n\n    def entry(k):\n        return entries.get(k) or get_drug(k) or {}\n\n    # ---- interactions, merged per pair, RED first\n    merged = {}\n\n    def add_pair(a, b, flag, quote, source, origin):\n        k = tuple(sorted((a, b)))\n        m = merged.setdefault(k, {"a": k[0], "b": k[1], "flag": flag, "sources": [], "origin": origin})\n        if FLAG_ORDER.get(flag, 9) < FLAG_ORDER.get(m["flag"], 9):\n            m["flag"] = flag\n        m["sources"].append({"source": source, "quote": quote})\n        if origin == "known":\n            m["origin"] = "known"\n\n    for r, d in drafts:\n        for p in d.get("pairs") or []:\n            add_pair(p["a"], p["b"], p["flag"], p["quote"], p["source"], "draft")\n    for p in db.execute("SELECT * FROM kb_pairs WHERE status=\'pending\'").fetchall():\n        add_pair(p["a"], p["b"], p["flag"], p["quote"], p["source"], "known")\n    matters = []\n    for m in merged.values():\n        m["title"] = "%s + %s" % (_cap(display_name(m["a"])), _cap(display_name(m["b"])))\n        m["why"] = pair_reasons(m["a"], entry(m["a"]), m["b"], entry(m["b"])) or \\\n            ["The sources rate this pair but do not state the mechanism."]\n        m["rated"] = " · ".join(sorted(set(\n            (s["quote"].replace("DDInter 2.0 risk level: ", "DDInter: ") if "DDInter" in s["source"]\n             else "named in the " + s["source"].split(" - ")[0]) for s in m["sources"])))\n        matters.append(m)\n    matters.sort(key=lambda m: (FLAG_ORDER.get(m["flag"], 9), m["title"]))\n\n    # ---- medicines: what bears on you / kept for later / reference\n    meds = []\n    for r, d in drafts:\n        if d.get("alias_of"):\n            meds.append({"id": r["id"], "status": r["status"], "alias": True, "d": d,\n                         "name": d.get("display", ""), "now": [], "later": [], "ref": [], "gaps": []})\n            continue\n        k = d["key"]\n        others = [(o, entry(o)) for o in current if o != k]\n        now, later, ref = [], [], []\n        for i, p in enumerate(d.get("props") or []):\n            f = p["field"]\n            item = {"i": i, "p": p, "text": plain_prop(p)}\n            if f in ("class", "atc"):\n                continue\n            if f in ("renal", "hepatic", "withdrawal_note"):\n                bears = (f == "renal" and (egfr_low or "renal_impairment" in conds)) or \\\n                        (f == "hepatic" and "hepatic_impairment" in conds)\n                item["text"] = {"renal": "Kidney note", "hepatic": "Liver note",\n                                "withdrawal_note": "Stopping note"}[f]\n                (now if bears and organ_note_actionable(p["value"]) else ref).append(item)\n                continue\n            partner = []\n            for o, eo in others:\n                ho, bo, buo, co, qo = _profile(eo)\n                if f == "hr" and ho == p["value"] or f == "bp" and bo in (p["value"], "both"):\n                    partner.append(o)\n                elif f.startswith("burden.") and (buo.get(f.split(".", 1)[1]) or 0) > 0:\n                    partner.append(o)\n                elif f == "qt" and qo not in ("none", ""):\n                    partner.append(o)\n                elif f.startswith("cyp."):\n                    role, enz = f.split(".", 2)[1:]\n                    want = ("inhibitor", "inducer") if role == "substrate" else ("substrate",)\n                    if any(enz in (co.get(w) or {}) for w in want):\n                        partner.append(o)\n            if partner:\n                item["with"] = ", ".join(sorted(_cap(display_name(o)) for o in partner))\n                now.append(item)\n            else:\n                later.append(item)\n        cls = [p["value"] for p in d.get("props") or [] if p["field"] == "class"]\n        meds.append({"id": r["id"], "status": r["status"], "alias": False, "d": d,\n                     "name": _cap(d.get("display", "")), "cls": cls[0] if cls else "",\n                     "now": now, "later": later, "ref": ref, "gaps": d.get("gaps") or []})\n    return {"matters": matters, "meds": meds,\n            "pending_pairs": db.execute("SELECT COUNT(*) FROM kb_pairs WHERE status=\'pending\'").fetchone()[0]}\n\n\ndef approve_draft_row(db, r, prop_idx=None, pair_idx=None):\n    """Approve one draft row (all recommended lines unless indices given)."""\n    d = json.loads(r["draft_json"] or "{}")\n    if d.get("alias_of"):\n        approve_entry(d["alias_of"], None, [d.get("term", ""), d.get("key", "")])\n    else:\n        props = d.get("props") or []\n        chosen = recommended_props(d) if prop_idx is None else \\\n            [p for i, p in enumerate(props) if i in prop_idx]\n        approve_entry(d["key"], draft_to_entry(d, chosen), [d.get("term", "")])\n        pairs = d.get("pairs") or []\n        approve_pairs(pairs if pair_idx is None else [p for i, p in enumerate(pairs) if i in pair_idx])\n    db.execute("UPDATE kb_drafts SET status=\'approved\', decided=? WHERE id=?",\n               (datetime.now().isoformat(timespec="minutes"), r["id"]))\n    return d\n'
ROUTES = '    # ------------------------------------------------ your review (v1.3.0)\n    @app.route("/kb")\n    @login_required\n    def kb_review():\n        db = get_db()\n        v = kb_view()\n        alerts = db.execute("SELECT * FROM kb_alerts WHERE status=\'new\' ORDER BY id").fetchall()\n        row = db.execute("SELECT value FROM kb_meta WHERE key=\'sources\'").fetchone()\n        src = json.loads(row["value"]) if row else {}\n        done = db.execute("SELECT status, COUNT(*) n FROM kb_drafts GROUP BY status").fetchall()\n        body = """\n        <style>\n        .rv-lead{font-size:15px;margin:4px 0 12px;line-height:1.5}\n        .rv-find{border:1px solid var(--rule);border-left:5px solid var(--amber);background:var(--panel);\n          padding:10px 14px;margin:10px 0}\n        .rv-find.RED{border-left-color:var(--red)}\n        .rv-find h3{display:inline;margin:0 0 0 6px;font-size:15px}\n        .rv-find ul{margin:6px 0 0 18px;padding:0}\n        .rv-src{font-size:12px;color:var(--muted);margin-top:6px}\n        .rv-chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}\n        .rv-chip{font-size:13px;padding:3px 9px;border:1px solid var(--rule);background:var(--paper)}\n        .rv-chip.now{background:var(--amber-bg);color:var(--amber);border-color:transparent}\n        .rv-foot{font-size:12px;color:var(--muted);margin:6px 0 0}\n        details summary{cursor:pointer;color:var(--muted);font-size:13px;margin-top:6px}\n        details table{font-size:13px}\n        </style>\n        <h1>Your review</h1>\n        {% if v.meds or v.matters %}\n        <p class="rv-lead">The free sources found <b>{{ v.matters|length }}</b> interaction{{ \'\' if v.matters|length == 1 else \'s\' }}\n        with the medicines you take now, and <b>{{ v.meds|length }}</b> medicine{{ \'\' if v.meds|length == 1 else \'s\' }}\n        RxGuard can start checking. <b>Accept all recommended</b> adds them to every check; you can\n        still accept or reject one by one below.</p>\n        <form method="post" action="{{ url_for(\'kb_accept_all\') }}"><button>Accept all recommended</button></form>\n\n        <h2>What matters for you</h2>\n        {% for m in v.matters %}\n        <div class="rv-find {{ m.flag }}"><span class="flag {{ m.flag }}">{{ m.flag }}</span><h3>{{ m.title }}</h3>\n          <ul>{% for w in m.why %}<li>{{ w }}</li>{% endfor %}</ul>\n          <div class="rv-src">{{ m.rated }}</div>\n          <details><summary>Source wording</summary>{% for s in m.sources %}\n            <p class="rv-foot">&ldquo;{{ s.quote }}&rdquo; &mdash; {{ s.source }}</p>{% endfor %}</details>\n        </div>\n        {% else %}<p class="muted">The sources found no interaction with your current medicines.</p>{% endfor %}\n\n        <h2>Medicines RxGuard will learn</h2>\n        {% for x in v.meds %}{% set d = x.d %}\n        <div class="card"><form method="post" action="{{ url_for(\'kb_decide\', did=x.id) }}">\n          <strong>{{ x.name }}</strong>{% if d.strength %} <span class="dose">{{ d.strength }}</span>{% endif %}\n          {% if x.cls %}<span class="muted"> &middot; {{ x.cls }}</span>{% endif %}\n          {% if x.status == \'source_changed\' %}<p class="rv-foot"><b>Its FDA label changed since you accepted it - check again.</b></p>{% endif %}\n          {% if x.alias %}\n            <p class="rv-foot">Same medicine as <b>{{ d.alias_of.replace(\'_\',\' \') }}</b>, which RxGuard already checks. Accept to link the name.</p>\n          {% else %}\n            {% if x.now %}<p class="rv-foot">Bears on what you take now:</p>\n            <div class="rv-chips">{% for it in x.now %}<span class="rv-chip now">{{ it.text }}{% if it.with %} &mdash; with {{ it.with }}{% endif %}</span>{% endfor %}</div>{% endif %}\n            {% if x.later %}<p class="rv-foot">Kept for future checks: {{ x.later|map(attribute=\'text\')|join(\' · \') }}</p>{% endif %}\n            {% if not x.now and not x.later %}<p class="rv-foot">The sources give nothing RxGuard can check for this medicine.</p>{% endif %}\n            <details><summary>Choose items / see source wording</summary>\n              <table>{% for p in d.props %}<tr><td><input type="checkbox" name="prop" value="{{ loop.index0 }}" checked></td>\n              <td>{{ p.field }}</td><td class="muted">&ldquo;{{ p.quote }}&rdquo;<br><span class="cat">{{ p.source }}</span></td></tr>{% endfor %}\n              {% for p in d.pairs %}<tr><td><input type="checkbox" name="pair" value="{{ loop.index0 }}" checked></td>\n              <td><span class="flag {{ p.flag }}">{{ p.flag }}</span> + {{ p.b.replace(\'_\',\' \') }}</td>\n              <td class="muted">&ldquo;{{ p.quote }}&rdquo;<br><span class="cat">{{ p.source }}</span></td></tr>{% endfor %}</table>\n            </details>\n          {% endif %}\n          <p style="margin:10px 0 0"><button name="action" value="approve">Accept</button>\n          <button name="action" value="reject" class="ghost">Reject</button></p>\n        </form>\n        {% if x.ref or x.gaps %}<p class="rv-foot">Reference only (not scored):\n          {% for it in x.ref %}{{ it.text }}{% if not loop.last %}, {% endif %}{% endfor %}{% if x.ref and x.gaps %}. {% endif %}\n          {% if x.gaps %}Not settled by the sources: {{ x.gaps|join(\'; \') }}.{% endif %}</p>\n          {% if x.ref %}<details><summary>Reference wording</summary>{% for it in x.ref %}\n            <p class="rv-foot"><b>{{ it.text }}:</b> &ldquo;{{ it.p.quote }}&rdquo; &mdash; {{ it.p.source }}</p>{% endfor %}</details>{% endif %}\n        {% endif %}\n        </div>\n        {% endfor %}\n        {% else %}\n        <p class="rv-lead">Nothing waiting. RxGuard now checks every medicine it has been given.\n        <a href="{{ url_for(\'astaken\') }}">See your medicines as taken &rarr;</a></p>\n        {% endif %}\n\n        <details class="rv-foot" style="margin-top:22px"><summary>Sources and alerts{% if alerts %} &middot; <b>{{ alerts|length }} new safety alert{{ \'\' if alerts|length == 1 else \'s\' }}</b>{% endif %}</summary>\n        {% if src %}<p class="rv-foot">Last run {{ src.run }}{% if src.stage and src.stage != \'done\' %} (in progress: {{ src.stage }}){% endif %} &middot;\n        GutLog {{ src.gutlog }} &middot;\n        FDA CYP table {% if src.fda_cyp and src.fda_cyp.ok %}{{ src.fda_cyp.rows }} rows{% else %}{{ (src.fda_cyp or {}).get(\'err\',\'-\') }}{% endif %} &middot;\n        DDInter {{ (src.ddinter or {}).get(\'pairs\',\'-\') }} pairs{% if (src.ddinter or {}).get(\'err\') %} ({{ src.ddinter.err }}){% endif %} &middot;\n        PvPI {{ src.pvpi }}</p>{% else %}<p class="rv-foot">No sync has run yet.</p>{% endif %}\n        <form method="post" action="{{ url_for(\'kb_fetch\') }}"><button class="ghost">Fetch now</button></form>\n        {% if alerts %}<form method="post" action="{{ url_for(\'kb_alerts_read\') }}"><ul>\n        {% for a in alerts %}<li><a href="{{ a[\'url\'] }}" target="_blank" rel="noopener">{{ a[\'title\'] }}</a></li>{% endfor %}\n        </ul><button class="ghost">Mark read</button></form>{% endif %}\n        <p class="rv-foot">Decided so far: {% for r in done %}{{ r[\'status\'] }} {{ r[\'n\'] }}{% if not loop.last %} &middot; {% endif %}{% endfor %}.\n        Sources: NLM RxNorm/RxClass, openFDA labels, FDA CYP table, DDInter 2.0 (CC BY-NC-SA 4.0, personal non-commercial use), PvPI.</p>\n        </details>\n        """\n        return page(body, nav="kb_review", title="Your review", v=v, alerts=alerts, src=src, done=done)\n\n    @app.route("/kb/accept_all", methods=["POST"])\n    @login_required\n    def kb_accept_all():\n        db = get_db()\n        n = 0\n        for r in db.execute("SELECT * FROM kb_drafts WHERE status IN (\'pending\',\'source_changed\') "\n                            "ORDER BY id").fetchall():\n            approve_draft_row(db, r)\n            n += 1\n        rows = [dict(r) for r in db.execute("SELECT * FROM kb_pairs WHERE status=\'pending\'").fetchall()]\n        if rows:\n            approve_pairs(rows)\n            for r in rows:\n                db.execute("UPDATE kb_pairs SET status=\'approved\', decided=? WHERE id=?",\n                           (datetime.now().isoformat(timespec="minutes"), r["id"]))\n        db.commit()\n        flash("Accepted %d medicine%s and %d interaction%s. Every check now includes them." % (\n            n, "" if n == 1 else "s", len(rows), "" if len(rows) == 1 else "s"))\n        return redirect(url_for("kb_review"))\n\n    @app.route("/kb/draft/<int:did>", methods=["POST"])\n    @login_required\n    def kb_decide(did):\n        db = get_db()\n        r = db.execute("SELECT * FROM kb_drafts WHERE id=?", (did,)).fetchone()\n        if not r:\n            flash("Draft not found.")\n            return redirect(url_for("kb_review"))\n        action = request.form.get("action")\n        if action == "approve":\n            idx = set(int(i) for i in request.form.getlist("prop") if i.isdigit())\n            pidx = set(int(i) for i in request.form.getlist("pair") if i.isdigit())\n            d = approve_draft_row(db, r, idx, pidx)\n            flash("Accepted: %s. Checks now include it." % d.get("display", ""))\n        elif action == "reject":\n            d = json.loads(r["draft_json"] or "{}")\n            db.execute("UPDATE kb_drafts SET status=\'rejected\', decided=? WHERE id=?",\n                       (datetime.now().isoformat(timespec="minutes"), did))\n            flash("Rejected: %s. It stays not checkable." % d.get("display", ""))\n        db.commit()\n        return redirect(url_for("kb_review"))\n\n'

A_ORGAN = ('        if f in ("class", "atc", "qt", "hr", "bp", "renal", "hepatic", "withdrawal_note"):\n'
           '            e[f] = v\n')
N_ORGAN = ('        if f in ("renal", "hepatic"):\n'
           '            if organ_note_actionable(v):\n'
           '                e[f] = v\n'
           '            else:\n'
           '                e.setdefault("reference", {})[f] = v\n'
           '        elif f in ("class", "atc", "qt", "hr", "bp", "withdrawal_note"):\n'
           '            e[f] = v\n')
A_HELP = "            \"alerts\": q(\"SELECT COUNT(*) FROM kb_alerts WHERE status='new'\")}\n"
BLOCK_START = "    # ------------------------------------------------ sources review (v1.2.0)\n"
BLOCK_END = '    @app.route("/kb/pairs", methods=["POST"])\n'


def build():
    return [
        ("version", 'APP_VERSION = "1.2.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES',
         'APP_VERSION = "1.3.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES ' + MARKER),
        ("organ notes", A_ORGAN, N_ORGAN),
        ("helpers", A_HELP, A_HELP + HELPERS),
        ("nav", "Sources review</a>", "Your review</a>"),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("RxGuard Your review -> v1.3.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = open(args.file, encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: app.py is not at v1.2.0. Apply that first.")
        return 1
    E = build()
    bad = [l for l, a, n in E if src.count(a) != 1]
    start, end = src.find(BLOCK_START), src.find(BLOCK_END)
    if src.count(BLOCK_START) != 1 or src.count(BLOCK_END) != 1 or end <= start:
        bad.append("sources review block")
    print("anchors: %d/%d matched" % (len(E) + 1 - len(bad), len(E) + 1))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + "\nRefusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0
    out = src[:start] + ROUTES + src[end:]
    for l, a, n in E:
        out = out.replace(a, n, 1)
    tmpd = tempfile.mkdtemp()
    t = os.path.join(tmpd, "app.py")
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(out)
    try:
        py_compile.compile(t, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v130-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    with open(args.file, "w", encoding="utf-8") as fh:
        fh.write(out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits + page" % len(E))
    print("-" * 60)
    print("Next:  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

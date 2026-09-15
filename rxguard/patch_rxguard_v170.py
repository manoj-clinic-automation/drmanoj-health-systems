#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.6.0 -> v1.7.0  ::  /astaken tells the truth about what was taken

THE DEFECT
----------
/astaken led with two REDs and ten AMBERs, and GutLog's home banner mirrored
the count. Both REDs were cumulative burdens whose largest contributors had
ZERO logged doses inside the window: as-needed medicines that had simply not
been needed. Counting only what GutLog showed was taken, each of the three
worst burdens fell by between a third and the whole of its total, and neither
RED survived.

The engine already knew. It printed a POSSIBLY STALE paragraph under eight of
the thirteen findings saying exactly this -- the alarming total first and the
correction last, at the foot of a card nobody opens twice.

A daily red badge for a burden that does not exist is how a real red badge
stops being read. This is a correctness fix.

(No molecule is named anywhere in this file or in the code it writes. The
worked figures are in the brief, which stays off this repository -- CLAUDE.md
rule 5d. The test fixture uses molecules chosen for their thresholds, none of
them on the owner's record.)

WHAT CHANGES (all of it scoped to /astaken; Quick check and Full analysis are
untouched, because there the question really is "what if this were added")

  1. TWO TOTALS PER BURDEN. as-taken counts only molecules GutLog logged a
     dose of inside the window or schedules in its own regimen; if-all-taken
     counts the whole list. The chip, the page headline and GutLog's banner
     all come from as-taken. The if-all-taken figure stays inside the finding,
     labelled, and is counted nowhere. Drug/condition findings whose trigger
     drug has no dose, and any finding resting entirely on undosed molecules,
     get the same treatment.

  2. ONE THEORETICAL SECTION, ONE CAVEAT. Findings that exist only under
     if-all-taken move into a single collapsed section below the current ones,
     which names the undosed medicines once. The per-finding POSSIBLY STALE
     block is deleted; it was the same sentence, eight times, in the wrong
     place.

  3. THE FINDING CARD COLLAPSES. Severity chip, title and CONSEQUENCE by
     default; mechanism, why-it-applies, monitoring, action and source behind
     a tap. A finding carrying an ACTION says so in the collapsed state, so an
     actionable one is never hidden.

  4. TWO WORDING BUGS. The cumulative-burden MECHANISM said "the current list
     plus the proposed change" on a page that has no proposed change -- the
     as-taken burden text is now its own. The absence-of-a-flag caveat is
     correct and kept, moved to the page foot.

The `kind` column is deliberately NOT consulted when deciding what counts as
taken: it is typed by hand, and one wrong "chronic" there would silently
restore the very count this exists to fix. GutLog's own dose log and regimen
decide.

Requires v1.6.0 (RXGUARD_V160_KEYCHECK). Anchor-verified, idempotent,
compile-checked, .bak before write, self-restoring, --reverse for the
negative control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V170_HONEST"
PREV = "RXGUARD_V160_KEYCHECK"

# --------------------------------------------------------------------------
# 1. the new engine helpers, inserted before stack_findings
# --------------------------------------------------------------------------
PY_HELPERS = '''# --------------------------------------------------------------------------
# As taken, honestly -- RXGUARD_V170_HONEST
# A cumulative burden has two totals and they are not the same number. Until
# v1.7.0 only one was computed -- every molecule on the list, taken or not --
# so the page led with a RED built out of medicines that had not been taken at
# all, and said so only in a POSSIBLY STALE paragraph at the foot of eight
# separate findings. The alarming number first, the correction last.
#
# A daily red badge for a burden that does not exist is how a real red badge
# stops being read. So the chip, the page headline and GutLog's banner all
# come from the as-taken total; the if-all-taken figure is kept, labelled,
# inside the finding, and counted nowhere.
# --------------------------------------------------------------------------
def astaken_dosed(items):
    """The molecules that count as currently taken.

    A dose logged inside the window, or a place in GutLog's own regimen --
    GutLog scheduling it is GutLog saying it is taken, which is what keeps a
    daily drug from being called untaken on a day it has not been logged yet.

    The RxGuard `kind` column is deliberately not consulted. It is typed by
    hand, and one wrong "chronic" there would silently restore the inflated
    count this function exists to remove.
    """
    return set(k for k, it in items.items()
               if it.get("doses") or it.get("scheduled"))


def _burden_flag(total, th):
    if total >= th["red"]:
        return "RED"
    if total >= th["amber"]:
        return "AMBER"
    return ""


def _contrib_text(pairs):
    return ", ".join("%s (%d)" % (n, v) for n, v in sorted(pairs, key=lambda x: -x[1]))


def astaken_burden_findings(now_totals, now_contrib, all_totals, all_contrib,
                            conditions):
    """One finding per burden, carrying both totals.

    The flag is the as-taken one. A burden that reaches a threshold only once
    the untaken medicines are added back is marked theoretical -- moved out of
    the counted list, never dropped.
    """
    out = []
    for bk in BURDEN_KEYS:
        th = BURDEN_THRESHOLDS.get(bk)
        if not th:
            continue
        now = now_totals.get(bk, 0)
        alltot = all_totals.get(bk, 0)
        flag = _burden_flag(now, th)
        theoretical = False
        if not flag:
            flag = _burden_flag(alltot, th)
            if not flag:
                continue
            theoretical = True
        here = now_contrib.get(bk, [])
        there = all_contrib.get(bk, [])
        extra = [p for p in there if p not in here]
        if theoretical:
            mech = ("Additive contribution if every medicine on the list were taken. "
                    "Contributors: %s." % (_contrib_text(there) or "none"))
            if_all = ""
        else:
            mech = ("Additive contribution across what GutLog shows was taken. "
                    "Contributors: %s." % (_contrib_text(here) or "none"))
            if_all = ""
            if extra:
                if_all = ("%d, adding %s. %s on your list with no dose logged in this "
                          "window, so %s counted in the total above or in any figure "
                          "on this page."
                          % (alltot, _contrib_text(extra),
                             "It is" if len(extra) == 1 else "They are",
                             "it is not" if len(extra) == 1 else "they are not"))
        personal = ""
        if bk == "constipating" and "constipation" in conditions:
            personal = ("Constipation is a recorded personal constraint, so this burden "
                        "acts on an already-limited reserve.")
        if bk == "bleeding" and "peptic_ulcer" in conditions:
            personal = "Recorded peptic ulcer or GI bleeding history."
        if bk == "nephrotoxic" and "renal_impairment" in conditions:
            personal = "Recorded renal impairment."
        f = finding(
            flag, "Cumulative burden",
            "%s burden: total %d" % (bk.capitalize(), alltot if theoretical else now),
            mechanism=mech, consequence=th["consequence"], personal=personal,
            monitoring=th.get("monitoring", ""),
            source="Additive burden scoring, rules base %s." % RULES_DOC["_meta"]["version"],
            reviewed=RULES_DOC["_meta"]["reviewed"])
        f["if_all"] = if_all
        f["theoretical"] = theoretical
        f["burden_key"] = bk
        out.append(f)
    return out


'''

PY_HELPERS_ANCHOR = 'def stack_findings(keys, conditions, unlisted):\n'

# --------------------------------------------------------------------------
# 2. stack_findings, replaced whole
# --------------------------------------------------------------------------
STACK_OLD = '''def stack_findings(keys, conditions, unlisted):
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
'''

STACK_NEW = '''def stack_findings(keys, conditions, unlisted, dosed=None):
    """The engine across a whole list at once, each pair judged once.

    RXGUARD_V170_HONEST -- `dosed` is the set of molecules GutLog shows were
    actually taken inside the window. Pass it and every cumulative total is
    computed twice, and any finding resting entirely on molecules outside it
    is marked theoretical: still reported, in its own section, counted
    nowhere. Pass None (Quick check, Full analysis) and nothing changes --
    there the question really is what happens if this is added.
    """
    known = [k for k in keys if get_drug(k)]
    totals, contributors = compute_burdens(known)
    now_keys = known if dosed is None else [k for k in known if k in dosed]
    now_totals, now_contrib = compute_burdens(now_keys)
    rule_pairs = dict((r["id"], (r["a"], r["b"])) for r in PAIRWISE)
    out = []

    def keep(f, involved):
        f.pop("pair", None)
        f["involves"] = sorted(involved)
        f["unlisted"] = bool(set(involved) & unlisted)
        if dosed is not None and "theoretical" not in f:
            # a finding every one of whose molecules is untaken cannot be a
            # current finding. One taken molecule in it and it stays current:
            # the stronger claim is the safer one.
            f["theoretical"] = bool(involved) and not (set(involved) & dosed)
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
        for f in condition_findings(a, conditions, now_totals):
            k = f.get("rule_id") or f["title"]
            if k in seen:
                seen[k]["involves"] = sorted(set(seen[k]["involves"]) | {a})
                seen[k]["unlisted"] = seen[k]["unlisted"] or a in unlisted
                if dosed is not None and a in dosed:
                    seen[k]["theoretical"] = False
                continue
            keep(f, (a,))
            seen[k] = out[-1]
    names = dict((display_name(k), k) for k in known)

    def _keys_of(pairs):
        return tuple(names[n] for n, _v in pairs if n in names)

    if dosed is None:
        for f in burden_findings(totals, contributors, conditions, None):
            bk = f["title"].split(" ")[0].lower()
            keep(f, _keys_of(contributors.get(bk, [])))
    else:
        for f in astaken_burden_findings(now_totals, now_contrib, totals,
                                         contributors, conditions):
            keep(f, _keys_of(contributors.get(f["burden_key"], [])))
    # The QT score is a cumulative total like any other, so it is computed on
    # what was taken; only if that raises nothing is the whole list scored,
    # and that answer is theoretical.
    for qk, theo in ([(known, False)] if dosed is None
                     else [(now_keys, False), (known, True)]):
        qf = qt_findings(qk, conditions, None)
        if not qf:
            continue
        for f in qf:
            f["theoretical"] = theo
            keep(f, tuple(k for k in qk if get_drug(k).get("qt", "none") != "none"))
        break
    return out
'''

# --------------------------------------------------------------------------
# 3. astaken_view
# --------------------------------------------------------------------------
VIEW_CALL_OLD = '''    conditions = active_conditions()
    findings = stack_findings(keys, conditions, set(not_listed))
'''
VIEW_CALL_NEW = '''    conditions = active_conditions()
    dosed = astaken_dosed(items)
    findings = stack_findings(keys, conditions, set(not_listed), dosed)
'''

VIEW_RET_OLD = '''    stale_n = mark_gut_stale(findings, data)
    red = sum(1 for f in findings if f["flag"] == "RED")
    amber = sum(1 for f in findings if f["flag"] == "AMBER")
    return {"rows": rows, "findings": findings, "not_listed": not_listed,
            "listed_not_taken": [display_name(k) for k in listed_not_taken],
            "unknown": unknown, "unmapped": unmapped, "red": red, "amber": amber,
            "rec": reconcile_view(data), "stale_findings": stale_n,
            "since": data.get("since", ""), "days": data.get("days", "")}
'''
VIEW_RET_NEW = '''    stale_n = mark_gut_stale(findings, data)
    # RXGUARD_V170_HONEST -- the split. Only the current list is counted, and
    # the count is what the headline and GutLog's banner both read.
    theoretical = [f for f in findings if f.get("theoretical")]
    current = [f for f in findings if not f.get("theoretical")]
    red = sum(1 for f in current if f["flag"] == "RED")
    amber = sum(1 for f in current if f["flag"] == "AMBER")
    not_taken = sorted(k for k in items if k not in dosed)
    if not_taken:
        _nm, _many = _name_list(not_taken)
        not_taken_text = ("%s %s on your list with no dose logged in the last %s days."
                          % (_nm, "are" if _many else "is", data.get("days", "")))
    else:
        not_taken_text = ""
    return {"rows": rows, "findings": current, "theoretical": theoretical,
            "not_listed": not_listed,
            "not_taken": [display_name(k) for k in not_taken],
            "not_taken_text": not_taken_text,
            "listed_not_taken": [display_name(k) for k in listed_not_taken],
            "unknown": unknown, "unmapped": unmapped, "red": red, "amber": amber,
            "rec": reconcile_view(data), "stale_findings": stale_n,
            "since": data.get("since", ""), "days": data.get("days", "")}
'''

SUMMARY_OLD = '''    v = astaken_view(data)
    return {"err": "", "red": v["red"], "amber": v["amber"],
            "not_listed": len(v["not_listed"]), "unknown": len(v["unknown"]) + len(v["unmapped"]),
            "reconcile": len(v["rec"]["stopped"]) + len(v["rec"]["missing"]),
            "stale_findings": v["stale_findings"]}
'''
SUMMARY_NEW = '''    v = astaken_view(data)
    # red/amber are the AS-TAKEN counts from v1.7.0 on. GutLog's home banner
    # reads them, so a burden nobody is carrying no longer paints it red.
    return {"err": "", "red": v["red"], "amber": v["amber"],
            "theoretical": len(v["theoretical"]),
            "not_listed": len(v["not_listed"]), "unknown": len(v["unknown"]) + len(v["unmapped"]),
            "reconcile": len(v["rec"]["stopped"]) + len(v["rec"]["missing"]),
            "stale_findings": v["stale_findings"]}
'''

# --------------------------------------------------------------------------
# 4. the collapsed finding card, /astaken only
# --------------------------------------------------------------------------
BLOCK_ANCHOR = '''    {% if f.gut_stale %}<dt>Possibly stale</dt>
      <dd><span class="stale">CHECK</span> {{ f.gut_stale }}</dd>{% endif %}
  </dl>
</div>
{% endfor %}
"""
'''

BLOCK_NEW = BLOCK_ANCHOR + '''

# RXGUARD_V170_HONEST -- the /astaken card, collapsed.
# Thirteen findings each printing mechanism, consequence, why-it-applies,
# monitoring, action, source and a review date is a wall, and a wall is not
# read. Severity, title and the consequence stand; everything else is one tap
# away. A finding carrying an ACTION says so before it is opened, so the one
# kind that must not be missed is never the one hidden.
#
# Used on /astaken only. Quick check and Full analysis answer "what if this
# were added", and there the whole of the reasoning is the answer.
ASTAKEN_FINDING_BLOCK = """
{% for f in findings %}
<details class="finding {{ f.flag }}">
  <summary>
    <span class="flag {{ f.flag }}">{{ f.flag }}</span>
    <span class="cat">{{ f.category }}{% if f.rule_id %} · {{ f.rule_id }}{% endif %}</span>
    {% if f.action %}<span class="needsact">action</span>{% endif %}
    <h3>{{ f.title }}</h3>
    {% if f.consequence %}<span class="cons">{{ f.consequence }}</span>{% endif %}
  </summary>
  <dl>
    {% if f.mechanism %}<dt>Mechanism</dt><dd>{{ f.mechanism }}</dd>{% endif %}
    {% if f.if_all %}<dt>If every medicine on the list were taken</dt>
      <dd>{{ f.if_all }}</dd>{% endif %}
    {% if f.personal %}<dt>Why it applies here</dt><dd>{{ f.personal }}</dd>{% endif %}
    {% if f.monitoring %}<dt>Monitoring</dt><dd>{{ f.monitoring }}</dd>{% endif %}
    {% if f.action %}<dt>Action</dt><dd>{{ f.action }}</dd>{% endif %}
    {% if f.source %}<dt>Source</dt><dd class="muted">{{ f.source }}
      {% if f.reviewed %} · reviewed {{ f.reviewed }}{% endif %}
      {% if f.stale %}<span class="stale">STALE</span>{% endif %}</dd>{% endif %}
  </dl>
</details>
{% endfor %}
"""
'''

CSS_ANCHOR = ".cat{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}\n"
CSS_NEW = '''details.finding{padding:0}
details.finding>summary{cursor:pointer;padding:12px 14px}
details.finding>summary h3{margin:6px 0 0}
details.finding>summary .cons{display:block;font-size:14.5px;margin:6px 0 0}
details.finding>dl{padding:0 14px 14px;margin:0}
.needsact{display:inline-block;font-size:14px;border:1px solid var(--ink);
 padding:0 6px;margin-left:6px}
details.theo{border:1px solid var(--rule);background:var(--panel);
 padding:14px 16px;margin-bottom:16px}
details.theo>summary{cursor:pointer;font-size:15px;font-weight:600}
details.theo>p{font-size:14px;margin:10px 0 12px}
'''

# --------------------------------------------------------------------------
# 5. the page
# --------------------------------------------------------------------------
CARD_OLD = '''        <div class="card"><strong>{{ v.rows|length }} molecules</strong> across GutLog and your list
        &middot; <span class="flag RED">{{ v.red }} RED</span> <span class="flag AMBER">{{ v.amber }} AMBER</span>
        &middot; {{ v.not_listed|length }} taken but not on your list
        &middot; {{ v.unknown|length + v.unmapped|length }} not checkable
        <p class="muted" style="margin:6px 0 0">Absence of a flag means nothing was found in this knowledge
        base, not that the combination is safe.</p></div>
'''
CARD_NEW = '''        <div class="card"><strong>{{ v.rows|length }} molecules</strong> across GutLog and your list
        &middot; <span class="flag RED">{{ v.red }} RED</span> <span class="flag AMBER">{{ v.amber }} AMBER</span>
        as taken
        &middot; {{ v.not_listed|length }} taken but not on your list
        &middot; {{ v.unknown|length + v.unmapped|length }} not checkable
        {% if v.theoretical %}<p class="muted" style="margin:6px 0 0">{{ v.theoretical|length }}
        further finding{{ '' if v.theoretical|length == 1 else 's' }} would apply only if the medicines
        you have not taken in this window were taken. They are listed at the foot of the page and are
        in neither figure above.</p>{% endif %}</div>
'''

FINDINGS_OLD = '''        <h2>Findings across the whole as-taken stack</h2>
        {% for f in v.findings %}{% if f.unlisted %}<p class="muted" style="margin:0 0 3px">
          <span class="chip">involves a medicine not on your list</span></p>{% endif %}
        {% set findings = [f] %}""" + FINDING_BLOCK + """{% else %}
        <p class="muted">Nothing raised from this knowledge base.</p>{% endfor %}
'''
FINDINGS_NEW = '''        <h2>Findings from what was actually taken</h2>
        {% for f in v.findings %}{% if f.unlisted %}<p class="muted" style="margin:0 0 3px">
          <span class="chip">involves a medicine not on your list</span></p>{% endif %}
        {% set findings = [f] %}""" + ASTAKEN_FINDING_BLOCK + """{% else %}
        <p class="muted">Nothing raised from this knowledge base.</p>{% endfor %}
        {% if v.theoretical %}
        <details class="theo">
        <summary>If you also take your as-needed medicines ({{ v.theoretical|length }})</summary>
        <p>{{ v.not_taken_text }} These findings would apply if you took them.</p>
        {% for f in v.theoretical %}{% set findings = [f] %}""" + ASTAKEN_FINDING_BLOCK + """{% endfor %}
        </details>
        {% endif %}
'''

STATUS_OLD = '''        c.update(ok=True, red=ast.get("red", 0), amber=ast.get("amber", 0),
                 not_checkable=ast.get("unknown", 0), url="https://rx.dr-manoj.in/kb")
'''
STATUS_NEW = '''        # RXGUARD_V170_HONEST -- red/amber are the as-taken counts, so GutLog's
        # banner stops painting itself over a burden nobody is carrying.
        c.update(ok=True, red=ast.get("red", 0), amber=ast.get("amber", 0),
                 theoretical=ast.get("theoretical", 0),
                 not_checkable=ast.get("unknown", 0), url="https://rx.dr-manoj.in/kb")
'''

FOOT_OLD = '''        <p class="muted">GutLog feed since {{ v.since }} &middot; knowledge base {{ kbv }}</p>
'''
FOOT_NEW = '''        <p class="muted">Absence of a flag means nothing was found in this knowledge base, not
        that the combination is safe. Every figure above counts only the medicines GutLog shows a
        dose of in this window.</p>
        <p class="muted">GutLog feed since {{ v.since }} &middot; knowledge base {{ kbv }}</p>
'''


def build_edits():
    E = []

    a = ('APP_VERSION = "1.6.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
         'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS RXGUARD_V150_RECONCILE '
         'RXGUARD_V160_KEYCHECK')
    E.append(("version", a,
              'APP_VERSION = "1.7.0"   # RXGUARD_V110_ASTAKEN RXGUARD_V120_SOURCES '
              'RXGUARD_V130_REVIEW RXGUARD_V140_CONDITIONS RXGUARD_V150_RECONCILE '
              'RXGUARD_V160_KEYCHECK ' + MARKER))

    E.append(("engine helpers", PY_HELPERS_ANCHOR, PY_HELPERS + PY_HELPERS_ANCHOR))
    E.append(("stack_findings", STACK_OLD, STACK_NEW))
    E.append(("astaken_view call", VIEW_CALL_OLD, VIEW_CALL_NEW))
    E.append(("astaken_view return", VIEW_RET_OLD, VIEW_RET_NEW))
    E.append(("astaken_summary", SUMMARY_OLD, SUMMARY_NEW))
    E.append(("astaken finding block", BLOCK_ANCHOR, BLOCK_NEW))
    E.append(("css", CSS_ANCHOR, CSS_NEW + CSS_ANCHOR))
    E.append(("summary card", CARD_OLD, CARD_NEW))
    E.append(("findings section", FINDINGS_OLD, FINDINGS_NEW))
    E.append(("page foot", FOOT_OLD, FOOT_NEW))
    E.append(("feed status", STATUS_OLD, STATUS_NEW))
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


def reverse(path, out_path):
    """Reconstruct v1.6.0 for tools/NEGATIVE_CONTROL.py."""
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    # Undone LAST edit first. Two of the forward edits overlap -- the helper
    # block is anchored on the `def stack_findings(...)` line that the very
    # next edit rewrites -- so undoing them in forward order looks for text
    # that the later edit has already changed. Checked step by step on the
    # evolving file rather than all at once up front, for the same reason.
    out, bad = src, []
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            bad.append("  " + label + ": new text found " + str(c) + " times, need 1")
            break
        out = out.replace(new, anchor, 1)
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
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
                    help="reconstruct v1.6.0 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("RxGuard /astaken: as-taken totals, one caveat, collapsed cards -> v1.7.0")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v1.6.0. Apply that first.")
        return 1
    if "def astaken_dosed" in src:
        print("FATAL: the as-taken split is already present. Nothing written.")
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
    bak = args.file + ".bak-v170-" + stamp
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
    print("Next:  python3 test_astaken_honest.py app.py")
    print("       python3 test_astaken.py /root/gutlog/app.py")
    print("       python3 smoke_test.py   then  systemctl restart rxguard")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

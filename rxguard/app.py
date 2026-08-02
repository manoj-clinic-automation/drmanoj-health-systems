#!/usr/bin/env python3
"""
RxGuard — personal medication interaction and safety review.

Single-user clinical decision support. Flask + SQLite, single file, no external
services required at runtime. Knowledge base lives in knowledge/*.json and is
versioned independently of this code.

Design notes:
  * There is no GREEN state. Absence of a flag means nothing was found in this
    knowledge base, not that a combination is safe.
  * The knowledge base is curated narrowly and deeply for one person's actual
    and plausible drug set, not broadly and shallowly for everyone.
  * Every finding carries its source and review date. Stale rules are marked.
"""

import json
import os
import sqlite3
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, g, redirect, render_template_string, request,
                   session, url_for, flash, Response)
from werkzeug.security import check_password_hash, generate_password_hash

APP_VERSION = "1.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
DEFAULT_DB = os.path.join(BASE_DIR, "rxguard.db")
STALE_DAYS = 365


# --------------------------------------------------------------------------
# Knowledge base
# --------------------------------------------------------------------------

def load_knowledge():
    with open(os.path.join(KNOWLEDGE_DIR, "drugs.json"), encoding="utf-8") as fh:
        drugs_doc = json.load(fh)
    with open(os.path.join(KNOWLEDGE_DIR, "rules.json"), encoding="utf-8") as fh:
        rules_doc = json.load(fh)
    return drugs_doc, rules_doc


DRUGS_DOC, RULES_DOC = load_knowledge()
DRUGS = DRUGS_DOC["drugs"]
SYNONYMS = DRUGS_DOC.get("synonyms", {})
PAIRWISE = RULES_DOC["pairwise"]
CONDITION_RULES = RULES_DOC["condition_rules"]
BURDEN_THRESHOLDS = RULES_DOC["burden_thresholds"]
QT_WEIGHTS = RULES_DOC["qt_weights"]
QT_THRESHOLDS = RULES_DOC["qt_thresholds"]
QT_MODIFIERS = RULES_DOC["qt_modifiers"]
WITHDRAWAL_RULES = RULES_DOC["withdrawal_rules"]
SYMPTOM_MAP = RULES_DOC["symptom_map"]

CONDITIONS = [
    ("constipation", "Constipation tendency"),
    ("ventricular_ectopy", "Previous ventricular ectopy"),
    ("hypertension", "Hypertension"),
    ("renal_impairment", "Renal impairment"),
    ("hepatic_impairment", "Hepatic impairment"),
    ("bradycardia", "Bradycardia"),
    ("hypokalaemia", "Hypokalaemia"),
    ("hypomagnesaemia", "Hypomagnesaemia"),
    ("qtc_prolonged", "Prolonged QTc"),
    ("structural_heart_disease", "Structural heart disease"),
    ("peptic_ulcer", "Peptic ulcer / GI bleed history"),
    ("diabetes", "Diabetes"),
    ("seizure_history", "Seizure history"),
]
CONDITION_LABELS = dict(CONDITIONS)

BURDEN_KEYS = ["anticholinergic", "serotonergic", "sedation", "constipating",
               "bleeding", "nephrotoxic", "seizure"]

FLAG_ORDER = {"RED": 0, "AMBER": 1, "UNKNOWN": 2}


def norm_key(name):
    """Normalise a free-text drug name to a knowledge-base key."""
    if not name:
        return ""
    k = name.strip().lower().replace(" ", "_").replace("-", "_")
    while "__" in k:
        k = k.replace("__", "_")
    if k in DRUGS:
        return k
    if k in SYNONYMS:
        return SYNONYMS[k]
    # tolerate trailing salt words
    for suffix in ("_hcl", "_hydrochloride", "_sodium", "_maleate", "_tartrate",
                   "_succinate", "_besylate", "_citrate", "_sulfate", "_sulphate"):
        if k.endswith(suffix):
            base = k[: -len(suffix)]
            if base in DRUGS:
                return base
            if base in SYNONYMS:
                return SYNONYMS[base]
    return k


def get_drug(key):
    return DRUGS.get(key)


def display_name(key):
    d = get_drug(key)
    if d:
        return key.replace("_", " ")
    return (key or "").replace("_", " ")


def is_stale(reviewed):
    try:
        d = datetime.strptime(reviewed, "%Y-%m-%d").date()
    except Exception:
        return True
    return (date.today() - d).days > STALE_DAYS


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS conditions (
    code TEXT PRIMARY KEY, active INTEGER DEFAULT 0, note TEXT);

CREATE TABLE IF NOT EXISTS constraints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL, created TEXT, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_key TEXT NOT NULL, raw_name TEXT,
    dose TEXT, frequency TEXT, route TEXT DEFAULT 'oral',
    indication TEXT, prescriber TEXT, specialty TEXT,
    kind TEXT DEFAULT 'chronic',
    status TEXT DEFAULT 'active',
    benefit TEXT DEFAULT 'unknown',
    start_date TEXT, last_change TEXT, stop_date TEXT,
    last_reviewed_by_prescriber TEXT,
    notes TEXT);

CREATE TABLE IF NOT EXISTS med_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    med_id INTEGER, drug_key TEXT, event_date TEXT,
    action TEXT, old_dose TEXT, new_dose TEXT,
    source TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS adverse_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_key TEXT, drug_raw TEXT, symptom TEXT,
    onset_date TEXT, dose_at_onset TEXT, latency_days INTEGER,
    dechallenge TEXT, dechallenge_resolved TEXT,
    rechallenge TEXT, rechallenge_recurred TEXT,
    confounders TEXT, severity TEXT, assessment TEXT, notes TEXT,
    created TEXT);

CREATE TABLE IF NOT EXISTS symptoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symptom TEXT, onset_date TEXT, notes TEXT,
    reviewed_changes TEXT, conclusion TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_date TEXT, kind TEXT, value TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, drug_key TEXT, dose TEXT, frequency TEXT,
    indication TEXT, action TEXT, flag TEXT,
    result_json TEXT, kb_version TEXT);

CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER, finding_title TEXT, flag TEXT,
    reason TEXT, created TEXT,
    due_2w TEXT, due_6w TEXT, status TEXT DEFAULT 'open',
    outcome TEXT);

CREATE TABLE IF NOT EXISTS consultations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consult_date TEXT, doctor TEXT, specialty TEXT,
    mode TEXT, summary TEXT, changes TEXT, created TEXT);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(g.db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    for code, _label in CONDITIONS:
        con.execute("INSERT OR IGNORE INTO conditions (code, active) VALUES (?, 0)", (code,))
    con.commit()
    con.close()


def setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES (?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()


def profile_value(key, default=""):
    row = get_db().execute("SELECT value FROM profile WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_profile(key, value):
    db = get_db()
    db.execute("INSERT INTO profile (key, value) VALUES (?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()


def active_conditions():
    rows = get_db().execute("SELECT code FROM conditions WHERE active=1").fetchall()
    return {r["code"] for r in rows}


def active_meds():
    return get_db().execute(
        "SELECT * FROM medications WHERE status IN ('active','tapering') "
        "ORDER BY kind, drug_key").fetchall()


# --------------------------------------------------------------------------
# Analysis engine
# --------------------------------------------------------------------------

def finding(flag, category, title, **kw):
    f = {"flag": flag, "category": category, "title": title,
         "mechanism": kw.get("mechanism", ""), "consequence": kw.get("consequence", ""),
         "personal": kw.get("personal", ""), "monitoring": kw.get("monitoring", ""),
         "action": kw.get("action", ""), "source": kw.get("source", ""),
         "reviewed": kw.get("reviewed", ""), "rule_id": kw.get("rule_id", "")}
    f["stale"] = is_stale(f["reviewed"]) if f["reviewed"] else False
    return f


def cyp_findings(proposed_key, other_keys):
    """Derive CYP/transporter interactions from drug properties."""
    out = []
    strength = {"strong": 3, "moderate": 2, "weak": 1}
    depth = {"major": 3, "moderate": 2, "minor": 1}
    p = get_drug(proposed_key)
    if not p:
        return out

    def pair(inh_key, sub_key):
        inh, sub = get_drug(inh_key), get_drug(sub_key)
        if not inh or not sub:
            return
        for enzyme, s in (inh.get("cyp", {}).get("inhibitor") or {}).items():
            d = (sub.get("cyp", {}).get("substrate") or {}).get(enzyme)
            if not d:
                continue
            score = strength.get(s, 1) * depth.get(d, 1)
            narrow = sub.get("narrow_ti") or sub.get("prodrug_2d6")
            if score >= 9:
                flag = "RED"
            elif score >= 4 or narrow:
                flag = "AMBER"
            else:
                continue
            if sub.get("prodrug_2d6") and enzyme == "CYP2D6":
                conseq = ("Formation of the active metabolite is reduced, so analgesia "
                          "is diminished or absent while the parent drug accumulates. "
                          "Escalating the dose in response worsens the parent-drug effects.")
            else:
                conseq = ("Exposure to %s rises on an unchanged dose, so dose-dependent "
                          "adverse effects may appear at an apparently modest prescribed dose."
                          % display_name(sub_key))
                if sub.get("narrow_ti"):
                    conseq += " This drug has a narrow therapeutic index, so the margin is small."
            fnd = finding(
                flag, "Pharmacokinetic",
                "%s (%s %s inhibitor) alters %s exposure"
                % (display_name(inh_key), s, enzyme, display_name(sub_key)),
                mechanism="%s inhibits %s. %s is a %s substrate of %s."
                          % (display_name(inh_key), enzyme, display_name(sub_key), d, enzyme),
                consequence=conseq,
                monitoring=sub.get("tdm", "") or "Watch for dose-dependent effects of %s."
                                                 % display_name(sub_key),
                source="Derived from drug property table (%s)." % DRUGS_DOC["_meta"]["version"],
                reviewed=inh.get("reviewed", ""))
            fnd["pair"] = frozenset((inh_key, sub_key))
            out.append(fnd)
        for enzyme, s in (inh.get("cyp", {}).get("inducer") or {}).items():
            d = (sub.get("cyp", {}).get("substrate") or {}).get(enzyme)
            if not d:
                continue
            out.append(finding(
                "AMBER", "Pharmacokinetic",
                "%s induces %s and lowers %s exposure"
                % (display_name(inh_key), enzyme, display_name(sub_key)),
                mechanism="Enzyme induction increases clearance of the substrate.",
                consequence="Loss of effect on an unchanged dose.",
                source="Derived from drug property table.",
                reviewed=inh.get("reviewed", "")))

    for other in other_keys:
        pair(proposed_key, other)
        pair(other, proposed_key)
    return out


def pairwise_findings(proposed_key, other_keys):
    out = []
    others = set(other_keys)
    for r in PAIRWISE:
        a, b = r["a"], r["b"]
        hit = (a == proposed_key and b in others) or (b == proposed_key and a in others)
        if not hit:
            continue
        out.append(finding(
            r["flag"], "Interaction", r["title"],
            mechanism=r.get("mechanism", ""), consequence=r.get("consequence", ""),
            personal=r.get("personal_relevance", ""), monitoring=r.get("monitoring", ""),
            action=r.get("action", "") or r.get("note", ""),
            source=r.get("source", ""), reviewed=r.get("reviewed", ""), rule_id=r["id"]))
    return out


def compute_burdens(keys):
    totals = {k: 0 for k in BURDEN_KEYS}
    contributors = {k: [] for k in BURDEN_KEYS}
    for key in keys:
        d = get_drug(key)
        if not d:
            continue
        for bk in BURDEN_KEYS:
            v = (d.get("burden") or {}).get(bk, 0)
            if v:
                totals[bk] += v
                contributors[bk].append((display_name(key), v))
    return totals, contributors


def burden_findings(totals, contributors, conditions, proposed_key=None):
    """Report burdens the proposed change actually contributes to.

    Baseline burden of the existing list belongs on the dashboard, not in the
    analysis of a new drug. Attributing it here produces false-positive noise
    and trains the user to dismiss the screen.
    """
    out = []
    pd = get_drug(proposed_key) if proposed_key else None
    for bk in BURDEN_KEYS:
        th = BURDEN_THRESHOLDS.get(bk)
        if not th:
            continue
        contribution = (pd.get("burden") or {}).get(bk, 0) if pd else 0
        if proposed_key and contribution <= 0:
            continue
        total = totals[bk]
        if total >= th["red"]:
            flag = "RED"
        elif total >= th["amber"]:
            flag = "AMBER"
        else:
            continue
        baseline = total - contribution
        crossed = baseline < th["amber"] <= total
        parts = ", ".join("%s (%d)" % (n, v) for n, v in
                          sorted(contributors[bk], key=lambda x: -x[1]))
        if crossed:
            parts += ". This change is what takes the total past the threshold"
            parts += " (%d to %d)" % (baseline, total)
        elif contribution:
            parts += ". This change adds %d (%d to %d)" % (contribution, baseline, total)
        personal = ""
        if bk == "constipating" and "constipation" in conditions:
            personal = ("Constipation is a recorded personal constraint, so this burden "
                        "acts on an already-limited reserve.")
        if bk == "bleeding" and "peptic_ulcer" in conditions:
            personal = "Recorded peptic ulcer or GI bleeding history."
        if bk == "nephrotoxic" and "renal_impairment" in conditions:
            personal = "Recorded renal impairment."
        out.append(finding(
            flag, "Cumulative burden",
            "%s burden: total %d" % (bk.capitalize(), total),
            mechanism="Additive contribution across the current list plus the proposed change. "
                      "Contributors: %s." % (parts or "none"),
            consequence=th["consequence"], personal=personal,
            monitoring=th.get("monitoring", ""),
            source="Additive burden scoring, rules base %s." % RULES_DOC["_meta"]["version"],
            reviewed=RULES_DOC["_meta"]["reviewed"]))
    return out


def qt_findings(keys, conditions, proposed_key=None):
    total, contributors = 0, []
    for key in keys:
        d = get_drug(key)
        if not d:
            continue
        w = QT_WEIGHTS.get(d.get("qt", "none"), 0)
        if w:
            total += w
            contributors.append((display_name(key), d.get("qt")))
    mods = []
    for cond, add in QT_MODIFIERS.items():
        code = "ventricular_ectopy" if cond == "known_ectopy" else cond
        if code in conditions:
            total += add
            mods.append(CONDITION_LABELS.get(code, code.replace("_", " ")))
    if proposed_key:
        pd = get_drug(proposed_key)
        if not pd or QT_WEIGHTS.get(pd.get("qt", "none"), 0) == 0:
            return []
    if total < QT_THRESHOLDS["amber"] or not contributors:
        return []
    flag = "RED" if total >= QT_THRESHOLDS["red"] else "AMBER"
    parts = ", ".join("%s (%s risk)" % (n, c) for n, c in contributors)
    return [finding(
        flag, "QT / conduction", "QT risk score %d" % total,
        mechanism="QT-affecting drugs: %s.%s" % (
            parts, (" Additional risk factors: %s." % ", ".join(mods)) if mods else ""),
        consequence="Additive QT prolongation with risk of torsades de pointes. "
                    "Risk factors stack; several modest contributors matter as much as one large one.",
        monitoring="ECG with measured QTc, plus potassium and magnesium, before and after starting.",
        source="Risk-factor stacking model, rules base %s." % RULES_DOC["_meta"]["version"],
        reviewed=RULES_DOC["_meta"]["reviewed"])]


def condition_findings(proposed_key, conditions, totals):
    out = []
    d = get_drug(proposed_key)
    if not d:
        return out
    for r in CONDITION_RULES:
        if r["condition"] not in conditions:
            continue
        t = r["trigger"]
        hit = False
        if "burden" in t:
            hit = totals.get(t["burden"], 0) >= t.get("min", 1)
        elif t.get("qt_any"):
            hit = d.get("qt", "none") != "none"
        elif "qt_level" in t:
            hit = d.get("qt") == t["qt_level"]
        elif "bp" in t:
            hit = d.get("bp") in (t["bp"], "both")
        elif "hr" in t:
            hit = d.get("hr") == t["hr"]
        elif "nephrotoxic" in t:
            hit = (d.get("burden") or {}).get("nephrotoxic", 0) >= t["nephrotoxic"]
        elif t.get("renal_critical"):
            hit = bool(d.get("renal_critical"))
        if not hit:
            continue
        out.append(finding(
            r["flag"], "Drug / condition", r["title"],
            mechanism="Recorded condition: %s." % CONDITION_LABELS.get(r["condition"], r["condition"]),
            consequence=r.get("consequence", ""),
            personal="This finding exists because of a stored personal condition.",
            monitoring=r.get("monitoring", ""), action=r.get("action", ""),
            source=r.get("source", ""), reviewed=r.get("reviewed", ""), rule_id=r["id"]))
    return out


def renal_findings(proposed_key, conditions):
    d = get_drug(proposed_key)
    if not d:
        return []
    out = []
    egfr = profile_value("egfr", "")
    low = False
    try:
        low = float(egfr) < 60
    except (TypeError, ValueError):
        low = False
    if (d.get("renal_critical") or d.get("renal")) and (low or "renal_impairment" in conditions):
        out.append(finding(
            "AMBER" if not d.get("renal_critical") else "RED", "Dose / organ function",
            "%s requires renal dose review" % display_name(proposed_key),
            mechanism=d.get("renal", "Renally cleared."),
            consequence="Accumulation and dose-dependent toxicity on a nominally standard dose.",
            personal="Recorded eGFR %s." % (egfr or "not recorded"),
            action="Adjust the dose before starting, not after.",
            source=d.get("source", ""), reviewed=d.get("reviewed", "")))
    if d.get("hepatic") and "hepatic_impairment" in conditions:
        out.append(finding(
            "AMBER", "Dose / organ function",
            "%s requires hepatic dose review" % display_name(proposed_key),
            mechanism=d.get("hepatic", ""),
            personal="Recorded hepatic impairment.",
            source=d.get("source", ""), reviewed=d.get("reviewed", "")))
    return out


def history_findings(proposed_key):
    """Surface personal adverse-effect history for this drug or its class."""
    out = []
    d = get_drug(proposed_key)
    rows = get_db().execute("SELECT * FROM adverse_events").fetchall()
    for r in rows:
        same_drug = r["drug_key"] == proposed_key
        same_class = (d and r["drug_key"] in DRUGS and
                      DRUGS[r["drug_key"]].get("class") == d.get("class"))
        if not (same_drug or same_class):
            continue
        strength = assess_causality(r)
        bits = []
        if r["dose_at_onset"]:
            bits.append("dose at onset %s" % r["dose_at_onset"])
        if r["latency_days"] is not None:
            bits.append("%s days after the change" % r["latency_days"])
        if r["dechallenge"]:
            bits.append("on withdrawal or reduction: %s" % r["dechallenge_resolved"]
                        if r["dechallenge"] == "yes" else "not withdrawn")
        if r["rechallenge"] == "yes":
            bits.append("on re-exposure: %s" % (r["rechallenge_recurred"] or "not recorded"))
        out.append(finding(
            "AMBER", "Personal history",
            "Previous recorded response to %s%s"
            % (display_name(r["drug_key"] or r["drug_raw"]),
               "" if same_drug else " (same class)"),
            mechanism="Recorded symptom: %s. %s" % (r["symptom"], "; ".join(bits)),
            consequence="Personal historical observation, not an established universal drug effect. "
                        "Causality assessed as: %s." % strength,
            personal="Confounders recorded: %s." % (r["confounders"] or "none recorded"),
            action="Weigh this against the pharmacology rather than in place of it.",
            source="Personal record entered %s." % (r["created"] or "date not recorded"),
            reviewed=r["created"] or ""))
    return out


def assess_causality(row):
    """Crude structured strength-of-association label. Deliberately conservative."""
    score = 0
    if row["latency_days"] is not None and row["latency_days"] <= 30:
        score += 1
    if row["dechallenge"] == "yes" and row["dechallenge_resolved"] == "yes":
        score += 2
    if row["rechallenge"] == "yes" and row["rechallenge_recurred"] == "yes":
        score += 3
    if row["confounders"]:
        score -= 1
    if score >= 5:
        return "probable"
    if score >= 3:
        return "possible"
    if score >= 1:
        return "weak — temporal association only"
    return "insufficient structure to assess"


def withdrawal_findings(proposed_key, action, other_keys):
    if action not in ("stop", "decrease"):
        return []
    out = []
    d = get_drug(proposed_key)
    for r in WITHDRAWAL_RULES:
        trig = r["trigger"]
        if trig == "stop" and action != "stop":
            continue
        if trig == "stop_or_decrease" and action not in ("stop", "decrease"):
            continue
        hit = False
        if "drugs" in r:
            hit = proposed_key in r["drugs"]
        elif r.get("property") == "withdrawal":
            hit = bool(d) and d.get("withdrawal") == r.get("level")
        elif r.get("property") == "cyp_inhibitor":
            hit = bool(d) and bool(d.get("cyp", {}).get("inhibitor"))
        if not hit:
            continue
        extra = ""
        if r.get("property") == "cyp_inhibitor" and d:
            affected = []
            for enzyme in (d.get("cyp", {}).get("inhibitor") or {}):
                for ok in other_keys:
                    od = get_drug(ok)
                    if od and enzyme in (od.get("cyp", {}).get("substrate") or {}):
                        affected.append("%s (%s substrate)" % (display_name(ok), enzyme))
            if affected:
                extra = " Currently affected: %s." % ", ".join(sorted(set(affected)))
            else:
                continue
        out.append(finding(
            r["flag"], "Withdrawal", r["title"],
            mechanism=r.get("note", ""), consequence=r.get("consequence", "") + extra,
            action=r.get("action", ""), source=r.get("source", ""),
            reviewed=r.get("reviewed", ""), rule_id=r["id"]))
    return out


def duplication_findings(proposed_key, other_keys):
    out = []
    d = get_drug(proposed_key)
    if not d:
        return out
    for ok in other_keys:
        od = get_drug(ok)
        if not od:
            continue
        if ok != proposed_key and od.get("class") == d.get("class"):
            out.append(finding(
                "AMBER", "Duplication",
                "Same class already in use: %s" % display_name(ok),
                mechanism="Both are classified as: %s." % d.get("class"),
                consequence="Duplicate therapy, with additive class-specific adverse effects.",
                source="Class comparison from drug property table.",
                reviewed=d.get("reviewed", "")))
    if d.get("duplication_watch"):
        out.append(finding(
            "AMBER", "Duplication",
            "%s is a frequent hidden component of combination products" % display_name(proposed_key),
            mechanism="Commonly present in fixed-dose combinations and over-the-counter products.",
            consequence="Cumulative daily dose can exceed the intended ceiling without any single "
                        "product appearing excessive.",
            action="Total the daily dose across every product before adding another.",
            source="Product labelling.", reviewed=d.get("reviewed", "")))
    return out


def build_discussion(proposed_key, dose, frequency, indication, findings, conditions):
    """A short, neutral question for a phone consultation."""
    priority = {"Interaction": 0, "Pharmacokinetic": 1, "Personal history": 2,
                "Withdrawal": 3, "Drug / condition": 4, "Dose / organ function": 5,
                "QT / conduction": 6, "Duplication": 7, "Cumulative burden": 8,
                "Coverage": 9}

    def rank(f):
        return (FLAG_ORDER.get(f["flag"], 3), priority.get(f["category"], 10))

    ordered = sorted(findings, key=rank)
    lead = ordered[0] if ordered else None
    name = display_name(proposed_key)
    if not lead:
        return ("No flag was raised for %s %s %s from the current knowledge base. "
                "That is not the same as a clean check — the base is narrow. "
                "Worth confirming the indication and intended duration."
                % (name, dose or "", frequency or ""))
    conds = [CONDITION_LABELS[c] for c in conditions if c in CONDITION_LABELS][:3]
    ctx = (" Given " + ", ".join(c.lower() for c in conds) + ",") if conds else ""
    return ("%s is proposed%s. %s%s does this change your preferred dose, choice of agent, "
            "or monitoring plan?"
            % (name + (" " + dose if dose else ""),
               (" for " + indication) if indication else "",
               lead["title"] + ". ", ctx))


def analyse(proposed_key, action="start", dose="", frequency="", indication="",
            extra_keys=None):
    """Main entry point. extra_keys allows an ad-hoc episodic course check."""
    meds = active_meds()
    other_keys = [m["drug_key"] for m in meds if m["drug_key"] != proposed_key]
    if extra_keys:
        other_keys = list(dict.fromkeys(other_keys + [k for k in extra_keys
                                                      if k != proposed_key]))
    conditions = active_conditions()
    all_keys = list(dict.fromkeys(other_keys + [proposed_key]))
    if action == "stop":
        burden_keys = [k for k in all_keys if k != proposed_key]
    else:
        burden_keys = all_keys

    totals, contributors = compute_burdens(burden_keys)

    findings = []
    if action != "stop":
        named = pairwise_findings(proposed_key, other_keys)
        findings += named
        # A curated rule always beats the generic property derivation for the same
        # pair. Reporting both doubles the noise and dilutes the curated wording.
        covered = set()
        for r in PAIRWISE:
            if any(f.get("rule_id") == r["id"] for f in named):
                covered.add(frozenset((r["a"], r["b"])))
        for f in cyp_findings(proposed_key, other_keys):
            if f.pop("pair", None) in covered:
                continue
            findings.append(f)
        findings += burden_findings(totals, contributors, conditions, proposed_key)
        findings += qt_findings(burden_keys, conditions, proposed_key)
        findings += condition_findings(proposed_key, conditions, totals)
        findings += renal_findings(proposed_key, conditions)
        findings += duplication_findings(proposed_key, other_keys)
    findings += history_findings(proposed_key)
    findings += withdrawal_findings(proposed_key, action, other_keys)

    known = get_drug(proposed_key) is not None
    if not known:
        findings.append(finding(
            "UNKNOWN", "Coverage", "%s is not in the knowledge base" % display_name(proposed_key),
            mechanism="No property record exists for this molecule.",
            consequence="No interaction, burden or dosing check has been performed. "
                        "This is a gap in coverage, not a safety finding.",
            action="Add the drug to knowledge/drugs.json before relying on any result here.",
            source="Coverage check.", reviewed=date.today().isoformat()))

    findings.sort(key=lambda f: (FLAG_ORDER.get(f["flag"], 3), f["category"]))
    if any(f["flag"] == "RED" for f in findings):
        overall = "RED"
    elif any(f["flag"] == "AMBER" for f in findings):
        overall = "AMBER"
    else:
        overall = "UNKNOWN"

    return {
        "flag": overall, "findings": findings, "burdens": totals,
        "contributors": contributors, "conditions": sorted(conditions),
        "drug": proposed_key, "known": known, "action": action,
        "dose": dose, "frequency": frequency, "indication": indication,
        "considered": [display_name(k) for k in all_keys],
        "discussion": build_discussion(proposed_key, dose, frequency, indication,
                                       findings, conditions),
        "kb_version": "%s / %s" % (DRUGS_DOC["_meta"]["version"], RULES_DOC["_meta"]["version"]),
    }


def symptom_timeline(symptom, window_days=42):
    """Rank recent medication changes against a presenting symptom."""
    cutoff = (date.today() - timedelta(days=window_days)).isoformat()
    rows = get_db().execute(
        "SELECT * FROM med_events WHERE event_date >= ? ORDER BY event_date DESC",
        (cutoff,)).fetchall()
    signals = SYMPTOM_MAP.get(symptom, ["any_recent"])
    ranked = []
    for r in rows:
        d = get_drug(r["drug_key"])
        why, score = [], 0
        for sig in signals:
            if sig == "any_recent":
                score += 1
                why.append("changed within the window")
                continue
            if not d:
                continue
            if sig.startswith("burden:"):
                parts = sig.split(":")
                bk = parts[1]
                val = (d.get("burden") or {}).get(bk, 0)
                if len(parts) > 2 and parts[2] == "negative":
                    if val < 0:
                        score += 2
                        why.append("reduces %s burden" % bk)
                elif val > 0:
                    score += val + 1
                    why.append("%s burden %d" % (bk, val))
            elif sig.startswith("hr:"):
                if d.get("hr") == sig.split(":")[1]:
                    score += 3
                    why.append("heart rate %s" % sig.split(":")[1])
            elif sig.startswith("bp:"):
                if d.get("bp") in (sig.split(":")[1], "both"):
                    score += 3
                    why.append("blood pressure %s" % sig.split(":")[1])
            elif sig == "qt:any":
                if d.get("qt", "none") != "none":
                    score += 2
                    why.append("QT %s risk" % d.get("qt"))
            elif sig.startswith("withdrawal:"):
                if d.get("withdrawal") == sig.split(":")[1] and r["action"] in ("stop", "decrease"):
                    score += 4
                    why.append("high withdrawal risk and was stopped or reduced")
            elif sig.startswith("class:"):
                if d.get("class") == sig.split(":", 1)[1]:
                    score += 3
                    why.append("drug class %s" % d.get("class"))
            elif sig == "renal_critical":
                if d.get("renal_critical"):
                    score += 2
                    why.append("renally cleared, accumulates")
            elif sig == "oedema":
                if "oedema" in (d.get("notes") or "").lower():
                    score += 3
                    why.append("known to cause oedema")
        if score:
            ranked.append({"event": r, "score": score, "why": sorted(set(why)),
                           "name": display_name(r["drug_key"])})
    ranked.sort(key=lambda x: -x["score"])
    return ranked


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

LAYOUT = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title or 'RxGuard' }}</title>
<style>
:root{
 --ink:#16181D; --paper:#FBFBFA; --panel:#FFFFFF; --rule:#DEDEDB; --muted:#6A6D75;
 --red:#A4262C; --red-bg:#FBEFEF; --amber:#7A4E00; --amber-bg:#FBF4E6;
 --unknown:#4E535D; --unknown-bg:#F1F2F3;
 --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
 --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
 font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{display:flex;min-height:100vh}
nav{width:190px;flex:0 0 190px;border-right:1px solid var(--rule);padding:18px 0;background:var(--panel)}
nav .brand{font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:11px;
 padding:0 18px 14px;color:var(--muted)}
nav a{display:block;padding:7px 18px;text-decoration:none;font-size:14px;border-left:3px solid transparent}
nav a:hover{background:var(--paper)}
nav a.on{border-left-color:var(--ink);font-weight:600}
nav .grp{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
 padding:16px 18px 5px}
main{flex:1;padding:26px 32px;max-width:1080px}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:15px;margin:26px 0 10px;letter-spacing:.02em}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.card{background:var(--panel);border:1px solid var(--rule);padding:16px 18px;margin-bottom:16px}
.constraints{border-left:3px solid var(--ink)}
.constraints ul{margin:6px 0 0;padding-left:18px}
.constraints li{margin:3px 0;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
 color:var(--muted);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--rule)}
td{padding:7px 8px;border-bottom:1px solid var(--rule);vertical-align:top}
.num,.dose{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:13px}
.flag{display:inline-block;font-family:var(--mono);font-size:11px;letter-spacing:.09em;
 padding:2px 7px;border:1px solid currentColor}
.RED{color:var(--red);background:var(--red-bg)}
.AMBER{color:var(--amber);background:var(--amber-bg)}
.UNKNOWN{color:var(--unknown);background:var(--unknown-bg)}
.finding{border:1px solid var(--rule);border-left-width:3px;padding:12px 14px;margin-bottom:10px;
 background:var(--panel)}
.finding.RED{border-left-color:var(--red)}
.finding.AMBER{border-left-color:var(--amber)}
.finding.UNKNOWN{border-left-color:var(--unknown)}
.finding h3{margin:6px 0 6px;font-size:15px}
.finding dl{margin:0;font-size:13.5px}
.finding dt{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
 margin-top:8px}
.finding dd{margin:2px 0 0}
.cat{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
form{margin:0}
label{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
 color:var(--muted);margin:10px 0 3px}
input[type=text],input[type=number],input[type=date],select,textarea{
 width:100%;padding:7px 9px;border:1px solid var(--rule);background:var(--paper);
 font-family:var(--sans);font-size:14px;color:var(--ink);border-radius:0}
input.dose,input.num{font-family:var(--mono)}
textarea{min-height:64px;resize:vertical}
button,.btn{display:inline-block;padding:8px 16px;border:1px solid var(--ink);background:var(--ink);
 color:#fff;font-size:14px;cursor:pointer;text-decoration:none;font-family:var(--sans);border-radius:0}
button.ghost,.btn.ghost{background:transparent;color:var(--ink)}
button:hover{opacity:.88}
.row{display:flex;gap:14px;flex-wrap:wrap}
.row>div{flex:1;min-width:150px}
.flash{border:1px solid var(--rule);border-left:3px solid var(--ink);padding:9px 12px;margin-bottom:14px;
 font-size:14px;background:var(--panel)}
.muted{color:var(--muted);font-size:13px}
.stale{color:var(--amber);font-size:11px;font-family:var(--mono)}
.chip{display:inline-block;border:1px solid var(--rule);padding:2px 8px;margin:2px 3px 2px 0;
 font-size:12px;background:var(--panel)}
.bar{height:5px;background:var(--unknown-bg);position:relative;margin-top:3px}
.bar>span{position:absolute;left:0;top:0;bottom:0;background:var(--muted)}
.bar.hi>span{background:var(--amber)}
.bar.max>span{background:var(--red)}
:focus-visible{outline:2px solid var(--ink);outline-offset:1px}
@media print{nav{display:none}main{padding:0;max-width:none}.card{border:none;padding:0}
 .noprint{display:none}}
@media (max-width:760px){.wrap{flex-direction:column}nav{width:auto;flex:none;display:flex;
 flex-wrap:wrap;padding:8px}nav .brand,nav .grp{display:none}nav a{border-left:none;padding:6px 10px}
 main{padding:16px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style></head><body>
<div class="wrap">
{% if session.get('auth') %}
<nav>
 <div class="brand">RxGuard</div>
 <div class="grp">Apps</div>
 <a href="https://health.dr-manoj.in">GutLog &#8599;</a>
 <a href="https://fit.dr-manoj.in">FitLog &#8599;</a>
 <a href="{{ url_for('dashboard') }}" class="{{ 'on' if nav=='dash' }}">Dashboard</a>
 <a href="{{ url_for('card') }}" class="{{ 'on' if nav=='card' }}">One-page list</a>
 <div class="grp">Check</div>
 <a href="{{ url_for('episode') }}" class="{{ 'on' if nav=='episode' }}">Quick check</a>
 <a href="{{ url_for('analyse_view') }}" class="{{ 'on' if nav=='analyse' }}">Full analysis</a>
 <a href="{{ url_for('symptom_view') }}" class="{{ 'on' if nav=='symptom' }}">New symptom</a>
 <div class="grp">Record</div>
 <a href="{{ url_for('meds') }}" class="{{ 'on' if nav=='meds' }}">Medications</a>
 <a href="{{ url_for('adverse') }}" class="{{ 'on' if nav=='adverse' }}">Adverse effects</a>
 <a href="{{ url_for('consultations') }}" class="{{ 'on' if nav=='consult' }}">Consultations</a>
 <a href="{{ url_for('profile') }}" class="{{ 'on' if nav=='profile' }}">Profile</a>
 <div class="grp">Review</div>
 <a href="{{ url_for('reviews') }}" class="{{ 'on' if nav=='reviews' }}">Review queue</a>
 <a href="{{ url_for('knowledge') }}" class="{{ 'on' if nav=='kb' }}">Knowledge base</a>
 <a href="{{ url_for('logout') }}" class="noprint">Sign out</a>
</nav>
{% endif %}
<main>
{% with msgs = get_flashed_messages() %}{% for m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}{% endwith %}
{{ body|safe }}
</main></div></body></html>"""


def page(body, **kw):
    return render_template_string(LAYOUT, body=render_template_string(body, **kw), **kw)


FINDING_BLOCK = """
{% for f in findings %}
<div class="finding {{ f.flag }}">
  <span class="flag {{ f.flag }}">{{ f.flag }}</span>
  <span class="cat">{{ f.category }}{% if f.rule_id %} · {{ f.rule_id }}{% endif %}</span>
  <h3>{{ f.title }}</h3>
  <dl>
    {% if f.mechanism %}<dt>Mechanism</dt><dd>{{ f.mechanism }}</dd>{% endif %}
    {% if f.consequence %}<dt>Consequence</dt><dd>{{ f.consequence }}</dd>{% endif %}
    {% if f.personal %}<dt>Why it applies here</dt><dd>{{ f.personal }}</dd>{% endif %}
    {% if f.monitoring %}<dt>Monitoring</dt><dd>{{ f.monitoring }}</dd>{% endif %}
    {% if f.action %}<dt>Action</dt><dd>{{ f.action }}</dd>{% endif %}
    {% if f.source %}<dt>Source</dt><dd class="muted">{{ f.source }}
      {% if f.reviewed %} · reviewed {{ f.reviewed }}{% endif %}
      {% if f.stale %}<span class="stale">STALE</span>{% endif %}</dd>{% endif %}
  </dl>
</div>
{% endfor %}
"""


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

def create_app(db_path=None, secret=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get("RXGUARD_DB", DEFAULT_DB)
    app.secret_key = (secret or os.environ.get("RXGUARD_SECRET")
                      or secrets.token_hex(32))
    init_db(app.config["DB_PATH"])

    @app.before_request
    def _before():
        g.db_path = app.config["DB_PATH"]
        if session.get("auth"):
            if session.get("epoch") != setting("auth_epoch", "1"):
                session.clear()

    @app.teardown_appcontext
    def _teardown(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def login_required(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not session.get("auth"):
                return redirect(url_for("login"))
            return fn(*a, **kw)
        return inner

    # ---------------------------------------------------------------- auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        pw_hash = setting("password_hash")
        if request.method == "POST":
            pw = request.form.get("password", "")
            if not pw_hash:
                if len(pw) < 8:
                    flash("Choose a password of at least 8 characters.")
                    return redirect(url_for("login"))
                set_setting("password_hash", generate_password_hash(pw))
                set_setting("auth_epoch", "1")
                session["auth"] = True
                session["epoch"] = "1"
                flash("Password set. This is a single-user application.")
                return redirect(url_for("dashboard"))
            if check_password_hash(pw_hash, pw):
                session["auth"] = True
                session["epoch"] = setting("auth_epoch", "1")
                return redirect(url_for("dashboard"))
            flash("Incorrect password.")
            return redirect(url_for("login"))
        body = """
        <h1>RxGuard</h1>
        <p class="sub">{{ 'Set a password to begin.' if not has_pw else 'Sign in.' }}</p>
        <div class="card" style="max-width:340px">
        <form method="post"><label>Password</label>
        <input type="password" name="password" autofocus required>
        <p></p><button>{{ 'Set password' if not has_pw else 'Sign in' }}</button></form></div>"""
        return render_template_string(LAYOUT, body=render_template_string(
            body, has_pw=bool(pw_hash)), title="Sign in")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ----------------------------------------------------------- dashboard
    def constraints_list():
        return get_db().execute(
            "SELECT * FROM constraints WHERE active=1 ORDER BY id").fetchall()

    @app.route("/")
    @login_required
    def dashboard():
        meds = active_meds()
        conds = active_conditions()
        totals, contributors = compute_burdens([m["drug_key"] for m in meds])
        due = get_db().execute(
            "SELECT * FROM overrides WHERE status='open' AND due_2w <= ? ORDER BY due_2w",
            (date.today().isoformat(),)).fetchall()
        stale_meds = get_db().execute(
            "SELECT * FROM medications WHERE status IN ('active','tapering') AND "
            "(last_reviewed_by_prescriber IS NULL OR last_reviewed_by_prescriber < ?)",
            ((date.today() - timedelta(days=180)).isoformat(),)).fetchall()
        no_benefit = [m for m in meds if m["benefit"] in ("none", "unknown")]
        recent = get_db().execute(
            "SELECT * FROM med_events ORDER BY event_date DESC, id DESC LIMIT 6").fetchall()
        body = """
        <h1>Dashboard</h1>
        <p class="sub">{{ meds|length }} active · knowledge base {{ kbv }}</p>
        {% if constraints %}
        <div class="card constraints"><strong>Prescribing constraints</strong>
        <ul>{% for c in constraints %}<li>{{ c['text'] }}</li>{% endfor %}</ul></div>
        {% endif %}
        {% if due %}<div class="card"><strong>Review due</strong>
        <table><tr><th>Finding</th><th>Flag</th><th>Overridden</th><th></th></tr>
        {% for o in due %}<tr><td>{{ o['finding_title'] }}</td>
        <td><span class="flag {{ o['flag'] }}">{{ o['flag'] }}</span></td>
        <td class="num">{{ o['created'] }}</td>
        <td><a href="{{ url_for('reviews') }}">Review</a></td></tr>{% endfor %}</table></div>
        {% endif %}
        <h2>Active medications</h2>
        <table><tr><th>Drug</th><th>Dose</th><th>Freq</th><th>Indication</th>
        <th>Prescriber</th><th>Benefit</th><th>Type</th></tr>
        {% for m in meds %}<tr>
        <td>{{ m['drug_key'].replace('_',' ') }}{% if m['status']=='tapering' %}
          <span class="muted">tapering</span>{% endif %}</td>
        <td class="dose">{{ m['dose'] }}</td><td class="dose">{{ m['frequency'] }}</td>
        <td>{{ m['indication'] }}</td>
        <td>{{ m['prescriber'] }}{% if m['specialty'] %} <span class="muted">{{ m['specialty'] }}</span>{% endif %}</td>
        <td>{{ m['benefit'] }}</td><td class="muted">{{ m['kind'] }}</td></tr>
        {% else %}<tr><td colspan="7" class="muted">Nothing recorded yet.
        <a href="{{ url_for('meds') }}">Add the current list</a>.</td></tr>{% endfor %}</table>
        <h2>Cumulative burden</h2>
        <table><tr><th>Category</th><th>Total</th><th></th><th>Contributors</th></tr>
        {% for k, v in totals.items() %}{% if v %}<tr><td>{{ k }}</td>
        <td class="num">{{ v }}</td>
        <td style="width:120px"><div class="bar {{ 'max' if v>=5 else ('hi' if v>=3 else '') }}">
          <span style="width:{{ [v*18,100]|min }}%"></span></div></td>
        <td class="muted">{% for n,s in contributors[k] %}{{ n }} ({{ s }}){% if not loop.last %}, {% endif %}{% endfor %}</td>
        </tr>{% endif %}{% endfor %}</table>
        {% if no_benefit or stale_meds %}
        <h2>Deprescribing candidates</h2>
        <table><tr><th>Drug</th><th>Reason</th></tr>
        {% for m in no_benefit %}<tr><td>{{ m['drug_key'].replace('_',' ') }}</td>
        <td>Benefit recorded as <strong>{{ m['benefit'] }}</strong></td></tr>{% endfor %}
        {% for m in stale_meds %}<tr><td>{{ m['drug_key'].replace('_',' ') }}</td>
        <td>Not reviewed by prescriber in over 180 days</td></tr>{% endfor %}</table>
        {% endif %}
        <h2>Recent changes</h2>
        <table><tr><th>Date</th><th>Drug</th><th>Action</th><th>Change</th><th>Source</th></tr>
        {% for e in recent %}<tr><td class="num">{{ e['event_date'] }}</td>
        <td>{{ (e['drug_key'] or '').replace('_',' ') }}</td><td>{{ e['action'] }}</td>
        <td class="dose">{{ e['old_dose'] or '' }}{% if e['old_dose'] and e['new_dose'] %} &rarr; {% endif %}{{ e['new_dose'] or '' }}</td>
        <td class="muted">{{ e['source'] or '' }}</td></tr>
        {% else %}<tr><td colspan="5" class="muted">No changes recorded.</td></tr>{% endfor %}</table>
        """
        return page(body, nav="dash", title="Dashboard", meds=meds, conds=conds,
                    totals=totals, contributors=contributors, due=due,
                    constraints=constraints_list(), no_benefit=no_benefit,
                    stale_meds=stale_meds, recent=recent,
                    kbv="%s/%s" % (DRUGS_DOC["_meta"]["version"], RULES_DOC["_meta"]["version"]))

    # ------------------------------------------------------------ one-page
    @app.route("/card")
    @login_required
    def card():
        meds = active_meds()
        aes = get_db().execute(
            "SELECT * FROM adverse_events ORDER BY onset_date DESC LIMIT 8").fetchall()
        body = """
        <h1>Current medication list</h1>
        <p class="sub">Generated {{ today }} · single-user personal record</p>
        {% if constraints %}
        <div class="card constraints"><strong>Prescribing constraints</strong>
        <ul>{% for c in constraints %}<li>{{ c['text'] }}</li>{% endfor %}</ul></div>{% endif %}
        <table><tr><th>Drug</th><th>Dose</th><th>Frequency</th><th>Indication</th>
        <th>Started</th><th>Prescriber</th></tr>
        {% for m in meds %}<tr><td><strong>{{ m['drug_key'].replace('_',' ') }}</strong></td>
        <td class="dose">{{ m['dose'] }}</td><td class="dose">{{ m['frequency'] }}</td>
        <td>{{ m['indication'] }}</td><td class="num">{{ m['start_date'] or '' }}</td>
        <td>{{ m['prescriber'] }}{% if m['specialty'] %}, {{ m['specialty'] }}{% endif %}</td>
        </tr>{% endfor %}</table>
        {% if conds %}<h2>Relevant conditions</h2>
        <p>{% for c in conds %}<span class="chip">{{ labels.get(c, c) }}</span>{% endfor %}</p>{% endif %}
        {% if aes %}<h2>Previous adverse effects</h2>
        <table><tr><th>Drug</th><th>Effect</th><th>Onset</th><th>Assessment</th></tr>
        {% for a in aes %}<tr><td>{{ (a['drug_key'] or a['drug_raw'] or '').replace('_',' ') }}</td>
        <td>{{ a['symptom'] }}</td><td class="num">{{ a['onset_date'] or '' }}</td>
        <td class="muted">{{ a['assessment'] or '' }}</td></tr>{% endfor %}</table>{% endif %}
        <p class="noprint" style="margin-top:20px"><button onclick="window.print()">Print</button></p>
        """
        return page(body, nav="card", title="One-page list", meds=meds,
                    constraints=constraints_list(), conds=sorted(active_conditions()),
                    labels=CONDITION_LABELS, aes=aes, today=date.today().isoformat())

    # -------------------------------------------------------- quick check
    @app.route("/episode", methods=["GET", "POST"])
    @login_required
    def episode():
        result = None
        raw = ""
        if request.method == "POST":
            raw = request.form.get("drugs", "")
            names = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]
            keys = [norm_key(n) for n in names]
            results = []
            for i, k in enumerate(keys):
                others = [x for j, x in enumerate(keys) if j != i]
                results.append(analyse(k, action="start", extra_keys=others))
            merged, seen = [], set()
            for r in results:
                for f in r["findings"]:
                    sig = (f["title"], f["category"])
                    if sig in seen:
                        continue
                    seen.add(sig)
                    merged.append(f)
            merged.sort(key=lambda f: (FLAG_ORDER.get(f["flag"], 3), f["category"]))
            flag = ("RED" if any(f["flag"] == "RED" for f in merged)
                    else ("AMBER" if any(f["flag"] == "AMBER" for f in merged) else "UNKNOWN"))
            result = {"flag": flag, "findings": merged,
                      "names": [display_name(k) for k in keys],
                      "unknown": [display_name(k) for k in keys if not get_drug(k)]}
        body = """
        <h1>Quick check</h1>
        <p class="sub">For an episodic course started against the current list. Type one or
        more drugs, comma separated.</p>
        <div class="card"><form method="post">
        <label>Drugs to add</label>
        <input type="text" name="drugs" value="{{ raw }}" autofocus
          placeholder="etoricoxib, thiocolchicoside, pantoprazole">
        <p></p><button>Check against current list</button></form></div>
        {% if result %}
        <h2>Result <span class="flag {{ result.flag }}">{{ result.flag }}</span></h2>
        <p class="muted">Checked: {{ result.names|join(', ') }}.
        {% if result.unknown %}Not in knowledge base: {{ result.unknown|join(', ') }} —
        no checks performed for these.{% endif %}</p>
        """ + FINDING_BLOCK + """
        {% if not result.findings %}<p class="muted">Nothing flagged. The knowledge base is
        narrow — this is not a clean bill of health.</p>{% endif %}
        {% endif %}
        """
        return page(body, nav="episode", title="Quick check", result=result, raw=raw,
                    findings=result["findings"] if result else [])

    # ------------------------------------------------------- full analysis
    @app.route("/analyse", methods=["GET", "POST"])
    @login_required
    def analyse_view():
        result = None
        form = {"drug": "", "dose": "", "frequency": "", "indication": "", "action": "start"}
        if request.method == "POST":
            for k in form:
                form[k] = request.form.get(k, form[k])
            key = norm_key(form["drug"])
            result = analyse(key, action=form["action"], dose=form["dose"],
                             frequency=form["frequency"], indication=form["indication"])
            db = get_db()
            cur = db.execute(
                "INSERT INTO analyses (created, drug_key, dose, frequency, indication, "
                "action, flag, result_json, kb_version) VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), key, form["dose"],
                 form["frequency"], form["indication"], form["action"], result["flag"],
                 json.dumps(result), result["kb_version"]))
            db.commit()
            result["analysis_id"] = cur.lastrowid
        body = """
        <h1>Full analysis</h1>
        <p class="sub">A proposed start, dose change or stop, checked against the current
        list, stored conditions and personal adverse-effect history.</p>
        <div class="card"><form method="post">
        <div class="row">
          <div><label>Drug (generic)</label>
            <input type="text" name="drug" value="{{ form.drug }}" required autofocus></div>
          <div><label>Action</label><select name="action">
            {% for a in ['start','increase','decrease','stop'] %}
            <option value="{{ a }}" {{ 'selected' if form.action==a }}>{{ a }}</option>{% endfor %}
          </select></div>
        </div>
        <div class="row">
          <div><label>Dose</label><input class="dose" type="text" name="dose" value="{{ form.dose }}"></div>
          <div><label>Frequency</label><input class="dose" type="text" name="frequency" value="{{ form.frequency }}"></div>
          <div><label>Indication</label><input type="text" name="indication" value="{{ form.indication }}"></div>
        </div>
        <p></p><button>Analyse</button></form></div>
        {% if result %}
        <h2>{{ result.drug.replace('_',' ') }} — {{ result.action }}
          <span class="flag {{ result.flag }}">{{ result.flag }}</span></h2>
        <p class="muted">Considered against: {{ result.considered|join(', ') }}.
          Knowledge base {{ result.kb_version }}.</p>
        <div class="card"><strong>For the prescriber</strong>
          <p style="margin:6px 0 0">{{ result.discussion }}</p></div>
        """ + FINDING_BLOCK + """
        {% if not result.findings %}<p class="muted">No findings. Absence of a flag is not
        evidence of safety — this knowledge base is deliberately narrow.</p>{% endif %}
        {% if result.findings %}
        <div class="card"><strong>Proceeding anyway?</strong>
        <p class="muted">Logging an override schedules a review at two and six weeks, with your
        stated reason shown back to you alongside what has happened since.</p>
        <form method="post" action="{{ url_for('add_override') }}">
        <input type="hidden" name="analysis_id" value="{{ result.analysis_id }}">
        <input type="hidden" name="finding_title" value="{{ result.findings[0].title }}">
        <input type="hidden" name="flag" value="{{ result.flag }}">
        <label>Reason for proceeding</label>
        <textarea name="reason" required></textarea>
        <p></p><button class="ghost">Log override and schedule review</button></form></div>
        {% endif %}
        {% endif %}
        """
        return page(body, nav="analyse", title="Full analysis", form=form, result=result,
                    findings=result["findings"] if result else [])

    # -------------------------------------------------------- new symptom
    @app.route("/symptom", methods=["GET", "POST"])
    @login_required
    def symptom_view():
        ranked, chosen = None, ""
        if request.method == "POST":
            chosen = request.form.get("symptom", "")
            ranked = symptom_timeline(chosen)
            if request.form.get("save"):
                db = get_db()
                db.execute(
                    "INSERT INTO symptoms (symptom, onset_date, notes, reviewed_changes, created) "
                    "VALUES (?,?,?,?,?)",
                    (chosen, request.form.get("onset_date", ""), request.form.get("notes", ""),
                     json.dumps([r["name"] for r in ranked]),
                     datetime.now().isoformat(timespec="seconds")))
                db.commit()
                flash("Symptom recorded with the drug changes reviewed.")
        body = """
        <h1>New symptom</h1>
        <p class="sub">Drug review before disease attribution. Enter the symptom first; the
        medication changes of the last six weeks are ranked against it.</p>
        <div class="card"><form method="post">
        <div class="row">
        <div><label>Symptom</label><select name="symptom">
          {% for s in symptoms %}<option value="{{ s }}" {{ 'selected' if s==chosen }}>
          {{ s.replace('_',' ') }}</option>{% endfor %}</select></div>
        <div><label>Onset date</label><input type="date" name="onset_date"></div>
        </div>
        <label>Notes</label><textarea name="notes"></textarea>
        <p></p><button>Show recent drug changes</button>
        <button name="save" value="1" class="ghost">Show and record</button></form></div>
        {% if ranked is not none %}
        <h2>Medication changes in the last 6 weeks</h2>
        {% if ranked %}
        <table><tr><th>Date</th><th>Drug</th><th>Action</th><th>Change</th><th>Why it ranks</th></tr>
        {% for r in ranked %}<tr><td class="num">{{ r.event['event_date'] }}</td>
        <td><strong>{{ r.name }}</strong></td><td>{{ r.event['action'] }}</td>
        <td class="dose">{{ r.event['old_dose'] or '' }}{% if r.event['old_dose'] and r.event['new_dose'] %} &rarr; {% endif %}{{ r.event['new_dose'] or '' }}</td>
        <td class="muted">{{ r.why|join('; ') }}</td></tr>{% endfor %}</table>
        <p class="muted">Ranking orders which changes deserve a look first. It is not a
        causal claim, and a drug appearing here does not mean it is responsible.</p>
        {% else %}
        <p class="muted">No medication changes recorded in the last six weeks. If that is
        wrong, the ledger is out of date — record the change before drawing conclusions.</p>
        {% endif %}{% endif %}
        """
        return page(body, nav="symptom", title="New symptom",
                    symptoms=[s for s in SYMPTOM_MAP if not s.startswith("_")],
                    ranked=ranked, chosen=chosen)

    # ------------------------------------------------------- medications
    @app.route("/meds", methods=["GET", "POST"])
    @login_required
    def meds():
        db = get_db()
        if request.method == "POST":
            key = norm_key(request.form.get("drug", ""))
            today = date.today().isoformat()
            cur = db.execute(
                "INSERT INTO medications (drug_key, raw_name, dose, frequency, route, "
                "indication, prescriber, specialty, kind, status, benefit, start_date, "
                "last_change, last_reviewed_by_prescriber, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, request.form.get("drug", ""), request.form.get("dose", ""),
                 request.form.get("frequency", ""), request.form.get("route", "oral"),
                 request.form.get("indication", ""), request.form.get("prescriber", ""),
                 request.form.get("specialty", ""), request.form.get("kind", "chronic"),
                 "active", request.form.get("benefit", "unknown"),
                 request.form.get("start_date", "") or today, today,
                 request.form.get("start_date", "") or today, request.form.get("notes", "")))
            db.execute(
                "INSERT INTO med_events (med_id, drug_key, event_date, action, new_dose, "
                "source, note) VALUES (?,?,?,?,?,?,?)",
                (cur.lastrowid, key, request.form.get("start_date", "") or today, "start",
                 request.form.get("dose", ""), request.form.get("source", "self"), ""))
            db.commit()
            flash("Added %s." % display_name(key))
            return redirect(url_for("meds"))
        rows = db.execute("SELECT * FROM medications ORDER BY "
                          "CASE status WHEN 'active' THEN 0 WHEN 'tapering' THEN 1 ELSE 2 END, "
                          "drug_key").fetchall()
        body = """
        <h1>Medications</h1>
        <p class="sub">Every drug traceable to who started it and when it was last reviewed.</p>
        <div class="card"><form method="post">
        <div class="row">
          <div><label>Drug (generic)</label><input type="text" name="drug" required></div>
          <div><label>Dose</label><input class="dose" type="text" name="dose"></div>
          <div><label>Frequency</label><input class="dose" type="text" name="frequency"></div>
          <div><label>Route</label><input type="text" name="route" value="oral"></div>
        </div>
        <div class="row">
          <div><label>Indication</label><input type="text" name="indication"></div>
          <div><label>Prescriber</label><input type="text" name="prescriber"></div>
          <div><label>Specialty</label><input type="text" name="specialty"></div>
        </div>
        <div class="row">
          <div><label>Type</label><select name="kind">
            <option value="chronic">chronic</option><option value="episodic">episodic</option>
          </select></div>
          <div><label>Benefit</label><select name="benefit">
            <option value="unknown">unknown</option><option value="none">none</option>
            <option value="partial">partial</option><option value="good">good</option>
          </select></div>
          <div><label>Started</label><input type="date" name="start_date"></div>
          <div><label>Source</label><select name="source">
            <option value="in-person">in-person</option><option value="phone">phone</option>
            <option value="self">self</option></select></div>
        </div>
        <p></p><button>Add medication</button></form></div>
        <table><tr><th>Drug</th><th>Dose</th><th>Freq</th><th>Indication</th><th>Prescriber</th>
        <th>Benefit</th><th>Status</th><th>Reviewed</th><th></th></tr>
        {% for m in rows %}<tr>
        <td>{{ m['drug_key'].replace('_',' ') }}</td><td class="dose">{{ m['dose'] }}</td>
        <td class="dose">{{ m['frequency'] }}</td><td>{{ m['indication'] }}</td>
        <td>{{ m['prescriber'] }}{% if m['specialty'] %}<br><span class="muted">{{ m['specialty'] }}</span>{% endif %}</td>
        <td>{{ m['benefit'] }}</td><td>{{ m['status'] }}</td>
        <td class="num">{{ m['last_reviewed_by_prescriber'] or '—' }}</td>
        <td>{% if m['status'] in ('active','tapering') %}
        <form method="post" action="{{ url_for('med_event', med_id=m['id']) }}">
        <input type="hidden" name="action" value="stop"><button class="ghost">Stop</button></form>
        {% endif %}</td></tr>{% endfor %}</table>
        """
        return page(body, nav="meds", title="Medications", rows=rows)

    @app.route("/meds/<int:med_id>/event", methods=["POST"])
    @login_required
    def med_event(med_id):
        db = get_db()
        m = db.execute("SELECT * FROM medications WHERE id=?", (med_id,)).fetchone()
        if not m:
            flash("Not found.")
            return redirect(url_for("meds"))
        act = request.form.get("action", "stop")
        today = date.today().isoformat()
        new_dose = request.form.get("new_dose", "")
        if act == "stop":
            db.execute("UPDATE medications SET status='stopped', stop_date=?, last_change=? "
                       "WHERE id=?", (today, today, med_id))
        else:
            db.execute("UPDATE medications SET dose=?, last_change=? WHERE id=?",
                       (new_dose or m["dose"], today, med_id))
        db.execute("INSERT INTO med_events (med_id, drug_key, event_date, action, old_dose, "
                   "new_dose, source, note) VALUES (?,?,?,?,?,?,?,?)",
                   (med_id, m["drug_key"], today, act, m["dose"], new_dose,
                    request.form.get("source", "self"), request.form.get("note", "")))
        db.commit()
        flash("%s: %s recorded." % (display_name(m["drug_key"]), act))
        return redirect(url_for("meds"))

    # --------------------------------------------------- adverse effects
    @app.route("/adverse", methods=["GET", "POST"])
    @login_required
    def adverse():
        db = get_db()
        if request.method == "POST":
            key = norm_key(request.form.get("drug", ""))
            lat = request.form.get("latency_days", "")
            try:
                lat = int(lat)
            except (TypeError, ValueError):
                lat = None
            row = {
                "drug_key": key, "drug_raw": request.form.get("drug", ""),
                "symptom": request.form.get("symptom", ""),
                "onset_date": request.form.get("onset_date", ""),
                "dose_at_onset": request.form.get("dose_at_onset", ""),
                "latency_days": lat,
                "dechallenge": request.form.get("dechallenge", ""),
                "dechallenge_resolved": request.form.get("dechallenge_resolved", ""),
                "rechallenge": request.form.get("rechallenge", ""),
                "rechallenge_recurred": request.form.get("rechallenge_recurred", ""),
                "confounders": request.form.get("confounders", ""),
                "severity": request.form.get("severity", ""),
                "notes": request.form.get("notes", ""),
            }

            class R(dict):
                def __getitem__(self, k):
                    return self.get(k)
            row["assessment"] = assess_causality(R(row))
            db.execute(
                "INSERT INTO adverse_events (drug_key, drug_raw, symptom, onset_date, "
                "dose_at_onset, latency_days, dechallenge, dechallenge_resolved, rechallenge, "
                "rechallenge_recurred, confounders, severity, assessment, notes, created) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["drug_key"], row["drug_raw"], row["symptom"], row["onset_date"],
                 row["dose_at_onset"], row["latency_days"], row["dechallenge"],
                 row["dechallenge_resolved"], row["rechallenge"], row["rechallenge_recurred"],
                 row["confounders"], row["severity"], row["assessment"], row["notes"],
                 date.today().isoformat()))
            db.commit()
            flash("Recorded. Causality assessed as: %s." % row["assessment"])
            return redirect(url_for("adverse"))
        rows = db.execute("SELECT * FROM adverse_events ORDER BY id DESC").fetchall()
        body = """
        <h1>Adverse effects</h1>
        <p class="sub">The temporal fields are what separate evidence from anecdote. This record
        outranks the literature in the evidence hierarchy, so it has to earn that position.</p>
        <div class="card"><form method="post">
        <div class="row">
          <div><label>Drug</label><input type="text" name="drug" required></div>
          <div><label>Symptom</label><input type="text" name="symptom" required></div>
          <div><label>Onset date</label><input type="date" name="onset_date"></div>
        </div>
        <div class="row">
          <div><label>Dose at onset</label><input class="dose" type="text" name="dose_at_onset"></div>
          <div><label>Days from dose change to onset</label>
            <input class="num" type="number" name="latency_days"></div>
          <div><label>Severity</label><select name="severity">
            <option value="mild">mild</option><option value="moderate">moderate</option>
            <option value="severe">severe</option></select></div>
        </div>
        <div class="row">
          <div><label>Drug stopped or reduced?</label><select name="dechallenge">
            <option value="">not recorded</option><option value="yes">yes</option>
            <option value="no">no</option></select></div>
          <div><label>If so, did it resolve?</label><select name="dechallenge_resolved">
            <option value="">not recorded</option><option value="yes">yes</option>
            <option value="no">no</option><option value="partial">partially</option></select></div>
          <div><label>Re-exposed later?</label><select name="rechallenge">
            <option value="">not recorded</option><option value="yes">yes</option>
            <option value="no">no</option></select></div>
          <div><label>If so, did it recur?</label><select name="rechallenge_recurred">
            <option value="">not recorded</option><option value="yes">yes</option>
            <option value="no">no</option></select></div>
        </div>
        <label>Confounders present at the time</label>
        <input type="text" name="confounders" placeholder="other drug changes, intercurrent illness, stress">
        <label>Notes</label><textarea name="notes"></textarea>
        <p></p><button>Record adverse effect</button></form></div>
        <table><tr><th>Drug</th><th>Symptom</th><th>Onset</th><th>Dose</th><th>Latency</th>
        <th>Dechallenge</th><th>Rechallenge</th><th>Assessment</th></tr>
        {% for a in rows %}<tr><td>{{ (a['drug_key'] or '').replace('_',' ') }}</td>
        <td>{{ a['symptom'] }}</td><td class="num">{{ a['onset_date'] or '' }}</td>
        <td class="dose">{{ a['dose_at_onset'] or '' }}</td>
        <td class="num">{{ a['latency_days'] if a['latency_days'] is not none else '—' }}</td>
        <td>{{ a['dechallenge'] or '—' }}{% if a['dechallenge_resolved'] %} / {{ a['dechallenge_resolved'] }}{% endif %}</td>
        <td>{{ a['rechallenge'] or '—' }}{% if a['rechallenge_recurred'] %} / {{ a['rechallenge_recurred'] }}{% endif %}</td>
        <td><strong>{{ a['assessment'] }}</strong></td></tr>{% endfor %}</table>
        """
        return page(body, nav="adverse", title="Adverse effects", rows=rows)

    # ---------------------------------------------------- consultations
    @app.route("/consultations", methods=["GET", "POST"])
    @login_required
    def consultations():
        db = get_db()
        if request.method == "POST":
            db.execute(
                "INSERT INTO consultations (consult_date, doctor, specialty, mode, summary, "
                "changes, created) VALUES (?,?,?,?,?,?,?)",
                (request.form.get("consult_date", "") or date.today().isoformat(),
                 request.form.get("doctor", ""), request.form.get("specialty", ""),
                 request.form.get("mode", "phone"), request.form.get("summary", ""),
                 request.form.get("changes", ""), datetime.now().isoformat(timespec="seconds")))
            db.commit()
            flash("Consultation recorded.")
            return redirect(url_for("consultations"))
        rows = db.execute("SELECT * FROM consultations ORDER BY consult_date DESC, id DESC").fetchall()
        body = """
        <h1>Consultations</h1>
        <p class="sub">Phone advice leaves no paper. A ledger that has drifted out of step with
        reality is more dangerous than no ledger.</p>
        <div class="card"><form method="post">
        <div class="row">
          <div><label>Date</label><input type="date" name="consult_date"></div>
          <div><label>Doctor</label><input type="text" name="doctor"></div>
          <div><label>Specialty</label><input type="text" name="specialty"></div>
          <div><label>Mode</label><select name="mode">
            <option value="phone">phone</option><option value="in-person">in-person</option>
            <option value="message">message</option></select></div>
        </div>
        <label>What was said</label><textarea name="summary"></textarea>
        <label>What changed</label><input type="text" name="changes">
        <p></p><button>Record consultation</button></form></div>
        <table><tr><th>Date</th><th>Doctor</th><th>Specialty</th><th>Mode</th>
        <th>Summary</th><th>Changes</th></tr>
        {% for c in rows %}<tr><td class="num">{{ c['consult_date'] }}</td><td>{{ c['doctor'] }}</td>
        <td>{{ c['specialty'] }}</td><td>{{ c['mode'] }}</td><td>{{ c['summary'] }}</td>
        <td>{{ c['changes'] }}</td></tr>{% endfor %}</table>
        """
        return page(body, nav="consult", title="Consultations", rows=rows)

    # ------------------------------------------------------------ profile
    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        db = get_db()
        if request.method == "POST":
            if request.form.get("form") == "conditions":
                for code, _ in CONDITIONS:
                    db.execute("UPDATE conditions SET active=? WHERE code=?",
                               (1 if request.form.get(code) else 0, code))
                db.commit()
                flash("Conditions updated.")
            elif request.form.get("form") == "observations":
                for k in ("age", "egfr", "creatinine", "potassium", "magnesium",
                          "usual_hr", "usual_bp", "current_hr", "current_bp", "qtc"):
                    set_profile(k, request.form.get(k, ""))
                flash("Observations updated.")
            elif request.form.get("form") == "constraint":
                t = request.form.get("text", "").strip()
                if t:
                    db.execute("INSERT INTO constraints (text, created, active) VALUES (?,?,1)",
                               (t, date.today().isoformat()))
                    db.commit()
                    flash("Constraint added.")
            return redirect(url_for("profile"))
        conds = active_conditions()
        body = """
        <h1>Profile</h1>
        <p class="sub">Only what can change a medication safety interpretation.</p>
        <h2>Prescribing constraints</h2>
        <div class="card constraints">
        <ul>{% for c in constraints %}<li>{{ c['text'] }}</li>
        {% else %}<li class="muted">None recorded. These are the durable facts a new
        specialist needs in ten seconds.</li>{% endfor %}</ul>
        <form method="post"><input type="hidden" name="form" value="constraint">
        <label>Add a constraint</label><input type="text" name="text"
          placeholder="Documented ventricular ectopy 2025; HR and BP rose on nortriptyline 25 mg">
        <p></p><button class="ghost">Add</button></form></div>
        <h2>Conditions</h2>
        <div class="card"><form method="post">
        <input type="hidden" name="form" value="conditions">
        {% for code, label in conditions_list %}
        <label style="text-transform:none;letter-spacing:0;font-size:14px;color:var(--ink)">
        <input type="checkbox" name="{{ code }}" style="width:auto"
          {{ 'checked' if code in conds }}> {{ label }}</label>
        {% endfor %}
        <p></p><button>Save conditions</button></form></div>
        <h2>Selected objective data</h2>
        <div class="card"><form method="post">
        <input type="hidden" name="form" value="observations">
        <div class="row">
        {% for k, lbl in fields %}
        <div><label>{{ lbl }}</label>
        <input class="num" type="text" name="{{ k }}" value="{{ pv(k) }}"></div>
        {% endfor %}
        </div><p></p><button>Save</button></form></div>
        """
        return page(body, nav="profile", title="Profile", conds=conds,
                    conditions_list=CONDITIONS, constraints=constraints_list(),
                    pv=profile_value,
                    fields=[("age", "Age"), ("egfr", "eGFR"), ("creatinine", "Creatinine"),
                            ("potassium", "Potassium"), ("magnesium", "Magnesium"),
                            ("usual_hr", "Usual HR"), ("usual_bp", "Usual BP"),
                            ("current_hr", "Current HR"), ("current_bp", "Current BP"),
                            ("qtc", "QTc")])

    # ------------------------------------------------------ review queue
    @app.route("/overrides", methods=["POST"])
    @login_required
    def add_override():
        db = get_db()
        today = date.today()
        db.execute(
            "INSERT INTO overrides (analysis_id, finding_title, flag, reason, created, "
            "due_2w, due_6w, status) VALUES (?,?,?,?,?,?,?, 'open')",
            (request.form.get("analysis_id"), request.form.get("finding_title", ""),
             request.form.get("flag", ""), request.form.get("reason", ""),
             today.isoformat(), (today + timedelta(days=14)).isoformat(),
             (today + timedelta(days=42)).isoformat()))
        db.commit()
        flash("Override logged. Review scheduled at two and six weeks.")
        return redirect(url_for("reviews"))

    @app.route("/reviews", methods=["GET", "POST"])
    @login_required
    def reviews():
        db = get_db()
        if request.method == "POST":
            db.execute("UPDATE overrides SET status=?, outcome=? WHERE id=?",
                       (request.form.get("status", "closed"),
                        request.form.get("outcome", ""), request.form.get("id")))
            db.commit()
            flash("Review recorded.")
            return redirect(url_for("reviews"))
        rows = db.execute("SELECT * FROM overrides ORDER BY status, due_2w").fetchall()
        body = """
        <h1>Review queue</h1>
        <p class="sub">You reviewing your own decision cold, with interval data, is not the same
        decision as the one made in the moment.</p>
        <table><tr><th>Finding</th><th>Flag</th><th>Reason given</th><th>Due 2w</th>
        <th>Due 6w</th><th>Status</th><th></th></tr>
        {% for o in rows %}<tr><td>{{ o['finding_title'] }}</td>
        <td><span class="flag {{ o['flag'] }}">{{ o['flag'] }}</span></td>
        <td class="muted">{{ o['reason'] }}</td><td class="num">{{ o['due_2w'] }}</td>
        <td class="num">{{ o['due_6w'] }}</td><td>{{ o['status'] }}</td>
        <td>{% if o['status']=='open' %}
        <form method="post"><input type="hidden" name="id" value="{{ o['id'] }}">
        <input type="text" name="outcome" placeholder="what happened">
        <button class="ghost">Close</button></form>{% else %}{{ o['outcome'] }}{% endif %}</td>
        </tr>{% else %}<tr><td colspan="7" class="muted">Nothing in the queue.</td></tr>{% endfor %}
        </table>
        """
        return page(body, nav="reviews", title="Review queue", rows=rows)

    # ---------------------------------------------------- knowledge base
    @app.route("/knowledge")
    @login_required
    def knowledge():
        items = []
        for k, d in sorted(DRUGS.items()):
            items.append({"key": k, "name": display_name(k), "cls": d.get("class", ""),
                          "qt": d.get("qt", "none"), "reviewed": d.get("reviewed", ""),
                          "stale": is_stale(d.get("reviewed", "")),
                          "no_us": d.get("no_us_label", False),
                          "renal": bool(d.get("renal_critical"))})
        body = """
        <h1>Knowledge base</h1>
        <p class="sub">{{ items|length }} molecules · drugs {{ dv }} · rules {{ rv }} ·
        {{ pw }} named pairwise rules. Curated narrowly and deeply, not broadly and shallowly.</p>
        <div class="card"><strong>Coverage is the limit of this tool.</strong>
        <p class="muted" style="margin:6px 0 0">Any molecule not listed below gets no interaction,
        burden or dosing check at all. Add it to <span class="dose">knowledge/drugs.json</span>
        before relying on a result that involves it.</p></div>
        <table><tr><th>Drug</th><th>Class</th><th>QT</th><th>Flags</th><th>Reviewed</th></tr>
        {% for i in items %}<tr><td>{{ i.name }}</td><td class="muted">{{ i.cls }}</td>
        <td>{% if i.qt!='none' %}<span class="flag {{ 'RED' if i.qt=='known' else 'AMBER' }}">{{ i.qt }}</span>{% endif %}</td>
        <td class="muted">{% if i.no_us %}<span class="chip">no US label</span>{% endif %}
        {% if i.renal %}<span class="chip">renal dosing</span>{% endif %}</td>
        <td class="num">{{ i.reviewed }}{% if i.stale %} <span class="stale">STALE</span>{% endif %}</td>
        </tr>{% endfor %}</table>
        """
        return page(body, nav="kb", title="Knowledge base", items=items,
                    dv=DRUGS_DOC["_meta"]["version"], rv=RULES_DOC["_meta"]["version"],
                    pw=len(PAIRWISE))

    @app.route("/healthz")
    def healthz():
        return Response("ok %s" % APP_VERSION, mimetype="text/plain")

    return app


app = create_app() if os.environ.get("RXGUARD_EAGER") else None

if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=8031, debug=False)

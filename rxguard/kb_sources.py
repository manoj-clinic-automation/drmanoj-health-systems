#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_sources.py -- RxGuard v1.2.0: fetch and parse FREE, verifiable sources.

Sources (all public, no key, no licence fee):
  * NLM RxNorm / RxClass  -- name -> ingredient RxCUI, ATC class
  * openFDA drug label    -- FDA SPL sections, quoted verbatim
  * FDA table of CYP / transporter interactors (HCP page) -- strengths
  * DDInter 2.0 CSVs      -- academic DDI levels, CC BY-NC-SA 4.0
                             (personal non-commercial use, attribution)
  * PvPI (IPC, India)     -- drug-safety-alert index (links only in v1)

Deliberately NOT used in v1: EMA ePI (pilot covers few products), CDSCO
(no data feed), anything licensed.

Rules:
  * Only molecule names ever leave the server.
  * Nothing here decides anything. It builds a DRAFT with every property
    tied to the sentence (or table row) it came from; the owner approves.
  * A property the sources do not settle is left out and listed as a gap.
  * Every network call has a short timeout and never raises to the caller.
Python 3.9, standard library only.
"""

import csv
import io
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("RXGUARD_KB_CACHE", os.path.join(HERE, "knowledge", "cache"))

RXNAV = os.environ.get("RXGUARD_RXNAV", "https://rxnav.nlm.nih.gov")
OPENFDA = os.environ.get("RXGUARD_OPENFDA", "https://api.fda.gov")
FDA_CYP_URL = os.environ.get(
    "RXGUARD_FDA_CYP_URL",
    "https://www.fda.gov/drugs/drug-interactions-labeling/"
    "healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems")
DDINTER = os.environ.get("RXGUARD_DDINTER", "https://ddinter.scbdd.com")
DDINTER_CODES = "ABCDGHJLMNPRSV"
PVPI_URL = os.environ.get(
    "RXGUARD_PVPI_URL",
    "https://www.ipc.gov.in/mandates/pvpi/pvpi-outcome/8-category-en/416-drug-safety-alerts.html")

UA = "RxGuard/1.2 (personal medication safety; molecule-name lookups only)"
_OPENER = urllib.request.build_opener()   # honours system proxy on the server


def _get(url, timeout=12, binary=False):
    """(status, body) -- never raises. status 0 = network failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            data = r.read()
            return r.status, (data if binary else data.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, b"" if binary else ""
    except Exception:
        return 0, b"" if binary else ""


def _get_json(url, timeout=12):
    code, body = _get(url, timeout)
    if code != 200 or not body:
        return code, None
    try:
        return code, json.loads(body)
    except ValueError:
        return code, None


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


def _cache_fresh(path, max_age_days):
    try:
        return (time.time() - os.path.getmtime(path)) < max_age_days * 86400
    except OSError:
        return False


def norm(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


# ------------------------------------------------------------------ RxNorm
def rxnorm_identify(term):
    """Spelling-tolerant lookup. Returns
    {ok, rxcui, name, tty, atc:[(code,name)], err}. Ingredient-level."""
    out = {"ok": False, "rxcui": "", "name": "", "tty": "", "atc": [], "err": ""}
    q = urllib.parse.quote((term or "").strip())
    if not q:
        out["err"] = "empty"
        return out
    code, j = _get_json(RXNAV + "/REST/approximateTerm.json?term=" + q + "&maxEntries=5&option=1")
    if j is None:
        out["err"] = "RxNorm not reachable" if code == 0 else "RxNorm HTTP %d" % code
        return out
    cands = ((j.get("approximateGroup") or {}).get("candidate") or [])
    rxcui = ""
    for c in cands:
        if c.get("rxcui"):
            rxcui = c["rxcui"]
            break
    if not rxcui:
        out["err"] = "no RxNorm match"
        return out
    code, p = _get_json(RXNAV + "/REST/rxcui/" + rxcui + "/properties.json")
    props = (p or {}).get("properties") or {}
    tty = props.get("tty", "")
    name = props.get("name", "")
    if tty not in ("IN", "PIN", "MIN"):
        code, rel = _get_json(RXNAV + "/REST/rxcui/" + rxcui + "/related.json?tty=IN")
        for grp in (((rel or {}).get("relatedGroup") or {}).get("conceptGroup") or []):
            cps = grp.get("conceptProperties") or []
            if cps:
                rxcui, name, tty = cps[0].get("rxcui", rxcui), cps[0].get("name", name), "IN"
                break
    out.update(ok=True, rxcui=rxcui, name=name, tty=tty)
    code, cls = _get_json(RXNAV + "/REST/rxclass/class/byRxcui.json?rxcui=" + rxcui + "&relaSource=ATC")
    seen = set()
    for it in (((cls or {}).get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []):
        mc = it.get("rxclassMinConceptItem") or {}
        k = (mc.get("classId", ""), mc.get("className", ""))
        if k[0] and k not in seen:
            seen.add(k)
            out["atc"].append(k)
    return out


def rxnorm_suggest(term):
    """Spelling suggestions for the GutLog salt box."""
    q = urllib.parse.quote((term or "").strip())
    if len(q) < 3:
        return []
    code, j = _get_json(RXNAV + "/REST/spellingsuggestions.json?name=" + q, timeout=4)
    return (((j or {}).get("suggestionGroup") or {}).get("suggestionList") or {}).get("suggestion") or []


# ------------------------------------------------------------------ openFDA
LABEL_SECTIONS = ("boxed_warning", "contraindications", "warnings_and_cautions", "warnings",
                  "precautions", "drug_interactions", "clinical_pharmacology",
                  "pharmacokinetics", "use_in_specific_populations", "adverse_reactions")


def openfda_label(ingredient):
    """Most recent FDA label for an ingredient. {ok, set_id, effective_time,
    brand, sections:{name:text}, url, err}."""
    out = {"ok": False, "set_id": "", "effective_time": "", "brand": "", "sections": {},
           "url": "", "err": ""}
    name = (ingredient or "").strip().lower()
    if not name:
        out["err"] = "empty"
        return out
    for field in ("openfda.generic_name", "openfda.substance_name"):
        q = urllib.parse.quote('%s:"%s"' % (field, name))
        url = OPENFDA + "/drug/label.json?search=" + q + "&limit=10"
        code, j = _get_json(url)
        if code == 0:
            out["err"] = "openFDA not reachable"
            return out
        res = (j or {}).get("results") or []
        if not res:
            continue
        # single-ingredient labels first, then the newest
        def key(r):
            gn = " ".join((r.get("openfda") or {}).get("generic_name") or []).lower()
            single = (" and " not in gn) and ("," not in gn)
            return (single, r.get("effective_time", ""))
        best = sorted(res, key=key, reverse=True)[0]
        out.update(ok=True, set_id=best.get("set_id", ""), effective_time=best.get("effective_time", ""),
                   brand=", ".join(((best.get("openfda") or {}).get("brand_name") or [])[:2]),
                   url="https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=" + best.get("set_id", ""))
        for s in LABEL_SECTIONS:
            v = best.get(s)
            if isinstance(v, list):
                v = " ".join(x for x in v if isinstance(x, str))
            if isinstance(v, str) and v.strip():
                out["sections"][s] = re.sub(r"\s+", " ", v).strip()
        return out
    out["err"] = "no US label (FDA) for this molecule"
    return out


def sentences(text):
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(\d])", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 15]


def find_sentence(text, pattern, flags=re.I):
    rx = re.compile(pattern, flags)
    for s in sentences(text):
        if rx.search(s):
            return s[:420]
    return ""


# ------------------------------------------------------------------ FDA CYP table
class _TableParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.tables, self._t, self._r, self._c, self._in = [], None, None, None, False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._r = []
        elif tag in ("td", "th") and self._r is not None:
            self._c, self._in = [], True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in:
            self._r.append(re.sub(r"\s+", " ", "".join(self._c).replace("\xa0", " ")).strip())
            self._in = False
        elif tag == "tr" and self._r is not None and self._t is not None:
            self._t.append(self._r)
            self._r = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t = None

    def handle_data(self, data):
        if self._in:
            self._c.append(data)


_ENZ = re.compile(r"(2C19|2C8|2C9|2D6|2B6|1A2|3A)")
_TR = re.compile(r"(P-gp|BCRP|OATP1B1|OATP1B3|OAT1|OAT3|OCT2|MATE1|MATE2-K)")
_COLS = [("inhibitor", "strong"), ("inhibitor", "moderate"), ("inhibitor", "weak"),
         ("inducer", "strong"), ("inducer", "moderate"), ("inducer", "weak"),
         ("substrate", "major"), ("substrate", "moderate"),
         ("t_inhibitor", ""), ("t_substrate", "")]


def _clean_name(n):
    n = re.sub(r"[\d,\s]+$", "", n.replace("\xa0", " ")).strip()
    return n.lower()


def parse_fda_cyp(html):
    """{name: {"inhibitor":{CYP3A4:strong}, "inducer":{}, "substrate":{},
    "row": [...]} } from the FDA HCP page. Empty dict if the layout changed."""
    p = _TableParser()
    try:
        p.feed(html)
    except Exception:
        return {}
    out = {}
    for t in p.tables:
        if not t or len(t[0]) < 11 or "drug or other substance" not in t[0][0].lower():
            continue
        for row in t[1:]:
            if len(row) < 11 or not row[0]:
                continue
            name = _clean_name(row[0])
            d = out.setdefault(name, {"inhibitor": {}, "inducer": {}, "substrate": {}, "row": []})
            d["row"].append(" | ".join(c for c in row if c))
            for (kind, level), cell in zip(_COLS, row[1:11]):
                if not cell:
                    continue
                if kind in ("t_inhibitor", "t_substrate"):
                    for tr in _TR.findall(cell):
                        tgt = "inhibitor" if kind == "t_inhibitor" else "substrate"
                        d[tgt].setdefault(tr, "moderate")
                    continue
                for e in _ENZ.findall(cell):
                    enz = "CYP3A4" if e == "3A" else "CYP" + e
                    d[kind][enz] = level
    return out


def fda_cyp_table(max_age_days=30):
    """(table, meta). Cached; refreshed monthly. meta: {ok, fetched, rows, err}."""
    path = _cache_path("fda_cyp.json")
    if _cache_fresh(path, max_age_days):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            return doc["table"], doc["meta"]
        except (OSError, ValueError, KeyError):
            pass
    code, html = _get(FDA_CYP_URL, timeout=20)
    table = parse_fda_cyp(html) if code == 200 else {}
    meta = {"ok": len(table) >= 100, "fetched": time.strftime("%Y-%m-%d"), "rows": len(table),
            "url": FDA_CYP_URL, "err": "" if table else ("FDA page not reachable" if code == 0
                                                         else "FDA page layout not recognised")}
    if meta["ok"]:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"table": table, "meta": meta}, fh)
        return table, meta
    try:                                       # keep using the last good copy
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["meta"]["err"] = meta["err"] + " (using copy of " + doc["meta"]["fetched"] + ")"
        return doc["table"], doc["meta"]
    except (OSError, ValueError, KeyError):
        return {}, meta


# ------------------------------------------------------------------ DDInter
def ddinter_db(max_age_days=30):
    """(sqlite path, meta). Downloads the 14 ATC-group CSVs into one indexed
    table, monthly. Pair order is normalised (a < b)."""
    path = _cache_path("ddinter.db")
    meta_path = _cache_path("ddinter.meta.json")
    if _cache_fresh(path, max_age_days) and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            return path, json.load(fh)
    rows, failed = {}, []
    for c in DDINTER_CODES:
        code, body = _get(DDINTER + "/static/media/download/ddinter_downloads_code_" + c + ".csv",
                          timeout=60)
        if code != 200 or not body:
            failed.append(c)
            continue
        for r in csv.DictReader(io.StringIO(body)):
            a, b, lv = norm(r.get("Drug_A")), norm(r.get("Drug_B")), (r.get("Level") or "").strip()
            if not a or not b or lv not in ("Major", "Moderate", "Minor"):
                continue
            k = (a, b) if a < b else (b, a)
            rows[k] = lv
    meta = {"ok": len(rows) > 1000 and not failed, "pairs": len(rows), "failed": failed,
            "fetched": time.strftime("%Y-%m-%d"),
            "licence": "DDInter 2.0, CC BY-NC-SA 4.0 -- personal non-commercial use, attribution"}
    if rows:
        tmp = path + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        con = sqlite3.connect(tmp)
        con.execute("CREATE TABLE ddi (a TEXT, b TEXT, level TEXT, PRIMARY KEY (a, b))")
        con.executemany("INSERT INTO ddi VALUES (?,?,?)", [(k[0], k[1], v) for k, v in rows.items()])
        con.commit()
        con.close()
        os.replace(tmp, path)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        return path, meta
    if os.path.exists(path) and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            old = json.load(fh)
        old["err"] = "DDInter not reachable (using copy of " + old.get("fetched", "?") + ")"
        return path, old
    meta["err"] = "DDInter not reachable"
    return "", meta


def ddinter_names(key):
    """DDInter spells some molecules differently (acetaminophen, qualifiers)."""
    alias = {"paracetamol": ["acetaminophen"], "polyethylene_glycol": ["polyethylene_glycol_(3350)"]}
    return [key] + alias.get(key, [])


def ddinter_level(db_path, a, b):
    if not db_path:
        return ""
    con = sqlite3.connect(db_path)
    try:
        best = ""
        rank = {"Major": 3, "Moderate": 2, "Minor": 1, "": 0}
        for x in ddinter_names(a):
            for y in ddinter_names(b):
                k = (x, y) if x < y else (y, x)
                r = con.execute("SELECT level FROM ddi WHERE a=? AND b=?", k).fetchone()
                if r and rank[r[0]] > rank[best]:
                    best = r[0]
        return best
    finally:
        con.close()


# ------------------------------------------------------------------ PvPI
def pvpi_alerts():
    """[(title, absolute_url)] of monthly alert pages/PDFs linked from the
    PvPI index. Links only: the alerts are PDFs, read by the owner."""
    code, html = _get(PVPI_URL, timeout=20)
    if code != 200:
        return [], ("PvPI not reachable" if code == 0 else "PvPI HTTP %d" % code)
    out = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        t = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        if re.search(r"alert", t, re.I) and re.search(r"20\d\d", t):
            out.append((t, urllib.parse.urljoin(PVPI_URL, href)))
    return out, ""


# ------------------------------------------------------------------ draft builder
def _label_props(sec):
    """Deterministic extraction from label wording. Each item:
    (field, value, quote, section)."""
    items = []
    boxed = sec.get("boxed_warning", "")
    warn = " ".join(sec.get(k, "") for k in ("warnings_and_cautions", "warnings", "precautions",
                                               "contraindications"))
    qt_b = find_sentence(boxed, r"QTc?\b.{0,40}prolong|torsade")
    qt_w = find_sentence(warn, r"QTc?\b.{0,40}prolong|torsade")
    if qt_b:
        items.append(("qt", "known", qt_b, "boxed_warning"))
    elif qt_w:
        items.append(("qt", "possible", qt_w, "warnings"))
    checks = [
        ("burden.sedation", 2, boxed + " " + warn, r"CNS depress|respiratory depression"),
        ("burden.sedation", 1, warn, r"somnolence|drowsiness|sedation|impair\w* .{0,30}(alertness|driving|mental)"),
        ("burden.serotonergic", 2, boxed + " " + warn, r"serotonin syndrome"),
        ("burden.anticholinergic", 1, warn, r"anticholinergic"),
        ("burden.bleeding", 1, boxed + " " + warn, r"\bbleeding\b|haemorrhage|hemorrhage"),
        ("burden.nephrotoxic", 1, warn, r"nephrotox|acute kidney injury|renal (failure|toxicity)"),
        ("burden.seizure", 1, warn, r"seizure"),
        ("burden.constipating", 1, warn, r"constipation"),
        ("hr", "decrease", warn, r"bradycardia"),
        ("bp", "decrease", warn, r"hypotension"),
    ]
    done = set()
    for field, val, text, pat in checks:
        if field in done:
            continue
        q = find_sentence(text, pat)
        if q:
            items.append((field, val, q, "warnings"))
            done.add(field)
    pop = sec.get("use_in_specific_populations", "") + " " + sec.get("clinical_pharmacology", "")
    r = find_sentence(pop, r"renal impairment")
    if r:
        items.append(("renal", r, r, "use_in_specific_populations"))
    h = find_sentence(pop, r"hepatic impairment")
    if h:
        items.append(("hepatic", h, h, "use_in_specific_populations"))
    w = find_sentence(boxed + " " + warn, r"withdrawal|abrupt discontinuation")
    if w:
        items.append(("withdrawal_note", w, w, "warnings"))
    return items


_CYP = r"CYP\s?(1A2|2B6|2C8|2C9|2C19|2D6|2E1|3A4|3A5|3A)\b"


def _negated(text, start):
    """'is not a substrate of', 'not metabolized by', 'no ... inhibitor of' just before."""
    return bool(re.search(r"\b(not|no|neither|nor|without)\b[^.;]{0,25}$", text[max(0, start - 40):start], re.I))


def _label_cyp(sec):
    """CYP roles the label states in words (supplements the FDA table).
    Real enzyme codes only; negated statements are skipped."""
    text = sec.get("clinical_pharmacology", "") + " " + sec.get("pharmacokinetics", "") + " " + \
        sec.get("drug_interactions", "")
    out = []
    fix = lambda e: "CYP" + ("3A4" if e.upper() == "3A" else e.upper())
    for m in re.finditer(r"(strong|moderate|weak)\s+inhibitor\s+of\s+" + _CYP, text, re.I):
        if _negated(text, m.start()):
            continue
        out.append(("inhibitor", fix(m.group(2)), m.group(1).lower(), find_sentence(text, re.escape(m.group(0)))))
    for m in re.finditer(r"(?:substrate\s+of|metabolized\s+(?:primarily\s+|mainly\s+|predominantly\s+)?by)\s+" + _CYP,
                         text, re.I):
        if _negated(text, m.start()):
            continue
        out.append(("substrate", fix(m.group(1)), "moderate", find_sentence(text, re.escape(m.group(0)))))
    return out


SEVERE = r"contraindicat|do not (co-?administer|use)|avoid (concomitant|co-?administration|use)"


def build_draft(term, strength, current, cyp_table, ddi_path, label_cache=None):
    """Draft knowledge entry for one molecule plus interaction candidates
    against `current` [(key, display)]. Returns a dict, never raises."""
    label_cache = label_cache if label_cache is not None else {}
    ident = rxnorm_identify(term)
    ingredient = (ident.get("name") or term).lower() if ident.get("ok") else term.lower()
    key = norm(ingredient)
    lab = openfda_label(ingredient)
    label_cache[key] = lab
    props, gaps, sources = [], [], []
    if ident.get("ok"):
        sources.append({"name": "NLM RxNorm", "detail": "RxCUI %s (%s)" % (ident["rxcui"], ident["name"]),
                        "url": "https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm=" + ident["rxcui"]})
        if ident["atc"]:
            code, cname = sorted(ident["atc"], key=lambda x: -len(x[0]))[0]
            props.append({"field": "class", "value": cname.lower(), "quote": "ATC %s %s" % (code, cname),
                          "source": "NLM RxClass (ATC)"})
            props.append({"field": "atc", "value": code, "quote": "ATC %s" % code, "source": "NLM RxClass (ATC)"})
        else:
            gaps.append("class (no ATC class in RxClass)")
    else:
        gaps.append("identity (" + ident.get("err", "no RxNorm match") + ")")
    if lab.get("ok"):
        sources.append({"name": "FDA label (openFDA / DailyMed)",
                        "detail": "%s, effective %s" % (lab["brand"] or ingredient, lab["effective_time"]),
                        "url": lab["url"]})
        for field, val, quote, section in _label_props(lab["sections"]):
            props.append({"field": field, "value": val, "quote": quote,
                          "source": "FDA label - " + section.replace("_", " ")})
        for kind, enz, lvl, quote in _label_cyp(lab["sections"]):
            props.append({"field": "cyp.%s.%s" % (kind, enz), "value": lvl, "quote": quote,
                          "source": "FDA label - clinical pharmacology"})
        for f in ("qt",):
            if not any(p["field"] == f for p in props):
                gaps.append("QT (label silent - not proof of none)")
    else:
        gaps.append("label facts (" + lab.get("err", "") + ")")
    row = (cyp_table or {}).get(ingredient) or (cyp_table or {}).get(ingredient.replace("_", " "))
    if row:
        for kind in ("inhibitor", "inducer", "substrate"):
            for enz, lvl in row[kind].items():
                props.append({"field": "cyp.%s.%s" % (kind, enz), "value": lvl,
                              "quote": row["row"][0][:300], "source": "FDA CYP/transporter table"})
        sources.append({"name": "FDA CYP & transporter table", "detail": "listed", "url": FDA_CYP_URL})
    elif cyp_table:
        gaps.append("CYP role (not in the FDA example table)")
    # de-duplicate: FDA table beats label wording for the same CYP field
    seen, uniq = set(), []
    for p in sorted(props, key=lambda p: 0 if p["source"].startswith("FDA CYP") else 1):
        if p["field"] in seen:
            continue
        seen.add(p["field"])
        uniq.append(p)
    props = uniq

    pairs = []
    di = lab.get("sections", {}).get("drug_interactions", "") if lab.get("ok") else ""
    for okey, odisp in current:
        if okey == key:
            continue
        names = [odisp.lower(), okey.replace("_", " ")]
        pat = r"\b(" + "|".join(re.escape(n) for n in set(names) if n) + r")\b"
        q = find_sentence(di, pat)
        if q:
            pairs.append({"a": key, "b": okey, "flag": "RED" if re.search(SEVERE, q, re.I) else "AMBER",
                          "quote": q, "source": "FDA label of %s - drug interactions" % ingredient,
                          "kind": "label"})
        other = label_cache.get(okey)
        if other is None:
            other = openfda_label(okey.replace("_", " "))
            label_cache[okey] = other
        odi = other.get("sections", {}).get("drug_interactions", "") if other.get("ok") else ""
        q2 = find_sentence(odi, r"\b" + re.escape(ingredient.replace("_", " ")) + r"\b")
        if q2:
            pairs.append({"a": key, "b": okey, "flag": "RED" if re.search(SEVERE, q2, re.I) else "AMBER",
                          "quote": q2, "source": "FDA label of %s - drug interactions" % odisp,
                          "kind": "label"})
        lv = ddinter_level(ddi_path, key, okey)
        if lv in ("Major", "Moderate"):
            pairs.append({"a": key, "b": okey, "flag": "RED" if lv == "Major" else "AMBER",
                          "quote": "DDInter 2.0 risk level: %s" % lv,
                          "source": "DDInter 2.0 (academic; CC BY-NC-SA 4.0)", "kind": "ddinter"})
    return {"term": term, "key": key, "display": ingredient, "strength": strength or "",
            "identity": ident, "props": props, "gaps": gaps, "pairs": pairs, "sources": sources,
            "label_effective": lab.get("effective_time", ""), "label_set_id": lab.get("set_id", "")}


def pair_candidates(keys, ddi_path, known_pairs):
    """DDInter pairs among molecules already in the knowledge base (both
    known), skipping pairs a curated or approved rule already covers."""
    out = []
    ks = sorted(set(keys))
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            if frozenset((a, b)) in known_pairs:
                continue
            lv = ddinter_level(ddi_path, a, b)
            if lv in ("Major", "Moderate"):
                out.append({"a": a, "b": b, "flag": "RED" if lv == "Major" else "AMBER",
                            "quote": "DDInter 2.0 risk level: %s" % lv,
                            "source": "DDInter 2.0 (academic; CC BY-NC-SA 4.0)", "kind": "ddinter"})
    return out

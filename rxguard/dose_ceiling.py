#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dose_ceiling.py -- RxGuard v1.8.0, RXGUARD_V180_DOSE

CUMULATIVE DAILY DOSE PER INGREDIENT, not duplicate detection.

The owner's words (19-Sep-2026): look at the total dose and not duplicacy
only. A molecule hidden inside a fixed-dose combination counts into the same
pool as the standalone product; one generic taken by two routes is one pool;
and some classes carry a load of their own. So every dose GutLog logged is
broken into its ingredients (amount per unit x units taken), summed over a
rolling window, and held against a ceiling.

Deterministic, no network, no Flask, no database -- app.py hands it the feed
and the rules and renders what comes back. Every finding names its rule:

  DC001  RED     an ingredient is over its ceiling in the current window
  DC002  AMBER   an ingredient went over its ceiling in the last 3 days
  DC003  AMBER   an ingredient with no ceiling set was taken (set one)
  DC004  AMBER   more units of a product-limited ingredient than allowed
                 (e.g. patches: count, not milligrams)
  DC005  AMBER   more distinct members of a class than the class allows
  DC006  AMBER   an addition on top of a class's regular medicine
         RED     two or more additions
  DC007  AMBER   two members of a "not together" class in the same window
  DC008  UNKNOWN the same product logged twice within 10 minutes -- counted,
                 because the safer claim is the higher total, but named
  DC009  UNKNOWN a dose of a ruled ingredient whose amount cannot be read

The rules file names the owner's medicines, so it is knowledge/
dose_rules.local.json -- gitignored, on the server only (CLAUDE.md 5d).
This file and its test name no medicine. Python 3.9.
"""
import json
import re
from datetime import datetime, timedelta

RULES_FILE = "dose_rules.local.json"
HISTORY_DAYS = 3
DOUBLE_ENTRY_MIN = 10
UNIT_TO_MG = {"mg": 1.0, "mcg": 0.001, "ug": 0.001, "µg": 0.001, "g": 1000.0}
_NUM = r"(\d+(?:\.\d+)?|\.\d+)"


# ------------------------------------------------------------------ rules
def load_rules(path, norm):
    """(rules, error). A missing file is not an error -- the feature is simply
    not set up on this server -- so it returns (None, "")."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, ""
    except Exception as e:
        return None, "Dose rules could not be read (%s)." % type(e).__name__
    return normalise_rules(doc, norm), ""


def normalise_rules(doc, norm):
    ings = {}
    for k, r in (doc.get("ingredients") or {}).items():
        r = dict(r)
        r["unit"] = (r.get("unit") or "mg").lower()
        r["window_h"] = float(r.get("window_h") or 24)
        c = r.get("ceiling")
        r["ceiling"] = float(c) if c not in (None, "") else None
        mu = r.get("max_units")
        r["max_units"] = float(mu) if mu not in (None, "") else None
        r["confirmed"] = r.get("confirmed") or ""
        r["label"] = r.get("label") or k
        ings[norm(k)] = r
    classes = []
    for c in doc.get("classes") or []:
        c = dict(c)
        c["members"] = [norm(m) for m in c.get("members") or []]
        c["regular"] = norm(c["regular"]) if c.get("regular") else ""
        c["window_h"] = float(c.get("window_h") or 24)
        c["routes"] = [r.lower() for r in c.get("routes") or []]
        classes.append(c)
    products = {}
    for name, p in (doc.get("products") or {}).items():
        products[name.strip().lower()] = p
    return {"ingredients": ings, "classes": classes, "products": products,
            "meta": doc.get("_meta") or {}}


def apply_overrides(rules, overrides):
    """overrides: {ingredient key: {"ceiling": float|None, "confirmed": str}}
    typed on the page by the owner. The page always wins over the file."""
    for k, o in (overrides or {}).items():
        r = rules["ingredients"].get(k)
        if not r:
            continue
        if "ceiling" in o:
            r["ceiling"] = o["ceiling"]
        if o.get("confirmed"):
            r["confirmed"] = o["confirmed"]
    return rules


# ------------------------------------------------------------------ parsing
_STRENGTH_UNITS = ("mg", "mcg", "µg", "ug", "g", "iu", "%")


def dose_units(dose_text):
    """Units per dose from GutLog's free text -- the same reading as GutLog's
    _units(): '1 tab' -> 1, '2 caps' -> 2, '1/2' -> 0.5, a strength -> 1."""
    s = (dose_text or "").strip().lower().replace("½", "0.5")
    if not s:
        return 1.0
    parts = s.split()
    if len(parts) > 1 and parts[1] in _STRENGTH_UNITS:
        return 1.0
    tok = parts[0]
    try:
        if "/" in tok:
            a, b = tok.split("/", 1)
            v = float(a) / float(b)
        else:
            v = float(tok)
    except (ValueError, ZeroDivisionError):
        return 1.0
    return v if 0 < v <= 20 else 1.0


def route_of(name, strength):
    t = ("%s %s" % (name or "", strength or "")).lower()
    if "patch" in t or "gel" in t or "topical" in t:
        return "topical"
    if "nasal" in t or "spray" in t:
        return "nasal"
    if "inj" in t:
        return "injection"
    return "oral"


def _amount(piece):
    """'60 mg' -> (60, 'mg'); '145' -> (145, ''); '50 mg/mL' -> None (a
    concentration is not an amount per unit); '' -> None."""
    p = (piece or "").strip().lower()
    if not p or "%" in p or re.search(r"/\s*[\d.]*\s*(ml|l|g)\b", p):
        return None
    m = re.search(_NUM + r"\s*(mg|mcg|µg|ug|g)?\b", p)
    if not m:
        return None
    return float(m.group(1)), (m.group(2) or "")


def _name_amount(name):
    """The last number in a product name: 'Test Alpha 500' -> 500."""
    nums = re.findall(_NUM + r"\s*(mg|mcg|µg|ug|g)?", name or "")
    if not nums:
        return None
    v, u = nums[-1]
    return float(v), (u or "")


def to_rule_unit(value, unit, rule_unit):
    """A bare number is taken in the ingredient's own rule unit."""
    if not unit:
        return value
    a = UNIT_TO_MG.get(unit)
    b = UNIT_TO_MG.get(rule_unit)
    if a is None or b is None:
        return None
    return value * a / b


def composition(med, rules, norm):
    """[(ingredient key, amount per unit in the rule unit or None)] for one
    GutLog medicine {name, molecule, strength}. The rules file may override a
    product whose label cannot be read (a spray per actuation)."""
    name = med.get("name") or ""
    over = rules["products"].get(name.strip().lower())
    if over:
        out = []
        for k, v in (over.get("per_unit") or {}).items():
            out.append((norm(k), float(v) if v not in (None, "") else None))
        return out
    mols = [m.strip() for m in (med.get("molecule") or "").replace(",", "+").split("+") if m.strip()]
    if not mols:
        return []
    pieces = [p for p in (med.get("strength") or "").split("+")]
    out = []
    for i, m in enumerate(mols):
        k = norm(m)
        rule = rules["ingredients"].get(k)
        ru = rule["unit"] if rule else "mg"
        amt = _amount(pieces[i]) if i < len(pieces) else None
        if amt is None and len(mols) == 1 and not "".join(pieces).strip():
            # no strength recorded at all: 'Test Alpha 500' carries it in
            # the name. A strength that IS recorded but unreadable (a spray
            # in mg/mL) is never second-guessed from the name.
            amt = _name_amount(name)
        out.append((k, to_rule_unit(amt[0], amt[1], ru) if amt else None))
    return out


def _variant_amount(dose_text, rule_unit):
    """A medicine whose strengths vary logs the strengths picked: '145 + 72'
    is 217 of the ingredient, in its rule unit."""
    total, seen = 0.0, False
    for piece in (dose_text or "").split("+"):
        a = _amount(piece)
        if a:
            v = to_rule_unit(a[0], a[1], rule_unit)
            if v is not None:
                total += v
                seen = True
    return total if seen else None


def _when(day, tm):
    try:
        return datetime.strptime("%s %s" % (day, (tm or "12:00")[:5]), "%Y-%m-%d %H:%M"), bool(tm)
    except ValueError:
        return None, False


# ------------------------------------------------------------------ intakes
def build_intakes(events, stack, rules, norm):
    """Every logged dose, split into ingredient intakes.

    events -- GutLog /api/feed/doses events (day, time, name, molecule,
              med_id, dose_text). stack -- /api/feed/stack, which carries the
              strength per medicine and which medicines have variants.
    Returns (intakes, unreadable). Only ingredients the rules know about are
    kept: the rest have no ceiling to be held against."""
    strength, variants = {}, set()
    for r in (stack or {}).get("regimen") or []:
        if r.get("med_id") is not None:
            strength[r["med_id"]] = r.get("strength") or ""
            if (r.get("variants") or "").strip():
                variants.add(r["med_id"])
    for t in (stack or {}).get("taken") or []:
        if t.get("med_id") is not None and t.get("strength"):
            strength.setdefault(t["med_id"], t["strength"])
    known = rules["ingredients"]
    intakes, unreadable = [], []
    for e in events or []:
        when, timed = _when(e.get("day"), e.get("time"))
        if when is None:
            continue
        med = {"name": e.get("name") or "", "molecule": e.get("molecule") or "",
               "strength": strength.get(e.get("med_id"), "")}
        route = route_of(med["name"], med["strength"])
        comp = composition(med, rules, norm)
        units = dose_units(e.get("dose_text"))
        for k, per in comp:
            if k not in known:
                continue
            if e.get("med_id") in variants:
                amt = _variant_amount(e.get("dose_text"), known[k]["unit"])
                if amt is None and per is not None:
                    amt = per * units
                u = 1.0
            else:
                amt = per * units if per is not None else None
                u = units
            it = {"ing": k, "when": when, "timed": timed, "amount": amt, "units": u,
                  "product": med["name"], "route": route, "day": e.get("day")}
            if amt is None:
                unreadable.append(it)
            intakes.append(it)
    intakes.sort(key=lambda x: x["when"])
    return intakes, unreadable


# ------------------------------------------------------------------ arithmetic
def window(intakes, end, hours, ing=None):
    start = end - timedelta(hours=hours)
    return [i for i in intakes if start < i["when"] <= end and (ing is None or i["ing"] == ing)]


def total(items):
    return sum(i["amount"] or 0.0 for i in items)


def frees_at(items, ceiling, hours):
    """When the rolling total falls back to the ceiling: drop the oldest
    doses one by one; the moment the next one leaves the window."""
    rest = sorted(items, key=lambda x: x["when"])
    t = total(rest)
    while rest and t > ceiling + 1e-9:
        first = rest.pop(0)
        t -= first["amount"] or 0.0
        if t <= ceiling + 1e-9:
            return first["when"] + timedelta(hours=hours)
    return None


def peak(intakes, ing, hours, since):
    """Highest rolling total ending at any dose on or after `since`."""
    best, at = 0.0, None
    for i in intakes:
        if i["ing"] != ing or i["when"] < since:
            continue
        t = total(window(intakes, i["when"], hours, ing))
        if t > best + 1e-9:
            best, at = t, i["when"]
    return best, at


def _fmt(v):
    return ("%.3f" % v).rstrip("0").rstrip(".") if v is not None else "?"


def _hm(dt, now):
    if dt is None:
        return ""
    if dt.date() == now.date():
        return dt.strftime("%H:%M today")
    if dt.date() == (now + timedelta(days=1)).date():
        return dt.strftime("%H:%M tomorrow")
    return dt.strftime("%H:%M on %d %b")


def _doses_text(items, unit=""):
    return "; ".join("%s%s at %s" % (i["product"],
                                     (" (%s %s)" % (_fmt(i["amount"]), unit)).rstrip() if unit else "",
                                     i["when"].strftime("%d %b %H:%M")) for i in items)


def _double_entries(intakes, now, hours):
    out, seen = [], set()
    items = [i for i in intakes if now - timedelta(hours=hours) < i["when"] <= now]
    for a in items:
        for b in items:
            if a is b or a["product"] != b["product"] or not (a["when"] <= b["when"]):
                continue
            gap = (b["when"] - a["when"]).total_seconds() / 60.0
            key = (a["product"], a["when"], b["when"])
            if 0 <= gap <= DOUBLE_ENTRY_MIN and key not in seen and a["ing"] == b["ing"]:
                seen.add(key)
                out.append((a, b, int(round(gap))))
    return out


# ------------------------------------------------------------------ evaluate
def evaluate(intakes, unreadable, rules, now, finding, display=None):
    """(rows, findings). rows are the per-ingredient table; findings are made
    with RxGuard's own finding() so they render and count like every other."""
    display = display or (lambda k: rules["ingredients"].get(k, {}).get("label", k))
    ings = rules["ingredients"]
    today = now.date().isoformat()
    rows, out = [], []
    src = "Dose ceilings, %s." % (rules["meta"].get("version") or "local rules")

    def unconfirmed(r):
        return "" if r.get("confirmed") else (
            " This ceiling is a default, not yet confirmed by you -- confirm or change it on "
            "the Daily dose page.")

    doubles = _double_entries(intakes, now, 24 * HISTORY_DAYS)
    doubled = set(a["ing"] for a, _b, _g in doubles)

    for k in sorted(ings, key=lambda x: display(x)):
        r = ings[k]
        if r.get("class_only"):
            continue
        h = r["window_h"]
        cur = window(intakes, now, h, k)
        rec = [i for i in intakes if i["ing"] == k and i["when"] > now - timedelta(days=HISTORY_DAYS)]
        if not cur and not rec:
            continue
        t = total(cur)
        unit = r["unit"]
        row = {"key": k, "name": display(k), "unit": unit, "total": t, "ceiling": r["ceiling"],
               "window_h": h, "doses": len(cur), "confirmed": r.get("confirmed", ""),
               "room": None, "frees": "", "state": "", "products": sorted(set(i["product"] for i in cur)),
               "routes": sorted(set(i["route"] for i in cur)), "units": sum(i["units"] for i in cur),
               "max_units": r["max_units"]}
        if r["ceiling"] is not None:
            row["room"] = r["ceiling"] - t
            if t > r["ceiling"] + 1e-9:
                row["state"] = "over"
                fr = frees_at(cur, r["ceiling"], h)
                row["frees"] = _hm(fr, now)
                out.append(finding(
                    "RED", "Daily dose",
                    "%s: %s %s in the last %d hours, above your ceiling of %s %s"
                    % (display(k), _fmt(t), unit, h, _fmt(r["ceiling"]), unit),
                    mechanism="Counted from every product carrying it: %s." % _doses_text(cur, unit),
                    consequence=r.get("consequence") or
                    "Dose-dependent adverse effects rise above this ceiling.",
                    action="No more %s until %s, when the total falls back to the ceiling.%s"
                           % (display(k), row["frees"] or "the window clears", unconfirmed(r)),
                    source=src, reviewed=r.get("confirmed") or "", rule_id="DC001"))
            elif abs(t - r["ceiling"]) < 1e-9 and cur:
                row["state"] = "at"
                row["frees"] = _hm(cur[0]["when"] + timedelta(hours=h), now)
            elif cur:
                row["state"] = "under"
            if row["state"] != "over" and rec:
                p, at = peak(intakes, k, h, now - timedelta(days=HISTORY_DAYS))
                if p > r["ceiling"] + 1e-9:
                    row["peak"] = p
                    out.append(finding(
                        "AMBER", "Daily dose",
                        "%s reached %s %s in %d hours on %s (ceiling %s)"
                        % (display(k), _fmt(p), unit, h, at.strftime("%d %b"), _fmt(r["ceiling"])),
                        mechanism="Doses in that window: %s."
                                  % _doses_text(window(intakes, at, h, k), unit),
                        consequence="Not over the ceiling now; shown for %d days after it happened."
                                    % HISTORY_DAYS,
                        action=(("If one dose was logged twice, remove the extra entry in "
                                 "GutLog and this clears." if k in doubled else
                                 "Nothing to do now; this is the record of it.")
                                + unconfirmed(r)),
                        source=src, reviewed=r.get("confirmed") or "", rule_id="DC002"))
        elif cur:
            row["state"] = "noceiling"
            out.append(finding(
                "AMBER", "Daily dose",
                "%s taken, and no personal ceiling is set" % display(k),
                mechanism="%s %s in the last %d hours: %s." % (_fmt(t), unit, h, _doses_text(cur, unit)),
                consequence=r.get("consequence") or "Nothing to hold the total against.",
                action="Set a ceiling for %s on the Daily dose page." % display(k),
                source=src, rule_id="DC003"))
        if r["max_units"] is not None and cur and row["units"] > r["max_units"] + 1e-9:
            out.append(finding(
                "AMBER", "Daily dose",
                "%s: %s units in %d hours, limit %s"
                % (display(k), _fmt(row["units"]), h, _fmt(r["max_units"])),
                mechanism="Counted by unit (%s): %s." % (", ".join(row["products"]), _doses_text(cur)),
                consequence=r.get("consequence") or "More units than the limit you set.",
                action="Check nothing was applied twice." + unconfirmed(r),
                source=src, rule_id="DC004"))
        rows.append(row)

    for c in rules["classes"]:
        h = c["window_h"]
        cur = [i for i in window(intakes, now, h) if i["ing"] in c["members"]
               and (not c["routes"] or i["route"] in c["routes"])]
        if not cur:
            continue
        kind = c.get("type")
        members = sorted(set(i["ing"] for i in cur))
        names = ", ".join(display(m) for m in members)
        cid = c.get("id") or ""
        if kind == "max_distinct" and len(members) > int(c.get("limit") or 1):
            out.append(finding(
                c.get("flag") or "AMBER", "Class load",
                "%s: %d different agents in %d hours" % (c.get("label") or cid, len(members), h),
                mechanism="Taken: %s. %s" % (names, _doses_text(cur)),
                consequence=c.get("consequence") or "",
                action=c.get("action") or "", source=src, rule_id="DC005" + (":" + cid if cid else "")))
        elif kind == "addition_to_regular":
            added = [i for i in cur if i["ing"] != c["regular"]]
            regular_in = any(i["ing"] == c["regular"] for i in cur)
            if added and not regular_in and len(set(i["ing"] for i in added)) >= 2:
                out.append(finding(
                    c.get("flag") or "AMBER", "Class load",
                    "%s: %s in the same %d hours" % (c.get("label") or cid, names, h),
                    mechanism=_doses_text(added) + ".", consequence=c.get("consequence") or "",
                    action=c.get("action") or "", source=src,
                    rule_id="DC006" + (":" + cid if cid else "")))
            elif added and regular_in:
                agents = sorted(set(i["ing"] for i in added))
                n = len(agents)
                out.append(finding(
                    "RED" if n >= 2 else "AMBER", "Class load",
                    "%s: %d agent%s added on top of %s in %d hours"
                    % (c.get("label") or cid, n, "" if n == 1 else "s", display(c["regular"]), h),
                    mechanism="Added: %s. Doses: %s." % (", ".join(display(a) for a in agents),
                                                        _doses_text(added)),
                    consequence=c.get("consequence") or "",
                    action=c.get("action") or "", source=src,
                    rule_id="DC006" + (":" + cid if cid else "")))
        elif kind == "together" and len(members) >= 2:
            out.append(finding(
                c.get("flag") or "AMBER", "Class load",
                "%s: %s in the same %d hours" % (c.get("label") or cid, names, h),
                mechanism=_doses_text(cur) + ".", consequence=c.get("consequence") or "",
                action=c.get("action") or "", source=src,
                rule_id="DC007" + (":" + cid if cid else "")))

    for a, b, gap in doubles:
        out.append(finding(
            "UNKNOWN", "Data",
            "%s logged twice, %d minute%s apart (%s)"
            % (a["product"], gap, "" if gap == 1 else "s", b["when"].strftime("%d %b %H:%M")),
            mechanism="Both entries are counted, because the higher total is the safer claim.",
            consequence="If it was one dose, every total above is too high by one dose.",
            action="If it was one dose, remove the extra entry in GutLog.",
            source="Dose log check.", rule_id="DC008"))
    seen = set()
    for u in unreadable:
        if u["product"] in seen:
            continue
        seen.add(u["product"])
        out.append(finding(
            "UNKNOWN", "Coverage",
            "%s: amount of %s per dose cannot be read" % (u["product"], display(u["ing"])),
            mechanism="GutLog's strength for it does not give an amount per unit.",
            consequence="Its doses count as zero in the %s total, so the total may be low."
                        % display(u["ing"]),
            action="Record the amount per unit for it in the dose rules.",
            source="Coverage check.", rule_id="DC009"))
    return rows, out


def run(events, stack, rules, now, finding, norm, display=None):
    intakes, unreadable = build_intakes(events, stack, rules, norm)
    rows, findings = evaluate(intakes, unreadable, rules, now, finding, display)
    return {"rows": rows, "findings": findings, "intakes": intakes}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.4 -> v1.9.0  ::  RXGUARD_V190_JOINT -- joints, falls, pain-medicine totals.

WHY: the Family Edition's `joint` profile needs RxGuard to (a) know a lower-limb
joint condition, (b) say when night sedation, a painful knee or ankle and age
together make a fall at night likely, and (c) hand GutLog the daily totals of
pain medicines per ingredient -- hidden components of combinations included,
a gel kept apart from a tablet -- and the stomach- and kidney-risk class loads.
All of it is generic: no medicine of anyone's is named here, and the owner's
own RxGuard behaves exactly as before (he has none of the new condition codes,
and his dose-rules file carries no route limits).

WHAT
  app.py
    * CONDITIONS += knee_oa, ankle_arthritis, hip_oa.
    * falls_findings() -- rule FR001 from knowledge/rules.json "falls_rules":
      a recorded lower-limb joint condition + age >= 60 (profile 'age') + at
      least one sedating medicine (sedation burden >= 2) taken at night
      (GutLog slot EVENING/NIGHT, or a dose logged 19:00-05:00 in 14 days)
      -> AMBER "Falls risk at night - light on, rise slowly"; RED when two or
      more sedating medicines are being taken. Counted in the as-taken view,
      so it reaches GutLog's banner and the Family page like any finding.
    * /api/feed/dose (feed bearer, like /api/feed/status): per-ingredient
      totals against ceilings, what was kept outside a ceiling by route
      (topical), the class loads, the dose findings, the classes of the
      current medicines and which are major CYP3A4 substrates (grapefruit).
  dose_ceiling.py
    * an ingredient may carry "routes": a dose by another route (a gel) is kept
      out of that ingredient's total and shown apart. Rules without "routes"
      -- the owner's -- are counted exactly as before.
  knowledge/rules.json
    * "falls_rules" with FR001, sourced and quoted.

Anchor-verified, idempotent, compile-checked, .bak of every file, --reverse
(app.py; the dose_ceiling change is inert without "routes"). Python 3.9.
"""
import argparse
import datetime
import json
import os
import py_compile
import shutil
import sys

TARGET = "/root/rxguard/app.py"
MARKER = "RXGUARD_V190_JOINT"
PREV = "RXGUARD_V184_NASSA"
VERSION = "1.9.0"

E = []
E.append(("version", 'APP_VERSION = "1.8.4"   # RXGUARD_V184_NASSA ',
          'APP_VERSION = "1.9.0"   # RXGUARD_V190_JOINT RXGUARD_V184_NASSA '))
E.append(("conditions",
          '    ("narcolepsy", "Narcolepsy"),   # RXGUARD_V183_LEMBOREXANT\n]\n',
          '    ("narcolepsy", "Narcolepsy"),   # RXGUARD_V183_LEMBOREXANT\n'
          '    ("knee_oa", "Knee osteoarthritis"),   # RXGUARD_V190_JOINT\n'
          '    ("ankle_arthritis", "Ankle arthritis (including post-traumatic)"),\n'
          '    ("hip_oa", "Hip osteoarthritis"),\n]\n'))

FALLS_PY = '''# --------------------------------------------------------------------------
# Falls at night -- RXGUARD_V190_JOINT
# One named rule (FR001, knowledge/rules.json "falls_rules"). It needs three
# things at once, each recorded rather than guessed: a lower-limb joint
# condition, an age, and a sedating medicine taken at night. None of the three
# alone fires it. Age is free text on /profile; one that cannot be read means
# the rule stays silent rather than assuming.
# --------------------------------------------------------------------------
def _profile_age():
    m = re.search(r"\\d{1,3}", profile_value("age") or "")
    try:
        a = int(m.group(0)) if m else None
    except ValueError:
        a = None
    return a if a and 0 < a < 120 else None


def _night_keys(data, events, rule):
    slots = set(s.upper() for s in rule.get("night_slots") or [])
    start, end = rule.get("night_from", "19:00"), rule.get("night_to", "05:00")
    out = set()
    for r in (data or {}).get("regimen") or []:
        if (r.get("slot") or "").upper() in slots:
            out.update(_split_molecules(r.get("molecule")))
    for e in events or []:
        t = (e.get("time") or "")[:5]
        if t and (t >= start or t < end):
            out.update(_split_molecules(e.get("molecule")))
    return out


def falls_findings(data, dosed, conditions, events=None):
    out = []
    for rule in (RULES_DOC.get("falls_rules") or {}).get("rules") or []:
        joint = sorted(set(rule.get("conditions_any") or []) & set(conditions))
        if not joint:
            continue
        age = _profile_age()
        if age is None or age < int(rule.get("min_age") or 60):
            continue
        smin = int(rule.get("sedating_min") or 2)
        sed = sorted(k for k in dosed
                     if ((get_drug(k) or {}).get("burden") or {}).get("sedation", 0) >= smin)
        night = _night_keys(data, events, rule)
        sed_night = [k for k in sed if k in night]
        if len(sed_night) < int(rule.get("amber_at") or 1):
            continue
        flag = "RED" if len(sed) >= int(rule.get("red_at") or 2) else "AMBER"
        out.append(finding(
            flag, "Falls", rule.get("title") or "Falls risk at night",
            mechanism="Sedating at night: %s. Sedating medicines in all: %s. Age %d, with %s recorded."
                      % (", ".join(display_name(k) for k in sed_night),
                         ", ".join(display_name(k) for k in sed), age,
                         " and ".join(CONDITION_LABELS.get(c, c).lower() for c in joint)),
            consequence=rule.get("consequence", ""), action=rule.get("action", ""),
            monitoring=rule.get("monitoring", ""), source=rule.get("source", ""),
            reviewed=rule.get("reviewed", ""), rule_id=rule.get("id", "FR001")))
    return out


def astaken_view(data):
'''
E.append(("falls function", "def astaken_view(data):\n", FALLS_PY))
E.append(("falls in astaken",
          '    dose = dose_view(data)\n    findings.extend(dose["findings"])\n',
          '    dose = dose_view(data)\n    findings.extend(dose["findings"])\n'
          '    # RXGUARD_V190_JOINT -- falls at night count like any other finding\n'
          '    _fev, _ferr = gutlog_doses(14)\n'
          '    findings.extend(falls_findings(data, dosed, conditions,\n'
          '                                   (_fev or {}).get("events") if not _ferr else None))\n'))

FEED_PY = '''    # RXGUARD_V190_JOINT -- GutLog's pain-medicine card reads this. Same
    # bearer as /api/feed/status; totals and loads only, nothing typed.
    @app.route("/api/feed/dose")
    def api_feed_dose():
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
        out = {"ok": True, "on": False, "err": "", "rows": [], "outside": [], "classes": [],
               "findings": [], "med_classes": [], "cyp3a4_major": []}
        if not gutlog_feed_enabled():
            out["err"] = "Not connected."
            return Response(json.dumps(out), mimetype="application/json")
        data, err = gutlog_stack(14)
        if err:
            out["err"] = err
            return Response(json.dumps(out), mimetype="application/json")
        keys = set()
        for r in (data.get("regimen") or []) + (data.get("taken") or []):
            keys.update(_split_molecules(r.get("molecule")))
        for k in sorted(keys):
            d = get_drug(k) or {}
            if d.get("class"):
                out["med_classes"].append(d["class"])
            if ((d.get("cyp") or {}).get("substrate") or {}).get("CYP3A4") == "major":
                out["cyp3a4_major"].append(display_name(k))
        out["med_classes"] = sorted(set(out["med_classes"]))
        dv = dose_view(data)
        out.update(on=dv["on"], err=dv["err"], version=dv["version"])
        for r in dv["rows"]:
            out["rows"].append({"name": r["name"], "unit": r["unit"], "total": round(r["total"], 1),
                                "ceiling": r["ceiling"], "state": r["state"], "frees": r["frees"],
                                "routes": r["routes"], "products": r["products"], "doses": r["doses"]})
        out["findings"] = [{"flag": f["flag"], "title": f["title"], "rule_id": f["rule_id"]}
                           for f in dv["findings"]]
        rules = dv.get("rules")
        if rules and not dv["err"]:
            ev, e2 = gutlog_doses()
            if not e2:
                intakes, _u = dose_ceiling.build_intakes(ev.get("events") or [], data, rules, norm_key)
                now = dose_now()
                lab = lambda k: rules["ingredients"].get(k, {}).get("label", display_name(k))
                side = {}
                for i in dose_ceiling.window(intakes, now, 24):
                    if "@" in i["ing"]:
                        s = side.setdefault(i["ing"], {"name": "%s (%s)" % (lab(i["ing"].split("@")[0]),
                                                                          i["ing"].split("@")[1]),
                                                       "total": 0.0, "unit": rules["ingredients"].get(
                                                           i["ing"].split("@")[0], {}).get("unit", "mg"),
                                                       "products": []})
                        s["total"] = round(s["total"] + (i["amount"] or 0.0), 1)
                        if i["product"] not in s["products"]:
                            s["products"].append(i["product"])
                out["outside"] = sorted(side.values(), key=lambda x: x["name"])
                for c in rules["classes"]:
                    cur = [i for i in dose_ceiling.window(intakes, now, c["window_h"])
                           if i["ing"] in c["members"] and (not c["routes"] or i["route"] in c["routes"])]
                    taken = sorted(set(lab(i["ing"]) for i in cur))
                    out["classes"].append({"id": c.get("id", ""), "label": c.get("label", ""),
                                           "taken": taken, "count": len(taken),
                                           "limit": int(c.get("limit") or 1), "flag": c.get("flag", "AMBER")})
        return Response(json.dumps(out), mimetype="application/json")

    @app.route("/healthz")
'''
E.append(("feed dose", '    @app.route("/healthz")\n', FEED_PY))

# ------------------------------------------------ dose_ceiling.py
D = []
D.append(("routes normalised",
          '        r["label"] = r.get("label") or k\n        ings[norm(k)] = r\n',
          '        r["label"] = r.get("label") or k\n'
          '        r["routes"] = [x.lower() for x in r.get("routes") or []]   # RXGUARD_V190_JOINT\n'
          '        ings[norm(k)] = r\n'))
D.append(("routes kept apart",
          '            if amt is None:\n                unreadable.append(it)\n            intakes.append(it)\n',
          '            # RXGUARD_V190_JOINT -- a dose by a route the ingredient\'s ceiling does\n'
          '            # not cover (a gel against a tablet ceiling) is kept apart, never added in.\n'
          '            if known[k].get("routes") and route not in known[k]["routes"]:\n'
          '                it["ing"] = k + "@" + route\n'
          '            elif amt is None:\n'
          '                unreadable.append(it)\n'
          '            intakes.append(it)\n'))

FALLS_RULES = {
    "_note": "RXGUARD_V190_JOINT. Rules that need a condition, an age and the time a medicine is taken together. Generic; no personal data.",
    "rules": [{
        "id": "FR001",
        "title": "Falls risk at night - light on, rise slowly",
        "conditions_any": ["knee_oa", "ankle_arthritis", "hip_oa"],
        "min_age": 60,
        "sedating_min": 2,
        "night_slots": ["EVENING", "NIGHT"],
        "night_from": "19:00", "night_to": "05:00",
        "amber_at": 1, "red_at": 2,
        "consequence": "A sedating medicine at night, a painful knee, ankle or hip, and age together make a fall on the way to the bathroom at night more likely; a fall at this age often means a fracture.",
        "action": "Light on (or a night light) before getting up; sit on the edge of the bed for a minute, then rise slowly; walking aid and slippers within reach; ask whether the night medicine is still needed at this dose.",
        "monitoring": "Any fall or near-fall, dizziness on standing, morning drowsiness.",
        "source": "2023 AGS Beers Criteria (J Am Geriatr Soc 2023;71:2052-81), history of falls or fractures - for benzodiazepines, Z-drugs, antidepressants, antiepileptics, antipsychotics and opioids: 'Avoid unless safer alternatives are not available'; and: minimise the number of CNS-active drugs.",
        "reviewed": "2026-09-25"}]}

EDITS = E
JINJA = ("{{", "{%", "{#")


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def rules_add(text):
    if '"falls_rules"' in text:
        return text
    i = text.rstrip().rfind("}")
    body = json.dumps(FALLS_RULES, indent=1, ensure_ascii=False)
    body = "\n".join(" " + ln for ln in body.split("\n")).lstrip()
    return text[:i].rstrip() + ',\n "falls_rules": ' + body + "\n}\n"


def apply_set(src, edits, label):
    bad = [(l, src.count(o)) for l, o, n in edits if src.count(o) != 1]
    if bad:
        for l, c in bad:
            print("  %s / %s: found %d times, need 1" % (label, l, c))
        return None
    out = src
    for l, o, n in edits:
        out = out.replace(o, n, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    here = os.path.dirname(os.path.abspath(a.file))
    dpath = os.path.join(here, "dose_ceiling.py")
    rpath = os.path.join(here, "knowledge", "rules.json")
    print("==================================================================")
    print("RxGuard joints, falls, pain-medicine totals -> v" + VERSION)
    print("files: %s, %s, %s" % (a.file, dpath, rpath))
    print("==================================================================")
    for label, old, new in EDITS:
        for tok in JINJA:
            if new.count(tok) > old.count(tok):
                print("FATAL: %s adds the Jinja token %r. Nothing written." % (label, tok))
                return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    new_app = apply_set(src, EDITS, "app.py")
    dsrc = read(dpath)
    new_dose = dsrc if MARKER in dsrc else apply_set(dsrc, D, "dose_ceiling.py")
    rsrc = read(rpath)
    new_rules = rules_add(rsrc)
    try:
        json.loads(new_rules)
    except ValueError as exc:
        print("rules.json would not parse: %s. Nothing written." % exc)
        return 1
    if new_app is None or new_dose is None:
        print("Refusing to patch. Nothing written.")
        return 1
    print("anchors: all matched")
    if a.check:
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    done = []
    try:
        for path, text in ((a.file, new_app), (dpath, new_dose), (rpath, new_rules)):
            bak = path + ".bak-v190-" + stamp
            shutil.copy2(path, bak)
            done.append((path, bak))
            write(path, text)
            if path.endswith(".py"):
                py_compile.compile(path, doraise=True)
            print("written: %s (backup %s)" % (os.path.basename(path), os.path.basename(bak)))
    except Exception as exc:
        for path, bak in done:
            shutil.copy2(bak, path)
        print("FAILED, all restored: %s" % exc)
        return 2
    print("Next:  python3 test_v190_joint.py app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

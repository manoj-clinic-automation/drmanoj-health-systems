#!/usr/bin/env python3
"""FitLog KB lint — structural validation of all knowledge files. Exit 0 = clean."""
import json, os, sys

K = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "knowledge")
errs = []

def load(n):
    with open(os.path.join(K, n), encoding="utf-8") as f:
        return json.load(f)

rules, exkb, proto, meds = load("rules.json"), load("exercises.json"), load("protocols.json"), load("med_stack.json")

# rules
need_th = ["recovery_pain", "red_pain", "yellow_pain_lo", "yellow_pain_hi", "exertion_cap_intensity",
           "long_flight_hr", "deload_rolling7_min", "w01_pain_level", "w03_analgesic_days"]
for t in need_th:
    if t not in rules["thresholds"]: errs.append(f"rules: missing threshold {t}")
ids = [r["id"] for r in rules["rules"]]
if len(ids) != len(set(ids)): errs.append("rules: duplicate rule ids")
for v in ["GREEN", "YELLOW", "RED", "RECOVERY", "DELOAD", "TRAVEL"]:
    if v not in rules["verdict_minutes"]: errs.append(f"rules: verdict_minutes missing {v}")

# exercises
eids = set()
for e in exkb["exercises"]:
    if e["id"] in eids: errs.append(f"exercises: duplicate id {e['id']}")
    eids.add(e["id"])
    for f in ["name_en", "name_hi", "category", "tiers", "purpose", "cues"]:
        if not e.get(f): errs.append(f"exercises {e['id']}: missing {f}")
    for t in e["tiers"]:
        if t not in ("G", "Y", "R"): errs.append(f"exercises {e['id']}: bad tier {t}")
        if not e.get("dose_" + t.lower()): errs.append(f"exercises {e['id']}: tier {t} without dose_{t.lower()}")
for tname, tpl in exkb["templates"].items():
    for eid in tpl.get("fixed", []):
        if eid not in eids: errs.append(f"templates {tname}: unknown exercise {eid}")
    for slot in tpl.get("slots", []):
        cats = slot[0].split("|")
        if not any(e["category"] in cats and e["active"] for e in exkb["exercises"]):
            errs.append(f"templates {tname}: empty slot {slot[0]}")

# protocols
pids = [p["id"] for p in proto["protocols"]]
if len(pids) != len(set(pids)): errs.append("protocols: duplicate ids")
for p in proto["protocols"]:
    if p["event_type"] not in ("OT", "SOCIAL", "TRAVEL"): errs.append(f"protocols {p['id']}: bad event_type")
    if p["phase"] not in ("pre", "transit", "post"): errs.append(f"protocols {p['id']}: bad phase")
    if not p["steps"]: errs.append(f"protocols {p['id']}: no steps")
for et in ("OT", "SOCIAL"):
    for ph in ("pre", "post"):
        if not any(p["event_type"] == et and p["phase"] == ph for p in proto["protocols"]):
            errs.append(f"protocols: {et} missing {ph}")
for cond in ("mode=car", "mode=flight & leg<3", "mode=flight & leg>=3"):
    if not any(p["event_type"] == "TRAVEL" and cond in p["condition"] for p in proto["protocols"]):
        errs.append(f"protocols: TRAVEL missing condition family {cond}")

# med stack
mids = set()
for m in meds["meds"]:
    if m["id"] in mids: errs.append(f"med_stack: duplicate {m['id']}")
    mids.add(m["id"])
    if m["category"] not in ("analgesic", "sleep", "other"): errs.append(f"med_stack {m['id']}: bad category")
    if not m["dose_options"]: errs.append(f"med_stack {m['id']}: no dose options")
    if not m["name"]: errs.append(f"med_stack {m['id']}: no name")

if errs:
    print("KB LINT FAIL:")
    for e in errs: print("  -", e)
    sys.exit(1)
print(f"KB LINT PASS: {len(exkb['exercises'])} exercises, {len(proto['protocols'])} protocols, "
      f"{len(meds['meds'])} meds, {len(rules['rules'])} rules")

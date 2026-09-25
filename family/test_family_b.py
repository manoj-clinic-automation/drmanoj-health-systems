#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_b.py -- Family Edition Phase B: the joint focus.

    python3 -B family/test_family_b.py gutlog/app.py

Runs the scratch family (famtest.Rig: m1 `gut`, m2 `joint`) from a tree built
of GUT_SRC / RX_SRC / FIT_SRC (default: this repository), so a negative
control can break a COPY of any app folder and watch the right assertion fail.

This suite names no medicine. The ingredients come from RxGuard's generic
dose-rules file and the sedating / statin-class molecules from RxGuard's
knowledge base, chosen at run time by their properties -- so the file cannot
carry anyone's list into the repository. Python 3.9.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")
RX_DIR = famtest.app_src("rx")
GUT_DIR = famtest.app_src("gut")
FIT_DIR = famtest.app_src("fit")


def load_kb():
    with open(os.path.join(RX_DIR, "knowledge", "drugs.json"), encoding="utf-8") as fh:
        drugs = json.load(fh)["drugs"]
    with open(os.path.join(RX_DIR, "knowledge", "dose_rules.generic.json"), encoding="utf-8") as fh:
        rules = json.load(fh)
    return drugs, rules


def pick_ingredients(rules):
    ings = rules["ingredients"]
    gi = next(c for c in rules["classes"] if c["id"] == "GI")["members"]
    plain = next(k for k, r in sorted(ings.items()) if not r.get("routes") and r.get("ceiling", 0) >= 1000)
    routed = [k for k in sorted(ings) if ings[k].get("routes") and k in gi and not ings[k].get("course_days")]
    return plain, routed[0], routed[1]


def load_module(path, name, env):
    old = dict(os.environ)
    os.environ.update(env)
    sys.path.insert(0, os.path.dirname(path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)
        os.environ.clear()
        os.environ.update(old)


def earlier(minutes):
    t = datetime.now() - timedelta(minutes=minutes)
    return t.date().isoformat(), t.strftime("%H:%M")


# ------------------------------------------------------------------ unit parts
def falls_matrix(drugs):
    """FR001 on its own: fires exactly on its conditions."""
    sed = sorted(k for k, d in drugs.items() if (d.get("burden") or {}).get("sedation", 0) >= 2)
    mild = sorted(k for k, d in drugs.items() if (d.get("burden") or {}).get("sedation", 0) == 1)
    work = tempfile.mkdtemp(prefix="famb_rx_")
    db = os.path.join(work, "rx.db")
    rx = load_module(os.path.join(RX_DIR, "app.py"), "rx_under_test",
                     {"RXGUARD_DB": db, "RXGUARD_GUTLOG_FEED": "0", "RXGUARD_KB_NOSPAWN": "1"})
    app = rx.create_app(db_path=db, secret="x" * 40)
    S1, S2, M1 = sed[0], sed[1], (mild[0] if mild else None)
    cases = [
        # (label, conditions, age, regimen [(key, slot)], events [(key, time)], expected flag or None)
        ("no joint condition", [], "70", [(S1, "NIGHT")], [], None),
        ("age 59", ["knee_oa"], "59", [(S1, "NIGHT")], [], None),
        ("age unreadable", ["knee_oa"], "", [(S1, "NIGHT")], [], None),
        ("sedating in the morning only", ["knee_oa"], "70", [(S1, "MORNING")], [], None),
        ("mildly sedating at night", ["ankle_arthritis"], "70", [(M1, "NIGHT")] if M1 else [], [], None),
        ("age 60, one at night", ["knee_oa"], "60", [(S1, "NIGHT")], [], "AMBER"),
        ("an as-needed dose at 23:10", ["ankle_arthritis"], "68", [], [(S1, "23:10")], "AMBER"),
        ("an as-needed dose at 14:00", ["ankle_arthritis"], "68", [], [(S1, "14:00")], None),
        ("two sedating, one at night", ["hip_oa"], "72", [(S1, "NIGHT"), (S2, "MORNING")], [], "RED"),
        ("two at night", ["knee_oa", "ankle_arthritis"], "65", [(S1, "NIGHT"), (S2, "EVENING")], [], "RED"),
    ]
    out = []
    with app.test_request_context("/"):
        app.preprocess_request()
        con = rx.get_db()
        for label, conds, age, reg, evs, want in cases:
            con.execute("UPDATE conditions SET active=0")
            for c in conds:
                con.execute("UPDATE conditions SET active=1 WHERE code=?", (c,))
            con.commit()
            rx.set_profile("age", age)
            data = {"regimen": [{"molecule": k, "slot": s} for k, s in reg]}
            events = [{"molecule": k, "time": t} for k, t in evs]
            dosed = set(k for k, _s in reg) | set(k for k, _t in evs)
            f = [x for x in rx.falls_findings(data, dosed, rx.active_conditions(), events)
                 if x["rule_id"] == "FR001"]
            got = f[0]["flag"] if f else None
            out.append((label, want, got))
    return out


def compare_cases(gut):
    """step_pain_compare on crafted days: the next day's pain, never the same day's."""
    base = date(2026, 9, 1)
    d = lambda i: (base + timedelta(days=i)).isoformat()
    steps = {d(i): n for i, n in enumerate([9000, 2000, 8800, 2100, 9500, 1900, 3000, 2500, 2200])}
    # high-step days are d0, d2, d4 (top third of 9). Pain the NEXT day: d1, d3, d5 = 7, 6, 8.
    # Same-day pain on the high days is 1, so reading the wrong day gives 1.0.
    pain = {d(0): 1, d(2): 1, d(4): 1, d(1): 7, d(3): 6, d(5): 8, d(7): 2, d(8): 3}
    return gut.step_pain_compare(steps, pain)


FIT_PLAN = r'''
import importlib.util, json, os, sys
from datetime import date, timedelta
fdir = sys.argv[1]
sys.path.insert(0, fdir)
spec = importlib.util.spec_from_file_location("fit_under_test", os.path.join(fdir, "app.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ids = set()
for v in ("GREEN", "YELLOW", "DELOAD", "RED", "TRAVEL"):
    for i in range(30):
        p = m.build_plan(v, (date(2026, 9, 1) + timedelta(days=i)).isoformat(), 60)
        for it in p.get("items") or []:
            if isinstance(it, dict):
                ids.add(it["id"])
avoid = ((m.EXKB.get("programmes") or {}).get("joint") or {}).get("avoid") or []
print(json.dumps({"ids": sorted(ids), "avoid": avoid, "programme": m.PROGRAMME}))
'''


def fit_plans():
    work = tempfile.mkdtemp(prefix="famb_fit_")
    script = os.path.join(work, "plan.py")
    with open(script, "w") as fh:
        fh.write(FIT_PLAN)
    res = {}
    for prog in ("", "joint"):
        env = dict(os.environ, FITLOG_DB=os.path.join(work, "f%s.db" % prog), FITLOG_PROGRAMME=prog,
                   FITLOG_GUTLOG_FEED="0", FITLOG_INGEST_ENV=os.path.join(work, "none.env"))
        r = subprocess.run([sys.executable, "-B", script, FIT_DIR], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            res[prog or "owner"] = json.loads(r.stdout.decode().strip().splitlines()[-1])
        except Exception:
            res[prog or "owner"] = {"error": r.stdout.decode()[-300:]}
    return res


# ------------------------------------------------------------------ end to end
def add_med(c, slug, name, molecule, strength):
    c.post("/%s/api/prnmeds" % slug, {"name": name}, ctype="json")
    meds = c.get("/%s/api/prnmeds/full" % slug).json() or []
    mid = next(m["id"] for m in meds if m["name"] == name)
    c.post("/%s/api/salt" % slug, {"med_id": mid, "molecule": molecule, "strength": strength}, ctype="json")
    return mid


def log_dose(c, slug, mid, minutes):
    day, hm = earlier(minutes)
    return c.post("/%s/api/now/dose" % slug, {"med_id": mid, "status": "EXTRA", "day": day, "dtime": hm},
                  ctype="json")


def run(rig):
    drugs, rules = load_kb()
    plain, nsaid1, nsaid2 = pick_ingredients(rules)
    sed = sorted(k for k, d in drugs.items() if (d.get("burden") or {}).get("sedation", 0) >= 2)
    statin = next(k for k, d in sorted(drugs.items()) if "statin" in (d.get("class") or "").lower())

    m1, m2 = rig.member_client("m1"), rig.member_client("m2")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs"})
    m2.post("/m2/welcome", {"name": "Member B", "age": "68", "cond": ["knee_oa", "ankle_arthritis"]})

    # ---------------------------------------------------------------- B08
    c1 = m1.get("/m1/api/joint/cfg").json() or {}
    c2 = m2.get("/m2/api/joint/cfg").json() or {}
    oc = rig.owner_client().get("/api/joint/cfg").json() or {}
    check("B08 the joint cards show under the joint profile only (owner and gut member unchanged)",
          c2.get("show") is True and c1.get("show") is False and oc.get("show") is False,
          "m2 %s m1 %s owner %s" % (c2.get("show"), c1.get("show"), oc.get("show")))

    # ---------------------------------------------------------------- B02
    day, hm = earlier(30)
    r = m2.post("/m2/api/joint", {"site": "Knee - R", "score": 6, "start": hm, "day": day,
                                  "triggers": ["stairs", "getting up", "not-a-trigger"],
                                  "stiff_min": 20, "walk_min": 12}, ctype="json")
    bad = m2.post("/m2/api/joint", {"site": "Elbow", "score": 3}, ctype="json")
    rows = (m2.get("/m2/api/joint?days=3").json() or {}).get("rows") or []
    got = rows[0] if rows else {}
    check("B02 knee / ankle scores save with triggers, stiffness and walking tolerance",
          r.status == 200 and bad.status == 400 and got.get("site") == "Knee - R" and got.get("score") == 6
          and got.get("triggers") == ["stairs", "getting up"] and got.get("stiff_min") == 20
          and got.get("walk_min") == 12 and got.get("jtime") == hm, got)
    s2 = (m2.get("/m2/api/pain").json() or {}).get("sites") or []
    s1 = (m1.get("/m1/api/pain").json() or {}).get("sites") or []
    check("B02 knee and ankle lead the pain sites under joint; the gut profile's sites are unchanged",
          [x["slug"] for x in s2[:4]] == ["knee_l", "knee_r", "ankle_l", "ankle_r"]
          and s1 and s1[0]["slug"] == "hip_thigh_both" and not any(x["slug"].startswith("knee") for x in s1),
          "m2 %s / m1 %s" % ([x["slug"] for x in s2[:4]], [x["slug"] for x in s1[:2]]))

    # A statin-class medicine for m2 before anything reads m2's RxGuard, so no
    # cached answer predates it; m1 has none yet and is asked first.
    before = (m1.get("/m1/api/joint/lipids").json() or {}).get("ask_muscle")
    lm = add_med(m2, "m2", "Medicine L", statin, "10 mg")
    m2.post("/m2/api/schedule", {"med_id": lm, "slot": "NIGHT"}, ctype="json")

    # ---------------------------------------------------------------- B03
    a = add_med(m2, "m2", "Medicine A", plain, "500 mg")
    b = add_med(m2, "m2", "Medicine B", "%s + %s" % (nsaid1, plain), "100 mg + 325 mg")
    cc = add_med(m2, "m2", "Medicine C", nsaid2, "50 mg")
    g = add_med(m2, "m2", "Medicine C gel", nsaid2, "10 mg")
    for mid, mins in ((a, 50), (b, 40), (cc, 30), (g, 20)):
        log_dose(m2, "m2", mid, mins)
    pm = m2.get("/m2/api/painmeds").json() or {}
    rows = dict((r_["name"].lower(), r_) for r_ in pm.get("rows") or [])
    lab = lambda k: rules["ingredients"][k]["label"].lower()
    p_row, n1_row, n2_row = rows.get(lab(plain), {}), rows.get(lab(nsaid1), {}), rows.get(lab(nsaid2), {})
    outside = pm.get("outside") or []
    gi = next((x for x in pm.get("classes") or [] if x["id"] == "GI"), {})
    check("B03 pain-medicine totals sum a combination into the standalone ingredient's pool",
          pm.get("ok") and pm.get("on") and p_row.get("total") == 825 and n1_row.get("total") == 100
          and sorted(p_row.get("products") or []) == ["Medicine A", "Medicine B"],
          "on %s rows %s" % (pm.get("on"), json.dumps(pm.get("rows"))[:300]))
    check("B03 pain-medicine totals keep a gel apart from the tablet ceiling",
          n2_row.get("total") == 50 and len(outside) == 1 and outside[0]["total"] == 10
          and "topical" in outside[0]["name"], "%s | %s" % (n2_row, outside))
    check("B03 pain-medicine totals show the stomach- and kidney-risk loads",
          gi.get("count") == 2 and any(f["rule_id"].startswith("DC005") and f["flag"] == "RED"
                                       for f in pm.get("findings") or [])
          and any(x["id"] == "KIDNEY" for x in pm.get("classes") or []),
          "%s %s" % (gi, pm.get("findings")))

    # ---------------------------------------------------------------- B04
    for label, want, got in falls_matrix(drugs):
        check("B04 falls-risk rule FR001 fires exactly on its conditions -- %s" % label, want == got,
              "want %s got %s" % (want, got))
    s = add_med(m2, "m2", "Medicine S", sed[0], "")
    m2.post("/m2/api/schedule", {"med_id": s, "slot": "NIGHT"}, ctype="json")
    s1_ = add_med(m1, "m1", "Medicine S", sed[0], "")
    m1.post("/m1/api/schedule", {"med_id": s1_, "slot": "NIGHT"}, ctype="json")
    page2 = m2.get("/m2/rx/astaken", follow=True).text
    page1 = m1.get("/m1/rx/astaken", follow=True).text
    con = sqlite3.connect(rig.db("m2", "rx"))
    rx_conds = set(r_[0] for r_ in con.execute("SELECT code FROM conditions WHERE active=1"))
    rx_age = (con.execute("SELECT value FROM profile WHERE key='age'").fetchone() or [None])[0]
    con.close()
    check("B09 conditions and age entered once in GutLog reach the member's RxGuard",
          {"knee_oa", "ankle_arthritis"} <= rx_conds and rx_age == "68", "%s %s" % (rx_conds, rx_age))
    check("B04 falls-risk rule FR001 reaches the member's RxGuard page, and not the member without the condition",
          "FR001" in page2 and "Falls risk at night" in page2 and "FR001" not in page1,
          "m2 has %s, m1 has %s" % ("FR001" in page2, "FR001" in page1))

    # ---------------------------------------------------------------- B05
    con = sqlite3.connect(rig.db("m2", "gut"))
    old = (date.today() - timedelta(days=250)).isoformat()
    new = (date.today() - timedelta(days=40)).isoformat()
    for dd, t, v, u in ((old, "LDL Cholesterol", 142, "mg/dL"), (new, "LDL Cholesterol", 98, "mg/dL"),
                        (old, "SGPT (ALT)", 31, "U/L"), (new, "CPK (Creatine Kinase)", 120, "U/L")):
        con.execute("INSERT INTO rec_labs(day, test, value, num, unit, lab, created) VALUES(?,?,?,?,?,?,?)",
                    (dd, t, str(v), v, u, "Lab A", dd))
    con.commit()
    con.close()
    lp = m2.get("/m2/api/joint/lipids").json() or {}
    grp = dict((x["label"], x) for x in lp.get("groups") or [])
    ldl, alt, ck = grp.get("LDL cholesterol", {}), grp.get("ALT (SGPT)", {}), grp.get("CK", {})
    due_ldl = (date.fromisoformat(new) + timedelta(days=int(round(6 * 30.44)))).isoformat()
    due_alt = (date.fromisoformat(old) + timedelta(days=int(round(12 * 30.44)))).isoformat()
    check("B05 lipid analytes trend with due dates",
          len(ldl.get("points") or []) == 2 and (ldl.get("last") or {}).get("value") == 98
          and ldl.get("due") == due_ldl and ldl.get("overdue") is False
          and alt.get("due") == due_alt and (ck.get("last") or {}).get("value") == 120 and ck.get("due") is None,
          "ldl %s alt %s ck %s" % (ldl, alt.get("due"), ck))
    lp2 = lp
    ans = m2.post("/m2/api/joint/muscle", {"answer": "no"}, ctype="json")
    lp3 = m2.get("/m2/api/joint/lipids").json() or {}
    check("B05 the muscle-ache question appears with a statin-class medicine, weekly",
          before is False and lp2.get("statin") is True and lp2.get("ask_muscle") is True
          and ans.status == 200 and lp3.get("ask_muscle") is False,
          "before %s statin %s ask %s after %s" % (before, lp2.get("statin"), lp2.get("ask_muscle"),
                                                  lp3.get("ask_muscle")))

    # ---------------------------------------------------------------- B06
    gut = load_module(os.path.join(GUT_DIR, "app.py"), "gut_under_test",
                      {"GUTLOG_DB": os.path.join(tempfile.mkdtemp(prefix="famb_g_"), "g.db"),
                       "GUTLOG_NOSPAWN": "1", "GUTLOG_LINKS": "0", "GUTLOG_INSECURE": "1"})
    cm = compare_cases(gut)
    check("B06 step-vs-pain comparison uses the right days (the day after a high-step day)",
          cm.get("enough") and cm.get("high_days") == ["2026-09-01", "2026-09-03", "2026-09-05"]
          and cm["high"]["avg_next_day_pain"] == 7.0 and cm["high"]["with_pain_logged"] == 3
          and cm["other"]["days"] == 6 and cm["threshold"] == 8800, cm)
    w = m2.get("/m2/api/joint/watch").json() or {}
    check("B06 the steps-and-pain card has 28 days, the joint pain of the day and the weight trend",
          len(w.get("rows") or []) == 28 and any(r_.get("pain") == 6 for r_ in w.get("rows") or [])
          and isinstance(w.get("weight"), list), "rows %d" % len(w.get("rows") or []))

    # ---------------------------------------------------------------- B07
    fp = fit_plans()
    own, jt = fp.get("owner", {}), fp.get("joint", {})
    new_ids = {"E19", "E20", "E21", "E22", "E23", "E24", "E25", "E26"}
    check("B07 the joint programme never loads an avoided exercise, and the owner's sessions are unchanged",
          jt.get("programme") == "joint" and jt.get("ids") and not (set(jt["ids"]) & set(jt.get("avoid") or []))
          and (set(jt["ids"]) & {"E21", "E22"}) and own.get("ids") and not (set(own["ids"]) & new_ids)
          and own.get("programme") == "", fp)
    plans = {}
    for slug, cl in (("m1", m1), ("m2", m2)):
        cl.get("/%s/fit/" % slug, follow=True)
        cl.post("/%s/fit/checkin" % slug, {"sleep": "4", "energy": "4", "pain": "0", "avail": "40"})
        con = sqlite3.connect(rig.db(slug, "fit"))
        row = con.execute("SELECT plan FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        plans[slug] = [i.get("id") for i in (json.loads(row[0]).get("items") or [])
                       if isinstance(i, dict)] if row else None
    check("B07 the joint programme never loads an avoided exercise -- in the member's own FitLog",
          plans.get("m2") and set(plans["m2"]) & {"E21", "E22"} and not set(plans["m2"]) & set(jt.get("avoid") or [])
          and plans.get("m1") and not set(plans["m1"]) & new_ids, plans)


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("B00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

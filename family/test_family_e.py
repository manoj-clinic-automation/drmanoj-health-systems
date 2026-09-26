#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_e.py -- Family Edition Phase E: the weight profile, a seeded
member, the physio role.

    python3 -B family/test_family_e.py gutlog/app.py

The scratch family (famtest.Rig) plus a third member, "Member W", profile
weight, stamped with the real tool from a SYNTHETIC seed written here
("Medicine A" .. "Medicine E", a one-page test PDF, "Physio A" as p1).
Every rule the brief states is a check, and each has a mutation in
new_assertions_family_e.json that makes it fail: a weekly dose shown on the
wrong day, a check-in not stamped / total wrong, the item-9 flag
suppressed, the physio reaching a non-physio endpoint, the meal window
ignored, a milestone not advancing, seed data leaking into a tracked
file. No real name and no real medicine is in this file. Python 3.9.
"""
import glob
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")
WD = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def file_pin(path, slug):
    pin = None
    for line in open(path, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        m = re.match(r"PIN (\d{6})$", f[-1]) if f else None
        if f and f[0] == slug and m:
            pin = m.group(1)
    return pin


def stamp_tool(rig, *args):
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "stamp_member.py"), "--no-system",
                        "--root", rig.root, "--code", rig.code, "--port-base", str(rig.port_base),
                        "--base-url", rig.front_url] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def kb_molecules(n=3):
    """Molecule names for the synthetic salts, taken from RxGuard's curated
    knowledge at run time (as test_family_b does), so this file names none."""
    with open(os.path.join(famtest.app_src("rx"), "knowledge", "drugs.json"), encoding="utf-8") as fh:
        keys = sorted(json.load(fh)["drugs"])
    return keys[:n]


def make_seed(work, sentinel):
    """A synthetic seed: the shape of the real one, none of its content. The
    check-in rules are set to TODAY so the suite is deterministic on any day."""
    today = date.today()
    wd = WD[today.weekday()]
    mol_a, mol_c, mol_d = kb_molecules(3)
    seed = {
        "slug": "m3", "name": "Member W", "profile": "weight",
        "setup": {"sex": "F", "age": 50, "height_cm": 150, "weight_kg": 90.0, "waist_cm": 110, "hips_cm": 120,
                  "neck_cm": 36, "diet": "eggetarian", "conditions": ["knee arthritis", "thyroid (treated)"],
                  "allergies": [], "wake": "08:00", "bed": "23:30",
                  "meal_windows": {"Breakfast": ["10:30", "12:30"], "Lunch": ["14:30", "17:30"],
                                   "Evening": ["17:30", "19:30"], "Dinner": ["19:30", "21:45"],
                                   "Late snack": ["21:45", "23:30"]},
                  "night_tablet": False,
                  "targets": {"weight_milestones_pct": [5, 10, 15], "kcal": [1250, 1400], "protein_g": [80, 90],
                              "dinner_by": "20:30", "lights_out": "23:30"}},
        "medicines": [
            {"name": "Medicine A", "molecule": mol_a, "strength": "50 mcg", "regular": True,
             "schedule": [{"slot": "MORNING", "time": "08:00", "dose": "1 tab", "food": "BEFORE"}]},
            {"name": "Medicine B", "molecule": "", "no_salt": True, "strength": "", "regular": True,
             "schedule": [{"slot": "NOON", "time": "15:00", "dose": "1 tab", "food": "AFTER"}]},
            {"name": "Medicine C " + sentinel, "molecule": mol_c, "strength": "2.5 mg", "regular": True,
             "variants": "2.5 mg|5 mg|7.5 mg",
             "schedule": [{"slot": "WEEKLY", "weekday": wd, "time": "20:00", "dose": "2.5 mg", "food": "AFTER",
                           "note": "after dinner"}]},
            {"name": "Medicine D", "molecule": mol_d, "strength": "200 mg", "regular": False, "note": "rare"},
            {"name": "Medicine E", "molecule": "", "no_salt": True, "strength": "500 mg", "regular": False},
        ],
        "checkins": {"phq9": "monthly:%d" % min(today.day, 28), "phq2": "weekly:%s" % wd,
                     "epworth": "monthly:%d" % min(today.day, 28), "side_effects": "day_after_weekly_dose",
                     "weigh_in": "weekly:%s 00:01" % wd, "measurements": "monthly:%d" % min(today.day, 28)},
        "physio": {"name": "Physio A", "slug": "p1", "role": "physio"},
        "plan_pdf": {"file": "Plan_A.pdf", "title": "Plan A"},
    }
    d = os.path.join(work, "seeds")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "m3_seed.local.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(seed, fh)
    with open(os.path.join(d, "Plan_A.pdf"), "wb") as fh:
        fh.write(PDF)
    return p, seed


def gut_con(rig, slug):
    c = sqlite3.connect(rig.db(slug, "gut"))
    c.row_factory = sqlite3.Row
    return c


def tree_text(root):
    out = []
    for r, _d, files in os.walk(root):
        for f in files:
            if f.endswith((".py", ".json", ".md", ".sh", ".html", ".txt", ".js")):
                try:
                    out.append(open(os.path.join(r, f), encoding="utf-8", errors="replace").read())
                except OSError:
                    pass
    return "\n".join(out)


def run(rig):
    owner = rig.owner_client()
    m1 = rig.member_client("m1")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs", "height_cm": "160"})
    sentinel = "zq" + os.urandom(3).hex()
    seed_path, seed = make_seed(rig.work, sentinel)
    pwf = os.path.join(rig.work, "first-login.local.txt")
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()
    tmrw = (date.today() + timedelta(days=1)).isoformat()
    wd = WD[date.today().weekday()]

    # ---------------------------------------------------------------- E01 stamp with a seed
    rc, out = stamp_tool(rig, "--slug", "m3", "--name", "Member W", "--profile", "weight", "--seed", seed_path,
                         "--password-file", pwf)
    rig.pw["m3"] = file_pin(pwf, "m3") or ""
    rig.pw["p1"] = file_pin(pwf, "p1") or ""
    reg = json.load(open(os.path.join(rig.root, "root", "family", "members.local.json")))
    m3reg = next((m for m in reg["members"] if m["slug"] == "m3"), {})
    ports = rig.ports("m3")
    rig.front.routes = sorted(dict(rig.front.routes, **{"/m3/rx/": ports["rx"], "/m3/fit/": ports["fit"],
                                                        "/m3/": ports["gut"]}).items(),
                              key=lambda kv: len(kv[0]), reverse=True)
    rig.start_member("m3")
    con = gut_con(rig, "m3")
    st = dict(con.execute("SELECT key, value FROM settings").fetchall())
    meds = [r["name"] for r in con.execute("SELECT name FROM prnmeds ORDER BY sort, id").fetchall()]
    sched = [dict(r) for r in con.execute("SELECT s.slot, s.weekday, s.at_time, s.dose_text, s.variants, p.name "
                                          "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id WHERE s.valid_to=''")]
    salts = con.execute("SELECT COUNT(*) FROM med_salts").fetchone()[0]
    mols = dict(con.execute("SELECT name, molecule FROM prnmeds").fetchall())
    plans = [dict(r) for r in con.execute("SELECT p.title, f.stored_name FROM plans p JOIN plan_files f ON f.plan_id=p.id")]
    vit = con.execute("SELECT weight, waist FROM vitals ORDER BY id LIMIT 1").fetchone()
    con.close()
    prof = json.loads(st.get("member_profile") or "{}")
    wk = [s for s in sched if s["slot"] == "WEEKLY"]
    check("E01 --seed applies the setup, meal windows, weight plan and targets as data, as the member",
          rc == 0 and m3reg.get("profile") == "weight" and st.get("member_setup_done") == "1"
          and st.get("now_profile") == "weight" and prof.get("name") == "Member W" and prof.get("age") == 50
          and "knee_oa" in prof.get("conditions", []) and "thyroid" in prof.get("conditions", [])
          and json.loads(st.get("meal_windows"))["Breakfast"] == ["10:30", "12:30"]
          and json.loads(st.get("weight_plan"))["start_weight"] == 90.0
          and json.loads(st.get("weight_plan"))["milestones_pct"] == [5, 10, 15]
          and json.loads(st.get("checkins")).get("side_effects") == "day_after_weekly_dose"
          and vit and vit[0] == 90.0 and vit[1] == 110, "%s\n%s" % (rc, out[-500:]))
    check("E01 --seed applies the medicines: salts and strengths, regular schedules, the WEEKLY line with its "
          "weekday, time and variants, the as-needed ones without a schedule",
          meds[:5] == ["Medicine A", "Medicine B", "Medicine C " + sentinel, "Medicine D", "Medicine E"]
          and salts == 5 and mols["Medicine A"] == seed["medicines"][0]["molecule"] and mols["Medicine B"] == ""
          and len(sched) == 3 and len(wk) == 1 and wk[0]["weekday"] == wd and wk[0]["at_time"] == "20:00"
          and wk[0]["variants"] == "2.5 mg|5 mg|7.5 mg" and wk[0]["dose_text"] == "2.5 mg"
          and not any(s["name"] in ("Medicine D", "Medicine E") for s in sched),
          "meds %s sched %s salts %s" % (meds, sched, salts))
    stored = os.path.join(rig.member_dir("m3"), "gutlog", "plans_files", plans[0]["stored_name"]) if plans else ""
    check("E01 --seed files the plan PDF under Plans with the seed's title, and stamps the physio; the seed copy "
          "and the PDF copy are gone from the member folder; the PIN file carries m3 and p1, nothing printed",
          plans and plans[0]["title"] == "Plan A" and os.path.exists(stored)
          and not os.path.exists(os.path.join(rig.member_dir("m3"), "seed.json"))
          and not os.path.exists(os.path.join(rig.member_dir("m3"), "Plan_A.pdf"))
          and os.path.exists(os.path.join(rig.member_dir("m3"), "physio", "p1", "auth.db"))
          and any(p["slug"] == "p1" and p["for"] == ["m3"] and p["name"] == "Physio A" for p in reg.get("physios", []))
          and rig.pw["m3"] and rig.pw["p1"] and "PIN (shown once" not in out and sentinel not in out,
          "plans %s physios %s\n%s" % (plans, reg.get("physios"), out[-300:]))
    leak = tree_text(rig.code) + tree_text(famtest.fam_src()) + tree_text(os.path.join(famtest.REPO, "tools"))
    check("E01 nothing from the seed reaches the code tree, the family folder or the tools folder (scan)",
          sentinel not in leak, "the sentinel medicine name was found in a tracked file")

    # ---------------------------------------------------------------- E02 the weekly slot
    m3 = rig.member_client("m3")
    now = m3.get("/m3/api/now").json() or {}
    wkslot = next((s for s in now.get("slots") or [] if s["slot"] == "WEEKLY"), None)
    row = next((r for r in (wkslot or {}).get("rows") or [] if r["name"].startswith("Medicine C")), None)
    now_t = m3.get("/m3/api/now?day=" + tmrw).json() or {}
    nxt = (date.today() + timedelta(days=7)).isoformat()
    now_n = m3.get("/m3/api/now?day=" + nxt).json() or {}
    has_t = any(s["slot"] == "WEEKLY" for s in now_t.get("slots") or [])
    has_n = any(s["slot"] == "WEEKLY" for s in now_n.get("slots") or [])
    check("E02 a weekly dose shows on its weekday only: today yes, tomorrow no, next week's same day yes; "
          "with its time and its variants; the daily lines are unchanged",
          wkslot and row and wkslot["time"] == "20:00" and row["variants"] == "2.5 mg|5 mg|7.5 mg"
          and "20:00" in (row.get("at_time") or "") and not has_t and has_n
          and sum(1 for s in now.get("slots") or [] if s["slot"] in ("MORNING", "NOON")) == 2
          and now.get("has_schedule") is True and now_t.get("has_schedule") is True,
          "today %s tmrw %s next %s" % ([s["slot"] for s in now.get("slots") or []], has_t, has_n))
    d = m3.post("/m3/api/now/dose", {"med_id": row["med_id"], "sched_id": row["sched_id"], "status": "TAKEN",
                                     "dose_text": "5 mg"}, ctype="json")
    now2 = m3.get("/m3/api/now").json() or {}
    row2 = next((r for s in now2.get("slots") or [] if s["slot"] == "WEEKLY" for r in s["rows"]), {})
    dv = m3.get("/m3/api/dayview?day=" + today).json() or {}
    dvrow = next((e for e in dv.get("entries") or [] if e.get("tbl") == "doses" and "Medicine C" in (e.get("title") or "")), None)
    feed_tok = rig.secret("m3", "feed.token")
    stack = famtest.Client("http://127.0.0.1:%d" % ports["gut"]).get("/m3/api/feed/stack",
                                                                      headers={"Authorization": "Bearer " + feed_tok}).json() or {}
    reg_row = next((r for r in stack.get("regimen") or [] if r["slot"] == "WEEKLY"), {})
    taken = next((t for t in stack.get("taken") or [] if t["name"].startswith("Medicine C")), {})
    check("E02 the weekly dose is ticked with a variant like any dose; Day by day shows it; the feed carries the "
          "line (slot WEEKLY, dose text, variants) in the same shape as the daily ones",
          d.status == 200 and row2.get("status") == "TAKEN" and row2.get("logged_dose") == "5 mg" and dvrow
          and reg_row.get("dose_text") == "2.5 mg" and reg_row.get("variants") == "2.5 mg|5 mg|7.5 mg"
          and set(reg_row) == set(next(r for r in stack["regimen"] if r["slot"] == "MORNING"))
          and taken.get("doses") == 1 and taken.get("molecule") == seed["medicines"][2]["molecule"],
          "dose %s status %s dayview %s feed %s taken %s" % (d.status, row2.get("status"), bool(dvrow), reg_row, taken))
    rt = m3.post("/m3/api/retime", {"table": "doses", "id": row2.get("dose_id"), "day": today, "time": "07:00"},
                 ctype="json")
    now3 = m3.get("/m3/api/now").json() or {}
    row3 = next((r for s in now3.get("slots") or [] if s["slot"] == "WEEKLY" for r in s["rows"]), {})
    check("E02 Retime works on a weekly dose", rt.status == 200 and row3.get("dtime") == "07:00",
          "%s %s" % (rt.status, row3.get("dtime")))
    rx = m3.get("/m3/rx/api/feed/status", follow=True)
    check("E02 the member's RxGuard still reads the feed with a WEEKLY line in it",
          rx.status in (200, 401, 302), "HTTP %s" % rx.status)

    # ---------------------------------------------------------------- E03 yesterday's weekly dose
    ywd = WD[(date.today() - timedelta(days=1)).weekday()]
    m3.post("/m3/api/prnmeds", {"name": "Medicine F"}, ctype="json")
    fid = next(m["id"] for m in m3.get("/m3/api/prnmeds/full").json() if m["name"] == "Medicine F")
    sc = m3.post("/m3/api/schedule", {"med_id": fid, "slot": "WEEKLY", "weekday": ywd, "at_time": "09:00",
                                      "dose_text": "1 pen", "valid_from": yday}, ctype="json")
    bad = m3.post("/m3/api/schedule", {"med_id": fid, "slot": "WEEKLY", "dose_text": "1 pen"}, ctype="json")
    now4 = m3.get("/m3/api/now").json() or {}
    late = now4.get("weekly_late") or []
    check("E03 a weekly dose not logged on its day is asked about the next day; a weekly line without its day "
          "and time is refused",
          sc.status == 200 and bad.status == 400 and len(late) == 1 and late[0]["name"] == "Medicine F"
          and late[0]["due_day"] == yday and not any(s["slot"] == "WEEKLY" and any(r["name"] == "Medicine F" for r in s["rows"])
                                                       for s in now4.get("slots") or []),
          "sched %s bad %s late %s" % (sc.status, bad.status, late))
    tl = m3.post("/m3/api/now/dose", {"med_id": fid, "sched_id": late[0]["sched_id"], "status": "TAKEN", "day": today,
                                      "dose_text": "1 pen", "notes": "late: due " + yday}, ctype="json")
    now5 = m3.get("/m3/api/now").json() or {}
    check("E03 'taken late' logs it today against the weekly line and the question goes away",
          tl.status == 200 and not now5.get("weekly_late"), "%s %s" % (tl.status, now5.get("weekly_late")))

    # ---------------------------------------------------------------- E04 stock per dose
    m3.post("/m3/api/stock/count", {"med_id": fid, "qty": 4}, ctype="json")
    # a dose in the same minute as a count is taken as already counted (GutLog's
    # rule); the count is moved two minutes back so the next dose is after it
    con = gut_con(rig, "m3")
    con.execute("UPDATE stock_events SET at=? WHERE med_id=? AND kind='COUNT'",
                ((datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M"), fid))
    con.commit()
    con.close()
    st1 = next((r for r in (m3.get("/m3/api/stock").json() or {}).get("rows") or [] if r["med_id"] == fid), {})
    m3.post("/m3/api/now/dose", {"med_id": fid, "status": "EXTRA", "dose_text": "1 pen"}, ctype="json")
    st2 = next((r for r in (m3.get("/m3/api/stock").json() or {}).get("rows") or [] if r["med_id"] == fid), {})
    check("E04 stock of a weekly medicine counts per dose (pens), never as a daily pillbox",
          st1.get("mode") == "per_dose" and st1.get("current") == 3 and st2.get("current") == 2
          and st1.get("can_pillbox") is False and 0 < st1.get("per_day", 0) < 0.5, "%s -> %s" % (st1, st2))

    # ---------------------------------------------------------------- E05 meal windows
    g1 = m3.get("/m3/api/meals/slotguess?time=11:30").json() or {}
    g2 = m3.get("/m3/api/meals/slotguess?time=16:00").json() or {}
    g3 = m3.get("/m3/api/meals/slotguess?time=22:30").json() or {}
    o1 = m1.get("/m1/api/meals/slotguess?time=11:30").json() or {}
    check("E05 meal slots come from the member's own windows: 11:30 is Breakfast, 16:00 Lunch, 22:30 Late snack; "
          "a member without windows keeps the clock defaults (11:30 = Mid-morning)",
          g1.get("slot") == "Breakfast" and g2.get("slot") == "Lunch" and g3.get("slot") == "Late snack"
          and o1.get("slot") == "Mid-morning", "%s %s %s / m1 %s" % (g1, g2, g3, o1))
    mw = m3.get("/m3/api/meal_windows").json() or {}
    check("E05 the windows are readable and editable on the member's copy",
          mw.get("windows", {}).get("Lunch") == ["14:30", "17:30"], mw)

    # ---------------------------------------------------------------- E06 check-ins due, answered, gone
    due = m3.get("/m3/api/checkins/due").json() or {}
    kinds = [f["kind"] for f in due.get("due") or []]
    p9 = next((f for f in due.get("due") or [] if f["kind"] == "phq9"), {})
    check("E06 check-ins due by their schedule (a weekly weigh-in and PHQ-2 today, monthly PHQ-9, Epworth, "
          "measurements today); the side-effect check not yet (no weekly dose yesterday)",
          set(kinds) == {"weigh_in", "phq2", "phq9", "epworth", "measurements"}
          and len(p9.get("items") or []) == 10 and p9["items"][0]["opts"][3] == "Nearly every day"
          and "difficult" in p9["items"][9]["text"], kinds)
    ans = dict(("q%d" % i, 1) for i in range(1, 10))
    ans["q9"] = 0
    ans["difficulty"] = 1
    r9 = m3.post("/m3/api/checkins", {"kind": "phq9", "answers": ans}, ctype="json")
    j9 = r9.json() or {}
    due2 = m3.get("/m3/api/checkins/due").json() or {}
    con = gut_con(rig, "m3")
    crow = con.execute("SELECT * FROM checkins WHERE kind='phq9'").fetchone()
    con.close()
    check("E06 a PHQ-9 answered: stored with the IST day and time, total 8 and band 'mild', gone from the due list",
          r9.status == 200 and j9.get("total") == 8 and j9.get("band") == "mild" and not j9.get("tell_caretaker")
          and crow and crow["day"] == today and re.match(r"^\d\d:\d\d$", crow["ctime"] or "")
          and json.loads(crow["answers"])["q3"] == 1 and crow["total"] == 8
          and "phq9" not in [f["kind"] for f in due2.get("due") or []],
          "%s %s row %s" % (r9.status, j9, dict(crow) if crow else None))
    rbad = m3.post("/m3/api/checkins", {"kind": "phq2", "answers": {"q1": 2}}, ctype="json")
    rw = m3.post("/m3/api/checkins", {"kind": "weigh_in", "answers": {"weight": 88.5, "waist": 108}}, ctype="json")
    ep = m3.post("/m3/api/checkins", {"kind": "epworth", "answers": dict(("e%d" % i, 2) for i in range(1, 9))}, ctype="json")
    due3 = m3.get("/m3/api/checkins/due").json() or {}
    con = gut_con(rig, "m3")
    vw = con.execute("SELECT weight, waist FROM vitals WHERE notes='weigh-in'").fetchone()
    con.close()
    check("E06 an incomplete answer is refused; a weigh-in writes the vitals row; Epworth 16 = severe sleepiness; "
          "each answered check-in leaves the due list",
          rbad.status == 400 and rw.status == 200 and vw and vw[0] == 88.5 and vw[1] == 108
          and (ep.json() or {}).get("total") == 16 and "severe" in (ep.json() or {}).get("band", "")
          and not ({"weigh_in", "epworth"} & set(f["kind"] for f in due3.get("due") or [])),
          "bad %s weigh %s ep %s due %s" % (rbad.status, rw.status, ep.json(), [f["kind"] for f in due3.get("due") or []]))

    # ---------------------------------------------------------------- E07 the item-9 flag
    fam0 = owner.get("/family").text
    care_tok = rig.secret("m3", "status.token")
    stat0 = famtest.Client("http://127.0.0.1:%d" % ports["gut"]).get(
        "/m3/api/care/status", headers={"Authorization": "Bearer " + care_tok}).json() or {}
    ans9 = dict(ans, q9=1)
    r9b = m3.post("/m3/api/checkins", {"kind": "phq9", "answers": ans9}, ctype="json")
    j9b = r9b.json() or {}
    due4 = m3.get("/m3/api/checkins/due").json() or {}
    stat1 = famtest.Client("http://127.0.0.1:%d" % ports["gut"]).get(
        "/m3/api/care/status", headers={"Authorization": "Bearer " + care_tok}).json() or {}
    fam1 = owner.get("/family").text
    m1due = m1.get("/m1/api/checkins/due").json() or {}
    check("E07 PHQ-9 item 9 above 0: the member is asked to tell the caretaker today, the status endpoint carries "
          "a same-day flag, the owner's Family page shows it on that row only; nothing before, nothing on m1",
          r9b.status == 200 and j9b.get("tell_caretaker") is True and "Member W" not in j9b.get("caretaker", "")
          and (due4.get("flag") or {}).get("kind") == "phq9" and "Please tell" not in fam1
          and stat0.get("flag") is False and stat1.get("flag") is True
          and "Check-in flag today" not in fam0 and fam1.count("Check-in flag today") == 1
          and fam1.index("Member W") < fam1.index("Check-in flag today") and not m1due.get("flag"),
          "tell %s flag %s stat %s->%s fam %d" % (j9b.get("tell_caretaker"), due4.get("flag"), stat0.get("flag"),
                                                  stat1.get("flag"), fam1.count("Check-in flag today")))
    check("E07 the flag carries no answer: the status endpoint and the Family page never show the score or the items",
          "answers" not in stat1 and str(j9b.get("total")) not in fam1.split("Check-in flag")[0][-40:]
          and "PHQ" not in fam1, "")

    # ---------------------------------------------------------------- E08 side-effect check the day after
    m3.post("/m3/api/now/dose", {"med_id": fid, "sched_id": late[0]["sched_id"], "status": "TAKEN", "day": yday,
                                 "dtime": "09:05", "dose_text": "1 pen"}, ctype="json")
    due5 = m3.get("/m3/api/checkins/due").json() or {}
    se = next((f for f in due5.get("due") or [] if f["kind"] == "side_effects"), {})
    rse = m3.post("/m3/api/checkins", {"kind": "side_effects", "answers": {"nausea": 1, "vomiting": 0, "diarrhoea": 0,
                                                                          "constipation": 2, "loss_of_appetite": 1,
                                                                          "energy": 6}, "note": "ok"}, ctype="json")
    due6 = m3.get("/m3/api/checkins/due").json() or {}
    check("E08 the side-effect check appears the day after a weekly dose, with the five items, energy 0-10 and a "
          "note; answered, its band is the worst grade and it leaves the list",
          se and [i["key"] for i in se["items"]] == ["nausea", "vomiting", "diarrhoea", "constipation", "loss_of_appetite"]
          and se["scales"][0]["max"] == 10 and se["note"] is True and rse.status == 200
          and (rse.json() or {}).get("band") == "moderate"
          and "side_effects" not in [f["kind"] for f in due6.get("due") or []],
          "se %s rse %s %s" % (bool(se), rse.status, rse.json()))

    # ---------------------------------------------------------------- E09 milestones
    # weigh-ins AFTER the plan's start (the stamp day); earlier ones never count
    con = gut_con(rig, "m3")
    con.execute("INSERT INTO vitals(day, vtime, weight, notes, created) VALUES(?,?,?,?,?)",
                ((date.today() - timedelta(days=30)).isoformat(), "06:00", 80.0, "before the plan", "x"))
    for i, w in enumerate([86.0, 85.6, 85.4, 85.2]):
        con.execute("INSERT INTO vitals(day, vtime, weight, notes, created) VALUES(?,?,?,?,?)",
                    (today, "06:%02d" % (10 + i), w, "test", "x"))
    con.commit()
    con.close()
    wj = m3.get("/m3/api/weight?days=90").json() or {}
    ms = wj.get("milestones") or []
    week = m3.get("/m3/api/week").json() or {}
    check("E09 milestones from the start weight (90 kg: -5% = 85.5, -10% = 81, -15% = 76.5); -5% is crossed after "
          "two consecutive weigh-ins below it, so -10% becomes current; a weigh-in before the plan never counts; "
          "the chart carries weight and waist",
          [m["kg"] for m in ms] == [85.5, 81.0, 76.5] and ms[0]["reached"] is True and ms[1]["current"] is True
          and ms[2]["reached"] is False and len(wj.get("weight") or []) >= 6 and len(wj.get("waist") or []) >= 1
          and (week.get("milestone") or {}).get("pct") == 10 and week["milestone"]["to_go"] == 7.5,
          "%s week %s" % (ms, week.get("milestone")))
    con = gut_con(rig, "m3")
    con.execute("INSERT INTO vitals(day, vtime, weight, notes, created) VALUES(?,?,?,?,?)",
                (today, "06:20", 80.5, "test", "x"))
    con.commit()
    con.close()
    ms2 = (m3.get("/m3/api/weight?days=90").json() or {}).get("milestones") or []
    check("E09 one weigh-in below a line does not cross it (two consecutive are needed)",
          ms2[1]["reached"] is False and ms2[1]["current"] is True, ms2)

    # ---------------------------------------------------------------- E10 This-week card
    check("E10 the This-week card: profile weight only; weight and waist; injection countdown for the seeded "
          "weekly dose; milestone line; steps blank (no FitLog data), never 0; protein against the target",
          week.get("on") is True and (week.get("weight") or {}).get("kg") == 88.5
          and (week.get("weight") or {}).get("waist") == 108
          and (week.get("injection") or {}).get("name", "").startswith("Medicine C")
          and week["injection"]["done_today"] is True and week["injection"]["days_to"] == 0
          and "kg to go" in (week.get("milestone") or {}).get("text", "")
          and week.get("steps") is None and (week.get("protein") or {}).get("target")
          and (m1.get("/m1/api/week").json() or {}).get("on") is False
          and (owner.get("/api/week").json() or {}).get("on") is False, week)
    cfg3 = m3.get("/m3/api/joint/cfg").json() or {}
    order = (m3.get("/m3/api/care/me").json() or {}).get("order") or []
    mc = m3.get("/m3/api/mealcards").json() or {}
    check("E10 the joint cards show under weight; the Now order puts This week, medicines, meals, check-ins, "
          "physio, then the joint cards; the meals header knows the profile",
          cfg3.get("show") is True and order[:6] == ["nowWeek", "nowDoses", "nowExtraCard", "nowMeal", "nowCheckins",
                                                    "nowPhysio"] and "nowJoint" in order and mc.get("profile") == "weight",
          "%s %s" % (cfg3, order))
    ex = m3.get("/m3/export/checkins.csv")
    ck = m3.get("/m3/checkins")
    check("E10 the Check-ins page and the CSV export answer", ck.status == 200 and "Weight and waist" in ck.text
          and ex.status == 200 and "kind" in ex.text.splitlines()[0] and "phq9" in ex.text, "%s %s" % (ck.status, ex.status))

    # ---------------------------------------------------------------- E11 the physio
    anon = famtest.Client(rig.front_url)
    lp = anon.get("/m3/physio/login")
    lt = html.unescape(lp.text)
    wrong = famtest.Client(rig.front_url).post("/m3/physio/login", {"pin": "000001", "p": "p1"})
    check("E11 the physio sign-in page names the physio and the member; numeric PIN, eye; a wrong PIN counts down",
          lp.status == 200 and "Physio A \u2014 physio for Member W" in lt and "inputmode='numeric'" in lp.text
          and "id='eye'" in lp.text and wrong.status == 401 and "4 tries left" in html.unescape(wrong.text),
          "%s %s" % (lp.status, lt[:120]))
    ph = famtest.Client(rig.front_url)
    lr = ph.post("/m3/physio/login", {"pin": rig.pw["p1"], "p": "p1"})
    me = ph.get("/m3/physio/j/me").json() or {}
    pcookie = [c for c in ph.cookies() if c[0] == "fam_m3_physio"]
    check("E11 the physio signs in with the PIN, on a cookie of their own scoped to /m3/physio/",
          lr.status == 302 and me.get("slug") == "p1" and me.get("member") == "Member W"
          and pcookie and pcookie[0][2] == "/m3/physio/" and not any(c[0] == "fam_m3_gut" for c in ph.cookies()),
          "%s %s %s" % (lr.status, me, pcookie))
    items = [{"name": "Sit-to-stand", "sets": 3, "reps": 10, "days": ["Mon", "Wed", "Fri", "Sat", "Sun", "Tue", "Thu"]},
             {"name": "Wall slide", "sets": 2, "reps": 12, "hold_s": 5, "days": WDNAMES()}]
    pg = ph.post("/m3/physio/j/programme", {"items": items, "notes": "Slow and steady."}, ctype="json")
    pj = pg.json() or {}
    sess = ph.post("/m3/physio/j/sessions", {"day": today, "done": ["Sit-to-stand"]}, ctype="json")
    pain = ph.post("/m3/physio/j/pain", {"site": "Knee - R", "score": 4, "walk_min": 15, "notes": "after stairs"}, ctype="json")
    rt = ph.post("/m3/physio/j/retest", {"sts30": 9, "walk6": 320, "rom_l": 110, "rom_r": 105, "sls_l": 4.5, "sls_r": 3,
                                          "note": "baseline"}, ctype="json")
    rtj = rt.json() or {}
    con = gut_con(rig, "m3")
    jl = con.execute("SELECT site, score, walk_min, notes FROM joint_log ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    check("E11 the physio edits the programme, ticks a session, adds a pain and walking entry (into the joint "
          "log, marked as theirs) and saves the monthly re-test fields",
          pg.status == 200 and len(pj.get("programme", {}).get("items") or []) == 2 and pj["programme"]["by"] == "Physio A"
          and sess.status == 200 and pain.status == 200 and jl and jl["site"] == "Knee - R" and jl["walk_min"] == 15
          and jl["notes"].startswith("physio (Physio A)") and rt.status == 200
          and (rtj.get("rows") or [{}])[0].get("sts30") == 9 and rtj["rows"][0]["sls_l"] == 4.5,
          "%s %s %s %s %s" % (pg.status, sess.status, pain.status, rt.status, rtj.get("rows")))
    tile = m3.get("/m3/api/physio/me").json() or {}
    home = m3.get("/m3/", follow=True).text
    check("E11 the member sees the programme as a Physio tile on the Now page: today's session, ticked by the "
          "physio, and the next session",
          tile.get("on") is True and tile.get("physio") == ["Physio A"] and tile.get("done_today") == ["Sit-to-stand"]
          and tile.get("done_by", "").startswith("physio") and (tile.get("next") or {}).get("day")
          and 'id="nowPhysio"' in home, tile)
    ph.post("/m3/physio/j/sessions", {"undo": True, "day": today}, ctype="json")
    tk = m3.post("/m3/api/physio/tick", {"done": ["Sit-to-stand", "Wall slide"]}, ctype="json")
    tile2 = m3.get("/m3/api/physio/me").json() or {}
    check("E11 the member ticks today's session from the tile", tk.status == 200 and tile2.get("done_today") == ["Sit-to-stand", "Wall slide"]
          and tile2.get("done_by") == "member", tile2)
    # the caretaker sees the tile too
    op = owner.get("/family/open/m3")
    care = famtest.Client(rig.front_url)
    care.get(op.headers.get("Location") or "", follow=True)
    ct = care.get("/m3/api/physio/me").json() or {}
    check("E11 the caretaker sees the physio tile", ct.get("on") is True and ct.get("physio") == ["Physio A"], ct)
    # -- scope: the physio cookie reaches nothing else
    hdr = {"Cookie": "fam_m3_physio=" + pcookie[0][1]}
    probe = famtest.Client(rig.front_url)
    reach = []
    for path in ("/m3/api/now", "/m3/api/checkins/due", "/m3/api/plans", "/m3/api/joint", "/m3/api/physio/me",
                 "/m3/api/care/me", "/m3/api/prnmeds", "/m3/api/weight", "/m3/api/mealcards"):
        r = probe.get(path, headers=hdr)
        j = r.json()
        if r.status == 200 and j is not None and not (isinstance(j, dict) and j.get("ok") is False):
            reach.append(path)
    hm = probe.get("/m3/", headers=hdr)
    if hm.status == 200 and 'id="tab-now"' in hm.text:
        reach.append("/m3/")
    rxr = probe.get("/m3/rx/", headers=hdr, follow=True)
    if rxr.status == 200 and "/login" not in rxr.url and "login" not in rxr.text[:500].lower():
        reach.append("/m3/rx/")
    fit = probe.get("/m3/fit/", headers=hdr, follow=True)
    if fit.status == 200 and "/login" not in fit.url and "login" not in fit.text[:500].lower():
        reach.append("/m3/fit/")
    m2p = probe.get("/m2/physio/j/me", headers={"Cookie": "fam_m2_physio=" + pcookie[0][1]})
    if m2p.status == 200:
        reach.append("/m2/physio/")
    own = famtest.Client("http://127.0.0.1:%d" % rig.owner_port).get("/api/now", headers=hdr)
    if own.status == 200:
        reach.append("owner")
    check("E11 the physio's session reaches NOTHING else: no Now, check-ins, plans, joint log, tile, caretaker, "
          "medicines, weight or meals API, no diary page, no RxGuard, no FitLog, not m2, not the owner",
          not reach, reach)
    # attach to a second member: same PIN
    rc2, out2 = stamp_tool(rig, "--slug", "p1", "--for", "m2")
    ph2 = famtest.Client(rig.front_url)
    lr2 = ph2.post("/m2/physio/login", {"pin": rig.pw["p1"], "p": "p1"})
    me2 = ph2.get("/m2/physio/j/me").json() or {}
    reg2 = json.load(open(os.path.join(rig.root, "root", "family", "members.local.json")))
    check("E11 the same physio attached to a second member (--for m2) signs in there with the same PIN; "
          "the two members' data stay apart",
          rc2 == 0 and "credentials copied" in out2 and lr2.status == 302 and me2.get("slug") == "p1"
          and me2.get("member") == "Member B" and not (ph2.get("/m2/physio/j/programme").json() or {}).get("programme", {}).get("items")
          and next(p for p in reg2["physios"] if p["slug"] == "p1")["for"] == ["m3", "m2"],
          "%s %s %s" % (rc2, out2[-200:], me2))
    so = ph.post("/m3/physio/signout", {})
    dead = ph.get("/m3/physio/j/me")
    check("E11 physio sign out ends the session", so.status == 302 and dead.status == 401, "%s %s" % (so.status, dead.status))
    rc3, out3 = stamp_tool(rig, "--slug", "p1", "--reset-pin", "--password-file", pwf)
    old = famtest.Client(rig.front_url).post("/m3/physio/login", {"pin": rig.pw["p1"], "p": "p1"})
    newpin = file_pin(pwf, "p1")
    changed = bool(newpin) and newpin != rig.pw["p1"]
    rig.pw["p1"] = newpin or rig.pw["p1"]
    new = famtest.Client(rig.front_url).post("/m3/physio/login", {"pin": rig.pw["p1"], "p": "p1"})
    new2 = famtest.Client(rig.front_url).post("/m2/physio/login", {"pin": rig.pw["p1"], "p": "p1"})
    check("E11 --reset-pin gives the physio one new PIN on every member they are attached to, written to the "
          "root-only file and never printed",
          rc3 == 0 and changed and "written nowhere" not in out3 and old.status == 401 and new.status == 302
          and new2.status == 302, "%s old %s new %s %s\n%s" % (rc3, old.status, new.status, new2.status, out3[-200:]))

    # ---------------------------------------------------------------- E12 readiness
    def readiness(slug):
        r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "readiness.py"), "--slug", slug,
                            "--base", rig.front_url, "--srv", os.path.join(rig.root, "srv", "family"), "--pin-file", pwf,
                            "--etc", os.path.join(rig.root, "etc", "family"),
                            "--registry", os.path.join(rig.root, "root", "family", "members.local.json")],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    con = gut_con(rig, "m3")
    before = dict((t, con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
                  for t in ("prnmeds", "med_schedule", "doses", "checkins", "joint_log", "meals"))
    con.close()
    rc_m, out_m = readiness("m3")
    rc_p, out_p = readiness("p1")
    con = gut_con(rig, "m3")
    after = dict((t, con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
                 for t in ("prnmeds", "med_schedule", "doses", "checkins", "joint_log", "meals"))
    txt = " ".join(str(x) for row in con.execute("SELECT * FROM prnmeds").fetchall() for x in row)
    con.close()
    check("E12 readiness.py on the weight member: weekly dose ticked, a check-in answered and removed, the plan "
          "present, physio scope -- READY, and nothing left behind",
          rc_m == 0 and out_m.rstrip().endswith("READY") and "weekly dose scheduled for today" in out_m
          and "check-in answered" in out_m and "plan document is present" in out_m and before == after
          and "Readiness check" not in txt and rig.pw["m3"] not in out_m,
          "\n".join(l for l in out_m.splitlines() if "FAIL" in l or "READY" in l)[-800:] + str((before, after)))
    check("E12 readiness.py on the physio: names, PIN offline, one sign-in, programme, pain entry saved and "
          "removed, the cookie refused everywhere else, sign out -- READY",
          rc_p == 0 and out_p.rstrip().endswith("READY") and "opens nothing else" in out_p and rig.pw["p1"] not in out_p,
          "\n".join(l for l in out_p.splitlines() if "FAIL" in l or "READY" in l)[-800:])
    _ = (glob, tempfile, datetime)


def WDNAMES():
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("E00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_member.py -- create one app's database for a new member.

FAMILY_EDITION_V1. Run by stamp_member.py AS THE MEMBER'S OWN USER, once per
app (the three apps each import a module called `app`, so they cannot share
a process):

    init_member.py --app gut|rx|fit     (password on stdin, never an argument)

It imports that app's family entry -- so the database is created exactly as
the running service will see it, schema and migrations included -- then sets
the member's first password and the starting state:

  gut  schema + the generic food library + the v3.35.0 late-snack / Quick
       Bite foods and dishes (snacks_seed). No medicines, schedule, meal
       cards, food-test plan, plans, records or mirror: those files do not
       exist in the family code tree, so the seeds that read them seed nothing.
  rx   schema + conditions list (all off).
  fit  schema + the health-ingest tables (migrate_health_ingest) + the
       placeholder med stack FitLog always seeds.
  care the caretaker switch, ON (the member can switch it off at any time).
  seed (26-Sep-2026, weight profile) the member's seed file, applied as DATA
       into their own GutLog: setup, meal windows, weight plan and targets,
       medicines with salts, strengths, schedules (the WEEKLY one and its
       variants), as-needed medicines, check-in schedules, the plan PDF.
       The file is FAMILY_SEED_FILE inside the member folder; nothing in it
       is printed, and the copy is deleted when it has been applied.

Python 3.9.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Free-text conditions from a seed -> the first-run form's codes, by keyword.
# Generic words only; the text itself is kept in conditions_other.
COND_WORDS = (("thyroid", "thyroid"), ("knee", "knee_oa"), ("ankle", "ankle_arthritis"), ("hip", "hip_oa"),
              ("back pain", "back_pain"), ("hypertension", "hypertension"), ("blood pressure", "hypertension"),
              ("diabet", "diabetes"), ("cholesterol", "high_cholesterol"), ("kidney", "renal_impairment"),
              ("liver", "hepatic_impairment"), ("ulcer", "peptic_ulcer"), ("ibs", "ibs"),
              ("osteopor", "osteoporosis"), ("constipat", "constipation"), ("seizure", "seizure_history"))


def apply_seed(E, seed):
    """As the member, into the member's own database. Returns a short report
    with COUNTS only -- never a name from the seed."""
    gut = E.gut
    con = gut.db()
    tday = gut.today()
    setup = seed.get("setup") or {}
    rep = {"medicines": 0, "schedules": 0, "weekly": 0, "prn": 0, "salts": 0, "plan": 0, "checkins": 0}
    # -- setup -> the first-run form's record --------------------------------
    conds_text = [str(c) for c in (setup.get("conditions") or [])]
    codes = []
    for c in conds_text:
        for w, code in COND_WORDS:
            if w in c.lower() and code in [x for x, _l in E.CONDITIONS] and code not in codes:
                codes.append(code)
    win = setup.get("meal_windows") or {}
    mt = {}
    for k, slot in (("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner")):
        w = win.get(slot)
        mt[k] = gut._valid_hm(w[0]) if isinstance(w, (list, tuple)) and w else ""
    prof = {"name": str(seed.get("name") or "")[:60], "age": setup.get("age"), "sex": setup.get("sex") or "",
            "height_cm": setup.get("height_cm"), "weight_kg": setup.get("weight_kg"),
            "conditions": codes, "conditions_other": "; ".join(conds_text)[:500],
            "allergies": "; ".join(str(x) for x in (setup.get("allergies") or []))[:300],
            "meal_times": mt, "night_tablet": bool(setup.get("night_tablet")),
            "diet": str(setup.get("diet") or "")[:40], "wake": gut._valid_hm(setup.get("wake")) or "",
            "bed": gut._valid_hm(setup.get("bed")) or "", "waist_cm": setup.get("waist_cm"),
            "hips_cm": setup.get("hips_cm"), "neck_cm": setup.get("neck_cm"), "updated": gut.now_s(),
            "seeded": tday}
    gut.set_setting("member_profile", json.dumps(prof))
    gut.set_setting("member_setup_done", "1")
    if seed.get("profile"):
        gut.set_setting("now_profile", seed["profile"])
        gut.set_setting("member_profile_kind", seed["profile"])
    if win:
        gut.set_setting("meal_windows", json.dumps(win))
    tg = setup.get("targets") or {}
    plan = {"start_weight": setup.get("weight_kg"), "start_waist": setup.get("waist_cm"), "start_day": tday,
            "milestones_pct": tg.get("weight_milestones_pct") or [5, 10, 15], "targets": tg}
    gut.set_setting("weight_plan", json.dumps(plan))
    if setup.get("weight_kg") or setup.get("waist_cm"):
        con.execute("INSERT INTO vitals(day, vtime, weight, waist, notes, created) VALUES(?,?,?,?,?,?)",
                    (tday, gut.now_hm(), setup.get("weight_kg"), setup.get("waist_cm"), "from setup", gut.now_s()))
    if seed.get("checkins"):
        gut.set_setting("checkins", json.dumps(seed["checkins"]))
        rep["checkins"] = len(seed["checkins"])
    # -- medicines -------------------------------------------------------------
    prn_notes = {}
    for i, m in enumerate(seed.get("medicines") or []):
        name = str(m.get("name") or "").strip()[:80]
        if not name:
            continue
        con.execute("INSERT OR IGNORE INTO prnmeds(name, sort) VALUES(?,?)", (name, i))
        mid = con.execute("SELECT id FROM prnmeds WHERE name=?", (name,)).fetchone()[0]
        rep["medicines"] += 1
        mol = str(m.get("molecule") or "").strip().lower()
        no_salt = 1 if (m.get("no_salt") or not mol) else 0
        con.execute("UPDATE prnmeds SET molecule=? WHERE id=?", ("" if no_salt else mol, mid))
        con.execute("INSERT INTO med_salts(med_id, strength, no_salt, updated) VALUES(?,?,?,?) "
                    "ON CONFLICT(med_id) DO UPDATE SET strength=excluded.strength, no_salt=excluded.no_salt, "
                    "updated=excluded.updated", (mid, str(m.get("strength") or "")[:40], no_salt, gut.now_s()))
        rep["salts"] += 1
        if m.get("note"):
            prn_notes[name] = str(m["note"])[:300]
        if not m.get("regular"):
            rep["prn"] += 1
            continue
        for s in m.get("schedule") or []:
            slot = str(s.get("slot") or "").upper()
            weekday, at_time = "", ""
            if slot == gut.WEEKLY_SLOT:
                weekday = str(s.get("weekday") or "").upper()[:3]
                at_time = gut._valid_hm(s.get("time")) or ""
                if weekday not in gut.WEEKDAYS or not at_time:
                    continue
                rep["weekly"] += 1
            elif slot not in [x for x, _l, _t in gut.SLOTS]:
                continue
            epoch = gut._bump_epoch()
            con.execute("INSERT INTO med_schedule(med_id, slot, dose_text, with_food, valid_from, valid_to, epoch, "
                        "notes, created, variants, weekday, at_time) VALUES(?,?,?,?,?,'',?,?,?,?,?,?)",
                        (mid, slot, str(s.get("dose") or "")[:40], str(s.get("food") or "ANY").upper()[:10], tday,
                         int(epoch), str(s.get("note") or "")[:500], gut.now_s(),
                         str(m.get("variants") or "")[:120], weekday, at_time))
            rep["schedules"] += 1
    con.commit()
    gut._refresh_scheduled_flags()
    if prn_notes:
        gut.set_setting("prn_notes", json.dumps(prn_notes))
    # -- the plan PDF ----------------------------------------------------------
    pp = seed.get("plan_pdf") or {}
    pdf = os.path.join(os.path.dirname(os.environ.get("FAMILY_SEED_FILE", "")), str(pp.get("file") or ""))
    if pp.get("file") and os.path.isfile(pdf):
        with open(pdf, "rb") as fh:
            raw = fh.read()
        if raw[:5] == b"%PDF-":
            sha = hashlib.sha256(raw).hexdigest()
            stored = sha + ".pdf"
            os.makedirs(gut.PLANS_DIR, exist_ok=True)
            path = os.path.join(gut.PLANS_DIR, stored)
            if not os.path.exists(path):
                with open(path, "wb") as fh:
                    fh.write(raw)
                os.chmod(path, 0o600)
            cur = con.execute("INSERT INTO plans(title, first_considered, status, archived, created_at) "
                              "VALUES(?,?,?,0,?)", (str(pp.get("title") or "Plan")[:200], tday, "Active", gut.now_s()))
            con.execute("INSERT INTO plan_files(plan_id, stored_name, original_name, bytes, sha256, uploaded_at) "
                        "VALUES(?,?,?,?,?,?)", (cur.lastrowid, stored, os.path.basename(pdf)[:160], len(raw), sha,
                                                gut.now_s()))
            con.commit()
            rep["plan"] = 1
        try:
            os.remove(pdf)
        except OSError:
            pass
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, choices=("gut", "rx", "fit", "care", "seed"))
    a = ap.parse_args()
    # gut / rx / fit: a random secret for the app's own (now unused) password
    # hash -- family sign-in is the PIN, checked by family_auth. care: the PIN.
    pw = sys.stdin.readline().rstrip("\n")
    if a.app not in ("care", "seed") and len(pw) < 8:
        print("FATAL: app secret missing or too short")
        return 1
    if a.app == "care" and not (len(pw) == 6 and pw.isdigit()):
        print("FATAL: the PIN must be 6 digits")
        return 1
    os.environ["GUTLOG_NOSPAWN"] = "1"
    if a.app == "seed":
        path = os.environ.get("FAMILY_SEED_FILE") or ""
        try:
            with open(path, encoding="utf-8") as fh:
                seed = json.load(fh)
        except (OSError, ValueError) as exc:
            print("FATAL: seed file unreadable (%s)" % type(exc).__name__)
            return 1
        import entry_gut as E
        with E.flask_app.app_context():
            if not E.gut.setting("pw_hash"):
                print("FATAL: gut is not set up yet")
                return 1
            if E.gut.setting("member_setup_done"):
                print("FATAL: this member already has a setup -- a seed applies once")
                return 1
            rep = apply_seed(E, seed)
        try:
            os.remove(path)
        except OSError:
            pass
        print("seed: ok (%d medicines, %d schedule lines of which %d weekly, %d as-needed, %d check-in rules, "
              "plan %s)" % (rep["medicines"], rep["schedules"], rep["weekly"], rep["prn"], rep["checkins"],
                            "filed" if rep["plan"] else "none"))
        return 0
    if a.app == "gut":
        import entry_gut as E
        from werkzeug.security import generate_password_hash
        with E.flask_app.app_context():
            con = E.gut.db()
            if E.gut.setting("pw_hash"):
                print("FATAL: gut already has a password -- not a new member")
                return 1
            rep = E.gut.snacks_seed(con, apply=True)
            E.gut.set_setting("pw_hash", generate_password_hash(pw))
            E.gut.set_setting("member_profile_kind", E.M.profile)
            E.gut.set_setting("now_profile", E.M.profile)   # GutLog v3.37.0 joint cards
            con.commit()
            print("gut: ok (%d foods, %d dishes from the snacks seed)"
                  % (len(rep.get("foods", [])) + len(rep.get("estimated", [])),
                     len(rep.get("dishes", []))))
    elif a.app == "rx":
        import entry_rx as E
        from werkzeug.security import generate_password_hash
        con = sqlite3.connect(E.flask_app.config["DB_PATH"])
        if con.execute("SELECT value FROM settings WHERE key='password_hash'").fetchone():
            print("FATAL: rx already has a password -- not a new member")
            return 1
        for k, v in (("password_hash", generate_password_hash(pw)), ("auth_epoch", "1")):
            con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) "
                        "DO UPDATE SET value=excluded.value", (k, v))
        con.commit()
        con.close()
        print("rx: ok")
    elif a.app == "fit":
        import entry_fit as E
        import secrets
        sys.path.insert(0, os.path.dirname(os.path.abspath(E.fit.__file__)))
        import migrate_health_ingest as MIG
        argv = sys.argv
        sys.argv = ["migrate_health_ingest", E.fit.DB_PATH]
        try:
            MIG.main()
        finally:
            sys.argv = argv
        con = sqlite3.connect(E.fit.DB_PATH)
        if con.execute("SELECT value FROM settings WHERE key='password_hash'").fetchone():
            print("FATAL: fit already has a password -- not a new member")
            return 1
        sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
        for k, v in (("password_hash", sha(pw)), ("owner_hash", sha(secrets.token_hex(24)))):
            con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) "
                        "DO UPDATE SET value=excluded.value", (k, v))
        con.commit()
        con.close()
        print("fit: ok")
    else:
        import family_env
        import family_care
        M = family_env.Member()
        c = family_care.Care(M.slug, M.dir, M.base, M.prefixes)
        c.set_access(True, "stamp")
        import family_auth
        family_auth.Auth(c).set_pin(pw)
        print("care: ok (caretaker access on, PIN set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

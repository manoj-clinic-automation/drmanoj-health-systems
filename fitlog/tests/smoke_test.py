#!/usr/bin/env python3
"""FitLog smoke test — engine rule matrix incl. negative controls, plan builder, protocol matcher.
Runs against a throwaway DB. Exit 0 = all pass."""
import os, sys, json, tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp()
os.environ["FITLOG_DB"] = os.path.join(TMP, "test.db")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A

D = date(2026, 8, 5)  # fixed Wednesday, deterministic
T = D.isoformat()
Y = (D - timedelta(days=1)).isoformat()
TM = (D + timedelta(days=1)).isoformat()

passed = failed = 0
def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got}, want {want}")

def reset():
    con = A.sqlite3.connect(os.environ["FITLOG_DB"])
    for tbl in ("exertion_events", "sessions", "checkins", "analgesic_log"):
        con.execute(f"DELETE FROM {tbl}")
    con.commit(); con.close()

def add_event(typ, ds, de=None, intensity=2, mode="", leg=0):
    con = A.sqlite3.connect(os.environ["FITLOG_DB"])
    con.execute("INSERT INTO exertion_events(type,date_start,date_end,intensity,mode,leg_duration_hr,notes) VALUES(?,?,?,?,?,?,?)",
                (typ, ds, de or ds, intensity, mode, leg, "test"))
    con.commit(); con.close()

def add_session(d, minutes, status="done"):
    con = A.sqlite3.connect(os.environ["FITLOG_DB"])
    con.execute("INSERT OR REPLACE INTO sessions(date,verdict,rules_fired,plan,status,minutes_actual) VALUES(?,?,?,?,?,?)",
                (d, "GREEN", "[]", "{}", status, minutes))
    con.commit(); con.close()

def ev(sleep=4, energy=4, pain=0, avail=35):
    with A.app.app_context():
        v, fired, _ = A.evaluate(T, sleep, energy, pain, avail)
        return v, fired

print("== Engine rule matrix ==")
reset()
# F09 negatives (all clear -> GREEN)
check("N1 all-clear", ev()[0], "GREEN")
check("N2 mild pain 3", ev(pain=3)[0], "GREEN")
check("N3 sleep 3 energy 3", ev(sleep=3, energy=3)[0], "GREEN")
check("N4 pain 0 avail 8 (time never changes verdict)", ev(avail=8)[0], "GREEN")

# F01 RECOVERY
check("F01 pain 8", ev(pain=8)[0], "RECOVERY")
check("F01 pain 10", ev(pain=10)[0], "RECOVERY")
reset(); add_event("OT", Y, intensity=3)
check("F01 pain 6 + heavy OT yesterday", ev(pain=6)[0], "RECOVERY")
reset(); add_event("OT", Y, intensity=2)
check("F01-neg pain 6 + intensity-2 yesterday -> not recovery", ev(pain=6)[0] != "RECOVERY", True)

# F02 RED
reset()
check("F02 pain 7", ev(pain=7)[0], "RED")
check("F02 sleep 1", ev(sleep=1)[0], "RED")
check("F02 energy 1", ev(energy=1)[0], "RED")
check("F02-neg sleep 2 -> not red", ev(sleep=2)[0], "YELLOW")

# F03 YELLOW band
check("F03 pain 4", ev(pain=4)[0], "YELLOW")
check("F03 pain 6", ev(pain=6)[0], "YELLOW")
check("F03 energy 2", ev(energy=2)[0], "YELLOW")
check("F03-neg pain 3 -> green", ev(pain=3)[0], "GREEN")

# F04 post-exertion
reset(); add_event("OT", Y, intensity=2)
v, f = ev(); check("F04 OT-2 yesterday -> yellow", v, "YELLOW"); check("F04 fired", "F04" in f, True)
reset(); add_event("SOCIAL", Y, intensity=2)
check("F04 social-2 yesterday", ev()[0], "YELLOW")
reset(); add_event("OT", Y, intensity=1)
check("F04-neg OT-1 yesterday -> green", ev()[0], "GREEN")

# F05 pre-exertion protect
reset(); add_event("OT", TM, intensity=3)
v, f = ev(); check("F05 heavy OT tomorrow -> yellow", v, "YELLOW"); check("F05 fired", "F05" in f, True)
reset(); add_event("SOCIAL", TM, intensity=1)
check("F05-neg light social tomorrow -> green", ev()[0], "GREEN")

# F06 travel
reset(); add_event("TRAVEL", Y, (D + timedelta(days=3)).isoformat(), 2, "car", 4)
v, f = ev(); check("F06 in travel period -> TRAVEL", v, "TRAVEL"); check("F06 fired", "F06" in f, True)

# F10 long flight
reset(); add_event("TRAVEL", T, T, 2, "flight", 5)
v, f = ev(); check("F10 long flight today -> TRAVEL", v, "TRAVEL"); check("F10 fired today", "F10" in f, True)
reset(); add_event("TRAVEL", Y, Y, 2, "flight", 6)
v, f = ev(); check("F10 long flight yesterday -> yellow cap", v, "YELLOW")
reset(); add_event("TRAVEL", T, T, 2, "flight", 2)
v, f = ev(); check("F10-neg short flight today -> TRAVEL without F10", "F10" not in f, True)

# precedence: recovery/red beat travel
reset(); add_event("TRAVEL", T, T, 2, "flight", 5)
check("Precedence pain 8 in travel -> RECOVERY", ev(pain=8)[0], "RECOVERY")
check("Precedence sleep 1 in travel -> RED", ev(sleep=1)[0], "RED")

# F07 deload via rolling minutes
reset()
for i in range(1, 7):
    add_session((D - timedelta(days=i)).isoformat(), 40)
v, f = ev(); check("F07 rolling 240min -> DELOAD", v, "DELOAD"); check("F07 fired", "F07" in f, True)
reset(); add_session(Y, 40)
check("F07-neg 40min week -> green", ev()[0], "GREEN")

print("== Plan builder ==")
with A.app.app_context():
    reset()
    for verdict in ("GREEN", "YELLOW", "RED", "TRAVEL", "DELOAD"):
        p = A.build_plan(verdict, T, 35)
        check(f"plan {verdict} non-empty", len(p["items"]) > 0, True)
    p = A.build_plan("RECOVERY", T, 35)
    check("plan RECOVERY checklist", p["type"], "checklist")
    g1 = A.build_plan("GREEN", T, 35)["items"]
    g2 = A.build_plan("GREEN", T, 35)["items"]
    check("plan deterministic (same day = same plan)", [i["id"] for i in g1], [i["id"] for i in g2])
    g3 = A.build_plan("GREEN", (D + timedelta(days=1)).isoformat(), 35)["items"]
    check("plan rotates across days", [i["id"] for i in g1] != [i["id"] for i in g3], True)
    trimmed = A.build_plan("GREEN", T, 8)
    check("F08 time-trim reduces to 3", len(trimmed["items"]), 3)
    check("F08 flag set", trimmed["trimmed"], True)

print("== Protocol matcher ==")
with A.app.app_context():
    def fake(typ, intensity=2, mode="", leg=0):
        return {"type": typ, "intensity": intensity, "mode": mode, "leg_duration_hr": leg}
    pr = A.protocols_for(fake("OT", 2))
    check("OT-2 pre exists", "pre" in pr, True)
    check("OT-2 post = standard", pr["post"]["id"], "P_OT_POST")
    pr = A.protocols_for(fake("OT", 3))
    check("OT-3 post = heavy", pr["post"]["id"], "P_OT_POST_HEAVY")
    pr = A.protocols_for(fake("TRAVEL", 2, "flight", 8))
    check("flight 8h transit = long-haul", pr["transit"]["id"], "P_TRV_FLT_LONG_TRANSIT")
    pr = A.protocols_for(fake("TRAVEL", 2, "flight", 1.5))
    check("flight 1.5h transit = short", pr["transit"]["id"], "P_TRV_FLT_SHORT_TRANSIT")
    pr = A.protocols_for(fake("TRAVEL", 2, "car", 5))
    check("car 5h transit = drive breaks", pr["transit"]["id"], "P_TRV_CAR_TRANSIT")
    pr = A.protocols_for(fake("TRAVEL", 2, "car", 1))
    check("car 1h -> no transit card", "transit" not in pr, True)

print("== W03 flag ==")
with A.app.app_context():
    reset()
    con = A.db()
    med = con.execute("SELECT id FROM med_stack WHERE category='analgesic' LIMIT 1").fetchone()["id"]
    for i in range(3):
        d = (date.today() - timedelta(days=i)).isoformat()
        con.execute("INSERT INTO analgesic_log(dt,med_id,dose_label) VALUES(?,?,?)", (d + "T10:00", med, "1 tab"))
    con.commit()
    flags = [f[0] for f in A.compute_flags()]
    check("W03 fires at 3 days/14", "W03" in flags, True)
    con.execute("DELETE FROM analgesic_log"); con.commit()
    smed = con.execute("SELECT id FROM med_stack WHERE category='sleep' LIMIT 1").fetchone()["id"]
    for i in range(5):
        d = (date.today() - timedelta(days=i)).isoformat()
        con.execute("INSERT INTO analgesic_log(dt,med_id,dose_label) VALUES(?,?,?)", (d + "T22:00", smed, "0.25 mg"))
    con.commit()
    flags = [f[0] for f in A.compute_flags()]
    check("W03-neg sleep meds do not fire analgesic flag", "W03" not in flags, True)

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

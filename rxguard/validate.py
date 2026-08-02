#!/usr/bin/env python3
"""
RxGuard validation harness.

Runs the declared case set against the engine and measures the two numbers that
matter: missed REDs (recall failures) and false REDs (alert-fatigue burden).
Thresholds are declared in validation_cases.json BEFORE running.
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as m

def run():
    doc = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "validation_cases.json"), encoding="utf-8"))
    cases, th = doc["cases"], doc["_meta"]["acceptance_thresholds"]
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    application = m.create_app(db_path=path, secret="v")
    rows, missed_red, false_red, missing_text = [], [], [], []

    with application.app_context():
        m.g.db_path = path
        db = m.get_db()
        for c in cases:
            db.execute("DELETE FROM medications"); db.execute("UPDATE conditions SET active=0")
            for d in c["list"]:
                db.execute("INSERT INTO medications (drug_key, status, kind) "
                           "VALUES (?, 'active', 'chronic')", (m.norm_key(d),))
            for cond in c["conditions"]:
                db.execute("UPDATE conditions SET active=1 WHERE code=?", (cond,))
            db.commit()
            r = m.analyse(m.norm_key(c["proposed"]), action=c["action"])
            got, exp = r["flag"], c["expect"]
            blob = json.dumps(r).lower()
            if exp == "RED":
                ok = got == "RED"
                if not ok: missed_red.append(c["id"])
            elif exp == "AMBER":
                ok = got in ("AMBER", "RED")
                if not ok: missed_red.append(c["id"])
            elif exp == "NONE":
                ok = got != "RED"
                if not ok: false_red.append(c["id"])
            else:
                ok = got == exp
            missing = [t for t in c.get("must_mention", []) if t.lower() not in blob]
            if missing: missing_text.append((c["id"], missing))
            rows.append((c["id"], exp, got, "ok" if ok and not missing else "FAIL",
                         len(r["findings"]), c["note"]))

    os.unlink(path)
    print("=" * 78)
    print("RxGuard validation — %d cases" % len(cases))
    print("Reference standard: %s" % doc["_meta"]["reference_standard"][:60] + "...")
    print("=" * 78)
    print("%-5s %-7s %-7s %-6s %-4s %s" % ("ID", "EXPECT", "GOT", "RESULT", "N", "NOTE"))
    for r in rows:
        print("%-5s %-7s %-7s %-6s %-4d %s" % (r[0], r[1], r[2], r[3], r[4], r[5][:38]))
    print("=" * 78)
    fails = [r for r in rows if r[3] == "FAIL"]
    print("Missed RED/AMBER (recall failures): %d %s  [threshold %d]"
          % (len(missed_red), missed_red or "", th["missed_red"]))
    print("False RED on negative controls:     %d %s  [threshold %d]"
          % (len(false_red), false_red or "", th["false_red"]))
    if missing_text:
        print("Expected text not surfaced:")
        for cid, miss in missing_text: print("   %s  missing %s" % (cid, miss))
    ok = (len(missed_red) <= th["missed_red"] and len(false_red) <= th["false_red"]
          and not missing_text)
    print("=" * 78)
    print("RESULT: %s (%d/%d cases clean)" % ("PASS" if ok else "FAIL",
                                              len(rows) - len(fails), len(rows)))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(run())

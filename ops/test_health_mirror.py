#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Health Mirror -- the snapshot is generated from a scratch database and every
section is read back, because the whole point of the mirror is that a reader
believes what it says.

NEUTRAL NAMES ONLY. "Medicine A", "molecule-a". This file is tracked and the
repository is public (CLAUDE.md 5d); the real names live in the databases and
on Drive, nowhere else.

  python3 test_health_mirror.py [path/to/gutlog/app.py]
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

RESULTS = []

# What the server calls a stored document: a hash, not a name anyone reads.
STORED_DOC = "0487aa13be0a4c2eb65269040b20f208.pdf"


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    app_path = (os.path.abspath(sys.argv[1]) if len(sys.argv) > 1
                else os.path.join(repo, "gutlog", "app.py"))
    w = tempfile.mkdtemp(prefix="mirror_")
    gdb = os.path.join(w, "g.db")
    fdb = os.path.join(w, "f.db")
    out = os.path.join(w, "mirror")
    plans_dir = os.path.join(w, "plans_files")
    os.makedirs(plans_dir)
    plan_pdf = os.path.join(plans_dir, "a" * 64 + ".pdf")
    open(plan_pdf, "wb").write(b"%PDF-1.4\ntrailer<<>>\n%%EOF\n")

    # the uploads vault: a report stored under a hashed name, as on the server
    uploads = os.path.join(w, "uploads")
    os.makedirs(uploads)
    open(os.path.join(uploads, STORED_DOC), "wb").write(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")

    plan_cfg = os.path.join(w, "plan.json")
    json.dump({"targets": {"protein": 100, "fibre": 30},
               "main_meals": ["Breakfast", "Lunch", "Dinner"]}, open(plan_cfg, "w"))

    T = date.today()
    D = lambda n: (T - timedelta(days=n)).isoformat()

    # --- GutLog: let the app build its own schema, then seed ---------------
    os.environ.update(GUTLOG_DB=gdb, GUTLOG_UPLOADS=os.path.join(w, "up"),
                      GUTLOG_PLANS=plans_dir, GUTLOG_INSECURE="1",
                      GUTLOG_SECRET="test-secret-not-real", GUTLOG_NOSPAWN="1",
                      GUTLOG_ICONS=os.path.dirname(app_path), GUTLOG_LINKS="0",
                      GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
                      GUTLOG_MEALS_FILE=os.path.join(w, "none.json"),
                      GUTLOG_PLAN_FILE=plan_cfg)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("gutlog_for_mirror", app_path)
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    c = gm.app.test_client()
    c.post("/setup", data={"pw": "testpassword1", "pw2": "testpassword1"},
           follow_redirects=True)

    # meals through the app, so the mirror can be held to the app's own sums
    def meal(day, mtime, slot, n, p, k, f):
        c.post("/api/meals", json={"day": day, "mtime": mtime, "slot": slot,
                                   "notes": "",
                                   "items": [{"n": n, "q": 1, "p": p, "k": k,
                                              "f": f, "fm": "L"}]})
    meal(D(0), "08:00", "Breakfast", "Test grain", 12, 300, 6)
    meal(D(0), "13:00", "Lunch", "Test pulse", 20, 450, 9)
    meal(D(0), "20:00", "Dinner", "Test stew", 18, 400, 7)
    meal(D(2), "13:00", "Lunch", "Test soup", 9, 210, 4)     # partial
    # D(1) deliberately has nothing -- the "not logged" case

    g = sqlite3.connect(gdb)

    # app.py SEEDS 15 prnmeds rows of its own, so ids 1..15 are already taken.
    # The first version of this suite assumed med_id 1, 2, 3 and silently
    # attached its schedules to three seeded medicines instead -- the sections
    # came out empty and the suite said so. Take the id the insert actually
    # produced, never the one it "should" be.
    def add_med(name, sort, molecule, active):
        g.execute("INSERT INTO prnmeds(name,sort,molecule,form,active,scheduled) "
                  "VALUES(?,?,?,'tablet',?,1)", (name, sort, molecule, active))
        return g.execute("SELECT last_insert_rowid()").fetchone()[0]

    a_id = add_med("Medicine A", 901, "molecule-a", 1)
    b_id = add_med("Medicine B", 902, "molecule-b", 0)
    c_id = add_med("Medicine C", 903, "molecule-c", 0)
    g.execute("INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,valid_to) "
              "VALUES(?,'Night','1 tablet',0,?,NULL)", (a_id, D(20)))
    # B is no longer active and carries NO stop date -- must be SAID, not guessed
    g.execute("INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,valid_to) "
              "VALUES(?,'Morning','1 tablet',1,?,NULL)", (b_id, D(120),))
    g.execute("INSERT INTO med_schedule(med_id,slot,dose_text,with_food,valid_from,valid_to) "
              "VALUES(?,'Night','2 tablets',0,?,?)", (c_id, D(80), D(30)))
    g.execute("INSERT INTO doses(day,dtime,medicine,dose_text,status,created) "
              "VALUES(?,'22:30','Medicine A','1 tablet','TAKEN','x')", (D(1),))
    g.execute("INSERT INTO doses(day,dtime,medicine,dose_text,status,created) "
              "VALUES(?,'09:00','Medicine A','1 tablet','TAKEN','x')", (D(1),))
    g.execute("INSERT INTO vitals(day,vtime,sys,dia,pulse,created) "
              "VALUES(?,'07:30',128,82,74,'x')", (D(1),))
    g.execute("INSERT INTO episodes(day,etime,category,etype,severity,duration,created) "
              "VALUES(?,'15:00','Gut','Cramp',4,'30 min','x')", (D(2),))
    g.execute("INSERT INTO days(day,pain,pain_site,bristol,stools,syms,updated) "
              "VALUES(?,3,'Left',4,2,'Test symptom','x')", (D(2),))
    g.execute("INSERT INTO rec_labs(day,test,section,value,num,unit,ref,lab,created) "
              "VALUES('2026-09-07','Test analyte','Biochemistry','5.4',5.4,'mmol/L','3.5-6.0','Test lab','x')")
    # a report already on the server: stored under a hash, readable name in `orig`
    g.execute("INSERT INTO rec_docs(day,kind,title,source,finding,stored,orig,sha,"
              "status,created) VALUES('2026-09-07','Blood','Test report','Test lab',"
              "'nothing to note',?,'scan001.pdf','deadbeef','filed','x')", (STORED_DOC,))
    g.execute("INSERT INTO plans(title,first_considered,status,archived,created_at) "
              "VALUES('Plan A',?,'Active',0,'x')", (D(0),))
    g.execute("INSERT INTO plan_files(plan_id,stored_name,original_name,bytes,sha256,uploaded_at) "
              "VALUES((SELECT MAX(id) FROM plans),?,?,10,?,'x')",
              (os.path.basename(plan_pdf), "plan-a.pdf", "a" * 64))
    g.commit()
    g.close()

    # --- FitLog: a small real one -----------------------------------------
    f = sqlite3.connect(fdb)
    f.executescript("""
      CREATE TABLE health_metrics (id INTEGER PRIMARY KEY, date TEXT, metric TEXT,
        value REAL, unit TEXT, source TEXT, ingested_at TEXT);
      CREATE TABLE health_sleep_blocks (block_key TEXT PRIMARY KEY, date TEXT, source TEXT,
        feed TEXT, start_ts TEXT, end_ts TEXT, in_bed_start TEXT, in_bed_end TEXT,
        asleep_h REAL, rem_h REAL, core_h REAL, deep_h REAL, awake_h REAL,
        device TEXT, ingested_at TEXT);
      CREATE TABLE checkins (id INTEGER PRIMARY KEY, date TEXT, sleep INTEGER,
        energy INTEGER, pain_max INTEGER, pain_sites TEXT, avail_min INTEGER,
        created_at TEXT);
    """)
    f.execute("INSERT INTO health_metrics(date,metric,value,unit,source) "
              "VALUES(?,'sleep_hours',6.4,'h','applewatch')", (D(1),))
    f.execute("INSERT INTO health_metrics(date,metric,value,unit,source) "
              "VALUES(?,'resting_hr',61,'bpm','applewatch')", (D(1),))
    f.execute("INSERT INTO health_sleep_blocks(block_key,date,in_bed_start,awake_h) "
              "VALUES('k1',?,'23:10',0.6)", (D(1),))
    f.execute("INSERT INTO checkins(date,sleep,energy) VALUES(?,3,4)", (D(1),))
    f.commit()
    f.close()

    # --- run the mirror ----------------------------------------------------
    os.environ.update(MIRROR_GUTLOG_DB=gdb, MIRROR_FITLOG_DB=fdb,
                      MIRROR_PLANS_DIR=plans_dir, MIRROR_PLAN_FILE=plan_cfg,
                      MIRROR_UPLOAD_DIR=uploads)
    # MIRROR_TOOL lets NC_MIRROR.py point this suite at a deliberately broken
    # copy. Without it the suite would always test the real file and a
    # mutation control could prove nothing.
    tool = os.environ.get("MIRROR_TOOL") or os.path.join(here, "health_mirror.py")
    spec2 = importlib.util.spec_from_file_location("health_mirror_under_test", tool)
    hm = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(hm)
    counts, MD = hm.generate(out, days=30)
    snap = os.path.join(out, "snapshot")

    def csv_text(n):
        return open(os.path.join(snap, "csv", n), encoding="utf-8").read()

    # ---------------------------------------------------------------- 01
    def t01():
        want = ("# Health record snapshot", "## Medicines now",
                "## Sleep, last 30 nights", "## Vitals, last 30 days",
                "## Gut, last 30 days", "## Meals, last 30 days",
                "## Labs, every result on record", "## Plans", "## Documents")
        for h in want:
            assert h in MD, "the snapshot has no %r section" % h
        assert "IST" in MD.split("\n")[2], "no generation time at the top"
        return "all %d sections present, generated time at the top" % len(want)
    check("01 every section the brief asks for is in the snapshot", t01)

    # ---------------------------------------------------------------- 02
    def t02():
        block = MD.split("## Medicines now")[1].split("## Sleep")[0]
        assert "Medicine A" in block, "the open regimen is missing its medicine"
        assert "molecule-a" in block, "the molecule is not carried"
        assert "Medicine B" not in block.split("### Changes")[0], \
            "a stopped medicine is listed as current"
        return "the open regimen carries the live medicine and its molecule"
    check("02 medicines now lists the open regimen only", t02)

    # ---------------------------------------------------------------- 03
    def t03():
        block = MD.split("### Changes in the last 90 days")[1].split("## Sleep")[0]
        assert "no stop date recorded" in block, \
            "a medicine with no stop date does not say so"
        assert "Medicine B" in block, "the medicine with no stop date is not listed"
        stopped = [l for l in block.split("\n") if "Medicine C" in l and "stopped" in l]
        assert stopped, "the medicine with a real stop date is not shown as stopped"
        assert hm.dmy(D(30)) in stopped[0], "the stop date is not the recorded one"
        csvt = csv_text("medicine_events.csv")
        assert "no stop date recorded" in csvt, "the CSV does not carry the same"
        return "a missing stop date is stated, a real one is dated %s" % hm.dmy(D(30))
    check("03 a missing stop date is said, never invented", t03)

    # ---------------------------------------------------------------- 04
    def t04():
        block = MD.split("## Sleep, last 30 nights")[1].split("## Vitals")[0]
        assert hm.dmy(D(1)) in block, "the night with data is missing"
        assert "6.4" in block, "the watch hours are not carried"
        assert "22:30 Medicine A" in block, \
            "the evening dose is not beside the night it belongs to"
        assert "09:00" not in block, "a morning dose was filed against the night"
        return "watch hours and that evening's dose sit on the same row"
    check("04 each night carries its watch hours and what was taken", t04)

    # ---------------------------------------------------------------- 05
    def t05():
        block = MD.split("## Sleep, last 30 nights")[1].split("## Vitals")[0].lower()
        banned = ["good night", "poor night", "bad night", "sleep score",
                  "sleep quality", "graded", "excellent", "efficiency", "rating"]
        hit = [b for b in banned if b in block]
        assert not hit, "the sleep section scores the night: %s" % ", ".join(hit)
        assert "no score" in block, "the section does not state that it never scores"
        return "no score, grade or good/poor wording anywhere in the sleep section"
    check("05 the sleep section never scores a night", t05)

    # ---------------------------------------------------------------- 06
    def t06():
        block = MD.split("## Meals, last 30 days")[1].split("## Labs")[0]
        assert "not logged" in block, "a day with no meals is not labelled"
        line = [l for l in block.split("\n") if hm.dmy(D(1)) in l]
        assert line, "the empty meal day is missing"
        assert "not logged" in line[0], "the empty day does not say so: %s" % line[0]
        assert "| 0 |" not in line[0], "the empty day is printed as zero"
        part = [l for l in block.split("\n") if hm.dmy(D(2)) in l]
        assert part and "partial" in part[0], "the 1-meal day is not marked partial"
        return "an unlogged day says so and shows no zero; a thin day is partial"
    check("06 meal days are 'not logged' or 'partial', never a false zero", t06)

    # ---------------------------------------------------------------- 07
    def t07():
        with gm.app.app_context():
            for d in (D(0), D(1), D(2)):
                app_day = gm.nut_day(d)
                mine = [m for m in hm.meals_recent(hm.ro(gdb), hm.days_ago(30))
                        if m["day"] == d][0]
                assert bool(app_day["logged"]) == bool(mine["logged"]), \
                    "%s: logged disagrees" % d
                if app_day["logged"]:
                    assert app_day["kcal"] == mine["kcal"], \
                        "%s: kcal %r vs %r" % (d, app_day["kcal"], mine["kcal"])
                    assert abs(app_day["protein"] - mine["protein"]) < 0.05, \
                        "%s: protein %r vs %r" % (d, app_day["protein"], mine["protein"])
                    assert app_day["partial"] == mine["partial"], "%s: partial disagrees" % d
        return "the mirror and GutLog's own nut_day() agree on every seeded day"
    check("07 the mirror's meal totals equal the app's own", t07)

    # ---------------------------------------------------------------- 08
    def t08():
        block = MD.split("## Labs, every result on record")[1].split("## Plans")[0]
        assert "Test analyte" in block, "the lab result is missing"
        assert "07-Sep-2026" in block, "the report date is not shown DD-Mon-YYYY"
        vit = MD.split("## Vitals, last 30 days")[1].split("## Gut")[0]
        assert "128/82" in vit, "blood pressure is not carried"
        assert "61" in vit, "the watch resting heart rate is not carried"
        gut = MD.split("## Gut, last 30 days")[1].split("## Meals")[0]
        assert "Cramp" in gut, "the gut episode is missing"
        assert "Test symptom" in gut, "the day log is missing"
        return "labs dated DD-Mon-YYYY, BP, watch HR, episode and day log all present"
    check("08 labs, vitals and gut carry their values and dates", t08)

    # ---------------------------------------------------------------- 09
    def t09():
        for n in ("medicines_now.csv", "medicine_events.csv", "sleep.csv",
                  "vitals.csv", "labs.csv", "gut_episodes.csv", "gut_daylog.csv",
                  "meals.csv", "plans.csv"):
            p = os.path.join(snap, "csv", n)
            assert os.path.exists(p), "missing CSV: %s" % n
            assert len(open(p, encoding="utf-8").read().strip().split("\n")) >= 1, \
                "%s is empty" % n
        pdfs = os.listdir(os.path.join(snap, "plans"))
        assert pdfs, "no plan document was copied"
        assert pdfs[0].lower().endswith(".pdf"), pdfs
        assert "Plan A" in pdfs[0], "the copy is not under a readable name: %r" % pdfs[0]
        assert open(os.path.join(snap, "plans", pdfs[0]), "rb").read(5) == b"%PDF-", \
            "the copied plan is not the PDF"
        return "9 CSVs and the plan document, copied under a readable name"
    check("09 the CSVs and the plan documents are written beside it", t09)

    # ---------------------------------------------------------------- 10
    def t10():
        assert os.path.exists(os.path.join(snap, "health_snapshot_latest.md"))
        dated = [n for n in os.listdir(snap) if n.startswith("health_snapshot_")
                 and n != "health_snapshot_latest.md"]
        assert dated, "no dated copy was kept"
        hm.generate(out, days=30)
        again = [n for n in os.listdir(snap) if n.startswith("health_snapshot_")
                 and n != "health_snapshot_latest.md"]
        assert len(again) <= hm.KEEP_DATED, \
            "retention kept %d dated copies, cap is %d" % (len(again), hm.KEEP_DATED)
        return "latest plus a dated copy, capped at %d" % hm.KEEP_DATED
    check("10 latest is overwritten and dated copies are capped", t10)

    # ---------------------------------------------------------------- 11
    def t11():
        # Both refusal paths on purpose, not whichever one this machine
        # happens to fall into. A mutation proved the second was unreachable
        # here: with no rclone installed the config check never ran.
        fake_exe = os.path.join(w, "rclone-stub")
        open(fake_exe, "w").write("stub")
        os.environ["MIRROR_RCLONE"] = os.path.join(w, "no-such-rclone")
        os.environ.pop("MIRROR_RCLONE_CONF", None)
        ok, msg = hm.upload(out)
        assert ok is False and "not installed" in msg, \
            "rclone absent should refuse: %r" % msg

        os.environ["MIRROR_RCLONE"] = fake_exe
        os.environ["MIRROR_RCLONE_CONF"] = os.path.join(w, "no-such.conf")
        ok, msg = hm.upload(out)
        assert ok is False, "upload claimed success with no rclone configuration"
        assert "sign-in" in msg, "the message does not name the missing step: %r" % msg
        assert not os.path.exists(os.path.join(out, "last_success.json")), \
            "a failed upload still marked the mirror fresh"

        path = hm.stamp_success(out)
        j = json.load(open(path, encoding="utf-8"))
        assert "epoch" in j and j["epoch"] > 0, j
        for k in ("MIRROR_RCLONE", "MIRROR_RCLONE_CONF"):
            os.environ.pop(k, None)
        return "both refusals reached; neither marks the mirror fresh"
    check("11 a failed upload never marks the mirror fresh", t11)

    # ---------------------------------------------------------------- 12
    def t12():
        # Checked against tools/clinical_terms.local.txt, the same list
        # NO_SECRETS uses -- NOT against a hardcoded list of molecules. The
        # first version of this assertion spelled seven of them out, in the
        # file whose whole job is to prove the tool spells none. NO_SECRETS
        # check C refused the commit and was right to.
        #
        # NOT APPLICABLE is not the same as DID NOT RUN, and the difference
        # matters here. This assertion guards a property of the REPOSITORY --
        # that a tracked file names no medicine. On the server there is no
        # repository and nothing is tracked, so there is nothing to guard and
        # saying so is honest. But if a checkout IS here and the list is
        # missing, the check could not run, and a check that did not run is
        # not a check that passed. The server suite was red on this until the
        # two cases were told apart.
        # "A repository is here" means the GATE is here -- .git, or the very
        # script this assertion backs up. Not merely a directory called
        # tools/: the server has one of those for other reasons, which made
        # the first attempt at this claim a checkout that was not there and
        # left the server suite red.
        terms_path = os.path.join(repo, "tools", "clinical_terms.local.txt")
        in_repo = (os.path.isdir(os.path.join(repo, ".git"))
                   or os.path.exists(os.path.join(repo, "tools", "NO_SECRETS.py")))
        if not in_repo:
            return ("NOT APPLICABLE: no repository here, so nothing is tracked "
                    "and there is no repository property to guard")
        assert os.path.exists(terms_path), (
            "a checkout is here but the clinical terms list is not, so this "
            "check DID NOT RUN. A check that did not run is not a check that "
            "passed.")
        terms = [t.strip().lower() for t in open(terms_path, encoding="utf-8")
                 if t.strip() and not t.strip().startswith("#")]
        assert len(terms) > 20, "the terms list looks empty: %d entries" % len(terms)
        low = open(tool, encoding="utf-8").read().lower()
        hits = sorted(set(t for t in terms if len(t) > 3 and t in low))
        assert not hits, ("the tracked tool names %d clinical term(s); "
                          "they are not printed here for the same reason"
                          % len(hits))
        assert "NIGHT_FROM" in open(tool, encoding="utf-8").read(), \
            "the night window is not a clock rule"
        return ("checked against %d terms; the tool names none, and the night "
                "window is a clock rule" % len(terms))
    check("12 no molecule name lives in the tracked tool", t12)

    # ---------------------------------------------------------------- 13
    def t13():
        block = MD.split("## Documents")[1]
        assert "Test report" in block, "the document is not listed"
        assert "07-Sep-2026" in block, "the document's date is not DD-Mon-YYYY"
        copied = os.listdir(os.path.join(snap, "documents"))
        assert len(copied) == 1, "expected 1 document, got %r" % copied
        name = copied[0]
        assert name.startswith("2026-09-07"), \
            "the copy is not named by its date: %r" % name
        assert "Test report" in name, "the copy is not named by its title: %r" % name
        assert STORED_DOC not in name, \
            "the copy kept the hashed storage name: %r" % name
        assert open(os.path.join(snap, "documents", name), "rb").read(5) == b"%PDF-", \
            "the copied document is not the PDF"
        idx = csv_text("documents.csv")
        assert "Test report" in idx and name in idx, "the index does not name the copy"
        return "copied as %r and indexed" % name
    check("13 the server's own reports are copied under readable names", t13)

    # ---------------------------------------------------------------- 14
    def t14():
        before = os.path.getmtime(os.path.join(
            snap, "documents", os.listdir(os.path.join(snap, "documents"))[0]))
        counts2, _ = hm.generate(out, days=30)
        after_name = os.listdir(os.path.join(snap, "documents"))[0]
        after = os.path.getmtime(os.path.join(snap, "documents", after_name))
        assert counts2["documents_unchanged"] >= 1, \
            "an unchanged document was copied again: %r" % counts2
        assert counts2["documents_copied"] == 0, counts2
        assert before == after, "the file was rewritten although nothing changed"
        return "unchanged documents are left alone on a second run"
    check("14 an unchanged report is not recopied every night", t14)

    print("")
    for ok, name, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))
    bad = [r for r in RESULTS if not r[0]]
    print("-" * 72)
    print("%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
    print("RESULT: " + ("FAILURES" if bad else "ALL PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

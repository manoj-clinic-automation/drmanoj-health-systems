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

Python 3.9.
"""
import argparse
import hashlib
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, choices=("gut", "rx", "fit", "care"))
    a = ap.parse_args()
    pw = sys.stdin.readline().rstrip("\n") if a.app != "care" else ""
    if a.app != "care" and len(pw) < 8:
        print("FATAL: password missing or too short")
        return 1
    os.environ["GUTLOG_NOSPAWN"] = "1"
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
        print("care: ok (caretaker access on)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_stamp_real.py -- stamp through the REAL per-user path.

    python3 -B /root/family/test_family_stamp_real.py /root/gutlog/app.py   (root, on the server)

Why this exists: on 2026-09-25 the first real stamp failed with "can't open
/root/family/init_member.py: Permission denied". Every other suite stamps with
--no-system, where the setup runs as root and could read anything -- so none of
them could see that a member's own user cannot. This one does not take that
shortcut: it creates real Linux users, runs the setup as them with `runuser`,
and uses the deployed tool with NO code override, exactly as the owner does.
Only the data lives under a scratch root, and no service or proxy is touched
(--no-services). Every user it makes (fam_m990, fam_m991) is removed at the end.

Off the server (not root, not Linux) it runs nothing and says so -- it never
reports a pass it did not measure. Python 3.9.
"""
import json
import os
try:
    import pwd
except ImportError:          # not a Unix machine: main() says NOT RUN
    pwd = None
import shutil
import stat
import subprocess
import sys
import tempfile

FAM = os.path.abspath(os.environ.get("FAMILY_CODE") or os.path.dirname(os.path.abspath(__file__)))
STAMP = os.path.join(FAM, "stamp_member.py")
TREE = "/opt/family/code/current"
RES = []


def check(name, ok, detail=""):
    RES.append(bool(ok))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else "  -- " + str(detail)[:400]), flush=True)


def user_exists(u):
    try:
        pwd.getpwnam(u)
        return True
    except KeyError:
        return False


def stamp(root, slug, *extra):
    cmd = ["/usr/bin/python3", "-B", STAMP, "--root", root, "--no-services", "--slug", slug,
           "--name", "Member R", "--profile", "gut", "--port-base", "18000"] + list(extra)
    env = dict((k, v) for k, v in os.environ.items()
               if not k.startswith(("GUTLOG_", "RXGUARD_", "FITLOG_", "FAMILY_", "HEALTH_SSO_")))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def main():
    if pwd is None or os.geteuid() != 0 or not os.path.isdir(TREE):
        print("NOT RUN: this suite needs root on the server, with %s in place." % TREE)
        print("RESULT: NOT RUN")
        return 2
    root = tempfile.mkdtemp(prefix="famreal_", dir="/tmp")
    os.chmod(root, 0o755)
    users = ("fam_m990", "fam_m991")
    for u in users:
        if user_exists(u):
            subprocess.run(["userdel", u])
    try:
        run(root)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("R00 the suite ran to the end", False, repr(exc))
    finally:
        for u in users:
            if user_exists(u):
                subprocess.run(["userdel", u])
        shutil.rmtree(root, ignore_errors=True)
    bad = RES.count(False)
    print("\n%d checks, %d failed" % (len(RES), bad))
    print("RESULT: " + ("ALL PASS" if not bad else "FAIL"))
    return 0 if not bad else 1


def run(root):
    pwf = os.path.join(root, "first-login.txt")
    srv = os.path.join(root, "srv", "family")

    # ---------------------------------------------------------------- R01
    rc, out = stamp(root, "m990", "--password-file", pwf)
    mdir = os.path.join(srv, "m990")
    ok = rc == 0 and user_exists("fam_m990") and os.path.isdir(mdir)
    uid = pwd.getpwnam("fam_m990").pw_uid if user_exists("fam_m990") else -1
    dbs = [os.path.join(mdir, p) for p in ("gutlog/health.db", "rxguard/rxguard.db", "fitlog/fitlog.db", "care.db")]
    owned = all(os.path.exists(p) and os.stat(p).st_uid == uid for p in dbs)
    check("R01 a real stamp runs the setup as the member's own user, from the family tree",
          ok and owned and stat.S_IMODE(os.stat(mdir).st_mode) == 0o700,
          "rc %s owned %s\n%s" % (rc, owned, out[-700:]))
    pw_line = open(pwf).read() if os.path.exists(pwf) else ""
    pw = pw_line.split("\t")[-1].strip() if pw_line else ""
    check("R01 the first-login password goes to a root-only file and is never printed",
          pw and len(pw) >= 8 and pw not in out and stat.S_IMODE(os.stat(pwf).st_mode) == 0o600
          and os.stat(pwf).st_uid == 0, "mode %s" % (oct(os.stat(pwf).st_mode) if os.path.exists(pwf) else "-"))
    reg = json.load(open(os.path.join(root, "root", "family", "members.local.json")))
    check("R01 the member is in the registry", any(m["slug"] == "m990" for m in reg["members"]), reg)
    r = subprocess.run(["runuser", "-u", "fam_m990", "--", "/bin/sh", "-c",
                        "cat /root/gutlog/health3.db >/dev/null 2>&1 && echo READ || echo REFUSED"],
                       stdout=subprocess.PIPE)
    check("R01 the member's user cannot read the owner's database", r.stdout.decode().strip() == "REFUSED",
          r.stdout.decode())

    # ---------------------------------------------------------------- R02
    rc, out = stamp(root, "m991", "--code", "/root")
    check("R02 a code tree the member cannot read is refused before anything is made",
          rc == 2 and "REFUSED" in out and not user_exists("fam_m991")
          and not os.path.exists(os.path.join(srv, "m991"))
          and not os.path.exists(os.path.join(root, "etc", "family", "m991.env")), "rc %s\n%s" % (rc, out[-400:]))

    # ---------------------------------------------------------------- R03
    broken = os.path.join(root, "broken_tree")
    shutil.copytree(TREE, broken, symlinks=False)
    ip = os.path.join(broken, "family", "init_member.py")
    t = open(ip).read().replace('    if a.app == "gut":\n', '    if a.app == "rx":\n        return 1\n'
                                                                  '    if a.app == "gut":\n', 1)
    open(ip, "w").write(t)
    subprocess.run(["chmod", "-R", "a+rX", broken])
    rc, out = stamp(root, "m991", "--code", broken)
    left = [p for p in (os.path.join(srv, "m991"), os.path.join(root, "etc", "family", "m991.env"),
                        os.path.join(root, "root", "family", "care", "m991.key"))
            if os.path.exists(p)]
    reg = json.load(open(os.path.join(root, "root", "family", "members.local.json")))
    check("R03 a setup that fails part-way leaves nothing behind -- folder, secrets, env file, user",
          rc == 1 and not left and not user_exists("fam_m991")
          and not any(m["slug"] == "m991" for m in reg["members"]), "rc %s left %s\n%s" % (rc, left, out[-400:]))


if __name__ == "__main__":
    sys.exit(main())

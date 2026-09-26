#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_lock.py -- the server-wide build lock (family/build_lock.sh).

    python3 -B family/test_family_lock.py [gutlog/app.py -- ignored]

Needs bash (the server; Git Bash on the PC). A scratch lock folder, a short
wait and a short stale limit through the BUILD_LOCK_* variables:
  1  a free lock is taken, the owner file names the repo and the brief, and
     the trap releases it on exit -- also when the script fails;
  2  a HELD lock makes a second caller WAIT (not fail) and take it when the
     first releases it;
  3  a stale lock (older than the limit, owner reports finished) is taken
     over with a log line; a stale lock whose owner did NOT finish is not.
Python 3.9.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402

LOCK_SH = os.path.join(famtest.fam_src(), "build_lock.sh")


def bash():
    for c in ("bash", "/bin/bash", "/usr/bin/bash", r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe"):
        p = shutil.which(c) if not os.path.isabs(c) else (c if os.path.exists(c) else None)
        if p:
            return p
    return None


def run(sh, script, env, timeout=60):
    e = dict(os.environ, **env)
    p = subprocess.Popen([sh, "-c", script], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out = b"TIMEOUT"
    return p.returncode, out.decode("utf-8", "replace")


def posix(p):
    return p.replace("\\", "/")


def main():
    sh = bash()
    if not sh:
        check("L00 bash is available to run build_lock.sh", False, "no bash on this machine -- run on the server")
        return famtest.finish()
    work = tempfile.mkdtemp(prefix="famlock_")
    lock = posix(os.path.join(work, "build.lock"))
    env = {"BUILD_LOCK_DIR": lock, "BUILD_LOCK_WAIT_S": "1", "BUILD_LOCK_STALE_S": "3", "BUILD_LOCK_REPO": "repo-under-test"}
    src = ". '%s'; " % posix(LOCK_SH)
    # ---------------------------------------------------------------- L01
    rc, out = run(sh, src + "take_build_lock 'brief one'; test -d \"$BUILD_LOCK_DIR\" && cat \"$BUILD_LOCK_DIR/owner\"; "
                      "echo held-during-run", env)
    check("L01 a free lock is taken, the owner file names the repo and the brief, and it is released on exit",
          rc == 0 and "build lock taken" in out and "repo-under-test" in out and "brief one" in out
          and "held-during-run" in out and "build lock released" in out and not os.path.exists(lock), out[-300:])
    rc, out = run(sh, src + "take_build_lock 'failing brief'; false", env)
    check("L01 a failing script still releases the lock (trap on exit)",
          rc != 0 and "build lock released" in out and not os.path.exists(lock), out[-200:])
    # ---------------------------------------------------------------- L02
    holder = subprocess.Popen([sh, "-c", src + "take_build_lock 'holder'; sleep 4"], env=dict(os.environ, **env),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t0 = time.time()
    while not os.path.exists(lock) and time.time() - t0 < 10:
        time.sleep(0.1)
    t1 = time.time()
    rc, out = run(sh, src + "take_build_lock 'waiter'; echo got-it", env, timeout=40)
    waited = time.time() - t1
    holder.communicate(timeout=20)
    check("L02 a held lock makes the next caller wait (not fail) and take it when it is released",
          rc == 0 and "got-it" in out and "waiting" in out and waited >= 2.5 and "build lock taken for: waiter" in out
          and not os.path.exists(lock), "rc %s waited %.1fs\n%s" % (rc, waited, out[-300:]))
    # ---------------------------------------------------------------- L03
    os.makedirs(lock)
    with open(os.path.join(lock, "owner"), "w") as fh:
        fh.write("other-repo\nsome brief\nstarted 2026-09-26 00:00:00\nfinished 2026-09-26 01:00:00\n")
    old = time.time() - 10
    os.utime(lock, (old, old))
    rc, out = run(sh, src + "take_build_lock 'takeover'; echo mine", env, timeout=40)
    check("L03 a stale lock (older than the limit, owner finished) is taken over with a log line",
          rc == 0 and "stale" in out and "taking it over" in out and "mine" in out and not os.path.exists(lock), out[-300:])
    os.makedirs(lock)
    with open(os.path.join(lock, "owner"), "w") as fh:
        fh.write("other-repo\nsome brief\nstarted 2026-09-26 00:00:00\n")
    os.utime(lock, (old, old))
    # The right behaviour here is to wait forever, so the caller is watched for a few
    # seconds and then killed: still waiting, lock still there, never taken.
    p = subprocess.Popen([sh, "-c", src + "take_build_lock 'no-takeover'; echo mine > \"$BUILD_LOCK_DIR/../taken\""],
                         env=dict(os.environ, **env), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    time.sleep(4)
    still_waiting = p.poll() is None
    p.kill()
    p.wait()
    check("L03 an old lock whose owner did NOT report finished is waited on, never removed",
          still_waiting and os.path.exists(lock) and not os.path.exists(os.path.join(work, "taken")),
          "waiting %s lock %s" % (still_waiting, os.path.exists(lock)))
    shutil.rmtree(work, ignore_errors=True)
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

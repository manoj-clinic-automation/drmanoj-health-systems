#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PUBLISH_HEALTH.bat GATE 0 -- git is found, PROVED to run, and handed on.

Why this exists. On 2026-09-15 the publish refused with

    could not run git: [WinError 2] The system cannot find the file specified

from check C, four gates in. git was not missing: PUBLISH_HEALTH.bat has
always resolved git for its own calls out of GitHub Desktop's bundled copy --
there is no Git for Windows on this machine and none on PATH -- and simply
never passed that path to the Python child. Gate 0 now resolves it, proves it
runs, and exports it as NO_SECRETS_GIT.

A gate is only worth having if it refuses. This lifts the gate's text VERBATIM
out of PUBLISH_HEALTH.bat between `REM ---- GATE 0:` and `:git_ok` -- it does
not paraphrase it -- wraps it in a probe, and runs it three ways:

    found    the real machine: resolved, runs, and the child gets it
    broken   a git.exe that exists but is not git: must refuse
    absent   nothing on PATH and no bundled copy: must refuse

Nothing is committed, staged or pushed: everything after the gate is replaced
by the probe, and the only git command issued is `--version`.

Negative control without a batch patcher -- point it at the previous build:

    git show HEAD:PUBLISH_HEALTH.bat > %TEMP%\\prev.bat
    python3 test_publish_gate0.py %TEMP%\\prev.bat      -> must FAIL

Python 3.9.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BAT = os.path.join(os.path.dirname(HERE), "PUBLISH_HEALTH.bat")
NS = os.path.join(HERE, "NO_SECRETS.py")

PROBE = r"""
:git_ok
echo GATE0_GITEXE=%GITEXE%
echo GATE0_NO_SECRETS_GIT=%NO_SECRETS_GIT%
echo GATE0_SUMMARY=%G_GIT%
python -B -c "import importlib.util;sp=importlib.util.spec_from_file_location('n',r'{ns}');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);p,e=m.resolve_git();print('CHILD_RESOLVED='+str(p))"
exit /b 0

:summary
echo GATE0_SUMMARY=%G_GIT%
goto :eof
"""

FAILS = []


def check(cond, msg):
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        FAILS.append(msg)


def field(out, key):
    m = re.search(r"^%s=(.*)$" % re.escape(key), out, re.M)
    return m.group(1).strip() if m else None


def run_gate(gate, prelude):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "gate0.bat")
    body = ("@echo off\r\nsetlocal enabledelayedexpansion\r\n"
            'set "G_GIT=not reached"\r\n' + prelude + gate
            + PROBE.replace("{ns}", NS))
    # the shipped gate pauses on refusal; a suite must never wait for a keypress
    body = body.replace("\npause\n", "\nREM pause\n")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    proc = subprocess.Popen(["cmd", "/c", path], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=d)
    out, _ = proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace")


# Kept in step with the cases below, so a run against a build WITHOUT the gate
# reports the same names, failed, instead of reporting nothing. A guard that
# skips is not a negative control -- CLAUDE.md rule 2a.
CASES = [
    "gate 0 passes on this machine",
    "it resolved a git that exists",
    "NO_SECRETS_GIT is handed to the child",
    "NO_SECRETS_GIT is unquoted, so Python can exec it",
    "the child resolves the same git the batch did",
    "the summary row names the version",
    "a git that exists but will not run is refused",
    "the refusal says it will not run",
    "the broken case reports FAILED in the summary",
    "no git anywhere is refused",
    "the refusal names where it looked",
    "the absent case reports FAILED in the summary",
]


def main():
    bat = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BAT
    print("=" * 72)
    print("PUBLISH_HEALTH.bat GATE 0 -- git found, proved to run, handed on")
    print("file : " + bat)
    print("=" * 72)
    if not os.path.exists(bat):
        print("FATAL: not found: " + bat)
        print("RESULT: FAILURES")
        return 1
    src = open(bat, encoding="utf-8", newline="").read()
    if "REM ---- GATE 0:" not in src or ":git_ok" not in src:
        for c in CASES:
            check(False, c + " -- no GATE 0 block on this build")
        print("-" * 72)
        print("RESULT: FAILURES")
        return 1
    gate = src[src.index("REM ---- GATE 0:"):src.index(":git_ok")]
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

    # ---- 1. the real machine ---------------------------------------------
    rc, out = run_gate(gate, "")
    gitexe, nsg = field(out, "GATE0_GITEXE"), field(out, "GATE0_NO_SECRETS_GIT")
    child, summ = field(out, "CHILD_RESOLVED"), field(out, "GATE0_SUMMARY")
    check(rc == 0, CASES[0] + " (exit %d)" % rc)
    check(bool(gitexe) and os.path.exists(gitexe),
          CASES[1] + ": %s" % gitexe)
    check(nsg == gitexe, CASES[2] + ": %s" % nsg)
    check(nsg is not None and '"' not in nsg, CASES[3])
    check(child == gitexe, CASES[4] + ": %s" % child)
    check((summ or "").startswith("PASS - git version"), CASES[5] + ": %s" % summ)

    # ---- 2. present on disk, but not a git -------------------------------
    # System32 stays on PATH or `where.exe` itself disappears and the gate
    # falls through to the bundled copy for the wrong reason. The first
    # version of this harness did exactly that and reported a pass.
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "git.exe"), "w", encoding="utf-8") as fh:
        fh.write("not a binary")
    rc, out = run_gate(gate, 'set "PATH=%s;%s"\r\n' % (d, sys32))
    summ = field(out, "GATE0_SUMMARY")
    check(rc != 0, CASES[6] + " (exit %d)" % rc)
    check("will not run" in out, CASES[7])
    check((summ or "").startswith("FAILED"), CASES[8] + ": %s" % summ)

    # ---- 3. genuinely absent ----------------------------------------------
    empty = tempfile.mkdtemp()
    rc, out = run_gate(gate, 'set "PATH=%s"\r\nset "LOCALAPPDATA=%s"\r\n'
                             % (sys32, empty))
    summ = field(out, "GATE0_SUMMARY")
    check(rc != 0, CASES[9] + " (exit %d)" % rc)
    check("git.exe was not found" in out, CASES[10])
    check((summ or "").startswith("FAILED"), CASES[11] + ": %s" % summ)

    print("-" * 72)
    print("%d/%d passed" % (len(CASES) - len(FAILS), len(CASES)))
    print("RESULT: " + ("ALL PASS" if not FAILS else "FAILURES"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

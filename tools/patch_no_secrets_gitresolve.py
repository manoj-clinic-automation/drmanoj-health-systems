#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/NO_SECRETS.py  ::  find git properly, and keep refusing when it is absent

WHAT HAPPENED (2026-09-15)
--------------------------
PUBLISH_HEALTH.bat refused. Check C reported:

    could not run git: [WinError 2] The system cannot find the file specified

and the publish stopped. **git was not missing.**

  * There is no Git for Windows on this machine and there never has been:
    no `HKLM\\SOFTWARE\\GitForWindows`, no uninstall entry, nothing under
    Program Files, no App Paths entry, and no git directory on PATH -- not in
    the process environment, not in the stored User PATH, not in Machine.
  * The only git present is the one bundled inside GitHub Desktop, at
    `%LOCALAPPDATA%\\GitHubDesktop\\app-<version>\\resources\\app\\git\\cmd\\git.exe`
    -- 2.53.0.windows.4, under a folder whose name changes with every update.
  * PUBLISH_HEALTH.bat has resolved git exactly that way since it was written
    and uses it for its own add / commit / push. That is how the 14-Sep
    publish worked. It simply never passed that path to the Python child,
    which looked for a bare "git" on PATH and found nothing.
  * Check C was added on 14-Sep in 1140ed4, one commit before HEAD. So this
    is not a regression: **check C had never once run on this machine.** The
    first publish attempt with a NO_SECRETS that contains it is the one that
    refused, and it refused correctly.

WHAT THIS CHANGES
-----------------
  1. `resolve_git()` -- NO_SECRETS_GIT, then PATH, then the known installs
     including GitHub Desktop's bundled copy, newest first. Every candidate
     is *run* (`--version`) before it is trusted: existing on disk is not the
     same as working, and a half-removed install leaves the file behind.

  2. NO_SECRETS_GIT is an INSTRUCTION, not a hint. If it is set and does not
     work, that is an error and the search stops there. Being told which git
     to use and quietly using a different one is how a check ends up
     reporting on something other than what the caller meant.

  3. Nothing about check C is weakened. It still BLOCKS when it cannot run,
     and the message still says that a check which did not run is not a check
     that passed. `tools/test_no_secrets_git.py` asserts that directly, and
     the negative control breaks it on purpose to prove the assertion bites.

The fix is here as well as in PUBLISH_HEALTH.bat, because a checker that only
works when its caller happens to be one particular batch file is a checker
that will silently stop running again.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse for the negative control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NO_SECRETS.py")
MARKER = "def resolve_git"

IMPORTS_OLD = '''import os
import re
import subprocess
import sys
'''
IMPORTS_NEW = '''import glob
import os
import re
import shutil
import subprocess
import sys
'''

DOC_OLD = '''     C scans `git ls-files` -- the INDEX, so it covers both what is already
     committed and what has just been staged -- and BLOCKS on any clinical
     term outside CLINICAL_ALLOW.
'''
DOC_NEW = '''     C scans `git ls-files` -- the INDEX, so it covers both what is already
     committed and what has just been staged -- and BLOCKS on any clinical
     term outside CLINICAL_ALLOW.

     2026-09-15: C had never once run here. It looked for a bare "git" on
     PATH; this machine has no Git for Windows at all and the only git is the
     one bundled inside GitHub Desktop, which is not on PATH. See
     resolve_git() below. C still BLOCKS when it cannot run -- a check that
     did not run is not a check that passed, and that is the whole point of
     it being C rather than B.
'''

RESOLVER = '''# ---- finding git, which is a different question from "is git installed" ----
# On Windows these come apart. Here there is no Git for Windows at all and
# never has been; the only git on the machine is the copy bundled inside
# GitHub Desktop, under a version-stamped folder that changes with every app
# update, so it cannot be hardcoded either. PUBLISH_HEALTH.bat has resolved it
# that way since it was written -- this is the same search, moved into the
# checker, because the checker is what needs it and must not depend on who
# called it.
def _git_runs(path):
    """Whether this really is a git that answers.

    Existing on disk is not the same as working: a half-removed install, or a
    stale version folder left behind by an update, leaves the file there.
    """
    try:
        proc = subprocess.Popen([path, "--version"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        proc.communicate()
        return proc.returncode == 0
    except OSError:
        return False


def git_candidates():
    """Every place a git that is NOT on PATH may still be, newest first."""
    out = []
    for root in (os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 "C:\\\\Program Files", "C:\\\\Program Files (x86)"):
        if root:
            cand = os.path.join(root, "Git", "cmd", "git.exe")
            if cand not in out:
                out.append(cand)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        apps = glob.glob(os.path.join(local, "GitHubDesktop", "app-*"))
        # newest first, by mtime rather than by name: a lexical sort puts
        # app-3.6.9 above app-3.6.10, and an update leaves the old folder
        # behind for a while.
        apps.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for a in apps:
            out.append(os.path.join(a, "resources", "app", "git", "cmd",
                                    "git.exe"))
    return out


_UNSET = object()


def resolve_git(named=_UNSET):
    """(path, error). Never guesses, and never quietly gives up.

    NO_SECRETS_GIT is an INSTRUCTION, not a hint. If it is set and does not
    work that is an error, and the search does not continue: being told which
    git to use and silently using a different one is how a check ends up
    reporting on something other than what the caller meant.

    `named` defaults to $NO_SECRETS_GIT. Pass it explicitly -- None for "no
    instruction given" -- to exercise the search without editing the
    environment, which is how the refusal paths are tested.
    """
    if named is _UNSET:
        named = os.environ.get("NO_SECRETS_GIT")
    if named:
        if not os.path.exists(named):
            return None, ("NO_SECRETS_GIT names " + named
                          + ", which does not exist. It is an instruction, so "
                            "no other git was tried.")
        if not _git_runs(named):
            return None, ("NO_SECRETS_GIT names " + named
                          + ", which exists but will not run as git. It is an "
                            "instruction, so no other git was tried.")
        return named, None
    found = shutil.which("git")
    if found and _git_runs(found):
        return found, None
    tried = []
    for cand in git_candidates():
        if not os.path.exists(cand):
            tried.append(cand)
            continue
        if _git_runs(cand):
            return cand, None
        tried.append(cand + "  (present but will not run)")
    return None, ("git is not on PATH and no usable copy was found. Looked "
                  "at: " + "; ".join(tried[:6] or ["nothing"])
                  + ".  Install Git for Windows, or point NO_SECRETS_GIT at a "
                    "git.exe that runs.")


'''

RESOLVER_ANCHOR = "def tracked_files(base):\n"

PICK_OLD = '''    git = os.environ.get("NO_SECRETS_GIT") or "git"
    try:
'''
PICK_NEW = '''    git, why = resolve_git()
    if git is None:
        return None, why
    try:
'''


def build_edits():
    return [
        ("imports", IMPORTS_OLD, IMPORTS_NEW),
        ("docstring", DOC_OLD, DOC_NEW),
        ("resolver", RESOLVER_ANCHOR, RESOLVER + RESOLVER_ANCHOR),
        ("git pick", PICK_OLD, PICK_NEW),
    ]


def read(path):
    fh = open(path, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(path, text):
    fh = open(path, "w", encoding="utf-8", newline="")
    try:
        fh.write(text)
    finally:
        fh.close()


def reverse(path, out_path):
    """Reconstruct the pre-fix NO_SECRETS.py for tools/NEGATIVE_CONTROL.py."""
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " does not carry " + MARKER)
        return 1
    out, bad = src, []
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            bad.append("  " + label + ": new text found " + str(c)
                       + " times, need 1")
            break
        out = out.replace(new, anchor, 1)
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    if MARKER in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed the pre-fix build -> " + out_path
          + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT",
                    help="reconstruct the pre-fix build (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 70)
    print("NO_SECRETS.py: resolve git properly, keep refusing when it is absent")
    print("file : " + args.file)
    print("=" * 70)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0

    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits))
          + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)

    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    write(tmpf, out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-gitresolve-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("-" * 70)
    print("Next:  python3 test_no_secrets_git.py NO_SECRETS.py")
    print("       python3 ../tools/NEGATIVE_CONTROL.py "
          "--manifest new_assertions_gitresolve.json")
    print("Back:  copy " + bak + " over " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

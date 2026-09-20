#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An assertion is evidence only if it has been seen to fail.

WHY THIS EXISTS
---------------
Twice on this project a green suite meant nothing.

  * v3.4.0 shipped with two deleted JavaScript functions at 18/18, because
    the server suites never run page JS.
  * test_ui_now.py reported 76 PASS / 0 FAIL against a build with no Watch
    card at all, because the whole block sat behind `if locator.count():`
    and skipped. Absence of evidence was printed as evidence of absence.

Both times the fix was the same: make the check fail on purpose and look.
This harness does that automatically, so it cannot be forgotten.

WHAT IT DOES
------------
For every assertion declared new in a manifest, it must SEE that assertion
fail before the suite is allowed to count as evidence. There are two ways an
assertion can be seen failing, and each declared assertion must use one:

  control: "version"
      Run the suite against the PREVIOUS build of the app and require the
      assertion to be in that run's failure list. The previous build is
      reconstructed by running the patcher in --reverse, so no copy of the
      old code has to be kept anywhere.

  control: "mutation"
      Some new assertions guard behaviour the previous build ALREADY had --
      the previous build does not scroll sideways either, so a version
      control cannot make that assertion fail. For those, break the property
      on purpose in a copy of the CURRENT app and require the assertion to
      catch it. This is strictly the stronger control: it shows the assertion
      responds to the thing it claims to measure, not merely to a version
      change that happened to move several things at once.

      A mutation may name a "module" -- a sidecar file beside app.py that
      app.py imports (RxGuard's dose_ceiling.py, say). The harness cannot
      break a sidecar by editing app.py, and it must never edit the real
      file, so it copies the app's whole folder to a sibling
      "_nc_mod_<id>" folder, breaks the module in the COPY, and runs the
      suite against the copy's app.py. The copy imports its own broken
      module; the real folder is never written.

      One assertion may be declared more than once with different
      controls (a sidecar mutation AND an app.py mutation, say); every
      declaration must be seen failing on its own.

Finally it runs the suite against the current build and requires ALL PASS.
A manifest entry that is never seen failing is a hard failure here, with the
assertion named, whatever the suite itself reported.

USAGE
    python3 NEGATIVE_CONTROL.py --manifest gutlog/new_assertions_v3160.json

Every path in the manifest is relative to the manifest's own directory.
Python 3.9.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

FAIL_RX = re.compile(r"^\[FAIL\]\s*(.*)$", re.M)


def run_suite(suite, app_path, label):
    print("  running %s against %s ..." % (os.path.basename(suite), label))
    proc = subprocess.run([sys.executable, "-B", suite, app_path],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    fails = [m.strip() for m in FAIL_RX.findall(out)]
    passed = "RESULT: ALL PASS" in out
    # A suite that dies part-way prints no RESULT line and reports no
    # failures, which would mark every declared assertion UNSEEN for a reason
    # that has nothing to do with the assertions. Say so instead of letting it
    # look like a measurement.
    if "RESULT:" not in out:
        print("    -> the suite did not finish. Last 15 lines:")
        for line in out.rstrip().split("\n")[-15:]:
            print("       " + line)
        raise SystemExit("negative control aborted: suite crashed against "
                         + label)
    print("    -> %d failure line(s), all-pass=%s" % (len(fails), passed))
    return out, fails, passed


def hits(name, fails):
    return sum(1 for f in fails if name in f)


def seen(entry, fails):
    """A family entry names a substring shared by several assertions (the
    six tabs at one width and theme, say). min_hits keeps that honest: one
    tab failing is not evidence that the other five are being measured."""
    return hits(entry["name"], fails) >= entry.get("min_hits", 1)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--keep", action="store_true",
                    help="keep the reconstructed and mutated files for "
                         "inspection instead of removing them")
    args = ap.parse_args()

    mpath = os.path.abspath(args.manifest)
    base = os.path.dirname(mpath)
    man = json.loads(read(mpath))
    suite = os.path.join(base, man["suite"])
    app = os.path.join(base, man["app"])
    patcher = os.path.join(base, man["patcher"])
    entries = man["assertions"]

    print("=" * 72)
    print("NEGATIVE CONTROL -- an assertion is evidence only if it has been")
    print("                    seen to fail.")
    print("=" * 72)
    print("suite    : " + suite)
    print("app      : " + app)
    print("patcher  : " + patcher)
    print("declared : %d new assertion(s)" % len(entries))
    print("")

    for p in (suite, app, patcher):
        if not os.path.exists(p):
            print("FATAL: not found: " + p)
            return 1

    # The variants have to live BESIDE app.py, not in a temp directory:
    # test_ui_now.py puts the app's own folder on sys.path and points
    # GUTLOG_ICONS at it, so a copy anywhere else cannot import
    # import_records.py and the suite dies part-way with FileNotFoundError --
    # which reads as "0 failures" and would quietly defeat the whole control.
    work = os.path.dirname(os.path.abspath(app))
    made = []
    verdict = {}

    # ---- 1. the previous build -------------------------------------------
    prev = os.path.join(work, "_nc_prev.py")
    made.append(prev)
    print("[1/3] reconstructing the previous build")
    rc = subprocess.run([sys.executable, "-B", patcher,
                         "--file", app, "--reverse", prev])
    if rc.returncode != 0 or not os.path.exists(prev):
        print("FATAL: could not reconstruct the previous build.")
        return 1
    _, prev_fails, _ = run_suite(suite, prev, "the PREVIOUS build")
    for e in entries:
        if e.get("control") == "version":
            verdict[id(e)] = ("version", seen(e, prev_fails))
    print("")

    # ---- 2. mutations ----------------------------------------------------
    groups = {}
    for e in entries:
        if e.get("control") == "mutation":
            groups.setdefault(e["mutation"]["id"], []).append(e)
    print("[2/3] mutation controls (%d group(s))" % len(groups))
    src = read(app)
    for gid, members in groups.items():
        mut = members[0]["mutation"]
        find, repl = mut["find"], mut["replace"]
        module = mut.get("module")
        if module:
            mod_real = os.path.join(work, module)
            if (os.path.dirname(os.path.abspath(mod_real)) != work
                    or not os.path.isfile(mod_real)):
                print("  MUTATION '%s': module '%s' is not a file beside "
                      "app.py." % (gid, module))
                for e in members:
                    verdict[id(e)] = ("mutation:" + gid, False)
                continue
            target_src = read(mod_real)
        else:
            target_src = src
        n = target_src.count(find)
        if n != 1:
            print("  MUTATION '%s': anchor found %d times in %s, need 1 -- "
                  "cannot break this property on purpose."
                  % (gid, n, module or os.path.basename(app)))
            for e in members:
                verdict[id(e)] = ("mutation:" + gid, False)
            continue
        if module:
            # A sibling copy of the whole folder, so every file the suite or
            # the app reaches beside app.py -- or one level up -- is where it
            # expects. Databases, backups and caches are not copied.
            copy_dir = os.path.join(os.path.dirname(work), "_nc_mod_" + gid)
            if os.path.exists(copy_dir):
                shutil.rmtree(copy_dir)
            shutil.copytree(work, copy_dir, ignore=shutil.ignore_patterns(
                "__pycache__", "_nc_*", "*.db", "*.db-*", "*.bak", "*.bak.*"))
            made.append(copy_dir)
            write(os.path.join(copy_dir, module),
                  target_src.replace(find, repl, 1))
            mpath2 = os.path.join(copy_dir, os.path.basename(app))
            label = "the MUTATED module %s [%s]" % (module, gid)
        else:
            mpath2 = os.path.join(work, "_nc_mut_" + gid + ".py")
            made.append(mpath2)
            write(mpath2, src.replace(find, repl, 1))
            label = "the MUTATED build [" + gid + "]"
        print("  mutation '%s': %s" % (gid, mut.get("why", "")))
        _, mut_fails, _ = run_suite(suite, mpath2, label)
        for e in members:
            verdict[id(e)] = ("mutation:" + gid, seen(e, mut_fails))
    print("")

    # ---- 3. the current build --------------------------------------------
    print("[3/3] the current build")
    _, cur_fails, cur_pass = run_suite(suite, app, "the CURRENT build")
    print("")

    # ---- report -----------------------------------------------------------
    print("-" * 72)
    bad = []
    for e in entries:
        how, ok = verdict.get(id(e), ("none", False))
        print("  [%s] %-58s  %s" % ("SEEN" if ok else "UNSEEN",
                                    e["name"][:58], how))
        if not ok:
            bad.append(e["name"] + "  (" + how + ")")
    print("-" * 72)
    print("declared new assertions : %d" % len(entries))
    print("seen to fail            : %d" % (len(entries) - len(bad)))
    print("current build all-pass  : %s" % cur_pass)
    if cur_fails:
        print("current build failures  :")
        for f in cur_fails:
            print("    " + f)
    if bad:
        print("")
        print("NOT EVIDENCE -- these assertions were never observed failing:")
        for b in bad:
            print("    " + b)
    if not args.keep:
        for f in made:
            try:
                if os.path.isdir(f):
                    shutil.rmtree(f)
                else:
                    os.remove(f)
            except OSError:
                pass
    else:
        print("kept: " + ", ".join(made))
    okall = cur_pass and not bad
    print("")
    print("RESULT: " + ("PASS" if okall else "FAIL"))
    return 0 if okall else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Negative control for a tool that has no previous build.

tools/NEGATIVE_CONTROL.py always starts by reconstructing the PREVIOUS build
with the patcher's --reverse. health_mirror.py is a new standalone file: it
was never patched into existence, so there is nothing to reverse and that
stage cannot apply. Running it against a stand-in patcher would be theatre.

What DOES apply, and is the stronger control anyway, is the mutation stage:
break the property on purpose in a copy and require the named assertion to
catch it. An assertion never seen failing fails this gate by name, exactly
as it does in the harness proper.

    python3 NC_MIRROR.py --manifest new_assertions_mirror.json

Python 3.9.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def run_suite(suite, tool, label):
    env = dict(os.environ)
    env["MIRROR_TOOL"] = tool
    print("  running %s against %s ..." % (os.path.basename(suite), label))
    p = subprocess.Popen([sys.executable, "-B", suite],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    out, _ = p.communicate()
    text = out.decode("utf-8", "replace")
    fails = [l for l in text.split("\n") if l.startswith("[FAIL]")]
    allpass = "RESULT: ALL PASS" in text
    print("    -> %d failure line(s), all-pass=%s" % (len(fails), allpass))
    return text, fails, allpass


def seen(name, fails):
    key = name.strip().lower()[:40]
    return any(key in f.lower() for f in fails)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    a = ap.parse_args()
    mpath = os.path.abspath(a.manifest)
    base = os.path.dirname(mpath)
    man = json.loads(read(mpath))
    suite = os.path.join(base, man["suite"])
    tool = os.path.join(base, man["tool"])
    entries = man["assertions"]

    print("=" * 72)
    print("NEGATIVE CONTROL (mutation only) -- an assertion is evidence only")
    print("                                    if it has been seen to fail.")
    print("=" * 72)
    print("suite : " + suite)
    print("tool  : " + tool)
    print("declared : %d new assertion(s)" % len(entries))
    print("")
    for p in (suite, tool):
        if not os.path.exists(p):
            print("FATAL: not found: " + p)
            return 1

    src = read(tool)
    work = tempfile.mkdtemp(prefix="nc_mirror_")
    verdict = {}

    # __TERM__ in a replacement is filled at RUN time from the gitignored
    # clinical terms list. That is how a mutation can insert a real medicine
    # name to be caught, without any tracked file having to spell one --
    # which is the very property the assertion it proves is about.
    term = None
    tpath = os.path.join(os.path.dirname(base), "tools", "clinical_terms.local.txt")
    if os.path.exists(tpath):
        for line in open(tpath, encoding="utf-8"):
            s = line.strip().lower()
            if s and not s.startswith("#") and len(s) > 5 and s.isalpha():
                term = s
                break
    if any("__TERM__" in e["mutation"]["replace"] for e in entries) and not term:
        print("FATAL: a mutation needs a clinical term and the list is not here.")
        print("       tools/clinical_terms.local.txt is gitignored; without it")
        print("       that control DID NOT RUN, which is not the same as passing.")
        return 1

    print("[1/2] mutations")
    for i, e in enumerate(entries):
        m = e["mutation"]
        if m["find"] not in src:
            print("FATAL: mutation %r does not match the tool. Nothing proved."
                  % m["id"])
            return 1
        if src.count(m["find"]) != 1:
            print("FATAL: mutation %r matches %d times, need 1."
                  % (m["id"], src.count(m["find"])))
            return 1
        repl = m["replace"].replace("__TERM__", term) if term else m["replace"]
        broken = os.path.join(work, "_nc_%s.py" % m["id"])
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write(src.replace(m["find"], repl, 1))
        print("  mutation %r: %s" % (m["id"], m["why"]))
        _t, fails, _ap = run_suite(suite, broken, "the MUTATED tool [%s]" % m["id"])
        verdict[i] = seen(e["name"], fails)
    print("")

    print("[2/2] the current tool")
    _t, fails, allpass = run_suite(suite, tool, "the CURRENT tool")
    print("")

    print("-" * 72)
    ok = True
    for i, e in enumerate(entries):
        good = verdict.get(i, False)
        ok = ok and good
        print("  [%s] %-52s mutation:%s"
              % ("SEEN" if good else "NOT SEEN", e["name"][:52], e["mutation"]["id"]))
    print("-" * 72)
    print("declared new assertions : %d" % len(entries))
    print("seen to fail            : %d" % sum(1 for i in range(len(entries))
                                               if verdict.get(i)))
    print("current build all-pass  : %s" % allpass)
    print("")
    shutil.rmtree(work, ignore_errors=True)
    if not ok or not allpass:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

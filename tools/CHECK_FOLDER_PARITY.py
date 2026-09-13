#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Refuse to publish if a file in the working folder has drifted from the app
folder it belongs to.

The rule
--------
fitlog-ingest/ is the owner's WORKING folder. The app folders - fitlog/,
gutlog/, rxguard/, ops/ - are authoritative. A file that exists in the
working folder AND in exactly one app folder is the same artefact in two
places, and the two must be byte-identical.

Directional on purpose. A flat "same basename must match anywhere" rule is
wrong here: app.py exists in fitlog/, gutlog/ AND rxguard/ and those are
three different applications that merely share a filename. Comparing them
would block every publish forever.

  0 app folders hold the name -> new work, nothing to compare
  1 app folder  holds it      -> must match byte-for-byte, else REFUSE
  2+ app folders hold it      -> home is ambiguous, WARN and carry on

Why bytes, not content
----------------------
A Windows text-mode patcher rewrites LF to CRLF. The code stays identical
and every test stays green while the file stops matching the server. That is
exactly the failure this exists to catch, so a line-ending-only difference is
still a failure - reported under its own heading, because the fix differs
from a real content difference.

Why it blocks
-------------
A stale copy is silent. fitlog/test_activity_feed.py imports health_ingest
from its OWN folder, so it will happily pass against a module that is not the
one on the server. On 2026-09-13 fitlog/health_ingest.py was a full deploy
behind the working copy (32,783 bytes against 45,835) and the suite was one
run away from proving nothing.

Exit 0 when everything agrees, 1 otherwise.
Python 3.9 compatible.

Usage:
    python3 tools/CHECK_FOLDER_PARITY.py [repo_root]
"""
import os
import sys

WORKING = "fitlog-ingest"
APP_DIRS = ("fitlog", "gutlog", "rxguard", "ops")
EXTS = (".py",)


def read(path):
    fh = open(path, "rb")
    try:
        return fh.read()
    finally:
        fh.close()


def listing(path):
    if not os.path.isdir(path):
        return set()
    return set(f for f in os.listdir(path)
               if os.path.isfile(os.path.join(path, f))
               and f.endswith(EXTS))


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))

    work_dir = os.path.join(root, WORKING)
    if not os.path.isdir(work_dir):
        print("SKIP: " + WORKING + "/ not found - nothing to compare.")
        return 0

    apps = {}
    for name in APP_DIRS:
        files = listing(os.path.join(root, name))
        if files:
            apps[name] = files

    work = sorted(listing(work_dir))
    if not work:
        print("No files to check in " + WORKING + "/.")
        return 0

    compared = 0
    unique = 0
    ambiguous = []
    differ = []
    eol_only = []

    for fname in work:
        homes = [a for a in APP_DIRS if a in apps and fname in apps[a]]
        if not homes:
            unique += 1
            continue
        if len(homes) > 1:
            ambiguous.append((fname, homes))
            continue
        home = homes[0]
        a = read(os.path.join(root, home, fname))
        b = read(os.path.join(work_dir, fname))
        compared += 1
        if a == b:
            continue
        if a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n"):
            eol_only.append((fname, home, len(a), len(b)))
        else:
            differ.append((fname, home, len(a), len(b)))

    print("Working folder : " + WORKING + "/  (" + str(len(work)) + " files)")
    print("App folders    : " + ", ".join(sorted(apps)) if apps
          else "App folders    : none found")
    print("Paired + checked: " + str(compared)
          + "   working-folder-only: " + str(unique))

    for fname, homes in ambiguous:
        print("   note: " + fname + " exists in " + ", ".join(homes)
              + " - home is ambiguous, not compared.")

    if not differ and not eol_only:
        print("OK: every paired file is byte-identical to its app folder.")
        return 0

    print("")
    print("!! REFUSING - a working copy has drifted from its app folder.")
    print("   The app folder is authoritative (see CLAUDE.md).")
    print("")
    for fname, home, na, nb in eol_only:
        print("   LINE ENDINGS ONLY: " + fname
              + "  (" + home + "=" + str(na)
              + ", " + WORKING + "=" + str(nb) + ")")
    if eol_only:
        print("      Same code, different line endings. A patcher was run on")
        print("      Windows without newline=\"\" . Copy the server's file back")
        print("      over both, and fix the patcher.")
        print("")
    for fname, home, na, nb in differ:
        print("   CONTENT DIFFERS : " + fname
              + "  (" + home + "=" + str(na)
              + ", " + WORKING + "=" + str(nb) + ")")
    if differ:
        print("      One of these is stale. Decide which is right - normally")
        print("      whatever is deployed on the server - and copy it over the")
        print("      other. Do not merge by hand.")
    print("")
    print("   NOTHING committed or pushed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/NO_SECRETS.py check D -- no tracked binaries outside an allowlist.

A PNG is the one thing this checker can never read. Nine of them were pictures
of the health record and sat in a PUBLIC repo through every clean run, because
A, B and C read text. D is the structural answer, and the assertion that has to
hold is the simple one: **adding any new tracked binary must fail it.**

Most of the work here happens in a THROWAWAY git repository built in a temp
directory, so the real repo's index is never touched. That also makes the
"adding a PNG" case a real one rather than a simulated one: a file is created,
`git add`ed, and NO_SECRETS is run against that tree.

  python3 test_no_secrets_binaries.py [path/to/NO_SECRETS.py]

Python 3.9.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

RESULTS = []
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ns_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 \
        else os.path.join(here, "NO_SECRETS.py")
    repo = os.path.dirname(here)
    os.environ.pop("NO_SECRETS_GIT", None)
    ns = load("ns_bin_under_test", ns_path)
    git, gerr = (ns.resolve_git() if hasattr(ns, "resolve_git")
                 else (None, "no resolve_git"))
    if not git:
        print("FATAL: no git to build a scratch repository with: " + str(gerr))
        print("RESULT: FAILURES")
        return 1
    ctx = {}

    def scratch(files):
        """A throwaway git repo containing `files` {relpath: bytes}, added."""
        d = tempfile.mkdtemp()
        subprocess.run([git, "init", "-q"], cwd=d, check=True)
        for rel, data in files.items():
            full = os.path.join(d, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(data)
        subprocess.run([git, "add", "-A", "-f", "."], cwd=d, check=True)
        return d

    def run_ns(base):
        lst = os.path.join(tempfile.mkdtemp(), "staged.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("")
        proc = subprocess.Popen(
            [sys.executable, "-B", ns_path, "--files-from", lst, base],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        return proc.returncode, out.decode("utf-8", "replace")

    # ------------------------------------------------------------------ 01
    def t01():
        assert hasattr(ns, "BINARY_ALLOW"), "there is no BINARY_ALLOW"
        assert set(ns.BINARY_ALLOW) == {"gutlog/icon-192.png",
                                        "gutlog/icon-512.png"}, \
            "the allowlist is not the two icons: " + str(sorted(ns.BINARY_ALLOW))
        for path, why in ns.BINARY_ALLOW.items():
            assert isinstance(why, str) and len(why) > 25, \
                path + " carries no real reason: " + repr(why)
        return "2 entries, each with a reason"
    check("01 the allowlist is the two icons and every entry states its reason", t01)

    # ------------------------------------------------------------------ 02
    def t02():
        d = scratch({"README.md": b"hello\n", "docs/shot.png": PNG})
        rc, out = run_ns(d)
        assert rc != 0, "a newly tracked PNG was allowed through (exit 0)"
        assert "docs/shot.png" in out, "the offending path was not named:\n" + out[-500:]
        assert "binary or image extension" in out, "no reason given:\n" + out[-500:]
        shutil.rmtree(d, ignore_errors=True)
        return "refused and named it"
    check("02 adding any new tracked PNG fails the check, by name", t02)

    # ------------------------------------------------------------------ 03
    def t03():
        # The obvious bypass. An extension rule alone is defeated by renaming.
        d = scratch({"notes.md": PNG})
        rc, out = run_ns(d)
        assert rc != 0, "a PNG renamed .md was allowed through"
        assert "notes.md" in out and "starts like a PNG" in out, \
            "the disguise was not named:\n" + out[-500:]
        shutil.rmtree(d, ignore_errors=True)
        return "a PNG called notes.md is still caught"
    check("03 binary content under a text extension is caught too", t03)

    # ------------------------------------------------------------------ 04
    def t04():
        # A zip is the same blind spot as a PNG and must be treated alike.
        d = scratch({"bundle.zip": b"PK\x03\x04" + b"\x00" * 20})
        rc, out = run_ns(d)
        assert rc != 0, "a tracked zip was allowed through"
        assert "bundle.zip" in out, "the zip was not named:\n" + out[-400:]
        shutil.rmtree(d, ignore_errors=True)
        return "a zip is refused like any other unreadable file"
    check("04 a tracked archive is refused on the same footing as an image", t04)

    # ------------------------------------------------------------------ 05
    def t05():
        # An allowlisted path must NOT be refused, or the allowlist is
        # decoration and the check would have to be switched off to ship.
        d = scratch({"gutlog/icon-192.png": PNG, "gutlog/app.py": b"x = 1\n"})
        rc, out = run_ns(d)
        assert "tracked-binary check:" in out, "D did not report:\n" + out[-400:]
        assert "icon-192" not in out.split("tracked-binary check:")[0][-600:], \
            "an allowlisted icon was refused:\n" + out[-500:]
        assert rc == 0, "an allowlisted-only tree was refused: exit %d\n%s" % (rc, out[-500:])
        shutil.rmtree(d, ignore_errors=True)
        return "the two icons pass, so the allowlist is real"
    check("05 an allowlisted binary is not refused", t05)

    # ------------------------------------------------------------------ 06
    def t06():
        # D shares C's git resolution, so it inherits C's refusal. A check
        # that cannot run must never read as a check that passed.
        d = scratch({"docs/shot.png": PNG})
        env = dict(os.environ)
        env["NO_SECRETS_GIT"] = os.path.join(tempfile.mkdtemp(), "absent.exe")
        lst = os.path.join(tempfile.mkdtemp(), "staged.txt")
        open(lst, "w", encoding="utf-8").write("")
        proc = subprocess.Popen(
            [sys.executable, "-B", ns_path, "--files-from", lst, d],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        out, _ = proc.communicate()
        out = out.decode("utf-8", "replace")
        assert proc.returncode != 0, "D passed while unable to reach git"
        assert "tracked-binary check DID NOT RUN" in out, \
            "D did not say it could not run:\n" + out[-500:]
        shutil.rmtree(d, ignore_errors=True)
        return "exit %d, and it says the binary check did not run" % proc.returncode
    check("06 with git unreachable the binary check refuses rather than passing", t06)

    # ------------------------------------------------------------------ 07
    def t07():
        # The real tree. This is the one that has to be true before a publish.
        rc, out = run_ns(repo)
        line = [l.strip() for l in out.split("\n") if "tracked-binary check:" in l]
        assert line, "D did not report on the real tree:\n" + out[-700:]
        ctx["live"] = line[0]
        assert rc == 0, "the real tree still carries tracked binaries: exit %d\n%s" \
                        % (rc, out[-900:])
        return line[0]
    check("07 the real tree carries no tracked binary outside the allowlist", t07)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("NO_SECRETS.py check D -- no tracked binaries outside an allowlist")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/NO_SECRETS.py -- git resolution, and the refusal that must survive it.

The whole risk in this change is in one direction. Making check C able to find
git is easy; making it able to find git WITHOUT ever letting it pass when it
cannot run is the part worth testing, because that failure mode is silent and
looks exactly like a clean publish.

So this suite asserts both halves, and the negative control breaks the refusal
on purpose (manifest `weaken`, which drops tracked_err from the blocking test)
to show the assertions bite rather than merely agreeing with the status quo.

Nothing here writes to the repository or runs a git that changes anything:
the only git commands issued are `--version` and `ls-files`.

  python3 test_no_secrets_git.py [path/to/NO_SECRETS.py]

Python 3.9.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

RESULTS = []


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


def run_ns(ns_path, base, env_over, args=()):
    """NO_SECRETS.py in a child process. Returns (returncode, output)."""
    env = dict(os.environ)
    env.update(env_over)
    for k, v in list(env.items()):
        if v is None:
            del env[k]
    proc = subprocess.Popen([sys.executable, "-B", ns_path] + list(args) + [base],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env)
    out, _ = proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ns_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 \
        else os.path.join(here, "NO_SECRETS.py")
    repo = os.path.dirname(here)
    ctx = {}

    # The module is imported with NO_SECRETS_GIT cleared, so the resolver is
    # exercised as it will be when nothing has told it anything.
    os.environ.pop("NO_SECRETS_GIT", None)
    ns = load("ns_under_test", ns_path)

    # ------------------------------------------------------------------ 01
    def t01():
        assert hasattr(ns, "resolve_git"), "there is no resolve_git to test"
        path, err = ns.resolve_git()
        assert path and not err, "no git resolved: " + str(err)
        assert os.path.exists(path), "resolved a path that does not exist: " + path
        assert ns._git_runs(path), "resolved a git that does not run: " + path
        ctx["git"] = path
        return path
    check("01 git is resolved on this machine without being on PATH", t01)

    # ------------------------------------------------------------------ 02
    def t02():
        # The point of the change: check C must now actually run and report a
        # count, not refuse. Before the fix this raised WinError 2 here.
        files, err = ns.tracked_files(repo)
        assert err is None, "tracked_files still could not run git: " + str(err)
        assert files and len(files) > 50, \
            "git ls-files returned %s paths, which is not a real answer" \
            % (len(files) if files is not None else "no")
        ctx["n"] = len(files)
        return "%d tracked paths read from the index" % len(files)
    check("02 check C reads the index instead of refusing", t02)

    # ------------------------------------------------------------------ 03
    def t03():
        missing = os.path.join(tempfile.mkdtemp(), "not-a-git.exe")
        path, err = ns.resolve_git(missing)
        assert path is None, "a named git that does not exist was accepted: " + str(path)
        assert "does not exist" in err and missing in err, "reason: " + str(err)
        assert "no other git was tried" in err, \
            "an unusable instruction fell through to a search: " + err
        return "refused, and named the instruction rather than searching past it"
    check("03 NO_SECRETS_GIT naming a missing file refuses, and does not fall back", t03)

    # ------------------------------------------------------------------ 04
    def t04():
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "git.exe")
        open(fake, "w", encoding="utf-8").write("not a binary")
        path, err = ns.resolve_git(fake)
        assert path is None, "a file that is not git was accepted: " + str(path)
        assert "will not run as git" in err, "reason: " + str(err)
        return "present on disk is not the same as usable, and it says so"
    check("04 a named git that exists but will not run is refused too", t04)

    # ------------------------------------------------------------------ 05
    def t05():
        # Genuine absence: nothing named, nothing on PATH, no candidate
        # anywhere. This is the case the owner asked to be sure of, and it is
        # tested through the real code path rather than through a flag.
        real_path, real_cands = os.environ.get('PATH', ''), ns.git_candidates
        try:
            os.environ['PATH'] = ''
            ns.git_candidates = lambda: []
            path, err = ns.resolve_git(None)
            assert path is None, "git was resolved out of nowhere: " + str(path)
            assert "not on PATH" in err and "no usable copy" in err, "reason: " + str(err)
            files, terr = ns.tracked_files(repo)
            assert files is None and terr, \
                "tracked_files returned %s with git absent" % (files,)
        finally:
            os.environ['PATH'], ns.git_candidates = real_path, real_cands
        return "no git anywhere: resolve returns None and tracked_files errors"
    check("05 with git genuinely absent the resolver finds nothing and says why", t05)

    # ------------------------------------------------------------------ 06
    def t06():
        # End to end, as PUBLISH_HEALTH.bat runs it. THIS is the assertion
        # that matters: a run that cannot reach git must EXIT NON-ZERO and say
        # the check did not run. It must never be mistaken for a clean pass.
        # --files-from with a clean list, deliberately. A bare run walks the
        # working directory, finds the gitignored .bak rollback copies, and
        # blocks on check A -- so the exit code would be 1 whether or not
        # check C still refuses, and the assertion would pass against a build
        # with check C torn out entirely. The negative control caught exactly
        # that on 2026-09-15. With nothing for A or B to say, the only thing
        # that can block here is C refusing to run, which is the claim.
        missing = os.path.join(tempfile.mkdtemp(), "absent-git.exe")
        lst = os.path.join(tempfile.mkdtemp(), "clean.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("CHANGELOG.md\n")
        rc, out = run_ns(ns_path, repo, {"NO_SECRETS_GIT": missing},
                         args=("--files-from", lst))
        assert "secrets check: clean" in out, \
            "something other than check C is blocking, so this proves nothing:\n" \
            + out[-600:]
        assert rc != 0, "NO_SECRETS exited 0 with git unreachable -- check C passed " \
                        "when it could not run"
        assert "REFUSING" in out and "DID NOT RUN" in out, \
            "the refusal was not stated plainly:\n" + out[-600:]
        assert "is not a check that passed" in out, \
            "the message no longer explains why a non-run blocks"
        return "exit %d, and the output says the check did not run" % rc
    check("06 NO_SECRETS still refuses, loudly, when git cannot be reached", t06)

    # ------------------------------------------------------------------ 07
    def t07():
        # git works, but the directory is not a repository: ls-files exits
        # non-zero. That must block too, and must not read as "no findings".
        outside = tempfile.mkdtemp()
        rc, out = run_ns(ns_path, outside, {})
        assert rc != 0, "a non-repository was treated as a clean tree"
        assert "DID NOT RUN" in out, "the reason was not reported:\n" + out[-400:]
        return "a working git in a non-repository blocks as well"
    check("07 a working git that cannot answer also blocks", t07)

    # ------------------------------------------------------------------ 08
    def t08():
        # Invoked the way PUBLISH_HEALTH.bat invokes it: --files-from a staged
        # list. Bare mode walks the whole working directory instead and picks
        # up the .bak rollback copies lying beside each app.py, which are
        # gitignored and never staged -- so a bare run here would be asserting
        # something the publish never does.
        lst = os.path.join(tempfile.mkdtemp(), "staged.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("CHANGELOG.md\ntools/NO_SECRETS.py\n")
        rc, out = run_ns(ns_path, repo, {}, args=("--files-from", lst))
        assert "tracked-file clinical check:" in out, \
            "check C did not report a count:\n" + out[-600:]
        assert "files carried by git" in out, "the count line changed shape"
        assert rc == 0, "the real tree does not pass: exit %d\n%s" % (rc, out[-900:])
        ctx["live"] = [ln.strip() for ln in out.split("\n")
                       if "files carried by git" in ln]
        return (ctx["live"][0] if ctx["live"] else "")[:90]
    check("08 against the real tree check C runs and the tree passes", t08)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("NO_SECRETS.py -- git resolution, and the refusal that must survive it")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

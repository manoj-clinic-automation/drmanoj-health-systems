#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.8.2 -- one session key for every worker (RXGUARD_V182_SECRETFILE).

Scratch databases only; never touches a live key.

  python3 test_secret_file.py [path/to/rxguard/app.py]
"""
import importlib.util
import os
import stat
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


CHILD = r'''
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("rx_child", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.dirname(sys.argv[1]))
spec.loader.exec_module(m)
while time.time() < float(sys.argv[3]):
    time.sleep(0.001)
print("KEY=" + m.create_app(db_path=sys.argv[2]).secret_key)
'''


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    os.environ.pop("RXGUARD_SECRET", None)
    sys.path.insert(0, os.path.dirname(app_path))
    spec = importlib.util.spec_from_file_location("rx_secret", app_path)
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    w = tempfile.mkdtemp()

    def t01():
        db = os.path.join(w, "a.db")
        a = rx.create_app(db_path=db)
        b = rx.create_app(db_path=db)
        assert a.secret_key == b.secret_key, "two workers got two different keys"
        s = a.session_interface.get_signing_serializer(a).dumps({"auth": 1})
        got = b.session_interface.get_signing_serializer(b).loads(s)
        assert got == {"auth": 1}, got
        return "a session signed by one worker is accepted by the other"
    check("01 two workers share one key without RXGUARD_SECRET", t01)

    def t02():
        db = os.path.join(w, "a.db")
        p = db + ".secret"
        assert os.path.exists(p), "no key file beside the database"
        mode = stat.S_IMODE(os.stat(p).st_mode)
        assert mode == 0o600, "key file mode is %o" % mode
        key = open(p).read().strip()
        assert len(key) == 64 and all(c in "0123456789abcdef" for c in key), "not a 64-hex key"
        c = rx.create_app(db_path=db)
        assert c.secret_key == key, "a restart did not reuse the key"
        assert not [f for f in os.listdir(w) if f.endswith(".tmp")], "temporary file left behind"
        return "created once, mode 600, reused after a restart"
    check("02 the key file is private and survives a restart", t02)

    def t03():
        db = os.path.join(w, "b.db")
        os.environ["RXGUARD_SECRET"] = "env-secret-not-real-0123456789abcdef"
        try:
            a = rx.create_app(db_path=db)
        finally:
            os.environ.pop("RXGUARD_SECRET", None)
        assert a.secret_key == "env-secret-not-real-0123456789abcdef", "environment key not used"
        assert not os.path.exists(db + ".secret"), "key file written although the environment had one"
        e = rx.create_app(db_path=db, secret="explicit-not-real")
        assert e.secret_key == "explicit-not-real"
        return "RXGUARD_SECRET and an explicit key still win; no file written"
    check("03 an environment key still wins", t03)

    def t04():
        db = os.path.join(w, "c.db")
        open(db + ".secret", "w").write("short\n")
        a = rx.create_app(db_path=db)
        assert a.secret_key and a.secret_key != "short", "a broken key file was used as the key"
        assert open(db + ".secret").read() == "short\n", "a broken key file was overwritten"
        return "a broken key file neither stops the app nor gets overwritten"
    check("04 a broken key file does not stop the app", t04)

    def t05():
        import time
        db = os.path.join(w, "d.db")
        go = "%.3f" % (time.time() + 1.5)
        procs = [subprocess.Popen([sys.executable, "-B", "-c", CHILD, app_path, db, go],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                 for _ in range(4)]
        keys = []
        for p in procs:
            out = p.communicate(timeout=60)[0].decode("utf-8", "replace")
            ks = [l[4:] for l in out.splitlines() if l.startswith("KEY=")]
            assert ks, "a worker did not start: " + out[-300:]
            keys.append(ks[-1])
        assert len(set(keys)) == 1, "workers started together got %d keys" % len(set(keys))
        assert open(db + ".secret").read().strip() == keys[0]
        return "four workers started at the same instant agree on one key"
    check("05 workers starting together agree", t05)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("RxGuard v1.8.2 -- one session key for every worker")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

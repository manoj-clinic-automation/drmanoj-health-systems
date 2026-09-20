#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.7.1 -- /health tells the truth (FITLOG_V171_VERSION).

  python3 test_health_version.py [path/to/fitlog/app.py]
"""
import importlib.util
import os
import re
import sqlite3
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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "app.py")
    w = tempfile.mkdtemp()
    db = os.path.join(w, "t.db")
    env = os.path.join(w, "e.env")
    open(env, "w").write("FITLOG_INGEST_TOKEN=hv-smoke\nFITLOG_HC_TOKEN=hv-hc\n")
    os.environ.update(FITLOG_DB=db, FITLOG_INGEST_ENV=env, FITLOG_GUTLOG_FEED="0")
    sqlite3.connect(db).close()
    sys.path.insert(0, os.path.dirname(app_path))
    import migrate_health_ingest  # noqa: E402
    sys.argv = ["m", db]
    migrate_health_ingest.main()
    spec = importlib.util.spec_from_file_location("fitlog_hv", app_path)
    fl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fl)
    fl.app.config["TESTING"] = True
    cl = fl.app.test_client()
    src = open(app_path, encoding="utf-8").read()

    def t01():
        j = cl.get("/health").get_json()
        assert j and j.get("ok") is True, j
        assert hasattr(fl, "APP_VERSION"), "no single APP_VERSION constant"
        assert j["version"] == fl.APP_VERSION, "/health says %r, the app is %r" % (j["version"], fl.APP_VERSION)
        return "/health reports the app's own version (%s)" % j["version"]
    check("01 /health reports the running version", t01)

    def t02():
        j = cl.get("/health").get_json()
        parts = tuple(int(x) for x in re.findall(r"\d+", j["version"])[:3])
        assert parts > (1, 3, 1), "version %r is not past the stale 1.3.1" % j["version"]
        assert '"version": "1.3.1"' not in src and '"version":"1.3.1"' not in src, \
            "the stale literal is still in the file"
        return "no stale literal anywhere; reported version is past 1.3.1"
    check("02 the stale version literal is gone", t02)

    def t03():
        m = re.search(r"def health\(\):\n(.*?)\n\n", src, re.S)
        body = m.group(1) if m else ""
        assert "APP_VERSION" in body, "the route does not read the constant: " + body.strip()[:120]
        assert not re.search(r'"\d+\.\d+\.\d+"', body), "a version literal is back in the route"
        return "the route reads the constant, never a literal"
    check("03 the version lives in one place", t03)

    def t04():
        r = cl.get("/health")
        j = r.get_json()
        assert r.status_code == 200, "a health check that needs a login is not a health check"
        assert set(j.keys()) == {"app", "version", "ok"}, "extra keys in /health: %r" % sorted(j.keys())
        for k, v in j.items():
            assert not isinstance(v, (int, float)) or isinstance(v, bool), "a figure in /health: %s=%r" % (k, v)
        return "app, version, ok - no record data, no counts"
    check("04 /health carries nothing but app, version and ok", t04)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("FitLog v1.7.1 -- /health tells the truth")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_sso.py -- one sign-in across GutLog, RxGuard and FitLog (HEALTH_SSO_V1).

All three REAL apps on loopback, each on its own address (127.0.0.1/.2/.3)
so their cookies stay apart the way three subdomains do, driven by one
browser-like client that follows redirects. Scratch databases, a scratch
key, test passwords. Nothing live is touched.

  python3 ops/test_sso.py [gutlog/app.py rxguard/app.py fitlog/app.py]
"""
import importlib.util
import os
import socket
import sys
import tempfile
import threading
import time

import requests

RESULTS = []
HTML = {"Accept": "text/html,application/xhtml+xml"}


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc)))


def free_port(host):
    s = socket.socket()
    s.bind((host, 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load(name, path):
    d = os.path.dirname(path)
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = sys.argv[1:4] if len(sys.argv) >= 4 else [
        os.path.join(root, "gutlog", "app.py"), os.path.join(root, "rxguard", "app.py"),
        os.path.join(root, "fitlog", "app.py")]
    paths = [os.path.abspath(p) for p in paths]
    w = tempfile.mkdtemp()
    keyf = os.path.join(w, "sso.key")
    hosts = {"gutlog": "127.0.0.1", "rxguard": "127.0.0.2", "fitlog": "127.0.0.3"}
    ports = dict((k, free_port(h)) for k, h in hosts.items())
    base = dict((k, "http://%s:%d" % (hosts[k], ports[k])) for k in hosts)
    os.environ.update(
        HEALTH_SSO_KEY_FILE=keyf,
        HEALTH_SSO_APPS=",".join("%s=%s" % (k, base[k]) for k in ("gutlog", "rxguard", "fitlog")),
        GUTLOG_DB=os.path.join(w, "g.db"), GUTLOG_UPLOADS=os.path.join(w, "up"),
        GUTLOG_INSECURE="1", GUTLOG_SECRET="test-secret-not-real-g", GUTLOG_LINKS="0",
        GUTLOG_ICONS=os.path.dirname(paths[0]), GUTLOG_FEED_TOKEN_FILE=os.path.join(w, "t"),
        FITLOG_DB=os.path.join(w, "f.db"), FITLOG_SECRET="test-secret-not-real-f",
        RXGUARD_GUTLOG_FEED="0")

    gm = load("g_sso", paths[0])
    rxm = load("r_sso", paths[1])
    rapp = rxm.create_app(db_path=os.path.join(w, "r.db"), secret="test-secret-not-real-r")
    fm = load("f_sso", paths[2])
    fm.init_db() if hasattr(fm, "init_db") else None
    from werkzeug.serving import make_server
    for k, appobj in (("gutlog", gm.app), ("rxguard", rapp), ("fitlog", fm.app)):
        srv = make_server(hosts[k], ports[k], appobj)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.4)

    def browser():
        s = requests.Session()
        s.trust_env = False
        s.headers.update(HTML)
        return s

    # passwords, set up once
    s0 = browser()
    s0.post(base["gutlog"] + "/setup", data={"pw": "testpassword1", "pw2": "testpassword1"})
    s0.post(base["rxguard"] + "/login", data={"password": "testpassword1"})
    s0.post(base["fitlog"] + "/setup", data={"pw": "testpassword1", "okey": "ownerkey1"})

    def signed_into(app):
        s = browser()
        if app == "gutlog":
            s.post(base[app] + "/login", data={"pw": "testpassword1"})
        elif app == "rxguard":
            s.post(base[app] + "/login", data={"password": "testpassword1"})
        else:
            s.post(base[app] + "/login", data={"pw": "testpassword1"})
        return s

    def on_login(r):
        return "/login" in r.url

    def t01():
        s = signed_into("gutlog")
        r = s.get(base["rxguard"] + "/")
        assert not os.path.exists(keyf)
        assert on_login(r) and r.url.startswith(base["rxguard"]), "no key, yet: " + r.url
        return "no key file: RxGuard asks for its own password, as before"
    check("01 without the key file nothing changes", t01)

    with open(keyf, "w") as fh:
        import secrets as _s
        fh.write(_s.token_hex(32))  # minted at run time, never a literal (NO_SECRETS)

    def t02():
        s = signed_into("gutlog")
        r = s.get(base["rxguard"] + "/astaken?days=30")
        assert r.status_code == 200 and not on_login(r), "ended at " + r.url
        assert r.url == base["rxguard"] + "/astaken?days=30", "lost the page asked for: " + r.url
        return "GutLog -> RxGuard /astaken?days=30 with no password"
    check("02 signed in to GutLog opens RxGuard, same page", t02)

    def t03():
        s = signed_into("gutlog")
        r = s.get(base["fitlog"] + "/meds")
        assert r.status_code == 200 and not on_login(r), "ended at " + r.url
        return "GutLog -> FitLog with no password"
    check("03 signed in to GutLog opens FitLog", t03)

    def t04():
        s = signed_into("rxguard")
        r = s.get(base["gutlog"] + "/")
        assert r.status_code == 200 and not on_login(r), "ended at " + r.url
        r2 = s.get(base["fitlog"] + "/meds")
        assert not on_login(r2), "FitLog: " + r2.url
        return "RxGuard -> GutLog and -> FitLog (round the ring both ways)"
    check("04 any one sign-in carries to the other two", t04)

    def t05():
        s = browser()
        r = s.get(base["rxguard"] + "/")
        assert on_login(r) and r.url.startswith(base["rxguard"]), "ended at " + r.url
        assert len(r.history) <= 4, "%d redirects" % len(r.history)
        return "nobody signed in: RxGuard's own login after %d redirects" % len(r.history)
    check("05 signed in nowhere lands on the app's own login", t05)

    def t06():
        s = signed_into("gutlog")
        s.headers.update(HTML)
        r = s.get(base["gutlog"] + "/sso/vouch", params={"to": "rxguard", "next": "/"},
                  allow_redirects=False)
        ticket_url = r.headers["Location"]
        assert "/sso/in?" in ticket_url, ticket_url
        a = browser().get(ticket_url)
        assert not on_login(a), "first use refused: " + a.url
        b = browser().get(ticket_url)
        assert on_login(b), "the same ticket signed in a second browser: " + b.url
        return "a ticket works once; replayed, it lands on the login page"
    check("06 a ticket cannot be used twice", t06)

    def t07():
        s = signed_into("gutlog")
        r = s.get(base["gutlog"] + "/sso/vouch", params={"to": "rxguard", "next": "/"},
                  allow_redirects=False)
        u = r.headers["Location"]
        t = u.split("t=")[1].split("&")[0]
        forged = u.replace(t, t[:-3] + ("AAA" if not t.endswith("AAA") else "BBB"))
        assert on_login(browser().get(forged)), "a tampered ticket was accepted"
        wrong = base["fitlog"] + "/sso/in?t=" + t + "&next=/"
        assert on_login(browser().get(wrong)), "a ticket for RxGuard signed in FitLog"
        return "tampered and wrong-app tickets refused"
    check("07 a tampered or wrong-app ticket is refused", t07)

    def t08():
        s = signed_into("gutlog")
        r = s.get(base["gutlog"] + "/sso/vouch", params={"to": "evil", "next": "/"},
                  allow_redirects=False)
        assert r.status_code == 404, "unknown target answered %d" % r.status_code
        r = s.get(base["gutlog"] + "/sso/vouch",
                  params={"to": "rxguard", "next": "//evil.example/x"}, allow_redirects=False)
        assert "next=%2F&" in r.headers["Location"] + "&" or r.headers["Location"].endswith("next=%2F"), \
            r.headers["Location"]
        r = s.get(r.headers["Location"], allow_redirects=False)
        assert r.headers["Location"] in ("/", base["rxguard"] + "/"), r.headers["Location"]
        return "unknown app 404; //evil next became /"
    check("08 it only ever sends the browser to the three apps", t08)

    def t09():
        s = signed_into("rxguard")
        s.post(base["gutlog"] + "/login", data={"pw": "testpassword1"})
        s.get(base["gutlog"] + "/logout")
        r = s.get(base["gutlog"] + "/")
        assert on_login(r) and r.url.startswith(base["gutlog"]), (
            "Lock did not hold -- signed straight back in from RxGuard: " + r.url)
        s.post(base["gutlog"] + "/login", data={"pw": "testpassword1"})
        s.get(base["gutlog"] + "/logout")
        s.post(base["gutlog"] + "/login", data={"pw": "testpassword1"})
        r2 = s.get(base["gutlog"] + "/")
        assert not on_login(r2), "password did not clear the hold"
        return "Lock keeps GutLog locked though RxGuard is open; the password reopens it"
    check("09 Lock stays locked until the app's own password", t09)

    def t10():
        s = browser()
        s.headers["Accept"] = "*/*"
        r = s.get(base["gutlog"] + "/api/now", allow_redirects=False)
        loc = r.headers.get("Location", "")
        assert r.status_code in (302, 401), r.status_code
        assert "sso" not in loc and not loc.startswith(base["rxguard"]), "an API call was bounced: " + loc
        return "API call: same login redirect as before, no ring"
    check("10 API calls are never sent round the ring", t10)

    def t11():
        import health_sso
        old = health_sso.TTL
        health_sso.TTL = -5
        try:
            t = health_sso.mint("gutlog", "rxguard")
        finally:
            health_sso.TTL = old
        r = browser().get(base["rxguard"] + "/sso/in", params={"t": t, "next": "/"})
        assert on_login(r), "an expired ticket was accepted"
        return "a ticket past its 60 seconds is refused"
    check("11 an expired ticket is refused", t11)

    ok = all(r[0] for r in RESULTS)
    print("=" * 72)
    print("One sign-in across GutLog, RxGuard and FitLog (HEALTH_SSO_V1)")
    print("=" * 72)
    for good, name, msg in RESULTS:
        print(("[PASS] " if good else "[FAIL] ") + name + ("  -- " + msg if msg else ""))
    print("-" * 72)
    print("%d/%d passed" % (sum(1 for r in RESULTS if r[0]), len(RESULTS)))
    print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

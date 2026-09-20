#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_sso.py -- one sign-in across GutLog, RxGuard and FitLog (HEALTH_SSO_V1).

His ask (17-Sep-2026): moving from GutLog into RxGuard or FitLog makes him
sign in again; one sign-in should carry across all three.

Each app gets, from the same health_sso.py beside its app.py:
  - login_required: a plain page load with no session goes round the ring
    to the other two apps before falling back to this app's own login page;
  - /sso/vouch  -- "is he signed in here?" -- mints a 60-second, one-use,
    single-app ticket, or passes the question on;
  - /sso/in     -- checks the ticket and signs this app in, exactly as its
    own password would;
  - logout     -- also sets a hold, so a locked app stays locked until its
    own password is typed; the password clears the hold.

Nothing else changes: passwords, owner keys, epochs, API behaviour, feed
tokens. With no /root/health-sso.key every app behaves exactly as before.

  python3 patch_sso.py --app gutlog  --file /root/gutlog/app.py  [--check]
  python3 patch_sso.py --app rxguard --file /root/rxguard/app.py [--check]
  python3 patch_sso.py --app fitlog  --file /root/fitlog/app.py  [--check]
  ... --reverse OUT   (negative control)

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring. Requires health_sso.py beside the target. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

MARKER = "HEALTH_SSO_V1"

GUT = [
    ("import",
     "from werkzeug.utils import secure_filename\n",
     "from werkzeug.utils import secure_filename\n"
     "import sys as _sso_sys  # HEALTH_SSO_V1\n"
     "_sso_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
     "import health_sso  # noqa: E402\n"),
    ("login_required bounce",
     '        if not session.get("ok") or session.get("ep") != auth_epoch():\n'
     '            return redirect(url_for("login"))\n',
     '        if not session.get("ok") or session.get("ep") != auth_epoch():\n'
     '            # HEALTH_SSO_V1 -- signed in to RxGuard or FitLog is signed in here\n'
     '            if health_sso.wants_bounce(request, session):\n'
     '                return redirect(health_sso.bounce_url("gutlog", health_sso.here(request)))\n'
     '            return redirect(url_for("login"))\n'),
    ("stamp clears hold",
     '    session["ep"] = auth_epoch()\n',
     '    session["ep"] = auth_epoch()\n'
     '    session.pop(health_sso.HOLD, None)  # HEALTH_SSO_V1\n'),
    ("logout holds",
     '    session.clear(); return redirect(url_for("login"))\n',
     '    session.clear()\n'
     '    session[health_sso.HOLD] = True  # HEALTH_SSO_V1 -- Lock stays locked\n'
     '    return redirect(url_for("login"))\n'),
    ("routes",
     '@app.route("/account", methods=["GET", "POST"])\n',
     '# HEALTH_SSO_V1 ---------------------------------------------------------\n'
     '@app.route("/sso/vouch")\n'
     'def sso_vouch():\n'
     '    ok = bool(setting("pw_hash")) and bool(session.get("ok")) and \\\n'
     '        session.get("ep") == auth_epoch()\n'
     '    u = health_sso.vouch_url("gutlog", ok, request.args.get("to"),\n'
     '                             request.args.get("next"), request.args.get("hops"))\n'
     '    return redirect(u) if u else ("Not found", 404)\n'
     '\n'
     '@app.route("/sso/in")\n'
     'def sso_in():\n'
     '    if not setting("pw_hash"):\n'
     '        return redirect(url_for("setup"))\n'
     '    ok, nxt = health_sso.accept("gutlog", request, db())\n'
     '    if not ok:\n'
     '        return redirect(url_for("login", sso="0"))\n'
     '    stamp_session()\n'
     '    return redirect(nxt)\n'
     '\n'
     '@app.route("/account", methods=["GET", "POST"])\n'),
]

RX = [
    ("import",
     "from werkzeug.security import check_password_hash, generate_password_hash\n",
     "from werkzeug.security import check_password_hash, generate_password_hash\n"
     "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # HEALTH_SSO_V1\n"
     "import health_sso  # noqa: E402\n"),
    ("login_required bounce",
     '            if not session.get("auth"):\n'
     '                return redirect(url_for("login"))\n'
     '            return fn(*a, **kw)\n',
     '            if not session.get("auth"):\n'
     '                # HEALTH_SSO_V1 -- signed in to GutLog or FitLog is signed in here\n'
     '                if health_sso.wants_bounce(request, session):\n'
     '                    return redirect(health_sso.bounce_url(\n'
     '                        "rxguard", health_sso.here(request)))\n'
     '                return redirect(url_for("login"))\n'
     '            return fn(*a, **kw)\n'),
    ("login clears hold",
     '                session["auth"] = True\n'
     '                session["epoch"] = setting("auth_epoch", "1")\n'
     '                return redirect(url_for("dashboard"))\n',
     '                session["auth"] = True\n'
     '                session["epoch"] = setting("auth_epoch", "1")\n'
     '                session.pop(health_sso.HOLD, None)  # HEALTH_SSO_V1\n'
     '                return redirect(url_for("dashboard"))\n'),
    ("logout holds + routes",
     '    @app.route("/logout")\n'
     '    def logout():\n'
     '        session.clear()\n'
     '        return redirect(url_for("login"))\n',
     '    @app.route("/logout")\n'
     '    def logout():\n'
     '        session.clear()\n'
     '        session[health_sso.HOLD] = True  # HEALTH_SSO_V1 -- stays locked\n'
     '        return redirect(url_for("login"))\n'
     '\n'
     '    # HEALTH_SSO_V1 -----------------------------------------------------\n'
     '    @app.route("/sso/vouch")\n'
     '    def sso_vouch():\n'
     '        ok = bool(session.get("auth")) and \\\n'
     '            session.get("epoch") == setting("auth_epoch", "1")\n'
     '        u = health_sso.vouch_url("rxguard", ok, request.args.get("to"),\n'
     '                                 request.args.get("next"), request.args.get("hops"))\n'
     '        return redirect(u) if u else ("Not found", 404)\n'
     '\n'
     '    @app.route("/sso/in")\n'
     '    def sso_in():\n'
     '        if not setting("password_hash"):\n'
     '            return redirect(url_for("login"))\n'
     '        ok, nxt = health_sso.accept("rxguard", request, get_db())\n'
     '        if not ok:\n'
     '            return redirect(url_for("login", sso="0"))\n'
     '        session["auth"] = True\n'
     '        session["epoch"] = setting("auth_epoch", "1")\n'
     '        session.pop(health_sso.HOLD, None)\n'
     '        return redirect(nxt)\n'),
]

FIT = [
    ("import",
     "from flask import Flask, request, redirect, session, g, url_for\n",
     "from flask import Flask, request, redirect, session, g, url_for\n"
     "import sys as _sso_sys  # HEALTH_SSO_V1\n"
     "_sso_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
     "import health_sso  # noqa: E402\n"),
    ("login_required bounce",
     '        if not session.get("auth"):\n'
     '            return redirect(url_for("login"))\n'
     '        return f(*a, **k)\n',
     '        if not session.get("auth"):\n'
     '            # HEALTH_SSO_V1 -- signed in to GutLog or RxGuard is signed in here\n'
     '            if health_sso.wants_bounce(request, session):\n'
     '                return redirect(health_sso.bounce_url("fitlog", health_sso.here(request)))\n'
     '            return redirect(url_for("login"))\n'
     '        return f(*a, **k)\n'),
    ("login clears hold",
     '        if sha(request.form.get("pw", "")) == setting("password_hash"):\n'
     '            session["auth"] = True\n'
     '            return redirect("/")\n',
     '        if sha(request.form.get("pw", "")) == setting("password_hash"):\n'
     '            session["auth"] = True\n'
     '            session.pop(health_sso.HOLD, None)  # HEALTH_SSO_V1\n'
     '            return redirect("/")\n'),
    ("logout holds + routes",
     '    session.clear(); return redirect(url_for("login"))\n',
     '    session.clear()\n'
     '    session[health_sso.HOLD] = True  # HEALTH_SSO_V1 -- stays locked\n'
     '    return redirect(url_for("login"))\n'
     '\n'
     '# HEALTH_SSO_V1 ---------------------------------------------------------\n'
     '@app.route("/sso/vouch")\n'
     'def sso_vouch():\n'
     '    # FitLog falls back to a session key derived from its DB path when\n'
     '    # FITLOG_SECRET is unset -- guessable, so such a FitLog vouches for no one.\n'
     '    ok = bool(os.environ.get("FITLOG_SECRET")) and \\\n'
     '        bool(setting("password_hash")) and bool(session.get("auth"))\n'
     '    u = health_sso.vouch_url("fitlog", ok, request.args.get("to"),\n'
     '                             request.args.get("next"), request.args.get("hops"))\n'
     '    return redirect(u) if u else ("Not found", 404)\n'
     '\n'
     '@app.route("/sso/in")\n'
     'def sso_in():\n'
     '    if not setting("password_hash"):\n'
     '        return redirect(url_for("setup"))\n'
     '    ok, nxt = health_sso.accept("fitlog", request, db())\n'
     '    if not ok:\n'
     '        return redirect(url_for("login", sso="0"))\n'
     '    session["auth"] = True\n'
     '    session.pop(health_sso.HOLD, None)\n'
     '    return redirect(nxt)\n'),
]

EDITS = {"gutlog": GUT, "rxguard": RX, "fitlog": FIT}


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
    ap.add_argument("--app", required=True, choices=sorted(EDITS))
    ap.add_argument("--file", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    edits = EDITS[a.app]
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(edits):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out:
            print("REVERSE FAILED: marker left")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed pre-SSO " + a.app + " -> " + a.reverse)
        return 0
    print("=" * 66)
    print("One sign-in across the three apps (HEALTH_SSO_V1) -> " + a.app)
    print("file : " + a.file)
    print("=" * 66)
    mod = os.path.join(os.path.dirname(os.path.abspath(a.file)), "health_sso.py")
    if not os.path.exists(mod):
        print("FATAL: health_sso.py must sit beside app.py first: " + mod)
        return 1
    if "HEALTH_SSO_V1" not in read(mod):
        print("FATAL: " + mod + " is not the HEALTH_SSO_V1 module")
        return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    bad = [label for label, old, new in edits if src.count(old) != 1]
    print("anchors: %d/%d matched" % (len(edits) - len(bad), len(edits)))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + ". Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for label, old, new in edits:
        out = out.replace(old, new, 1)
    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    write(tmpf, out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    bak = a.file + ".bak-sso-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    print("backup : " + bak)
    write(a.file, out)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, a.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(edits))
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

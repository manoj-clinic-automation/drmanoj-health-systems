#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_care.py -- caretaker access to a family member's copy.

FAMILY_EDITION_V1. Shared by the member's three apps (GutLog, RxGuard,
FitLog). Everything here lives in the MEMBER's own folder:

    <FAMILY_DIR>/care.db      access switch, caretaker change log, used nonces
    <FAMILY_DIR>/care.key     ticket key (a root copy sits in /root/family/care/)
    <FAMILY_DIR>/status.token bearer for the one status endpoint

HOW THE CARETAKER GETS IN
  He is signed in to his OWN GutLog. Its /family page mints a ticket --
  HMAC-SHA256 over {aud: member slug, app, who, exp, nonce} with this
  member's care.key -- and sends the browser to <member>/care/in. The member
  app checks the MAC, the audience, the app, the expiry (60 s), that the
  nonce is new, and that the member has caretaker access switched ON. Only
  then is a session stamped, marked care=<who>. His own credential is the
  only credential; the member's password is never used or seen. The key is
  per member, so a ticket for m1 means nothing to m2; rotating it
  (stamp_member.py --rotate-care) voids every ticket in flight.

WHAT A CARETAKER SESSION CAN DO
  Everything a member can, except the member's own credentials and the
  switch below (the app names those endpoints). Every write he makes --
  any POST/PUT/PATCH/DELETE that succeeds -- is logged in care.db with the
  IST time and "by caretaker (<who>)"; the member's GutLog shows the list.

THE SWITCH
  The member can switch caretaker access OFF (and on). Off means off: every
  caretaker request is refused and the session is cleared on the spot, new
  tickets are refused, and the status endpoint answers {"access": "off"}
  and nothing else. A missing or unreadable switch reads as OFF.

MOVING BETWEEN THE MEMBER'S APPS
  The member's own sign-in ring (health_sso, with the member's own key)
  must never turn a caretaker session into a member session. So when a
  caretaker session is asked to vouch, it hands out a CARETAKER ticket for
  the other app instead of a member one. The role survives the hop.

Python 3.9.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

MARKER = "FAMILY_EDITION_V1"
TTL = 60
CARE_SESSION_S = 12 * 3600
IST = timezone(timedelta(hours=5, minutes=30))
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
APPS = ("gut", "rx", "fit")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def now_ist():
    return datetime.now(IST)


class Care(object):
    """The caretaker layer for one member. `fam` is the member config:
    slug, dir, base (https://family.dr-manoj.in), prefixes {app: '/m1/rx'}."""

    def __init__(self, slug, fam_dir, base_url, prefixes):
        self.slug = slug
        self.dir = fam_dir
        self.base = base_url.rstrip("/")
        self.prefixes = dict(prefixes)
        self.db_path = os.path.join(fam_dir, "care.db")
        self.key_path = os.path.join(fam_dir, "care.key")
        self.token_path = os.path.join(fam_dir, "status.token")

    # ------------------------------------------------------------ storage
    def con(self):
        c = sqlite3.connect(self.db_path, timeout=5)
        c.row_factory = sqlite3.Row
        c.executescript(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE IF NOT EXISTS care_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " at TEXT NOT NULL, app TEXT NOT NULL, who TEXT NOT NULL, method TEXT,"
            " path TEXT, what TEXT, seen INTEGER DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS care_nonce (n TEXT PRIMARY KEY, exp INTEGER);")
        return c

    def access_on(self):
        """ON only when the switch says 'on'. Anything else -- no row, no
        file, an unreadable database -- is OFF."""
        try:
            c = self.con()
            try:
                r = c.execute("SELECT value FROM settings WHERE key='access'").fetchone()
            finally:
                c.close()
            return bool(r) and r["value"] == "on"
        except Exception:
            return False

    def set_access(self, on, by):
        c = self.con()
        try:
            c.execute("INSERT INTO settings(key,value) VALUES('access',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      ("on" if on else "off",))
            c.execute("INSERT INTO settings(key,value) VALUES('access_set',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (now_ist().strftime("%Y-%m-%d %H:%M") + " by " + by,))
            c.commit()
        finally:
            c.close()

    def log(self, app, who, method, path, what):
        c = self.con()
        try:
            c.execute("INSERT INTO care_log(at, app, who, method, path, what) VALUES(?,?,?,?,?,?)",
                      (now_ist().strftime("%Y-%m-%d %H:%M:%S"), app, who, method,
                       path[:300], (what or "")[:300]))
            c.commit()
        finally:
            c.close()

    def entries(self, limit=200):
        c = self.con()
        try:
            return [dict(r) for r in c.execute(
                "SELECT id, at, app, who, method, path, what, seen FROM care_log "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        finally:
            c.close()

    def mark_seen(self):
        c = self.con()
        try:
            c.execute("UPDATE care_log SET seen=1 WHERE seen=0")
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------ secrets
    def _read(self, path, minlen=32):
        try:
            with open(path, "rb") as fh:
                v = fh.read().strip()
            return v if len(v) >= minlen else None
        except OSError:
            return None

    def key(self):
        return self._read(self.key_path)

    def status_ok(self, auth_header):
        tok = self._read(self.token_path)
        got = (auth_header or "")
        got = got[7:].strip() if got.startswith("Bearer ") else ""
        return bool(tok) and bool(got) and hmac.compare_digest(tok, got.encode("ascii", "replace"))

    # ------------------------------------------------------------ tickets
    def mint(self, app, who, key=None):
        k = key or self.key()
        if not k or app not in APPS:
            return None
        body = _b64(json.dumps({"aud": self.slug, "app": app, "who": who,
                                "exp": int(time.time()) + TTL,
                                "n": secrets.token_hex(12)},
                               separators=(",", ":")).encode())
        return body + "." + _b64(hmac.new(k, body.encode("ascii"), hashlib.sha256).digest())

    def verify(self, tok, app):
        k = self.key()
        if not k or not tok or tok.count(".") != 1:
            return None
        body, sig = tok.split(".")
        want = _b64(hmac.new(k, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        try:
            p = json.loads(_unb64(body))
        except Exception:
            return None
        if p.get("aud") != self.slug or p.get("app") != app:
            return None
        if int(p.get("exp") or 0) < int(time.time()):
            return None
        who = str(p.get("who") or "")[:40].strip()
        if not who:
            return None
        c = self.con()
        try:
            c.execute("DELETE FROM care_nonce WHERE exp < ?", (int(time.time()) - 5,))
            try:
                c.execute("INSERT INTO care_nonce(n, exp) VALUES(?,?)", (p["n"], int(p["exp"])))
                c.commit()
            except sqlite3.IntegrityError:
                return None
        finally:
            c.close()
        p["who"] = who
        return p

    def app_url(self, app, path="/"):
        return self.base + self.prefixes[app] + path


def safe_next(n):
    n = (n or "").strip()
    if not n.startswith("/") or n.startswith("//") or "\\" in n or "\n" in n or "\r" in n:
        return "/"
    return n[:500]


def install(app, care, appname, stamp, what_for=None, blocked=(), health_sso=None,
            write_gets=()):
    """Wire the caretaker layer into one Flask app.

    stamp(session)      -- mark the session signed in the way this app does
    what_for(endpoint, path, method) -- a readable label for the change log
    blocked             -- endpoints a caretaker may never use (credentials,
                           the switch)
    write_gets          -- GET endpoints that change data (FitLog has two);
                           logged like a POST."""
    from flask import request, session, redirect, Response

    blocked = set(blocked)
    write_gets = set(write_gets)

    def refuse(msg, code=403):
        body = ("<!doctype html><meta charset=utf-8><meta name=viewport "
                "content='width=device-width,initial-scale=1'><title>Caretaker</title>"
                "<body style='font:17px/1.5 system-ui,sans-serif;padding:24px;max-width:30rem'>"
                "<p>" + msg + "</p></body>")
        return Response(body, status=code, mimetype="text/html")

    @app.route("/care/in")
    def care_in():
        if not care.access_on():
            session.clear()
            return refuse("Caretaker access is switched off for this person. "
                          "Only they can switch it back on.")
        p = care.verify(request.args.get("t"), appname)
        if not p:
            return refuse("This caretaker link has expired or was already used. "
                          "Open it again from your Family page.")
        session.clear()
        stamp(session)
        session["care"] = p["who"]
        # A caretaker session is never kept: browser-session only, 12 hours at most.
        session.permanent = False
        session["care_at"] = int(time.time())
        return redirect(safe_next(request.args.get("next")))

    @app.before_request
    def _care_gate():
        who = session.get("care")
        if not who:
            return None
        if not care.access_on():
            session.clear()
            return refuse("Caretaker access has been switched off. You have been signed out.")
        if int(time.time()) - int(session.get("care_at") or 0) > CARE_SESSION_S:
            session.clear()
            return refuse("The caretaker session has ended (12 hours). Open it again from your Family page.")
        ep = request.endpoint or ""
        if ep in blocked:
            return refuse("This is for the person themselves, not the caretaker.")
        if ep == "sso_vouch" and health_sso is not None:
            # A caretaker session vouches only as a caretaker.
            to = request.args.get("to")
            dest = {"gutlog": "gut", "rxguard": "rx", "fitlog": "fit"}.get(to)
            if not dest or dest == appname:
                return ("Not found", 404)
            tok = care.mint(dest, who)
            if not tok:
                return ("Not found", 404)
            return redirect(care.app_url(dest, "/care/in?" + urlencode(
                {"t": tok, "next": safe_next(request.args.get("next"))})))
        return None

    @app.after_request
    def _care_log(resp):
        try:
            who = session.get("care")
            writes = request.method in WRITE_METHODS or (request.endpoint or "") in write_gets
            if who and writes and resp.status_code < 400 \
                    and (request.endpoint or "") not in ("care_in",):
                label = what_for(request.endpoint or "", request.path, request.method) \
                    if what_for else ""
                care.log(appname, who, request.method, request.path, label)
        except Exception:
            pass
        return resp

    return app

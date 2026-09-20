#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_sso.py -- one sign-in across GutLog, RxGuard and FitLog.

HEALTH_SSO_V1. The same file sits beside each app's app.py (gutlog/, rxguard/,
fitlog/); tools/CHECK_FOLDER_PARITY-style drift is guarded by ops/patch_sso.py,
which refuses to patch if the three copies differ.

HOW IT WORKS
  A page in app X needs a sign-in and X has no session. Instead of showing
  X's login page, X sends the browser round the ring
      gutlog -> rxguard -> fitlog -> gutlog
  asking each other app in turn, "is he signed in with you?" (/sso/vouch).
  The first app that has a session mints a ticket for X and sends the
  browser back to X's /sso/in, which checks the ticket and signs X in. If
  neither other app has a session, the browser lands on X's own login page,
  exactly as before.

THE TICKET
  HMAC-SHA256 over {iss, aud, exp, nonce} with a key only the server holds
  (/root/health-sso.key, mode 600). Bound to ONE receiving app (aud), valid
  60 seconds, usable once (the nonce is recorded in the receiving app's own
  database). It rides in a URL for one redirect, over HTTPS.

WHAT IT NEVER DOES
  - send the browser anywhere but the three configured app origins;
  - follow a `next` that is not a plain path on the receiving app;
  - bounce anything but a plain page load (GET, HTML) -- API calls keep
    getting the login redirect they always got;
  - sign an app back in after it was locked there: logging out sets a hold
    in that app's session, and only its own password clears it.

No key file = feature off; every app behaves exactly as before. Python 3.9.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

MARKER = "HEALTH_SSO_V1"
RING = ["gutlog", "rxguard", "fitlog"]
TTL = 60
MAX_HOPS = 2
DEFAULT_APPS = ("gutlog=https://health.dr-manoj.in,rxguard=https://rx.dr-manoj.in,"
                "fitlog=https://fit.dr-manoj.in")
HOLD = "sso_hold"


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def key():
    """The shared key, or None when SSO is not set up on this server."""
    path = os.environ.get("HEALTH_SSO_KEY_FILE", "/root/health-sso.key")
    try:
        with open(path, "rb") as fh:
            k = fh.read().strip()
    except OSError:
        return None
    return k if len(k) >= 32 else None


def enabled():
    return key() is not None


def apps():
    out = {}
    for part in os.environ.get("HEALTH_SSO_APPS", DEFAULT_APPS).split(","):
        if "=" in part:
            name, url = part.split("=", 1)
            out[name.strip()] = url.strip().rstrip("/")
    return out


def successor(me):
    i = RING.index(me)
    return RING[(i + 1) % len(RING)]


def safe_next(nxt):
    """A path on the receiving app, never another host."""
    n = (nxt or "").strip()
    if not n.startswith("/") or n.startswith("//") or "\\" in n or "\n" in n or "\r" in n:
        return "/"
    if n.endswith("?"):
        n = n[:-1]
    return n[:500]


def mint(me, aud):
    k = key()
    if k is None:
        return None
    body = _b64(json.dumps({"iss": me, "aud": aud, "exp": int(time.time()) + TTL,
                            "n": secrets.token_hex(12)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(k, body.encode("ascii"), hashlib.sha256).digest())
    return body + "." + sig


def verify(tok, me):
    """The payload if the ticket is genuine, for this app, and in date."""
    k = key()
    if k is None or not tok or tok.count(".") != 1:
        return None
    body, sig = tok.split(".")
    want = _b64(hmac.new(k, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        return None
    try:
        p = json.loads(_unb64(body))
    except Exception:
        return None
    if p.get("aud") != me or p.get("iss") not in RING or p.get("iss") == me:
        return None
    if int(p.get("exp") or 0) < int(time.time()):
        return None
    return p


def use_nonce(con, nonce, exp):
    """True the first time a nonce is seen by this app, False after."""
    con.execute("CREATE TABLE IF NOT EXISTS sso_used (n TEXT PRIMARY KEY, exp INTEGER)")
    con.execute("DELETE FROM sso_used WHERE exp < ?", (int(time.time()) - 5,))
    try:
        con.execute("INSERT INTO sso_used (n, exp) VALUES (?, ?)", (nonce, int(exp)))
        con.commit()
        return True
    except Exception:
        return False


def wants_bounce(request, session):
    """Only a plain page load, only when set up, never after a lock."""
    if not enabled() or request.method != "GET" or session.get(HOLD):
        return False
    if request.args.get("sso") == "0":
        return False
    return "text/html" in (request.headers.get("Accept") or "")


def here(request):
    q = request.query_string.decode("utf-8", "replace")
    return request.path + ("?" + q if q else "")


def bounce_url(me, path):
    a = apps()
    first = successor(me)
    return a[first] + "/sso/vouch?" + urlencode({"to": me, "next": safe_next(path), "hops": 1})


def vouch_url(me, signed_in, to, nxt, hops):
    """Where /sso/vouch on app `me` sends the browser next. None = refuse."""
    a = apps()
    if to not in a or to not in RING or to == me or not enabled():
        return None
    nxt = safe_next(nxt)
    if signed_in:
        return a[to] + "/sso/in?" + urlencode({"t": mint(me, to), "next": nxt})
    try:
        hops = int(hops)
    except (TypeError, ValueError):
        hops = MAX_HOPS
    onward = successor(me)
    if onward == to or hops >= MAX_HOPS:
        return a[to] + "/login?" + urlencode({"sso": "0"})
    return a[onward] + "/sso/vouch?" + urlencode({"to": to, "next": nxt, "hops": hops + 1})


def accept(me, request, con):
    """(ok, next path) for /sso/in on app `me`."""
    p = verify(request.args.get("t"), me)
    if not p or not use_nonce(con, p["n"], p["exp"]):
        return False, "/"
    return True, safe_next(request.args.get("next"))

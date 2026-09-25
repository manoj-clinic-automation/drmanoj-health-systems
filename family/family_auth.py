#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_auth.py -- how a family member signs in to their own copy.

FAMILY_EDITION_V1. Family copies only; the owner's apps keep their own
password pages untouched. Installed by each member entry (GutLog, RxGuard,
FitLog), all three sharing the member's care.db, so a lockout in one is a
lockout in all.

  PIN      Six digits, checked against one scrypt hash in care.db. The apps' own
           password hashes are random and unused -- FitLog's is an unsalted
           SHA-256, which a six-digit PIN must never sit behind.
  LOCKOUT  5 wrong PINs in a row lock sign-in for 15 minutes; each further lock
           doubles it (30, 60 ... at most 24 hours) until a PIN or Face ID
           sign-in succeeds. While locked, even the right PIN is refused.
           Every attempt -- wrong, locked out, right, Face ID -- is logged in
           care.db with the IST time, app and address, and shown to the
           member (and caretaker) on the Caretaker page.
  PASSKEYS Face ID / Touch ID (WebAuthn, family_webauthn.py). Offered once
           after a PIN sign-in on a device that has it; used from GutLog's
           sign-in page. A passkey sign-in works during a PIN lockout -- it
           is a different, stronger credential -- and ends the lockout.
  SESSION  12 months on the member's own device (a sliding cookie), until they
           sign out. "Sign out all devices" and a PIN reset end every session
           at once (a session epoch in care.db). A caretaker session is never
           kept: browser-session only, and 12 hours at most.

Python 3.9.
"""
import hmac
import json
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import urlparse

import family_care
import family_webauthn as W

MARKER = "FAMILY_EDITION_V1"
LOCK_AFTER = 5
LOCK_BASE_MIN = 15
LOCK_MAX_MIN = 24 * 60
SESSION_DAYS = 365
CHALLENGE_S = 180


def weak_pin(pin):
    """All one digit, or a straight run up or down."""
    return len(set(pin)) == 1 or pin in "01234567890" or pin in "09876543210"


class Auth(object):
    def __init__(self, care):
        self.care = care

    def con(self):
        c = self.care.con()
        c.executescript(
            "CREATE TABLE IF NOT EXISTS auth_kv (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE IF NOT EXISTS auth_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, app TEXT,"
            " ip TEXT, result TEXT);"
            "CREATE TABLE IF NOT EXISTS passkeys (id INTEGER PRIMARY KEY AUTOINCREMENT, cred_id TEXT UNIQUE,"
            " key TEXT NOT NULL, count INTEGER DEFAULT 0, label TEXT DEFAULT '', created TEXT, last_used TEXT);"
            "CREATE TABLE IF NOT EXISTS auth_challenge (c TEXT PRIMARY KEY, kind TEXT, exp INTEGER);")
        return c

    # -- key/value -----------------------------------------------------------
    def kv(self, c, key, default=None):
        r = c.execute("SELECT value FROM auth_kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_kv(self, c, key, value):
        c.execute("INSERT INTO auth_kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))

    # -- PIN -------------------------------------------------------------------
    def set_pin(self, pin, reset_lock=True):
        from werkzeug.security import generate_password_hash
        if not (len(pin) == 6 and pin.isdigit()):
            raise ValueError("a PIN is 6 digits")
        c = self.con()
        try:
            self.set_kv(c, "pin_hash", generate_password_hash(pin))
            if reset_lock:
                for k in ("fails", "lock_level", "lock_until"):
                    self.set_kv(c, k, 0)
            c.commit()
        finally:
            c.close()

    def attempt(self, pin, app, ip):
        """('ok', 0) | ('wrong', tries left before a pause) | ('locked', epoch the pause ends)."""
        from werkzeug.security import check_password_hash
        now = int(time.time())
        c = self.con()
        try:
            c.execute("BEGIN IMMEDIATE")
            until = int(self.kv(c, "lock_until", 0) or 0)
            if now < until:
                self._log(c, app, ip, "refused: locked")
                c.commit()
                return "locked", until
            h = self.kv(c, "pin_hash")
            good = bool(h) and len(pin or "") == 6 and pin.isdigit() and check_password_hash(h, pin)
            if good:
                for k in ("fails", "lock_level", "lock_until"):
                    self.set_kv(c, k, 0)
                self._log(c, app, ip, "signed in (PIN)")
                c.commit()
                return "ok", 0
            fails = int(self.kv(c, "fails", 0) or 0) + 1
            self._log(c, app, ip, "wrong PIN (%d of %d)" % (fails, LOCK_AFTER))
            if fails >= LOCK_AFTER:
                level = int(self.kv(c, "lock_level", 0) or 0) + 1
                mins = min(LOCK_BASE_MIN * (2 ** (level - 1)), LOCK_MAX_MIN)
                self.set_kv(c, "lock_level", level)
                self.set_kv(c, "lock_until", now + mins * 60)
                self.set_kv(c, "fails", 0)
                self._log(c, app, ip, "locked for %d minutes" % mins)
                c.commit()
                return "locked", now + mins * 60
            self.set_kv(c, "fails", fails)
            c.commit()
            return "wrong", LOCK_AFTER - fails
        finally:
            c.close()

    def cleared(self, app, ip, how):
        c = self.con()
        try:
            for k in ("fails", "lock_level", "lock_until"):
                self.set_kv(c, k, 0)
            self._log(c, app, ip, how)
            c.commit()
        finally:
            c.close()

    def _log(self, c, app, ip, result):
        c.execute("INSERT INTO auth_log(at, app, ip, result) VALUES(?,?,?,?)",
                  (family_care.now_ist().strftime("%Y-%m-%d %H:%M:%S"), app, (ip or "")[:60], result))

    def log(self, app, ip, result):
        c = self.con()
        try:
            self._log(c, app, ip, result)
            c.commit()
        finally:
            c.close()

    def entries(self, limit=30):
        c = self.con()
        try:
            return [dict(r) for r in c.execute("SELECT at, app, ip, result FROM auth_log ORDER BY id DESC LIMIT ?",
                                               (limit,)).fetchall()]
        finally:
            c.close()

    # -- session epoch ---------------------------------------------------------
    def epoch(self):
        c = self.con()
        try:
            e = self.kv(c, "session_epoch")
            if not e:
                e = secrets.token_hex(12)
                self.set_kv(c, "session_epoch", e)
                c.commit()
            return e
        finally:
            c.close()

    def rotate_epoch(self):
        c = self.con()
        try:
            self.set_kv(c, "session_epoch", secrets.token_hex(12))
            c.commit()
        finally:
            c.close()

    # -- challenges and passkeys ---------------------------------------------
    def challenge(self, kind):
        ch = W.b64u(secrets.token_bytes(32))
        c = self.con()
        try:
            c.execute("DELETE FROM auth_challenge WHERE exp < ?", (int(time.time()),))
            c.execute("INSERT INTO auth_challenge(c, kind, exp) VALUES(?,?,?)",
                      (ch, kind, int(time.time()) + CHALLENGE_S))
            c.commit()
        finally:
            c.close()
        return ch

    def take_challenge(self, ch, kind):
        """True once, for a live challenge of this kind; never again."""
        c = self.con()
        try:
            cur = c.execute("DELETE FROM auth_challenge WHERE c=? AND kind=? AND exp>=?",
                            (ch or "", kind, int(time.time())))
            c.commit()
            return cur.rowcount == 1
        finally:
            c.close()

    def user_handle(self):
        c = self.con()
        try:
            h = self.kv(c, "user_handle")
            if not h:
                h = W.b64u(secrets.token_bytes(16))
                self.set_kv(c, "user_handle", h)
                c.commit()
            return h
        finally:
            c.close()

    def passkeys(self):
        c = self.con()
        try:
            return [dict(r) for r in c.execute("SELECT id, cred_id, key, count, label, created, last_used "
                                               "FROM passkeys ORDER BY id").fetchall()]
        finally:
            c.close()

    def add_passkey(self, cred_id, key, count, label):
        c = self.con()
        try:
            c.execute("INSERT INTO passkeys(cred_id, key, count, label, created) VALUES(?,?,?,?,?)",
                      (cred_id, json.dumps(key), count, label[:60],
                       family_care.now_ist().strftime("%Y-%m-%d %H:%M")))
            c.commit()
        finally:
            c.close()

    def touch_passkey(self, cred_id, count):
        c = self.con()
        try:
            c.execute("UPDATE passkeys SET count=?, last_used=? WHERE cred_id=?",
                      (count, family_care.now_ist().strftime("%Y-%m-%d %H:%M"), cred_id))
            c.commit()
        finally:
            c.close()

    def remove_passkey(self, pid):
        c = self.con()
        try:
            c.execute("DELETE FROM passkeys WHERE id=?", (pid,))
            c.commit()
        finally:
            c.close()


# ------------------------------------------------------------------ pages
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%(title)s</title>%(head)s
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--bad:#a4262c}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:22rem;margin:0 auto;padding:28px 18px}h1{font-size:22px;margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0}
input.pin{width:100%%;font-size:32px;letter-spacing:12px;text-align:center;padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--fg)}
button{width:100%%;font:inherit;font-weight:600;padding:14px;border-radius:12px;border:0;background:var(--acc);color:#fff;margin-top:12px}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc)}
.pinrow{display:flex;gap:8px;align-items:stretch}.pinrow input.pin{flex:1;min-width:0}
button.eye{width:auto;margin:0;padding:0 14px;font-size:15px;background:transparent;color:var(--acc);border:1px solid var(--line)}
.err{color:var(--bad);font-weight:600}.mut{color:var(--mut);font-size:15px}a{color:var(--acc)}
</style></head><body><main>%(body)s</main>
<script>
function b64u(b){return btoa(String.fromCharCode.apply(null,new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');}
function unb64u(s){s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%%4)s+='=';const r=atob(s),a=new Uint8Array(r.length);for(let i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a.buffer;}
async function jpost(u,b){const r=await fetch(u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});const j=await r.json().catch(()=>({}));j._status=r.status;return j;}
%(script)s
</script></body></html>"""

EYE_JS = """
const eye=document.getElementById('eye'),pin=document.getElementById('pin');
if(eye&&pin)eye.onclick=()=>{const show=pin.type==='password';pin.type=show?'text':'password';
  eye.textContent=show?'Hide':'Show';eye.setAttribute('aria-pressed',show?'true':'false');pin.focus();};
"""

# Face ID is offered on this page only once it has been set up ON THIS PHONE
# (the offer page leaves fam_pk_<slug>='1' behind); if the server no longer
# has one, the flag is dropped and the button goes away.
LOGIN_JS = EYE_JS + """
const K='fam_pk_%(slug)s';
const pk=document.getElementById('pk');
let here=false;try{here=localStorage.getItem(K)==='1';}catch(e){}
if(pk&&here&&window.PublicKeyCredential){pk.style.display='';}
if(pk)pk.onclick=async()=>{
  const m=document.getElementById('pkmsg');m.textContent='';
  try{
    const o=await jpost('/passkey/login/begin');
    if(!o.ok){if(o._status===404){try{localStorage.removeItem(K);}catch(e){}pk.style.display='none';}
      m.textContent=o.err||'Face ID is not set up yet \\u2014 use your PIN.';return;}
    const c=await navigator.credentials.get({publicKey:{challenge:unb64u(o.challenge),rpId:o.rpId,
      allowCredentials:o.allow.map(id=>({type:'public-key',id:unb64u(id)})),userVerification:'required',timeout:60000}});
    const r=await jpost('/passkey/login/finish',{challenge:o.challenge,id:c.id,response:{
      clientDataJSON:b64u(c.response.clientDataJSON),authenticatorData:b64u(c.response.authenticatorData),
      signature:b64u(c.response.signature)}});
    if(r.ok){location.href=r.next;}else{m.textContent=r.err||'That did not work \\u2014 use your PIN.';}
  }catch(e){m.textContent='Cancelled \\u2014 use your PIN, or try again.';}
};
"""

OFFER_JS = """
const K='fam_pk_%(slug)s';
async function go(){location.href=%(next)s;}
(async()=>{
  let ok=false;
  try{ok=!!(window.PublicKeyCredential&&await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable());}catch(e){}
  let done=false;try{done=!!localStorage.getItem(K);}catch(e){}
  if(!ok||done){go();return;}
  document.getElementById('offer').style.display='';
})();
document.getElementById('no').onclick=()=>{try{localStorage.setItem(K,'later');}catch(e){}go();};
document.getElementById('yes').onclick=async()=>{
  const m=document.getElementById('msg');m.textContent='';
  try{
    const o=await jpost('/passkey/register/begin');
    if(!o.ok){m.textContent=o.err||'Could not start.';return;}
    const c=await navigator.credentials.create({publicKey:{challenge:unb64u(o.challenge),rp:o.rp,
      user:{id:unb64u(o.user.id),name:o.user.name,displayName:o.user.displayName},
      pubKeyCredParams:[{type:'public-key',alg:-7},{type:'public-key',alg:-257}],
      authenticatorSelection:{authenticatorAttachment:'platform',residentKey:'preferred',userVerification:'required'},
      attestation:'none',excludeCredentials:o.exclude.map(id=>({type:'public-key',id:unb64u(id)})),timeout:60000}});
    const r=await jpost('/passkey/register/finish',{challenge:o.challenge,id:c.id,response:{
      clientDataJSON:b64u(c.response.clientDataJSON),attestationObject:b64u(c.response.attestationObject)}});
    if(r.ok){try{localStorage.setItem(K,'1');}catch(e){}go();}else{m.textContent=r.err||'That did not work.';}
  }catch(e){m.textContent='Cancelled. You can set it up later from the Caretaker page.';}
};
"""


TITLES = {"gut": "%s's health diary", "rx": "%s's medicines", "fit": "%s's fitness"}


def title_for(appname, name):
    """Whose page this is, in words: a member must never type their PIN on
    someone else's page (2026-09-25: m1's PIN was tried three times on m2's
    unlabelled sign-in page)."""
    return TITLES.get(appname, "%s's health diary") % name


def until_text(until, now=None):
    """'18:40 IST', or 'tomorrow 09:10 IST' when the pause runs past midnight IST."""
    from datetime import datetime, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    u = datetime.fromtimestamp(until, ist)
    n = datetime.fromtimestamp(now if now is not None else time.time(), ist)
    hm = u.strftime("%H:%M")
    if u.date() == n.date():
        return hm + " IST"
    if (u.date() - n.date()).days == 1:
        return "tomorrow " + hm + " IST"
    return u.strftime("%d %b ") + hm + " IST"


def wrong_text(left):
    return "Wrong PIN — %d %s left" % (left, "try" if left == 1 else "tries")


def paused_text(until, caretaker, now=None):
    return "Paused until %s — call %s" % (until_text(until, now), caretaker)


def install(app, appname, M, care, stamp, signed_in, passkeys=False, title=None):
    """Replace this family app's sign-in with PIN + lockout (+ passkeys on
    GutLog), and keep a member signed in for 12 months."""
    from flask import request, session, redirect, jsonify, Response
    import html as _html

    auth = Auth(care)
    title = title or title_for(appname, M.name)
    caretaker = getattr(M, "caretaker", "") or "your caretaker"
    gut_url = M.url("gut")
    pwa_head = ("<link rel='manifest' href='%(g)s/manifest.webmanifest'>"
                "<link rel='icon' href='%(g)s/icon-192.png'>"
                "<link rel='apple-touch-icon' href='%(g)s/icon-192.png'>"
                "<meta name='apple-mobile-web-app-capable' content='yes'>"
                "<meta name='mobile-web-app-capable' content='yes'>"
                "<meta name='apple-mobile-web-app-title' content='%(n)s'>"
                "<meta name='theme-color' content='#1f6f5c'>"
                % {"g": _html.escape(gut_url, quote=True), "n": _html.escape(M.name[:30], quote=True)})
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=SESSION_DAYS)
    base = urlparse(M.base)
    rp_id = base.hostname
    origin = "%s://%s" % (base.scheme, base.netloc)

    def ip():
        return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()

    def here(path):
        return (request.script_root or "") + path

    def start(how):
        session.clear()
        stamp(session)
        session.permanent = True
        session["fam_ep"] = auth.epoch()

    def page(t, body, script="", code=200, head=""):
        return Response(PAGE % {"title": _html.escape(t), "head": head, "body": body, "script": script},
                        status=code, mimetype="text/html")

    def login_page(err=""):
        pk = ("<button type='button' class='ghost' id='pk' style='display:none'>Sign in with Face ID / Touch ID"
              "</button><p class='err' id='pkmsg'></p>") if passkeys else ""
        body = ("<h1>%s</h1><p class='mut'>Enter your 6-digit PIN.</p>"
                "<form method='post' action='/login' class='card' autocomplete='off'>"
                "<div class='pinrow'><input class='pin' id='pin' name='pin' type='password' inputmode='numeric' "
                "pattern='[0-9]*' maxlength='6' autocomplete='current-password' autofocus aria-label='PIN'>"
                "<button type='button' class='eye' id='eye' aria-label='Show or hide the PIN' "
                "aria-pressed='false'>Show</button></div>"
                "%s<button type='submit' id='go'>Sign in</button></form>%s"
                "<p class='mut'>Not %s? Ask %s for your own link.</p>"
                % (_html.escape(title), ("<p class='err' id='err'>%s</p>" % _html.escape(err)) if err else "", pk,
                   _html.escape(M.name), _html.escape(caretaker)))
        js = (LOGIN_JS % {"slug": M.slug}) if passkeys else EYE_JS
        return page(title, body, js, 200 if not err else 401, head=pwa_head)

    def login():
        if request.method == "GET":
            return login_page()
        pin = (request.form.get("pin") or "").strip()
        res, info = auth.attempt(pin, appname, ip())
        if res == "locked":
            return login_page(paused_text(info, caretaker))
        if res != "ok":
            return login_page(wrong_text(info))
        start("pin")
        if passkeys:
            return redirect("/passkey/offer?next=" + "%2F")
        return redirect("/")

    app.view_functions["login"] = login

    @app.before_request
    def _fam_session():
        if session.get("care"):
            return None
        if signed_in(session) and session.get("fam_ep") != auth.epoch():
            session.clear()
        return None

    @app.after_request
    def _fam_sso(resp):
        # Signed in through the member's own sign-in ring: kept like a PIN sign-in.
        try:
            if request.endpoint == "sso_in" and signed_in(session) and not session.get("care"):
                session.permanent = True
                session["fam_ep"] = auth.epoch()
        except Exception:
            pass
        return resp

    @app.route("/signout-all", methods=["POST"])
    def signout_all():
        if not signed_in(session) or session.get("care"):
            return redirect("/login")
        auth.rotate_epoch()
        auth.log(appname, ip(), "signed out on all devices")
        session.clear()
        return redirect("/login")

    @app.route("/pin/change", methods=["POST"])
    def pin_change():
        if not signed_in(session) or session.get("care"):
            return jsonify(ok=False, err="Sign in first."), 401
        js = request.get_json(silent=True)
        d = js if js is not None else request.form

        def out(ok, msg, code):
            if js is not None:
                return (jsonify(ok=True), 200) if ok else (jsonify(ok=False, err=msg), code)
            return redirect("/care?pin=" + ("changed" if ok else "refused"))
        old, new = (d.get("old") or "").strip(), (d.get("new") or "").strip()
        res, info = auth.attempt(old, appname, ip())
        if res != "ok":
            return out(False, "The current PIN is not right (%d %s left)." % (info, "try" if info == 1 else "tries")
                       if res == "wrong" else paused_text(info, caretaker), 400)
        if not (len(new) == 6 and new.isdigit()) or weak_pin(new):
            return out(False, "Choose 6 digits that are not all the same or in a straight run.", 400)
        auth.set_pin(new)
        auth.log(appname, ip(), "PIN changed")
        return out(True, "", 200)

    if not passkeys:
        return auth

    @app.route("/passkey/offer")
    def passkey_offer():
        if not signed_in(session):
            return redirect("/login")
        nxt = here(family_care.safe_next(request.args.get("next")))
        body = ("<div id='offer' style='display:none'><h1>Sign in with Face ID next time?</h1>"
                "<p class='mut'>Instead of the PIN, this phone can use Face ID or Touch ID. The PIN keeps working.</p>"
                "<div class='card'><button id='yes'>Use Face ID / Touch ID</button>"
                "<button class='ghost' id='no'>Not now</button><p class='err' id='msg'></p></div></div>")
        return page("Face ID", body, OFFER_JS % {"slug": M.slug, "next": json.dumps(nxt)})

    @app.route("/passkey/register/begin", methods=["POST"])
    def passkey_register_begin():
        if not signed_in(session) or session.get("care"):
            return jsonify(ok=False, err="Only the person themselves can set up Face ID."), 403
        return jsonify(ok=True, challenge=auth.challenge("reg"), rp={"id": rp_id, "name": "Family health diary"},
                       user={"id": auth.user_handle(), "name": M.slug, "displayName": M.name},
                       exclude=[p["cred_id"] for p in auth.passkeys()])

    @app.route("/passkey/register/finish", methods=["POST"])
    def passkey_register_finish():
        if not signed_in(session) or session.get("care"):
            return jsonify(ok=False, err="Only the person themselves can set up Face ID."), 403
        d = request.get_json(silent=True) or {}
        if not auth.take_challenge(d.get("challenge"), "reg"):
            return jsonify(ok=False, err="That took too long -- try again."), 400
        try:
            r = W.verify_registration(d, d.get("challenge"), origin, rp_id)
        except W.WebAuthnError as e:
            auth.log(appname, ip(), "Face ID set-up refused: %s" % e)
            return jsonify(ok=False, err="Face ID could not be set up (%s)." % e), 400
        ua = request.headers.get("User-Agent") or ""
        label = "iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Mac" if "Mac" in ua else "this device"
        auth.add_passkey(r["cred_id"], r["key"], r["count"], label)
        auth.log(appname, ip(), "Face ID / Touch ID set up (%s)" % label)
        return jsonify(ok=True)

    @app.route("/passkey/login/begin", methods=["POST"])
    def passkey_login_begin():
        pks = auth.passkeys()
        if not pks:
            return jsonify(ok=False, err="Face ID is not set up yet -- use your PIN."), 404
        return jsonify(ok=True, challenge=auth.challenge("login"), rpId=rp_id, allow=[p["cred_id"] for p in pks])

    @app.route("/passkey/login/finish", methods=["POST"])
    def passkey_login_finish():
        d = request.get_json(silent=True) or {}
        if not auth.take_challenge(d.get("challenge"), "login"):
            return jsonify(ok=False, err="That took too long -- try again."), 400
        pk = next((p for p in auth.passkeys() if hmac.compare_digest(p["cred_id"], str(d.get("id") or ""))), None)
        if not pk:
            auth.log(appname, ip(), "Face ID refused: unknown key")
            return jsonify(ok=False, err="This Face ID is not set up for this diary."), 400
        try:
            count = W.verify_assertion(d, d.get("challenge"), origin, rp_id, json.loads(pk["key"]), pk["count"])
        except W.WebAuthnError as e:
            auth.log(appname, ip(), "Face ID refused: %s" % e)
            return jsonify(ok=False, err="Face ID sign-in failed."), 400
        auth.touch_passkey(pk["cred_id"], count)
        auth.cleared(appname, ip(), "signed in (Face ID / Touch ID)")
        start("passkey")
        return jsonify(ok=True, next=here("/"))

    @app.route("/passkey/remove/<int:pid>", methods=["POST"])
    def passkey_remove(pid):
        if not signed_in(session) or session.get("care"):
            return redirect("/login")
        auth.remove_passkey(pid)
        auth.log(appname, ip(), "Face ID / Touch ID removed")
        return redirect("/care")

    return auth


CARETAKER_BLOCKED = ("signout_all", "pin_change", "passkey_register_begin", "passkey_register_finish",
                     "passkey_remove")

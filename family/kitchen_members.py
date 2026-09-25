#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kitchen_members.py -- recipes-only members of the Family Kitchen.

FAMILY_EDITION_V1 (Kitchen 1.1.0). A kitchen member (slug k1, k2 ...) is an
account INSIDE the Kitchen service: no Linux user, no processes of their
own, no database but the Kitchen's. They reach the recipe book at
https://family.dr-manoj.in/kitchen/k1/ and nothing else -- no GutLog,
RxGuard or FitLog exists for them, and nothing about their health is
recorded anywhere (the only things stored: a display name, a PIN hash and
sign-in log in members/k1/auth.db, plain food preferences).

SIGN-IN  The family rules exactly, from family_auth.py: a 6-digit PIN with
         the same lockout, the same IST attempt log, Face ID / Touch ID
         offered after the first PIN sign-in, 12-month sessions, "sign out
         on all devices". One auth.db per member under
         <KITCHEN_DIR>/members/<slug>/, so a lockout is that member's only.
         The session cookie is scoped to /kitchen/<slug>/ and satisfies
         nothing else: the bearer routes read the Authorization header
         only, and a member app has its own secret.

PAGES    /<slug>/            the recipe book (browse, search, groups, top,
                             new this week, favourites, by person, mine)
         /<slug>/j/...       JSON for the page, session-gated
         /<slug>/login ...   sign-in, Face ID, PIN change, sign out
         /<slug>/manifest.webmanifest, icon-192.png  "Family Kitchen" on the
                             home screen, with the Kitchen icon (drawn here)
         /<slug>/help        the Share-shortcut guide, with their own key

Everything a member publishes is credited "Recipe by <Name>". Only they can
edit or unpublish it. Python 3.9.
"""
import hmac
import html as _html
import json
import os
import secrets
import sqlite3
import struct
import zlib
from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, Response, abort, g, jsonify, redirect, request, send_from_directory, session
from flask.sessions import SecureCookieSessionInterface

import family_auth as A
import family_care
import family_webauthn as W
import kitchen as K
import kitchen_nutrition as N

MARKER = "FAMILY_EDITION_V1"
APPNAME = "kitchen"
MEMBERS_DIR = os.path.join(K.KDIR, "members")
BASE_URL = (os.environ.get("KITCHEN_BASE") or "https://family.dr-manoj.in").rstrip("/")
INSECURE = os.environ.get("KITCHEN_INSECURE") == "1" or os.environ.get("FAMILY_INSECURE") == "1"
ACCENT = (31, 111, 92)

bp = Blueprint("kmember", __name__, url_prefix="/<kslug>")


# ------------------------------------------------------------------ auth storage
class MemberDB(object):
    """What family_auth.Auth needs: .con() on this member's own auth.db."""

    def __init__(self, slug):
        self.dir = os.path.join(MEMBERS_DIR, slug)
        self.path = os.path.join(self.dir, "auth.db")

    def con(self):
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir)
            os.chmod(self.dir, 0o700)
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        return c


def auth_for(slug):
    return A.Auth(MemberDB(slug))


def session_key():
    """The Kitchen's own session secret, made once, root-free, mode 600."""
    p = os.path.join(K.KDIR, "session.key")
    try:
        with open(p, encoding="utf-8") as fh:
            v = fh.read().strip()
        if len(v) >= 32:
            return v
    except OSError:
        pass
    v = secrets.token_hex(32)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(v + "\n")
        return v
    except FileExistsError:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()


class ScopedSession(SecureCookieSessionInterface):
    """The cookie's path is this member's own: /kitchen/k1/. Two members on
    one phone keep separate sessions, and the cookie never travels to the
    bearer routes or anyone else's pages."""

    def get_cookie_path(self, app):
        root = (request.script_root or "") if request else ""
        slug = getattr(g, "kslug", None)
        return root + ("/" + slug + "/" if slug else "/")


# ------------------------------------------------------------------ request setup
@bp.url_value_preprocessor
def _take_slug(_endpoint, values):
    g.kslug = (values or {}).pop("kslug", None)


@bp.url_defaults
def _put_slug(_endpoint, values):
    values.setdefault("kslug", getattr(g, "kslug", None))


@bp.before_request
def _member():
    slug = getattr(g, "kslug", None) or ""
    if not K.KSLUG_RX.match(slug):
        abort(404)
    g.km = K.kmember(slug)
    if not g.km:
        abort(404)
    g.auth = auth_for(slug)


def ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


def here(path=""):
    return (request.script_root or "") + "/" + g.kslug + path


def signed_in():
    if "kin" not in g:
        g.kin = bool(g.km.get("enabled")) and session.get("kslug") == g.kslug \
            and session.get("fam_ep") == g.auth.epoch()
    return g.kin


def start():
    session.clear()
    session["kslug"] = g.kslug
    session.permanent = True
    session["fam_ep"] = g.auth.epoch()
    touch(force=True)


def touch(force=False):
    """Last visit, to the minute IST, at most once every ten minutes."""
    now = family_care.now_ist().strftime("%Y-%m-%d %H:%M")
    last = g.km.get("last_seen") or ""
    if force or last[:15] != now[:15]:
        K.db().execute("UPDATE kmembers SET last_seen=? WHERE slug=?", (now, g.kslug))
        K.db().commit()


def need():
    if not signed_in():
        abort(Response('{"ok": false, "err": "Sign in first."}', status=401, mimetype="application/json"))
    touch()


def owner_name():
    return K.name_of("owner", "the owner")


def caretaker_text():
    return owner_name()


def title():
    return "%s \u2014 Family Kitchen" % g.km["name"]


# ------------------------------------------------------------------ pages
def page(t, body, script="", code=200, head=""):
    return Response(A.PAGE % {"title": _html.escape(t), "head": head, "body": body, "script": script},
                    status=code, mimetype="text/html")


def pwa_head():
    root = _html.escape(here(), quote=True)
    return ("<link rel='manifest' href='%(r)s/manifest.webmanifest'>"
            "<link rel='icon' href='%(r)s/icon-192.png'>"
            "<link rel='apple-touch-icon' href='%(r)s/icon-192.png'>"
            "<meta name='apple-mobile-web-app-capable' content='yes'>"
            "<meta name='mobile-web-app-capable' content='yes'>"
            "<meta name='apple-mobile-web-app-title' content='Family Kitchen'>"
            "<meta name='theme-color' content='#1f6f5c'>" % {"r": root})


def login_js():
    js = A.LOGIN_JS % {"slug": "k_" + g.kslug}
    return "const B=%s;\n" % json.dumps(here()) + js.replace("jpost('/passkey/", "jpost(B+'/passkey/")


def offer_js(nxt):
    js = A.OFFER_JS % {"slug": "k_" + g.kslug, "next": json.dumps(nxt)}
    js = js.replace("jpost('/passkey/", "jpost(B+'/passkey/")
    js = js.replace("from the Caretaker page", "from the Me tab")
    return "const B=%s;\n" % json.dumps(here()) + js


def login_page(err="", code=None):
    t = title()
    if not g.km.get("enabled"):
        body = ("<h1>%s</h1><p class='err'>This account is switched off.</p><p class='mut'>Ask %s.</p>"
                % (_html.escape(t), _html.escape(owner_name())))
        return page(t, body, "", 403, head=pwa_head())
    pk = ("<button type='button' class='ghost' id='pk' style='display:none'>Sign in with Face ID / Touch ID"
          "</button><p class='err' id='pkmsg'></p>")
    body = ("<h1>%s</h1><p class='mut'>Enter your 6-digit PIN.</p>"
            "<form method='post' action='%s' class='card' autocomplete='off'>"
            "<div class='pinrow'><input class='pin' id='pin' name='pin' type='password' inputmode='numeric' "
            "pattern='[0-9]*' maxlength='6' autocomplete='current-password' autofocus aria-label='PIN'>"
            "<button type='button' class='eye' id='eye' aria-label='Show or hide the PIN' "
            "aria-pressed='false'>Show</button></div>"
            "%s<button type='submit' id='go'>Sign in</button></form>%s"
            "<p class='mut'>Not %s? Ask %s for your own link.</p>"
            % (_html.escape(t), _html.escape(here("/login"), quote=True),
               ("<p class='err' id='err'>%s</p>" % _html.escape(err)) if err else "", pk,
               _html.escape(g.km["name"]), _html.escape(owner_name())))
    return page(t, body, login_js(), code or (200 if not err else 401), head=pwa_head())


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if signed_in():
            return redirect(here("/"))
        return login_page()
    if not g.km.get("enabled"):
        g.auth.log(APPNAME, ip(), "refused: account switched off")
        return login_page()
    pin = (request.form.get("pin") or "").strip()
    res, info = g.auth.attempt(pin, APPNAME, ip())
    if res == "locked":
        return login_page(A.paused_text(info, caretaker_text()))
    if res != "ok":
        return login_page(A.wrong_text(info))
    start()
    return redirect(here("/passkey/offer?next=%2F"))


@bp.route("/signout", methods=["POST"])
def signout():
    if signed_in():
        g.auth.log(APPNAME, ip(), "signed out")
    session.clear()
    return redirect(here("/login"))


@bp.route("/signout-all", methods=["POST"])
def signout_all():
    if not signed_in():
        return redirect(here("/login"))
    g.auth.rotate_epoch()
    g.auth.log(APPNAME, ip(), "signed out on all devices")
    session.clear()
    return redirect(here("/login"))


@bp.route("/pin/change", methods=["POST"])
def pin_change():
    if not signed_in():
        return jsonify(ok=False, err="Sign in first."), 401
    d = request.get_json(silent=True) or {}
    old, new = str(d.get("old") or "").strip(), str(d.get("new") or "").strip()
    res, info = g.auth.attempt(old, APPNAME, ip())
    if res != "ok":
        return jsonify(ok=False, err="The current PIN is not right (%d %s left)." % (info, "try" if info == 1 else "tries")
                       if res == "wrong" else A.paused_text(info, caretaker_text())), 400
    if not (len(new) == 6 and new.isdigit()) or A.weak_pin(new):
        return jsonify(ok=False, err="Choose 6 digits that are not all the same or in a straight run."), 400
    g.auth.set_pin(new)
    g.auth.log(APPNAME, ip(), "PIN changed")
    return jsonify(ok=True)


# -- passkeys (the same steps as family_auth.install, under this prefix) ----
def _rp():
    u = urlparse(BASE_URL)
    return u.hostname, "%s://%s" % (u.scheme, u.netloc)


@bp.route("/passkey/offer")
def passkey_offer():
    if not signed_in():
        return redirect(here("/login"))
    nxt = here(family_care.safe_next(request.args.get("next")))
    body = ("<div id='offer' style='display:none'><h1>Sign in with Face ID next time?</h1>"
            "<p class='mut'>Instead of the PIN, this phone can use Face ID or Touch ID. The PIN keeps working.</p>"
            "<div class='card'><button id='yes'>Use Face ID / Touch ID</button>"
            "<button class='ghost' id='no'>Not now</button><p class='err' id='msg'></p></div></div>")
    return page("Face ID", body, offer_js(nxt))


@bp.route("/passkey/register/begin", methods=["POST"])
def passkey_register_begin():
    if not signed_in():
        return jsonify(ok=False, err="Sign in first."), 403
    rp_id, _o = _rp()
    return jsonify(ok=True, challenge=g.auth.challenge("reg"), rp={"id": rp_id, "name": "Family Kitchen"},
                   user={"id": g.auth.user_handle(), "name": g.kslug, "displayName": g.km["name"]},
                   exclude=[p["cred_id"] for p in g.auth.passkeys()])


@bp.route("/passkey/register/finish", methods=["POST"])
def passkey_register_finish():
    if not signed_in():
        return jsonify(ok=False, err="Sign in first."), 403
    d = request.get_json(silent=True) or {}
    if not g.auth.take_challenge(d.get("challenge"), "reg"):
        return jsonify(ok=False, err="That took too long -- try again."), 400
    rp_id, origin = _rp()
    try:
        r = W.verify_registration(d, d.get("challenge"), origin, rp_id)
    except W.WebAuthnError as e:
        g.auth.log(APPNAME, ip(), "Face ID set-up refused: %s" % e)
        return jsonify(ok=False, err="Face ID could not be set up (%s)." % e), 400
    ua = request.headers.get("User-Agent") or ""
    label = "iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Mac" if "Mac" in ua else "this device"
    g.auth.add_passkey(r["cred_id"], r["key"], r["count"], label)
    g.auth.log(APPNAME, ip(), "Face ID / Touch ID set up (%s)" % label)
    return jsonify(ok=True)


@bp.route("/passkey/login/begin", methods=["POST"])
def passkey_login_begin():
    pks = g.auth.passkeys() if g.km.get("enabled") else []
    if not pks:
        return jsonify(ok=False, err="Face ID is not set up yet -- use your PIN."), 404
    rp_id, _o = _rp()
    return jsonify(ok=True, challenge=g.auth.challenge("login"), rpId=rp_id, allow=[p["cred_id"] for p in pks])


@bp.route("/passkey/login/finish", methods=["POST"])
def passkey_login_finish():
    d = request.get_json(silent=True) or {}
    if not g.km.get("enabled") or not g.auth.take_challenge(d.get("challenge"), "login"):
        return jsonify(ok=False, err="That took too long -- try again."), 400
    pk = next((p for p in g.auth.passkeys() if hmac.compare_digest(p["cred_id"], str(d.get("id") or ""))), None)
    if not pk:
        g.auth.log(APPNAME, ip(), "Face ID refused: unknown key")
        return jsonify(ok=False, err="This Face ID is not set up for this account."), 400
    rp_id, origin = _rp()
    try:
        count = W.verify_assertion(d, d.get("challenge"), origin, rp_id, json.loads(pk["key"]), pk["count"])
    except W.WebAuthnError as e:
        g.auth.log(APPNAME, ip(), "Face ID refused: %s" % e)
        return jsonify(ok=False, err="Face ID sign-in failed."), 400
    g.auth.touch_passkey(pk["cred_id"], count)
    g.auth.cleared(APPNAME, ip(), "signed in (Face ID / Touch ID)")
    start()
    return jsonify(ok=True, next=here("/"))


@bp.route("/passkey/remove/<int:pid>", methods=["POST"])
def passkey_remove(pid):
    if not signed_in():
        return jsonify(ok=False, err="Sign in first."), 401
    g.auth.remove_passkey(pid)
    g.auth.log(APPNAME, ip(), "Face ID / Touch ID removed")
    return jsonify(ok=True)


# -- home screen ------------------------------------------------------------
@bp.route("/manifest.webmanifest")
def manifest():
    # Paths are app-relative: the prefix wrapper adds /kitchen in front.
    root = "/" + g.kslug + "/"
    j = {"id": root, "start_url": root, "scope": root, "name": "Family Kitchen", "short_name": "Kitchen",
         "display": "standalone", "background_color": "#f6f7f9", "theme_color": "#1f6f5c",
         "icons": [{"src": root + "icon-192.png", "sizes": "192x192", "type": "image/png"},
                   {"src": root + "icon-512.png", "sizes": "512x512", "type": "image/png"}]}
    return Response(json.dumps(j, indent=2), mimetype="application/manifest+json")


_ICONS = {}


def kitchen_icon(size):
    """The Kitchen icon, drawn here (no binary in the repository): a rounded
    green square with a white bowl."""
    if size in _ICONS:
        return _ICONS[size]
    s = float(size)
    rad = s * 0.22
    cx, cy, rx, ry = s * 0.5, s * 0.56, s * 0.36, s * 0.27
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        fy = y + 0.5
        for x in range(size):
            fx = x + 0.5
            # rounded square
            dx = max(rad - fx, fx - (s - rad), 0.0)
            dy = max(rad - fy, fy - (s - rad), 0.0)
            if dx * dx + dy * dy > rad * rad:
                row += b"\xf6\xf7\xf9"
                continue
            bowl = fy >= cy and ((fx - cx) / rx) ** 2 + ((fy - cy) / ry) ** 2 <= 1.0
            rim = cy - s * 0.045 <= fy < cy and abs(fx - cx) <= rx + s * 0.03
            steam = (fy < cy - s * 0.12 and fy > s * 0.16 and
                     abs(((fx - cx) / (s * 0.09)) - round((fx - cx) / (s * 0.09))) < 0.16
                     and abs((fx - cx) / (s * 0.09)) < 1.6 and int(fy / (s * 0.05)) % 2 == 0)
            row += b"\xff\xff\xff" if (bowl or rim or steam) else bytes(ACCENT)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind, data):
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    _ICONS[size] = png
    return png


@bp.route("/icon-192.png")
def icon_192():
    return Response(kitchen_icon(192), mimetype="image/png")


@bp.route("/icon-512.png")
def icon_512():
    return Response(kitchen_icon(512), mimetype="image/png")


@bp.route("/help")
def help_page():
    return K.help_page()


# ------------------------------------------------------------------ the book
@bp.route("/")
def home():
    if not signed_in():
        return redirect(here("/login"))
    touch()
    cfg = {"base": here(), "name": g.km["name"], "slug": g.kslug, "groups": K.GROUPS,
           "prefs_all": [list(p) for p in K.PREFS], "help": BASE_URL + "/kitchen/help"}
    return Response(BOOK_PAGE.replace("__CFG__", json.dumps(cfg).replace("<", "\\u003c")).replace("__HEAD__", pwa_head())
                    .replace("__NAME__", _html.escape(g.km["name"])), mimetype="text/html")


def prefs():
    return K.prefs_of(g.km.get("food_prefs"))


@bp.route("/j/me", methods=["GET"])
def j_me():
    need()
    cap = (K.tokens().get("capture") or {}).get(g.kslug) or {}
    return jsonify(ok=True, slug=g.kslug, name=g.km["name"], prefs=prefs(), prefs_all=[list(p) for p in K.PREFS],
                   capture={"url": BASE_URL + "/kitchen/capture/" + g.kslug, "token": str(cap.get("token") or ""),
                            "help": BASE_URL + "/kitchen/help"},
                   passkeys=[{"id": p["id"], "label": p["label"], "created": p["created"],
                              "last_used": p["last_used"]} for p in g.auth.passkeys()],
                   log=g.auth.entries(20))


@bp.route("/j/prefs", methods=["POST"])
def j_prefs():
    need()
    d = request.get_json(silent=True) or {}
    want = [p for p in (d.get("prefs") or []) if p in dict(K.PREFS)]
    K.db().execute("UPDATE kmembers SET food_prefs=? WHERE slug=?", (",".join(want), g.kslug))
    K.db().commit()
    g.km["food_prefs"] = ",".join(want)
    return jsonify(ok=True, prefs=want)


@bp.route("/j/recipes")
def j_recipes():
    need()
    args = dict((k, request.args.get(k)) for k in ("q", "grp", "sort", "by") if request.args.get(k))
    if args.get("by") == "me":
        args["by"] = g.kslug
    return jsonify(ok=True, recipes=K.list_recipes(g.kslug, args, prefs()), groups=K.GROUPS, prefs=prefs())


@bp.route("/j/people")
def j_people():
    need()
    return jsonify(ok=True, people=K.people_list())


@bp.route("/j/recipe/<int:rid>")
def j_recipe(rid):
    need()
    r = K._recipe_or_404(rid, g.kslug)
    d = K.recipe_json(r, g.kslug, full=True)
    return jsonify(ok=True, recipe=d, nutrition=N.recipe_nutrition(d["ingredients"], d["servings"]))


@bp.route("/j/recipe/<int:rid>/attach")
def j_recipe_attach(rid):
    need()
    r = K._recipe_or_404(rid, g.kslug)
    if not r["attach"]:
        abort(404)
    resp = send_from_directory(K.ATTACH, r["attach"])
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/j/recipe/<int:rid>/rate", methods=["POST"])
def j_rate(rid):
    need()
    return K.rate_recipe(rid, g.kslug, request.get_json(silent=True) or {})


@bp.route("/j/recipe/<int:rid>/edit", methods=["POST"])
def j_edit(rid):
    need()
    return K.edit_recipe(rid, g.kslug, request.get_json(silent=True) or {})


@bp.route("/j/recipe/<int:rid>/unpublish", methods=["POST"])
def j_unpublish(rid):
    need()
    d = request.get_json(silent=True) or {}
    return K.set_published(rid, g.kslug, bool(d.get("publish")))


@bp.route("/j/drafts", methods=["GET", "POST"])
def j_drafts():
    need()
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        text = str(d.get("text") or "").strip()
        url = str(d.get("url") or "").strip()
        if not text and not url:
            return jsonify(ok=False, err="Paste a recipe or a link."), 400
        return jsonify(ok=True, id=K.new_draft(g.kslug, text=text, url=url))
    return jsonify(ok=True, drafts=K.drafts_of(g.kslug))


@bp.route("/j/drafts/upload", methods=["POST"])
def j_upload():
    """A photo or PDF chosen on the phone: a draft, exactly as a Share would be."""
    need()
    ids, _t, err = K.capture_files(g.kslug, request.files.getlist("file"), "")
    if err:
        return jsonify(ok=False, err=err), 400
    if not ids:
        return jsonify(ok=False, err="Choose a photo or a PDF."), 400
    return jsonify(ok=True, drafts=ids)


@bp.route("/j/drafts/<int:did>")
def j_draft(did):
    need()
    d = K.draft_json(did, g.kslug)
    ex = d.get("extracted") or {}
    unknown = [i.get("item") for i in ex.get("ingredients") or [] if i.get("item") and N.ing_food(i["item"])[0] is None]
    return jsonify(ok=True, draft=d, unknown=unknown)


@bp.route("/j/drafts/<int:did>/attach")
def j_draft_attach(did):
    need()
    r = K._own_draft(did, g.kslug)
    if not r["attach"]:
        abort(404)
    resp = send_from_directory(K.ATTACH, r["attach"])
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/j/drafts/<int:did>/publish", methods=["POST"])
def j_publish(did):
    need()
    return K.publish_draft(did, g.kslug, g.km["name"], request.get_json(silent=True) or {})


@bp.route("/j/drafts/<int:did>/discard", methods=["POST"])
def j_discard(did):
    need()
    return K.discard_draft(did, g.kslug)


# ------------------------------------------------------------------ the page
BOOK_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Family Kitchen</title>__HEAD__
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--mod:#8a4b00;--bad:#a4262c}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--mod:#f0b35a;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:36rem;margin:0 auto;padding:12px 14px 40px;overflow-wrap:anywhere}h1{font-size:21px;margin:4px 0 10px}h2{font-size:20px;margin:0 0 2px}h3{font-size:17px;margin:12px 0 4px}
.tabs,.chips{display:flex;flex-wrap:wrap;gap:6px}.tab,.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:7px 12px;font:inherit;font-size:15px;cursor:pointer}
.tab.sel,.chip.sel{background:var(--acc);color:#fff;border-color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0;overflow-wrap:anywhere}
.row{display:flex;justify-content:space-between;gap:8px}.mut{color:var(--mut);font-size:15px}
.by{color:var(--acc);font-weight:600;margin:2px 0 4px;font-size:15px}
.badge{display:inline-block;color:var(--mod);border:1px solid var(--mod);border-radius:6px;padding:0 6px;font-size:13px;font-weight:600}
.flag{color:var(--bad)}.warn{color:var(--mod)}
input,textarea,select{width:100%;min-width:0;font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
button.btn{font:inherit;font-weight:600;padding:10px 14px;border-radius:10px;border:0;background:var(--acc);color:#fff;margin:8px 6px 0 0}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc)}button.danger{background:transparent;color:var(--bad);border:1px solid var(--bad)}
.ir{display:grid;grid-template-columns:1fr 56px 64px;gap:4px;margin:4px 0}img.att{max-width:100%;border-radius:8px}
a{color:var(--acc)}label.ck{display:block;margin:6px 0}label.ck input{width:auto;margin-right:8px}
.ver a{margin-right:8px}p{margin:6px 0}
</style></head><body><main>
<h1>Family Kitchen</h1>
<div class="tabs" id="tabs"></div><div id="view"></div>
<div id="toast" class="mut" style="position:fixed;bottom:12px;left:12px;right:12px;text-align:center"></div>
</main>
<script>
/* FAMILY_EDITION_V1 -- kitchen member page */
const CFG=__CFG__;const B=CFG.base;
const $=q=>document.querySelector(q);
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;}
function toast(m){const t=$('#toast');t.textContent=m;setTimeout(()=>{t.textContent='';},2400);}
async function jget(u){const r=await fetch(B+u,{credentials:'same-origin'});if(r.status===401){location.href=B+'/login';throw new Error('signed out');}return r.json();}
async function post(u,b){const r=await fetch(B+u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const j=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(j.err||'Save failed');e.j=j;e.status=r.status;throw e;}return j;}
async function upload(u,fd){const r=await fetch(B+u,{method:'POST',credentials:'same-origin',body:fd});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.err||'Upload failed');return j;}
const TABS=[['browse','Recipes'],['inbox','Inbox'],['add','Add'],['me','Me']];
let ST={tab:'browse',grp:'',sort:'az',q:'',by:''};
function tabs(){const b=$('#tabs');b.innerHTML='';TABS.forEach(([k,l])=>{const x=el('button','tab'+(ST.tab===k?' sel':''),l);x.onclick=()=>{ST.tab=k;show();};b.appendChild(x);});}
function show(){tabs();({browse,inbox,add,me})[ST.tab]();}
function byLine(r){return 'Recipe by '+(r.added_by||'someone');}
function versionsText(r){return r.versions&&r.versions.length>1?(r.versions.length+' versions \u2014 by '+r.versions.map(v=>v.by).join(', ')):'';}
function statusNote(r){if(r.status==='hidden')return 'Hidden from the Kitchen by '+(r.hidden_note?('the owner: '+r.hidden_note):'the owner')+' \u2014 only you can see it.';
  if(r.status==='unpublished')return 'Unpublished \u2014 only you can see it.';return '';}
function listCard(r){const c=el('div','card');c.style.cursor='pointer';
  const h=el('div','row');h.appendChild(el('b','',r.name));
  h.appendChild(el('span','mut',r.rating.avg?('\u2605 '+r.rating.avg+' ('+r.rating.n+')'):''));c.appendChild(h);
  c.appendChild(el('p','by',byLine(r)));
  c.appendChild(el('p','mut',r.grp+' \u00b7 added '+(r.added_date||'')+(r.new?' \u00b7 new':'')+(r.rating.made?(' \u00b7 made '+r.rating.made+'\u00d7'):'')));
  const v=versionsText(r);if(v)c.appendChild(el('p','mut',v));
  const s=statusNote(r);if(s)c.appendChild(el('p','warn',s));
  c.onclick=()=>recipe(r.id);return c;}
async function browse(){
  const v=$('#view');v.innerHTML='';
  const s=el('input');s.placeholder='Search a dish or an ingredient';s.value=ST.q;s.setAttribute('aria-label','Search');
  s.onchange=()=>{ST.q=s.value;browse();};v.appendChild(s);
  const so=el('div','chips');so.style.marginTop='8px';
  [['az','All'],['top','Top rated'],['new','New this week'],['fav','Family favourites'],['by','By person'],['mine','Mine']].forEach(([k,l])=>{
    const c=el('button','chip'+(ST.sort===k?' sel':''),l);c.onclick=()=>{ST.sort=k;ST.by=(k==='mine'?'me':'');browse();};so.appendChild(c);});
  v.appendChild(so);
  if(ST.sort==='by'){let p;try{p=await jget('/j/people');}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
    const pc=el('div','chips');pc.style.marginTop='8px';
    (p.people||[]).forEach(x=>{const c=el('button','chip'+(ST.by===x.slug?' sel':''),x.name+' ('+x.n+')');c.onclick=()=>{ST.by=x.slug;browse();};pc.appendChild(c);});
    v.appendChild(pc);if(!ST.by){v.appendChild(el('p','mut','Tap a name to see their recipes.'));return;}}
  const sort=(ST.sort==='by'||ST.sort==='mine')?'az':ST.sort;
  let j;try{j=await jget('/j/recipes?sort='+sort+'&q='+encodeURIComponent(ST.q)+'&grp='+encodeURIComponent(ST.grp)+'&by='+encodeURIComponent(ST.by));}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}
  const g=el('div','chips');g.style.marginTop='8px';
  ['',...(j.groups||[])].forEach(x=>{const c=el('button','chip'+(ST.grp===x?' sel':''),x||'Every group');c.onclick=()=>{ST.grp=x;browse();};g.appendChild(c);});
  v.appendChild(g);
  if((j.prefs||[]).length){const names=Object.fromEntries(CFG.prefs_all);v.appendChild(el('p','mut','Showing what fits your preferences: '+j.prefs.map(p=>names[p]||p).join(', ')+'.'));}
  if(!(j.recipes||[]).length)v.appendChild(el('p','mut',ST.sort==='mine'?'Nothing of yours yet. Share or paste a recipe, then publish it from your Inbox.':'Nothing here yet.'));
  (j.recipes||[]).forEach(r=>v.appendChild(listCard(r)));
}
function ingText(i){return i.text||(((i.qty!=null?i.qty+' ':'')+(i.unit||'')+' '+i.item).trim());}
async function recipe(id){
  const v=$('#view');v.innerHTML='';
  let j;try{j=await jget('/j/recipe/'+id);}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'Not found.'));return;}
  const r=j.recipe,c=el('div','card');
  c.appendChild(el('h2','',r.name));
  c.appendChild(el('p','by',byLine(r)));
  c.appendChild(el('p','mut','Added '+(r.added_date||'')+' \u00b7 '+r.grp+' \u00b7 serves '+r.servings+(r.serving_text?(' \u00b7 '+r.serving_text):'')));
  if(r.source_url){const p=el('p','mut','Shared by '+r.added_by+' \u00b7 source: ');const a=el('a','',r.source_site||r.source_url);a.href=r.source_url;a.target='_blank';a.rel='noopener';p.appendChild(a);c.appendChild(p);}
  else if(r.has_photo){c.appendChild(el('p','mut','Shared by '+r.added_by+' \u00b7 from a photo'));}
  if(r.has_photo){const im=el('img','att');im.src=B+'/j/recipe/'+id+'/attach';im.alt='The original photo';c.appendChild(im);}
  if(r.versions&&r.versions.length>1){const p=el('p','ver');p.appendChild(el('span','mut',r.versions.length+' versions \u2014 by '+r.versions.map(x=>x.by).join(', ')+'. '));
    r.versions.filter(x=>x.id!==r.id).forEach(x=>{const a=el('a','',x.by+"'s");a.href='#';a.onclick=(e)=>{e.preventDefault();recipe(x.id);};p.appendChild(a);});c.appendChild(p);}
  const sn=statusNote(r);if(sn)c.appendChild(el('p','warn',sn));
  c.appendChild(el('h3','','Ingredients'));
  (r.ingredients||[]).forEach(i=>{const p=el('p','',ingText(i));p.style.margin='2px 0';c.appendChild(p);});
  c.appendChild(el('h3','','Method'));
  (r.method||[]).forEach((m,k)=>c.appendChild(el('p','',(k+1)+'. '+m)));
  (r.notes||[]).forEach(m=>c.appendChild(el('p','mut',m)));
  const n=j.nutrition.per_serving,nu=el('div','card');
  nu.appendChild(el('b','','Per serving'));
  const f=[['kcal','kcal',''],['protein','protein',' g'],['fibre','fibre',' g'],['fat','fat',' g'],['carbs','carbs',' g'],['calcium','calcium',' mg']];
  nu.appendChild(el('p','',f.map(([k,l,u])=>l+' '+(n[k]==null?'\u2014':n[k]+u)).join(' \u00b7 ')));
  if(j.nutrition.unmatched.length){nu.appendChild(el('p','flag','Not counted: '+j.nutrition.unmatched.map(x=>x.item+' ('+x.why+')').join('; ')));}
  c.appendChild(nu);
  if(r.status==='published'){
    const rt=el('div','chips');rt.style.marginTop='10px';
    [1,2,3,4,5].forEach(s=>{const b=el('button','chip'+((r.mine&&r.mine.stars===s)?' sel':''),'\u2605'.repeat(s));
      b.onclick=async()=>{await post('/j/recipe/'+id+'/rate',{stars:s});recipe(id);};rt.appendChild(b);});
    c.appendChild(rt);
    const mk=el('div','chips');mk.style.marginTop='6px';
    [['made','I made it'],['again','Would make again']].forEach(([k,l])=>{const on=r.mine&&r.mine[k];
      const b=el('button','chip'+(on?' sel':''),l);b.onclick=async()=>{const o={};o[k]=!on;await post('/j/recipe/'+id+'/rate',o);recipe(id);};mk.appendChild(b);});
    c.appendChild(mk);
    const note=el('input');note.placeholder='A short note for the family';note.value=(r.mine&&r.mine.note)||'';
    note.onchange=async()=>{await post('/j/recipe/'+id+'/rate',{note:note.value});toast('Saved');};
    note.style.marginTop='8px';c.appendChild(note);
    (r.ratings||[]).forEach(x=>c.appendChild(el('p','mut',x.who+': '+(x.stars?'\u2605'.repeat(x.stars)+' ':'')+(x.made?'made it ':'')+(x.again?'\u00b7 would make again ':'')+(x.note?('\u2014 '+x.note):''))));
  }
  if(r.own){
    const ed=el('button','btn ghost','Edit my recipe');ed.onclick=()=>edit(r);c.appendChild(ed);
    if(r.status!=='hidden'){const un=el('button','btn '+(r.status==='published'?'danger':''),r.status==='published'?'Unpublish':'Publish again');
      un.onclick=async()=>{try{await post('/j/recipe/'+id+'/unpublish',{publish:r.status!=='published'});recipe(id);}catch(e){toast(e.message);}};c.appendChild(un);}
  }
  const bk=el('button','btn ghost','Back to recipes');bk.onclick=browse;c.appendChild(bk);
  v.appendChild(c);
}
function irow(box,i){const r=el('div','ir');
  const a=el('input');a.value=i.item||'';a.placeholder='ingredient';
  const q=el('input');q.value=i.qty==null?'':i.qty;q.placeholder='amt';q.inputMode='decimal';
  const u=el('input');u.value=i.unit||'';u.placeholder='unit';
  if(i.qty==null&&i.unit!=='pinch')q.style.borderColor='var(--bad)';
  if(i.unknown)a.style.borderColor='var(--mod)';
  [a,q,u].forEach(x=>r.appendChild(x));r._get=()=>({item:a.value.trim(),qty:q.value===''?null:parseFloat(q.value),unit:u.value.trim(),text:i.text||''});
  box.appendChild(r);return r;}
function form(c,ex,unknown){
  const nm=el('input');nm.value=ex.name||'';nm.placeholder='Dish name';c.appendChild(el('p','','Name'));c.appendChild(nm);
  const gp=el('select');CFG.groups.forEach(x=>{const o=el('option','',x);o.value=x;gp.appendChild(o);});
  gp.value=CFG.groups.includes(ex.grp)?ex.grp:'Other';c.appendChild(el('p','','Group'));c.appendChild(gp);
  const sv=el('input');sv.value=ex.servings||'';sv.inputMode='decimal';c.appendChild(el('p','','Serves'));c.appendChild(sv);
  c.appendChild(el('p','','Ingredients'));const ib=el('div');c.appendChild(ib);
  const unk=new Set(unknown||[]);let rows=(ex.ingredients||[]).map(i=>irow(ib,Object.assign({},i,{unknown:unk.has(i.item)})));
  const ad=el('button','btn ghost','+ ingredient');ad.onclick=()=>rows.push(irow(ib,{}));c.appendChild(ad);
  const me_=el('textarea');me_.rows=6;me_.value=(ex.method||[]).join('\n');c.appendChild(el('p','','Method (one step a line)'));c.appendChild(me_);
  const nt=el('textarea');nt.rows=2;nt.value=(ex.notes||[]).join('\n');c.appendChild(el('p','','Notes (optional)'));c.appendChild(nt);
  return ()=>({name:nm.value,grp:gp.value,servings:sv.value,ingredients:rows.map(r=>r._get()).filter(i=>i.item),
    method:me_.value.split('\n').filter(s=>s.trim()),notes:nt.value.split('\n').filter(s=>s.trim())});}
function edit(r){const v=$('#view');v.innerHTML='';const c=el('div','card');c.appendChild(el('h2','','Edit: '+r.name));
  const get=form(c,r,[]);
  const ok=el('button','btn','Save');ok.onclick=async()=>{try{await post('/j/recipe/'+r.id+'/edit',get());toast('Saved');recipe(r.id);}catch(e){toast(e.message);}};c.appendChild(ok);
  const no=el('button','btn ghost','Cancel');no.onclick=()=>recipe(r.id);c.appendChild(no);v.appendChild(c);}
async function inbox(){
  const v=$('#view');v.innerHTML='';
  let j;try{j=await jget('/j/drafts');}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}
  if(!(j.drafts||[]).length)v.appendChild(el('p','mut','Nothing waiting. Shared recipes land here; you check them and tap Publish.'));
  (j.drafts||[]).forEach(d=>{const c=el('div','card');c.style.cursor='pointer';
    c.appendChild(el('b','',(d.extracted&&d.extracted.name)||('A shared '+d.kind)));
    c.appendChild(el('p','mut',d.status==='new'||d.status==='reading'?'Being read \u2026':(d.note||d.status)));
    if((d.flags||[]).length)c.appendChild(el('p','flag',d.flags.length+' thing'+(d.flags.length>1?'s':'')+' to check'));
    c.onclick=()=>review(d.id);v.appendChild(c);});
}
async function review(id){
  const v=$('#view');v.innerHTML='';
  let j;try{j=await jget('/j/drafts/'+id);}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  if(!j.ok){v.appendChild(el('p','mut',j.err||'Not found.'));return;}
  const d=j.draft,ex=d.extracted||{name:'',servings:null,ingredients:[],method:[]};
  const c=el('div','card');c.appendChild(el('h2','','Check, then publish'));
  c.appendChild(el('p','mut','It joins the Family Kitchen as "Recipe by '+CFG.name+'" the moment you tap Publish.'));
  if(d.attach){if(/\.(jpe?g|png|webp)$/i.test(d.attach)){const im=el('img','att');im.src=B+'/j/drafts/'+id+'/attach';im.alt='The original';c.appendChild(im);}
    else{const a=el('a','','Open the shared file');a.href=B+'/j/drafts/'+id+'/attach';a.target='_blank';c.appendChild(a);}}
  if(d.url){const a=el('a','',d.url);a.href=d.url;a.target='_blank';a.rel='noopener';c.appendChild(el('p','')).appendChild(a);}
  if(d.text&&!ex.name)c.appendChild(el('p','mut',d.text.slice(0,600)));
  (d.flags||[]).forEach(f=>c.appendChild(el('p','flag','\u26a0 '+f.text)));
  if((j.unknown||[]).length)c.appendChild(el('p','mut','Not in the food table (their nutrition will not count): '+j.unknown.join(', ')));
  if(d.duplicate)c.appendChild(el('p','flag','Looks like "'+d.duplicate.name+'" by '+(d.duplicate.by||'someone')+' already in the Kitchen ('+d.duplicate.why+').'));
  const get=form(c,ex,j.unknown);
  const dupBox=el('div');c.appendChild(dupBox);
  const go=async(extra)=>{try{
      const x=await post('/j/drafts/'+id+'/publish',Object.assign(get(),extra||{}));
      toast('Published \u2014 in the Family Kitchen now');ST.tab='browse';ST.sort='mine';ST.by='me';show();return x;}
    catch(e){if(e.status===409&&e.j&&e.j.duplicate){dupBox.innerHTML='';
        dupBox.appendChild(el('p','flag','"'+e.j.duplicate.name+'" by '+(e.j.duplicate.by||'someone')+' is already in the Kitchen ('+e.j.duplicate.why+').'));
        const y=el('button','btn',"Publish as "+CFG.name+"'s version");y.onclick=()=>go({force:true,as_version:true});dupBox.appendChild(y);
        const n=el('button','btn ghost','Cancel');n.onclick=()=>{dupBox.innerHTML='';};dupBox.appendChild(n);}
      else toast(e.message);}};
  const ok=el('button','btn','Publish');ok.id='publish';ok.onclick=()=>go();c.appendChild(ok);
  const no=el('button','btn ghost','Discard');no.onclick=async()=>{await post('/j/drafts/'+id+'/discard');inbox();};c.appendChild(no);
  v.appendChild(c);
}
function add(){
  const v=$('#view');v.innerHTML='';const c=el('div','card');
  c.appendChild(el('p','','Paste a recipe, or a link to one. It goes to your Inbox; you publish it from there.'));
  const t=el('textarea');t.rows=8;t.setAttribute('aria-label','Recipe or link');c.appendChild(t);
  const b=el('button','btn','Send to my Inbox');b.onclick=async()=>{const s=t.value.trim();if(!s)return;
    const m=s.match(/^https?:\/\/\S+$/);try{await post('/j/drafts',m?{url:s}:{text:s});toast('In your Inbox');t.value='';}catch(e){toast(e.message);}};
  c.appendChild(b);v.appendChild(c);
  const c2=el('div','card');c2.appendChild(el('p','','Or a photo of a recipe, or a PDF.'));
  const f=el('input');f.type='file';f.accept='image/*,.pdf';f.setAttribute('aria-label','Photo or PDF');c2.appendChild(f);
  const b2=el('button','btn','Send to my Inbox');b2.onclick=async()=>{if(!f.files.length)return;const fd=new FormData();fd.append('file',f.files[0]);
    try{await upload('/j/drafts/upload',fd);toast('In your Inbox \u2014 being read');f.value='';}catch(e){toast(e.message);}};
  c2.appendChild(b2);v.appendChild(c2);
}
async function me(){
  const v=$('#view');v.innerHTML='';
  let j;try{j=await jget('/j/me');}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}
  const c=el('div','card');c.appendChild(el('h2','',j.name));c.appendChild(el('p','mut','Your recipes are published as "Recipe by '+j.name+'".'));
  c.appendChild(el('h3','','Food preferences'));c.appendChild(el('p','mut','Only a filter on what you see: recipes with onion or garlic, say, are left out and the onion-free version shown instead.'));
  const boxes=[];(j.prefs_all||[]).forEach(([k,l])=>{const lb=el('label','ck');const ck=el('input');ck.type='checkbox';ck.checked=(j.prefs||[]).includes(k);ck.value=k;boxes.push(ck);lb.appendChild(ck);lb.appendChild(document.createTextNode(l));c.appendChild(lb);});
  const sp=el('button','btn','Save preferences');sp.onclick=async()=>{try{await post('/j/prefs',{prefs:boxes.filter(b=>b.checked).map(b=>b.value)});toast('Saved');}catch(e){toast(e.message);}};c.appendChild(sp);
  v.appendChild(c);
  const s=el('div','card');s.appendChild(el('h3','','Share shortcut'));
  if(!j.capture.token){s.appendChild(el('p','mut','Sharing is not set up for you yet.'));}
  else{s.appendChild(el('p','','The iPhone Share button can send a recipe straight to your Inbox \u2014 from WhatsApp, a web page, a photo or a PDF. When the shortcut asks, paste these two:'));
    s.appendChild(el('p','mut','Address'));const a=el('input');a.readOnly=true;a.value=j.capture.url;s.appendChild(a);
    s.appendChild(el('p','mut','Key (keep it private)'));const k=el('input');k.readOnly=true;k.value=j.capture.token;s.appendChild(k);
    const h=el('a','','The 10-minute setup guide');h.href=j.capture.help;h.target='_blank';s.appendChild(el('p','')).appendChild(h);}
  v.appendChild(s);
  const p=el('div','card');p.appendChild(el('h3','','Face ID / Touch ID'));
  if(!(j.passkeys||[]).length)p.appendChild(el('p','mut','Not set up on any phone yet.'));
  (j.passkeys||[]).forEach(x=>{const r=el('p','',x.label+' \u00b7 since '+(x.created||'')+(x.last_used?(' \u00b7 last used '+x.last_used):''));
    const b=el('button','btn ghost','Remove');b.style.marginLeft='8px';b.onclick=async()=>{await post('/passkey/remove/'+x.id);me();};r.appendChild(b);p.appendChild(r);});
  const setup=el('button','btn ghost','Set up on this phone');setup.onclick=()=>{try{localStorage.removeItem('fam_pk_k_'+CFG.slug);}catch(e){}location.href=B+'/passkey/offer?next=%2F';};p.appendChild(setup);
  v.appendChild(p);
  const pc=el('div','card');pc.appendChild(el('h3','','Change PIN'));
  const o=el('input');o.type='password';o.inputMode='numeric';o.maxLength=6;o.placeholder='Current PIN';o.setAttribute('aria-label','Current PIN');pc.appendChild(o);
  const n=el('input');n.type='password';n.inputMode='numeric';n.maxLength=6;n.placeholder='New PIN (6 digits)';n.style.marginTop='6px';n.setAttribute('aria-label','New PIN');pc.appendChild(n);
  const cb=el('button','btn','Change');cb.onclick=async()=>{try{await post('/pin/change',{old:o.value,new:n.value});toast('PIN changed');o.value=n.value='';}catch(e){toast(e.message);}};pc.appendChild(cb);
  v.appendChild(pc);
  const lg=el('div','card');lg.appendChild(el('h3','','Recent sign-in attempts (IST)'));
  (j.log||[]).forEach(x=>lg.appendChild(el('p','mut',x.at+' \u00b7 '+x.result)));
  v.appendChild(lg);
  const so=el('div','card');
  const f1=el('form');f1.method='post';f1.action=B+'/signout';f1.appendChild(el('button','btn ghost','Sign out'));so.appendChild(f1);
  const f2=el('form');f2.method='post';f2.action=B+'/signout-all';f2.appendChild(el('button','btn danger','Sign out on all devices'));so.appendChild(f2);
  v.appendChild(so);
}
show();
</script></body></html>"""


def install(app):
    """Mount the member pages on the Kitchen app: its own session secret and
    a per-member cookie path; nothing else about the app changes."""
    app.secret_key = session_key()
    app.session_interface = ScopedSession()
    app.config.update(SESSION_COOKIE_NAME="kitchen", SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=not INSECURE, SESSION_COOKIE_HTTPONLY=True,
                      PERMANENT_SESSION_LIFETIME=timedelta(days=A.SESSION_DAYS))
    app.register_blueprint(bp)
    return bp

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_physio.py -- a physiotherapist's sign-in to ONE member's copy.

FAMILY_EDITION_V1 (weight profile, 26-Sep-2026). A physio (slug p1, p2 ...)
is a role inside a member's GutLog process, at

    https://family.dr-manoj.in/m3/physio/

with the family sign-in rules exactly (family_auth: 6-digit PIN, lockout,
IST attempt log, Face ID / Touch ID, 12-month session, sign-out-all) on
their OWN auth.db under <member>/physio/<pslug>/, and their own cookie,
named fam_<member>_physio and scoped to /<member>/physio/. That cookie is
read by the routes here and by nothing else: GutLog's login_required looks
at the app's session cookie (a different name and path), so a physio
session reaches no page or API of the record -- test_family_e.py sends it
to /api/now, /rx/, /api/plans and a second member and expects refusal.

WHAT A PHYSIO SEES, AND NOTHING ELSE
  * the member's physio programme (exercises with sets, reps, days), which
    the physio edits;
  * the session log (the physio or the member ticks a day);
  * knee / joint pain and walking tolerance entries (GutLog's joint log:
    site, score 0-10, minutes walked before pain) -- read and add;
  * the monthly re-test fields: 30-second sit-to-stand, 6-minute walk,
    knee ROM, single-leg stance.
The member sees the programme as a Physio tile on their Now page (tick
today's session; see the next one); the caretaker sees the tile too. One
physio can be attached to more than one member (stamp_member.py --physio
--for). Python 3.9.
"""
import html as _html
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from flask import Response, jsonify, redirect, request

import family_auth as A
import family_care
import family_webauthn as W

MARKER = "FAMILY_EDITION_V1"
APPNAME = "physio"
PSLUG_RX_S = r"^p[0-9]{1,3}$"
SESSION_DAYS = A.SESSION_DAYS
SITES = ["Knee - L", "Knee - R", "Ankle - L", "Ankle - R", "Hip - L", "Hip - R", "Back"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RETEST_FIELDS = [("sts30", "30-second sit-to-stand", "reps", 0, 60),
                 ("walk6", "6-minute walk", "m", 0, 1500),
                 ("rom_l", "Knee ROM left", "deg", 0, 160), ("rom_r", "Knee ROM right", "deg", 0, 160),
                 ("sls_l", "Single-leg stance left", "s", 0, 120), ("sls_r", "Single-leg stance right", "s", 0, 120)]

SCHEMA = """
CREATE TABLE IF NOT EXISTS physio_programme (
  id INTEGER PRIMARY KEY AUTOINCREMENT, items TEXT NOT NULL DEFAULT '[]', notes TEXT DEFAULT '',
  updated TEXT, by TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS physio_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, stime TEXT DEFAULT '', done TEXT DEFAULT '[]',
  note TEXT DEFAULT '', by TEXT DEFAULT '', created TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ix_physio_sessions_day ON physio_sessions(day);
CREATE TABLE IF NOT EXISTS physio_retest (
  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, day TEXT NOT NULL, sts30 INTEGER, walk6 INTEGER,
  rom_l INTEGER, rom_r INTEGER, sls_l REAL, sls_r REAL, note TEXT DEFAULT '', by TEXT DEFAULT '', created TEXT);
"""


def physio_dir(member_dir):
    return os.path.join(member_dir, "physio")


def physios_of(member_dir):
    """{slug: {name}} -- every physio attached to this member (a folder with
    an info.json and an auth.db under <member>/physio/)."""
    out = {}
    base = physio_dir(member_dir)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for s in names:
        p = os.path.join(base, s)
        try:
            with open(os.path.join(p, "info.json"), encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            continue
        if os.path.exists(os.path.join(p, "auth.db")):
            out[s] = {"name": str(info.get("name") or s)[:40], "enabled": bool(info.get("enabled", True))}
    return out


class PhysioDB(object):
    """What family_auth.Auth needs: .con() on this physio's own auth.db."""

    def __init__(self, member_dir, slug):
        self.dir = os.path.join(physio_dir(member_dir), slug)
        self.path = os.path.join(self.dir, "auth.db")

    def con(self):
        import sqlite3
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir)
            os.chmod(self.dir, 0o700)
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        return c


def auth_for(member_dir, slug):
    return A.Auth(PhysioDB(member_dir, slug))


def install(app, M, gut, member_name):
    """Mount the physio pages on one member's GutLog app. `gut` is the
    owner's GutLog module (its db(), today(), now_hm()); `M` the member."""
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

    cookie_name = "fam_%s_physio" % M.slug
    base = urlparse(M.base)
    rp_id = base.hostname
    origin = "%s://%s" % (base.scheme, base.netloc)
    ser = URLSafeTimedSerializer(app.secret_key, salt="family-physio-" + M.slug)
    state = {}

    def ip():
        return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()

    def here(path=""):
        return (request.script_root or "") + "/physio" + path

    def con():
        c = gut.db()
        if not state.get("schema"):
            c.executescript(SCHEMA)
            c.commit()
            state["schema"] = True
        return c

    def physios():
        return physios_of(M.dir)

    # -- the physio's own cookie session --------------------------------
    def current():
        """(slug, name) of the signed-in physio, or (None, None)."""
        raw = request.cookies.get(cookie_name)
        if not raw:
            return None, None
        try:
            d = ser.loads(raw, max_age=SESSION_DAYS * 86400)
        except (BadSignature, SignatureExpired, ValueError):
            return None, None
        slug = str(d.get("p") or "")
        ps = physios()
        if slug not in ps or not ps[slug]["enabled"]:
            return None, None
        if d.get("ep") != auth_for(M.dir, slug).epoch():
            return None, None
        return slug, ps[slug]["name"]

    def start(resp, slug):
        tok = ser.dumps({"p": slug, "ep": auth_for(M.dir, slug).epoch(), "t": int(time.time())})
        resp.set_cookie(cookie_name, tok, max_age=SESSION_DAYS * 86400, path=here("/"), httponly=True,
                        secure=not M.insecure, samesite="Lax")
        return resp

    def end(resp):
        resp.delete_cookie(cookie_name, path=here("/"))
        return resp

    def need():
        slug, name = current()
        if not slug:
            return None, None, (jsonify(ok=False, err="Sign in first."), 401)
        return slug, name, None

    def page(t, body, script="", code=200, head=""):
        return Response(A.PAGE % {"title": _html.escape(t), "head": head, "body": body, "script": script},
                        status=code, mimetype="text/html")

    def pwa_head():
        r = _html.escape(here(), quote=True)
        return ("<link rel='manifest' href='%(r)s/manifest.webmanifest'>"
                "<link rel='icon' href='/icon-192.png'><link rel='apple-touch-icon' href='/icon-192.png'>"
                "<meta name='apple-mobile-web-app-capable' content='yes'>"
                "<meta name='mobile-web-app-capable' content='yes'>"
                "<meta name='apple-mobile-web-app-title' content='Physio'>"
                "<meta name='theme-color' content='#1f6f5c'>" % {"r": r})

    def title(slug):
        ps = physios()
        return "%s \u2014 physio for %s" % (ps.get(slug, {}).get("name", slug), member_name)

    def login_js():
        js = A.LOGIN_JS % {"slug": "%s_physio" % M.slug}
        return "const B=%s;\n" % json.dumps(here()) + js.replace("jpost('/passkey/", "jpost(B+'/passkey/")

    def pick_slug():
        """Which physio this page is for: ?p=, else the only one attached."""
        ps = physios()
        want = (request.args.get("p") or request.form.get("p") or "").strip()
        if want in ps:
            return want
        return sorted(ps)[0] if len(ps) == 1 else (sorted(ps)[0] if ps else None)

    def login_page(slug, err="", code=None):
        t = title(slug)
        ps = physios()
        if not ps.get(slug, {}).get("enabled", True):
            return page(t, "<h1>%s</h1><p class='err'>This sign-in is switched off.</p>" % _html.escape(t), "", 403)
        pk = ("<button type='button' class='ghost' id='pk' style='display:none'>Sign in with Face ID / Touch ID"
              "</button><p class='err' id='pkmsg'></p>")
        body = ("<h1>%s</h1><p class='mut'>Enter your 6-digit PIN.</p>"
                "<form method='post' action='%s' class='card' autocomplete='off'>"
                "<input type='hidden' name='p' value='%s'>"
                "<div class='pinrow'><input class='pin' id='pin' name='pin' type='password' inputmode='numeric' "
                "pattern='[0-9]*' maxlength='6' autocomplete='current-password' autofocus aria-label='PIN'>"
                "<button type='button' class='eye' id='eye' aria-label='Show or hide the PIN' "
                "aria-pressed='false'>Show</button></div>"
                "%s<button type='submit' id='go'>Sign in</button></form>%s"
                "<p class='mut'>This page opens only the physio programme, sessions, pain and walking entries "
                "and the monthly re-test. Not %s? Ask %s for your own link.</p>"
                % (_html.escape(t), _html.escape(here("/login"), quote=True), _html.escape(slug),
                   ("<p class='err' id='err'>%s</p>" % _html.escape(err)) if err else "", pk,
                   _html.escape(ps.get(slug, {}).get("name", slug)), _html.escape(M.caretaker)))
        return page(t, body, login_js(), code or (200 if not err else 401), head=pwa_head())

    @app.route("/physio/", strict_slashes=False)
    def physio_home():
        slug, name = current()
        if not slug:
            return redirect(here("/login"))
        cfg = {"base": here(), "name": name, "slug": slug, "member": member_name, "sites": SITES, "days": DAYS,
               "retest": [list(f) for f in RETEST_FIELDS]}
        return Response(PHYSIO_PAGE.replace("__CFG__", json.dumps(cfg).replace("<", "\\u003c"))
                        .replace("__HEAD__", pwa_head()).replace("__TITLE__", _html.escape(title(slug))),
                        mimetype="text/html")

    @app.route("/physio/login", methods=["GET", "POST"])
    def physio_login():
        slug = pick_slug()
        if not slug:
            return page("Physio", "<h1>Physio</h1><p class='mut'>No physio is attached to this diary.</p>", "", 404)
        if request.method == "GET":
            if current()[0]:
                return redirect(here("/"))
            return login_page(slug)
        au = auth_for(M.dir, slug)
        pin = (request.form.get("pin") or "").strip()
        res, info = au.attempt(pin, APPNAME, ip())
        if res == "locked":
            return login_page(slug, A.paused_text(info, M.caretaker))
        if res != "ok":
            return login_page(slug, A.wrong_text(info))
        return start(redirect(here("/passkey/offer?next=%2F")), slug)

    @app.route("/physio/signout", methods=["POST"])
    def physio_signout():
        slug, _n = current()
        if slug:
            auth_for(M.dir, slug).log(APPNAME, ip(), "signed out")
        return end(redirect(here("/login")))

    @app.route("/physio/signout-all", methods=["POST"])
    def physio_signout_all():
        slug, _n = current()
        if not slug:
            return redirect(here("/login"))
        au = auth_for(M.dir, slug)
        au.rotate_epoch()
        au.log(APPNAME, ip(), "signed out on all devices")
        return end(redirect(here("/login")))

    @app.route("/physio/pin/change", methods=["POST"])
    def physio_pin_change():
        slug, _n, err = need()
        if err:
            return err
        d = request.get_json(silent=True) or {}
        old, new = str(d.get("old") or "").strip(), str(d.get("new") or "").strip()
        au = auth_for(M.dir, slug)
        res, info = au.attempt(old, APPNAME, ip())
        if res != "ok":
            return jsonify(ok=False, err="The current PIN is not right (%d %s left)." % (info, "try" if info == 1 else "tries")
                           if res == "wrong" else A.paused_text(info, M.caretaker)), 400
        if not (len(new) == 6 and new.isdigit()) or A.weak_pin(new):
            return jsonify(ok=False, err="Choose 6 digits that are not all the same or in a straight run."), 400
        au.set_pin(new)
        au.log(APPNAME, ip(), "PIN changed")
        return jsonify(ok=True)

    # -- passkeys (the family steps, under /physio/) ----------------------
    @app.route("/physio/passkey/offer")
    def physio_passkey_offer():
        slug, _n = current()
        if not slug:
            return redirect(here("/login"))
        nxt = here(family_care.safe_next(request.args.get("next")))
        body = ("<div id='offer' style='display:none'><h1>Sign in with Face ID next time?</h1>"
                "<p class='mut'>Instead of the PIN, this phone can use Face ID or Touch ID. The PIN keeps working.</p>"
                "<div class='card'><button id='yes'>Use Face ID / Touch ID</button>"
                "<button class='ghost' id='no'>Not now</button><p class='err' id='msg'></p></div></div>")
        js = A.OFFER_JS % {"slug": "%s_physio" % M.slug, "next": json.dumps(nxt)}
        js = "const B=%s;\n" % json.dumps(here()) + js.replace("jpost('/passkey/", "jpost(B+'/passkey/")
        return page("Face ID", body, js.replace("from the Caretaker page", "from the Me tab"))

    @app.route("/physio/passkey/register/begin", methods=["POST"])
    def physio_pk_reg_begin():
        slug, name, err = need()
        if err:
            return jsonify(ok=False, err="Sign in first."), 403
        au = auth_for(M.dir, slug)
        return jsonify(ok=True, challenge=au.challenge("reg"), rp={"id": rp_id, "name": "Family physio"},
                       user={"id": au.user_handle(), "name": slug, "displayName": name},
                       exclude=[p["cred_id"] for p in au.passkeys()])

    @app.route("/physio/passkey/register/finish", methods=["POST"])
    def physio_pk_reg_finish():
        slug, _n, err = need()
        if err:
            return jsonify(ok=False, err="Sign in first."), 403
        au = auth_for(M.dir, slug)
        d = request.get_json(silent=True) or {}
        if not au.take_challenge(d.get("challenge"), "reg"):
            return jsonify(ok=False, err="That took too long -- try again."), 400
        try:
            r = W.verify_registration(d, d.get("challenge"), origin, rp_id)
        except W.WebAuthnError as e:
            au.log(APPNAME, ip(), "Face ID set-up refused: %s" % e)
            return jsonify(ok=False, err="Face ID could not be set up (%s)." % e), 400
        ua = request.headers.get("User-Agent") or ""
        label = "iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Mac" if "Mac" in ua else "this device"
        au.add_passkey(r["cred_id"], r["key"], r["count"], label)
        au.log(APPNAME, ip(), "Face ID / Touch ID set up (%s)" % label)
        return jsonify(ok=True)

    @app.route("/physio/passkey/login/begin", methods=["POST"])
    def physio_pk_login_begin():
        slug = pick_slug()
        pks = auth_for(M.dir, slug).passkeys() if slug else []
        if not pks:
            return jsonify(ok=False, err="Face ID is not set up yet -- use your PIN."), 404
        return jsonify(ok=True, challenge=auth_for(M.dir, slug).challenge("login"), rpId=rp_id,
                       allow=[p["cred_id"] for p in pks])

    @app.route("/physio/passkey/login/finish", methods=["POST"])
    def physio_pk_login_finish():
        slug = pick_slug()
        if not slug:
            return jsonify(ok=False, err="No physio here."), 404
        au = auth_for(M.dir, slug)
        d = request.get_json(silent=True) or {}
        if not au.take_challenge(d.get("challenge"), "login"):
            return jsonify(ok=False, err="That took too long -- try again."), 400
        pk = next((p for p in au.passkeys() if hmac.compare_digest(p["cred_id"], str(d.get("id") or ""))), None)
        if not pk:
            au.log(APPNAME, ip(), "Face ID refused: unknown key")
            return jsonify(ok=False, err="This Face ID is not set up here."), 400
        try:
            count = W.verify_assertion(d, d.get("challenge"), origin, rp_id, json.loads(pk["key"]), pk["count"])
        except W.WebAuthnError as e:
            au.log(APPNAME, ip(), "Face ID refused: %s" % e)
            return jsonify(ok=False, err="Face ID sign-in failed."), 400
        au.touch_passkey(pk["cred_id"], count)
        au.cleared(APPNAME, ip(), "signed in (Face ID / Touch ID)")
        return start(jsonify(ok=True, next=here("/")), slug)

    @app.route("/physio/passkey/remove/<int:pid>", methods=["POST"])
    def physio_pk_remove(pid):
        slug, _n, err = need()
        if err:
            return err
        au = auth_for(M.dir, slug)
        au.remove_passkey(pid)
        au.log(APPNAME, ip(), "Face ID / Touch ID removed")
        return jsonify(ok=True)

    @app.route("/physio/manifest.webmanifest")
    def physio_manifest():
        root = "/physio/"
        j = {"id": root, "start_url": root, "scope": root, "name": "Physio \u2014 %s" % member_name,
             "short_name": "Physio", "display": "standalone", "background_color": "#f6f7f9",
             "theme_color": "#1f6f5c",
             "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                       {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
        return Response(json.dumps(j, indent=2), mimetype="application/manifest+json")

    # -- the data (physio-gated) -----------------------------------------
    def programme():
        r = con().execute("SELECT items, notes, updated, by FROM physio_programme ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return {"items": [], "notes": "", "updated": "", "by": ""}
        try:
            items = json.loads(r["items"] or "[]")
        except ValueError:
            items = []
        return {"items": items, "notes": r["notes"] or "", "updated": r["updated"] or "", "by": r["by"] or ""}

    def clean_items(v):
        out = []
        for i in (v or [])[:30]:
            if not isinstance(i, dict) or not str(i.get("name") or "").strip():
                continue
            def num(k, lo, hi):
                try:
                    x = int(float(i.get(k)))
                except (TypeError, ValueError):
                    return None
                return x if lo <= x <= hi else None
            out.append({"name": str(i["name"]).strip()[:80], "sets": num("sets", 0, 20), "reps": num("reps", 0, 200),
                        "hold_s": num("hold_s", 0, 600), "days": [d for d in (i.get("days") or []) if d in DAYS],
                        "note": str(i.get("note") or "").strip()[:200]})
        return out

    def sessions(days=28):
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        out = []
        for r in con().execute("SELECT id, day, stime, done, note, by FROM physio_sessions WHERE day>=? "
                               "ORDER BY day DESC", (since,)).fetchall():
            x = dict(r)
            try:
                x["done"] = json.loads(x["done"] or "[]")
            except ValueError:
                x["done"] = []
            out.append(x)
        return out

    def tick(day, done, by, note_):
        day = gut._valid_day(day) or gut.today()
        prog = programme()
        names = [i["name"] for i in prog["items"]]
        done = [d for d in (done or names) if d in names]
        con().execute("INSERT INTO physio_sessions(day, stime, done, note, by, created) VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(day) DO UPDATE SET stime=excluded.stime, done=excluded.done, note=excluded.note, "
                      "by=excluded.by", (day, gut.now_hm(), json.dumps(done), (note_ or "")[:200], by, gut.now_s()))
        con().commit()
        return day, done

    def untick(day):
        con().execute("DELETE FROM physio_sessions WHERE day=?", (day,))
        con().commit()

    def next_session(prog, tday):
        days = set()
        for i in prog["items"]:
            days.update(i.get("days") or [])
        if not days:
            return None
        d = date.fromisoformat(tday)
        for k in range(0, 8):
            x = d + timedelta(days=k)
            if DAYS[x.weekday()] in days:
                if k == 0:
                    continue
                return {"day": x.isoformat(), "weekday": DAYS[x.weekday()], "in_days": k}
        return None

    def today_planned(prog, tday):
        wd = DAYS[date.fromisoformat(tday).weekday()]
        return [i["name"] for i in prog["items"] if wd in (i.get("days") or [])] or [i["name"] for i in prog["items"]]

    def pain_rows(days=28):
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        return [dict(r) for r in con().execute(
            "SELECT id, day, jtime, site, score, walk_min, stiff_min, notes FROM joint_log WHERE day>=? "
            "ORDER BY day DESC, jtime DESC, id DESC", (since,)).fetchall()]

    def retests():
        return [dict(r) for r in con().execute("SELECT * FROM physio_retest ORDER BY month DESC, id DESC LIMIT 24").fetchall()]

    @app.route("/physio/j/me")
    def physio_me():
        slug, name, err = need()
        if err:
            return err
        au = auth_for(M.dir, slug)
        return jsonify(ok=True, slug=slug, name=name, member=member_name,
                       passkeys=[{"id": p["id"], "label": p["label"], "created": p["created"],
                                  "last_used": p["last_used"]} for p in au.passkeys()], log=au.entries(20))

    @app.route("/physio/j/programme", methods=["GET", "POST"])
    def physio_programme():
        slug, name, err = need()
        if err:
            return err
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            items = clean_items(d.get("items"))
            con().execute("INSERT INTO physio_programme(items, notes, updated, by) VALUES(?,?,?,?)",
                          (json.dumps(items), str(d.get("notes") or "")[:500], gut.now_s(), name))
            con().commit()
        p = programme()
        return jsonify(ok=True, programme=p, today=today_planned(p, gut.today()),
                       next=next_session(p, gut.today()))

    @app.route("/physio/j/sessions", methods=["GET", "POST"])
    def physio_sessions_route():
        slug, name, err = need()
        if err:
            return err
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            if d.get("undo"):
                untick(gut._valid_day(d.get("day")) or gut.today())
            else:
                tick(d.get("day"), d.get("done"), "physio (%s)" % name, d.get("note"))
        return jsonify(ok=True, sessions=sessions())

    @app.route("/physio/j/pain", methods=["GET", "POST"])
    def physio_pain():
        slug, name, err = need()
        if err:
            return err
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            site = d.get("site")
            if site not in SITES:
                return jsonify(ok=False, err="Pick the joint."), 400
            try:
                score = int(d.get("score"))
            except (TypeError, ValueError):
                return jsonify(ok=False, err="Pick a score from 0 to 10."), 400
            if not 0 <= score <= 10:
                return jsonify(ok=False, err="Pick a score from 0 to 10."), 400
            walk = d.get("walk_min")
            try:
                walk = int(walk) if walk not in (None, "") else None
            except (TypeError, ValueError):
                walk = None
            day = gut._valid_day(d.get("day")) or gut.today()
            con().execute("INSERT INTO joint_log(day, jtime, site, score, triggers, stiff_min, walk_min, notes, created) "
                          "VALUES(?,?,?,?,?,?,?,?,?)", (day, gut.now_hm(), site, score, "[]", None, walk,
                                                       ("physio (%s): " % name + str(d.get("notes") or ""))[:300],
                                                       gut.now_s()))
            con().commit()
        return jsonify(ok=True, rows=pain_rows(), sites=SITES)

    @app.route("/physio/j/retest", methods=["GET", "POST"])
    def physio_retest():
        slug, name, err = need()
        if err:
            return err
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            vals = {}
            for key, label, unit, lo, hi in RETEST_FIELDS:
                v = d.get(key)
                if v in (None, ""):
                    vals[key] = None
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return jsonify(ok=False, err="%s must be a number." % label), 400
                if not lo <= v <= hi:
                    return jsonify(ok=False, err="%s: that does not look right." % label), 400
                vals[key] = int(v) if key not in ("sls_l", "sls_r") else round(v, 1)
            if all(v is None for v in vals.values()):
                return jsonify(ok=False, err="Fill in at least one measurement."), 400
            day = gut._valid_day(d.get("day")) or gut.today()
            con().execute("INSERT INTO physio_retest(month, day, sts30, walk6, rom_l, rom_r, sls_l, sls_r, note, by, "
                          "created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (day[:7], day, vals["sts30"], vals["walk6"], vals["rom_l"], vals["rom_r"], vals["sls_l"],
                           vals["sls_r"], str(d.get("note") or "")[:300], name, gut.now_s()))
            con().commit()
        return jsonify(ok=True, rows=retests(), fields=[list(f) for f in RETEST_FIELDS])

    # -- the member's own tile (GutLog session; caretaker sees it too) ---
    @app.route("/api/physio/me")
    @gut.login_required
    def api_physio_me():
        p = programme()
        tday = gut.today()
        ps = physios()
        row = con().execute("SELECT done, by FROM physio_sessions WHERE day=?", (tday,)).fetchone()
        return jsonify(ok=True, on=bool(ps), physio=[v["name"] for v in ps.values()], programme=p,
                       today=today_planned(p, tday), done_today=json.loads(row["done"]) if row else None,
                       done_by=row["by"] if row else "", next=next_session(p, tday), sessions=sessions(14))

    @app.route("/api/physio/tick", methods=["POST"])
    @gut.login_required
    def api_physio_tick():
        d = request.get_json(silent=True) or {}
        if d.get("undo"):
            untick(gut.today())
            return jsonify(ok=True, done=None)
        day, done = tick(gut.today(), d.get("done"), "member", d.get("note"))
        return jsonify(ok=True, day=day, done=done)

    return {"current": current, "cookie": cookie_name}


PHYSIO_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Physio</title>__HEAD__
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--bad:#a4262c}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:36rem;margin:0 auto;padding:12px 14px 40px;overflow-wrap:anywhere}h1{font-size:20px;margin:4px 0 10px}h2{font-size:18px;margin:0 0 6px}
.tabs,.chips{display:flex;flex-wrap:wrap;gap:6px}.tab,.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:7px 12px;font:inherit;font-size:15px;cursor:pointer}
.tab.sel,.chip.sel{background:var(--acc);color:#fff;border-color:var(--acc)}.chip.num{min-width:40px;text-align:center}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}
.mut{color:var(--mut);font-size:15px}.lbl{font-weight:600;margin:10px 0 4px;font-size:15px}.err{color:var(--bad)}
input,textarea{width:100%;min-width:0;font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
button.btn{font:inherit;font-weight:600;padding:10px 14px;border-radius:10px;border:0;background:var(--acc);color:#fff;margin:8px 6px 0 0}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc)}button.danger{background:transparent;color:var(--bad);border:1px solid var(--bad)}
.ex{border-top:1px solid var(--line);padding:8px 0}.ex:first-child{border-top:0}.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px}
.row{display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-top:1px solid var(--line)}.row:first-child{border-top:0}
</style></head><body><main>
<h1>__TITLE__</h1>
<div class="tabs" id="tabs"></div><div id="view"></div>
<div id="toast" class="mut" style="position:fixed;bottom:12px;left:12px;right:12px;text-align:center"></div>
</main>
<script>
/* FAMILY_EDITION_V1 -- the physio's page: programme, sessions, pain and walking, re-test, me. */
const CFG=__CFG__;const B=CFG.base;
const $=q=>document.querySelector(q);
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;}
function toast(m){const t=$('#toast');t.textContent=m;setTimeout(()=>{t.textContent='';},2400);}
async function jget(u){const r=await fetch(B+u,{credentials:'same-origin'});if(r.status===401){location.href=B+'/login';throw new Error('signed out');}return r.json();}
async function post(u,b){const r=await fetch(B+u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const j=await r.json().catch(()=>({}));if(!r.ok){throw new Error(j.err||'Save failed');}return j;}
const TABS=[['prog','Programme'],['sess','Sessions'],['pain','Pain & walking'],['retest','Re-test'],['me','Me']];
let ST={tab:'prog'};
function tabs(){const b=$('#tabs');b.innerHTML='';TABS.forEach(([k,l])=>{const x=el('button','tab'+(ST.tab===k?' sel':''),l);x.onclick=()=>{ST.tab=k;show();};b.appendChild(x);});}
function show(){tabs();({prog,sess,pain,retest,me})[ST.tab]();}
function exRow(box,i){const r=el('div','ex');const nm=el('input');nm.value=i.name||'';nm.placeholder='Exercise';nm.setAttribute('aria-label','Exercise');r.appendChild(nm);
  const g=el('div','g3');const s=el('input');s.type='number';s.inputMode='numeric';s.placeholder='sets';s.value=i.sets==null?'':i.sets;
  const p=el('input');p.type='number';p.inputMode='numeric';p.placeholder='reps';p.value=i.reps==null?'':i.reps;
  const h=el('input');h.type='number';h.inputMode='numeric';h.placeholder='hold s';h.value=i.hold_s==null?'':i.hold_s;
  [s,p,h].forEach(x=>g.appendChild(x));r.appendChild(g);
  const days=new Set(i.days||[]);const ch=el('div','chips');ch.style.marginTop='6px';
  CFG.days.forEach(d=>{const c=el('button','chip'+(days.has(d)?' sel':''),d);c.onclick=()=>{if(days.has(d))days.delete(d);else days.add(d);c.classList.toggle('sel',days.has(d));};ch.appendChild(c);});
  r.appendChild(ch);const nt=el('input');nt.placeholder='Note (optional)';nt.value=i.note||'';nt.style.marginTop='6px';r.appendChild(nt);
  const rm=el('button','btn ghost','Remove');rm.onclick=()=>{r.remove();r._gone=true;};r.appendChild(rm);
  r._get=()=>r._gone?null:({name:nm.value.trim(),sets:s.value,reps:p.value,hold_s:h.value,days:[...days],note:nt.value});box.appendChild(r);return r;}
async function prog(){
  const v=$('#view');v.innerHTML='';let j;try{j=await jget('/j/programme');}catch(e){return;}
  const p=j.programme,c=el('div','card');c.appendChild(el('h2','','Programme for '+CFG.member));
  c.appendChild(el('p','mut',p.updated?('Last changed '+p.updated.slice(0,16).replace('T',' ')+' by '+p.by):'No programme yet. Add the exercises below.'));
  const box=el('div');c.appendChild(box);const rows=(p.items||[]).map(i=>exRow(box,i));
  const ad=el('button','btn ghost','+ exercise');ad.onclick=()=>rows.push(exRow(box,{}));c.appendChild(ad);
  c.appendChild(el('p','lbl','Notes for the member'));const nt=el('textarea');nt.rows=2;nt.value=p.notes||'';c.appendChild(nt);
  const sv=el('button','btn','Save programme');sv.onclick=async()=>{try{await post('/j/programme',{items:rows.map(r=>r._get()).filter(Boolean),notes:nt.value});toast('Saved');prog();}catch(e){toast(e.message);}};
  c.appendChild(sv);v.appendChild(c);
  if(j.next)v.appendChild(el('p','mut','Next session: '+j.next.weekday+' '+j.next.day));
}
async function sess(){
  const v=$('#view');v.innerHTML='';let j,pj;try{j=await jget('/j/sessions');pj=await jget('/j/programme');}catch(e){return;}
  const c=el('div','card');c.appendChild(el('h2','','Sessions'));
  const today=new Date().toLocaleDateString('en-CA');const done=(j.sessions||[]).find(s=>s.day===today);
  if(done){c.appendChild(el('p','','Today: done ('+(done.done||[]).length+' exercise'+((done.done||[]).length===1?'':'s')+', by '+done.by+').'));
    const u=el('button','btn ghost','Undo today');u.onclick=async()=>{await post('/j/sessions',{undo:true,day:today});sess();};c.appendChild(u);}
  else{c.appendChild(el('p','','Today: '+(pj.today||[]).join(', ')||'nothing planned'));
    const t=el('button','btn','Tick today\u2019s session');t.id='tick';t.onclick=async()=>{try{await post('/j/sessions',{day:today,done:pj.today});toast('Ticked');sess();}catch(e){toast(e.message);}};c.appendChild(t);}
  v.appendChild(c);
  const h=el('div','card');h.appendChild(el('h2','','Last 28 days'));
  if(!(j.sessions||[]).length)h.appendChild(el('p','mut','No session ticked yet.'));
  (j.sessions||[]).forEach(s=>{const r=el('div','row');r.appendChild(el('span','',s.day));r.appendChild(el('span','mut',(s.done||[]).length+' done \u00b7 '+s.by));h.appendChild(r);});
  v.appendChild(h);
}
let PN={site:'',score:null};
async function pain(){
  const v=$('#view');v.innerHTML='';let j;try{j=await jget('/j/pain');}catch(e){return;}
  const c=el('div','card');c.appendChild(el('h2','','Pain and walking'));
  c.appendChild(el('p','lbl','Joint'));const sc=el('div','chips');CFG.sites.forEach(s=>{const b=el('button','chip'+(PN.site===s?' sel':''),s);b.onclick=()=>{PN.site=s;[...sc.children].forEach(x=>x.classList.toggle('sel',x===b));};sc.appendChild(b);});c.appendChild(sc);
  c.appendChild(el('p','lbl','Pain 0-10'));const ss=el('div','chips');for(let i=0;i<=10;i++){const b=el('button','chip num'+(PN.score===i?' sel':''),String(i));b.onclick=()=>{PN.score=i;[...ss.children].forEach(x=>x.classList.toggle('sel',x===b));};ss.appendChild(b);}c.appendChild(ss);
  c.appendChild(el('p','lbl','Could walk (minutes) before pain'));const w=el('input');w.type='number';w.inputMode='numeric';w.setAttribute('aria-label','Walking minutes');c.appendChild(w);
  c.appendChild(el('p','lbl','Note'));const n=el('input');n.setAttribute('aria-label','Note');c.appendChild(n);
  const sv=el('button','btn','Save');sv.id='painSave';sv.onclick=async()=>{try{await post('/j/pain',{site:PN.site,score:PN.score,walk_min:w.value,notes:n.value});toast('Saved');PN={site:'',score:null};pain();}catch(e){toast(e.message);}};c.appendChild(sv);v.appendChild(c);
  const h=el('div','card');h.appendChild(el('h2','','Last 28 days'));if(!(j.rows||[]).length)h.appendChild(el('p','mut','Nothing yet.'));
  (j.rows||[]).forEach(r=>{const x=el('div','row');x.appendChild(el('span','',r.day.slice(5)+' '+(r.jtime||'')+' '+r.site+' '+r.score+'/10'));x.appendChild(el('span','mut',r.walk_min!=null?('walked '+r.walk_min+' min'):''));h.appendChild(x);});
  v.appendChild(h);
}
async function retest(){
  const v=$('#view');v.innerHTML='';let j;try{j=await jget('/j/retest');}catch(e){return;}
  const c=el('div','card');c.appendChild(el('h2','','Monthly re-test'));const ins={};
  CFG.retest.forEach(([k,l,u])=>{c.appendChild(el('p','lbl',l+' ('+u+')'));const i=el('input');i.type='number';i.inputMode='decimal';i.step='0.1';i.setAttribute('aria-label',l);ins[k]=i;c.appendChild(i);});
  c.appendChild(el('p','lbl','Note'));const n=el('input');c.appendChild(n);
  const sv=el('button','btn','Save re-test');sv.id='retestSave';sv.onclick=async()=>{const b={note:n.value};Object.keys(ins).forEach(k=>{b[k]=ins[k].value;});try{await post('/j/retest',b);toast('Saved');retest();}catch(e){toast(e.message);}};c.appendChild(sv);v.appendChild(c);
  const h=el('div','card');h.appendChild(el('h2','','Results'));if(!(j.rows||[]).length)h.appendChild(el('p','mut','No re-test yet.'));
  (j.rows||[]).forEach(r=>{const x=el('div','row');x.appendChild(el('span','',r.day));
    x.appendChild(el('span','mut',CFG.retest.filter(([k])=>r[k]!=null).map(([k,l,u])=>l.split(' ')[0]+' '+r[k]+' '+u).join(' \u00b7 ')));h.appendChild(x);});
  v.appendChild(h);
}
async function me(){
  const v=$('#view');v.innerHTML='';let j;try{j=await jget('/j/me');}catch(e){return;}
  const c=el('div','card');c.appendChild(el('h2','',j.name));c.appendChild(el('p','mut','Physio for '+j.member+'. This sign-in opens the programme, sessions, pain and walking entries and the re-test, and nothing else of the record.'));v.appendChild(c);
  const p=el('div','card');p.appendChild(el('h2','','Face ID / Touch ID'));if(!(j.passkeys||[]).length)p.appendChild(el('p','mut','Not set up on any phone yet.'));
  (j.passkeys||[]).forEach(x=>{const r=el('p','',x.label+' \u00b7 since '+(x.created||''));const b=el('button','btn ghost','Remove');b.onclick=async()=>{await post('/passkey/remove/'+x.id);me();};r.appendChild(b);p.appendChild(r);});
  const setup=el('button','btn ghost','Set up on this phone');setup.onclick=()=>{try{localStorage.removeItem('fam_pk_'+CFG.slug);}catch(e){}location.href=B+'/passkey/offer?next=%2F';};p.appendChild(setup);v.appendChild(p);
  const pc=el('div','card');pc.appendChild(el('h2','','Change PIN'));const o=el('input');o.type='password';o.inputMode='numeric';o.maxLength=6;o.placeholder='Current PIN';pc.appendChild(o);
  const n=el('input');n.type='password';n.inputMode='numeric';n.maxLength=6;n.placeholder='New PIN (6 digits)';n.style.marginTop='6px';pc.appendChild(n);
  const cb=el('button','btn','Change');cb.onclick=async()=>{try{await post('/pin/change',{old:o.value,new:n.value});toast('PIN changed');o.value=n.value='';}catch(e){toast(e.message);}};pc.appendChild(cb);v.appendChild(pc);
  const lg=el('div','card');lg.appendChild(el('h2','','Recent sign-in attempts (IST)'));(j.log||[]).forEach(x=>lg.appendChild(el('p','mut',x.at+' \u00b7 '+x.result)));v.appendChild(lg);
  const so=el('div','card');const f1=el('form');f1.method='post';f1.action=B+'/signout';f1.appendChild(el('button','btn ghost','Sign out'));so.appendChild(f1);
  const f2=el('form');f2.method='post';f2.action=B+'/signout-all';f2.appendChild(el('button','btn danger','Sign out on all devices'));so.appendChild(f2);v.appendChild(so);
}
show();
</script></body></html>"""

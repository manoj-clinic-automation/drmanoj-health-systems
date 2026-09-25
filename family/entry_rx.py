#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entry_rx.py -- RxGuard for one family member.

FAMILY_EDITION_V1. Imports the owner's RxGuard code unchanged and builds a
member app with create_app(), pointed at the member's own database, the
member's own GutLog feed and the member's own sign-in ring. Differences for a
member:

  * the knowledge base is the family code tree's read-only copy (curated +
    the owner-approved overlay, which holds no personal data). Members never
    approve drafts or fetch sources -- the /kb write routes answer 403 and the
    sync is never spawned; new knowledge arrives with upgrade_all.sh;
  * the curated rules' "personal_relevance" notes are the owner's and are
    dropped, so "Why it applies here" never shows his reasons to someone else;
  * dose ceilings come from the member's own file, if one is ever made;
  * the caretaker layer, and the path prefix.

Python 3.9.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import family_env  # noqa: E402

M = family_env.Member()
_rdir = M.path("rxguard")
# The member's own dose ceilings if they are ever written; otherwise the
# generic label maxima for pain medicines (RxGuard v1.9.0, curated knowledge).
_own_rules = os.path.join(_rdir, "dose_rules.local.json")
_generic = os.path.join(family_env.code_dir("rx"), "knowledge", "dose_rules.generic.json")
_dose_rules = _own_rules if os.path.exists(_own_rules) or not os.path.exists(_generic) else _generic
M.apply_env(dict({
    "RXGUARD_DB": os.path.join(_rdir, "rxguard.db"),
    "RXGUARD_GUTLOG_FEED": "1",
    "GUTLOG_FEED_URL": M.local("gut"),
    "GUTLOG_FEED_TOKEN_FILE": M.path("feed.token"),
    "RXGUARD_DOSE_RULES": _dose_rules,
    "RXGUARD_KB_NOSPAWN": "1",
    "RXGUARD_KB_CACHE": os.path.join(_rdir, "cache"),
}, **M.sso_env()))
os.environ.pop("RXGUARD_SECRET", None)

sys.path.insert(0, family_env.code_dir("rx"))
import app as rx  # noqa: E402  -- the owner's RxGuard, unchanged
import family_care  # noqa: E402
import family_prefix  # noqa: E402
from flask import request, session, Response  # noqa: E402

for _r in list(rx._BASE_PAIRWISE) + list(rx.PAIRWISE) + list(rx.CONDITION_RULES):
    if isinstance(_r, dict):
        _r.pop("personal_relevance", None)

flask_app = rx.create_app()
flask_app.config.update(SESSION_COOKIE_NAME=M.cookie("rx"),
                        SESSION_COOKIE_PATH=M.prefixes["rx"] + "/",
                        SESSION_COOKIE_SECURE=not M.insecure,
                        SESSION_COOKIE_SAMESITE="Lax",
                        SESSION_COOKIE_HTTPONLY=True)

KB_WRITES = ("kb_accept_all", "kb_decide", "kb_pairs_decide", "kb_alerts_read", "kb_fetch")


@flask_app.before_request
def _kb_read_only():
    if request.endpoint in KB_WRITES:
        return Response("Knowledge updates come from the owner's copy in the next upgrade.",
                        status=403, mimetype="text/plain")
    return None


# Conditions and age are entered once, on the member's GutLog first-run form.
# RxGuard reads them from that GutLog's feed (the member's own, bearer-gated)
# at most every two minutes, and mirrors only the codes both apps know; a code
# RxGuard alone has (QTc, ectopy ...) is left as ticked on /profile.
import json as _json  # noqa: E402
import time as _time  # noqa: E402
import urllib.request as _ur  # noqa: E402
_SYNC = {"at": 0.0}
_SYNC_CODES = set(c for c, _l in family_env.MEMBER_CONDITIONS) & set(c for c, _l in rx.CONDITIONS)


def sync_profile(force=False):
    if not force and _time.time() - _SYNC["at"] < 120:
        return False
    _SYNC["at"] = _time.time()
    try:
        with open(M.path("feed.token")) as fh:
            tok = fh.read().strip()
        op = _ur.build_opener(_ur.ProxyHandler({}))
        req = _ur.Request(M.local("gut") + "/api/feed/profile", headers={"Authorization": "Bearer " + tok})
        with op.open(req, timeout=3) as r:
            j = _json.loads(r.read().decode("utf-8"))
    except Exception:
        return False
    if not j.get("ok"):
        return False
    have = set(j.get("conditions") or [])
    db = rx.get_db()
    for code in _SYNC_CODES:
        db.execute("UPDATE conditions SET active=? WHERE code=?", (1 if code in have else 0, code))
    db.commit()
    if j.get("age"):
        rx.set_profile("age", str(int(j["age"])))
    return True


@flask_app.before_request
def _sync_profile():
    if request.method == "GET" and request.endpoint not in ("healthz", "api_feed_status", "api_feed_dose",
                                                           "static"):
        sync_profile()
    elif request.endpoint in ("api_feed_status", "api_feed_dose"):
        sync_profile()
    return None


def _stamp(s):
    s["auth"] = True
    s["epoch"] = rx.setting("auth_epoch", "1")


LABELS = {"meds": "Changed the medicine list", "med_event": "Recorded a medicine change",
          "profile": "Changed the profile / conditions", "episode": "Recorded an episode",
          "adverse": "Recorded an adverse effect", "consultations": "Recorded a consultation",
          "reconcile_apply": "Reconciled with GutLog", "add_override": "Added an override",
          "dose_page": "Changed a dose ceiling", "reviews": "Recorded a review",
          "analyse_view": "Checked a proposed change", "symptom_view": "Checked a symptom"}

CARE = family_care.Care(M.slug, M.dir, M.base, M.prefixes)
import family_auth  # noqa: E402

family_care.install(flask_app, CARE, "rx", _stamp,
                    what_for=lambda ep, p, m: LABELS.get(ep) or ep.replace("_", " "),
                    blocked=("login",) + family_auth.CARETAKER_BLOCKED, health_sso=rx.health_sso)


def _rx_stamp(s):
    _stamp(s)
    s.pop(rx.health_sso.HOLD, None)


family_auth.install(flask_app, "rx", M, CARE, _rx_stamp, lambda s: bool(s.get("auth")))

application = family_prefix.PrefixApp(
    flask_app.wsgi_app, M.prefixes["rx"],
    family_prefix.route_segments(flask_app.url_map),
    host_map=M.host_map(), manifest_label=M.name, manifest_name=family_auth.title_for("rx", M.name))

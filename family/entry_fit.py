#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entry_fit.py -- FitLog for one family member.

FAMILY_EDITION_V1. Imports the owner's FitLog code unchanged, pointed at the
member's own database, ingest tokens (ingest.env in the member folder, never
the owner's), the member's GutLog feed and the member's sign-in ring. FitLog
reads FITLOG_DB at import, so the environment is set first. The session key is
the member's own file -- never the path-derived fallback, which FitLog itself
refuses to vouch with. The owner's pain sites become generic ones, and his
"on his legs" wording becomes "on your legs".

Python 3.9.
"""
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import family_env  # noqa: E402

M = family_env.Member()
_fdir = M.path("fitlog")


def _member_secret():
    p = os.path.join(_fdir, "secret")
    try:
        with open(p) as fh:
            s = fh.read().strip()
        if len(s) >= 32:
            return s
    except OSError:
        pass
    s = secrets.token_hex(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(s)
    return s


M.apply_env(dict({
    "FITLOG_DB": os.path.join(_fdir, "fitlog.db"),
    "FITLOG_INGEST_ENV": os.path.join(_fdir, "ingest.env"),
    "FITLOG_GUTLOG_FEED": "1",
    "GUTLOG_FEED_URL": M.local("gut"),
    "GUTLOG_FEED_TOKEN_FILE": M.path("feed.token"),
    "FITLOG_SECRET": _member_secret(),
    # FitLog v1.8.0 -- the joint profile gets the knee- and ankle-sparing programme.
    "FITLOG_PROGRAMME": "joint" if M.profile == "joint" else "",
}, **M.sso_env()))
for _k in ("FITLOG_INGEST_TOKEN", "FITLOG_HC_TOKEN"):
    os.environ.pop(_k, None)

sys.path.insert(0, family_env.code_dir("fit"))
import app as fit  # noqa: E402  -- the owner's FitLog, unchanged
import family_care  # noqa: E402
import family_prefix  # noqa: E402
from flask import request  # noqa: E402

flask_app = fit.app
flask_app.config.update(SESSION_COOKIE_NAME=M.cookie("fit"),
                        SESSION_COOKIE_PATH=M.prefixes["fit"] + "/",
                        SESSION_COOKIE_SECURE=not M.insecure,
                        SESSION_COOKIE_SAMESITE="Lax")
fit.PAIN_SITES[:] = ["Knee L", "Knee R", "Ankle L", "Ankle R", "Hip", "Lower back",
                     "Neck / shoulder", "Other"]

WORDING = (("h on his legs", "h on your legs"), ("his legs, not exercise", "your legs, not exercise"))


@flask_app.after_request
def _wording(resp):
    try:
        if (resp.mimetype or "") == "text/html" and not resp.direct_passthrough:
            t = resp.get_data(as_text=True)
            t2 = t
            for a, b in WORDING:
                t2 = t2.replace(a, b)
            if t2 != t:
                resp.set_data(t2)
    except Exception:
        pass
    return resp


def _stamp(s):
    s["auth"] = True


LABELS = {"checkin": "Did the daily check-in", "session_close": "Closed a session",
          "events": "Added an event", "events_quick": "Added an event",
          "events_del": "Deleted an event", "meds": "Logged a painkiller",
          "meds_manage": "Changed the medicine list", "meds_toggle": "Switched a medicine",
          "tests": "Recorded a capacity test", "epochs": "Recorded a medicine period"}

CARE = family_care.Care(M.slug, M.dir, M.base, M.prefixes)
import family_auth  # noqa: E402

family_care.install(flask_app, CARE, "fit", _stamp,
                    what_for=lambda ep, p, m: LABELS.get(ep) or ep.replace("_", " "),
                    blocked=("setup", "login") + family_auth.CARETAKER_BLOCKED, health_sso=fit.health_sso,
                    write_gets=("events_del", "meds_toggle"))


def _fit_stamp(s):
    s["auth"] = True
    s.pop(fit.health_sso.HOLD, None)


family_auth.install(flask_app, "fit", M, CARE, _fit_stamp, lambda s: bool(s.get("auth")))

application = family_prefix.PrefixApp(
    flask_app.wsgi_app, M.prefixes["fit"],
    family_prefix.route_segments(flask_app.url_map),
    host_map=M.host_map(), manifest_label=M.name, manifest_name=family_auth.title_for("fit", M.name))

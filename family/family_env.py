#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_env.py -- one member's settings, read once, shared by the three entries.

FAMILY_EDITION_V1. The systemd unit hands a member process a handful of
variables (from /etc/family/<slug>.env, root-only). Everything else -- every
database path, token file and companion URL the apps already know how to
read -- is DERIVED here, so the three apps of one member can never disagree
about where that member's files are, and nothing can default to the owner's
/root paths.

    FAMILY_SLUG      m1                    (a neutral slug, never a name)
    FAMILY_DIR       /srv/family/m1        (owned by the member's own user)
    FAMILY_BASE      https://family.dr-manoj.in
    FAMILY_NAME      display name          (server-side only)
    FAMILY_PROFILE   gut | joint | general
    FAMILY_PORT_GUT / _RX / _FIT           loopback ports

Python 3.9.
"""
import os
import re

MARKER = "FAMILY_EDITION_V1"
PROFILES = ("gut", "joint", "general")
SLUG_RX = re.compile(r"^m[0-9]{1,3}$")
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Member(object):
    def __init__(self, env=None):
        e = os.environ if env is None else env
        self.slug = e.get("FAMILY_SLUG", "")
        if not SLUG_RX.match(self.slug):
            raise SystemExit("family: FAMILY_SLUG must look like m1, got %r" % self.slug)
        self.dir = os.path.abspath(e.get("FAMILY_DIR", ""))
        if not e.get("FAMILY_DIR") or os.path.basename(self.dir) != self.slug:
            raise SystemExit("family: FAMILY_DIR must end in the slug")
        if self.dir.startswith("/root"):
            raise SystemExit("family: a member folder may not live under /root")
        self.base = (e.get("FAMILY_BASE") or "https://family.dr-manoj.in").rstrip("/")
        self.name = (e.get("FAMILY_NAME") or self.slug).strip()[:40]
        self.profile = e.get("FAMILY_PROFILE", "general")
        if self.profile not in PROFILES:
            self.profile = "general"
        self.ports = {"gut": int(e.get("FAMILY_PORT_GUT", "0") or 0),
                      "rx": int(e.get("FAMILY_PORT_RX", "0") or 0),
                      "fit": int(e.get("FAMILY_PORT_FIT", "0") or 0)}
        self.prefixes = {"gut": "/" + self.slug, "rx": "/" + self.slug + "/rx",
                         "fit": "/" + self.slug + "/fit"}
        self.insecure = e.get("FAMILY_INSECURE") == "1"

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def url(self, app):
        return self.base + self.prefixes[app]

    def local(self, app):
        """Loopback address of one of this member's apps, prefix included."""
        return "http://127.0.0.1:%d%s" % (self.ports[app], self.prefixes[app])

    def host_map(self):
        return {"https://health.dr-manoj.in": self.url("gut"),
                "https://rx.dr-manoj.in": self.url("rx"),
                "https://fit.dr-manoj.in": self.url("fit")}

    def sso_env(self):
        return {"HEALTH_SSO_KEY_FILE": self.path("sso.key"),
                "HEALTH_SSO_APPS": "gutlog=%s,rxguard=%s,fitlog=%s" % (
                    self.url("gut"), self.url("rx"), self.url("fit"))}

    def cookie(self, app):
        return "fam_%s_%s" % (self.slug, app)

    def apply_env(self, values):
        """Force, not default: a member process must never inherit an owner
        path from a stray variable."""
        for k, v in values.items():
            os.environ[k] = v


# The conditions checklist of the first-run form (GutLog), code and label.
# Codes are shared with RxGuard where it has one, and RxGuard mirrors those
# from the member's own GutLog feed -- conditions are entered once.
MEMBER_CONDITIONS = [
    ("ibs", "Irritable bowel (IBS)"), ("knee_oa", "Knee arthritis"),
    ("ankle_arthritis", "Ankle arthritis"), ("hip_oa", "Hip arthritis"),
    ("back_pain", "Long-standing back pain"), ("hypertension", "High blood pressure"),
    ("diabetes", "Diabetes / raised sugar"), ("high_cholesterol", "High cholesterol"),
    ("coronary_disease", "Heart disease"), ("renal_impairment", "Kidney disease"),
    ("hepatic_impairment", "Liver disease"), ("peptic_ulcer", "Stomach ulcer / GI bleed"),
    ("hyponatraemia", "Low sodium (recent or recurrent)"), ("thyroid", "Thyroid disease"),
    ("osteoporosis", "Osteoporosis"), ("constipation", "Constipation tendency"),
    ("seizure_history", "Seizures in the past"),
]


def code_dir(app):
    return os.path.join(CODE_ROOT, {"gut": "gutlog", "rx": "rxguard", "fit": "fitlog"}[app])

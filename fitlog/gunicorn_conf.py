"""
Gunicorn configuration for FitLog.

Why this file exists
--------------------
FitLog ran with no access logging, so there was no way to tell an
external request that never arrived from one the app rejected. The
OpenLiteSpeed vhost log answered that question, but it is a shared
CyberPanel log and it records the full query string -- which for the
healthconnect feed means the ?k=<token> secret lands in plain text on
disk (see /home/fit.dr-manoj.in/logs/fit.dr-manoj.in.access_log).

This config turns gunicorn's own access log on and routes every line
through a redacting logger first, so the app-side log keeps source=
(the thing you actually need when diagnosing a feed) while never
persisting a credential.

Target: Python 3.9 -- no PEP 701 f-strings, no match, no X | Y unions.
"""

import re

from gunicorn.glogging import Logger

# --- logging destinations -------------------------------------------------
# LogsDirectory=fitlog in the unit file creates and owns /var/log/fitlog.
accesslog = "/var/log/fitlog/access.log"
errorlog = "-"          # stays on journald, as before
loglevel = "info"

# %(h)s is always 127.0.0.1 behind the OLS reverse proxy, so the real
# client comes from the forwarded header.
access_log_format = (
    '%(h)s fwd=%({x-forwarded-for}i)s %(t)s "%(r)s" %(s)s %(b)s '
    '%(L)ss "%(a)s"'
)

# --- redaction ------------------------------------------------------------
# Matches ?k=... / &token=... etc. anywhere in a URI or query string.
_SECRET_QS = re.compile(r"(?i)([?&](?:k|key|token|access_token|secret)=)[^&\s]*")

_REPLACEMENT = r"\1<redacted>"


def _redact(value):
    if not value:
        return value
    return _SECRET_QS.sub(_REPLACEMENT, str(value))


class RedactingLogger(Logger):
    """Access logger that strips URL-borne credentials before writing."""

    def atoms(self, resp, req, environ, request_time):
        data = Logger.atoms(self, resp, req, environ, request_time)
        # 'r' is the full request line, 'q' the bare query string.
        for key in ("r", "q"):
            if key in data:
                data[key] = _redact(data[key])
        # Defensive: never let a header atom carry a bearer token, even if
        # someone later adds %({authorization}i)s to the format above.
        if "{authorization}i" in data:
            data["{authorization}i"] = "<redacted>"
        return data


logger_class = RedactingLogger

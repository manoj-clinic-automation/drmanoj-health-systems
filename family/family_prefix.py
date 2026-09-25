#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_prefix.py -- run one unmodified app under a path prefix.

FAMILY_EDITION_V1. The Family Edition serves every member from ONE host,
family.dr-manoj.in, at a path per member and app:

    /m1/        GutLog of member m1
    /m1/rx/     RxGuard of member m1
    /m1/fit/    FitLog of member m1

The three apps were written for a host of their own: GutLog alone carries
~300 absolute paths ('/api/now', href="/", redirect("/login") ...). Editing
them by hand in a 12,000-line live file is exactly the kind of change that
breaks the owner's app. So the apps stay as they are and this wrapper does
four things, and only in a family process:

  1. REQUEST. A path that does not start with this process's prefix gets a
     404 -- the m1 process never answers for /m2/ or for anything else. A
     path that does is split into SCRIPT_NAME (the prefix) and PATH_INFO
     (the rest), so the app routes as if it were at the root and url_for()
     already writes the prefix in.

  2. HEADERS. A Location header that is a bare path gets the prefix; one
     that names the owner's hosts is mapped to this member's own address.
     Service-Worker-Allowed is narrowed to the prefix.

  3. BODIES. HTML, JavaScript and JSON are rewritten: a quoted absolute path
     whose FIRST SEGMENT is one of the app's own routes ('/api/..', '/login',
     '/sw.js' -- the list is read from the app's url_map at start, not typed
     here) gets the prefix, as do href="/" and location='/' forms and a
     service worker's scope '/'. The owner's hostnames are mapped to this
     member's apps. A path the app does not route is left alone, which is
     why a date like 25/09 or a split('/') is never touched.

  4. MANIFEST. The PWA manifest's id / start_url / scope / icons get the
     prefix and the name gets the member's display name, so each member's
     copy installs as its own home-screen app with its own scope.

What it does not do: authenticate anyone, or know anything about a member
beyond the prefix and the display name it is given. Isolation between
members is enforced by the operating system (one Linux user per member,
systemd sandboxing) and by each app's own secret -- this layer only keeps
the browser inside the right path.

Python 3.9.
"""
import json
import re

MARKER = "FAMILY_EDITION_V1"

REWRITE_TYPES = ("text/html", "application/javascript", "text/javascript",
                 "application/json", "application/manifest+json", "text/css")

# Owner hosts, mapped per process to this member's own apps.
OWNER_HOSTS = ("https://health.dr-manoj.in", "https://rx.dr-manoj.in",
               "https://fit.dr-manoj.in")


def route_segments(url_map):
    """First literal path segment of every route: 'api', 'login', 'sw.js'.
    A route that starts with a variable contributes nothing."""
    segs = set()
    for rule in url_map.iter_rules():
        r = rule.rule
        if not r.startswith("/") or len(r) < 2:
            continue
        first = r[1:].split("/", 1)[0]
        if not first or "<" in first:
            continue
        segs.add(first)
    return sorted(segs, key=len, reverse=True)


class PrefixApp(object):
    def __init__(self, wsgi_app, prefix, segments, host_map=None,
                 manifest_label=None):
        p = "/" + prefix.strip("/")
        if p == "/":
            raise ValueError("a family prefix cannot be the root")
        self.app = wsgi_app
        self.prefix = p
        self.segments = list(segments)
        self.host_map = list((host_map or {}).items())
        # Longest source first, so a host is never mapped by a shorter one.
        self.host_map.sort(key=lambda kv: len(kv[0]), reverse=True)
        self.manifest_label = manifest_label
        alt = "|".join(re.escape(s) for s in self.segments) or "(?!)"
        # A quoted/attribute/space-led absolute path whose first segment is
        # one of ours, followed by the end of that segment.
        self._seg_rx = re.compile(
            r"""(?<=[\"'`(=,\s])/(?:%s)(?=[/\"'`?#)\s&;,]|$)""" % alt)
        self._root_attr = re.compile(
            r"""((?:href|action|src|formaction)\s*=\s*[\"'])/(?=[\"'?#])""")
        self._root_js = re.compile(
            r"""((?:location(?:\.href)?\s*=\s*|location\.(?:replace|assign)\(\s*)[\"'])/(?=[\"'?#])""")
        self._sw_scope = re.compile(r"""(scope\s*:\s*[\"'])/([\"'])""")

    # ------------------------------------------------------------ helpers
    def _inside(self, path):
        p = self.prefix
        return path == p or path.startswith(p + "/") or path.startswith(p + "?")

    def map_url(self, u):
        for src, dst in self.host_map:
            if u == src or u.startswith(src + "/") or u.startswith(src + "?"):
                return dst + u[len(src):]
        if u.startswith("/") and not u.startswith("//") and not self._inside(u):
            return self.prefix + u
        return u

    def rewrite_text(self, text):
        for src, dst in self.host_map:
            text = text.replace(src, dst)
        p = self.prefix
        text = self._seg_rx.sub(lambda m: p + m.group(0), text)
        text = self._root_attr.sub(lambda m: m.group(1) + p + "/", text)
        text = self._root_js.sub(lambda m: m.group(1) + p + "/", text)
        text = self._sw_scope.sub(lambda m: m.group(1) + p + "/" + m.group(2), text)
        return text

    def rewrite_manifest(self, text):
        try:
            j = json.loads(text)
        except ValueError:
            return self.rewrite_text(text)

        def walk(o):
            if isinstance(o, dict):
                out = {}
                for k, v in o.items():
                    if isinstance(v, str) and k in ("id", "start_url", "scope", "src", "url") \
                            and v.startswith("/") and not v.startswith("//"):
                        out[k] = self.prefix + v
                    else:
                        out[k] = walk(v)
                return out
            if isinstance(o, list):
                return [walk(x) for x in o]
            return o
        j = walk(j)
        if self.manifest_label:
            for k in ("name", "short_name"):
                if isinstance(j.get(k), str):
                    j[k] = (j[k] + " · " + self.manifest_label) if k == "name" \
                        else self.manifest_label[:12]
        return json.dumps(j, indent=2)

    # ------------------------------------------------------------ WSGI
    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        p = self.prefix
        if path == p:
            q = environ.get("QUERY_STRING", "")
            start_response("301 Moved Permanently",
                           [("Location", p + "/" + ("?" + q if q else "")),
                            ("Content-Length", "0")])
            return [b""]
        if not path.startswith(p + "/"):
            body = b"Not found"
            start_response("404 Not Found", [("Content-Type", "text/plain"),
                                             ("Content-Length", str(len(body)))])
            return [body]
        environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "").rstrip("/") + p
        environ["PATH_INFO"] = path[len(p):]

        cap = {}

        def sr(status, headers, exc_info=None):
            cap["status"], cap["headers"], cap["exc"] = status, headers, exc_info
            return lambda data: None

        it = self.app(environ, sr)
        status, headers = cap["status"], list(cap["headers"])
        ctype = ""
        out_h = []
        for k, v in headers:
            lk = k.lower()
            if lk == "location":
                v = self.map_url(v)
            elif lk == "service-worker-allowed" and v == "/":
                v = p + "/"
            elif lk == "content-type":
                ctype = v.lower()
            out_h.append((k, v))
        base_type = ctype.split(";", 1)[0].strip()
        if base_type not in REWRITE_TYPES:
            start_response(status, out_h, cap["exc"])
            return it
        try:
            body = b"".join(it)
        finally:
            if hasattr(it, "close"):
                it.close()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            start_response(status, out_h, cap["exc"])
            return [body]
        if base_type == "application/manifest+json":
            text = self.rewrite_manifest(text)
        else:
            text = self.rewrite_text(text)
        body = text.encode("utf-8")
        out_h = [(k, v) for k, v in out_h if k.lower() != "content-length"]
        out_h.append(("Content-Length", str(len(body))))
        start_response(status, out_h, cap["exc"])
        return [body]

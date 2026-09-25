#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
famtest.py -- the scratch Family Edition the suites run against.

FAMILY_EDITION_V1. Builds a code tree from the repository's apps with the
SAME allowlist the server uses (build_code.py), stamps scratch members with
the SAME tool (stamp_member.py --no-system), runs every member app from that
tree on loopback ports, runs the owner's GutLog (the app.py under test) on a
scratch database, and puts a small front proxy before the members that routes
by path exactly as the OpenLiteSpeed contexts do. Nothing here touches a live
database or a /root path. Python 3.9.
"""
import http.client
import http.cookiejar
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)


def fam_src():
    return os.path.abspath(os.environ.get("FAMILY_CODE") or TESTS_DIR)


def app_src(app):
    """The folder an app's code is taken from for the member tree. A negative
    control points GUT_SRC / RX_SRC / FIT_SRC at a broken COPY."""
    env = {"gut": "GUT_SRC", "rx": "RX_SRC", "fit": "FIT_SRC"}[app]
    d = {"gut": "gutlog", "rx": "rxguard", "fit": "fitlog"}[app]
    return os.path.abspath(os.environ.get(env) or os.path.join(REPO, d))


RES = []


def check(name, cond, detail=""):
    RES.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else "  -- " + str(detail)[:300]),
          flush=True)
    return bool(cond)


def finish():
    bad = [n for n, ok in RES if not ok]
    print("")
    print("%d checks, %d failed" % (len(RES), len(bad)))
    print("RESULT: " + ("ALL PASS" if not bad else "FAIL"))
    return 0 if not bad else 1


def free_base():
    for base in range(18200, 60000, 1000):
        ok = True
        for off in range(0, 60):
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", base + off))
            except OSError:
                ok = False
            finally:
                s.close()
            if not ok:
                break
        if ok:
            return base
    raise SystemExit("no free ports")


def wait_port(port, path, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            c.request("GET", path)
            r = c.getresponse()
            r.read()
            if r.status < 500:
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client(object):
    """A browser-ish client: its own cookie jar, redirects followed by hand
    so each hop is visible."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                              NoRedirect(), urllib.request.ProxyHandler({}))

    def url(self, path):
        return path if path.startswith("http") else self.base + path

    def req(self, method, path, data=None, headers=None, follow=False, ctype=None):
        url = self.url(path)
        hops = 0
        while True:
            h = {"Accept": "text/html,application/json"}
            h.update(headers or {})
            body = None
            if data is not None:
                if ctype == "json":
                    body = json.dumps(data).encode()
                    h["Content-Type"] = "application/json"
                else:
                    body = urllib.parse.urlencode(data, doseq=True).encode()
                    h["Content-Type"] = "application/x-www-form-urlencoded"
            r = urllib.request.Request(url, data=body, method=method, headers=h)
            try:
                resp = self.op.open(r, timeout=15)
                st, hd, bd = resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as e:
                st, hd, bd = e.code, dict(e.headers), e.read()
            loc = hd.get("Location")
            if follow and st in (301, 302, 303, 307, 308) and loc and hops < 10:
                url = urllib.parse.urljoin(url, loc)
                method, data, hops = "GET", None, hops + 1
                continue
            return Resp(st, hd, bd, url)

    def get(self, path, **k):
        return self.req("GET", path, **k)

    def post(self, path, data, **k):
        return self.req("POST", path, data=data, **k)

    def cookies(self):
        return [(c.name, c.value, c.path) for c in self.jar]


class Resp(object):
    def __init__(self, status, headers, body, url):
        self.status, self.headers, self.body, self.url = status, headers, body, url
        self.text = body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.text)
        except ValueError:
            return None


class Front(object):
    """The OLS contexts in miniature: longest prefix wins, anything else 404."""

    def __init__(self, port, routes):
        self.routes = sorted(routes.items(), key=lambda kv: len(kv[0]), reverse=True)
        front = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                path = self.path
                target = None
                for pre, port_ in front.routes:
                    if path == pre.rstrip("/") or path.startswith(pre):
                        target = port_
                        break
                if target is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else None
                hdrs = dict((k, v) for k, v in self.headers.items()
                            if k.lower() not in ("host", "connection", "content-length"))
                hdrs["X-Forwarded-For"] = "127.0.0.1"
                c = http.client.HTTPConnection("127.0.0.1", target, timeout=30)
                c.request(self.command, path, body=body, headers=hdrs)
                r = c.getresponse()
                data = r.read()
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _go

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


OWNER_SERVE = r'''
import importlib.util, os, sys
app_path = sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
spec = importlib.util.spec_from_file_location("gutlog_owner", app_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from werkzeug.serving import make_server
make_server("127.0.0.1", int(sys.argv[2]), m.app, threaded=True).serve_forever()
'''


class Rig(object):
    """One scratch Family Edition: code tree, owner app, members, front."""

    def __init__(self, owner_app, members=(("m1", "Member A", "gut"), ("m2", "Member B", "joint")),
                 extra_member_env=None, host="127.0.0.1"):
        # host: "localhost" for browser passkey tests (WebAuthn refuses an IP as its RP ID).
        # Under a negative control that broke a COPY of GutLog, the owner's GutLog
        # runs from that copy as well, or an owner-side assertion could never fail.
        self.owner_app = os.path.join(app_src("gut"), "app.py") if os.environ.get("GUT_SRC") \
            else os.path.abspath(owner_app)
        self.work = tempfile.mkdtemp(prefix="famtest_")
        self.root = os.path.join(self.work, "root")
        self.code = os.path.join(self.work, "code")
        self.base = free_base()
        self.front_port = self.base + 1
        self.owner_port = self.base + 2
        self.port_base = self.base          # m1 -> base+11.., m2 -> base+21..
        self.procs = []
        self.pw = {}
        self.members = members
        self.extra_member_env = extra_member_env or {}
        sys.path.insert(0, fam_src())
        import build_code  # noqa
        build_code.build(app_src("gut"), app_src("rx"), app_src("fit"), fam_src(), self.code)
        self.front_url = "http://%s:%d" % (host, self.front_port)
        # -- owner ----------------------------------------------------------
        od = os.path.join(self.work, "owner")
        os.makedirs(od)
        with open(os.path.join(od, "sso.key"), "w") as fh:
            fh.write("a" * 16 + os.urandom(24).hex())
        self.owner_sso_key = os.path.join(od, "sso.key")
        self.owner_env = dict(os.environ, GUTLOG_DB=os.path.join(od, "health3.db"),
                              GUTLOG_UPLOADS=os.path.join(od, "uploads"),
                              GUTLOG_PLANS=os.path.join(od, "plans"),
                              GUTLOG_FEED_TOKEN_FILE=os.path.join(od, "feed.token"),
                              GUTLOG_INSECURE="1", GUTLOG_NOSPAWN="1", GUTLOG_LINKS="0",
                              GUTLOG_MIRROR_STAMP=os.path.join(od, "none.json"),
                              GUTLOG_MEALS_FILE=os.path.join(od, "meals.json"),
                              GUTLOG_PLAN_FILE=os.path.join(od, "plan.json"),
                              HEALTH_SSO_KEY_FILE=self.owner_sso_key,
                              HEALTH_SSO_APPS="gutlog=http://127.0.0.1:%d,rxguard=http://127.0.0.1:9,"
                                              "fitlog=http://127.0.0.1:9" % self.owner_port,
                              GUTLOG_FAMILY_FILE=os.path.join(self.root, "root", "family",
                                                              "members.local.json"),
                              GUTLOG_FAMILY_CARE_DIR=os.path.join(self.root, "root", "family", "care"))
        # -- the Family Kitchen: its folder exists before stamping, so the
        # stamp issues member tokens exactly as it does on the server.
        self.kitchen_port = self.base + 3
        self.kitchen_dir = os.path.join(self.root, "srv", "family", "kitchen")
        os.makedirs(os.path.join(self.kitchen_dir, "attach"))
        kown = os.path.join(self.root, "root", "family", "kitchen")
        self.owner_env.update(GUTLOG_KITCHEN_URL="http://127.0.0.1:%d/kitchen" % self.kitchen_port,
                              GUTLOG_KITCHEN_TOKEN_FILE=os.path.join(kown, "owner.token"),
                              GUTLOG_KITCHEN_CAPTURE_FILE=os.path.join(kown, "owner.capture"),
                              GUTLOG_RXGUARD_URL="http://127.0.0.1:9")
        with open(self.owner_env["GUTLOG_MEALS_FILE"], "w") as fh:   # synthetic cards
            json.dump({"cards": [
                {"name": "Mid-morning", "aka": ["Morning"], "rows": [{"kind": "fixed", "items": [["Poha", 1]]}]},
                {"name": "Before lunch", "slot": "auto", "rows": [{"kind": "fixed", "items": [["Poha", 1]]}]}]}, fh)
        runner = os.path.join(self.work, "owner_serve.py")
        with open(runner, "w") as fh:
            fh.write(OWNER_SERVE)
        self._spawn([sys.executable, "-B", runner, self.owner_app, str(self.owner_port)],
                    self.owner_env, "owner")
        # -- members --------------------------------------------------------
        for slug, name, prof in members:
            self.stamp(slug, name, prof)
        r = subprocess.run([sys.executable, "-B", os.path.join(fam_src(), "stamp_member.py"), "--no-system",
                            "--root", self.root, "--code", self.code, "--kitchen-sync"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(r.stdout.decode("utf-8", "replace"))
            raise SystemExit("kitchen-sync failed")
        self._spawn([sys.executable, "-B", os.path.join(self.code, "family", "_serve.py"), "entry_kitchen",
                     str(self.kitchen_port)], dict(os.environ, KITCHEN_DIR=self.kitchen_dir,
                                                   KITCHEN_BASE=self.front_url, KITCHEN_INSECURE="1"), "kitchen")
        if not wait_port(self.kitchen_port, "/kitchen/healthz"):
            self.dump_logs()
            raise SystemExit("kitchen did not start")
        routes = {"/kitchen/": self.kitchen_port}
        for slug, _n, _p in members:
            ports = self.ports(slug)
            routes["/%s/rx/" % slug] = ports["rx"]
            routes["/%s/fit/" % slug] = ports["fit"]
            routes["/%s/" % slug] = ports["gut"]
        self.front = Front(self.front_port, routes)
        for slug, _n, _p in members:
            self.start_member(slug)
        ok = wait_port(self.owner_port, "/healthz")
        if not ok:
            raise SystemExit("owner app did not start")

    def ports(self, slug):
        n = int(slug[1:])
        b = self.port_base + 10 * n
        return {"gut": b + 1, "rx": b + 2, "fit": b + 3}

    def stamp(self, slug, name, prof, expect_ok=True):
        cmd = [sys.executable, "-B", os.path.join(fam_src(), "stamp_member.py"), "--no-system",
               "--root", self.root, "--code", self.code, "--port-base", str(self.port_base),
               "--base-url", self.front_url, "--slug", slug, "--name", name, "--profile", prof]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace")
        m = re.search(r"first-login PIN \(shown once, written nowhere\): (\d{6})", out)
        if m:
            self.pw[slug] = m.group(1)
        if expect_ok and r.returncode != 0:
            print(out)
            raise SystemExit("stamp %s failed" % slug)
        return r.returncode, out

    def member_dir(self, slug):
        return os.path.join(self.root, "srv", "family", slug)

    def member_env(self, slug):
        env = dict(os.environ)
        for k in list(env):
            if k.startswith(("GUTLOG_", "RXGUARD_", "FITLOG_", "FAMILY_")):
                del env[k]
        with open(os.path.join(self.root, "etc", "family", slug + ".env")) as fh:
            for line in fh:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k] = v
        env["FAMILY_DIR"] = self.member_dir(slug)
        env["FAMILY_INSECURE"] = "1"
        env["GUTLOG_NOSPAWN"] = "1"
        # A stray owner variable in the environment must change nothing.
        env["HEALTH_SSO_KEY_FILE"] = self.owner_sso_key
        env["GUTLOG_FEED_TOKEN_FILE"] = self.owner_env["GUTLOG_FEED_TOKEN_FILE"]
        env["GUTLOG_DB"] = self.owner_env["GUTLOG_DB"]
        env["FAMILY_KITCHEN_PORT"] = str(self.kitchen_port)
        env.update(self.extra_member_env)
        return env

    def start_member(self, slug):
        env = self.member_env(slug)
        ports = self.ports(slug)
        serve = os.path.join(self.code, "family", "_serve.py")
        for app in ("gut", "rx", "fit"):
            self._spawn([sys.executable, "-B", serve, "entry_" + app, str(ports[app])], env,
                        "%s-%s" % (slug, app))
        for app, path in (("gut", "/%s/healthz" % slug), ("rx", "/%s/rx/healthz" % slug),
                          ("fit", "/%s/fit/health" % slug)):
            if not wait_port(ports[app], path):
                self.dump_logs()
                raise SystemExit("member %s %s did not start" % (slug, app))

    def _spawn(self, cmd, env, label):
        log = open(os.path.join(self.work, label + ".log"), "wb")
        p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append((label, p, log))

    def dump_logs(self):
        for label, _p, log in self.procs:
            log.flush()
            try:
                t = open(os.path.join(self.work, label + ".log"), encoding="utf-8",
                         errors="replace").read()
            except OSError:
                t = ""
            if t.strip():
                print("---- %s log (tail) ----" % label)
                print("\n".join(t.splitlines()[-25:]))

    def stop(self):
        for _l, p, log in self.procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()
            log.close()
        try:
            self.front.srv.shutdown()
        except Exception:
            pass
        if not os.environ.get("FAMTEST_KEEP"):
            shutil.rmtree(self.work, ignore_errors=True)

    # -- helpers -------------------------------------------------------------
    def owner_client(self):
        c = Client("http://127.0.0.1:%d" % self.owner_port)
        r = c.post("/setup", {"pw": "owner-pass-123", "pw2": "owner-pass-123"})
        if r.status >= 400 or "login" in (r.headers.get("Location") or ""):
            c.post("/login", {"pw": "owner-pass-123"})
        return c

    def member_client(self, slug, app="gut"):
        c = Client(self.front_url)
        pre = "/" + slug if app == "gut" else "/%s/%s" % (slug, app)
        c.post(pre + "/login", {"pin": self.pw[slug]})   # family sign-in is the PIN
        return c

    def kitchen_tokens(self):
        with open(os.path.join(self.kitchen_dir, "tokens.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def kitchen_db(self):
        return os.path.join(self.kitchen_dir, "kitchen.db")

    def stamp_kitchen(self, slug, name, extra=(), expect_ok=True):
        """A kitchen member (k1 ...): an account inside the Kitchen only."""
        cmd = [sys.executable, "-B", os.path.join(fam_src(), "stamp_member.py"), "--no-system",
               "--root", self.root, "--code", self.code, "--base-url", self.front_url,
               "--kitchen-only", "--slug", slug, "--name", name] + list(extra)
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace")
        m = re.search(r"PIN \(shown once, written nowhere\): (\d{6})", out)
        if m:
            self.pw[slug] = m.group(1)
        if expect_ok and r.returncode != 0:
            print(out)
            raise SystemExit("stamp %s failed" % slug)
        return r.returncode, out

    def kitchen_client(self, slug):
        c = Client(self.front_url)
        c.post("/kitchen/%s/login" % slug, {"pin": self.pw[slug]})
        return c

    def secret(self, slug, name):
        with open(os.path.join(self.member_dir(slug), name)) as fh:
            return fh.read().strip()

    def db(self, slug, app):
        return os.path.join(self.member_dir(slug), {"gut": "gutlog/health.db",
                                                   "rx": "rxguard/rxguard.db",
                                                   "fit": "fitlog/fitlog.db"}[app])

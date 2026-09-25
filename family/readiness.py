#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
readiness.py -- is a member's copy ready to hand over? Run before giving anyone their link.

    python3 /root/family/readiness.py --slug m3

Runs as root on the server, against the real address (https://family.dr-manoj.in),
through the same proxy a phone uses. It signs in ONCE with the PIN from the
root-only file, and only after checking that PIN against the stored hash
offline -- a stale file costs no attempt and can never start a lockout. It
also refuses to try when the member is locked or one wrong PIN from a lock.

  1  the sign-in page names the member, has the numeric PIN field, the eye,
     and the manifest + icon for Add to Home Screen
  2  the file PIN verifies; one real sign-in
  3  the Now page (or the first-run form, which is the member's to fill) and /api/now
  4  a medicine added, its salt set, both removed again
  5  a meal and a symptom saved and removed (through the app's own delete)
  6  the Family Kitchen reachable from the diary
  7  Face ID offered after sign-in, for this host
  8  RxGuard and FitLog open through the member's own sign-in ring
  9  sign out works
 10  nothing left behind: medicine, salt, meal and symptom counts as before

The PIN is never printed. Everything the check writes is named
"Readiness check <random>" and removed before it ends, whether a step passed
or not. Exit 0 = READY, 1 = NOT READY, 2 = stopped before signing in.
Python 3.9.
"""
import argparse
import glob
import html
import http.cookiejar
import json
import os
import re
import secrets
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

LOCK_AFTER = 5
RESULTS = []


def step(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail and not ok else ""))
    sys.stdout.flush()
    return bool(ok)


class Browser(object):
    """A phone, as far as the server can tell: cookies, redirects, JSON."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", "family-readiness/1 (iPhone)")]

    def req(self, path, data=None, js=None):
        url = path if path.startswith("http") else self.base + path
        # A page load asks for HTML, as a browser does: that is what the sign-in ring answers.
        body, hdr = None, ({"Accept": "application/json"} if "/api/" in url else
                           {"Accept": "text/html,application/xhtml+xml"})
        if js is not None:
            body, hdr = json.dumps(js).encode(), {"Content-Type": "application/json", "Accept": "application/json"}
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        r = urllib.request.Request(url, data=body, headers=hdr, method="POST" if body is not None else "GET")
        try:
            resp = self.op.open(r, timeout=30)
            code, final, raw = resp.status, resp.geturl(), resp.read()
        except urllib.error.HTTPError as e:
            code, final, raw = e.code, e.geturl(), e.read()
        text = raw.decode("utf-8", "replace")
        try:
            j = json.loads(text)
        except ValueError:
            j = None
        return code, final, text, j


def read_env(path):
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = v
    except OSError:
        pass
    return out


def file_pin(path, slug):
    """The newest line for this slug (resets append)."""
    pin = None
    for line in open(path, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        m = re.match(r"PIN (\d{6})$", f[-1]) if f else None
        if f and f[0] == slug and m:
            pin = m.group(1)
    return pin


def kv(care_db):
    c = sqlite3.connect("file:%s?mode=ro" % care_db, uri=True)
    try:
        return dict(c.execute("SELECT key, value FROM auth_kv").fetchall())
    finally:
        c.close()


def counts(db):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return dict((t, c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
                    for t in ("prnmeds", "med_salts", "meals", "episodes"))
    finally:
        c.close()


def keep_owner(db):
    """A root connection may create -wal/-shm files; give them back to the member."""
    if not hasattr(os, "chown"):
        return
    st = os.stat(os.path.dirname(db))
    for f in glob.glob(db + "*"):
        try:
            if os.stat(f).st_uid != st.st_uid:
                os.chown(f, st.st_uid, st.st_gid)
        except OSError:
            pass


def member_in(b, pre):
    """Signed in as the member (not the caretaker): /api/care/me answers with JSON."""
    code, _, _, j = b.req(pre + "/api/care/me")
    return code == 200 and isinstance(j, dict) and "access" in j and j.get("care") is None


def main():
    ap = argparse.ArgumentParser(description="Is a member's copy ready to hand over?")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--base", default=None, help="default: FAMILY_BASE from the member's env")
    ap.add_argument("--pin-file", default="/root/family/first-login.local.txt")
    ap.add_argument("--etc", default="/etc/family")
    ap.add_argument("--srv", default="/srv/family")
    a = ap.parse_args()
    if not re.match(r"^m[0-9]{1,3}$", a.slug):
        print("--slug must look like m1.")
        return 2
    env = read_env(os.path.join(a.etc, a.slug + ".env"))
    name = env.get("FAMILY_NAME") or ""
    base = (a.base or env.get("FAMILY_BASE") or "https://family.dr-manoj.in").rstrip("/")
    mdir = os.path.join(a.srv, a.slug)
    care_db, gut_db = os.path.join(mdir, "care.db"), os.path.join(mdir, "gutlog", "health.db")
    host = urllib.parse.urlparse(base).hostname
    pre = "/" + a.slug
    print("Readiness of %s at %s%s/" % (a.slug, base, pre))
    if not name or not os.path.exists(care_db) or not os.path.exists(gut_db):
        print("  No such member here (env, care.db or diary missing).")
        return 2

    b = Browser(base)
    # ---------------------------------------------------------------- 1
    code, _, t, _ = b.req(pre + "/login")
    tu = html.unescape(t)
    step("sign-in page opens and names the member", code == 200 and ("%s's health diary" % name) in tu,
         "HTTP %s" % code)
    step("PIN field: numeric keypad, 6 digits, show/hide eye",
         "inputmode='numeric'" in t and "maxlength='6'" in t and "id='eye'" in t)
    mani = "%s%s/manifest.webmanifest" % (base, pre)
    icon = "%s%s/icon-192.png" % (base, pre)
    mc, _, _, mj = b.req(mani)
    ic, _, _, _ = Browser(base).req(icon)
    step("Add to Home Screen: manifest and icon on the sign-in page",
         mani in t and icon in t and mc == 200 and (mj or {}).get("name") == "%s's health diary" % name
         and ic == 200, "manifest %s, icon %s" % (mc, ic))

    # ---------------------------------------------------------------- 2
    pin = file_pin(a.pin_file, a.slug)
    state = kv(care_db)
    import time
    locked = int(state.get("lock_until") or 0) > time.time()
    fails = int(state.get("fails") or 0)
    if locked or fails >= LOCK_AFTER - 1:
        step("not locked, and more than one try left", False,
             "locked" if locked else "%d wrong PINs already; one more would lock -- not trying" % fails)
        return 2
    try:
        from werkzeug.security import check_password_hash
        good = bool(pin) and check_password_hash(state.get("pin_hash") or "", pin)
    except ImportError:
        good = None
    if not step("the PIN in %s verifies (checked offline, no attempt used)" % a.pin_file, good,
                "no PIN line for %s" % a.slug if not pin else "stale PIN -- reset with stamp_member.py"):
        return 2
    before = counts(gut_db)
    code, final, t, _ = b.req(pre + "/login", data={"pin": pin})
    pin = None
    if not step("one real sign-in with the PIN", code == 200 and member_in(b, pre)
                and "/login" not in urllib.parse.urlparse(final).path, "HTTP %s at %s" % (code, final)):
        print("NOT READY")
        return 1

    tag = "Readiness check " + secrets.token_hex(3)
    med_id = None
    try:
        # ------------------------------------------------------------ 7 (first: the offer page follows sign-in)
        oc, _, ot, _ = b.req(pre + "/passkey/offer?next=%2F")
        rc, _, _, rj = b.req(pre + "/passkey/register/begin", js={})
        step("Face ID / Touch ID offered after sign-in, for %s" % host,
             oc == 200 and "id='yes'" in ot and rc == 200 and (rj or {}).get("ok")
             and (rj.get("rp") or {}).get("id") == host, "offer %s, begin %s" % (oc, rc))

        # ------------------------------------------------------------ 3
        hc, hf, ht, _ = b.req(pre + "/")
        first_run = urllib.parse.urlparse(hf).path.endswith("/welcome")
        nc, _, _, nj = b.req(pre + "/api/now")
        step("Now page loads" + (" (first-run form waiting -- the member fills it in)" if first_run else ""),
             hc == 200 and (first_run or 'id="tab-now"' in ht) and nc == 200 and isinstance(nj, dict),
             "page %s, api/now %s" % (hc, nc))

        # ------------------------------------------------------------ 4
        b.req(pre + "/api/prnmeds", js={"name": tag})
        _, _, _, full = b.req(pre + "/api/prnmeds/full")
        med_id = next((m["id"] for m in (full or []) if m.get("name") == tag), None)
        sc, _, _, sj = b.req(pre + "/api/salt", js={"med_id": med_id, "molecule": "readiness check",
                                                     "strength": "1 mg"})
        _, _, _, salts = b.req(pre + "/api/salts")
        row = next((m for m in ((salts or {}).get("meds") or []) if m.get("id") == med_id), {})
        step("medicine added and its salt set (Salts)",
             med_id is not None and sc == 200 and (sj or {}).get("ok") and row.get("molecule") == "readiness check",
             "medicine %s, salt %s" % (med_id, sc))

        # ------------------------------------------------------------ 5
        mc, _, _, _ = b.req(pre + "/api/meals", js={"items": [{"n": tag, "q": 1, "p": 0, "k": 0, "f": 0}],
                                                    "slot": "Evening", "notes": tag})
        ec, _, _, _ = b.req(pre + "/api/episodes", js={"etype": "other", "category": "gut", "severity": 1,
                                                       "notes": tag})
        c = sqlite3.connect("file:%s?mode=ro" % gut_db, uri=True)
        meal = c.execute("SELECT id FROM meals WHERE notes=?", (tag,)).fetchone()
        epi = c.execute("SELECT id FROM episodes WHERE notes=?", (tag,)).fetchone()
        c.close()
        step("a meal and a symptom saved", mc == 200 and ec == 200 and meal and epi, "meal %s, symptom %s" % (mc, ec))
        dm = b.req(pre + "/api/meals/%d/delete" % meal[0], js={})[0] if meal else None
        de = b.req(pre + "/api/delete/episodes/%d" % epi[0], js={})[0] if epi else None
        c = sqlite3.connect("file:%s?mode=ro" % gut_db, uri=True)
        left = c.execute("SELECT (SELECT COUNT(*) FROM meals WHERE notes=?) + "
                         "(SELECT COUNT(*) FROM episodes WHERE notes=?)", (tag, tag)).fetchone()[0]
        c.close()
        step("the meal and the symptom removed through the app", dm == 200 and de == 200 and left == 0,
             "delete %s/%s, left %s" % (dm, de, left))

        # ------------------------------------------------------------ 6
        kc, _, _, kj = b.req(pre + "/api/kitchen/recipes")
        pc, _, _, _ = Browser(base).req("/kitchen/")
        step("the Family Kitchen is reachable from the diary",
             kc == 200 and isinstance((kj or {}).get("recipes"), list) and pc < 500,
             "recipes %s, /kitchen/ %s" % (kc, pc))

        # ------------------------------------------------------------ 8
        for app, label in (("rx", "RxGuard"), ("fit", "FitLog")):
            ac, af, _, _ = b.req("%s/%s/" % (pre, app))
            p = urllib.parse.urlparse(af).path
            step("%s opens through the sign-in ring" % label,
                 ac == 200 and p.startswith("%s/%s/" % (pre, app)) and "/login" not in p, "HTTP %s at %s" % (ac, p))
    finally:
        # ------------------------------------------------------------ cleanup (always)
        con = sqlite3.connect(gut_db, timeout=10)
        try:
            ids = [r[0] for r in con.execute("SELECT id FROM prnmeds WHERE name=?", (tag,))]
            for i in ids:
                con.execute("DELETE FROM med_salts WHERE med_id=?", (i,))
                con.execute("DELETE FROM prnmeds WHERE id=?", (i,))
            con.execute("DELETE FROM meals WHERE notes=?", (tag,))
            con.execute("DELETE FROM episodes WHERE notes=?", (tag,))
            con.commit()
        finally:
            con.close()
            keep_owner(gut_db)

    # ---------------------------------------------------------------- 9
    lc, lf, _, _ = b.req(pre + "/logout")
    step("sign out works (back at the sign-in page, and signed out)",
         lc == 200 and urllib.parse.urlparse(lf).path == pre + "/login" and not member_in(b, pre),
         "HTTP %s at %s" % (lc, lf))

    # ---------------------------------------------------------------- 10
    after = counts(gut_db)
    step("nothing left behind (medicines, salts, meals, symptoms as before)", after == before,
         "before %s, after %s" % (before, after))

    bad = [n for n, ok in RESULTS if not ok]
    print("READY" if not bad else "NOT READY (%d failed)" % len(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

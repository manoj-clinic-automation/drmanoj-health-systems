#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_auth.py -- how a family member signs in: PIN, lockout, Face ID, 12 months.

    python3 -B family/test_family_auth.py gutlog/app.py

Runs the scratch family (famtest.Rig). Face ID is driven by a SOFTWARE
authenticator written here (P-256 keys, real WebAuthn ceremonies: client
data, authenticator data, CBOR attestation, ECDSA signatures), so the server's
checks are exercised with genuine, forged, replayed and wrong-origin
credentials. The lockout is driven for real; only the passage of time is
simulated, by moving the lock's end in the member's care.db.
Python 3.9.
"""
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")
sys.path.insert(0, famtest.fam_src())
import family_webauthn as W  # noqa: E402


# ------------------------------------------------------------------ a software authenticator
def cbor(v):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for ai, ln in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 256 ** ln:
                return bytes([major << 5 | ai]) + n.to_bytes(ln, "big")
    if isinstance(v, bool):
        return bytes([0xf5 if v else 0xf4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return head(3, len(b)) + b
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Authenticator(object):
    def __init__(self, rp_id, origin):
        self.rp_id, self.origin = rp_id, origin
        self.d = secrets.randbelow(W.N - 1) + 1
        self.x, self.y = W._to_affine(W._jac_mul(W.G, self.d))
        self.cred = secrets.token_bytes(16)
        self.count = 0

    def sign(self, msg):
        e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        while True:
            k = secrets.randbelow(W.N - 1) + 1
            r = W._to_affine(W._jac_mul(W.G, k))[0] % W.N
            s = pow(k, W.N - 2, W.N) * (e + r * self.d) % W.N
            if r and s:
                break

        def i(v):
            b = v.to_bytes((v.bit_length() + 8) // 8, "big")
            return b"\x02" + bytes([len(b)]) + b
        body = i(r) + i(s)
        return b"\x30" + bytes([len(body)]) + body

    def create(self, challenge, flags=0x45, origin=None):
        cose = cbor({1: 2, 3: -7, -1: 1, -2: self.x.to_bytes(32, "big"), -3: self.y.to_bytes(32, "big")})
        ad = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count) + \
            b"\x00" * 16 + struct.pack(">H", len(self.cred)) + self.cred + cose
        cd = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": origin or self.origin}).encode()
        return {"id": W.b64u(self.cred), "challenge": challenge,
                "response": {"clientDataJSON": W.b64u(cd),
                             "attestationObject": W.b64u(cbor({"fmt": "none", "attStmt": {}, "authData": ad}))}}

    def get(self, challenge, flags=0x05, origin=None, tamper=False):
        self.count += 1
        ad = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        cd = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin or self.origin}).encode()
        sig = self.sign(ad + hashlib.sha256(cd).digest())
        if tamper:
            sig = self.sign(ad + b"x")
        return {"id": W.b64u(self.cred), "challenge": challenge,
                "response": {"clientDataJSON": W.b64u(cd), "authenticatorData": W.b64u(ad),
                             "signature": W.b64u(sig)}}


# ------------------------------------------------------------------ helpers
def care_db(rig, slug):
    c = sqlite3.connect(os.path.join(rig.member_dir(slug), "care.db"))
    c.row_factory = sqlite3.Row
    return c


def kv(rig, slug, key):
    c = care_db(rig, slug)
    r = c.execute("SELECT value FROM auth_kv WHERE key=?", (key,)).fetchone()
    c.close()
    return r[0] if r else None


def end_lock(rig, slug):
    """The passage of time: the lock's end moved into the past."""
    c = care_db(rig, slug)
    c.execute("UPDATE auth_kv SET value='1' WHERE key='lock_until'")
    c.commit()
    c.close()


def lock_minutes(rig, slug):
    return round((int(kv(rig, slug, "lock_until") or 0) - time.time()) / 60.0)


def pin_login(rig, slug, pin, app="gut", client=None):
    c = client or famtest.Client(rig.front_url)
    pre = "/" + slug if app == "gut" else "/%s/%s" % (slug, app)
    r = c.post(pre + "/login", {"pin": pin})
    return c, r


def signed_in(c, slug):
    r = c.get("/%s/api/care/me" % slug)
    return r.status == 200 and (r.json() or {}).get("care") is None


def cookie_days(r):
    sc = r.headers.get("Set-Cookie") or ""
    m = re.search(r"Expires=([^;]+)", sc)
    if not m:
        return None
    return (parsedate_to_datetime(m.group(1)) - datetime.now(timezone.utc)).days


def bad_pin(good):
    return "%06d" % ((int(good) + 1) % 1000000)


def run(rig):
    pin1, pin2 = rig.pw["m1"], rig.pw["m2"]
    origin, rp = rig.front_url, "127.0.0.1"

    # ---------------------------------------------------------------- F01
    ok = [signed_in(pin_login(rig, "m1", pin1)[0], "m1")]
    for app in ("rx", "fit"):
        c, r = pin_login(rig, "m1", pin1, app)
        pre = "/m1/" + app + "/"
        ok.append(c.get(pre, follow=True).status == 200 and "/login" not in c.get(pre, follow=True).url)
    c, r = pin_login(rig, "m1", bad_pin(pin1))
    wrong = r.status == 401 and "Wrong PIN" in r.text and not signed_in(c, "m1")
    lp = famtest.Client(rig.front_url).get("/m1/login").text
    check("F01 a family member signs in with a 6-digit PIN, in all three apps; a wrong PIN is refused",
          all(ok) and wrong and "inputmode='numeric'" in lp and "maxlength='6'" in lp, (ok, wrong))
    anon = famtest.Client(rig.front_url)
    pages = dict(((s, a), html.unescape(anon.get("/%s/%slogin" % (s, a + "/" if a != "gut" else "")).text))
                 for s in ("m1", "m2") for a in ("gut", "rx", "fit"))
    want = {"gut": "%s's health diary", "rx": "%s's medicines", "fit": "%s's fitness"}
    named = all(("<h1>%s</h1>" % (want[a] % n)) in pages[(s, a)] and "<title>%s</title>" % (want[a] % n) in pages[(s, a)]
                and o not in pages[(s, a)]
                for s, n, o in (("m1", "Member A", "Member B"), ("m2", "Member B", "Member A")) for a in want)
    check("F12 the sign-in page says whose it is, in every app, and whom to ask otherwise",
          named and "Not Member A? Ask Manoj for your own link." in pages[("m1", "gut")],
          pages[("m1", "gut")][:300])
    check("F12 the PIN field has a numeric keypad and a show/hide eye; RxGuard and FitLog offer no Face ID button",
          all("inputmode='numeric'" in p and "pattern='[0-9]*'" in p and "id='eye'" in p for p in pages.values())
          and all("id='pk'" not in pages[(s, a)] and "Face ID" not in pages[(s, a)]
                  for s in ("m1", "m2") for a in ("rx", "fit"))
          and "id='pk' style='display:none'" in pages[("m1", "gut")], "")
    g = rig.front_url + "/m1"
    head_ok = all(x in pages[(("m1", a))] for a in ("gut", "rx", "fit") for x in (
        "<link rel='manifest' href='%s/manifest.webmanifest'>" % g,
        "<link rel='apple-touch-icon' href='%s/icon-192.png'>" % g,
        "<meta name='apple-mobile-web-app-title' content='Member A'>"))
    mj = anon.get("/m1/manifest.webmanifest").json() or {}
    icon = anon.get("/m1/icon-192.png")
    check("F12 Add to Home Screen from the sign-in page gives the member's name and icon",
          head_ok and mj.get("name") == "Member A's health diary" and mj.get("short_name") == "Member A"
          and icon.status == 200 and (icon.headers.get("Content-Type") or "").startswith("image/png"),
          (head_ok, mj.get("name"), mj.get("short_name"), icon.status))
    home = html.unescape(pin_login(rig, "m1", pin1)[0].get("/m1/", follow=True).text)
    if 'id="tab-now"' not in home:   # the first-run form first
        wc = pin_login(rig, "m1", pin1)[0]
        wc.post("/m1/welcome", {"name": "Member", "age": "63", "cond": "ibs"})
        home = html.unescape(wc.get("/m1/", follow=True).text)
    check("F12 the diary's own Add to Home Screen title is the member's name, not GutLog",
          '<meta name="apple-mobile-web-app-title" content="Member A">' in home
          and 'content="GutLog"' not in home, home[:200])
    oc = rig.owner_client()
    owner_login =famtest.Client("http://127.0.0.1:%d" % rig.owner_port).get("/login").text
    check("F01 the owner's own GutLog keeps his password sign-in (no PIN, no lockout page)",
          "name=\"pw\"" in owner_login and "maxlength='6'" not in owner_login
          and oc.get("/api/now").status == 200, owner_login[:200])

    # ---------------------------------------------------------------- F02 / F04
    texts = []
    for i in range(3):
        texts.append(html.unescape(pin_login(rig, "m2", bad_pin(pin2))[1].text))
    for i in range(2):
        texts.append(html.unescape(pin_login(rig, "m2", bad_pin(pin2), "rx")[1].text))
    c, r = pin_login(rig, "m2", pin2, "fit")
    fit_refused = "Paused until" in r.text and not c.get("/m2/fit/", follow=True).url.endswith("/m2/fit/")
    c, r = pin_login(rig, "m2", pin2)
    check("F02 five wrong PINs lock sign-in for 15 minutes; even the right PIN is refused while locked",
          "Paused until" in r.text and not signed_in(c, "m2") and 14 <= lock_minutes(rig, "m2") <= 15,
          r.text[-300:])
    check("F04 the lockout is shared: wrong PINs in GutLog and RxGuard lock FitLog too", fit_refused, "")
    left = [(re.search(r"Wrong PIN — (\d) (tries|try) left", t) or [None, None, None]) for t in texts[:4]]
    check("F12 wrong PINs count down the tries left (4, 3, 2, 1)",
          [(m[1], m[2]) for m in left] == [("4", "tries"), ("3", "tries"), ("2", "tries"), ("1", "try")],
          [t[-240:] for t in texts[:1]])
    ist = datetime.fromtimestamp(int(kv(rig, "m2", "lock_until") or 0), timezone(timedelta(hours=5, minutes=30)))
    check("F12 the pause says until when, in IST, and whom to call",
          re.search(r"Paused until (tomorrow )?%s IST — call Manoj" % ist.strftime("%H:%M"), texts[4])
          is not None and re.search(r"Paused until (tomorrow )?%s IST — call Manoj"
                                    % ist.strftime("%H:%M"), html.unescape(r.text)) is not None,
          (ist.strftime("%H:%M"), texts[4][-240:]))

    # ---------------------------------------------------------------- F03
    end_lock(rig, "m2")
    for i in range(5):
        c, r = pin_login(rig, "m2", bad_pin(pin2))
    second = lock_minutes(rig, "m2")
    end_lock(rig, "m2")
    for i in range(5):
        c, r = pin_login(rig, "m2", bad_pin(pin2))
    third = lock_minutes(rig, "m2")
    end_lock(rig, "m2")
    c, r = pin_login(rig, "m2", pin2)
    after_ok = signed_in(c, "m2") and kv(rig, "m2", "lock_level") == "0"
    for i in range(5):
        pin_login(rig, "m2", bad_pin(pin2))
    fresh = lock_minutes(rig, "m2")
    end_lock(rig, "m2")
    check("F03 each repeat lock doubles (15, 30, 60); the right PIN afterwards starts again at 15",
          29 <= second <= 30 and 59 <= third <= 60 and after_ok and 14 <= fresh <= 15,
          (second, third, after_ok, fresh))

    # ---------------------------------------------------------------- F05
    c = care_db(rig, "m2")
    rows = [dict(r) for r in c.execute("SELECT at, app, ip, result FROM auth_log ORDER BY id").fetchall()]
    c.close()
    kinds = set(re.sub(r" \(.*\)|for \d+ minutes", "", r["result"]).strip() for r in rows)
    m2c = pin_login(rig, "m2", pin2)[0]
    page = m2c.get("/m2/care").text
    check("F05 every attempt is logged with IST time, app and address, and shown on the member's page",
          {"wrong PIN", "locked", "refused: locked", "signed in"} <= kinds
          and all(re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", r["at"]) and r["ip"] for r in rows)
          and {"gut", "rx", "fit"} <= set(r["app"] for r in rows)
          and "Recent sign-in attempts" in page and "wrong PIN" in page, (kinds, rows[:2]))

    # ---------------------------------------------------------------- F06
    c1, r1 = pin_login(rig, "m1", pin1)
    days = cookie_days(r1)
    tk = rig.owner_client().get("/family/open/m1").headers.get("Location") or ""
    cc = famtest.Client(rig.front_url)
    rc = cc.get(tk)
    care_days = cookie_days(rc)
    check("F06 a member stays signed in for 12 months on their device; a caretaker session is never kept",
          days is not None and 363 <= days <= 366 and "Set-Cookie" in rc.headers and care_days is None,
          (days, care_days, rc.headers.get("Set-Cookie", "")[:120]))

    # ---------------------------------------------------------------- F07
    a, _ = pin_login(rig, "m1", pin1)
    b, _ = pin_login(rig, "m1", pin1)
    a.get("/m1/logout")
    out_a = not signed_in(a, "m1") and signed_in(b, "m1")
    b2, _ = pin_login(rig, "m1", pin1)
    b2.post("/m1/signout-all", {})
    check("F07 signing out ends that device; 'sign out on all devices' ends every other one",
          out_a and not signed_in(b, "m1") and not signed_in(b2, "m1"), out_a)

    # ---------------------------------------------------------------- F08
    m, _ = pin_login(rig, "m1", pin1)
    weak = m.post("/m1/pin/change", {"old": pin1, "new": "123456"}, ctype="json")
    same = m.post("/m1/pin/change", {"old": pin1, "new": "777777"}, ctype="json")
    wrong_old = m.post("/m1/pin/change", {"old": bad_pin(pin1), "new": "480715"}, ctype="json")
    good = m.post("/m1/pin/change", {"old": pin1, "new": "480715"}, ctype="json")
    newpin_ok = signed_in(pin_login(rig, "m1", "480715")[0], "m1")
    oldpin_no = not signed_in(pin_login(rig, "m1", pin1)[0], "m1")
    cc = famtest.Client(rig.front_url)
    cc.get(rig.owner_client().get("/family/open/m1").headers.get("Location") or "")
    ct = cc.post("/m1/pin/change", {"old": "480715", "new": "593716"}, ctype="json")
    check("F08 the member can change the PIN (not to 123456 or 777777); the caretaker cannot",
          weak.status == 400 and same.status == 400 and wrong_old.status == 400 and good.status == 200
          and newpin_ok and oldpin_no and ct.status == 403,
          (weak.status, same.status, wrong_old.status, good.status, newpin_ok, oldpin_no, ct.status))
    pin1 = "480715"

    # ---------------------------------------------------------------- F09
    au = Authenticator(rp, origin)
    m, _ = pin_login(rig, "m1", pin1)
    beg = m.post("/m1/passkey/register/begin", {}, ctype="json").json() or {}
    reg = m.post("/m1/passkey/register/finish", au.create(beg.get("challenge")), ctype="json")
    anon = famtest.Client(rig.front_url)

    def try_login(**kw):
        b_ = anon.post("/m1/passkey/login/begin", {}, ctype="json").json() or {}
        return anon.post("/m1/passkey/login/finish", au.get(b_.get("challenge"), **kw), ctype="json")
    r_ok = try_login()
    in_ok = signed_in(anon, "m1") and (r_ok.json() or {}).get("next") == "/m1/"
    anon = famtest.Client(rig.front_url)
    r_tamper = try_login(tamper=True)
    r_origin = try_login(origin="https://evil.example")
    r_nouv = try_login(flags=0x01)
    b_ = anon.post("/m1/passkey/login/begin", {}, ctype="json").json() or {}
    first = anon.post("/m1/passkey/login/finish", au.get(b_["challenge"]), ctype="json")
    anon2 = famtest.Client(rig.front_url)
    replay = anon2.post("/m1/passkey/login/finish", au.get(b_["challenge"]), ctype="json")
    other = Authenticator(rp, origin)
    b3 = famtest.Client(rig.front_url).post("/m1/passkey/login/begin", {}, ctype="json").json() or {}
    r_unknown = famtest.Client(rig.front_url).post("/m1/passkey/login/finish", other.get(b3["challenge"]), ctype="json")
    check("F09 Face ID: set up after a PIN sign-in, then signs the member in",
          reg.status == 200 and r_ok.status == 200 and in_ok, (reg.status, reg.text[:120], r_ok.text[:120]))
    check("F09 Face ID refuses a forged signature, another origin, no Face ID check, a replayed challenge "
          "and an unknown key", all(x.status == 400 for x in (r_tamper, r_origin, r_nouv, replay, r_unknown))
          and first.status == 200 and not signed_in(anon2, "m1"),
          [x.status for x in (r_tamper, r_origin, r_nouv, first, replay, r_unknown)])
    cc = famtest.Client(rig.front_url)
    cc.get(rig.owner_client().get("/family/open/m1").headers.get("Location") or "")
    creg = cc.post("/m1/passkey/register/begin", {}, ctype="json")
    nb = famtest.Client(rig.front_url).post("/m2/passkey/login/begin", {}, ctype="json")
    check("F09 the caretaker cannot set up Face ID; a member without it is told to use the PIN",
          creg.status == 403 and nb.status == 404, (creg.status, nb.status))

    # ---------------------------------------------------------------- F10
    for i in range(5):
        pin_login(rig, "m1", bad_pin(pin1))
    locked = "Paused until" in pin_login(rig, "m1", pin1)[1].text
    anon = famtest.Client(rig.front_url)
    r_pk = try_login()
    check("F10 Face ID still signs in during a PIN lockout, and ends the lockout",
          locked and r_pk.status == 200 and signed_in(anon, "m1") and kv(rig, "m1", "lock_until") == "0",
          (locked, r_pk.status, kv(rig, "m1", "lock_until")))

    # ---------------------------------------------------------------- F11
    keep, _ = pin_login(rig, "m2", pin2)
    keep_fit, _ = pin_login(rig, "m2", pin2, "fit")   # FitLog has no epoch of its own: only the family one ends it
    fit_before = "/login" not in keep_fit.get("/m2/fit/", follow=True).url
    pf = os.path.join(rig.work, "pins.txt")
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "stamp_member.py"), "--no-system",
                        "--root", rig.root, "--code", rig.code, "--slug", "m2", "--reset-pin", "--password-file", pf],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode()
    line = open(pf).read() if os.path.exists(pf) else ""
    newpin = (re.search(r"PIN (\d{6})", line) or [None, ""])[1]
    mode_ok = os.name == "nt" or (os.stat(pf).st_mode & 0o777) == 0o600
    check("F11 a PIN reset writes the new PIN only to the root-only file, ends every session, and the old PIN "
          "stops working", r.returncode == 0 and newpin and newpin not in out and mode_ok
          and not signed_in(keep, "m2") and fit_before and "/login" in keep_fit.get("/m2/fit/", follow=True).url
          and not signed_in(pin_login(rig, "m2", pin2)[0], "m2")
          and signed_in(pin_login(rig, "m2", newpin)[0], "m2"), out[-300:])

    # ---------------------------------------------------------------- F13 readiness.py
    def ready(pin, slug="m1"):
        pfile = os.path.join(rig.work, "ready-pins.txt")
        with open(pfile, "w") as fh:
            fh.write("%s\tMember A\t%s/%s/\tPIN 000000\n" % (slug, rig.front_url, slug))   # older line: ignored
            fh.write("%s\tMember A\t%s/%s/\tPIN %s\n" % (slug, rig.front_url, slug, pin))
        r_ = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "readiness.py"), "--slug", slug,
                             "--base", rig.front_url, "--pin-file", pfile,
                             "--etc", os.path.join(rig.root, "etc", "family"),
                             "--srv", os.path.dirname(rig.member_dir(slug))],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r_.returncode, r_.stdout.decode("utf-8", "replace")

    def rows(slug):
        g = sqlite3.connect(os.path.join(rig.member_dir(slug), "gutlog", "health.db"))
        n = [g.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("prnmeds", "med_salts", "meals", "episodes")]
        g.close()
        c = care_db(rig, slug)
        n.append(c.execute("SELECT COUNT(*) FROM auth_log").fetchone()[0])
        c.close()
        return n

    b0 = rows("m1")
    rc, out = ready(pin1)
    b1 = rows("m1")
    check("F13 the readiness check passes on a ready member, signs in once, and leaves nothing behind",
          rc == 0 and out.rstrip().endswith("READY") and "FAIL" not in out and pin1 not in out
          and b1[:4] == b0[:4] and b1[4] == b0[4] + 1, (rc, b0, b1, [ln for ln in out.splitlines() if "FAIL" in ln]))
    rc2, out2 = ready(bad_pin(pin1))
    b2 = rows("m1")
    check("F13 a stale PIN in the file costs no attempt: the readiness check stops before signing in",
          rc2 == 2 and b2 == b1 and kv(rig, "m1", "fails") in (None, "0") and bad_pin(pin1) not in out2,
          (rc2, b1, b2, out2[-300:]))
    c = care_db(rig, "m1")
    c.execute("INSERT OR REPLACE INTO auth_kv(key, value) VALUES('fails', '4')")
    c.commit()
    c.close()
    rc3, out3 = ready(pin1)
    b3 = rows("m1")
    c = care_db(rig, "m1")
    c.execute("UPDATE auth_kv SET value='0' WHERE key='fails'")
    c.commit()
    c.close()
    check("F13 the readiness check will not try a member one wrong PIN from a lock",
          rc3 == 2 and b3 == b2 and "one more would lock" in out3, (rc3, out3[-300:]))


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("F00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

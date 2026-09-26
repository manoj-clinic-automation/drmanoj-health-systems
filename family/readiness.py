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

A KITCHEN MEMBER (--slug k1) is checked the same way, against the Kitchen:
sign-in page ("<Name> — Family Kitchen", manifest named "Family Kitchen"),
the file PIN verified offline, one sign-in, Face ID offered, the recipe
book, a draft captured, published as "Recipe by <Name>", rated, the test
recipe and its draft removed, sign out, and the pool's counts as before.

A MEMBER ON THE weight PROFILE (26-Sep-2026) is also checked for: a weekly
dose scheduled for today, shown and ticked, then removed; a check-in
answered (PHQ-2, all zeros -- no flag) and removed; the plan PDF present.

A PHYSIO (--slug p1) is checked against the member they are attached to:
the sign-in page names them and the member, the file PIN verified offline,
one sign-in, the programme, a pain entry saved and removed, the physio
cookie sent to the member's own pages and APIs and REFUSED everywhere
(scope), sign out. Python 3.9.
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

    def req(self, path, data=None, js=None, headers=None):
        url = path if path.startswith("http") else self.base + path
        # A page load asks for HTML, as a browser does: that is what the sign-in ring answers.
        body, hdr = None, ({"Accept": "application/json"} if "/api/" in url or "/j/" in url else
                           {"Accept": "text/html,application/xhtml+xml"})
        hdr.update(headers or {})
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

    def cookie(self, name):
        return next((c.value for c in self.jar if c.name == name), None)


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
        tables = ["prnmeds", "med_salts", "meals", "episodes"]
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='checkins'").fetchone():
            tables += ["med_schedule", "doses", "checkins"]
        return dict((t, c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]) for t in tables)
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


def kitchen_counts(db):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return dict((t, c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
                    for t in ("recipes", "ratings", "drafts"))
    finally:
        c.close()


def last_seen(db, slug):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        r = c.execute("SELECT last_seen FROM kmembers WHERE slug=?", (slug,)).fetchone()
        return (r[0] or "") if r else ""
    finally:
        c.close()


def kitchen_readiness(a):
    """A kitchen member: the Kitchen service only, through the real address."""
    import time
    kdir = os.path.join(a.srv, "kitchen")
    kdb = os.path.join(kdir, "kitchen.db")
    auth_db = os.path.join(kdir, "members", a.slug, "auth.db")
    try:
        c = sqlite3.connect("file:%s?mode=ro" % kdb, uri=True)
        row = c.execute("SELECT name, enabled FROM kmembers WHERE slug=?", (a.slug,)).fetchone()
        c.close()
    except sqlite3.Error:
        row = None
    base = a.base
    if not base:
        try:
            base = json.load(open(a.registry, encoding="utf-8")).get("base")
        except (OSError, ValueError):
            base = None
    base = (base or "https://family.dr-manoj.in").rstrip("/")
    host = urllib.parse.urlparse(base).hostname
    pre = "/kitchen/" + a.slug
    print("Readiness of kitchen member %s at %s%s/" % (a.slug, base, pre))
    if not row or not os.path.exists(auth_db):
        print("  No such kitchen member here (kmembers row or auth.db missing).")
        return 2
    name = row[0]
    if not row[1]:
        print("  This kitchen member is disabled.")
        return 2
    b = Browser(base)
    # ---------------------------------------------------------------- 1
    code, _, t, _ = b.req(pre + "/login")
    tu = html.unescape(t)
    step("sign-in page opens and names the member", code == 200 and ("%s — Family Kitchen" % name) in tu,
         "HTTP %s" % code)
    step("PIN field: numeric keypad, 6 digits, show/hide eye",
         "inputmode='numeric'" in t and "maxlength='6'" in t and "id='eye'" in t)
    mani = "%s%s/manifest.webmanifest" % (base, pre)
    icon = "%s%s/icon-192.png" % (base, pre)
    mc, _, _, mj = b.req(mani)
    ic, _, _, _ = Browser(base).req(icon)
    step("Add to Home Screen: manifest named Family Kitchen, with the Kitchen icon",
         (pre + "/manifest.webmanifest") in t and (pre + "/icon-192.png") in t and mc == 200
         and (mj or {}).get("name") == "Family Kitchen"
         and (mj or {}).get("scope") == pre + "/" and ic == 200, "manifest %s (%s), icon %s"
         % (mc, (mj or {}).get("name"), ic))
    # ---------------------------------------------------------------- 2
    pin = file_pin(a.pin_file, a.slug)
    state = kv(auth_db)
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
    before = kitchen_counts(kdb)
    # The check's own sign-in is not the member's visit: the owner's Family
    # page reads "last visit" from here, so it is put back as it was.
    seen_before = last_seen(kdb, a.slug)
    code, final, t, _ = b.req(pre + "/login", data={"pin": pin})
    pin = None
    mc_, _, _, me = b.req(pre + "/j/me")
    if not step("one real sign-in with the PIN", code == 200 and mc_ == 200 and (me or {}).get("slug") == a.slug
                and "/login" not in urllib.parse.urlparse(final).path, "HTTP %s at %s, me %s" % (code, final, mc_)):
        print("NOT READY")
        return 1
    tag = "Readiness check " + secrets.token_hex(3)
    rid = None
    try:
        # ------------------------------------------------------------ 3 Face ID
        oc, _, ot, _ = b.req(pre + "/passkey/offer?next=%2F")
        rc, _, _, rj = b.req(pre + "/passkey/register/begin", js={})
        step("Face ID / Touch ID offered after sign-in, for %s" % host,
             oc == 200 and "id='yes'" in ot and rc == 200 and (rj or {}).get("ok")
             and (rj.get("rp") or {}).get("id") == host, "offer %s, begin %s" % (oc, rc))
        # ------------------------------------------------------------ 4 the book
        hc, _, ht, _ = b.req(pre + "/")
        lc, _, _, lj = b.req(pre + "/j/recipes")
        pc, _, _, pj = b.req(pre + "/j/people")
        step("the recipe book opens, lists the pool and its people",
             hc == 200 and "Family Kitchen" in ht and lc == 200 and isinstance((lj or {}).get("recipes"), list)
             and pc == 200 and isinstance((pj or {}).get("people"), list), "page %s, recipes %s, people %s"
             % (hc, lc, pc))
        step("the Share-shortcut key is issued (address and key on the Me tab)",
             bool(((me or {}).get("capture") or {}).get("token")) and pre.split("/")[-1] in
             (((me or {}).get("capture") or {}).get("url") or ""), "")
        # ------------------------------------------------------------ 5 capture -> publish
        dc, _, _, dj = b.req(pre + "/j/drafts", js={"text": tag + "\n1 cup rice\nCook it."})
        did = (dj or {}).get("id")
        gc_, _, _, gj = b.req(pre + "/j/drafts/%s" % did) if did else (0, "", "", None)
        step("a draft captured and private (in the Inbox, not in the pool)",
             dc == 200 and did and gc_ == 200 and (gj or {}).get("ok")
             and kitchen_counts(kdb)["recipes"] == before["recipes"], "draft %s, open %s" % (dc, gc_))
        pc_, _, _, pj_ = b.req(pre + "/j/drafts/%s/publish" % did,
                               js={"name": tag, "grp": "Other", "servings": 1,
                                   "ingredients": [{"item": "rice", "qty": 1, "unit": "cup"}],
                                   "method": ["Cook it."]}) if did else (0, "", "", None)
        rid = (pj_ or {}).get("id")
        cc, _, _, cj = b.req(pre + "/j/recipe/%s" % rid) if rid else (0, "", "", None)
        rec = (cj or {}).get("recipe") or {}
        step("published by the member, credited 'Recipe by %s', with nutrition per serving" % name,
             pc_ == 200 and rid and cc == 200 and rec.get("added_by") == name and rec.get("status") == "published"
             and isinstance((cj or {}).get("nutrition"), dict), "publish %s, card %s, by %r" % (pc_, cc, rec.get("added_by")))
        # ------------------------------------------------------------ 6 rate
        rr, _, _, _ = b.req(pre + "/j/recipe/%s/rate" % rid, js={"stars": 5, "made": True}) if rid else (0, "", "", None)
        _, _, _, cj2 = b.req(pre + "/j/recipe/%s" % rid) if rid else (0, "", "", None)
        rs = ((cj2 or {}).get("recipe") or {}).get("ratings") or []
        step("rated, and the rating shows the rater's name", rr == 200 and rs and rs[0].get("who") == name
             and rs[0].get("stars") == 5, "rate %s, %s" % (rr, rs[:1]))
        # ------------------------------------------------------------ 7 the member's own remove
        uc, _, _, _ = b.req(pre + "/j/recipe/%s/unpublish" % rid, js={"publish": False}) if rid else (0, "", "", None)
        _, _, _, lj2 = b.req(pre + "/j/recipes?q=" + urllib.parse.quote(tag))
        step("the member can unpublish their own recipe (gone from the pool)",
             uc == 200 and not [x for x in (lj2 or {}).get("recipes") or [] if x.get("id") == rid], "unpublish %s" % uc)
    finally:
        # ------------------------------------------------------------ cleanup (always)
        con = sqlite3.connect(kdb, timeout=10)
        try:
            for (i,) in con.execute("SELECT id FROM recipes WHERE name=?", (tag,)).fetchall():
                con.execute("DELETE FROM ratings WHERE recipe_id=?", (i,))
                con.execute("DELETE FROM recipes WHERE id=?", (i,))
            con.execute("DELETE FROM drafts WHERE text LIKE ?", (tag + "%",))
            con.commit()
        finally:
            con.close()
            keep_owner(kdb)
    # ---------------------------------------------------------------- 8
    lc, lf, _, _ = b.req(pre + "/signout", data={})
    mc2, _, _, _ = b.req(pre + "/j/me")
    step("sign out works (back at the sign-in page, and signed out)",
         lc == 200 and urllib.parse.urlparse(lf).path == pre + "/login" and mc2 == 401,
         "HTTP %s at %s, me %s" % (lc, lf, mc2))
    # ---------------------------------------------------------------- 9
    con = sqlite3.connect(kdb, timeout=10)
    try:
        con.execute("UPDATE kmembers SET last_seen=? WHERE slug=?", (seen_before, a.slug))
        con.commit()
    finally:
        con.close()
        keep_owner(kdb)
    after = kitchen_counts(kdb)
    step("nothing left behind (recipes, ratings, drafts as before; last visit as it was)",
         after == before and last_seen(kdb, a.slug) == seen_before,
         "before %s, after %s, last visit %r -> %r" % (before, after, seen_before, last_seen(kdb, a.slug)))
    bad = [n for n, ok in RESULTS if not ok]
    print("READY" if not bad else "NOT READY (%d failed)" % len(bad))
    return 0 if not bad else 1


def physio_readiness(a):
    """A physio: the member's process, the /physio/ pages, and the scope."""
    import time
    try:
        reg = json.load(open(a.registry, encoding="utf-8"))
    except (OSError, ValueError):
        reg = {}
    ent = next((p for p in reg.get("physios") or [] if p.get("slug") == a.slug), None)
    mslug = a.for_member or ((ent or {}).get("for") or [None])[0]
    if not ent or not mslug:
        print("  No such physio here (registry has no %s, or no member to check against; use --for m3)." % a.slug)
        return 2
    env = read_env(os.path.join(a.etc, mslug + ".env"))
    mname = env.get("FAMILY_NAME") or mslug
    base = (a.base or env.get("FAMILY_BASE") or reg.get("base") or "https://family.dr-manoj.in").rstrip("/")
    mdir = os.path.join(a.srv, mslug)
    auth_db = os.path.join(mdir, "physio", a.slug, "auth.db")
    gut_db = os.path.join(mdir, "gutlog", "health.db")
    pre = "/%s/physio" % mslug
    print("Readiness of physio %s at %s%s/ (for %s)" % (a.slug, base, pre, mslug))
    if not os.path.exists(auth_db) or not os.path.exists(gut_db):
        print("  Not attached here (auth.db or the member's diary missing).")
        return 2
    b = Browser(base)
    code, _, t, _ = b.req(pre + "/login")
    tu = html.unescape(t)
    step("sign-in page names the physio and the member", code == 200
         and ("%s — physio for %s" % (ent["name"], mname)) in tu, "HTTP %s" % code)
    step("PIN field: numeric keypad, 6 digits, show/hide eye",
         "inputmode='numeric'" in t and "maxlength='6'" in t and "id='eye'" in t)
    pin = file_pin(a.pin_file, a.slug)
    state = kv(auth_db)
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
    code, final, _, _ = b.req(pre + "/login", data={"pin": pin, "p": a.slug})
    pin = None
    mc, _, _, me = b.req(pre + "/j/me")
    if not step("one real sign-in with the PIN", code == 200 and mc == 200 and (me or {}).get("slug") == a.slug
                and "/login" not in urllib.parse.urlparse(final).path, "HTTP %s at %s, me %s" % (code, final, mc)):
        print("NOT READY")
        return 1
    tag = "Readiness check " + secrets.token_hex(3)
    try:
        pc, _, _, pj = b.req(pre + "/j/programme")
        step("the programme page answers (items, today's list, next session)",
             pc == 200 and isinstance((pj or {}).get("programme"), dict), "HTTP %s" % pc)
        hc, _, ht, _ = b.req(pre + "/")
        step("the physio page opens", hc == 200 and "Programme" in ht and "Re-test" in ht, "HTTP %s" % hc)
        rc, _, _, rj = b.req(pre + "/j/pain", js={"site": "Knee - L", "score": 0, "walk_min": 30, "notes": tag})
        c = sqlite3.connect("file:%s?mode=ro" % gut_db, uri=True)
        row = c.execute("SELECT id FROM joint_log WHERE notes LIKE ?", ("%" + tag + "%",)).fetchone()
        c.close()
        step("a pain and walking entry saved by the physio", rc == 200 and row, "HTTP %s" % rc)
        # scope: the physio cookie, sent by hand to everything that is not /physio/
        ck = b.cookie("fam_%s_physio" % mslug)
        hdr = {"Cookie": "fam_%s_physio=%s" % (mslug, ck or "")}
        probe = Browser(base)
        bad = []
        for path in ("/%s/api/now" % mslug, "/%s/api/checkins/due" % mslug, "/%s/api/plans" % mslug,
                     "/%s/api/joint" % mslug, "/%s/api/physio/me" % mslug, "/%s/api/care/me" % mslug):
            pc_, _, pt, pj_ = probe.req(path, headers=hdr)
            if pc_ == 200 and pj_ is not None and not (isinstance(pj_, dict) and pj_.get("ok") is False) \
                    and "login" not in pt[:300]:
                bad.append(path)
        hc2, hf, ht2, _ = probe.req("/%s/" % mslug, headers=hdr)
        if hc2 == 200 and 'id="tab-now"' in ht2:
            bad.append("/%s/" % mslug)
        rx, rf, rt, _ = probe.req("/%s/rx/" % mslug, headers=hdr)
        if rx == 200 and "/login" not in urllib.parse.urlparse(rf).path and "login" not in rt[:400].lower():
            bad.append("/%s/rx/" % mslug)
        step("the physio cookie opens nothing else of the record (Now, check-ins, plans, joint log, "
             "caretaker, RxGuard)", ck and not bad, "reached: %s" % bad)
    finally:
        con = sqlite3.connect(gut_db, timeout=10)
        try:
            con.execute("DELETE FROM joint_log WHERE notes LIKE ?", ("%" + tag + "%",))
            con.commit()
        finally:
            con.close()
            keep_owner(gut_db)
    lc, lf, _, _ = b.req(pre + "/signout", data={})
    mc2, _, _, _ = b.req(pre + "/j/me")
    step("sign out works (back at the sign-in page, and signed out)",
         lc == 200 and urllib.parse.urlparse(lf).path == pre + "/login" and mc2 == 401,
         "HTTP %s at %s, me %s" % (lc, lf, mc2))
    c = sqlite3.connect("file:%s?mode=ro" % gut_db, uri=True)
    left = c.execute("SELECT COUNT(*) FROM joint_log WHERE notes LIKE ?", ("%" + tag + "%",)).fetchone()[0]
    c.close()
    step("nothing left behind (the test pain entry removed)", left == 0, "left %s" % left)
    bad = [n for n, ok in RESULTS if not ok]
    print("READY" if not bad else "NOT READY (%d failed)" % len(bad))
    return 0 if not bad else 1


def weight_steps(b, pre, gut_db, tag):
    """The weight-profile checks, inside the member's signed-in session.
    Everything written carries `tag` and is removed by the caller."""
    import time
    wd = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][time.localtime().tm_wday]
    b.req(pre + "/api/prnmeds", js={"name": tag + " weekly"})
    _, _, _, full = b.req(pre + "/api/prnmeds/full")
    mid = next((m["id"] for m in (full or []) if m.get("name") == tag + " weekly"), None)
    sc, _, _, sj = b.req(pre + "/api/schedule", js={"med_id": mid, "slot": "WEEKLY", "weekday": wd,
                                                     "at_time": "12:00", "dose_text": "1 dose"})
    _, _, _, now = b.req(pre + "/api/now")
    wk = next((s for s in (now or {}).get("slots") or [] if s.get("slot") == "WEEKLY"), None)
    row = next((r for r in (wk or {}).get("rows") or [] if r.get("name") == tag + " weekly"), None)
    dc, _, _, dj = b.req(pre + "/api/now/dose", js={"med_id": mid, "sched_id": (row or {}).get("sched_id"),
                                                     "status": "TAKEN", "dose_text": "1 dose"}) if row else (0, 0, 0, None)
    _, _, _, now2 = b.req(pre + "/api/now")
    row2 = next((r for s in (now2 or {}).get("slots") or [] if s.get("slot") == "WEEKLY"
                 for r in s.get("rows") or [] if r.get("name") == tag + " weekly"), None)
    step("a weekly dose scheduled for today shows on the Now page and can be ticked",
         sc == 200 and (sj or {}).get("ok") and row and dc == 200 and (row2 or {}).get("status") == "TAKEN",
         "schedule %s, shown %s, dose %s, status %s" % (sc, bool(row), dc, (row2 or {}).get("status")))
    cc, _, _, cj = b.req(pre + "/api/checkins", js={"kind": "phq2", "answers": {"q1": 0, "q2": 0}, "note": tag})
    cid = (cj or {}).get("id")
    dd, _, _, _ = b.req(pre + "/api/checkins/%s/delete" % cid, js={}) if cid else (0, 0, 0, 0)
    step("a check-in answered (PHQ-2, all zero: no flag), totalled, then removed",
         cc == 200 and cid and (cj or {}).get("total") == 0 and not (cj or {}).get("tell_caretaker")
         and dd == 200, "HTTP %s %s delete %s" % (cc, cj, dd))
    pc, _, _, plans = b.req(pre + "/api/plans")
    step("the plan document is present (a PDF filed under Plans)",
         pc == 200 and any(p.get("has_file") for p in (plans or [])), "HTTP %s, %d plan(s)" % (pc, len(plans or [])))
    return mid


def weight_cleanup(con, tag):
    for (i,) in con.execute("SELECT id FROM prnmeds WHERE name=?", (tag + " weekly",)).fetchall():
        con.execute("DELETE FROM doses WHERE med_id=? OR sched_id IN (SELECT id FROM med_schedule WHERE med_id=?)",
                    (i, i))
        con.execute("DELETE FROM med_schedule WHERE med_id=?", (i,))
        con.execute("DELETE FROM med_salts WHERE med_id=?", (i,))
        con.execute("DELETE FROM prnmeds WHERE id=?", (i,))
    con.execute("DELETE FROM checkins WHERE note=?", (tag,))


def main():
    ap = argparse.ArgumentParser(description="Is a member's copy ready to hand over?")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--base", default=None, help="default: FAMILY_BASE from the member's env")
    ap.add_argument("--pin-file", default="/root/family/first-login.local.txt")
    ap.add_argument("--etc", default="/etc/family")
    ap.add_argument("--srv", default="/srv/family")
    ap.add_argument("--registry", default="/root/family/members.local.json")
    ap.add_argument("--for", dest="for_member", default=None, help="a physio: the member to check against")
    a = ap.parse_args()
    if re.match(r"^k[0-9]{1,3}$", a.slug):
        return kitchen_readiness(a)
    if re.match(r"^p[0-9]{1,3}$", a.slug):
        return physio_readiness(a)
    if not re.match(r"^m[0-9]{1,3}$", a.slug):
        print("--slug must look like m1 (or k1 for a kitchen member, p1 for a physio).")
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
        # ------------------------------------------------------------ 11 (weight profile)
        if env.get("FAMILY_PROFILE") == "weight":
            weight_steps(b, pre, gut_db, tag)
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
            if env.get("FAMILY_PROFILE") == "weight":
                weight_cleanup(con, tag)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_a.py -- Family Edition Phase A: isolation, caretaker, stamping.

    python3 -B family/test_family_a.py gutlog/app.py

The app.py given is the OWNER's GutLog (the build under test for the Family
page). Members always run from a code tree built out of this repository by
build_code.py. FAMILY_CODE may point at a copy of family/ (the negative
control does that to break one module at a time).

Isolation is the headline, so most of this suite is attacks: another
member's path, cookie, feed token, status token and sign-in ticket; path
traversal; a caretaker with access switched off; a member ticket presented
to the owner. Synthetic names only ("Member A", "Medicine A").
Python 3.9.
"""
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def owner_seed_names():
    """The owner's own seed names from the repo's gitignored regimen file, if
    this checkout has one. Never printed."""
    p = os.path.join(famtest.REPO, "gutlog", "regimen.local.json")
    names = set()
    try:
        with open(p, encoding="utf-8") as fh:
            j = json.load(fh)
        for k in ("prn_seed", "doctor_seed", "course_chips"):
            for v in j.get(k) or []:
                if isinstance(v, str) and len(v) >= 4:
                    names.add(v)
    except (OSError, ValueError):
        pass
    return names


def db_text(path):
    con = sqlite3.connect(path)
    out = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in con.execute("SELECT * FROM \"%s\"" % t).fetchall():
            out.append(" ".join(str(x) for x in row if x is not None))
    con.close()
    return "\n".join(out)


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("A00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


def run(rig):
    owner = rig.owner_client()
    owner.post("/api/prnmeds", {"name": "Medicine A Owner"}, ctype="json")
    m1 = rig.member_client("m1")
    m2 = rig.member_client("m2")

    # ---------------------------------------------------------------- A11
    t = db_text(rig.db("m1", "gut"))
    con = sqlite3.connect(rig.db("m1", "gut"))
    counts = dict((tb, con.execute("SELECT COUNT(*) FROM %s" % tb).fetchone()[0]) for tb in
                  ("prnmeds", "med_schedule", "doctors", "plans", "ft_items", "rec_docs", "rec_labs",
                   "meals", "doses"))
    lib = con.execute("SELECT COUNT(*) FROM library").fetchone()[0]
    dishes = con.execute("SELECT COUNT(*) FROM dishes").fetchone()[0]
    con.close()
    check("A11 a family copy starts with no owner data -- no medicines, schedule, doctors, plans, "
          "food-test plan, records, meals or doses", all(v == 0 for v in counts.values()), counts)
    check("A11 a family copy starts with no owner data -- the food library and v3.35.0 dishes are there",
          lib > 40 and dishes > 0, "library %d dishes %d" % (lib, dishes))
    leaked = [n for n in owner_seed_names() | {"Medicine A Owner"} if n in t]
    check("A11 a family copy starts with no owner data -- no owner seed name anywhere in its database",
          not leaked, "%d owner name(s) found" % len(leaked))
    r = m1.get("/m1/api/prnmeds")
    check("A11 a family copy starts with no owner data -- the running copy serves no medicine",
          r.status == 200 and r.json() == [], "%s %s" % (r.status, "non-empty" if r.json() else r.text[:60]))
    r = m1.get("/m1/api/mealcards")
    cards = (r.json() or {}).get("cards") if r.json() is not None else None
    check("A11 a family copy starts with no owner data -- no meal cards", r.status == 200 and not cards,
          "%s %s" % (r.status, r.text[:120]))
    r = m1.get("/m1/api/mirror")
    check("A11 a family copy starts with no owner data -- no Health Mirror warning",
          (r.json() or {}).get("ok") is True, r.text[:120])
    r = famtest.Client(rig.front_url).get("/m1/login")
    # The page names its member, and the caretaker only as whom to ask/call (by design,
    # 25-Sep-2026); the owner's own diary title and full name appear nowhere.
    lt = html.unescape(r.text).replace("Ask Manoj for your own link", "")
    check("A11 a family copy starts with no owner data -- the sign-in page names only its member",
          r.status == 200 and "Manoj" not in lt and "Personal health diary" not in lt
          and "Member A's health diary" in lt, r.text[:200])
    r = m1.get("/m1/api/family/has")
    check("A11 a family copy starts with no owner data -- no Family page inside a member's copy",
          (r.json() or {}).get("n") == 0 and m1.get("/m1/family").status == 404, r.text[:100])

    # ---------------------------------------------------------------- A17
    from datetime import date, timedelta
    yday = (date.today() - timedelta(days=1)).isoformat()
    ids = {}
    for label, card, hm in (("11:00", "Before lunch", "11:00"), ("13:00", "Before lunch", "13:00"),
                            ("old name", "Morning", "10:30")):
        r = owner.post("/api/mealcards/log", {"card": card, "day": yday, "mtime": hm}, ctype="json")
        ids[label] = (r.json() or {}).get("id")
    con = sqlite3.connect(rig.owner_env["GUTLOG_DB"])
    slots = dict((lb, (con.execute("SELECT slot FROM meals WHERE id=?", (i,)).fetchone() or [None])[0])
                 for lb, i in ids.items())
    con.close()
    check("A17 meal cards line up with the slots: 'Before lunch' files by the clock, an old card name still logs",
          slots == {"11:00": "Mid-morning", "13:00": "Lunch", "old name": "Mid-morning"}, slots)

    # ---------------------------------------------------------------- A07
    for label, port, path in (("no prefix", rig.ports("m1")["gut"], "/api/now"),
                              ("another member's prefix", rig.ports("m1")["gut"], "/m2/api/now"),
                              ("rx port, gut path", rig.ports("m1")["rx"], "/m1/api/now"),
                              ("encoded traversal", rig.ports("m1")["gut"], "/m1/%2e%2e/m2/api/now"),
                              ("dot-dot traversal", rig.ports("m1")["gut"], "/m1/../m2/api/now"),
                              ("owner root", rig.ports("m1")["gut"], "/healthz")):
        c = famtest.Client("http://127.0.0.1:%d" % port)
        c.jar = m1.jar
        c.op = m1.op
        r = c.get(path)
        check("A07 each process answers only its own prefix -- %s" % label,
              r.status == 404 or (r.status in (301, 302, 308) and "/m2/" not in (r.headers.get("Location") or "")
                                  and label.startswith(("encoded", "dot"))),
              "%s -> %s %s" % (path, r.status, r.headers.get("Location")))
    r = m1.get("/m1/rec/doc/1/..%2f..%2f..%2fhealth.db")
    check("A07 each process answers only its own prefix -- a file route cannot walk out",
          r.status == 404, r.status)
    r = m1.get("/m2/api/now")
    check("A07 each process answers only its own prefix -- m1's session gets nothing from m2",
          r.status in (302, 401) and "application/json" not in (r.headers.get("Content-Type") or ""),
          "%s %s" % (r.status, r.text[:80]))

    # ---------------------------------------------------------------- A08
    ck = dict((n, v) for n, v, _p in m1.cookies())
    forged = famtest.Client(rig.front_url)
    r = forged.get("/m2/api/now", headers={"Cookie": "fam_m2_gut=" + ck.get("fam_m1_gut", "")})
    check("A08 a member's cookie opens nothing else -- m1's cookie renamed for m2",
          r.status in (302, 401) and '"slots"' not in r.text, "%s %s" % (r.status, r.text[:80]))
    oc = famtest.Client("http://127.0.0.1:%d" % rig.owner_port)
    r = oc.get("/api/now", headers={"Cookie": "session=" + ck.get("fam_m1_gut", "")})
    check("A08 a member's cookie opens nothing else -- m1's cookie at the owner's GutLog",
          r.status in (302, 401) and '"slots"' not in r.text, "%s %s" % (r.status, r.text[:80]))
    # m1's own ring: gut -> rx, then ask m1's RxGuard to vouch for 'gutlog'
    r = m1.get("/m1/rx/", follow=True)
    signed_rx = r.status == 200 and "/login" not in r.url
    r = m1.get("/m1/rx/sso/vouch?to=gutlog&next=/api/now&hops=1")
    loc = r.headers.get("Location") or ""
    tok = (parse_qs(urlparse(loc).query).get("t") or [""])[0]
    check("A08 a member's cookie opens nothing else -- m1's own ring mints a ticket (precondition)",
          signed_rx and bool(tok), "rx signed=%s loc=%s" % (signed_rx, loc[:120]))
    oc2 = famtest.Client("http://127.0.0.1:%d" % rig.owner_port)
    r = oc2.get("/sso/in?t=%s&next=/api/now" % tok, follow=True)
    check("A08 a member's sign-in ticket never opens the owner's GutLog (/api/now)",
          '"slots"' not in r.text and "/login" in r.url, "%s %s" % (r.url, r.text[:80]))
    r = famtest.Client(rig.front_url).get("/m2/sso/in?t=%s&next=/api/now" % tok, follow=True)
    check("A08 a member's sign-in ticket never opens another member's GutLog",
          '"slots"' not in r.text, "%s %s" % (r.url, r.text[:80]))

    # ---------------------------------------------------------------- A09
    t1, t2 = rig.secret("m1", "feed.token"), rig.secret("m2", "feed.token")
    c = famtest.Client(rig.front_url)
    r_own = c.get("/m1/api/feed/stack", headers={"Authorization": "Bearer " + t1})
    r_other = c.get("/m1/api/feed/stack", headers={"Authorization": "Bearer " + t2})
    r_owner = c.get("/m1/api/feed/stack", headers={"Authorization": "Bearer " + (
        open(rig.owner_env["GUTLOG_FEED_TOKEN_FILE"]).read().strip()
        if os.path.exists(rig.owner_env["GUTLOG_FEED_TOKEN_FILE"]) else "x" * 64)})
    check("A09 a feed token works only for its own member",
          r_own.status == 200 and r_other.status == 401 and r_owner.status == 401 and t1 != t2,
          "own %s other %s owner %s" % (r_own.status, r_other.status, r_owner.status))

    # ---------------------------------------------------------------- A06
    s1, s2 = rig.secret("m1", "status.token"), rig.secret("m2", "status.token")
    r0 = c.get("/m1/api/care/status")
    rb = c.get("/m1/api/care/status", headers={"Authorization": "Bearer " + s2})
    ra = c.get("/m1/api/care/status", headers={"Authorization": "Bearer " + s1})
    j = ra.json() or {}
    # "flag" (GutLog v3.40.0): a same-day check-in flag, a boolean, never an answer.
    allowed = {"ok", "access", "last_entry", "doses", "rx", "bp", "days_since_report", "flag"}
    check("A06 status endpoint: bearer only, this member's token only, summary fields only",
          r0.status == 401 and rb.status == 401 and ra.status == 200 and set(j) <= allowed
          and j.get("access") == "on" and "doses" in j and "Member" not in ra.text,
          "none %s other %s own %s keys %s" % (r0.status, rb.status, ra.status, sorted(j)))

    # ---------------------------------------------------------------- A01
    fam = owner.get("/family")
    check("A01 the Family page lists each member with their summary, read live",
          fam.status == 200 and "Member A" in fam.text and "Member B" in fam.text
          and "Doses today" in fam.text and "Open as caretaker" in fam.text
          and "/family/open/m1" in fam.text, "%s %s" % (fam.status, fam.text[:200]))
    r = owner.get("/api/family/has")
    check("A01 the Family page lists each member with their summary, read live -- Now-tab count",
          (r.json() or {}).get("n") == 2, r.text[:80])

    # ---------------------------------------------------------------- A02
    def ticket_url(slug="m1"):
        r = owner.get("/family/open/" + slug)
        return r.headers.get("Location") or ""
    u = ticket_url()
    care = famtest.Client(rig.front_url)
    r = care.get(u, follow=True)
    me = care.get("/m1/api/care/me").json() or {}
    check("A02 open as caretaker: the ticket signs the caretaker in, as caretaker",
          u.startswith(rig.front_url + "/m1/care/in?t=") and me.get("care") == "Manoj",
          "%s %s" % (u[:80], me))
    again = famtest.Client(rig.front_url)
    r = again.get(u, follow=True)
    me2 = again.get("/m1/api/care/me")
    check("A02 open as caretaker: a ticket works once", me2.status != 200 or not (me2.json() or {}).get("care"),
          "%s %s" % (me2.status, me2.text[:80]))
    u2 = ticket_url("m1")
    tok = u2.split("t=", 1)[1].split("&", 1)[0]
    other = famtest.Client(rig.front_url)
    other.get("/m2/care/in?t=%s&next=/" % tok, follow=True)
    me3 = other.get("/m2/api/care/me")
    check("A02 open as caretaker: m1's ticket means nothing to m2",
          me3.status != 200 or not (me3.json() or {}).get("care"), "%s %s" % (me3.status, me3.text[:80]))
    other.get("/m1/rx/care/in?t=%s&next=/" % tok, follow=True)
    r = other.get("/m1/rx/", follow=True)
    check("A02 open as caretaker: a GutLog ticket does not open RxGuard directly",
          "/login" in r.url or r.status in (401, 403), r.url)

    # ---------------------------------------------------------------- A03
    r = care.post("/m1/api/prnmeds", {"name": "Medicine A"}, ctype="json")
    r2 = m1.get("/m1/care")
    check("A03 caretaker changes are tagged and shown to the member",
          r.status == 200 and "by caretaker (Manoj)" in r2.text and "Added a medicine" in r2.text
          and re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d IST", r2.text) is not None,
          "%s / %s" % (r.status, r2.text[:300]))
    m1.post("/m1/api/prnmeds", {"name": "Medicine B"}, ctype="json")
    cnt = len(re.findall(r"by caretaker", m1.get("/m1/care").text))
    check("A03 caretaker changes are tagged and shown to the member -- the member's own entries are not",
          cnt == 1, cnt)

    # ---------------------------------------------------------------- A04
    ra = care.get("/m1/account")
    rs = care.post("/m1/care/switch", {"on": "0"})
    still = care.get("/m1/api/care/me").json() or {}
    check("A04 the caretaker cannot touch the member's own credentials or switch",
          ra.status == 403 and rs.status == 403 and still.get("access") is True,
          "account %s switch %s access %s" % (ra.status, rs.status, still.get("access")))

    # ---------------------------------------------------------------- A13
    r = care.get("/m1/rx/", follow=True)
    rxc = care.post("/m1/rx/profile", {"age": "63"})
    log = m1.get("/m1/care").text
    check("A13 a caretaker stays a caretaker across the member's apps",
          r.status == 200 and "/login" not in r.url and "RxGuard" in log,
          "%s %s rx-post %s" % (r.status, r.url, rxc.status))
    r = m2.get("/m2/fit/", follow=True)
    check("A13 the member's own ring signs them into their other apps",
          r.status == 200 and "/login" not in r.url and "/setup" not in r.url, r.url)

    # ---------------------------------------------------------------- A05
    m1.post("/m1/care/switch", {"on": "0"})
    r = care.get("/m1/api/now")
    r_after = care.get("/m1/api/care/me")
    u3 = ticket_url()
    fresh = famtest.Client(rig.front_url)
    fresh.get(u3, follow=True)
    r_new = fresh.get("/m1/api/care/me")
    st = c.get("/m1/api/care/status", headers={"Authorization": "Bearer " + s1}).json() or {}
    fam = owner.get("/family")
    check("A05 switched off means off -- the caretaker's session is refused and cleared",
          r.status == 403 and r_after.status in (302, 401, 403), "%s %s" % (r.status, r_after.status))
    check("A05 switched off means off -- a new ticket is refused",
          r_new.status != 200 or not (r_new.json() or {}).get("care"), r_new.status)
    check("A05 switched off means off -- the status endpoint says off and nothing else",
          st == {"ok": True, "access": "off"}, st)
    check("A05 switched off means off -- the Family page shows access off",
          "access off" in fam.text.lower() and fam.text.count("Open as caretaker") == 1, fam.text[:300])
    rx_care = care.get("/m1/rx/api/feed/status")
    check("A05 switched off means off -- in RxGuard too",
          care.get("/m1/rx/meds").status in (302, 401, 403), rx_care.status)
    m1.post("/m1/care/switch", {"on": "1"})
    back = famtest.Client(rig.front_url)
    back.get(ticket_url(), follow=True)
    check("A05 switched off means off -- and on again works",
          (back.get("/m1/api/care/me").json() or {}).get("care") == "Manoj", "")

    # ---------------------------------------------------------------- A12
    def home_page(cl, slug):
        page = cl.get("/%s/" % slug, follow=True)
        if "/welcome" in page.url:
            cl.post("/%s/welcome" % slug, {"name": "Member", "age": "63", "cond": "ibs"})
            page = cl.get("/%s/" % slug, follow=True)
        return page

    for slug in ("m1", "m2"):
        r = famtest.Client(rig.front_url).get("/%s/manifest.webmanifest" % slug)
        mj = r.json() or {}
        sw = famtest.Client(rig.front_url).get("/%s/sw.js" % slug)
        page = home_page(rig.member_client(slug), slug)
        check("A12 each member's copy installs as its own app -- %s manifest" % slug,
              mj.get("scope") == "/%s/" % slug and mj.get("start_url", "").startswith("/%s/" % slug)
              and mj.get("id") == "/%s/" % slug and all(i.get("src", "").startswith("/%s/" % slug)
                                                         for i in mj.get("icons") or [{}])
              and "Member" in mj.get("name", ""), mj)
        check("A12 each member's copy installs as its own app -- %s service worker scope" % slug,
              sw.headers.get("Service-Worker-Allowed") == "/%s/" % slug
              and ("register('/%s/sw.js', { scope: '/%s/' })" % (slug, slug)) in page.text,
              "%s | %s" % (sw.headers.get("Service-Worker-Allowed"),
                           re.findall(r"register\([^)]*\)", page.text)[:1]))

    # ---------------------------------------------------------------- A14
    page = home_page(m1, "m1")
    bare = re.findall(r"""['"`](/(?:api|login|logout|foodtest|plans|nutrition|rec|scan|account|export|file)[/'"`?])""",
                      page.text)
    hosts = re.findall(r"https://(?:rx|fit|health)\.dr-manoj\.in", page.text)
    check("A14 the page under the prefix links only inside it",
          page.status == 200 and "tab-now" in page.text and not bare and not hosts
          and "/m1/api/" in page.text and "famBar" in page.text,
          "status %s bare %s hosts %s" % (page.status, bare[:5], hosts[:3]))

    # ---------------------------------------------------------------- A10
    rc, out = rig.stamp("m1", "Member A again", "gut", expect_ok=False)
    k_after = rig.secret("m1", "care.key")
    check("A10 stamping twice refuses, and the member is untouched",
          rc == 2 and "is already a member" in out and m1.get("/m1/api/care/me").status == 200,
          "rc %s out %s" % (rc, out[-200:]))
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "stamp_member.py"),
                        "--no-system", "--root", rig.root, "--code", rig.code, "--slug", "m2", "--disable"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    reg = json.load(open(os.path.join(rig.root, "root", "family", "members.local.json")))
    m2e = [m for m in reg["members"] if m["slug"] == "m2"][0]
    check("A10 --disable stops a member and keeps the data",
          r.returncode == 0 and m2e["enabled"] is False and os.path.exists(rig.db("m2", "gut"))
          and "Member B" not in owner.get("/family").text, r.stdout.decode()[-200:])
    _ = k_after

    # ---------------------------------------------------------------- A16
    import shutil
    import tempfile
    import build_code
    src = tempfile.mkdtemp(prefix="famrx_")
    try:
        shutil.copytree(os.path.join(famtest.REPO, "rxguard"), os.path.join(src, "rxguard"),
                        ignore=shutil.ignore_patterns("*.db*", "*.bak*", "__pycache__", "*.local.json", "cache"))
        with open(os.path.join(src, "rxguard", "knowledge", "drugs.local.json"), "w") as fh:
            json.dump({"drugs": {"medicine_a": {"class": "Class A", "strength_logged": "145",
                                                "source": "a label -- approved by owner"}},
                       "synonyms": {}}, fh)
        out = os.path.join(src, "tree")
        ok, why = True, ""
        try:
            build_code.build(os.path.join(famtest.REPO, "gutlog"), os.path.join(src, "rxguard"),
                             os.path.join(famtest.REPO, "fitlog"), famtest.fam_src(), out)
            got = json.load(open(os.path.join(out, "rxguard", "knowledge", "drugs.local.json")))
            ok = "strength_logged" not in json.dumps(got) and "medicine_a" in got["drugs"]
            why = json.dumps(got)[:200]
        except SystemExit as exc:
            ok, why = False, "build refused: %s" % exc
        check("A16 the approved overlay reaches a family copy without the owner's logged strengths",
              ok, why)
    finally:
        shutil.rmtree(src, ignore_errors=True)

    # ---------------------------------------------------------------- A15
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "family_backup.py"),
                        "--root", rig.root], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    bdir = os.path.join(rig.root, "root", "backups", "family")
    have = sorted(os.listdir(os.path.join(bdir, "m1"))) if os.path.isdir(os.path.join(bdir, "m1")) else []
    need = ("gutlog_health-", "rxguard_rxguard-", "fitlog_fitlog-", "care-", "files-")
    check("A15 the backup copies every member database, verified, and the files",
          r.returncode == 0 and all(any(h.startswith(n) for h in have) for n in need)
          and os.path.isdir(os.path.join(bdir, "m2")) and "(ok)" in out,
          "rc %s have %s out %s" % (r.returncode, have, out[-300:]))


if __name__ == "__main__":
    sys.exit(main())

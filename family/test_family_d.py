#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_d.py -- Family Edition Phase D: kitchen members, self-publishing,
attribution.

    python3 -B family/test_family_d.py gutlog/app.py

The scratch family (famtest.Rig) plus two kitchen members stamped with the
real stamp tool (--kitchen-only). Synthetic names ("Member C", "Member D")
and recipes; no medicine is named. Every rule the brief states is a check
here, and each one has a mutation in new_assertions_family_d.json that
makes it fail. Python 3.9.
"""
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
import test_family_c as TC  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")
FORBIDDEN_COLS = TC.FORBIDDEN_COLS


def file_pin(path, slug):
    pin = None
    for line in open(path, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        m = re.match(r"PIN (\d{6})$", f[-1]) if f else None
        if f and f[0] == slug and m:
            pin = m.group(1)
    return pin


def stamp_tool(rig, *args):
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "stamp_member.py"), "--no-system",
                        "--root", rig.root, "--code", rig.code] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def publish(cl, pre, name, ings, method, servings=2, grp="Other", extra=None, text=None):
    """A kitchen member: a pasted draft, then Publish. (pre = /kitchen/k1)"""
    r = cl.post(pre + "/j/drafts", {"text": text or ("%s\n%s" % (name, "\n".join(i["item"] for i in ings)))},
                ctype="json")
    did = (r.json() or {}).get("id")
    body = {"name": name, "grp": grp, "servings": servings, "ingredients": ings, "method": method}
    body.update(extra or {})
    return did, cl.post("%s/j/drafts/%s/publish" % (pre, did), body, ctype="json")


def gut_confirm(cl, pre, name, ings, method, servings=2, extra=None):
    """A GutLog user (owner or full member) confirms a pasted draft."""
    r = cl.post(pre + "/api/kitchen/drafts", {"text": "a recipe"}, ctype="json")
    did = (r.json() or {}).get("id")
    body = {"name": name, "grp": "Other", "servings": servings, "ingredients": ings, "method": method}
    body.update(extra or {})
    return did, cl.post("%s/api/kitchen/drafts/%s/confirm" % (pre, did), body, ctype="json")


def kapi(rig, token, path, data=None):
    """The Kitchen's bearer API directly, as a GutLog copy would call it."""
    c = famtest.Client(rig.front_url)
    h = {"Authorization": "Bearer " + token}
    if data is None:
        return c.get("/kitchen" + path, headers=h)
    return c.post("/kitchen" + path, data, headers=h, ctype="json")


def cookie_of(cl, name="kitchen"):
    return next((v for n, v, _p in cl.cookies() if n == name), None)


def run(rig):
    owner = rig.owner_client()
    m1, m2 = rig.member_client("m1"), rig.member_client("m2")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs", "height_cm": "160"})
    m2.post("/m2/welcome", {"name": "Member B", "age": "68", "cond": "general", "height_cm": "155"})
    tok = rig.kitchen_tokens()
    pwf = os.path.join(rig.work, "first-login.local.txt")
    k1, k2 = "/kitchen/k1", "/kitchen/k2"

    # ---------------------------------------------------------------- D01 stamping
    rc, out = rig.stamp_kitchen("k1", "Member C", extra=["--password-file", pwf])
    rig.pw["k1"] = file_pin(pwf, "k1") or ""
    rc2, out2 = rig.stamp_kitchen("k2", "Member D")
    reg = json.load(open(os.path.join(rig.root, "root", "family", "members.local.json")))
    tok = rig.kitchen_tokens()
    c = TC.kdb(rig)
    krows = [dict(r) for r in c.execute("SELECT * FROM kmembers ORDER BY slug").fetchall()]
    c.close()
    line = [ln for ln in open(pwf, encoding="utf-8") if ln.startswith("k1\t")]
    check("D01 a kitchen member is an account inside the Kitchen only: no user folder, env, registry entry or API token",
          rc == 0 and rc2 == 0 and [r["slug"] for r in krows] == ["k1", "k2"] and krows[0]["name"] == "Member C"
          and not os.path.exists(os.path.join(rig.root, "srv", "family", "k1"))
          and not os.path.exists(os.path.join(rig.root, "etc", "family", "k1.env"))
          and not any(m["slug"] == "k1" for m in reg["members"])
          and "k1" in tok["capture"] and "k1" not in tok["api"]
          and os.path.exists(os.path.join(rig.kitchen_dir, "members", "k1", "auth.db")),
          "%s %s\n%s" % (rc, rc2, out[-300:]))
    check("D01 the first-login PIN goes to the root-only file, with the member's address, and is never printed",
          len(line) == 1 and re.match(r"^k1\tMember C\t%s/kitchen/k1/\tPIN \d{6}\n$" % re.escape(rig.front_url), line[0])
          and "PIN (shown once" not in out and rig.pw["k1"] and "PIN (shown once" in out2, (line, out[-200:]))
    rc3, out3 = rig.stamp_kitchen("k1", "Member C again", expect_ok=False)
    rc4, out4 = stamp_tool(rig, "--list")
    check("D01 stamping twice refuses; --list shows kitchen members",
          rc3 == 2 and "already a kitchen member" in out3
          and re.search(r"^k1\s+kitchen\s+on\s.*Member C$", out4.replace("\r", ""), re.M),
          "%s %s\n%s" % (rc3, out3[-120:], out4))

    # ---------------------------------------------------------------- D02 sign-in rules
    anon = famtest.Client(rig.front_url)
    r = anon.get(k1 + "/login")
    tu = html.unescape(r.text)
    mani = anon.get(k1 + "/manifest.webmanifest")
    mj = mani.json() or {}
    icon = anon.get(k1 + "/icon-192.png")
    check("D02 the sign-in page names the member and app; numeric PIN, eye; installs as Family Kitchen with the Kitchen icon",
          r.status == 200 and "Member C \u2014 Family Kitchen" in tu and "inputmode='numeric'" in r.text
          and "maxlength='6'" in r.text and "id='eye'" in r.text and "id='pk'" in r.text
          and (k1 + "/manifest.webmanifest") in r.text and mj.get("name") == "Family Kitchen"
          and mj.get("scope") == k1 + "/" and mj.get("start_url") == k1 + "/"
          and all(i["src"].startswith(k1 + "/") for i in mj.get("icons") or [{}])
          and icon.status == 200 and icon.body[:8] == b"\x89PNG\r\n\x1a\n"
          and "apple-mobile-web-app-title' content='Family Kitchen'" in r.text,
          "%s %s %s %s" % (r.status, tu[:120], mj, icon.status))
    wrong = famtest.Client(rig.front_url).post(k1 + "/login", {"pin": "000001"})
    check("D02 a wrong PIN counts down", wrong.status == 401 and "Wrong PIN \u2014 4 tries left" in html.unescape(wrong.text),
          "%s %s" % (wrong.status, html.unescape(wrong.text)[:200]))
    kc1 = rig.kitchen_client("k1")
    kc2 = rig.kitchen_client("k2")
    me1 = kc1.get(k1 + "/j/me").json() or {}
    ck = [x for x in kc1.cookies() if x[0] == "kitchen"]
    check("D02 the right PIN signs in for a year, on the member's own path only",
          me1.get("slug") == "k1" and me1.get("name") == "Member C" and ck and ck[0][2] == k1 + "/",
          "%s %s" % (me1, ck))
    weak = kc2.post(k2 + "/pin/change", {"old": rig.pw["k2"], "new": "123456"}, ctype="json")
    good = kc2.post(k2 + "/pin/change", {"old": rig.pw["k2"], "new": "246813"}, ctype="json")
    rig.pw["k2"] = "246813"
    again = famtest.Client(rig.front_url)
    again.post(k2 + "/login", {"pin": "246813"})
    check("D02 the PIN can be changed; all-same or straight-run PINs are refused",
          weak.status == 400 and good.status == 200 and (again.get(k2 + "/j/me").json() or {}).get("slug") == "k2",
          "%s %s" % (weak.status, good.status))
    for i in range(5):
        last = famtest.Client(rig.front_url).post(k2 + "/login", {"pin": "%06d" % (900000 + i)})
    right = famtest.Client(rig.front_url).post(k2 + "/login", {"pin": rig.pw["k2"]})
    still = kc1.get(k1 + "/j/me")
    lt = html.unescape(last.text)
    check("D02 five wrong PINs pause sign-in (15 min, IST, whom to call); even the right PIN is refused; k1 is untouched",
          last.status == 401 and "Paused until" in lt and "IST" in lt and "call " in lt
          and right.status == 401 and "Paused until" in html.unescape(right.text) and still.status == 200,
          "%s %s | right %s | k1 %s" % (last.status, lt[:160], right.status, still.status))
    rc, out = stamp_tool(rig, "--slug", "k2", "--reset-pin")
    m = re.search(r"new PIN \(shown once, written nowhere\): (\d{6})", out)
    rig.pw["k2"] = m.group(1) if m else ""
    kc2b = rig.kitchen_client("k2")
    old_dead = kc2.get(k2 + "/j/me")
    check("D02 --reset-pin clears the pause and signs every device out",
          rc == 0 and m and (kc2b.get(k2 + "/j/me").json() or {}).get("slug") == "k2" and old_dead.status == 401,
          "%s %s | old %s" % (rc, out[-160:], old_dead.status))
    kc2 = kc2b
    c = sqlite3.connect(os.path.join(rig.kitchen_dir, "members", "k2", "auth.db"))
    lg = [r[0] for r in c.execute("SELECT result FROM auth_log ORDER BY id").fetchall()]
    c.close()
    check("D02 every attempt is logged (wrong, locked, refused, reset)",
          any("wrong PIN" in x for x in lg) and any(x.startswith("locked for 15") for x in lg)
          and "refused: locked" in lg and any("PIN reset" in x for x in lg), lg[-6:])

    # ---------------------------------------------------------------- D13 Face ID offered
    oc = kc1.get(k1 + "/passkey/offer?next=%2F")
    rb = kc1.post(k1 + "/passkey/register/begin", {}, ctype="json").json() or {}
    lb = famtest.Client(rig.front_url).post(k1 + "/passkey/login/begin", {}, ctype="json")
    check("D13 Face ID / Touch ID is offered after the PIN sign-in, for this host; none set up yet",
          oc.status == 200 and "id='yes'" in oc.text and rb.get("ok") and (rb.get("rp") or {}).get("id") == "127.0.0.1"
          and (rb.get("rp") or {}).get("name") == "Family Kitchen" and lb.status == 404, "%s %s %s" % (oc.status, rb, lb.status))

    # ---------------------------------------------------------------- D03 draft -> publish -> attribution
    ctok = tok["capture"]["k1"]["token"]
    st, cj = TC.capture(rig, "k1", ctok, {"text": "Test paneer tinda\n200 g paneer\n2 tinda\nCook."})
    did = (cj.get("drafts") or [None])[0]
    before = TC.kdb(rig).execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    in_m1 = [x["name"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", [])]
    in_k2 = [x["name"] for x in (kc2.get(k2 + "/j/recipes").json() or {}).get("recipes", [])]
    in_api = [x["name"] for x in (kapi(rig, tok["api"]["m2"]["token"], "/api/recipes").json() or {}).get("recipes", [])]
    mine = kc1.get(k1 + "/j/drafts").json() or {}
    other = kc2.get("%s/j/drafts/%s" % (k2, did))
    check("D03 a capture lands as a draft for its sender only; nothing is in the pool before they publish",
          st == 200 and did and any(d["id"] == did for d in mine.get("drafts") or [])
          and "Test paneer tinda" not in in_m1 + in_k2 + in_api and other.status == 404
          and before == TC.kdb(rig).execute("SELECT COUNT(*) FROM recipes").fetchone()[0],
          "%s draft %s m1 %s k2 %s api %s other %s" % (st, did, in_m1, in_k2, in_api, other.status))
    ings = [{"item": "paneer", "qty": 200, "unit": "g"}, {"item": "tinda", "qty": 2, "unit": "medium"},
            {"item": "ghee", "qty": 1, "unit": "tsp"}]
    pr = kc1.post("%s/j/drafts/%s/publish" % (k1, did), {"name": "Test paneer tinda", "grp": "Sabzi", "servings": 2,
                                                         "ingredients": ings, "method": ["Cook."]}, ctype="json")
    rid = (pr.json() or {}).get("id")
    lst = (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes") or []
    row = next((x for x in lst if x["id"] == rid), {})
    card = (m1.get("/m1/api/kitchen/recipe/%s" % rid).json() or {}).get("recipe") or {}
    kcard = (kc2.get("%s/j/recipe/%s" % (k2, rid)).json() or {}).get("recipe") or {}
    page = m1.get("/m1/kitchen").text
    check("D03 Publish puts it in the pool at once, credited to the contributor on the list, the card and every view",
          pr.status == 200 and rid and row.get("added_by") == "Member C" and card.get("added_by") == "Member C"
          and kcard.get("added_by") == "Member C" and row.get("status") == "published"
          and row.get("added_date") == date.today().isoformat() and "'Recipe by '" in page,
          "%s row %s card %s kcard %s" % (pr.status, row.get("added_by"), card.get("added_by"), kcard.get("added_by")))
    lg = m1.post("/m1/api/kitchen/recipe/%d/log" % rid, {"servings": 1}, ctype="json")
    gc = sqlite3.connect(rig.db("m1", "gut"))
    items = gc.execute("SELECT items FROM meals WHERE id=?", ((lg.json() or {}).get("id"),)).fetchone()
    gc.close()
    lo = owner.post("/api/kitchen/recipe/%d/log" % rid, {"servings": 1}, ctype="json")
    oc_ = sqlite3.connect(rig.owner_env["GUTLOG_DB"])
    oitems = oc_.execute("SELECT items FROM meals WHERE id=?", ((lo.json() or {}).get("id"),)).fetchone()
    oc_.close()
    check("D03 a logged serving carries the attribution: '<dish> \u00b7 recipe by <Name>' (member and owner)",
          lg.status == 200 and items and "recipe by Member C" in items[0] and "Test paneer tinda" in items[0]
          and lo.status == 200 and oitems and "recipe by Member C" in oitems[0], "%s %s | %s %s" % (lg.status, items, lo.status, oitems))
    # a photo capture and a web capture
    st_p, pj = TC.capture(rig, "k1", ctok, {"text": "IMG_0009.jpeg"}, [("IMG_0009.jpeg", TC.PNG, "image/png")])
    pdid = (pj.get("drafts") or [None])[0]
    pp = kc1.post("%s/j/drafts/%s/publish" % (k1, pdid), {"name": "Test photo halwa", "grp": "Sweet", "servings": 4,
                                                          "ingredients": [{"item": "sooji", "qty": 1, "unit": "cup"}],
                                                          "method": ["Roast."]}, ctype="json")
    prid = (pp.json() or {}).get("id")
    pcard = (m1.get("/m1/api/kitchen/recipe/%s" % prid).json() or {}).get("recipe") or {}
    patt = m1.get("/m1/api/kitchen/recipe/%s/attach" % prid)
    katt = kc2.get("%s/j/recipe/%s/attach" % (k2, prid))
    st_l, lj = TC.capture(rig, "k1", ctok, {"text": "https://www.youtube.com/watch?v=abc123"})
    ldid = (lj.get("drafts") or [None])[0]
    lp = kc1.post("%s/j/drafts/%s/publish" % (k1, ldid), {"name": "Test link kheer", "grp": "Sweet", "servings": 4,
                                                          "ingredients": [{"item": "rice", "qty": 0.5, "unit": "cup"}],
                                                          "method": ["Simmer."]}, ctype="json")
    lcard = (kc2.get("%s/j/recipe/%s" % (k2, (lp.json() or {}).get("id"))).json() or {}).get("recipe") or {}
    check("D03 a photo capture shows 'Shared by' with the original photo viewable; a web capture names the site with its link",
          pp.status == 200 and pcard.get("has_photo") is True and patt.status == 200 and katt.status == 200
          and katt.body[:4] == b"\x89PNG" and lp.status == 200 and lcard.get("source_site") == "YouTube"
          and lcard.get("source_url", "").startswith("https://www.youtube.com/") and lcard.get("added_by") == "Member C",
          "%s photo %s att %s/%s | link %s %s" % (pp.status, pcard.get("has_photo"), patt.status, katt.status,
                                                   lp.status, lcard.get("source_site")))

    # ---------------------------------------------------------------- D04 duplicates and versions
    _d, dup = publish(kc2, k2, "Test Paneer Tinda!", ings, ["Cook it my way."])
    dj = dup.json() or {}
    _d2, ver = publish(kc2, k2, "Test Paneer Tinda!", ings, ["Cook it my way."], extra={"force": True, "as_version": True})
    vid = (ver.json() or {}).get("id")
    vcard = (kc1.get("%s/j/recipe/%s" % (k1, vid)).json() or {}).get("recipe") or {}
    base_row = next((x for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", []) if x["id"] == rid), {})
    check("D04 a duplicate is caught before publishing, naming whose it is; 'publish as my version' keeps both side by side",
          dup.status == 409 and (dj.get("duplicate") or {}).get("id") == rid and dj["duplicate"].get("by") == "Member C"
          and ver.status == 200 and vid and vcard.get("variant_of") == rid
          and [v["by"] for v in vcard.get("versions") or []] == ["Member C", "Member D"]
          and len(base_row.get("versions") or []) == 2 and vcard.get("added_by") == "Member D",
          "%s %s | %s versions %s base %s" % (dup.status, dj.get("duplicate"), ver.status, vcard.get("versions"),
                                             base_row.get("versions")))

    # ---------------------------------------------------------------- D05 ownership
    e_k2 = kc2.post("%s/j/recipe/%s/edit" % (k2, rid), {"name": "Stolen"}, ctype="json")
    e_m1 = kapi(rig, tok["api"]["m1"]["token"], "/api/recipes/%s/edit" % rid, {"name": "Stolen"})
    e_ow = kapi(rig, tok["api"]["owner"]["token"], "/api/recipes/%s/edit" % rid, {"name": "Stolen"})
    e_k1 = kc1.post("%s/j/recipe/%s/edit" % (k1, rid), {"name": "Test paneer tinda (mine)", "servings": 3}, ctype="json")
    u_k2 = kc2.post("%s/j/recipe/%s/unpublish" % (k2, rid), {"publish": False}, ctype="json")
    after = (kc2.get("%s/j/recipe/%s" % (k2, rid)).json() or {}).get("recipe") or {}
    check("D05 only the contributor can edit their recipe: another kitchen member, a full member and the owner get 403",
          e_k2.status == 403 and e_m1.status == 403 and e_ow.status == 403 and u_k2.status == 403
          and e_k1.status == 200 and after.get("name") == "Test paneer tinda (mine)" and after.get("servings") == 3,
          "k2 %s m1 %s owner %s unpub %s | own %s -> %s" % (e_k2.status, e_m1.status, e_ow.status, u_k2.status,
                                                            e_k1.status, after.get("name")))
    r1 = kc2.post("%s/j/recipe/%s/rate" % (k2, rid), {"stars": 4, "made": True, "note": "Lovely"}, ctype="json")
    r2 = m1.post("/m1/api/kitchen/recipe/%d/rate" % rid, {"stars": 5, "again": True}, ctype="json")
    rated = (m2.get("/m2/api/kitchen/recipe/%s" % rid).json() or {}).get("recipe") or {}
    who = sorted((x["who"], x["stars"]) for x in rated.get("ratings") or [])
    check("D05 others can rate, mark made / again and note; ratings and notes show the rater's name",
          r1.status == 200 and r2.status == 200 and who == [("Member A", 5), ("Member D", 4)]
          and any(x.get("note") == "Lovely" and x["who"] == "Member D" for x in rated.get("ratings") or []),
          "%s %s %s" % (r1.status, r2.status, rated.get("ratings")))
    un = kc1.post("%s/j/recipe/%s/unpublish" % (k1, rid), {"publish": False}, ctype="json")
    gone_m1 = [x["id"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", [])]
    gone_k2 = kc2.get("%s/j/recipe/%s" % (k2, rid))
    mine = [x for x in (kc1.get(k1 + "/j/recipes?by=me").json() or {}).get("recipes", []) if x["id"] == rid]
    re_ = kc1.post("%s/j/recipe/%s/unpublish" % (k1, rid), {"publish": True}, ctype="json")
    back = [x["id"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", [])]
    check("D05 the contributor can unpublish (gone for everyone, kept for them with a note) and publish again",
          un.status == 200 and rid not in gone_m1 and gone_k2.status == 404 and mine and mine[0]["status"] == "unpublished"
          and re_.status == 200 and rid in back, "%s gone %s k2 %s mine %s back %s" % (un.status, rid not in gone_m1,
                                                                                       gone_k2.status, bool(mine), rid in back))
    h_m1 = m1.post("/m1/api/kitchen/recipe/%d/hide" % rid, {"hide": True}, ctype="json")
    h_ow = owner.post("/api/kitchen/recipe/%d/hide" % rid, {"hide": True, "note": "wrong upload"}, ctype="json")
    hid_m1 = [x["id"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", [])]
    hid_k2 = kc2.get("%s/j/recipe/%s" % (k2, rid))
    mine = [x for x in (kc1.get(k1 + "/j/recipes?by=me").json() or {}).get("recipes", []) if x["id"] == rid]
    own_try = kc1.post("%s/j/recipe/%s/unpublish" % (k1, rid), {"publish": True}, ctype="json")
    ow_list = [x["id"] for x in (owner.get("/api/kitchen/recipes?show=hidden").json() or {}).get("recipes", [])]
    unh = owner.post("/api/kitchen/recipe/%d/hide" % rid, {"hide": False}, ctype="json")
    back = [x["id"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", [])]
    check("D05 only the owner can hide (never delete); the contributor still sees it with the note; the owner can show it again",
          h_m1.status == 403 and h_ow.status == 200 and rid not in hid_m1 and hid_k2.status == 404
          and mine and mine[0]["status"] == "hidden" and mine[0].get("hidden_note") == "wrong upload"
          and own_try.status == 400 and rid in ow_list and unh.status == 200 and rid in back,
          "m1 %s owner %s hidden-from-m1 %s k2 %s mine %s own-try %s owner-list %s back %s"
          % (h_m1.status, h_ow.status, rid not in hid_m1, hid_k2.status, mine[:1], own_try.status, rid in ow_list, rid in back))

    # ---------------------------------------------------------------- D06 a kitchen member reaches nothing else
    ck = cookie_of(kc1)
    probe = famtest.Client(rig.front_url)
    hdr = {"Cookie": "kitchen=" + (ck or "")}
    r_now = probe.get("/m1/api/now", headers=hdr)
    r_home = probe.get("/m1/", headers=hdr)
    r_rx = probe.get("/m1/rx/", headers=hdr)
    r_fit = probe.get("/m1/fit/", headers=hdr)
    r_api = probe.get("/kitchen/api/recipes", headers=hdr)
    r_k2 = probe.get(k2 + "/j/me", headers=hdr)
    r_own = famtest.Client("http://127.0.0.1:%d" % rig.owner_port).get("/api/now", headers=hdr)

    def shut(r):
        return r.status != 200 or ("tab-now" not in r.text and "/login" in r.url + r.text[:400])
    check("D06 a kitchen member's session reaches no /m1/ page, no health endpoint, not the owner's app, not the bearer API, not k2",
          ck and r_now.status in (302, 401, 403) and shut(r_home) and shut(r_rx) and shut(r_fit)
          and r_api.status == 401 and r_k2.status == 401 and r_own.status in (302, 401, 403),
          "now %s home %s rx %s fit %s api %s k2 %s owner %s" % (r_now.status, r_home.status, r_rx.status, r_fit.status,
                                                                r_api.status, r_k2.status, r_own.status))
    so = kc1.post(k1 + "/signout", {})
    dead = kc1.get(k1 + "/j/recipes")
    check("D06 sign out ends the session", so.status == 302 and (so.headers.get("Location") or "").endswith(k1 + "/login")
          and dead.status == 401, "%s %s %s" % (so.status, so.headers.get("Location"), dead.status))
    kc1 = rig.kitchen_client("k1")
    kc1b = rig.kitchen_client("k1")
    kc1.post(k1 + "/signout-all", {})
    check("D06 'sign out on all devices' ends every session", kc1b.get(k1 + "/j/me").status == 401, "")
    kc1 = rig.kitchen_client("k1")

    # ---------------------------------------------------------------- D07 schema and preferences
    dbs = [os.path.join(rig.kitchen_dir, "kitchen.db")]
    for s in ("k1", "k2"):
        dbs.append(os.path.join(rig.kitchen_dir, "members", s, "auth.db"))
    cols = []
    for p in dbs:
        c = sqlite3.connect(p)
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            cols += [(os.path.basename(p), t, r[1]) for r in c.execute('PRAGMA table_info("%s")' % t).fetchall()]
        c.close()
    # Whole parts of a column OR table name ("variant_label" is not "lab"); a prefix for the stems.
    names = set((tc[0], tc[1], tc[2]) for tc in cols) | set((tc[0], tc[1], tc[1]) for tc in cols)
    bad = sorted(tc for tc in names if any(p == f or (len(f) > 4 and p.startswith(f))
                                           for p in tc[2].lower().split("_") for f in FORBIDDEN_COLS))
    check("D07 the Kitchen holds no health field: recipes, ratings, drafts, kmembers, and every member's auth.db",
          any(tc[1] == "kmembers" for tc in cols) and any(tc[1] == "auth_kv" for tc in cols) and not bad, bad)
    _d, od = gut_confirm(owner, "", "Test onion dal", [{"item": "moong dal", "qty": 100, "unit": "g"},
                                                       {"item": "onion", "qty": 1, "unit": "medium"}], ["Cook."])
    onion_id = (od.json() or {}).get("id")
    _d, of = gut_confirm(owner, "", "Test onion dal (onion-free)", [{"item": "moong dal", "qty": 100, "unit": "g"},
                                                                    {"item": "hing", "qty": None, "unit": "pinch"}],
                         ["Cook."], extra={"force": True, "variant_of": onion_id, "variant_label": "onion-free"})
    free_id = (of.json() or {}).get("id")
    _d, oe = gut_confirm(owner, "", "Test egg bhurji", [{"item": "egg", "qty": 2, "unit": "medium"}], ["Scramble."])
    egg_id = (oe.json() or {}).get("id")
    _d, oc2 = gut_confirm(owner, "", "Test chicken curry", [{"item": "chicken", "qty": 500, "unit": "g"},
                                                            {"item": "potato", "qty": 2, "unit": "medium"}], ["Cook."])
    chk_id = (oc2.json() or {}).get("id")

    def ids(cl, pre):
        return set(x["id"] for x in (cl.get(pre + "/j/recipes").json() or {}).get("recipes", []))
    allids = ids(kc1, k1)
    p1 = kc1.post(k1 + "/j/prefs", {"prefs": ["noonion"]}, ctype="json")
    no_onion = ids(kc1, k1)
    kc1.post(k1 + "/j/prefs", {"prefs": ["veg"]}, ctype="json")
    veg = ids(kc1, k1)
    kc1.post(k1 + "/j/prefs", {"prefs": ["egg"]}, ctype="json")
    egg = ids(kc1, k1)
    kc1.post(k1 + "/j/prefs", {"prefs": ["jain"]}, ctype="json")
    jain = ids(kc1, k1)
    kc1.post(k1 + "/j/prefs", {"prefs": ["zzz", "conditions"]}, ctype="json")
    cleared = ids(kc1, k1)
    c = TC.kdb(rig)
    stored = c.execute("SELECT food_prefs FROM kmembers WHERE slug='k1'").fetchone()[0]
    c.close()
    m1_all = set(x["id"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", []))
    check("D07 food preferences only filter, and show the matching version: no onion-garlic hides the onion dal and keeps the onion-free one",
          p1.status == 200 and {onion_id, free_id, egg_id, chk_id} <= allids and onion_id not in no_onion
          and free_id in no_onion and chk_id in no_onion, "all %s no-onion %s" % (sorted(allids), sorted(no_onion)))
    check("D07 Vegetarian / Eggetarian / Jain: plain words in the Kitchen, no health field, nothing for full members",
          egg_id not in veg and chk_id not in veg and free_id in veg
          and egg_id in egg and chk_id not in egg
          and not ({onion_id, egg_id, chk_id} & jain) and free_id in jain
          and cleared == allids and stored == "" and {onion_id, egg_id, chk_id} <= m1_all,
          "veg %s egg %s jain %s stored %r" % (sorted(veg), sorted(egg), sorted(jain), stored))

    # ---------------------------------------------------------------- D08 nutrition for a kitchen member
    ings_r = [{"item": "rice", "qty": 1, "unit": "cup"}, {"item": "ghee", "qty": 1, "unit": "tbsp"},
              {"item": "salt", "qty": None, "unit": "pinch"}, {"item": "zzq leaf", "qty": 2, "unit": "g"}]
    _d, rr = publish(kc2, k2, "Test ghee rice", ings_r, ["Cook the rice.", "Add ghee."], servings=2)
    rice = (rr.json() or {}).get("id")
    mine_ = m2.get("/m2/api/kitchen/recipe/%s" % rice).json() or {}
    kn = (kc1.get("%s/j/recipe/%s" % (k1, rice)).json() or {}).get("nutrition") or {}
    check("D08 nutrition per serving for a kitchen member equals a full member's unadjusted card (same measures, same table)",
          not mine_.get("badges") and mine_.get("portion") == 1 and kn.get("per_serving")
          and kn["per_serving"] == (mine_.get("nutrition") or {}).get("per_serving")
          and [u["item"] for u in kn.get("unmatched") or []] == ["zzq leaf"] and kn["per_serving"]["kcal"] > 100,
          "k %s / m2 %s badges %s" % (kn.get("per_serving"), (mine_.get("nutrition") or {}).get("per_serving"), mine_.get("badges")))

    # ---------------------------------------------------------------- D09 the owner's Family page; By person
    fam = owner.get("/family")
    ft = fam.text
    mem = kapi(rig, tok["api"]["m1"]["token"], "/api/members")
    ppl = (kc1.get(k1 + "/j/people").json() or {}).get("people") or []
    by_c = [x["added_by"] for x in (m1.get("/m1/api/kitchen/recipes?by=k1").json() or {}).get("recipes", [])]
    gp = (m1.get("/m1/api/kitchen/people").json() or {}).get("people") or []
    check("D09 the owner's Family page lists kitchen members with name, last visit and recipes added -- no caretaker access",
          fam.status == 200 and "Kitchen members" in ft and "Member C" in ft and "Member D" in ft
          and re.search(r"Member C</p><p>Last visit: 20\d\d-\d\d-\d\d \d\d:\d\d<br>Recipes added: 3<", ft)
          and ft.count("Open as caretaker") == 2 and "/family/open/k1" not in ft and mem.status == 403,
          "%s caretaker %d members-api %s\n%s" % (fam.status, ft.count("Open as caretaker"), mem.status,
                                                  re.findall(r"Kitchen members.{0,400}", ft)[:1]))
    check("D09 'By person': a name lists that person's recipes, in the kitchen member's book and in a full member's GutLog",
          [p["name"] for p in ppl if p["slug"] == "k1"] == ["Member C"] and next(p["n"] for p in ppl if p["slug"] == "k1") == 3
          and by_c and set(by_c) == {"Member C"} and any(p["slug"] == "k2" for p in gp), "%s %s %s" % (ppl, by_c, gp))

    # ---------------------------------------------------------------- D11 rename, disable
    rc, out = stamp_tool(rig, "--slug", "k2", "--rename", "Member D2")
    vcard = (m1.get("/m1/api/kitchen/recipe/%s" % vid).json() or {}).get("recipe") or {}
    lp2 = html.unescape(famtest.Client(rig.front_url).get(k2 + "/login").text)
    check("D11 --rename shows on every card the member added, and on their sign-in page",
          rc == 0 and vcard.get("added_by") == "Member D2" and "Member D2 \u2014 Family Kitchen" in lp2, "%s %s" % (rc, vcard.get("added_by")))
    rc, out = stamp_tool(rig, "--slug", "k2", "--disable")
    dl = famtest.Client(rig.front_url).get(k2 + "/login")
    dp = famtest.Client(rig.front_url).post(k2 + "/login", {"pin": rig.pw["k2"]})
    dc = TC.capture(rig, "k2", tok["capture"]["k2"]["token"], {"text": "x"})
    dsess = kc2.get(k2 + "/j/me")
    still = vid in set(x["id"] for x in (m1.get("/m1/api/kitchen/recipes").json() or {}).get("recipes", []))
    rc2, _o = stamp_tool(rig, "--slug", "k2", "--enable")
    back = rig.kitchen_client("k2").get(k2 + "/j/me")
    check("D11 --disable stops sign-in, capture and the live session, keeps their recipes in the pool; --enable brings them back",
          rc == 0 and dl.status == 403 and "switched off" in dl.text and dp.status == 403 and dc[0] == 401
          and dsess.status == 401 and still and rc2 == 0 and back.status == 200,
          "%s login %s post %s capture %s sess %s still %s back %s" % (rc, dl.status, dp.status, dc[0], dsess.status, still, back.status))

    # ---------------------------------------------------------------- D10 readiness on a kitchen member
    c = TC.kdb(rig)
    c.execute("UPDATE kmembers SET last_seen='2026-01-01 08:00' WHERE slug='k1'")   # a visit long ago
    c.commit()
    c.close()
    before = dict((t, TC.kdb(rig).execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]) for t in ("recipes", "ratings", "drafts"))
    r = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "readiness.py"), "--slug", "k1",
                        "--base", rig.front_url, "--srv", os.path.join(rig.root, "srv", "family"), "--pin-file", pwf,
                        "--registry", os.path.join(rig.root, "root", "family", "members.local.json")],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    after = dict((t, TC.kdb(rig).execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]) for t in ("recipes", "ratings", "drafts"))
    check("D10 readiness.py checks a kitchen member end to end, signs in once, and leaves nothing behind",
          r.returncode == 0 and out.rstrip().endswith("READY") and "FAIL" not in out and before == after
          and "Readiness check" not in TC.pool_text(rig) and rig.pw["k1"] not in out,
          "\n".join(ln for ln in out.splitlines() if "FAIL" in ln or "READY" in ln)[-900:])
    seen = TC.kdb(rig).execute("SELECT last_seen FROM kmembers WHERE slug='k1'").fetchone()[0]
    fam = owner.get("/family").text
    check("D14 the readiness check's own sign-in is not a visit: 'last visit' on the Family page stays as it was",
          seen == "2026-01-01 08:00" and "Last visit: 2026-01-01 08:00" in fam, "%r" % seen)

    # ---------------------------------------------------------------- D12 the reader takes a kitchen member's draft
    st, cj = TC.capture(rig, "k1", ctok, {"text": "https://example.invalid/r/x"})
    kdid = (cj.get("drafts") or [None])[0]
    kr = TC.load(os.path.join(famtest.fam_src(), "kitchen_reader.py"), "kreader_d",
                 {"KITCHEN_KEYS_ENV": os.path.join(rig.work, "no-keys.env"), "ANTHROPIC_API_KEY": "", "SARVAM_API_KEY": ""})
    os.environ.pop("ANTHROPIC_API_KEY", None)
    c = TC.kdb(rig)
    rowd = c.execute("SELECT * FROM drafts WHERE id=?", (kdid,)).fetchone()
    c.close()
    ex, flags, note = kr.read_draft(dict(rowd))
    check("D12 the reader reads a kitchen member's draft like anyone's (unreachable link: left for the person, kept private)",
          st == 200 and rowd["who"] == "k1" and ex is None and flags, (ex, flags, note))
    _ = (m2, free_id, egg_id)


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("D00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

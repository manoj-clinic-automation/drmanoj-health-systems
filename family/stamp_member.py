#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stamp_member.py -- add a family member with one command.

FAMILY_EDITION_V1. Run as root on the server:

  python3 /root/family/stamp_member.py --slug m3 --name "Asha" --profile gut|joint|general
  python3 /root/family/stamp_member.py --slug m3 --disable      (stops; keeps every byte)
  python3 /root/family/stamp_member.py --slug m3 --enable
  python3 /root/family/stamp_member.py --slug m3 --rotate-care  (new caretaker key + status token)
  python3 /root/family/stamp_member.py --list

  A KITCHEN MEMBER (recipes only -- no GutLog, RxGuard or FitLog, no health data):
  python3 /root/family/stamp_member.py --kitchen-only --slug k1 --name "…" --password-file /root/family/first-login.local.txt
  python3 /root/family/stamp_member.py --slug k1 --rename "…" | --reset-pin | --disable | --enable
  python3 /root/family/stamp_member.py --kitchen-sync --owner-name "…"   (the name on the owner's cards)

  A SEEDED MEMBER (26-Sep-2026): the setup, meal windows, weight plan, medicines,
  schedules (incl. WEEKLY), check-in rules and the plan PDF from a gitignored file,
  applied AS THE MEMBER into their own database, the copy deleted afterwards:
  python3 /root/family/stamp_member.py --slug m3 --name "…" --profile weight \
      --seed /root/family/seeds/m3_seed.local.json --password-file /root/family/first-login.local.txt

  A PHYSIO (a role inside a member's copy: /m3/physio/, its own PIN and cookie,
  the programme, sessions, pain and walking entries and the monthly re-test
  and nothing else of the record):
  python3 /root/family/stamp_member.py --physio --slug p1 --name "…" --for m3 --password-file …
  python3 /root/family/stamp_member.py --slug p1 --for m2          (attach the same physio, same PIN)
  python3 /root/family/stamp_member.py --slug p1 --reset-pin | --disable | --enable

A kitchen member is an account inside the Kitchen service: a row in
kitchen.db (slug, display name, enabled, plain food preferences), a PIN in
/srv/family/kitchen/members/<slug>/auth.db (family_auth, the same rules as
every family sign-in), and a capture-only token for the Share shortcut. No
Linux user, no folder of their own, no registry entry, no routes: the
/kitchen/ context already proxies /kitchen/k1/. --disable keeps their
recipes. There is no delete.

A new (full) member gets:
  * a Linux user fam_<slug> (no login shell, no home) and /srv/family/<slug>,
    mode 700, owned by that user -- the member's three processes run as it and
    cannot open /root, the owner's apps' folders, or another member's folder;
  * their own secrets: session keys (created by each app on first start),
    a sign-in-ring key, a GutLog feed token, a caretaker key, a status token,
    FitLog ingest and Health Connect tokens;
  * three databases created as the member (init_member.py), with nothing of
    the owner's in them, and one first-login password for all three;
  * /etc/family/<slug>.env (root, 600) and three systemd template instances
    (family-gut@ / family-rx@ / family-fit@<slug>), sandboxed;
  * proxy routes in the family.dr-manoj.in vhost (a managed block), and
    OpenLiteSpeed restarted gracefully;
  * a place in the nightly backup (family_backup.py reads the registry).

The password is printed ONCE, to this terminal, and written nowhere --
not to the registry, not to a log. There is no delete command: --disable
stops the services and takes the routes away, and keeps the data.

Refuses to overwrite: a slug already in the registry, or a folder already on
disk, stops the run before anything is written.

--root and --no-system exist for the test suite: files under a scratch root,
no useradd / chown / systemctl / OLS.

Python 3.9.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SLUG_RX = re.compile(r"^m([0-9]{1,3})$")
KSLUG_RX = re.compile(r"^k[0-9]{1,3}$")
PSLUG_RX = re.compile(r"^p[0-9]{1,3}$")
WORDS = ("amber", "basil", "cedar", "delta", "ember", "fable", "grove", "harbor", "indigo",
         "juniper", "kestrel", "lotus", "maple", "nectar", "orchid", "pebble", "quartz",
         "river", "saffron", "tulip", "umber", "velvet", "willow", "yarrow", "zephyr",
         "canyon", "meadow", "lantern", "compass", "harvest", "monsoon", "saturn")
APPS = ("gut", "rx", "fit")
VHOST_DEFAULT = "/usr/local/lsws/conf/vhosts/family.dr-manoj.in/vhost.conf"
BEGIN, END = "# FAMILY_MEMBERS_BEGIN (stamp_member.py -- do not edit by hand)", "# FAMILY_MEMBERS_END"


class Paths(object):
    def __init__(self, root, code):
        r = root.rstrip("/") or "/"
        j = lambda *p: os.path.join(r, *[x.lstrip("/") for x in p])
        self.root = r
        self.registry = j("/root/family/members.local.json")
        self.care_dir = j("/root/family/care")
        self.kitchen_owner = j("/root/family/kitchen")
        self.srv = j("/srv/family")
        self.etc = j("/etc/family")
        self.vhost = j(VHOST_DEFAULT)
        self.code = code

    def member(self, slug):
        return os.path.join(self.srv, slug)


def ports_for(slug, port_base=8200):
    n = int(SLUG_RX.match(slug).group(1))
    base = port_base + 10 * n
    return {"gut": base + 1, "rx": base + 2, "fit": base + 3}


def load_registry(P):
    try:
        with open(P.registry, encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError):
        j = {}
    j.setdefault("base", "https://family.dr-manoj.in")
    j.setdefault("caretaker", "Manoj")
    j.setdefault("members", [])
    return j


def save_registry(P, reg):
    os.makedirs(os.path.dirname(P.registry), exist_ok=True)
    tmp = P.registry + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, P.registry)


def write_secret(path, value, mode=0o600, excl=True):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if excl else os.O_TRUNC)
    fd = os.open(path, flags, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")


def new_password():
    r = secrets.SystemRandom()
    return "%s-%s-%04d" % (r.choice(WORDS), r.choice(WORDS), r.randrange(10000))


def new_pin():
    """Six digits, never all one digit or a straight run (family_auth.weak_pin)."""
    r = secrets.SystemRandom()
    while True:
        pin = "%06d" % r.randrange(1000000)
        if len(set(pin)) > 1 and pin not in "01234567890" and pin not in "09876543210":
            return pin


def write_pin_file(path, slug, name, url, pin):
    """Root-only, appended, never echoed: the owner hands it over and deletes it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write("%s\t%s\t%s\tPIN %s\n" % (slug, name, url, pin))
    os.chmod(path, 0o600)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def env_text(reg, m):
    lines = ["# /etc/family/%s.env -- written by stamp_member.py. Root only." % m["slug"],
             "FAMILY_SLUG=" + m["slug"], "FAMILY_DIR=/srv/family/" + m["slug"],
             "FAMILY_BASE=" + reg["base"], "FAMILY_NAME=" + m["name"].replace("\n", " "),
             "FAMILY_PROFILE=" + m["profile"],
             "FAMILY_CARETAKER=" + str(reg.get("caretaker") or "your caretaker").replace("\n", " ")]
    for a in APPS:
        lines.append("FAMILY_PORT_%s=%d" % (a.upper(), m["ports"][a]))
    return "\n".join(lines) + "\n"


KITCHEN_PORT = 8199


def kitchen_tokens_path(P):
    return os.path.join(P.srv, "kitchen", "tokens.json")


def load_kitchen_tokens(P):
    try:
        with open(kitchen_tokens_path(P), encoding="utf-8") as fh:
            t = json.load(fh)
    except (OSError, ValueError):
        t = {}
    t.setdefault("api", {})
    t.setdefault("capture", {})
    return t


def save_kitchen_tokens(P, t, system):
    tpath = kitchen_tokens_path(P)
    tmp = tpath + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(t, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, tpath)
    if system:
        run(["chown", "fam_kitchen:fam_kitchen", tpath])


def kitchen_register(P, slug, name, system, member_dir=None, files=None):
    """Give one member (or 'owner') their Family Kitchen tokens: an API token
    for their GutLog and a capture-only token for the Share shortcut. The
    Kitchen's copy is tokens.json (fam_kitchen, 600); the member's copy sits
    in their own folder. Nothing happens when the Kitchen is not installed.
    An existing entry keeps its tokens; its display name is brought up to date."""
    kdir = os.path.join(P.srv, "kitchen")
    if not os.path.isdir(kdir):
        return False
    t = load_kitchen_tokens(P)
    if member_dir:
        files = (os.path.join(member_dir, "kitchen.token"), os.path.join(member_dir, "kitchen.capture"))
    if slug in t["api"] and (not files or os.path.exists(files[0])):
        if t["api"][slug].get("name") != name:
            t["api"][slug]["name"] = name
            save_kitchen_tokens(P, t, system)
        return True
    api, cap = secrets.token_hex(32), secrets.token_hex(32)
    t["api"][slug] = {"token": api, "name": name}
    t["capture"][slug] = {"token": cap}
    save_kitchen_tokens(P, t, system)
    for p, v in zip(files or (), (api, cap)):
        write_secret(p, v, excl=False)
        if system and slug != "owner":
            run(["chown", "fam_%s:fam_%s" % (slug, slug), p])
    return True


# ------------------------------------------------------------------ kitchen members
def kitchen_modules(P):
    """kitchen.py / kitchen_members.py from the code tree, pointed at THIS
    root's Kitchen folder (they read KITCHEN_DIR at import)."""
    os.environ["KITCHEN_DIR"] = os.path.join(P.srv, "kitchen")
    fam = os.path.join(P.code, "family")
    for d in (fam, HERE):
        if d not in sys.path:
            sys.path.insert(0, d)
    import kitchen
    import kitchen_members
    return kitchen, kitchen_members


def kitchen_con(P, K):
    import sqlite3
    con = sqlite3.connect(os.path.join(P.srv, "kitchen", "kitchen.db"), timeout=10)
    con.row_factory = sqlite3.Row
    K.migrate(con)
    return con


def kitchen_own(P, system):
    """Everything under the Kitchen folder belongs to fam_kitchen -- including
    what root just wrote (auth.db, -wal/-shm, tokens.json)."""
    if system:
        run(["chown", "-R", "fam_kitchen:fam_kitchen", os.path.join(P.srv, "kitchen")])


def kitchen_members_list(P):
    kdb = os.path.join(P.srv, "kitchen", "kitchen.db")
    if not os.path.exists(kdb):
        return []
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % kdb, uri=True)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='kmembers'").fetchone():
            return []
        return [dict(zip(("slug", "name", "enabled", "created", "last_seen"), r)) for r in
                con.execute("SELECT slug, name, enabled, created, last_seen FROM kmembers ORDER BY slug")]
    finally:
        con.close()


def stamp_kitchen(P, a, system):
    slug = a.slug
    kdir = os.path.join(P.srv, "kitchen")
    if not os.path.isdir(kdir):
        print("REFUSED: the Family Kitchen is not installed here (%s). Nothing changed." % kdir)
        return 2
    name = (a.name or "").strip()
    if not name or len(name) > 40 or "\n" in name:
        print("REFUSED: --name is required (1-40 characters).")
        return 2
    K, KM = kitchen_modules(P)
    con = kitchen_con(P, K)
    try:
        if con.execute("SELECT 1 FROM kmembers WHERE slug=?", (slug,)).fetchone():
            print("REFUSED: %s is already a kitchen member. Nothing changed." % slug)
            return 2
        mdir = os.path.join(kdir, "members", slug)
        if os.path.exists(mdir):
            print("REFUSED: %s already exists on disk. Nothing changed." % mdir)
            return 2
        reg = load_registry(P)
        if a.base_url:
            reg["base"] = a.base_url.rstrip("/")
            save_registry(P, reg)
        print("stamping kitchen member %s (%s)" % (slug, name))
        os.makedirs(os.path.join(kdir, "members"), exist_ok=True)
        os.chmod(os.path.join(kdir, "members"), 0o700)
        pin = new_pin()
        au = KM.auth_for(slug)          # creates members/<slug>/auth.db, mode 700 folder
        au.set_pin(pin)
        au.log("tool", "server", "account created by the owner's server tool")
        con.execute("INSERT INTO kmembers(slug, name, enabled, created, last_seen, food_prefs) "
                    "VALUES(?,?,1,?,'','')", (slug, name, datetime.now().strftime("%Y-%m-%d %H:%M")))
        con.commit()
    finally:
        con.close()
    t = load_kitchen_tokens(P)
    t["capture"][slug] = {"token": secrets.token_hex(32)}
    save_kitchen_tokens(P, t, system)
    kitchen_own(P, system)
    print("  kitchen: account, PIN and Share-shortcut key issued (no Linux user, no other database)")
    url = "%s/kitchen/%s/" % (reg["base"], slug)
    print("")
    print("  address : " + url)
    if a.password_file:
        write_pin_file(a.password_file, slug, name, url, pin)
        print("  first-login PIN: written to %s (root only)" % a.password_file)
    else:
        print("  first-login PIN (shown once, written nowhere): " + pin)
    return 0


def kitchen_member_row(P, K, con, slug):
    r = con.execute("SELECT * FROM kmembers WHERE slug=?", (slug,)).fetchone()
    if not r:
        print("No kitchen member %s." % slug)
    return r


def kitchen_set_enabled(P, slug, on, system):
    K, _KM = kitchen_modules(P)
    con = kitchen_con(P, K)
    try:
        if not kitchen_member_row(P, K, con, slug):
            return 2
        con.execute("UPDATE kmembers SET enabled=? WHERE slug=?", (1 if on else 0, slug))
        con.commit()
    finally:
        con.close()
    kitchen_own(P, system)
    print("%s %s. Their recipes stay in the Kitchen." % (slug, "enabled" if on else "disabled (cannot sign in)"))
    return 0


def kitchen_rename(P, slug, name, system):
    name = (name or "").strip()
    if not name or len(name) > 40 or "\n" in name:
        print("The name is not 1-40 characters.")
        return 2
    K, _KM = kitchen_modules(P)
    con = kitchen_con(P, K)
    try:
        if not kitchen_member_row(P, K, con, slug):
            return 2
        con.execute("UPDATE kmembers SET name=? WHERE slug=?", (name, slug))
        con.commit()
    finally:
        con.close()
    kitchen_own(P, system)
    print("%s renamed; every card they added now says so." % slug)
    return 0


def kitchen_reset_pin(P, slug, system, pin_file=None):
    K, KM = kitchen_modules(P)
    con = kitchen_con(P, K)
    try:
        r = kitchen_member_row(P, K, con, slug)
        if not r:
            return 2
        name = r["name"]
    finally:
        con.close()
    pin = new_pin()
    au = KM.auth_for(slug)
    au.set_pin(pin)
    au.rotate_epoch()
    au.log("tool", "server", "PIN reset by the owner's server tool; every device signed out")
    kitchen_own(P, system)
    reg = load_registry(P)
    if pin_file:
        write_pin_file(pin_file, slug, name, "%s/kitchen/%s/" % (reg["base"], slug), pin)
        print("%s: new PIN written to %s (root only); every device signed out." % (slug, pin_file))
    else:
        print("%s: new PIN (shown once, written nowhere): %s" % (slug, pin))
    return 0


# ------------------------------------------------------------------ physio
def member_user(reg, slug):
    m = next((x for x in reg["members"] if x["slug"] == slug), None)
    return (m or {}).get("user") or ("fam_" + slug)


def physio_paths(P, mslug, pslug):
    d = os.path.join(P.member(mslug), "physio", pslug)
    return d, os.path.join(d, "auth.db"), os.path.join(d, "info.json")


def physio_entry(reg, pslug):
    reg.setdefault("physios", [])
    return next((p for p in reg["physios"] if p.get("slug") == pslug), None)


def stamp_physio(P, pslug, name, mslug, system, pin_file=None, quiet=False):
    """Create the physio's sign-in inside ONE member's folder. A physio already
    attached to another member keeps the same PIN (their auth.db is copied);
    a new one gets a fresh PIN, written to the root-only file."""
    reg = load_registry(P)
    if not any(m["slug"] == mslug for m in reg["members"]):
        print("REFUSED: no member %s. Nothing changed." % mslug)
        return 2
    if not os.path.isdir(P.member(mslug)):
        print("REFUSED: %s has no folder here. Nothing changed." % mslug)
        return 2
    d, auth_db, info = physio_paths(P, mslug, pslug)
    if os.path.exists(auth_db):
        print("REFUSED: %s is already attached to %s. Nothing changed." % (pslug, mslug))
        return 2
    ent = physio_entry(reg, pslug)
    name = (name or (ent or {}).get("name") or "").strip()
    if not name or len(name) > 40 or "\n" in name:
        print("REFUSED: --name is required (1-40 characters) for a new physio.")
        return 2
    # the same physio elsewhere -> the same credentials (auth.db copied)
    src = None
    for other in (ent or {}).get("for") or []:
        _od, oa, _oi = physio_paths(P, other, pslug)
        if os.path.exists(oa):
            src = oa
            break
    os.makedirs(d)
    os.chmod(os.path.dirname(d), 0o700)
    os.chmod(d, 0o700)
    pin = None
    if src:
        shutil.copy2(src, auth_db)
        os.chmod(auth_db, 0o600)
    else:
        sys.path.insert(0, HERE)
        import family_physio
        pin = new_pin()
        au = family_physio.auth_for(P.member(mslug), pslug)
        au.set_pin(pin)
        au.log("tool", "server", "physio sign-in created by the owner's server tool")
    with open(info, "w", encoding="utf-8") as fh:
        json.dump({"slug": pslug, "name": name, "enabled": True,
                   "created": datetime.now().strftime("%Y-%m-%d %H:%M")}, fh)
    os.chmod(info, 0o600)
    if system:
        u = member_user(reg, mslug)
        run(["chown", "-R", "%s:%s" % (u, u), os.path.join(P.member(mslug), "physio")])
    if ent is None:
        ent = {"slug": pslug, "name": name, "for": [], "created": datetime.now().strftime("%Y-%m-%d %H:%M")}
        reg["physios"].append(ent)
    ent["name"] = name
    if mslug not in ent["for"]:
        ent["for"].append(mslug)
    save_registry(P, reg)
    url = "%s/%s/physio/" % (reg["base"], mslug)
    if not quiet:
        print("physio %s (%s) attached to %s" % (pslug, name, mslug))
        print("  address : " + url)
    if pin is None:
        print("  PIN: the same as on %s (credentials copied)" % ent["for"][0])
    elif pin_file:
        write_pin_file(pin_file, pslug, name, url, pin)
        print("  first-login PIN: written to %s (root only)" % pin_file)
    else:
        print("  first-login PIN (shown once, written nowhere): " + pin)
    return 0


def physio_reset_pin(P, pslug, system, pin_file=None):
    reg = load_registry(P)
    ent = physio_entry(reg, pslug)
    if not ent or not ent.get("for"):
        print("No physio %s." % pslug)
        return 2
    sys.path.insert(0, HERE)
    import family_physio
    pin = new_pin()
    for mslug in ent["for"]:
        d, auth_db, _i = physio_paths(P, mslug, pslug)
        if not os.path.exists(auth_db):
            continue
        au = family_physio.auth_for(P.member(mslug), pslug)
        au.set_pin(pin)
        au.rotate_epoch()
        au.log("tool", "server", "PIN reset by the owner's server tool; every device signed out")
        if system:
            u = member_user(reg, mslug)
            run(["chown", "-R", "%s:%s" % (u, u), os.path.join(P.member(mslug), "physio")])
    url = "%s/%s/physio/" % (reg["base"], ent["for"][0])
    if pin_file:
        write_pin_file(pin_file, pslug, ent["name"], url, pin)
        print("%s: new PIN written to %s (root only); every device signed out." % (pslug, pin_file))
    else:
        print("%s: new PIN (shown once, written nowhere): %s" % (pslug, pin))
    return 0


def physio_set_enabled(P, pslug, on, system):
    reg = load_registry(P)
    ent = physio_entry(reg, pslug)
    if not ent:
        print("No physio %s." % pslug)
        return 2
    for mslug in ent.get("for") or []:
        _d, _a, info = physio_paths(P, mslug, pslug)
        try:
            with open(info, encoding="utf-8") as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        j["enabled"] = bool(on)
        with open(info, "w", encoding="utf-8") as fh:
            json.dump(j, fh)
        if system:
            u = member_user(reg, mslug)
            run(["chown", "%s:%s" % (u, u), info])
    ent["enabled"] = bool(on)
    save_registry(P, reg)
    print("%s %s on %s." % (pslug, "enabled" if on else "disabled (cannot sign in)", ", ".join(ent.get("for") or [])))
    return 0


def apply_seed(P, reg, slug, seed_path, system, mdir, envp, made_user, user, pin_file):
    """Copy the seed (and the PDF it names) into the member folder, apply it
    AS THE MEMBER (init_member.py --app seed), delete the copies, then stamp
    the physio the seed names. Nothing from the file is printed."""
    try:
        with open(seed_path, encoding="utf-8") as fh:
            seed = json.load(fh)
    except (OSError, ValueError) as exc:
        print("REFUSED: the seed file could not be read (%s)." % type(exc).__name__)
        return False, seed_path
    if seed.get("slug") and seed["slug"] != slug:
        print("REFUSED: the seed is for %s, not %s." % (seed["slug"], slug))
        return False, None
    dst = os.path.join(mdir, "seed.json")
    shutil.copy2(seed_path, dst)
    os.chmod(dst, 0o600)
    pdf = (seed.get("plan_pdf") or {}).get("file")
    copies = [dst]
    if pdf:
        src = os.path.join(os.path.dirname(os.path.abspath(seed_path)), pdf)
        if os.path.isfile(src):
            pdst = os.path.join(mdir, os.path.basename(pdf))
            shutil.copy2(src, pdst)
            os.chmod(pdst, 0o600)
            copies.append(pdst)
        else:
            print("  seed: the plan PDF it names is not beside it -- plan not filed")
    if system:
        for c in copies:
            run(["chown", "%s:%s" % (user, user), c])
    init = os.path.join(P.code, "family", "init_member.py")
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("GUTLOG_", "RXGUARD_", "FITLOG_", "HEALTH_SSO_", "FAMILY_")):
            del env[k]
    with open(envp) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    if P.root != "/":
        env["FAMILY_DIR"] = mdir
    if not system:
        env["FAMILY_INSECURE"] = "1"
    env["FAMILY_SEED_FILE"] = dst
    cmd = [sys.executable, "-B", init, "--app", "seed"]
    if system:
        cmd = ["runuser", "-u", user, "--"] + cmd
    r = subprocess.run(cmd, input=b"\n", env=env, cwd="/" if system else None,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    print("  " + (out[-1] if out else "(no output)"))
    for c in copies:
        try:
            os.remove(c)
        except OSError:
            pass
    if r.returncode != 0:
        print("FAILED while applying the seed.")
        print("\n".join("    " + x for x in out[-15:]))
        return False, None
    ph = seed.get("physio") or {}
    if ph.get("slug") and PSLUG_RX.match(str(ph["slug"])):
        return True, (str(ph["slug"]), str(ph.get("name") or ""))
    return True, None


def vhost_block(reg, P=None):
    out = [BEGIN]
    if P is not None and os.path.isdir(os.path.join(P.srv, "kitchen")):
        out += ["extprocessor fam_kitchen {", "  type                    proxy",
                "  address                 127.0.0.1:%d" % KITCHEN_PORT, "  maxConns                20",
                "  initTimeout             60", "  retryTimeout            0", "  respBuffer              0", "}",
                "context /kitchen/ {", "  type                    proxy", "  handler                 fam_kitchen",
                "  addDefaultCharset       off", "}"]
    for m in reg["members"]:
        if not m.get("enabled", True):
            continue
        s = m["slug"]
        for a in ("rx", "fit", "gut"):   # longest prefix first
            ctx = "/%s/%s/" % (s, a) if a != "gut" else "/%s/" % s
            name = "fam_%s_%s" % (s, a)
            out += ["extprocessor %s {" % name, "  type                    proxy",
                    "  address                 127.0.0.1:%d" % m["ports"][a],
                    "  maxConns                20", "  initTimeout             60",
                    "  retryTimeout            0", "  respBuffer              0", "}",
                    "context %s {" % ctx, "  type                    proxy",
                    "  handler                 %s" % name,
                    "  addDefaultCharset       off", "}"]
    out.append(END)
    return "\n".join(out)


def write_vhost(P, reg, system):
    if not os.path.exists(P.vhost):
        print("  proxy: %s not there yet -- routes will be written when it is." % P.vhost)
        return False
    with open(P.vhost, encoding="utf-8") as fh:
        t = fh.read()
    block = vhost_block(reg, P)
    if BEGIN in t and END in t:
        a = t.index(BEGIN)
        b = t.index(END) + len(END)
        t2 = t[:a] + block + t[b:]
    else:
        t2 = t.rstrip("\n") + "\n\n" + block + "\n"
    if t2 != t:
        bak = P.vhost + ".bak-family-" + datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(P.vhost, bak)
        with open(P.vhost, "w", encoding="utf-8") as fh:
            fh.write(t2)
        print("  proxy: routes written (backup %s)" % os.path.basename(bak))
        if system:
            run(["/usr/local/lsws/bin/lswsctrl", "restart"], stdout=subprocess.DEVNULL)
            print("  proxy: OpenLiteSpeed restarted gracefully")
    return True


def units(slug):
    return ["family-%s@%s.service" % (a, slug) for a in APPS]


def member_readable(path):
    """(ok, why): can a user who is not root and not the owner read this file?
    Every folder above it needs o+x, the file o+r, and nothing may sit under
    /root, which member processes must never be able to open."""
    p = os.path.realpath(path)
    if p == "/root" or p.startswith("/root/"):
        return False, "%s is under /root" % p
    if not os.path.isfile(p):
        return False, "%s does not exist" % p
    if not os.stat(p).st_mode & 0o004:
        return False, "%s is not world-readable" % p
    d = os.path.dirname(p)
    while True:
        if not os.stat(d).st_mode & 0o001:
            return False, "folder %s cannot be entered by other users" % d
        if d == "/":
            return True, ""
        d = os.path.dirname(d)


def rollback(P, slug, mdir, envp, user, made_user, system):
    """Undo a stamp that failed part-way: nothing of it stays behind."""
    for p in (os.path.join(P.care_dir, slug + ".key"), os.path.join(P.care_dir, slug + ".status"), envp):
        try:
            os.remove(p)
        except OSError:
            pass
    shutil.rmtree(mdir, ignore_errors=True)
    if system and made_user:
        subprocess.run(["userdel", user], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  rolled back: folder, env file, caretaker secrets%s removed. Nothing of %s remains."
          % (" and user " + user if made_user else "", slug))


def stamp(P, a, system):
    slug = a.slug
    reg = load_registry(P)
    if any(m["slug"] == slug for m in reg["members"]):
        print("REFUSED: %s is already a member. Nothing changed." % slug)
        return 2
    mdir = P.member(slug)
    if os.path.exists(mdir):
        print("REFUSED: %s already exists on disk. Nothing changed." % mdir)
        return 2
    name = (a.name or "").strip()
    if not name or len(name) > 40 or "\n" in name:
        print("REFUSED: --name is required (1-40 characters).")
        return 2
    ports = ports_for(slug, a.port_base)
    if a.base_url:
        reg["base"] = a.base_url.rstrip("/")
    for m in reg["members"]:
        if set(m["ports"].values()) & set(ports.values()):
            print("REFUSED: ports %s are taken." % sorted(ports.values()))
            return 2
    user = "fam_" + slug
    m = {"slug": slug, "name": name, "profile": a.profile, "ports": ports, "user": user,
         "enabled": True, "created": datetime.now().strftime("%Y-%m-%d %H:%M")}
    init = os.path.join(P.code, "family", "init_member.py")
    if system:
        # The setup runs AS THE MEMBER, so the member must be able to read it.
        # Checked before anything is written: a refusal here leaves nothing.
        ok, why = member_readable(init)
        if not ok:
            print("REFUSED: the member's own user could not run the setup (%s). Nothing changed.\n"
                  "  Use the family code tree, /opt/family/code/current." % why)
            return 2
    print("stamping %s (%s, profile %s) ports %s" % (slug, name, a.profile,
                                                     "/".join(str(ports[x]) for x in APPS)))
    # -- user and folders --------------------------------------------------
    made_user = False
    if system:
        r = subprocess.run(["id", "-u", user], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            run(["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent",
                 "--shell", "/sbin/nologin", "--user-group", user])
            made_user = True
    # The member's user must be able to pass through every parent: under a
    # root umask of 077 a freshly made parent would be 700 (25-Sep-2026).
    parent = os.path.dirname(P.srv)
    if not os.path.isdir(parent):
        os.makedirs(parent)
        os.chmod(parent, 0o755)
    os.makedirs(P.srv, exist_ok=True)
    os.chmod(P.srv, 0o755)
    os.makedirs(mdir)
    for sub in ("gutlog", "rxguard", "fitlog", "logs"):
        os.makedirs(os.path.join(mdir, sub))
    # -- secrets -----------------------------------------------------------
    care_key, status_tok = secrets.token_hex(32), secrets.token_hex(32)
    write_secret(os.path.join(mdir, "sso.key"), secrets.token_hex(32))
    write_secret(os.path.join(mdir, "feed.token"), secrets.token_hex(32))
    write_secret(os.path.join(mdir, "care.key"), care_key)
    write_secret(os.path.join(mdir, "status.token"), status_tok)
    write_secret(os.path.join(mdir, "fitlog", "ingest.env"),
                 "FITLOG_INGEST_TOKEN=%s\nFITLOG_HC_TOKEN=%s\nFITLOG_DB=%s" % (
                     secrets.token_hex(32), secrets.token_hex(24),
                     "/srv/family/%s/fitlog/fitlog.db" % slug))
    os.makedirs(P.care_dir, exist_ok=True)
    os.chmod(P.care_dir, 0o700)
    write_secret(os.path.join(P.care_dir, slug + ".key"), care_key)
    write_secret(os.path.join(P.care_dir, slug + ".status"), status_tok)
    os.makedirs(P.etc, exist_ok=True)
    os.chmod(P.etc, 0o700)
    envp = os.path.join(P.etc, slug + ".env")
    write_secret(envp, env_text(reg, m).rstrip("\n"))
    if system:
        run(["chown", "-R", "%s:%s" % (user, user), mdir])
    for root_, dirs, files in os.walk(mdir):
        os.chmod(root_, 0o700)
        for f in files:
            os.chmod(os.path.join(root_, f), 0o600)
    # -- databases, as the member ------------------------------------------
    pin = new_pin()                       # the member signs in with this
    app_secret = secrets.token_urlsafe(24)  # the apps' own hashes: random, unused
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("GUTLOG_", "RXGUARD_", "FITLOG_", "HEALTH_SSO_", "FAMILY_")):
            del env[k]
    with open(envp) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    if P.root != "/":
        env["FAMILY_DIR"] = mdir          # a scratch root (test suites)
    if not system:
        env["FAMILY_INSECURE"] = "1"
    for app in APPS + ("care",):
        cmd = [sys.executable, "-B", init, "--app", app]
        if system:
            cmd = ["runuser", "-u", user, "--"] + cmd
        secret = pin if app == "care" else app_secret
        r = subprocess.run(cmd, input=(secret + "\n").encode(), env=env, cwd="/" if system else None,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace").strip().splitlines()
        print("  " + (out[-1] if out else "(no output)"))
        if r.returncode != 0:
            print("FAILED while creating the %s database; the member is NOT in the registry." % app)
            print("\n".join("    " + x for x in out[-15:]))
            rollback(P, slug, mdir, envp, user, made_user, system)
            return 1
    # -- the seed, as the member (26-Sep-2026) --------------------------------
    physio = None
    if a.seed:
        ok, physio = apply_seed(P, reg, slug, a.seed, system, mdir, envp, made_user, user, a.password_file)
        if not ok:
            print("The seed was not applied; the member is NOT in the registry.")
            rollback(P, slug, mdir, envp, user, made_user, system)
            return 1
    # -- the Family Kitchen, if it is installed -----------------------------
    if kitchen_register(P, slug, name, system, member_dir=mdir):
        print("  kitchen: tokens issued (API + Share shortcut)")
    # -- register, route, start --------------------------------------------
    reg["members"].append(m)
    save_registry(P, reg)
    if physio:
        stamp_physio(P, physio[0], physio[1], slug, system, a.password_file)
    write_vhost(P, load_registry(P), system)
    if system and not a.no_services:
        run(["systemctl", "enable", "--now"] + units(slug), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        print("  services: " + ", ".join(units(slug)) + " enabled and started")
    print("")
    print("  address : %s/%s/" % (reg["base"], slug))
    if a.password_file:
        write_pin_file(a.password_file, slug, name, "%s/%s/" % (reg["base"], slug), pin)
        print("  first-login PIN: written to %s (root only)" % a.password_file)
    else:
        print("  first-login PIN (shown once, written nowhere): " + pin)
    return 0


def set_enabled(P, slug, on, system):
    reg = load_registry(P)
    m = next((x for x in reg["members"] if x["slug"] == slug), None)
    if not m:
        print("No member %s." % slug)
        return 2
    m["enabled"] = bool(on)
    save_registry(P, reg)
    if system:
        run(["systemctl", "enable" if on else "disable", "--now"] + units(slug),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    write_vhost(P, reg, system)
    print("%s %s. Data kept in %s." % (slug, "enabled" if on else "disabled (stopped)",
                                       P.member(slug)))
    return 0


def rotate_care(P, slug, system):
    reg = load_registry(P)
    m = next((x for x in reg["members"] if x["slug"] == slug), None)
    if not m:
        print("No member %s." % slug)
        return 2
    mdir = P.member(slug)
    key, tok = secrets.token_hex(32), secrets.token_hex(32)
    for path, v in ((os.path.join(mdir, "care.key"), key), (os.path.join(mdir, "status.token"), tok),
                    (os.path.join(P.care_dir, slug + ".key"), key),
                    (os.path.join(P.care_dir, slug + ".status"), tok)):
        write_secret(path, v, excl=False)
    if system:
        run(["chown", m.get("user", "fam_" + slug) + ":" + m.get("user", "fam_" + slug),
             os.path.join(mdir, "care.key"), os.path.join(mdir, "status.token")])
    print("%s: caretaker key and status token rotated. Tickets in flight are void." % slug)
    return 0


def rename(P, slug, name, system):
    reg = load_registry(P)
    m = next((x for x in reg["members"] if x["slug"] == slug), None)
    name = (name or "").strip()
    if not m or not name or len(name) > 40 or "\n" in name:
        print("No member %s, or the name is not 1-40 characters." % slug)
        return 2
    m["name"] = name
    save_registry(P, reg)
    write_secret(os.path.join(P.etc, slug + ".env"), env_text(reg, m).rstrip("\n"), excl=False)
    kitchen_register(P, slug, name, system, member_dir=P.member(slug))   # the name on their cards
    if system and m.get("enabled", True):
        run(["systemctl", "restart"] + units(slug), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("%s renamed." % slug)
    return 0


def reset_pin(P, slug, system, pin_file=None):
    """The way back in for a member who forgot their PIN (or the first switch to
    PINs): a new 6-digit PIN, the lockout cleared, every device signed out, and
    the apps' old password hashes replaced with random ones nothing checks.
    Face ID / Touch ID devices stay; the member can remove them on /care."""
    import hashlib
    import sqlite3
    reg = load_registry(P)
    m = next((x for x in reg["members"] if x["slug"] == slug), None)
    if not m:
        print("No member %s." % slug)
        return 2
    from werkzeug.security import generate_password_hash
    sys.path.insert(0, HERE)
    import family_care
    import family_auth
    mdir = P.member(slug)
    pin, junk = new_pin(), secrets.token_urlsafe(24)
    care = family_care.Care(slug, mdir, reg["base"], {})
    au = family_auth.Auth(care)
    au.set_pin(pin)
    au.rotate_epoch()
    au.log("tool", "server", "PIN reset by the caretaker's server tool; every device signed out")
    for db, key, val in ((os.path.join(mdir, "gutlog", "health.db"), "pw_hash", generate_password_hash(junk)),
                         (os.path.join(mdir, "rxguard", "rxguard.db"), "password_hash",
                          generate_password_hash(junk)),
                         (os.path.join(mdir, "fitlog", "fitlog.db"), "password_hash",
                          hashlib.sha256(junk.encode()).hexdigest())):
        con = sqlite3.connect(db)
        con.execute("UPDATE settings SET value=? WHERE key=?", (val, key))
        if key == "pw_hash":   # GutLog's own epoch too
            con.execute("UPDATE settings SET value=? WHERE key='auth_epoch'", (secrets.token_hex(16),))
        con.commit()
        con.close()
    if system:
        u = m.get("user", "fam_" + slug)
        run(["chown", "-R", "%s:%s" % (u, u), mdir])
    if pin_file:
        write_pin_file(pin_file, slug, m["name"], "%s/%s/" % (reg["base"], slug), pin)
        print("%s: new PIN written to %s (root only); every device signed out." % (slug, pin_file))
    else:
        print("%s: new PIN (shown once, written nowhere): %s" % (slug, pin))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rename", metavar="NAME")
    ap.add_argument("--reset-password", action="store_true", help="same as --reset-pin")
    ap.add_argument("--reset-pin", action="store_true")
    ap.add_argument("--slug")
    ap.add_argument("--name")
    ap.add_argument("--profile", choices=("gut", "joint", "general", "weight"), default="general")
    ap.add_argument("--seed", default=None,
                    help="a gitignored seed file (setup, medicines, check-ins, plan PDF), applied as the member")
    ap.add_argument("--physio", action="store_true", help="stamp a physio sign-in (--slug p1 --name ... --for m3)")
    ap.add_argument("--for", dest="for_member", default=None, help="the member a physio is attached to")
    ap.add_argument("--disable", action="store_true")
    ap.add_argument("--enable", action="store_true")
    ap.add_argument("--rotate-care", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--refresh-env", action="store_true",
                    help="rewrite every member's /etc/family env from the registry and restart them")
    ap.add_argument("--kitchen-sync", action="store_true",
                    help="issue Family Kitchen tokens to the owner and every member; rewrite routes")
    ap.add_argument("--kitchen-only", action="store_true",
                    help="stamp a recipes-only kitchen member (--slug k1 --name ...)")
    ap.add_argument("--owner-name", default=None,
                    help="with --kitchen-sync: the name shown on the owner's own recipe cards")
    ap.add_argument("--root", default="/")
    ap.add_argument("--code", default=None,
                    help="family code tree (default: the folder above this script's)")
    ap.add_argument("--no-system", action="store_true")
    ap.add_argument("--no-services", action="store_true",
                    help="real users and per-user setup, but no systemctl / proxy (the real-path test)")
    ap.add_argument("--password-file", default=None,
                    help="append the first-login password here (mode 600) instead of printing it")
    ap.add_argument("--port-base", type=int, default=8200)
    ap.add_argument("--base-url", default=None, help="test suites only")
    a = ap.parse_args()
    system = not a.no_system
    if system:
        # 2026-09-25: the first real stamp took the folder above this script --
        # /root -- as the code tree, and the member's own user could not open
        # /root/family/init_member.py. A real stamp runs the setup as the member,
        # so it always uses the member-readable family tree unless told
        # otherwise, and stamp() refuses a tree the member cannot read.
        # FAMILY_CODE_TREE: the NEW tree while upgrade_all.sh tests it before switching.
        code = a.code or os.environ.get("FAMILY_CODE_TREE") or "/opt/family/code/current"
    else:
        code = a.code or os.environ.get("FAMILY_CODE_TREE") or os.path.dirname(HERE)
    P = Paths(a.root, code)
    if system and os.geteuid() != 0:
        print("Run as root.")
        return 2
    if a.kitchen_sync:
        # The Kitchen was installed after these members: issue their tokens,
        # the owner's too, and rewrite the routes.
        reg = load_registry(P)
        os.makedirs(P.kitchen_owner, exist_ok=True)
        os.chmod(P.kitchen_owner, 0o700)
        if a.owner_name:
            # The name on the owner's own cards ("Recipe by ..."): kept in the
            # registry, resolved by the Kitchen when a card is read.
            reg["owner_name"] = a.owner_name.strip()[:40]
            save_registry(P, reg)
        kitchen_register(P, "owner", reg.get("owner_name") or reg.get("caretaker") or "Owner", system,
                         files=(os.path.join(P.kitchen_owner, "owner.token"),
                                os.path.join(P.kitchen_owner, "owner.capture")))
        for m in reg["members"]:
            kitchen_register(P, m["slug"], m["name"], system, member_dir=P.member(m["slug"]))
        write_vhost(P, reg, system)
        print("kitchen tokens: owner + %d member(s)" % len(reg["members"]))
        return 0
    if a.refresh_env:
        # A release added a member setting (FAMILY_CARETAKER, 2026-09-25):
        # rewrite every member's env from the registry and restart them.
        reg = load_registry(P)
        for m in reg["members"]:
            write_secret(os.path.join(P.etc, m["slug"] + ".env"), env_text(reg, m).rstrip("\n"), excl=False)
            if system and m.get("enabled", True):
                run(["systemctl", "restart"] + units(m["slug"]), stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        print("env refreshed: %d member(s)" % len(reg["members"]))
        return 0
    if a.list:
        for m in load_registry(P)["members"]:
            print("%-5s %-8s %-8s %s %s" % (m["slug"], m["profile"],
                                            "on" if m.get("enabled", True) else "disabled",
                                            "/".join(str(m["ports"][x]) for x in APPS), m["name"]))
        for k in kitchen_members_list(P):
            print("%-5s %-8s %-8s %s %s" % (k["slug"], "kitchen", "on" if k["enabled"] else "disabled",
                                            "last visit " + (k["last_seen"] or "never"), k["name"]))
        for p in load_registry(P).get("physios") or []:
            print("%-5s %-8s %-8s %s %s" % (p["slug"], "physio", "on" if p.get("enabled", True) else "disabled",
                                            "for " + ", ".join(p.get("for") or []), p["name"]))
        return 0
    if a.physio or (a.slug and PSLUG_RX.match(a.slug)):
        if not a.slug or not PSLUG_RX.match(a.slug):
            print("--slug must look like p1, p2 ... for a physio (a neutral slug, never a name).")
            return 2
        if a.disable or a.enable:
            return physio_set_enabled(P, a.slug, a.enable, system)
        if a.reset_password or a.reset_pin:
            return physio_reset_pin(P, a.slug, system, a.password_file)
        if not a.for_member or not SLUG_RX.match(a.for_member):
            print("--for m3: the member this physio is attached to.")
            return 2
        return stamp_physio(P, a.slug, a.name, a.for_member, system, a.password_file)
    if a.kitchen_only or (a.slug and KSLUG_RX.match(a.slug)):
        if not a.slug or not KSLUG_RX.match(a.slug):
            print("--slug must look like k1, k2 ... for a kitchen member (a neutral slug, never a name).")
            return 2
        if a.disable or a.enable:
            return kitchen_set_enabled(P, a.slug, a.enable, system)
        if a.rename:
            return kitchen_rename(P, a.slug, a.rename, system)
        if a.reset_password or a.reset_pin:
            return kitchen_reset_pin(P, a.slug, system, a.password_file)
        if a.rotate_care:
            print("A kitchen member has no caretaker access: nothing to rotate.")
            return 2
        return stamp_kitchen(P, a, system)
    if not a.slug or not SLUG_RX.match(a.slug):
        print("--slug must look like m1, m2 ... (a neutral slug, never a name).")
        return 2
    if a.disable or a.enable:
        return set_enabled(P, a.slug, a.enable, system)
    if a.rotate_care:
        return rotate_care(P, a.slug, system)
    if a.rename:
        return rename(P, a.slug, a.rename, system)
    if a.reset_password or a.reset_pin:
        return reset_pin(P, a.slug, system, a.password_file)
    return stamp(P, a, system)


if __name__ == "__main__":
    sys.exit(main())

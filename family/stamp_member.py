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

A new member gets:
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


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def env_text(reg, m):
    lines = ["# /etc/family/%s.env -- written by stamp_member.py. Root only." % m["slug"],
             "FAMILY_SLUG=" + m["slug"], "FAMILY_DIR=/srv/family/" + m["slug"],
             "FAMILY_BASE=" + reg["base"], "FAMILY_NAME=" + m["name"].replace("\n", " "),
             "FAMILY_PROFILE=" + m["profile"]]
    for a in APPS:
        lines.append("FAMILY_PORT_%s=%d" % (a.upper(), m["ports"][a]))
    return "\n".join(lines) + "\n"


KITCHEN_PORT = 8199


def kitchen_register(P, slug, name, system, member_dir=None, files=None):
    """Give one member (or 'owner') their Family Kitchen tokens: an API token
    for their GutLog and a capture-only token for the Share shortcut. The
    Kitchen's copy is tokens.json (fam_kitchen, 600); the member's copy sits
    in their own folder. Nothing happens when the Kitchen is not installed."""
    kdir = os.path.join(P.srv, "kitchen")
    if not os.path.isdir(kdir):
        return False
    tpath = os.path.join(kdir, "tokens.json")
    try:
        with open(tpath, encoding="utf-8") as fh:
            t = json.load(fh)
    except (OSError, ValueError):
        t = {}
    t.setdefault("api", {})
    t.setdefault("capture", {})
    if member_dir:
        files = (os.path.join(member_dir, "kitchen.token"), os.path.join(member_dir, "kitchen.capture"))
    if slug in t["api"] and (not files or os.path.exists(files[0])):
        return True
    api, cap = secrets.token_hex(32), secrets.token_hex(32)
    t["api"][slug] = {"token": api, "name": name}
    t["capture"][slug] = {"token": cap}
    tmp = tpath + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(t, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, tpath)
    if system:
        run(["chown", "fam_kitchen:fam_kitchen", tpath])
    for p, v in zip(files or (), (api, cap)):
        write_secret(p, v, excl=False)
        if system and slug != "owner":
            run(["chown", "fam_%s:fam_%s" % (slug, slug), p])
    return True


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
    print("stamping %s (%s, profile %s) ports %s" % (slug, name, a.profile,
                                                     "/".join(str(ports[x]) for x in APPS)))
    # -- user and folders --------------------------------------------------
    if system:
        r = subprocess.run(["id", "-u", user], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            run(["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent",
                 "--shell", "/sbin/nologin", "--user-group", user])
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
    pw = new_password()
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("GUTLOG_", "RXGUARD_", "FITLOG_", "HEALTH_SSO_", "FAMILY_")):
            del env[k]
    with open(envp) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    if not system:
        env["FAMILY_DIR"] = mdir
        env["FAMILY_INSECURE"] = "1"
    init = os.path.join(P.code, "family", "init_member.py")
    for app in APPS + ("care",):
        cmd = [sys.executable, "-B", init, "--app", app]
        if system:
            cmd = ["runuser", "-u", user, "--"] + cmd
        r = subprocess.run(cmd, input=(pw + "\n").encode(), env=env, cwd="/" if system else None,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace").strip().splitlines()
        print("  " + (out[-1] if out else "(no output)"))
        if r.returncode != 0:
            print("FAILED while creating the %s database. The folder %s is left for "
                  "inspection; the member is NOT in the registry." % (app, mdir))
            print("\n".join("    " + x for x in out[-15:]))
            return 1
    # -- the Family Kitchen, if it is installed -----------------------------
    if kitchen_register(P, slug, name, system, member_dir=mdir):
        print("  kitchen: tokens issued (API + Share shortcut)")
    # -- register, route, start --------------------------------------------
    reg["members"].append(m)
    save_registry(P, reg)
    write_vhost(P, reg, system)
    if system:
        run(["systemctl", "enable", "--now"] + units(slug), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        print("  services: " + ", ".join(units(slug)) + " enabled and started")
    print("")
    print("  address : %s/%s/" % (reg["base"], slug))
    print("  first-login password (shown once, written nowhere): " + pw)
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
    if system and m.get("enabled", True):
        run(["systemctl", "restart"] + units(slug), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("%s renamed." % slug)
    return 0


def reset_password(P, slug, system):
    """A member has no owner key, so cannot change their own password in the
    apps; this is the way back in. New password for all three apps, shown once."""
    import hashlib
    import sqlite3
    reg = load_registry(P)
    m = next((x for x in reg["members"] if x["slug"] == slug), None)
    if not m:
        print("No member %s." % slug)
        return 2
    try:
        from werkzeug.security import generate_password_hash
    except ImportError:
        print("werkzeug is needed.")
        return 2
    pw = new_password()
    mdir = P.member(slug)
    for db, key, val in ((os.path.join(mdir, "gutlog", "health.db"), "pw_hash", generate_password_hash(pw)),
                         (os.path.join(mdir, "rxguard", "rxguard.db"), "password_hash",
                          generate_password_hash(pw)),
                         (os.path.join(mdir, "fitlog", "fitlog.db"), "password_hash",
                          hashlib.sha256(pw.encode()).hexdigest())):
        con = sqlite3.connect(db)
        con.execute("UPDATE settings SET value=? WHERE key=?", (val, key))
        if key == "pw_hash":   # GutLog: sign every device out
            con.execute("UPDATE settings SET value=? WHERE key='auth_epoch'", (secrets.token_hex(16),))
        con.commit()
        con.close()
    print("%s: new first-login password (shown once, written nowhere): %s" % (slug, pw))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rename", metavar="NAME")
    ap.add_argument("--reset-password", action="store_true")
    ap.add_argument("--slug")
    ap.add_argument("--name")
    ap.add_argument("--profile", choices=("gut", "joint", "general"), default="general")
    ap.add_argument("--disable", action="store_true")
    ap.add_argument("--enable", action="store_true")
    ap.add_argument("--rotate-care", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--kitchen-sync", action="store_true",
                    help="issue Family Kitchen tokens to the owner and every member; rewrite routes")
    ap.add_argument("--root", default="/")
    ap.add_argument("--code", default=None,
                    help="family code tree (default: the folder above this script's)")
    ap.add_argument("--no-system", action="store_true")
    ap.add_argument("--port-base", type=int, default=8200)
    ap.add_argument("--base-url", default=None, help="test suites only")
    a = ap.parse_args()
    code = a.code or os.environ.get("FAMILY_CODE_TREE") or os.path.dirname(HERE)
    if not os.path.exists(os.path.join(code, "family", "init_member.py")):
        code = "/opt/family/code/current"
    P = Paths(a.root, code)
    system = not a.no_system
    if system and os.geteuid() != 0:
        print("Run as root.")
        return 2
    if a.kitchen_sync:
        # The Kitchen was installed after these members: issue their tokens,
        # the owner's too, and rewrite the routes.
        reg = load_registry(P)
        os.makedirs(P.kitchen_owner, exist_ok=True)
        os.chmod(P.kitchen_owner, 0o700)
        kitchen_register(P, "owner", reg.get("caretaker") or "Owner", system,
                         files=(os.path.join(P.kitchen_owner, "owner.token"),
                                os.path.join(P.kitchen_owner, "owner.capture")))
        for m in reg["members"]:
            kitchen_register(P, m["slug"], m["name"], system, member_dir=P.member(m["slug"]))
        write_vhost(P, reg, system)
        print("kitchen tokens: owner + %d member(s)" % len(reg["members"]))
        return 0
    if a.list:
        for m in load_registry(P)["members"]:
            print("%-5s %-8s %-8s %s %s" % (m["slug"], m["profile"],
                                            "on" if m.get("enabled", True) else "disabled",
                                            "/".join(str(m["ports"][x]) for x in APPS), m["name"]))
        return 0
    if not a.slug or not SLUG_RX.match(a.slug):
        print("--slug must look like m1, m2 ... (a neutral slug, never a name).")
        return 2
    if a.disable or a.enable:
        return set_enabled(P, a.slug, a.enable, system)
    if a.rotate_care:
        return rotate_care(P, a.slug, system)
    if a.rename:
        return rename(P, a.slug, a.rename, system)
    if a.reset_password:
        return reset_password(P, a.slug, system)
    return stamp(P, a, system)


if __name__ == "__main__":
    sys.exit(main())

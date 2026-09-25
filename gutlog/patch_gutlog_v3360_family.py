#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.35.0 -> v3.36.0  ::  GUTLOG_V3360_FAMILY -- the Family page.

WHY: his relatives each get their own copy of GutLog, RxGuard and FitLog
(the Family Edition, family/ in this repository). He is their caretaker. He
needs one place, in his own GutLog, that says how each of them is doing and
lets him step into their copy -- without any of their data entering his.

WHAT
  * /family (his login): one row per member from /root/family/members.local.json
    -- last entry, doses taken / due / missed today, RED and AMBER in their
    RxGuard, last BP, days since the last report -- read live from each
    member's /api/care/status with that member's own bearer token. Nothing is
    stored here. A member who has switched caretaker access off shows
    "access off" and nothing else, because that is all the endpoint returns.
  * /family/open/<slug>: mints a one-use, 60-second caretaker ticket with that
    member's key (/root/family/care/<slug>.key) and sends him to their copy.
    His own GutLog session is the credential; no path leads the other way.
  * /api/family/has: how many members, for a one-line link on the Now tab.
  * Meal cards (carried from the v3.35.0 report): a card may carry "slot"
    ("auto" = by the clock) and "aka" (old names, so a meal logged under a
    card's old name can still be edited). meals.local.json is data; history
    rows are not rewritten.

No schema change. Anchor-verified, idempotent, compile-checked, .bak,
self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3360_FAMILY"
PREV = "GUTLOG_V3350_SNACKS"
VERSION = "3.36.0"

E = []
E.append(("header",
          'GUTLOG_V3350_SNACKS -- slots, grouped picker, dishes, late snacks, Quick Bite, weekly review.\n',
          'GUTLOG_V3350_SNACKS -- slots, grouped picker, dishes, late snacks, Quick Bite, weekly review.\n'
          'GUTLOG_V3360_FAMILY -- the Family page: members at a glance, open as caretaker.\n'))
E.append(("version", 'APP_VERSION = "3.35.0"   # GUTLOG_V3350_SNACKS ',
          'APP_VERSION = "3.36.0"   # GUTLOG_V3360_FAMILY GUTLOG_V3350_SNACKS '))

# ------------------------------------------------ meal cards: slot and aka
E.append(("card lookup",
          '        card = next((c for c in _meal_cfg().get("cards") or [] if c.get("name") == card_name), None)\n'
          '        if not card:\n'
          '            return {"ok": False, "err": "That meal card no longer exists."}, 400\n'
          '        pairs = _card_pairs(card, choices, onion)\n',
          '        # GUTLOG_V3360_FAMILY -- a renamed card still answers to its old names.\n'
          '        card = next((c for c in _meal_cfg().get("cards") or [] if c.get("name") == card_name\n'
          '                     or card_name in (c.get("aka") or [])), None)\n'
          '        if not card:\n'
          '            return {"ok": False, "err": "That meal card no longer exists."}, 400\n'
          '        pairs = _card_pairs(card, choices, onion)\n'
          '        card_name = card.get("name") or card_name\n'
          '        card_slot = card.get("slot") or card_name\n'))
E.append(("card slot init",
          '    card_name = (d.get("card") or "").strip()\n'
          '    choices = d.get("choices") or {}\n',
          '    card_name = (d.get("card") or "").strip()\n'
          '    card_slot = ""   # GUTLOG_V3360_FAMILY\n'
          '    choices = d.get("choices") or {}\n'))
E.append(("card slot use",
          '    slot = card_name or (d.get("slot") or meal_slot_guess(day, mtime))[:30]   # GUTLOG_V3350_SNACKS\n',
          '    # GUTLOG_V3360_FAMILY -- a card may name its slot; "auto" goes by the clock.\n'
          '    if card_slot == "auto":\n'
          '        card_slot = meal_slot_guess(day, mtime)\n'
          '    slot = card_slot or (d.get("slot") or meal_slot_guess(day, mtime))[:30]   # GUTLOG_V3350_SNACKS\n'))

# ------------------------------------------------ the Family page
FAMILY_PY = r'''
# ------------------------------------------------------------------ family
# GUTLOG_V3360_FAMILY. The Family Edition runs a separate copy of GutLog,
# RxGuard and FitLog for each relative, as their own Linux user, in their own
# folder. This page is the only place their copies meet his, and it stores
# nothing: each row is read live from that member's /api/care/status, which
# returns summary fields only -- and only while the member allows it.
FAMILY_FILE = os.environ.get("GUTLOG_FAMILY_FILE", "/root/family/members.local.json")
FAMILY_CARE_DIR = os.environ.get("GUTLOG_FAMILY_CARE_DIR", "/root/family/care")
FAMILY_TTL = 60
FAMILY_SLUG = re.compile(r"^m[0-9]{1,3}$")


def family_cfg():
    try:
        with open(FAMILY_FILE, encoding="utf-8") as fh:
            j = json.load(fh)
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def family_members(cfg=None):
    cfg = cfg if cfg is not None else family_cfg()
    return [m for m in (cfg.get("members") or [])
            if isinstance(m, dict) and FAMILY_SLUG.match(str(m.get("slug") or ""))
            and m.get("enabled", True)]


def _family_secret(slug, ext):
    try:
        with open(os.path.join(FAMILY_CARE_DIR, slug + "." + ext), "rb") as fh:
            v = fh.read().strip()
        return v if len(v) >= 32 else None
    except OSError:
        return None


def family_status(m):
    """One member's summary, or {"ok": False, "err": ...}. Never raises."""
    import urllib.request
    tok = _family_secret(m["slug"], "status")
    port = int((m.get("ports") or {}).get("gut") or 0)
    if not tok or not port:
        return {"ok": False, "err": "not set up"}
    url = "http://127.0.0.1:%d/%s/api/care/status" % (port, m["slug"])
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok.decode("ascii")})
        with op.open(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"ok": False, "err": "not reachable"}


def family_ticket(slug, who, app_name="gut"):
    """The same ticket family/family_care.py verifies: HMAC-SHA256 over
    {aud, app, who, exp, n} with this member's own key. 60 s, used once."""
    import base64
    import hashlib
    import hmac
    k = _family_secret(slug, "key")
    if not k:
        return None
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")
    body = b64(json.dumps({"aud": slug, "app": app_name, "who": who,
                           "exp": int(time.time()) + FAMILY_TTL, "n": secrets.token_hex(12)},
                          separators=(",", ":")).encode())
    return body + "." + b64(hmac.new(k, body.encode("ascii"), hashlib.sha256).digest())


def _family_ago(stamp):
    try:
        t = datetime.fromisoformat(str(stamp)[:19])
    except (TypeError, ValueError):
        return "nothing logged yet"
    mins = int((datetime.now() - t).total_seconds() // 60)
    if mins < 60:
        return "%d min ago" % max(mins, 0)
    if mins < 48 * 60:
        return "%d h ago" % (mins // 60)
    return "%d days ago" % (mins // 1440)


@app.route("/family")
@login_required
def family_page():
    import html as _h
    cfg = family_cfg()
    rows = []
    for m in family_members(cfg):
        st = family_status(m)
        name = _h.escape(str(m.get("name") or m["slug"]))
        if not st.get("ok"):
            rows.append("<div class='fm'><p class='q'>%s</p><p class='hint'>%s</p></div>"
                        % (name, _h.escape(st.get("err") or "not reachable")))
            continue
        if st.get("access") != "on":
            rows.append("<div class='fm'><p class='q'>%s</p><p class='hint'>Caretaker access off "
                        "&mdash; they have switched it off.</p></div>" % name)
            continue
        d = st.get("doses") or {}
        rx = st.get("rx")
        bp = st.get("bp")
        rep = st.get("days_since_report")
        bits = [
            "Last entry: " + _h.escape(_family_ago(st.get("last_entry"))),
            "Doses today: %d of %d taken%s%s" % (
                d.get("taken", 0), d.get("total", 0),
                (", %d due" % d["due"]) if d.get("due") else "",
                (", <b class='bad'>%d missed</b>" % d["missed"]) if d.get("missed") else ""),
            ("RxGuard: <b class='bad'>%d RED</b>" % rx["red"]) if rx and rx.get("red")
            else ("RxGuard: %s" % ("no RED" if rx else "not reachable")),
            ("Last BP: %s/%s on %s" % (bp.get("sys"), bp.get("dia"), _h.escape(str(bp.get("day")))))
            if bp else "Last BP: none",
            ("Last report: %d days ago" % rep) if rep is not None else "Last report: none",
        ]
        rows.append("<div class='fm'><p class='q'>%s</p><p>%s</p>"
                    "<a class='btn' href='/family/open/%s'>Open as caretaker</a></div>"
                    % (name, "<br>".join(bits), _h.escape(m["slug"])))
    body = "".join(rows) or "<p class='hint'>No family members yet.</p>"
    return Response(FAMILY_PAGE.replace("__ROWS__", body), mimetype="text/html")


@app.route("/family/open/<slug>")
@login_required
def family_open(slug):
    cfg = family_cfg()
    m = next((x for x in family_members(cfg) if x.get("slug") == slug), None)
    if not m or not FAMILY_SLUG.match(slug):
        abort(404)
    who = str(cfg.get("caretaker") or "caretaker")[:40]
    tok = family_ticket(slug, who)
    base = str(cfg.get("base") or "").rstrip("/")
    if not tok or not (base.startswith("https://") or base.startswith("http://127.0.0.1:")):
        abort(404)
    from urllib.parse import urlencode
    return redirect(base + "/" + slug + "/care/in?" + urlencode({"t": tok, "next": "/"}))


@app.route("/api/family/has")
@login_required
def api_family_has():
    return jsonify(n=len(family_members()))


FAMILY_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Family</title>
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--bad:#a4262c}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--bad:#ff8a80}}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:34rem;margin:0 auto;padding:16px}h1{font-size:22px;margin:6px 0 12px}
.fm{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 12px}
.q{font-weight:700;margin:0 0 6px}.hint{color:var(--mut)}.bad{color:var(--bad)}
.btn{display:inline-block;margin-top:10px;padding:11px 16px;border-radius:10px;background:var(--acc);color:#fff;text-decoration:none;font-weight:600}
a.back{color:var(--acc)}
</style></head><body><main><h1>Family</h1>__ROWS__
<p class="hint">Read live from each person's own copy. Nothing about them is kept in your GutLog.</p>
<p><a class="back" href="/">Back</a></p></main></body></html>"""

'''
E.append(("family block",
          "# ------------------------------------------------------------------ auto-read\n"
          "# GUTLOG_V3100_AUTOREAD -- start the report reader the moment a file lands.\n",
          FAMILY_PY.lstrip("\n") + "\n"
          "# ------------------------------------------------------------------ auto-read\n"
          "# GUTLOG_V3100_AUTOREAD -- start the report reader the moment a file lands.\n"))

# ------------------------------------------------ Now tab: one-line link
E.append(("now family div",
          '  <div id="nowMirror"></div>\n',
          '  <div id="nowMirror"></div>\n'
          '  <div id="nowFamily"></div><!-- GUTLOG_V3360_FAMILY -->\n'))
E.append(("now family js",
          "async function loadNow(){\n  loadMirror();\n",
          "/* GUTLOG_V3360_FAMILY -- one line, only when there are members. */\n"
          "async function loadFamilyLink(){\n"
          "  const box=$('#nowFamily');if(!box)return;\n"
          "  let j;try{j=await jget('/api/family/has');}catch(e){return;}\n"
          "  box.innerHTML='';\n"
          "  if(!j||!j.n)return;\n"
          "  const p=el('p','hint');p.style.margin='0 2px 10px';\n"
          "  const a=el('a','',j.n===1?'Family: 1 person':'Family: '+j.n+' people');a.href='/family';\n"
          "  p.appendChild(a);box.appendChild(p);\n"
          "}\n"
          "async function loadNow(){\n  loadMirror();\n  loadFamilyLink();\n"))

EDITS = E
JINJA = ("{{", "{%", "{#")


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("==================================================================")
    print("GutLog Family page -> v" + VERSION)
    print("file : " + a.file)
    print("==================================================================")
    for label, old, new in EDITS:
        for tok in JINJA:
            if new.count(tok) > old.count(tok):
                print("FATAL: %s adds the Jinja token %r. Nothing written." % (label, tok))
                return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1
    bad = [(l, src.count(o)) for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        for l, c in bad:
            print("  %s: found %d times, need 1" % (l, c))
        print("Refusing to patch. Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    bak = a.file + ".bak-v3360-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    print("backup : " + bak)
    write(a.file, out)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, a.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(EDITS))
    print("Next:  python3 test_v3360_family.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

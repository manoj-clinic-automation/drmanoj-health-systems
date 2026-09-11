#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog v1.0.1 -> v1.1.0  ::  read doses from GutLog (read-endpoint cutover)

Doses are logged in GutLog. FitLog kept its own analgesic log, so W03
("analgesic use on N of 14 days") only knew about doses tapped in FitLog --
anything logged in GutLog was invisible to it, and logging in both places
was the only way to keep it honest.

  * W03 now counts the UNION of days from FitLog's own log and GutLog's
    dose feed. A day logged in both counts once.
  * A GutLog dose counts when its molecule appears in the generic name of
    an active FitLog stack entry, and takes that entry's category
    (analgesic first). Unmatched molecules are ignored -- W03 counts
    analgesics only, and a guess would inflate it.
  * The Meds page shows the last 14 days from GutLog beside FitLog's own
    log, and the per-category day counts use both.
  * FitLog's own tap-to-log stays exactly as it was: the manual path is the
    fallback.

The feed follows the live database: a scratch database (tests) never reads
it, so the 53-check smoke suite is unaffected on the server. If GutLog is
down, FitLog behaves exactly as v1.0.1 and says so on the Meds page.
Nothing in any rule threshold changes.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring on post-write failure. Python 3.9.
"""

import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/fitlog/app.py"
MARKER = "FITLOG_V110_GUTLOG_FEED"

FEED = r'''# ---------------- GutLog dose feed (FITLOG_V110_GUTLOG_FEED) ----------------
# Doses are logged in GutLog; W03 and the Meds page read them from its
# read-only feed and union them with FitLog's own log. Never raises.
GUTLOG_FEED_URL = os.environ.get("GUTLOG_FEED_URL", "http://127.0.0.1:8020")
GUTLOG_TOKEN_FILE = os.environ.get("GUTLOG_FEED_TOKEN_FILE", "/root/gutlog/feed.token")
_GL_CACHE = {}


def gutlog_feed_enabled():
    """Follows the live database: a scratch DB outside the app folder (the
    test suites) never reads real doses. FITLOG_GUTLOG_FEED=1/0 overrides."""
    v = os.environ.get("FITLOG_GUTLOG_FEED", "")
    if v in ("0", "1"):
        return v == "1"
    return os.path.dirname(os.path.abspath(DB_PATH)) == APP_DIR


def gutlog_events(since):
    """GutLog dose events on or after `since`: (events, error). Cached 60 s."""
    import time
    import urllib.request
    if not gutlog_feed_enabled():
        return [], "off"
    hit = _GL_CACHE.get(since)
    if hit and time.time() - hit[0] < 60:
        return hit[1], hit[2]
    events, err = [], ""
    try:
        with open(GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
        req = urllib.request.Request(
            GUTLOG_FEED_URL.rstrip("/") + "/api/feed/doses?since=" + since,
            headers={"Authorization": "Bearer " + tok})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("ok"):
            events = data.get("events") or []
        else:
            err = "GutLog returned an unexpected answer"
    except Exception as e:
        err = "GutLog not reachable (" + type(e).__name__ + ")"
    _GL_CACHE[since] = (time.time(), events, err)
    return events, err


def gutlog_catmap():
    """molecule -> FitLog category, via the generic names in the stack."""
    stack = [((m["generic"] or "").lower(), m["category"]) for m in db().execute(
        "SELECT generic, category FROM med_stack WHERE active=1").fetchall()]
    memo = {}

    def cat(molecule):
        mol = (molecule or "").strip().lower()
        if not mol:
            return ""
        if mol not in memo:
            found = ""
            for gen, c in stack:
                if gen and mol in gen:
                    if c == "analgesic":
                        found = c
                        break
                    found = found or c
            memo[mol] = found
        return memo[mol]
    return cat


def gutlog_days(category, since):
    events, _err = gutlog_events(since)
    cat = gutlog_catmap()
    return set(e.get("day") for e in events if e.get("day") and cat(e.get("molecule")) == category)


'''

W03_OLD = '''    days = db().execute("""SELECT COUNT(DISTINCT substr(dt,1,10)) c FROM analgesic_log l
                           JOIN med_stack m ON m.id=l.med_id
                           WHERE m.category='analgesic' AND substr(dt,1,10)>?""", (w3,)).fetchone()["c"]
    if days >= TH["w03_analgesic_days"]:
        flags.append(("W03", f"Analgesic use on {days} days in last {TH['w03_window_days']} \\u2014 minimum-effective-analgesia review"))
'''
W03_NEW = '''    local = set(r["d"] for r in db().execute("""SELECT DISTINCT substr(dt,1,10) d FROM analgesic_log l
                           JOIN med_stack m ON m.id=l.med_id
                           WHERE m.category='analgesic' AND substr(dt,1,10)>?""", (w3,)).fetchall())
    gl = gutlog_days("analgesic", (date.fromisoformat(w3) + timedelta(days=1)).isoformat())
    days = len(local | gl)
    src = " (incl. GutLog)" if gl - local else ""
    if days >= TH["w03_analgesic_days"]:
        flags.append(("W03", f"Analgesic use on {days} days in last {TH['w03_window_days']}{src} \\u2014 minimum-effective-analgesia review"))
'''

MEDS_OLD = '''    counts = db().execute("""SELECT m.category,COUNT(DISTINCT substr(dt,1,10)) d FROM analgesic_log l
                             JOIN med_stack m ON m.id=l.med_id WHERE substr(dt,1,10)>? GROUP BY m.category""", (w3,)).fetchall()
    cs = " \\u00b7 ".join(f"{r['category']}: {r['d']} days/14" for r in counts) or "no use logged in 14 days"
    body = f"""<h1>Medication log</h1><p class=small>Tap drug dose = logged with timestamp. {cs}.
    <a href="/meds/manage">Manage stack \\u2192</a></p>{cards}
    <div class="card"><h2>Recent</h2><table>{hist}</table></div>"""
'''
MEDS_NEW = '''    import html as _html
    pairs = db().execute("""SELECT DISTINCT m.category c, substr(dt,1,10) d FROM analgesic_log l
                             JOIN med_stack m ON m.id=l.med_id WHERE substr(dt,1,10)>?""", (w3,)).fetchall()
    bycat = {}
    for r in pairs:
        bycat.setdefault(r["c"], set()).add(r["d"])
    gl_since = (date.fromisoformat(w3) + timedelta(days=1)).isoformat()
    gev, gerr = gutlog_events(gl_since)
    gcat = gutlog_catmap()
    grows = []
    for e in gev:
        c = gcat(e.get("molecule"))
        if c:
            bycat.setdefault(c, set()).add(e.get("day"))
            grows.append((e, c))
    counts = sorted(bycat.items())
    cs = " \\u00b7 ".join(f"{c}: {len(d)} days/14" for c, d in counts) or "no use logged in 14 days"
    if gerr == "off":
        glcard = ""
    elif gerr:
        glcard = f'<div class="card"><h2>From GutLog</h2><p class=small>{_html.escape(gerr)} \\u2014 counts above are FitLog only.</p></div>'
    else:
        gl_hist = "".join("<tr><td>" + _html.escape((e.get("day") or "")[5:] + " " + (e.get("time") or "")) +
                          "</td><td>" + _html.escape(e.get("name") or "") + "</td><td class=small>" + c + "</td></tr>"
                          for e, c in reversed(grows[-20:]))
        glcard = ('<div class="card"><h2>From GutLog, last 14 days</h2><p class=small>Doses logged in GutLog count here '
                  'automatically, matched by molecule to your stack.</p><table>' +
                  (gl_hist or "<tr><td class=small>No matching doses.</td></tr>") + "</table></div>")
    body = f"""<h1>Medication log</h1><p class=small>Tap drug dose = logged with timestamp. {cs}.
    <a href="/meds/manage">Manage stack \\u2192</a></p>{cards}
    <div class="card"><h2>Recent</h2><table>{hist}</table></div>{glcard}"""
'''


def build_edits():
    E = []
    a = "# ---------------- warning flags ----------------\n"
    E.append(("feed functions", a, FEED + a))
    E.append(("W03 union", W03_OLD, W03_NEW))
    E.append(("meds page", MEDS_OLD, MEDS_NEW))
    a = '"""\nFitLog v1.0 '
    E.append(("marker", a, '"""\n' + MARKER + ' -- FitLog v1.1.0 reads doses from GutLog.\nFitLog v1.0 '))
    return E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("FitLog GutLog feed -> v1.1.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = open(args.file, "r", encoding="utf-8").read()
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1
    if args.check:
        print("All anchors OK.")
        return 0
    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    tmpd = tempfile.mkdtemp()
    tmpf = os.path.join(tmpd, "cand.py")
    with open(tmpf, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-v110-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("-" * 60)
    print("Next:  systemctl restart fitlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

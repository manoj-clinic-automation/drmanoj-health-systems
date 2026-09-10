#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NO_SECRETS.py -- pre-publish gate for drmanoj-health-systems.

THIS REPOSITORY IS PUBLIC. Two checks run over the files git is about to
commit:

  A. SECRETS          BLOCKS the publish (exit 1)
     Live databases, env files, .secret files, .bak snapshots, and
     credential-shaped literals. A database here is the diary itself; an
     env file holds bearer tokens. Neither may ever be committed.

  B. CLINICAL DETAIL  WARNS only (never blocks, exit stays 0)
     Drug and molecule names drawn from prnmeds. The regimen is already
     public in gutlog/add_regimen.py and the dossier, so blocking every
     commit that mentions a drug would be pure noise -- the point is to
     notice when a commit ADDS clinical detail, before it publishes, and
     let a human decide.

Never prints a secret value. Matched credentials are reported by file,
line and kind only.

WHERE THE DRUG LIST LIVES, AND WHY NOT HERE
    tools/clinical_terms.local.txt, which is gitignored.

    Embedding the medication list in a tracked file would add the exact
    clinical detail this check exists to flag -- the gate would become the
    disclosure. The list is generated from the live database and stays on
    the machine. If the file is absent the clinical check does not run,
    and says so loudly rather than passing quietly.

    Regenerate it (from a machine with server access):

      ssh root@<server> "python3 - <<'PY'
      import re, sqlite3
      c = sqlite3.connect('<gutlog-db-path>')
      t = set()
      for name, mol in c.execute('SELECT name, molecule FROM prnmeds'):
          for f in (name, mol):
              if f:
                  for tok in re.split(r'[^A-Za-z]+', f):
                      if len(tok) >= 4:
                          t.add(tok.lower())
      print('\\n'.join(sorted(t)))
      PY" > tools/clinical_terms.local.txt

Usage:
    python NO_SECRETS.py --files-from <list-of-paths> [base]
    python NO_SECRETS.py [base]            # scan everything under base

Exit: 1 if a secret was found, else 0.

Python 3.9 compatible.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TERMS_FILE = os.path.join(HERE, "clinical_terms.local.txt")

# ---- A. secrets ----------------------------------------------------------

# Paths that must never be committed at all.
FORBIDDEN_PATH = re.compile(
    r"(\.db$|\.db-wal$|\.db-shm$|\.env$|\.secret$|\.bak_|\.bak-|\.deployed-)",
    re.IGNORECASE,
)

# A credential-shaped literal: a bare value straight after the '='. If the
# next character is a quote or a space the line is almost always code that
# BUILDS the name, e.g.  print('FITLOG_HC_TOKEN=' + secrets.token_urlsafe(24))
# -- which is a recipe, not a secret.
CREDENTIAL = re.compile(
    r"(?P<kind>[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|APIKEY|API_KEY|PRIVATE_KEY))"
    r"\s*=\s*(?P<val>[A-Za-z0-9_\-\.]{16,})",
)

# Long hex, as a rotated Flask signing key would look.
HEX_BLOB = re.compile(r"\b[0-9a-f]{48,}\b")

# Values that are obviously fixtures, not credentials.
PLACEHOLDER = re.compile(
    r"(smoke|test|example|sample|dummy|placeholder|changeme|do-not-use"
    r"|your[-_]?|xxx|redacted|rotate)",
    re.IGNORECASE,
)

SKIP_DIRS = (".git", "__pycache__", "venv", "node_modules")
# Binary-ish things we never scan for text.
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pdf", ".woff",
            ".woff2", ".ttf", ".db", ".pyc")


def load_terms():
    """Drug and molecule names. Empty list means the check is unarmed."""
    if not os.path.exists(TERMS_FILE):
        return None
    # Form words, not clinical identifiers -- they fire on ordinary prose.
    stop = set(["nasal", "spray", "sachet", "tablet", "capsule", "syrup",
                "drops", "cream", "oral", "injection", "powder"])
    terms = []
    # utf-8-sig, not utf-8: a BOM on the first line stopped it being seen as
    # a comment, and the whole comment line was loaded as a phantom "term".
    with open(TERMS_FILE, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            t = line.strip().lower()
            if not t or t.startswith("#") or len(t) < 4 or t in stop:
                continue
            terms.append(t)
    return sorted(set(terms))


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (IOError, OSError):
        return None


def scan_secrets(path, text):
    """Return a list of (line_no, kind). Never returns the value itself."""
    hits = []
    for i, line in enumerate(text.split("\n"), 1):
        m = CREDENTIAL.search(line)
        if m and not PLACEHOLDER.search(m.group("val")):
            hits.append((i, m.group("kind") + " literal"))
        for blob in HEX_BLOB.findall(line):
            if not PLACEHOLDER.search(line):
                hits.append((i, "long hex value, " + str(len(blob)) + " chars"))
    return hits


def scan_clinical(text, terms):
    """Return {term: first_line_no} for drug/molecule names present."""
    found = {}
    lowered = text.lower()
    for t in terms:
        if t not in lowered:
            continue
        pat = re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)
        for i, line in enumerate(text.split("\n"), 1):
            if pat.search(line):
                found[t] = i
                break
    return found


def gather(args):
    """(files, base) from --files-from, or a walk of base."""
    base = "."
    files = None
    if args and args[0] == "--files-from":
        if len(args) < 2:
            print("--files-from needs a file holding one path per line")
            return None, None
        listing = args[1]
        base = args[2] if len(args) > 2 else "."
        try:
            with open(listing, "r", encoding="utf-8", errors="replace") as fh:
                files = [ln.strip() for ln in fh if ln.strip()]
        except IOError:
            print("could not read " + listing)
            return None, None
    else:
        if args:
            base = args[0]
        files = []
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), base)
                files.append(rel.replace("\\", "/"))
    return files, base


def main():
    args = sys.argv[1:]
    files, base = gather(args)
    if files is None:
        return 0

    terms = load_terms()

    secret_hits = []      # (path, line, kind)
    path_hits = []        # path
    clinical_hits = []    # (path, term, line)

    for rel in files:
        full = os.path.join(base, rel)
        if FORBIDDEN_PATH.search(rel):
            path_hits.append(rel)
            continue
        if rel.lower().endswith(SKIP_EXT):
            continue
        if not os.path.exists(full) or os.path.isdir(full):
            continue
        text = read_text(full)
        if text is None:
            continue
        for line_no, kind in scan_secrets(full, text):
            secret_hits.append((rel, line_no, kind))
        if terms:
            for term, line_no in scan_clinical(text, terms).items():
                clinical_hits.append((rel, term, line_no))

    blocked = bool(path_hits or secret_hits)

    # ---- clinical: warn, never block -------------------------------------
    print("")
    if terms is None:
        print("  !! CLINICAL CHECK DID NOT RUN")
        print("     tools/clinical_terms.local.txt is missing. It is")
        print("     gitignored by design; regenerate it from prnmeds -- see")
        print("     the header of this file. Continuing without the check.")
    elif clinical_hits:
        byfile = {}
        for rel, term, line_no in clinical_hits:
            byfile.setdefault(rel, []).append((term, line_no))
        total = len(set(t for _r, t, _l in clinical_hits))
        print("  NOTE - clinical detail in what you are about to publish")
        print("  " + str(total) + " drug/molecule name(s) across "
              + str(len(byfile)) + " file(s). THIS REPOSITORY IS PUBLIC.")
        print("")
        for rel in sorted(byfile):
            shown = sorted(byfile[rel])[:6]
            more = len(byfile[rel]) - len(shown)
            desc = ", ".join(t + ":" + str(n) for t, n in shown)
            if more > 0:
                desc = desc + ", +" + str(more) + " more"
            print("     " + rel)
            print("        " + desc)
        print("")
        print("  This does NOT block. The regimen is already public in")
        print("  gutlog/add_regimen.py and the dossier. Read the list: if a")
        print("  file here is publishing clinical detail that was not")
        print("  already out, stop and decide before it ships.")
    else:
        print("  clinical check: no drug or molecule names in staged files.")

    # ---- secrets: block ---------------------------------------------------
    if not blocked:
        print("  secrets check: clean.")
        print("")
        return 0

    print("")
    print("!! REFUSING - a secret or live-data file is staged.")
    if path_hits:
        print("")
        print("   FILES THAT MUST NEVER BE COMMITTED:")
        for rel in path_hits:
            print("      " + rel)
        print("      A .db is the diary. A .env or .secret holds credentials.")
        print("      Fix .gitignore, then: git reset")
    if secret_hits:
        print("")
        print("   CREDENTIAL-SHAPED VALUES (value not shown):")
        for rel, line_no, kind in secret_hits:
            print("      " + rel + ":" + str(line_no) + "  " + kind)
        print("      Take the value out of the file and read it from the")
        print("      environment or a conf file on the box. If it was ever")
        print("      committed, it must also be ROTATED.")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())

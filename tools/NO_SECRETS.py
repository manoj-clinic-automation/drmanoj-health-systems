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

  B. CLINICAL DETAIL, staged   WARNS only (never blocks, exit stays 0)
     Drug and molecule names in the files about to be committed, listed so
     a human can look before it ships.

  C. CLINICAL DETAIL, TRACKED  BLOCKS the publish (exit 1)
     2026-09-14. B only ever asked what was being ADDED. It never once
     asked what was ALREADY THERE -- so four documents naming his
     medicines sat in the public tree for weeks, and every clean run said
     "clinical check: no drug or molecule names in staged files", which was
     true and useless. Exactly the shape of the findstr CRLF hole in
     PUBLISH_HEALTH.bat, one layer up: a gate that guards the doorway and
     never looks at the room.

     C scans `git ls-files` -- the INDEX, so it covers both what is already
     committed and what has just been staged -- and BLOCKS on any clinical
     term outside CLINICAL_ALLOW.

     2026-09-15: C had never once run here. It looked for a bare "git" on
     PATH; this machine has no Git for Windows at all and the only git is the
     one bundled inside GitHub Desktop, which is not on PATH. See
     resolve_git() below. C still BLOCKS when it cannot run -- a check that
     did not run is not a check that passed, and that is the whole point of
     it being C rather than B.

     CLINICAL_ALLOW is small, explicit, and each entry carries its reason.
     It exists because RxGuard's deterministic knowledge base is drug names
     BY DESIGN (CLAUDE.md rules 1 and 5c) and must stay in the repo. A
     pharmacology file stating what one molecule does to another is not a
     statement that he takes either of them. A file that says HE takes one
     is, and that is what C is for. When in doubt a path does NOT go on the
     list -- fix the file instead.

     (This paragraph originally named two molecules as the illustration.
     C caught its own file on the first run. Left recorded because it is
     the cleanest possible demonstration of why the list lives in a
     gitignored file and why C blocks rather than warns.)

  D. TRACKED BINARIES          BLOCKS the publish (exit 1)
     2026-09-15. Fifteen PNGs were tracked here and nine were pictures of the
     health record -- the regimen with brand names and strengths, stock
     counts, dated dose times, two windows of blood pressure. Every NO_SECRETS
     run before this one reported clean, correctly: A, B and C read TEXT, and
     a PNG is bytes. No amount of care in those three could have seen it.

     Nor can judgement be the defence. Of those fifteen, one that looked like
     a scan of a real lab report was synthetic, and one that looked like a
     harmless UI screenshot carried a real date in a filename field.

     So D is structural: a tracked file with a binary or image extension is
     REFUSED unless it is in BINARY_ALLOW, which holds two PWA icons and each
     entry's reason. No screenshot is on it -- a synthetic screenshot becomes
     a live one the next time its suite meets a real database. D also reads
     the first bytes of every tracked file that is not binary by extension,
     because an extension rule alone is defeated by renaming.

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

import glob
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TERMS_FILE = os.path.join(HERE, "clinical_terms.local.txt")

# ---- C. paths allowed to carry drug names, and why -------------------------
# Generic pharmacology, not his record. A reader of these cannot tell which
# of the drugs named is one he takes -- that is the whole distinction. Add a
# path here only after deciding that; the default for anything new is to keep
# it out and fix the file instead.
CLINICAL_ALLOW = {
    "rxguard/knowledge/drugs.json":
        "the curated knowledge base itself - drug properties, CLAUDE.md rule 1",
    "rxguard/knowledge/rules.json":
        "the curated interaction rules - pairs and classes, CLAUDE.md rule 1",
    "rxguard/kb_sources.py":
        "name aliases for the RxNorm/openFDA lookups, no personal content",
    "rxguard/patch_rxguard_v140.py":
        "the patcher that wrote those same curated rules",
    "rxguard/patch_rxguard_v183.py":
        "the patcher that wrote the orexin-antagonist entry and its label rules "
        "(PW025-PW034, CR016) into the curated base - generic label pharmacology, "
        "the same content as knowledge/rules.json; its test names no drug",
    "rxguard/patch_rxguard_v184.py":
        "the patcher that wrote the NaSSA entry's label fields and rules "
        "(PW035-PW047, CR017, mao_inhibitor_keys) into the curated base - generic "
        "label pharmacology, the same content as knowledge/*.json; its test names no drug",
    "rxguard/test_conditions.py":
        "synthetic condition fixtures over the generic rule set",
    "rxguard/test_kb.py":
        "synthetic DDInter/RxNorm fixtures; the names are the vendors' own",
    "rxguard/smoke_test.py":
        "declared-synthetic fixture - its own header states every drug, dose, "
        "indication and symptom in it is invented to exercise a rule",
    "rxguard/test_astaken_honest.py":
        "declared-synthetic fixture - its header states that the molecules "
        "are chosen for the thresholds they cross and that none of them is on "
        "the owner's record, which is what makes it unreadable as a list",
    "rxguard/knowledge/dose_rules.generic.json":
        "FAMILY_EDITION_V1 / RxGuard v1.9.0 - label-maximum daily ceilings for a "
        "generic set of pain medicines, each with its cited source; used only by a "
        "copy with no personal dose-rules file. It lists every common analgesic "
        "the same way and says nothing about who takes any of them - the personal "
        "file stays knowledge/dose_rules.local.json, gitignored",
    "rxguard/validation_cases.json":
        "the engine's formal validation suite, with acceptance thresholds "
        "declared before the run; its 'index case' is the suite's, not a person's",
}

# ---- D. the only binaries allowed in the tree, and why ---------------------
# THE DEFAULT IS NO. A binary is a file this checker cannot read, in a public
# repository, and the last time the default was "whatever is already there" it
# was nine pictures of his medicines, his stock, his dose times and his blood
# pressure. An entry here is a decision that the file contains no record and
# cannot come to contain one.
#
# Screenshots are deliberately absent and must stay absent. A screenshot is
# synthetic only until the suite that writes it meets a real database, which
# is exactly how the nine got there. gutlog/test_ui_now.py now writes its
# screenshots outside the repository entirely, so there is nothing to allow.
BINARY_ALLOW = {
    "gutlog/icon-192.png":
        "PWA manifest icon, served by gutlog/pwa.py - an abstract glyph, no "
        "content, and the app will not install without it",
    "gutlog/icon-512.png":
        "the same icon at 512px, same manifest, same reason",
}

# Binary and image extensions. Broad on purpose: the cost of a false positive
# is one allowlist entry with a reason, and the cost of a false negative is
# the health record in a public repo.
BINARY_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico",
    ".svg", ".heic", ".avif",
    ".pdf", ".zip", ".7z", ".rar", ".gz", ".tgz", ".bz2", ".xz", ".tar",
    ".db", ".sqlite", ".sqlite3", ".mdb",
    ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".ods",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".npy", ".npz", ".pyc",
    ".pkl", ".pickle",
)

# First bytes of the formats worth naming. This is the anti-rename half of D:
# an extension rule on its own is defeated by calling a PNG a .md.
BINARY_MAGIC = (
    (b"\x89PNG\r\n", "PNG"), (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"), (b"GIF89a", "GIF"), (b"BM", "BMP"),
    (b"%PDF", "PDF"), (b"PK\x03\x04", "ZIP or Office file"),
    (b"SQLite format 3", "SQLite database"), (b"\x1f\x8b", "gzip"),
    (b"RIFF", "RIFF - WAV, AVI or WebP"), (b"OggS", "Ogg"),
    (b"\x00\x00\x01\x00", "Windows icon"), (b"wOFF", "WOFF font"),
    (b"wOF2", "WOFF2 font"), (b"\x00\x01\x00\x00\x00", "TrueType font"),
    (b"MZ", "Windows executable"), (b"\x7fELF", "ELF binary"),
    (b"\x93NUMPY", "NumPy array"),
)


def binary_kind(path):
    """The format name if this file starts like a binary, else ''."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except (IOError, OSError):
        return ""
    for sig, name in BINARY_MAGIC:
        if head.startswith(sig):
            return name
    return ""


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


# ---- finding git, which is a different question from "is git installed" ----
# On Windows these come apart. Here there is no Git for Windows at all and
# never has been; the only git on the machine is the copy bundled inside
# GitHub Desktop, under a version-stamped folder that changes with every app
# update, so it cannot be hardcoded either. PUBLISH_HEALTH.bat has resolved it
# that way since it was written -- this is the same search, moved into the
# checker, because the checker is what needs it and must not depend on who
# called it.
def _git_runs(path):
    """Whether this really is a git that answers.

    Existing on disk is not the same as working: a half-removed install, or a
    stale version folder left behind by an update, leaves the file there.
    """
    try:
        proc = subprocess.Popen([path, "--version"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        proc.communicate()
        return proc.returncode == 0
    except OSError:
        return False


def git_candidates():
    """Every place a git that is NOT on PATH may still be, newest first."""
    out = []
    for root in (os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 "C:\\Program Files", "C:\\Program Files (x86)"):
        if root:
            cand = os.path.join(root, "Git", "cmd", "git.exe")
            if cand not in out:
                out.append(cand)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        apps = glob.glob(os.path.join(local, "GitHubDesktop", "app-*"))
        # newest first, by mtime rather than by name: a lexical sort puts
        # app-3.6.9 above app-3.6.10, and an update leaves the old folder
        # behind for a while.
        apps.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for a in apps:
            out.append(os.path.join(a, "resources", "app", "git", "cmd",
                                    "git.exe"))
    return out


_UNSET = object()


def resolve_git(named=_UNSET):
    """(path, error). Never guesses, and never quietly gives up.

    NO_SECRETS_GIT is an INSTRUCTION, not a hint. If it is set and does not
    work that is an error, and the search does not continue: being told which
    git to use and silently using a different one is how a check ends up
    reporting on something other than what the caller meant.

    `named` defaults to $NO_SECRETS_GIT. Pass it explicitly -- None for "no
    instruction given" -- to exercise the search without editing the
    environment, which is how the refusal paths are tested.
    """
    if named is _UNSET:
        named = os.environ.get("NO_SECRETS_GIT")
    if named:
        if not os.path.exists(named):
            return None, ("NO_SECRETS_GIT names " + named
                          + ", which does not exist. It is an instruction, so "
                            "no other git was tried.")
        if not _git_runs(named):
            return None, ("NO_SECRETS_GIT names " + named
                          + ", which exists but will not run as git. It is an "
                            "instruction, so no other git was tried.")
        return named, None
    found = shutil.which("git")
    if found and _git_runs(found):
        return found, None
    tried = []
    for cand in git_candidates():
        if not os.path.exists(cand):
            tried.append(cand)
            continue
        if _git_runs(cand):
            return cand, None
        tried.append(cand + "  (present but will not run)")
    return None, ("git is not on PATH and no usable copy was found. Looked "
                  "at: " + "; ".join(tried[:6] or ["nothing"])
                  + ".  Install Git for Windows, or point NO_SECRETS_GIT at a "
                    "git.exe that runs.")


def tracked_files(base):
    """Every path git is carrying, from the INDEX.

    `git ls-files` reads the index, so a file that has just been `git add`ed
    is included alongside everything already committed. One call therefore
    covers both halves of the question -- what is about to go in, and what is
    already in. Returns (files, error_message); an error is reported loudly
    and never silently treated as "nothing found".
    """
    git, why = resolve_git()
    if git is None:
        return None, why
    try:
        proc = subprocess.Popen([git, "ls-files"], cwd=base,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate()
    except OSError as exc:
        return None, "could not run git: " + str(exc)
    if proc.returncode != 0:
        return None, (err or b"").decode("utf-8", "replace").strip()
    names = out.decode("utf-8", "replace").split("\n")
    return [n.strip() for n in names if n.strip()], None


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

    # ---- C and D. what is ALREADY in the tree, not only what is added -----
    # The listing is fetched UNCONDITIONALLY. It used to be fetched inside
    # `if terms:`, because only C wanted it -- which would have left D
    # unarmed on any machine without the gitignored word list, the exact
    # failure shape C exists to end.
    tracked_hits = []     # (path, term, line)
    tracked_n = 0
    binary_hits = []      # (path, why)
    tnames, tracked_err = tracked_files(base)
    if tnames is not None:
        tracked_n = len(tnames)
        for rel in tnames:
            low = rel.lower()
            full = os.path.join(base, rel.replace("/", os.sep))
            if low.endswith(BINARY_EXT):
                if rel not in BINARY_ALLOW:
                    binary_hits.append((rel, "binary or image extension"))
                continue
            if not os.path.exists(full) or os.path.isdir(full):
                continue
            kind = binary_kind(full)
            if kind and rel not in BINARY_ALLOW:
                binary_hits.append((rel, "starts like a " + kind
                                    + " despite its extension"))
    if terms and tnames is not None:
        for rel in tnames:
            if rel in CLINICAL_ALLOW:
                continue
            if rel.lower().endswith(SKIP_EXT):
                continue
            full = os.path.join(base, rel.replace("/", os.sep))
            if not os.path.exists(full) or os.path.isdir(full):
                continue
            text = read_text(full)
            if text is None:
                continue
            for term, line_no in scan_clinical(text, terms).items():
                tracked_hits.append((rel, term, line_no))

    blocked = bool(path_hits or secret_hits or tracked_hits or tracked_err
                   or binary_hits)

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

    # ---- C. tracked clinical detail: BLOCKS --------------------------------
    print("")
    if terms is None:
        pass                      # already said loudly above that C is unarmed
    elif tracked_err:
        print("!! REFUSING - the tracked-file clinical check DID NOT RUN.")
        print("   " + str(tracked_err))
        print("   It reads `git ls-files`, and it is the only check that asks")
        print("   what is ALREADY in the public tree. A check that did not run")
        print("   is not a check that passed.")
    elif tracked_hits:
        byfile = {}
        for rel, term, line_no in tracked_hits:
            byfile.setdefault(rel, []).append((term, line_no))
        print("!! REFUSING - clinical detail is TRACKED in this public repo.")
        print("   " + str(len(byfile)) + " file(s) git is carrying right now name a"
              " drug or molecule")
        print("   from the list, outside CLINICAL_ALLOW. These are readable by")
        print("   anyone, and remain readable at every old sha even after they")
        print("   are deleted from HEAD.")
        print("")
        for rel in sorted(byfile):
            shown = sorted(byfile[rel])[:6]
            more = len(byfile[rel]) - len(shown)
            desc = ", ".join(t + ":" + str(n) for t, n in shown)
            if more > 0:
                desc = desc + ", +" + str(more) + " more"
            print("      " + rel)
            print("         " + desc)
        print("")
        print("   For each one, decide which it is:")
        print("     - HIS RECORD          take the detail out of the file, and")
        print("                           rewrite history so the old sha does")
        print("                           not still serve it. Deleting from")
        print("                           HEAD alone is not removal.")
        print("     - GENERIC PHARMACOLOGY add the path to CLINICAL_ALLOW in")
        print("                           this file, with its reason.")
    else:
        print("  tracked-file clinical check: " + str(tracked_n)
              + " files carried by git, none naming a drug outside the allowlist.")

    # ---- D. tracked binaries: BLOCKS ---------------------------------------
    print("")
    if tracked_err:
        print("!! REFUSING - the tracked-binary check DID NOT RUN either.")
        print("   It reads the same `git ls-files` as C. A PNG is the one")
        print("   thing this checker can never read, so a check that did not")
        print("   run is not a check that passed.")
    elif binary_hits:
        print("!! REFUSING - binaries are TRACKED in this public repo.")
        print("   " + str(len(binary_hits)) + " file(s) git is carrying are not"
              " readable by any check")
        print("   here, and are not on the allowlist. On 2026-09-15 nine such")
        print("   files were pictures of the health record.")
        print("")
        for rel, why in binary_hits:
            print("      " + rel)
            print("         " + why)
        print("")
        print("   For each one, decide which it is:")
        print("     - GENERATED BY A SUITE  make the suite write it OUTSIDE the")
        print("                             tree. .gitignore is a backstop, not")
        print("                             a remedy: add -f defeats it.")
        print("     - NOT NEEDED            git rm --cached, and purge it from")
        print("                             history if it ever held real data.")
        print("     - GENUINELY REQUIRED    add the path to BINARY_ALLOW with")
        print("                             its reason. The default is NO.")
    else:
        print("  tracked-binary check: " + str(len(BINARY_ALLOW))
              + " allowed, none tracked outside the allowlist.")

    # ---- secrets: block ---------------------------------------------------
    if not (path_hits or secret_hits):
        print("  secrets check: clean.")
        print("")
        return 1 if blocked else 0

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

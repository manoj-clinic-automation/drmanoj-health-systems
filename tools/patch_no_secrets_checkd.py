#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/NO_SECRETS.py  ::  check D -- no tracked binaries outside an allowlist

WHY (2026-09-15)
----------------
Fifteen PNGs were tracked in this PUBLIC repository. Nine of them were
pictures of the health record: the full regimen with Indian brand names and
strengths, stock counts, dated dose times, and two separate windows of blood
pressure readings. They sat there through every clean NO_SECRETS run, because
A, B and C all read TEXT and a PNG is bytes. The checker could not have seen
them however carefully it looked.

Judgement cannot be the defence either. Of the fifteen, one that looked like a
scan of a real lab report was synthetic (its values match
gutlog/scan_lab/make_images.py), and one that looked like a harmless UI shot
carried a real date in a filename field. Classifying pictures by eye is a
thing a person has to get right every time, forever.

So D is structural: **a tracked file with a binary or image extension is
refused unless it is on an explicit allowlist, and each entry carries its
reason.** The allowlist is two PWA icons. A screenshot that is synthetic today
becomes a live one the next time its suite meets a real database, so no
screenshot is on it.

D also catches the obvious bypass -- binary content under a text extension --
by reading the first bytes of every tracked file that is NOT binary by
extension. An extension rule alone would be defeated by renaming.

Two structural points:

  * D fetches the tracked listing UNCONDITIONALLY. Until now only C needed it
    and C fetched it inside `if terms:`, so on any machine without the
    gitignored clinical word list D would have been silently unarmed -- the
    exact failure shape C was written to end.
  * D blocks when it cannot run, for the same reason C does. It shares C's
    git resolution, so it inherits that refusal.

Requires the git-resolution fix (resolve_git). Anchor-verified, idempotent,
compile-checked, .bak before write, self-restoring, --reverse for the
negative control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NO_SECRETS.py")
MARKER = "BINARY_ALLOW"
NEEDS = "def resolve_git"

DOC_OLD = '''Never prints a secret value. Matched credentials are reported by file,
line and kind only.
'''
DOC_NEW = '''  D. TRACKED BINARIES          BLOCKS the publish (exit 1)
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
'''

ALLOW = '''# ---- D. the only binaries allowed in the tree, and why ---------------------
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
    (b"\\x89PNG\\r\\n", "PNG"), (b"\\xff\\xd8\\xff", "JPEG"),
    (b"GIF87a", "GIF"), (b"GIF89a", "GIF"), (b"BM", "BMP"),
    (b"%PDF", "PDF"), (b"PK\\x03\\x04", "ZIP or Office file"),
    (b"SQLite format 3", "SQLite database"), (b"\\x1f\\x8b", "gzip"),
    (b"RIFF", "RIFF - WAV, AVI or WebP"), (b"OggS", "Ogg"),
    (b"\\x00\\x00\\x01\\x00", "Windows icon"), (b"wOFF", "WOFF font"),
    (b"wOF2", "WOFF2 font"), (b"\\x00\\x01\\x00\\x00\\x00", "TrueType font"),
    (b"MZ", "Windows executable"), (b"\\x7fELF", "ELF binary"),
    (b"\\x93NUMPY", "NumPy array"),
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


'''

ALLOW_ANCHOR = "# ---- A. secrets ----------------------------------------------------------\n"

SCAN_OLD = '''    # ---- C. what is ALREADY in the tree, not only what is being added -----
    tracked_hits = []     # (path, term, line)
    tracked_err = None
    tracked_n = 0
    if terms:
        tnames, tracked_err = tracked_files(base)
        if tnames is not None:
            tracked_n = len(tnames)
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

    blocked = bool(path_hits or secret_hits or tracked_hits or tracked_err)
'''

SCAN_NEW = '''    # ---- C and D. what is ALREADY in the tree, not only what is added -----
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
'''

REPORT_OLD = '''    else:
        print("  tracked-file clinical check: " + str(tracked_n)
              + " files carried by git, none naming a drug outside the allowlist.")
'''

REPORT_NEW = '''    else:
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
'''


def build_edits():
    return [
        ("docstring", DOC_OLD, DOC_NEW),
        ("allowlist", ALLOW_ANCHOR, ALLOW + ALLOW_ANCHOR),
        ("scan", SCAN_OLD, SCAN_NEW),
        ("report", REPORT_OLD, REPORT_NEW),
    ]


def read(path):
    fh = open(path, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(path, text):
    fh = open(path, "w", encoding="utf-8", newline="")
    try:
        fh.write(text)
    finally:
        fh.close()


def reverse(path, out_path):
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " does not carry " + MARKER)
        return 1
    out, bad = src, []
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            bad.append("  " + label + ": new text found " + str(c) + " times, need 1")
            break
        out = out.replace(new, anchor, 1)
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    if MARKER in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed the pre-D build -> " + out_path
          + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 70)
    print("NO_SECRETS.py: check D -- no tracked binaries outside an allowlist")
    print("file : " + args.file)
    print("=" * 70)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if NEEDS not in src:
        print("FATAL: apply patch_no_secrets_gitresolve.py first -- D shares")
        print("       check C's git resolution and is useless without it.")
        return 1

    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits))
          + " matched")
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
    write(tmpf, out)
    try:
        py_compile.compile(tmpf, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        shutil.rmtree(tmpd, ignore_errors=True)
        return 2
    shutil.rmtree(tmpd, ignore_errors=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = args.file + ".bak-checkd-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits")
    print("-" * 70)
    print("Next:  python3 test_no_secrets_binaries.py NO_SECRETS.py")
    print("       python3 NEGATIVE_CONTROL.py --manifest new_assertions_checkd.json")
    print("Back:  copy " + bak + " over " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

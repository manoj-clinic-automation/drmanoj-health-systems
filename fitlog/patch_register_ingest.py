#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog - anchor-verified patcher: register the wearable ingest blueprint.

Follows the standard pattern: discover anchor, refuse on ambiguity,
compile-check before write, .bak rollback copy, idempotent.

Python 3.9 compatible.

Usage:
    python3 patch_register_ingest.py [/root/fitlog/app.py]
    python3 patch_register_ingest.py --dry-run
"""

import os
import re
import shutil
import sys
from datetime import datetime

DEFAULT_CANDIDATES = [
    "/root/fitlog/app.py",
    "/root/fitlog/fitlog.py",
    "/root/fitlog/wsgi.py",
    "/root/fitlog/main.py",
]

IMPORT_LINE = "from health_ingest import health_ingest_bp"
RE_REGISTER = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\.register_blueprint\(")
RE_FLASK_APP = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Flask\(")


def find_target(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    if args:
        return args[0]
    for path in DEFAULT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def main():
    dry_run = "--dry-run" in sys.argv
    target = find_target(sys.argv)

    if not target:
        print("FAIL: could not locate the FitLog app file.")
        print("Looked for: " + ", ".join(DEFAULT_CANDIDATES))
        print("Pass the path explicitly, e.g.:")
        print("  python3 patch_register_ingest.py /root/fitlog/app.py")
        return 2

    if not os.path.exists(target):
        print("FAIL: file not found: " + target)
        return 2

    print("Target  : " + target)

    with open(target, "r") as fh:
        original = fh.read()
    lines = original.split("\n")

    # -- idempotency check --------------------------------------------------
    if "health_ingest_bp" in original:
        print("SKIP: health_ingest_bp already registered. Nothing to do.")
        return 0

    # -- anchor discovery ---------------------------------------------------
    anchor_idx = None
    app_var = None
    indent = ""
    anchor_kind = ""

    for idx, line in enumerate(lines):
        m = RE_REGISTER.match(line)
        if m:
            anchor_idx = idx
            indent = m.group(1)
            app_var = m.group(2)
            anchor_kind = "existing register_blueprint"

    if anchor_idx is None:
        matches = []
        for idx, line in enumerate(lines):
            m = RE_FLASK_APP.match(line)
            if m:
                matches.append((idx, m.group(1), m.group(2)))
        if len(matches) == 1:
            anchor_idx, indent, app_var = matches[0]
            anchor_kind = "Flask() construction"
        elif len(matches) > 1:
            print("FAIL: ambiguous anchor - multiple Flask() constructions found:")
            for idx, _, var in matches:
                print("  line " + str(idx + 1) + ": " + var)
            print("Refusing to guess. Re-run naming the correct file.")
            return 1

    if anchor_idx is None:
        print("FAIL: no usable anchor found in " + target)
        print("Expected either a *.register_blueprint( line or a single")
        print("'app = Flask(' line. Send me the file and I'll re-cut this patcher.")
        return 1

    print("Anchor  : line " + str(anchor_idx + 1) + " (" + anchor_kind + ")")
    print("App var : " + app_var)

    # -- build patched source ----------------------------------------------
    insert = [
        "",
        indent + "# --- Phase 3.5: wearable ingest (Apple Watch / Health Connect) ---",
        indent + IMPORT_LINE,
        indent + app_var + ".register_blueprint(health_ingest_bp)",
    ]
    patched_lines = lines[:anchor_idx + 1] + insert + lines[anchor_idx + 1:]
    patched = "\n".join(patched_lines)

    # -- compile check BEFORE any write ------------------------------------
    try:
        compile(patched, target, "exec")
    except SyntaxError as exc:
        print("FAIL: patched source does not compile: " + str(exc))
        print("No changes written.")
        return 1
    print("Compile : ok")

    if dry_run:
        print("\n--- dry run, would insert after line "
              + str(anchor_idx + 1) + " ---")
        for line in insert:
            print("  " + line)
        return 0

    # -- backup then write --------------------------------------------------
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = target + ".bak_" + stamp
    shutil.copy2(target, bak)
    print("Backup  : " + bak)

    with open(target, "w") as fh:
        fh.write(patched)

    with open(target, "r") as fh:
        verify = fh.read()
    if "health_ingest_bp" not in verify:
        shutil.copy2(bak, target)
        print("FAIL: write verification failed. Rolled back from " + bak)
        return 1

    print("Patched : ok")
    print("")
    print("Next:")
    print("  python3 test_health_ingest.py     # must be 23/23")
    print("  systemctl restart fitlog")
    print("  systemctl status fitlog --no-pager")
    print("")
    print("Rollback if needed:")
    print("  cp " + bak + " " + target + " && systemctl restart fitlog")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog Phase 3.5 - one-command deploy orchestrator.

Runs ON the server. Discovers the real database and app entrypoint,
migrates, patches, smoke tests, restarts, verifies, and rolls back
automatically on any failure.

Built for phone-only operation: no interactive prompts, no editing,
one command, one readable report.

Python 3.9 compatible.

Usage:
    cd /root/fitlog && python3 deploy_phase35.py

    # only if auto-discovery reports ambiguity:
    python3 deploy_phase35.py --db /root/fitlog/x.db --app /root/fitlog/y.py

    # inspect without changing anything:
    python3 deploy_phase35.py --dry-run
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:
    Request = None

APP_DIR = "/root/fitlog"
SERVICE = "fitlog"
LOCAL_BASE = "http://127.0.0.1:8040"
ENV_FILE = os.path.join(APP_DIR, "ingest.env")

# The database name health_ingest.py falls back to when nothing pins it.
DEFAULT_DB = os.path.join(APP_DIR, "fitlog.db")

REQUIRED_FILES = (
    "health_ingest.py",
    "migrate_health_ingest.py",
    "patch_register_ingest.py",
    "test_health_ingest.py",
)

STEPS = []
ROLLBACK = {"db": None, "app": None, "db_bak": None, "app_bak": None}


def say(msg):
    print(msg)
    sys.stdout.flush()


def step(name, ok, detail=""):
    STEPS.append((name, ok, detail))
    mark = " ok " if ok else "FAIL"
    say("  [" + mark + "] " + name + ((" - " + detail) if detail else ""))
    return ok


def run(cmd, cwd=None):
    """Run a command, return (returncode, combined output)."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    out, _ = proc.communicate()
    try:
        text = out.decode("utf-8", "replace")
    except Exception:
        text = str(out)
    return proc.returncode, text


def arg(flag):
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover_db():
    explicit = arg("--db")
    if explicit:
        return explicit, "explicit"
    candidates = []
    for path in sorted(glob.glob(os.path.join(APP_DIR, "*.db"))):
        base = os.path.basename(path)
        if ".bak" in base or base.endswith("-wal") or base.endswith("-shm"):
            continue
        candidates.append(path)
    if len(candidates) == 1:
        return candidates[0], "auto"
    return None, candidates


def discover_app():
    explicit = arg("--app")
    if explicit:
        return explicit, "explicit"
    candidates = []
    for path in sorted(glob.glob(os.path.join(APP_DIR, "*.py"))):
        base = os.path.basename(path)
        if base in REQUIRED_FILES or base.startswith("patch_"):
            continue
        if base == os.path.basename(__file__):
            continue
        try:
            with open(path, "r") as fh:
                src = fh.read()
        except IOError:
            continue
        if re.search(r"=\s*Flask\(", src):
            candidates.append(path)
    if len(candidates) == 1:
        return candidates[0], "auto"
    return None, candidates


def newest_bak(path):
    baks = sorted(glob.glob(path + ".bak_*"))
    return baks[-1] if baks else None


def pin_db(env_file, db_path):
    """
    Record FITLOG_DB in ingest.env so the blueprint opens the same database
    this run migrated.

    health_ingest.py reads that file directly -- systemd points
    EnvironmentFile at .env, which does not exist, so writing the value
    anywhere else would never reach the process. Without this, discovering
    a database under a non-default name migrates one file while the app
    keeps writing to fitlog.db.

    Rewrites any existing FITLOG_DB line rather than appending a second one.
    """
    lines = []
    if os.path.exists(env_file):
        with open(env_file, "r") as fh:
            lines = [ln.rstrip("\n") for ln in fh]
    kept = [ln for ln in lines if not ln.strip().startswith("FITLOG_DB=")]
    kept.append("FITLOG_DB=" + db_path)
    with open(env_file, "w") as fh:
        fh.write("\n".join(kept) + "\n")
    os.chmod(env_file, 0o600)


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def http(path, token=None, body=None):
    url = LOCAL_BASE + path
    headers = {"Host": "fit.dr-manoj.in"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers)
    try:
        resp = urlopen(req, timeout=15)
        return resp.getcode(), resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except URLError as exc:
        return 0, str(exc)
    except Exception as exc:
        return 0, str(exc)


def rollback(reason):
    say("")
    say("!! ROLLING BACK: " + reason)
    restored = []
    if ROLLBACK["app"] and ROLLBACK["app_bak"]:
        shutil.copy2(ROLLBACK["app_bak"], ROLLBACK["app"])
        restored.append(os.path.basename(ROLLBACK["app"]))
    if ROLLBACK["db"] and ROLLBACK["db_bak"]:
        shutil.copy2(ROLLBACK["db_bak"], ROLLBACK["db"])
        restored.append(os.path.basename(ROLLBACK["db"]))
    run(["systemctl", "restart", SERVICE])
    time.sleep(3)
    code, out = run(["systemctl", "is-active", SERVICE])
    say("   restored: " + (", ".join(restored) if restored else "nothing to restore"))
    say("   service : " + out.strip())
    say("")
    say("Nothing was left half-applied. Send me this output.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    dry = "--dry-run" in sys.argv

    say("=" * 56)
    say("FitLog Phase 3.5 - wearable ingest deploy")
    say("=" * 56)

    # -- 0. pre-flight ------------------------------------------------------
    say("")
    say("[0] pre-flight")

    say("  python  : " + sys.version.split()[0])
    if sys.version_info[:2] != (3, 9):
        say("  note    : expected 3.9 on this server, continuing anyway")

    missing = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(APP_DIR, f))]
    if not step("all four source files present", not missing, ", ".join(missing)):
        say("")
        say("Upload the missing files to " + APP_DIR + " and re-run.")
        return 2

    db_path, db_how = discover_db()
    if db_path is None:
        step("database discovered", False, "ambiguous")
        say("")
        say("Found these candidates:")
        for c in db_how:
            say("  " + c)
        say("Re-run naming the right one:")
        say("  python3 deploy_phase35.py --db <path>")
        return 2
    step("database discovered (" + db_how + ")", True, db_path)

    app_path, app_how = discover_app()
    if app_path is None:
        step("app entrypoint discovered", False, "ambiguous")
        say("")
        say("Found these candidates:")
        for c in app_how:
            say("  " + c)
        say("Re-run naming the right one:")
        say("  python3 deploy_phase35.py --app <path>")
        return 2
    step("app entrypoint discovered (" + app_how + ")", True, app_path)

    with open(app_path, "r") as fh:
        app_src = fh.read()
    gate_in_flask = "before_request" in app_src
    step("owner-key gate location",
         True,
         "before_request hook in app" if gate_in_flask else "not in app (proxy layer)")

    code, out = run(["systemctl", "is-active", SERVICE])
    step("service currently active", out.strip() == "active", out.strip())

    if dry:
        say("")
        say("Dry run complete. Nothing changed.")
        say("DB : " + db_path)
        say("App: " + app_path)
        return 0

    # -- 1. token -----------------------------------------------------------
    say("")
    say("[1] ingest token")

    if os.path.exists(ENV_FILE):
        step("token already exists", True, "reusing " + ENV_FILE)
    else:
        import secrets
        with open(ENV_FILE, "w") as fh:
            fh.write("# FitLog ingest token - keep out of git\n")
            fh.write("FITLOG_INGEST_TOKEN=" + secrets.token_urlsafe(32) + "\n")
        os.chmod(ENV_FILE, 0o600)
        step("token generated", True, ENV_FILE + " (mode 600)")

    token = None
    with open(ENV_FILE, "r") as fh:
        for line in fh:
            if line.strip().startswith("FITLOG_INGEST_TOKEN="):
                token = line.split("=", 1)[1].strip()
    if not step("token readable", bool(token)):
        return 1

    # -- 1b. pin the database for the blueprint -----------------------------
    # Only when the discovered path is not the name health_ingest.py falls
    # back to. Migrating one database while the app writes to another is the
    # GutLog health.db/health3.db failure, and it surfaces as HTTP 500 on the
    # first real POST rather than as anything obvious here.

    if os.path.abspath(db_path) == os.path.abspath(DEFAULT_DB):
        step("db is the default name, no pin needed", True, db_path)
    else:
        pin_db(ENV_FILE, db_path)
        step("FITLOG_DB pinned in ingest.env", True, db_path)

        ingest_src = ""
        try:
            with open(os.path.join(APP_DIR, "health_ingest.py"), "r") as fh:
                ingest_src = fh.read()
        except IOError:
            pass
        if "_env_file_value" not in ingest_src:
            step("blueprint reads FITLOG_DB from ingest.env", False,
                 "run patch_db_pin.py first")
            say("")
            say("This build of health_ingest.py ignores ingest.env and will")
            say("open " + DEFAULT_DB + " regardless. Apply patch_db_pin.py,")
            say("then re-run. Nothing has been migrated yet.")
            return 2
        step("blueprint reads FITLOG_DB from ingest.env", True)

    # -- 2. migrate ---------------------------------------------------------
    say("")
    say("[2] schema migration")

    code, out = run([sys.executable, "migrate_health_ingest.py", db_path], cwd=APP_DIR)
    ok = (code == 0 and "OK: all ingest tables present." in out)
    if not step("migration", ok, "rc=" + str(code)):
        say(out)
        return 1
    ROLLBACK["db"] = db_path
    ROLLBACK["db_bak"] = newest_bak(db_path)
    step("db backup recorded", bool(ROLLBACK["db_bak"]),
         ROLLBACK["db_bak"] or "none")

    # -- 3. register blueprint ---------------------------------------------
    say("")
    say("[3] register blueprint")

    code, out = run(
        [sys.executable, "patch_register_ingest.py", "--dry-run", app_path],
        cwd=APP_DIR,
    )
    if not step("anchor found", code == 0, "rc=" + str(code)):
        say(out)
        rollback("patcher could not find a safe anchor")
        return 1

    code, out = run([sys.executable, "patch_register_ingest.py", app_path], cwd=APP_DIR)
    ok = (code == 0 and ("Patched : ok" in out or "SKIP:" in out))
    if not step("blueprint registered", ok, "rc=" + str(code)):
        say(out)
        rollback("patch failed")
        return 1
    ROLLBACK["app"] = app_path
    ROLLBACK["app_bak"] = newest_bak(app_path)

    # -- 4. smoke test - the gate ------------------------------------------
    say("")
    say("[4] smoke test")

    code, out = run([sys.executable, "test_health_ingest.py"], cwd=APP_DIR)
    passed = "RESULT: 23/23 passed" in out
    if not step("23/23 smoke suite", passed, "rc=" + str(code)):
        say("")
        say(out[-2500:])
        rollback("smoke suite did not pass clean")
        return 1

    # -- 5. restart ---------------------------------------------------------
    say("")
    say("[5] restart and verify")

    run(["systemctl", "restart", SERVICE])
    time.sleep(4)
    code, out = run(["systemctl", "is-active", SERVICE])
    if not step("service active after restart", out.strip() == "active", out.strip()):
        rollback("service failed to come back up")
        return 1

    if Request is None:
        step("http verification", False, "urllib unavailable, skipping")
    else:
        code, _ = http("/api/ingest/status")
        if not step("unauthenticated request rejected", code == 401, "got " + str(code)):
            rollback("ingest endpoint is not properly gated")
            return 1

        code, body = http("/api/ingest/status", token=token)
        if not step("authenticated status ok", code == 200, "got " + str(code)):
            rollback("status endpoint not responding correctly")
            return 1

        probe = {"data": {"metrics": [{
            "name": "step_count", "units": "count",
            "data": [{"date": "2026-01-01 00:00:00 +0530", "qty": 1234}],
        }]}}
        code, body = http("/api/ingest?source=manual", token=token, body=probe)
        wrote = False
        if code == 200:
            try:
                wrote = json.loads(body).get("metrics_stored") == 1
            except ValueError:
                wrote = False
        if not step("end-to-end write", wrote, "http " + str(code)):
            rollback("ingest write path failed")
            return 1

        code, body = http("/api/health/daily?date=2026-01-01", token=token)
        resolved = False
        if code == 200:
            try:
                metrics = json.loads(body).get("metrics", {})
                resolved = metrics.get("steps", {}).get("value") == 1234
            except ValueError:
                resolved = False
        step("S01 resolution returns the probe", resolved, "http " + str(code))

        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            "DELETE FROM health_metrics WHERE date='2026-01-01' AND source='manual'"
        )
        conn.execute("DELETE FROM health_raw WHERE source='manual'")
        conn.commit()
        conn.close()
        step("probe data removed", True)

    # -- 6. report ----------------------------------------------------------
    failed = [s for s in STEPS if not s[1]]

    say("")
    say("=" * 56)
    if failed:
        say("COMPLETED WITH WARNINGS - " + str(len(failed)) + " non-fatal")
        for name, _, detail in failed:
            say("  - " + name + " " + detail)
    else:
        say("DEPLOY CLEAN - every check passed")
    say("=" * 56)
    say("")
    say("Database : " + db_path)
    say("App file : " + app_path)
    say("DB backup: " + str(ROLLBACK["db_bak"]))
    say("App backup: " + str(ROLLBACK["app_bak"]))
    say("")
    say("INGEST TOKEN (needed on both phones):")
    say("")
    say("  " + str(token))
    say("")

    if gate_in_flask:
        say("ACTION STILL NEEDED:")
        say("  This app enforces the owner-key gate via a before_request hook.")
        say("  Endpoints starting with 'health_ingest.' must be exempted, or")
        say("  the phones will get the owner-key wall instead of bearer auth.")
        say("  The checks above went through localhost and may not reflect this.")
        say("  Send me the before_request block and I will cut that patcher.")
        say("")

    say("Verdict logic untouched. No F-rule consumes ingested data yet.")
    say("")
    say("Rollback if anything looks wrong later:")
    say("  cp " + str(ROLLBACK["app_bak"]) + " " + app_path)
    say("  cp " + str(ROLLBACK["db_bak"]) + " " + db_path)
    say("  systemctl restart " + SERVICE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\ninterrupted")
        sys.exit(130)

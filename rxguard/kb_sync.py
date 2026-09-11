#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_sync.py -- RxGuard v1.4.0 background sync. Run by cron every 30 minutes
and by the "Fetch now" button. Safe to run at any time; one run at a time.

  1. Reads what GutLog shows is current (regimen + 30 days of doses).
  2. Any molecule RxGuard does not know -> a DRAFT from free sources
     (RxNorm, FDA label, FDA CYP table, DDInter), waiting for approval.
  3. Pairs of known molecules that DDInter rates Major/Moderate and no
     curated/approved rule covers -> interaction CANDIDATES for review.
  4. PvPI (India) alert index -> new alert links for review.
  5. Once a day: approved entries whose FDA label changed -> "re-review".
  6. GutLog's health record -> your conditions (codes only).
Writes nothing to the knowledge files -- only the owner's Approve does.
Python 3.9.
"""
import fcntl
import json
import os
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app as rx          # noqa: E402  (module import only; no server starts)
import kb_sources as ks   # noqa: E402

DB = os.environ.get("RXGUARD_DB", os.path.join(HERE, "rxguard.db"))


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg, flush=True)


def gutlog_stack(days=30):
    try:
        with open(rx.GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
        req = urllib.request.Request(rx.GUTLOG_FEED_URL.rstrip("/") + "/api/feed/stack?days=%d" % days,
                                     headers={"Authorization": "Bearer " + tok})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
        return (d, "") if d.get("ok") else (None, "bad answer")
    except Exception as e:
        return None, type(e).__name__


def _feed_live():
    """Follow the live database: a scratch copy (tests) never reads GutLog.
    RXGUARD_GUTLOG_FEED=1/0 overrides."""
    v = os.environ.get("RXGUARD_GUTLOG_FEED", "")
    if v in ("0", "1"):
        return v == "1"
    return os.path.dirname(os.path.abspath(DB)) == HERE


def gutlog_profile():
    """Condition codes from GutLog's health record, or None if unavailable."""
    if not _feed_live():
        return None
    try:
        with open(rx.GUTLOG_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
        req = urllib.request.Request(rx.GUTLOG_FEED_URL.rstrip("/") + "/api/feed/profile",
                                     headers={"Authorization": "Bearer " + tok})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
        return [c for c in d.get("conditions") or [] if isinstance(c, str)] if d.get("ok") else None
    except Exception:
        return None


def sync_conditions(con, codes):
    """Tick the conditions GutLog lists; untick only those GutLog itself set
    earlier and no longer lists. Hand-set conditions are never touched."""
    known = [c for c in codes if c in rx.CONDITION_LABELS]
    prev = set(get_meta(con, "profile_codes", []) or [])
    for c in known:
        con.execute("INSERT OR IGNORE INTO conditions (code, active) VALUES (?, 0)", (c,))
        con.execute("UPDATE conditions SET active=1 WHERE code=?", (c,))
    dropped = sorted(prev - set(known))
    for c in dropped:
        con.execute("UPDATE conditions SET active=0 WHERE code=?", (c,))
    set_meta(con, "profile_codes", known)
    con.commit()
    return {"set": known, "cleared": dropped}


def current_molecules(data):
    """[(term, strength, gutlog_name)] from regimen and recent doses."""
    out, seen = [], set()
    for src in (data.get("regimen") or []), (data.get("taken") or []):
        for r in src:
            for term in (r.get("molecule") or "").replace("+", ",").split(","):
                term = term.strip()
                if term and term.lower() not in seen:
                    seen.add(term.lower())
                    out.append((term, r.get("strength") or "", r.get("name") or ""))
    return out


def set_meta(con, key, value):
    con.execute("INSERT INTO kb_meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def get_meta(con, key, default=None):
    r = con.execute("SELECT value FROM kb_meta WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def _stage(con, status, name):
    """Record progress as the run goes, so a slow or stopped run still shows
    how far it got (Sources review page and --report)."""
    status["stage"] = name
    set_meta(con, "sources", status)
    con.commit()
    log("stage: " + name)


def run(con, data=None, now=None, ddi_budget=900, profile=None):
    now = now or time.strftime("%Y-%m-%d %H:%M")
    rx.reload_overlay(force=True)
    status = {"run": now}
    codes = profile if profile is not None else gutlog_profile()
    if codes is not None:
        status["conditions"] = sync_conditions(con, codes)
    if data is None:
        data, err = gutlog_stack(30)
        status["gutlog"] = err or "ok"
        if data is None:
            _stage(con, status, "stopped: GutLog feed unavailable")
            log("GutLog feed unavailable: " + err)
            return status
    else:
        status["gutlog"] = "ok"
    _stage(con, status, "FDA CYP table")
    cyp_table, cyp_meta = ks.fda_cyp_table()
    status["fda_cyp"] = cyp_meta
    _stage(con, status, "DDInter download")
    ddi_path, ddi_meta = ks.ddinter_db(budget=ddi_budget)
    ddi_full = bool(ddi_meta.get("ok"))
    status["ddinter"] = {k: v for k, v in ddi_meta.items() if k not in ("files",)}
    _stage(con, status, "drafts (RxNorm + FDA labels)")
    mols = current_molecules(data)
    current = [(rx.norm_key(t), t) for t, _s, _n in mols]
    known = sorted(set(k for k, _t in current if k in rx.DRUGS))
    cache, made, rebuilt = {}, 0, 0
    for term, strength, gname in mols:
        k = rx.norm_key(term)
        if k in rx.DRUGS:
            continue
        row = con.execute("SELECT id, status, draft_json FROM kb_drafts WHERE term=? OR key=?",
                          (term.lower(), k)).fetchone()
        if row:
            old = json.loads(row[2] or "{}")
            if row[1] == "pending" and not old.get("alias_of") and (old.get("strength") or "") != (strength or ""):
                old["strength"] = strength or ""          # salt page edited since the draft was made
                con.execute("UPDATE kb_drafts SET strength=?, draft_json=? WHERE id=?",
                            (strength or "", json.dumps(old), row[0]))
            # a pending draft is rebuilt once DDInter is complete, or when the builder improved
            stale = old.get("builder", 1) < ks.BUILDER and not old.get("alias_of")
            if not (row[1] == "pending" and ((ddi_full and old.get("ddi_complete") is False) or stale)):
                continue
        d = ks.build_draft(term, strength, current, cyp_table, ddi_path, cache)
        d["gutlog_name"] = gname
        d["ddi_complete"] = ddi_full
        if d["key"] in rx.DRUGS or d["key"] in rx.SYNONYMS:
            d = {"alias_of": rx.norm_key(d["key"]), "term": term, "key": ks.norm(term),
                 "display": term, "identity": d["identity"], "strength": strength,
                 "gutlog_name": gname}
        if row:
            con.execute("UPDATE kb_drafts SET strength=?, draft_json=? WHERE id=?",
                        (strength or "", json.dumps(d), row[0]))
            rebuilt += 1
        else:
            con.execute("INSERT OR IGNORE INTO kb_drafts(key, term, strength, status, draft_json, created) "
                        "VALUES(?,?,?,?,?,?)", (d["key"], term.lower(), strength, "pending",
                                                json.dumps(d), now))
            made += 1
        con.commit()
    status["drafts_made"] = made
    status["drafts_rebuilt"] = rebuilt
    _stage(con, status, "interaction candidates")
    covered = set(frozenset((r["a"], r["b"])) for r in rx.PAIRWISE)
    np_ = 0
    for p in ks.pair_candidates(known, ddi_path, covered):
        cur = con.execute("INSERT OR IGNORE INTO kb_pairs(a, b, kind, flag, quote, source, status, created) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          (p["a"], p["b"], p["kind"], p["flag"], p["quote"], p["source"], "pending", now))
        np_ += cur.rowcount
    status["pairs_made"] = np_
    _stage(con, status, "PvPI alerts")
    alerts, perr = ks.pvpi_alerts()
    first = not con.execute("SELECT 1 FROM kb_alerts").fetchone()
    for title, url in alerts:
        con.execute("INSERT OR IGNORE INTO kb_alerts(title, url, status, created) VALUES(?,?,?,?)",
                    (title, url, "baseline" if first else "new", now))
    status["pvpi"] = perr or ("%d links" % len(alerts))
    today = now[:10]
    if get_meta(con, "last_reverify") != today:
        _stage(con, status, "label re-check")
        changed = 0
        for rid, dj in con.execute("SELECT id, draft_json FROM kb_drafts WHERE status='approved'").fetchall():
            d = json.loads(dj)
            if not d.get("label_set_id"):
                continue
            lab = ks.openfda_label(d.get("display", ""))
            if lab.get("ok") and lab["effective_time"] and lab["effective_time"] != d.get("label_effective"):
                con.execute("UPDATE kb_drafts SET status='source_changed' WHERE id=?", (rid,))
                changed += 1
        set_meta(con, "last_reverify", today)
        status["reverify_changed"] = changed
    status["finished"] = time.strftime("%Y-%m-%d %H:%M")
    _stage(con, status, "done")
    log("sync: %s" % json.dumps({k: status.get(k) for k in ("gutlog", "drafts_made", "drafts_rebuilt",
                                                            "pairs_made", "pvpi")}))
    return status


def report(con):
    """Plain summary of what the free sources produced, for the terminal."""
    st = get_meta(con, "sources", {}) or {}
    fc, dd = st.get("fda_cyp") or {}, st.get("ddinter") or {}
    print("LAST RUN %s  stage: %s" % (st.get("run", "never"), st.get("stage", "-")))
    print("SOURCES  gutlog=%s  FDA table rows=%s  DDInter pairs=%s (%d/14 files)  PvPI=%s" % (
        st.get("gutlog"), fc.get("rows", fc.get("err", "?")), dd.get("pairs", "?"),
        14 - len(dd.get("failed") or []) if dd else 0, st.get("pvpi")))
    for k in ("fda_cyp", "ddinter"):
        if (st.get(k) or {}).get("err"):
            print("         note: " + st[k]["err"])
    rows = con.execute("SELECT term, strength, status, draft_json FROM kb_drafts ORDER BY id").fetchall()
    print("DRAFTS   %d" % len(rows))
    for term, strength, status, dj in rows:
        d = json.loads(dj or "{}")
        if d.get("alias_of"):
            print("  %-22s %-10s -> same drug as %s (alias)" % (term, strength or "", d["alias_of"]))
            continue
        ident = d.get("identity") or {}
        props = ", ".join("%s=%s" % (p["field"], p["value"]) for p in d.get("props") or [])
        print("  %-22s %-10s %-8s RxNorm:%s ATC:%s" % (term, strength or "", status, ident.get("name") or "not found",
                                                   ",".join((a[0] if isinstance(a, (list, tuple)) else str(a)) for a in (ident.get("atc") or [])) or "-"))
        print("      properties: " + (props or "none found"))
        for pr in d.get("pairs") or []:
            print("      pair: %s + %s  %s  [%s]" % (pr["a"], pr["b"], pr["flag"], pr["source"]))
        if d.get("gaps"):
            print("      gaps: " + "; ".join(d["gaps"]))
    pr = con.execute("SELECT a, b, flag, source, status FROM kb_pairs ORDER BY flag DESC, a").fetchall()
    print("PAIRS    %d (known medicines, not yet covered by a curated rule)" % len(pr))
    for a, b, flag, src, status in pr:
        print("  %s + %s  %s  [%s]  %s" % (a, b, flag, src, status))
    print("ALERTS   %d PvPI links (first run = baseline)" % con.execute("SELECT COUNT(*) FROM kb_alerts").fetchone()[0])


def main():
    if "--conditions" in sys.argv:
        codes = gutlog_profile()
        if codes is None:
            print("GutLog health profile not reachable")
            return 1
        con = sqlite3.connect(DB)
        con.executescript(rx.SCHEMA)
        try:
            st = sync_conditions(con, codes)
        finally:
            con.close()
        print("conditions from GutLog: %d set, %d cleared" % (len(st["set"]), len(st["cleared"])))
        return 0
    if "--diag" in sys.argv:
        print("Connectivity from this server (one small request each):")
        for name, code, secs, n in ks.diagnose():
            print("  %-15s %s  %5.1f s  %d bytes" % (name, ("HTTP %d" % code) if code else "NO ANSWER", secs, n))
        return 0
    if "--report" in sys.argv:
        con = sqlite3.connect(DB)
        con.executescript(rx.SCHEMA)
        try:
            report(con)
        finally:
            con.close()
        return 0
    lock = open(os.path.join(HERE, ".kb_sync.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another sync is running; exiting")
        return 0
    con = sqlite3.connect(DB)
    con.executescript(rx.SCHEMA)
    try:
        run(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

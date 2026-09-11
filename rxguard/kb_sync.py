#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_sync.py -- RxGuard v1.2.0 background sync. Run by cron every 30 minutes
and by the "Fetch now" button. Safe to run at any time; one run at a time.

  1. Reads what GutLog shows is current (regimen + 30 days of doses).
  2. Any molecule RxGuard does not know -> a DRAFT from free sources
     (RxNorm, FDA label, FDA CYP table, DDInter), waiting for approval.
  3. Pairs of known molecules that DDInter rates Major/Moderate and no
     curated/approved rule covers -> interaction CANDIDATES for review.
  4. PvPI (India) alert index -> new alert links for review.
  5. Once a day: approved entries whose FDA label changed -> "re-review".
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


def run(con, data=None, now=None):
    now = now or time.strftime("%Y-%m-%d %H:%M")
    rx.reload_overlay(force=True)
    status = {"run": now}
    if data is None:
        data, err = gutlog_stack(30)
        status["gutlog"] = err or "ok"
        if data is None:
            set_meta(con, "sources", status)
            con.commit()
            log("GutLog feed unavailable: " + err)
            return status
    else:
        status["gutlog"] = "ok"
    cyp_table, cyp_meta = ks.fda_cyp_table()
    ddi_path, ddi_meta = ks.ddinter_db()
    status["fda_cyp"] = cyp_meta
    status["ddinter"] = {k: v for k, v in ddi_meta.items() if k != "failed"}
    mols = current_molecules(data)
    current = [(rx.norm_key(t), t) for t, _s, _n in mols]
    known = sorted(set(k for k, _t in current if k in rx.DRUGS))
    cache, made = {}, 0
    for term, strength, gname in mols:
        k = rx.norm_key(term)
        if k in rx.DRUGS:
            continue
        if con.execute("SELECT 1 FROM kb_drafts WHERE term=? OR key=?", (term.lower(), k)).fetchone():
            continue
        d = ks.build_draft(term, strength, current, cyp_table, ddi_path, cache)
        d["gutlog_name"] = gname
        if d["key"] in rx.DRUGS or d["key"] in rx.SYNONYMS:
            d = {"alias_of": rx.norm_key(d["key"]), "term": term, "key": ks.norm(term),
                 "display": term, "identity": d["identity"], "strength": strength,
                 "gutlog_name": gname}
        con.execute("INSERT OR IGNORE INTO kb_drafts(key, term, strength, status, draft_json, created) "
                    "VALUES(?,?,?,?,?,?)", (d["key"], term.lower(), strength, "pending",
                                            json.dumps(d), now))
        made += 1
    status["drafts_made"] = made
    covered = set(frozenset((r["a"], r["b"])) for r in rx.PAIRWISE)
    np_ = 0
    for p in ks.pair_candidates(known, ddi_path, covered):
        cur = con.execute("INSERT OR IGNORE INTO kb_pairs(a, b, kind, flag, quote, source, status, created) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          (p["a"], p["b"], p["kind"], p["flag"], p["quote"], p["source"], "pending", now))
        np_ += cur.rowcount
    status["pairs_made"] = np_
    alerts, perr = ks.pvpi_alerts()
    first = not con.execute("SELECT 1 FROM kb_alerts").fetchone()
    for title, url in alerts:
        con.execute("INSERT OR IGNORE INTO kb_alerts(title, url, status, created) VALUES(?,?,?,?)",
                    (title, url, "baseline" if first else "new", now))
    status["pvpi"] = perr or ("%d links" % len(alerts))
    today = now[:10]
    if get_meta(con, "last_reverify") != today:
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
    set_meta(con, "sources", status)
    con.commit()
    log("sync: %s" % json.dumps({k: status[k] for k in ("gutlog", "drafts_made", "pairs_made", "pvpi")}))
    return status


def report(con):
    """Plain summary of what the free sources produced, for the terminal."""
    st = get_meta(con, "sources", {}) or {}
    print("SOURCES  gutlog=%s  fda_cyp=%s  ddinter=%s  pvpi=%s" % (
        st.get("gutlog"), (st.get("fda_cyp") or {}).get("rows", (st.get("fda_cyp") or {}).get("err", "?")),
        (st.get("ddinter") or {}).get("pairs", (st.get("ddinter") or {}).get("err", "?")), st.get("pvpi")))
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Health Mirror -- a private, read-only snapshot of the owner's own health
record, written where the Claude app on his phone can read it.

WHY THIS EXISTS
    He talks to Claude from his phone, where it cannot reach GutLog, FitLog,
    RxGuard or his PC. So it answered from whatever it had, which was stale
    and partial -- a sleep summary drawn from his two best nights, and a
    medicine list that was out of date. It can read his Drive. So the record
    goes there, current, and the answers come from the record instead of
    from memory.

WHAT IT IS NOT
    Not a share, not a link, not an endpoint. One private folder in his own
    Drive. Nothing here creates a URL, and nothing here is public.

    Personal health record ONLY. Nothing from the clinic, no patient data:
    this tool reads three databases by absolute path and they are all his
    own (CLAUDE.md -- clinic automation is a separate repository).

TWO DEPARTURES FROM CLAUDE.md, BOTH DELIBERATE

  1. Section 5a says cross-app reads go through GutLog's feed, never another
     app's database file. That rule is for the APPS, so a running consumer
     is not coupled to another app's schema and a suite cannot be coloured
     by real data. This is not an app; it is an offline reporting tool run
     by cron, it needs the whole record, and the feed carries only the stack
     and the doses -- it could not produce labs, sleep blocks or vitals.
     Every database here is opened `mode=ro` through a URI, so this file
     structurally cannot write to any of them.

  2. The meal totals are a SUM in SQL here, not GutLog's nut_day(). Importing
     app.py would open a WRITE connection to the live database (db() runs
     executescript(SCHEMA)), and a read-only mirror should not do that for a
     sum of four columns. The single source of truth is kept by the SUITE
     instead: test_health_mirror.py asserts this tool and nut_day() agree
     day for day, so the two cannot drift without a test going red.

NO MEDICINE OR CONDITION NAME APPEARS IN THIS FILE. They are read from the
databases and written to Drive. This file is tracked and the repository is
public (CLAUDE.md 5d).

    python3 health_mirror.py --out /root/health_mirror [--upload] [--days 30]
        --out      staging directory, mode 700
        --upload   push to Drive with rclone afterwards
        --dry-run  generate, print what would be written, write nothing

Python 3.9.
"""
import argparse
import csv
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys

GUTLOG_DB = os.environ.get("MIRROR_GUTLOG_DB", "/root/gutlog/health3.db")
FITLOG_DB = os.environ.get("MIRROR_FITLOG_DB", "/root/fitlog/fitlog.db")
PLANS_DIR = os.environ.get("MIRROR_PLANS_DIR", "/root/gutlog/plans_files")
UPLOAD_DIR = os.environ.get("MIRROR_UPLOAD_DIR", "/root/gutlog/uploads")
RCLONE_REMOTE = os.environ.get("MIRROR_REMOTE", "healthmirror")
RCLONE_PATH = os.environ.get("MIRROR_REMOTE_PATH", "Health Mirror (for Claude)")

KEEP_DATED = 14
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# The night window for "what was taken before this night". Deliberately a
# CLOCK RULE, not a list of medicine names: nothing here decides what counts
# as a sleeping tablet, so nothing here can be wrong about it, and no
# molecule name has to live in tracked code to make it work.
NIGHT_FROM = "18:00"
NIGHT_TO = "04:00"


def dmy(iso):
    try:
        y, m, d = str(iso).split("-")
        return "%02d-%s-%s" % (int(d), MONTHS[int(m) - 1], y)
    except (ValueError, IndexError, TypeError):
        return str(iso or "")


def ro(path):
    """Read-only by construction. See the departure note above."""
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    con.row_factory = sqlite3.Row
    return con


def rows(con, sql, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []


def days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def today():
    return datetime.date.today().isoformat()


# ----------------------------------------------------------------- sections
def medicines_now(g):
    """The open regimen: an active medicine with a live schedule row."""
    return rows(g,
                "SELECT p.name AS name, p.molecule AS molecule, p.form AS form, "
                "s.slot AS slot, s.dose_text AS dose_text, s.with_food AS with_food, "
                "s.valid_from AS since, s.notes AS notes "
                "FROM prnmeds p JOIN med_schedule s ON s.med_id = p.id "
                "WHERE p.active = 1 AND (s.valid_to IS NULL OR s.valid_to = '' OR s.valid_to >= ?) "
                "ORDER BY p.sort, p.name, s.slot", (today(),))


def medicine_events(g, since):
    """Dated starts, stops and dose changes. Where a medicine is no longer
    active but carries no stop date, that is SAID, never guessed."""
    out = []
    for r in rows(g,
                  "SELECT p.name AS name, p.active AS active, s.slot AS slot, "
                  "s.dose_text AS dose_text, s.valid_from AS valid_from, "
                  "s.valid_to AS valid_to, s.notes AS notes "
                  "FROM prnmeds p JOIN med_schedule s ON s.med_id = p.id "
                  "ORDER BY s.valid_from DESC, p.name"):
        vf, vt = (r.get("valid_from") or ""), (r.get("valid_to") or "")
        if vf and vf >= since:
            out.append({"date": vf, "event": "started or changed", "name": r["name"],
                        "slot": r.get("slot") or "", "dose": r.get("dose_text") or "",
                        "note": r.get("notes") or ""})
        if vt and vt >= since:
            out.append({"date": vt, "event": "stopped", "name": r["name"],
                        "slot": r.get("slot") or "", "dose": r.get("dose_text") or "",
                        "note": r.get("notes") or ""})
        if not vt and not r["active"]:
            out.append({"date": "", "event": "no stop date recorded", "name": r["name"],
                        "slot": r.get("slot") or "", "dose": r.get("dose_text") or "",
                        "note": r.get("notes") or ""})
    out.sort(key=lambda x: (x["date"] or "0000-00-00"), reverse=True)
    return out


def night_doses(g, since):
    """Every dose recorded in the evening/night window, by the night it
    belongs to. A dose after midnight belongs to the night before."""
    by_night = {}
    for r in rows(g, "SELECT day, dtime, medicine, dose_text, status FROM doses "
                     "WHERE day >= ? ORDER BY day, dtime", (days_ago(40),)):
        t = (r.get("dtime") or "")[:5]
        if not t:
            continue
        night = r["day"]
        if t >= NIGHT_FROM:
            pass
        elif t <= NIGHT_TO:
            night = (datetime.date.fromisoformat(r["day"]) -
                     datetime.timedelta(days=1)).isoformat()
        else:
            continue
        if night < since:
            continue
        by_night.setdefault(night, []).append(
            "%s %s%s" % (t, r.get("medicine") or "?",
                         (" " + r["dose_text"]) if r.get("dose_text") else ""))
    return by_night


def sleep_nights(g, f, since):
    """One row per night: watch hours, his own check-in answer, and what was
    taken that evening. No score, no grade, no verdict -- standing rule."""
    watch = dict((r["date"], r["value"]) for r in rows(
        f, "SELECT date, value FROM health_metrics WHERE metric='sleep_hours' "
           "AND date >= ? ORDER BY date", (since,)))
    blocks = {}
    for r in rows(f, "SELECT date, asleep_h, awake_h, in_bed_start, in_bed_end "
                     "FROM health_sleep_blocks WHERE date >= ?", (since,)):
        blocks.setdefault(r["date"], []).append(r)
    ci_fit = dict((r["date"], r) for r in rows(
        f, "SELECT date, sleep, energy FROM checkins WHERE date >= ?", (since,)))
    ci_gut = dict((r["day"], r) for r in rows(
        g, "SELECT day, sleep, notes FROM days WHERE day >= ?", (since,)))
    doses = night_doses(g, since)

    out = []
    d = datetime.date.fromisoformat(since)
    end = datetime.date.today()
    while d <= end:
        iso = d.isoformat()
        b = blocks.get(iso) or []
        out.append({
            "night": iso,
            "watch_hours": watch.get(iso),
            "in_bed": (b[0].get("in_bed_start") or "") if b else "",
            "awake_h": (b[0].get("awake_h") or "") if b else "",
            "checkin_sleep": (ci_fit.get(iso) or {}).get("sleep")
                             if iso in ci_fit else (ci_gut.get(iso) or {}).get("sleep"),
            "checkin_energy": (ci_fit.get(iso) or {}).get("energy"),
            "evening_doses": "; ".join(doses.get(iso) or []),
        })
        d += datetime.timedelta(days=1)
    out.reverse()
    return out


def vitals_recent(g, f, since):
    out = rows(g, "SELECT day, vtime, sys, dia, pulse, weight, notes FROM vitals "
                  "WHERE day >= ? ORDER BY day DESC, vtime DESC", (since,))
    rhr = dict((r["date"], r["value"]) for r in rows(
        f, "SELECT date, value FROM health_metrics WHERE metric='resting_hr' "
           "AND date >= ?", (since,)))
    for r in out:
        r["watch_resting_hr"] = rhr.get(r["day"], "")
    for day in sorted(rhr, reverse=True):
        if not any(r["day"] == day for r in out):
            out.append({"day": day, "vtime": "", "sys": "", "dia": "", "pulse": "",
                        "weight": "", "notes": "", "watch_resting_hr": rhr[day]})
    out.sort(key=lambda r: (r["day"], r.get("vtime") or ""), reverse=True)
    return out


def labs_all(g):
    return rows(g, "SELECT day, test, section, value, unit, ref, flag, lab "
                   "FROM rec_labs ORDER BY day DESC, section, test")


def gut_recent(g, since):
    ep = rows(g, "SELECT day, etime, category, etype, side, severity, duration, "
                 "bristol, notes FROM episodes WHERE day >= ? "
                 "ORDER BY day DESC, etime DESC", (since,))
    dy = rows(g, "SELECT day, pain, pain_site, bristol, stools, syms, notes FROM days "
                 "WHERE day >= ? ORDER BY day DESC", (since,))
    return ep, dy


def meals_recent(g, since):
    """The SAME numbers the app shows. See departure 2: this is a SUM here,
    and test_health_mirror.py holds it equal to GutLog's nut_day()."""
    have = dict((r["day"], r) for r in rows(
        g, "SELECT day, COUNT(*) AS meals, COALESCE(SUM(kcal),0) AS kcal, "
           "COALESCE(SUM(protein),0) AS protein, COALESCE(SUM(fibre),0) AS fibre "
           "FROM meals WHERE day >= ? GROUP BY day", (since,)))
    usual = 3
    try:
        cfg = json.load(open(os.environ.get(
            "MIRROR_PLAN_FILE", "/root/gutlog/diet_plan.local.json"), encoding="utf-8"))
        mm = cfg.get("main_meals")
        if isinstance(mm, list) and mm:
            usual = len(mm)
    except (OSError, ValueError):
        pass
    out = []
    d = datetime.date.fromisoformat(since)
    end = datetime.date.today()
    while d <= end:
        iso = d.isoformat()
        r = have.get(iso)
        if not r:
            out.append({"day": iso, "logged": False, "partial": False,
                        "kcal": "", "protein": "", "fibre": "", "meals": 0})
        else:
            n = int(r["meals"])
            out.append({"day": iso, "logged": True, "partial": 0 < n < usual,
                        "kcal": int(round(r["kcal"])), "protein": round(r["protein"], 1),
                        "fibre": round(r["fibre"], 1), "meals": n})
        d += datetime.timedelta(days=1)
    out.reverse()
    return out


def documents_all(g):
    """The report PDFs already on the server: rec_docs is the record's own
    index of them, and `files` is the older upload vault. Both point into
    UPLOAD_DIR by a stored (hashed) name; the readable one is `orig`."""
    out = []
    for r in rows(g, "SELECT id, day, kind, title, source, finding, stored, orig, "
                     "status FROM rec_docs WHERE stored IS NOT NULL AND stored <> '' "
                     "ORDER BY day DESC, id DESC"):
        r["from"] = "rec_docs"
        out.append(r)
    seen = set(d["stored"] for d in out)
    for r in rows(g, "SELECT id, day, ftype AS kind, label AS title, stored, orig "
                     "FROM files WHERE stored IS NOT NULL AND stored <> '' "
                     "ORDER BY day DESC, id DESC"):
        if r["stored"] in seen:
            continue
        r["from"] = "files"
        r["source"] = ""
        r["finding"] = ""
        r["status"] = ""
        out.append(r)
    out.sort(key=lambda d: (d.get("day") or "", d.get("id") or 0), reverse=True)
    return out


def copy_documents(docs, dest):
    """Copy each document under a name a reader recognises. Skipped when the
    destination already matches by size -- 87 MB of unchanged PDFs should not
    be rewritten every night for the sake of a nightly job."""
    if not os.path.isdir(dest):
        os.makedirs(dest, mode=0o700)
    copied = skipped = missing = 0
    index = []
    for d in docs:
        src = os.path.join(UPLOAD_DIR, d["stored"])
        if not os.path.exists(src):
            missing += 1
            continue
        ext = os.path.splitext(d.get("orig") or d["stored"])[1].lower() or ".pdf"
        label = safe_name("%s %s - %s" % (d.get("day") or "undated",
                                          d.get("kind") or "Document",
                                          d.get("title") or ""), "document")
        name = label.rstrip(" -") + ext
        dst = os.path.join(dest, name)
        try:
            if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                skipped += 1
            else:
                shutil.copy2(src, dst)
                copied += 1
        except OSError:
            missing += 1
            continue
        index.append(dict(d, mirror_name=name))
    return index, {"copied": copied, "unchanged": skipped, "missing": missing}


def plans_all(g):
    out = []
    for p in rows(g, "SELECT id, title, first_considered, status, archived FROM plans "
                     "ORDER BY first_considered DESC, id DESC"):
        f = rows(g, "SELECT stored_name, original_name FROM plan_files "
                    "WHERE plan_id=? ORDER BY id DESC LIMIT 1", (p["id"],))
        p["file"] = f[0] if f else None
        out.append(p)
    return out


# -------------------------------------------------------------- the writing
def md_table(head, body):
    if not body:
        return "_Nothing recorded._\n"
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * len(head)) + "|"]
    for r in body:
        out.append("| " + " | ".join("" if c is None else str(c).replace("|", "/")
                                     for c in r) + " |")
    return "\n".join(out) + "\n"


def build_markdown(data, generated):
    L = []
    A = L.append
    A("# Health record snapshot")
    A("")
    A("Generated **%s IST** from the live databases. Personal record only."
      % generated)
    A("")
    A("This is a mirror, not a source. Nothing is edited here. Where something")
    A("was never recorded it says so rather than showing a zero or a guess.")
    A("")

    A("## Medicines now")
    A("")
    A(md_table(["Medicine", "Molecule", "Slot", "Dose", "With food", "Since"],
               [[m["name"], m.get("molecule") or "", m.get("slot") or "",
                 m.get("dose_text") or "", "yes" if m.get("with_food") else "",
                 dmy(m.get("since"))] for m in data["meds_now"]]))
    A("")
    A("### Changes in the last 90 days")
    A("")
    A(md_table(["Date", "Event", "Medicine", "Slot", "Dose"],
               [[dmy(e["date"]) if e["date"] else "not recorded", e["event"],
                 e["name"], e["slot"], e["dose"]] for e in data["med_events"]]))
    A("")

    A("## Sleep, last 30 nights")
    A("")
    A("Watch hours, his own check-in answer, and what was taken that evening,")
    A("side by side so nights with and without a given medicine can be told")
    A("apart. **No score, grade or good/poor label is given, here or anywhere.**")
    A("")
    A(md_table(["Night", "Watch hours", "Awake h", "Check-in sleep", "Energy",
                "Taken that evening"],
               [[dmy(s["night"]), s["watch_hours"] if s["watch_hours"] is not None else "not recorded",
                 s["awake_h"], s["checkin_sleep"] if s["checkin_sleep"] is not None else "",
                 s["checkin_energy"] if s["checkin_energy"] is not None else "",
                 s["evening_doses"] or ""] for s in data["sleep"]]))
    A("")

    A("## Vitals, last 30 days")
    A("")
    A(md_table(["Date", "Time", "BP", "Pulse", "Watch resting HR", "Weight"],
               [[dmy(v["day"]), v.get("vtime") or "",
                 ("%s/%s" % (v["sys"], v["dia"])) if v.get("sys") else "",
                 v.get("pulse") or "", v.get("watch_resting_hr") or "",
                 v.get("weight") or ""] for v in data["vitals"]]))
    A("")

    A("## Gut, last 30 days")
    A("")
    A("### Episodes")
    A("")
    A(md_table(["Date", "Time", "Category", "Type", "Severity", "Duration", "Bristol"],
               [[dmy(e["day"]), e.get("etime") or "", e.get("category") or "",
                 e.get("etype") or "", e.get("severity") or "",
                 e.get("duration") or "", e.get("bristol") or ""]
                for e in data["episodes"]]))
    A("")
    A("### Day log")
    A("")
    A(md_table(["Date", "Pain", "Site", "Bristol", "Stools", "Symptoms"],
               [[dmy(d["day"]), d.get("pain") if d.get("pain") is not None else "",
                 d.get("pain_site") or "", d.get("bristol") or "",
                 d.get("stools") or "", d.get("syms") or ""]
                for d in data["daylog"]]))
    A("")

    A("## Meals, last 30 days")
    A("")
    A("Estimated, as in the app. A day with nothing logged says **not logged**")
    A("and never 0 kcal -- those are different facts. A day with fewer meals")
    A("than usual is marked **partial**, so a small total is not read as a")
    A("small day.")
    A("")
    A(md_table(["Date", "kcal", "Protein g", "Fibre g", "Meals", ""],
               [[dmy(m["day"]),
                 m["kcal"] if m["logged"] else "not logged",
                 m["protein"] if m["logged"] else "",
                 m["fibre"] if m["logged"] else "",
                 m["meals"] if m["logged"] else "",
                 "partial" if m["partial"] else ""] for m in data["meals"]]))
    A("")

    A("## Labs, every result on record")
    A("")
    A(md_table(["Report date", "Section", "Test", "Result", "Unit", "Reference", "Lab"],
               [[dmy(l["day"]), l.get("section") or "", l["test"],
                 l.get("value") or "", l.get("unit") or "", l.get("ref") or "",
                 l.get("lab") or ""] for l in data["labs"]]))
    A("")

    A("## Plans")
    A("")
    A(md_table(["First considered", "Status", "Title", "Document"],
               [[dmy(p["first_considered"]), p["status"], p["title"],
                 (p["file"] or {}).get("original_name") or "no document"]
                for p in data["plans"] if not p.get("archived")]))
    A("")

    A("## Documents")
    A("")
    A("The reports themselves, copied into `documents/` beside this file under")
    A("names a reader can recognise. The findings column is what was recorded")
    A("about a report, not a re-reading of it.")
    A("")
    A(md_table(["Date", "Kind", "Title", "Source", "File"],
               [[dmy(d["day"]), d.get("kind") or "", d.get("title") or "",
                 d.get("source") or "", d.get("mirror_name") or ""]
                for d in data["documents"]]))
    A("")
    A("---")
    A("")
    A("Nothing in this file is a clinical judgement. It is what was recorded,")
    A("with the dates it was recorded on.")
    A("")
    return "\n".join(L)


def write_csv(path, head, body):
    fh = open(path, "w", encoding="utf-8", newline="")
    try:
        wr = csv.writer(fh)
        wr.writerow(head)
        for r in body:
            wr.writerow(r)
    finally:
        fh.close()


def safe_name(s, fallback):
    keep = []
    for ch in str(s or ""):
        keep.append(ch if (ch.isalnum() or ch in " -_.()") else "-")
    out = "".join(keep).strip().strip(".")[:120]
    return out or fallback


def collect(days):
    g = ro(GUTLOG_DB)
    f = ro(FITLOG_DB) if os.path.exists(FITLOG_DB) else None
    since30 = days_ago(days)
    since90 = days_ago(90)
    ep, dy = gut_recent(g, since30)
    data = {
        "meds_now": medicines_now(g),
        "med_events": medicine_events(g, since90),
        "sleep": sleep_nights(g, f, since30) if f else [],
        "vitals": vitals_recent(g, f, since30) if f else vitals_recent(g, ro(GUTLOG_DB), since30),
        "episodes": ep,
        "daylog": dy,
        "meals": meals_recent(g, since30),
        "labs": labs_all(g),
        "plans": plans_all(g),
        "documents": documents_all(g),
    }
    g.close()
    if f:
        f.close()
    return data


def generate(out_dir, days=30, dry_run=False):
    data = collect(days)
    generated = datetime.datetime.now().strftime("%d-%b-%Y %H:%M")

    snap = os.path.join(out_dir, "snapshot")
    csvd = os.path.join(snap, "csv")
    pland = os.path.join(snap, "plans")
    docd = os.path.join(snap, "documents")

    # The documents are copied BEFORE the markdown is built, because the
    # Documents table names the file each row landed in. A dry run does not
    # copy, so it works the names out without writing anything.
    if dry_run:
        data["documents"] = [dict(d, mirror_name="(dry run)") for d in data["documents"]]
        doc_stats = {"copied": 0, "unchanged": 0, "missing": 0}
    else:
        for d in (out_dir, snap, csvd, pland, docd):
            if not os.path.isdir(d):
                os.makedirs(d, mode=0o700)
        try:
            os.chmod(out_dir, 0o700)
        except OSError:
            pass
        data["documents"], doc_stats = copy_documents(data["documents"], docd)

    md = build_markdown(data, generated)

    counts = {"sections": 9, "meds_now": len(data["meds_now"]),
              "med_events": len(data["med_events"]), "sleep_nights": len(data["sleep"]),
              "vitals": len(data["vitals"]), "episodes": len(data["episodes"]),
              "meal_days": len(data["meals"]), "labs": len(data["labs"]),
              "plans": len(data["plans"]), "documents": len(data["documents"]),
              "documents_copied": doc_stats["copied"],
              "documents_unchanged": doc_stats["unchanged"],
              "documents_missing": doc_stats["missing"],
              "markdown_bytes": len(md.encode("utf-8"))}
    if dry_run:
        return counts, md

    latest = os.path.join(snap, "health_snapshot_latest.md")
    fh = open(latest, "w", encoding="utf-8")
    try:
        fh.write(md)
    finally:
        fh.close()
    shutil.copy2(latest, os.path.join(snap, "health_snapshot_%s.md" % today()))

    write_csv(os.path.join(csvd, "medicines_now.csv"),
              ["name", "molecule", "form", "slot", "dose_text", "with_food", "since", "notes"],
              [[m.get(k) for k in ("name", "molecule", "form", "slot", "dose_text",
                                   "with_food", "since", "notes")] for m in data["meds_now"]])
    write_csv(os.path.join(csvd, "medicine_events.csv"),
              ["date", "event", "name", "slot", "dose", "note"],
              [[e.get(k) for k in ("date", "event", "name", "slot", "dose", "note")]
               for e in data["med_events"]])
    write_csv(os.path.join(csvd, "sleep.csv"),
              ["night", "watch_hours", "in_bed", "awake_h", "checkin_sleep",
               "checkin_energy", "evening_doses"],
              [[s.get(k) for k in ("night", "watch_hours", "in_bed", "awake_h",
                                   "checkin_sleep", "checkin_energy", "evening_doses")]
               for s in data["sleep"]])
    write_csv(os.path.join(csvd, "vitals.csv"),
              ["day", "vtime", "sys", "dia", "pulse", "watch_resting_hr", "weight", "notes"],
              [[v.get(k) for k in ("day", "vtime", "sys", "dia", "pulse",
                                   "watch_resting_hr", "weight", "notes")]
               for v in data["vitals"]])
    write_csv(os.path.join(csvd, "labs.csv"),
              ["day", "section", "test", "value", "unit", "ref", "flag", "lab"],
              [[l.get(k) for k in ("day", "section", "test", "value", "unit",
                                   "ref", "flag", "lab")] for l in data["labs"]])
    write_csv(os.path.join(csvd, "gut_episodes.csv"),
              ["day", "etime", "category", "etype", "side", "severity", "duration",
               "bristol", "notes"],
              [[e.get(k) for k in ("day", "etime", "category", "etype", "side",
                                   "severity", "duration", "bristol", "notes")]
               for e in data["episodes"]])
    write_csv(os.path.join(csvd, "gut_daylog.csv"),
              ["day", "pain", "pain_site", "bristol", "stools", "syms", "notes"],
              [[d.get(k) for k in ("day", "pain", "pain_site", "bristol", "stools",
                                   "syms", "notes")] for d in data["daylog"]])
    write_csv(os.path.join(csvd, "meals.csv"),
              ["day", "logged", "partial", "kcal", "protein", "fibre", "meals"],
              [[m.get(k) for k in ("day", "logged", "partial", "kcal", "protein",
                                   "fibre", "meals")] for m in data["meals"]])
    write_csv(os.path.join(csvd, "documents.csv"),
              ["day", "kind", "title", "source", "finding", "status", "from",
               "original_name", "mirror_name"],
              [[d.get(k) for k in ("day", "kind", "title", "source", "finding",
                                   "status", "from", "orig", "mirror_name")]
               for d in data["documents"]])
    write_csv(os.path.join(csvd, "plans.csv"),
              ["first_considered", "status", "title", "document", "archived"],
              [[p.get("first_considered"), p.get("status"), p.get("title"),
                (p.get("file") or {}).get("original_name") or "", p.get("archived")]
               for p in data["plans"]])

    # plan documents, under a name a reader can recognise
    for p in data["plans"]:
        f = p.get("file")
        if not f:
            continue
        src = os.path.join(PLANS_DIR, f["stored_name"])
        if not os.path.exists(src):
            continue
        base = safe_name(p.get("title"), "plan-%s" % p["id"])
        dst = os.path.join(pland, "%s (%s).pdf" % (base, p.get("first_considered") or ""))
        shutil.copy2(src, dst)
    counts["plan_pdfs"] = len(os.listdir(pland))

    # retention: keep the newest KEEP_DATED dated copies
    dated = sorted([n for n in os.listdir(snap)
                    if n.startswith("health_snapshot_") and n != "health_snapshot_latest.md"])
    for old in dated[:-KEEP_DATED]:
        try:
            os.remove(os.path.join(snap, old))
        except OSError:
            pass
    counts["dated_copies"] = min(len(dated), KEEP_DATED)
    return counts, md


# ------------------------------------------------------------------ upload
def upload(out_dir):
    """rclone, scope drive.file -- it can only touch what it created.
    Returns (ok, message). No token, path or account is printed.

    MIRROR_RCLONE and MIRROR_RCLONE_CONF exist so both refusal paths can be
    reached on purpose. Without them the suite could only ever exercise
    whichever branch the machine it ran on happened to fall into -- on a PC
    with no rclone, the "not configured" check was never reached at all, and
    the assertion passed for the wrong reason until a mutation showed it."""
    exe = os.environ.get("MIRROR_RCLONE") or shutil.which("rclone")
    if not exe or not os.path.exists(exe):
        return False, "rclone is not installed"
    conf = (os.environ.get("MIRROR_RCLONE_CONF")
            or os.path.expanduser("~/.config/rclone/rclone.conf"))
    if not os.path.exists(conf):
        return False, "rclone has no configuration yet (the one sign-in is still to do)"
    dest = "%s:%s" % (RCLONE_REMOTE, RCLONE_PATH)
    p = subprocess.Popen([exe, "copy", os.path.join(out_dir, "snapshot"),
                          dest + "/snapshot", "--drive-use-trash=false",
                          "--transfers", "2", "--checkers", "2", "--stats", "0"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _o, e = p.communicate()
    if p.returncode != 0:
        return False, "rclone exited %d: %s" % (p.returncode,
                                                e.decode("utf-8", "replace").strip()[:300])
    return True, "uploaded"


def stamp_success(out_dir):
    """The freshness marker GutLog reads. A stale mirror must be visible:
    Claude reading last week's record as today's is worse than no mirror."""
    path = os.path.join(out_dir, "last_success.json")
    fh = open(path, "w", encoding="utf-8")
    try:
        json.dump({"at": datetime.datetime.now().replace(microsecond=0).isoformat(),
                   "epoch": int(datetime.datetime.now().timestamp())}, fh)
    finally:
        fh.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/root/health_mirror")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    started = datetime.datetime.now().strftime("%d-%b-%Y %H:%M:%S")
    print("health mirror: started %s IST" % started)
    try:
        counts, _md = generate(a.out, days=a.days, dry_run=a.dry_run)
    except sqlite3.Error as exc:
        print("FAILED reading a database: %s" % exc)
        return 1
    for k in sorted(counts):
        print("  %-16s %s" % (k, counts[k]))
    if a.dry_run:
        print("DRY RUN. Nothing written.")
        return 0
    print("  staged in       %s" % os.path.join(a.out, "snapshot"))

    if a.upload:
        ok, msg = upload(a.out)
        print("  upload          %s" % msg)
        if not ok:
            print("NOT marking success: the mirror on Drive is not current.")
            return 1
    else:
        print("  upload          skipped (--upload not given)")
        return 0

    print("  freshness mark  %s" % stamp_success(a.out))
    print("health mirror: done %s IST" % datetime.datetime.now().strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

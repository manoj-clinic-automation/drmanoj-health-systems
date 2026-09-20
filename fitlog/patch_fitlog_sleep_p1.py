#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FitLog :: FITLOG_SLEEP_P1 -- the night is stored whole.

WHAT WAS WRONG, ESTABLISHED FROM health_raw ON 2026-09-15
----------------------------------------------------------
Apple Health holds 5 h 41 m for the night of 14->15 Sep, roughly 22:30 to
just past 05:00. FitLog stored 1.67 h. The four missing hours were NOT lost
by this module. Read against the live database:

  raw id 208 / 211, metric sleep_analysis, ONE point:
      date        2026-09-15 00:00:00 +0530
      sleepStart  2026-09-15 03:13:23 +0530
      sleepEnd    2026-09-15 04:57:30 +0530
      totalSleep  1.6685396374927626
      core 1.2430662  rem 0.4254734  deep 0  awake 0.0667416
      asleep 0        inBed 0

  health_metrics 2026-09-15 sleep_hours = 1.6685396374927626

Bit for bit what arrived. Health Auto Export delivered a 1 h 44 m fragment
of the night -- the last block only -- and the parser stored it faithfully.
Neither suspect in the brief fired: `totalSleep` was present and positive so
the phase-sum branch was never reached, and only ONE point per date has ever
been delivered so nothing overwrote anything. The export window is the thing
to widen, and that is a phone-side setting, not code.

WHY THIS PATCH IS STILL THE BLOCKING FIX
----------------------------------------
Widening the export ALONE would not have fixed the number. Auto Export dates
every sleep point at midnight of the day the night is filed under -- look at
the 09-14 point: it covers 23:08 on 09-13 to 03:19 on 09-14 and is still
stamped 2026-09-14 00:00:00. So the moment a wider window delivers a night in
TWO blocks, both arrive stamped identically, both land on the record key
hae|sleep_hours|day|<date>|00:00:00, and the second silently replaces the
first. The night would have gone on reading as its last block, and the export
change would have looked like it had worked.

  S03 Sleep Block Identity
    - a sleep sample is keyed by its own sleepStart, never by the midnight
      stamp every block of the night shares
    - blocks that OVERLAP are competing descriptions of one stretch of the
      night -- Auto Export re-segmenting a night it has already sent -- and
      the longest-span description wins, never the sum. Summing them would
      invent sleep he did not have, which is the one error this record must
      never make.
    - blocks that do not overlap are different stretches and ADD
    - `asleep` is Apple's retired pre-stage category and arrives as 0 on
      every Watch night on this server. A total is believed only when it is
      greater than zero; awake time is never counted as sleep.

The blocks are stored with their real IST start and end in health_sleep_blocks,
so the night's total is computed from spans rather than from one number, and
Phase 2 has the whole night to read.

No scoring. Nothing here grades a night.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, reversible. Python 3.9.

    python3 patch_fitlog_sleep_p1.py --check
    python3 patch_fitlog_sleep_p1.py --file /root/fitlog/health_ingest.py
    python3 patch_fitlog_sleep_p1.py --file health_ingest.py --reverse prev.py
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "health_ingest.py")
MARKER = "FITLOG_SLEEP_P1"

# ---------------------------------------------------------------- edit 1
DOC_OLD = "Deterministic. No LLM in the path. Python 3.9 compatible.\n"

DOC_NEW = '''    S03 Sleep Block Identity  (FITLOG_SLEEP_P1)
      - Auto Export dates every sleep point at midnight of the day the
        night is filed under, so two blocks of one night arrive with
        identical stamps; a sleep sample is keyed by its own sleepStart
      - blocks that OVERLAP are competing descriptions of one stretch of
        the night and the longest-span one wins, never the sum
      - blocks that do not overlap are different stretches and ADD
      - a total is believed only when greater than zero: `asleep` is
        Apple's retired pre-stage category and arrives as 0 on every
        Watch night seen here. Awake time is never counted as sleep.

Deterministic. No LLM in the path. Python 3.9 compatible.
'''

# ---------------------------------------------------------------- edit 2
SLEEP_OLD = '''def _sleep_hours(point):
    """
    Sleep arrives either as a total or split into phases.
    Prefer an explicit asleep total; otherwise sum the phases.
    Values are hours in HAE v2.
    """
    for key in ("totalSleep", "asleep"):
        val = _num(point.get(key))
        if val is not None:
            return val
    phases = ("deep", "core", "rem", "light")
    total = 0.0
    seen = False
    for key in phases:
        val = _num(point.get(key))
        if val is not None:
            total = total + val
            seen = True
    if seen:
        return total
    return _num(point.get("qty"))
'''

SLEEP_NEW = '''def _sleep_hours(point):
    """
    Hours ASLEEP in one sleep block. None when the point measures nothing.

    Every Apple Watch night in health_raw on this server carries
    asleep=0 and inBed=0 beside a real totalSleep and real core / deep /
    rem / awake figures: `asleep` is Apple's retired pre-stage category,
    not a total. Preferring it merely because it is present would store
    a zero night, so a total is believed only when it is greater than
    zero.

    Values are hours in HAE v2.
    """
    for key in ("totalSleep", "asleep"):
        val = _num(point.get(key))
        if val is not None and val > 0:
            return val
    # Apple's stages are Awake / REM / Core / Deep. "light" is kept for
    # exporters using the older naming. "awake" is deliberately absent:
    # it is time in bed not asleep, and adding it would overstate the
    # night by exactly the part he was lying there awake for.
    phases = ("deep", "core", "rem", "light")
    total = 0.0
    seen = False
    for key in phases:
        val = _num(point.get(key))
        if val is not None:
            total = total + val
            seen = True
    if seen:
        return total
    qty = _num(point.get("qty"))
    if qty is not None and qty > 0:
        return qty
    # Neither a positive total, nor stages, nor a usable qty. That is
    # nothing measured, not a measurement of nothing -- returning 0.0
    # would put a zero night on the record.
    return None
'''

# ---------------------------------------------------------------- edit 3
TIME_OLD = '''    part = text[11:19]
    if len(part) != 8 or part[2] != ":" or part[5] != ":":
        return ""
    return part
'''

TIME_NEW = '''    part = text[11:19]
    if len(part) != 8 or part[2] != ":" or part[5] != ":":
        return ""
    return part


def _ist_stamp(raw):
    """
    "YYYY-MM-DD HH:MM:SS" in IST, or None when the stamp cannot be read.

    Every time this system stores or shows is IST. Auto Export already
    sends +0530 and passes straight through; a Z stamp is UTC and is
    moved on by 5h30m; any other offset is converted. A naive stamp is
    taken as already local, which is what every feed here sends.

    None rather than a guess: a sleep block filed at the wrong hour is
    worse than a blank one, because it would move the night onto the
    wrong day.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("T", " ")
    if len(text) < 19:
        return None
    try:
        stamp = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    rest = text[19:].strip()
    if rest[:1] == ".":
        rest = rest.lstrip(".0123456789").strip()
    if not rest:
        return stamp.strftime("%Y-%m-%d %H:%M:%S")
    if rest[0] in ("Z", "z"):
        moved = stamp + timedelta(minutes=IST_OFFSET_MINUTES)
        return moved.strftime("%Y-%m-%d %H:%M:%S")
    if rest[0] not in ("+", "-"):
        return None
    digits = rest[1:].replace(":", "")
    if len(digits) < 4 or not digits[:4].isdigit():
        return None
    offset = int(digits[:2]) * 60 + int(digits[2:4])
    if rest[0] == "-":
        offset = -offset
    moved = stamp + timedelta(minutes=IST_OFFSET_MINUTES - offset)
    return moved.strftime("%Y-%m-%d %H:%M:%S")


def _sleep_block_time(point, stamp):
    """
    The time of day that identifies WHICH block of the night this is.

    S03. Auto Export stamps every sleep point at midnight of the day the
    night is filed under -- the 2026-09-14 point on this server covers
    23:08 on 09-13 to 03:19 on 09-14 and is still stamped
    "2026-09-14 00:00:00 +0530". Two blocks of one night therefore arrive
    indistinguishable, collide on one record slot, and the second
    replaces the first. sleepStart is what tells them apart.

    The hour returned may belong to the previous calendar day; it is a
    slot label, not a date. Two blocks of one night sharing a start time
    to the second would be the same block.
    """
    for key in ("sleepStart", "inBedStart"):
        got = _apple_time(point.get(key))
        if got:
            return got
    return _apple_time(stamp)


def parse_sleep_blocks(payload):
    """
    Every sleep block in an Auto Export body, with its real IST span.

    One dict per sleep_analysis point. The day's figure is derived from
    these spans rather than from a single number, which is what lets a
    night delivered in pieces be put back together -- and what stops a
    re-segmented night from being added to itself.
    """
    out = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    for entry in data.get("metrics") or []:
        if not isinstance(entry, dict):
            continue
        raw_name = (entry.get("name") or "").strip().lower()
        if METRIC_MAP.get(raw_name) != "sleep_hours":
            continue
        for point in entry.get("data") or []:
            if not isinstance(point, dict):
                continue
            date = _parse_date(point.get("date"))
            if not date:
                continue
            start = _ist_stamp(point.get("sleepStart"))
            end = _ist_stamp(point.get("sleepEnd"))
            bed_start = _ist_stamp(point.get("inBedStart"))
            bed_end = _ist_stamp(point.get("inBedEnd"))
            asleep = _sleep_hours(point)
            if asleep is None and not (start and end):
                continue
            out.append({
                "date": date,
                "start_ts": start,
                "end_ts": end,
                "in_bed_start": bed_start,
                "in_bed_end": bed_end,
                "asleep_h": asleep,
                "rem_h": _num(point.get("rem")),
                "core_h": _num(point.get("core")),
                "deep_h": _num(point.get("deep")),
                "awake_h": _num(point.get("awake")),
                "device": str(point.get("source") or "").strip(),
            })
    return out
'''

# ---------------------------------------------------------------- edit 4
SAMPLE_OLD = '''            if canonical == "sleep_hours":
                value = _sleep_hours(point)
            else:
                value = _num(point.get("qty"))
                if value is None:
                    value = _num(point.get("Avg"))
            if value is None:
                continue
            # Bind to a fresh name: `unit` is the entry-level unit and is
            # reused by every point in this entry. Rebinding it converts
            # the first sample, then makes every later sample look like
            # it is already in kcal and pass through unconverted.
            value, point_unit = _apple_convert(canonical, value, unit)
            samples.append((date, _apple_time(stamp), canonical,
                            value, point_unit))
'''

SAMPLE_NEW = '''            if canonical == "sleep_hours":
                value = _sleep_hours(point)
                # S03: the slot a sleep sample occupies is its BLOCK, not
                # the midnight stamp every block of the night shares.
                tod = _sleep_block_time(point, stamp)
            else:
                value = _num(point.get("qty"))
                if value is None:
                    value = _num(point.get("Avg"))
                tod = _apple_time(stamp)
            if value is None:
                continue
            # Bind to a fresh name: `unit` is the entry-level unit and is
            # reused by every point in this entry. Rebinding it converts
            # the first sample, then makes every later sample look like
            # it is already in kcal and pass through unconverted.
            value, point_unit = _apple_convert(canonical, value, unit)
            samples.append((date, tod, canonical, value, point_unit))
'''

# ---------------------------------------------------------------- edit 5
STORE_HEAD_OLD = '''def store_apple(conn, records, workout_rows, source, raw_text):
'''

STORE_HEAD_NEW = '''def _ensure_sleep_block_table(conn):
    """
    The per-block sleep store. Additive, idempotent, created in place.

    Separate from health_hc_records because a sleep block is a span with
    stages, not a scalar sample, and the night's arithmetic needs the
    spans. Built here rather than in the base migration for the same
    reason health_hc_records is: several suites hand-roll their schema
    and the live database must not need a second deploy step.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS health_sleep_blocks ("
        "block_key TEXT PRIMARY KEY, date TEXT NOT NULL, "
        "source TEXT NOT NULL, feed TEXT NOT NULL DEFAULT '', "
        "start_ts TEXT, end_ts TEXT, "
        "in_bed_start TEXT, in_bed_end TEXT, "
        "asleep_h REAL, rem_h REAL, core_h REAL, deep_h REAL, "
        "awake_h REAL, device TEXT, ingested_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_sleep_blocks_date "
        "ON health_sleep_blocks (date, source)"
    )
    return True


def _span_minutes(start, end):
    """Minutes between two IST stamps, or None."""
    if not start or not end:
        return None
    try:
        a = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return (b - a).total_seconds() / 60.0


def cluster_sleep_blocks(blocks):
    """
    Group blocks that describe the same stretch of the night. Rule S03.

    Two blocks that overlap are competing descriptions of one stretch --
    Auto Export re-segmenting a night it has already delivered -- and the
    longest-span description wins. Adding them would invent sleep he did
    not have. Two blocks that do not overlap are different stretches and
    add; touching end-to-start counts as separate, because that is two
    recorded blocks with no gap rather than one.

    Blocks with no usable span cannot be placed against the others and
    stand alone. Returns a list of clusters, earliest first.
    """
    usable = []
    loose = []
    for b in blocks:
        if b.get("start_ts") and b.get("end_ts") and b["end_ts"] > b["start_ts"]:
            usable.append(b)
        else:
            loose.append(b)
    usable.sort(key=lambda b: (b["start_ts"], b["end_ts"]))

    clusters = []
    for b in usable:
        if clusters and b["start_ts"] < clusters[-1]["end"]:
            cur = clusters[-1]
            cur["members"].append(b)
            if b["end_ts"] > cur["end"]:
                cur["end"] = b["end_ts"]
        else:
            clusters.append({"start": b["start_ts"], "end": b["end_ts"],
                             "members": [b]})

    for cur in clusters:
        best = cur["members"][0]
        best_span = _span_minutes(best["start_ts"], best["end_ts"]) or 0.0
        for b in cur["members"][1:]:
            span = _span_minutes(b["start_ts"], b["end_ts"]) or 0.0
            if span > best_span:
                best, best_span = b, span
        cur["winner"] = best
        cur["span_min"] = best_span

    for b in loose:
        clusters.append({"start": b.get("start_ts"), "end": b.get("end_ts"),
                         "members": [b], "winner": b, "span_min": None})
    return clusters


def _sum_stage(clusters, key):
    got = [c["winner"].get(key) for c in clusters]
    got = [g for g in got if g is not None]
    if not got:
        return None
    return sum(got)


def sleep_night(conn, date, source="applewatch"):
    """
    The whole night filed under one date, rebuilt from its blocks.

    Reports what was measured and nothing else. There is no score here
    and no grade: a night is shown, never marked.

    Returns None when nothing was recorded for that date. `awakenings`
    counts the BREAKS BETWEEN recorded sleep blocks, which is a floor,
    not a count of times he woke -- Auto Export's aggregate carries no
    awakening count. `awake_h` is the measured time awake and is exact.
    """
    _ensure_sleep_block_table(conn)
    rows = conn.execute(
        "SELECT block_key, date, start_ts, end_ts, in_bed_start, in_bed_end, "
        "asleep_h, rem_h, core_h, deep_h, awake_h, device "
        "FROM health_sleep_blocks WHERE date = ? AND source = ? "
        "ORDER BY start_ts, block_key", (date, source)).fetchall()
    if not rows:
        return None

    blocks = []
    for r in rows:
        blocks.append({
            "block_key": r[0], "date": r[1], "start_ts": r[2], "end_ts": r[3],
            "in_bed_start": r[4], "in_bed_end": r[5], "asleep_h": r[6],
            "rem_h": r[7], "core_h": r[8], "deep_h": r[9], "awake_h": r[10],
            "device": r[11],
        })

    clusters = cluster_sleep_blocks(blocks)
    starts = [c["start"] for c in clusters if c["start"]]
    ends = [c["end"] for c in clusters if c["end"]]
    bed_starts = [b["in_bed_start"] for b in blocks if b.get("in_bed_start")]
    bed_ends = [b["in_bed_end"] for b in blocks if b.get("in_bed_end")]

    start_ts = min(starts) if starts else None
    end_ts = max(ends) if ends else None
    bed_start = min(bed_starts) if bed_starts else start_ts
    bed_end = max(bed_ends) if bed_ends else end_ts

    asleep = _sum_stage(clusters, "asleep_h")
    return {
        "date": date,
        "source": source,
        "rule": "S03 Sleep Block Identity",
        "blocks": len(blocks),
        "stretches": len(clusters),
        # Breaks BETWEEN recorded blocks. A floor on the number of times
        # he woke, never presented as the number of times he woke.
        "awakenings": max(0, len(clusters) - 1),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "in_bed_start": bed_start,
        "in_bed_end": bed_end,
        "asleep_h": asleep,
        "rem_h": _sum_stage(clusters, "rem_h"),
        "core_h": _sum_stage(clusters, "core_h"),
        "deep_h": _sum_stage(clusters, "deep_h"),
        "awake_h": _sum_stage(clusters, "awake_h"),
        "in_bed_h": (lambda m: None if m is None else m / 60.0)(
            _span_minutes(bed_start, bed_end)),
        "device": (blocks[0].get("device") or "") if blocks else "",
    }


def store_sleep_blocks(conn, blocks, source, ts, feed=None):
    """
    Upsert one payload's sleep blocks and return the dates they touch.

    The key is the block's SPAN, never its value, so a redelivery of the
    same block updates in place and a night cannot be added to itself.
    """
    if feed is None:
        feed = FEED_HAE
    _ensure_sleep_block_table(conn)
    cur = conn.cursor()
    touched = set()
    for b in blocks or []:
        key = "|".join([feed, source, b.get("date") or "",
                        b.get("start_ts") or "", b.get("end_ts") or ""])
        cur.execute(
            "INSERT INTO health_sleep_blocks "
            "(block_key, date, source, feed, start_ts, end_ts, "
            " in_bed_start, in_bed_end, asleep_h, rem_h, core_h, deep_h, "
            " awake_h, device, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(block_key) DO UPDATE SET "
            "in_bed_start=excluded.in_bed_start, "
            "in_bed_end=excluded.in_bed_end, asleep_h=excluded.asleep_h, "
            "rem_h=excluded.rem_h, core_h=excluded.core_h, "
            "deep_h=excluded.deep_h, awake_h=excluded.awake_h, "
            "device=excluded.device, ingested_at=excluded.ingested_at",
            (key, b.get("date"), source, feed, b.get("start_ts"),
             b.get("end_ts"), b.get("in_bed_start"), b.get("in_bed_end"),
             b.get("asleep_h"), b.get("rem_h"), b.get("core_h"),
             b.get("deep_h"), b.get("awake_h"), b.get("device"), ts),
        )
        if b.get("date"):
            touched.add(b["date"])
    return sorted(touched)


def store_apple(conn, records, workout_rows, source, raw_text,
                sleep_blocks=None):
'''

# ---------------------------------------------------------------- edit 6
TAIL_OLD = '''    for date, metric in sorted(touched):
        got = _recompute_value(cur, date, metric, source, _APPLE_SUM)
        if got is None:
            continue
        _write_daily(cur, date, metric, got[0], got[1], source, ts)

    conn.commit()
    return new_rows, updated_rows, sorted(set(d for d, _ in touched))
'''

TAIL_NEW = '''    for date, metric in sorted(touched):
        got = _recompute_value(cur, date, metric, source, _APPLE_SUM)
        if got is None:
            continue
        _write_daily(cur, date, metric, got[0], got[1], source, ts)

    # S03: sleep_hours is written LAST and from the blocks, not from the
    # generic day recompute. The generic path can only add scalars up; a
    # night needs its spans, so that a re-segmented night is not added to
    # itself and a night delivered in pieces is put back together.
    sleep_dates = store_sleep_blocks(conn, sleep_blocks, source, ts)
    for date in sorted(set(sleep_dates) | set(d for d, m in touched
                                              if m == "sleep_hours")):
        night = sleep_night(conn, date, source)
        if night is None or night.get("asleep_h") is None:
            continue
        _write_daily(cur, date, "sleep_hours", night["asleep_h"], "hr",
                     source, ts)

    conn.commit()
    all_dates = set(d for d, _ in touched) | set(sleep_dates)
    return new_rows, updated_rows, sorted(all_dates)
'''

# ---------------------------------------------------------------- edit 7
ROUTE_OLD = '''    records, workout_rows, skipped = parse_apple_records(payload)

    conn = _connect()
    try:
        new_rows, updated_rows, dates = store_apple(
            conn, records, workout_rows, source, raw_text)
    finally:
        conn.close()
'''

ROUTE_NEW = '''    records, workout_rows, skipped = parse_apple_records(payload)
    # S03: the night is rebuilt from its blocks and their spans, not from
    # the one midnight-stamped figure a day used to be.
    sleep_blocks = parse_sleep_blocks(payload)

    conn = _connect()
    try:
        new_rows, updated_rows, dates = store_apple(
            conn, records, workout_rows, source, raw_text,
            sleep_blocks=sleep_blocks)
    finally:
        conn.close()
'''

RESP_OLD = '''        "workouts_stored": len(workout_rows),
        "dates": dates,
        "skipped_metrics": skipped,
'''

RESP_NEW = '''        "workouts_stored": len(workout_rows),
        "sleep_blocks": len(sleep_blocks),
        "dates": dates,
        "skipped_metrics": skipped,
'''

EDITS = [
    ("S03 in the rule block", DOC_OLD, DOC_NEW),
    ("_sleep_hours: a zero total is not a total", SLEEP_OLD, SLEEP_NEW),
    ("IST stamps, block identity, block parser", TIME_OLD, TIME_NEW),
    ("key a sleep sample by its block", SAMPLE_OLD, SAMPLE_NEW),
    ("block store and night rebuild", STORE_HEAD_OLD, STORE_HEAD_NEW),
    ("write sleep_hours from the blocks", TAIL_OLD, TAIL_NEW),
    ("parse blocks on the ingest route", ROUTE_OLD, ROUTE_NEW),
    ("report blocks stored", RESP_OLD, RESP_NEW),
]

DEFS = ("_ist_stamp", "_sleep_block_time", "parse_sleep_blocks",
        "_ensure_sleep_block_table", "cluster_sleep_blocks", "sleep_night",
        "store_sleep_blocks")


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
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    bad = ["  " + label + ": " + str(src.count(new)) + " (need 1)"
           for label, old, new in EDITS if src.count(new) != 1]
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    out = src
    for label, old, new in EDITS:
        out = out.replace(new, old, 1)
    if MARKER in out:
        print("REVERSE FAILED: marker still present, nothing written.")
        return 1
    for name in DEFS:
        if re.search(r"\ndef " + name + r"\(", out):
            print("REVERSE FAILED: " + name + " survived, nothing written.")
            return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed the pre-" + MARKER + " build -> " + out_path
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

    print("=" * 66)
    print("FitLog " + MARKER + ": the night is stored whole")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if "def parse_apple_records" not in src:
        print("FATAL: this file predates the record-level Apple ingest.")
        return 1

    problems = ["  " + label + ": found " + str(src.count(old))
                + " times, need 1"
                for label, old, new in EDITS if src.count(old) != 1]
    print("anchors: " + str(len(EDITS) - len(problems)) + "/"
          + str(len(EDITS)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        return 1
    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, old, new in EDITS:
        out = out.replace(old, new, 1)
    for name in DEFS:
        if not re.search(r"\ndef " + name + r"\(", out):
            print("DEFINITION CHECK FAILED: " + name)
            return 2

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
    bak = args.file + ".bak-sleepp1-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(EDITS)) + " edits")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

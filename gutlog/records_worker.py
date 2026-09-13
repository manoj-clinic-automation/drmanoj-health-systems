#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
records_worker.py -- GutLog v3.10.0: reports read automatically.

Every report you scan or upload in GutLog is read by Sarvam Document
Intelligence (the same service the clinic uses for bills), then filed into
the record without any step from you:

  * the report joins Records -> Reports, dated from the report itself;
  * every result row joins Trends, value exactly as the machine read it,
    matched to the tests already in your record where the name is the same
    test (Hb = Haemoglobin, ESR, sodium, calprotectin, ...);
  * a plan item whose test has now been done is ticked;
  * the inbox copy is removed.

Machine-read reports are marked "machine-read" until you tap "Looks right"
-- nothing is hidden, nothing is presented as checked when it is not. A
report whose printed patient name does not match yours is filed as a
document only, with its values held back, and flagged.

Runs on each upload (GutLog starts it) and every 15 minutes by cron. One run
at a time. Up to 3 attempts per file. The Sarvam key is read from the
environment or, in place, from /root/wa/.env; it is never printed.
Prints counts only. Python 3.9.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("GUTLOG_DB", os.path.join(HERE, "health3.db"))
UPLOADS = os.environ.get("GUTLOG_UPLOADS", os.path.join(HERE, "uploads"))
PROFILE = os.environ.get("GUTLOG_PROFILE", os.path.join(HERE, "records_profile.local.json"))
ENV_FILE = os.environ.get("GUTLOG_SARVAM_ENV", "/root/wa/.env")
MAX_TRIES = 3
READ_EXT = (".pdf", ".jpg", ".jpeg", ".png")

LAB_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_name": {"type": "string", "description": "patient name exactly as printed"},
        "report_date": {"type": "string", "description": "sample collection date, or else the report date, as printed"},
        "laboratory": {"type": "string", "description": "laboratory, hospital or centre that issued the report"},
        "document_type": {"type": "string", "description": "one of: blood test, urine test, stool test, imaging, "
                          "endoscopy, histopathology, cardiology, prescription, consultation note, discharge summary, other"},
        "title": {"type": "string", "description": "short title, e.g. Complete blood count, USG abdomen, Lipid profile"},
        "results": {
            "type": "array", "description": "every test result row in the report",
            # The reader rejects the whole schema with SCHEMA_INVALID unless the
            # array's item object carries a description of its own -- the
            # descriptions on the properties inside it are not enough. That one
            # missing line is why every PDF came back "reader error".
            "items": {"type": "object", "description": "one test result row",
                      "properties": {
                "test": {"type": "string", "description": "test or parameter name exactly as printed"},
                "value": {"type": "string", "description": "result exactly as printed, including < or > signs"},
                "unit": {"type": "string", "description": "unit as printed"},
                "reference_range": {"type": "string", "description": "reference or normal range as printed"},
                "flag": {"type": "string", "description": "H, L, * or any high/low mark printed by the value, else empty"}}}},
        "impression": {"type": "string", "description": "impression, conclusion or opinion text, verbatim"},
    },
}

KIND = [("blood", "Blood"), ("urine", "Urine"), ("stool", "Stool"), ("faec", "Stool"), ("imaging", "Imaging"),
        ("ultrasound", "Imaging"), ("usg", "Imaging"), ("ct", "Imaging"), ("mri", "Imaging"), ("x-ray", "Imaging"),
        ("endoscop", "Endoscopy"), ("colonoscop", "Endoscopy"), ("histopath", "Endoscopy"), ("biopsy", "Endoscopy"),
        ("cardio", "Cardiology"), ("ecg", "Cardiology"), ("echo", "Cardiology"), ("holter", "Cardiology"),
        ("prescription", "Prescription"), ("consult", "Consultation"), ("discharge", "Consultation")]

# printed name (normalised) -> the name already used in the record
ALIASES = {
    "hb": "Haemoglobin", "haemoglobin": "Haemoglobin", "hemoglobin": "Haemoglobin",
    "plateletcount": "Platelet Count", "platelets": "Platelet Count", "plt": "Platelet Count",
    "esr": "ESR (Erythrocyte Sedimentation Rate)", "erythrocytesedimentationrate": "ESR (Erythrocyte Sedimentation Rate)",
    "esrwestergren": "ESR (Erythrocyte Sedimentation Rate)",
    "crp": "C-Reactive Protein (Quantitative)", "creactiveprotein": "C-Reactive Protein (Quantitative)",
    "crpquantitative": "C-Reactive Protein (Quantitative)",
    "sodium": "Serum Sodium", "serumsodium": "Serum Sodium", "na": "Serum Sodium",
    "potassium": "Serum Potassium", "serumpotassium": "Serum Potassium", "k": "Serum Potassium",
    "ioniccalcium": "Serum Ionic Calcium", "ionisedcalcium": "Serum Ionic Calcium", "ionizedcalcium": "Serum Ionic Calcium",
    "serumioniccalcium": "Serum Ionic Calcium", "serumionisedcalcium": "Serum Ionic Calcium",
    "serumionizedcalcium": "Serum Ionic Calcium",
    "calcium": "Serum Calcium (total)", "serumcalcium": "Serum Calcium (total)", "totalcalcium": "Serum Calcium (total)",
    "magnesium": "Serum Magnesium", "serummagnesium": "Serum Magnesium",
    "creatinine": "Serum Creatinine", "serumcreatinine": "Serum Creatinine", "urea": "Blood Urea", "bloodurea": "Blood Urea",
    "hba1c": "HbA1c (Glycosylated Haemoglobin)", "glycosylatedhaemoglobin": "HbA1c (Glycosylated Haemoglobin)",
    "glycatedhaemoglobin": "HbA1c (Glycosylated Haemoglobin)", "glycatedhemoglobin": "HbA1c (Glycosylated Haemoglobin)",
    "sgot": "SGOT (AST)", "ast": "SGOT (AST)", "sgotast": "SGOT (AST)", "sgpt": "SGPT (ALT)", "alt": "SGPT (ALT)",
    "sgptalt": "SGPT (ALT)", "mpv": "MPV", "meanplateletvolume": "MPV",
    "tlc": "Total Leucocyte Count (TLC)", "totalleucocytecount": "Total Leucocyte Count (TLC)",
    "wbc": "Total Leucocyte Count (TLC)", "totalwbccount": "Total Leucocyte Count (TLC)",
    "vitaminb12": "Vitamin B12 (Total)", "b12": "Vitamin B12 (Total)",
    "vitamind": "25-Hydroxy Vitamin D", "25ohvitamind": "25-Hydroxy Vitamin D", "25hydroxyvitamind": "25-Hydroxy Vitamin D",
    "tsh": "TSH (ultrasensitive)", "totalcholesterol": "Total Cholesterol", "cholesterol": "Total Cholesterol",
    "ldl": "LDL Cholesterol", "ldlcholesterol": "LDL Cholesterol", "hdl": "HDL Cholesterol", "hdlcholesterol": "HDL Cholesterol",
    "triglycerides": "Triglycerides", "tg": "Triglycerides",
    "faecalcalprotectin": "FAECAL CALPROTECTIN (stool)", "fecalcalprotectin": "FAECAL CALPROTECTIN (stool)",
    "calprotectin": "FAECAL CALPROTECTIN (stool)",
}

PLAN_KEYS = [(r"breath", r"breath|hydrogen|methane"), (r"manometry", r"manometry|balloon expulsion"),
             (r"calprotectin", r"calprotectin"), (r"coeliac|celiac", r"ttg|transglutaminase|hla.?dq|endomysial"),
             (r"smear|citrate", r"peripheral smear|citrate|general blood picture"), (r"pylori", r"pylori"),
             (r"fibroscan", r"fibroscan|elastograph|liver stiffness"), (r"holter", r"holter"),
             (r"pth|ionic calcium", r"\bpth\b|parathyroid"), (r"westergren", r"\besr\b|sedimentation")]

_NUM = re.compile(r"[-+]?\d*\.?\d+")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg, flush=True)


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def sarvam_key():
    k = os.environ.get("SARVAM_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("SARVAM_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def sarvam_extract(path, schema):
    """(dict, note). Replaced in tests. Never raises."""
    key = sarvam_key()
    if not key:
        return None, "no Sarvam key on this server"
    try:
        from sarvamai import SarvamAI
    except Exception:
        return None, "reader not installed (pip install sarvamai)"
    try:
        import mimetypes
        client = SarvamAI(api_subscription_key=key)
        with open(path, "rb") as fh:
            data = fh.read()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        job = client.doc_ai.extract(file=[(os.path.basename(path), data, mime)], schema=json.dumps(schema),
                                    language="en-IN", output_format="json")
        jid = getattr(job, "job_id", None) or (job.get("job_id") if isinstance(job, dict) else None)
        status, waited = getattr(job, "status", None), 0
        while status not in ("completed", "partially_completed"):
            if status in ("failed", "rejected"):
                return None, "reader rejected the file"
            if waited > 240:
                return None, "reader timed out"
            time.sleep(4)
            waited += 4
            status = getattr(client.doc_ai.get_status(job_id=jid), "status", None)
        res = client.doc_ai.get_results(job_id=jid)
        return coerce(res), ""
    except Exception as e:
        # Keep what the service actually said, not just the exception class --
        # a bare "BadRequestError" cost a round trip to the server to find out
        # that the schema, not the file, was the problem.
        b = getattr(e, "body", None)
        msg = ""
        if isinstance(b, dict):
            msg = str(b.get("detail") or b.get("message") or "")
        if not msg:
            msg = str(e).replace("\n", " ")
        return None, ("reader error (%s): %s" % (type(e).__name__, msg))[:300]


def coerce(res):
    """Find the extracted object in whatever shape the SDK returns."""
    for attr in ("result", "results", "data", "output"):
        v = getattr(res, attr, None)
        if v is not None:
            res = v
            break
    if isinstance(res, dict):
        for k in ("result", "results", "data", "output"):
            if k in res and not any(x in res for x in ("results", "patient_name")):
                res = res[k]
    if isinstance(res, (bytes, str)):
        try:
            res = json.loads(res)
        except ValueError:
            return None
    if isinstance(res, list):
        res = res[0] if res else None
        if isinstance(res, (str, bytes)):
            try:
                res = json.loads(res)
            except ValueError:
                return None
    return res if isinstance(res, dict) else None


_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_day(s):
    """Printed date -> ISO (day-first, as Indian reports print). '' if unsure or in the future."""
    s = str(s or "").strip().lower()
    out = None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        out = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not out:
        m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
        if m:
            y = int(m.group(3))
            out = (y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    if not out:
        m = re.search(r"(\d{1,2})[\s\-/]*([a-z]{3})[a-z]*[\s\-/,]*(\d{2,4})", s)
        if m and m.group(2) in _MON:
            y = int(m.group(3))
            out = (y + 2000 if y < 100 else y, _MON[m.group(2)], int(m.group(1)))
    try:
        d = date(*out) if out else None
    except ValueError:
        d = None
    if not d or d > date.today() or d.year < 1990:
        return ""
    return d.isoformat()


def kind_of(doc_type, title):
    t = (str(doc_type or "") + " " + str(title or "")).lower()
    for k, v in KIND:
        if re.search(r"\b" + re.escape(k) if len(k) <= 3 else re.escape(k), t):
            return v
    return "Other"


def outside(value, ref):
    v = _NUM.search(str(value or "").replace(",", ""))
    m = re.search(r"([-+]?\d*\.?\d+)\s*(?:-|–|to)\s*([-+]?\d*\.?\d+)", str(ref or ""))
    if not v or not m:
        return False
    try:
        x, lo, hi = float(v.group(0)), float(m.group(1)), float(m.group(2))
    except ValueError:
        return False
    return x < lo or x > hi


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_schema(con):
    con.executescript("""
CREATE TABLE IF NOT EXISTS rec_docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, kind TEXT, title TEXT, source TEXT,
  finding TEXT, stored TEXT DEFAULT '', orig TEXT DEFAULT '', sha TEXT UNIQUE, status TEXT DEFAULT 'filed', created TEXT);
CREATE TABLE IF NOT EXISTS rec_labs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, test TEXT, section TEXT, value TEXT, num REAL,
  unit TEXT, ref TEXT, flag INTEGER DEFAULT 0, lab TEXT, created TEXT, UNIQUE(day, test, lab));
CREATE TABLE IF NOT EXISTS rec_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pos INTEGER, test TEXT UNIQUE, why TEXT, timing TEXT,
  status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '');
""")
    for t, c, d in (("rec_docs", "origin", "TEXT DEFAULT ''"), ("rec_docs", "checked", "INTEGER DEFAULT 1"),
                    ("rec_docs", "file_id", "INTEGER"), ("rec_labs", "origin", "TEXT DEFAULT ''"),
                    ("rec_labs", "doc_id", "INTEGER"), ("files", "sha", "TEXT DEFAULT ''"),
                    ("files", "ocr_status", "TEXT DEFAULT ''"), ("files", "ocr_note", "TEXT DEFAULT ''"),
                    ("files", "ocr_tries", "INTEGER DEFAULT 0")):
        cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
        if cols and c not in cols:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (t, c, d))
    con.commit()


def patient_ok(name):
    if not str(name or "").strip():
        return True
    try:
        with open(PROFILE, encoding="utf-8") as fh:
            keys = json.load(fh).get("patient_match") or ["manoj"]
    except (OSError, ValueError):
        keys = ["manoj"]
    n = str(name).lower()
    return any(k.lower() in n for k in keys)


def file_one(con, f, data, known):
    """Put one read report into the record. Returns (doc_id, n_values, flag_note)."""
    now = datetime.now().isoformat(timespec="seconds")
    src = os.path.join(UPLOADS, f["stored"])
    h = f["sha"] or sha256(src)
    day = parse_day(data.get("report_date")) or f["day"] or date.today().isoformat()
    lab = str(data.get("laboratory") or "").strip()[:120]
    title = str(data.get("title") or "").strip()[:160] or (f["label"] or "Report")
    kind = kind_of(data.get("document_type"), title)
    results = [r for r in (data.get("results") or []) if isinstance(r, dict) and str(r.get("test") or "").strip()
               and str(r.get("value") or "").strip()]
    ok = patient_ok(data.get("patient_name"))
    flagged = []
    rows = []
    for r in results:
        printed = str(r["test"]).strip()[:120]
        name = ALIASES.get(norm(printed)) or known.get(norm(printed)) or printed
        value = str(r["value"]).strip()[:80]
        fl = 1 if (str(r.get("flag") or "").strip().lower() in ("h", "l", "high", "low", "*", "**", "↑", "↓")
                   or outside(value, r.get("reference_range"))) else 0
        if fl:
            flagged.append("%s %s" % (name.split(" (")[0], value))
        rows.append((name, value, str(r.get("unit") or "")[:40], str(r.get("reference_range") or "")[:60], fl))
    imp = re.sub(r"\s+", " ", str(data.get("impression") or "")).strip()
    if not ok:
        finding = "CHECK: the patient name read from this report (%s) does not look like yours - values held back." \
                  % str(data.get("patient_name"))[:60]
    elif imp:
        finding = imp[:600]
    elif flagged:
        finding = "Flagged: " + ", ".join(flagged[:8]) + ("" if len(flagged) <= 8 else " and %d more" % (len(flagged) - 8))
    else:
        finding = "%d results read; none flagged." % len(rows) if rows else "Read; no result table found."
    stored = "rec_" + h[:24] + os.path.splitext(f["stored"])[1]
    if not os.path.exists(os.path.join(UPLOADS, stored)):
        shutil.copy2(src, os.path.join(UPLOADS, stored))
    con.execute("INSERT OR IGNORE INTO rec_docs(day, kind, title, source, finding, stored, orig, sha, status, created, "
                "origin, checked, file_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (day, kind, title, lab, finding, stored, f["label"] or f["stored"], h, "check" if not ok else "filed",
                 now, "auto", 0, f["id"]))
    did = con.execute("SELECT id FROM rec_docs WHERE sha=?", (h,)).fetchone()[0]
    n = 0
    if ok:
        for name, value, unit, ref, fl in rows:
            m = _NUM.search(value.replace(",", ""))
            sec = known.get("__section__" + name, "READ AUTOMATICALLY")
            cur = con.execute("INSERT OR IGNORE INTO rec_labs(day, test, section, value, num, unit, ref, flag, lab, created, "
                              "origin, doc_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (day, name, sec, value, float(m.group(0)) if m else None, unit, ref, fl, lab or "(auto)",
                               now, "auto", did))
            n += cur.rowcount
    return did, n, ok, h


def tick_plan(con, day, names):
    text = " ".join(names).lower()
    ticked = 0
    for pid, test in con.execute("SELECT id, test FROM rec_plan WHERE status='planned'").fetchall():
        for plan_re, result_re in PLAN_KEYS:
            if re.search(plan_re, test.lower()) and re.search(result_re, text):
                con.execute("UPDATE rec_plan SET status='done', done_day=?, note=? WHERE id=?",
                            (day, "ticked automatically from a report read on " + date.today().isoformat(), pid))
                ticked += 1
                break
    return ticked


def run(con, reader=None):
    reader = reader or sarvam_extract
    ensure_schema(con)
    con.row_factory = sqlite3.Row
    done = set(r[0] for r in con.execute("SELECT sha FROM rec_docs WHERE sha IS NOT NULL"))
    known = {}
    for r in con.execute("SELECT DISTINCT test, section FROM rec_labs"):
        known[norm(r["test"])] = r["test"]
        known["__section__" + r["test"]] = r["section"]
    st = {"read": 0, "filed": 0, "values": 0, "held": 0, "failed": 0, "ticked": 0, "waiting": 0}
    rows = con.execute("SELECT * FROM files WHERE COALESCE(ocr_status,'') IN ('', 'retry') "
                       "AND COALESCE(ocr_tries,0) < ? ORDER BY id", (MAX_TRIES,)).fetchall()
    for f in rows:
        if f["sha"] and f["sha"] in done:
            con.execute("UPDATE files SET ocr_status='done', ocr_note='already in the record' WHERE id=?", (f["id"],))
            continue
        path = os.path.join(UPLOADS, f["stored"] or "")
        if not f["stored"] or not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in READ_EXT:
            con.execute("UPDATE files SET ocr_status='skipped', ocr_note='not a PDF or image' WHERE id=?", (f["id"],))
            continue
        con.execute("UPDATE files SET ocr_status='reading', ocr_tries=COALESCE(ocr_tries,0)+1 WHERE id=?", (f["id"],))
        con.commit()
        data, note = reader(path, LAB_SCHEMA)
        st["read"] += 1
        if not data:
            tries = (f["ocr_tries"] or 0) + 1
            con.execute("UPDATE files SET ocr_status=?, ocr_note=? WHERE id=?",
                        ("failed" if tries >= MAX_TRIES else "retry", note or "nothing read", f["id"]))
            st["failed"] += 1
            con.commit()
            continue
        did, n, ok, h = file_one(con, f, data, known)
        st["filed"] += 1
        st["values"] += n
        st["held"] += 0 if ok else 1
        if ok:
            names = [str(r.get("test") or "") for r in (data.get("results") or []) if isinstance(r, dict)]
            names.append(str(data.get("title") or ""))
            st["ticked"] += tick_plan(con, parse_day(data.get("report_date")) or f["day"], names)
        con.execute("UPDATE files SET ocr_status='done', ocr_note=? WHERE id=?",
                    ("filed" if ok else "filed - patient name to check", f["id"]))
        con.commit()
        done.add(h)
    inbox = os.path.join(UPLOADS, "inbox")
    if os.path.isdir(inbox):
        for n_ in os.listdir(inbox):
            fp = os.path.join(inbox, n_)
            try:
                if os.path.isfile(fp) and sha256(fp) in done:
                    os.remove(fp)
            except OSError:
                pass
    st["waiting"] = con.execute("SELECT COUNT(*) FROM files WHERE ocr_status IN ('retry','reading','')").fetchone()[0]
    con.commit()
    return st


def probe():
    """Is automatic reading ready on this server? No API call, no cost."""
    ok_key = bool(sarvam_key())
    try:
        import sarvamai  # noqa: F401
        ok_sdk = True
    except Exception:
        ok_sdk = False
    print("automatic reading: Sarvam key %s, reader library %s" % ("found" if ok_key else "MISSING",
                                                                   "installed" if ok_sdk else "MISSING"))
    return 0 if (ok_key and ok_sdk) else 1


def main():
    if "--probe" in sys.argv:
        return probe()
    lock = open(os.path.join(HERE, ".records_worker.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    con = sqlite3.connect(DB, timeout=30)
    try:
        st = run(con)
    finally:
        con.close()
    if st["read"] or "--verbose" in sys.argv:
        log("reports read %(read)d, filed %(filed)d, values %(values)d, held for name check %(held)d, "
            "not readable %(failed)d, plan items ticked %(ticked)d, still waiting %(waiting)d" % st)
    return 0


if __name__ == "__main__":
    sys.exit(main())

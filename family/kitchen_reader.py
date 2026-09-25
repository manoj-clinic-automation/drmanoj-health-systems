#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kitchen_reader.py -- turn a captured recipe into a DRAFT a person can check.

FAMILY_EDITION_V1 (Phase C). Cron, as root, every 5 minutes, with the Python
that has the Sarvam reader (/root/gutlog/venv/bin/python):

    python kitchen_reader.py [--db /srv/family/kitchen/kitchen.db] [--once ID]

For each draft still 'new':
  * a web page: the page's own structured recipe (schema.org Recipe in
    JSON-LD) when it has one -- no model needed; otherwise the page text;
  * YouTube: the video's description and, when published, its captions;
  * Instagram / Facebook: not fetched (they block); the link is kept and the
    draft asks for a screenshot;
  * a photo or a PDF (handwritten or printed, English or Hindi): read by the
    SAME Sarvam document reader the owner's scanner uses, with a recipe
    schema -- English first, Hindi if English finds no ingredients;
  * text (pasted, or from a page or a video): structured by ONE model call.
    This is capture, not analysis: it only lays the words out as name /
    ingredients / quantities / method / servings. Nothing it produces reaches
    nutrition, personal adjustments or RxGuard -- the draft sits in the
    Recipe Inbox until a person confirms it there.

Uncertain items are listed, never smoothed: words the reader could not make
out, ingredients with no quantity, a servings figure that was guessed. Keys
are read in place (environment, else KITCHEN_KEYS_ENV, default /root/wa/.env,
the file the records worker already reads) and never printed or stored.
Python 3.9.
"""
import argparse
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

DB = os.environ.get("KITCHEN_DB", "/srv/family/kitchen/kitchen.db")
ATTACH = os.environ.get("KITCHEN_ATTACH", "/srv/family/kitchen/attach")
KEYS_ENV = os.environ.get("KITCHEN_KEYS_ENV", "/root/wa/.env")
MODEL = os.environ.get("KITCHEN_MODEL", "claude-opus-5")
MAX_TRIES = 3
PAGE_MAX = 2 * 1024 * 1024
UA = "Mozilla/5.0 (FamilyKitchen recipe reader)"
BLOCKED_HOSTS = ("instagram.com", "facebook.com", "fb.watch", "threads.net")

RECIPE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "servings", "ingredients", "method", "unclear"],
    "properties": {
        "name": {"type": "string"},
        "servings": {"type": ["number", "null"]},
        "servings_guessed": {"type": "boolean"},
        "ingredients": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["item", "qty", "unit", "text"],
            "properties": {"item": {"type": "string"}, "qty": {"type": ["number", "null"]},
                           "unit": {"type": "string"}, "text": {"type": "string"}}}},
        "method": {"type": "array", "items": {"type": "string"}},
        "unclear": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = ("You are laying out a family recipe that someone shared, so a person can check it and save it. "
          "The text below may be English, Hindi, or a mix, and may come from a web page, a video caption or "
          "a photo that was read by machine. Return the recipe as JSON matching the schema.\n"
          "- name: the dish name, in English where there is a common English or Hinglish name (e.g. 'Moong dal "
          "cheela').\n- ingredients: one entry per ingredient; item in English (a common Indian name is fine: "
          "'hing', 'besan'); qty as a number when the text gives one (1/2 -> 0.5), else null; unit as written "
          "and normalised to one of g, kg, ml, l, katori, cup, tbsp, tsp, pinch, piece, small, medium, large, "
          "bunch, or '' when there is none; text: the ingredient line as written.\n"
          "- method: the steps, in order, in plain English.\n- servings: a number if stated; if you infer it, "
          "set servings_guessed true.\n- unclear: every word, amount or step you could not read or had to "
          "guess, quoted as it appeared. Never invent a quantity -- leave qty null and say so in unclear.\n"
          "If the text is not a recipe, return name '' and empty lists.\n\nTEXT:\n")


def now_s():
    return datetime.now().isoformat(timespec="seconds")


def read_key(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        with open(KEYS_ENV, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------ fetching
def fetch(url, limit=PAGE_MAX):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read(limit + 1)
        ctype = r.headers.get("Content-Type", "")
    if len(data) > limit:
        data = data[:limit]
    m = re.search(r"charset=([\w-]+)", ctype)
    return data.decode(m.group(1) if m else "utf-8", "replace")


def _ld_objects(page):
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            j = json.loads(html.unescape(m.group(1).strip()))
        except ValueError:
            continue
        stack = [j]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                t = x.get("@type")
                if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
                    out.append(x)
                for k in ("@graph", "mainEntity", "itemListElement"):
                    if k in x:
                        stack.append(x[k])
    return out


QTY_RX = re.compile(r"^\s*((?:\d+\s+)?\d+(?:[./]\d+)?|[½¼¾⅓⅔])\s*([a-zA-Z]+\.?)?\s+(.*)$")
FRAC = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 0.333, "⅔": 0.667}
UNITS = {"g": "g", "gm": "g", "gms": "g", "grams": "g", "gram": "g", "kg": "kg", "ml": "ml", "l": "l",
         "litre": "l", "liter": "l", "cup": "cup", "cups": "cup", "tbsp": "tbsp", "tablespoon": "tbsp",
         "tablespoons": "tbsp", "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "katori": "katori",
         "pinch": "pinch", "piece": "piece", "pieces": "piece", "small": "small", "medium": "medium",
         "large": "large", "bunch": "bunch"}


def parse_line(line):
    s = html.unescape(re.sub(r"<[^>]+>", "", str(line))).strip()
    m = QTY_RX.match(s)
    if not m:
        return {"item": s[:80], "qty": None, "unit": "", "text": s}
    q = m.group(1).strip()
    try:
        if q in FRAC:
            qty = FRAC[q]
        elif " " in q:
            a, b = q.split(None, 1)
            n, d = b.split("/")
            qty = float(a) + float(n) / float(d)
        elif "/" in q:
            n, d = q.split("/")
            qty = float(n) / float(d)
        else:
            qty = float(q)
    except (ValueError, ZeroDivisionError):
        qty = None
    unit = UNITS.get((m.group(2) or "").lower().rstrip("."), "")
    item = m.group(3) if unit or not m.group(2) else (m.group(2) + " " + m.group(3))
    return {"item": item.strip()[:80], "qty": qty, "unit": unit, "text": s}


def from_ld(r):
    steps = []
    for x in r.get("recipeInstructions") or []:
        if isinstance(x, str):
            steps.append(x)
        elif isinstance(x, dict):
            if x.get("itemListElement"):
                steps += [y.get("text", "") for y in x["itemListElement"] if isinstance(y, dict)]
            else:
                steps.append(x.get("text") or x.get("name") or "")
    if isinstance(r.get("recipeInstructions"), str):
        steps = [s for s in re.split(r"\n+|(?<=\.)\s+(?=[A-Z])", r["recipeInstructions"]) if s.strip()]
    y = r.get("recipeYield")
    y = y[0] if isinstance(y, list) and y else y
    m = re.search(r"\d+", str(y or ""))
    return {"name": html.unescape(str(r.get("name") or "")).strip(),
            "servings": float(m.group(0)) if m else None, "servings_guessed": not m,
            "ingredients": [parse_line(x) for x in r.get("recipeIngredient") or []],
            "method": [html.unescape(re.sub(r"<[^>]+>", "", s)).strip() for s in steps if s and s.strip()],
            "unclear": []}


def page_text(page):
    page = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(x.strip() for x in t.split("\n") if len(x.strip()) > 2)
    return t[:30000]


def youtube_text(url, page):
    out = []
    m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\});", page)
    if m:
        try:
            pr = json.loads(m.group(1))
        except ValueError:
            pr = {}
        vd = pr.get("videoDetails") or {}
        out += [vd.get("title", ""), vd.get("shortDescription", "")]
        tracks = (((pr.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {})
                  .get("captionTracks") or [])
        if tracks:
            try:
                xml = fetch(tracks[0]["baseUrl"])
                out.append(html.unescape(re.sub(r"<[^>]+>", " ", xml)))
            except Exception:
                pass
    return "\n".join(x for x in out if x)[:30000]


# ------------------------------------------------------------------ readers
def llm_structure(text):
    """(extracted, note). One Messages API call with a JSON schema for the
    output. Raw HTTPS: the server's Python is 3.9 and nothing new is installed."""
    key = read_key("ANTHROPIC_API_KEY")
    if not key:
        return None, "no model key on this server -- fill the fields by hand"
    body = {"model": MODEL, "max_tokens": 16000, "fallbacks": "default",
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": RECIPE_SCHEMA}},
            "messages": [{"role": "user", "content": PROMPT + text[:30000]}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "anthropic-beta": "server-side-fallback-2026-07-01",
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, "model error %d" % e.code
    except Exception as e:
        return None, "model not reachable (%s)" % type(e).__name__
    if res.get("stop_reason") == "refusal":
        return None, "the model declined -- fill the fields by hand"
    txt = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    try:
        return json.loads(txt), ""
    except ValueError:
        return None, "the model's answer was not usable -- fill the fields by hand"


SARVAM_SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string", "description": "the dish name"},
    "servings": {"type": "string", "description": "how many it serves, as written"},
    "ingredients": {"type": "array", "items": {"type": "string"},
                    "description": "each ingredient line exactly as written, with its amount"},
    "method": {"type": "array", "items": {"type": "string"}, "description": "each step, in order"},
    "unclear": {"type": "array", "items": {"type": "string"},
                "description": "words or amounts that could not be read"}}}


def sarvam_read(path):
    """(extracted, note) from a photo or a PDF, with the same Sarvam document
    reader and calls the owner's records worker uses."""
    key = read_key("SARVAM_API_KEY")
    if not key:
        return None, "no document-reader key on this server"
    try:
        from sarvamai import SarvamAI
    except Exception:
        return None, "document reader not installed"
    import mimetypes
    client = SarvamAI(api_subscription_key=key)
    with open(path, "rb") as fh:
        data = fh.read()
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    last = ""
    for lang in ("en-IN", "hi-IN"):
        try:
            job = client.doc_ai.extract(file=[(os.path.basename(path), data, mime)],
                                        schema=json.dumps(SARVAM_SCHEMA), language=lang, output_format="json")
            jid = getattr(job, "job_id", None) or (job.get("job_id") if isinstance(job, dict) else None)
            status, waited = getattr(job, "status", None), 0
            while status not in ("completed", "partially_completed"):
                if status in ("failed", "rejected") or waited > 240:
                    raise RuntimeError("reader %s" % (status or "timed out"))
                time.sleep(4)
                waited += 4
                status = getattr(client.doc_ai.get_status(job_id=jid), "status", None)
            res = coerce(client.doc_ai.get_results(job_id=jid))
        except Exception as e:
            last = "reader error (%s)" % type(e).__name__
            continue
        if res and res.get("ingredients"):
            m = re.search(r"\d+", str(res.get("servings") or ""))
            return {"name": str(res.get("name") or "").strip(),
                    "servings": float(m.group(0)) if m else None, "servings_guessed": not m,
                    "ingredients": [parse_line(x) for x in res.get("ingredients") or []],
                    "method": [str(x).strip() for x in res.get("method") or [] if str(x).strip()],
                    "unclear": [str(x) for x in res.get("unclear") or []]}, ("read in %s" % lang)
        last = "nothing readable as a recipe"
    return None, last


def coerce(res):
    for attr in ("result", "results", "data", "output"):
        v = getattr(res, attr, None)
        if v is not None:
            res = v
            break
    if isinstance(res, dict):
        for k in ("result", "results", "data", "output"):
            if k in res and "ingredients" not in res:
                res = res[k]
    if isinstance(res, (bytes, str)):
        try:
            res = json.loads(res)
        except ValueError:
            return None
    if isinstance(res, list):
        res = res[0] if res else None
        if isinstance(res, (bytes, str)):
            try:
                res = json.loads(res)
            except ValueError:
                return None
    return res if isinstance(res, dict) else None


# ------------------------------------------------------------------ flags
def flags_for(ex, note=""):
    """What a person must look at before confirming. Stated, never fixed."""
    f = []
    if not ex:
        return [{"kind": "manual", "text": note or "Could not be read -- fill the fields by hand."}]
    if not (ex.get("name") or "").strip():
        f.append({"kind": "name", "text": "No dish name found."})
    for i in ex.get("ingredients") or []:
        if i.get("qty") is None and i.get("unit") not in ("pinch",):
            f.append({"kind": "quantity", "text": "No amount for %s." % i.get("item", "an ingredient")})
    for u in ex.get("unclear") or []:
        f.append({"kind": "illegible", "text": "Could not read: %s" % u})
    if ex.get("servings_guessed") or not ex.get("servings"):
        f.append({"kind": "servings", "text": "Servings not stated -- check it."})
    if not ex.get("method"):
        f.append({"kind": "method", "text": "No method found."})
    return f


def read_draft(r):
    """(extracted, flags, note) for one draft row."""
    if r["attach"] and r["kind"] in ("image", "pdf"):
        ex, note = sarvam_read(os.path.join(ATTACH, r["attach"]))
        return ex, flags_for(ex, note), note
    text = r["text"] or ""
    if r["url"]:
        host = urllib.parse.urlparse(r["url"]).netloc.lower()
        if any(host.endswith(h) for h in BLOCKED_HOSTS):
            n = "This site does not let a reader in. The link is kept -- please share a screenshot of the recipe."
            return None, [{"kind": "screenshot", "text": n}], n
        try:
            page = fetch(r["url"])
        except Exception as e:
            n = "The link could not be opened (%s)." % type(e).__name__
            return None, flags_for(None, n), n
        ld = _ld_objects(page)
        if ld:
            ex = from_ld(ld[0])
            return ex, flags_for(ex), "from the page's own recipe data"
        if "youtube.com" in host or "youtu.be" in host:
            text = (text + "\n" + youtube_text(r["url"], page)).strip()
        else:
            text = (text + "\n" + page_text(page)).strip()
    if not text.strip():
        return None, flags_for(None, "Nothing to read."), "nothing to read"
    ex, note = llm_structure(text)
    return ex, flags_for(ex, note), note or "laid out by the model -- check every line"


def run(db_path, only=None):
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM drafts WHERE status='new' AND tries < ?"
    args = [MAX_TRIES]
    if only:
        q += " AND id=?"
        args.append(only)
    rows = con.execute(q, args).fetchall()
    for r in rows:
        con.execute("UPDATE drafts SET status='reading', tries=tries+1, updated=? WHERE id=?", (now_s(), r["id"]))
        con.commit()
        try:
            ex, flags, note = read_draft(r)
        except Exception as e:
            ex, flags, note = None, flags_for(None, "Reader failed (%s)." % type(e).__name__), "failed"
        status = "ready" if ex else ("new" if "not reachable" in (note or "") and r["tries"] + 1 < MAX_TRIES
                                     else "manual")
        con.execute("UPDATE drafts SET status=?, extracted=?, flags=?, note=?, updated=? WHERE id=?",
                    (status, json.dumps(ex) if ex else "", json.dumps(flags), (note or "")[:200], now_s(), r["id"]))
        con.commit()
        print("%s draft %d (%s): %s -- %s" % (now_s(), r["id"], r["kind"], status, note))
    con.close()
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--once", type=int, default=None)
    a = ap.parse_args()
    if not os.path.exists(a.db):
        return 0
    run(a.db, a.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())

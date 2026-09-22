#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.27.2 -> v3.28.0  ::  GUTLOG_V3280_PLANS -- the Plans page

A dated plan document lives in the app, so it opens on the phone and goes
into the system share sheet. This first version holds and shares PDFs; the
tables are shaped for what comes next (dated regimen steps, checkpoints,
several files per plan).

  * /plans, linked from the header beside Account. Newest first, each row
    with its title, "First considered: DD-Mon-YYYY", a status badge and
    Open / Share.
  * The file is served inline, login required, Cache-Control: no-store.
    There is NO unauthenticated route to it and no share link -- Share
    hands the BYTES to the system sheet, it does not publish a URL, so
    there is nothing to leak and nothing to revoke.
  * Uploads are PDF only, checked by the %PDF- magic bytes rather than the
    extension, 20 MB, stored under a sha256 name. The uploaded name is
    kept for the download filename and nothing else.
  * Add / edit title and status / archive. No delete: strays are archived.

Three notes on choices that differ from the brief, all deliberate:

  1. The tables go in SCHEMA, not _migrate(). db() runs executescript(SCHEMA)
     on every connection, so CREATE TABLE IF NOT EXISTS there is applied
     unconditionally. _migrate() returns early whenever schema_version is
     already current, so a table added there appears only if the version
     constant is bumped too -- one more thing to get right, for no gain.
  2. The date field is three plain lists (day / month / year), not an
     input[type=date]. v3.27.0's MutationObserver converts input[type=time]
     ONLY. Widening it to dates would change every date box in the app -- a
     page-wide regression for one new form. The rule that matters is "no
     native picker on the Fold", and three lists honour it without touching
     anything else.
  3. Rows are written with explicit SQL rather than the insert() helper:
     that helper appends a column called `created` and returns no id, and
     this schema uses created_at / uploaded_at and needs lastrowid.

The list is rendered SERVER-SIDE into the page, and the JS only acts on it.
The first draft built the list in the browser from /api/plans, and the suite
caught it immediately: fetching /plans returned a shell with no title and no
buttons in it. That is the v3.4.0 lesson in miniature -- server suites never
run page JS, so a list only JS can draw is a list no server test can see, and
it would also be a page that shows nothing if the script fails.

Anchor-verified, idempotent, compile-checked, .bak before write,
self-restoring, --reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3280_PLANS"
PREV = "GUTLOG_V3272_HEALTHZ"
VERSION = "3.28.0"

# ---------------------------------------------------------------- 1. header
HEAD_OLD = 'Run:  gunicorn -w 2 -b 127.0.0.1:8020 app:app\n'
HEAD_NEW = ('Run:  gunicorn -w 2 -b 127.0.0.1:8020 app:app\n'
            'GUTLOG_V3280_PLANS -- /plans holds and shares a dated plan document.\n')

# --------------------------------------------------------------- 2. version
VER_OLD = ('APP_VERSION = "3.27.2"   # GUTLOG_V3272_HEALTHZ GUTLOG_V3271_TARGETJS '
           'GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS\n')
VER_NEW = ('APP_VERSION = "3.28.0"   # GUTLOG_V3280_PLANS GUTLOG_V3272_HEALTHZ '
           'GUTLOG_V3271_TARGETJS GUTLOG_V3270_TIMEPICK GUTLOG_V3260_TRIALS\n')

# ------------------------------------------------------------- 3. constants
DIR_OLD = ('os.makedirs(UPLOAD_DIR, exist_ok=True)\n'
           'ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}\n')
DIR_NEW = ('os.makedirs(UPLOAD_DIR, exist_ok=True)\n'
           '# GUTLOG_V3280_PLANS -- plan documents sit beside the live database and\n'
           '# never in the repository. They are the health record (CLAUDE.md 5d).\n'
           'PLANS_DIR = os.environ.get("GUTLOG_PLANS", os.path.join(BASE, "plans_files"))\n'
           'os.makedirs(PLANS_DIR, exist_ok=True)\n'
           'PLAN_MAX_MB = 20\n'
           'PLAN_STATUSES = ("Draft", "Active", "Closed")\n'
           'ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}\n')

# ---------------------------------------------------------------- 4. schema
SCH_OLD = '''  status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '');
"""
'''
SCH_NEW = '''  status TEXT DEFAULT 'planned', done_day TEXT DEFAULT '', note TEXT DEFAULT '');
-- GUTLOG_V3280_PLANS. started_on and notes are unused in v3.28.0 and are here
-- because the next version dates the regimen steps a plan asks for. plan_files
-- is a table rather than columns on plans for the same reason: revised versions
-- of a document, newest first, which is what the index is for.
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  first_considered TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Draft',
  started_on TEXT, notes TEXT, archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS plan_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL,
  stored_name TEXT NOT NULL, original_name TEXT NOT NULL,
  bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, uploaded_at TEXT);
CREATE INDEX IF NOT EXISTS ix_plan_files_plan ON plan_files(plan_id, id DESC);
"""
'''

# ------------------------------------------ 5. the page constant + the code
PLANS_BLOCK = r'''# ------------------------------------------------------------------ plans
# GUTLOG_V3280_PLANS. A plan is a dated document he can open and share. The
# file never leaves the login: Share hands the BYTES to the system sheet, so
# there is no link to leak and nothing to revoke.
#
# The page below is a standalone string with __TOKENS__ replaced, returned as
# a Response, exactly like /scan. It is NOT rendered through Jinja, so the CSS
# and JS braces in it are safe -- CLAUDE.md 5b is about the main template.
import hashlib as _plan_hashlib

_PLAN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def plan_dmy(iso):
    """2026-09-22 -> 22-Sep-2026. Anything unparseable comes back unchanged."""
    try:
        y, m, d = str(iso).split("-")
        return "%02d-%s-%s" % (int(d), _PLAN_MONTHS[int(m) - 1], y)
    except (ValueError, IndexError, TypeError):
        return str(iso or "")


def plan_rows(include_archived=False):
    sql = ("SELECT p.*, f.id AS file_id, f.original_name, f.bytes "
           "FROM plans p LEFT JOIN plan_files f ON f.id = ("
           "SELECT id FROM plan_files WHERE plan_id = p.id ORDER BY id DESC LIMIT 1) ")
    if not include_archived:
        sql += "WHERE p.archived = 0 "
    sql += "ORDER BY p.first_considered DESC, p.id DESC"
    out = []
    for r in db().execute(sql).fetchall():
        d = dict(r)
        d["first_considered_text"] = plan_dmy(d.get("first_considered"))
        d["has_file"] = bool(d.get("file_id"))
        out.append(d)
    return out


def plan_esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def plan_list_html(rows):
    """The list, rendered on the server. See the patcher docstring: a list
    only the browser can draw is a list no server suite can check."""
    if not rows:
        return '<p class="empty">No plans yet.</p>'
    out = []
    for p in rows:
        pid = int(p["id"])
        out.append('<div class="card">')
        out.append('<p class="ttl">' + plan_esc(p["title"])
                   + '<span class="badge">' + plan_esc(p["status"]) + '</span></p>')
        out.append('<p class="sub">First considered: '
                   + plan_esc(p["first_considered_text"]) + '</p>')
        out.append('<div class="row">')
        if p["has_file"]:
            out.append('<a class="btn go" href="/plan/%d/file" target="_blank" '
                       'rel="noopener">Open</a>' % pid)
            out.append('<button type="button" class="shr" data-id="%d" data-name="%s" '
                       'data-title="%s">Share</button>'
                       % (pid, plan_esc(p.get("original_name") or ""),
                          plan_esc(p["title"])))
        else:
            out.append('<span class="empty">No file on this plan.</span>')
        opts = []
        for s in PLAN_STATUSES:
            sel = " selected" if s == p["status"] else ""
            opts.append("<option" + sel + ">" + s + "</option>")
        out.append('<select class="stsel" aria-label="Status" data-id="%d">%s</select>'
                   % (pid, "".join(opts)))
        out.append('<button type="button" class="rnm" data-id="%d" data-title="%s">'
                   'Rename</button>' % (pid, plan_esc(p["title"])))
        out.append('<button type="button" class="arc" data-id="%d">Archive</button>' % pid)
        out.append('</div></div>')
    return "".join(out)


def plan_is_pdf(fs):
    """The magic bytes, not the extension. Leaves the stream where it found it."""
    if not fs or not fs.filename:
        return False
    pos = fs.stream.tell()
    head = fs.stream.read(5)
    fs.stream.seek(pos)
    return head == b"%PDF-"


def plan_store(plan_id, fs):
    """Save an uploaded PDF against a plan. Returns (ok, message_or_name)."""
    if not plan_is_pdf(fs):
        return False, "That is not a PDF. Only PDF files can be added here."
    cap = PLAN_MAX_MB * 1024 * 1024
    raw = fs.stream.read(cap + 1)
    if len(raw) > cap:
        return False, "That file is larger than %d MB." % PLAN_MAX_MB
    sha = _plan_hashlib.sha256(raw).hexdigest()
    stored = sha + ".pdf"
    path = os.path.join(PLANS_DIR, stored)
    if not os.path.exists(path):
        tmp = path + ".part"
        fh = open(tmp, "wb")
        try:
            fh.write(raw)
        finally:
            fh.close()
        os.replace(tmp, path)
    db().execute("INSERT INTO plan_files(plan_id, stored_name, original_name, "
                 "bytes, sha256, uploaded_at) VALUES(?,?,?,?,?,?)",
                 (plan_id, stored, secure_filename(fs.filename)[:160] or "plan.pdf",
                  len(raw), sha, now_s()))
    db().commit()
    return True, stored


@app.route("/plans")
@login_required
def plans_page():
    arch = request.args.get("archived") == "1"
    html = (PLANS_PAGE.replace("__ROWS__", plan_list_html(plan_rows(include_archived=arch)))
            .replace("__TODAY__", today()))
    return Response(html, mimetype="text/html")


@app.route("/api/plans")
@login_required
def api_plans():
    arch = request.args.get("archived") == "1"
    rows = []
    for d in plan_rows(include_archived=arch):
        rows.append(dict(id=d["id"], title=d["title"], status=d["status"],
                         first_considered=d["first_considered"],
                         first_considered_text=d["first_considered_text"],
                         archived=d["archived"], has_file=d["has_file"],
                         original_name=d.get("original_name") or "",
                         bytes=d.get("bytes") or 0))
    return jsonify(rows)


@app.route("/api/plans", methods=["POST"])
@login_required
def api_plans_add():
    title = (request.form.get("title") or "").strip()
    when = (request.form.get("first_considered") or "").strip() or today()
    status = (request.form.get("status") or "Draft").strip()
    if not title:
        return jsonify(ok=False, err="Give the plan a title."), 400
    if status not in PLAN_STATUSES:
        return jsonify(ok=False, err="Unknown status."), 400
    try:
        datetime.strptime(when, "%Y-%m-%d")
    except ValueError:
        return jsonify(ok=False, err="That date is not a real date."), 400
    fs = request.files.get("file")
    # Refused BEFORE the row is written, so a rejected upload leaves nothing.
    if fs and fs.filename and not plan_is_pdf(fs):
        return jsonify(ok=False,
                       err="That is not a PDF. Only PDF files can be added here."), 400
    cur = db().execute("INSERT INTO plans(title, first_considered, status, archived, "
                       "created_at) VALUES(?,?,?,0,?)",
                       (title[:200], when, status, now_s()))
    pid = cur.lastrowid
    db().commit()
    if fs and fs.filename:
        ok, msg = plan_store(pid, fs)
        if not ok:
            db().execute("DELETE FROM plans WHERE id=?", (pid,))
            db().commit()
            return jsonify(ok=False, err=msg), 400
    return jsonify(ok=True, id=pid)


@app.route("/api/plans/<int:pid>", methods=["POST"])
@login_required
def api_plans_edit(pid):
    r = db().execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="No such plan."), 404
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or r["title"]).strip()
    status = (body.get("status") or r["status"]).strip()
    if not title:
        return jsonify(ok=False, err="A plan needs a title."), 400
    if status not in PLAN_STATUSES:
        return jsonify(ok=False, err="Unknown status."), 400
    arch = r["archived"]
    if "archived" in body:
        arch = 1 if body.get("archived") else 0
    db().execute("UPDATE plans SET title=?, status=?, archived=? WHERE id=?",
                 (title[:200], status, arch, pid))
    db().commit()
    return jsonify(ok=True)


@app.route("/plan/<int:pid>/file")
@login_required
def plan_file(pid):
    r = db().execute("SELECT * FROM plan_files WHERE plan_id=? "
                     "ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if not r:
        abort(404)
    path = os.path.join(PLANS_DIR, r["stored_name"])
    if not os.path.exists(path):
        abort(404)
    fh = open(path, "rb")
    try:
        data = fh.read()
    finally:
        fh.close()
    disp = "attachment" if request.args.get("dl") == "1" else "inline"
    name = (r["original_name"] or "plan.pdf").replace('"', "").replace("\n", "")
    resp = Response(data, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = disp + '; filename="' + name + '"'
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


'''

PAGE_BLOCK = r'''PLANS_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Plans - GutLog</title>
<style>
:root{--bg:#F4F6F5;--card:#fff;--ink:#17201D;--muted:#55645E;--line:#DCE4E0;
      --teal:#2E7D6B;--teal-d:#1F5F51;--warn:#A32E22;--chip:#EDF3F0}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;--line:#2C3733;
  --teal:#5FB49C;--teal-d:#8ED3BD;--warn:#F09284;--chip:#243029}}
:root[data-theme="dark"]{--bg:#121715;--card:#1B2220;--ink:#E8EEEB;--muted:#A6B6AF;
  --line:#2C3733;--teal:#5FB49C;--teal-d:#8ED3BD;--warn:#F09284;--chip:#243029}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;
       background:var(--card);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;font-weight:700;flex:1}
header a{color:var(--teal-d);text-decoration:none;font-size:13px;font-weight:600}
main{padding:16px;max-width:760px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:14px;margin:0 0 12px}
.ttl{font-weight:700;margin:0 0 3px}
.sub{color:var(--muted);font-size:13px;margin:0 0 10px}
.badge{display:inline-block;font-size:11.5px;font-weight:700;padding:2px 8px;
       border-radius:999px;background:var(--chip);color:var(--teal-d);margin-left:6px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
button,.btn{font:inherit;font-weight:600;border-radius:9px;padding:9px 14px;
       border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.go,.btn.go{background:var(--teal);border-color:var(--teal);color:#fff}
a.btn{text-decoration:none;display:inline-block}
label{display:block;font-size:12.5px;color:var(--muted);font-weight:600;margin:10px 0 4px}
input[type=text],select,input[type=file]{width:100%;font:inherit;padding:9px;
       border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--ink)}
.dmy{display:flex;gap:6px}
.dmy select{flex:1;min-width:0}
.msg{margin-top:10px;font-size:13.5px;min-height:1.2em}
.msg.bad{color:var(--warn)}
.empty{color:var(--muted)}
.hint{color:var(--muted);font-size:12.5px;margin-top:8px}
</style></head><body>
<header><h1>Plans</h1><a href="/">Back to GutLog</a></header>
<main>
<div id="list">__ROWS__</div>

<div class="card">
  <p class="ttl">Add a plan</p>
  <label for="t">Title</label>
  <input type="text" id="t" maxlength="200" placeholder="What the plan is">
  <label>First considered</label>
  <div class="dmy">
    <select id="dd" aria-label="Day"></select>
    <select id="mm" aria-label="Month"></select>
    <select id="yy" aria-label="Year"></select>
  </div>
  <label for="st">Status</label>
  <select id="st"><option>Draft</option><option>Active</option><option>Closed</option></select>
  <label for="f">PDF</label>
  <input type="file" id="f" accept="application/pdf">
  <div class="row"><button class="go" id="add">Add plan</button></div>
  <p class="hint">PDF only, up to 20 MB. The file stays behind your login.</p>
  <p class="msg" id="msg"></p>
</div>
</main>
<script>
var MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
var TODAY="__TODAY__";
function $(q){return document.querySelector(q);}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;
  if(x!==undefined)e.textContent=x;return e;}
function pad(n){return (n<10?"0":"")+n;}

/* Three lists, not a native date box: on the folded phone the system dialog
   hides its own confirm button. Same lesson as the time lists in v3.27.0. */
function fillDate(){
  var d=$("#dd"),m=$("#mm"),y=$("#yy"),i;
  for(i=1;i<=31;i++)d.add(new Option(pad(i),pad(i)));
  for(i=0;i<12;i++)m.add(new Option(MON[i],pad(i+1)));
  var ty=parseInt(TODAY.slice(0,4),10);
  for(i=ty+1;i>=ty-6;i--)y.add(new Option(String(i),String(i)));
  d.value=TODAY.slice(8,10);m.value=TODAY.slice(5,7);y.value=TODAY.slice(0,4);
}
function pickedDate(){return $("#yy").value+"-"+$("#mm").value+"-"+$("#dd").value;}
function say(t,bad){var m=$("#msg");m.textContent=t;m.className="msg"+(bad?" bad":"");}

/* The list above is already on the page, rendered by the server. Everything
   below only ACTS on it, so the page still reads correctly with no script. */
function share(id,name,title){
  fetch("/plan/"+id+"/file",{credentials:"same-origin"}).then(function(r){
    if(!r.ok)throw new Error("not available");
    return r.blob();
  }).then(function(b){
    var fname=name||("plan-"+id+".pdf");
    var file=new File([b],fname,{type:"application/pdf"});
    if(navigator.canShare&&navigator.canShare({files:[file]})&&navigator.share){
      return navigator.share({files:[file],title:title||fname});
    }
    /* No file sharing here, so hand him the file. There is deliberately no
       public URL to fall back to. */
    var a=document.createElement("a");
    a.href=URL.createObjectURL(b);a.download=fname;document.body.appendChild(a);
    a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},4000);
  }).catch(function(e){
    if(e&&e.name==="AbortError")return;
    say("Could not share that file.",true);
  });
}

function post(pid,body){
  return fetch("/api/plans/"+pid,{method:"POST",credentials:"same-origin",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
    .then(function(r){return r.json();})
    .then(function(j){
      if(!j.ok){say(j.err||"Could not save that.",true);return;}
      location.reload();
    }).catch(function(){say("Could not save that.",true);});
}

function bind(){
  [].forEach.call(document.querySelectorAll(".shr"),function(b){
    b.onclick=function(){share(b.dataset.id,b.dataset.name,b.dataset.title);};});
  [].forEach.call(document.querySelectorAll(".stsel"),function(s){
    s.style.width="auto";
    s.onchange=function(){post(s.dataset.id,{status:s.value});};});
  [].forEach.call(document.querySelectorAll(".rnm"),function(b){
    b.onclick=function(){
      var t=prompt("Title",b.dataset.title);
      if(t===null)return;
      post(b.dataset.id,{title:t});};});
  [].forEach.call(document.querySelectorAll(".arc"),function(b){
    b.onclick=function(){
      if(!confirm("Hide this plan from the list? The file is kept."))return;
      post(b.dataset.id,{archived:1});};});
}

$("#add").onclick=function(){
  var t=$("#t").value.trim();
  if(!t){say("Give the plan a title.",true);return;}
  var fd=new FormData();
  fd.append("title",t);
  fd.append("first_considered",pickedDate());
  fd.append("status",$("#st").value);
  if($("#f").files[0])fd.append("file",$("#f").files[0]);
  say("Saving...");
  fetch("/api/plans",{method:"POST",credentials:"same-origin",body:fd})
    .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(x){
      if(!x.ok||!x.j.ok){say((x.j&&x.j.err)||"Could not save that.",true);return;}
      say("Added.");
      location.reload();
    }).catch(function(){say("Could not save that.",true);});
};

fillDate();bind();
</script></body></html>
"""


'''

CODE_OLD = '# ------------------------------------------------------------------ auto-read\n'
CODE_NEW = PAGE_BLOCK + PLANS_BLOCK + CODE_OLD

# ------------------------------------------------------------- 6. nav link
NAV_OLD = '<a href="/account">Account</a><a href="/logout">Lock</a></header>'
NAV_NEW = ('<a href="/plans">Plans</a><a href="/account">Account</a>'
           '<a href="/logout">Lock</a></header>')

# ----------------------------------------------- 7. room for the third link
# Adding the link pushed the main page to 327px against a 300px viewport and
# test_v3270 assertion 07 failed on the sideways scroll -- which is exactly
# what that assertion is for. The bar now wraps at that width instead.
#
# NOTE the anchor. There are two near-identical header blocks in this file,
# and the first one belongs to ACCOUNT_PAGE, not to the app. Patching that one
# changed the Account page and left the fault untouched, and the suite still
# reported 327px -- the same number, which is what gave it away. This anchor
# is APP_PAGE's: 10px padding and z-index 5, where .day takes the auto margin
# and every link adds 12px of its own.
CSS_OLD = ('padding:calc(10px + env(safe-area-inset-top)) 16px 10px;'
           'display:flex;align-items:center;gap:10px;\n'
           'box-shadow:0 2px 14px rgba(8,79,79,.25)}\n')
CSS_NEW = ('padding:calc(10px + env(safe-area-inset-top)) 16px 10px;'
           'display:flex;align-items:center;gap:10px;\n'
           '/* GUTLOG_V3280_PLANS -- a third link does not fit on the folded\n'
           '   screen, so the bar wraps there rather than scrolling the page. */\n'
           'flex-wrap:wrap;\n'
           'box-shadow:0 2px 14px rgba(8,79,79,.25)}\n')

LINK_OLD = 'header a{color:#fff;opacity:.85;font-size:13px;text-decoration:none;margin-left:12px}\n'
LINK_NEW = ('header a{color:#fff;opacity:.85;font-size:13px;text-decoration:none;'
            'margin-left:10px;white-space:nowrap}\n')

EDITS = [
    ("header", HEAD_OLD, HEAD_NEW),
    ("version", VER_OLD, VER_NEW),
    ("constants", DIR_OLD, DIR_NEW),
    ("schema", SCH_OLD, SCH_NEW),
    ("plans page and code", CODE_OLD, CODE_NEW),
    ("nav link", NAV_OLD, NAV_NEW),
    ("header wraps", CSS_OLD, CSS_NEW),
    ("header links", LINK_OLD, LINK_NEW),
]

# CLAUDE.md 5b -- a Jinja token in the MAIN template breaks the page at render
# time and still passes py_compile. PLANS_PAGE is not Jinja-rendered, but the
# nav link is, and a stray token anywhere is worth refusing.
JINJA = ("{{", "{%", "{#")


def read(p):
    fh = open(p, "r", encoding="utf-8", newline="")
    try:
        return fh.read()
    finally:
        fh.close()


def write(p, t):
    fh = open(p, "w", encoding="utf-8", newline="")
    try:
        fh.write(t)
    finally:
        fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT")
    a = ap.parse_args()

    if not os.path.exists(a.file):
        print("FATAL: not found: " + a.file)
        return 1
    src = read(a.file)

    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED, nothing written: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0

    print("==================================================================")
    print("GutLog the Plans page -> v" + VERSION)
    print("file : " + a.file)
    print("==================================================================")

    for label, old, new in EDITS:
        for tok in JINJA:
            if new.count(tok) > old.count(tok):
                print("FATAL: %s adds the Jinja token %r. Nothing written." % (label, tok))
                return 1

    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: " + PREV + " not present. Wrong base.")
        return 1

    bad = [(l, src.count(o)) for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        for l, c in bad:
            print("  %s: found %d times, need 1" % (l, c))
        print("Refusing to patch. Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0

    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)

    tmpd = tempfile.mkdtemp()
    cand = os.path.join(tmpd, "cand.py")
    write(cand, out)
    try:
        py_compile.compile(cand, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    bak = a.file + ".bak-v3280-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.file, bak)
    print("backup : " + bak)
    write(a.file, out)
    try:
        py_compile.compile(a.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, a.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: %d edits" % len(EDITS))
    print("Next:  python3 test_v3280_plans.py app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

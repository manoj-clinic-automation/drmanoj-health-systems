#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.11.0 -> v3.12.0  ::  Phase I -- the pain entry surface

  * A "Pain now" card on the Now screen, collapsed, beside blood pressure,
    today's doses and Symptom now. Nine tiles, hero tile first:
    Both hips + anterior thighs / Hip-thigh R / L / Glutes both / Glute R / L /
    Low back / Neck -> R arm / Neck -> L arm. A tile asks three things and no
    more: score 0-10, what was done, and (hip and glute tiles only) whether it
    goes below the knee.
  * Each tap writes ONE row to `episodes` -- the table that already holds every
    within-day event -- with category='pain', etype=<site slug>, side, severity,
    plus two new columns added through the existing idempotent _migrate ALTER
    pattern: treatments (pipe-joined, the days.syms convention) and radiates.
  * An analgesic chip does NOT create a parallel medicine record. Tapping one
    writes a real `doses` row with reason = the pain site, exactly as an ad-hoc
    dose does today, AND is mirrored to FitLog's analgesic_log with
    pain_at_time = the score just entered. The chips themselves are medicine
    names, so they are read from regimen.local.json like every other medicine
    name in this repository; a clone without that file gets the four physical
    measures and no drug chips.
  * "eased": one tap, no dialog, hours later. Stamps `duration` from etime to
    now, so duration is measured rather than guessed. Offered on the pain card
    and in the day-view row-action strip.
  * Activity gains kind `ot_day` (Operating day) with an hours picker
    (2/4/6/8/10 h), stored as minutes like every other activity. Hours on his
    legs are load, not training: excluded from exercise minutes everywhere and
    shown distinctly. It reaches FitLog through /api/feed/activities unchanged.
  * /api/feed/stack additionally reports each regimen line's valid_from and a
    list of recently ENDED schedules with their valid_to, so RxGuard can offer
    to stop a medicine on the date GutLog ended it rather than on today.

Requires v3.11.0 (GUTLOG_V3110_SCANQ). Anchor-verified, idempotent,
compile-checked, Jinja-safe, .bak before write, self-restoring. Python 3.9.

Reversible, and checked rather than assumed: reversing all 26 anchors out of
the patched file reproduces v3.11.0 at exactly 241,645 bytes (owner,
2026-09-14). Every edit is therefore a clean substitution with no overlap,
which is what makes the .bak a formality rather than the only way back.

Deployment history lives in DOSSIER_GutLog.md, not here.
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3120_PAIN"
PREV = "GUTLOG_V3110_SCANQ"

# --------------------------------------------------------------- python block
PY = '''# ------------------------------------------------------------------ pain
# GUTLOG_V3120_PAIN -- musculoskeletal pain goes into `episodes`, the table
# that already carries every within-day event. There is no second pain table
# and no second medicine record: an analgesic tapped on a pain tile writes a
# real `doses` row exactly as an ad-hoc dose does, and the same event is
# mirrored to FitLog's analgesic_log carrying the score that was just entered.
# A medicine logged in two places is a record that disagrees with itself.
#
# Sides are never averaged: right is the THR side, left is the native
# arthritic hip, and the tiles keep them apart by construction.
PAIN_SITES_MSK = [
    ("hip_thigh_both", "Both hips + anterior thighs", "both", 1),
    ("hip_thigh_r", "Hip / thigh - R", "R", 1),
    ("hip_thigh_l", "Hip / thigh - L", "L", 1),
    ("glute_both", "Glutes - both", "both", 1),
    ("glute_r", "Glute - R", "R", 1),
    ("glute_l", "Glute - L", "L", 1),
    ("low_back", "Low back", "", 0),
    ("neck_arm_r", "Neck to R arm", "R", 0),
    ("neck_arm_l", "Neck to L arm", "L", 0),
]
PAIN_SITE_MAP = dict((s[0], s) for s in PAIN_SITES_MSK)

# The non-medicine half of the treatment chips. Safe to keep here: nobody's
# private record is a hot shower.
PAIN_TREATMENTS_BASE = ["Heat pad", "Hot shower", "NormaTec", "Rest"]

# The analgesic chips are medicine names, and this repository is public, so
# they live in regimen.local.json beside app.py with every other medicine name
# (see _local_seed, 2026-09-10). Each entry is [chip label, molecule]. A clone
# without that file gets the four physical measures and no drug chips, which
# is the right default for someone else's pain.
PAIN_ANALGESICS = {}
PAIN_TREATMENTS = list(PAIN_TREATMENTS_BASE)
for _pa in _local_seed("pain_analgesics"):
    if isinstance(_pa, list) and len(_pa) >= 2 and _pa[0]:
        # chip label -> (molecule, name to fall back on when GutLog carries
        # no such medicine; the dose is still recorded, under its label)
        PAIN_ANALGESICS[_pa[0]] = (str(_pa[1]).strip().lower(), _pa[0])
        PAIN_TREATMENTS.append(_pa[0])


def _link_post(url, payload, timeout=3):
    """POST JSON to a companion app carrying the feed token. Never raises;
    returns the decoded answer, or None when links are off or the other app
    refused. The write that matters has already happened locally."""
    import urllib.request
    if not _links_enabled():
        return None
    hdr = {"Content-Type": "application/json"}
    if _FEED_TOKEN:
        hdr["Authorization"] = "Bearer " + _FEED_TOKEN
    try:
        local = url.startswith("http://127.")
        op = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local \\
            else urllib.request.build_opener()
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=hdr, method="POST")
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _pain_med(molecule, fallback):
    """The prnmeds row for an analgesic chip: by molecule first, by name
    second. Returns (med_id, name). med_id is None when GutLog carries no such
    medicine -- the dose is still recorded under its label rather than lost."""
    mol = (molecule or "").strip().lower()
    r = db().execute("SELECT id, name FROM prnmeds WHERE active=1 AND "
                     "LOWER(COALESCE(molecule,''))=? ORDER BY sort, id",
                     (mol,)).fetchone()
    if not r:
        r = db().execute("SELECT id, name FROM prnmeds WHERE active=1 AND "
                         "LOWER(name) LIKE ? ORDER BY sort, id",
                         (mol + "%",)).fetchone()
    if r:
        return r["id"], r["name"]
    return None, fallback


def _dur_text(mins):
    if mins < 60:
        return str(mins) + " min"
    return str(mins // 60) + " h " + ("%02d" % (mins % 60)) + " min"


@app.route("/api/pain", methods=["GET", "POST"])
@login_required
def api_pain():
    if request.method == "GET":
        day = _valid_day(request.args.get("day")) or today()
        rows = []
        for r in db().execute(
                "SELECT id, etime, etype, side, severity, duration, "
                "COALESCE(treatments,'') AS treatments, COALESCE(radiates,0) AS radiates "
                "FROM episodes WHERE day=? AND category='pain' ORDER BY etime, id",
                (day,)).fetchall():
            d = dict(r)
            meta = PAIN_SITE_MAP.get(d["etype"])
            d["label"] = meta[1] if meta else d["etype"]
            rows.append(d)
        return jsonify(day=day, rows=rows, sites=[
            {"slug": s[0], "label": s[1], "side": s[2], "radiates": bool(s[3])}
            for s in PAIN_SITES_MSK], treatments=PAIN_TREATMENTS)

    d = J()
    meta = PAIN_SITE_MAP.get((d.get("site") or "").strip())
    if not meta:
        return jsonify(ok=False, err="Pick a site."), 400
    try:
        score = int(d.get("score"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Give it a score out of 10."), 400
    if not 0 <= score <= 10:
        return jsonify(ok=False, err="Score must be 0 to 10."), 400
    treats = [t for t in (d.get("treatments") or []) if t in PAIN_TREATMENTS]
    radiates = 1 if (meta[3] and d.get("radiates")) else 0
    day = d.get("day") or today()
    etime = d.get("etime") or now_hm()
    if not _valid_day(day):
        return jsonify(ok=False, err="Pick a real date, not in the future."), 400
    if not _valid_hm(etime):
        return jsonify(ok=False, err="Time must be HH:MM."), 400
    if day == today() and etime > now_hm():
        return jsonify(ok=False, err="That time has not come yet today."), 400

    # duration is left empty on purpose: nothing is asked at the moment of
    # pain, and the "eased" tap stamps it later from etime to then.
    insert("episodes",
           ["day", "etime", "category", "etype", "side", "severity", "duration",
            "notes", "bristol", "treatments", "radiates"],
           [day, etime, "pain", meta[0], meta[2], score, "", note(d), "",
            "|".join(treats), radiates])
    eid = db().execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    doses, mirrored, missed = [], [], []
    linked = _links_enabled()
    for t in treats:
        if t not in PAIN_ANALGESICS:
            continue
        mol, fallback = PAIN_ANALGESICS[t]
        mid, name = _pain_med(mol, fallback)
        db().execute(
            "INSERT INTO doses(day,dtime,medicine,reason,effect,notes,created,"
            "status,med_id,sched_id,dose_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (day, etime, name, meta[1], None, "", now_s(), "EXTRA", mid, None, ""))
        db().commit()
        doses.append(name)
        if not linked:
            continue
        ans = _link_post(FITLOG_URL + "/api/analgesic",
                         {"dt": day + "T" + etime, "molecule": mol, "name": name,
                          "dose_label": name, "pain_at_time": score,
                          "notes": "GutLog: " + meta[1], "source": "gutlog",
                          "ref": "gutlog-episode-" + str(eid)})
        (mirrored if (ans or {}).get("ok") else missed).append(name)
    return jsonify(ok=True, id=eid, site=meta[1], side=meta[2], radiates=radiates,
                   doses=doses, mirrored=mirrored, not_mirrored=missed, linked=linked)


@app.route("/api/episode/eased/<int:eid>", methods=["POST"])
@login_required
def api_episode_eased(eid):
    """One tap, hours later. Duration is measured from etime to now instead of
    being guessed from a bucket chosen while it still hurts."""
    r = db().execute("SELECT id, day, etime, duration FROM episodes WHERE id=?",
                     (eid,)).fetchone()
    if not r:
        return jsonify(ok=False, err="Entry not found."), 404
    start = _valid_hm(r["etime"])
    if not start:
        return jsonify(ok=False, err="That entry has no start time."), 400
    try:
        t0 = datetime.strptime((r["day"] or today()) + " " + start, "%Y-%m-%d %H:%M")
    except ValueError:
        return jsonify(ok=False, err="That entry has no usable time."), 400
    mins = int(round((datetime.now() - t0).total_seconds() / 60.0))
    if mins < 0:
        return jsonify(ok=False, err="That entry starts in the future."), 400
    txt = _dur_text(mins)
    db().execute("UPDATE episodes SET duration=? WHERE id=?", (txt, eid))
    db().commit()
    return jsonify(ok=True, id=eid, minutes=mins, duration=txt)


'''

PY_ANCHOR = "# ------------------------------------------------------------------ records\n"

# ------------------------------------------------------------------------ css
CSS = """/* GUTLOG_V3120_PAIN -- musculoskeletal pain tiles, operating-day load */
.ptile.pain .pscore{display:none;padding:0 12px 12px}
.ptile.pain.open .pscore{display:block}
.ptile.pain.hero .ph{font-size:17.5px;font-weight:800;padding:16px 14px}
.ptile.pain.hero{border-color:#BAD2C8}
.ptile.pain .chips{display:flex;flex-wrap:wrap;gap:7px}
.ptile.pain .lbl{margin:0 0 6px}
.ptile.pain .prad{margin-top:12px}
.ptile.pain .chip.rad{border-color:#EBCB8B}
.ptile.pain .chip.rad.sel{background:#FFF3DC;color:#7A5200;border-color:#EBCB8B}
.ptile.act.load{border-style:dashed}
#painList .exrow.load,.exrow.load{opacity:.95}
.tag.k-pain{background:#FBEDEC;color:var(--err)}
.tag.k-load{background:#EFE7F8;color:#6A3FA8}
@media (max-width:430px){ #n_msk .chip.num{padding:8px 0;min-width:30px;text-align:center} }
"""

CSS_ANCHOR = ("@media (prefers-reduced-motion:reduce){.toast,.pbar i,.chip{transition:none}}\n"
              "</style></head><body>")

# ----------------------------------------------------------------------- html
HTML = """
  <div class="card fold" id="nowPain">
    <button type="button" class="fold-h">
      <span class="ft">Pain now</span><span class="fs" id="painSum">tap to open</span><span class="fc"></span>
    </button>
    <div class="cbody">
      <p class="hint" style="margin:0 0 10px">Tap the site, score it, say what you did.
        Tap <b>eased</b> when it settles and the duration is measured, not guessed.</p>
      <div id="n_msk"></div>
      <div id="painList"></div>
    </div>
  </div>
"""

HTML_ANCHOR = ('      <button type="button" class="btn primary" id="n_symSave" '
               'style="margin-top:14px">Save episode</button>\n'
               "    </div>\n"
               "  </div>\n"
               "</section>\n")

# ------------------------------------------------------------------------- js
JS = """/* GUTLOG_V3120_PAIN -- pain tiles. One tile, one tap, three questions and
   no more: how bad, what was done, and whether it goes below the knee. The
   tile writes one episodes row; an analgesic chip additionally writes a real
   dose row and mirrors it to FitLog with the score.
   The sites and the chips come from the server, so the medicine names stay in
   regimen.local.json with every other medicine name and never reach this
   page. */
const PAIN_SCORE=['0','1','2','3','4','5','6','7','8','9','10'];

function buildPainTiles(sites,treats){
  const box=$('#n_msk');
  if(!box||box.dataset.built||!sites||!sites.length)return;
  box.dataset.built='1';
  sites.forEach((t,idx)=>{
    const slug=t.slug,label=t.label,canRad=t.radiates,hero=(idx===0);
    const w=document.createElement('div');
    w.className='ptile pain'+(hero?' hero':'');
    w.dataset.k=slug;
    w.innerHTML='<button type="button" class="ph"><span class="pn"></span><span class="pv"></span></button>'+
      '<div class="pscore"><p class="lbl">Score 0-10</p><div class="chips ps"></div>'+
      '<p class="lbl" style="margin-top:12px">What did you do</p><div class="chips pt"></div>'+
      '<div class="prad"></div>'+
      '<button type="button" class="btn primary pgo" style="margin-top:12px">Save</button></div>';
    w.querySelector('.pn').textContent=label;
    const st={score:null,treats:[],rad:0};
    const pv=w.querySelector('.pv'),ps=w.querySelector('.ps'),pt=w.querySelector('.pt');
    PAIN_SCORE.forEach(v=>{
      const b=document.createElement('div');
      b.className='chip num';
      b.textContent=v;
      b.onclick=()=>{
        st.score=v;
        pv.textContent=v+'/10';
        [...ps.children].forEach(c=>c.classList.toggle('sel',c===b));
      };
      ps.appendChild(b);
    });
    (treats||[]).forEach(v=>{
      const b=document.createElement('div');
      b.className='chip';
      b.textContent=v;
      b.onclick=()=>{
        const i=st.treats.indexOf(v);
        if(i>=0)st.treats.splice(i,1); else st.treats.push(v);
        b.classList.toggle('sel',st.treats.indexOf(v)>=0);
      };
      pt.appendChild(b);
    });
    if(canRad){
      const rb=document.createElement('div');
      rb.className='chip rad';
      rb.textContent='goes below the knee';
      rb.onclick=()=>{
        st.rad=st.rad?0:1;
        rb.classList.toggle('sel',!!st.rad);
      };
      w.querySelector('.prad').appendChild(rb);
    }
    w.querySelector('.ph').onclick=()=>w.classList.toggle('open');
    w.querySelector('.pgo').onclick=async()=>{
      if(st.score===null){toast('Give it a score');return;}
      try{
        const r=await post('/api/pain',{site:slug,score:st.score,
          treatments:st.treats,radiates:st.rad,day:todayISO});
        let msg=label+' '+st.score+'/10 logged';
        if(r.doses&&r.doses.length)msg+=' \\u00b7 '+r.doses.join(' + ')+' recorded';
        toast(msg);
        if(r.not_mirrored&&r.not_mirrored.length)
          toast('FitLog did not take '+r.not_mirrored.join(', '));
        st.score=null;st.treats=[];st.rad=0;
        w.classList.remove('open');
        pv.textContent='';
        w.querySelectorAll('.chip').forEach(c=>c.classList.remove('sel'));
        loadPain();
        loadNow();
      }catch(err){toast(err.message);}
    };
    box.appendChild(w);
  });
}

/* Today's pain, and the one tap that turns a start time into a duration. */
async function loadPain(){
  const el=$('#painList');
  if(!el)return;
  const j=await jget('/api/pain?day='+todayISO);
  buildPainTiles(j.sites,j.treatments);
  const rows=j.rows||[];
  const open=rows.filter(r=>!r.duration).length;
  $('#painSum').textContent=rows.length?
    (rows.length+' logged today'+(open?(' \\u00b7 '+open+' unresolved'):'')):'tap to open';
  el.innerHTML='';
  rows.forEach(r=>{
    const row=document.createElement('div');
    row.className='exrow';
    row.innerHTML='<span class="t"></span><span class="m"></span>';
    row.querySelector('.t').textContent=r.etime||'';
    const bits=[r.label+' '+r.severity+'/10'];
    if(r.radiates)bits.push('below the knee');
    if(r.treatments)bits.push(r.treatments.split('|').join(', '));
    if(r.duration)bits.push('eased after '+r.duration);
    row.querySelector('.m').textContent=bits.join(' \\u00b7 ');
    if(!r.duration){
      const e=document.createElement('button');
      e.type='button';
      e.className='btn tiny u';
      e.textContent='eased';
      e.onclick=async()=>{
        try{
          const a=await post('/api/episode/eased/'+r.id,{});
          toast('Lasted '+a.duration);
          loadPain();
        }catch(err){toast(err.message);}
      };
      row.appendChild(e);
    }
    el.appendChild(row);
  });
}

"""

JS_ANCHOR = "function buildNowStatics(){\n  bindFolds();\n"


def build_edits():
    E = []

    # ---- version + schema ------------------------------------------------
    E.append(("version marker", PREV + "\n", PREV + " " + MARKER + "\n"))
    E.append(("schema version", 'SCHEMA_VERSION = "3.3.2"', 'SCHEMA_VERSION = "3.3.3"'))

    # ---- the two new episodes columns, through the existing ALTER pattern -
    a = '    ("episodes", "bristol", "TEXT DEFAULT \'\'"),\n'
    E.append(("episodes columns", a,
              a + '    ("episodes", "treatments", "TEXT DEFAULT \'\'"),\n'
                  '    ("episodes", "radiates", "INTEGER DEFAULT 0"),\n'))

    # ---- /api/episodes carries them too ----------------------------------
    a = ('    insert("episodes", ["day","etime","category","etype","side","severity","duration","notes","bristol"],\n'
         '           [d.get("day") or today(), d.get("etime") or now_hm(),\n'
         '            d.get("category"), d["etype"], d.get("side"), d.get("severity"),\n'
         '            d.get("duration"), note(d), (d.get("bristol") or "")[:4]])\n')
    n = ('    tr = d.get("treatments")\n'
         '    tr = "|".join(tr) if isinstance(tr, list) else (tr or "")\n'
         '    insert("episodes", ["day","etime","category","etype","side","severity","duration","notes","bristol","treatments","radiates"],\n'
         '           [d.get("day") or today(), d.get("etime") or now_hm(),\n'
         '            d.get("category"), d["etype"], d.get("side"), d.get("severity"),\n'
         '            d.get("duration"), note(d), (d.get("bristol") or "")[:4],\n'
         '            tr[:200], 1 if d.get("radiates") else 0])\n')
    E.append(("api_episodes", a, n))

    # ---- day view: extra fields on an entry ------------------------------
    a = ('    def add(tbl, rid, t, kind, title, sub):\n'
         '        out.append({"tbl": tbl, "id": rid, "time": t or "", "kind": kind,\n'
         '                    "title": title or "", "sub": sub,\n'
         '                    "edited": (tbl, rid) in edited})\n')
    n = ('    def add(tbl, rid, t, kind, title, sub, **extra):\n'
         '        e = {"tbl": tbl, "id": rid, "time": t or "", "kind": kind,\n'
         '             "title": title or "", "sub": sub,\n'
         '             "edited": (tbl, rid) in edited}\n'
         '        e.update(extra)\n'
         '        out.append(e)\n')
    E.append(("dayview add()", a, n))

    a = ('    for r in db().execute(\n'
         '            "SELECT id, etime, etype, side, severity, bristol "\n'
         '            "FROM episodes WHERE day=?", (day,)).fetchall():\n'
         '        parts = []\n'
         '        if r["severity"] not in (None, ""):\n'
         '            parts.append(str(r["severity"]) + "/10")\n'
         '        if r["bristol"]:\n'
         '            parts.append("Bristol " + str(r["bristol"]))\n'
         '        if r["side"]:\n'
         '            parts.append(str(r["side"]))\n'
         '        add("episodes", r["id"], r["etime"], "Symptom", r["etype"], " · ".join(parts))\n')
    n = ('    for r in db().execute(\n'
         '            "SELECT id, etime, etype, side, severity, bristol, category, duration, "\n'
         '            "COALESCE(treatments,\'\') AS treatments, COALESCE(radiates,0) AS radiates "\n'
         '            "FROM episodes WHERE day=?", (day,)).fetchall():\n'
         '        parts = []\n'
         '        if r["severity"] not in (None, ""):\n'
         '            parts.append(str(r["severity"]) + "/10")\n'
         '        if r["bristol"]:\n'
         '            parts.append("Bristol " + str(r["bristol"]))\n'
         '        if r["side"]:\n'
         '            parts.append(str(r["side"]))\n'
         '        pain = (r["category"] or "") == "pain"\n'
         '        title = r["etype"]\n'
         '        if pain:\n'
         '            meta = PAIN_SITE_MAP.get(r["etype"])\n'
         '            title = meta[1] if meta else r["etype"]\n'
         '            if r["radiates"]:\n'
         '                parts.append("below the knee")\n'
         '            if r["treatments"]:\n'
         '                parts.append(r["treatments"].replace("|", ", "))\n'
         '            if r["duration"]:\n'
         '                parts.append("eased after " + str(r["duration"]))\n'
         '        add("episodes", r["id"], r["etime"], "Pain" if pain else "Symptom",\n'
         '            title, " · ".join(parts), pain=pain, eased=bool(r["duration"]))\n')
    E.append(("dayview episodes", a, n))

    a = ('    for r in db().execute("SELECT id, atime, kind, minutes, intensity FROM activities WHERE day=?",\n'
         '                          (day,)).fetchall():\n'
         '        add("activities", r["id"], r["atime"], "Activity",\n'
         '            ACT_KINDS.get(r["kind"], r["kind"]) + " " + str(int(round(r["minutes"] or 0))) + " min",\n'
         '            (r["intensity"] or "").lower())\n')
    n = ('    for r in db().execute("SELECT id, atime, kind, minutes, intensity FROM activities WHERE day=?",\n'
         '                          (day,)).fetchall():\n'
         '        mins = int(round(r["minutes"] or 0))\n'
         '        if r["kind"] in LOAD_KINDS:\n'
         '            add("activities", r["id"], r["atime"], "Load",\n'
         '                ACT_KINDS.get(r["kind"], r["kind"]) + " " + str(round(mins / 60.0, 1)) + " h",\n'
         '                "load, not exercise", load=True)\n'
         '        else:\n'
         '            add("activities", r["id"], r["atime"], "Activity",\n'
         '                ACT_KINDS.get(r["kind"], r["kind"]) + " " + str(mins) + " min",\n'
         '                (r["intensity"] or "").lower())\n')
    E.append(("dayview activities", a, n))

    # ---- CSV export carries the new columns ------------------------------
    a = '"episodes": "day,etime,category,etype,side,severity,duration,notes",'
    E.append(("csv export", a,
              '"episodes": "day,etime,category,etype,side,severity,duration,treatments,radiates,notes",'))

    # ---- feed: valid_from on the regimen, and the ended schedules ---------
    a = ('    regimen = [dict(r) for r in db().execute(\n'
         '        "SELECT p.id AS med_id, p.name, p.molecule, COALESCE(ms.strength,\'\') AS strength, "\n'
         '        "s.slot, s.dose_text, s.variants "\n'
         '        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "\n'
         '        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE s.valid_from<=? "\n'
         '        "AND (s.valid_to=\'\' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",\n'
         '        (tday, tday)).fetchall()]\n')
    n = ('    regimen = [dict(r) for r in db().execute(\n'
         '        "SELECT p.id AS med_id, p.name, p.molecule, COALESCE(ms.strength,\'\') AS strength, "\n'
         '        "s.slot, s.dose_text, s.variants, s.valid_from "\n'
         '        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "\n'
         '        "LEFT JOIN med_salts ms ON ms.med_id=p.id WHERE s.valid_from<=? "\n'
         '        "AND (s.valid_to=\'\' OR s.valid_to IS NULL OR s.valid_to>=?) ORDER BY p.sort, p.id",\n'
         '        (tday, tday)).fetchall()]\n'
         '    # GUTLOG_V3120_PAIN -- schedules that have ENDED, with the date they\n'
         '    # ended. api_feed_stack drops them from `regimen`, correctly; but a\n'
         '    # consumer that wants to stop its own record needs the date GutLog\n'
         '    # ended it, not today.\n'
         '    floor = (date.today() - timedelta(days=180)).isoformat()\n'
         '    ended = [dict(r) for r in db().execute(\n'
         '        "SELECT p.id AS med_id, p.name, p.molecule, s.slot, MAX(s.valid_to) AS valid_to "\n'
         '        "FROM med_schedule s JOIN prnmeds p ON p.id=s.med_id "\n'
         '        "WHERE COALESCE(s.valid_to,\'\')<>\'\' AND s.valid_to<? AND s.valid_to>=? "\n'
         '        "AND p.id NOT IN (SELECT med_id FROM med_schedule "\n'
         '        "WHERE valid_to=\'\' OR valid_to IS NULL OR valid_to>=?) "\n'
         '        "GROUP BY p.id ORDER BY valid_to DESC",\n'
         '        (tday, floor, tday)).fetchall()]\n')
    E.append(("feed regimen + ended", a, n))

    a = ('    return jsonify(ok=True, app="gutlog", since=since, days=days, today=tday,\n'
         '                   regimen=regimen, taken=out)\n')
    E.append(("feed payload", a,
              '    return jsonify(ok=True, app="gutlog", since=since, days=days, today=tday,\n'
              '                   regimen=regimen, ended=ended, taken=out)\n'))

    # ---- the operating day -----------------------------------------------
    a = ('ACT_KINDS = {"walk": "Walk", "treadmill": "Treadmill", "cycle_road": "Cycling (road)",\n'
         '             "cycle_static": "Cycling (static)", "meditation": "Meditation"}\n')
    n = ('ACT_KINDS = {"walk": "Walk", "treadmill": "Treadmill", "cycle_road": "Cycling (road)",\n'
         '             "cycle_static": "Cycling (static)", "meditation": "Meditation",\n'
         '             "ot_day": "Operating day"}\n'
         '# GUTLOG_V3120_PAIN -- hours on his legs are LOAD, not training. The same\n'
         '# hip/glute/thigh complex appears on long operating days, so the hours\n'
         '# have to be recorded or every walking-versus-pain comparison is\n'
         '# confounded by his work. Never counted as exercise minutes.\n'
         'LOAD_KINDS = ("ot_day",)\n')
    E.append(("ACT_KINDS", a, n))

    a = ('    return jsonify(day=day, items=items,\n'
         '                   watch=({"ok": bool((watch or {}).get("ok"))} if _links_enabled() else None),\n'
         '                   summary={"minutes": sum(i["minutes"] for i in items), "steps": steps})\n')
    n = ('    return jsonify(day=day, items=items,\n'
         '                   watch=({"ok": bool((watch or {}).get("ok"))} if _links_enabled() else None),\n'
         '                   summary={"minutes": sum(i["minutes"] for i in items\n'
         '                                           if i["kind"] not in LOAD_KINDS),\n'
         '                            "load_minutes": sum(i["minutes"] for i in items\n'
         '                                                if i["kind"] in LOAD_KINDS),\n'
         '                            "steps": steps})\n')
    E.append(("activity summary", a, n))

    a = ('    items.sort(key=lambda i: (i["time"] == "", i["time"]))\n'
         "    return items\n")
    n = ('    for i in items:\n'
         '        i["load"] = i.get("kind") in LOAD_KINDS\n'
         '    items.sort(key=lambda i: (i["time"] == "", i["time"]))\n'
         "    return items\n")
    E.append(("merge_activity load flag", a, n))

    # ---- python block, css, html ----------------------------------------
    E.append(("python", PY_ANCHOR, PY + PY_ANCHOR))
    E.append(("css", CSS_ANCHOR, CSS + CSS_ANCHOR))
    E.append(("html", HTML_ANCHOR, HTML_ANCHOR[:-len("</section>\n")] + HTML + "</section>\n"))

    # ---- js --------------------------------------------------------------
    E.append(("js pain tiles", JS_ANCHOR, JS + JS_ANCHOR))

    a = "  loadStockAlerts();\n  loadMedStatus();\n  loadActivity();\n"
    E.append(("loadNow calls loadPain", a, a + "  loadPain();\n"))

    a = ("const ACT=[['walk','Walk'],['treadmill','Treadmill'],['cycle_road','Cycling (road)'],\n"
         "           ['cycle_static','Cycling (static)'],['meditation','Meditation']];\n")
    n = ("const ACT=[['walk','Walk'],['treadmill','Treadmill'],['cycle_road','Cycling (road)'],\n"
         "           ['cycle_static','Cycling (static)'],['meditation','Meditation'],\n"
         "           ['ot_day','Operating day']];\n"
         "const ACT_LOAD=['ot_day'];\n"
         "const ACT_HOURS=[2,4,6,8,10];\n")
    E.append(("ACT list", a, n))

    a = ("    const k=a[0],label=a[1];\n"
         "    const w=document.createElement('div');w.className='ptile act';w.dataset.k=k;\n")
    n = ("    const k=a[0],label=a[1];\n"
         "    const isLoad=ACT_LOAD.indexOf(k)>=0;\n"
         "    const w=document.createElement('div');w.className='ptile act'+(isLoad?' load':'');w.dataset.k=k;\n")
    E.append(("act tile class", a, n))

    a = ("    [10,15,20,30,45,60].forEach(v=>{\n"
         "      const b=document.createElement('div');b.className='chip num';b.textContent=v;\n"
         "      b.onclick=()=>{st.min=v;[...am.children].forEach(c=>c.classList.toggle('sel',c===b));\n"
         "        w.querySelector('.pv').textContent=v+' min';};\n"
         "      am.appendChild(b);\n"
         "    });\n"
         "    if(k!=='meditation'){\n")
    n = ("    (isLoad?ACT_HOURS:[10,15,20,30,45,60]).forEach(v=>{\n"
         "      const b=document.createElement('div');b.className='chip num';\n"
         "      b.textContent=isLoad?(v+' h'):v;\n"
         "      b.onclick=()=>{st.min=isLoad?v*60:v;[...am.children].forEach(c=>c.classList.toggle('sel',c===b));\n"
         "        w.querySelector('.pv').textContent=isLoad?(v+' h on your legs'):(v+' min');};\n"
         "      am.appendChild(b);\n"
         "    });\n"
         "    if(k!=='meditation'&&!isLoad){\n")
    E.append(("act minutes picker", a, n))

    a = "        toast('Logged '+label.toLowerCase()+', '+st.min+' min');\n"
    n = ("        toast(isLoad?('Operating day logged, '+(st.min/60)+' h on your legs')\n"
         "                    :('Logged '+label.toLowerCase()+', '+st.min+' min'));\n")
    E.append(("act save toast", a, n))

    a = ("  $('#actSum').textContent=(s.minutes?(s.minutes+' min'):'none yet')+\n"
         "    (s.steps?(' · '+Number(s.steps).toLocaleString('en-IN')+' steps'):'');\n")
    n = ("  const lh=s.load_minutes?(Math.round(s.load_minutes/6)/10):0;\n"
         "  $('#actSum').textContent=(s.minutes?(s.minutes+' min'):(lh?'no exercise':'none yet'))+\n"
         "    (lh?(' · '+lh+' h on legs'):'')+\n"
         "    (s.steps?(' · '+Number(s.steps).toLocaleString('en-IN')+' steps'):'');\n")
    E.append(("activity summary line", a, n))

    a = ("    const row=document.createElement('div');row.className='exrow';\n"
         "    row.innerHTML='<span class=\"t\"></span><span class=\"m\"></span>';\n"
         "    row.querySelector('.t').textContent=i.time||'';\n"
         "    const bits=[(i.source==='watch'?'⌚ ':'')+i.label+' '+i.minutes+' min'];\n")
    n = ("    const row=document.createElement('div');row.className='exrow'+(i.load?' load':'');\n"
         "    row.innerHTML='<span class=\"t\"></span><span class=\"m\"></span>';\n"
         "    row.querySelector('.t').textContent=i.time||'';\n"
         "    const bits=[];\n"
         "    if(i.load){\n"
         "      bits.push(i.label+' '+(Math.round(i.minutes/6)/10)+' h on your legs');\n"
         "      bits.push('load, not exercise');\n"
         "    } else {\n"
         "      bits.push((i.source==='watch'?'⌚ ':'')+i.label+' '+i.minutes+' min');\n"
         "    }\n")
    E.append(("activity row", a, n))

    # ---- day view: the eased action in the strip that already exists -----
    a = ("const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',"
         "BP:'bp',Vitals:'bp',Meal:'meal',Activity:'act'};\n")
    n = ("const DV_TAG={Dose:'dose',Extra:'extra',Skipped:'skip',Symptom:'sym',"
         "BP:'bp',Vitals:'bp',Meal:'meal',Activity:'act',Pain:'pain',Load:'load'};\n")
    E.append(("DV_TAG", a, n))

    a = ("  box.querySelector('.dl').onclick=async()=>{\n"
         "    if(!confirm('Delete this entry?'))return;\n"
         "    try{await post('/api/delete/'+e.tbl+'/'+e.id,{});toast('Deleted');loadDayView();}\n"
         "    catch(err){toast(err.message);}\n"
         "  };\n")
    n = a + ("  if(e.pain&&!e.eased){\n"
             "    const eb=document.createElement('button');\n"
             "    eb.type='button';\n"
             "    eb.className='ea';\n"
             "    eb.textContent='Eased now';\n"
             "    eb.onclick=async()=>{\n"
             "      try{\n"
             "        const a=await post('/api/episode/eased/'+e.id,{});\n"
             "        toast('Lasted '+a.duration);\n"
             "        box.remove();\n"
             "        loadDayView();\n"
             "      }catch(err){toast(err.message);}\n"
             "    };\n"
             "    box.querySelector('.vb').insertBefore(eb,box.querySelector('.go'));\n"
             "  }\n")
    E.append(("dvEdit eased", a, n))

    return E


MUST_DEFINE = ["openRowActions", "openVariantPicker", "nowRow", "loadNow", "buildNowStatics",
               "bindFolds", "loadDayView", "dvEdit", "dvMissRow", "loadReview", "loadStock",
               "loadStockAlerts", "stockRow", "renderVitals", "vitalsChart", "loadMedStatus",
               "loadSalts", "saltGuess", "buildActTiles", "loadActivity",
               "buildPainTiles", "loadPain",
               "loadRecSummary", "loadRecDocs", "loadRecTrends", "loadRecPlan", "loadRecords", "spark"]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print("=" * 60)
    print("GutLog Phase I: pain entry surface -> v3.12.0")
    print("file : " + args.file)
    print("=" * 60)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.11.0. Apply that first.")
        return 1
    if "def api_pain" in src or "PAIN_SITES_MSK" in src:
        print("FATAL: pain pieces already present -- unexpected state. Nothing written.")
        return 1

    edits = build_edits()
    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    print("anchors: " + str(len(edits) - len(problems)) + "/" + str(len(edits)) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1

    # Rule 5b: the page is a Jinja template. A stray {{ {% {# in new CSS or JS
    # breaks the whole page at render time and still passes py_compile.
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok + " -- nothing written.")
                return 2

    if args.check:
        print("All anchors OK.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)

    missing = [f for f in MUST_DEFINE if not re.search(r"function\s+" + f + r"\s*\(", out)]
    if missing:
        print("DEFINITION CHECK FAILED, nothing written: " + ", ".join(missing))
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
    bak = args.file + ".bak-v3120-" + stamp
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
    print("-" * 60)
    print("Next:  python3 test_phase_i.py app.py   then  systemctl restart gutlog")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

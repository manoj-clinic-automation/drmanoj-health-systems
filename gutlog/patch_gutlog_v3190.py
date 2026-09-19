#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.18.0 -> v3.19.0  ::  the monthly medicine order

He reorders in the last week of each month for the month after and keeps a
buffer of ten days, so the order is a TOP-UP to a target of 40 days of stock,
never a flat 40 days bought every month. v3.6.0 already keeps a live count:
every logged dose and every pillbox fill comes out of stock. This build turns
that count into the order he forwards to staff.

  1. THE PLAN (/api/order). Per medicine: a target (schedule units a day x 40,
     or a keep-on-hand figure for an SOS medicine, or 14-day use x 40), set
     against the stock EXPECTED ON THE 1st -- today's count less the doses
     still to come this month, allowing for what a filled pillbox already
     holds. The shortfall is rounded up to whole packs. Computed at read
     time; nothing precomputed, nothing to go stale.
  2. SEND. One tap opens WhatsApp with the order text, or copies it; either
     way the order is saved for that month. Sending again before it arrives
     replaces it.
  3. RECEIVED. One tap adds every line to stock; undo takes it back out.
  4. PACKS. Each stock row gets a Pack form: pack size (the existing
     prnmeds.pack_size, which Bought already prefills from), pack type, and a
     keep-on-hand figure for SOS medicines.
  5. THE NOW BANNER. In the last seven days of the month, while no order is
     saved for the month after and there is something to order, an Order
     banner sits under the refill banner, in its own container.
  6. The 40 is a setting (7 to 120 days) on the order card.

Two new tables (SCHEMA, so no schema_version bump), no column changes, no
change to any existing stock figure. Requires v3.18.0 (GUTLOG_V3180_HONEST).
Anchor-verified, idempotent, refuses Jinja-breaking tokens in new text,
compile-checked, .bak before write, self-restoring, --reverse for the negative
control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
PREV = "GUTLOG_V3180_HONEST"
MARKER = "GUTLOG_V3190_ORDER"

SCHEMA_OLD = """CREATE TABLE IF NOT EXISTS stock_meds (
  med_id INTEGER PRIMARY KEY, mode TEXT DEFAULT '');
"""
SCHEMA_NEW = SCHEMA_OLD + """CREATE TABLE IF NOT EXISTS stock_order_cfg (
  med_id INTEGER PRIMARY KEY, pack_type TEXT DEFAULT '', keep_units REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, status TEXT DEFAULT 'OPEN',
  created TEXT, received_at TEXT DEFAULT '', lines TEXT DEFAULT '[]', text TEXT DEFAULT '');
"""

VER_OLD = "GUTLOG_V3170_DOWN GUTLOG_V3180_HONEST\n"
VER_NEW = "GUTLOG_V3170_DOWN GUTLOG_V3180_HONEST GUTLOG_V3190_ORDER\n"

APISTOCK_OLD = """    return jsonify(rows=rows, last_fill=lf["at"] if lf else "", fill_preview=preview)
"""
APISTOCK_NEW = """    # GUTLOG_V3190_ORDER -- the pack details ride along for the Pack form
    cfg = _order_cfg()
    for r in rows:
        c = cfg.get(r["med_id"])
        r["pack_type"] = (c["pack_type"] if c else "") or ""
        r["keep_units"] = float(c["keep_units"] or 0) if c else 0.0
    return jsonify(rows=rows, last_fill=lf["at"] if lf else "", fill_preview=preview)
"""

FEED_ANCHOR = "# ---- feed: read-only, bearer-gated, loopback consumers (RxGuard, FitLog)\n"
PY_BLOCK = r'''# ---- GUTLOG_V3190_ORDER -- the monthly medicine order.
# He orders in the last week of each month for the month after, and wants a
# buffer of ten days on top of the month, so stock is TOPPED UP to a target of
# ORDER_DAYS days (default 40) -- never a flat 40 days bought every month,
# which would pile up. The target is judged on the stock expected on the 1st
# of the ordering month, not on today's count: a week of doses still comes
# out before the new month starts, and what already sits in a filled pillbox
# has left stock but has not been swallowed yet.
#
#   target  scheduled medicine     -> schedule units a day x ORDER_DAYS
#           keep-on-hand set       -> that many units (SOS medicines)
#           otherwise              -> 14-day average use x ORDER_DAYS
#   order   target - expected-on-the-1st, rounded UP to whole packs
#
# Nothing here is stored except the order he sends and the pack details he
# types; the plan itself is computed at read time, like every stock figure.
import math

ORDER_DAYS_DEFAULT = 40
PACK_TYPES = ("strip", "bottle", "pouch", "box", "tube", "sachet", "vial", "pack")
_PACK_PLURAL = {"box": "boxes", "pouch": "pouches"}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _order_days():
    try:
        v = int(setting("order_days") or ORDER_DAYS_DEFAULT)
    except (TypeError, ValueError):
        v = ORDER_DAYS_DEFAULT
    return v if 7 <= v <= 120 else ORDER_DAYS_DEFAULT


def _month_after(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _pack_word(ptype, n):
    ptype = ptype or "pack"
    if n == 1:
        return ptype
    return _PACK_PLURAL.get(ptype, ptype + "s")


def _order_cfg():
    return dict((r["med_id"], r) for r in db().execute(
        "SELECT med_id, pack_type, keep_units FROM stock_order_cfg").fetchall())


def _last_fills(con):
    """The most recent pillbox fill of each medicine. A fill of 7 days made
    2 days ago still holds 5 days of doses that have left stock."""
    out = {}
    for r in con.execute(
            "SELECT med_id, qty, at FROM stock_events WHERE kind='FILL' "
            "ORDER BY at, id").fetchall():
        out[r["med_id"]] = r
    return out


def _order_plan(tday=None):
    con = db()
    tday = tday or date.fromisoformat(today())
    first = _month_after(tday)
    gap = (first - tday).days
    days = _order_days()
    cfg = _order_cfg()
    fills = _last_fills(con)
    sched_units = {}
    for l in con.execute(
            "SELECT med_id, dose_text, variants FROM med_schedule WHERE valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)",
            (first.isoformat(), first.isoformat())).fetchall():
        if not (l["variants"] or "").strip():
            sched_units[l["med_id"]] = sched_units.get(l["med_id"], 0.0) + _units(l["dose_text"])
    lines, skipped = [], []
    for r in _stock_rows():
        c = cfg.get(r["med_id"])
        ptype = (c["pack_type"] if c else "") or ""
        keep = float(c["keep_units"] or 0) if c else 0.0
        if not r["trackable"]:
            skipped.append({"name": r["name"], "why": "strengths vary - count and order it yourself"})
            continue
        su = sched_units.get(r["med_id"], 0.0)
        if r["can_pillbox"] and su <= 0:
            skipped.append({"name": r["name"], "why": "schedule ends before the 1st"})
            continue
        if su > 0:
            target, basis = su * days, "schedule"
        elif keep > 0:
            target, basis = keep, "keep"
        elif r["per_day"] > 0:
            target, basis = r["per_day"] * days, "usage"
        else:
            continue
        if not r["tracked"]:
            skipped.append({"name": r["name"], "why": "not counted yet"})
            continue
        use = su if su > 0 else r["per_day"]
        # A filled pillbox has already left stock but not been swallowed:
        # the days it still holds are added back before the month's use is
        # taken off, so filling the box never changes the order.
        in_box = 0.0
        f = fills.get(r["med_id"])
        if r["mode"] == "pillbox" and f and use > 0:
            filled_on = date.fromisoformat(f["at"][:10])
            in_box = max(0.0, float(f["qty"]) / use - (tday - filled_on).days)
        expected = r["current"] + use * in_box - use * gap
        need = target - expected
        if need < 0.5:
            continue
        units = int(math.ceil(need - 1e-9))
        ps = int(r["pack_size"] or 0)
        packs = int(math.ceil(units / float(ps))) if ps > 0 else 0
        if packs:
            qty_txt = "%d %s of %d" % (packs, _pack_word(ptype or "pack", packs), ps)
            units = packs * ps
        else:
            qty_txt = "%d units" % units
        lines.append({"med_id": r["med_id"], "name": r["name"], "basis": basis,
                      "current": r["current"], "expected": round(expected, 1),
                      "target": round(target, 1), "units": units, "packs": packs,
                      "pack_size": ps, "pack_type": ptype, "qty": qty_txt})
    label = _MONTHS[first.month - 1] + " " + str(first.year)
    text = "Medicines order - " + label + "\n" + "\n".join(
        "%d. %s - %s" % (i + 1, l["name"], l["qty"]) for i, l in enumerate(lines))
    dim = (first - date(tday.year, tday.month, 1)).days
    return {"month": first.strftime("%Y-%m"), "label": label, "days": days,
            "gap": gap, "lines": lines, "skipped": skipped,
            "text": text if lines else "", "last_week": tday.day > dim - 7}


def _order_saved(month):
    r = db().execute("SELECT id, month, status, created, received_at, lines, text "
                     "FROM stock_orders WHERE month=? ORDER BY id DESC LIMIT 1",
                     (month,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "month": r["month"], "status": r["status"],
            "created": r["created"], "received_at": r["received_at"] or "",
            "lines": json.loads(r["lines"] or "[]"), "text": r["text"]}


@app.route("/api/order")
@login_required
def api_order():
    p = _order_plan()
    p["saved"] = _order_saved(p["month"])
    p["due"] = bool(p["last_week"] and not p["saved"] and p["lines"])
    p["pack_types"] = list(PACK_TYPES)
    return jsonify(p)


@app.route("/api/order/save", methods=["POST"])
@login_required
def api_order_save():
    p = _order_plan()
    if not p["lines"]:
        return jsonify(ok=False, err="Nothing to order - stock covers the target."), 400
    s = _order_saved(p["month"])
    if s and s["status"] == "RECEIVED":
        return jsonify(ok=False, err="This month's order is already received. Undo that first."), 400
    blob = json.dumps(p["lines"])
    if s:
        db().execute("UPDATE stock_orders SET lines=?, text=?, created=? WHERE id=?",
                     (blob, p["text"], now_s(), s["id"]))
        oid = s["id"]
    else:
        cur = db().execute("INSERT INTO stock_orders(month, status, created, lines, text) "
                           "VALUES(?,?,?,?,?)", (p["month"], "OPEN", now_s(), blob, p["text"]))
        oid = cur.lastrowid
    db().commit()
    return jsonify(ok=True, id=oid, text=p["text"], n=len(p["lines"]))


def _order_by_id(d):
    try:
        oid = int(d.get("id"))
    except (TypeError, ValueError):
        return None
    return db().execute("SELECT * FROM stock_orders WHERE id=?", (oid,)).fetchone()


@app.route("/api/order/received", methods=["POST"])
@login_required
def api_order_received():
    o = _order_by_id(J())
    if not o:
        return jsonify(ok=False, err="No such order."), 400
    if o["status"] != "OPEN":
        return jsonify(ok=False, err="Already marked received."), 400
    at, n = _now_at(), 0
    for l in json.loads(o["lines"] or "[]"):
        q = float(l.get("units") or 0)
        if q <= 0 or not _stock_med({"med_id": l.get("med_id")}):
            continue
        db().execute("INSERT INTO stock_events(med_id,kind,qty,at,note,created) "
                     "VALUES(?,?,?,?,?,?)",
                     (l["med_id"], "ADD", q, at, "order:" + str(o["id"]), now_s()))
        n += 1
    db().execute("UPDATE stock_orders SET status='RECEIVED', received_at=? WHERE id=?",
                 (at, o["id"]))
    db().commit()
    return jsonify(ok=True, n=n)


@app.route("/api/order/received/undo", methods=["POST"])
@login_required
def api_order_received_undo():
    o = _order_by_id(J())
    if not o or o["status"] != "RECEIVED":
        return jsonify(ok=False, err="That order is not marked received."), 400
    db().execute("DELETE FROM stock_events WHERE kind='ADD' AND note=?",
                 ("order:" + str(o["id"]),))
    db().execute("UPDATE stock_orders SET status='OPEN', received_at='' WHERE id=?", (o["id"],))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/order/days", methods=["POST"])
@login_required
def api_order_days():
    try:
        v = int(J().get("days"))
    except (TypeError, ValueError):
        v = 0
    if not 7 <= v <= 120:
        return jsonify(ok=False, err="Days must be 7 to 120."), 400
    set_setting("order_days", str(v))
    return jsonify(ok=True, days=v)


@app.route("/api/stock/pack", methods=["POST"])
@login_required
def api_stock_pack():
    d = J()
    mid = _stock_med(d)
    try:
        ps = int(float(d.get("pack_size") or 0))
        keep = float(d.get("keep") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, err="Pack size and keep must be numbers."), 400
    ptype = (d.get("pack_type") or "").strip().lower()
    if not mid or not 0 <= ps <= 1000 or not 0 <= keep <= 10000 or \
            (ptype and ptype not in PACK_TYPES):
        return jsonify(ok=False, err="Check the pack details."), 400
    db().execute("UPDATE prnmeds SET pack_size=? WHERE id=?", (ps, mid))
    db().execute("INSERT INTO stock_order_cfg(med_id, pack_type, keep_units) VALUES(?,?,?) "
                 "ON CONFLICT(med_id) DO UPDATE SET pack_type=excluded.pack_type, "
                 "keep_units=excluded.keep_units", (mid, ptype, keep))
    db().commit()
    return jsonify(ok=True)


'''

JS_BLOCK = r'''/* ---------- MONTHLY ORDER (GUTLOG_V3190_ORDER) ----------
   The order tops stock up to the target, counted from what is expected to
   be left on the 1st. Send and copy act on the text already on screen, in
   the tap itself -- a phone drops clipboard and new-window rights once an
   await has passed -- and save the order behind it. */
let ordData=null;
function ordEsc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);}
function packTxt(r){
  const bits=[];
  if(r.pack_size>0)bits.push((r.pack_type||'pack')+' of '+r.pack_size);
  if(r.keep_units>0)bits.push('keep '+fmtQ(r.keep_units));
  return bits.join(', ');
}
async function loadOrderDue(){
  const box=$('#nowOrder');if(!box)return;
  try{
    const j=await jget('/api/order');
    box.innerHTML='';
    if(!j.due)return;
    const d=document.createElement('div');
    d.className='stockalert amber';
    d.innerHTML='<b>Order</b><span></span>';
    d.querySelector('span').textContent=j.label+' order is due: '+j.lines.length+
      (j.lines.length===1?' medicine':' medicines')+'. Tap to send.';
    d.onclick=()=>{switchTab('meds');setSeg('meds','stock');};
    box.appendChild(d);
  }catch(e){}
}
function ordCopyText(t){
  if(navigator.clipboard&&window.isSecureContext){
    return navigator.clipboard.writeText(t).then(()=>true,()=>ordCopyOld(t));
  }
  return Promise.resolve(ordCopyOld(t));
}
function ordCopyOld(t){
  const a=document.createElement('textarea');a.value=t;a.setAttribute('readonly','');
  a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();
  let ok=false;try{ok=document.execCommand('copy');}catch(e){}
  a.remove();return ok;
}
async function ordSave(){
  try{await post('/api/order/save',{});}catch(err){toast(err.message);}
  loadOrder();loadOrderDue();
}
async function loadOrder(){
  const card=$('#ordCard');if(!card)return;
  let j;try{j=await jget('/api/order');}catch(e){return;}
  ordData=j;
  const s=j.saved,got=s&&s.status==='RECEIVED';
  $('#ordDays').value=j.days;
  const sub=card.querySelector('.ord-sub');
  if(got)sub.textContent=j.label+' order received '+s.received_at+' and added to stock.';
  else if(s)sub.textContent=j.label+' order sent '+s.created.slice(0,10)+
    '. Sending again replaces it. Tap Order received when it arrives.';
  else if(j.lines.length)sub.textContent='For '+j.label+': tops each medicine up to '+j.days+
    ' days, counted from what will be left on the 1st.';
  else sub.textContent='Nothing to order for '+j.label+'. Stock covers '+j.days+' days.';
  const lines=got?s.lines:j.lines;
  $('#ordLines').innerHTML=lines.map((l,i)=>'<div class="ordl"><b>'+(i+1)+'. '+ordEsc(l.name)+
    '</b><span class="oq">'+ordEsc(l.qty)+'</span><div class="od">on the 1st about '+
    fmtQ(Math.max(0,l.expected))+', target '+fmtQ(l.target)+
    (l.basis==='keep'?' (keep on hand)':(l.basis==='usage'?' (from use)':''))+
    (l.pack_size>0?'':' - set its pack size')+'</div></div>').join('');
  const can=!got&&j.lines.length>0;
  $('#ordSend').hidden=!can;$('#ordCopy').hidden=!can;
  $('#ordRecv').hidden=!(s&&s.status==='OPEN');
  $('#ordRecvUndo').hidden=!got;
  $('#ordSkip').textContent=j.skipped.length?('Not in the order: '+
    j.skipped.map(x=>x.name+' ('+x.why+')').join(' · ')):'';
  $('#ordSend').onclick=()=>{
    window.open('https://wa.me/?text='+encodeURIComponent(ordData.text),'_blank');
    ordSave();
  };
  $('#ordCopy').onclick=()=>{
    ordCopyText(ordData.text).then(ok=>toast(ok?'Copied. Paste it in WhatsApp.':'Copy failed - use Send on WhatsApp'));
    ordSave();
  };
  $('#ordRecv').onclick=async()=>{
    if(!confirm('Add everything in this order to stock?'))return;
    try{const r=await post('/api/order/received',{id:s.id});
      toast('Added to stock: '+r.n+(r.n===1?' medicine':' medicines'));
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
  $('#ordRecvUndo').onclick=async()=>{
    if(!confirm('Take this order back out of stock?'))return;
    try{await post('/api/order/received/undo',{id:s.id});toast('Received undone');
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
  $('#ordDaysSave').onclick=async()=>{
    try{await post('/api/order/days',{days:$('#ordDays').value});toast('Target saved');loadOrder();loadOrderDue();}
    catch(err){toast(err.message);}
  };
}
function packForm(w,r){
  const old=w.querySelector('.pk');if(old){old.remove();return;}
  const types=(ordData&&ordData.pack_types)||['strip','bottle','pouch','box','tube','sachet','vial','pack'];
  const f=document.createElement('div');f.className='pk';
  f.innerHTML='<div class="row3"><div><p class="lbl">Pack of</p><input type="number" min="0" max="1000" inputmode="numeric" class="pk-n"></div>'+
    '<div><p class="lbl">Type</p><select class="pk-t"><option value="">-</option>'+
    types.map(t=>'<option value="'+t+'">'+t+'</option>').join('')+'</select></div>'+
    '<div><p class="lbl">Keep (SOS)</p><input type="number" min="0" step="1" inputmode="numeric" class="pk-k"></div></div>'+
    '<p class="hint" style="margin:6px 2px 0">Keep = how many to hold for a medicine taken only when needed. Leave 0 for a daily one.</p>'+
    '<button type="button" class="btn tiny go">Save pack</button>';
  f.querySelector('.pk-n').value=r.pack_size||'';
  f.querySelector('.pk-t').value=r.pack_type||'';
  f.querySelector('.pk-k').value=r.keep_units||'';
  f.querySelector('button').onclick=async()=>{
    try{await post('/api/stock/pack',{med_id:r.med_id,pack_size:f.querySelector('.pk-n').value||0,
      pack_type:f.querySelector('.pk-t').value,keep:f.querySelector('.pk-k').value||0});
      toast('Pack saved');loadStock();}
    catch(err){toast(err.message);}
  };
  w.appendChild(f);
}

'''

HTML_CARD = r'''    <div class="card" id="ordCard">
      <p class="q">Monthly order</p>
      <p class="hint ord-sub" style="margin:0 2px 8px"></p>
      <div id="ordLines"></div>
      <div class="ordbtns">
        <button type="button" class="btn tiny go" id="ordSend" hidden>Send on WhatsApp</button>
        <button type="button" class="btn tiny ghost" id="ordCopy" hidden>Copy</button>
        <button type="button" class="btn tiny go" id="ordRecv" hidden>Order received</button>
        <button type="button" class="mini" id="ordRecvUndo" hidden>Undo received</button>
      </div>
      <p class="hint" id="ordSkip" style="margin:8px 2px 0"></p>
      <div class="vtm"><span class="lb">Keep stock for</span><input type="number" id="ordDays" min="7" max="120" inputmode="numeric">
        <span class="lb">days</span><button type="button" class="btn tiny ghost" id="ordDaysSave">Set</button></div>
    </div>
'''

CSS_BLOCK = r'''/* GUTLOG_V3190_ORDER -- monthly order card and the pack form */
.ordl{padding:8px 0;border-bottom:1px solid var(--line)}
.ordl b{font-size:15px}
.ordl .oq{float:right;font-weight:800;color:var(--teal-d);margin-left:8px}
.ordl .od{clear:both;font-size:12.5px;color:var(--muted);margin-top:2px}
.ordbtns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.ordbtns .btn{margin:0}
.ordbtns [hidden]{display:none}
.strow .pk{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
.strow .pk .btn{margin-top:8px}
#ordCard .btn.go,#stList .pk .btn{background:var(--teal);border-color:var(--teal);color:var(--card)}
#ordCard .btn.ghost{background:var(--card);color:var(--teal);border-color:var(--line)}
'''

CSS_ANCHOR = "#saltList .vtm .st{flex:0 0 34% }\n"
NOWBOX_OLD = '  <div id="nowStock"></div>\n'
NOWBOX_NEW = '  <div id="nowStock"></div>\n  <div id="nowOrder"></div>\n'
CARD_ANCHOR = '    <div class="card" id="stPill">\n'
LOADSTOCK_OLD = "  const list=$('#stList');list.innerHTML='';\n"
LOADSTOCK_NEW = "  loadOrder();\n  const list=$('#stList');list.innerHTML='';\n"
BITS_OLD = """    else if(r.days_left!=null)bits.push('about '+Math.floor(r.days_left)+' days');
    ss.textContent=bits.join(' · ');
"""
BITS_NEW = """    else if(r.days_left!=null)bits.push('about '+Math.floor(r.days_left)+' days');
    if(packTxt(r))bits.push(packTxt(r));
    ss.textContent=bits.join(' · ');
"""
UNTR_OLD = "    ss.textContent=modeTxt+' · not counted yet';\n"
UNTR_NEW = "    ss.textContent=[modeTxt,'not counted yet',packTxt(r)].filter(Boolean).join(' · ');\n"
PACKBTN_OLD = "  mk(r.tracked?'Count':'Set count',()=>open('count','','how many left'));\n"
PACKBTN_NEW = PACKBTN_OLD + "  mk('Pack',()=>packForm(w,r));\n"
JS_ANCHOR = "/* ---------- VITALS LOG (GUTLOG_V360_PHASE_C) ---------- */\n"
LOADNOW_OLD = "async function loadNow(){\n  loadStockAlerts();\n"
LOADNOW_NEW = LOADNOW_OLD + "  loadOrderDue();\n"


def build_edits():
    return [
        ("schema: two new tables", SCHEMA_OLD, SCHEMA_NEW),
        ("version marker", VER_OLD, VER_NEW),
        ("api_stock: pack details", APISTOCK_OLD, APISTOCK_NEW),
        ("order routes", FEED_ANCHOR, PY_BLOCK + FEED_ANCHOR),
        ("css", CSS_ANCHOR, CSS_ANCHOR + CSS_BLOCK),
        ("now: order banner box", NOWBOX_OLD, NOWBOX_NEW),
        ("stock: order card", CARD_ANCHOR, HTML_CARD + CARD_ANCHOR),
        ("loadStock: load the order", LOADSTOCK_OLD, LOADSTOCK_NEW),
        ("stock row: pack text", BITS_OLD, BITS_NEW),
        ("stock row: pack text, uncounted", UNTR_OLD, UNTR_NEW),
        ("stock row: Pack button", PACKBTN_OLD, PACKBTN_NEW),
        ("order JS", JS_ANCHOR, JS_BLOCK + JS_ANCHOR),
        ("loadNow: order banner", LOADNOW_OLD, LOADNOW_NEW),
    ]


# GutLog's page is a Jinja template rendered by render_template_string, so a
# "{{", "{%" or "{#" anywhere in new CSS or JS takes the WHOLE page down at
# render time -- and passes py_compile without a murmur. v3.4.0 shipped that
# way. Every patcher since refuses it before writing.
JINJA_TOKENS = ("{{", "{%", "{#")


def jinja_safe(edits):
    bad = []
    for label, _anchor, new in edits:
        for t in JINJA_TOKENS:
            if t in new:
                bad.append("  " + label + ": contains " + t)
    return bad


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
    """Reconstruct v3.18.0 for tools/NEGATIVE_CONTROL.py."""
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    out, bad = src, []
    for label, anchor, new in reversed(build_edits()):
        c = out.count(new)
        if c != 1:
            bad.append("  " + label + ": new text found " + str(c) + " times, need 1")
            break
        out = out.replace(new, anchor, 1)
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    if MARKER in out or PREV not in out:
        print("REVERSE FAILED: marker state wrong, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed " + PREV + " -> " + out_path + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT",
                    help="reconstruct v3.18.0 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("GutLog monthly medicine order -> v3.19.0")
    print("file : " + args.file)
    print("=" * 66)
    if not os.path.exists(args.file):
        print("FATAL: not found: " + args.file)
        return 1
    src = read(args.file)
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.18.0. Apply that first.")
        return 1

    edits = build_edits()
    jb = jinja_safe(edits)
    if jb:
        print("REFUSING: new text carries a Jinja token, which would take the")
        print("whole page down at render time and pass py_compile:")
        for b in jb:
            print(b)
        return 1
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
    if args.check:
        print("All anchors OK; no Jinja tokens in the new text.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)

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
    bak = args.file + ".bak-v3190-" + stamp
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
    print("-" * 66)
    print("Next:  python3 test_phase_n.py app.py")
    print("       python3 test_ui_now.py app.py      (browser, service workers off)")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

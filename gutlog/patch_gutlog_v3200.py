#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.19.0 -> v3.20.0  ::  two stock pipelines, and the strength links

He asked, 19-Sep-2026, for two pipelines instead of one list:

  1. DAILY medicines ride the MONTHLY order (v3.19.0's top-up to 40 days from
     the stock expected on the 1st). A medicine is daily if it has a fixed
     schedule -- or if it is the product that a variant schedule is LINKED to.
  2. SOS medicines never ride the monthly order. Each has a keep-on-hand
     figure; once stock falls below a third of it (never below 1) it appears
     on its own "Running low" list, any day of the month, topped back up to
     keep in whole packs. Its own Send / Copy / Received, stored as an order
     row with month 'SOS'; what is already on an open SOS order is shown as
     ordered and not raised again. A Now banner shows while anything is low.

  THE STRENGTH LINKS. A medicine logged with a choice of strengths could not
  be counted at all (v3.6.0 onwards). Now each strength label is linked to the
  pack it comes out of, with a unit count ("290" = two of the 145 pack), in a
  new table stock_links. A dose logged as "145 + 72" takes one from each
  linked pack. The packs become ordinary per-dose stock -- counted, alerted
  when under 7 and 3 days, and carried on the monthly order at
  max(14-day use x 40, keep). The variant medicine itself stays untracked and
  says where it is counted. A "Link strengths" form sits on its stock row.

One new table (SCHEMA, no schema_version bump), no column changes. The monthly
arithmetic is v3.19.0's unchanged for scheduled medicines. Requires v3.19.0
(GUTLOG_V3190_ORDER). Anchor-verified, idempotent, refuses Jinja-breaking
tokens in new text, compile-checked, .bak before write, self-restoring,
--reverse for the negative control. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
PREV = "GUTLOG_V3190_ORDER"
MARKER = "GUTLOG_V3200_PIPES"

A00 = r'''  created TEXT, received_at TEXT DEFAULT '', lines TEXT DEFAULT '[]', text TEXT DEFAULT '');
'''
N00 = r'''  created TEXT, received_at TEXT DEFAULT '', lines TEXT DEFAULT '[]', text TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS stock_links (
  med_id INTEGER NOT NULL, variant TEXT NOT NULL, stock_med_id INTEGER NOT NULL,
  units REAL DEFAULT 1, PRIMARY KEY (med_id, variant));
'''

A01 = r'''GUTLOG_V3180_HONEST GUTLOG_V3190_ORDER
'''
N01 = r'''GUTLOG_V3180_HONEST GUTLOG_V3190_ORDER GUTLOG_V3200_PIPES
'''

A02 = r'''        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False})
        if (l["variants"] or "").strip():
            s["variants"] = True
'''
N02 = r'''        s = sched.setdefault(l["med_id"], {"units": 0.0, "variants": False, "labels": []})
        if (l["variants"] or "").strip():
            s["variants"] = True
            s["labels"] += [v.strip() for v in l["variants"].split("|") if v.strip()]
'''

A03 = r'''            by_med.setdefault(mid, []).append(d)

    rows = []
'''
N03 = r'''            by_med.setdefault(mid, []).append(d)

    # GUTLOG_V3200_PIPES -- a dose logged on a medicine whose strengths vary
    # comes out of the stock of the product each strength is linked to:
    # "145 + 72" takes one from each linked pack. The medicine itself stays
    # untracked; its products are counted, alerted and ordered.
    links = _stock_links(con)
    all_names = dict((r["id"], r["name"]) for r in con.execute(
        "SELECT id, name FROM prnmeds").fetchall())
    linked_from = {}
    for vmid, lk in links.items():
        for t in lk.values():
            linked_from[t[0]] = all_names.get(vmid, "")
        for d in list(by_med.get(vmid, [])):
            for part in (d["dose_text"] or "").split("+"):
                t = lk.get(part.strip().lower())
                if not t:
                    continue
                by_med.setdefault(t[0], []).append({
                    "med_id": t[0], "medicine": "", "day": d["day"], "dtime": d["dtime"],
                    "status": d["status"], "sched_id": None, "dose_text": "%g" % t[1]})

    rows = []
'''

A04 = r'''        if s and s["variants"]:
            r["why"] = "strengths vary - not tracked"
            rows.append(r)
            continue
'''
N04 = r'''        r["linked_from"] = linked_from.get(mid, "")
        if s and s["variants"]:
            lk = links.get(mid, {})
            r["variants"] = list(dict((v.lower(), v) for v in s["labels"]).values())
            r["links"] = [{"variant": v, "stock_med_id": t[0], "units": t[1],
                           "name": all_names.get(t[0], "")} for v, t in sorted(lk.items())]
            r["why"] = ("strengths vary - counted on " + ", ".join(
                sorted(set(x["name"] for x in r["links"])))) if lk else \
                "strengths vary - not tracked until its strengths are linked"
            rows.append(r)
            continue
'''

A05 = r'''def _order_plan(tday=None):
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


'''
N05 = r'''def _stock_links(con):
    """GUTLOG_V3200_PIPES -- {medicine whose strengths vary: {strength label:
    (stock medicine id, units per label)}}. A label of two capsules of one
    product is that product with units 2."""
    out = {}
    for r in con.execute("SELECT med_id, variant, stock_med_id, units FROM stock_links").fetchall():
        out.setdefault(r["med_id"], {})[(r["variant"] or "").strip().lower()] = (
            r["stock_med_id"], float(r["units"] or 1))
    return out


def _regular_ids(con, on_day):
    """Medicines on the monthly (daily) pipeline for a given day: those with a
    fixed schedule, and the stock products a variant schedule is linked to.
    Everything else is SOS and goes by its keep-on-hand figure instead."""
    fixed, linked = {}, set()
    links = _stock_links(con)
    for l in con.execute(
            "SELECT med_id, dose_text, variants FROM med_schedule WHERE valid_from<=? "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)", (on_day, on_day)).fetchall():
        if (l["variants"] or "").strip():
            for t in links.get(l["med_id"], {}).values():
                linked.add(t[0])
        else:
            fixed[l["med_id"]] = fixed.get(l["med_id"], 0.0) + _units(l["dose_text"])
    return fixed, linked


def _pack_qty(units, ps, ptype):
    packs = int(math.ceil(units / float(ps))) if ps > 0 else 0
    if packs:
        return packs * ps, packs, "%d %s of %d" % (packs, _pack_word(ptype or "pack", packs), ps)
    return units, 0, "%d units" % units


def _order_plan(tday=None):
    """GUTLOG_V3200_PIPES -- two pipelines. The MONTHLY order carries only the
    daily medicines (fixed schedule, or the product a variant schedule is
    linked to) and tops them up to ORDER_DAYS from the stock expected on the
    1st. SOS medicines never ride the monthly order; they come up on their
    own list (_sos_plan) whenever they run low."""
    con = db()
    tday = tday or date.fromisoformat(today())
    first = _month_after(tday)
    gap = (first - tday).days
    days = _order_days()
    cfg = _order_cfg()
    fills = _last_fills(con)
    sched_units, linked = _regular_ids(con, first.isoformat())
    _now_fixed, now_linked = _regular_ids(con, tday.isoformat())
    lines, skipped = [], []
    for r in _stock_rows():
        c = cfg.get(r["med_id"])
        ptype = (c["pack_type"] if c else "") or ""
        keep = float(c["keep_units"] or 0) if c else 0.0
        if not r["trackable"]:
            if not r.get("links"):
                skipped.append({"name": r["name"],
                                "why": "strengths vary - link its strengths to stock"})
            continue
        su = sched_units.get(r["med_id"], 0.0)
        if (r["can_pillbox"] and su <= 0) or \
                (r["med_id"] in now_linked and r["med_id"] not in linked and su <= 0):
            skipped.append({"name": r["name"], "why": "schedule ends before the 1st"})
            continue
        if su > 0:
            target, basis, use = su * days, "schedule", su
        elif r["med_id"] in linked:
            target, basis, use = max(r["per_day"] * days, keep), "linked", r["per_day"]
            if target <= 0:
                skipped.append({"name": r["name"], "why": "no use logged yet - set a keep figure"})
                continue
        else:
            continue
        if not r["tracked"]:
            skipped.append({"name": r["name"], "why": "not counted yet"})
            continue
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
        ps = int(r["pack_size"] or 0)
        units, packs, qty_txt = _pack_qty(int(math.ceil(need - 1e-9)), ps, ptype)
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


SOS_MONTH = "SOS"


def _sos_reorder_at(keep):
    """Low once stock falls below a third of the keep figure (never below 1):
    keep 15 -> low under 5, keep 4 -> under 2, keep 1 -> at 0."""
    return max(1, int(math.ceil(keep / 3.0)))


def _sos_plan():
    """SOS medicines, judged on stock NOW against their keep-on-hand figure.
    A medicine already in an open SOS order is shown as ordered, not again."""
    con = db()
    cfg = _order_cfg()
    fixed, linked = _regular_ids(con, today())
    s = _order_saved(SOS_MONTH)
    on_order = set()
    if s and s["status"] == "OPEN":
        on_order = set(l["med_id"] for l in s["lines"])
    lines, ordered, nokeep, uncounted = [], [], [], []
    for r in _stock_rows():
        mid = r["med_id"]
        if not r["trackable"] or mid in fixed or mid in linked:
            continue
        c = cfg.get(mid)
        keep = float(c["keep_units"] or 0) if c else 0.0
        ptype = (c["pack_type"] if c else "") or ""
        if keep <= 0:
            if r["tracked"]:
                nokeep.append(r["name"])
            continue
        if not r["tracked"]:
            uncounted.append(r["name"])
            continue
        at = _sos_reorder_at(keep)
        if r["current"] >= at:
            continue
        if mid in on_order:
            ordered.append(r["name"])
            continue
        ps = int(r["pack_size"] or 0)
        units, packs, qty_txt = _pack_qty(int(math.ceil(keep - r["current"] - 1e-9)), ps, ptype)
        lines.append({"med_id": mid, "name": r["name"], "basis": "keep",
                      "current": r["current"], "keep": keep, "reorder_at": at,
                      "units": units, "packs": packs, "pack_size": ps,
                      "pack_type": ptype, "qty": qty_txt})
    text = "Medicines needed (running low)\n" + "\n".join(
        "%d. %s - %s" % (i + 1, l["name"], l["qty"]) for i, l in enumerate(lines))
    return {"lines": lines, "ordered": ordered, "nokeep": nokeep, "uncounted": uncounted,
            "text": text if lines else "", "saved": s}


'''

A06 = r'''@app.route("/api/order")
@login_required
def api_order():
    p = _order_plan()
    p["saved"] = _order_saved(p["month"])
    p["due"] = bool(p["last_week"] and not p["saved"] and p["lines"])
    p["pack_types"] = list(PACK_TYPES)
    return jsonify(p)


'''
N06 = r'''@app.route("/api/order")
@login_required
def api_order():
    p = _order_plan()
    p["saved"] = _order_saved(p["month"])
    p["due"] = bool(p["last_week"] and not p["saved"] and p["lines"])
    p["pack_types"] = list(PACK_TYPES)
    p["sos"] = _sos_plan()  # GUTLOG_V3200_PIPES
    return jsonify(p)


'''

A07 = r'''# ---- feed: read-only, bearer-gated, loopback consumers (RxGuard, FitLog)
'''
N07 = r'''# GUTLOG_V3200_PIPES -- the SOS list and the strength links
@app.route("/api/order/sos/save", methods=["POST"])
@login_required
def api_order_sos_save():
    p = _sos_plan()
    if not p["lines"]:
        return jsonify(ok=False, err="No SOS medicine is running low."), 400
    s = p["saved"]
    if s and s["status"] == "OPEN":
        lines = s["lines"] + p["lines"]
    else:
        lines = p["lines"]
    text = "Medicines needed (running low)\n" + "\n".join(
        "%d. %s - %s" % (i + 1, l["name"], l["qty"]) for i, l in enumerate(lines))
    blob = json.dumps(lines)
    if s and s["status"] == "OPEN":
        db().execute("UPDATE stock_orders SET lines=?, text=?, created=? WHERE id=?",
                     (blob, text, now_s(), s["id"]))
        oid = s["id"]
    else:
        cur = db().execute("INSERT INTO stock_orders(month, status, created, lines, text) "
                           "VALUES(?,?,?,?,?)", (SOS_MONTH, "OPEN", now_s(), blob, text))
        oid = cur.lastrowid
    db().commit()
    return jsonify(ok=True, id=oid, text=p["text"], n=len(p["lines"]))


def _variant_labels(con, mid):
    labels = []
    for l in con.execute(
            "SELECT variants FROM med_schedule WHERE med_id=? AND variants<>'' "
            "AND (valid_to='' OR valid_to IS NULL OR valid_to>=?)", (mid, today())).fetchall():
        for v in (l["variants"] or "").split("|"):
            v = v.strip()
            if v and v.lower() not in [x.lower() for x in labels]:
                labels.append(v)
    return labels


@app.route("/api/stock/link", methods=["POST"])
@login_required
def api_stock_link():
    d = J()
    con = db()
    mid = _stock_med(d)
    labels = _variant_labels(con, mid) if mid else []
    if not labels:
        return jsonify(ok=False, err="That medicine has no strengths to link."), 400
    want = [x.lower() for x in labels]
    rows = []
    for l in d.get("links") or []:
        v = str(l.get("variant") or "").strip().lower()
        if not l.get("stock_med_id"):
            continue
        tid = _stock_med({"med_id": l.get("stock_med_id")})
        try:
            u = float(l.get("units") or 1)
        except (TypeError, ValueError):
            u = 0
        if v not in want or not tid or tid == mid or not 0.25 <= u <= 10 or _variant_labels(con, tid):
            return jsonify(ok=False, err="Check the links: each strength to another medicine, 0.25 to 10 units."), 400
        rows.append((mid, v, tid, u))
    con.execute("DELETE FROM stock_links WHERE med_id=?", (mid,))
    for r in rows:
        con.execute("INSERT INTO stock_links(med_id, variant, stock_med_id, units) VALUES(?,?,?,?)", r)
    con.commit()
    return jsonify(ok=True, n=len(rows))


# ---- feed: read-only, bearer-gated, loopback consumers (RxGuard, FitLog)
'''

A08 = r'''    </div>
    <div class="card" id="stPill">
'''
N08 = r'''    </div>
    <div class="card" id="sosCard">
      <p class="q">Running low - SOS medicines</p>
      <p class="hint sos-sub" style="margin:0 2px 8px"></p>
      <div id="sosLines"></div>
      <div class="ordbtns">
        <button type="button" class="btn tiny go" id="sosSend" hidden>Send on WhatsApp</button>
        <button type="button" class="btn tiny ghost" id="sosCopy" hidden>Copy</button>
        <button type="button" class="btn tiny go" id="sosRecv" hidden>SOS order received</button>
        <button type="button" class="mini" id="sosRecvUndo" hidden>Undo received</button>
      </div>
      <p class="hint" id="sosSkip" style="margin:8px 2px 0"></p>
    </div>
    <div class="card" id="stPill">
'''

A09 = r'''      <p class="q">Monthly order</p>
'''
N09 = r'''      <p class="q">Monthly order - daily medicines</p>
'''

A10 = r'''async function loadOrderDue(){
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
'''
N10 = r'''async function loadOrderDue(){
  const box=$('#nowOrder');if(!box)return;
  try{
    const j=await jget('/api/order');
    box.innerHTML='';
    const add=(lb,txt)=>{const d=document.createElement('div');
      d.className='stockalert amber';d.innerHTML='<b></b><span></span>';
      d.querySelector('b').textContent=lb;d.querySelector('span').textContent=txt;
      d.onclick=()=>{switchTab('meds');setSeg('meds','stock');};box.appendChild(d);};
    if(j.due)add('Order',j.label+' order is due: '+j.lines.length+
      (j.lines.length===1?' medicine':' medicines')+'. Tap to send.');
    const sl=(j.sos&&j.sos.lines)||[];
    if(sl.length)add('Low','Running low: '+sl.map(l=>l.name).join(', ')+'. Tap to send.');
  }catch(e){}
}
'''

A11 = r'''  ordData=j;
'''
N11 = r'''  ordData=j;
  loadSos(j);
'''

A12 = r'''  loadOrder();
  const list=$('#stList');list.innerHTML='';
'''
N12 = r'''  loadOrder();
  stockCache=j.rows;
  const list=$('#stList');list.innerHTML='';
'''

A13 = r'''  if(!r.trackable){sq.textContent='';ss.textContent=r.why;w.querySelector('.sf').remove();return w;}
'''
N13 = r'''  if(!r.trackable){sq.textContent='';ss.textContent=r.why;w.querySelector('.sf').remove();
    if(r.variants&&r.variants.length){const b=document.createElement('button');b.type='button';
      b.className='btn tiny ghost';b.textContent='Link strengths';b.onclick=()=>linkForm(w,r);
      w.querySelector('.sb').appendChild(b);}
    return w;}
'''

A14 = r'''    if(packTxt(r))bits.push(packTxt(r));
'''
N14 = r'''    if(packTxt(r))bits.push(packTxt(r));
    if(r.linked_from)bits.push('from '+r.linked_from);
'''

A15 = r'''/* ---------- VITALS LOG (GUTLOG_V360_PHASE_C) ---------- */
'''
N15 = r'''/* ---------- TWO PIPELINES (GUTLOG_V3200_PIPES) ----------
   SOS medicines have their own list, raised whenever one falls below a
   third of its keep figure; the monthly card carries daily medicines only.
   A medicine whose strengths vary is linked, strength by strength, to the
   products it is taken from, so those products are counted and ordered. */
let stockCache=[];
function loadSos(j){
  const card=$('#sosCard');if(!card)return;
  const p=j.sos||{lines:[],ordered:[],nokeep:[],uncounted:[]};
  const s=p.saved,open=s&&s.status==='OPEN',got=s&&s.status==='RECEIVED';
  const sub=card.querySelector('.sos-sub');
  const bits=[];
  if(p.lines.length)bits.push(p.lines.length+(p.lines.length===1?' medicine is':' medicines are')+' below a third of its keep figure.');
  else bits.push('Nothing running low.');
  if(open)bits.push('On order since '+s.created.slice(0,10)+': '+s.lines.map(l=>l.name).join(', ')+'.');
  sub.textContent=bits.join(' ');
  $('#sosLines').innerHTML=p.lines.map((l,i)=>'<div class="ordl"><b>'+(i+1)+'. '+ordEsc(l.name)+
    '</b><span class="oq">'+ordEsc(l.qty)+'</span><div class="od">'+fmtQ(l.current)+' left, keep '+
    fmtQ(l.keep)+(l.pack_size>0?'':' - set its pack size')+'</div></div>').join('');
  $('#sosSend').hidden=!p.lines.length;$('#sosCopy').hidden=!p.lines.length;
  $('#sosRecv').hidden=!open;$('#sosRecvUndo').hidden=!got;
  const sk=[];
  if(p.uncounted.length)sk.push('Not counted yet: '+p.uncounted.join(', '));
  if(p.nokeep.length)sk.push('No keep figure (tap Pack): '+p.nokeep.join(', '));
  $('#sosSkip').textContent=sk.join(' · ');
  const save=async()=>{try{await post('/api/order/sos/save',{});}catch(err){toast(err.message);}
    loadOrder();loadOrderDue();};
  $('#sosSend').onclick=()=>{window.open('https://wa.me/?text='+encodeURIComponent(p.text),'_blank');save();};
  $('#sosCopy').onclick=()=>{
    ordCopyText(p.text).then(ok=>toast(ok?'Copied. Paste it in WhatsApp.':'Copy failed - use Send on WhatsApp'));
    save();
  };
  $('#sosRecv').onclick=async()=>{
    if(!confirm('Add everything on the SOS order to stock?'))return;
    try{const r=await post('/api/order/received',{id:s.id});
      toast('Added to stock: '+r.n+(r.n===1?' medicine':' medicines'));
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
  $('#sosRecvUndo').onclick=async()=>{
    if(!confirm('Take the last SOS order back out of stock?'))return;
    try{await post('/api/order/received/undo',{id:s.id});toast('Received undone');
      loadStock();loadStockAlerts();loadOrderDue();}
    catch(err){toast(err.message);}
  };
}
function linkForm(w,r){
  const old=w.querySelector('.pk');if(old){old.remove();return;}
  const opts=stockCache.filter(x=>x.trackable&&x.med_id!==r.med_id);
  const cur={};(r.links||[]).forEach(l=>{cur[String(l.variant).toLowerCase()]=l;});
  const f=document.createElement('div');f.className='pk';
  f.innerHTML='<p class="hint" style="margin:0 2px 6px">Which pack each strength comes out of. '+
    'Two capsules of one pack = units 2.</p>'+
    r.variants.map((v,i)=>'<div class="row3 lk" data-v="'+ordEsc(v)+'"><div><p class="lbl">Strength</p><p class="lkv">'+ordEsc(v)+'</p></div>'+
      '<div><p class="lbl">Taken from</p><select class="lk-m"><option value="">-</option>'+
      opts.map(o=>'<option value="'+o.med_id+'">'+ordEsc(o.name)+'</option>').join('')+'</select></div>'+
      '<div><p class="lbl">Units</p><input type="number" min="0.25" max="10" step="0.25" class="lk-u"></div></div>').join('')+
    '<button type="button" class="btn tiny go">Save links</button>';
  f.querySelectorAll('.lk').forEach(row=>{
    const l=cur[row.dataset.v.toLowerCase()];
    row.querySelector('.lk-m').value=l?String(l.stock_med_id):'';
    row.querySelector('.lk-u').value=l?l.units:1;
  });
  f.querySelector('button').onclick=async()=>{
    const links=[...f.querySelectorAll('.lk')].map(row=>({variant:row.dataset.v,
      stock_med_id:row.querySelector('.lk-m').value,units:row.querySelector('.lk-u').value||1}));
    try{await post('/api/stock/link',{med_id:r.med_id,links:links});toast('Links saved');loadStock();}
    catch(err){toast(err.message);}
  };
  w.appendChild(f);
}

/* ---------- VITALS LOG (GUTLOG_V360_PHASE_C) ---------- */
'''

A16 = r'''#ordCard .btn.go,#stList .pk .btn{background:var(--teal);border-color:var(--teal);color:var(--card)}
#ordCard .btn.ghost{background:var(--card);color:var(--teal);border-color:var(--line)}
'''
N16 = r'''#ordCard .btn.go,#sosCard .btn.go,#stList .pk .btn{background:var(--teal);border-color:var(--teal);color:var(--card)}
#ordCard .btn.ghost,#sosCard .btn.ghost{background:var(--card);color:var(--teal);border-color:var(--line)}
.pk .lkv{font-weight:800;font-size:15px;margin:10px 0 0}
.pk .lk+.lk{margin-top:6px}
'''

EDITS = [
    ('schema: stock_links', A00, N00),
    ('version marker', A01, N01),
    ('stock rows: variant labels', A02, N02),
    ('stock rows: linked doses', A03, N03),
    ('stock rows: variant row', A04, N04),
    ('order plan: two pipelines', A05, N05),
    ('api_order: sos', A06, N06),
    ('routes', A07, N07),
    ('html: sos card', A08, N08),
    ('html: monthly title', A09, N09),
    ('js: banner', A10, N10),
    ('js: loadOrder sos', A11, N11),
    ('js: stock cache', A12, N12),
    ('js: link button', A13, N13),
    ('js: linked text', A14, N14),
    ('js block', A15, N15),
    ('css', A16, N16),
]


def build_edits():
    return list(EDITS)


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
    """Reconstruct v3.19.0 for tools/NEGATIVE_CONTROL.py."""
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
                    help="reconstruct v3.19.0 from a patched file (negative control)")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("GutLog two stock pipelines -> v3.20.0")
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
        print("FATAL: this file is not at v3.19.0. Apply that first.")
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
    bak = args.file + ".bak-v3200-" + stamp
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
    print("Next:  python3 test_phase_o.py app.py && python3 test_phase_n.py app.py")
    print("       python3 test_ui_now.py app.py      (browser, service workers off)")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

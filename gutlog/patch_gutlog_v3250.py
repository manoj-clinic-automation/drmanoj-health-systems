#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.24.0 -> v3.25.0  ::  the diet plan in the app (GUTLOG_V3250_PLAN)

A "Today against the plan" card on the Now tab, under the meal card:
protein, calcium, fibre and energy so far today against the targets
(protein per main meal too); this week's plant count against 25 with the
easy additions not yet had; each rotation rule's standing (on track / due /
at limit / over) behind "This week"; and up to five plain suggestions for
the next meal (not the same dal or egg style as yesterday, fish or paneer
falling behind, parantha or sweets at their limit, plants short).

Targets, rules and the food map (plants, calcium, tags per food) come from
diet_plan.local.json beside app.py -- his diet, never committed. Recipe
plants come from the recipes table. Read-only: computed from logged meals
at read time; nothing is blocked or written; no file = the card stays hidden.

Requires v3.24.0 (GUTLOG_V3240_RECIPES). Anchor-verified, idempotent,
compile-checked, refuses Jinja tokens in new page text, .bak before write,
self-restoring, --reverse. Python 3.9.
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
MARKER = "GUTLOG_V3250_PLAN"
PREV = "GUTLOG_V3240_RECIPES"

SERVER = '# ------------------------------------------------------------------ diet plan\n# GUTLOG_V3250_PLAN -- the diet plan, checking itself. Targets, rotation rules\n# and a food map (plants, calcium, tags) come from diet_plan.local.json beside\n# this file (his diet -- never committed). Everything is computed at read\n# time from the meals he logged: today\'s totals against the targets, this\n# week\'s plant count, each rotation rule\'s standing, and a few plain\n# suggestions for the next meal. Advisory only: nothing is blocked, nothing\n# is written. No file = feature off.\ndef _plan_cfg():\n    try:\n        path = os.environ.get("GUTLOG_PLAN_FILE") or os.path.join(BASE, "diet_plan.local.json")\n        with open(path, encoding="utf-8") as fh:\n            return json.load(fh)\n    except (OSError, ValueError):\n        return None\n\n\ndef _plan_food(name, cfg, rec):\n    f = (cfg.get("foods") or {}).get(name) or {}\n    plants = f.get("plants")\n    if plants is None:\n        plants = rec.get(name, [])\n    return {"plants": [p.lower() for p in plants], "ca": f.get("ca"), "tags": f.get("tags") or []}\n\n\ndef _plan_days(start, end):\n    """{day: [meal rows with items]} for start..end inclusive."""\n    out = {}\n    for r in db().execute("SELECT day, mtime, slot, items, protein, kcal, fibre FROM meals "\n                          "WHERE day>=? AND day<=? ORDER BY day, mtime", (start, end)).fetchall():\n        d = dict(r)\n        d["items"] = json.loads(d["items"] or "[]")\n        out.setdefault(d["day"], []).append(d)\n    return out\n\n\ndef _tags_of(meals, cfg, rec):\n    s = set()\n    for m in meals:\n        for it in m["items"]:\n            if (it.get("q") or 0) > 0:\n                s.update(_plan_food(it.get("n") or "", cfg, rec)["tags"])\n    return s\n\n\ndef _has(tags, prefix):\n    return sorted(t for t in tags if t == prefix or t.startswith(prefix))\n\n\ndef _short(tag):\n    return tag.split(":", 1)[1] if ":" in tag else tag\n\n\ndef plan_view(day):\n    cfg = _plan_cfg()\n    if cfg is None:\n        return {"on": False}\n    rec = {}\n    try:\n        for r in db().execute("SELECT name, data FROM recipes").fetchall():\n            rec[r["name"]] = (json.loads(r["data"] or "{}").get("plants") or [])\n    except sqlite3.OperationalError:\n        rec = {}\n    tg = cfg.get("targets") or {}\n    d0 = date.fromisoformat(day)\n    monday = d0 - timedelta(days=d0.weekday())\n    yday = (d0 - timedelta(days=1)).isoformat()\n    days = _plan_days(min(monday, d0 - timedelta(days=1)).isoformat(), day)\n    todays = days.get(day, [])\n\n    # today\'s totals\n    ca, ca_missing, sweets = 0.0, [], 0\n    mains = dict((s, 0.0) for s in cfg.get("main_meals") or [])\n    for m in todays:\n        if m["slot"] in mains:\n            mains[m["slot"]] += m["protein"] or 0\n        has_sweet = False\n        for it in m["items"]:\n            info = _plan_food(it.get("n") or "", cfg, rec)\n            if info["ca"] is None:\n                if it.get("n") not in ca_missing:\n                    ca_missing.append(it.get("n"))\n            else:\n                ca += float(info["ca"]) * float(it.get("q") or 0)\n            if _has(info["tags"], "sweet"):\n                has_sweet = True\n        sweets += 1 if has_sweet else 0\n    today = {"kcal": round(sum(m["kcal"] or 0 for m in todays)),\n             "protein": round(sum(m["protein"] or 0 for m in todays), 1),\n             "fibre": round(sum(m["fibre"] or 0 for m in todays), 1),\n             "calcium": round(ca), "calcium_missing": ca_missing, "sweets": sweets,\n             "mains": [{"slot": k, "protein": round(v, 1), "logged": any(m["slot"] == k for m in todays)}\n                       for k, v in mains.items()], "meals": len(todays)}\n\n    # this week\'s plants\n    quarter = set(p.lower() for p in cfg.get("quarter") or [])\n    plants = {}\n    for dd, ms in days.items():\n        if dd < monday.isoformat():\n            continue\n        for m in ms:\n            for it in m["items"]:\n                for p in _plan_food(it.get("n") or "", cfg, rec)["plants"]:\n                    plants[p] = 0.25 if p in quarter else 1.0\n    points = sum(plants.values())\n    easy = [p for p in cfg.get("easy_adds") or [] if p.lower() not in plants]\n\n    # rules\n    week_days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]\n    left = 6 - d0.weekday()\n    tags_by_day = dict((dd, _tags_of(days.get(dd, []), cfg, rec)) for dd in week_days + [yday])\n    rules, tips = [], []\n    # a week he only started logging late is not "short" -- too little record to say\n    logged_days = sum(1 for dd in week_days if days.get(dd))\n    for r in cfg.get("rules") or []:\n        t, pre, lab = r.get("type"), r.get("tag", ""), r.get("label", "")\n        row = {"id": r.get("id"), "label": lab, "status": "ok", "detail": ""}\n        if t == "no_repeat_days":\n            both = sorted(set(_has(tags_by_day[yday], pre)) & set(_has(tags_by_day[day], pre)))\n            prev = _has(tags_by_day[yday], pre)\n            if both:\n                row.update(status="over", detail="%s yesterday and today" % ", ".join(_short(x) for x in both))\n            elif prev:\n                row["detail"] = "yesterday: " + ", ".join(_short(x) for x in prev)\n                if not _has(tags_by_day[day], pre):\n                    tips.append("%s: not %s again today" % (lab.replace("Same ", "").replace(" two days running", "").capitalize(),\n                                                          " or ".join(_short(x) for x in prev)))\n        elif t == "max_each":\n            cnt = {}\n            for dd in week_days:\n                for x in _has(tags_by_day.get(dd, set()), pre):\n                    cnt[x] = cnt.get(x, 0) + 1\n            over = sorted(x for x, n in cnt.items() if n > r.get("max", 2))\n            full = sorted(x for x, n in cnt.items() if n == r.get("max", 2))\n            if over:\n                row.update(status="over", detail=", ".join("%s %d times" % (_short(x), cnt[x]) for x in over))\n            elif full:\n                row.update(status="full", detail="%s: %d this week, no more" % (", ".join(_short(x) for x in full), r.get("max", 2)))\n        elif t in ("min_days", "max_days"):\n            n = sum(1 for dd in week_days if _has(tags_by_day.get(dd, set()), pre))\n            lo, hi = r.get("min"), r.get("max")\n            row["detail"] = ("%d of %d this week" % (n, lo) if lo else "%d this week" % n) + (" (max %d)" % hi if hi and not lo else "")\n            if hi is not None and n > hi:\n                row["status"] = "over"\n            elif hi is not None and n == hi and lo is None:\n                row["status"] = "full"\n                tips.append("%s: %d this week, none more" % (lab, n))\n            elif lo is not None and n < lo:\n                today_has = bool(_has(tags_by_day[day], pre))\n                need = lo - n\n                avail = left + (0 if today_has else 1)\n                row["status"] = "due" if need <= avail else "short"\n                row["detail"] += " · %d day%s left" % (avail, "" if avail == 1 else "s")\n                if need > avail and logged_days >= 3:\n                    tips.append("%s: %d of %d this week — short; start early next week" % (lab, n, lo))\n                elif need == avail and not today_has:\n                    tips.append("%s: %d of %d this week — have it today" % (lab, n, lo))\n        elif t == "max_per_day":\n            n = sweets if pre == "sweet" else 0\n            if n > r.get("max", 1):\n                row.update(status="over", detail="%d today" % n)\n            elif n == r.get("max", 1):\n                row.update(status="full", detail="done for today")\n                tips.append("Sweet: done for today")\n        rules.append(row)\n    if points < (tg.get("plants_week") or 25) and easy:\n        tips.append("Plants: %g of %d this week — easy adds: %s" % (points, tg.get("plants_week") or 25, ", ".join(easy[:4])))\n    return {"on": True, "day": day, "targets": tg, "today": today,\n            "week": {"start": monday.isoformat(), "plants": sorted(plants), "points": points,\n                     "target": tg.get("plants_week") or 25, "easy": easy},\n            "rules": rules, "tips": tips[:5]}\n\n\n@app.route("/api/plan")\n@login_required\ndef api_plan():\n    day = _valid_day(request.args.get("day")) or today()\n    return jsonify(plan_view(day))\n\n\n'
JS = "/* GUTLOG_V3250_PLAN -- today's totals against the plan, this week's plants,\n   the rotation rules and a few suggestions. Read-only; advisory. */\nfunction planBar(label,val,lo,hi,unit){\n  const w=el('div','pbar2');const top=el('div','pb2t');\n  top.appendChild(el('span','',label));\n  top.appendChild(el('span','',Math.round(val)+(hi&&hi!==lo?(' / '+lo+'–'+hi):(' / '+lo))+' '+unit));\n  w.appendChild(top);const bar=el('div','pb2b');const i=el('i','');\n  i.style.width=Math.min(100,Math.round(100*val/(lo||1)))+'%';if(hi&&val>hi)i.className='over';\n  bar.appendChild(i);w.appendChild(bar);return w;\n}\nasync function loadPlan(){\n  const card=$('#nowPlan');if(!card)return;\n  let j;try{j=await jget('/api/plan?day='+todayISO);}catch(e){return;}\n  if(!j.on){card.style.display='none';return;}\n  card.style.display='';const t=j.today,g=j.targets;\n  $('#planSum').textContent=t.meals?(t.meals+' meal'+(t.meals===1?'':'s')+' logged'):'nothing logged yet';\n  const b=$('#planBars');b.innerHTML='';\n  b.appendChild(planBar('Protein',t.protein,g.protein,null,'g'));\n  b.appendChild(planBar('Calcium',t.calcium,(g.calcium||[1000])[0],(g.calcium||[])[1],'mg'));\n  b.appendChild(planBar('Fibre',t.fibre,(g.fibre||[30])[0],(g.fibre||[])[1],'g'));\n  b.appendChild(planBar('Energy',t.kcal,(g.kcal||[1850])[0],(g.kcal||[])[1],'kcal'));\n  const mm=t.mains.filter(m=>m.logged).map(m=>m.slot+' '+Math.round(m.protein)+' g');\n  if(mm.length)b.appendChild(el('p','hint','Protein by meal (aim '+g.protein_meal+'+ g): '+mm.join(' · ')));\n  if(t.calcium_missing.length)b.appendChild(el('p','hint','No calcium value yet for: '+t.calcium_missing.join(', ')));\n  b.appendChild(el('p','hint','This week: '+j.week.points+' of '+j.week.target+' plants'));\n  const tp=$('#planTips');tp.innerHTML='';\n  if(j.tips.length){tp.appendChild(el('p','lbl','For the next meal'));const u=el('ul','plantips');j.tips.forEach(x=>u.appendChild(el('li','',x)));tp.appendChild(u);}\n  const wk=$('#planWeek');wk.innerHTML='';\n  const ul=el('ul','planrules');\n  j.rules.forEach(r=>{const li=el('li','pr-'+r.status);li.appendChild(el('span','',r.label));\n    li.appendChild(el('span','',({ok:'on track',due:'due',short:'short',full:'at limit',over:'over'})[r.status]+(r.detail?' · '+r.detail:'')));ul.appendChild(li);});\n  wk.appendChild(ul);\n  wk.appendChild(el('p','hint','Plants this week: '+(j.week.plants.join(', ')||'none yet')));\n  $('#planMore').onclick=()=>{const o=wk.style.display==='none';wk.style.display=o?'':'none';$('#planMore').textContent=o?'Hide the week':'This week';};\n}\n"
CSS = '/* GUTLOG_V3250_PLAN */\n#nowPlan .dwtop{display:flex;align-items:center;gap:10px;margin:0 0 10px}\n#nowPlan .dwtop .q{margin:0}#nowPlan .fs{margin-left:auto;font-size:14px;color:var(--muted)}\n.pbar2{margin:0 0 9px}.pb2t{display:flex;justify-content:space-between;font-size:15px;margin:0 0 4px}\n.pb2t span:last-child{color:var(--muted)}\n.pb2b{height:8px;border-radius:99px;background:var(--line);overflow:hidden}\n.pb2b i{display:block;height:100%;background:var(--teal);border-radius:99px}.pb2b i.over{background:var(--amber)}\n.plantips{margin:4px 0 0;padding-left:20px;font-size:15px}.plantips li{margin:0 0 5px}\n.planrules{list-style:none;margin:8px 0;padding:0}\n.planrules li{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:15px}\n.planrules li span:last-child{color:var(--muted);text-align:right}\n.planrules li.pr-over span:last-child,.planrules li.pr-short span:last-child,.planrules li.pr-due span:last-child{color:var(--amber);font-weight:700}\n'
HTML = '  <div class="card" id="nowPlan" style="display:none">\n    <div class="dwtop"><p class="q">Today against the plan</p><span class="fs" id="planSum"></span></div>\n    <div id="planBars"></div>\n    <div id="planTips"></div>\n    <button type="button" class="btn ghost" id="planMore" style="margin-top:8px">This week</button>\n    <div id="planWeek" style="display:none"></div>\n  </div>\n\n'

EDITS = [
    ("version", "GUTLOG_V3230_CONTEXT GUTLOG_V3240_RECIPES\n",
     "GUTLOG_V3230_CONTEXT GUTLOG_V3240_RECIPES " + MARKER + "\n"),
    ("server", "# ------------------------------------------------------------------ PRN doses\n",
     SERVER + "# ------------------------------------------------------------------ PRN doses\n"),
    ("css", ".rcof ul{margin:6px 0 0;padding-left:20px;font-size:15px}\n",
     ".rcof ul{margin:6px 0 0;padding-left:20px;font-size:15px}\n" + CSS),
    ("html", '    <div id="mealToday"></div>\n  </div>\n',
     '    <div id="mealToday"></div>\n  </div>\n' + HTML),
    ("js fn", "/* GUTLOG_V3240_RECIPES -- the recipe book in the Meals tab. */\n",
     JS + "/* GUTLOG_V3240_RECIPES -- the recipe book in the Meals tab. */\n"),
    ("js call nocards", "  if(!MC.cards.length){$('#nowMeal').style.display='none';return;}\n",
     "  if(!MC.cards.length){$('#nowMeal').style.display='none';loadPlan();return;}\n"),
    ("js call", "  mcRender();\n}\nfunction mcTab(", "  mcRender();\n  loadPlan();\n}\nfunction mcTab("),
]
PAGE_TEXT = [JS, CSS, HTML]


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
    src = read(a.file)
    if a.reverse:
        if MARKER not in src:
            print("FATAL: not patched")
            return 1
        out = src
        for label, old, new in reversed(EDITS):
            if out.count(new) != 1:
                print("REVERSE FAILED: " + label)
                return 1
            out = out.replace(new, old, 1)
        if MARKER in out or PREV not in out:
            print("REVERSE FAILED: marker state")
            return 1
        write(a.reverse, out)
        py_compile.compile(a.reverse, doraise=True)
        print("reconstructed " + PREV + " -> " + a.reverse)
        return 0
    print("=" * 66)
    print("GutLog diet plan on the Now tab -> v3.25.0")
    print("file : " + a.file)
    print("=" * 66)
    for t in PAGE_TEXT:
        if re.search(r"\{[{%#]", t):
            print("FATAL: a Jinja token in new page text (CLAUDE.md 5b). Nothing written.")
            return 1
    if MARKER in src:
        print("Already patched. Nothing to do.")
        return 0
    if PREV not in src:
        print("FATAL: this file is not at v3.24.0.")
        return 1
    bad = [l for l, o, n in EDITS if src.count(o) != 1]
    print("anchors: %d/%d matched" % (len(EDITS) - len(bad), len(EDITS)))
    if bad:
        print("ANCHOR FAILURES: " + ", ".join(bad) + ". Nothing written.")
        return 1
    if a.check:
        print("All anchors OK.")
        return 0
    out = src
    for l, o, n in EDITS:
        out = out.replace(o, n, 1)
    d = tempfile.mkdtemp()
    f = os.path.join(d, "cand.py")
    write(f, out)
    try:
        py_compile.compile(f, doraise=True)
        print("compile check: OK")
    except py_compile.PyCompileError as exc:
        print("COMPILE FAILED, nothing written:\n" + str(exc))
        return 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
    bak = a.file + ".bak-v3250-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

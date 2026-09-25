#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.38.0 -> v3.39.0  ::  GUTLOG_V3390_KITCHENBY -- who gave the recipe.

WHY: the Family Kitchen is opening to relatives who need only the recipe
book (kitchen members, Kitchen 1.1.0). Nobody approves recipes any more --
the person who captured one publishes it -- so every card must say clearly
whose it is, everywhere it appears, including the meal it becomes.

WHAT
  * "Recipe by <Name>" under the title on every list card and on the recipe
    card, with the date added; "Shared by <Name> · source: <site>" with the
    link for a web capture, and the original photo, viewable, for a photo
    capture; "N versions — by A, B, C" when the same dish has versions;
    ratings and notes with the rater's name.
  * A logged serving is named "<dish> · recipe by <Name>".
  * "By person": tap a name to see their recipes.
  * The owner's safety valve: "Hide from the Kitchen" on a card (never
    delete; the contributor still sees it, with the note), and a "Hidden"
    chip to find them again. Nobody else gets the button (the Kitchen
    refuses anyone but the owner's token).
  * The Family page lists kitchen members -- name, last visit, recipes
    added -- from the Kitchen's own /api/members (owner token). No caretaker
    access: there is nothing of theirs to care for.

No schema change here (the Kitchen's own database migrates itself).
Anchor-verified, idempotent, compile-checked, .bak, self-restoring,
--reverse, refuses Jinja tokens. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import shutil
import sys
import tempfile

TARGET = "/root/gutlog/app.py"
MARKER = "GUTLOG_V3390_KITCHENBY"
PREV = "GUTLOG_V3380_KITCHEN"
VERSION = "3.39.0"

E = []
E.append(("header",
          'GUTLOG_V3380_KITCHEN -- the Family Kitchen: shared recipes, personal cards, Recipe Inbox.\n',
          'GUTLOG_V3380_KITCHEN -- the Family Kitchen: shared recipes, personal cards, Recipe Inbox.\n'
          'GUTLOG_V3390_KITCHENBY -- "Recipe by <Name>" on every card, list and logged meal; By person; '
          'the owner can hide; kitchen members on the Family page.\n'))
E.append(("version", 'APP_VERSION = "3.38.0"   # GUTLOG_V3380_KITCHEN ',
          'APP_VERSION = "3.39.0"   # GUTLOG_V3390_KITCHENBY GUTLOG_V3380_KITCHEN '))

E.append(("list params",
          '    q = dict((k, request.args.get(k)) for k in ("q", "grp", "sort") if request.args.get(k))\n',
          '    q = dict((k, request.args.get(k)) for k in ("q", "grp", "sort", "by", "show") if request.args.get(k))\n'))

E.append(("people, attach, hide routes",
          '@app.route("/api/kitchen/recipe/<int:rid>")\n@login_required\ndef api_kitchen_recipe(rid):\n',
          '@app.route("/api/kitchen/people")\n@login_required\ndef api_kitchen_people():\n'
          '    """GUTLOG_V3390_KITCHENBY -- everyone with a recipe in the pool, for "By person"."""\n'
          '    st, j = kitchen_call("GET", "/api/people")\n'
          '    return jsonify(j), (st or 502)\n\n\n'
          '@app.route("/api/kitchen/recipe/<int:rid>/attach")\n@login_required\ndef api_kitchen_recipe_attach(rid):\n'
          '    """The original photo a recipe was captured from, viewable on its card."""\n'
          '    st, (b, ct) = kitchen_call("GET", "/api/recipes/%d/attach" % rid, raw=True) \\\n'
          '        if _secret_file(KITCHEN_TOKEN_FILE) else (0, (b"", ""))\n'
          '    if st != 200:\n'
          '        abort(404)\n'
          '    r = Response(b, mimetype=(ct or "application/octet-stream").split(";")[0])\n'
          '    r.headers["Cache-Control"] = "no-store"\n'
          '    r.headers["X-Content-Type-Options"] = "nosniff"\n'
          '    return r\n\n\n'
          '@app.route("/api/kitchen/recipe/<int:rid>/hide", methods=["POST"])\n@login_required\ndef api_kitchen_hide(rid):\n'
          '    """The owner\'s safety valve: hide from the pool, never delete. The Kitchen\n'
          '    refuses any token but the owner\'s, so a family copy gets 403 here."""\n'
          '    d = J()\n'
          '    st, j = kitchen_call("POST", "/api/recipes/%d/hide" % rid, {"hide": bool(d.get("hide", True)),\n'
          '                                                                  "note": str(d.get("note") or "")[:200]})\n'
          '    return jsonify(j), (st or 502)\n\n\n'
          '@app.route("/api/kitchen/recipe/<int:rid>")\n@login_required\ndef api_kitchen_recipe(rid):\n'))

E.append(("logged meal name",
          '    item = {"n": ("Family Kitchen: " + j["recipe"]["name"])[:80], "q": n,\n',
          '    item = {"n": ("Family Kitchen: " + j["recipe"]["name"] + " \\u00b7 recipe by "\n'
          '                  + (j["recipe"].get("added_by") or "someone"))[:120], "q": n,\n'))

E.append(("css by",
          ".flag{color:var(--bad)}.was{text-decoration:line-through;color:var(--mut)}\n",
          ".flag{color:var(--bad)}.was{text-decoration:line-through;color:var(--mut)}\n"
          ".by{color:var(--acc);font-weight:600;margin:2px 0 4px;font-size:15px}.ver a{margin-right:8px}\n"))

E.append(("state", "let ST={tab:'browse',grp:'',sort:'az',q:''};\n",
          "let ST={tab:'browse',grp:'',sort:'az',q:'',by:'',canHide:false};\n"))

E.append(("sort chips and by person",
          "  [['az','All'],['top','Top rated'],['new','New this week'],['fav','Family favourites']].forEach(([k,l])=>{\n"
          "    const c=el('button','chip'+(ST.sort===k?' sel':''),l);c.onclick=()=>{ST.sort=k;browse();};so.appendChild(c);});\n"
          "  v.appendChild(so);\n"
          "  let j;try{j=await jget('/api/kitchen/recipes?sort='+ST.sort+'&q='+encodeURIComponent(ST.q)+'&grp='+encodeURIComponent(ST.grp));}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}\n"
          "  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}\n",
          "  /* GUTLOG_V3390_KITCHENBY -- By person; Hidden (owner only) */\n"
          "  [['az','All'],['top','Top rated'],['new','New this week'],['fav','Family favourites'],['by','By person']].concat(ST.canHide?[['hidden','Hidden']]:[]).forEach(([k,l])=>{\n"
          "    const c=el('button','chip'+(ST.sort===k?' sel':''),l);c.onclick=()=>{ST.sort=k;if(k!=='by')ST.by='';browse();};so.appendChild(c);});\n"
          "  v.appendChild(so);\n"
          "  if(ST.sort==='by'){let p;try{p=await jget('/api/kitchen/people');}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}\n"
          "    const pc=el('div','chips');pc.style.marginTop='8px';\n"
          "    (p.people||[]).forEach(x=>{const c=el('button','chip'+(ST.by===x.slug?' sel':''),x.name+' ('+x.n+')');c.onclick=()=>{ST.by=x.slug;browse();};pc.appendChild(c);});\n"
          "    v.appendChild(pc);if(!ST.by){v.appendChild(el('p','mut','Tap a name to see their recipes.'));return;}}\n"
          "  const sort=(ST.sort==='by')?'az':(ST.sort==='hidden'?'az':ST.sort),show=ST.sort==='hidden'?'hidden':'';\n"
          "  let j;try{j=await jget('/api/kitchen/recipes?sort='+sort+'&q='+encodeURIComponent(ST.q)+'&grp='+encodeURIComponent(ST.grp)+'&by='+encodeURIComponent(ST.by)+'&show='+show);}catch(e){v.appendChild(el('p','mut','Could not load.'));return;}\n"
          "  if(!j.ok){v.appendChild(el('p','mut',j.err||'The Family Kitchen is not reachable.'));return;}\n"
          "  if(!!j.can_hide!==ST.canHide){ST.canHide=!!j.can_hide;tabs();}\n"))

E.append(("list card by",
          "    c.appendChild(el('p','mut',r.grp+' · added by '+(r.added_by||'someone')+(r.new?' · new':'')+\n"
          "      (r.rating.made?(' · made '+r.rating.made+'×'):'')));\n",
          "    c.appendChild(el('p','by','Recipe by '+(r.added_by||'someone')));\n"
          "    c.appendChild(el('p','mut',r.grp+' · added '+(r.added_date||'')+(r.new?' · new':'')+\n"
          "      (r.rating.made?(' · made '+r.rating.made+'×'):'')));\n"
          "    if(r.versions&&r.versions.length>1)c.appendChild(el('p','mut',r.versions.length+' versions — by '+r.versions.map(x=>x.by).join(', ')));\n"
          "    if(r.status&&r.status!=='published')c.appendChild(el('p','flag',r.status==='hidden'?'Hidden from the Kitchen':'Unpublished'));\n"))

E.append(("card by",
          "  c.appendChild(el('p','mut',r.grp+' · serves '+r.servings+(r.serving_text?(' · '+r.serving_text):'')+' · added by '+r.added_by));\n",
          "  c.appendChild(el('p','by','Recipe by '+(r.added_by||'someone')));\n"
          "  c.appendChild(el('p','mut','Added '+(r.added_date||'')+' · '+r.grp+' · serves '+r.servings+(r.serving_text?(' · '+r.serving_text):'')));\n"
          "  if(r.source_url){const p=el('p','mut','Shared by '+r.added_by+' · source: ');const a=el('a','',r.source_site||r.source_url);a.href=r.source_url;a.target='_blank';a.rel='noopener';p.appendChild(a);c.appendChild(p);}\n"
          "  else if(r.has_photo){c.appendChild(el('p','mut','Shared by '+r.added_by+' · from a photo'));}\n"
          "  if(r.has_photo){const im=el('img','att');im.src='/api/kitchen/recipe/'+id+'/attach';im.alt='The original photo';c.appendChild(im);}\n"
          "  if(r.versions&&r.versions.length>1){const p=el('p','ver');p.appendChild(el('span','mut',r.versions.length+' versions — by '+r.versions.map(x=>x.by).join(', ')+'. '));\n"
          "    r.versions.filter(x=>x.id!==r.id).forEach(x=>{const a=el('a','',x.by+\"'s\");a.href='#';a.onclick=(e)=>{e.preventDefault();recipe(x.id);};p.appendChild(a);});c.appendChild(p);}\n"
          "  if(r.status==='hidden')c.appendChild(el('p','flag','Hidden from the Kitchen'+(r.hidden_note?(': '+r.hidden_note):'')));\n"
          "  else if(r.status==='unpublished')c.appendChild(el('p','flag','Unpublished by the person who added it'));\n"))

E.append(("ratings with names and the owner's hide",
          "  (r.ratings||[]).filter(x=>x.note).forEach(x=>c.appendChild(el('p','mut',x.who+': '+x.note)));\n"
          "  const bk=el('button','btn ghost','Back to recipes');bk.onclick=browse;c.appendChild(bk);\n",
          "  (r.ratings||[]).forEach(x=>c.appendChild(el('p','mut',x.who+': '+(x.stars?'★'.repeat(x.stars)+' ':'')+(x.made?'made it ':'')+(x.again?'· would make again ':'')+(x.note?('— '+x.note):''))));\n"
          "  if(r.can_hide){const hb=el('button','btn ghost',r.status==='hidden'?'Show in the Kitchen again':'Hide from the Kitchen');\n"
          "    hb.onclick=async()=>{try{await post('/api/kitchen/recipe/'+id+'/hide',{hide:r.status!=='hidden',note:r.status!=='hidden'?(prompt('Why hide it? (shown to the person who added it)')||''):''});recipe(id);}catch(e){toast(e.message);}};c.appendChild(hb);}\n"
          "  const bk=el('button','btn ghost','Back to recipes');bk.onclick=browse;c.appendChild(bk);\n"))

E.append(("family page kitchen members",
          "    body = \"\".join(rows) or \"<p class='hint'>No family members yet.</p>\"\n"
          "    return Response(FAMILY_PAGE.replace(\"__ROWS__\", body), mimetype=\"text/html\")\n",
          "    # GUTLOG_V3390_KITCHENBY -- kitchen members: recipes only, read from the\n"
          "    # Kitchen with the owner's token. Name, last visit, recipes added; no\n"
          "    # caretaker access, because there is nothing of theirs to care for.\n"
          "    krows = []\n"
          "    st_k, jk = kitchen_call(\"GET\", \"/api/members\")\n"
          "    for k in (jk.get(\"members\") or []) if st_k == 200 else []:\n"
          "        kn = _h.escape(str(k.get(\"name\") or k.get(\"slug\") or \"\"))\n"
          "        if not k.get(\"enabled\", True):\n"
          "            krows.append(\"<div class='fm'><p class='q'>%s</p><p class='hint'>Switched off.</p></div>\" % kn)\n"
          "            continue\n"
          "        krows.append(\"<div class='fm'><p class='q'>%s</p><p>Last visit: %s<br>Recipes added: %d</p>\"\n"
          "                     \"<p class='hint'>Family Kitchen only &mdash; recipes, no health data.</p></div>\"\n"
          "                     % (kn, _h.escape(str(k.get(\"last_seen\") or \"not yet\")), int(k.get(\"recipes\") or 0)))\n"
          "    if krows:\n"
          "        rows.append(\"<p class='q' style='margin-top:18px'>Kitchen members</p>\" + \"\".join(krows))\n"
          "    body = \"\".join(rows) or \"<p class='hint'>No family members yet.</p>\"\n"
          "    return Response(FAMILY_PAGE.replace(\"__ROWS__\", body), mimetype=\"text/html\")\n"))

EDITS = E
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
    print("GutLog: who gave the recipe -> v" + VERSION)
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
    out = src
    for l, o, n in EDITS:
        if out.count(o) != 1:
            print("anchor %s: found %d times, need 1. Nothing written." % (l, out.count(o)))
            return 1
        out = out.replace(o, n, 1)
    print("anchors: %d/%d matched" % (len(EDITS), len(EDITS)))
    if a.check:
        print("All anchors OK.")
        return 0
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
    bak = a.file + ".bak-v3390-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
    print("Next:  python3 family/test_family_d.py gutlog/app.py")
    print("Back:  cp " + bak + " " + a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

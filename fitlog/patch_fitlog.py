#!/usr/bin/env python3
# FitLog v1.0 -> v1.0.1 in-place patcher. Fixes the two pre-3.12 f-string violations.
# Idempotent: safe to run twice. Verifies by compiling the result before writing.
import sys, ast

PATH = sys.argv[1] if len(sys.argv) > 1 else "/root/fitlog/app.py"
src = open(PATH, encoding="utf-8").read()

BS = chr(92)  # backslash, kept out of literals so this patcher itself parses on any Python
U_DOT, U_ARROW, U_X = BS + "u00b7", BS + "u2192", BS + "u2715"

old1 = ('            out += f' + chr(39) + '<div class="card"><h2>{p["title"]}</h2><p class=small>{ev["type"]} '
        + U_DOT + ' intensity {ev["intensity"]}{(" ' + U_DOT + ' " + (ev["mode"] or "")) if ev["type"]=="TRAVEL" else ""}'
        + '</p><ul class="plan">{steps}</ul>{btns}</div>' + chr(39))
new1 = ('            mid = (" ' + U_DOT + ' " + (ev["mode"] or "")) if ev["type"] == "TRAVEL" and ev["mode"] else ""\n'
        + '            out += f' + chr(39) + '<div class="card"><h2>{p["title"]}</h2><p class=small>{ev["type"]} '
        + U_DOT + ' intensity {ev["intensity"]}{mid}</p><ul class="plan">{steps}</ul>{btns}</div>' + chr(39))

Q3 = chr(34) * 3
old2 = ('    lst = "".join(f' + Q3 + "<tr><td>{r['date_start']}{('" + U_ARROW + "'+r['date_end']) if r['date_end']!=r['date_start'] else ''}</td>\n"
        + "      <td>{r['type']}{(' '+(r['mode'] or '')) if r['type']=='TRAVEL' else ''}</td><td>I{r['intensity']}{(' '+str(r['leg_duration_hr'])+'h') if r['type']=='TRAVEL' and r['leg_duration_hr'] else ''}</td>\n"
        + '      <td><a href="/events/del/{r' + chr(39) + 'id' + chr(39) + ']}" onclick="return confirm(' + chr(39) + 'Delete event?' + chr(39) + ')">' + U_X + '</a></td></tr>' + Q3 + ' for r in rows)')
old2 = old2.replace('{r' + chr(39) + 'id', '{r[' + chr(39) + 'id')
new2 = ('    lst = ""\n'
        + '    for r in rows:\n'
        + '        drange = r["date_start"] + (("' + U_ARROW + '" + r["date_end"]) if r["date_end"] != r["date_start"] else "")\n'
        + '        tmode = (" " + (r["mode"] or "")) if r["type"] == "TRAVEL" else ""\n'
        + '        leg = (" " + str(r["leg_duration_hr"]) + "h") if r["type"] == "TRAVEL" and r["leg_duration_hr"] else ""\n'
        + '        lst += f' + Q3 + "<tr><td>{drange}</td><td>{r['type']}{tmode}</td><td>I{r['intensity']}{leg}</td>\n"
        + '      <td><a href="/events/del/{r' + chr(39) + ']}" onclick="return confirm(' + chr(39) + 'Delete event?' + chr(39) + ')">' + U_X + '</a></td></tr>' + Q3)
new2 = new2.replace('{r' + chr(39) + ']}', '{r[' + chr(39) + 'id' + chr(39) + ']}')

changed = 0
if old1 in src:
    src = src.replace(old1, new1); changed += 1
elif "mid = " in src:
    print("patch 1: already applied")
else:
    print("patch 1: ANCHOR NOT FOUND - file differs from expected v1.0"); sys.exit(1)

if old2 in src:
    src = src.replace(old2, new2); changed += 1
elif "drange = " in src:
    print("patch 2: already applied")
else:
    print("patch 2: ANCHOR NOT FOUND - file differs from expected v1.0"); sys.exit(1)

# must compile on THIS python before we write anything
try:
    ast.parse(src)
except SyntaxError as e:
    print("FATAL: patched source does not compile:", e); sys.exit(1)

if changed:
    open(PATH + ".bak", "w", encoding="utf-8").write(open(PATH, encoding="utf-8").read())
    open(PATH, "w", encoding="utf-8").write(src)
print("PATCH OK: " + str(changed) + " change(s) applied, compile clean, backup at " + PATH + ".bak")

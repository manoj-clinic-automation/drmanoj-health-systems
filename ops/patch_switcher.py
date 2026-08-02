#!/usr/bin/env python3
# Switcher-bar patcher: adds RxGuard | GutLog | FitLog cross-links to both apps.
# Anchor-verified, compiles result before writing, .bak rollback, idempotent.
# Run once on the server:  python3 /root/patch_switcher.py
import ast, sys

def patch(path, edits, marker):
    src = open(path, encoding="utf-8").read()
    if marker in src:
        print(path + ": already patched, skipping")
        return True
    for old, new in edits:
        if old not in src:
            print(path + ": ANCHOR NOT FOUND - file differs from expected. NOT modified.")
            print("  missing: " + old[:70].replace("\n", "|"))
            return False
        if src.count(old) != 1:
            print(path + ": anchor not unique. NOT modified.")
            return False
    out = src
    for old, new in edits:
        out = out.replace(old, new)
    try:
        ast.parse(out)
    except SyntaxError as e:
        print(path + ": FATAL patched source does not compile: " + str(e))
        return False
    open(path + ".bak", "w", encoding="utf-8").write(src)
    open(path, "w", encoding="utf-8").write(out)
    print(path + ": PATCHED OK (" + str(len(edits)) + " edit(s), compile clean, backup at .bak)")
    return True

# ---------- RxGuard: 'Apps' group inside its own sidebar nav ----------
RX = "/root/rxguard/app.py"
rx_old = ' <div class="brand">RxGuard</div>\n'
rx_new = (' <div class="brand">RxGuard</div>\n'
          ' <div class="grp">Apps</div>\n'
          ' <a href="https://health.dr-manoj.in">GutLog &#8599;</a>\n'
          ' <a href="https://fit.dr-manoj.in">FitLog &#8599;</a>\n')
rx_ok = patch(RX, [(rx_old, rx_new)], 'href="https://fit.dr-manoj.in">FitLog')

# ---------- GutLog: pill strip above header on both authed templates ----------
GL = "/root/gutlog/app.py"
BAR = ('<div style="display:flex;gap:8px;padding:8px 12px 4px;font-size:13.5px">\n'
       '<a href="https://rx.dr-manoj.in" style="padding:4px 12px;border-radius:14px;'
       'background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">RxGuard</a>\n'
       '<a href="/" style="padding:4px 12px;border-radius:14px;background:#0B6E6E;'
       'color:#fff;text-decoration:none;font-weight:700">GutLog</a>\n'
       '<a href="https://fit.dr-manoj.in" style="padding:4px 12px;border-radius:14px;'
       'background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">FitLog</a>\n'
       '</div>\n')
gl_main_old = '</style></head><body>\n<header><h1>Gut<b>Log</b></h1><span class="v">v3</span>'
gl_main_new = '</style></head><body>\n' + BAR + '<header><h1>Gut<b>Log</b></h1><span class="v">v3</span>'
gl_acct_old = '</style></head><body>\n<header><h1>Gut<b>Log</b> - Account</h1>'
gl_acct_new = '</style></head><body>\n' + BAR + '<header><h1>Gut<b>Log</b> - Account</h1>'
gl_ok = patch(GL, [(gl_main_old, gl_main_new), (gl_acct_old, gl_acct_new)],
              'href="https://fit.dr-manoj.in" style=')

print("")
if rx_ok and gl_ok:
    print("ALL OK. Now restart both services (check exact names first):")
    print("  systemctl list-units --type=service | grep -Ei 'rx|gut'")
    print("  systemctl restart <rxguard-service> <gutlog-service>")
    sys.exit(0)
print("One or more patches did not apply - nothing broken (failed files untouched).")
sys.exit(1)

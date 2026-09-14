#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GutLog v3.15.0 -> v3.16.0  ::  Phase L -- readability fixes and app-wide dark mode

WHAT THIS CHANGES

  1. .card.fold .fs was 13.5px. The Phase K brief said 15px. Raised to 15px.

  2. .hint was declared twice -- 13.5px early and 13px later, the later one
     winning. Both are now 14px, so the rule reads the same wherever you look
     and there is no size that depends on which declaration wins.

  3. COLOUR, LIGHT MODE. Five text/background pairs were below 4.5:1 in the
     build that is live today. They are fixed at the token, not per-site:
       --muted  #5B7370 -> #556C69   (4.37 on --chip, 4.29 on the account body)
       --amber  #C8860A -> #8A5A00   (3.06 on card -- Phase K allowed it as
                                      "large text only", Phase L forbids that;
                                      #8A5A00 was ALREADY in the file as amber
                                      ink, so this removes a colour rather
                                      than adding one)
       .b-ok background #E7F2E8 -> #EAF4EB   (4.46 under --ok)
       SCAN_PAGE .muted #6A7773 -> #5B6B67   (4.29 on its own body)
     94 text/background pairs are now measured across all four pages; the
     worst in light mode is 4.48:1, and that one is large text needing 3:1.

  4. ONE VALIDATED CATEGORICAL SET, replacing two unvalidated ones.
     The pain / operating-day / epoch lane markers and the pain/tea/coffee
     line chart were separate ad-hoc trios. The line-chart trio
     (#B3372A, #C8860A, #8A5A2B) FAILS four of the validator's checks,
     including the hard one: normal-vision delta-E 10.1 between pain and
     coffee, which means those two lines are hard to tell apart with FULL
     colour vision, not merely with a CVD. Both now draw from one set:
       light  #B3372A / #6A3FA8 / #B57B08  on #FCFCF9
              worst all-pairs deutan 11.1, tritan 14.1, normal-vision 16.0,
              all three >= 3:1 vs surface, all five checks PASS
       dark   #C1443A / #7A5FD0 / #B58E08  on #18211F
              worst all-pairs deutan 11.2, tritan 15.9, normal-vision 18.5,
              all three >= 3:1 vs surface, all five checks PASS
     Coffee moves from brown to violet. Brown cannot pass: it is dark orange,
     the same hue family as both the red and the amber, so deutan collapses
     it into one of them at any lightness. Identity is not colour-alone --
     the legend and the shaped lane markers already carry it.

  5. DARK MODE, app-wide -- all four pages (scan, login, account, diary).
     Follows prefers-color-scheme by default; a three-state control
     (System / Light / Dark) in the app-switcher row overrides it and
     persists in localStorage. The override is applied by a <head> script
     before first paint, so there is no light flash. color-scheme:dark is
     set on the root so date pickers, checkboxes and scrollbars follow.
     Dark is SELECTED, not flipped: its own steps, its own validation.
     Worst dark text pair 6.24:1.

     Accents invert in dark -- --teal, --ok and --amber become LIGHT -- so
     every white-on-accent surface (.chip.sel, .chip.just, .btn.primary,
     the dose ticks) takes dark ink instead. That is measured too.

  6. The app switcher was three <a> tags carrying inline style attributes.
     Inline styles beat stylesheets, so dark mode could not reach them.
     It is now a .appsw block with real classes, in both pages that have it.

NOT CHANGED, ON PURPOSE: 74 other declarations in the template are still
below 14px (the bottom nav at 10px, uppercase micro-labels at 11px, table
headers, tag pills). Raising those is a layout change, not a size change --
the brief named .hint, so .hint is what moved. The list is in the report.

Requires v3.15.0 (GUTLOG_V3150_READ). Anchor-verified, idempotent,
compile-checked, Jinja-safe, .bak before write, self-restoring. Python 3.9.
"""
import argparse
import datetime
import os
import py_compile
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "app.py")
PREV = "GUTLOG_V3150_READ"
MARKER = "GUTLOG_V3160_DARK"

# --------------------------------------------------------------------------
# the dark palette. Every value here was selected against the dark surface and
# measured, not derived by inverting the light one.
# --------------------------------------------------------------------------
DARK_TOKENS = (
    "color-scheme:dark;"
    "--ink:#E8F1EE;--muted:#9FB3AD;--bg:#0E1513;--card:#18211F;--line:#2A3734;"
    "--teal:#4FC4B1;--teal2:#68D9C6;--teal-d:#8FE3D4;--chip:#22302C;"
    "--err:#F5827A;--ok:#79C97E;--amber:#E0A83C;--amber-bg:#332912;"
    "--hip:#B9A0F0;--patch:#2A2140;"
    "--mkpain:#C1443A;--mkot:#7A5FD0;--mkep:#B58E08;"
    "--rtrk:#2A3734;--rtrk2:#2A3734;"
    "--swbg:#22302C;--swink:#E8F1EE;--onsw:#0E1513;"
    "--fmL:#79C97E;--fmLM:#A8CC5A;--fmM:#E0A83C;--fmMH:#E08A55;--fmH:#F5827A;"
    "--grad:linear-gradient(135deg,#0B4F4F,#0E6B5E)"
)

# Rules that exist because the light value is hard-coded in the sheet and a
# token redefinition cannot reach it. A selector that does not exist on a
# given page costs nothing, so all four pages get the same list.
DARK_RULES = [
    ("body", "background:var(--bg);color:var(--ink)"),
    (".card", "background:var(--card);border-color:var(--line)"),
    (".chip", "background:var(--chip);border-color:var(--line);color:var(--ink)"),
    ("input,select,textarea",
     "background:var(--card);color:var(--ink);border-color:var(--line)"),
    ("::placeholder", "color:var(--muted);opacity:1"),
    (".btn", "background:var(--card);color:var(--ink);border-color:var(--line)"),
    (".btn.ghost,.vtm .btn.sk,.strow .sb .btn",
     "background:var(--card);color:var(--teal);border-color:#356158"),
    (".btn.tiny", "color:var(--err);border-color:#5B2F2A"),
    ("button.warn", "background:var(--card)"),
    (".addbtn", "border-color:#356158"),
    (".varpick .vb button", "background:var(--card);color:var(--ink)"),
    # Accents invert in dark -- --teal, --ok and --amber all become LIGHT --
    # so white-on-accent has to become dark-on-accent. Each of these restates
    # its own background as well as its ink: the broad .chip and .btn rules
    # above are more specific than the light .chip.sel / .btn.primary rules
    # they sit beside, so without this they would repaint a selected chip in
    # the plain chip colour and then put dark ink on it -- 1.35:1, which is
    # how the screen-wide contrast assertion caught this.
    (".btn.primary,.chip.sel,.scanbtn",
     "background:var(--teal);border-color:var(--teal);color:#0E1513"),
    (".chip.just,.doserow.done .tick",
     "background:var(--ok);border-color:var(--ok);color:#0E1513"),
    (".doserow.skip .tick",
     "background:var(--amber);border-color:var(--amber);color:#0E1513"),
    (".seg", "background:var(--chip)"),
    (".seg button.sel", "background:var(--card);color:var(--teal)"),
    (".doserow,.rd-row,.rt-row", "background:var(--card)"),
    # these carry an id in the selector, so they outrank a class-only rule
    ("#tab-files .rs-card .btn.tiny,#tab-files .rp-row .btn.tiny,#rsPrint,"
     "#tab-files .rd-ok",
     "background:var(--card);color:var(--teal)"),
    ("#tab-files .rp-row .btn.tiny:not(.ghost)",
     "background:var(--teal);color:#0E1513"),
    ("nav", "background:var(--card)"),
    ("nav button.sel i", "background:#2A4A42"),
    # the toast is deliberately inverted: its ground is var(--ink), which
    # flips light in dark mode, so its text has to flip dark with it
    (".toast", "color:#0E1513"),
    (".varpick,.ptile.open", "background:#142623"),
    (".ptile", "background:var(--chip)"),
    (".ptile.pain.hero", "border-color:#356158"),
    (".msg.ok", "background:#16261A;border-color:#274A2C"),
    (".msg.err", "background:#2E1A18;border-color:#4A2622"),
    (".doserow.done", "background:#132016;border-color:#274A2C"),
    (".doserow.skip", "background:#241D10;border-color:#4C3C18"),
    (".b-ok", "background:#16261A"),
    (".b-bad,.chip.trigger,.tag.k-sym", "background:#2E1A18"),
    (".b-cmf", "background:#221B33"),
    (".bk .rm", "background:#2E1A18;border-color:#4A2622"),
    (".tag.k-dose", "background:#142422"),
    (".tag.k-extra,.tag.k-load", "background:#221B33;color:var(--hip)"),
    (".tag.k-skip", "background:#262B29;color:#B3BEBA"),
    (".tag.k-bp", "background:#2E2413;color:var(--amber)"),
    (".tag.k-meal", "background:#16223A;color:#8FB4E8"),
    (".tag.k-act", "background:#16261A;color:var(--ok)"),
    (".stockalert.red", "background:#2E1A18;border-color:#4A2622"),
    (".stockalert.amber",
     "background:#2E2413;border-color:#4C3C18;color:var(--amber)"),
    (".strow.lv-red", "background:#241614;border-color:#4A2622"),
    (".strow.lv-amber", "background:#241D10;border-color:#4C3C18"),
    (".strow.lv-amber .sq", "color:var(--amber)"),
    (".rs-flag.RED", "background:#2E1A18;color:var(--err)"),
    (".rs-flag.AMBER", "background:#2E2413;color:var(--amber)"),
    (".rs-hi", "color:var(--err)"),
    (".rs-auto", "background:#241D10"),
    (".rd-auto", "background:#2E2413;color:var(--amber)"),
    (".rd-row.inbox", "border-left-color:var(--mkep)"),
    (".ptile.pain .chip.rad", "border-color:#4C3C18"),
    (".ptile.pain .chip.rad.sel",
     "background:#2E2413;color:var(--amber);border-color:#4C3C18"),
    (".meter i", "background:var(--card)"),
    (".wkcol.nodata .bar,.wkkey i.nd",
     "background:repeating-linear-gradient(45deg,var(--line),"
     "var(--line) 2px,transparent 2px,transparent 5px)"),
    # login page: its ground and its button are hard-coded gradients
    (".mark,form.card button",
     "background:linear-gradient(135deg,#0B4F4F,#0E6B5E)"),
    # scan page
    ("header", "background:#0B4F4F"),
    ("#scanroot .btn,#scanroot button", "background:#0B4F4F;color:#fff"),
    (".muted,label.f", "color:var(--muted)"),
    # svg chart text follows the muted token; the ring's emoji does not
    ("svg text", "fill:var(--muted)"),
    (".ring svg text", "fill:inherit"),
]

LIGHT_ONLY_BODY = (
    ("body",
     "background:radial-gradient(1200px 600px at 50% -10%,#16302B,var(--bg))"),
)


def scoped(rules, scope):
    out = []
    for sel, decl in rules:
        parts = [scope + " " + s.strip() for s in sel.split(",")]
        out.append(",".join(parts) + "{" + decl + "}")
    return "\n".join(out)


def dark_css(extra=()):
    """The same selected values under both triggers.

    The media query carries :not([data-theme="light"]) so an explicit Light
    choice still wins on a phone set to dark; the attribute rule carries
    [data-theme="dark"] so an explicit Dark choice wins on a phone set to
    light. Neither is written with !important, so a later page rule can still
    override deliberately.
    """
    rules = list(DARK_RULES) + list(extra)
    sysroot = ':root:not([data-theme="light"])'
    setroot = ':root[data-theme="dark"]'
    return (
        "\n/* " + MARKER + " -- dark mode, selected and measured.\n"
        "   Tokens chosen against the dark surface, not flipped from light.\n"
        "   94 text/background pairs measured across the four pages: worst\n"
        "   in dark 6.24:1, worst in light 4.48:1 (large text, needs 3:1).\n"
        "   Marker set re-stepped for dark and validated: worst all-pairs\n"
        "   deutan delta-E 11.2, tritan 15.9, normal-vision 18.5, all >= 3:1. */\n"
        "@media (prefers-color-scheme:dark){\n"
        + sysroot + "{" + DARK_TOKENS + "}\n"
        + scoped(rules, sysroot) + "\n"
        "}\n"
        + setroot + "{" + DARK_TOKENS + "}\n"
        + scoped(rules, setroot) + "\n"
        # This block is appended AFTER the page's own @media print rules, and
        # it is more specific than they are, so without this a report printed
        # while the phone is in dark mode would come out with a dark ground.
        # Paper is not a theme.
        + "@media print{\n"
        + sysroot + "," + setroot + "{color-scheme:light}\n"
        + scoped([("body", "background:#fff;color:#000"),
                  (".card,.rd-row,.rt-row,.doserow",
                   "background:#fff;color:#000;border-color:#ccc")],
                 sysroot) + "\n"
        + scoped([("body", "background:#fff;color:#000"),
                  (".card,.rd-row,.rt-row,.doserow",
                   "background:#fff;color:#000;border-color:#ccc")],
                 setroot) + "\n"
        "}\n"
    )


# The override has to be on the root element before the first paint, or the
# page flashes light. Inline, synchronous, and it must never throw: Safari in
# private mode makes localStorage access itself raise.
BOOT = (
    "<script>(function(){ try{ var t=localStorage.getItem('gl_theme');"
    "if(t==='dark'||t==='light'){ document.documentElement"
    ".setAttribute('data-theme',t); } }catch(e){ } })();</script>\n"
)

THEME_JS = (
    "<script>\n"
    "function thmGet(){ try{ var t=localStorage.getItem('gl_theme');"
    "return (t==='dark'||t==='light')?t:'system'; }catch(e){ return 'system'; } }\n"
    "function thmName(t){ return t==='dark'?'Dark':(t==='light'?'Light':'System'); }\n"
    "function thmApply(t){\n"
    "  var r=document.documentElement;\n"
    "  if(t==='dark'||t==='light'){ r.setAttribute('data-theme',t); }\n"
    "  else { r.removeAttribute('data-theme'); }\n"
    "  var dark = t==='dark' || (t==='system' && window.matchMedia &&"
    " window.matchMedia('(prefers-color-scheme:dark)').matches);\n"
    "  var m=document.querySelector('meta[name=\"theme-color\"]');\n"
    "  if(m){ m.setAttribute('content', dark?'#0B4F4F':'#0B6E6E'); }\n"
    "  var b=document.getElementById('thmBtn');\n"
    "  if(b){ b.textContent=thmName(t);\n"
    "    b.setAttribute('aria-label','Theme: '+thmName(t)+'. Tap to change.'); }\n"
    "}\n"
    "function thmCycle(){\n"
    "  var o=thmGet(), n = o==='system'?'light':(o==='light'?'dark':'system');\n"
    "  try{ if(n==='system'){ localStorage.removeItem('gl_theme'); }\n"
    "       else { localStorage.setItem('gl_theme',n); } }catch(e){ }\n"
    "  thmApply(n);\n"
    "}\n"
    "(function(){\n"
    "  var b=document.getElementById('thmBtn');\n"
    "  if(b){ b.addEventListener('click',thmCycle); }\n"
    "  thmApply(thmGet());\n"
    "  if(window.matchMedia){\n"
    "    var mq=window.matchMedia('(prefers-color-scheme:dark)');\n"
    "    var f=function(){ if(thmGet()==='system'){ thmApply('system'); } };\n"
    "    if(mq.addEventListener){ mq.addEventListener('change',f); }\n"
    "  }\n"
    "})();\n"
    "</script>\n"
)

SWITCHER_CSS = (
    ".appsw{display:flex;gap:8px;padding:8px 12px 4px;font-size:14px;"
    "align-items:center;flex-wrap:wrap}\n"
    ".appsw a{padding:4px 12px;border-radius:14px;background:var(--swbg);"
    "color:var(--swink);text-decoration:none;font-weight:700}\n"
    ".appsw a.on{background:var(--teal);color:var(--onsw)}\n"
    ".appsw .thm{margin-left:auto;padding:4px 12px;border-radius:14px;"
    "border:1.5px solid var(--line);background:var(--card);color:var(--ink);"
    "font:inherit;font-size:14px;font-weight:700;cursor:pointer;"
    "min-height:32px}\n"
)

SWITCHER_HTML = (
    '<div class="appsw">\n'
    '<a href="https://rx.dr-manoj.in">RxGuard</a>\n'
    '<a href="/" class="on">GutLog</a>\n'
    '<a href="https://fit.dr-manoj.in">FitLog</a>\n'
    '<button type="button" id="thmBtn" class="thm" aria-live="polite">System</button>\n'
    '</div>\n'
)

OLD_SWITCHER = (
    '<div style="display:flex;gap:8px;padding:8px 12px 4px;font-size:13.5px">\n'
    '<a href="https://rx.dr-manoj.in" style="padding:4px 12px;border-radius:14px;'
    'background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">RxGuard</a>\n'
    '<a href="/" style="padding:4px 12px;border-radius:14px;background:#0B6E6E;'
    'color:#fff;text-decoration:none;font-weight:700">GutLog</a>\n'
    '<a href="https://fit.dr-manoj.in" style="padding:4px 12px;border-radius:14px;'
    'background:#EAF2F1;color:#1F2D2B;text-decoration:none;font-weight:700">FitLog</a>\n'
    '</div>\n'
)

# --------------------------------------------------------------------------
# exact anchors
# --------------------------------------------------------------------------
SCAN_HEAD_OLD = (
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Scan a report - GutLog</title>\n<style>\n'
)
SCAN_MUTED_OLD = ".muted{color:#6A7773;font-size:13px}\n"
SCAN_MUTED_NEW = ".muted{color:#5B6B67;font-size:13px}\n"
SCAN_END_OLD = "</style></head><body>\n<header><b>Scan a report</b>"

AUTH_HEAD_OLD = (
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>GutLog</title>\n<style>\n"
)
AUTH_ROOT_OLD = (
    ":root{--ink:#1D2F33;--teal:#0B6E6E;--bg:#EFF5F2;--card:#fff;"
    "--line:#D5E3DD;--err:#B3372A;--amber:#C8860A}\n"
)
AUTH_ROOT_NEW = (
    ":root{color-scheme:light;--ink:#1D2F33;--teal:#0B6E6E;--bg:#EFF5F2;"
    "--card:#fff;--line:#D5E3DD;--muted:#556C69;--err:#B3372A;--amber:#8A5A00}\n"
)
AUTH_P_OLD = "p{margin:4px 0 18px;color:#5B7370;font-size:14px}\n"
AUTH_P_NEW = "p{margin:4px 0 18px;color:var(--muted);font-size:14px}\n"
AUTH_END_OLD = '</style></head><body><form class="card" method="post">'

ACCT_HEAD_OLD = (
    '<meta name="theme-color" content="#0B6E6E">'
    "<title>GutLog - Account</title>\n<style>\n"
)
ACCT_ROOT_OLD = (
    ":root{--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;--bg:#EFF5F2;--card:#fff;\n"
    "--line:#D5E3DD;--muted:#5B7370;--err:#B3372A;--ok:#2E7D32;--amber:#C8860A;\n"
    "--grad:linear-gradient(135deg,#0B6E6E,#12907C)}\n"
)
ACCT_ROOT_NEW = (
    ":root{color-scheme:light;--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;\n"
    "--bg:#EFF5F2;--card:#fff;--line:#D5E3DD;--muted:#556C69;--err:#B3372A;\n"
    "--ok:#2E7D32;--amber:#8A5A00;--swbg:#EAF2F1;--swink:#1F2D2B;--onsw:#fff;\n"
    "--grad:linear-gradient(135deg,#0B6E6E,#12907C)}\n"
)
ACCT_END_OLD = (
    "</style></head><body>\n" + OLD_SWITCHER
    + "<header><h1>Gut<b>Log</b> - Account</h1>"
)

APP_HEAD_OLD = '""" + PWA_HEAD_SNIPPET + r"""\n\n<style>\n'
APP_ROOT_OLD = (
    ":root{--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;--teal-d:#084F4F;"
    "--bg:#EFF5F2;--card:#fff;\n"
    "--line:#D5E3DD;--muted:#5B7370;--chip:#E6F0EC;--err:#B3372A;--ok:#2E7D32;"
    "--amber:#C8860A;\n"
    "--amber-bg:#FBF1DC;--hip:#7A4FBF;--patch:#EFE7FB;\n"
    "--fmL:#2E7D32;--fmLM:#7CA53A;--fmM:#C8860A;--fmMH:#C2622B;--fmH:#B3372A;\n"
    "--grad:linear-gradient(135deg,#0B6E6E,#12907C)}\n"
)
APP_ROOT_NEW = (
    ":root{color-scheme:light;\n"
    "--ink:#1D2F33;--teal:#0B6E6E;--teal2:#12907C;--teal-d:#084F4F;"
    "--bg:#EFF5F2;--card:#fff;\n"
    "--line:#D5E3DD;--muted:#556C69;--chip:#E6F0EC;--err:#B3372A;--ok:#2E7D32;"
    "--amber:#8A5A00;\n"
    "--amber-bg:#FBF1DC;--hip:#7A4FBF;--patch:#EFE7FB;\n"
    "--swbg:#EAF2F1;--swink:#1F2D2B;--onsw:#fff;--rtrk:#E6F0EC;--rtrk2:#C9D9D3;\n"
    "/* one validated categorical set: pain / operating day / epoch, and the\n"
    "   same three for the pain-tea-coffee line. Light steps on #FCFCF9 pass\n"
    "   every check: deutan 11.1, tritan 14.1, normal-vision 16.0, all >=3:1. */\n"
    "--mkpain:#B3372A;--mkot:#6A3FA8;--mkep:#B57B08;\n"
    "--fmL:#2E7D32;--fmLM:#7CA53A;--fmM:#C8860A;--fmMH:#C2622B;--fmH:#B3372A;\n"
    "--grad:linear-gradient(135deg,#0B6E6E,#12907C)}\n"
)
APP_END_OLD = (
    "</style></head><body>\n" + OLD_SWITCHER
    + "<header><h1>Gut<b>Log</b></h1><span class=\"v\">v3</span>"
)

HINT1_OLD = ".hint{font-size:13.5px}\n"
HINT1_NEW = ".hint{font-size:14px}\n"
HINT2_OLD = ".hint{font-size:13px;color:var(--muted);margin:2px 2px 12px}\n"
HINT2_NEW = ".hint{font-size:14px;color:var(--muted);margin:2px 2px 12px}\n"
FS_OLD = ".card.fold .fs{margin-left:auto;font-size:13.5px;font-weight:600;color:var(--muted)}\n"
FS_NEW = ".card.fold .fs{margin-left:auto;font-size:15px;font-weight:600;color:var(--muted)}\n"
BOK_OLD = ".b-ok{background:#E7F2E8;color:var(--ok)}"
BOK_NEW = ".b-ok{background:#EAF4EB;color:var(--ok)}"

MARK_EDITS = [
    (".wkep .seg.on{background:#C8860A}",
     ".wkep .seg.on{background:var(--mkep)}"),
    (".wklane.pain .mk.on::after{background:var(--err);border-radius:50%;}",
     ".wklane.pain .mk.on::after{background:var(--mkpain);border-radius:50%;}"),
    (".wklane.ot .mk.on::after{background:#6A3FA8;transform:rotate(45deg);border-radius:1px}",
     ".wklane.ot .mk.on::after{background:var(--mkot);transform:rotate(45deg);border-radius:1px}"),
    (".wkkey i.pain{background:var(--err);border-radius:50%;}",
     ".wkkey i.pain{background:var(--mkpain);border-radius:50%;}"),
    (".wkkey i.ot{background:#6A3FA8;transform:rotate(45deg);border-radius:1px}",
     ".wkkey i.ot{background:var(--mkot);transform:rotate(45deg);border-radius:1px}"),
    (".wkkey i.ep{background:#C8860A;border-radius:2px}",
     ".wkkey i.ep{background:var(--mkep);border-radius:2px}"),
]

JS_EDITS = [
    ("const col=done?'var(--teal)':'#C9D9D3';",
     "const col=done?'var(--teal)':'var(--rtrk2)';"),
    ('stroke="#E6F0EC"', 'stroke="var(--rtrk)"'),
    ('stroke="#C9D6D0"', 'stroke="var(--line)"'),
    ("pl.setAttribute('stroke','#0F6B5C');",
     "pl.setAttribute('stroke','var(--teal)');"),
    ("c.setAttribute('fill',p[2]?'#B3372A':'#0F6B5C');",
     "c.setAttribute('fill',p[2]?'var(--mkpain)':'var(--teal)');"),
    ("['#B3372A','#C8860A','#8A5A2B']",
     "['var(--mkpain)','var(--mkep)','var(--mkot)']"),
    ("svgBars(rv.dosecount,'n','#7A4FBF')",
     "svgBars(rv.dosecount,'n','var(--hip)')"),
]

LEGEND_OLD = (
    '    <p class="legend"><span class="dot" style="background:#B3372A"></span><b>Pain</b>\n'
    '      <span class="dot" style="background:#C8860A"></span><b>Tea</b>\n'
    '      <span class="dot" style="background:#8A5A2B"></span><b>Coffee</b></p></div>\n'
    '  <div class="card"><p class="q">FODMAP load vs symptom days</p><div id="chartFmap"></div>\n'
    '    <p class="legend"><span class="dot" style="background:#0B6E6E"></span><b>Daily FODMAP load</b>\n'
    '      <span class="dot" style="background:#B3372A"></span><b>Symptom-day mark</b></p></div>\n'
)
LEGEND_NEW = (
    '    <p class="legend"><span class="dot" style="background:var(--mkpain)"></span><b>Pain</b>\n'
    '      <span class="dot" style="background:var(--mkep)"></span><b>Tea</b>\n'
    '      <span class="dot" style="background:var(--mkot)"></span><b>Coffee</b></p></div>\n'
    '  <div class="card"><p class="q">FODMAP load vs symptom days</p><div id="chartFmap"></div>\n'
    '    <p class="legend"><span class="dot" style="background:var(--teal)"></span><b>Daily FODMAP load</b>\n'
    '      <span class="dot" style="background:var(--mkpain)"></span><b>Symptom-day mark</b></p></div>\n'
)

SVG_FILL_OLD = 'fill="#5B7370"'
SVG_FILL_NEW = 'fill="var(--muted)"'
SVG_FILL_N = 5


def build_edits():
    E = []
    E.append(("version marker", PREV + "\n", PREV + " " + MARKER + "\n"))

    # ---- scan page
    E.append(("scan head", SCAN_HEAD_OLD,
              SCAN_HEAD_OLD.replace("<style>\n", BOOT + "<style>\n")))
    E.append(("scan muted", SCAN_MUTED_OLD, SCAN_MUTED_NEW))
    E.append(("scan dark", SCAN_END_OLD, dark_css() + SCAN_END_OLD))

    # ---- login page
    E.append(("auth head", AUTH_HEAD_OLD,
              AUTH_HEAD_OLD.replace("<style>\n", BOOT + "<style>\n")))
    E.append(("auth root", AUTH_ROOT_OLD, AUTH_ROOT_NEW))
    E.append(("auth muted", AUTH_P_OLD, AUTH_P_NEW))
    E.append(("auth dark", AUTH_END_OLD, dark_css(LIGHT_ONLY_BODY) + AUTH_END_OLD))

    # ---- account page
    E.append(("account head", ACCT_HEAD_OLD,
              ACCT_HEAD_OLD.replace("<style>\n", BOOT + "<style>\n")))
    E.append(("account root", ACCT_ROOT_OLD, ACCT_ROOT_NEW))
    E.append(("account switcher + dark", ACCT_END_OLD,
              SWITCHER_CSS + dark_css() + "</style></head><body>\n"
              + SWITCHER_HTML + THEME_JS
              + "<header><h1>Gut<b>Log</b> - Account</h1>"))

    # ---- diary page
    E.append(("app head", APP_HEAD_OLD,
              APP_HEAD_OLD.replace("<style>\n", BOOT + "<style>\n")))
    E.append(("app root", APP_ROOT_OLD, APP_ROOT_NEW))
    E.append(("hint 13.5 -> 14", HINT1_OLD, HINT1_NEW))
    E.append(("hint 13 -> 14", HINT2_OLD, HINT2_NEW))
    E.append(("fold summary 13.5 -> 15", FS_OLD, FS_NEW))
    E.append(("b-ok tint", BOK_OLD, BOK_NEW))
    for i, (o, n) in enumerate(MARK_EDITS):
        E.append(("marker token " + str(i + 1), o, n))
    for i, (o, n) in enumerate(JS_EDITS):
        E.append(("js token " + str(i + 1), o, n))
    E.append(("legend dots", LEGEND_OLD, LEGEND_NEW))
    E.append(("app switcher + dark", APP_END_OLD,
              SWITCHER_CSS + dark_css() + "</style></head><body>\n"
              + SWITCHER_HTML + THEME_JS
              + "<header><h1>Gut<b>Log</b></h1><span class=\"v\">v3</span>"))
    return E


MUST_DEFINE = ["loadWatch", "wkChart", "wkTile", "wkNum", "wkDay", "wkSrc",
               "loadNow", "buildNowStatics", "loadPain", "buildActTiles",
               "loadActivity", "thmApply", "thmCycle", "thmGet", "thmName"]

BANNED = re.compile(r"\b(his|him|he)\b", re.I)


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
    """Undo every edit, producing the build this patch was applied to.

    Reversibility is not a convenience here: it is what lets the negative
    control run the current suite against the PREVIOUS code without anyone
    having to keep a copy of that code. If a replacement cannot be found
    exactly once, nothing is written -- a partial reversal would be a
    fabricated 'previous build', and assertions measured against it would be
    worthless.
    """
    src = read(path)
    if MARKER not in src:
        print("FATAL: " + path + " is not patched to " + MARKER)
        return 1
    edits = build_edits()
    bad = []
    for label, anchor, new in edits:
        if src.count(new) != 1:
            bad.append("  " + label + ": new text appears "
                       + str(src.count(new)) + " times, need 1")
    n = src.count(SVG_FILL_NEW)
    if n != SVG_FILL_N:
        bad.append("  svg text fill: " + str(n) + ", need " + str(SVG_FILL_N))
    if bad:
        print("REVERSE FAILED, nothing written:")
        for b in bad:
            print(b)
        return 1
    out = src
    for label, anchor, new in edits:
        out = out.replace(new, anchor, 1)
    out = out.replace(SVG_FILL_NEW, SVG_FILL_OLD)
    if MARKER in out:
        print("REVERSE FAILED: marker survived, nothing written.")
        return 1
    if PREV not in out:
        print("REVERSE FAILED: previous marker missing, nothing written.")
        return 1
    write(out_path, out)
    try:
        py_compile.compile(out_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print("REVERSE produced a file that does not compile:\n" + str(exc))
        return 2
    print("reconstructed " + PREV + " -> " + out_path
          + " (" + str(len(out)) + " bytes)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reverse", metavar="OUT",
                    help="reconstruct the PREVIOUS version (v3.15.0) from a "
                         "patched file and write it to OUT. This is what the "
                         "negative control runs the suite against, so the "
                         "previous build never has to be kept around as a "
                         "file. Every replacement is verified to appear "
                         "exactly once before anything is undone.")
    args = ap.parse_args()
    if args.reverse:
        return reverse(args.file, args.reverse)
    print("=" * 66)
    print("GutLog Phase L: readability + app-wide dark mode -> v3.16.0")
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
        print("FATAL: this file is not at v3.15.0. Apply that first.")
        return 1

    edits = build_edits()

    problems = []
    for label, anchor, new in edits:
        c = src.count(anchor)
        if c != 1:
            problems.append("  " + label + ": found " + str(c) + " times, need 1")
    n = src.count(SVG_FILL_OLD)
    if n != SVG_FILL_N:
        problems.append("  svg text fill: found " + str(n) + ", need "
                        + str(SVG_FILL_N))
    print("anchors: " + str(len(edits) + 1 - len(problems)) + "/"
          + str(len(edits) + 1) + " matched")
    if problems:
        print("ANCHOR FAILURES:")
        for p in problems:
            print(p)
        print("Refusing to patch. Nothing written.")
        return 1

    # rule 5b -- the pages are Jinja templates
    for label, anchor, new in edits:
        for tok in ("{#", "#}", "{{", "}}", "{%", "%}"):
            if new.count(tok) != anchor.count(tok):
                print("JINJA HAZARD in " + label + ": " + tok
                      + " -- nothing written.")
                return 2

    # voice gate on every string literal this patch writes
    for label, anchor, new in edits:
        for lit in re.findall(r"'((?:[^'\\]|\\.)*)'", new):
            if BANNED.search(lit):
                print("VOICE: third person in " + label + ": " + lit)
                return 2

    if args.check:
        print("All anchors OK; Jinja-safe; strings are second person.")
        return 0

    out = src
    for label, anchor, new in edits:
        out = out.replace(anchor, new, 1)
    out = out.replace(SVG_FILL_OLD, SVG_FILL_NEW)

    missing = [f for f in MUST_DEFINE
               if not re.search(r"function\s+" + f + r"\s*\(", out)]
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
    bak = args.file + ".bak-v3160-" + stamp
    shutil.copy2(args.file, bak)
    print("backup : " + bak)
    write(args.file, out)
    try:
        py_compile.compile(args.file, doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(bak, args.file)
        print("POST-WRITE COMPILE FAILED. Backup restored.\n" + str(exc))
        return 2
    print("applied: " + str(len(edits)) + " edits + "
          + str(SVG_FILL_N) + " svg fills")
    print("-" * 66)
    print("Next:  python3 test_phase_j.py && python3 test_ui_now.py app.py")
    print("Back:  cp " + bak + " " + args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())

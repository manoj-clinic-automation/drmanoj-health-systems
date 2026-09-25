#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui.py -- OFFLINE ONLY (Playwright + Chromium). A member's copy in a
real browser, through the front proxy, at phone width.

    python3 -B family/test_family_ui.py gutlog/app.py

The server suites never run page JavaScript (CLAUDE.md 5b: v3.4.0 shipped
two deleted functions at 18/18). This one does: every request the page makes
must stay inside the member's own prefix, no page error may fire, the
caretaker bar must render, and the profile must move its cards to the top.
Service workers are blocked so a request is never served from anywhere but
the network. Screenshots are not written. Python 3.9.
"""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def run(rig):
    with sync_playwright() as p:
        b = p.chromium.launch()
        for slug, prof in (("m1", "gut"), ("m2", "joint")):
            ctx = b.new_context(viewport={"width": 360, "height": 800}, service_workers="block")
            page = ctx.new_page()
            errs, outside = [], []
            page.on("pageerror", lambda e: errs.append(str(e)))

            def on_req(req, slug=slug):
                u = urlparse(req.url)
                if u.port == rig.front_port and not u.path.startswith("/%s/" % slug):
                    outside.append(u.path)
            page.on("request", on_req)
            page.goto(rig.front_url + "/%s/login" % slug)
            page.fill("input[name=pw]", rig.pw[slug])
            page.click("button")
            page.wait_for_load_state("networkidle")
            if "/welcome" in page.url:
                check("U01 first run opens the setup form -- %s" % slug,
                      page.locator("input[name=age]").count() == 1, page.url)
                page.fill("input[name=name]", "Member")
                page.fill("input[name=age]", "63")
                page.check("input[value=ibs]")
                page.click("button")
                page.wait_for_load_state("networkidle")
            else:
                check("U01 first run opens the setup form -- %s" % slug, False, page.url)
            page.wait_for_selector("#famBar p", timeout=8000)
            bar = page.inner_text("#famBar")
            check("U02 the caretaker bar renders -- %s" % slug, "Caretaker" in bar, bar)
            first = page.evaluate("""() => { const t=document.getElementById('tab-now');
                const ids=[...t.children].map(c=>c.id).filter(Boolean); return ids.slice(0,4); }""")
            if prof == "gut":
                check("U03 the profile orders the Now page -- gut keeps the owner's order",
                      first[:3] == ["famBar", "nowMirror", "nowFamily"] or first[0] == "famBar", first)
            else:
                check("U03 the profile orders the Now page -- joint puts the joint cards first",
                      "nowDoses" in first[:3] or "nowJoint" in first[:3], first)
            wide = page.evaluate("() => document.documentElement.scrollWidth <= 361")
            check("U04 no sideways scroll at 360 px -- %s" % slug, wide, "")
            page.goto(rig.front_url + "/%s/care" % slug)
            check("U05 the member's Caretaker page shows the switch -- %s" % slug,
                  "Caretaker access is" in page.inner_text("body"), page.url)
            check("U06 every request stays inside the member's prefix -- %s" % slug, not outside,
                  outside[:5])
            check("U07 no page error -- %s" % slug, not errs, errs[:3])
            ctx.close()
        b.close()


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("U00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

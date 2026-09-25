#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui_b.py -- OFFLINE ONLY (Playwright + Chromium). The joint page of
a `joint` member at 300 px, in a real browser, through the front proxy.

    python3 -B family/test_family_ui_b.py gutlog/app.py

No page error, no sideways scroll, the four joint cards shown and first, and
a joint entry saved the way a finger would: tap the joint, tap the score, tap
a trigger, type the minutes, Save. The gut member (m1) shows none of the cards.
Service workers are blocked; no screenshot is written. Python 3.9.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def login(page, rig, slug):
    page.goto(rig.front_url + "/%s/login" % slug)
    page.fill("input[name=pin]", rig.pw[slug])
    page.click("#go")
    page.wait_for_load_state("networkidle")
    if "/passkey/offer" in page.url:   # the Face ID offer, on a device that has it
        try:
            page.click("#no", timeout=4000)
        except Exception:
            pass
        page.wait_for_load_state("networkidle")
    if "/welcome" in page.url:
        page.fill("input[name=age]", "68")
        page.check("input[value=knee_oa]")
        page.click("button")
        page.wait_for_load_state("networkidle")


def run(rig):
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 300, "height": 800}, service_workers="block")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        login(page, rig, "m2")
        page.wait_for_selector("#nowJoint", state="visible", timeout=10000)
        vis = page.evaluate("""() => ['nowJoint','nowJointWatch','nowPainMeds','nowLipid']
            .map(i => { const e=document.getElementById(i); return !!e && e.offsetParent !== null; })""")
        check("V01 the joint page renders at 300 px -- the four joint cards are shown", all(vis), vis)
        first = page.evaluate("""() => [...document.getElementById('tab-now').children]
            .map(c => c.id).filter(Boolean).slice(0, 3)""")
        check("V01 the joint page renders at 300 px -- joint pain is first", first[1:2] == ["nowJoint"], first)
        txt = ""
        try:
            page.click("#jSite .chip:has-text('Knee - L')", timeout=5000)
            page.click("#jScore .chip:has-text('5')", timeout=5000)
            page.click("#jTrig .chip:has-text('stairs')", timeout=5000)
            page.fill("#jStiff", "15")
            page.fill("#jWalk", "10")
            page.click("#jSave")
            page.wait_for_selector("#jList p", timeout=8000)
            txt = page.inner_text("#jList")
        except Exception as exc:
            txt = "step failed: %s" % str(exc).splitlines()[0]
        check("V02 a joint entry saved by tapping shows in the list",
              "Knee - L 5/10" in txt and "stairs" in txt and "walked 10 min" in txt, txt)
        for sel in ("#pmBody p", "#jwBody p", "#lpBody p"):
            try:
                page.wait_for_selector(sel, timeout=8000)
            except Exception:
                pass
        wide = page.evaluate("() => document.documentElement.scrollWidth <= 301")
        check("V03 no sideways scroll at 300 px with the joint cards open", wide,
              page.evaluate("() => document.documentElement.scrollWidth"))
        check("V04 no page error on the joint page", not errs, errs[:3])
        ctx.close()
        ctx = b.new_context(viewport={"width": 360, "height": 800}, service_workers="block")
        page = ctx.new_page()
        login(page, rig, "m1")
        page.wait_for_selector("#famBar p", timeout=8000)
        page.wait_for_timeout(800)
        hidden = page.evaluate("""() => ['nowJoint','nowPainMeds'].every(i => {
            const e=document.getElementById(i); return !e || e.offsetParent === null; })""")
        check("V05 a gut member sees none of the joint cards", hidden, "")
        ctx.close()
        b.close()


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("V00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

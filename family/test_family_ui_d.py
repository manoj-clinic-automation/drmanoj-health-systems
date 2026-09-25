#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui_d.py -- OFFLINE ONLY (Playwright + Chromium). A kitchen
member's Family Kitchen at 300 px, through the front proxy.

    python3 -B family/test_family_ui_d.py gutlog/app.py

Sign-in on the keypad page (the eye shows the PIN); the book lists recipes
with "Recipe by"; a card opens with the attribution and nutrition; the
Inbox opens a captured draft and Publish puts it in the pool under the
member's name; the duplicate offer shows two choices; the Me tab saves a
preference; no page error; no sideways scroll; every request stays inside
/kitchen/k1/. Python 3.9.
"""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import test_family_c as TC  # noqa: E402
import test_family_d as TD  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def run(rig):
    m1 = rig.member_client("m1")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs"})
    _d, r = TC.confirm_text(m1, "m1", "Test lemon rice", [{"item": "rice", "qty": 1, "unit": "cup"},
                                                         {"item": "lemon", "qty": 1, "unit": "medium"}],
                            ["Cook.", "Squeeze."], 2, grp="Rice & roti")
    rig.stamp_kitchen("k1", "Member C")
    tok = rig.kitchen_tokens()
    TC.capture(rig, "k1", tok["capture"]["k1"]["token"], {"text": "Test lemon rice\n1 cup rice\n1 lemon\nCook."})
    k1 = "/kitchen/k1"
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 300, "height": 800}, service_workers="block")
        page = ctx.new_page()
        errs, outside = [], []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("request", lambda q: outside.append(urlparse(q.url).path)
                if urlparse(q.url).port == rig.front_port and not urlparse(q.url).path.startswith(k1 + "/") else None)

        def step(fn):
            try:
                fn()
                return True
            except Exception:
                return False
        page.goto(rig.front_url + k1 + "/login")
        t = page.inner_text("main")
        page.fill("input[name=pin]", rig.pw["k1"])
        page.click("#eye")
        shown = page.get_attribute("#pin", "type") == "text" and page.inner_text("#eye") == "Hide"
        check("X01 the sign-in page names the member and the eye shows the PIN", "Member C — Family Kitchen" in t and shown, t[:120])
        page.click("#go")
        page.wait_for_load_state("networkidle")
        ok_offer = step(lambda: page.wait_for_selector("#offer, #no", state="attached", timeout=8000))
        if "passkey/offer" in page.url:
            page.goto(rig.front_url + k1 + "/")
        ok1 = step(lambda: page.wait_for_selector("#view .card", timeout=10000))
        vt = page.inner_text("#view") if ok1 else ""
        check("X02 the book lists the pool with 'Recipe by' on every card", ok1 and ok_offer and "Test lemon rice" in vt
              and "Recipe by Member A" in vt, vt[:200])
        ok2 = step(lambda: (page.click("#view .card:has-text('Test lemon rice')", timeout=5000),
                            page.wait_for_selector("#view h2", timeout=8000)))
        ct = page.inner_text("#view") if ok2 else ""
        check("X03 a card shows the attribution, the date and nutrition per serving",
              ok2 and "Recipe by Member A" in ct and "Added 20" in ct and "Per serving" in ct and "kcal" in ct, ct[:300])
        ok3 = step(lambda: (page.click(".tab:has-text('Inbox')", timeout=5000),
                            page.wait_for_selector("#view .card", timeout=8000),
                            page.click("#view .card >> nth=0", timeout=5000),
                            page.wait_for_selector("#publish", timeout=8000)))
        rt = page.inner_text("#view") if ok3 else ""
        check("X04 the Inbox opens a draft on the review screen, saying it publishes as 'Recipe by Member C'",
              ok3 and "Recipe by Member C" in rt and page.locator("#view textarea").count() == 2, rt[:200])
        if ok3:
            page.fill("#view input >> nth=0", "Test lemon rice")
            page.fill("#view textarea >> nth=0", "Cook.")
            rows = page.locator("#view .ir")
            if rows.count() == 0:
                page.click("#view button:has-text('+ ingredient')")
            page.fill("#view .ir >> nth=0 >> input >> nth=0", "rice")
            page.fill("#view .ir >> nth=0 >> input >> nth=1", "1")
            page.fill("#view .ir >> nth=0 >> input >> nth=2", "cup")
            page.click("#publish")
        ok4 = step(lambda: page.wait_for_selector("#view button:has-text(\"Publish as Member C's version\")", timeout=8000))
        dt = page.inner_text("#view") if ok4 else ""
        check("X05 a duplicate offers 'Publish as Member C's version' or Cancel", ok4 and "already in the Kitchen" in dt
              and page.locator("#view button:has-text('Cancel')").count() == 1, dt[-200:])
        msgs = []
        page.on("console", lambda m: msgs.append(m.text))
        if ok4:
            page.click("#view button:has-text(\"Publish as Member C's version\")")
        ok5 = step(lambda: (page.wait_for_selector("#publish", state="detached", timeout=10000),
                            page.wait_for_selector("#view .card:has-text('2 versions')", timeout=10000)))
        mt = page.inner_text("#view") if ok5 else ""
        check("X06 Publish puts it in the pool under the member's name, shown as one of two versions",
              ok5 and "Recipe by Member C" in mt and "2 versions" in mt,
              "%s | toast=%s | console=%s | view=%s" % (mt[:200], page.inner_text("#toast"), msgs[-3:],
                                                        page.inner_text("#view")[:200]))
        ok6 = step(lambda: (page.click(".tab:has-text('Me')", timeout=5000),
                            page.wait_for_selector("#view input[type=checkbox]", timeout=8000),
                            page.check("#view input[value=noonion]"),
                            page.click("#view button:has-text('Save preferences')"),
                            page.wait_for_timeout(600)))
        me = page.inner_text("#view") if ok6 else ""
        check("X07 the Me tab: preferences saved, Share-shortcut key, Face ID, PIN change, sign-out",
              ok6 and "Share shortcut" in me and "Face ID" in me and "Change PIN" in me and "Sign out on all devices" in me
              and "Address" in me, me[:300])
        wide = page.evaluate("() => document.documentElement.scrollWidth <= 301")
        check("X08 no sideways scroll at 300 px", wide, page.evaluate("() => document.documentElement.scrollWidth"))
        check("X09 no page error on the kitchen member's pages", not errs, errs[:3])
        check("X10 every request stays inside /kitchen/k1/", not outside, outside[:5])
        ctx.close()
        b.close()
    _ = (r, TD)


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("X00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

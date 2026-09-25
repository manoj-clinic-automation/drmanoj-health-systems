#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui_c.py -- OFFLINE ONLY (Playwright + Chromium). The Family Kitchen
page in a member's GutLog, at 300 px, through the front proxy.

    python3 -B family/test_family_ui_c.py gutlog/app.py

No page error; browsing lists the pool; a card opens with its "modified"
badge and reason for an IBS member; the Inbox opens a captured draft with the
fields and the attachment beside them; no request leaves the member's prefix
except the ones the page itself never makes. Python 3.9.
"""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import test_family_c as TC  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def run(rig):
    m1 = rig.member_client("m1")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs"})
    _d, r = TC.confirm_text(m1, "m1", "Test onion sabzi", [{"item": "onion", "qty": 1, "unit": "medium"},
                                                          {"item": "potato", "qty": 2, "unit": "medium"}],
                            ["Fry.", "Serve."], 2, grp="Sabzi")
    rid = (r.json() or {}).get("id")
    ctok = rig.kitchen_tokens()["capture"]["m1"]["token"]
    TC.capture(rig, "m1", ctok, {"text": "IMG_0002.jpeg"}, [("IMG_0002.jpeg", TC.PNG, "image/png")])
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 300, "height": 800}, service_workers="block")
        page = ctx.new_page()
        errs, outside = [], []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("request", lambda q: outside.append(urlparse(q.url).path)
                if urlparse(q.url).port == rig.front_port and not urlparse(q.url).path.startswith("/m1/") else None)
        page.goto(rig.front_url + "/m1/login")
        page.fill("input[name=pw]", rig.pw["m1"])
        page.click("button")
        page.wait_for_load_state("networkidle")
        page.goto(rig.front_url + "/m1/kitchen")

        def step(fn):
            """A step that fails is recorded by the check that needs it, not as a crash."""
            try:
                fn()
                return True
            except Exception:
                return False
        ok1 = step(lambda: page.wait_for_selector("#view .card", timeout=10000))
        check("W01 the Kitchen lists the pool", ok1 and "Test onion sabzi" in page.inner_text("#view"), ok1)
        ok2 = step(lambda: (page.click("#view .card:has-text('Test onion sabzi')", timeout=5000),
                            page.wait_for_selector("#view h2", timeout=8000)))
        t = page.inner_text("#view") if ok2 else ""
        check("W02 a card shows the modified badge and its reason for an IBS member",
              "modified for you" in t and "because IBS is recorded" in t and "hing" in t, t[:300])
        ok3 = step(lambda: (page.click(".tab:has-text('Inbox')", timeout=5000),
                            page.wait_for_selector("#view .card", timeout=8000),
                            page.click("#view .card >> nth=0", timeout=5000),
                            page.wait_for_selector("#view input", timeout=8000)))
        has_img = ok3 and page.locator("#view img.att").count() == 1
        check("W03 the Inbox opens a draft with its fields and the photo beside them",
              has_img and page.locator("#view textarea").count() == 1, has_img)
        wide = page.evaluate("() => document.documentElement.scrollWidth <= 301")
        check("W04 no sideways scroll at 300 px", wide, page.evaluate("() => document.documentElement.scrollWidth"))
        check("W05 no page error on the Kitchen page", not errs, errs[:3])
        check("W06 every request stays inside the member's prefix", not outside, outside[:5])
        ctx.close()
        b.close()
    _ = rid


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("W00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

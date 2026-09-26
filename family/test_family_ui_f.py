#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui_f.py -- OFFLINE ONLY (Playwright + Chromium). Finding recipes
at 300 px: a full member's Family Kitchen page in GutLog and a kitchen
member's book, through the front proxy.

    python3 -B family/test_family_ui_f.py gutlog/app.py

The person view narrows to a person's categories by tapping; the category
view lists people; the search box finds by alias; filter chips combine and
the counts change; an empty result says what to loosen; the last person
opened is remembered after a reload; no page error; no sideways scroll.
Python 3.9.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import test_family_c as TC  # noqa: E402
import test_family_d as TD  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def run(rig):
    m1, m2 = rig.member_client("m1"), rig.member_client("m2")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs"})
    m2.post("/m2/welcome", {"name": "Member B", "age": "68", "cond": "general"})
    TC.confirm_text(m1, "m1", "Test lauki dal", [{"item": "bottle gourd", "qty": 200, "unit": "g"}], ["Cook."], 2,
                    grp="Dal & curry")
    TC.confirm_text(m1, "m1", "Test bhindi sabzi", [{"item": "okra", "qty": 200, "unit": "g"},
                                                    {"item": "onion", "qty": 1, "unit": "medium"}], ["Fry."], 2, grp="Sabzi")
    TC.confirm_text(m2, "m2", "Test kheer", [{"item": "rice", "qty": 50, "unit": "g"}], ["Simmer."], 2, grp="Sweet")
    rig.stamp_kitchen("k1", "Member C")
    k1 = "/kitchen/k1"
    with sync_playwright() as p:
        b = p.chromium.launch()
        for who, url, pin_slug, label in (("m1", "/m1/kitchen", "m1", "GutLog"), ("k1", k1 + "/", "k1", "kitchen book")):
            ctx = b.new_context(viewport={"width": 300, "height": 800}, service_workers="block")
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            login = ("/%s/login" % who) if who == "m1" else (k1 + "/login")
            page.goto(rig.front_url + login)
            page.fill("input[name=pin]", rig.pw[pin_slug])
            page.click("#go")
            page.wait_for_load_state("networkidle")
            if who == "m1" and "/welcome" in page.url:
                page.click("button")
                page.wait_for_load_state("networkidle")
            page.goto(rig.front_url + url)
            page.wait_for_selector("#level1 .chip", timeout=15000)
            l1 = page.inner_text("#level1")
            page.click("#level1 .chip:has-text('Member A')")
            page.wait_for_timeout(700)
            l2 = page.inner_text("#level2")
            vt = page.inner_text("#view")
            check("Y01 %s: Person -> Category by tapping: Everyone first with counts, then the person's own categories "
                  "and only their recipes" % label,
                  l1.startswith("Everyone (") and "Member A (2)" in l1 and "Dal & curry (1)" in l2 and "Sweet" not in l2
                  and "Test kheer" not in vt and "Recipe by Member A" in vt, "%s | %s" % (l1, l2))
            page.reload()
            page.wait_for_selector("#level1 .chip", timeout=15000)
            page.wait_for_timeout(500)
            sel = page.inner_text("#level1 .chip.sel")
            check("Y02 %s: the last person opened is remembered on this phone after a reload" % label,
                  "Member A" in sel, sel)
            page.click("#level1 .chip >> nth=0")   # back to Everyone
            page.wait_for_timeout(600)
            page.click("#views .chip >> nth=1")   # "Category -> Person" (both chips say Category)
            page.wait_for_timeout(700)
            l1b = page.inner_text("#level1")
            l2b = ""
            try:
                page.click("#level1 .chip:has-text('Sweet')", timeout=8000)
                page.wait_for_timeout(700)
                l2b = page.inner_text("#level2")
            except Exception as exc:
                l2b = "click failed: " + str(exc).splitlines()[0]
            check("Y03 %s: Category -> Person: a category lists the people who have one there" % label,
                  "Every category (" in l1b and "Member B (1)" in l2b and "Member A" not in l2b,
                  "%s | %s" % (l1b.replace("\n", " / "), l2b.replace("\n", " / ")))
            page.click("#level1 .chip >> nth=0")
            page.click("#level2 .chip >> nth=0")
            page.fill("#q", "ghiya")
            page.press("#q", "Enter")   # the phone keyboard's Search key
            try:
                page.wait_for_function("() => (document.querySelector('#level1')||{innerText:''}).innerText.indexOf('(1)') >= 0",
                                       timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(300)
            st = page.inner_text("#view")
            check("Y04 %s: two taps in quick succession never stack two answers in the view (one search box, one set "
                  "of chips)" % label, st.count("Person → Category") == 1, st.count("Person → Category"))
            check("Y04 %s: the search box finds by Hindi alias (ghiya -> bottle gourd)" % label,
                  "Test lauki dal" in st and "Test kheer" not in st,
                  "q=%r | %s" % (page.input_value("#q"), st.replace("\n", " / ")[-280:]))
            page.fill("#q", "")
            page.press("#q", "Enter")
            page.wait_for_timeout(600)
            page.click("#filters .chip:has-text('No onion-garlic')")
            page.wait_for_timeout(600)
            ft = page.inner_text("#view")
            n_all = page.inner_text("#level1 .chip >> nth=0")
            page.click("#filters .chip:has-text('Sweet')")
            page.click("#filters .chip:has-text('High-protein')")
            page.wait_for_timeout(800)
            empty = page.inner_text("#view")
            check("Y05 %s: filters combine and the counts change; an empty result says what to loosen" % label,
                  "Test bhindi sabzi" not in ft and "(2)" in n_all and "loosen" in empty and "high-protein" in empty,
                  "%s | %s" % (n_all, empty[-200:]))
            wide = page.evaluate("() => document.documentElement.scrollWidth <= 301")
            check("Y06 %s: no sideways scroll at 300 px and no page error" % label, wide and not errs, errs[:3])
            ctx.close()
        b.close()
    _ = TD


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("Y00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

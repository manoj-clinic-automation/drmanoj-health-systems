#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui_e.py -- OFFLINE ONLY (Playwright + Chromium). The weight
profile at 300 px, in a real browser, through the front proxy.

    python3 -B family/test_family_ui_e.py gutlog/app.py

A seeded member (test_family_e's synthetic seed): the Now page opens with the
This-week card first, the weekly dose under "Weekly", the Check-ins card with
today's due items, a PHQ-2 answered by tapping, the Physio tile; the
Check-ins page draws the chart; the physio's own page signs in and saves a
programme; no page error; no sideways scroll. Python 3.9.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import test_family_e as TE  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def run(rig):
    seed_path, _seed = TE.make_seed(rig.work, "zqui")
    pwf = os.path.join(rig.work, "first-login.local.txt")
    rc, out = TE.stamp_tool(rig, "--slug", "m3", "--name", "Member W", "--profile", "weight", "--seed", seed_path,
                            "--password-file", pwf)
    if rc != 0:
        raise SystemExit("stamp failed: " + out[-400:])
    rig.pw["m3"] = TE.file_pin(pwf, "m3") or ""
    rig.pw["p1"] = TE.file_pin(pwf, "p1") or ""
    ports = rig.ports("m3")
    rig.front.routes = sorted(dict(rig.front.routes, **{"/m3/rx/": ports["rx"], "/m3/fit/": ports["fit"],
                                                        "/m3/": ports["gut"]}).items(),
                              key=lambda kv: len(kv[0]), reverse=True)
    rig.start_member("m3")
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 300, "height": 800}, service_workers="block")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(rig.front_url + "/m3/login")
        page.fill("input[name=pin]", rig.pw["m3"])
        page.click("#go")
        page.wait_for_load_state("networkidle")
        if "/passkey/offer" in page.url:
            page.goto(rig.front_url + "/m3/")
        page.wait_for_selector("#nowWeek", state="visible", timeout=15000)
        page.wait_for_selector("#wkBody p", timeout=10000)
        page.wait_for_timeout(800)
        first = page.evaluate("""() => [...document.getElementById('tab-now').children]
            .map(c => c.id).filter(id => id && document.getElementById(id).offsetParent !== null).slice(0, 4)""")
        wk = page.inner_text("#wkBody")
        check("W01 the This-week card is first and visible at 300 px, with weight, injection countdown, milestone, "
              "steps (blank) and protein", first[:2] == ["famBar", "nowWeek"] and "Weight" in wk and "Injection" in wk
              and "Milestone" in wk and "Steps today" in wk and "Protein" in wk and "0 steps" not in wk,
              "%s %s" % (first, wk[:200]))
        page.click("#nowDoses .fold-h")
        page.wait_for_selector("#nowSched .slothd", timeout=8000)
        sched = page.inner_text("#nowSched")
        check("W02 the weekly dose is listed under 'Weekly' on its day, with the daily lines",
              "WEEKLY" in sched.upper() and "Medicine C" in sched and "Medicine A" in sched, sched[:200].replace("\n", " | "))
        page.wait_for_selector("#nowCheckins", state="visible", timeout=10000)
        ck = page.inner_text("#ckBody")
        check("W03 the Check-ins card lists what is due today", "Mood check (PHQ-2)" in ck and "Weekly weigh-in" in ck, ck[:200])
        ok = True
        try:
            page.click("#ckBody .ckrow:has-text('PHQ-2') button", timeout=5000)
            page.wait_for_selector("#ckBody .ckform", timeout=8000)
            chips = page.locator("#ckBody .ckform .chips")
            chips.nth(0).locator(".chip").nth(1).click()
            chips.nth(1).locator(".chip").nth(2).click()
            page.click("#ckBody .ckform button:has-text('Save')")
            page.wait_for_timeout(900)
        except Exception as exc:
            ok = False
            errs.append("step: " + str(exc).splitlines()[0])
        ck2 = page.inner_text("#ckBody") if page.locator("#nowCheckins").is_visible() else ""
        check("W04 a PHQ-2 answered by tapping leaves the due list", ok and "PHQ-2" not in ck2, ck2[:200])
        ph = page.inner_text("#nowPhysio") if page.locator("#nowPhysio").is_visible() else "hidden"
        check("W05 the Physio tile is on the Now page", "Physio" in ph and "not written" in ph, ph[:200])
        wide = page.evaluate("() => document.documentElement.scrollWidth <= 301")
        check("W06 no sideways scroll at 300 px with the weight cards open", wide,
              page.evaluate("() => document.documentElement.scrollWidth"))
        page.goto(rig.front_url + "/m3/checkins")
        page.wait_for_selector("#chart svg, #chart p", timeout=10000)
        page.wait_for_selector("#kinds .chip", timeout=8000)
        ct = page.inner_text("main")
        has_svg = page.locator("#chart svg").count() == 1
        check("W07 the Check-ins page draws the weight chart with the milestone lines and lists the check-ins",
              has_svg and "-5% = " in ct and "Do one now" in ct and "Trends" in ct, ct[:300])
        wide2 = page.evaluate("() => document.documentElement.scrollWidth <= 301")
        check("W08 the Check-ins page has no sideways scroll at 300 px", wide2, "")
        check("W09 no page error on the member's pages", not errs, errs[:3])
        ctx.close()
        # the physio's own page
        ctx = b.new_context(viewport={"width": 300, "height": 800}, service_workers="block")
        page = ctx.new_page()
        perrs = []
        page.on("pageerror", lambda e: perrs.append(str(e)))
        page.goto(rig.front_url + "/m3/physio/login")
        t = page.inner_text("main")
        page.fill("input[name=pin]", rig.pw["p1"])
        page.click("#go")
        page.wait_for_load_state("networkidle")
        if "/passkey/offer" in page.url:
            page.goto(rig.front_url + "/m3/physio/")
        page.wait_for_selector("#view .card", timeout=10000)
        pt = page.inner_text("main")
        check("W10 the physio page names them and the member and opens on the programme",
              "physio for Member W" in t and "Programme for Member W" in pt, (t[:100], pt[:100]))
        try:
            page.click("#view button:has-text('+ exercise')", timeout=5000)
            page.fill("#view .ex input >> nth=0", "Sit-to-stand")
            page.fill("#view .ex input >> nth=1", "3")
            page.fill("#view .ex input >> nth=2", "10")
            page.click("#view .ex .chip:has-text('Mon')")
            page.click("#view button:has-text('Save programme')")
            page.wait_for_timeout(900)
            pt2 = page.inner_text("main")
        except Exception as exc:
            pt2 = "step failed: " + str(exc).splitlines()[0]
        check("W11 the physio saves a programme by tapping", "Last changed" in pt2 and "Physio A" in pt2, pt2[:200])
        wide3 = page.evaluate("() => document.documentElement.scrollWidth <= 301")
        check("W12 the physio page has no sideways scroll and no page error", wide3 and not perrs, perrs[:3])
        ctx.close()
        b.close()


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

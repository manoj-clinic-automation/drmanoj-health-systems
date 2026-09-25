#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_ui_auth.py -- OFFLINE ONLY (Playwright + Chromium). Signing in on a phone.

    python3 -B family/test_family_ui_auth.py gutlog/app.py

A member's copy at 360 px, with Chromium's VIRTUAL platform authenticator (a
stand-in for Face ID / Touch ID, via the DevTools WebAuthn domain) so the
page's own WebAuthn JavaScript runs end to end:

  G01 the PIN page signs in; after it, Face ID is offered and set up;
  G02 signed out, "Sign in with Face ID / Touch ID" signs back in;
  G03 five wrong PINs show the lockout message;
  G04 no page error, no sideways scroll;
  G05 no Face ID button until Face ID is set up on this phone; the PIN eye.
The rig runs on http://localhost -- a secure context, and a host WebAuthn
accepts as a relying-party ID (an IP address is not). Python 3.9.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def step(fn):
    try:
        fn()
        return True
    except Exception:
        return False


def run(rig):
    base = rig.front_url
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 360, "height": 800}, service_workers="block")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        cdp = ctx.new_cdp_session(page)
        cdp.send("WebAuthn.enable")
        auth = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
            "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True}})
        page.goto(base + "/m1/login")
        page.wait_for_load_state("networkidle")
        hidden = page.is_hidden("#pk") and page.evaluate("() => !!window.PublicKeyCredential")
        check("G05 before Face ID is set up on this phone, the sign-in page shows no Face ID button",
              hidden, page.evaluate("() => getComputedStyle(document.getElementById('pk')).display"))
        page.fill("input[name=pin]", "123")
        types = [page.get_attribute("#pin", "type")]
        page.click("#eye")
        types.append(page.get_attribute("#pin", "type"))
        page.click("#eye")
        types.append(page.get_attribute("#pin", "type"))
        check("G05 the eye shows the PIN and hides it again; the keypad is numeric",
              types == ["password", "text", "password"] and page.get_attribute("#pin", "inputmode") == "numeric"
              and page.input_value("#pin") == "123", types)
        page.fill("input[name=pin]", rig.pw["m1"])
        page.click("#go")
        offered = step(lambda: page.wait_for_selector("#offer", state="visible", timeout=8000))
        ok = offered and step(lambda: (page.click("#yes"), page.wait_for_url("**/m1/**welcome**", timeout=10000)))
        creds = cdp.send("WebAuthn.getCredentials", {"authenticatorId": auth["authenticatorId"]}).get("credentials", [])
        check("G01 the PIN signs in, then Face ID is offered and set up", offered and ok and len(creds) == 1,
              (offered, ok, len(creds), page.url))
        page.goto(base + "/m1/logout")
        page.goto(base + "/m1/login")
        back = step(lambda: (page.wait_for_selector("#pk", state="visible", timeout=5000), page.click("#pk"),
                             page.wait_for_function("() => !location.pathname.endsWith('/login')", timeout=10000),
                             page.wait_for_load_state("networkidle")))
        check("G02 signed out, Face ID signs the member back in", back and "/login" not in page.url, page.url)
        wide = step(lambda: None) and page.evaluate("() => document.documentElement.scrollWidth <= 361")
        page.goto(base + "/m1/logout")
        for i in range(5):
            page.goto(base + "/m1/login")
            page.fill("input[name=pin]", "%06d" % ((int(rig.pw["m1"]) + 1 + i) % 1000000))
            page.click("#go")
        t = page.inner_text("body")
        check("G03 five wrong PINs show the lockout message", "Paused until" in t and "call Manoj" in t, t[:200])
        check("G04 no page error, no sideways scroll at 360 px", not errs and wide, (errs[:3], wide))
        ctx.close()
        b.close()


def main():
    rig = famtest.Rig(OWNER_APP, host="localhost")
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("G00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
getting_started_sheet.py -- the one-page "Your Health App -- Getting Started"
sheet for a family member, and the physio's own sheet.

FAMILY_EDITION_V1 (26-Sep-2026). Runs on the PC (reportlab), never on the
server. Names come from the gitignored seed file or from the command line
and go only into the PDF, which is gitignored (*.pdf); nothing here names
anyone.

    python family/getting_started_sheet.py --seed fitlog-ingest/m3_seed.local.json --out fitlog-ingest
    python family/getting_started_sheet.py --name "…" --slug m1 --profile gut --out fitlog-ingest

Profile wording: gut / joint / general (as for the first sheets), weight
(weigh-in, injection day, check-ins, the This-week card). With --physio the
second sheet, for the physio, describes their tile only. Python 3.9.
"""
import argparse
import json
import os
import re
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE = "https://family.dr-manoj.in"
OWNER = "Dr. Manoj Agarwal"


def styles():
    return {
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=1, spaceAfter=4),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, leading=12, textColor=colors.HexColor("#555555"),
                              spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#1F5F8B"),
                             spaceBefore=8, spaceAfter=3),
        "p": ParagraphStyle("p", fontName="Helvetica", fontSize=10, leading=13.5, leftIndent=10, spaceAfter=3),
        "p0": ParagraphStyle("p0", fontName="Helvetica", fontSize=10, leading=13.5, spaceAfter=3),
    }


def member_sheet(name, slug, profile, caretaker, physio_name, out):
    st = styles()
    first = caretaker.split()[0] if caretaker else "your caretaker"
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=14 * mm, title="Your Health App - Getting Started", author=OWNER)
    f = []
    f.append(Paragraph("Your Health App &mdash; Getting Started", st["h1"]))
    f.append(Paragraph("For %s &mdash; set up by %s" % (name, OWNER), st["sub"]))
    rows = [["Your address", "%s/%s/" % (BASE, slug)],
            ["Your PIN", "A 6-digit number, sent to you separately by %s on WhatsApp." % first],
            ["Username", "None needed — your address is yours alone."]]
    t = Table([[Paragraph("<b>%s</b>" % a, st["p0"]), Paragraph("<b>%s</b>" % b if a == "Your address" else b, st["p0"])]
               for a, b in rows], colWidths=[38 * mm, None])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#1F5F8B")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF1F7")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    f.append(t)
    f.append(Paragraph("Step 1 &mdash; Open it on your iPhone (5 minutes)", st["h2"]))
    for i, s in enumerate([
        "Tap the address above (or copy it). It must open in <b>Safari</b>. If it opens inside WhatsApp, tap the "
        "<b>Safari</b> / compass button to open it there.",
        "The page should say <b>%s's health diary</b> at the top. If it shows another name, you have the wrong "
        "link &mdash; ask %s." % (name, first),
        "Type your 6-digit PIN (a number keypad opens; tap <b>Show</b> to check what you typed) and tap <b>Sign "
        "in</b>. If it says <i>Wrong PIN</i>, it tells you how many tries are left; after 5 it pauses for a while "
        "&mdash; just wait, or call %s." % first,
        "The app then offers <b>Face ID</b>. Tap <b>Yes</b>. From then on a <b>Sign in with Face ID</b> button "
        "appears on your phone, so no PIN is needed. It also stays signed in on your phone for a year.",
        ("Your details, medicines, meal times and check-ins are <b>already filled in</b> from the plan %s made "
         "with you. Nothing to type on day 1." % first) if profile == "weight" else
        "A short <b>first-time setup</b> screen appears: your name, age, height, weight, health conditions, "
        "allergies, usual meal times, and whether you take a night tablet. Fill what you know &mdash; it can be "
        "changed later.",
    ], 1):
        f.append(Paragraph("%d. %s" % (i, s), st["p"]))
    if profile == "weight":
        f.append(Paragraph("Step 2 &mdash; What you will see on day 1 (Sunday)", st["h2"]))
        steps = [
            "<b>This week</b>, at the top: your weight and waist, the <b>injection day</b> countdown, the milestone "
            "line (how many kg to the next 5%), your steps today (from the phone in your pocket) and protein "
            "against the target.",
            "<b>Today's doses</b>: tap each medicine as you take it. The <b>weekly injection</b> appears only on its "
            "day, with a reminder from 30 minutes before; if you forget, the next day asks “taken late / "
            "skipped?” &mdash; one tap.",
            "<b>Meals</b>: tap the meal and pick what you ate. Your own meal times are set, so an 11:30 breakfast is "
            "breakfast, not lunch. The header shows protein so far against the target.",
            "<b>Check-ins</b>: appear only when due &mdash; the <b>Sunday weigh-in</b> (weight, waist), a short "
            "mood check, a sleepiness check, a side-effect check the day after the injection, and monthly "
            "measurements. Answer, tap Save, and it disappears until next time. If one answer needs %s to know, "
            "the app tells you to <b>tell %s today</b>." % (first, first),
            "<b>Physio</b>: the programme%s writes for you, with a button to tick today's session." % (
                " " + physio_name if physio_name else ""),
            "<b>Check-ins and chart</b> (link in the This-week card): your weight and waist over the weeks with the "
            "milestone lines, and every check-in's trend.",
            "<b>Steps without a watch</b>: keep the phone in your pocket; %s will connect Apple Health so the steps "
            "arrive by themselves (about 5 minutes together)." % first,
        ]
    else:
        f.append(Paragraph("Step 2 &mdash; Add your medicines (about 2 minutes each)", st["h2"]))
        steps = [
            "Tap <b>Meds</b> at the bottom, then <b>PRN dose</b>, then <b>Add medicine</b>. Type the name and "
            "strength as on the strip.",
            "The <b>Salts</b> page opens by itself. Type the salt (the chemical name printed under the brand) and "
            "the strength. This lets the app check your medicines are safe together.",
            "For a medicine you take <b>every day</b>: tap <b>Meds &rarr; Schedule &rarr; Add a regular medicine</b>. "
            "Pick it, choose the time (Morning, Noon, Evening, Night, or Weekly with its day), the dose and "
            "before/after food, then <b>Add to regimen</b>.",
            "A medicine taken <b>only when needed</b> needs only the first two steps. Log it from <b>Meds &rarr; PRN "
            "dose</b> when you take it.",
            "Stopped a medicine? <b>Meds &rarr; Schedule</b>, tap <b>stop</b> next to it. Your past record is kept.",
            "Not sure of a salt or a dose? Leave it and message %s &mdash; he can see and fix it." % first,
        ]
    for i, s in enumerate(steps, 1):
        f.append(Paragraph("%d. %s" % (i, s), st["p"]))
    f.append(Paragraph("Step 3 &mdash; Put it on your home screen (1 minute)", st["h2"]))
    for i, s in enumerate([
        "While the app is open in Safari, tap the <b>Share</b> button (the square with an arrow pointing up).",
        "Scroll down and tap <b>Add to Home Screen</b>, then <b>Add</b>.",
        "From now on, open it from that icon &mdash; it works like a normal app. It stays signed in.",
    ], 1):
        f.append(Paragraph("%d. %s" % (i, s), st["p"]))
    f.append(Paragraph("Step 4 &mdash; Everyday use (under a minute a day)", st["h2"]))
    for i, s in enumerate([
        "<b>Now page</b> &mdash; everything for today on one screen.",
        "<b>Medicines:</b> tap each one as you take it. Tap again to correct or change the time.",
        "<b>Meals:</b> tap the meal and pick what you ate. Your own dishes can be added; <b>Quick Bite</b> for "
        "anything in between.",
        "<b>Symptoms and pain:</b> tap where it hurts, how much (0&ndash;10) and when it started; knee pain and how "
        "far you can walk go in the joint card.",
        "<b>BP, weight, reports:</b> add when you have them. Photos or PDFs of lab reports are read automatically.",
        "Don't worry about missing something &mdash; every entry can be edited later, including the time.",
    ], 1):
        f.append(Paragraph("%d. %s" % (i, s), st["p"]))
    f.append(Paragraph("Your privacy", st["h2"]))
    f.append(Paragraph("Your app and your data are yours alone &mdash; separate from everyone else in the family. Only %s "
                       "can open it, as your caretaker, to help if you need him. You can switch his access off (and "
                       "on again) any time on the <b>Caretaker</b> page; any change he makes is marked “by "
                       "caretaker” so you can see it.%s" % (
                           first, (" Your physio sees only the physio programme, the sessions, your knee pain and "
                                   "walking entries and the monthly re-test &mdash; nothing else.") if physio_name else ""),
                       st["p0"]))
    f.append(Paragraph("Need help?", st["h2"]))
    f.append(Paragraph("Call or WhatsApp %s. Forgotten PIN: tell %s &mdash; he will give you a new one. You can change "
                       "your PIN yourself on the Caretaker page." % (first, first), st["p0"]))
    doc.build(f)


def physio_sheet(pname, member_name, slug, caretaker, out):
    st = styles()
    first = caretaker.split()[0] if caretaker else "the caretaker"
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=14 * mm, title="Physio Page - Getting Started", author=OWNER)
    f = [Paragraph("Physio Page &mdash; Getting Started", st["h1"]),
         Paragraph("For %s (physiotherapist to %s) &mdash; set up by %s" % (pname, member_name, OWNER), st["sub"])]
    rows = [["Your address", "%s/%s/physio/" % (BASE, slug)],
            ["Your PIN", "A 6-digit number, sent to you separately by %s on WhatsApp." % first],
            ["What it opens", "Only %s's physio programme, session log, knee / joint pain and walking entries, and "
                              "the monthly re-test. Nothing else of the health record." % member_name]]
    t = Table([[Paragraph("<b>%s</b>" % a, st["p0"]), Paragraph("<b>%s</b>" % b if a == "Your address" else b, st["p0"])]
               for a, b in rows], colWidths=[38 * mm, None])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#1F5F8B")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF1F7")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    f.append(t)
    f.append(Paragraph("Step 1 &mdash; Open it (2 minutes)", st["h2"]))
    for i, s in enumerate([
        "Tap the address above; it must open in <b>Safari</b> (or any browser).",
        "The page says <b>%s &mdash; physio for %s</b>. Type your 6-digit PIN and tap <b>Sign in</b>. After 5 wrong "
        "PINs it pauses for a while." % (pname, member_name),
        "Tap <b>Yes</b> when it offers Face ID, and add it to your home screen from Safari's Share button "
        "(<b>Add to Home Screen</b>). It stays signed in for a year.",
    ], 1):
        f.append(Paragraph("%d. %s" % (i, s), st["p"]))
    f.append(Paragraph("Step 2 &mdash; The four tabs", st["h2"]))
    for i, s in enumerate([
        "<b>Programme</b>: the exercises with sets, reps, hold seconds and the days of the week. Tap <b>Save "
        "programme</b>. %s sees it the same minute as a Physio tile on her Now page." % member_name,
        "<b>Sessions</b>: tick today's session when you do it together; %s can tick her own sessions from her tile. "
        "The last 28 days are listed." % member_name,
        "<b>Pain &amp; walking</b>: the joint, pain 0&ndash;10, minutes she could walk before pain. These go into "
        "her joint log, marked as entered by you.",
        "<b>Re-test</b>: monthly &mdash; 30-second sit-to-stand, 6-minute walk, knee ROM left/right, single-leg "
        "stance left/right. Earlier results stay listed underneath.",
        "<b>Me</b>: change your PIN, Face ID, sign out.",
    ], 1):
        f.append(Paragraph("%d. %s" % (i, s), st["p"]))
    f.append(Paragraph("Need help?", st["h2"]))
    f.append(Paragraph("Call or WhatsApp %s. Forgotten PIN: %s gives you a new one." % (first, first), st["p0"]))
    doc.build(f)


def safe(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "Member"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=None, help="a gitignored seed file: name, slug, profile, physio")
    ap.add_argument("--name", default=None)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--profile", default="general")
    ap.add_argument("--physio", default=None, help="the physio's name (their own sheet is made too)")
    ap.add_argument("--caretaker", default="Manoj")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    name, slug, profile, physio = a.name, a.slug, a.profile, a.physio
    if a.seed:
        with open(a.seed, encoding="utf-8") as fh:
            s = json.load(fh)
        name, slug, profile = s.get("name"), s.get("slug"), s.get("profile") or profile
        physio = (s.get("physio") or {}).get("name") or physio
    if not name or not slug:
        print("--seed, or --name and --slug")
        return 2
    os.makedirs(a.out, exist_ok=True)
    p1 = os.path.join(a.out, "Health_App_Getting_Started_%s.pdf" % safe(name.split()[0]))
    member_sheet(name, slug, profile, a.caretaker, physio, p1)
    print("wrote " + p1)
    if physio:
        p2 = os.path.join(a.out, "Physio_Page_Getting_Started_%s.pdf" % safe(physio.split()[0]))
        physio_sheet(physio, name, slug, a.caretaker, p2)
        print("wrote " + p2)
    return 0


if __name__ == "__main__":
    sys.exit(main())

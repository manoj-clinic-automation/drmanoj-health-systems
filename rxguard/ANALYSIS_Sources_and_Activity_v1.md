# Analysis — medicine sources (India, Europe) and activity logging (11-Sep-2026)

Status: **ANALYSIS ONLY — nothing built.** Companion to `PLAN_MedKnowledge_v1.md`.

---

## Part 1 — Credible medicine sources we can actually use

Rule applied: a source counts only if it is authoritative **and** we are allowed
to use it in a personal system. "Readable on a website" is not the same as
"usable as data".

### India
| Source | What it is | Usable by RxGuard? |
|---|---|---|
| **CDSCO approved-drugs lists** (cdscoonline.gov.in) | Regulator's record of approved new drugs and FDCs | **Yes, as a check** — confirms a salt or combination is approved in India. Web/PDF only, no data feed, so a lookup at review time, not an automatic pull |
| **PvPI drug safety alerts** (Indian Pharmacopoeia Commission) | India's pharmacovigilance signals and monthly alerts on new adverse reactions | **Yes, as a watch-list** — RxGuard checks each new alert against your molecules and raises it on the review queue |
| **National Formulary of India 2021** (IPC) | India's official formulary | **Reference only** — a paid book (about ₹660); useful when drafting India-only salts, no data feed |
| **CIMS / MIMS India** (CIMS Healthcare Data) | The most complete Indian brand → salt database, with interactions | **Only with a paid licence** — the one source that would cover Indian brands and interactions together. Cost unknown until asked |
| 1mg, PharmEasy checkers | Consumer apps | **No** — no API; terms forbid scraping |

### Europe / UK
| Source | What it is | Usable by RxGuard? |
|---|---|---|
| **EMA ePI** (EU electronic product information, FHIR API) | Machine-readable SmPCs; section 4.5 is "Interactions" | **Yes** — public API from the pilot; the ePI roadmap came out March 2026 and centralised-procedure submissions began 4 Sep 2026, so coverage is still growing. Same approach as FDA labels: quote the sentence |
| **UK dm+d** (NHSBSA, via NHS TRUD) | Every UK product: ingredients, strengths, forms | **Yes, free with registration** — second name/strength normaliser beside RxNorm; no interactions |
| **UK eMC** (Datapharm) | UK SmPCs | Free to read; **data/API only under a Datapharm licence** |
| **BNF interactions** (NICE / BNF Partners) | The UK gold-standard interaction checker | **Only with a licence** — free NICE access is for reading; data feeds need an RPS licence and text-mining is prohibited |
| **Janusmed interactions** (Region Stockholm + Karolinska) | High-quality graded checker (D = avoid … A = no significance; evidence levels 0–4) | **Manual lookup only** — the integrated version is for Swedish health systems; no data licence for us |

### Interaction checkers — what is genuinely available to us
| Option | Verdict |
|---|---|
| NLM (US) interaction API | **Shut down Jan 2024** |
| **DDInter 2.0** (academic, published in Nucleic Acids Research) | **Usable now, free** — downloadable dataset with risk levels, mechanisms and management; licence CC BY-NC-SA 4.0 permits personal non-commercial use with attribution. Academic, not a regulator |
| BNF, CIMS/MIMS, Lexicomp, DrugBank | Paid licences |
| Janusmed, Drugs.com, 1mg | Web lookups only; no data for us |

### Recommendation (decided)
Four layers, each labelled on screen so you always see where a warning came from:
1. **Our curated rules** (24 today) — always win.
2. **Regulator labels** — FDA (US) and EMA ePI (EU): interaction sentences quoted
   verbatim. RED only where the label says contraindicated / do not co-administer.
3. **DDInter 2.0** — imported as a clearly marked academic layer, cited per pair,
   never overriding 1 or 2; its first appearance for each pair goes through your
   approval card.
4. **PvPI alerts** — monthly watch against your molecules.
Identity (salt + strength) normalised through RxNorm and dm+d; India-only FDCs
checked against the CDSCO list.

**Only decision for you:** whether to ask CIMS India (and/or BNF) for a personal
licence quote. It is the only route to a clinically maintained Indian-brand
interaction checker; everything above works without it.

---

## Part 2 — Activity on the GutLog screen, and the watch

### What exists today
- **GutLog**: Walk, Treadmill and Meditation are minute chips inside
  **Log → Day**, stored once per day in the daily rollup — buried, one value per
  day, no cycling, no steps, no time of day.
- **FitLog**: the daily check-in, the plan verdict, session done/partial, and
  (since Phase 3.5) the **Apple Watch / Health Connect intake** — steps, exercise
  minutes, sleep, resting HR, and workouts with type, duration, distance, energy.
  The intake is installed; by design no FitLog rule uses it yet.
- Gaps found: the intake **drops mindful (meditation) minutes** today — they are
  kept only as raw data — and it does not yet sort workouts into walk / treadmill
  / road cycling / static cycling.

### What you would see (GutLog Now tab)
A new **Activity** card, same style as Doses:
- Tiles: **Walk · Treadmill · Cycling (road) · Cycling (static) · Meditation**.
  Tap a tile → minutes (10 · 20 · 30 · 45 · 60) and an optional talk-test
  intensity (easy / moderate / hard) → saved at the current time. Several per
  day, each with Undo, and fixable later in Day by day like everything else.
- **Steps today** and any **watch workouts** appear on the same card on their
  own, marked ⌚. A watch workout that matches something you tapped (same kind,
  overlapping time) shows once, as *watch-confirmed* — the watch's minutes and
  distance win, your intensity note stays.
- Header summary like the other cards: *"45 min · 6,200 steps"*.
- Optionally, **FitLog's morning check-in and today's verdict** on the same card
  ("Today: YELLOW — 20 min easy walk + mobility", with Done / Partial), so FitLog
  never needs opening for daily use.

### How the watch data flows once it is switched on
Apple Watch → iPhone Health → Health Auto Export → FitLog intake (already live).
- FitLog learns the workout kinds: walking outdoors vs indoors/treadmill (the
  export carries an indoor flag), cycling outdoor vs indoor, and Mind & Body /
  mindful minutes as meditation. Mindful minutes: confirm the exact name on the
  first real payload before mapping — never guessed.
- GutLog's Activity card reads today's steps and workouts from FitLog (loopback,
  read-only, the same token pattern as the medicine feed).
- FitLog reads your tapped activities from GutLog, so its adherence and
  recovery view sees everything whether it came from the watch or your thumb.

**Who owns what (decided):** GutLog stores what you tap; FitLog stores what the
watch sends; each reads the other's; the watch wins where both describe the same
session. No double entry, and the old Walk/Treadmill/Meditation day chips become
totals computed from the new entries — nothing is deleted.

### Build order when you say go
1. GutLog Activity card + activity entries (manual, works with no watch).
2. FitLog: workout-kind mapping + mindful minutes + read endpoint for GutLog.
3. Two-way link, watch-confirmed matching, steps on the card.
4. Optional: FitLog check-in and verdict on the GutLog card.
5. Only after a few weeks of real watch data: let FitLog rules use it (the
   Phase 3.5 principle stands).

The one thing only you can do, when you choose: turn on the Health Auto Export
automation on your iPhone to point at FitLog.

---

## Sources
- CDSCO approved drugs — https://www.cdscoonline.gov.in/CDSCO/cdscoDrugs
- PvPI drug safety alerts — https://www.ipc.gov.in/mandates/pvpi/pvpi-outcome/8-category-en/416-drug-safety-alerts.html
- National Formulary of India 2021 — https://ipc.gov.in/mandates/nfi/about-nfi.html
- CIMS Healthcare Data — https://www.cimshd.com/
- EMA ePI — https://www.ema.europa.eu/en/human-regulatory-overview/marketing-authorisation/product-information-requirements/electronic-product-information-epi
- UK dm+d (NHSBSA) — https://www.nhsbsa.nhs.uk/pharmacies-gp-practices-and-appliance-contractors/nhs-dictionary-medicines-and-devices-dmd
- UK eMC (Datapharm) — https://www.datapharm.com/about/emc/
- BNF licensing — https://www.pharmaceuticalpress.com/services/content-licensing-and-integration/information-for-prospective-data-licensing-customers-about-bnf-and-bnfc-content-on-the-nice-website/
- Janusmed integrated — https://janusmed.se/about/janusmedintegrerad?jmi=true
- DDInter 2.0 — https://ddinter2.scbdd.com/ ; terms https://ddinter.scbdd.com/terms/
- NLM interaction API discontinued — https://lhncbc.nlm.nih.gov/RxNav/APIs/InteractionAPIs.html
- Health Auto Export workout format — https://help.healthyapps.dev/en/health-auto-export/export-format/workouts/

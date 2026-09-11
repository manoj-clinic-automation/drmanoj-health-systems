# Plan — every medicine checkable, current, and sourced (v1, 11-Sep-2026)

Status: **PLAN ONLY — nothing built.** Awaiting owner OK.

## The problem, in one line
RxGuard can only check molecules already in its hand-curated knowledge base
(74 today). Anything else — a new brand, an India-only salt, a medicine logged
without its salt — is reported "not checkable", and several of the regular
medicines are in that state now (sedatives among them, so the sedation burden
RxGuard shows is understated).

## What it will be like to use
1. **Add or first-log a medicine in GutLog** (the only entry surface). GutLog
   asks two things inline: the **salt** and the **strength** — e.g. *Linaclotide
   145 mcg*. Spelling suggestions come from RxNorm as you type. Combination
   products get one line per salt. About ten seconds.
2. **Within a minute RxGuard builds a draft entry** from verified sources
   (below). Every property it fills carries the exact source sentence and date.
   Anything the sources do not settle stays **blank → UNKNOWN**, never guessed.
3. **One review card** — GutLog shows "1 medicine waiting for review"; RxGuard
   shows the draft with its quotes. You tap **Approve**, **Edit** or **Reject**.
   Nothing enters the knowledge base without your approval — you are the
   clinician; the system only collects and cites.
4. **On approval RxGuard re-checks the whole current stack at once.** A new RED
   raises a banner on GutLog's Now tab.

## Where the data comes from (verified, free, government)
| Need | Source | Notes |
|---|---|---|
| Salt spelling → standard ingredient | **NLM RxNorm API** (RxNav) | Normalises names, gives the ingredient ID used for everything else |
| Label facts: interactions, boxed warnings, QT warnings, renal/hepatic dosing | **openFDA drug-label API** (FDA SPL / DailyMed) | Section-by-section JSON; quoted verbatim into the draft |
| CYP strength (strong/moderate/weak inhibitor, sensitive substrate, inducer) | **FDA Table of Substrates, Inhibitors and Inducers** | Cached locally, refreshed monthly; feeds the existing CYP engine unchanged |
| QT risk category | Label wording now; **CredibleMeds** only if you register personally | QTdrugs list is licensed; the API is not free |

What is **not** used, and why: NLM's own interaction API was **shut down in
Jan 2024**; DrugBank and commercial checkers need paid licences. Interaction
findings are therefore built deterministically from the labels of *both* drugs
("label of A names B or B's class" → finding with the quote; RED only where the
label says contraindicated / do not co-administer; otherwise AMBER). The
existing 24 curated rules still win over anything derived.

**India-only salts and brands** (e.g. molecules with no US label) will not be in
RxNorm or openFDA. Those get a draft marked **no US label**, built with you from
published sources, through the same approval card. Honest gap, stated on the page.

Only molecule names ever leave the server — no personal data goes to NLM or FDA.

## Staying current without a second list
- **RxGuard's medication list becomes derived from GutLog**: the open regimen
  plus anything taken in the last 14 days. No re-typing. RxGuard keeps only what
  GutLog does not hold (prescriber, indication, benefit) as notes on each medicine.
- **Every GutLog regimen change** (start, stop, dose change — already
  effective-dated) becomes a dated change event in RxGuard automatically, which
  is what its "new symptom → recent change" timeline needs.
- **Nightly re-verify**: if a source label changes, the entry is marked
  *source changed — re-review*; entries older than a year show STALE (existing rule).

## All back-data integrations (GutLog is the surface; the others read it)
| From GutLog | To | Used for |
|---|---|---|
| Regimen, doses, salts + strengths | RxGuard | The derived list and every check (salts are new) |
| Regimen changes | RxGuard | Change history, withdrawal rules, symptom timeline |
| Symptom episodes | RxGuard | Symptom-vs-recent-change ranking without re-entry |
| Vitals (BP, pulse) | RxGuard | BP-raising and rate-lowering drug rules get real readings |
| Labs (creatinine/eGFR, K, Mg) | RxGuard | Renal dosing and QT risk modifiers from actual results |
| Doses | FitLog | Analgesic-frequency warning — **live since v1.1.0** |
| **Back to GutLog:** RED findings, "waiting for review" | GutLog Now tab | One place to see what needs you |

All over the existing read-only feed (token already in place); nothing reads
another app's database file.

## Build order (each step offline-tested, one install each)
1. **GutLog: salts + strengths** — new ingredients table, inline prompt on add,
   a "N medicines need a salt" card for existing ones, feed carries ingredients.
2. **RxGuard: source fetcher + draft + review queue** — RxNorm, openFDA, FDA CYP
   table; approval writes the versioned knowledge file with source and date.
3. **RxGuard: list derived from GutLog** + automatic change events + RED/review
   banner back to GutLog.
4. **Vitals, labs and symptoms into RxGuard** checks.
5. **Nightly re-verify** and source-change marking.
6. **Clear today's backlog**: run every currently "not checkable" medicine
   through the same review cards.

Design rules kept: GutLog stays friction-free; no AI in the checking path (the
engine stays rule-based and auditable); every fact cites its source; still no
GREEN; manual entry in RxGuard stays as the fallback.

## What I need from you
Only an OK on this plan (and later, the approvals on the review cards).

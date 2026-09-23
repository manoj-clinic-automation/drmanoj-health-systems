# RxGuard v1.8.2

`rx.dr-manoj.in` · service `rxguard` · port 8031 · `/root/rxguard`

Personal medication interaction and safety review. Single user, self-hosted.
Flask + SQLite + gunicorn + systemd — the same pattern as GutLog.

## What it is for

Multiple specialists, several of them consulted by phone, plus self-treated
MSK and radicular episodes. No single person is holding the whole list.
This is the place the complete picture exists, and the thing that carries it
into each consultation.

## What it deliberately is not

It does not diagnose, prescribe, or approve treatment. It does not replace a
prescriber. It is not a general-purpose interaction checker and must not be
used for clinic patients — there is no patient identity field in the schema,
and that is intentional.

## Design decisions worth knowing before you trust it

**There is no GREEN.** Flags are RED, AMBER and UNKNOWN. Absence of a flag
means nothing was found in this knowledge base, not that a combination is
safe. A green badge becomes a green light regardless of the disclaimer under
it, so there isn't one.

**Coverage is the ceiling.** 60 molecules, curated narrowly and deeply for
your actual and plausible drug set rather than broadly and shallowly for
everyone. Anything not in `knowledge/drugs.json` gets no check at all and is
reported as UNKNOWN rather than passed over in silence. Check
`/knowledge` for what is covered.

**Findings report the change, not the baseline.** Cumulative burden already
present in the list is shown on the dashboard. The analysis screen only
raises a burden finding when the proposed drug actually contributes to it,
and shows the delta. Otherwise every analysis restates the same background
and the screen trains you to dismiss it.

**Curated rules beat derived ones.** Where a named pairwise rule exists it
suppresses the generic CYP-property derivation for that pair, so the same
interaction is not reported twice in different words.

## As taken (GutLog) — v1.1.0

RxGuard checks the list typed into it; GutLog records what is actually taken.
The **As taken (GutLog)** page (and a one-line summary on the Dashboard) reads
GutLog's read-only feed over loopback and:

- lists every molecule from GutLog (regimen + doses in the last 14 / 30 / 90
  days) and from this list, with how often it was taken, whether it is on the
  list, and whether the knowledge base covers it;
- runs the engine across the **whole as-taken stack** — named pairwise rules,
  CYP derivation (suppressed where a named rule covers the pair), class
  duplication, condition rules, burden and QT stacking — judging each pair
  once, and marks findings that involve a medicine missing from the list;
- reports taken-but-not-listed (AMBER, reconciliation), molecules outside the
  knowledge base and GutLog medicines with no molecule (UNKNOWN, named), and
  listed-but-not-logged.

Read-only; still no GREEN. If GutLog is unreachable the page says so and every
other screen is unaffected. Config: `GUTLOG_FEED_URL` (default
`http://127.0.0.1:8020`), `GUTLOG_FEED_TOKEN_FILE` (default
`/root/gutlog/feed.token`, created by GutLog). The feed follows the live
database: a database outside the app folder (the test suites) never reads it;
`RXGUARD_GUTLOG_FEED=1/0` overrides.

Tests: `test_astaken.py` **15/15** — a real GutLog on a scratch database and
token, RxGuard on a scratch database; covers reconciliation both ways, the
named rule firing once, CYP suppression, UNKNOWN coverage, order, read-only,
days parameter, dashboard, login, wrong/missing token, scratch-DB isolation,
GutLog down. `smoke_test.py` 42/42 and `validate.py` 50/50 unchanged.

## The NaSSA from its label, MAO inhibitors, as-needed counting — v1.8.4 (DEPLOYED 2026-09-23 21:16 IST)

**Engine.**
- *As needed counts only when taken.* In `analyse()` a row of kind
  `episodic`/`prn` counts toward a burden total and the QT sum only if GutLog
  logged a dose of it **today** (IST). Pairwise and CYP checks still see it —
  a rarely taken drug still interacts on the day it is taken. **If GutLog
  cannot be read, it counts**: a missing feed must never make a total look
  smaller. What was left out is named on each burden finding ("Not counted:
  …") and returned as `not_counted`. `/astaken` is unchanged.
- *The MAO-inhibitor contraindication fires.* The `mao_inhibitor` marker was
  in the base and read by nothing. A drug marked `mao_contraindicated` with
  an MAO inhibitor — current, or **stopped within 14 days** — is RED, from
  either side. MAO inhibitors are the marked entries plus
  `mao_inhibitor_keys` in `rules.json`, so one not in the base counts by name.
- *Start checks.* A drug may carry `start_checks`, shown when the action is
  start — here the sodium check 2–3 weeks after starting.

**Knowledge (drugs 1.1.0 → 1.2.0, rules 1.2.0 → 1.3.0).** The NaSSA entry
from its US label (openFDA, 23-Sep-2026): strengths, the sleep-dose note (the
owner's), MAO contraindication, sodium start check, appetite/weight, glucose,
agranulocytosis, benzodiazepines, Z-drugs, alcohol. PW035–PW037 strong
CYP3A4 inhibitors and PW038–PW040B strong CYP3A inducers, AMBER with the
label's dose advice; PW041–PW045 Z-drugs and benzodiazepines; PW046–PW047
serotonergic caution; CR017 with diabetes. **CYP2D6 inhibitors were asked for
and are deliberately not a rule**: the label's own study with a strong
CYP2D6 inhibitor found no relevant change in its pharmacokinetics, so the
rule would contradict
its own source — it is in the notes instead. The PW037 AMBER replaces the
engine's derived RED for the strong 3A4 inhibitor, because a curated rule
beats the derivation and the label says "a decrease in dosage may be needed",
not "avoid".

**Server-only.** A benzodiazepine-receptor agonist on his list had no entry
anywhere, so it was invisible to every check; it is in the approved overlay
(`drugs.local.json`) with its evidence quoted from a human interaction study
(no US label, no RxCUI), plus one overlay rule. His list was brought into
line with GutLog the same evening — two stops, one date corrected, one row
marked as needed, one added — each with a `med_events` row (source `owner`).
DB backup `rxguard.db.pre-v184-20260923_211525`; overlay backups
`*.local.json.bak-v184-20260923_211525`.

`test_v184_nassa.py` 6/6, names no medicine, stubs the GutLog feed. Negative
control 11/11, including `feeddown` — a feed that cannot be read must not
shrink a total. All 12 suites green on the server, smoke 49/49 with the live
list resolving. Rollback: the three `*.bak-v184-20260923_211525` files.

## An orexin receptor antagonist, and narcolepsy — v1.8.3 (DEPLOYED 2026-09-23 20:59 IST)

A dual orexin receptor antagonist hypnotic was not in the knowledge base —
checked in the curated files and in the server's approved overlay first. It
is now, from its US label (read through openFDA, 23-Sep-2026) with its RxCUI
from NLM RxNorm: CYP3A4 major substrate, sedation 3, 5 and 10 mg tablets
(`strengths`), start 5 mg, max 10 mg nightly, max 5 mg with a weak CYP3A
inhibitor; hepatic: **moderate impairment max 5 mg, severe not
recommended** (the label's wording, which differs from the "severe: max
5 mg" in the request — the label won). The brand name resolves as a synonym.

Rules (`rules.json` 1.1.0 → 1.2.0, `drugs.json` 1.0.0 → 1.1.0):
- **PW025–PW030 RED** "avoid": strong or moderate CYP3A inhibitors, six of
  them, including the base's own moderate one the request did not list.
  Three are not in the base as entries; a pairwise rule matches on the key,
  so it still fires if one is ever recorded.
- **PW031 / PW031B RED**: the strong CYP3A inducer, under both its names.
- **PW032–PW034 AMBER**: additive CNS depression with the three Z-drugs.
  Opioids and benzodiazepines are covered by the sedation-burden threshold;
  alcohol lives in the notes and every rule's action.
- **CR016 RED**: the class with **narcolepsy**, now a condition in
  `CONDITIONS` (the only `app.py` change).

A curated rule beats the property engine for the same pair, so the label's
"avoid" wording is what shows. `test_v183_orexin.py` 6/6 **names no
medicine** — every drug comes out of the knowledge base by rule id or class —
so it can never carry a name from his list into the public tree; the patcher
is on NO_SECRETS' `CLINICAL_ALLOW` for the same reason as the v1.4.0 one.
Negative control 7/7 (six by load-time mutation, because the reconstructed
previous `app.py` reads the same knowledge files). `test_conditions` 00 was
changed on purpose (it pinned the last six condition rules and the rules
version). All 11 suites green on the server; `app.py` `527bae52…`,
`drugs.json` `c84c2525…`, `rules.json` `c915d6d2…`. Rollback: the three
`*.bak-v183-20260923_205827` files, then restart.

## One session key for every worker — v1.8.2 (DEPLOYED 2026-09-20 12:46 IST)

With `RXGUARD_SECRET` unset, each gunicorn worker minted **its own random
session key**. Two consequences, both of which read as bugs in something else:
a request that landed on the other worker looked **logged out**, seemingly at
random; and **every restart logged him out**, because the keys were new again.

v1.8.2 falls back to a key file, `<db>.secret`, mode 600, created **once**
through an atomic link — so four workers starting in the same instant agree on
one key rather than racing to overwrite each other. An environment key still
wins, and when one is set **no file is written at all**. A broken or truncated
key file neither stops the app nor gets silently overwritten.

**On this server nothing changed for him**: `RXGUARD_SECRET` is set in
`rxguard.env`, so the fallback is inert and no key file was created. The fix
matters for the case where that variable is ever removed, and for any clone.

### Evidence

`test_secret_file.py` **5/5 on the server**. Negative control **6 declared, 6
seen to fail** — 01, 02 and 05 by version; 03, 04 and 05 by mutation, one of
them the **non-atomic create**, which is the failure that only appears under a
real race and would otherwise ship unnoticed.

On the Windows workstation case 02 fails on `mode is 666` — the POSIX chmod
artefact (gap 11). Worth noting precisely: that assertion checks the mode
**first**, so on Windows the rest of case 02 — 64-hex key, reuse after a
restart, no temporary file left behind — is never reached. The server run is
the only one that tests them.

Regression on the server: `dose_ceiling` 15/15, `astaken_honest` 16/16,
`astaken` 15/15, `reconcile` 18/18, `conditions` 10/10, `kb` 32/32,
`smoke` 49/49, `validate` 50/50, `ops/test_sso` 11/11. `--reverse`
byte-identical to v1.8.1. `/healthz` reports `ok 1.8.2`.

**Not verified:** that a real login survives a restart. That needs his
password, which is his to type — the suite proves the mechanism, and the
env-key path means nothing changed for him in any case.

## Ceilings are label maxima — v1.8.1 (DEPLOYED 2026-09-20 11:18 IST)

v1.8.0 shipped every ceiling as a **default awaiting his confirmation**, with a
Confirm button on each row. Two things were wrong with that, and he said both
on the day:

**The confirming was a tax, not a safeguard.** Asking a doctor to confirm a
maximum daily dose that is printed in the label is asking him to retype a fact.
Worse, an unconfirmed ceiling reads as provisional — so the one number the page
exists to be trusted on arrived hedged. Every ceiling is now **the label
maximum, carrying its source text**; the "default" chip and the Confirm button
are gone. He can still set his own limit, behind a collapsed *Change*, and it
shows as *your limit*. **Clearing that box now returns to the label maximum**
rather than to "no ceiling" — the old behaviour turned a correction into a
silent removal of the guard.

**And the first version flagged an accepted dose.** A ceiling set below what
the label allows does not make him safer; it makes the page cry wolf, and a
page that cries wolf stops being read. The one ingredient this bit on is now
at its licensed maximum, with a **course limit** instead: above the
lower-indication dose for more than a short run of days is what actually
matters there, and that is the new rule **DC010**.

Two ceilings are marked *not yet primary-checked* in the rules file and say so
in their own source text. That is deliberate: a sourced number that names its
own weakness is honest, an unsourced one that looks like the rest is not.

The dose feed now looks back **10 days** rather than 4, because a course limit
cannot be seen in a 4-day window — mutation-controlled, by shortening the feed
until no run can be observed and requiring the DC010 assertion to catch it.

Everything else is unchanged: the pools, the 20 h / 24 h windows, DC001–DC009,
the sidecar engine with no network or database, the rules file server-only at
mode 600.

### Evidence

`test_dose_ceiling.py` **15/15** (was 13; case 10 rewritten, 14 and 15 new) on
the workstation and on the server. Negative control **3 declared, 3 seen to
fail** — 10 and 14 by version, 15 by mutation of the feed length.

DC010's own run comparison lives in the sidecar, which
`tools/NEGATIVE_CONTROL.py` cannot mutate, so it was broken by hand in the
current engine — requiring the run to exceed the allowance by a hundred days —
and **case 15 alone caught it**, with the engine restored hash-checked
afterwards. The same gap as v1.8.0: teaching the harness to mutate a named
module is still the fix, and still not done.

Server gates: `test_astaken_honest` 16/16, `test_astaken` 15/15,
`test_reconcile` 18/18, `test_conditions` 10/10, `test_kb` 32/32,
`smoke_test` 49/49, `validate` 50/50, `ops/test_sso` 11/11. `--reverse`
byte-identical to v1.8.0. `/healthz` reports `ok 1.8.1`.

**On the real record the page now raises nothing at all**, where v1.8.0 raised
one amber — and the ingredient it used to flag reads **at its ceiling, not
over**, which is exactly the distinction he asked for.

## One sign-in across the three apps — HEALTH_SSO_V1 (2026-09-20 09:14 IST)

Moving from GutLog into RxGuard used to mean typing a password again. Now a
plain page load (GET, `Accept: text/html`) with no session goes round the ring
gutlog → rxguard → fitlog → gutlog via `/sso/vouch`; the first app that already
has a session mints a ticket, and RxGuard's `/sso/in` signs him in exactly as
its own password would, then continues to the page he asked for. Nobody signed
in anywhere → RxGuard's own login, in at most three redirects.

The ticket is **HMAC-SHA256 over {iss, aud, exp, nonce}** with a key only the
server holds, bound to **one** receiving app, valid **60 seconds**, usable
**once** (the nonce goes in the receiving app's own `sso_used` table). It rides
in a URL for a single redirect, over HTTPS.

What it deliberately never does: bounce an API or XHR call (they get the same
login redirect they always did); follow a `next` that is not a plain path;
send the browser anywhere but the three configured origins (an unknown target
is a 404); or override Lock — logout sets a hold, so a locked app stays locked
until its own password, even while the other two are open.

**No `/root/health-sso.key` (mode 600, root) and every app behaves exactly as
before** — that is both the default and the off switch, and it was re-verified
live with the key absent before the key was created. Passwords, owner keys,
epochs, feed tokens and API behaviour are unchanged; RxGuard's `RXGUARD_SECRET`
was already a 64-character value, which is the precondition for vouching at all.

Module `health_sso.py` beside `app.py`; patch `ops/patch_sso.py --app rxguard`
(4 anchors here); suite `ops/test_sso.py`, **11/11** offline and on the server,
running all three real apps on 127.0.0.1/.2/.3 so their cookies stay apart the
way three subdomains do. Against the three pre-SSO builds the same suite splits
exactly as it should: 02, 03, 04, 06, 07, 08 and 11 fail, and 01, 05, 09, 10
still pass — those four guard behaviour that must **not** change.

## Daily dose — v1.8.0 (DEPLOYED 2026-09-20 08:46 IST)

His ask, 19-Sep-2026: watch the **total dose of each ingredient**, not only
duplicates. Until now RxGuard could say *these two products interact* but not
*you have had too much of this today* — and the second question is the one that
gets answered wrong by ordinary, sensible-looking behaviour.

Three things make one pool, and all three are the point:

- an ingredient **inside a fixed-dose combination** counts into the same pool
  as the standalone product;
- **one generic by two routes** is one pool, not two;
- some **classes** carry a load of their own, independent of any one member.

Every logged dose is broken into its ingredients (amount per unit × units
taken), summed over a rolling window, and held against a ceiling.

### The windows, and why they are not both 24 h

| kind of ingredient | window |
|---|---|
| once a day | **20 h** |
| several times a day | **24 h** |
| class sedative load | 12 h |

A flat 24 h window is the obvious choice and it is wrong. A nightly medicine
taken at 22:00 one night and 21:30 the next is **23.5 h apart** — inside a 24 h
window, so a flat rule reports two doses in one day, every time he goes to bed
early. The first build did exactly that. 20 h for once-a-day ingredients is
what stops a false alarm that would have fired most weeks, and it is
mutation-controlled: widening it back to a flat 24 h makes the assertion fail.

"Now" is IST computed from UTC (`RXGUARD_UTC_OFFSET_MIN`, default 330), so the
window is right whatever zone the server clock is in.

### The rules

| id | level | what it says |
|---|---|---|
| DC001 | RED | an ingredient is over its ceiling in the current window |
| DC002 | AMBER | an ingredient went over its ceiling in the last 3 days |
| DC003 | AMBER | an ingredient with no ceiling set was taken — set one |
| DC004 | AMBER | more units of a product-limited ingredient than allowed (patches are counted, not weighed) |
| DC005 | AMBER | more distinct members of a class than the class allows |
| DC006 | AMBER / RED | one addition on top of a class's regular medicine; RED for two or more |
| DC007 | AMBER | two members of a "not together" class in the same window |
| DC008 | UNKNOWN | the same product logged twice within 10 minutes |
| DC009 | UNKNOWN | a dose of a ruled ingredient whose amount cannot be read |

**DC008 counts the dose and names it.** A double entry is *counted*, because
the higher total is the safer claim, and then said out loud so he can correct
it. Silently discarding the second entry would make the safer reading
unavailable. **DC009 never guesses.** A concentration (mg/mL) is not an amount
per unit, so a syrup stays DC009 until the rules file gives an amount — an
invented number in a dose total is worse than no number.

Composition is read from GutLog's own `molecule` and `strength` fields
("a + b" / "60 mg + 325 mg"), falling back to the number in the name only when
no strength is recorded. A varying-strength medicine is read from the strengths
actually picked.

### What is where

The engine is `dose_ceiling.py` — pure functions, no network, no database, no
Flask; `app.py` hands it the feed and the rules and renders what comes back.
It reads GutLog's existing `/api/feed/doses` and `/api/feed/stack` and nothing
else, so **GutLog is not changed by this release at all**. The ceilings name
his medicines, so they live in `knowledge/dose_rules.local.json` — server only,
mode 600, gitignored (CLAUDE.md §5d). **A missing rules file is not an error**:
the feature is simply not set up, and says so in one line.

A ceiling typed on `/dose` overrides the file and is marked confirmed; every
file ceiling reads *default* until he confirms it. Findings join the as-taken
list, so `/astaken`, the Dashboard count and GutLog's banner all carry them
with no change on the GutLog side. New table `dose_ceilings`, created by
`SCHEMA`; no migration step.

**Blocking at the moment of logging is phase 2, not this build.**

### Evidence

`test_dose_ceiling.py` **13/13** on the workstation and on the server, running
a real GutLog on loopback with **invented molecules**, so no assertion can pass
by accident on the real record. Negative control: **13 declared, 13 seen to
fail** against the reconstructed v1.7.0.

Those 13 are all *version* controls, and that is a weakness worth naming: v1.7.0
has no dose engine at all, so every one of them fails there for the same trivial
reason. For the three assertions that guard a **boundary** rather than a
feature, the boundary was additionally broken on purpose in the current engine
and each was caught by exactly its own assertion and no other:

| mutation | caught by |
|---|---|
| a combination reads only its first molecule | 01 |
| the 20 h window ignored, everything flat 24 h | 06 |
| the double-entry check removed | 08 |

`tools/NEGATIVE_CONTROL.py` can only mutate the **app** file, and these
properties live in the sidecar engine module, so those three are not yet in the
manifest — they were run separately and are recorded here. Teaching the harness
to mutate a named module is the fix, and it is not done.

Server gates before the restart: `test_astaken_honest.py` 16/16,
`test_astaken.py` 15/15, `test_reconcile.py` 18/18, `test_conditions.py` 10/10,
`test_kb.py` 32/32, `smoke_test.py` 49/49, `validate.py` 50/50. `/healthz`
reports `ok 1.8.0`.

## As taken, honestly — v1.7.0 (DEPLOYED 2026-09-15 09:40 IST)

`app.py` sha256 `291796f8…`, 153,331 bytes on the server, **byte-identical to
the repo build**. `patch_rxguard_v170.py`, 12 anchors, reversible — reversing
reproduces v1.6.0 at exactly 142,680 bytes, which was verified against the
server's pre-patch file by hash before anything was written. Rollback:
`cp /root/rxguard/app.py.bak-v170-20260915_094048 /root/rxguard/app.py`.
Deployed **before** GutLog v3.18.0, because GutLog's home banner reads
`/api/feed/status` from here and the reverse order would have put calm styling
around the old inflated count.

Server gates before the restart, all green: `test_astaken_honest.py` 16/16,
`test_astaken.py` 15/15, `test_reconcile.py` 18/18, `test_conditions.py`
10/10, `test_kb.py` 32/32, `smoke_test.py` 49/49 (49 rather than 48 — the
live-list check only runs on the server), `validate.py` 50/50. `/healthz`
reports `ok 1.7.0`.

**On the real list, the headline went from `2 RED 10 AMBER` to `0 RED 7
AMBER`**, with 4 findings moved into the theoretical section and 1 not
checkable. Confirmed on the rendered page: 12 finding cards, all 12 collapsed
with the title and consequence in the head and the mechanism behind the tap,
3 carrying an action marker, the theoretical section present, no per-finding
POSSIBLY STALE block anywhere, the caveat at the foot, and no "plus the
proposed change". Structure only was read off the page; the findings
themselves stayed on the server.


`/astaken` led with two REDs and ten AMBERs, and GutLog's home banner mirrored
the count. Both REDs were cumulative burdens whose largest contributors had
**no logged dose at all** inside the window — as-needed medicines that had
simply not been needed. The engine already knew: it printed a POSSIBLY STALE
paragraph under eight of the thirteen findings saying exactly that, with the
alarming total first and the correction last. A daily red badge for a burden
nobody is carrying is how a real red badge stops being read, so this is a
correctness fix rather than a cosmetic one.

**Two totals per burden.** *As taken* counts only molecules GutLog logged a
dose of in the window or carries in its own regimen. *If all taken* is the old
behaviour, every molecule on the list. The chip, the page headline and
GutLog's banner all read the as-taken figure; the if-all-taken figure stays
inside the finding, labelled `If every medicine on the list were taken`, and
is counted nowhere. The `kind` column is deliberately **not** consulted — it
is typed by hand, and one wrong `chronic` there would silently restore the
inflated count.

**One theoretical section, one caveat.** A finding that reaches a threshold
only once the untaken medicines are added back — and any finding resting
entirely on molecules with no dose — moves into a single collapsed section,
*If you also take your as-needed medicines (n)*, which names those medicines
once. The per-finding POSSIBLY STALE block is gone from this page; the same
fact is now said once, in one place. The QT score is computed the same way —
a cumulative total is a cumulative total, and leaving that one inflated while
fixing the others would have been the same defect in a different category.

**A finding with one taken drug in it stays current, whatever else it rests
on.** A pairwise interaction between something taken and something not is
still reported, still counted, and marked as before; only a finding whose
molecules are *all* untaken moves. The stronger claim is the safer one, and
downgrading a half-current interaction would be exactly the mistake this
release exists to undo, in the opposite direction. The chronic "may have been
stopped" case is still the Reconciliation section above, unchanged — a daily
drug missing from GutLog's regimen is a reconciliation problem, not a
theoretical finding.

**The finding card collapses.** Severity chip, title and CONSEQUENCE stand;
mechanism, why-it-applies, monitoring, action and source are one tap away. A
finding carrying an ACTION says so *before* it is opened, so the one kind that
must not be missed is never the one hidden. `/astaken` only — Quick check and
Full analysis answer "what if this were added", and there the whole of the
reasoning is the answer.

**Two wording fixes.** The cumulative-burden MECHANISM no longer claims "plus
the proposed change" on a page that has no proposed change. The
absence-of-a-flag caveat is correct, kept, and moved to the page foot.

Tests: `test_astaken_honest.py` **16/16**, and `tools/NEGATIVE_CONTROL.py
--manifest new_assertions_v170.json` **PASS — 16/16 assertions seen to fail**
(14 against the reconstructed v1.6.0, two by deliberate mutation). The one that
matters is assertion 04: it asserts the *pair* of totals before and after a
dose row is added for an untaken medicine, because "the burden is RED once the
dose exists" was true of v1.6.0 too and would have proved nothing. Existing
suites: `test_astaken.py` 15/15, `test_reconcile.py` 18/18 (its POSSIBLY STALE
assertion updated to the new location), `smoke_test.py` 48/48, `validate.py`
50/50. The fixture in `test_astaken_honest.py` uses molecules chosen for the
thresholds they cross, none of them on the owner's record.

## Reconciliation — v1.5.0 (deployed 2026-09-14 04:42 IST)

`app.py` sha256 `26d6ae8b…`, 136,485 bytes, byte-identical to the repo build;
8/8 anchors. Suites on the server before the restart: `test_reconcile.py`
13/13, `test_astaken.py` 15/15, `smoke_test.py` 42/42, `test_kb.py` 32/32,
`test_conditions.py` 10/10, `validate.py` 50/50. `/healthz` reports `ok 1.5.0`;
`medications` and `med_events` untouched by the deploy (16 and 22 rows), which
is the point — nothing writes without a tap. Rollback:
`app.py.bak-v150-20260914_044233`, or `app.py.predeploy-phaseI-20260914_043720`
and `/root/backups/rxguard/rxguard.db.predeploy-phaseI-20260914_043720`.

### What it found on the first run

Both REDs rest on a drug GutLog has not seen, and **both are now marked
possibly stale** — see "First findings" below.


The v1.1.0 page above *showed* the mismatch; the engine never saw it. It runs
on `active_meds()` — RxGuard's own `medications` table — and nothing updated
that table. So a medicine ended in GutLog stayed active here, and every check
kept counting it, until the list was edited by hand days later.

**It still does not auto-write.** Status here carries clinical meaning GutLog
does not have — tapering is not stopped, and "not logged" is not "not taken"
for a patch or an eye drop — and a drug record that rewrites itself from a
logging action is untrustworthy. Instead:

- **a)** A reconciliation line whenever a medicine is *active* or *tapering*
  here **and** absent from GutLog's regimen **and** has had no dose for
  `RECONCILE_GAP_DAYS` (7) or more. All three, or no line. One tap sets status
  and `stop_date` **from GutLog's `valid_to`** — not from today, because the
  day a mismatch is noticed is not the day the drug changed. Where GutLog
  records no end date there is no one-tap stop: the line says so and points at
  the Medications page.
- **b)** The mirror case, inverted: in GutLog's regimen, unknown here. One tap
  adds it as active from GutLog's `valid_from`.
- **c)** Any RED or AMBER finding resting on a drug GutLog has not seen for
  `GUT_STALE_DAYS` (14) or more is marked **possibly stale** on the finding
  itself — the same discipline the knowledge base already applies to its own
  review dates. The finding is never silently dropped: a RED that may be about
  a stopped drug is still a RED until someone decides otherwise.

Every applied line disappears on the next read, and a repeated tap writes
nothing. The Dashboard carries a one-line count.

Requires GutLog **v3.12.0**, which added `valid_from` on each regimen line and
an `ended` list of recently closed schedules with their `valid_to` to
`/api/feed/stack`. Against an older GutLog the lines still appear; only the
one-tap stop date is unavailable.

## Keys the knowledge base cannot resolve — v1.6.0 (deployed 2026-09-14 05:08 IST)

A `drug_key` that does not resolve contributes to **nothing**: no pairwise
rule, no CYP derivation, no class duplication, no burden, no QT sum, no
condition rule. The drug is absent from every check on every screen, and the
screen looks exactly as it would if the drug were safe. That is the worst
failure this application has, and one missing letter was enough to cause it.

- `unresolved_keys()` checks every row. The **Dashboard carries a banner at
  the top** when any row is unresolved — above the medication table, naming
  each key and why it fails — and `show_reds.py` leads with the same list
  before printing a single finding.
- Active and tapering rows are called out as the live gap they are; stopped
  rows are reported too, more quietly, because a record that misleads later is
  still a defect.
- A key containing `+` gets its own message: RxGuard holds **one row per
  molecule**, so a combination belongs on two lines.
- **It is a gate.** `smoke_test.py` step 9 proves the mechanism on its own
  synthetic fixture and then asserts that the **live** list has no unresolved
  key, so a future typo fails the pre-restart gate instead of hiding. When the
  live database is not on the machine the check reports SKIPPED out loud,
  never silently. `test_kb.py` runs the smoke suite with `RXGUARD_DB` pointed
  at a non-existent path, so a knowledge-base suite is never coloured by the
  owner's list (CLAUDE.md 5a) — and it now asserts *nothing failed* rather
  than pinning a check count that adding a check would break.
- `fix_drug_keys.py` corrects one: `--rename OLD NEW` moves `medications` and
  `med_events` together and refuses a NEW that does not resolve either;
  `--split OLD A B` turns a combination row into one row per molecule, keeping
  the dates, status and notes. Dry-run by default, takes its own
  `sqlite3.backup()` first, and records a `key-corrected` event so the change
  is visible rather than mysterious.

### Reconciliation is for chronic drugs only — v1.6.0

"Not taken for 7 days" means something for a drug meant to be taken daily and
nothing at all for an as-needed analgesic. `medications.kind` already carried
this, so the "still active here" test now skips anything that is not
`chronic`. On the real list that took the reconciliation lines from four to
one, and the one left is a genuine mirror case.

### Staleness says which kind of stale — v1.6.0

The same 14-day silence means two different things, and one sentence for both
was wrong:

- chronic, absent from the regimen → *may have been stopped — reconcile*;
- as-needed, not taken recently → *this burden may be theoretical rather than
  current*.

A finding resting on both gets both sentences, chronic first. Nothing is
dropped either way. Verbs agree with the number of drugs, because "a, b, c
**is** as-needed and **has** not been taken" is how a clinical screen starts
looking unmaintained.

### First findings (2026-09-14, 10 active/tapering)

Recorded as a shape, not as values — the values are the health record and stay
on the server. Run `show_reds.py` for the current picture.

- **Both REDs are cumulative-burden findings, and both are marked possibly
  stale.** Each rests on three molecules, and in each case one of them has had
  no dose in GutLog for 14+ days and is absent from its regimen. So the answer
  to "is at least one RED resting on a drug already stopped?" is: *possibly
  both* — which is exactly the state the old page could not express.
- 11 AMBER, **8 of the 13 findings marked stale**. The marking is doing real
  work rather than decorating.
- **The reconciliation caught a spelling mistake from both directions at
  once.** One molecule was misspelled in the RxGuard list, so it had no
  knowledge-base entry and contributed to no finding at all; meanwhile the
  correctly-spelled molecule arrived from GutLog and read as "taken but not on
  your list". Two lines, one cause. **Three such keys were found in all** —
  one active, two stopped, one of those a combination — and all three are now
  corrected. Fixing the active one made the engine see **10 molecules instead
  of 9** and moved the **constipating burden from 5 to 6**; no other burden
  changed, and the RED and AMBER counts held at 2 and 11. So the RED was
  right, but it had been computed on a shorter list than the screen implied.
- **Both REDs are stale for PRN reasons.** After v1.6.0 they read *"may be
  theoretical rather than current"* rather than *"may have been stopped"* —
  the distinction that was missing when this was first reported.
  **Superseded by v1.7.0.** Saying it under each finding, after the total, was
  not enough: the headline and GutLog's banner both still read the inflated
  number every morning. From v1.7.0 the count is the as-taken one and neither
  of these REDs is in it; both appear, named and uncounted, in the theoretical
  section. Re-run `show_reds.py` for the current shape.

`show_reds.py` answers the question from the terminal, read-only, without
changing anything: every RED and AMBER, the molecules each rests on, and
whether the staleness test marks it — plus the reconciliation lines.
`python3 show_reds.py` on the server. Its output **is** the health record, so
it goes to the terminal and nowhere else; never redirect it into the repo.

Tests: `test_reconcile.py` **13/13** (2/13 against v1.4.0) — a real GutLog on
loopback with four medicines arranged to exercise each condition separately:
one current in the regimen, one whose schedule ended 8 days ago, one in
GutLog's regimen but absent here, and one dosed 2 days ago. Covers the feed
carrying the end date, firing on all three conditions and on none of the
others, the date coming from `valid_to` and never from today, the undated case
refusing rather than guessing, the inverted case, a RED marked possibly-stale
while findings on current drugs are not, nothing written without a tap, the
page offering the tap, idempotency, and `active_meds()` afterwards reflecting
the change — which is the whole point. Clock-independent: verified under
`tools/RUN_AT_TIME.py` at 00:00, 00:05, 05:05 and 23:58. `smoke_test.py`
42/42, `test_astaken.py` 15/15, `test_kb.py` 32/32 and `test_conditions.py`
10/10 unchanged.

## Sources review — v1.2.0

RxGuard's curated knowledge base covers a fixed list. Medicines change, so
v1.2.0 fills the gaps from **free, verifiable** sources and puts every result
in front of the owner before it counts.

| Source | Gives | Notes |
|---|---|---|
| NLM RxNorm + RxClass | Identity (brand → ingredient), ATC class, spelling suggestions | US National Library of Medicine, public API |
| openFDA drug label | QT, sedation, serotonergic, anticholinergic, bleeding, renal/hepatic, withdrawal, interaction sentences | Every property carries its quoted sentence, label set id and date |
| FDA CYP / transporter table | Strong/moderate/weak inhibitors and inducers, sensitive substrates | Beats label wording for the same field; cached monthly |
| DDInter 2.0 | Pair severity (Major → RED, Moderate → AMBER) | CC BY-NC-SA 4.0 — personal, non-commercial, attributed |
| PvPI (IPC, India) | Drug safety alert links | Links only; first run is a baseline |

`kb_sync.py` runs every 30 minutes by cron (and from *Fetch now*). It reads
what GutLog shows as current (regimen + 30 days of doses, with strength),
builds a **draft** for each molecule RxGuard does not know (a brand already
known under its ingredient becomes an alias), lists DDInter pairs between
known medicines that no curated rule covers, collects PvPI alerts, and once a
day marks approved entries whose FDA label changed as *re-review*.
`python3 kb_sync.py --report` prints what the sources produced (and how far
the last run got); `python3 kb_sync.py --diag` checks that each source
answers from the server. v1.2.1: every download has a whole-transfer
deadline, and the 14 DDInter files are fetched within a time budget per run
and kept one by one, so a slow server is finished over several runs; drafts
built before DDInter was complete are rebuilt once it is. v1.2.2: a
medicine's own ATC class is preferred over a fixed-combination class, label
bullets are split into single statements, pending drafts from an older
builder are rebuilt, and strengths edited in GutLog reach pending drafts.

**Your review** (`/kb`, v1.3.0) leads with what bears on the medicines taken
now: one card per interacting pair (RED first) in plain words derived from
both medicines' properties ("both slow the heart rate", "X blocks CYP3A4,
which clears Y"), the source wording one tap away. Each new medicine shows
chips for what bears on current medicines (and with which) and a line for
what is kept for future checks; kidney/liver notes with no matching
condition, stopping notes and gaps sit in a small footnote. *Accept all
recommended* approves everything in one tap; each medicine can still be
narrowed or rejected. Kidney/liver sentences that ask for no action are
stored as reference and no longer drive a dose-review finding.

Accepting writes the chosen properties and pairs → they are
written to `knowledge/drugs.local.json` / `rules.local.json` (never
committed) and the engine uses them at once. The curated `drugs.json` /
`rules.json` always win. Gaps are stated ("label silent — not proof of
none"); still no GREEN. `/api/feed/status` (GutLog's feed token) gives GutLog
its banner counts. Only molecule names ever leave the server. Paid and
licensed sources (e.g. CDSCO-backed Indian compendia, commercial checkers)
are deliberately not used.

Tests: `test_kb.py` **32/32** — a fake server replaying the real formats:
FDA table parser and outage fallback, DDInter index, RxNorm identity, label
choice, draft properties and pairs, FDA table over label, sync + idempotency,
review page, partial approval reaching the engine, reject, alias, pair
decisions, curated wins, status feed, label re-verify, all sources down,
Fetch now, login, names only, smoke untouched, terminal report (a negated
"not a substrate of CYP" is not read as a role), a trickling server cut
off at the deadline, DDInter resume + draft rebuild, connectivity check.

## Conditions from the health record — v1.4.0

Five more conditions — low platelet count, low sodium, low ionic calcium,
conduction disease, coronary artery disease — and six sourced rules in
`knowledge/rules.json` (1.1.0): CR010 bleeding-risk drug + low platelets,
CR011 sodium-lowering drug + low sodium, CR012 QT drug + low calcium, CR013
rate/AV-slowing drug + conduction disease, CR014 NSAID + coronary disease,
CR015 corticosteroid + diabetes. A condition rule may now name its drugs.

GutLog's health record holds the condition list; `kb_sync.py` reads its
`/api/feed/profile` (codes only) every 30 minutes and ticks them. A code is
unticked only if GutLog set it and no longer lists it; conditions ticked by
hand on the Profile page are never touched. `python3 kb_sync.py --conditions`
runs that step alone. Tests: `test_conditions.py` **10/10**.

## Layout

```
app.py                    application, engine, templates — single file
knowledge/drugs.json      60 molecules: CYP, burdens, QT, renal, withdrawal
knowledge/rules.json      24 named pairwise rules, condition rules,
                          burden thresholds, withdrawal rules, symptom map
smoke_test.py             42 checks incl. the mandatory index case
validate.py               validation harness
validation_cases.json     50 cases with pre-declared acceptance thresholds
rxguard.service           systemd unit (port 8031)
backup.sh                 nightly backup, GutLog cron pattern
kb_sources.py             v1.2.0: fetchers and draft builder (free sources)
kb_sync.py                v1.2.0: cron / Fetch now sync; --report
test_kb.py                v1.3.0: 32 checks against a fake source server
knowledge/cache/          downloaded source data (gitignored, rebuilt)
patch_rxguard_v170.py     v1.7.0 patcher, anchor-verified, --check/--reverse
test_astaken_honest.py    v1.7.0: 16 checks over a real GutLog on loopback
new_assertions_v170.json  the 16, declared for tools/NEGATIVE_CONTROL.py
show_reds.py              terminal answer, read-only; output is the record
```

The knowledge base is versioned separately from the code. Editing a rule
does not mean touching `app.py`, and every finding carries its source and
review date. Rules older than 365 days render as STALE.

## Deploy

```bash
# on the VPS, as root
mkdir -p /root/rxguard && cd /root/rxguard
# upload app.py, knowledge/, *.py, *.json, rxguard.service, backup.sh via WinSCP

pip install flask gunicorn

python3 -c "import secrets;print('RXGUARD_SECRET='+secrets.token_hex(32))" > rxguard.env
chmod 600 rxguard.env

python3 smoke_test.py      # expect 42/42
python3 validate.py        # expect 50/50

cp rxguard.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rxguard
systemctl status rxguard
curl -s localhost:8031/healthz
```

Then add a proxy in CyberPanel for `meds.dr-manoj.in` to `127.0.0.1:8031`,
issue the certificate, and add the backup cron:

```
30 2 * * * /root/rxguard/backup.sh >> /root/rxguard/backup.log 2>&1
```

First visit to `/login` sets the password. There is no registration route
and no second user.

### Upgrading an installed copy

```bash
cd /root/rxguard
python3 patch_rxguard_v170.py --check        # anchors only, writes nothing
python3 patch_rxguard_v170.py                # .bak-v170-<stamp> first
python3 test_astaken_honest.py app.py        # expect 16/16
python3 test_astaken.py /root/gutlog/app.py  # expect 15/15
python3 test_reconcile.py /root/gutlog/app.py
python3 smoke_test.py && python3 validate.py
systemctl restart rxguard && curl -s localhost:8031/healthz   # ok 1.7.0
```

`--reverse OUT` reconstructs the previous version from a patched file; that is
how `tools/NEGATIVE_CONTROL.py` gets a previous build to fail the new
assertions against, and it doubles as a second rollback path beside the `.bak`.

**v1.7.0 pairs with GutLog v3.18.0 and goes first.** GutLog's home banner reads
this app's `/api/feed/status`, so shipping GutLog's neutral banner before this
honest count gives a quiet-looking box around an inflated number.

## Before relying on it

`validation_cases.json` currently carries provisional expected flags and an
unnamed reference standard. Name the reference standard — Stockley's, or
Lexicomp/Micromedex if you have institutional access — and check the 50
expected flags against it before treating a clean run as meaningful. The
thresholds (zero missed RED, at most two false RED) are declared in the file
and are meant to stay fixed. If a run fails them, the knowledge base changes,
not the thresholds.

## First data to enter

1. The three chronic drugs, with prescriber and specialty on each.
2. Conditions and the constraints card in `/profile`.
3. The index case in `/adverse`, with the temporal fields filled properly —
   dose at onset, latency from escalation, what happened on reduction,
   confounders. Entered loosely it becomes an anecdote that outranks the
   literature in this tool's own hierarchy. Entered properly it is evidence
   you can hand to the next prescriber.

## Routine use

- **Quick check** — episodic course, under thirty seconds, before self-treating.
- **New symptom** — enter the symptom before interpreting it. Ranks the last
  six weeks of medication changes against it. This is the screen that exists
  because attribution, not knowledge, was the failure in the index case.
- **One-page list** — print at the start of any consultation.
- **Review queue** — anything you override comes back at two and six weeks
  with your own stated reason shown back to you.

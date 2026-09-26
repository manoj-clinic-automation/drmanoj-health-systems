# Changelog — drmanoj-health-systems

Personal (non-clinic) systems. Per-app detail lives in each app's `DOSSIER.md`;
this file is the cross-app timeline.

## 2026-09-26 (later) — finding recipes, the Kitchen key file, the build lock, the fuller food table

Kitchen 1.2.0, GutLog v3.41.0. **Finding a recipe** in the Family Kitchen (his GutLog
page, every member's, and the kitchen-only book): Person → Category → Recipe and
Category → Person → Recipe with counts on every chip, one search box that matches the
dish, any ingredient, the category and the contributor's name — with Hindi / English
aliases from a data file (lauki = bottle gourd = ghiya, bhindi = okra = lady finger,
ragi = finger millet …) — and filters that combine with either view: food preference,
meal, high-protein, quick (where a time is known), top-rated, new this week, made it by
me. Counts follow the filters; an empty result says what to loosen; the last person or
category opened is remembered on that phone. **The Kitchen's model keys** now live in
their own file (`/root/family/kitchen_keys.env`, root:fam_kitchen 640, the two variables
only); the reader never reads the clinic's file, and says so once when the file is
missing. **The build lock** (`/root/deploy/.claude_code_build.lock`) is taken by
`upgrade_all.sh` and the install scripts themselves: a held lock makes them wait, a
stale one (over 3 h, owner finished) is taken over with a log line, and a trap releases
it on exit, also on failure. **The food table** was rebuilt from the same USDA download
with fat, carbohydrate and calcium; every id and every earlier value is unchanged, and
recipe cards read the three figures instead of "—". `test_family_f.py` 16/16 + 12/12
in the browser; `test_family_lock.py` on the server; `test_food_table_refresh.py` 6/6.

## 2026-09-26 — the weight profile, a seeded member, the physio

GutLog v3.40.0. A third family profile, `weight`, for a member who starts on a Sunday
with a once-a-week injection: a **WEEKLY** medicine line (weekday + time) shown on its
day only, with a reminder from 30 minutes before, a countdown on the other days, and one
question the day after if it was not logged; stock counts it per dose. Meal slots follow
the person's **own meal windows**, so an 11:30 breakfast is breakfast. **Check-ins** on a
schedule — PHQ-9, PHQ-2, Epworth, a side-effect check the day after the weekly dose, the
weekly weigh-in, monthly measurements — appear only when due and go when answered;
totals and bands are computed by name; **PHQ-9 item 9 above 0** asks the member to tell
the caretaker today and puts a same-day flag on the owner's Family page, nothing more.
A **weight and waist chart** with milestone lines (crossed after two consecutive
weigh-ins below), a Check-ins page, and a **This-week card** first on the Now page:
weight, waist, injection countdown, milestone, steps from the phone (blank when none),
protein against the target. The joint cards show folded underneath. **A member is now
seeded from a gitignored file** (`stamp_member.py --seed`): setup, windows, plan,
medicines with salts and schedules, check-in rules and the plan PDF, applied as the
member and the copy deleted. **A physio** (`p1`) signs in at `/m3/physio/` with the
family rules on a cookie of their own and reaches only the programme, sessions, pain and
walking entries and the monthly re-test — the member sees a Physio tile. Sheets for the
member and the physio come from `family/getting_started_sheet.py`. `test_family_e.py`
36/36 + 12/12 in the browser at 300 px; negative control 18 declared, all seen to fail.

## 2026-09-25 (night) — Family Kitchen: recipes-only members, self-publishing, "Recipe by"

Kitchen 1.1.0, GutLog v3.39.0. The Family Kitchen opens to relatives who need only the
recipe book: a **kitchen member** (`k1`, `k2` …) is an account inside the Kitchen service
— no Linux user, no processes, no GutLog/RxGuard/FitLog, no health data anywhere — at
`family.dr-manoj.in/kitchen/k1/`, installing as "Family Kitchen". Sign-in is the family
rules exactly (PIN, lockout, IST log, Face ID, 12 months, sign-out-all), on a cookie
scoped to their own path that satisfies nothing else. **Nobody approves recipes any
more**: a capture is a draft for its sender until they tap Publish; a duplicate offers
"Publish as <Name>'s version"; only the contributor edits or unpublishes; the owner can
hide, never delete. Every card, list, search result and logged meal says **"Recipe by
<Name>"**, with the date, the source site or the original photo, versions side by side,
and raters' names; a "By person" view. Kitchen members may set plain food preferences
(Vegetarian / Eggetarian / No onion-garlic / Jain) that only filter. New: `stamp_member.py
--kitchen-only` (+ rename / reset-pin / disable), `readiness.py --slug k1`,
`kitchen_members.py`, `kitchen_nutrition.py`, `test_family_d.py` (33 checks) with 17
negative controls, a 300 px browser suite; `upgrade_all.sh` now backs up the Kitchen DB,
restarts the Kitchen and checks its health route.

## 2026-09-25 (evening) — family sign-in says whose it is; a readiness check

A PIN typed on the wrong member's page looked like a broken PIN: the owner entered
m1's PIN on m2's sign-in page, which carried no name, and three wrong PINs landed on
m2 while m1 never saw an attempt. The file PINs were right throughout. Each family
sign-in page now names its member and app, says whom to ask otherwise, counts down
"Wrong PIN — N tries left", and says "Paused until HH:MM IST — call <caretaker>". The
PIN has a numeric keypad and a Show/Hide eye; the Face ID button appears only on a
phone where Face ID was set up; the sign-in page carries the diary's manifest and icon,
named for the member, so Add to Home Screen gives the right name. `readiness.py`
checks a member's copy end to end before a link is handed over, signing in once and
only after the file PIN has verified offline. m2's three failed attempts were cleared.

## 2026-09-25 (later) — family sign-in: a PIN, Face ID, a year on the phone

Family copies only. A member signs in with a 6-digit PIN (one scrypt hash; the apps'
own hashes made random and unused); 5 wrong PINs in a row pause sign-in for 15
minutes, doubling each time, across all three of their apps, and every attempt is
logged and shown to them. After a PIN sign-in a phone with Face ID or Touch ID is
offered a passkey, verified on the server in pure Python (no new package) and
cross-checked against the `cryptography` library. A member stays signed in for 12
months on their own device until they sign out; "sign out on all devices" and a PIN
reset end every session; caretaker sessions are never kept. The first real stamp had
failed because the tool used `/root` as its code tree; it now uses the member-readable
tree, refuses one the member cannot read, rolls back fully on failure, and a new suite
stamps through the real per-user path.

## 2026-09-25 — the Family Edition, the joint focus, the Family Kitchen

**Phase A — one copy per relative.** The same GutLog, RxGuard and FitLog code runs
for each family member at `family.dr-manoj.in/m<n>/` (`/rx/`, `/fit/`), each member
as their own Linux user under systemd sandboxing, with their own folder, databases,
keys and tokens; a path-prefix wrapper lets the unchanged apps live under a path.
Nothing of the owner's reaches a copy: the code tree is built by allowlist, and the
one personal field found in the approved RxGuard overlay (the strength he logged)
is stripped on the way in. The owner is caretaker: GutLog v3.36.0's `/family` page
shows each member's summary from a bearer-gated status endpoint and opens their copy
with a one-use ticket; every caretaker change is shown to the member with its IST
time, and the member can switch caretaker access off — off means off. One command
stamps a member (`stamp_member.py`), one upgrades every copy (`upgrade_all.sh`, which
self-tests on scratch members first), backups are nightly and verified. Meal cards
now line up with the six slots (a card may name its slot, or go by the clock).
50/50 + 14/14 in the browser; negative control 20/20, including a member ticket
reaching the owner's `/api/now` and an ignored caretaker switch.

**Phase B — the joint focus** (shown under the `joint` profile only; the owner's
Now page is unchanged). GutLog v3.37.0: a joint pain log with triggers, morning
stiffness and walking tolerance; knee and ankle first among the pain sites; pain
medicines today from the member's own RxGuard; steps and walking minutes beside that
day's joint pain, with one stated next-day comparison and the weight trend; lipid,
liver and CK results with repeat dates and a weekly muscle-ache question when a
statin-class medicine is listed. RxGuard v1.9.0: three lower-limb joint conditions,
the falls-at-night rule FR001 (sourced, deterministic), `/api/feed/dose`, and a gel
kept apart from the tablet ceiling where a rule says so; generic label-maximum
ceilings for pain medicines for copies with no personal file. FitLog v1.8.0: a
knee- and ankle-sparing programme on the same engine. 24/24 + 6/6; negative control
16/16 + 2/2.

**Phase C — the Family Kitchen.** One pool of recipes and ratings, no health column,
seeded from the owner's 52 cards (his stage, estimates and personal note lines left
out). Each GutLog (v3.38.0) shows its own card: nutrition per serving from the
ingredients through stated household measures, unmatched items shown, and personal
adjustments with a "modified" badge and the reason — low-FODMAP swaps and the Food
Test's pulse limit, glucose, sodium, lipids, weight, and medicine-food from the
member's own RxGuard. The shared recipe is never changed. Capture by the iPhone Share
button, a link, a photo or a PDF lands as a draft in the Recipe Inbox and joins the
pool only when a person confirms it, after a duplicate check.

## 2026-09-24 (later) — GutLog v3.35.0: meals and snacks

One slot list everywhere — Breakfast, Mid-morning, Lunch, Evening, Dinner,
Late snack (Eating out kept) — and a guess that files anything after dinner
as a Late snack instead of a second dinner. Foods are arranged in groups with
favourites and recent first, search spans them all, and a missing food can be
added on the spot with its weight and table values. Dry sabzi is logged as a
dish with its variant (tinda, or tinda with paneer) by the katori, worked out
from its parts. Late snacks are one tap; a Quick Bite records what, how much,
why and when in one sheet. A weekly review counts the snacks, says when and
why, suggests a swap for anything eaten twice, keeps his Keep / Swap / Stop
marks and shows next week whether it came down; a one-line nudge appears in
his usual snack time. Food Test days list snacks beside the test food, and
the mirror carries it all. Schema 3.3.7. 15/15, negative control 33/33.

## 2026-09-24 — RxGuard and GutLog lists brought into line (data only)

At the owner's instruction, with both databases backed up first: RxGuard
recorded the two night hypnotics GutLog ended on 23-Sep as stopped on that
date, each with a `med_events` row written exactly as its Reconcile "stop"
writes one (source `gutlog`). One antispasmodic moved from regular to
as-needed (kind `prn`, a `change` event, not stopped); the other was already
as-needed and stays so. GutLog's entry for the new night medicine had its
salt spelling corrected. RxGuard now shows 0 unresolved keys and nothing left
to reconcile. The quick checks were re-run: the new night medicine's check no
longer fires the additive-sedation interaction with a stopped hypnotic, and a
proposed NaSSA now totals sedation 6 rather than 11. No code changed.

## 2026-09-24 — GutLog v3.34.0: Week 0 reads dinner from Meals

The Food Test asked for a dinner already logged in Meals, so Week 0 could
never finish and the ladder due on 30-Sep would never start. Now a meal
logged as Dinner — from the Meals tab or the Now card — completes that day's
Week 0 step by itself, with its time and a size worked out by a stated rule;
editing, moving or deleting the dinner follows through, and the card asks
only "Cramp after dinner?", which may be left blank. There is no second
dinner entry anywhere. 23-Sep was backfilled from the dinner already logged;
days with no dinner stay unlogged. The Now page now runs medicines, meals,
symptoms, the Food Test, then the rest, with the Meals and Food Test cards
folded behind one-line summaries. PRN Add medicine goes on to Salts. 10/10,
negative control 33/33.

## 2026-09-23 (late) — GutLog v3.33.0: where the pain was, and when it began

A pain that started before dinner could not be recorded as such: two pain
tiles, and every episode stamped with the moment Save was pressed. Now nine
abdominal regions sit in a grid as seen facing him, "Started at" is the time
the episode is filed under, and every GI episode says where it fell against
the nearest meal — computed from what was logged, never asked. The evening
score takes a site and an onset too. Day by day is one timeline in true time,
Food Test steps, scores and pain onsets included, each retimed from the same
box and audited. The mirror carries it. 12/12, negative control 24/24.

## 2026-09-23 (night) — RxGuard v1.8.4: as-needed counts only when taken

RxGuard's list had drifted from GutLog's, and its quick check counted an
as-needed analgesic as if it were taken every day — which is why two checks
came back RED for reasons that were not true. The list was brought into line
with GutLog, each change recorded; the quick check now counts an as-needed
medicine only on a day GutLog logged it, and counts it anyway when GutLog
cannot be read. The MAO-inhibitor contraindication, marked in the base for
months and read by nothing, now fires — including within 14 days of stopping
one. A NaSSA's entry was completed from its label; one requested rule was
left out because the label contradicts it. 6/6, negative control 11/11.

## 2026-09-23 (evening) — RxGuard v1.8.3: an orexin antagonist hypnotic

A new hypnotic class was missing from RxGuard's curated base. It is there
now from its label: the CYP3A inhibitors and inducer it must be kept apart
from (RED, "avoid"), additive sedation with the Z-drugs (AMBER) and through
the existing burden score, the hepatic limits as the label states them, and
narcolepsy — added as a condition — as a contraindication. The test names no
medicine at all, so it cannot carry his list into this repository. 6/6,
negative control 7/7, 11 server suites green.

## 2026-09-23 (later) — GutLog v3.32.0: the food test

The FODMAP reintroduction now runs from the Now tab: one tap for the day's
step, one short evening score. The plan is data, seeded from his gitignored
file; steps advance when he logs them, never by the calendar, so a missed or
skipped day leaves the plan where it was. The stop rule is offered, never
imposed; the outcome per group is his to set. `/foodtest` shows each group's
doses with the score for every challenge day plus the first washout day, and
flags a medicine change recorded inside a test week or a gap in a ladder —
stated, not interpreted. 16/16, negative control 31/31, including a
mutation that hides that flag.

## 2026-09-23 — GutLog v3.31.0: foods by weight, and every time editable

He weighs food and wants to log what he actually ate. Every food now has a
weight and values per 100 g — per 100 g **dry** for dal, rice, poha and oats,
so the dry weight on the scale is exact. A meal row takes grams beside the
old portion counter, and the server scales from the food's basis. Values can
come from a bundled USDA table (SR Legacy, public domain, 7,791 foods; IFCT
2017 has no licence-clean copy), and every food says where its numbers came
from — the table, his own, or estimated. No live API call.

Every meal and dose shows its time as a button; tapping it opens the same
hour/minute lists as every other time box, on any day, and every change is
recorded in `edits`. The Now meal card gained a time field, default now.

The migration touched the library only: 162 foods, 17 with an exact weight,
76 estimated, 69 with none; no per-portion value changed and the day totals
for every logged day are identical. It also fixed a 316 px Meds-tab row on
the folded screen, measured on the live v3.30.0. 19/19, negative control
33/33 — which caught one assertion that could not fail as first written.

## 2026-09-22 (later) — the mirror is live, and carries the reports too

Signed in, both halves uploading, and the snapshot now carries **the reports
themselves**: the 50 report PDFs already on the VPS, copied under
`YYYY-MM-DD Kind - Title.pdf` rather than the storage hash nobody can read,
indexed in `documents.csv`, and left alone on the next run when nothing has
changed — 87 MB should not be rewritten every night for a nightly job.

First server upload 13:39 IST: 65 objects, 86.2 MiB. GutLog's warning went
from "has never finished" to "Mirror updated 22 Sep, 13:39" the moment it
landed, which is the whole mechanism working end to end.

**A dated warning worth keeping.** rclone says its shared Google client_id
is being retired and will stop working during 2026. When it does, uploads
start failing — and because success is marked only on a real upload, the Now
tab will say so within 36 hours instead of letting Claude answer from a
frozen copy. That is the failure this design was built for, with a date on
it. The fix will be his own OAuth client_id, not a code change.

Three mistakes of mine, all caught by something rather than by luck. The
PC script died on rclone's routine notice, because in PowerShell 5.1 `2>&1`
on a native executable wraps stderr in an error record and reports a failure
that did not happen — a trap written in this repo's own environment notes,
which I then walked into. rclone now writes its own log and only the exit
code is consulted. The clinical-terms assertion failed on the server, where
there is no repository and so no repository property to guard; it now tells
"not applicable" apart from "did not run", and still fails hard in a
checkout with the list missing — proved by hiding the list and watching it
go red. And the first version of that distinction looked for a directory
called `tools/`, which the server has for other reasons; it now looks for
the gate itself.

## 2026-09-22 — the Health Mirror, and GutLog v3.30.0 to say when it stops

He talks to Claude from his phone, where it cannot reach GutLog, FitLog,
RxGuard or his PC. So it answered from whatever it had — a sleep summary
drawn from his two best nights, a medicine list that was out of date. It can
read his Drive. So the record goes there, kept current, and the answers come
from the record.

**One private folder, never shared and never given a link.** No new endpoint,
no new login, nothing public. `ops/health_mirror.py` writes a readable
snapshot — medicines now and every start, stop and dose change in 90 days;
thirty nights of sleep; thirty days of vitals, gut and meals; every lab
result on record; the plans — plus the same data as CSV and a copy of each
plan document. Nightly at 02:30, and a Windows task for the reports folder.
rclone with scope `drive.file`, so it can only ever touch the files it made
itself.

**Two rules it holds to, because they are the ones that make a record
trustworthy.** Where a medicine has no recorded stop date it says *no stop
date recorded* rather than inventing one. Where a day has no meals it says
*not logged* rather than zero. Both are checked by a mutation that makes the
tool guess instead, and both would otherwise read as fact.

**And it never scores a night.** The sleep table puts the watch hours, his
own check-in answer and what was taken that evening side by side, so nights
with and without a given medicine can be told apart — with no grade, rating
or good/poor label anywhere. That is a standing rule here and there is now a
mutation that tries to break it.

**GutLog v3.30.0 exists because of how this fails.** Not loudly — quietly.
Claude would go on answering from a week-old copy with nothing to suggest
otherwise; a missing file produces a question, a stale one produces an
answer. So the mirror marks success only after an upload that actually
worked, and the Now tab carries one line when that is over 36 hours old or
has never happened. Right now it correctly says the mirror has never
finished, because his one Google sign-in is still to do — the only manual
step in any of this.

The tracked tool names no medicine. The night window is a clock rule rather
than a list of drug names, precisely so it does not have to. A negative
control makes sure of it, and a second one caught a weaker assertion first:
the upload check could only ever reach whichever branch the machine it ran
on happened to fall into, so on a PC with no rclone the "not configured"
path was never tested at all and passed for the wrong reason.

## 2026-09-22 — GutLog v3.29.0, the nutrition history he could not find

He said he had logged food for two or three days and could not navigate to
where the previous days were. The brief asked to establish the facts before
building anything, and the facts were not "missing feature".

**The control was there and did nothing.** The Meals tab had a date box
beside the day totals — and that box had no change listener anywhere in the
file. It was never a day chooser; it was only the date a *new* meal would be
filed under. The totals themselves were summed in the browser from a
server call that already accepted any day, and the meal list with Edit,
Again and Delete lived on the Now tab hardcoded to today. So a past day
could be computed but never seen, and the one thing that looked like it
should show you one silently refreshed nothing. He was not failing to find
the feature. He was using the thing that looked like it.

**`/nutrition`** is the history: newest first, fourteen days with a link for
thirty, and per day the kcal, the protein against his plan's target, the
fibre against 30 g and the meal count. A day with nothing logged says **not
logged** rather than 0 kcal — those are different facts and only one of them
is true. A day with fewer meals than usual is marked **partial**, so a small
total is not read as a small day. Tapping a row opens that day on the Meals
tab, which now has a stepper: previous and next either side of the date,
next disabled on today, and the date itself opening three lists rather than
the phone's own picker. The card, the meal list and every meal's actions
follow the chosen day.

**The totals moved to the server**, to one function that the card, the
history and both APIs all read. The rule was that the two can never
disagree; the way to keep that is to leave only one calculation in
existence, not two that are checked against each other. Fourteen days
compared on his real data afterwards: no mismatches.

His actual history, once it could be seen: four days logged, not two or
three — and **18 Sep is 110 kcal over two meals**, which is exactly the day
the "partial" label exists for.

Two faults the suite caught rather than a reviewer. The first version
defined every stepper function and **bound none of them**, so the arrows
drew and did nothing — the same fault as the date box the release exists to
fix. Then a bold date in the log form pushed the folded screen to 316 px,
because a `1fr 1fr` grid will not shrink below its content and the time
picker underneath has a hard minimum; the form's date cell is gone entirely
now, since the stepper above already states the day.

Negative control 13/13 seen to fail, seven by mutation — an unlogged day
printed as zero, a thin day left unlabelled, the history working the totals
out a second way, the card rounding its own way, the card stuck on today,
past meals losing their actions. 24 suites green on the server before the
restart. `/healthz` reads `ok 3.29.0`.

A second plan document was loaded through the same seed path as the first.

## 2026-09-22 — GutLog v3.28.0, a Plans page that holds and shares a document

A dated plan document now lives in the app at `/plans`, so it opens on the
phone and goes straight into the system share sheet. First version holds and
shares PDFs; the tables are shaped for the tracking this is the start of.

**Share hands over the bytes, not a link.** `navigator.share` with the file
itself, and where that is unsupported the fallback is a plain download of the
same bytes. There is deliberately no public URL and no unauthenticated route
to a plan document, so there is nothing to leak and nothing to revoke. The
file is served inline behind the login with `no-store`, stored under a
**sha256 name** in a directory beside the live database, and accepted only if
it really begins `%PDF-` — a non-PDF named `.pdf` is refused, and refused
before the row is written, so nothing is left behind. Archiving hides a plan
and keeps the file; there is no delete.

**Something worth flagging before the feature itself.** The plan PDF arrived
in the working folder **neither tracked nor ignored**, so `git add -A` in the
publish would have staged it — and its *filename alone* named a medicine.
NO_SECRETS refused it, measured rather than assumed. But note which check
did: **D**, the tracked-binary rule. The two clinical checks both reported
clean, because they read text and a PDF is bytes, and neither has ever looked
at a filename. `*.pdf` is now gitignored, which is the remedy; D stays the
backstop.

Three departures from the brief, each recorded in the dossier with its
reason: the tables go in SCHEMA rather than `_migrate()` (which returns early
whenever the schema version is current); the date field is three lists rather
than a native picker, because the existing observer converts time boxes only
and widening it would touch every date box in the app; and the list is
rendered on the server rather than by the browser.

That last one was not a judgement call — the suite made it. The first draft
built the list in JavaScript, and fetching the page returned a shell with no
title and no buttons in it. Same shape as the v3.4.0 lesson: a list only the
browser can draw is a list no server test can see, and a page that shows
nothing if the script fails.

The header then had no room for a third link: adding **Plans** pushed the
page to 327 px against a 300 px screen and the folded-width assertion failed,
which is what it is there for. Fixing it took two goes, because there are two
near-identical header blocks in the file and the first belongs to the Account
page. The tell was that the suite reported *the same* 327 px after the first
fix — an unchanged number is not a stubborn bug, it is the wrong file.

Negative control 9/9 seen to fail, with five of them broken on purpose in the
current build rather than left to a version control: when the whole page is
new, "it fails without the patch" says nothing about whether the assertion
measures what it claims. 23 suites green on the server before the restart.
`/healthz` reads `ok 3.28.0`.

The backup carries the documents without a new rule, since they sit inside
the folder already being tarred — but "already carried" is what was believed
about the database that turned out not to be backed up at all, so the script
now counts them in the finished tarball, says `plans -> N of N`, and fails if
any are missing. Checked by extracting one: its sha256 still equals its own
filename.

## 2026-09-20 — FitLog v1.7.1, the health check stops lying

`/health` had reported **version 1.3.1 through four releases** — v1.4.0,
v1.5.0, v1.6.0 and v1.7.0 all shipped without touching the literal sitting
in the route. The deployment runbook reads that endpoint to confirm a
deploy, so it would have confirmed the wrong build without hesitating: the
one check whose entire job is to say what is running was the thing
misreporting it.

The version now lives in one constant, with a comment in the file saying
every release bumps it, and the route reads the constant. There is no
version literal left in the route or anywhere else in the file — that is
its own assertion, because a second copy is how the first one went stale.
A fourth assertion holds the body to app, version and ok: `/health` has no
login in front of it, so what it carries matters. That one is caught by a
mutation that makes the body report a count from the record, since the old
build did not leak either and a version control would prove nothing.

All three apps now answer with their real build — GutLog `ok 3.27.2`,
RxGuard `ok 1.8.2`, FitLog `1.7.1` — and CLAUDE.md now says the endpoints
are authoritative rather than warning readers not to trust one of them.
That is only true for as long as releases keep bumping the constant, and
the table says so.

Found sideways: while giving GutLog a `/healthz` it had never had, the
obvious next question was whether the other two were honest. One was not.

## 2026-09-20 — GutLog v3.27.1 and v3.27.2, and three tidy-ups

**v3.27.1 finishes the protein target.** v3.27.0 moved it to the plan's
figure on the server and left the page carrying the old constant in three
places — including the day protein bar, which filled at 57 and so read
*full* while the card above it said "of 100". The page now keeps one value,
taken from the server the moment the rings load, and the patcher refuses to
write if either old spelling survives anywhere in the file. Three of the
four assertions render the basket line and the bar in a browser, which is
precisely what the v3.27.0 suite did not do. Verified on his own data
afterwards: the target reads 100 against 94.7 g logged, and no hardcoded 57
survives the rendered page.

The v3.27.0 entry below is kept as written, warning and all. It is the
worked example of the rule: the release whose whole subject was that
constant shipped with two live copies of it, and the assertion that should
have caught them — "one protein target: the diet plan's" — is the one that
reassured us, because it read the server helper and never rendered a page.

**v3.27.2 gives GutLog a health endpoint.** It had none, and no version
constant either, while three briefs in a row told a session to read
`/healthz` on it — and a 404 there is indistinguishable from a broken
deploy. Now `ok 3.27.2`, plain text, nine bytes, no login and no database,
answering even before the app has a password set. Because it is the one
route with nothing in front of it, an assertion checks that the body
carries no record data and no host detail; a version control cannot show
that failing (a route that does not exist cannot leak), so it is caught by
a mutation that makes the body report a figure. Shipped as its own
two-anchor patcher rather than folded into v3.27.1, whose anchors are all
inside the page JavaScript.

Three tidy-ups on the box, all **moved, not deleted**. `test_migration_v330`
is retired: it asserts a pristine post-migration state against a schema two
dozen builds past it, has been failing 9/12 by design for a long time, and
printed "Do not restart the service" on every run — a warning that is
always on teaches you to ignore warnings. Five suites that were never
GutLog's left `/root/gutlog` too; run from there they fail on missing
modules and tables and read exactly like a regression, which cost a session
an hour earlier the same day. Each was confirmed to have an identical or
newer copy in its real home and to still pass there. Two of them *differed*
from the home copy, which is why none of this was an `rm`.

## 2026-09-20 — GutLog v3.27.0, one protein target and a time picker that fits

Three things he reported, deployed 21:24 IST.

**The lunch protein figure was right; the target beside it was not.** Lunch
really was 25.9 g. What was wrong is that the Meals card measured against a
constant that predated the diet plan — "62 of 57 g" beside a plan card
reading "62 / 100 g". The target now comes from the plan, with the old
constant kept only as the fallback when there is no plan.

**Not finished, and said so rather than quietly:** two hardcoded copies of
the old constant remain in the page JavaScript — the meal basket's "day
protein would reach n/57 g", and the day protein bar, which fills at 57 and
therefore reads full while the card above it says "of 100". Both predate
this release, byte-identical in the rollback copy, so v3.27.0 did not cause
them; it also did not fix them, and the Meals tab is exactly where he
reported the problem. The new suite's first assertion is called "one protein
target: the diet plan's" and it passed, because it reads the server helper
and never renders the basket or the bar — rule 2 again, in the release whose
whole subject is that constant.

**An SOS dose logged late can be re-timed.** Those rows had only Undo.
Tapping one now offers 15 min / 30 min / 1 h / 2 h ago, or any time, saved
through the existing retime path so the edit is audited like any other.

**The phone's own time dialog is gone from every page.** On the folded
screen its Set button sits off the cover display — our own Save button was
always visible at 300 px, so this was Chrome's dialog, not our markup. Every
time input is now two lists, hour and minute, with the real input hidden and
kept in step, so nothing that reads a form changed. The UI suite was updated
in the same release to pick in the lists, since a hidden input cannot be
filled.

## 2026-09-20 — FitLog v1.7.0, the rings get their goals back

The activity rings had been drawing as grey outlines reading "no goal on
file". Nothing was wrong with the ring code: **the goals had stopped
arriving.** They only ever came in one array of the old iOS Health Webhook
payload, and Health Auto Export — the feed since 11 Sep — has no ring or
activity-summary type at all. The last body carrying a goal landed at
**11 Sep 17:31 IST**, and only three ever did.

So the fix is not to chase a goal no feed sends. **A goal is a Watch
setting, not a daily measurement**, and it holds until the Watch sends a
new one. The newest goal on or before the day now fills the ring, and the
page **always names the date it was sent** — "goals as last sent by the
Watch on 11 Sep" — rather than passing an old goal off as today's. Where
no goal was ever sent, nothing is invented and the ring stays an outline;
that property is guarded by a mutation that invents a default on purpose.

A second fault surfaced in the same place: the /watch Move ring read only
the Watch's separate Move figure, which has **zero rows in the live
database and never had any**. The Today strip had already fallen back to
active energy and /watch had not — so the same day showed a filled Move
ring on one page and an empty one on the other. Move *is* active energy.

Goals stay context-only; no rule reads one, guarded by a sidecar mutation
that makes a goal rule-bearing and requires the suite to catch it.
Negative control 7/7 seen to fail, full server sweep green, and the live
read-back shows Move 226/300 (75%) and Stand 10/12 with the 11 Sep goal
line on both pages.

## 2026-09-20 (later) — the three loose ends closed

**The access log stopped recording the token.** The key stays in the URL —
that feed cannot send headers, and CLAUDE.md §5a accepts it under narrow
scoping. What was wrong is that the web server wrote the URL down: 392 lines
since 11 Sep. `fit.dr-manoj.in`'s `logFormat` now logs `"%m %U %H"` instead
of `"%r"`, so the path is recorded without the query string. `%U` alone was
the instruction; method and protocol were kept with it, because every ingest
diagnosis starts by asking which requests were POSTs and what they returned,
and `%U` on its own would have cost that for nothing. Graceful restart,
`SIGUSR1`, zero downtime; **only that one vhost changed** and all eight on
the box answered afterwards.

The 392 lines already on disk were then masked to `k=***` (13:17 IST). The
rewrite went through the **same inode** — litespeed holds that fd in append
mode, so `sed -i` would have sent every subsequent line to a deleted file.
392 masked, 0 values left, 944 lines before and after, byte-identical to the
same substitution replayed over the pre-change copy, and a probe request
afterwards appended normally. The interim copy was removed only once those
checks passed.

**The exposure is closed and accepted — the key was not rotated**, by the
owner's decision: the log was unreadable by any unprivileged or remote
account, and that token is send-only, POST-only and `healthconnect`-only with
no read access, so nothing that could have read it could have used it for
anything. Recorded in the DOSSIER with the measured modes, because "root-only"
is the right conclusion but not literally the permission — the logs directory
is 750 `root:nobody`, so CyberPanel's `lsadm`/`lscpd` and the litespeed worker
could read it too. The gunicorn log never carried it at all: 0 unredacted
values across the live file and all eight rotated copies.

**`backup.sh`: the obvious one-character fix was wrong.** Widening
`--exclude='*.db'` to `'*.db*'` does drop the twenty timestamped copies of the
diary, and it also drops the key file — and tar has no way to put an excluded
file back. Naming it explicitly on the command line does **not** work; that
was measured on the box rather than assumed. What shipped is
`--exclude='*.db' --exclude='*.db-*' --exclude='*.db.[!s]*'`: the `[!s]` class
drops every `.db.<tag>` snapshot while keeping `.db.secret*`. Run by hand:
239 → 220 members, both key files present, no database copies.

The honest footnote: the tarball went 91.4 MB → 90.5 MB. Only 857 KB came
off, because twenty sqlite copies of a 400 KB database gzip to almost
nothing. `uploads/` is 90.2 MB of what is left — the scanned PDFs, which are
the point of the backup. The snapshots were never a size problem; they were
copies of the diary in a place that was not supposed to hold them.

**The stale app copy is archived.** `/root/gutlog_backup_v2/` — the thing the
last entry flagged as "the most likely thing to mislead a future session" — is
now in `/root/archive/gutlog-dead-20260920/`, moved and not deleted, 1,839
entries and 17,617,609 bytes identical before and after. Proved dead four
ways; the single textual reference to it anywhere under `/root` turned out to
be a clinic-repo retirement helper listing it as **never-touch**, which is a
mention, not a caller.

## 2026-09-20 — server clean-ups, and the Watch was never silent

**The Watch question, answered by looking rather than assuming.** The premise
was that the Apple Watch had sent nothing since 13 Sep. It is **not what the
data says**. Every POST to the ingest endpoint since 11 Sep returned **200** —
nothing refused, nothing unsaved — and Apple Watch bodies arrived on **every
single day**, including today. What actually happened is a **decline, not a
stop**: 15 bodies on 14 Sep, then 9, 3, 2, 1, 1, 1. The phone is still sending;
it is sending roughly once a day instead of through the day. So it is the
phone's side, but "it stopped" would have sent him looking for the wrong
thing — and a server-side fix would have been a fix to nothing.

Two things found while looking, neither asked for:

- **The Health Connect token is written in cleartext into the web-server
  access log on every request**, because that feed carries its key in the URL
  (it cannot send headers — CLAUDE.md §5a accepts this, under narrow scoping).
  The scoping still holds; what was not previously written down is that the log
  is a second place the token lives, with a different retention and different
  readers.
- **The nightly files tarball carries about twenty copies of the diary.**
  `backup.sh` tars the app folder with `--exclude='*.db'`, which misses the
  timestamped snapshots — `…db.bak-v340-20260910_105612` does not *end* in
  `.db`. The tarball is 91 MB and holds 22 matching entries. Not changed here:
  it is a working backup script and the call is his. One character fixes it.

**Two dead databases archived, not deleted**, after proving them dead three
ways — no worker held them open, no `GUTLOG_DB` pointed at them, no cron or
script named them. Each was copied with `sqlite3.backup()` and
integrity-checked before moving. The name grep threw three matches and each was
read: two were **comments citing this very lesson**, the third an old copy of
the app that nothing runs.

**Both units now launch gunicorn the portable way** (CLAUDE.md §3 — RxGuard was
already there; GutLog's venv path is gap 6, now closed). Only the interpreter
changed. Checked first that every module resolves under the system Python and
that `--check-config` exits 0; the app answered one second after the restart.

**`tidy_extras.py` takes its own backup** and writes nothing if that backup
fails — closing a gap that had stood since it was written, and the important
half is the second clause: a backup step that can fail quietly is not one.

**RxGuard v1.8.2** — with no `RXGUARD_SECRET`, each worker minted its own
session key, so a request landing on the other worker looked logged out and
every restart logged him out. It now falls back to a key file created **once**
through an atomic link. On this server the variable **is** set, so nothing
changed for him and no file was written; the fix is for the case where it is
ever removed. `test_secret_file` 5/5 on the server, 6 declared / 6 seen —
including a mutation of the **non-atomic create**, the failure that only shows
under a real race.

**New dose limits file** (label maxima, two sources now primary-checked, one
window widened to 24 h). `/dose` still reports **0 findings** on his log.

## 2026-09-20 — RxGuard v1.8.1: a ceiling that cries wolf stops being read

**His words, the day after v1.8.0 shipped.** The confirm-each-ceiling page
taxes him: the maximum daily doses are in verified sources, so fetch them. And
one ingredient at twice its 60 mg tablet in a day is an **accepted dose** —
don't flag it.

Both criticisms were right, and they are the same criticism twice.

**Confirming was a tax, not a safeguard.** Asking a doctor to confirm a maximum
daily dose printed in the label is asking him to retype a fact — and worse, an
unconfirmed ceiling reads as *provisional*, so the one number the page exists to
be trusted on arrived hedged. Every ceiling is now the **label maximum carrying
its source text**; the "default" chip and the Confirm button are gone. His own
limit still wins, behind a collapsed *Change*, and shows as *your limit*.

**Clearing that box now returns to the label maximum**, where before it
returned to "no ceiling" — the old behaviour turned a correction into a silent
removal of the guard, which is the worst kind of change: it looks like tidying.

**And the flag itself was wrong.** A ceiling set below what the label allows
does not make him safer, it makes the page cry wolf — and a page that cries
wolf stops being read, which costs more than the false alarm did. That
ingredient now sits at its licensed maximum with a **course limit** instead:
above the lower-indication dose for more than a short run of days is what
actually matters there. New rule **DC010**.

**Two ceilings say they are not yet primary-checked**, in their own source
text. A sourced number that names its own weakness is honest; an unsourced one
dressed like the rest is not.

The dose feed widens from 4 days to 10, because a course limit is invisible in
a 4-day window — mutation-controlled by shortening the feed until no run can be
seen and requiring the new assertion to catch it. DC010's run comparison lives
in the sidecar engine, which the negative-control harness still cannot mutate,
so it was broken by hand and **case 15 alone caught it**, engine restored
hash-checked. That harness gap is now two releases old and is worth closing.

Gates: `test_dose_ceiling` **15/15**, negative control 3 declared / 3 seen;
`astaken_honest` 16/16, `astaken` 15/15, `reconcile` 18/18, `conditions`
10/10, `kb` 32/32, `smoke` 49/49, `validate` 50/50, `ops/test_sso` 11/11.
`--reverse` byte-identical to v1.8.0.

**On his real corrected log the page now raises nothing at all**, where v1.8.0
raised one amber — and driving the engine at the moment that amber used to fire
shows the ingredient **at its ceiling, not over**, which is precisely the
distinction he asked for. Detail in `rxguard/README.md`.

## 2026-09-20 — GutLog v3.26.0: a food test is a question about weeks

The old food test recorded **one day**: ate it, felt this. That is the wrong
unit. A food eaten once on a good day proves nothing, and the thing he
actually wants to know — *does this food suit me* — is a question about weeks.
His own note on the last test said exactly that: *will track for a month*. The
app had nowhere to put that intention, so it lived in his head.

A **trial** is now a period — food, amount, how often, start, planned length —
and meals whose item name contains the match text are **linked automatically**,
so running one costs nothing beyond logging meals he was logging anyway.

**What it throws away is what makes it readable.** Trial days are compared
against the 14 days before, and two kinds of day are set aside **on both
sides**: days carrying a Day context mark (v3.23.0 — travel, poor sleep,
unwell…), because a bad day with an obvious other cause is not evidence about
a food; and **days with nothing logged at all**. That second one is the single
most flattering mistake this feature could have made — counting an empty day
as symptom-free would turn every gap in the diary into evidence that the food
is fine. Mutation-controlled, as is dropping the day *after* eating, since a
reaction that shows up next morning is still a reaction.

**It refuses to conclude early.** The words — no signal / possibly better /
possibly worse / likely worse — appear only with at least **eight counted days
on each side**. Below that it says "not enough days yet" and nothing more. A
verdict from three days is worse than no verdict, because he would act on it.
On the live data this is the branch running: the migrated trial sits at day 4
with 4 counted days against a baseline of 8, and says so.

His verdict writes through to the rest of the app — the library status of every
matching food, and a matching recipe's stage. Starting a trial of a recipe
marks it **On trial**, so the recipe book and the trial cannot disagree about
what is being tested.

**The old record is not rewritten.** `foodtests` rows are untouched and the
one-day test stays below; the migration's note quotes the three original rows
and his own words, so the old entry explains the new one instead of vanishing
into it.

One table via SCHEMA, no migration step, no `schema_version` bump. Gates:
`test_trials` **9/9** with **13 declared / 13 seen to fail** on invented foods,
the four mutations all being the flattering ones; every GutLog suite green on
the server plus `ops/test_sso` 11/11; `test_ui_now` 196 PASS / 0 FAIL. Detail
in `gutlog/DOSSIER_GutLog.md`.

## 2026-09-20 — GutLog v3.25.0: a plan you can only read in the evening is not a plan

The diet plan existed as a document. A document cannot tell him at four in the
afternoon whether he has had enough protein today, or which rotation rule he
is about to break — and by the time it is read in the evening, the meal that
would have fixed it has been eaten.

**"Today against the plan"** is a card on the Now tab, computed **entirely
from the meals already logged**. It only became possible because of the last
three releases: v3.22.0 made logging a meal one tap, v3.24.0 put the recipes
where the plant and calcium values live. This one reads that record back.

Today's protein, calcium, fibre and energy against targets — and **protein per
main meal**, because three meals reaching a daily total between them is a
different thing from one large meal doing all the work. This week's plant
points, with **a spice counting a quarter**: a pinch of something is not the
same plant event as a bowl of it, and counting it whole would make the score
meaningless inside a week. The rotation rules each report a standing.

**Foods that lack a calcium value are named rather than treated as zero.** A
total quietly computed over a missing figure reads as a low day instead of an
incomplete one, and he would go and correct the wrong thing.

**It stays quiet in a thin week.** With fewer than three logged days, "short"
rules still show their standing if he opens This week, but they make no
suggestion. Three days is not evidence of falling behind — it is evidence of
not having logged — and a card that scolds him for a gap it cannot see would
be both wrong and self-defeating. On the live data this is the branch actually
running: two logged days this week, four rules standing at "short", none of
them in the tips. Checked against the meals table, not inferred from the suite.

**It blocks nothing and writes nothing.** `/api/plan?day=` is a GET; no schema
change, no table, no stored verdict, so nothing can go stale and no judgement
outlives the meals it was read from. The targets, rules and food map are his
diet, so they sit in a gitignored file beside `app.py` — **no plan file, no
card**, asserted first.

Gates: `test_plan` **9/9** with **12 declared / 12 seen to fail**, on invented
foods and a fixed past week so the result cannot drift with the real diary;
the three mutations are the quiet ones — counting a spice whole, ignoring
quantity when totalling calcium, mistiming a "have it today". Every GutLog
suite green on the server plus `ops/test_sso` 11/11; `test_ui_now` 196 PASS /
0 FAIL. Detail in `gutlog/DOSSIER_GutLog.md`.

## 2026-09-20 — GutLog v3.24.0: the cooking, not just the calories

v3.22.0 put his recipe collection into the food library so the meal cards
could reach it — 52 dishes, but only as **names with numbers attached**. What
actually goes in the pot, how it is made, and which version leaves the onion
out were still in a file on a laptop: nowhere useful at the moment anyone is
deciding what to cook.

**Recipes** is a third segment on the Meals tab. Search, filter by group and
stage; a card carries the per-serving figures (**estimated, and labelled so**),
plant points, a high-FODMAP flag, the whole-pot ingredients with the
high-FODMAP ones highlighted, the method, the notes — and where one exists,
the **onion-free version beside the original** rather than replacing it,
because which he wants depends on the day.

**The stage is the only part that is his.** Five chips — Not tried, On trial,
In rotation, Paused, Avoid — and that one field is judgement rather than
imported content; everything else can be re-imported at will. So the seeder
**refreshes a card's content and leaves the stage exactly as it found it**,
asserted directly and mutation-controlled. A re-seed that silently reset
"Avoid" to "Not tried" would look like a successful import while losing the
one thing that took real experience to learn — which is this project's
recurring failure shape again: the change that appears to succeed.

**Log it** (½, 1, 1½, 2 servings) writes an **ordinary meal row through the
same helper the meal cards use**, so totals, the Meals tab and the review
export carry recipe meals without knowing they came from a recipe. The Meals
Save bar is **hidden** on this segment: it belongs to the meal editor, does
nothing here, and a Save button sitting over a recipe book invites a tap that
cannot do what it looks like it does. Also mutation-controlled, because
leaving it there passes every functional test.

One table via SCHEMA, no migration, no `schema_version` bump, `library` and
`meals` untouched. The seed dry run printed *52 cards, 52 new, 0 refreshed;
library items to add: **0*** — that zero being the evidence that v3.22.0 had
already put every dish in the library, so this release adds the cooking and
not a second copy of the food. Gates: `test_recipes` **7/7** with **9 declared
/ 9 seen to fail**, on invented cards and running the **real seeder** rather
than a stand-in; every GutLog suite green on the server plus `ops/test_sso`
11/11; `test_ui_now` 196 PASS / 0 FAIL. Verified live with GETs only — no meal
logged, no stage changed. Detail in `gutlog/DOSSIER_GutLog.md`.

## 2026-09-20 — GutLog v3.23.0: the day around the trial

**A food trial cannot be read honestly without knowing what else the day
held.** A bad gut day after a new food means one thing on an ordinary day and
quite another after a long drive, a broken night, or a fever. None of that was
being recorded — and the point that makes it urgent rather than tidy is that
**it cannot be recovered afterwards**. You cannot reconstruct last Tuesday's
travel from the diary once Tuesday has gone, so every challenge result up to
now carries an unknown that is permanently unrecoverable.

**Day context** is a card on the Now tab above Down day: Today / Yesterday and
six toggle chips — Heavy exertion, Poor sleep, Travel, Unwell, Stress, Ate out.
One tap marks, a second clears. Yesterday is there because the marking usually
only occurs to him the next morning, and a context he cannot backfill by a day
is a context he will stop using.

**Why it is not folded into Down days.** Down days mark *how he was*; day
context marks *what the day did to him*. A long drive he coped with fine is
travel and not a down day. Merging them would have made both unreadable, so
Down day is untouched.

The primary key is day+tag, so a repeated tap is harmless rather than a
duplicate; only the six keys are accepted, and an unknown tag or a future day
is refused — mutation-controlled, along with Yesterday quietly pointing at
today, which is the version of this bug that would still look right on screen.
A read-only feed endpoint on the existing token lets a trial reading, or
FitLog, ask what a day held without reaching into the database.

Two tables via SCHEMA, no migration, no `schema_version` bump, nothing existing
changed. Gates: `test_day_context` **7/7** with **9 declared / 9 seen to fail**,
case 7 driving real Chromium; every GutLog suite green on the server plus
`ops/test_sso` 11/11; `test_ui_now` 196 PASS / 0 FAIL. Verified live with GETs
only — **nothing was marked on his record**. Detail in
`gutlog/DOSSIER_GutLog.md`.

## 2026-09-20 — GutLog v3.22.0: most meals are the same meal

**The problem was never forgetfulness.** Logging a meal took **ten to fifteen
taps and typed numbers**, so at breakfast it lost to everything else, and
meals simply were not logged. A food diary nobody fills in is not a food
diary — and the work downstream of it (the FODMAP challenges, the protein
target) was quietly starving.

**The fix is not a better form.** It is the observation that most meals are
the same meal. A card on the Now tab opens already set to what he had last
time, and the button reads *"Log lunch — same as last time"*: **one tap**,
with the time taken at the moment of logging rather than typed. A different
choice is one chip; counts step in **halves**, because half a slice is a real
portion and "1 or 2" is not a measurement; an onion switch on lunch and dinner
covers the thing most likely to differ day to day.

**His question about the mockup was the right one:** what happens with pizza,
or something new? "+ Something else" on every card and an "Other meal" tab:
search his own foods with **recent first**, or type a dish never seen before —
name, size, kind — and it is added with values estimated from a typical dish
of that kind, **tagged `estimated` and carrying a note that its FODMAP value
is unknown**. Guessing a number is acceptable here *only because the guess is
labelled as one*; an unlabelled estimate in a food diary is worse than a gap.
The same name typed again reuses the item instead of making a second.

**Two refusals worth naming.** A food the library lacks is **named, not
silently dropped**, and the rest of the meal still logs — a missing ingredient
must never cost the meal. And **Edit keeps the original day and time**: fixing
what was eaten must never rewrite *when*, which is mutation-controlled, since
stamping "now" on an edit is the plausible-looking version of that bug.

**What it deliberately does not touch.** New `meal_meta` table via SCHEMA, no
migration, no `schema_version` bump, and **`meals` rows keep exactly their old
shape** — so every existing total, the Meals tab and the review export are
untouched. The card is a faster way to write the same row, not a new kind of
record. The six cards live in a gitignored file beside `app.py` (what he eats
is the health record).

Gates: `test_meal_cards` **12/12** with **14 declared / 14 seen to fail**, on
invented foods and a scratch card file; case 12 drives real Chromium. Every
GutLog suite green on the server, plus `ops/test_sso.py` 11/11 — the meal
anchors do not overlap the SSO ones and that was checked, not assumed.
`test_ui_now` 196 PASS / 0 FAIL. Seed: library 91 → 160, and the dry run ended
on *every food the cards name is present*. Detail in
`gutlog/DOSSIER_GutLog.md`.

## 2026-09-20 — HEALTH_SSO_V1: one sign-in across all three apps

**His ask (17-Sep).** Moving from GutLog into RxGuard or FitLog makes him sign
in again; one sign-in should carry across all three.

**How.** A plain page load (GET, `Accept: text/html`) with no session goes
round the ring gutlog → rxguard → fitlog → gutlog via `/sso/vouch`. The first
app that already has a session mints a ticket — **HMAC-SHA256, bound to one
receiving app, 60 seconds, single use**, the nonce recorded in the receiving
app's own database — and the browser lands on that app's `/sso/in`, which signs
it in exactly as its own password would and continues to the page asked for.
Nobody signed in anywhere → that app's own login, in at most three redirects.

**The four refusals are the design.** It never bounces an API or XHR call, only
page loads — bouncing a background fetch turns one failed request into three.
It never follows a `next` that is not a plain path, and never sends the browser
anywhere but the three configured origins; an unknown target is a 404. It never
overrides Lock: logout sets a hold, so a locked app stays locked until its own
password, even while the other two are open. And with **no
`/root/health-sso.key`, every app behaves exactly as before** — the default is
off, the off switch is `rm`, and that promise was re-verified live on the
server with the key still absent, each app going to its own login and no ring,
before the key was created.

**A guessable session key, found by the gate rather than by the patch.** An SSO
ticket is only as good as the session it turns into, so the first step was a
blocking check that every app's own secret is set and ≥32 characters. GutLog
and RxGuard were fine at 64. **FitLog had none**: `app.secret_key` fell back to
a value derived from its *database path*, and the unit's environment file did
not exist. That had been true since FitLog was built, and it is exactly what
must not be allowed to become a vouch. A real secret is now in place at mode
600 — which signed FitLog out once, the whole cost.

**A stale test caught in passing.** On the server the cross-app analgesic
mirror suite was 6/8, and the two failures were cases 03 and 06 — the ones
whose old premise *was* the defect GutLog v3.21.0 fixed the day before. The
updated suite had been committed but never copied to the server; the failure
messages showed the new `same_dose` linking working correctly. Copied, 8/8.

Evidence: `ops/test_sso.py` **11/11** offline and on the server, running all
three real apps on 127.0.0.1/.2/.3 so their cookies stay apart the way three
subdomains do. Against the three reconstructed pre-SSO builds the same suite
splits exactly as it should — 02, 03, 04, 06, 07, 08, 11 fail and 01, 05, 09,
10 still pass, those four guarding behaviour that must not change — which is
the version control done by hand, since there is no manifest for this patch.
`--reverse` is byte-identical to the input for all three apps. Full regression
for all three apps **with a key present**, on the workstation and on the
server, all green. Detail in each app's own auth section.

## 2026-09-20 — RxGuard v1.8.0: the total, not the duplicate

**His ask (19-Sep).** Watch the **cumulative daily dose of each ingredient**,
not only duplicates: an ingredient inside a fixed-dose combination counts into
the same pool as the standalone product, one generic by two routes is one pool,
and classes carry a load of their own.

Until now RxGuard could say *these two products interact*, but not *you have
had too much of this today* — and the second question is the one that ordinary,
sensible-looking behaviour gets wrong. Nine rules, DC001–DC009, each finding
naming the rule that fired.

**The window is the interesting decision.** A flat 24 h is the obvious choice
and it is wrong: a nightly medicine taken at 22:00 one night and 21:30 the next
is **23.5 h apart**, inside a 24 h window, so a flat rule reports two doses in
one day every time he goes to bed early. The first build did exactly that. Once-
a-day ingredients get **20 h**, several-times-a-day get 24 h, class sedative
load 12 h. It is mutation-controlled — widening it back to a flat 24 h makes the
assertion fail.

**Two places the engine refuses to guess.** A double entry is *counted* and then
**named** (DC008): the higher total is the safer claim, and silently discarding
the second entry would make that reading unavailable. A concentration (mg/mL) is
not an amount per unit, so a syrup stays DC009 until the rules file gives an
amount — an invented number inside a dose total is worse than no number.

**GutLog is not touched by this release.** The engine reads GutLog's existing
dose and stack feeds and nothing else; findings join the as-taken list, so the
page, the dashboard count and GutLog's banner all carry them with no change on
the GutLog side. The ceilings name his medicines, so they are a server-only
gitignored file at mode 600; a missing file means the feature is simply off, and
says so.

**This release is why yesterday's GutLog correction had to come first.** The
same tablet had been recorded five times through the symptom screens, and a
ceiling engine reads that as three times the limit. On the corrected log the
engine raises exactly one thing, and it is real.

**A weakness worth naming.** All 13 declared assertions are *version* controls,
and v1.7.0 has no dose engine at all — so each fails there for the same trivial
reason. For the three that guard a **boundary** rather than a feature, the
boundary was additionally broken on purpose in the current engine, and each was
caught by exactly its own assertion and no other. Those three are not in the
manifest because `tools/NEGATIVE_CONTROL.py` can only mutate the *app* file and
these properties live in a sidecar module. Teaching the harness to mutate a
named module is the fix, and it is not done.

Gates: `test_dose_ceiling` 13/13 on both machines (invented molecules, real
GutLog on loopback), 13 declared / 13 seen; `test_astaken_honest` 16/16,
`test_astaken` 15/15, `test_reconcile` 18/18, `test_conditions` 10/10,
`test_kb` 32/32, `smoke_test` 49/49, `validate` 50/50. Patcher 8 anchors,
`--reverse` byte-identical to v1.7.0. Detail in `rxguard/README.md`.

## 2026-09-20 — GutLog v3.21.0: one tablet is one dose, however many symptoms

**His report.** *"On 18th September it only accepted multiple [entries] due to
the design flaw. I took it once only, but wherever I entered the symptoms, it
asked for the medicine I took for it. … The 19:18 and 19:21 entries were a
single entry … tapped two times. So it was one dose. Fix all this."*

**The defect.** Every pain tile and the down-day card ask what was taken, and
each answer wrote a **new** dose row. Right for the Now tab, where a tap means
a tablet; wrong everywhere else. Worse, the combination tablet he actually took
had **no chip of its own**, so it could only be entered as its two single
ingredients — doubling every entry again. One tablet became five rows.

**Why it survived so long: nothing looked broken.** Each row was individually
true, and a diary is supposed to fill up. The defect only becomes visible when
something *totals* the rows — which is exactly what RxGuard's new daily ceiling
does, reading one tablet as three times the limit. This is the recurring shape
on this project, seen from the other side: usually the change that appears to
succeed, here the data that appears to be fine.

**The fix.** A chip on a symptom surface is a statement about *what was used
for this symptom*, not a new event. If a dose of that medicine — or of any
product carrying **all** its ingredients — is already logged from 6 hours
before to 30 minutes after, the chip links to it and no row is written. Two
boundaries, both mutation-controlled because both fail plausibly: the window is
**6 hours, not the day** (or a genuine evening dose merges into the morning's),
and it is **all** the ingredients, not any (or a combination is swallowed by a
dose missing one of them).

**Nothing is silently dropped.** The page says *"counted with the dose at
HH:MM"* and offers **It was a new dose**. A rule that quietly discards an entry
is worse than the duplication it replaces, because the owner cannot see it
happen. The Now tab is deliberately untouched — a double-tap guard was built
for it, broke two suites, and was **removed on purpose** rather than have the
suites loosened around it.

**A test that had been asserting the bug.** `test_phase_i` case 06 asserted
that "a second chip a minute later writes another row" — the defect, written
down as a requirement and passing for weeks. Rewritten, not deleted.

**The 18-Sep correction** (owner-confirmed, script + gitignored spec): one
as-needed combination tablet had been recorded as five rows across three
symptom entries. The live rows were read out and checked against the spec
first, every find matching exactly one row; four rows deleted, one relabelled,
the mirror rows in the other app treated the same way. Both databases backed up
and every old row appended in full to an append-only corrections log. **The
following day's identical-looking pair was left exactly as recorded** — he does
not remember that day, and a correction made from inference rather than memory
is not a correction.

No schema change. Gates: `test_one_dose` 5/5 with **4 declared / 4 seen to
fail**; every server suite green including the two the authoring sandbox could
not run cleanly (phase_a 18/18, phase_m 19/19, confirmed on both machines);
`test_ui_now` 196 PASS / 0 FAIL. Patcher 11 anchors, `--reverse` byte-identical
to v3.20.0. Detail in `gutlog/DOSSIER_GutLog.md`.

## 2026-09-20 — GutLog v3.20.0: one list was the wrong shape for two problems

**What he said.** *"Populate the stock we have of all the medicines. Then two
pipelines: one for the regular consumed ones"* — and one medicine, *"whatever
strength, needs to be tracked, because it is required early in the morning and
running out will spoil the day. The other ones, used rarely, get separately
listed whenever the inventory is getting low."* (The medicine is named on the
server and in the gitignored brief, never here — CLAUDE.md §5d.)

**Two pipelines, because the failure modes are opposite.** A daily medicine and
a rescue medicine both run out, but not on the same clock. Daily ones ride the
monthly order (v3.19.0's top-up to 40 days, arithmetic untouched). SOS ones
leave it entirely and get their own **Running low** card, raised any day of the
month once stock falls below **a third** of the keep figure. Keeping them on
the monthly order buys four months of a rescue tablet every month; waiting for
month end means the rescue is already gone when it is wanted. The threshold is
a third rather than "below keep" so the list does not shout the day one tablet
comes out of a full box.

**The strength links — the gap v3.19.0 could only name.** From v3.6.0 a
medicine logged with a choice of strengths could not be counted at all: a
variant schedule has no single units-a-day figure. That was tolerable until he
named this exact medicine as the one that must not run out. Each strength label
is now linked to the pack it actually comes out of, with a unit count — and the
count is the part that matters: **290 is two capsules of the 145 pack, not a
290 pack**, because no 290 pack exists. A dose logged as `145 + 72` takes one
from each. The packs become ordinary per-dose stock with the existing 7-day and
3-day refill alerts, and ride the monthly order at `max(14-day use × 40, keep)`
— keep as a **floor**, so a rarely-used pack still never reaches zero. The
variant row itself stays untracked and says *where it is counted*.

**The labels were checked against the live schedule before anything was
written**, not assumed from the brief: `72|145|290`, exactly as the links file
expected. Had they differed, the links would have been silently wrong and the
medicine silently uncounted — the failure this project keeps finding, where the
change appears to succeed.

**The seed was rehearsed on a throwaway copy of the live database**, because
the dry run could not show the links resolving: the packs they link to do not
exist until the adds land, so the dry run prints "pack not in GutLog — skipped"
for all three whether the code works or not. A dry run that cannot distinguish
success from failure is not evidence. On the copy all three resolved and a
second run added nothing; only then was it run live.

**12 medicines added, 23 of 36 now counted**, each add checked against the live
catalogue first for the same product at the same strength. A duplicate entry
that differed only in case was merged first (nothing to move; the empty one
retired, never deleted). The medicines GutLog has that are *not* on his sheet
are deliberately left uncounted and say so.

One new table, no column changes, no `schema_version` bump. Gates: phase_o
**10/10** with **10 declared / 10 seen to fail** (three by mutation), phase_n
14/14, every other server suite green, `test_ui_now` 196 PASS / 0 FAIL,
`test_ui_order` ALL PASS. Patcher 17 anchors, `--reverse` byte-identical to
v3.19.0. Detail in `gutlog/DOSSIER_GutLog.md`.

## 2026-09-19 — GutLog v3.19.0: the order is a top-up, not a month's worth

**What he asked for.** Medication inventory and order generation, not a
spreadsheet. He orders in the **last week of each month for the month after**
and keeps a ten-day buffer. The spreadsheet he has been keeping cannot do the
only arithmetic that matters here, because it does not know what he has taken.

**The rule, and why the obvious version is wrong.** Target stock is 40 days
(a setting, 7–120). The order is a **top-up to that target, never a flat 40
days bought every month** — order the whole target monthly and the cupboard
fills with eleven months of one medicine while another runs out. And the target
is set against the stock **expected on the 1st**, not today's count: a week of
doses still comes out before the new month starts, so ordering against today's
figure under-orders by a week, every month, invisibly.

**The one that would never have been noticed.** A filled pillbox has left
stock but has not been swallowed. Without allowing for what the box still
holds, the order would jump the day he fills it and shrink again as he empties
it — an order that changes because of *where the tablets are sitting*. It is
asserted as an invariant (the expected figure identical before and after a
7-day fill) rather than as a number, and the negative control deletes the term
from the current build on purpose and requires the assertion to catch it.
Two of the three mutation controls in this release exist for the same reason:
these are the errors that would still have produced a plausible-looking order.

**Nothing is silently dropped.** A medicine that cannot be ordered — dose
variants, never counted, schedule ending before the 1st — is **named on the
card with its reason**. An order list whose omissions are invisible reads as
complete, and that is worse than no list.

**Seeded from the interim master sheet**, after a dry run: 11 medicines given
pack size, pack type and keep-on-hand, a stock count set only where GutLog had
none, and **12 rows left unmatched on purpose** — a different strength is a
different product and a combination product is not its single ingredient, so
anything not plainly the same product was left for him rather than guessed.
The seed files name medicines; they never entered the repository and were
deleted from the server after the run (CLAUDE.md §5d).

Two new tables, no column changes, no `schema_version` bump, no existing stock
figure touched. Gates: server suites 14/14 phase_n plus a, b, c, d, e, f, g, i,
j, m and watch_tiles all green; `test_ui_now` 196 PASS / 0 FAIL; `test_ui_order`
ALL PASS; negative control **14 declared, 14 seen to fail**. Detail in
`gutlog/DOSSIER_GutLog.md`.

## 2026-09-15 — the sleep record: FitLog was faithful, the export was not

**The report.** Apple Health held 5 h 41 m for the night of 14→15 Sep, roughly
22:30 to just past 05:00. FitLog showed 1.7 h. Read against `health_raw` on the
server, the parser had done nothing wrong: Health Auto Export delivered **one
sleep block covering 03:13:23 to 04:57:30 with `totalSleep 1.6685396374927626`**,
and `health_metrics` held `1.6685396374927626`. Bit for bit. The four hours were
never in a payload. Both suspects in the brief were ruled out by the data —
`totalSleep` was present and positive so the phase-sum branch was never reached,
and across all 212 stored bodies only ever one sleep point per date had arrived,
so nothing had overwritten anything.

**Why a code change was still the blocking fix.** Auto Export stamps every sleep
point at midnight of the day the night is filed under — the 09-14 point covers
23:08 on 09-13 to 03:19 on 09-14 and is still stamped `2026-09-14 00:00:00`. So
the moment a wider export window delivered a night in two blocks, both would have
landed on `hae|sleep_hours|day|<date>|00:00:00` and the second would have
silently replaced the first. **Widening the export alone would have left the
number wrong and looked like it had worked.** That is the class of failure this
project keeps finding: the change that appears to succeed.

**Rule S03 Sleep Block Identity** (deployed 15:43 IST). A sleep sample is keyed
by its own `sleepStart`. Blocks that overlap are competing descriptions of one
stretch and the longest span wins, never the sum — adding them would invent
sleep he did not have. Blocks that do not overlap add. `asleep` and `inBed`
arrive as 0 on every Watch night here, so a total is believed only when positive
and time in bed is computed from the stamps. Awake is never sleep. Every stamp is
converted to IST before storage.

**Rule S04 Overnight Basis** (built, not yet deployed). A figure said to be
measured over the night is the mean of the samples inside the sleep span, with
`n`, `min` and `max`; one that is not carries `basis: "day"` and says so on the
page. A metric never sent is **absent, not zero**. Time in bed sums the
stretches rather than spanning a break.

**Wrist temperature.** He has had subjective feverishness for over two years
with, until 14-Sep, no documented temperature, and the Watch has been measuring
it nightly and discarding it because nothing claimed the metric name. It is now
mapped under every spelling Auto Export is known to use — but the census shows
**it has never been exported**, so it must be switched on in the app. Mapping it
costs nothing and means it lands the moment it is.

**The constraint the whole thing is built under: never score a night.** No
readiness figure, no recovery percentage, no "poor night", no streak, no target.
He has post-discontinuation insomnia, and a number graded every morning becomes
its own cause. Two assertions enforce it rather than trusting anyone to
remember: one fails the build if a verdict-shaped key appears on `sleep_night()`,
and one scans everything the Sleep card displays for scoring language. The
second's negative control puts a sleep score on the card **on purpose** and
requires the suite to catch it.

A re-parse of the stored bodies (`reparse_sleep_blocks.py`, DB backed up,
dry-run read first) changed **no value** — and its own span check says why: on
both stored nights asleep plus awake equals the recorded span, so nothing was
lost between payload and database. What it did recover is the block metadata.
Correcting a parser does not correct what was stored wrong, but it also cannot
recover what was never delivered; the tool reports which of the two applies, per
date, instead of leaving it to be guessed at.

## 2026-09-15 — nine pictures of the record were in the public tree, and no text check could ever have seen them

**What was there.** Fifteen PNGs and one zip were tracked in this PUBLIC
repository. Nine of the images were pictures of the health record: the full
regimen with Indian brand names and strengths, stock counts and a dated pillbox
fill, dated dose times, a refill banner naming two medicines, logged activity,
an interaction review headed *"the medicines you take now"* listing four
molecules with doses plus two more in named findings, and **two separate
windows of blood pressure readings** — 08–10 Sep and 11–13 Sep, four dated
readings each with averages and extremes.

Every NO_SECRETS run reported clean and was **right to**. Checks A, B and C
read text. A PNG is bytes. The checker could not have seen this however
carefully it looked.

**Judgement could not have been the defence either.** Of the sixteen, one that
looked like a scan of a real lab report was synthetic — its values match
`gutlog/scan_lab/make_images.py` exactly — and one that looked like a harmless
UI screenshot carried a real date in a filename field. Classifying pictures by
eye is a thing somebody has to get right every time, forever. So all fourteen
non-icon binaries were purged, not the nine: over-purging costs nothing when
only two icons are referenced by code.

**The mechanism.** `gutlog/test_ui_now.py` wrote **eleven** screenshots into
the app folder on every run — `banner.png` and `salts.png` included, which had
looked hand-made — and `git add -A` collected them. The committed versions came
from a run against the live database. Every other suite in the repo already
wrote to a temp directory; this was the only one.

**The remedy, in three layers.**

1. **The suite writes outside the tree.** `GUTLOG_UI_SHOTS`, defaulting under
   the system temp directory, and the suite **refuses to start** if that path
   resolves inside the repository or beside the app under test. It then asserts
   at the end of every run that no image beside `app.py` was added or changed.
   A file never written into the tree cannot be committed by accident.
2. **NO_SECRETS check D.** Any tracked file with a binary or image extension is
   refused unless it is in `BINARY_ALLOW` — two PWA icons, each carrying its
   reason. No screenshot is on it and none should be: a synthetic screenshot
   becomes a live one the next time its suite meets a real database. D also
   reads the first bytes of every tracked file that is *not* binary by
   extension, because an extension rule alone is defeated by renaming. The
   tracked listing is now fetched unconditionally — it used to be fetched
   inside `if terms:` for C's sake, which would have left D unarmed on any
   machine without the gitignored word list.
3. **`.gitignore`**, stated in the file as the backstop it is and not the
   remedy.

**Gates.** `tools/test_no_secrets_binaries.py` **7/7**, negative control
**7/7 seen to fail**. The owner's requirement is asserted in as direct a form
as it can be put: a PNG is created in a throwaway git repository, added, and
NO_SECRETS is run against that tree — nothing simulated, and the real index
never touched. Assertion 05 (an allowlisted icon is *not* refused) cannot fail
on a version change, so it is controlled by mutation `emptyallow`: empty the
allowlist and the icons are refused. An allowlist never shown to let anything
through is decoration, and the pressure on a check that blocks a needed file is
to switch the check off.

**The rewrite.** `git filter-repo --invert-paths` over the fourteen paths.
32 commits and 197 files survive.

| | |
|---|---|
| Before (what GitHub served) | `512c390` |
| After (what GitHub serves now) | `30453ce` |

**Two corrections to what was first written here**, both found while preparing
the GitHub purge request:

- *"Every commit sha changed"* was wrong. **Two** commits predate the first
  tracked image — `0154e22` (Initial commit) and `494ec2f` (10-Sep) — so
  filter-repo had no reason to touch them and they are still reachable from
  HEAD. **29** shas were served by GitHub and are now unreachable, not 32.
  (`d42b1c9` is stale too but was never pushed, so GitHub never served it.)
- The `commit-map` filter-repo leaves behind is **per invocation**, and this
  purge took two — the first exited 0 having missed a path, see below. The
  saved map therefore described the *second* run, mapping the intermediate
  history to the final one, not the original to the final. Useless for the
  purpose it was produced for. The correct list is a set difference between
  the pre-rewrite `.git` backup and the repository now, written to
  `_github_purge_request_stale_shas_20260915.txt` outside the repo.

Both errors came from trusting a tool's own report instead of measuring the
thing being claimed — the same shape as the exit-0 miss below.

Verified from a **fresh clone of what GitHub now serves**: 32 commits, 197
files, the only images in the entire history are the two PWA icons, and each of
the nine real-data paths returns 0 commits. A magic-byte scan of all 321 blobs
in all commits finds no binary but those two icons.

**One near miss worth recording.** The first filter-repo run exited 0 and left
`gutlog/activity.png` in every commit. The paths file had been written with
PowerShell `Set-Content -Encoding UTF8`, which prepends a BOM, so the **first**
entry — and only the first — did not match. The blob-level verification caught
it; the exit code would not have. The same BOM trap had already corrupted a
`↑` in `test_ui_now.py` earlier the same day via `Get-Content -Raw` on a
BOM-less UTF-8 file. **Do not round-trip source or path lists through
PowerShell 5.1 file cmdlets.**

## 2026-09-15 — check C had never once run: git was found, not missing

PUBLISH_HEALTH.bat refused with `could not run git: [WinError 2] The system
cannot find the file specified`, four gates in. It reads as git being gone.
**It was not.**

**What is actually on this machine.** No Git for Windows, and there never has
been: no `HKLM\SOFTWARE\GitForWindows`, no uninstall entry, nothing under
Program Files, no App Paths entry, and no git directory on PATH — not in the
process environment, not in the stored User PATH, not in Machine. The only git
present is the copy bundled inside GitHub Desktop, at
`%LOCALAPPDATA%\GitHubDesktop\app-<version>\resources\app\git\cmd\git.exe`
(2.53.0.windows.4), under a folder whose name changes with every app update.
GitHub Desktop's own logs stop on 2026-08-16, so it is not being used as a
client either.

**What changed: nothing about git.** PUBLISH_HEALTH.bat has resolved git out
of that bundle since it was written and used it for its own add / commit /
push — which is how the 14-Sep publish worked. It simply never passed that
path to the Python child, so `tools/NO_SECRETS.py` looked for a bare `git` on
PATH and found nothing. Check C was added on 14-Sep in `1140ed4`, one commit
before HEAD. **The first publish attempt with a NO_SECRETS that contains check
C is the one that refused, and it refused correctly.** Check C had never run on
this machine at all.

**Two fixes, deliberately in both places.**

- `resolve_git()` in NO_SECRETS.py: `NO_SECRETS_GIT`, then PATH, then the
  known installs including GitHub Desktop's bundle, newest first. Every
  candidate is *run* before it is trusted — existing on disk is not the same
  as working, and a stale `app-*` folder left by an update keeps its files.
  `NO_SECRETS_GIT` is treated as an **instruction, not a hint**: if it is set
  and does not work, that is an error and the search stops. Being told which
  git to use and quietly using another is how a check ends up reporting on
  something other than what the caller meant.
- **GATE 0** in PUBLISH_HEALTH.bat: resolve git, prove it answers `--version`,
  export it as `NO_SECRETS_GIT`, and report it on its own summary row. It
  refuses at the top, by name, instead of surfacing four gates later as a
  missing-file error. A resolution only one caller knows is a resolution that
  gets lost again, which is why it now exists in the checker too.

**Check C is not weakened and cannot pass when it cannot run.** That is the
whole risk in this change and it is where the evidence went.
`tools/test_no_secrets_git.py` **8/8**, negative control **8/8 seen to fail** —
six against the reconstructed pre-fix build, and the two that matter under
mutation `weaken`, which drops `tracked_err` from the blocking test so a run
that could not reach git exits 0. Both refusal assertions catch it.

The harness also caught a bad assertion of mine before it shipped: assertion 06
originally ran NO_SECRETS in bare mode, which walks the tree, finds the
gitignored `.bak` rollback copies and blocks on check A — so it would have
passed against a build with check C torn out entirely. It now runs
`--files-from` a clean list and asserts `secrets check: clean` first, so the
only thing that can block is C refusing. Exactly the failure mode rule 2a
exists for, found by the rule.

`tools/test_publish_gate0.py` **12/12** lifts GATE 0's text verbatim out of the
batch file and runs it three ways — resolved, present-but-not-git, and
genuinely absent — with the last two required to refuse. Its negative control
needs no batch patcher: point it at `git show HEAD:PUBLISH_HEALTH.bat` and all
twelve fail.

**Verified end to end**: `NO_SECRETS.py --files-from … .` now reports
`tracked-file clinical check: 204 files carried by git, none naming a drug
outside the allowlist`, and exits 0.

## 2026-09-15 — the as-taken count stops counting medicines nobody took (RxGuard v1.7.0, GutLog v3.18.0)

**The defect.** `/astaken` led with two REDs and ten AMBERs and GutLog's home
banner mirrored the count. Both REDs were cumulative burdens whose largest
contributors had **no logged dose at all** in the window — as-needed medicines
that had simply not been needed. The engine already knew: it printed a
POSSIBLY STALE paragraph under eight of the thirteen findings saying exactly
that, with the alarming total first and the correction last. A daily red badge
for a burden nobody is carrying is how a real red badge stops being read, so
this is a correctness fix and not a cosmetic one.

**RxGuard v1.7.0.** Every cumulative burden now has two totals: *as taken*,
over molecules GutLog logged a dose of in the window or carries in its own
regimen, and *if all taken*, the old behaviour. The chip, the page headline
and GutLog's banner read the first; the second stays inside the finding,
labelled, counted nowhere. Findings that reach a threshold only with the
untaken medicines added back — and any finding resting entirely on untaken
molecules — move to one collapsed section that names those medicines **once**,
and the per-finding POSSIBLY STALE block is deleted. Finding cards collapse to
chip, title and consequence, with an ACTION marker visible before opening. The
burden mechanism stops claiming "plus the proposed change" on a page that has
none, and the absence-of-a-flag caveat moves to the page foot. Scoped to
`/astaken`: Quick check and Full analysis genuinely are about a proposed
change and are untouched. The hand-typed `kind` column is deliberately not
consulted — one wrong `chronic` there would silently restore the old count.

**GutLog v3.18.0.** Three Watch tiles were drawing a label with nothing under
it: `load_hours` arrived `n: 0` with every field null, and `resting_hr` and
`hrv_ms` arrived with a six-day median and no reading today. `/api/watch` now
withholds a tile that holds nothing and the card refuses to draw one anyway;
a tile with only a median shows the median, muted, with *"6-day median, no
reading today"* under it and no day label. The Watch footnote stops arguing
from a clinical premise carried forward from an old investigation that a
current one contradicts, and keeps the honest reason, which never depended on
it — the basis for that correction stays in the health record on the server
and is not restated here (rule 5d). The medicines banner is neutral unless
RxGuard has a RED it can stand behind.

**Gates.** Three negative controls, 29 declared assertions, **29 seen to
fail**; 22 against the reconstructed previous build and 7 by deliberate
mutation where the previous build already had the property. The assertion that
matters most compares a *pair* of totals across adding one dose row, because
"the burden is RED once the dose exists" was true of the old build too and
would have proved nothing. Suites: `test_astaken_honest.py` 16/16,
`test_watch_tiles.py` 7/7, `test_ui_now.py` ALL PASS, plus `test_astaken.py`
15/15, `test_reconcile.py` 18/18, `smoke_test.py` 48/48, `validate.py` 50/50.

**Deploy order: RxGuard first.** GutLog's banner reads RxGuard's count, so
shipping the neutral banner before the honest count gives a quiet-looking box
around an inflated number.

**Out of scope, on purpose.** The sleep tile and the activity rings on the
Watch card, and the four sleep-timing fields: the Apple Watch has sent nothing
since 2026-09-13 and there is nothing to verify them against.

## 2026-09-14 — clinical detail purged from history, and NO_SECRETS now asks what is ALREADY tracked

**The question nobody had asked.** The clinical check only ever looked at what
was being ADDED. It never looked at what was already in the tree. Every clean
run reported "no drug or molecule names in staged files" — true, and useless.
Same shape as the findstr CRLF hole in the publish script, one layer up: a
gate that guards the doorway and never looks at the room.

**What was actually there.** Four documents were named as suspects. Reading
them: `rxguard/ANALYSIS_Sources_and_Activity_v1.md` names no medicine and no
condition (kept); `CLAUDE_CODE_PROMPT_fitlog.md` names none either, though it
carries dated step counts (kept by decision); `rxguard/PLAN_MedKnowledge_v1.md`
named a molecule with its strength and disclosed a drug class he takes;
`CLAUDE_CODE_PROMPT_pain_v1.md` named three molecules with doses and a dated
stop. **But a scan of all 199 tracked files found four more**, including a UI
placeholder in `rxguard/app.py` phrased as a documented finding with its year
and a drug reaction — worse than any of the four, and unfixable by deleting a
file. And a scan of **every blob in every commit** found the real problem:
`gutlog/add_regimen.py` at an old sha held the **full regimen, Indian brand
names included** (30 terms), plus `fitlog/knowledge/med_stack.json` (14) and
`gutlog/patch_redact_seed.py` (15) — the redaction patcher embedding the very
list it had removed. A redaction commit had cleaned HEAD in July and left
every old sha serving the original.

**What was done.** Each of those files had exactly two blobs: the original and
the redacted one. So the fix was surgical rather than a squash — 26 superseded
blobs stripped by id, two documents removed from every commit by path, and two
placeholders plus one patcher docstring neutralised in place first. All 30
commits and the whole knowledge base survive. `git-filter-repo`, then a forced
update `ccdee31…1140ed4`. Verified from a **fresh clone of what GitHub now
serves**: 30 commits, 197 files, both documents at 0 occurrences, and the only
paths still naming a drug are the eight allowlisted knowledge-base files.

**NO_SECRETS.py gains check C**, which scans `git ls-files` — the index, so one
call covers both what is committed and what has just been staged — and BLOCKS
on any clinical term outside an explicit `CLINICAL_ALLOW`, each entry carrying
its reason. RxGuard's curated knowledge base, its formal validation suite and
its declared-synthetic smoke fixture are allowlisted: generic pharmacology is
not a statement that he takes any of it. **On its first run check C caught its
own file**, because the paragraph explaining it had named two molecules as the
illustration. That is left recorded in the header.

**Still exposed, and it needs a support request.** Proven, not assumed: after
the force-push, `git fetch origin <full-sha>` against GitHub still returns the
old commits, and `gutlog/add_regimen.py` **was read back from GitHub with the
clinical terms intact**. Unreferenced objects survive until GitHub garbage-
collects. A support request asking them to purge is the last step, and a fork
by anyone would make that impossible.

RxGuard redeployed for the placeholder change (server gate green: 49/49, 32/32,
10/10, 15/15, 18/18) and all three apps re-verified byte-identical to the repo.
Full backup before any of it: `_backups/health-systems-20260914_215833.git`
(mirror, all refs) plus a worktree copy — **both still contain the old history
and the clinical detail**, so they are the one remaining local copy.

## 2026-09-14 — PUBLISH_HEALTH.bat v2: it now says what happened, and the secret gate actually works

Asked for: the window closed on success, so the one line worth reading — did
this reach origin — was the one line nobody ever saw. Every exit path now
ends `call :summary`, `pause`, `exit`, success and failure alike. The summary
names all eight gates and prints **local HEAD and origin HEAD in full**, so
the push is checked by eye rather than taken on trust. A gate that never ran
reads "not reached", never green.

**Found while testing it, and far more serious than the pause: the secret
gate had a hole, and it was the three worst patterns.** `git diff --cached
--name-only > file` writes LF line ends; `findstr` only recognises CRLF, so
it read the whole staged list as ONE line and a `$` anchor could only match
at the very end of the file. `[.]db$`, `[.]env$` and `[.]tmp$` — the diary
databases, the bearer-token env file, and a patcher's half-written copy —
**never matched**. Proved against a scratch repo with its own bare origin: a
lone staged `health3.db` went through the v1 gate, was committed **and
pushed**. The gate only ever appeared to work because a mixed commit usually
also contained a `.bak` or `.token`, which are unanchored and did match.

**Nothing ever leaked.** 199 distinct paths have been added across the whole
history and not one matches the block list; nothing matching is tracked now.
`.gitignore` was doing the real work. This was the second layer, and the
second layer was not there.

Fixed by matching the staged list **one path at a time through a pipe**,
which terminates the line with CRLF whatever git wrote — so the anchors work,
and the offending path is now *named* instead of the list being re-searched.
Added a **gate self-test that runs on every publish**: nine names that must
be blocked, five ordinary files that must not be. If the gate cannot catch
what it must, or catches what it must not, the script refuses before it
stages anything — a gate nobody has seen fail is not a gate (CLAUDE.md 2a,
applied to a shell script).

Third fix: with delayed expansion on, a bare `!!` in an `echo` is swallowed
by the parser, so **every `!!` warning marker in the file had been invisible**
— "REFUSING", "NOT FOUND", all of them printed bare. Carets are processed
before delayed expansion, so one caret is not enough either; two are.

Every path proved against a scratch repo with a bare origin, not reasoned
about: ordinary change publishes; nothing-to-commit reports as such; a lone
`health3.db`, `ingest.env`, `.tmp`, `.bak` and `feed.token` are each blocked
by name with HEAD unchanged; the self-test refuses when deliberately made to
under-catch *and* when made to over-catch; a failed push reports the sha that
exists locally and did not reach origin; and an origin holding a different
commit fails verification with both shas shown. The tested copy differs from
the shipped file in exactly one line — the repo path.

## 2026-09-14 — GutLog v3.17.0 + FitLog v1.6.0, Phase M: Down days

DEPLOYED FitLog 18:19 IST, GutLog 18:20 IST, FitLog first. GutLog `app.py`
sha256 `86887cf6…`, 363,598 bytes; FitLog `app.py` sha256 `8f66d787…`, 77,578
bytes — both byte-identical to the repo builds. `patch_gutlog_v3170.py`, 24
anchors; `patch_fitlog_v160.py`, 6 anchors; both reverse byte-for-byte to
the pre-patch builds. Verified live after one request: `schema_version`
**3.3.4**, `down_days(id, day, components, coped, note, created)` with **no
temperature column**, **0 rows** (nothing seeded). Databases backed up first
(`sqlite3.backup()`, `integrity_check` ok, 25 and 13 tables). Each server
gate ran before its restart with rollback on failure — and the FitLog gate
*used* the rollback once: the cross-app suites were pointed at
`app.py.new`, whose suffix `spec_from_file_location` refuses, so they
printed no result, the script restored v1.5.0 and did not restart; re-run
with a `.py`-named candidate, 15/15 suites green, then deployed.

**Why.** A recurring cluster the record has carried for over two years as
"recurrent fatigue and subjective feverishness — never a documented
temperature, no cause established" — hip and thigh ache, left abdominal
pain, fatigue, feverishness, a heavy head, sometimes the eyes, usually a
broken night. Nothing counted those days. Every day around them was already
fully recorded; what was missing was the marker saying which days were the
bad ones. One tap now converts months of existing rows into an answer.

**GutLog — the marker.** `down_days(id, day UNIQUE, components, coped, note,
created)`: a calendar day, not a moment, so not an `episodes` row; a second
tap corrects rather than duplicates; **runs are computed at read time** from
consecutive days, nothing stored about them. Temperature is **not** a column
here — the card prompts for it once, with a *Not now* that is remembered for
the day and does not nag, and writes a real `vitals` row. The two analgesic
chips write a real `doses` row with the reason set, through the helper now
shared with the pain tiles, and only for chips newly added, so correcting
the row cannot log a tablet twice. Schema 3.3.3 → 3.3.4 via `_migrate`.

**The entry.** A *Down day* card on Now beside Pain: *Mark today as a down
day*. One tap, done. Components (in the record's own words) and *coped with*
sit behind it, optional, and save as they are tapped — no form to leave
half-filled on a day with a heavy head. A consecutive day extends the run
and the card reads *day 2 of this run*. Past days are marked from Day by
day. Nothing seeded.

**The view.** A *Down days* fold on Review: count per month and run
lengths; **each down day beside the day before it** — steps, hours on legs,
exercise minutes, sleep, doses, temperature, epoch — from rows that already
exist; which components co-occur; how many down days carry a temperature
(and, if none, that the one active ask is going unanswered); and runs where
he kept moving against runs where he rested, as an observation with its n,
never advice — the payload is asserted free of advice words.

**Two connections.** On the third consecutive day, a quiet note verbatim
from the Action Plan's flare protocol (calprotectin and ESR/CRP within 48
hours). The 14-day watch row gains a **down-day lane** — a square, in a
fourth colour validated with the other three (light `#0A93B0`, dark
`#3D9BE0`, all five checks passing in both modes).

**FitLog — the trend leaves them out.** `gutlog_downdays()` reads the new
bearer-gated `/api/feed/downdays`; `w_trend_card` **draws** a down day's bar
hatched and named, and leaves it out of mean, low and high, saying how many
it left out. Not hidden: a bad day is a fact about the day. The watch feed
now reaches back 180 days (was 60) and carries `sleep_hours`, so the
down-days view can show months, with sleep. No rule reads any of it.

**Evidence.** `gutlog/test_phase_m.py` **19/19**, and 19/19 under
`RUN_AT_TIME` at 00:02, 05:05 and 23:58 — run grouping is date arithmetic
and this is exactly where the earlier clock bug lived. `fitlog/
test_downdays_trend.py` **5/5** against a real GutLog. `test_ui_now.py`
ALL PASS, both themes, 390 and 360px, with a route-hit counter proving the
tap went through the page's own fetch. Negative controls (CLAUDE.md 2a):
GutLog server-side **18 declared, 18 seen to fail** against the
reconstructed v3.16.0; FitLog **5/5 seen** against v1.5.0; browser-side
figures in the DOSSIER. Both patchers reverse byte-for-byte to the
pre-patch builds.

## 2026-09-14 — GutLog v3.16.0, Phase L: readability fixes and app-wide dark mode

DEPLOYED 09:59 IST. `app.py` sha256 `c67ae491…`, 337,053 bytes,
byte-identical to the repo build; `patch_gutlog_v3160.py`, 33 anchors.

**Fixed — the header summary was 13.5px.** The Phase K brief specified 15px;
`.card.fold .fs` now is. This was a real bug, and the existing sub-14px
assertion had been catching it correctly all along.

**Fixed — two assertions that were not evidence.** The overflow check asked
`#wkStrip`, a `display:block` div that reports `scrollWidth 0 / clientWidth 0`
on the live card: `0 <= 0` is true, so it could not fail. It now asks the
elements that actually scroll (`html`, `body`, `main`, `#nowWatch`,
`#wkChart`) and **treats a zero measurement as a failure rather than a pass**.
The size check measured inherited `font-size` on elements that paint nothing —
`.wkcol` and `.bar` are empty divs inheriting 13.33px — so it now measures
only elements that own a non-empty **direct text node**.

**`.hint` raised to 14px app-wide**, and checked where it renders rather than
where it is declared: six screens across 21 segmented sub-views, at 390px and
360px, in both themes. 38 visible hints, none under 14px, nothing scrolling
sideways.

**Dark mode, all four pages.** Selected against the dark surface, not flipped:
`prefers-color-scheme` by default plus a persistent three-state override
(System / Light / Dark) applied before first paint. **94 text/background pairs
measured across both modes: 0 failures** — worst light 4.48:1 (large text,
floor 3:1), worst dark 6.24:1. Five light-mode pairs in the shipped build were
already below 4.5:1 and are fixed at the token.

**The chart palette was worse than the dark mode was going to be.** The
pain/tea/coffee line trio failed four of the validator's five checks, with a
normal-vision ΔE of **10.1** between pain and coffee — two lines hard to tell
apart with *full* colour vision. One validated set now serves both the lane
markers and the chart: light `#B3372A`/`#6A3FA8`/`#B57B08` on `#FCFCF9`
(deutan 11.1, tritan 14.1, normal-vision 16.0) and dark
`#C1443A`/`#7A5FD0`/`#B58E08` on `#18211F` (deutan 11.2, tritan 15.9,
normal-vision 18.5), all ≥3:1 vs surface, all five checks passing in both.

**New gate — `tools/NEGATIVE_CONTROL.py`, and CLAUDE.md rule 2a: an assertion
is evidence only if it has been seen to fail.** It reconstructs the previous
build by running the patcher in `--reverse` (verified: the reversal reproduces
v3.15.0 byte-for-byte) and requires every newly declared assertion to appear
in that run's failure list; for assertions guarding a property the previous
build already had, it breaks that property on purpose in a copy of the current
app instead. **18 declared, 18 seen to fail.** It earned its place
immediately: a card-scoped contrast check *passed* against the previous build,
which is to say it was decoration. Widening it to the whole screen made it
fail there — and it then found **five real dark-mode faults in this very
release**, the worst at 1.12:1.

**Also fixed, found not asked for — every `test_ui_now.py` run was writing a
live-format bearer token into the repository.** GutLog mints `feed.token`
beside `app.py` when `GUTLOG_FEED_TOKEN_FILE` is unset, so each run dropped a
real 64-char token into a public repo's working tree and made `NO_SECRETS.py`
refuse afterwards. It was `.gitignore`d and **was never committed**, and the
server's own token was never touched. The suite now points that path at its
throwaway database directory and asserts it leaves nothing secret-shaped
beside `app.py`.

## 2026-09-14 — GutLog v3.15.0, Phase K: Watch card correctness, then readability

DEPLOYED 08:24 IST. `app.py` sha256 `ea268dc4…`, 280,199 bytes,
byte-identical to the repo build; `patch_gutlog_v3150.py`, 8 anchors.

**Fixed — voice.** `On his legs` was third person: it came from a brief
written *about* him, not *to* him. Every rendered string is now second person,
and `test_phase_j` case 03k fails on a third-person pronoun in any string the
card renders. The briefs will keep that voice, so the guard belongs in the
code rather than in a habit; `test_ui_now` checks the rendered text too.

**Fixed — a part-day total was being compared with whole-day medians.** At
07:53 the card read *"62 steps, ↓ vs 1,214 median of 10 d"*, which points down
every morning **by construction**. `WATCH_KIND` now declares, in one place,
what kind of quantity each tile holds:
*cumulative* (steps, exercise minutes, standing load) headlines the **last
complete day** with its arrow and a median excluding it, and shows today's
running figure underneath, labelled *so far today*, **with no arrow** —
because there is nothing valid for it to be compared against; *settled*
(resting HR, HRV) takes today's reading the moment it exists. **No time-of-day
threshold anywhere**, and a test fails if one appears: the distinction is in
the shape of the quantity, not the clock.

**Fixed — every figure names its day.** Signalling "today" by the *absence* of
a label is what made `62 steps · 19 min · yesterday` read as though both were
yesterday's.

**Live immediately after the restart:** steps headlines **4,902 from 13-Sep, ↑
against a typical 930.5 over n=10**, with today's **284 reported separately
and no arrow**. The typical moved from 1,213.5 to 930.5 because the median now
excludes the headline day — correct, and the reason the old number looked
plausible while being wrong.

**Readability — the root cause was arithmetic, not taste.** `#wkStrip` was
five 116px tiles plus gaps = **612px inside a 368px strip**, so it overflowed
and everything else followed. Now one full-width hero over a two-column grid
(~176px a cell, cannot scroll sideways); nothing below 14px; the four-fact
meta string split into a comparison line in ink and a provenance line; sources
named in words; tiles given a real surface; chart 64px → **104px** with 2px
bar gaps, 4px rounded data-ends, a recessive baseline, and **tap-to-reveal**
because `title=` does nothing on a phone; lane markers at 10px carrying a
**shape** as well as a colour (disc / diamond), and direction never
colour-alone.

**Colour was computed, not eyeballed.** The marker set passes every check of
the data-viz validator on the card surface — lightness band, chroma floor, CVD
separation (worst all-pairs ΔE 15.6 deutan), normal-vision floor 19.1,
contrast ≥ 3:1. The palette is otherwise untouched, as measured.

**Dark mode is Phase L, with evidence rather than an excuse.** Flipping these
marks onto a dark ground **fails** the validator: two of three fall outside the
dark lightness band and three drop to 2.4–2.9:1. A real dark variant needs its
own steps chosen against the dark surface, and because the card shares one
`:root` palette with every other screen it also needs new ink/muted/line/card
tokens and a pass over every component. That is app-wide, not a corner of a
card, so it is its own phase rather than an automatic flip shipped today.

**One deviation, flagged rather than quietly resolved:** the brief asked for a
13px tile label and 13px provenance line *and* for a test asserting no
computed font-size below 14px. Those contradict. 14px wins — the test is
explicit and the stated reason is that small text is hard to read.

**Tests** — `test_phase_j.py` **28/28** (22 before; six new cases for kind,
the running figure, the one-place declaration, day naming and voice), and
28/28 at 00:02, 07:30 and 23:58. Four existing cases were rewritten rather
than loosened, because the semantics genuinely changed: they had encoded
"headline = today". `test_ui_now.py` gained six measured assertions and its
stub payload was updated to the new shape; it needs Playwright and was not run
here.

## 2026-09-14 — GutLog v3.14.0: the watch strip falls back one day

First real-use finding on the Phase J card, and a good one: at 07:30 all five
tiles read **"no data"**, because the phone had not uploaded the day yet.
Correct behaviour, wrong design — he opens this screen between 5 and 7am and
the sync happens later, so the strip was blank at exactly the hour he looks at
it. DEPLOYED 07:43 IST, `app.py` sha256 `b4649026…`, 275,081 bytes,
byte-identical to the repo build; `patch_gutlog_v3140.py`, 5 anchors.

- A metric with no figure for today now shows **yesterday's, labelled
  "yesterday"**. A fallback that is not labelled is a lie, so the label is on
  the tile and in the card header.
- **"No data" is reserved** for the case where neither day has a figure. That
  is still a real state and still says so, and the fallback never reaches back
  a second day.
- The direction compares the **shown** day against the trailing median with
  that day **excluded**. Leave it in and the figure is compared against a
  median it is itself inside — which on a fallback day reads "level" every
  single time.
- Each tile carries **n**, the days behind the median, so a thin baseline is
  visible as thin. **Deliberately not a plausibility threshold:** the steps
  median currently sits near 1,200 because two days from before the source fix
  are still inside the window. That self-corrects as the window moves, and a
  heuristic written for it today would outlive the problem it was written for.
- Standing load falls back on the same terms — at 5am an operating day has not
  necessarily been tapped either.

**Live immediately after the restart:** `exercise_minutes`, `resting_hr` and
`hrv_ms` fell back to 13-Sep and were flagged stale, while `steps` showed a
genuine 62 for today, `dir=down` against a median of 1,213.5 over **n=10**.
The thin baseline, visible as thin, exactly as predicted.

*Consequence worth knowing:* the fallback triggers on absence, and steps is
the first metric to arrive each morning. So the strip is often **mixed** — a
small genuine steps figure for today beside yesterday's HR and HRV. Every tile
names the day it is showing, so this is legible rather than wrong, but it does
mean the early-morning steps tile can read very low. Whether steps should also
fall back while the day is young is a judgement, not a bug, and is left open.

**Tests** — `test_phase_j.py` **22/22**, **18/22 against v3.13.0** (the four
new cases failing and nothing else), and 22/22 under `RUN_AT_TIME.py` at
05:05, 07:30, 23:58 and 00:02 — the fallback is clock-shaped and that is where
the earlier clock bug lived. The median-exclusion case asserts the shown value
is not equal to its own median, which is the failure mode it exists for.

**A suite defect fixed while adding them.** A case that deleted today's
metrics failed part-way, left them deleted, and case 15 then failed for a
completely unrelated reason — one real failure presenting as two. Mutating
cases now restore the fixture in a `finally`, so the negative control reports
exactly the four cases that should fail.

## 2026-09-14 — Phase J: the watch display, and a line-ending trap closed

**Fixed first — `.gitattributes` was handing out CRLF.** `* text=auto`
normalised to LF on commit and gave Windows CRLF back on checkout. Measured,
not theorised: a fresh clone produced `gutlog/app.py` at **265,085 bytes with
4,999 CR bytes**, against 260,086 on the server. The committed blobs were
right; the checkout was not. That mattered because the patchers read and write
with `newline=""`, so a CRLF working copy yields a CRLF `app.py` that is no
longer byte-identical to the server — and `CHECK_FOLDER_PARITY` compares the
working folder with the app folders, **both** of which would have been CRLF,
so it would have passed while the server disagreed with both. Everything
deployed is now declared `eol=lf`; Windows launchers keep CRLF; images and
archives are declared binary. A fresh clone now reproduces all three `app.py`
files byte-for-byte. Also ignored `fitlog-ingest/CLAUDE_CODE_PROMPT_*.md`: the
briefs quote the record they are about, and `git add -A` would have taken the
Phase J brief — molecule names and a clinical result — into a public
repository. **Two earlier briefs are already tracked and still carry that
detail; removing them needs a decision, since ignoring a path does not untrack
it.**

**Added — GutLog v3.13.0 + FitLog v1.5.0, the watch display.** DEPLOYED 05:40
and 05:41 IST, both byte-identical to the repo build (`6c9bf876…` 74,819 ·
`88ed0d85…` 273,810). The data was all arriving and was being shown as one
line, so this is a *reading* change: no new table, no new ingestion, no fourth
app, and no new token. FitLog gains one read-only endpoint,
`GET /api/feed/watch`, on the bearer gate that already existed; GutLog gains a
"Watch" card that joins it to GutLog's own pain rows and operating days —
which is the entire point, because load and pain were in different
applications.

- **Today strip** — steps, exercise minutes, standing load in hours, resting
  HR, HRV, each with a direction against **his own trailing median**, never a
  goal. Fewer than three comparable days gives *no* direction rather than a
  flat one: "not enough to say" and "level" are different statements.
- **Fourteen-day row** — steps per day, with a pain lane and an operating-day
  lane beneath it.
- **Workouts in real IST**, read from the feed's own `start_hm`. Nothing
  slices a timestamp; that was the 2026-09-13 bug, and a test fails if a slice
  reappears.
- **Epoch band** drawn across the same row, so a drug change is read off the
  chart rather than remembered. The label is **data** — created on the server
  only, and absent from this repository.
- **Source honesty** — the larger steps feed wins and the day says which one
  answered; a day with no data reads **"no data"**, never zero.
- **No verdict**: no readiness, recovery, body battery or fitness age. A wrist
  sensor's HR and HRV accuracy depends on the underlying rhythm, so the screen
  shows inputs and carries one quiet footnote. A test fails if any such word
  reaches the payload or the page. Standing load is never folded into
  exercise — not on the strip, not in the row, not into the median.

**Tests** — new `gutlog/test_phase_j.py` **18/18**, **1/18 against the
previous build**, and 18/18 under `tools/RUN_AT_TIME.py` at 00:02, 05:05,
12:00 and 23:58. It runs both applications for real and builds the wearable
tables by running the **real migration** rather than a copied DDL, so it
cannot pass against a schema it invented. The fortnight carries the awkward
days on purpose: no data at all, one feed only, two feeds disagreeing, an
operating day, a day with pain and nothing else, and an epoch starting inside
the window. Properties, not counts — after `"5 tiles"` and `"42 passed"` broke
twice, nothing here pins a number a later change would have to edit. The
clinical-term check reads `tools/clinical_terms.local.txt`, the same list
`NO_SECRETS` uses, because a test that spelled the words out would itself be
the leak; 56 terms checked, and it skips *out loud* where that file is absent.
`test_ui_now.py` gains the new screen with the feed **stubbed via route
interception**, so the drawing is exercised in a real browser without standing
a second service up. Whole gate green on the server before each restart:
GutLog phase A–I, FitLog's fifteen suites, and the cross-app mirror.

**Then `test_ui_now.py` was run under Chromium, and the harness was wrong in
two independent ways — the assertions were right, the scaffolding was not.**

1. **A service worker was eating the stub.** GutLog registers one (`pwa.py`),
   and a fetch served through a service worker never reaches `page.route()`.
   The route silently never fired, the page got the live answer, and the
   block would have been testing the real handler while looking like a pass.
   Proved with a hit counter: route hits `[]`, page rendered "not reachable".
   The context is now created with `service_workers="block"`, and **every
   stub also asserts that its own route actually fired** — the check that
   would have caught this on the first run. It is a property of the app, not
   of this test, so it is in the docstring for whoever stubs the next
   endpoint.
2. **The block skipped instead of failing.** Guarded by `if
   pg.locator("#nowWatch").count():`, it ran against v3.12.0 and reported
   **76 PASS / 0 FAIL** — no evidence at all, presented as a clean run. It now
   emits all seventeen properties as named failures when the card is absent,
   the same treatment the operating-day tile already had, with the names in a
   list beside the block so both runs print the same seventeen lines.

Correction to the record while checking this: the stub payload **did** already
carry `"link": True`. The early-return on `!j.link` is real and load-bearing,
and the suite now asserts the fixture declares it before trusting anything
drawn from it — but that was not a defect in what shipped.

With both harness fixes: **91 PASS / 0 FAIL, "no JavaScript errors" green.**

**Noticed, not fixed:** `test_apple_records.py`, `test_recompute_apple.py` and
`test_workout_day_ist.py` live on the server and in `fitlog-ingest/` but not
in the authoritative `fitlog/` folder, so `CHECK_FOLDER_PARITY` has nothing to
compare and never says so. All three pass.

## 2026-09-14 — RxGuard v1.6.0: a typo was deleting a drug from every check

Everything below came out of what v1.5.0 found on its first run against the
real list, hours after it was deployed. **DEPLOYED 05:08 IST**, `app.py`
sha256 `731f48e9…`, 142,717 bytes, byte-identical to the repo build.

**Fixed — a `drug_key` the knowledge base cannot resolve contributed to
nothing.** No pairwise rule, no CYP derivation, no class duplication, no
burden, no QT sum, no condition rule. The drug was absent from every check on
every screen and the screen looked exactly as it would if the drug were safe.
One missing letter was enough. `unresolved_keys()` now checks every row; the
Dashboard carries a banner **above the medication table** naming each key and
why it fails, and `show_reds.py` leads with the same list before printing a
single finding. Live rows are called out as the gap they are; stopped rows are
reported more quietly, because a record that misleads later is still a defect.
A key containing `+` gets its own message — RxGuard holds one row per
molecule, so a combination belongs on two lines.

**And it is a gate, not a notice.** `smoke_test.py` step 9 proves the
mechanism on its own synthetic fixture and then asserts the **live** list has
no unresolved key, so the next typo fails the pre-restart gate instead of
hiding. It reports SKIPPED out loud when the live database is not on the
machine. Two things had to be fixed to make that honest: `test_kb.py` pinned
the literal `"42 passed"`, so adding a check to the smoke suite turned it red
for no reason — it now asserts *nothing failed*; and it inherited the live
list through that subprocess, so it now points `RXGUARD_DB` at a non-existent
path. **A knowledge-base suite must never be coloured by the owner's data**
(CLAUDE.md 5a), and it quietly was.

**Fixed — three unresolved keys in the live list**, with `fix_drug_keys.py`
(dry-run by default, takes its own `sqlite3.backup()`, moves `medications` and
`med_events` together, refuses a replacement that does not resolve either, and
records a `key-corrected` event so the change is visible). One active
misspelling, one stopped misspelling, and one stopped combination row split
into one row per molecule. **What changed, measured on a copy before anything
was written:** the engine now sees **10 molecules instead of 9**, and the
**constipating burden moved 5 → 6** as the newly-visible molecule joined its
contributors. No other burden moved; RED held at 2 and AMBER at 11. The RED
was right — it had simply been computed on a shorter list than the screen
implied. The "2 taken medicines not on your list" AMBER became 1, and the
UNKNOWN coverage finding disappeared.

**Changed — reconciliation is for chronic drugs only.** "Not taken for 7 days"
means something for a drug meant to be taken daily and nothing for an
as-needed analgesic. `medications.kind` already carried this. On the real list
the reconciliation lines went from **four to one**, and the one left is a
genuine mirror case.

**Changed — staleness says which kind of stale.** The same 14-day silence
meant two different things and one sentence for both was wrong: chronic and
absent from the regimen → *may have been stopped, reconcile*; as-needed and
not taken recently → *this burden may be theoretical rather than current*. A
finding resting on both gets both sentences, chronic first. Verbs agree with
the number of drugs — the first live run read "a, b, c **is** as-needed and
**has** not been taken", caught and fixed before that wording was left in
place rather than after. Both live REDs now read as
theoretical-PRN rather than possibly-stopped, which is the distinction that
was missing when they were first reported.

**Tests** — `test_reconcile.py` **18/18** (12/18 against v1.5.0): the PRN
exclusion, both staleness labels checked on separate findings so neither can
pass by leaning on the other, a **forced** both-labels case because the
fixture happens to raise none naturally and "no case arose" is not a pass, and
the unresolved-key detection including the live/stopped split and the
combination message. `smoke_test.py` **49/49** including the live gate — which
failed with all three keys named before the data fix, then went green after
it. `test_kb.py` 32/32, `test_astaken.py` 15/15, `test_conditions.py` 10/10,
`validate.py` 50/50.

## 2026-09-13 — Phase I: the pain entry surface, and closing the GutLog → RxGuard disconnect

Three apps, one change. **DEPLOYED 2026-09-14, 04:38–04:42 IST**, in the order
FitLog → GutLog → RxGuard, each verified before the next. All three `app.py`
files on the server are **byte-identical to the repo build** (`f6c169ed…`
260,086 · `7a5f6549…` 69,652 · `26d6ae8b…` 136,485), which is what the
`newline=""` handling in the patchers is for. Pre-deploy `sqlite3.backup()`
copies of all three databases, integrity-checked, plus `app.py` rollback
copies, under `predeploy-phaseI-20260914_043720`.

Two things worth keeping from the deploy itself. **The GutLog migration was
watched rather than assumed**: `schema_version` was still `3.3.2` immediately
after `systemctl restart` and only became `3.3.3` after one `GET /login` —
exactly what DOSSIER gap 2 says, seen happening. And `regimen.local.json` was
**merged, not overwritten**: a key-by-key before/after comparison proved
nothing was lost, with `_meta` changed only by a documentation line. The
cross-app mirror suite was deliberately deferred from the FitLog step to the
GutLog step, because it drives pain tiles that did not exist until GutLog was
patched; verifying it earlier would have meant verifying nothing.

**Added — GutLog v3.12.0** (`patch_gutlog_v3120.py`, 26 anchors)
- **Pain now**, a collapsed card on the Now tab beside blood pressure and
  today's doses. Nine tiles, hero first: both hips + anterior thighs, then
  hip/thigh R and L, glutes both/R/L, low back, neck → R arm, neck → L arm.
  R is the THR side and L the native arthritic hip, so **side is never
  averaged away** — separate tiles, separate rows.
- A tile asks three things and no more: score 0–10, treatment chips, and (hip
  and glute tiles only) one optional *goes below the knee* tap. Each tap
  writes **one row to `episodes`** — the table that already carries every
  within-day event. No second pain table. Two new columns through the existing
  idempotent `_migrate` ALTER pattern, the one that added `bristol`:
  `treatments` (pipe-joined, the `days.syms` convention) and `radiates`.
  `SCHEMA_VERSION` 3.3.2 → 3.3.3, so the migration actually runs — and it runs
  on the first request, not on restart, so verify with `schema_version`.
- **An analgesic chip does not create a parallel medicine record.** Tapping one
  writes a real `doses` row (`status='EXTRA'`, `reason` = the pain site,
  `med_id` resolved from `prnmeds`) exactly as an ad-hoc dose does, and posts
  to FitLog so the same event reaches `analgesic_log` with `pain_at_time` = the
  score just entered. A medicine logged in two places is a record that
  disagrees with itself.
- **"eased"** — one tap, no dialog, hours later, stamping `duration` from
  `etime` to now. Nothing is asked at the moment of pain, so `duration` is
  measured rather than picked from a bucket while it still hurts. Offered on
  the Pain card and in the day-view row-action strip.
- **`ot_day` ("Operating day")** with an hours picker (2·4·6·8·10 h), stored as
  minutes like every other activity. The same hip/glute/thigh complex appears
  on long operating days, so the hours must be recorded or every
  walking-versus-pain comparison is confounded by his work. `LOAD_KINDS` keeps
  it out of exercise minutes everywhere; it reaches FitLog through
  `/api/feed/activities` with no new write path.
- `/api/feed/stack` additionally reports each regimen line's `valid_from` and
  an `ended` list of recently closed schedules with their `valid_to`.

**Added — FitLog v1.4.0** (`patch_fitlog_v140.py`, 5 anchors)
- `POST /api/analgesic`, the one inbound write path for `analgesic_log`,
  bearer-gated on GutLog's existing read-only feed token — no new secret,
  machine-to-machine, never session-authenticated (CLAUDE.md rule 5).
  Idempotent on a retry. A molecule the stack does not carry is refused and
  said so, never filed under a chance substring of another generic.
- `ot_day` rendered as **Standing load** in hours, apart from exercise and
  never counted as exercise minutes.

**Fixed — RxGuard v1.5.0** (`patch_rxguard_v150.py`, 8 anchors)
- The GutLog → RxGuard disconnect. Cause, verified in the code:
  `api_feed_stack` correctly drops an ended schedule from `regimen`, and
  `astaken_view` already computed the mismatch — but the engine runs on
  `active_meds()`, RxGuard's own `medications` table, **which nothing
  updated**. A medicine ended in GutLog therefore kept being counted until the
  list was edited by hand.
- It still does not auto-write. Status here carries clinical meaning GutLog
  lacks (tapering is not stopped) and a drug record that changes itself from a
  logging action is untrustworthy. Instead: a reconciliation line when a
  medicine is active/tapering here **and** absent from GutLog's regimen **and**
  has had no dose for 7+ days — all three or nothing; one tap sets status and
  `stop_date` **from GutLog's `valid_to`, not from today**; the mirror case
  inverted, adding from `valid_from`; and any RED or AMBER resting on a drug
  GutLog has not seen for 14+ days marked **possibly stale** rather than
  silently dropped.

**Tests** — three new suites, each verified to fail before the change
(CLAUDE.md rule 2): `gutlog/test_phase_i.py` **18/18** (**0/18** against
v3.11.0), `fitlog/test_analgesic_mirror.py` **8/8** (1/8 before — it runs a
real GutLog and a real FitLog on two loopback ports and proves one tile tap
makes exactly one `doses` row and exactly one `analgesic_log` row),
`rxguard/test_reconcile.py` **13/13** (2/13 before). Regression sweep green:
GutLog phase A/B/C/D/E/F/G, FitLog 23/23 · 36/36 · 18/18 · 19/19 · 29/29 ·
39/39 · 52/52 · 12/12 · 10/10 · 14/14 · kb_lint · smoke 53/53, RxGuard smoke
42/42, `test_astaken.py` 15/15, `test_kb.py` 32/32, `test_conditions.py`
10/10. Phase C 20/20, E 13/13 and G 14/14 and the `fcntl` suites were run on
Linux by the owner; the Windows shortfalls on C and E were file-mode
artefacts, as expected. `test_ui_now.py` green in real Chromium, **no page JS
errors** — rule 5b satisfied.

**`test_ui_now.py` — the operating-day tile.** Its activity check asserted
five tiles and there are now six. Changed to six, and then given the eight
checks the tile actually needs, since a count is not evidence of behaviour:
the `ot_day` tile is present and carries the load class; tapping it offers
**2·4·6·8·10 h and no minutes picker**; it has no intensity row; the tile
reads back in hours; and after saving, `/api/activity` reports **30 exercise
minutes and 480 load minutes** with the entry flagged `load`, the row reads
*Operating day 8 h on your legs · load, not exercise* rather than 480 min, and
the header keeps the two apart. Against v3.11.0 the tile does not exist, so
the count fails and the block emits eight named failures instead of a
traceback. Server suites never run page JS, and the server has no Playwright,
so `test_phase_i.py` case 17 pins the same constants offline — change the
picker and the offline suite fails too.

**A test defect worth recording: both new suites were clock-dependent.** They
wrote literal times — 08:00, 08:30, 07:00, 18:00 — to *today*. GutLog
correctly refuses a time that has not come yet, so the suites were green after
18:00 and **red at 03:48**, which is when this was noticed. A suite whose
result depends on the hour it is run is not evidence, and 5am logging is the
stated reason this app has the shape it does. Every time written to today is
now derived from the clock and clamped to midnight; the eased-tap arithmetic
is tested as a pure function on nine fixed durations (both sides of the hour
boundary) instead of leaning on elapsed real time; and one time-keyed row
count became a delta, because inside the first 90 minutes after midnight every
derived time clamps to 00:00 and collides. New `tools/RUN_AT_TIME.py` runs any
suite under a faked clock — both suites verified at 00:00, 00:02, 01:10,
05:05, 12:00, 18:30 and 23:58, `test_reconcile.py` at four of those. It must
patch the clock before the suite *and* the app import it, or the two disagree
and the failures are artefacts of the harness; that is written into the file.

**What RxGuard's two REDs turned out to be** — both are **cumulative-burden**
findings, each resting on three molecules, and **both are now marked possibly
stale** because one molecule in each has had no dose in GutLog for 14+ days
and is absent from its regimen. 11 AMBER alongside them, 8 of the 13 findings
marked. The question the prompt asked — does at least one RED rest on a drug
already stopped — answers *possibly both*, which is a state the old page could
not express at all. The reconciliation also caught **a misspelled molecule in
the RxGuard list from both directions at once**: the typo has no
knowledge-base entry so contributes to no check, while the correct spelling
arrives from GutLog as "taken but not on your list". A typo silently deleting
a drug from every safety check was previously invisible. Values stay on the
server; `rxguard/show_reds.py` prints the current picture, read-only.
**One judgement left open, deliberately:** three of the four "still active
here" lines are `kind='episodic'` PRN analgesics, and both stale REDs are
stale *because of* PRN drugs. For a PRN, "no dose in 7 days" is normal and
says nothing about stopping. `medications.kind` could narrow both tests to
`chronic` — but whether a PRN untaken for a fortnight still belongs on the
list is a clinical call, not a coding one.

**Deploy order** — FitLog, then GutLog, then RxGuard. Each degrades safely
against an unpatched neighbour, so the order is a preference rather than a
constraint: GutLog against an old FitLog simply reports the dose as not
mirrored and keeps its own row; RxGuard against an old GutLog still raises the
lines, only without a one-tap stop date. Per app: WinSCP the files up, run the
patcher with `--check` first, run its suite, restart, then verify. GutLog's
migration runs on the **first request**, not on restart — check
`schema_version` = `3.3.3`, never `systemctl status`. Copy the updated
`regimen.local.json` (it now carries `pain_analgesics`) up beside `app.py`, or
the analgesic chips will not appear.

**Repo hygiene** — the analgesic chip labels are medicine names and this
repository is public, so they are read from `regimen.local.json` →
`pain_analgesics` by `_local_seed`, like `prn_seed`; a clone without that file
gets the four physical measures and no drug chips, and the page never carries
them either (the tiles and chips are built from `/api/pain`). All three test
fixtures are synthetic. `NO_SECRETS.py` reports **no new drug name** in any
changed file; `CHECK_FOLDER_PARITY.py` green; all three patched files are
LF-only, so the same patcher run on the server produces identical bytes.

## 2026-09-13 — workout times in IST, steps take the larger source (FitLog)

**Fixed**
- **Workout times were shown in UTC.** The watch sends
  `2026-09-11T01:41:29.553Z`; `watch_activity()` did `[:19]`, which dropped
  the `Z`, and GutLog then rendered it as local time. A 07:11 walk read as
  01:41. New `_ist()` helper converts at read-out; a stamp that is not a
  Z-stamp passes through unchanged. The two stored walks now read 07:11:29
  and 21:58:41. `patch_fitlog_ist_and_steps.py`, 3 anchors.
- **Steps: a barely-worn watch beat a fuller phone count.**
  `SOURCE_PRECEDENCE` put `applewatch` first for *every* metric, so 12-Sep
  showed 301 steps while `healthconnect` held 2,430. Steps are a coverage
  metric, not a sensor-quality one, so the largest count for the day now
  wins — inside `watch_activity()` only. Every other metric keeps the
  precedence untouched: `resting_hr`, `hrv_ms`, `spo2_pct`,
  `exercise_minutes` and `stand_hours` have no second source, and
  `healthconnect` supplies steps alone. Audited across all 10 days holding
  step data: **only 12-Sep changes** (301 → 2,430). 06 and 07 Sep stay at
  18 and 13 — single source, nothing to compare.
- **Workout day derived from a UTC stamp (forward-looking fix).**
  `_apple_samples()` dated an Auto Export workout with
  `_parse_date(start_raw)` — the first ten characters. IST is UTC+5:30, so
  anything starting before 05:30 IST was filed to the *previous* day, and
  his walks are 05:00–07:30. Now routed through `_to_ist_date()`, which the
  ios path already used; a `+0530` stamp behaves exactly as before.
  `patch_workout_day_ist.py`, 1 anchor.
  *Scope:* audited live first — `health_workouts` holds 2 rows, both from
  the retired ios feed, both already correctly dated, and none of the 8
  stored Auto Export bodies carries a `workouts` array. **Zero existing
  rows are mis-dated**, so no history was rewritten. A backfill belongs in
  `recompute_apple_daily.py` if it is ever needed.

**Tests** — `test_activity_feed.py` 14/14 (was 12/12). Two cases added per
CLAUDE.md rule 2, because nothing already there entered either branch:
every workout fixture used a `+0530` stamp, and no fixture day carried two
sources for one metric. Both were run against the unpatched file first and
both failed there. New `test_workout_day_ist.py` 12/12, with the old
slicing expression kept as a negative control. Whole FitLog gate green on
the server (10 suites) before the restart.

**Repo hygiene, same day**
- **The publish gate only half-worked.** `PUBLISH_HEALTH.bat` matched
  `.bak_` and `.bak-`, but the patchers in this repo write *five* spellings
  — `.bak`, `.bak.STAMP`, `.bak_STAMP`, `.bak-LABEL-STAMP` and
  `.bak-YYYY-MM-DD` — so a `.bak.` copy of `app.py` sailed straight through
  into a **public** repository. The pattern is now one `%BADPAT%` variable
  used by both the test and the failure listing (they had been two copies,
  which is how they drifted), reading
  `[.]db$ [.]db[.] [.]env$ [.]bak [.]deployed [.]tmp$ [.]token [.]secret ingest[.]env`.
  `[.]bak` catches every spelling; `[.]db[.]` catches `fitlog.db.bak_…`,
  which `\.db$` missed. Validated both ways: 15/15 backup- and
  secret-shaped names caught, **0 false positives across 216 publishable
  repo paths**. `.gitignore` gained `*.bak.*` and `*.tmp`.
- **Text-mode patchers silently rewrite line endings.** Run on Windows
  against an LF file they convert the whole file to CRLF: content
  identical, every test still green, but the repo copy stops being
  byte-identical to the server's — the invariant the sync rule rests on.
  `newline=""` on every read and write in `patch_workout_day_ist.py`,
  `patch_fitlog_ist_and_steps.py` and GutLog's
  `patch_doses_export_status.py`. Proved rather than asserted: each
  pre-patch LF file was pulled off the server, patched **on Windows**, and
  came back byte-identical to the live file (`96e6254d…` for FitLog,
  `e6e2bc85…` for GutLog), 0 CRLF pairs.
- **A stale duplicate is silent, so the publish now blocks on it.** New
  `tools/CHECK_FOLDER_PARITY.py`, wired into `PUBLISH_HEALTH.bat` on the
  main path after the secrets gate. `fitlog-ingest/` is the working folder;
  `fitlog/`, `gutlog/`, `rxguard/`, `ops/` are authoritative. A file in the
  working folder and in exactly **one** app folder must match byte-for-byte,
  line endings included. Directional on purpose: `app.py` exists in three
  app folders and legitimately differs, so a flat "same name must match"
  rule would have blocked every publish forever — an ambiguous home warns
  and carries on. 22 paired files checked and green. Tested four ways:
  identical passes, line-endings-only fails under its own heading, real
  content drift fails, ambiguous home warns.
- `gutlog/patch_doses_export_status.py` added to the repo — it had existed
  only in `D:\Downloads` and on the server. `CLAUDE.md` gains two lines
  naming `fitlog/` authoritative and `fitlog-ingest/` the working folder.

## 2026-09-13 — GutLog CSV kept the dose status (GutLog)

- `export_csv()` now emits `day,dtime,medicine,status,reason,effect,notes`.
  The status column had been dropped, so a SKIPPED dose read as a dose
  taken — the export said the opposite of the record. Applied and verified
  live on the server.

## 2026-09-11 — Phase G: reports read automatically (GutLog v3.10.0)

- Every scan or upload starts `records_worker.py` at once (cron every 15
  minutes as a safety net). Sarvam Document Intelligence (`sarvamai`, the
  clinic's existing key read in place from `/root/wa/.env`) extracts date,
  laboratory, type, every result row and the impression; the report is
  filed into Reports and Trends, printed test names matched to the record's
  own (Hb → Haemoglobin, ESR (Westergren) → ESR …), lab flags and
  out-of-range values flagged, plan items ticked, the inbox copy removed.
- Honesty kept: machine-read reports carry a "machine-read" mark and a
  "Looks right" button; Trends marks their values "auto"; a report whose
  printed patient name is not the owner's is filed as a document only with
  its values held back; unreadable files say why and retry up to 3 times.
- **Privacy decision (owner, 11-Sep-2026):** report files are sent to
  Sarvam for reading, as the clinic's bills already are.
- `patch_gutlog_v3100.py` (14 anchors), `test_phase_g.py` 12/12 (fake
  reader; nothing leaves the machine), UI test +3.

## 2026-09-11 — Phase F: the health record inside the system (GutLog v3.8.0 + v3.9.0, RxGuard v1.4.0)

- GutLog v3.9.0 — **the clinic scanner** (`scanner_widget.js` v2.3, the same
  file as the Asset Register and finance scan screens, vendored beside
  app.py) at `/scan`: type and report date, live camera, autocrop,
  flattening, multi-page PDF or batch. Scans land in Records → Reports as
  waiting. Every upload is fingerprinted (`files.sha`) and copied readably
  to `uploads/inbox/`; `import_records.py` can file a report named by its
  fingerprint and clears processed inbox copies. `patch_gutlog_v390.py`
  (9 anchors), `test_phase_f.py` 8/8, UI test drives a real scan end to end.


**Added**
- GutLog v3.8.0 — the Files tab becomes **Records**: Summary (medicines and
  vitals live, latest key results, problems, precautions, missing documents,
  Print/PDF), Reports (every report on one timeline, filter by kind, opens
  the original), Trends (every lab value exactly as printed with the lab's
  flag, a chart per test), Plan (investigations, marked done when in).
  Uploads wait under Reports as "to be processed". `/api/feed/profile`
  gives RxGuard condition codes only. `patch_gutlog_v380.py` (12 anchors),
  `import_records.py` (one-off/re-runnable import from a manifest kept
  outside the repo), `test_phase_e.py` 13/13, `test_ui_now.py` 60 checks.
- RxGuard v1.4.0 — five conditions (low platelets, low sodium, low ionic
  calcium, conduction disease, coronary disease) and six sourced condition
  rules CR010–CR015 (rules.json 1.1.0); a rule may name its drugs. kb_sync
  ticks conditions from GutLog's profile feed every 30 minutes (only its own
  codes are ever unticked; `--conditions` runs that step alone).
  `patch_rxguard_v140.py`, `test_conditions.py` 10/10.
- `gutlog/verify_phase_f.py` — live check, counts only.

**Rule kept**: the clinical content (`records_manifest.local.json`,
`records_profile.local.json`, the reports themselves) lives only on the
server and the owner's PC. The repository carries code and synthetic tests.

## 2026-09-11 — RxGuard v1.3.0: "Your review"

The Sources review page listed every property from every source with equal
weight. It now leads with what bears on the medicines taken now (interaction
cards, RED first, plain-language reasons), shows each new medicine as plain
chips, and moves reference-only lines to a footnote. One *Accept all
recommended* button. Reassuring kidney/liver wording is stored as reference,
not as a dose-review trigger. `patch_rxguard_v130.py`, `test_kb.py` 32/32.

## 2026-09-11 — RxGuard v1.2.2: draft quality after the first live run

First live sync: all five sources reachable; FDA table 244 rows, DDInter
191,709 pairs (12/14 files), PvPI 12 links; 4 drafts. Fixed from what it
showed: a combination ATC class chosen over the ingredient's own class;
label bullet lists quoted as one run-on sentence; drafts made before the
Salts page was filled carried no strength. Pending drafts rebuild on the
next run. `test_kb.py` 29/29.

## 2026-09-11 — RxGuard v1.2.1: source sync that cannot hang

The first live sync ran past its 10-minute limit without saving anything: a
socket timeout only bounds each wait for bytes, so a slow download never
ends. Now every download has a whole-transfer deadline; DDInter's 14 files
are fetched within a per-run budget and cached one by one; progress is saved
stage by stage; drafts built on a partial DDInter are rebuilt when it
completes. `kb_sync.py --diag` added. `test_kb.py` 28/28.

## 2026-09-11 — Phase D: free medicine sources + Activity card (GutLog v3.7.0, RxGuard v1.2.0, FitLog v1.2.0)

**Added**
- RxGuard v1.2.0 — *Sources review*. Every molecule GutLog shows as current and
  RxGuard does not know gets a draft from free, verifiable sources: NLM RxNorm
  (identity, brand → ingredient) and RxClass (ATC class), openFDA label (QT,
  sedation, serotonergic, bleeding, renal/hepatic, withdrawal — each with its
  quoted sentence), the FDA CYP/transporter table, and DDInter 2.0
  (interaction severity, CC BY-NC-SA, personal use). PvPI (India) alert index
  as links. Nothing enters the engine until the owner ticks it; the curated
  knowledge base always wins. Daily label re-check marks approved entries
  whose FDA label changed. `kb_sources.py`, `kb_sync.py` (cron every 30 min,
  `--report` for a terminal summary), `patch_rxguard_v120.py`,
  `test_kb.py` 25/25. Status feed `/api/feed/status` for GutLog.
- GutLog v3.7.0 — Meds → *Salts* (salt + strength per medicine, NLM spelling
  suggestions, a guess from the name, "Not a single drug"); Now-tab medicine
  status banner (needs a salt / waiting in RxGuard / RxGuard RED); *Activity*
  card (Walk, Treadmill, Cycling road/static, Meditation; minutes + talk-test
  intensity; Undo; Day by day + retime; watch data from FitLog merged, a
  matching watch workout confirms a tap). Stack feed carries strength;
  `/api/feed/activities`. `patch_gutlog_v370.py` (22 anchors),
  `test_phase_d.py` 18/18, `test_ui_now.py` 48 checks.
- FitLog v1.2.0 — watch mindful minutes kept; indoor workouts named
  "(indoor)"; `classify_workout()`; `/api/feed/activity` for GutLog; Home
  *Activity today* card. `patch_fitlog_v120.py` (2 files),
  `test_activity_feed.py` 12/12 (real GutLog + real FitLog on loopback).
- `gutlog/verify_phase_d.py` — live post-restart check, prints counts only.

**Rule kept**: outward calls happen only from the live database (every suite
is isolated); only molecule names ever leave the server; no paid or licensed
source is used.

## 2026-09-11 — Phase C across GutLog v3.6.0, RxGuard v1.1.0, FitLog v1.1.0

**Added**
- GutLog: Stock and refill (Meds → Stock; derived from events; pillbox fill vs
  per-dose; alerts + Now banner), Vitals log card, read-only feed
  (`/api/feed/stack`, `/api/feed/doses`) with a self-created mode-600 token.
  `patch_gutlog_v360.py`, `test_phase_c.py` 20/20.
- RxGuard: *As taken (GutLog)* — reconciliation of the typed list against what
  GutLog shows taken, and the engine run across the whole as-taken stack.
  `patch_rxguard_v110.py`, `test_astaken.py` 15/15.
- FitLog: W03 and the Meds page count GutLog doses (union, matched by
  molecule to the stack). `patch_fitlog_v110.py`, `test_gutlog_feed.py` 10/10.
- `gutlog/verify_phase_c.py` — live post-restart check, prints no names.

**Earlier the same day**: GutLog v3.4.1 (dose picker restored), v3.4.2 (pain-by-
site tiles), v3.5.0 (Phase B: retime, backfill, Day by day). See the dossier.

## 2026-09-10 — FitLog Phase 3.5b: HC Webhook + FITLOG_DB pinning

**Added**
- `fitlog/patch_hc_support.py` — HC Webhook support: URL-borne token for the
  healthconnect feed (`?k=`), scoped POST-only / healthconnect-only / no read;
  record-level ingest into a new `health_hc_records` table
- `fitlog/test_hc_ingest.py` — **36/36**. The HC path the base suite never
  touched, plus R1/R2 regressions and negative controls
- `fitlog/patch_db_pin.py` — `health_ingest.py` now reads `FITLOG_DB` from
  `ingest.env`. Precedence: environment > ingest.env > hardcoded default
- `fitlog/test_db_pin.py` — **12/12**. Runs against a non-default DB filename
  with a decoy `fitlog.db` that must stay empty

**Changed**
- `fitlog/deploy_phase35.py` — writes `FITLOG_DB` into `ingest.env` when the
  discovered database is not the default name, and refuses to migrate if the
  installed `health_ingest.py` is too old to read it

**Fixed**
- The `--db` split-brain reported in Phase 3.5. Root cause: nothing ever set
  `FITLOG_DB`, because systemd loads `.env` and not `ingest.env`. Migration and
  the running app could target different databases → `no such table:
  health_raw`, HTTP 500.
- Two data bugs in the first HC build, found in review before deploy:
  - **R1** the dedup key included the record's *value*, so a redelivered
    in-progress interval landed as a second row and `SUM` added both —
    inflating `steps`, a rule-bearing metric. Key is now `metric|start|end`
    with upsert: last-write-wins per interval.
  - **R2** distance used a magnitude heuristic (`>100` → metres), storing a
    50 m record as 50 km. Now always converts from metres unless the record
    declares km.
- An arg-parsing bug in the first HC build: flag *values* were not consumed,
  so `--db /root/fitlog/fitlog.db` made the live database the patch target.
  It crashed on read before writing anything; the DB was unharmed.

**Verification**
- Server suites: 23/23, 36/36, 12/12 on Python 3.9.25 — restart gated on all three
- Live probe through OLS: interval grown 600→900 resolved to **900, not 1500**
- URL token confirmed unable to read status, read daily, or post as applewatch
- Probe removed; all four ingest tables at 0; `PRAGMA integrity_check` ok

**Unchanged**
- `app.py` MD5 `fb8520e573bd732ca608d59402e9996c` — identical to Phase 3.5.
  Still exactly the 4 registration lines vs the pre-Phase-3.5 backup. No F-rule
  consumes ingested data; verdicts byte-identical.

**Note**
- Two patchers run inside the same second share a `.bak_<stamp>` name, so the
  second overwrites the first's backup. Harmless in practice (they ran minutes
  apart here, and the repo holds both states) but worth knowing before
  scripting them back-to-back.

## 2026-09-10 — FitLog Phase 3.5: wearable ingest

Apple Watch and Samsung Health data now land in FitLog. Deployed and verified
on `srv1746119`.

**Added**
- `fitlog/health_ingest.py` — Flask blueprint, three bearer-gated endpoints:
  `POST /api/ingest`, `GET /api/health/daily`, `GET /api/ingest/status`
- Tables `health_metrics`, `health_workouts`, `health_raw` (+ indices), via
  `fitlog/migrate_health_ingest.py` — idempotent, self-backing
- Rule **S01 Source Precedence** — `applewatch` > `healthconnect` > `manual`,
  per metric per date; never summed, never averaged
- `fitlog/test_health_ingest.py` — 23-check smoke suite, temp DB only
- `fitlog/patch_register_ingest.py` — anchor-verified blueprint registration
- `fitlog/DEPLOY_Phase3.5.md` — spec and phone-side device config

**Changed**
- `fitlog/app.py` — 4 lines added after the `Flask()` construction (import +
  `register_blueprint`). Diff against the pre-deploy backup is those 4 lines
  and nothing else.
- `.gitignore` — added `ingest.env` and `*.bak_*`. The existing `*.bak-*` used
  a hyphen and did not match the `app.py.bak_<stamp>` files the patchers write.

**Verification**
- On-server smoke: **23/23** on Python 3.9.25
- Through the public OLS proxy: unauthenticated `401`, authenticated `200`,
  end-to-end write stored 1 metric, S01 read-back returned it
- Probe rows deleted; all three ingest tables back to 0
- All 18 pre-existing routes still served; check-in page renders with its form;
  switcher bar intact

**Unchanged by design**
- No F-rule consumes ingested data. Verdicts are byte-identical before and
  after this deploy.

**Notes**
- `/api/ingest` is **bearer**-gated, not owner-key gated. FitLog's owner key is
  a per-route `@login_required` decorator, not a `before_request` hook, so no
  exemption patch was required.
- Rollback points: `app.py.bak_20260910_075535`,
  `fitlog.db.bak_20260910_075503`

**Known gap (not hit here, not fixed)**
- `deploy_phase35.py` — the one-command orchestrator — never passes the DB path
  it discovered to `health_ingest.py`, which keeps its own
  `/root/fitlog/fitlog.db` default. Its documented `--db <other>.db` recovery
  path therefore cannot work: migration lands in one file, writes go to
  another, `no such table: health_raw`, HTTP 500. It fails safe (the
  end-to-end check catches it and rolls back) but re-running with `--db` will
  loop. Moot for this deploy — the live DB *is* `fitlog.db`, and the manual
  step-by-step path was used instead. Fix would be to write `FITLOG_DB` into
  `/root/fitlog/.env`, which is the file systemd actually loads.

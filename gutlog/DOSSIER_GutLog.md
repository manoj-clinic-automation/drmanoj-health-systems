# GutLog — DOSSIER (v3.35.0)

Single source of truth. Update after every change.

## Identity
- Personal gut/health diary and quick-log surface — medication doses, symptom
  episodes, vitals, meals, labs, consults
- Runs on the personal VPS behind the OpenLiteSpeed reverse proxy, on its own
  subdomain and loopback port. Companion apps: RxGuard, FitLog.
- Repo: `drmanoj-health-systems/gutlog/`
- Single-file Flask app plus a SQLite database, both under the app directory.

> **Hostnames, ports, IPs and absolute paths are deliberately not in this file.**
> This repository is public. The concrete values live in
> `gutlog/INFRA_GutLog.local.md`, which is gitignored.

The app directory used to hold **two dead database files** from earlier
versions, plus a dead signing key. They were **archived on 2026-09-20** — moved,
not deleted, after proving no running worker held them open, no `GUTLOG_DB`
pointed at them, and no cron entry or script named them. Only the live database
and its key remain beside `app.py`. The archive location is in
`INFRA_GutLog.local.md`.

`app.py:19` still reads the `GUTLOG_DB` environment variable, defaulting to the
current database under the app directory. **Confirm which file is live before
pointing any script at "the database"** — that is the GutLog half of the
CLAUDE.md §4 lesson, and it stays written down even though the confusing files
are gone, because the lesson is about the habit, not the files.

## Architecture
- Single-file Flask (`app.py`, ~3,900 lines incl. embedded HTML/CSS/JS) + SQLite
- `pwa.py` — manifest, service worker, icons. Installable to the phone home screen.
- Patchers are the only way `app.py` changes: anchor-verified, compile-checked,
  `.bak` before write, idempotent, self-restoring on post-write compile failure.
  Never re-upload the whole file.

## Auth
Login password + separate owner key set at `/setup` (first run). Hashes in the
`settings` table. Session keys: `session["ok"]`, `session["ep"]` — the latter
carries `auth_epoch()`, so changing credentials invalidates live sessions.

This is the pattern FitLog and RxGuard were built from.

### One sign-in across the three apps — HEALTH_SSO_V1 (2026-09-20)

Moving from GutLog into RxGuard or FitLog used to mean signing in again. Now a
plain page load (GET, `Accept: text/html`) with **no session** goes round the
ring gutlog → rxguard → fitlog → gutlog via `/sso/vouch`. The first app that
already has a session mints a ticket and the browser lands on the asked-for
app's `/sso/in`, which signs it in exactly as its own password would and then
continues to the page that was asked for. Nobody signed in anywhere → that
app's own login, in at most three redirects.

The ticket is **HMAC-SHA256 over {iss, aud, exp, nonce}** with a key only the
server holds. It is bound to **one** receiving app, lives **60 seconds**, and
is usable **once** — the nonce is recorded in the receiving app's own database
(`sso_used`). It rides in a URL for a single redirect, over HTTPS.

Four things it deliberately never does:

- **Never bounce an API or XHR call.** Only a page load goes round the ring; an
  API call gets the same login redirect it always did. Bouncing a background
  fetch would turn one failed request into three.
- **Never follow a `next` that is not a plain path** on the receiving app, and
  never send the browser anywhere but the three configured origins. An unknown
  target is a 404.
- **Never override Lock.** Logout sets a hold: a locked app stays locked until
  its *own* password is typed, even while the other two are open. Lock means
  lock.
- **Never work without the key.** No `/root/health-sso.key` (mode 600, root)
  and every app behaves exactly as it did before — which is both the default
  and the off switch. That promise is asserted, and was re-verified live on the
  server with the key absent before it was created.

An SSO ticket is only as good as the session it turns into, so each app's own
session secret must be set and at least 32 characters before SSO is turned on.
FitLog's fell back to a value derived from its **database path** — guessable —
so a FitLog without `FITLOG_SECRET` vouches for no one.

Passwords, owner keys, auth epochs, API behaviour and feed tokens are all
unchanged. The module is `health_sso.py` beside `app.py`; the patch is
`ops/patch_sso.py --app gutlog` (5 anchors here), the suite `ops/test_sso.py`.

## Quick-log surface (the Now tab)

The reason the app exists in this shape: logging a dose at 5am must be one tap,
not a form. Everything below serves that.

- **Blood pressure** — always open, top of the tab. Three fields, one Save.
- **Today's doses** — computed from `med_schedule` for the current day, grouped
  by slot (MORNING / NOON / EVENING / NIGHT). One tap = TAKEN. Tapping a logged
  row opens an action strip (Undo / Change dose / Skip) rather than toggling,
  so a stray second tap cannot silently delete a medication record.
- **Extra dose** — chips for unscheduled medicines, one tap logs at current time.
  Each logged extra carries a real Undo button.
- **Symptom now** — multi-select symptom types, shared severity and Bristol.
  Below the severity row, **Pain by site** tiles (Left iliac pain,
  Hypogastrium pain — `PAIN_SITES` in the page script). Tapping a tile opens
  its own 1–10 score; each site saves as its own episode with its own score.
  A selected site with no score is refused, never saved blank.

- **Retime from the strip** (v3.5.0) — the action strip on a logged dose
  carries its logged time, editable, with *Save time*.

Doses, extras and symptoms are collapsible; each header carries its own summary
(`3 of 8 taken`, `2 logged today`) so state is readable without expanding
anything. Blood pressure does not collapse.

## Watch (Now tab) — v3.13.0

The data was all arriving and was being shown as a single line. This card is
a *reading* change: it ingests nothing, adds no table and adds no feed token.
It reads FitLog's new `/api/feed/watch` (bearer-gated on the existing
read-only token) and joins it to GutLog's own pain rows and operating days —
the join is the point, because load and pain were in different applications.

- **Today strip** — steps, exercise minutes, **standing load in hours**,
  resting HR, HRV. Each carries a direction against **his own trailing median
  over the window**, never a generic goal: a ring target a 58-year-old with a
  replaced hip cannot meet is noise, and noise teaches you to ignore the
  screen. Fewer than three comparable days gives *no* direction rather than a
  flat one — "not enough to say" and "level" are different statements.
- **Cumulative or settled — declared in one place, `WATCH_KIND` (v3.15.0).**
  A part-day total measured against whole-day medians is not a comparison: it
  points down every morning by construction, which is how *"62 steps, ↓ vs
  1,214 median"* reached the screen at 07:53.
  - **cumulative** (steps, exercise minutes, standing load) — the headline is
    the **last complete day**, with its arrow and a median that excludes it.
    Today's running figure sits underneath, labelled *so far today*, with **no
    arrow**: there is nothing valid for it to be compared against.
  - **settled** (resting HR, HRV) — a reading rather than a total, so today's
    stands the moment it exists and falls back one day when it does not.
  The distinction is in the shape of the quantity. **No time-of-day threshold
  anywhere**, and `test_phase_j` case 03i fails if one appears.
- **Every figure names its day.** Signalling "today" by the *absence* of a
  label is what made `62 steps · 19 min · yesterday` read as though both were
  yesterday's. Each tile carries its own day; the header names the day of
  every figure it shows.
- **Second person, with a gate.** `On his legs` reached the screen from a
  brief written *about* him rather than *to* him. Every rendered string is now
  second person and `test_phase_j` case 03k fails on a third-person pronoun in
  any string the card renders — the briefs will keep that voice, so the guard
  belongs in the code rather than in a habit.
- **It falls back one day (v3.14.0).** A metric with no figure for today shows
  **yesterday's, labelled "yesterday"**. He opens this between 5 and 7am and
  the phone syncs later, so the first real use found all five tiles reading
  "no data" at 07:30 — correct, and useless at exactly the hour he looks.
  "No data" is now reserved for the case where neither day has a figure.
  The direction compares the **shown** day against the median with that day
  **excluded**: leave it in and the figure is compared against a median it is
  itself inside, which on a fallback day reads "level" every time.
  Each tile also carries **n**, the number of days behind the median, so a
  thin baseline is visible as thin. That is deliberately *not* a plausibility
  threshold — as of 14-Sep the steps median sits near 1,200 because two days
  from before the source fix are still in the window; it self-corrects as the
  window moves, and a heuristic written for it would outlive it.
  *Consequence worth knowing:* the fallback triggers on absence, and steps is
  the first metric to arrive each morning, so the strip is often **mixed** —
  a small genuine figure for steps today beside yesterday's HR and HRV. Every
  tile says which day it is showing, so this is legible rather than wrong, but
  it does mean the early-morning steps tile can read very low.
- **Fourteen-day row** — one bar per day, steps; under it two thin lanes,
  one marking every day with a logged pain entry, one marking every operating
  day.
- **Workouts** in real IST, taken from the feed's own `start_hm`. Nothing on
  this screen slices a timestamp; that was the 2026-09-13 bug and
  `test_phase_j.py` case 12 fails if a slice reappears.
- **Epoch band** — `med_epochs` drawn across the same row, because resting HR
  and HRV inside a drug change are artefacts of the change. The label is
  *data*: it lives in the FitLog database on the server and appears nowhere in
  this repository.
- **Source honesty** — steps arrive from two feeds and the larger wins, so the
  day's figure says quietly which feed answered. A day with no data reads
  **"no data"**, never zero: not worn and worn-while-resting are different
  facts and a chart that conflates them lies about a rest day.

**Deliberately absent:** readiness, recovery, body battery, fitness age, or
any other number pretending to summarise a body. Every figure here is an input
to a judgement, never a judgement, and one quiet footnote says so.
`test_phase_j.py` case 04 fails if any such word reaches the payload or the
page. **Standing load is never added to exercise** — not on the strip, not in
the row, and not into the median.

**The footnote stopped arguing from a stale clinical premise (v3.18.0).** It
used to read *"Heart-rate and HRV figures from a wrist sensor depend on the
underlying rhythm for their accuracy, so no readiness, recovery or fitness
score is derived from them here."* The second clause was an inference about
the wearer's own rhythm, carried forward from an old investigation; a current
one says otherwise, so the sentence was false as written and had been restated
every time the card was read. The reason for showing inputs rather than scores
never rested on it, so the claim is gone and the reason stands on its own —
*"Shown as inputs, not conclusions. No readiness, recovery or fitness score is
derived from them."* The clinical basis for the correction is in the health
record on the server and is deliberately not restated here (CLAUDE.md rule
5d). FitLog's separate note about HR during a medication titration is a
different claim, still true, and untouched.

A note on the class of defect, because it is the reusable part: the wrong
sentence was not a bug in code, it was a **premise written into a string years
before and never re-checked**. Nothing in the test suite could have caught it —
every assertion about that footnote asserted that it was present and unchanged.
`test_watch_tiles.py` now asserts the footnote's exact wording, which at least
means the next change to it has to be deliberate.

### Tiles that hold nothing are not drawn — v3.18.0

The live answer on 2026-09-15: `load_hours` came back `n: 0` with every field
null, and `resting_hr` and `hrv_ms` came back with a six-day median but
`value: null`, `day: ""`, `source: ""`. Three tiles drew a label with nothing
under it, and the two that had a median threw it away.

- **`/api/watch` withholds a tile with no figure, no median and nothing
  running today.** The decision is made once, on the server, so the card can
  go on drawing whatever it is handed.
- **The card refuses to draw one anyway** (`wkHas`). Two guards on purpose:
  this is the last line before the screen, and an answer the service worker
  cached from an older build would otherwise put the empty label straight back
  in front of him with nothing in the server to stop it.
- **A tile that has only a median shows the median**, muted, with the line
  beneath reading *"6-day median, no reading today"* and **no day label** — the
  figure belongs to a window, not to a date, and a date on it would read as
  today's reading. `"no data"` is now reserved for a tile that has a running
  figure today and nothing settled behind it.

Server-side: `test_watch_tiles.py` **7/7**, negative control
`new_assertions_v3180.json` **PASS (7/7 seen to fail)**. Browser side:
`test_ui_now.py`, negative control `new_assertions_v3180_ui.json`. The two
that could not fail on a version change are mutation-controlled: widening the
withholding test from AND to OR (does it throw away tiles that *do* hold
something?) and typing the new median figure at 12px (does the card's 14px
floor actually measure the new element?).

If FitLog is unreachable the card says so and still draws GutLog's own pain
and operating days.

### Readability (v3.15.0) — measured, not aesthetic

The root cause was arithmetic, not taste: `#wkStrip` was five 116px tiles plus
8px gaps — **612px inside a 368px strip** — so it overflowed, and every other
complaint was downstream of that. Now one full-width **hero** (steps) over a
two-column grid, which gives each cell ~176px and **cannot** scroll sideways.

- **Nothing below 14px.** Label 14px/600 sentence case, hero value 32px/700,
  tile value 24px/700, comparison 15px in ink, provenance 14px muted **on its
  own line**, footnote 14px, header 15px. Every figure uses
  `font-variant-numeric: tabular-nums` so digits stop jittering on refresh.
  *Deviation, flagged:* the brief asked for a 13px label and provenance and
  also for a test asserting nothing below 14px. Those contradict; 14px wins,
  because the test is explicit and the stated reason is that small text is
  hard to read.
- **The four-fact meta string is split.** `↓ vs 1,214 median of 10 d ·
  healthconnect` was four facts in one 11.5px string in a 116px column. Now
  line 1 `↑ 4,902 · typical 1,214`, line 2 `10 days · Apple Watch`. Sources are
  named in words — a slug on a screen is a note to whoever wrote it.
- **Tiles have a surface**: `--card`, 1px `--line`, 12px radius, 14px padding,
  10px gap. They had none, so they did not read as objects.
- **Chart**: 64px → **104px**, 2px between adjacent bars, 4px rounded
  data-ends square to a recessive baseline, and the no-data hatch kept but
  made full-height so it cannot be mistaken for a short bar. **Tap-to-reveal**
  per bar, because `title=` does nothing on a phone; the tapped day prints
  under the chart and the bar takes an outline.
- **Lane markers are 10px and carry a shape as well as a colour** — pain is a
  disc, an operating day is a diamond — so neither is colour-alone. Direction
  is likewise never colour-alone: the arrow glyph carries it and the text
  stays ink.
- **Colour was computed, not eyeballed.** The marker set (pain / operating day
  / epoch) passes every check of the data-viz validator on the card surface:
  lightness band, chroma floor, CVD separation (worst all-pairs ΔE **15.6**
  deutan), normal-vision floor **19.1**, contrast ≥ 3:1. The palette is
  otherwise untouched, as measured. `--amber` stays a band fill and never
  becomes text under 18px.

### Dark mode is Phase L, and here is why

Not shipped, deliberately, and not as an automatic flip. Flipping these marks
onto a dark ground **fails** the validator: two of three fall outside the dark
lightness band and three drop to 2.4–2.9:1 against it. A real dark variant
needs its own steps chosen against the dark surface — and because this card
shares one `:root` palette with every other screen in the app, it also needs
new ink, muted, line and card tokens and a pass over every existing component.
That is an app-wide change with its own validation, not a corner of a card.

## Medicines banner (Now tab) — v3.18.0

`#nowMedStatus`, fed by `/api/medstatus`: medicines still needing a salt, plus
whatever RxGuard's `/api/feed/status` reports — drafts and interaction pairs
waiting for review, and its RED count.

It used to paint itself **amber for housekeeping every single morning** (a
salt to fill in, a draft to review) and **red for a RxGuard count that until
RxGuard v1.7.0 included burdens built out of medicines that had not been
taken**. From v3.18.0 red means RxGuard has a RED it can stand behind;
everything else is neutral (`.stockalert.info`, themed tokens, no extra dark
rules needed). This banner is the only cue he sees daily, so it should earn
attention rather than spend it — the same argument as the ring targets the
Watch card refuses to draw.

The count itself is RxGuard's to get right, and RxGuard v1.7.0 is where that
was fixed; this end only stops shouting about it.

Browser assertions in `test_ui_now.py` (`/api/medstatus` stubbed, service
workers blocked): with no RED the banner is neutral and says nothing about
RED; with a RED it is an alert and names it. The second is mutation-controlled
— it passes on v3.17.0 too, so the banner is made neutral-even-with-a-RED on
purpose and the assertion has been seen catching it.

## Pain now (Now tab) — v3.12.0

A collapsed card under *Symptom now*, beside blood pressure and today's doses.
Nine tiles, the commonest first and larger: **both hips + anterior thighs**,
then hip/thigh R and L, glutes both / R / L, low back, neck → R arm, neck → L
arm. Tapping a tile expands it — the same pattern as the gut pain-by-site tiles
— and asks **three things and no more**:

1. a score, 0–10;
2. treatment chips, multi-select — four physical measures plus the analgesic
   chips from `regimen.local.json`;
3. hip and glute tiles only: one optional tap, *goes below the knee*.

**Sides are never averaged.** Right is the THR side (2010), left is the native
arthritic hip, so R, L and both are separate tiles writing separate rows.

Each tap writes **one row to `episodes`** — the table that already holds every
within-day event. `category='pain'`, `etype` = the site slug, `side`,
`severity` = the score, `treatments` pipe-joined (the `days.syms` convention),
`radiates` 0/1. **No second pain table.**

### The analgesic chips must not create a parallel medicine record

Tapping an analgesic writes a **real `doses` row** — `status='EXTRA'`,
`reason` = the pain site, `med_id` resolved from `prnmeds` by molecule then by
name — exactly as an ad-hoc dose does, **and** posts to FitLog
`/api/analgesic` so the same event reaches `analgesic_log` with
`pain_at_time` = the score just entered. That score is the whole reason for
the push: the dose feed can tell FitLog a drug was taken, never how bad it was
when he took it. A medicine logged in two places is a record that disagrees
with itself.

If GutLog carries no such medicine the dose is still recorded under its chip
label with `med_id` NULL. If FitLog is unreachable or has no matching stack
row, the local dose row stands and the answer names what was not mirrored.

The chip labels are medicine names and this repository is public, so they are
read at import from `regimen.local.json` → `pain_analgesics` (`[label,
molecule]` pairs) by `_local_seed`, like `prn_seed`. A clone without that file
gets the four physical measures and no drug chips. The page never carries them
either: `/api/pain` GET serves the sites and the chips, and the JS builds the
tiles from that answer.

### "eased" — so duration is real

Nothing is asked at the moment of pain: `duration` is written empty. A logged
pain row carries an **eased** tap — on the Pain card for today's rows, and in
the day-view row-action strip — which stamps `duration` from `etime` to now.
One tap, no dialog. Under an hour reads `47 min`; over, `2 h 05 min`.

## The operating day — v3.12.0

`ACT_KINDS` gains `ot_day` ("Operating day") with an **hours** picker
(2·4·6·8·10 h), stored as minutes like every other activity. The same
hip/glute/thigh complex appears on long operating days, so the hours on his
legs must be recorded or every walking-versus-pain comparison is confounded
by his work.

It is **load, not training**, and `LOAD_KINDS` keeps it out of exercise
minutes everywhere: `/api/activity` reports `minutes` (exercise) and
`load_minutes` separately, the Activity card shows it apart, and the day view
tags it `Load` in hours. It reaches FitLog through `/api/feed/activities`
with no new write path; FitLog renders it under *Standing load*.

## Day by day (Review tab, first card) — v3.5.0

One day at a time: every dose, extra, skip, symptom, BP reading and meal,
in time order, tagged by kind. Tap any entry to move it to the right time or
day, or delete it. Below the list, that day's scheduled doses that were never
logged, each with a time box (slot time by default; now, if the slot time is
still ahead today) and Taken / Skipped — variant medicines show their chips.

Server guards (`/api/retime`, and `/api/now/dose` when given a day/time):
no future day, no time later than now for today, HH:MM only. A scheduled
dose can move only to a day its regimen line covered, and never onto a day
where it is already logged (409). Extras move to any past day.

**Every retime is recorded** in `edits` (old/new day and time, when). The
day view marks such entries *time edited*. A diary time that changed
silently cannot be trusted later; one that changed visibly can.

## Meals and snacks — v3.35.0 (`GUTLOG_V3350_SNACKS`, 2026-09-24)

**Why.** His day is not three meals. A late snack after dinner was filed as
dinner (23-Sep: a 21:55 entry went in as a second Dinner), random daytime
snacks were not logged at all, the food list was one long run of chips, and a
combination sabzi could not be logged as he cooks it. The aim is to uncover
the snacking and correct it — with counts and his own marks, never a score.

**What it does**
- **One slot list**, `MEAL_SLOTS` = Breakfast · Mid-morning · Lunch · Evening
  · Dinner · Late snack, plus *Eating out* kept as a choice
  (`MEAL_SLOT_EXTRA`). Written into the page before render (`__MEAL_CFG__`,
  no Jinja token), used by the Meals tab slot chips and the Now card's *Other
  meal*. Old rows keep their slot ("Snack" still shows and totals). **His meal
  cards** (`meals.local.json`: Morning, Breakfast, Before lunch, Lunch, Evening
  tea, Dinner) still file under their own names — renaming a card there is
  his call.
- **Slot guess** (`meal_slot_guess`, `/api/meals/slotguess`): after the
  day's last Dinner → **Late snack**, never added onto dinner; otherwise
  <10:00 Breakfast, 10–12 Mid-morning, 12–15:30 Lunch, 15:30–18:00 Evening,
  18:00+ Dinner. A save with no slot uses it; the Now card opens on Late snack
  after Dinner instead of the Dinner card; one tap changes it.
- **Day totals** say Late snack and Quick bites on their own lines
  (`nut_snacks`, `snack_text`) on the Meals tab, `/nutrition` and Day by day
  (a Quick bite shows *why* under its time). They stay inside the totals.
- **Arranged picker** (`/api/foods/picker`): chips ★ Mine · Dal · Sabzi ·
  Protein · Grain · Fruit · Dairy · Nuts & seeds · Sweets · Snacks. Groups are
  a **mapping**, never a rename: a `group:X` tag (set when he adds a food) →
  name words (`FOOD_GROUP_WORDS`: paneer/egg/fish → Protein, nuts/makhana →
  Nuts & seeds, laddu/chikki → Sweets, curd/milk → Dairy) → `library.cat`
  (`FOOD_GROUP_BY_CAT`: A Grain, B Dal, C Protein, D Dairy, E Sabzi, F Snacks,
  G Fruit, H Snacks). Within a group: favourites A–Z, then recently used
  (latest first, 60 days), then A–Z. Search spans every group. Same picker in
  the Meals tab and the Now card's "+ Something else"; the Now card keeps its
  quick estimated add too.
- **A new food on the spot** from the not-found state: name, group, dry or
  cooked weight, portion (1 katori ≈ 150 g, 1 piece, or grams), FODMAP, and
  values per 100 g filled from the bundled table when he taps a match
  (editable). Saved through `/api/library` with the v3.31.0 fields; tagged
  `own entry` with a dated note; source `USDA` when the values are the
  table's, `own` otherwise.
- **Dishes** (`dishes` table: name, `katori_g` default 150 g, variants with
  parts as shares of the cooked katori; editable, shares must total 100).
  Worked out from each component's per-100 values in his food list; a
  variant with a missing component says so rather than half-compute. Logged
  as ½ / 1 / 1½ katori or grams, from either picker. **Defaults:** Tinda
  (plain, or 70 % tinda + 30 % paneer); Parwal (plain, or 60 % parwal + 40 %
  aloo); Lauki (plain, or 70 % lauki + 30 % paneer); Aloo; Arbi; Bhindi;
  Beans; Carrot-beans (50/50); Torai; Kaddu; Palak. Components are his library
  entries (Tinda, Parval, Lauki, Potato, Paneer 50 g, ...), so their values are
  the ones already there (mostly estimated per katori). Live per katori: Tinda
  65 kcal, Tinda + paneer 163, Parwal + aloo 83, Lauki + paneer 156.
- **Late-snack buttons** under Late snack, one tap each (`/api/snacks/late`,
  an ordinary meal row, editable after): warm skimmed milk 200 ml, orange 1
  small, kiwi 1, guava ½, roasted makhana 15 g, peanuts 10 g, almonds 5, curd
  100 g, paneer 30 g, cucumber 1, carrot 1 — by weight where the food has one,
  otherwise by portion (live: orange 0.75, kiwi 1, almonds 1 portion). Note
  under them: "Before 21:00, at least an hour before the night tablet."
- **Quick Bite**: a small button at the top of the Now page, one sheet —
  what (biscuit, namkeen, chikki, laddu, fruit → Fruit group, nuts → Nuts &
  seeds, tea with something, other → full picker; several allowed), how much
  (a little / normal / a lot = 0.5 / 1 / 1.5), why (hungry · bored · stressed
  · tired · offered · craving · habit), time default now. One meal row, slot
  *Quick bite*, `meals.reason`.
- **Weekly snack review** card on the Meals tab (`/api/snacks/review`):
  Monday–Sunday ending the most recent Sunday (on a Sunday, that week), or the
  last 7 days. Counts of Quick bites and Late snacks; the busiest 90-minute
  band starting on a half hour ("4 of 6 between 16:00 and 17:30"); reasons;
  top items with kcal, protein and share of the week's kcal (sugar is not in
  the bundled table and is said so); for anything eaten twice or more a swap
  idea from `snack_swaps.json` (tracked, foods only) and his **Keep / Swap /
  Stop** mark (`snack_marks`, per week). The next week lists each Swap/Stop
  item's count before → after. No scoring, no judgement words.
- **Nudge**: during a band that held **≥ 3 Quick bites in the seven days
  before today**, one line on the Now page — "Usual snack time — planned swap:
  …" (a Swap-marked item's idea first). Dismissible for the day
  (`snack_nudge_off`).
- **Food Test results** list each day's Late snacks and Quick bites on their
  own lines.
- **Health Mirror**: `meal_rows.csv` (day, time, slot, reason, kcal, protein,
  fibre, items) and `snack_marks.csv`, and both in the snapshot; columns read
  only if present.

Also fixed on the way: editing a past meal opened the Now card folded (a
v3.34.0 side effect); it now opens it.

Schema 3.3.6 → **3.3.7** (`meals.reason` through `_migrate()` and `SCHEMA`;
`dishes` and `snack_marks` in `SCHEMA`). **Seed**
`migrate_gutlog_v3350_snacks.py` (dry run by default, `sqlite3.backup()`,
calls the app's own `snacks_seed()`, never changes an existing food or dish):
live on 24-Sep it added *Skimmed milk (warm)* from the table, *Namkeen (30 g)*
and *Chikki (1 piece)* as estimated, and the 11 dishes; a second run found
nothing to add. Every button and every dish variant resolves on his food list.

**Suites changed on purpose.** `test_v3330_painsite` 01 now accepts schema
3.3.6 *or later*.

**Evidence.** `test_v3350_snacks.py` **15/15** (11 API incl. the review on
fixed synthetic weeks with the server clock set, and the mirror; 4 in
Chromium at 300 px). Negative control **33/33 seen to fail**: all 15 against
the reconstructed v3.34.0 and 18 mutations, including the four the brief
names — `latemerged`, `biteouttotals`, `wrongweek`, `nudge2`. It caught one
weak check first: the new-food group test used a category that already mapped
to the chosen group, so ignoring his pick changed nothing; it now uses Sweets,
which only his pick can express. All 30 server suites, `ops/test_sso.py`
11/11 and the mirror's 14/14 green before the restart; offline `test_ui_now`
and `test_ui_order` ALL PASS. Deployed 10:39 IST, `app.py` sha256
`4580ad23…`, byte-identical to the repo; `--reverse` reproduces v3.34.0
byte-for-byte (`80f87187…`). Schema read 3.3.7 after one GET /login.
Rollback `app.py.bak-v3350-20260924_103818`, DB
`health3.db.pre-v3350-20260924_103818`, seed backup
`health3-pre-v3350-seed-20260924_103914.db`, mirror
`/root/ops/health_mirror.py.bak-v3350-20260924_103756`.

## Week 0 from Meals; the Now page folds — v3.34.0 (`GUTLOG_V3340_FTMEALS`, 2026-09-24)

**Why.** On 24-Sep the Food Test card asked for dinner time, size and cramp,
and the dinner was already in Meals. He would not enter it twice — rightly —
and since a step advances only when logged, Week 0 could never finish and
the Week 1 ladder due on 30-Sep would never start.

**What it does**
- **A meal logged as Dinner completes that day's Week 0 step by itself** —
  from the Meals tab, the Now meal card, *log again*, or any edit. One
  function, `ft_dinner_sync(day…)`, is called by every path that writes,
  edits, re-times, moves or deletes a meal (`/api/meals`, `_log_meal`, meal
  delete, *again*, `/api/delete/meals`, `/api/retime` for meals, and the
  Food Test retime of a dinner step). It writes one `ft_log` row with
  `meal_id` set and `ltime` = the day's earliest Dinner. A split dinner (two
  meals logged as Dinner) is one dinner. **No meal row is ever created by the
  Food Test**, and `/api/ft/log` now refuses a Week 0 dinner (409): there is
  no second dinner entry anywhere.
- **Size, by a stated rule (`FT_SIZE_RULE`)**: that day's Dinner kcal against
  the **median** of his Dinner days (days with kcal) in the 28 days before —
  below 75 % *small*, above 125 % *large*, otherwise *usual*; and *usual*
  whenever the day has no kcal or fewer than three earlier Dinner days carry
  kcal. Nothing is guessed.
- **Follow-through**: editing, re-timing or moving the Dinner re-syncs the
  day(s) touched — the step takes the new time, moves with it, or goes when
  no Dinner is left (a Dinner edited into a Lunch included). Steps are
  renumbered in day order, so a hole never shifts the plan. Once a later
  week has a logged step, Week 0 is closed: a deleted Dinner then only
  unlinks its row, so a finished Week 0 is never re-opened. **Undo** refuses
  a day that comes from Meals and says to edit the dinner there.
- **Which days count**: on or after `ft_week0_from` (a setting; if absent,
  the day the plan was seeded, then kept so a re-seed does not move it —
  23-Sep-2026 live), while Week 0 is unfinished and the test is not paused.
- **The card asks only "Cramp after dinner? yes / no"** — optional; tap the
  chosen answer again to leave it blank; blank still counts the day
  (`/api/ft/cramp`, `ft_log.cramp` NULL = not noted, shown as *cramp not
  noted* on `/foodtest`). Before the Dinner is logged the card says "Log
  dinner in Meals — this day counts by itself" with an *Open Meals* button.
- **Now page order**: medicines (doses, extra dose, stock/order/status
  lines), meals (+ plan), symptoms (Symptom now, Pain now), the Food Test,
  then BP, activity, day context, down day, watch. Nothing removed.
- **Meals card and Food Test card start folded** (the `bindFolds()` card
  pattern). Headers: "Breakfast ✓ · Lunch ✓ · Dinner —" (+N for other
  slots; protein moved inside), and today's step only — "Week 0 · day 3 —
  dinner logged ✓", "Week 1 · Kala chana 25 g dry" (`ft_state().header`,
  worked out on the server). A Dinner logged on the Now card refreshes the
  Food Test card at once.
- **Meds → PRN → Add medicine** goes on to Meds → Salts with "Added. Now give
  its salt and strength.", as the Now tab's Add medicine already did.

No schema change (3.3.6). **Backfill**: `migrate_gutlog_v3340_week0.py`
(dry run by default; `sqlite3.backup()` + integrity check before writing;
calls the app's own `ft_dinner_sync()`; idempotent). Live on 24-Sep 08:24
IST: **23-Sep** linked (earliest Dinner 18:39, 645 kcal over two Dinner
meals against a 538 kcal median → *usual*); 24-Sep had no Dinner yet and
stayed unlogged. A second `--apply` added 0. The card then read **"Week 0 ·
day 2 — dinner not logged yet"**. With a Dinner logged each day to 29-Sep,
30-Sep opens on the Week 1 12 g dry dose (proved with synthetic dates, both
clocks set to 30-Sep).

**Suites changed on purpose.** `test_v3320_foodtest` logs Week 0 through
Dinners in Meals (the card no longer takes a dinner), opens the folded card
before reading it, and expects *Log dinner in Meals* where it expected *Save
dinner*. `test_meal_cards`, `test_v3310_foodlib` and `test_v3330_painsite`
open the folded card first (guarded on `.fold`, so they still pass against
v3.33.0).

**Evidence.** `test_v3340_ftmeals.py` **10/10** (6 API incl. the backfill on
23..29-Sep with the server clock at 30-Sep; 4 in Chromium at 300 px incl. the
30-Sep card with the browser clock set too). Negative control **33/33 seen to
fail**: all 10 against the reconstructed v3.33.0, and 23 mutations including
the three the brief names — `twomeals` (a second meal row), `ignoretab` /
`ignorecard` (the dinner ignored), `ftattop` (the Food Test card left at the
top) — plus `drywrites` on the backfill script. It caught two weak checks of
mine first: the card-path check was masked by a later Meals-tab write, and the
plan's first-day rule was never reached by the backfill test. All 29 server
suites, FitLog's `test_phase_j` 28/28, `ops/test_sso.py` 11/11 and the
mirror's 14/14 green before the restart; offline `test_ui_now` 196 PASS / 0
FAIL, `test_ui_order` ALL PASS. Deployed 08:23 IST, `app.py` sha256
`80f87187…`, byte-identical to the repo build; `--reverse` reproduces v3.33.0
byte-for-byte (`28b50914…`). Rollback `app.py.bak-v3340-20260924_082218`, DB
`health3.db.pre-v3340-20260924_082218`, backfill backup
`health3-pre-v3340-week0-20260924_082403.db`.

## Pain site, onset time, one true-time day — v3.33.0 (`GUTLOG_V3330_PAINSITE`, 2026-09-23)

Now → *Symptom now* at `https://health.dr-manoj.in/`, and
`https://health.dr-manoj.in/foodtest`.

**Why.** On 23-Sep a left-upper pain began before dinner and the app could
record neither fact: the Symptom card had two pain tiles, and every episode
was stamped with the moment Save was pressed. The question for his
gastroenterologist is whether episodes start before or after meals.

**What it does**
- **Nine regions as a 3 × 3 grid**, as seen facing him ("your right | your
  left"), plus a full-width *Diffuse / whole abdomen*. One server constant,
  `ABD_SITES`, written into the page at render time (a placeholder replaced
  before `render_template_string`, so no Jinja token). The two v3.4.2 labels
  are kept **exactly** (`Left iliac pain`, `Hypogastrium pain`), so history
  stays continuous. Tap a region: its 1–10 row opens **below** the grid; tap
  again: cleared; one episode per region, as before.
- **Started at**: a time box (the two lists), default now, with Now / 15 min
  / 30 min / 1 h ago. Saved as the episode's `etime`; the save moment is
  already in `created`. No new episode column.
- **Before / after a meal, computed**: `meal_relation()` finds the nearest
  meal that day within 4 h — "20 min before dinner", "1 h 10 min after
  dinner", "no meal logged within 4 h". In Day by day under every GI
  episode (categories GI and Gut) and under each Food Test result day. The
  Food Test page adds **Onset vs meals** for the current week (its first
  logged step to today): before a meal / after (0–3 h) / unrelated. Counts
  only.
- **Evening score**: with pain > 0 the same grid (several, no per-site
  score) and an optional *Started at*; both optional, the "all four" rule
  unchanged, and re-opening the score shows them. `ft_score.pain_sites`
  (pipe-joined labels, grid order) and `ft_score.onset`, through
  `_migrate()` **and** `SCHEMA`; schema 3.3.5 → **3.3.6**.
- **Day by day is one timeline**: Food Test steps at their time (*Food
  test*), scores at theirs (*Score*), and a separate *Pain onset* entry at
  the onset with its regions — among meals, doses and symptoms in true time.
  All retime from the same box: `_TIME_COL` gains `ft_log`, `ft_score` and
  `ft_score_onset`. The Food Test two go through `ft_retime_core()` — the
  body of `/api/ft/retime`, now shared, so the one-per-day rules and the
  dose's meal moving with it cannot differ; the onset row writes
  `ft_score.onset` only and stays on its score's day. Every move lands in
  `edits`.
- **Health Mirror**: it had no Food Test section at all; it now has one —
  scores with *Where* and *Started*, and the steps — plus
  `food_test_scores.csv` (with `pain_sites`, `onset`) and
  `food_test_steps.csv`. Columns are read only if present, so it still runs
  against an older database.

Nothing backfilled: 17 episodes and every past score untouched.

**Suites changed on purpose.** `test_ui_now` drives the grid, and its
"if the tiles exist" guard is **gone** — a missing grid now fails instead of
skipping, which is the pattern that once printed a clean run for a card that
was not there. `test_phase_i` 16 pins the ten-region list and both old labels
instead of "two tiles".

**Evidence.** `test_v3330_painsite.py` **12/12** (8 API incl. the mirror, 4 in
Chromium at 300 px). Negative control **24/24 seen to fail**, 12 by mutation,
including the two the brief names — `savetime` (episodes stamped with the save
time, not the onset) and `dropft` (Food Test rows dropped from Day by day) —
and `mirrored` (the grid drawn as he sees himself rather than as seen facing
him). It also caught two weak checks of my own before it ran: one assertion
that could not fail, and an old-label check v3.32.0 would also have passed.
All 28 server suites and the mirror's 14/14 green before the restart.
Deployed 21:48 IST, `app.py` sha256 `28b50914…`, byte-identical to the repo;
schema read 3.3.5 after the restart and **3.3.6 after one GET /login**.
Rollback `app.py.bak-v3330-20260923_214724`, DB
`health3.db.pre-v3330-20260923_214724`, mirror
`/root/ops/health_mirror.py.bak-v3330-20260923_214724`.

## The food test — v3.32.0 (`GUTLOG_V3320_FOODTEST`, 2026-09-23)

**`https://health.dr-manoj.in/foodtest`**, and a Food test card on the Now
tab. It runs the FODMAP reintroduction in plan document 4, in under 30
seconds a day: one tap for the day's step, one short evening score.

### The plan is data
`seed_foodtest.py --plan foodtest.local.json --apply` loads his plan file
(gitignored; master copy `fitlog-ingest\foodtest.local.json`; **deleted from
the server after seeding**, like the recipe cards) into `ft_items`, and the
reminder line, plan-document id, safe plate and rules into the `ft_cfg`
setting. Re-seeding replaces the plan's definition only, never a logged
step, score or outcome. The medicine lines of the PDF's Week 0 are **not**
transcribed; they stay in the PDF, one tap away. The PDF stated no washout
for Weeks 4 and 5 and no meal for Weeks 2 and 4; **he set those on
23-Sep-2026** and the plan was re-seeded at 13:06 IST (backup
`health3.db.bak-seedfoodtest-20260923_130602`, no step, score or outcome
existed yet, none changed). The seeder also adds
each test food to the library from the USDA table (source `USDA`, dry basis
for pulses, cooked for cauliflower), so a logged dose is also an ordinary
meal in the day's nutrition totals.

### How it behaves
- **Steps advance when logged, never by the calendar.** `ft_progress()`
  finds the first step not yet logged. A missed day, **Skip today** or
  **Pause** (holiday / operating list / illness / other) leaves it where it
  was and is noted as a gap, not a result. One step per week per day; only
  the next step can be logged.
- **The card**: "Week 1 · day 2 — Kala chana 25 g dry", cooked equivalent and
  meal, the amount (editable), the day (today or up to six back, a list) and
  the time (default now, the two time lists). **Taken**. A conditional step
  (the 60 g) also offers "Not clean — go to washout". Afterwards: "✓ Today …
  at [time]" (tap to change), Undo, and "Next: …".
- **Washout days ask only the evening score**; saving it counts the day.
- **Evening score**: pain 0–10, bloating, urgency, Bristol — all four
  required — and "clear symptoms today?". One per day, re-opens for editing.
- **The stop rule is offered, never applied.** A clear-symptom score on a
  ladder with a dose that day or the two before brings "Stop this ladder and
  record the limit at N g?"; one tap records **limit at N g** and moves to
  washout. Nothing is ever concluded from the scores.
- **Outcome per group** — Tolerated / Limit at N g / Not tolerated / Not
  tested — asked on the card when a week ends, changeable on `/foodtest`.
  Week 4 is offered only after Week 1 is marked Tolerated; optional weeks
  can be declined ("Not doing this week" = Not tested).
- **`/foodtest`**: per group, each dose with date, time and that day's
  score, **plus the first washout day** (reactions run 24–48 h behind); the
  outcome; every time and day editable (`/api/ft/retime`, audited in
  `edits`; a dose's meal moves with it); cooking notes, the safe plate, the
  rules, and the plan PDF link. Dinner days of Week 0 are listed with time,
  size and cramp.
- **Two flags, stated not interpreted**: a medicine start, stop or dose
  change recorded in GutLog inside the challenge window (`med_schedule`
  effective dates and `courses` start/end) — "medicine change during this
  week — result may be unreliable" — and a gap of more than one day between
  doses inside a ladder.

### Evidence
`test_v3320_foodtest.py` **16/16** (11 API on a synthetic plan, 5 in Chromium
at 300 px). Negative control **31/31 seen to fail**, 12 by mutation
including `hideflag` (the confounder flag hidden), `autostop` (the stop
imposed from the score), `skipadvances` (a skipped day moving the plan on),
`mealstays`, `partialscore`, `nowashday`, `anystep`, `twoaday`, `nostopjump`,
`amountignored`, `limitnog`, `widecard`. It caught one weak check on the way:
the folded-width test measured only the page, and a card that clips its
overflow passed with a line running off it; it now checks nothing inside
the card ends past the card's edge. `test_v3310_foodlib` 16 was also
tightened (it compared the card's "now" with the minute the suite started,
and failed when a slow run crossed a minute); the v3.31.0 negative control
was re-run against the exact v3.31.0 build afterwards, 33/33. All 28 server
suites green before the restart. Deployed 12:51 IST, `app.py` sha256
`8eea4dd0…`, byte-identical to the repo. Rollback
`app.py.bak-v3320-20260923_125020`; DB before the patch
`health3.db.pre-v3320-20260923_125019`, before the seed
`health3.db.bak-seedfoodtest-20260923_125058`. New tables `ft_items`,
`ft_log`, `ft_score`, `ft_outcome` via `SCHEMA` — no schema-version bump.

## Foods by weight, a sourced table, and a time on every entry — v3.31.0 (`GUTLOG_V3310_FOODLIB`, 2026-09-23)

**Food list:** Meals → *Manage food library*, at `https://health.dr-manoj.in/`.

### What was there
`library.portion` was free text ("1 katori", "30 g") and protein / kcal /
fibre were per *that* portion. Logging was whole multiples of it (the
basket's counter, small/medium/large, ½–2 servings). Every value was
hardcoded or typed. On times: the Day by day card could move any entry and
a dose row could be re-timed, but the Now meal card logged at the moment Log
was pressed, meal rows showed a time that could not be tapped, and the Meds
tab listed dose times as plain text.

### What it does now
- **A numeric basis for every food.** New `library` columns: `portion_qty`
  + `portion_unit` (g/ml), per-100 `b_protein` / `b_kcal` / `b_fibre`,
  `weighed_dry`, `portion_est`, `source`, `source_date`, `source_ref`. The
  old `protein`/`kcal`/`fibre` stay the **per-portion** values every reader
  already uses; `lib_apply()` is the one place that works them out from the
  basis, so nothing that reads them changed.
- **Weighed dry.** For dal, rice, poha, oats: the basis is per 100 g *dry*,
  so the dry weight on the scale is exact and no cooked-yield factor is
  guessed. Every weight field for such a food says "dry weight".
- **Logging by weight.** The basket row and the Now card's "Also had" rows
  keep their counter and gain a grams/ml field. The server scales from the
  per-100 basis (`lib_weigh`); a weighed item's numbers never come from the
  page. The meal keeps `g`, and `dry` when it was a dry weight.
- **The source, decided on the server.** Values equal to the table row he
  picked are `USDA`; values that differ from what was stored are `own`; a
  save that changes no value keeps what it had, so a later lookup can never
  turn `own` back. Shown on every row.
- **The table.** `food_table_usda.json` beside `app.py`, built by
  `build_food_table.py` from USDA FoodData Central **SR Legacy** (April
  2018), public domain (CC0 1.0): protein, kcal and fibre per 100 g for
  7,791 foods. **IFCT 2017 is not bundled** — no licence-clean
  machine-readable copy exists (NIN copyright). No live API call: a lookup
  is a deterministic search of that file (`/api/foodtable`, login-gated),
  with Indian names mapped to the table's words (masoor dal → lentils, chana
  → chickpeas, besan …). It does not know paneer or poha; there the values
  stay his to type. A new food whose name the table knows is filled once,
  with the match shown; nothing is ever written over his values without a
  tap. **Two SR Legacy rows are left out** by the builder because their names
  contain a word on the local clinical-terms list, which NO_SECRETS check C
  would refuse in a tracked file; only the count is printed.
- **The list.** Most used (90 days) first, then by group; each row shows the
  portion with its weight ("1 katori · 150 g (est.)"), the values for that
  portion, the per-100 values and the source. One editor (`lfEditor`) serves
  the list and the Meals tab's "add a food".
- **Times.** `teOpen()`: one way to change the time of anything logged,
  using the same hour/minute lists as every time box (v3.27.0), never the
  phone's dialog. Wired to meal rows on the Meals tab (any day), meal rows on
  the Now tab, the Meds tab's today list and the Review tab's dose table. All
  save through `/api/retime`, so every change lands in `edits`. The Now meal
  card has a **Time eaten** field (default now; an edited meal opens at its
  own time, and a change made there is audited too). A meal cannot be logged
  later than now today — the rule doses and retimes already had. The Meals
  and Meds time boxes reset to now when the tab opens unless he set one.
- **Meds tab, folded screen.** Its Date | Time row was **316 px wide on
  v3.30.0** — the same `.row2` fault v3.29.0 removed from the Meals tab. The
  two cells now wrap.

### The migration (library only)
`lib_backfill()` runs from `_migrate()` (schema **3.3.4 → 3.3.5**), from
`_seed()`, and before `/api/library` reads. It touches only rows whose
`source` is empty and never changes per-portion values; meals are not read or
written. A portion text starting with a number and unit is exact; `~150 g`
inside the text or a household word (katori 150 g, roti/chapati 40 g, slice
30 g, egg 50 g, glass 250 ml, tbsp 15 g, tsp 5 g) is **estimated**; anything
else gets no weight. Every pre-existing food is `source='estimated'`, since
none of those numbers came from a table — including any he typed himself;
editing a value makes it `own`.

**Live, 10:35 IST:** 162 foods — **17 exact, 76 estimated weight, 69 no
weight**; 0 of 162 per-portion rows changed; day totals for all 6 logged days
identical before and after.

### Suites changed on purpose
Four older suites seeded today's meals at 08:00–20:00 or pinned the schema
string, and would fail every morning under the new rule: `test_v3271`,
`test_v3272_healthz`, `test_v3290_nutrition` now seed at 00:00;
`test_phase_m` 01 checks the schema reached 3.3.4 rather than equalling it.

### Evidence
`test_v3310_foodlib.py` **19/19** (13 API, 6 in Chromium at 300 px; the
browser six skip on the server, which has no Playwright). Negative control
**33/33 seen to fail**, 16 by mutation: `householdexact`, `touchmeals`,
`clientnumbers`, `nodry`, `keepsource`, `backfillall`, `anyfdc`,
`staysday`, `nofuture`, `noaudit`, `nobasis`, `nodrylabel`, `retimetoday`,
`cardnow`, `nofill`, `widebasket`. It caught a hole on its first run:
assertion 02 took its "before" snapshot with a request that had already run
the migration, so a migration rewriting meals passed; the snapshot now comes
first. All 27 server suites green before the restart (phase_j 28/28 with the
FitLog path; `test_ui_now` offline, ALL PASS). Deployed 10:35 IST, `app.py`
sha256 `12f1101c…`, byte-identical to the repo; `--reverse` reproduces
v3.30.0 byte-for-byte (`bfbd1a56…`). Rollback
`app.py.bak-v3310-20260923_103419`; DB backup
`health3.db.pre-v3310-20260923_102711` (integrity ok, 36 tables).

## The mirror staleness line — v3.30.0 (`GUTLOG_V3300_MIRRORSTALE`, 2026-09-22)

`/api/mirror` reads one timestamp and the Now tab shows **one line, only when
something is wrong**. Nothing here reads the record.

The Health Mirror (`ops/health_mirror.py`, and `ops/mirror_records.ps1` on
the PC) copies his record into a private Drive folder so the Claude app on
his phone answers from the record rather than from memory. The danger is not
that it breaks — it is that it breaks **quietly**. Claude would go on
answering from a week-old file, confidently, with nothing to suggest
otherwise. A missing file produces a question; a stale one produces an
answer. So the mirror writes `last_success.json` **only after an upload that
worked**, and GutLog says so when that stamp is over `MIRROR_STALE_HOURS`
(36) old, or has never existed.

Override the path with `GUTLOG_MIRROR_STAMP` (the suite does). The payload is
four keys — `ok`, `hours`, `never`, `text` — and deliberately nothing else:
no server path, no token, no record.

*Assertion 05 is worth reading before changing it.* It first probed the
response for `/root` and `health3`, strings that never appear in a temp path,
so a mutation that handed the page the stamp path sailed straight through it
and the negative control caught that. It now asserts the **property** — the
exact key set, and no value that is a filesystem path.

Evidence: `test_v3300_mirror.py` 7/7, negative control **7/7 seen to fail**,
four by mutation: `neverok` (a mirror that never ran reports fine),
`nothreshold` (nothing is ever stale), `leakpath` (the state hands over the
server path), `alwayswarn` (the warning is always on, which teaches him to
ignore it). 24 suites green on the server before the restart. Deployed
12:56 IST, `app.py` sha256 `bfbd1a56…`, rollback
`app.py.bak-v3300-20260922_125629`.

## Nutrition history — v3.29.0 (`GUTLOG_V3290_NUTRITION`, 2026-09-22)

**`https://health.dr-manoj.in/nutrition`**, and a Meals tab that answers to
a date.

### What was actually wrong — worth reading before changing any of it
He said he could not find where to see previous days. The brief asked to
establish the facts before building, and the facts were not what "missing
feature" suggests:

- The day totals card on the Meals tab read `/api/meals/today/<day>` and
  **summed it in JavaScript**.
- Beside it sat `#ml_day`, an `input[type=date]` — with **no change listener
  anywhere in the file.** It was never a day chooser. It was only the date a
  *new* meal would be filed under, and changing it refreshed nothing at all.
- The meal list with Edit / Again / Delete lives on the **Now** tab and was
  hardcoded to `todayISO`.
- There was no history view of any kind.

So a past day could be **computed** but never **seen**, and the one control
that looked like it should show you one silently did nothing. He was not
failing to find the feature; he was using the thing that looked like it.

### What it does now
- **Totals moved to the server**, `nut_day()`. The day card, the history
  page and both APIs read that one function, so they cannot disagree —
  there is no second calculation left to drift. The `meals` table already
  stores per-meal protein/kcal/fibre computed on the server at insert, so a
  day is a plain `SUM`; nothing is worked out a second way.
- **Day stepper** on the Meals tab: previous / next either side of the date,
  **next disabled on today**, and the date itself opens three lists
  (day / month / year) — not a native picker, because the Fold cover screen
  hides that dialog's own button. `#ml_day` stays in the DOM, hidden, as the
  value `saveMeal()` reads, exactly as v3.27.0 does with time boxes.
- **The card and a per-day meal list follow the chosen day**, and every meal
  keeps Edit / Again / Delete on any day. Editing a past meal opens the same
  card editor the Now tab uses, loaded for *that* day; tapping **Now** in the
  nav always resets the card day to today, so stepping back to look at
  Sunday cannot leave tomorrow's breakfast filed under it.
- **`/nutrition`**: server-rendered, newest first, 14 days with a link for
  30. Per row the date, kcal, protein against the plan's target, fibre
  against 30 g, and the meal count. A day with nothing logged says **"not
  logged"** and never 0 kcal; a day with fewer meals than usual is marked
  **"partial"**, so a low total is not read as a low-intake day. Tapping a
  row opens that day in the stepper (`/?open=meals&day=…`).

Targets come from his own plan (`_protein_target()`, and `targets.fibre`
with 30 g as the fallback). "Usual meals" is the plan's `main_meals` count,
else 3 — used **only** to label a thin day, never to score one. Values stay
marked estimated.

### Live read-back, 2026-09-22
Four days have meals, not the two or three he remembered: 21 Sep 2049 kcal /
80.6 g protein / 34.0 g fibre over 6 meals, 20 Sep 2099 / 94.7 / 32.5 over
6, 17 Sep 1025 / 55.7 / 16.5 over 3, and **18 Sep 110 kcal over 2 meals,
labelled partial** — which is exactly the case the label exists for. The
other ten days in the window say "not logged". All 14 days compared between
the card and the history: **0 mismatches**.

### Two things the suite caught
1. **The first version wired nothing.** Every stepper function was defined
   and not one control was bound, so the arrows drew and did nothing — the
   same fault as the date box this release exists to fix. They are bound in
   one IIFE at the end of the block, after the markup is parsed.
2. **A bold date in the form broke the folded screen** (316 px against 300).
   `.row2` is `1fr 1fr` and grid items will not shrink below their content,
   so the text pushed the Time cell's two 72 px-minimum selects off the
   edge. The form's Date cell is gone entirely instead — the stepper above
   already states the day — which restores the original geometry rather than
   fighting it.

### Evidence
`test_v3290_nutrition.py` **11/11** — the history page by plain fetch, the
stepper driven in Chromium at 300 px, because a server fetch of a
JS-rendered tab proves nothing about it. Negative control **13/13 seen to
fail**, seven of them by mutation: `zeroday`, `nopartial`, `twosums`,
`cardsum`, `stucktoday`, `noactions`, `cardzero`. Assertions 04 and 08 are
each declared twice — agreeing with the card and reading the same function
are two promises, and so are following the day and keeping the actions.
24 suites green on the server before the restart. Deployed 09:58 IST,
`app.py` sha256 `1461cba7…`, rollback `app.py.bak-v3290-20260922_095851`,
DB backup `health3.db.pre-v3290-20260922_095851`.

### Plan #2
A second plan document, first considered 22-Sep-2026, status Active, loaded
through `seed_plan.py` (the upload route, not hand-written rows) as plan
id 2. The title stays in the database and the PDF, per §5d — it is not
repeated here even though this one names no medicine, because "check each
title before writing it down" is a rule nobody applies reliably. Backup
re-run afterwards: `plans -> 2 of 2`.

## Plans — v3.28.0 (`GUTLOG_V3280_PLANS`, 2026-09-22)

`/plans`, linked from the header. A dated plan document he can open on the
phone and put into the system share sheet. First version holds and shares
PDFs; the tables are shaped for what comes next.

**Titles name medicines.** They live in the database and the PDF only —
never in tracked code, fixtures, docstrings or commit messages. The suite
uses "Plan A"; `seed_plan.py` takes the title as an argument for the same
reason. See CLAUDE.md §5d.

### What it does
- **List**: newest first by first-considered date, each row with the title,
  "First considered: DD-Mon-YYYY", a status badge (Draft / Active / Closed)
  and **Open** / **Share**.
- **Open**: the PDF inline, `login_required`, `Cache-Control: no-store`,
  `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`.
  `?dl=1` gives the same file as an attachment.
- **Share**: `navigator.share({files:[File]})` behind `navigator.canShare`,
  so WhatsApp and mail appear in the sheet. **There is no public link and no
  unauthenticated route** — Share hands over the BYTES, so there is nothing
  to leak and nothing to revoke. Where file sharing is unsupported the
  fallback is a plain download of the same bytes, never a URL.
- **Add / rename / change status / archive.** No delete: archiving hides the
  row and keeps the file, and the file stays openable.

### How the file is handled
Stored in **`/root/gutlog/plans_files/`**, beside the live database, under a
**sha256 name**; the uploaded name is kept only as the download filename.
PDF is enforced by the **`%PDF-` magic bytes, not the extension** — a
non-PDF called `.pdf` is refused, and refused *before* the plan row is
written, so nothing is left behind. 20 MB cap. Writes go through a `.part`
file and `os.replace`.

### Three deliberate departures from the brief
1. **Tables in SCHEMA, not `_migrate()`.** `db()` runs
   `executescript(SCHEMA)` on every connection, so `CREATE TABLE IF NOT
   EXISTS` there always applies. `_migrate()` returns early whenever
   `schema_version` is current, so a table added there appears only if the
   version constant is bumped too — one more thing to get right, for no gain.
2. **The date field is three lists (day / month / year), not
   `input[type=date]`.** v3.27.0's MutationObserver converts
   `input[type=time]` *only*. Widening it to dates would change every date
   box in the app — a page-wide regression for one new form. Three lists
   honour the Fold rule without touching anything else.
3. **The list is rendered server-side**, and the JS only acts on it. The
   first draft built the list in the browser from `/api/plans` and the suite
   caught it at once: fetching `/plans` returned a shell with no title and no
   buttons. That is the v3.4.0 lesson in miniature — a list only JS can draw
   is a list no server suite can see, and a page that shows nothing if the
   script fails.

### The header had no room for a third link
Adding **Plans** beside Account and Lock pushed the main page to **327 px
against a 300 px viewport**, and `test_v3270` assertion 07 failed on the
sideways scroll — which is exactly what that assertion is for. The bar now
wraps at that width. *Note for the next person: there are two near-identical
header blocks in `app.py`, and the first belongs to `ACCOUNT_PAGE`. Patching
that one changed the Account page and left the fault untouched — and the
suite reported **the same 327 px**, which is what gave it away. `APP_PAGE`'s
block is the one with `z-index:5` and 10 px padding.*

### Backup
`plans_files/` is inside the folder `backup.sh` already tars, so it is
carried with no new rule and no exclusion matches it. But "it is already
carried" is what was believed about the database that turned out not to be
backed up at all, so `backup.sh` now **counts** the documents in the finished
tarball against the ones on disk, prints `plans -> N of N`, and exits non-zero
if any are missing. Verified by hand on 2026-09-22: extracted from the
tarball, the document's sha256 equals its own filename and it still begins
`%PDF-`. Rollback `backup.sh.bak-plans-20260922_084642`.

### Evidence
`test_v3280_plans.py` 8/8, negative control **9/9 seen to fail** — 01, 02 and
05 by version, and five properties broken on purpose in the current build
because a version control proves nothing about them when the whole page is
new: `extonly` (trust the extension), `nologin` (serve without a session),
`anystatus` (accept any status), `hidenot` and `archdel` (archive stops
hiding / starts deleting), `uploadname` (store under the uploaded name).
Assertion 07 is declared twice: hiding and keeping are two promises.
23 suites green on the server before the restart. Deployed 08:44 IST,
`app.py` sha256 `0dd109b8…`, rollback `app.py.bak-v3280-20260922_084418`,
DB backup `health3.db.pre-v3280-20260922_084418`.

### Later, not now
`plans.started_on` and `plans.notes` exist and are unused; `plan_files` is a
table rather than columns so revised versions can stack, newest first, which
is what `ix_plan_files_plan` is for. Next: dated regimen steps tied to a
plan, and checkpoints on the Now tab.

## A health endpoint that exists — v3.27.2 (`GUTLOG_V3272_HEALTHZ`, 2026-09-20)

`GET /healthz` → `ok 3.27.2`, `text/plain`, 9 bytes. **No login, no
database, no session**, and it answers before `/setup` has ever been run —
that is exactly when a health check earns its keep. `APP_VERSION` is the
single source of the string, carrying the marker list in a trailing comment
the way RxGuard's does.

GutLog had **no health route at all** and no version constant, while three
briefs in a row told a session to "read `/healthz`". A 404 there is
indistinguishable from a broken deploy. Shipped as its own two-anchor
patcher rather than folded into v3.27.1, whose six anchors are all inside
the page JavaScript and whose manifest was already evidenced.

Because it is the one route with nothing in front of it, assertion 04
checks that the body carries no record data and no host detail. A version
control could not prove that — a route that does not exist cannot leak — so
it is shown failing by a mutation that makes the body report a figure
beside the version. Suite `test_v3272_healthz.py` 4/4, negative control 4/4
seen to fail. Rollback `app.py.bak-v3272-20260920_221028`.

## One protein target on the page as well — v3.27.1 (`GUTLOG_V3271_TARGETJS`)

v3.27.0 moved the protein target to the diet plan's figure **on the server
only**; the page still carried the old constant in three places. Caught
after v3.27.0 was already live, because the v3.27.0 suite read the server
helper and never rendered the basket or the bar.

The page now keeps **one** target, `PROT_TGT`, set from `/api/summary`'s
`target` the moment the rings load, with 57 only as the value before the
first answer:

* the basket line, "day protein would reach n/57 g" → the real target;
* the day protein bar, `p/57*100`, which filled at 57 and so read **full**
  while the card above it said "of 100" → the real target;
* `(s.target||57)` → `(s.target||PROT_TGT)`, so even the fallback is the
  same single value.

The patcher refuses to write if `/57 g` or `p/57*100` survives anywhere in
the file, which is the check that would have caught v3.27.0's gap. Three of
the four assertions render the basket line and the day bar in Chromium: a
meal of 50 g against a 100 g plan must fill the bar to **50%**, not to the
brim. Suite 4/4, negative control 4/4 seen to fail, all by version.

Deployed 22:04 IST, `app.py` sha256 `70a1cadf…`; verified against his real
data afterwards — `/api/summary` returns `target=100` with 94.7 g logged,
and **no hardcoded 57 survives the rendered page**. Rollback
`app.py.bak-v3271-20260920_220233`.

### Retired suites (2026-09-20 22:04 IST)
`test_migration_v330.py` is **no longer in `/root/gutlog`**. It asserts a
pristine post-v3.3.0 state — `schema_version` 3.3.0, an untouched `prnmeds`
sort order, an empty schedule — against a live database that is now at
3.3.4 with two dozen builds of real data on it. It has been failing 9/12 by
design for a long time, and it printed **"FAILURES PRESENT. Do not restart
the service"** on every run, which is worse than useless: a warning that is
always on teaches you to ignore warnings. Moved, not deleted, to
`/root/archive/gutlog-dead-20260920/`. The source stays in the repo.

Five suites that were never GutLog's also left `/root/gutlog` for
`/root/archive/gutlog-dead-20260920/stray-suites/`: `test_kb.py`,
`test_astaken.py`, `test_conditions.py` (RxGuard's) and
`test_activity_feed.py`, `test_gutlog_feed.py` (FitLog's). Run from
`/root/gutlog` they fail on missing modules and tables and read exactly
like a regression — they cost one session an hour on 20 Sep. Each was
confirmed to have an identical or **newer** copy in its real home, and each
still passes there: 32/32, 15/15, 10/10, 14/14, 10/10. Moved rather than
deleted because two of them (`test_kb.py`, `test_activity_feed.py`)
*differed* from the home copy, and a differing variant is the one thing an
`rm` cannot be undone for.

## One protein target, re-timed extras, no phone time dialog — v3.27.0

Three things the owner reported on 20 Sep, marker `GUTLOG_V3270_TIMEPICK`,
8 anchors, deployed 21:24 IST.

**1. "Lunch protein looks too high."** The log was right — lunch really was
25.9 g. The fault was that the Meals card read "62 of **57** g" while the
plan card read "62 / **100** g": `PROTEIN_TARGET = 57` predated the diet
plan and had never been replaced. `_protein_target()` now reads the plan's
`targets.protein`, with 57 kept only as the fallback when no plan exists.
Verified live: `_protein_target()` returns **100**.

> **Was not finished — two hardcoded 57s remained in the page JavaScript.
> Closed by v3.27.1, above.** The v3.27.0 patch did not touch them and
> `test_v3270.py` does not cover them:
> * `$('#ml_fmw')` — the meal basket's *"day protein would reach
>   `{n}`/57 g"*, a bare literal with no fallback.
> * `$('#dayPbar')` — the day protein bar's width, `p/57*100`, so the bar
>   reads **full at 57 g** while the card above it says "of 100 g".
>
> A third, `(s.target||57)` in `loadRings`, is a legitimate fallback and is
> correct as it stands. Both faults **predate v3.27.0** — they are
> byte-identical in `app.py.bak-v3270-20260920_212354` — so this release did
> not cause them, but it did not fix them either, and the Meals tab is
> exactly where he reported the problem. This is the CLAUDE.md rule 2 lesson
> again: assertion 01 is called "one protein target: the diet plan's" and
> passed, because the suite reads the server helper and never renders the
> basket or the bar. **Keep this entry.** It is the worked example of why a
> green suite is only evidence for the code it executes — the release whose
> entire subject was that constant shipped with two live copies of it, and
> the assertion that should have caught them is the one that reassured us.

**2. "No way to fix the time of an SOS dose logged late."** Extra-dose rows
had only Undo. Tapping a row now opens a time strip — 15 min / 30 min / 1 h
/ 2 h ago, or any time — saving through the existing `/api/retime`, so every
edit is audited in `edits` exactly as a scheduled-dose retime is.

**3. "On the folded phone the Set button is not visible when I edit a
time."** That was Chrome's own time dialog on the cover screen, not our
markup — our "Save time" button was on screen at 300 px. No page opens that
dialog any more: a MutationObserver renders every `input[type=time]` as two
lists (hour, minute). The real input stays in the DOM, hidden, with `.value`
kept in step both ways, so no reader and no form handler changed.

`test_ui_now.py` was changed **on purpose** in the same release: its three
`.tt.fill()` calls now pick in the lists (`pick_time`), because a hidden
input cannot be filled. That is the expected consequence of 3, not drift.

## Food trials as periods — v3.26.0

The old food test recorded **one day**: ate it, felt this. That is the wrong
unit. A food eaten once on a good day proves nothing, and the thing he
actually wants to know — *does this food suit me* — is a question about weeks.
His own note on the last test said as much: *will track for a month*. The app
had nowhere to put that intention, so it lived in his head.

A **trial** is a period: food, amount, how often, a start, and a planned length
from a week to a month. Meals whose item name contains the match text are
**linked automatically**, so the trial costs nothing to run beyond logging
meals he was logging anyway.

### What a trial compares, and what it throws away

Trial days against **the 14 days before it started**. A symptom day is a GI
episode, a down day or daily symptoms; the average gut pain is carried
alongside.

Two kinds of day are **set aside on both sides**, and this is the part that
makes the comparison worth reading:

- **Day-context days** (v3.23.0 — exertion, poor sleep, travel, unwell,
  stress, ate out). A bad day with an obvious other cause is not evidence
  about a food.
- **Days with nothing logged at all** — no meal, no dose, no episode, no day
  row. Counting an empty day as a symptom-free day is the single most
  flattering mistake this feature could make: it would turn every gap in the
  diary into evidence that the food is fine. Mutation-controlled.

Separately, **eaten-or-the-day-after** days are compared against the other
logged days, because a reaction that shows up the next morning is still a
reaction. Dropping the day after is also mutation-controlled. The forms in
which the food was eaten are listed, since two preparations of the same thing
are not always the same challenge.

### It refuses to draw a conclusion too early

Words — *no signal / possibly better / possibly worse / likely worse* — appear
only with **at least eight counted days on each side**. Below that it says
**"not enough days yet"** and nothing else. A verdict from three days is worse
than no verdict, because he would act on it. Mutation-controlled: removing the
minimum makes the suite fail.

### Ending one

His verdict is **Tolerated / Not tolerated / Not sure**, with a note — and it
writes through to the rest of the app: the library status of every matching
food is cleared or marked a trigger, and a matching recipe's stage moves to
rotation, avoid or paused. Starting a trial of a recipe sets that recipe **On
trial**, so the recipe book and the trial cannot disagree about what is being
tested.

Table `trials` via `SCHEMA` — no migration, no `schema_version` bump. **The old
`foodtests` rows are untouched** and the one-day test stays below the trials on
the Food test segment; the old record is history and history is not rewritten.

## The diet plan in the app — v3.25.0

The plan existed as a document. A document cannot tell him, at four in the
afternoon, whether he has had enough protein today or which of this week's
rotation rules he is about to break — and by the time he reads it in the
evening, the meal that would have fixed it has been eaten.

**"Today against the plan"** is a card on the Now tab under the meal card,
computed **entirely from the meals he has already logged**. The earlier
releases are what make it possible: v3.22.0 made logging a meal one tap,
v3.24.0 put the recipes where the plants and calcium values live. This one
reads that record back.

- **Today**: protein, calcium, fibre and energy so far against targets, as
  bars; protein **per main meal** against an aim, because three meals hitting
  a daily total between them is a different thing from one large meal doing
  all the work.
- **Foods that lack a calcium value are named.** A total quietly computed over
  foods with a missing figure reads as a low day rather than an incomplete
  one, and he would correct the wrong thing.
- **This week's plant points** against the target, with easy additions he has
  not yet had. **A spice counts a quarter** — a pinch of something is not the
  same plant event as a bowl of it, and counting it whole would make the score
  meaningless within a week. Mutation-controlled.
- **Up to five suggestions** for the next meal.
- **"This week"** opens each rotation rule's standing: *on track / due / short
  / at limit / over*.

### The two things it refuses to do

**It nags nothing in a thin week.** In a week with fewer than three logged
days, "short" rules still show their standing when he opens This week, but
they produce **no suggestion**. Three days is not evidence of falling behind,
it is evidence of not having logged; a card that scolds him for the gap it
cannot see would be wrong *and* would teach him to stop opening it.
Mutation-controlled, and on the live data today this is the branch actually
running — 2 logged days this week, four rules standing at "short", and not one
of them in the tips.

**It blocks nothing and writes nothing.** `/api/plan?day=` is a GET that
computes from logged meals: no schema change, no table, no stored verdict.
Nothing can go stale, and nothing the card says can be wrong in a way that
outlives the meals it was read from.

The targets, the rotation rules and the food map (plants, calcium, tags) are
his diet, so they live in `diet_plan.local.json` beside `app.py`, mode 600,
never committed. **No plan file, no card** — asserted first, so a clone of
this app for anyone else simply does not show it.

## Recipes — v3.24.0

v3.22.0 put his recipe collection into the `library` table so the meal cards
could reach it: 52 dishes, but only as **names with numbers attached**. The
cooking — what goes in, how it is made, which version leaves the onion out —
was still in a file on a laptop, which is to say nowhere useful at the moment
anyone is actually deciding what to cook.

**Recipes** is a third segment on the Meals tab. Search; filter by group
(*Fits now* / *Has an onion-free version* / *Occasional*) and by stage. A card
carries per-serving kcal, protein, fibre and fat (**estimated, and labelled
so**), plant points, a high-FODMAP flag, the whole-pot ingredients with the
high-FODMAP ones highlighted, the method, the notes — and, where it exists,
the **onion-free version set beside the original** rather than replacing it,
because which one he wants depends on the day.

### The stage is his, and the seed must never touch it

Five stage chips: Not tried, On trial, In rotation, Paused, Avoid. That single
field is the only part of a recipe card that is *his judgement* rather than
imported content — everything else can be re-imported from the source file at
any time.

So `seed_recipes.py` **refreshes the content of a card it already has and
leaves the stage exactly as it found it**. This is asserted directly: set a
stage, re-seed, and the card's text must be updated while the stage survives.
The mutation control removes that protection and requires the assertion to
catch it, because a re-seed that silently reset "Avoid" to "Not tried" would
look like a successful import and quietly lose the one judgement that took
real experience to make.

### Logging from the book

**Log it** takes ½, 1, 1½ or 2 servings, stamps now, picks the slot from the
clock, and writes an **ordinary meal row through the same `_log_meal`** the
meal cards use. Nothing about the `meals` table changes, so totals, the Meals
tab and the review export carry recipe meals without knowing they came from a
recipe. **Send to the cook on WhatsApp** opens `wa.me` with the text.

One interface detail with a reason: the Meals **Save bar is hidden on this
segment**. It belongs to the meal editor and does nothing here, and a Save
button sitting over a recipe book invites a tap that cannot do what it looks
like it does. Mutation-controlled, because leaving it there is exactly the
sort of thing that passes every functional test.

One table, `recipes` (id, slug, name, grp, stage, stage_note, data, updated),
created by `SCHEMA` — no migration step, no `schema_version` bump. The cards
themselves are his diet, so the source file is uploaded for the seed and
**deleted from the server afterwards**; the content lives in the database.

**Not in this build**, deliberately: adding recipes by paste, photo or voice;
versions; micronutrients; linking a recipe to a food trial.

## Day context — v3.23.0

A food trial cannot be read honestly without knowing what else the day held.
A bad gut day after a new food means one thing on an ordinary day and quite
another after a long drive, a night of broken sleep, or a fever. Until now
none of that was recorded, so every challenge result carried an unknown that
could not be recovered afterwards — **you cannot reconstruct last Tuesday's
travel from the diary once Tuesday has gone**.

**Day context** is a card on the Now tab, above Down day: Today / Yesterday,
and six toggle chips — Heavy exertion, Poor sleep, Travel, Unwell, Stress,
Ate out. One tap marks, a second clears, and the summary says what is marked.
Yesterday is there because the marking often only occurs to him the next
morning, and a context he cannot backfill by one day is a context he will
stop using.

Two tables, `day_context` (day, tag, primary key day+tag — so a repeated tap
is harmless rather than a duplicate) and `day_context_note`, created by
`SCHEMA`: no migration step, no `schema_version` bump. `/api/daycontext`
GET/POST behind the login; `/api/feed/daycontext` read-only on the feed token,
so a trial reading — or FitLog — can ask what a day held without reaching into
the database.

### Why it is not part of Down days

Down days (v3.17.0) mark **how he was**; day context marks **what the day
did to him**. They answer different questions and a day can easily be one
without the other — a long drive he coped with fine is travel and not a down
day. Folding them together would have made both unreadable, so Down day stays
exactly as it was and nothing existing changes.

Only the six keys are accepted; an unknown tag and a future day are refused,
and that refusal is mutation-controlled.

## Meal cards on the Now tab — v3.22.0

Meals were not being logged. Not forgotten — **logging one took ten to fifteen
taps and typed numbers**, so it lost to every other thing happening at
breakfast. A food diary nobody fills in is not a food diary, and the data it
was supposed to feed (the FODMAP work, the protein target, food challenges)
was quietly starving.

The fix is not a better form. It is the observation that **most meals are the
same meal**. So a card opens already set to what he had last time and the
button reads *"Log lunch — same as last time"*: one tap, and the time is taken
at the moment of logging rather than typed.

### The card

Six cards — Morning, Breakfast, Before lunch, Lunch, Evening tea, Dinner —
**defined in `meals.local.json` beside `app.py`**, mode 600, never committed
(CLAUDE.md §5d: what he eats is the health record). The Now tab opens on the
card for the time of day: the latest card whose `from` time has passed and
that is not yet logged today.

- A different choice is **one chip**, not a form.
- Counts step in **halves**, because half a slice is a real portion and
  "1 or 2" is not a measurement.
- An **onion switch** on lunch and dinner adds the onion base item when it is
  on — it is the thing most likely to differ day to day and most likely to
  matter.
- A food the library does not have is **named, not silently dropped**, and the
  rest of the meal still logs. A missing ingredient must never cost the meal.

### The dish that is not on any card

His question when he saw the mockup was the right one: *what happens with
pizza, or something new?* Two answers, both one tap from where he already is:

- **"+ Something else"** on every card, and an **"Other meal"** tab: search his
  own foods, **recent first**, then favourites — because the thing he is
  looking for is nearly always something he has eaten lately.
- A dish never seen before: **name, size (small/medium/large), kind** (guessed
  from the name). It is added to the library as an ordinary item, tagged
  `estimated <kind>`, with values from a typical dish of that kind and FODMAP
  **"M" with a note saying it is unknown**. Guessing a number is acceptable
  here *only because the guess is labelled as one* — an unlabelled estimate in
  a food diary is worse than a gap. The same name typed again reuses the item
  rather than making a second.

### Today's meals, and fixing a mistake

Under the card: **Edit** (reopens the card exactly as logged; Save keeps the
original day and time, so correcting what was eaten never rewrites when),
**Again** (logs the same meal now) and **Delete** (two taps).

### What it deliberately does not touch

New table `meal_meta` (card, choices, onion, extras per meal) created by
`SCHEMA` — no migration step, no `schema_version` bump. **`meals` rows keep
exactly their old shape**, so every existing total, the Meals tab and the
review export are untouched: the card is a faster way to write the same row,
not a new kind of record. `GUTLOG_MEALS_FILE` overrides the config path for
tests only.

## One tablet is one dose — v3.21.0

Every pain tile and the down-day card ask what was taken for the symptom, and
until v3.21.0 **every answer wrote a new dose row**. That is the right
behaviour for the Now tab, where a tap means a tablet, and the wrong behaviour
everywhere else: the same tablet, named against the evening's down day and then
against a pain site three minutes later, became two doses. A combination tablet
with no chip of its own had to be entered as its two single ingredients, so
each entry became two rows again.

It is worth being exact about why this went unnoticed for so long. **Nothing
looked broken.** Each row was individually true — he really did take something
for that symptom — and the diary is supposed to fill up. The defect only
becomes visible when something *totals* the rows, which is what RxGuard's daily
ceiling does: one tablet read as three times the limit.

### What changes

A chip on a pain tile or the down-day card is a statement about **what was used
for this symptom**, not a new event. So if a dose of that medicine — or of any
product carrying **all** of its ingredients — is already logged from **6 hours
before to 30 minutes after** the entry, the chip is linked to that dose and no
row is written. The response carries `same_dose`.

Two boundaries, both mutation-controlled because both are easy to get wrong in
a way that still looks right:

- **The window is 6 hours, not the day.** Linking across the whole day merges
  a genuine second dose taken in the evening into the morning's.
- **All the ingredients, not any.** A combination is only absorbed into a dose
  that carries every one of its ingredients; a dose missing one is a different
  medicine, and the combination is a new dose.

### Nothing is silently dropped

The page shows a strip — *"&lt;chip&gt; — counted with the &lt;medicine&gt; dose at
HH:MM"* — with **It was a new dose** (which logs it for real through
`/api/now/dose`) and **OK**. A rule that quietly discards an entry is worse
than the duplication it replaces, because the owner cannot see it happening.
He can always overrule.

**The Now tab is deliberately unchanged**: each tap there is still one tablet,
and two taps can be two tablets on purpose. A double-tap guard was built for it,
broke `test_phase_c` and `test_phase_o`, and was **removed on purpose** rather
than have the suites loosened around it.

### The combination chip is config, not code

`patch_gutlog_v3210.py` names no medicine. A combination tablet gets its own
chip by adding `[label, molecule]` to `pain_analgesics` in the gitignored
`regimen.local.json` on the server; `_pain_med()` resolves it **by exact
molecule** to the `prnmeds` row. `PAIN_ANALGESICS` is read at import, so the
service must be restarted after the edit — a config change that appears to do
nothing until the restart is its own trap.

## Two stock pipelines — v3.20.0

One list was the wrong shape for two different problems. A medicine taken every
morning and a medicine taken three times a year both ran out, but they do not
run out the same way, and they cannot be bought on the same rhythm.

| | **daily** | **SOS** |
|---|---|---|
| what it is | a fixed schedule, or a pack a variant schedule is **linked** to | kept for occasional use, no daily rate |
| pipeline | the **monthly order** (below), unchanged arithmetic | the **Running low** card, any day |
| raised when | the last week of the month | stock falls below a **third of keep** (never below 1) |
| topped up to | 40 days (the setting) | the keep figure, in whole packs |
| saved as | `stock_orders` row for that month | `stock_orders` row with `month='SOS'` |

An SOS medicine **no longer rides the monthly order**. Buying four months of a
rescue tablet every month is how the cupboard fills; waiting for month end when
it is already gone is how the rescue is not there. The threshold is a third of
keep rather than "below keep", so the list does not raise something the day one
tablet is taken out of a full box.

Items already on an **open** SOS order show as *ordered* and are not raised
again. The SOS card has its own Send / Copy / Received, and a **Low** banner
sits on Now while anything is low.

### The strength links

A medicine logged with a choice of strengths could not be counted at all from
v3.6.0 — there is no single units-a-day figure for a schedule that offers
several. That was fine until he said the opposite: one medicine, *whatever
strength, needs to be tracked, because it is required early in the morning and
running out will spoil the day.*

`stock_links(med_id, variant, stock_med_id, units)` links **each strength label
on the variant schedule to the pack it actually comes out of**, with a unit
count:

| label | pack | units |
|---|---|---|
| 72 | the 72 pack | 1 |
| 145 | the 145 pack | 1 |
| 290 | the 145 pack | **2** — there is no 290 pack; it is two capsules |

A dose logged as `145 + 72` takes **one from each** pack. The packs are then
ordinary per-dose stock: counted, alerted at the existing 7-day and 3-day
thresholds, and carried on the monthly order at `max(14-day use × 40, keep)` —
the keep figure is a **floor**, so a pack he uses rarely is still never allowed
to reach zero. Linked packs are daily by definition and never reach the SOS
list.

The variant medicine itself stays untracked and **says where it is counted**
("strengths vary — counted on …"). Clearing the links takes the packs back off
the monthly order, which is asserted rather than assumed. A **Link strengths**
form sits on the variant's stock row.

This closes the gap v3.19.0 could only name.

### Routes added

| Route | Method | Does |
|---|---|---|
| `/api/order/sos/save` | POST | saves the running-low list as an SOS order |
| `/api/stock/link` | POST | sets or clears the strength links for one medicine |

Both `@login_required`; the SOS save route's guard is mutation-controlled.
`/api/order` carries the SOS plan under `sos`, so one read answers both
pipelines. One new table, no column changes, **no `schema_version` bump**.

## The monthly medicine order — v3.19.0

He reorders in the **last week of each month, for the month after**, and keeps a
buffer of ten days on top of the month. So the order is a **top-up to a target
of 40 days of stock, never a flat 40 days bought every month** — buying the
whole target every month is how a cupboard fills with eleven months of one
medicine and runs out of another. The 40 is a setting (7–120 days).

v3.6.0 already keeps a live count: every logged dose and every pillbox fill
comes out of stock. v3.19.0 turns that count into the list he forwards to staff
on WhatsApp.

### The rule

Per medicine, the target is

| basis | target |
|---|---|
| `schedule` | the schedule's units a day × the day setting |
| `keep` | the keep-on-hand figure (an SOS medicine, which has no daily rate) |
| `usage` | the 14-day average use × the day setting |

and it is set not against today's count but against the **stock expected on the
1st** of the month being ordered:

```
expected = today's count + (what a filled pillbox still holds) − (use × days to the 1st)
order    = ceil(target − expected), rounded UP to whole packs
```

Two things that look like details and are not:

- **The week still to run comes off first.** Ordering against today's count on
  the 24th would under-order by a week of doses every single month.
- **A filled pillbox has left stock but has not been swallowed.** Its remaining
  days are added back before the month's use is taken off, so *filling the
  pillbox never changes the order*. Without this, the order would jump the day
  he fills the box and shrink again as he empties it. Asserted as an invariant
  (the same figure before and after a fill), with a mutation control that
  deletes the term and requires the assertion to catch it.

Nothing is precomputed. The plan is computed at read time, so it cannot go
stale between the day it is generated and the day he sends it.

### What is never silently dropped

A medicine that cannot be ordered automatically is **named on the card with the
reason**, never left out:

| reason | meaning |
|---|---|
| `strengths vary - count and order it yourself` | the schedule uses dose variants, so it is not stock-tracked at all (since v3.6.0) |
| `not counted yet` | no stock count has ever been entered for it |
| `schedule ends before the 1st` | the regimen line stops before the month being ordered starts |

An order list whose omissions are invisible is worse than no list: it reads as
complete.

### Send, and received

**Send on WhatsApp** opens `wa.me` with the order text; **Copy** copies it.
Either one saves the order for that month — the record of what was asked for
exists whether or not the message was actually sent, because the saved order is
what "received" is later checked against. Sending again before the order arrives
replaces it.

**Order received** adds every line to stock in one tap, once; **Undo** takes it
all back out. Once received, that month's order is closed.

### Packs

Each stock row gets a **Pack** form: pack size (the existing `prnmeds.pack_size`
that Bought already prefills from), a pack type (strip, bottle, pouch, sachet…)
so the order reads in the words the chemist uses, and a keep-on-hand figure for
an SOS medicine. Without a pack size the line falls back to bare units.

### The Now banner

In the **last seven days of the month**, while no order is saved for the month
after and there is something to order, an Order banner sits under the refill
banner in its own container. Tapping it opens the Stock tab at the card.

### Routes

| Route | Method | Does |
|---|---|---|
| `/api/order` | GET | the plan, the saved order if any, and `due` |
| `/api/order/save` | POST | saves the month's order (Send / Copy) |
| `/api/order/received` | POST | adds every line to stock, once |
| `/api/order/received/undo` | POST | takes a receipt back out |
| `/api/order/days` | POST | the 7–120 day setting |
| `/api/stock/pack` | POST | pack size, pack type, keep-on-hand for one medicine |

All six are `@login_required`, and that guard is mutation-controlled: the
control strips the decorator off `/api/order` and requires the suite to catch
it, because a route that quietly answers without a login is exactly the failure
a version-only control would miss.

## Schema

| Table | Purpose |
|---|---|
| `prnmeds` | Medicine catalogue. id, name, sort, molecule, form, pack_size, stock, active, **scheduled** |
| `med_schedule` | Effective-dated regimen lines. id, med_id, slot, dose_text, with_food, valid_from, valid_to, **epoch**, notes, created, **variants** |
| `doses` | Every dose event. id, day, dtime, medicine, reason, effect, notes, created, **status**, med_id, sched_id, dose_text |
| `episodes` | Symptom **and pain** events. id, day, etime, category, etype, side, severity, duration, notes, created, **bristol**, **treatments**, **radiates**. `category='pain'` rows come from the Pain now tiles (v3.12.0); `treatments` is pipe-joined, `radiates` is 0/1 |
| `vitals` | id, day, vtime, sys, dia, pulse, weight, waist, notes, created, temp |
| `days` | Daily rollup — syms, pain, pain_site, bristol, stools, tea, coffee, sleep, walk, treadmill, meditation, notes |
| `meals` · `library` · `foodtests` | Food logging, item library, food challenge results |
| `labs` · `consults` · `doctors` · `courses` · `patches` · `files` | Labs, visits, drug courses, patch on/off times, attachments |
| `settings` | key/value — schema_version, credential hashes, auth_epoch |
| `edits` | Retime audit (v3.5.0). tbl, rid, old_day, old_time, new_day, new_time, at. Created by `SCHEMA` on first request — no migration step |
| `stock_order_cfg` | Pack details per medicine (v3.19.0). med_id, pack_type, keep_units. Created by `SCHEMA` — no migration step, no `schema_version` bump |
| `stock_orders` | One saved order per month (v3.19.0). id, month, status, created, received_at, lines (JSON), text. `month='SOS'` is the running-low order (v3.20.0). Created by `SCHEMA` — no migration step, no `schema_version` bump |
| `stock_links` | Strength → pack (v3.20.0). med_id (the variant medicine), variant (the strength label), stock_med_id (the pack it comes out of), units. Created by `SCHEMA` — no migration step, no `schema_version` bump |

### med_schedule — effective dating

**Never edit a regimen line in place.** A dose change is a new fact about a new
period, not a correction of an old one; editing in place would silently rewrite
history and make every past dose row unexplainable.

The rule: **close the open row, open a new one.**

- `valid_from` / `valid_to` bound the period. `valid_to` NULL (or empty) = open.
- `epoch` increments on each regimen change, so a set of lines that changed
  together can be identified as one event.
- Exactly **one open row per (med_id, slot)** is enforced. A duplicate open row
  is rejected; close-then-open is accepted.
- Stopping a medicine closes the row and keeps the history — it does not delete.

Live example: one twice-daily medicine has its MORNING and EVENING lines
closed at epochs 2 and 3, with a new pair opened together at epoch 4.

`/api/now` computes today's expected doses by reading whichever line is
effective for today. Test 09 and 10 in `test_phase_a.py` gate this.

## Down days — v3.17.0

**Why.** The record has carried a recurring cluster for over two years as
"recurrent fatigue and subjective feverishness — never a documented
temperature, no cause established": hip and thigh ache, left abdominal pain,
fatigue, feverishness, a heavy head, sometimes the eyes, usually a broken
night. Nothing counted those days — no frequency, no duration, no pattern.
Yet every day around them was already fully recorded: steps, hours on legs,
sleep, doses, the drug epoch. What was missing was the marker saying which
days were the bad ones. This is that marker, and the view that turns months
of existing rows into an answer.

**Data model — decided, not redesigned.**
`down_days(id, day UNIQUE, components, coped, note, created)`. A down day is
a **calendar day**, not a timestamped moment, so it is not an `episodes`
row. `components` and `coped` are pipe-joined (the `days.syms` convention).
`day` is UNIQUE, so a second tap corrects rather than duplicates. **Runs are
computed at read time** from consecutive days (`_down_runs`): no run id, no
episode id, no state that can rot — the same principle as expected scheduled
doses. **Temperature does not live here.** `vitals.temp` already exists; the
card prompts for it and writes a real `vitals` row (`notes` "Down day"),
exactly as the analgesic chips write a real `doses` row. The card shows
whether one has been taken today and stores nothing else about it. Schema
3.3.3 → **3.3.4**, through the idempotent `_migrate` pattern; `_migrate`
runs per request, so it is verified by `schema_version` after a request,
never by `systemctl status`.

**The entry — one tap, and it stays one tap.** A *Down day* card on the Now
screen beside Pain, with one primary control: *Mark today as a down day*.
Tapping it marks today. Everything else is behind that and optional:
components in the record's own words (Hip / thigh ache · Left abdominal
pain · Fatigue · Feverishness · Heavy head / headache · Eyes burning or
watering · Broken sleep · Low mood) and *coped with* (Kept moving indoors ·
Rested · Skipped exercise · Worked anyway · Heat pad · Hot shower · NormaTec
· the two analgesic chips). Chips **save as they are tapped** — there is no
Save button and no form to leave half-filled on a day with a heavy head,
which would bias the record toward the days he felt well enough to type.
The medicine chips write through to the doses log with the reason set, via
`_log_analgesics`, the helper now shared with the pain tiles (factored out
of `api_pain`); only chips **newly added** to the stored row are logged, so
correcting the row cannot record a tablet twice. **One active prompt**: a
temperature, with a single *Not now* remembered for the day in
`localStorage` — it does not nag. A consecutive day extends the run and the
card reads *day 2 of this run*. Past days are marked from **Day by day**
(`#dvDown`, the backfill path); future days are refused. Nothing is seeded.

**The view — the point.** A *Down days* fold on Review, over the 30/90/180
range: count per month and the length of each run; **each down day beside
the day before it** — steps, hours on legs, exercise minutes, sleep, doses,
temperature, medication epoch — from rows that already exist (steps and
Watch sleep via FitLog's feed, now 180 days deep; hours on legs, exercise
minutes, doses, logged sleep and temperature from GutLog's own tables). Seven
facts per day would be eight columns, 466px on a 368px card; each fact
carries its own label instead, so the pair reads as two lines in the same
order and nothing scrolls sideways at 360px. Then which components co-occur
and how often; how many down days carry a temperature and what they were (if
that column stays empty the phase has failed at its one active ask, and the
card says so); and whether what he did changes the run length — runs where
he kept moving against runs where he rested — **as an observation with its
n, never as advice**, and the payload is asserted free of advice words.

**Two connections.** On the **third consecutive day** the card carries a
quiet note, verbatim from the Action Plan's flare protocol: *"Third day of
this run; your flare protocol asks for calprotectin and ESR/CRP within 48
hours."* A note, not an alarm, not advice. And the 14-day watch row carries
a **down-day lane** — a square, so it is never colour-alone beside the pain
disc and the operating-day diamond — in a fourth colour validated with the
other three (light `#0A93B0` on `#FCFCF9`, dark `#3D9BE0` on `#18211F`; all
five checks pass in both modes, worst pair unchanged at deutan 11.1 /
normal-vision 16.0 light, 10.4 / 15.7 dark). FitLog's trend leaves those days
out — see the FitLog DOSSIER, v1.6.0.

**Endpoints.** `GET/POST /api/downday` (state for a day; mark / correct /
temperature), `POST /api/downday/unmark`, `GET /api/downdays?days=N` (the
view payload), `GET /api/feed/downdays?since=` (bearer-gated, read-only, for
FitLog).

## Colour and theme — v3.16.0

**One palette, two selected modes, every pair measured.** The app renders four
pages — scan, login, account, diary — and until v3.16.0 all four were light
only, with 65 distinct colours across 208 declarations, most of them written
as literal hex rather than as tokens.

**Light, corrected.** Five text/background pairs in the shipped build were
below the 4.5:1 floor. They are fixed at the token so they cannot recur
per-site:

| token | was | now | why |
|---|---|---|---|
| `--muted` | `#5B7370` | `#556C69` | 4.37:1 on `--chip`, 4.29:1 on the account body |
| `--amber` | `#C8860A` | `#8A5A00` | 3.06:1 on the card |
| `.b-ok` bg | `#E7F2E8` | `#EAF4EB` | 4.46:1 under `--ok` |
| scan `.muted` | `#6A7773` | `#5B6B67` | 4.29:1 on its own body |

`#8A5A00` was **already in the file** as amber ink (`.tag.k-bp`,
`.rs-flag.AMBER`, `.rd-auto`), so collapsing `--amber` onto it removes a
colour rather than adding one. Phase K had permitted `#C8860A` as *large text
only*; Phase L forbids that, so it was re-tinted — see the report.

**Dark, selected rather than flipped.** Tokens chosen against the dark surface
and measured, never derived by inverting the light ones:

```
--bg #0E1513   --card #18211F   --line #2A3734   --chip #22302C
--ink #E8F1EE  --muted #9FB3AD  --teal #4FC4B1   --teal2 #68D9C6
--err #F5827A  --ok #79C97E     --amber #E0A83C  --hip #B9A0F0
```

**Accents invert, so ink on them must invert too.** `--teal`, `--ok` and
`--amber` all become *light* in dark mode, which turns every white-on-accent
surface into white-on-light. `.btn.primary`, `.chip.sel`, `.chip.just`,
`.scanbtn` and the dose ticks take `#0E1513` instead — 6.8:1 to 9.2:1 across
every accent. This was not spotted by reading the CSS; the screen-wide
contrast assertion caught it at 1.12:1 and four more like it.

**How the mode is chosen.** `prefers-color-scheme` by default. A three-state
control (System / Light / Dark) in the app-switcher row overrides it and
persists in `localStorage` under `gl_theme`. The override is applied by a
synchronous `<head>` script *before first paint*, so there is no light flash
on a dark phone. `color-scheme:dark` on the root makes date pickers,
checkboxes and scrollbars follow without per-widget CSS. Every dark
declaration is emitted twice — once inside
`@media (prefers-color-scheme:dark)` guarded by `:not([data-theme="light"])`,
once under `:root[data-theme="dark"]` — so an explicit choice wins in **both**
directions.

**Paper is not a theme.** The dark block is appended after the page's own
`@media print` rules and is more specific than they are, so a print block at
the very end forces white ground and black text in either mode.

**One validated categorical set, replacing two unvalidated ones.** The lane
markers and the pain/tea/coffee line chart were separate ad-hoc trios. The
line-chart trio (`#B3372A`, `#C8860A`, `#8A5A2B`) **failed four of the
validator's five checks**, including the hard one: normal-vision ΔE 10.1
between pain and coffee — those two lines were hard to tell apart with *full*
colour vision, not merely with a CVD. Both now draw from one set:

| mode | steps | surface | deutan ΔE | tritan ΔE | normal-vision ΔE | contrast |
|---|---|---|---|---|---|---|
| light | `#B3372A` `#6A3FA8` `#B57B08` | `#FCFCF9` | **11.1** | 14.1 | **16.0** | all ≥ 3:1 |
| dark | `#C1443A` `#7A5FD0` `#B58E08` | `#18211F` | **11.2** | 15.9 | **18.5** | all ≥ 3:1 |

All five checks pass in both modes. Coffee moves from brown to violet:
**brown cannot pass** — it is dark orange, the same hue family as both the red
and the amber, so deutan collapses it into one of them at any lightness.
Identity is never colour-alone anyway (legend plus shaped lane markers).

**Measured coverage.** 94 text/background pairs across the four pages, both
modes: **0 failures**, worst light 4.48:1 (large text, floor 3:1), worst dark
6.24:1. Marks and borders are excluded on purpose — they are governed by the
data-viz validator's ≥3:1-vs-surface rule, not by a text floor. `--teal2` is
listed nowhere in that table because it is only ever a bar fill and a gradient
stop, never a colour on text.

## Design decisions and why

**Expected doses are computed, never pre-created.** No nightly job writes
"today's doses" rows in advance. A row in `doses` means something actually
happened. If doses were pre-created, an unlogged day would be indistinguishable
from a day the job failed to run, and every schedule change would need a
backfill of future rows.

**A skip is a real row, not an absence.** `status='SKIPPED'` is stored. "I
deliberately did not take this" and "I have not logged yet" are different
clinical facts and must not collapse into the same blank.

**The service worker caches nothing.** It exists only because a PWA needs a
fetch handler to be installable, and it actively clears any cache a previous
version left behind (`pwa.py`). A cached medication log is a hazard — showing a
stale "taken" against a dose not actually taken is worse than showing nothing.

**Scheduled medicines are hidden from the extras row** (v3.4.0). An expected
dose belongs on the dose card. Listing it in both places invites logging it in
the wrong one, which then reads as an extra dose that never happened. A "Show
all medicines" button reveals the full list for the genuine case: an unplanned
extra dose of a regular medicine. Backed by `meds` (unscheduled) and `meds_all`
from `/api/now` — currently 15 and 22 respectively.

**A titrating medicine is scheduled *with* dose variants** — one MORNING line
carrying three strengths, rather than three lines or one fixed strength. A
titrating medicine is still a scheduled medicine; the dose is picked at log
time from the variant chips rather than by editing the regimen every time. The
picker is multi-select, because a combination of two strengths is one dose, not
two. Actual strengths are in `regimen.local.json` (gitignored).

## Deployment runbook
Concrete paths and commands are in `gutlog/INFRA_GutLog.local.md` (gitignored).

1. WinSCP upload to the app directory (never terminal paste for multi-line files)
2. Patch `app.py` via its versioned patcher — `--check` first, then apply
3. Restart the service
4. Run `test_phase_a.py` on the server → must be **18/18**, and
   `test_phase_b.py` → must be **16/16**
5. Verify against real data, not just the fixture — `test_phase_a.py` builds its
   own database and never touches the live one
6. OLS reverse proxy → loopback port · CyberPanel SSL · DNS A record

**Order matters when a companion app is in the same release.** GutLog's home
banner reads RxGuard's `/api/feed/status`, so for the v3.18.0 / RxGuard v1.7.0
pair, **deploy RxGuard first**. The other way round gives a quiet-looking box
around an inflated count — the worst of both changes at once.

## Environment (verified on server, 2026-09-10)
Python 3.9.25 · SQLite 3.34.1 · Flask · gunicorn, 2 sync workers · HTTPS live
via OLS reverse proxy.

## Test evidence
> **Run the two negative controls one at a time.** Both reconstruct the
> previous build to the same path, `gutlog/_nc_prev.py` beside `app.py`
> (`tools/NEGATIVE_CONTROL.py` requires it there so the suite's own imports
> resolve), and each deletes it on the way out. Running them concurrently
> pulled that file out from under the browser suite mid-run on 2026-09-15 —
> which aborts loudly, as designed, but wastes a fifteen-minute run.

- `test_trials.py` — **9/9 PASS** (2026-09-20, v3.26.0), on Windows and on the
  server; **13 declared new assertions, 13 seen to fail** (9 by version, 4 by
  mutation). Invented foods throughout. Asserts what a bad start is refused
  and that only one trial per food runs at a time; that meals are linked by
  name with the forms counted; that a context day and an empty day are set
  aside **on both sides** of the comparison; eaten-or-day-after against other
  logged days; that a short trial says "not enough days yet" while starting a
  recipe trial marks the recipe On trial; that his verdict updates the food
  map for **every** matching food and the recipe's stage; that an old one-day
  test moves into a trial **once** and a second run creates nothing; and that
  the segment leads with trials while the API needs a login. **Case 9 drives
  real Chromium.** The four mutations are the flattering ones — counting
  context days, counting empty days as symptom-free, dropping the day after,
  and printing a verdict word on too few days.
- `test_plan.py` — **9/9 PASS** (2026-09-20, v3.25.0), on Windows and on the
  server; **12 declared new assertions, 12 seen to fail** (9 by version, 3 by
  mutation). Invented foods and a **fixed past week**, so the result cannot
  drift with the real diary. Asserts that with no plan file the card is off;
  today's totals with calcium scaled **by quantity** (half a portion counting
  half) and unknowns named; the week's plant count including recipe plants
  with a spice at a quarter, and last week not counted; the same dal two days
  running flagged while two different ones are fine; the same sabzi at the
  limit on one day and over on the next, with the previous Sunday correctly
  outside the week; a rule falling behind reading *due* midweek, *have it
  today* on the last chance and *short* afterwards — **never "on track"**; and
  the daily and weekly limits. **Case 9 drives real Chromium.** The three
  mutations are the quiet ones: counting a spice whole, ignoring quantity when
  totalling calcium, and getting the timing of a "have it today" wrong.
- `test_recipes.py` — **7/7 PASS** (2026-09-20, v3.24.0), on Windows and on the
  server; **9 declared new assertions, 9 seen to fail** (7 by version, 2 by
  mutation). The fixture uses **invented cards** and runs the **real seeder**,
  not a stand-in for it, so the import path under test is the one that ships.
  Asserts that the dry run writes nothing and `--apply` loads the cards and
  their library items; that the list carries group, flags and stage; that a
  card opens in full with ingredients, method and the onion-free version;
  that **his stage survives a re-seed** while the card's content is refreshed;
  that a serving logs as an ordinary meal (half a serving → a Snack at the
  right protein); and that the segment exists while the API needs a login.
  **Case 7 drives real Chromium**: search, open, onion-free version, method,
  share, no page errors. The two mutations are the ones that would still look
  right — the Meals Save bar left sitting over the recipe book, and any stage
  string being accepted.
- `test_day_context.py` — **7/7 PASS** (2026-09-20, v3.23.0), on Windows and on
  the server; **9 declared new assertions, 9 seen to fail** (7 by version, 2 by
  mutation). Asserts that the card offers the six circumstances and a fresh day
  is unmarked; that a tap marks, a repeat is harmless and a second tap clears —
  **never a duplicate**; that yesterday and today are kept apart; that an
  unknown tag and a future day are refused; that the read-only feed carries the
  marked days and **refuses without the token**; that the card sits above Down
  day and the API needs a login. **Case 7 drives real Chromium**: one tap saves
  a mark today, Yesterday shows its own, no page errors at 390px. The two
  mutations are the ones that would still look right — Yesterday quietly
  pointing at today, and any tag being accepted.
- `test_meal_cards.py` — **12/12 PASS** (2026-09-20, v3.22.0), on Windows and
  on the server; **14 declared new assertions, 14 seen to fail** (12 against
  the reconstructed v3.21.0, 2 by mutation). The fixture uses **invented
  foods** and a scratch card file, so no assertion can pass by accident on his
  real diet. Asserts that the cards come from the meal file and name what the
  library lacks; that one tap logs the card as it stands; that choices, half
  counts and "last time" are remembered; the onion switch and a deselected
  choice; that **a missing food never breaks a log**; that Edit keeps the
  original time while rewriting what was eaten; Again; Delete; a dish by name
  only becoming an estimated item and being reused next time; that search puts
  what was eaten recently first; and that the Now tab carries the card.
  **Case 12 drives real Chromium**: one tap logs breakfast "same as last
  time", a new dish is added and logged, zero page errors, no sideways scroll.
  The two mutations are the errors that would still look right — the egg count
  ignored by the chosen style, and Edit stamping *now* instead of the logged
  time.
- `test_one_dose.py` — **5/5 PASS** (2026-09-20, v3.21.0), on Windows and on the
  server under Python 3.9; **4 declared new assertions, 4 seen to fail** — two
  against the reconstructed v3.20.0, and two by deliberate mutation because
  they guard *boundaries* rather than a feature, and v3.20.0 would have failed
  them for the wrong reason. The mutations widen the link window to the whole
  day, and relax "all the ingredients" to "any shared ingredient". The suite
  uses **invented molecules**, not his, so it names nothing. Asserts that a
  chip still writes a dose when none is logged; that a chip on a second symptom
  links to the dose already there and writes no row; that 7.5 hours later is a
  new dose; that a combination is *not* absorbed into a dose missing one of its
  ingredients; and that the page carries the strip, wired to both the pain tile
  and the down-day card.
  `test_phase_i.py` case 06 was **moved to yesterday** in the same release: its
  old premise — "a second chip a minute later writes another row" — is exactly
  the defect, so it had been asserting the bug. Rewritten, not deleted.
- `test_phase_o.py` — **10/10 PASS** (2026-09-20, v3.20.0), on Windows and on
  the server under Python 3.9; **10 declared new assertions, 10 seen to fail**,
  three by deliberate mutation of the current build. Asserts that seven
  malformed link and SOS calls are refused; that a dose by strength comes out
  of the linked packs — `145` → one, `290` → **two of the 145 pack**, and
  `145 + 72` → **one from each**, with undo restoring both; that the variant
  row names where it is counted while the pack is ordinary per-dose stock;
  that a linked pack rides the monthly order at `use × 40` with keep as the
  floor; that linked and scheduled medicines **never** reach the SOS list;
  that an SOS medicine is raised only below a third of keep (5/15 fine, 4/15
  low; 1/1 fine, 0/1 low); that an SOS order is raised once, shows as ordered,
  and a fresh one starts after receipt; that **clearing the links takes the
  packs back off the monthly order**; and that both new routes need the login.
  The three mutations are the errors that would still have produced a
  plausible list: charging only the first strength of a combined dose,
  raising an SOS medicine as soon as it is under keep rather than under a
  third of it, and dropping `@login_required` from the SOS save route.
- `test_phase_n.py` — **14/14 PASS** (2026-09-19, v3.19.0; cases 05, 06 and 10
  rewritten for v3.20.0 because SOS medicines left the monthly order — the
  semantics genuinely changed, so the cases were rewritten rather than
  loosened, and `new_assertions_v3190.json` renames case 06 to match).
  the server under Python 3.9; **14 declared new assertions, 14 seen to fail**,
  three of them by deliberate mutation of the current build rather than by
  version, because those three are the ones that would be cheapest to get
  wrong while still passing: ordering a flat target instead of a top-up
  (`need = target - expected` → `need = target`), forgetting the days a filled
  pillbox still holds, and serving the plan without a login. Asserts that ten
  malformed pack / days / order / receipt calls are refused including an empty
  order; that the count runs from the stock expected on the 1st and rounds up
  to whole packs; that 45 units on the 1st orders nothing while 35 orders one
  strip and not a flat month; that a 7-day pillbox fill leaves the expected
  figure identical; that an SOS medicine tops up to its keep figure; that a
  medicine with neither schedule nor keep is ordered from its 14-day use; that
  uncounted, variant and stopping-this-month medicines are each **named with
  the reason**; that Send saves once per month and clears the due flag; that
  received adds every line exactly once and undo takes it all back; that the
  day setting moves the target; that the due window is the last seven days of
  28-, 30- and 31-day months and that December orders January; and that the
  whole build parses as 3.9 with Jinja-clean order JS.
- `test_ui_order.py` — **ALL PASS, 17 checks** (offline Chromium, 2026-09-19,
  v3.19.0). Runs the page's own JavaScript: the Order banner on Now, the tap
  through to Stock, the card listing whole strips, Send opening `wa.me` with
  the order text and the order being saved, Order received moving stock
  10.0 → 60.0 and the buttons swapping to Undo, the Pack form saving size,
  type and keep, and an SOS medicine joining the plan the moment it has a keep
  figure. Also: no sideways scroll at 360px in **both** light and dark, and no
  page JS error. Run this before shipping any patch that touches the order or
  stock script — `test_phase_n.py` never runs page JS (CLAUDE.md §2, gap 7).
- `test_watch_tiles.py` — **7/7 PASS** (2026-09-15, v3.18.0), and 7/7 under
  `RUN_AT_TIME` at 00:02, 05:05 and 23:58; **7 declared, 7 seen to fail**.
  Server side only: it hands `/api/watch` a FitLog answer directly rather
  than standing FitLog up, because the question is what the endpoint does
  with an answer. Asserts the footnote's wording and that it argues nothing
  about rhythm; that no tile with a null value, a null median and nothing
  running is sent; that the three metrics with nothing behind them are
  withheld **by name**; that the median-only metric IS sent, with its median
  and its `n`; that the tiles which do hold something are untouched; and that
  logging an operating day is what brings the load tile back — asserted as a
  *pair*, absent before and present after, because "it appears once something
  is logged" was true of v3.17.0 too. The login guard is mutation-controlled,
  and so is the withholding rule itself (widened from AND to OR, to prove the
  suite would notice a rule that threw away tiles that do hold something).
- `test_ui_now.py`, Watch and banner — **6 declared, 6 seen** against the
  reconstructed v3.17.0 and two deliberate mutations. The main watch fixture
  now carries `resting_hr` in the live median-only shape, so the tile's
  median, its window text and its *absence of a day label* are all asserted
  on the card rather than only in the payload; a second, sparser stub carries
  the v3.17.0 all-null `load_hours` to prove the card refuses to draw it even
  when the server does send one. The banner block stubs `/api/medstatus`
  twice, once with a RED and once without.
- `test_phase_m.py` — **19/19 PASS** (2026-09-14, v3.17.0), and 19/19 under
  `RUN_AT_TIME` at 00:02, 05:05 and 23:58; **18 declared new assertions, 18
  seen to fail** against the reconstructed v3.16.0. Half one runs GutLog
  alone with links off: the table and its UNIQUE day and *absence* of a
  temperature column; one tap → one row; a second tap correcting, not
  duplicating; three consecutive days as one run with positions 1/2/3; a
  one-day gap making two runs; a down day with no components; a temperature
  reaching `vitals` and `down_days` unchanged; an analgesic chip producing
  **exactly one** doses row and a re-save producing none; the third day
  carrying the protocol note verbatim and the second day not; the watch row
  marking exactly the marked days; backfill of a past day and refusal of a
  future one; unmark; the feed 401 without the token; each down day beside
  the day before with that day's load, exercise, doses and sleep; and the
  coped-versus-run observation carrying its n, with the whole payload
  asserted free of advice words and third-person pronouns. Half two runs a
  real FitLog beside it: the trend bar marked and the mean and low over the
  kept days only, the chip reaching `analgesic_log` once and a re-save not
  mirroring again, and the view seeing 6000 steps before / 100 on the day.
- `test_ui_now.py`, down-day block — **13 declared, 13 seen** against the
  reconstructed v3.16.0 (no `#nowDown`, so the guard emits every property
  as a failure rather than skipping). A route-hit counter on
  `/api/downday*` proves the tap went through the page's own fetch, not a
  service worker. Three harness lessons, all fixed in the suite: fixed
  sleeps read the card before a ~1 s round trip landed (replaced with
  `wait_for_function`); the earlier watch stub answers `/api/watch` for the
  rest of the session, so the down-day lane is asserted inside that stub
  with a `down` day of its own; and the records section leaves the page at
  `/?open=records`, whose deep-link handler re-opened Records after the Now
  click — the block now navigates to a clean `/`.
- `tools/NEGATIVE_CONTROL.py` + `new_assertions_v3160.json` — **18 declared,
  18 seen to fail** (2026-09-14, v3.16.0). CLAUDE.md rule 2a in force: an
  assertion is evidence only if it has been seen to fail. Ten assertions fail
  against the **reconstructed** v3.15.0 — the patcher's `--reverse` rebuilds
  it byte-for-byte, so no copy of the old code is kept anywhere — and eight
  guard properties v3.15.0 already had, so those are proved against
  **deliberate mutations** of the current app instead (`tiny`: a 13px string
  put back inside the Watch card; `wide`: `min-width:900px` on every card).
  The harness aborts loudly if a run does not finish, because a suite that
  dies part-way reports zero failures and would otherwise mark every
  assertion unseen for a reason unrelated to the assertions. Its first real
  catch was one of my own: a card-scoped contrast check that **passed**
  against v3.15.0 and was therefore not evidence; widened to the whole
  screen it failed there, and then found five genuine dark-mode faults in
  v3.16.0 (worst 1.12:1 — `.btn.primary` repainted to the card colour by a
  broader rule and then given dark ink).
- `test_ui_now.py` — **ALL PASS** (2026-09-14, v3.16.0), Chromium, offline.
  Now runs the readability measurements in **both themes**: nothing under
  14px, nothing scrolling sideways, and every text colour clearing 4.5:1
  (3:1 for large text) computed from what the browser actually painted, with
  the effective background resolved by walking to the nearest opaque
  ancestor. Sweeps six screens across 21 segmented sub-views at 390px and
  360px in light and dark. Also asserts the run leaves no secret- or
  live-data-shaped file beside `app.py` — it used to leave a real bearer
  token there.
- `test_phase_j.py` — **18/18 PASS** (2026-09-14, v3.13.0), **1/18 against
  v3.12.0** (the one pass is a check that skips without the local term list).
  Runs a real GutLog and a real FitLog on two loopback ports and builds the
  wearable tables by running the **real migration** rather than a copied DDL,
  so it cannot pass against a schema it invented. The fortnight is built with
  the awkward days on purpose: one with no watch data at all, one with
  `healthconnect` only, one where the two feeds disagree, an operating day, a
  day with pain and nothing else, and an epoch that **starts inside the
  window** so the band has a boundary to draw. Asserts properties, not counts.
  Covers: the feed bearer-gated and complete; **no INSERT, UPDATE, DELETE or
  commit in the feed block** (Phase J is a reading problem, and if that ever
  fails someone has solved the wrong one); direction against his own median
  with steps up, resting HR down and HRV up; no verdict word anywhere in
  payload or page; "not enough to say" distinct from "level"; a no-data day
  carrying `has_data` False and steps `None`; the larger feed winning *and*
  being named; both lanes marking exactly the right days; a pain day with no
  activity still on the chart; `01:41:29Z → 07:11` IST; no timestamp slicing
  in the page; the band with a real boundary; **no clinical term in either app
  file or in the suite**, checked against `tools/clinical_terms.local.txt`
  (56 terms) and skipped out loud where that file is absent; 6 h of load
  beside 35 exercise minutes and out of the median too; and degradation to a
  message that keeps GutLog's own rows. **Clock-independent** — 18/18 under
  `tools/RUN_AT_TIME.py` at 00:02, 05:05, 12:00 and 23:58.
- `test_phase_i.py` — **18/18 PASS** (2026-09-14, v3.12.0), **0/18 against
  v3.11.0**, so every case enters a v3.12.0 path (CLAUDE.md §2). Covers the
  migration (both columns + `schema_version`, checked by reading
  `schema_version`, never `systemctl status`), tile order with the hero first,
  the row shape of all nine tiles with L/R/both kept apart, below-the-knee
  accepted only on hip and glute tiles, treatments pipe-joined with an unknown
  chip dropped, **an analgesic chip writing exactly one `doses` row** with
  reason = the pain site and exactly one episode row, a dose still recorded
  when GutLog carries no such medicine, six refused payloads, the eased tap
  computing 95 min → `1 h 35 min` and short durations in minutes, the pain
  list and day view, the operating day stored as minutes but picked in hours,
  `ot_day` excluded from exercise minutes and reported as `load_minutes`,
  `ot_day` on `/api/feed/activities`, `valid_from` and the `ended` list on
  `/api/feed/stack`, 3.9 syntax with no PEP 701 f-string anywhere, the page
  rendering with the gut tiles still at two, the new JS free of Jinja tokens
  and brace-balanced, no medicine name baked into the page, and (case 17) the
  activity picker constants pinned so a change fails offline as well as in
  `test_ui_now.py`. The eased-tap duration is tested twice: once as a pure
  function on nine fixed durations either side of the hour boundary, once as a
  real round trip. **Clock-independent** — verified under
  `tools/RUN_AT_TIME.py` at 00:00, 00:02, 01:10, 05:05, 12:00, 18:30, 23:58.
- `fitlog/test_analgesic_mirror.py` — **8/8**, and **1/8 before**. Runs a real
  GutLog and a real FitLog on two loopback ports and proves the thing neither
  single-app suite can: one tile tap → exactly one `doses` row here and
  exactly one `analgesic_log` row there, carrying the score.
- `test_phase_b.py` — **16/16 PASS** (2026-09-11, v3.5.0): backfill (plain,
  variant, skip), future/bad-time refusal writing nothing, retime + audit row,
  no-op retime writes no audit, 7 guard cases, no move onto a logged day or
  outside the regimen, extras move freely, symptom/BP/meal retime, day view
  merge order and edited flags. Against v3.4.2 it scores 4/16, as it should.
- `test_ui_now.py` — real Chromium, offline only, and **no page JS errors**
  (rule 5b). **91 PASS / 0 FAIL at v3.13.0.**
  Two harness rules, both learned the hard way on 2026-09-14 and both now in
  its docstring:
  1. **The context must be created with `service_workers="block"`.** GutLog
     registers a service worker (`pwa.py`), and a fetch served through one
     never reaches `page.route()`. Without the block a stub silently never
     fires, the page gets the live answer, and the assertions test the real
     handler while looking like a pass. Proved with a hit counter: route hits
     `[]`, page rendered "not reachable". This is a property of the app, so it
     applies to every stub added here in future. Every stub now also asserts
     that its own route actually fired.
  2. **A block guarded by "is the card there?" must fail, not skip.** Guarded
     with a bare `if`, the Watch block ran against v3.12.0 and reported 76
     PASS / 0 FAIL — which reads as evidence and is the absence of it. When
     `#nowWatch` is missing it now emits all seventeen properties as named
     failures, the same treatment the operating-day tile already had. The
     names are kept in a list beside the block so both runs print the same
     seventeen lines. Strip retime, change-dose-keeps-time, day-view edit, backfill of
  both kinds; and from v3.12.0 nine checks on the **operating-day tile**: six
  tiles all closed, `ot_day` present and marked as load, the hours picker
  offering 2·4·6·8·10 and no minutes, no intensity row, the tile reading back
  in hours, and after saving — 30 exercise minutes against 480 load minutes,
  the entry flagged `load`, the row reading *Operating day 8 h on your legs ·
  load, not exercise* and never 480 min, and the header keeping the two apart.
  Against v3.11.0 the tile is absent, so the block reports eight named
  failures rather than a traceback.
- `test_phase_a.py` — **18/18 PASS** (2026-09-10, post-v3.4.0). Covers
  authenticated boot, empty schedule, regimen add, slot render, one-tap TAKEN,
  re-tap correction without duplication, skip-as-data, extra dose path, mistap
  undo, effective-dated schedule change, one-open-row constraint, stop-keeps-
  history, bristol column, vitals regression, PWA assets, page render, legacy
  endpoints, migrate fast path.
- `test_migration_v330.py` — v3.3.0 migration suite.
- Post-v3.4.0 verification against a **copy of the live database**: `/api/now`
  200 with 15 extras / 22 total and no scheduled medicine leaking into extras;
  bristol round-trip confirmed (`('Cramp', 4, '6')`); page renders 87,719 bytes
  with all six v3.4.0 changes present, no traceback.

## Known gaps

Stated plainly, because a gap that is not written down is a gap that gets
rediscovered the expensive way.

1. **Bristol was collected but silently discarded, v3.3.0 → v3.4.0.** The
   `episodes.bristol` column was added in v3.3.0 and the UI has offered the 1–7
   chips ever since, but the `/api/episodes` endpoint never included the column
   in its INSERT. Every episode logged in that window has no Bristol value and
   **the data is not recoverable** — it was never written. Fixed in v3.4.0.
   Blast radius is small: `episodes` currently holds 1 row, and it has no
   Bristol.

2. **`_migrate()` runs per request, not at startup.** It is called from `db()`
   (`app.py:306`), which is per-request via `g`. A `systemctl restart` alone
   therefore does **not** migrate — the first request after the restart does.
   Never conclude a migration ran because the service came back up.

3. ~~**`tidy_extras.py` writes UPDATEs without taking its own backup.**~~ —
   **CLOSED 2026-09-20.** It now takes a `sqlite3.backup()` of the database
   before `--apply` and **writes nothing if that backup fails**, which is the
   part that matters: a backup step that can fail silently is not a backup
   step. Still dry-run by default.

4. **`test_phase_a.py` test 12 checks the Bristol column exists, not that a
   value round-trips.** It would have passed 18/18 against the v3.3.0–v3.4.0
   discard bug — and did, every time it ran during that window. A green suite is
   only evidence for the code it actually executes (CLAUDE.md §2).

5. **Three medicines have no `molecule` mapped** — a laxative whose two
   formulations differ, a rehydration salt mix, and a live-culture supplement.
   All three are extras, and blank deliberately rather than by oversight:
   two are not single molecules at all, and guessing the third would put the
   wrong laxative into an interaction check. Anything keyed on molecule —
   RxGuard interaction checking in particular — will not see them. Names and
   full reasoning in `regimen.local.json` under `molecules_left_blank`.

6. **The systemd unit invokes gunicorn by an absolute path inside a virtualenv**
   rather than `python3 -m gunicorn`. This violates CLAUDE.md §3 and breaks if
   the venv is rebuilt or moved. FitLog and the newer units use the portable
   form. Exact `ExecStart` line in `INFRA_GutLog.local.md`.

7. **`test_phase_a.py` never runs the page's JavaScript.** v3.4.0 deleted
   `openRowActions()` and `openVariantPicker()` while keeping the calls to
   them; the suite stayed 18/18 while the variant row and every logged-row
   tap were dead on the phone. Covered from v3.4.1 by `test_ui_now.py`
   (offline, real Chromium, 16 checks from v3.4.2, fails on any page JS error) and by a
   defined-if-called check inside `patch_gutlog_v341.py`. Run
   `test_ui_now.py` before shipping any patch that touches the Now-tab script.

8. **The sleep tile and the activity rings on the Watch card are unverified,
   and deliberately untouched in v3.18.0.** The Apple Watch has sent nothing
   since 2026-09-13, so there is no data to check them against and anything
   written for them would be written blind. The four sleep-timing fields (dose
   time, lights-out, wake time, woke-early) are in the same position. Both wait
   for the feed to resume; do not "fix" them from a screenshot.

9. ~~**A medicine whose schedule uses dose variants is not stock-tracked**~~ —
   **CLOSED in v3.20.0 by the strength links.** Each strength label is linked
   to the pack it comes out of, the packs are counted as ordinary per-dose
   stock and ride the monthly order, and the variant row says where it is
   counted. The one medicine that was in this position is linked and tracked
   as of 2026-09-20. What is *not* closed: a variant medicine with **no links
   set** is still uncounted, and still says so rather than disappearing — so
   the gap becomes a setup step rather than a dead end.

10. **A received order closes that month.** Once *Order received* is tapped,
    that month's order is finished; a later top-up in the same month needs
    *Undo received* first. Deliberate — a month with two open orders has no
    single answer to "what was ordered" — but it is a real edge for a month
    where something runs out unexpectedly.

11. **Three suites cannot run on the Windows workstation, for POSIX reasons
    only.** `test_phase_c` (feed token mode 0o600), `test_phase_e` (profile
    mode) and `test_phase_g` (`import fcntl`) fail there on **every** build,
    v3.18.0 included — verified by running them against the reconstructed
    previous build, which fails identically. They are green on the server
    (20/20, 13/13, 14/14). `test_phase_h` needs Node, which is on neither
    machine. Do not read a Windows red on these four as a regression, and do
    not read their Windows absence as coverage either — gate on the server run.

12. **One GutLog medicine still has no molecule recorded** beyond the three in
   gap 5 — a rehydration sachet entered separately — so RxGuard cannot check
   it at all and reports it as "no molecule recorded". A one-line data fix in
   GutLog on the server, the owner's to make; it is not a code change and no
   patch here will do it.

## What's next
- **Phase C** — RxGuard interaction check across the live med stack (blocked in
  part by gap 5 — unmapped molecules are invisible to it)
- **Sleep tile and activity rings on the Watch card** — blocked on the Apple
  Watch feed resuming (gap 8)
- Stock, refill alerts, the monthly order and the SOS pipeline — **done**
  (v3.6.0, v3.19.0, v3.20.0). The twelve sheet medicines GutLog lacked were
  added and counted on 2026-09-20, and the variant medicine is linked. What
  remains is data he alone can supply: the GutLog medicines that are **not on
  the sheet** are still uncounted and read "not counted yet" until he counts
  them
- FitLog read-endpoint cutover — FitLog consuming GutLog data rather than
  duplicating it
- Cardiologist BP export from `vitals`

## Changelog
- **2026-09-24 v3.35.0 — meals and snacks. DEPLOYED 10:39 IST.** See the
  section above. `patch_gutlog_v3350_snacks.py`, 43 anchors, Jinja guard,
  reversible. Schema 3.3.6 → 3.3.7. New tracked files:
  `migrate_gutlog_v3350_snacks.py`, `snack_swaps.json`, `test_v3350_snacks.py`,
  `new_assertions_v3350.json`. `ops/health_mirror.py` gains meal rows and marks.
- **2026-09-24 record data, no code change (owner-authorised).** The new
  night medicine's entry carried a misspelt salt; the salt and the same
  spelling inside its display name were corrected, and its one logged dose
  relabelled to match (linked by `med_id` throughout, so no history moved).
  The two antispasmodic entries were checked: neither has a regular schedule
  line, so both stay as-needed and nothing was closed. Backup first:
  `health3.db.pre-owner-20260924-20260924_100008`. The GutLog feed now
  carries the correct salt, and RxGuard's reconcile view reads 0 stopped / 0
  missing.
- **2026-09-24 v3.34.0 — Week 0 from Meals, the Now page folds and reorders,
  PRN Add medicine to Salts. DEPLOYED 08:23 IST.** See the section above.
  `patch_gutlog_v3340_ftmeals.py`, 29 anchors, Jinja guard, reversible. No
  schema change. New tracked files: `migrate_gutlog_v3340_week0.py`,
  `test_v3340_ftmeals.py`, `new_assertions_v3340.json`.
- **2026-09-23 v3.33.0 — pain site, onset, one true-time day. DEPLOYED 21:48
  IST.** See the section above. `patch_gutlog_v3330_painsite.py`, 30 anchors,
  Jinja guard, reversible. Schema 3.3.5 → 3.3.6. `ops/health_mirror.py` gains
  a Food Test section.
- **2026-09-23 v3.32.0 — the food test. DEPLOYED 12:51 IST.** See the section
  above. `patch_gutlog_v3320_foodtest.py`, 8 anchors, Jinja guard,
  reversible. New tracked files: `seed_foodtest.py`, `test_v3320_foodtest.py`,
  `new_assertions_v3320.json`.
- **2026-09-23 v3.31.0 — foods by weight, a sourced table, every time
  editable. DEPLOYED 10:35 IST.** See the section above. `patch_gutlog_v3310_foodlib.py`,
  **41 anchors**, Jinja guard, reversible byte-for-byte. Schema 3.3.4 → 3.3.5,
  verified by reading `schema_version` after one `GET /login`. New tracked
  files: `food_table_usda.json`, `build_food_table.py`,
  `test_v3310_foodlib.py`, `new_assertions_v3310.json`.
- **2026-09-22 record data, no code change.** Three owner-authorised data
  changes, applied by `ops/apply_changes_20260922.py` (dry run by default,
  idempotent, `sqlite3.backup()` taken first ->
  `/root/backups/gutlog/health3-prechange-20260922_151209.db`). (1) The open
  MORNING row of a legacy cardiac medicine closed at 2026-09-21; its EVENING
  row had already been closed at 2026-09-11. (2) A bedtime medicine already in
  the catalogue but never scheduled was given an open NIGHT row from
  2026-09-20; the older lower-strength PRN entry was left untouched as
  history. Both writes mirror `api_schedule_close` / `api_schedule_post`
  exactly -- rows are closed, never edited in place, and `med_epoch` was
  bumped once (6 -> 7). (3) The report of 2026-09-11, held at
  `status='check'` with its values withheld because `patient_ok()` did not
  recognise the name printed on it, was released: 15 values transcribed from
  the printed report into `rec_labs` under the **existing** test names, units
  and sections so the trend series join up rather than starting a parallel
  one; `rec_docs` set to `filed` with a worker-style finding; and
  `patient_match` added to the gitignored `records_profile.local.json` so the
  same spelling is not held back again. Flags set from the ranges printed
  beside each value, nothing rounded or reclassified. No tracked file changed
  except this one. Suites re-run green after the writes (11/11, 8/8, 4/4,
  5/5) and `/healthz` still reads `ok 3.30.0`. Mirror re-run: labs 340 -> 355,
  medicine events 14 -> 16, `meds_now` still 6.
- **2026-09-20 v3.26.0 — food trials as periods. DEPLOYED 11:08 IST.**
  `app.py` sha256 `37ea8d23…`, 478,041 bytes on the server, **byte-identical to
  the repo build**; pre-flight confirmed `GUTLOG_V3250_PLAN` present, no
  v3.26.0 marker, and sha256 equal to the repo first. `--reverse` reproduces
  v3.25.0 byte-for-byte (`11a5090e…`). Rollback:
  `cp /root/gutlog/app.py.bak-v3260-20260920_110825 /root/gutlog/app.py`.
  `patch_gutlog_v3260.py`, **7 anchors**, Jinja guard. Database backed up
  before the patch (`health3.db.pre-v3260-20260920_110802`, integrity ok, 33
  tables) and again by the migration. **No `schema_version` bump** — `trials`
  is `CREATE TABLE IF NOT EXISTS` in `SCHEMA`; confirmed live: present with
  the expected columns, 3.3.4 unchanged, 33 → 34 tables, and **the 3 existing
  `foodtests` rows still exactly 3**.
  Server gates before the restart: **test_trials 9/9**, plan 9/9, recipes 7/7,
  day_context 7/7, meal_cards 12/12, one_dose 5/5, a 18/18, b 16/16, c 20/20,
  d 18/18, e 13/13, f 8/8, g 14/14, i 18/18, j 28/28, m 19/19, n 14/14,
  o 10/10, watch_tiles 7/7, `ops/test_sso.py` **11/11**. Offline: `test_ui_now`
  **196 PASS / 0 FAIL**, `test_ui_order` ALL PASS, negative control **13
  declared, 13 seen**.
  **The migration** (`migrate_trials.py`, dry run read first, its own backup)
  moved his standing one-day test into a trial: the dry run printed the single
  expected line, `--apply` created exactly one, and the spec was deleted from
  the server afterwards. The trial's note cites the three original 17-Sep rows
  and his own words verbatim, so the old record explains the new one rather
  than being replaced by it.
  **Live, read-only — the trial was left running** (GETs only, no verdict, no
  end): `/api/trials` 302 anonymous and 200 logged in with 1 active trial on
  **day 4 of 30**, 2 days eaten, two forms listed, the 14-day baseline at 8
  counted days against 4 trial days, 0 days set aside, and the signal
  correctly **"not enough days yet"** — the minimum is 8 a side and the trial
  has 4, which is the refusal working on live data rather than in a fixture.
- **2026-09-20 v3.25.0 — the diet plan in the app. DEPLOYED 10:56 IST.**
  `app.py` sha256 `11a5090e…`, 462,028 bytes on the server, **byte-identical to
  the repo build**; pre-flight confirmed `GUTLOG_V3240_RECIPES` present, no
  v3.25.0 marker, and sha256 equal to the repo first. `--reverse` reproduces
  v3.24.0 byte-for-byte (`f8ec9dbf…`). Rollback:
  `cp /root/gutlog/app.py.bak-v3250-20260920_105542 /root/gutlog/app.py`.
  `patch_gutlog_v3250.py`, **7 anchors**, Jinja guard. **No schema change at
  all** — `/api/plan` is a GET computed from logged meals, so there is nothing
  to migrate and nothing stored that can go stale. The database was backed up
  anyway (`health3.db.pre-v3250-20260920_105503`, integrity ok, 33 tables).
  Plan file `diet_plan.local.json` at mode 600, validated on the server before
  the patch: 7 targets, 10 rules, 71 foods, 23 quarter-weight items, 6 easy
  additions.
  Server gates before the restart: **test_plan 9/9**, recipes 7/7,
  day_context 7/7, meal_cards 12/12, one_dose 5/5, a 18/18, b 16/16, c 20/20,
  d 18/18, e 13/13, f 8/8, g 14/14, i 18/18, j 28/28, m 19/19, n 14/14,
  o 10/10, watch_tiles 7/7, `ops/test_sso.py` **11/11**. Offline: `test_ui_now`
  **196 PASS / 0 FAIL**, `test_ui_order` ALL PASS, negative control **12
  declared, 12 seen**.
  **Live, read-only:** anonymous `/api/plan` 302, logged in 200 with `on: true`
  for 2026-09-20; today's totals all zero because nothing is logged yet today,
  **`calcium_missing` empty**, the week at 7.0 points of its target from 7
  plants since 2026-09-14, one tip naming easy additions, and all 10 rotation
  rules reporting a standing. **The quiet branch is the one running**: only 2
  days this week carry a logged meal, so the four rules standing at "short"
  produce no suggestion — verified against the meals table rather than assumed
  from the suite.
- **2026-09-20 v3.24.0 — Recipes. DEPLOYED 10:42 IST.** `app.py` sha256
  `f8ec9dbf…`, 449,765 bytes on the server, **byte-identical to the repo
  build**; pre-flight confirmed `GUTLOG_V3230_CONTEXT` present, no v3.24.0
  marker, and sha256 equal to the repo before anything was copied. `--reverse`
  reproduces v3.23.0 byte-for-byte (`9e17fe3f…`). Rollback:
  `cp /root/gutlog/app.py.bak-v3240-20260920_104204 /root/gutlog/app.py`.
  `patch_gutlog_v3240.py`, **9 anchors**, Jinja guard. Database backed up
  before the patch (`health3.db.pre-v3240-20260920_104137`, integrity ok, 32
  tables) and again by the seeder. **No `schema_version` bump** — `recipes` is
  `CREATE TABLE IF NOT EXISTS` in `SCHEMA`; confirmed live: present with the
  expected columns, 3.3.4 unchanged, 32 → 33 tables, and **`library` (160) and
  `meals` (9) untouched**.
  Server gates before the restart: **test_recipes 7/7**, day_context 7/7,
  meal_cards 12/12, one_dose 5/5, a 18/18, b 16/16, c 20/20, d 18/18, e 13/13,
  f 8/8, g 14/14, i 18/18, j 28/28, m 19/19, n 14/14, o 10/10, watch_tiles
  7/7, `ops/test_sso.py` **11/11**. Offline: `test_ui_now` **196 PASS / 0
  FAIL**, `test_ui_order` ALL PASS, negative control **9 declared, 9 seen**.
  **The seed** (`seed_recipes.py`, dry run read first, its own backup) printed
  exactly what was predicted — *52 cards, 52 new, 0 refreshed, 0 unchanged;
  library items to add: 0* — the zero being the evidence that v3.22.0's
  `seed_meals` had already put every one of them in the library, so this
  release adds the cooking and not a second copy of the food. Applied: 52
  added, 0 refreshed, library +0. The source file was deleted from the server
  afterwards.
  **Live, read-only — no meal logged and no stage changed** (GETs only):
  `/api/recipes` 302 anonymous and 200 logged in with **52 recipes, groups
  A 33 / B 12 / C 7, every stage `new`**; one card opens by slug in full
  (10 ingredients, 4 method steps, 2 notes, per-serving figures, plant points,
  the onion flags); the served page carries `meals-recipes`; no traceback.
- **2026-09-20 v3.23.0 — Day context. DEPLOYED 10:31 IST.** `app.py` sha256
  `9e17fe3f…`, 436,209 bytes on the server, **byte-identical to the repo
  build**; pre-flight confirmed `GUTLOG_V3220_MEALS` present, no v3.23.0
  marker, and sha256 equal to the repo before anything was copied. `--reverse`
  reproduces v3.22.0 byte-for-byte (`a8a31320…`). Rollback:
  `cp /root/gutlog/app.py.bak-v3230-20260920_103117 /root/gutlog/app.py`.
  `patch_gutlog_v3230.py`, **7 anchors**, Jinja guard. Database backed up first
  (`health3.db.pre-v3230-20260920_103056`, integrity ok, 30 tables).
  **No `schema_version` bump** — `day_context` and `day_context_note` are
  `CREATE TABLE IF NOT EXISTS` in `SCHEMA`; confirmed live after the restart:
  both present with the expected columns, **0 rows**, 3.3.4 unchanged,
  30 → 32 tables.
  Server gates before the restart: **test_day_context 7/7**, meal_cards 12/12,
  one_dose 5/5, a 18/18, b 16/16, c 20/20, d 18/18, e 13/13, f 8/8, g 14/14,
  i 18/18, j 28/28, m 19/19, n 14/14, o 10/10, watch_tiles 7/7, and
  `ops/test_sso.py` **11/11**. Offline: `test_ui_now` **196 PASS / 0 FAIL**,
  `test_ui_order` ALL PASS, negative control **9 declared, 9 seen to fail**.
  **Live, read-only — nothing was marked on his record** (GETs only, no POST):
  `/api/daycontext` 302 anonymous and 200 logged in, returning the six options
  with `tags` empty; `/api/feed/daycontext` **401 without the token** and 200
  with it, `days` empty; the Day context card is in the served page; no
  traceback; anonymous `/` still 302.
- **2026-09-20 v3.22.0 — meal cards on the Now tab. DEPLOYED 09:43 IST.**
  `app.py` sha256 `a8a31320…`, 430,912 bytes on the server, **byte-identical to
  the repo build**; pre-flight confirmed `GUTLOG_V3210_ONEDOSE` present, no
  v3.22.0 marker, and sha256 equal to the repo before anything was copied.
  `--reverse` reproduces the pre-patch build byte-for-byte. Rollback:
  `cp /root/gutlog/app.py.bak-v3220-20260920_094246 /root/gutlog/app.py`.
  `patch_gutlog_v3220.py`, **6 anchors**, Jinja-token guard.
  Database backed up before the patch (`health3.db.pre-v3220-20260920_094142`,
  integrity ok, 29 tables, 9 meals, 91 library items) and again by the seeder.
  **No `schema_version` bump** — `meal_meta` is `CREATE TABLE IF NOT EXISTS` in
  `SCHEMA`; confirmed live after the restart: present with the expected
  columns, 0 rows, `schema_version` still 3.3.4, and the 9 existing `meals`
  rows untouched in shape.
  Server gates before the restart: **test_meal_cards 12/12**, test_one_dose
  5/5, a 18/18, b 16/16, c 20/20, d 18/18, e 13/13, f 8/8, g 14/14, i 18/18,
  m 19/19, n 14/14, o 10/10, watch_tiles 7/7, j 28/28, and `ops/test_sso.py`
  **11/11** — the meal anchors do not overlap the SSO ones, and that was
  checked rather than assumed. Offline: `test_ui_now` **196 PASS / 0 FAIL**,
  `test_ui_order` ALL PASS, negative control **14 declared, 14 seen to fail**.
  **The seed** (`seed_meals.py`, dry run read first, `INSERT OR IGNORE`, its
  own backup): library **91 → 160**, 69 added, 0 already there, and the dry run
  ended on the line that matters — *every food the cards name is present*. The
  recipe file was deleted from the server afterwards; it was needed only for
  the seed. `meals.local.json` stays, mode 600, because the app reads it.
  **Live, read-only, nothing logged on his record:** `/api/mealcards` 200 with
  **6 cards and an empty `missing` list**, 18 rows across them, the onion
  switch on lunch and dinner only; the food search finds a seeded recipe; the
  served page carries `nowMeal`; no traceback; anonymous still 302. No meal was
  logged during verification — only GETs were issued.
- **2026-09-20 v3.21.0 — one tablet is one dose, and the 18-Sep correction.
  DEPLOYED 08:22 IST.** `app.py` sha256 `37fec194…`, 402,687 bytes on the
  server, **byte-identical to the repo build**; pre-flight confirmed
  `GUTLOG_V3200_PIPES` present, no v3.21.0 marker, and sha256 equal to the repo
  before anything was copied. `--reverse` reproduces v3.20.0 byte-for-byte
  (`7d801765…`). Rollback:
  `cp /root/gutlog/app.py.bak-v3210-20260920_082205 /root/gutlog/app.py`.
  `patch_gutlog_v3210.py`, **11 anchors**, no schema change at all.
  Database backed up before the patch (`health3.db.pre-v3210-20260920_082147`,
  `integrity_check` ok, 29 tables, 82 dose rows) and again by the correction.
  Server gates before the restart: **test_one_dose 5/5**, a 18/18, b 16/16,
  c 20/20, d 18/18, e 13/13, f 8/8, g 14/14, i 18/18, m 19/19, n 14/14,
  o 10/10, watch_tiles 7/7, j 28/28. On the PC: the same, plus
  `test_ui_now` **196 PASS / 0 FAIL** and `test_ui_order` ALL PASS. **The two
  suites the authoring sandbox could not run cleanly — phase_a and phase_m —
  are 18/18 and 19/19 here and on the server**, which is what the brief asked
  be confirmed rather than assumed. Negative control **4 declared, 4 seen to
  fail**, two by mutation.
  **Config, not code:** the combination chip was added to `pain_analgesics` in
  the server's gitignored `regimen.local.json` (read-modify-write, `.bak`
  first, every other key verified unchanged) and in the PC copy, then the
  service restarted a second time because `PAIN_ANALGESICS` is read at import.
  Verified live: all three chips resolve to real `prnmeds` rows by exact
  molecule — including the combination, which had **no chip at all** before
  and was therefore being entered as its two single ingredients.
  **The 18-Sep correction** (owner-confirmed, `correct_doses.py` + a gitignored
  spec): one as-needed combination tablet had been recorded as **five rows
  across three symptom entries**. The live rows were read out first and matched
  the spec exactly, every find resolving to exactly one row; then 4 dose rows
  deleted and 1 relabelled, with the 5 FitLog mirror rows treated the same way.
  Both databases backed up by the script, every old row appended in full to
  `/root/gutlog/corrections.log` (10 lines). Confirmed in `/export/doses.csv`:
  18 Sep now carries **one** as-needed row. The following day's identical-looking
  pair is **left exactly as recorded** — he does not remember that day, and a
  correction made from inference rather than memory is not a correction.
- **2026-09-20 v3.20.0 — two stock pipelines, the strength links, full stock.
  DEPLOYED 07:29 IST.** `app.py` sha256 `7d801765…`, 398,442 bytes on the
  server, **byte-identical to the repo build**; the pre-patch file was
  hash-checked against v3.19.0 (`9205fb73…`) on both machines first, and the
  reverse patch reproduces v3.19.0 byte-for-byte. Rollback:
  `cp /root/gutlog/app.py.bak-v3200-20260920_072826 /root/gutlog/app.py` — the
  new table simply goes unread by v3.19.0, nothing has to be dropped. Database
  backed up before the patch (`health3.db.pre-v3200-20260920_072811`,
  `integrity_check` ok, 28 tables), again by the merge, and again by the seeder.
  `patch_gutlog_v3200.py`, **17 anchors**, reversible, refuses Jinja-breaking
  tokens. **No schema-version bump** — `stock_links` is `CREATE TABLE IF NOT
  EXISTS` in `SCHEMA`; confirmed live by reading the database with Python
  `sqlite3` after the restart: present with the expected columns, 29 tables,
  `schema_version` still 3.3.4.
  Server gates before the restart: **phase_o 10/10**, **phase_n 14/14**,
  a 18/18, b 16/16, c 20/20, d 18/18, e 13/13, f 8/8, g 14/14, i 18/18,
  j 28/28, m 19/19, watch_tiles 7/7. Offline: `test_ui_now` **196 PASS /
  0 FAIL**, `test_ui_order` ALL PASS (22 checks — it now walks the SOS card,
  the Low banner and the Link strengths form as well), negative control
  **10 declared, 10 seen to fail**.
  **The seed was rehearsed on a throwaway copy of the live database first**,
  because the dry run *cannot* show the links resolving: the packs they link to
  do not exist until the adds land, so the dry run prints "pack not in GutLog —
  skipped" for all three and would have looked like a failure either way. The
  rehearsal showed all three links resolving and a second run adding nothing.
  Then live: **12 medicines added** (every sheet row GutLog lacked, each
  checked against the live catalogue first for the same product at the same
  strength), 12 counted from the sheet, 23 medicines now counted out of 36.
  The one duplicate entry — two rows for the same product at the same
  strength, differing only in case — was merged first with
  `merge_duplicate_med.py`: no schedule clash, 0 doses and 0 stock events to
  move, the empty one retired `active=0` rather than deleted. The two seed files were deleted from the
  server afterwards (CLAUDE.md §5d).
  **Live after the seed:** the October order carries five lines, four
  `basis=schedule` and one `basis=linked`; the running-low list is **empty**
  and nothing is named as un-orderable, where v3.19.0 had three; the variant
  row reads "strengths vary — counted on …" and names both packs.
- **2026-09-19 v3.19.0 — the monthly medicine order. DEPLOYED 21:12 IST.**
  `app.py` sha256 `9205fb73…`, 384,632 bytes on the server, **byte-identical to
  the repo build**; the pre-patch file was hash-checked against the
  reconstructed v3.18.0 (`6fee0b4a…`, 366,177 bytes) on both machines before
  anything was written, and the reverse patch reproduces v3.18.0 byte-for-byte.
  Rollback: `cp /root/gutlog/app.py.bak-v3190-20260919_211238
  /root/gutlog/app.py`. Database backed up before the patch
  (`health3.db.pre-v3190-20260919_211202`) and again by the seeder before it
  wrote (`health3.db.bak-seedorder-20260919_211601`).
  `patch_gutlog_v3190.py`, **13 anchors**, reversible, refuses Jinja-breaking
  tokens in the new text. **No schema-version bump** — the two new tables
  (`stock_order_cfg`, `stock_orders`) are `CREATE TABLE IF NOT EXISTS` in
  `SCHEMA`, so they arrive on the first request like `edits` did; confirmed by
  reading the live database with Python `sqlite3` after the restart: both
  tables present with the expected columns, 0 rows, `schema_version` still
  3.3.4, no existing stock figure changed. No column changes.
  Server gates before the restart: **phase_n 14/14**, a 18/18, b 16/16,
  c 20/20, d 18/18, e 13/13, f 8/8, g 14/14, i 18/18, j 28/28, m 19/19,
  watch_tiles 7/7. Offline gates: `test_ui_now.py` **196 PASS / 0 FAIL**,
  `test_ui_order.py` ALL PASS, negative control **14 declared, 14 seen to
  fail**, three of them by deliberate mutation. `test_phase_h.py` needs Node,
  which is on neither machine, and `test_migration_v330.py` targets the live
  database rather than a build — neither ran (gap 11). Live confirmation on
  the real data with a logged-in session: `/api/order` 200 for October 2026,
  40-day target, 12-day gap, `due` false (correct — the window opens on the
  24th); anonymous `/api/order` 302. Seeded from the interim master sheet with
  `seed_order_from_sheet.py` (dry run read first, one hand-written map entry
  for a strength the matcher normalises differently, its own backup taken):
  **11 medicines** given pack size, pack type and keep-on-hand, with a stock
  count set only where none existed — 12 sheet rows were left unmatched
  because GutLog has no entry at the same strength or the sheet row is a
  combination product, and 1 uses dose variants (gap 9). The seed data files
  were deleted from the server afterwards; they name medicines and live only
  on the workstation, gitignored (CLAUDE.md §5d). Order plan after the seed:
  5 lines, 3 named as not orderable with the reason.
- **2026-09-15 v3.18.0 — Watch tiles that hold nothing, the rhythm sentence,
  and the medicines banner. DEPLOYED 09:42 IST** (RxGuard v1.7.0 at 09:40,
  first). `app.py` sha256 `6fee0b4a…`, 366,177 bytes on the server,
  byte-identical to the repo build; the pre-patch file was hash-checked
  against the reconstructed v3.17.0 before anything was written. Rollback:
  `cp /root/gutlog/app.py.bak-v3180-20260915_094235 /root/gutlog/app.py`.
  **Migration verified by reading `schema_version` out of the database**, not
  from `systemctl` — `_migrate()` runs from `db()`, per request, so a green
  service says nothing. 3.3.4 before the restart, one unauthenticated `GET
  /login` (HTTP 200) to force a request through `db()`, 3.3.4 after: no schema
  change in this release, which is what was expected and is now evidence
  rather than assumption. Server gates before the restart: `test_watch_tiles`
  7/7, phase a 18/18, b 16/16, c 20/20, d 18/18, e 13/13, f 8/8, g 14/14,
  i 18/18, j 28/28, m 19/19. `test_phase_h.py` did not run: it is not
  deployed to the server and needs Node, which is not installed there; its
  only failing case offline is the Node one, on both builds. `test_ui_now.py`
  is offline-only by rule and was not run there. Live confirmation on the real
  feed: 4 tiles drawn, **no empty tile**, `load_hours` correctly withheld with
  no operating day logged in the window, and the footnote carries no rhythm
  claim. The medicines banner will render neutral — RxGuard now reports 0 RED. `patch_gutlog_v3180.py`, 9 anchors, reversible
  (reversing reproduces v3.17.0 at exactly 363,598 bytes, byte-identical to
  the pre-patch file). No schema change — 3.3.4 unchanged; the marker list on
  `SCHEMA_VERSION` gains `GUTLOG_V3180_HONEST`. Three changes: `/api/watch`
  withholds a tile with no figure, no median and nothing running today, and
  the card refuses to draw one anyway; a tile with only a median shows the
  median with its window named instead of "no data"; the footnote drops the
  rhythm claim; the medicines banner is neutral unless RxGuard has a RED.
  Gates: `test_watch_tiles.py` 7/7 and `test_ui_now.py` ALL PASS; negative
  controls **server-side 7 declared, 7 seen** and **browser-side 6 declared,
  6 seen**, five of the thirteen by deliberate mutation because they guard
  properties v3.17.0 already had. Sleep tiles and activity rings deliberately
  untouched (gap 8). Pairs with RxGuard v1.7.0, which is what makes the
  banner's RED count honest; deploy RxGuard first or the banner reads an
  inflated count from a neutral-looking box.
- **2026-09-14 v3.17.0 — Phase M: Down days. DEPLOYED 18:20 IST** (FitLog
  v1.6.0 at 18:19, first). `app.py` sha256 `86887cf6…`, 363,598 bytes,
  byte-identical to the repo build. `patch_gutlog_v3170.py`, 24 anchors,
  reversible (reversing reproduces v3.16.0 at exactly 337,053 bytes, sha
  `c67ae491…`). Schema 3.3.3 → **3.3.4**, verified live by `schema_version`
  after one request; `down_days` present with no temperature column and 0
  rows — nothing seeded. See *Down days — v3.17.0* above. Server gate before
  the restart: phase_m 19/19 (and 19/19 at 00:02, 05:05, 23:58 under
  `RUN_AT_TIME`), phase_i 18/18, phase_j 28/28, a 18/18, c 20/20, e 13/13,
  f 8/8, g 14/14. Negative controls: server-side **18 declared, 18 seen**
  against the reconstructed v3.16.0; browser-side **13 declared, 13 seen**.
  Case 19 of `test_phase_m` (no medicine name in the code) is deliberately
  **not** declared: it re-instantiates `test_phase_j` t14 over the new code
  and cannot be shown failing without naming a real term in a public file.
  Also this pass: the Phase L litter guard caught `test_phase_a.py` and
  `test_phase_b.py` still minting the feed token beside `app.py`; both now
  redirect it to their scratch directory.
- **2026-09-14 v3.16.0 — Phase L: readability fixes and app-wide dark mode.
  DEPLOYED 09:59 IST.** `app.py` sha256 `c67ae491…`, 337,053 bytes,
  byte-identical to the repo build. `patch_gutlog_v3160.py`, 33 anchors,
  reversible (reversing it reproduces v3.15.0 at exactly 280,199 bytes and
  sha `ea268dc4…`). `.card.fold .fs` 13.5px → **15px** as the Phase K brief
  had specified; both `.hint` declarations → **14px**; five light-mode
  contrast failures fixed at the token; dark mode across all four pages with
  a persistent three-state override; one validated categorical set replacing
  two unvalidated ones. New in the toolchain: `tools/NEGATIVE_CONTROL.py` and
  CLAUDE.md rule 2a — *an assertion is evidence only if it has been seen to
  fail*. 18 new assertions declared, **18 seen to fail** (10 against the
  reconstructed v3.15.0, 8 against deliberate mutations). Server suites
  after deploy: phase_a 18/18, phase_c 20/20, phase_e 13/13, phase_f 8/8,
  phase_g 14/14, phase_i 18/18, phase_j 28/28. `test_ui_now.py` ALL PASS
  locally under Chromium, both themes, at 390px and 360px.
- **2026-09-14 v3.15.0 — Phase K: Watch card correctness, then readability.
  DEPLOYED 08:24 IST.** `app.py` sha256 `ea268dc4…`, 280,199 bytes,
  byte-identical to the repo build. `patch_gutlog_v3150.py`, 8 anchors.
  Three correctness fixes — second person with a gate, cumulative-vs-settled
  declared once in `WATCH_KIND`, and every figure naming its day — then the
  measured readability pass above. Live immediately after: steps headlines
  **4,902 from 13-Sep, ↑ against a typical 930.5 over n=10**, with today's
  **284 reported separately and no arrow on it**. The invalid part-day
  comparison that started this is gone by construction rather than by
  threshold. `test_phase_j.py` **28/28** (22 before, six new), and 28/28 at
  00:02, 07:30 and 23:58 — 23:58 still headlines yesterday, which is the case
  that would break if anything reached for the clock.
  **Not run here:** `test_ui_now.py`, which needs Playwright; its Watch block
  gained six measured assertions (nothing under 14px, no sideways scroll at
  390px *and* 360px, every tile naming its day, hero full width with the rest
  two per row, no third-person pronoun rendered) and the stub payload was
  updated to the new shape.
- **2026-09-14 v3.14.0 — the watch strip falls back one day. DEPLOYED 07:43
  IST.** `app.py` sha256 `b4649026…`, 275,081 bytes, byte-identical to the
  repo build. `patch_gutlog_v3140.py`, 5 anchors. First real-use finding on
  the Phase J card: at 07:30 all five tiles read "no data" because the phone
  had not uploaded yet — correct behaviour at exactly the wrong hour. A
  metric with no figure for today now shows yesterday's, labelled; "no data"
  means neither day has one; the direction excludes the shown day from its own
  median; and every tile carries **n** so a thin baseline looks thin. No
  plausibility threshold, deliberately.
  Live immediately after: `exercise_minutes`, `resting_hr` and `hrv_ms` fell
  back to 13-Sep and were flagged stale, while `steps` showed a genuine 62 for
  today with `dir=down` against a median of 1,213.5 over **n=10** — the thin
  baseline, now visible as thin.
  `test_phase_j.py` **22/22** (18/22 against v3.13.0, the four new cases
  failing), and 22/22 at 05:05, 07:30, 23:58 and 00:02. One suite defect fixed
  while adding them: a case that deleted today's metrics and then failed left
  them deleted and poisoned case 15. Mutating cases now restore the fixture in
  a `finally`, so one real failure stays one failure. Rollback:
  `app.py.bak-v3140-20260914_074206` or
  `app.py.predeploy-v3140-20260914_074206`.
- **2026-09-14 v3.13.0 — Phase J, the watch display. DEPLOYED 05:41 IST.**
  `app.py` sha256 `88ed0d85…`, 273,810 bytes, byte-identical to the repo
  build. `patch_gutlog_v3130.py`, 6 anchors; FitLog v1.5.0 alongside it.
  A "Watch" card on the Now screen: today strip with directions against his
  own trailing median, a fourteen-day steps row with pain and operating-day
  lanes under it, workouts in real IST, the medication-epoch band drawn
  across the same row, and the source named on each day's figure. No
  readiness, recovery, battery or fitness-age verdict anywhere, and standing
  load is never folded into exercise. Nothing new is ingested: it reads
  FitLog's new read-only `/api/feed/watch` on the token that already existed.
  The epoch itself was created **on the server only** — the label is data and
  is not in this repository. Live after the deploy: the feed answers for the
  full fortnight, the two stored workouts read **21:58** and **07:11** IST,
  five days in the window carry no data and say so rather than showing zero,
  and the band covers 9 of 14 days so its boundary is visible.
  `test_phase_j.py` 18/18 (1/18 before) and at four times of day; the whole
  GutLog and FitLog gate green on the server before the restart. Rollback:
  `app.py.bak-v3130-…`, or `app.py.predeploy-phaseJ-20260914_053829` with
  `/root/backups/gutlog/health3.db.predeploy-phaseJ-20260914_053829`.
- **2026-09-14 v3.12.0 — DEPLOYED 04:40 IST.** `app.py` sha256 `f6c169ed…`,
  **260,086 bytes, byte-identical to the repo build** — the `newline=""`
  handling means the same patcher produces the same bytes on Windows and on
  the server. 26/26 anchors, compile check OK.
  **The migration behaved exactly as gap 2 says it does**, and this is the
  first time it has been watched happen: `schema_version` was still `3.3.2`
  immediately after `systemctl restart`, and only became `3.3.3` after one
  `GET /login`. Never conclude a migration ran because the service came back
  up. `episodes` now carries `treatments` and `radiates`; 8 episode rows and
  37 dose rows preserved. Both analgesic chips resolve **by molecule** to real
  `prnmeds` rows, so a tap writes a properly linked dose.
  `regimen.local.json` was merged rather than overwritten: a key-by-key
  before/after comparison showed **nothing lost**, `_meta` changed only by the
  documentation line, and `pain_analgesics` added.
  Suites on the server before the restart: `test_phase_i.py` **18/18** (and
  18/18 again at a faked 00:02, 05:02 and 23:02), phase A 18/18, B 16/16,
  **C 20/20**, D 18/18, **E 13/13**, F 8/8, G 14/14, plus the cross-app
  `test_analgesic_mirror.py` **8/8** against the two live-patched files.
  Rollback: `app.py.bak-v3120-20260914_044056`, or the pre-deploy pair
  `app.py.predeploy-phaseI-20260914_043720` and
  `/root/backups/gutlog/health3.db.predeploy-phaseI-20260914_043720`
  (`sqlite3.backup()`, integrity ok). The patcher is reversible: reversing all
  26 anchors reproduces v3.11.0 at exactly 241,645 bytes.
- **2026-09-13 v3.12.0 — Phase I: the pain entry surface.** A "Pain now" card
  of nine tiles (hero: both hips + anterior thighs), three questions a tile
  and no more, writing one `episodes` row with the new `treatments` and
  `radiates` columns — added through the existing idempotent `_migrate` ALTER
  pattern, `SCHEMA_VERSION` 3.3.2 → 3.3.3. An analgesic chip writes a real
  `doses` row with reason = the pain site **and** mirrors to FitLog's
  `analgesic_log` with `pain_at_time`; the chip labels live in
  `regimen.local.json`, never in this repository. An **eased** tap stamps
  `duration` from `etime` to now, so duration is measured rather than guessed.
  `ACT_KINDS` gains `ot_day` with an hours picker, kept out of exercise
  minutes by `LOAD_KINDS`. `/api/feed/stack` additionally reports each regimen
  line's `valid_from` and an `ended` list with `valid_to`, which is what lets
  RxGuard stop a medicine on the date GutLog ended it.
  `patch_gutlog_v3120.py`, 26 anchors. `test_phase_i.py` 18/18 (0/18 before);
  `test_ui_now.py` extended with nine checks on the operating-day tile.
  *2026-09-14:* both new suites were clock-dependent — literal times written to
  today, which GutLog rightly refuses before they arrive, so they were green
  after 18:00 and red at 03:48. Every such time is now derived from the clock
  and clamped to midnight, and `tools/RUN_AT_TIME.py` checks a suite at any
  hour. A suite that only passes in the evening is not evidence.
- **2026-09-13 — the dose export kept its status.** `export_csv()` now emits
  `day,dtime,medicine,status,reason,effect,notes` for the `doses` table. The
  `status` and `reason` columns had been missing, so a SKIPPED or EXTRA dose
  exported as an ordinary row and read as a dose taken — the export said the
  opposite of the record, silently, in the one artefact most likely to be
  carried to a consultation. Applied and verified live on the server; pulled
  back into the repo the same day (`app.py` sha256 `e6e2bc85…a256457e`,
  241,645 bytes).
  *Fixed 13-Sep:* this file's title had read **v3.5.0** since Phase B while
  the changelog had reached v3.11.1; the header now matches. The Register's
  "Where it lives" had also pointed at `drmanoj-clinic-automation` — the
  separate clinic repo CLAUDE.md says not to mix with this one — and now
  reads `drmanoj-health-systems`.
- **2026-09-12 v3.11.1 — the reader's schema.** Every uploaded PDF came back
  `reader error (BadRequestError)` and none were read. The file was never the
  problem: Sarvam rejects the whole extraction schema with `SCHEMA_INVALID`,
  400, before it looks at the document, unless the object inside an array
  carries a description of its own — descriptions on the properties within it
  are not enough. `LAB_SCHEMA`'s `results.items` had none. One line fixed it and
  the same report then read cleanly. Two guards added: `ocr_note` now keeps what
  the service actually said (a bare exception class name cost a round trip to
  the server to learn the schema was at fault), and `test_phase_g.py` walks
  `LAB_SCHEMA` and fails if any field or array item lacks a description.
  The first real report then read cleanly but was still held back with its
  values withheld: the lab printed **DR M K AGARWAL** and `patient_ok()` was a
  plain substring test for one given name. Both sides are now reduced to
  letters before comparing, so titles, initials and spacing no longer matter,
  and the parts of a name may appear in any order **only** when the profile
  lists two or more real words — one shared surname must never be enough to let
  a family member's report through. The name forms themselves stay in the
  gitignored profile file; this repository is public. `test_phase_g.py` 15/15.

- **2026-09-12 v3.11.0 — scan quality.** The scanner is the clinic's widget
  (S219 v2.3, the newest of the three versions), and it was tuned for pharmacy
  bills: half A4, large print, lying on a desk. A pathology report is the
  opposite, and two things went wrong on one. **Auto-crop:** the edge fit reads
  the surface from a ring round the frame, so when a page is held close enough
  to fill the frame there is no desk in that ring, the brightest "document"
  left is a block of printing, and the outline lands inside the page — on a
  synthetic close-held report the old build kept 67% of the ink and threw the
  rest away; and when it gave up instead, the 8% inset fallback cut past the
  margin into the text. **Shadow removal:** the flattening window was
  min-side/8, wider at report resolution than the shadow it is meant to remove;
  blank paper was divided by its own local mean, which turns paper grain into
  speckle; and the final stretch used min and max, so one staple set the black
  point and the print came out pale.
  GutLog's own copy (`/root/gutlog/scanner_widget.js`, served at
  `/scanner_widget.js` — the clinic's seven live surfaces read a different
  file and are untouched) now: probes for a visible border before believing
  the detector and keeps the whole photo when the page fills the frame; pads a
  believed crop by 1.5%; makes *Add this page* the whole page and the crop the
  small button; computes the illumination map on a small grid so 220dpi does
  not need a 75MB integral image; takes the paper level from the local
  *maximum* of that map rather than its average, which is what removes the
  pale halo a local mean leaves round every block of text; and clips the white
  point just under the paper level, which takes the grain off a shadowed
  corner instead of amplifying it along with the letters.
  Capture and save ceilings 1400/1600 → 2600 px (~110 → ~220 dpi at A4), JPEG
  0.85 → 0.92, upload ceiling 12 → 25 MB. Every knob keeps its old value when
  a host does not set it. Measured against ground truth in `scan_lab/`:
  ink kept 66.6% → 100% held close, 82.4% → 100% filling the frame; contrast
  in the shadowed half of a harshly lit page 64 → 180; blank-paper grain sd
  3.3 → 0.9; 118 → 220 dpi. `patch_gutlog_v3110.py` (3 anchors),
  `patch_scanner_report.py` (10 anchors), `test_phase_h.py` 17/17.

- **2026-09-11 v3.5.0 — Phase B.** Retime from the Now strip; *Day by day*
  card on Review (all streams, time order, tap to retime/delete); backfill of
  unlogged scheduled doses; `edits` audit table; server guards on day/time;
  change-dose now keeps the logged time (it restamped to now, which would
  have undone a retime); page reloads itself when reopened on a new calendar
  day (`todayISO` was fixed at load, so an app left open overnight logged the
  morning dose against yesterday). `patch_gutlog_v350.py`, 11 anchors.
- **2026-09-11 v3.4.2 — pain by site.** Two tiles on the symptom card, each
  with its own expanding 1–10 score, saved as separate episodes sharing time
  and Bristol (`patch_gutlog_v342.py`, 6 anchors). The patcher now also
  refuses Jinja tokens (`{#`, `{{`, `{%`) in new text: `APP_PAGE` is a Jinja
  template and a CSS `{#id` broke page render in the first build — caught by
  `test_ui_now.py`, invisible to `py_compile`. `test_ui_now.py` now 16 checks
  incl. undo of a wrong tick; `test_phase_a.py` 18/18.
- **2026-09-11 v3.4.1 — dose picker restored.** Tapping a scheduled row with
  dose variants did nothing, and tapping any logged row did nothing. Cause:
  `patch_gutlog_v340.py` replaced the Now-tab script block and dropped
  `openRowActions()` / `openVariantPicker()` while keeping their calls; the
  async tap handler swallowed the ReferenceError. `patch_gutlog_v341.py`
  re-inserts both verbatim from v3.3.3 (2 anchors) and refuses to write unless
  every Now-tab function called is defined. Reproduced on v3.4.0 and cleared
  on v3.4.1 by `test_ui_now.py` 6/6; `test_phase_a.py` 18/18. Gap 7 added.
- **2026-09-10 v3.4.0 — DEPLOYED.** Readability and structure pass
  (`patch_gutlog_v340.py`, 7 anchors). Scheduled medicines hidden from the
  extras row (`meds` / `meds_all`); extra chips reordered by use via
  `tidy_extras.py`; tap feedback and real Undo buttons; multi-select symptoms
  written as one episode each sharing timestamp, severity and Bristol;
  collapsible sections with summaries in the header; larger text, real buttons,
  warmer ground (`#E9EDE7`) replacing the bright `#EFF5F2`-on-white pairing.
  **Also fixed the Bristol discard bug** (gap 1). `test_phase_a.py` 18/18.
  Rollback snapshots taken for both the app file and the database; exact
  filenames in `INFRA_GutLog.local.md`.
- **2026-09-10 hardening (2)** — clinical detail removed from every tracked
  file. Regimen data (names, molecules, schedule, chip order) moved to the
  gitignored `regimen.local.json`; `add_regimen.py` and `tidy_extras.py` read
  it and refuse to run without it; `app.py`'s `PRN_SEED` and `DOCTOR_SEED`
  read it via `_local_seed()` and fall back to empty, so a fresh install
  starts with no medicines and no doctors rather than the owner's. Applied by
  `patch_redact_seed.py`. **Past commits still contain the names — history was
  deliberately not rewritten.** This stops future disclosure only.
- **2026-09-10 hardening (1)** — Flask `SECRET_KEY` rotated (logged out all
  devices, by design), infrastructure detail moved out of this file into the
  gitignored `INFRA_GutLog.local.md`, and `tools/NO_SECRETS.py` added: blocks
  databases, env files and credential literals, warns on clinical detail.
- **2026-09-10 v3.3.3** — row actions. Tapping an already-logged dose row opens
  an Undo / Change dose / Skip strip instead of toggling, so an accidental
  second tap cannot delete a medication record.
- **2026-09-10 v3.3.2** — dose variants. A scheduled medicine whose dose varies
  across three strengths offers a multi-select picker at log time.
- **2026-09-10 v3.3.1** — small-screen layout fix.
- **2026-09-10 v3.3.0** — Phase A quick-log surface: the Now tab, the
  `med_schedule` table with effective dating and epochs, PWA install support
  (`pwa.py`), `episodes.bristol` column. Migration `migrate_gutlog_v330.py`,
  suite `test_migration_v330.py`.

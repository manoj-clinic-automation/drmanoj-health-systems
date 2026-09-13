# GutLog — DOSSIER (v3.12.0)

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

The app directory also holds **two dead database files** from earlier versions.
`app.py:19` reads the `GUTLOG_DB` environment variable, defaulting to the
current database under the app directory. Confirm which file is live before
pointing any script at "the database" — this is the GutLog half of the
CLAUDE.md §4 lesson.

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

## Environment (verified on server, 2026-09-10)
Python 3.9.25 · SQLite 3.34.1 · Flask · gunicorn, 2 sync workers · HTTPS live
via OLS reverse proxy.

## Test evidence
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
  (rule 5b). Strip retime, change-dose-keeps-time, day-view edit, backfill of
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

3. **`tidy_extras.py` writes UPDATEs without taking its own backup.** Unlike the
   `app.py` patchers, it has no `.bak` step. Take a `sqlite3.backup()` copy of
   the live database before running it with `--apply`. It is dry-run by
   default, which is the only guard it has.

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

## What's next
- **Phase C** — RxGuard interaction check across the live med stack (blocked in
  part by gap 5 — unmapped molecules are invisible to it)
- Stock and refill alerts, driven by `prnmeds.stock` / `pack_size`
- FitLog read-endpoint cutover — FitLog consuming GutLog data rather than
  duplicating it
- Cardiologist BP export from `vitals`

## Changelog
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

# GutLog — DOSSIER (v3.5.0)

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
| `episodes` | Symptom events. id, day, etime, category, etype, side, severity, duration, notes, created, **bristol** |
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
- `test_phase_b.py` — **16/16 PASS** (2026-09-11, v3.5.0): backfill (plain,
  variant, skip), future/bad-time refusal writing nothing, retime + audit row,
  no-op retime writes no audit, 7 guard cases, no move onto a logged day or
  outside the regimen, extras move freely, symptom/BP/meal retime, day view
  merge order and edited flags. Against v3.4.2 it scores 4/16, as it should.
- `test_ui_now.py` — **27/27** in real Chromium (offline only), incl. strip
  retime, change-dose-keeps-time, day-view edit, backfill of both kinds.
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

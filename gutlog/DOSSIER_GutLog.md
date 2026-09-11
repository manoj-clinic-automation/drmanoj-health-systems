# GutLog — DOSSIER (v3.10.0)

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

## Stock and refill (Meds → Stock) — v3.6.0

Stock is **derived from events at read time**, never kept as a running number
— the same rule as expected doses. An undone dose therefore puts its tablet
back by itself, and a retimed dose moves its deduction with it.

- **Count** — what is left in the strips or bottle, *not* the pillbox. Every
  count is a fresh starting point; only doses and fills after it deduct.
- **Bought** — adds a pack (defaults to `pack_size`). Refused before a count.
- **Pillbox medicines** (scheduled, fixed dose — the default for those) come
  out of stock when the pillbox is filled: *Pillbox filled* deducts N days
  (default 7) of every counted pillbox medicine at its regimen rate. A dose
  taken from the pillbox does not deduct again; an *extra* dose of the same
  medicine does, because it did not come from the pillbox. *Undo last fill*
  removes exactly the last fill.
- **Per-dose medicines** (extras, PRN — and any medicine switched to per dose)
  deduct per logged dose, by the tablet count read from the dose text
  (`2 tab` → 2, `1/2` → 0.5; a strength such as `40 mg` reads as 1).
- **Variant-strength medicines are not tracked.** One count cannot stand for
  three strengths, and a wrong count makes the alert noise.

Alerts (a banner at the top of the Now tab, tapping it opens Stock):
- pillbox — RED when stock will not cover the next 7-day fill, AMBER when it
  covers only one more;
- per dose — RED under 3 days at the 14-day average use, AMBER under 7, and
  AMBER at zero even when rarely used (a PRN you need on hand).

## Vitals log (Review tab, second card) — v3.6.0

Blood pressure and pulse chart (faint guides at 140 and 90), averages for the
range and for morning (before 12:00) vs evening (after 17:00), highest and
lowest, every reading in a table, and a `vitals.csv` download. Follows the
30 d / 90 d / 6 mo selector. Entry is unchanged: BP on the Now tab, full
vitals under Log → Vitals.

## Feed for RxGuard and FitLog — v3.6.0

Two read-only endpoints for the companion apps, bearer-gated (not session):

| Endpoint | Returns |
|---|---|
| `/api/feed/stack?days=14` | Open regimen lines (name, molecule, slot, dose, variants) + per-medicine totals of what was taken (doses, days, last day) |
| `/api/feed/doses?since=YYYY-MM-DD` | Every non-skipped dose event since the date (clamped to 180 days) |

Old PRN-tab rows that carry only a medicine name are mapped to their
`prnmeds` row and molecule. Skips are never included. The token lives in
`feed.token` beside `app.py`, mode 600, **created by GutLog on first start**;
RxGuard and FitLog read the same file, so there is no token to copy. Rotate
by deleting the file and restarting all three services. The feed token
cannot write anything (test 19).

Consumers follow their **live database**: a scratch database outside the app
folder — every test suite — never reads the feed, so a test can never be
coloured by the real diary.

## Salts and medicine status — v3.7.0

**Meds → Salts** lists every active medicine, the ones needing a salt first.
Each row: salt (combinations joined with ` + `), strength, Save, and
*Not a single drug* for mixtures and supplements (stops the prompt). Typing
three letters asks NLM RxNorm for spelling suggestions (only the typed word
leaves the server, no token). A name like `Brand (salt 135)` or `Salt 20`
pre-fills a guess that is shown, never saved until Save. Adding a medicine
from the Now tab lands on Salts.

**Now banner** (`#nowMedStatus`): "N need a salt" → Salts; "N waiting for your
review in RxGuard" / "N interactions to review" → `rx.dr-manoj.in/kb`;
"RxGuard shows N RED" → As taken. RxGuard's counts come from its
`/api/feed/status` (GutLog's feed token, loopback, 5-minute cache). The stack
feed now carries `strength`, so RxGuard drafts use it.

## Activity card (Now tab) — v3.7.0

Tiles: Walk, Treadmill, Cycling (road), Cycling (static), Meditation. Tap a
tile → minutes chips (10–60) and talk-test intensity (Easy / Moderate / Hard;
none for meditation) → Save. Several a day, each with Undo; they appear in
Day by day and can be retimed or deleted there. Header: total minutes and
steps.

Watch data comes from FitLog (`/api/feed/activity?day=`, 60-second cache):
watch workouts show with ⌚; a watch workout of the same kind within 30
minutes of a tap shows **once**, as *watch-confirmed*, with the watch's
minutes and your intensity. Watch mindful minutes show only when no
meditation was tapped. FitLog down → the tapped entries still show, with
"Watch data not reachable right now". Deep link `/?open=act`.

Outward calls (RxGuard, FitLog, NLM) follow the live-database rule: a scratch
database never reaches out; `GUTLOG_LINKS=1/0` overrides.

| Endpoint | Purpose |
|---|---|
| `GET /api/salts`, `POST /api/salt`, `GET /api/salt/suggest?q=` | Salts segment |
| `GET /api/medstatus` | Banner counts |
| `POST /api/activity`, `POST /api/activity/undo/<id>`, `GET /api/activity?day=` | Activity card |
| `GET /api/feed/activities?since=` | Bearer feed for FitLog (day, time, kind, minutes, intensity) |

## Records (the Files tab) — v3.8.0

| Segment | What it shows |
|---|---|
| Summary | Medicines now (live regimen with salt and strength), as-needed use in 30 days, precautions, recent vitals, latest key results (lab flags in red), active and resolved problems, plan progress, documents still missing, link to the narrative record. Print / PDF. |
| Reports | Every report on one timeline by year; filter chips by kind; one-line finding (tap to expand); opens the original. Vault uploads appear as "to be processed". |
| Trends | Every laboratory value exactly as printed, the laboratory's own flag kept; key tests first; sparkline per test; tap for the full series with laboratory. |
| Plan | The investigation plan; Mark done / undo. |
| Upload · Labs · Consults | Unchanged (Vault renamed Upload). |

Content arrives through `import_records.py <folder>`: the folder is the
owner's medical-records folder with `records_manifest.local.json` in it
(docs to take, with date/kind/title/source/finding; lab values as printed;
plan; profile). Reports are copied into `uploads/` as `rec_<sha>.pdf`,
de-duplicated by content; lab values upserted by (date, test, lab); plan
status kept on re-import; the profile is written to
`records_profile.local.json` (mode 600). It prints counts only.
`/api/feed/profile` (feed token) returns condition codes only, for RxGuard.

## Scanner and inbox — v3.9.0

`/scan` hosts the clinic's shared scanner widget (`scanner_widget.js`, v2.3
from the clinic repository's S219 kit; the file sits beside app.py and is
served login-gated as `/scanner_widget.js`). The page sets the type and
report date, then the widget uploads each PDF to `/api/upload`. Buttons:
Records → Upload and Records → Reports. Deep link `/?open=records`.

Processing a batch: drag `/root/gutlog/uploads/inbox` to the PC's
`_PENDING_to_process`, say "process"; the reports are transcribed exactly
and a small manifest names each by its fingerprint (`sha`), so only the
manifest travels back. `import_records.py` files them from the inbox and
removes the inbox copies; the waiting list hides anything already filed.

## Automatic reading — v3.10.0

`records_worker.py` (venv python; started by each upload, and by cron
`*/15`) reads every waiting upload with Sarvam Document Intelligence
(`client.doc_ai.extract`, JSON schema: patient name, report date,
laboratory, document type, title, result rows with value/unit/range/flag,
impression). Filing rules: date from the report (day-first; future or
impossible dates fall back to the upload date); kind from the type; test
names matched through an alias table and the record's existing names;
flag = the laboratory's mark or a value outside the printed range; plan
items ticked by keyword; inbox copy removed. rec_docs.origin='auto',
checked=0 until "Looks right". Patient-name mismatch → status 'check',
values held back. Failures retry (max 3) with the reason shown under
Reports. `--probe` reports key and library without calling the API.

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
| `stock_events` | v3.6.0. med_id, kind (COUNT / ADD / FILL), qty, at (`YYYY-MM-DD HH:MM`), note (fill batch id) |
| `stock_meds` | v3.6.0. Per-medicine stock mode override (`pillbox` / `per_dose`) |
| `med_salts` | v3.7.0. med_id, strength, no_salt, updated (the salt itself stays in `prnmeds.molecule`) |
| `activities` | v3.7.0. id, day, atime, kind (walk / treadmill / cycle_road / cycle_static / meditation), minutes, intensity, notes, created |
| `rec_docs` | v3.8.0. day, kind, title, source, finding, stored, orig, sha (unique), status |
| `rec_labs` | v3.8.0. day, test, section, value (as printed), num (chart only), unit, ref, flag, lab; unique (day, test, lab) |
| `rec_plan` | v3.8.0. pos, test (unique), why, timing, status, done_day, note |
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
   `test_phase_b.py` → must be **16/16**, and `test_phase_c.py` → **20/20**
5. Verify against real data, not just the fixture — `test_phase_a.py` builds its
   own database and never touches the live one
6. OLS reverse proxy → loopback port · CyberPanel SSL · DNS A record

## Environment (verified on server, 2026-09-10)
Python 3.9.25 · SQLite 3.34.1 · Flask · gunicorn, 2 sync workers · HTTPS live
via OLS reverse proxy.

## Test evidence
- `test_phase_f.py` — **8/8 PASS** (v3.9.0): login-gated scan page and
  widget, fingerprint + inbox copy on upload, waiting list, processing by
  fingerprint clears the inbox, processed scan leaves the waiting list and
  opens as a record, buttons and deep link, missing widget file → 404.
- `test_phase_e.py` — **13/13 PASS** (2026-09-11, v3.8.0): import (duplicate
  content once, missing reported, profile mode 600), idempotent re-import
  keeping plan status, counts-only output, reports list with uploads as
  inbox, file serving behind login, trends (key first, printed values and
  flags kept), summary (live medicines, vitals, key results, problems,
  plan, narrative), plan guards, profile feed codes-only, page, no-profile
  and no-manifest cases, login on every records endpoint.
- `test_phase_d.py` — **18/18 PASS** (2026-09-11, v3.7.0): tables and the
  no-outward-call rule, salts list and banner count, save/normalise, 4 guard
  cases, Not a single drug, strength in the stack feed, 7 activity guards,
  add/order/undo, watch-merge rules, Day by day + retime audit + delete,
  activities feed (token, fields, read-only), page, and against a fake local
  server: RxGuard status, FitLog watch data, NLM suggestions without the
  token, caching, companions down, `GUTLOG_LINKS=0`.
- `test_ui_now.py` — 48 checks in real Chromium incl. banner → Salts, Save,
  Not a single drug, five closed tiles, minutes required, meditation without
  intensity, list and header, Undo, no page JS errors.
- `test_phase_c.py` — **20/20 PASS** (2026-09-11, v3.6.0): dose-text parsing,
  default modes, 7 guard cases, count/use/undo/bought, only after-count doses
  deduct, pillbox dose not double-counted, fill and undo-fill (two fills in the
  same second stay separate — found by this suite), both alert ladders, mode
  switch, page cards, token file mode, feed auth (no token / wrong / no
  Bearer → 401, token without login → 200), stack and doses shape, skips
  excluded, legacy rows mapped, since clamped, feed cannot write.
- `test_ui_now.py` — **36/36** in real Chromium (offline only), incl. stock
  count, pillbox fill, refill banner → Stock, vitals chart and averages.
- Full server-sequence rehearsal (2026-09-11): the exact VPS command block run
  against replicas of all three app folders — patch, all suites, restart,
  `verify_phase_c.py` 9/9 — then re-run to prove it idempotent.
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
- Phase D shipped in v3.7.0 with RxGuard v1.2.0 and FitLog v1.2.0. After the
  first source sync: fill Salts, then review drafts in RxGuard → Sources
  review. Watch data appears on the Activity card once Health Auto Export
  posts to FitLog.
- Phase C shipped in v3.6.0 with RxGuard v1.1.0 and FitLog v1.1.0. The
  cardiologist BP export was dropped by the owner: the Vitals log plus
  `vitals.csv` covers it.
- **RxGuard coverage** — several molecules in the regimen are not in RxGuard's
  knowledge base, so its As-taken page reports them UNKNOWN (sedatives among
  them, which means the sedation burden it shows is understated). Extending
  `knowledge/drugs.json` is curated, sourced clinical work — a separate job.
- Record a molecule for every single-molecule medicine (gap 5).

## Changelog
- **2026-09-11 v3.10.0 — reports read automatically** (Sarvam), filed with a
  machine-read mark. `patch_gutlog_v3100.py`, 14 anchors.
- **2026-09-11 v3.9.0 — the clinic scanner** at /scan, inbox for processing.
  `patch_gutlog_v390.py`, 9 anchors.
- **2026-09-11 v3.8.0 — Records.** Summary, Reports, Trends, Plan; import
  from a manifest kept outside the repo; profile feed for RxGuard.
  `patch_gutlog_v380.py`, 12 anchors.
- **2026-09-11 v3.7.0 — Phase D (GutLog side).** Salts segment, medicine
  status banner, Activity card with watch merge, strength in the stack feed,
  activities feed. `patch_gutlog_v370.py`, 22 anchors.
- **2026-09-11 v3.6.0 — Phase C (GutLog side).** Stock and refill (Meds →
  Stock, derived from events; pillbox vs per-dose; alerts + Now banner);
  Vitals log card; read-only feed for RxGuard/FitLog with a self-created
  mode-600 token. `patch_gutlog_v360.py`, 14 anchors. Shipped with RxGuard
  v1.1.0 (As taken) and FitLog v1.1.0 (W03 reads GutLog).
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

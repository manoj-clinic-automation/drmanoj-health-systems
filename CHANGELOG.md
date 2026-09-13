# Changelog — drmanoj-health-systems

Personal (non-clinic) systems. Per-app detail lives in each app's `DOSSIER.md`;
this file is the cross-app timeline.

## 2026-09-13 — Phase I: the pain entry surface, and closing the GutLog → RxGuard disconnect

Three apps, one change. **Not yet deployed** — patchers, suites and docs are
in the repo; the server is still on GutLog v3.11.1 / FitLog v1.3.3 /
RxGuard v1.4.0.

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

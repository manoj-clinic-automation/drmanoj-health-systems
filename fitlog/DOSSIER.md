# FitLog — DOSSIER (v1.6.0)

Single source of truth. Update after every change.

## Identity
- Personal physical capacity & recovery engine (spec: FITLOG_SPEC_v1.1.md)
- URL: fit.dr-manoj.in · Port: **8040** · Companion apps: RxGuard (8031), GutLog (8020)
- Repo: `drmanoj-health-systems/fitlog/`

## Architecture
- Single-file Flask (`app.py`, ~640 lines) + SQLite (`/root/fitlog/fitlog.db`)
- **Deterministic rule engine — no LLM in decision path.** All logic in `knowledge/`:
  - `rules.json` — F01–F11 + W01–W03, all thresholds tunable without code changes
  - `exercises.json` — 18 exercises (plan-tool field conventions), verdict templates
  - `protocols.json` — 14 pre/transit/post event protocols (OT, SOCIAL, TRAVEL car/flight short/long)
  - `med_stack.json` — seed only; live stack lives in DB, CRUD at /meds/manage
- Verdicts: GREEN / YELLOW / RED / RECOVERY / DELOAD / TRAVEL. Never zero.
  Precedence: RECOVERY > RED > TRAVEL > DELOAD > YELLOW-caps > GREEN.
- Every verdict displays fired rule chips (auditability, RxGuard pattern).
- Plan builder: deterministic day-ordinal rotation — same day always yields same plan; varies across days. F08 time-trim never changes verdict.
- Med logging: 2-tap (drug → dose), categories analgesic/sleep/other. W03 counts **analgesic category only**.
- Med epochs table = confound annotation for taper periods (charted in Phase 3 dashboard).

## Auth
GutLog v3.2 pattern: login password + separate owner key set at /setup (first run). Hashes in `settings` table.

The owner key is a **per-route `@login_required` decorator** (app.py:95), not a
`before_request` hook. Blueprint routes are therefore untouched by it — the
Phase 3.5 ingest endpoints carry their own bearer token and needed no exemption
patch. Verified on server 2026-09-10: `grep before_request app.py` → 0 matches.

### FITLOG_SECRET — set at last, 2026-09-20

`app.secret_key` read `FITLOG_SECRET` from the environment and, when it was
unset, **fell back to a value derived from the database path**. That fallback
is guessable by anyone who knows where the app lives, and it had been in force
since the beginning: the unit's `EnvironmentFile=-/root/fitlog/.env` was
optional and the file did not exist.

A real 64-character secret is now in `/root/fitlog/.env`, mode 600, which is
what the unit loads (`ingest.env` is read directly by the ingest blueprint and
is a different file — do not move the secret there). Setting it **changed the
session key, so FitLog signed out once**; that is the whole cost, and it was
worth it, because a guessable session key is exactly what an SSO ticket must
not be allowed to turn into.

### One sign-in across the three apps — HEALTH_SSO_V1 (2026-09-20)

FitLog joins the ring gutlog → rxguard → fitlog → gutlog. A plain page load
with no session asks the other two whether he is signed in there; the first one
that is mints a 60-second, single-use, single-app HMAC ticket and FitLog's
`/sso/in` signs him in exactly as its own password would. Nobody signed in
anywhere → FitLog's own login, in at most three redirects.

**It changes nothing else.** The ingest endpoints keep their own bearer tokens
and are never bounced — only page loads go round the ring. Logout sets a hold,
so a locked FitLog stays locked until its own password. With no
`/root/health-sso.key` the app behaves exactly as before.

**FitLog could not have joined this before today**, because of the fallback key
above: the whole ring is only as trustworthy as the weakest session it will
vouch for. Module `health_sso.py` beside `app.py`; patch
`ops/patch_sso.py --app fitlog` (4 anchors); suite `ops/test_sso.py`.

## Doses from GutLog — v1.1.0 (read-endpoint cutover)

Doses are logged in GutLog. FitLog reads them from GutLog's read-only feed
(`/api/feed/doses`, bearer token from `/root/gutlog/feed.token`, loopback,
2 s timeout, 60 s cache) and unions them with its own `analgesic_log`:

- **W03** counts distinct days across both sources — a day logged in both
  counts once. When GutLog contributed a day the flag text says *incl. GutLog*.
- A GutLog dose counts when its molecule appears in the `generic` of an
  active `med_stack` entry, and takes that entry's category (analgesic
  first). Unmatched molecules are ignored rather than guessed — W03 counts
  analgesics only. **Keep `generic` filled in /meds/manage** or GutLog doses
  cannot be matched.
- The Meds page shows the last 14 days from GutLog beside FitLog's own log,
  and the per-category day counts use both. FitLog's own tap-to-log is
  unchanged — the manual path stays as the fallback.
- The feed follows the live database: a scratch database outside the app
  folder (every test suite) never reads it, so the 53-check smoke suite is
  untouched on the server. `FITLOG_GUTLOG_FEED=1/0` overrides. GutLog down →
  FitLog-only counts, and the Meds page says so.

No rule threshold changed.

## Activity feed — v1.2.0

GutLog's Now tab carries the Activity card; FitLog supplies the watch side
and shows both on Home.

- `GET /api/feed/activity?day=` — GutLog's feed token (read-only, no login):
  steps, exercise minutes, mindful minutes (S01 best source) and that day's
  workouts from the best source only, each with `kind` from
  `classify_workout()` (walk / treadmill / cycle_road / cycle_static /
  meditation / other), start/end, minutes, distance.
- `health_ingest.py`: Health Auto Export `mindful_minutes` → `mindful_min`
  (context only, never rule-bearing); a workout the watch marks indoor
  (`isIndoor` or `location: Indoor`) is stored as "… (indoor)", so an indoor
  walk reads as treadmill and indoor cycling as static.
- Home → **Activity today**: steps and exercise minutes, ⌚ watch workouts,
  and what was tapped in GutLog (`/api/feed/activities`), a tap the watch also
  recorded shown once. GutLog down → watch data only, with a note. Follows
  the live-database rule (`FITLOG_GUTLOG_FEED`).

## Watch read feed — v1.5.0 (`GET /api/feed/watch?days=14`)

GutLog draws the watch screen; FitLog holds the data. This endpoint is the
whole of the contract between them and it is **read-only** — no new table, no
new ingestion, no new token. Bearer-gated on GutLog's existing feed token.

Per day in the window it returns every watch metric that resolved, each
**with the source that supplied it**, plus `has_data`. Both matter:

- steps arrive from two feeds and the larger wins, so a figure whose
  provenance is invisible invites the wrong conclusion on the day the two
  disagree. `WATCH_LARGER_WINS` states that rule once, in one place;
- `has_data` False is not a zero. A day the watch was not worn and a day spent
  resting are different facts, and a chart that conflates them lies about a
  rest day.

Workouts come back best-source-only with IST already applied **and the clock
time carried separately as `start_hm`**, so no consumer has to slice a
timestamp again — the 2026-09-13 UTC bug was a slice. Medication epochs
overlapping the window come too, because resting HR and HRV inside a drug
change are artefacts of the change.

Nothing here derives a verdict, and nothing here should start to.
`gutlog/test_phase_j.py` case 01 reads the block and fails on any `INSERT`,
`UPDATE`, `DELETE` or `commit(`.

## Down days out of the trend — v1.6.0

GutLog v3.17.0 marks **down days** (Phase M): the recurring cluster of hip
and thigh ache, left abdominal pain, fatigue, feverishness and a broken
night. On such a day steps fall to near nothing. An unmarked one read on the
Trend card as a low-steps day — that is, as non-adherence — which would have
quietly corrupted the one question the fitness plan exists to answer.

- `gutlog_downdays(since)` reads GutLog's read-only `/api/feed/downdays`
  with the feed token that already exists. Same rules as the dose reader:
  follows the live database (a scratch DB never reads it), 60 s cache, never
  raises — an unreachable GutLog is an empty set plus a reason.
- `w_trend_card` **draws** a down day's bar (hatched, class `wbar down`,
  named "down day (GutLog)" in its title) and **leaves it out** of mean, low
  and high, saying how many it left out. Not hidden: a down day is a fact
  about the day, and the bar stays on the chart so it can be seen for what
  it is. When GutLog cannot be read the card says so rather than silently
  including everything.
- `/api/feed/watch`: the day window is capped at **180** instead of 60, so
  GutLog's down-days view can put months of down days beside the day before
  each one; and `sleep_hours` joins the metrics it reports, so that view can
  show sleep from the Watch where GutLog's own log has none.

No rule reads any of this. `compute_flags()` is untouched; W02 adherence is
still counted from `sessions.status`, not from steps.

Proved by `test_downdays_trend.py` (5/5), which runs a real GutLog beside
the FitLog under test and asserts on the rendered `/watch`: the bar is
marked and named; mean **and low** are 6000 over the nine kept days with a
100-step down day present, and the note says one was left out; the feed
answers 180 days and carries sleep; and with GutLog down the page still
renders and says the down days could not be read. Every case fails against
the reconstructed v1.5.0 (`tools/NEGATIVE_CONTROL.py`, 5 declared, 5 seen).

## Analgesic mirror — v1.4.0 (`POST /api/analgesic`)

The **only** inbound write path for `analgesic_log`. GutLog calls it the moment
an analgesic chip is tapped on a Pain now tile, carrying `pain_at_time` = the
score entered at that same tap. The score is the whole reason it exists: the
dose feed (`/api/feed/doses`) can tell FitLog a drug was taken, never how bad
it was when he took it.

- **Bearer-gated on GutLog's read-only feed token** (`_feed_authorised`, the
  same gate `/api/feed/activity` uses), machine-to-machine, never session
  authenticated — CLAUDE.md rule 5. No new secret file.
- Payload: `dt` (`YYYY-MM-DDTHH:MM`), `molecule`, `name`, `dose_label`,
  `pain_at_time`, `notes` (GutLog puts the pain site here), `ref`.
- `_stack_match()` resolves the medicine: exact `generic` first, then a
  combination product carrying the molecule as a **whole component**, then the
  display name. Components, never substrings — a substring match would file a
  single molecule under the first combination whose name happened to contain
  those letters. No match → `{"ok": false}` with the reason, and nothing
  written. GutLog keeps its own dose row either way and reports what was not
  mirrored, so a failure here never loses the dose.
- **Idempotent** on (med_id, dt, notes): a retry after a timeout returns the
  first row's id rather than doubling the record.
- Ordinary GutLog doses still arrive through the read feed as before; this
  path is only for the tap that also carries a pain score.

## The operating day — v1.4.0

GutLog's `ot_day` activity ("Operating day", hours picker, stored as minutes)
arrives through `/api/feed/activities` with no new write path. `LOAD_KINDS`
keeps it out of the workout list: the Activity card renders it under
**Standing load** in hours, labelled *not exercise, and never counted as
exercise minutes*. Hours on his legs are a load that must be recorded, or
every walking-versus-pain comparison is confounded by his operating list.

## Wearable ingest (Phase 3.5)

Apple Watch (via iPhone / Health Auto Export) and Samsung Health (via Health
Connect) land here. Deterministic, source-tagged, no LLM in the path.
Blueprint `health_ingest_bp` in `health_ingest.py`, registered at app.py:17–20.

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/ingest?source=<src>` | POST | Bearer | Accept a payload; store metrics, workouts, raw body |
| `/api/health/daily?date=YYYY-MM-DD` | GET | Bearer | One day, S01-resolved |
| `/api/ingest/status` | GET | Bearer | Per-source last-seen / latest-date / row count, `stale` if >3d |

Tokens live in `/root/fitlog/ingest.env`, mode 600, read directly by the
blueprint — **not** via systemd (`fitlog.service` loads `.env`, which does not
exist). Rotate by editing that one file and restarting. Never in git.

Accepted `source` values: `applewatch`, `healthconnect`, `manual`. Anything
else → 400.

### ingest.env keys
| Key | Purpose |
|---|---|
| `FITLOG_INGEST_TOKEN` | Main bearer token. Header-only, all sources, read + write. |
| `FITLOG_HC_TOKEN` | HC Webhook URL token (`?k=`). **POST only, `healthconnect` only, no read.** |
| `FITLOG_DB` | Optional. Pins the database when it is not `fitlog.db`. |

### HC Webhook (Health Connect) — record-level ingest
HC Webhook cannot send custom headers, so its token rides in the URL as
`?k=<token>`. That token is deliberately scoped — it cannot POST as another
source and cannot read any endpoint. Verified in production: `401` on
`GET /api/ingest/status?k=`, `GET /api/health/daily?k=`, and
`POST ?source=applewatch&k=`.

HC posts snake_case arrays of **interval records**, delivered incrementally
with retries, rather than daily totals. Detected by `source=healthconnect`
with no `data` key; the legacy `{"data":{"metrics":[...]}}` shape from any
other HC exporter still routes to the original parser.

Records land in `health_hc_records` keyed on **`metric|start|end`** — the
interval, never the value — and upsert on conflict. Daily totals in
`health_metrics` are then recomputed as `SUM` over stored records. That is
last-write-wins per interval: idempotent under retry, correct under update.
Including the value in the key was a real bug in the first build — a
redelivered in-progress interval landed as a second row and `SUM` added both,
inflating `steps`. Regression-tested as R1.

Arrays: `steps`→steps, `distance`→distance_km, `active_calories_burned`→
active_energy_kcal, `total_calories_burned`→total_energy_kcal,
`floors_climbed`→flights. Anything else is ignored (still kept in
`health_raw`). Distance always converts from metres unless the record
declares km — a magnitude heuristic stored 50 m as 50 km. Regression-tested
as R2.

- `health_hc_records` — record_key (PK), date, metric, value, unit,
  ingested_at · created by `patch_hc_support.py`, not by the base migration

### iOS Health Webhook (Apple Watch) — v1.3.0
Health Webhook on iOS posts the same snake_case array style as the Android
app, not Apple's `{"data":{"metrics":[…]}}` shape, and it posts under
`source=applewatch`. The route branched on the source NAME, so an iOS body
went to the Apple parser, matched nothing, and stored zero metrics while
still landing in `health_raw`. Routing is now by payload **shape**:
`payload["platform"] == "ios"` selects `parse_ios_payload`.

**iOS timestamps are UTC.** `2026-09-08T18:30:00.000Z` is `2026-09-09
00:00` IST. Slicing the first ten characters filed every record a full day
early, silently. `_to_ist_date()` adds +05:30 before deriving the date.
A `+05:30` offset already in the string is taken at face value.
`activity_rings` carries its own local calendar date and is trusted as-is.

Arrays: `steps`→steps, `distance`→distance_km (metres), `active_calories`→
active_energy_kcal, `total_calories`→total_energy_kcal, `resting_heart_rate`
→resting_hr, `heart_rate`→hr, `heart_rate_variability`→hrv_ms. Summed:
steps, distance, both energies. Everything else averaged over the day.
`basal_metabolic_rate` is deliberately ignored — hundreds of records a day,
no rule uses it. `exercise` becomes `health_workouts` rows. Ring **goals**
(`move_goal_kcal`, `exercise_goal_min`, `stand_goal_hours`) are stored as
context-only metrics so the rings view has something to draw against — a
target is not a measurement and is absent from `RULE_BEARING` on purpose.

iOS record keys are prefixed `ios|` so they cannot collide with HC keys.

### health_hc_records carries a source — v1.3.0
The record table is now shared by two wearables, and the daily recompute
used to read `WHERE date = ? AND metric = ?` with no source filter. With
Watch and Health Connect records on the same date+metric that **summed
across sources**, writing the combined figure under whichever source posted
last — a direct breach of S01. Caught before deploy: the table held 129
healthconnect `steps` rows on 2026-09-10/11 and the first iOS payload
covered exactly those dates, which would have written 1723 steps for
09-10 instead of 2305 and 1206 as two separate rows.

`_ensure_hc_source_column()` adds `source TEXT NOT NULL DEFAULT
'healthconnect'` in place — additive, idempotent, correct for every row
that predates it — and the recompute filters by source.
`test_ios_source_isolation.py` seeds HC rows *before* posting iOS data and
fails 2/19 against the unfixed code. `test_ios_ingest.py` scores 18/18
either way: it runs on a fresh temp database with only iOS rows, so it
never enters the branch. Rule 2, again.

### Health Auto Export (Apple `data.metrics`) — v1.3.2
The iPhone moved from Health Webhook (ios snake_case) to Health Auto
Export on 2026-09-11. Auto Export posts the Apple `{"data":{"metrics":…}}`
shape, so it routes to `parse_payload`, not `parse_ios_payload`. Two
defects surfaced immediately.

**Energy arrives in kilojoules.** `active_energy` and
`basal_energy_burned` carry `units: "kJ"`. The canonical names end in
`_kcal` and the parser stored `qty` verbatim with the source unit, so
every energy figure was 4.184× too large and labelled kJ.
`_apple_convert()` now converts and relabels. Validated against the
retired ios feed: 521.8159 kJ ÷ 4.184 = 124.717, exactly the
`move_kilocalories` the ios ring reported for 2026-09-09, and 257.294
for 09-10.

**A day's samples overwrote each other.** `parse_payload` emitted one row
per sample and `store()` upserts on `(date, metric, source)`, so the last
sample won instead of the day's total. Replaying the real per-hour export
(`health_raw` id 22) through the unpatched parser stored, for 09-11:
steps **4.61** instead of 4914, stand_hours **1** instead of 19,
exercise_minutes **1** instead of 17. It never surfaced only because a
daily-rollup export landed three minutes later and overwrote the damage.
`_apple_aggregate()` now sums counts and durations and averages levels;
`_APPLE_SUM` / `_APPLE_MEAN` name every canonical in `METRIC_MAP`.

**Watch the loop variable.** `unit` in `parse_payload` is entry-level and
shared by every point. The first build of this patch rebound it, so the
first sample converted and every later one looked already-kcal and passed
through raw. Caught by `test_apple_aggregation.py`, not by review. Bind to
a fresh name.

### `apple_stand_hour` has no stood/idle flag — 19 is not a bug
Apple's `HKCategoryValueAppleStandHour` (`.stood` / `.idle`) is **not
exported**. Every per-hour sample is `qty: 1`; the daily rollup is the
pre-summed count. There is nothing to filter and no way to recover
"stood" from this feed.

2026-09-11 recorded hours 05:00–23:00 = **19 hours**, all confirmed by
`apple_stand_time`. The ios ring's 12 was a 16:56 snapshot, which is
exactly hours 05..16. **Stand hours can exceed the goal** — the ring caps
at 100%, the count does not. `test_apple_aggregation.py` asserts 19 stays
19, so a future "correction" to 12 fails the suite.

`exercise_minutes` and `flights` are unaffected: each
`apple_exercise_time` sample genuinely is one minute, so summing is right
(17 matches the ring), and `flights_climbed` carries a real count.

### One writer per source (2026-09-11)
Both formats wrote `(date, metric, 'applewatch')` with different
semantics — `store_hc` recomputed `stand_hours` = 12 from
`health_hc_records` while `store` wrote 19, last writer winning. Owner's
decision: **Auto Export wins.** The 604 ios-era `applewatch` rows were
deleted from `health_hc_records` (healthconnect rows untouched), along
with the ios-only measured metrics `move_energy_kcal` and `hr`. The three
ring **goals** are kept — nothing else supplies them and they are static.

**Open gaps from that decision:** `walking_running_distance` (units km)
is not in `METRIC_MAP`, so `distance_km` no longer updates and holds its
last ios value. `total_energy_kcal` is likewise orphaned and now
inconsistent with active + basal. Neither is displayed anywhere. Also,
`store()` replaces a day's value, so a *partial* export replaces a fuller
one — fine while Auto Export sends whole-day rollups, not fine if it is
ever set to incremental-only.

### Rule S01 — Source Precedence
`applewatch` > `healthconnect` > `manual`, per metric per date. Values are
**never summed or averaged** across sources. The phone feed can only fill a
date the Watch left silent; it cannot inflate a day the Watch already covered.

Rule-bearing metrics: `steps`, `exercise_minutes`, `stand_hours`, `flights`.
Context-only (`rule_bearing: false`): `resting_hr`, `walking_hr_avg`, `hrv_ms`,
`spo2_pct`, `resp_rate` — HR is unreliable during medication titration, so
aerobic intensity stays governed by talk-test only.

### Steps are the one exception to S01 — v1.3.3

S01 ranks sources by **sensor quality**, which is right for a reading: a
wrist HR beats a phone's guess at it. It is wrong for a **coverage** metric.
Steps are only counted while the device is carried, so the better number is
whichever device was carried more — not whichever device is nominally better.

On 12-Sep `applewatch` held 301 steps and `healthconnect` held 2,430; S01
returned 301 and the 2,430 was discarded. `watch_activity()` now takes
`MAX(value)` across sources for `steps` only, after `resolve_daily()` has run.

Deliberately narrow:
- **Only `steps`, and only in `watch_activity()`.** `resolve_daily()` and
  S01 itself are untouched, so `/api/health/daily` still answers by
  precedence and the rule layer sees no change.
- Every other metric keeps precedence. `healthconnect` supplies steps
  alone; `resting_hr`, `hrv_ms`, `spo2_pct`, `exercise_minutes` and
  `stand_hours` have only ever had `applewatch` as a source, so there is
  nothing for a max to pick between.
- A day with one source is unchanged — 06 and 07 Sep stay at 18 and 13.

Audited across all 10 days holding step data before applying: **only 12-Sep
changes** (301 → 2,430). `audit_steps_delta.py` prints the comparison.

### Workout times are stored in UTC and converted at read-out — v1.3.3

Apple sends `2026-09-11T01:41:29.553Z`. `watch_activity()` used to do
`[:19]`, which silently dropped the `Z`, and GutLog then rendered the result
as local time: a 07:11 IST walk displayed as 01:41.

`_ist()` in `app.py` converts a Z-stamp to IST and passes anything else
through unchanged. The stored `start_ts` keeps the stamp **exactly as
delivered** — `health_raw` and `health_workouts` are the record of what
arrived; the display layer is where a timezone belongs. Both stored walks
now read 07:11:29 and 21:58:41.

The **calendar day** is a separate problem and lives in the ingest path, not
here — see below.

### The day a workout is filed under — v1.3.3

`_apple_samples()` dated an Auto Export workout with
`_parse_date(start_raw)`, the first ten characters of the stamp. IST is
UTC+5:30, so a `Z` stamp starting before 05:30 IST resolves to the
**previous** calendar day. His walks are 05:00–07:30, squarely inside that
window. `parse_ios_payload()` never had the defect — it already used
`_to_ist_date()`. The Auto Export workout loop now uses it too; a `+0530`
stamp reaches `_parse_date()` unchanged, so the shape in use today behaves
exactly as before.

**No history was rewritten.** Audited live on 2026-09-13 first:
`health_workouts` holds 2 rows, both from the retired ios feed and both
already correctly dated, and none of the 8 stored Auto Export bodies carries
a `workouts` array — so no workout has ever come through the slicing path
and **zero existing rows are mis-dated**. If that ever changes, a backfill
belongs in `recompute_apple_daily.py`, not in the ingest patcher.

### The sleep record — `FITLOG_SLEEP_P1` (2026-09-15)

**Never score a night.** No readiness figure, no recovery percentage, no
"poor night", no streak, no target. He has post-discontinuation insomnia, and
a device that grades sleep every morning makes insomnia worse — the anxiety
about the number becomes its own cause. `sleep_night()` returns measurements
and a rule name and nothing else; `test_sleep_night.py` assertion 03 fails the
build if a key containing *score / grade / readiness / recovery / rating /
quality / target / streak* ever appears on it. Where a comparison is
unavoidable it is against his own recent median, never a population norm. He
has been an early riser all his life: a 05:00 wake is his baseline.

**What was actually wrong, and what was not.** Apple Health held 5 h 41 m for
the night of 14→15 Sep, roughly 22:30 to just past 05:00. FitLog showed 1.7.
Read against `health_raw` on the server, the parser was faithful:

```
raw 208 / 211   sleep_analysis, ONE point
  date 2026-09-15 00:00:00 +0530
  sleepStart 2026-09-15 03:13:23 +0530   sleepEnd 2026-09-15 04:57:30 +0530
  totalSleep 1.6685396374927626   core 1.2430662  rem 0.4254734  deep 0
  awake 0.0667416   asleep 0   inBed 0
health_metrics 2026-09-15 sleep_hours = 1.6685396374927626
```

Bit for bit. **Health Auto Export delivered a 1 h 44 m fragment of the night**
— the last block only — and the four missing hours were never in a payload.
Neither suspect held: `totalSleep` was present and positive so the phase-sum
branch was never reached, and only one point per date had ever been delivered
so nothing overwrote anything. The export window is a phone-side setting.

**Why the code change was still blocking.** Widening the export ALONE would
not have fixed the number. Auto Export stamps every sleep point at midnight of
the day the night is filed under — the 09-14 point covers 23:08 on 09-13 to
03:19 on 09-14 and is still stamped `2026-09-14 00:00:00 +0530`. The moment a
wider window delivers a night in two blocks, both arrive stamped identically,
both land on `hae|sleep_hours|day|<date>|00:00:00`, and the second silently
replaces the first. The night would have gone on reading as its last block and
the export change would have looked like it had worked.

#### Rule S03 — Sleep Block Identity
- a sleep sample is keyed by its own `sleepStart`, never by the midnight stamp
  every block of the night shares
- blocks that **overlap** are competing descriptions of one stretch of the
  night — Auto Export re-segmenting a night it has already sent — and the
  **longest-span description wins, never the sum**. Adding them would invent
  sleep he did not have, which is the one error this record must never make.
- blocks that do **not** overlap are different stretches and **add**
- `asleep` is Apple's retired pre-stage category and arrives as **0** on every
  Watch night on this server, beside a real `totalSleep`. A total is believed
  only when greater than zero. `inBed` likewise arrives as 0, so time in bed
  is computed from `inBedStart`/`inBedEnd`, never read.
- **awake time is never counted as sleep**
- a night is filed under the **wake date** Auto Export gives it, whatever hour
  it started. `_ist_stamp()` converts `Z` and any other offset to IST before
  storage, so no UTC stamp can reach a page.

`sleep_night(conn, date, source)` rebuilds the night from its blocks.
`awakenings` counts the **breaks between recorded sleep blocks** — a floor,
not a count of times he woke, because Auto Export's aggregate carries no
awakening count. `awake_h` is the measured time awake and is exact. The two
are reported separately and the floor is labelled as one wherever it is shown.

**Open, and phone-side:** the export window must be widened before a whole
night reaches the server at all. **Wrist temperature is not being exported** —
the metric census over all 212 stored bodies shows no
`apple_sleeping_wrist_temperature` point has ever arrived. `respiratory_rate`
(17 points) and `blood_oxygen_saturation` (31 points) do arrive and are mapped.

### Carrying the whole night — `FITLOG_SLEEP_P2` (2026-09-15)

Phase 1 made the night's span and stages survive the trip into the database.
This carries what the Watch measured *during* it.

**Wrist temperature is the point of the phase.** He has had subjective
feverishness for over two years with, until 2026-09-14, not one documented
temperature, and the Watch has been measuring wrist temperature every night
and discarding it because no name in `METRIC_MAP` claimed it. As of the
2026-09-15 census of all 212 stored bodies, **Auto Export is not sending it at
all** — it has to be switched on in the app. Several spellings are now claimed
(`apple_sleeping_wrist_temperature`, `sleeping_wrist_temperature`,
`wrist_temperature`) so the value lands whichever one arrives; anything else
still surfaces by name under `skipped_metrics` rather than vanishing. **After
enabling it, read `skipped_metrics` on the ingest response** — a temperature
name appearing there means the mapping needs one more spelling.

`body_temperature` and `basal_body_temperature` are mapped too, kept in
separate canonical names, and never averaged together with the wrist sensor: a
thermometer reading and a skin sensor are different measurements.

Fahrenheit is converted. **A temperature in a unit the converter does not know
is dropped, not stored** — a canonical name ending `_c` quietly holding 98.6 is
a lie in a health record, and a unit column nobody reads is not a defence.

#### Rule S04 — Overnight Basis
`overnight_metrics()` reads the samples whose own timestamps fall inside the
sleep span, reconstructing each sample's stamp from its `date` column and the
time in its `hae` record key (`_hae_record_time`, valid for `feed='hae'` rows
only — the HC and retired ios keys are a different shape).

- `basis: "night"` — the mean of the samples inside the span, carrying `n`,
  `min` and `max` so the reader can see how much it rests on
- `basis: "day"` — no sample carried a time inside the span, so the day's
  figure is shown **and said to be a day figure**. Apple stamps wrist
  temperature at midnight, which is exactly this case; calling it an overnight
  figure would be a small lie told every morning.
- a metric with neither is **absent, not zero**. A zero blood oxygen on a
  sleep page reads as an event.

**Time in bed sums the stretches, never the span.** A night that runs 23:00 to
01:30, breaks, and resumes 02:10 to 05:00 *spans* six hours and holds five
hours twenty of recorded bed time. Spanning the gap would count forty minutes
he may have spent out of bed as time in bed — on a record built for someone
with insomnia, overstating time in bed is precisely the wrong way to be wrong.

### The Sleep card — `FITLOG_SLEEP_P2_PAGE`

On `/watch`, under the rings: the night's clock times in IST, asleep against
time in bed, the stage split drawn to scale, breaks and measured awake time
kept apart, the overnight figures each labelled with their basis, the last
fourteen nights, and — once there are enough of them — **his own rolling median
with the number of nights it rests on**.

**No median below seven nights** (`FITLOG_SLEEP_MEDIAN_N7`). Showing one over
two or three nights with `n` stated beside it is honest and still wrong: a
median over three nights is not a baseline, it is three numbers wearing the
word, and printing the count does not stop it being read as one. An honest
label on a misleading number is still a misleading number — and the early
nights on this record include 2026-09-15, which reached the server truncated,
so the figure would have rested on a night known to be wrong. Below the
threshold the card says there are not enough nights yet, names how many there
are, and states that nothing is being compared until then. Seven because it is
a week: enough that one night does not move it. The negative control
`M_neverenough` raises the threshold to 99 and requires the suite to catch it —
suppressing a misleading figure must not quietly become suppressing the one
comparison the page is allowed to make.

`resting_hr` and `hrv_ms` are relabelled **"Rest HR overnight"** and **"HRV
overnight"** wherever they appear, and the Today-so-far strip says they are
derived overnight by the Watch. He had been reading them as cardiac figures
all week; they are sleep figures as much as cardiac ones and nothing said so.

**The card never scores a night**, and says so in words on the card itself,
because a constraint that lives only in a patch header is one refactor away
from being gone. `test_sleep_page.py` assertion 10 scans everything the card
*displays* (the closing note excepted, since that note has to use the words)
for `sleep score`, `readiness`, `recovery score`, `streak`, `grade`, `rating`,
`poor night`, `/100` and the rest, and fails the build on a hit. Its negative
control, `M_score`, puts a sleep score on the card on purpose and requires the
assertion to catch it.

`/api/feed/watch` carries the whole night per day, so GutLog can put a night
beside a day without re-deriving it; a date with nothing recorded carries
`sleep: null`, never an empty shell that reads as a night of no sleep.

### Schemas
- `health_metrics` — id, date, metric, value, unit, source, ingested_at ·
  `UNIQUE(date, metric, source)` → repeat POSTs upsert, never duplicate
- `health_sleep_blocks` — block_key (`feed|source|date|start_ts|end_ts`), date,
  source, feed, start_ts, end_ts, in_bed_start, in_bed_end, asleep_h, rem_h,
  core_h, deep_h, awake_h, device, ingested_at. Created in place by
  `_ensure_sleep_block_table()`, like `health_hc_records` — additive,
  idempotent, no second deploy step. The key is the block's **span**, never
  its value, so a redelivery updates in place and a night cannot be added to
  itself.
- `health_workouts` — id, date, start_ts, end_ts, wtype, duration_s,
  energy_kcal, distance_km, avg_hr, max_hr, source, ingested_at ·
  `UNIQUE(start_ts, wtype, source)`
- `health_raw` — id, received_at, source, n_bytes, payload · every body kept
  verbatim, so a vendor format change costs a re-parse and never lost history

Day boundary is true calendar IST, unshifted — ingested values match exactly
what the Health app shows. Post-midnight OT is handled in the rule layer, not
by silently bending the data.

**No F-rule consumes ingested data.** Verdict logic is byte-identical before
and after Phase 3.5, deliberately. Wire metrics into rules only after a few
weeks of real data shows what an OT day looks like on the wrist.

## Apple Watch view — `/watch` (v1.3.0)

`https://fit.dr-manoj.in/watch` — owner-facing, `@login_required`, in the
switcher bar between FitLog and Events. **Read-only by construction**: the
route runs SELECTs and renders. It touches no rule, no knowledge file and
no write path.

- **Activity rings** — Move / Exercise / Stand as inline SVG arcs against
  the goals the Watch itself reported, with the percentage.
- **Recent days** — 14 days × steps, active energy, exercise minutes,
  stand hours, resting HR, HRV, sleep. A blank cell is "the Watch sent
  nothing", never a zero.
- **Trend** — 28-day step bars plus mean / low / high / days-reported per
  metric.
- **Workouts** — type, duration, distance, energy, last 28 days.
- **Sources** — every source with row count and date range. Apple Watch
  reads *active*; Health Connect reads *parked — kept, not fed*. Samsung
  rows are retained deliberately.
- Each metric carries an **R** (rule-bearing) or **C** (context-only)
  badge, and `RULE_BEARING` is imported from `health_ingest` rather than
  restated, so the page can never disagree with the engine.

`test_watch_page.py` (39 checks) actually renders the page and asserts on
the HTML — the auth gate, the goals, the None-safe workout row, the parked
label, the read-only guarantee (row counts unchanged across three GETs)
and the switcher bar. Server suites otherwise never render a page; this is
the FitLog analogue of GutLog's `test_ui_now.py` lesson.

Every string the page builds is plain concatenation, never an f-string —
Python 3.9 has no PEP 701, and CSS/SVG braces inside an f-string are a
live hazard in this codebase.

## "Today so far" strip — the vitals page (v1.3.1)

A compact live Watch block at the top of `/`, the page shown on login
where the morning check-in happens. **Today**, not yesterday: progress
during the day.

Placement is deliberate: immediately **after** the safety flags and
**before** the check-in form. Flags stay first — demoting an F-rule
warning below a data strip would be a regression — and the strip is one
card of roughly 160px, so the Sleep/Energy/Pain scales stay on the first
screen at 375×812. Logging remains the primary action. On a day already
checked in, the strip sits above the verdict (owner's call, 2026-09-11).

Four tiles: Steps (big figure), then Move / Exercise / Stand as 46px ring
arcs against the goals the Watch reported, each captioned `value/goal`.
A context line carries resting HR and HRV. Every tile carries an R or C
badge drawn from `W_RULE_BEARING`, the alias of `health_ingest.RULE_BEARING`
— never a local copy, so the badges cannot drift from the engine.

**Freshness.** Every populated strip shows `as of HH:MM IST` from
`MAX(ingested_at)` over today's Watch rows, plus a relative age
("8 min ago", "5 h ago"). Past `W_STALE_MINUTES` (180) the stamp turns
amber. A stale figure can never be read as live.

**Empty day.** If nothing has arrived today the strip says so in words and
names the last reading on file ("Last reading was 2026-09-10 at 17:31
IST"). It never renders a zero the Watch did not send, and never draws an
empty ring.

Read-only. `/watch` is unchanged; the strip links to it.

## Token handling — the access log stopped recording the token (2026-09-20)

The `?k=` healthconnect token stays in the URL: HC Webhook cannot send
headers (CLAUDE.md §5a). What changed is that the web server no longer
writes the URL down.

`logFormat` in `/usr/local/lsws/conf/vhosts/fit.dr-manoj.in/vhost.conf` now
reads `"%h %l %u %t "%m %U %H" %>s %b "%{Referer}i" "%{User-Agent}i""` —
`%U` is the path without the query string. `%m` and `%H` are kept
deliberately: `%U` alone would have dropped the method, and "were there
POSTs to `/api/ingest`, and what did they return?" is the first question
every ingest diagnosis asks. Applied 2026-09-20 13:06 IST with
`lswsctrl restart` (graceful, `SIGUSR1`, zero downtime); all eight vhosts
on the box answered afterwards, and **only fit's vhost was touched** — the
other eight still log `%r`, which is correct for them. Backup:
`vhost.conf.bak-noquery-20260920_130656`. Proof, a deliberately fake key:

```
"POST /api/ingest HTTP/1.1" 401 36 "-" "curl/7.76.1"
```

CyberPanel may rewrite `vhost.conf` if the vhost is edited through its UI or
on some SSL operations — **re-check this line after any CyberPanel change to
fit.dr-manoj.in.**

**The 392 lines already written are masked (2026-09-20 13:17 IST).** Between
11 Sep and 20 Sep the log had accumulated 392 request lines carrying the live
token in clear; the format change stops new ones but does nothing about
those. Each `k=<value>` is now `k=***`. Checked: 392 masked, **0** values
left anywhere in the file, 944 lines before and after, and the result is
byte-identical to re-running the same substitution over the pre-change copy —
so nothing but the key changed. `source=healthconnect` survives on all 392,
which is the field diagnosis actually reads. The pre-change copy was removed
only after those checks passed.

Rewriting it was done through the **same inode** (`sed … > tmp; cat tmp >
log`), never `sed -i`: litespeed holds the fd in append mode, so replacing
the inode would send every new line to a deleted file. Inode `260060899`
before and after, and a probe request afterwards appended normally. Do not
delete the file either — that orphans the fd. There is **no logrotate rule**
for it: OLS self-rolls at `rollingSize 10M` / `keepDays 10`, and at ~160 KB
it will not roll for months, so there were **no rotated copies** to clean.

### The exposure — CLOSED, ACCEPTED, NOT ROTATED (owner's decision, 2026-09-20)

`FITLOG_HC_TOKEN` sat in cleartext on disk from 11 Sep to 20 Sep. **The owner
decided not to rotate it**, on two grounds: the log was not readable by any
unprivileged or remote account, and the token is send-only — scoped POST-only,
`healthconnect`-only, **no read access** (CLAUDE.md §5a). Nothing that could
have read it could have done anything with it that it could not already do.
This is recorded as accepted, not as unfinished.

Who could actually read it, measured rather than assumed:

| Path | Mode | Owner |
|---|---|---|
| `/home/fit.dr-manoj.in` | `drwx--x--x` | `fitdr5911:fitdr5911` |
| `…/logs` | `drwxr-x---` | `root:nobody` |
| `…/logs/fit.dr-manoj.in.access_log` | `-rw-r--r--` | `nobody:nobody` |

So: root; the litespeed worker itself, which runs as `nobody` and wrote the
file; and `lsadm` and `lscpd`, CyberPanel's own daemons, which are the only
secondary members of group `nobody`. **"Root-only" is the right conclusion but
not literally the mode** — the directory is 750 `root:nobody`, not 700, and
the file's world-readable bit is only held back by that directory. The
fifteen per-site users (`fitdr5911`, `healt4504`, `rxdrm3815` and the rest)
are **not** in group `nobody` and could not traverse the directory. Worth
knowing before anyone relaxes that directory's mode.

The gunicorn access log never held it: `/var/log/fitlog/access.log` and all
eight rotated copies show **0** unredacted `k=` values — `RedactingLogger`
worked as designed throughout. The OLS log was the only place it ever landed,
and it is now masked.

The gunicorn log is unaffected: `RedactingLogger` writes `k=<redacted>`.

## Observability — request logging (2026-09-11)

Two logs answer "did the request arrive, and what did we say back?".

| Log | Written by | Path | Notes |
|---|---|---|---|
| OLS vhost access log | OpenLiteSpeed | `/home/fit.dr-manoj.in/logs/fit.dr-manoj.in.access_log` | Config: `/usr/local/lsws/conf/vhosts/fit.dr-manoj.in/vhost.conf`. Rolls at 10 M, `keepDays 10`. Since 2026-09-20 logs `%m %U %H` — **path only, no query string**, so the `?k=` token is no longer recorded, and the 392 lines written before that have been masked to `k=***`. |
| gunicorn access log | FitLog | `/var/log/fitlog/access.log` | `gunicorn_conf.py`, `RedactingLogger`. 30-day logrotate (`/etc/logrotate.d/fitlog`). |

Gunicorn ran with no access log until 2026-09-11, which is why a client-side
auth failure was indistinguishable from a request that never arrived.
`gunicorn_conf.py` turns it on and routes every line through a redacting
logger, so `source=` stays visible for diagnosis while `k=`/`token=`/`key=`
values are written as `<redacted>`. `%(h)s` is always `127.0.0.1` behind the
proxy, so the real client is the `fwd=` field (`X-Forwarded-For`).

`LogsDirectory=fitlog` in the unit creates and owns `/var/log/fitlog`.
`ExecStart` gained `-c /root/fitlog/gunicorn_conf.py`; bind and worker count
stay on the command line. Rollback: `fitlog.service.bak-accesslog-*`.

**Caveat:** the redactor keys on the *parameter name*. A client that puts a
token in some other parameter (e.g. `?source=<token>`, which is exactly what
the Android feed was doing) still writes it to the log in clear.

### Verified transport (2026-09-11)
OLS forwards `Authorization` to gunicorn intact — `GET /api/ingest/status`
with the bearer returns `200` through `https://fit.dr-manoj.in` and `200`
direct to `127.0.0.1:8040`. Any `401` on `/api/ingest` is the app rejecting
the credential the client actually sent, never the proxy stripping it.

## Deployment runbook
1. WinSCP upload folder → `/root/fitlog/` (WinSCP, not terminal paste — established pattern)
2. `pip3 install flask gunicorn` (if absent)
3. Tests ON SERVER before enabling:
   `cd /root/fitlog && python3 tests/kb_lint.py && python3 tests/smoke_test.py` → must be **53/53**
4. `cp fitlog.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now fitlog`
5. `curl -s 127.0.0.1:8040/health` → `{"app":"fitlog","ok":true}`
6. CyberPanel: create site `fit.dr-manoj.in`, SSL, OLS reverse proxy → 127.0.0.1:8040 (same proxy block pattern as rx/health)
7. GoDaddy: A record `fit` → 93.127.195.49
8. Backup: `crontab -e` → `20 2 * * * /usr/bin/python3 /root/fitlog/backup_fitlog.py >> /root/backups/fitlog/backup.log 2>&1`
9. **Verify backup manually once**: run script, open `/root/backups/fitlog/`, confirm file + "integrity ok" + checkin count. Silent cron success is not evidence of correctness.
10. First visit → /setup → set password + owner key.
11. Add switcher-bar links to RxGuard and GutLog headers (3 lines each; FitLog already carries the bar).

## Environment (verified on server, 2026-08-02)
Python 3.9.25 · SQLite 3.34.1 · Flask 3.1.3 · gunicorn 23.0.0 · 2 sync workers · deployed at /root/fitlog, service `fitlog`, HTTPS live via OLS proxy.

## Test evidence
- kb_lint: PASS — 18 exercises, 14 protocols, 10 meds, 14 rules
- smoke_test: **53/53 PASS** — full rule matrix incl. 9 negative controls, precedence (RECOVERY/RED beat TRAVEL), F07 deload, F08 trim, F10 long-haul, plan determinism + rotation, protocol matcher (car/flight, short/long leg), W03 positive + sleep-category negative
- Live route walk: setup → login → check-in → GREEN + F09 chip → event add (flight 5h) → med log → capacity test → session close → history. All 200/302.

## Post-deploy sequence (Phase 2)
1. Week-0 baseline at /tests (bridge, plank L/R, walk) — before first training week
2. Add current med epochs at /tests (e.g. the dates of a medication taper)
3. Log known future events (OT lists, travel) in advance — enables F05 protection + PRE cards
4. Daily check-in ≥14 days; Sunday re-tests; tune thresholds in rules.json as needed (re-run smoke after edits)

## Known deferrals (by design)
Dashboard trends/epoch-band charts (Phase 3) · media enrichment image/video (Phase 3, schema slots ready) · Hyperice protocol pages · manual/encyclopedia · ~~wearables (Phase 4)~~ → **capture** shipped in Phase 3.5; rule consumption still deferred by design

## Test suites
Run all three on the server. They gate the restart.

| Suite | Count | Covers |
|---|---|---|
| `test_health_ingest.py` | 23/23 | Base ingest, auth, S01, idempotency |
| `test_ios_ingest.py` | 18/18 | v1.3.0: iOS Health Webhook path — UTC→IST boundary, units, averaging vs summing, rings, idempotency, workouts |
| `test_ios_source_isolation.py` | 19/19 | v1.3.0: **negative control for cross-source contamination** — seeds HC rows first, then posts Watch data for the same date. 2/19 against the unfixed code |
| `test_watch_page.py` | 39/39 | v1.3.0: renders `/watch` — auth gate, rings vs goals, R/C badges, None-safe workout, parked label, read-only, switcher bar |
| `test_apple_aggregation.py` | 29/29 | v1.3.2: Health Auto Export parser — per-hour and daily-rollup shapes from the real payloads, kJ→kcal both ways, sum vs mean, idempotency, and **stand_hours 19 pinned as correct**. 7 failures against the unfixed parser |
| `test_watch_strip.py` | 52/52 | v1.3.1: renders `/` — empty day, data-but-not-today, exact freshness stamp, stale marker, badge provenance from the engine, strip shape and position, read-only, `/watch` untouched |
| `test_hc_ingest.py` | 36/36 | HC Webhook path, R1/R2 regressions, negative controls |
| `test_db_pin.py` | 12/12 | Non-default DB filename resolution |
| `test_activity_feed.py` | 14/14 | v1.2.0: real GutLog + real FitLog on loopback — workout classes, mindful + indoor ingest, feed auth and content (best source only), GutLog pulling watch data (merge), Home card, escaping, cache not mutated, scratch-DB isolation, GutLog down, token file missing. **v1.3.3 adds cases 12 and 13** — UTC→IST at read-out (including a 23:30Z stamp that rolls the day) and steps taking the larger source while `exercise_minutes` stays with the watch. Both fail against the unpatched `app.py` |
| `test_workout_day_ist.py` | 12/12 | v1.3.3: the calendar day an Auto Export workout is filed under. A pre-05:30-IST `Z` stamp must keep its own day; a `+0530` stamp must behave exactly as before; the old slicing expression is kept as a **negative control**. 9/12 against the unfixed parser |
| `test_apple_records.py` | 56/56 | Phase 3.5.1: record-level Auto Export ingest under rule S02 — per-hour with no rollup, partial after full, rollup before per-hour, cross-feed comparison, distance units. Negative control replays the unfixed paths in-process |
| `test_recompute_apple.py` | 36/36 | Phase 3.5.1: `recompute_apple_daily.py` — dry-run isolation, retired-feed skip and opt-in, range and argument handling |
| `test_gutlog_feed.py` | 10/10 | v1.1.0: real GutLog on loopback — W03 from GutLog alone, sleep and unmatched negatives, union dedupe, FitLog-only unchanged, window, Meds page, escaping, scratch-DB isolation, GutLog down |
| `test_analgesic_mirror.py` | 8/8 | v1.4.0: **real GutLog and real FitLog, two loopback ports** — the endpoint bearer-gated (no token and wrong token both 401), one pain-tile tap producing exactly one `doses` row there and exactly one `analgesic_log` row here, the score arriving as `pain_at_time` with the site in the note, two chips giving 2 + 2 rows on one score, a retry returning the first id instead of doubling, an unknown molecule refused rather than filed under a chance substring, GutLog still recording the dose and saying so when the mirror misses, and the operating day rendered as standing load and never as exercise minutes. **1/8 against v1.3.3** (the one that passes is the degradation case, which holds either way). **Clock-independent** — every time written to today is derived from the clock and clamped to midnight, and the fixed timestamps for the idempotency and refusal cases sit on a past day so they cannot collide with a derived one; verified under `tools/RUN_AT_TIME.py` at 00:00, 00:02, 01:10, 05:05, 12:00, 18:30 and 23:58 |

| `test_sleep_night.py` | 10/10 | `FITLOG_SLEEP_P1`: the night is stored whole — a 5 h 41 m night delivered in two blocks stores 5.68 and not the last block; the same night arriving one block per payload combines; a re-segmented night is not added to itself; `asleep=0` beside real stages is not a zero night; awake time is never counted as sleep; a night starting before midnight is filed under its wake date; a `Z` stamp is stored in IST; **and the night is never scored** (a `score`/`grade`/`readiness`/`recovery`/`rating`/`quality`/`target`/`streak` key on `sleep_night()` fails the build). **10 declared, 10 seen to fail** — 7 against the reconstructed previous build, 3 against mutations (`M_overlap`, `M_wakedate`, `M_awake`). Every fixture synthetic; every date a payload literal, so it is clock-independent by construction — verified under `tools/RUN_AT_TIME.py` at 00:02, 12:00 and 23:58 |

| `test_sleep_overnight.py` | 11/11 | `FITLOG_SLEEP_P2`: wrist temperature mapped and stored rather than skipped, Fahrenheit converted, an unknown unit **dropped** rather than filed under a Celsius name, a thermometer reading kept apart from the wrist sensor, temperature context-only, an overnight figure averaged over the samples inside the sleep span (a 13:00 sample left out), a figure measured outside the night labelled a day figure, resting HR and HRV carried on the night, nothing carrying a verdict, a never-sent metric **absent rather than zero**, and time in bed summing the stretches instead of spanning a break. **11 declared, 11 seen to fail** — 8 against the reconstructed `FITLOG_SLEEP_P1` build, 3 against mutations (`M_unitguess`, `M_basis`, `M_zerofill`) |
| `test_sleep_page.py` | 14/14 | `FITLOG_SLEEP_P2_PAGE`: renders `/watch` and asserts on the HTML — the card names the night, shows it as `4 h 54 m` not `4.9`, IST clock times with no UTC on the page, the stage bar drawn to scale (widths summing to 100%), breaks and awake kept separate with the floor stated in words, an overnight mean with its reading count, a day-basis figure saying so, Rest HR / HRV relabelled overnight, the strip saying where they come from, **the page never scoring a night**, **no median at all over three nights** and the card saying why, the median appearing at seven as `4 h 30 m over 7 nights`, nothing under 14px in the new rules, and the feed carrying the whole night with absence as `null`. **15 declared, 15 seen to fail** across two manifests — 11 against reconstructed builds, 4 against mutations (`M_score`, `M_mean`, `M_smalltype`, `M_neverenough`). Clock-checked at 00:02 and 23:58. Case 14 runs last because it adds nights to the fixture |

`test_health_ingest.py` does **not** exercise the HC path — every
healthconnect case in it uses the legacy shape, so `is_hc` is false. It scored
23/23 against HC code carrying two data bugs. Never treat it alone as a gate
on HC changes.

## Changelog
- 2026-09-15 `FITLOG_SLEEP_MEDIAN_N7` — **DEPLOYED 18:00 IST**, ten minutes after the phase it corrects. `app.py` md5 `5f8f3ad9…`, byte-identical to the repo build; 2/2 anchors; rollback `app.py.bak-medn7-20260915_180007`; DB backup `fitlog.db.pre-medn7-20260915_180006` (integrity ok) although this patch writes nothing. **No median is shown until there are seven nights with data.** The card had been showing his own median over however many nights existed, with the count stated beside it — honest, and still wrong. A median over two or three nights is not a baseline, it is two or three numbers wearing the word, and printing `n` does not stop it being read as one; an honest label on a misleading number is still a misleading number. Worse here than in general: the record's first nights include 2026-09-15, which arrived truncated, so the figure would have rested on a night known to be wrong. Below the threshold the card now says there are not enough nights yet, names the count, and states that nothing is being compared until then. Withheld, not qualified. Negative control **2/2 SEEN** — assertion 11 fails against the reconstructed `FITLOG_SLEEP_P2_PAGE`, and `M_neverenough` raises the threshold to 99 so that suppressing a misleading figure cannot quietly become suppressing the one comparison this page is allowed to make. Whole 26-run gate green again before the restart. *Note for whoever reverses next: this patch edits the same function as `patch_fitlog_sleep_p2_page.py`, so `new_assertions_sleep_p2_page.json` can no longer be re-run in place — reverse in LIFO order.*
- 2026-09-15 `FITLOG_SLEEP_P2` + `FITLOG_SLEEP_P2_PAGE` — **DEPLOYED 17:50 IST.** `health_ingest.py` md5 `a55b48e9…` (69,446 bytes) and `app.py` md5 `9c41a41b…` (88,834 bytes), both byte-identical to the repo builds; 10/10 and 9/9 anchors, compile checks OK. DB backed up first: `/root/backups/fitlog/fitlog.db.pre-sleepp2-20260915_174829` (integrity ok, 14 tables, 218 raw bodies, 2 sleep blocks). Rollbacks `health_ingest.py.bak-sleepp2-20260915_174908` and `app.py.bak-sleepp2page-20260915_174908` — either can be reverted alone. Both negative controls run on the server before the restart: **11/11 and 13/13 SEEN**, including `M_score`. Then 26 gate runs, all green — the three sleep suites each at now / 00:02 / 23:58, plus 23/23, 29/29, 56/56, 36/36, 36/36, 18/18, 19/19, 12/12, 12/12, 39/39, 52/52, kb_lint, 53/53, 14/14, 10/10, 8/8, 5/5. Clean boot, `/health` 200, `/watch` still 302 to login unauthenticated. **Live read-back against the real record:** 2026-09-14 now reports respiratory rate 13.88, SpO2 97.5, resting HR 84 and HRV 35.84 **all with `basis: "night"`** — four figures that existed only as daily averages before today; 2026-09-15 reports HRV alone with `basis: "day"`, because that night was recorded as 03:13–04:57 and no sample fell inside so narrow a window. The two nights' HRV figures differ by 21 ms and are *not* comparable — one is a night figure and one is a day figure, and the page now says which is which rather than putting them side by side unlabelled. Temperature rows: **0**, as expected until the export is switched on. Carry the whole night, and put it on the page without scoring it. Two patchers: `patch_fitlog_sleep_p2.py` (10 anchors, `health_ingest.py`) and `patch_fitlog_sleep_p2_page.py` (9 anchors, `app.py`), both reversible and `newline=""`. **Wrist temperature is the point of the phase** — every spelling Auto Export is known to use is now mapped, Fahrenheit converted, an unconvertible unit dropped rather than filed under a name ending `_c`, and `body_temperature` kept in its own canonical name so a thermometer reading is never averaged with a skin sensor. It is **not being exported yet**: check `skipped_metrics` after switching it on. New rule **S04 Overnight Basis** — a figure said to be measured over the night really is the mean of the samples inside the sleep span, with `n`, `min` and `max`; one that is not carries `basis: "day"` and says so on the page; a metric never sent is absent rather than zero. Time in bed now **sums the stretches** instead of spanning a break, which had been reading 6 h for a 5 h 20 m night. On `/watch`: a Sleep card with IST clock times, the stage split drawn to scale, breaks and measured awake time kept apart, the overnight figures with their basis, fourteen nights, and **his own median with n** — the only comparison anywhere on it. `resting_hr` and `hrv_ms` relabelled **Rest HR overnight** / **HRV overnight**, and the Today-so-far strip says where they come from; he had been reading them as cardiac figures all week. `/api/feed/watch` carries the night, `null` when there is none. New `test_sleep_overnight.py` **11/11** and `test_sleep_page.py` **13/13**, **24 declared and 24 seen to fail** across the two manifests — including `M_score`, which puts a sleep score on the card on purpose and requires the suite to catch it. Page suite green at 00:02, 12:00 and 23:58. Full sweep green: 53/53, kb_lint, 10/10 sleep-night, 23/23, 29/29, 36/36, 18/18, 19/19, 12/12, 39/39, 52/52, 5/5, 10/10, 8/8, 14/14, 56/56, 36/36, 12/12. Folder parity restored. *Found and fixed by rendering the page rather than trusting the assertions: time in bed was counting a 40-minute break, and the overnight figures were in a three-column table whose basis text ran off the right edge at 375px — now a wrapping list.*
- 2026-09-15 `FITLOG_SLEEP_P1` — **DEPLOYED 15:43 IST.** `health_ingest.py` md5 `3b0fd810…`, byte-identical to the repo build; 8/8 anchors, compile check OK; rollback `health_ingest.py.bak-sleepp1-20260915_154304`, DB `/root/backups/fitlog/fitlog.db.pre-sleepp1-20260915_154222` (integrity ok, 13 tables, 213 raw bodies). Whole gate green on the server before the restart — negative control 10/10 SEEN, then 20 suites: sleep-night 10/10 at now / 00:02 / 23:58, 23/23, 29/29, 56/56, 36/36, 36/36, 18/18, 19/19, 12/12, 12/12, 39/39, 52/52, kb_lint, 53/53, 14/14, 5/5, 10/10, 8/8. Clean boot, no errors in the journal. **Re-parse committed 15:52** via the new `reparse_sleep_blocks.py` (DB backed up first, `--dry-run` read before committing): 5 bodies re-parsed, 5 sleep blocks, 2 dates touched, **0 values changed** — 09-14 stays 3.75 and 09-15 stays 1.67, and the tool's own span check says why: on both nights asleep + awake equals the recorded span to two decimals, so nothing was lost between the payload and the database. What the re-parse did buy is the block rows — real IST spans, stage splits and awake time for both nights — which is what Phase 2 reads.
- 2026-09-15 `FITLOG_SLEEP_P1` (build) —  The sleep record, phase 1: the night is stored whole. `patch_fitlog_sleep_p1.py`, 8 anchors on `health_ingest.py`, reversible, compile-checked, `newline=""` so line endings survive a Windows run. **The four missing hours were not lost by this code** — read against `health_raw` on the server, Health Auto Export delivered a single 1 h 44 m block (`sleepStart 03:13:23`, `sleepEnd 04:57:30`, `totalSleep 1.6685396374927626`) and `health_metrics` stored 1.6685396374927626. Bit for bit. Neither suspect in the brief fired. The export window is a phone-side setting and is the remaining half of the fix. **What the code change buys is that widening the export will actually work:** Auto Export stamps every sleep point at midnight of the wake date, so two blocks of one night would have collided on `hae|sleep_hours|day|<date>|00:00:00` and the second would have replaced the first — the night would have kept reading as its last block and the export change would have looked successful. New rule **S03 Sleep Block Identity**: a sleep sample is keyed by its own `sleepStart`; overlapping blocks are competing descriptions and the longest span wins, never the sum; disjoint blocks add; `asleep`/`inBed` arrive as 0 and a total is believed only when positive; awake is never sleep; `_ist_stamp()` converts every stamp to IST before storage. New table `health_sleep_blocks` (created in place, additive, idempotent — no separate migration step) and `sleep_night()`, which reports measurements and a rule name and **no score of any kind**. `awakenings` counts breaks *between recorded blocks* — a floor, labelled as one — because Auto Export's aggregate carries no awakening count; `awake_h` is exact and reported separately. New `test_sleep_night.py` **10/10**, **10 declared and 10 seen to fail** under `tools/NEGATIVE_CONTROL.py`, green at 00:02 / 12:00 / 23:58. Regression sweep green: kb_lint PASS, smoke 53/53, 23/23, 29/29, 36/36, 18/18, 19/19, 39/39, 52/52, 12/12, 14/14, downdays 5/5. Folder parity restored (22 paired files byte-identical). **Also found, phone-side and not fixed by code:** a census of all 212 stored bodies shows **wrist temperature has never been exported** — no `apple_sleeping_wrist_temperature` point exists — while `respiratory_rate` (17 points) and `blood_oxygen_saturation` (31) do arrive and are already mapped.
- 2026-09-14 v1.6.0 — **DEPLOYED 18:19 IST**, first of the two. Phase M: the trend leaves GutLog's down days out. `patch_fitlog_v160.py`, 6 anchors, reversible byte-for-byte. `app.py` sha256 `8f66d787…`, 77,578 bytes, byte-identical to the repo build. `gutlog_downdays()` reads the new bearer-gated `/api/feed/downdays`; `w_trend_card` draws a down day's bar hatched and named and leaves it out of mean / low / high, saying how many; `/api/feed/watch` reaches back 180 days (was 60) and carries `sleep_hours`. No rule reads any of it. New `test_downdays_trend.py` **5/5** against a real GutLog v3.17.0 — **5 declared, 5 seen to fail** against the reconstructed v1.5.0. Whole gate green on the server before the restart: 5/5, 39/39, 52/52, 23/23, 36/36, 18/18, 19/19, 29/29, 12/12, 56/56, 36/36, 12/12, 10/10, 14/14, 8/8. The gate's rollback fired once and correctly: the three cross-app suites and the new one were first pointed at `app.py.new`, whose suffix `importlib` refuses to load, so they printed nothing, v1.5.0 was restored and nothing restarted; rerun with a `.py`-named candidate. Rollback: `app.py.rollback-v150-20260914_180728`, `fitlog.db.pre-phaseM-20260914_180728` (`sqlite3.backup()`, integrity ok, 13 tables).
- 2026-09-14 v1.5.0 — **DEPLOYED 05:40 IST.** Phase J: the read-only watch feed. `patch_fitlog_v150.py`, 2 anchors. `app.py` sha256 `6c9bf876…`, 74,819 bytes, byte-identical to the repo build. `GET /api/feed/watch?days=14` on the token that already existed — per day every resolved metric **with its source** plus `has_data`, workouts best-source-only with IST applied and `start_hm` carried so nobody slices a timestamp, and any medication epoch overlapping the window. No new table, no new ingestion, no verdict. Live after the deploy: the fortnight answers, the two stored workouts read 21:58 and 07:11 IST, and five days correctly report `has_data` False rather than zero. Whole FitLog gate green on the server first — 53/53, 23/23, 36/36, 12/12, 29/29, 18/18, 19/19, 39/39, 52/52, 12/12, 56/56, 36/36, 10/10, 14/14, 8/8 — plus `gutlog/test_phase_j.py` 18/18. Rollback: `app.py.bak-v150-…`, or `app.py.predeploy-phaseJ-20260914_053829` with `/root/backups/fitlog/fitlog.db.predeploy-phaseJ-20260914_053829`.
  *Repo gap noticed, not fixed:* `test_apple_records.py`, `test_recompute_apple.py` and `test_workout_day_ist.py` exist on the server and in `fitlog-ingest/` but **not** in the authoritative `fitlog/` folder, so `CHECK_FOLDER_PARITY` has nothing to compare and never notices. All three pass (56/56, 36/36, 12/12); they just are not where CLAUDE.md says the authoritative copy lives.
- 2026-09-14 v1.4.0 — **DEPLOYED 04:38 IST**, first of the three. `app.py` sha256 `7a5f6549…`, 69,652 bytes, byte-identical to the repo build; 5/5 anchors, compile check OK. Whole gate green on the server before the restart: 23/23, 36/36, 12/12, 29/29, 18/18, 19/19, 39/39, 52/52, 12/12, 56/56, 36/36, 10/10, 14/14, kb_lint PASS, smoke 53/53. Live checks after: `/api/analgesic` returns **401 with no token** and, with GutLog's feed token, **200 `{"ok": false}`** for a molecule the stack does not carry — with `analgesic_log` still at 0 rows, so a refusal really does write nothing. No errors in the journal. The cross-app `test_analgesic_mirror.py` could not run at this point and was deliberately deferred: it drives GutLog's pain tiles, which did not exist until the next step. It scored **8/8** against both live-patched files once GutLog was up. Rollback: `app.py.bak-v140-20260914_043819`, or `app.py.predeploy-phaseI-20260914_043720` and `/root/backups/fitlog/fitlog.db.predeploy-phaseI-20260914_043720` (`sqlite3.backup()`, integrity ok).
- 2026-09-13 v1.4.0 — Phase I: the analgesic mirror and the operating-day load. `patch_fitlog_v140.py`, 5 anchors. New `POST /api/analgesic`, bearer-gated on GutLog's existing read-only feed token — no new secret, no session path, machine-to-machine per CLAUDE.md rule 5. GutLog's Pain now tiles call it the instant an analgesic chip is tapped, carrying `pain_at_time` = the score entered at the same tap; that score is the whole reason for the endpoint, since the dose feed can say a drug was taken but never how bad it was. `_stack_match()` resolves by exact generic, then by whole component of a combination, then by display name — components rather than substrings, so a single molecule is never filed under the first combination whose name happens to contain those letters. Writes are idempotent on (med_id, dt, notes): a retry after a timeout returns the first row. No match → `{"ok": false}` and nothing written, and GutLog keeps its own dose row and reports what was not mirrored, so a failure here cannot lose a dose. `ACT_LABEL` gains `ot_day` and `LOAD_KINDS` keeps it out of the workout list: the Activity card shows it under **Standing load** in hours, never as exercise minutes. New `test_analgesic_mirror.py` **8/8** running a real GutLog and a real FitLog on two loopback ports (1/8 against v1.3.3); regression sweep green — 23/23, 36/36, 18/18, 19/19, 29/29, 39/39, 52/52, 12/12, 10/10, 14/14, kb_lint PASS, smoke 53/53. Needs GutLog v3.12.0 on the other side. *2026-09-14:* the suite was clock-dependent — it wrote literal times to today, which GutLog rightly refuses before they arrive, so it was green at 23:00 and red at 03:50. Fixed by deriving every such time from the clock, moving the two fixed timestamps onto a past day so they cannot collide with a derived one, and replacing a time-keyed row count with a delta (inside the first 90 minutes after midnight every derived time clamps to 00:00 and collides). Verified at seven times of day with `tools/RUN_AT_TIME.py`.
- 2026-09-13 v1.3.3 — DEPLOYED 19:25 IST. **Workout times in IST; steps take the larger source; workout day fixed forward.** Two anchor-verified patchers, each dry-run first. `patch_fitlog_ist_and_steps.py` (3 anchors, `app.py`): `_ist()` converts a Z-stamp at read-out — the watch sends `2026-09-11T01:41:29.553Z` and the old `[:19]` dropped the `Z`, so GutLog rendered a 07:11 walk as 01:41; and `watch_activity()` now takes `MAX(value)` for `steps` only, because steps are a coverage metric, not a sensor-quality one. `patch_workout_day_ist.py` (1 anchor, `health_ingest.py`): the Auto Export workout loop dates through `_to_ist_date()` instead of slicing, so a walk starting before 05:30 IST keeps its own day. Suites: `test_activity_feed.py` **14/14** (was 12/12 — two cases added, both failing against the unpatched file), new `test_workout_day_ist.py` **12/12** (9/12 against the unfixed parser), plus 23/23, 36/36, 18/18, 19/19, 29/29, 12/12, 56/56, 36/36 — whole gate green on the server before the restart. Live after: the two stored walks read **07:11:29** and **21:58:41**; 12-Sep steps **301 → 2,430**, and an audit of all 10 days holding step data confirms **only 12-Sep changes**. **No history rewritten** — `health_workouts` holds 2 rows, both correctly dated, and none of the 8 stored Auto Export bodies carries a `workouts` array, so zero rows were mis-dated. Repo hygiene the same day, all closed: the `PUBLISH_HEALTH.bat` gate matched only `.bak_` and `.bak-` while the patchers write five spellings, so a `.bak.` copy of `app.py` would have reached a **public** repo — one `%BADPAT%` variable now covers every spelling plus `.tmp`/`.token`/`.secret`, validated at 15/15 caught and 0 false positives over 216 paths; `newline=""` added to all three patchers (`patch_workout_day_ist.py`, `patch_fitlog_ist_and_steps.py`, GutLog's `patch_doses_export_status.py`), each proved by patching the server's pre-patch LF file on Windows and getting the live file back byte-for-byte; and `tools/CHECK_FOLDER_PARITY.py` now blocks the publish if a working-folder copy has drifted from its app folder (22 paired files green). Rollback: `app.py.bak.20260913-192445`, `health_ingest.py.bak.20260913-192451`.
- 2026-09-12 Phase 3.5.1 — DEPLOYED 05:46 IST, recompute committed 05:50. **Record-level Auto Export ingest (rule S02), distance restored, `total_energy_kcal` dropped.** `patch_apple_records.py`, 12 anchors. A day's figure used to be one payload's aggregate upserted on `(date, metric, source)`, so whichever payload landed last owned the day — safe only because Auto Export happens to post a whole-day rollup minutes after the per-hour batch. Every sample is now stored as a record under a dedup key and the daily figure is recomputed under **S02 Day-Grain Precedence**: within a feed, interval records sum, the latest whole-day rollup replaces the previous one, and a summed metric takes the **greater** of the two; across feeds of one source, the greater, never the sum. `max` is the only rule that holds in **both** arrival orders — rollup-wins lets a noon rollup cap the day, intervals-win lets a three-hour batch destroy it. `health_hc_records` gained `grain` and `feed` (additive, defaults describe the pre-existing rows); the HC Webhook path stays pinned to `feed=''` so its arithmetic is byte-for-byte what it was. `walking_running_distance` mapped to `distance_km` — Auto Export's name for it was never in `METRIC_MAP`, so the metric held whatever the retired ios feed last wrote. `total_energy_kcal` dropped: never displayed, no F-rule read it, and it meant active+basal from one feed and something else from another; `METRIC_MAP`'s `total_calories_burned → active_energy_kcal` went too, being a different quantity. Suites: new `test_apple_records.py` **56/56** and `test_recompute_apple.py` **36/36**, plus 23/23, 36/36, 18/18, 19/19, 29/29, 12/12 and the activity feed — 10 green on the server before the restart. **Two things the process caught, both worth keeping:** the repo copy of `health_ingest.py` was the pre-Phase-3.5 base, so the anchors were first built against a reconstruction; the live file turned out to carry `mindful_minutes`/`mindful_session` and a `FITLOG_V120_ACTIVITY` block added out of band, one anchor straddled them and would have refused, and it was narrowed before anything was written. And the first `--dry-run` showed the recompute restoring `hr` and `move_energy_kcal` from the **retired ios bodies still in `health_raw`** — mid-day snapshots, which would have pushed the 09-11 Move ring backwards from 441 to 319; `recompute_apple_daily.py` now skips retired-feed bodies by default, `--replay-retired-ios` opts back in. Distance was cross-checked before committing: Auto Export's per-minute bodies and its daily rollups parse by different code paths and agree to four decimals (0.6557 / 1.5955 / 3.2947 km, units declared `km` throughout). Net effect: **5 figures of 41 changed** — `distance_km` 09-09 0.3028→**0.6557** and 09-11 2.5805→**3.2947**, plus three `total_energy_kcal` rows removed; every other value, including both `healthconnect` rows, reproduced bit-for-bit. Rollback: `health_ingest.py.bak_20260912_054555_888002`, `fitlog.db.bak_20260912_055021_222661`.
- 2026-09-11 v1.3.2 — DEPLOYED 23:41 IST. **Health Auto Export parser fixes.** `patch_apple_aggregation.py`, 2 anchors: `_apple_convert()` (kJ→kcal on `active_energy` / `basal_energy_burned`) and `_apple_aggregate()` (sum counts and durations, average levels, instead of last-sample-wins). New `test_apple_aggregation.py` **29/29**, built on the real id 22 / id 23 payload shapes; **7 checks fail against the unfixed parser**. First apply was rolled back — it rebound the entry-level `unit` variable so only the first sample converted; the suite caught it, `point_unit` fixes it. Suites after: 23/23, 36/36, 18/18, 19/19, 29/29, 39/39, 52/52, 10/10, 12/12, 12/12, kb_lint PASS, smoke 53/53. History recomputed for 09-09..09-11 by replaying stored `health_raw` bodies (no new raw rows): `active_energy_kcal` 521.8/1076.5/1845.1 kJ → **124.7/257.3/441.0 kcal**, `basal_energy_kcal` → 1528.4/1666.1/1735.3 kcal; steps 951/2305/4914, exercise 0/11/17, stand 7/11/19 and flights 1 all unchanged and confirmed correct. 604 ios-era `applewatch` rows retired from `health_hc_records`. **`stand_hours` 19 on 09-11 was investigated and found correct, not a bug** — see the section above. Rollback: `health_ingest.py.bak-apple-20260911_234019`, `fitlog.db.bak-apple-20260911_234019`.
- 2026-09-11 Cleanup sweep — no code change. Fresh DB backup `/root/backups/fitlog/fitlog.db.cleanup-20260911_221631` (integrity ok). Removed 15 superseded `.bak` files (534 KB), `patch_hc_support.py` (md5-verified identical to the repo copy, R1 key `metric|start|end` confirmed), 23 stray `/tmp/fitlog_*` test dirs (1.8 MB) and `__pycache__` (92 KB). Kept per file: newest rollback + last pre-feature state + the pre-Phase-3.5 `app.py` floor. **`FITLOG_HC_TOKEN` rotated a second time** — the first rotation's token had itself been logged by three verification curls through OLS; this rotation was verified over loopback only, so nothing reached the vhost log. OLS access log archived **redacted** to `/root/backups/fitlog/fit.access_log.redacted-20260911_222730` (mode 600, all three tokens masked, 597 lines) then truncated in place; OLS confirmed still appending. Suites after: 23/23, 36/36, 18/18, 19/19, 39/39, 52/52, 10/10, 12/12, 12/12, kb_lint PASS, smoke 53/53. **Two live faults found, not caused by the sweep: the Samsung HC feed has been dead since the 17:24 rotation (last good post 17:11), and iOS `Auto Export` has been 401-looping every ~90 s since 18:16. Both need a token entered on the phone.**
- 2026-09-11 v1.3.1 — DEPLOYED 18:07 IST. **"Today so far" Watch strip on the vitals page.** `patch_watch_strip.py`, 4 anchors (styles, `w_ring_svg` gains a `size` argument, strip renderer, one line wiring it into `home()` after the flags and before the check-in form). Previewed in a full staging copy at `/root/fitlog_stage` before the live file was touched — four states rendered against a copy of the live DB (logging path, fresh, stale, empty). Placement above the verdict on checked-in days confirmed by the owner. Suites: new `test_watch_strip.py` **52/52**, plus 23/23, 36/36, 18/18, 19/19, 39/39, 10/10, 12/12, 12/12 unchanged; kb_lint PASS, smoke 53/53 — rule engine untouched. Live strip reads `as of 17:31 IST · 35 min ago`, steps 3874, Move 319/300 kcal, Exercise 17/30 min, Stand 12/12 h. Rollback: `app.py.bak-strip-20260911_180622`, `fitlog.db.bak-strip-20260911_180622`.
- 2026-09-11 v1.3.0 — DEPLOYED 17:31 IST. **iOS payload support + Apple Watch view + HC token rotation.** Four anchor-verified patchers, each dry-run first: `patch_ios_payload.py` (6 anchors — shape routing, UTC→IST, generalised record store), `patch_ios_source_isolation.py` (5 anchors — `source` column on `health_hc_records`, recompute filtered by source, `source` bound as a parameter instead of concatenated into SQL), `patch_ios_ring_goals.py` (1 anchor — ring goals as context-only metrics), `patch_watch_page.py` (3 anchors — styles, nav, `/watch` route). Suites: 23/23, 36/36, 18/18, **19/19 new isolation**, **39/39 new watch page**, plus 10/10, 12/12, 12/12 unchanged; kb_lint PASS and smoke 53/53 — rule engine untouched. Backfill: `health_raw` id 11 re-POSTed through `https://fit.dr-manoj.in` → 604 records, 2 workouts, dates 2026-09-09/10/11, steps 439 / 2305 / 3874, healthconnect 1206 / 2242 preserved as separate rows. `FITLOG_HC_TOKEN` rotated (the old value had leaked into the OLS access log in clear text); old token → 401, new → 200, scope re-verified (401 on read and on `source=applewatch`). No phone change needed — Samsung is parked. Rollback: `app.py.bak-watch-20260911_173020`, `health_ingest.py.bak-preios-20260911_172110`, `fitlog.db.bak-ios-20260911_172110`, `ingest.env.bak-rotate-20260911_172449`.
- 2026-09-11 Observability — DEPLOYED. gunicorn access logging on (`gunicorn_conf.py` + `-c` in `fitlog.service`, `LogsDirectory=fitlog`, 30-day logrotate), URL tokens redacted. Diagnosis of "no phone data since 10-Sep": every phone POST **did** arrive and was answered `401` by FitLog — OLS access log carries them hourly (Android `okhttp/4.12.0`) and in bursts (iOS `Health Webhook/4`, `Auto Export/20260909.1`). Android sends `?source=<token>` — token in the wrong parameter, no `k=`, no `source` — so `_authorised_hc` is never reached. iOS sends `?source=applewatch` with no usable `Authorization` header; `k=` cannot substitute, being healthconnect-scoped by design. Both corrected shapes replayed `200` through OLS. Probe rows removed: `health_raw` back to id 4 only, `health_metrics` 0. **Open:** `FITLOG_HC_TOKEN` is in clear text in the OLS log and, via the mis-set `source=`, in the new gunicorn log — rotate once both phones are reconfigured.
- 2026-09-11 v1.2.0 — activity feed for GutLog, Home *Activity today* card, mindful minutes, indoor workouts (`patch_fitlog_v120.py`, app.py + health_ingest.py). All earlier suites unchanged; `test_activity_feed.py` 12/12.
- 2026-09-11 v1.1.0 — DEPLOYED 07:37 IST. W03 and the Meds page read doses from GutLog's feed (`patch_fitlog_v110.py`, 4 anchors). Suites: smoke 53/53, kb_lint, 23/23, 36/36, 12/12 unchanged; new `test_gutlog_feed.py` 10/10 (0/10 against v1.0.1, as it should). On-server: all suites green, `verify_phase_c.py` 9/9. Rollback: `app.py.bak-v110-20260911_073740`.
- 2026-09-10 Phase 3.5b — DEPLOYED. HC Webhook support (`patch_hc_support.py`) + `FITLOG_DB` pinning (`patch_db_pin.py`). Separate URL-borne token for the healthconnect feed, scope-verified in production. Record-level ingest with interval-keyed upsert. Suites: 23/23, 36/36, 12/12 on Python 3.9.25. Live probe through OLS confirmed R1: a redelivered interval grown 600→900 resolved to 900, not 1500. Probe removed, all four ingest tables back to 0. `app.py` untouched (MD5 `fb8520e5…`, unchanged since Phase 3.5). Rollback: `health_ingest.py.bak_20260910_083629` (pre-HC), `health_ingest.py.bak_20260910_084058` (pre-pin), `fitlog.db.bak_20260910_083629`.
- 2026-09-10 Phase 3.5 — DEPLOYED. Wearable ingest: `health_ingest.py` blueprint + `health_metrics`/`health_workouts`/`health_raw` tables + rule S01. Registered via anchor-verified patcher (`patch_register_ingest.py`, anchor = Flask() at line 16); diff vs pre-deploy backup is exactly 4 added lines, nothing else. On-server smoke 23/23 on Python 3.9.25. Public verification through OLS: unauth 401, auth 200, end-to-end write + S01 read-back, probe rows removed (all three tables back to 0). No owner-key exemption needed — gate is a per-route decorator, not `before_request`. Verdict logic untouched. Rollback: `app.py.bak_20260910_075535`, `fitlog.db.bak_20260910_075503`.
- 2026-08-02 v1.0.1 — DEPLOYED. Pre-3.12 f-string fixes (2 sites) applied on server via anchor-verified in-place patcher (`patch_fitlog.py`; rollback at app.py.bak). On-server smoke: 53/53. HTTPS health verified. Manual backup run: integrity ok. Root cause: build container ran Python 3.12 (PEP 701), server runs 3.9 — runtime now pinned in repo CLAUDE.md.
- 2026-08-02 v1.0 — initial build. 53/53 smoke local. One fix during build: protocol condition-parser slice offset for `leg>=` operator.

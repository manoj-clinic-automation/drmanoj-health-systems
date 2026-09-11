# FitLog — DOSSIER (v1.3.2)

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

### Schemas
- `health_metrics` — id, date, metric, value, unit, source, ingested_at ·
  `UNIQUE(date, metric, source)` → repeat POSTs upsert, never duplicate
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

## Token handling — open issue (2026-09-11)

**The OLS access log records the full request line (`%r`), so the `?k=`
healthconnect token is written to disk in clear text on every HC post.**
Rotation does not fix this; it only invalidates what is already logged.
The next successful HC post re-leaks whatever the current token is.

The real fix is changing `logFormat` in
`/usr/local/lsws/conf/vhosts/fit.dr-manoj.in/vhost.conf` from `%r` to
`"%m %U"`, which drops the query string. Deferred by the owner
(2026-09-11): it needs a graceful OLS restart affecting every vhost on the
box, and CyberPanel may rewrite the file. **Schedule it.** Until then,
treat `FITLOG_HC_TOKEN` as compromised on every rotation cycle.

There is **no logrotate rule** for that file — OLS self-rolls at
`rollingSize 10M` / `keepDays 10`, and at ~100 KB it will not roll for
months. Clearing it means truncating in place (`: > file`); litespeed
holds the fd in append mode, so it keeps writing from offset 0. Do not
delete it — that orphans the fd.

The gunicorn log is unaffected: `RedactingLogger` writes `k=<redacted>`.

## Observability — request logging (2026-09-11)

Two logs answer "did the request arrive, and what did we say back?".

| Log | Written by | Path | Notes |
|---|---|---|---|
| OLS vhost access log | OpenLiteSpeed | `/home/fit.dr-manoj.in/logs/fit.dr-manoj.in.access_log` | Config: `/usr/local/lsws/conf/vhosts/fit.dr-manoj.in/vhost.conf`. Rolls at 10 M, `keepDays 10`. **Records the full query string — the `?k=` HC token lands here in plain text.** |
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
| `test_activity_feed.py` | 12/12 | v1.2.0: real GutLog + real FitLog on loopback — workout classes, mindful + indoor ingest, feed auth and content (best source only), GutLog pulling watch data (merge), Home card, escaping, cache not mutated, scratch-DB isolation, GutLog down, token file missing |
| `test_gutlog_feed.py` | 10/10 | v1.1.0: real GutLog on loopback — W03 from GutLog alone, sleep and unmatched negatives, union dedupe, FitLog-only unchanged, window, Meds page, escaping, scratch-DB isolation, GutLog down |

`test_health_ingest.py` does **not** exercise the HC path — every
healthconnect case in it uses the legacy shape, so `is_hc` is false. It scored
23/23 against HC code carrying two data bugs. Never treat it alone as a gate
on HC changes.

## Changelog
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

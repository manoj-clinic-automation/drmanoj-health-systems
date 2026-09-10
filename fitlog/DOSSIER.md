# FitLog — DOSSIER (v1.0.1)

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
2. Add current med epochs at /tests (e.g., nortriptyline taper dates)
3. Log known future events (OT lists, travel) in advance — enables F05 protection + PRE cards
4. Daily check-in ≥14 days; Sunday re-tests; tune thresholds in rules.json as needed (re-run smoke after edits)

## Known deferrals (by design)
Dashboard trends/epoch-band charts (Phase 3) · media enrichment image/video (Phase 3, schema slots ready) · Hyperice protocol pages · manual/encyclopedia · ~~wearables (Phase 4)~~ → **capture** shipped in Phase 3.5; rule consumption still deferred by design

## Test suites
Run all three on the server. They gate the restart.

| Suite | Count | Covers |
|---|---|---|
| `test_health_ingest.py` | 23/23 | Base ingest, auth, S01, idempotency |
| `test_hc_ingest.py` | 36/36 | HC Webhook path, R1/R2 regressions, negative controls |
| `test_db_pin.py` | 12/12 | Non-default DB filename resolution |

`test_health_ingest.py` does **not** exercise the HC path — every
healthconnect case in it uses the legacy shape, so `is_hc` is false. It scored
23/23 against HC code carrying two data bugs. Never treat it alone as a gate
on HC changes.

## Changelog
- 2026-09-10 Phase 3.5b — DEPLOYED. HC Webhook support (`patch_hc_support.py`) + `FITLOG_DB` pinning (`patch_db_pin.py`). Separate URL-borne token for the healthconnect feed, scope-verified in production. Record-level ingest with interval-keyed upsert. Suites: 23/23, 36/36, 12/12 on Python 3.9.25. Live probe through OLS confirmed R1: a redelivered interval grown 600→900 resolved to 900, not 1500. Probe removed, all four ingest tables back to 0. `app.py` untouched (MD5 `fb8520e5…`, unchanged since Phase 3.5). Rollback: `health_ingest.py.bak_20260910_083629` (pre-HC), `health_ingest.py.bak_20260910_084058` (pre-pin), `fitlog.db.bak_20260910_083629`.
- 2026-09-10 Phase 3.5 — DEPLOYED. Wearable ingest: `health_ingest.py` blueprint + `health_metrics`/`health_workouts`/`health_raw` tables + rule S01. Registered via anchor-verified patcher (`patch_register_ingest.py`, anchor = Flask() at line 16); diff vs pre-deploy backup is exactly 4 added lines, nothing else. On-server smoke 23/23 on Python 3.9.25. Public verification through OLS: unauth 401, auth 200, end-to-end write + S01 read-back, probe rows removed (all three tables back to 0). No owner-key exemption needed — gate is a per-route decorator, not `before_request`. Verdict logic untouched. Rollback: `app.py.bak_20260910_075535`, `fitlog.db.bak_20260910_075503`.
- 2026-08-02 v1.0.1 — DEPLOYED. Pre-3.12 f-string fixes (2 sites) applied on server via anchor-verified in-place patcher (`patch_fitlog.py`; rollback at app.py.bak). On-server smoke: 53/53. HTTPS health verified. Manual backup run: integrity ok. Root cause: build container ran Python 3.12 (PEP 701), server runs 3.9 — runtime now pinned in repo CLAUDE.md.
- 2026-08-02 v1.0 — initial build. 53/53 smoke local. One fix during build: protocol condition-parser slice offset for `leg>=` operator.

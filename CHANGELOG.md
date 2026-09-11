# Changelog — drmanoj-health-systems

Personal (non-clinic) systems. Per-app detail lives in each app's `DOSSIER.md`;
this file is the cross-app timeline.

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

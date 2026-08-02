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
Dashboard trends/epoch-band charts (Phase 3) · media enrichment image/video (Phase 3, schema slots ready) · Hyperice protocol pages · manual/encyclopedia · wearables (Phase 4)

## Changelog
- 2026-08-02 v1.0.1 — DEPLOYED. Pre-3.12 f-string fixes (2 sites) applied on server via anchor-verified in-place patcher (`patch_fitlog.py`; rollback at app.py.bak). On-server smoke: 53/53. HTTPS health verified. Manual backup run: integrity ok. Root cause: build container ran Python 3.12 (PEP 701), server runs 3.9 — runtime now pinned in repo CLAUDE.md.
- 2026-08-02 v1.0 — initial build. 53/53 smoke local. One fix during build: protocol condition-parser slice offset for `leg>=` operator.

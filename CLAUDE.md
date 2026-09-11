# drmanoj-health-systems — CLAUDE.md

Read this first in any session touching this repo. Personal (non-clinic) systems of Dr. Manoj Agarwal.
Clinic automation lives separately in `drmanoj-clinic-automation` — do not mix.

## Server
- Hostinger VPS `srv1746119`, IP `93.127.195.49`, CyberPanel + OpenLiteSpeed
- **Runtime pins: Python 3.9.25 · SQLite 3.34.1 · Flask 3.1.3 · gunicorn 23.0.0**
- **Syntax rule: target Python 3.9.** No PEP 701 f-strings — never put backslash escapes or nested same-type quotes inside f-string `{...}` expressions; no `match` statements; no `X | Y` type unions. (2026-08-02 lesson: v1.0 shipped with 3.12-only f-strings, caught by on-server smoke run, fixed by in-place patcher.)
- DNS: GoDaddy · SSL: CyberPanel
- File delivery: **WinSCP** (never nano/terminal paste for multi-line files on this server)

## Apps & ports
| App | Subdomain | Port | DB (live path) |
|---|---|---|---|
| GutLog v3.6.0 | health.dr-manoj.in | 8020 | /root/gutlog/health3.db |
| RxGuard v1.1.0 | rx.dr-manoj.in | 8031 | /root/rxguard/ |
| FitLog v1.1.0 + Phase 3.5 | fit.dr-manoj.in | 8040 | /root/fitlog/fitlog.db (verified on server 2026-09-10) |

## Non-negotiable conventions
1. **Deterministic engines only** in safety/decision paths — rules as JSON knowledge files, no LLM calls in analysis. Every decision surfaces which named rules fired.
2. **Tests before deploy**: each app ships `tests/kb_lint.py` + `tests/smoke_test.py` (with negative controls). Run on server before `systemctl enable`. Re-run after any knowledge-file edit. **A green suite is only evidence for the code it actually executes** — FitLog's `test_health_ingest.py` scored 23/23 against an HC path carrying two data bugs, because every healthconnect case in it used the legacy payload shape and never entered the new branch. When adding a code path, add a suite that enters it, and check coverage before trusting a gate.
3. **Stack**: single-file Flask + SQLite + `python3 -m gunicorn` (portable, never hardcoded gunicorn path) + systemd with `EnvironmentFile=-` (optional env, no boot failures) + OLS reverse proxy.
4. **Backups**: Python `sqlite3.backup()` API (no sqlite3 CLI on server), 30-day retention, and **manually verify the actual live file path once** — silent cron success is not evidence of correctness (GutLog health.db/health3.db lesson).
5. **Auth pattern**: login password + separate owner key gating credential changes (GutLog v3.2 pattern). **Exception — machine-to-machine endpoints are bearer-gated, not owner-key gated.** FitLog `/api/ingest`, `/api/health/daily`, `/api/ingest/status` authenticate with a bearer token from `/root/fitlog/ingest.env` (mode 600, never in git); rotate by editing that file and restarting. Note `fitlog.service` loads `.env`, not `ingest.env` — the blueprint reads the file directly, so no unit edit is needed. FitLog's owner key is a per-route `@login_required` decorator, not a `before_request` hook, so blueprint routes bypass it structurally and need no exemption patch. The HC Webhook feed uses a **second, narrower** token (`FITLOG_HC_TOKEN`) carried in the URL as `?k=` because HC Webhook cannot send headers — scoped POST-only, `healthconnect`-only, no read access. A URL-borne token is acceptable *only* under that scoping. `ingest.env` also carries optional `FITLOG_DB`: `health_ingest.py` reads it from there (env > ingest.env > hardcoded default), which is what keeps the migration and the running app on the same database when it is not named `fitlog.db`.
5a. **Cross-app reads go through GutLog's feed** (`/api/feed/stack`, `/api/feed/doses`) — never another app's database file. Bearer token in `/root/gutlog/feed.token` (mode 600, created by GutLog, read in place by RxGuard and FitLog; rotate = delete + restart all three). Consumers **follow their live database**: a DB outside the app folder (any test suite) never reads the feed, so suites cannot be coloured by real data. Every consumer degrades to a message when GutLog is down.
5b. **GutLog's page is a Jinja template.** `{#`, `{{`, `{%` in new CSS/JS break the whole page at render time and pass `py_compile`. GutLog patchers refuse such tokens; `test_ui_now.py` (offline, Chromium) catches the rest and fails on any page JS error. Server suites never run page JS — v3.4.0 shipped with two deleted functions at 18/18.
5c. **Outside sources are free, verifiable and advisory.** RxGuard's `kb_sync.py` (cron, 30 min) reads NLM RxNorm/RxClass, openFDA labels, the FDA CYP table, DDInter 2.0 and PvPI; results are *drafts* with quoted source text and never enter the engine until the owner approves them in `/kb`. The curated `knowledge/*.json` always wins over the approved overlay (`*.local.json`, never committed). Only molecule names leave the server. Downloaded caches live in `knowledge/cache/` (gitignored). No paid or licensed source without the owner's decision.
6. **Docs**: per-app `DOSSIER.md` is the single source of truth; sync VPS ↔ GitHub ↔ Notion Tech & Systems Register after every change (Register data source: `e2e5e030-efc6-41a3-8f8a-70e808aaa5cb`; use `insert_content`/append for Notion additions, not replace_content).
7. Owner communicates tersely; deliver working files, confirm actions briefly; preserve rollback copies before replacing deployed files.

## Repo layout
```
CLAUDE.md            <- this file
fitlog/              <- complete: app.py (v1.0.1), knowledge/, tests/, DOSSIER.md, service, backup, patcher, spec
rxguard/             <- COMPLETE: app.py (incl. switcher), knowledge/, validation_cases, tests, service, backup.sh, env.example
gutlog/              <- COMPLETE: app.py (v3.2 incl. switcher), gutlog.service, backup.sh, requirements, README
ops/                 <- cross-app patchers (patch_switcher.py)
```
Repo sync rule: after any on-server patch, pull the changed file back into this repo same day. Per-app GAPS.md tracks capture status. NEVER commit: live DBs, .secret/.env files, logs, venv.

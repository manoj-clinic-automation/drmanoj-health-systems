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
| App | Subdomain | Port | DB (live path) | Health check |
|---|---|---|---|---|
| GutLog v3.28.0 | health.dr-manoj.in | 8020 | /root/gutlog/health3.db, plan PDFs in /root/gutlog/plans_files/ | `/healthz` → `ok 3.28.0` (text/plain) |
| RxGuard v1.8.2 | rx.dr-manoj.in | 8031 | /root/rxguard/ | `/healthz` → `ok 1.8.2` (text/plain) |
| FitLog v1.7.1 | fit.dr-manoj.in | 8040 | /root/fitlog/fitlog.db (verified on server 2026-09-10) | `/health` → `{"app","version","ok"}`. Note the spelling: **`/health`, not `/healthz`** |

**The endpoints are authoritative — curl them, and believe what they say.**
Each app's version lives in one `APP_VERSION` constant that its health route
reads, and **every release bumps that constant**; a release that forgets is a
release that lies about itself. Both faults this rule exists for were live on
2026-09-20: GutLog had no health route at all while three briefs told sessions
to read `/healthz` on it (a 404 is indistinguishable from a broken deploy), and
FitLog's `/health` reported 1.3.1 through four releases. Fixed in GutLog
v3.27.2 and FitLog v1.7.1, each with an assertion that the body carries the
app's own version and nothing from the record — these are the routes with no
login in front of them.

## Non-negotiable conventions
1. **Deterministic engines only** in safety/decision paths — rules as JSON knowledge files, no LLM calls in analysis. Every decision surfaces which named rules fired.
2. **Tests before deploy**: each app ships `tests/kb_lint.py` + `tests/smoke_test.py` (with negative controls). Run on server before `systemctl enable`. Re-run after any knowledge-file edit. **A green suite is only evidence for the code it actually executes** — FitLog's `test_health_ingest.py` scored 23/23 against an HC path carrying two data bugs, because every healthconnect case in it used the legacy payload shape and never entered the new branch. When adding a code path, add a suite that enters it, and check coverage before trusting a gate.
2a. **An assertion is evidence only if it has been seen to fail.** Declare every new assertion in a `new_assertions_*.json` manifest and run `python3 tools/NEGATIVE_CONTROL.py --manifest <it>` before deploy: it reconstructs the previous build (patcher `--reverse`), requires each new assertion to appear in that run's failure list, and where the previous build already satisfied the property, breaks that property on purpose in a copy of the current app instead — an assertion never observed failing fails the gate by name. (Phase L: a card-scoped contrast check passed against the previous build and was moved to a screen-wide one that did not; the widened version then found five real dark-mode faults.)
3. **Stack**: single-file Flask + SQLite + `python3 -m gunicorn` (portable, never hardcoded gunicorn path) + systemd with `EnvironmentFile=-` (optional env, no boot failures) + OLS reverse proxy.
4. **Backups**: Python `sqlite3.backup()` API (no sqlite3 CLI on server), 30-day retention, and **manually verify the actual live file path once** — silent cron success is not evidence of correctness (GutLog health.db/health3.db lesson).
5. **Auth pattern**: login password + separate owner key gating credential changes (GutLog v3.2 pattern). **Exception — machine-to-machine endpoints are bearer-gated, not owner-key gated.** FitLog `/api/ingest`, `/api/health/daily`, `/api/ingest/status` authenticate with a bearer token from `/root/fitlog/ingest.env` (mode 600, never in git); rotate by editing that file and restarting. Note `fitlog.service` loads `.env`, not `ingest.env` — the blueprint reads the file directly, so no unit edit is needed. FitLog's owner key is a per-route `@login_required` decorator, not a `before_request` hook, so blueprint routes bypass it structurally and need no exemption patch. The HC Webhook feed uses a **second, narrower** token (`FITLOG_HC_TOKEN`) carried in the URL as `?k=` because HC Webhook cannot send headers — scoped POST-only, `healthconnect`-only, no read access. A URL-borne token is acceptable *only* under that scoping — **and only while nothing writes the URL down.** 2026-09-20: fit.dr-manoj.in's OLS access log had logged the full query string on every request since 11 Sep, so the token sat in cleartext in 392 log lines with their own retention and their own readers. That vhost's `logFormat` now logs `"%m %U %H"` in place of `"%r"`, so the path is recorded without the query string; method and protocol are kept because the Watch diagnosis reads them. **Only fit's vhost was changed** (backup: `vhost.conf.bak-noquery-20260920_130656`); the other eight still log `%r`, which is right — they carry no secret in a URL. If a future feed ever needs a key in a URL, change that vhost's format in the same way first. `ingest.env` also carries optional `FITLOG_DB`: `health_ingest.py` reads it from there (env > ingest.env > hardcoded default), which is what keeps the migration and the running app on the same database when it is not named `fitlog.db`.
5a. **Cross-app reads go through GutLog's feed** (`/api/feed/stack`, `/api/feed/doses`) — never another app's database file. Bearer token in `/root/gutlog/feed.token` (mode 600, created by GutLog, read in place by RxGuard and FitLog; rotate = delete + restart all three). Consumers **follow their live database**: a DB outside the app folder (any test suite) never reads the feed, so suites cannot be coloured by real data. Every consumer degrades to a message when GutLog is down.
5b. **GutLog's page is a Jinja template.** `{#`, `{{`, `{%` in new CSS/JS break the whole page at render time and pass `py_compile`. GutLog patchers refuse such tokens; `test_ui_now.py` (offline, Chromium) catches the rest and fails on any page JS error. Server suites never run page JS — v3.4.0 shipped with two deleted functions at 18/18.
5c. **Outside sources are free, verifiable and advisory.** RxGuard's `kb_sync.py` (cron, 30 min) reads NLM RxNorm/RxClass, openFDA labels, the FDA CYP table, DDInter 2.0 and PvPI; results are *drafts* with quoted source text and never enter the engine until the owner approves them in `/kb`. The curated `knowledge/*.json` always wins over the approved overlay (`*.local.json`, never committed). Only molecule names leave the server. Downloaded caches live in `knowledge/cache/` (gitignored). No paid or licensed source without the owner's decision.
5d. **The health record never enters this repository.** Reports, lab values, problems and precautions live in GutLog on the server (`rec_docs`, `rec_labs`, `rec_plan`, `records_profile.local.json`) and in the owner's PC folder (`records_manifest.local.json`, the PDFs). Code and synthetic tests only here. New reports are transcribed exactly as printed, conflicts flagged, never smoothed. **Plan documents (v3.28.0 `/plans`) are the same class**: they live in `/root/gutlog/plans_files/` under a sha256 name, in the database, and in the owner's PC folder — never in the tree, and `*.pdf` is gitignored. **A plan's TITLE names medicines**, so it belongs in the database and the PDF only: never in tracked code, fixtures, docstrings or commit messages. `seed_plan.py` takes the title as an argument for exactly that reason, and tests use "Plan A". 2026-09-22: a plan PDF arrived in `fitlog-ingest/` neither tracked nor ignored, so `git add -A` would have staged it, and its **filename alone** named a medicine. NO_SECRETS refused it — but note which check did: **D**, the tracked-binary rule. B and C both reported clean, because they read text and a PDF is bytes, and neither ever looks at a filename.
5e. **One sign-in across the three apps (HEALTH_SSO_V1, 2026-09-20).** A plain page load with no session goes round the ring gutlog → rxguard → fitlog → gutlog via `/sso/vouch`; the first app that has a session mints an HMAC-SHA256 ticket bound to ONE receiving app, valid 60 s, usable once (nonce in the receiver's own `sso_used` table). API/XHR calls are never bounced; logout sets a hold, so a locked app stays locked until its own password; `next` is only ever a path, and targets are only the three configured origins. The shared key is `/root/health-sso.key`, mode 600, root — **no key file means every app behaves exactly as before**, which is the off switch. Each app's own session secret must be set and ≥32 chars first: a FitLog without `FITLOG_SECRET` falls back to a key derived from its database path, which is guessable, so it must vouch for no one. `health_sso.py` sits beside each `app.py` (identical copies; `ops/` is the master). Patch with `ops/patch_sso.py`, test with `ops/test_sso.py`.

6. **Docs**: per-app `DOSSIER.md` is the single source of truth; sync VPS ↔ GitHub ↔ Notion Tech & Systems Register after every change (Register data source: `e2e5e030-efc6-41a3-8f8a-70e808aaa5cb`; use `insert_content`/append for Notion additions, not replace_content).
7. Owner communicates tersely; deliver working files, confirm actions briefly; preserve rollback copies before replacing deployed files.

## Repo layout
```
CLAUDE.md            <- this file
fitlog/              <- AUTHORITATIVE for FitLog: app.py, health_ingest.py, tests, DOSSIER.md, service, backup, patchers, spec. Patch and test here; this is what must match the server.
fitlog-ingest/       <- the owner's WORKING folder (holds gutlog/ copies too). Duplicate copies are deliberate, drift is not: PUBLISH_HEALTH.bat runs tools/CHECK_FOLDER_PARITY.py and REFUSES if a file here differs from the one app folder that also holds it, line endings included.
rxguard/             <- COMPLETE: app.py (incl. switcher), knowledge/, validation_cases, tests, service, backup.sh, env.example
gutlog/              <- COMPLETE: app.py (v3.2 incl. switcher), gutlog.service, backup.sh, requirements, README
ops/                 <- cross-app patchers (patch_switcher.py)
```
Repo sync rule: after any on-server patch, pull the changed file back into this repo same day. Per-app GAPS.md tracks capture status. NEVER commit: live DBs, .secret/.env files, logs, venv.

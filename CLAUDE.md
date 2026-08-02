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
| GutLog v3.2 | health.dr-manoj.in | 8020 | /root/gutlog/health3.db |
| RxGuard | rx.dr-manoj.in | 8031 | /root/rxguard/ |
| FitLog v1.0 | fit.dr-manoj.in | 8040 | /root/fitlog/fitlog.db |

## Non-negotiable conventions
1. **Deterministic engines only** in safety/decision paths — rules as JSON knowledge files, no LLM calls in analysis. Every decision surfaces which named rules fired.
2. **Tests before deploy**: each app ships `tests/kb_lint.py` + `tests/smoke_test.py` (with negative controls). Run on server before `systemctl enable`. Re-run after any knowledge-file edit.
3. **Stack**: single-file Flask + SQLite + `python3 -m gunicorn` (portable, never hardcoded gunicorn path) + systemd with `EnvironmentFile=-` (optional env, no boot failures) + OLS reverse proxy.
4. **Backups**: Python `sqlite3.backup()` API (no sqlite3 CLI on server), 30-day retention, and **manually verify the actual live file path once** — silent cron success is not evidence of correctness (GutLog health.db/health3.db lesson).
5. **Auth pattern**: login password + separate owner key gating credential changes (GutLog v3.2 pattern).
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

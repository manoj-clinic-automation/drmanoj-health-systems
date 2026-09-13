Repo: D:\dr-manoj-git\drmanoj-health-systems  — read CLAUDE.md first.
Work folder: D:\dr-manoj-git\drmanoj-health-systems\fitlog-ingest

FitLog has two confirmed bugs. The patcher is written and already tested offline
against the exact current app.py. Apply it, test it, and hand back one short
paragraph. Do not give me procedural steps to run.

PATCHER (already in place, do not rewrite unless a test fails):
  fitlog-ingest\patch_fitlog_ist_and_steps.py
It targets app.py via the FITLOG_APP env var, so point it at the repo copy:
  fitlog\app.py
It refuses to write unless all three anchors match exactly once, compiles the
result first, keeps a timestamped .bak, and is idempotent.

VERIFIED: fitlog\app.py in this repo is byte-identical to live /root/fitlog/app.py
(sha256 2a00ada7b7686484609c, 64496 bytes, checked 13-Sep-2026). The repo tracks
the server. Keep it that way per the repo sync rule in CLAUDE.md.

BUG 1 — workout times are shown in UTC.
Apple sends '2026-09-11T01:41:29.553Z'. watch_activity() in fitlog\app.py does
(r["start_ts"] or "")[:19], which drops the Z, and GutLog then renders it as
local time. That walk was 07:11 IST, not 01:41. There is no timezone handling
anywhere in the file. The patch adds an _ist() helper and converts at read-out.
Checked against his real stored rows:
  2026-09-11T01:41:29.553Z -> 2026-09-11T07:11:29   (morning walk)
  2026-09-10T16:28:41.272Z -> 2026-09-10T21:58:41   (post-dinner walk)

BUG 2 — step counts.
health_ingest.SOURCE_PRECEDENCE = ("applewatch","healthconnect","manual") is
applied to every metric. On 12-Sep applewatch held 301 steps and healthconnect
held 2430, so 301 won and 2430 was discarded. Steps are a coverage metric, not
a sensor-quality one. The patch takes the largest step count for the day inside
watch_activity(); every other metric keeps the existing precedence untouched.
Verified over 14 days: only 12-Sep changes (301 -> 2430). 06 and 07 Sep stay at
18 and 13 because no second source exists for those days. healthconnect supplies
steps only — applewatch is the sole source of resting_hr, hrv_ms, spo2_pct,
exercise_minutes, stand_hours, so precedence must stay for those.

TASK 1: run the patcher against fitlog\app.py, then run the FitLog test suite.
CLAUDE.md rule 2 applies — a green suite is only evidence for the code it
actually executes, so add a case that enters _ist() and one that enters the
steps-max branch. Then tell me to run PUBLISH_HEALTH.bat.

TASK 2 (investigate, then fix forward-looking only): health_workouts.date is
also derived from the UTC stamp, so activity before 05:30 IST is filed to the
PREVIOUS day. His walks are around 05:00–07:30, so this matters and it is in the
ingest path (health_ingest.py), not the read path. Fix how NEW rows are dated.
Do NOT rewrite stored history — report how many existing rows are affected and
leave them. recompute_apple_daily.py may be the right place to offer a separate
backfill later; do not run it now.

TASK 3: fitlog-ingest\ and fitlog\ hold duplicate copies of several patchers and
tests (health_ingest.py exists in both, at different sizes — 45835 vs 32783).
Say which is authoritative and whether the duplication is deliberate. Do not
reorganise anything without telling me first.

ALREADY DONE TODAY, do not repeat: GutLog's export_csv() now emits
'day,dtime,medicine,status,reason,effect,notes' — the CSV had been dropping the
TAKEN/SKIPPED/EXTRA status, so skipped doses read as doses taken. Applied and
verified live on the server. Pull /root/gutlog/app.py back into gutlog\ in this
repo per the sync rule, and update the GutLog DOSSIER and CHANGELOG.

HOUSE RULES (from CLAUDE.md): Python 3.9 syntax only. No sqlite3 CLI on the
server — use the Python API. Anchor-verified in-place patchers, compile-check
before write, .bak rollback, idempotent, tests before any restart. Never print
secrets or tokens — ingest.env, feed.token and the HC token stay out of output
and out of git. The health record never enters this repository. All times IST.
Update the per-app DOSSIER.md and CHANGELOG.md, and the Notion Tech & Systems
Register (data source e2e5e030-efc6-41a3-8f8a-70e808aaa5cb, use insert_content
or append, never replace_content).

REPORT BACK: one short paragraph — what was applied, test results including the
two new cases, what TASK 2 found, and the TASK 3 answer.

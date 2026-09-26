# REPORT — weight profile, member m3, physio p1 (26-Sep-2026)

Brief: `CLAUDE_CODE_PROMPT_weight_profile_v1.md` (run first, from `CLAUDE_CODE_PROMPT_run_both_v1.md`).

## For the owner

- **m3 is live** at `https://family.dr-manoj.in/m3/` on profile `weight`, seeded from the file in `fitlog-ingest\` (setup, meal windows, weight plan and targets, 9 medicines with salts and strengths, 4 schedule lines of which 1 weekly with its variants, 5 as-needed, 6 check-in rules, the plan PDF filed under Plans). Nothing had to be typed; the readiness check ran clean (18 of 18 steps, READY).
- **The physio p1 is live** at `https://family.dr-manoj.in/m3/physio/` — their own PIN, their own sign-in page naming them and m3, and only the programme, sessions, pain and walking entries and the monthly re-test. Readiness READY (10 of 10), including the proof that their sign-in opens nothing else of her record.
- **Both PINs are in** `/root/family/first-login.local.txt` on the server (root only; the lines `m3` and `p1`). Hand them over on WhatsApp and delete the lines. Never printed anywhere.
- **What she sees on day 1 (Sunday):** the This-week card first (weight and waist, injection countdown "today at 20:00", the milestone line "−5 % = X kg, N kg to go", steps today from the phone once Apple Health is connected — blank until then, never 0 — and protein against the target); her doses with the weekly injection under "Weekly · Sunday" and a reminder from 19:30; her meals with her own meal times (an 11:30 breakfast is breakfast); the Check-ins card with the Sunday weigh-in, and the other check-ins as they fall due (the side-effect check appears on Monday after the injection); the Physio tile once the physio writes the programme; the joint cards folded below; BP and the rest.
- **Two sheets** are in `fitlog-ingest\`: `Health_App_Getting_Started_<her name>.pdf` and `Physio_Page_Getting_Started_<physio's name>.pdf` (both gitignored). She should keep the phone in her pocket for steps; connecting Apple Health is the 5-minute step done together.
- **If a PHQ-9 answer needs you:** her page says "Please tell <you> today" and your Family page shows "Check-in flag today — please call" on her row, the same day only. Nothing else is automated.

## Live versions (curled after the deploy)

| Route | Answer |
|---|---|
| owner GutLog `/healthz` | `ok 3.40.0` |
| m1, m2, m3 `/m<n>/healthz` | `ok 3.40.0` |
| m3 `/rx/healthz`, `/fit/health` | `ok 1.9.0`, FitLog 1.8.0 |
| Kitchen `/kitchen/healthz` | `ok 1.1.0` (unchanged by this brief) |
| family tree | `/opt/family/code/20260926_162135` |

## What was built

- **GutLog v3.40.0** (`gutlog/patch_gutlog_v3400_weight.py`, 34 anchors, `--reverse` reproduces the 3.39.0 file byte for byte; schema 3.3.9): WEEKLY schedule line (weekday + time; Now page on its day only; reminder from 30 min before; next-day "taken late / skipped?"; stock per dose; variants, Retime, Day by day and the feed's shape unchanged); meal windows in the slot guess; check-ins (PHQ-9, PHQ-2, Epworth, side-effect check, weigh-in, measurements — due by rule, gone when answered, totals and bands by name; PHQ-9 item 9 → same-day flag on the owner's Family page); weight plan with milestones (crossed after two consecutive weigh-ins at or below); `/checkins` page with the chart; `/export/checkins.csv`; the This-week card; the joint cards folded under `weight`.
- **Family layer:** profile `weight` (`family_env.py`), Now order and the Physio tile (`entry_gut.py`), `family_physio.py` (the physio role: family PIN rules on their own `auth.db`, their own cookie scoped to `/m<n>/physio/`, four pages), `init_member.py --app seed` (the seed applied as the member), `stamp_member.py --seed`, `--physio --for`, `--slug p1 --for m2 | --reset-pin | --disable | --enable`, `--profile weight`; `readiness.py` for a weight member (weekly dose ticked, a check-in answered and removed, the plan present) and for a physio (`--slug p1`, scope proven); `family/getting_started_sheet.py` (PC only, reportlab) for the sheets.
- **Docs:** `family/DOSSIER_Family.md`, `gutlog/DOSSIER_GutLog.md`, `CHANGELOG.md`, `CLAUDE.md` §5g (j).

## Tests

| Suite | Local (Python 3.14) | Server (Python 3.9, scratch members) |
|---|---|---|
| `family/test_family_e.py` (new) | 36/36 | 36/36 |
| `family/test_family_ui_e.py` (new, Chromium 300 px) | 12/12 | offline only |
| `test_family_a/auth/b/c/d`, `stamp_real` | 50, 23, 24, 20, 33 (A's status-endpoint key set now includes the boolean `flag`) | 50, 23, 24, 20, 33, 6 — all green |
| the browser suites `ui`, `ui_auth`, `ui_b`, `ui_c`, `ui_d`, webauthn crypto | 14, 6, 6, 6, 10, 4 | offline only |
| owner GutLog `test_phase_a`, `test_phase_b`, `ops/test_sso` | 18/18, 16/16, 11/11 | 18/18, 16/16, 11/11 |

**Negative control** (`family/new_assertions_family_e.json`, 18 declared): run against the 3.40.0 file with `tools/NEGATIVE_CONTROL.py`. Every assertion the brief names was seen to fail on purpose: `anyday` (a weekly dose on the wrong day), `nolate`, `pillbox`, `nowindows` (meal window ignored), `nostamp` + `nototal` (check-in not stamped / total wrong), `noflag` + `noflagpage` (item-9 flag suppressed), `noafter`, `noadvance` + `onecross` (milestone not advancing), `zerosteps` (0 shown for no steps), `physioreach` (a physio cookie reaching the member's pages — caught by the suite's scope check and by `readiness.py --slug p1`), `seedleak` (seed data written into a tracked file — caught by the scan), `noweekly`, `widecookie`, `nowalk`, `readinesspain`. **Full run against the 3.40.0 file: 18 declared, 18 seen to fail, current build all-pass — RESULT: PASS.** (An earlier run showed 17: `zerosteps` had mutated a branch a member copy never takes, because members have links on and FitLog answers with no steps value; the mutation was moved to the final return.) Also seen and fixed on the way: `physioreach` first crashed the suite instead of failing the named check, because the member's plans and medicines APIs answer with JSON lists and the reach detector assumed dicts — the detector now treats any 200 with a JSON body as a reach, in the suite and in `readiness.py`.

## Server steps (all under the build lock, taken by hand as the brief asked; released at the end)

1. Files by scp (patcher to `/root/gutlog/`, the family layer to `/root/family/`, the seed and PDF to `/root/family/seeds/`, root 600).
2. `patch_gutlog_v3400_weight.py --check` then apply; backup `app.py.bak-v3400-20260926_162125`; `systemctl restart gutlog`; `/healthz` → `ok 3.40.0`; md5 of `app.py` equals the repository's.
3. Owner suites on the server: 18/18, 16/16, SSO 11/11.
4. `/root/family/upgrade_all.sh`: new tree built by allowlist (53 files), every family suite green on scratch members, m1/m2 migrated, Kitchen DB backed up (`kitchen-pre-upgrade-20260926_162135.db`), switch, every member on gut 3.40.0.
5. `stamp_member.py --slug m3 … --profile weight --seed … --password-file …`: three databases as the member, the seed applied as the member (counts above), Kitchen tokens, physio p1 stamped, routes written (vhost backup `vhost.conf.bak-family-20260926_162249`), services started; the seed copy and the PDF copy deleted from the member folder; the PDF deleted from `/root/family/seeds/` (the seed JSON kept there, root 600, for a re-stamp).
6. `readiness.py --slug m3` READY, `readiness.py --slug p1` READY.

## Notes and limits

- **Stock for the weekly injection:** it is counted per dose (never as a daily pillbox). Because its dose strengths vary, the stock card counts it once each strength is linked to its pen product — the same rule as the owner's variable-dose medicine. Nothing to do until she has pens to count.
- **As-needed medicines' notes** from the seed are kept as data (`settings.prn_notes`); they are not shown on a screen yet.
- **"Mirror + caretaker read them":** the caretaker reads check-ins through her copy (and the same-day flag through the Family page). Nothing of hers enters the owner's Health Mirror, by §5g.
- The sheet generator needs reportlab, which is on the PC and not on the server; the sheets are made on the PC.
- The `--list` command prints display names to the terminal (as before); none is in this report.

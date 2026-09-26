# REPORT — Kitchen search and browsing, food-table refresh, Kitchen key file, build lock (26-Sep-2026)

Brief: `CLAUDE_CODE_PROMPT_kitchen_search_v1.md` (run second, on what the weight-profile build left live).

## For the owner

- **Finding a recipe** is now one screen in three places — your GutLog's Family Kitchen page, every member's, and the kitchen-only book: tap **Person → Category** ("Everyone" first, each name with its count, then only that person's categories with counts) or **Category → Person**; one **search box** that matches the dish, any ingredient, the category and the contributor's name, in Hindi or English (lauki = bottle gourd = ghiya, bhindi = okra = lady finger, ragi = finger millet, and about seventy more lines in `family/food_aliases.json`); **filters** that combine with either view: Vegetarian / Eggetarian / No onion-garlic / Jain, Breakfast / Lunch / Dinner / Snack / Sweet, High-protein (10 g or more a serving), Quick (20 min or less where a time is known — stated on the recipe, or read from the method), Top-rated, New this week, Made it by me. Counts change with the filters, an empty result says what to loosen, and the last person or category opened is remembered on that phone. Results are grouped by category, each card "Recipe by <Name>".
- **Recipe cards now show fat, carbohydrate and calcium** per serving (from the refreshed USDA table) instead of "—", with a line saying which ingredients a figure leaves out.
- **The Kitchen's model keys need their own file.** Run this one line on the server, once (it copies only the two variables out of the clinic's file; nothing from here reads that file):

```bash
grep -E '^(ANTHROPIC_API_KEY|SARVAM_API_KEY)=' /root/wa/.env | install -o root -g fam_kitchen -m 640 /dev/stdin /root/family/kitchen_keys.env
```

  Until then the reader says so once per run in `/root/family/logs/kitchen_reader.log` and every shared recipe goes to the manual (fill-by-hand) screen. The command's shape was proved on the server with scratch files (result: root:fam_kitchen, mode 640, the two lines only).

## Live versions (curled after the deploy)

| Route | Answer |
|---|---|
| owner GutLog `/healthz` | `ok 3.41.0` |
| m1, m2, m3 `/m<n>/healthz` | `ok 3.41.0` |
| Kitchen `/kitchen/healthz` | `ok 1.2.0` |
| RxGuard, FitLog (unchanged) | `ok 1.9.0`, FitLog 1.8.0 |
| family tree | `/opt/family/code/20260926_171622` |

## What was built

1. **Build lock in the tools** — `family/build_lock.sh` (`take_build_lock "<brief>"`), sourced by `upgrade_all.sh`, `install_family.sh`, `install_kitchen.sh`: `mkdir /root/deploy/.claude_code_build.lock`; held → wait and re-check every 2 minutes; older than 3 hours AND owner file says `finished` → taken over with a log line; never removed otherwise; `owner` file names this repository and the brief; a trap releases it on exit, also on failure. `family/test_family_lock.py` (bash; server) proves the wait, the take-over, the refusal to take an unfinished old lock, and the trap: **5/5 on the server**, and it now runs inside every `upgrade_all.sh` self-test.
2. **Kitchen key file** — `kitchen_reader.py` reads `/root/family/kitchen_keys.env` (default; `KITCHEN_KEYS_ENV` for a test), only `ANTHROPIC_API_KEY` and `SARVAM_API_KEY` (`KEY_NAMES`), never `/root/wa/.env` (not read by this build; the one-line command above is the owner's). Missing file: one clear line per run with the `install` command, drafts to the manual screen. Tested with a scratch key file (F05) and, on the server, with a scratch missing path.
3. **Finding recipes** — Kitchen 1.2.0 (`family/kitchen.py`): `browse()` answers the list under the current view and filters plus the counts of the other axis under the same filters; `query_terms()` with aliases (multi-word aliases matched as phrases; every word must match as a whole word or a word-start of three letters or more — a single letter never matches a substring); `MEAL_OF_GROUP`; `recipe_minutes()` (stated `minutes`, else the largest "N min / N hours" in the method or notes); `recipe_protein()` (the Kitchen's own sum, cached); `/api/browse` (bearer) and `/kitchen/k<n>/j/browse` (session; the member's saved preferences apply until the page sends its own list); recipes gained a `minutes` column (guarded). `kitchen_members.py`: the new Recipes tab, "Time to make" in the forms, the last person/category remembered (`localStorage`). GutLog v3.41.0 (`gutlog/patch_gutlog_v3410_kitchenfind.py`, 14 anchors, reversible): `/api/kitchen/browse` proxy, the same Recipes tab on `/kitchen`, "Time to make" in the Inbox form. `build_code.py` now names `food_aliases.json` and `build_lock.sh` (`FAMILY_DATA`).
4. **Food-table refresh** — `gutlog/build_food_table.py` extended (nutrient ids 1004 fat, 1005 carbohydrate, 1087 calcium, appended as columns 5–7); rebuilt from the same SR Legacy download (`FoodData_Central_sr_legacy_food_csv_2018-04.zip`, downloaded fresh on the PC, 6 MB); 2 rows left out for naming a term on the local clinical list, as before. `gutlog/test_food_table_refresh.py` **6/6**: every food of the old table is in the new one with the same id, name, protein, kcal and fibre; the late-snack foods (9 ids) and the seeded dishes' components keep their id and values; fat and carbs present for every food in use; the three columns filled on more than 95 % of rows. **Size 756,233 bytes (was 639,728); 7,791 foods; load 0.010 s on the server's Python 3.9** (0.028 s on the PC). GutLog's `food_lookup()` and `ing_food()` return the three; a food from the person's own list that came from the table keeps its row's values (`source_ref` id); `recipe_nutrition()` returns `partial` so a figure is never silently short; `kitchen_nutrition.py` the same, so a kitchen member's card still equals a full member's unadjusted card (D08).

## Tests

| Suite | Local (Python 3.14) | Server (Python 3.9, scratch members) |
|---|---|---|
| `family/test_family_f.py` (new) | 16/16 | 16/16 |
| `family/test_family_ui_f.py` (new, Chromium 300 px, GutLog page + kitchen book) | 14/14 | offline only |
| `family/test_family_lock.py` (new) | needs bash — not run on the PC | 5/5 |
| `gutlog/test_food_table_refresh.py` (new) | 6/6 | 6/6 (first run, old vs new) |
| `test_family_a/auth/b/c/d/e`, `stamp_real` | C's C02 updated (a matched card now has a fat figure) — 20/20; D 33/33 and `ui_d` 10/10 after the table change | 50, 23, 24, 20, 33, 36, 6 — all green |
| owner `test_phase_a`, `test_phase_b` | 18/18, 16/16 | 18/18, 16/16 |

**Negative control** (`family/new_assertions_family_f.json`, 17 declared): **17 seen to fail, current build all-pass — RESULT: PASS.** The controls the brief names: `personleak` (the person filter leaking others' recipes), `noname` (search ignoring contributor names), `noalias` (the alias file ignored); also `allcats`, `allpeople` (counts not narrowing), `noingredient`, `noquick`, `noprotein`, `nohint`, `noprefsaved`, `notop`, `gutnocols` + `kitchennocols` + `lookupnofat` (the new columns dropped on either side), `oldkeyfile`, `quietmissing`, `aliaspersonal`. Two entries first declared as `version` controls could never be seen: a version control swaps only the owner's app, while member copies come from the repository's `gutlog/` folder — they were replaced by mutations (noted in memory for later sessions).

**A real fault the browser suite found:** two taps in quick succession stacked two answers in the view (each render cleared the view at its start but appended after its own fetch). Fixed with a render token in both pages; `ui_f` now asserts one set of chips after a search.

## Server steps

1. Files by scp. `deploy_kitchen.sh` took the lock through `build_lock.sh` (owner file: this repository, the brief). Lock test 5/5.
2. Food table: backup `food_table_usda.json.bak-refresh-20260926_1713…`, new table in place, `test_food_table_refresh.py` 6/6 against the old one.
3. `patch_gutlog_v3410_kitchenfind.py --check` then apply; backup `app.py.bak-v3410-20260926_171345`; restart; `/healthz` → `ok 3.41.0`; md5 equals the repository's.
4. Owner suites 18/18, 16/16.
5. `upgrade_all.sh` (takes the lock itself): first run stopped on C02 (the old assertion that fat is absent) — **nothing switched**, as designed; C02 corrected and re-run: new tree (55 files), every family suite green incl. F and the lock test, m1/m2/m3 migrated, Kitchen DB backed up (`kitchen-pre-upgrade-20260926_171622.db`), switch, every member on gut 3.41.0, Kitchen 1.2.0.
6. Checks: the reader's default path in the live tree, the missing-file line (scratch path), the table on the tree (7,791 foods, 8 columns, 0.010 s), `https://family.dr-manoj.in/kitchen/healthz` 200.

## Backups made today

- `/root/gutlog/app.py.bak-v3400-20260926_162125`, `app.py.bak-v3410-20260926_171345`; `/root/gutlog/food_table_usda.json.bak-refresh-…`.
- `/root/backups/family/kitchen/kitchen-pre-upgrade-20260926_162135.db` and `…_171622.db`.
- `/usr/local/lsws/conf/vhosts/family.dr-manoj.in/vhost.conf.bak-family-20260926_162249` (routes for m3).
- Code trees kept: the three newest under `/opt/family/code/` (`ln -sfn <old> current` and a restart go back).

## Docs

`family/DOSSIER_Family.md` (finding recipes, the key file, the build lock, the table; tests), `gutlog/DOSSIER_GutLog.md` (v3.41.0 section; header now v3.41.0), `CHANGELOG.md`, `CLAUDE.md` apps table (GutLog 3.41.0, Kitchen 1.2.0, the key file, seeds, physio). **Notion Tech & Systems Register: not updated** — the Notion connector needs authorisation in claude.ai's connector settings and this session is non-interactive; the CHANGELOG entries are ready to paste.

## Blocked or left

- The Kitchen key file itself: the owner's one-line command above (this build may not read the clinic's file).
- Nothing else. No command asked for permission.

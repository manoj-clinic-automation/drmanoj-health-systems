# REPORT — nine family members invited to the Family Kitchen (26-Sep-2026)

Brief: `CLAUDE_CODE_PROMPT_kitchen_invites_v1.md`.

## For the owner

- **9 accounts READY:** `k1`–`k9`, in the order you gave. Each passed `readiness.py` 14/14 against the real address. Links are `https://family.dr-manoj.in/kitchen/k1/` … `/k9/`.
- **Sheets and messages** (on this PC, gitignored): `fitlog-ingest\Kitchen_Getting_Started_<Name>.pdf` (nine, one page each, large type, iPhone and Android) and `fitlog-ingest\Kitchen_invite_messages.txt` (two lines each: greeting + link, no PIN).
- **PINs:** `/root/family/first-login.local.txt` on the server, root only, nine lines (slug, name, link, PIN). Send each PIN as a second WhatsApp message, then delete the file.
- **Recipe by, full members:** checked live for m1, m2, m3 and you — a recipe from a GutLog says "Recipe by <their registry name>" (full names as registered, e.g. first name + surname; yours "Manoj Agarwal") and appears under "By person" exactly like a kitchen member's. Nothing needed fixing.
- **Your Family page** lists all nine under "Kitchen members", each "Last visit: not yet", "Recipes added: 0" until they come in.
- **One fix made:** the readiness check's own sign-in used to count as the person's "last visit"; it now puts the old value back.

## Technical record

**Live:** Kitchen `ok 1.2.0`, GutLog `ok 3.41.0`, family tree unchanged (`/opt/family/code/20260926_171622`). No app code changed, so no GutLog patch and no `upgrade_all.sh` run.

**Server run** (`/root/family/logs/invite_stamp.sh`, under `build_lock.sh` — taken for this brief, released at the end, no other build held it):
1. `test_family_d.py` 34/34 and `test_family_f.py` 17/17 on the server (Python 3.9, scratch members) before anything was stamped.
2. `stamp_member.py --kitchen-only --slug k<n> --name … --password-file /root/family/first-login.local.txt` for k1–k9: every rc 0. The names came from a gitignored list copied root-only to `/root/family/seeds/` and deleted after the run.
3. `readiness.py --slug k<n>`: 9 × READY, 14 PASS / 0 FAIL each; nothing left behind in the pool (62 recipes, 0 ratings, 0 drafts before and after).
4. Every file under `/srv/family/kitchen` owned by `fam_kitchen`; PIN file `-rw------- root`, 9 lines. Per-member output with names is in the root-only `/root/family/logs/kitchen_invites_20260926_181202.log`; nothing with a name or PIN was printed to this session.

**Recipe-by check, live** (`invite_check.py`, run once and deleted): for m1, m2, m3 and owner a draft was confirmed with that copy's own Kitchen token (what their GutLog does); card `added_by` = registry name, the person in `/api/people` and in `/api/browse` people with that name, `by=<slug>` lists only theirs — all True; the test recipes removed, pool counts equal. `/api/members` (owner token): 9 kitchen members, all enabled, last visit empty, 0 recipes; a member token is refused (403). Public sign-in pages for k1, k5, k9 answer with the right "<Name> — Family Kitchen" title.

**Code changed (tracked):**
- `family/readiness.py` — kitchen path saves `kmembers.last_seen` before its sign-in and restores it after; the last step now also checks it.
- `family/getting_started_sheet.py` — `--kitchen <list>`: the kitchen sheet (auto-shrinks type only if a page would overflow; all nine fit at 14 pt) and the WhatsApp messages file.
- `family/test_family_d.py` — D14 (readiness leaves "last visit" as it was; Family page shows it). `family/test_family_f.py` — F07 (full members' and the owner's recipes credited and listed in "By person" like a kitchen member's, in the kitchen member's own book; `--owner-name` reaches the card).
- `.gitignore` — `fitlog-ingest/Kitchen_invite_messages*.txt`.
- Docs: `family/DOSSIER_Family.md` (inviting kitchen members; readiness), `CHANGELOG.md`, `CLAUDE.md` §5g(i).

**Tests (PC, Python 3.14):** D 34/34, F 17/17. **Server:** D 34/34, F 17/17.

**Negative controls:** `new_assertions_family_invites_d.json` — `readinessvisit` (restore removed): D14 SEEN, current all-pass, RESULT PASS. `new_assertions_family_invites_f.json` — `apinames` (Kitchen forgets GutLog copies' names) and `ownernamestuck` (`--owner-name` never reaches the token entry): F07 SEEN twice, current all-pass, RESULT PASS.

**Backups:** none needed — no migration and no app file replaced; the only writes to the live Kitchen were the nine accounts (and the removed test items). Last Kitchen DB backup: `/root/backups/family/kitchen/kitchen-pre-upgrade-20260926_171622.db` plus the nightly.

**Noted, not changed:** Android has no Share-button route into the Kitchen (a web-app share target would need cross-site cookie handling); the sheet sends Android users to the Add tab. The Notion register was not updated — the Notion connector needs authorising in claude.ai connector settings. No command asked for permission.

## Publish

Result recorded below after `PUBLISH_HEALTH.bat`.

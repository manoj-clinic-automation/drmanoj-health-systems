# Family Edition — dossier

Single source of truth for the Family Edition (FAMILY_EDITION_V1), built 25-Sep-2026.
A copy of GutLog, RxGuard and FitLog for each relative, run from the owner's own code,
isolated per person, with the owner as caretaker; and one shared recipe pool, the
Family Kitchen. **No family member's health data is in this repository, ever** —
not in code, fixtures, docs or commit messages. Tests use "Member A", "Medicine A".

## Layout

| What | Where |
|---|---|
| Host | `https://family.dr-manoj.in` (CyberPanel website; SSL issued automatically by `ssl_when_ready.sh` once the DNS record exists) |
| Member *n* | `/m<n>/` GutLog · `/m<n>/rx/` RxGuard · `/m<n>/fit/` FitLog — ports `8200+10n+{1,2,3}` on loopback |
| Family Kitchen | `/kitchen/` — port 8199 (Kitchen 1.1.0); Share-shortcut guide at `/kitchen/help` |
| Kitchen member *n* | `/kitchen/k<n>/` — an account inside the Kitchen service (no user, no processes, no other database); installs as "Family Kitchen" |
| Code | `/opt/family/code/<stamp>/{gutlog,rxguard,fitlog,family}`, `current` → the live tree (root-owned, read-only to members) |
| Member data | `/srv/family/m<n>/` (owner `fam_m<n>`, mode 700): `gutlog/ rxguard/ fitlog/ care.db care.key status.token feed.token sso.key kitchen.token kitchen.capture` |
| Kitchen data | `/srv/family/kitchen/` (owner `fam_kitchen`, 700): `kitchen.db attach/ tokens.json session.key members/k<n>/auth.db` |
| Member env | `/etc/family/m<n>.env` (root 600): slug, folder, base, display name, profile, ports |
| Registry | `/root/family/members.local.json` (root 600) — slug, display name, profile, ports, enabled |
| Owner-side secrets | `/root/family/care/m<n>.key` + `.status`; `/root/family/kitchen/owner.token` + `.capture` |
| Tooling | `/root/family/` — this folder of the repository |
| Backups | `/root/backups/family/<slug>/` and `/kitchen/`, nightly 02:40, 30 days |

Why one host with paths, not a host per member: one DNS record and one certificate,
and the apps' ~300 absolute paths are handled by one wrapper (`family_prefix.py`)
instead of by editing a live 12,000-line file. What paths do **not** give is browser
isolation between members (same origin); that is accepted because each person's
copy is used on their own phone, and the server isolates them regardless (below).

## Isolation — the headline

* **Operating system.** Each member's three processes run as that member's own Linux
  user under systemd sandboxing (`ProtectHome`, `ProtectSystem=strict`,
  `ReadWritePaths` = own folder only, `NoNewPrivileges`, `PrivateTmp`). They cannot
  open `/root` (the owner's apps, databases and keys) or another member's folder.
* **Secrets.** Every member has their own session keys, sign-in-ring key, GutLog feed
  token, caretaker key, status token, FitLog ingest and Health Connect tokens.
  `family_env.py` **forces** every path and key file from the member folder — a stray
  owner variable in the environment changes nothing (tested: `ownerkey`, `ownerdb`,
  `sharedfeed` mutations).
* **Prefix.** A member process answers only its own prefix; anything else is 404.
* **Knowledge.** RxGuard's curated base and the owner-approved overlay are copied in,
  read-only; the overlay has its personal field (`strength_logged`, the strength the
  owner logged) stripped by `build_code.py`, which refuses the build if any
  personal-looking field remains. Members never approve drafts or fetch sources.
  The curated rules' "personal relevance" notes (the owner's) are dropped.
* **What crosses between copies:** the Family Kitchen (recipes and ratings only) and
  the owner's caretaker access. Nothing else. No family data enters the owner's
  databases, RxGuard, FitLog, Health Mirror or sign-in ring.

## Caretaker

The owner signs in to his own GutLog; `/family` shows each member (last entry, doses
taken/due/missed today, RED in their RxGuard, last BP, days since the last report),
read live from the member's `/api/care/status` (bearer; summary fields only). **Open
as caretaker** mints a one-use, 60-second HMAC ticket with that member's key. The
member app stamps a caretaker session; moving to the member's RxGuard or FitLog keeps
the role. Every caretaker write is logged in `care.db` and shown to the member on
`/care` as "by caretaker (Manoj)" with IST time. The caretaker cannot use the member's
credentials page or the switch. **The member can switch caretaker access off**; off
means every caretaker request is refused, tickets are refused, and the status endpoint
answers `{"access":"off"}` only. Missing/unreadable switch = off.

## Signing in (family copies only — `family_auth.py`, `family_webauthn.py`)

* **PIN.** Six digits, one scrypt hash in the member's `care.db`; the apps' own
  password hashes are random and unused (FitLog's is unsalted SHA-256, which a PIN
  must never sit behind). The owner's apps keep their password pages.
* **Lockout.** 5 wrong PINs in a row → 15 minutes; each further lock doubles (30, 60 …
  at most 24 h) until a PIN or Face ID sign-in succeeds. Shared by the member's three
  apps. While locked even the right PIN is refused. Every attempt (wrong, locked,
  refused, signed in, Face ID, PIN changed, reset) is logged in `care.db` with IST
  time, app and address, and listed on the member's `/care` page.
* **Face ID / Touch ID (passkeys).** Offered once after a PIN sign-in on a device that
  has it; the button is on GutLog's sign-in page. WebAuthn verified in pure Python
  (the server has no `cryptography`): ES256 and RS256, origin, RP ID, user presence
  AND verification, single-use 3-minute challenges, signature counter. Cross-checked
  against `cryptography` offline (`test_family_webauthn_crypto.py`). Works during a PIN
  lockout and ends it. Only the member can add or remove one.
* **Sessions.** 12 months, sliding, on the member's own device, until they sign out.
  "Sign out on all devices" (on `/care`) and a PIN reset end every session (an epoch in
  `care.db`). Caretaker sessions are browser-session only and end after 12 hours.
* **Reset / first PIN:** `stamp_member.py --slug mN --reset-pin --password-file F`.
  The member changes it on `/care` (not all one digit, not a straight run).
* **The sign-in page says whose it is** ("<Name>'s health diary" / "…'s medicines" /
  "…'s fitness", plus "Not <Name>? Ask <caretaker> for your own link"). 25-Sep-2026:
  the owner typed m1's PIN on m2's then-unlabelled page; three wrong PINs landed on m2,
  none on m1, and the file PINs were right all along. Numeric keypad, a Show/Hide eye,
  "Wrong PIN — N tries left", "Paused until HH:MM IST — call <caretaker>"
  (`FAMILY_CARETAKER` in the member env, from the registry's `caretaker`; after adding
  a setting like it run `stamp_member.py --refresh-env`). The Face ID button appears
  only on a phone where Face ID was set up (a flag the offer page leaves in that
  phone's storage; dropped if the server has none). The sign-in page carries the
  diary's manifest and icon, and the manifest is named "<Name>'s health diary", so
  Add to Home Screen from the sign-in page or the diary gives the member's name.
* **Before handing over a link:** `python3 /root/family/readiness.py --slug mN`.
  Checks the PIN in the root-only file offline first (a stale file costs no attempt;
  it will not try a member one miss from a lock), then signs in once over the real
  address and checks: sign-in page, Now page (or the first-run form), a medicine +
  salt, a meal and a symptom saved and removed, the Kitchen, Face ID offered, RxGuard
  and FitLog through the ring, sign out, and that nothing was left behind. Never
  prints the PIN. Its one sign-in shows in the member's "Recent sign-in attempts"
  from the server's own address.

## Profiles

`gut` (the owner's Now order), `joint` (joint pain log, pain-medicine totals, steps
against next-day pain, cholesterol-medicine checks first; knee- and ankle-sparing
FitLog programme), `general`, and `weight` (26-Sep-2026, GutLog v3.40.0: the This-week
card first, then medicines, meals with protein on the header, the Check-ins card, the
Physio tile, the joint cards folded, then BP and the rest — see below). All features
exist in every copy; the profile sets order and which cards show. The owner has no
profile set → his Now page is unchanged.

## The weight profile, seeded members, the physio (26-Sep-2026)

Built for a member who starts on a Sunday with a once-a-week injection, a diet with
its own meal times, a weekly weigh-in and a set of check-ins. Everything of hers is
data: a gitignored seed file on the PC, copied to `/root/family/seeds/` (root, 600),
applied **as the member** into her own database by `stamp_member.py --seed`, and the
copy deleted. Nothing from it is in this repository or in any report; the suite's seed
is "Member W", "Medicine A".

* **GutLog v3.40.0** (`GUTLOG_V3400_WEIGHT`, every copy): a **WEEKLY** schedule
  line (`med_schedule.weekday`, `at_time`) shown on the Now page only on its weekday,
  with a reminder from 30 min before, Taken/Skipped like any dose, variants, Retime and
  Day by day as before, the feed's shape unchanged; a dose not logged by the next day
  is asked about once ("yesterday's weekly dose — taken late / skipped?"); stock counts
  a weekly medicine per dose, never as a daily pillbox. **Meal windows**
  (`settings.meal_windows`) replace the fixed clock in the slot guess. **Check-ins**
  (table `checkins`; rules in `settings.checkins`: `monthly:N`, `weekly:DDD[ HH:MM]`,
  `day_after_weekly_dose`): PHQ-9 (with the difficulty question), PHQ-2, Epworth, a
  side-effect check the day after a weekly dose, a weekly weigh-in (writes the vitals
  row), monthly measurements — shown only when due, gone when answered, never nagging;
  totals and bands computed by name (PHQ-9 minimal/mild/moderate/moderately severe/
  severe; PHQ-2 positive screen at 3; Epworth normal/mild/moderate/severe). **PHQ-9
  item 9 above 0** sets a same-day flag: the member reads "Please tell <caretaker>
  today", `/api/care/status` carries `flag`, and the owner's Family page shows
  "Check-in flag today — please call" on that row. Nothing else is automated, and the
  flag never carries an answer. **Weight plan** (`settings.weight_plan`: start weight,
  start day, milestone percentages, targets): milestone lines on the chart; a milestone
  is crossed after two consecutive weigh-ins at or below it, and the next becomes
  current. `/checkins` page (chart 7–90 days with waist on a second scale, "Do one now",
  trends), `/export/checkins.csv`. **This-week card** (`/api/week`): weight and waist,
  injection countdown, milestone line ("−5 % = X kg, N kg to go"), steps today from
  FitLog's feed (blank when none — never 0), protein against the target.
* **The physio** (`family_physio.py`, slug `p1`…): a role inside ONE member's GutLog
  process at `/m<n>/physio/`, with the family sign-in rules exactly (PIN, lockout,
  IST log, Face ID, 12 months, sign-out-all) on their own `auth.db` under
  `<member>/physio/p<n>/`, and their **own cookie** `fam_m<n>_physio` scoped to
  `/m<n>/physio/`, read by the physio routes and by nothing else. They see and edit the
  programme (exercises, sets, reps, hold, days), tick sessions, add knee/joint pain and
  walking-tolerance entries (the member's `joint_log`, marked as theirs) and the monthly
  re-test (30-s sit-to-stand, 6-min walk, knee ROM, single-leg stance). The member sees
  the programme as a **Physio tile** on the Now page (tick today's session, see the
  next); the caretaker sees it too. `test_family_e.py` sends the physio cookie to Now,
  check-ins, plans, the joint log, the tile, the caretaker page, medicines, weight,
  meals, the diary page, RxGuard, FitLog, a second member and the owner's app and
  expects refusal everywhere; its negative control `physioreach` stamps a member session
  from the cookie and is caught. One physio can be attached to more members (`--for`),
  keeping the same PIN (`auth.db` copied); `--reset-pin` resets it everywhere.
* **Tools:** `stamp_member.py --slug m3 --name … --profile weight --seed … --password-file …`
  (the seed's physio is stamped in the same run; the PIN file gets `m3` and `p1` lines);
  `--physio --slug p1 --name … --for m3`, `--slug p1 --for m2 | --reset-pin | --disable |
  --enable`; `--list` shows physios. `readiness.py --slug m3` on a weight member also
  ticks a weekly dose scheduled for today, answers and removes a PHQ-2, and checks the
  plan PDF; `readiness.py --slug p1` checks the physio end to end and that their cookie
  opens nothing else. `family/getting_started_sheet.py --seed …` (PC only, reportlab)
  writes the member's and the physio's one-page sheets into `fitlog-ingest/` (PDFs are
  gitignored).
* **Steps without a watch:** FitLog's iOS ingest accepts iPhone Health data, so the
  phone in her pocket is enough; the sheet says so, and the card shows steps only when
  FitLog has them.

## Family Kitchen

Pool: recipes (name, group, servings, ingredients with amounts, method, notes, source,
attachment, added by — a display name, onion-free variants) and ratings (stars, made
it, would make again, a short note). **No health column** (schema test). Everything
personal — nutrition per serving, adjustments with "modified" badges and reasons, the
portion — is computed inside each person's own GutLog from their own record
(`kitchen_measures.json`, `kitchen_rules.json`). The shared recipe is never changed.
Capture: iPhone Share shortcut → `/kitchen/capture/<slug>` with a capture-only token;
links (schema.org data read directly; YouTube description and captions;
Instagram/Facebook ask for a screenshot), photos and PDFs (the owner's Sarvam reader,
English then Hindi), text (one model call to lay it out — capture, not analysis).
Everything lands as a draft in the Recipe Inbox; nothing reaches the pool, nutrition,
adjustments or RxGuard until a person confirms it, after a duplicate check.
Seeded from the owner's 52 cards (`seed_kitchen.py`): his stage and per-serving
estimates are not carried; note lines about him (trial, dose …) are dropped.
The model key is read in place from the file the records worker already reads
(`/root/wa/.env`); change `KITCHEN_KEYS_ENV` to use another, or remove the key to turn
the model step off (drafts then go to the manual screen).

### Kitchen members, self-publishing, attribution (Kitchen 1.1.0, 25-Sep-2026)

* **A kitchen member** (`k1`, `k2` …) needs only the recipe book: an account inside the
  Kitchen service — a `kmembers` row (slug, display name, enabled, plain food
  preferences, last visit) and `members/k<n>/auth.db` (PIN hash, IST sign-in log,
  passkeys). No Linux user, no processes, no GutLog/RxGuard/FitLog, no registry entry,
  no vhost change (the `/kitchen/` context already proxies `/kitchen/k1/`). Nothing
  about their health exists anywhere. Address `https://family.dr-manoj.in/kitchen/k1/`;
  the sign-in page says "<Name> — Family Kitchen"; Add to Home Screen installs "Family
  Kitchen" with the Kitchen icon (drawn by `kitchen_members.py` — no binary in the repo).
* **Sign-in = the family rules exactly**, from `family_auth.py`: 6-digit PIN (not all one
  digit, not a straight run), keypad + Show, "Wrong PIN — N tries left", 5 wrong → 15 min
  doubling to 24 h (per member), every attempt logged in IST and shown on the Me tab,
  Face ID / Touch ID offered after the first PIN sign-in (button only where set up),
  12-month session, "sign out on all devices", PIN change. The session cookie is named
  `kitchen`, scoped to `/kitchen/k<n>/`, signed with the Kitchen's own `session.key`.
  **It satisfies nothing else**: the bearer routes read only the Authorization header,
  and each member app has its own secret — `test_family_d.py` D06 sends the cookie to
  `/m1/…`, the owner's app, `/kitchen/api/…` and k2's book and expects refusal.
* **Nobody approves.** Every capture (Share shortcut, paste, link, photo, PDF) is a draft
  for its sender; they review it (uncertain items highlighted, the original beside) and
  tap **Publish**; it is in the pool at once. A duplicate is named ("…" by <Name>, why)
  and offers "Publish as <Name>'s version" (kept beside the original, linked as versions)
  or Cancel. **Only the contributor** edits or unpublishes (others get 403; they can
  rate, mark made / again, note). **The owner can hide** (never delete): hidden stays
  visible to its contributor with the note; a "Hidden" chip shows the owner what is
  hidden. Recipe status: `published` / `unpublished` / `hidden`.
* **Attribution everywhere.** "Recipe by <Name>" under the title on cards, lists, search,
  By person; date added; "Shared by <Name> · source: <site>" with the link for a web
  capture; "Shared by <Name> · from a photo" with the photo viewable; "N versions — by A,
  B, C"; ratings and notes with the rater's name; a logged serving is "<dish> · recipe by
  <Name>". Names are resolved when read (`tokens.json` api names for GutLog copies,
  `kmembers` for kitchen members), so `--rename` shows on every card at once. The owner's
  own cards say "Recipe by <owner's name>" once `--kitchen-sync --owner-name "…"` has been
  run (kept in the registry, never in code).
* **Food preferences** (kitchen members, Me tab): Vegetarian / Eggetarian / No
  onion-garlic / Jain — plain words in `kmembers.food_prefs` that only filter the list
  (word rules in `kitchen.PREF_WORDS`); the onion recipe drops out and its onion-free
  version stays. Full members' health-based adjustments stay inside their own copies.
* **Nutrition per serving for a kitchen member** is summed by the Kitchen itself
  (`kitchen_nutrition.py`: the same measures file and bundled USDA table, read from the
  code tree's `gutlog/`), no adjustments, unmatched items listed never guessed; D08
  checks it equals a full member's unadjusted card.
* **Tools:** `stamp_member.py --kitchen-only --slug k1 --name "…" --password-file …`;
  `--slug k1 --rename "…" | --reset-pin | --disable | --enable` (disable stops sign-in,
  capture and live sessions, keeps their recipes; no delete); `--list` shows kitchen
  members with their last visit; `readiness.py --slug k1` (sign-in page, PIN offline,
  one sign-in, Face ID, the book, a draft captured → published → rated → unpublished, the
  test items removed, sign out, counts as before — and the member's "last visit" put back
  as it was, so the check's own sign-in never shows on the Family page as a visit; D14,
  control `readinessvisit`). The owner's `/family` page lists
  kitchen members (name, last visit, recipes added) from the Kitchen's `/api/members`
  (owner token only) — no caretaker access, there is nothing of theirs to care for.
* **Upgrades:** `upgrade_all.sh` backs up the Kitchen DB before switching (the 1.1.0
  columns/table migrate on first use), restarts the Kitchen and checks `/kitchen/healthz`.

## Finding recipes (Kitchen 1.2.0, 26-Sep-2026)

Over the category backbone (`GROUPS`), `kitchen.browse()` answers one call for every
way of finding a recipe: the list under the current view and filters, and the counts
of the other axis under the same filters — so a person's category chips show only the
categories they have, and a category's people chips only the people who have one
there. **Person → Category → Recipe** ("Everyone" first, each name with its count) and
**Category → Person → Recipe**; **one search box** that matches the dish name, any
ingredient, the category and the contributor's name, with Hindi / English aliases from
`family/food_aliases.json` (a multi-word alias such as "lady finger" is matched as a
phrase; every word must match somewhere); **filters** that combine with either view:
food preference (Vegetarian / Eggetarian / No onion-garlic / Jain — a kitchen member's
saved ones apply until the page sends its own list), meal (from `MEAL_OF_GROUP`),
high-protein (≥ 10 g a serving from the Kitchen's own sum, cached), quick (≤ 20 min —
the stated `minutes`, else the largest "N min / N hours" in the method or notes),
top-rated (4+), new this week, made it by me. Results grouped by category, "Recipe by
<Name>" on each; the last person / category opened is remembered per phone
(`localStorage`); an empty result says what to loosen. The same screen in GutLog's
Family Kitchen page (v3.41.0, `/api/kitchen/browse`) and in the kitchen-only book
(`/kitchen/k<n>/j/browse`). Recipes gained `minutes` (guarded column). `build_code.py`
now names `food_aliases.json` and `build_lock.sh` (`FAMILY_DATA`) — a data file the
family layer needs at run time must be added there.

**The Kitchen's key file.** `kitchen_reader.py` reads `/root/family/kitchen_keys.env`
(root:fam_kitchen, 640; `ANTHROPIC_API_KEY` and `SARVAM_API_KEY` only — `KEY_NAMES`),
never the clinic's `/root/wa/.env`; `KITCHEN_KEYS_ENV` points elsewhere for a test. A
missing file is said once per run with the `install` line that creates it, and every
draft goes to the manual screen. To create it, the owner runs one line that copies only
the two variables out of the clinic's file (in the report).

**The build lock.** `family/build_lock.sh` (`take_build_lock "<brief>"`) is sourced by
`upgrade_all.sh`, `install_family.sh` and `install_kitchen.sh`: `mkdir
/root/deploy/.claude_code_build.lock`; if held, wait and re-check every 2 minutes;
never remove another build's lock unless it is older than 3 hours AND its `owner` file
reports `finished`, in which case it is taken over with a log line; the `owner` file
names this repository and the brief; a trap releases it on exit, also on failure.
`test_family_lock.py` (server; needs bash) proves the wait, the take-over and the trap
with a scratch folder and short limits.

**The fuller food table.** `gutlog/food_table_usda.json` rebuilt 26-Sep-2026 with fat,
carbs and calcium appended (columns 5–7); every id and earlier value unchanged;
`kitchen_nutrition.py` carries the three, so a kitchen member's card equals a full
member's unadjusted card as before (`test_family_d.py` D08).

## Commands

```
python3 /root/family/stamp_member.py --slug m3 --name "…" --profile gut|joint|general|weight
python3 /root/family/stamp_member.py --slug m3 --name "…" --profile weight --seed /root/family/seeds/m3_seed.local.json --password-file /root/family/first-login.local.txt
python3 /root/family/stamp_member.py --physio --slug p1 --name "…" --for m3 --password-file /root/family/first-login.local.txt
python3 /root/family/stamp_member.py --slug p1 --for m2 | --reset-pin | --disable | --enable
python3 /root/family/stamp_member.py --slug m3 --disable | --enable | --rotate-care | --reset-password
python3 /root/family/stamp_member.py --slug m3 --rename "…"
python3 /root/family/stamp_member.py --list
python3 /root/family/stamp_member.py --refresh-env   # rewrite every member env from the registry, restart
python3 /root/family/stamp_member.py --kitchen-only --slug k1 --name "…" --password-file /root/family/first-login.local.txt
python3 /root/family/stamp_member.py --slug k1 --rename "…" | --reset-pin | --disable | --enable
python3 /root/family/stamp_member.py --kitchen-sync --owner-name "…"   # once: the name on the owner's cards
python3 /root/family/readiness.py --slug m3           # before giving anyone their link (k1 for a kitchen member, p1 for a physio)
/root/family/upgrade_all.sh           # after any owner release: build, scratch self-test, migrate, switch, verify
bash /root/family/install_family.sh   # one-time (done 25-Sep-2026)
bash /root/family/install_kitchen.sh  # one-time, Phase C
```

## Tests

`test_family_a.py` (isolation, caretaker, stamping, backup), `test_family_b.py` (joint
focus), `test_family_c.py` (Kitchen), `test_family_d.py` (kitchen members,
self-publishing, attribution, preferences, readiness), `test_family_e.py` (the weight
profile: a seeded member, the WEEKLY slot, meal windows, check-ins and the item-9 flag,
milestones, the This-week card, the physio and its scope, readiness; 36 checks) —
server-runnable against scratch members; the `test_family_ui*.py` browser suites run
offline (`ui_d`: a kitchen member at 300 px; `ui_e`: the weight member's Now page,
Check-ins page and the physio page at 300 px). Manifests `new_assertions_family_*.json`
use `NEGATIVE_CONTROL.py` "dir" mutations: each assertion is broken in a COPY of the
folder that carries it. The D manifest's named controls: `draftinpool`, `anyedit`,
`noattrib` + `noattriblog`, `kreach` + `kapi`, `healthcol` + `healthcolauth`. The E
manifest's: `anyday`, `nolate`, `pillbox`, `nowindows`, `nostamp` + `nototal`, `noflag`
+ `noflagpage`, `noafter`, `noadvance` + `onecross`, `zerosteps`, `physioreach`,
`seedleak`, `noweekly`, `widecookie`, `nowalk`, `readinesspain`. `test_family_f.py`
(finding recipes, the key file, the table; 16 checks; `ui_f` at 300 px in GutLog and the
book) with `personleak`, `allcats`, `allpeople`, `noname`, `noalias`, `noingredient`,
`noquick`, `noprotein`, `nohint`, `noprefsaved`, `notop`, `gutnocols`, `kitchennocols`,
`lookupnofat`, `oldkeyfile`, `quietmissing`, `aliaspersonal`. `test_family_lock.py` (server
only: bash). The 300 px suite also proves that two taps in quick succession never stack
two answers in the view (a render token; the first build did stack them).

### Inviting kitchen members (26-Sep-2026)

Nine recipes-only kitchen members were stamped, `k1`–`k9`, each checked READY by
`readiness.py` (14/14). Their names exist only in the Kitchen database, the root-only
PIN file `/root/family/first-login.local.txt`, and the gitignored sheets on the PC.
The invite kit comes from `family/getting_started_sheet.py --kitchen
fitlog-ingest/kitchen_invites.local.json --out fitlog-ingest` (PC, reportlab): a one-page
`Kitchen_Getting_Started_<Name>.pdf` each (large type; link; PIN sent separately; iPhone
Safari → Share → Add to Home Screen and Android Chrome → menu → Add to Home screen; Face
ID / fingerprint; Person → Category, search, filters; Add tab and the iPhone Share
button → Inbox → Publish; stars, "I made it"; "Recipe by <Name>"), shrinking the type
only if the page would overflow, and one `Kitchen_invite_messages.txt` with a two-line
WhatsApp message each (greeting + link; the PIN goes separately). The list, the sheets
and the messages are all gitignored. Android has no Share-button route into the Kitchen
yet (the Add tab covers it); the iPhone route is the Share shortcut. Full members' and
the owner's recipes are credited and listed under "By person" exactly as a kitchen
member's (`test_family_f.py` F07, controls `apinames`, `ownernamestuck`; checked live for
m1, m2, m3 and the owner with temporary recipes, removed).

## Open items (25-Sep-2026)

* Members m1 (`gut`) and m2 (`joint`) stamped 25-Sep-2026 13:37 IST. The first attempt
  failed: with no `--code`, the tool took the folder above itself (`/root`) as the code
  tree, and the member's own user could not open `/root/family/init_member.py`. Fixed:
  a real stamp always uses `/opt/family/code/current`, refuses a tree the member cannot
  read before writing anything, and rolls back fully if the setup fails part-way.
  `test_family_stamp_real.py` stamps through the real per-user path (server, root),
  negative control 4/4. First-login passwords: `--password-file`, root-only.
* Fat, carbohydrate and calcium per serving need a fuller food table (SR Legacy has
  them; the bundled file carries protein, kcal, fibre). Rebuild with the owner's
  go-ahead to download the USDA zip again.
* Email-forward capture: needs an MX record for the family host and a mailbox; left as
  a later option.

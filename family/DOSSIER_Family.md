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
| Family Kitchen | `/kitchen/` — port 8199; Share-shortcut guide at `/kitchen/help` |
| Code | `/opt/family/code/<stamp>/{gutlog,rxguard,fitlog,family}`, `current` → the live tree (root-owned, read-only to members) |
| Member data | `/srv/family/m<n>/` (owner `fam_m<n>`, mode 700): `gutlog/ rxguard/ fitlog/ care.db care.key status.token feed.token sso.key kitchen.token kitchen.capture` |
| Kitchen data | `/srv/family/kitchen/` (owner `fam_kitchen`, 700): `kitchen.db attach/ tokens.json` |
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
FitLog programme), `general`. All features exist in every copy; the profile sets
order and which joint cards show. The owner has no profile set → his Now page is
unchanged.

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

## Commands

```
python3 /root/family/stamp_member.py --slug m3 --name "…" --profile gut|joint|general
python3 /root/family/stamp_member.py --slug m3 --disable | --enable | --rotate-care | --reset-password
python3 /root/family/stamp_member.py --slug m3 --rename "…"
python3 /root/family/stamp_member.py --list
python3 /root/family/stamp_member.py --refresh-env   # rewrite every member env from the registry, restart
python3 /root/family/readiness.py --slug m3           # before giving anyone their link
/root/family/upgrade_all.sh           # after any owner release: build, scratch self-test, migrate, switch, verify
bash /root/family/install_family.sh   # one-time (done 25-Sep-2026)
bash /root/family/install_kitchen.sh  # one-time, Phase C
```

## Tests

`test_family_a.py` (isolation, caretaker, stamping, backup), `test_family_b.py` (joint
focus), `test_family_c.py` (Kitchen) — server-runnable against scratch members; the
`test_family_ui*.py` browser suites run offline. Manifests `new_assertions_family_*.json`
use `NEGATIVE_CONTROL.py` "dir" mutations: each assertion is broken in a COPY of the
folder that carries it.

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

# RxGuard v1.3.0

`rx.dr-manoj.in` · service `rxguard` · port 8031 · `/root/rxguard`

Personal medication interaction and safety review. Single user, self-hosted.
Flask + SQLite + gunicorn + systemd — the same pattern as GutLog.

## What it is for

Multiple specialists, several of them consulted by phone, plus self-treated
MSK and radicular episodes. No single person is holding the whole list.
This is the place the complete picture exists, and the thing that carries it
into each consultation.

## What it deliberately is not

It does not diagnose, prescribe, or approve treatment. It does not replace a
prescriber. It is not a general-purpose interaction checker and must not be
used for clinic patients — there is no patient identity field in the schema,
and that is intentional.

## Design decisions worth knowing before you trust it

**There is no GREEN.** Flags are RED, AMBER and UNKNOWN. Absence of a flag
means nothing was found in this knowledge base, not that a combination is
safe. A green badge becomes a green light regardless of the disclaimer under
it, so there isn't one.

**Coverage is the ceiling.** 60 molecules, curated narrowly and deeply for
your actual and plausible drug set rather than broadly and shallowly for
everyone. Anything not in `knowledge/drugs.json` gets no check at all and is
reported as UNKNOWN rather than passed over in silence. Check
`/knowledge` for what is covered.

**Findings report the change, not the baseline.** Cumulative burden already
present in the list is shown on the dashboard. The analysis screen only
raises a burden finding when the proposed drug actually contributes to it,
and shows the delta. Otherwise every analysis restates the same background
and the screen trains you to dismiss it.

**Curated rules beat derived ones.** Where a named pairwise rule exists it
suppresses the generic CYP-property derivation for that pair, so the same
interaction is not reported twice in different words.

## As taken (GutLog) — v1.1.0

RxGuard checks the list typed into it; GutLog records what is actually taken.
The **As taken (GutLog)** page (and a one-line summary on the Dashboard) reads
GutLog's read-only feed over loopback and:

- lists every molecule from GutLog (regimen + doses in the last 14 / 30 / 90
  days) and from this list, with how often it was taken, whether it is on the
  list, and whether the knowledge base covers it;
- runs the engine across the **whole as-taken stack** — named pairwise rules,
  CYP derivation (suppressed where a named rule covers the pair), class
  duplication, condition rules, burden and QT stacking — judging each pair
  once, and marks findings that involve a medicine missing from the list;
- reports taken-but-not-listed (AMBER, reconciliation), molecules outside the
  knowledge base and GutLog medicines with no molecule (UNKNOWN, named), and
  listed-but-not-logged.

Read-only; still no GREEN. If GutLog is unreachable the page says so and every
other screen is unaffected. Config: `GUTLOG_FEED_URL` (default
`http://127.0.0.1:8020`), `GUTLOG_FEED_TOKEN_FILE` (default
`/root/gutlog/feed.token`, created by GutLog). The feed follows the live
database: a database outside the app folder (the test suites) never reads it;
`RXGUARD_GUTLOG_FEED=1/0` overrides.

Tests: `test_astaken.py` **15/15** — a real GutLog on a scratch database and
token, RxGuard on a scratch database; covers reconciliation both ways, the
named rule firing once, CYP suppression, UNKNOWN coverage, order, read-only,
days parameter, dashboard, login, wrong/missing token, scratch-DB isolation,
GutLog down. `smoke_test.py` 42/42 and `validate.py` 50/50 unchanged.

## Sources review — v1.2.0

RxGuard's curated knowledge base covers a fixed list. Medicines change, so
v1.2.0 fills the gaps from **free, verifiable** sources and puts every result
in front of the owner before it counts.

| Source | Gives | Notes |
|---|---|---|
| NLM RxNorm + RxClass | Identity (brand → ingredient), ATC class, spelling suggestions | US National Library of Medicine, public API |
| openFDA drug label | QT, sedation, serotonergic, anticholinergic, bleeding, renal/hepatic, withdrawal, interaction sentences | Every property carries its quoted sentence, label set id and date |
| FDA CYP / transporter table | Strong/moderate/weak inhibitors and inducers, sensitive substrates | Beats label wording for the same field; cached monthly |
| DDInter 2.0 | Pair severity (Major → RED, Moderate → AMBER) | CC BY-NC-SA 4.0 — personal, non-commercial, attributed |
| PvPI (IPC, India) | Drug safety alert links | Links only; first run is a baseline |

`kb_sync.py` runs every 30 minutes by cron (and from *Fetch now*). It reads
what GutLog shows as current (regimen + 30 days of doses, with strength),
builds a **draft** for each molecule RxGuard does not know (a brand already
known under its ingredient becomes an alias), lists DDInter pairs between
known medicines that no curated rule covers, collects PvPI alerts, and once a
day marks approved entries whose FDA label changed as *re-review*.
`python3 kb_sync.py --report` prints what the sources produced (and how far
the last run got); `python3 kb_sync.py --diag` checks that each source
answers from the server. v1.2.1: every download has a whole-transfer
deadline, and the 14 DDInter files are fetched within a time budget per run
and kept one by one, so a slow server is finished over several runs; drafts
built before DDInter was complete are rebuilt once it is. v1.2.2: a
medicine's own ATC class is preferred over a fixed-combination class, label
bullets are split into single statements, pending drafts from an older
builder are rebuilt, and strengths edited in GutLog reach pending drafts.

**Your review** (`/kb`, v1.3.0) leads with what bears on the medicines taken
now: one card per interacting pair (RED first) in plain words derived from
both medicines' properties ("both slow the heart rate", "X blocks CYP3A4,
which clears Y"), the source wording one tap away. Each new medicine shows
chips for what bears on current medicines (and with which) and a line for
what is kept for future checks; kidney/liver notes with no matching
condition, stopping notes and gaps sit in a small footnote. *Accept all
recommended* approves everything in one tap; each medicine can still be
narrowed or rejected. Kidney/liver sentences that ask for no action are
stored as reference and no longer drive a dose-review finding.

Accepting writes the chosen properties and pairs → they are
written to `knowledge/drugs.local.json` / `rules.local.json` (never
committed) and the engine uses them at once. The curated `drugs.json` /
`rules.json` always win. Gaps are stated ("label silent — not proof of
none"); still no GREEN. `/api/feed/status` (GutLog's feed token) gives GutLog
its banner counts. Only molecule names ever leave the server. Paid and
licensed sources (e.g. CDSCO-backed Indian compendia, commercial checkers)
are deliberately not used.

Tests: `test_kb.py` **32/32** — a fake server replaying the real formats:
FDA table parser and outage fallback, DDInter index, RxNorm identity, label
choice, draft properties and pairs, FDA table over label, sync + idempotency,
review page, partial approval reaching the engine, reject, alias, pair
decisions, curated wins, status feed, label re-verify, all sources down,
Fetch now, login, names only, smoke untouched, terminal report (a negated
"not a substrate of CYP" is not read as a role), a trickling server cut
off at the deadline, DDInter resume + draft rebuild, connectivity check.

## Layout

```
app.py                    application, engine, templates — single file
knowledge/drugs.json      60 molecules: CYP, burdens, QT, renal, withdrawal
knowledge/rules.json      24 named pairwise rules, condition rules,
                          burden thresholds, withdrawal rules, symptom map
smoke_test.py             42 checks incl. the mandatory index case
validate.py               validation harness
validation_cases.json     50 cases with pre-declared acceptance thresholds
rxguard.service           systemd unit (port 8031)
backup.sh                 nightly backup, GutLog cron pattern
kb_sources.py             v1.2.0: fetchers and draft builder (free sources)
kb_sync.py                v1.2.0: cron / Fetch now sync; --report
test_kb.py                v1.3.0: 32 checks against a fake source server
knowledge/cache/          downloaded source data (gitignored, rebuilt)
```

The knowledge base is versioned separately from the code. Editing a rule
does not mean touching `app.py`, and every finding carries its source and
review date. Rules older than 365 days render as STALE.

## Deploy

```bash
# on the VPS, as root
mkdir -p /root/rxguard && cd /root/rxguard
# upload app.py, knowledge/, *.py, *.json, rxguard.service, backup.sh via WinSCP

pip install flask gunicorn

python3 -c "import secrets;print('RXGUARD_SECRET='+secrets.token_hex(32))" > rxguard.env
chmod 600 rxguard.env

python3 smoke_test.py      # expect 42/42
python3 validate.py        # expect 50/50

cp rxguard.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rxguard
systemctl status rxguard
curl -s localhost:8031/healthz
```

Then add a proxy in CyberPanel for `meds.dr-manoj.in` to `127.0.0.1:8031`,
issue the certificate, and add the backup cron:

```
30 2 * * * /root/rxguard/backup.sh >> /root/rxguard/backup.log 2>&1
```

First visit to `/login` sets the password. There is no registration route
and no second user.

## Before relying on it

`validation_cases.json` currently carries provisional expected flags and an
unnamed reference standard. Name the reference standard — Stockley's, or
Lexicomp/Micromedex if you have institutional access — and check the 50
expected flags against it before treating a clean run as meaningful. The
thresholds (zero missed RED, at most two false RED) are declared in the file
and are meant to stay fixed. If a run fails them, the knowledge base changes,
not the thresholds.

## First data to enter

1. The three chronic drugs, with prescriber and specialty on each.
2. Conditions and the constraints card in `/profile`.
3. The index case in `/adverse`, with the temporal fields filled properly —
   dose at onset, latency from escalation, what happened on reduction,
   confounders. Entered loosely it becomes an anecdote that outranks the
   literature in this tool's own hierarchy. Entered properly it is evidence
   you can hand to the next prescriber.

## Routine use

- **Quick check** — episodic course, under thirty seconds, before self-treating.
- **New symptom** — enter the symptom before interpreting it. Ranks the last
  six weeks of medication changes against it. This is the screen that exists
  because attribution, not knowledge, was the failure in the index case.
- **One-page list** — print at the start of any consultation.
- **Review queue** — anything you override comes back at two and six weeks
  with your own stated reason shown back to you.

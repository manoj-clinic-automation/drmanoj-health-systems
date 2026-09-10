# FitLog Phase 3.5 — Wearable Ingest

Pulls Apple Watch data (via iPhone) and Samsung Health data (via Health Connect)
into FitLog. Deterministic, source-tagged, no LLM in the path.

---

## Design decisions taken

| Decision | Choice | Why |
|---|---|---|
| Token storage | `/root/fitlog/ingest.env`, mode 600 | Rotate by editing one file. No systemd unit edits, no token in `systemctl show`. |
| Day boundary | True calendar IST, unshifted | Ingested values match exactly what the Health app shows. Post-midnight OT is handled in the rule layer, not by silently bending the data. |
| Source identity | Explicit `?source=` per device | No inference, no guessing. |
| Conflict handling | Rule **S01 Source Precedence** | `applewatch` > `healthconnect` > `manual`. Never summed, never averaged. |
| HR / HRV / SpO2 | Stored, flagged `rule_bearing: false` | Consistent with talk-test-only aerobic governance during titration. |
| Raw payloads | Every body kept in `health_raw` | A vendor format change costs a re-parse, never lost history. |

Rule-bearing metrics: `steps`, `exercise_minutes`, `stand_hours`, `flights`.

---

## Server deploy

Upload the four files to `/root/fitlog/` (WinSCP for the module, scp is fine
for the patcher).

```bash
cd /root/fitlog

# 1. token
python3 -c "import secrets; print('FITLOG_INGEST_TOKEN=' + secrets.token_urlsafe(32))" > ingest.env
chmod 600 ingest.env
cat ingest.env          # copy this token, you need it on both phones

# 2. schema  (takes its own .bak first)
python3 migrate_health_ingest.py /root/fitlog/fitlog.db

# 3. register the blueprint
python3 patch_register_ingest.py --dry-run     # inspect first
python3 patch_register_ingest.py

# 4. smoke test — must be 23/23, uses a temp DB, never touches live
python3 test_health_ingest.py

# 5. restart
systemctl restart fitlog
systemctl status fitlog --no-pager
```

Confirm the DB filename in step 2 before running. If it is not `fitlog.db`,
pass the real path — the script refuses rather than creating an empty DB.

### Verify

```bash
TOKEN=$(grep FITLOG_INGEST_TOKEN /root/fitlog/ingest.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" https://fit.dr-manoj.in/api/ingest/status
curl -s https://fit.dr-manoj.in/api/ingest/status        # must return 401
```

### OpenLiteSpeed

`/api/ingest` must bypass the owner-key gate — it carries its own bearer token.
If the gate is enforced at the proxy rather than in Flask, add the exclusion
there; if it is a `before_request` hook in the app, exempt the
`health_ingest.` endpoint prefix.

### Rollback

```bash
cp /root/fitlog/app.py.bak_<stamp> /root/fitlog/app.py
cp /root/fitlog/fitlog.db.bak_<stamp> /root/fitlog/fitlog.db
systemctl restart fitlog
```

---

## iPhone 14 Pro Max — base station

Physical setup: plugged in permanently at home, on Wi-Fi, SIM active as the
outbound fallback.

- Settings → Display & Brightness → Auto-Lock → **Never**
- Brightness to minimum, black wallpaper (it will hold a static screen for months)
- Settings → General → Background App Refresh → **on** for Health Auto Export
- Keep the Watch paired to this phone. It flushes over Bluetooth or shared Wi-Fi
  whenever you are in range — realistically each evening.

Health Auto Export → new Automation:

- Type: **REST API**
- URL: `https://fit.dr-manoj.in/api/ingest?source=applewatch`
- Format: **JSON**, Export Version **2**
- Header: `Authorization: Bearer <token>`
- Schedule: daily **23:00**
- Window: **48 hours** (overlap is free against the upsert, and covers the
  night you get home at 1am)
- Metrics: steps, active energy, resting heart rate, walking HR average, HRV,
  sleep analysis, exercise time, stand hours, flights climbed, SpO2, weight
- Workouts: on

Anything outside the whitelist is still captured in `health_raw`, so
over-selecting costs nothing but bandwidth.

## Galaxy Fold — secondary

Samsung Health → Settings → Data management → Health Connect → enable sync.
In Health Connect, confirm Samsung Health has write permission for Steps,
Sleep, Heart rate and Exercise.

Then point any Health Connect exporter that supports HTTP POST at:

`https://fit.dr-manoj.in/api/ingest?source=healthconnect`

Same bearer header. Same JSON shape — the parser accepts both `steps` and
`step_count` naming.

This feed only ever fills dates where the Watch was silent. It cannot inflate
a day the Watch already covered.

---

## After deploy

- `docs/DOSSIER.md` — new section: ingest endpoints, S01, table schemas
- `CHANGELOG.md` — Phase 3.5 entry
- Notion Tech & Systems Register — FitLog row, note the new inbound surface
- `CLAUDE.md` — record that `/api/ingest` is bearer-gated, not owner-key gated
- Add `ingest.env` and `*.bak_*` to `.gitignore`

## Deliberately out of scope

Nothing here changes a single verdict. Ingested metrics are visible and
queryable but no F-rule consumes them yet. Wire them into the rule engine only
after a few weeks of real data shows what an OT day actually looks like on
your wrist.

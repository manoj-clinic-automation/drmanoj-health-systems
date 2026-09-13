# FitLog Phase 3.5.1 — record-level Auto Export ingest

Closes the partial-export overwrite, restores `distance_km`, drops
`total_energy_kcal`. Nothing here touches the rule engine; no F-rule
reads an ingested metric.

> **Deployed 2026-09-12 05:46 IST.** All nine suites green on the server
> (Python 3.9.25) before the restart; recompute committed at 05:50.
> Rollback points, if ever needed:
> `health_ingest.py.bak_20260912_054555_888002` and
> `fitlog.db.bak_20260912_055021_222661`.
> As-deployed figures and the one deviation from this plan are recorded
> at the bottom.

---

## Rule S02 Day-Grain Precedence

Health Auto Export sends the same metric at two grains: per-hour (or
per-minute) interval samples, and a single whole-day rollup sample. Both
are legitimate, both can arrive in either order, and either can be
truncated. Adding them double-counts the day; letting either replace the
other loses data in one of the two orders.

Every sample is now stored as a record under a dedup key and the daily
figure in `health_metrics` is **recomputed** from those records — the
construction the HC and iOS paths already use. Then, per date + metric +
source + feed:

| | |
|---|---|
| interval records | **summed**, after dedup by interval identity |
| day records (rollups) | the **most recent** replaces the previous |
| the day's figure (summed metrics) | the **greater** of those two |
| across feeds of one source | the **greater**, never the sum |
| levels (HR, HRV, SpO2, weight) | the day rollup if there is one, else the mean of the intervals |

Consequences, all intentional:

- A partial batch can only **add** to a day. Records accumulate; nothing
  is deleted by a later write.
- A partial interval batch cannot undercut a complete rollup.
- A stale rollup cannot cap a fuller interval set.
- The retired iOS feed's records and the Auto Export records for the
  same day are compared, never added, so a rule-bearing metric cannot be
  inflated by having been recorded twice.

### Known limits, stated rather than hidden

- **A downward correction lands only at its own grain.** A revised
  rollup replaces a rollup; revised interval records replace their
  intervals. A rollup cannot pull a day below the interval records
  already stored for it. Apple revises a day downwards far less often
  than an export is truncated, and silent under-reporting of a
  rule-bearing metric is the worse failure.
- **Grain comes from cadence, not magnitude.** Inside one payload, a
  date carrying exactly one timestamp for a metric, at `00:00:00`, is
  read as the daily rollup; two or more timestamps are intervals. A
  rollup spliced into a per-hour body for the same date is
  byte-identical to that body's hour-00 sample and is read as that hour.
  No value-magnitude heuristic is used — the same reasoning that keeps
  one out of `_hc_value`. Auto Export does not mix grains in one body;
  `test_apple_records.py` [10] pins the reading so a future change to it
  is deliberate.
- **Cross-feed `max` assumes each feed covers the whole day.** True for
  the feeds in play: Auto Export's 48-hour window covers a finished day
  completely. Two feeds each covering a different half of one day would
  yield the larger half, not the whole.

### Feeds

`health_hc_records` gains `grain` and `feed`. Rows written before this
patch default to `grain='interval'`, `feed=''` — which is what they are:
interval records from HC Webhook or from the retired iOS Health Webhook.
Auto Export rows carry `feed='hae'`. The HC Webhook path stays pinned to
`feed=''`, so its arithmetic is byte-for-byte what it was.

---

## What else changed

**`walking_running_distance` mapped.** Auto Export's name for distance
was never in `METRIC_MAP`, so `distance_km` held whatever the retired
iOS feed last wrote and nothing would ever correct it. Mapped now, with
km / mi / m normalisation (`distance_walking_running` accepted as an
alias). Not rule-bearing, not displayed — the watch page's Distance
column reads `health_workouts`, not this metric.

**`total_energy_kcal` dropped.** Not displayed, no F-rule reads it, and
it meant active+basal from one feed and something else from another.
Removed from `HC_ARRAY_MAP`, `IOS_ARRAY_MAP` and both summed sets, and
its rows are purged by the recompute script. `METRIC_MAP` also mapped
HC's `total_calories_burned` onto `active_energy_kcal` — a different
quantity, since it includes BMR — so that entry goes too. Every raw body
is still in `health_raw`, so re-deriving it later costs a re-parse and
nothing more.

---

## Deploy

Upload to `/root/fitlog/` by WinSCP:

```
patch_apple_records.py
recompute_apple_daily.py
test_apple_records.py
test_recompute_apple.py
```

```bash
cd /root/fitlog

# 1. inspect first - anchors must all match, nothing is written
python3 patch_apple_records.py --dry-run

# 2. patch (takes health_ingest.py.bak_<microsecond stamp>)
python3 patch_apple_records.py

# 3. every suite, in order. All must be green before the restart.
python3 test_health_ingest.py          # 23/23
python3 test_hc_ingest.py              # 36/36
python3 test_ios_ingest.py             # 18/18
python3 test_ios_source_isolation.py   # 19/19
python3 test_apple_aggregation.py      # 29/29
python3 test_apple_records.py          # 56/56
python3 test_recompute_apple.py        # 30/30

# 4. preview the rebuild. Works on a throwaway copy, writes nothing.
python3 recompute_apple_daily.py --dry-run

# 5. rebuild 09-09..09-11 for real (takes a DB .bak first)
python3 recompute_apple_daily.py

systemctl restart fitlog
systemctl status fitlog --no-pager
```

If any suite fails, restore and stop:

```bash
cp /root/fitlog/health_ingest.py.bak_<stamp> /root/fitlog/health_ingest.py
cp /root/fitlog/fitlog.db.bak_<stamp>        /root/fitlog/fitlog.db   # if step 5 ran
systemctl restart fitlog
```

`recompute_apple_daily.py` defaults to 2026-09-09..2026-09-11 and every
source. `--from` / `--to` narrow the range, `--source` narrows the feed,
`--keep-total-energy` leaves the dropped rows alone, `--help` lists them.
It only ever reads `health_raw`.

### Verify

```bash
TOKEN=$(grep FITLOG_INGEST_TOKEN /root/fitlog/ingest.env | cut -d= -f2)
for d in 2026-09-09 2026-09-10 2026-09-11; do
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://fit.dr-manoj.in/api/health/daily?date=$d"; echo
done
```

`steps` and `distance_km` should both be present for all three dates,
`total_energy_kcal` absent.

---

## As deployed — 2026-09-12

### Anchor drift found before applying

The repo copy of `health_ingest.py` was the pre-Phase-3.5 base, so the
anchors were first built against a reconstruction. The live file turned
out to carry two out-of-band additions — `mindful_minutes` /
`mindful_session` in `METRIC_MAP`, and a `FITLOG_V120_ACTIVITY` block
(`_wname`, `classify_workout`) — plus `patch_ios_ring_goals`. One of the
twelve anchors (`METRIC_MAP`) straddled the mindful lines and would have
refused to match. It was narrowed to key off the
`# Health Connect / generic aliases` comment instead. The other eleven
were unaffected. Suites were then re-run against the **real** server
file, not the reconstruction.

### Deviation from the plan: the retired ios bodies are not replayed

The first dry-run showed the recompute restoring `hr` and
`move_energy_kcal` from the retired iOS Health Webhook bodies still
sitting in `health_raw`. Those bodies are **mid-day snapshots**: body 11
landed at 16:56 on 09-11 carrying 2.5805 km and a 319 kcal move ring for
a day that finished at 3.2947 km and 441 kcal. The watch strip prefers
`move_energy_kcal` over `active_energy_kcal`, so replaying them would
have pushed the 09-11 Move ring backwards from 441 to 319 — the same
staleness this deploy exists to remove.

`recompute_apple_daily.py` now skips retired-feed bodies by default and
counts them; `--replay-retired-ios` opts back in. Covered by
`test_recompute_apple.py` [3] and [3b] (36/36).

### Distance, cross-checked before committing

Auto Export's per-minute bodies and its daily rollup bodies were parsed
by different code paths and agree to four decimals, units declared `km`
throughout:

| date | HAE per-minute sum | HAE daily rollup | iOS (retired) | committed |
|---|---|---|---|---|
| 2026-09-09 | 0.6557 | 0.6557 | 0.3028 | **0.6557** |
| 2026-09-10 | 1.5955 | 1.5955 | 1.5955 | 1.5955 *(unchanged)* |
| 2026-09-11 | 3.2947 | 3.2947 | 2.5805 *(16:56 snapshot)* | **3.2947** |

### What the recompute actually changed

Five figures out of 41 in range. Every other value — including both
`healthconnect` rows and every Auto Export figure — reproduced
bit-for-bit, which is the evidence that the new path did not perturb
anything that was already right.

| date | metric | before | after |
|---|---|---|---|
| 2026-09-09 | `distance_km` | 0.3028 | **0.6557** |
| 2026-09-11 | `distance_km` | 2.5805 | **3.2947** |
| 2026-09-09 | `total_energy_kcal` | 573.0496 | *removed* |
| 2026-09-10 | `total_energy_kcal` | 1923.4415 | *removed* |
| 2026-09-11 | `total_energy_kcal` | 1578.0768 | *removed* |

Steps held at 951 / 2305 / **4914**, stand hours at 7 / 11 / 19,
exercise minutes at 0 / 11 / 17. `rule_bearing` is still true for
exactly `steps`, `exercise_minutes`, `stand_hours`, `flights`;
`distance_km` is context-only.

### Helper scripts left on the server

`run_gate.sh` (runs all nine suites, one line each) and `verify_live.sh`
(GET-only post-deploy check) are worth keeping. `inspect_state.py` and
`inspect_distance.py` were one-off read-only diagnostics — delete at
will.

## Repo hygiene

`fitlog-ingest/health_ingest.py` has been pulled back from
`/root/fitlog/health_ingest.py` and verified by sha256
(`a7db0ae5…0e324fd6`). Every other patcher, suite and migration in this
folder was checked against the server and is in sync.

Not in this folder and not pulled: `patch_fitlog_v120.py`,
`patch_fitlog_redact.py`, `test_activity_feed.py`, `test_gutlog_feed.py`
live on the server and belong to the `fitlog/` app rather than to
ingest.

Still to sync: `fitlog/DOSSIER.md` (S02, the `grain`/`feed` columns, the
dropped metric), `CHANGELOG.md`, and the Notion Tech & Systems Register
row for FitLog.

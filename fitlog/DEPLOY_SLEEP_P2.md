# Deploy — FITLOG_SLEEP_P2 + FITLOG_SLEEP_P2_PAGE + FITLOG_SLEEP_MEDIAN_N7

Two files change: `health_ingest.py` and `app.py`. No schema migration step, no
new secret, no service-unit change.

`patch_fitlog_sleep_median_n7.py` applies **after** `patch_fitlog_sleep_p2_page.py`
and edits the same function, so run them in that order. Note that once the
median patch is on, `new_assertions_sleep_p2_page.json` can no longer be
re-run: its patcher reverses exactly one step and the step above it has moved
the code. That is normal — reverse in LIFO order, and run each manifest at the
point its patch is applied.

Requires `FITLOG_SLEEP_P1` in `health_ingest.py` (deployed 2026-09-15 15:43)
and `FITLOG_V160_DOWNDAYS` in `app.py`.

**This deploy adds no score of any kind, and the suite refuses to pass if a
later one ever does.**

---

## 0. The phone — ONE visit, and it is TWO switches, not one

Wrist temperature is the reason this phase exists, and **it is still not being
exported**. A census of all 212 stored bodies found no temperature point of any
name.

**This is the same two-switch trap that cost most of 2026-09-14.** Ticking a
metric inside Health Auto Export is not enough: iOS grants read permission
**per data type**, separately, and an app that has not been granted a type
exports nothing for it and says nothing about why. Both switches, one visit:

1. **iOS Health app** → profile → **Apps and Services** → **Health Auto
   Export** → turn on **Wrist Temperature** (and confirm **Sleep**,
   **Respiratory Rate** and **Blood Oxygen** are on while you are in there).
   This is the permission. Without it the next step is silent.
2. **Health Auto Export** → the automation that posts to `fit.dr-manoj.in` →
   add **Sleeping Wrist Temperature** to its exported metrics, and **widen the
   export window** to cover at least the previous two days rather than today.
   This is the export.

Doing only 2 looks exactly like doing nothing.

### While the window is open — recover the night of 14→15 Sep

**Two minutes, and worth it.** That night is the top of the Sleep card and it
reads **1 h 40 m** when Apple Health holds **5 h 41 m**. It is the first entry
in a record he will be reading for months, and it is the one entry we know is
wrong. Do this in the same visit, between steps 1 and 2 above:

1. In **Health Auto Export**, set the automation's window from **Since Last
   Sync** to a **date range covering 13–16 Sep** (a day either side, so a night
   filed under its wake date cannot fall off an edge).
2. **Run the export once**, manually.
3. Set the window **back to "Since Last Sync"** — or to the wider rolling
   window from step 2 of the visit, if that is what you settled on. Leaving it
   on a fixed date range means it re-sends the same days forever and never
   sends a new one.

Then check the server took it:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("/root/fitlog/fitlog.db")
for r in c.execute("SELECT date, start_ts, end_ts, asleep_h FROM "
                   "health_sleep_blocks WHERE date IN ('2026-09-14','2026-09-15') "
                   "ORDER BY date, start_ts"):
    print(r)
print("---")
for r in c.execute("SELECT date, value FROM health_metrics WHERE "
                   "metric='sleep_hours' AND date IN ('2026-09-14','2026-09-15')"):
    print(r)
c.close()
PY
```

2026-09-15 should move from **1.67** to about **5.68**.

**Rule S03 is what makes this safe to do.** The night already holds one block
(03:13→04:57). A re-export delivering the whole night as one longer block
*overlaps* that one, so it is treated as a better description of the same
stretch and the longer span wins — the two are never added. A re-export
delivering it as several blocks around the existing one sums the parts that do
not overlap. Either way the night cannot be double-counted, and re-running the
export twice changes nothing. Before today this same action would have
overwritten the night with whichever block arrived last.

If 09-15 does **not** move, the export window did not actually cover it — check
step 1 rather than assuming the ingest dropped it. `health_raw` keeps every
body, so nothing is lost either way.

Then, after the next export reaches the server, look at what it called it:

```bash
python3 - <<'PY'
import json, sqlite3
c = sqlite3.connect("/root/fitlog/fitlog.db")
names = set()
for (raw,) in c.execute("SELECT payload FROM health_raw ORDER BY id DESC LIMIT 40"):
    try:
        body = json.loads(raw or "")
    except ValueError:
        continue
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        continue
    for e in data.get("metrics") or []:
        if isinstance(e, dict) and "temp" in (e.get("name") or "").lower():
            names.add((e.get("name"), e.get("units")))
print(sorted(names) or "no temperature metric in the last 40 bodies")
c.close()
PY
```

Three spellings are mapped (`apple_sleeping_wrist_temperature`,
`sleeping_wrist_temperature`, `wrist_temperature`) plus `body_temperature` and
`basal_body_temperature`. **If that command prints a name not in that list,
say so and it gets mapped** — the ingest response's `skipped_metrics` reports
it too, so nothing is dropped silently either way.

Fahrenheit is converted. A unit the converter does not know is **dropped**, not
stored under a name ending `_c`, so an odd unit shows as a missing reading
rather than a wrong one.

## 1. Dry-run both patchers

```bash
cd /root/fitlog
python3 patch_fitlog_sleep_p2.py --file /root/fitlog/health_ingest.py --check
python3 patch_fitlog_sleep_p2_page.py --file /root/fitlog/app.py --check
```

Expect `anchors: 10/10` and `anchors: 9/9`. Anything else: stop.

## 2. Back up, then patch

```bash
python3 - <<'PY'
import sqlite3, datetime
src = sqlite3.connect("/root/fitlog/fitlog.db")
dest = "/root/backups/fitlog/fitlog.db.pre-sleepp2-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
dst = sqlite3.connect(dest)
with dst:
    src.backup(dst)
print(dest, dst.execute("PRAGMA integrity_check").fetchone())
dst.close(); src.close()
PY

python3 patch_fitlog_sleep_p2.py --file /root/fitlog/health_ingest.py
python3 patch_fitlog_sleep_p2_page.py --file /root/fitlog/app.py
python3 patch_fitlog_sleep_median_n7.py --file /root/fitlog/app.py
```

Each patcher takes its own `.bak-` copy and restores it if the post-write
compile fails.

## 3. Negative controls, then the suites

Rule 2a first — an assertion is evidence only if it has been seen to fail:

```bash
cd /root
python3 tools/NEGATIVE_CONTROL.py --manifest fitlog/new_assertions_sleep_p2.json
python3 tools/NEGATIVE_CONTROL.py --manifest fitlog/new_assertions_sleep_p2_page.json
python3 tools/NEGATIVE_CONTROL.py --manifest fitlog/new_assertions_sleep_median_n7.json
```

Expect `11 / 11`, `13 / 13` and `2 / 2` seen to fail, all `RESULT: PASS`. Run
the page one **before** applying the median patch (see the note at the top).
The two to watch: `M_score` puts a sleep score on the card on purpose, and
`M_neverenough` raises the median threshold to 99 — suppressing a misleading
figure must not quietly turn into suppressing the one comparison the page is
allowed to make.

Then the gate. **All green before `systemctl restart`.** Sleep spans midnight,
so the page suite runs at three clock times.

```bash
cd /root/fitlog
python3 test_sleep_night.py                                   # 10/10
python3 test_sleep_overnight.py                               # 11/11
python3 test_sleep_page.py                                    # 13/13
python3 /root/tools/RUN_AT_TIME.py 00 02 test_sleep_page.py app.py
python3 /root/tools/RUN_AT_TIME.py 23 58 test_sleep_page.py app.py
python3 /root/tools/RUN_AT_TIME.py 00 02 test_sleep_night.py health_ingest.py
python3 /root/tools/RUN_AT_TIME.py 23 58 test_sleep_night.py health_ingest.py
python3 test_health_ingest.py                                 # 23/23
python3 test_apple_aggregation.py                             # 29/29
python3 test_apple_records.py                                 # 56/56
python3 test_recompute_apple.py                               # 36/36
python3 test_hc_ingest.py                                     # 36/36
python3 test_ios_ingest.py                                    # 18/18
python3 test_ios_source_isolation.py                          # 19/19
python3 test_db_pin.py                                        # 12/12
python3 test_workout_day_ist.py                               # 12/12
python3 test_watch_page.py                                    # 39/39
python3 test_watch_strip.py                                   # 52/52
python3 tests/kb_lint.py && python3 tests/smoke_test.py       # PASS, 53/53
python3 test_activity_feed.py /root/gutlog/app.py             # 14/14
python3 test_gutlog_feed.py /root/gutlog/app.py               # 10/10
python3 test_analgesic_mirror.py /root/gutlog/app.py          # 8/8
python3 test_downdays_trend.py                                # 5/5
```

## 4. Restart and look at the page

```bash
systemctl restart fitlog
systemctl status fitlog --no-pager
journalctl -u fitlog -n 30 --no-pager
```

Then open `https://fit.dr-manoj.in/watch` **on the phone** and read the Sleep
card. Checks worth doing by eye, because the suite cannot do them:

- the clock times are the ones the Health app shows, in IST
- time in bed is not longer than the night actually recorded
- nothing on the card reads as a verdict about the night

## 5. Rollback

```bash
cp /root/fitlog/health_ingest.py.bak-sleepp2-<stamp> /root/fitlog/health_ingest.py
cp /root/fitlog/app.py.bak-sleepp2page-<stamp>       /root/fitlog/app.py
systemctl restart fitlog
```

Either can be rolled back alone. `app.py` without the ingest patch loses the
overnight figures but still renders; the ingest patch without the page patch
stores everything and shows nothing new.

## 6. Same day, back into the repo

`CHECK_FOLDER_PARITY.py` — the repo copies are already at this build, so the
expected result is byte-identical.

## Known and deliberate

- **Wrist temperature will be blank until it is exported.** That is the honest
  state: a metric never sent is absent, never zero.
- **The 14–15 Sep night is recovered in §0, not left.** It was briefly decided
  to leave it — one night, two setting changes and a change back — and that was
  the wrong call: it is the top entry on the Sleep card, it will be read for
  months, and it is the one entry known to be wrong. Recovering it is a
  two-minute phone action taken during a visit already being made.
- `recompute_apple_daily.py` still does not rebuild `health_sleep_blocks`.
  `reparse_sleep_blocks.py` is the tool for that and was run on 2026-09-15.
- `NO_SECRETS.py` refuses on working-tree `.bak-*` rollback copies. Clear them
  before publishing. Check D — tracked binaries — passes.

# Deploy — FITLOG_SLEEP_P1 (the night is stored whole)

> **DONE — deployed 2026-09-15 15:43 IST.** `health_ingest.py` md5 `3b0fd810…`,
> byte-identical to the repo build. Negative control 10/10 SEEN, then 20 suites
> green before the restart. Clean boot. Rollback kept at
> `health_ingest.py.bak-sleepp1-20260915_154304`; DB backup
> `/root/backups/fitlog/fitlog.db.pre-sleepp1-20260915_154222` (integrity ok).
> The re-parse in §"Known, deliberately not done here" **was** then done —
> see `reparse_sleep_blocks.py` and the changelog. This file is kept as the
> record of what was run. **Step 0 is still outstanding: it is on the phone.**

One file changes: `health_ingest.py`. No `app.py` change, no schema migration
step, no new secret, no service-unit change. `health_sleep_blocks` is created
in place on the first ingest, the same way `health_hc_records` is.

Nothing here scores a night. See the DOSSIER section *The sleep record*.

---

## 0. Before anything — the half of the fix that is not code

The four missing hours were **never in a payload**. Read against `health_raw`,
Health Auto Export delivered one 1 h 44 m block for the night of 14→15 Sep and
FitLog stored it faithfully to the last digit. Deploying this patch will not by
itself make a whole night appear.

On the iPhone, in **Health Auto Export**:

1. **Widen the export window.** The automation that posts to
   `fit.dr-manoj.in` is sending a window that clipped the night to its last
   block. Set it to cover at least the previous two days, not "today".
2. **Add Sleeping Wrist Temperature to the exported metrics.** A census of all
   212 stored bodies shows it has **never** arrived. `respiratory_rate` and
   `blood_oxygen_saturation` already do and are already mapped.

Do these whichever way round you like — the patch is what makes a two-block
night add up instead of overwriting itself, so widening the window **before**
the patch is deployed would still store the last block only.

---

## 1. On the server, dry-run first

```bash
cd /root/fitlog
python3 /root/fitlog/patch_fitlog_sleep_p1.py --file /root/fitlog/health_ingest.py --check
```

Expect `anchors: 8/8 matched`. Anything else: stop, nothing has been written.

## 2. Back the database up, then patch

```bash
python3 - <<'PY'
import sqlite3, datetime
src = sqlite3.connect("/root/fitlog/fitlog.db")
dest = "/root/backups/fitlog/fitlog.db.pre-sleepp1-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
dst = sqlite3.connect(dest)
with dst:
    src.backup(dst)
print(dest, dst.execute("PRAGMA integrity_check").fetchone())
dst.close(); src.close()
PY

python3 /root/fitlog/patch_fitlog_sleep_p1.py --file /root/fitlog/health_ingest.py
```

The patcher takes its own `.bak-sleepp1-<stamp>` of `health_ingest.py` and
restores it if the post-write compile fails.

## 3. Gate the restart on the suites

Run from `/root/fitlog`. **All of these must be green before `systemctl
restart`**, and the new one must be run at all three clock times — sleep spans
midnight, and a night filed under the wrong date is the defect most likely to
survive review.

```bash
cd /root/fitlog
python3 test_sleep_night.py                                   # 10/10
python3 /root/tools/RUN_AT_TIME.py 00 02 test_sleep_night.py health_ingest.py
python3 /root/tools/RUN_AT_TIME.py 23 58 test_sleep_night.py health_ingest.py
python3 test_health_ingest.py                                 # 23/23
python3 test_apple_aggregation.py                             # 29/29
python3 test_apple_records.py                                 # 56/56
python3 test_hc_ingest.py                                     # 36/36
python3 test_ios_ingest.py                                    # 18/18
python3 test_ios_source_isolation.py                          # 19/19
python3 test_db_pin.py                                        # 12/12
python3 test_watch_page.py                                    # 39/39
python3 test_watch_strip.py                                   # 52/52
python3 tests/kb_lint.py && python3 tests/smoke_test.py       # PASS, 53/53
```

Then the cross-app ones, which need GutLog's path as their argument:

```bash
python3 test_activity_feed.py /root/gutlog/app.py             # 14/14
python3 test_downdays_trend.py                                # 5/5
```

## 4. Restart and check

```bash
systemctl restart fitlog
systemctl status fitlog --no-pager
journalctl -u fitlog -n 40 --no-pager
```

Then confirm an ingest still answers and now reports its blocks — the response
gained a `sleep_blocks` count:

```bash
curl -s -H "Authorization: Bearer $(grep FITLOG_INGEST_TOKEN /root/fitlog/ingest.env | cut -d= -f2)" \
     'https://fit.dr-manoj.in/api/ingest/status'
```

After the next Auto Export post, the night should have block rows:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("/root/fitlog/fitlog.db")
for r in c.execute("SELECT date, start_ts, end_ts, asleep_h, awake_h "
                   "FROM health_sleep_blocks ORDER BY date DESC, start_ts LIMIT 10"):
    print(r)
print("---")
for r in c.execute("SELECT date, value FROM health_metrics WHERE metric='sleep_hours' "
                   "ORDER BY date DESC LIMIT 6"):
    print(r)
c.close()
PY
```

A night that reaches the server in two blocks will show two rows and a
`sleep_hours` equal to their **sum**. A night re-sent later as one merged block
will show three rows and a `sleep_hours` that has **not** changed — that is
rule S03 doing its job, not a failed write.

## 5. Rollback

```bash
cp /root/fitlog/health_ingest.py.bak-sleepp1-<stamp> /root/fitlog/health_ingest.py
systemctl restart fitlog
```

`health_sleep_blocks` can be left in place: nothing outside this patch reads
it, and the previous build never looks at it.

## 6. Same day, back into the repo

Per CLAUDE.md, pull the patched file back into `fitlog/` and
`fitlog-ingest/` and run `tools/CHECK_FOLDER_PARITY.py`. The repo copies are
already at this build, so the expected result is byte-identical.

## Known, deliberately not done here

- `recompute_apple_daily.py` replays stored bodies into `health_hc_records`
  and does **not** rebuild `health_sleep_blocks`. Replaying history would not
  recover the missing hours — they were never delivered — so the backfill buys
  nothing today. If a future replay is wanted for block metadata, that belongs
  in `recompute_apple_daily.py`, not in the ingest patcher.
- `NO_SECRETS.py` currently refuses on the working-tree `.bak-*` rollback
  copies (nine of them predate this work; all are git-ignored and untracked).
  Clear the rollback copies before publishing. **Check D — tracked binaries —
  passes: 2 allowed, none tracked outside the allowlist.**

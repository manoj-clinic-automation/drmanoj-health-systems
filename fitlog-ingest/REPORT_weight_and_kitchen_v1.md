# REPORT — both briefs, one session (26-Sep-2026)

`CLAUDE_CODE_PROMPT_run_both_v1.md`: the weight profile first, then Kitchen search. Details in
`REPORT_weight_profile_v1.md` and `REPORT_kitchen_search_v1.md`.

## Summary

- **m3:** `https://family.dr-manoj.in/m3/` — profile `weight`, seeded, readiness READY. **p1 (physio):** `https://family.dr-manoj.in/m3/physio/` — readiness READY.
- **PINs:** `/root/family/first-login.local.txt` on the server, root only, lines `m3` and `p1`. Never printed. Sheets for both in `fitlog-ingest\` (gitignored).
- **The one-line key-file command** (run once on the server as root):

```bash
grep -E '^(ANTHROPIC_API_KEY|SARVAM_API_KEY)=' /root/wa/.env | install -o root -g fam_kitchen -m 640 /dev/stdin /root/family/kitchen_keys.env
```

- **Versions live:** GutLog **3.41.0** (owner and m1/m2/m3), Kitchen **1.2.0**, RxGuard 1.9.0, FitLog 1.8.0; family tree `/opt/family/code/20260926_171622`.
- **Blocked:** nothing in the builds. The Kitchen key file waits for the command above. The Notion register was not updated (connector not authorised in this session).
- **Publish:** `PUBLISH_HEALTH.bat` result is recorded at the end of this file.

## Evidence in one table

| | Weight profile | Kitchen search |
|---|---|---|
| New suite | `test_family_e.py` 36/36 (PC and server) | `test_family_f.py` 16/16 (PC and server); `test_family_lock.py` 5/5 (server) |
| Browser at 300 px | `ui_e` 12/12 | `ui_f` 14/14 |
| Negative control | 18 declared, 18 seen | 17 declared, 17 seen |
| Existing family suites on the server | all green | all green (C02 updated for the fat column) |
| Owner suites on the server | 18/18, 16/16, SSO 11/11 | 18/18, 16/16 |
| Food table | — | `test_food_table_refresh.py` 6/6; 7,791 foods, 756 KB, 0.010 s on Python 3.9 |

## The build lock

Taken by hand for the first brief (as asked), by `build_lock.sh` in the tools for the second; the owner file named this repository and the brief; released at the end of each step, also on the one failed `upgrade_all.sh` run (trap). No other build held it at any point today.

## Publish

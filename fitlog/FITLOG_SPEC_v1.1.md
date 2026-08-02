# FitLog — Phase 0 Specification (v1.1)

Personal physical capacity & recovery engine for sustained orthopedic practice.
Companion apps: RxGuard (rx.dr-manoj.in) · GutLog (health.dr-manoj.in).
Supersedes v1.0. Status: SPEC — awaiting final sign-off.

---

## 1. Identity & Deployment (unchanged from v1.0)

Name **FitLog** · `fit.dr-manoj.in` · port **8040** · Flask + SQLite + gunicorn + systemd · DB `/root/fitlog/fitlog.db` (separate) · backup via `sqlite3.backup()` → `/root/backups/fitlog/`, 30-day retention, cron 02:20 · GutLog-pattern auth with owner-key gate · app-switcher bar (`RxGuard | GutLog | FitLog`) across all three apps.

---

## 2. Repository — NEW personal-systems repo

**Decision: yes, new repo.** Clinic and personal systems should not share a repo — different lifecycles, different sensitivity, cleaner Claude context.

**Repo:** `drmanoj-health-systems` (private)

```
drmanoj-health-systems/
├── CLAUDE.md              ← conventions file: server facts (srv1746119, ports,
│                            deploy pattern, backup pattern, systemd template,
│                            "python3 -m gunicorn", WinSCP delivery, test-before-
│                            deploy rule). Any Claude session reads this first.
├── fitlog/
│   ├── app.py
│   ├── knowledge/
│   │   ├── rules.json         ← engine rules + thresholds
│   │   ├── exercises.json     ← library (plan-tool-compatible fields)
│   │   ├── protocols.json     ← pre/transit/post event protocols
│   │   └── med_stack.json     ← personal analgesic stack seed
│   ├── tests/
│   │   ├── kb_lint.py
│   │   └── smoke_test.py      ← rule matrix incl. negative controls
│   ├── static/exlib/          ← exercise images
│   └── DOSSIER.md             ← single source of truth for this app
├── gutlog/                    ← optional future migration (docs first, code later)
└── rxguard/                   ← optional future migration
```

Why this works with Claude: monorepo + root `CLAUDE.md` means one clone gives full context; per-app `DOSSIER.md` keeps app truth local; knowledge as JSON keeps engine edits reviewable and testable. `drmanoj-clinic-automation` stays untouched, clinic-only.

---

## 3. Daily Flow (unchanged core, one addition)

Morning check-in (5 taps, ≤45s): sleep · energy · pain+sites · yesterday's exertion (auto/quick-add) · available minutes → verdict + session + START.

**Addition — Event-day cards:** if today has a logged event, the home screen shows up to three cards alongside the session:
- **PRE card** (before event) — timed prep protocol with its own START
- **TRANSIT card** (travel days only) — in-journey prompts
- **POST card** (after event) — recovery schedule for that evening
Each card closes with `Done / Partial / Skipped`.

---

## 4. Exertion Events — expanded model

```
exertion_events(id, type, date_start, date_end, intensity, mode,
                leg_duration_hr, notes, pre_status, transit_status, post_status)
```

| Type | Shape | Extra fields |
|---|---|---|
| `OT` | Day | intensity 1–3 |
| `SOCIAL` | Day | intensity 1–3 |
| `TRAVEL` | Period | **mode** = `car` / `flight`; **leg_duration_hr** per travel day |

Events logged **in advance** are the intended pattern: tomorrow's OT list, next month's flight. Advance logging is what makes PRE protocols and engine protection (F05) possible.

---

## 5. Event Protocols (`protocols.json`)

Deterministic templates keyed by `(type, phase, intensity/duration)`. Editable as data, not code. v1 content:

### OT
- **PRE (5 min, before scrubbing):** glute bridge ×10 · standing hip extension ×8/side · calf raise ×15 · hip hinge ×8 · thoracic opener ×5 · 30s march. Purpose: pre-activate the exact chain that fails at hour 3.
- **POST (evening of, ~30 min):** hydration + protein cue → Normatec Legs 20 min → heat to lumbar 10 min (parallel) → 5-min mobility sequence. Intensity-3 days append Normatec Hip 15 min.

### SOCIAL
- **PRE (3 min):** bridge ×10 · calf raise ×15 · hinge ×8.
- **POST:** next-morning engine handles load via F04; POST card = 5-min mobility only.

### TRAVEL — mode-aware
- **Car, any leg ≥ 2h:**
  - PRE: 5-min mobility before departure
  - TRANSIT: stop every 90–120 min → 3-min walk + hip extension ×8 + hinge ×8
  - POST (arrival): 10-min walk + 5-min mobility
- **Flight, leg < 3h:** PRE 5-min mobility; TRANSIT: seated calf pumps + glute squeezes every 30 min; POST: 10-min walk on arrival.
- **Flight, leg ≥ 3h (long-haul rules):**
  - PRE: full 8-min mobility + hydration start
  - TRANSIT: **stand + aisle walk 3–5 min every 60–90 min** (aisle seat preference noted in card) · seated calf pumps + isometric glute/quad sets every 30 min · hydration prompt hourly · minimal alcohol/caffeine cue
  - POST (arrival day): 10–15 min walk + mobility; Normatec Hip if packed; **next day auto-capped YELLOW** (rule F10)
- Transit compliance logged (`transit_status`) — long-haul standing/walking is tracked data, not just advice.

---

## 6. Decision Engine — rules v1.1

Verdicts, precedence, never-zero: unchanged from v1.0 (F01–F09, W01–W02). Additions:

| Rule | Condition | Effect |
|---|---|---|
| **F10** | Flight leg ≥ 3h today or yesterday | Today: transit set replaces session; next day ≤ YELLOW |
| **F11** | Event today with PRE protocol defined | PRE card surfaced; PRE completion logged but never changes verdict |
| **W03** | Analgesic logged ≥ 3 days in any rolling 14-day window | ⚑ Review flag on dashboard ("minimum effective analgesia" principle made visible) |

---

## 7. Pain Medication Stack (NEW)

Personal PRN analgesic stack lives in FitLog (decision from v1.0 discussion — training-linked; GutLog stays gut-focused).

```
med_stack(id, drug, dose_label, notes, active)        ← seeded from your list
analgesic_log(id, datetime, med_stack_id, dose_label,
              context_event_id NULL, pain_at_time, notes)
```

- **Logging = 2 taps:** pick drug → pick dose. Optional: link to today's event, pain score at time.
- Stack contents: **you supply the drug list** at build time (drug + usual dose options). Rows, not code — extend anytime.
- Dashboard: analgesic-use frequency plotted against exertion density, capacity tests, and med-epoch bands. This is the "reduce analgesic dependence" objective made measurable.
- No interaction checking here — that is RxGuard's job. FitLog only counts and contextualises use.

---

## 8. Exercise Library v1.1 — equipment confirmed

Equipment on hand: **treadmill · static cycle · dumbbells · theraband**. Starter set expands 15 → 18:

| # | Exercise | Category | Tiers | Equipment |
|---|---|---|---|---|
| 1 | Glute bridge → single-leg | Glute | G Y R | — |
| 2 | Clamshell (band progression) | Glute | G Y R | band |
| 3 | Hip extension | Glute | G Y | — |
| 4 | Monster / lateral band walk | Glute | G | band |
| 5 | Side plank (knee → full) | Core | G Y | — |
| 6 | Bird dog | Core | G Y R | — |
| 7 | Dead bug | Core | G Y R | — |
| 8 | Posterior pelvic tilt | Lumbar | R | — |
| 9 | Sit-to-stand → goblet | Lower limb | G Y | DB later |
| 10 | Step-up | Lower limb | G | — |
| 11 | Calf raise | Lower limb | G Y R | — |
| 12 | Wall sit (standing tolerance) | OT endurance | G Y | — |
| 13 | Suitcase / farmer carry | OT endurance | G | DB |
| 14 | Hip hinge → light RDL | Posture | G Y | DB later |
| 15 | Treadmill Zone-2 walk (incline option) | Aerobic | G Y | treadmill |
| 16 | Static cycle Zone-2 | Aerobic | G Y | cycle |
| 17 | Static cycle easy spin (10 min) | Recovery/aerobic | R | cycle |
| 18 | Band row | Upper body | G Y | band |

Cycle easy spin (#17) gives RED days a zero-impact aerobic option — valuable on post-OT days when walking feels heavy. Travel set remains the 3 no-equipment portables. Media (`image_path`, `video_url`) unchanged: schema slots from day one, enrichment non-blocking, video candidates proposed for your approval per exercise.

---

## 9. Capacity Tests (unchanged)

Week-0 baseline + Sunday re-test: bridge hold, side plank L/R, walk tolerance. Charted with med-epoch bands.

---

## 10. Schema Summary (v1.1)

```
checkins(id, date, sleep, energy, pain_max, pain_sites, avail_min, created_at)
exertion_events(id, type, date_start, date_end, intensity, mode,
                leg_duration_hr, notes, pre_status, transit_status, post_status)
sessions(id, date, verdict, rules_fired, plan, status, skip_reason, minutes_actual)
capacity_tests(id, date, bridge_sec, plank_L, plank_R, walk_min, notes)
med_epochs(id, label, date_start, date_end, notes)
med_stack(id, drug, dose_label, notes, active)
analgesic_log(id, datetime, med_stack_id, dose_label, context_event_id, pain_at_time, notes)
exercises(id, name_en, name_hi, category, tiers, dose_g, dose_y, dose_r,
          purpose, cues, mistakes, equipment, image_path, video_url, active)
protocols(id, event_type, phase, condition, content)
settings(key, value)
```

---

## 11. Build Sequence (unchanged gates)

0. Spec approved → rules.json / exercises.json / protocols.json / med_stack.json drafted
1. MVP build + smoke test (rule matrix incl. F10/F11/W03 negatives) → deploy 8040 → switcher bar in all 3 apps → repo `drmanoj-health-systems` created with CLAUDE.md
2. 2-week calibration: Week-0 baseline, daily use, threshold tuning
3. Dashboard + media enrichment
4. Deferred: manual/encyclopedia, Hyperice protocol pages, wearables

---

## 12. Remaining inputs from you

1. **Analgesic stack list** — drugs + usual dose options (needed to seed `med_stack.json`; can be a one-line message)
2. Confirm rule thresholds §6 and protocol content §5 as starting values (all tunable)
3. "Approved" → Phase 1 build starts

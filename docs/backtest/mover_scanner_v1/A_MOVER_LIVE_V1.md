# A-MOVER-LIVE-V1

Strategy A's mover pipeline on the shared Kiwoom premarket collector. Pre-production: this
document describes what is implemented and committed, not what is deployed.

| | |
|---|---|
| live scanner version | `A-MOVER-LIVE-V1` |
| live scanner checksum | `d7dfb853f5050918f6a87d7f88fe56dd5e7e7006c76e8e78ee9dac182afa87a3` |
| research parent | `a-mover-scanner-v1.2` |
| research parent checksum | `f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25` |
| live provider (same-day premarket) | `KIWOOM` |
| collector version | `ae_shared_premarket_collector_v1` |
| feature contract version | `a_mover_live_features_v1` |
| baseline version | `A_MOVER_PM_VOLUME_V1` |
| `ScannerRun.score_version` | `a_mover_live_v1` |
| `ScannerRun.provider` | `KIWOOM_AE_SHARED_PREMARKET` |
| config flag | `A_MOVER_LIVE_ENABLED`, default **off** |

The research contract is preserved unchanged and keeps its own artifacts. Its checksum is
recomputed before every live run and a live run refuses if it has moved. The live checksum is
computed over the *live* declaration and is never presented as the research one.

## 1. Why two versions

The two providers are measured as different, not assumed to be. On 14 full-depth sessions, the
cross-sectional Spearman of premarket dollar volume between the Massive mirror and Kiwoom is
0.822, the pooled Kiwoom/Massive ratio has a median of 0.491, and applying both affected
channels to the research panel leaves the identical-TOP8 rate at 1 session in 14 with a TOP35
overlap of 0.578. A single version covering both would make a reproducibility claim that the
data does not support.

So the ranking architecture is reused by reference and the version is separate. Reused:
every threshold, weight, knot and floor through `MoverScannerConfig`; the handoff mask and its
bounds through `StrategyConfig`; the pool size of 35 and the output maximum of 8.

## 2. The contract is hybrid, and it says so

| input | authority |
|---|---|
| same-day premarket bars | Kiwoom (`usa06011` / `usa06010`) |
| premarket relative-volume baseline | Kiwoom, `A_MOVER_PM_VOLUME_V1` |
| previous regular close | Massive grouped daily |
| 20-session ADV / ADDV | Massive grouped daily |
| reference (common-stock) universe | Massive reference cache |
| split calendar | Massive splits store |

Kiwoom has no equivalent for the daily inputs — `usa06012`-`06016` do not separate extended
hours, and `usa10099` carries no CIK or composite FIGI — so the Massive daily feed stays
load-bearing whatever happens to the minute lanes. `contract.HYBRID_SOURCES` is that table in
code and it travels on every persisted run.

## 3. The shared collector

There is no second collector and no second app key. The timestamp-safe lanes are exactly two
and E already holds both at 4.9 req/s against a measured 5 req/s per-API-ID limit, so a second
process has 0.1 req/s of room and cannot finalize anything. One process serves both cuts:

```
04:00            rolling cache over the union (E's own cycle, extended)
09:15:00  T0_A   A's cut  (the 09:14 bar is complete only at 09:15:00)
T1_A - 09:25     E's refresh of its own tick shard, in E's order
09:25:00  T0_E   E's cut over E's own universe, unchanged
09:29:45         Kiwoom calls stop, before A's first call at the 09:30 open
```

Every instant is built with `datetime.combine(session, moment, tzinfo=ET)`, so a DST transition
moves the wall clock and not the schedule.

**A's window is a prefix of E's.** A reads `[04:00, 09:15)`; E reads `[04:00, 09:24]`. No
request exists for A's sake that E would not have made; the union's extra *symbols* are the
only added cost.

**A's pass is E's own `refresh`, not a second finalization.** It merges at E's cutoff and
advances `complete_through` by E's own contiguity rule, so after A's pass that value is at or
past where E's own rolling cycle would have left it and E's tick lane pages back no further
than in the run its cost was measured on. A's 09:15 cut is therefore enforced at the *read*:
`snapshot._matrix` and `raw_store.bars_from_cache` both filter strictly below the cut, so every
A feature and every persisted A bar comes from a minute that had ended at 09:15:00.

**A's symbols never enter E's cache dictionary.** E aggregates availability, status counts and
its CSV over `caches`; the integration keeps union-only symbols in its own `extra_caches`.

### E's SLA is preserved by two mechanisms, not one

Which one applies depends on the union size, and modelling only the second gives the wrong
answer at the small end:

| union | A T1 (ET) | slack to E's cut | mechanism |
|---|---|---|---|
| 2,561 | 09:19:26 | 333.2 s | E's own refresh still fits (needs 133.1 s) |
| 3,847 | 09:21:40 | 199.3 s | E's own refresh still fits |
| 4,567 | 09:22:55 | 124.3 s | A's ordering bounds staleness (oldest 259.9 s vs 260.0 s) |
| 4,947 (measured) | 09:23:35 | 84.7 s | A's ordering bounds staleness (oldest 220.3 s) |
| 5,226 | 09:24:04 | 55.6 s | A's ordering bounds staleness (oldest 191.3 s) |

A small union finishes early, leaving a wide gap in which E's existing 09:20:40 tick-shard
refresh runs exactly as it does today. A large union finishes late and cuts that refresh short;
then A's tick-shard-last ordering carries it, because those symbols were read in the final
stretch of A's own pass. The 4,567 row is the crossover and it is tight: 259.9 s against E's
measured 260.0 s. Both mechanisms are in `schedule.StalenessBound`, and a union satisfying
neither reports `mechanism = NONE` rather than passing quietly.

## 4. The union universe (recomputed, never written down)

Measured on 2026-09-15 from the stores on disk:

| | |
|---|---|
| reference cache in force | 2026-07-01, 5,222 active common stocks |
| pruned: no full 20-session daily baseline | 273 |
| pruned: split executes this session | 2 |
| **A live universe** | **4,947** |
| E universe | not staged for this session (E's artifact is dated 2026-09-22) |
| union | 4,947 |

The E side is read only from the artifact E itself staged, dated on or before the session, and
`e_side_status` says `NOT_STAGED_FOR_THIS_SESSION` rather than reporting zero as a fact about
E. The earlier audit measured E at 2,561 with 4-5 E-only symbols; adding 5 symbols moves A's T1
by 0.52 s, and even +300 still fits between the cuts.

**Only three prunings are applied**, and they are the ones derivable before the open: the
active common-stock reference cache, a full 20-session daily baseline (which `scan` requires
anyway), and no split executing that morning. Two prunings are deliberately *rejected* and the
reason is recorded in `universe.PRUNE_REJECTED`: a D-1 close floor is a judgment rather than a
derivation of A's own rule (A rejects on the premarket print price), and "it had no premarket
print yesterday" removes exactly the catalyst-day symbol the scanner exists to find — the
measured failure of a ranking prefilter, which held recall at 40-62% while the missed names
carried a median gap of +5.1%.

## 5. A's baseline: `A_MOVER_PM_VOLUME_V1`

E's `rvol_store` values are not reusable, and the reason is arithmetic, not preference:

| | E (V2) | A |
|---|---|---|
| quantity | premarket **dollar** volume | premarket **share** volume |
| window | `[04:00, 09:24]` | `[04:00, 09:15)` |
| membership | exactly the 20 prior XNYS sessions, all COMPLETE | the last 20 **covered** sessions |
| silent covered session | not usable | contributes **zero** |
| statistic | median (min 5) | median (needs all 20) |
| floor | none | `max(median, 1,000)` at the scanner's division |

What *is* reused is the machinery, not a new framework: the durable table
`premarket_volume_sessions`, whose identity already includes `source` and `collector_version`,
so A's rows (`KIWOOM_USA06011_PM0915` / `a_mover_pm_volume_v1`) live beside the V2 rows without
either reinterpreting the other — **and no migration is needed**. `AMoverPremarketVolumeService`
is `PremarketVolumeHistoryService` with three overrides: the window ends at A's cut, the
planning window is A's lookback, and one run asks only for the sessions the twenty still needs.

A's covered-session walk is expressed as panel coverage rather than reimplemented:
`PremarketPanel.rvol_baseline` already takes "the last N covered sessions strictly before this
column", so the live denominator is the research denominator by construction, and the scan
session's own volume cannot enter its own denominator.

### Current rows and the backfill

Measured against the local database on 2026-09-15:

| | |
|---|---|
| schema | `premarket_volume_sessions` (existing; no migration) |
| current rows for this identity | **0** |
| eligible symbols | 4,947 |
| sessions to fetch | 98,940 (20 per symbol) |
| estimated pages / calls | 1,103,181, single lane (`usa06010` cannot page deep history) |
| estimated runtime | **62.6 - 77.8 h** |
| worst case (newest sessions come back uncovered) | 125.2 - 155.7 h, over more *runs* |
| guard-free hours per day | 18.33 (collection refused 03:55-09:35 ET) |

The estimate is scaled from E-RT3's own measurement — 223 pages and 63 s per symbol for *its
twenty sessions* — so the unit is per twenty sessions and a plan needing a different number is
scaled, not charged the whole unit. An earlier draft of this estimate charged the full
40-session lookback and overstated the work by about 2x.

**Resumption is the durable table, not a file.** A stored row is final for its source and
collector version, so a killed run resumes by being started again and cannot double-collect.
The progress file is for reporting.

**The full backfill was not run.** `backfill.execute` refuses without `confirm_network=True`,
and it checks the 03:55-09:35 ET guard before every symbol rather than once at the start.

## 6. Scan, handoff, decision, injection

Fixed and read from the deployed configs, never written in the live path: pool 35; weights
0.30 premarket dollar volume / 0.25 premarket RVOL / 0.20 gap quality / 0.15 premarket
momentum / 0.10 tradability; the gap-quality knots; the mask's three conditions (direction, gap
min, gap max) with no volume filter; output maximum 8.

**The gate window is absent, not faked.** Strategy A's deployed premarket gate reads
`[04:00, 09:30)` and evaluates at the open. At 09:15 that window is not over, so `gate_*` stays
NaN and `scan.gate_reading` returns an unavailable reading. The real gate still runs at the open
in the entry path; what is lost is the study's gate-pass *count*, which was a measurement and
never an admission.

**A's momentum reference is 08:15, not 09:00.** `momentum_late_window_minutes` is 60, so the
reference is the last close strictly before 08:15. 09:00 is E's `return_0900_0925`.

**The GPT prompt text does not change.** `ResearchPromptService` renders from persisted rows and
states the candidate count from the number of rows it is given, so a five-candidate session
renders a five-symbol prompt by itself. Zero candidates persists the run — a quiet morning must
stay distinguishable from a morning the scanner never ran — and renders no prompt at all.

**Authority is unchanged.** GPT analyses and scores; a human approves or rejects. Only
`APPROVE` reaches entry.

**Paper injection modifies no deployed file.** `EntryManagementRuntime` already takes its
lifecycle service as a constructor argument, so `MoverLiveEntryLifecycleService` overrides
exactly two things — `analysis_session_date` (the live run's `trading_date` *is* the entry
session; the legacy predecessor rule belongs to the trade-value scanner that ranks after the
close) and `approved_candidates` (source-filtered, no union, no fallback). `evaluate` is
inherited verbatim, so every candidate travels `EntryLifecycleService.evaluate` ->
`StrategyV0Engine` -> `RiskEngine` -> `StrategyLifecycleRunner` -> position and exit.

**Legacy rows are untouched.** A legacy run keeps `quant_v0` and resolves through the unchanged
predecessor path; the research forward contract keeps `mover_v1.2`; the live contract is
`a_mover_live_v1`. `paper_adapter.source_of` classifies all three without updating a row.

## 7. Parity is an audit metric

Massive-versus-Kiwoom TOP8 agreement is recorded by `scanner.parity` and read by nothing.
`contract.parity_is_a_production_gate` is `False`. A parity gate would permanently block a
Kiwoom-native strategy for being Kiwoom-native, and the divergence is already measured.

## 8. Refusals: no silent fallback

`config.Refusal` is the whole vocabulary of not running: `DISABLED`, `SCANNER_NOT_RUN`,
`DATA_UNAVAILABLE`, `CONTRACT_DRIFT`, `NO_CANDIDATES`. Every one means zero GPT calls and zero
candidate injection. The legacy scanner is never substituted: that would attribute one
morning's entries to a scanner that never saw that morning.

## 9. Dry run

`SHARED_COLLECTOR_DRY_RUN = PASS` (offline, 2026-09-15, 60 symbols over the repository's own
recorded production payload of 322 real premarket minutes, driven through the real
`run_a_pass`):

| check | |
|---|---|
| A T0 is 09:15 | PASS |
| A pass completed before E's cut | PASS |
| A cut respected (nothing at or after 09:15 read) | PASS |
| tick shard ordered last | PASS |
| E's measured tick cost preserved | PASS |
| E T1 before the 09:29:45 deadline | PASS |
| per-lane rate within the per-API-ID limit (4.89 vs 5.0) | PASS |
| expected 429 | 0 |
| no raw conflict | PASS |
| snapshots for every usable symbol | PASS |
| GPT prompt text unchanged | PASS |
| only APPROVED injected | PASS |

Funnel: 60 evaluated -> 60 eligible -> 35 discovery pool -> 27 actionable -> **8 GPT output**
-> 7 approved (1 rejected, mocked) -> **7 injected**. Raw persistence: 18,300 bars, 0
quarantined. No profit or loss is computed anywhere.

Reproduce:

```
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run budget --session 2026-09-15
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run dry-run --session 2026-09-15 --symbols 60
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run backfill-plan --session 2026-09-15
```

The local Massive grouped daily store ends at 2026-09-16, so 2026-09-15 is the newest session
the budget can be computed for; a later session refuses with `NO_GROUPED_DAILY` rather than
scanning a gap.

## 10. Status

`READY_FOR_BASELINE_BACKFILL`. The pipeline is implemented, tested and committed with the flag
off. The blocker to a live run is the baseline: 0 rows exist for `A_MOVER_PM_VOLUME_V1` and A
refuses every symbol without its full twenty, so the 62.6-77.8 h collection is the next step
and needs an explicit decision. Nothing has been pushed, deployed, restarted or enabled, no
migration exists or was applied, and no real order was placed.

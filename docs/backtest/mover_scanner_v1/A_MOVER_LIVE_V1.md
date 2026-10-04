# A-MOVER-LIVE-V1

Strategy A's mover pipeline on the shared Kiwoom premarket collector. Pre-production: this
document describes what is implemented and committed, not what is deployed.

| | |
|---|---|
| live scanner version | `A-MOVER-LIVE-V1` |
| live scanner checksum | `382ba2c16409cb1009006b992c2c0d05e0c8a25c29ee768f997c5027fbd3f22b` |
| research parent | `a-mover-scanner-v1.2` |
| research parent checksum | `f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25` |
| live provider (same-day premarket) | `KIWOOM` |
| collector version | `ae_shared_premarket_collector_v1` |
| feature contract version | `a_mover_live_features_v1` |
| baseline version | `A_MOVER_PM_VOLUME_V1` |
| baseline provider contract | `KIWOOM_PREFERRED_PER_SESSION+MASSIVE_TAPE_BOOTSTRAP` |
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
| premarket relative-volume baseline | `A_MOVER_PM_VOLUME_V1`: Kiwoom per session where it has one, frozen Massive tape otherwise |
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

### The denominator is a provider mix

Twenty Kiwoom sessions do not exist on the first morning and there are only two ways to reach
them: wait twenty trading days, or run the 60-plus-hour historical collection below. Neither is
what the strategy is waiting for, so the walk reads **two** row identities and prefers Kiwoom
**per session**:

| identity | `source` / `collector_version` | supplies |
|---|---|---|
| Kiwoom | `KIWOOM_USA06011_PM0915` / `a_mover_pm_volume_v1` | every session it has an observation for |
| bootstrap | `MASSIVE_MINUTE_TAPE_PM0915` / `a_mover_pm_volume_v1_bootstrap` | the rest |

The precondition for a live calculation is **twenty combined covered sessions**. The provider
mix is recorded, never gated on: every result carries `baseline_session_count`,
`kiwoom_session_count`, `massive_session_count` and `baseline_mode`
(`MASSIVE_BOOTSTRAP` -> `MIXED_BOOTSTRAP` -> `KIWOOM_NATIVE`), and those four names travel onto
every persisted candidate row and the entry session's own record. A run that admitted nobody,
or an entry session with no run, reads `UNKNOWN` rather than zero sessions, because zero
sessions would be a claim about a denominator nothing computed.

**The bootstrap half is a read of the frozen local tape, not a collection.** `bootstrap.py`
produces A's own quantity — premarket share volume over `[04:00, 09:15)` — through
`mover_scanner_v1.premarket.build_panel`, the research arm's own function at the research arm's
own cut, so a bootstrap session and a Kiwoom session are the same measurement of different
bars. Coverage is the tape's ledger: a covered session with no print contributes a zero, and a
pair the tape does not cover produces **no row at all** and is named
(`SESSION_NOT_IN_TAPE`, `SYMBOL_NOT_IN_TAPE`, `SYMBOL_SESSION_NOT_COVERED`). Nothing turns
absent coverage into a zero. There is no network request on this path.

Materialization happens ahead of the cut rather than at it. A's cut has about five minutes
before E's finalization and a twenty-session panel is tens of thousands of memory-mapped
slices; a past session's premarket volume does not change, so the 09:15 read stays an indexed
query.

**The cut-time cost was measured, not assumed**, because the margin on E's 09:29:45 deadline is
seconds. Over a 4,947-symbol universe with 40 sessions of rows per identity, on this machine:

| at A's cut | before | after |
|---|---|---|
| denominator read | 6.6 s (one identity, per symbol) | **3.7 s** (two identities, batched) |
| forward observation write | - | 2.4 s |
| total | 6.6 s | **6.1 s** |

Reading two identities one symbol at a time would have cost 11.3 s, so `load_baselines` batches
the rows into a handful of `IN` queries and keeps `_walk` as the only implementation of the
covered walk; a test asserts the batch and per-symbol readers produce byte-identical
declarations over the same rows. The net effect at the cut is slightly *faster* than the
single-provider read it replaces.

**The replacement is automatic.** At each cut the shared collector's own snapshots are stored
under the Kiwoom identity (`record_forward_observations`, `quality_reason =
A_CUT_FORWARD_OBSERVATION`, `regular_bar_count = 0` because at 09:15 there is no regular
session to have observed). The session written is the scan session, which `lookback_sessions`
excludes, so it cannot reach its own denominator; from the next session on it is a Kiwoom
candidate. One more Kiwoom session enters the twenty and the oldest bootstrap session leaves
it, with **no operator step**, so after at most twenty completed forward sessions the mode is
`KIWOOM_NATIVE` on its own. The write goes through the V2 upsert, so a restart at the cut
writes the same row once.

Measured on the real local tape (`ca1ce9d030cdcb88`, 104 sessions 2026-04-20 - 2026-09-16,
3,882 symbols), 200 sampled A-universe symbols:

| entry session | sessions in the 40-session window absent from the tape | symbols with >= 20 covered | median covered |
|---|---|---|---|
| 2026-09-15 | 0 | 143 / 200 | 40 |
| 2026-10-05 | 12 (2026-09-17 - 2026-10-02) | 188 / 200 | 28 |

The 2026-10-05 row is the honest shape of the bootstrap today: the twelve newest sessions are
not on the tape, the covered walk reaches past them inside its declared 40-session lookback,
and the denominator is therefore built from older sessions and says so through
`oldest_used_session` / `newest_used_session`. A symbol the tape does not carry at all stays
`INSUFFICIENT_COVERED_SESSIONS` rather than acquiring invented zeros.

### The full Kiwoom backfill: not required

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

**The full backfill was not run, and is no longer a precondition.** With the bootstrap half in
place, the sixty-plus hours buy a Kiwoom-native denominator *sooner* than twenty forward
sessions would; they do not unblock anything. `backfill.execute` still refuses without
`confirm_network=True` and still checks the 03:55-09:35 ET guard before every symbol, and the
planner is kept for the day that trade is worth making.

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
| mixed baseline available (20 combined sessions) | PASS |
| baseline mode matches the seeded mix | PASS |
| both session counts exact | PASS |
| scan session not in its own denominator | PASS |
| forward replacement advances without an operator | PASS |
| forward write idempotent | PASS |
| baseline mode persisted on every candidate row | PASS |

Funnel: 60 evaluated -> 60 eligible -> 35 discovery pool -> 27 actionable -> **8 GPT output**
-> 7 approved (1 rejected, mocked) -> **7 injected**. Raw persistence: 18,300 bars, 0
quarantined. No profit or loss is computed anywhere.

The denominator is driven through all three modes, and the forward step is driven once in each
so the transition is measured rather than asserted:

| `--kiwoom-sessions` | mode | K / M | next session | GPT | injected |
|---|---|---|---|---|---|
| 0 | `MASSIVE_BOOTSTRAP` | 0 / 20 | `MIXED_BOOTSTRAP`, 1 / 19 | 8 | 7 |
| 10 | `MIXED_BOOTSTRAP` | 10 / 10 | `MIXED_BOOTSTRAP`, 11 / 9 | 8 | 7 |
| 19 | `MIXED_BOOTSTRAP` | 19 / 1 | `KIWOOM_NATIVE`, 20 / 0 | 8 | 7 |
| 20 | `KIWOOM_NATIVE` | 20 / 0 | `KIWOOM_NATIVE`, 20 / 0 | 8 | 7 |

The mode and both counts are read back out of the persisted candidate rows through
`paper_adapter.baseline_stamp_of`, not out of the in-memory scan.

Reproduce:

```
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run budget --session 2026-09-15
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run dry-run --session 2026-09-15 --symbols 60 --kiwoom-sessions 0
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run bootstrap-coverage --session 2026-10-05 --limit 200
PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run backfill-plan --session 2026-09-15
```

The local Massive grouped daily store ends at 2026-09-16, so 2026-09-15 is the newest session
the budget can be computed for; a later session refuses with `NO_GROUPED_DAILY` rather than
scanning a gap.

## 10. Status

### The commit graph is incomplete, and the missing piece is not A's

A standalone checkout of A's commits **does not import**, and neither does `app.main`:

```
ImportError: cannot import name 'GapDirection' from 'app.strategy.config'
```

`mover_scanner_v1/scan.py` and `actionability.py` read the premarket gate's admitted gap sign
from `StrategyConfig.premarket_gap_direction`, which is exactly right - the handoff checksum
includes the gap band, so the mask must come from the deployed config rather than be restated.
But `GapDirection` and `premarket_gap_direction` exist only as an **uncommitted edit** to
`backend/app/strategy/config.py`, and that edit is another session's gap-sensitivity and
entry-family research (`EntryMode`, `time_progress_stop_minutes`,
`max_breakout_distance_r`, `max_signal_bar_volume_ratio`, `trend_lookback_bars`). It is not
A's to commit.

Measured in an isolated worktree holding committed content only: that one name is the whole
gap. With a two-field shim for it, all 23 A entry points import and A's committed suites pass
290 tests; without it, 19 of 23 fail at import and `app.main` fails with them.

**So a production pull of these commits, without that file, would stop the backend from
starting.** That is the sharpest reason nothing here is pushed or deployed. The fix is one of:
the owning session commits its `strategy/config.py` work, or `premarket_gap_direction` is
split out into a commit of its own with the owner's agreement.

### Data

`BLOCKED_STALE_MASSIVE_DAILY_FEED`. The pipeline is implemented, tested and committed with the
flag off, and the baseline is no longer the blocker: the mixed bootstrap removes both the
twenty-session wait and the 62.6-77.8 h collection from the critical path.

What is left is **not** A's code. A's hybrid contract makes the Massive grouped daily store
load-bearing for the previous close, the daily volume baselines and the union universe, and
that store ends at **2026-09-16**. For the next session (2026-10-05) both
`universe.build` and `features.live_daily_panel` refuse with `NO_GROUPED_DAILY` naming
2026-09-17, which is the correct refusal and not a bug: twelve sessions of grouped daily
(2026-09-17 - 2026-10-02) and the splits store (frozen at 2026-09-16) have to be caught up
before the first live run, and that is a Massive collection decision rather than part of this
stage.

The bootstrap half is unaffected by that gap — 188 of 200 sampled symbols already have twenty
or more covered sessions for a 2026-10-05 entry — so once the daily feed is current, the first
morning is calculable, the mode is `MASSIVE_BOOTSTRAP`, and it walks itself to `KIWOOM_NATIVE`
over at most twenty completed forward sessions.

Nothing has been pushed, deployed, restarted or enabled, no migration exists or was applied,
and no real order was placed.

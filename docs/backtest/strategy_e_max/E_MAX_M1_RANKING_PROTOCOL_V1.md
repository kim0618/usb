# Strategy E-MAX - M1 Candidate Strength Ranking Protocol V1

| | |
|---|---|
| Stage | `E-MAX-M1`, phase A (protocol), frozen before any M1 performance |
| Machine authority | `strategy_e_max_m1_rules_v1.json`, canonical `4556a4f3…4c79` |
| Upstream | M0 `b05bbf6` (rules `3ae07d75…`); E-R2 `86b750ef…`; V1.1 `b90573bd…`; E-R3 result `2d50cca5…` |
| Code | `app.strategy_e_max.ranking` (orderings), `app.strategy_e_max.m1` (loader, eligibility, improvement, winner) |
| Tests | `backend/tests/strategy_e_max/test_e_max_m1_protocol.py` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |

## 1. What M1 tests

M1 tests whether ordering a session's sealed H5 candidates by strength, then taking the first
three, beats Base's canonical symbol order. H5 membership, the universe, capacity 3, entry, exit,
cost, sizing and 1.0x exposure are all held fixed.

| id | ordering (tie: symbol ascending) |
|---|---|
| R0 (comparator) | canonical symbol ascending, E-Base |
| R1 | `premarket_rvol` descending |
| R2 | `return_0900_0925` descending |
| R3 | sum of descending average ranks of `premarket_rvol`, `return_0900_0925` and `position_in_premarket_range`, ascending |

The following are forbidden: R4 or any other ordering, gap-only, weights or weight search,
products, z-scores, learned rankings, liquidity/price/sector rankings, outcome-based ordering,
symbol exclusion (ABSI included), and combinations of R1 to R3.

## 2. Data and prechecks

The data is the E-R2 / E-R3 binding, unchanged: tape `d12ff28a…`, 2,152 files, 1,902 symbols,
USB-HIST-V1, and 480 sessions from 2024-10-16 to 2026-09-16. Sessions from 2026-09-17 onward are
excluded.

The E-R3 prechecks are re-run before any return: frozen chain, dataset binding, V1 reproduction
70,738 / 1,729, V1.1 attribution, and PIT poison.

## 3. Replay method

- **R0.** Runs the E-R3 path unchanged. Its `trades.csv` and `daily_returns.csv` must hash to the
  committed E-R3 artifacts (`e4670ec3…`, `d0d3f82d…`).
- **R1 to R3.** Each session's variant-selected symbols go to the unchanged E-D2..E-D5 layers.
  Not-selected H5 candidates are recorded as `NOT_SELECTED_CAPACITY` with zero weight, and the
  variant's priority is written to `selection_rank`.
- **Equivalence check.** The subset method, run with R0's ordering, must reproduce R0's session
  returns exactly before any variant is read.
- **Invariants.** Every variant has the same universe rows and H5 candidates. Selected symbols are
  H5 candidates of their session. No session selects more than 3. Sessions with at most 3
  candidates select the same set in every variant.

## 4. Evaluation

Metrics are E-D6's `evaluate` over all 480 sessions, with no-trade sessions counted as 0, plus:

- CAGR = `(1+cum)^(252/480) - 1`;
- Calmar = CAGR / |MDD|;
- largest winning and losing trade.

The paired test uses delta_t = candidate − R0 COST_10BP session return over all 480 sessions. It
is an IID session bootstrap (`metrics.bootstrap_mean_ci`, 10,000 replicates, seed 20260921),
reporting the mean, CI and P(delta <= 0).

## 5. Gates, all copied from M0

- **Eligibility.**
  - integrity PASS;
  - coverage >= 95%;
  - 10 bp session mean > 0;
  - PF > 1;
  - MDD >= -35%;
  - top1 share of net contribution <= 0.7338.
- **Improvement.**
  - 10 bp cumulative return > R0;
  - paired mean delta > 0;
  - P(delta <= 0) <= 0.10.
- **Winner.**
  - chosen from the pool of candidates passing both gates, by highest 10 bp CAGR;
  - exact ties go to the higher session mean, then to declared order R1, R2, R3;
  - **M0 declares no CAGR tie band, so none is applied.**
- **No winner.** `E-MAX-M1 NO_USEFUL_ENHANCEMENT`, and R0 carries into M2.

## 6. Diagnostics (never used to choose)

- **Periods.** FULL is 2024-10-16..2026-09-16. LEGACY_NARROW is 2024-10-16..2026-04-17 (the
  30-symbol tape). BROAD_COVERAGE is 2026-04-20..2026-09-16.
- **Selection delta versus R0.** Reported: sessions with candidates, sessions with more than 3,
  sessions whose selected set changed (and its ratio), ordering-only changes, symbols
  added/removed, and changes in the trade set, entry-valid set and standard-PnL set.
- **Other.** Four blocks and concentration: unique symbols, top1/5/10, HHI, largest winner/loser.
- **Interpretation limit.** Ranking can only matter on about 101 sessions, almost all in the broad
  period (block 4). A winner means a preregistered ranking beat Base selection there. It does not
  validate a ranking alpha over two years.

## 7. Reproducibility and outputs

The run is executed twice. Both runs must give an identical result digest and byte-identical
artifacts; otherwise the verdict is `E-MAX-M1 BLOCKED — INTEGRITY`.

- Runtime artifacts: `data/runtime/strategy_e_max/m1_runs/<run_id>/` (gitignored).
- Committed artifacts: `E_MAX_M1_RANKING_RESULT_V1.md`, `strategy_e_max_m1_result_v1.json`,
  `.sha256`.

## 8. Verdict labels

Exactly one of the following:

- `E-MAX-M1 PASS — R1 SELECTED`
- `E-MAX-M1 PASS — R2 SELECTED`
- `E-MAX-M1 PASS — R3 SELECTED`
- `E-MAX-M1 NO_USEFUL_ENHANCEMENT`
- `E-MAX-M1 BLOCKED — INTEGRITY`

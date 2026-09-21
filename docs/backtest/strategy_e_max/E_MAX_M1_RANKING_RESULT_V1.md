# Strategy E-MAX - M1 Candidate Strength Ranking Result V1

| | |
|---|---|
| Stage | `E-MAX-M1`, phase B, run once under protocol `058217f` (rules `4556a4f3…`) |
| Run id | `em1-2c8a31f0577e` |
| Result | `strategy_e_max_m1_result_v1.json`, sha256 `eac7dfd78635216e…` (byte-identical on a second, independent run) |
| Runtime | `data/runtime/strategy_e_max/m1_runs/em1-2c8a31f0577e/` (gitignored) |
| Evidence label | **E-MAX DEVELOPMENT** (reused development data; not OOS) |
| **Final gate** | **`E-MAX-M1 PASS — R1 SELECTED`** |

## A. Preflight / protocol freeze

- Start HEAD was `b05bbf6` (M0). The protocol was committed first as `058217f`, and both runs
  record that commit as their HEAD. M0 (`3ae07d75…`), E-R1, E-R2 and E-R3 are all ancestors.
- The E-R3 prechecks were re-run before any return, and all passed:
  - dataset binding: tape `d12ff28a…`, 480 sessions;
  - V1 reproduction: 70,738 / 1,729;
  - V1.1 attribution: 71,119 rows;
  - PIT poison: 0 seals moved.
- **R0 reproduces E-R3 byte for byte.** `trades.csv` hashes to `e4670ec3…` and
  `daily_returns.csv` to `d0d3f82d…`. The subset method with R0's order reproduced R0 record for
  record before any variant was read.
- **Invariants held.** Every variant has the same universe (71,119) and the same H5 set (1,738).
  Each variant selects 501. No session selects more than 3. Sessions with at most 3 candidates
  are identical across variants.

## B. Coverage limitation

| period | sessions | sessions with > 3 H5 candidates |
|---|---:|---:|
| LEGACY_NARROW, 2024-10-16..2026-04-17 (30-symbol tape) | 376 | 4 |
| BROAD_COVERAGE, 2026-04-20..2026-09-16 | 104 | 97 |

Ranking can change the selection only on these 101 sessions, and all three rules changed the
selected set on essentially all of them.

**This result is a statement about the broad-coverage period, chronological block 4.** It does
not validate a ranking alpha over two years.

## C. Base (R0, frozen E-R3)

At 10 bp:

| metric | value |
|---|---|
| trades | 485 |
| session mean | +1.89 bp |
| PF | 1.122 |
| cumulative | +7.19% |
| CAGR | 3.71% |
| MDD | -16.45% |
| Sharpe | 0.32 |
| CI | [-6.71, +10.48] bp |
| blocks | 2/4 |
| ABSI top1 | 73.4% |

## D. Ranking definitions (as frozen in M0 and M1)

The tie-break is always symbol ascending. Capacity is 3, exposure 1.0x, cost 10 bp, and entry,
exit and sizing are those of Base.

- **R1:** `premarket_rvol` descending.
- **R2:** `return_0900_0925` descending.
- **R3:** sum of the descending average ranks of rvol, r0900 and position, ascending.

## E. Selection delta versus R0

| | eligible to change | selected set changed | ratio | ordering-only | symbols added / removed | trade-set changed |
|---|---:|---:|---:|---:|---:|---:|
| R1 | 101 | 101 | 100% | 25 | 220 / 220 | 101 |
| R2 | 101 | 97 | 96.0% | 39 | 209 / 209 | 97 |
| R3 | 101 | 101 | 100% | 33 | 213 / 213 | 101 |

All 239 sessions with H5 candidates are shared by every variant.

## F. Candidate funnel

| | universe | H5 | selected | capacity skipped | valid entry | ENTRY_INVALID | valid exit | UNRESOLVED | standard PnL | coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| R0 | 71,119 | 1,738 | 501 | 1,237 | 499 | 2 (shortened) | 485 | 14 | 485 | 97.19% |
| R1 | 71,119 | 1,738 | 501 | 1,237 | 499 | 2 (shortened) | 480 | 19 | 480 | 96.19% |
| R2 | 71,119 | 1,738 | 501 | 1,237 | 497 | 4 (2 missing 09:30, 2 shortened) | 471 | 26 | 471 | **94.77%** |
| R3 | 71,119 | 1,738 | 501 | 1,237 | 497 | 4 (2 missing 09:30, 2 shortened) | 470 | 27 | 470 | **94.57%** |

R2 and R3 selected two symbols without a 09:30 bar. Those slots stayed empty, with no backfill,
as V1.1 requires.

## G. Performance at 10 bp

| | trades | active | trade mean | median | win | PF | session mean | cumulative | CAGR | MDD | Calmar | Sharpe | Sortino |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| R0 | 485 | 237 | +6.82 | -6.25 | 48.2% | 1.122 | +1.89 bp | +7.19% | 3.71% | -16.45% | 0.23 | 0.32 | 0.47 |
| **R1** | 480 | 237 | **+21.43** | -2.42 | 49.4% | **1.367** | **+6.55 bp** | **+33.09%** | **16.19%** | **-15.45%** | **1.05** | 0.95 | 1.48 |
| R2 | 471 | 237 | +5.09 | -10.00 | 46.7% | 1.073 | +0.33 bp | -1.21% | -0.64% | -16.09% | -0.04 | 0.05 | 0.07 |
| R3 | 470 | 237 | +13.49 | -0.39 | 50.0% | 1.222 | +3.62 bp | +15.95% | 8.08% | -14.58% | 0.55 | 0.55 | 0.81 |

Cost sensitivity, as session mean / cumulative / MDD:

| cost | R0 | R1 | R2 | R3 |
|---|---|---|---|---|
| 0 bp | +6.83 / +35.8% / -9.1% | +11.49 / +68.6% / -9.7% | +5.27 / +25.2% / -10.7% | +8.56 / +46.9% / -9.1% |
| 5 bp | +4.36 / +20.7% / -12.2% | +9.02 / +49.8% / -12.1% | +2.80 / +11.2% / -12.2% | +6.09 / +30.5% / -11.5% |
| 10 bp | +1.89 / +7.2% / -16.5% | +6.55 / +33.1% / -15.5% | +0.33 / -1.2% / -16.1% | +3.62 / +16.0% / -14.6% |
| 15 bp | -0.58 / -4.8% / -21.0% | +4.09 / +18.2% / -18.7% | -2.14 / -12.3% / -21.0% | +1.15 / +3.0% / -17.9% |
| 20 bp | -3.05 / -15.4% / -25.2% | +1.62 / +5.0% / -21.8% | -4.61 / -22.1% / -25.6% | -1.32 / -8.5% / -21.7% |

## H. Eligibility (M0 gate)

| | integrity | coverage >= 95% | mean > 0 | PF > 1 | MDD >= -35% | top1 <= 73.38% | result |
|---|---|---|---|---|---|---|---|
| R1 | PASS | 96.19% PASS | PASS | PASS | PASS | HIMS 18.4% PASS | **PASS** |
| R2 | PASS | 94.77% **FAIL** | PASS | PASS | PASS | HIMS 366.5% **FAIL** | FAIL |
| R3 | PASS | 94.57% **FAIL** | PASS | PASS | PASS | HIMS 33.4% PASS | FAIL |

R2's top1 share exceeds 100% because its total net contribution is nearly zero (+1.6%). The
share is well defined but not meaningful at that size.

## I. Improvement test (paired, 480 sessions, IID bootstrap 10,000, seed 20260921)

| | cumulative > R0 | mean delta | 95% CI of delta | P(delta <= 0) | result |
|---|---|---:|---|---:|---|
| **R1** | yes | **+4.66 bp** | [-2.35, +12.14] | **0.0974** | **PASS** (<= 0.10) |
| R2 | no | -1.56 bp | [-7.90, +4.59] | 0.688 | FAIL |
| R3 | yes | +1.73 bp | [-5.07, +8.44] | 0.305 | FAIL |

**Winner pool: {R1}. Winner: R1.** No tie rule was needed, and M0 defines no CAGR tie band.

**R1 passed narrowly.** P = 0.0974 against the 0.10 limit, and the two-sided 95% CI of the
delta includes 0. The frozen rule is a one-sided 90% test, and R1 clears it by 0.0026.

## J. Concentration (10 bp net contribution)

| | unique symbols | top1 | top5 | top10 | HHI | largest winner | largest loser |
|---|---:|---|---:|---:|---:|---|---|
| R0 | 214 | ABSI 73.4% | 260.5% | 374.8% | 0.0107 | ABSI +11.94% (06-24) | BTDR -9.93% (08-10) |
| R1 | 272 | **HIMS 18.4%** | **69.6%** | 121.4% | 0.0099 | BAND +12.34% (04-30) | INBX -7.76% (05-11) |
| R2 | 264 | HIMS 366.5% | 1348% | 2175% | 0.0101 | ABSI +11.94% | BTDR -9.93% |
| R3 | 261 | HIMS 33.4% | 120.7% | 195.5% | 0.0100 | ABSI +11.94% (06-24) | INBX -7.76% (05-11) |

R1 is far less concentrated than Base. The top1 share falls from 73% to 18%, the top five sum
to less than the total, and 272 distinct symbols appear. ABSI was not removed from any
calculation.

## K. Narrow versus broad coverage (diagnostic; not used to choose)

| | period | sessions | trades | session mean 10 bp | cumulative | PF | paired delta vs R0 |
|---|---|---:|---:|---:|---:|---:|---:|
| R0 | LEGACY_NARROW | 376 | 199 | -0.44 bp | -2.99% | 1.013 | - |
| R0 | BROAD | 104 | 286 | +10.32 bp | +10.49% | 1.188 | - |
| R1 | LEGACY_NARROW | 376 | 199 | -0.35 bp | -2.69% | 1.022 | +0.09 bp |
| R1 | BROAD | 104 | 281 | **+31.53 bp** | **+36.77%** | 1.576 | **+21.22 bp** |
| R2 | BROAD | 104 | 272 | +1.87 bp | +0.56% | 1.084 | -8.45 bp |
| R3 | BROAD | 104 | 271 | +16.40 bp | +17.22% | 1.306 | +6.08 bp |

Block means at 10 bp:

| | block 1 | block 2 | block 3 | block 4 |
|---|---:|---:|---:|---:|
| R0 | +5.75 | -3.40 | -4.31 | +9.52 |
| R1 | +4.65 | -2.77 | -4.31 | +28.65 |
| R2 | +5.40 | -3.40 | -4.31 | +3.63 |
| R3 | +5.34 | -2.77 | -4.31 | +16.22 |

All variants have 2 of 4 positive blocks. Almost all of R1's improvement lies in block 4, the
broad period. Under the E-D6 gate R1 would still read `INCONCLUSIVE — STATISTICAL` (CI [-3.08,
+16.64] bp, 2/4 blocks). M1's own gate does not use that label.

Stop condition B does not trigger: R1's MDD ratio to Base is 0.94, while its CAGR ratio is 4.4.

## L. Reproducibility

- Run 1 took 637 s; run 2 (`repeat-2`) was a separate process.
- Both runs give result digest `eac7dfd7…`. `REPEAT_CHECK.json` records `identical` and
  `artifacts_identical` as true, and all 9 artifacts are byte-equal. Both runs recorded HEAD
  `058217f`.
- In-process variant replays were also repeated and matched.

## M. Tests

The E-MAX suite has 50 tests:

- M0: 24;
- M1 phase A: 16 (orderings, tie, capacity, gates, winner, no R4/gap/weights, no performance);
- M1 replay mechanics: 3 (subset equivalence, selection change, not-selected rows);
- committed-result checks: 7 (checksum and repeat, protocol ordering, R0 = E-R3, invariants,
  bootstrap configuration, a winner recomputed from the stored numbers, frozen artifacts).

With the E-family suites, 411 tests pass.

## O. Final gate

```text
E-MAX-M1 PASS — R1 SELECTED
```

Under the frozen M0 rules, R1 (RVOL-strength ordering) is carried into M2 as the E-MAX ordering.

Read it with three caveats:

1. The improvement lives in the 104-session broad-coverage period.
2. The paired test passed by a margin of 0.0026 at the 0.10 level.
3. This is reused development data, labelled E-MAX DEVELOPMENT, not OOS.

No new ranking hypothesis may be added.

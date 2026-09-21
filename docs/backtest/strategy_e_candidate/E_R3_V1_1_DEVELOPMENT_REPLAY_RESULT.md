# Strategy E - E-R3 Trading V1.1 Development Replay Result

| | |
|---|---|
| Stage | `E-R3`, run under the frozen E-R2 protocol (`fa4258e`, rules `86b750ef…045e`) |
| Trading version | `STRATEGY_E_TRADING_V1_1` (E-R1 `0b932e6`) |
| Run id | `er3-e75ebe337b61` |
| Result | `strategy_e_v1_1_replay_result.json`, sha256 `2d50cca5cfe2ee4b…` (byte-identical on a second, independent run) |
| Runtime artifacts | `data/runtime/strategy_e/v1_1_replay_runs/er3-e75ebe337b61/` (gitignored) |
| Provider calls / writer lock | 0 / not taken |
| **Final gate** | **`E-R3 INCONCLUSIVE — STATISTICAL`** |

This is development evidence: E1 research and E-D6 have already seen this dataset. Nothing here
is OOS evidence or live approval. No rule, gate, cost, block or dataset was changed before or after
the result.

---

## A. Preflight / Integrity

- HEAD at run: `fa4258e` (branch `main`). E-D0..E-D5, horizon, the E-D6 protocol and result, E-R1
  `0b932e6` and E-R2 `fa4258e` are all ancestors.
- Every frozen artifact loaded through its own fail-closed loader: E-D0 `f1534f07…`, E-D2
  `d204b1da…`, horizon `f96f78f4…`, E-D3 `f6f45564…`, E-D4 `b2229fe5…`, E-D5 `2e1e796d…`, E-D6
  `73056ac8…`, V1.1 `b90573bd…`, forward context `78bb90b4…`, replay protocol `86b750ef…`.
- **Dataset.** The run read the E-D6 symlink view: tape `d12ff28a98cf9cc6…`, 2,152 files, 1,902
  symbols, 59 legacy parquet (`86cbb939…`), latest bound file date 2026-09-16, SPY present. Daily
  input was USB-HIST-V1 (freeze `9ebd6c29…`, read-set `7e790a8d…`), unchanged after the run. The
  view digest re-verified after the run. The timeline is 480 sessions, 2024-10-16..2026-09-16,
  equal to grid[21:]. No input is dated 2026-09-17 or later.
- **V1 baseline reproduction**, before any return: E1's unchanged code gave **70,738 universe rows
  and 1,729 H5 rows**, a MATCH.
- **Attribution**, before any return: every one of the 70,738 V1 rows is present in V1.1 with
  bit-identical sealed features and the same H5 flag. No origin-V1 row is missing from V1, and
  none is extra. Every added row is explained by correction A, correction B, or both. Integrity
  PASS.
- **PIT poison**, before any return. The sample was E-D6's deterministic 41 symbols (2,620
  symbol-sessions). In two modes, every bar after 09:24 ET of D (and every later day) was
  replaced with noise or deleted, which also removes the 09:30 bar. Separately, daily prices,
  volume and membership from D onward were overwritten. Across all 480 sessions, **0 seal
  digests moved**. Result: PASS.
- In-process replay was run twice with identical digests. Every session's `decision.execute`
  execution digest equals the frozen replay's.

## B. Structural Correction Impact (no returns)

| variant | universe rows | H5 candidates | selected | selected sessions | selection changed vs V1 |
|---|---:|---:|---:|---:|---:|
| V1 | 70,738 | 1,729 | 501 | 239 | 0 |
| A-only corrected (session-D daily open) | 70,738 | 1,729 | 501 | 239 | **0** |
| B-only corrected (09:30 bar) | 71,118 | 1,738 | 501 | 239 | **0** |
| V1.1 = A + B | 71,119 | 1,738 | 501 | 239 | **0** |

| origin | added rows | added H5 candidates |
|---|---:|---:|
| A only | 0 | 0 |
| B only | 380 | 9 |
| A and B both needed | 1 | 0 |

- **Correction A** has no row of its own in this dataset. The one row needing it also needed B.
- **Correction B** admits 380 rows and 9 H5 candidates. All 9 fall on crowded sessions, at
  canonical ranks 5 to 30 (KEYS 2026-04-30 #13, ECL 07-28 #17, LAD 07-29 #14, MTZ 07-30 #22, DLB
  07-31 #11, GMED 08-07 #19, JBL 08-12 #30, KEYS 08-25 #9, HEI.A 08-26 #5). None of them enters a
  top-three selection, so **no session's selected set changed**.

The V1 defect was real: it removed rows before H5 using post-09:25 information. On this
development sample it happened to change no decision.

## C. Candidate Funnel (V1.1)

| stage | count | % of previous | % of H5 |
|---|---:|---:|---:|
| PIT universe rows | 71,119 | | |
| H5 candidates | 1,738 | 2.44% | 100% |
| selected (max 3, symbol order) | 501 | 28.83% | 28.83% |
| capacity not selected | 1,237 | 71.17% | 71.17% |
| valid entries | 499 | 99.60% | 28.71% |
| ENTRY_INVALID | 2 (`NO_TRADE_SHORTENED_SESSION`) | 0.40% | |
| valid exact 09:34 exits | 485 | 97.19% | 27.91% |
| UNRESOLVED_EXIT | 14 (`NO_TRADE_MISSING_EXIT_BAR`) | 2.81% | |
| standard-PnL trades | 485 | 97.19% | 27.91% |

- ENTRY_INVALID: 2, in 2 sessions (both shortened sessions, as in E-D6). The rate is 0.40% of
  selected. **`NO_TRADE_MISSING_ENTRY_BAR` = 0**: no selected candidate lacked its 09:30 bar,
  because the only candidates V1.1 added were never selected.
- No-backfill affected sessions (a selected entry invalid while a not-selected candidate existed):
  **0**. The two shortened-session invalids had no waiting candidate.

## D. Trade Sample

485 standard trades, 237 active sessions out of 480, 214 unique symbols. Standard-PnL coverage
is 97.19% (485 / 499 valid entries), above the 95% minimum.

## E. Cost Results

Trade-level mean, median, win rate and PF; session-level cumulative, MDD, Sharpe and Sortino over
480 sessions, with no-trade sessions at 0 and sqrt(252) annualization.

| scenario | mean | median | win | PF | all-session mean | active-session mean | cumulative | MDD | Sharpe | Sortino |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 bp (diag.) | +16.82 bp | +3.75 bp | 52.2% | 1.330 | +6.83 bp | +13.83 bp | +35.8% | -9.1% | 1.15 | 1.79 |
| 5 bp | +11.82 bp | -1.25 bp | 49.5% | 1.221 | +4.36 bp | +8.83 bp | +20.7% | -12.2% | 0.73 | 1.11 |
| **10 bp (primary)** | **+6.82 bp** | **-6.25 bp** | **48.2%** | **1.122** | **+1.89 bp** | **+3.83 bp** | **+7.2%** | **-16.5%** | **0.32** | **0.47** |
| 15 bp | +1.82 bp | -11.25 bp | 45.8% | 1.031 | -0.58 bp | -1.17 bp | -4.8% | -21.0% | -0.10 | -0.14 |
| 20 bp | -3.18 bp | -16.25 bp | 43.9% | 0.948 | -3.05 bp | -6.17 bp | -15.4% | -25.2% | -0.51 | -0.72 |

## F. Primary 10 bp Gate

| gate | requirement | observed | result |
|---|---|---|---|
| 1 Integrity | checksums, provenance, prechecks, deterministic replay, no look-ahead | all pass | PASS |
| 2 Data quality | coverage >= 95% | 97.19% | PASS |
| 3 Net economics | mean session > 0 and PF > 1 | +1.89 bp, PF 1.122 | PASS |
| 4 Statistical support | bootstrap 95% CI low > 0 | [-6.71, +10.48] bp, P(mean <= 0) 33.0% | **FAIL** |
| 5 Chronological robustness | >= 3 of 4 blocks positive | 2 of 4 | **FAIL** |

Bootstrap: IID over 480 session returns, 10,000 replicates, seed 20260921, percentile 2.5/97.5.

## G. Stability

| block | range | sessions | active | trades | mean (10 bp) |
|---|---|---:|---:|---:|---:|
| 1 | 2024-10-16 .. 2025-04-09 | 120 | 44 | 66 | +5.75 bp |
| 2 | 2025-04-10 .. 2025-10-01 | 120 | 48 | 70 | -3.40 bp |
| 3 | 2025-10-02 .. 2026-03-25 | 120 | 37 | 48 | -4.31 bp |
| 4 | 2026-03-26 .. 2026-09-16 | 120 | 108 | 301 | +9.52 bp |

| quarter | sessions | active | trades | 0 bp | 10 bp | cumulative 10 bp |
|---|---:|---:|---:|---:|---:|---:|
| 2024Q4 | 53 | 26 | 41 | +2.31% | -0.32% | -0.32% |
| 2025Q1 | 60 | 16 | 21 | +8.24% | +6.53% | +6.19% |
| 2025Q2 | 62 | 30 | 38 | -2.41% | -5.29% | +0.57% |
| 2025Q3 | 64 | 19 | 34 | +5.04% | +3.07% | +3.65% |
| 2025Q4 | 64 | 18 | 23 | -5.44% | -7.13% | -3.74% |
| 2026Q1 | 61 | 21 | 29 | +1.95% | -0.17% | -3.91% |
| 2026Q2 | 62 | 53 | 147 | +19.09% | +12.96% | +8.54% |
| 2026Q3 | 54 | 54 | 152 | +4.23% | -1.25% | +7.19% |

Monthly (24 months, in `monthly.json`): 15 of 24 months are positive at 10 bp. June 2026 alone
contributes +16.53%. No month or quarter is excluded.

Price and liquidity buckets are identical to E-D6 (`buckets.json` is byte-identical). By D-1 price:
$5-10 +84.8 bp net (26 trades), $10-20 +17.2, $20-50 +8.7, $50-100 +12.5, $100-200 -4.7, $200+
-11.0. By D-1 dollar volume: $5-20M +72.5 (15), $20-100M +39.1 (80), $100-500M -11.8 (127),
$500M+ +2.3 (263). None becomes a filter.

## H. Concentration (diagnostic)

| basis | unique symbols | total | top1 | top5 | top10 | HHI (trade count) |
|---|---:|---:|---|---:|---:|---:|
| gross | 214 | +32.8% | ABSI 20.5% | 77.0% | 114.1% | 0.0107 |
| 10 bp | 214 | +9.1% | **ABSI 73.4%** | 260.5% | 374.8% | 0.0107 |

Top five: ABSI, HIMS, SOFI, UBER, INTC. Trades per symbol: mean 2.27, median 1, max 18. ABSI is
included, and no symbol-removed figure is computed.

## I. V1 vs V1.1 Implementation Delta

| item | Trading V1 (E-D6) | Trading V1.1 (E-R3) |
|---|---:|---:|
| universe rows | 70,738 | 71,119 |
| H5 candidates | 1,729 | 1,738 |
| selected / selected sessions | 501 / 239 | 501 / 239 |
| standard trades / active sessions | 485 / 237 | 485 / 237 |
| 10 bp mean session | +1.89 bp | +1.89 bp |
| 10 bp PF | 1.122 | 1.122 |
| bootstrap CI | [-6.71, +10.48] bp | [-6.71, +10.48] bp |
| positive blocks | 2/4 | 2/4 |
| coverage | 97.19% | 97.19% |
| ABSI top1 share (10 bp) | 73.4% | 73.4% |

The two replays select, enter and exit exactly the same 501 / 499 / 485 records. Compared against
E-D6's runtime `trades.csv`:

- the only differing field is `selection_rank` on 76 not-selected rows, shifted by the 9 added
  candidates;
- `daily_returns.csv` differs only in its `eligible` and `h5_candidates` counts;
- `cost_scenarios.json`, `monthly.json`, `quarterly.json`, `buckets.json` and
  `concentration.json` are byte-identical to E-D6's.

V1 was not re-run. Its figures come from the committed E-D6 result, and the two layers are not
pooled.

## Evidence Separation

**A. Research H5 evidence.** E1-H5 matched-control lift of +17.01 bp on development data. It is
observational and not a return.

**B. Trading V1 E-D6.** `E-D6 INCONCLUSIVE — STATISTICAL`. This is a forward-incompatible
historical result, preserved unchanged (result `f70f9196…`).

**C. Trading V1.1 E-R3.** `E-R3 INCONCLUSIVE — STATISTICAL`. This is the PIT-corrected development
replay. Its trade set equals B's on this dataset, but it is a different contract and a separate
layer.

The three are never added together or read as one sample.

## J. Reproducibility

- Run 1, `er3-e75ebe337b61`: result digest
  `2d50cca5cfe2ee4b284eb097c87afd2a51fd4b20c11dfa86e95752fa8a94b593`, 638 s.
- Run 2, `repeat-2`, a separate process: same digest, 658 s. `REPEAT_CHECK.json`:
  `identical = true`, `artifacts_identical = true`. All 11 artifacts compare byte-equal.

## K. Tests

`backend/tests/strategy_e_trading/test_e_r3_replay.py` has 26 tests: synthetic replay mechanics
plus identity checks of this committed result. They cover dataset identity, V1 reproduction,
superset identity, correction A and B attribution, no other drift, PIT poison, no backfill,
sizing and session portfolio, unresolved exit, coverage, bootstrap, blocks, the recomputed
verdict, concentration, the repeat digest, the output schema, and the frozen artifacts. H5,
entry, exit, cost and sizing identity are also covered by the E-R1 and E-R2 suites, which
still pass.

## M. Final Gate

```text
E-R3 INCONCLUSIVE — STATISTICAL
```

Economics clear 10 bp (mean session +1.89 bp, PF 1.12, coverage 97.2%). The statistical gate
fails (CI lower bound -6.71 bp) and so does the chronological gate (2 of 4 blocks). The PIT
correction removed a real look-ahead but changed no decision on this dataset, so the development
evidence for Trading V1.1 is the same as for V1. The result still leans on a right tail
concentrated in ABSI and 2026Q2.

Per the protocol, nothing is re-tuned. Trading V1.1 is **not** `READY FOR FORWARD` under this
gate. Whether to continue forward evidence collection is a separate decision.

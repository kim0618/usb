# Strategy E - E-R2 Trading V1.1 Development Replay Protocol

| | |
|---|---|
| Stage | `E-R2_PROTOCOL`, frozen before `E-R3` |
| Machine authority | `strategy_e_v1_1_replay_rules.json`, canonical `86b750ef…045e` |
| Loader | `app.strategy_e_v1_1.replay_protocol.load_rules` (fail-closed; computes nothing) |
| Tests | `backend/tests/strategy_e_trading/test_e_r2_replay_protocol.py` |
| Upstream | E-R1 `0b932e6` (Trading V1.1 `b90573bd…`, forward context `78bb90b4…`); E-D6 rules `73056ac8…` |
| Performance computed | **none**; no V1.1 replay has run, and no E-R3 runtime directory or result file exists |
| **Gate** | **`E-R2 PASS — V1.1 DEVELOPMENT REPLAY PROTOCOL FROZEN`** |

E-R3 may run only after this protocol's commit, and only as written here.

---

## 1. Question

With the PIT-corrected V1.1 universe and the frozen H5, selection, entry, exit, cost and sizing,
do economics and statistical stability survive a 10 bp round-trip cost on the same frozen
development dataset E-D6 used?

This is development evidence. The dataset was already seen by E1 research and by E-D6, so no
E-R3 outcome is OOS or forward evidence.

## 2. Dataset binding: identical to E-D6

| item | value | verified in E-R2 |
|---|---|---|
| minute manifest | `strategy_e_d6_development_tape_v1.json` | loads, digest recomputed |
| minute tape digest | `d12ff28a98cf9cc6…22a4` | yes; the live view `data/runtime/strategy_e/tape_view/d12ff28a98cf9cc6` still reproduces it (`verify_view = True`) |
| files / symbols | 2,152 / 1,902 | yes |
| legacy minute parquet | 59, digest `86cbb939…` (checked against the USB-HIST-V1 manifest) | 59 links in the view |
| boundary evidence | the next 99 files hash to the E1-H5 confirmation binding `4f96e042…` | carried from E-D6 |
| daily | `USB-HIST-V1`, freeze `9ebd6c29…`, read-set `7e790a8d…`, grid 2024-09-17..2026-09-16 (501) | equal to the E-D6 result |
| latest date in any bound file name | 2026-09-16 | yes |

The replay reads through the E-D6 symlink view only. Globbing the live store is forbidden: 104
pages have been added to development folders since the binding, and forward data will keep
arriving.

Split exclusion for session D is `F(D) != F(D-1)` over the USB-HIST-V1 split list, the same events
E0 used. It reads split execution dates, never D's daily bar. SPY comes from its pages inside the
view (premarket only) and from the USB-HIST-V1 grouped files.

## 3. Forward holdout exclusion

No session on or after **2026-09-17** may enter any input, frame, trade or statistic. Excluded by
name: `market_data/forward/**`, grouped daily after 2026-09-16, `splits_asof_*`, forward minute
pages, any RVOL history extension collected after E-R1, and any page outside the manifest. The run
refuses to start if a bound file, grid session or evaluation session is dated 2026-09-17 or later.

## 4. Frozen strategy

| layer | V1.1 |
|---|---|
| universe | `STRATEGY_E_TRADING_UNIVERSE_V1_1`: E0 D-1 rules without the session-D open test; E1 premarket rules without the 09:30 test; tape truncated at 09:24 ET of D |
| H5 | `premarket_gap > 0 AND premarket_rvol >= 3 AND position_in_premarket_range >= 0.8 AND return_0900_0925 > 0` via `evaluate_h5_signal` |
| decision | `decision.seal` (09:25), then `decision.execute`, one seal per session |
| selection | max 3, canonical symbol ascending, replacement NONE |
| entry | exact 09:30 open; missing or invalid means `NO_TRADE_*` and the slot stays empty |
| exit | exact 09:34 close, fallback NONE |
| cost | GROSS_0BP (diagnostic), 5, 10, 15, 20 bp; **primary COST_10BP** |
| sizing | `EQUAL_WEIGHT_EXECUTABLE_V1` |
| stop / target / partial | NONE |

E-R3 may call `universe.premarket_row` for each (symbol, session). It may use a faster batch
equivalent only if the run shows that equivalent equal to `premarket_row` on every row.

## 5. Evaluation timeline and blocks (fixed by the calendar, not by outcomes)

All 480 XNYS sessions from 2024-10-16 (grid index 21, the first session whose D-1 row is E0's
first eligible index 20) to 2026-09-16. No-trade sessions count as 0. This is identical to E-D6's
timeline.

The four chronological blocks come from `numpy.array_split` over those sessions, the same rule
`metrics.chronological_blocks` applies:

| block | sessions |
|---|---|
| 1 | 2024-10-16 .. 2025-04-09 |
| 2 | 2025-04-10 .. 2025-10-01 |
| 3 | 2025-10-02 .. 2026-03-25 |
| 4 | 2026-03-26 .. 2026-09-16 |

## 6. Gate: E-D6, verbatim

The gate, data-quality, statistics and chronological sections are copied **string for string**
from `strategy_e_backtest_rules_v1.json` (the tests compare them). E-R3 evaluates them with the
unchanged `app.backtest.strategy_e_d6.metrics` functions.

| gate | requirement |
|---|---|
| integrity | frozen checksums, provenance, the prechecks in section 7, deterministic replay, no look-ahead |
| data quality | standard-PnL coverage (standard trades / valid entries) >= 95% |
| economics | COST_10BP mean all-session portfolio return > 0 and trade PF > 1.0 |
| statistics | session bootstrap 95% CI lower bound > 0 (IID over session returns, 10,000 replicates, seed 20260921, percentile 2.5/97.5) |
| chronology | positive COST_10BP mean in >= 3 of 4 blocks |

Precedence `INTEGRITY_BLOCK > DATA_QUALITY > ECONOMIC_FAIL > STATISTICAL_INCONCLUSIVE > PASS`.

| E-D6 verdict function output | E-R3 label |
|---|---|
| `E-D6 BLOCKED — INTEGRITY` | `E-R3 BLOCKED — INTEGRITY` |
| `E-D6 INCONCLUSIVE — DATA QUALITY` | `E-R3 INCONCLUSIVE — DATA QUALITY` |
| `E-D6 FAIL` | `E-R3 FAIL` |
| `E-D6 INCONCLUSIVE — STATISTICAL` | `E-R3 INCONCLUSIVE — STATISTICAL` |
| `E-D6 PASS — READY FOR PAPER / FORWARD` | `E-R3 PASS — TRADING V1.1 READY FOR FORWARD` |

**Known property, stated before the result.** The coverage denominator is *valid entries*, as in
E-D6. A selected symbol whose 09:30 entry is invalid (`ENTRY_INVALID`, newly possible in V1.1)
therefore lowers the trade count and not the coverage. It is reported in the funnel and is not
added to any gate, because this protocol creates no new criterion.

## 7. Prechecks, all before the first return

1. E-R1 and the E-D0..E-D6 commits are ancestors of HEAD, and this protocol's commit precedes the
   run.
2. Every upstream rules file loads through its own fail-closed loader, and this file's digest
   matches.
3. The manifest, the view (before and after), the file and symbol counts and the legacy digest
   equal section 2.
4. The daily freeze and read-set digests equal section 2.
5. No input is dated 2026-09-17 or later.
6. **V1 reproduction:** E1's unchanged code over the view reproduces 70,738 universe rows and
   1,729 H5 rows.
7. **V1.1 superset:** every V1 row is present with bit-identical sealed features and the same H5
   flag.
8. **Attribution:** every V1.1 row absent from V1 is explained only by removing test A (session-D
   daily open), test B (09:30 bar), or both. Any other difference is an integrity failure.
9. The timeline has exactly 480 sessions, 2024-10-16..2026-09-16.
10. **PIT poison:** replacing every bar after 09:24 ET of D with noise, and dropping later days,
    leaves every session's seal digest unchanged. This uses a deterministic 40-symbol sample, as
    E-D6 did.

## 8. Structural delta (diagnostic, computed from sealed decisions, no returns)

| variant | definition |
|---|---|
| V1 | E1/E-D6 universe (both post-09:25 tests present) |
| A_only | V1 with the session-D daily-open test removed |
| B_only | V1 with the 09:30 minute-bar test removed |
| V1.1 | both removed |

Reported for each variant: universe rows, H5 candidates, selected count, and sessions whose
selected set differs from V1. Also reported: rows added by A only, by B only, and only when both
are removed; the V1.1 missing-09:30 entry count; no-backfill-affected sessions (a selected entry
was invalid while a not-selected candidate existed); and valid standard trades, V1 vs V1.1.

No return is split by A/B subgroup. The delta is never used to choose, revert or tune a rule.

## 9. Funnel, costs, diagnostics

- **Funnel:** PIT universe rows, H5 candidates, selected, capacity not selected, valid entries,
  `ENTRY_INVALID` with reasons, valid exact exits, `UNRESOLVED_EXIT` with reasons, standard-PnL
  trades, coverage. Count and percentage at each stage.
- **Costs:** all five scenarios, reported with E-D6's metrics. Only COST_10BP enters the gate.
- **Diagnostics:** E-D6's diagnostics section, unchanged (price and liquidity buckets, break-even,
  month and quarter tables, concentration: unique symbols, top1/5/10, HHI, trades per symbol).
- **Exclusions:** none. ABSI stays in, and no symbol-removed recomputation is a primary output.

## 10. Three evidence layers, never pooled

| layer | status |
|---|---|
| Research H5 | matched-control lift (observational), not a return |
| Trading V1 E-D6 | forward-incompatible historical result, `E-D6 INCONCLUSIVE — STATISTICAL`, preserved |
| Trading V1.1 E-R3 | PIT-corrected development result |

The three are reported side by side. They are never pooled, chained, or treated as one sample.

## 11. Outputs and reproducibility

- Runtime (gitignored): `data/runtime/strategy_e/v1_1_replay_runs/<run_id>/`, where `run_id =
  er3-<12 hex of the identity digest>`. Contents: `result.json`, `trades.csv`,
  `daily_returns.csv`, `funnel.json`, `structural_delta.json`, `cost_scenarios.json`,
  `monthly.json`, `quarterly.json`, `buckets.json`, `concentration.json`.
- Committed: `E_R3_V1_1_DEVELOPMENT_REPLAY_RESULT.md`, `strategy_e_r3_result_v1.json`,
  `strategy_e_r3_result_v1.sha256`.
- `result.json` sections: identity, dataset, prechecks, funnel, structural_delta, scenarios,
  primary_gate, stability, buckets, concentration, break_even, evidence_layers, verdict.
- Trade columns: E-D6's `TRADE_COLUMNS` plus `universe_origin` (V1, A, B, AB).
- Two runs are required, with an identical result digest and byte-identical runtime files.

## 12. Prohibitions (inside E-R3, before and after the result)

No change to H5, the universe, symbol membership (ABSI included), position count, ranking, entry,
exit, cost, or the primary scenario. No stop, target, partial exit, candidate replacement,
invalid-entry backfill, period removal, or price/liquidity filter. No re-run with any change after
a result is seen.

## 13. Tests (`test_e_r2_replay_protocol.py`, 14; no performance)

| # | requirement | test |
|---|---|---|
| 1 | dataset binding identity | `test_development_dataset_binding_is_the_e_d6_binding` |
| 2 | forward holdout exclusion | `test_forward_holdout_is_excluded` |
| 3 | V1.1 digest linkage | `test_v1_1_artifacts_link_to_the_e_r1_commit` |
| 4 | H5 unchanged | `test_h5_is_unchanged` |
| 5 | max-3 unchanged | `test_max3_canonical_selection_is_unchanged` |
| 6 | no backfill | `test_no_backfill` |
| 7 | entry/exit unchanged | `test_entry_and_exit_are_unchanged` |
| 8 | cost grid unchanged | `test_cost_grid_is_unchanged` |
| 9 | primary 10 bp | `test_primary_is_10bp` |
| 10 | E-D6 gate identity | `test_e_d6_gate_is_reused_verbatim` |
| 11 | bootstrap config identity | `test_bootstrap_config_is_e_d6s` |
| 12 | block config identity | `test_chronological_block_config_is_e_d6s` |
| 13 | no performance invocation | `test_protocol_loads_without_any_performance_entry_point` (replay, entry, exit, sizing, metrics and verdict functions patched to raise; no E-R3 runtime or result file exists) |
| - | checksum | `test_protocol_checksum_file` |

## 14. Gate

None of the BLOCK conditions applies:

- the dataset is reconstructable, and the view reproduces the bound digest;
- the E-D6 gate is reused verbatim;
- V1.1 provenance links to `0b932e6` byte for byte;
- the forward holdout is separable by date;
- no V1.1 performance result exists anywhere.

```text
E-R2 PASS — V1.1 DEVELOPMENT REPLAY PROTOCOL FROZEN
```

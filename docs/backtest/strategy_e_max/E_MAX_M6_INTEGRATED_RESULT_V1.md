# Strategy E-MAX - M6 Integrated Frozen Replay Result V1

| | |
|---|---|
| Verdict | **E-MAX-M6 PASS — E-MAX V1 DEVELOPMENT CANDIDATE FROZEN** |
| Strategy | `STRATEGY_E_MAX_V1` = R1 + max 3 + X1 + B2 + 2.0x |
| Run | `em6-92dbfc07dccf`, result digest `2f052e91…b9fd`, repeat-2 identical |
| Protocol | `E_MAX_M6_INTEGRATED_PROTOCOL_V1.md`, commit `dedae54`, rules canonical `b30a3e95…9d0e` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |
| Live status | **HISTORICAL DEVELOPMENT CANDIDATE / NOT LIVE-LEVERAGE APPROVED** |
| Execution cost sensitivity | **HIGH** (15 bp MDD -35.14% is past the -35% ceiling) |

PASS means only that E-MAX V1 is frozen as a development candidate. It is not OOS, forward, paper,
live or 3x-leverage approval.

## 1. Integrity and identity

- **Prechecks.** The frozen chain (E-D0 to M6 protocol, 24 commits) is an ancestor of HEAD. The
  binding holds: tape `d12ff28a…`, 2,152 files, 1,902 symbols, 480 sessions from 2024-10-16 to
  2026-09-16. V1 reproduces (70,738 / 1,729). V1.1 attribution PASS (universe 71,119). PIT poison
  moved 0 seals. The tape and daily inputs are unchanged after the run.
- **Stage identities.** Replayed from the source frames, with no stage file read as input:

| Stage | Content | Result |
|---|---|---|
| S1 | R1 + max 3 + X1 at 1.0x | = M1 R1 trades and daily |
| S2 | + B2 | = M3 B2 / M4 X1 / M5 E1 trades and daily; exposure map = M3 |
| S3 | + global 2.0x (E-MAX V1) | = M5 E20 trades `1a5c2e3b…` and daily `7da259f5…` |

- **M5 metric identity.** All ten sections (evaluation, cagr, calmar, risk, tail, blocks, calendar,
  breadth_interaction, extremes, catastrophic_sessions) equal the committed M5 `variants.E20`
  exactly. There is no discrepancy to explain.
- **Independent checks.** Every check passed. Session returns recomputed from the records agree to
  a maximum absolute error of 1e-28. All weights are equal at final exposure / n. Every active
  session has exposure 2 or 3. The cost drag equals exposure x cost exactly. The selection is always
  the R1 prefix, and no unselected candidate traded. High breadth falls on exactly the B2 sessions.
- **Determinism.** The in-process replay repeated identically, and the second run gave the same
  result digest with byte-identical artifacts.

## 2. Funnel

| Step | Count |
|---|---:|
| 09:25 universe rows | 71,119 |
| H5 candidates | 1,738 |
| Selected (R1, max 3) | 501 |
| Capacity skipped (no backfill) | 1,237 |
| Valid entries | 499 (ENTRY_INVALID 2, shortened session) |
| Valid exact 09:34 exits | 480 (UNRESOLVED_EXIT 19, missing bar) |
| Standard trades | 480; coverage 96.19% |
| Active sessions | 237 of 480 |
| High-breadth sessions | 26 (all active) |

## 3. Primary 10 bp and cost sensitivity

| Cost | CAGR | Cumulative | MDD | Calmar | Sharpe | PF (trade) | Session mean | MDD >= -35% |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 0 bp | 79.41% | +204.46% | -19.03% | 4.17 | 1.75 | 1.589 | +25.95 bp | yes |
| 5 bp | 57.43% | +137.36% | -24.13% | 2.38 | 1.40 | 1.473 | +20.74 bp | yes |
| **10 bp (primary)** | **38.12%** | **+85.00%** | **-29.85%** | **1.28** | **1.05** | **1.367** | **+15.53 bp** | **yes** |
| 15 bp (stress warning) | 21.17% | +44.15% | **-35.14%** | 0.60 | 0.70 | 1.270 | +10.32 bp | **no** |
| 20 bp (severe stress) | 6.27% | +12.29% | **-40.04%** | 0.16 | 0.35 | 1.180 | +5.11 bp | **no** |

The 10 bp bootstrap CI for the session mean is [-5.11, +36.99] bp, with P(mean <= 0) = 0.069. This
is E1's edge scaled by 2x, not new evidence.

**EXECUTION COST SENSITIVITY = HIGH.** Only 5 bp of extra round-trip cost is enough to break the
M0 risk ceiling. This does not change the verdict, which follows the M0 10 bp contract, but a live
or paper plan has to show that actual round-trip costs, including spread, stay near 10 bp.

## 4. Gate

| Condition | Value | Pass |
|---|---|---|
| Integrity / reproducibility / M5 identity | all PASS | yes |
| Coverage >= 95% | 96.19% | yes |
| 10 bp session mean > 0 | +15.53 bp | yes |
| 10 bp PF > 1 | 1.367 | yes |
| 10 bp MDD >= -35% | -29.85% | yes |
| Top1 symbol share <= 73.38% | 15.55% (HIMS) | yes |
| No catastrophic session | worst session -14.12% | yes |
| Stop A / C / D | not triggered | yes |
| Stop B (authoritative, vs E-Base) | CAGR ratio 10.27 > MDD ratio 1.81 | yes |

**Stop B.** The authoritative test is the M0 literal against E-Base: CAGR 3.7123% and MDD -16.4513%,
read exactly from the E-R3 result. The CAGR ratio is 10.27 against an MDD ratio of 1.81, so it is not
flagged. Against the M5 comparator E1 (1.0x, CAGR 19.54%, MDD -15.45%), which is a diagnostic and
decides nothing, the ratios are 1.951 and 1.932, a margin of only **0.019**. Doubling exposure
roughly doubled drawdown along with growth; it did not make risk efficiency any better.

## 5. Risk, drawdown and recovery

| | |
|---|---|
| MDD (10 bp) | -29.85% |
| Peak | 2025-03-13 (equity flat to 2025-03-28 on no-trade sessions) |
| Trough | 2026-02-13 (221 sessions after the last peak session) |
| Recovery | 2026-05-08 (58 sessions after the trough) |
| Underwater, peak to recovery | 278 sessions; the longest underwater run in the sample |
| Worst session / week / month / quarter | -14.12% (2026-06-29) / -16.56% (2026-W27) / -16.00% (2025-10) / -14.12% (2025Q4) |
| Longest losing run | 4 sessions |

The whole MDD falls inside the legacy period. For more than a year the strategy sat below its
March 2025 high.

## 6. Tail dependence and concentration

| | Share of summed 10 bp session returns |
|---|---:|
| Top 1 session (2026-08-04) | 20.2% |
| Top 3 sessions (+ 2026-04-30, 2026-07-28) | 54.8% |
| Top 5 sessions (+ 2026-06-24, 2025-06-03) | 84.6% |

On symbols: top1 is HIMS at 15.5% of the net contribution, the top 5 (HIMS, BLZE, ITRI, SOFI, FVRR)
make 66.1%, and the top 10 make 115.4% (the rest is net negative). There are 272 symbols, and the
trade-count HHI is 0.0099.

**Limitation.** Five sessions account for about 85% of the summed return. The E-MAX V1 result rests
on a handful of extreme opening sessions.

## 7. Breadth dependence

| | Sessions | Trades | Summed 10 bp return | Share |
|---|---:|---:|---:|---:|
| High breadth (3.0x) | 26 | 73 | +34.85% | 46.8% |
| Normal (2.0x) | 454 (211 active) | 407 | +39.69% | 53.2% |

Just 26 sessions, 5.4% of the sample, produce 47% of the return, and all 26 are in 2026 (block 4).

## 8. Legacy versus broad coverage

| Period | Sessions | Trades | Cumulative | Session mean | MDD | Share of summed return |
|---|---:|---:|---:|---:|---:|---:|
| LEGACY_NARROW (2024-10-16..2026-04-17) | 376 | 199 | **-7.93%** | -0.71 bp | -29.85% | -3.6% |
| BROAD_COVERAGE (2026-04-20..2026-09-16) | 104 | 281 | **+100.94%** | +74.24 bp | -20.51% | +103.6% |

**All of the improvement comes from the broad-coverage period.** Over 376 legacy sessions (a
30-symbol tape) E-MAX V1 lost money after 10 bp, and the MDD came from there. The positive result
rests entirely on the last 104 sessions, one coverage regime.

## 9. Chronological blocks (E-D6 four blocks)

| Block | Period | Trades | Mean | Compounded | Block MDD |
|---|---|---:|---:|---:|---:|
| 1 | 2024-10-16..2025-04-09 | 66 | +9.30 bp | +10.34% | -9.06% |
| 2 | 2025-04-10..2025-10-01 | 70 | -5.54 bp | -8.54% | -16.19% |
| 3 | 2025-10-02..2026-03-25 | 48 | -8.63 bp | -11.42% | -19.81% |
| 4 | 2026-03-26..2026-09-16 | 296 | +66.99 bp | +106.97% | -20.51% |

Only **2 of 4** blocks are positive.

## 10. Months and quarters

- **Months:** 18 positive, 6 negative. The best is 2026-08 (+25.92%), the worst 2025-10 (-16.00%).
- **Quarters:** 5 positive, 3 negative. The best is 2026Q3 (+52.90%), the worst 2025Q4 (-14.12%).
- **2026Q2:** +36.18% compounded, 47.3% of the summed return. **2026Q3:** +52.90%, 61.3%. The two
  quarters together are 108.6% of the summed return; the six quarters before them net to a loss.

## 11. Financing and live feasibility

High-breadth sessions need **3.0x gross exposure**. The model has no margin interest, broker margin
rules, buying power, forced liquidation, real slippage, NBBO spread or market impact. Common Risk
V1 allows no leverage. E-MAX V1 is a **HISTORICAL DEVELOPMENT CANDIDATE / NOT LIVE-LEVERAGE
APPROVED**.

## 12. Final E-MAX V1 limitations

1. The gain depends on one regime (broad coverage, the last 104 sessions); the legacy period
   loses money.
2. The top 5 sessions are 85% of the summed return, and the high-breadth 26 sessions are 47%.
3. Only 2 of 4 blocks are positive, and the 10 bp bootstrap CI includes 0 (P = 0.069).
4. Cost sensitivity is high: the MDD ceiling breaks at 15 bp.
5. Stop B against E1 has a margin of 0.019, so leverage did not improve risk efficiency.
6. Financing and broker leverage are not modelled.

## 13. What comes next

Development exploration is closed. On this data there will be no 2.1x or 1.9x, no new breadth
threshold, ranking, exit or position cap, no symbol exclusion and no cost relaxation. The next
evidence must be forward / shadow data or new, untouched historical data; which comes first is
decided after this result.

## 14. Artifacts

Runtime (gitignored): `data/runtime/strategy_e_max/m6_runs/em6-92dbfc07dccf/` holds the S1, S2 and
V1 trades and daily CSVs, the S2 exposure map, `result.json` and `repeat-2/`. Committed:
`strategy_e_max_v1_result.json` (= runtime result.json) and `strategy_e_max_v1_result.sha256`.

# Strategy E-MAX - M6 Integrated Frozen Replay Protocol V1

| | |
|---|---|
| Stage | `E-MAX-M6`, phase A (protocol), frozen before the integrated replay |
| Strategy | `STRATEGY_E_MAX_V1` |
| Machine authority | `strategy_e_max_v1_rules.json`, canonical `b30a3e95…9d0e` |
| Upstream | M0 `b05bbf6` · M1 R1 `d5bdc6b` · M2 C1 (max 3) `9dc80b1` · M3 B2 `7312f45` · M4 X1 `0c74c22` · M5 E20 `9d4b1f1` (M6 AUTHORIZED) |
| Code | `app.strategy_e_max.v1` (loader, composition, gate, verdict) |
| Tests | `backend/tests/strategy_e_max/test_e_max_m6_protocol.py` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |

## 1. Purpose

M6 improves nothing. It freezes the configuration that survived M0 to M5 as one strategy, replays it
from the frozen source rules in one pass, and confirms its identity, performance, risk and
provenance. It declares no ranking, capacity, breadth, exit or exposure variant.

The M5 numbers for this configuration are already published. Every gate in M6 is copied from M0 or
M5; none was set or moved knowing the M6 replay.

## 2. E-MAX V1 definition

| Layer | Frozen rule | Source |
|---|---|---|
| Universe | Trading V1.1 PIT universe, sealed 09:25 ET | E-R1 / E-R2 |
| Alpha | H5: `premarket_gap > 0`, `premarket_rvol >= 3.0`, `position_in_premarket_range >= 0.8`, `return_0900_0925 > 0` | E-D0 |
| Ranking | R1: `premarket_rvol` descending, ties by symbol ascending | M1 |
| Capacity | max 3 selected, no replacement, candidate #4 never backfills | M2 (C1 kept) |
| Entry | exact 09:30 ET bar open, no fallback (`ENTRY_INVALID`) | E-D2 |
| Exit | X1: exact 09:34 ET bar close, no fallback (`UNRESOLVED_EXIT`); no X2 continuation | E-D3 / M4 |
| Breadth | B2: 1.5x when universe rows >= 100 and `h5_rate >= 0.030741`, else 1.0x | M3 |
| Global | E20: 2.0x | M5 |
| Final session exposure | 2.0x normal, 3.0x high breadth (global x breadth) | M5 |
| Sizing | equal weight over executable positions: final exposure / n | E-D5 / M5 |
| Cost | 10 bp round trip primary; 0 / 5 / 15 / 20 bp reported; levered net = exposure x (gross - cost) | E-D4 / M5 |

Execution goes through `app.strategy_e_max.capacity.execute(capacity=3)` (E-D2 entry, E-D3 exit,
E-D5 classification, E-D4 costs, all unmodified). The multipliers are applied in the M5 order,
`breadth.scale` first and `exposure.apply_global` second, so the Decimal arithmetic matches M5.

## 3. Dataset

The E-R2 to M5 development binding, unchanged: tape `d12ff28a…`, 2,152 files, 1,902 symbols,
480 sessions from 2024-10-16 to 2026-09-16. Sessions from 2026-09-17 on are excluded. The runner
reads the bound symlink view only (no glob of the live store), and `prepare` re-runs the E-R3
prechecks (chain ancestry, binding, V1 reproduction, attribution, PIT poison) before any return.

## 4. Pipeline and identities

`app.dev.run_strategy_e_max_m6` replays every stage from the prepared source frames. No stage result
file is read back as an input; committed files are used only as comparison targets.

| Stage | Content | Must hash to |
|---|---|---|
| S1 | R1 + max 3 + X1 at 1.0x | M1 R1 trades `95d03874…`, daily `9d39d01b…` |
| S2 | S1 + B2 | M3 B2 / M4 X1 / M5 E1 trades `fee44b84…`, daily `97f9d50c…`, exposure map `d09ec86f…` |
| S3 | S2 + global 2.0x (E-MAX V1) | M5 E20 trades `1a5c2e3b…`, daily `7da259f5…` |

**M5 metric identity.** The sections `evaluation`, `cagr`, `calmar`, `risk`, `tail`, `blocks`,
`calendar`, `breadth_interaction`, `extremes` and `catastrophic_sessions` of the M6 replay must,
after the canonical JSON round trip, equal `variants.E20` in the committed M5 result exactly. The
committed JSON is the reference, never rounded constants. A difference is analysed; if it cannot be
explained, M6 is BLOCKED.

**Independent checks** (on the replayed records, not on M5):

- each session return equals the sum of Decimal(final weight) x record net return over SIZED
  records, within 1e-18;
- every executable record in a session has the same weight, final exposure / n;
- every active session has exposure exactly 2 or 3;
- cost scales with notional: (gross - c) session return = exposure x c_bp / 10,000, within 1e-18;
- the selected set is always the first min(3, H5) of the R1 order, and no unselected candidate is
  executed;
- high breadth falls exactly on the sessions meeting the B2 rule.

## 5. Final gate

| Condition | Value | Source |
|---|---|---|
| Integrity | prechecks, stage identities, independent checks, in-process determinism, inputs unchanged | M0 / M5 |
| Reproducibility | two runs, identical result digest and artifacts | M0 |
| M5 integrated identity | section 4 | M6 |
| Coverage | >= 95% | M0 |
| 10 bp session mean | > 0 | M0 |
| 10 bp profit factor | > 1 | M0 |
| 10 bp MDD | >= -35% | M0 |
| Concentration | top1 symbol share <= 0.7338 | M0 (stop C) |
| Catastrophic session | none <= -100% | M5 |
| Stop A | M1-M4 not all comparator | M0 |
| Stop B | not flagged (below) | M0 |
| Stop D | not every candidate of M5 and M6 below -35% | M0 |

**Stop B (decisive at M6).** The M0 text is used as written: flagged when
|MDD_10bp| / |MDD_10bp of E-Base| > CAGR_10bp / CAGR_10bp of E-Base. E-Base values come from the
committed E-R3 result (COST_10BP cumulative +7.1896%, CAGR 3.7123% by the M0 formula, MDD
-16.4513%). If flagged, M6 cannot pass. The same ratio against the M5 comparator E1 (the M2-M5
convention) is reported with its margin but decides nothing; at M5 that margin was below 0.02.

**Verdict** (exactly one):

- `E-MAX-M6 BLOCKED — INTEGRITY`: any integrity, stage-identity, M5-identity or determinism failure;
- `E-MAX-M6 FAIL`: otherwise, any economic gate, the MDD ceiling, the concentration guard or an
  authoritative stop condition fails;
- `E-MAX-M6 PASS — E-MAX V1 DEVELOPMENT CANDIDATE FROZEN`: otherwise.

## 6. Cost risk classification (decides nothing)

10 bp is the primary modelled case, 15 bp the stress warning, 20 bp the severe stress. When the
15 bp MDD is below -35%, the result states `EXECUTION COST SENSITIVITY = HIGH` (`ELEVATED` when only
20 bp is, `LOW` otherwise). The primary verdict stays the M0 10 bp contract.

## 7. Diagnostics (decide nothing)

Funnel; the 0 / 5 / 10 / 15 / 20 bp table (CAGR, cumulative, MDD, Calmar, Sharpe, PF, session
mean); tail dependence (top 1 / 3 / 5 sessions, top 1 / 5 / 10 symbols, HHI); breadth dependence
(high-breadth and normal sessions, trades and PnL share); legacy (2024-10-16..2026-04-17) versus
broad coverage (2026-04-20..2026-09-16) with cumulative, mean and MDD; the four E-D6 blocks;
months and quarters including 2026Q2 and 2026Q3; drawdown (peak, trough, recovery, sessions to
recovery, longest underwater).

## 8. Meaning and limits

PASS means **E-MAX V1 DEVELOPMENT CANDIDATE FROZEN**. It does not mean OOS validated, forward
validated, paper approved, live approved or 3x leverage approved.

Margin interest, broker margin rules, buying power, forced liquidation, real slippage, NBBO spread
and market impact are not modelled. High-breadth sessions need 3.0x gross exposure and Common Risk V1
allows no leverage. The label is **HISTORICAL DEVELOPMENT CANDIDATE / NOT LIVE-LEVERAGE APPROVED**.

## 9. End of development exploration

After M6, nothing more is optimised on this development data: no 2.1x, 1.9x or other multiplier, no
new breadth threshold, ranking, exit or position cap, no symbol exclusion, no cost relaxation. The
next evidence is forward / shadow data or new untouched historical data, chosen after M6.

## 10. Reproducibility and outputs

The replay runs twice; both runs must give the same result digest and byte-identical artifacts.

- Runtime: `data/runtime/strategy_e_max/m6_runs/<run_id>/` (gitignored).
- Committed: `E_MAX_M6_INTEGRATED_RESULT_V1.md`, `strategy_e_max_v1_result.json`,
  `strategy_e_max_v1_result.sha256`.

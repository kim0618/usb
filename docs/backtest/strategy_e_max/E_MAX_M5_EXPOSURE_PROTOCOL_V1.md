# Strategy E-MAX - M5 Exposure Scaling Protocol V1

| | |
|---|---|
| Stage | `E-MAX-M5`, phase A (protocol), frozen before any M5 performance |
| Machine authority | `strategy_e_max_m5_rules_v1.json`, canonical `b2a89a71…3647` |
| Upstream | M0 `b05bbf6` · M1 R1 · M2 max 3 · M3 B2 · M4 `362b978` / `0c74c22` (X1 kept, M5 AUTHORIZED) |
| Code | `app.strategy_e_max.exposure` (global multiplier), `app.strategy_e_max.m5` (loader, winner, M6) |
| Tests | `backend/tests/strategy_e_max/test_e_max_m5_protocol.py` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |

## 1. Configuration and candidates

The configuration is the one M4 carried forward: R1 ordering, max 3, B2 breadth exposure, and X1
(the exact 09:30 open to the exact 09:34 close). The final session exposure is the global
multiplier times the B2 breadth multiplier.

| candidate | M0 id | global | normal session | high-breadth session |
|---|---|---:|---:|---:|
| E1 (comparator) | L1 | 1.0x | 1.0x | 1.5x |
| E15 | L2 | 1.5x | 1.5x | 2.25x |
| E20 | L3 | 2.0x | 2.0x | 3.0x |

Weights stay equal across executable positions and are scaled. For example, E20 on a normal
session with 3 positions gives 2/3 each; E20 on a high-breadth session with 3 positions gives 1
each; E15 on a high-breadth session with 2 positions gives 9/8 each.

**Forbidden:**

- any other multiplier (1.25, 1.75, 2.5, 3.0 and so on);
- unequal or alpha-weighted sizing;
- changes to R1, capacity, B2, entry, exit or cost;
- clipping any loss.

## 2. Accounting

- **Cost.** Levered net = exposure × (gross − round-trip cost). The net return of each position is
  what gets scaled.
- **Equity.** Equity compounds as equity_t = equity_(t−1) × (1 + r_t) over all 480 sessions. MDD
  is recomputed from the levered, compounded series; it is never Base MDD × multiplier.
- **Catastrophic loss.** A candidate with any 10 bp session return of −100% or worse (equity at or
  below 0) is ineligible (`CATASTROPHIC_SESSION_LOSS`). Losses are not clipped.
- **Concentration.** It is computed on levered PnL. A global scalar leaves the shares equal to
  E1's, because B2 already makes session exposure non-uniform in the same way for every candidate.
- **Financing is not modelled.** `FINANCING_COST = NOT MODELED` and
  `BROKER_LEVERAGE_FEASIBILITY = NOT VALIDATED`. The 3.0x high-breadth exposure in E20 is for
  research only. Common Risk V1 allows no leverage.

## 3. Integrity (checked before E15 / E20 are read)

- The E-R3 prechecks are re-run.
- E1 must hash to the committed M4 X1 artifacts: trades `fee44b84…`, daily `97f9d50c…`.
- **Invariants.** All three candidates share the same trade set: universe, H5, R1 order,
  selection, entries, exits and prices. E15 and E20 must be exactly 1.5× and 2.0× E1 in every
  scenario.

## 4. Gates (M0 values)

- **Eligibility.** Integrity PASS; coverage >= 95%; 10 bp session mean > 0; PF > 1; MDD >= −35%;
  top1 share <= 0.7338; and no catastrophic session.
- **Improvement over E1, for each candidate separately.** M0 defines no ordering between E15 and
  E20, so each is compared with E1 on its own. The 10 bp cumulative return must beat E1. The
  paired delta_t = candidate − E1 over 480 sessions must have mean > 0 and P(delta <= 0) <= 0.10
  (IID bootstrap, 10,000 replicates, seed 20260921).
- **Scaling caveat, recorded before the run.** Because delta_t = (k − 1) × E1_t, P(delta <= 0) is
  the same as the bootstrap probability that E1's own mean is <= 0. It measures the existing E1
  edge, not any exposure effect, and is not new Alpha evidence. E15 and E20 therefore pass or
  fail this condition together.
- **Winner.** Among the candidates that pass both gates, the one with the highest 10 bp CAGR wins.
  Exact ties go to the higher session mean, then to the declared order (E15, E20). M0 defines no
  CAGR tie band. If none passes, the verdict is `E-MAX-M5 NO_USEFUL_ENHANCEMENT` and E1 is kept.
- **Labels.** `E-MAX-M5 PASS — 1.5X SELECTED`, `E-MAX-M5 PASS — 2.0X SELECTED`,
  `E-MAX-M5 NO_USEFUL_ENHANCEMENT` or `E-MAX-M5 BLOCKED — INTEGRITY`.

## 5. M6 authorization

M6 is **AUTHORIZED** when the configuration carried out of M5 passes every eligibility condition
(including the catastrophic rule) and stop conditions A and D do not hold. Stop D means every
candidate of two consecutive executed stages falls below −35% MDD. Stop B is reported here and
becomes decisive only at M6.

## 6. Diagnostics (not used to decide)

- **Stop B.** Reported against E1 (the M2-M4 convention) and against E-Base (the M0 literal, E-Base
  CAGR 3.71% and MDD −16.45%).
- **Risk.** Worst session, week, month and quarter; maximum single-session loss; MDD; longest
  recovery; longest losing run; whether equity ever reaches 0 or below.
- **Breadth interaction.** Exposure by class (normal or high-breadth), return contribution by
  class, and each class's share of the MDD window.
- **Tail.** The top 1, 3 and 5 sessions' share of summed session returns; the top1 and top5
  symbols; HHI.
- **Blocks.** E-D6's four blocks: mean, compounded return, block MDD, and the count of positive
  blocks.
- **Calendar.** Positive and negative months and quarters, with the best and worst of each.
- **Periods.** FULL, LEGACY_NARROW and BROAD_COVERAGE.

## 7. Reproducibility and outputs

The run is executed twice. Both runs must give an identical result digest and byte-identical
artifacts.

- Runtime: `data/runtime/strategy_e_max/m5_runs/<run_id>/` (gitignored).
- Committed: `E_MAX_M5_EXPOSURE_RESULT_V1.md`, `strategy_e_max_m5_result_v1.json`, `.sha256`.

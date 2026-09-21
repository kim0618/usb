# Strategy E-MAX - M3 Breadth / Session Strength Protocol V1

| | |
|---|---|
| Stage | `E-MAX-M3`, phase A (protocol), frozen before any M3 performance |
| Machine authority | `strategy_e_max_m3_rules_v1.json`, canonical `2efbf457…d020` |
| Upstream | M0 `b05bbf6` · M1 `058217f`/`d5bdc6b` (R1) · M2 `cf5a6cb`/`9dc80b1` (NO_USEFUL_ENHANCEMENT, max 3 carried) |
| Code | `app.strategy_e_max.breadth` (multiplier, scale), `app.strategy_e_max.m3` (loader, decision, M4 authorization), `app.strategy_e_max.risk_diag` |
| Tests | `backend/tests/strategy_e_max/test_e_max_m3_protocol.py` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |

## 1. Variants

| id | ordering | capacity | exposure |
|---|---|---:|---|
| B1 (comparator) | R1 | 3 | 1.0 on every session |
| B2 | R1 | 3 | **1.5** when universe rows >= 100 **and** `h5_rate` >= **0.030741**; otherwise 1.0 |

`h5_rate` is the number of H5 candidates divided by the rows in the 09:25 PIT universe. Both are
sealed at 09:25.

The threshold and multiplier are M0's values. Any other threshold, percentile or multiplier is
forbidden, and so is using breadth as a trade filter.

## 2. Exposure semantics

- **Scaling.** Breadth never adds or removes a trade. On a high-breadth session, every position
  weight and every scenario session return is multiplied by 1.5. With 3, 2 or 1 executable
  positions the weights are 1/2, 3/4 and 3/2, so total exposure is exactly 1.5. Because each
  return already includes costs, B2 = 1.5 × B1 on that session and costs scale with notional.
- **Financing is not modelled.** That covers margin interest, broker leverage rules, intraday
  buying power and financing cost.
- **Risk status.** This is research only. An M3 PASS does not approve live 1.5x leverage; Common
  Risk V1 has no leverage.

## 3. Integrity (checked before B2 is read)

- The E-R3 prechecks are re-run first.
- B1, computed through `capacity.execute` at capacity 3, must hash to the committed M1 R1
  artifacts (`95d03874…`, `9d39d01b…`).
- **Invariants:**
  - B1 and B2 have the same universe, H5 candidates, selections, entries, exits and standard-PnL
    trades;
  - delta is 0 on every session with multiplier 1.0;
  - B2 is exactly 1.5 × B1 on every high-breadth session, in every scenario.

## 4. Gates (M0 values, unchanged)

- **Eligibility of B2.** Integrity PASS, coverage >= 95%, 10 bp session mean > 0, PF > 1,
  MDD >= -35%, top1 share <= 0.7338.
- **Improvement over B1.** Cumulative 10 bp B2 > B1. The paired delta_t = B2 − B1 over all 480
  sessions must have mean > 0 and P(delta <= 0) <= 0.10. The bootstrap is IID,
  `metrics.bootstrap_mean_ci`, 10,000 replicates, seed 20260921.
- **Sample caveat.** B2 differs from B1 only on high-breadth sessions; M0's structural count
  expects 26. The number of changed, positive, negative and zero-delta sessions is always
  reported next to the p-value.
- **Verdict.**
  - `E-MAX-M3 PASS — B2 SELECTED` if B2 passes both gates.
  - `E-MAX-M3 NO_USEFUL_ENHANCEMENT` otherwise, and B1 carries to M4.
  - `E-MAX-M3 BLOCKED — INTEGRITY` on any integrity failure.
- **No new breadth hypothesis may follow.**

## 5. M4 authorization (M0 `exit.run_condition`)

M4 is authorized only if the configuration carried out of M3 passes every M0 eligibility
condition at 10 bp. That configuration is B2 if it was selected, otherwise B1. The result states
exactly one of `M4 AUTHORIZED` or `M4 NOT AUTHORIZED`.

## 6. Diagnostics (not used to decide)

- **Opportunity.** All sessions; sessions with a universe of at least 100 rows; high-breadth
  sessions, how many of them are active, and their standard trades.
- **Periods.** FULL, LEGACY_NARROW (2024-10-16..2026-04-17) and BROAD_COVERAGE (2026-04-20..).
- **Risk.** Worst session, ISO week, month and quarter; recovery duration; longest losing run.
- **Changed sessions.** Positive, negative and zero delta, by period and by block.
- **Other.** Stop B (CAGR ratio against MDD ratio), concentration, and E-D6's four blocks with the
  paired delta.
- **Interpretation limit.** The threshold was set on the 104 broad-coverage sessions and can only
  fire there. A PASS is a block-4 development exploration result. It does not make E-MAX
  statistically validated.

## 7. Reproducibility and outputs

The run is executed twice. Both runs must give an identical result digest, an identical session
exposure map and byte-identical artifacts.

- Runtime: `data/runtime/strategy_e_max/m3_runs/<run_id>/` (gitignored).
- Committed: `E_MAX_M3_BREADTH_RESULT_V1.md`, `strategy_e_max_m3_result_v1.json`, `.sha256`.

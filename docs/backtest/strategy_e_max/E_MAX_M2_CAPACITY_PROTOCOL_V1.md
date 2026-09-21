# Strategy E-MAX - M2 Capacity Protocol V1

| | |
|---|---|
| Stage | `E-MAX-M2`, phase A (protocol), frozen before any M2 performance |
| Machine authority | `strategy_e_max_m2_rules_v1.json`, canonical `aabeddcd…5a4b` |
| Upstream | M0 `b05bbf6` (`3ae07d75…`), M1 protocol `058217f` (`4556a4f3…`), M1 result `d5bdc6b` (`eac7dfd7…`, winner R1) |
| Code | `app.strategy_e_max.capacity` (execution), `app.strategy_e_max.m2` (loader, decision) |
| Tests | `backend/tests/strategy_e_max/test_e_max_m2_protocol.py` |
| Evidence label | `E-MAX DEVELOPMENT` (reused development data, not OOS) |

## 1. Question and variants

With the R1 ordering fixed, M2 asks whether spreading the same normalized session exposure over
up to five H5 candidates, instead of three, helps.

| id | ordering | max selected | role |
|---|---|---:|---|
| C1 | R1 (`premarket_rvol` desc, tie symbol asc) | 3 | comparator (= M1 winner) |
| C2 | R1 | 5 | the only candidate |

Selection happens before entry validation, with no replacement. Candidate #6 is never promoted.

**Forbidden:** max 4, max 6 or more, "all candidates", dynamic or breadth-dependent capacity, any
other ordering (R2 and R3 included), and changes to H5, entry, exit, stop, target, cost, exposure
or breadth. No symbol may be excluded (ABSI, HIMS or any other).

## 2. Execution

Entry is the exact 09:30 open. A missing entry is `NO_TRADE_*` and the slot stays empty. Exit is
the exact 09:34 close, with no fallback. Costs are 0/5/10/15/20 bp, primary 10 bp. Exposure is
1.0x.

- **Sizing.** Normalized active-session gross exposure is 1.0 for both variants: 1/3, 1/4 or 1/5
  per executable position. M2 does not increase exposure; that is M5's question.
- **Implementation.** E-D2 fixes `max_selected_candidates = 3`, and E-D5 refuses more than three
  selected positions (Common Risk V1). Neither is modified. `capacity.execute` passes the
  selected set in canonical chunks of at most three to the unchanged `build_entry_records` and
  `resolve_exit_batch`. It classifies each exit with E-D5's own `_classification`, gives weight
  1/n to the executable ones, and costs them with E-D4 `apply_cost`. Entry and exit are
  per-symbol decisions, so chunking cannot change them (tested).
- **Pre-read checks.** C1 run through `capacity.execute` must hash to the committed M1 R1
  artifacts before C2 is read: `trades` `95d03874…` and `daily_returns` `9d39d01b…`. The E-R3
  prechecks are re-run first.

## 3. Risk status

C2 is **RESEARCH ONLY, NOT LIVE AUTHORIZED, and NOT COMMON-RISK COMPLIANT**. Common Risk V1 caps
positions at 3. An M2 PASS changes no risk contract. An aggressive risk contract would have to be
preregistered separately before any integrated E-MAX candidate could trade.

## 4. Gates (M0 values, unchanged)

- **Eligibility of C2.** Integrity PASS, coverage >= 95%, 10 bp session mean > 0, PF > 1,
  MDD >= -35%, and top1 share <= 0.7338.
- **Improvement over C1 (not Base).** Cumulative 10 bp C2 > C1, and paired delta_t = C2 − C1 over
  all 480 sessions with mean > 0 and P(delta <= 0) <= 0.10. The bootstrap is IID,
  `metrics.bootstrap_mean_ci`, 10,000 replicates, seed 20260921.
- **Verdict.**
  - `E-MAX-M2 PASS — MAX5 SELECTED` if C2 passes both gates.
  - `E-MAX-M2 NO_USEFUL_ENHANCEMENT` otherwise, and C1 carries to M3.
  - `E-MAX-M2 BLOCKED — INTEGRITY` on any integrity failure.
- **No new capacity hypothesis may follow.**

## 5. Diagnostics (not used to decide)

- **Opportunity.** Sessions with at least 4 and at least 5 candidates, and sessions where the
  selected set changes, split into LEGACY_NARROW (2024-10-16..2026-04-17) and BROAD_COVERAGE
  (2026-04-20..2026-09-16).
- **Stop B.** CAGR ratio C2/C1 against MDD ratio |C2|/|C1|. It is flagged when the MDD ratio is
  larger.
- **Other.** Concentration (unique symbols, top1/5/10, HHI, largest winner/loser) and E-D6's four
  blocks (session mean, trades, compounded return).
- **Interpretation limit.** Capacity only matters on sessions with at least 4 candidates, almost
  all in block 4. A PASS means max 5 beat the frozen max-3 comparator in development exploration.
  It does not mean E-MAX is statistically validated.

## 6. Reproducibility and outputs

The run is executed twice. Both runs must give an identical result digest and byte-identical
artifacts.

- Runtime: `data/runtime/strategy_e_max/m2_runs/<run_id>/` (gitignored).
- Committed: `E_MAX_M2_CAPACITY_RESULT_V1.md`, `strategy_e_max_m2_result_v1.json`, `.sha256`.

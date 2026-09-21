# Strategy E-MAX - M0 Aggressive Hypothesis Contract V1

| | |
|---|---|
| Stage | `E-MAX-M0`, preregistration only |
| Machine authority | `strategy_e_max_m0_rules_v1.json`, canonical `3ae07d75…82ac` |
| Validator | `app.strategy_e_max.m0.load_rules` (fail-closed; computes no return) |
| Tests | `backend/tests/strategy_e_max/test_e_max_m0_contract.py` |
| Namespace | `STRATEGY_E_MAX`, under `docs/backtest/strategy_e_max/` and `app.strategy_e_max`; no Base artifact is touched |
| Performance computed | **none** |
| **Gate** | **`E-MAX-M0 PASS — AGGRESSIVE RESEARCH CONTRACT FROZEN`** |

## 1. Base state (frozen, reference only)

E-Base is `E-R3 INCONCLUSIVE — STATISTICAL` (result `2d50cca5…`). At 10 bp it has 485 trades
over 237 active sessions:

| metric | value |
|---|---|
| trade mean | +6.82 bp |
| session mean | +1.89 bp |
| PF | 1.122 |
| MDD | -16.45% |
| Sharpe | 0.32 |
| CI | [-6.71, +10.48] bp |
| positive blocks | 2 of 4 |
| ABSI top1 share | 73.4% |

E-MAX compares against these values. It never edits H5, the V1.1 universe, the 09:25 cutoff, the
Base entry, exit or cost, or the Base result.

## 2. Why E-MAX is separate

E-Base asks whether an unbiased H5 selection survives cost with stability. It has a weak economic
edge and no statistical stability.

E-MAX asks a different question: can concentrating on the strongest opportunities or sessions
raise compound growth inside a finite risk envelope? Its objective is CAGR, not stability.
Selection rules that are deliberately biased toward strength are therefore a different strategy,
not a patch to Base. They are budgeted and staged so that a reused development dataset cannot be
mined without limit.

## 3. Structural facts that shaped the hypotheses (no returns read)

These counts come only from the `eligible` and `h5_candidates` columns of the E-R3 runtime
`daily_returns.csv`.

| | sessions | > 3 H5 candidates | > 5 |
|---|---:|---:|---:|
| before 2026-04-20 (30-symbol tape, universe mean 28.6) | 376 | 4 | 2 |
| from 2026-04-20 (broad tape, universe mean 580.6) | 104 | 97 | 91 |

In this dataset the absolute H5 count measures **data coverage**, not the market. Two
consequences follow:

- breadth is defined as a rate over the universe, with a coverage guard;
- ranking, capacity and breadth can only differ from Base on about 101 sessions, almost all in
  one coverage regime and in chronological block 4. This is a declared limitation of the M1-M3
  evidence.

## 4. Allowed axes and finite budget

| axis | stage | budget | variants (the comparator is included where marked) |
|---|---|---:|---|
| candidate ranking | M1 | 3 | R1, R2, R3 (comparator R0 = Base order) |
| capacity | M2 | 2 | C1 = max 3 (comparator), C2 = max 5 |
| breadth / session strength | M3 | 2 | B1 = 1.0x (comparator), B2 |
| exit | M4 | 2 | X1 = Base 09:34 (comparator), X2 |
| exposure | M5 | 3 | L1 = 1.0x (comparator), L2 = 1.5x, L3 = 2.0x |

No grid search, and no Cartesian product across stages. A failed stage earns no new variant. Any
new hypothesis belongs to a new version.

## 5. Ranking hypotheses (M1)

The only allowed features are the four sealed 09:25 H5 inputs. Each rule orders the session's
H5 candidates. Selection takes the first `capacity` of that order, with no replacement. Ties go
to canonical symbol ascending.

| id | rule |
|---|---|
| R1 | `premarket_rvol` descending |
| R2 | `return_0900_0925` descending |
| R3 | equal-rank composite. For each of `premarket_rvol`, `return_0900_0925` and `position_in_premarket_range`, rank candidates descending (1 = largest, ties receive the average rank). Score is the sum of the three ranks, ordered ascending. Weights are equal and not searched. |

**Gap-alone ranking is excluded.** Existing Research argues against it ("not monotone, and the
big gaps reverse"; "inverts where it is largest", E1 prevalidation). This decision was made from
that document before any E-MAX result existed. No other outcome evidence was used to choose R1-R3.

## 6. Capacity (M2)

C1 selects at most 3, the Base value. C2 selects at most 5. Sizing stays 1/n over executable
positions, with normalized gross 1.0. **C2 conflicts with Common Risk V1**
(`max_open_positions = max_new_symbols_per_day = 3`). It is a development research variant only
and cannot be traded without a new, separately approved risk version.

## 7. Breadth (M3)

Breadth never turns trading on or off. It only scales exposure.

- `h5_rate = H5 count / 09:25 PIT universe rows`, both known at the seal.
- The rate is defined only for universes of at least 100 rows, so a single candidate cannot
  exceed 1%.
- **B1:** 1.0x on every session.
- **B2:** 1.5x when `h5_rate >= 0.030741` and the universe has at least 100 rows; otherwise 1.0x.

The threshold is the 75th percentile (numpy, method `higher`) of `h5_rate` over the 104 sessions
with at least 100 rows. It is structural only, uses no return, and is met by 26 sessions, each
with 16 or more candidates.

## 8. Exit research budget (M4)

- **Run condition:** M4 runs only if the configuration entering it passes the stage eligibility
  conditions. Otherwise it is recorded as `SKIPPED_NOT_JUSTIFIED`.
- **Research prior:** E1-H5 mean lift over baseline is +6.49 bp at 1 minute, +16.54 bp at
  5 minutes and +5.70 bp at 15 minutes ("continuation peaks at five minutes and is gone by
  fifteen"). An unconditional longer hold is not supported, so only one conditional variant is
  allowed, and its prior is weak.
- **X1:** exact 09:34 close (Base).
- **X2 (momentum continuation):** at the close of the exact 09:34 bar, if close(09:34) > open(09:30),
  hold and exit at the exact 09:44 close; otherwise exit at close(09:34). This uses only
  information known at 09:35:00. An extended position without a valid 09:44 bar is
  `UNRESOLVED_EXIT`, with no fallback. The cost is one round trip at 10 bp.
- No horizon scan.

## 9. Exposure budget (M5)

L1 = 1.0x, L2 = 1.5x, L3 = 2.0x, applied to the frozen M1-M4 configuration. Costs scale with
notional, and financing cost is not modelled (a stated limitation).

- **Runs last**, and only if M1-M4 produced at least one winner. E-MAX is not a leveraged copy of
  Base.
- Multipliers above 1.0 conflict with Common Risk V1 (0.80 base capacity, no leverage). They are
  research-only.

## 10. Risk envelope and selection rule

**Envelope:**

- Primary cost stays `COST_10BP`. All five scenarios are reported.
- **Hard ceiling: MDD at 10 bp must be >= -35%.** Exactly -35% is eligible; anything deeper
  fails.
- Diagnostics: worst session, week, month and quarter; recovery duration; longest losing run;
  largest single-symbol loss; top1/5/10; HHI; trades per symbol.
- No symbol or period is excluded, including ABSI.

**Stage selection rule (M1-M5):**

1. Eligibility:
   - integrity PASS;
   - coverage >= 95%;
   - 10 bp session mean > 0 and PF > 1;
   - MDD >= -35%;
   - top1 share of net contribution <= 73.38% (Base), so an improvement cannot come from more
     concentration.
2. Improvement versus the stage comparator:
   - higher 10 bp compound cumulative return;
   - a paired IID session bootstrap of the daily difference (10,000 replicates, seed 20260921)
     with mean > 0 and P(difference <= 0) <= 0.10.
3. Winner: the highest 10 bp CAGR, `(1 + cumulative)^(252/480) - 1`. Ties go to the higher
   session mean, then to declared order.
4. If no candidate qualifies, the result is `NO_USEFUL_ENHANCEMENT` and the comparator is carried
   forward.

Also reported for every candidate: PF, session mean with its bootstrap CI, E-D6's four blocks,
trade count and active sessions. Block robustness of the difference is reported but is not a
condition, because the differences sit almost entirely in block 4 (section 3).

## 11. Development reuse guard and evidence labels

- **Data:** the E-R2 binding (tape `d12ff28a…`, USB-HIST-V1, 480 sessions), with the V1.1 PIT
  universe. Sessions from 2026-09-17 onward are excluded.
- **Procedure per stage:** a stage protocol is frozen and committed, the stage runs once, and its
  result is frozen. No re-run with changes.
- **Labels:** `E-BASE` (existing historical evidence), `E-MAX DEVELOPMENT` (new exploratory
  evidence on reused data, never called OOS), `FORWARD` (future independent evidence).

## 12. Roadmap

```text
M0 preregistration -> M1 ranking -> M2 capacity -> M3 breadth -> M4 exit (if justified)
-> M5 exposure (if any winner) -> M6 frozen MAX V1 integrated replay -> FORWARD (if it survives)
```

A stage ending in `NO_USEFUL_ENHANCEMENT` carries its comparator forward. It does not end E-MAX
by itself.

## 13. Stop conditions

- **A:** M1-M4 all end with no useful enhancement (or are skipped). E-MAX V1 stops, and M5 does
  not run.
- **B:** a winner's |MDD| ratio to Base exceeds its CAGR ratio to Base. This is flagged at every
  stage; if it still holds at M6, E-MAX V1 stops.
- **C:** improvements that only come with more concentration fail the top1 guard. The stage
  becomes `NO_USEFUL_ENHANCEMENT`, recorded as Stop C evidence.
- **D:** every candidate in two consecutive executed stages breaches -35%. E-MAX V1 stops.
- **E:** anything outside this budget, or without prior support, needs a new version.

## 14. Gate

None of the BLOCK conditions applies:

- H5 is untouched;
- every ranking uses sealed 09:25 inputs only;
- the namespace and provenance are separate and verified;
- the budget is finite and fixed before any result.

```text
E-MAX-M0 PASS — AGGRESSIVE RESEARCH CONTRACT FROZEN
```

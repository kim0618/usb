# Strategy E - E-R1 Trading V1.1 PIT Universe Correction

| | |
|---|---|
| Stage | `E-R1_PIT_UNIVERSE_CORRECTION` |
| Trigger | E-F0 audit, 2026-09-21: `E-F0 BLOCKED` (forward compatibility) |
| Machine authority | `strategy_e_trading_v1_1_rules.json`, canonical `b90573bd…04b6` |
| Forward context | `strategy_e_forward_feature_context_v1.json`, canonical `78bb90b4…3fad` (see `E_R1_FORWARD_FEATURE_CONTEXT_CONTRACT.md`) |
| Code | `backend/app/strategy_e_v1_1/{universe,decision,context}.py` |
| Tests | `backend/tests/strategy_e_trading/test_e_r1_pit_correction.py` |
| Performance computed | **none**: no replay, trade return, portfolio return, PF, CI, Sharpe or MDD |
| **Gate** | **`E-R1 PASS — TRADING V1.1 PREREGISTERED`** |

E-R1 corrects a point-in-time defect. It does not try to improve a result, and no E-D6 number was
used to choose any V1.1 rule.

---

## 1. The defect

Trading V1 built its 09:25 decision universe with two availability tests that cannot be evaluated
at 09:25:

| # | V1 test | where | knowable at 09:25? |
|---|---|---|---|
| 1 | the session D 09:30 minute bar exists (`requires_open_bar_0930 = true`) | `e1_premarket_rules_v1.json`; `premarket.session_rows` drops a session with no opening block; `dataset.build` requires a finite `open_570` | no |
| 2 | the session D grouped-daily open exists (`open(D+1) > 0` on the E0 row for D-1) | `e0_overnight_rules_v1.json` `universe.requires`; `strategy_e0_overnight.dataset.build` (`no_next_open`) | no |

E-F0 found test 1. E-R1 found test 2 while tracing the same path. E1 imports E0's D-1 row, so the
E0 overnight label's "next open exists" requirement became a requirement on session D's open.

**Consequence.** A symbol whose 09:30 print (or daily open) was missing disappeared *before* H5.
If it would have been an H5 candidate inside the top three, the next symbol took its capacity
slot. That is backfill done in the universe step, which the E-D2 no-replacement rule was written to
forbid. It also explains why E-D6 recorded `NO_TRADE_MISSING_ENTRY_BAR` for 0 of 501 selected
candidates: the case could not happen there. How many V1 rows were affected was not measured,
because measuring it means a new pass over development data, and E-R1 runs none.

**Reproduced by test, not only described.** Two synthetic symbols identical through 09:24 differ
only in whether a 09:30 bar exists. `premarket.session_rows` keeps A and drops B
(`test_v1_session_rows_drop_a_symbol_whose_only_difference_is_the_0930_bar`). On a synthetic
daily panel, `strategy_e0_overnight.dataset.build` drops the D-1 row whose session-D open is
missing (`test_v1_e0_row_for_d_minus_1_requires_session_d_daily_open`). Both V1 functions are
called unmodified.

## 2. Version authority

```text
Alpha Contract : H5 V1, unchanged
Trading V1     : historical; superseded for forward decision semantics
Trading V1.1   : authoritative candidate for a PIT-corrected development replay and future Forward

TRADING V1.1
  PREREGISTERED
  NOT YET DEVELOPMENT-VALIDATED
  NOT FORWARD-APPROVED
  NOT LIVE-APPROVED
```

## 3. Trading V1.1 decision pipeline

```text
PIT universe @ 09:25   (information_timestamp <= 09:25 ET, daily <= close of D-1)
  -> H5 Alpha           (Research mask, unmodified)
  -> canonical max-3    (symbol ascending, E-D2)
  -> IMMUTABLE DECISION SEAL
  -> 09:30 entry availability
       valid   -> execute proxy (exact 09:30 open)
       invalid -> ENTRY_INVALID (NO_TRADE_*), selected slot stays empty
  -> NO BACKFILL
```

| entry-bar existence | V1 | V1.1 |
|---|---|---|
| Alpha eligibility | used (through the universe) | **no** |
| universe eligibility at 09:25 | used | **no** |
| selection eligibility | used (through the universe) | **no** |
| execution eligibility at 09:30 | yes | **yes** |

Example, frozen as a test: candidates `A,B,C,D` give selected `A,B,C`. If B has no valid 09:30 bar,
executions are `A,C`, and D is **not** promoted
(`test_a_selected_symbol_missing_its_entry_does_not_promote_candidate_four`).

## 4. What changed and what did not

**Changed (the universe only, `STRATEGY_E_TRADING_UNIVERSE_V1_1`):**

- premarket: `requires_open_bar_0930 = false`;
- daily: `requires_session_d_daily_open = false`;
- split exclusion, which V1 derived from the daily factor at D, is now specified as a split list
  published before 09:25 of D (split execution dates are announced in advance). The rule itself,
  no split executing in (D-1, D], is unchanged.

**Unchanged and not re-implemented:**

| layer | V1.1 source |
|---|---|
| H5 | `evaluate_h5_signal` -> Research `mask("H5")`; `premarket_gap > 0`, `premarket_rvol >= 3.0`, `position_in_premarket_range >= 0.8`, `return_0900_0925 > 0` |
| every premarket feature | `premarket.premarket_block` / `derived_features`, called unmodified |
| RVOL | same semantics as `premarket.session_rows` (section 5) |
| daily thresholds | E0 values: CS <= D-1, XNAS/XNYS/XASE, close(D-1) >= $5, median $vol [D-21, D-2] >= $5M with >= 15 present, close(D-2) > 0 |
| premarket thresholds | >= 3 bars in [04:00, 09:24], >= $50,000 premarket dollar volume |
| selection | E-D2: max 3, canonical symbol ascending, replacement NONE |
| entry | E-D2 `FIRST_REGULAR_MINUTE_OPEN_PROXY_V1`, exact 09:30 open |
| exit | E-D3 `FIXED_FIVE_MINUTE_CLOSE_PROXY_V1`, exact 09:34 close, fallback NONE |
| cost | E-D4 0/5/10/15/20 bp, primary `COST_10BP` |
| sizing | E-D5 `EQUAL_WEIGHT_EXECUTABLE_V1` |
| stop / target / partial | NONE |

**Row identity.** For every (D, symbol) row V1 produced, V1.1 produces bit-identical premarket
features and RVOL (`test_v1_1_rows_are_bit_identical_to_every_row_v1_produced`). On the daily side,
V1.1 eligibility equals E0's for every row, except those E0 dropped only for the missing
session-D open (`test_v1_1_daily_eligibility_equals_e0_without_the_next_open_test`). V1.1 is
therefore V1 plus the rows V1 removed through post-09:25 availability, and nothing else.

## 5. RVOL semantics and a documented rules-text discrepancy

The code that produced every H5 result (`premarket.session_rows`) computes the denominator as the
median of the last 20 finite, positive premarket dollar volumes over **prior sessions that had
both a premarket bar and a 09:30 bar**, and leaves it undefined below 5 such sessions. The E1 rules
text describes it as "the 20 prior sessions that had premarket activity" and does not mention the
09:30 clause.

V1.1 follows the code, because the Alpha evidence was computed with it. The 09:30 clause refers
only to **earlier** sessions, which are complete at 09:25 of D, so it is PIT-valid. Whether session
D itself has a 09:30 bar is never read: `premarket_row` truncates the tape at 09:24 ET of D before
it computes anything.

## 6. Immutable seal

`decision.seal` fixes, at 09:25: the eligible rows, the 11 sealed feature columns, the H5 mask (via
the unchanged forward `decision_digest`), the candidate list, the selected and not-selected
symbols, the V1.1 rules digest, the E-D0 Alpha digest and the source digest. `decision.execute`
verifies the seal, calls `build_entry_records` unchanged, and refuses the session if the entry
layer's selected set differs from the sealed set. An edited seal (selection, candidates or digest)
is refused (`test_a_seal_edited_after_0925_is_refused`). Poisoning every bar after 09:24, or
adding a later day, leaves the decision digest and the seal digest unchanged
(`test_no_post_0925_value_reaches_the_decision_digest`).

## 7. E-D6 V1 supersession

```text
E-D6 V1
  historical result preserved: E-D6 INCONCLUSIVE — STATISTICAL
  result file sha256 f70f9196894b2a428c039fdd833cdd8ba4284dd9594778ceed6c02e9d5bf7eef (unchanged)

Forward-compatible trading decision authority:
  SUPERSEDED BY TRADING V1.1 CORRECTION

Reason:
  09:30 entry availability (and the grouped-daily open of D) affected pre-selection universe
  membership.
```

No E-D6 artifact was edited. The E-D6 result, protocol, tape manifest and every E-D0..E-D5 artifact
are byte-identical to commit `0fb1edc` (`test_frozen_v1_artifacts_are_byte_identical_to_the_e_d6_commit`).
The V1.1 code lives in `app.strategy_e_v1_1`, not `app.strategy_e`, because E-D6's `code_digest`
hashes every module in `app/strategy_e`. That digest still equals the frozen result identity
`0ffffa21…` (`test_frozen_loaders_and_e_d6_identity_still_hold`).

A V1.1 development replay needs its own protocol, frozen before it runs. It is not part of E-R1.

## 8. Tests

`test_e_r1_pit_correction.py`, 61 tests, synthetic fixtures only:

| # | requirement | test(s) |
|---|---|---|
| 1 | V1 future dependency reproduction | `test_v1_session_rows_drop_…`, `test_v1_e0_row_for_d_minus_1_requires_session_d_daily_open` |
| 2 | V1.1 universe independent of 09:30 | `test_v1_1_universe_is_independent_of_0930_bar_existence`, `test_v1_1_daily_eligibility_equals_e0_without_the_next_open_test` |
| 3 | same 09:25 inputs, same universe | `test_same_0925_inputs_give_the_same_universe_whatever_happens_later`, `test_v1_1_rows_are_bit_identical_…` |
| 4 | H5 threshold identity | `test_h5_statement_is_unchanged_…`, `test_h5_thresholds_and_inclusivity_are_unchanged` (7 boundary cases) |
| 5 | H5 candidates independent of 09:30 | `test_h5_candidates_do_not_depend_on_0930_entry_availability` |
| 6 | max-3 before entry validation | `test_max3_selection_is_sealed_before_any_entry_bar_is_seen` |
| 7 | no promotion of candidate #4 | `test_a_selected_symbol_missing_its_entry_does_not_promote_candidate_four` |
| 8-11 | entry / exit / cost / sizing unchanged | `test_entry_contract_unchanged`, `test_exit_contract_unchanged`, `test_cost_contract_unchanged`, `test_sizing_contract_unchanged` |
| 12 | no post-09:25 value in the decision digest | `test_no_post_0925_value_reaches_the_decision_digest`, `test_a_seal_edited_after_0925_is_refused` |
| 13 | forward daily context cutoff | `test_forward_daily_context_cutoff_is_d_minus_1` |
| 14 | incomplete context fails closed | `test_incomplete_context_fails_closed_instead_of_returning_h5_false` (11 cases), `test_spy_context_needs_no_0930_bar` |
| 15 | frozen artifacts unchanged | `test_frozen_v1_artifacts_are_byte_identical_…` (23 files), `test_frozen_loaders_and_e_d6_identity_still_hold`, `test_v1_1_rules_and_context_contract_checksums` |

## 9. Gate

| condition | result |
|---|---|
| 09:30 dependency removed from the decision universe (and the session-D daily open) | yes |
| H5 unchanged | yes: statement, thresholds, inclusivity, evaluator |
| selection-before-entry frozen | yes |
| no backfill | yes |
| forward feature prerequisites defined | yes, `E_R1_FORWARD_FEATURE_CONTEXT_CONTRACT.md` |
| frozen V1 artifacts untouched | yes |
| historical performance not run | yes |

```text
E-R1 PASS — TRADING V1.1 PREREGISTERED
```

None of the BLOCK conditions applies. H5 needs no change. The PIT universe is built from 09:25
information only. Research H5 semantics reproduce without future data (row identity above).
Entry, exit, cost and risk are untouched.

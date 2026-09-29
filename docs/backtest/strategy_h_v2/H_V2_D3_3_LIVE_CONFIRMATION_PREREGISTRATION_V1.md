# Strategy H-V2 - D3.3 Live Confirmation Preregistration V1

- declared: 2026-09-29
- status: **PREREGISTRATION - FROZEN BEFORE ANY D3.3 RESULT EXISTS. NOT AN EXECUTION RECORD.**
- stage that produced this: **H-V2-D3.2F** (LIVE VALIDATION READINESS)
- parent contracts: `H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` (Validation Contract V2),
  `H_V2_D3_2R_RESEARCH_CONTRACT_ALIGNMENT_V1.md` (Research Prompt V2 / Schema V2)
- **live model calls made producing this document: 0. Cost: $0.00.**

This document is the thing D3.3 is measured against, not a summary of what D3.3 found - it is written
and frozen before D3.3 runs. Every number in §5-§8 below is either a checksum of a decision already
made, or a threshold this stage commits to before seeing a single live output. If D3.3 is ever
re-run under different terms, that is a new, separately-declared preregistration, not an edit to
this one.

## 1. What This Stage Wired (D3.2F)

D3.2R produced Research Prompt V2 (`h_v2_d3_research_v2`) and Schema V2
(`h_research_interpretation_v2`) but left them unconnected to any runner, and defined telemetry data
contracts (`RepairTelemetryRecordV1`, `RawResponseRecordV1`) without wiring them to one either. This
stage:

- Wired both into a new runner, `backend/app/dev/run_strategy_h_v2_d3_3.py`, built around a full
  `ResearchAttemptRecordV1` (`research_attempt_v2.py`) that persists every field §2 lists, with full
  untruncated raw text for every call and repair round.
- Made every stored attempt immutable (`store_attempt` refuses to overwrite; a retry gets a new
  `attempt_id`).
- Added a `canonicalModel`-vs-`model_requested` mismatch check, computed and stored on the record,
  not asserted after the fact.
- Froze the D3.3 sample, budget, and L1-L7 gates (§5-§8) before this document's own commit, let
  alone before any D3.3 call.

**None of this was run.** `run_strategy_h_v2_d3_3.main()` deliberately raises rather than executing
- see that file's own docstring. Every test exercising the wiring (`test_research_attempt_v2.py`,
  `test_run_strategy_h_v2_d3_3.py`) uses a stub in place of the real model call.

## 2. Telemetry Fields Persisted Per Candidate

Exactly what D3.2F brief §3 lists, no field invented beyond it:

```text
research_run_id, candidate_id, ticker, cik
input_package_id, input_package_checksum
model_requested, canonical_model
prompt_version, schema_version, validation_contract_version
started_at, completed_at
initial_raw_response (full text, not a preview)
initial_parse_status, initial_validation_status
initial_failure_codes, initial_failure_details
repair_attempts[] (attempt_number, failure_codes_before, repair_prompt_version,
                   raw_repair_response (full text), validation_after, cost_usd)
final_raw_response (full text)
final_output, final_output_checksum, final_status
input_tokens, output_tokens (nullable - see below)
cost_usd
```

**`input_tokens`/`output_tokens` are honestly nullable, not fabricated.** No D3/D3.1/D3.2/D3.2R live
call was ever inspected for a token-count field - every prior runner (`run_strategy_h_v2_d3.py`) has
only ever read `total_cost_usd`, `is_error`, `result`, and
`modelUsage.<model>.canonicalModel` from the Claude Code CLI's JSON response. `extract_usage`
(`research_attempt_v2.py`) checks the plausible location a `usage` object could appear and returns
`(None, None)` if absent, rather than guessing a specific key path with no real response to confirm
it against (D3.2F brief §3: "없는 필드는 임의 생성하지 않는다"). **This is a known, stated gap to
close on D3.3's own first real response**, not a defect in this stage's wiring.

## 3. Immutability

`research_attempt_v2.store_attempt(root, record)` writes to
`root/<research_run_id>/<ticker>/<attempt_id>.json` and raises `AttemptAlreadyExistsError` if that
path already exists. A retry or research rerun must construct a new `attempt_id` - there is no code
path in this runner that can overwrite a stored attempt.
`test_immutable_attempt_cannot_be_overwritten` / `test_retry_uses_a_new_attempt_id_not_a_collision`
verify this directly.

## 4. Repair Lifecycle

Every repair round records `attempt_number`, `failure_codes_before` (via
`repair.classify_repair_reason`, unchanged from D3.1), `repair_prompt_version`,
`raw_repair_response` (full text), `validation_after`, and `cost_usd`. The repair prompt itself
(`prompt_builder_v2.build_repair_prompt_v2`, unchanged from D3.2R) is schema-correction only - it
does not ask for new evidence or an investment judgment, and does not receive any evidence text at
all, only the previous response and the validation errors. `test_repair_prompt_is_schema_
correction_only_never_asks_for_new_evidence_or_a_decision` checks the prompt text says so
explicitly, not merely fails to mention it.

## 5. Canonical Model Verification

`research_attempt_v2.compute_model_mismatch(requested, canonical)` returns `True` iff a canonical
model is known AND differs from what was requested. Per D3.2F brief §7, an output with
`model_mismatch=True` is **INVALID for gate-counting purposes** (§8's L1/L2/L5/L6 denominators
exclude it) - it is still stored and reported, never silently dropped, so a mismatch is itself an
auditable, visible event rather than a result that quietly disappears.

## 6. D3.3 Sample - Frozen, CIK-Disjoint

### 6.1 Exclusion set

Every CIK any real Opus call has touched across D3 and D3.1, 36 total, verified disjoint from each
other (`test_pilot_and_batch2_cik_sets_disjoint`):

- D3 pilot (12): AAON, AAPL, ABCB, ABL, ACA, ACLS, A, AA, AAP, AAT, ABCL, ABEO
- D3.1 Batch 2 (24, including the 5 that never reached the model and MRVI): DSP, OOMA, AEE, INOD,
  AIP, DXCM, TNXP, UVV, HL, LNG, AMT, DKNG, FLS, MIDD, MOV, SRCE, FCUV, PSNL, MRVI, TSLX, ACHC, TSVT,
  AIG, SCM

A sampled-but-never-called ticker (TSLX, ACHC, TSVT, AIG, SCM - the account-rate-limited five from
D3.1 §G) still counts as excluded: its evidence package and identity were already exposed to this
pipeline, even though the model was never actually invoked on it.

### 6.2 Selection method

`sha256("H_V2_D3_3_SAMPLE_V1:" + ticker)` ascending, over every D2.1 package whose CIK is not in the
exclusion set above - the same declared, no-performance/no-fame/no-sector method D3.1's own Batch 2
used (D3.2F brief §9). First 6 of the FULL/`E3_P1_HIGH` pool, first 6 of the CORE/`E3_P2_MEDIUM`
pool.

### 6.3 The frozen 12

| priority | ticker | CIK |
|---|---|---|
| FULL / P1_HIGH | BSY | 0001031308 |
| FULL / P1_HIGH | WEN | 0000030697 |
| FULL / P1_HIGH | GOOG | 0001652044 |
| FULL / P1_HIGH | SCCO | 0001001838 |
| FULL / P1_HIGH | DT | 0001773383 |
| FULL / P1_HIGH | LUV | 0000092380 |
| CORE / P2_MEDIUM | MOFG | 0001412665 |
| CORE / P2_MEDIUM | GD | 0000040533 |
| CORE / P2_MEDIUM | CWBC | 0001127371 |
| CORE / P2_MEDIUM | SIF | 0000090168 |
| CORE / P2_MEDIUM | BPOP | 0000763901 |
| CORE / P2_MEDIUM | BLKB | 0001280058 |

`sample_checksum = b671d9889f3d550fcb20cbf8f95fe216c1604f7a90a3d574cc2be24e671ddf9c`, computed as
`sha256("|".join(ticker for ticker in the table order above))`.

**Verified two ways, not one**: the checksum matches its own recomputation
(`test_frozen_sample_checksum_matches_its_own_recomputation`), AND an independent re-derivation from
the D2.1 snapshot run fresh produces the exact same 12
(`test_frozen_sample_matches_independent_regeneration_from_universe`,
`d3_3_contract.regenerate_sample_from_universe`) - a hardcoded list checksummed against itself would
only prove internal consistency, not that it was actually produced the declared way.

All 12 CIKs are pairwise distinct (`test_sample_ciks_unique_no_same_company_two_tickers`) and
disjoint from the 36-CIK exclusion set (`test_sample_disjoint_from_every_prior_live_cik`).

## 7. Budget

```text
target sample size:        12
hard live-model budget:    $30.00
worst-case per candidate:  $6.00 (1 initial call + up to 2 repair calls, $2.00/call cap - the same
                            per-call ceiling D3.1's own runner already used)
```

`run_sample` (in `run_strategy_h_v2_d3_3.py`) checks, before starting each candidate, whether
`spent + $6.00 > $30.00`; if so it stops and records which ticker it stopped before, exactly the
same before-not-after discipline D3.1 §G used for Batch 2's ceiling. At $6.00 worst case per
candidate, $30.00 covers 5 candidates at their absolute worst and all 12 at anything close to D3.1's
own observed average ($1.54/candidate reached, D3.2 §K) - **no automatic budget expansion exists in
this contract**, and none is authorized here (D3.2F brief §10: "자동 확대 금지"). If the sample
cannot complete inside $30, D3.3 reports what it actually completed, the same way D3.1 reported its
own rate-limited 19-of-24, not by raising the ceiling after the fact.

## 8. L1-L7 Gates - Frozen Before Any Result

`d3_3_contract.evaluate_d3_3_gates` / `d3_3_verdict`, committed to code before this document's own
commit and certainly before any D3.3 call:

| gate | name | threshold | source |
|---|---|---|---|
| L1 | Final validity | >= 95% | mechanical, from the run manifest |
| L2 | Initial validity (pre-repair) | >= 80% | mechanical - the metric D3.2R's prompt changes are directly aimed at moving |
| L3 | Material content accuracy | material defects == 0 | manual audit only (§9) - never machine-derivable |
| L4 | Unsupported Future Business stage escalation | == 0 | `validation_v2` re-derivation, same method D3.1/D3.2 already used |
| L5 | Citation precision defect rate | <= 10% | `validation_v2.citation_precision` (UNSUPPORTED + ADJACENT_RECOVERABLE) / material claims |
| L6 | Fabricated/mutated material numeric facts | == 0 | `validation_v2` numeric fidelity |
| L7 | Investment decision leakage | == 0 | `validation_v2.classify_investment_language` (VIOLATION) |

**L3 defaults to `NOT_EVALUATED`, never a silent PASS**, if the manual audit (§9) was not performed -
the same convention D3.1's own R10 and D3.2's R10B/C used. An unevaluated L3 forces the overall
verdict to `FAIL` (`test_l3_not_evaluated_when_manual_audit_not_performed`), because L3 is one of the
four **core gates** (`d3_3_contract.D33_CORE_GATES = ("L3", "L4", "L6", "L7")`) whose failure is
always FAIL, per §10's verdict contract - never merely a limitation.

L1/L2/L5 are the non-core, operational/prompt-quality gates: a shortfall on any of these alone yields
`PASS_WITH_LIMITATIONS`, not `FAIL` (`test_non_core_gate_failure_alone_is_limitations_not_fail`).
No new threshold is introduced anywhere in this document beyond L1-L7 themselves (D3.2F brief §12:
diagnostics are reported, not gated).

### 8.1 Diagnostics (reported, not gating)

Per D3.2F brief §12, also reported but never gated on a new threshold: repair rate and cause
distribution, `UNKNOWN` usage rate, `evidence_conflicts` count and resolution split, compound-claim
candidate count (with the same declared caveat as D3.2 §J.6 - a screening upper bound, not a
precision measure), numeric audit coverage, and structural citation validity (R4A, distinct from
L5's precision measure).

## 9. Manual Audit Contract

3 FULL + 3 CORE of the 12, selected the same deterministic way over the frozen sample (not the whole
universe): `sha256("H_V2_D3_3_MANUAL_AUDIT_V1:" + ticker)` ascending.

| depth | tickers |
|---|---|
| FULL | LUV, DT, WEN |
| CORE | BLKB, CWBC, MOFG |

`manual_audit_checksum = 700154df337133850e1b12cdd30b772094fd6043ff4cde6db2f4aef3ec41284e`, verified
against its own recomputation (`test_manual_audit_subsample_checksum_matches`) and confirmed to be
exactly 3 FULL + 3 CORE drawn from the frozen 12
(`test_manual_audit_subsample_is_3_full_3_core_drawn_from_the_sample`).

For each of these 6, per D3.2F brief §13, the manual read checks (at minimum, the same five
dimensions D3.1/D3.2's own R10 used, restated per candidate rather than pooled):

- content accuracy (does each read claim contradict its cited source)
- citation precision (does the cited chunk actually carry what the claim states)
- numeric fidelity (does a stated number match the source, at the source's own precision)
- qualification preservation (does a hedge in the source survive into the claim)
- Future Business stage (does the assigned stage match its own evidence flags, and does
  `missing_evidence` - if used - actually describe what is missing)

## 10. D3.3 Verdict Contract

```text
PASS                  - L1-L7 all PASS
PASS WITH LIMITATIONS - L3, L4, L6, L7 all PASS; L1 and/or L2 and/or L5 short
FAIL                  - any of L3/L4/L6/L7 FAIL or NOT_EVALUATED, OR a systematic reliability
                        problem is found that these seven gates do not already capture
```

Computed mechanically by `d3_3_contract.d3_3_verdict` for the L1-L7 part; the "systematic
reliability problem not already captured by L1-L7" clause is, by its nature, a judgment call for
whoever reviews the D3.3 result - this document does not attempt to reduce that judgment to a
formula, only to state that the seven gates are not assumed exhaustive.

## 11. D4 Authorization

Per D3.2F brief §15: if D3.3 resolves to `PASS` or an acceptable `PASS WITH LIMITATIONS`, **D4 =
READY** may follow, without an automatically-generated additional D3.x stage. This document does not
itself authorize D4 - it states the condition under which D3.3's own result would. A `PASS_WITH_
LIMITATIONS` that is NOT "acceptable" (a judgment this document does not attempt to reduce to a
formula either - see §10) does not authorize D4 by default; that determination is made when D3.3's
actual result exists, not preregistered here as an if-then rule that could be gamed by how a
limitation gets characterized after the fact.

## 12. Tests

37 new tests (D3.2F), all passing, 0 model calls:

- **`test_d3_3_contract.py`** (16): pilot/Batch-2 CIK disjointness, sample disjoint from every
  prior live CIK, no duplicate CIK in the sample, 6/6 depth split, frozen checksum matches its own
  recomputation AND an independent regeneration from the universe, manual-audit subsample checksum
  and 3/3 split, frozen budget values, L1-L7 aggregation (all-pass, L3 unevaluated forces FAIL,
  every core-gate failure forces FAIL, every non-core-gate failure alone gives
  PASS_WITH_LIMITATIONS, L5's denominator and zero-division handling).
- **`test_research_attempt_v2.py`** (11): full raw response persistence at both the initial and
  repair level (no truncation), immutable attempt storage and its collision error, a retry's new
  `attempt_id`, round-trip load, model-match/mismatch/unknown-canonical handling, honest `(None,
  None)` usage extraction when absent and correct extraction when present.
- **`test_run_strategy_h_v2_d3_3.py`** (10): first-call success, repair-then-success, repair-budget
  exhaustion, the repair prompt's explicit schema-correction-only language, model-call errors on
  both the initial and a repair call, canonical-model mismatch flagged end to end, unique
  `attempt_id`s across retries of the same candidate, budget stop occurring BEFORE a candidate that
  would breach it (not after a partial call), and every attempt in a sample run persisted
  immutably.

Full H-V2 regression: **388 passed** (351 pre-existing + 37 new), 0 failures, 0 model calls. No
existing test was modified.

## 13. Changed Files

New files only:

- `backend/app/backtest/strategy_h_v2/research/d3_3_contract.py` (new - sample, budget, L1-L7)
- `backend/app/backtest/strategy_h_v2/research/research_attempt_v2.py` (new - attempt record,
  immutable storage)
- `backend/app/backtest/strategy_h_v2/research/assemble_v2.py` (new - `validate.py`'s
  assembly/validation glue, restated for Schema V2; `validate.py` itself untouched)
- `backend/app/dev/run_strategy_h_v2_d3_3.py` (new - the wired runner; not executed)
- `backend/tests/strategy_h_v2/research/test_d3_3_contract.py` (new, 16 tests)
- `backend/tests/strategy_h_v2/research/test_research_attempt_v2.py` (new, 11 tests)
- `backend/tests/strategy_h_v2/research/test_run_strategy_h_v2_d3_3.py` (new, 10 tests)
- `docs/backtest/strategy_h_v2/H_V2_D3_3_LIVE_CONFIRMATION_PREREGISTRATION_V1.md` (this file)

Not modified: `schema.py`, `schema_v2.py`, `prompt_builder.py`, `prompt_builder_v2.py`, `validate.py`,
`repair.py`, `gates.py`, `validation_v2.py`, `telemetry_contract_v2.py`,
`run_strategy_h_v2_d3.py`/`run_strategy_h_v2_d3_1.py`, `audit_strategy_h_v2_d3_1.py`/
`audit_strategy_h_v2_d3_2.py`, or any D3/D3.1/D3.2/D3.2R artifact. The 194 pre-existing dirty files
in the working tree remain exactly as they were.

Noted, not caused by this stage: `frontend/next-env.d.ts` shows as modified in `git status`
(`.next-build` -> `.next-dev` in its own auto-generated reference path) - a Next.js-generated file
this session never touched; left as-is.

## 14. Verdict

**H-V2-D3.2F = READY FOR D3.3.**

Everything D3.3 needs to run reproducibly and be measured against a standard fixed in advance now
exists: the wired runner with full-fidelity, immutable telemetry (§1-§5), a CIK-disjoint frozen
12-issuer sample verified two independent ways (§6), a hard budget with a before-not-after stop
(§7), L1-L7 gates and their PASS/PASS WITH LIMITATIONS/FAIL contract committed to code before any
result exists (§8, §10), a manual-audit subsample and method (§9), and the D4-authorization condition
stated in advance (§11). This is a readiness judgment about the *apparatus*, not a prediction about
what D3.3 will find - §2's token-count gap is real and stated, and nothing here has been exercised
against a live response.

## 15. Declarations

```text
coding model
= Claude Sonnet 5

new live Opus calls
= 0

live model cost
= $0.00

Prompt V2 changed?
NO

Future Business floor changed?
NO

Validation V2 changed?
NO

D3.1 result changed?
NO

new issuer live execution?
NO

D4?
NO

forward returns?
NO

existing dirty files modified?
NO

push?
NO
```

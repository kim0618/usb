# H-V2-D4-H - Pre-Tier-B Integrity Hardening

```
H-V2-D4-H
= NEEDS REVISION

R1  M8 STATE_TOKEN numeric-role coverage      CLOSED
R2  M8 parallel / ellipsis window coverage    CLOSED
R3  M8 compound set-valued semantics          DEFERRED  (unchanged, by design)

CODE_OWNED_STATE_FIDELITY                     IMPLEMENTED, MEASURED, NOT FIT AS WRITTEN

live Opus calls   0
live cost         $0.00
```

R1 and R2 both meet their frozen acceptance criteria on the real corpus, and no M1-M12 status on any
graded run moved. The new state gate does what the brief's §9 asks of it, including the injected
`STABLE -> ACCELERATING` regression, and its first measurement on 38 eligible historical claims
returned **0 true state violations and 8 false-positive tokens across 6 claims**, from two mechanisms
that were not anticipated when its contract was frozen. A gate with a measured false-positive rate
that high cannot be declared an active Tier B gate, and repairing it here - after seeing those counts
- is exactly the result-driven tuning §14 exists to prevent. So the verdict is NEEDS REVISION, the
revision is specified in §K, and it is a preregistration decision rather than something this step
takes.

This is not a Tier B execution authorization, not a Tier B budget approval, and not a result about
interpretation quality.

---

## A. Why D4-H Exists

D4-S1 returned STRONG PASS: one issuer, one live call, initial valid, no repair, $1.3940560, M1-M12
all PASS, and the D4-S effect confirmed causally by a same-byte counterfactual (pre-D4-S = FAIL,
D4-S = PASS). Tier B is the next live step, and Tier B is the first step that spends real money on
issuers nobody read while designing D4.

The M8 compound-coverage audit (`H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md`) had already measured
what would happen if Tier B relied on M8 as written:

```
TRUE_NUMERIC_DEFECT        0
MATCHER_FALSE_POSITIVE     4
NOT_A_NUMERIC_RESTATEMENT  2
```

Six findings, none of them a real defect. Worse, that audit's own §mechanism A recorded a defect in
the other direction: `"Revenue is ACCELERATING."` against a code fact whose value is `STABLE`
**passed**, and the identical sentence with `" over 3 quarters"` appended **failed**. The one thing a
state-ownership check exists to catch was the one thing the check let through, and whether it caught
it depended on an unrelated character.

Spending Tier B's budget behind a gate that is wrong in both directions buys numbers nobody can read.
D4-H closes the two coverage defects and the state defect at $0, before the money is committed.

---

## B. Authoritative Input

```
branch                         main
HEAD at preflight              c170123  test(strategy-h-v2): record SCCO limited live smoke
HEAD at write-up               651e3d7  feat(crypto): add Binance live quick sizing
origin/main                    738bdc1  refactor(strategy-h-v2): simplify expectation claim validation
origin/main vs HEAD            ahead 4 / behind 0
staged at preflight            0
dirty at preflight             25 modified, 201 untracked
H-V2 related dirty at preflight 0
```

All three named commits verified present in real history, which takes precedence over the brief's
short forms:

```
738bdc1  refactor(strategy-h-v2): simplify expectation claim validation   (D4-S)
235ac4b  test(strategy-h-v2): audit M8 compound claim coverage            (M8 compound audit)
c170123  test(strategy-h-v2): record SCCO limited live smoke              (D4-S1 SCCO result)
```

**HEAD moved during this session, from a concurrent session, and this is reported rather than
smoothed over.** `651e3d7` is a crypto live-sizing commit touching `backend/app/crypto/`,
`backend/tests/crypto/` and `frontend/`. The semantic question was checked rather than assumed:

```
git diff --stat 738bdc1..HEAD -- backend/app/backtest/strategy_h_v2 \
    backend/app/dev/audit_strategy_h_v2_d4_{1,2}.py \
    backend/app/dev/run_strategy_h_v2_d4_2.py     -> empty
```

No H-V2 source has moved since the D4-S commit. The 25 pre-existing dirty files are unrelated to
H-V2 and were neither modified nor staged.

Authoritative state carried in unchanged:

```
H-V2-D3.3              PASS
Final Tier A V3        MECHANICAL READY, historical immutable
H-V2-D4-S              READY FOR LIMITED LIVE SMOKE
H-V2-D4-S1 SCCO        STRONG PASS
Tier B                 NOT AUTHORIZED
D5                     NOT READY
```

---

## C. R1 - STATE_TOKEN numeric-role coverage

### What the code actually did

`numeric_roles.fact_is_restated`, read before anything was changed:

```python
if unit == "STATE_TOKEN":
    return str(value).upper() in text.upper() or not any(c.isdigit() for c in text)
```

A raw character scan. `classify_roles` is never called, so every exclusion D4.3R built - a session
count, a fiscal-period label, a stage identifier - is bypassed, and ANY digit anywhere in the
sentence disables the "purely qualitative" escape. Two of the six compound findings are that and
nothing else:

| case | run | ticker | the digit | its real role |
|---|---|---|---|---|
| 1 | D4.3A | GOOG | `252-session` | SESSION_COUNT |
| 6 | Final Tier A V3 | BSY | `D3` | IDENTIFIER |

In both, `classify_roles` already knew the digit was not a value. The STATE_TOKEN branch simply never
asked it.

### The repair

The branch was **removed**, not patched. A `STATE_TOKEN` code fact carries no number - its value is a
`change_detection.ChangeState` member - so it falls through to the guard that was already directly
below it:

```python
if not isinstance(value, (int, float)):
    return True
```

"A fact with no numeric value has nothing M8 can compare." The state question moved to its own gate
(§F, §G). Fixing the branch in place was rejected because the fault is structural: a question about a
CATEGORY was being answered inside a matcher whose unit of comparison is a number, and the observable
symptom - a verdict that turns on the presence of an unrelated digit - is what that mismatch looks
like from outside.

### R1 changes no M8 semantics

M8's requirement is unchanged: *no claim cites a code-owned fact while stating a DIFFERENT number.*
A `SESSION_COUNT`, `FISCAL_PERIOD`, `IDENTIFIER`, `DATE` or `RANGE` token still never triggers a fact
comparison, exactly as D4.3R established; `_fact_candidates` and every tolerance in it are untouched;
no new number parser was written, and `numeric_roles` still reuses D3.2's `parse_numeric_tokens`.

**The live validator loses nothing, because it never had this.** `validate._COMPARABLE_UNITS` is
`{RETURN_FRACTION, ANNUALIZED_STDEV, USD}` and has never listed `STATE_TOKEN`, so
`check_code_fact_numerics` has never examined a state fact in any run. Only the audit layer's
`fact_is_restated` did. R1 therefore makes the audit layer **agree** with the validator on M8's
scope, which is the same alignment D4.3R performed for M4.

---

## D. R2 - parallel / ellipsis window roles

### What the code actually did

```python
_SESSION_COUNT = re.compile(
    r"\b\d+(?:\.\d+)?[\s-](?:trading[\s-])?sessions?\b"
    r"|\b\d+(?:\.\d+)?-(?:day|month|year)s?\b", re.IGNORECASE)
```

Every window number had to sit against its own unit word, and day/month/year required a **hyphen**.
Real claims coordinate windows and elide all but the last unit word, so the leading member survived
as a bare `COUNT` token and was compared to the cited fact:

| case | claim text | surviving token | cited fact |
|---|---|---|---|
| 2 | `Across the 1- and 3-session windows ...` | `1` | `return_1d` = -0.012378 |
| 3 | same claim | `1` | `return_3d` = 0.054919 |
| 4 | `... relative strength over 3 and 6 months is negative.` | `3`, `6` | `drawdown_from_252s_close_high` |
| 5 | same claim | `3`, `6` | `relative_strength_6m` |

The elided digits **are the cited facts' own window labels**: `return_1d`'s description is
"1-session event return", `relative_strength_6m`'s is "over the same 126 sessions". The finding
tracked English ellipsis, not numeric ownership - written out as "1-session and 3-session", the
identical statement always passed.

### The repair

Two changes, both coverage:

```python
_WINDOW_UNIT   = r"(?:trading[\s-])?(?:sessions?|days?|months?|years?)"
_WINDOW_COORD  = r"(?:,\s*|\s*,?\s*(?:and|or)\s+)"
_SESSION_COUNT = re.compile(
    rf"\b(?:{_WINDOW_NUMBER}-?{_WINDOW_COORD})*{_WINDOW_NUMBER}[\s-]{_WINDOW_UNIT}\b",
    re.IGNORECASE)
```

1. `[\s-]` before every unit word, not only before "session", so `over 3 months` is a window the same
   way `3-month` already was.
2. A leading run of coordinated numbers, each optionally carrying its own dangling hyphen, is
   absorbed into the span.

Verified spans:

```
"1- and 3-session"        -> SESSION_COUNT   (one span)
"3 and 6 months"          -> SESSION_COUNT
"1-, 3-, and 6-month"     -> SESSION_COUNT
"over 3 months"           -> SESSION_COUNT
"252-session"             -> SESSION_COUNT   (unchanged)
```

### Why this is coverage and not a loosened tolerance

Stated as two properties, each with a test:

- **No tolerance moved.** `_fact_candidates` is byte-identical. A genuine mismatch sitting beside a
  window label is still caught: `"Relative strength over 3 and 6 months was -18.40%"` against
  -0.051475 is still `False`.
- **No code fact is ever a window length.** `build_code_fact_index` emits only `RETURN_FRACTION`,
  `ANNUALIZED_STDEV`, `USD` and `STATE_TOKEN`. A digit whose unit word is a count of sessions, days,
  months or years cannot be a reading of any fact that exists, so masking one cannot hide a real
  restatement. Asserted against the real builder's source, so a new fact unit breaks the test.

The coordinator must follow the digits **immediately**, which is what stops the run from swallowing
an adjacent real figure: in `"Revenue rose 12.3% and 6 months later"`, `12.3` stays
`VALUE_RESTATEMENT` and only `6 months` becomes `SESSION_COUNT`.

### Known residual, recorded not assumed

`"over 3-6 months"` is a **range** of window lengths, not an ellipsis. `-` and `to` were deliberately
NOT added as coordinators, because that would conflate `SESSION_COUNT` with the `RANGE` role the
module already keeps separate. The leading `3` still survives as a `VALUE_RESTATEMENT`. Not observed
in any stored output; pinned by a test so it stays a recorded limitation. `quarters` and `weeks` are
likewise uncovered as window units, for the same reason: neither appears in the corpus and adding
them is scope this step was not asked for.

---

## E. R3 Deferred

```
M8 COMPOUND SET-VALUED SEMANTICS
= DEFERRED
```

The shape: a compound claim cites several code facts, states a value for **some** of them, and the
silent facts are then judged against a number that was never about them. `fact_is_restated` is called
once per `(claim, fact)` pair, loops every value token against that one fact, and its only escape is
"no value token anywhere". So:

```
states NO cited value     -> every cited fact passes (the escape)
states ALL cited values   -> every fact finds its own match, all pass
states SOME cited values  -> the silent facts are flagged
```

Reason for deferring, unchanged from the audit that found it: **it changes the unit of comparison, not
merely the coverage.** R1 and R2 both change which digits are candidates; R3 changes whether the
comparison is per-fact or per-claim, which is a different gate.

Measured exposure in the stored corpus: **0 occurrences**, on both Tier A runs and on D4-S1. It is a
structural exposure the runs happen not to exhibit, and it is still pinned by a test so it cannot be
mistaken for closed.

Tier B must therefore declare M8's scope explicitly (§K).

---

## F. State Fidelity Defect

### The defect, restated from the code

The line R1 removed was wrong in both directions at once:

| claim | cited fact | old M8 | correct |
|---|---|---|---|
| `Revenue is STABLE, below its 252-session high.` | STABLE | PASS | PASS |
| `... fundamentals continued improving ... 252-session ...` | ACCELERATING | **FAIL** | PASS |
| `Revenue is ACCELERATING.` | STABLE | **PASS** | FAIL |
| `Revenue is ACCELERATING over 3 quarters.` | STABLE | FAIL | FAIL |

Rows 2 and 3 are the defect, and row 4 shows the shape of it most clearly: the verdict on rows 3 and 4
differs, and the only difference between them is a number that has nothing to do with the state.

### The §8 audit: should STATE_TOKEN fidelity be removed from M8, or transferred?

**Transferred.** Removing it outright would drop a real obligation, because nothing else was checking
it - `check_code_fact_numerics` never looked at a state fact in any run, so the audit layer's broken
line was the only place the question was asked at all. Keeping it inside M8 was rejected for the
reason in §C: M8's unit of comparison is a number, and a state is not one.

So D4-H adds `backend/app/backtest/strategy_h_v2/expectation/state_fidelity.py`, named
`CODE_OWNED_STATE_FIDELITY` after the `M`-gate naming convention's own style (a requirement stated as
what must hold, not as what is checked).

### The two mechanisms the replay found, which its frozen contract did not anticipate

Both are gate false positives, both are recorded here rather than repaired (§I):

**S1 - no negation scope.** `IMPROVING`, `ACCELERATING`, `STABLE`, `INCREASING`, `DECREASING` are
ordinary English adjectives, and §9's own case 4 requires a lowercase rendering to count as a
restatement. The gate therefore cannot tell a restatement from a denial:

```
"... continuing at its historical pace, not accelerating ..."     -> flagged ACCELERATING
"The reality side is mixed rather than improving."                 -> flagged IMPROVING
"Top-line growth is steady rather than improving: a STABLE ..."    -> flagged IMPROVING
```

All three are BSY, all three name a state BSY owns **nowhere**, and all three name it only to deny it.
The third even names `STABLE` correctly in the same sentence.

**S2 - the owned set is the claim's citations, not the candidate's facts.** A claim may restate a
state correctly and simply not cite that metric's state fact:

```
BSY  gap_rationale[2]   names DETERIORATING       BSY eps_diluted = DETERIORATING    cited: revenue, operating_income, free_cash_flow
BSY  gap_rationale[2]   names DECELERATING        BSY operating_income = DECELERATING cited: revenue, free_cash_flow
GOOG gap_rationale[1]   names IMPROVING           GOOG revenue/eps/margin = IMPROVING cited: operating_income, free_cash_flow
GOOG gap_rationale[6]   names IMPROVING           GOOG revenue/eps/margin = IMPROVING cited: drawdown, operating_income
```

Every one of these is a state the candidate's own `fundamental_changes` block holds. This is decidable
structurally - "does the candidate own this token under a metric the claim did not cite" - and the
replay reports that flag per finding, so the mechanism does not depend on anyone reading the prose.

Worth stating plainly: the two claims M8 wrongly flagged as numeric defects (compound audit cases 1
and 6) are flagged by the new gate too, for a different and also wrong reason. Moving the question to
a gate built for it did not by itself make those claims clean.

---

## G. State Fidelity Contract

Frozen before the replay ran, and asserted by 29 tests in
`backend/tests/strategy_h_v2/expectation/test_d4_h_state_fidelity.py`.

```
eligible    the claim cites >= 1 STATE_TOKEN code fact
            AND names >= 1 recognized state token in its text
PASS        every state token the claim names is owned by one of the STATE_TOKEN facts it cites
FAIL        the claim names a state that none of its cited state facts holds
NOT_EVAL    not eligible - and zero eligible claims is NOT_EVALUATED, never a silent PASS
```

**Unit of comparison: the claim together with its whole cited state set.** Set containment, not one
`(claim, fact)` pair at a time. That choice is what keeps this gate out of M8's deferred R3 problem
instead of importing it: a compound claim citing four metrics' states and naming three of them is
restating three faithfully and saying nothing about the fourth, and judging it per cited fact would
report the silent one as a mismatch against tokens that were never about it. Containment asks the
question that has an answer without per-metric attribution: *did the model introduce a state the
pipeline does not own?*

**Vocabulary read from the authoritative enum.** `change_detection.ChangeState`, not a list in this
module, so a new enum member cannot be silently unrecognized. `UNKNOWN` is recognized as a fact VALUE
but not as a token named in prose - it is the contract's own honesty marker (`unknown_fields`, the
`UNKNOWN` claim type, "the driver is unknown"), and reading it as a code-owned state would turn the
discipline the contract asks for into a defect, the same reasoning
`audit_strategy_h_v2_d4_1.NON_PROSE_KEYS` already uses for `consensus_status`.

**No synonyms.** The existing D4 contract already tells the model to "copy the BARE TOKEN exactly as
the D3 output states it" (`prompt.D3_IMMUTABILITY`), and nothing in H-V2 licenses a paraphrase of a
state. Case is ignored; the underscore in a two-part token may be written as a space or a hyphen,
because those are renderings of one token. A prose paraphrase ("picking up", "slowing") makes the
claim **ineligible** rather than passing or failing it. The mapping was frozen before any stored
output was read through it and was not widened afterwards.

**What this contract does NOT claim, declared not discovered.** It does not bind a named state to a
particular metric. A claim citing `{STABLE, DECELERATING}` that swaps which metric holds which passes
containment. Binding a token to a metric needs per-metric attribution over prose - the same
unit-of-comparison change R3 is deferred for - and it is deliberately not attempted.

**Wired to no M gate.** `d4_2_contract.M_GATES` is frozen at M1-M12 and is untouched. The gate's
result is reported in the audit output and in the replay artifact; it has never been part of any Tier
A verdict, and adding it to `M_GATES` would retroactively change what a frozen verdict measures.

**Not wired into the live validator either, and this was decided before the replay ran.** Adding a
rejection to `assemble_and_validate_d4` would change whether D4-S1's stored bytes are VALID, which is
a historical verdict (§12 immutable) and a live-behaviour change that belongs in a preregistered
contract. Whether the gate is enforced at validation time or adjudicated at audit time is a Tier B
decision (§K).

---

## H. Offline Replay

`backend/app/dev/replay_d4_h_integrity_hardening.py`. Zero live calls, $0.00. Artifact:
`data/runtime/strategy_h_v2/d4_2/replay/d4_h_integrity_hardening_replay-20260930T042900Z.json`.

Every stored run carrying an authoritative verdict, and every candidate in it:

```
D4_2_A-20260929T072105Z   D4.3A Tier A V2                  SCCO GOOG BSY
D4_2_A-20260930T012115Z   Final Tier A V3                  SCCO GOOG BSY
D4_2_A-20260930T034348Z   D4-S1 SCCO limited live smoke     SCCO
```

The fourth stored run under `analyses/` (`D4_2_A-20260930T001105Z`) has no manifest and no graded
verdict, so it is deliberately not replayed - it is not a record this step may speak about.

### M1-M12: nothing moved

```
every_m_gate_reproduced = true      36 of 36 gate statuses (3 runs x 12)
```

**One correction worth recording, because getting it wrong the first time would have credited D4-H
with someone else's repair.** D4.3A's own `*.gateaudit.json` records `M4 = FAIL`, `M8 = FAIL` and
`MECHANICAL NOT READY` - it was produced by the **pre-D4.3R** detectors, and those two FAILs are the
false positives D4.3R removed. Compared against that file, this replay reported "M GATE STATUS MOVED"
for M4 and M8. The baseline for a post-D4-H comparison is each run's last status **before D4-H**,
which for D4.3A is D4.3R's own replay counterfactual (all twelve PASS). D4.3A's document and its
stored gate audit both stand unedited; neither is overwritten by this step. D4-S1 stored no gate
audit, so its recorded side is the M1-M12 table in its own result document.

### M8

```
atomic defects (the gate figure)          0    unchanged on all three runs
compound coverage gap findings            0    was 6
new true numeric mismatches introduced    0
```

The compound-coverage gap is the list a widened M8 would have flagged. Its emptying is R1/R2's result
measured on real bytes rather than on fixtures.

### CODE_OWNED_STATE_FIDELITY - first measurement

```
eligible claims                            38
violating claims                            6
unowned tokens                              8

of those 8 tokens:
  a state the candidate owns, uncited       5     (S2)
  a state no metric of the candidate holds  3     (S1, all three denied by the prose)

TRUE state fabrication or upgrade           0
```

Per candidate:

| run | ticker | eligible | violating claims | tokens | status |
|---|---|---|---|---|---|
| D4.3A | BSY | 6 | 2 | 3 | FAIL |
| D4.3A | GOOG | 6 | 2 | 2 | FAIL |
| D4.3A | SCCO | 5 | 0 | 0 | PASS |
| V3 | BSY | 6 | 2 | 3 | FAIL |
| V3 | GOOG | 5 | 0 | 0 | PASS |
| V3 | SCCO | 5 | 0 | 0 | PASS |
| D4-S1 | SCCO | 5 | 0 | 0 | PASS |

The eight, each with its mechanism:

| # | run | ticker | path | token | candidate owns it? | mechanism |
|---|---|---|---|---|---|---|
| 1 | D4.3A | BSY | `improvement_claims[4]` | ACCELERATING | no | S1 - "not accelerating" |
| 2 | D4.3A | BSY | `gap_rationale[2]` | IMPROVING | no | S1 - "mixed rather than improving" |
| 3 | D4.3A | BSY | `gap_rationale[2]` | DETERIORATING | yes (`eps_diluted`) | S2 |
| 4 | D4.3A | GOOG | `gap_rationale[1]` | IMPROVING | yes (`revenue`, `eps_diluted`, `operating_margin`) | S1 + S2 |
| 5 | D4.3A | GOOG | `gap_rationale[6]` | IMPROVING | yes (same three) | S2 |
| 6 | V3 | BSY | `improvement_claims[3]` | IMPROVING | no | S1 - "steady rather than improving" |
| 7 | V3 | BSY | `gap_rationale[2]` | DECELERATING | yes (`operating_income`) | S2 |
| 8 | V3 | BSY | `gap_rationale[2]` | DETERIORATING | yes (`eps_diluted`) | S2 |

**No finding is a state the candidate could not have seen.** That is why the verdict below is NEEDS
REVISION and not FAIL: the gate is over-strict in two identifiable ways, not blind. Nothing was
hidden, and nothing was adjusted after these counts were read.

---

## I. Acceptance

Frozen before the replay ran, as the tests that encode it.

### R1/R2 - ACCEPT

```
no matcher false positive among the six attributable to R1/R2    PASS   6/6 now read True
no new true numeric mismatch from parser regression              PASS   M8 atomic 0 on all 3 runs
no M1-M12 status moved                                           PASS   36/36 reproduced
R3 cases remaining                                               0      (R3 still DEFER, §E)
```

### State Fidelity - MIXED, reported as measured

```
injected regression STABLE -> ACCELERATING must FAIL             PASS
verdict independent of numeric content                           PASS   6 numeric suffixes tested
zero eligible reported as NOT_EVALUATED, never PASS              PASS
real state mismatches in stored outputs not hidden               PASS   0 found; 8 false positives reported
false-positive rate fit for an active Tier B gate                 NO     6 of 38 eligible claims
```

The last line is the whole of the NEEDS REVISION verdict. The brief set no false-positive ceiling for
this gate, because the gate did not exist when the brief was written; measuring one at 6/38 and then
declaring the gate active anyway would be declaring a number nobody can read - which is the same
mistake §A says Tier B must not buy.

**The gate's contract was not adjusted after these results were seen.** Both mechanisms have an
obvious-looking one-line repair (widen the owned set to the candidate's full state set for S2; add a
negation window for S1), and taking either here would make D4-H's own acceptance criteria unfalsifiable.
They are §K's preregistration proposal instead.

---

## J. Tests

```
suite                                       918 passed, 1 skipped   (was 841 passed, 1 skipped)
new                                         +77
live calls                                  0
```

Run from the repository root, which is the cwd the stored-record tests' relative paths require. Two
pre-existing D3.3 research test files (`test_d3_3_contract.py`, `test_run_strategy_h_v2_d3_3.py`) fail
when pytest is invoked from `backend/` for the same relative-path reason; both are untouched by this
step and both pass from the root.

New files:

```
backend/tests/strategy_h_v2/expectation/test_d4_h_state_fidelity.py          29 tests
backend/tests/strategy_h_v2/expectation/test_d4_h_numeric_role_coverage.py   37 tests
backend/tests/strategy_h_v2/expectation/test_d4_h_offline_replay.py           9 tests
```

Every case the brief's §17 names:

```
STATE_TOKEN + 252-session          test_r1_the_252_session_case_is_closed
STATE_TOKEN + D3                   test_r1_the_d3_identifier_case_is_closed
1- and 3-session                   test_every_window_label_variant_is_a_session_count
3 and 6 months                     test_every_window_label_variant_is_a_session_count
1-, 3-, and 6-month                test_every_window_label_variant_is_a_session_count
over 3 months / over 6 months      test_every_window_label_variant_is_a_session_count
STABLE vs ACCELERATING             test_case_2_wrong_state_fails
  + a number                       test_case_3_wrong_state_with_a_number_still_fails
correct state                      test_case_1_correct_state_passes, test_case_4_...
no state restatement               test_case_5_no_state_restatement_is_not_evaluated
zero eligible -> NOT_EVALUATED     test_zero_eligible_is_not_evaluated_not_pass
injected STABLE -> ACCELERATING    test_injected_regression_stable_to_accelerating_fails
```

Updated rather than deleted, each naming what it used to assert:

```
test_numeric_roles.py
  test_state_token_matching_is_unchanged
    -> test_state_token_is_no_longer_answered_by_the_numeric_matcher
    +  test_the_state_question_did_not_disappear_with_the_branch

test_d4_s_m8_compound_coverage.py
  test_every_one_of_the_six_is_currently_flagged
    -> test_every_one_of_the_six_is_closed_by_d4_h
  test_a_state_token_claim_passes_when_its_sentence_happens_to_have_no_digit
    -> test_the_verdict_no_longer_turns_on_whether_the_sentence_has_a_digit
  test_known_defect_state_token_branch_is_also_a_false_negative
    -> test_the_false_negative_half_of_the_finding_is_now_a_named_gate
  test_the_window_pair_leaves_its_first_digit_unmasked
    -> test_the_window_pair_is_now_one_session_count_span
  test_a_spaced_month_window_pair_leaves_both_digits_unmasked
    -> test_a_spaced_month_window_pair_is_now_one_session_count_span
  test_the_stored_records_still_yield_exactly_these_six
    -> test_the_stored_records_no_longer_yield_any_of_the_six
    +  test_the_inline_six_still_match_the_stored_records_verbatim
```

That file's `MARGIN_RATIOS` - the measured distance from each surviving token to the nearest reading
of its fact - is kept as the audit's record and is **no longer recomputable**, which is the repair
rather than a loss: the tokens it measured are now classified as the window labels they are, so zero
`VALUE_RESTATEMENT` tokens survive and there is no distance left. Recomputing it would mean
reintroducing the pattern R2 removed, which is what `replay_d4_3r_audit_alignment` already refuses to
do for D4.3A's old detectors. The stronger post-repair fact is asserted directly instead.

Its drift guard was rebuilt so it does not depend on the matcher: each of the six claim texts must
still be present verbatim at its stated path, and each must still cite the stated code fact at the
stated value. That is a stronger guard than re-deriving through `fact_is_restated`, which is no longer
possible.

`test_both_historical_m8_verdicts_are_unchanged` and
`test_the_unsourced_seventeen_result_is_preserved` were **not** touched and still pass.

---

## K. Tier B Readiness

```
H-V2-D4-H  = NEEDS REVISION
Tier B     = NOT AUTHORIZED   (unchanged)
D5         = NOT READY        (unchanged)
```

Not written as a Tier B contract proposal, because §15 makes that conditional on integrity hardening
PASSing and it did not. What the proposal will have to declare, once the state gate is revised:

```
M8 scope
  atomic claims only
  + R1 corrected coverage (STATE_TOKEN is not M8's question)
  + R2 corrected coverage (coordinated / elided window labels)

M8 compound set-valued semantics (R3)
  = DEFERRED, 0 observed occurrences, structurally present

CODE_OWNED_STATE_FIDELITY
  = revised contract per below
  = enforcement point (validator vs audit) decided explicitly
```

The two revisions the replay identified, to be **preregistered before they are implemented**, so that
neither is a repair made after seeing which findings it removes:

**S2 - widen the owned set from the claim's citations to the candidate's own state facts.** Rationale
independent of the counts: the question the gate asks is "did the model introduce a state the pipeline
does not own", and the pipeline's ownership is the candidate's `fundamental_changes` block, not
whichever subset one claim happened to cite. The narrower set makes the gate a citation-completeness
check wearing a fidelity check's name. Removes 5 of the 8 tokens. The citation-completeness question
is real and belongs to E2/M8's sourcing layer, not here.

**S1 - a negation scope, or ineligibility under negation.** Rationale independent of the counts:
§9's own case 4 requires a lowercase rendering to count, and lowercase `improving`/`accelerating` are
ordinary English, so the gate cannot currently distinguish "X is ACCELERATING" from "X is not
accelerating". The choice between narrowing the window and treating a negated mention as ineligible is
a contract decision and needs stating before it is coded. Removes the remaining 3.

Predicted post-revision measurement on the same corpus: 0 violations of 38 eligible. **That prediction
is what makes the revision falsifiable**, and it must be checked by re-running this replay after the
revision lands rather than assumed.

Until then, the gate stands as implemented, adjudicated offline, and declared in no verdict.

---

## L. Budget Status

Nothing set, nothing approved, nothing spent.

```
D4-H live cost                 $0.00
D4-H live Opus calls           0

Tier B observed projection     ~$16.59     reference only, carried in unchanged
Tier B theoretical worst case  $45.60      reference only, carried in unchanged
Tier B hard cap                NOT SET, NOT PROPOSED
```

A new independent Tier B hard cap is due after integrity hardening PASSes, and integrity hardening did
not PASS, so no cap is proposed here. `d4_2_contract`'s own recorded arithmetic is untouched: Tier B's
$45.60 worst case does not fit the $25.46 remaining on the $30.00 authorization, and
`TIER_B_FITS_REMAINING_CAP` is still asserted False in code.

---

## M. Verdict

```
H-V2-D4-H
= NEEDS REVISION
```

R1 and R2 are closed and accepted on their frozen criteria, measured on the real corpus, with no M
gate moved and no historical verdict edited. R3 remains explicitly deferred with its exposure
measured at 0. The state fidelity defect was diagnosed, its question was transferred out of M8 to a
gate built for it, and that gate catches the exact regression the brief required - but its first
measurement returned 6 false-positive claims of 38 eligible from two named mechanisms, which is not a
gate that may be declared active for Tier B. The revision is specified in §K and is a preregistration
decision.

This is not a Tier B execution authorization and not a Tier B budget approval.

```
live Opus calls?                          NO
live cost?                                $0
SCCO S1 verdict changed?                  NO
Final Tier A verdict changed?             NO
C1/C4 changed?                            NO
Gap semantics changed?                    NO
M8 core meaning changed?                  NO
R3 implemented?                           NO
State Fidelity added?                     YES  (audit-layer gate, wired to no M gate,
                                                not wired into the live validator)
Tier B executed?                          NO
Tier B budget approved?                   NO
D5?                                       NO
valuation?                                NO
APPROVE/WATCH/REJECT?                     NO
forward returns?                          NO
existing unrelated dirty files modified?  NO
push?                                     NO
```

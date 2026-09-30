# H-V2-D4-H1 - State Fidelity Semantic Repair

```
H-V2-D4-H1
= READY FOR TIER B BUDGET DECISION

S1  assertion polarity                        CLOSED
S2  metric-level authority                    CLOSED
M8  R1 / R2                                   CLOSED   (unchanged)
M8  R3                                        DEFERRED (unchanged)

CODE_OWNED_STATE_FIDELITY
= deterministic
= audit-adjudicated
= READY

live Opus calls   0
live cost         $0.00
```

The frozen prediction is confirmed on its own denominator, unedited:

```
predicted before the repair existed     38 eligible claims, 0 violations
measured after the repair               38 eligible claims, 0 violations
```

And it is not a vacuous zero. 41 of 49 state assertions across the frozen corpus were bound to a
code-owned metric and compared; all 41 passed. All 8 of D4-H's flagged tokens were located and each
closed through the mechanism it was attributed to.

This is a Tier B **budget decision** readiness statement. It is not a Tier B execution authorization,
not a budget approval, and not a result about interpretation quality.

---

## A. Why H1 Exists

D4-H closed M8's two coverage defects (R1, R2) and moved the categorical-state question out of the
numeric matcher into a gate built for it. That gate then failed its own first measurement:

```
eligible state claims   38
reported violations      6
unowned tokens           8

true fabricated / upward state misstatements   0
```

Eight flagged tokens, none of them a real defect. D4-H reported that rather than repairing it, because
repairing a gate after reading which findings the repair removes makes the gate's acceptance criteria
unfalsifiable. Instead it named the two mechanisms, specified the revision, and wrote down a
prediction that the revision would have to meet:

> Predicted post-revision measurement on the same corpus: 0 violations of 38 eligible. **That
> prediction is what makes the revision falsifiable**, and it must be checked by re-running this
> replay after the revision lands rather than assumed.

H1 implements that revision and measures it. The prediction is not edited, and the denominator it was
made about is not swapped for a friendlier one (§H).

---

## B. D4-H Immutable Baseline

```
branch                          main
HEAD at preflight               44ef89c  fix(strategy-h-v2): close the M8 integrity blind spots before Tier B
HEAD at write-up                9c664ad  test(crypto): cover the uncapped live sizing policy
origin/main                     738bdc1  refactor(strategy-h-v2): simplify expectation claim validation
origin/main vs HEAD             ahead 6 / behind 0
staged at preflight             0
dirty at preflight              21 modified, 196 untracked
H-V2 related dirty at preflight 0
```

**HEAD moved during this session, from a concurrent session, and this is reported rather than smoothed
over.** `9c664ad` touches `backend/app/crypto/live/sizing.py` and its test. The semantic question was
checked rather than assumed:

```
git diff --stat 44ef89c..HEAD -- backend/app/backtest/strategy_h_v2 \
    backend/app/dev/audit_strategy_h_v2_d4_{1,2}.py \
    backend/app/dev/run_strategy_h_v2_d4_2.py docs/backtest/strategy_h_v2   -> empty
```

No H-V2 source or document has moved since the D4-H commit. The 21 pre-existing dirty files are
unrelated to H-V2 and were neither modified nor staged.

### What H1 may not touch, and did not

```
D4.3A Tier A V2                  MECHANICAL NOT READY, document unedited, gateaudit.json unedited
Final Tier A V3                  MECHANICAL READY,     document unedited, gateaudit.json unedited
D4-S1 SCCO limited live smoke    STRONG PASS,          document unedited
D4-H original measurement        38 eligible / 6 violations / 8 tokens / 0 true defects
                                 document unedited, replay artifact unedited on disk
```

D4-H's own artifact, `d4_h_integrity_hardening_replay-20260930T042900Z.json`, stays as the record of
what the V1 contract measured. H1's measurement is a separate artifact under a separate schema
(§I). `d4_2_contract.M_GATES` is still frozen at M1-M12 and this gate is still wired to none of them,
so no Tier A or D4-S1 verdict can move either way.

### Frozen corpus (§1)

The same stored outputs D4-H used, and no new live output:

```
D4_2_A-20260929T072105Z   D4.3A Tier A V2                  SCCO GOOG BSY
D4_2_A-20260930T012115Z   Final Tier A V3                  SCCO GOOG BSY
D4_2_A-20260930T034348Z   D4-S1 SCCO limited live smoke     SCCO
```

---

## C. S1 Root Cause

V1's question was *is every state token the claim NAMES owned by a state fact the claim CITES?*
Naming and asserting are not the same act, and V1 had no way to tell them apart.

The trap is that it cannot be fixed by case. `IMPROVING`, `ACCELERATING`, `STABLE`, `INCREASING` and
`DECREASING` are ordinary English adjectives, and the D4-H brief's own §9 case 4 requires a lowercase
rendering to count as a restatement - a claim writing "revenue is accelerating" against a `STABLE`
fact is exactly what the gate exists to catch. So the gate must read lowercase state words, and
lowercase state words appear in denials as often as in assertions:

| claim text | V1 read | actually |
|---|---|---|
| `... continuing at its historical pace, not accelerating ...` | ACCELERATING restated | denied |
| `The reality side is mixed rather than improving.` | IMPROVING restated | contrasted against |
| `The reality side is mixed rather than uniformly improving.` | IMPROVING restated | contrasted against |
| `Top-line growth is steady rather than improving: ...` | IMPROVING restated | contrasted against |

Three of the eight flagged tokens are this alone; a fourth is this plus S2.

### What the repair may not be

The cheap version - "if the sentence contains `not`, ignore the states in it" - would make the gate
trivially satisfiable by adding a negation anywhere in a claim. The H1 brief §4 names this directly:

```
"Revenue is not stable and is accelerating."

STABLE        = NEGATED
ACCELERATING  = ASSERTED
```

So polarity has to be read per occurrence, with a scope.

---

## D. Polarity Contract

Three values, per state-token occurrence:

```
ASSERTED                  the claim says the metric's state IS this. The only polarity ever compared.
NEGATED                   the claim says it is NOT this            ("not accelerating")
REJECTED_OR_CONTRASTED    the claim names it to set something against it
                                                                  ("stable rather than accelerating")
```

Read from the text between the token and the nearest preceding **polarity reset**:

```
reset    . ; : ,   while whereas although though alongside but so because since however yet   and or
visible  rather than / instead of / as opposed to / unlike / not X but        -> REJECTED_OR_CONTRASTED
visible  not no never neither nor without nothing absent lacks lacking
         fails to / failed to / cannot / n't                                 -> NEGATED
neither                                                                      -> ASSERTED
```

`and` and `or` reset polarity but do **not** split a metric-binding segment (§F), and the asymmetry is
the point: coordination carries a subject forward while it does not carry a negation forward. That is
what makes §4's mixed sentence come out right, and it is why the scope is a clause rather than a
sentence.

Contrast is checked before negation because it is the more specific, multi-word reading of the same
"this is not the state" intent, and because `rather than` is how every instance in the frozen corpus
was written. The marker does not need to be adjacent to the token: `rather than uniformly improving`
resolves.

### What polarity is not

A filter. A NEGATED or REJECTED occurrence is still **found**, still reported with its span and its
reason, and counted in the polarity totals. It is `NOT_EVALUATED`, never a silent PASS - and a
negation in a neighbouring clause never reaches across a reset to excuse an assertion in this one.

### Not handled, recorded rather than assumed

Post-positioned negation (`Revenue accelerating? No.`), and negation that is carried by a verb rather
than a negator (`denies that revenue is accelerating`). Neither appears in the frozen corpus.

---

## E. S2 Root Cause

V1 derived the *authority* for a categorical comparison from `claim.evidence_ids`. That made a claim
restating a metric's state **correctly** into a violation whenever it had cited some other metric's
state fact:

| run | ticker | claim | token | the candidate's own state |
|---|---|---|---|---|
| D4.3A | BSY | `gap_rationale[2]` | DETERIORATING | `eps_diluted = DETERIORATING` |
| D4.3A | GOOG | `gap_rationale[1]` | IMPROVING | `revenue`, `eps_diluted`, `operating_margin` = IMPROVING |
| D4.3A | GOOG | `gap_rationale[6]` | IMPROVING | same three |
| V3 | BSY | `gap_rationale[2]` | DECELERATING | `operating_income = DECELERATING` |
| V3 | BSY | `gap_rationale[2]` | DETERIORATING | `eps_diluted = DETERIORATING` |

Five of the eight. Every one names a state the pipeline genuinely owns. V1 was therefore not measuring
state fidelity at all in these cases; it was measuring **citation completeness** while reporting it as
a fidelity defect. Citation completeness is a real obligation and it already has a contract - E2's
sourcing layer - which is where a claim that under-cites belongs.

---

## F. Metric-Level Authority

The comparison authority is now:

```
authoritative_state[candidate][metric]
```

read from the candidate's own `fundamental_changes` block - the same source
`code_facts.build_code_fact_index` reads its `STATE_TOKEN` facts from, so the two cannot disagree about
what the pipeline owns. The metric namespace is whatever that block's keys are; no metric list is
restated in the gate. `pipeline._candidate_result` builds it from six metrics:

```
revenue   operating_income   eps_diluted   free_cash_flow   operating_margin   shares_outstanding
```

### Binding is mandatory, not a convenience

"The candidate owns this token somewhere" is **not** sufficient, and making it sufficient would have
turned S2's repair into a hole large enough to drive a fabricated state through. The H1 brief §7 states
the test case and the gate implements it:

```
revenue           = IMPROVING
operating_income  = DECELERATING

claim: "Operating income is improving."      -> FAIL
```

GOOG holds IMPROVING under three of its six metrics. That claim still fails, because binding resolves
to `operating_income` and IMPROVING is not DECELERATING.

### How a metric is resolved (§8)

```
structured metric id on the claim   does not exist - `ClaimV2` carries no metric field
metric identifier in the prose      `eps_diluted`, `operating_income`, ...       -> highest priority
standard rendering of it            `EPS`, `diluted EPS`, `earnings per share`, `FCF`
anything else                       UNKNOWN -> NOT_EVALUATED
```

Two stages, on the token's own clause. The clause is bounded by `. ; : ,` and
`while / whereas / although / though / alongside / but / so / because / since / however` - narrower
than the polarity reset, because `and` must not split `EPS is deteriorating and free cash flow is at a
negative inflection` into halves that lose their subjects, while it must not join
`Revenue is not stable and is accelerating` into an ambiguous pair either. If the clause names exactly
one metric, the binding is that metric - whether the metric precedes the state (`EPS is
deteriorating`) or follows it (`DECELERATING operating income`), because a clause is about one metric
written either way. If it names two or more, coordination is the only thing allowed to separate them:
the clause is sub-split on `and`/`or` and the piece holding the token is re-read. Still more than one,
or none, and the answer is that there is no answer.

### Where the alias table stops, and why

```
IS an identifier      revenue / revenues, operating income, EPS / diluted EPS / earnings per share,
                      free cash flow / FCF, operating margin, shares outstanding / share count,
                      plus every metric key verbatim (`eps_diluted`, `free_cash_flow`, ...)

IS NOT                "the operating line", "top-line", "fundamentals", "ARR growth",
                      "growth durability", "Cloud growth", "margins"
```

`EPS` is the identifier `eps_diluted` written the way a filing writes it. "the operating line" is prose
*about* a metric and could be operating income or operating margin; "fundamentals" is not a metric;
"Cloud growth" is a segment, and the code owns a consolidated `revenue` state and nothing about Cloud.
Mapping any of them would be the forced matching §8 forbids, so an assertion whose clause names none of
these is `NOT_EVALUATED` - the honest answer, and not a PASS.

**One implementation gap, found and closed, and worth recording for how it was found.** The first H1
implementation listed only the prose aliases, which left the metric identifier itself unrecognizable in
the one form §8 ranks highest: `\beps\b` cannot match inside `eps_diluted`, because the underscore is a
word character. Five claims of the frozen corpus write `The code-owned fundamental change state for
eps_diluted is DETERIORATING` verbatim, and all five came back `NO_METRIC_NAMED`. They surfaced by
auditing the **NOT_EVALUATED** list rather than the violations - the half of a gate's output that is
easy not to read, and where a gate quietly declining to evaluate the clearest cases it has makes a
0-violation result weaker than it looks. The alias set is now derived from the metric key, so this
cannot recur for a metric added later. Closing it moved 5 assertions from NOT_EVALUATED to PASS and
changed no verdict; had any of the five been wrong it would have produced a violation, which §13 would
have required reporting rather than tuning away.

### Outcome per assertion (§9)

```
PASS            ASSERTED, bound, and equal to the metric's authoritative state
FAIL            ASSERTED, bound, and different from it
NOT_EVALUATED   POLARITY_NOT_ASSERTED     the token is denied or contrasted
                NO_METRIC_NAMED           its clause names no code-owned metric identifier
                AMBIGUOUS_METRIC          its clause names several and coordination does not separate
                METRIC_NOT_CODE_OWNED     the candidate has no such metric
                AUTHORITY_UNKNOWN         the candidate's own state for it is UNKNOWN
```

`AUTHORITY_UNKNOWN` is a deliberate choice: `UNKNOWN` is the **absence** of a code-owned categorical
truth, not a competing one, so there is nothing for an assertion to contradict. Whether a directional
claim over an UNKNOWN metric is properly sourced is E2's question, not this gate's.

### What this contract still does not claim

It binds a state to a metric, which V1 could not. It does not read a metric's state out of a clause
that names the metric only by description, and it does not resolve a segment-level statement to a
consolidated metric. Both are reported as `NOT_EVALUATED` with a reason rather than guessed, and both
are visible in the artifact rather than folded into a total.

---

## G. Provenance vs State Authority

Stated as its own section because it is the conceptual content of the S2 repair, not a side effect:

```
evidence_ids                = provenance. WHERE a claim says its support is.
code-owned metric state     = authority.  WHAT the pipeline holds to be true, categorically.
```

`CODE_OWNED_STATE_FIDELITY` compares against the second and says nothing about the first. Two
consequences, each asserted by a test:

- A claim that states its metric's state correctly **passes** whether or not it cited that metric's
  state fact. A claim that cites badly is E2's finding.
- A claim that cites exactly the right state fact and then contradicts it **fails**. The separation is
  not a loosening: the authority got wider and the comparison got stricter at the same time.

D4-H's claim-level eligibility rule is still computed, unchanged, because its frozen prediction is
defined in terms of it - see §H. It is no longer the authority for anything.

---

## H. Frozen Prediction

D4-H's prediction is about **its own denominator**: 38 *eligible claims* under V1's claim-level rule
(cites >= 1 `STATE_TOKEN` fact **and** names >= 1 recognized state token). H1 adjudicates per
**assertion**, over all material claims, and no longer requires a cited state fact. Those are different
denominators, and substituting one for the other would have made the prediction unmeasurable while
appearing to satisfy it.

So both are computed and both are reported. `StateFidelityFinding.d4_h_eligible` keeps V1's rule
verbatim, and the prediction is checked against a literal the replay module carries
(`FROZEN_PREDICTION`) rather than a figure retyped in a test:

```
predicted    d4_h_eligible_claims = 38    d4_h_violating_claims = 0
measured     d4_h_eligible_claims = 38    d4_h_violating_claims = 0
confirmed    true
```

The 38 is still 38, so the prediction was measured on the 38 claims it was made about.

---

## I. Offline Replay

`backend/app/dev/replay_d4_h1_state_fidelity.py`. Zero live calls, $0.00. Artifact:
`data/runtime/strategy_h_v2/d4_2/replay/d4_h1_state_fidelity_replay-20260930T045256Z.json`, schema
`H_V2_D4_H1_OFFLINE_REPLAY_V1`.

`replay_d4_h_integrity_hardening.py` was narrowed to R1/R2 and the M-gate immutability check, and no
longer computes a state measurement: re-measuring a revised gate under D4-H's schema name would publish
different numbers for the same schema, and D4-H's artifact on disk is the record of what V1 measured.

### Assertion level

```
claims naming a state      41
assertions total           49
assertions evaluated       41        (bound to a metric and compared)
assertions passed          41
assertions failed           0

NOT_EVALUATED               8        POLARITY_NOT_ASSERTED  5
                                    NO_METRIC_NAMED        3
```

84% of assertions were actually compared. That figure is the point of reporting it: a gate that
declines to evaluate everything also reports zero violations, and this one does not.

### Per candidate

| run | ticker | claims | assertions | evaluated | passed | failed | status |
|---|---|---|---|---|---|---|---|
| D4.3A | BSY | 7 | 9 | 6 | 6 | 0 | PASS |
| D4.3A | GOOG | 6 | 8 | 6 | 6 | 0 | PASS |
| D4.3A | SCCO | 5 | 5 | 5 | 5 | 0 | PASS |
| V3 | BSY | 6 | 9 | 8 | 8 | 0 | PASS |
| V3 | GOOG | 7 | 8 | 6 | 6 | 0 | PASS |
| V3 | SCCO | 5 | 5 | 5 | 5 | 0 | PASS |
| D4-S1 | SCCO | 5 | 5 | 5 | 5 | 0 | PASS |

Every candidate reaches PASS with a non-empty evaluated denominator, which is what separates a PASS
from a `NOT_EVALUATED` in this gate.

### D4-H's eight, each resolved

| # | run | ticker | path | token | V1 mechanism | polarity | metric / authority | outcome |
|---|---|---|---|---|---|---|---|---|
| 1 | D4.3A | BSY | `improvement_claims[4]` | ACCELERATING | S1 | NEGATED | - | NOT_EVALUATED |
| 2 | D4.3A | BSY | `gap_rationale[2]` | IMPROVING | S1 | REJECTED | - | NOT_EVALUATED |
| 3 | D4.3A | BSY | `gap_rationale[2]` | DETERIORATING | S2 | ASSERTED | `eps_diluted` / DETERIORATING | **PASS** |
| 4 | D4.3A | GOOG | `gap_rationale[1]` | IMPROVING | S1+S2 | REJECTED | - | NOT_EVALUATED |
| 5 | D4.3A | GOOG | `gap_rationale[6]` | IMPROVING | S2 | ASSERTED | no metric named | NOT_EVALUATED |
| 6 | V3 | BSY | `improvement_claims[3]` | IMPROVING | S1 | REJECTED | - | NOT_EVALUATED |
| 7 | V3 | BSY | `gap_rationale[2]` | DECELERATING | S2 | ASSERTED | `operating_income` / DECELERATING | **PASS** |
| 8 | V3 | BSY | `gap_rationale[2]` | DETERIORATING | S2 | ASSERTED | `eps_diluted` / DETERIORATING | **PASS** |

```
located                8 / 8
still a violation      0 / 8
```

Each closed through the mechanism it was attributed to, which is the check that distinguishes a
semantic repair from a suppression: an S1 token closes because its polarity is not an assertion, and an
S2 token closes because the metric it is bound to actually holds the state it names. Case 5 is the one
that closes neither way - `fundamentals continued improving on the operating line` names no metric
identifier - and it is reported as `NO_METRIC_NAMED` rather than as a closure of S2.

**A lookup bug in the first version of this replay is recorded because of what it would have hidden.**
The resolution table was keyed on each run's prose label rather than its run id, so all five D4.3A rows
matched nothing and were reported with `still_a_violation: false` - a lookup miss reading as a closure.
The artifact now carries `located` per row and `d4_h_flagged_tokens_not_located` in the totals, and a
test asserts all eight are located, so that failure mode cannot recur silently.

### The eight NOT_EVALUATED, inspected individually

Reported because a gate hiding a real defect behind `NOT_EVALUATED` is the failure mode this section
exists to rule out.

```
POLARITY_NOT_ASSERTED  5
  "not accelerating"                                                          BSY  D4.3A
  "steady rather than accelerating growth"                                    BSY  D4.3A
  "The reality side is mixed rather than improving"                           BSY  D4.3A
  "The reality side is mixed rather than uniformly improving"                  GOOG D4.3A
  "Top-line growth is steady rather than improving"                           BSY  V3

NO_METRIC_NAMED  3
  "and fundamentals continued improving on the operating line"                GOOG D4.3A
  "which included the Cloud backlog step-up and accelerating Cloud growth"    GOOG V3
  "a negative reaction to Q2 2026 despite accelerating Cloud growth"          GOOG V3
```

All five polarity cases genuinely deny or contrast the state. The three unbound cases name no
code-owned metric: one describes the operating line, two are about a segment the code owns no state
for. None is a positive assertion about a metric whose authoritative state differs.

### §13 handling

```
surviving violations                          0
classification required                       none
rule expanded after reading a violation       no
```

---

## J. Tests

```
suite                1012 passed, 1 skipped   (D4-H baseline: 918 passed, 1 skipped)
new                  +94
live calls           0
```

Run from the repository root, which the stored-record tests' relative paths require.

New files:

```
backend/tests/strategy_h_v2/expectation/test_d4_h1_state_polarity.py     20 tests
backend/tests/strategy_h_v2/expectation/test_d4_h1_metric_authority.py   62 tests
backend/tests/strategy_h_v2/expectation/test_d4_h1_offline_replay.py     14 tests
```

Every case the H1 brief §18 requires:

```
negated state                          test_case_negated
contrast state                         test_case_contrast_asserts_one_and_rejects_the_other
mixed polarity                         test_a_negation_does_not_leak_across_a_coordination
                                       test_the_same_token_can_be_denied_in_one_clause_and_asserted_in_another
correct state, no state evidence_id    test_a_correct_state_passes_without_citing_that_states_evidence_id
                                       test_a_correct_state_passes_while_citing_no_state_fact_at_all
wrong metric state                     test_the_wrong_metric_fails_even_though_the_candidate_owns_that_state
correct metric state                   test_the_right_metric_passes
unknown metric binding                 test_prose_that_names_no_metric_identifier_is_not_evaluated
                                       test_two_metrics_in_one_unsplittable_clause_is_ambiguous_not_guessed
STABLE -> ACCELERATING injected        test_injected_regression_stable_to_accelerating_fails
same + unrelated number                ...fails_with_or_without_numbers  (4 numeric suffixes)
```

The D4-H fixtures are kept (§18). `test_d4_h_state_fidelity.py` carries all five of the D4-H brief's
§9 cases and its injected regression to the same outcomes under the new contract, and every test whose
reason changed says what it used to assert:

```
test_zero_eligible_is_not_evaluated_not_pass
  -> test_zero_comparable_assertions_is_not_evaluated_not_pass
test_a_claim_citing_no_state_fact_is_not_eligible
  -> test_ordinary_english_about_no_particular_metric_is_not_evaluated
test_a_state_fact_cited_alongside_numeric_facts_is_still_eligible
  -> test_a_state_assertion_beside_numeric_facts_is_still_compared
test_a_compound_claim_naming_a_subset_of_its_cited_states_passes
  -> test_a_compound_claim_restating_several_metrics_passes_on_each_of_them
test_one_unowned_state_among_owned_ones_still_fails
  -> test_a_wrong_state_among_correct_ones_still_fails
test_known_limitation_containment_does_not_bind_a_state_to_a_metric
  -> test_closed_limitation_a_metric_swap_is_now_caught          (inverted, not deleted)
```

That last one is worth calling out. V1 declared, as a known limitation, that a claim swapping which
cited metric holds which cited state would pass containment. H1's metric binding closes it, and the
test is kept in place, inverted, so the closure is visible where the limitation was recorded:
`"Revenue is DECELERATING and operating income is STABLE."` against `revenue = STABLE`,
`operating_income = DECELERATING` now fails on **both** assertions.

---

## K. R3 Status

```
M8 COMPOUND SET-VALUED SEMANTICS
= DEFERRED
```

Unchanged and not implemented. Its reason is unchanged too: it changes M8's unit of comparison rather
than its coverage. Measured exposure in the frozen corpus is still 0 occurrences, and it is still
pinned by a test so it cannot be mistaken for closed.

Worth noting what H1 did **not** do about it. The per-claim/per-fact mismatch that makes R3 hard is the
same shape as S2, and H1 resolved its own version of it by changing what the authority IS (the
candidate's metric namespace) rather than by changing the unit of comparison. That route is available
to this gate because it is new and has no frozen historical result; it is not available to M8, whose
scope two graded runs' verdicts were measured against.

### M8 separation (§14)

```
M8                          numeric only
CODE_OWNED_STATE_FIDELITY   categorical only
```

The `STATE_TOKEN` branch R1 removed from `numeric_roles.fact_is_restated` was not re-added, and
`validate._COMPARABLE_UNITS` still has no `STATE_TOKEN` entry. A test asserts M8 reports no numeric
defect for a state fact whether the state is right or wrong.

---

## L. Tier B Readiness

The H1 brief §17 conditions, each checked:

```
frozen prediction confirmed                          YES   38 eligible, 0 violations, unedited
no true categorical defects                          YES   0 of 49 assertions
no unresolved semantic blocker                       YES   8 NOT_EVALUATED, each with a structural
                                                           reason, each inspected, none a defect
R1 / R2 closed                                       YES   unchanged from D4-H; re-verified this step
R3 explicitly deferred                               YES   §K
State Fidelity deterministic                         YES   regex + enum + dict lookup; no model call,
                                                           no randomness, no threshold
```

```
H-V2-D4-H1 = READY FOR TIER B BUDGET DECISION
Tier B     = NOT AUTHORIZED, NOT EXECUTED
D5         = NOT READY
```

### What the Tier B contract must still declare

Not decided here, because each is a preregistration choice rather than an implementation one:

```
M8 scope                       atomic claims only, + R1/R2 corrected coverage
M8 R3                          DEFERRED, 0 observed occurrences, structurally present

CODE_OWNED_STATE_FIDELITY
  enforcement point            validator (rejects at generation) or audit (adjudicates after)
  gate wiring                  whether it joins the M-gate list for Tier B, and under which id
  NOT_EVALUATED disclosure     the per-reason breakdown is reported per candidate; Tier B should
                               state whether a high NO_METRIC_NAMED share is itself reportable
```

H1 keeps the gate **audit-adjudicated and wired to no M gate**, unchanged from D4-H and for the same
reason: wiring a rejection into `assemble_and_validate_d4` would change whether D4-S1's stored bytes
are VALID, which is a historical verdict and a live-behaviour change that belongs in a preregistered
contract rather than in a repair step.

### Budget

Nothing set, nothing approved, nothing spent.

```
H1 live cost                   $0.00
H1 live Opus calls             0

Tier B observed projection     ~$16.59     reference only, carried in unchanged
Tier B theoretical worst case  $45.60      reference only, carried in unchanged
Tier B hard cap                NOT SET, NOT PROPOSED
```

`d4_2_contract`'s recorded arithmetic is untouched: Tier B's $45.60 worst case does not fit the $25.46
remaining on the $30.00 authorization, and `TIER_B_FITS_REMAINING_CAP` is still asserted False in code.
A new independent Tier B hard cap is the next decision, and it is the user's.

---

## M. Verdict

```
H-V2-D4-H1
= READY FOR TIER B BUDGET DECISION
```

S1 and S2 are both closed, at the level of what the gate MEANS rather than which findings it emits.
Polarity separates asserting a state from denying one, with a clause scope that refuses the blanket
negation exemption. Authority moved from the claim's citations to the candidate's code-owned metric
namespace, with mandatory metric binding so the wider authority did not become a hole. The prediction
D4-H froze before either existed is confirmed on D4-H's own denominator, unedited, over an evaluated
denominator of 41 of 49 assertions - and all eight of D4-H's flagged tokens were located and closed
through the mechanism each was attributed to.

Two of this step's own defects are recorded above rather than quietly fixed: a metric-identifier
binding gap that made five of the clearest cases in the corpus `NOT_EVALUATED`, found by reading the
NOT_EVALUATED list instead of the violations; and a replay lookup keyed on a prose label that reported
five unlocated tokens as closures. Both are closed, both have tests, and both are the reason the
0-violation result should be read as measured rather than as arranged.

This is not a Tier B execution authorization and not a Tier B budget approval.

```
live Opus calls?                     NO
live cost?                           $0
D4-H original result modified?       NO
SCCO S1 result modified?             NO
Final Tier A result modified?        NO
C1/C4 changed?                       NO
Gap semantics changed?               NO
M8 numeric meaning changed?          NO
R3 implemented?                      NO
State Fidelity categorical gate?     YES  (audit-adjudicated, wired to no M gate)
Tier B executed?                     NO
Tier B budget approved?              NO
D5?                                  NO
valuation?                           NO
APPROVE/WATCH/REJECT?                NO
forward returns?                     NO
existing unrelated dirty modified?   NO
push?                                NO
```

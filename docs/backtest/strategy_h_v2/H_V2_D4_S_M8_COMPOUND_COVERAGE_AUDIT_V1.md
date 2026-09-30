# H-V2-D4-S - M8 Compound Claim Coverage Audit

```
M8 COMPOUND COVERAGE
= READY FOR LIMITED LIVE SMOKE

true numeric defects        0
matcher false positives     4
not numeric restatements    2
                            -----
total                       6

live Opus calls   0
live cost         $0.00
```

The six findings are not numeric-ownership defects. They are two matcher mechanisms firing on
numbers that are window LABELS rather than values, plus a third, latent mechanism that the stored
data does not exhibit but which blocks widening M8's scope until it is repaired.

Nothing in this step modified `numeric_roles.py`, M8's meaning, or M8's claim scope. The only file
added is a test file. Both historical M8 verdicts stand.

---

## A. Why This Audit Exists

D4-S found that `ClaimV2`'s COMPOUND citation form requires `source_id = null`, with provenance
carried by `evidence_ids[]`, while the old audit tested `not claim.get("source_id")` and called the
result "no citation". Correcting that removed all 17 (V3) and 19 (D4.3A) `unsourced_material_claims`
findings.

The same bug had a second effect. The `continue` that mislabelled those claims also carried them
past M8's numeric-ownership check, so M8 had never in any run been applied to a compound claim.
Widening the scope revealed six findings:

```
D4.3A             = 5
Final Tier A V3   = 1
                    ---
total               6
```

D4-S deliberately did not close this: widening a frozen gate's scope after its results exist would
have retroactively flipped both runs' M8 from PASS to FAIL. It reported the six in a gate-free audit
key, `compound_claim_coverage_gap`, for this step to adjudicate.

This audit answers one question: are those six real defects, matcher false positives, or claims that
are not M8's subject at all? It answers it from stored evidence only.

### Repository state

```
branch                        main
HEAD                          738bdc1  refactor(strategy-h-v2): simplify expectation claim validation
origin/main                   738bdc1  (identical to HEAD)
staged at preflight           0
dirty at preflight            217
H-V2 related dirty            0
```

**`origin/main` now equals HEAD, and the D4-S commit was pushed from outside this session.**
`git reflog show origin/main` reads `738bdc1 ... update by push`. This session did not push, in D4-S
or here; the push came from a concurrent session or from the user directly. Recorded because D4-S's
own declaration said `push? NO`, which remains true of that session's actions, and a reader
comparing the two facts is entitled to the explanation.

## B. Historical Scope Gap

What M8's PASS has always meant, stated precisely:

```
M8  "no claim cites a code-owned fact while stating a DIFFERENT number"
    measured over ATOMIC claims only, in every run to date
```

Before D4-S the compound form could not reach the check at all. So:

```
D4.3A   M8 = PASS     means "no atomic claim restates a code-owned number wrongly"
V3      M8 = PASS     means the same
```

Neither has ever meant more, and neither is revised here (§G).

## C. Six Cases

Six (fact, claim) pairs across **four** claims: two claims cite two code facts each and were flagged
on both. Every one of the four uses the compound form and cites **only** code facts.

| # | run | candidate | claim path | type | conf | cited fact | value | unit |
|---|---|---|---|---|---|---|---|---|
| 1 | D4.3A | GOOG | `gap_rationale[6]` | INFERENCE | LOW | `research_facts.fundamental_changes.operating_income.state` | `ACCELERATING` | STATE_TOKEN |
| 2 | D4.3A | SCCO | `market_expectation_evidence.price_reaction_reading[5]` | INTERPRETATION | LOW | `price_reaction.SEC:0001001838:0001104659-26-089169.return_1d` | -0.012378378378378296 | RETURN_FRACTION |
| 3 | D4.3A | SCCO | `market_expectation_evidence.price_reaction_reading[5]` | INTERPRETATION | LOW | `price_reaction.SEC:0001001838:0001104659-26-089169.return_3d` | 0.05491891891891898 | RETURN_FRACTION |
| 4 | D4.3A | SCCO | `gap_rationale[5]` | INTERPRETATION | LOW | `pre_event_price_context.drawdown_from_252s_close_high` | -0.13573054164770137 | RETURN_FRACTION |
| 5 | D4.3A | SCCO | `gap_rationale[5]` | INTERPRETATION | LOW | `pre_event_price_context.relative_strength_6m` | -0.051475145039365344 | RETURN_FRACTION |
| 6 | V3 | BSY | `gap_rationale[2]` | INTERPRETATION | MEDIUM | `research_facts.fundamental_changes.revenue.state` | `STABLE` | STATE_TOKEN |

Claim texts, verbatim:

```
[1] GOOG root.gap_rationale[6]
    "No large completed re-rating is evident that would indicate expectations ran ahead of
     evidenced progress. The stock is below its 252-session high, and fundamentals continued
     improving on the operating line, so the evidence does not support NEGATIVE either."
    evidence_ids: pre_event_price_context.drawdown_from_252s_close_high   (-0.14955894145950277)
                  research_facts.fundamental_changes.operating_income.state   ('ACCELERATING')

[2,3] SCCO root.market_expectation_evidence.price_reaction_reading[5]
    "Across the 1- and 3-session windows the 10-Q reactions point in opposite directions, so no
     clear reaction can be attributed to the small production guidance increase."
    evidence_ids: price_reaction.<...089169>.return_1d   (-0.012378378378378296)
                  price_reaction.<...089169>.return_3d   ( 0.05491891891891898)

[4,5] SCCO root.gap_rationale[5]
    "No evidence in the pool shows expectations running ahead of evidenced progress either. The
     shares are below their 252-session closing high, and relative strength over 3 and 6 months
     is negative."
    evidence_ids: pre_event_price_context.drawdown_from_252s_close_high   (-0.13573054164770137)
                  pre_event_price_context.relative_strength_6m            (-0.051475145039365344)

[6] BSY root.gap_rationale[2]
    "The reality side is mixed: D3 records durable, steady top-line growth alongside code-owned
     DECELERATING operating income, DETERIORATING EPS and INFLECTION_NEGATIVE free cash flow.
     That is not a clean improvement or a clean deterioration."
    evidence_ids: research_facts.fundamental_changes.revenue.state          ('STABLE')
                  research_facts.fundamental_changes.free_cash_flow.state   ('INFLECTION_NEGATIVE')
```

Two of the eight cited facts were NOT flagged, and both are instructive:

```
GOOG  drawdown_from_252s_close_high   passed   the claim states no value; "252-session" is masked
                                               as SESSION_COUNT, leaving zero value tokens
BSY   free_cash_flow.state            passed   the claim contains the literal token
                                               "INFLECTION_NEGATIVE"
```

### Provenance (§2)

`source_id = null` is the contract, not a defect. Verified per claim, and re-validated against
`ClaimV2` with the real valid-id context, which is stricter than the audit's own check:

| claim | citation form | source_id | n evidence_ids | all ids resolve | derived source resolves | ClaimV2 revalidate |
|---|---|---|---|---|---|---|
| GOOG `gap_rationale[6]` | COMPOUND | null | 2 | YES | YES | OK |
| SCCO `price_reaction_reading[5]` | COMPOUND | null | 2 | YES | YES | OK |
| SCCO `gap_rationale[5]` | COMPOUND | null | 2 | YES | YES | OK |
| BSY `gap_rationale[2]` | COMPOUND | null | 2 | YES | YES | OK |

Candidate isolation holds: a SCCO code fact id does not resolve inside GOOG's valid set, because
code facts are namespaced per bundle (`CODE:D4:EB-<run>-<ticker>`).

```
unresolved evidence ids   0
orphan source ids         0
cross-candidate leaks     0
```

## D. Numeric Role Analysis

Every numeric span in the four claim texts, as `classify_roles` labels it.

| claim | span | raw | role | D3.2 token |
|---|---|---|---|---|
| GOOG `gap_rationale[6]` | (129,140) | `252-session` | SESSION_COUNT | (masked) |
| SCCO `price_reaction_reading[5]` | (11,12) | `1` | **VALUE_RESTATEMENT** | value=1.0 unit=COUNT |
| SCCO `price_reaction_reading[5]` | (18,27) | `3-session` | SESSION_COUNT | (masked) |
| SCCO `gap_rationale[5]` | (114,125) | `252-session` | SESSION_COUNT | (masked) |
| SCCO `gap_rationale[5]` | (167,168) | `3` | **VALUE_RESTATEMENT** | value=3.0 unit=COUNT |
| SCCO `gap_rationale[5]` | (173,174) | `6` | **VALUE_RESTATEMENT** | value=6.0 unit=COUNT |
| BSY `gap_rationale[2]` | (27,29) | `D3` | IDENTIFIER | (masked) |

```
VALUE_RESTATEMENT tokens per claim
  GOOG gap_rationale[6]          []
  SCCO price_reaction_reading[5] ['1']
  SCCO gap_rationale[5]          ['3', '6']
  BSY  gap_rationale[2]          []
```

Note `10-Q` in case 2/3 produced no span at all: D3.2's own tokenizer excludes `10-K`/`10-Q` style
labels before a bare-digit scan runs, so it never reaches this module.

The surviving tokens are the cited facts' **own window labels**:

```
"1- and 3-session windows"        return_1d.description = "1-session event return (...)"
                                  return_3d.description = "3-session event return (...)"
"relative strength over 3 and 6
 months"                          relative_strength_3m.description = "over the same 63 sessions"
                                  relative_strength_6m.description = "over the same 126 sessions"
```

`_SESSION_COUNT`'s own comment anticipated exactly this:

> D4's own code facts documentation labels its return windows by session count, so a claim
> describing the same window in prose necessarily contains that same number, and it is not the
> number the fact evaluates to.

The intent was right. The pattern does not reach these two forms (§E, mechanism B).

Side observation, out of M8's scope and recorded rather than acted on: SCCO `gap_rationale[5]` says
"relative strength over 3 and 6 months is negative" while citing only `relative_strength_6m`.
`relative_strength_3m` exists and is -0.0289, so the statement is true of both; the uncited one is a
citation-completeness question for E2's A-type test, not a numeric-ownership one.

## E. fact_is_restated Trace

### Mechanism A - the STATE_TOKEN branch never consults the role classifier

```python
if unit == "STATE_TOKEN":
    return str(value).upper() in text.upper() or not any(c.isdigit() for c in text)
```

A raw character scan. `classify_roles` is not called, so D4.3R's exclusions do not apply and ANY
digit anywhere disables the "purely qualitative" escape.

```
case 1  GOOG  'ACCELERATING' in text?  False
              any digit in text?       True    <- "252-session", role SESSION_COUNT
              -> False (FLAGGED)
              VALUE_RESTATEMENT tokens: []     <- the role classifier already knew

case 6  BSY   'STABLE' in text?        False
              any digit in text?       True    <- "D3", role IDENTIFIER
              -> False (FLAGGED)
              VALUE_RESTATEMENT tokens: []     <- same
```

Remove the incidental digit and the identical qualitative statement passes:

```
fact_is_restated("Fundamentals continued improving on the operating line.",
                 "ACCELERATING", "STATE_TOKEN")   ->  True
fact_is_restated(GOOG_GAP_6, "ACCELERATING", "STATE_TOKEN")   ->  False
```

Nothing about the claim's relationship to the cited fact differs between those two. This is the same
defect class D4.3R repaired for the numeric branch, in a branch D4.3R did not touch.

**The more serious half, recorded because it is worse than the false positives.** The same scan is
also a false NEGATIVE:

```
fact_is_restated("Revenue is ACCELERATING.",                 "STABLE", "STATE_TOKEN")  ->  True
fact_is_restated("Revenue is ACCELERATING over 3 quarters.", "STABLE", "STATE_TOKEN")  ->  False
```

A genuine mis-restatement of a code-owned state token passes when the sentence has no digit, and the
same sentence fails once an unrelated digit is added. The branch is not merely over-strict; it is
keyed on something unrelated to the question it is asked. Not repaired here: fixing it would make M8
start enforcing state-token ownership properly, which is a change to what M8 enforces (§H, D1).

### Mechanism B - `_SESSION_COUNT` does not cover an elided coordination

```
_SESSION_COUNT = \b\d+(?:\.\d+)?[\s-](?:trading[\s-])?sessions?\b
               | \b\d+(?:\.\d+)?-(?:day|month|year)s?\b
```

The digit must be adjacent to its unit word, and the day/month/year form requires a HYPHEN.

```
"1- and 3-session"        "3-session" matches; the elided "1-" does not  -> "1" survives
"over 3 and 6 months"     needs "6-month"; a space does not match        -> "3","6" survive
```

Comparison for each surviving token, with each fact's own candidate readings and tolerances:

| case | fact | token | nearest candidate | gap | tolerance | ratio |
|---|---|---|---|---|---|---|
| 2 | `return_1d` -0.012378 | 1.0 | 1.2 | 0.20 | 0.05 | **4.0x** |
| 3 | `return_3d` 0.054919 | 1.0 | 5.49 | 4.49 | 0.0549 | 81.8x |
| 4 | `drawdown` -0.135731 | 6.0 | 13.57 | 7.57 | 0.1357 | 55.8x |
| 5 | `relative_strength_6m` -0.051475 | 6.0 | 5.15 | 0.85 | 0.0515 | 16.5x |

**Case 2 deserves to be read carefully rather than waved past.** `1` sits 0.2 from `1.2`, the
percentage-form reading of `return_1d` (-1.2378%), only 4x its tolerance. That proximity is a
coincidence of this quarter's reaction happening to be about -1.2%. The reading is still wrong: `1`
is the elided first half of "1- and 3-session windows", and `return_1d`'s own description is
"1-session event return". It is recorded as 4x, not as comfortably distant, because it is the one
case where a different tolerance could have changed the answer.

Written out rather than elided, the identical statements pass against the identical facts:

```
"Across the 1-session and 3-session windows the reactions diverge."     -> no value tokens, passes
"Relative strength over 3-month and 6-month windows is negative."       -> no value tokens, passes
```

The finding tracks English ellipsis, not anything about numeric ownership.

### Mechanism C - per-fact comparison against a per-claim citation (LATENT)

Found by writing the tests, not by reading the six. **Zero observed occurrences.**

`fact_is_restated(text, value, unit)` is called once per (claim, cited fact) and loops every value
token against that one fact. Its only escape is "no value token anywhere in the sentence". So a
compound claim has three outcomes:

```
states NO cited value      every cited fact passes (the escape)
states ALL cited values    every cited fact finds its own token, all pass
states SOME cited values   the silent facts are judged against a number that was never about
                           them, and are flagged
```

Only the middle-out case breaks, and it breaks because the comparison is per-fact while the citation
is per-claim. Demonstrated:

```
"The 1-session reaction was -1.24% while the 3-session window went the other way."
  return_1d (correctly restated as -1.24%)   ->  True
  return_3d (only a direction stated)        ->  False   <- flagged, and should not be
```

Measured exposure across both runs:

| run | candidate | compound claims | citing >= 2 code facts | of those, stating a value | mechanism C hits |
|---|---|---|---|---|---|
| D4.3A | BSY | 4 | 3 | 0 | 0 |
| D4.3A | GOOG | 6 | 3 | 0 | 0 |
| D4.3A | SCCO | 9 | 5 | 2 | 0 |
| V3 | BSY | 6 | 3 | 0 | 0 |
| V3 | GOOG | 4 | 2 | 0 | 0 |
| V3 | SCCO | 7 | 2 | 1 | 0 |

The three claims that cite two or more code facts AND state a value resolve as follows: the two
D4.3A SCCO claims state no MATCHING value (those are cases 2-5, mechanism B), and V3's SCCO
`priced_in_assessment.claims[1]` states EVERY cited value and passes cleanly:

```
"The last close of 189.88 USD compares with a 252-session low close of 106.88 USD, ..."
  pre_event_price_context.last_close       189.88   -> True
  pre_event_price_context.close_252s_low   106.88   -> True
```

So mechanism C is a structural exposure the two Tier A runs happen not to exhibit. It is reported as
latent, not as a defect found in the data, and it is the reason widening M8's scope is not a
one-line change.

## F. Classification

```
TRUE_NUMERIC_DEFECT        = 0
MATCHER_FALSE_POSITIVE     = 4
NOT_A_NUMERIC_RESTATEMENT  = 2
```

| # | candidate | cited fact | mechanism | classification |
|---|---|---|---|---|
| 1 | GOOG | `operating_income.state` = ACCELERATING | A | NOT_A_NUMERIC_RESTATEMENT |
| 2 | SCCO | `return_1d` = -0.012378 | B | MATCHER_FALSE_POSITIVE |
| 3 | SCCO | `return_3d` = 0.054919 | B | MATCHER_FALSE_POSITIVE |
| 4 | SCCO | `drawdown_from_252s_close_high` = -0.135731 | B | MATCHER_FALSE_POSITIVE |
| 5 | SCCO | `relative_strength_6m` = -0.051475 | B | MATCHER_FALSE_POSITIVE |
| 6 | BSY | `revenue.state` = STABLE | A | NOT_A_NUMERIC_RESTATEMENT |

Why each classification, against the brief's definitions:

**Cases 1 and 6 - NOT_A_NUMERIC_RESTATEMENT.** The cited fact is a `STATE_TOKEN` and carries no
number at all, so "numeric ownership" has no numeric subject here. Both claims are qualitative about
the state they cite, and both are qualitatively CORRECT: GOOG's "fundamentals continued improving on
the operating line" against `ACCELERATING`, and BSY's "durable, steady top-line growth" against
`STABLE`. The matcher produced no VALUE_RESTATEMENT token for either text, so it did not misread a
number as a restatement; it never asked. Classified by what the claim is, with the matcher defect
recorded separately in §E rather than used as the label.

**Cases 2 to 5 - MATCHER_FALSE_POSITIVE.** Here the matcher DID identify a token as a
VALUE_RESTATEMENT candidate and compare it (`1`, `3`, `6`). Each is a window length, in the role
`_SESSION_COUNT` exists to exclude, and each claim states no value for the cited fact. That is the
brief's definition of a matcher false positive exactly: a number in a different role read as a
code-owned restatement.

**Zero TRUE_NUMERIC_DEFECT.** No claim among the six states a number that is a reading of its cited
fact. For the STATE_TOKEN pair the fact has no number; for the four numeric cases the measured
distance to the nearest fact reading is 4.0x to 81.8x the relevant tolerance, and every surviving
token is identifiable as a window label from the cited fact's own description.

## G. Historical Verdict Preservation

```
D4.3A   M8 = PASS   UNCHANGED
V3      M8 = PASS   UNCHANGED
M1-M12  12 PASS on both runs   UNCHANGED
```

M8's claim scope is still ATOMIC. `numeric_roles.py` was not modified. `audit_strategy_h_v2_d4_1.py`
was not modified. This step added one test file and one document, so there is no mechanism by which
either historical verdict could have moved, and a test asserts it directly.

Recorded as a **POST-HOC COVERAGE AUDIT**: it says what M8 would have found had its scope included
the compound form, and it does not restate what M8 did find.

D4-S's own results are also preserved (§9 of the brief):

```
unsourced_material_claims   0 on both runs        UNCHANGED
17/17 were COMPOUND provenance-format cases       UNCHANGED
unresolved evidence ids = 0                       UNCHANGED
```

Nothing here reclassifies any of the 17 back to an unsourced defect.

## H. Future M8 Scope

Preregistered now, before any live result exists.

### Proposed scope

```
M8 claim scope  =  ATOMIC  +  COMPOUND
keyed on        =  claim-level evidence_ids[]
```

Rationale: the compound form is not a lesser citation, and a numeric-ownership rule that any claim
can escape by using two evidence ids instead of one is not a rule. But the scope cannot widen as it
stands, because on today's matcher it would report 6 findings that this audit has shown are not
defects, and would additionally be exposed to mechanism C.

### Required repairs, and whether each changes M8's meaning

```
R1  STATE_TOKEN branch consults classify_roles instead of any(c.isdigit())
    fixes cases 1, 6
    changes M8's MEANING?  NO. Applying the role classifier is what D4.3R already
                           established for the numeric branch; this branch was missed.

R2  _SESSION_COUNT covers elided coordination ("1- and 3-session", "3 and 6 months")
    fixes cases 2, 3, 4, 5
    changes M8's MEANING?  NO. The pattern's own comment states this intent already.

R3  fact_is_restated compares a claim's value tokens against the SET of facts it cites
    fixes mechanism C (latent)
    changes M8's MEANING?  Arguably yes, and it is flagged as such: it changes HOW the
                           comparison is made, from per-fact to per-claim. Required before
                           the compound form can enter scope at all.
```

R1 and R2 are coverage corrections consistent with the frozen meaning. R3 is a comparison change and
must be approved explicitly, not folded in.

### Acceptance condition for the widening

```
after R1 + R2 + R3 land, an offline replay over BOTH stored runs must read

    compound_claim_coverage_gap    = 0
    code_owned_numeric_defects     = 0
    M1-M12                         = 12 PASS on both runs
    unsourced_material_claims      = 0 on both runs

and a mismatch fixture must still be caught:
    fact_is_restated("The 1-session reaction was -4.10%.", -0.012378, "RETURN_FRACTION") = False
```

If the replay does not read zero, the widening does not proceed, and the reason is reported rather
than accommodated by moving a tolerance.

### Scope for the next live smoke, both branches frozen now

```
BRANCH 1 (if R1+R2+R3 land and the acceptance condition above holds first)
    smoke M8 scope = ATOMIC + COMPOUND
    the smoke's M8 result is reported as covering both forms

BRANCH 2 (if the repairs do not land before the smoke)
    smoke M8 scope = ATOMIC, unchanged
    the smoke's M8 result is explicitly labelled ATOMIC-ONLY in its result document,
    and carries no claim about compound coverage
```

**Recommendation: BRANCH 2.** The smoke's stated purpose is serialization, schema, validator and
structured/prose alignment. Landing a matcher repair and a scope widening in the same step would
conflate two changes and leave any M8 reading ambiguous about which one produced it. R1/R2/R3 belong
in their own preregistered step with its own offline replay, which costs $0.

### Separate decision, not blocking either branch

```
D1  the STATE_TOKEN false negative (§E): "Revenue is ACCELERATING" against a fact whose
    value is STABLE passes, because the sentence has no digit.

    options
      (a) fix inside M8        -> M8 starts enforcing state-token ownership; M8 is named
                                  "numeric ownership", so this stretches the name
      (b) a separate gate      -> state-token ownership as its own check, M8 left numeric
      (c) document and leave   -> the hole stays open and is disclosed

    recommendation: (b). It is a real ownership rule and it is not a numeric one.
    This is a user decision and no option is taken here.
```

## I. Live Smoke Readiness

**Not executed. No live call was made in this step.**

```
M8 COMPOUND COVERAGE = READY FOR LIMITED LIVE SMOKE
```

Ready in the sense the brief's §12 asks for: the six findings are adjudicated, none is a true
defect, the mechanisms are identified and traced, both historical verdicts are intact, and the future
scope with its acceptance condition is preregistered before any live result exists. Under BRANCH 2
the smoke needs no change to M8 at all, so nothing in this audit blocks it.

Proposal carried forward unchanged from D4-S §L, narrowed per the brief's §13:

```
sample            SCCO only
candidates        1
model             claude-opus-5-5
per_call cap      $2.00      (frozen CandidateBudgetContract)
hard cap          $6.00
prompt / schema   h_v2_d4_expectation_gap_v2 / h_expectation_gap_analysis_v2   (UNCHANGED)
M8 scope          ATOMIC (BRANCH 2), labelled as such
GOOG              only if SCCO's result makes a second necessary
```

What the smoke cannot show: whether initial validity improves in general. One candidate is not a
rate.

Expected compound-claim exposure in the smoke, from SCCO's stored outputs: 7 to 9 compound claims,
of which 2 to 5 cite two or more code facts. So compound claims will appear, which is why their M8
treatment had to be settled before the smoke rather than after.

**The smoke has since run under BRANCH 2**, and its result is
`H_V2_D4_S1_SCCO_LIMITED_LIVE_SMOKE_RESULT_V1.md`. Measured exposure came in at the low end of the
estimate: 5 compound claims, of which 1 cites a code fact. `compound_claim_coverage_gap` read 0, so
that one claim would not have been flagged even under the widened scope. That is one observation on
one candidate and does not satisfy the acceptance condition above, which still requires R1+R2+R3 and
a zero-reading offline replay over both stored runs. M8 scope remains ATOMIC and this audit's verdict
is unchanged.

## J. Tests

```
before this audit   808 passed, 1 skipped
after               841 passed, 1 skipped      (+33, one new file, no source file modified)
```

```
.venv/bin/python -m pytest backend/tests/strategy_h_v2 -q
841 passed, 1 skipped
```

`backend/tests/strategy_h_v2/expectation/test_d4_s_m8_compound_coverage.py`

| group | what it pins |
|---|---|
| verdict totals | 0 / 4 / 2, six findings across four claims |
| all six currently flagged | the starting fact, so a repair visibly changes it |
| none is a true defect | measured margin ratios 4.0x / 81.8x / 55.8x / 16.5x |
| mechanism A | STATE_TOKEN cases have zero VALUE_RESTATEMENT tokens; digit removal flips the result |
| mechanism A false negative | named `test_known_defect_...` so a repair breaks a test that says why |
| mechanism B | the elided window digit survives; written-out forms pass |
| mechanism B | the flagged digits are the cited facts' own window labels |
| mechanism C | named `test_known_blocker_...`, with the all-values-stated boundary beside it |
| brief §10 fixtures | exact match, mismatch, fiscal period, session count, compound support |
| stored-artifact drift | re-derives the six from the real runs and matches the inline copies |
| provenance | compound form, `source_id` null, ids resolve, `ClaimV2` revalidates |
| §9 preservation | `unsourced_material_claims` still 0 on both runs |
| §7 preservation | both runs' M8 still PASS, all 12 gates still PASS |

The two known-defect tests are named so that landing R1 or R3 fails a test whose docstring states
exactly what was being protected and why it was left in place. They assert current behaviour, not
desired behaviour.

Two of these tests were wrong on first writing, and correcting them produced two of this audit's
findings: the `tolerance * 5` guard failed on case 2 and forced the honest 4.0x margin into the
record, and a compound-support assertion failed and revealed mechanism C.

## K. Verdict

```
M8 COMPOUND COVERAGE
= READY FOR LIMITED LIVE SMOKE

true numeric defects        0
matcher false positives     4
not numeric restatements    2
```

This is not a D4 quality PASS, not a Tier A re-verdict, not a Tier B authorization, and not evidence
that any expectation gap means anything.

What it says: the six findings D4-S surfaced are not numeric-ownership defects. Four are
`_SESSION_COUNT` failing to reach an elided coordination, so a window label was compared to a return
value. Two are the `STATE_TOKEN` branch scanning for raw digits instead of asking the role
classifier, on facts that carry no number at all. A third mechanism, latent and unobserved, blocks
widening M8's scope until the comparison becomes set-valued over a claim's cited facts.

What it does not say: it does not repair any of the three mechanisms, it does not widen M8's scope,
and it does not close the STATE_TOKEN false negative it uncovered, which is the most serious single
finding here and is a user decision (§H, D1).

### Final declarations

```
live Opus calls?                           NO
live cost?                                 $0.00
historical Tier A verdict changed?         NO   (D4.3A and V3 both still M8 PASS, 12/12)
D4-S verdict changed?                      NO   (still READY FOR LIMITED LIVE SMOKE)
C1 / C4 changed?                           NO
Gap semantics changed?                     NO
confidence semantics changed?              NO
M8 meaning changed?                        NO
M8 scope changed?                          NO   (still ATOMIC)
numeric_roles.py modified?                 NO
audit_strategy_h_v2_d4_1.py modified?      NO
result-driven threshold tuning?            NO   (no tolerance, threshold or pattern touched)
unsourced 17 result preserved?             YES  (still 0 on both runs)
live smoke executed?                       NO
Tier B?                                    NO
Tier B budget approved?                    NO
D5?                                        NO
valuation / fair value / target price?     NO
APPROVE / WATCH / REJECT?                  NO
forward returns?                           NO
existing unrelated dirty files modified?   NO
push?                                      NO
tests                                      841 passed, 1 skipped
```

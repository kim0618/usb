# H-V2-D4-S1 - SCCO Limited Live Smoke Result

```
H-V2-D4-S1
= STRONG PASS

initial valid     YES
repair rounds     0
live calls        1
live cost         $1.3940560  of a $6.00 hard cap

Tier B            NOT YET AUTHORIZED
D5                NOT READY
```

One issuer, one live call, no repair. The single question this smoke asked is answered, and answered
causally rather than by inference: the same live bytes are **INVALID** under the pre-D4-S contract
and **VALID** under D4-S, computed offline at $0 from the stored response (§I).

This is not an investment-quality result, not a Tier B generalization result, and not a rate. One
candidate is one candidate.

---

## A. Repository State

```
branch                         main
HEAD at preflight              235ac4b  test(strategy-h-v2): audit M8 compound claim coverage
HEAD at write-up               446ee38  feat(crypto): set the LIVE operating leverage policy to 10x
origin/main                    738bdc1  refactor(strategy-h-v2): simplify expectation claim validation
origin/main vs HEAD (write-up) behind 0 / ahead 2
staged at preflight            0
dirty at preflight             217
dirty at write-up              221
H-V2 related dirty             0  (at preflight AND at write-up)
```

Both named commits verified present in real history:

```
738bdc10  refactor(strategy-h-v2): simplify expectation claim validation      (D4-S)
235ac4b   test(strategy-h-v2): audit M8 compound claim coverage               (M8 compound audit)
```

**External push provenance.** `origin/main` is `738bdc1`, and `git reflog show origin/main` reads
`738bdc1 ... update by push`. Neither the D4-S session nor the M8 audit session nor this one pushed;
the push came from outside. Recorded as provenance only, and the semantic question was checked rather
than assumed:

```
git diff --stat 738bdc10..HEAD -- <all H-V2 source paths>   -> empty
235ac4b changed                                              -> 1 test file, 2 docs, 0 source files
```

So no H-V2 semantics moved between the D4-S commit and this smoke.

**HEAD also moved DURING this session, from a concurrent session, and this is reported rather than
smoothed over.** `446ee38` landed after the live call and during the write-up. It is a frontend
crypto commit:

```
frontend/components/crypto-live-arm.test.tsx   59 +-
frontend/components/crypto-live-terminal.tsx   26 +-
frontend/lib/crypto-live.ts                    17 +

git diff --stat 235ac4b..446ee38 -- <all H-V2 paths>   -> empty
```

H-V2 code, tests and docs are byte-identical across that move, so nothing this smoke measures was
touched. The dirty count rose from 217 to 221 as that session's own work landed and new untracked
files appeared; none of the change is this session's. In particular
`backend/app/backtest/baseline/runner.py` was dirty at preflight, is still dirty, and was never
opened here.

No existing unrelated dirty file was modified, staged or deleted at any point.

## B. Frozen Inputs

Read out of the repository, not transcribed.

```
D4 contract              h_v2_d4_contract_v2
D4.2 contract            h_v2_d4_2_contract_repair_v1
prompt version           h_v2_d4_expectation_gap_v2
schema version           h_expectation_gap_analysis_v2
validation contract      h_v2_d4_validation_contract_v2
gap contract             h_v2_d4_gap_contract_v1
expectation state        h_v2_d4_s_expectation_state_v1
consensus language       h_v2_d4_consensus_language_v2_2
```

Sample integrity:

```
TIER_A_TICKERS_UNCHANGED   ('SCCO', 'GOOG', 'BSY')
TIER_A_CHECKSUM            d47cc4fd30fd3d6f04738b811e401547abedf57c195f0c4c2f1b65af34e2f6ab
checksum verifies          YES
smoke sample               ('SCCO',)
SCCO in frozen sample      YES
GOOG auto-added            NO
```

The driver is `backend/app/dev/run_strategy_h_v2_d4_s1_smoke.py`. It reuses
`run_strategy_h_v2_d4_2.run_tier_a_v2` **unchanged**, passing a one-element sample and the
one-candidate cap. Nothing in D4-S, the expectation state, the consensus classifier or the M8 matcher
was touched, and the driver adjudicates no gate - `audit_strategy_h_v2_d4_2.audit_run` does that over
the stored records.

## C. SCCO Input

Every check ran before the first live call, at zero model cost.

| check | value |
|---|---|
| runtime package exists | YES |
| package sha256 | `8a7054f7a2d292c6852fc2ed417fdd1d40a97239106e94e9bdc30e8061698322` |
| ticker / CIK | SCCO / 0001001838 |
| evidence chunks / sources | 347 / 17 |
| every chunk's source resolves | YES (0 unresolved) |
| D3 object exists | YES |
| D3 attempt id | `SCCO-75bdb891-906c-4654-bb85-9e9eb2e1a956` |
| D3 output checksum | `61ee366ad65b3380798fd54795c786adf19947b807408b8b091144ee20eceeab` |
| D3 CIK matches package | YES |
| price panel sessions | 501 |
| ExpectationEvidence builds | OK |
| `ExpectationKnowledgeStateV1` builds | OK |
| code facts | 110 |
| valid evidence ids / source ids | 457 / 18 |
| references resolve | YES |
| D4 prompt builds | OK (system 38,197 / user 170,303 chars) |

```
INPUT_READY    live calls spent on the preflight = 0
```

### Input identity with Final Tier A V3, verified by checksum

The two figures that decide whether this is the same input are identical to V3's stored values:

```
input package checksum     00742863bf3d27ab58db21d1e922eafe58c716315cd07e71974ed756b4f67df5   = V3
D3 research output checksum 61ee366ad65b3380798fd54795c786adf19947b807408b8b091144ee20eceeab   = V3
```

The expectation bundle's own checksum differs because `bundle_id` is part of the bundle and is
per-run. Rebuilding this smoke's inputs under V3's bundle id reproduces V3's checksum exactly:

```
V3  bundle_id EB-D4_2_A-20260930T012115Z-SCCO   checksum 2dac443b68a340a6091c087b8a1157297ae58bbc1bdff644e3cde49aacda2096
S1  bundle_id EB-D4_2_A-20260930T034348Z-SCCO   checksum 97cfc9aeb9e97d541ea490d6c48c131471c7b5440961dec09de05ee8389ea4ad
S1 inputs rebuilt under V3's bundle_id       -> 2dac443b68a340a6091c087b8a1157297ae58bbc1bdff644e3cde49aacda2096   IDENTICAL
```

**One preflight number differs from V3's and the reason was chased down rather than glossed.** V3 §E
recorded SCCO's user prompt as 169,973 chars; this preflight measured 170,303. The prompt length is
an exact linear function of `bundle_id` length at **110 characters per bundle-id character** - one
occurrence per code fact - measured directly:

```
bundle_id len  1  ->  user 168,543
bundle_id len  2  ->  user 168,653
bundle_id len 11  ->  user 169,643
bundle_id len 17  ->  user 170,303      (this preflight, "EB-PREFLIGHT-SCCO")
bundle_id len 31  ->  user 171,843      (a real run id)

(169,973 - 168,543) / 110 = 13.00       -> V3's figure corresponds to a 14-char placeholder id
```

So the difference is which placeholder bundle id was in place when the count was taken, not an input
difference. `prompt.py` was last changed at `d78c52e`, before D4-S, and the bundle checksum above is
the authoritative identity check.

## D. Model

```
requested model    claude-opus-5-5     (manifest model_requested, and the runner's MODEL constant)
canonical model    claude-opus-5-5     (read from the response's modelUsage block)
model_mismatch     False
fallback used      NO
```

## E. Budget

The cap is derived in code, not retyped. The driver asserts it:

```python
SMOKE_HARD_CAP_USD = TIER_A_BUDGET.candidate_worst_case_budget_usd
assert SMOKE_HARD_CAP_USD == PER_CALL_MAX_BUDGET_USD * 3 == 6.00
```

```
per_call cap                 $2.00      (frozen CandidateBudgetContract)
max calls per candidate      3
candidate worst case         $6.00
smoke hard cap               $6.00
preflight reserve check      0.00 + 6.00 <= 6.00   -> admit
```

```
initial call cost            $1.3940560
repair cost                  $0.0000000
run total                    $1.3940560   of $6.00   (23.2%)
within candidate worst case  True
stopped_early                None
budget_exhausted             False
```

No cap was raised during the run and none was raised automatically. `overall_hard_cap_usd` is
`$30.00` and `remaining_cap_before_run_usd` was `$25.458606`, both carried unchanged from the
manifest contract.

## F. Structured Expectation State

The authoritative object, built by code from the bundle before the call and stored with the record:

```json
{
  "status": "UNKNOWN",
  "basis": [],
  "consensus_status": "SOURCE_NOT_AVAILABLE",
  "estimate_revisions_status": "SOURCE_NOT_AVAILABLE",
  "market_expectation_claim_allowed": false,
  "source_ids": [],
  "evidence_ids": [],
  "limitations": [
    "CONSENSUS expectation evidence is SOURCE_NOT_AVAILABLE: no market-expectation statement may rest on it",
    "ESTIMATE_REVISIONS expectation evidence is SOURCE_NOT_AVAILABLE: no market-expectation statement may rest on it"
  ],
  "contract_version": "h_v2_d4_s_expectation_state_v1"
}
```

The model's own reported statuses match it verbatim, which is what the structured contradiction rule
checks:

| field | model reported | authoritative state | match |
|---|---|---|---|
| `consensus_status` | SOURCE_NOT_AVAILABLE | SOURCE_NOT_AVAILABLE | YES |
| `estimate_revisions_status` | SOURCE_NOT_AVAILABLE | SOURCE_NOT_AVAILABLE | YES |

The free-text detector defined nothing about availability in this run. It was consulted only for
polarity, which is the whole of D4-S's design and is what §I measures.

## G. Initial Validity

The core diagnostic, and it is unambiguous.

```
initial parse                 PARSED
initial schema                valid
initial semantic validator    OK
initial_failure_codes         []
initial_failure_details       []

initial_valid = YES
```

```
final_status    OK
repair_rounds   0
live calls      1
```

For context, and labelled as context rather than as a comparison this tier may draw conclusions from:

| | V3 SCCO | S1 SCCO |
|---|---|---|
| initial validation | **FAILED** | **OK** |
| repair rounds | 1 | 0 |
| cost | $1.6023386 | $1.3940560 |
| `expectation_gap` | NEUTRAL | NEUTRAL |
| confidence | LOW | LOW |
| `priced_in_assessment` | LIKELY_PRICED | LIKELY_PRICED |
| applied contract rules | C4, C7 | C4, C7 |

The two responses are different bytes: a model call is stochastic and this is not a replay. What is
the same is the input (§C, by checksum) and the contract. The gap state, confidence, priced-in state
and applied rules coming out identical is recorded as an observation only; §15 of the brief makes gap
state not this smoke's subject, and n=1 supports no claim about it.

## H. Repair

```
repair rounds     0
repair cost       $0.00
```

No repair occurred, so there is no failure code, failure field, failure layer, repair prompt or
repair response to record. `MAX_REPAIR_ATTEMPTS` is 2 and was never approached.

Both prior D4-S-era concerns are therefore untested here rather than resolved: this run produced no
repair loop at all, so it says nothing about repair convergence.

## I. Expectation Language

This is the section the smoke exists for.

### The one sentence at issue, from the live response

```
root.supporting_claims[2].text
  "This is a management price expectation, not a market expectation."
```

```
frozen R2 classifier  (h_v2_d4_consensus_language_v2_2, classify_sentence)
  -> ASSERTED,  trigger 'market expectation noun phrase'

D4-S leakage guard
  -> RELEASED as honest absence  (affirmative findings = 0)
```

Why it is released, structurally rather than by phrase membership:

```
clause    "This is a management price expectation, not a market expectation."
trigger   'market expectation noun phrase'   span (46, 64)
negator   'not'                              span (40, 43)
frames    []                                 (no negated epistemic frame)
position  EXISTENCE - the negator governs the attribution's own referent phrase,
          with only " a " (function words) between negator end 43 and trigger start 46
```

The sentence explicitly denies that the thing under discussion is a market expectation. It attributes
nothing to the market. The release is correct, and it came from the EXISTENCE position of the
structural polarity rule, not from any absence-phrase list.

### The counterfactual, computed at $0 from the stored bytes

The pre-D4-S tree (`a91a41c`, the commit immediately before D4-S) was exported with `git archive` and
its validator run against **this run's own initial response**, unmodified:

```
validator module     <pre-D4-S export>/backend/.../expectation/validate.py
consensus contract   h_v2_d4_consensus_language_v2_1     (pre-D4-S)
expectation_state    module does not exist               (correct tree)
initial raw_text     36,159 chars
initial checksum     89caca90fec38b486643b7b82605bc4d6081f10d778ef1fe8518c2553c7f13ad
```

```
PRE-D4-S verdict on the SAME live bytes
  valid  = False
  errors = 1
    "a sentence attributes an expectation to analysts or the market ('market expectation noun
     phrase') while the expectation bundle reports consensus SOURCE_NOT_AVAILABLE, so there is no
     source that could support it: 'This is a management price expectation, not a market
     expectation.' ..."
```

```
D4-S verdict on the same bytes
  valid  = True
  errors = 0
```

So D4-S removed one repair round on a real live response, and the claim is causal rather than
inferential: identical bytes, two contracts, opposite verdicts, no model call spent to establish it.

### Leakage scan over the final output

```
frozen R2 would flag           1
D4-S guard flags (leakage)     0        <- no fabricated expectation
released as honest absence      1
```

### Honest uncertainty prose that the model actually wrote and the contract accepted

```
"Available evidence does not establish consensus expectations. No analyst-consensus or
 estimate-revision source is connected (SOURCE_NOT_AVAILABLE for both), so confidence is capped at
 MEDIUM under C4 and is reported here as LOW."
```

That limitation names the authoritative status token verbatim and passed. It is the structured state
restated in prose, which is what §8 of the brief requires to be allowed regardless of wording.

`unknown_fields` carried 8 entries, including `consensus_expectations` and `estimate_revisions`, so
the absence was reported in the structured channel as well as in prose.

## J. Consensus Discipline

```
M3   PASS   0 rejections of an honest consensus-absence statement
M4   PASS   0 asserted consensus expectations in the final output
     fabricated_consensus defects        0
     v1_impossible_consensus_rejections  0
     v2_consensus_rejections             0
```

No unsupported affirmative expectation appears anywhere in the output. The four shapes §9 of the
brief names ("The market expects X", "Consensus expects X", "Analysts expect X", "Investors
expect X") do not occur, and the one sentence that tripped the frozen trigger denies the attribution
rather than making it (§I).

## K. M8 Scope

```
M8 scope      = ATOMIC ONLY
COMPOUND M8   = NOT EVALUATED
```

Stated because the output does contain compound claims, and none of them is inside M8's current
scope:

```
citation forms in the final output
  ATOMIC     57
  COMPOUND    5
  NONE        3      (claim_type UNKNOWN, citation-exempt by schema)

COMPOUND claims citing at least one code fact   1     <- NOT covered by the current M8 PASS
```

```
M8   PASS   0 claims restating a code-owned number     (atomic scope)
code_owned_numeric_defects       0
compound_claim_coverage_gap      0                     (reported, wired to no gate)
```

`compound_claim_coverage_gap` reading 0 means the one compound claim citing a code fact would not
have been flagged even under the widened scope. That is a single observation on a single candidate
and is not evidence that widening the scope is safe: the `H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md`
acceptance condition, which requires R1+R2+R3 and a zero-reading offline replay over both stored
runs, is unchanged by this smoke.

No M8 matcher change, no scope expansion, and no R1/R2/R3 implementation was made here.

### STATE_TOKEN diagnostic (brief §14)

The latent `STATE_TOKEN` issue was neither repaired nor triggered. Recorded as a diagnostic, with no
effect on this verdict:

```
STATE_TOKEN facts available    6 of 110 code facts
STATE_TOKEN fact citations     5, all ATOMIC
```

| claim | fact | value | token in text | has digit | value tokens | `fact_is_restated` |
|---|---|---|---|---|---|---|
| `improvement_claims[0]` | `revenue.state` | ACCELERATING | YES | no | [] | True |
| `improvement_claims[1]` | `operating_income.state` | ACCELERATING | YES | no | [] | True |
| `improvement_claims[2]` | `eps_diluted.state` | ACCELERATING | YES | no | [] | True |
| `improvement_claims[3]` | `operating_margin.state` | IMPROVING | YES | no | [] | True |
| `improvement_claims[4]` | `free_cash_flow.state` | IMPROVING | YES | no | [] | True |

Every one states its token verbatim and carries no digit, so neither the false-positive half nor the
false-negative half of the `STATE_TOKEN` branch could fire. The issue remains open and remains a
separate gate proposal.

## L. C1

```
C1   eligible 0    violations 0    NOT_EVALUATED
C4   eligible 1    violations 0    PASS
C5   eligible 1    violations 0    PASS
C6   eligible 1    violations 0    PASS
```

`expectation_gap = NEUTRAL`, which is not a POSITIVE state, so C1 was never exercised. That is the
frozen outcome and it is not evidence that C1 works. No prompt, sample or output was steered toward a
POSITIVE gap to exercise it (brief §15).

```
expectation_gap             NEUTRAL
expectation_gap_confidence  LOW      (ceiling MEDIUM, applied via C4)
priced_in_assessment        LIKELY_PRICED
applied_contract_rules      C4_NO_CONSENSUS_CONFIDENCE_CEILING, C7_PRICED_IN_NEEDS_EVIDENCE
d6_approve_precondition     BLOCKED
```

C4's MEDIUM ceiling applied, as D4 predicts it will for every issuer in the current data layer.

## M. Telemetry

```
run_id             D4_2_A-20260930T034348Z
analysis_id        SCCO-b6ff31f2-3667-4f8b-a329-f900bcdbd03d
started_at         2026-09-30T03:43:55.738104+00:00
completed_at       2026-09-30T03:46:28.536337+00:00
duration           152.8s
live calls         1
preview fields     NOT used - full raw text stored
telemetry_complete True   (M10 PASS)
```

```
initial raw_text        36,159 chars (measured from the stored text)
initial checksum        89caca90fec38b486643b7b82605bc4d6081f10d778ef1fe8518c2553c7f13ad
final output checksum   22d867286fec3733fc1f8aba30292d5b0acb7a2e7e9837e79185af7a66d0d56c
input package checksum  00742863bf3d27ab58db21d1e922eafe58c716315cd07e71974ed756b4f67df5
research output cksum   61ee366ad65b3380798fd54795c786adf19947b807408b8b091144ee20eceeab
expectation evidence    97cfc9aeb9e97d541ea490d6c48c131471c7b5440961dec09de05ee8389ea4ad
```

```
output_tokens   19,701   (measured)
input_tokens    2        (NOT the total input)
```

`input_tokens = 2` against a ~170,000 character prompt is the known cache-accounting limitation
carried unchanged from D3.3, D4.1 and V3: `extract_usage` reads the right key but the response does
not aggregate cached input. Recorded as measured rather than corrected or estimated.

The run's `run_id` carries the runner's frozen `D4_2_A-` prefix because `run_tier_a_v2` was reused
unmodified. It is a one-candidate smoke, not a Tier A run, and the manifest records
`sample_size = 1`.

The analyses directory now holds four run ids. Three predate this step (`...20260929T072105Z` =
D4.3A, `...20260930T001105Z` = one of V3's two zero-cost EXECPATH failures, `...20260930T012115Z` =
Final Tier A V3) and one is this smoke. Nothing in the three earlier trees was read for write or
modified.

## N. Tests

```
before the run   841 passed, 1 skipped
after the run    841 passed, 1 skipped      (identical)
```

```
.venv/bin/python -m pytest backend/tests/strategy_h_v2 -q
841 passed, 1 skipped
```

The one skip is `test_probe_under_a_forced_non_project_interpreter_is_marked_unofficial`, expected
since D4.3R §L and unchanged.

Every area the brief's §16 names, run individually after the live call:

| area | file | result |
|---|---|---|
| telemetry, M1-M12 aggregation | `test_d4_3r_audit_alignment.py` | 17 passed, 1 skipped |
| expectation state / prose consistency | `test_d4_s_expectation_state.py` | 43 passed |
| consensus leakage (V1 phrases) | `test_consensus_language.py` | 41 passed |
| consensus leakage (R2 market referent) | `test_consensus_language_r2.py` | 29 passed |
| M8 atomic-only, compound audit | `test_d4_s_m8_compound_coverage.py` | 33 passed |
| M8 numeric roles | `test_numeric_roles.py` | 12 passed |
| budget | `test_d4_budget_contract.py` | 15 passed |
| contract / canonical model | `test_d4_2_contract.py` | 13 passed |
| checksum, clean checkout | `test_clean_checkout.py` | 18 passed |
| validator | `test_d4_validate.py` | 33 passed |

`test_d4_s_m8_compound_coverage.py`'s stored-artifact drift test still re-derives exactly the six
historical compound findings from D4.3A and V3, so this smoke added none and disturbed neither run.

No live result was edited to make a test pass, and no test was edited to accommodate a live result.
No same-session patch and rerun occurred: one call was made, it validated, and nothing was changed
afterwards.

## O. Verdict

```
H-V2-D4-S1
= STRONG PASS
```

Measured against the success conditions frozen in the brief's §12 before the result was seen:

| condition | reading |
|---|---|
| final valid output exists | YES |
| no expectation-language false positive | YES - 0 under the live contract, and the one candidate sentence was released by the structural rule |
| no fabricated expectation | YES - M4 PASS, `fabricated_consensus` 0 |
| structured expectation state / prose consistent | YES - statuses match verbatim, limitations quote the status token |
| no new structural runtime/audit mismatch | YES - M1-M12 12 PASS, every audit defect category 0 |
| M8 ATOMIC-only checks pass | YES - M8 PASS, `code_owned_numeric_defects` 0 |
| **Strong PASS**: initial valid YES and repair 0 | YES |

```
M1   PASS   canonical model valid                     1/1 == claude-opus-5-5
M2   PASS   schema parse valid                        1/1 schema-valid
M3   PASS   consensus absence accepted                0 rejections
M4   PASS   fabricated consensus absent               0 asserted expectations
M5   PASS   direction enum respected                  used STRENGTHENED, WEAKENED; enum visible
M6   PASS   C1 respected                              0 violations; C1 NOT_EVALUATED (0 eligible)
M7   PASS   C4 respected                              0 above the MEDIUM ceiling
M8   PASS   numeric ownership respected               0 restatements (ATOMIC scope)
M9   PASS   D5/D6 fields absent                       0 fields, 0 vocabulary hits
M10  PASS   raw telemetry complete                    1/1 full untruncated
M11  PASS   budget contract respected                 $1.39 vs $6.00 reserve
M12  PASS   clean-checkout reproducibility            clean-checkout import passed

core gates (M3, M4, M5, M9, M12)   all PASS
M1-M12                             12 PASS, 0 FAIL, 0 NOT_EVALUATED
```

What this verdict does **not** say. It is not a D4 Expectation Gap quality PASS, not an APPROVE,
WATCH or REJECT, not a valuation, not a fair value, not a target price and not a forward return. It
is not a rate: one issuer, and that issuer is one of the three whose outputs were read closely while
D4 was designed. It does not show that initial validity is now stable in general - D4.3R2 §H's
UNSTABLE diagnostic was computed on a three-candidate run and is not recomputed or superseded here.

What it does say. On one real live response, under the frozen prompt and schema, with the input
verified identical by checksum, the D4-S structured-authority design produced a first-attempt valid
output where the pre-D4-S contract would have produced a rejection and a repair round. The rejection
that did not happen is identified by name, by sentence, and by the structural rule position that
released it.

## P. Next Action

```
Tier B          NOT YET AUTHORIZED
```

One smoke does not authorize Tier B and this document takes no step toward it. D4.3R2 §H's frozen
rule requires a separate user review of the UNSTABLE initial-validity diagnostic before any Tier B
authorization, and that review is not discharged by a one-candidate result.

Open items, unchanged by this smoke and each needing a user decision:

```
1  GOOG second smoke candidate
   The brief's §13 allows it "only if necessary". SCCO returned STRONG PASS with zero repairs, so
   nothing in this result makes a second candidate necessary. Recommendation: do not spend it.

2  M8 R1 + R2 + R3
   Two coverage corrections and one comparison change, preregistered with an acceptance condition
   in H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md §H. Offline, $0. Required before M8's scope can
   include the compound form.

3  STATE_TOKEN ownership (D1)
   Did not fire in this run (§K). Still open, still recommended as its own gate rather than as a
   change to M8's numeric meaning.

4  Tier B budget
   Unchanged and still not fitting: observed 6-company projection $16.5903 against $16.6268626
   remaining, a $0.037 margin. `d4_2_contract.TIER_B_FITS_REMAINING_CAP` stays asserted False.
```

### Final declarations

```
live sample                                SCCO only
requested model                            claude-opus-5-5
canonical model                            claude-opus-5-5   (mismatch False)
hard cap                                   $6.00
initial valid                              YES
repair                                     0
live cost                                  $1.3940560
live calls                                 1
expectation false positive                 0   (1 would have occurred pre-D4-S, on the same bytes)
fabricated consensus                       0
M8 scope                                   ATOMIC ONLY
compound M8                                NOT EVALUATED
C1                                         NOT_EVALUATED (0 eligible, gap NEUTRAL)
Tier B calls                               0
Tier B authorized                          NO
D5                                         NO
valuation / fair value / target price      NO
APPROVE / WATCH / REJECT                   NO
forward returns                            NO
D4-S code modified                         NO
expectation-state modified                 NO
consensus classifier modified              NO
M8 matcher modified                        NO
M8 compound scope expanded                 NO
R1 / R2 / R3 implemented                   NO
STATE_TOKEN gate implemented               NO
C1 / C4 changed                            NO
Gap semantics changed                      NO
confidence semantics changed               NO
same-session patch + rerun                 NO
existing unrelated dirty files modified    NO
push                                       NO
tests                                      841 passed, 1 skipped
```

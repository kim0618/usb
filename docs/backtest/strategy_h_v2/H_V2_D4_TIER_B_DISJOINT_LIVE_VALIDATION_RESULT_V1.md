# H-V2-D4 Tier B - Disjoint End-to-End Live Validation Result

```
H-V2-D4 Tier B
= FAIL

D5 Valuation Fundamentals
= NOT READY

cause            E1 alone: 4/6 final outputs = 66.7% against a frozen >= 95%
every other gate PASS or NOT_EVALUATED
fabrication      0 in every final output
live cost        $17.8984966 of a $45.60 ceiling
```

The verdict is FAIL and the reason is convergence, not fabrication. Two of six candidates produced no
D4 output at all, because the contract refused - three times each - to accept a guidance comparison the
model could not support, and on one of them refused an outright fabricated consensus. Nothing
unsupported reached a final output; on two issuers nothing reached one.

Those are different failures and this document does not blur them. A pipeline that cannot produce an
answer on a third of unseen issuers is not ready for D5, and E1's threshold was frozen before D4.1 ran
and is not adjusted here. But the content gates that Tier B exists to test - fabricated consensus,
numeric ownership, categorical state fidelity, decision leakage, UNKNOWN discipline - all passed on
real denominators, and the two failures are the contract holding rather than the contract leaking.

Not an alpha result, not a backtest, and not a statement about any issuer.

---

## A. Repository State

```
branch                          main
HEAD at preflight               4cae7b9  fix(strategy-h-v2): repair state fidelity semantics before Tier B
origin/main at preflight        4cae7b9
ahead / behind                  0 / 0
staged                          0
dirty modified at preflight     21   (27 at write-up)
untracked at preflight          196
H-V2 related dirty              0    -> no STOP condition (brief §2)
```

`origin/main` equals HEAD, and `git reflog show origin/main` reads `4cae7b9 ... update by push`. Neither
the D4-H session, nor the D4-H1 session, nor this one pushed; the push came from outside. Recorded as
provenance only.

All five authoritative commits verified present in real history, which takes precedence over the
brief's short forms:

```
738bdc1  refactor(strategy-h-v2): simplify expectation claim validation      D4-S
235ac4b  test(strategy-h-v2): audit M8 compound claim coverage               M8 compound audit
c170123  test(strategy-h-v2): record SCCO limited live smoke                 D4-S1
44ef89c  fix(strategy-h-v2): close the M8 integrity blind spots before Tier B  D4-H
4cae7b9  fix(strategy-h-v2): repair state fidelity semantics before Tier B     D4-H1
```

`4cae7b9` confirmed as the H1 result. The 21 pre-existing dirty files are unrelated to H-V2 (crypto,
frontend, baseline) and were neither modified nor staged; the count grew to 27 during the run from a
concurrent session, and none of the additions is H-V2 either.

---

## B. Frozen Contract

Everything adjudicated here was frozen before this run, and almost all of it before D4-B existed. Used
BY REFERENCE, never restated:

```
sample + CIK exclusion    d4_1_contract.TIER_B_SAMPLE / EXCLUDED_CIKS      frozen 2026-09-29
E1-E8 thresholds          d4_1_contract.E1_.. + evaluate_d4_1_gates        frozen before D4.1 ran
verdict contract          PASS / PASS_WITH_LIMITATIONS / FAIL              d4_1_contract
budget accounting         d4_2_contract.TIER_B_BUDGET                      the repaired reserve
manual-audit subset       manual_audit_strategy_h_v2_d4_1                  all six issuers, seeded
D3 leg                    d3_3_contract + research_one_v3                  Prompt V2 / Schema V2 / Validation V2
D4 leg                    run_strategy_h_v2_d4_2.analyze_one_v2            D4-S + D4-H1 validator
```

The one thing with no frozen home was the State Fidelity gate, active for the first time in this run.
`d4_b_contract.py` freezes it before any Tier B output existed:

```
SF1 = CODE_OWNED_STATE_FIDELITY
  threshold        == 0 violating assertions
  aggregation      ASSERTION - failed assertions summed over candidates
  zero eligible    NOT_EVALUATED, never PASS
  not a violation  NEGATED, REJECTED_OR_CONTRASTED
gates   E1..E8 + SF1        core  E3 E4 E5 E7 SF1
M8      atomic ACTIVE, R1 CLOSED, R2 CLOSED, R3 DEFERRED
```

### Why a new runner, and why the dry run came first

`run_strategy_h_v2_d4_1.run_tier_b` already existed and does the same chaining, but it predates two
repairs and would have run Tier B under both defects: its D4 leg is D4.1's `analyze_one` rather than
the repaired `analyze_one_v2`, so Tier B would have tested a contract D4.2 superseded and D4-S1 never
validated; and its preflight reserves $3.60 per candidate, which is D4.1's per-call-cap-as-candidate-
reserve defect. Calling it would have been a cheaper way to reach a wrong number.

So `run_strategy_h_v2_d4_b.py` is orchestration and accounting only - no prompt, no schema, no
threshold, no validator - and it was exercised end to end against stubbed legs before any live call.
**That dry run found two contract defects, each of which would have corrupted this result:**

**1. The ceiling sat seven femtocents below its own authorization.** `TIER_B_BUDGET
.candidate_worst_case_budget_usd` is 7.6, which has no exact binary representation, so `6 * 7.6`
evaluates to `45.599999999999994`. `admits_next_candidate` asks `spent + 7.6 <= cap`, and before the
SIXTH candidate that reads `45.6 <= 45.599999999999994`, which is False. The frozen six would have
stopped at five with `budget_exhausted: True` - the sample truncated by an IEEE-754 artifact rather
than by a budget decision. It would not have bitten at observed costs, which is exactly why it is
worth naming: a latent truncation that only appears when spending runs high appears on the run you
least want it on. Corrected by rounding to cents, bounded so it can never widen a cap (must equal
$45.60 exactly, must move the product by less than one cent), plus an assertion that the ceiling
admits all six at full worst case and refuses a seventh.

**2. SF1 would have failed a run for the absence of a defect.** SF1 is a core gate, and
`d4_1_verdict`'s core rule is "anything but PASS is a FAIL". That is right for E3/E4/E5/E7, each a
count over every graded output, so each always has a denominator. SF1's denominator is DATA-dependent:
a run in which the model never restated a code-owned categorical state has nothing for SF1 to compare,
and that is a zero denominator, not a fabrication. Split into `D4_B_CORE_GATES` (a FAIL is never a
limitation) and `MUST_ALWAYS_EVALUATE` = E3/E4/E5/E7 (an unscored gate means the audit did not run).
SF1 NOT_EVALUATED now caps the verdict at PASS_WITH_LIMITATIONS; SF1 FAIL is still fatal.

---

## C. Sample Integrity

The frozen six, unchanged, with no substitution:

| ticker | CIK | depth | priority | chunks | sources | package checksum |
|---|---|---|---|---|---|---|
| IDCC | 0001405495 | FULL | E3_P1_HIGH | 190 | 12 | 8c01dacd… |
| DORM | 0000868780 | FULL | E3_P1_HIGH | 136 | 13 | 9203da74… |
| FRPT | 0001611647 | FULL | E3_P1_HIGH | 173 | 13 | 878edc5a… |
| TG | 0000850429 | CORE | E3_P2_MEDIUM | 123 | 14 | ab19090c… |
| CRK | 0000023194 | CORE | E3_P2_MEDIUM | 120 | 11 | e2fec50c… |
| SPSC | 0001092699 | CORE | E3_P2_MEDIUM | 104 | 11 | a4d93121… |

```
sample checksum frozen     acf2da18c8792f599c2435747200625ca783dfa0eb4f65140d590b5c13c825b0
sample checksum recomputed identical
manifest checksum          identical
```

Every candidate's ticker, CIK, depth and priority matched its D2.1 package. Disjointness (§4) is
CIK-based, never ticker-based, so one issuer under two symbols cannot slip through:

```
EXCLUDED_CIKS                            48  (D3 pilot 12 + D3.1 Batch 2 24 + D3.3 Tier A 12)
overlap with the Tier B six              NONE
distinct CIKs in the sample              6
regenerate_tier_b_from_universe()        identical to the frozen literal
```

The re-derivation matters: a hardcoded list checksummed by hashing itself proves nothing, so the sample
was recomputed offline from the D2.1 snapshot and compared to the literal.

---

## D. Budget

Independent authorization, the repaired accounting, nothing expanded.

```
hard cap (ceiling, not an expectation)   $45.60
run total                                $17.8984966
within cap                               YES
every candidate within its worst case    YES
stopped early / budget exhausted         no / no
observed projection (non-gating)         $16.59   -> actual came in 7.9% above it
```

Split as §9 requires:

| ticker | D3 leg | D4 initial | D4 repair | candidate total | within $7.60 |
|---|---|---|---|---|---|
| IDCC | $1.4164 | $0.9729 | $0.7272 | $3.1165 | yes |
| DORM | $1.1993 | $0.8526 | $0.4046 | $2.4565 | yes |
| FRPT | $1.2939 | $1.0472 | $0.9443 | $3.2854 | yes |
| TG | $2.0859 | $1.1730 | $0.0000 | $3.2589 | yes |
| CRK | $1.4216 | $0.9657 | $0.0000 | $2.3873 | yes |
| SPSC | $1.2109 | $1.0348 | $1.1483 | $3.3939 | yes |
| **total** | **$8.6280** | **$6.0461** | **$3.2244** | **$17.8985** | |

Repair was 18.0% of the run's cost. The two candidates that never converged cost $6.6793 between them
and produced no output, which is a real cost of the failure and is not netted out anywhere.

### A component assumption was exceeded, and it was absorbed

```
D3 leg worst-case assumption   $1.60   (TIER_B_BUDGET.preceding_stage_worst_case_usd)
TG's actual D3 leg             $2.0859 -> exceeded by $0.4859
```

TG's D3 leg cost more than the contract's own assumption for that component. The candidate reserve of
$7.60 absorbed it and TG still finished at $3.2589, well inside. This is recorded rather than smoothed
because it is the same family of defect D4.1 had - a reserve that does not bound what it claims to -
and because the absorption is direct evidence that D4.2's repair does what it was built for. The
assumption is NOT adjusted here: changing a frozen budget component after seeing a run's costs is the
result-driven tuning the brief forbids.

---

## E. End-to-End Execution

Run `D4_B-20260930T053146Z`, six candidates, D3 then D4 per candidate, one candidate at a time, budget
checked before each candidate started. Wall clock 14:31 to 15:13 UTC+9, about 42 minutes.

```
        D3 final   D3 init  D3 rep   D4 final                   D4 init  D4 rep   gap
IDCC    OK         O        0        OK                         X        2        NEUTRAL / LOW
DORM    OK         O        0        OK                         X        1        UNKNOWN / UNKNOWN
FRPT    OK         O        0        SCHEMA_VALIDATION_FAILED   X        2        (no output)
TG      OK         X        1        OK                         O        0        NEUTRAL / LOW
CRK     OK         O        0        OK                         O        0        NEUTRAL / LOW
SPSC    OK         O        0        SCHEMA_VALIDATION_FAILED   X        2        (no output)

D3 leg  6/6 final OK        D4 leg  4/6 final OK
sample completed            4 / 6
```

### The two failures, in full

Both are the same structural shape: the model asserted a guidance comparison it did not have the
evidence for, and the contract refused it through both repair rounds.

**FRPT** - `SCHEMA_VALIDATION_FAILED` after 2 repairs, $3.2854 spent.

```
initial  previous guidance needs both bounds or neither - a half-stated range is how a point
         guidance and a truncated range become indistinguishable
R1       the same error, unchanged
R2       state=RAISED asserts a CHANGE in guidance, which requires both the previous and the
         current range - otherwise it is INITIATED or UNKNOWN
```

**SPSC** - `SCHEMA_VALIDATION_FAILED` after 2 repairs, $3.3939 spent.

```
initial  state=ABOVE_COMPANY_GUIDANCE is a comparison and requires the reported value and both
R1       prior-guidance bounds - a comparison with one side missing is an assertion
R2       a sentence attributes an expectation to analysts or the market ('consensus expect')
         while the expectation bundle reports consensus SOURCE_NOT_AVAILABLE, so there is no
         source that could support it
```

**SPSC's second repair round is the single most important behavioural finding of this run.** On an
issuer nobody had looked at, the model attempted to fabricate a consensus expectation, and the
contract blocked it. The cost of blocking it was that the candidate produced nothing. That is the
trade Tier B was built to observe, and it is visible here in both directions at once: E3 reads 0
fabricated consensus in final outputs precisely because one candidate has no final output.

Neither failure was given a substitute input, a relaxed rule or a third repair round. No sample
substitution, and the D3 outputs for both are intact and were graded (§G).

---

## F. Model Verification

```
requested                    claude-opus-5-5
D3 canonical, all 6          claude-opus-5-5
D4 canonical, all graded     claude-opus-5-5
any model mismatch           NO
fallback                     none - not permitted, and none occurred
telemetry complete           YES on every graded candidate
```

Checked against each response's own reported `canonicalModel` rather than against the request string,
which is the check D3.2F built the runner around. Both legs assert the frozen model at import time, so
a fallback could not have been configured silently.

---

## G. D3 Research Quality

Graded by D3.3's own frozen audit, unchanged - not by a second opinion invented for Tier B. All six D3
legs produced a final output, including the two whose D4 leg later failed.

| ticker | material claims | structural provenance defects | citation-defective claims | numeric-defect claims | investment language | completeness |
|---|---|---|---|---|---|---|
| IDCC | 73 | 0 | 0 | 0 | 0 | PARTIAL |
| DORM | 72 | 0 | 0 | 0 | 0 | PARTIAL |
| FRPT | 94 | 0 | 0 | 0 | 0 | PARTIAL |
| TG | 92 | 0 | 1 | 1 | 0 | PARTIAL |
| CRK | 88 | 0 | 1 | 1 | 0 | PARTIAL |
| SPSC | 87 | 0 | 0 | 0 | 0 | PARTIAL |
| **total** | **506** | **0** | **2** | **2** | **0** | |

```
structural provenance          506 / 506 material claims resolve
numeric fidelity               504 tokens in their cited chunk of 507 (99.4%), 3 unsupported
Future Business discipline     14 items, 0 stage escalations beyond evidence flags
investment language            0
research completeness          PARTIAL on all six - stated, not hidden
```

The two citation defects are real and are named rather than aggregated away:

```
TG   business_model.geography[3]      "exports ... totaled 9% of consolidated net sales, with 6%
                                      going to Asia"     -> 9 and 6 are not in the cited chunk
CRK  business_model.key_dependencies[4]  "fund part of the drilling and completion costs of 27
                                      wells"             -> 27 is not in the cited chunk
```

Both are citation-precision defects in the D3 leg: the sentence may well be true of the filing, but
the chunk the claim points at does not carry the number. 2 defective claims in 506 is D3.3's own
measured range and neither is a D4 gate, but neither is discounted either.

TG's D3 leg is also the run's only `PROHIBITED_LANGUAGE` repair: the initial response used investment
language the D3 contract forbids, and one repair round removed it. Final investment-language count is
0 on all six.

---

## H. Expectation Evidence

Built for all six candidates from the PIT-trimmed price panel; stored beside each analysis.

```
consensus_status, all 6              SOURCE_NOT_AVAILABLE
estimate_revisions_status, all 6     SOURCE_NOT_AVAILABLE
ExpectationKnowledgeState, all 4 graded   status=UNKNOWN, market_expectation_claim_allowed=false
```

The structured state is the authority (§11) and it held: every graded candidate's knowledge state is
UNKNOWN with market-expectation claims disallowed, and every graded output carries an explicit
UNKNOWN-typed claim saying so rather than leaving the absence implicit.

No honest-uncertainty sentence was misread as an assertion. `fabricated_consensus` is 0 on all four
graded outputs, and the prose classifier flagged nothing that the structured state permitted - which
is the specific structural defect §11 names, and it did not occur.

---

## I. Expectation Gap

```
NEUTRAL   3   (IDCC, TG, CRK)
UNKNOWN   1   (DORM)
no output 2   (FRPT, SPSC)

WIDE_POSITIVE / POSITIVE / NEGATIVE / WIDE_NEGATIVE   0
```

The distribution is not a PASS/FAIL basis (§13) and is reported only because it determines C1's
denominator. What the reasoning looks like matters more than the labels, and in all four graded
outputs the gap is argued from the evidence rather than asserted:

```
IDCC  "Price weakness after the Q2 reaction cannot show that expectations lag reality. It is
       equally consistent with information outside this evidence pool."
DORM  "There is no non-price evidence that expectations lag the evidenced underlying progress, so
       a positive gap is not supported (C1)."     -> and it returned UNKNOWN rather than NEUTRAL
TG    "No non-price evidence indicates that expectations materially lag the evidenced improvement,
       so a positive gap is not supported under the asymmetry rule."
CRK   "price history alone cannot show that expectations lag"
```

DORM is worth singling out: presented with two-sided evidence it returned `UNKNOWN / UNKNOWN` and a
`priced_in` of UNKNOWN rather than manufacturing a direction. That is the behaviour the UNKNOWN
discipline exists to produce.

---

## J. C1 / C4

```
       eligible  violations  status
C1     0         0           NOT_EVALUATED
C4     4         0           PASS
C5     4         0           PASS
C6     3         0           PASS
```

**C1 is NOT_EVALUATED and this is not a PASS.** No graded output is POSITIVE or WIDE_POSITIVE, so the
rule "a POSITIVE gap requires non-price expectation evidence" had no case to apply to. Reporting 0
violations as evidence the rule holds would be reporting a zero denominator as a result, which §14 and
§26 both forbid. The rule's substance was nevertheless reasoned about in every graded output (§I),
which is a different and weaker observation than the rule being tested, and is stated as such.

C4's ceiling applied to all four graded candidates - consensus and revisions are both
SOURCE_NOT_AVAILABLE for every one - and no confidence exceeded it. Confidences were LOW on three and
UNKNOWN on one, against a MEDIUM ceiling, with no silent downgrade: the ceiling is recorded in the
fired contract rules (`C4_NO_CONSENSUS_CONFIDENCE_CEILING`) rather than applied invisibly.

---

## K. State Fidelity

SF1's first live activation, aggregated exactly as the preregistration froze it.

```
SF1 = CODE_OWNED_STATE_FIDELITY          PASS

assertions total                 18
assertions evaluated             15      bound to a code-owned metric and compared
assertions passed                15
assertions FAILED                 0

not evaluated                     3      NO_METRIC_NAMED 2, POLARITY_NOT_ASSERTED 1
polarity                          ASSERTED 17, NEGATED 1, REJECTED_OR_CONTRASTED 0
```

83.3% of state occurrences were bound and compared, so the zero rests on a real denominator rather
than on a gate that declined to look. Had it been zero-eligible it would have read NOT_EVALUATED and
capped the verdict, per the contract frozen in §B.

Two assertions were verified by hand against the authority rather than only by the gate:

```
CRK   "the code-owned states for revenue and operating income are both INFLECTION_NEGATIVE"
      authority: revenue=INFLECTION_NEGATIVE, operating_income=INFLECTION_NEGATIVE     exact
IDCC  "Reported revenue, operating income and EPS are in a negative inflection"
      authority: revenue / operating_income / eps_diluted all INFLECTION_NEGATIVE       exact
```

The one NEGATED occurrence was correctly not counted as an assertion, which is the S1 repair D4-H1
built, working on live data for the first time.

---

## L. Numeric Ownership

```
M8 atomic numeric ownership        ACTIVE
E5 code-owned numeric defects      0        (threshold == 0)
R1 STATE_TOKEN coverage            CLOSED
R2 window / ellipsis coverage      CLOSED
```

Four code-owned figures were checked by hand against the code fact index, at full precision:

```
CRK  return_6m                          -0.3640776699029127      exact
CRK  relative_strength_3m               -0.022948823057584722     exact
TG   drawdown_from_252s_close_high      -0.3342995169082126      exact
TG   return_1m                          -0.13659147869674193     exact
```

Every one is a bit-exact transcription, not a recomputation. The graded outputs quote code-owned
numbers at 16-17 significant figures, which is what transcription looks like and is not what
arithmetic looks like.

**DORM's initial D4 response is the counter-example that makes E5 meaningful.** It cited
`benchmark_adjusted_3d` and stated `53`, and the runtime validator rejected it:

```
claim cites code-owned fact(s) ['price_reaction...benchmark_adjusted_3d'] but states number(s) '53'
that are not those values
```

One repair round fixed it. E5 measures final outputs, so the final is clean - but the attempt happened,
on an unseen issuer, and the contract caught it. That is the gate doing work rather than describing an
absence.

---

## M. Compound Scope

```
M8 scope                           ATOMIC claims only, with R1/R2 corrected coverage
COMPOUND SET-VALUED R3             DEFERRED - not implemented in this run
compound coverage gap findings     1
```

**R3's measured exposure is no longer zero.** D4-H measured 0 occurrences of mechanism C across its
whole corpus and reported it as a structural exposure the runs happened not to exhibit. Tier B exhibits
it once:

```
CRK  supporting_claims[4]
     cites  pre_event_price_context.relative_strength_3m
            pre_event_price_context.relative_strength_6m
     text   "Relative strength against the benchmark over the last 63 sessions was
             -0.022948823057584722, far smaller in magnitude than the 126-session figure, so most
             of the relative decline occurred in the earlier part of the 126-session window."
```

The claim states the 3-month value it cites, exactly and correctly, and says nothing numeric about the
6-month fact beyond a magnitude comparison. Judging per cited fact flags it against the 6-month value
it never restated - mechanism C precisely, and a false positive. Wired to no gate, so E5 is unaffected,
and R3 is NOT implemented here: it changes M8's unit of comparison rather than its coverage, and
implementing it mid-run would change what E5 measures after the run started.

What changed is the case for closing it. "Structurally present, 0 observed" is a weaker argument for
deferral than it was this morning.

### Compound provenance (§19)

`ClaimV2`'s compound form requires `source_id` to be null, so a null `source_id` is the contract and
not a missing citation. E2's numerator is A-type claims whose citation does not resolve, decided
structurally from the claim's type and cited ids, and it is 0. No compound claim was counted as
unsourced.

---

## N. UNKNOWN Discipline

```
E6 UNKNOWN discipline violations   0    (C1/C4/C5/C6 combined, measured on final outputs)
```

Beyond the gate, the graded outputs use UNKNOWN as a working answer rather than as a formality:

```
DORM   expectation_gap = UNKNOWN, priced_in = UNKNOWN - declined a direction on two-sided evidence
CRK    12 unknown_fields, 11 limitations, including "The D5 valuation stage has not run; no
       statement about valuation is made or implied here"
TG     5 priced-in limitations against 4 evidence_ids
IDCC   3 priced-in limitations
```

Every graded output names what it does not know, including CRK explicitly recording that its
expectation side rests mainly on price history and that "a positive gap could not be supported on this
evidence in any case" - the C1 asymmetry stated as a limitation rather than discovered by a gate.

---

## O. Manual Audit

The frozen subset (§23) already existed and was used unchanged: all six issuers, every `gap_rationale`
claim, the whole `priced_in_assessment`, every `why_now`, `guidance_assessments`, `result_vs_guidance`,
`management_signal_changes` and `conflicts` entry, plus the first 6 remaining material numeric claims
by `sha256("H_V2_D4_1_MANUAL_AUDIT_V1:<ticker>|<path>")`.

```
selected claims    104   (CRK 30, TG 30, IDCC 26, DORM 18)
FRPT, SPSC         no final output - nothing to audit
```

Findings across the nine dimensions:

```
D3 material facts          2 citation-precision defects in 506 claims (§G), both named
D4 gap rationale           every graded gap argued from evidence, none asserted
expectation evidence       consensus/revisions SOURCE_NOT_AVAILABLE held on all six
consensus discipline       0 attributions to analysts / market / consensus in any final output
numeric ownership          4 spot checks bit-exact; 1 blocked attempt (DORM, §L)
state fidelity             2 spot checks exact; 15 evaluated, 0 failed (§K)
priced-in evidence         all non-UNKNOWN assessments carry evidence_ids and limitations
why-now evidence           present and sourced where claimed
UNKNOWN discipline         used substantively (§N)
```

One claim was checked in detail because it looked unsupported in the rendered window and was not:
CRK's `guidance_assessments[0].claims[0]` states "approximately $1.4 billion to $1.5 billion in 2026",
and the cited chunk contains verbatim "We currently expect to spend approximately $1.4 billion to $1.5
billion in 2026 on our development and exploration projects". The render truncates before the sentence;
the citation is precise.

No content defect was found in any graded final output.

---

## P. E1-E8

Thresholds frozen before D4.1 ran, unchanged after seeing this result.

| gate | name | threshold | observed | status |
|---|---|---|---|---|
| E1 | schema validity | >= 95% | **4/6 = 66.7%** | **FAIL** |
| E2 | material gap claims source-linked | == 0 | 0 | PASS |
| E3 | fabricated consensus | == 0 | 0 | PASS |
| E4 | future source / price leakage | == 0 | 0 | PASS |
| E5 | code-owned numeric integrity | == 0 | 0 | PASS |
| E6 | UNKNOWN discipline | == 0 | 0 | PASS |
| E7 | investment decision leakage | == 0 | 0 | PASS |
| E8 | priced-in claims fully supported | == 0 | 0 | PASS |
| SF1 | code-owned categorical state fidelity | == 0 | 0 of 15 evaluated | PASS |

E6 and E8 were fed the MECHANICAL count rather than `None`. `evaluate_d4_1_gates` types both as
manual-audit inputs, where `None` means NOT_EVALUATED; the audit computes both over final outputs, so
passing those counts is strictly stronger than declaring the gates unevaluated. The manual audit was
performed separately (§O) and is not what these two gates are computed from.

E7's classifier kept its existing contextual behaviour: SEC/GAAP uses of "fair value" in cited filing
text are not decision leakage, and 0 leaks were found in any final output. No `cheap`, `undervalued`,
`price target`, `buy`, `sell`, `approve`, `watch`, `reject`, `entry` or `exit` vocabulary appears.

---

## Q. Cost

```
live cost             $17.8984966
hard cap              $45.60          39.3% used
observed projection   $16.59          actual 7.9% above, non-gating
D3 leg                $8.6280
D4 initial            $6.0461
D4 repair             $3.2244         18.0% of the run
wasted on the two non-converging candidates   $6.6793
live Opus calls       20  (D3: 6 initial + 1 repair = 7;  D4: 6 initial + 7 repair = 13)
```

Every candidate finished inside its own $7.60 worst case. The run stopped early for no reason and
exhausted nothing.

---

## R. Tests

```
before the run   1038 passed, 1 skipped
after the run    1038 passed, 1 skipped
new for D4-B     26
live calls in tests   0
```

Run from the repository root, which the stored-record tests' relative paths require. New file
`backend/tests/strategy_h_v2/expectation/test_d4_b_tier_b_runner.py` covers the frozen preregistration,
the chained run with both legs stubbed, split accounting, the budget preflight in both directions, a
model mismatch, a D3-leg failure producing no D4 call, and the audit graded over a stub run including
an injected state mismatch failing SF1 and the whole verdict. The two dry-run defects each have a test
that pins the defect and its correction.

Post-run verification: telemetry complete on every graded candidate, checksums matched, model identity
verified both legs, D3 validation audited, expectation state derived, C1/C4 computed with denominators,
SF1 aggregated per the frozen rule, M8 R1/R2 active with R3 deferred, E1-E8 evaluated, budget within
cap.

---

## S. Limitations

Stated rather than discovered later.

**What this run cannot say.** Six issuers is six issuers. The E gates prove that D4 produced
evidence-disciplined expectation interpretations under a frozen contract; they do not prove that any
expectation gap is real, that a NEUTRAL gap means anything, or that this pipeline is profitable. Alpha
is D6 plus D7 Forward Shadow and neither exists.

**Convergence is measured on 6, not on a rate.** 4/6 is a count, not an estimate of a failure rate. The
sample was not built to estimate one.

**No consensus provider exists.** Every candidate's expectation side rests on company guidance, filing
language and price history. Every graded output says so. A gap conclusion on this evidence base is
structurally limited regardless of the gates.

**D3 research completeness is PARTIAL on all six.** No candidate had a complete evidence picture.

**Audit row reporting gap, gate-neutral.** The audit omits `d4_repair_rounds` for candidates with no
final output, so §25's repair diagnostic reads 3 from the audit rows when the manifest records 7. The
manifest is the primary record and §E reports the true figures. Not patched in this session: the audit
is the grader, and editing the grader after reading its output is what §1 forbids. Worth fixing before
the next tier.

**R3's exposure is now 1, not 0** (§M). The deferral still holds on its own reasoning, but the argument
for it is weaker than it was.

**The D3 leg's worst-case component assumption was exceeded once** (§D), absorbed by the candidate
reserve and left unadjusted.

---

## T. Verdict

```
H-V2-D4 Tier B
= FAIL
```

E1 is the sole failing gate, at 4/6 = 66.7% against a threshold frozen before D4.1 ran. Every other
gate passed, SF1 passed on a real denominator in its first live activation, and no content defect was
found in any graded final output by gate or by hand.

The failure is convergence, and its mechanism is the same on both candidates: the model asserted a
guidance comparison it lacked the evidence for, and the contract refused it through both repair rounds
rather than accept a half-stated range, an unsupported comparison, or - on SPSC - a fabricated
consensus. Nothing unsupported reached a final output because on two issuers nothing reached one.

That is not a technicality to be argued down into PASS_WITH_LIMITATIONS. A chain that cannot produce
an answer on a third of unseen issuers is not ready for the next stage, and the threshold is not
adjusted after the fact. It is also not a fabrication failure, and the document says which it is,
because the two call for different repairs: this one points at the guidance-assessment contract's
repair path, not at the model's discipline.

---

## U. D5 Authorization

```
D5 Valuation Fundamentals
= NOT READY
```

§28 makes D5 contract design conditional on PASS or PASS_WITH_LIMITATIONS. Tier B returned FAIL, so D5
is NOT READY and no D5 contract design begins. D5 is not executed, and nothing here authorizes it.

The next step is a decision the user owns. What this run identifies as the thing to fix is narrow and
specific: the guidance-assessment repair path. Both failures are the model unable to satisfy
`guidance_assessments` / `result_vs_guidance` bound completeness across two repair rounds, and in both
cases the repair prompt did not move it off the same error on the first round. Whether that is a repair-
prompt problem, a schema-affordance problem, or correct behaviour on issuers whose filings genuinely
lack comparable guidance ranges is not decided here, and deciding it from this run's outputs would be
fitting a repair to six observations.

```
live Opus calls                      20
live cost                            $17.8984966
hard cap                             $45.60
sample                               frozen 6
sample completed                     4 / 6
requested model                      claude-opus-5-5
canonical model                      claude-opus-5-5 on every call
model mismatch                       NO
D3 initial valid                     5 / 6
D4 initial valid                     2 / 6
repairs                              D3 1, D4 7
Expectation Gap distribution         NEUTRAL 3, UNKNOWN 1, no output 2
C1                                   NOT_EVALUATED (0 eligible)
C4                                   PASS (4 eligible, 0 violations)
State Fidelity SF1                   PASS (15 evaluated, 0 violations)
M8 atomic                            0 defects
M8 compound R3                       DEFERRED (exposure now 1)
E1-E8                                E1 FAIL, E2-E8 PASS
material content defects             0 in graded outputs
fabricated consensus                 0 in final outputs; 1 blocked attempt (SPSC)
D5/D6 leakage                        0
D5 executed                          NO
forward returns                      NO
valuation / APPROVE-WATCH-REJECT     NO
existing unrelated dirty modified    NO
push                                 NO
```

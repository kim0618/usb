# H-V2-D4-BR-C - Disjoint Convergence Confirmation Result

```
H-V2-D4-BR-C
= FAIL

cause            E7, a core gate - 1 decision-vocabulary leak in a final output
convergence      PASSED on its own terms - D4 final valid 4/4, two honest abstentions
run              D4_BR_C-20261001T005758Z
live cost        $11.2124832 of a $30.40 ceiling
D5               NOT READY
```

The question this step asked was whether the D3 -> D4 chain reaches a valid final output on four
issuers no H live call has ever touched. It does, 4 of 4, and two of the four converged on an honest
`UNKNOWN` rather than manufacturing a comparison. That is the behaviour D4-BR was repaired to produce
and it generalized.

The run failed anyway, on a different gate. COLL's final output contains the sentence "there was
neither a run-up nor a sell-off before the event", and E7's decision-vocabulary detector matches
`\bsell\b` inside "sell-off". E7 is a core gate with a threshold of zero, so the frozen aggregation
returns FAIL and D5 stays NOT READY. No rule was adjusted after the fact and no candidate was re-run.

Not an alpha result, not a backtest, and not a statement about any issuer.

---

## A. Repository / Sample

```
branch / HEAD        main @ 52176ca4326822dde93af866248368f6346f931a
D4-BR in history     b248ade
H-V2 dirty at start  none
test baseline        1081 passed, 1 skipped   (unchanged by this step's wiring)
```

The preregistered four, `d4_br_contract.D4_BR_CONFIRMATION_SAMPLE`, used unchanged. The sample was
regenerated from the D2.1 snapshot rather than compared against a hash of itself, and the runner
refuses to start if the recomputation disagrees.

```
checksum frozen      69598e04ad0e99e6b438567301be46b71340a43382cf5d3e3a5c2503775aa442
checksum recomputed  69598e04ad0e99e6b438567301be46b71340a43382cf5d3e3a5c2503775aa442
manifest checksum    69598e04ad0e99e6b438567301be46b71340a43382cf5d3e3a5c2503775aa442
```

| ticker | CIK | depth | priority | input package checksum |
|---|---|---|---|---|
| AEYE | 0001362190 | FULL | E3_P1_HIGH | `4d1cd6bf43295edbb18e665c4fe5719a4910e53ca3bab963b5d2c3e5d4a54fcb` |
| COLL | 0001267565 | FULL | E3_P1_HIGH | `917fb844c14c4446303b636720be68ce8f75655cc8002c5a9f6e608a67bde8bf` |
| FG | 0001934850 | CORE | E3_P2_MEDIUM | `5f3f92e155ca5d44f023e0df07f559c6f45538fe7a410b0c0fecaca5776a2ad0` |
| VRRM | 0001682745 | CORE | E3_P2_MEDIUM | `90f35f6bda47c010efe4db3b6de062c7b7d3d5f4ea52f092842996975dd0eb0f` |

Disjointness, checked rather than asserted: the four CIKs intersect `D4_BR_EXCLUDED_CIKS` - all 54
CIKs any H live call has ever touched, D4.1's 48 plus Tier B's 6 - in the empty set. The run writes to
`data/runtime/strategy_h_v2/d4_br_c/` under run-id prefix `D4_BR_C-`, so no confirmation record can be
read as a Tier B record and Tier B's store was not written to.

Model: `claude-opus-5-5` requested on both legs, and the canonical model on every one of the 12
responses reads `claude-opus-5-5`. `any_mismatch: false`.

---

## B. Budget

```
hard cap                        $30.40    4 x $7.60, derived from TIER_B_BUDGET, not typed
run total                       $11.2124832
within hard cap                 YES
every candidate within $7.60    YES       max was COLL at $3.1585018
observed projection             $11.06    non-gating diagnostic
stopped early / exhausted       no / no
```

The ceiling is `4 * TIER_B_BUDGET.candidate_worst_case_budget_usd` rounded to cents, bound to
`d4_2_contract` rather than restated, and a test asserts the preflight admits all four candidates at
their full worst case and refuses a fifth - the IEEE-754 truncation D4-B found on its own dry run,
pinned at n=4. Nothing expanded it; `D4_BR_C_NO_AUTOMATIC_EXPANSION` is `True`.

The projection was $11.06 and the run cost $11.2124832, which is 1.4% over. That is a coincidence
worth no more weight than that: the projection is two measured per-candidate averages multiplied by
four, and four candidates is not a sample that can confirm a cost model.

```
D3 legs        $5.3063254
D4 initial     $4.0039808
D4 repair      $1.9021770
```

---

## C. D3 Results

```
D3 final valid          4 / 4
D3 initial valid        4 / 4
D3 repair rounds        0
```

| ticker | status | initial | repairs | cost |
|---|---|---|---|---|
| AEYE | OK | OK | 0 | $1.5350660 |
| COLL | OK | OK | 0 | $1.2170178 |
| FG | OK | OK | 0 | $1.2483128 |
| VRRM | OK | OK | 0 | $1.3059288 |

Every D3 leg validated on its first response. No candidate reached D4 on a substituted or
manufactured input, because none needed to.

---

## D. D4 Results

```
D4 initial valid        1 / 4
D4 final valid          4 / 4        E1: 4/4 = 100.0% against a frozen >= 95%    PASS
```

| ticker | status | initial | repairs | gap | confidence | priced-in | cost |
|---|---|---|---|---|---|---|---|
| AEYE | OK | FAILED | 1 | NEUTRAL | LOW | LIKELY_PRICED | $1.4139708 |
| COLL | OK | FAILED | 2 | NEUTRAL | LOW | LIKELY_PRICED | $1.9414840 |
| FG | OK | **OK** | 0 | **UNKNOWN** | UNKNOWN | UNKNOWN | $0.9116970 |
| VRRM | OK | FAILED | 1 | **UNKNOWN** | UNKNOWN | PARTIALLY_PRICED | $1.6390060 |

`expectation_gap` distribution: NEUTRAL 2, UNKNOWN 2. No POSITIVE-family output, which is why C1 has
no denominator - see §G.

Telemetry is complete on all four: every initial and repair raw response, and both timestamps, are
stored.

---

## E. Repairs

```
repair candidates       3 / 4        AEYE, COLL, VRRM
repair rounds           4            AEYE 1, COLL 2, FG 0, VRRM 1
full-budget candidates  1            COLL, which converged on its last available round
repair dependence high  NO           the frozen rule is >= 3 of 4; this is 1
```

Causes, per candidate, from the record rather than reconstructed:

| ticker | initial failure | round 0 | round 1 | terminal |
|---|---|---|---|---|
| AEYE | `SCHEMA` | `SCHEMA` -> OK | - | none |
| COLL | `PROHIBITED_LANGUAGE` | `PROHIBITED_LANGUAGE` -> FAILED | `SCHEMA` -> OK | none |
| FG | - | - | - | none |
| VRRM | `SCHEMA` | `SCHEMA` -> OK | - | none |

`terminal_failure_codes` is empty for all four because all four produced a final output. The field
exists because Tier B's two failures had no stored terminal reason; this run had nothing for it to
record, which is the outcome the field was added to make legible either way.

Every repair stayed inside §10's permitted set. `attempted_consensus_attributions` is 0 on all four,
so the repair-provenance lock had no new provider category to reject and the fabrication route the
lock exists to close was never approached. COLL's round 1 is the one repair that is worth reading: it
removed a number attached to a code-owned citation (`benchmark_adjusted_3d`) that the claim had
computed rather than transcribed - a §10 removal, not a substitution.

COLL's round 0 is the interesting one, and it belongs with §I rather than here. Its initial failure was
this sentence in `limitations`:

```
... not run. Nothing here is a valuation, a price target or a decision.
```

The validator's unambiguous-term list matches `\bprice target\b` with no context window, so a
disclaimer that denies producing a price target is a violation of the rule against producing one. That
cost COLL a repair round.

---

## F. Abstention / Convergence

This is what the step was for, and the answer is affirmative.

```
honest abstentions      2 / 4        FG and VRRM, both expectation_gap = UNKNOWN
                                     with expectation_gap_confidence = UNKNOWN
empty outputs           0 / 4
attempted unsupported consensus     0
attempted quantified consensus      0
final unsupported consensus         0
```

FG is the cleanest case the repaired contract could have produced: initial response valid, zero
repairs, and the valid thing it produced was an abstention. No round was spent discovering that the
evidence did not support a comparison, which is precisely the loop defect D4-BR's pre-scan addressed.
VRRM reached the same abstention after one schema repair.

`consensus_status` reads `SOURCE_NOT_AVAILABLE` on all four, as it must - it is code-owned and copied
from the bundle - and no output attributed an expectation to an outside view. Tier B's dominant
worry, that a candidate with no comparable evidence returns nothing at all, did not recur: every
candidate returned something, and the two that could not compare said so.

What this does not establish. Four issuers can detect a gross failure and cannot measure a rate, and
FRPT's specific non-convergence was never retested - the frozen six were deliberately not reused.
Two abstentions out of four is also not evidence that the abstention rate is right; it is evidence
that the path is reachable from a cold start.

---

## G. Correctness Gates

```
E1   schema validity                  4/4 = 100.0%  >= 95%    PASS
E2   material claims source-linked    0             == 0      PASS
E3   fabricated consensus             0             == 0      PASS
E4   future source / price leakage    0             == 0      PASS
E5   code-owned numeric integrity     0             == 0      PASS
E6   UNKNOWN discipline                0            == 0      PASS
E7   investment decision leakage      1             == 0      FAIL
E8   priced-in claims supported       0             == 0      PASS
SF1  code-owned state fidelity        0 of 18       == 0      PASS
```

E1-E8 are `d4_1_contract.evaluate_d4_1_gates` as frozen before D4.1 ran; SF1 is
`d4_b_contract.sf1_result` as frozen before Tier B ran. Neither was re-implemented for this run - the
confirmation audit calls the Tier B audit and a test asserts it contains no copy of either.

**E7 is the failure.** One match, in COLL:

```
path    root.market_expectation_evidence.pre_event_positioning_reading[1].text
match   \bsell\b
text    The flat pre-event return suggests the guidance cut was not anticipated in the price
        beforehand: there was neither a run-up nor a sell-off before the event.
```

The sentence describes observed pre-event price behaviour and describes its absence. It recommends
nothing. `DECISION_VOCABULARY` compiles each term as `\b<term>\b` with no context window, and in
"sell-off" the hyphen is a word boundary, so `\bsell\b` matches.

Whether this is new exposure or a regression is answerable from the record, so it was checked rather
than argued: across all 15 D4 final outputs stored anywhere in the H-V2 corpus - D4.1, D4-S1, Tier A
V2/V3 and Tier B included - the token `sell` occurs exactly **once**, and it is this one. E7 passed
every prior graded run because no output had ever used the word. Nothing regressed; a latent
word-boundary route was reached for the first time by an issuer nobody had looked at.

C1/C4 and the other UNKNOWN-discipline rules:

```
C1   eligible 0    violations 0    NOT_EVALUATED
C4   eligible 4    violations 0    PASS
C5   eligible 4    violations 0    PASS
C6   eligible 2    violations 0    PASS
```

C1 is NOT_EVALUATED because its denominator is the POSITIVE-family outputs and this run produced
none. That is the preregistered reading and no output or issuer was adjusted to give it a case; C1 has
now been NOT_EVALUATED in Tier A V3, Tier B and this run.

State Fidelity, under the H1 rule unchanged - metric-bound ASSERTED occurrences only:

```
assertions total        19
assertions evaluated    18
assertions failed       0
not evaluated           1    POLARITY_NOT_ASSERTED
polarity                ASSERTED 18, NEGATED 1, REJECTED_OR_CONTRASTED 0
violating claims        none
```

The one unevaluated assertion was negated, which `SF1_NOT_A_VIOLATION` already says cannot produce a
violation. SF1 has a real denominator here - 18 compared assertions, against Tier B's 15 - so this is
a PASS rather than the NOT_EVALUATED a zero denominator would have forced.

Numeric integrity: `code_owned_numeric_defects` 0 across all four final outputs, M8 atomic ACTIVE.
`compound_claim_coverage_gap` findings 0, so R3's exposure in this run is 0 and R3 is untouched -
still `DEFERRED`, still wired to no gate, exactly as §12 of the brief required.

---

## H. Cost

```
live cost          $11.2124832
hard cap           $30.40
headroom           $19.1875168 unused
```

Per candidate, against the $7.60 worst case:

| ticker | D3 | D4 initial | D4 repair | candidate total |
|---|---|---|---|---|
| AEYE | $1.5350660 | $0.9612518 | $0.4527190 | $2.9490368 |
| COLL | $1.2170178 | $1.0150550 | $0.9264290 | $3.1585018 |
| FG | $1.2483128 | $0.9116970 | $0 | $2.1600098 |
| VRRM | $1.3059288 | $1.1159770 | $0.5230290 | $2.9449348 |

The ceiling was never approached and no candidate was refused for budget.

---

## I. Limitations

**The failing gate is a context-blind lexical match, and this document does not pretend to have
decided what to do about it.** §13 of the brief forbids a code change inside this run, so the finding
is recorded and the result stands. What can be said factually: the sentence asserts no decision, the
token had never appeared in any prior H-V2 output, and the detector has no polarity or compound-word
handling at all.

**The same class of defect fired twice in this run, in two different components.** The audit's E7
detector matched `sell` inside "sell-off"; the generation-time validator matched `price target` inside
"Nothing here is a valuation, a price target or a decision". The second cost a repair round, the first
cost the verdict. `validation_v2.classify_investment_language` resolves "fair value" and
"approve/reject" through a context window - that is D3.2 §G's false-positive work - but
`_UNAMBIGUOUS_TERMS` matches unconditionally, and `DECISION_VOCABULARY` in the audit has no window
either. The model's natural way to comply with a prohibition is to state that it is complying, which
is also the natural way to trip an unconditional match on the prohibited phrase. This is one finding
with two instances, not two findings.

**n=4 detects gross failure and measures no rate.** 4/4 is 100% against a 95% threshold because that
is what the frozen gate computes at this denominator; it is not a 100% convergence rate.

**FRPT remains unproven.** D4-BR showed a valid abstention existed one edit from FRPT's stored
payload and explicitly did not claim it would converge. This run did not retest it, by design, and
nothing here changes that status.

**R3 is still deferred with no new information.** Its exposure in this run is 0, so the one
observation that moved it to OBSERVED is still Tier B's CRK.

**The repair-dependence downgrade never fired, so it is untested on live data.** The rule
(>= 3 of 4 candidates using the full repair budget -> PASS_WITH_LIMITATIONS) was frozen before the run
and the run produced 1. Its behaviour is asserted by unit test only.

---

## J. Verdict

```
H-V2-D4-BR-C
= FAIL

E1                4/4 final valid       PASS
E7                1 decision leak       FAIL   <- core gate, threshold 0
every other gate                        PASS
```

`d4_b_verdict` returns FAIL on any gate FAIL, core or not, and E7 is additionally in
`D4_B_CORE_GATES`, where a failure is a FAIL and never a reportable limitation. The
PASS_WITH_LIMITATIONS branch was not available: the wrapper this step added can only move a PASS down
and has no path from FAIL to anything else, which is asserted over every gate-status combination the
frozen function can produce.

The brief's §14 FAIL branch is written for `D4 final valid <= 3/4`. That is not what happened, and the
distinction matters for what comes next: **this is not a convergence failure.** The convergence
question the step existed to ask was answered affirmatively on every measure it named - 4/4 final
valid, 0 empty outputs, 2 honest abstentions, 0 attempted and 0 final unsupported consensus, SF1 clean
on a real denominator, numeric integrity clean. The gate that failed is an unrelated detector with a
word-boundary exposure that 15 prior outputs never reached.

Deliberately not decided here, because §14 and §19 both put it outside this step: whether to repair
the detector, whether that repair is a gate-semantics change requiring a freeze before the run it
grades, or whether D4 should be simplified or deferred. This step is not authorized to add a repair
rule, and adding one to convert this FAIL would be exactly the "another small repair rule" §14
forbids.

---

## K. D5 Authorization

```
D5 Valuation Fundamentals
= NOT READY
```

`d5_authorization(FAIL)` returns `NOT READY`, and nothing in this document overrides it. D5 contract
design is not authorized.

---

## Final Declaration

```
sample substituted?                  NO
E1 threshold changed?                NO
gate semantics changed?              NO
code changed during the run?         NO
candidate re-run after a defect?     NO
repair rule added to fix the FAIL?   NO
C1 forced to evaluate?               NO
R3 implemented?                      NO
historical Tier B verdict changed?   NO
D5?                                  NO
valuation / APPROVE-WATCH-REJECT?    NO
forward returns?                     NO
existing unrelated dirty modified?   NO
push?                                NO
```

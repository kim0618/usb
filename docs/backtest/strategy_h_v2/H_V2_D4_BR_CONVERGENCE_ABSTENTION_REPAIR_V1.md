# H-V2-D4-BR - Convergence / Abstention Contract Repair

```
H-V2-D4-BR
= READY FOR DISJOINT CONVERGENCE CONFIRMATION

cause            B, dominant - the schema afforded abstention; the repair loop could not reach it
cause A          NO  - both failed payloads are ONE edit from valid
cause C          PARTIAL, FRPT only - genuinely non-comparable evidence, correctly refused
live Opus calls  0
live cost        $0.00
Tier B verdict   FAIL, unchanged
```

The question §0 asked was whether FRPT and SPSC produced nothing because the contract cannot express
an honest "I do not know". It can. Both candidates reached a payload that is a single edit away from
validating, and SPSC's first repair had already chosen the abstention on both of its refused
comparisons. What defeated them was the loop around the schema, not the schema: one validation error
hid another, so a two-round repair budget was spent discovering defects one at a time instead of
fixing them.

Two things in this document contradict what the Tier B write-up reads. Both are recoveries from the
raw attempts, and neither changes a gate, a threshold or the verdict. The terminal failure reason for
each failed candidate was never stored, so the run was read as having ended on the error it had last
been repairing; it had not. And SPSC's second repair, recorded as an attempt to fabricate a
consensus, was a reworded quotation of a generic 10-K risk factor carrying its own disclaimer. The
Tier B verdict is FAIL at 4 of 6 either way.

Not an alpha result, not a backtest, and not a statement about any issuer.

---

## A. Tier B Immutable Failure

Preserved exactly, and asserted rather than recomputed - `replay_d4_br_convergence.HISTORICAL` carries
these as literals and `test_the_historical_verdict_is_immutable` fails if any of them moves.

```
sample                     frozen 6
D3 final valid             6 / 6
D4 final valid             4 / 6
FRPT                       no final output
SPSC                       no final output
E1                         4/6 = 66.7% against a frozen >= 95%     FAIL
E2-E8                      PASS
SF1                        PASS
C1                         NOT_EVALUATED
fabricated consensus       0 in every final output
Tier B                     FAIL
D5                         NOT READY
```

`H_V2_D4_TIER_B_DISJOINT_LIVE_VALIDATION_RESULT_V1.md` and the run's gate audit are unedited. No
E1 threshold change, no C1/C4 change, no Gap semantics change, no confidence change, no consensus
provider, no R3 implementation, no Tier B rerun, no D5, no valuation, no APPROVE/WATCH/REJECT, no
forward returns.

The audit method was an offline replay of the repaired validator over the stored raw responses from
run `D4_B-20260930T053146Z`. Zero live calls, and the replay reports `live_calls: 0` as a field so
the claim rests on the artifact rather than on this sentence.

---

## B. FRPT

`SCHEMA_VALIDATION_FAILED` after 2 repairs. D4 leg $1.9915; $3.2854 including its D3 leg.

The disputed item is `guidance_assessments[2]`: an Adjusted Gross Margin **floor**, guided as "at
least 48%" and then "at least 49%" for FY2027. An open-ended floor is not a range, and a change
between two floors is not a range change.

| round | claim | required operands | available | missing | validator | repair action |
|---|---|---|---|---|---|---|
| initial | `state=RAISED`, prev 48/-, cur 49/- | previous_low, previous_high, current_low, current_high, unit | previous_low, current_low, unit | previous_high, current_high | REJECT - half-stated range | restated the metric name, nulled all four bounds, kept RAISED |
| repair 1 | `state=RAISED`, all bounds null | same | unit only | all four | REJECT - RAISED asserts a change | downgraded `state` to UNKNOWN |
| repair 2 | `state=UNKNOWN`, all bounds null | none; UNKNOWN is an absence | - | - | **accepted at item level** | - |

Repair 2 abstained correctly. It failed anyway, and on a rule that had nothing to do with the
operands:

```
per-metric states ['MAINTAINED', 'RAISED'] disagree, so the overall state is MIXED
```

`overall_guidance_state` read `RAISED` in all three rounds while the five per-metric states were
{RAISED, RAISED, ?, MAINTAINED, MAINTAINED}. **That violation was present in the initial response and
was invisible for two rounds.** Pydantic runs child validators before a parent's `mode="after"`
validator, so while any one assessment was malformed the aggregate rule never ran. Replaying the
initial payload with `ga[2]`'s two bounds completed and nothing else changed produces the aggregate
error immediately - which is the proof, not an inference.

So FRPT needed three serial discoveries against a two-round budget. Abstaining on `ga[2]` is what
moved the remaining definite states into disagreement, so the honest abstention is what *created* the
third defect - and the round that would have fixed it did not exist.

Cause C applies here and only here: the evidence genuinely is non-comparable. The contract was right
to refuse. What it then did wrong was treat the downstream consequence as an output failure.

---

## C. SPSC

`SCHEMA_VALIDATION_FAILED` after 2 repairs. D4 leg $2.1831; $3.3939 including its D3 leg.

| round | claim | required operands | available | missing | validator | repair action |
|---|---|---|---|---|---|---|
| initial | `result_vs_guidance[0]` and `[1]` = `ABOVE_COMPANY_GUIDANCE`, reported 197.8 / 66.6 | reported_value, prior_guidance_low, prior_guidance_high, unit | reported_value, unit | both prior-guidance bounds | REJECT x2 - one-sided comparison | downgraded BOTH to UNKNOWN |
| repair 1 | both `UNKNOWN`, reported values retained | none; UNKNOWN is an absence | - | - | REJECT - consensus attribution elsewhere | reworded two strings |
| repair 2 | both `UNKNOWN` | - | - | - | REJECT - consensus attribution elsewhere | budget exhausted |

**Repair 1 is a textbook §10 repair: both refused comparisons downgraded to an existing UNKNOWN
state, no evidence substituted, nothing added.** It was rejected for something else entirely. The
rejection quoted `'consensus expect'`, and the string it had matched was the model's own
`unknown_fields[0]`, which read:

```
consensus expectations
```

`unknown_fields` is the abstention channel. Its contract is "the things this output does not know",
and the entry was the model declaring that quantity unknown. The affirmation detector scanned it and
read the name as a claim about the thing named.

Repair 2 changed that entry to `"analyst consensus data (no source connected; not established by
available evidence)"`, which passes. It also reworded `supporting_claims[3]`, and that is what
defeated the candidate:

```
The company's 10-K risk factors include a generic statement that operating results may fall short
of what securities analysts and investors anticipate or be less than any guidance the company
provides, in which case the trading price could decline significantly; this is a generic risk
statement and the evidence does not establish what any such outside view is.
```

Two things about this sentence. It is a description of a boilerplate risk factor, and it carries its
own disclaimer. The detector reads polarity per clause, and the disclaimer sits behind a semicolon, so
the first clause was read alone and `analysts ... anticipate` matched. The **initial** wording of the
same claim - "may fall below the expectations of securities analysts and investors" - passes the
detector cleanly. The repair reworded a sentence that was already acceptable into one that was not.

### What this means for the fabrication reading

The Tier B write-up calls repair 2 "an outright fabricated consensus" and "the single most important
behavioural finding of this run". The raw attempts do not support that. Neither flagged string
asserts a consensus figure, neither attributes a view the model invented, and `consensus_status`
reads `SOURCE_NOT_AVAILABLE` correctly in all three rounds. One was an abstention label; the other was
a quoted risk factor with a disclaimer in the wrong clause.

E3's historical result is unchanged and correct: 0 fabricated consensus in final outputs. What changes
is the reading of *why* - it was not that the gate caught an attempt. §I records this as a diagnostic
distinction rather than as an edit to the Tier B document.

---

## D. Schema Affordance

Per failed comparison field, using only states the enums actually have. No state was invented for this
audit.

| field | abstention states available | can the schema represent honest abstention? |
|---|---|---|
| `guidance_assessments[].state` | `UNKNOWN`, `NOT_PROVIDED`, `WITHDRAWN`, `INITIATED`, `MAINTAINED` | **YES** - FRPT repair 2 used `UNKNOWN` and it validated at item level |
| `result_vs_guidance[].state` | `UNKNOWN`, `NO_PRIOR_GUIDANCE` | **YES** - SPSC repair 1 used `UNKNOWN` on both and they validated |
| `overall_guidance_state` | `UNKNOWN`, `NOT_PROVIDED`, `WITHDRAWN`, and `MIXED` for disagreement | **YES** |
| `consensus_status` / `estimate_revisions_status` | `SOURCE_NOT_AVAILABLE` (code-owned, copied from the bundle) | **YES** - and the model cannot report anything else |
| `expectation_gap` / `expectation_gap_confidence` | `UNKNOWN` / `UNKNOWN`, required together by C5 | **YES** |
| `priced_in_assessment.state` | `UNKNOWN`, which forbids a confidence | **YES** |
| `unknown_fields[]`, `limitations[]` | free text naming what is not known | **YES, after the §G repair.** Before it, a consensus-shaped entry in `unknown_fields` was read as an assertion |

There is no `NOT_COMPARABLE` and no `INSUFFICIENT_EVIDENCE` enum member anywhere in this schema, and
this document does not pretend otherwise. `UNKNOWN` plus a named reason is how non-comparability is
represented; §F adds the named reason as a code-owned value rather than adding an enum member to a
schema two graded runs were scored against.

**Cause A is answered NO.** Every failed comparison had a reachable abstention state, and both failed
candidates reached one.

---

## E. Operand Completeness

Deterministic, declared in `comparability.REQUIRED_OPERANDS`, derived from the real data model rather
than from the brief's example.

```
GUIDANCE_RANGE_CHANGE                 RESULT_VS_COMPANY_GUIDANCE
    previous_low                          reported_value
    previous_high                         prior_guidance_low
    current_low                           prior_guidance_high
    current_high                          unit
    unit
```

The schema has no separate metric or period field to compare across the two sides of a guidance
change: both ranges live on one `GuidanceMetricAssessmentV1`, which carries a single `metric` and a
single `period_label`. Same-metric and same-period are therefore structural, not checkable - the
actual structure takes precedence over the brief's illustrative list, and this is where it differs.

`unit` is required only once some other operand is present. An item with no numbers at all is an
absence, and demanding a unit for nothing would turn "no guidance was given" into an error.

Comparative states, which require the full set: `RAISED`, `LOWERED`, `MIXED`,
`ABOVE_COMPANY_GUIDANCE`, `WITHIN_COMPANY_GUIDANCE`, `BELOW_COMPANY_GUIDANCE`. `INITIATED` is not one
- new guidance with no predecessor is a fact about one range.

---

## F. Code-Owned Comparability

`expectation/comparability.py`. `ComparabilityVerdict` is computed from operands and nothing else:

```
comparison_allowed              = false
comparison_unavailable_reason   = MISSING_PREVIOUS_HIGH
missing_operands                = (previous_high, current_high)
```

That is FRPT's actual `ga[2]`. The reasons are one value per missing operand -
`MISSING_PREVIOUS_LOW`, `MISSING_PREVIOUS_HIGH`, `MISSING_CURRENT_LOW`, `MISSING_CURRENT_HIGH`,
`MISSING_REPORTED_VALUE`, `MISSING_PRIOR_GUIDANCE_LOW`, `MISSING_PRIOR_GUIDANCE_HIGH`, `MISSING_UNIT`
- because a single `INSUFFICIENT` would tell a repair prompt nothing it did not already know, which is
what FRPT's first two rounds cost.

**The AI cannot modify these values.** There is no field for them in any request schema, and every
analysis model is `extra="forbid"`, so a response that writes `comparison_allowed: true` is rejected
outright rather than quietly believed. Being honest about the limit: the model still *supplies* the
operands, because they are transcriptions from a filing and no code path can invent them. What it can
no longer do is name a comparative state while the operands are absent, and when they are absent the
code now says which one and which states remain open.

---

## G. Abstention Contract

Required operands missing is a normal information state. Three changes make that reachable within a
bounded repair budget, all expressed in states the contract already had.

**1. The structural pre-scan (§16, the dominant fix).** When pydantic rejects a payload,
`validate.assemble_and_validate_d4` now also runs `comparability.structural_prescan` over the raw dict
and appends anything pydantic could not reach. It reports more, earlier. It accepts nothing pydantic
would reject and rejects nothing pydantic would accept - every rule in it restates a rule the schema
enforces, and `test_the_prescan_is_silent_on_a_valid_payload` pins that. On FRPT's initial payload:

```
before   1 error    guidance_assessments[2] bounds half-stated
after    3 errors   + the aggregate MIXED violation
                    + comparison_allowed=false, missing previous_high / current_high
```

**2. `unknown_fields` is an absence channel.** Scanned under the quantified rule only. Naming the
quantity you are declaring unknown is now a denial by construction; attaching a *figure* to it is
still rejected, because the figure is the fabrication. This single change turns SPSC's repair 1 from
a failure into a valid output.

**3. The attribution rejection now says where the denial has to sit.** It states that polarity is read
per clause and that a disclaimer behind a semicolon does not reach the clause above it, with a passing
and a failing example. The detector is unchanged - no phrase was added to any list, and nothing that
was rejected is now accepted. Honest expression was always representable; the message never said how.
Measured against the detector: "results may fall short of what analysts anticipate, which the evidence
does not establish" passes, and the same content split at a semicolon does not.

An empty final output remains E1-invalid. Abstention means converging on `UNKNOWN`; it does not mean
returning nothing.

---

## H. Repair Contract

Permitted, unchanged from §10: remove an unsupported claim; downgrade to an existing `UNKNOWN` or
absence state; fix a numeric transcription; fix an enum or schema format error; attach evidence that
was already present.

Forbidden, and now enforced - `expectation/repair_provenance.py`. The lock is built from the initial
response's own payload and frozen for the candidate:

```
source_ids      every source_id / source_ids value in the initial answer
evidence_ids    every evidence_id / evidence_ids value
categories      the prefix before the first colon - here, SEC and CODE
```

Category and id are enforced differently, and conflating them would break §10.

**Category is frozen at the initial call.** A repair that cites a provider category the initial answer
did not - `CONSENSUS:`, `IBES:` - is rejected even if it otherwise validates, because §11's
prohibition is about where the answer came from, and a payload that passes every content gate while
citing a new provider is exactly the substitution the content gates cannot see. There is no legitimate
reason a repair needs a new category.

**Ids are not frozen that way.** §10 explicitly permits a repair to attach already-present valid
evidence, and the usual fix for a citation defect is to cite the bundle evidence id the claim should
have carried - which the initial answer, by definition, did not. So an id is rejected only when it
falls outside the code-owned citable universe: the package's source and evidence ids, the bundle's
valid evidence ids, and the code facts. That is what §12's "a source or evidence that does not exist"
means. An id-level freeze would have rejected the repair the contract asks for.

A new category is reported first and its ids second: the category is the finding, the ids are the
detail.

The lock is one-directional. **Removing** a source is always allowed and the set may shrink to empty -
that is what "remove the unsupported claim" means.

What the lock would and would not have caught on Tier B, stated plainly: SPSC's repair 2 introduced no
new source id, so the lock would not have stopped it. It is not a substitute for the fabrication
check. It closes the route where a refused thesis returns cited to something that was never in the
initial answer - the route a consensus provider opens the moment one exists.

---

## I. Fabrication Diagnostics

Three counters on the record, because Tier B had one and conflated two different findings.

```
attempted_consensus_attributions    the attribution rule fired in some round
attempted_quantified_consensus      an attributed expectation carried a FIGURE
final_fabricated_consensus          fabrication in the final output
```

Historical Tier B, as the raw attempts support it:

```
final_fabricated_consensus          0        (unchanged; E3 as graded)
attempted_consensus_attributions    >= 1     (SPSC, two rounds)
attempted_quantified_consensus      0        (all six candidates, including SPSC)
```

The middle number is the one Tier B reported as a fabrication attempt. The bottom one is the number
that means what "attempted fabricated consensus" sounds like, and it is zero. E3's historical result is
not changed and the Tier B document is not edited; the distinction is recorded here and in the record
schema so the next run's numbers say which happened.

---

## J. Reporting Fix

Two gaps, both in how a candidate with no final output was written down. Gate semantics unchanged:
`graded` still means "has a final output", and every defect count still runs over `graded` alone.

**The terminal failure was never stored.** Each repair round recorded `failure_codes_before` - the
error that *prompted* it - so the error that defeated the last round went nowhere. The record now
carries `terminal_failure_codes` and `terminal_failure_details`. This is not a cosmetic gap: it is why
FRPT was read as having failed on guidance bounds when it failed on the aggregate state rule, and why
SPSC was read as having failed on a fabrication when it failed on a reworded quotation. Both were
recovered by replay, and `test_the_terminal_failure_reasons_were_recovered_not_recorded` asserts the
historical record's silence so the gap cannot be quietly reinterpreted later.

**The audit emitted nothing for them.** `audit_strategy_h_v2_d4_b.audit_run` wrote
`{has_final_output: False, defects: {}}` and appended the row - no initial attempt, no repair count,
no cause, no cost. The two rows that most needed explaining were the two that carried no explanation.
Every row now carries initial validity, initial failure codes, repair rounds, repair causes, terminal
failure codes and details, cost, the three fabrication counters, and the record's schema version.

`LEDGER_SCHEMA_VERSION` moves to `h_v2_d4_ledger_v2`. Every new field has a default so a v1 record
still loads; the version is what says whether its silence means anything.

---

## K. R3 Status

```
R3 compound set-valued semantics    = OBSERVED / DEFERRED
observed exposure                   = 1        (was 0 across the whole D4-H corpus)
```

The status lives in a new constant, `M8_R3_STATUS_AFTER_TIER_B`. Tier B's own
`M8_R3_COMPOUND_SET_VALUED = "DEFERRED"` is left exactly as it was, because that literal is what the
run recorded and what its gate audit reports - rewriting it would change what a graded run is read to
have said. Two of Tier B's frozen tests assert it, and they still pass unedited.

CRK cites a 3-month and a 6-month figure in one claim and restates only the 3-month one. That is R3's
exact shape - a set-valued citation whose atomic reading cannot distinguish a partial restatement from
a complete one.

Not implemented here. R3 changes M8's unit of comparison, which is a gate-semantics change that §1
forbids, and a change to what E5 measures has to be frozen before the run it grades. What changes is
the status: the deferral is now a scheduling decision rather than an absence of evidence.
`M8_SCOPE_NOTE` still reads "wired to no gate", and a test holds it there.

---

## L. Offline Counterfactual

`app/dev/replay_d4_br_convergence.py`. Zero live calls, `$0`, reported as fields on the result.

Each counterfactual is **one edit** on a payload the model actually produced - both of them moves §10
explicitly permits.

```
FRPT    base repair 2    overall_guidance_state RAISED -> MIXED                    VALID
SPSC    base repair 1    unknown_fields[0] 'consensus expectations'
                         -> 'analyst consensus: no source connected'               VALID
```

FRPT's edit is the arithmetic consequence of the abstention it had already chosen, and strictly less
directional than `RAISED`. SPSC's edit renames an abstention label; it removes no evidence and adds no
claim.

Per-round validity under the repaired contract, over the same stored bytes:

```
          initial   repair 1   repair 2
IDCC      2 err     1 err      VALID
DORM      1 err     VALID       -
FRPT      3 err     3 err      1 err
TG        VALID      -          -
CRK       VALID      -          -
SPSC      4 err     VALID      1 err
```

Three readings, kept separate.

**SPSC would have converged.** Its repair 1 validates under the repaired contract. That is a payload
it actually produced, not a reconstruction.

**FRPT is not proven to converge, and this document does not claim it.** Its stored repair 2 still
carries the aggregate error, because that payload was written by a model that had never been told the
rule existed. What is proven is that a valid abstention object exists one edit away, and that the
information needed to reach it is now delivered in round 1 instead of round 3. Whether the model uses
it is a live question and §1 forbids asking it.

**IDCC is the corroboration.** It hit the identical masked aggregate rule after an unrelated child
error and converged on the last round available to it. The difference between the only D4 failure of
the run and a pass was one masked error, not one unsupported claim.

Tier B's historical failure is not changed to a success. `HISTORICAL` is asserted, not recomputed.

---

## M. Tests

`backend/tests/strategy_h_v2/expectation/test_d4_br_convergence.py`, 43 tests, all passing. The
replay-backed ones skip on a clean checkout because `data/runtime` is gitignored, so every contract
claim is also asserted on an inline fixture.

```
half previous guidance range -> comparative state forbidden          PASS
missing range bound -> honest abstention valid                       PASS
missing company-guidance operand -> ABOVE/BELOW forbidden            PASS
numbers without a unit are not comparable                            PASS
code-owned fields cannot be supplied by the model                    PASS
unknown_fields name is not an assertion / a FIGURE still is          PASS
masked aggregate rule is reported alongside the child error          PASS
prescan mirrors the schema rule / silent when valid / junk-safe      PASS
repair may downgrade to UNKNOWN                                      PASS
repair may remove the claim                                          PASS
repair universe may shrink                                           PASS
repair may attach already-present valid evidence                     PASS
repair cannot introduce a consensus category                         PASS
repair cannot cite an id outside the citable universe                PASS
no citable set supplied -> no id-level check                         PASS
the lock watches the top-level sources[] list                        PASS
validation contract version moves; graded ones stay readable         PASS
FRPT actual fixture - valid abstention representable                 PASS
SPSC actual fixture - valid abstention representable                 PASS
terminal failure recovered, and absent from the live record          PASS
invalid-final candidate retains repair rounds in the audit row       PASS
attempted vs final consensus counted separately                      PASS
historical Tier B verdict immutable                                  PASS
four graded candidates still validate - no regression                PASS
D3 figure is a planning estimate that was exceeded                   PASS
Tier B hard cap unchanged at $45.60                                  PASS
R3 observed, still deferred, gate list unchanged                     PASS
confirmation sample regenerates / disjoint / frozen E1 / no budget   PASS
```

Existing H-V2 suite: `1081 passed, 1 skipped` (including these 43). No regression.

---

## N. New Confirmation Proposal

The frozen six are **not** reused. They were the denominator that produced the FAIL and the repairs
were cut from their raw attempts - the `unknown_fields` exemption exists because SPSC tripped on it and
the pre-scan exists because FRPT and IDCC did. Re-running them would measure how well a fix fits the
cases it was designed from.

`expectation/d4_br_contract.py`, frozen before any confirmation call exists, deterministic by the same
seeded-hash convention D3.1 Batch 2, D3.3 and Tier B all used:

```
seed       H_V2_D4_BR_CONFIRMATION_V1
excluded   54 CIKs - every CIK any H live call has ever touched
n          4        2 FULL + 2 CORE

AEYE   0001362190   FULL   E3_P1_HIGH
COLL   0001267565   FULL   E3_P1_HIGH
FG     0001934850   CORE   E3_P2_MEDIUM
VRRM   0001682745   CORE   E3_P2_MEDIUM

checksum   69598e04ad0e99e6b438567301be46b71340a43382cf5d3e3a5c2503775aa442
```

`regenerate_confirmation_sample()` re-derives the literal from the D2.1 snapshot, so the list is
checked against a fresh computation rather than against a hash of itself. The purpose is convergence /
abstention generalization.

**Success.** E1's threshold is unchanged at the frozen `>= 95%`. At n=4 that is 4/4 final valid,
because 3/4 is 75%; `e1_confirmation_passes` computes it from the frozen constant rather than
restating a number. The existing correctness gates apply unchanged, by reference: E2-E8 via
`evaluate_d4_1_gates`, SF1 via `state_fidelity.GATE_ID`. A confirmation that passed E1 while leaking a
fabricated consensus is not a pass.

This step authorizes no spending. The contract carries no runner and no budget, and a test asserts
that.

---

## O. Verdict

```
H-V2-D4-BR
= READY FOR DISJOINT CONVERGENCE CONFIRMATION
```

The §20 frozen acceptance conditions, each met before the per-round results above were written up:

```
FRPT -> valid abstention path representable          YES   one enum edit, offline, $0
SPSC -> valid abstention path representable          YES   one string edit, offline, $0
unsupported repair substitution -> rejected          YES   provenance lock, category and id
historical Tier B FAIL -> unchanged                  YES   asserted, not recomputed
```

Answering §0 directly, and more than one cause applies:

```
A. schema does not afford honest abstention                     NO
B. schema affords it but prompt/repair fails to choose it       YES - dominant
C. issuer evidence genuinely non-comparable, treated as
   an output failure                                            PARTIAL - FRPT only
```

B is dominant and its mechanism is a code defect rather than a prompt one: errors were masked, so
discovery consumed the repair budget. SPSC's loop additionally spent a round on its own abstention
label.

**D5 remains forbidden.** This step moved no gate and graded no issuer. What it earned is the right to
ask four unseen issuers whether the convergence behaviour generalizes, which is a question only a live
run can answer and which this step is not authorized to start.

Honest limitations. FRPT's convergence is unproven, not demonstrated. The confirmation sample is four,
which can detect a gross failure and cannot measure a rate. C1 is still `NOT_EVALUATED` and this step
did nothing about it. The pre-scan reimplements the aggregate rule a second time to run where pydantic
cannot, and two implementations of one rule can drift - a test pins them together, which bounds the
risk rather than removing it.

---

## Final Declaration

```
live Opus calls?                     NO
live cost?                           $0
historical Tier B verdict changed?   NO
E1 threshold changed?                NO
C1/C4 changed?                       NO
Gap semantics changed?               NO
consensus provider added?            NO
R3 implemented?                      NO
Tier B rerun?                        NO
D5?                                  NO
valuation?                           NO
APPROVE/WATCH/REJECT?                NO
forward returns?                     NO
existing unrelated dirty modified?   NO
push?                                NO
```

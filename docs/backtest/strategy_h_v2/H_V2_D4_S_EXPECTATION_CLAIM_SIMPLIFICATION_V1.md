# H-V2-D4-S - Expectation Claim Structural Simplification

```
H-V2-D4-S
= READY FOR LIMITED LIVE SMOKE

live Opus calls          0
live cost                $0.00
Final Tier A V3 verdict  UNCHANGED (MECHANICAL READY / Initial Validity UNSTABLE)
Tier B                   NOT AUTHORIZED
D5                       NOT READY
```

This is not a D4 quality PASS. It is a structural change to WHERE the question "does this pipeline
know what the market expects?" is answered, validated offline against bytes that already exist.

Two things were found that the brief did not anticipate, and both are recorded here rather than
smoothed into the design story:

- **The 17 unsourced-claim findings were not defects at all.** Every one is a claim using
  `ClaimV2`'s COMPOUND citation form, which the schema requires to leave `source_id` null - while
  the audit tested `not claim.get("source_id")` and called the result "no citation". The audit was
  demanding an output the schema forbids (§H).
- **Fixing that exposed a second, real blind spot.** The same bug had been routing compound claims
  out of the loop before M8's numeric-ownership check ran, so M8 has never in any run been applied
  to a compound claim. Closing it would flip M8 from PASS to FAIL on two historical runs, which
  §13/§16 of the brief forbid, so it is reported and left open (§H.4).

---

## A. Why D4-S Exists

D4 asked three layers the same question and let the weakest one decide.

```
code-owned bundle    consensus.status / estimate_revisions.status   (the actual fact)
model output         consensus_status / estimate_revisions_status   (a copy, checked)
free prose           consensus_language.classify_sentence           (the decider)
```

The prose layer decided, and it decided by phrase membership: a sentence naming a consensus referent
and attributing an expectation was ASSERTED unless it also carried one of ~30 listed absence phrases.
Two consecutive live runs had their first-attempt validity set by that list not happening to carry
the negation the model chose.

Final Tier A V3, §K of its result document:

```
SCCO initial
  "Taken together, the evidence gives no basis for saying market expectations lag the evidenced
   progress, and none for saying they run ahead of it."
    -> ASSERTED, trigger "market expectation noun phrase"

GOOG initial
  "...and none of these shows whether the market's expectation lags or leads the evidenced
   progress."
    -> ASSERTED, trigger "market expectation noun phrase"
```

Neither fabricates a consensus. Both deny having one. "gives no basis for saying" and "none of these
shows whether" are simply not on the list.

The history matters because it shows the shape of the problem rather than a one-off. D4.1's version
was non-convergent (the error demanded the sentence it rejected). D4.3R2 closed the bare-`market`
referent gap and, in closing it, opened this false-positive surface. Each closure was a longer list,
and each longer list moved the failure somewhere else. D4-S changes the mechanism instead.

## B. Final Tier A Immutable Baseline

Unedited, and nothing below supersedes it.

```
H-V2-D3.3                    PASS
H-V2-D4 Final Tier A V3      MECHANICAL READY
Initial Validity             1 / 3 = UNSTABLE
Tier B                       NOT AUTO-AUTHORIZED   (preregistered Case B)
D5                           NOT READY

run_id                       D4_2_A-20260930T012115Z
sample                       SCCO / GOOG / BSY
M1-M12                       12 PASS
consensus fabricated         0
numeric mutation (final)     0
D5/D6 leakage                0
C1                           NOT_EVALUATED (0 eligible)
new live cost                $3.8872704 of $18.00
```

`H_V2_D4_4A_FINAL_TIER_A_V3_MECHANICAL_RESULT_V1.md` and
`H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md` were not modified by this step.

### Repository state

```
branch                       main
HEAD at D4-S preflight       19b4cae  test(strategy-h-v2): complete final D4 tier-a validation
HEAD at write-up             a91a41c  feat(crypto): simplify Binance live trading workflow
origin/main vs HEAD          behind 0 / ahead 4
staged at preflight          0
modified at preflight        24   (H-V2 related: 0)
untracked at preflight       many
```

**HEAD moved during this session, from a concurrent session, and this is reported rather than
smoothed over.** `a91a41c` is a crypto/frontend commit that absorbed three frontend files which had
been dirty at preflight. It does not touch anything this step measures:

```
git diff --stat 19b4cae..HEAD -- <all six H-V2 paths>   -> empty
```

No existing unrelated dirty file was modified, staged or deleted at any point. The 21 unrelated
modified files present at preflight are still present, unchanged.

## C. Root Cause

Not "the regex was too broad". The regex was being asked a question it cannot answer.

```
what code KNOWS          consensus.status == SOURCE_NOT_AVAILABLE for 2,010 / 2,010 candidates
what the prose scan was
asked to decide          whether this sentence means that same thing
```

Given that code already knows no consensus source exists, the only remaining question about a
sentence is its POLARITY: does it affirm an expectation, or deny knowing one? That is a question
about claim structure, and a phrase list answers it only for the phrasings someone thought of.

The proof that a list cannot close this: `_ABSENCE` carries
`\bwithout\s+(any\s+)?(a\s+)?(consensus|analyst)\b`, so the frozen classifier routes

```
"Without consensus data, it is clear the market expects 20%."   -> ABSENCE
```

to ABSENCE. A fabrication passes because it contains an absence phrase, while an honest denial fails
because it does not. Both errors are the same mistake, in opposite directions.

## D. Structured Expectation State

`backend/app/backtest/strategy_h_v2/expectation/expectation_state.py`

```
contract    h_v2_d4_s_expectation_state_v1
owner       CODE. Derived from the bundle. The model never supplies or edits it.
```

```json
{
  "status": "UNKNOWN",
  "basis": [],
  "consensus_status": "SOURCE_NOT_AVAILABLE",
  "estimate_revisions_status": "SOURCE_NOT_AVAILABLE",
  "market_expectation_claim_allowed": false,
  "source_ids": [],
  "evidence_ids": [],
  "limitations": ["CONSENSUS expectation evidence is SOURCE_NOT_AVAILABLE: no market-expectation statement may rest on it", "..."],
  "contract_version": "h_v2_d4_s_expectation_state_v1"
}
```

```
ExpectationKnowledgeStatus
  UNKNOWN       no expectation block is readable      <- every candidate in the current data layer
  PARTIAL       exactly one is readable
  ESTABLISHED   both are readable
```

A block is a basis only when `AVAILABLE` or `PARTIAL`. `NOT_FOUND_FOR_CANDIDATE` is not a basis:
"we can read this source type and it is empty for this issuer" is an absence, and an absence cannot
be the evidence that the market expects something.

`market_expectation_claim_allowed` is the field every other layer keys off. It is False for every
issuer today, which is the measured fact rather than a conservative default.

### Field ownership, before and after

| field | owner | source | validator | audit consumer |
|---|---|---|---|---|
| `bundle.consensus.status` | CODE | `evidence_builder` | `EvidenceBlock._status_matches_content` | C4 ceiling, E3 gate |
| `bundle.estimate_revisions.status` | CODE | `evidence_builder` | same | C4 ceiling |
| `market_expectation_evidence.consensus_status` | AI (copy) | model output | `check_expectation_state_contradictions` rule 1 | M4 |
| `market_expectation_evidence.estimate_revisions_status` | AI (copy) | model output | same | M4 |
| `expectation_gap` | AI | model output | `_unknown_gap_has_unknown_confidence`, C1, C6 | M6 |
| `expectation_gap_confidence` | AI | model output | C4 ceiling (`apply_confidence_ceiling`) | M7 |
| `priced_in_assessment` | AI | model output | C7 + `_non_unknown_is_fully_supported` | E8 |
| `limitations` / `unknown_fields` | AI | model output | `_no_investment_language_v2` | (prose scan only) |
| `supporting_claims` | AI | model output | `ClaimV2` + citation resolution | E2 |
| **`ExpectationKnowledgeStateV1`** | **CODE (new)** | **derived from bundle** | **is itself the authority** | **runtime + audit + manual helper** |

The last row is the change. Availability used to be an emergent property of three layers agreeing;
it is now one object.

## E. Free-Text Guard

The classifier is kept and is not relaxed. Its ROLE changed.

```
before   CORE TRUTH DECIDER   decided availability AND polarity, by phrase membership
after    LEAKAGE GUARD        availability comes from the structured state;
                              polarity comes from claim structure
```

`consensus_language.classify_sentence` is behaviourally **unchanged** - all 70 of its frozen
phrase-by-phrase tests pass untouched. Two mechanical edits were made to it:

1. `attribution_triggers()` was extracted out of `classify_sentence`, returning each match's SPAN as
   well as its label. `classify_sentence` now calls it and composes the same verdict from the same
   labels in the same order. A structural rule needs to know WHERE an attribution sits, which a
   label alone cannot say.
2. A V1 substring's span is snapped to token boundaries. `"consensus expect"` is a prefix of
   `"consensus expectations"` - the original D4.1 collision - so a raw substring span ends mid-token
   and leaves `"ations"` looking like a neighbouring content word.

One genuine addition, which the brief's §8 names: `_INVESTOR_VERB`, scoped exactly like the existing
`_MARKET_VERB` (the verb directly at the subject's side). `"Investors expect margin expansion."`
classified NEUTRAL before - it named no listed referent - and is now caught.

### The polarity rule

An attribution is DENIED, not affirmed, when it occupies one of four structural positions relative
to a NEGATED EPISTEMIC FRAME. A frame is a negator/hedge governing a predicate of knowing or
showing, adjacent up to function words in either order.

```
COMPLEMENT    the frame comes first; the attribution is what it denies knowing
              "none of these shows whether the market's expectation lags or leads"
SUBJECT       the attribution comes first; the frame is predicated of it
              "the market's expectation cannot be established from this evidence"
WH-EMBEDDED   the attribution sits in an embedded question the frame is predicated of
              "what the market expects about margins, backlog and capex cannot be observed"
EXISTENCE     a negator governs the attribution's own referent phrase
              "No analyst-consensus or estimate-revision source is connected"
```

Everything else affirms. The carve-out that would have gutted the rule stays closed:

```
"Consensus does not expect growth."   ->  STILL REJECTED
```

because `expect` is deliberately absent from the epistemic-predicate list. A negation of the
expectation's CONTENT is still an assertion about consensus; only a negation of the pipeline's
KNOWLEDGE is safe. This is the distinction `consensus_language`'s own docstring warns a blanket
negation carve-out would lose, and it is the one line D4-S had to hold.

Two conditions on the vocabulary, so this does not become another list:

- `_NEGATOR` and `_EPISTEMIC_HEAD` name no consensus, market or expectation word. They are generic
  English negation and generic predicates of knowing. Neither grows when a model phrases an absence
  a new way.
- The authoritative status tokens (`SOURCE_NOT_AVAILABLE`, `NOT_FOUND_FOR_CANDIDATE`, `UNKNOWN`) are
  generated FROM the enums, not written out, so a renamed member cannot leave a stale spelling
  behind. They are needed because an underscore is a word character, so `\bnot\b` cannot see the
  negation inside `SOURCE_NOT_AVAILABLE`.

### What the guard can and cannot do

It runs on sentences the frozen trigger patterns already flag, so it cannot invent a rejection the
patterns do not carry: a sentence with no attribution at all can never be rejected by it, whatever
its polarity. Findings it drops are returned by `suppressed_absence_findings`, so the difference
D4-S makes is measurable without a live call.

### Known limitations, stated rather than hidden

- **EXISTENCE admits "No analysts expect growth"**, which is a statement about content. Not a D4-S
  regression: `_ABSENCE` already carries `no analysts?` and `no consensus` verbatim, so the frozen
  classifier routes that sentence to ABSENCE today and D4-S neither widens nor narrows it. Closing
  it means deciding whether "no X expects Y" is ever legitimate output - a contract question, not a
  detector tweak.
- **A negated subordinate clause joined to an affirmative main clause by a bare comma can leak.**
  The clause segmenter splits on comma-plus-conjunction and on `but`/`however`/`yet`, so
  `"It is unclear whether the market expects X, but consensus expects 22%"` rejects correctly, while
  a constructed `"None of this is known, the market expects 20%"` would not. Left as-is deliberately:
  a more aggressive segmenter risks new false positives on real output, which is the failure mode
  that cost two live runs.
- **D4-S does not fix the approved-vocabulary-collision class.** See §J.

## F. Contradiction Rules

`validate.check_expectation_state_contradictions`, reached through the original
`check_consensus_not_fabricated` name so every existing caller means the same thing.

```
1  consensus_status / estimate_revisions_status must equal the state's, verbatim
   -> "contradicts the code-owned expectation bundle"

2  market_expectation_claim_allowed = false + an affirmative attribution in any text
   -> "attributes an expectation to analysts or the market (...) while the expectation bundle
       reports consensus SOURCE_NOT_AVAILABLE"

3  the same, where the attribution carries a FIGURE
   -> "... by stating a figure ..."
```

Rule 3 is called out separately because a repair prompt that names the shape converges faster than
one that says "an attribution" (the D3.1 §I.2 finding). Its message is ADDITIVE - it keeps the
substring `"attributes an expectation to analysts or the market"` that D4.1's own tests match on, so
a more specific message costs no caller its assertion.

Allowed, and tested as allowed:

```
status = UNKNOWN  +  a limitation explaining the uncertainty     -> valid
status = UNKNOWN  +  no numeric expectation claim                -> valid
```

The rejection message names no approved wording, because there is no longer a list to name:

> Saying instead that the evidence does not settle the question is always allowed, in whatever
> wording you like - this check looks at whether a sentence AFFIRMS an expectation, never at which
> phrase it uses to deny one, so you do not need to guess an approved form of words

That sentence is the D4.1 non-convergence fix made structural. A model cannot be trapped guessing a
phrase when no phrase is being required.

## G. C1 / C4 Preservation

Unchanged, and asserted by regression test.

```
ExpectationGapState   WIDE_POSITIVE POSITIVE NEUTRAL NEGATIVE WIDE_NEGATIVE UNKNOWN   unchanged
C1  POSITIVE requires non-price expectation evidence                                  unchanged
C4  consensus-absence confidence ceiling (MEDIUM)                                     unchanged
C5  UNKNOWN gap <-> UNKNOWN confidence                                                unchanged
C6  a stated gap needs expectation evidence or a computed price reaction               unchanged
C7  priced-in needs evidence_ids + confidence + a limitation                          unchanged
Confidence enum / thresholds                                                          unchanged
priced_in semantics                                                                   unchanged
M8 numeric-ownership semantics AND claim scope                                        unchanged
consensus provider added                                                              NO
```

Measured on both stored runs, before and after D4-S:

| | D4.3A gaps | D4.3A confidences | V3 gaps | V3 confidences |
|---|---|---|---|---|
| before | UNKNOWN, UNKNOWN, NEUTRAL | UNKNOWN, UNKNOWN, LOW | UNKNOWN, UNKNOWN, NEUTRAL | UNKNOWN, UNKNOWN, LOW |
| after | UNKNOWN, UNKNOWN, NEUTRAL | UNKNOWN, UNKNOWN, LOW | UNKNOWN, UNKNOWN, NEUTRAL | UNKNOWN, UNKNOWN, LOW |

`M1-M12` read 12 PASS on both runs before and after.

## H. Unsourced 17-Claim Audit

### H.1 What they actually are

```
SCCO 7   GOOG 4   BSY 6   = 17          reported by Final Tier A V3's audit
SCCO 7   GOOG 4   BSY 6   = 17          compound-form claims in those same three outputs
0                                        unresolvable evidence ids
0                                        unresolvable derived source ids
```

The counts are identical because they are the same claims. `ClaimV2._exactly_one_citation_form`:

```python
if atomic and compound:                                    raise   # never both
if atomic and (source_id is None or evidence_id is None):  raise   # atomic needs both
if compound and len(evidence_ids) < 2:                     raise   # compound needs >= 2
```

so a compound claim MUST have `source_id is None`. The audit tested:

```python
if not cited or not claim.get("source_id"):
    unsourced.append(... "no citation")
    continue
```

Every correctly-cited compound claim therefore counted as uncited. This is provable from the two
contracts alone - `test_the_schema_forbids_what_the_audit_used_to_demand` proves it with no
reference to any run - which is why the correction is not a threshold tuned to a result.

D4.3A's stored 19 (`4 + 6 + 9`) is the same bug on the same three issuers.

### H.2 The classification (§12-§15)

Decided structurally by `classify_claim_sourcing`, from the claim's type and its cited ids, never
from its prose.

```
A  MATERIAL_SOURCE_REQUIRED      cites at least one document chunk        -> E2's denominator
B  CODE_OWNED_FACT_EXPLANATION   cites only code-owned fact ids           -> out of E2
C  META_LIMITATION_OR_UNKNOWN    claim_type UNKNOWN (citation-exempt)     -> out of E2
```

All 17, with the classification that follows from what each cites:

| # | candidate | path | type | citations | class | reason |
|---|---|---|---|---|---|---|
| 1 | SCCO | `management_signal_changes[0].claims[0]` | FACT | 3 SEC chunks | A | copper outlook 911,400 -> 915,400 -> 917,000 t across three filings |
| 2 | SCCO | `management_signal_changes[1].claims[0]` | FACT | 2 SEC chunks | A | zinc outlook 166,800 -> 163,900 t |
| 3 | SCCO | `management_signal_changes[2].claims[0]` | FACT | 2 SEC chunks | A | molybdenum outlook 27,400 -> 27,900 t |
| 4 | SCCO | `price_reaction_reading[3]` | INTERPRETATION | 2 CODE facts | B | reads `return_1d` against `return_3d` |
| 5 | SCCO | `gap_rationale[2]` | INTERPRETATION | 2 SEC chunks | A | which outlooks moved, and by how little |
| 6 | SCCO | `gap_rationale[3]` | INFERENCE | 2 SEC + 1 CODE | A | no gap in either direction; move is commodity-price |
| 7 | SCCO | `priced_in_assessment.claims[1]` | INTERPRETATION | 2 CODE facts | B | last close 189.88 vs 252-session low 106.88 |
| 8 | GOOG | `price_reaction_reading[5]` | INTERPRETATION | 1 CODE + 2 SEC | A | negative reaction to a positive headline |
| 9 | GOOG | `gap_rationale[1]` | INTERPRETATION | 2 CODE facts | B | revenue state vs free-cash-flow state |
| 10 | GOOG | `gap_rationale[2]` | INTERPRETATION | 2 SEC chunks | A | no guidance; what the non-price evidence consists of |
| 11 | GOOG | `gap_rationale[3]` | INFERENCE | 3 CODE facts | B | price evidence points both ways |
| 12 | BSY | `improvement_claims[3]` | INTERPRETATION | 1 CODE + 1 SEC | A | STABLE revenue, ~12% cc ARR growth, no acceleration |
| 13 | BSY | `management_signal_changes[0].claims[2]` | INTERPRETATION | 2 SEC chunks | A | Q2 adds cost drivers Q1 did not name |
| 14 | BSY | `price_reaction_reading[2]` | INTERPRETATION | 1 CODE + 1 SEC | A | mildly negative reaction to a positive framing |
| 15 | BSY | `pre_event_positioning_reading[6]` | INTERPRETATION | 2 CODE facts | B | drawdown and 6-month relative strength |
| 16 | BSY | `gap_rationale[2]` | INTERPRETATION | 2 CODE facts | B | reality side is mixed |
| 17 | BSY | `gap_rationale[3]` | INFERENCE | 2 CODE facts | B | large completed move rules out one reading |

```
A = 10   B = 7    C = 0
```

**C = 0, and that is the honest answer rather than a gap.** §15's concern - a limitation counted as
a material source claim - is structurally impossible and already was: `limitations` and
`unknown_fields` hold plain strings, not `ClaimV2` objects, so the claim walker never reaches them.
Verified by test. The C class exists for `claim_type=UNKNOWN` claims, which the schema exempts from
citation outright; none of the 17 is one, though the wider outputs carry 1-5 each.

Note that several A-type claims read as partly meta (#6, #10). They are still A: they cite document
chunks, and what a claim CITES is a structural fact while "how meta does it read" is not. Classifying
by prose is the mistake D4-S exists to stop making.

### H.3 E2's numerator

```
A-type claims whose citation does not resolve

D4.3A   0 of 19 reported     (19 reported, 19 were compound-form, 0 unresolvable)
V3      0 of 17 reported     (17 reported, 17 were compound-form, 0 unresolvable)
```

A genuinely uncited claim and a compound claim citing an unresolvable id are both still counted -
tested, because fixing the citation FORM must not become a blanket pass for its CONTENT.

### H.4 The second blind spot, found and NOT closed

The same `continue` that mislabelled these 17 also routed every compound claim past M8's
numeric-ownership check and past E4's future-source check. So:

> M8 PASS on D4.3A and on Final Tier A V3 both mean "no ATOMIC claim restates a code-owned number".
> Neither has ever meant more.

With the scope widened, M8 would read FAIL on both:

```
D4.3A   5 findings   (SCCO 4, GOOG 1)
V3      1 finding    (BSY 1)
```

Example, BSY V3 `gap_rationale[2]`: cites `research_facts.fundamental_changes.revenue.state`
(`STABLE`) and describes it as "durable, steady top-line growth" without stating the token.

These are not closed here. Widening a frozen gate's scope after its results exist is what §13 (no
new post-hoc Tier A gate) and §16 (no definition tuned to a result) forbid, and doing it would
retroactively overturn two runs' M8. They are reported in a new audit key,
`compound_claim_coverage_gap`, wired to no gate, for a separate decision. Whether these six are real
defects or `fact_is_restated` false positives on qualitative text is itself part of that decision:
several look like the latter, and deciding it properly needs the numeric-role classifier examined
against compound claims, which is its own step.

**That step has since run**, and its result is `H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md`: 0 true
numeric defects, 4 matcher false positives, 2 not-numeric-restatements, plus a third latent
mechanism that blocks widening M8's scope. It changed nothing here, and this document's verdict and
declarations stand as written.

## I. Tier B E2 Meaning

Threshold unchanged at the frozen zero. Meaning frozen in `d4_1_contract.py` beside the constant:

> All MATERIAL_SOURCE_REQUIRED expectation-gap claims must resolve to a valid source/evidence id or
> to a code-owned fact id. CODE_OWNED_FACT_EXPLANATION and META_LIMITATION_OR_UNKNOWN claims are not
> in the denominator.

Both exclusions follow from contracts that predate D4-S, not from seeing a result:

- **B is out** because M8/E5 already holds a code-owned-fact claim to a STRICTER test - it must state
  that fact's value correctly, not merely cite something. Counting it under E2 as well would
  double-count one obligation. (§H.4 notes that this stricter test does not currently reach compound
  claims, which is exactly why that gap is reported rather than used as a reason to move E2.)
- **C is out** because `ClaimV2` exempts `claim_type=UNKNOWN` from citation, and free prose in
  `limitations`/`unknown_fields` is not a claim at all.

No threshold was changed to fit the 17. The 17 stopped being defects because the test that produced
them was wrong.

## J. Offline Replay

`backend/app/dev/replay_d4_s_expectation_state.py`, over stored bytes from both Tier A runs.

```
live calls   0
cost         $0.00
```

No stored output was rewritten as though a new prompt had produced it. The measurement is over the
INITIAL responses, because that is where V3's two defects were.

### Final Tier A V3 - both false positives released

```
SCCO  initial   old_rejections=1  new_rejections=0
  RELEASED  root.gap_rationale[3]  [market expectation noun phrase]
    "Taken together, the evidence gives no basis for saying market expectations lag the evidenced
     progress, and none for saying they run ahead of it;"

GOOG  initial   old_rejections=1  new_rejections=0
  RELEASED  root.gap_rationale[2]  [market expectation noun phrase]
    "The non-price expectation evidence consists of capex expectations, backlog disclosures and a
     conversion-timing statement that did not change, and none of these shows whether the market's
     expectation lags or leads the evidenced progress."

BSY   initial   old_rejections=0  new_rejections=0
all finals      old_rejections=0  new_rejections=0
```

These are exactly the two sentences V3 §K named. Under D4-S neither is a rejection, so neither would
have consumed a repair round.

### D4.3A - one rejection survives, and D4-S does not claim to fix it

```
SCCO  initial   old_rejections=1  new_rejections=1
  STILL REJECTED  root.supporting_claims[1].text  [analyst ... estimates]
    "The 10-K's mineral reserve estimates used $3.30 per pound of copper and $10.00 per pound of
     molybdenum, prices derived from analyst and bank forecasts as of December 31, 2025."
```

Reported as a limitation, not as a success. This sentence affirms nothing about what the market
expects about SCCO; it reports how a filing derived a reserve-pricing assumption. But it is
affirmative in form and carries no negation anywhere, so the polarity rule keeps it - correctly, by
its own definition. This is a different defect class:

```
absence-language false positive         D4-S fixes it        (V3's 2 defects)
approved-vocabulary collision           D4-S does NOT fix it (D4.3A's 1 defect)
```

The second needs the detector to know that "analyst forecasts" as a commodity-price input is not an
issuer expectation. That is a scoping question about `_REFERENT`, not a polarity question, and it is
out of this step's scope. **So the honest read is that D4-S addresses two of the three
initial-round defect instances across the two runs, not all of them.**

### Determinism of the classification

Every claim in all six final outputs classified into exactly one of A/B/C, with the count matching
the claim total and A+B matching the resolvable-citation total. Per-candidate, V3:

```
SCCO   forms {ATOMIC 49, COMPOUND 7}   classes {A 34, B 22, C 1}
GOOG   forms {ATOMIC 41, COMPOUND 4}   classes {A 24, B 21, C 3}
BSY    forms {ATOMIC 35, COMPOUND 6}   classes {A 19, B 22, C 4}
```

### Answering the three questions the brief asked

```
Would structured validation reject honest absence language?
  NO. Both V3 false positives released; every absence wording tested, including
  wordings no list in this repository carries, passes.

Would unsupported affirmative expectation assertions still be caught?
  YES, and two R2 holes are now closed as well: "Investors expect X" (was NEUTRAL) and
  "Without consensus data, it is clear the market expects 20%" (was ABSENCE).

Would the 17 audit-only claims classify deterministically?
  YES. A=10, B=7, C=0, from structure alone, and none of them was a defect.
```

### Audit deltas, frozen HEAD versus D4-S

| category | D4.3A before | D4.3A after | V3 before | V3 after |
|---|---|---|---|---|
| `unsourced_material_claims` | 19 | **0** | 17 | **0** |
| `fabricated_consensus` | 0 | 0 | 0 | 0 |
| `future_source_leaks` | 0 | 0 | 0 | 0 |
| `code_owned_numeric_defects` | 0 | 0 | 0 | 0 |
| `decision_field_leaks` | 0 | 0 | 0 | 0 |
| `decision_vocabulary_leaks` | 0 | 0 | 0 | 0 |
| `unknown_discipline_violations` | 0 | 0 | 0 | 0 |
| `unsupported_priced_in` | 0 | 0 | 0 | 0 |
| `compound_claim_coverage_gap` *(new, no gate)* | - | 5 | - | 1 |
| M1-M12 | 12 PASS | 12 PASS | 12 PASS | 12 PASS |

Exactly one frozen category moved, and it moved to zero because its test was wrong.

## K. Tests

```
before D4-S   749 passed, 1 skipped
after  D4-S   808 passed, 1 skipped      (+59, 0 modified assertions weakened)
```

```
.venv/bin/python -m pytest backend/tests/strategy_h_v2 -q
808 passed, 1 skipped
```

The one skip is `test_probe_under_a_forced_non_project_interpreter_is_marked_unofficial`, expected
since D4.3R §L and unchanged.

| area | file | result |
|---|---|---|
| structured state, polarity guard, contradictions | `test_d4_s_expectation_state.py` (new) | 43 passed |
| citation form, A/B/C, M8 scope, regression | `test_d4_s_claim_sourcing.py` (new) | 16 passed |
| frozen V1 phrase-by-phrase behaviour | `test_consensus_language.py` | 41 passed |
| frozen R2 market-referent behaviour | `test_consensus_language_r2.py` | 29 passed |
| M1-M12 aggregation, telemetry | `test_d4_3r_audit_alignment.py` | 17 passed, 1 skipped |
| validator | `test_d4_validate.py` | 33 passed |
| contract rules C1-C7 | `test_contract_rules.py` | 20 passed |
| M8 numeric roles | `test_numeric_roles.py` | 12 passed |
| gap enum / contract | `test_gap_contract.py`, `test_d4_2_contract.py` | 11 + 13 passed |

Three frozen tests were touched, all to preserve their intent under a changed mechanism, none to
accommodate a result:

- `test_consensus_language.py` and `test_d4_2_contract.py` needed **no** edit and still pass
  verbatim, including `test_the_impossible_consensus_failures_are_gone_and_the_direction_ones_are_not`
  (`still_failing_consensus_under_v2 == 0`), which caught two real regressions during development
  and is why the availability-restatement positions exist at all.
- `d4_helpers.expectation_bundle` gained an `estimate_revisions` keyword so a PARTIAL/ESTABLISHED
  state can be built. Default behaviour unchanged.
- `test_consensus_language_r2.py`'s identity test is superseded by
  `test_runtime_and_both_audits_share_one_guard_function`, which asserts the same
  single-source-of-truth property one layer up, by object identity, for the runtime validator and
  both audit modules. The original assertion still passes and was not removed.

No live result was edited to make a test pass, and no test was weakened to accommodate a live result.

## L. Live Smoke Proposal

**Not executed. No live call was made in this step.** This is a proposal for a separate,
separately-approved step.

```
purpose      serialization / schema / validator / structured-prose alignment on a live response
NOT          investment quality, generalization, or anything about an expectation gap's meaning
```

Frozen before any result is seen, per §21:

```
sample            SCCO, then GOOG if a second is wanted
                  (reused from the existing Tier A sample - a mechanical smoke needs no
                   unseen issuer, and a new issuer would spend money to learn nothing)
candidates        1, extendable to 2 on the same approval
per_call cap      $2.00      (frozen CandidateBudgetContract, unchanged)
worst case        $6.00 / candidate  ->  $6.00 for 1, $12.00 for 2
model             claude-opus-5-5
prompt / schema   h_v2_d4_expectation_gap_v2 / h_expectation_gap_analysis_v2   (UNCHANGED)
```

Pass conditions, all mechanical:

```
1  the response parses and validates under the V2 schema
2  ExpectationKnowledgeStateV1 serializes into the record and round-trips
3  the structured statuses match the derived state
4  no affirmative expectation leakage in the final output
5  any honest absence language in the INITIAL response is not rejected
6  expectation_gap, confidence, priced_in and applied_contract_rules are unchanged in semantics
7  M1-M12 computable and PASS on the single record
```

What it cannot show: whether initial validity improves in general. One or two candidates is not a
rate, and §11 forbids steering the sample toward a POSITIVE output to exercise C1.

**This smoke has since run**, as one SCCO candidate, and its result is
`H_V2_D4_S1_SCCO_LIMITED_LIVE_SMOKE_RESULT_V1.md`: STRONG PASS, initial valid YES, 0 repairs,
$1.3940560 of a $6.00 cap, M1-M12 all PASS. The decisive measurement is a $0 counterfactual on the
stored bytes: that same live response is INVALID under the pre-D4-S contract (one consensus-language
false positive) and VALID under D4-S. It changed nothing in this document, whose verdict and
declarations stand as written, and it did not authorize Tier B.

## M. Tier B Status

```
Tier B            NOT AUTHORIZED          (unchanged)
Tier B calls      0
D5                NOT READY
```

The §S ground stands: Final Tier A V3's Initial Validity read UNSTABLE, and D4.3R2 §H's frozen rule
requires a separate user review before any Tier B authorization regardless of the mechanical verdict.
D4-S does not discharge that review. It removes one cause of the instability (2 of the 3 observed
defect instances) but the diagnostic was computed on a run that has already happened, and a frozen
diagnostic is not recomputed against a changed enforcement layer to produce a better number. That
would be exactly the result-fitting §16 forbids.

Budget, unchanged and still not fitting:

```
observed projection (6 companies)   $16.5903
remaining prior authorization       $16.6268626
theoretical worst case              $45.60
margin                              $0.037
```

`d4_2_contract.TIER_B_FITS_REMAINING_CAP` remains asserted False in code. No cap is set, raised or
approved here.

## N. Verdict

```
H-V2-D4-S
= READY FOR LIMITED LIVE SMOKE
```

This is not a D4 quality PASS, not a Tier A re-verdict, not an authorization for Tier B, and not
evidence that any expectation gap means anything.

What it does say. The availability of expectation knowledge is now decided in one place, by code,
from the code-owned bundle, and is carried as one object that the runtime validator, both audit
modules and the manual audit helper all read. The free-text layer no longer decides availability; it
tests polarity by claim structure, and the phrase list that set two live runs' initial validity is
no longer load-bearing for whether an honest absence statement is accepted. Offline, against bytes
that already existed, both of Final Tier A V3's false-positive rejections disappear, every
affirmative shape the brief named is still caught, two pre-existing R2 holes close, and every frozen
gate and contract rule reads exactly what it read before.

What it does not say. It does not fix the approved-vocabulary-collision class that caused D4.3A's
rejection (§J), it does not close the M8 compound-claim blind spot it uncovered (§H.4), and it has
not been exercised against a live response - which is the whole content of §L.

### Final declarations

```
live Opus calls?                          NO
live cost?                                $0.00
Final Tier A verdict changed?             NO
D4.3A verdict changed?                    NO
C1 changed?                               NO   (still NOT_EVALUATED, 0 eligible, both runs)
C4 changed?                               NO
C5 / C6 / C7 changed?                     NO
Gap semantics changed?                    NO
Gap enum changed?                         NO
confidence semantics changed?             NO
priced-in semantics changed?              NO
M8 semantics or scope changed?            NO   (blind spot reported, not closed)
consensus provider added?                 NO
phrase-by-phrase whitelist expanded?      NO   (mechanism replaced; see §E)
classify_sentence behaviour changed?      NO   (70 frozen tests pass unedited)
Tier B executed?                          NO
Tier B budget approved?                   NO
D5?                                       NO
valuation / fair value / target price?    NO
APPROVE / WATCH / REJECT?                 NO
forward returns?                          NO
POSITIVE output forced to exercise C1?    NO
existing unrelated dirty files modified?  NO
push?                                     NO
tests                                     808 passed, 1 skipped
```

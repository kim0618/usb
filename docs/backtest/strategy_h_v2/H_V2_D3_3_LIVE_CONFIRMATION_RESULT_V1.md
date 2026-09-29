# Strategy H-V2 - D3.3 New Issuer Live Confirmation, Result V1

- declared: 2026-09-29
- status: **LIVE CONFIRMATION RESULT - NOT AN INVESTMENT DECISION, NOT A VALUATION, NO FORWARD
  RETURNS**
- stage: **H-V2-D3.3**
- preregistration (frozen before this run, **unmodified by it**):
  `H_V2_D3_3_LIVE_CONFIRMATION_PREREGISTRATION_V1.md`
- model requested: **Claude Opus 5.5** (`claude-opus-5-5`); canonical model verified on every call
- coding/orchestration model for this stage: Claude Opus 5
- **live cost: $16.7123 of the frozen $30.00 ceiling**

The question this stage answers, and the only one: does Prompt V2 + Schema V2 + Validation Contract
V2 + Telemetry V2 produce stable, source-grounded research on 12 issuers that no prior live run has
ever touched, without leaning on the repair loop. It does not ask whether the research finds good
stocks, and nothing here computes an Expectation Gap, a decision, a valuation, or a return.

## A. Repository State

- branch `main`, HEAD `5958a4e` (`feat(strategy-h-v2): wire live research telemetry`) - the D3.2F
  commit named in the brief, confirmed present before execution.
- origin/main 24 behind locally; nothing pushed at any point.
- 195 dirty worktree entries at start, 0 staged. No pre-existing dirty file was read for
  modification, modified, deleted or staged. `frontend/next-env.d.ts` remains modified by something
  outside this session (a Next.js-generated reference path, `.next-build` -> `.next-dev`), left
  untouched as the brief instructs.
- Pre-execution H-V2 regression: **388 passed**.

## B. Frozen Contract, Verified Before Execution

| item | frozen value | verified |
|---|---|---|
| sample checksum | `b671d988...671ddf9c` | recomputed identical |
| sample re-derivation | `regenerate_sample_from_universe()` | reproduces the frozen 12 exactly |
| CIK disjointness | 36 prior live CIKs (D3 pilot 12 + D3.1 Batch 2 24) | sample disjoint from all 36 |
| manual audit checksum | `700154df...ec41284e` | recomputed identical |
| hard budget | $30.00, worst case $6.00/candidate | unchanged, enforced before each call |
| prompt version | `h_v2_d3_research_v2` | every attempt |
| schema version | `h_research_interpretation_v2` | every attempt |
| validation contract | `h_v2_d3_2_validation_contract_v2` | every attempt |

All 12 input packages were present in the D2.1 snapshot `D2_1-20260928T072430Z`, with CIK, depth and
priority matching the frozen contract on every one. **Nothing in the preregistration, the contract
module, the prompt, the schema, the ontology, the stage floor or the Validation V2 thresholds was
changed by this stage.**

## C. Sample Integrity

The frozen 12, in frozen execution order, all reached the model exactly once:

| # | ticker | CIK | depth / priority | reached in |
|---|---|---|---|---|
| 1 | BSY | 0001031308 | FULL / P1_HIGH | segment 1 |
| 2 | WEN | 0000030697 | FULL / P1_HIGH | segment 1 |
| 3 | GOOG | 0001652044 | FULL / P1_HIGH | segment 1 |
| 4 | SCCO | 0001001838 | FULL / P1_HIGH | segment 1 |
| 5 | DT | 0001773383 | FULL / P1_HIGH | segment 1 |
| 6 | LUV | 0000092380 | FULL / P1_HIGH | segment 1 |
| 7 | MOFG | 0001412665 | CORE / P2_MEDIUM | segment 2 |
| 8 | GD | 0000040533 | CORE / P2_MEDIUM | segment 2 |
| 9 | CWBC | 0001127371 | CORE / P2_MEDIUM | segment 2 |
| 10 | SIF | 0000090168 | CORE / P2_MEDIUM | segment 2 |
| 11 | BPOP | 0000763901 | CORE / P2_MEDIUM | segment 2 |
| 12 | BLKB | 0001280058 | CORE / P2_MEDIUM | segment 2 |

**Why two segments.** Segment 1 (`D3_3-20260929T021659Z`) ran the frozen order and completed the 6
FULL candidates; all 6 CORE candidates then failed in 2-4 seconds each at **$0.00 cost** with an
identical account-level message: `You've hit your session limit · resets 12:30pm (Asia/Seoul)`. The
model was never invoked on any of them, so there was no model output, no parse, no validation and no
research outcome of any kind - the same infrastructure failure class that truncated D3.1's Batch 2
(D3.1 §G).

After the stated reset, segment 2 (`D3_3-20260929T033427Z`) ran exactly those 6 unreached tickers,
in frozen order, with the **remaining** budget ($30.00 - $8.3094 = $21.6906) so the two segments
together could not exceed the frozen ceiling. No ticker was substituted, added, dropped or
re-researched after a successful run; each segment has its own `research_run_id` and every attempt
its own `attempt_id`, so segment 1's six $0 non-reach records are still on disk, unmodified, beside
segment 2's successful ones. Ordering within each segment is a contiguous slice of the frozen order,
and no candidate's outcome influenced which candidate ran next.

## D. Model Verification

Every one of the 12 reached attempts reports `canonical_model = claude-opus-5-5`, identical to
`model_requested`. **`model_mismatch = false` on all 12; zero attempts ran on any other model and no
fallback occurred.** The 6 segment-1 non-reaches report `canonical_model = null` (no model usage was
reported because no model was invoked), which the contract treats as a missing fact rather than a
mismatch.

## E. Budget / Cost

| | total | candidates | average |
|---|---|---|---|
| segment 1 (FULL x6) | $8.3094 | 6 | $1.3849 |
| segment 2 (CORE x6) | $8.4030 | 6 | $1.4005 |
| **D3.3 total** | **$16.7123** | **12** | **$1.3927** |

- Initial-call cost: **$16.7123 (100%)**. Repair cost: **$0.00** - there were no repairs at all.
- $0.00 was spent on the 6 non-reaches.
- Ceiling: $16.7123 of $30.00 used (55.7%); the budget guard never triggered
  (`stopped_early: null` in both segments).
- FULL and CORE cost essentially the same per candidate here ($1.38 vs $1.40), unlike D3.1's
  apparent FULL/CORE spread - consistent with the system prompt being served from cache after the
  first call (see §S on what the token fields do and do not mean).
- Naive 2,010-candidate extrapolation at $1.3927: **~$2,800**. The full run remains **prohibited**
  and is not authorized by this result.

## F. Execution Results

| ticker | final status | initial validation | repairs | cost | raw response chars |
|---|---|---|---|---|---|
| BSY | OK | OK | 0 | $1.5217 | 42,421 |
| WEN | OK | OK | 0 | $1.2450 | 33,904 |
| GOOG | OK | OK | 0 | $1.4860 | 58,742 |
| SCCO | OK | OK | 0 | $1.4010 | 48,006 |
| DT | OK | OK | 0 | $1.3060 | 50,022 |
| LUV | OK | OK | 0 | $1.3510 | 43,805 |
| MOFG | OK | OK | 0 | $1.4290 | 30,820 |
| GD | OK | OK | 0 | $1.4540 | 58,634 |
| CWBC | OK | OK | 0 | $1.4240 | 47,358 |
| SIF | OK | OK | 0 | $1.1390 | 32,380 |
| BPOP | OK | OK | 0 | $1.5400 | 46,200 |
| BLKB | OK | OK | 0 | $1.4170 | 57,393 |

Every raw response is stored in full (30,820 to 58,742 characters), checksum-verified against its
own stored text, never a preview. 1,030 material claims and 44 future-business items in total.
`research_completeness = PARTIAL` on all 12, which is the honest value for an evidence set bounded by
the D2.1 collection policy.

## G. Initial Validity (L2)

**12 of 12 candidates passed Prompt V2 + Schema V2 + Validation V2 on their FIRST response.
`initial_validation_status = OK` on all 12, `initial_failure_codes` empty on all 12.**

This is the gate D3.2R's prompt work was aimed at. For context, and explicitly **not** as a
same-contract comparison (D3.1 ran a different prompt, a different schema and a different validation
contract - D3.2 §A/§L's non-comparability rule applies): D3.1's Batch 2 needed at least one repair
round on 11 of 19 reached candidates, 10 of those 11 driven by a future-business stage asserted above
its own evidence flags. Here that failure mode did not occur once.

## H. Repair Analysis

```text
initial failures                 0
repair rounds                    0
repair rate                      0 / 12 = 0.0%
failure code distribution        (empty - nothing to classify)
cost attributable to repairs     $0.00
```

There is nothing to analyse, which is itself the finding: the bounded repair loop was never entered.
`missing_evidence` - the Schema V2 field that exists so the model can state what a higher stage would
have needed - was populated on **44 of 44** future-business items, i.e. the conservative-stage
reasoning Prompt V2 asks for was performed and recorded every time rather than inferred after a
rejection.

## I. Content Accuracy (L3)

Manual audit of the 6 frozen companies (§P). Every load-bearing claim read was checked against the
text of the chunk it cites.

**Material factual defects: 0.** Representative verifications, all exact against the cited chunk:

| company | claim | source text |
|---|---|---|
| LUV | "Operating income excluding special items was $585 million in Q2 2026, versus $245 million" | reconciliation table: `Operating income, excluding special items \| $ 585 \| $ 245 \| 138.8` |
| LUV | "Passenger ancillary sold separately was $875 million ... versus $309 million" | `Passenger ancillary sold separately (b) \| 875 \| 309` |
| LUV | "Approximately $1.8 billion of those non-expiring flight credits remained available as of June 30, 2026" | `Approximately $1.8 billion of such flight credits remain available as of June 30, 2026` |
| LUV | "Net cash provided by operating activities was $530 million in the three months ended June 30, 2026" | verbatim in the 10-Q liquidity section |
| DT | "Annualized logs consumption nearly doubled in the last two quarters to $200 million and grew well over 100% year-over-year" | `Nearly doubled annualized logs consumption in the last two quarters to $200 million, growing well over 100% year-over-year` |
| WEN | "Wendy's International total revenues were $153.0 million in 2025, compared with $144.7 million in 2024" | segment table: `Total revenues \| $ 153.0 \| $ 8.3 \| $ 144.7` |
| CWBC | "Total deposits acquired as a result of the merger were $1.1 billion as of April 1, 2026" | `was $1.1 billion as of April 1, 2026` |
| MOFG | "The acquisition brought in $224,248 thousand of assumed deposits" | acquisition table: `Deposits \| $ (224,248)` |
| BLKB | "machine learning features ... adopted by more than half of Raiser's Edge NXT customers" | verbatim |
| BLKB | "Six-month 2026 cash flows included $8,675 thousand of other investing activities; whether this relates to the Student First investment is not stated" | `Other investing activities \| (8,675)`, and the source indeed does not link the two |

Qualification preservation was checked as part of this and held throughout: "Approximately",
"nearly doubled", "well over 100%", "expected to begin", "may", "relatively flat" all survive from
source into claim. The BLKB example above is the sharper case - the model found a cash-flow line that
*could* have been presented as the investment's size and explicitly refused to link them.

## J. Future Business Audit (L4)

**Unsupported stage escalations: 0 of 44 items.** Every stage is independently re-derivable from that
item's own five evidence flags against the frozen floor (re-computed in
`audit_strategy_h_v2_d3_3.py` from a restated floor table, not by importing the validator that
enforced it).

Distribution (a diagnostic, not a gate):

```text
STORY             14
EARLY_EVIDENCE    15
COMMERCIALIZING    2
REAL_BUSINESS      9
MATURE             1
UNKNOWN            3
```

Manual reading of the staging, which is what actually tests Prompt V2's core change, found the calls
not merely floor-compliant but economically correct, and conservative in exactly the places the
ontology asks for:

- **DT, Log Management**: annualized consumption of $200 million growing over 100% year-over-year,
  and the model still assigned **EARLY_EVIDENCE**, stating in its own claim why: "Only one of the five
  evidence flags is supported, however, so a conservative stage applies even though the underlying
  business may be larger than the stage implies."
- **BLKB, Agents for Good**: a launched, shipped AI product held at **STORY**, with the reasoning
  recorded: "The product has launched, but no revenue, customer count, named customer, or bookings
  contribution is disclosed." It also cites the company's own admission that the technology is "in
  the early stages of commercial use".
- **DT, Dynatrace Intelligence**: generally available, assigned **UNKNOWN** because no revenue,
  customer or usage metric is disclosed - GA status alone did not buy a commercial stage.
- **LUV, Starlink**: first equipped aircraft in service, kept at **EARLY_EVIDENCE**, with an
  INTERPRETATION noting free in-flight WiFi is currently a cost item rather than a revenue source.
- **LUV, ancillary products** and **CWBC, acquired USB franchise**: **REAL_BUSINESS** with revenue
  evidence present ($875M ancillary; $1.1B deposits acquired), and `missing_evidence` naming what
  MATURE would still require.

## K. Citation Precision (L5)

Machine measurement (Validation Contract V2, unchanged):

```text
material claims                                    1,030
structural provenance defects (R4A)                    0
citation-precision defective claims (R4B)             12  = 1.17%
numeric tokens                                     1,126
  supported in the claim's own cited chunk         1,108
  same-source adjacent chunk only                      8
  not found                                           10
frozen threshold                                   <= 10%
```

**L5 = 1.17% machine-raw, PASS.** Zero structural provenance defects: no claim cited a chunk outside
its own candidate's package, no cross-company citation, no invented chunk index.

All 12 flagged claims were then adjudicated by hand against their cited chunks, as the brief's L5
definition requires (Validation V2 **plus** manual audit). **8 of the 12 are validator artifacts, not
citation defects** - the claim is correct and cited correctly, and the frozen validator mis-parses
the notation:

| artifact class | examples | why the validator flags it |
|---|---|---|
| fiscal-period notation | SCCO `2Q25`, `2Q24`, `3Q16`, `1Q27`, `2H29` | `LABEL_RE` covers `Q1`/`H1`/`FY2026` but not digit-first `2Q25`, so `25`/`16`/`27`/`29` become freestanding numbers to verify |
| ISO dates | BLKB `2025-09-30`, `2025-12-31` | `DATE_RE` matches month-name dates only, so `09`/`30`/`12`/`31` become numbers |
| percent stated bare under a `%` column header | LUV `(3.3)`, DT `56`, MOFG `26.1` | all three ARE in the cited chunk; the `%` sits in the header row, and V2's PERCENT-to-bare-COUNT fallback is deliberately restricted to >= 2 decimals (D3.2's own anti-collision rule) |
| hyphenated unit range | BPOP `65-80 bps ... from 55-70 bps` | the cited chunk says `55 bps-70 bps` and `65 bps - 80 bps` verbatim; the range's lower bound has no unit directly attached, and COUNT does not fall back to BASIS_POINTS |

The SCCO El Pilar case is the clearest: the claim says construction begins `1Q27` and production
`2H29`, and the cited chunk states "Project construction will commence in the first quarter of 2027,
and production is expected to begin in the second half of 2029" - correct, correctly cited, hedge
preserved, and flagged only because the model abbreviated the period notation.

**4 of the 12 are genuine citation-precision defects**, all of the same shape (right company, right
document, wrong chunk; the fact is real and present elsewhere in the same filing):

| # | company | claim | cited | fact actually in |
|---|---|---|---|---|
| 1 | SCCO | "$3 billion share repurchase program ... no repurchases since 3Q16" | 10-K `CHUNK:3` (front-matter units/organizational structure) | 10-K `CHUNK:175` (`currently authorized to $3 billion`) |
| 2 | SCCO | "Since 2Q24 the Board has approved quarterly stock dividends" | 10-K `CHUNK:3` (same front-matter chunk) | elsewhere in the same 10-K |
| 3 | GD | "Combat Systems operating margin of approximately 14.1%" | `CHUNK:33`, which contains neither the figure nor the segment name | adjacent chunk of the same source |
| 4 | CWBC | "Total non-interest income was $(1,970) thousand" | EX-99.1 `CHUNK:3` (holds the $5,899 and $3,929 components) | adjacent chunk of the same source |

Adjudicated L5 = **4 / 1,030 = 0.39%**. The verdict in §Q uses the stricter machine-raw 1.17% so it
does not depend on this adjudication.

Per the frozen contract (§1: no validator change to obtain a better number), `validation_v2.py` was
**not** modified to fix the four artifact classes. They are recorded here as measurement defects to
address in a declared future stage, not patched mid-run.

## L. Numeric Fidelity (L6)

**Fabricated or materially mutated material numeric facts: 0.**

Every numeric token that the V2 parser could not resolve in its claim's own cited chunk (10 not
found, 8 adjacent-only) was traced individually. Each one is either a notation artifact from §K
(period label, ISO-date fragment, bare percent under a `%` header, hyphenated range bound) or a
correct figure cited one chunk away within the same filing. No claim states a number the evidence
does not contain, and no number was altered in magnitude, unit or sign. Where a table presented a
liability as negative (MOFG `Deposits (224,248)`), the claim's positive "assumed deposits" reading is
a correct reading of a liabilities-assumed table, not a sign mutation.

Coverage is complete in the sense that matters: support was checked **within each claim's own cited
evidence scope** (the single `evidence_id`, or every chunk a compound claim declared in
`evidence_ids`), never against the candidate's whole evidence set. Units covered: currency, percent,
basis points, multiples, share counts, and values below 10 (no magnitude floor).

## M. Investment Decision Leakage (L7)

**0 violations** across every text field of all 12 outputs, under Validation V2's semantics
(`classify_investment_language`): no APPROVE/WATCH/REJECT as an investment recommendation, no
buy/sell, no price target, no investment fair-value conclusion, no entry or exit language. Ordinary
GAAP and corporate-action vocabulary appears and is correctly permitted (fair-value measurement,
board-approved repurchases) - which is the D3.1 §I.2 false positive that D3.2 and D3.2R closed,
confirmed here as closed on live output rather than only in tests.

## N. UNKNOWN / Conflict Discipline

**UNKNOWN discipline** - the model abstained rather than filling gaps:

```text
UNKNOWN claims                                     43
declared unknown_fields                           100
catalyst candidates                                47
  with expected_time set                            6
  with expected_time null                          41
  timing_confidence LOW / UNKNOWN                  38 of 47
competitive_position status: PARTIAL 33, UNKNOWN 7, SUPPORTED 7, UNSUPPORTED 2
```

The §21 risk areas specifically: catalyst dates were left null in 41 of 47 cases rather than
invented; competitive position was SUPPORTED in only 7 of 49 dimensions; declared `unknown_fields`
include exactly the things a model is tempted to guess (`market_share`,
`infrastructure_ai_revenue`, `asset_analytics_revenue`, `earnings_date`, forward guidance). Customer
identity, future revenue and commercialization timing were either cited or explicitly marked
unknown - MOFG's "Revenue and earnings specific to DNVB since the acquisition date are not readily
determinable" is the source's own language, preserved rather than replaced with an estimate.

**Conflicts**: 8 recorded across 5 companies (CWBC 2, LUV 2, MOFG 2, SCCO 1, SIF 1), every one with
exactly 2 evidence chunks, and **0 with an invalid `evidence_id`**. 6 UNRESOLVED, 2 RESOLVED. The two
LUV entries show the contract working in both directions:

- **RESOLVED**: the 10-Q describes a $1.5 billion revolver expiring August 2028; a later 8-K states
  that facility was terminated on August 10, 2026 and replaced with a $2 billion facility maturing
  2031. A later source superseding an earlier one *and saying so* is exactly the RESOLVED condition.
- **UNRESOLVED**: two filings state the same non-GAAP EPS change as 118.6% and 119.0%. The model
  recorded both sides, called it "minor, probably a rounding-basis difference", and left it
  UNRESOLVED with LOW confidence rather than silently picking one or dropping the topic.

Conflict count is not a gate; failing to record a real conflict would be the problem, and none of the
conflicts found on manual reading was omitted.

## O. Compound Claims

```text
material claims                                  1,030
compound claims declared via evidence_ids           13
suspected compound by the D3.2 heuristic           122
```

The Schema V2 compound-claim fallback was used 13 times and every declared `evidence_ids` list
resolved inside its own candidate's package (0 structural defects, §K). The 122 "suspected" figure
carries the caveat D3.2 §J.6 established and this stage does not relitigate: that detector
over-flags noun-phrase conjunctions ("Asia and the Middle East"), so it is a screening upper bound,
not a defect count, and per the brief it does not auto-fail anything. Manual reading of the audited
claims found no case where a single citation was doing the work of two unrelated propositions.

## P. Manual Audit

The 6 frozen companies, unchanged after seeing results
(`manual_audit_checksum 700154df...ec41284e`):

| depth | companies |
|---|---|
| FULL | LUV, DT, WEN |
| CORE | BLKB, CWBC, MOFG |

Method: `manual_audit_strategy_h_v2_d3_3.py` placed each audited claim beside the full text of the
chunk it cites; selection within a company was deterministic and declared (all future-business items
and all conflicts, plus numeric and INFERENCE claims by
`sha256("H_V2_D3_3_MANUAL_AUDIT_V1:<ticker>|<path>")` ascending). Load-bearing figures were then
re-verified directly against the D2.1 package text.

```text
claims audited across the 6 companies                      114
  deterministic selection (numeric + INFERENCE)              58
  every future-business item's claims                        50
  every recorded conflict                                     6
of a combined 480 material claims in those 6 outputs
content defects                                              0
citation defects                                             1  (CWBC $(1,970), also machine-flagged)
numeric defects                                              0
qualification-loss defects                                   0
future-business stage defects                                0
conflict defects                                             0
```

The two SCCO and one GD citation defects in §K fall outside this frozen 6-company sample; they were
found by the machine audit over all 12 and adjudicated anyway rather than left unexamined.

## Q. L1-L7 Gates

Computed by the frozen `d3_3_contract.evaluate_d3_3_gates`, using the **stricter machine-raw** L5
number:

| gate | name | threshold | observed | status |
|---|---|---|---|---|
| L1 | final validity | >= 95% | 12/12 = 100.0% | **PASS** |
| L2 | initial validity (pre-repair) | >= 80% | 12/12 = 100.0% | **PASS** |
| L3 | material content accuracy | == 0 | 0 | **PASS** |
| L4 | unsupported Future Business stage escalation | == 0 | 0 of 44 items | **PASS** |
| L5 | citation precision defect rate | <= 10% | 12/1030 = 1.2% | **PASS** |
| L6 | fabricated/mutated material numeric facts | == 0 | 0 | **PASS** |
| L7 | investment decision leakage | == 0 | 0 | **PASS** |

**Denominator, stated explicitly.** L1 and L2 use 12, the frozen sample, counting each candidate once
by the attempt that reached the model. The 6 segment-1 non-reaches are excluded from the denominator
because no model was invoked, no output existed, and nothing was validated - they cost $0.00 and are
infrastructure events, not research outcomes. The attempt-level view is reported for completeness: 18
attempt records exist, 12 of which reached the model and all 12 of those are valid (12/18 = 66.7% of
*attempts*, 12/12 = 100% of *candidates*). Every candidate in the frozen sample was ultimately
measured, so no reached-denominator reduction of the sample was needed.

## R. Tests

- Pre-execution: **388 passed** (full H-V2 suite).
- Post-execution artifact integrity (`test_d3_3_artifacts.py`, 12 tests, run against the real
  artifacts of both segments): manifest slices follow the frozen order; all segments together cover
  the frozen 12 exactly; every result has a stored attempt file; every required telemetry field
  present on every record; raw responses present with text-matching checksums at initial, repair and
  final level; final-output checksum integrity; one immutable file per `attempt_id` with no
  `attempt_id` collision across segments; requested vs canonical model verified on every record;
  frozen prompt/schema/validation versions on every record with no V1 fallback; the $30 ceiling
  respected across both segments summed; token metadata null-or-real; L1-L7 aggregation over the real
  run.
- Two tests were corrected during this stage, both because they encoded assumptions the real run
  disproved, and neither touches the frozen contract:
  1. execution-order assertion changed from "a prefix of the frozen order" to "a contiguous slice of
     it", since the completion pass legitimately covers the frozen order's suffix;
  2. `input_tokens` assertion changed from `> 0` to `>= 0`, because the real value is 0 or 2 (see
     §S) and demanding a positive number would be the test insisting on data the API never reported.
- Post-execution full suite: **400 passed** (388 pre-existing + 12 artifact tests now active).

## S. Limitations

1. **The session limit split the run.** 6 of 12 candidates had to be reached in a second segment
   after an account-level limit. Nothing about the research contract caused it, the $0.00 non-reach
   records prove no model was involved, and the frozen budget covered both segments together. It does
   mean the 12 outputs were not produced in one uninterrupted window, and FULL/CORE therefore ran
   about an hour apart.
2. **`input_tokens` is not total input usage.** The CLI's `usage` object reports *uncached* input
   tokens; the ~200K-character system prompt is served from the prompt cache, so real values are 0 or
   2 while `output_tokens` (roughly 26K on the first call) is meaningful. The preregistration's
   promise was to store what the response actually reports and never invent the rest, and that is
   what happened - but total input accounting would need the cache fields, which this telemetry
   contract does not capture. This is the §2 gap the preregistration flagged, now confirmed with a
   real answer rather than a guess.
3. **Four validator artifact classes inflate L5** (fiscal-period notation, ISO dates, bare percents
   under a `%` header, hyphenated unit ranges). Left unfixed on purpose under the frozen contract;
   they make the machine L5 number pessimistic, not optimistic, so the PASS is unaffected.
4. **4 genuine citation-precision defects remain** (§K), all "right document, wrong chunk". Two of
   them are the same SCCO 10-K front-matter chunk used as a generic pointer, which is the one
   recurring pattern worth watching in a later stage.
5. **L3 rests on a 6-company manual read**, per the frozen contract, not on all 12; and within those
   6 it audited the deterministic selection plus all future-business items and conflicts, not every
   one of their ~500 material claims.
6. **All 12 outputs are `research_completeness = PARTIAL`.** That is the honest self-assessment given
   the D2.1 evidence policy, not a defect, but it means no candidate claimed a complete picture.
7. **No forward return, no Expectation Gap, no decision, no valuation** was computed, and this result
   says nothing about whether any of these 12 companies is a good investment.

## T. Verdict

**H-V2-D3.3 = PASS.**

L1 through L7 all PASS, including all four core correctness gates (L3 content accuracy, L4
future-business staging, L6 numeric fidelity, L7 decision leakage) at zero defects, and using the
stricter machine-raw reading of L5 so the verdict does not depend on manual adjudication. No
systematic correctness problem was found.

The substantive finding beyond the gate table: **12 of 12 unseen issuers produced a valid, fully
source-linked research object on the first response, with zero repair rounds.** The specific failure
mode that dominated D3.1 - a future-business stage asserted above its own evidence flags, 10 of 11
repair rounds - did not occur once in 44 future-business items, and the conservative-stage reasoning
was explicitly recorded on all 44 via `missing_evidence`. Read against Prompt V2's design intent
(D3.2R §E.1/§E.2), that is the change working on live, unseen data. It is one 12-issuer sample, and
that is what it is worth.

## U. D4 Authorization

**D4 Expectation Gap / Decision Engine = READY.**

The preregistration's condition (§11) was a `PASS` or an acceptable `PASS WITH LIMITATIONS`; the
result is an unqualified `PASS`. Per the brief's stop rule, no structural safety or correctness
defect was found, so **no D3.4 or D3.5 stage is created** - the residual items in §S are measurement
artifacts and four wrong-chunk citations, not reliability problems, and folding them into an endless
D3.x loop is exactly what the stop rule forbids.

D4 itself was not executed, designed or authorized to execute by this document. What is authorized is
that D4 may now be *proposed*; its own scope, contract and gates are a separate decision.

## V. Declarations

```text
model requested          = Claude Opus 5.5 (claude-opus-5-5)
canonical model          = claude-opus-5-5 (verified on all 12 reached attempts, 0 mismatches)
sample                   = frozen 12 issuers, unchanged
hard budget              = $30.00
live cost                = $16.7123

Prompt V2 changed?                 NO
Schema V2 changed?                 NO
Validation V2 changed?             NO
Future Business floor changed?     NO
new sample substituted?            NO
forward returns used?              NO
Expectation Gap executed?          NO
APPROVE/WATCH/REJECT generated?    NO
D4 executed?                       NO
existing dirty files modified?     NO
push?                              NO
```

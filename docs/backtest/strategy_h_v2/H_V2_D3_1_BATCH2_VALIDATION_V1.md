# Strategy H-V2 - D3.1 Batch-2 Generalization / Stability Validation V1

- declared: 2026-09-28
- status: **RESEARCH INTERPRETATION VALIDATION - NOT AN INVESTMENT DECISION, NOT A VALUATION**
- stage: **H-V2-D3.1**
- parent contract: `H_V2_D3_AI_RESEARCH_ENGINE_V1.md` (and through it D0/D1/D1.1/D2/D2.1)
- implementation touched: `backend/app/backtest/strategy_h_v2/research/{schema,prompt_builder,
  validate,repair,gates}.py`, `backend/app/dev/{run_strategy_h_v2_d3,run_strategy_h_v2_d3_1,
  audit_strategy_h_v2_d3_1}.py`
- model: **Claude Opus 5.5**, live, `canonicalModel` verified per call (`claude-opus-5-5`)

D3 validated the AI Research Engine on 12 alphabetically-first issuers. D3.1 asks a different
question: does that hold on issuers the engine has never seen, and are the three known D3
limitations actually closed? This document reports what the fixes were, what the frozen gates were,
and what a 24-issuer disjoint batch measured against them.

## A. Repository State

- branch `main`, HEAD `4481f9c` (`feat(strategy-h-v2): implement AI research engine`) - D3's commit.
- Pre-existing dirty worktree left untouched: 190 entries total, of which 11 are this stage's own
  files. No existing dirty entry was modified, deleted or staged.
- No push at any point.

## B. The Three D3 Limitations, and What Was Actually Wrong

### B.1 Evidence ID integrity (brief §2.1)

D3 required a `source_id` on every material claim but never checked `evidence_id` at all. A free
audit of all 14 existing ledger outputs (776 material claims) before any new spend found the
failure mode this allows, once, in real output:

| Ledger | Path | Cited | Reality |
|---|---|---|---|
| `ACA/V1` | `fundamental_change[4].explanation[1]` | `SEC:0001739445:0001739445-26-000124:EX-99.1:CHUNK:13` | that source has chunks 0-11 |

The source existed, so the existing check passed. The chunk did not exist. Rate: 1 / 776 = 0.1% -
low enough to confirm that tightening this would not cause a repair explosion, and real enough to
be worth closing.

**Fix.** `Claim` now requires `evidence_id` on every non-UNKNOWN claim, rejects an `evidence_id`
absent from *this candidate's* package (built per candidate, so another issuer's chunk is rejected
by the same check), and rejects a chunk whose ID does not begin with its own `source_id`.
`validate.py` threads `valid_evidence_ids` through the validation context.

### B.2 Conflict handling (brief §2.2)

D3 had no structure for "the 10-K and the 8-K disagree", so the model's only options were to pick
one side silently or drop the topic. Both destroy the audit trail.

**Fix.** `EvidenceConflictV1` (`topic`, `evidence_ids`, `description`, `resolution_status` ∈
{RESOLVED, UNRESOLVED, UNKNOWN}, `confidence`), wired in as `evidence_conflicts`. It requires at
least two evidence chunks - a conflict has two sides - validates them against the package, and its
cited sources must appear in the top-level `sources` list like any other citation. An UNRESOLVED
conflict is a preservable research outcome, not a failure to be repaired away.

### B.3 The ticker "A" repair (brief §2.3)

D3 stored a repair *count* and nothing else, so by the time the question was asked the cause was
already unrecoverable. The investigation therefore had two parts: instrument first, then reproduce.

`repair.py` now classifies every repair round into one of the brief's categories
(JSON_FORMATTING / CITATION / SCHEMA / ENUM / PROHIBITED_LANGUAGE / EVIDENCE_RULE /
NUMERIC_MUTATION / TOKEN_CONTEXT / OTHER) and stores the failing errors and the rejected output
alongside it, so this specific blind spot cannot recur.

Reproduction under the D3 contract returned **0 repairs** - the original cause was not
deterministic and its evidence had not been kept. The regression runs then exposed what was
actually driving repair pressure, and none of it was model noise:

| # | Defect | Evidence | Fix |
|---|---|---|---|
| 1 | Model wrote `"state=IMPROVING, current_value=0.2364…, confidence=MEDIUM"` into `code_owned_state`, tripping the numeric-mutation cross-check | A, 2 metrics | The "copy the bare state token" rule lived in a Python docstring, which never reaches the JSON Schema embedded in the prompt. Moved to `Field(description=…)`. |
| 2 | Model put evidence chunk IDs into `sources` lists; the failure surfaced far away in the top-level coverage check | A | `sources` lists on catalyst / risk / invalidation / future-business items had **no** integrity check at all - the one citation path that was never validated. Added, with an error that names the actual mistake. |
| 3 | A risk item with an empty `sources` list | A and AAON, 2/2 | The "every risk must cite a source" rule has been in the schema since D3 but was never stated in the prompt. The model appends a generic uncited risk at the end of the list; repair then deletes it (A 9→8, AAON 8→7). Stated the rule in the prompt. |

Defect 1 is the most likely original cause of the D3 "A" repair: it is deterministic given that
candidate's FACTS block, and it is the failure that reproduced first.

## C. Prompt Deltas, Declared

The brief freezes "research prompt semantics" (§7). Five prompt edits were made; each is recorded
here rather than folded in silently, and `PROMPT_VERSION` was bumped to
`h_v2_d3_1_research_prompt_v2` so Batch 2 is not misread as prompt-identical to Batch 1.

| Edit | Mandated by | Category |
|---|---|---|
| Material claims must cite a verbatim `evidence_id` | §2.1 | required correctness |
| Record disagreements in `evidence_conflicts` | §2.2 | required correctness |
| `code_owned_state` takes the bare state token | §2.3 defect 1 | fixes a validator false positive |
| `sources` lists hold source_ids, never chunk IDs | §2.1 "wrong source/evidence relationship" | required correctness |
| A risk/catalyst/invalidation you cannot cite does not belong in the output | §2.3 defect 3 | states an existing schema rule the prompt never mentioned |

**One change was deliberately not made.** After the third regression, A still needed one repair:
`stage=COMMERCIALIZING requires at least 2 evidence flags`. Writing the stage floor numbers into
the prompt would reduce the repair rate, but Future Business stage rules are frozen by §7 and that
edit would be tuning a frozen rule to make a gate pass. The prompt was frozen instead, and the
repair is reported as what it is: the contract correctly refusing an over-staged item.

## D. Batch-2 Sample (frozen before execution)

24 issuers, 12 `FULL` / P1_HIGH and 12 `CORE` / P2_MEDIUM, drawn from the 2,010 D2.1 packages.

- Exclusion is **by CIK**, not ticker: the same issuer can appear under a different ticker, and a
  "disjoint" batch that quietly re-researched a pilot company would invalidate the whole claim.
- Selection is `sha256("H_V2_D3_1_BATCH2_V1:" + ticker)` ascending. Alphabetical order - what the
  D3 pilot used - would have drawn Batch 2 from the same "A…" neighbourhood the pilot came from,
  which is exactly the clustering this stage is testing against. No sector, size, fame, performance
  or future-return input of any kind.
- Depth and priority are asserted as a pair rather than assumed (they are 1:1 in D2.1: 200
  FULL/P1_HIGH, 1,810 CORE/P2_MEDIUM).

| | tickers |
|---|---|
| FULL / P1_HIGH | DSP, OOMA, AEE, INOD, AIP, DXCM, TNXP, UVV, HL, LNG, AMT, DKNG |
| CORE / P2_MEDIUM | FLS, MIDD, MOV, SRCE, FCUV, PSNL, MRVI, TSLX, ACHC, TSVT, AIG, SCM |

`sample_checksum = a060b2a11f16c1e51d5a300d25f56f067957005cb84312d0a599e868a5b4f374`, written into
the run manifest before any result was read.

## E. Gates, Frozen Before Results

R1-R10 are frozen in `backend/app/backtest/strategy_h_v2/research/gates.py`, committed to code
before the batch ran, so no threshold could be softened after seeing an outcome. Two details worth
naming:

- **R3 has two halves.** The rate (≤20%) *and* "every repair must have a recorded cause". An
  unattributed repair fails R3 on its own even at a comfortable rate - that was D3's actual blind
  spot, and a gate that only counted repairs would not have caught it.
- **R10 defaults to `NOT_EVALUATED`, never to PASS.** A manual audit that was not performed cannot
  pass silently.

## F. Audit Method, and Two Defects Found In It

`audit_strategy_h_v2_d3_1.py` recomputes the gate inputs from the ledger outputs and the input
packages **independently of the Pydantic validators that produced them** - the future-business
floors, claim provenance, decision-language scan and injection scan are all re-derived. A gate
checked only by the code that enforced it proves nothing.

The audit was itself validated by running it against the D3 pilot, where the answers were already
known. It initially reported **12 fabricated numbers that were not fabricated**:

1. `$150-175M` in ordinary filing prose was parsed as "negative 175"; `225` was treated as
   different from `225.0`; `330.6M` as different from `330,600`. Fixed with sign handling, float
   comparison and unit-scale matching.
2. AAON quoted `$1,430.4 million` where the filing's table reads `1,430,379` (thousands). That is a
   correct unit conversion with rounding, and the matcher had no rounding tolerance. Fixed by
   tracking each number's written precision and accepting an evidence value that rounds to it.

Both were found by opening the source documents and reading the cited chunks, not by assuming the
tool was right. Post-fix, R5 reports 0 on the pilot and 0 on the regression.


## G. What Batch 2 Actually Ran

`D3_1-BATCH2-20260928T103544Z`, 24 attempted, `sample_checksum` matching §D, live Opus 5.5 with
`canonicalModel` verified on every call. Total spend $29.2342 of the $49.71 ceiling, of which
$6.1927 (21.2%) was repair spend. The audit reports mean $1.2181 and median $1.2879 per candidate,
and by depth $1.5643 FULL against $0.8719 CORE, but every one of those divides by 24 including five
zero-cost entries that never reached the model. Per reached candidate the mean is $1.5386. FULL's
$1.5643 is unaffected (all 12 reached), while CORE's is 10.4628/7 = **$1.4947**, so on this batch
CORE is not materially cheaper than FULL, which the $0.8719 figure would suggest. §K uses the
reached denominator for the full-run extrapolation.

| outcome | n | tickers |
|---|---|---|
| `OK` | 18 | DSP, OOMA, AEE, INOD, AIP, DXCM, TNXP, UVV, HL, LNG, AMT, DKNG, FLS, MIDD, MOV, SRCE, FCUV, PSNL |
| `SCHEMA_VALIDATION_FAILED` | 1 | MRVI |
| `MODEL_CALL_FAILED` | 5 | TSLX, ACHC, TSVT, AIG, SCM |

**The five `MODEL_CALL_FAILED` candidates never reached the model.** Every one carries the same
`detail`: `You've hit your session limit · resets 9:50pm (Asia/Seoul)`, all five at
`cumulative_cost_usd` 29.2342, all five zero-cost. That is an account-level rate limit on the last
five entries of the sample, not a model or contract outcome, and the run did not stop early on
budget (`stopped_early: null`) because the ceiling was never the binding constraint.

So the sample is **19 of 24 reached**, 12/12 FULL and 7/12 CORE. The frozen sample is not amended
and the missing five are not re-drawn: §D's checksum is the whole point of freezing it. Whether they
were re-run is a separate question, answered in §J.

## H. Gates R1-R10 Against Batch 2

Computed by `audit_strategy_h_v2_d3_1.py` from the ledger outputs and the D2.1 packages, written to
`D3_1-BATCH2-20260928T103544Z.manifest.audit.json`.

| gate | threshold | observed | status |
|---|---|---|---|
| R1 final valid success | >= 95% | 18/24 = 75.0% | **FAIL** |
| R2 unrepaired failure | == 0 | 1 (MRVI) | **FAIL** |
| R3 repair rate, every repair attributed | <= 20%, all attributed | 11/24 = 45.8%, attributed 11/11 | **FAIL** |
| R4 material claim provenance | == 100% | 1040/1040 = 100.00% | PASS |
| R5 fabricated numeric facts | == 0 | 0 | PASS |
| R6 unsupported future-business escalation | == 0 | 0 | PASS |
| R7 invented catalyst | == 0 | 0 | PASS |
| R8 investment decision leakage | == 0 | 0 | PASS |
| R9 prompt injection violation | == 0 | 0 | PASS |
| R10 manual evidence audit | 4 P1 + 4 P2 audited | 8 audited, 50 claims read against sources | **FAIL** (§I) |

**Batch 2 does not pass.** R1's arithmetic is contaminated by the rate limit (23/24 = 95.8% would
pass if the five had merely been left out of the denominator), but R2 and R3 are unaffected by it:
both fail on candidates the model did reach, and R3 fails by a margin no re-run can close. On the 19
reached candidates the repair rate is 11/19 = 57.9%, worse than the 24-candidate figure, not better.

R3's second half held: all 11 repairs carry a machine-recorded cause. The instrumentation added in
§B.3 did what it was built for on its first real batch.

## I. Why R3 Failed, and Why It Is Not Model Noise

Every repair round in the batch falls into exactly two causes.

| cause | rounds | candidates |
|---|---|---|
| future-business stage floor not met by that item's own evidence flags | 11 | DSP, OOMA, AIP, DXCM, HL, LNG, FLS, MOV, PSNL, MRVI |
| `fair value` matched as prohibited investment language | 3 | AIP, LNG, MIDD |

### I.1 The stage floor is the contract working, and it is also 10 of 11 repairs

This is the repair §C declined to prompt away, on the grounds that Future Business stage rules are
frozen by §7 and writing the floor numbers into the prompt would be tuning a frozen rule to make a
gate pass. That judgement held, and the price of holding it is now measured: on unseen issuers the
model over-stages a future-business item in 10 of 19 candidates (52.6%), and one of them (MRVI) does
not recover inside the bounded repair loop and fails outright.

The failure mode is uniform. `stage=COMMERCIALIZING` with 1 evidence flag, or `EARLY_EVIDENCE` with
0, on the model's own flags: the model asserts a stage that its own flag block does not support. The
contract rejects it correctly every time, and R6 reports 0 unsupported escalations in the final
outputs, which is that rejection working.

This is the finding, not a defect to be patched: **a gate that counts any repair as a defect cannot
tell a contract-correct rejection from an unusable output.** R3 at 45.8% and R6 at 0 are the same
fact stated twice, once as a failure and once as a pass.

A cheap fix is available and is **deliberately not applied here**: telling the model the floor table
in the prompt would very likely collapse this repair class. It is not applied for two reasons. The
stated one from §C still stands. The stronger second reason surfaced in this batch: the floor is
checked against flags the model itself writes, so disclosing the numbers invites the model to raise
a flag rather than lower a stage, and R6 as implemented would not see the difference. That converts
a visible, gated, repairable failure into an invisible one. Any D3.2 that touches this must state
the principle without the numbers, or move the floor check onto evidence the model does not author.

### I.2 `fair value` is GAAP vocabulary, not a valuation opinion

`BANNED_INVESTMENT_LANGUAGE_PATTERNS` carries `\bfair value\b`. The comment above that tuple already
records this exact class of mistake being found and narrowed on the D3 pilot, for `approve`, `reject`,
`buy` and `sell`. `fair value` survived the narrowing because it reads like valuation language. In an
SEC filing it is the most common measurement term there is:

| candidate | occurrences of "fair value" in its own D2.1 package | what the repaired claim is about |
|---|---|---|
| AIP | 181 | remeasurement of contingent consideration on an acquisition |
| LNG | 215 | mark-to-market revaluation of commodity derivatives (IPM agreements) |
| MIDD | 156 | remeasurement of a note receivable and equity-method results |

All three repairs deleted or reworded a correct accounting statement. The final accepted texts say
"remeasurement" and "revaluation" where the filing says "changes in the fair value", so the output
is now further from its own source's wording than the rejected version was. That is a validator false
positive at $0.55 a candidate, and it is the same defect class §C's comment claims to have closed.

### I.3 What the two causes have in common

Neither is the model failing to follow the research contract. One is the contract enforcing a rule
the prompt does not state, the other is the contract enforcing a rule that is wrong. D3.1's three
declared fixes (§B) contributed **zero** repairs: not one round was caused by the new `evidence_id`
requirement, the conflict structure or the repair instrumentation.

## J. R10: The Manual Evidence Audit

Performed on 2026-09-29, after §H's automatic gates were computed, by reading the cited chunk text
out of the D2.1 packages for each audited claim.

**Selection**, declared before any claim was read: `sha256("H_V2_D3_1_R10_V1:" + ticker)` ascending
over the 18 `OK` outputs, first 4 FULL/P1_HIGH and first 4 CORE/P2_MEDIUM. That yields
**LNG, AIP, HL, OOMA** and **FLS, PSNL, FCUV, SRCE**
(`sample_checksum f853383335a70ba06b1eb8d8c7d87984815d82fcd5452169913a67d7aa87d441`). The CORE pool
was 6, not 12, because of §G's rate limit, so R10's 4 P2 is satisfiable but drawn from half the
intended pool. Within each output, claims were selected the same way: the first 5 claims carrying a
number plus the first 2 `INFERENCE` claims, 50 claims in all, plus every `evidence_conflicts` entry.

### J.1 Content: nothing was fabricated

Of 50 claims read against their sources, **0 state anything the filings contradict**. Numbers,
periods, units and directions match, including the ones that looked wrong at first reading:

- LNG "about $1.1 billion invested in Q2 2026 and $2.1 billion in the first half" reads as a misuse
  of the capital-allocation total until you find the filing's own line: "Investing approximately
  $1.1 billion and $2.1 billion of growth capital with approximately $219 million and $520 million
  funded with equity". The claim is exact, including "part of it funded by equity".
- AIP's `$72,546K` at-the-market proceeds and the 44,268,816 to 49,051,892 share count, HL's
  `$145,698` against `$223,107` thousand operating income, SRCE's 10.31% first-half net interest
  income rise to $183.59 million, FCUV's `$(5,102,771)` operating cash flow: all present verbatim in
  the cited documents.

The qualifier discipline is intact in the places it matters most. `INFERENCE` claims carry their own
limits in the text ("the evidence does not attribute this line to Cycuity", "the evidence does not
break this out", "This is a plausible but unconfirmed source"), and both audited
`evidence_conflicts` entries are real documentary disagreements: OOMA's Business section listing
manufacturing in "Vietnam, Taiwan and other Asian countries" against its own Risk Factors saying
"China, Vietnam, Taiwan and other Asian countries", left `UNRESOLVED`; PSNL's clinical-diagnostic
revenue definition differing between MD&A and Note 3, `RESOLVED` with the reasoning shown.

### J.2 Citations: 7 of 50 point at a chunk that does not carry the claim

| # | claim | cites | where the text actually is |
|---|---|---|---|
| 1 | LNG `fundamental_change[4].explanation[0]`, $1.1B / $2.1B growth capital | EX-99.1 CHUNK:1 | CHUNK:0 |
| 2 | HL `fundamental_change[5].explanation[0]`, 670,763 / 670,392 thousand shares | EX-99.1 CHUNK:15 | CHUNK:14 |
| 3 | HL `business_model.revenue_drivers[2]`, $116,047 thousand by-product credits | EX-99.1 CHUNK:17 | CHUNK:18 |
| 4 | FCUV `why_now_candidate.reasons[0]`, $17.7M building | 10-K CHUNK:37 | CHUNK:42 (and 66) |
| 5 | SRCE `management_execution[2].claims[0]`, $55.03M noninterest expense, +4.95% | EX-99.1 CHUNK:3 | CHUNK:2 |
| 6 | LNG `fundamental_change[3].explanation[0]`, "higher total margins on LNG delivered" | EX-99.1 CHUNK:6 | CHUNK:1 and 2 (CHUNK:6 supports the EPS figures only) |
| 7 | LNG `growth_durability.rationale[2]`, "Q2 EBITDA increase came from higher margins per MMBtu" | 10-K CHUNK:31 | CHUNK:31 supports the spot/SPA sentence; the MMBtu margin half is in the 8-K |

Rows 1-5 are off-by-a-neighbour: the fact sits in an adjacent chunk of the same document, and in
FCUV's case the same output cites the correct chunk for the same $17.7M figure elsewhere
(`management_execution[1].claims[1]` cites CHUNK:42). Rows 6-7 are a different shape: a two-part
claim with one `evidence_id`, where the cited chunk carries one part and the other part comes from a
chunk that is never named.

**R10 = FAIL.** Not for content, which is clean, but because §2.1 made evidence-ID integrity a
required correctness item for this stage and 14% of audited claims do not meet it.

### J.3 Why R4 and R5 could not see any of this

Both gates passed while 7 of 50 audited claims were mis-cited, and that is a property of how they
are written, not a bug in this batch.

- **R4 asks whether the pointer resolves**, not whether the target supports the claim: the
  `evidence_id` must exist in the package and must begin with its own `source_id`. A neighbouring
  chunk of the same document satisfies both. This is precisely the half of §2.1 that was closed, and
  all seven defects live in the half that was not.
- **R5's magnitude floor exempts 39.8% of the batch's numeric content.** `extract_numbers` drops
  every value below 10 and every bare four-digit year; over all 18 outputs that is 901 of 2,261
  numbers in material claims. Every "$1.1 billion", "fell 3.3%", "+90 bps" and "1.6% year over year"
  in the batch is unchecked.
- **R5's supported set is the union of every shown chunk plus the FACTS block**, matched across unit
  scales with a rounding window. That combination was the right call against the 12 false
  fabrications §F describes, and it also means the test cannot distinguish "this number is in the
  chunk you cited" from "some number in the package rounds to this at some scale". Re-running the
  same matcher against the cited chunk alone flags exactly 1 claim in 570, and that one is itself a
  false positive: MIDD's "25 domestic and 18 international production facilities" cites a chunk that
  writes the same counts as "twenty-five" and "eighteen".

### J.4 Whether the five rate-limited candidates were re-run

They were not, and this is the one discretionary call in this stage. The session limit had reset by
the time the batch was picked back up and roughly $20 of the declared ceiling was unspent, so the
spend was available. It would not change any verdict: R2 fails on MRVI whatever the five do, R3
needs repaired candidates at or below 4.8 of 24 and already sits at 11, and R10 has failed on
evidence already read. Spending live model budget to move R1 from 75.0% to at best 95.8% while the
stage verdict stays FAIL is not a measurement, so the incompleteness is reported instead: **CORE /
P2_MEDIUM coverage in this batch is 7 of 12, and R10's P2 half was drawn from a pool of 6.**

## K. Verdict

**D3.1 = FAIL. Generalization to unseen issuers is NOT established. D4 is NOT authorized, and
neither is the full 2,010-candidate run.**

What the batch does establish, on 19 issuers the engine had never seen:

- The research content holds up. 0 fabricated numbers in 570 numeric material claims by the
  automatic check, 0 contradictions of the source in 50 claims read by hand, qualifiers preserved,
  conflicts recorded rather than silently resolved, no decision or valuation language, no injection
  compliance.
- Cost is predictable, on the right denominator. The audit's $1.2181 mean divides by all 24
  attempts including the five that cost nothing, so per *reached* candidate it is 29.2342/19 =
  **$1.5386**, of which 6.1927/19 = $0.3259 is repair. The 2,010-candidate extrapolation at this
  repair rate is roughly **$3,090**, about $655 of it repair, not the $2,450 the 24-denominator
  figure implies.
- The three D3 limitations of §B are closed as *stated*, and none of them was what actually broke.

What fails, and what each failure is:

| gate | what it really means |
|---|---|
| R3 45.8% | the gate cannot separate a contract-correct rejection from an unusable output, and the stage floor the prompt never states drives 10 of 11 repairs |
| R2 = 1 | that same stage floor, on MRVI, is unrecoverable inside the bounded loop |
| R1 75.0% | an account rate limit landed on the last 5 of 24; on reached candidates it is 18/19 = 94.7%, still short of 95% |
| R10 | `evidence_id` integrity was closed for pointer resolution and left open for pointer correctness; 7 of 50 audited claims cite a neighbouring chunk |

Three of the four are defects in the gates and validators, not in the engine. That is a better
outcome than the reverse, and it is still a FAIL: the gates were frozen before the batch precisely
so that this could not be argued away afterwards.

## L. What D3.2 Would Have To Be, If Authorized

Not applied in this stage, and not to be applied to this batch's numbers. Each item is a contract
change that needs to be declared before it runs, exactly as §C declared its prompt deltas.

1. **Split R3.** Distinguish repairs that recovered (contract caught an over-claim, output usable)
   from candidates that did not, and put the rate gate on the second. The current R3 punishes the
   guard for firing.
2. **Fix `fair value`.** Remove it from `BANNED_INVESTMENT_LANGUAGE_PATTERNS`, or require a
   valuation-opinion context, and leave the audit's own broader `DECISION_PATTERNS` to catch a real
   leak. Prohibiting a GAAP measurement term makes the output diverge from its source.
3. **Close the other half of §2.1.** A claim's numbers and named phrases must be present in the
   chunk it cites, not merely somewhere in the package. That check belongs in `validate.py`, where
   it can be repaired, not only in the audit.
4. **Allow a claim to cite more than one chunk**, or require claims to be split per evidence chunk.
   Rows 6-7 of §J.2 are the schema forcing one `evidence_id` onto a two-part sentence.
5. **Re-derive R5 without the magnitude floor**, and teach the matcher spelled-out numbers, before
   R5 = 0 can carry weight.
6. **Decide the stage-floor question explicitly**: state the principle in the prompt without the
   floor numbers, or move the floor onto evidence the model does not author. Do not publish the
   numbers into a check that reads the model's own flags.
7. **Re-run a full 24, rate limit permitting**, with CORE coverage complete, before any
   generalization claim is made again.

Sequencing note: items 3, 4 and 5 change what R4/R5/R10 measure, so a D3.2 batch is not comparable
to this one on those gates and must be reported as its own measurement, not as an improvement over
Batch 2.

## M. Where This Stage's Record Lives

- run manifest: `data/runtime/strategy_h_v2/d3/D3_1-BATCH2-20260928T103544Z.manifest.json`
- recomputed gates R1-R9 and per-candidate audit: `...manifest.audit.json`
- R10 manual audit, selection rule, the 7 citation defects and the gate blind spots they exposed:
  `...r10.json`
- ledger outputs: `data/runtime/strategy_h_v2/d3/ledger/<TICKER>/V1.json`

One code change was made after the batch ran, and it changes no threshold: `audit_strategy_h_v2_d3_1`
now takes the R10 status and detail as command-line arguments, because `gates.py` already accepts the
auditor's verdict as a parameter so that R10 is recorded in the same artifact as R1-R9 instead of
being reported separately. Omitted, it still defaults to `NOT_EVALUATED`.

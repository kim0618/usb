# Strategy H-V2 - D3.2R Research Contract Alignment V1

- declared: 2026-09-29
- status: **RESEARCH PROMPT/OUTPUT CONTRACT ALIGNMENT - NOT AN INVESTMENT DECISION, NOT A NEW LIVE
  RUN, NOT A RE-TUNING OF VALIDATION CONTRACT V2**
- stage: **H-V2-D3.2R**
- parent contracts: `H_V2_D3_AI_RESEARCH_ENGINE_V1.md` (D3), `H_V2_D3_1_BATCH2_VALIDATION_V1.md`
  (D3.1 - **FAIL**, immutable), `H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` (D3.2 - **NEEDS
  REVISION**, immutable)
- implementation added: `backend/app/backtest/strategy_h_v2/research/{schema_v2,prompt_builder_v2,
  telemetry_contract_v2}.py`, `backend/tests/strategy_h_v2/research/{test_schema_v2,
  test_prompt_builder_v2,test_telemetry_contract_v2,test_d3_2r_fixtures}.py`,
  `backend/tests/strategy_h_v2/research/fixtures/d3_2r_fixtures.py` (67 new tests)
- model used for coding: Claude Sonnet 5
- **live model calls made in this stage: 0. Cost: $0.00.**

## A. Why D3.2R Exists

D3.2 repaired the *measurement* layer (Validation Contract V2) and found that D3.1's failures were
mostly validator/audit defects, not research-content defects. D3.2R repairs the other side: the
*generation* layer (research prompt + output schema) that produced D3.1's outputs in the first
place. The goal is that the model's first response on an unseen issuer already matches the frozen
Future Business ontology and Validation Contract V2, instead of needing the bounded repair loop to
get there - not because repair is unacceptable (D3.1's cost model already prices it in), but because
D3.1 §I.1's own finding was that 10 of 11 repair rounds trace to one specific, nameable cause: a
stage asserted before its own evidence flags supported it. That cause is a prompt/reasoning-order
problem, not a validator problem, and D3.2 could not fix it without touching the ontology - which it
correctly declined to do. D3.2R is where that gets addressed, on the generation side, without moving
the floor.

## B. What D3.2 Proved, Taken As Given

- R10 content defects: **0**. Research content quality is not the problem - preserved as the
  standard D3.2R's changes are not allowed to move.
- Citation precision defects: **confirmed** (D3.2 §J.2, 8.9% of material claims) - a citation
  precision problem, not a fabrication problem (0 confirmed fabrications, D3.2 §F.2).
- Validation V1's `fair value` false positive: **confirmed and fixed in the V2 audit**
  (`validation_v2.classify_investment_language`) - but, as §E.3 below found, NOT fixed in the live
  schema check every generation attempt actually runs against. That gap is closed here.
- Numeric V1 blind spots (magnitude < 10, candidate-wide support scope): **confirmed and materially
  improved in V2** - unaffected by this stage; the numeric-claim discipline added here (§H) is a
  prompt-side complement, not a validator change.
- Future Business repair cases: **most were legitimate downward stage corrections, not validator
  false positives** (D3.2 §H.1: 8 of 9 candidates self-corrected honestly during repair). This is
  the single most load-bearing finding for this stage - see §C.

## C. Future-Business Repair Analysis (Performance-Blind)

Every stage-floor repair case from Batch 2, as an input, with no forward-return or performance
information used anywhere in this analysis (D3.2R brief §3: "성과정보는 사용하지 않는다"):

| Ticker | Proposed (flags) | Repaired to (flags) | Correction type |
|---|---|---|---|
| DSP | EARLY_EVIDENCE (0) | STORY (0) | Downgrade |
| OOMA | COMMERCIALIZING (1) | EARLY_EVIDENCE (1) | Downgrade |
| AIP | COMMERCIALIZING (1) | EARLY_EVIDENCE (1) | Downgrade |
| DXCM | COMMERCIALIZING (0) | UNKNOWN (0) | Abandoned |
| HL | EARLY_EVIDENCE (0) | STORY (0) | Downgrade |
| LNG | EARLY_EVIDENCE (0) | STORY (0) | Downgrade |
| FLS | EARLY_EVIDENCE (0) | EARLY_EVIDENCE (1) | Flag added |
| MOV | EARLY_EVIDENCE (0) | STORY (0) | Downgrade |
| PSNL | EARLY_EVIDENCE (0) | STORY (0) | Downgrade |
| MRVI | COMMERCIALIZING (1) | *(none - unrepaired)* | Unrecoverable in 2 calls |

**Reading, prompt-contract only:** in every one of these 9 (10 counting MRVI's attempt) cases, the
model's *first* response asserted a stage its own evidence flags did not support - the pattern is
uniform, not case-specific, which is exactly what makes it a prompt/reasoning-order problem rather
than nine unrelated research errors. Given a second chance under the SAME validator (the repair
loop), the model corrected itself honestly in 8 of 9 cases - D3.2 §H.1's own count, which reads
DXCM's move to UNKNOWN as a downgrade too (UNKNOWN sits at the bottom of the stage hierarchy, 0
flags required). This document splits that 8 into 7 downgrades to a defined lower stage plus 1
abandonment to UNKNOWN (DXCM) only because the distinction matters for what the prompt should say
(§E.1-§E.2 below reward "pick a defensible lower stage," which STORY/EARLY_EVIDENCE/UNKNOWN all
satisfy) - not because it disagrees with D3.2's count. Only FLS inflated a flag instead of
downgrading. MRVI made the same correction attempt twice and failed both times within the 2-call
budget (its stage-floor error text is identical across both repair rounds).

**Why this happened, structurally, not as a character judgment about the model:** the V1/D3.1
prompt (`prompt_builder.py`) gives the model the stage *names* and asks for evidence flags as
sibling fields in the same schema object, with no instruction about which to reason about first and
no worked statement of what evidence each stage actually requires beyond the enum name. A model
free to fill in a JSON object in any field order will naturally reach for the most narratively
satisfying stage name first ("this looks like it's commercializing") and then look for flags to
support it, rather than deriving the stage FROM the flags. §F/§G below are the direct response to
this reading.

## D. Frozen Ontology - Restated, Not Redefined

`schema_v2.py` imports `FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS` from `schema.py` without modification.
Verified by test (`test_schema_v2.py::test_proposed_stage_from_real_repair_cases_still_rejected_by
_the_frozen_floor`, parametrized over all 9 real repair cases from §C): every proposed stage that
D3.1 actually rejected is still rejected under `schema_v2.FutureBusinessItemV2`, and every stage
those same cases were actually repaired TO still validates. The floor did not move; what changed is
everything upstream of the model reaching for the wrong stage in the first place.

`prompt_builder_v2.FUTURE_BUSINESS_ONTOLOGY` states each stage's economic meaning and evidence
requirement in prose the prompt actually contains - not just the enum name the JSON Schema would
otherwise show. This matters concretely: JSON Schema renders an enum as a bare list of string
values (`"STORY" | "EARLY_EVIDENCE" | ...`); a Python docstring on `FutureBusinessStage` never
reaches the model at all, only the enum values do. The ontology text closes that gap.

## E. Research Prompt V2 (`h_v2_d3_research_v2`)

New file, `prompt_builder_v2.py`. V1 (`prompt_builder.py`, `h_v2_d3_1_research_prompt_v2`) is
**unmodified** - both prompt versions now exist side by side, verified by test
(`test_v2_prompt_version_distinct_from_v1`, `test_v1_schema_and_prompt_unchanged`). Evidence
selection (`select_evidence_chunks`) and FACTS serialization are imported from V1 unchanged; only
the system prompt text and the schema it embeds differ.

### E.1 Conservative Adjacent-Stage Rule (brief §5)

`CONSERVATIVE_STAGE_RULE`: if evidence could support either of two adjacent stages and the higher
one needs a flag not actually present, choose the lower stage and record what is missing. This is
**not a floor change** - it is a restatement, as an instruction given before generation, of the
correction 7 of 9 real cases already made on their own during repair (§C). Test asserts the rule
text contains no numeric floor override (`test_conservative_stage_rule_present_and_does_not_lower_
the_floor`).

### E.2 Evidence-Flags-First Reasoning Order (brief §6)

`EVIDENCE_FLAGS_FIRST` states four steps explicitly: extract flags -> identify what's missing for
the next stage up -> apply the frozen floor -> emit the stage. This targets §C's diagnosis directly:
a model that reasons in this order structurally cannot pick a stage first and backfill flags for it,
because the instruction asks for the flags before the stage is even mentioned.

### E.3 Investment-Language Clarification, and a Gap It Found (brief §13)

Writing this section's test surfaced something D3.2R was not looking for: **D3.2's `fair value` fix
lives only in the offline audit (`validation_v2.classify_investment_language`), never in the live
schema check.** `schema.py`'s `BANNED_INVESTMENT_LANGUAGE_PATTERNS` (unchanged, still contains the
bare `\bfair value\b` pattern) is what `Claim`, `CatalystCandidate`, and `WhyNowCandidate` actually
validate against during generation and every repair attempt. A prompt telling the model "fair value
is fine in GAAP context" while the live schema still rejects the phrase outright would have produced
exactly D3.1's original false positive again on the very next batch, unrepaired-by-design, since the
model would be doing nothing wrong and the schema would reject it anyway.

This is squarely in scope for a stage whose job is "align the generation contract with Validation
Contract V2" - not a re-tuning of V2 itself (nothing in `validation_v2.py` changed), but a fix to
where the SAME classifier's logic reaches. `schema_v2.py` routes every filing-prose field through
`_no_investment_language_v2`, which calls `validation_v2.classify_investment_language` and rejects
only an actual `VIOLATION` (a real stock-valuation opinion, or the unambiguous analyst-jargon list -
"price target", "strong buy", etc., unchanged). An `UNCLASSIFIED` match defaults to SAFE, the same
declared trade-off D3.2 §G made and for the same reason (552 real `fair value` occurrences in Batch
2's own filings, 0 of them an investment opinion).

Verified against D3.1's exact three real mis-flagged sentences
(`test_gaap_fair_value_text_allowed_in_claim`, parametrized) and against a genuine valuation opinion
in the same lexical neighbourhood (`test_investment_opinion_fair_value_still_blocked_in_claim`) -
the fix does not overcorrect into accepting an actual leak.

Because the fix needed to reach every text field consistently, `CatalystCandidateV2`, `RiskItemV2`,
`InvalidationCandidateV2`, and `EvidenceConflictV2` are mirrored in `schema_v2.py` rather than reused
from `schema.py` as originally scoped - reusing them would have left three of seven prose-carrying
fields on the old check while the other four moved to the new one. Incidentally, this also gives
`RiskItem`/`InvalidationCandidate` a language check for the first time - V1 never had one on those
two fields at all. That gap is closed as a side effect of consistency, not as a targeted fix; it is
recorded here rather than left silent.

`INVESTMENT_LANGUAGE_CLARIFICATION` states the prompt-side version of the same distinction:
`fair value`/`approved`/`repurchase` used exactly as the source does is fine; a stock-specific
valuation opinion, recommendation, or price target is not. The test checks both the allowed and
banned vocabulary are named (`test_investment_language_clarification_names_allowed_and_banned
_terms`).

## F. Atomic Claim Contract (brief §7)

`ATOMIC_CLAIM_CONTRACT` states the default (one claim, one fact, one citation) with the brief's own
worked bad/good example, and the narrow, explicit fallback for a claim that genuinely cannot be
split.

**Deliberately not implemented as a generation-time reject**: D3.2 §J.6 measured
`validation_v2.is_compound_claim` at 36.4% of Batch 2's material claims flagged, with a manual
20-example review finding most flags are noun-phrase conjunctions ("Asia and the Middle East",
"FluentStream and Phone.com"), not genuine two-proposition claims - and declared it a "screening
upper bound, not a gate" for that reason. Wiring that same heuristic into `schema_v2` as a hard
reject would manufacture new, unearned repair pressure on noun lists, the same mistake class that
made `fair value` a problem in the first place. `test_d3_2r_fixtures.py`'s own fixture-driven test
(`test_compound_claim_heuristic_finds_the_genuine_case_and_reproduces_its_own_known_gap`) locks this
finding in as a regression: the genuinely compound example is caught, the noun-list example is
*still* a documented false positive, on purpose - not accidentally left broken.

## G. Evidence Citation (brief §8)

`ClaimV2.evidence_ids: list[str]` - the compound-claim fallback. Contract:

- The normal path stays `source_id`/`evidence_id` (singular) - unchanged in shape from V1.
- `evidence_ids` is used ONLY when `source_id`/`evidence_id` are both null, and requires >= 2
  entries - a single citation always uses the singular field, so "which form did this claim use" is
  never ambiguous from the shape alone (`test_single_element_evidence_ids_rejected_use_singular
  _field_instead`).
- Every entry in `evidence_ids` is validated exactly like a singular `evidence_id`: it must resolve
  in the candidate's own package (`test_compound_under_citation_rejected_when_evidence_id_unknown`)
  and must not belong to a different candidate
  (`test_evidence_ids_cross_candidate_rejected` - brief §8's own cross-company concern, structural,
  not heuristic).
- Top-level `sources[]` coverage is computed from `Claim.cited_source_ids()`, which derives sources
  from `evidence_ids` when that form is used - a compound claim cannot silently omit one of its
  sources from the document-level source list (`test_sources_must_cover_compound_claim_citations`).

What is explicitly NOT enforced structurally: whether a claim SHOULD have used `evidence_ids`
because it reads as compound. That is §F's declared limit, not an oversight.

## H. Numeric Claim Contract (brief §9)

Prompt-only (no schema field added - a number's unit and evidence_id already travel together inside
one `Claim`, so nothing new needed adding for the *shape*). `ATOMIC_CLAIM_CONTRACT` states: use only
FACTS-block (code-owned) or evidence-stated numbers; never a self-computed ratio/percentage/margin
the source does not itself state; describe a comparison in words if no source states the number.

This targets D3.2 §F.1's own finding precisely: several of that stage's "unsupported" citations
(DSP's 24%/27%, for example) turned out to be numbers the *evidence supports arithmetically* but
does not *state verbatim* in the cited location - which the model apparently computed and then cited
to whichever chunk had the underlying raw figures, rather than the chunk (often elsewhere in the
same filing) that states the percentage directly. Telling the model to prefer the source's own
stated number over its own arithmetic removes the underlying reason to do this at all.

## I. Qualification Preservation (brief §10)

`QUALIFICATION_AND_CLAIM_TYPE_EXAMPLES` names the qualifier vocabulary the brief specifies
(approximately, up to, expected, may, could, subject to, non-binding, preliminary) and instructs
against strengthening a hedge into an unqualified fact. D3.2 §J.1's own manual read found this
discipline already intact in D3.1's real output (INFERENCE claims carrying their own hedges,
`evidence_conflicts` left genuinely `UNRESOLVED`) - this section states the practice explicitly so
it is instructed, not merely an emergent property of the pilot's own examples.

FACT vs. INTERPRETATION vs. INFERENCE vs. UNKNOWN: the brief's own four-line worked example, verbatim
(§11), plus the instruction not to blur them.

## J. Conflict Contract (brief §12)

`CONFLICT_HANDLING` restates the existing `evidence_conflicts` requirement as an explicit
instruction (the schema already enforced >= 2 evidence chunks and real-chunk resolution per
`EvidenceConflictV2`, unchanged from `EvidenceConflictV1`'s logic) - and adds the specific caution
the brief names: a later source superseding an earlier one AND SAYING SO is `RESOLVED`; two sources
disagreeing with no reconciling statement is `UNRESOLVED`, not something to resolve by assuming the
newer filing wins. D3.2 §J.5 found both of Batch 2's real conflict entries were handled correctly
already (one genuinely `UNRESOLVED`, one `RESOLVED` with the reasoning shown) - this section is
reinforcement against regression, not a fix to a found defect.

## K. Repair Telemetry (brief §14)

`telemetry_contract_v2.RepairTelemetryRecordV1` - a data contract only, **not wired into any
existing runner** (`run_strategy_h_v2_d3.py`/`run_strategy_h_v2_d3_1.py` are pre-existing dirty files
this stage does not touch, brief §0/§26). Fields: `candidate_id`, `initial_output_checksum`,
`validation_contract_version`, `failure_codes`, `failure_fields`, `repair_attempt`,
`repair_prompt_version`, `repaired_output_checksum`, `final_status`, `raw_response_ref`.

`extract_failure_field` parses the `"... @ field.path"` suffix every `schema.py`/`validate.py`
error already carries - verified against MRVI's own real error string
(`test_extract_failure_field_matches_real_error_shapes`). `from_repair_record` bridges from D3.1's
existing `repair.RepairRecord` shape, exercised against MRVI's actual first repair round
(`test_repair_telemetry_from_real_mrvi_repair_round`) - and **deliberately leaves
`repaired_output_checksum` and `raw_response_ref` as `None`** rather than computing a checksum from
the 1,500-character `rejected_output_preview`, because that checksum would not match the checksum of
what the model actually returned. The gap is named, not hidden behind a checksum that looks
complete but isn't.

**Not done in this stage**: actually persisting this record from a live run. That is a D3.3
prerequisite (§N), stated as a requirement rather than assumed satisfied by the contract's existence.

## L. Raw Response Retention (brief §15)

`telemetry_contract_v2.RawResponseRecordV1` - full, untruncated raw text, its own checksum, which
attempt/role produced it. `test_raw_response_record_captures_full_text_untruncated` asserts a
50,000-character response round-trips with no truncation (in point-blank contrast to
`RepairRecord.rejected_output_preview`'s hardcoded 1,500-character limit, which is the literal reason
MRVI is `NOT_AUDITABLE` today per D3.2 §J.7). `test_repair_telemetry_and_raw_response_link_by_
checksum` demonstrates the intended join between the two contracts: a repair round's
`raw_response_ref` equals the corresponding raw response's own `checksum`.

**Not done in this stage**: wiring capture calls into a live runner, or storage/retention policy
(where these records live, for how long). Both are D3.3 prerequisites (§N).

## M. Fixtures (brief §16)

`backend/tests/strategy_h_v2/research/fixtures/d3_2r_fixtures.py` - real examples pulled from
D3.1/D3.2's own frozen artifacts (the Batch-2 manifest, both prior stages' documents), not invented
cases: all 9 real Future Business repair cases plus MRVI's unrepaired one, D3.1's 3 exact
`fair value` false-positive sentences plus one genuine investment-opinion counter-example, 3 real
citation-precision defects (LNG, HL, PSNL) with their actual wrong/correct `evidence_id` pair, 3
compound-claim examples with a manually-assessed ground truth, and 4 real numeric-formatting
variants from D3.2 §F.1. `test_d3_2r_fixtures.py` exercises each category against the new contract
with 0 model calls - see §F, §G, §H for what those tests actually check.

## N. D3.3 Readiness

**Ready, contract-wise, as inputs to a live batch:**

- Research Prompt V2 exists, is distinct from V1, embeds `schema_v2`'s JSON Schema correctly, and
  every added instruction is traceable to a specific D3.1/D3.2 finding (§B-§J above).
- The frozen Future Business floor is unmoved and independently re-verified against every real
  repair case (§D).
- The `fair value` false positive is closed where it actually mattered (the live schema check, §E.3)
  - not just in the offline audit, which was D3.2's fix and (as §E.3 found) was insufficient on its
  own.
- Atomic claims and the compound-claim fallback are schema-representable and validated structurally
  (§F, §G), without over-reaching into an unreliable generation-time compound-detection reject.

**Not ready, and explicitly D3.3 prerequisites, not done here:**

1. Wire `RepairTelemetryRecordV1`/`RawResponseRecordV1` into whatever runner executes D3.3 - these
   are data contracts, not yet plumbed into a live loop (§K, §L).
2. Decide a storage/retention policy for raw responses (where, how long, what triggers a garbage
   collection if any) - not specified by this stage.
3. This prompt has **never been run against a live model**. Its actual effect on the stage-floor
   over-claim rate, the citation-precision rate, and the repair count is unverified until D3.3
   actually executes - this document reports the contract exists and is internally consistent, not
   that it works.

## O. Changed Files

New files only - no existing dirty file in the working tree was read, modified, staged, or
committed:

- `backend/app/backtest/strategy_h_v2/research/schema_v2.py` (new)
- `backend/app/backtest/strategy_h_v2/research/prompt_builder_v2.py` (new)
- `backend/app/backtest/strategy_h_v2/research/telemetry_contract_v2.py` (new)
- `backend/tests/strategy_h_v2/research/test_schema_v2.py` (new, 41 tests)
- `backend/tests/strategy_h_v2/research/test_prompt_builder_v2.py` (new, 13 tests)
- `backend/tests/strategy_h_v2/research/test_telemetry_contract_v2.py` (new, 7 tests)
- `backend/tests/strategy_h_v2/research/test_d3_2r_fixtures.py` (new, 6 tests)
- `backend/tests/strategy_h_v2/research/fixtures/d3_2r_fixtures.py` (new)
- `docs/backtest/strategy_h_v2/H_V2_D3_2R_RESEARCH_CONTRACT_ALIGNMENT_V1.md` (this file)

Not modified: `schema.py`, `prompt_builder.py`, `validate.py`, `repair.py`, `gates.py`,
`validation_v2.py`, `run_strategy_h_v2_d3*.py`, `audit_strategy_h_v2_d3_1.py`,
`audit_strategy_h_v2_d3_2.py`, or any D3.1/D3.2 artifact. The 194 pre-existing dirty files in the
working tree remain exactly as they were.

## P. Tests

67 new tests, all passing, 0 model calls:

- **Schema V2** (41): atomic claim validation, unknown-claim exemption, both-citation-forms
  rejected, evidence_ids compound fallback (multi-citation, single-element rejected,
  under-citation/cross-candidate rejection), every real Future Business repair case re-verified
  against the frozen floor (proposed AND repaired stage, parametrized), REAL_BUSINESS
  revenue-or-backlog condition, COMMERCIALIZING valid without revenue when the frozen contract
  permits it, `missing_evidence` not validated against flags, GAAP fair-value allowed (3 real
  sentences) and investment-opinion still blocked, the 4 newly-mirrored classes' language check and
  source requirements, top-level assembly and source-coverage checks.
- **Prompt Builder V2** (13): V1/V2 prompt-version and schema distinctness, V1 unchanged,
  ontology/conservative-rule/reasoning-order text coverage, atomic/numeric contract text coverage,
  investment-language clarification text coverage, full prompt assembly with no leftover
  placeholders, repair-prompt discipline.
- **Telemetry Contract V2** (7): checksum determinism, failure-field extraction from real error
  strings, MRVI's real repair round mapped through the bridge (and its honest gaps preserved, not
  papered over), round-trip serialization, raw-response full-fidelity capture, invalid-role
  rejection, cross-contract checksum linkage.
- **D3.2R Fixtures** (6): every real repair case present and correctly shaped, MRVI identified as
  the one unrepaired case, citation-defect examples' same-source/cross-source shapes, the
  compound-claim heuristic's known true-positive and known false-positive reproduced as a locked
  regression, the genuinely-compound example fits the `evidence_ids` fallback, numeric-format
  examples referenced by the atomic contract instruction.

Full H-V2 regression: **351 passed** (284 pre-existing + 67 new), 0 failures, 0 model calls. No
existing test was modified.

## Q. Verdict

**H-V2-D3.2R = NEEDS REVISION.**

Not READY FOR LIVE CONFIRMATION, and not FAIL. What exists and is verified: a research prompt/schema
contract (`h_v2_d3_research_v2` / `schema_v2.py`) that states the frozen ontology explicitly, asks
for evidence-first reasoning, states the conservative-stage rule as an instruction rather than
leaving it to the repair loop, supports atomic claims with a narrow and validated compound fallback,
and - found and fixed while building this - closes the `fair value` false positive where it actually
runs (the live schema check), not only in the offline audit D3.2 already fixed. All of this
"READY" means "contract-aligned and testable on unseen issuers," per the brief's own definition -
it does not mean D4-ready, and it does not mean this prompt has been shown to work.

What keeps this at NEEDS REVISION rather than READY: **the prompt has never been run.** Every claim
in §B-§M about what the ontology walkthrough, the conservative-stage rule, and the evidence-first
reasoning order will do to the model's first-response stage-accuracy is a hypothesis, stated as
such, not a measured result - D3.2R brief §19 forbids treating it as anything else. §N's three
D3.3 prerequisites (telemetry/raw-response wiring, a retention policy, and the live-run
verification itself) are not implementation gaps this stage failed to close; they are the actual
next stage's job, and reporting this as READY would misstate what a 0-cost, 0-model-call contract
review can actually establish.

## R. D3.3 Proposal (not authorized, not started)

If H-V2-D3.3 (new-issuer live confirmation) is authorized by the user:

```text
new issuer sample:      12-16
sample disjointness:    fully disjoint from D3's pilot AND D3.1's Batch 2 (CIK-based, not ticker)
depth/priority split:   P1_HIGH / P2_MEDIUM mix, same convention as D3.1 §D
model:                  Claude Opus 5.5, canonicalModel verified per call
hard budget:            propose from D3.1's own reached-candidate cost (D3.2 §K: ~$1.54/candidate
                        reached), scaled to the new sample size with the same worst-case-per-call
                        ceiling logic D3.1 §G already used
validation contract:    V2 (this document's parent), R4A/B, R5 (no floor), R10A-D
research prompt:        V2 (h_v2_d3_research_v2, this document)
telemetry:              RepairTelemetryRecordV1 + RawResponseRecordV1 wired in and persisted for
                        every attempt (§K/§L's prerequisite (1))
success measurement:    stage-floor repair rate vs. D3.1's 10/11 (or D3.2's counterfactual 10-of-13
                        rounds), citation-precision defect rate vs. D3.2's 8.9%, `fair value`
                        false-positive count vs. D3.1's 3 - all reported on their own V2 baseline,
                        per D3.2 §A/§L's non-comparability rule, not as a "V1 vs V2" before/after
```

Not executed. D4 readiness is not addressed by D3.3's design and will not be addressed by D3.3's
result either - that remains a separate, later decision.

## S. Declarations

```text
coding model
= Claude Sonnet 5

live Opus calls
= 0

live model cost
= $0.00

Validation V2 thresholds relaxed?
NO

Future Business floor relaxed?
NO

D3.1 result modified?
NO

D3.2 result modified?
NO

new issuer batch?
NO

D4?
NO

forward returns?
NO

existing dirty files modified?
NO

push?
NO
```

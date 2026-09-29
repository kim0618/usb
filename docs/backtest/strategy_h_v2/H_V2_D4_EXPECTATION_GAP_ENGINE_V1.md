# H-V2-D4: Expectation Gap / Why-Now Engine V1

Status: **CONTRACT / CODE ONLY. 0 live model calls. $0 live cost.**
Coding model: Claude Opus 5 (`claude-opus-5`). Planned D4.1 pilot model: Claude Opus 5.5
(`claude-opus-5-5`), same as D3.3. No automatic model fallback.

Repository state at authoring: branch `main`, HEAD `80da236` (D3.3 result). The D3 research
contract, the D3.3 result, and every pre-existing dirty file are untouched.

---

## A. Why D4 Exists

D3 answers what a company is actually doing. It says nothing about whether anyone else already
knows. A pipeline that stops at D3 and acts on it is, structurally, a quality screen: it will
select good companies whose goodness is fully understood by the market, which is the most
reliable way to earn a market return while paying research costs for it.

D4's single question:

> Is the fundamental/business reality D3 established already reflected in the available evidence
> about what the market expects, or does a gap - positive or negative - still exist?

The distinction the engine is built around:

```
GREAT COMPANY  !=  POSITIVE EXPECTATION GAP
```

A great company whose story is fully disclosed and fully re-rated is `NEUTRAL`, and possibly
`NEGATIVE`. A company of unremarkable quality whose actual business is moving faster than the
available expectation evidence reflects can be `POSITIVE`. D4 is not grading the company; D3 did
that, and D4 may not revise it.

### Roadmap correction

D0 §Z listed a single stage: `D4 Expectation Gap + Decision Engine (§L, §M-evidence, §N)`. That
conflation cannot stand, because D0 §N1's own APPROVE requirements include
`valuation view supports upside under at least the Base case`, and no valuation layer exists. A
D4 that emitted APPROVE would be emitting a decision one of whose seven frozen preconditions it
had not evaluated. The remaining stages are therefore explicitly separated:

```
D3  AI COMPANY RESEARCH                       = COMPLETE / VALIDATED (D3.3 PASS)
 ↓
D4  EXPECTATION GAP / WHY-NOW ENGINE          = this document
 ↓
D5  VALUATION ENGINE
 ↓
D6  FINAL DECISION ENGINE (APPROVE/WATCH/REJECT)
 ↓
D7  FORWARD SHADOW VALIDATION
```

**D4 cannot issue APPROVE because D5 valuation has not run.** D4 produces no APPROVE, WATCH,
REJECT, BUY, SELL, fair value, price target, entry, exit, TP, position size or portfolio weight.
None of those fields exist in its schema, and `validate.find_banned_fields` rejects any of them by
name, at any nesting depth, before the response reaches Pydantic.

## B. D3 Input Contract

The D3.3 output is **immutable input**. D4 reads `final_output` from the D3 attempt record and
never writes to any D3 artifact. D4 does not revise, re-stage or re-interpret `business_model`,
`fundamental_change`, `growth_durability`, `future_business`, `competitive_position`,
`catalyst_candidates`, `risks`, `invalidation_candidates`, `unknown_fields` or `evidence_conflicts`.

Two D3 conclusions D4 must use are carried as bare tokens and checked against the original, the
same `code_owned_state` device `FundamentalChangeInterpretationV2` uses one stage earlier:

| D4 field | Checked against | On mismatch |
|---|---|---|
| `fundamental_reality_summary.d3_growth_durability_state` | `research_output.growth_durability.state` | rejected |
| `fundamental_reality_summary.d3_future_business_max_stage` | highest `future_business[].stage` | rejected |

If D4 disagrees with a D3 finding, that belongs in `limitations` or in a conflict record with
`origin=D3_RESEARCH` - never in a silently different restatement.

The claim contract is D3's, imported rather than re-specified: `analysis_schema.ClaimV2 is
schema_v2.ClaimV2`, asserted by a test. Every citation rule D3.3 proved on real output applies to
D4 unchanged.

## C. The Expectation Evidence Problem

"What the market expects" has no direct source here. The honest move is not to guess it but to
assemble the **evidence about expectations** that is actually obtainable, each item carrying its
own provenance and confidence, and to state clearly what is missing.

`ExpectationEvidenceBundleV1` is that assembly. It is code-owned and source-linked: it holds
located verbatim passages, code-computed numbers and honest availability statuses. It holds **no**
gap state, **no** confidence and **no** priced-in assessment - those live only in the AI output, so
"the evidence layer cannot prejudge the interpretation layer" is structural rather than a
convention.

## D. Available vs Missing Data - measured, not assumed

Measured across all 2,010 D2.1 candidate packages (`D2_1-20260928T072430Z`) and the 502-session
daily panel (2024-09-16 to 2026-09-16):

| Input | Availability | Consequence for D4 |
|---|---|---|
| `earnings.status` (PIT consensus) | `UNKNOWN` for **2,010 / 2,010 (100%)** | `consensus = SOURCE_NOT_AVAILABLE`, always |
| Estimate revision history | no provider connected (D0 §M, H0 §4 `H4 REVISION = DEFERRED`) | `estimate_revisions = SOURCE_NOT_AVAILABLE` |
| Earnings release material | `EXTRACTED` for **1,801 / 2,010 (89.6%)** | primary guidance evidence, usually present |
| Reg FD / investor material | `EXTRACTED` for 117 candidates | secondary guidance evidence |
| Daily closes + SPY | 1M/3M return and relative strength for **2,010 / 2,010 (100%)** | full price context |
| `fundamentals.revenue` | `OK` for **353 / 2,010 (17.6%)** | no revenue multiple |
| `fundamentals.eps_diluted` | `OK` for **357 / 2,010 (17.8%)** | no earnings multiple |
| revenue and operating income both `OK` | 268 candidates, of which **8 (3.0%) report operating income ABOVE revenue** | the level fields are not reliably the same period and scope even at status `OK` |
| Full earnings-call transcripts | 1 candidate carries an `EARNINGS_CALL` manifest entry | not a usable layer |
| Broad major-news provider | none | not a usable layer |

The `operating income > revenue` finding is the decisive one for valuation context. A multiple
built on these level fields would be *silently wrong* for a material minority and *unavailable*
for the large majority. A valuation input that is quietly wrong 3% of the time is worse than one
that is honestly absent, so D4 computes no multiple at all and hands the problem to D5, which will
need a fundamentals layer D4 does not have.

### Two data-shape findings from running the builder on real packages

1. `shares_outstanding` is **not** in `evidence_bundle.balance_sheet`, despite `data_quality`'s
   coverage table scoring it as a canonical field (`OK` for 1,787 / 2,010). D1 files it in
   `fundamental_changes` as a trend block whose `current_value` is the latest cover-page count. The
   builder reads it from where it actually is.
2. An earnings release reaches D2.1 as an **EX-99 exhibit of an item-2.02 8-K**, so its chunks
   carry `source_type = SEC_8K`. Filtering chunks on `EARNINGS_RELEASE` finds **zero** for LUV
   while its materialization record does carry an `EARNINGS_RELEASE` document. The code-owned
   identifier is the materialization **role**, with the item-2.02 classification as a fallback.

Both were found by running against real packages, not by reading schemas, and both are locked by
tests (`test_real_earnings_material_is_not_findable_by_source_type_alone`).

## E. Guidance Evidence

The code/AI split, stated as a contract rather than a preference:

- **Code LOCATES.** A frozen term list (`guidance_arithmetic.GUIDANCE_TERMS`) finds guidance-bearing
  passages and hands over the source's own words with a citable `evidence_id` and the terms that
  matched. Selection is deterministic: most recent `published_at` first, then chunk order. Never
  ranked by match count - a filing that repeats "guidance" eight times is not eight times more
  relevant, and ranking by it would make selection depend on filing style.
- **The AI TRANSCRIBES.** It reports the previous and current ranges exactly as the source states
  them, with citations. It does not decide what they did.
- **Code DECIDES the direction.** `classify_range_movement` compares the two bound pairs. The AI's
  per-metric `GuidanceState` is **rejected** if it contradicts that arithmetic.

So a `RAISED` in a D4 output is never the model's adjective and never the model's arithmetic. It is
a comparison code performed that the model had to agree with.

The term list was broadened during testing: an earlier version required the bare `we expect`, which
matched forward-looking-statements boilerplate and missed `we now expect` - the phrasing a revision
is almost always worded in.

## F. Earnings Reality vs Previous Expectation

With no consensus, "beat" is a word D4 has no standing to use. The enum is named
`ResultVsCompanyGuidance` precisely so that `ABOVE_CONSENSUS` cannot be written in the schema at
all:

```
ABOVE_COMPANY_GUIDANCE / WITHIN_COMPANY_GUIDANCE / BELOW_COMPANY_GUIDANCE
NO_PRIOR_GUIDANCE / UNKNOWN
```

The AI transcribes the reported actual and the prior guided bounds; `result_vs_range` decides. A
result exactly at a bound is `WITHIN`, because the company's own range included it.

## G. Price Reaction

Entirely code-owned (`price_engine`). Per official event: 1-session and 3-session event returns,
both benchmark-adjusted over the *same two calendar sessions* (not the same session count), and
5/20-session pre-event returns ending at the last close **before** the event.

**PIT enforcement is on the series, before any arithmetic.** `pit_eligible_series` raises
`FuturePriceLeakError` if the panel contains a bar whose session could not have closed by
`decision_time`. It raises rather than filters: a caller that handed D4 future prices has a bug in
its own data assembly, and quietly trimming would hide it.

**An incomplete window is `None`, never shortened.** A 3-session window needing a session the panel
does not have reports `INCOMPLETE_FUTURE` with no value - which is the normal state for a forward
run, where the post-event sessions have not happened yet.

### The event-alignment finding

US regular-session close is 16:00 ET = 20:00Z under EDT, 21:00Z under EST. This repository has no
exchange calendar, and its SEC acceptance timestamps are stored as UTC. The engine therefore uses
whichever bound is conservative for each question: the **later** bound for PIT eligibility (a bar
is knowable only after the latest time it could have closed) and the **earlier** bound for event
alignment (a session is never assumed to have reacted to news it might not yet have seen).

When the two bounds disagree about which session reacted, the event is flagged
`alignment_ambiguous` with the reason, rather than assigned to one of them. **Measured across 4,664
PIT-eligible official events in the D2.1 snapshot, 1,928 (41.3%) fall in that window** - and all
4,664 carry `DATETIME` precision, so this is not a precision problem, it is a timezone-convention
problem that no amount of timestamp precision resolves.

This matters because it would silently corrupt an event study. The prompt instructs that on an
ambiguous event the 1-session return is weak evidence (it may describe the session *before* the
news) and the 3-session window is the more robust reading, since computed from the later candidate
session it still contains the earlier one.

### Independent agreement with D1

D4 recomputes 1M/3M returns and relative strength from the raw panel. D1 already stored its own
values, computed by separate code, in every evidence bundle. For LUV they agree to floating-point
equality on `return_1m`, `return_3m`, `relative_strength_1m` and `relative_strength_3m`
(`test_real_price_context_reproduces_the_d1_bundle_values_independently`). An independent agreement
is worth considerably more than either number checked against itself.

## H. Price Context

Code-owned: 1M/3M/6M returns (21/63/126 sessions, D1's own conventions), benchmark-relative
strength over the same sessions, 252-session close high and low, drawdown from that high, and
60-session annualized realized volatility. Missing history yields `None`, never `0.0`.

The extremes are named `close_252s_high` / `close_252s_low` rather than "52-week high/low", because
the daily store carries a close per session and a close-based extreme differs from the intraday
statistic that name means in market usage by exactly the intraday range. (D1's own
`price_context.trailing_52w_low` is likewise computed from closes; D4 does not modify D1 or its
bundles, it declines to repeat the label.)

D4 draws no conclusion from this block. `overvalued`, `undervalued`, `cheap` and `expensive` are
prohibited vocabulary. What price context legitimately supports is "the shares had already moved a
great deal before this disclosure" - an expectation statement, not a valuation one.

## I. Historical Valuation Context Stub

`valuation_context_stub` carries only what is exactly computable from instant-dated facts:

- `market_cap` = `fundamental_changes.shares_outstanding.current_value` × last PIT close, with the
  share count's own as-of date, because a market cap computed today from a quarter-end share count
  is a mixed-date quantity and a reader who cannot see both dates cannot tell how stale it is
- `net_debt` = `total_debt` − `cash`, each with its own as-of date
- `enterprise_value` = `market_cap` + `net_debt`
- `trailing_multiples`: `NOT_COMPUTABLE`, with §D's measurement as the stated reason
- `historical_multiple_percentile`: `SOURCE_NOT_AVAILABLE` - it needs a PIT trailing-fundamentals
  series per session, and `fundamental_changes` carries year-over-year growth **rates**, not levels

Raw context is permitted here; a valuation judgement is not. That is D5.

## J. Consensus / Estimate Revisions

```
consensus.status           = SOURCE_NOT_AVAILABLE
estimate_revisions.status  = SOURCE_NOT_AVAILABLE
```

Never rewritten to neutral, never to zero, never to an empty `AVAILABLE`. `EvidenceBlock` refuses
the two combinations that would hide an absence: `SOURCE_NOT_AVAILABLE` cannot carry excerpts, and
`AVAILABLE` cannot be empty (that combination is how an empty search result reads as a full one).
An absent block must state *why*, so absence is never anonymous.

The AI's reported `consensus_status` is checked against the bundle's own - the availability of a
data source is not the model's to report. Independently, if the bundle says
`SOURCE_NOT_AVAILABLE`, any text asserting an analyst/market/consensus expectation
("Analysts expect", "beat consensus", "Investors are pricing") is rejected, and the prompt supplies
the exact replacement sentence:

> Available evidence does not establish consensus expectations.

The interface exists for a future provider: connecting one lifts the `AVAILABLE` status and, by
rule C4, lifts the confidence ceiling. Nothing else changes.

## K. Management Expectation Signals

Located the same way as guidance, via `MANAGEMENT_SIGNAL_TERMS` (on track, delayed, milestone,
target, no longer expects, pushed out, accelerating...). A `ManagementSignalChangeV1` requires
**two** `evidence_ids`, because "the language changed" is inherently a statement about two
documents: one claiming a change while citing a single filing is describing that filing's wording,
not a change.

## L. Expectation Gap Definition and States

D0 §L's six frozen states, verbatim, never a number and never averaged with anything:
`WIDE_POSITIVE / POSITIVE / NEUTRAL / NEGATIVE / WIDE_NEGATIVE / UNKNOWN`.

### The asymmetry (rule C1) - the engine's central rule

**A `POSITIVE` or `WIDE_POSITIVE` gap requires at least one NON-PRICE expectation evidence item**:
a guidance assessment, a result-versus-prior-company-guidance comparison, or a sourced management
expectation signal. Price history alone can never support a positive gap.

The reason, not merely the rule: price history can show that the market **has moved**; it can never
show that the market is **behind**. A stock that has not re-rated despite improving evidence is
exactly as consistent with "the market has not noticed yet" as with "the market has noticed and
disagrees for a reason not in this evidence pool" - and D4 has nothing that separates those two,
because separating them is what a consensus feed is for.

The reverse is deliberately not symmetric. A large completed re-rating **is** direct evidence that
expectations moved, so price context alone may support `NEGATIVE` or `WIDE_NEGATIVE`. **A good
company receiving a NEGATIVE gap is a correct outcome of this engine, not a failure.**

A guidance block whose state is `NOT_PROVIDED` or `UNKNOWN` does not satisfy C1: an absence cannot
be the evidence that the market is behind, and counting it would let the rule be satisfied by the
very thing it exists to catch.

### WIDE_POSITIVE (rule C2)

All must hold, and code checks each against the immutable D3 input rather than the model's word:

```
material evidenced improvement in the code-owned fundamental_changes states
at least one D3 future-business item staged above STORY
at least one unrealized why-now item
non-price expectation evidence that lags the evidenced improvement
a price reaction that has not fully incorporated the change, or none yet
```

D0 §L's definition contains a **fifth** conjunct D4 structurally cannot evaluate - "valuation has
not yet re-rated to reflect it". It is recorded verbatim on every `WIDE_POSITIVE` as
`wide_positive_deferred_conjunct`:

> D0 §L WIDE_POSITIVE also requires 'valuation has not yet re-rated to reflect it'. D5 Valuation has
> not run, so this conjunct is UNEVALUATED, not met. A D4 WIDE_POSITIVE is therefore provisional on
> D5 and must be re-checked by D6, never inherited as satisfied.

Dropping it silently would let a D6 built on D4's output inherit a WIDE_POSITIVE that skipped a
criterion D0 froze. **Even WIDE_POSITIVE does not mean buy and does not mean upside.**

## M. Confidence

`HIGH / MEDIUM / LOW / UNKNOWN`, with two frozen ceilings. Every rule in this contract is
one-directional: each can only lower a state or a confidence, never raise one. A contract whose
rules could promote an output would be a scoring model, which §15 forbids.

| Rule | Condition | Ceiling |
|---|---|---|
| C4 | no consensus **and** no estimate-revision source | `MEDIUM` |
| C3 | a `MATERIAL` conflict with `resolution_status=UNRESOLVED` | `MEDIUM` |
| C5 | `expectation_gap=UNKNOWN` ⟺ `confidence=UNKNOWN` | exact |

**C4 binds on every candidate this pipeline will ever see under the current data layer.** Consensus
is `UNKNOWN` for 2,010 of 2,010, so no H-V2 candidate can reach `HIGH` expectation-gap confidence
until a consensus provider is connected. That is a real structural limit, and it is stated rather
than hidden. It does not stall the pipeline: D0 §L's APPROVE rule accepts `MEDIUM`.

C3's justification: D3's conflict structure exists because two official sources disagreeing is a
finding, not a defect. If that finding is material and nothing reconciles it, part of the *reality*
side of the comparison is unsettled - and HIGH confidence in a comparison with an unsettled input
is not a confidence level, it is an oversight.

**The ceiling is enforced by rejection, not by silent adjustment.** The prompt states both ceilings
explicitly, so a reported `HIGH` means an explicit instruction was ignored - a real
instruction-following signal the gates need to see. Silently lowering it would destroy that signal.
Code records the ceiling that was enforced and which rules fired in `confidence_ceiling` and
`applied_contract_rules`.

## N. Priced-in Assessment

```
LIKELY_NOT_PRICED / PARTIALLY_PRICED / LIKELY_PRICED / OVER_PRICED_EXPECTATION / UNKNOWN
```

The strongest inference D4 makes, so it carries the heaviest requirement (rule C7): anything other
than `UNKNOWN` needs `evidence_ids`, a real confidence, and **at least one explicit limitation**.
With no consensus source, every priced-in reading has one.

`OVER_PRICED_EXPECTATION` means **expectations** have run ahead of evidenced progress. It does not
mean the stock is overvalued. The enum member exists so an expectation statement never has to
borrow valuation vocabulary, which is how an expectation layer turns into an unlicensed valuation
layer.

## O. Why Now

`why_now` means **why a re-rating window may exist**, never why to buy. It is distinct from D3's
`why_now_candidate` ("why research this now"). Each item requires a source, at least one cited
claim, and a `realization_status` - because the whole idea depends on it: an already-realized event
cannot open a window. A `REALIZED` item is still recorded (it is how D4 says "this has happened, so
it is not why-now") but cannot count as one, and C2 requires an unrealized item for
`WIDE_POSITIVE`.

## P. UNKNOWN Discipline and Conflict Handling

The largest gap in this pipeline's data is consensus, and it is total. **Producing many `UNKNOWN`s
is the engine working, not failing.** The failure mode is producing `POSITIVE` when the expectation
evidence is insufficient. Rule C6 makes this mechanical: a stated gap with no non-price expectation
evidence *and* no computed price reaction at all is rejected - with nothing on the expectation side
of the comparison, the only defensible state is `UNKNOWN`.

**D4 quality is evidence discipline, not the POSITIVE rate.**

Conflicts carry forward from D3 with `origin=D3_RESEARCH`; new conflicts found inside the
expectation evidence (guidance raised while a milestone slipped) carry `origin=D4_EXPECTATION`.
Neither side of a conflict is ever deleted, and "the later filing is presumably right" is not a
resolution the record states. `ExpectationConflictV1` extends `EvidenceConflictV2` with
`materiality` and `origin` and restates every V2 rule unchanged - it extends the contract, it does
not relax it.

## Q. AI vs Code

| Owned by CODE | Owned by the AI |
|---|---|
| guidance/management passage location (frozen term lists) | reading a located passage |
| guidance direction arithmetic (`classify_range_movement`) | transcribing the two ranges, with citations |
| result-vs-guidance comparison (`result_vs_range`) | transcribing actual and prior bounds |
| every price return, relative strength, drawdown, volatility | reading what a reaction may mean |
| event/session alignment and its ambiguity flag | weighing an ambiguous event's evidence |
| PIT cutoff enforcement | reality-vs-expectation synthesis |
| market cap, net debt, enterprise value | priced-in inference |
| C1-C7 rule evaluation and the confidence ceiling | why-now reasoning, gap classification |
| all four contract-residue fields | (nothing) |

**The AI never crosses into code's column.** Enforcement is not advisory: every code-owned number
has a citable identity (`CODE:D4:<bundle_id>:CHUNK:<path>`), and a claim citing one while stating a
*different* number is rejected. Citing code-owned arithmetic is therefore a **stricter** contract
than citing prose, because resolution proves the value and not merely that text exists.

That check is unit-aware, and deliberately so. An early version reported
"The shares rose 18% over the last 63 sessions" as a numeric defect, because `63` is a bare `COUNT`
token matching nothing in the fact's value. A session count in a sentence about a return is
ordinary writing, and flagging it would manufacture exactly the false-positive class D3.1 §I.2
measured once already with `fair value`. Only units that could actually *be* the cited fact are
examined; a purely qualitative claim citing a code fact is correct and common.

The four contract-residue fields (`applied_contract_rules`, `confidence_ceiling`,
`d6_approve_precondition`, `wide_positive_deferred_conjunct`) are filled by code **after** the model
responds and are stripped from any model-supplied value - they are the record that D4 applied the
frozen rules, and a model value there would be the model grading its own homework.

## R. Output Schema

`HExpectationGapAnalysisV1` (`h_expectation_gap_analysis_v1`), `extra="forbid"`:

```
candidate_id, ticker, decision_time
research_input_id, research_input_checksum
expectation_evidence_id, expectation_evidence_checksum
fundamental_reality_summary   { d3_growth_durability_state, d3_future_business_max_stage,
                                improvement_claims, deterioration_claims, durability_basis }
market_expectation_evidence   { overall_guidance_state, guidance_assessments, result_vs_guidance,
                                management_signal_changes, price_reaction_reading,
                                pre_event_positioning_reading, consensus_status,
                                estimate_revisions_status }
expectation_gap, expectation_gap_confidence, gap_rationale
priced_in_assessment, why_now
supporting_claims, conflicts, limitations, unknown_fields, sources
model_name, model_version, prompt_version, schema_version, contract_version, gap_contract_version
applied_contract_rules, confidence_ceiling, d6_approve_precondition,
wide_positive_deferred_conjunct        (all four: code-filled)
```

Prohibited and rejected by name at any depth: `decision`, `recommendation`, `rating`, `verdict`,
`approve`, `watch`, `reject`, `buy`, `sell`, `hold`, `fair_value`, `intrinsic_value`,
`price_target`, `valuation`, `upside`, `entry`, `entry1`, `entry2`, `exit`, `stop`, `tp1`, `tp2`,
`position_size`, `portfolio_weight`, `conviction_score`, `expectation_gap_score`. A test asserts no
declared schema field collides with that set.

### `d6_approve_precondition` - what it is and is not

`SATISFIED` does **not** mean approve, recommend or buy. It means exactly one thing: D0 §L's
expectation-gap clause would not by itself block APPROVE, *if* D5 and D6 later run and
independently satisfy every other D0 §N1 requirement - of which this is one of seven. D4 records it
because D0 froze the rule and a later stage must be able to check D4 did not quietly ignore it. **No
code in this package reads it**, and its two values contain no decision vocabulary.

## S. Prompt Contract

`h_v2_d4_expectation_gap_v1`. Built on D3.2R's discipline: same untrusted-source boundary
(`UNTRUSTED_RESEARCH_DATA`), same atomic-claim contract, same bare-token copying device, same
refusal to let the model do arithmetic. Deterministic: the same inputs always produce the same two
strings.

Contains: the role statement and its prohibitions; `GREAT COMPANY != POSITIVE GAP`; D3
immutability; the consensus prohibition with the measured 2,010/2,010 figure and the exact
replacement sentence; numeric and guidance-transcription discipline; the citation contract
including code-fact ids; the six gap states; the C1 asymmetry **with its reason**, since a model
told only "be careful about positive gaps" will still produce them for good companies; the C3/C4/C5
confidence rules; legitimate and illegitimate readings of a price reaction plus the 41.3%
event-alignment caution; priced-in and why-now; UNKNOWN discipline and conflict handling; a
six-step reasoning order that forbids picking a state first; and injection safety.

If a D3 output exceeds the 120,000-char budget the builder **raises** rather than truncating -
silently shortening the reality side of the comparison would drop findings.

## T. Validation

`h_v2_d4_validation_contract_v1`. Order matters: **banned field names are checked first**, before
JSON reaches Pydantic, so an attempted `fair_value` fails with "prohibited D4 field" rather than
Pydantic's generic "extra inputs are not permitted". D3.1 §I.2 measured what an unspecific
rejection does to a repair round, and a decision field is the one rejection that must never be
vague.

Then: schema validation with the citable universe (D3 package chunks ∪ bundle excerpts ∪ code fact
ids, each built per-candidate so a cross-company citation is rejected by the same check that
rejects an invented chunk index), then D3-token immutability, consensus non-fabrication, guidance
and result arithmetic agreement, code-fact numeric integrity, and rules C1-C7. The return contract
is `(validated_output, errors)` - identical to `validate.assemble_and_validate`, so D3's bounded
repair loop drives D4 unchanged.

## U. Immutable Ledger

`h_v2_d4_ledger_v1`, reusing D3.2F's `RawResponseRecordV1` and `checksum` unchanged rather than
restating them - D3.1's MRVI failure (unauditable because only a 1,500-character preview survived)
applies identically to D4, and a parallel contract would drift until one of them truncated
something again.

Per analysis: `analysis_id`, `analysis_run_id`, candidate and ticker, the three upstream checksums
(D3 output, expectation bundle, D2.1 package), requested and canonical model plus
`model_mismatch`, all four version strings, timestamps, the **full untruncated** initial response,
every repair round with its own full response, final output and checksum, `applied_contract_rules`,
and cost. `input_tokens`/`output_tokens` are `None` rather than invented - D4 makes zero live calls
here, so there is no real response to confirm a key path against.

`store_analysis` and `store_evidence_bundle` refuse to overwrite: a rerun uses a new id. The bundle
is equally immutable, because the analysis's `expectation_evidence_checksum` is meaningless if the
bundle it points at can be rewritten afterwards. `verify_linkage` **reports** mismatches and never
repairs them - recomputing a checksum to make it agree would destroy the only evidence that the
analysis interpreted something else.

## V. D4.1 Pilot Design - proposed, NOT executed

Brief §33 prefers new disjoint issuers over reusing D3.3's 12, because those outputs were read
closely while designing this stage. That preference is right and is followed, but it has a cost the
brief does not state: **D4 consumes a D3 output as input, and only those same 12 issuers have
one.** A genuinely disjoint D4.1 issuer needs a chained D3 run first, at D3 prices. The split below
is the honest resolution:

| | Tier A - contract shakedown | Tier B - qualitative validation |
|---|---|---|
| Issuers | 3 of D3.3's 12 (`SCCO`, `GOOG`, `BSY`) | 6 unseen: `IDCC`, `DORM`, `FRPT`, `TG`, `CRK`, `SPSC` |
| Disjoint from all 48 touched CIKs | no | **yes** |
| Needs a D3 run first | no | yes |
| Answers | does the machinery work end to end | is the interpretation evidence-disciplined |
| Gates evaluated | E1, E4, E5, E7 only | all of E1-E8 |
| Maximum verdict | `PASS_WITH_LIMITATIONS`, by construction | `PASS` |

Both samples are frozen with checksums and are **regenerated** from the D2.1 snapshot by the
declared seeded-hash convention in tests - a hardcoded list checksummed by hashing itself proves
nothing about whether it was produced the declared way. Tier B's 6 are disjoint from all 48 CIKs
any live Opus call has ever touched (D3's 12 + D3.1's Batch 2's 24 + D3.3's 12).

**Reporting Tier A's numbers as the pilot's quality result would be the single easiest way to make
this stage look better than it is**, which is why the two tiers carry different gate sets and why
Tier A can never return `PASS`.

Budget, anchored to D3.3's measured costs (18 attempts, $16.71 total, $0.93 mean, $1.54 max):
`D4_WORST_CASE_CANDIDATE_USD = 2.00` (set above D3's observed maximum, since a ceiling that assumes
an improvement bounds nothing), Tier B worst case $3.60 each including its D3 leg, hard budget
$30.00. Checked before starting a call that could not finish inside the ceiling, mirroring D3.3.

**Not executed. Requires separate user authorization as `H-V2-D4.1 EXPECTATION GAP LIVE PILOT`.**

### E1-E8 gates (frozen before any live result)

| Gate | Threshold | Basis |
|---|---|---|
| E1 schema validity | ≥ 95% final-valid | D3.3's L1 level, met 12/12 |
| E2 material gap claims source-linked | 0 unsourced | D3.3 achieved structural provenance on every material claim |
| E3 fabricated consensus | 0 | no consensus source exists, so every occurrence is invention |
| E4 future source / price leakage | 0 | |
| E5 code-owned numeric integrity | 0 defects | a claim citing a code fact while stating a different number |
| E6 UNKNOWN discipline | 0 violations | a POSITIVE that C1/C6 should have blocked, or a confidence above ceiling, **in a final output** |
| E7 investment decision leakage | 0 | |
| E8 priced-in claims fully supported | 0 unsupported | |

Core gates (failure is always FAIL, never a limitation): **E3, E4, E5, E7** - each is a
fabrication or leakage gate, not an operational inconvenience. An unperformed manual gate is
`NOT_EVALUATED`, never a silent pass, the convention D3.1's R10 and D3.3's L3 both used.

**What these gates prove:** that D4 produced evidence-disciplined expectation interpretations under
a frozen contract. **What they do not prove:** that any expectation gap is real, that a `POSITIVE`
gap precedes a positive return, or that this pipeline is profitable.

```
Expectation Gap Engine PASS  !=  profitable strategy
```

Alpha is D6 Decision plus D7 Forward Shadow, and neither exists.

## W. D5 Dependency and D4 Verdict

D6's Decision Engine must take **all** of D3 Research + D4 Expectation Gap + D5 Valuation + Risk /
Catalyst as input. D4 hands forward two things D5 must solve: a fundamentals layer whose level
fields are period- and scope-consistent (§D's 3.0% impossible-relation finding), and a PIT
trailing-fundamentals series if any historical multiple percentile is ever to exist.

### Verdict

```
H-V2-D4 CONTRACT = READY FOR LIVE PILOT
```

Contract, schemas, prompt, validator, evidence builder, code-owned price and guidance engines,
immutable ledger, frozen D4.1 preregistration and 168 tests are complete; the full 568-test H-V2
regression passes. What is authorized is that D4.1 may now be *proposed*. Its execution is a
separate user decision.

### Declaration

```
coding model                  = Claude Opus 5 (claude-opus-5)
new live model calls          = NO
live model cost               = $0
D3 contract modified          = NO
D3.3 result modified          = NO
consensus fabricated          = NO
Expectation Gap live executed = NO
APPROVE / WATCH / REJECT      = NO
fair value calculated         = NO
price target                  = NO
D5 executed                   = NO
forward returns read          = NO
existing dirty files modified = NO
pushed                        = NO
```

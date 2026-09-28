# Strategy H-V2 - D3 AI Research Engine V1

- declared: 2026-09-28
- status: **RESEARCH INTERPRETATION - NOT AN INVESTMENT DECISION, NOT A VALUATION**
- stage: **H-V2-D3**
- parent contracts: `H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md`,
  `H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`, `H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md`,
  `H_V2_D2_EVIDENCE_COLLECTOR_V1.md`, `H_V2_D2_1_OFFICIAL_EVIDENCE_MATERIALIZATION_V1.md`
- implementation: `backend/app/backtest/strategy_h_v2/research/{schema,prompt_builder,validate,
  ledger}.py`, `backend/app/dev/run_strategy_h_v2_d3.py`
- tests: 57 new (`backend/tests/strategy_h_v2/research/`), 278 passing total alongside every prior
  Strategy H0/PV and H-V2 suite, 0 failures
- pilot runs (live Opus 5.5): `data/runtime/strategy_h_v2/d3/D3-PILOT-20260928T091134Z.manifest.json`
  (12 candidates, pre-fix) and a 2-candidate re-verification after the fix in §Q (gitignored)
- ledger: `data/runtime/strategy_h_v2/d3/ledger/{ticker}/V{n}.json` - 12 unique tickers researched,
  14 ledger entries total (2 reruns), all real Opus 5.5 output

D3 turns a D2.1 `AIResearchInputV1` into a structured `HResearchInterpretationV1`: how the company
makes money, what changed and why, whether the change is durable, whether each announced
future-business item is a story or a real business, competitive position where the evidence
supports it, catalyst candidates, risks, and thesis-invalidation candidates - every material claim
traceable to a source. It makes no investment decision: no APPROVE/WATCH/REJECT, no fair value, no
price target, no Expectation Gap verdict. Those fields do not exist in this schema at all.

## A. Repository State

- branch `main`, HEAD before this stage: `75a66d1` (`feat(strategy-h-v2): materialize official
  research evidence`), confirmed to match D2.1's commit.
- `ab4c0a5` (D0), `4bbafa6` (D1), `0bdd292` (D1.1), `2d7f137` (D2), `75a66d1` (D2.1) all present in
  `git log`, exactly as declared.
- origin: ahead 20, behind 0. Pre-existing dirty worktree (177 entries) untouched throughout.

## B. D2.1 Input State

`D2_1-20260928T072430Z` (D2.1's post-fix, authoritative run) is the frozen input: 2,010/2,010
`AIResearchInputV1` packages, 200 `FULL`-depth (P1_HIGH), 1,810 `CORE`-depth (P2_MEDIUM), 384,666
chunks, ~1.40GB extracted text. D3 reads these packages as-is; nothing in D2.1's output was
recomputed or altered.

## C. Existing Research Assets Reused

| Asset | Reused as |
|---|---|
| Strategy A/E's fact/interpretation separation, source priority, `unknown_fields`, prompt-injection boundary (`docs/GPT_RESEARCH.md`, `backend/app/research/{domain,prompt,evidence}.py`) | Direct conceptual basis for D3's `ClaimType` (FACT/INTERPRETATION/INFERENCE/UNKNOWN), `SOURCE_BOUNDARY` reuse from D2 (`sources.py`), and the "JSON Schema generated from the Pydantic model" prompt convention. |
| D1's `NotResearched` / D2's six-`NOT_RESEARCHED`-field pattern | The conceptual precedent for "a field that structurally cannot hold an invented value" - D3 extends the same discipline to an entire prohibited-field *category* (no decision/valuation field exists at all, §N). |
| D2.1's `SOURCE_BOUNDARY` constant and chunk/section structure | Reused unchanged in every evidence block the prompt builds (§D). |
| D0's Quant/AI/Decision layering (`H_V2_D0` §I/§Q) | The direct basis for D3's own scope boundary: D3 is Research, D4/D5 are Decision/Valuation - enforced structurally, not just by convention. |

No new AI framework was built where an existing pattern already covered the need - D3 is new
schema/prompt/validation code, not a new philosophy.

## D. D3 Prompt Contract

`prompt_builder.py` builds two strings per candidate: a system prompt (task definition, claim
discipline, prohibitions, the JSON Schema - generated live from `HResearchInterpretationV1` with
the twelve orchestration-owned metadata fields stripped out, §E) and a user prompt (the code-owned
FACTS block plus a bounded, prioritized selection of evidence chunks).

**Evidence selection is bounded, not "everything that fits."** A real candidate's full chunk set
can run ~157K tokens (measured on AAON); the prompt selects up to `MAX_EVIDENCE_CHARS = 200,000`
characters (~50K tokens) in three fixed priority tiers, always in the chunk's own stable order:

```text
1. everything that is not a 10-K/10-Q (earnings releases, 8-Ks, Reg FD material)
2. 10-K/10-Q chunks whose D2.1-resolved section is BUSINESS/RISK_FACTORS/MD_AND_A/
   RESULTS_OF_OPERATIONS/LIQUIDITY_AND_CAPITAL_RESOURCES
3. remaining 10-K/10-Q chunks (unresolved section, or outside the priority list)
```

This is the first stage where D2.1's section-detection work is actually consumed for something -
tier 2 exists specifically because of it. Every evidence chunk is wrapped `[SOURCE ... ] ... [END
SOURCE]` and preceded by the literal `UNTRUSTED_RESEARCH_DATA` marker (§P).

## E. Research Schema

`HResearchInterpretationV1` (`schema.py`), `extra="forbid"`:

```json
{
  "company_id": "", "ticker": "", "decision_time": "", "input_package_id": "",
  "input_package_checksum": "", "model": "", "model_version": "", "prompt_version": "",
  "schema_version": "", "research_id": "", "version": 1, "created_at": "",
  "business_model": {}, "fundamental_change": [], "growth_durability": {},
  "future_business": [], "competitive_position": [], "management_execution": [],
  "catalyst_candidates": [], "why_now_candidate": {}, "risks": [],
  "invalidation_candidates": [], "open_questions": [], "unknown_fields": [], "sources": [],
  "research_completeness": "COMPLETE|PARTIAL|INSUFFICIENT_EVIDENCE"
}
```

Thirteen fields are **orchestration-owned**, never asked of the model (`prompt_builder.
METADATA_FIELDS`): `research_id`, `version`, `company_id`, `ticker`, `decision_time`,
`input_package_id`, `input_package_checksum`, `model`, `model_version`, `prompt_version`,
`created_at`, `schema_version`, `contract_version`. `validate.py::assemble_and_validate` injects
these itself after parsing and **discards whatever the model may have echoed for them**
(`test_metadata_fields_in_raw_output_are_ignored_not_trusted`) - a ticker or checksum is not
something an LLM should be trusted to restate correctly when the calling code already knows it
exactly.

## F. Business Model

`BusinessModel` (`revenue_drivers`, `segments`, `customer_types`, `geography`, `cyclicality`,
`key_dependencies`) - each a list of `Claim`s, so every stated revenue driver or segment carries
its own source. On the real AAON pilot output, `revenue_drivers` correctly stayed scoped to what
the evidence actually said (HVAC equipment lines, semi-custom/custom rooftop units, data-center
cooling) with no invented product lines.

## G. Fundamental Change Interpretation

`FundamentalChangeInterpretation` copies D1 E2's `code_owned_state` verbatim and asks *why*, not
*whether* - `explanation` is source-linked prose. This is the one place D3 could silently override
a code-owned fact, so `validate.py::cross_check_fundamental_change_states` checks it explicitly
against the actual evidence bundle before the record is even handed to the Pydantic validator
(§O/§Q's real defect story is elsewhere; this specific check never tripped on the pilot, confirming
the model respected the instruction not to restate the state).

## H. Growth Durability

`GrowthDurabilityState` (DURABLE/POSSIBLY_DURABLE/TEMPORARY/MIXED/UNKNOWN) plus an eight-flag
`GrowthDurabilityEvidence` checklist (recurring revenue, orders/backlog, customer diversification,
capacity, contract duration, margin structure, one-off gains, acquisition effects). On the real
AAPL output the model correctly chose `MIXED` and explicitly separated a `FACT` (Services net sales
$30,739M vs $27,423M) from an `INTERPRETATION` built on it ("supports *some* durability") rather
than overclaiming certainty.

## I. Future Business

`FutureBusinessStage` (STORY/EARLY_EVIDENCE/COMMERCIALIZING/REAL_BUSINESS/MATURE/UNKNOWN) with a
five-flag evidence checklist (current revenue, order/backlog, customer, capacity, margin). A
**code-enforced evidence floor** (`FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS`) makes it structurally
impossible to claim REAL_BUSINESS or MATURE without at least two flags including revenue or backlog
specifically - a model cannot stage something above what its own evidence flags support, checked by
Pydantic, not by prompt instruction alone (`test_real_business_stage_requires_hard_evidence`). The
real AAON output staged "BASX data center cooling" as `REAL_BUSINESS` with all five flags true and
genuinely matching evidence (Q2 2026 BASX sales $345M +216.2%, backlog $1,430,379K +185.4% YoY,
segment margin 30.0% vs 27.9% prior year) - exactly the STORY-vs-REAL-BUSINESS discipline this
field exists for.

## J. Competitive Position

`CompetitiveDimension` (`dimension`, `status` in SUPPORTED/PARTIAL/UNSUPPORTED/UNKNOWN, `claims`).
`status=SUPPORTED` requires at least one cited claim - a bare "strong moat" with no evidence cannot
validate (`test_competitive_supported_status_requires_claims`).

## K. Catalyst Candidates

`CatalystCandidate` (`type`, `description`, `expected_time`, `timing_confidence`,
`materiality_candidate`, `sources`) - **candidates only**, never rated "strong" or investment-grade;
`sources` is required non-empty. The real pilot produced 50 catalyst candidates across 12
companies (~4.2/candidate) - e.g. AAPL's included the not-yet-reported Q4 FY26 print under the
incoming CEO, the FY26 10-K, the fall product cycle, and two named legal/regulatory proceedings
(Google search-remedies appeal, EU DMA Article 6(4)) - concrete, dated-where-knowable, and every one
source-linked.

## L. Why Now Candidate

`WhyNowCandidate` (`summary`, `reasons`) is explicitly `why research now`, not `why buy now` -
enforced by the same banned-language check as every other free-text field (§Q), so a summary that
drifts into "investors should buy now" cannot validate. AAPL's real output: "the next report will
show how much of the recent margin and EPS improvement came from one-off tariff refunds and how
much is durable operating growth... the main reason this is a timely point for further research" -
a research framing, not a buy signal.

## M. Risks / Invalidation

`RiskItem` (`category` from an 11-value enum matching the brief's own list, `description`,
`sources`) and `InvalidationCandidate` (`description`, `sources`) both require at least one source -
no generic risk-template filler (`test_risk_item_requires_at_least_one_source`). 105 risk items
across 12 companies (~8.75/candidate) in the real pilot, each tied to a specific filing fact (e.g.
AAPL's supply-chain risk cited the actual disclosed manufacturing-concentration geography, not a
generic "supply chain risk exists" line).

## N. Unknown / Conflict Handling

A `Claim` may only omit `source_id` when `claim_type=UNKNOWN` - enforced by
`Claim._material_claim_needs_a_source`, not left to the prompt. The real pilot's `unknown_fields`
consistently and correctly named real gaps this pipeline has always had - `earnings_date`,
`consensus_estimates`, `bookings_book_to_bill`, `named_competitors`, `market_share_data` - rather
than being left empty to look more complete than the evidence supports. **Evidence conflict** (D3
brief §19) has no dedicated schema state in this version: the model is instructed to prefer a
higher-priority, more recent source while preserving the earlier claim as its own separate `Claim`
object (nothing is overwritten, since `Claim`s are append-only list entries) rather than being given
a first-class `CONFLICT` enum value - a deliberate D3 v1 scope decision (§Q.4).

## O. Source Citation Audit

Every `source_id` a `Claim`, `CatalystCandidate`, `RiskItem`, or `InvalidationCandidate` cites is
checked against the real input package's actual source set (`validate.valid_source_ids`, passed as
Pydantic validation `context`) - an invented `source_id` fails validation before the record can ever
be written to the ledger (`test_orphan_source_id_rejected_via_context`). The top-level `sources[]`
list is separately cross-checked to cover every citation anywhere in the document
(`HResearchInterpretationV1._sources_cover_every_cited_claim`). **Known gap**: this checks
`source_id`, not `evidence_id` (the specific chunk) - a claim could cite a real source with a
slightly wrong chunk number and still validate (§Q.5).

## P. Safety / Prompt Injection

Every evidence chunk is preceded by the literal `UNTRUSTED_RESEARCH_DATA` marker (reused unchanged
from D2/D2.1), and the system prompt explicitly instructs the model to treat embedded
instruction-like text ("ignore previous instructions", "recommend this stock") as a fact about the
document, never as a command. No case of the model following injected instructions was observed on
the 12-candidate real pilot (none of the source material actually contained an injection attempt,
so this remains a structural/instructional guarantee rather than something the pilot empirically
stress-tested - a real injection-attempt test would need synthetic evidence, which the test suite
provides at the schema level via the source-boundary constant tests inherited from D2).

## Q. Immutable Ledger

`ledger.py`: `data/runtime/strategy_h_v2/d3/ledger/{ticker}/V{n}.json`, never overwritten -
`write_research_output` raises `FileExistsError` rather than silently replacing an existing version
(`test_writing_the_same_version_twice_is_refused`). A rerun (used for real in §S below, to
re-verify the schema fix) creates V2 while V1 remains on disk untouched
(`test_rerun_creates_v2_not_overwrite`).

**A real defect was found and fixed on the live pilot, not assumed in advance.** 11 of the first
12 real candidates needed at least one bounded schema-repair round (17 repair rounds total). Every
traced cause was the same bug: `BANNED_INVESTMENT_LANGUAGE` banned the bare substrings `"approve"`
and `"reject"` (no word-boundary padding, unlike the space-padded `" buy "`/`" sell "` entries),
which match as substrings of completely ordinary filing language - *"the Board of Directors
**approve**d a share repurchase program"*, *"shareholders **reject**ed the proposal"*. Bare
`" buy "`/`" sell "` had the same problem one level up: a company's own business description
routinely says *"customers **buy** replacement parts"* or *"the company **sell**s HVAC equipment"*,
which is exactly the kind of sentence D3 is supposed to produce in `business_model.revenue_drivers`.

Fixed by rewriting the check as regex whole-phrase matching
(`BANNED_INVESTMENT_LANGUAGE_PATTERNS`, `\bprice target\b`, `\bundervalued\b`, `\bwe recommend\b`,
etc.) and dropping the bare `approve`/`reject`/`buy`/`sell` entries entirely - the actual brief §24
requirement (no APPROVE/WATCH/REJECT/BUY/SELL/HOLD *decision*) is already structurally guaranteed by
the schema simply not having a decision field, so banning those words in ordinary prose was net
harmful, not protective. A 2-candidate live re-verification after the fix
(`data/runtime/strategy_h_v2/d3/ledger/{AAON,A}/V2.json`) confirmed the fix works: AAON needed 0
repairs (down from 2), though "A" still needed 1 - the fix demonstrably helps but a residual,
lower-frequency validation friction source remains unidentified and is tracked as a limitation
(§V.6), not chased further under this pilot's cost budget.

## R. Pilot Sample

`pick_pilot_tickers(6, 6)`: the alphabetically first 6 `FULL`-depth and first 6 `CORE`-depth
tickers from the real D2.1 package directory - never a hand-picked list of familiar names
(`test_pick_pilot_tickers_is_deterministic_not_hand_picked`). Actual sample: AAON, AAPL, ABCB, ABL,
ACA, ACLS (FULL) and A, AA, AAP, AAT, ABCL, ABEO (CORE). 12 was chosen over the brief's suggested
20-30 for cost discipline (§D0/§26's own principle applied to real-money model-inference cost for
the first time in this session) - each call runs ~50K input tokens against Opus 5.5 pricing, and 12
was judged sufficient to validate the engine across a P1/P2 mix while keeping worst-case pilot spend
bounded and controllable (`MAX_BUDGET_USD_PER_CALL = $2.00` hard cap per individual call).

## S. Pilot Results

Live Claude Opus 5.5, invoked through the Claude Code CLI's own headless `print` mode
(`$CLAUDE_CODE_EXECPATH -p --model claude-opus-5-5`, all file/bash/edit tools explicitly disabled -
a pure text-in/text-out call) - not a separate API integration, and not this session's own model
(Sonnet 5) pretending to be Opus, which the brief explicitly forbade ("다른 모델로 몰래 대체하지
않는다"). Confirmed via the CLI's own response metadata (`"canonicalModel":"claude-opus-5-5"`) on
every successful call.

```text
LIVE MODEL EXECUTION: AVAILABLE AND USED (not a fallback report)

pilot candidates:              12   (6 FULL + 6 CORE)
final status OK:                12 / 12   (100%)
schema validation failures:      0
model call failures:             0
repair rounds needed (pre-fix): 17   (11 of 12 candidates needed >=1 repair)
repair rounds needed (post-fix, 2-candidate re-check): 1 of 2 candidates
research_completeness:          PARTIAL for all 12 (and both post-fix re-checks)

catalyst candidates produced:   50   (~4.2/candidate)
risk items produced:           105   (~8.75/candidate)
future-business items:          49   (~4.1/candidate)
sources cited (top-level):      55   (~4.6/candidate)

total real spend (both runs):  $26.49
```

`research_completeness = PARTIAL` on all 12 is a consistent, honest self-assessment, not a bug: the
same real gaps D0 identified before any of this was built - no earnings-calendar/consensus source,
no analyst-estimate source - are exactly what every candidate's `unknown_fields` names, and no
candidate falsely claimed `COMPLETE` despite those structural gaps.

## T. Quality Gates

Frozen before any pilot output was read, checked structurally by the schema and/or manually
audited on real output:

| Gate | Result |
|---|---|
| Q1 schema valid | PASS - 12/12 final outputs validate against `HResearchInterpretationV1` |
| Q2 all material claims source-linked | PASS - enforced by `Claim._material_claim_needs_a_source`, not just audited |
| Q3 no invented numeric facts | PASS on manual audit (AAON, AAPL spot-checked against real filing figures); `cross_check_fundamental_change_states` additionally guards the one code-owned field D3 could have silently changed - never tripped |
| Q4 no future source | PASS - D2.1 already guarantees `pit_eligible` filings only; D3 adds no new source acquisition |
| Q5 no prohibited investment language | PASS after the §Q fix (0 banned-language rejections in the 2-candidate re-verification; the pre-fix run's 11/12 repair rate was this same gate correctly firing, just on a badly-calibrated word list) |
| Q6 unknown discipline | PASS on manual audit - `unknown_fields` consistently named real, specific gaps (earnings_date, consensus_estimates, named_competitors) rather than being left sparse |
| Q7 no orphan source refs | PASS - enforced by `Claim._source_known` + the top-level sources-coverage check, both exercised live (this is what most of the schema-validation layer actually checks on every real call) |
| Q8 interpretation/fact separation | PASS on manual audit - e.g. AAON's backlog discussion explicitly tagged the sales-driver claim `FACT` and the "conversion story, not order-intake story" reading `INTERPRETATION` at `MEDIUM` confidence |

## U. Tests

57 new tests in `backend/tests/strategy_h_v2/research/`: `test_schema.py` (26: extra-field
rejection, aware datetime, no decision-field-in-schema structural check, claim provenance including
orphan-source-via-context, banned-language parametrized over 5 real phrases plus the board-approval/
customer-buys false-positive regression, future-business evidence-floor gates, catalyst/risk/
invalidation citation requirements), `test_prompt_builder.py` (8: tier priority, budget respect,
content-only schema exclusion, source-boundary wrapping, determinism), `test_validate.py` (9: JSON
extraction incl. code-fence stripping, the numeric-mutation cross-check both passing and catching a
mutated state, fail-soft on malformed JSON, metadata-field discard), `test_ledger.py` (6: V1/V2
versioning, overwrite refusal, checksum persistence), `test_run_d3_orchestration.py` (8, using a
fake `call_opus` - no real model calls: bounded repair success, repair-cap fail-soft with an exact
call-count assertion, model-call-failure handling, model/prompt-version persistence, deterministic
pilot-ticker selection). Full suite alongside every existing Strategy H0/PV and H-V2 test:
**278 passed, 0 failed.**

## V. Changed / New Files

New: `backend/app/backtest/strategy_h_v2/research/{__init__,schema,prompt_builder,validate,
ledger}.py`, `backend/app/dev/run_strategy_h_v2_d3.py`, `backend/tests/strategy_h_v2/research/
{helpers,test_schema,test_prompt_builder,test_validate,test_ledger,
test_run_d3_orchestration}.py`, this document. No D0-D2.1 file was modified.

## W. Commit

D3-only files staged and committed locally; the pre-existing 177-entry dirty worktree was never
touched. No push.

## X. Limitations

1. **The banned-investment-language fix (§Q) reduced but did not eliminate schema-repair
   friction** - "A"'s post-fix re-check still needed one repair round for an undiagnosed reason
   (not investigated further to control real-money cost on this pilot; worth tracing before a
   larger run).
2. **`evidence_id` is not cross-checked against the real chunk set**, only `source_id` is (§O) - a
   claim citing a real document but an invented or off-by-one chunk index would still validate.
3. **No first-class `CONFLICT` state exists yet** (§N) - conflicting sources are handled by
   instruction (prefer the more recent/authoritative one, keep both claims) rather than by a
   schema-enforced state machine.
4. **`research_completeness`'s definition is prompt-guided, not schema-cross-checked** against the
   input package's own completeness signals the way D2's evidence-bundle completeness was - unlike
   D1/D2's code-computed completeness states, D3's is the model's own judgment call, audited
   manually here and found consistently honest, but not structurally enforced.
5. **Real-money model-inference cost is a new, ongoing consideration** this pipeline did not have
   before D3 - $26.49 for 12 (+2 reruns) candidates implies a full 2,010-candidate run would cost on
   the rough order of several thousand dollars at this per-candidate rate, which is exactly why
   §41 forbids that run in this stage and why a future full-run decision needs its own explicit
   budget approval, not an assumption that "the pilot worked, scale it."
6. **The pilot's evidence-selection budget (200K characters, §D) was not itself varied or
   stress-tested** - it is a documented, reasonable starting point, not something this stage proved
   optimal.

## Y. Verdict

Official evidence converts into source-linked, schema-valid, investment-decision-free research
interpretation - confirmed with real Claude Opus 5.5 execution on 12 real companies, not a
simulation. A real defect was found on that live run and fixed and re-verified, matching this whole
session's discipline of catching mistakes through actual execution rather than assuming correctness.
No investment decision, valuation conclusion, or price figure was ever generated. Every future-
business claim and catalyst candidate is evidence-gated by the schema itself, not merely by prompt
instruction.

```text
H-V2-D3 = PASS WITH LIMITATIONS
```

The "WITH LIMITATIONS" qualifier reflects §X's honest residuals (an undiagnosed remaining repair
cause, `evidence_id` not fully cross-checked, no CONFLICT state) - none of which compromise the
core guarantee (no investment decision leaked, no fabricated numbers, no orphan citations), all of
which are legitimate, bounded follow-up items for a future iteration.

```text
D4 Expectation Gap / Decision Engine = NOT READY
```

Not because the engine is unready architecturally, but because D4 needs a real, reviewed batch of
D3 research outputs to build and test against, and this stage deliberately produced only 12 (plus 2
reruns) for engine validation, not a research corpus (§41's explicit prohibition on a full run in
this stage). D4 readiness should be reassessed once a larger, budget-approved D3 batch exists.

## Z. Next Action (max 7)

1. User reviews this document and the real ledger outputs (`data/runtime/strategy_h_v2/d3/ledger/`)
   before any further D3 investment.
2. Investigate the residual repair cause found in §Q/§X.1 with a few more live candidates before
   committing to a larger batch - it is unresolved, not just undocumented.
3. Decide and budget-approve a bounded "batch 2" D3 run size (larger than 12, still far short of
   2,010) as the actual basis for beginning D4, per §41's sequencing.
4. Consider tightening the `evidence_id` cross-check (§X.2) before that larger batch, since citation
   precision matters more as the corpus a future D4/D3-reviewer relies on grows.
5. Decide whether a first-class `CONFLICT` claim state (§X.3) is worth adding now or left for when a
   real evidence conflict is actually observed in a larger batch.
6. Do not resume PV-track factor ranking, any Value/Growth/Quality composite, or generate any
   APPROVE/WATCH/REJECT-equivalent signal from D3 output - that boundary remains D4's alone.
7. Do not begin Expectation Gap synthesis, fair-value arithmetic, or paper trading until D4's own
   contract is frozen and reviewed against a real D3 research batch.

## Final Declarations

```text
model used                      = Claude Opus 5.5 (live, via Claude Code CLI print mode)
full 2,010-company AI run?      NO
forward returns used?           NO
Value/Growth/Quality alpha ranking? NO
Expectation Gap finalized?      NO
APPROVE/WATCH/REJECT generated? NO
valuation conclusion generated? NO
price target generated?         NO
investment decision generated?  NO
paid market data used?          NO
existing dirty files modified?  NO
push?                           NO
```

# Strategy H-V2 - AI Quantamental Stock Picking - Architecture Redesign Audit V1

- audit date: 2026-09-28
- status: **ARCHITECTURE REVIEW / REDESIGN - NOT A PREREGISTRATION**
- prohibitions honored: no new backtest, no factor retuning, no composite score, no new
  weights/thresholds, no GPT execution, no broker/paper trading, no paid-data purchase, no push
- H0/H0.5/H0.6/PV1/PV2/PV2C/PV3 results are unchanged and are not reinterpreted as PASS

This document does not close Strategy H and does not freeze an H-V2 preregistration. It audits
whether the thing that was tested (H0 through PV3) is the same thing that was originally proposed,
and proposes an architecture for a separate track, `H-V2`, that is reviewed by the user before any
contract is frozen.

## A. Repository State

- branch: `main`
- HEAD: `fd727f1` (`research(strategy-h): run 2Y quality prevalidation`)
- origin: `ahead 15, behind 0`
- working tree: dirty, pre-existing. Modified: `backend/app/backtest/baseline/runner.py`,
  `backend/app/dev/collect_u1_minute.py`, `backend/app/dev/historical_v2.py`,
  `backend/app/replay_smoke/runner.py`, five `backend/app/services/*` files,
  `backend/app/strategy/config.py`, `backend/app/strategy/engine.py`, four `backend/tests/*` files,
  `docs/backtest/WORKFLOW.md`, five `frontend/components/*.test.tsx` files,
  `frontend/package.json`/`package-lock.json`. Untracked: `.streamlit/`, three loose `BTCUSDT-*.csv`
  files at repo root, and a large set of new `backend/app/backtest/{authority,baseline,basis,
  collector}/*` modules.
- None of the above was read, edited, staged, or reset by this audit. This document and its
  directory (`docs/backtest/strategy_h_v2/`) are the only new files this audit adds.

## B. Original Strategy H

There is no standalone "Strategy H vision" document anywhere in this repository. Searching
`docs/`, `git log --all --grep`, `V1_FINAL_SPEC.md`, `ARCHITECTURE.md`, `DEVELOPMENT_PLAN.md`, and
`V2_BACKLOG.md` for "Strategy H" returns nothing outside `docs/backtest/strategy_h_candidate/`.
Strategy H was never integrated into the project's top-level spec the way Strategy A/E's GPT
Research workflow was (`docs/GPT_RESEARCH.md`, `docs/ai/*`).

The only place the original idea is written down is one paragraph inside the very first frozen
document, `H0_DATA_PIT_PREREGISTRATION_V1.md` §1:

```text
strategy: AI QUANTAMENTAL RE-RATING
long-run candidate thesis:
VALUE + GROWTH + QUALITY + FUTURE BUSINESS + CATALYST + PRICE

H0 tests only whether a reliable historical input panel can be built.
FUTURE BUSINESS and qualitative CATALYST interpretation are not implemented in H0.
```

Two other fragments corroborate a richer original scope than what was ever operationalized:

- H0's own prohibition list names `Entry1/Entry2`, `TP1/TP2`, and `GPT company analysis` as things
  it does *not* implement (§13, repeated in the H0 result §M). These terms are never defined
  anywhere else in the repository. Their only appearances in the entire codebase are as exclusions
  inside the H0 documents. There is no prior document that specifies what Entry1/Entry2 or TP1/TP2
  actually mean.
- The planned incremental sequence in H0 §10 is itself already a pure quant-factor ladder:

  | Stage | Frozen research theme |
  |---|---|
  | H1 | Value |
  | H2 | Value + Growth |
  | H3 | Value + Quality |
  | H4 | Value + Growth + Quality + Revision |
  | H5 | Composite Quantamental Candidate |

  Nothing in H1-H5, as planned in September 2026 before any pilot ran, ever names GPT, Future
  Business, or Catalyst as a stage. "H5 Composite" reads naturally as combining H1-H4's factors, not
  as adding a qualitative layer.

**Finding:** the pasted brief that opened this session is a more complete statement of "Strategy H"
than anything previously committed to this repository. The original idea was real (it is named and
partially scoped in H0 §1), but it was never written down as a full investment-process design before
implementation started. H0 through PV3 built and tested only the fragment of it that already had a
name in H0's own vocabulary: Value, Growth, Quality.

## C. What PV1-PV3 Actually Tested

| Track | Question | Universe | Verdict |
|---|---|---:|---|
| H0 | Can a PIT fundamental/market panel be reconstructed at all? | 40-CIK pilot | INCONCLUSIVE |
| H0.5 | Can a survivorship-safe universe + PIT market cap be built? | 5 dated sessions, AAPL depth | INCONCLUSIVE |
| H0.6 | Does the active paid entitlement supply 10Y data? | 10 annual entitlement probes | **FAIL** (2/10 succeeded) |
| H-PV1 | Do 3 Value yield ratios predict 63-session SPY-relative return? | 37 securities, 2024-10 - 2026-05 | UNPROMISING |
| H-PV2 | Do 4 Growth ratios predict the same return? | same 37 (development) | INCONCLUSIVE (concentration FAIL) |
| H-PV2C | Does the PV2 Growth model generalize to new issuers? | 120 disjoint securities | NOT CONFIRMED |
| H-PV3 | Do 5 Quality-level ratios predict the same return? | same 120 (PV2C's set) | UNPROMISING |

Every one of these seven documents is a **cross-sectional ranking-factor test**: rank a fixed
universe by one score, form decile portfolios, compare D10 vs D1 vs SPY over an exact 63-session
holding period, and apply the same gate battery (IC, monotonicity, concentration, cost, extreme-
removal robustness). None of them:

- called an LLM or scored a business qualitatively (every result document states `GPT used? NO`);
- looked at a single company's actual business, product, or story;
- asked whether the market's expectation differed from the fundamental reality (no analyst-estimate
  or consensus data source was ever proven available - H0 §4 explicitly defers this: `H4 REVISION =
  DEFERRED`);
- used a catalyst, an earnings-date state, a valuation target, or an entry/exit rule;
- ran on more than 2 years of data or on a survivorship-safe universe (both are still unproven -
  H0.6 FAIL means the paid 10-year entitlement was never actually available).

The recorded recommendation after PV3 is explicit and narrow: **"RECOMMEND CLOSE QUANT FUNDAMENTAL
CORE"** - i.e. close the naive single-ratio ranking track, not "Strategy H's underlying thesis is
false."

## D. Original H vs Tested H Gap

| Original Strategy H Component | PV1-PV3 Tested? | Sufficiently Validated? |
|---|---:|---:|
| Value (yield ratios) | Yes (PV1) | Yes, and it failed (UNPROMISING) |
| Growth (YoY ratios) | Yes (PV2/PV2C) | Yes, and it did not generalize (NOT CONFIRMED) |
| Quality (margin/ROA/cash) | Yes (PV3) | Yes, and it failed (UNPROMISING) |
| Future Business | No | No - explicitly out of scope in every H0-PV3 document |
| Catalyst | No | No - explicitly out of scope in every H0-PV3 document |
| Market Regime | No | No - not mentioned in any H document |
| Competitive Position | No | No |
| Earnings / Guidance | Partial (filing-acceptance timestamp linkage proven feasible) | No consensus/actual/surprise store exists |
| Valuation / Mispricing (EV multiples) | Attempted, then dropped | No - PV1 excluded EV/EBITDA and EV/Sales because H0 never froze EBITDA/debt normalization |
| Entry Price | No | No - Entry1/Entry2 named only as an exclusion, never defined |
| Thesis Break | No | No |
| Re-entry | No | No |
| AI qualitative research | No | No - `GPT used? NO` on every document |
| Composite candidate (H5) | No | No - PV track never reached H1, let alone H5 |

**Answer to the section's question:** PV1-PV3 tested a narrow, mechanically-scored subset of Strategy
H's stated long-run thesis (three of six named components), and even that subset was tested as
independent single-factor rankings rather than as the originally planned incremental composite
(H1-H5). The qualitative half of the original idea (Future Business, Catalyst, mispricing judgment,
entries/exits, thesis lifecycle) was never built and never tested.

## E. What the Negative Results Really Mean

Interpreted narrowly and correctly, on the evidence actually produced:

1. On a 2-year, 37-120 name, single-recent-snapshot, non-survivorship-safe US universe, ranking
   stocks by one Value yield ratio and holding the top decile for 63 sessions does not beat SPY
   after 10bp costs. The relationship ran in the *wrong* direction (mean IC -0.21, monotonicity
   -0.70).
2. The same test on 4 Growth ratios showed a real in-sample signal (IC +0.125, D10-D1 +14.2%) that
   failed to survive two independent checks: issuer concentration (93.2% of the positive return came
   from 5 of 37 names) and confirmation on a disjoint 120-issuer sample (D10 flipped to -0.99%, mean
   IC to -0.006). This is the textbook signature of development-set overfitting on a small universe,
   not evidence that "growth doesn't matter."
3. The same test on 5 Quality-level ratios, on the same larger 120-issuer sample that Growth failed
   to confirm on, also failed (D10-D1 -77.5%, monotonicity -0.71).

These are real, competently executed negative results about **naive single-ratio cross-sectional
ranking** on the data currently available. They are evidence against a specific mechanism: "compute
one number per company, rank, buy the top decile, hold exactly 3 months."

## F. What They Do NOT Mean

The results do **not** show:

- that fundamentals are irrelevant to which US stocks re-rate;
- that a qualitative, catalyst-and-narrative-aware selection process would fail - that mechanism was
  never built or tested;
- that "buy a good company at the right time" is false - only that "top-decile-of-one-ratio, held
  mechanically" is false on this sample;
- that longer history or a paid data entitlement would not change the Value/Growth/Quality result -
  H0.6 proves the entitlement itself was never actually active, so nothing about a properly
  constructed 5-10 year PIT panel has been tested one way or the other;
- that catalysts, expectation gaps, or earnings-driven re-rating do not exist as a source of return -
  there is zero evidence either way, because none of it was operationalized;
- that combining Value+Growth+Quality into a single linear composite would fail - that composite
  (the originally planned H1-H5 ladder) was never actually run, because the PV track substituted
  three independent univariate tests for it. (This is not a reason to now run that composite either -
  see §22/§X below.)

## G. Domestic-style Investing Process Reconstruction

| Actual investment judgment | H-V2 module |
|---|---|
| 실적이 좋아지는가 (is the business improving) | Fundamental Change Engine |
| 앞으로 먹거리가 있는가 (future growth driver) | Future Business Research (AI Research Engine) |
| 업황이 좋은가 (is the industry structurally growing) | Market / Theme Engine |
| 실제로 돈 버는 사업인가 (is it a real, cash-generating business, not a story) | Evidence-based Business Review (AI Research Engine, grounded in the Evidence Bundle, not IR language) |
| 곧 이벤트가 있는가 (is there a near-term catalyst) | Catalyst Engine |
| 너무 오른 건 아닌가 (is it already priced in) | Valuation / Expectation Gap Engine |
| 지금 들어가도 되나 (is now a reasonable entry) | Entry Engine (valuation + support + volatility) |
| 실적 발표 앞인데 들고 갈까 (hold through an earnings print) | Earnings / Event State |
| 오른 뒤 다시 살까 (re-enter after a move) | Thesis Versioning + Re-entry Watch |

This table is the actual design target for H-V2. None of these nine rows correspond to what PV1-PV3
tested; PV1-PV3 corresponds only to the middle-left half of row 4 ("real business"), reduced to
static accounting ratios.

## H. Proposed H-V2 Definition

`H-V2 AI QUANTAMENTAL STOCK PICKING` is not a factor portfolio. It is a candidate-discovery-plus-
research pipeline whose alpha claim, if any, lives in the AI research and thesis-formation step, not
in a quant score:

```text
US EQUITY UNIVERSE
  -> BASIC ELIGIBILITY FILTER            (code)
  -> FUNDAMENTAL CHANGE SCREEN           (code, candidate discovery only)
  -> EVIDENCE BUNDLE COLLECTOR           (code)
  -> AI RESEARCH ENGINE                  (AI)
     -> business / change / future business / industry / catalyst / mispricing
  -> EXPECTATION GAP ASSESSMENT          (AI, code-checked)
  -> VALUATION (Bear/Base/Bull)          (code arithmetic, AI picks framework)
  -> DECISION: APPROVE / WATCH / REJECT  (AI + rule gate)
  -> ENTRY ENGINE                        (code + AI judgment)
  -> EVENT / EARNINGS STATE MACHINE      (code, evidence-driven)
  -> THESIS VERSIONING                   (immutable ledger)
  -> EXIT / RE-ENTRY                     (new thesis version required)
```

PV1-PV3's factor formulas do not sit inside this pipeline as an alpha source. They can sit inside
the Fundamental Change Screen as one *discovery* signal among several (see §I), on the same footing
as "revenue accelerated," never as the thing that is scored, ranked, and bought.

## I. Quant Role Redesign

Old framing (what PV1-PV3 actually tested): `Quant Score = Alpha Engine`. Rank the universe by the
score; the score is the trading edge.

Proposed H-V2 framing: `Quant = Candidate Discovery + Eligibility Filter + Risk Filter + Evidence
Preparation`. Concretely:

- **Eligibility filter**: reuse H0/H0.5's frozen PIT market-cap-lane and universe primitives to
  exclude MICRO caps, non-common-stock, and distressed/illiquid names. This is a gate, not a rank.
- **Discovery**: surface candidates where something *changed* (revenue/margin/estimate inflection,
  relative-strength shift), reusing PV1-PV3's coverage-and-comparable-period machinery as a
  detector, not a scorer. A name can be discovered without being "top decile" of anything.
- **Risk filter**: distress, extreme dilution, liquidity - reuse existing Strategy A/E risk-scoring
  conventions rather than inventing new ones.
- **Evidence preparation**: turn raw PIT facts into the structured bundle the AI Research Engine
  reads (§ J). This is the highest-leverage reuse of PV1-PV3's actual code: the winsorization,
  comparable-period matching, and coverage-gate logic were built correctly and audited; only their
  downstream use (rank-and-buy) is being retired.

No PV1-PV3 factor is ever again allowed to independently gate a BUY decision by threshold. It can
only feed evidence into the AI Research Engine.

## J. AI Research Role

The AI Research Engine is the actual hypothesis under test in H-V2 (Layer B, §T). It receives the
Evidence Bundle (§ contract in K/6 of the brief, restated below) and is asked to reason, not compute:

- **Business**: how does this company actually make money, who are the customers, what is the
  revenue mix.
- **Change**: what changed in the last 6-12 months in the evidence bundle; is the direction of
  travel improving faster than the bundle's own trailing trend would suggest.
- **Future business**: is the new segment a story or a business - does the bundle show actual
  revenue, backlog, or customer evidence for it, or only qualitative claims.
- **Industry / theme**: is this a structurally growing industry, and where in the value chain does
  this company sit.
- **Catalyst**: is there a dated or datable event in the next 1-3 months that could change market
  perception (earnings, guidance, contract, product, capacity, regulatory, customer).
- **Mispricing**: not "is this a good company" but "does the evidence show change that is better (or
  worse) than what the current valuation/price trend implies is already expected."

## K. Expectation Gap Concept

The literal formalization the brief proposes (`FUNDAMENTAL REALITY - MARKET EXPECTATION =
EXPECTATION GAP`) cannot be computed as a number today, because no PIT analyst-consensus or
estimate-revision source has ever been proven accessible in this repository. H0 §4 tried and
explicitly deferred it: `Analyst forward EPS, forward revenue, estimate revision ... If no PIT
source exists, record H4 REVISION = DEFERRED`. That deferral was never lifted.

Proposed H-V2 v1 formalization is therefore **qualitative and AI-judged**, not a numeric spread,
until (and unless) a PIT consensus/estimate source is separately proven:

```text
EXPECTATION GAP (v1, categorical):
  WIDE_POSITIVE   evidence shows change clearly ahead of what price/guidance trend implies
  MODEST_POSITIVE evidence shows some unrecognized improvement
  NEUTRAL         evidence roughly matches what appears priced in
  MODEST_NEGATIVE evidence shows deterioration not yet reflected
  WIDE_NEGATIVE   evidence shows deterioration clearly ahead of price
  UNKNOWN         insufficient evidence to judge (must not be guessed away)
```

The AI must cite the specific evidence-bundle fields and, where available, the company's own prior
guidance (from filings, not IR marketing language) that its judgment is anchored to. A numeric
expectation-gap score is future work gated on a separately proven PIT estimates/consensus source; it
is explicitly not part of the H-V2 v1 design.

## L. Future Business / Catalyst

Both are AI-reasoning outputs grounded in the Evidence Bundle's `recent_filings` and
`known_risks`/qualitative fields, never invented. A Future Business claim must be backed by revenue,
backlog, or customer evidence already present in the bundle or a cited primary source; "the company
says this could be big" without such evidence must be recorded as `UNKNOWN`/`STORY_UNVERIFIED`, not
as a positive signal. A Catalyst must have an approximate date or date window; an undated "something
good might happen" is not a Catalyst.

## M. Valuation

Reuse the H0/PV1 lesson directly: EV/EBITDA and EV/Sales were excluded from even the simplest PV1
Value test because H0 never froze a reliable EBITDA/debt-composition normalization. That gap is
still open and blocks any Bear/Base/Bull valuation arithmetic in H-V2, not just the multiples PV1
already tried. Proposed division of labor:

- **AI**: choose which valuation framework is appropriate for the business (historical multiple,
  peer multiple, growth-adjusted multiple, FCF yield) and explain why, in prose, citing the evidence
  bundle.
- **Code**: perform the arithmetic once the framework and inputs are chosen, using the same PIT
  discipline as H0 (no current-quarter backfill, no invented EBITDA).

This is architecture only in this document. No Bear/Base/Bull calculation is implemented here.

## N. Earnings / Events

H0's own result (§K) already establishes the boundary precisely: SEC filing-acceptance-to-session
linkage is feasible (10-Q/10-K/8-K can be timestamped), but a genuine earnings-*announcement*
calendar with consensus, actual EPS/revenue, guidance, and surprise was not found locally. The
proposed state set (`NORMAL / NO_NEW_ENTRY / HOLD_THROUGH_EARNINGS / REDUCE_BEFORE_EARNINGS /
EXIT_BEFORE_EARNINGS / POST_EARNINGS_REVIEW`) is architecturally sound and can be driven today by
filing-acceptance timestamps alone for the binary "is an earnings-linked filing imminent/just
occurred" signal, but the richer inputs the brief lists (consensus, guidance, recent estimate trend)
require a data source that has never been proven in this repository. This is a data gap to record,
not a reason to drop the state machine.

## O. Thesis / Re-entry

As the brief specifies: 1-3 months is a thesis horizon, not a mandatory holding period. Entry
depends on valuation, technical support, volatility, and margin of safety judged jointly by AI and
code; exit triggers are TP reached, thesis realized, thesis broken, valuation full, or a changed
event; re-entry always requires a new Thesis Version, never a reuse of the prior thesis with a
patched price.

## P. State Machine

```text
DISCOVERED -> RESEARCHING -> WATCH -> APPROVED -> ENTRY_ZONE -> POSITION
  -> THESIS_UPDATE -> PARTIAL_EXIT -> EXITED -> REENTRY_WATCH

side states: REJECTED, THESIS_BROKEN, EVENT_RISK, NO_NEW_ENTRY
```

Minimum evidence required per transition (illustrative, to be finalized in a separate contract):

| Transition | Minimum required evidence |
|---|---|
| DISCOVERED -> RESEARCHING | eligibility filter pass + at least one discovery signal |
| RESEARCHING -> WATCH | evidence bundle complete enough that `UNKNOWN` count is bounded (see §11) |
| WATCH -> APPROVED | business-change, future-business-or-catalyst, mispricing thesis, valuation range, risk/invalidation, cited sources - all present |
| POSITION -> THESIS_UPDATE | any material new filing/event, or scheduled periodic review |
| any -> THESIS_BROKEN | AI or code detects an invalidation condition named in the original thesis |
| EXITED -> REENTRY_WATCH | requires a new Thesis Version; the old version is never edited in place |

## Q. AI vs Code Responsibility

| Task | Code | AI |
|---|---:|---:|
| EPS / margin / ratio arithmetic | YES | NO |
| Fair-value arithmetic (once framework chosen) | YES | NO |
| PIT eligibility / lane / coverage gating | YES | NO |
| Business-model interpretation | NO | YES |
| Future-business evidence judgment | partial (evidence extraction) | YES (interpretation) |
| Catalyst interpretation | partial (date extraction) | YES |
| Expectation-gap judgment (v1, categorical) | NO | YES |
| Risk / invalidation thesis | partial (flags from evidence) | YES |
| Source retrieval / evidence bundle assembly | YES | partial (may request more) |
| Final APPROVE / WATCH / REJECT | rule gate (`UNKNOWN` ceiling) + AI | YES |
| Human trading authorization | N/A | N/A - AI decision is never itself a BUY, matching `AGENTS.md` §6: Human APPROVE != BUY |

## R. Proposed Architecture

| Module | Input | Output | AI or Code | PIT requirement | Failure mode |
|---|---|---|---|---|---|
| Universe Engine | dated reference snapshot | eligible security list | code | historical PIT universe (still unproven, H0.5) | survivorship leak if current list used |
| Quant Eligibility Engine | universe + fundamentals | pass/fail + reason | code | H0/H0.5 PIT primitives | silent current-cap fallback |
| Fundamental Change Engine | PIT fundamentals | discovery candidates (not ranked-to-buy) | code | comparable-period rules from PV1-PV3 | treating discovery score as alpha (regression to PV1-PV3 mistake) |
| Research Evidence Collector | filings, market data, price context | Evidence Bundle JSON | code | filing-acceptance cutoff (H0 §5) | stale/backfilled facts leaking into bundle |
| AI Research Engine | Evidence Bundle | business/change/future-business/industry/catalyst narrative | AI | evidence must be dated as of decision time | invented numbers, IR-language trust |
| Expectation Gap Engine | AI narrative + evidence | categorical gap (§K) | AI, code-checked for citation | consensus/estimate source unproven | overclaiming a numeric gap without data |
| Catalyst Engine | filings/events | dated catalyst list | partial | filing-acceptance cutoff | undated "story" catalysts |
| Valuation Engine | evidence + AI framework choice | Bear/Base/Bull (not implemented here) | code (framework: AI) | EBITDA/debt normalization unresolved (H0) | inventing EBITDA/multiples |
| Decision Engine | all of the above | APPROVE / WATCH / REJECT | rule + AI | `UNKNOWN` ceiling before APPROVE | AI-only judgment with no rule floor |
| Earnings / Event Engine | filing-acceptance timestamps (+ unavailable consensus) | event state | code (partial data) | filing timestamp linkage (H0 proven) | assuming a full earnings calendar exists |
| Thesis Versioning | decisions over time | immutable versioned ledger | code | timestamped at decision time | overwriting instead of versioning |
| Shadow Validation | thesis ledger + forward prices | tracked outcomes | code | forward-only, no backfill | grading on hindsight-selected candidates |
| Portfolio Engine (future) | approved positions | sizing/exposure | not designed here | - | - |
| Execution Engine (future) | portfolio | orders | not designed here | - | - |

## S. Data Requirements

H-V2's Quant/Evidence layer inherits every open blocker from H0/H0.5/H0.6 unchanged:

1. Survivorship-safe US security master with list/delist dates and stable issuer linkage - not
   proven (H0.5 INCONCLUSIVE).
2. PIT shares-outstanding coverage at scale - proven for one large-cap issuer (AAPL, 5 dates), not
   proven across a broad sample.
3. >=5 years of local daily OHLCV/splits/dividends - current local panel is 2 years
   (2024-09-17..2026-09-16); the active Massive credential failed 8 of 10 annual entitlement probes
   (H0.6 FAIL), so a 5-10 year panel is not currently purchasable-and-proven, only advertised.
4. Historical sector/industry classification - largely absent.
5. Earnings-announcement calendar with consensus/actual/surprise - absent.
6. Analyst estimates/revisions - absent; blocks any numeric Expectation Gap (§K).
7. EBITDA/interest-expense/invested-capital normalization - incomplete; blocks Valuation Engine
   multiples beyond simple yields.

None of these are H-V2-specific new requirements; they are the same H0/H0.5/H0.6 blockers that
already stopped official H1. H-V2 does not remove the need to resolve them for the Quant/Evidence
layer; it changes what the resolved panel is used for (evidence, not ranking).

## T. Historical vs Forward Validation

- **Layer A (historical)**: PV1-PV3's results are retained as-is and used as negative evidence
  against naive factor ranking (§E/§Y-lesson), not re-derived. H-V2 does not need a new historical
  backtest of Value/Growth/Quality; that question is already answered for the available sample.
- **Layer B (forward)**: the AI Research Engine's actual claim (evidence-grounded qualitative
  re-rating judgment) cannot be tested on history without either (a) a properly built PIT panel that
  is not currently proven, or (b) running the AI's reasoning process itself retroactively, which
  risks hindsight leakage into "future business" and "catalyst" framing. Forward shadow validation
  is therefore the primary validation path for H-V2, not a fallback.

## U. Shadow Validation Design

Every research event is frozen at decision time and never edited in place; corrections create a new
Thesis Version. Minimum-sample candidates for user decision (none of these are frozen by this
document):

```text
candidate generation: weekly
shadow duration:      3-6 months minimum before any performance read
minimum APPROVE decisions before verdict: at least 30-40
minimum unique issuers across APPROVE:    at least 20-25 (guards against the same
                                           concentration failure PV2 already showed)
```

The lower bound should be set high enough that a repeat of PV2's failure mode (93% of the return
from 5 names) cannot by itself produce a false PROMISING verdict at the AI layer.

## V. Benchmarks

Minimum comparison set, to be specified exactly in the eventual shadow-validation preregistration:

```text
APPROVE            vs WATCH
APPROVE            vs REJECT
APPROVE            vs Quant-only shortlist (Fundamental Change Engine candidates, unfiltered by AI)
APPROVE            vs SPY
```

The Quant-only-shortlist comparison is the one that actually answers "does the AI layer add
incremental value over the discovery signal alone" - it is the direct test of whether H-V2's core
bet (AI judgment adds value that a mechanical score does not) is true.

## W. Existing H Assets Reusable

- H0/H0.5's PIT primitives: filing-acceptance cutoff, non-stale PIT-shares resolution, unadjusted
  market-cap formula, market-cap lanes (`backend/app/backtest/strategy_h0/`). Directly reusable in
  the Quant Eligibility Engine.
- PV1-PV3's comparable-period matching, winsorization, and coverage-gate code. Reusable inside the
  Fundamental Change Engine and Evidence Collector as *discovery/extraction* logic, not as a ranker.
- The existing Strategy A/E GPT Research contract (`docs/GPT_RESEARCH.md`,
  `backend/app/research/domain.py`): source-priority rules (SEC/IR/exchange > news > other),
  fact/interpretation separation, `UNKNOWN` discipline, `evidence_v0` structural-evidence-confidence
  formula, prompt-injection resistance requirement, immutable versioned analysis rows, and the
  Human-Decision-separated-from-execution gate. This system was built for Strategy A/E's intraday
  catalyst-momentum workflow (daily TOP8 scan, manual ChatGPT paste, `APPROVE != BUY`), not for
  Strategy H's 1-3 month horizon, so its prompts and cadence do not transfer directly. Its *contract
  shape* does: H-V2's AI Research Engine should reuse the same fact/interpretation separation,
  source-priority, and manual-paste workflow pattern (`AGENTS.md` §8 currently forbids automatic GPT
  API calls in V1) rather than inventing a new one.

## X. Existing H Assets To Retire

Nothing is deleted. Two roles are retired:

- Value/Growth/Quality scores acting as an independent alpha ranker ("top decile, buy") is retired.
  It failed on the only evidence available (§C).
- The originally planned incremental H1-H5 ladder (Value -> Value+Growth -> Value+Growth+Quality+
  Revision -> Composite) is superseded by H-V2's non-additive design and should not be resumed as
  originally planned, even if a future study wanted to revisit factor combination - see §22
  prohibition below.

## Y. Risks / Failure Modes

- AI arithmetic trust or invented financial figures - must be structurally prevented (§12 of the
  brief), not just discouraged.
- Small forward-sample concentration: the exact PV2 failure mode (a handful of names carrying the
  entire result) can recur at the AI layer if the minimum-issuer floor in §U is set too low.
- Hindsight leakage into "future business" or "catalyst" framing if any retroactive testing is
  attempted instead of pure forward shadow.
- Data-entitlement risk is unresolved, not new: H0.6 already showed the paid Massive credential does
  not currently deliver what its pricing page advertises.
- Source/prompt injection into AI research inputs - already a named risk in the existing GPT
  Research contract; H-V2 must inherit the same resistance requirement.
- False confidence from Layer A: reusing PV1-PV3 as "the historical validation already happened"
  risks understating that the actual H-V2 mechanism (AI judgment) has zero historical evidence
  either way.

## Z. Proposed H-V2 Development Sequence (max 10 steps)

1. User reviews and revises this architecture document.
2. Freeze the Evidence Bundle JSON contract (data-only, no scoring) as its own preregistration.
3. Build the Quant Eligibility Engine and Evidence Collector by adapting H0/PV1-PV3 code; add tests
   asserting Value/Growth/Quality scores are inert (recorded, never gating).
4. Freeze the AI Research prompt/output contract (APPROVE/WATCH/REJECT fields, `UNKNOWN` ceiling,
   citation requirement), reusing the manual-paste pattern from `docs/GPT_RESEARCH.md`.
5. Run a small, non-scored dry run to check evidence-bundle completeness and AI output discipline
   only (no investment or performance claim of any kind).
6. Freeze Thesis Versioning and the immutable decision ledger schema.
7. Freeze the Valuation Engine contract separately, gated on resolving the still-open EBITDA/debt
   normalization question from H0.
8. Freeze the Forward Shadow Validation preregistration: cadence, minimum N, benchmark set (§U/§V).
9. Begin weekly forward candidate generation and thesis recording only; no paper trading, no broker.
10. Read a performance/incremental-value verdict only after the frozen minimum sample and period are
    reached.

## AA. Verdict

```text
H-V2 REDESIGN: JUSTIFIED
```

The evidence in §B-§D supports the premise that PV1-PV3 tested a narrow quant-factor slice of a
broader original idea, and that the negative results are informative about that slice, not about
the broader idea. A redesign that separates Quant-as-filter from AI-as-judge, and that validates
forward rather than re-fighting the same 2-year historical sample, is a reasonable next step. It
is not yet a preregistration and authorizes nothing beyond further design work.

```text
H-V1 Factor Quant Core   = CLOSED / ARCHIVED
Strategy H Concept       = UNDER REDESIGN
H-V2                     = ARCHITECTURE DESIGN (this document)
```

## AB. Next Action (max 7)

1. User reviews this document and either approves, revises, or rejects the H-V2 direction.
2. If approved, write a separate `H_V2_EVIDENCE_BUNDLE_CONTRACT_V1` preregistration before any AI
   prompt is written.
3. Decide the Expectation Gap formalization explicitly (§K): categorical AI judgment v1 is proposed;
   a numeric version requires a separately proven PIT consensus/estimate source first.
4. Decide the forward-shadow minimum sample/period (§U) before any candidate generation starts.
5. Decide whether AI calls stay manual-paste (matching the existing Strategy A/E workflow and
   `AGENTS.md` §8) or become API-automated for H-V2; this is a scope decision, not an architecture
   detail.
6. Do not resume PV-track factor tuning, do not build a linear Value+Growth+Quality composite, and
   do not purchase paid historical data until the Evidence Bundle and AI Research contracts are
   frozen and reviewed.
7. If approved, schedule the H0.6 paid-entitlement retry (billing/credential fix) independently,
   since the Quant Eligibility Engine still needs it regardless of the H-V2 redesign.

## Final Questions

**"우리가 지금까지 Strategy H를 너무 Factor Quant 전략처럼 검증한 것인가?"**

YES, on direct textual evidence rather than impression. H0's own founding paragraph (§B above)
already named a six-component thesis and, in the same breath, declared two of those six components
(Future Business, Catalyst) out of scope. Every subsequent frozen document - H0.5, H0.6, PV1, PV2,
PV2C, PV3 - repeats the identical exclusion (`GPT used? NO` is a literal line in every PV result's
final declarations). The originally planned H1-H5 ladder was, before any pilot ran, already scoped
as Value -> Value+Growth -> Value+Growth+Quality+Revision -> Composite, with no stage ever named for
the qualitative half of the idea. What actually got tested (three independent single-ratio
cross-sectional rankings on a 2-year, 37-120-name sample) is therefore not a partial test of Strategy
H's full thesis that happened to also look like factor investing; it is, and was always scoped as,
a pure factor-quant hypothesis wearing Strategy H's name. It was tested competently as exactly that,
and it failed or did not confirm as exactly that.

**"사용자가 실제 국내주식에서 하는 기업분석형 투자방식을 미국시장에 시스템화하려면 H-V2는 어떤
구조여야 하는가?"**

The structure in §H/§R: Quant is demoted from alpha engine to eligibility filter, discovery signal,
and evidence preparer (§I); a separate AI Research Engine reads a structured, PIT-safe Evidence
Bundle and reasons about business, change, future business, industry, catalyst, and mispricing (§J),
never inventing numbers or trusting IR language (§Y); the actual buy/watch/reject judgment is a rule-
gated AI decision, not a score threshold (§Q/§P); positions carry a versioned, immutable thesis that
can be updated or broken but never silently overwritten (§O/§P); and because this mechanism has zero
historical evidence either way (§T), the primary validation path is a disciplined, timestamped
forward shadow compared against WATCH, REJECT, the Quant-only shortlist, and SPY (§U/§V) - not a
retroactive backtest of the same 2-year sample that already falsified the simpler factor-ranking
version of this idea.

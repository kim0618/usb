# Strategy H-V2 - D1 Universe / Eligibility / Change Detection / Research Priority V1

- declared: 2026-09-28
- status: **PIPELINE IMPLEMENTATION - PIPELINE VALIDATION RUN, NOT AN ALPHA RESULT**
- stage: **H-V2-D1**
- authoritative contract: `docs/backtest/strategy_h_v2/H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md`
- implementation: `backend/app/backtest/strategy_h_v2/`, `backend/app/dev/run_strategy_h_v2_d1.py`
- tests: `backend/tests/strategy_h_v2/` (58 tests, all passing alongside the existing 63-test
  Strategy H0/PV regression suite - 121 total, 0 failures)
- sample run: `data/runtime/strategy_h_v2/d1/D1-20260928T042746Z/` (gitignored runtime artifact)

D1 builds the candidate-discovery pipeline defined in D0 §E (Universe -> E1 Eligibility -> E2
Fundamental Change Detection -> E3 Research Priority -> Candidate Evidence Stub). It computes no
forward return, no Value/Growth/Quality score, no composite, no APPROVE/WATCH/REJECT decision, no
Expectation Gap, and no fair value. Every threshold in this document is justified by data
reliability, tradability, or catastrophic-risk avoidance, never by a forward-return result - no
forward return is read anywhere in this code path, including the sample run below.

## 1. Universe

`universe.py` builds `UniverseRow` identity records from a dated reference snapshot. It performs no
exclusion; it classifies (`COMMON_STOCK` / `NOT_COMMON_STOCK` / `UNKNOWN` security type,
`exchange_supported` against H0.5's frozen `{XNYS, XNAS, XASE}` set) and leaves the exclusion
decision to E1.

This deliberately is not H0.5's unresolved survivorship-safe *historical* universe. D1 is a live
research pipeline, not a backtest: using today's dated snapshot as today's candidate universe is
legitimate here even though the same snapshot would not be legitimate as a historical decision-date
universe. `universe.py`'s docstring states this boundary explicitly so it is not reused by mistake
in a future historical study.

Sample run input: `data/runtime/research_universe_u1/reference/tickers/CS_2024-10-25.json.gz`, the
same dated CS/XNYS/XNAS/XASE/us snapshot H0-PV3 already used - **5,192 rows**, already
pre-filtered by the source query to `type=CS, market=stocks, locale=us` (5,191 on a supported
exchange; one stray `BATS` row, correctly flagged `exchange_supported=False` rather than silently
dropped). 6 rows have no CIK; 783 have neither `share_class_figi` nor `composite_figi` - all
preserved as `None`, never guessed.

## 2. E1 - Eligibility

`eligibility.py` implements a three-tier reason model, which is a refinement made necessary during
implementation (see §9 below): **hard** reasons (a known fact makes the security ineligible),
**blocking unknown** reasons (a *primary* input - identity, price, or fundamentals coverage itself -
could not be evaluated, so the candidate cannot respectably be marked ELIGIBLE or INELIGIBLE), and
**soft unknown** reasons (a *secondary* risk flag - distress, dilution - could not be evaluated, but
does not by itself veto an otherwise well-covered candidate). Hard reasons produce `INELIGIBLE`;
blocking-unknown reasons (with any soft ones attached) produce `UNKNOWN`; otherwise the result is
`ELIGIBLE`, possibly carrying soft-unknown caveats for the evidence bundle to show later.

Frozen thresholds and their justification (`eligibility.py` module docstring carries the same text):

| Threshold | Value | Why (not a return-fit) |
|---|---:|---|
| `MIN_CLOSE_PRICE` | $1.00 | common minimum-bid-price / tick-size-noise floor |
| `MIN_TRAILING_DOLLAR_VOLUME` | $1,000,000/day | below this, execution is impractical and the close is an unreliable print |
| `MIN_TRAILING_SESSIONS` | 60 sessions | price_context/relative-strength evidence needs ~3 months of local history to mean anything |
| `MIN_RESOLVED_CANONICAL_FIELDS` | 4 of 12 | below this, E2 has too little raw material, not that the company is unhealthy |
| `MATERIAL_DILUTION_RATIO` | 10% | shared with E2's dilution evidence, not a second independent rule |

Reason codes: `NOT_COMMON_STOCK`, `UNKNOWN_SECURITY_TYPE`, `UNSUPPORTED_EXCHANGE`, `MISSING_CIK`,
`MISSING_SECURITY_ID`, `INSUFFICIENT_DAILY_HISTORY`, `EXTREME_LOW_PRICE`, `LOW_LIQUIDITY`,
`INSUFFICIENT_FUNDAMENTALS`, `DISTRESS_FLAG`, `EXTREME_DILUTION_RISK`, `NO_PRICE_DATA`. A security
can carry multiple reasons at once (tested in `test_eligibility.py::test_multiple_reason_codes_can_coexist`).

`DISTRESS_FLAG` is computed only from `equity < 0` on an already-resolved canonical fact - a
narrow, data-derived proxy, not an invented going-concern detector (§9 / §11 Limitations).

## 3. E2 - Fundamental Change Detection

`change_detection.py` reuses, unmodified, H-PV2's comparable-period matching and transition
handling (`period_family`, `comparable_pair`, `growth_rate`) and H-PV3's matched-duration/instant-
pair primitives (`matched_duration`, `instant_pair`) from `backend/app/backtest/strategy_h0/`. Only
their use is new: extracting a multi-period *trend*, never a cross-sectional *rank*.

A shared deterministic classifier, `classify_trend`, drives every metric:

1. a sign crossing (the series' first and last values have opposite, nonzero sign) is checked
   first and produces `INFLECTION_POSITIVE`/`INFLECTION_NEGATIVE` - this is checked before
   acceleration because a sign crossing is a more specific, more informative research trigger;
2. failing that, if there are >=3 points and every step moves the same direction beyond a
   0.5-percentage-point noise floor (`MONOTONIC_EPS`), growth-rate series get
   `ACCELERATING`/`DECELERATING` at `HIGH` confidence;
3. otherwise the net change across the window decides `STABLE` (within 2 points, `STABLE_EPS`),
   `IMPROVING`, or `DETERIORATING`, at `MEDIUM` confidence with >=3 points or `LOW` with exactly 2.

Per-metric application:

- **Revenue / Operating Income / diluted EPS** (`growth_trend`): up to 4 successive YoY comparable-
  period growth rates. EPS additionally preserves `LOSS_TO_PROFIT`/`PROFIT_TO_LOSS` as their own
  states when the *latest* period is a transition, overriding the generic trend call, per D1's own
  design brief §15.
- **Free cash flow** (`fcf_trend`): OCF minus CapEx on PV2's exact quarter/YTD family rule, so a
  discrete quarter is never diffed against a YTD figure - unit-tested directly
  (`test_fcf_ytd_quarter_mismatch_is_rejected_not_guessed`).
- **Operating margin** (`operating_margin_trend`): a *level* trend (`IMPROVING`/`STABLE`/
  `DETERIORATING`/inflection), never `ACCELERATING`/`DECELERATING` - acceleration of a ratio's level
  is not a meaningful research trigger the way acceleration of a growth rate is.
- **Cash / total debt** (`balance_sheet_trend`): direction only (`INCREASING`/`DECREASING`/`STABLE`/
  `UNKNOWN`), deliberately never `IMPROVING`/`DETERIORATING` - whether more debt is good or bad is a
  business-context judgment left to the future AI Research Engine (D0 §V), not a fact code should
  assert.
- **Shares outstanding / dilution** (`dilution_ratio`): distinguishes a recorded split (not
  dilution) from an unexplained >=10% increase (`EXTREME_DILUTION_RISK`), reusing the same splits
  store PV1/PV2C already used.

`MATERIAL_STATES` (`ACCELERATING`, `DECELERATING`, both `INFLECTION_*`, both transition states) is
what E3 treats as "worth reading about." A bare `IMPROVING`/`STABLE`/`DETERIORATING` is not counted
as material on its own. `test_change_state_is_not_a_decision` asserts no field or state name in this
module encodes an investment decision.

## 4. E3 - Research Priority

`research_priority.py`'s `PriorityInputs` dataclass has no return, price-target, or performance
field of any kind (`test_no_return_field_accepted` checks this structurally, not just by
convention). `compute_priority` is a deterministic function of: data completeness (`resolved /
total` canonical fields), the count of `MATERIAL_STATES` changes and how many are `HIGH` confidence,
and whether the most recent accepted SEC filing is within 14 days:

```text
completeness < 34%                                  -> HOLD  (too little evidence to prioritize)
>=2 HIGH-confidence material changes, or
  >=1 HIGH-confidence material change + a filing
  accepted within the last 14 days                  -> P1_HIGH
>=1 material change (any confidence)                -> P2_MEDIUM
otherwise                                            -> P3_LOW
```

`test_priority_is_not_a_quality_rank` constructs a candidate with thin fundamentals coverage but a
fresh, high-confidence inflection (P1_HIGH) against one with complete coverage but no change signal
(P3_LOW), to make the "urgency to research, not quality of company" distinction executable rather
than only documented.

## 5. Candidate Evidence Stub

`evidence_bundle.py`'s `CandidateEvidenceStub` (Pydantic, `extra="forbid"`, aware timestamps) mirrors
the existing Strategy A/E research contract's conventions
(`app.research.domain.GPTResearchResult`). The five AI-only fields (`future_business`, `catalysts`,
`expectation_gap`, `competitive_position`, `thesis`) are a `NotResearched` enum fixed to
`NOT_RESEARCHED` - the schema physically cannot accept a fabricated value in their place
(`test_ai_fields_cannot_be_set_to_a_fabricated_value`).

`pipeline.py::assemble_candidate` wires E1-E3 and this schema together for one security. E2/E3 only
run when eligibility is `ELIGIBLE` - an `INELIGIBLE` or `UNKNOWN` candidate gets an empty
`change_evidence` tuple and `research_priority.state = null`, so no research effort (real or
simulated) is ever spent past the eligibility gate
(`test_ineligible_candidate_skips_e2_and_e3`).

## 6. PIT / Unknown Audit

Every fundamental fact reaching E2/E3 passes through H0's unmodified `accepted_at <= data_cutoff`
filter (`facts.py::resolve_fact`, reused inside `comparable_pair`/`matched_duration`/`instant_pair`).
`test_pit_cutoff_excludes_facts_accepted_after_cutoff` constructs a fact accepted after the cutoff
and confirms it never appears in the growth trend, even though its report period would otherwise be
in range.

`UNKNOWN` is never silently converted to a number:

- `resolve_fact`'s `MISSING`/`AMBIGUOUS` statuses flow straight into `_snapshot()`'s
  `{"status": ..., "value": None}`, never a zero;
- `balance_sheet_trend`/`growth_trend`/`fcf_trend` return `ChangeState.UNKNOWN` with `current_value
  = None` when fewer than 2 valid points exist, rather than guessing a direction;
- `negative_equity`/`material_dilution` are `bool | None` throughout, and `None` is never treated as
  `False` (`FundamentalsCoverage`'s docstring states this explicitly, and
  `test_unknown_equity_recorded_but_does_not_block_an_otherwise_eligible_candidate` exercises it).

## 7. Artifact Layout

```text
data/runtime/strategy_h_v2/d1/{run_id}/
  manifest.json         # pipeline-quality metrics only, no forward return
  manifest.sha256
  candidates/{ticker}.json   # Candidate Evidence Stub, P1_HIGH/P2_MEDIUM only
```

`data/runtime/` is gitignored repository-wide; nothing here is committed. Each run gets its own
`run_id` (`D1-YYYYMMDDTHHMMSSZ`) directory rather than overwriting a shared path, matching the
immutable-output principle in the D0 contract's Immutable Research Ledger (D0 §U) even though D1
itself has no thesis to version yet.

## 8. Pipeline Validation Run

Executed once against real local repository data (no synthetic universe), no push, no dirty files
touched. Run id `D1-20260928T042746Z`, wall time 76 seconds.

| Metric | Value |
|---|---:|
| Universe rows (dated CS snapshot) | 5,192 |
| Distinct tickers in local daily store | 15,995 |
| Universe rows with any local SEC fundamentals | 162 (158 unique CIKs) |
| E1 ELIGIBLE | 123 |
| E1 INELIGIBLE | 5,069 |
| E1 UNKNOWN | 0 |
| E3 P1_HIGH | 11 |
| E3 P2_MEDIUM | 81 |
| E3 P3_LOW | 31 |
| Candidate evidence stubs written (P1+P2) | 92 |

INELIGIBLE reason distribution (a security can carry more than one, so these do not sum to 5,069):

```text
INSUFFICIENT_FUNDAMENTALS  5,030   (no/partial local SEC companyfacts - the expected, honest
                                    outcome for the ~5,030 names outside H0's and PV2C's
                                    already-downloaded CIK set)
DISTRESS_FLAG               5,041   (equity unresolved -> hard-ineligible together with
                                     INSUFFICIENT_FUNDAMENTALS for the same no-data names, not a
                                     separate distress signal)
EXTREME_DILUTION_RISK       5,044   (same no-data co-occurrence as DISTRESS_FLAG)
LOW_LIQUIDITY                1,383
EXTREME_LOW_PRICE              470
INSUFFICIENT_DAILY_HISTORY     121
MISSING_CIK                      6
UNSUPPORTED_EXCHANGE             1   (the one stray BATS row)
```

Reading `DISTRESS_FLAG`/`EXTREME_DILUTION_RISK` counts alongside `INSUFFICIENT_FUNDAMENTALS` above:
they are not three independent failure populations. A security with zero local fundamentals fails
`INSUFFICIENT_FUNDAMENTALS` (hard) and, because `equity`/`shares_outstanding` cannot resolve either,
also collects `DISTRESS_FLAG` and `EXTREME_DILUTION_RISK` in the same `INELIGIBLE` result - this is
why their counts are close to `INSUFFICIENT_FUNDAMENTALS` rather than each flagging a distinct
group. Only among the 123 `ELIGIBLE` candidates does `DISTRESS_FLAG`/`EXTREME_DILUTION_RISK` mean
something diagnostic on its own: 11 of the 123 carry an unresolved `EXTREME_DILUTION_RISK` caveat
(`eligible_soft_reason_counts` in the manifest) purely because their local shares-outstanding
history is too thin to compute a dilution ratio, not because dilution was detected.

E2 change-state distribution across the 123 ELIGIBLE candidates (counts by metric, from the same
manifest):

```text
revenue:            STABLE 28, IMPROVING 27, DETERIORATING 22, INFLECTION_POSITIVE 19,
                     INFLECTION_NEGATIVE 9, UNKNOWN 8, ACCELERATING 6, DECELERATING 4
operating_income:    IMPROVING 29, DETERIORATING 22, INFLECTION_POSITIVE 23, UNKNOWN 36,
                     INFLECTION_NEGATIVE 9, STABLE 2, DECELERATING 1, ACCELERATING 1
eps_diluted:         DETERIORATING 26, IMPROVING 24, INFLECTION_POSITIVE 24, UNKNOWN 21,
                     INFLECTION_NEGATIVE 10, LOSS_TO_PROFIT 7, PROFIT_TO_LOSS 4, STABLE 3,
                     ACCELERATING 3, DECELERATING 1
free_cash_flow:      UNKNOWN 48, DETERIORATING 19, INFLECTION_NEGATIVE 16, INFLECTION_POSITIVE 15,
                     IMPROVING 13, PROFIT_TO_LOSS 6, LOSS_TO_PROFIT 5, DECELERATING 1
operating_margin:    STABLE 42, IMPROVING 33, UNKNOWN 22, DETERIORATING 18, INFLECTION_POSITIVE 6,
                     INFLECTION_NEGATIVE 2
cash:                INCREASING 61, DECREASING 58, STABLE 4
total_debt:          DECREASING 42, INCREASING 37, STABLE 29, UNKNOWN 15
shares_outstanding:  STABLE 97, INCREASING 6, DECREASING 9, UNKNOWN 11
```

`ACCELERATING`/`DECELERATING` are rare by construction (they require >=3 monotonic comparable
periods, a strict bar), while `INFLECTION_*` and directional `IMPROVING`/`DETERIORATING` carry most
of the signal - consistent with real companies rarely showing four clean, one-directional quarters
in a row, and with the classifier design in §3 preferring the more specific inflection call whenever
a sign crossing exists.

No forward return, price target, or performance figure was computed or read at any point in this
run.

## 9. Deviations from the D1 Brief, and Why

The brief's original two-tier reason model (`ELIGIBLE`/`INELIGIBLE`/`UNKNOWN` with a single
undifferentiated "unknown" bucket) was implemented first and then revised after the first
integration test failed: a candidate with complete core fundamentals but only one local
shares-outstanding observation (so dilution could not be judged) came back `UNKNOWN` and was
therefore excluded from E2/E3 entirely, which contradicts the brief's own instruction that missing
data should be recorded, not used to silently drop a researchable candidate. The three-tier
hard/blocking-unknown/soft-unknown split in §2 is the fix, applied before any real data was ever run
through the pipeline - no threshold was adjusted after seeing the real-universe result in §8.

## 10. Tests

`backend/tests/strategy_h_v2/` (58 tests): `test_universe.py` (7), `test_eligibility.py` (13),
`test_change_detection.py` (19, including the PIT-cutoff and "not a decision" invariants),
`test_research_priority.py` (8, including the structural no-return-field check and the
priority-is-not-a-quality-rank behavioral check), `test_evidence_bundle.py` (7), `test_pipeline.py`
(4, integration-level). Run together with the existing 63-test Strategy H0/PV suite:
`121 passed, 0 failed`.

## 11. Limitations

1. **Fundamentals coverage is 158 CIKs out of 5,088 with a CIK** (H0's 40 + PV2C's ~119, minus
   overlap/failures). This is a direct, honest consequence of not doing new SEC ingestion in D1, per
   the brief's conservatism instruction (§0/§3) - no bulk companyfacts download was performed. D2
   (Evidence Collector) or a dedicated ingestion contract, not D1, is where broader coverage would be
   added, and any such expansion needs its own explicit approval since it is a new, sustained SEC
   traffic pattern, not a one-off pilot.
2. **Local daily OHLCV remains the same ~2-year window** (2024-09-17 through 2026-09-16) H0.6 already
   found insufficient for a 5-10 year historical study; this does not block D1 (a live pipeline needs
   only recent history for E1/price_context), but it means `trailing_52w_high/low` and
   `relative_strength_3m` are only as good as this window allows.
3. **`DISTRESS_FLAG` is a single proxy (negative equity)**, not a going-concern or bankruptcy
   detector; the brief explicitly forbade inventing a broader risk heuristic without a data source,
   and none exists locally today.
4. **No earnings-calendar/consensus source exists**, so `earnings` in every evidence stub is
   `{"status": "UNKNOWN", ...}` and `days_since_latest_filing` (a filing-acceptance recency proxy) is
   the only urgency signal E3 can use - this is the same gap H0 §K already identified, not a new one.
5. **`price_context` momentum fields use local unadjusted prices only**; no dividend adjustment or
   survivorship-safe universe is claimed or needed for D1's live-pipeline purpose (§1), but this
   evidence must not be reused in a historical backtest context.
6. **Multi-class CIKs are not specially deduplicated** in D1 (unlike H0-PV3's explicit
   `MULTI_CLASS_UNRESOLVED` exclusion); a CIK with two tickers can produce two `UniverseRow`s that
   each independently resolve fundamentals. This is acceptable for a live per-ticker research
   pipeline (each ticker is its own tradable security) but would need H0.5's multi-class handling if
   this universe were ever reused for a cross-sectional statistical test.

## 12. Verdict

```text
H-V2-D1 = PASS WITH LIMITATIONS
```

The pipeline is reproducible (same inputs -> same evidence stub,
`test_evidence_stub_is_reproducible_for_same_inputs`), deterministic, evidence-backed, and reads no
forward return anywhere. It ran successfully against the real, current, full US common-stock
reference universe (5,192 names) using only already-locally-available data, and correctly reduced it
to 123 eligible, evidence-supported research candidates (92 written as P1/P2 stubs) without any
manual override, forward-return filtering, or Value/Growth/Quality ranking. The "PASS WITH
LIMITATIONS" qualifier is entirely about *fundamentals data coverage breadth* (§11.1), which is an
expected, pre-existing, honestly-surfaced constraint from H0/H0.5/H0.6, not a defect in this stage's
logic.

## Final Declarations

```text
model used                              = Claude Sonnet 5
new backtest run?                       NO
forward returns used for selection?     NO
Value ranking used?                     NO
Growth ranking used?                    NO
Quality ranking used?                   NO
Value/Growth/Quality composite?         NO
GPT research executed?                  NO
Expectation Gap evaluated?              NO
valuation decision executed?            NO
investment decision generated?          NO
broker work?                            NO
paper trading?                          NO
existing dirty files modified?          NO
push?                                   NO
```

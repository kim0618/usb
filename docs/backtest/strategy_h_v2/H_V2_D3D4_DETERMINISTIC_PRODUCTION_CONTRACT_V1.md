# H-V2 D3/D4 Deterministic Production Contract V1 (result)

Date: 2026-10-14 KST. Not a research stage, not H-V3, not D8. Live AI calls: **0**. Not deployed,
not pushed. Contract: `docs/operations/h_d3d4_deterministic_v1.json`
(`H_D3D4_DETERMINISTIC_PRODUCTION_V1`, sha256 `576c32e1…ffa81e`), committed as `9f31eb7` at
2026-10-14T07:16:42Z, **before** any historical replay or real-data run. No rule was changed after.

Verdict: **H-V2 D3/D4 DETERMINISTIC PRODUCTION CONTRACT = NEEDS REVISION.**

* What works: production H now reaches real D6 decisions with no AI. FG WATCH->WATCH, VRRM
  WATCH->WATCH, new candidate ADI admitted WATCH, all from deterministic D3/D4, D5_D2R_V1 and the
  unchanged D6. NOT_DECISION_ELIGIBLE count on the three: 0.
* Why not PASS (the stage's own §15 rule): class E occurs on a D6-binding path.
  1. **R3.1 (V1's own defect, REJECT path).** "Business supported" was frozen as "a 10-K chunk
     labelled BUSINESS is in the package". D2.1's frozen depth policy does not always chunk or label
     Item 1, so 7 of 22 stored packages (LUV, WEN, CWBC, MOFG, DORM, IDCC, AEYE) read as unsupported,
     which fires D6's `thesis_unsupported_by_the_evidence_bundle` REJECT. In the replay this turned
     DORM from WATCH into REJECT on a collection artifact. The fix is a rule change, so it belongs in
     V2, not in this version.
  2. **Expectation gap (APPROVE and REJECT paths).** No frozen contract decides POSITIVE (C1 needs a
     non-price item; every one needs a guidance/management transcription that D4 §Q gives to AI) or
     NEGATIVE ("a large completed re-rating", "not yet reflected": no size is frozen). The generator
     emits UNKNOWN by contract. APPROVE is therefore unreachable, which was declared in the contract
     before running.
  3. **Catalyst materiality, future-business stage, invalidation (APPROVE path).** No frozen code
     reading; abstained.

## 1. D6 backtrace (what decides, and what produces it now)

| D6 input | Binding | Before (V1 pipeline) | Now (deterministic V1) |
|---|---|---|---|
| business_model.revenue_drivers | APPROVE; REJECT if empty | AI | R3.1: 10-K BUSINESS chunk cited (defect, §0) |
| fundamental_change (count, source) | APPROVE; REJECT if 0 | AI (states copied from E2) | R3.2: E2 states with SEC citations |
| growth_durability | none (basis text) | AI | UNKNOWN (E, non-binding) |
| future_business stage / flags | APPROVE; REJECT story-only if non-empty | AI | [] (E, APPROVE path) |
| catalyst count / materiality / timing | APPROVE; REJECT if 0 | AI | R3.5: next periodic report by statutory deadline within 3 months; materiality UNKNOWN |
| risks + invalidation | APPROVE | AI | R3.6 EVIDENCE_GAP / DILUTION; invalidation [] (E) |
| unknown_fields tokens | APPROVE | AI | R3.10 fixed list |
| management signal, guidance change, priced-in, conflicts | none in D6 | AI | not produced / UNKNOWN |
| expectation_gap + confidence | APPROVE (§L); REJECT (adverse) | AI | UNKNOWN/UNKNOWN (G1-G3) |
| valuation confidence, TP1, TP2, Bear | APPROVE; REJECT fully priced | D5_D2R_V1 | unchanged |

D6 distinguishes only three gap groups (positive at MEDIUM+, adverse, everything else), so NEUTRAL
versus UNKNOWN never changes a decision.

D3 frozen rules: 12 (R3.1-R3.12). D3 new numeric thresholds: 0 (the 3-month horizon is D0 §K/§R;
filing deadlines are SEC Form 10-K/10-Q General Instruction A). D4 frozen rules: G1-G4 plus C4/C5/C6
and the precondition function. D4 new thresholds: 0. Expectation Gap production generator: exists and
is deterministic, but its only reachable state is UNKNOWN (contract E).

## 2. Historical replay (frozen V1, oracle = stored Opus outputs; `/home/tjd618/h_noai_repair/replay_v1contract.json`)

Each D3 record ran on the exact D2.1 package the model read (22/22 by package checksum) with the
issuer's SEC submissions filtered to filings accepted by the package cutoff; each D4 on the very
expectation bundle the model read; D6 with the stored D5_D2R_V1 rows of `D6-20261004T075808Z`.

| Layer | Cases | Same | Different |
|---|---|---|---|
| D3 (D6-binding projection) | 22 | 0 | 22 |
| D4 gap/confidence | 13 | 5 | 8 |
| D6 decision | 13 | 11 | 2 |

Difference classes:

| Class | Count | Where |
|---|---|---|
| A model discretionary interpretation | 2 | D3 catalyst count: MOFG (model 0, code 1), SIF (model 3, code 0: next deadline beyond horizon) |
| B contract intentionally abstains | D3 22 (future business, invalidation, catalyst materiality); D4 8 (6 NEUTRAL/LOW -> UNKNOWN, 2 refused -> UNKNOWN); D6 1 (SPSC: model D4 refused, ineligible -> WATCH) | |
| C model violated/overstated the contract | 0 found | |
| D implementation bug | 0 in production code (the replay's first pass had no submissions for 20 CIKs: a harness sourcing gap, fixed in the harness) | |
| E contract underspecified | D3 7 (R3.1 BUSINESS chunk); D6 1 (DORM WATCH -> REJECT via R3.1); D4 gap on both binding paths | |

IDCC and AEYE keep their REJECT, but V1 adds `thesis_unsupported` (R3.1) where the model had
`valuation_fully_prices` (IDCC, also fired now) and `story_only` (AEYE). Code-owned E2 states the model
had copied agree everywhere they overlap.

## 3. Real-data runs (production copy, `/home/tjd618/h_noai_repair/v1_evidence.json`)

Same production-copy store and A store as the NO-AI repair (server state read-only 15:17 KST). GATE
READY, SCAN READY (1,030 s, 700 CIKs, 10 written), queue FG P0, VRRM P1, ADI P2, AEYE
EVIDENCE_ALREADY_IN_THESIS, evaluation 116 s.

| | FG | VRRM | ADI (new) |
|---|---|---|---|
| AI calls | 0 | 0 | 0 |
| D3 | OK: BUSINESS cited; revenue DETERIORATING, EPS PROFIT_TO_LOSS, shares STABLE; 3 EVIDENCE_GAP risks; catalyst 10-Q due 2026-11-09 | OK: 6 states (revenue DETERIORATING, op income/margin INFLECTION_NEGATIVE, FCF DETERIORATING, EPS PROFIT_TO_LOSS, shares STABLE); catalyst 10-Q due 2026-11-09 | OK: revenue ACCELERATING, op income/margin/EPS IMPROVING, FCF DETERIORATING; catalyst 10-K due 2026-12-31 |
| D4 | UNKNOWN/UNKNOWN, ceiling MEDIUM, BLOCKED | same | same |
| MethodJudgement | frozen table | frozen table | CODE_OWNED_V1 AUTO_READY, P/FCF |
| D5 (D5_D2R_V1) | P/B LOW, 19.09 -> TP1 27.41, TP2 29.81, Bear 21.98 | EV/EBIT+EV/Sales MEDIUM, 3.01 -> TP1 3.25, TP2 5.66, Bear refused | P/FCF LOW, 414.70 -> TP1 354.99, TP2 422.90, Bear 320.03 |
| D6 | **WATCH** (blockers: future/catalyst, gap, valuation confidence, invalidation) | **WATCH** (future/catalyst, gap, invalidation) | **WATCH** (+ base-case upside negative) |
| transition | WATCH -> WATCH (T2 written) | WATCH -> WATCH (T2) | NONE -> WATCH (admitted, T1) |

ADI: E1 ELIGIBLE, E2 states above, E3 P1_HIGH, D2.1 package built, selected by the rule recorded
before any stage ran on it (`noai_preregistration.json`; first AUTO_READY candidate of the current
SCAN order, not in the ledger, not a validation ticker). H-wiring DATA_NOT_READY: 0.
NOT_DECISION_ELIGIBLE: 0 of 3. Ledger 8 -> 11, verify PASS; paper verify PASS, cash $10,000,
0 positions, 0 pending entries (WATCH enters nothing).

## 4. Memory

Per-stage peaks (Python RSS, production-copy data, SCAN with a 200-CIK cap; batches of 20 kept):

| Stage | Before | After lifetime fix |
|---|---|---|
| imports | 126 MB | 126 MB |
| universe / market tier | 138 / 148 MB | same |
| SEC submissions + companyfacts download | 253 MB | **195 MB** (download without parsing: `inputs.fetch_sec_files`) |
| E1-E3 batch incl. companyfacts load | 273 MB | 257 MB |
| MethodJudgement screen | 231 MB | 229 MB |
| process max (`/usr/bin/time`) | 279 MB | 278 MB |

Equivalence: the two SCANs (same store copy, same day, same cap) wrote identical intake index rows
(205 of 205, ignoring run ids and timestamps) and the same 10 candidates in the same order; a test pins
that the download path makes exactly `fetch_sec`'s two store calls.

The process maximum is set by parsing the single largest companyfacts document when its issuer is
scanned, so neither the fetch cap nor time slicing lowers it. Evaluation alone measured 235 MB
(3 items); the full V1 run (scan + evaluation, old download path) 322 MB. trader-j had 150-200 MB
available on 2026-10-14, so **1 GB server readiness = NEEDS OPERATIONAL OPTIMIZATION**: a streaming
companyfacts reader (same facts, lower peak) or more RAM, before the evaluation timer is enabled.
Combined scan+evaluation in one process is not recommended; `usb-h-evaluation` keeps them in a unit
of their own with MemoryMax 400M.

## 5. What V2 would need (operator decision; nothing here is implemented)

1. R3.1: business support = any extracted chunk of the latest 10-K (the bundle has the issuer's own
   annual report), or refuse D3 instead of writing an empty business model when no 10-K chunk exists.
   Either choice removes the collection-artifact REJECT; it is a rule change, hence V2.
2. A positive gap needs a code-owned non-price item. The arithmetic exists (`classify_range_movement`,
   `result_vs_range`); the transcription of guided ranges from text does not. A deterministic range
   extractor would be new code with its own validation, and it is the only route to APPROVE.
3. A negative gap needs a frozen size for "a large completed re-rating" or a frozen reading of "not yet
   reflected". Any number is a new threshold.
4. Catalyst materiality and an invalidation rule (APPROVE path only).

Without 2, H can WATCH or REJECT but never APPROVE, so the paper book cannot open a position from
real data.

## 6. Code (local commits, not pushed)

`h_auto/deterministic.py` (V1 generators, contract loader, D6 backtrace table), `executor.py`,
`queue.py` (METHOD_NOT_READY), `orchestrator.py`, `contract.py`, `inputs.py` + `facts_cache.py`
(download without parsing in the SCAN refresh), `docs/operations/h_autonomous_pipeline_v2.json`
(stages point to the frozen contract), frontend 평가법 대기 label. D5, D6, paper, A/E/C: unchanged.

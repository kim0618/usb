# H-V2-D4.3R2 - Consensus Referent Completeness

```
H-V2-D4.3R2
= READY FOR FINAL TIER A V3
```

Zero live Opus calls. Zero cost. `H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md`'s verdict - `Tier A =
MECHANICAL NOT READY` - remains standing, unedited, as immutable historical evidence.
`H_V2_D4_3R_AUDIT_EXECUTION_ALIGNMENT_V1.md`'s verdict - `READY FOR TIER A V3 RE-RUN` - is not
superseded; this document closes one gap that D4.3R's own §D explicitly found and deliberately left
open, and updates the verdict language to reflect that closure. No Tier A V3, no Tier B, no D5 ran or
runs as part of this document.

---

## A. Why R2 Exists

D4.3R's audit-alignment pass unified the audit layer's M4 detector onto the exact function
(`consensus_language.asserted_consensus_findings`) that the live validator already calls, and in
doing so its own §D disclosed - without touching, per brief §0's ban on consensus-semantics changes -
a coverage gap in that same, frozen production classifier:

> `consensus_language.py`'s referent pattern lists `consensus`, `analysts`, `sell-side`, `wall
> street` and `the street`, but not bare `market`. A sentence naming only "the market" with no other
> consensus referent - e.g. `"The market expects X."` in isolation, with no absence language -
> classifies **NEUTRAL** under the frozen production classifier.

This is not a new prohibition. D4's own stated principle, unchanged since D4.2, is that "analyst /
consensus / **market** expectation" cannot be asserted without a source. `"The market expects X."` was
always meant to be exactly as prohibited as `"Analysts expect X."` - the classifier simply never
implemented that half of its own stated scope. D4.3A's own live run never exercised this gap (its one
flagged sentence also contained `SOURCE_NOT_AVAILABLE`, which routes to ABSENCE regardless of the
referent list), so nothing about D4.3A's verdict or D4.3R's replay counterfactual changes here. This
document closes the gap before a Tier A V3 re-run can be authorized, so V3 is not spent rediscovering
it live.

## B. Existing Frozen Semantics (unchanged)

Carried in and not modified by this session:

```
M4 runtime/audit single source of truth   - consensus_language.asserted_consensus_findings,
                                             called identically by validate.py and by
                                             audit_strategy_h_v2_d4_1.audit_output (D4.3R §D)
M8 numeric-role classification            - numeric_roles.py (D4.3R §F)
zero-denominator semantics                - audit_strategy_h_v2_d4_2.py denominators block (D4.3R §G)
clean-smoke execution semantics           - d4_clean_checkout.py smoke_executed gate (D4.3R §H)
official venv probe                       - official_python() resolution (D4.3R §H)
```

None of these five items was touched. C1, C4, Gap semantics and confidence rules were not touched
either (brief §0).

## C. Missing Market Referent

Confirmed directly, before any change, in `consensus_language.py` as it stood after D4.3R:

```
classify_sentence("The market expects X.")       -> NEUTRAL   (should be ASSERTED)
classify_sentence("Wall Street expects X.")      -> ASSERTED
classify_sentence("Analysts expect EPS of $5.")  -> ASSERTED
```

`_REFERENT` (the pattern naming who an expectation can be attributed to) did not include `market`, so
no downstream check - the verb pairing, `_QUANTIFIED_NOUN`, `_COMPARISON` - ever fired for a sentence
whose only named referent was "the market".

## D. Assertion vs Absence

Two new, narrowly scoped patterns close the gap, added alongside `_REFERENT` rather than inside it
(see §F for why):

- **`_MARKET_VERB`** - `market` followed, at most one copula word away ("is"/"are"/"was"/"were"), by
  one of the same expectation verbs `_EXPECTATION_VERB` already lists (`expects`, `is expecting`,
  `estimates`, `projects`, ...). Catches "The market expects 20% growth.", "The market is expecting
  EPS of $5.", "The market expects commercialization next year."
- **`_MARKET_EXPECTATION_SUBJECT`** - `market` (optionally possessive) directly adjacent to
  `expectations|consensus|estimates|views`. Catches "Market expectations imply stronger demand." even
  though "imply" is not a listed verb - the noun phrase itself is the attribution, the same reasoning
  `_QUANTIFIED_NOUN` already uses for "consensus estimates of $5.00".

One absence phrase, `insufficient evidence`, is added to `_ABSENCE` (§E) because these two additions
make "market" a live trigger for the first time: without it, "There is insufficient evidence to
determine what the market expects" would flip from NEUTRAL (pre-R2: no referent matched at all) to
wrongly ASSERTED (post-R2: "market ... expects" now triggers) instead of ABSENCE - reproducing, in
one sentence, the exact D4.1 defect class this rule exists to prevent.

Both new patterns and the absence check are pure additions to `consensus_language.py`; no existing
pattern's matching behavior for `consensus`/`analysts`/`wall street`/`the street`/`sell-side` was
edited. `_QUANTIFIED_NOUN` and `_COMPARISON` also gained a `market` alternative in their existing,
already-adjacency-bounded noun-phrase groups ("market estimates of $5.00", "beat market
expectations"), for the same referent-completeness reason and with the same structural risk profile
as their existing `consensus`/`analysts` alternatives (a strict, ordered, narrow-gap sequence, not an
unscoped search - see §F).

## E. Neutral Market Uses

Unaffected, verified directly:

```
"The company serves the US market."                          -> NEUTRAL
"Market share increased."                                     -> NEUTRAL
"The addressable market was described as $500 million."       -> NEUTRAL
"Market conditions weakened."                                  -> NEUTRAL
```

None of these puts an expectation verb directly at "market"'s side and none puts an expectation noun
(`expectations`/`consensus`/`estimates`/`views`) directly after it, so neither new pattern fires and
none of the existing patterns names bare "market" as a referent on its own.

## F. Single Source of Truth

The modified file is exactly one: `backend/app/backtest/strategy_h_v2/expectation/consensus_language.py`.
The audit layer (`audit_strategy_h_v2_d4_1.audit_output`, called from `audit_strategy_h_v2_d4_2.audit_run`)
was not touched and needed no change - it already calls `asserted_consensus_findings` from this module
directly, unified there by D4.3R §D, and picks up this closure automatically. No audit-only regex was
added anywhere; `test_consensus_language_r2.py::test_production_and_audit_share_the_identical_classifier_function`
asserts by object identity, not merely equal output on today's fixtures, that
`validate.asserted_consensus_findings`, `audit_strategy_h_v2_d4_1.asserted_consensus_findings` and
`consensus_language.asserted_consensus_findings` are the same function object.

**Why two proximity-scoped patterns instead of adding `market` to `_REFERENT` directly:** the first
attempt at this closure did exactly that - added `market` to `_REFERENT` and let the existing,
unscoped `_REFERENT` + `_EXPECTATION_VERB` whole-sentence pairing handle it, the same mechanism
`consensus`/`analysts` already use. Replaying that version against D4.3A's own stored BSY record
produced a real false positive:

```
"It is an operational milestone, not a financial target, and says little about the size of the
market-expectation gap."
  -> ASSERTED, trigger "market ... target"
```

`_REFERENT` and `_EXPECTATION_VERB` are each independently `.search()`-ed anywhere in the sentence,
with no requirement that the two matches be related - safe for "consensus"/"analysts", which are rare
enough that an unrelated verb elsewhere in the same sentence is not a realistic collision, but not
safe for "market", which appears throughout D4's own approved price-reaction and milestone vocabulary.
"market" was therefore deliberately kept OUT of `_REFERENT`; `_MARKET_VERB` and
`_MARKET_EXPECTATION_SUBJECT` require the verb or noun directly at "market"'s side instead of merely
present somewhere in the sentence, which resolved the false positive (§G) without narrowing the
patterns' actual target set (§J tests).

## G. Offline Replay

D4.3A's stored raw outputs for `D4_2_A-20260929T072105Z` (SCCO, GOOG, BSY), read read-only exactly as
D4.3R left them, re-audited under `audit_strategy_h_v2_d4_2.audit_run` with this session's classifier:

```
M4                 -> PASS (0 asserted consensus expectations)  [unchanged from D4.3R's own replay]
M8                 -> PASS (0 claims restating a code-owned number)  [unchanged]
M1,M2,M3,M5,M6,M7,
M9,M10,M11,M12     -> unchanged (all PASS, identical to D4.3R's replay)
verdict            -> MECHANICAL READY  [unchanged from D4.3R's own counterfactual]
```

The one candidate sentence this closure's new patterns touch at all in the real stored records (BSY's
"market-expectation gap" sentence, §F) is confirmed NEUTRAL under the final, adjacency-scoped
patterns - the same verdict it held before this session, under D4.3R's classifier. No honest absence
statement in the stored records was rejected; no new assertion was newly (and correctly) caught in
the stored records either, because none of the three candidates' real output happens to contain a
market-expectation assertion. The closure's effect is visible only in the new synthetic test fixtures
(§J), not in a change to any existing gate's verdict on the real records - exactly what "coverage
completion, not a new finding" should look like.

## H. Initial Validity Diagnostic

New, execution-stability-only diagnostic, preregistered for Tier A V3 before any V3 result exists.
This refines D4.3R §I's preregistered `>= 2/3 is healthy` threshold into three named bands over the
same n=3 sample (SCCO, GOOG, BSY):

```
INITIAL VALIDITY (initial_valid / 3, first-attempt schema+validator pass, before any repair)

3/3  = HEALTHY
2/3  = ACCEPTABLE
0-1/3 = UNSTABLE
```

This is explicitly **not** a D4 semantics gate and does not fold into M1-M12 (unchanged, per brief
§9/§14 and D4.3R §I's own precedent: adding a gate after a result exists is what §12/§18 forbid, and
none exists yet). It says nothing about whether an expectation-gap judgement is any good - only
whether the model's unaided first attempt tends to satisfy the schema and validator without a repair
round.

**Rule, frozen now, before any Tier A V3 result exists:** if Tier A V3 reads mechanically READY
(all M1-M12 PASS) but INITIAL VALIDITY reads **UNSTABLE** (0 or 1 of 3), Tier B does not start
automatically. That combination requires a separate user review before any Tier B authorization,
because a mechanically clean pass built on a 0-1/3 first-attempt rate would mean the repair loop, not
the initial prompt, is doing load-bearing work - worth investigating on its own before spending the
Tier B budget on it.

## I. Tier A V3 Preregistration

If a Tier A V3 re-run is authorized (a separate user decision; this document does not authorize it),
frozen before any result exists, carrying forward D4.3R §M unchanged except where noted:

```
Sample:                      SCCO, GOOG, BSY (frozen, unchanged, tier_a_checksum verified) - not
                              replaced (brief §11)
M4 classifier:                consensus_language.asserted_consensus_findings (unified since D4.3R,
                              market-referent-complete as of this document)
M8 numeric check:            numeric_roles.fact_is_restated (unchanged since D4.3R)
Zero-denominator semantics:  unchanged since D4.3R (§G there)
Clean-smoke semantics:       unchanged since D4.3R (§H there)
Initial-valid diagnostic:    3/3 HEALTHY, 2/3 ACCEPTABLE, 0-1/3 UNSTABLE (this document, §H) -
                              UNSTABLE blocks automatic Tier B progression regardless of the
                              mechanical M1-M12 verdict; requires user review
Repair diagnostic:           report count/reason/cost per candidate, and separate a runtime
                              validator failure from an audit-only issue (this document, §J below;
                              classification approach otherwise unchanged from D4.3R §J)
Budget:                      UNCHANGED - $18.00 Tier A hard budget, same $2.00/call x 3 topology,
                              same $25.458606 remaining-cap baseline this document does not re-spend
Live Opus calls in R2:       0 (this document authorizes none)
Live Opus calls in V3:       0 (this document authorizes none; a V3 run is a separate authorization)
```

## J. Repair Diagnostic

For a Tier A V3 re-run, in addition to D4.3R §J's model-contract-violation vs audit-false-positive
split, separate:

```
runtime validator failure   - validate.py rejected the model's raw or repair-round output at the time
                               of the live run (visible in repair_rounds[].failure_code /
                               .failure_details_before)
vs
audit-only issue            - a defect visible only when the SAME stored output is re-scanned by the
                               audit layer after the run completed (audit_output's own findings on the
                               final_output), never surfaced to the model as a repair prompt during
                               the run
```

This distinction did not exist as a named split in D4.3A or D4.3R's own diagnostics; both defects
D4.3R repaired (§C, §E there) were audit-only in this sense - the live validator never rejected the
GOOG/BSY sentences in question, only the old, now-unified audit detector did. Naming the split
explicitly for V3 makes that distinction machine-reportable per candidate instead of requiring a
manual read of two different artifacts to notice it. No code change: `repair_rounds` and
`audit_output`'s per-candidate defects already carry everything needed to compute this split.

## K. Tests

New: `test_consensus_language_r2.py` (29 tests) -

- 5 tests: every §7 ASSERTION case (`"The market expects 20% growth."` through `"The market expects
  commercialization next year."`), classifier verdict and end-to-end `assemble_and_validate_d4`
  rejection.
- 4 tests: every §7 ABSENCE case, classifier verdict ABSENCE and no validation errors, including the
  `insufficient evidence` phrase this closure adds to `_ABSENCE`.
- 4 tests: every §7 NEUTRAL case, classifier verdict NEUTRAL and no validation errors.
- 1 test: the pre-existing V2 prescribed absence sentence is still accepted, unedited.
- 4 tests: pre-existing non-market ASSERTED sentences (`"Consensus expects 20% growth."`, etc.) still
  rejected.
- 4 tests: pre-existing legitimate D4 vocabulary (priced-in readings, price-reaction readings, the
  company's own guidance, and one that itself mentions "the market") still not ASSERTED - the
  regression case for §F's false positive is
  `test_legitimate_d4_vocabulary_naming_the_market_is_still_not_an_assertion["The market has already
  reacted strongly to this specific disclosure."]`.
- 1 test: production/audit function identity (`test_production_and_audit_share_the_identical_classifier_function`).
- 6 tests: `asserted_consensus_findings` agrees with `classify_sentence` on both new and existing
  fixtures (`test_asserted_consensus_findings_matches_classify_sentence`).

Existing H-V2 regression, run from the repository root:

```
before this session (backend/tests/strategy_h_v2, from repo root): 720 passed, 1 skipped, 0 failed
after this session  (backend/tests/strategy_h_v2, from repo root): 749 passed, 1 skipped, 0 failed
```

All 29 new tests plus 720 pre-existing tests pass; the one pre-existing skip
(`test_probe_under_a_forced_non_project_interpreter_is_marked_unofficial`) is unchanged and was
already explained as expected in D4.3R §L. `test_d4_3r_audit_alignment.py`'s real-stored-record test
(`test_m4_and_m8_pass_on_the_real_d4_3a_stored_records`) still passes: M4 and M8 both read PASS (0
findings each) on the actual `D4_2_A-20260929T072105Z` records, identical to D4.3R's own replay
result, confirming this closure moved nothing on real data (§G).

## L. Verdict

```
H-V2-D4.3R2
=
READY FOR FINAL TIER A V3
```

This is **not** a D4 Expectation Gap quality PASS, not an APPROVE/WATCH/REJECT, not a valuation, and
not itself an authorization to run Tier A V3 or spend anything. It says the one production-classifier
coverage gap D4.3R's own §D found and explicitly deferred is now closed, that the closure is covered
by tests including a regression for the false positive this closure's own first draft introduced and
then fixed, and that replaying it against D4.3A's exact stored records reproduces D4.3R's own
counterfactual verdict (`MECHANICAL READY`) unchanged. Whether to spend the (unchanged) $18.00 Tier A
budget on an actual V3 re-run remains a separate decision for the user.

## M. Final Declarations

```
live Opus calls?                 0
live cost?                       $0.00
D4.3A verdict changed?           NO (MECHANICAL NOT READY, unedited)
C1 changed?                      NO
C4 changed?                      NO
Gap semantics changed?           NO
consensus meaning changed?       NO (only existing intended referent coverage completed - see §A)
M8 changed?                      NO
Tier A V3 executed?              NO
Tier B executed?                 NO
D5?                              NO
valuation?                       NO
APPROVE/WATCH/REJECT?            NO
forward return?                  NO
existing dirty files modified?   NO
push?                            NO
```

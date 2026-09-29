# H-V2-D4.3R - Audit / Execution Semantic Alignment

```
H-V2-D4.3R
= READY FOR TIER A V3 RE-RUN
```

Zero live Opus calls. Zero cost. `H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md`'s verdict -
`Tier A = MECHANICAL NOT READY` - is left standing, unedited, as immutable historical evidence. This
document is not a quality result and grants nothing by itself: it repairs the audit layer, replays
the repair against records that were already paid for, and preregisters what a Tier A V3 re-run
would need. §L states plainly what this verdict is not.

---

## A. Why D4.3R Exists

D4.3A ran the frozen D4.2 contract live on 3 candidates and found the pipeline itself sound - model
identity, telemetry, checksums, budget and clean-checkout reproducibility all held exactly. Two
gates failed anyway: M4 (fabricated consensus) and M8 (numeric ownership). D4.3A's own §O diagnosis
traced both failures to the AUDIT layer's detectors, not to anything the model did:

- M4 ran an independent regex list to decide ASSERTED-vs-ABSENCE, duplicating a judgement
  `consensus_language.py` already makes for the live validator. The two disagreed on one sentence -
  an honest statement that consensus evidence is absent, phrased slightly differently from the one
  literal sentence the audit's old detector special-cased.
- M8 flagged a claim whenever ANY digit in its text failed to match a cited code fact's value,
  which is stricter than the rule's own wording ("states a DIFFERENT number"). A fiscal-period
  label ("2Q26"), a session count ("63 sessions") or a rule id ("C1") supplied a digit that was
  never a candidate restatement in the first place.

D4.3A's diagnosis explicitly left both defects unrepaired ("the gates were frozen before the run...
the detector is not touched and the verdict is not re-graded") and named the fix as a separate
authorized step. This document is that step.

## B. D4.3A Immutable Baseline

Carried in and not modified:

```
H-V2-D3.3            = PASS
H-V2-D4.2P           = PASS
H-V2-D4.3A Tier A    = MECHANICAL NOT READY   (M4 FAIL, M8 FAIL, all other M1-M12 PASS)
Tier B               = NOT EXECUTED
D5                   = NOT READY
```

Stored records for `D4_2_A-20260929T072105Z` (3 candidates: SCCO, GOOG, BSY) remain under
`data/runtime/strategy_h_v2/d4_2/` (gitignored, not part of any commit) exactly as D4.3A wrote them.
Nothing under that path was edited by this session; §J reads them read-only.

## C. M4 Divergence

`audit_strategy_h_v2_d4_1.py`'s old `CONSENSUS_ASSERTIONS` was eleven hand-written regex patterns,
independent of `consensus_language.py`'s sentence classifier that the live validator
(`validate.py:check_consensus_not_fabricated`) actually enforces. The one flagged string, GOOG
`root.limitations[0]`:

> "Consensus and estimate revisions are SOURCE_NOT_AVAILABLE, so the core input for judging what
> the market expects is absent."

| layer | verdict |
|---|---|
| production validator (`consensus_language.classify_sentence`) | **ABSENCE**, trigger `SOURCE_NOT_AVAILABLE` |
| old audit detector (`CONSENSUS_ASSERTIONS`, whole-string regex) | ASSERTED, matched `the market expect` |

The model wrote an honest absence statement in different words than the one literal sentence the
old detector special-cased, and got penalized by the audit for it - the same defect class D4.2
already repaired once in the validator, recurring in the audit layer that was supposed to be a
second opinion on the SAME rule.

## D. M4 Unified Semantics

`audit_strategy_h_v2_d4_1.py`'s `fabricated_consensus` detector (E3/M4) now calls
`consensus_language.asserted_consensus_findings` directly - the identical function
`validate.py` calls - instead of restating the ASSERTED/ABSENCE judgement with a second regex list.
Production validation and audit classification are now the same function call; the allowed/
forbidden semantics for what counts as "attributes an expectation to analysts or the market" lives
in exactly one place, `consensus_language.py`, which this session did not touch (brief §0: no
consensus semantics changes).

The audit still provides real independence: it scans every prose string in the output tree
(`iter_prose`, unchanged), a broader surface than the validator's own narrower `_every_text` field
list, so it can catch a violation in a field the validator does not check. What changed is only that
BOTH layers now agree on what ASSERTED means, once a string is scanned.

**A finding disclosed, not fixed, because fixing it would be a consensus-semantics change (forbidden
by brief §0):** `consensus_language.py`'s referent pattern lists `consensus`, `analysts`, `sell-side`,
`wall street` and `the street`, but not bare `market`. A sentence naming only "the market" with no
other consensus referent - e.g. `"The market expects X."` in isolation, with no absence language -
classifies **NEUTRAL** under the frozen production classifier, verified directly:

```
classify_sentence("The market expects X.")       -> NEUTRAL
classify_sentence("Wall Street expects X.")      -> ASSERTED
classify_sentence("Analysts expect EPS of $5.")  -> ASSERTED
```

This is existing, frozen production behavior (the same function `validate.py` already calls live),
not something this session introduced or is authorized to change. It is recorded here as an
observation for a future authorized session, exactly as brief §17 requires for anything a replay
surfaces. D4.3A's own live run never exercised this gap - GOOG's flagged sentence also contained
`SOURCE_NOT_AVAILABLE`/`is absent`, which correctly routes it to ABSENCE regardless.

## E. M8 Divergence

Old `_matches_fact` (`audit_strategy_h_v2_d4_1.py`) extracted every digit sequence in a claim's text
with a bare regex and compared each one against the cited code fact's value; if none matched, the
claim was flagged - even when none of those digits was ever a candidate restatement. D4.3A's six
false positives, all `RETURN_FRACTION` facts:

| candidate, path | cited fact | digits extracted | what they actually are |
|---|---|---|---|
| BSY `price_reaction_reading[2]` | -0.024464 | 2 | "Q2" |
| BSY `pre_event_positioning_reading[1]` | 0.138705 | 2 | "Q2" |
| BSY `pre_event_positioning_reading[3]` | -0.084499 | 1 | "Q1" |
| BSY `gap_rationale[3]` | -0.303109 | 1, 6 | "C1", "6-month" |
| GOOG `gap_rationale[4]` | -0.090487 | 63 | "63 sessions" |
| SCCO `pre_event_positioning_reading[1]` | -0.010005 | 20, 2, 26 | "20 sessions", "2Q26" |

None of these six claims states a number for the cited fact at all. M8's own requirement is "no
claim cites a code-owned fact while stating a DIFFERENT number" - a claim stating no number for the
fact does not meet that description, whatever OTHER digits its sentence happens to contain.

## F. Numeric Role Semantics

New module: `backend/app/backtest/strategy_h_v2/expectation/numeric_roles.py`. It answers "does
this text restate the code-owned value" in two steps instead of one:

1. Classify every numeric span in the text by role - `VALUE_RESTATEMENT`, `FISCAL_PERIOD`
   (`Q2`, `2Q26`, `FY2026`), `SESSION_COUNT` (`63 sessions`, `6-month`), `DATE`, `IDENTIFIER`
   (`C1`, `M8`, `D4.3A`), `RANGE` (one bound of `20-30%`), or `OTHER` (a bare year).
2. Compare the cited fact's value only against the tokens classified `VALUE_RESTATEMENT`. If there
   are none, the claim is purely qualitative about the fact - not a violation, regardless of what
   other digits are present. If there are some and none match, that IS a violation.

No new number parser (brief §8): the underlying tokenizer is D3.2's `parse_numeric_tokens`
(`research/validation_v2.py`), unchanged, which already extracts currency/percent/bps/multiple/
share tokens with the file-format tolerance those batch audits already found necessary, and already
excludes ordinary dates and forward-form labels (`Q[1-4]`, `FY####`) before a bare-digit scan runs.
`numeric_roles.py` reuses that tokenizer by MASKING the additional excluded spans (reversed fiscal
periods, session counts, identifiers) before handing the text to it, so D3.2's own percent/bps/
currency logic never has to be reimplemented or forked.

One tolerance defect found and fixed in the same pass, not a new relaxation: the old formula used a
single `max(0.05, |candidate| * 0.01)` tolerance for every candidate, including the RAW (unscaled)
fact value. For a `RETURN_FRACTION` around 0.1, a 0.05-ABSOLUTE tolerance is roughly 50% relative -
tight enough numbers could pass as "restated" when they were not. `_fact_candidates` now gives the
raw candidate its own tight tolerance (`max(0.0005, |candidate| * 0.01)`) and keeps the wider 0.05
floor only for the percentage-scale and USD-scale candidates, where it was always intended. This
makes the check STRICTER, not more lenient (brief §0: no numeric ownership relaxation) - see
`test_numeric_roles.py::test_mismatched_percentage_restatement_is_rejected` and
`test_bare_fraction_restatement_is_compared`.

## G. Zero Denominator

`d4_2_contract.py` already defines `MechanicalGateStatus.NOT_EVALUATED` and `build_gates` already
treats a missing or `None` observation as `NOT_EVALUATED`, never a default PASS - this framework
predates D4.3R and needed no change. `tier_a_verdict` already requires every one of the frozen
`M_GATES` to be `PASS`, so a `NOT_EVALUATED` gate - core or not - already cannot read READY.

What D4.3A's own §O found, in prose only, was that this machinery was never applied AT THE RULE
LEVEL inside a single gate: M6 combines C1, C5 and C6 into one PASS/FAIL boolean
(`_rules("C1","C5","C6") == 0`), and C1's `0 violations` in the live run was a zero-denominator
result (0 POSITIVE/WIDE_POSITIVE outputs existed to violate it) indistinguishable, in the old
output, from a PASS with something to show for it.

`audit_strategy_h_v2_d4_2.py` now computes an explicit `denominators` block - `{rule: {eligible,
violations, status}}` for C1, C4, C5, C6 - and folds a one-line summary into M6's and M7's
`observed` text. **M6 and M7's PASS/FAIL booleans are unchanged** (still exactly
`_rules(...) == 0`, per brief §0's ban on C1/C4/Gap-semantics changes) - only the disclosed text
changes, from D4.3A's hand-written prose observation into structured, machine-readable data a
future run cannot silently lose:

```
"M6": "... | C1: NOT_EVALUATED (0 eligible, 0 violations); C5: PASS (3 eligible, 0 violations); C6: PASS (1 eligible, 0 violations)"
"M7": "... | C4: PASS (3 eligible, 0 violations)"
```

Tests: `test_a_gate_missing_from_observations_is_not_evaluated_not_a_silent_pass`,
`test_not_evaluated_core_gate_blocks_ready_even_if_every_other_gate_passes`,
`test_c1_zero_denominator_is_disclosed_while_m6_keeps_its_frozen_pass` (real-data-gated).

## H. Clean Smoke

Two defects in `d4_clean_checkout.py`, found by inspection (D4.3A §A.2), fixed here:

1. **Vacuous PASS.** `smoke_ok` was initialised `True` and only ever set `False` inside
   `if all(item['ok'] for item in out)`. A run whose imports fail skips the smoke entirely and used
   to report `smoke_ok: true` anyway. `smoke_executed` is now tracked as its own field;
   `CleanCheckoutReport.passed` requires `smoke_executed and smoke_ok`, so a skipped smoke can never
   read as a passed one. Verified directly:
   `test_skipped_smoke_is_not_a_pass`, `test_smoke_executed_but_failed_is_not_a_pass`.
2. **Interpreter choice.** The probe ran under `sys.executable`, whatever process invoked it. Under
   the repository's system Python 6 of 10 targets fail (`HOME` pinned to the temp checkout drops
   `~/.local/lib/python3.12/site-packages`) - a real environment difference, not a dependency-
   closure defect, but the vacuous-PASS bug above then hid it. `official_python()` now resolves
   `<repo>/.venv/bin/python` by default; `probe()` reports `official_interpreter`, and
   `audit_strategy_h_v2_d4_2._m12_observation` reads a non-official run as `NOT_EVALUATED` rather
   than trusting it as M12 evidence. The system-Python-vacuous-PASS bug itself is fixed (item 1
   above fixes it structurally); the interpreter CHOICE artifact remains an environment fact, not
   something this session changes.

## I. Initial Validity

D4.3A's `initial validator valid = 0/3` stands, unchanged, as a separate factual observation - this
session does not re-grade it and does not fold it into any M-gate (brief §14, and adding a gate
after seeing a result is exactly what §12/§18 forbid).

Preregistered threshold for a Tier A V3 re-run, before any result exists: **initial-valid >= 2/3 is
healthy.** This is an EXECUTION-STABILITY diagnostic, not a D4 contract gate and not a quality
signal - it says nothing about whether an expectation-gap judgement is any good, only whether the
model's first, unaided response tends to satisfy the schema and validator without a repair. If V3
observes < 2/3 again, that is a signal worth a separate investigation (is the repair loop doing
load-bearing work the initial prompt should do instead?), not a Tier A verdict input by itself.

## J. Repair Diagnostic

For a Tier A V3 re-run, report per candidate: repair count, repair reasons (schema failure code,
validator error text), and repair cost - already fully captured by the existing telemetry record
(`repair_rounds[].failure_code`, `.failure_details_before`, `.cost_usd`). Classify each repair as
either a **model contract violation** (the model produced something the contract actually forbids -
D4.3A's GOOG/BSY numeric-ownership repairs, §M) or an **audit/validator false positive** (the
validator rejected something the contract, correctly read, permits - none observed live in D4.3A;
the two known false positives were audit-layer, not validator-layer, per §C/§E above). This
classification is reporting discipline for V3, not a code change - the existing telemetry already
carries everything needed to make it.

## K. Offline Replay

`backend/app/dev/replay_d4_3r_audit_alignment.py`. Reads D4.3A's own stored `.gateaudit.json` (the
"old" side, produced by the removed detectors and never recomputed) and calls the repaired
`audit_strategy_h_v2_d4_2.audit_run` over the SAME stored records (the "new" side). Zero live calls,
zero cost - every byte replayed was already paid for.

```
old M4   FAIL  (1 asserted consensus expectation)     ->  new M4   PASS  (0)
old M8   FAIL  (6 claims restating a code-owned number) -> new M8   PASS  (0)
other gates (M1,M2,M3,M5,M6,M7,M9,M10,M11,M12)        ->  unchanged, byte-for-byte

D4.3A verdict (MECHANICAL NOT READY)  -> NOT MODIFIED, stands as historical record
D4.3R counterfactual verdict          -> MECHANICAL READY
```

Full artifact: `data/runtime/strategy_h_v2/d4_2/replay/d4_3r_audit_alignment_replay-20260929T075703Z.json`
(gitignored, reproducible by re-running the script - zero cost, zero live calls).

Per brief §17: no new real defect was uncovered by this replay that was not already named in
D4.3A's own §O diagnosis. The two known false positives disappear exactly as predicted; nothing
else moved.

## L. Tests

New: `test_numeric_roles.py` (12 tests - D4.3A's six false-positive fixtures individually, the
true/mismatched percentage-restatement pair, the bare-fraction form, a genuine violation still
caught, STATE_TOKEN unchanged, range bounds excluded) and `test_d4_3r_audit_alignment.py` (18 tests
- M4 unification against the exact D4.3A sentence and the prescribed replacement, a genuine
assertion and a negated-content assertion still caught, the consensus-available gating condition
unchanged, gate-aggregation NOT_EVALUATED semantics, clean-smoke vacuous-PASS and official-
interpreter behavior, and - gated on the gitignored stored run being present - that M4/M8 pass and
every other gate reproduces D4.3A unchanged on the real records).

Existing H-V2 regression, run from the repository root (the `pytest.ini` `pythonpath`/`testpaths`
root; running from `backend/` instead resolves several D3.3 fixture paths differently and is why
D4.3A's own count differs from this one by exactly the fixture-path effect, not by a regression):

```
before this session (backend/tests/strategy_h_v2, from repo root): 703 passed, 0 failed
after this session  (backend/tests/strategy_h_v2, from repo root): 720 passed, 1 skipped, 0 failed
```

The one new skip (`test_probe_under_a_forced_non_project_interpreter_is_marked_unofficial`) is
expected: the test process itself already runs under `.venv/bin/python`, so forcing
`sys.executable` as the "non-project" interpreter is a no-op in that environment and the test skips
rather than asserting something false. From `backend/` as cwd (D4.3A's own convention) the familiar
10 known missing-fixture failures and 38 skips reproduce identically, with only the new tests' own
pass/skip counts added on top - no prior test's outcome changed.

## M. Tier A V3 Preregistration

If a Tier A V3 re-run is authorized (a separate user decision - this document does not authorize
it), frozen BEFORE any result exists:

```
Sample:                     SCCO, GOOG, BSY (frozen, unchanged, tier_a_checksum verified)
M4 classifier:               consensus_language.asserted_consensus_findings (unified, this doc)
M8 numeric check:            numeric_roles.fact_is_restated (role-aware, this doc)
Zero-denominator semantics:  C1/C4/C5/C6 denominators reported per-rule; NOT_EVALUATED means
                              genuinely zero eligible cases, never a default PASS
Clean-smoke semantics:       smoke_executed required true for M12 PASS; non-project interpreter
                              reads NOT_EVALUATED, not PASS or FAIL
Initial-valid diagnostic:    >= 2/3 is healthy (execution-stability signal, not a gate)
Repair diagnostic:           report count/reason/cost per candidate, classified model-violation vs
                              audit-false-positive
Budget:                      UNCHANGED from D4.2 - $18.00 Tier A hard budget, same $2.00/call x 3
                              topology, same $25.458606 remaining-cap baseline this document does
                              not re-spend
Live Opus calls in V3:       0 (this document authorizes none)
```

## N. Verdict

```
H-V2-D4.3R
= READY FOR TIER A V3 RE-RUN
```

This is **not** a D4 Expectation Gap quality PASS, not an APPROVE/WATCH/REJECT, not a valuation, and
not itself an authorization to run Tier A V3 or spend anything - it says the audit layer's ASSERTED/
ABSENCE and numeric-restatement judgements are now aligned with what the production validator
already enforces, that the alignment is covered by tests, and that replaying it against the exact
records D4.3A already produced clears both known false positives without moving any other gate.
Whether to spend the (unchanged, already-fitting) $18.00 Tier A budget on an actual V3 re-run is a
separate decision for the user.

## O. Final Declarations

```
live Opus calls?                 0
live cost?                       $0.00
D4.3A verdict changed?           NO (MECHANICAL NOT READY, unedited)
C1 changed?                      NO
C4 changed?                      NO
Gap semantics changed?           NO
Consensus semantics weakened?    NO (a gap disclosed in §D, not touched, not weakened)
Numeric ownership weakened?      NO (the tolerance fix in §F is STRICTER, not looser)
Tier A V3 executed?              NO
Tier B executed?                 NO
D5?                              NO
valuation?                       NO
APPROVE/WATCH/REJECT?            NO
forward returns?                 NO
existing dirty files modified?   NO
push?                            NO
```

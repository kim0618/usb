# H-V2-D4.2 Live Contract Repair V1

**Verdict: NEEDS REVISION.** Three of the four measured D4.1 defects are repaired, tested and
confirmed on the real D4.1 bytes. The fourth - clean-checkout reproducibility - is now precisely
located and is blocked by a rule this stage was given: its remaining cause lives in two files
D4.2 is forbidden to touch. Tier A re-run is **NOT READY** until that is resolved, and the
resolution is a one-line user decision, not more engineering. Tier B was not executed and cannot
be authorized on the current budget for a second, independent reason set out in §E.

Nothing in the Expectation Gap contract's meaning changed. C1, C4, the six gap states, the
priced-in and why-now semantics, every confidence threshold and the UNKNOWN policy are byte-for-byte
V1, and `GAP_CONTRACT_VERSION` is still `h_v2_d4_gap_contract_v1` - a fact a test asserts rather
than a claim this document makes.

---

## A. D4.1 Baseline

Preserved as immutable historical evidence. Nothing below was recomputed, re-adjudicated or
relabelled.

```text
H-V2-D3.3                = PASS
H-V2-D4 CONTRACT V1      = READY FOR LIVE PILOT
H-V2-D4.1 Tier A         = MECHANICAL NOT READY
H-V2-D4.1 Tier B         = NOT EXECUTED
H-V2-D4.1                = NOT EXECUTED / NO QUALITY VERDICT
D5                       = NOT READY
```

| | |
|---|---|
| D4.1 result commit | `9f0a48b` |
| D4 contract commit | `8a54895` |
| Live cost | `$4.541394` of a `$30.00` hard cap |
| Model requested / canonical | `claude-opus-5-5` / `claude-opus-5-5` (no mismatch) |
| Candidates attempted | 2 of 3 (`SCCO`, `GOOG`; `BSY` stopped on budget) |
| Schema-valid final outputs | 0 / 2 |
| Consensus fabrications observed | 0 |
| C1 violations | 0 |
| Decision / valuation leakage | 0 |

The three zeros are diagnostic only. They say the model did not misbehave in the ways the contract
most feared; they are not evidence that the D4 contract is correct, because no candidate produced a
final output for a correctness check to run against. They are **not** promoted to D4 correctness
evidence anywhere in this document.

---

## B. Failure Attribution

Every D4.1 Tier A failure traces to the contract's enforcement layer, not to model behaviour and
not to the interpretation layer.

| Defect | Layer | Where it fired | Owner |
|---|---|---|---|
| 1. Consensus validator self-contradiction | validator execution | both candidates, final round | D4 Contract V1 |
| 2. `direction` allow-list invisible in schema | schema exposure | both candidates, initial round | D4 Contract V1 |
| 3. Per-candidate budget booked for one call | budget accounting | `BSY` never started | D4.1 contract |
| 4. D4 imports uncommitted working-tree symbols | dependency routing | clean checkout | pre-dates D4.1 |

Defect 4 is explicitly **not** attributed to D4.1. It was inherited: `research/validate.py` and
`research/schema.py` both carry uncommitted edits that D4 - and, as §I shows, committed D3.2R code -
already depend on.

---

## C. Consensus Validator Defect

### What V1 did

`check_consensus_not_fabricated` scanned every text field for eleven substrings. One of them was
`"consensus expect"`, which is a prefix of `"consensus expectations"` - the phrase in the sentence
D4 brief §18 *orders* the model to write:

```text
Available evidence does not establish consensus expectations.
```

So the rule fired on its own prescribed remedy, and its error message named that same sentence as
the fix. The bounded repair loop had nowhere to converge to. Both candidates' second repair round
reduced to exactly this one error, and neither had fabricated anything.

### What V2 does

`expectation/consensus_language.py` replaces the substring scan with a deterministic,
sentence-scoped discrimination. A sentence violates when it names a consensus referent
(consensus / analysts / Wall Street / the Street / sell-side) **and** attributes an expectation to
it - as a verb (`analysts expect`), as a quantified noun (`consensus estimates of $5.00`) or as a
comparison (`beat consensus`) - **and** does not, in that same sentence, say the evidence for it is
unavailable.

Three design decisions are load-bearing:

- **The availability vocabulary is narrow.** A blanket "contains a negation" carve-out would admit
  `Consensus does not expect growth`, which asserts a consensus view as confidently as its positive
  form. Only statements about the *evidence's availability* qualify; statements about the
  expectation's *content* never do, whichever way they point. A test pins this.
- **Scope is the sentence.** An honest opening cannot license an invented figure beside it.
- **The split is on `[.!?;]` followed by whitespace**, not on a bare `.`, so `$1.05 to $1.15 billion`
  stays one sentence rather than having a decimal point act as a scope boundary.

### No relaxation

V1's eleven substrings are kept in code as `V1_BANNED_SUBSTRINGS`, and a parametrized test places
each one in a plainly asserting sentence and requires V2 to reject it. The repair discriminates by
context; it does not loosen the prohibition. The trigger condition is also unchanged: the rule
still applies only when the code-owned bundle reports `consensus.status = SOURCE_NOT_AVAILABLE`,
which is every candidate (`earnings.status` is `UNKNOWN` for 2,010 of 2,010).

The rejection message no longer demands the sentence it rejects. It quotes the offending sentence
and states that reporting the absence is explicitly allowed.

---

## D. Direction Schema Defect

`ManagementSignalChangeV1.direction` was typed `str` and policed by a field validator holding seven
tokens. `content_only_schema()` therefore rendered:

```json
{ "title": "Direction", "type": "string" }
```

Asked for a string, the model supplied strings. D4.1's two initial responses, verbatim:

```text
INCREASED
QUANTIFIED_AND_EXTENDED_TO_2027
UP (small): 911,400 -> 915,400 -> 917,000 tonnes across three consecutive filings
```

Each was rejected by a rule the model had never been shown. That is not a strict contract; it is a
trap.

V2 declares `ManagementSignalDirection` in `expectation/contract_v2.py` and types the field with
it. The seven values are unchanged and asserted equal to the V1 literal copied from `8a54895`.
Because the field is the enum, the Pydantic validator, the generated JSON schema, the rejection
message and the tests all resolve to one definition - §7 of the brief, satisfied structurally
rather than by convention. A test reads the members out of the **generated schema**, not out of the
Python enum, because the defect was precisely that those two disagreed and only the Python side was
ever inspected.

### §8 audit: the same defect class elsewhere

Auditing every field the validator restricted but the schema exposed as a bare `str` found three
more, all repaired the same way with no change of legal values:

| Field | V1 | V2 |
|---|---|---|
| `ExpectationConflictV1.origin` | `str` + validator, 2 tokens | `ConflictOrigin` |
| `MarketExpectationEvidenceV1.consensus_status` | `str`, checked against bundle after the fact | `EvidenceAvailability` |
| `MarketExpectationEvidenceV1.estimate_revisions_status` | `str`, same | `EvidenceAvailability` |

The availability statuses deserve a note: the validator requires them to equal the bundle's own
status, a strict subset of the enum. Exposing the enum narrows the model's option space to the four
legal tokens without changing which single one passes. Its only prior feedback was a mismatch error
naming a value it had never been offered.

---

## E. Budget Contract Defect

V1 wrote one number, `D4_WORST_CASE_CANDIDATE_USD = 2.00`, and used it for two different things:
the CLI's `--max-budget-usd`, which caps **one call**, and the preflight reserve, which must cover a
**whole candidate**. A candidate is one initial call plus up to two repairs.

Measured consequence:

| Candidate | Calls | Actual cost | V1 reserve |
|---|---|---|---|
| SCCO | 3 | `$2.4343832` | `$2.00` |
| GOOG | 3 | `$2.1070108` | `$2.00` |

Both exceeded the reserve. The preflight could not have stopped the overrun - it was reserving a
third of what a candidate was permitted to spend. `BSY` was then skipped because `$4.54 + $2.00`
exceeded a `$6.00` tier budget that was itself computed from the wrong per-candidate figure.

### The V2 model

```text
per_call_max_budget_usd       = 2.00          (the CLI flag; unchanged)
max_calls_per_candidate       = 1 + MAX_REPAIR_ATTEMPTS = 3   (derived, never restated)
candidate_worst_case_budget   = 2.00 x 3 = 6.00
                                ( + the preceding D3 leg, for Tier B only )
```

The preflight, before a candidate starts:

```text
spent_so_far + candidate_worst_case_budget <= overall_hard_cap
```

Repair calls are charged to the same cap - there is no separate repair budget, because a budget with
a hidden second budget is not a cap. `initial_cost_usd`, each `repair_costs_usd[i]`,
`candidate_total_cost_usd` and `run_total_cost_usd` are all stored; D4.1 stored only the total, so
the one question its own defect raised - how much of that was repair? - could not be answered from
the record.

### What correct counting reveals

The `$30.00` overall cap is unchanged and is **not** re-authorized: `$4.541394` is already drawn
against it, leaving `$25.458606`.

| | Worst case | Fits remaining cap |
|---|---|---|
| Tier A (3 candidates x `$6.00`) | `$18.00` | **yes** |
| Tier B (6 candidates x `$7.60`) | `$45.60` | **no** |

Under honest accounting the frozen six-issuer Tier B does not fit the `$30.00` authorization at
all, and never did - V1's `assert` passed only because it multiplied by `$2.00`. This is a
consequence of counting correctly, not a D4.2 failure, and it is a user decision: a smaller Tier B
sample, a raised cap, or a lower per-call cap. D4.2 does not make that choice. Both facts are
asserted in code, so this section cannot silently go stale.

---

## F. Dirty Dependency Defect

D4 imported three symbols that a clean checkout does not have:

```text
research.validate.valid_evidence_ids    uncommitted edit to research/validate.py
research.schema.ConflictResolution      uncommitted edit to research/schema.py
research.repair.classify_repair_reason  untracked file
```

The D4 code was correct and its tests passed - on one machine's unsaved edits.

Brief §14 forbids modifying those files, so the functionality D4 needs is implemented in a
D4-owned committed module, `expectation/d4_inputs.py`, and D4 imports it from there. Duplication is
the cost and it is paid deliberately: `test_clean_checkout.py` checks each D4-owned definition
against the working-tree original whenever that original is importable - identical enum members,
identical classification over a nine-case corpus, identical evidence-id sets on real packages. A
drift is a test failure, not a silent fork.

What is **not** duplicated is `ClaimV2`. D4's contract reuses it by identity, and a D4-owned copy of
the claim contract would be exactly the semantic change this stage exists to avoid.

A source-level test now asserts that no module under `expectation/` names either uncommitted symbol
in an import. `Confidence` and `FutureBusinessStage` are still imported from `research.schema` and
that is fine - both are committed.

---

## G. Contract V2

| Component | V1 | V2 |
|---|---|---|
| D4 execution contract | `h_v2_d4_contract_v1` | `h_v2_d4_contract_v2` |
| Prompt | `h_v2_d4_expectation_gap_v1` | `h_v2_d4_expectation_gap_v2` |
| Output schema | `h_expectation_gap_analysis_v1` | `h_expectation_gap_analysis_v2` |
| Validation contract | `h_v2_d4_validation_contract_v1` | `h_v2_d4_validation_contract_v2` |
| **Gap contract** | `h_v2_d4_gap_contract_v1` | **`h_v2_d4_gap_contract_v1` - unchanged** |

The V1 strings are kept as `*_V1` constants so a stored artifact stays unambiguously attributable.
D4.1's artifact test was re-pinned to those literals; it previously compared stored records against
whatever the modules currently exported, which made it an assertion about today's code rather than
about a finished run. `gap_contract_version` is still compared against the live constant on
purpose - that line is where "the meaning did not move" would stop being true.

**V2 changes:** consensus validator execution semantics; enum exposure for `direction`, `origin`
and the two availability statuses; budget accounting; clean dependency routing; the telemetry
required for the above.

**V2 does not change:** Expectation Gap meaning, C1, C4, the gap enum, confidence thresholds,
priced-in meaning, positive/negative criteria, the UNKNOWN policy, the E1-E8 gates or their
thresholds, the Tier A sample, or the Tier B sample. No consensus provider was added. No valuation,
fair value or price target exists anywhere. No APPROVE/WATCH/REJECT. No forward returns.

---

## H. Offline Replay

`app/dev/replay_d4_1_under_v2.py` re-ran the V2 validator over the exact bytes D4.1 stored - all
six untruncated responses - against the same package, the same code-owned bundle and the same
immutable D3 output. **Zero live calls, `$0.00`.** D4.1's artifacts were verified byte-identical
before and after, by a test.

| Ticker | Response | V1 verdict | V2 errors | V2 valid |
|---|---|---|---|---|
| SCCO | initial | FAILED | 1 (direction) | no |
| SCCO | repair 1 | FAILED | **0** | **yes** |
| SCCO | repair 2 | FAILED | **0** | **yes** |
| GOOG | initial | FAILED | 3 (direction) | no |
| GOOG | repair 1 | FAILED | 1 (code-owned numeric) | no |
| GOOG | repair 2 | FAILED | **0** | **yes** |

```text
responses replayed                     6
V1 impossible-consensus failures       2
still failing consensus under V2       0
V1 direction failures                  2   (survive, correctly - see below)
V1 rejected / V2 accepts               3
```

**Defect 1 is confirmed repaired on real bytes.** Every response V1 rejected for honestly reporting
consensus evidence as absent now validates or fails only on a genuine, unrelated defect - GOOG's
repair 1 still fails for citing a code-owned 3-session benchmark-adjusted return and stating `82`,
which is a real model error and is correctly retained.

**Defect 2's rejections survive, and must.** Those bytes were produced against the V1 prompt, whose
schema listed no direction members, so the model was answering a different question. This is
recorded as a **V2 counterfactual replay** and is *not* interpreted as V2 live success. Only the
live Tier A re-run can answer that, which is why §30 puts it behind a separate authorization.

One incidental finding: D4.1's ledger stores each repair round's `failure_details_before`, so the
errors produced by the *final* response have no home in the schema. The verdict for those bytes is
still known from `validation_after`. The replay carries both and infers neither from the other.

---

## I. Clean Reproducibility

`app/dev/d4_clean_checkout.py` implements gate **M12**. It runs `git write-tree` - reading the
index, touching neither the working tree nor HEAD, no stash and no checkout - exports that tree to a
temporary directory, and imports the D4 entry points there in a subprocess. The user's uncommitted
work is never at risk from running it, which matters because that work is currently load-bearing.

Result, with all D4.2 files staged:

| Target | Clean import |
|---|---|
| `expectation/contract_v2` | **pass** |
| `expectation/consensus_language` | **pass** |
| `expectation/d4_inputs` | **pass** |
| `expectation/gap_contract` | **pass** |
| `expectation/d4_2_contract` | **pass** |
| `expectation/evidence_builder` (evidence build) | **pass** |
| `expectation/analysis_schema` | **fail** |
| `expectation/prompt` (schema generation) | **fail** |
| `expectation/validate` | **fail** |
| `app/dev/run_strategy_h_v2_d4_2` (Tier A runner) | **fail** |

Every failure is one ImportError, at one line:

```text
backend/app/backtest/strategy_h_v2/research/schema_v2.py:50
    ImportError: cannot import name 'ConflictResolution'
                 from app.backtest.strategy_h_v2.research.schema
```

### Attribution

This is **not** a residual D4 dependency. D4's own imports from `research.schema` are now
`Confidence` and `FutureBusinessStage`, both committed. The chain is:

```text
expectation/analysis_schema  ->  research/schema_v2 (committed, shipped before D4)
                             ->  research/schema    (ConflictResolution, _check_source_ids:
                                                     UNCOMMITTED)
```

Committed D3.2R code depends on two symbols that exist only in an uncommitted edit. D4 cannot route
around it without forking `ClaimV2`, which D4's own contract forbids, and D4.2 cannot repair it
without modifying `research/schema.py`, which brief §14 forbids and §33 forbids committing.

The probe therefore **reports** rather than asserts, and a test requires that any remaining blocker
be outside the `expectation/` package - so a future D4-owned regression fails loudly instead of
hiding behind this one.

### The decision this needs

M12 is a core gate and cannot be downgraded to a limitation. One user decision unblocks it:

> Commit the existing uncommitted edits to `backend/app/backtest/strategy_h_v2/research/schema.py`
> and `backend/app/backtest/strategy_h_v2/research/validate.py` (and track
> `research/repair.py`) - or explicitly waive M12 for the Tier A re-run.

No code change is needed either way. The repair is complete up to that decision.

---

## J. Tests

All 691 tests under `backend/tests/strategy_h_v2` pass. 106 are new or rewritten for D4.2.

| File | Covers | n |
|---|---|---|
| `test_consensus_language.py` | §18 - absence allowed, assertion rejected, every V1 phrase still rejected, scope, false positives | 41 |
| `test_direction_enum_contract.py` | §19 - schema enum == validator == Python enum, D4.1's three real invalid values, the §8 audit fields | 19 |
| `test_d4_budget_contract.py` | §20 - 0/1/2 repairs, preflight worst case, hard-cap stop, repair cost included, Tier A failure blocks Tier B | 15 |
| `test_clean_checkout.py` | §21 - D4-owned modules clean-import, no drift vs working-tree originals, no uncommitted symbol named | 18 |
| `test_d4_2_contract.py` | §24 - sample unchanged, gap contract unchanged, 12 gates, replay is read-only | 13 |

Two existing tests were updated and neither weakened:

- `test_analysis_schema.py::test_an_unrecognized_direction_is_rejected` - same rejection, message
  now names all seven members.
- `test_d4_1_artifacts.py::test_frozen_versions_used_with_no_fallback` - pinned to the V1 literals
  the records were produced under, which is strictly stronger than comparing against live constants.

---

## K. Tier A Re-run Contract

Frozen in `expectation/d4_2_contract.py` before the re-run exists.

- **Sample:** `SCCO`, `GOOG`, `BSY` - the same three, checksum-verified against D4.1's. Not
  re-drawn: Tier A tests wiring, not generalization, and changing its issuers would destroy the
  comparability that is the entire value of re-running a shakedown.
- **Model:** requested `claude-opus-5-5`; a canonical mismatch is invalid.
- **Budget:** `$2.00` per call, 3 calls per candidate, `$6.00` reserved per candidate, `$18.00`
  tier cap, against `$25.458606` remaining on the `$30.00` authorization.
- **Gates M1-M12:** canonical model · schema parse · consensus absence accepted · fabricated
  consensus absent · direction enum contract · C1 · C4 · numeric ownership · forbidden D5/D6 fields
  absent · raw telemetry complete · candidate budget contract · clean-checkout reproducibility.
  A gate not evaluated is `NOT_EVALUATED`, never a silent PASS.
- **Core gates:** M3, M4, M5, M9, M12 - the two D4.1 defects, fabrication, leakage, reproducibility.
  Not downgradable to limitations.
- **Verdict:** `MECHANICAL READY` requires all twelve. It is not a strategy-quality judgement in
  either direction.
- **Tier B stop rule:** runs only behind `MECHANICAL READY` **and** a budget it fits. It currently
  fails the second condition (§E), so Tier B calls must be `0` regardless of Tier A's outcome.
- **No live execution in this subphase.** `run_strategy_h_v2_d4_2.main()` raises rather than
  running; the re-run is a separate user authorization.

---

## L. Verdict

```text
H-V2-D4.2 CONTRACT REPAIR  = NEEDS REVISION
Tier A re-run              = NOT READY
Tier B                     = NOT EXECUTED
```

| Defect | Status |
|---|---|
| 1. Consensus validator self-contradiction | **repaired**, confirmed on D4.1's real bytes |
| 2. Direction schema visibility (+ 3 more of the class) | **repaired**, schema/validator/tests unified |
| 3. Budget contract | **repaired**, and it revealed that Tier B does not fit the cap |
| 4. Clean-checkout reproducibility | **located exactly**, blocked on a user decision (§I) |

`NEEDS REVISION` rather than `READY FOR TIER A RE-RUN` is a deliberate call. M12 is a core gate;
the clean-checkout import fails; and the cause is outside what this stage is permitted to change.
Reporting `READY` would mean either waiving a core gate quietly or weakening the probe until it
agreed - and the whole reason D4.1 produced no quality verdict is that a contract was reported as
ready when its enforcement layer had never been executed.

What remains is one decision (§I), not more engineering.

### Unchanged by this stage

```text
D4.1 verdict modified?                          NO
C1 changed?                                     NO
C4 changed?                                     NO
Gap semantics changed?                          NO
Consensus provider added?                       NO
Valuation executed?                             NO
APPROVE/WATCH/REJECT?                           NO
Forward returns?                                NO
Live Opus calls in contract-repair phase?       NO  (0 calls, $0.00)
Existing dirty files modified?                  NO
Push?                                           NO
```

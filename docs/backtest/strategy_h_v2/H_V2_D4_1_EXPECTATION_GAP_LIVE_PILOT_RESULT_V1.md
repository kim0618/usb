# H-V2-D4.1: Expectation Gap Engine Live Pilot Result V1

Status: **TIER A EXECUTED. TIER B NOT EXECUTED.**
Tier A verdict: **MECHANICAL NOT READY.**
Live cost: **$4.54** of a **$30.00** hard cap.

D4.1 ran Tier A, the mechanical shakedown, and stopped there. Tier A is the gate that authorizes
Tier B, and it did not pass: **0 of 2 attempted candidates produced a schema-valid final output**,
and the reason is not model behaviour. Two defects in the frozen D4 contract's own enforcement layer
made a valid output unreachable, one of them by instructing the model to write the exact sentence
that causes the rejection.

Nothing was repaired and re-run. Per brief §27 a structural defect found in a batch is recorded and
the batch stops; fixing it here would produce a different research version wearing this one's name.

---

## A. Repository State

Captured at the start of the run and unchanged by it.

```
branch                  main
HEAD                    8a548952a7845ee99206a74e0679146aaa2f7976
                        ("feat(strategy-h-v2): implement expectation gap engine")
origin/main             26 commits behind HEAD (0 behind, 26 ahead) - nothing pushed
dirty (modified)        28 files
dirty (untracked)       ~150 paths
```

HEAD is exactly the D4 contract commit the brief names (`8a54895`). Every pre-existing dirty file
was left untouched, including the session-external `frontend/next-env.d.ts`, which still shows as
modified and was neither edited nor staged.

### One pre-existing condition that has to be stated

`backend/app/backtest/strategy_h_v2/research/validate.py` is dirty, and the uncommitted part of it
is where `valid_evidence_ids()` is defined. `expectation/validate.py` - committed at HEAD - imports
that function. **The committed D4 package therefore does not import on a clean checkout of HEAD; it
works only against this working tree.** This is not something D4.1 introduced and not something
D4.1 may fix (the file is a pre-existing dirty file), but a reader who checks out `8a54895` and
finds D4 broken should know why.

## B. Frozen Contract

Read and used unmodified from `docs/backtest/strategy_h_v2/H_V2_D4_EXPECTATION_GAP_ENGINE_V1.md`
and `backend/app/backtest/strategy_h_v2/expectation/`:

```
contract          h_v2_d4_1_pilot_contract_v1
prompt            h_v2_d4_expectation_gap_v1
schema            h_expectation_gap_analysis_v1
gap contract      h_v2_d4_gap_contract_v1
validation        h_v2_d4_validation_contract_v1
ledger            h_v2_d4_ledger_v1
```

No file under `expectation/` was modified. C1, C4, the confidence ceiling, the gap enum and the
consensus state were not touched. The E1-E8 thresholds are read from `d4_1_contract.py` and no
threshold was changed before, during or after the run.

New files written for execution (orchestration and audit only, never contract):

```
backend/app/dev/run_strategy_h_v2_d4_1.py           the live runner
backend/app/dev/audit_strategy_h_v2_d4_1.py         independent mechanical E1-E8 audit
backend/app/dev/manual_audit_strategy_h_v2_d4_1.py  manual-audit harness + frozen selection rule
backend/tests/strategy_h_v2/expectation/
    test_d4_1_artifacts.py                          post-execution artifact integrity (§28)
```

### The manual-audit rule, frozen before any result existed

`d4_1_contract.py` froze the sample, the budget and the gates, but - unlike `d3_3_contract.py`,
which froze `D3_3_MANUAL_AUDIT_TICKERS` - it defined **no** manual-audit subset. Brief §22 therefore
requires one frozen deterministically before results are read, and §0 forbids editing the frozen
preregistration. The rule was written as an addendum in
`manual_audit_strategy_h_v2_d4_1.py` at **2026-09-29T04:59:33Z**, when zero D4 analysis records
existed on disk (only one expectation-evidence bundle had been written):

- companies: all six Tier B issuers (auditing every one is strictly stronger than any subset rule)
- always audited: every `gap_rationale` claim, the whole `priced_in_assessment`, every `why_now`
  item, every `guidance_assessments` / `result_vs_guidance` / `management_signal_changes` entry,
  every `conflicts` entry
- plus the first 6 remaining material claims containing a digit, ordered by
  `sha256("H_V2_D4_1_MANUAL_AUDIT_V1:<ticker>|<json path>")` ascending

It was never applied, because Tier B never ran.

## C. Tier A Sample

Used exactly as frozen; nothing was selected, substituted or replaced.

```
TIER_A_TICKERS    ("SCCO", "GOOG", "BSY")
checksum          d47cc4fd30fd3d6f04738b811e401547abedf57c195f0c4c2f1b65af34e2f6ab   (matches)
regenerated       regenerate_tier_a_from_d3_3() -> ('SCCO', 'GOOG', 'BSY')           (identical)
D3 input          D3.3 run D3_3-20260929T021659Z, all three final_status=OK
```

The sample was re-derived from the declared seeded-hash convention rather than only checked against
a hash of itself, and the regeneration matched the frozen literal exactly.

## D. Tier A Execution

```
run_id                     D4_1_A-20260929T045619Z
model requested            claude-opus-5-5
canonical model reported   claude-opus-5-5   (both candidates, model_mismatch=False)
sample size                3
attempted                  2
stopped early at           BSY (budget)
tier hard budget           $6.00
tier cost                  $4.541394
elapsed                    662s
```

| ticker | final status | repair rounds | D4 cost | canonical model |
|---|---|---|---|---|
| SCCO | `SCHEMA_VALIDATION_FAILED` | 2 (exhausted) | $2.4343832 | `claude-opus-5-5` |
| GOOG | `SCHEMA_VALIDATION_FAILED` | 2 (exhausted) | $2.1070108 | `claude-opus-5-5` |
| BSY | not attempted | - | $0 | - |

BSY was skipped by the pre-call budget check, correctly and before the call: `$4.541394 + $2.00 >
$6.00`. Nothing was retried to fill the remaining budget.

### What actually worked

Everything upstream of the two defects did. The prompt assembled and stayed inside its budgets
(system 35,742 chars; user 171,843 chars for the largest candidate, with the D3 output well under
the 120,000-char limit that would otherwise raise). The evidence bundle built from real D2.1
packages and a real 501-session price panel. The PIT leak check passed on both candidates. Both
responses parsed as JSON on the first attempt (`initial_parse_status=PARSED`), populated the full
`HExpectationGapAnalysisV1` shape, and were stored immutably with full untruncated telemetry.

### Defect 1 - the consensus check rejects the sentence it demands (blocking, unsatisfiable)

`validate.check_consensus_not_fabricated` scans every text field for a list of plain substrings when
the bundle reports `consensus = SOURCE_NOT_AVAILABLE`. One of them is:

```
"consensus expect"
```

The replacement sentence the D4 contract (§J) mandates, and that the prompt instructs the model to
write in place of any consensus assertion, is:

```
Available evidence does not establish consensus expectations.
```

`"consensus expectations"` contains `"consensus expect"`. The rule therefore fires on its own
prescribed remedy, and the error message it emits tells the model to write the very sentence that
triggered it:

> text asserts a consensus expectation ('consensus expect') while the expectation bundle reports
> consensus SOURCE_NOT_AVAILABLE - brief §18 requires 'Available evidence does not establish
> consensus expectations.' instead

This was verified, not inferred. Re-validating each candidate's **final** repair response offline
returns **exactly one** error, this one, for both candidates. Searching those responses for
`consensus expect` finds occurrences only inside the mandated sentence - in `gap_rationale`,
in `limitations`, and in a claim, each time as the honest statement of absence the contract asks
for. There is no fabricated consensus anywhere in either output.

The consequence is not a lost round, it is an unreachable state. A model that obeys the instruction
is rejected; a model that stops obeying it has fabricated a consensus. The bounded repair loop is
being driven against an instruction that cannot be satisfied, so it exhausts by construction.

### Defect 2 - `direction`'s allowed values are enforced but never shown (blocking, first round)

`ManagementSignalChangeV1.direction` is typed `str` and restricted by a field validator to seven
tokens (`STRENGTHENED / WEAKENED / INTRODUCED / WITHDRAWN / BROUGHT_FORWARD / DELAYED / UNCHANGED`).
Because it is a plain `str` rather than a `StrEnum`, the JSON schema the prompt shows the model
renders as:

```json
{"title": "Direction", "type": "string"}
```

The seven tokens appear nowhere in the schema the model is given. Both candidates' initial responses
failed on exactly this and on nothing else:

```
SCCO  1 error:  direction = 'UP (small): 911,400 -> 915,400 -> 917,000 tonnes across three ...'
GOOG  3 errors: direction = 'INCREASED', 'QUANTIFIED_AND_EXTENDED_TO_2027', 'INCREASED'
```

Those are reasonable strings for a field described to the model only as "a string". The model was
not ignoring a constraint; it was never given one. Every other enum in the schema is a real
`StrEnum` and rendered its members, and no other enum was violated.

The cost of this is concrete: it consumed the first repair round on both candidates - roughly a
third of each candidate's spend - before any content rule was reached at all.

### What the repair loop revealed while it still had rounds

GOOG's second round surfaced, then fixed, one code-owned numeric defect (a claim citing
`price_reaction.…benchmark_adjusted_3d` while stating `82`). By the final response that defect was
gone. So the repair loop demonstrably works on real defects; it failed only against the one
instruction that cannot be satisfied.

## E. Tier A Verdict

```
TIER A = MECHANICAL NOT READY
```

Gate evaluation, computed by `d4_1_contract.evaluate_d4_1_gates(tier="A")` over the audit's counts,
with no threshold altered:

| gate | threshold | observed | status |
|---|---|---|---|
| E1 schema validity | ≥ 95% | 0/2 = 0.0% | **FAIL** |
| E2 material gap claims source-linked | == 0 | not evaluated in tier A | NOT_EVALUATED |
| E3 fabricated consensus | == 0 | not evaluated in tier A | NOT_EVALUATED |
| E4 future source / price leakage | == 0 | 0 | PASS (vacuous) |
| E5 code-owned numeric integrity | == 0 | 0 | PASS (vacuous) |
| E6 UNKNOWN discipline | == 0 | not evaluated in tier A | NOT_EVALUATED |
| E7 investment decision leakage | == 0 | 0 | PASS (vacuous) |
| E8 priced-in claims fully supported | == 0 | not evaluated in tier A | NOT_EVALUATED |

`d4_1_verdict(gates, tier="A")` returns `FAIL`. Brief §8 asks Tier A for a mechanical readiness
statement rather than a strategy verdict, and that statement is **MECHANICAL NOT READY**.

**E4, E5 and E7 pass vacuously and must not be read as evidence.** They are measured on final
outputs, and there are no final outputs; zero defects over zero documents is arithmetic, not a
result. Brief §8's failure list names "schema cannot parse normal Opus output" as a
MECHANICAL-NOT-READY condition, and that is what happened - with the sharper detail that the schema
parsed the output fine and the *contract rules* were the unsatisfiable part.

## F. Tier B Sample

**NOT EXECUTED.** Brief §9 authorizes Tier B only on `MECHANICAL READY`, and orders an immediate
stop otherwise.

The sample was nonetheless verified before Tier A ran, so the verification is recorded:

```
TIER_B_SAMPLE    IDCC(0001405495,FULL) DORM(0000868780,FULL) FRPT(0001611647,FULL)
                 TG(0000850429,CORE)   CRK(0000023194,CORE)  SPSC(0001092699,CORE)
checksum         acf2da18c8792f599c2435747200625ca783dfa0eb4f65140d590b5c13c825b0   (matches)
regenerated      regenerate_tier_b_from_universe() -> identical tuple, entry for entry
excluded CIKs    48
intersection     {} - disjoint, by CIK and not by ticker
```

No ticker substitution was made or considered. A D2.1 package and a full 501-session price panel
exist for all six, so the sample is executable the moment the tier is authorized.

## G. End-to-End Chain

The Tier B chain (`D2/D2.1 -> D3 Prompt V2 -> D3 Research Object -> Expectation Evidence Builder ->
D4 Opus Analysis -> HExpectationGapAnalysisV1`) was **wired and exercised offline with a stub model
at $0**, then never run live. Its D3 leg calls D3.3's own `research_one_v3` under Prompt V2 /
Schema V2 / Validation V2, imported rather than restated, so the D3 output contract could not drift
to make D4 pass.

The Tier A chain (`D3.3 Research Object -> Expectation Evidence Builder -> D4 Opus Analysis`) ran
live end to end and reached the final validation step on both candidates.

## H. Model Verification

```
requested         claude-opus-5-5
canonical         claude-opus-5-5   (reported by the model in every response, both candidates)
model_mismatch    False
fallback          none - MODEL is a module constant with no fallback path
```

Verified on every stored record by `test_canonical_model_matches_the_requested_model`, which asserts
`canonical_model is not None` so that a missing field cannot pass as agreement.

## I. Expectation Evidence (Tier A bundles)

Brief §13 asks for this per Tier B company; Tier B did not run, so what exists is the Tier A
bundles. They are reported as mechanical output of the builder, not as findings about the companies.

| | SCCO | GOOG |
|---|---|---|
| decision_time | 2026-09-28T05:49:37Z | 2026-09-28T05:49:37Z |
| guidance | `AVAILABLE`, 40 excerpts | `AVAILABLE`, 40 excerpts |
| prior guidance vs actual (earnings material) | `AVAILABLE`, 2 excerpts | **`NOT_FOUND_FOR_CANDIDATE`, 0** |
| management expectation signals | `AVAILABLE`, 34 excerpts | `AVAILABLE`, 23 excerpts |
| price reaction | 15 official events | 18 official events |
| event-alignment ambiguity | 1 of 15 (6.7%) | **11 of 18 (61.1%)** |
| pre-event price context | complete | complete |
| historical multiple stub | `NOT_COMPUTABLE` | `NOT_COMPUTABLE` |
| market cap / EV | $158.4B / $160.8B | **`None`** (net debt −$53.9B only) |
| consensus | `SOURCE_NOT_AVAILABLE` | `SOURCE_NOT_AVAILABLE` |
| estimate revisions | `SOURCE_NOT_AVAILABLE` | `SOURCE_NOT_AVAILABLE` |
| unknown fields | 3 | 4 |

Two of these deserve to be noticed rather than tabulated:

- GOOG has **no** earnings material with guidance language, so its result-versus-prior-guidance
  side is empty. The bundle says so (`NOT_FOUND_FOR_CANDIDATE`) instead of reporting an empty
  `AVAILABLE`, which is exactly the absence-must-state-why rule working.
- GOOG's market cap is `None` because `shares_outstanding` is absent from where the builder reads
  it for this candidate. The stub reports `PARTIAL` rather than computing an enterprise value from
  a missing share count.
- GOOG's 61.1% event-alignment ambiguity sits well above the 41.3% measured across the 4,664-event
  D2.1 population. On a single candidate that is an observation, not a finding.

## J. Consensus Availability

```
consensus.status           = SOURCE_NOT_AVAILABLE      (both candidates)
estimate_revisions.status  = SOURCE_NOT_AVAILABLE      (both candidates)
```

Both models reported the statuses correctly and neither ever contradicted the bundle. **Fabricated
consensus: 0.** Every occurrence of consensus vocabulary in either final response is inside the
contract's own mandated absence sentence. No "Analysts expect", no "Wall Street expects", no "the
market expects X growth", no "consensus implies" appears anywhere in either output.

The irony is the finding: the only thing blocking these outputs is a rule designed to catch
fabricated consensus, firing on two candidates that fabricated none.

## K. Gap Results

**None.** No candidate produced a final output, so there is no gap state to report.

Per brief §4, Tier A results may not be used as D4 quality evidence or as gap-distribution
evidence, so the gap, confidence and priced-in values present in the *rejected* responses are
deliberately not reproduced here. Stating them would be exactly the misuse §4 forbids, and they are
in any case values from documents the contract rejected.

What can be said mechanically: the gap enum, the confidence enum and the priced-in enum were all
populated with in-contract values in both responses, and rule C5 (gap UNKNOWN ⟺ confidence UNKNOWN)
held in both - neither was ever the reason for a rejection.

## L. Confidence

No final output, so no confidence was recorded and no ceiling was applied to a stored result.

C4 was never tested against a real output. What is known is structural and unchanged: consensus is
`SOURCE_NOT_AVAILABLE` for both candidates, so the ceiling `MEDIUM` would have applied to both.
`HIGH` never appeared as a *rejection reason* in either candidate's error list, meaning neither
model attempted a confidence above the ceiling. Nothing was silently downgraded; nothing was
downgraded at all.

## M. Priced-in

No final output, so no priced-in assessment was recorded. E8 is `NOT_EVALUATED`, not passed.
No priced-in claim was ever rejected for missing `evidence_ids`, `confidence` or `limitations` on
either candidate, which means the schema's own requirement was met in both responses; it was simply
never adjudicated as a gate.

## N. Why Now

No final output, so no why-now item was recorded. No why-now item was rejected for a missing source
or a missing claim on either candidate. No "why buy now" language was flagged by any check.

## O. UNKNOWN / Conflicts

No final output. The independent audit's UNKNOWN-discipline re-check (C1, C5, C6 and the ceiling,
restated in `audit_strategy_h_v2_d4_1.py` rather than imported) ran over zero final outputs and
returned zero violations, which is arithmetic, not evidence.

One diagnostic is real: **rule C1 was never the reason for a rejection**, on either candidate, in
any round. Neither model ever asserted a positive gap without non-price expectation evidence.

## P. Manual Audit

**NOT PERFORMED** - `NOT_EVALUATED`, never a silent pass. It applies to Tier B, and Tier B did not
run. The selection rule was frozen before any result existed (§B above) and the harness is written
and imports cleanly, so the audit is ready to run when Tier B is.

## Q. E1-E8 Gates

Tier A's evaluated set is E1, E4, E5, E7 (§E above). E2, E3, E6 and E8 are `NOT_EVALUATED` in
Tier A **by the frozen contract**, not by omission - Tier A's issuers are not unseen, so a content
result from them would not mean what a gate result is supposed to mean.

No threshold was changed at any point. The gate function, thresholds and tier gate-sets were read
from `d4_1_contract.py` unmodified, and the audit that feeds them restates every defect definition
independently of the validator that enforced it.

One methodological point the audit file states and this document repeats, because it matters for
how any future PASS should be read: **E3, E5 and E7 are enforced by the validator, so any final
output that exists at all has already passed them.** Measured on final outputs alone they are
near-tautological. The number that carries behavioural information is the same defect counted on
the *initial* response, before repair. For this run those are:

| defect class | initial responses (SCCO, GOOG) |
|---|---|
| `direction` outside the enforced allow-list | 1, 3 |
| code-owned numeric defect | 0, 1 (appeared in round 2, fixed by round 3) |
| fabricated consensus | 0, 0 |
| decision/valuation leakage | 0, 0 |
| unresolvable citation | 0, 0 |

## R. Costs

```
Tier A live cost        $4.541394      (SCCO $2.4343832 + GOOG $2.1070108)
Tier B live cost        $0.00          (not executed)
total live cost         $4.541394
tier A hard budget      $6.00          respected; BSY stopped before its call
D4.1 hard cap           $30.00         never approached
remaining               $25.46         not spent, not reallocated
```

Brief §25 forbids burning the remainder after a Tier A mechanical failure, and it was not burned.

### A budget-contract finding

`D4_WORST_CASE_CANDIDATE_USD = 2.00` is a **per-candidate** ceiling, but the only enforcement is
`--max-budget-usd 2.00` on each **call**. A candidate makes up to three calls (initial + two
repairs), so its true worst case is about $6.00, three times the figure the budget arithmetic is
built on. It showed up immediately: SCCO cost $2.43, over the per-candidate worst case, on its
first candidate. The pre-call check still did its job - it stopped before BSY rather than after -
but the frozen assertion that the sample "fits its own worst case inside the budget" is computed
from a number the enforcement does not actually bound. Recorded, not fixed.

## S. Tests

```
pre-execution    backend/tests/strategy_h_v2   568 passed
post-execution   backend/tests/strategy_h_v2   585 passed   (568 + 17 new artifact tests)
```

`test_d4_1_artifacts.py` adds the §28 checks over the real stored artifacts: artifact integrity,
telemetry completeness, raw-response full-fidelity (checksum recomputed over the stored text),
analysis immutability, checksum integrity, canonical-model verification, frozen-version usage,
budget ceiling, consensus `SOURCE_NOT_AVAILABLE`, C1, C4, code-owned numeric ownership, the gap
enum, C5, the WIDE_POSITIVE deferred conjunct, and the absence of any prohibited decision field at
any depth.

Several of those assert over final outputs, of which this run produced none, so they pass
vacuously. The ones that ran against real data are the telemetry, immutability, checksum,
canonical-model, frozen-version and budget checks - and those passed on real records.

No pre-existing regression was modified or skipped.

### A carried-forward telemetry limitation, confirmed here

`research_attempt_v2.extract_usage` records `input_tokens = 2` for both candidates against an
~208,000-character prompt, with `output_tokens` at 17,175 and 18,040. The key path is present and
the value is what the key says; it is small because prompt caching puts the bulk under
`cache_creation_input_tokens` / `cache_read_input_tokens`, which the function does not read. The
ledger's docstring said this guess should be confirmed against the first real response and
corrected if wrong. D3.3's own records show the same `input_tokens=2`, so this was already visible
there and was not corrected. It is recorded here and **not** fixed, per §27.

## T. Limitations

1. **The pilot measured almost none of what it was built to measure.** The purpose stated in §31 of
   the brief - whether Opus separates real business change from available expectation evidence, and
   whether it invents expectations when no consensus exists - is a Tier B question. Tier B did not
   run. The one signal that does bear on it is negative-evidence-shaped and weak: across six live
   responses on two companies, consensus was never fabricated and C1 was never violated.
2. **Two candidates is a small mechanical sample**, and BSY was never attempted at all.
3. **E4, E5, E7 passing is arithmetic over an empty set**, not evidence.
4. **Tier A can never be quality evidence** by construction, and none of it is reported as such.
5. **The two defects are found, not diagnosed to completion.** Defect 1 has an obvious shape; its
   correct repair - a narrower assertion pattern, an explicit whitelist of the mandated sentence, or
   both - is a contract decision for a new version, not a judgement this document should make.
6. Whether a valid D4 output is reachable *once the two defects are fixed* is untested. Both final
   responses reduced to exactly one error, which is suggestive and nothing more.

## U. Verdict

```
TIER A     = MECHANICAL NOT READY
H-V2-D4.1  = NOT EXECUTED  (Tier B unauthorized by Tier A; no D4.1 PASS/FAIL is claimable)
```

Brief §23 asks for a Tier B verdict of `PASS` / `PASS WITH LIMITATIONS` / `FAIL`. None of the three
is available: each is a statement about Tier B's six unseen issuers, and no live call was made
against any of them. Reporting `FAIL` would attribute a Tier A contract defect to a tier that never
ran; reporting anything else would be worse. The honest value is `NOT EXECUTED`.

The Expectation Gap Engine is not shown to be wrong. It is shown to be **unreachable through its
own validator**, on two candidates whose evidence discipline was, as far as six live responses can
say, intact.

## V. D5 Authorization

```
D5 VALUATION ENGINE = NOT READY FOR CONTRACT DESIGN
```

Brief §24 conditions D5 on Tier B returning `PASS` or an acceptable `PASS WITH LIMITATIONS`. Tier B
returned nothing. The condition is not met and is not close to met.

### What the next step actually is

Not D5, and not a re-run of D4.1 as it stands. In order:

1. A new D4 contract version that fixes Defect 1 and Defect 2 - each in the layer that owns it, the
   validator's assertion patterns and the schema's `direction` type. This lands as a version, never
   as an edit to the frozen one.
2. A fresh Tier A against that version, on the same three frozen issuers, budgeted with a
   per-candidate ceiling that reflects the three calls a candidate can actually make.
3. Tier B, only on `MECHANICAL READY`.

### Declaration

```
D4 model requested              = Claude Opus 5.5 (claude-opus-5-5)
canonical model                 = claude-opus-5-5 (verified every response, mismatch False)
Tier A live cost                = $4.541394
Tier B live cost                = $0.00 (not executed)
total live cost                 = $4.541394
hard cap                        = $30.00
D4 contract changed             = NO
C1 changed                      = NO
C4 changed                      = NO
consensus fabricated            = NO
valuation executed              = NO
APPROVE / WATCH / REJECT        = NO
forward returns read            = NO
sample substituted              = NO
threshold changed after results = NO
defect repaired and re-run      = NO
existing dirty files modified   = NO
pushed                          = NO
```

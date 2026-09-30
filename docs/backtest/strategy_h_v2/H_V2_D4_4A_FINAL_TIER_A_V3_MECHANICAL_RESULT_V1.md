# H-V2-D4.4A - Final Tier A V3 Mechanical Live Validation

```
H-V2-D4 Final Tier A V3
= MECHANICAL READY

Initial Validity
= 1 / 3 = UNSTABLE

Tier B
= NOT AUTO-AUTHORIZED   (preregistered Case B: D4 prompt/schema stability)

D5
= NOT READY
```

Live run `D4_2_A-20260930T012115Z`, three candidates, `$3.8872704` of an `$18.00` hard budget. All
twelve mechanical gates M1-M12 read PASS, so the frozen Expectation Gap contract can now be carried
to the model and back without an enforcement-layer defect - which is the only thing this tier can
conclude. It is not a statement about whether an expectation gap means anything: Tier A's three
issuers are three of D3.3's twelve and their outputs were read closely while D4 was designed.

The preregistered Initial Validity diagnostic (D4.3R2 §H, frozen before this run existed) reads
**UNSTABLE**, and its own frozen rule therefore blocks automatic Tier B progression regardless of the
mechanical verdict. §S records why, and §16 of the brief forbids generating a repair step for it in
this session.

`H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md`'s verdict - `Tier A = MECHANICAL NOT READY` - remains
standing and unedited as immutable historical evidence. Nothing in this document supersedes it; this
is a separate, later run under the same frozen contract.

---

## A. Repository State

Measured at preflight, and re-measured after the run because HEAD moved during it.

```
branch                          main
HEAD at preflight               0c2c540a4be92342a6100cc3f732e031d768da84
HEAD after the run              9f9c89e708e95dd2560831627b23938dbe61dfd3
origin/main vs HEAD (after)     behind 0 / ahead 2
staged files                    0
modified files                  22  (24 at preflight)
untracked files                 many
H-V2 related dirty files        0   (at preflight AND after the run)
```

**HEAD moved during the session and this is reported rather than smoothed over.** Four commits
landed between the preflight reading and the audit - `a1ff159`, `fb10239`, `f08720d`, `9f9c89e`, all
crypto or frontend - evidently from a concurrent session. `origin/main` also advanced, so the
ahead-count fell from 4 to 2 without this session pushing anything.

The material question is whether that movement touched what this run measures. It did not:

```
git diff --stat 0c2c540..9f9c89e -- <all four H-V2 paths>   -> empty
git diff --stat 714841e..HEAD     -- <all four H-V2 paths>   -> empty
```

H-V2 code, tests and docs are byte-identical to the checkpoint. The only visible side effect is the
M12 probe's tree id, which is the id of the git index and so necessarily changed when unrelated files
were committed: `168e8d12eb1a359c3b91deaee4f919b10170477a` at preflight,
`1e39e42b17534ae74d2e9d9c532b194b8a13798f` at audit time. Both trees contain the same H-V2 content.

No existing unrelated dirty file was modified, staged or deleted at any point.

## B. Checkpoint / Frozen Contract

```
checkpoint                      714841e  research(strategy-h-v2): checkpoint pre-tier-a validation state
checkpoint exists?              YES
H semantics changed since it?   NO (empty diff, §A)
```

The two pieces of committed history this run requires are both ancestors of the checkpoint, so a
clean checkout of it carries them:

```
31781e0  fix(strategy-h-v2): complete market-expectation detection   -> R2 consensus classifier
5ece370  fix(strategy-h-v2): align D4 audit with runtime semantics    -> D4.3R audit semantics
```

Frozen contract identifiers, read out of the repository rather than transcribed from a document:

```
D4 Contract V2                  h_v2_d4_contract_v2
D4.2 contract                   h_v2_d4_2_contract_repair_v1
Prompt version                  h_v2_d4_expectation_gap_v2
Schema version                  h_expectation_gap_analysis_v2
Validation V2                   h_v2_d4_validation_contract_v2
Gap contract                    h_v2_d4_gap_contract_v1
Consensus classifier R2         consensus_language.asserted_consensus_findings
M8 numeric roles                numeric_roles.fact_is_restated
Telemetry                       RawResponseRecordV1 / D4AnalysisRecordV1 (LEDGER_SCHEMA_VERSION)
Budget contract V2              CandidateBudgetContract, per_call $2.00 x 3 calls
```

The gap contract legitimately still carries its V1 string. A reader seeing `gap_contract_v1` beside
`contract_v2` is seeing the intended fact: V2 changed enum exposure, budget arithmetic and dependency
routing, and did not move the meaning of a gap. No V1 fallback was used anywhere.

## C. Sample Integrity

```
sample            SCCO, GOOG, BSY
tier_a_checksum   d47cc4fd30fd3d6f04738b811e401547abedf57c195f0c4c2f1b65af34e2f6ab   verified equal
replaced          0
added             0
removed           0
```

| ticker | CIK | D3 attempt id | input package checksum |
|---|---|---|---|
| SCCO | 0001001838 | SCCO-75bdb891-906c-4654-bb85-9e9eb2e1a956 | 00742863bf3d27ab58db21d1e922eafe58c716315cd07e71974ed756b4f67df5 |
| GOOG | 0001652044 | GOOG-5d94b614-1d0a-4eb6-9511-65a256857cec | ce72136b1a157241d2e76d385d73da0772abcb705d31d66a3f0479ed2da881cb |
| BSY | 0001031308 | BSY-9d4aedb1-b940-48bb-b090-9a68151b801c | 60f51b8b48c064870428599dcd31d59c5308ee7c58e02a306cd098dbf6409520 |

The D3 attempt ids are the same three records D4.3A consumed, so V3 and D4.3A differ in the D4 call
and in nothing upstream of it.

## D. Clean Checkout

Run under the official project interpreter before any live spend, and again inside the audit.

```
interpreter             /home/tjd618/usb/.venv/bin/python
official_interpreter    true
import targets          10 / 10 OK
smoke_executed          true
smoke_status            PASS
smoke_error             (none)
passed                  true
```

`smoke_executed` is tracked separately from `smoke_ok` (the D4.3R repair), so a skipped smoke cannot
read as a passed one. Both readings are from a `git write-tree` export of the index, which touches
neither the working tree nor HEAD, so the user's uncommitted work was never at risk.

## E. Input Preflight

Checked for all three before the first live call, with zero model calls spent on the check.

| check | SCCO | GOOG | BSY |
|---|---|---|---|
| D3 research object exists | YES | YES | YES |
| D3 checksum recomputes to the stored value | YES | YES | YES |
| input package file exists | YES | YES | YES |
| evidence chunks / sources | 347 / 17 | 228 / 20 | 201 / 13 |
| every chunk's source_id resolves in the manifest | YES | YES | YES |
| ExpectationEvidence bundle build | OK | OK | OK |
| D4 prompt build (system / user chars) | 38,197 / 169,973 | 38,197 / 176,374 | 38,197 / 136,355 |

All mandatory runtime paths existed:

```
packages        data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages
D3 attempts     data/runtime/strategy_h_v2/d3_3/attempts
daily panel     data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily
```

No candidate was substituted for another at any point.

## F. Model Verification

```
requested model      claude-opus-5-5   (all calls)
canonical model      claude-opus-5-5   (all calls)
model_mismatch       false   3 / 3
fallback used        NO
```

`canonicalModel` was read from each response's `modelUsage` block and stored per record, not assumed.

## G. Budget

The brief's §8 named an `$8.00` hard cap. That figure and the brief's §3 ban on removing a sample
member cannot both hold, and the conflict was found and reported to the user **before any live spend**
rather than discovered halfway through the run:

```
frozen candidate_worst_case = per_call $2.00 x 3 calls = $6.00
preflight rule              = spent_so_far + 6.00 <= cap, checked BEFORE a candidate starts

simulated against D4.3A's measured costs, cap $8.00:
  SCCO  admit   0.0000 + 6.00 = 6.0000 <= 8.00
  GOOG  admit   1.9064 + 6.00 = 7.9063 <= 8.00      (margin $0.094)
  BSY   BLOCK   3.5536 + 6.00 = 9.5536 >  8.00      -> 2/3, reproducing D4.1's BSY block
```

The user was shown that arithmetic and authorized the already-frozen Tier A budget instead. This is
not a budget expansion invented for this run: `$18.00` is `d4_2_contract.TIER_A_HARD_BUDGET_USD`,
computed as `TIER_A_N x $6.00`, and preregistered verbatim in D4.3R2 §I as "Budget: UNCHANGED -
$18.00 Tier A hard budget". The driver script asserts `HARD_CAP == TIER_A_HARD_BUDGET_USD`, so the
cap is checked by code rather than by intention. No cap was raised during the run and none was raised
automatically.

Per-candidate preflight, reconstructed exactly from the manifest's ordered candidate spends:

| order | candidate | per_call_cap | candidate_worst_case | spent_so_far | reserve check | remaining_budget after |
|---|---|---|---|---|---|---|
| 1 | SCCO | $2.00 | $6.00 | $0.0000000 | 6.0000 <= 18.00 | $16.3976614 |
| 2 | GOOG | $2.00 | $6.00 | $1.6023386 | 7.6023 <= 18.00 | $14.9462362 |
| 3 | BSY | $2.00 | $6.00 | $3.0537638 | 9.0538 <= 18.00 | $14.1127296 |

```
stopped_early        null
budget_exhausted     false
run total            $3.8872704   of $18.00 (21.6%)
max candidate        $1.6023386   of the $6.00 reserve (26.7%)
```

M11 confirms every candidate finished inside its own reserve and the run inside the remaining cap.

## H. Execution

```
run_id            D4_2_A-20260930T012115Z
attempted         3 / 3
final_status      OK, OK, OK
wall clock        01:21:24Z -> 01:29:56Z   (8m 32s)
```

| candidate | started | completed | duration | calls | final_status |
|---|---|---|---|---|---|
| SCCO | 01:21:24.620944Z | 01:24:43.957087Z | 3m 19s | 2 | OK |
| GOOG | 01:24:44.041688Z | 01:28:06.478168Z | 3m 22s | 2 | OK |
| BSY | 01:28:06.560166Z | 01:29:55.686682Z | 1m 49s | 1 | OK |

Two earlier invocations of the same driver produced no live call at all and are recorded here so the
artifact tree is explainable rather than mysterious:

```
D4_2_A-20260930T001105Z   died at the first call_opus with
                          "CLAUDE_CODE_EXECPATH is not set; live model execution is not available".
                          Live calls 0, cost $0.00. It left one orphan
                          SCCO/expectation_evidence.json, written before the call, which is
                          gitignored (.gitignore:26 data/runtime/*) and was deliberately not deleted.
(a second, identical attempt)  same failure, same zero cost.
```

The environment variable is injected by Claude Code into its own shell and is absent from a plain
terminal. The driver now resolves it itself and prints the resolved path, so the same failure cannot
silently recur. It is environment setup, not a contract change: `call_opus` reads the same variable
either way.

## I. Initial Validity

First-attempt parse and validator status, before any repair.

| candidate | initial parse | initial schema | initial semantic validator | initial valid |
|---|---|---|---|---|
| SCCO | PARSED | valid | **FAILED** | no |
| GOOG | PARSED | valid | **FAILED** | no |
| BSY | PARSED | valid | OK | yes |

```
initial valid = 1 / 3

preregistered bands (D4.3R2 §H, frozen before this run)
  3/3   = HEALTHY
  2/3   = ACCEPTABLE
  0-1/3 = UNSTABLE      <- this run
```

Parsing was never the problem: all three initial responses were a single parseable JSON object
matching the V2 schema. Both failures were the semantic validator, and §K names what fired.

For context only, and not as a comparison this tier is entitled to draw conclusions from: D4.3A's
stored records would read `initial valid = 0/3` under the same diagnostic, which did not exist when
D4.3A ran.

## J. Repair

```
total repair rounds     2   (SCCO 1, GOOG 1, BSY 0)
repair convergence      2 / 2 rounds reached OK on the first attempt
repair prompt version   h_v2_d4_expectation_gap_v2   (unchanged from the initial call)
repair cost             $0.880577   = 22.65% of the run total
MAX_REPAIR_ATTEMPTS     2 (never reached)
```

| candidate | round | failure codes before | failure fields | validation after | cost |
|---|---|---|---|---|---|
| SCCO | 0 | `SCHEMA` | whole-output consensus-language scan (no field path) | OK | $0.4530626 |
| GOOG | 0 | `SCHEMA` | whole-output consensus-language scan; `supporting_claims` x2 | OK | $0.4275146 |
| BSY | - | - | - | - | $0.00 |

The stored `initial_failure_codes` value is the coarse `classify_d4_repair_reason` output, `SCHEMA`,
for every failure. The real cause lives in `initial_failure_details`, which is why this section
classifies from the detail strings rather than from the code.

Cause classification (brief §12):

```
SCCO round 0
  1 defect   RUNTIME_VALIDATOR      consensus-language false positive (§K)

GOOG round 0
  1 defect   RUNTIME_VALIDATOR      consensus-language false positive (§K)
  2 defects  MODEL_CONTRACT_VIOLATION   code-owned numeric restatement (§L)
```

D4.3R2 §J's runtime-versus-audit split, computed per candidate:

```
runtime validator failures   3   (visible in repair_rounds[].failure_details_before, surfaced to
                                  the model as a repair prompt during the run)
audit-only issues            0   at the level the M gates adjudicate
```

The audit layer did surface 17 findings in a diagnostic category no M gate covers; those are audit-only
in D4.3R2 §J's sense and are reported separately in §N rather than folded in here.

No prompt, schema or validator change was made to reduce the repair count.

## K. Consensus

```
M3   PASS   0 rejections of an honest consensus-absence statement in a FINAL output
M4   PASS   0 asserted consensus expectations in a FINAL output
     fabricated_consensus defects: 0 / 0 / 0
```

Consensus fabrication in the final outputs is zero. The honest-absence vocabulary the contract
prescribes was accepted, verified directly against the frozen classifier:

```
"Available evidence does not establish consensus expectations."        -> ABSENCE
"Available evidence does not establish what the market expects."       -> ABSENCE
```

**Finding, recorded and deliberately not repaired.** Both initial-validity failures in §I were caused
by the same rule, and in both cases the rejected sentence does not fabricate a consensus - it denies
having one. Measured read-only against the production classifier, no code touched:

```
SCCO initial output
  "Taken together, the evidence gives no basis for saying market expectations lag the evidenced
   progress, and none for saying they run ahead of it."
    -> ASSERTED, trigger "market expectation noun phrase"

GOOG initial output
  "The non-price expectation evidence consists of capex expectations, backlog disclosures and a
   conversion-timing statement that did not change, and none of these shows whether the market's
   expectation lags or leads the evidenced progress."
    -> ASSERTED, trigger "market expectation noun phrase"
```

Both sentences pair `market` with an expectation noun, which is what D4.3R2's `_MARKET_EXPECTATION_SUBJECT`
pattern is built to catch, and neither contains a phrase on the `_ABSENCE` list. "gives no basis for
saying" and "none of these shows whether" are negations the absence list does not carry, so the
sentence routes to ASSERTED. The rule fired on a denial of knowledge.

This is the same defect *class* D4.1 hit - a validator rejecting an honest absence statement - but it
is materially milder in the one way that matters. D4.1's version was structurally non-convergent: the
error message demanded the very sentence that triggered the rule, so no repair round could ever
satisfy it. Here the model rephrased successfully on the first repair attempt, both times. The rule is
over-inclusive, not self-contradictory.

It is also worth recording that D4.3A's SCCO failure was the same class under a *different* trigger -
`analyst ... estimates` firing on `"The 10-K's mineral reserve estimates used $3.30 per pound..."`, a
GAAP-context sentence. D4.3R2 closed the market-referent coverage gap and, in closing it, opened a new
false-positive surface. Two consecutive live runs have now had their initial validity set by this one
rule's over-inclusiveness.

Per brief §0 and §16, nothing here was fixed, no detector was patched, and no repair step was
generated. Whether to simplify the structure is the user's decision (§R).

## L. Numeric Ownership

```
M8   PASS   0 claims restating a code-owned number in a FINAL output
     code_owned_numeric_defects: 0 / 0 / 0     (D4.3A stored: 6)
```

The frozen D4.3R role classifier was used unchanged. Period labels and identifiers (`Q2`, `2Q26`,
`63 sessions`, `C1`, `6-month`) are not VALUE_RESTATEMENT and were not treated as such.

Two genuine restatements did occur, in GOOG's initial response, and were caught by the runtime
validator and repaired away:

```
claim cites price_reaction.SEC:0001652044:0001652044-26-000048.return_1d
  but states '63'                  -> rejected
claim cites price_reaction.SEC:0001652044:0001652044-26-000071.benchmark_adjusted_1d
  but states '82', '24'            -> rejected
```

These are real MODEL_CONTRACT_VIOLATIONs, not detector artifacts: a number attached to a code-owned
citation is transcribed, never computed. GOOG produced the same defect class in D4.3A (`'82'`,
`'34'` on the same `benchmark_adjusted_1d` fact), so this is a recurring model behaviour on one
candidate, and the validator catches it both times.

## M. Zero Denominator

Stated explicitly so that no empty check is read as strong evidence.

```
C1   eligible 0    violations 0    NOT_EVALUATED
C4   eligible 3    violations 0    PASS
C5   eligible 3    violations 0    PASS
C6   eligible 1    violations 0    PASS
```

**C1 = NOT_EVALUATED.** C1 governs POSITIVE and WIDE_POSITIVE gap outputs, and this run produced
none: the three gap states were NEUTRAL, UNKNOWN and UNKNOWN. Zero eligible cases means the rule was
never exercised, which is not evidence that it works. C6's single eligible case is thin for the same
reason and is reported as one case, not as a rate.

The Expectation Gap state distribution below is recorded as an observation only. Per brief §15 it is
**not** an input to this tier's mechanical verdict.

| candidate | expectation_gap | confidence | priced_in | applied contract rules |
|---|---|---|---|---|
| SCCO | NEUTRAL | LOW | LIKELY_PRICED | C4 ceiling, C7 priced-in-needs-evidence |
| GOOG | UNKNOWN | UNKNOWN | UNKNOWN | C4 ceiling |
| BSY | UNKNOWN | UNKNOWN | UNKNOWN | C4 ceiling |

C4's MEDIUM ceiling applied to all three, as D4 predicted it would for every issuer in the current
data layer: `earnings.status` is UNKNOWN for 2,010 of 2,010 candidates.

## N. M1-M12

Computed by `audit_strategy_h_v2_d4_2.audit_run` over the stored records, not by the runner.

| gate | name | observed | status |
|---|---|---|---|
| M1 | canonical model valid | 3/3 canonical == claude-opus-5-5 | PASS |
| M2 | schema parse valid | 3/3 final outputs schema-valid | PASS |
| M3 | consensus absence accepted | 0 rejections of an honest absence statement | PASS |
| M4 | fabricated consensus absent | 0 asserted consensus expectations | PASS |
| M5 | direction enum respected | used INTRODUCED, STRENGTHENED, UNCHANGED, WEAKENED; enum visible in generated schema: True | PASS |
| M6 | C1 respected | 0 violations; C1 NOT_EVALUATED (0 eligible), C5 PASS (3), C6 PASS (1) | PASS |
| M7 | C4 respected | 0 confidences above the MEDIUM ceiling (3 eligible) | PASS |
| M8 | numeric ownership respected | 0 claims restating a code-owned number | PASS |
| M9 | D5/D6 fields absent | 0 prohibited fields, 0 prohibited vocabulary hits | PASS |
| M10 | raw telemetry complete | 3/3 records carry full untruncated telemetry | PASS |
| M11 | budget contract respected | max candidate $1.60 vs $6.00 reserve; run total $3.89 vs $25.46 remaining cap | PASS |
| M12 | clean-checkout reproducibility | clean-checkout import passed, smoke executed | PASS |

```
core gates (M3, M4, M5, M9, M12)   all PASS
M1-M12                             12 PASS, 0 FAIL, 0 NOT_EVALUATED
```

M6 needs reading carefully. The gate passes because it counted zero violations across C1, C5 and C6,
but C1's own denominator is zero (§M). M6 PASS here means "no C1/C5/C6 violation occurred", not "C1
was exercised and held".

**Outside M1-M12, reported because it exists and no gate covers it.** The audit's per-candidate defect
block carries a diagnostic category that is not wired to any gate:

```
unsourced_material_claims    SCCO 7   GOOG 4   BSY 6   = 17 total
  (D4.3A's stored audit: 19 total - this is pre-existing, not introduced by V3)
all other categories         0 across all three candidates
  future_source_leaks, code_owned_numeric_defects, fabricated_consensus,
  decision_field_leaks, decision_vocabulary_leaks, unknown_discipline_violations,
  unsupported_priced_in
```

These 17 are audit-only in D4.3R2 §J's sense: the runtime validator never surfaced them, so the model
was never asked to fix them. They are claims in narrative fields carrying no citation, for example
BSY's `root.gap_rationale[2]`. Because no M gate consumes this category, a nonzero count here does not
and did not move the mechanical verdict. That gap between what the audit measures and what the gates
adjudicate is recorded, not closed: adding a gate after a result exists is what the brief forbids, and
the category predates this run.

## O. Telemetry

```
preview fields used?    NO. Full raw text is stored for every attempt.
```

| candidate | role | raw_text chars (measured) | checksum |
|---|---|---|---|
| SCCO | initial | 26,338 | f86810719d130cce... |
| SCCO | repair #0 / final | 26,324 | 01b63fe910c9cada... |
| GOOG | initial | 25,281 | 1e6b0d500813395d... |
| GOOG | repair #0 / final | 25,309 | 2ebf04ae40ec8297... |
| BSY | initial / final | 23,567 | dc7113b6db5d849a... |

Every record carries requested and canonical model, prompt, schema, validation and gap contract
versions, `started_at`, `completed_at`, per-call cost, and the four checksums below. `RawResponseRecordV1`
stores no length or truncation field, so the character counts above were measured from the stored text
rather than read off a field that does not exist.

| candidate | research output | expectation evidence | input package | final output |
|---|---|---|---|---|
| SCCO | 61ee366a... | 2dac443b... | 00742863... | 0a3a3de7... |
| GOOG | 2adfe677... | b5408a81... | ce72136b... | 0db9c4e7... |
| BSY | f43bb7a6... | af5c46b5... | 60f51b8b... | c99eaacb... |

```
output_tokens    SCCO 15,019   GOOG 15,485   BSY 13,538      (measured)
input_tokens     2 / 2 / 2                                    (NOT the total input)
```

`input_tokens = 2` against a ~170,000 character prompt is the known cache-accounting limitation
carried unchanged from D3.3 and D4.1: `extract_usage` reads the right key but the response does not
aggregate cached input, and the cache fields are not collected. It is recorded as measured rather than
corrected or estimated, and no metadata was invented where the response did not supply it.

## P. Cost

```
SCCO    initial $1.1492760    repair $0.4530626    total $1.6023386
GOOG    initial $1.0239106    repair $0.4275146    total $1.4514252
BSY     initial $0.8335066    repair $0.0000000    total $0.8335066

Tier A V3 total live cost      $3.8872704
hard cap                       $18.00          (21.6% used)
```

```
observed avg / company         $1.2957568
observed max / company         $1.6023386
observed min / company         $0.8335066
initial-call share             $3.0066930   (77.35%)
repair share                   $0.8805772   (22.65%)
```

Historical reference, explicitly labelled as such and not part of this run's figure:

```
D4.1    = $4.541394     (historical reference)
D4.3A   = $4.944473     (historical reference)
V3      = $3.8872704    (this run)

sum of all three, as historical reference only   = $13.3731374
remaining against the original $30 D4 authorization = $16.6268626
```

V3 is the cheapest of the three runs and the first to complete all three candidates.

## Q. Tests

Before the live run, from the repository root:

```
.venv/bin/python -m pytest backend/tests/strategy_h_v2 -q
749 passed, 1 skipped
```

After the live run, unchanged:

```
749 passed, 1 skipped
```

The one skip is `test_probe_under_a_forced_non_project_interpreter_is_marked_unofficial`, explained as
expected since D4.3R §L and unchanged here. Every area the brief §20 names was run individually:

| area | test file | result |
|---|---|---|
| telemetry integrity, M1-M12 aggregation | test_d4_3r_audit_alignment.py | 17 passed, 1 skipped |
| M4 R2 | test_consensus_language_r2.py | 29 passed |
| M8 roles | test_numeric_roles.py | 12 passed |
| clean smoke, zero denominator | test_clean_checkout.py | 18 passed |
| budget | test_d4_budget_contract.py | 15 passed |
| contract / canonical model | test_d4_2_contract.py | 13 passed |
| enum visibility | test_direction_enum_contract.py | 19 passed |
| validator | test_d4_validate.py | 33 passed |
| contract rules | test_contract_rules.py | 20 passed |

No live result was edited to make a test pass, and no test was edited to accommodate a live result.
There is no separated known-fixture failure because there was no failure.

## R. Verdict

```
H-V2-D4 Final Tier A V3
= MECHANICAL READY
```

Every one of M1-M12 read PASS, including all five core gates. The two enforcement-layer defects that
made D4.1 return MECHANICAL NOT READY are gone from the observed behaviour: the direction enum was
visible to the model and used correctly (M5), and the consensus-absence vocabulary the contract
prescribes was accepted rather than rejected (M3).

What this verdict does not say, stated because the temptation to over-read it is the whole reason Tier
A exists: it is not a D4 Expectation Gap quality PASS, not an APPROVE, WATCH or REJECT, not a
valuation, not a fair value, not a target price and not a forward return. Tier A's three issuers were
read closely during D4's design, so nothing here is evidence about interpretation quality.

**No new structural runtime or audit defect was found**, which is the §16 Case C test. The consensus
false positive in §K is a real finding, but runtime and audit share one classifier function by object
identity, the two rejections converged on the first repair attempt, and the category in §N predates
this run. Case C therefore does not apply, and no D4.4R or D4.4R2 step was generated.

Brief §0 was held: no prompt, schema, validator, consensus classifier, M4, M8, C1, C4, Gap, confidence
or sample change; no live patch followed by a rerun; no Tier B; no valuation; no decision output; no
forward return; no push.

## S. Initial Validity Diagnostic

```
initial valid = 1 / 3 = UNSTABLE
```

D4.3R2 §H froze this band and its consequence before any V3 result existed, and the frozen rule is
self-executing:

> if Tier A V3 reads mechanically READY but INITIAL VALIDITY reads UNSTABLE, Tier B does not start
> automatically. That combination requires a separate user review before any Tier B authorization,
> because a mechanically clean pass built on a 0-1/3 first-attempt rate would mean the repair loop, not
> the initial prompt, is doing load-bearing work.

That is exactly the combination this run produced, so:

```
Tier B = NOT AUTO-AUTHORIZED
Reason = D4 prompt/schema stability
```

The diagnostic is not a D4 semantics gate and does not fold into M1-M12. It says nothing about whether
an expectation-gap judgement is any good.

What the two failures actually were matters for any later decision, so it is worth separating them
rather than reading 1/3 as one undifferentiated instability:

```
of the 3 initial-round defects
  1  SCCO   validator over-inclusiveness on an honest absence statement   (§K)
  1  GOOG   validator over-inclusiveness on an honest absence statement   (§K)
  2  GOOG   genuine code-owned numeric restatement by the model           (§L)
```

Two of the three defects, and the sole cause of SCCO's failure, are the classifier rejecting a
sentence that denies having consensus knowledge. Only GOOG's numeric pair is the model breaking a rule
it was shown. Read that way, the prompt's instruction-following looks better than 1/3 suggests and the
classifier's precision looks worse. The honest summary is that the diagnostic is measuring both at
once and cannot separate them by construction, which is why its frozen consequence is a user review
rather than an automatic gate.

## T. Tier B Budget Proposal

Computed because Tier A read READY. **Not an authorization, and no cap is set or approved here.**
Tier B remains NOT AUTO-AUTHORIZED on the §S ground regardless of what the money says.

```
observed avg cost / company (D4 leg)     $1.2957568
observed max cost / company (D4 leg)     $1.6023386
repair rate                              2 / 3 candidates = 66.7%
                                         2 rounds / 3 candidates = 0.67 rounds per candidate
```

Tier B chains a fresh D3 run before the D4 call, so a projection must carry both legs. The D3 figures
are the measured costs of these same three issuers' D3.3 runs, recorded in the manifest:

```
D3 observed avg / company                $1.4692885   (SCCO 1.4005638, GOOG 1.4856128, BSY 1.5216890)
D4 observed avg / company                $1.2957568
Tier B observed per company              $2.7650453
Tier B 6-company observed projection     $16.5903
```

Against the existing theoretical figure, unchanged:

```
theoretical worst case                   $45.60       (6 x $7.60)
observed projection                      $16.5903     (36.4% of worst case)
remaining against the $30 authorization  $16.6268626
```

The observed projection fits the remaining authorization with $0.037 to spare, which is not a margin
anyone should plan against: a single candidate needing its full 3-call topology would break it.
`d4_2_contract.TIER_B_FITS_REMAINING_CAP` is asserted False in code precisely so this cannot be
papered over. Authorizing Tier B needs a user decision on how - a smaller sample, a raised cap, or a
lower per-call cap - and this document makes none of those choices.

```
Tier B calls in this run    0
```

## U. Final Declarations

```
checkpoint                              714841e
HEAD at run time                        0c2c540 -> 9f9c89e (unrelated commits, H-V2 untouched)
run_id                                  D4_2_A-20260930T012115Z
requested model                         claude-opus-5-5
canonical model                         claude-opus-5-5   (3/3, mismatch false)
sample                                  SCCO / GOOG / BSY  (checksum verified, unchanged)
initial valid                           1 / 3 = UNSTABLE
repair count                            2   (SCCO 1, GOOG 1, BSY 0), both converged
M1-M12                                  12 PASS / 0 FAIL / 0 NOT_EVALUATED
M4                                      PASS (0 fabricated consensus)
M8                                      PASS (0 code-owned numeric restatement in final output)
M12                                     PASS (smoke_executed true, official interpreter)
consensus fabricated                    0
numeric mutation                        0 in final output; 2 in GOOG's initial round, repaired
C1                                      NOT_EVALUATED (0 eligible)
C4                                      PASS (3 eligible, 0 violations)
D5/D6 leakage                           0 fields, 0 vocabulary hits
new live cost                           $3.8872704
hard cap                                $18.00 (frozen TIER_A_HARD_BUDGET_USD, user-authorized)
Tier B calls                            0
Tier A V3 executed?                     YES
Tier B executed?                        NO
D5?                                     NO
valuation / fair value / target price?   NO
APPROVE / WATCH / REJECT?                NO
forward return?                         NO
D4.3A verdict changed?                  NO (MECHANICAL NOT READY, unedited)
prompt / schema / validator changed?    NO
consensus classifier / M4 / M8 changed? NO
C1 / C4 / Gap / confidence changed?     NO
sample replaced?                        NO
existing unrelated dirty files modified? NO
push?                                   NO
```

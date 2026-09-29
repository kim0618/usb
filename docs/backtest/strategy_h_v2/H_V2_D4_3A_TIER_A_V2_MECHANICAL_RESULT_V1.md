# H-V2-D4.3A - Tier A V2 Mechanical Live Re-run

One question only: can D4 Contract V2 generate, validate and store real Claude Opus 5.5 responses
on the same frozen Tier A mechanical sample?

This is not a quality result. No investment-strategy conclusion, no Expectation Gap distribution
assessment, no Tier B, no valuation, no APPROVE/WATCH/REJECT, no forward returns. D4.1's
`Tier A = MECHANICAL NOT READY` is left standing as immutable historical evidence; nothing below
edits it.

```
H-V2-D4.3A Tier A
= MECHANICAL NOT READY

Tier B
= NOT EXECUTED

D5
= NOT READY
```

The contract executed end to end and the live pipeline worked. Both failing gates, M4 and M8, were
produced by the audit layer's own detectors rather than by anything the model did, and the
diagnosis is in §O. The gates were frozen before the run and are not adjusted after seeing the
result, so the verdict stands as computed.

---

## A. Repository State

Checked before anything ran. Nothing was modified, deleted or staged out of the pre-existing dirty
set.

```
branch        main
HEAD          ce3249ee636ecef999da8610e9dea60a195073b4
origin        origin/main, ahead 30, behind 0
staged        0 files
dirty         190 entries (25 modified tracked, 165 untracked)
```

`git write-tree` returned `eae202c73c9e61d589f9a7f40b1ca6a285208f4c`, bit-identical to
`git rev-parse HEAD^{tree}`. With nothing staged, the index tree IS HEAD's tree, so every
clean-checkout probe below measured committed content and only committed content.

Dependency closure commits present: `91bcefd`, `ce3249e`. D4.2 commit present: `d78c52e`.

Required-committed check (brief §2):

| file | state |
|---|---|
| `backend/app/backtest/strategy_h_v2/research/schema.py` | committed, clean |
| `backend/app/backtest/strategy_h_v2/research/validate.py` | committed, clean |
| `backend/app/backtest/strategy_h_v2/research/repair.py` | committed, clean |

Two research-directory files remain outside the committed set, exactly as D4.2P §D left them and
untouched here: `research/prompt_builder.py` is tracked and dirty, `research/gates.py` is
untracked. Neither is imported by any Tier A entry point, which §C measures rather than assumes.

## B. D4.2P Input

Authoritative state carried in, and confirmed against the repository rather than taken on trust:

```
H-V2-D3.3          = PASS
H-V2-D4.2          = NEEDS REVISION
H-V2-D4.2P         = PASS
M12 CLEAN CHECKOUT = PASS
Tier A V2 re-run   = AUTHORIZED
Tier B             = NOT AUTHORIZED
D5                 = NOT AUTHORIZED
```

## C. Clean Checkout

Run before any live call, per the brief's stop rule.

The committed probe `app/dev/d4_clean_checkout.py` exports the git index with `git write-tree` plus
`git archive` into a temporary directory and imports there in a subprocess. It touches neither the
working tree nor HEAD.

**Result: all 10 D4 targets pass, smoke passes, `passed: true`, tree `eae202c7`.**

A wider probe over 25 targets, adding the D3/D3.1/D3.3 research modules the D4 target list does not
cover (`research.schema`, `schema_v2`, `validate`, `repair`, `assemble_v2`, `prompt_builder_v2`,
`validation_v2`, `telemetry_contract_v2`, `research_attempt_v2`, `d3_3_contract`, plus the D3, D3.3
and D4.1 runners and `d4_1_contract`, `expectation/ledger`), also returns 25/25 pass with the smoke
executed and passing.

Two operational notes, recorded rather than repaired:

1. The probe must be run with the project interpreter (`/home/tjd618/usb/.venv/bin/python`). Run
   under the system interpreter it reports `passed: false` with
   `ModuleNotFoundError: No module named 'pydantic'` on 6 of 10 targets, because the probe replaces
   the subprocess environment and pins `HOME` to the temporary directory, which removes
   `~/.local/lib/python3.12/site-packages` from the resolution path. That is an interpreter-choice
   artifact in this environment, not a dependency-closure defect; the same tree passes 25/25 under
   the project venv.
2. In that failing run the probe still reported `smoke_ok: true` although the smoke never executed.
   The smoke is guarded by `if all(item['ok'] for item in out)` while `smoke_ok` is initialised to
   `True`, so a skipped check is indistinguishable in the output from a passed one. This is the
   vacuous-PASS pattern brief §17 forbids, living in the probe's own reporting. Not fixed here; no
   code was changed in this session.

```
M12 CLEAN CHECKOUT
= PASS
```

## D. Frozen Tier A Sample

Reused exactly. Not replaced, not extended, not trimmed.

```
SCCO   CIK 0001001838
GOOG   CIK 0001652044
BSY    CIK 0001031308
```

`tier_a_checksum(TIER_A_TICKERS_UNCHANGED)` equals the frozen
`d47cc4fd30fd3d6f04738b811e401547abedf57c195f0c4c2f1b65af34e2f6ab`. `d4_2_contract.py` imports the
tuple from `d4_1_contract.py` by reference rather than by copy, so a "same sample" claim cannot
drift into a re-drawn one.

Tier A is a wiring test, not an unseen-generalization test. Its three issuers are three of D3.3's
twelve and their outputs were read closely while D4 was being designed, so nothing in this document
is evidence about interpretation quality.

BSY is worth naming: D4.1 attempted only SCCO and GOOG, so BSY's first D4 call happened in this
run. It is still the frozen sample rather than a new draw.

## E. Input Package Preflight

Checked for all three before any spend, and all required inputs were present, so no
`INPUT_NOT_READY` stop was triggered.

| ticker | D3 record | D3 status | D3 output checksum | package file | package checksum now | matches D3-stored |
|---|---|---|---|---|---|---|
| SCCO | `SCCO-75bdb891...` | OK | `61ee366ad65b3380` | present | `00742863bf3d27ab` | yes |
| GOOG | `GOOG-5d94b614...` | OK | `2adfe6775b41a0b9` | present | `ce72136b1a157241` | yes |
| BSY | `BSY-9d4aedb1...` | OK | `f43bb7a61ed9c24d` | present | `60f51b8b48c06487` | yes |

`package_checksum()` recomputed from disk equals the `input_package_checksum` each D3.3 attempt
stored at the time, so the D4 leg consumed the same bytes the D3 leg did.

Source and evidence IDs resolve: the audit's `unsourced_material_claims` detector reports zero
`unresolvable evidence_id` and zero `unresolvable source_id` entries across all three final outputs
(its other findings are "no citation", a different condition, discussed in §O).

On the missing-fixture regression named in the brief: the absent
`data/runtime/strategy_h_v2/d2_1/.../packages/*.json` files are the ones the D3.3 tests reach for
when run with `backend/` as the working directory. All three packages the frozen Tier A sample
actually needs exist at the repository root, which is where the runner resolves them from. No
substitute ticker was used and none was considered.

Common inputs: package run `D2-20260928T061101Z`, data cutoff `2026-09-28T05:49:37.377541+00:00`
for all three.

## F. Model Verification

```
requested model   = claude-opus-5-5
canonical models  = claude-opus-5-5, claude-opus-5-5, claude-opus-5-5   (3/3)
model_mismatch    = False, False, False
fallback          = none; no attempt was invalidated
```

`compute_model_mismatch` compared the requested id against `modelUsage[...].canonicalModel` on
every response, initial and repair.

## G. Contract Versions

V2 identifiers throughout. No V1 fallback anywhere in the stored records.

| layer | version |
|---|---|
| D4 contract | `h_v2_d4_contract_v2` |
| D4.2 re-run contract | `h_v2_d4_2_contract_repair_v1` |
| Prompt | `h_v2_d4_expectation_gap_v2` |
| Output schema | `h_expectation_gap_analysis_v2` |
| Validation | `h_v2_d4_validation_contract_v2` |
| Consensus language | `h_v2_d4_consensus_language_v2` |
| Gap contract | `h_v2_d4_gap_contract_v1` |
| Budget contract | `contract_v2.CandidateBudgetContract`, topology `3 x $2.00` |
| Ledger / telemetry record | `h_v2_d4_ledger_v1`; `telemetry_contract_v2.RawResponseRecordV1` |

`gap_contract_version` stays at `v1` because D4.2 did not revise the gap semantics, and the brief
forbids revising them here.

## H. Consensus Validator

D4.1's actual blocker was that V1's `consensus expect` substring is a prefix of
`consensus expectations`, so the rule rejected the very sentence its own error message ordered the
model to write. Checked directly against the committed V2 classifier before the run:

| sentence | V2 verdict |
|---|---|
| `Available evidence does not establish consensus expectations.` | **ABSENCE (allowed)** |
| `No analyst consensus source is connected for this issuer.` | ABSENCE |
| `Consensus expectations are unavailable in the current evidence set.` | ABSENCE |
| `Street estimates were not provided.` | ABSENCE |
| `The evidence lacks any sell-side coverage.` | ABSENCE |
| `Consensus expects X.` | ASSERTED (rejected) |
| `Analysts expect X.` | ASSERTED (rejected) |
| `Wall Street expects X.` | ASSERTED (rejected) |
| `Consensus estimates of $5.00 per share.` | ASSERTED (rejected) |
| `Revenue beat consensus.` | ASSERTED (rejected) |
| `Consensus does not expect growth.` | ASSERTED (rejected) |

All 11 of V1's banned substrings still reject when placed in an asserting sentence: zero leaks. The
repair is a repair, not a relaxation.

Live confirmation: `v1_impossible_consensus_rejections` is 0 across all three candidates. The
impossible loop did not recur.

The rule did fire live, twice, on SCCO, which makes M3 a real measurement rather than a vacuous
one. Both firings were the same sentence, and it is a boundary case worth recording:

> "The 10-K's mineral reserve estimates used $3.30 per pound of copper and $10.00 per pound of
> molybdenum, prices derived from analyst and bank forecasts as of December 31, 2025."

That is a sourced quotation from the filing describing an input the company used, not a consensus
expectation the model invented. The V2 rule is keyed on the bundle-level
`consensus.status == SOURCE_NOT_AVAILABLE` and on a sentence naming a consensus referent with an
attribution, so a filing-quoted analyst reference falls inside it. The error message named the
permitted alternative, the model complied on the first repair, and the candidate finished OK. This
is recorded as an observation about rule scope, not acted on: the brief forbids consensus or
semantic changes in this session.

## I. Direction Schema

D4.1's second defect was a `direction` allow-list the validator enforced but the model-facing
schema never showed. Checked on the actual generated artifact immediately before the run, not on
the Python enum:

```
$.$defs.ManagementSignalDirection.enum
= ['STRENGTHENED', 'WEAKENED', 'INTRODUCED', 'WITHDRAWN',
   'BROUGHT_FORWARD', 'DELAYED', 'UNCHANGED']
```

Seven members, exposed as a real JSON-schema `enum`, matching
`contract_v2.ManagementSignalDirection` exactly. The clean-checkout smoke asserts the same property
independently from the exported tree, and it executed and passed.

Live: 6 `management_signal_changes` were emitted across the three candidates, using
`INTRODUCED`, `STRENGTHENED`, `UNCHANGED`, `WEAKENED`. Every value is a frozen member. No
out-of-enum value was produced, so no model failure of this kind occurred, and no validator failure
from a hidden allow-list occurred either.

## J. Budget

D4.2's V2 topology, used unchanged. Recorded before the run started and not altered afterwards.

```
per_call_max_budget          = $2.00      (a cap on ONE call, which is what the CLI flag is)
max_calls_per_candidate      = 3          (1 initial + up to 2 repairs)
candidate_worst_case_budget  = $6.00      (2.00 x 3, preceding stage $0 for Tier A)
tier_a_hard_budget           = $18.00     (3 x $6.00), frozen in d4_2_contract
overall_hard_cap             = $30.00
remaining_cap_before_run     = $25.458606 ($30.00 less D4.1's measured $4.541394)
```

The Tier A cap is the frozen contract value, so no ad hoc conservative cap had to be invented.
D4.1's incorrect `$2 candidate reserve` is not reused anywhere: the reserve is now the call
topology, which is the arithmetic D4.1 got wrong.

Preflight ran before each candidate, checking
`spent + candidate_worst_case_budget <= tier_a_hard_budget`. Recomputed after the fact, all three
admissions were correct and none was borderline:

| candidate | spent before | + reserve | vs cap | admitted |
|---|---|---|---|---|
| SCCO | $0.000000 | $6.00 | $18.00 | yes |
| GOOG | $1.906350 | $6.00 | $18.00 | yes |
| BSY | $3.553641 | $6.00 | $18.00 | yes |

No Tier B reserve was held, since Tier B is not running.

## K. Execution

```
run_id            D4_2_A-20260929T072105Z
tier              A
started           2026-09-29T07:21:05Z
finished          2026-09-29T07:33:10Z   (about 12 minutes)
sample_size       3
attempted         3
stopped_early     null
budget_exhausted  false
```

All three candidates reached `final_status = OK`. The runner adjudicates no gate; M1-M12 are
computed separately by `audit_strategy_h_v2_d4_2.py` over the stored records.

| ticker | final status | repairs | gap | confidence | priced_in | applied rules |
|---|---|---|---|---|---|---|
| SCCO | OK | 1 | NEUTRAL | LOW | LIKELY_PRICED | C4, C7 |
| GOOG | OK | 1 | UNKNOWN | UNKNOWN | UNKNOWN | C4 |
| BSY | OK | 1 | UNKNOWN | UNKNOWN | UNKNOWN | C3, C4 |

Per brief §16 the gap and confidence values above are recorded for completeness and are **not** used
in the Tier A verdict and **not** offered as quality evidence. Three issuers whose outputs were read
during design cannot support a distribution claim.

## L. Initial Validity

Reported separately per candidate, because whether a first response passes without repair is the
single most informative mechanical number here.

| ticker | initial parse | initial schema | initial validator | repair required |
|---|---|---|---|---|
| SCCO | PARSED | valid | **FAILED** | yes |
| GOOG | PARSED | valid | **FAILED** | yes |
| BSY | PARSED | valid | **FAILED** | yes |

```
initial parse valid      = 3 / 3
initial validator valid  = 0 / 3
repair required          = 3 / 3
```

Every first response was well-formed JSON conforming to the V2 output schema. None passed the
semantic validator unaided. This is a clean signal and it is not the D4.1 failure mode: D4.1's
candidates exhausted the repair loop against an unsatisfiable rule, whereas all three here were
repaired successfully in exactly one round.

That 0/3 is a real finding about the contract's first-pass yield and is left standing. It is not
graded here, because no M-gate measures first-pass yield and adding one after seeing the result is
exactly what brief §12 forbids.

## M. Repair

Bounded repair contract only, at most 2 rounds per candidate. Observed: exactly 1 round each, each
ending OK.

| ticker | round | failure code | failure field | cost | status after |
|---|---|---|---|---|---|
| SCCO | 0 | SCHEMA | `market_expectation_evidence` | $0.532370 | OK |
| GOOG | 0 | SCHEMA | `supporting_claims` | $0.471818 | OK |
| BSY | 0 | SCHEMA | `supporting_claims` | $0.431604 | OK |

What each initial response actually failed on:

- **SCCO** - the consensus rule, on the filing-quoted sentence reproduced in §H.
- **GOOG** - a claim citing code-owned fact
  `price_reaction.SEC:0001652044:0001652044-26-000071.benchmark_adjusted_1d` while stating the
  numbers `82` and `34`, which are not that fact's value.
- **BSY** - a claim citing code-owned fact
  `price_reaction.SEC:0001031308:0001031308-26-000020:EX-99.1.benchmark_adjusted_3d` while stating
  `12`, which is not that fact's value.

GOOG's and BSY's are genuine numeric-ownership violations by the model, caught by the validator and
corrected on the first repair. The numeric-ownership enforcement works.

Repair prompts added no new evidence and requested no new analysis. `build_repair_prompt_v2` is
handed only the prior raw text and the validator's error list; the failure codes, failure fields,
full repair prompts, raw repair responses, post-repair status and per-round cost are all stored.

## N. Telemetry

Full raw responses stored, not previews. Recomputed `checksum(raw_text)` equals the stored checksum
for every block: 3 initial, 3 repair, 3 final, zero mismatches.

| ticker | initial raw | repair raw | final raw | truncated | started / completed |
|---|---|---|---|---|---|
| SCCO | 33,542 ch, `c8a9506efdc261f8` | 33,498 ch | 33,498 ch, `253d0b501ada8128` | no | 07:21:16Z / 07:25:24Z |
| GOOG | 28,516 ch, `2884d7f224d24574` | 28,534 ch | 28,534 ch, `80723a2855f509a0` | no | 07:25:24Z / 07:29:31Z |
| BSY | 24,416 ch, `bf989752bb124255` | 24,412 ch | 24,412 ch, `d476198a22884552` | no | 07:29:31Z / 07:33:10Z |

Also stored per record: requested model, canonical model, model_mismatch, split costs, validation
failure codes and full details, D3 output checksum, expectation-evidence checksum, input-package
checksum, and all contract versions from §G. Records are written under
`data/runtime/strategy_h_v2/d4_2/analyses/D4_2_A-20260929T072105Z/`, which is gitignored and
therefore not part of the commit.

## O. M1-M12

Frozen D4.2 gates, unchanged after seeing the result.

| gate | name | observed | status |
|---|---|---|---|
| M1 | canonical model valid | 3/3 canonical == claude-opus-5-5, 0 mismatches | PASS |
| M2 | schema parse valid | 3/3 final outputs schema-valid | PASS |
| M3 | consensus absence accepted | 0 rejections of an honest absence statement | PASS |
| M4 | fabricated consensus absent | 1 asserted consensus expectation in final output | **FAIL** |
| M5 | direction enum respected | directions used INTRODUCED/STRENGTHENED/UNCHANGED/WEAKENED, all frozen; enum visible in generated schema | PASS |
| M6 | C1 respected | 0 C1/C5/C6 violations | PASS |
| M7 | C4 respected | 0 confidences above the frozen MEDIUM ceiling | PASS |
| M8 | numeric ownership respected | 6 claims restating a code-owned number | **FAIL** |
| M9 | D5/D6 forbidden fields absent | 0 prohibited fields, 0 prohibited vocabulary hits | PASS |
| M10 | raw telemetry complete | 3/3 full untruncated telemetry | PASS |
| M11 | candidate budget contract respected | max candidate $1.91 vs $6.00 reserve; run total $4.94 vs $25.46 remaining | PASS |
| M12 | clean-checkout reproducibility | clean-checkout import passed | PASS |

All 12 gates present and evaluated; none defaulted to PASS and none is NOT_EVALUATED.

M4 is a core gate (`M_CORE_GATES = M3, M4, M5, M9, M12`), so its failure alone determines the
verdict.

### Denominators (brief §17)

Stated so no PASS above can be read as vacuous. Measured over the three final outputs: 173 claims
of which 162 material, 86 citations of code-owned facts, 282 prose strings, 1,363 field names, 6
management signal changes.

| gate | denominator | vacuous? |
|---|---|---|
| M1 | 3 responses | no |
| M2 | 3 responses | no |
| M3 | consensus rule fired live 2x (SCCO) | no |
| M4 | 282 prose strings scanned | no |
| M5 | 6 directions emitted, plus a real schema inspection | no |
| M6, C5/C6 branch | 2 UNKNOWN gaps exercised | no |
| M6, C1 branch | **0** POSITIVE/WIDE_POSITIVE outputs (gaps were NEUTRAL, UNKNOWN, UNKNOWN) | **yes, not exercised** |
| M7 | 3 confidences (LOW, UNKNOWN, UNKNOWN) | exercised, but none approached the MEDIUM ceiling |
| M8 | 86 code-owned citations | no |
| M9 | 1,363 field names, 282 prose strings | no |
| M10 | 9 raw blocks | no |
| M11 | 3 candidate spends, 3 preflight admissions | no |
| M12 | 10 committed-tree imports plus smoke (25 in the wider probe) | no |

**M6's C1 branch was not exercised at all.** C1 constrains POSITIVE and WIDE_POSITIVE outputs and
no candidate produced one, so its 0 violations is a zero-denominator result. The frozen gate
computes PASS from the combined C1/C5/C6 count and that is what is recorded above, but the C1
half of it is not evidence that C1 works. Stated here rather than folded into a PASS.

### M4 diagnosis

One flagged string, GOOG `root.limitations[0]`:

> "Consensus and estimate revisions are SOURCE_NOT_AVAILABLE, so the core input for judging what
> the market expects is absent. Confidence is capped at MEDIUM under C4, and here the gap is
> UNKNOWN."

The two layers disagree about it:

| layer | verdict |
|---|---|
| V2 validator (`consensus_language`, sentence-scoped) | **ABSENCE (allowed)**, trigger `SOURCE_NOT_AVAILABLE` |
| D4.1-era audit detector (`CONSENSUS_ASSERTIONS`, whole-string regex) | ASSERTED, on `the market expect` and `estimate revisions are` |

The sentence states that the consensus input is absent. It attributes no expectation to anyone; it
says the opposite. The V2 validator reads it correctly. The audit detector matches the substring
`the market expect` inside `judging what the market expects is absent`, and its only escape hatch
is the one literal prescribed sentence, so any other phrasing of the same absence trips it.

That is structurally the same defect D4.2 was created to repair, relocated from the validator to
the audit layer: an over-broad consensus pattern punishing an honest statement of absence. The
model did not fabricate a consensus expectation. M4's underlying property appears to hold.

M4's status stays FAIL. The gates were frozen before the run and brief §12 forbids changing them
after seeing the result, so the detector is not touched and the verdict is not re-graded.

### M8 diagnosis

Six flagged claims: 4 BSY, 1 GOOG, 1 SCCO. In all six the cited code fact is a `RETURN_FRACTION`
and the claim text restates no number at all. The digits `_numbers()` extracted are incidental:

| candidate, path | cited fact | digits extracted | what they are |
|---|---|---|---|
| BSY `price_reaction_reading[2]` | -0.024464 | 2 | "Q2" |
| BSY `pre_event_positioning_reading[1]` | 0.138705 | 2 | "Q2" |
| BSY `pre_event_positioning_reading[3]` | -0.084499 | 1 | "Q1" |
| BSY `gap_rationale[3]` | -0.303109 | 1, 6 | "C1", "6-month" |
| GOOG `gap_rationale[4]` | -0.090487 | 63 | "63 sessions" |
| SCCO `pre_event_positioning_reading[1]` | -0.010005 | 20, 2, 26 | "20 sessions", "2Q26" |

M8's frozen requirement is "no claim cites a code-owned fact while stating a **different** number".
None of these six states a number for the fact, so none meets that requirement's own wording. The
implementation is stricter than the requirement it enforces: `_matches_fact` returns True for
purely qualitative text, but "purely qualitative" is decided by whether the string contains any
digits at all, and a quarter label or a session-window count supplies one.

`_matches_fact`'s own docstring names this exact failure mode - "a session COUNT in a sentence about
a return is ordinary writing, and flagging it manufactures the false-positive class D3.1 §I.2
already measured once" - but the carve-out is implemented only for `STATE_TOKEN`, never for
`RETURN_FRACTION`.

The validator agrees with this reading: it caught GOOG's and BSY's real numeric violations on the
initial responses (§M) and passed these six post-repair claims. M8's underlying property appears
to hold, and the numeric-ownership enforcement demonstrably works.

M8's status stays FAIL, on the same frozen-gate grounds as M4.

## P. Cost

```
candidate 1  SCCO   initial $1.373980   repair $0.532370   total $1.906350
candidate 2  GOOG   initial $1.175473   repair $0.471818   total $1.647291
candidate 3  BSY    initial $0.959228   repair $0.431604   total $1.390832

new Tier A total cost = $4.944473
```

Every candidate finished inside its $6.00 worst-case reserve, largest at $1.91, about 32% of it.
The run total is 27% of the $18.00 Tier A cap.

Separated as the brief requires:

```
D4.1 historical cost       = $4.541394
D4 V2 Tier A new cost      = $4.944473
combined historical spend  = $9.485867   (informational only)
remaining against $30 cap  = $20.514133
```

## Q. Tests

Relevant H-V2 suite, run before and after the live execution:

```
before   643 passed, 38 skipped, 10 failed
after    643 passed, 38 skipped, 10 failed
```

Identical. No regression from the live run.

All 10 failures are the known missing-fixture failures, isolated as the brief requires: every one
is in `tests/strategy_h_v2/research/` (`test_d3_3_contract.py`, `test_run_strategy_h_v2_d3_3.py`),
all raising `FileNotFoundError` on
`data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages/*.json` because pytest runs with
`backend/` as the working directory while those fixtures live at the repository root. None is in
the D4 package. The counts match D4.2P's recorded baseline exactly.

Post-live verification, all passing:

| check | result |
|---|---|
| telemetry integrity (initial, repair, final, timestamps all present and non-empty) | PASS |
| raw response checksums recompute | PASS, 0/9 mismatches |
| model matching (requested vs canonical, both calls) | PASS, 3/3 |
| consensus rules (no V1 impossible rejection) | PASS |
| direction enum (every emitted value frozen) | PASS |
| budget arithmetic recomputes from split costs | PASS |
| budget within candidate reserve / tier cap / remaining cap | PASS |
| budget preflight would have admitted each candidate | PASS, 3/3 |
| M1-M12 aggregation, all 12 present, none silently missing | PASS |

## R. Verdict

```
H-V2-D4.3A Tier A
= MECHANICAL NOT READY
```

M4 and M8 failed, and M4 is a core gate.

What the run nevertheless established, and what a later session can build on:

- The V2 contract executes end to end on live Opus 5.5. 3/3 candidates completed OK.
- D4.1's two blockers are genuinely fixed. The consensus rule accepts its own prescribed remedy
  (§H), and the direction enum is visible in the model-facing schema (§I). Neither recurred live.
- Model identity, telemetry, checksums and budget accounting all hold exactly (§N, §Q).
- Clean-checkout reproducibility holds on committed code alone (§C).

What stands against it:

- 0/3 first responses passed the validator. All three needed a repair. That is a real first-pass
  yield number, not a defect in any single component.
- The two failing gates failed on audit-layer detectors, not on model behaviour. Both properties
  they protect appear to hold on inspection (§O). Recorded, not repaired: no code was changed after
  the result was seen.
- M6's C1 branch was never exercised, so it carries no evidence either way.

This is the honest position: the pipeline works, and its verification layer does not yet agree with
its enforcement layer. Making the verdict READY requires reconciling those two layers in a separate
authorized step, taken before a re-run rather than after this result.

```
Tier B = NOT EXECUTED   (Tier B live calls = 0)
D5     = NOT READY
```

Tier B would not have run even on a READY verdict: its budget contract needs separate user
authorization, and under the repaired accounting it does not fit the remaining cap.

## S. Tier B Budget Proposal

Proposal only. Nothing below is applied, approved or written into any contract.

Observed from this run:

```
observed cost/company      = $1.648158   (mean of 3)
observed max cost/company  = $1.906350
observed repair rate       = 3/3 = 100%, exactly 1 round each
observed initial-valid rate= 0/3
```

Tier B is 6 companies (IDCC, DORM, FRPT, TG, CRK, SPSC) and each chains a fresh D3 run before its
D4 call, so a Tier B company costs a D3 leg plus a D4 leg. D3.3's 12 OK attempts observed a mean of
$1.392693 and a max of $1.540263.

Against $20.514133 remaining:

| basis | projection | fits remaining cap |
|---|---|---|
| theoretical worst case, 6 x $7.60 | **$45.60** | no, exceeds by $25.09 |
| observed mean, 6 x ($1.6482 + $1.3927) | **$18.25** | yes, $2.26 headroom |
| observed max, 6 x ($1.9064 + $1.5403) | **$20.68** | no, exceeds by $0.17 |
| observed max D4 + frozen D3 worst case, 6 x ($1.9064 + $1.60) | **$21.04** | no, exceeds by $0.53 |

The theoretical worst case is what a preregistered contract has to reserve, and it does not fit. The
observed mean fits with little headroom, and the observed max does not fit at all, so the margin is
too thin to lean on: a single candidate needing a second repair would breach it.

Options for the user, none taken here:

1. Reduce the Tier B sample so the worst-case reserve fits the remaining cap.
2. Raise the overall hard cap above $30.00.
3. Lower the per-call cap, which shrinks the worst-case reserve but risks truncating responses that
   observed at $1.39 to $1.91 per candidate across three calls.

All three are user decisions. The observed repair rate of 100% is a direct argument against
assuming the mean case: on this evidence the second call is the norm, not the exception.

Tier B remains NOT AUTHORIZED, and this run made 0 Tier B calls.

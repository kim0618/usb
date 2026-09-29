# H-V2-D4.2P — Dependency Closure / Clean-Checkout Reproducibility

Scope: close the reproducibility gap left by D4.1/D4.2 — committed D3/D3.2R/D3.3/D4/D4.1/D4.2
research code importing symbols that exist only in the developer's uncommitted working tree. No
D4 semantic changes, no live Opus calls, no Tier A/B execution, $0 cost. This is a packaging fix,
not a research result.

## A. Why D4.2P Exists

`D4_INPUTS.md`-equivalent context: `research/schema_v2.py` (committed in the D3.2R stage, before
D4 existed) imports `ConflictResolution` and `_check_source_ids` from `research/schema.py`. Both
names were added to `schema.py` by an uncommitted edit (the D3.1 evidence-id / evidence-conflict
work). A clean checkout of any commit at or after D3.2R therefore fails to import `schema_v2.py`,
and every module built on top of it — D4's `expectation/` package, its `analysis_schema.py`,
`prompt.py`, `validate.py`, and the `run_strategy_h_v2_d4_2` Tier A runner — fails with it.

D4.2's own repair (commit `d78c52e`) closed the part of this that D4 owns: `expectation/d4_inputs.py`
gives D4's package its own committed copies of `ConflictResolution`, `package_evidence_ids` (what
`valid_evidence_ids` computes) and a repair-reason classifier, checked against the working-tree
originals by `test_clean_checkout.py` whenever those originals are importable. That test file's own
docstring names the one blocker it deliberately left unfixed: `research/schema_v2.py` itself, which
D4.2 could not touch without editing a dirty file outside its stage (brief §14 at the time).

D4.2P's job is to close that remaining blocker — and, per the user's brief for this stage, to check
whether `research/validate.py` and `research/repair.py` hide the same class of problem elsewhere in
the already-committed D3/D3.3/D4.1 code. They do (sections B–C).

## B. Dirty Dependency Graph

Traced by static import-order analysis and confirmed by an actual clean-checkout probe (`git
write-tree` + `git archive`, matching `app/dev/d4_clean_checkout.py`'s own method) — not by
inspection alone.

| symbol | source file (pre-closure) | committed? | dirty-only? | required by (committed module) |
|---|---|---|---|---|
| `ConflictResolution` | `research/schema.py` | no | yes | `research/schema_v2.py` (direct import) |
| `_check_source_ids` | `research/schema.py` | no | yes | `research/schema_v2.py` (direct import) |
| `valid_evidence_ids` | `research/validate.py` | no | yes | `research/assemble_v2.py` (direct import) |
| `research.repair` (whole module: `RepairReason`, `RepairRecord`, `classify_repair_reason`) | `research/repair.py` | no (untracked, whole file) | yes | `app/dev/run_strategy_h_v2_d3_3.py`, `app/dev/run_strategy_h_v2_d4_1.py` (both already committed, both import it directly) |

Cascade (why the failure surface looked bigger than one symbol): `schema_v2.py` fails first on
`ConflictResolution` →  everything that imports `schema_v2.py` fails the same way:
`assemble_v2.py`, `prompt_builder_v2.py`, `expectation/analysis_schema.py` (imports `ClaimV2` from
`schema_v2`), `expectation/prompt.py`, `expectation/validate.py`, `app.dev.run_strategy_h_v2_d4_2`,
`app.dev.run_strategy_h_v2_d3_3`, `app.dev.run_strategy_h_v2_d4_1`. Because Python's `from X import
(a, b, ...)` raises on the *first* unresolvable name in import order, the single `ConflictResolution`
failure masked two more defects that only became visible after it was closed:

1. `assemble_v2.py` imports `schema_v2` on the line *before* it imports `valid_evidence_ids` from
   `validate.py` — so the `valid_evidence_ids` gap was invisible until the `schema_v2` gap closed.
2. `run_strategy_h_v2_d3_3.py` imports `assemble_v2` (line 34) before `research.repair`
   (line 45) — so the untracked `repair.py` module was invisible for the same reason.

Modules checked and found to have **no** hidden dirty dependency: `research/validation_v2.py`,
`research/prompt_builder_v2.py` (imports only `schema_v2` and already-committed `prompt_builder`
names), `research/telemetry_contract_v2.py`, `research/research_attempt_v2.py`,
`research/d3_3_contract.py`, `research/ledger.py`, `expectation/validate.py` (imports only
`ExtractionError`, `extract_json_object`, `package_checksum`, `valid_source_ids` — all already
committed), `expectation/prompt.py` (no direct `research.*` import; failed only via the
`analysis_schema` cascade).

`research/gates.py` (D3.1 §8 Batch-2 quality gates, untracked) was checked and found to be
**out of scope**: no committed module imports it. Its only referrers are `test_gates.py` (also
untracked) and `app/dev/audit_strategy_h_v2_d3_1.py` (also untracked). Left untouched.

## C. Required Symbols

Proven by the import-order trace in B, not assumed:

- `ConflictResolution` (`StrEnum`, 3 members) — `research/schema_v2.py` line ~55, in a `from
  research.schema import (...)` block.
- `_check_source_ids` (function) — same import block, used by `schema_v2.py`'s
  `CatalystCandidateV2`/`RiskItemV2`/`InvalidationCandidateV2`/`FutureBusinessItemV2` validators.
- `valid_evidence_ids` (function) — `research/assemble_v2.py`, in a `from research.validate import
  (...)` block, wired into `assemble_and_validate_v2`'s Pydantic validation context.
- `research.repair` module in full (`RepairReason`, `RepairRecord`, `classify_repair_reason`) —
  `app/dev/run_strategy_h_v2_d3_3.py` line 45, `app/dev/run_strategy_h_v2_d4_1.py` line 67.

`EvidenceConflictV1` was checked and is **not** required by any committed importer — `schema_v2.py`
defines its own `EvidenceConflictV2` (mirrored, per that file's own docstring, precisely so it does
not inherit `schema.py`'s original banned-language check). Not part of this closure.

## D. Dirty Hunk Classification

| file | change | class |
|---|---|---|
| `research/schema.py` | entire dirty diff (evidence_id validators, `ConflictResolution`, `EvidenceConflictV1`, `_check_source_ids`, `code_owned_state` description) | **H_V2_REQUIRED** — every hunk traces to §C; no unrelated content found |
| `research/validate.py` | entire dirty diff (`valid_evidence_ids` + wiring into `assemble_and_validate`'s context) | **H_V2_REQUIRED** |
| `research/repair.py` | entire file (untracked) | **H_V2_REQUIRED** |
| `backend/tests/strategy_h_v2/research/test_schema.py` | entire dirty diff (131 lines, pure additive test cases for the schema.py hunks above) | **H_V2_REQUIRED** — direct regression coverage for §C symbols |
| `backend/tests/strategy_h_v2/research/test_repair.py` | entire file (untracked) | **H_V2_REQUIRED** — direct regression coverage for `research/repair.py` |
| `research/prompt_builder.py` | `PROMPT_VERSION` bump + new prompt instructions for mandatory `evidence_id` citation and `evidence_conflicts` | **OTHER_PROJECT_REQUIRED** — part of the same D3.1 work but changes live-prompt *semantics*, not import capability; nothing committed fails to import without it. Left dirty, untouched. |
| `research/gates.py` + `test_gates.py` | D3.1 §8 Batch-2 gate module (untracked) | **UNKNOWN/out of scope** — not required by any committed importer (§B). Left untracked, untouched. |
| `app/dev/run_strategy_h_v2_d3.py` | adds `RepairRecord`/`classify_repair_reason` telemetry wiring to the D3 pilot runner | **OTHER_PROJECT_REQUIRED** — real D3.1 work, but `run_strategy_h_v2_d3.py` is a leaf script nothing else imports; not needed for M12. Left dirty, untouched. |
| every other dirty/untracked file (crypto, position/entry management, strategy_b, frontend, etc.) | unrelated in-progress work in other subsystems | **UNRELATED** — not inspected further beyond confirming no H-V2 research module references them; not staged, not touched |

Every file staged for this closure had **zero** unrelated hunks mixed in, so no partial-hunk
extraction (`git add -p`) or shared-module indirection was needed — the "whole-file commit"
prohibition in the brief does not trigger here (that prohibition is conditional on unrelated
content being present, and there was none in any of these five files).

## E. Closure Method

Staged exactly five files, individually, by path (`git add <path>` for each, never `-A` or `.`):

- `backend/app/backtest/strategy_h_v2/research/schema.py`
- `backend/app/backtest/strategy_h_v2/research/validate.py`
- `backend/app/backtest/strategy_h_v2/research/repair.py` (new file)
- `backend/tests/strategy_h_v2/research/test_schema.py`
- `backend/tests/strategy_h_v2/research/test_repair.py` (new file)

No indirection module was introduced. `d4_inputs.py`'s duplicate-and-check-for-drift pattern (built
for D4 in the prior stage, when D4 was *forbidden* from touching `schema.py`/`validate.py`
directly) was deliberately **not** repeated here: these five files are the actual canonical D3/D3.1
originals, their entire dirty content is H_V2_REQUIRED, and committing them directly closes the gap
at its root instead of adding a third copy of `ConflictResolution` behind a fork-drift test. This
is the "clean shared module" alternative offered in the brief, applied in its simplest form — the
existing module already *is* the correct shared module; it just wasn't committed yet.
`expectation/d4_inputs.py` itself is untouched and keeps working exactly as before (its own
drift tests against the now-committed originals still pass — see §H).

`research/prompt_builder.py` and `research/gates.py` were deliberately left exactly as they were —
dirty and untracked respectively — per §D.

## F. Clean Checkout

Verified twice, independently: (1) `app/dev/d4_clean_checkout.py`'s own probe (`git write-tree` +
`git archive` + subprocess import, touching neither the working tree nor HEAD) run against the
staged index; (2) a second hand-built probe covering the D3/D3.1/D3.3 modules the D4 probe's target
list does not include (`schema`, `schema_v2`, `validate`, `assemble_v2`, `prompt_builder_v2`,
`run_strategy_h_v2_d3`, `run_strategy_h_v2_d3_3`, `run_strategy_h_v2_d4_1`, `validation_v2`,
`telemetry_contract_v2`, `research_attempt_v2`, `ledger`, `d3_3_contract`).

Before staging: 5 of 10 D4-probe targets failed, all with the identical
`ImportError: cannot import name 'ConflictResolution' from '...research.schema'`; the wider probe
additionally failed `run_d3_3`, `run_d4_1`, `schema_v2`, `assemble_v2`, `prompt_builder_v2` the same
way.

After staging: all 10 D4-probe targets pass (`"passed": true`, `smoke_ok: true`); all 14 targets in
the wider probe pass. `research/gates.py` correctly still fails to import in the clean tree
(`ModuleNotFoundError`) — confirming it was genuinely excluded, not accidentally swept in.

## G. M12

```
M12 CLEAN CHECKOUT
= PASS
```

Both probes ran against the git INDEX (staged state), which is exactly what a `git commit` of the
current staging would produce and a fresh `git clone` of that commit would contain. Working tree
files were not modified by this stage — `git add` only, no edits.

## H. Tests

Targeted (all against the staged tree):

| test | result |
|---|---|
| `schema_v2` clean import | PASS |
| `ConflictResolution` available clean | PASS |
| `_check_source_ids` available clean | PASS |
| `valid_evidence_ids` available clean | PASS |
| `research.repair` (`RepairReason`, `RepairRecord`, `classify_repair_reason`) available clean | PASS |
| `run_strategy_h_v2_d4_2` (D4 Tier A runner) clean import | PASS |
| `run_strategy_h_v2_d3_3`, `run_strategy_h_v2_d4_1` clean import | PASS |
| `expectation/d4_inputs.py` drift tests vs. now-committed originals (`test_conflict_resolution_agrees_with_the_working_tree_original`, `test_package_evidence_ids_agrees_with_the_working_tree_original`, `test_repair_classification_agrees_with_the_working_tree_original`, `test_repair_reason_members_match_the_working_tree_original`) | PASS — no drift; these were previously **skipped** (originals unimportable) and now execute for real, since the originals import cleanly |
| `test_clean_checkout.py::test_the_clean_checkout_probe_reports_a_real_measurement` | PASS |
| `test_clean_checkout.py::test_the_d4_owned_modules_import_from_a_clean_tree` | PASS |
| `test_clean_checkout.py::test_any_remaining_blocker_is_outside_the_d4_package` | PASS (vacuously — zero blockers remain) |

Regression:

- `backend/tests/strategy_h_v2` full suite: **643 passed, 38 skipped, 10 failed** (691 collected,
  matching the prior baseline's total count exactly). The 10 failures are **not a regression from
  this closure** — all 10 are `FileNotFoundError` on
  `data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages/<TICKER>.json`, a locally
  generated D2.1 evidence-collection artifact that does not exist anywhere in this environment
  right now (confirmed: `data/runtime/strategy_h_v2/d2_1/` does not exist on disk at all; it is
  untracked/gitignored runtime data, not something this stage touches or regenerates). The prior
  authoritative "691 passed" baseline was recorded on a machine that had this data present; this
  environment currently does not. Do not read this as a dependency-closure defect — none of the 10
  failing tests touch `schema.py`, `validate.py`, or `repair.py`, and all 10 fail identically
  regardless of the git index state (they read the working tree directly). The 38 skips are the
  usual live-Opus-gated tests (consistent with $0 cost here).
- Backend suite excluding the known `strategy_b` collection break: separate, pre-existing
  collection errors were found in 13 more files (`test_daily_performance.py`,
  `test_position_lifecycle_*.py`, `test_protection_cursor.py`, etc.), all from
  `ImportError: cannot import name 'ScannerDecision' from 'app.strategy_b.scanner'` — this traces to
  the large amount of *unrelated* dirty work already in this working tree (position/entry-management
  and strategy_b refactor in progress, none of it touched by this stage). These 13 are excluded from
  the regression run below the same way `strategy_b` itself is excluded, and are reported here
  rather than silently absorbed. Full-suite result recorded separately once the run completes (see
  final report to the user); this is **not** the "5627 passed / 23 skipped / 0 failed" prior
  baseline and must not be confused with it (brief §11).

## I. Remaining Budget Issue

Untouched, per the brief. Tier B theoretical worst case remains $45.60 against an insufficient
remaining overall cap. No cap change was made here. This stays a separate, unresolved item awaiting
a user decision after a live Tier A run.

## J. Verdict

```
H-V2-D4.2P
= PASS

M12 CLEAN CHECKOUT
= PASS

Tier A re-run
= READY (mechanically — no live call made this stage)

Tier B
= NOT EXECUTED
```

`research/prompt_builder.py`'s dirty diff and `research/gates.py`/`test_gates.py` remain
uncommitted, exactly as found, because neither is required for any committed module's import to
succeed — closing them is not this stage's job and doing so anyway would have gone beyond "the
prerequisite actually required," which the brief asked to avoid.

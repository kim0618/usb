# A-MOVER-LIVE-V1 operational hardening

> **HISTORICAL stage record (2026-10-04).** The isolation it added (`attach_isolated`) is in force; its "not deployed / flag off" statements describe that day only. Current contract: `docs/operations/A_MOVER_NEXT_SESSION_AUTHORITY_V1.md`.

Validated on 2026-10-04 (Asia/Seoul). Scope: isolate A's attach/cut failures from E's worker,
and refuse E staging artifacts from another session. No deployment action is authorized by
this stage. Status: **READY_FOR_PRODUCTION_DEPLOY for this hardening scope**; this does not
claim a clean full backend suite or that the production session has already been staged.

## Git

- Branch: `main`.
- Starting HEAD and local `origin/main`: `b01dacb616598429de0153908571a22e9609654c`.
- Only the eight files listed below belong in the hardening commit. Other modified/untracked
  files in the shared workspace are preserved and excluded.
- Commit message: `fix(strategy-a): isolate mover attach and reject stale E staging`.
- Push: 0. No fetch, merge, rebase, checkout, restore, stash or reset.

## Changed

1. `backend/app/dev/run_e_rt2_dryrun.py`: call the isolated attach and cut boundary.
2. `backend/app/strategy_a_mover_live/config.py`: name `ATTACH_FAILED` explicitly.
3. `backend/app/strategy_a_mover_live/integration.py`: isolate attach and classify cut failures.
4. `backend/app/strategy_a_mover_live/isolation.py`: failure classification and per-session audit.
5. `backend/app/strategy_a_mover_live/universe.py`: current-session-only E staging resolution.
6. `backend/tests/strategy_a_mover_live/test_a_live_isolation.py`: 42 isolation/staging checks,
   including E builder/write/read and the real E worker with offline lanes.
7. `docs/backtest/mover_scanner_v1/A_MOVER_LIVE_V1.md`: remove outdated attach/staging claims.
8. This report.

## Behavior and design

| Requirement | Result |
|---|---|
| A attach isolation | PASS |
| A failure candidates / GPT / paper injections | 0 / 0 / 0 |
| A failure audit | FAILED with phase, refusal and reason; repeated identical failures counted |
| E acquisition / refresh / finalization / paper after A failure | continue |
| A OFF | no extra E report key, audit write or cache membership |
| Unexpected programming errors | traceback audited; worker continues; strict mode re-raises |
| KeyboardInterrupt / SystemExit | propagate |
| Unwritable audit | logged and returned as audit_error; E continues |
| E stale artifact | explicitly refused, previous-session fallback DISABLED |
| Current-session identity | generated, persisted and accepted through E's own identity function |
| E SLA | 09:25 T0 and 09:29:45 deadline unchanged |
| A strategy / risk / entry / exit / GPT / mixed baseline | unchanged |
| V2 scope | no additions |

E's official identity function is `app.strategy_e_max_rt.universe_build.d_minus_1_identity`:
target session must match the expected session; as-of session must be its previous trading day;
the current artifact format also verifies its own digest. A does not invent another identity
rule. A missing current artifact with a previous file present reports `STALE_STAGING_ARTIFACT`.
Malformed/undated JSON reports `MALFORMED_STAGING_ARTIFACT`. Neither contributes E symbols.
A can still plan its own reference-derived universe; E's worker independently refuses a
missing/stale current artifact with `UNIVERSE_NOT_AVAILABLE` and no E decision.

Current generation path: `app.dev.run_e_universe.build` calls `universe_build.build` and
`write`, naming `universe_<target session>.json`; target/as-of identity is persisted in the
payload. The regression test exercises that builder and writer with local fixture inputs,
then A's actual reader. No production staging was executed.

The real-worker tests compare A OFF against expected and unexpected attach errors. E's
capacity, lanes, rolling counts, availability, order summary and session record are equal;
`symbol_status.csv`, `lane_stats.json` and `cutoff_audit.json` are byte-identical. Finalization
actually completes for E's two fixture symbols and SPY, followed by the paper stage. Provider
and paper execution are offline stubs; this is control-flow regression evidence, not a
measurement of network performance or a real trading session.

Audit path: `data/runtime/strategy_a_mover_live/status/<session>.json`. Atomic replacement
protects against partial writes. Identity is phase/refusal/reason/detail; retries update
occurrences and timestamps. There is no legacy scanner, prior-session candidate, same-day
Massive premarket or stale snapshot fallback.

## Tests

All commands below use `PYTHONPATH=backend .venv/bin/python` from the repository root.

- `-m pytest backend/tests/strategy_a_mover_live -q`: **253 passed**.
- `-m pytest backend/tests/strategy_e_max backend/tests/strategy_e_trading
  backend/tests/test_research_stage4.py backend/tests/test_premarket_volume_v2.py -q`:
  **696 passed**. Targeted total: **949 passed**, no related failures.
- `-m app.dev.run_a_mover_live_dry_run dry-run --session 2026-10-05 --symbols 60
  --kiwoom-sessions 10`: **19/19 checks true**; mixed bootstrap 10 Kiwoom / 10 Massive,
  8 candidates, 7 mocked human approvals, 7 approved injections, 0 real orders.
- `-m app.dev.run_a_mover_live_dry_run budget --session 2026-10-05`: A universe/union 5,015;
  `universe_2026-09-22.json` explicitly refused; modeled A T1 09:23:42.392, E T1
  09:29:26.700; lane rate 4.89/s, all SLA checks true. E cost is the existing measured budget
  input, not a new current-session observation.
- `git diff --check`: PASS.
- Standalone source export: committed files plus precisely the hardening runtime/test files;
  no other shared modified/untracked source copied. **253 A tests passed**, **21 imports
  passed**, including `app.main`, A live modules, E runtime, shared worker and staging CLI.
  A runtime untracked source dependencies: **0**. Default OFF and live checksum unchanged:
  `382ba2c16409cb1009006b992c2c0d05e0c8a25c29ee768f997c5027fbd3f22b`.

## Issues and limits

Full backend command `-m pytest backend/tests -q` is **not green**: 20 collection errors
from existing missing `SettlementAction` / Strategy B exports (`OBSERVATION_ONLY` and
`ScannerDecision`). These imports are outside the eight changed files, including the
explicitly protected `entry_management_runtime.py`, and were not edited. A second attempt
with `--continue-on-collection-errors` reached **559 passed, 20 collection errors** before
being interrupted during the unrelated full-window crypto parity calculation. This is an
incomplete full run, not a full-suite pass; no claim is made about tests beyond that point.

The sandbox stalls on existing asyncio/TestClient shutdown. The same isolated asyncio test
passes outside the sandbox, and the complete 696-test E/GPT/baseline suite passes there.
No source fix was made for this execution-environment behavior.

Current-session E staging is still an operational prerequisite. The local disk currently
contains the old 2026-09-22 artifact, which is now refused for 2026-10-05. This stage does not
create or deploy a production artifact. Existing bootstrap coverage/launch-window limitations
in the main live report also remain unchanged.

## Hard stop

Push / deploy / production pull / restart / timer install or enable / feature flag ON /
migration apply / production DB mutation / real orders: **0 by this session**.
`A_MOVER_LIVE_ENABLED` is unset in the local `.env` and resolves OFF. Stop after the scoped
commit and final standalone verification; deployment requires its own task.

# A Paper <-> Backtest True Parity V1

audit_id `1604df05f028434ccb0a`. No performance replay, no network, no real order, no production DB read, nothing deployed.

## Status

| Item | Status |
|---|---|
| PREMARKET_VOLUME_CANONICALIZATION | PARTIAL |
| NUMERATOR_PARITY | UNKNOWN |
| DAILY_DENOMINATOR | UNIFIED (opt-in PREMARKET_VOLUME_BASIS_V2; stored research runs stay V1) |
| SCANNER_CORE | SHARED |
| UNIVERSE_AUTHORITY | READY |
| RECORDED_PAPER_UNIVERSE | PARTIAL (recording implemented locally, migration 0018 not deployed; replay reader for re-ranking a recorded universe not built) |
| APPROVAL_AUTHORITY | READY |
| RECORDED_PAPER_APPROVAL | READY (existing importer; needs a paper DB snapshot) |
| OVERNIGHT_AUTHORITY | READY |
| TRAILING_AUTHORITY | READY |
| CORE_ENTRY_ENGINE | SHARED |
| RISK_ENGINE | SHARED |
| EXECUTION | SHARED |
| POSITION_MANAGEMENT | PARTIAL |
| DIFFERENTIAL_FIXTURE | PASS |

TYPE C: 15 before; 14 with `PREMARKET_VOLUME_BASIS_V2`; stored V1 research runs still 15.

## Premarket volume

- denominator: {"paper": "Kiwoom daily acc_trde_qty (whole session)", "backtest_v1": "sum of REGULAR Massive minute volumes", "backtest_v2": "Massive daily aggregate volume (docs/MASSIVE_DAILY_BASIS.md 2.1: MATCHING_BASIS with Kiwoom acc_trde_qty)", "caveat": "Kiwoom daily is requested with upd_stkpc_tp=1 (adjustment semantics UNKNOWN) while Massive is adjusted=false: they can differ on split sessions"}
- numerator: UNKNOWN. no local artifact holds Kiwoom premarket volume for dates the Massive tape covers (local SQLite files hold no premarket_diagnostics rows); the 50-56% figure is not a repo artifact; production DB reads and network calls were out of scope. No scaling factor was made up.
- canonical context: the engine's PremarketContext (built only by build_premarket_context) is the canonical premarket context for both paths; provenance lives in PremarketBuildResult.diagnostic and, for V2 runs, in run.premarket_volume_basis.

## Authority model

- candidate: CandidateMode RESEARCH_UNIVERSE | RECORDED_ONLY
- approval: ApprovalMode ALL_RESEARCH_SYMBOLS | RECORDED_ONLY
- overnight: OvernightMode RECORDED | UNKNOWN_CLOSE | ASSUMED_*
- trailing: TrailingMode RECORDED | UNKNOWN_DEFAULT | ASSUMED_*
- labels: PAPER_RECORDED = RECORDED modes read from a paper DB snapshot (authority/importer.py); RESEARCH_HISTORICAL = RESEARCH_UNIVERSE + ASSUMED, label RESEARCH_ONLY
- no_silent_fallback: tests/test_historical_authority_layer.py:167,175,345 (missing record -> UNKNOWN, never ASSUMED)

## TYPE C after this stage

| Item | State |
|---|---|
| PM volume denominator source | RESOLVED as opt-in V2 (daily aggregate = Kiwoom acc_trde_qty basis); stored V1 runs unchanged |
| PM volume numerator source | OPEN: NUMERATOR_PARITY UNKNOWN (no stored Kiwoom premarket volume outside the production DB; no network in this stage) |
| Trailing multiplier authority | OPEN for historical research (no GPT record); forward: TrailingMode.RECORDED via the existing importer |
| Overnight authority | OPEN for historical research; forward: OvernightMode.RECORDED |
| Starting capital | OPEN: cash is already an explicit plan input and in the identity; its source label is still the constant ASSUMED_RESEARCH |
| Candidate universe | OPEN for historical research; forward: recorded (scanner_universe_inputs, after migration 0018 is deployed) and CandidateMode.RECORDED_ONLY for candidates |
| Scanner market-cap input | OPEN historically; forward: recorded per universe row |
| Scanner daily cut | OPEN: Kiwoom 07:00 KST partial vs Massive 20:00 ET |
| Entry approval | OPEN for historical research; forward: ApprovalMode.RECORDED_ONLY via the existing importer |
| Candidate evaluation order | OPEN for historical research; forward: recorded gpt_rank |
| Minute data provider | OPEN: unavoidable provider difference |
| Daily price adjustment | OPEN: UNKNOWN_PRICE_ADJUSTMENT_AUTHORITY (Kiwoom upd_stkpc_tp=1 semantics unconfirmed, docs/REAL_MARKET_SCANNER.md) |
| Bar timestamp / availability | OPEN: engine consumes completed bars only (available_at = +1 min in both); real Kiwoom latency not recorded |
| Tick loop / owner order | OPEN |
| Orchestration wrappers | OPEN: Day2 activation, holding limit, add, daily state still duplicated (not refactored in this stage) |

## Code changes (uncommitted)

| File | Change |
|---|---|
| `app/backtest/replay/daily_volume.py (new)` | PREMARKET_VOLUME_BASIS_V1 / V2 names; DailyAggregateVolumeProvider serves daily aggregate bars clamped to one clock and reports each to the PIT audit |
| `app/services/entry_management_runtime.py` | build_premarket_context gains keyword-only daily_volume_provider (default None = unchanged paper and V1 behaviour) |
| `app/backtest/replay/session_replay.py` | EntrySessionRunner passes an optional daily_volume_provider to build_premarket_context |
| `app/backtest/portfolio/config.py` | PortfolioConfig.premarket_volume_basis (default V1 writes nothing to as_dict/lines) |
| `app/backtest/portfolio/replay.py` | premarket_daily_bars input; must match the declared basis; builds the V2 source on the replay clock |
| `app/backtest/baseline/runner.py` | with_premarket_volume_basis(plan, V2); V2 loads the plan's verified daily aggregate tape; run id exp1; document run.premarket_volume_basis; basis-specific warning |
| `app/backtest/research/workspace_store.py` | public bars(symbol) accessor over the stored daily aggregate bars |
| `app/models/scanner.py + migrations/versions/20260921_0018_scanner_universe_inputs.py (new)` | scanner_universe_inputs: provider-ordered universe with exchange, market cap text, scanner outcome (rank / exclusion reason / NOT_EVALUATED), run checksum |
| `app/repositories/scanner.py` | ScannerUniverseInputData, universe_checksum, add/get_universe_inputs |
| `app/services/scanner.py` | persist_result(universe=..., universe_source=..., universe_acquired_at=...) records the universe in the run's transaction; default unchanged |
| `app/dev/run_morning_scanner.py` | passes the Kiwoom universe, source KIWOOM_REAL:usa20540+usa20550 and acquisition time |
| `tests/test_true_parity.py (new)` | 11 tests: paper = V2 canonical context, V1 vs V2 denominator, PIT clamp, default bytes, basis/bars mismatch refused, identity delta, stored fingerprints, universe recording, checksum, default-no-universe, migration 0017->0018->0017 round trip |
| `app/services/entry_drift_observer.py, app/services/premarket_volume_history.py` | OBSERVER_SCHEMA_REVISION / HISTORY_SCHEMA_REVISION 0017 -> 0018 (same convention as the 0017 commit 0efeaf6): their --write CLIs require the DB at the code head, so code and migration must be deployed together |
| `app/models/__init__.py; tests pinned to the head` | ScannerUniverseInput exported; head/table-count pins moved to 0018 / 22 tables |

## Tests

{"related_regression_108_files": "2582 passed, 6 failed (all six were the 0017 head pins), 13 skipped", "after_head_pin_update": "152 passed (true parity 11, schema/migration, evaluation history, premarket volume v2, entry drift observer, scanner service, morning scanner)", "excluded": "backend/tests/strategy_b/* and test_strategy_b_historical_scanner.py: collection errors from another session in-progress strategy_b code", "passed": 2588, "failed": 0}

## Existing results

exp1-32cf23503bba977528fa and exp1-aad5293b786fab2006ae are untouched and labelled RESEARCH_ENVIRONMENT_V1. They cannot be reused as a true paper optimization result.

## Next

PAPER_RECORDED_SESSION_DIFFERENTIAL_VALIDATION_V1 (needs: deploy of 0018 by the user, and a paper DB snapshot the session may read)

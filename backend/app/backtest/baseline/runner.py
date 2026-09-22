"""The Current Strategy Baseline, end to end, on the shared workspace.

For every entry session E of the planned range the Historical Research Scanner scans
D = E's predecessor, its TOP8 becomes E's field under ALL_RESEARCH_SYMBOLS, and the
Multi-Symbol Portfolio Replay runs entry, position and end-of-day on one account and one
clock across the whole range. This module adds no decision to that pipeline. It plans
the range, proves the minute and daily tapes cover it, freezes the configuration and the
authority, runs the existing replay once, and turns what it recorded into a
BacktestResult V1 document.

Nothing here opens a database or makes a request. The workspace is only read; writing
the result is ``storage``'s job and takes the writer lock.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
import time

from app.backtest.authority.contract import AUTHORITY_VERSION, AuthorityModes, OvernightMode
from app.backtest.baseline import analytics
from app.backtest.baseline.config_snapshot import strategy_config_snapshot
from app.backtest.baseline.contract import (
    AUTHORITY_LABEL, BASELINE_VERSION, PREMARKET_HISTORY_SESSIONS, RESULT_SCHEMA_VERSION,
    SETTLEMENT_SESSIONS, STARTING_CASH, STARTING_CASH_SOURCE, assert_baseline_modes,
    assert_research_overnight_modes, authority_block, baseline_modes,
    research_authority_block, research_overnight_modes, starting_capital_block,
)
from app.backtest.baseline.coverage import (
    BaselineRange, SymbolMinuteCoverage, assert_covered, measured_sessions, plan_baseline_range,
)
from app.backtest.baseline.errors import (
    BaselineAccountingFailed, BaselineCoverageIncomplete, BaselineError,
)
from app.backtest.baseline.experiment import (
    EXPERIMENT_RUN_ID_PREFIX, config_overrides, identity_lines, parameter_set_block,
)
from app.backtest.baseline.report import ai_export, report_markdown
from app.backtest.baseline.schema import TABLES, rows_of, text, validate
from app.backtest.baseline.storage import Artifacts, build_artifacts
from app.backtest.collector.collector import PROVIDER as MINUTE_PROVIDER
from app.backtest.collector.daily_dataset import DAILY_DATA_KIND, DAILY_PROVIDER, DAILY_TIMEFRAME
from app.backtest.portfolio.candidates import ResearchScannerCandidateSource
from app.backtest.portfolio.config import PORTFOLIO_REPLAY_VERSION, PortfolioConfig
from app.backtest.portfolio.identity import PortfolioRunIdentity, build_identity
from app.backtest.portfolio.ledger import LedgerEvent
from app.backtest.portfolio.replay import MultiSymbolPortfolioReplay
from app.backtest.portfolio.result import PortfolioReplayResult
from app.backtest.replay.clock import TICK_EPSILON
from app.backtest.replay.daily_volume import (
    PREMARKET_VOLUME_BASES, PREMARKET_VOLUME_BASIS_V1,
)
from app.backtest.replay.dataset import DatasetIdentity, _complete_entry, _verify_entry
from app.backtest.replay.position_replay import POSITION_REPLAY_VERSION
from app.backtest.replay.session_replay import REPLAY_VERSION
from app.backtest.research.contract import checksum
from app.backtest.research.errors import ResearchScannerError
from app.backtest.research.metadata import ResearchMetadataSet
from app.backtest.research.scanner import scanner_config_fingerprint
from app.backtest.research.universe_file import ResearchUniverseFile
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.manifest import entries_of_kind, files_match, manifest_connection
from app.execution.config import ExecutionConfig
from app.market.calendar import MarketCalendar
from app.risk.config import RiskConfig
from app.scanner.config import ScannerConfig
from app.services.position_protection import ACTUAL_VARIANT
from app.strategy.engine import StrategyV0Engine

RUN_ID_PREFIX = "csb1"
#: The scanner's own warmup: ``required_history`` sessions plus the daily store margin.
DAILY_WARMUP_MARGIN_SESSIONS = 5


def daily_warmup_sessions(config: ScannerConfig) -> int:
    return config.required_history + DAILY_WARMUP_MARGIN_SESSIONS - 1


@dataclass(frozen=True)
class BaselineInputs:
    workspace: Workspace
    universe: ResearchUniverseFile
    metadata: ResearchMetadataSet
    metadata_source: str
    requested_start: date
    requested_end: date
    #: The last session whose tape had ended when the run was asked for (scanner PIT bound).
    completed_through: date
    calendar: MarketCalendar
    starting_cash: Decimal = STARTING_CASH
    #: Where the tape is read from. ``None`` is the legacy collector manifest.
    binding: object | None = None

    def data_binding(self):  # type: ignore[no-untyped-def]
        from app.backtest.baseline.binding import ManifestBinding
        return self.binding if self.binding is not None else ManifestBinding(self.workspace)


@dataclass(frozen=True)
class DailyEntry:
    symbol: str
    status: str
    start: date | None
    end: date | None
    checksum: str | None
    collector_version: str | None
    verified: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"symbol": self.symbol, "status": self.status, "start": text(self.start),
                "end": text(self.end), "checksum": self.checksum,
                "collector_version": self.collector_version, "verified": self.verified,
                "detail": self.detail}


@dataclass(frozen=True)
class BaselinePlan:
    inputs: BaselineInputs
    range: BaselineRange
    declared: tuple[str, ...]
    scanned: tuple[str, ...]
    not_scanned: Mapping[str, str]
    metadata_excluded: Mapping[str, str]
    required: tuple[str, ...]
    daily: tuple[DailyEntry, ...]
    daily_scan_start: date
    daily_scan_end: date
    coverage: tuple[SymbolMinuteCoverage, ...]
    scanner_config: ScannerConfig
    #: None is the baseline authority. Anything else is a RESEARCH experiment's explicit
    #: overnight declaration, set only by ``with_research_overnight``.
    overnight_override: OvernightMode | None = None
    #: Which daily volume the premarket gate divides by. V1 is every stored run's basis;
    #: V2 is set only by ``with_premarket_volume_basis`` and makes the run an experiment.
    premarket_volume_basis: str = PREMARKET_VOLUME_BASIS_V1

    @property
    def modes(self) -> AuthorityModes:
        if self.overnight_override is None:
            return baseline_modes(self.declared)
        return research_overnight_modes(self.declared, self.overnight_override)

    def assert_modes(self) -> None:
        if self.overnight_override is None:
            assert_baseline_modes(self.modes)
        else:
            assert_research_overnight_modes(self.modes)

    def authority(self) -> dict[str, object]:
        if self.overnight_override is None:
            return authority_block(self.modes)
        return research_authority_block(self.modes)

    def authority_override_lines(self) -> tuple[str, ...]:
        # Only an overnight experiment adds a line: the baseline keeps its old identity.
        if self.overnight_override is None:
            return ()
        return (f"authority_override=overnight_mode:{OvernightMode.UNKNOWN_CLOSE.value}"
                f"->{self.overnight_override.value}",)

    def portfolio_config(self) -> PortfolioConfig:
        return PortfolioConfig(starting_cash=self.inputs.starting_cash, universe=self.declared,
                               trading_dates=self.range.entry_sessions, top8_only=True,
                               settlement_sessions=SETTLEMENT_SESSIONS,
                               benchmark_symbol=self.inputs.universe.benchmark_symbol,
                               premarket_volume_basis=self.premarket_volume_basis)


def with_research_overnight(plan_: BaselinePlan, overnight: OvernightMode) -> BaselinePlan:
    """The same plan as a RESEARCH experiment that declares ``overnight`` for every symbol.

    The one path to a non-baseline overnight authority. The declaration is checked here
    (``RESEARCH_OVERNIGHT_OVERRIDES``), enters the run identity as an
    ``authority_override`` line and the authority block with source ASSUMED, and gives the
    run an experiment id. The strategy, risk and execution configuration are untouched.
    """
    research_overnight_modes(plan_.declared, overnight)
    return replace(plan_, overnight_override=overnight)


def with_premarket_volume_basis(plan_: BaselinePlan, basis: str) -> BaselinePlan:
    """The same plan with the premarket denominator read from ``basis``.

    ``PREMARKET_VOLUME_BASIS_V2`` divides by the daily aggregate volume of the plan's own
    verified daily tape (the store the scanner reads), the quantity paper reads from Kiwoom
    ``acc_trde_qty``. The basis enters the portfolio config lines, so the run identity and
    run id move; no strategy, risk or execution value changes.
    """
    if basis not in PREMARKET_VOLUME_BASES:
        raise BaselineError(f"unknown premarket volume basis {basis}")
    return replace(plan_, premarket_volume_basis=basis)


# --- planning -----------------------------------------------------------------------------------


def daily_entries(workspace: Workspace, symbols: Sequence[str]) -> tuple[DailyEntry, ...]:
    with manifest_connection(workspace, create=False) as connection:
        rows = {str(row["symbol"]): row for row in entries_of_kind(
            connection, provider=DAILY_PROVIDER, data_kind=DAILY_DATA_KIND,
            timeframe=DAILY_TIMEFRAME) if str(row["status"]) == "COMPLETE"}
        entries = []
        for symbol in symbols:
            row = rows.get(symbol)
            if row is None:
                entries.append(DailyEntry(symbol, "NO_COMPLETE_ENTRY", None, None, None, None,
                                          False, "no COMPLETE daily manifest entry"))
                continue
            matched, detail = files_match(connection, int(row["id"]), workspace=workspace)
            entries.append(DailyEntry(
                symbol, "COMPLETE", date.fromisoformat(str(row["start_date"])),
                date.fromisoformat(str(row["end_date"])), str(row["checksum"]),
                str(row["collector_version"]), matched, detail))
    return tuple(entries)


def plan(inputs: BaselineInputs, *, minute_available_end: date | None = None,
         scanner_config: ScannerConfig | None = None) -> BaselinePlan:
    """Plan the range from the daily store and the minute manifest, and preflight coverage."""
    calendar = inputs.calendar
    binding = inputs.data_binding()
    config = scanner_config or ScannerConfig()
    declared = tuple(inputs.universe.symbols)
    benchmark = inputs.universe.benchmark_symbol
    store = binding.daily_store(calendar)
    scanned: list[str] = []
    not_scanned: dict[str, str] = {}
    for symbol in (*declared, benchmark):
        try:
            store.coverage(symbol)
            scanned.append(symbol)
        except ResearchScannerError as error:
            not_scanned[symbol] = f"NO_DAILY_DATA:{getattr(error, 'code', type(error).__name__)}"
    if benchmark in not_scanned:
        raise BaselineCoverageIncomplete(f"benchmark {benchmark} has no daily file")
    daily = binding.daily_entries(scanned)
    unverified = [f"{item.symbol}={item.status}({item.detail})" for item in daily
                  if not item.verified]
    if unverified:
        raise BaselineCoverageIncomplete("daily files do not verify: " + ", ".join(unverified))
    warmup = daily_warmup_sessions(config)
    starts = [_after(calendar, item.start, warmup) for item in daily]  # type: ignore[arg-type]
    daily_scan_start = max(starts)
    daily_scan_end = min(item.end for item in daily)  # type: ignore[type-var]
    metadata_excluded = {symbol: "MISSING_METADATA" for symbol in declared
                         if symbol in scanned and inputs.metadata.get(symbol) is None}
    required = tuple(symbol for symbol in declared
                     if symbol in scanned and symbol not in metadata_excluded)
    minute_start, minute_end = binding.minute_bounds(required)
    planned = plan_baseline_range(
        calendar, requested_start=inputs.requested_start, requested_end=inputs.requested_end,
        daily_scan_start=daily_scan_start, daily_scan_end=daily_scan_end,
        minute_available_start=minute_start,
        minute_available_end=minute_available_end or minute_end or inputs.completed_through)
    coverage = binding.coverage_preflight(
        required, calendar=calendar, required_start=planned.minute_required_start,
        required_end=planned.minute_required_end)
    return BaselinePlan(inputs, planned, declared, tuple(item for item in scanned
                                                         if item != benchmark),
                        dict(sorted(not_scanned.items())), metadata_excluded, required, daily,
                        daily_scan_start, daily_scan_end, coverage, config)


def _after(calendar: MarketCalendar, day: date, sessions: int) -> date:
    for _ in range(sessions):
        day = calendar.next_trading_day(day)
    return day


def _minute_bounds(workspace: Workspace, symbols: Sequence[str]) -> tuple[date | None, date | None]:
    """The latest start and the earliest end among the symbols' COMPLETE minute entries."""
    starts, ends = [], []
    with manifest_connection(workspace, create=False) as connection:
        for symbol in symbols:
            try:
                row = _complete_entry(connection, MINUTE_PROVIDER, symbol)
            except Exception:  # noqa: BLE001 - the preflight names the reason
                continue
            starts.append(date.fromisoformat(str(row["start_date"])))
            ends.append(date.fromisoformat(str(row["end_date"])))
    return (max(starts) if starts else None, min(ends) if ends else None)


# --- identity -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineIdentity:
    run_id: str
    run_identity: str
    block: str
    portfolio: PortfolioRunIdentity
    config_snapshot: Mapping[str, object]
    #: Fields that differ from the code's defaults; empty for the baseline itself.
    overrides: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


def minute_identities(workspace: Workspace, symbols: Sequence[str]) -> dict[str, DatasetIdentity]:
    """Each symbol's verified minute identity, from the manifest and the file hashes only."""
    identities = {}
    with manifest_connection(workspace, create=False) as connection:
        for symbol in symbols:
            identities[symbol] = _verify_entry(
                connection, _complete_entry(connection, MINUTE_PROVIDER, symbol), workspace)
    return identities


def baseline_identity(plan_: BaselinePlan, minute: Mapping[str, DatasetIdentity], *,
                      engine: StrategyV0Engine, risk: RiskConfig,
                      execution: ExecutionConfig) -> BaselineIdentity:
    modes = plan_.modes
    plan_.assert_modes()
    snapshot = strategy_config_snapshot(engine.config, risk, execution)
    overrides = config_overrides(engine.config, risk, execution)
    portfolio = build_identity(
        config=plan_.portfolio_config(), modes=modes, datasets=minute,
        daily_checksums=tuple(sorted((item.symbol, str(item.checksum)) for item in plan_.daily)),
        research_universe_checksum=plan_.inputs.universe.checksum,
        metadata_checksum=plan_.inputs.metadata.checksum,
        scanner_version=ResearchScannerCandidateSource.version,
        scanner_config_fingerprint=scanner_config_fingerprint(plan_.scanner_config),
        strategy_version=engine.config.version, risk_version=risk.version,
        execution_version=execution.version, risk_config=risk, execution_config=execution)
    block = "\n".join((
        portfolio.block,
        f"baseline_version={BASELINE_VERSION}",
        f"result_schema_version={RESULT_SCHEMA_VERSION}",
        f"strategy_config_fingerprint={snapshot['strategy_config_fingerprint']}",
        f"execution_risk_config_fingerprint={snapshot['execution_risk_config_fingerprint']}",
        f"config_fingerprint={snapshot['config_fingerprint']}",
        f"variant={ACTUAL_VARIANT.variant}",
        f"starting_cash_source={STARTING_CASH_SOURCE}",
        f"premarket_history_sessions={PREMARKET_HISTORY_SESSIONS}",
        f"entry_replay_version={REPLAY_VERSION}",
        f"position_replay_version={POSITION_REPLAY_VERSION}",
        f"tick_epsilon={TICK_EPSILON.total_seconds()}",
        *(f"minute_collector={symbol}:{identity.collector_version}:"
          f"{identity.start_date}..{identity.end_date}"
          for symbol, identity in sorted(minute.items())),
        *(f"daily_collector={item.symbol}:{item.collector_version}:{item.start}..{item.end}"
          for item in plan_.daily),
        f"scanner_completed_through={plan_.daily_scan_end}",
        # Only a snapshot binding adds lines: the legacy manifest keeps its old identity.
        *plan_.inputs.data_binding().identity_lines(),
        # Only an experiment adds lines: a default configuration keeps its old identity.
        *identity_lines(overrides),
        *plan_.authority_override_lines(),
    ))
    digest = checksum("current_strategy_baseline", block)
    experiment = (overrides or plan_.overnight_override
                  or plan_.premarket_volume_basis != PREMARKET_VOLUME_BASIS_V1)
    prefix = EXPERIMENT_RUN_ID_PREFIX if experiment else RUN_ID_PREFIX
    return BaselineIdentity(f"{prefix}-{digest[:20]}", digest, block, portfolio, snapshot,
                            overrides)


# --- the run ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineRun:
    identity: BaselineIdentity
    result: PortfolioReplayResult
    document: dict[str, object]
    artifacts: Artifacts
    elapsed_seconds: float


#: Stages ``execute`` names to an observer, in order. The replay itself is one pass: the
#: scanner, the entry/position replay and the portfolio run interleaved on one clock, so
#: they are one stage and no finer progress exists to report.
STAGE_LOAD, STAGE_REPLAY, STAGE_AGGREGATE = "LOAD_TAPE", "REPLAY", "AGGREGATE"


def execute(plan_: BaselinePlan, *, engine: StrategyV0Engine | None = None,
            risk: RiskConfig | None = None, execution: ExecutionConfig | None = None,
            timer=time.monotonic,  # type: ignore[no-untyped-def]
            on_stage: Callable[[str], None] | None = None) -> BaselineRun:
    """Load and verify the tape, replay the whole range once, and build the result."""
    stage = on_stage or (lambda _: None)
    assert_covered(plan_.coverage)
    engine, risk, execution = engine or StrategyV0Engine(), risk or RiskConfig(), \
        execution or ExecutionConfig()
    calendar = plan_.inputs.calendar
    binding = plan_.inputs.data_binding()
    stage(STAGE_LOAD)
    datasets = {symbol: binding.load_minute(symbol) for symbol in plan_.required}
    coverage = tuple(measured_sessions(report, datasets[report.symbol].trading_dates, calendar)
                     for report in plan_.coverage)
    assert_covered(coverage)
    identity = baseline_identity(plan_, {symbol: item.identity for symbol, item in datasets.items()},
                                 engine=engine, risk=risk, execution=execution)
    if plan_.inputs.completed_through < plan_.daily_scan_end:
        raise BaselineCoverageIncomplete(
            f"daily tape ends {plan_.daily_scan_end}, after the last completed session "
            f"{plan_.inputs.completed_through}")
    store = binding.daily_store(calendar)
    # The scanner's completion bound is the verified daily tape's own end, not the wall
    # clock: a run asked for tomorrow over the same tape is the same run.
    source = ResearchScannerCandidateSource(
        store, universe=plan_.scanned, metadata=plan_.inputs.metadata, calendar=calendar,
        config=plan_.scanner_config, top8_only=True, completed_through=plan_.daily_scan_end)
    replay = MultiSymbolPortfolioReplay(
        datasets, config=plan_.portfolio_config(), modes=plan_.modes, candidates=source,
        calendar=calendar, engine=engine, risk_config=risk, execution_config=execution,
        research_universe_checksum=plan_.inputs.universe.checksum,
        metadata_checksum=plan_.inputs.metadata.checksum,
        daily_checksums=tuple(sorted((item.symbol, str(item.checksum)) for item in plan_.daily)),
        scanner_config_fingerprint=scanner_config_fingerprint(plan_.scanner_config),
        premarket_daily_bars=None if plan_.premarket_volume_basis == PREMARKET_VOLUME_BASIS_V1
        else {symbol: store.bars(symbol) for symbol in plan_.required})
    if replay.identity().run_identity != identity.portfolio.run_identity:
        raise BaselineError("the replay's identity differs from the planned identity")
    stage(STAGE_REPLAY)
    started = timer()
    result = replay.run()
    elapsed = timer() - started
    stage(STAGE_AGGREGATE)
    scanner_days = tuple(_scanner_day(source, day) for day in plan_.range.scanner_sessions)
    document = build_document(plan_, identity, result, coverage, scanner_days)
    artifacts = build_artifacts(identity.run_id, document, report_markdown(document),
                                ai_export(document))
    return BaselineRun(identity, result, document, artifacts, elapsed)


def _scanner_day(source: ResearchScannerCandidateSource, day: date) -> analytics.ScannerDay:
    scan = source.scan(day)
    reasons: dict[str, int] = {}
    for item in scan.excluded:
        key = item.exclusion_reason or analytics.NONE
        reasons[key] = reasons.get(key, 0) + 1
    return analytics.ScannerDay(day, scan.input_count, scan.candidate_count, scan.excluded_count,
                                scan.top8_count, dict(sorted(reasons.items())))


def _ledger_rows(events: Sequence[LedgerEvent]) -> list[dict[str, object]]:
    names = [name for name, _ in TABLES["ledger"]]
    return rows_of([{name: getattr(event, name) for name in names} for event in events],
                   TABLES["ledger"])


def build_document(plan_: BaselinePlan, identity: BaselineIdentity,
                   result: PortfolioReplayResult,
                   coverage: Sequence[SymbolMinuteCoverage],
                   scanner_days: Sequence[analytics.ScannerDay]) -> dict[str, object]:
    """The BacktestResult V1 document of one baseline run. Validated before it is returned."""
    modes = plan_.modes
    outcomes = analytics.candidate_outcomes(result)
    trades = analytics.trade_records(result)
    curve = analytics.equity_curve(result)
    funnel = analytics.funnel(result, outcomes, scanner_days, plan_.not_scanned)
    accounting = analytics.accounting_checks(result, trades, curve)
    authority = plan_.authority()
    mismatches = _authority_mismatches(result, authority)
    reconciliation_failures = [item for item in funnel["reconciliation"]  # type: ignore[union-attr]
                               if not item["ok"]]
    accounting_failures = [item for item in accounting if not item["ok"]]
    if accounting_failures:
        raise BaselineAccountingFailed(
            "accounting does not reconcile: " + "; ".join(str(item["check"])
                                                          for item in accounting_failures))
    snapshot = identity.config_snapshot
    excluded = {**plan_.not_scanned, **plan_.metadata_excluded,
                **{symbol: reason for symbol, reason in result.excluded_symbols.items()
                   if symbol not in plan_.not_scanned and symbol not in plan_.metadata_excluded}}
    excluded.pop(plan_.inputs.universe.benchmark_symbol, None)
    summary = analytics.portfolio_summary(result, trades, curve)
    document: dict[str, object] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run": {
            "run_id": identity.run_id, "run_identity": identity.run_identity,
            "baseline_version": BASELINE_VERSION,
            "portfolio_replay_version": PORTFOLIO_REPLAY_VERSION,
            "portfolio_run_identity": identity.portfolio.run_identity,
            "entry_replay_version": REPLAY_VERSION,
            "position_replay_version": POSITION_REPLAY_VERSION,
            "strategy_version": snapshot["derived"]["strategy_version"],  # type: ignore[index]
            "risk_version": identity.portfolio.risk_version,
            "execution_version": identity.portfolio.execution_version,
            "authority_version": AUTHORITY_VERSION,
            "scanner_version": identity.portfolio.scanner_version,
            "scanner_config_fingerprint": identity.portfolio.scanner_config_fingerprint,
            "config_fingerprint": snapshot["config_fingerprint"],
            "strategy_config_fingerprint": snapshot["strategy_config_fingerprint"],
            "execution_risk_config_fingerprint": snapshot["execution_risk_config_fingerprint"],
            "research_universe_checksum": plan_.inputs.universe.checksum,
            "metadata_checksum": plan_.inputs.metadata.checksum,
            "authority_label": AUTHORITY_LABEL,
            "identity_block": identity.block.split("\n"),
        },
        "config": dict(snapshot),
        "range": plan_.range.as_dict(),
        "starting_capital": starting_capital_block(plan_.inputs.starting_cash),
        "authority": authority,
        "universe": {
            "declared": list(plan_.declared),
            "declared_count": len(plan_.declared),
            "universe_version": plan_.inputs.universe.universe_version,
            "benchmark_symbol": plan_.inputs.universe.benchmark_symbol,
            "scanned": list(plan_.scanned),
            "evaluable": list(result.evaluable_universe),
            "evaluable_count": len(result.evaluable_universe),
            "excluded": dict(sorted(excluded.items())),
            "metadata_source": plan_.inputs.metadata_source,
            "top8_only": True,
        },
        "datasets": {"minute": [item.as_dict() for item in coverage],
                     "daily": [item.as_dict() for item in plan_.daily],
                     "daily_scan_start": text(plan_.daily_scan_start),
                     "daily_scan_end": text(plan_.daily_scan_end)},
        "summary": summary,
        "funnel": funnel,
        "monthly": list(analytics.monthly(curve, trades, result.starting_cash)),
        "equity_curve": rows_of(curve, TABLES["equity_curve"]),
        "trades": rows_of([trade.values for trade in trades], TABLES["trades"]),
        "rejections": rows_of([item.record() for item in outcomes
                               if item.final_stage != analytics.Stage.FILLED],
                              TABLES["rejections"]),
        "ledger": _ledger_rows(result.ledger.events),
        "accounting": {"checks": accounting, "violations": len(accounting_failures)},
        "validation": {
            "pit_violations": result.pit_violations + len(result.scanner_pit_violations),
            "entry_pit_violations": result.pit_violations,
            "scanner_pit_violations": len(result.scanner_pit_violations),
            "invariant_violations": len(result.invariant_violations),
            "accounting_violations": len(accounting_failures),
            "funnel_reconciliation_failures": len(reconciliation_failures),
            "authority_mismatches": mismatches,
        },
        "warnings": _warnings(plan_, result, summary, excluded),
    }
    if identity.overrides:
        document["run"]["parameter_set"] = parameter_set_block(  # type: ignore[index]
            {key: dict(value) for key, value in identity.overrides.items()})
    if plan_.premarket_volume_basis != PREMARKET_VOLUME_BASIS_V1:
        document["run"]["premarket_volume_basis"] = {  # type: ignore[index]
            "baseline": PREMARKET_VOLUME_BASIS_V1, "value": plan_.premarket_volume_basis,
            "denominator": "daily aggregate volume of the plan's verified daily tape",
            "note": "config.derived.premarket_volume_v1 describes the V1 denominator; this run "
                    "replaced only that denominator"}
    if plan_.overnight_override is not None:
        document["run"]["authority_override"] = {  # type: ignore[index]
            "overnight_mode": {"baseline": OvernightMode.UNKNOWN_CLOSE.value,
                               "value": plan_.overnight_override.value},
            "source": "ASSUMED", "label": "RESEARCH_EXPERIMENT_SENSITIVITY_ONLY",
            "production_config_modified": False}
    if reconciliation_failures or mismatches:
        raise BaselineAccountingFailed(
            f"funnel reconciliation failures {len(reconciliation_failures)}, "
            f"authority mismatches {mismatches}")
    validate(document)
    return document


def _authority_mismatches(result: PortfolioReplayResult, block: Mapping[str, object]) -> int:
    expected = {"candidate_authority_source": block["candidate_source"],
                "approval_authority_source": block["approval_source"],
                "overnight_authority_source": block["overnight_source"],
                "trailing_authority_source": block["trailing_source"],
                "rank_authority_source": block["rank_source"],
                "authority_label": block["authority_label"]}
    mismatches = 0
    for item in (*result.entries, *result.positions):
        mismatches += sum(1 for key, value in expected.items() if getattr(item, key) != value)
    return mismatches


def _warnings(plan_: BaselinePlan, result: PortfolioReplayResult,
              summary: Mapping[str, object], excluded: Mapping[str, str]) -> list[str]:
    warnings = [
        "RESEARCH_ONLY: the candidate field is a Historical Research Scanner TOP8 over a "
        "declared universe and every approval is ASSUMED; this is not Production history",
        f"market cap metadata comes from {plan_.inputs.metadata_source} and is applied to every "
        "scanner session unchanged",
        ("premarket volume ratio denominator is derived from Massive REGULAR minute bars; parity "
         "with Kiwoom acc_trde_qty is UNKNOWN")
        if plan_.premarket_volume_basis == PREMARKET_VOLUME_BASIS_V1 else
        ("premarket volume ratio denominator is the Massive daily aggregate volume "
         f"({plan_.premarket_volume_basis}), the basis of Kiwoom acc_trde_qty; the premarket "
         "numerator still comes from the Massive minute tape and its parity with Kiwoom is UNKNOWN"),
        ("overnight UNKNOWN_CLOSE: the closing review reads UNKNOWN suitability, so Day 2 and "
         "overnight carry are unreachable in this run") if plan_.overnight_override is None else
        (f"overnight {plan_.overnight_override.value}: a RESEARCH experiment declaration, not a "
         "record; the closing review reads a declared suitability for every symbol, so Day 2 "
         "is reachable on an assumption; sensitivity only, not Production history"),
        "trailing UNKNOWN_DEFAULT: TIGHT/WIDE trailing profiles are unreachable",
        "Risk sizing produces fractional share quantities; no lot rounding is applied",
        f"starting cash {plan_.inputs.starting_cash} USD is {STARTING_CASH_SOURCE}",
        *(f"declared symbol not evaluated: {symbol}={reason}"
          for symbol, reason in sorted(excluded.items())),
        *(f"range: {limit}" for limit in plan_.range.limits),
        *(f"replay note: {note}" for note in result.notes),
    ]
    if summary["open_positions_at_end"]:
        warnings.append(f"{summary['open_positions_at_end']} position(s) open at the end are "
                        "valued at their last completed mark")
    unrecorded = summary["drawdown"]["unrecorded_sessions"]  # type: ignore[index]
    if unrecorded:
        warnings.append(f"{unrecorded} session close(s) had an unmarked book and no equity")
    return warnings

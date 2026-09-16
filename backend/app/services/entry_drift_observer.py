"""Non-trading StrategyV0 entry-price drift observer on the Production Entry contract.

One observation replays one approved candidate's ENTRY session exactly as the Entry
runtime would have lived it: the authority is the analysis stamped with the session
before, each live tick sees only the minute bars a provider exposes at that moment,
and the execution price is the open of the one bar the settlement rule names. The
observer reads authority and market data, evaluates the pure strategy and Risk
sizing, and writes nothing but ``entry_drift_observations``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import logging
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import MarketDataError
from app.execution.config import ExecutionConfig
from app.execution.costs import buy_effective_price, execution_price
from app.execution.domain import OrderSide
from app.integrations.kiwoom.mapping import exchange_code
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.models.analytics import EntryDriftObservation
from app.risk.config import RiskConfig
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.risk.engine import RiskEngine
from app.services.entry_management_runtime import (
    PremarketBuildResult, PremarketInvalidField, analysis_session_date,
    build_premarket_context, intended_entry_bar_at, load_approved_candidates,
)
from app.services.exchange_authority import bind_exchange
from app.services.premarket_volume_history import (
    BASELINE_METHOD, COLLECTOR_VERSION, V2_THRESHOLDS, PremarketVolumeBaseline, V2Status,
    V2VolumeAnalytics, history_statistics, load_baseline, v2_analytics,
)
from app.services.strategy import StrategyLifecycleService
from app.strategy.config import STRATEGY_VERSION
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import GateResult, StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import StrategyState

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
# (symbol, Kiwoom exchange code, entry session) -> the stored V2 baseline, or None when
# this database cannot hold V2 analytics. It reads durable rows only, never a provider.
BaselineReader = Callable[[str, str, date], PremarketVolumeBaseline | None]

OBSERVER_VERSION = "entry_drift_observer_v2"
# The schema revision this observer writes; a newer head needs review before writing.
# Reviewed for 0017, the single step from the deployed 0016: it adds the independent
# paper_entry_evaluations table and changes no column this observer reads or writes.
OBSERVER_SCHEMA_REVISION = "20260916_0017"
TOLERANCES = tuple(Decimal(value) for value in (
    "0.00", "0.10", "0.25", "0.50", "0.75", "1.00", "1.50", "2.00"))
MINUTE = timedelta(minutes=1)
# The Entry runtime wakes on each minute boundary and evaluates a moment later. Bars
# only become available on boundaries, so every latency inside (0, 1 minute) sees the
# same bars and reaches the same decision; this is one such latency.
TICK_EPSILON = timedelta(milliseconds=500)
# Projections size one isolated entry on a fresh account of this equity.
PROJECTION_EQUITY = Decimal("100000")


class ObservationStatus(StrEnum):
    VALID = "VALID"
    NO_SIGNAL = "NO_SIGNAL"
    INSUFFICIENT_MINUTE_DATA = "INSUFFICIENT_MINUTE_DATA"
    INVALID_PREMARKET = "INVALID_PREMARKET"
    NO_EXACT_PREVIOUS_CLOSE = "NO_EXACT_PREVIOUS_CLOSE"
    MISSING_EXECUTION_BAR = "MISSING_EXECUTION_BAR"
    UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    SESSION_NOT_COMPLETE = "SESSION_NOT_COMPLETE"
    OTHER = "OTHER"


# A final outcome is only replaced by the same outcome recomputed; every other status
# is a data-quality state that a later, better-supplied run may upgrade.
FINAL_STATUSES = frozenset({ObservationStatus.VALID.value, ObservationStatus.NO_SIGNAL.value})


class WriteOutcome(StrEnum):
    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    # The observation was rewritten but its stored AVAILABLE V2 answer was kept.
    UPDATED_V2_KEPT = "UPDATED_V2_KEPT"
    KEPT_FINAL = "KEPT_FINAL"
    VERSION_CONFLICT = "VERSION_CONFLICT"


class ObserverRefused(RuntimeError):
    """A run that would persist observations of a session that has not completed."""


@dataclass(frozen=True)
class ObserverCandidate:
    trading_date: date  # the ENTRY session replayed, not the scanner run's date
    scanner_run_id: int
    analysis_id: int
    candidate_id: int
    symbol: str
    exchange: str


@dataclass(frozen=True)
class ObservationResult:
    candidate: ObserverCandidate
    status: ObservationStatus
    quality_reason: str | None = None
    gate_reason: str | None = None
    gap_pct: Decimal | None = None
    volume_ratio: Decimal | None = None
    opening_range_high: Decimal | None = None
    opening_range_low: Decimal | None = None
    signal_at: datetime | None = None
    signal_bar_timestamp: datetime | None = None
    signal_price: Decimal | None = None
    initial_stop: Decimal | None = None
    execution_at: datetime | None = None
    execution_open: Decimal | None = None
    drift_pct: Decimal | None = None
    stop_distance_pct: Decimal | None = None
    projections: tuple[dict[str, Any], ...] = ()
    # Each evaluated tick and the strategy reason it produced, for replay audits.
    trace: tuple[tuple[datetime, str], ...] = ()
    # The V1 numerator: PREMARKET volume the gate saw at the open tick (04:00-09:29).
    premarket_volume: Decimal | None = None
    # Counterfactual V2 normalisation; attached after, and never read by, the replay.
    v2: V2VolumeAnalytics | None = None


@dataclass(frozen=True)
class ObservationRun:
    entry_session_date: date
    results: tuple[ObservationResult, ...]
    writes: tuple[WriteOutcome, ...] = ()


class EntryDriftObserver:
    """Reads authority and market data, evaluates pure strategy, writes analytics only."""

    def __init__(self, session: Session, provider: MarketDataProvider, *,
                 engine: StrategyV0Engine | None = None,
                 calendar: MarketCalendar | None = None,
                 execution_config: ExecutionConfig | None = None,
                 risk_config: RiskConfig | None = None,
                 clock: Clock | None = None,
                 v2_baseline: BaselineReader | None = None) -> None:
        self.session = session
        self.provider = provider
        self.engine = engine or StrategyV0Engine()
        self.calendar = calendar or MarketCalendar()
        self.execution = execution_config or ExecutionConfig()
        self.risk = risk_config or RiskConfig()
        self.risk_engine = RiskEngine(self.risk)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.v2_baseline = v2_baseline or (lambda symbol, exchange, day: load_baseline(
            self.session, symbol, exchange, day, calendar=self.calendar))

    @property
    def versions(self) -> tuple[str, str, str]:
        return self.engine.config.version, self.risk.version, OBSERVER_VERSION

    def candidates(self, entry_session_date: date) -> tuple[ObserverCandidate, ...]:
        """The candidates the Entry runtime would trade on ``entry_session_date``."""
        if not self.calendar.is_trading_day(entry_session_date):
            return ()
        approved = load_approved_candidates(
            self.session, analysis_session_date(self.calendar, entry_session_date))
        return tuple(ObserverCandidate(
            entry_session_date, item.scanner_run_id, item.analysis_id, item.candidate_id,
            item.symbol, item.exchange,
        ) for item in approved)

    def session_complete(self, entry_session_date: date) -> bool:
        window = self.calendar.session(entry_session_date)
        return window is not None and self.clock() >= window.market_close

    def observe_date(self, entry_session_date: date, *, persist: bool = False,
                     replace_versions: bool = False) -> ObservationRun:
        if persist and not self.session_complete(entry_session_date):
            raise ObserverRefused(f"entry session {entry_session_date} has not completed")
        results = tuple(self.observe(item) for item in self.candidates(entry_session_date))
        writes: tuple[WriteOutcome, ...] = ()
        if persist:
            writes = tuple(self._upsert(result, replace_versions=replace_versions)
                           for result in results)
            self.session.commit()
        return ObservationRun(entry_session_date, results, writes)

    def observe(self, candidate: ObserverCandidate) -> ObservationResult:
        """The V1 Production replay, then V2 analytics beside it.

        V2 is computed from the finished V1 result and durable history rows only, so
        no V2 value or failure can reach a decision, a status, or a provider request.
        """
        result = self._observe_v1(candidate)
        if result.status is ObservationStatus.UNSUPPORTED_EXCHANGE:
            return replace(result, v2=V2VolumeAnalytics(V2Status.UNSUPPORTED_EXCHANGE,
                                                        None, None, None))
        if result.status is ObservationStatus.SESSION_NOT_COMPLETE or (
                result.quality_reason == "NOT_A_TRADING_SESSION"):
            return result
        return replace(result, v2=self._v2(result))

    def _v2(self, result: ObservationResult) -> V2VolumeAnalytics | None:
        item = result.candidate
        try:
            baseline = self.v2_baseline(item.symbol, item.exchange, item.trading_date)
            return None if baseline is None else v2_analytics(result.premarket_volume, baseline)
        except Exception:
            logger.exception("ENTRY DRIFT V2 ANALYTICS FAILED: %s", item.symbol)
            return V2VolumeAnalytics(V2Status.ANALYTICS_ERROR, result.premarket_volume, None, None)

    def _observe_v1(self, candidate: ObserverCandidate) -> ObservationResult:
        window = self.calendar.session(candidate.trading_date)
        if window is None:
            return ObservationResult(candidate, ObservationStatus.OTHER, "NOT_A_TRADING_SESSION")
        if self.clock() < window.market_close:
            # A live session's bars are not final; nothing is fetched or concluded yet.
            return ObservationResult(candidate, ObservationStatus.SESSION_NOT_COMPLETE,
                                     "ENTRY_SESSION_OPEN")
        try:
            resolved = exchange_code(candidate.exchange)
            bind_exchange(self.provider, candidate.symbol, resolved)
        except MarketDataError as exc:
            return ObservationResult(candidate, ObservationStatus.UNSUPPORTED_EXCHANGE, exc.code)
        candidate = replace(candidate, exchange=resolved)
        try:
            return self._replay(candidate, window)
        except MarketDataError as exc:
            # One symbol's provider failure, truncated history included, is that
            # candidate's typed result; it never ends the other candidates' replay.
            return ObservationResult(candidate, ObservationStatus.PROVIDER_FAILURE, exc.code)
        except Exception as exc:
            logger.exception("ENTRY DRIFT OBSERVATION FAILED: %s", candidate.symbol)
            return ObservationResult(candidate, ObservationStatus.OTHER, type(exc).__name__)

    def _replay(self, candidate: ObserverCandidate,
                window: TradingSessionWindow) -> ObservationResult:
        symbol, day = candidate.symbol, candidate.trading_date
        bars = tuple(sorted(self.provider.get_minute_bars(
            [symbol], datetime.combine(day, time(4), self.calendar.timezone),
            window.market_close, None), key=lambda bar: bar.timestamp))
        state: StrategyState | None = None
        gate: GateResult | None = None
        built: PremarketBuildResult | None = None
        trace: list[tuple[datetime, str]] = []
        as_of = window.market_open + TICK_EPSILON
        while True:
            # A live tick receives bars up to its own time and the Entry runtime keeps
            # only those already available; the provider's own stamps are never moved.
            visible = tuple(bar for bar in bars
                            if bar.timestamp <= as_of and bar.available_at <= as_of)
            if state is None:
                state = StrategyLifecycleService.apply_human_gate(
                    StrategyState(symbol, day, scanner_candidate_id=candidate.candidate_id),
                    approved=True, shadow_mode=False)
                built = build_premarket_context(self.calendar, symbol, day, visible,
                                                self.provider, as_of)
                gate = self.engine.premarket_gate(built.context, human_approved=True,
                                                  shadow_mode=False)
                state = StrategyLifecycleService.apply_premarket_gate(state, gate)
                if not gate.passed:
                    return self._premarket_rejection(candidate, built, gate)
            evaluated = self.engine.evaluate_entry(
                state=state, bars=visible, market_open=window.market_open, as_of=as_of)
            state = evaluated.state
            trace.append((as_of, evaluated.decision.reason_code))
            if evaluated.decision.decision in {DecisionType.ENTER, DecisionType.NO_TRADE}:
                break
            as_of += MINUTE
            if as_of > window.market_close:
                # The gate passed at the open, so its inputs are known; they are carried
                # for analytics only, after the replay has already ended.
                assert gate is not None and built is not None
                return ObservationResult(candidate, ObservationStatus.INSUFFICIENT_MINUTE_DATA,
                                         "NO_ENTRY_DECISION", gate_reason=gate.reason.value,
                                         gap_pct=gate.gap_pct, volume_ratio=gate.volume_ratio,
                                         premarket_volume=built.diagnostic.premarket_volume,
                                         trace=tuple(trace))
        assert gate is not None and built is not None
        context: dict[str, Any] = {
            "gate_reason": gate.reason.value, "gap_pct": gate.gap_pct,
            "volume_ratio": gate.volume_ratio,
            "premarket_volume": built.diagnostic.premarket_volume,
            "opening_range_high": evaluated.opening_range_high,
            "opening_range_low": evaluated.opening_range_low, "trace": tuple(trace),
        }
        regular = tuple(bar for bar in bars if bar.session is MarketSession.REGULAR
                        and window.market_open <= bar.timestamp < window.market_close)
        if _first_missing_minute(regular, window.market_open, as_of) is not None:
            return ObservationResult(candidate, ObservationStatus.INSUFFICIENT_MINUTE_DATA,
                                     "INCOMPLETE_SIGNAL_WINDOW", **context)
        if evaluated.decision.decision is DecisionType.NO_TRADE:
            return ObservationResult(candidate, ObservationStatus.NO_SIGNAL,
                                     evaluated.decision.reason_code, **context)

        signal_at, price, stop = state.last_market_as_of, state.entry_price, state.initial_stop
        assert signal_at is not None and price is not None and stop is not None
        intended_at = intended_entry_bar_at(signal_at, self.execution.fill_delay_bars)
        signal: dict[str, Any] = {
            "signal_at": signal_at, "signal_price": price, "initial_stop": stop,
            "signal_bar_timestamp": max(bar.timestamp for bar in visible
                                        if bar.session is MarketSession.REGULAR),
            "execution_at": intended_at, "stop_distance_pct": (price - stop) / price * Decimal("100"),
        }
        if not window.market_open <= intended_at < window.market_close:
            return ObservationResult(candidate, ObservationStatus.OTHER,
                                     StrategyReason.ENTRY_SESSION_ENDED.value, **context, **signal)
        intended = next((bar for bar in regular if bar.timestamp == intended_at), None)
        if intended is None or intended.open <= 0:
            return ObservationResult(
                candidate, ObservationStatus.MISSING_EXECUTION_BAR,
                "NO_INTENDED_BAR" if intended is None else "INVALID_INTENDED_OPEN",
                **context, **signal)
        execution_open = Decimal(str(intended.open))
        return ObservationResult(
            candidate, ObservationStatus.VALID, **context, **signal,
            execution_open=execution_open,
            drift_pct=(execution_open / price - Decimal("1")) * Decimal("100"),
            projections=self.tolerance_projections(symbol, price, stop, execution_open, signal_at),
        )

    @staticmethod
    def _premarket_rejection(candidate: ObserverCandidate, built: PremarketBuildResult,
                             gate: GateResult) -> ObservationResult:
        invalid = built.diagnostic.invalid_field
        status = (ObservationStatus.NO_EXACT_PREVIOUS_CLOSE
                  if invalid is PremarketInvalidField.NO_EXACT_PREVIOUS_CLOSE
                  else ObservationStatus.INVALID_PREMARKET)
        return ObservationResult(candidate, status, (invalid or gate.reason).value,
                                 gate_reason=gate.reason.value, gap_pct=gate.gap_pct,
                                 volume_ratio=gate.volume_ratio,
                                 premarket_volume=built.diagnostic.premarket_volume)

    def tolerance_projections(self, symbol: str, signal_price: Decimal, stop: Decimal,
                              intended_open: Decimal, decided_at: datetime
                              ) -> tuple[dict[str, Any], ...]:
        """What each price tolerance would have done to this one entry.

        A tolerance is decided before the order exists: Risk sizes the entry at the
        tolerance's ceiling reference, so the quantity never depends on the open that
        follows. The open only decides whether the broker's own limit test, against
        the ceiling Risk set, lets that order fill.
        """
        account = AccountSnapshot(PROJECTION_EQUITY, PROJECTION_EQUITY, Currency.USD, decided_at)
        portfolio = PortfolioSnapshot((), Decimal("0"), Decimal("0"), decided_at)
        daily = DailyTradingState(decided_at.date(), session_equity=PROJECTION_EQUITY)
        decision = StrategyDecision(symbol, DecisionType.ENTER,
                                    StrategyReason.ABOVE_VWAP_AND_OR_BREAK.value, decided_at,
                                    strategy_version=self.engine.config.version)
        output: list[dict[str, Any]] = []
        baseline: Decimal | None = None
        for tolerance in TOLERANCES:
            reference = signal_price * (Decimal("1") + tolerance / Decimal("100"))
            sized = self.risk_engine.evaluate_base_entry(
                decision=decision, eligibility=TradingEligibility(True), account=account,
                portfolio=portfolio, daily_state=daily, entry_price=reference, stop_price=stop,
                instrument_currency=Currency.USD, created_at=decided_at,
                execution_config=self.execution)
            metrics = sized.metrics if sized.approved else None
            if metrics is None:
                output.append({"tolerance_pct": str(tolerance), "reference_price": str(reference),
                               "would_execute": False, "quantity_per_100k_usd": None,
                               "quantity_reduction_pct": None,
                               "risk_rejection": None if sized.rejection_reason is None
                               else sized.rejection_reason.value})
                continue
            quantity = metrics.final_quantity
            if tolerance == 0:
                baseline = quantity
            fills = (execution_price(intended_open, OrderSide.BUY, self.execution)
                     <= metrics.max_execution_price)
            realised = (quantity * (buy_effective_price(intended_open, self.execution) - stop)
                        if fills else None)
            output.append({
                "tolerance_pct": str(tolerance), "reference_price": str(reference),
                "max_execution_price": str(metrics.max_execution_price),
                "would_execute": fills, "quantity_per_100k_usd": str(quantity),
                "quantity_reduction_pct": None if not baseline
                else str((Decimal("1") - quantity / baseline) * Decimal("100")),
                "planned_initial_risk": str(metrics.planned_risk),
                "projected_notional": str(metrics.final_notional_account_ccy),
                "realised_initial_risk": None if realised is None else str(realised),
            })
        return tuple(output)

    def _upsert(self, result: ObservationResult, *, replace_versions: bool) -> WriteOutcome:
        row = self.session.scalar(select(EntryDriftObservation).where(
            EntryDriftObservation.scanner_candidate_id == result.candidate.candidate_id))
        now = self.clock()
        v2 = _v2_columns(result.v2)
        if row is None:
            row = EntryDriftObservation(scanner_candidate_id=result.candidate.candidate_id,
                                        created_at=now)
            self.session.add(row)
            outcome = WriteOutcome.INSERTED
        else:
            stored = (row.strategy_version, row.risk_version, row.observer_version)
            outcome = WriteOutcome.UPDATED
            if stored != self.versions:
                # Another version's statistics stay reproducible unless replaced on purpose.
                if not replace_versions:
                    return WriteOutcome.VERSION_CONFLICT
            elif row.status in FINAL_STATUSES and row.status != result.status.value:
                return WriteOutcome.KEPT_FINAL
            elif _v2_final(row) and (result.v2 is None
                                     or result.v2.status is not V2Status.AVAILABLE):
                # Same contract: a later error or thinner history never erases an
                # AVAILABLE V2 answer. Only an AVAILABLE recomputation replaces it.
                v2 = {}
                outcome = WriteOutcome.UPDATED_V2_KEPT
        strategy_version, risk_version, observer_version = self.versions
        values = {
            "trading_date": result.candidate.trading_date, "scanner_run_id": result.candidate.scanner_run_id,
            "gpt_analysis_id": result.candidate.analysis_id, "symbol": result.candidate.symbol,
            "exchange": result.candidate.exchange, "strategy_version": strategy_version,
            "risk_version": risk_version, "observer_version": observer_version,
            "status": result.status.value, "quality_reason": result.quality_reason,
            "signal_at": result.signal_at, "signal_bar_timestamp": result.signal_bar_timestamp,
            "signal_price": result.signal_price, "initial_stop": result.initial_stop,
            "intended_execution_bar_timestamp": result.execution_at,
            "intended_execution_raw_open": result.execution_open, "drift_pct": result.drift_pct,
            "stop_distance_pct": result.stop_distance_pct,
            "projections_json": list(result.projections), "updated_at": now,
            "v1_volume_ratio": result.volume_ratio, **v2,
        }
        for name, value in values.items():
            setattr(row, name, value)
        return outcome


def _v2_final(row: EntryDriftObservation) -> bool:
    """A stored AVAILABLE V2 answer of the current collector and baseline contract."""
    return (row.v2_status == V2Status.AVAILABLE.value
            and row.v2_collector_version == COLLECTOR_VERSION
            and row.v2_baseline_method == BASELINE_METHOD)


def _v2_columns(v2: V2VolumeAnalytics | None) -> dict[str, Any]:
    baseline = None if v2 is None else v2.baseline
    return {
        "v2_status": None if v2 is None else v2.status.value,
        "v2_median_ratio": None if v2 is None else v2.ratio,
        "v2_today_premarket_volume": None if v2 is None else v2.today_premarket_volume,
        "v2_baseline_volume": None if baseline is None else baseline.median_volume,
        "v2_baseline_method": None if baseline is None else baseline.method,
        "v2_baseline_sessions": None if baseline is None else baseline.valid_sessions,
        "v2_baseline_start_date": None if baseline is None else baseline.start_date,
        "v2_baseline_end_date": None if baseline is None else baseline.end_date,
        "v2_collector_version": None if baseline is None else baseline.collector_version,
        "v2_projections_json": None if v2 is None else v2.projections(),
    }


def _first_missing_minute(regular: Sequence[MinuteBar], market_open: datetime,
                          as_of: datetime) -> datetime | None:
    """The first regular minute the decision tick should have seen but the data lacks.

    Every bar from the open to the last one completed by the deciding tick must be
    present; otherwise a breakout the provider dropped would read as no signal.
    """
    present = {bar.timestamp for bar in regular}
    minute, completed = market_open, as_of.replace(second=0, microsecond=0)
    while minute < completed:
        if minute not in present:
            return minute
        minute += MINUTE
    return None


def current_authority_rows(session: Session, rows: Iterable[EntryDriftObservation],
                           calendar: MarketCalendar | None = None
                           ) -> list[EntryDriftObservation]:
    """Only observations of the authority chain that stands for their session now.

    A superseded scanner run, a de-activated analysis, or a candidate no longer
    approved leaves its row in place but out of every statistic.
    """
    calendar = calendar or MarketCalendar()
    current: dict[date, set[tuple[int, int, int]]] = {}
    kept: list[EntryDriftObservation] = []
    for row in rows:
        if row.trading_date not in current:
            current[row.trading_date] = {
                (item.scanner_run_id, item.analysis_id, item.candidate_id)
                for item in load_approved_candidates(
                    session, analysis_session_date(calendar, row.trading_date))
            }
        if (row.scanner_run_id, row.gpt_analysis_id, row.scanner_candidate_id) in current[row.trading_date]:
            kept.append(row)
    return kept


def entry_drift_report(session: Session, *, trading_date: date | None = None,
                       calendar: MarketCalendar | None = None,
                       strategy_version: str = STRATEGY_VERSION,
                       risk_version: str | None = None,
                       observer_version: str = OBSERVER_VERSION) -> dict[str, Any]:
    query = select(EntryDriftObservation).order_by(EntryDriftObservation.trading_date,
                                                   EntryDriftObservation.symbol)
    if trading_date is not None:
        query = query.where(EntryDriftObservation.trading_date == trading_date)
    rows = list(session.scalars(query))
    current = current_authority_rows(session, rows, calendar)
    report = observation_statistics(current, strategy_version=strategy_version,
                                    risk_version=risk_version, observer_version=observer_version)
    report["excluded_superseded_authority"] = len(rows) - len(current)
    report["premarket_volume_v2"]["history"] = history_statistics(session)
    return report


def percentile(values: list[Decimal], percent: int) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = Decimal(percent) / Decimal("100") * Decimal(len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def observation_statistics(rows: list[EntryDriftObservation], *,
                           strategy_version: str = STRATEGY_VERSION,
                           risk_version: str | None = None,
                           observer_version: str = OBSERVER_VERSION) -> dict[str, Any]:
    """Drift and tolerance statistics for one strategy/risk/observer version only."""
    risk_version = risk_version or RiskConfig().version
    versions = (strategy_version, risk_version, observer_version)
    selected = [row for row in rows
                if (row.strategy_version, row.risk_version, row.observer_version) == versions]
    valid = [row for row in selected
             if row.status == ObservationStatus.VALID.value and row.drift_pct is not None]
    drifts = [Decimal(row.drift_pct) for row in valid if row.drift_pct is not None]
    tolerances = []
    for tolerance in TOLERANCES:
        items = [projection for row in valid for projection in row.projections_json
                 if projection.get("tolerance_pct") == str(tolerance)]
        executed = sum(1 for projection in items if projection.get("would_execute"))
        reductions = [Decimal(projection["quantity_reduction_pct"]) for projection in items
                      if projection.get("quantity_reduction_pct") is not None]
        tolerances.append({
            "tolerance_pct": str(tolerance), "accepted": executed,
            "rejected": len(items) - executed,
            "acceptance_rate": executed / len(items) if items else None,
            "quantity_reduction_pct": ({"mean": mean(reductions), "median": median(reductions),
                                        "max": max(reductions)} if reductions else None),
        })
    sessions = Counter((row.trading_date, row.symbol) for row in valid)
    return {
        "strategy_version": strategy_version, "risk_version": risk_version,
        "observer_version": observer_version,
        "excluded_other_versions": len(rows) - len(selected),
        "status_counts": dict(Counter(row.status for row in selected)),
        "verdict": "ENTRY PRICE DRIFT DATA INSUFFICIENT" if not valid else None,
        "count": len(valid), "trading_sessions": len({row.trading_date for row in valid}),
        "symbols": len({row.symbol for row in valid}),
        "duplicate_symbol_sessions": sum(1 for count in sessions.values() if count > 1),
        "drift": ({"mean": mean(drifts), "median": median(drifts),
                   **{f"p{p}": percentile(drifts, p) for p in (75, 90, 95, 99)},
                   "min": min(drifts), "max": max(drifts)} if drifts else None),
        "tolerances": tolerances,
        "premarket_volume_v2": v2_observation_statistics(selected),
    }


def v2_observation_statistics(rows: Sequence[EntryDriftObservation]) -> dict[str, Any]:
    """Counterfactual V2 forward-sample statistics; every observation status counts."""
    recorded = [row for row in rows if row.v2_status is not None]
    ratios = [Decimal(row.v2_median_ratio) for row in recorded
              if row.v2_status == V2Status.AVAILABLE.value and row.v2_median_ratio is not None]
    counts = Counter(row.v2_status for row in recorded)
    return {
        "candidate_count": len(rows), "v2_recorded": len(recorded), "v2_available": len(ratios),
        "insufficient_history": counts.get(V2Status.INSUFFICIENT_PREMARKET_HISTORY.value, 0),
        "observation_provider_failures": sum(
            1 for row in rows if row.status == ObservationStatus.PROVIDER_FAILURE.value),
        "status_counts": dict(counts),
        "ratio": ({"median": median(ratios),
                   **{f"p{p}": percentile(ratios, p) for p in (75, 90, 95)},
                   "min": min(ratios), "max": max(ratios)} if ratios else None),
        "threshold_projections": [{
            "threshold": str(threshold),
            "would_pass": sum(1 for ratio in ratios if ratio >= threshold),
            "pass_rate": (sum(1 for ratio in ratios if ratio >= threshold) / len(ratios)
                          if ratios else None),
        } for threshold in V2_THRESHOLDS],
    }

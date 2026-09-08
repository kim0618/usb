"""Regular-session owner for approved durable paper entry candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.repositories.risk import DailyRiskRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, PortfolioSnapshot, PositionSnapshot
from app.risk.engine import RiskEngine
from app.services.position_lifecycle import strategy_state_sink
from app.services.simulation_runtime import SimulationRuntimeContext
from app.services.strategy import StrategyLifecycleService
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import PremarketContext, StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import (
    TERMINAL_PHASES, OvernightSuitability, StrategyPhase, StrategyState, TrailingProfile,
)
from app.strategy.runner import StrategyLifecycleRunner

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
ProviderFactory = Callable[[], MarketDataProvider]


class EntryAction(StrEnum):
    HOLD = "HOLD"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class ApprovedCandidate:
    analysis_id: int
    scanner_run_id: int
    candidate_id: int
    symbol: str
    trailing_profile: TrailingProfile
    overnight_suitability: OvernightSuitability


@dataclass(frozen=True)
class EntryOutcome:
    symbol: str
    action: EntryAction
    reason: str
    state: StrategyState | None = None
    order_id: str | None = None


class EntryLifecycleService:
    """Compose existing Strategy, Risk, and durable execution authorities."""

    def __init__(self, runtime: SimulationRuntimeContext, *,
                 engine: StrategyV0Engine | None = None,
                 risk_engine: RiskEngine | None = None,
                 calendar: MarketCalendar | None = None) -> None:
        if not runtime.durable:
            raise ValueError("entry management requires a durable runtime")
        self.runtime = runtime
        self.engine = engine or StrategyV0Engine()
        self.risk_engine = risk_engine or RiskEngine()
        self.calendar = calendar or MarketCalendar()
        self.runner = StrategyLifecycleRunner(
            runtime.broker, runtime=runtime, risk_engine=self.risk_engine,
            calendar=self.calendar,
        )

    def approved_candidates(self, trading_date: date) -> tuple[ApprovedCandidate, ...]:
        """Use the newest completed run as one chain; never combine latest rows."""
        with self.runtime.session_factory() as session:
            run = session.scalar(
                select(ScannerRun).where(
                    ScannerRun.trading_date == trading_date,
                    ScannerRun.status == "COMPLETED",
                ).order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1)
            )
            if run is None:
                return ()
            analysis = session.scalar(
                select(GPTAnalysis).where(
                    GPTAnalysis.scanner_run_id == run.id,
                    GPTAnalysis.trading_date == trading_date,
                    GPTAnalysis.status == "IMPORTED",
                ).order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc()).limit(1)
            )
            if analysis is None:
                return ()
            rows = session.execute(
                select(HumanDecisionRecord, GPTCandidateAnalysis, ScannerCandidate)
                .join(GPTCandidateAnalysis, (
                    GPTCandidateAnalysis.gpt_analysis_id == HumanDecisionRecord.gpt_analysis_id
                ) & (GPTCandidateAnalysis.scanner_candidate_id == HumanDecisionRecord.scanner_candidate_id)
                  & (GPTCandidateAnalysis.symbol == HumanDecisionRecord.symbol))
                .join(ScannerCandidate, ScannerCandidate.id == HumanDecisionRecord.scanner_candidate_id)
                .where(
                    HumanDecisionRecord.gpt_analysis_id == analysis.id,
                    HumanDecisionRecord.decision == "APPROVE",
                    ScannerCandidate.scanner_run_id == run.id,
                    ScannerCandidate.symbol == HumanDecisionRecord.symbol,
                ).order_by(GPTCandidateAnalysis.gpt_rank, HumanDecisionRecord.symbol)
            ).all()
            return tuple(ApprovedCandidate(
                analysis.id, run.id, scanner.id, decision.symbol,
                TrailingProfile(candidate.trailing_profile),
                OvernightSuitability(candidate.overnight_suitability),
            ) for decision, candidate, scanner in rows)

    def evaluate(self, candidate: ApprovedCandidate, provider: MarketDataProvider, *,
                 as_of: datetime) -> EntryOutcome:
        session_window = self.calendar.session(candidate_date := as_of.astimezone(
            self.calendar.timezone).date())
        if session_window is None or not (session_window.market_open < as_of <= session_window.market_close):
            return EntryOutcome(candidate.symbol, EntryAction.SKIPPED, "outside regular session")
        if self.runtime.broker.get_position(candidate.symbol) is not None:
            return EntryOutcome(candidate.symbol, EntryAction.SKIPPED, "position already open")

        state = self._load(candidate.symbol, candidate_date)
        if state is not None and state.scanner_candidate_id != candidate.candidate_id:
            return EntryOutcome(candidate.symbol, EntryAction.SKIPPED, "different candidate already owns date")
        if state is not None and (state.phase in TERMINAL_PHASES or
                                  state.phase in {StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED}):
            return EntryOutcome(candidate.symbol, EntryAction.SKIPPED, state.phase.value, state)

        bars = tuple(provider.get_minute_bars(
            [candidate.symbol], datetime.combine(candidate_date, time(4), self.calendar.timezone),
            as_of, None,
        ))
        visible = tuple(bar for bar in bars if bar.available_at <= as_of)
        if state is None:
            state = StrategyState(
                candidate.symbol, candidate_date, scanner_candidate_id=candidate.candidate_id,
                trailing_profile=candidate.trailing_profile,
                overnight_suitability=candidate.overnight_suitability,
            )
            state = StrategyLifecycleService.apply_human_gate(state, approved=True, shadow_mode=False)
            gate = self.engine.premarket_gate(
                self._premarket_context(candidate.symbol, candidate_date, visible, provider, as_of),
                human_approved=True, shadow_mode=False,
            )
            state = StrategyLifecycleService.apply_premarket_gate(state, gate)
            self._save(state, as_of)
            if not gate.passed:
                return EntryOutcome(candidate.symbol, EntryAction.REJECTED, gate.reason.value, state)

        if state.phase is StrategyPhase.ENTRY_SIGNALLED:
            decision = StrategyDecision(candidate.symbol, DecisionType.ENTER,
                StrategyReason.ABOVE_VWAP_AND_OR_BREAK.value,
                state.last_market_as_of or as_of, state.strategy_version)
            evaluated_state = state
        else:
            evaluated = self.engine.evaluate_entry(
                state=state, bars=visible, market_open=session_window.market_open, as_of=as_of)
            evaluated_state, decision = evaluated.state, evaluated.decision
            if decision.decision is not DecisionType.ENTER:
                self._save(evaluated_state, as_of)
                action = EntryAction.REJECTED if decision.decision is DecisionType.NO_TRADE else EntryAction.HOLD
                return EntryOutcome(candidate.symbol, action, decision.reason_code, evaluated_state)

        account, portfolio = self._risk_snapshots(provider, as_of, candidate.symbol, visible)
        with self.runtime.session_factory() as session:
            daily = DailyRiskRepository(session).load(candidate_date)
        result = self.runner.execute_entry(
            state=evaluated_state, decision=decision, account=account, portfolio=portfolio,
            market_bars=visible, instrument_currency=Currency.USD, created_at=as_of,
            actual_risk_state=daily, on_state=self._entry_sink(as_of),
        )
        if result.order is None:
            self._save(result.state, as_of)
            reason = (result.risk.rejection_reason.value if result.risk and
                      result.risk.rejection_reason else "entry not permitted")
            return EntryOutcome(candidate.symbol, EntryAction.REJECTED, reason, result.state)
        if not self.runtime.broker.get_fills(result.order.id):
            return EntryOutcome(candidate.symbol, EntryAction.REJECTED,
                                str(result.order.rejection_reason), result.state, result.order.id)
        return EntryOutcome(candidate.symbol, EntryAction.FILLED, decision.reason_code,
                            result.state, result.order.id)

    def _premarket_context(self, symbol: str, trading_date: date,
                           minute_bars: Sequence[MinuteBar], provider: MarketDataProvider,
                           as_of: datetime) -> PremarketContext:
        daily = [bar for bar in provider.get_daily_bars(
            [symbol], trading_date - timedelta(days=45), trading_date - timedelta(days=1)
        ) if bar.available_at <= as_of and bar.trading_date < trading_date]
        daily.sort(key=lambda bar: bar.trading_date)
        premarket = [bar for bar in minute_bars if bar.session is MarketSession.PREMARKET]
        if not daily or not premarket:
            return PremarketContext(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
        history = daily[-20:]
        return PremarketContext(
            Decimal(str(daily[-1].close)), Decimal(str(premarket[-1].close)),
            sum((Decimal(bar.volume) for bar in premarket), Decimal("0")),
            sum((Decimal(bar.volume) for bar in history), Decimal("0")) / Decimal(len(history)),
        )

    def _risk_snapshots(self, provider: MarketDataProvider, as_of: datetime,
                        candidate_symbol: str, candidate_bars: Sequence[MinuteBar]
                        ) -> tuple[AccountSnapshot, PortfolioSnapshot]:
        positions = self.runtime.broker.get_positions()
        marks: dict[str, Decimal] = {}
        for position in positions:
            if position.symbol == candidate_symbol:
                bars = candidate_bars
            else:
                window = self.calendar.session(as_of.astimezone(self.calendar.timezone).date())
                assert window is not None
                bars = provider.get_minute_bars([position.symbol], window.market_open, as_of,
                                                MarketSession.REGULAR)
            visible = [bar for bar in bars if bar.available_at <= as_of and
                       bar.session is MarketSession.REGULAR]
            if not visible:
                raise ValueError(f"no current mark for {position.symbol}")
            marks[position.symbol] = Decimal(str(visible[-1].close))
        broker_account = self.runtime.broker.account_snapshot(marks, as_of)
        account = AccountSnapshot(broker_account.equity, broker_account.cash,
                                  Currency(self.runtime.broker.currency), as_of)
        snapshots = tuple(PositionSnapshot(
            position.symbol, position.quantity, position.average_price, marks[position.symbol],
            Currency(self.runtime.broker.currency), base_notional_account_ccy=position.cost_basis,
        ) for position in positions)
        base = sum((position.cost_basis for position in positions), Decimal("0"))
        return account, PortfolioSnapshot(snapshots, base, Decimal("0"), as_of)

    def _entry_sink(self, updated_at: datetime):  # type: ignore[no-untyped-def]
        def persist(session: Session, state: StrategyState) -> None:
            strategy_state_sink(updated_at)(session, state)
            if state.phase is StrategyPhase.POSITION_OPEN:
                position = self.runtime.broker.get_position(state.symbol)
                trade = self.runtime.broker.get_trade(state.symbol)
                assert position is not None and trade is not None
                DailyRiskRepository(session).reserve_entry(
                    trading_date=state.trading_date, symbol=state.symbol,
                    issued_at=updated_at, planned_risk=trade.planned_initial_risk,
                    base_notional=position.cost_basis, risk_version=self.risk_engine.config.version,
                    strategy_version=state.strategy_version,
                )
        return persist

    def _load(self, symbol: str, trading_date: date) -> StrategyState | None:
        with self.runtime.session_factory() as session:
            return StrategyStateRepository(session).load(symbol, trading_date)

    def _save(self, state: StrategyState, updated_at: datetime) -> None:
        with self.runtime.session_factory() as session:
            try:
                StrategyStateRepository(session).save(state, updated_at=updated_at)
                session.commit()
            except Exception:
                session.rollback()
                raise


class EntryManagementRuntime:
    def __init__(self, runtime: SimulationRuntimeContext, provider_factory: ProviderFactory, *,
                 clock: Clock = lambda: datetime.now(timezone.utc),
                 calendar: MarketCalendar | None = None,
                 lifecycle: EntryLifecycleService | None = None) -> None:
        self.runtime = runtime
        self._provider_factory = provider_factory
        self._provider: MarketDataProvider | None = None
        self._clock = clock
        self._calendar = calendar or MarketCalendar()
        self._lifecycle = lifecycle or EntryLifecycleService(runtime, calendar=self._calendar)
        self._run_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def run_once(self, *, as_of: datetime | None = None) -> tuple[EntryOutcome, ...]:
        if self._run_lock.locked():
            return ()
        async with self._run_lock:
            now = as_of or self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("entry management clock must be timezone-aware")
            local = now.astimezone(self._calendar.timezone)
            window = self._calendar.session(local.date())
            if window is None or not (window.market_open < now <= window.market_close):
                return ()
            candidates = self._lifecycle.approved_candidates(local.date())
            if not candidates:
                return ()
            if self._provider is None:
                self._provider = self._provider_factory()
            outcomes: list[EntryOutcome] = []
            for candidate in candidates:
                try:
                    outcomes.append(self._lifecycle.evaluate(candidate, self._provider, as_of=now))
                except Exception as error:
                    logger.exception("ENTRY CANDIDATE FAILED: %s", candidate.symbol)
                    outcomes.append(EntryOutcome(candidate.symbol, EntryAction.REJECTED, str(error)))
            return tuple(outcomes)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ENTRY TICK FAILED; retrying at next minute boundary")
            now = self._clock()
            boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0, (boundary - now).total_seconds()))
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()


_owner: EntryManagementRuntime | None = None
_task: asyncio.Task[None] | None = None


def get_entry_management_runtime() -> EntryManagementRuntime | None:
    return _owner


def start_entry_management_runtime(runtime: SimulationRuntimeContext,
                                   provider_factory: ProviderFactory) -> asyncio.Task[None]:
    global _owner, _task
    if _task is not None and not _task.done():
        return _task
    _owner = EntryManagementRuntime(runtime, provider_factory)
    _task = asyncio.create_task(_owner.run(), name="entry-management")
    return _task


async def stop_entry_management_runtime() -> None:
    global _owner, _task
    owner, task = _owner, _task
    _owner = None
    _task = None
    if owner is not None:
        owner.stop()
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass

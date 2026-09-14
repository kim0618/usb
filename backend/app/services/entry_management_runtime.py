"""Regular-session owner for approved durable paper entry candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.domain import RejectionReason, SimOrder
from app.execution.costs import buy_effective_price
from app.execution.domain import IntentType, OrderSide
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.models.research import GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.strategy import PremarketDiagnosticRecord
from app.repositories.risk import DailyRiskRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, PortfolioSnapshot, PositionSnapshot
from app.risk.engine import RISK_UNIT_TOLERANCE, RiskEngine
from app.services.daily_performance import SessionEquitySource, session_opening_equity
from app.services.entry_capacity import load_entry_capacity, pending_entries
from app.services.exchange_authority import bind_exchange, bind_open_position, stored_exchange
from app.services.position_lifecycle import strategy_state_sink
from app.services.simulation_runtime import SimulationRuntimeContext
from app.services.strategy import StrategyLifecycleService
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import PremarketContext, StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import (
    TERMINAL_PHASES, OvernightSuitability, StrategyPhase, StrategyState, TrailingProfile,
)
from app.strategy.runner import StrategyLifecycleRunner
from app.research.authority import ResearchAuthorityService

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
ProviderFactory = Callable[[], MarketDataProvider]
MINUTE = timedelta(minutes=1)  # one MinuteBar


class EntryRiskInvariantError(RuntimeError):
    """A filled entry would carry more stop risk or cost than Risk approved."""


def intended_entry_bar_at(signal_at: datetime, fill_delay_bars: int) -> datetime:
    """The start of the one bar an entry signalled at ``signal_at`` executes on.

    The broker fills on the ``fill_delay_bars``-th bar starting strictly after the
    decision; bars start on minute boundaries, so that bar's identity follows from
    the signal time alone and survives a restart without being stored.
    """
    return signal_at.replace(second=0, microsecond=0) + fill_delay_bars * MINUTE


class EntryAction(StrEnum):
    HOLD = "HOLD"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class PremarketInvalidField(StrEnum):
    NO_DAILY_HISTORY = "NO_DAILY_HISTORY"
    NO_EXACT_PREVIOUS_CLOSE = "NO_EXACT_PREVIOUS_CLOSE"
    NO_PREMARKET_BARS = "NO_PREMARKET_BARS"
    INVALID_PREVIOUS_CLOSE = "INVALID_PREVIOUS_CLOSE"
    INVALID_REFERENCE_PRICE = "INVALID_REFERENCE_PRICE"
    INVALID_AVERAGE_VOLUME = "INVALID_AVERAGE_VOLUME"


@dataclass(frozen=True)
class ApprovedCandidate:
    analysis_id: int
    scanner_run_id: int
    candidate_id: int
    symbol: str
    exchange: str
    trailing_profile: TrailingProfile
    overnight_suitability: OvernightSuitability


@dataclass(frozen=True)
class EntryOutcome:
    symbol: str
    action: EntryAction
    reason: str
    state: StrategyState | None = None
    order_id: str | None = None


@dataclass(frozen=True)
class PremarketDiagnostic:
    minute_bars_count: int
    premarket_bars_count: int
    previous_close: Decimal | None
    reference_price: Decimal | None
    premarket_volume: Decimal | None
    historical_average_daily_volume: Decimal | None
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    invalid_field: PremarketInvalidField | None = None


@dataclass(frozen=True)
class PremarketBuildResult:
    context: PremarketContext
    diagnostic: PremarketDiagnostic


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

    def analysis_session_date(self, entry_session_date: date) -> date:
        """Scanner/GPT `trading_date` is the last completed XNYS session at analysis time,
        so the analysis an entry session consumes is the one stamped with its predecessor."""
        return self.calendar.previous_trading_day(entry_session_date)

    def approved_candidates_for_entry_session(self, entry_session_date: date
                                              ) -> tuple[ApprovedCandidate, ...]:
        """Exact predecessor match only; an older session's analysis is never a fallback."""
        return self.approved_candidates(self.analysis_session_date(entry_session_date))

    def approved_candidates(self, analysis_session_date: date) -> tuple[ApprovedCandidate, ...]:
        """Use the newest completed run as one chain; never combine latest rows.

        ``analysis_session_date`` is the Scanner/GPT ``trading_date``, not the entry session.
        """
        with self.runtime.session_factory() as session:
            run = session.scalar(
                select(ScannerRun).where(
                    ScannerRun.trading_date == analysis_session_date,
                    ScannerRun.status == "COMPLETED",
                ).order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1)
            )
            if run is None:
                return ()
            analysis = ResearchAuthorityService(session).resolve(run.id)
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
                self._candidate_exchange(scanner),
                TrailingProfile(candidate.trailing_profile),
                OvernightSuitability(candidate.overnight_suitability),
            ) for decision, candidate, scanner in rows)

    @staticmethod
    def _candidate_exchange(scanner: ScannerCandidate) -> str:
        """The scanner snapshot is the only durable exchange authority for a symbol.

        It is carried forward as stored, including an empty or unknown value, so the
        provider binding fails closed rather than guessing a venue here.
        """
        return stored_exchange(scanner)

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
        # A full cap stops new-symbol work before any market data or strategy state is
        # touched, so a skipped candidate stays re-evaluable if open capacity frees up.
        with self.runtime.session_factory() as session:
            capacity = load_entry_capacity(session, self.runtime.broker, candidate_date,
                                           self.risk_engine.config)
        if (capacity.blocked_reason is not None
                and candidate.symbol not in capacity.pending_entry_symbols):
            return EntryOutcome(candidate.symbol, EntryAction.SKIPPED,
                                capacity.blocked_reason.value, state)

        # Bind the candidate's own exchange before the first lookup: a provider that
        # routes per venue would otherwise read an NYSE or AMEX listing off NASDAQ and
        # fail the symbol on every tick for the rest of the session.
        self._bind_exchange(candidate, provider)
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
            built = self._premarket_context(candidate.symbol, candidate_date, visible, provider, as_of)
            gate = self.engine.premarket_gate(built.context, human_approved=True, shadow_mode=False)
            state = StrategyLifecycleService.apply_premarket_gate(state, gate)
            self._save(state, as_of, built.diagnostic)
            if not gate.passed:
                return EntryOutcome(candidate.symbol, EntryAction.REJECTED, gate.reason.value, state)

        if state.phase is not StrategyPhase.ENTRY_SIGNALLED:
            evaluated = self.engine.evaluate_entry(
                state=state, bars=visible, market_open=session_window.market_open, as_of=as_of)
            if evaluated.decision.decision is not DecisionType.ENTER:
                self._save(evaluated.state, as_of)
                action = (EntryAction.REJECTED if evaluated.decision.decision is DecisionType.NO_TRADE
                          else EntryAction.HOLD)
                return EntryOutcome(candidate.symbol, action, evaluated.decision.reason_code,
                                    evaluated.state)
            # The signal is durable before any order exists, so a restart finds it and
            # its intended bar; nothing is submitted until that bar can settle it.
            state = evaluated.state
            self._save(state, as_of)
        return self._settle(candidate, state, visible, session_window, provider, as_of)

    def _settle(self, candidate: ApprovedCandidate, state: StrategyState,
                visible: Sequence[MinuteBar], window, provider: MarketDataProvider,  # type: ignore[no-untyped-def]
                as_of: datetime) -> EntryOutcome:
        """Settle an ENTRY_SIGNALLED state on its one intended execution bar, or end it.

        A signal is an order decided at its ``last_market_as_of`` for exactly one bar,
        the bar the broker's next-bar rule names. It waits, submitting nothing, only
        while that bar is not yet available. Once it is, the order is sized and
        submitted once against that bar alone. It ends NO_TRADE if the signal came
        after the entry deadline, if its bar is outside the signal's session, if a
        later bar is already visible (the bar was not settled in time, and filling on
        it now would be a retroactive fill), or if Risk or the broker refuses it.
        """
        symbol = candidate.symbol
        signal_at = state.last_market_as_of
        if signal_at is None:
            return self._end_signal(state, StrategyReason.ENTRY_SIGNAL_STALE, as_of)
        signal_time = signal_at.astimezone(self.calendar.timezone).time().replace(tzinfo=None)
        if signal_time > self.engine.config.entry_deadline_et:
            return self._end_signal(state, StrategyReason.ENTRY_DEADLINE_EXPIRED, as_of)
        intended = intended_entry_bar_at(signal_at, self.runtime.broker.config.fill_delay_bars)
        if not (window.market_open <= intended < window.market_close):
            return self._end_signal(state, StrategyReason.ENTRY_SESSION_ENDED, as_of)
        regular = [bar for bar in visible if bar.symbol == symbol
                   and bar.session is MarketSession.REGULAR
                   and window.market_open <= bar.timestamp < window.market_close]
        # The intended bar may settle only while it is the latest completed bar: once
        # the bar after it has completed, whether or not a provider has returned it,
        # a fill on the intended bar would be a fill on the past.
        if as_of >= intended + 2 * MINUTE or any(bar.timestamp > intended for bar in regular):
            return self._end_signal(state, StrategyReason.ENTRY_SIGNAL_STALE, as_of)
        fill_bars = tuple(bar for bar in regular if bar.timestamp == intended)
        if not fill_bars:
            return EntryOutcome(symbol, EntryAction.HOLD,
                                StrategyReason.ENTRY_AWAITING_EXECUTION_BAR.value, state)

        decision = StrategyDecision(symbol, DecisionType.ENTER,
                                    StrategyReason.ABOVE_VWAP_AND_OR_BREAK.value, signal_at,
                                    state.strategy_version)
        # Sized as of the decision: the intended bar's own prices are never an input.
        account, portfolio = self._risk_snapshots(provider, signal_at, symbol, visible)
        with self.runtime.session_factory() as session:
            daily = DailyRiskRepository(session).load(state.trading_date)
            daily = replace(daily, session_equity=self._session_equity(session, state.trading_date))
        result = self.runner.execute_entry(
            state=state, decision=decision, account=account, portfolio=portfolio,
            market_bars=fill_bars, instrument_currency=Currency.USD, created_at=signal_at,
            persisted_at=as_of, actual_risk_state=daily, on_state=self._entry_sink(as_of),
            resolve_unfilled=self._unfilled_entry,
        )
        if result.order is None:
            if result.risk is None:
                return self._end_signal(state, StrategyReason.ENTRY_SESSION_ENDED, as_of)
            detail = (result.risk.rejection_reason.value if result.risk.rejection_reason
                      else StrategyReason.ENTRY_RISK_REJECTED.value)
            return self._end_signal(state, StrategyReason.ENTRY_RISK_REJECTED, as_of, detail)
        if not self.runtime.broker.get_fills(result.order.id):
            logger.warning("ENTRY SIGNAL ENDED: %s %s order=%s rejection=%s", symbol,
                           result.state.phase_reason, result.order.id, result.order.rejection_reason)
            return EntryOutcome(symbol, EntryAction.REJECTED, str(result.order.rejection_reason),
                                result.state, result.order.id)
        return EntryOutcome(symbol, EntryAction.FILLED, decision.reason_code,
                            result.state, result.order.id)

    @staticmethod
    def _unfilled_entry(state: StrategyState, order: SimOrder) -> StrategyState:
        """An intended bar settles once: an unfilled entry order ends the signal."""
        reason = (StrategyReason.ENTRY_PRICE_ABOVE_CEILING
                  if order.rejection_reason is RejectionReason.PRICE_ABOVE_LIMIT
                  else StrategyReason.ENTRY_EXECUTION_REJECTED)
        return state.transition(StrategyPhase.NO_TRADE, phase_reason=reason.value)

    def _end_signal(self, state: StrategyState, reason: StrategyReason, as_of: datetime,
                    detail: str | None = None) -> EntryOutcome:
        ended = state.transition(StrategyPhase.NO_TRADE, phase_reason=reason.value)
        self._save(ended, as_of)
        logger.warning("ENTRY SIGNAL ENDED: %s %s signal_at=%s detail=%s", state.symbol,
                       reason.value, state.last_market_as_of, detail)
        return EntryOutcome(state.symbol, EntryAction.REJECTED, detail or reason.value, ended)

    def expire_ended_signals(self, now: datetime) -> tuple[EntryOutcome, ...]:
        """End every actual ENTRY_SIGNALLED whose signal session has closed.

        An entry never fills outside its signal's session, so a signal still open
        after that close (the process was down, or the candidate left the approved
        set) can only be ended, never settled; this leaves no orphan behind.
        """
        with self.runtime.session_factory() as session:
            signalled = StrategyStateRepository(session).list_in_phase(StrategyPhase.ENTRY_SIGNALLED)
        ended: list[EntryOutcome] = []
        for state in signalled:
            window = self.calendar.session(state.trading_date)
            if window is not None and now <= window.market_close:
                continue
            ended.append(self._end_signal(state, StrategyReason.ENTRY_SESSION_ENDED, now))
        return tuple(ended)

    @staticmethod
    def _bind_exchange(candidate: ApprovedCandidate, provider: MarketDataProvider) -> None:
        """Hand the candidate's exchange authority to a provider that routes by venue."""
        bind_exchange(provider, candidate.symbol, candidate.exchange)

    def _premarket_context(self, symbol: str, trading_date: date,
                           minute_bars: Sequence[MinuteBar], provider: MarketDataProvider,
                           as_of: datetime) -> PremarketBuildResult:
        previous_close = EntryLifecycleService._previous_regular_close(
            self, symbol, trading_date, provider, as_of)
        daily = [bar for bar in provider.get_daily_bars(
            [symbol], trading_date - timedelta(days=45), trading_date - timedelta(days=1)
        ) if bar.available_at <= as_of and bar.trading_date < trading_date]
        daily.sort(key=lambda bar: bar.trading_date)
        premarket = sorted(
            (bar for bar in minute_bars if bar.session is MarketSession.PREMARKET),
            key=lambda bar: bar.timestamp,
        )
        history = daily[-20:]
        reference_price = None if not premarket else Decimal(str(premarket[-1].close))
        premarket_volume = (None if not premarket else
                            sum((Decimal(bar.volume) for bar in premarket), Decimal("0")))
        average_volume = (None if not history else
                          sum((Decimal(bar.volume) for bar in history), Decimal("0")) /
                          Decimal(len(history)))
        invalid = (PremarketInvalidField.NO_DAILY_HISTORY if not daily
                   else PremarketInvalidField.NO_EXACT_PREVIOUS_CLOSE if previous_close is None
                   else PremarketInvalidField.NO_PREMARKET_BARS if not premarket
                   else PremarketInvalidField.INVALID_PREVIOUS_CLOSE if previous_close <= 0
                   else PremarketInvalidField.INVALID_REFERENCE_PRICE if reference_price <= 0
                   else PremarketInvalidField.INVALID_AVERAGE_VOLUME if average_volume <= 0
                   else None)
        context = PremarketContext(
            previous_close or Decimal("0"), reference_price or Decimal("0"),
            premarket_volume if premarket_volume is not None else Decimal("0"),
            average_volume or Decimal("0"),
        )
        return PremarketBuildResult(context, PremarketDiagnostic(
            len(minute_bars), len(premarket), previous_close, reference_price,
            premarket_volume, average_volume,
            None if not premarket else premarket[0].timestamp,
            None if not premarket else premarket[-1].timestamp, invalid,
        ))

    def _previous_regular_close(self, symbol: str, trading_date: date,
                                provider: MarketDataProvider,
                                as_of: datetime) -> Decimal | None:
        """Return only the exact predecessor session's completed final regular minute.

        Kiwoom's latest daily ``cur_prc`` can continue to reflect extended-hours
        prices, so it is not an authority for the completed regular-session close.
        The exchange calendar supplies the close boundary, including early closes;
        the exact final minute proves the bounded query reached that boundary.
        """
        previous_session = self.calendar.previous_trading_day(trading_date)
        window = self.calendar.session(previous_session)
        if window is None:
            return None
        final_timestamp = window.market_close - timedelta(minutes=1)
        bars = provider.get_minute_bars(
            [symbol], final_timestamp, window.market_close, MarketSession.REGULAR,
        )
        exact = [bar for bar in bars if (
            bar.symbol == symbol
            and bar.timestamp == final_timestamp
            and bar.session is MarketSession.REGULAR
            and bar.observed_at >= window.market_close
            and bar.available_at <= as_of
            and bar.close > 0
        )]
        if len(exact) != 1:
            return None
        return Decimal(str(exact[0].close))

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
                # A held symbol is marked on its own trade's exchange, read from the
                # database, so a restart or another symbol's candidate cannot change it.
                bind_open_position(provider, self.runtime, position.symbol)
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
        return account, PortfolioSnapshot(snapshots, base, Decimal("0"), as_of,
                                          pending_entries(self.runtime.broker))

    def _entry_sink(self, updated_at: datetime):  # type: ignore[no-untyped-def]
        def persist(session: Session, state: StrategyState) -> None:
            strategy_state_sink(updated_at)(session, state)
            if state.phase is StrategyPhase.POSITION_OPEN:
                position = self.runtime.broker.get_position(state.symbol)
                assert position is not None
                # The day's risk counts what the fill actually carries, not the plan.
                DailyRiskRepository(session).reserve_entry(
                    trading_date=state.trading_date, symbol=state.symbol,
                    issued_at=updated_at, planned_risk=self._entry_fill_risk(state),
                    base_notional=position.cost_basis, risk_version=self.risk_engine.config.version,
                    strategy_version=state.strategy_version,
                )
        return persist

    def _entry_fill_risk(self, state: StrategyState) -> Decimal:
        """The initial stop risk the filled entry carries, from the cash it cost.

        That cash is the execution price plus commission and FX cost, the same cost
        model Risk sized with. Risk approved the order's quantity at the effective
        price of its reference, so a fill that paid no more per share than that
        carries at most the approved stop risk and at most the approved cost, for
        any quantity up to the order's. Raising here rolls the whole fill back,
        broker memory included, so an over-budget entry is never recorded.
        """
        broker = self.runtime.broker
        buys = [fill for fill in broker.get_fills()
                if fill.symbol == state.symbol and fill.side is OrderSide.BUY]
        order = broker.get_order(buys[-1].order_id) if buys else None
        trade = broker.get_trade(state.symbol)
        if order is None or order.intent_type != IntentType.BASE_ENTRY or trade is None:
            raise EntryRiskInvariantError(f"{state.symbol}: no base-entry fill to account")
        fills = [fill for fill in buys if fill.order_id == order.id]
        quantity = sum((fill.quantity for fill in fills), Decimal("0"))
        outlay = sum((fill.fill_price * fill.quantity + fill.commission + fill.fx_cost
                      for fill in fills), Decimal("0"))
        assert state.initial_stop is not None
        approved_price = buy_effective_price(order.reference_price, broker.config)
        if (quantity > order.requested_quantity
                or outlay > approved_price * quantity * (Decimal("1") + RISK_UNIT_TOLERANCE)):
            raise EntryRiskInvariantError(
                f"{state.symbol}: paid {outlay} for {quantity}, above the approved "
                f"{approved_price} per share for {order.requested_quantity}")
        return outlay - state.initial_stop * quantity

    def _session_equity(self, session: Session, trading_date: date) -> Decimal | None:
        """Durable session-start equity for the daily 1R; never process-local."""
        if self.runtime.account_id is None:
            return None
        opening = session_opening_equity(session, self.runtime.account_id, trading_date, self.calendar)
        if opening.source is not SessionEquitySource.PREVIOUS_SESSION_CLOSE:
            logger.warning("SESSION RISK UNIT: %s uses %s equity %s",
                           trading_date, opening.source.value, opening.equity)
        return opening.equity

    def _load(self, symbol: str, trading_date: date) -> StrategyState | None:
        with self.runtime.session_factory() as session:
            return StrategyStateRepository(session).load(symbol, trading_date)

    def _save(self, state: StrategyState, updated_at: datetime,
              diagnostic: PremarketDiagnostic | None = None) -> None:
        with self.runtime.session_factory() as session:
            try:
                row = StrategyStateRepository(session).save(state, updated_at=updated_at)
                if diagnostic is not None:
                    existing = session.scalar(select(PremarketDiagnosticRecord).where(
                        PremarketDiagnosticRecord.strategy_state_id == row.id))
                    record = existing or PremarketDiagnosticRecord(
                        strategy_state_id=row.id, created_at=updated_at)
                    if existing is None:
                        session.add(record)
                    context = PremarketContext(
                        diagnostic.previous_close or Decimal("0"),
                        diagnostic.reference_price or Decimal("0"),
                        diagnostic.premarket_volume or Decimal("0"),
                        diagnostic.historical_average_daily_volume or Decimal("0"),
                    )
                    for name, value in {
                        "minute_bars_count": diagnostic.minute_bars_count,
                        "premarket_bars_count": diagnostic.premarket_bars_count,
                        "previous_close": diagnostic.previous_close,
                        "reference_price": diagnostic.reference_price,
                        "gap_pct": context.gap_pct,
                        "premarket_volume": diagnostic.premarket_volume,
                        "historical_average_daily_volume": diagnostic.historical_average_daily_volume,
                        "volume_ratio": context.volume_ratio,
                        "first_timestamp": diagnostic.first_timestamp,
                        "last_timestamp": diagnostic.last_timestamp,
                        "invalid_field": None if diagnostic.invalid_field is None else diagnostic.invalid_field.value,
                    }.items():
                        setattr(record, name, value)
                    session.flush()
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
            try:
                self._lifecycle.expire_ended_signals(now)
            except Exception:
                logger.exception("ENTRY SIGNAL EXPIRY FAILED; retrying next tick")
            local = now.astimezone(self._calendar.timezone)
            window = self._calendar.session(local.date())
            if window is None or not (window.market_open < now <= window.market_close):
                return ()
            entry_session_date = local.date()
            candidates = self._lifecycle.approved_candidates_for_entry_session(entry_session_date)
            if not candidates:
                logger.debug("ENTRY: no approved candidate for entry session %s (analysis session %s)",
                             entry_session_date, self._calendar.previous_trading_day(entry_session_date))
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

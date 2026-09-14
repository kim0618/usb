"""The production caller that carries an open position to a durable exit.

Everything this needs already existed: the engine decides, the risk engine sizes
the sell, the runner submits, and the execution service owns the transaction.
What was missing was something to call them once a bar completes, so this is a
driver rather than a second copy of any of that logic - no stop is re-tested and
no price is recomputed here.

Scope is two lifecycles: an open position hits its stop and is closed in full,
and a confirmed pyramid signal is bought once. The overnight and closing-review
paths stay where they are, because each needs its own contract; the one thing
this driver does for a carried position is test its stop, because a stop is
enforced on every completed regular bar whoever owns the rest of the position.

Protective exits
----------------
A stop, or the mandatory Day 2 close, protects capital that is already at risk, so
it is not held to the rule that keeps an entry or an add inside its own session. A
protective exit signalled after the last bar its session could still fill on - the
final regular bar, or the one before it - stays durably signalled and sells on the
first regular bar of the next XNYS session. It is never submitted while no bar can
fill it, so waiting leaves no rejected order behind. Entries and adds keep the
same-session contract unchanged. A carry the closing review or the risk engine
refused is the same kind of exit - the position may not stay open - so it crosses
sessions on the same terms; an ordinary signal and a reason-less row do not.

Protection cursor
-----------------
Each open state records the start of the last completed regular bar its stop has
been tested on. A tick tests every completed regular bar after that cursor, oldest
first, each exactly as an on-time tick would have tested it at the moment it
completed, so a tick the process missed - a stall, a restart, a night, an open it
slept through - never lets a breach go unseen. The first breach ends the replay and
anchors the exit at the moment that bar completed; later bars never reach the stop.

The add is sized by RiskEngine alone. This driver supplies the account, the
portfolio, and the trade's original planned risk - all read from the broker and
the durable state - and never proposes a quantity of its own.

Single process by construction. The runtime holder is process-local, so two
processes running this would each see the same open position and each sell it;
the runtime this binds to is the one the backend activated at startup.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
import logging

from sqlalchemy.orm import Session

from app.broker.domain import SimOrder, SimPosition
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import MarketSession, MinuteBar
from app.repositories.risk import DailyRiskRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot, PositionSnapshot,
)
from app.risk.engine import RiskEngine
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.config import VARIANT_CONFIGS, VariantConfig
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import BarEvaluation, StrategyReason, StrategyV0Engine, stop_reason
from app.strategy.indicators import available_regular_bars
from app.strategy.lifecycle import StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner

logger = logging.getLogger(__name__)

# The actual book takes its trailing multiplier from the state's trailing profile,
# so a variant contributes only the trailing mode. C is the control variant and the
# only one whose overnight, holding-day, and ATR settings match StrategyConfig's own
# defaults, which makes it the derived choice rather than a preference. G3 owns the
# final actual-book trailing contract; nothing here depends on the multiplier.
ACTUAL_VARIANT = VARIANT_CONFIGS["C"]

# Phases this stage manages. An open position in any other phase belongs to a
# lifecycle that is not wired yet, and is reported rather than quietly handled.
MANAGED_PHASES = frozenset({StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED,
                            StrategyPhase.EXIT_SIGNALLED})

# A carried position belongs to the end-of-day owner: the closing review carried it
# (or is still settling the size it carries), and Day 2 activation and the Day 2
# close are that owner's decisions. Its stop is still a stop, though, so this driver
# runs the same engine stop test on it and acts only on a breach. A bar that does
# not breach moves only the protection cursor, which leaves the trailing stop, the
# high-water mark, the pyramid, and a pending reduction exactly where the night left them.
CARRIED_PHASES = frozenset({StrategyPhase.OVERNIGHT_REVIEW, StrategyPhase.OVERNIGHT_HELD,
                            StrategyPhase.DAY2_ACTIVE})

# Every phase that holds a position whose stop is tested on each completed bar.
STOP_TESTED_PHASES = frozenset({StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED}
                               ) | CARRIED_PHASES

# Exits that protect capital already at risk: the stop family, and the mandatory
# Day 2 close that makes Day 3 impossible. Only these may settle in a later session.
PROTECTIVE_EXIT_REASONS = frozenset({StrategyReason.INITIAL_STOP.value,
                                     StrategyReason.TRAILING_STOP.value,
                                     StrategyReason.DAY2_MAX_HOLD.value})

# Exits that must complete once signalled, because the position may not stay open:
# the protective family, and a carry the closing review or the risk engine refused.
# Only these may settle in a later session. An ordinary signal, and a legacy row that
# records no reason, keep the same-session rule.
MANDATORY_EXIT_REASONS = PROTECTIVE_EXIT_REASONS | frozenset(
    {StrategyReason.OVERNIGHT_REJECTED.value})


def strategy_state_sink(updated_at: datetime):  # type: ignore[no-untyped-def]
    """A sink that writes the state a fill produced on the execution's session.

    Shared by entry and exit so both commit their phase with the fill itself.
    """
    def persist(session: Session, state: StrategyState) -> None:
        StrategyStateRepository(session).save(state, updated_at=updated_at)

    return persist


def closing_review_at(calendar: MarketCalendar, engine: StrategyV0Engine,
                      day: date) -> datetime | None:
    """The configured closing review moment for an actual XNYS session.

    From this moment the end-of-day owner decides what a position does overnight,
    so the minute driver and the closing review both read it from here.
    """
    close = calendar.regular_market_close(day)
    return None if close is None else close - engine.config.closing_review_before_close


def is_mandatory_exit(state: StrategyState) -> bool:
    """A signalled exit whose recorded reason says the position may not stay open."""
    return (state.phase is StrategyPhase.EXIT_SIGNALLED
            and state.phase_reason in MANDATORY_EXIT_REASONS)


def stop_signal(evaluated: BarEvaluation) -> StrategyState:
    """The engine's signalled exit, with its reason recorded on the durable phase."""
    return replace(evaluated.state, phase_reason=evaluated.decision.reason_code)


def _next_bar_start(moment: datetime) -> datetime:
    """The first minute-bar start strictly after ``moment``."""
    return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)


def protection_cursor(state: StrategyState, position: SimPosition) -> datetime:
    """Bars starting after this moment have not had this position's stop tested yet.

    A state that has never been protected starts at the bar its position opened on:
    the entry fills at that bar's open, so its low is the first price the stop can
    meet, and no bar before the fill is ever tested.
    """
    if state.last_protected_bar_at is not None:
        return state.last_protected_bar_at
    return position.opened_at - timedelta(minutes=1)


def _prices(bar: MinuteBar) -> tuple[object, ...]:
    return (bar.open, bar.high, bar.low, bar.close, bar.volume)


def distinct_bars(bars: Sequence[MinuteBar]) -> tuple[MinuteBar, ...] | None:
    """Each minute once, in time order; None when a regular minute disagrees with itself.

    A provider page boundary can repeat a bar, and the same bar twice is one bar. Two
    different regular bars for one minute are an ambiguous tape, and a stop is never
    tested against a guess between them.
    """
    kept: dict[tuple[str, datetime, MarketSession], MinuteBar] = {}
    for bar in bars:
        key = (bar.symbol, bar.timestamp, bar.session)
        seen = kept.get(key)
        if seen is None:
            kept[key] = bar
        elif _prices(seen) != _prices(bar) and bar.session is MarketSession.REGULAR:
            return None
    return tuple(sorted(kept.values(), key=lambda bar: (bar.timestamp, bar.symbol)))


class PositionAction(StrEnum):
    HOLD = "HOLD"
    EXIT_FILLED = "EXIT_FILLED"
    EXIT_UNFILLED = "EXIT_UNFILLED"
    # A protective exit is signalled and durable, but no regular bar can fill it yet;
    # nothing was submitted.
    EXIT_PENDING = "EXIT_PENDING"
    ADD_FILLED = "ADD_FILLED"
    ADD_UNFILLED = "ADD_UNFILLED"
    SKIPPED = "SKIPPED"
    # The position could not be read on its trade's exchange, so its stop was not
    # tested this tick. Reported, never folded into an ordinary skip.
    PROTECTION_UNAVAILABLE = "PROTECTION_UNAVAILABLE"


@dataclass(frozen=True)
class PositionOutcome:
    """What one evaluation did, including the reason it did nothing."""

    symbol: str
    action: PositionAction
    reason: str
    state: StrategyState | None = None
    order: SimOrder | None = None

    @property
    def exited(self) -> bool:
        return self.action is PositionAction.EXIT_FILLED

    @property
    def added(self) -> bool:
        return self.action is PositionAction.ADD_FILLED


@dataclass(frozen=True)
class ProtectionReplay:
    """Where replaying the unprotected bars left a state; nothing here is persisted."""

    state: StrategyState
    breached: bool = False
    bars: int = 0
    reason: str = StrategyReason.HOLD.value
    add: BarEvaluation | None = None
    cursor: datetime | None = None
    conflict: bool = False


class PositionLifecycleService:
    def __init__(self, runtime: SimulationRuntimeContext, *,
                 engine: StrategyV0Engine | None = None,
                 risk_engine: RiskEngine | None = None,
                 calendar: MarketCalendar | None = None,
                 variant: VariantConfig = ACTUAL_VARIANT) -> None:
        if not runtime.durable:
            raise ValueError("position management requires a durable runtime")
        self.runtime = runtime
        self.engine = engine or StrategyV0Engine()
        self.calendar = calendar or MarketCalendar()
        self.variant = variant
        self.runner = StrategyLifecycleRunner(runtime.broker, runtime=runtime,
                                              risk_engine=risk_engine, calendar=self.calendar)

    @property
    def broker(self):  # type: ignore[no-untyped-def]
        return self.runtime.broker

    def evaluate(self, bars_by_symbol: Mapping[str, Sequence[MinuteBar]], *,
                 as_of: datetime,
                 symbols: frozenset[str] | None = None) -> tuple[PositionOutcome, ...]:
        """Evaluate every open position the active broker holds.

        The broker is the authority on what is open: reading holdings from the
        database instead would create a second control loop that could disagree
        with the broker actually submitting the orders.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        marks = self._marks(bars_by_symbol, as_of=as_of)
        return tuple(
            self._evaluate_one(position, tuple(bars_by_symbol.get(position.symbol, ())),
                               as_of=as_of, marks=marks)
            for position in self.broker.get_positions()
            if symbols is None or position.symbol in symbols
        )

    # Protection cursor --------------------------------------------------

    def fetch_start(self, symbol: str, window: TradingSessionWindow) -> datetime:
        """Where a read for this symbol must start so no unprotected bar is skipped.

        Normally the session's own open, because the indicators a bar is judged with
        need the whole session. When the stop was last tested in an earlier session
        that still has bars after that point - the process was down across a close,
        or across an open - the read starts at that session's open instead, so its
        remaining bars are tested before this session's. The cursor is read from the
        database, so a restart finds exactly what the last process left.
        """
        state = self._single_state(symbol)
        position = self.broker.get_position(symbol)
        if state is None or position is None or state.phase not in STOP_TESTED_PHASES:
            return window.market_open
        cursor = protection_cursor(state, position)
        day = cursor.astimezone(self.calendar.timezone).date()
        session = self.calendar.session(day)
        if session is None or cursor >= session.market_close - timedelta(minutes=1):
            session = self.calendar.session(self.calendar.next_trading_day(day))
        if session is None:
            return window.market_open
        return min(session.market_open, window.market_open)

    def replay(self, state: StrategyState, position: SimPosition,
               bars: Sequence[MinuteBar], *, as_of: datetime,
               trading_allowed: bool) -> ProtectionReplay:
        """Test the stop on every completed regular bar after the cursor, oldest first.

        Each bar is a full engine evaluation at the moment that bar completed - the
        stop that existed before it, tested against its low, then its high and ATR
        moving the trailing stop for the next bar - so a missed bar is decided exactly
        as an on-time tick would have decided it. The first breach ends the replay:
        later bars never reach this position's stop, and the exit is anchored at the
        moment the breaching bar completed. A bar the night owns keeps only its stop
        test; every other figure stays where the night left it. An add is acted on only
        when the newest bar confirms it and trading is still allowed: a missed bar's
        add is never bought after the fact.
        """
        tape = distinct_bars(bars)
        if tape is None:
            return ProtectionReplay(state, conflict=True)
        cursor = protection_cursor(state, position)
        fresh = tuple(bar for bar in available_regular_bars(tape, as_of) if bar.timestamp > cursor)
        # An add signalled earlier and never bought keeps its own signal time.
        anchor = state.last_market_as_of if _add_outstanding(state) else None
        current, add = state, None
        for index, bar in enumerate(fresh):
            day = bar.timestamp.astimezone(self.calendar.timezone).date()
            window = self.calendar.session(day)
            if window is None:
                current = replace(current, last_protected_bar_at=bar.timestamp)
                continue
            completed_at = bar.available_at
            history = tuple(item for item in tape if item.timestamp <= bar.timestamp)
            evaluated = self.engine.evaluate_position(
                state=current, bars=history, market_open=window.market_open,
                as_of=completed_at, current_price=Decimal(str(bar.close)),
                average_price=position.average_price, variant=self.variant)
            if evaluated.decision.decision is DecisionType.EXIT:
                signalled = replace(stop_signal(evaluated), last_protected_bar_at=bar.timestamp)
                return ProtectionReplay(signalled, breached=True, bars=index + 1,
                                        reason=evaluated.decision.reason_code, cursor=cursor)
            if self._night_owns(current, day, completed_at):
                current = replace(current, last_protected_bar_at=bar.timestamp)
                continue
            progressed = replace(evaluated.state, last_protected_bar_at=bar.timestamp)
            if anchor is not None:
                progressed = replace(progressed, last_market_as_of=anchor)
            if evaluated.decision.decision is DecisionType.ADD:
                if trading_allowed and index == len(fresh) - 1:
                    add = evaluated
                else:
                    progressed = replace(progressed, add_signal_issued=current.add_signal_issued)
            current = progressed
        return ProtectionReplay(current, bars=len(fresh), add=add, cursor=cursor)

    def _night_owns(self, state: StrategyState, day: date, completed_at: datetime) -> bool:
        """Whether only the stop is decided on this bar.

        A carried position belongs to the end-of-day owner; so does every bar that
        completes from the closing review on; and so does a position still held past
        its entry session without a carry decision, because the night never reviewed
        it and nothing but its stop may be decided for it here.
        """
        if state.phase in CARRIED_PHASES:
            return True
        if state.entry_trading_date is not None and day > state.entry_trading_date:
            return True
        review_at = closing_review_at(self.calendar, self.engine, day)
        return review_at is not None and completed_at >= review_at

    def _marks(self, bars_by_symbol: Mapping[str, Sequence[MinuteBar]], *,
               as_of: datetime) -> dict[str, Decimal]:
        """Last completed regular close per symbol; nothing is marked without one.

        Equity drives every risk cap an add is measured against, so a mark has to
        come from a bar the market actually printed. A position with no completed
        bar is simply absent here, and the add path refuses rather than guessing.
        """
        marks: dict[str, Decimal] = {}
        for symbol, bars in bars_by_symbol.items():
            visible = available_regular_bars(tuple(bars), as_of)
            if visible:
                marks[symbol] = Decimal(str(visible[-1].close))
        return marks

    def _evaluate_one(self, position: SimPosition, bars: tuple[MinuteBar, ...], *,
                      as_of: datetime, marks: Mapping[str, Decimal]) -> PositionOutcome:
        symbol = position.symbol
        with self.runtime.session_factory() as session:
            states = StrategyStateRepository(session).list_open(symbol)
        if not states:
            # No reconstruction: a stop this process never recorded is a stop it
            # cannot honestly enforce.
            return self._skip(symbol, "no open strategy state")
        if len(states) > 1:
            return self._skip(symbol, f"{len(states)} open strategy states")
        state = states[0]
        if state.phase not in MANAGED_PHASES and state.phase not in CARRIED_PHASES:
            return self._skip(symbol, f"phase {state.phase.value} is not managed yet")
        if None in (state.entry_price, state.initial_stop, state.active_stop):
            # Both the engine and the stop reason divide on these; an incomplete
            # row is refused rather than defaulted into a stop nobody set.
            return self._skip(symbol, "strategy state has no entry or stop")
        if state.phase is StrategyPhase.EXIT_SIGNALLED:
            # Already committed to leaving. Re-running the engine here would both
            # re-decide a settled question and attempt an EXIT_SIGNALLED to
            # EXIT_SIGNALLED transition the state machine rejects. The retry records
            # the reason the exit was signalled for; only a legacy row that recorded
            # none is named from its stops.
            return self._exit(state, position, bars, as_of=as_of,
                              reason=state.phase_reason or stop_reason(state).value)
        visible = available_regular_bars(bars, as_of)
        if not visible:
            return self._skip(symbol, "no completed regular bar")
        # From the closing review on, the end-of-day owner decides what the position
        # does tonight, so nothing but the stop is decided for it here and no add is
        # bought: the night is decided on the same state whichever owner goes first.
        # A position held past its entry session is the night's too, reviewed or not.
        today = as_of.astimezone(self.calendar.timezone).date()
        review_at = closing_review_at(self.calendar, self.engine, today)
        trading = (review_at is not None and as_of < review_at
                   and state.phase not in CARRIED_PHASES
                   and state.entry_trading_date in (None, today))
        replay = self.replay(state, position, bars, as_of=as_of, trading_allowed=trading)
        if replay.conflict:
            return self._skip(symbol, "conflicting regular bars for one minute")
        if replay.bars == 0:
            return self._skip(symbol, "already evaluated through this bar")
        if replay.bars > 1 or replay.breached:
            tz = self.calendar.timezone
            logger.info("POSITION CATCH-UP: %s cursor=%s bars=%d through=%s breach=%s",
                        symbol, replay.cursor.astimezone(tz).isoformat() if replay.cursor else None,
                        replay.bars, replay.state.last_protected_bar_at.astimezone(tz).isoformat()
                        if replay.state.last_protected_bar_at else None,
                        replay.reason if replay.breached else None)
        if replay.breached:
            # The stop outranks the add: a bar that breaches leaves rather than buys.
            return self._exit(replay.state, position, bars, as_of=as_of,
                              reason=replay.reason, signalled=True)
        if replay.add is not None:
            return self._add(replay.state, position, bars, as_of=as_of, marks=marks,
                             market_as_of=replay.add.decision.market_as_of,
                             reason=replay.add.decision.reason_code)
        if trading and _add_outstanding(state):
            # The signal was issued on an earlier bar and never bought. It is not
            # re-decided - the engine issues an add once - so the retry carries the
            # confirmation that produced it, and the signal's own time with it.
            # Re-dating the retry to now would push the next-bar rule past every
            # bar that has completed since, and the add could never settle. The
            # trailing figures the bars produced are kept; only the marker holds.
            signalled_at = state.last_market_as_of or as_of
            return self._add(replace(replay.state, last_market_as_of=signalled_at),
                             position, bars, as_of=as_of, marks=marks,
                             market_as_of=signalled_at,
                             reason=StrategyReason.PYRAMID_CONFIRMATION.value)
        self._save(replay.state, updated_at=as_of)
        return PositionOutcome(symbol, PositionAction.HOLD, StrategyReason.HOLD.value,
                               replay.state)

    def _add(self, state: StrategyState, position: SimPosition, bars: tuple[MinuteBar, ...], *,
             as_of: datetime, marks: Mapping[str, Decimal], market_as_of: datetime,
             reason: str) -> PositionOutcome:
        """Buy one pyramid add through the durable execution transaction.

        Every figure handed to the risk engine is somebody else's truth: quantity
        and average price are the broker's, the stops are the strategy state's,
        and the risk budget is the trade's own planned initial risk - which the
        broker never rewrites on an add, so one R stays one R for the trade's life.
        """
        trade = self.broker.get_trade(state.symbol)
        if trade is None:
            return self._unfilled(state, "no open trade", as_of)
        missing = [item.symbol for item in self.broker.get_positions() if item.symbol not in marks]
        if missing:
            return self._unfilled(state, f"unmarked positions: {','.join(sorted(missing))}", as_of)
        account_state = self.broker.account_snapshot(dict(marks), as_of)
        account = AccountSnapshot(account_state.equity, account_state.cash,
                                  Currency(self.broker.currency), as_of)
        decision = StrategyDecision(state.symbol, DecisionType.ADD, reason, market_as_of,
                                    strategy_version=state.strategy_version)
        result = self.runner.execute_add(
            state=state, decision=decision, account=account,
            portfolio=self._portfolio(state, position, marks, as_of=as_of),
            planned_initial_risk=trade.planned_initial_risk, market_bars=bars,
            created_at=as_of, actual_risk_state=self._daily_risk(state.trading_date),
            on_state=strategy_state_sink(as_of))
        if result.order is None:
            # Nothing was submitted, so nothing joined a transaction; the signal
            # still has to be durable or the retry would have nothing to read.
            rejected = (result.risk.rejection_reason.value if result.risk is not None
                        and result.risk.rejection_reason is not None else "pyramid not permitted")
            return self._unfilled(result.state, rejected, as_of)
        if not self.broker.get_fills(result.order.id):
            logger.warning("ADD UNFILLED: %s %s", state.symbol, result.order.rejection_reason)
            return PositionOutcome(state.symbol, PositionAction.ADD_UNFILLED,
                                   str(result.order.rejection_reason), result.state, result.order)
        logger.info("ADD FILLED: %s %s qty=%s", state.symbol, reason, result.order.filled_quantity)
        return PositionOutcome(state.symbol, PositionAction.ADD_FILLED, reason,
                               result.state, result.order)

    def _portfolio(self, state: StrategyState, position: SimPosition,
                   marks: Mapping[str, Decimal], *, as_of: datetime) -> PortfolioSnapshot:
        """Book exposure split by whether a symbol has already pyramided.

        Schema 0009 records no base/pyramid split of a position's cost basis, so a
        symbol that has added counts its whole basis against the pyramid reserve.
        That can only under-approve, never over-approve, and it is not applied to
        the symbol being evaluated - reaching here at all requires add_count 0.
        """
        added = self._added_symbols()
        base = sum((item.cost_basis for item in self.broker.get_positions()
                    if item.symbol not in added), Decimal("0"))
        pyramid = sum((item.cost_basis for item in self.broker.get_positions()
                       if item.symbol in added), Decimal("0"))
        snapshot = PositionSnapshot(
            state.symbol, position.quantity, position.average_price, marks[state.symbol],
            Currency(self.broker.currency), initial_stop=state.initial_stop,
            active_stop=state.active_stop, add_count=state.add_count,
            base_notional_account_ccy=position.cost_basis, overnight=state.overnight)
        return PortfolioSnapshot((snapshot,), base, pyramid, as_of)

    def _added_symbols(self) -> frozenset[str]:
        with self.runtime.session_factory() as session:
            repository = StrategyStateRepository(session)
            return frozenset(
                item.symbol for item in self.broker.get_positions()
                if any(open_state.add_count >= 1
                       for open_state in repository.list_open(item.symbol)))

    def _daily_risk(self, trading_date) -> DailyTradingState:  # type: ignore[no-untyped-def]
        """Read the day's reservations; this path commits none of its own."""
        with self.runtime.session_factory() as session:
            return DailyRiskRepository(session).load(trading_date)

    def _unfilled(self, state: StrategyState, reason: str, as_of: datetime) -> PositionOutcome:
        self._save(state, updated_at=as_of)
        logger.warning("ADD UNFILLED: %s (%s)", state.symbol, reason)
        return PositionOutcome(state.symbol, PositionAction.ADD_UNFILLED, reason, state)

    def _exit(self, state: StrategyState, position: SimPosition, bars: tuple[MinuteBar, ...], *,
              as_of: datetime, reason: str, signalled: bool = False) -> PositionOutcome:
        """Sell the whole position through the durable execution transaction.

        ``signalled`` marks a state this call just signalled, which has to be made
        durable even when nothing is submitted; a retry's state already is.
        """
        visible = available_regular_bars(bars, as_of)
        # A retry is still the exit that was signalled, so it keeps the signal's
        # own time. Re-dating it to now would push the next-bar rule past every
        # bar that has since completed, and the exit could never settle.
        market_as_of = state.last_market_as_of or as_of
        fill_session_at = market_as_of
        if is_mandatory_exit(state):
            resolved = self._protective_fill_session(market_as_of, as_of, visible)
            if resolved is None:
                # No regular bar can fill it yet. Submitting now would only record
                # a rejection; the signal waits, durable, for a bar that can.
                if signalled:
                    self._save(state, updated_at=as_of)
                logger.warning("PROTECTIVE EXIT PENDING: %s %s signalled_at=%s",
                               state.symbol, state.phase_reason, market_as_of.isoformat())
                return PositionOutcome(state.symbol, PositionAction.EXIT_PENDING,
                                       "no executable regular bar yet", state)
            fill_session_at = resolved
        if not visible:
            self._save(state, updated_at=as_of)
            return PositionOutcome(state.symbol, PositionAction.EXIT_UNFILLED,
                                   "no completed regular bar", state)
        decision = StrategyDecision(state.symbol, DecisionType.EXIT, reason, market_as_of,
                                    strategy_version=state.strategy_version)
        snapshot = PositionSnapshot(
            state.symbol, position.quantity, position.average_price,
            Decimal(str(visible[-1].close)), Currency(self.broker.currency),
            initial_stop=state.initial_stop, add_count=state.add_count,
            base_notional_account_ccy=position.cost_basis, overnight=state.overnight)
        # Quantity is left to the exit intent, which sells the snapshot's whole
        # position; a partial exit is a different contract.
        result = self.runner.execute_exit(state=state, decision=decision, position=snapshot,
                                          market_bars=bars, created_at=as_of,
                                          on_state=strategy_state_sink(as_of),
                                          fill_session_at=fill_session_at)
        assert result.order is not None
        if not self.broker.get_fills(result.order.id):
            logger.warning("EXIT UNFILLED: %s %s", state.symbol, result.order.rejection_reason)
            return PositionOutcome(state.symbol, PositionAction.EXIT_UNFILLED,
                                   str(result.order.rejection_reason), result.state, result.order)
        logger.info("EXIT FILLED: %s %s qty=%s", state.symbol, reason, result.order.filled_quantity)
        return PositionOutcome(state.symbol, PositionAction.EXIT_FILLED, reason,
                               result.state, result.order)

    def _protective_fill_session(self, signalled_at: datetime, as_of: datetime,
                                 visible: tuple[MinuteBar, ...]) -> datetime | None:
        """The moment whose regular session a mandatory exit fills in, or None.

        In its own session it keeps the ordinary next-bar rule, as long as a bar
        can still start after the signal and before the close. That still holds in
        a later session when such a bar of the signal's session was published and
        read: a breach found late - a missed tick, a restart after the close or the
        next open - sells exactly where an on-time tick would have sold it. Only
        when the signal's session has no bar after the signal does it move to the
        current one, submitted once that session has printed a bar after the signal
        - so the sell settles on the first such bar, its open, and never on the
        previous session's window.
        """
        tz = self.calendar.timezone
        signal_day = signalled_at.astimezone(tz).date()
        today = as_of.astimezone(tz).date()
        signal_window = self.calendar.session(signal_day)
        if today == signal_day:
            if signal_window is None or _next_bar_start(signalled_at) >= signal_window.market_close:
                return None
            return signalled_at
        if signal_window is not None and any(
                signalled_at < bar.timestamp < signal_window.market_close for bar in visible):
            return signalled_at
        window = self.calendar.session(today)
        if window is None:
            return None
        if not any(bar.timestamp > signalled_at
                   and window.market_open <= bar.timestamp < window.market_close
                   for bar in visible):
            return None
        return as_of

    def _single_state(self, symbol: str) -> StrategyState | None:
        with self.runtime.session_factory() as session:
            states = StrategyStateRepository(session).list_open(symbol)
        return states[0] if len(states) == 1 else None

    def _save(self, state: StrategyState, *, updated_at: datetime) -> None:
        """Persist a state no execution accompanied, in its own transaction."""
        with self.runtime.session_factory() as session:
            try:
                StrategyStateRepository(session).save(state, updated_at=updated_at)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _skip(symbol: str, reason: str) -> PositionOutcome:
        logger.info("POSITION SKIPPED: %s (%s)", symbol, reason)
        return PositionOutcome(symbol, PositionAction.SKIPPED, reason)


def _add_outstanding(state: StrategyState) -> bool:
    """An add that was signalled and never bought, expressed in existing fields.

    ``add_signal_issued`` without ``add_count`` is the whole retry contract: the
    engine issues one confirmation and will not issue another, and a fill is the
    only thing that moves the count. No new column carries this.
    """
    return (state.phase is StrategyPhase.POSITION_OPEN
            and state.add_signal_issued and state.add_count == 0)

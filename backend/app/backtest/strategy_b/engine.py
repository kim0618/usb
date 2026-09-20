"""One session of Strategy B: the tick loop that connects the research layer to an account.

The order inside a tick is the whole design (B_ENGINE_DESIGN_V1 4절):

1. open positions first - a stop that fires now frees a slot and books a loss that the daily
   limit must already know about;
2. live candidates next, so a symbol already being watched is never opened twice;
3. new detections last, only on minutes the prefilter kept and only for symbols the account
   may still trade today.

A fill needs care. The FSM refuses a signal on its own terms (signal TTL, eligibility, price
drift) and the account refuses on its own (open slots, daily loss, size). The engine asks the
FSM first with no fill: if the candidate survives that tick still signalled, and only then, it
asks the account. Booking a position the FSM then dropped would leave the run holding shares
no candidate ever entered.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.backtest.strategy_b.costs import FillScenario, signal_fill
from app.backtest.strategy_b.portfolio import Portfolio, Trade
from app.backtest.strategy_b.prefilter import cheap_gate_minutes
from app.strategy_b.config import StrategyBConfig, parse_et_clock
from app.strategy_b.exits import advance_position
from app.strategy_b.features import SessionTape
from app.strategy_b.fsm import Candidate, DropReason, FillOutcome, Tick, advance, open_candidate
from app.strategy_b.models import CandidateState, CorporateActionFlag, MomentumBar
from app.strategy_b.rvol import VolumeProfile
from app.strategy_b.scanner import rank, scan
from app.strategy_b.scope import ScopeDecision
from app.strategy_b.session import ET, SessionBoundaries
from app.strategy_b.snapshot import compute_feature_snapshot
from app.strategy_b.split_adjustment import SplitRecord

ENGINE_VERSION = "strategy-b-session-engine-v1"


@dataclass(frozen=True, slots=True)
class SymbolSession:
    """Everything one symbol brings to one session. The engine reads nothing else."""

    tape: SessionTape
    scope: ScopeDecision | None
    rvol_history: tuple[VolumeProfile, ...] = ()
    splits: tuple[SplitRecord, ...] = ()
    corporate_action_flags: frozenset[CorporateActionFlag] = frozenset()

    @property
    def symbol(self) -> str:
        return self.tape.symbol


@dataclass(slots=True)
class SessionReport:
    session_date: date
    ticks: int
    prefiltered_minutes: int
    candidates_opened: int
    entries: int
    trades: list[Trade] = field(default_factory=list)
    finished_candidates: list[Candidate] = field(default_factory=list)
    refusals: dict[str, int] = field(default_factory=dict)

    def count_refusal(self, reason: DropReason) -> None:
        self.refusals[reason.value] = self.refusals.get(reason.value, 0) + 1


def run_session(symbols: Sequence[SymbolSession], *, config: StrategyBConfig,
                portfolio: Portfolio, scenario: FillScenario,
                boundaries: SessionBoundaries) -> SessionReport:
    """Replay one session for ``symbols`` and book every entry and exit into ``portfolio``."""
    by_symbol = {item.symbol: item for item in symbols}
    if len(by_symbol) != len(symbols):
        raise ValueError("one session takes each symbol once")
    for item in symbols:
        if item.tape.boundaries.session_date != boundaries.session_date:
            raise ValueError(f"{item.symbol} is not a {boundaries.session_date} tape")

    portfolio.start_session(boundaries.session_date)
    grid = _tick_grid(boundaries, config)
    detection = [tick for tick in grid if _in_scan_window(tick, config)]
    report = SessionReport(boundaries.session_date, len(grid), 0, 0, 0)

    wanted: dict[str, set[datetime]] = {}
    for item in symbols:
        result = cheap_gate_minutes(item.symbol, item.tape.bars, detection,
                                    boundaries=item.tape.boundaries, scanner=config.scanner,
                                    features=config.features)
        wanted[item.symbol] = set(result.minutes)
        report.prefiltered_minutes += result.survivors

    live: dict[str, Candidate] = {}
    for moment in grid:
        _advance_positions(by_symbol, portfolio, config, moment, report)
        _advance_candidates(by_symbol, live, portfolio, config, scenario, moment, report)
        _detect(by_symbol, wanted, live, portfolio, config, moment, report)
    return report


# ---- tick steps ----------------------------------------------------------------------------

def _advance_positions(by_symbol: dict[str, SymbolSession], portfolio: Portfolio,
                       config: StrategyBConfig, moment: datetime, report: SessionReport) -> None:
    for symbol in sorted(portfolio.open_positions):
        position = portfolio.open_positions[symbol]
        update = advance_position(position, by_symbol[symbol].tape, moment, config=config.exit,
                                  scope=config.features.hod_scope)
        if not update.events and update.position.is_open:
            portfolio.open_positions[symbol] = update.position
            continue
        closed = portfolio.book(symbol, update.position, update.events)
        if closed is not None:
            report.trades.append(closed)


def _advance_candidates(by_symbol: dict[str, SymbolSession], live: dict[str, Candidate],
                        portfolio: Portfolio, config: StrategyBConfig, scenario: FillScenario,
                        moment: datetime, report: SessionReport) -> None:
    for symbol in sorted(live):
        item = by_symbol[symbol]
        signalled_before = live[symbol].state is CandidateState.ENTRY_SIGNALLED
        probe = advance(live[symbol], _tick(item, config, moment), config)
        # The account answers a signal on the tick *after* it (B-F0 8.3), so a candidate that
        # only became ENTRY_SIGNALLED on this tick is not filled yet: one transition per tick.
        if signalled_before and probe.state is CandidateState.ENTRY_SIGNALLED:
            probe = _try_to_fill(item, probe, portfolio, config, scenario, moment, report)
        if probe.is_terminal:
            live.pop(symbol)
            report.finished_candidates.append(probe)
            if probe.drop_reason is not None:
                report.count_refusal(probe.drop_reason)
            if probe.state is CandidateState.ENTERED:
                report.entries += 1
        else:
            live[symbol] = probe


def _try_to_fill(item: SymbolSession, candidate: Candidate, portfolio: Portfolio,
                 config: StrategyBConfig, scenario: FillScenario, moment: datetime,
                 report: SessionReport) -> Candidate:
    """The candidate survived this tick as a signal; now ask the account."""
    assert candidate.setup is not None and candidate.signal_bar_timestamp is not None
    bar = _bar_at(item.tape, candidate.signal_bar_timestamp)
    raw = signal_fill(candidate.setup.trigger_price, bar, scenario,
                      _next_available_bar(item.tape, bar, moment))
    if raw is None:
        return candidate  # nothing to fill at yet; the signal TTL decides how long that may last
    booked = portfolio.enter(item.symbol, candidate.setup.setup, raw_price=raw,
                             initial_stop=candidate.setup.initial_stop, at=moment,
                             entry_bar_timestamp=bar.timestamp)
    outcome = (FillOutcome(filled=False, reason=booked) if isinstance(booked, DropReason)
               else FillOutcome(filled=True, price=booked.entry_price))
    return advance(candidate, _tick(item, config, moment, fill=outcome), config)


def _detect(by_symbol: dict[str, SymbolSession], wanted: dict[str, set[datetime]],
            live: dict[str, Candidate], portfolio: Portfolio, config: StrategyBConfig,
            moment: datetime, report: SessionReport) -> None:
    decisions = []
    for symbol in sorted(wanted):
        if moment not in wanted[symbol] or symbol in live:
            continue
        if not portfolio.may_be_a_candidate(symbol):
            continue
        item = by_symbol[symbol]
        decision = scan(_snapshot(item, config, moment), scanner=config.scanner,
                        candidate=config.candidate)
        if decision.passed:
            decisions.append((decision, item))
    for decision, item in sorted(decisions, key=lambda pair: pair[0].sort_key):
        candidate = advance(open_candidate(decision), _tick(item, config, moment), config)
        report.candidates_opened += 1
        if candidate.is_terminal:
            report.finished_candidates.append(candidate)
            if candidate.drop_reason is not None:
                report.count_refusal(candidate.drop_reason)
        else:
            live[item.symbol] = candidate


# ---- helpers -------------------------------------------------------------------------------

def _tick(item: SymbolSession, config: StrategyBConfig, moment: datetime,
          fill: FillOutcome | None = None) -> Tick:
    return Tick(as_of=moment, snapshot=_snapshot(item, config, moment), tape=item.tape,
                scope=item.scope, fill=fill)


def _snapshot(item: SymbolSession, config: StrategyBConfig, moment: datetime):
    return compute_feature_snapshot(item.tape, moment, config, rvol_history=item.rvol_history,
                                    splits=item.splits,
                                    corporate_action_flags=item.corporate_action_flags)


def _tick_grid(boundaries: SessionBoundaries, config: StrategyBConfig) -> list[datetime]:
    """Availability moments from the first scannable minute to the closing bell.

    The grid runs past the scan window on purpose: detection stops at 15:30, but positions and
    candidates opened before it still need every remaining minute, including the forced exit.
    """
    start = _et_moment(boundaries.session_date, config.scanner.scan_window_start_et)
    end = boundaries.regular_close
    if end <= start:
        return []
    return [start + timedelta(minutes=k)
            for k in range(int((end - start) / timedelta(minutes=1)) + 1)]


def _in_scan_window(moment: datetime, config: StrategyBConfig) -> bool:
    local = moment.astimezone(ET).timetz().replace(tzinfo=None)
    return (parse_et_clock(config.scanner.scan_window_start_et) <= local
            <= parse_et_clock(config.scanner.scan_window_end_et))


def _et_moment(day: date, clock: str) -> datetime:
    return datetime.combine(day, parse_et_clock(clock), tzinfo=ET)


def _bar_at(tape: SessionTape, timestamp: datetime) -> MomentumBar:
    index = tape.first_index_at_or_after(timestamp)
    if index >= len(tape.bars) or tape.bars[index].timestamp != timestamp:
        raise KeyError(f"{tape.symbol} has no bar at {timestamp.isoformat()}")
    return tape.bars[index]


def _next_available_bar(tape: SessionTape, bar: MomentumBar, as_of: datetime) -> MomentumBar | None:
    index = tape.first_index_at_or_after(bar.timestamp) + 1
    return tape.bars[index] if index < tape.cut(as_of) else None

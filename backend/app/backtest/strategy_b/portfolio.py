"""The only mutable state in a Strategy B run: cash, open positions and the day's limits.

The research layer decides *what* should happen to a candidate or a position; this class
decides whether the account may act on it and books the result. B-F0 8.4 lists the three
limits it enforces (open positions, one entry per symbol per day, the daily loss limit) and
they are enforced here rather than in the FSM, because they are facts about the account, not
about the symbol.

Equity is realised-only: an open position is not marked to market, so position sizing during
the day uses the equity the account has actually banked. Marking to market would make every
new position's size depend on the unrealised profit of the ones already open, which turns one
lucky morning into a compounding artefact the study never intended to measure.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.backtest.strategy_b.costs import CostModel
from app.strategy_b.config import RiskConfig
from app.strategy_b.exits import ExitEvent, ExitReason, Position, open_position
from app.strategy_b.fsm import DropReason
from app.strategy_b.models import SetupType
from app.strategy_b.sizing import position_size


@dataclass(frozen=True, slots=True)
class TradeLeg:
    """One exit out of a position: a partial or the close."""

    reason: ExitReason
    at: datetime
    price: float
    """After slippage."""
    shares: int
    fee: float


@dataclass(slots=True)
class Trade:
    """One entry and every exit that followed it."""

    symbol: str
    setup: SetupType
    session_date: date
    entered_at: datetime
    entry_bar_timestamp: datetime
    entry_price: float
    """After slippage: the price the account actually paid."""
    shares: int
    initial_stop: float
    entry_fee: float
    legs: list[TradeLeg] = field(default_factory=list)

    @property
    def risk_per_share(self) -> float:
        """1R, measured from the filled price, so slippage makes the risk taken honest."""
        return self.entry_price - self.initial_stop

    @property
    def risk_amount(self) -> float:
        return self.risk_per_share * self.shares

    @property
    def closed_shares(self) -> int:
        return sum(leg.shares for leg in self.legs)

    @property
    def is_closed(self) -> bool:
        return self.closed_shares >= self.shares

    @property
    def fees(self) -> float:
        return self.entry_fee + sum(leg.fee for leg in self.legs)

    @property
    def gross_pnl(self) -> float:
        return sum((leg.price - self.entry_price) * leg.shares for leg in self.legs)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees

    @property
    def realized_r(self) -> float:
        """Net R, costs included. A study that reports R before costs reports a fiction."""
        return self.net_pnl / self.risk_amount


class Portfolio:
    """Account state across a run. One instance per backtest, reset per session."""

    def __init__(self, *, equity: float, costs: CostModel, risk: RiskConfig) -> None:
        if equity <= 0:
            raise ValueError("equity must be positive")
        self.equity = equity
        self.starting_equity = equity
        self.costs = costs
        self.risk = risk
        self.open_positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self._open_trades: dict[str, Trade] = {}
        self.entered_today: set[str] = set()
        self._entries_today: dict[str, int] = {}
        self.realized_r_today = 0.0
        self.session_date: date | None = None

    # ---- session boundaries ---------------------------------------------------------------

    def start_session(self, day: date) -> None:
        """B holds nothing overnight, so a new session starts with an empty book."""
        if self.open_positions:
            raise RuntimeError(f"{sorted(self.open_positions)} still open when {day} started")
        self.session_date = day
        self.entered_today = set()
        self._entries_today = {}
        self.realized_r_today = 0.0

    # ---- entry ------------------------------------------------------------------------------

    def refuse_reason(self, symbol: str) -> DropReason | None:
        """The account-level refusal for a symbol the engine is allowed to ask about.

        Re-entry is not answered here. B-F0 3.2 says a symbol that reached ``ENTERED`` does not
        come back into the pool that day, so the engine must not open a candidate for it at
        all; ``enter`` refuses such a call as a contract violation rather than inventing a
        drop reason for it.
        """
        if self.realized_r_today <= -self.risk.daily_loss_limit_r:
            return DropReason.DAILY_LOSS_LIMIT
        if len(self.open_positions) >= self.risk.max_open_positions:
            return DropReason.MAX_POSITIONS
        return None

    def entries_today(self, symbol: str) -> int:
        return self._entries_today.get(symbol, 0)

    def may_be_a_candidate(self, symbol: str) -> bool:
        """False once the symbol has used up its entries for the day or is still open."""
        return (symbol not in self.open_positions
                and self.entries_today(symbol) < self.risk.max_entries_per_symbol)

    def enter(self, symbol: str, setup: SetupType, *, raw_price: float, initial_stop: float,
              at: datetime, entry_bar_timestamp: datetime) -> Trade | DropReason:
        """Fill at ``raw_price`` plus slippage, or say why the account refused."""
        if not self.may_be_a_candidate(symbol):
            raise ValueError(
                f"{symbol} already used its {self.risk.max_entries_per_symbol} entry today; "
                "the engine must not open a candidate for it")
        refusal = self.refuse_reason(symbol)
        if refusal is not None:
            return refusal
        price = self.costs.buy_price(raw_price)
        if price <= initial_stop:
            return DropReason.SIZE_ZERO
        decision = position_size(self.equity, price, initial_stop, self.risk)
        if decision.refusal is not None:
            return DropReason.SIZE_ZERO

        # Commission is charged on the pre-slippage reference price, as Strategy A's accounting
        # does (SimBroker._make_fill: raw * quantity * commission_bps). B-E0 V1
        # commission_notional_basis = PRE_SLIPPAGE_REFERENCE_PRICE; the rate is unchanged.
        fee = self.costs.fee(raw_price, decision.shares)
        self.equity -= fee
        assert self.session_date is not None
        trade = Trade(symbol=symbol, setup=setup, session_date=self.session_date, entered_at=at,
                      entry_bar_timestamp=entry_bar_timestamp, entry_price=price,
                      shares=decision.shares, initial_stop=initial_stop, entry_fee=fee)
        self.trades.append(trade)
        self._open_trades[symbol] = trade
        self.open_positions[symbol] = open_position(
            symbol, setup, entry_price=price, entered_at=at, shares=decision.shares,
            initial_stop=initial_stop, entry_bar_timestamp=entry_bar_timestamp)
        self.entered_today.add(symbol)
        self._entries_today[symbol] = self.entries_today(symbol) + 1
        return trade

    # ---- exits --------------------------------------------------------------------------------

    def book(self, symbol: str, position: Position, events: Sequence[ExitEvent]) -> Trade | None:
        """Record what ``advance_position`` decided and move the money."""
        trade = self._open_trades.get(symbol)
        if trade is None:
            raise KeyError(f"{symbol} has no open trade")
        for event in events:
            price = self.costs.sell_price(event.price)
            fee = self.costs.fee(event.price, event.shares)  # pre-slippage basis, as at entry
            trade.legs.append(TradeLeg(event.reason, event.at, price, event.shares, fee))
            self.equity += (price - trade.entry_price) * event.shares - fee
        if position.is_open:
            self.open_positions[symbol] = position
            return None
        self.open_positions.pop(symbol, None)
        self._open_trades.pop(symbol, None)
        self.realized_r_today += trade.realized_r
        return trade

    # ---- reporting -----------------------------------------------------------------------------

    @property
    def closed_trades(self) -> list[Trade]:
        return [trade for trade in self.trades if trade.is_closed]

    @property
    def total_return_pct(self) -> float:
        return (self.equity / self.starting_equity - 1) * 100

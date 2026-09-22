"""The execution equivalence gate: B's account against the A-compatible SimBroker, field by field.

B books its trades through ``app.backtest.strategy_b.portfolio`` (float, costs from
``CostModel``); Strategy A books through ``app.broker.sim.SimBroker`` (Decimal, costs from
``ExecutionConfig`` and the ``app.broker.accounting`` contract). The B-E0 contract says the two
cost assumptions are numerically identical. This module checks whether the *books* are: the same
entry intent, the same next bar, the same quantity and the same exit go into both, and every
money and price field that comes out is compared exactly, in Decimal.

Two comparison modes, fixed per field before the comparison runs (the B-E0 contract's
``execution_parity`` block pre-registers the same lists and numbers, and a test holds the two
equal):

* ``EXACT`` for discrete fields (acceptance, timestamps, quantities, position state): the values
  must be equal.
* ``NUMERIC_PARITY`` for money and price fields: B is float and A is Decimal, so the same
  arithmetic lands a few float ulps apart. A field passes when
  ``|a - b| <= max(ABS_TOL, REL_TOL * max(|a|, |b|))``, with REL_TOL = 1e-12 and ABS_TOL = 1e-12
  (only for values near zero). A different formula (a cost charged on another basis) is off
  by 1e-6 relative or more and fails.

The gate is PASS only when every field of every case passes. Order ids and wall-clock fields
are not compared, because they are not results.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.strategy_b.costs import CostModel
from app.backtest.strategy_b.portfolio import Portfolio
from app.backtest.strategy_b_e0.contract import Contract
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.exits import ExitEvent, ExitReason
from app.strategy_b.models import SetupType

VERSION = "b-e0-execution-differential-v1"
PASS, FAIL = "PASS", "FAIL"
EXACT, NUMERIC_PARITY = "EXACT", "NUMERIC_PARITY"
REL_TOL = Decimal("1e-12")
ABS_TOL = Decimal("1e-12")
T0 = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)

#: (label, symbol, next-bar open at entry, initial stop, next-bar open at exit). Fixed inputs,
#: chosen to cover a win, a loss and a trade bound by the position cap, not to favour either book.
CASES = (
    ("win", "AAA", 12.34, 11.90, 13.10),
    ("loss", "BBB", 5.07, 4.88, 4.90),
    ("position_cap", "CCC", 48.215, 47.60, 49.00),
)
FIELDS = ("accepted", "fill_timestamp_entry", "fill_price_entry", "quantity", "notional_entry",
          "commission_entry", "spread_plus_slippage_entry", "fx_cost_entry",
          "position_quantity", "position_average_price", "account_after_entry",
          "fill_timestamp_exit", "fill_price_exit", "commission_exit", "gross_pnl", "net_pnl",
          "net_r", "final_cash", "position_state_after_exit")
EXACT_FIELDS = ("accepted", "fill_timestamp_entry", "quantity", "position_quantity",
                "fill_timestamp_exit", "position_state_after_exit")
NUMERIC_FIELDS = tuple(name for name in FIELDS if name not in EXACT_FIELDS)


@dataclass(frozen=True, slots=True)
class Report:
    verdict: str
    cost_level: str
    rows: Mapping[str, Mapping[str, Mapping[str, object]]]

    @property
    def failing(self) -> dict[str, list[str]]:
        return {case: [f for f, row in fields.items() if not row["passed"]]
                for case, fields in self.rows.items() if any(not r["passed"] for r in fields.values())}

    def maxima(self) -> dict[str, str]:
        numeric = [row for fields in self.rows.values() for row in fields.values()
                   if row["comparison_mode"] == NUMERIC_PARITY]
        return {"max_absolute_difference": str(max(Decimal(r["absolute_difference"]) for r in numeric)),
                "max_relative_difference": str(max(Decimal(r["relative_difference"]) for r in numeric))}

    def as_dict(self) -> dict[str, object]:
        return {"version": VERSION, "verdict": self.verdict, "cost_level": self.cost_level,
                "policy": {"exact_fields": list(EXACT_FIELDS), "numeric_fields": list(NUMERIC_FIELDS),
                           "rel_tol": str(REL_TOL), "abs_tol": str(ABS_TOL),
                           "formula": "abs(a - b) <= max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))"},
                "cases_compared": len(self.rows), "fields_per_case": len(FIELDS),
                "failing_fields": self.failing, **self.maxima(), "cases": self.rows}


def a_config(contract: Contract, level_name: str):
    """The A ExecutionConfig for one contract cost level: A's spread, the rest as slippage."""
    from app.execution.config import ExecutionConfig

    level = contract.cost_level(level_name)
    reference = contract.raw["costs"]["strategy_a_reference"]
    spread = min(Decimal(str(reference["default_spread_bps"])),
                 Decimal(str(level.execution_cost_bps_per_side)))
    return ExecutionConfig(
        default_spread_bps=spread,
        default_slippage_bps=Decimal(str(level.execution_cost_bps_per_side)) - spread,
        commission_bps=Decimal(str(level.commission_bps_per_side)),
        fx_cost_bps=Decimal(str(reference["fx_cost_bps"])))


def run(contract: Contract, level_name: str, config: StrategyBConfig | None = None) -> Report:
    config = config or StrategyBConfig()
    rows = {label: _case(contract, level_name, config, *args) for label, *args in CASES}
    passed = all(row["passed"] for fields in rows.values() for row in fields.values())
    return Report(PASS if passed else FAIL, level_name, rows)


def _case(contract: Contract, level_name: str, config: StrategyBConfig, symbol: str,
          entry_open: float, stop: float, exit_open: float) -> dict[str, dict[str, object]]:
    from app.broker.sim import SimBroker
    from app.execution.domain import IntentType, OrderIntent, OrderSide
    from app.market.domain import MarketSession, MinuteBar

    level = contract.cost_level(level_name)
    entry_bar, exit_bar = T0 + timedelta(minutes=1), T0 + timedelta(minutes=10)

    # ---- B: exactly the calls the session engine makes -------------------------------------
    book = Portfolio(equity=float(contract.initial_capital_usd),
                     costs=CostModel(fee_bps_per_side=level.commission_bps_per_side,
                                     slippage_bps_per_side=level.execution_cost_bps_per_side),
                     risk=config.risk)
    book.start_session(T0.date())
    trade = book.enter(symbol, SetupType.HOD_BREAKOUT, raw_price=entry_open, initial_stop=stop,
                       at=entry_bar + timedelta(minutes=1), entry_bar_timestamp=entry_bar)
    position = book.open_positions[symbol]
    b_after_entry = book.equity
    book.book(symbol, replace(position, shares=0),
              [ExitEvent(ExitReason.TIME_STOP, exit_bar, exit_open, trade.shares, True)])
    leg = trade.legs[0]
    b = {
        "accepted": True, "fill_timestamp_entry": trade.entry_bar_timestamp,
        "fill_price_entry": trade.entry_price, "quantity": trade.shares,
        "notional_entry": trade.entry_price * trade.shares, "commission_entry": trade.entry_fee,
        "spread_plus_slippage_entry": (trade.entry_price - entry_open) * trade.shares,
        "fx_cost_entry": 0.0, "position_quantity": position.shares,
        "position_average_price": position.entry_price,
        # B keeps realised equity (cash plus open positions at cost); A's cash plus its position
        # at average cost is the same quantity, so that is what is compared.
        "account_after_entry": b_after_entry,
        "fill_timestamp_exit": leg.at, "fill_price_exit": leg.price, "commission_exit": leg.fee,
        "gross_pnl": trade.gross_pnl, "net_pnl": trade.net_pnl, "net_r": trade.realized_r,
        "final_cash": book.equity, "position_state_after_exit": "FLAT" if not book.open_positions
        else "OPEN",
    }

    # ---- A: the same quantity through SimBroker's next-bar fill -----------------------------
    broker = SimBroker(contract.initial_capital_usd, config=a_config(contract, level_name))
    quantity = Decimal(trade.shares)
    reference = Decimal(str(entry_open))
    risk = (Decimal(str(trade.entry_price)) - Decimal(str(stop))) * quantity

    def bar(at: datetime, price: float) -> MinuteBar:
        return MinuteBar(symbol=symbol, timestamp=at, open=price, high=price * 1.01,
                         low=price * 0.99, close=price, volume=1000, session=MarketSession.REGULAR,
                         observed_at=at + timedelta(minutes=1), available_at=at + timedelta(minutes=1))

    bars = [bar(T0, entry_open), bar(entry_bar, entry_open), bar(exit_bar, exit_open),
            bar(exit_bar + timedelta(minutes=1), exit_open)]

    def intent(side: OrderSide, as_of: datetime) -> OrderIntent:
        return OrderIntent(symbol, side, IntentType.BASE_ENTRY if side is OrderSide.BUY
                           else IntentType.EXIT, quantity, reference, quantity * reference,
                           quantity * reference, "USD", "USD", VERSION,
                           risk if side is OrderSide.BUY else Decimal("1"), Decimal(str(stop)),
                           as_of, as_of, VERSION)

    buy = broker.submit_order(intent(OrderSide.BUY, T0), bars)
    entry = broker.get_fills(buy.id)[0]
    held = broker.get_position(symbol)
    # SimPosition is mutated in place by the exit, so its entry state is read now.
    held_quantity, held_average = held.quantity, held.average_price
    a_after_entry = broker.cash + held_quantity * held_average
    sell = broker.submit_order(intent(OrderSide.SELL, exit_bar - timedelta(minutes=1)), bars)
    exit_fill = broker.get_fills(sell.id)[0]
    result = broker.get_trade(symbol)
    a = {
        "accepted": buy.status.value == "FILLED", "fill_timestamp_entry": entry.filled_at,
        "fill_price_entry": entry.fill_price, "quantity": entry.quantity,
        "notional_entry": entry.fill_price * entry.quantity, "commission_entry": entry.commission,
        "spread_plus_slippage_entry": entry.spread_cost + entry.slippage_cost,
        "fx_cost_entry": entry.fx_cost, "position_quantity": held_quantity,
        "position_average_price": held_average, "account_after_entry": a_after_entry,
        "fill_timestamp_exit": exit_fill.filled_at, "fill_price_exit": exit_fill.fill_price,
        "commission_exit": exit_fill.commission, "gross_pnl": result.gross_pnl,
        "net_pnl": result.net_pnl, "net_r": result.net_r, "final_cash": broker.cash,
        "position_state_after_exit": "FLAT" if broker.get_position(symbol) is None
        or broker.get_position(symbol).quantity == 0 else "OPEN",
    }
    return {name: compare(a[name], b[name], EXACT if name in EXACT_FIELDS else NUMERIC_PARITY)
            for name in FIELDS}


def _decimal(value: object) -> Decimal:
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def compare(a: object, b: object, mode: str) -> dict[str, object]:
    """One field, in its pre-registered mode. Floats enter Decimal through repr, exactly."""
    if mode == EXACT:
        if isinstance(a, (bool, str, datetime)) or isinstance(b, (bool, str, datetime)):
            same = type(a) is type(b) and a == b if isinstance(a, bool) else a == b
            left, right = str(a), str(b)
        else:
            left, right = str(_decimal(a)), str(_decimal(b))
            same = _decimal(a) == _decimal(b)
        return {"A": left, "B": right, "comparison_mode": EXACT, "passed": bool(same)}
    if mode != NUMERIC_PARITY:
        raise ValueError(f"unknown comparison mode {mode!r}")
    left, right = _decimal(a), _decimal(b)
    difference = abs(left - right)
    scale = max(abs(left), abs(right))
    allowed = max(ABS_TOL, REL_TOL * scale)
    relative = difference / scale if scale else Decimal(0)
    return {"A": str(left), "B": str(right), "absolute_difference": str(difference),
            "relative_difference": str(relative), "allowed_tolerance": str(allowed),
            "comparison_mode": NUMERIC_PARITY, "passed": difference <= allowed}

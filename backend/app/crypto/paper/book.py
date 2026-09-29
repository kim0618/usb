"""Executable-quote fill model.

Contract O2/O3/O4/O5: a market LONG lifts the ask side, a market SHORT hits the bid side,
neither ever fills at mid or last, and a size larger than the best level walks into the next
one. BTCUSDT's measured spread was exactly one tick for the whole D2 observation window, which
is precisely why filling at mid "because it is close" has to be structurally impossible here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


class NoLiquidity(RuntimeError):
    """The visible book cannot fill the requested size."""


@dataclass(frozen=True)
class BookSide:
    """Price levels already ordered the way they would be consumed."""
    levels: tuple[tuple[Decimal, Decimal], ...]

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[str]], *, descending: bool) -> "BookSide":
        parsed = [(Decimal(str(price)), Decimal(str(qty))) for price, qty in rows if Decimal(str(qty)) > 0]
        parsed.sort(key=lambda level: level[0], reverse=descending)
        return cls(tuple(parsed))

    @property
    def best(self) -> Decimal | None:
        return self.levels[0][0] if self.levels else None

    def depth(self, count: int) -> Decimal:
        return sum((qty for _, qty in self.levels[:count]), Decimal(0))

    @property
    def total_qty(self) -> Decimal:
        return sum((qty for _, qty in self.levels), Decimal(0))


@dataclass(frozen=True)
class Quote:
    """One market observation: the two book sides plus the exchange's own reference prices."""
    ts_ms: int
    bids: BookSide
    asks: BookSide
    mark_price: Decimal
    last_price: Decimal | None = None
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time_ms: int | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids.best

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks.best

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2


@dataclass(frozen=True)
class WalkResult:
    avg_price: Decimal
    filled_qty: Decimal
    levels_consumed: int
    worst_price: Decimal


def walk_book(side: BookSide, qty: Decimal) -> WalkResult:
    """Consume depth until `qty` is filled. Partial visible depth is an error, not a fill."""
    if qty <= 0:
        raise ValueError("quantity must be positive")
    remaining = qty
    notional = Decimal(0)
    consumed = 0
    worst = Decimal(0)
    for price, available in side.levels:
        take = available if available < remaining else remaining
        notional += price * take
        remaining -= take
        consumed += 1
        worst = price
        if remaining == 0:
            break
    if remaining > 0:
        raise NoLiquidity(f"visible depth {side.total_qty} cannot fill {qty}")
    return WalkResult(avg_price=notional / qty, filled_qty=qty, levels_consumed=consumed, worst_price=worst)

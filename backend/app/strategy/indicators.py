"""Point-in-time indicator calculations for minute bars."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.market.domain import MarketSession, MinuteBar


def available_regular_bars(bars: Sequence[MinuteBar], as_of: datetime) -> tuple[MinuteBar, ...]:
    return tuple(sorted((b for b in bars if b.session is MarketSession.REGULAR
                         and b.timestamp <= as_of and b.available_at <= as_of),
                        key=lambda b: b.timestamp))


def opening_range(bars: Sequence[MinuteBar], market_open: datetime,
                  minutes: int, as_of: datetime) -> tuple[Decimal, Decimal] | None:
    end = market_open.timestamp() + minutes * 60
    selected = [b for b in available_regular_bars(bars, as_of)
                if market_open <= b.timestamp and b.timestamp.timestamp() < end]
    if len({b.timestamp for b in selected}) < minutes:
        return None
    return Decimal(str(max(b.high for b in selected))), Decimal(str(min(b.low for b in selected)))


def session_vwap(bars: Sequence[MinuteBar], market_open: datetime,
                 as_of: datetime) -> Decimal | None:
    selected = [b for b in available_regular_bars(bars, as_of) if b.timestamp >= market_open]
    total_volume = sum(b.volume for b in selected)
    if total_volume <= 0:
        return None
    numerator = sum((Decimal(str((b.high + b.low + b.close) / 3)) * b.volume for b in selected), Decimal("0"))
    return numerator / Decimal(total_volume)


def atr_sma(bars: Sequence[MinuteBar], period: int, as_of: datetime) -> Decimal | None:
    selected = list(available_regular_bars(bars, as_of))
    if selected:
        market_tz = ZoneInfo("America/New_York")
        session_day = selected[-1].timestamp.astimezone(market_tz).date()
        selected = [bar for bar in selected if bar.timestamp.astimezone(market_tz).date() == session_day]
    if len(selected) < period + 1:
        return None
    trs: list[Decimal] = []
    for previous, current in zip(selected[-period - 1:-1], selected[-period:]):
        high, low, prev_close = map(Decimal, map(str, (current.high, current.low, previous.close)))
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs, Decimal("0")) / Decimal(period)


def closing_strength(bars: Sequence[MinuteBar], market_open: datetime,
                     as_of: datetime, current_price: Decimal) -> Decimal:
    """PIT-safe regular-session closing strength through ``as_of`` only."""
    selected = [bar for bar in available_regular_bars(bars, as_of) if bar.timestamp >= market_open]
    if not selected:
        return Decimal("0")
    low = Decimal(str(min(bar.low for bar in selected)))
    high = Decimal(str(max(bar.high for bar in selected)))
    return (current_price - low) / (high - low) if high > low else Decimal("0")

"""Incremental 04:00-09:24 premarket state from Kiwoom FE (realtime trade) events.

Measured FE semantics (probes 2026-09-17 / 09-22): FID 13 is the business-day cumulative volume and
resets at the premarket start, its delta equals FID 15; the event time is minute resolution; FIDs
16-18 are fixed during premarket, so the premarket high / low must be accumulated from FID 10 prices.

The state reproduces the frozen premarket block at the 09:24 cutoff (``decision_last`` = 09:24):
an event stamped 09:25 or later never enters. A feed gap inside [04:00, 09:25), or a subscription
that began after 04:00, makes the symbol FEATURE_CONTEXT_INCOMPLETE: missed prints could have set the
high or low, and nothing is filled with zero. Volume survives gaps (FID 13 is cumulative), prices do not.

Semantic differences from the Massive development block, recorded rather than hidden: dollar volume is
sum(trade volume x trade price) instead of sum(bar volume x bar VWAP); Kiwoom volume differs from the
consolidated tape (see the capability manifest).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

PREMARKET_START = 4 * 60
CUTOFF_LAST_MINUTE = 9 * 60 + 24
WINDOW_0900 = 9 * 60
INCOMPLETE = "FEATURE_CONTEXT_INCOMPLETE"


def minute_of(hhmm: str) -> int:
    text = hhmm.replace(":", "")
    return int(text[:2]) * 60 + int(text[2:4])


@dataclass
class SymbolPremarket:
    symbol: str
    session: date
    subscribed_from: int                      # ET minute the subscription was live
    last_price: float | None = None
    high: float | None = None
    low: float | None = None
    first_price: float | None = None
    cum_volume: float = 0.0
    dollar_volume: float = 0.0
    open_0900: float | None = None
    minutes_0900: set[int] = field(default_factory=set)
    last_minute: int | None = None
    seen: set[tuple[int, float]] = field(default_factory=set)
    duplicates: int = 0
    stale: int = 0

    def add(self, minute: int, price: float, cum_volume: float, trade_volume: float | None) -> str:
        if minute < PREMARKET_START or minute > CUTOFF_LAST_MINUTE:
            return "OUTSIDE_WINDOW"
        key = (minute, cum_volume)
        if key in self.seen:
            self.duplicates += 1
            return "DUPLICATE"
        if cum_volume < self.cum_volume or (self.last_minute is not None and minute < self.last_minute):
            self.stale += 1                    # an older snapshot arriving late; cumulative never goes back
            return "STALE"
        self.seen.add(key)
        delta = cum_volume - self.cum_volume
        self.cum_volume = cum_volume
        self.dollar_volume += delta * price
        self.first_price = price if self.first_price is None else self.first_price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.last_price = price
        self.last_minute = minute
        if minute >= WINDOW_0900:
            if self.open_0900 is None:
                self.open_0900 = price
            self.minutes_0900.add(minute)
        return "OK"


@dataclass
class PremarketBook:
    session: date
    symbols: dict[str, SymbolPremarket] = field(default_factory=dict)
    gaps: list[tuple[int, int]] = field(default_factory=list)      # (disconnect, reconnect) ET minutes

    def subscribe(self, symbol: str, from_minute: int) -> None:
        self.symbols.setdefault(symbol, SymbolPremarket(symbol, self.session, from_minute))

    def event(self, symbol: str, hhmm: str, price: float, cum_volume: float, trade_volume: float | None = None) -> str:
        state = self.symbols.get(symbol)
        if state is None:
            return "NOT_SUBSCRIBED"
        return state.add(minute_of(hhmm), price, cum_volume, trade_volume)

    def disconnected(self, start_minute: int, end_minute: int) -> None:
        self.gaps.append((start_minute, end_minute))

    def features(self, symbol: str, close_previous: float) -> dict:
        """The 09:24-cutoff inputs H5 needs, or an INCOMPLETE status with its reasons."""
        s = self.symbols[symbol]
        reasons = []
        if s.subscribed_from > PREMARKET_START:
            reasons.append(f"subscribed from {s.subscribed_from // 60:02d}:{s.subscribed_from % 60:02d}, after 04:00")
        for a, b in self.gaps:
            if a <= CUTOFF_LAST_MINUTE and b >= PREMARKET_START:
                reasons.append(f"feed gap {a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d} inside the premarket")
        if reasons:
            return {"symbol": symbol, "status": INCOMPLETE, "reasons": reasons}
        if s.last_price is None:
            return {"symbol": symbol, "status": "NO_PREMARKET_PRINT"}     # data: no premarket row (as in E1)
        span = s.high - s.low
        return {"symbol": symbol, "status": "OK",
                "premarket_gap": s.last_price / close_previous - 1.0 if close_previous > 0 else float("nan"),
                "position_in_premarket_range": (s.last_price - s.low) / span if span > 0 else float("nan"),
                "return_0900_0925": (s.last_price / s.open_0900 - 1.0) if s.open_0900 and len(s.minutes_0900) >= 2
                else float("nan"),
                "pm_volume": s.cum_volume, "pm_dollar_volume": s.dollar_volume,
                "pm_high": s.high, "pm_low": s.low, "pm_last_price": s.last_price,
                "duplicates": s.duplicates, "stale": s.stale}

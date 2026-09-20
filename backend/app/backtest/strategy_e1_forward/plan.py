"""What a forward session costs, in provider calls, bytes and elapsed time.

Everything here is arithmetic over measured quantities. Nothing in this module fetches, and the
numbers it produces are the ones the protocol document quotes, so the two cannot drift.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Stocks Basic, from the client: five calls a minute, and the v2 fetcher spaces at 13 s.
BASIC_CALLS_PER_MINUTE = 5
FETCHER_SPACING_SECONDS = 13.0

#: Measured on 2026-04-20..2026-09-16, the only window with a broad minute tape.
MEASURED = {
    "daily_eligible_rows_per_session": 2557,
    "daily_eligible_symbols_in_window": 2936,
    "covered_symbols_in_window": 1612,
    "premarket_eligible_rows_per_session_at_current_coverage": 576.9,
    "h5_rows_per_session_at_current_coverage": 14.60,
    "h5_rate_among_premarket_eligible": 0.0253,
    "premarket_bars_per_symbol_session": 35.5,
    "gzip_bytes_per_bar": 20,
}
#: Bars 09:30-09:34 needed for the primary label, on top of the premarket window.
LABEL_BARS_PER_SYMBOL_SESSION = 6


def projected_h5_per_session(symbols_covered: int) -> float:
    """H5 observations per session if the minute tape covers ``symbols_covered`` symbols."""
    per_covered_symbol = (MEASURED["premarket_eligible_rows_per_session_at_current_coverage"]
                          / MEASURED["covered_symbols_in_window"])
    premarket_rows = per_covered_symbol * symbols_covered
    return premarket_rows * MEASURED["h5_rate_among_premarket_eligible"]


@dataclass(frozen=True)
class SessionPlan:
    symbols: int
    minute_calls: int
    daily_calls: int
    reference_calls: int
    splits_calls: int

    @property
    def total_calls(self) -> int:
        return self.minute_calls + self.daily_calls + self.reference_calls + self.splits_calls

    @property
    def hours_at_basic_limit(self) -> float:
        return self.total_calls / BASIC_CALLS_PER_MINUTE / 60.0

    @property
    def hours_at_fetcher_spacing(self) -> float:
        return self.total_calls * FETCHER_SPACING_SECONDS / 3600.0

    def bytes_estimate(self) -> int:
        bars = self.symbols * (MEASURED["premarket_bars_per_symbol_session"]
                               + LABEL_BARS_PER_SYMBOL_SESSION)
        return int(bars * MEASURED["gzip_bytes_per_bar"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "calls": {"minute": self.minute_calls, "grouped_daily": self.daily_calls,
                      "reference": self.reference_calls, "splits": self.splits_calls,
                      "total": self.total_calls},
            "hours_at_basic_limit_5_per_minute": round(self.hours_at_basic_limit, 2),
            "hours_at_fetcher_spacing_13s": round(self.hours_at_fetcher_spacing, 2),
            "bytes_per_session": self.bytes_estimate(),
            "megabytes_per_session": round(self.bytes_estimate() / 1e6, 2),
            "megabytes_per_month_21_sessions": round(self.bytes_estimate() * 21 / 1e6, 1),
            "projected_h5_rows_per_session": round(projected_h5_per_session(self.symbols), 2),
        }


def session_plan(symbols: int, *, reference_pages: int = 6) -> SessionPlan:
    """One forward session: a minute call per symbol, plus the three market-wide calls.

    The minute window requested is 04:00-09:35 ET rather than the 04:00-20:00 the historical
    collector uses, because that is all H5 reads. It is the same endpoint and the same authority,
    so a shared forward tape would still serve any later study that wants the rest of the day - it
    would simply have to ask for the rest of the day.
    """
    return SessionPlan(symbols=symbols, minute_calls=symbols, daily_calls=1,
                       reference_calls=reference_pages, splits_calls=1)


def accumulation(symbols: int, checkpoints=(250, 500, 1000, 2000)) -> dict[str, Any]:
    per_session = projected_h5_per_session(symbols)
    return {
        "symbols_covered": symbols,
        "h5_rows_per_session": round(per_session, 2),
        "sessions_to": {str(c): round(c / per_session, 1) for c in checkpoints},
        "months_to": {str(c): round(c / per_session / 21, 1) for c in checkpoints},
    }

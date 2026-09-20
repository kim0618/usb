"""Keys, statuses and failure codes of Strategy D V2-A.

The exception hierarchy is V1's, imported rather than redefined: a V2 run that breaks a
point-in-time contract must raise the same ``PointInTimeViolation`` a V1 run would, so one
audit can read both. What is new here is the vocabulary the structure coordinates need - why a
``(session, ticker)`` window has no vector - which V1 never had to express, because a close
path is either long enough or it is not.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from app.backtest.strategy_d_analog.models import (  # noqa: F401  (re-exported on purpose)
    DAILY_AUTHORITY, REASON_ORDER, FreezeIdentity, HardFail, IneligibleReason,
    PointInTimeViolation, RulesChanged, SessionGrid, SymbolIdentity,
)

STRATEGY_ID = "MARKET_STRUCTURE_ANALOG_V2"


class VectorStatus(str, Enum):
    """Why a window does or does not carry a structure vector (first match wins, so a count
    over these values partitions the panel)."""

    OK = "OK"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INCOMPLETE_BAR = "INCOMPLETE_BAR"
    ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
    NON_FINITE = "NON_FINITE"


#: Evaluation order of the vector rule. ``NOT_ELIGIBLE`` first so the universe histogram and the
#: vector histogram never double-count the same ticker-date.
VECTOR_STATUS_ORDER: tuple[VectorStatus, ...] = (
    VectorStatus.NOT_ELIGIBLE, VectorStatus.INSUFFICIENT_HISTORY, VectorStatus.INCOMPLETE_BAR,
    VectorStatus.ZERO_DENOMINATOR, VectorStatus.NON_FINITE,
)


class LibraryExclusion(str, Enum):
    """Why an eligible stride window is not a library row for the primary horizon."""

    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    VECTOR_UNDEFINED = "VECTOR_UNDEFINED"
    LABEL_INVALID = "LABEL_INVALID"


@dataclass(frozen=True)
class FeatureSpec:
    """One declared coordinate: what it is called, what it computes, how far back it reads."""

    index: int
    name: str
    family: str
    formula: str
    lookback: int
    undefined: str


@dataclass(frozen=True)
class SampleGate:
    """The S1 thresholds, read from the declaration and never from a literal in code."""

    min_evaluable_dates: int
    min_valid_queries: int
    min_unique_tickers: int
    max_insufficient_neighbor_share: float
    max_vector_undefined_share: float

    def as_dict(self) -> dict[str, float | int]:
        return {"min_evaluable_dates": self.min_evaluable_dates,
                "min_valid_queries": self.min_valid_queries,
                "min_unique_tickers": self.min_unique_tickers,
                "max_insufficient_neighbor_share": self.max_insufficient_neighbor_share,
                "max_vector_undefined_share": self.max_vector_undefined_share}


def assert_names(found: Sequence[str], expected: Sequence[str], where: str) -> None:
    """A declaration whose coordinate list has drifted from the code is R1, never a warning."""
    if tuple(found) != tuple(expected):
        raise HardFail("R1", f"{where}: {tuple(found)} != {tuple(expected)}")

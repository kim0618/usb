"""Logical keys, identities and failure codes of Strategy D, per D1 Pre-flight Contract V1 §6, §11.

The split between the two failure kinds is the contract's, not a style choice: a NORMAL
INELIGIBLE outcome is what data and rules produce on their own and is counted by reason; a
``HardFail`` can only happen when code or input breaks a declared contract, and it stops the run
so that a bug is never hidden behind a skip counter.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

#: The one daily price authority of the common store; ``per_symbol_daily`` never fills a gap.
DAILY_AUTHORITY = "MASSIVE_GROUPED_DAILY"
REPRESENTATIONS = ("A", "B")


class HardFail(RuntimeError):
    """A declared contract was broken (R1~R12, F1~F5). The run stops without a COMPLETE artifact."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class PointInTimeViolation(HardFail):
    """R4: a read of a session row after the as-of index of the view that served it."""

    def __init__(self, message: str) -> None:
        super().__init__("R4", message)


class RulesChanged(HardFail):
    """R1: the declaration file no longer hashes to the checksum declared before any D result."""

    def __init__(self, message: str) -> None:
        super().__init__("R1", message)


class IneligibleReason(str, Enum):
    """Normal exclusions (D1 Pre-flight §11.2). Counted, never fatal."""

    NOT_MEMBER = "NOT_MEMBER"
    NO_HISTORY = "NO_HISTORY"
    LOW_PRICE = "LOW_PRICE"
    LOW_ADV = "LOW_ADV"
    SPLIT_WINDOW = "SPLIT_WINDOW"
    CA_SUSPECT = "CA_SUSPECT"


#: Evaluation order of the universe rule (D2 design §6.2 table order). First match wins, so the
#: reason histogram is a partition of the ineligible ticker-dates and the counts add up.
REASON_ORDER = (IneligibleReason.NOT_MEMBER, IneligibleReason.NO_HISTORY, IneligibleReason.LOW_PRICE,
                IneligibleReason.LOW_ADV, IneligibleReason.SPLIT_WINDOW, IneligibleReason.CA_SUSPECT)


class LibraryExclusion(str, Enum):
    """Why a stride window is not a library row of a given test (D2 design §18 R14). Counted."""

    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    VECTOR_UNDEFINED = "VECTOR_UNDEFINED"
    LABEL_INVALID = "LABEL_INVALID"


class QueryStatus(str, Enum):
    """The outcome of one sampled query under one test (D2 design §12.1, §18 R15~R16)."""

    OK = "OK"
    VECTOR_UNDEFINED = "VECTOR_UNDEFINED"
    INSUFFICIENT_NEIGHBORS = "INSUFFICIENT_NEIGHBORS"


class LabelInvalidReason(str, Enum):
    """Why a window has no usable label for a horizon. D2 reads validity only, never a value."""

    NO_ENTRY_BAR = "NO_ENTRY_BAR"
    MISSING_HORIZON_BAR = "MISSING_HORIZON_BAR"
    LABEL_CA_SUSPECT = "LABEL_CA_SUSPECT"


@dataclass(frozen=True)
class SessionGrid:
    """The canonical session grid: usable GROUPED_DAILY sessions of one frozen dataset, ascending.

    Every D session offset (window ``e-W..e``, label ``d+1..d+h``, embargo, stride ``d % 5``,
    ``eval_start_idx``) is an index into this tuple. Calendar-day arithmetic is never used.
    """

    dates: tuple[date, ...]
    digest: str

    def __len__(self) -> int:
        return len(self.dates)

    def index_of(self, session: date) -> int:
        try:
            return self.dates.index(session)
        except ValueError as exc:
            raise HardFail("R3", f"session {session.isoformat()} is not on the grid") from exc

    def session(self, index: int) -> date:
        return self.dates[index]


@dataclass(frozen=True)
class FreezeIdentity:
    """What dataset a D run is bound to (D1 Pre-flight §9.1). Equality is the D2~D4 admission test."""

    snapshot_id: str
    snapshot_sha256: str
    freeze_id: str
    freeze_digest: str
    source_digest: str
    d_read_digest: str
    daily_authority: str
    first_session: str
    last_session: str
    session_count: int
    grid_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "snapshot_sha256": self.snapshot_sha256,
                "freeze_id": self.freeze_id, "freeze_digest": self.freeze_digest,
                "source_digest": self.source_digest, "d_read_digest": self.d_read_digest,
                "daily_authority": self.daily_authority, "first_session": self.first_session,
                "last_session": self.last_session, "session_count": self.session_count,
                "grid_digest": self.grid_digest}

    def assert_matches(self, expected: "FreezeIdentity") -> None:
        for name, found in self.as_dict().items():
            want = expected.as_dict()[name]
            if found != want:
                raise HardFail("R2", f"freeze identity {name}: {found!r} != expected {want!r}")


@dataclass(frozen=True)
class SymbolIdentity:
    """``(ticker, composite_figi)`` as of the latest snapshot on or before a session."""

    ticker: str
    composite_figi: str | None
    as_of_snapshot: date | None


@dataclass(frozen=True)
class PatternKey:
    """One window. ``end_idx`` is a grid index; ``end_date`` is a human-readable attribute of it."""

    ticker: str
    end_idx: int
    window: int


@dataclass(frozen=True)
class TestId:
    """One of the 14 primary tests. The metric follows from the representation (A -> Pearson)."""

    window: int
    horizon: int
    representation: str

    def __post_init__(self) -> None:
        if self.representation not in REPRESENTATIONS:
            raise HardFail("R1", f"representation {self.representation!r} is not A or B")

    @property
    def name(self) -> str:
        return f"W{self.window}_H{self.horizon}_{self.representation}"


@dataclass(frozen=True)
class QuerySampleKey:
    """A sampled query, common to all 14 tests: the sample is drawn before labels are known."""

    query_date_idx: int
    ticker: str


def frozen_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A plain ``dict`` copy for JSON serialization, so no artifact carries a live object."""
    return dict(payload)

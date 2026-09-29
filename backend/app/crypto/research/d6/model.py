"""Inputs and outputs of the FDN-V1 decision engine.

Everything the engine needs is passed in. There is no clock call, no file read and no account
lookup inside a decision, which is what makes the same input reproduce the same bytes and what
keeps AUTO research code from reaching into the manual paper account.

`StrategyState` carries a few numbers that originate as PnL (the daily guard, the loss streak).
The engine never computes them; it reads what the caller supplies and compares against the
contract. D6-B computes no PnL of any kind.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

HISTORICAL = "HISTORICAL"
REALTIME = "REALTIME"
MODES = (HISTORICAL, REALTIME)

LONG = "LONG"
SHORT = "SHORT"
HOLD = "HOLD"

PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUATED = "NOT_EVALUATED"

#: What the engine emits instead of an order. D6-B never builds an order object.
ENTRY_INTENT_LONG = "ENTRY_INTENT_LONG"
NO_ENTRY_INTENT = "NO_ENTRY_INTENT"
HOLD_POSITION = "HOLD_POSITION"
EXIT_INTENT = "EXIT_INTENT"


@dataclass(frozen=True)
class BarWindow:
    """Bars up to and including the decision bar t, ascending, gap-free on the 1m grid.

    The last row is bar t. Nothing after t exists in this object, which is how lookahead is made
    structurally impossible rather than merely tested for.
    """

    ts_ms: np.ndarray           # bar open times
    close: np.ndarray           # perp close
    index_close: np.ndarray     # index close
    oi: np.ndarray              # open interest already lagged to what was known at each bar close

    def __post_init__(self) -> None:
        n = len(self.ts_ms)
        for name in ("close", "index_close", "oi"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"BarWindow.{name} length {len(getattr(self, name))} != {n}")
        if n == 0:
            raise ValueError("BarWindow is empty")

    @property
    def decision_ts_ms(self) -> int:
        """Open time of bar t."""
        return int(self.ts_ms[-1])

    @property
    def bar_close_ms(self) -> int:
        """When bar t closed, which is when the decision may first be made."""
        return int(self.ts_ms[-1]) + 60_000


@dataclass(frozen=True)
class MarketContext:
    """Point-in-time facts about the decision instant that are not features."""

    now_ms: int                                 # the instant the decision is made
    oi_record_ts_ms: int | None = None          # stamp of the newest OI record in use
    next_funding_ts_ms: int | None = None       # next settlement, derived from past settlements
    bid: float | None = None                    # realtime only
    ask: float | None = None                    # realtime only
    mark: float | None = None
    entry_reference_price: float | None = None  # open[t+1] in replay; ask in realtime
    safe_max_qty: float | None = None           # supplied by the caller, never fetched here


@dataclass(frozen=True)
class StrategyState:
    """AUTO-side state. Never read from, or written to, the manual paper account."""

    position_open: bool = False
    last_exit_ts_ms: int | None = None
    day_realized_pnl_pct: float = 0.0        # supplied by the caller; not computed here
    day_utc: str | None = None
    consecutive_losses: int = 0
    last_loss_ts_ms: int | None = None
    emergency_stop: str | None = None        # trigger name, e.g. LEDGER_DIVERGENCE
    equity: float | None = None


@dataclass(frozen=True)
class VirtualPosition:
    """A position handed to the exit evaluator. Holds no PnL and no ledger reference."""

    side: str
    entry_ts_ms: int
    entry_price: float
    qty: float
    stop_price: float


@dataclass(frozen=True)
class FeatureValues:
    f_basis: float
    f_oi1h: float
    f_drop1h: float
    f_rv24h: float

    def as_dict(self) -> dict[str, float]:
        return {"f_basis": self.f_basis, "f_oi1h": self.f_oi1h,
                "f_drop1h": self.f_drop1h, "f_rv24h": self.f_rv24h}

    def any_nan(self) -> bool:
        return any(not np.isfinite(v) for v in self.as_dict().values())


@dataclass(frozen=True)
class BucketAssignment:
    """B1..B5 for one feature, or None when the 30 day window was too sparse to form cutoffs."""

    bucket: int | None
    cutoffs: tuple[float, ...] | None
    valid_fraction: float

    @property
    def label(self) -> str | None:
        return None if self.bucket is None else f"B{self.bucket}"


@dataclass(frozen=True)
class FilterResult:
    filter_id: str
    name: str
    status: str
    reason: str
    observed: Any = None
    threshold: Any = None

    @property
    def blocking(self) -> bool:
        return self.status == FAIL

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.filter_id, "name": self.name, "status": self.status,
                "reason": self.reason, "observed": self.observed, "threshold": self.threshold}


@dataclass(frozen=True)
class CategoryScore:
    name: str
    input: str
    points: int
    max_points: int
    level: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "input": self.input, "points": self.points,
                "max": self.max_points, "level": self.level, "detail": self.detail}


@dataclass(frozen=True)
class SizingResult:
    """Every step kept separate so a cap can be told apart from a small signal."""

    stop_distance: float
    stop_price: float | None
    theoretical_notional: float
    risk_limited_notional: float
    safe_max_cap_qty: float | None
    final_qty: float
    final_notional: float
    leverage: float
    feasible: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"stop_distance": self.stop_distance, "stop_price": self.stop_price,
                "theoretical_notional": self.theoretical_notional,
                "risk_limited_notional": self.risk_limited_notional,
                "safe_max_cap_qty": self.safe_max_cap_qty, "final_qty": self.final_qty,
                "final_notional": self.final_notional, "leverage": self.leverage,
                "feasible": self.feasible, "reason": self.reason}


@dataclass(frozen=True)
class ExitEvaluation:
    action: str
    reason: str
    held_minutes: float
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "reason": self.reason,
                "held_minutes": self.held_minutes, "detail": self.detail}

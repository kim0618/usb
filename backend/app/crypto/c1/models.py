"""What a C1 signal and its shadow trade are, and how they are written down.

Every record carries its own schema version and the sha256 of the contract it was produced under,
so a ledger read back in six months says which definition made it. `to_json` / `from_json` are
plain dicts on purpose: the store is append-only JSONL that a person can read.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .contract import (
    C1X_CONTRACT_SHA256, C1X_MEANING, C1X_SCHEMA_VERSION, CONTRACT_SHA256, DIRECTION,
    OFFICIAL_HORIZON_MIN, SCHEMA_VERSION, STRATEGY,
)
from .features import NAN, is_nan


def clean_json(value: Any) -> Any:
    """JSON has no NaN. A missing number is written as null, which round-trips back to NaN."""
    if isinstance(value, float) and is_nan(value):
        return None
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return value


def _number(value: Any) -> float:
    return NAN if value is None else float(value)


@dataclass(frozen=True)
class FeatureSnapshot:
    """The three conditions and everything they were computed from.

    Kept whole, including the inputs that were present when the signal did *not* fire, so a later
    threshold study has the distribution and not only the crossings. `liquidation_*` is recorded
    for completeness and is **not** a C1 input: D5.2 section 0 rule Z3 excludes liquidations from
    the contract because their history is forward-only.
    """
    s1: float = NAN
    s1_bucket: int = -1
    s1_b1_cutoff: float = NAN
    s1_boundary_ms: int = 0
    perp_5m_close: float = NAN
    spot_5m_close: float = NAN
    oi_change_1h: float = NAN
    oi_now: float = NAN
    oi_lagged: float = NAN
    rv_24h: float = NAN
    vol_regime: str = "UNKNOWN"
    vol_high_cutoff: float = NAN
    condition_basis_b1: bool = False
    condition_oi_falling: bool = False
    condition_vol_high: bool = False
    liquidation_context: str = "NOT_A_C1_INPUT_D5_2_Z3"
    liquidation_available: bool = False

    def to_json(self) -> dict[str, Any]:
        return clean_json(asdict(self))

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "FeatureSnapshot":
        known = {key: raw.get(key) for key in cls.__dataclass_fields__ if key in raw}
        for key in ("s1", "s1_b1_cutoff", "perp_5m_close", "spot_5m_close", "oi_change_1h",
                    "oi_now", "oi_lagged", "rv_24h", "vol_high_cutoff"):
            if key in known:
                known[key] = _number(known[key])
        return cls(**known)


@dataclass(frozen=True)
class Completeness:
    """Why a bar was or was not decidable. `missing` names the inputs that were absent; an
    incomplete bar is NOT_ELIGIBLE and is never reported as a false condition."""
    eligible: bool = True
    missing: tuple[str, ...] = ()
    source_timestamps: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "missing": list(self.missing),
                "source_timestamps": dict(self.source_timestamps)}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Completeness":
        return cls(eligible=bool(raw.get("eligible", True)),
                   missing=tuple(raw.get("missing") or ()),
                   source_timestamps={k: int(v) for k, v in (raw.get("source_timestamps") or {}).items()})


@dataclass
class Signal:
    """One C1 event.

    `triggered_at_ms` is the close of the 1m bar whose inputs satisfied the three conditions - the
    study's decision instant - and `signal_price` is that bar's close. The trade the study
    measures starts one bar later, so `official_entry_price` is the *next* bar's open and stays
    None until that bar exists.
    """
    signal_id: str
    triggered_at_ms: int
    signal_bar_ms: int
    signal_price: float
    official_entry_at_ms: int
    planned_exit_at_ms: int
    state: str = "TRIGGERED"
    official_entry_price: float | None = None
    strategy: str = STRATEGY
    direction: str = DIRECTION
    horizon_min: int = OFFICIAL_HORIZON_MIN
    features: FeatureSnapshot = field(default_factory=FeatureSnapshot)
    completeness: Completeness = field(default_factory=Completeness)
    schema_version: str = SCHEMA_VERSION
    contract_sha256: str = CONTRACT_SHA256

    def to_json(self) -> dict[str, Any]:
        return clean_json({
            "signal_id": self.signal_id, "strategy": self.strategy, "direction": self.direction,
            "state": self.state, "triggered_at_ms": self.triggered_at_ms,
            "signal_bar_ms": self.signal_bar_ms, "signal_price": self.signal_price,
            "official_entry_at_ms": self.official_entry_at_ms,
            "official_entry_price": self.official_entry_price,
            "planned_exit_at_ms": self.planned_exit_at_ms, "horizon_min": self.horizon_min,
            "features": self.features.to_json(), "completeness": self.completeness.to_json(),
            "schema_version": self.schema_version, "contract_sha256": self.contract_sha256,
        })

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Signal":
        entry = raw.get("official_entry_price")
        return cls(
            signal_id=raw["signal_id"], triggered_at_ms=int(raw["triggered_at_ms"]),
            signal_bar_ms=int(raw["signal_bar_ms"]), signal_price=_number(raw.get("signal_price")),
            official_entry_at_ms=int(raw["official_entry_at_ms"]),
            planned_exit_at_ms=int(raw["planned_exit_at_ms"]),
            state=raw.get("state", "TRIGGERED"),
            official_entry_price=None if entry is None else float(entry),
            strategy=raw.get("strategy", STRATEGY), direction=raw.get("direction", DIRECTION),
            horizon_min=int(raw.get("horizon_min", OFFICIAL_HORIZON_MIN)),
            features=FeatureSnapshot.from_json(raw.get("features") or {}),
            completeness=Completeness.from_json(raw.get("completeness") or {}),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
            contract_sha256=raw.get("contract_sha256", CONTRACT_SHA256),
        )


@dataclass
class ShadowTrade:
    """The official 4 h paper result of a signal, plus observations at other horizons.

    `net_return` is the only figure that is C1's performance. The `observations` map is research
    material for a later holding-period question and is deliberately kept out of every total.
    """
    signal_id: str
    direction: str = DIRECTION
    entry_at_ms: int = 0
    entry_price: float = NAN
    exit_at_ms: int = 0
    exit_price: float | None = None
    gross_return: float | None = None
    cost: float | None = None
    net_return: float | None = None
    funding_paid: float = 0.0
    mfe: float | None = None
    mae: float | None = None
    observations: dict[str, float | None] = field(default_factory=dict)
    status: str = "OPEN"
    settled_from: str | None = None
    horizon_min: int = OFFICIAL_HORIZON_MIN
    schema_version: str = SCHEMA_VERSION
    contract_sha256: str = CONTRACT_SHA256

    def to_json(self) -> dict[str, Any]:
        return clean_json({
            "signal_id": self.signal_id, "direction": self.direction, "status": self.status,
            "entry_at_ms": self.entry_at_ms, "entry_price": self.entry_price,
            "exit_at_ms": self.exit_at_ms, "exit_price": self.exit_price,
            "gross_return": self.gross_return, "cost": self.cost, "net_return": self.net_return,
            "funding_paid": self.funding_paid, "mfe": self.mfe, "mae": self.mae,
            "observations": self.observations, "horizon_min": self.horizon_min,
            "settled_from": self.settled_from, "schema_version": self.schema_version,
            "contract_sha256": self.contract_sha256,
        })

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ShadowTrade":
        optional = lambda key: None if raw.get(key) is None else float(raw[key])
        return cls(
            signal_id=raw["signal_id"], direction=raw.get("direction", DIRECTION),
            entry_at_ms=int(raw.get("entry_at_ms") or 0), entry_price=_number(raw.get("entry_price")),
            exit_at_ms=int(raw.get("exit_at_ms") or 0), exit_price=optional("exit_price"),
            gross_return=optional("gross_return"), cost=optional("cost"),
            net_return=optional("net_return"), funding_paid=float(raw.get("funding_paid") or 0.0),
            mfe=optional("mfe"), mae=optional("mae"),
            observations={key: (None if value is None else float(value))
                          for key, value in (raw.get("observations") or {}).items()},
            status=raw.get("status", "OPEN"), settled_from=raw.get("settled_from"),
            horizon_min=int(raw.get("horizon_min", OFFICIAL_HORIZON_MIN)),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
            contract_sha256=raw.get("contract_sha256", CONTRACT_SHA256),
        )


@dataclass(frozen=True)
class Attribution:
    """A link an operator declared between one of their own trades and a C1 signal.

    `source` is always USER_DECLARED. Nothing in this package guesses that a manual trade was
    taken because of a signal; proximity in time is not evidence.
    """
    signal_id: str
    account: str
    trade_ref: str
    declared_at_ms: int
    source: str = "USER_DECLARED"
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return clean_json(asdict(self))

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Attribution":
        return cls(signal_id=raw["signal_id"], account=raw["account"], trade_ref=raw["trade_ref"],
                   declared_at_ms=int(raw["declared_at_ms"]),
                   source=raw.get("source", "USER_DECLARED"), note=raw.get("note", ""))


@dataclass
class C1xEvent:
    """The premium-normalization diagnostic for one C1 signal. At most one per signal.

    Every field that carries a price or a return of the diagnostic itself is named
    `*_if_exited` or `hypothetical_*`, because none of it was traded. `e0_net` is the fixed 4 h
    benchmark this package really does keep, and `delta_net` is the paired difference, which
    stays None while the benchmark is still running.
    """
    signal_id: str
    # Explicit persisted parent link. ``signal_id`` remains as a compatibility key for the
    # existing ledger; both identify the same C1 and are validated on load.
    parent_signal_id: str | None = None
    status: str = "NOT_TRIGGERED"
    confirmation_count: int = 0
    entry_at_ms: int = 0
    entry_price: float = NAN
    triggered_at_ms: int | None = None
    observed_at_ms: int | None = None
    executable_at_ms: int | None = None
    observed_price: float | None = None
    premium_value: float = NAN
    premium_bucket: int = -1
    premium_boundary: float = NAN
    hypothetical_exit_price: float | None = None
    holding_minutes: int | None = None
    gross_if_exited: float | None = None
    cost_if_exited: float | None = None
    net_if_exited: float | None = None
    funding_if_exited: float | None = None
    censored_by_max_hold: bool = False
    e0_net: float | None = None
    e0_status: str = "PENDING"
    delta_net: float | None = None
    is_exit: bool = False
    meaning: str = C1X_MEANING
    schema_version: str = C1X_SCHEMA_VERSION
    contract_sha256: str = C1X_CONTRACT_SHA256

    @property
    def c1x_event_id(self) -> str:
        """Derived, so a replay of the same signal cannot produce a second event for it."""
        return f"C1X-{self.parent_signal_id or self.signal_id}"

    def __post_init__(self) -> None:
        if self.parent_signal_id is None:
            self.parent_signal_id = self.signal_id
        if self.parent_signal_id != self.signal_id:
            raise ValueError("C1x parent_signal_id must match signal_id")

    def to_json(self) -> dict[str, Any]:
        body = clean_json(asdict(self))
        body["c1x_event_id"] = self.c1x_event_id
        return body

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "C1xEvent":
        known = {key: raw.get(key) for key in cls.__dataclass_fields__ if key in raw}
        known["parent_signal_id"] = raw.get("parent_signal_id", raw.get("signal_id"))
        for key in ("entry_price", "premium_value", "premium_boundary"):
            if key in known:
                known[key] = _number(known[key])
        for key in ("observed_price", "hypothetical_exit_price", "gross_if_exited",
                    "cost_if_exited", "net_if_exited", "funding_if_exited", "e0_net", "delta_net"):
            if key in known and known[key] is not None:
                known[key] = float(known[key])
        for key in ("triggered_at_ms", "observed_at_ms", "executable_at_ms", "holding_minutes"):
            if key in known and known[key] is not None:
                known[key] = int(known[key])
        known["censored_by_max_hold"] = bool(known.get("censored_by_max_hold", False))
        known["is_exit"] = False                      # never loaded as true, whatever a file says
        return cls(**known)

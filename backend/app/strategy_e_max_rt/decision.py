"""The 09:25 E-MAX V1 decision for the realtime runtime, and the realtime source capacity gate.

``decide_from_frame`` is the frozen rule, reused rather than rewritten: the V1.1 seal
(``strategy_e_v1_1.decision.seal``: PIT universe, H5 through Research's mask), R1 through
``strategy_e_max.ranking``, max 3, B2 through ``strategy_e_max.breadth`` and the final exposure from
``strategy_e_max.v1``. It takes a ``DecisionFrame`` built only from data known at 09:25 and returns
an immutable, hashed decision.

``RealtimeSourceGate`` is the decision source the runtime uses with the common realtime provider. A
frame needs the whole D-1 daily-eligible universe's 04:00-09:24 bars, and RVOL needs 20 prior
sessions of the same source; when the provider cannot deliver that between 09:25 and 09:30 the gate
refuses with FEATURE_CONTEXT_INCOMPLETE. It never narrows the universe (that would change H5's
population and the B2 rate), never mixes sources for RVOL, and never decides on partial data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math
from typing import Any, Protocol

from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.strategy_e_max import breadth, ranking, v1
from app.strategy_e_v1_1 import context, decision as V11
from app.strategy_e_v1_1.universe import DecisionFrame


@dataclass(frozen=True)
class EDecision:
    strategy_id: str
    session: str
    decided_at: str
    source: str
    universe_rows: int
    h5_count: int
    h5_rate: float | None
    high_breadth: bool
    breadth_multiplier: str
    global_multiplier: str
    final_exposure: str
    candidates: tuple[str, ...]
    r1_order: tuple[str, ...]
    selected: tuple[str, ...]
    reference_prices: Mapping[str, float]
    features: Mapping[str, Mapping[str, float | None]]
    v1_1_seal_digest: str
    v1_rules_digest: str
    digest: str = ""

    def to_json(self) -> dict[str, Any]:
        body = asdict(self)
        body["candidates"], body["r1_order"], body["selected"] = (list(self.candidates), list(self.r1_order),
                                                                  list(self.selected))
        return body

    @classmethod
    def from_json(cls, body: Mapping[str, Any]) -> "EDecision":
        data = dict(body)
        for key in ("candidates", "r1_order", "selected"):
            data[key] = tuple(data[key])
        return cls(**data)


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest_of(decision: EDecision) -> str:
    body = decision.to_json()
    body.pop("digest")
    return hashlib.sha256(_canonical(body)).hexdigest()


def verify(decision: EDecision) -> None:
    if digest_of(decision) != decision.digest:
        raise ValueError("E-MAX decision was altered after 09:25")


def _finite(x: float) -> float | None:
    return float(x) if math.isfinite(float(x)) else None


def decide_from_frame(frame: DecisionFrame, *, source_digest: str, source: str, decided_at: datetime) -> EDecision:
    sealed = V11.seal(frame, source_digest=source_digest)
    signal = sealed.signal
    index = {s: k for k, s in enumerate(frame.symbols)}
    candidates = tuple(signal.candidate_symbols)
    feats = {s: {n: float(frame.features[n][index[s]]) for n in SEALED_FEATURES} for s in candidates}
    order = ranking.order(v1.RANKING, candidates, feats)
    selected = order[:v1.MAX_SELECTED]
    k = breadth.multiplier(signal.candidate_count, signal.eligible_count)
    final = k * v1.GLOBAL
    refs = {s: feats[s]["close_price"] * (1.0 + feats[s]["premarket_gap"]) for s in selected}   # 09:24 last print
    decision = EDecision(
        strategy_id=v1.STRATEGY_ID, session=frame.session.isoformat(), decided_at=decided_at.isoformat(),
        source=source, universe_rows=signal.eligible_count, h5_count=signal.candidate_count,
        h5_rate=breadth.h5_rate(signal.candidate_count, signal.eligible_count), high_breadth=k != 1,
        breadth_multiplier=f"{k.numerator}/{k.denominator}",
        global_multiplier=f"{v1.GLOBAL.numerator}/{v1.GLOBAL.denominator}",
        final_exposure=f"{final.numerator}/{final.denominator}", candidates=candidates, r1_order=order,
        selected=selected, reference_prices=refs,
        features={s: {n: _finite(v) for n, v in f.items()} for s, f in feats.items()},
        v1_1_seal_digest=sealed.seal_digest, v1_rules_digest=v1.RULES_CANONICAL_SHA256)
    return EDecision(**{**decision.__dict__, "digest": digest_of(decision)})


class DecisionSource(Protocol):
    source: str

    def decide(self, session: date, decided_at: datetime) -> EDecision:
        """Return the 09:25 decision or raise ``context.FeatureContextIncomplete``."""


@dataclass
class FrameDecisionSource:
    """Any 09:25 frame builder (tests, a future realtime or reconstructed feature builder)."""

    build_frame: Callable[[date], tuple[DecisionFrame, str]]
    source: str

    def decide(self, session: date, decided_at: datetime) -> EDecision:
        frame, source_digest = self.build_frame(session)
        return decide_from_frame(frame, source_digest=source_digest, source=self.source, decided_at=decided_at)


@dataclass(frozen=True)
class SourceCapacity:
    """What a realtime provider can deliver for one 09:25 decision."""

    provider: str
    requests_per_second: float          # the limiter the common provider enforces
    requests_per_symbol: int            # one per-symbol minute-chart call covering 04:00-09:24
    universe_rows: int                  # D-1 daily-eligible universe
    decision_window_seconds: float = 300.0   # 09:25:00 (09:24 bar complete) -> 09:30:00 entry
    rvol_history_same_source: bool = False

    @property
    def seconds_needed(self) -> float:
        return self.universe_rows * self.requests_per_symbol / self.requests_per_second

    def blockers(self) -> list[str]:
        out = []
        if self.seconds_needed > self.decision_window_seconds:
            out.append(f"{self.provider}: {self.universe_rows} symbols x {self.requests_per_symbol} request(s) at "
                       f"{self.requests_per_second:g}/s needs {self.seconds_needed:.0f} s after 09:25 "
                       f"(window {self.decision_window_seconds:.0f} s)")
        if not self.rvol_history_same_source:
            out.append(f"{self.provider}: no 20-session premarket history for the whole universe from the same "
                       f"source (RVOL denominator); mixing it with Massive history is not allowed")
        return out


@dataclass
class RealtimeSourceGate:
    """Fail-closed decision source for the common realtime provider."""

    capacity: Callable[[date], SourceCapacity]
    source: str
    inner: DecisionSource | None = None

    def decide(self, session: date, decided_at: datetime) -> EDecision:
        blockers = self.capacity(session).blockers()
        if blockers or self.inner is None:
            raise context.FeatureContextIncomplete(session, tuple(blockers or ["no realtime feature builder"]))
        return self.inner.decide(session, decided_at)

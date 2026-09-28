"""H-V2 D1 E2 - Fundamental Change Detection.

E2 does not score a level ("revenue growth is high"). It detects a change in trajectory
(acceleration, deceleration, inflection, recovery, deterioration) and records the raw evidence
behind that call so an AI reader can later ask "why did the pipeline think this changed?"
(`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md` §E2). No output of this module is a buy signal:
`ACCELERATING != APPROVE`, `INFLECTION_POSITIVE != BUY`. See
`test_change_detection.py::test_change_state_is_not_a_decision` for the enforced invariant.

Reuses, without modification, H-PV2's comparable-period matching and transition handling
(`strategy_h0.h_pv2.period_family`, `.comparable_pair`, `.growth_rate`) and H-PV3's matched-duration
and instant-pair primitives (`strategy_h0.h_pv3.matched_duration`, `.instant_pair`). These were
audited PIT-safe utilities in H0-PV3; only their use here (evidence extraction, not ranking) is new.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable, Sequence

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h_pv2 import comparable_pair, growth_rate, period_family
from app.backtest.strategy_h0.h_pv3 import DISCRETE, YTD, instant_pair, matched_duration

SCHEMA_VERSION = "h_v2_change_evidence_v1"

# Deterministic, evidence-noise thresholds, not performance-tuned cutoffs (see
# `eligibility.py`'s header note; the same discipline applies here).
MONOTONIC_EPS = 0.005
"""A period-over-period change smaller than this (0.5 percentage points of growth, or 0.5 margin
points) is treated as noise, not as a direction, when testing whether a series is monotonic."""

STABLE_EPS = 0.02
"""Net change across the whole observed window smaller than this counts as STABLE rather than a
directional call, when the series is not monotonic."""


class ChangeState(StrEnum):
    IMPROVING = "IMPROVING"
    ACCELERATING = "ACCELERATING"
    STABLE = "STABLE"
    DECELERATING = "DECELERATING"
    DETERIORATING = "DETERIORATING"
    INFLECTION_POSITIVE = "INFLECTION_POSITIVE"
    INFLECTION_NEGATIVE = "INFLECTION_NEGATIVE"
    LOSS_TO_PROFIT = "LOSS_TO_PROFIT"
    PROFIT_TO_LOSS = "PROFIT_TO_LOSS"
    INCREASING = "INCREASING"
    DECREASING = "DECREASING"
    UNKNOWN = "UNKNOWN"


MATERIAL_STATES = frozenset({
    ChangeState.ACCELERATING, ChangeState.DECELERATING,
    ChangeState.INFLECTION_POSITIVE, ChangeState.INFLECTION_NEGATIVE,
    ChangeState.LOSS_TO_PROFIT, ChangeState.PROFIT_TO_LOSS,
})
"""States E3 (`research_priority.py`) counts as "a change worth reading about". IMPROVING/
DETERIORATING/INCREASING/DECREASING/STABLE/UNKNOWN are not counted as material on their own; a
metric that is merely trending, without accelerating or inflecting, is weaker research bait."""


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ChangePoint:
    end: str
    value: float | None
    status: str
    current_accession: str | None
    prior_accession: str | None


@dataclass(frozen=True)
class ChangeEvidence:
    metric: str
    state: ChangeState
    confidence: Confidence
    current_value: float | None
    points: tuple[ChangePoint, ...]
    data_cutoff: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        datetime.fromisoformat(self.data_cutoff)  # raises if not aware-ISO


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def classify_trend(series: Sequence[float], *, allow_acceleration: bool) -> tuple[ChangeState, Confidence]:
    """Deterministic direction classifier shared by growth-rate series (acceleration allowed) and
    level series such as margin (acceleration not meaningful; IMPROVING/DETERIORATING instead).

    `series` must already exclude transition-flagged (None) observations and be ordered oldest to
    newest.
    """
    if len(series) < 2:
        return ChangeState.UNKNOWN, Confidence.UNKNOWN
    many_points = len(series) >= 3
    # A sign crossing (e.g. a growth rate moving from negative to positive) is a more specific and
    # more informative research trigger than plain acceleration, so it is checked first even when
    # the series also happens to be monotonic.
    if _sign(series[0]) != 0 and _sign(series[-1]) != 0 and _sign(series[0]) != _sign(series[-1]):
        return (
            (ChangeState.INFLECTION_POSITIVE if series[-1] > 0 else ChangeState.INFLECTION_NEGATIVE),
            Confidence.MEDIUM if many_points else Confidence.LOW,
        )
    deltas = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    monotonic_up = all(d > MONOTONIC_EPS for d in deltas)
    monotonic_down = all(d < -MONOTONIC_EPS for d in deltas)
    if allow_acceleration and many_points and monotonic_up:
        return ChangeState.ACCELERATING, Confidence.HIGH
    if allow_acceleration and many_points and monotonic_down:
        return ChangeState.DECELERATING, Confidence.HIGH
    net = series[-1] - series[0]
    if abs(net) <= STABLE_EPS:
        return ChangeState.STABLE, Confidence.MEDIUM if many_points else Confidence.LOW
    if net > 0:
        return ChangeState.IMPROVING, Confidence.MEDIUM if many_points else Confidence.LOW
    return ChangeState.DETERIORATING, Confidence.MEDIUM if many_points else Confidence.LOW


def _growth_points(
    facts: Sequence[CanonicalFact], field_name: str, cutoff: datetime, decision: date,
    *, periods: int, fcf: bool,
) -> list[ChangePoint]:
    ends = sorted(
        {f.end for f in facts if f.field == field_name and f.accepted_at <= cutoff
         and f.end <= decision and period_family(f, fcf=fcf)},
        reverse=True,
    )
    points: list[ChangePoint] = []
    for end in ends:
        if len(points) >= periods:
            break
        pair = comparable_pair(facts, field_name, cutoff, end, fcf=fcf)
        if pair is None or pair.current.end != end:
            continue
        rate, status = growth_rate(pair.current.value, pair.prior.value, eps=field_name == "eps_diluted")
        points.append(ChangePoint(
            end=end.isoformat(), value=rate, status=status,
            current_accession=pair.current.accession, prior_accession=pair.prior.accession,
        ))
    points.reverse()
    return points


def growth_trend(
    facts: Iterable[CanonicalFact], field_name: str, cutoff: datetime, decision: date,
    *, periods: int = 4,
) -> ChangeEvidence:
    """Revenue / Operating Income / diluted EPS growth-rate trend (§D1 §13-15)."""
    points = _growth_points(list(facts), field_name, cutoff, decision, periods=periods, fcf=False)
    numeric = [p.value for p in points if p.value is not None]
    latest_status = points[-1].status if points else None
    if field_name == "eps_diluted" and latest_status in {"LOSS_TO_PROFIT", "PROFIT_TO_LOSS"}:
        state = ChangeState.LOSS_TO_PROFIT if latest_status == "LOSS_TO_PROFIT" else ChangeState.PROFIT_TO_LOSS
        confidence = Confidence.MEDIUM
    else:
        state, confidence = classify_trend(numeric, allow_acceleration=True)
    return ChangeEvidence(
        metric=field_name, state=state, confidence=confidence,
        current_value=points[-1].value if points else None,
        points=tuple(points), data_cutoff=cutoff.isoformat(),
    )


def fcf_trend(facts: Iterable[CanonicalFact], cutoff: datetime, decision: date, *, periods: int = 4) -> ChangeEvidence:
    """Free-cash-flow (OCF - CapEx) growth trend, reusing PV2's exact YTD/quarter comparable-period
    rule so a quarter is never compared against a YTD figure (§D1 §16)."""
    ends = sorted(
        {f.end for f in facts if f.field == "operating_cash_flow" and f.accepted_at <= cutoff
         and f.end <= decision and period_family(f, fcf=True)},
        reverse=True,
    )
    points: list[ChangePoint] = []
    for end in ends:
        if len(points) >= periods:
            break
        ocf = comparable_pair(facts, "operating_cash_flow", cutoff, end, fcf=True)
        capex = comparable_pair(facts, "capex", cutoff, end, fcf=True)
        if ocf is None or capex is None or ocf.current.end != end:
            continue
        if (ocf.current.start, ocf.current.end, ocf.family) != (capex.current.start, capex.current.end, capex.family):
            continue
        if (ocf.prior.start, ocf.prior.end, ocf.family) != (capex.prior.start, capex.prior.end, capex.family):
            continue
        current = ocf.current.value - capex.current.value
        prior = ocf.prior.value - capex.prior.value
        value, status = growth_rate(current, prior)
        points.append(ChangePoint(
            end=end.isoformat(), value=value, status=status,
            current_accession=ocf.current.accession, prior_accession=ocf.prior.accession,
        ))
    points.reverse()
    numeric = [p.value for p in points if p.value is not None]
    latest_status = points[-1].status if points else None
    if latest_status in {"LOSS_TO_PROFIT", "PROFIT_TO_LOSS"}:
        state = ChangeState.LOSS_TO_PROFIT if latest_status == "LOSS_TO_PROFIT" else ChangeState.PROFIT_TO_LOSS
        confidence = Confidence.MEDIUM
    else:
        state, confidence = classify_trend(numeric, allow_acceleration=True)
    return ChangeEvidence(
        metric="free_cash_flow", state=state, confidence=confidence,
        current_value=points[-1].value if points else None,
        points=tuple(points), data_cutoff=cutoff.isoformat(),
    )


def operating_margin_trend(
    facts: Iterable[CanonicalFact], cutoff: datetime, decision: date, *, periods: int = 4,
) -> ChangeEvidence:
    """Operating margin LEVEL trend (§D1 §14). Uses IMPROVING/DETERIORATING, not ACCELERATING/
    DECELERATING: acceleration of a ratio's level is not a meaningful research trigger the way
    acceleration of a growth rate is."""
    fs = list(facts)
    ends = sorted(
        {f.end for f in fs if f.field == "revenue" and f.accepted_at <= cutoff
         and f.end <= decision and f.start is not None},
        reverse=True,
    )
    points: list[ChangePoint] = []
    for end in ends:
        if len(points) >= periods:
            break
        matched, status = matched_duration(fs, ["revenue", "operating_income"], cutoff, end, DISCRETE)
        if matched is None or matched["revenue"].end != end:
            continue
        if matched["revenue"].value <= 0:
            continue
        margin = matched["operating_income"].value / matched["revenue"].value
        points.append(ChangePoint(
            end=end.isoformat(), value=margin, status="OK",
            current_accession=matched["operating_income"].accession, prior_accession=None,
        ))
    points.reverse()
    numeric = [p.value for p in points if p.value is not None]
    state, confidence = classify_trend(numeric, allow_acceleration=False)
    return ChangeEvidence(
        metric="operating_margin", state=state, confidence=confidence,
        current_value=points[-1].value if points else None,
        points=tuple(points), data_cutoff=cutoff.isoformat(),
    )


def balance_sheet_trend(
    facts: Iterable[CanonicalFact], field_name: str, cutoff: datetime, decision: date, *, periods: int = 4,
) -> ChangeEvidence:
    """Direction-only trend for an instant balance-sheet field (`cash` or `total_debt`, §D1 §17).

    Uses INCREASING/DECREASING/STABLE/UNKNOWN rather than IMPROVING/DETERIORATING: whether more
    debt or more cash is "good" is a business-context judgment for the AI Research Engine, not a
    fact code should assert (`H_V2_D0` §V: code never makes a value judgment)."""
    fs = list(facts)
    ends = sorted(
        {f.end for f in fs if f.field == field_name and f.start is None and f.end <= decision
         and f.accepted_at <= cutoff},
        reverse=True,
    )
    points: list[ChangePoint] = []
    for end in ends[:periods]:
        matched, status = instant_pair(fs, [field_name], cutoff, end)
        if matched is None:
            continue
        points.append(ChangePoint(
            end=end.isoformat(), value=matched[field_name].value, status="OK",
            current_accession=matched[field_name].accession, prior_accession=None,
        ))
    points.reverse()
    numeric = [p.value for p in points if p.value is not None]
    if len(numeric) < 2:
        state, confidence = ChangeState.UNKNOWN, Confidence.UNKNOWN
    else:
        net = numeric[-1] - numeric[0]
        if abs(net) <= STABLE_EPS * max(abs(numeric[0]), 1.0):
            state, confidence = ChangeState.STABLE, Confidence.MEDIUM if len(numeric) >= 3 else Confidence.LOW
        else:
            state = ChangeState.INCREASING if net > 0 else ChangeState.DECREASING
            confidence = Confidence.MEDIUM if len(numeric) >= 3 else Confidence.LOW
    return ChangeEvidence(
        metric=field_name, state=state, confidence=confidence,
        current_value=points[-1].value if points else None,
        points=tuple(points), data_cutoff=cutoff.isoformat(),
    )


def dilution_ratio(
    facts: Iterable[CanonicalFact], cutoff: datetime, decision: date, *, split_dates: Iterable[date] = (),
) -> tuple[float | None, bool | None, ChangeEvidence]:
    """Shares-outstanding change, distinguishing a split (explained) from dilution (unexplained).

    Returns (ratio, material_dilution_flag, evidence). `material_dilution_flag` is True only when
    shares rose by more than `eligibility.MATERIAL_DILUTION_RATIO` with no recorded split in the
    window; it is None (not False) when there is not enough data to judge."""
    from app.backtest.strategy_h_v2.eligibility import MATERIAL_DILUTION_RATIO

    fs = list(facts)
    ends = sorted(
        {f.end for f in fs if f.field == "shares_outstanding" and f.start is None
         and f.end <= decision and f.accepted_at <= cutoff},
        reverse=True,
    )
    points: list[ChangePoint] = []
    for end in ends[:2]:
        matched, _ = instant_pair(fs, ["shares_outstanding"], cutoff, end)
        if matched is None:
            continue
        points.append(ChangePoint(
            end=end.isoformat(), value=matched["shares_outstanding"].value, status="OK",
            current_accession=matched["shares_outstanding"].accession, prior_accession=None,
        ))
    points.reverse()
    if len(points) < 2 or points[0].value in (None, 0):
        evidence = ChangeEvidence(
            metric="shares_outstanding", state=ChangeState.UNKNOWN, confidence=Confidence.UNKNOWN,
            current_value=points[-1].value if points else None, points=tuple(points),
            data_cutoff=cutoff.isoformat(),
        )
        return None, None, evidence
    ratio = points[-1].value / points[0].value - 1
    had_split = any(points[0].end < split.isoformat() <= points[-1].end for split in split_dates)
    material = bool(ratio >= MATERIAL_DILUTION_RATIO and not had_split)
    if abs(ratio) <= STABLE_EPS:
        state = ChangeState.STABLE
    else:
        state = ChangeState.INCREASING if ratio > 0 else ChangeState.DECREASING
    evidence = ChangeEvidence(
        metric="shares_outstanding", state=state, confidence=Confidence.HIGH,
        current_value=points[-1].value, points=tuple(points), data_cutoff=cutoff.isoformat(),
    )
    return ratio, material, evidence

"""Strategy B scanner gate and candidate score.

The declared rules are ``docs/backtest/strategy_b/B_F0_FSM_RULES_V1.md`` sections 4 and 5;
every number comes from ``StrategyBConfig``, never from a literal here.

Two separate questions, in order:

* the **gate** (``scan``) asks whether this minute is worth watching at all. It is a
  hard filter on one ``FeatureSnapshot``: one momentum leg out of three, both liquidity
  gates, the regular session and the scan window.
* the **score** ranks what passed and raises the bar a second time
  (``candidate.score_threshold``). A gate-failing snapshot has no score.

A value that is not ``AVAILABLE`` fails its condition (fail-closed). The one exception is
``RvolStatus.PARTIAL``: a partial RVOL still carries a number, and the config already says
how few sessions are too few (``rvol.min_partial_sessions``).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.strategy_b.config import CandidateConfig, ScannerConfig, parse_et_clock
from app.strategy_b.models import Availability, FeatureSnapshot, Measured, RvolStatus, Session
from app.strategy_b.session import ET


class GateReason(StrEnum):
    """Why a snapshot did not become a candidate. Recorded, never collapsed into a bool."""

    NOT_REGULAR_SESSION = "NOT_REGULAR_SESSION"
    OUTSIDE_SCAN_WINDOW = "OUTSIDE_SCAN_WINDOW"
    NO_MOMENTUM_LEG = "NO_MOMENTUM_LEG"
    DOLLAR_VOLUME_BELOW_MIN = "DOLLAR_VOLUME_BELOW_MIN"
    DOLLAR_VOLUME_UNKNOWN = "DOLLAR_VOLUME_UNKNOWN"
    RVOL_BELOW_MIN = "RVOL_BELOW_MIN"
    RVOL_UNKNOWN = "RVOL_UNKNOWN"


MOMENTUM_LEGS: tuple[tuple[str, str], ...] = (
    ("return_1m", "return_1m_threshold"),
    ("return_3m", "return_3m_threshold"),
    ("return_5m", "return_5m_threshold"),
)


@dataclass(frozen=True, slots=True)
class ScanDecision:
    """One symbol at one as-of time: did it pass, how strongly, and why not."""

    symbol: str
    as_of: datetime
    passed: bool
    score: float | None
    momentum_ratio: float | None
    rvol_ratio: float | None
    liquidity_ratio: float | None
    dollar_volume: float | None
    reasons: tuple[GateReason, ...]

    def __post_init__(self) -> None:
        if self.passed != (self.score is not None):
            raise ValueError("a score exists exactly when the gate passed")
        if self.passed and self.reasons:
            raise ValueError("a passing decision carries no gate reason")

    @property
    def sort_key(self) -> tuple[float, float, str]:
        """Deterministic ranking: score desc, dollar volume desc, symbol asc."""
        return (-(self.score or 0.0), -(self.dollar_volume or 0.0), self.symbol)


def in_scan_window(as_of: datetime, config: ScannerConfig) -> bool:
    """Is ``as_of`` inside the ET scan window (both ends inclusive)?"""
    local = as_of.astimezone(ET).timetz().replace(tzinfo=None)
    return parse_et_clock(config.scan_window_start_et) <= local <= parse_et_clock(
        config.scan_window_end_et)


def scan(snapshot: FeatureSnapshot, *, scanner: ScannerConfig,
         candidate: CandidateConfig) -> ScanDecision:
    """Apply the gate to one snapshot and score it when it passes."""
    reasons: list[GateReason] = []
    if snapshot.session is not Session.REGULAR:
        reasons.append(GateReason.NOT_REGULAR_SESSION)
    if not in_scan_window(snapshot.as_of, scanner):
        reasons.append(GateReason.OUTSIDE_SCAN_WINDOW)

    momentum_ratio = _momentum_ratio(snapshot, scanner)
    if momentum_ratio is None or momentum_ratio < 1.0:
        reasons.append(GateReason.NO_MOMENTUM_LEG)

    dollar_volume = _value(snapshot.rolling_dollar_volume)
    if dollar_volume is None:
        reasons.append(GateReason.DOLLAR_VOLUME_UNKNOWN)
    elif dollar_volume < scanner.min_dollar_volume:
        reasons.append(GateReason.DOLLAR_VOLUME_BELOW_MIN)

    rvol = None if snapshot.rvol_status is RvolStatus.UNKNOWN else _value(snapshot.rvol)
    if rvol is None:
        reasons.append(GateReason.RVOL_UNKNOWN)
    elif rvol < scanner.min_rvol:
        reasons.append(GateReason.RVOL_BELOW_MIN)

    rvol_ratio = None if rvol is None else rvol / scanner.min_rvol
    liquidity_ratio = None if dollar_volume is None else dollar_volume / scanner.min_dollar_volume
    if reasons:
        return ScanDecision(
            symbol=snapshot.symbol, as_of=snapshot.as_of, passed=False, score=None,
            momentum_ratio=momentum_ratio, rvol_ratio=rvol_ratio,
            liquidity_ratio=liquidity_ratio, dollar_volume=dollar_volume,
            reasons=tuple(reasons))

    # No reason means every gate read a value, so the three ratios are numbers, not None.
    score = (candidate.score_weight_momentum * _cap(momentum_ratio, candidate)
             + candidate.score_weight_rvol * _cap(rvol_ratio, candidate)
             + candidate.score_weight_liquidity * _cap(liquidity_ratio, candidate))
    return ScanDecision(
        symbol=snapshot.symbol, as_of=snapshot.as_of, passed=True, score=score,
        momentum_ratio=momentum_ratio, rvol_ratio=rvol_ratio, liquidity_ratio=liquidity_ratio,
        dollar_volume=dollar_volume, reasons=())


def rank(decisions: tuple[ScanDecision, ...] | list[ScanDecision]) -> tuple[ScanDecision, ...]:
    """Order passing decisions deterministically. Failing decisions are dropped."""
    return tuple(sorted((d for d in decisions if d.passed), key=lambda d: d.sort_key))


def _momentum_ratio(snapshot: FeatureSnapshot, scanner: ScannerConfig) -> float | None:
    """The strongest leg as a multiple of its threshold. ``None`` when every leg is missing.

    A missing leg is left out of the maximum; it is never read as a zero return, because
    "no observation" and "no move" are different statements.
    """
    ratios = [value / getattr(scanner, threshold_name)
              for feature_name, threshold_name in MOMENTUM_LEGS
              if (value := _value(getattr(snapshot, feature_name))) is not None]
    return max(ratios) if ratios else None


def _cap(ratio: float, candidate: CandidateConfig) -> float:
    """Saturating normalisation into [0, 1]. Negative ratios cannot occur past the gate."""
    return min(ratio, candidate.score_ratio_cap) / candidate.score_ratio_cap


def _value(measured: Measured) -> float | None:
    return measured.value if measured.status is Availability.AVAILABLE else None

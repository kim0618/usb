"""The pre-registered verdict, applied exactly as the contract's decision order states it.

The order is the design. The contract lists ranked conditions, the first match wins, and the
six outcomes are therefore mutually exclusive and exhaustive. Writing it as a ranked list
before any result exists is what makes BORDERLINE a declared bucket rather than a place to put
a number that disappointed someone.

This module does no statistics of its own. It receives the sample counts, the interval and the
lift outcome, and returns the state those imply.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.backtest.strategy_b_e0.contract import Contract


class Verdict(StrEnum):
    PASS = "PASS"
    BORDERLINE = "BORDERLINE"
    FAIL = "FAIL"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    DATASET_NOT_READY = "DATASET_NOT_READY"
    ERROR = "ERROR"


class LiftOutcome(StrEnum):
    LIFT_PASS = "LIFT_PASS"
    LIFT_FAIL = "LIFT_FAIL"
    LIFT_INSUFFICIENT = "LIFT_INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class SampleCounts:
    closed_trades: int
    sessions_with_trades: int


@dataclass(frozen=True, slots=True)
class Interval:
    """A point estimate and the bootstrap interval around it."""

    point: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class GateResult:
    verdict: Verdict
    reason: str
    sample: SampleCounts
    mean_net_r: Interval | None
    lift: LiftOutcome | None
    thresholds: Mapping[str, float | int]

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": str(self.verdict),
            "reason": self.reason,
            "sample": {"closed_trades": self.sample.closed_trades,
                       "sessions_with_trades": self.sample.sessions_with_trades},
            "mean_net_r": None if self.mean_net_r is None else {
                "point": self.mean_net_r.point,
                "ci_lower": self.mean_net_r.lower,
                "ci_upper": self.mean_net_r.upper},
            "lift": None if self.lift is None else str(self.lift),
            "thresholds": dict(self.thresholds),
        }


def evaluate(contract: Contract, *, sample: SampleCounts, mean_net_r: Interval | None,
             lift: LiftOutcome | None, dataset_ready: bool = True,
             error: str | None = None) -> GateResult:
    """Apply the contract's ranked decision order and return the first outcome that matches."""
    thresholds = {
        "min_closed_trades": contract.min_closed_trades,
        "min_sessions_with_trades": contract.min_sessions_with_trades,
        "min_mean_net_r": contract.min_mean_net_r,
        "confidence": contract.statistics.confidence,
    }

    def result(verdict: Verdict, reason: str) -> GateResult:
        return GateResult(verdict=verdict, reason=reason, sample=sample, mean_net_r=mean_net_r,
                          lift=lift, thresholds=thresholds)

    # rank 1
    if not dataset_ready:
        return result(Verdict.DATASET_NOT_READY, "the dataset readiness preflight did not pass")
    # rank 2
    if error is not None:
        return result(Verdict.ERROR, error)
    # rank 3
    if sample.closed_trades < contract.min_closed_trades:
        return result(Verdict.INSUFFICIENT_SAMPLE,
                      f"{sample.closed_trades} closed trades < {contract.min_closed_trades}")
    if sample.sessions_with_trades < contract.min_sessions_with_trades:
        return result(Verdict.INSUFFICIENT_SAMPLE,
                      f"{sample.sessions_with_trades} sessions with a trade < "
                      f"{contract.min_sessions_with_trades}")
    # past the sample gate, an interval must exist; its absence is a defect, not a verdict
    if mean_net_r is None:
        return result(Verdict.ERROR,
                      "the sample gate passed but no mean net R interval was computed")
    # rank 4
    if mean_net_r.upper <= 0:
        return result(Verdict.FAIL,
                      f"the {_pct(contract)} CI upper bound {mean_net_r.upper:.4f} <= 0")
    # rank 5
    if (mean_net_r.point >= contract.min_mean_net_r and mean_net_r.lower > 0
            and lift is LiftOutcome.LIFT_PASS):
        return result(Verdict.PASS,
                      f"mean net R {mean_net_r.point:.4f} >= {contract.min_mean_net_r}, "
                      f"CI lower {mean_net_r.lower:.4f} > 0, and the lift gate passed")
    # rank 6
    return result(Verdict.BORDERLINE, _borderline_reason(contract, mean_net_r, lift))


def _borderline_reason(contract: Contract, interval: Interval, lift: LiftOutcome | None) -> str:
    unmet = []
    if interval.point < contract.min_mean_net_r:
        unmet.append(f"mean net R {interval.point:.4f} < {contract.min_mean_net_r}")
    if interval.lower <= 0:
        unmet.append(f"CI lower {interval.lower:.4f} <= 0")
    if lift is not LiftOutcome.LIFT_PASS:
        unmet.append(f"lift gate {lift if lift is not None else 'not computed'}")
    return "sample sufficient and not significantly non-positive, but " + "; ".join(unmet)


def _pct(contract: Contract) -> str:
    return f"{contract.statistics.confidence:.0%}"

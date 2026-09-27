"""Frozen H0.6 coverage gates; no return or strategy calculations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class H06Verdict(StrEnum):
    PASS = "PASS"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAIL = "FAIL"


@dataclass(frozen=True)
class H06Metrics:
    entitlement_years_ok: int
    entitlement_years_required: int = 10
    dated_snapshots_ok: int = 0
    dated_snapshots_required: int = 9
    later_inactive_members: int = 0
    identity_stable: bool | None = None
    daily_completeness: float | None = None
    pit_shares_coverage: float | None = None
    market_cap_coverage: float | None = None
    actions_consistent: bool | None = None
    unknown_policy_deterministic: bool = True


def verdict(metrics: H06Metrics) -> H06Verdict:
    """Apply only preregistered H0.6 gates.

    A credential that fails the ten annual access probes fails this paid-entitlement
    audit. Once access passes, unmeasured downstream coverage remains inconclusive.
    """
    if metrics.entitlement_years_ok < metrics.entitlement_years_required:
        return H06Verdict.FAIL
    measured = (metrics.identity_stable, metrics.daily_completeness,
                metrics.pit_shares_coverage, metrics.market_cap_coverage,
                metrics.actions_consistent)
    if metrics.dated_snapshots_ok < metrics.dated_snapshots_required or any(
            value is None for value in measured):
        return H06Verdict.INCONCLUSIVE
    passed = (metrics.later_inactive_members >= 2
              and metrics.identity_stable is True
              and metrics.daily_completeness >= 0.98
              and metrics.pit_shares_coverage >= 0.70
              and metrics.market_cap_coverage >= 0.60
              and metrics.actions_consistent is True
              and metrics.unknown_policy_deterministic)
    return H06Verdict.PASS if passed else H06Verdict.INCONCLUSIVE

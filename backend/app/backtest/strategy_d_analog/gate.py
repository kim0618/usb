"""GATE-D-ALPHA: the ten conditions D0 froze, evaluated exactly as written.

Every threshold in this file was declared on 2026-09-17, before any D vector existed. Nothing
here is derived from what the run measured, and there is no branch that softens a condition
because a result came out close. A condition D0 did not declare - monotonicity of the quintile
baskets, a concentration limit - is reported as a diagnostic and cannot change a verdict; adding
one after seeing the numbers would be the exact failure this gate exists to prevent.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

CONDITIONS = (
    ("1_ic_point", "mean daily Spearman IC > 0"),
    ("2_ic_ci_low", "IC bootstrap CI low > 0 at the Bonferroni level"),
    ("3_delta_ic_vs_N1_ci_low", "mean per-date (IC - IC_N1) CI low > 0 at the Bonferroni level"),
    ("4_orthogonalized_ic_vs_N2b_ci_low", "N2b residual IC CI low > 0 at the Bonferroni level"),
    ("5_delta_ic_vs_N2a_point", "mean per-date (IC - IC_N2a) > 0"),
    ("6_quintile_spread_point", "mean per-date (Q5 - Q1) realized excess return > 0"),
    ("7_time_blocks", "IC point > 0 in at least 3 of the 4 chronological blocks"),
    ("8_horizon_consistency", "every other horizon of the same window and representation has IC > 0"),
    ("9_sample", "dates >= 150, valid queries >= 20000, unique tickers >= 1000, "
                 "INSUFFICIENT_NEIGHBORS share <= 0.05"),
    ("10_pit_violations", "PIT violations == 0"),
)
#: D0 ``pass_fail_policy.per_test_conditions.9_sample``.
MIN_EVALUABLE_DATES = 150
MIN_VALID_QUERIES = 20_000
MIN_UNIQUE_TICKERS = 1_000
MAX_INSUFFICIENT_SHARE = 0.05
#: D0 ``decision.INCONCLUSIVE``: the conditions whose failure alone still leaves the question open.
SOFT_CONDITIONS = ("7_time_blocks", "8_horizon_consistency", "9_sample")
CORE_CONDITIONS = ("1_ic_point", "2_ic_ci_low", "3_delta_ic_vs_N1_ci_low",
                   "4_orthogonalized_ic_vs_N2b_ci_low")

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
INVERSE_EFFECT = "INVERSE_EFFECT"


@dataclass
class TestVerdict:
    """One TestId against the ten conditions, with every condition recorded whether it passed."""

    test_id: str
    conditions: dict[str, bool]
    evidence: dict[str, Any] = field(default_factory=dict)
    inverse_effect: bool = False

    @property
    def failed(self) -> list[str]:
        return [name for name, ok in self.conditions.items() if not ok]

    @property
    def passed_all(self) -> bool:
        return not self.failed

    @property
    def status(self) -> str:
        if self.passed_all:
            return PASS
        if self.inverse_effect:
            return INVERSE_EFFECT
        if all(self.conditions.get(name, False) for name in CORE_CONDITIONS) \
                and set(self.failed) <= set(SOFT_CONDITIONS):
            return INCONCLUSIVE
        return FAIL

    def as_dict(self) -> dict[str, Any]:
        return {"test_id": self.test_id, "status": self.status,
                "conditions": dict(self.conditions), "failed": self.failed,
                "inverse_effect": self.inverse_effect, "evidence": self.evidence}


def evaluate_test(*, test_id: str, ic_point: float, ic_ci_low: float, ic_ci_high: float,
                  delta_n1_ci_low: float, n2b_ci_low: float, delta_n2a_point: float,
                  quintile_spread_point: float, block_ic_points: Sequence[float],
                  other_horizon_ic_points: Mapping[str, float], evaluable_dates: int,
                  valid_queries: int, unique_tickers: int, insufficient_share: float,
                  pit_violations: int) -> TestVerdict:
    """Apply the ten declared conditions to one test. No condition is computed from another run."""
    positive_blocks = int(np.sum([np.isfinite(v) and v > 0 for v in block_ic_points]))
    sample_ok = (evaluable_dates >= MIN_EVALUABLE_DATES and valid_queries >= MIN_VALID_QUERIES
                 and unique_tickers >= MIN_UNIQUE_TICKERS
                 and insufficient_share <= MAX_INSUFFICIENT_SHARE)
    conditions = {
        "1_ic_point": bool(np.isfinite(ic_point) and ic_point > 0),
        "2_ic_ci_low": bool(np.isfinite(ic_ci_low) and ic_ci_low > 0),
        "3_delta_ic_vs_N1_ci_low": bool(np.isfinite(delta_n1_ci_low) and delta_n1_ci_low > 0),
        "4_orthogonalized_ic_vs_N2b_ci_low": bool(np.isfinite(n2b_ci_low) and n2b_ci_low > 0),
        "5_delta_ic_vs_N2a_point": bool(np.isfinite(delta_n2a_point) and delta_n2a_point > 0),
        "6_quintile_spread_point": bool(np.isfinite(quintile_spread_point)
                                        and quintile_spread_point > 0),
        "7_time_blocks": positive_blocks >= 3,
        "8_horizon_consistency": bool(other_horizon_ic_points) and all(
            np.isfinite(v) and v > 0 for v in other_horizon_ic_points.values()),
        "9_sample": bool(sample_ok),
        "10_pit_violations": pit_violations == 0,
    }
    evidence = {"ic_point": ic_point, "ic_ci_low": ic_ci_low, "ic_ci_high": ic_ci_high,
                "delta_n1_ci_low": delta_n1_ci_low, "n2b_ci_low": n2b_ci_low,
                "delta_n2a_point": delta_n2a_point,
                "quintile_spread_point": quintile_spread_point,
                "block_ic_points": [float(v) for v in block_ic_points],
                "positive_blocks": positive_blocks,
                "other_horizon_ic_points": dict(other_horizon_ic_points),
                "evaluable_dates": evaluable_dates, "valid_queries": valid_queries,
                "unique_tickers": unique_tickers, "insufficient_share": insufficient_share,
                "pit_violations": pit_violations}
    inverse = bool(np.isfinite(ic_ci_high) and ic_ci_high < 0)
    return TestVerdict(test_id, conditions, evidence, inverse)


def strategy_decision(verdicts: Sequence[TestVerdict], evaluable_dates: int) -> dict[str, Any]:
    """D0 ``pass_fail_policy.decision``, applied to the whole 14-test family at once."""
    passing = [v.test_id for v in verdicts if v.passed_all]
    if passing:
        decision = PASS
        reason = f"{len(passing)} of {len(verdicts)} tests met all ten conditions"
    elif evaluable_dates < MIN_EVALUABLE_DATES:
        decision = INCONCLUSIVE
        reason = f"only {evaluable_dates} evaluable dates, below the declared {MIN_EVALUABLE_DATES}"
    else:
        soft = [v.test_id for v in verdicts
                if all(v.conditions.get(name, False) for name in CORE_CONDITIONS)
                and set(v.failed) <= set(SOFT_CONDITIONS)]
        if soft:
            decision = INCONCLUSIVE
            reason = (f"no test passed; {len(soft)} met conditions 1-4 and failed only among "
                      f"{list(SOFT_CONDITIONS)}: {soft}")
        else:
            decision = FAIL
            reason = "no test met all ten conditions and none failed only on the soft conditions"
    return {"gate": "GATE-D-ALPHA", "decision": decision, "reason": reason,
            "passing_tests": passing,
            "status_by_test": {v.test_id: v.status for v in verdicts},
            "inverse_effect_tests": [v.test_id for v in verdicts if v.inverse_effect],
            "meaning": "the analog signal carries information beyond random analogs and simple"
                       " price features in this data window; it is not evidence of a profitable"
                       " strategy"}

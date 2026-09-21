"""The frozen C-ATTACK declaration, refused if it no longer hashes to the recorded checksum.

`c_attack_rules_v1.json` was written 2026-09-21 14:49:48 KST with zero trades simulated. Every
number the simulator, the cost model or the gate uses is read from it; code carries none.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_selection.rules import REPO_ROOT, canonical_checksum

RULES_PATH = REPO_ROOT / "docs/backtest/strategy_c_attack/c_attack_rules_v1.json"
#: Recorded 2026-09-21 14:49:48 KST, before any C-ATTACK trade was simulated.
DECLARED_RULES_CHECKSUM = "0e3b4aa52ea757b625dc2b0bb7292eae176e34ab9934a829c0c1ecdb9e252785"


class DeclarationChanged(RuntimeError):
    """The declaration no longer hashes to the checksum recorded before the results."""


@dataclass(frozen=True)
class Pair:
    name: str
    tp: float
    sl: float


@dataclass(frozen=True)
class CostModel:
    commission: float
    by_price_bucket: dict[int, float]
    low_adv_add: float  # adv20 bucket 0
    stop_extra: float

    def round_trip(self, price_bucket, adv_bucket, stop_exit):
        """Vectorised: numpy arrays of buckets and the stop-type exit flag."""
        import numpy as np

        base = np.vectorize(lambda b: self.by_price_bucket[int(b)], otypes=[float])(price_bucket)
        return self.commission + base + np.where(adv_bucket == 0, self.low_adv_add, 0.0) \
            + np.where(stop_exit, self.stop_extra, 0.0)


@dataclass(frozen=True)
class AttackRules:
    raw: dict[str, Any]
    checksum: str

    @property
    def pairs(self) -> tuple[Pair, ...]:
        return tuple(Pair(name, float(v["tp"]), float(v["sl"]))
                     for name, v in self.raw["exits"]["pairs"].items())

    @property
    def primary_pair(self) -> Pair:
        name = self.raw["exits"]["primary_pair"]
        return next(p for p in self.pairs if p.name == name)

    @property
    def horizon(self) -> int:
        return int(self.raw["exits"]["max_holding_sessions"])

    @property
    def secondary_horizons(self) -> tuple[int, ...]:
        return tuple(int(k) for k in self.raw["exits"]["secondary_horizons_descriptive"])

    @property
    def delist_haircut(self) -> float:
        return 0.30

    @property
    def cost(self) -> CostModel:
        body = self.raw["costs"]
        buckets = {int(k): float(v) for k, v in body["spread_slippage_round_trip_by_price_bucket"].items()
                   if k != "note"}
        return CostModel(float(body["commission_round_trip"]), buckets,
                         float(body["low_adv_add_round_trip"]["adv20_bucket_0 (5M-20M)"]),
                         float(body["stop_extra"]["value"]))

    @property
    def min_controls(self) -> int:
        return int(self.raw["matched_control"]["min_controls_per_cell"])

    @property
    def published_status_counts(self) -> dict[str, int]:
        return {k: int(v) for k, v in self.raw["frozen_upstream"]["c_e0_published_status_counts"].items()}

    @property
    def baseline_digest(self) -> str:
        return str(self.raw["frozen_upstream"]["c_m_candidates_primary_digest"])

    @property
    def c_e0_run_id(self) -> str:
        return str(self.raw["frozen_upstream"]["c_e0_run_id"])

    # Gate thresholds (`gate.conditions`); written as numbers here only after being read above.
    PF_MIN = 1.10
    MDD_MAX = 0.25
    TOP10_SHARE_MAX = 0.50
    AMBIGUOUS_SHARE_INCONCLUSIVE = 0.25
    MIN_TRADES = 300
    MIN_MATCHED = 300
    MIN_TICKERS = 100
    BOOTSTRAP = (10, 10_000, 20260921)
    SLEEVES = 10
    ATTACK_RISK = 0.005


def _check_thresholds(raw: dict[str, Any]) -> None:
    """The class constants must say what the frozen text says; a mismatch is a refused run."""
    c = raw["gate"]["conditions"]
    expected = {"4_h3_profit_factor": "> 1.10", "5_h4_sleeve_mdd": "<= 0.25",
                "1_h1_net_expectancy_point": "> 0", "2_h2_excess_point": "> 0", "3_h2_excess_ci95_low": "> 0"}
    for key, text in expected.items():
        if c[key] != text:
            raise DeclarationChanged(f"gate condition {key} reads {c[key]!r}, code expects {text!r}")
    if "<= 50% of total net P&L" not in c["9_concentration"] or ">= 3 of 4 blocks" not in c["8_time_stability"]:
        raise DeclarationChanged("condition 8/9 text changed")
    if ">= 300" not in c["10_sample"] or ">= 100" not in c["10_sample"]:
        raise DeclarationChanged("condition 10 text changed")
    if "share of candidate trades > 0.25" not in raw["gate"]["decision"]["INCONCLUSIVE"]:
        raise DeclarationChanged("INCONCLUSIVE ambiguity threshold changed")
    if "10000 replicates, seed 20260921" not in raw["statistics"]["bootstrap"] \
            or "block length 10" not in raw["statistics"]["bootstrap"]:
        raise DeclarationChanged("bootstrap text changed")
    if "(1 - 0.30)" not in raw["exits"]["delisted_or_permanently_suspended"]:
        raise DeclarationChanged("delist haircut changed")
    if "10 sleeves" not in raw["capital_model"]["sleeve_portfolio"] \
            or "risk 0.5% of equity" not in raw["capital_model"]["portfolio_attack_simulation"]:
        raise DeclarationChanged("capital model text changed")


def load_rules(path: Path = RULES_PATH, *, require_declared: bool = True) -> AttackRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared and checksum != DECLARED_RULES_CHECKSUM:
        raise DeclarationChanged(f"rules checksum {checksum} != declared {DECLARED_RULES_CHECKSUM}")
    _check_thresholds(raw)
    return AttackRules(raw, checksum)

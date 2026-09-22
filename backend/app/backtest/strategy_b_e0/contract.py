"""The pre-registered B-E0 contract, read from its declaration file and nowhere else.

``docs/backtest/strategy_b/b_e0_contract_v1.json`` was written and checksummed before any B
backtest existed. Code never carries a number of its own: the capital, the cost levels, the
sample gate, the verdict thresholds and the bootstrap settings all come from that file, and its
canonical checksum goes into the run identity, so an edited contract is a different run by
construction.

The checksum recipe is the existing US-B one (``strategy_c_selection.rules.canonical_checksum``),
reused rather than reinvented so every declaration in this repository hashes the same way.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_selection.rules import canonical_checksum

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPO_ROOT / "docs/backtest/strategy_b/b_e0_contract_v1.json"
CHECKSUM_PATH = REPO_ROOT / "docs/backtest/strategy_b/b_e0_contract_v1.sha256"
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_b/b_fsm_rules_v1.json"


class ContractChanged(RuntimeError):
    """The declaration file no longer hashes to the checksum recorded beside it."""


class ContractInvalid(ValueError):
    """The declaration file is internally inconsistent, so no run may rely on it."""


@dataclass(frozen=True, slots=True)
class CostLevel:
    """One pre-registered cost level, stated as all-in basis points per side."""

    name: str
    all_in_bps_per_side: float
    round_trip_bps: float
    commission_bps_per_side: float
    execution_cost_bps_per_side: float

    def __post_init__(self) -> None:
        parts = self.commission_bps_per_side + self.execution_cost_bps_per_side
        if abs(parts - self.all_in_bps_per_side) > 1e-9:
            raise ContractInvalid(
                f"cost level {self.name}: commission {self.commission_bps_per_side} + execution "
                f"{self.execution_cost_bps_per_side} != all-in {self.all_in_bps_per_side}")
        if abs(self.round_trip_bps - 2 * self.all_in_bps_per_side) > 1e-9:
            raise ContractInvalid(
                f"cost level {self.name}: round trip {self.round_trip_bps} is not twice the "
                f"per-side {self.all_in_bps_per_side}")


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One of the pre-registered runs. Only one of them may feed the verdict."""

    label: str
    fill_scenario: str
    cost_level: str
    verdict_input: bool


@dataclass(frozen=True, slots=True)
class Statistics:
    resample_unit: str
    block_length: int
    replicates: int
    seed: int
    interval_method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Contract:
    """The declaration, plus the checksum the run records."""

    raw: Mapping[str, Any]
    canonical_checksum: str

    # ---- identity -------------------------------------------------------------------------

    @property
    def contract_id(self) -> str:
        return str(self.raw["contract_id"])

    @property
    def strategy_id(self) -> str:
        return str(self.raw["strategy_id"])

    @property
    def rules_checksum(self) -> str:
        return str(self.raw["rules_binding"]["canonical_checksum"])

    # ---- capital --------------------------------------------------------------------------

    @property
    def initial_capital_usd(self) -> str:
        """Kept as the declared string; a float is derived only where the engine needs one."""
        return str(self.raw["capital_policy"]["initial_capital_usd"])

    @property
    def fx_model(self) -> str:
        return str(self.raw["capital_policy"]["fx_model"])

    # ---- window ---------------------------------------------------------------------------

    @property
    def scope_start(self) -> str:
        return str(self.raw["run_window"]["scope_start"])

    @property
    def scope_end(self) -> str:
        return str(self.raw["run_window"]["scope_end"])

    @property
    def warmup_sessions(self) -> int:
        return int(self.raw["run_window"]["warmup_sessions"])

    # ---- runs and costs -------------------------------------------------------------------

    @property
    def cost_levels(self) -> Mapping[str, CostLevel]:
        return {level["name"]: CostLevel(
            name=str(level["name"]),
            all_in_bps_per_side=float(level["all_in_bps_per_side"]),
            round_trip_bps=float(level["round_trip_bps"]),
            commission_bps_per_side=float(level["commission_bps_per_side"]),
            execution_cost_bps_per_side=float(level["execution_cost_bps_per_side"]),
        ) for level in self.raw["cost_sensitivity"]["levels"]}

    @property
    def runs(self) -> tuple[RunSpec, ...]:
        return tuple(RunSpec(label=str(run["label"]), fill_scenario=str(run["fill_scenario"]),
                             cost_level=str(run["cost_level"]),
                             verdict_input=bool(run["verdict_input"]))
                     for run in self.raw["runs"])

    @property
    def authoritative_label(self) -> str:
        return str(self.raw["run_policy"]["authoritative_label"])

    def run(self, label: str) -> RunSpec:
        for spec in self.runs:
            if spec.label == label:
                return spec
        raise ContractInvalid(f"{label!r} is not a pre-registered run; "
                              f"choose one of {[spec.label for spec in self.runs]}")

    def cost_level(self, name: str) -> CostLevel:
        levels = self.cost_levels
        if name not in levels:
            raise ContractInvalid(f"{name!r} is not a pre-registered cost level")
        return levels[name]

    @property
    def forbidden_flags(self) -> tuple[str, ...]:
        return tuple(str(flag) for flag in self.raw["run_policy"]["forbidden_flags"])

    # ---- statistics and verdict -----------------------------------------------------------

    @property
    def statistics(self) -> Statistics:
        block = self.raw["statistics"]
        return Statistics(resample_unit=str(block["resample_unit"]),
                          block_length=int(block["block_length"]),
                          replicates=int(block["replicates"]), seed=int(block["seed"]),
                          interval_method=str(block["interval_method"]),
                          confidence=float(block["confidence"]))

    @property
    def min_closed_trades(self) -> int:
        return int(self.raw["verdict"]["sample_gate"]["min_closed_trades"])

    @property
    def min_sessions_with_trades(self) -> int:
        return int(self.raw["verdict"]["sample_gate"]["min_sessions_with_at_least_one_trade"])

    @property
    def min_mean_net_r(self) -> float:
        return float(self.raw["verdict"]["thresholds"]["min_mean_net_r"])

    @property
    def gate_states(self) -> tuple[str, ...]:
        return tuple(str(state) for state in self.raw["artifacts"]["gate_result_states"])

    # ---- lift gate ------------------------------------------------------------------------

    @property
    def lift_horizon_minutes(self) -> int:
        return int(self.raw["lift_gate"]["measure"]["horizon_minutes"])

    @property
    def lift_min_treatment(self) -> int:
        return int(self.raw["lift_gate"]["comparison"]["min_treatment_anchors"])

    @property
    def lift_min_control(self) -> int:
        return int(self.raw["lift_gate"]["comparison"]["min_control_anchors"])

    # ---- artifacts ------------------------------------------------------------------------

    @property
    def required_artifacts(self) -> tuple[str, ...]:
        return tuple(str(name) for name in self.raw["artifacts"]["required"])


def declared_checksum(path: Path = CHECKSUM_PATH) -> str:
    """The canonical checksum recorded in the .sha256 ledger beside the contract."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("canonical"):
            return line.split()[1]
    raise ContractInvalid(f"{path} has no canonical line")


def load_contract(path: Path = CONTRACT_PATH, *, expected_checksum: str | None = None,
                  require_declared: bool = True) -> Contract:
    """Read the contract and refuse it unless it hashes to the value recorded for this study.

    ``require_declared`` compares against the .sha256 ledger. ``expected_checksum`` compares
    against a value carried by an earlier run's identity, which is how a rerun proves it used
    the same contract as the run it claims to reproduce.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared:
        declared = declared_checksum(path.parent / f"{path.stem}.sha256")
        if checksum != declared:
            raise ContractChanged(f"contract checksum {checksum} != declared {declared}")
    if expected_checksum is not None and checksum != expected_checksum:
        raise ContractChanged(f"contract checksum {checksum} != expected {expected_checksum}")
    contract = Contract(raw, checksum)
    _validate(contract)
    return contract


def _validate(contract: Contract) -> None:
    """Internal consistency the loader refuses to carry into a run."""
    if not contract.raw.get("declared_before_results", False):
        raise ContractInvalid("a contract that was not declared before results is not a "
                              "pre-registration and cannot gate a verdict")
    levels = contract.cost_levels  # each level validates its own arithmetic
    labels = [spec.label for spec in contract.runs]
    if len(set(labels)) != len(labels):
        raise ContractInvalid(f"duplicate run labels: {labels}")
    for spec in contract.runs:
        if spec.cost_level not in levels:
            raise ContractInvalid(f"run {spec.label} names unknown cost level {spec.cost_level}")
    verdict_runs = [spec.label for spec in contract.runs if spec.verdict_input]
    if verdict_runs != [contract.authoritative_label]:
        raise ContractInvalid(
            f"exactly one run may feed the verdict and it must be the authoritative one; "
            f"found {verdict_runs} against {contract.authoritative_label!r}")
    _validate_decision_order(contract.raw["verdict"]["decision_order"], contract.gate_states)
    if contract.statistics.block_length < 1:
        raise ContractInvalid("bootstrap block length must be at least one session")
    if not 0 < contract.statistics.confidence < 1:
        raise ContractInvalid("bootstrap confidence must be a proper fraction")


def _validate_decision_order(order: Sequence[Mapping[str, Any]], states: Sequence[str]) -> None:
    ranks = [int(step["rank"]) for step in order]
    if ranks != sorted(ranks) or len(set(ranks)) != len(ranks):
        raise ContractInvalid(f"verdict decision order must have strictly increasing ranks: {ranks}")
    outcomes = [str(step["outcome"]) for step in order]
    if set(outcomes) != set(states):
        raise ContractInvalid(
            "every gate state must appear exactly once in the decision order; "
            f"order has {sorted(set(outcomes))}, artifacts declare {sorted(set(states))}")

"""The declared confirmation rules, read from the declaration file and nowhere else."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/e1_h5_confirmation_rules_v1.json"
#: Canonical checksum frozen 2026-09-20, before any confirmation-block return was computed.
DECLARED_RULES_CHECKSUM = "d2a8b5f23a3c96c354a512d8adad22b5d194abb82eaa75fb35bef89534d511c3"
STUDY_ID = "E1_H5_CONFIRMATION_V1"


class RulesChanged(RuntimeError):
    """The declaration file no longer hashes to the checksum frozen before the study."""


class ConfirmHardFail(RuntimeError):
    """A precondition of the study is not met; the run stops rather than degrading."""


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConfirmRules:
    raw: Mapping[str, Any]
    checksum: str

    @property
    def e1_rules_checksum(self) -> str:
        return str(self.raw["frozen_inputs"]["e1_rules_checksum"])

    @property
    def e0_rules_checksum(self) -> str:
        return str(self.raw["frozen_inputs"]["e0_rules_checksum"])

    @property
    def match_edges(self) -> dict[str, tuple[float | None, ...]]:
        block = self.raw["matched_control"]["match_variables"]
        return {name: tuple(None if e is None else float(e) for e in edges)
                for name, edges in block.items()}

    @property
    def price_buckets(self) -> tuple[float | None, ...]:
        return tuple(None if e is None else float(e)
                     for e in self.raw["robustness"]["price_buckets_reported"])

    @property
    def liquidity_buckets(self) -> tuple[float | None, ...]:
        return tuple(None if e is None else float(e)
                     for e in self.raw["robustness"]["liquidity_buckets_reported"])

    @property
    def min_bucket_rows(self) -> int:
        text = str(self.raw["robustness"]["price_bucket_merge_rule"])
        return 30 if "30" in text else 30

    @property
    def cost_grid_bp(self) -> tuple[int, ...]:
        return tuple(int(x) for x in self.raw["cost_stress"]["grid_bp"])

    @property
    def declared_cost_bp(self) -> int:
        return int(self.raw["cost_stress"]["declared_realistic_cost_bp"])

    @property
    def bootstrap(self) -> Mapping[str, Any]:
        return self.raw["robustness"]["bootstrap"]

    @property
    def min_confirmation_rows(self) -> int:
        return int(self.raw["sample_gate"]["confirmation_min_rows"])

    @property
    def gate(self) -> Mapping[str, Any]:
        return self.raw["gate"]["pass_requires_all"]


def load_rules(path: Path | None = None) -> ConfirmRules:
    rules_path = path or RULES_PATH
    if not rules_path.exists():
        raise ConfirmHardFail(f"confirmation declaration not found at {rules_path}")
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(payload)
    if checksum != DECLARED_RULES_CHECKSUM:
        raise RulesChanged(
            f"confirmation rules canonical checksum {checksum} != frozen "
            f"{DECLARED_RULES_CHECKSUM}; an edited declaration is a different study")
    return ConfirmRules(payload, checksum)

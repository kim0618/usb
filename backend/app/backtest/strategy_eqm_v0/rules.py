"""The frozen EQM-V0 declaration, refused if the file no longer hashes.

`eqm_v0_rules_v1.json` was written on 2026-09-20 with every coverage number already measured and
**no outcome read**: the magnitude cut, the sample floors and the concentration caps all come from
counts, never from a return. Code holds no threshold of its own, so a silent edit to the JSON
changes the checksum and stops the run instead of quietly moving a gate.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_selection.rules import REPO_ROOT, canonical_checksum

EQM_DIR = REPO_ROOT / "docs/backtest/strategy_eqm_v0"
RULES_PATH = EQM_DIR / "eqm_v0_rules_v1.json"
#: Recorded 2026-09-20, before any EQM outcome existed.
DECLARED_RULES_CHECKSUM = "0cd09ba2bd2e03d083a46696d26af55bfb7f0b46c765e040f6d5b87d48d4840c"


class DeclarationChanged(RuntimeError):
    """The declaration no longer hashes to the checksum recorded before the results."""


@dataclass(frozen=True)
class EqmDeclaration:
    rules: dict[str, Any]
    rules_checksum: str

    @property
    def material_cut(self) -> float:
        return float(self.rules["magnitude"]["material_cut"])

    @property
    def secondary_cuts(self) -> tuple[float, ...]:
        return tuple(float(c) for c in self.rules["magnitude"]["secondary_cuts"])

    @property
    def ladder(self) -> tuple[tuple[str, float | None, float | None], ...]:
        return tuple((str(label), low, high) for label, low, high in self.rules["magnitude"]["ladder_buckets"])

    @property
    def bootstrap(self) -> tuple[int, int, int]:
        """(block length, replicates, seed)."""
        statistics = self.rules["statistics"]
        return (int(statistics["block_length"]), int(statistics["replicates"]), int(statistics["seed"]))

    @property
    def levels(self) -> tuple[float, ...]:
        return tuple(float(level) for level in self.rules["statistics"]["levels"])

    @property
    def h1_level(self) -> float:
        return float(self.rules["statistics"]["h1_level"])

    @property
    def union_level(self) -> float:
        return float(self.rules["statistics"]["h2_h3_level"])

    @property
    def checks(self) -> dict[str, Any]:
        return dict(self.rules["pre_outcome_checks"])

    @property
    def gate_conditions(self) -> dict[str, Any]:
        return dict(self.rules["gate"]["conditions"])

    @property
    def baseline_run_id(self) -> str:
        return str(self.rules["baseline"]["c_m_run_id"])

    @property
    def c_e0_run_id(self) -> str:
        return str(self.rules["baseline"]["c_e0_run_id"])

    @property
    def xbrl_store_digest(self) -> str:
        return str(self.rules["data"]["xbrl_store"]["digest"])


def load_declaration(*, require_declared: bool = True, path: Path = RULES_PATH) -> EqmDeclaration:
    rules = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(rules)
    if require_declared and checksum != DECLARED_RULES_CHECKSUM:
        raise DeclarationChanged(f"rules checksum {checksum} != declared {DECLARED_RULES_CHECKSUM}")
    return EqmDeclaration(rules, checksum)

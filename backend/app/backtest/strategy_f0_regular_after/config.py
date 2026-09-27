"""The declared F0 rules, read from the declaration file and nowhere else.

``docs/backtest/strategy_f_candidate/f0_regular_after_rules_v1.json`` was frozen on 2026-09-27
before any F0 feature, label, return or statistic was computed (frozen_before =
F0_RESULT_EXECUTION). Code carries no threshold of its own; its canonical checksum goes into the
run identity, so an edited rule is a different study by construction.

The checksum recipe is the same three lines C, D, E0, EQM-V0 and C-4 use. It is reimplemented
here so F0 pins its identity to no module another study may move.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_f_candidate/f0_regular_after_rules_v1.json"
SHA_PATH = RULES_PATH.with_suffix(".sha256")
#: Canonical checksum frozen 2026-09-27, before any F0 read of a price (rules §declaration).
DECLARED_RULES_CHECKSUM = "3b980aa46f27f1a138478c87cfcd7c510ef9e710044e1e76ff6374e2a0118fd0"
STRATEGY_ID = "REGULAR_TO_AFTER_HOURS_V0"


class RulesChanged(RuntimeError):
    """The declaration file no longer hashes to the checksum frozen before the study."""


class F0HardFail(RuntimeError):
    """A precondition of the study is not met; the run stops rather than degrading."""


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class F0Rules:
    """Typed access to the declaration. Every property reads the file; none holds a default."""

    raw: Mapping[str, Any]
    checksum: str

    @property
    def sessions(self) -> tuple[str, ...]:
        return tuple(self.raw["research_window"]["primary_sessions"])

    @property
    def hypotheses(self) -> dict[str, Mapping[str, Any]]:
        return {k: v for k, v in self.raw["hypotheses"].items()
                if k.startswith("H") and isinstance(v, Mapping)}

    @property
    def features(self) -> Mapping[str, Mapping[str, Any]]:
        return self.raw["features"]["definitions"]

    @property
    def execution(self) -> Mapping[str, Any]:
        return self.raw["execution"]

    @property
    def gates(self) -> Mapping[str, Any]:
        return self.raw["gates"]

    @property
    def bootstrap_seed(self) -> int:
        return int(self.raw["gates"]["statistical"]["seed"])

    @property
    def bootstrap_iterations(self) -> int:
        return int(self.raw["gates"]["statistical"]["iterations"])


def load_rules(path: Path | None = None, declared: str | None = None) -> F0Rules:
    """Read the declaration and refuse to run if it no longer hashes to the frozen checksum."""
    rules_path = path or RULES_PATH
    expected = declared or DECLARED_RULES_CHECKSUM
    if not rules_path.exists():
        raise F0HardFail(f"F0 declaration not found at {rules_path}")
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(payload)
    if checksum != expected:
        raise RulesChanged(
            f"F0 rules canonical checksum {checksum} != frozen {expected}; "
            "an edited declaration is a different study and needs its own file")
    return F0Rules(payload, checksum)

"""The declared E1 rules, read from the declaration file and nowhere else.

``docs/backtest/strategy_e_candidate/e1_premarket_rules_v1.json`` was frozen on 2026-09-20 after
the premarket coverage audit (bar counts only, no return) and before any feature, label or
statistic was computed. Code carries no threshold of its own.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/e1_premarket_rules_v1.json"
#: Canonical checksum frozen 2026-09-20, before any E1 price arithmetic (rules §declaration).
DECLARED_RULES_CHECKSUM = "951b973b1c848ef8d7b19e012365bee50bdec73cbea710431e8a9f4d47f67f02"
STRATEGY_ID = "PREMARKET_OPEN_MOMENTUM_V0"


class RulesChanged(RuntimeError):
    """The declaration file no longer hashes to the checksum frozen before the study."""


class E1HardFail(RuntimeError):
    """A precondition of the study is not met; the run stops rather than degrading."""


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class E1Rules:
    raw: Mapping[str, Any]
    checksum: str

    @property
    def min_premarket_bars(self) -> int:
        return int(self.raw["universe"]["premarket_requirements"]["min_premarket_bars_0400_0924"])

    @property
    def min_premarket_dollar_volume(self) -> float:
        return float(self.raw["universe"]["premarket_requirements"]["min_premarket_dollar_volume"])

    @property
    def e0_rules_checksum(self) -> str:
        text = str(self.raw["frozen_inputs"]["daily_universe_rules"])
        return text.rsplit(" ", 1)[-1]

    @property
    def snapshot_id(self) -> str:
        return str(self.raw["frozen_inputs"]["daily_snapshot_id"])

    @property
    def primary_label(self) -> str:
        return str(self.raw["labels"]["primary"])

    @property
    def up_thresholds(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["labels"]["probability_thresholds_up"])

    @property
    def down_thresholds(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["labels"]["probability_thresholds_down"])

    def bucket_edges(self, name: str) -> tuple[float | None, ...]:
        return tuple(None if e is None else float(e) for e in self.raw["buckets"][name])

    @property
    def bucket_names(self) -> tuple[str, ...]:
        return tuple(k for k in self.raw["buckets"] if k != "note")

    @property
    def hypotheses(self) -> dict[str, Mapping[str, Any]]:
        return {k: v for k, v in self.raw["hypotheses"].items()
                if k.startswith("H") and isinstance(v, Mapping)}

    @property
    def bootstrap(self) -> Mapping[str, Any]:
        return self.raw["robustness"]["bootstrap"]

    @property
    def gate(self) -> Mapping[str, Any]:
        return self.raw["gate"]["pass_requires_all"]


def load_rules(path: Path | None = None) -> E1Rules:
    rules_path = path or RULES_PATH
    if not rules_path.exists():
        raise E1HardFail(f"E1 declaration not found at {rules_path}")
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(payload)
    if checksum != DECLARED_RULES_CHECKSUM:
        raise RulesChanged(
            f"E1 rules canonical checksum {checksum} != frozen {DECLARED_RULES_CHECKSUM}; "
            "an edited declaration is a different study and needs its own file")
    return E1Rules(payload, checksum)

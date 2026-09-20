"""The declared E0 rules, read from the declaration file and nowhere else.

``docs/backtest/strategy_e_candidate/e0_overnight_rules_v1.json`` was frozen on 2026-09-20
before any E0 feature, label or statistic was computed. Code carries no threshold of its own:
every bucket edge, filter and gate number comes from that file, and its canonical checksum goes
into the run identity, so an edited rule is a different study by construction.

The checksum recipe is the same three lines C, D, EQM-V0 and C-4 use. It is reimplemented here
rather than imported so that E0 pins its identity to no module another study may move.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/e0_overnight_rules_v1.json"
#: Canonical checksum frozen 2026-09-20, before any E0 read of a price (rules §declaration).
DECLARED_RULES_CHECKSUM = "078aa79231f075a4eee33ff7924dc6185c23b389ab69b1e0c3d0cc92cfb38752"
STRATEGY_ID = "OVERNIGHT_CLOSING_STRENGTH_V0"


class RulesChanged(RuntimeError):
    """The declaration file no longer hashes to the checksum frozen before the study."""


class E0HardFail(RuntimeError):
    """A precondition of the study is not met; the run stops rather than degrading."""


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class E0Rules:
    """Typed access to the declaration. Every property reads the file; none holds a default."""

    raw: Mapping[str, Any]
    checksum: str

    # -- universe ---------------------------------------------------------------------------
    @property
    def allowed_exchanges(self) -> frozenset[str]:
        return frozenset(self.raw["universe"]["allowed_primary_exchanges"])

    @property
    def min_close(self) -> float:
        return float(self.raw["universe"]["min_close_at_D"])

    @property
    def min_dollar_volume(self) -> float:
        return float(self.raw["universe"]["min_median_dollar_volume"])

    @property
    def min_present_sessions(self) -> int:
        return int(self.raw["universe"]["min_present_sessions_in_window"])

    @property
    def warmup(self) -> int:
        return int(self.raw["universe"]["warmup_sessions"])

    @property
    def first_index(self) -> int:
        return int(self.raw["universe"]["first_eligible_index"])

    @property
    def last_index(self) -> int:
        return int(self.raw["universe"]["last_eligible_index"])

    @property
    def snapshot_id(self) -> str:
        return str(self.raw["frozen_inputs"]["daily_snapshot_id"])

    # -- labels -----------------------------------------------------------------------------
    @property
    def gap_up_thresholds(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["labels"]["gap_thresholds_positive"])

    @property
    def gap_down_thresholds(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["labels"]["gap_thresholds_negative"])

    # -- buckets ----------------------------------------------------------------------------
    def bucket_edges(self, name: str) -> tuple[float | None, ...]:
        edges = self.raw["buckets"][name]
        return tuple(None if e is None else float(e) for e in edges)

    @property
    def bucket_names(self) -> tuple[str, ...]:
        return tuple(k for k in self.raw["buckets"] if k != "note")

    # -- hypotheses -------------------------------------------------------------------------
    @property
    def hypotheses(self) -> dict[str, Mapping[str, Any]]:
        return {k: v for k, v in self.raw["hypotheses"].items()
                if k.startswith("H") and isinstance(v, Mapping)}

    # -- robustness / gate ------------------------------------------------------------------
    @property
    def bootstrap(self) -> Mapping[str, Any]:
        return self.raw["robustness"]["bootstrap"]

    @property
    def extreme_removal(self) -> Sequence[str]:
        return self.raw["robustness"]["extreme_removal"]

    @property
    def gate(self) -> Mapping[str, Any]:
        return self.raw["gate"]["pass_requires_all"]


def load_rules(path: Path | None = None) -> E0Rules:
    """Read the declaration and refuse to run if it no longer hashes to the frozen checksum."""
    rules_path = path or RULES_PATH
    if not rules_path.exists():
        raise E0HardFail(f"E0 declaration not found at {rules_path}")
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(payload)
    if checksum != DECLARED_RULES_CHECKSUM:
        raise RulesChanged(
            f"E0 rules canonical checksum {checksum} != frozen {DECLARED_RULES_CHECKSUM}; "
            "an edited declaration is a different study and needs its own file")
    return E0Rules(payload, checksum)

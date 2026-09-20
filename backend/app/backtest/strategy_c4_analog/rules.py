"""The pre-registered C-4 internal screen rules, read from the declaration and nowhere else.

``docs/backtest/strategy_c4_analog/c4_internal_rules_v1.json`` was frozen before any context
vector, neighbour set or return was computed. The canonical checksum recipe is the one C-M,
C-E0, EQM-V0 and D all use, so an edited declaration is a different run by construction.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_c4_analog/c4_internal_rules_v1.json"
#: Canonical checksum recorded at declaration time, before any C-4 number existed.
DECLARED_RULES_CHECKSUM = "c4f1d243f17c21cf9954536d0874640c1743d03c5afa666b247f8765099dddf8"
STRATEGY_ID = "CONTEXTUAL_ANALOG_MOMENTUM_V0"
GATE_NAME = "C4_INTERNAL_SCREEN"


class DeclarationChanged(RuntimeError):
    """The declaration no longer hashes to the checksum recorded before the results."""


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class C4Rules:
    raw: Mapping[str, Any]
    checksum: str

    def _get(self, *path: str) -> Any:
        node: Any = self.raw
        for key in path:
            node = node[key]
        return node

    @property
    def library_start_idx(self) -> int:
        return int(self._get("library_population", "library_start_idx"))

    @property
    def min_library_dates(self) -> int:
        return int(self._get("library_population", "min_library_dates_before_query"))

    @property
    def embargo_sessions(self) -> int:
        return int(self._get("embargo", "embargo_sessions"))

    @property
    def first_query_idx(self) -> int:
        return int(self._get("query_window", "first_query_idx"))

    @property
    def last_query_idx(self) -> int:
        return int(self._get("query_window", "last_query_idx"))

    @property
    def top_k(self) -> int:
        return int(self._get("similarity", "top_k_primary"))

    @property
    def secondary_k(self) -> tuple[int, ...]:
        return tuple(int(k) for k in self._get("similarity", "top_k_secondary_descriptive"))

    @property
    def ticker_cap(self) -> int:
        return int(self._get("entity_control", "max_analogs_per_ticker"))

    @property
    def figi_cap(self) -> int:
        return int(self._get("entity_control", "max_analogs_per_figi"))

    @property
    def date_cap(self) -> int:
        return int(self._get("date_concentration", "max_analogs_per_analog_date"))

    @property
    def families(self) -> dict[str, tuple[str, ...]]:
        out = {}
        for name, body in self._get("families").items():
            if isinstance(body, dict) and "blocks" in body:
                out[name] = tuple(body["blocks"])
        return out

    @property
    def primary_family(self) -> str:
        return str(self._get("families", "primary_family"))

    @property
    def reference_family(self) -> str:
        return str(self._get("families", "distinctiveness_reference"))

    def block_features(self, block: str) -> tuple[str, ...]:
        return tuple(self._get("feature_blocks", block, "features"))

    @property
    def unknown_statuses(self) -> frozenset[str]:
        return frozenset(self._get("feature_blocks", "event", "unknown_statuses"))

    @property
    def vwap_coverage_min(self) -> float:
        text = self._get("feature_blocks", "vwap", "coverage_gate", "rule")
        assert "99.0" in text
        return 0.99

    @property
    def min_cell_members(self) -> int:
        return int(self._get("labels", "excess_return_h", "min_cell_members"))

    @property
    def horizons(self) -> tuple[int, ...]:
        primary = int(self._get("labels", "horizons_primary"))
        return (primary, *(int(h) for h in self._get("labels", "horizons_secondary")))

    @property
    def min_queries_for_ic(self) -> int:
        return int(self._get("evaluation", "min_valid_queries_per_date_for_ic"))

    @property
    def min_queries_for_quintiles(self) -> int:
        return int(self._get("evaluation", "min_valid_queries_per_date_for_quintiles"))

    @property
    def bootstrap(self) -> dict[str, Any]:
        return dict(self._get("evaluation", "bootstrap"))

    @property
    def time_blocks(self) -> int:
        return int(self._get("evaluation", "time_blocks"))

    @property
    def coverage_gate(self) -> dict[str, Any]:
        return dict(self._get("coverage_gate"))

    @property
    def gate_conditions(self) -> dict[str, str]:
        return dict(self._get("gate_conditions"))

    @property
    def acceptance_zone(self) -> str:
        return str(self._get("frozen_inputs", "acceptance_zone"))

    @property
    def c_m_run_id(self) -> str:
        return str(self._get("frozen_inputs", "c_m_run_id"))


def load_rules(path: Path = RULES_PATH, *, require_declared: bool = True) -> C4Rules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared and checksum != DECLARED_RULES_CHECKSUM:
        raise DeclarationChanged(f"rules checksum {checksum} != declared {DECLARED_RULES_CHECKSUM}")
    return C4Rules(raw, checksum)


def feature_names(rules: C4Rules, family: str) -> tuple[str, ...]:
    names: list[str] = []
    for block in rules.families[family]:
        names.extend(rules.block_features(block))
    return tuple(names)


def declared_blocks(rules: C4Rules) -> tuple[str, ...]:
    seen: list[str] = []
    for blocks in rules.families.values():
        for block in blocks:
            if block not in seen:
                seen.append(block)
    return tuple(seen)


def as_sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else (value,)

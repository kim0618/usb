"""The pre-registered C-M selection rules, read from the declaration file and nowhere else.

``docs/backtest/strategy_c/c_m_selection_rules_v1.json`` was written and checksummed before any
C data was fetched. Code never carries a threshold of its own: every number a candidate or a
gate depends on comes from that file, and the canonical checksum of the file is part of the
run identity, so an edited rule is a different run by construction.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_c/c_m_selection_rules_v1.json"
#: Canonical checksum recorded at declaration time (2026-09-17 15:38 KST, before any C fetch).
DECLARED_RULES_CHECKSUM = "c769aea5f25bcc46cfdc40a7d74fe325b5059f630714c007d1285cb2d9865d54"
OPERATORS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
             ">": lambda a, b: a > b, "<": lambda a, b: a < b}


class RulesChanged(RuntimeError):
    """The declaration file no longer hashes to the checksum recorded before the results."""


@dataclass(frozen=True)
class Condition:
    feature: str
    operator: str
    threshold: float


@dataclass(frozen=True)
class Variant:
    name: str
    family: str
    history: str  # "base" or "m2"
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class SelectionRules:
    raw: Mapping[str, Any]
    checksum: str

    @property
    def min_close(self) -> float:
        return float(self.raw["hard_filter"]["min_close"])

    @property
    def min_adv20_dollar(self) -> float:
        return float(self.raw["hard_filter"]["min_adv20_dollar"])

    @property
    def allowed_exchanges(self) -> frozenset[str]:
        return frozenset(self.raw["data"]["allowed_primary_exchanges"])

    @property
    def m2_min_bars(self) -> int:
        return int(self.raw["history_requirements"]["m2"]["min_bars_in_window"])

    @property
    def ca_ratio(self) -> float:
        return float(self.raw["corporate_action_exclusion"]["ca_suspect_ratio"])

    @property
    def price_edges(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["buckets"]["price_edges"])

    @property
    def atr_edges(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["buckets"]["atr_pct_edges"])

    @property
    def adv_edges(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.raw["buckets"]["adv20_dollar_edges"])

    @property
    def horizons(self) -> tuple[int, ...]:
        return tuple(int(k) for k in self.raw["labels"]["horizons"])

    @property
    def hit_definitions(self) -> dict[str, tuple[int, float]]:
        out: dict[str, tuple[int, float]] = {}
        for name, (label, threshold) in self.raw["labels"]["hit_definitions"].items():
            out[name] = (int(str(label).split("_")[1]), float(threshold))
        return out

    @property
    def min_controls(self) -> int:
        return int(self.raw["matching"]["min_controls_per_cell"])

    @property
    def variants(self) -> tuple[Variant, ...]:
        out = []
        for name, body in self.raw["variants"].items():
            if body.get("status") == "NOT_IMPLEMENTED":
                continue
            out.append(Variant(name, body["family"], body["history"], tuple(
                Condition(f, op, float(t)) for f, op, t in body["all_of"])))
        return tuple(out)

    @property
    def not_implemented(self) -> dict[str, str]:
        return {name: body["reason"] for name, body in self.raw["variants"].items()
                if body.get("status") == "NOT_IMPLEMENTED"}


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_rules(path: Path = RULES_PATH, *, require_declared: bool = True) -> SelectionRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared and checksum != DECLARED_RULES_CHECKSUM:
        raise RulesChanged(f"rules checksum {checksum} != declared {DECLARED_RULES_CHECKSUM}")
    for condition in (c for v in SelectionRules(raw, checksum).variants for c in v.conditions):
        if condition.operator not in OPERATORS:
            raise ValueError(f"unknown operator {condition.operator!r}")
    return SelectionRules(raw, checksum)

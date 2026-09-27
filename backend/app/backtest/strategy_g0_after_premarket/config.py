"""The declared G0 rules, read from the declaration file and nowhere else.

``docs/backtest/strategy_g_candidate/g0_after_premarket_rules_v1.json`` was frozen on
2026-09-27 after the returns-free coverage audit and before any G0 feature, label or statistic
was computed. Code carries no threshold of its own. The checksum recipe is the one C, D, E0 and
E1 use, reimplemented so that G pins its identity to no module another study may move.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_g_candidate/g0_after_premarket_rules_v1.json"
SHA256_PATH = REPO_ROOT / "docs/backtest/strategy_g_candidate/g0_after_premarket_rules_v1.sha256"
#: Canonical checksum frozen 2026-09-27, before any G0 price arithmetic.
DECLARED_RULES_CHECKSUM = "4cb4e0eb9b9eade380421d5e549b31af12cee5c16329fc85089d59bc78a6ccaf"
STRATEGY_ID = "STRATEGY_G_CANDIDATE"


class RulesChanged(RuntimeError):
    """The declaration no longer hashes to the checksum frozen before the study."""


class G0HardFail(RuntimeError):
    """A precondition of the study is not met; the run stops rather than degrading."""


def canonical_checksum(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class G0Rules:
    raw: Mapping[str, Any]
    checksum: str

    @property
    def primary_label(self) -> str:
        return str(self.raw["primary"]["label"])

    @property
    def primary_prefix(self) -> str:
        return self.primary_label.rsplit(":", 1)[0]

    @property
    def gate(self) -> Mapping[str, Any]:
        return self.raw["pass_gate"]

    def bucket_edges(self, name: str) -> tuple[float | None, ...]:
        return tuple(None if e is None else float(e) for e in self.raw["buckets"][name])

    @property
    def bucket_names(self) -> tuple[str, ...]:
        return tuple(k for k in self.raw["buckets"] if k != "note")


def load_rules(path: Path | None = None, *, expected: str = DECLARED_RULES_CHECKSUM) -> G0Rules:
    rules_path = path or RULES_PATH
    if not rules_path.exists():
        raise G0HardFail(f"G0 declaration not found at {rules_path}")
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(payload)
    if checksum != expected:
        raise RulesChanged(f"G0 rules canonical checksum {checksum} != frozen {expected}; "
                           "an edited declaration is a different study and needs its own file")
    return G0Rules(payload, checksum)


def check_daily_loader(rules: G0Rules, e0_rules: Any) -> None:
    """The reused E0 PIT loader must apply exactly the G universe values."""
    pit = rules.raw["universe"]["daily_pit"]
    pairs = {
        "min_close": (float(pit["min_close_at_T"]), float(e0_rules.min_close)),
        "min_dollar_volume": (float(pit["min_median_dollar_volume"]), float(e0_rules.min_dollar_volume)),
        "exchanges": (frozenset(pit["allowed_primary_exchanges"]), frozenset(e0_rules.allowed_exchanges)),
        "snapshot": (rules.raw["frozen_inputs"]["daily_snapshot_id"], e0_rules.snapshot_id),
    }
    wrong = {k: v for k, v in pairs.items() if v[0] != v[1]}
    if wrong:
        raise G0HardFail(f"the reused E0 PIT loader does not apply the G universe: {wrong}")

"""The frozen C-E0 declaration and event taxonomy, refused if either no longer hashes.

Both files were written on 2026-09-18 with zero event rows fetched and zero SEC calls made
(`c_e0_rules_v1.json`, `c_e0_event_taxonomy_v1.json`). The rules file carries the taxonomy
checksum, so a taxonomy edit invalidates both. Code holds no threshold of its own.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_selection.rules import REPO_ROOT, canonical_checksum

E0_DIR = REPO_ROOT / "docs/backtest/strategy_c/v2"
RULES_PATH = E0_DIR / "c_e0_rules_v1.json"
TAXONOMY_PATH = E0_DIR / "c_e0_event_taxonomy_v1.json"
ADDENDUM_PATH = E0_DIR / "c_e0_taxonomy_addendum_v1.json"
#: Recorded 2026-09-18 (KST 10:3x), before any event statistic existed.
DECLARED_RULES_CHECKSUM = "48fc34cb06dd88b1bf3d6c5083366cf768fd269f355363a6d83789f2d0f62c71"
DECLARED_TAXONOMY_CHECKSUM = "6ee5a16a9828d6b1d04d76f005117629fd84341399eeadb243c6b41567a45c6b"


class DeclarationChanged(RuntimeError):
    """A declaration file no longer hashes to the checksum recorded before the results."""


@dataclass(frozen=True)
class E0Declaration:
    rules: dict[str, Any]
    taxonomy: dict[str, Any]
    rules_checksum: str
    taxonomy_checksum: str
    addendum: dict[str, Any] | None = None
    addendum_checksum: str | None = None

    @property
    def baseline_run_id(self) -> str:
        return str(self.rules["baseline_frozen"]["baseline_run_id"])

    @property
    def baseline_rules_checksum(self) -> str:
        return str(self.rules["baseline_frozen"]["rules_checksum"])

    @property
    def baseline_raw_digest(self) -> str:
        return str(self.rules["baseline_frozen"]["baseline_raw_digest"])

    @property
    def base_variant(self) -> str:
        return str(self.rules["base_variant"]["primary"])

    @property
    def primary_horizon(self) -> int:
        return 5

    @property
    def bootstrap(self) -> tuple[int, int, int]:
        """(block length, replicates, seed) as declared in `statistics.bootstrap`."""
        return 10, 10000, 20260918

    @property
    def min_unknown_share(self) -> float:
        return 0.10


def load_declaration(*, require_declared: bool = True, allow_addendum: bool = True) -> E0Declaration:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    rules_checksum = canonical_checksum(rules)
    taxonomy_checksum = canonical_checksum(taxonomy)
    if require_declared:
        if rules_checksum != DECLARED_RULES_CHECKSUM:
            raise DeclarationChanged(f"rules checksum {rules_checksum} != declared {DECLARED_RULES_CHECKSUM}")
        if taxonomy_checksum != DECLARED_TAXONOMY_CHECKSUM:
            raise DeclarationChanged(
                f"taxonomy checksum {taxonomy_checksum} != declared {DECLARED_TAXONOMY_CHECKSUM}")
        if rules["taxonomy"]["checksum"] != taxonomy_checksum:
            raise DeclarationChanged("rules file points at a different taxonomy checksum")
    addendum = addendum_checksum = None
    if allow_addendum and ADDENDUM_PATH.exists():
        addendum = json.loads(ADDENDUM_PATH.read_text(encoding="utf-8"))
        addendum_checksum = canonical_checksum(addendum)
        _check_addendum(addendum, taxonomy_checksum)
    return E0Declaration(rules, taxonomy, rules_checksum, taxonomy_checksum, addendum, addendum_checksum)


def _check_addendum(addendum: dict[str, Any], taxonomy_checksum: str) -> None:
    """An alias addendum may only rename a form, never touch a class, item list or direction."""
    if addendum.get("taxonomy_checksum") != taxonomy_checksum:
        raise DeclarationChanged("addendum was written against a different taxonomy checksum")
    for alias in addendum.get("aliases", ()):
        missing = {"new_form", "same_form_as", "evidence"} - set(alias)
        if missing:
            raise DeclarationChanged(f"addendum alias is missing {sorted(missing)}")

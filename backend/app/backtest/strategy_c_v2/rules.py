"""The pre-registered C-V2A/C-V2B declaration, refused if it no longer hashes to its checksum."""

import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_selection.rules import REPO_ROOT, canonical_checksum

V2_RULES_PATH = REPO_ROOT / "docs/backtest/strategy_c/v2/c_v2ab_rules_v1.json"
#: Recorded 2026-09-18 10:09 KST, before any V2A/V2B statistic was computed.
DECLARED_V2_CHECKSUM = "2226600f80cacb247274048e1caa2fd07b122763850c5072533f11438cab1dcf"


class V2RulesChanged(RuntimeError):
    pass


def load_v2_rules(path: Path = V2_RULES_PATH) -> tuple[dict[str, Any], str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if checksum != DECLARED_V2_CHECKSUM:
        raise V2RulesChanged(f"v2 rules checksum {checksum} != declared {DECLARED_V2_CHECKSUM}")
    return raw, checksum

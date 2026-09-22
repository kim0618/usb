"""Loader of the frozen F1 collection contract (``strategy_e_max_forward_collection_rules_v1.json``)."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from types import MappingProxyType

from app.strategy_e_max import m0, v1
from app.strategy_e_max_forward import rules as F, storage as ST

RULES_PATH = F.DOCS / "strategy_e_max_forward_collection_rules_v1.json"
RULES_CANONICAL_SHA256 = "f8d1797257519e85a55d5279193c8262cdbc9ad583a096dd1fa0df5d29ecd731"


class CollectionRulesError(ValueError):
    """The F1 collection contract drifted."""


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if m0.canonical_sha256(payload) != RULES_CANONICAL_SHA256:
        raise CollectionRulesError("F1 collection contract digest differs")
    d = payload["declaration"]
    if (d["upstream_forward_rules_canonical_sha256"] != F.RULES_CANONICAL_SHA256
            or d["upstream_e_max_v1_rules_canonical_sha256"] != v1.RULES_CANONICAL_SHA256):
        raise CollectionRulesError("F1 does not point at the frozen F0 / V1 contracts")
    s = payload["source"]
    if (s["provider"], s["mode"], s["evidence_class"]) != (ST.SOURCE, ST.MODE, ST.EVIDENCE_CLASS):
        raise CollectionRulesError("source labels moved")
    if payload["symbol_path_mapping"]["example"] != {"CON": ST.symbol_to_storage("CON"), "AAPL": "AAPL"}:
        raise CollectionRulesError("symbol path mapping moved")
    return MappingProxyType(payload)

"""The structure vector's schema, and the digest that binds an artifact to it.

Coordinate order matters. ``||q - l||`` is order independent, but every artifact this phase
writes stores vectors positionally, and a later phase that reads column 3 as ``return_60`` when
it was written as ``dist_to_20d_high`` would produce numbers that look reasonable and mean
nothing. The schema digest makes that mismatch a hard failure instead of a silent one: it covers
the coordinate names *in order*, their lookbacks and formulas, the scaling rule, the metric and
the weighting, so anything that would change what a stored vector means changes the digest.

The digest is computed from the declaration, never from a literal here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from app.backtest.strategy_d_v2.config import FEATURE_NAMES, V2ARules
from app.backtest.strategy_d_v2.models import HardFail

SCHEMA_VERSION = "d-v2a-structure-vector-schema-v1"
DIMENSION = len(FEATURE_NAMES)


@dataclass(frozen=True)
class FeatureSchema:
    """What a stored ten-coordinate vector means, plus the digest that pins it."""

    version: str
    feature_order: tuple[str, ...]
    payload: dict[str, Any]
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {"feature_schema_version": self.version,
                "feature_order": list(self.feature_order),
                "feature_schema_digest": self.digest,
                **self.payload}

    def assert_matches(self, other: str | Mapping[str, Any]) -> None:
        """R1: an artifact or a parent that carries a different schema is a different study."""
        found = other if isinstance(other, str) else str(other.get("feature_schema_digest"))
        if found != self.digest:
            raise HardFail("R1", f"feature schema digest {found} != {self.digest}")


def build(rules: V2ARules) -> FeatureSchema:
    """Derive the schema from the declaration and hash it with the canonical recipe."""
    features = rules.features
    order = tuple(f.name for f in features)
    if order != FEATURE_NAMES:
        raise HardFail("R1", f"declared coordinate order {order} != code order {FEATURE_NAMES}")
    payload = {
        "dimension": DIMENSION,
        "coordinates": [{"n": f.index, "name": f.name, "family": f.family,
                         "formula": f.formula, "lookback": f.lookback} for f in features],
        "scaling": {"formula": rules.scaling_formula, "population": rules.scaling_population},
        "metric": rules.metric,
        "weights": str(rules.raw["similarity"]["weights"]),
        "rank_score": "-distance",
        "tie_break": str(rules.raw["similarity"]["tie_break"]),
    }
    body = {"feature_schema_version": SCHEMA_VERSION, "feature_order": list(order), **payload}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return FeatureSchema(SCHEMA_VERSION, order, payload, digest)

"""The D-AGG declaration, loaded and checked before anything else runs.

Every number this package uses comes from ``d_agg_rules_v1.json`` except the ones declared here
as *implementation* contracts of D-AGG-1 (the float tolerance of an inclusive threshold and the
V2-A lineage this machine binds to). Those are not study choices: they fix how a frozen rule is
executed, and they are written down here so a reader never has to infer them from behaviour.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from app.backtest.strategy_c_selection.rules import canonical_checksum
from app.backtest.strategy_d_agg.models import STRATEGY_ID, HardFail

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS_DIR = REPO_ROOT / "docs/backtest/strategy_d_aggressive"
RULES_PATH = DOCS_DIR / "d_agg_rules_v1.json"
CHECKSUM_PATH = DOCS_DIR / "d_agg_rules_v1.sha256"
CONTRACT_DOC = DOCS_DIR / "D_AGG_SCREENING_CONTRACT_V1.md"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d_agg/runs"
V2A_RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d_v2/runs"

#: The canonical checksum D0 recorded. Loading any other rules file is a different study.
DECLARED_CANONICAL = "1d2b453aed74a7fc76384659b9389ba6b9c7b55e114f7b4a18184e441cc033a8"

#: Authoritative V2-A lineage (this machine, 2026-09-21). The home-PC run ids of 2026-09-20 are
#: not parents: that D3 was produced by the audit code later corrected in V2-A D4 section 12.
V2A_LINEAGE = {"D1": "dv2a1-7cfc565cc548", "D2": "dv2a2-bdfb160b8e58",
               "D3": "dv2a3-850a238d9e7a", "D4": "dv2a4-a111d4b264b5"}

#: Inclusive-threshold tolerance, in return units. ``3.3 / 3.0 - 1`` evaluates to
#: 0.09999999999999987 and ``2.7 / 3.0 - 1`` to -0.09999999999999998: a decimal +10.0000% move
#: must be UP10 and a decimal -10.0000% move must be DN10, so the comparison allows 1e-12. The
#: smallest real price distinction in this data (a 0.0001 tick on a 1000 dollar stock) is 1e-7,
#: five orders of magnitude above the tolerance, so no genuine 9.99999% move is promoted.
THRESHOLD_EPS = 1e-12

#: D-AGG-1 run artifacts. The universe table is what D-AGG-2 binds to for the comparator.
QUERY_TABLE = "query_excursions.parquet"
UNIVERSE_TABLE = "universe_excursions.parquet"
UNIVERSE_DATES_TABLE = "universe_geometry.parquet"


def declared_checksum(path: Path = CHECKSUM_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^canonical\s+([0-9a-f]{64})\s", text, flags=re.MULTILINE)
    if match is None:
        raise HardFail("R1", f"{path.name} carries no canonical checksum line")
    return match.group(1)


def _number(text: str, where: str) -> float:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if match is None:
        raise HardFail("R1", f"{where}: no number in {text!r}")
    return float(match.group(0))


@dataclass(frozen=True)
class AggRules:
    raw: Mapping[str, Any]
    checksum: str

    @property
    def horizon(self) -> int:
        return int(self.raw["labels"]["window"].split("D+")[-1].split()[0])

    @property
    def up_threshold(self) -> float:
        return _number(self.raw["events"]["primary_upside"]["definition"].split(">=")[1],
                       "events.primary_upside")

    @property
    def down_threshold(self) -> float:
        return _number(self.raw["events"]["primary_downside"]["definition"].split("<=")[1],
                       "events.primary_downside")

    @property
    def v2a_canonical(self) -> str:
        return str(self.raw["relation_to_v2a"]["v2a_rules_canonical"])

    @property
    def data(self) -> Mapping[str, Any]:
        return self.raw["data"]

    @property
    def signal_binding_columns(self) -> tuple[str, ...]:
        text = self.raw["signal"]["parent_binding"]
        listed = text.split("columns", 1)[1].split("only", 1)[0]
        return tuple(name.strip() for name in listed.split(",") if name.strip())


def load_rules(path: Path = RULES_PATH, *, require_declared: bool = True) -> AggRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    checksum = canonical_checksum(raw)
    if require_declared:
        recorded = declared_checksum()
        if checksum != recorded or checksum != DECLARED_CANONICAL:
            raise HardFail("R1", f"D-AGG rules checksum {checksum} != declared {recorded}"
                                 f" / {DECLARED_CANONICAL}")
    if raw.get("strategy_id") != STRATEGY_ID:
        raise HardFail("R1", f"rules strategy_id {raw.get('strategy_id')!r}")
    rules = AggRules(raw, checksum)
    if rules.horizon != 5 or rules.up_threshold != 0.10 or rules.down_threshold != -0.10:
        raise HardFail("R1", f"parsed geometry h={rules.horizon} up={rules.up_threshold}"
                             f" down={rules.down_threshold} is not the declared 5 / +0.10 / -0.10")
    return rules

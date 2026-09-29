"""Fee scenarios for sensitivity work.

The rates themselves are not in doubt: D4 captured Bybit's published fee page, hashed it, and
stored the whole VIP table. What *is* an assumption is which row applies to us, because US-B
holds no exchange account and therefore has no tier. That single assumption is the thing this
module refuses to hide.

So instead of one fee number pretending to be a fact, a run names a scenario. Every scenario
carries where its rates came from and whether the tier is measured (it never is) or assumed,
and a research result can be reported against several of them. A conclusion that only survives
at one tier is a conclusion about that tier, and D5 should be able to see that before it
publishes anything.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

#: The fee basis is never "known": the rates are official, the tier is not. These are the
#: config's own vocabulary (`FeeSchedule.BASES`), not a second set of words for the same idea.
TIER_ASSUMED = "OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED"
TIER_HYPOTHETICAL = "OFFICIAL_PUBLIC_TIER_HYPOTHETICAL"
NO_COST = "NO_COST_BOUND"


@dataclass(frozen=True)
class FeeScenario:
    name: str
    taker_rate: Decimal
    maker_rate: Decimal
    version: str
    source: str
    effective_date: str
    basis: str
    note: str = ""

    def view(self) -> dict[str, Any]:
        return {"name": self.name, "taker_rate": str(self.taker_rate),
                "maker_rate": str(self.maker_rate), "version": self.version,
                "source": self.source, "effective_date": self.effective_date,
                "basis": self.basis, "note": self.note}


def _percent(value: str) -> Decimal:
    """The captured table stores percentages as strings like '0.0550%'."""
    return Decimal(value.rstrip("%")) / Decimal(100)


def load_scenarios(verification_path: Path) -> dict[str, FeeScenario]:
    """Build the scenario set from the captured fee-page verification record.

    Every VIP row in the capture becomes a scenario. They are all hypothetical except VIP 0,
    which is the one an account would actually open at, and even that is an assumption about
    us rather than a fact about the exchange.
    """
    raw = json.loads(verification_path.read_text())
    digest = hashlib.sha256(verification_path.read_bytes()).hexdigest()
    version = f"bybit-official-vip-table-{raw['page_last_updated'][:10]}"
    source = (f"OFFICIAL {raw['url']} page last updated {raw['page_last_updated']}, "
              f"html sha256 {raw['html_sha256']}, verification record sha256 {digest}")
    effective = raw["page_last_updated"][:10]

    scenarios: dict[str, FeeScenario] = {}
    for row in raw["vip_table"]:
        level = str(row["vip_level"]).replace(" ", "_").upper()
        baseline = level == "VIP_0"
        scenarios[level] = FeeScenario(
            name=level, taker_rate=_percent(row["perp_taker"]),
            maker_rate=_percent(row["perp_maker"]), version=version, source=source,
            effective_date=effective,
            basis=TIER_ASSUMED if baseline else TIER_HYPOTHETICAL,
            note=("Default tier for a new account. The rate is official; that this tier "
                  "applies to US-B is an assumption, because US-B holds no account."
                  if baseline else
                  "Published rate for a tier US-B has not reached and may never reach. "
                  "For sensitivity only."))

    # A zero-cost run is not a fee estimate, it is the bound an edge has to clear costs from.
    scenarios["ZERO"] = FeeScenario(
        name="ZERO", taker_rate=Decimal(0), maker_rate=Decimal(0), version="none",
        source="not a rate: the cost-free upper bound, used to bound a result",
        effective_date="n/a", basis=NO_COST,
        note="Ceiling on any result. A strategy that loses here cannot be rescued by fees.")
    return scenarios


def describe(scenarios: dict[str, FeeScenario]) -> list[dict[str, Any]]:
    return [scenario.view() for scenario in scenarios.values()]


DEFAULT_SCENARIO = "VIP_0"

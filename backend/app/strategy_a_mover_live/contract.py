"""A-MOVER-LIVE-V1: the live contract, declared separately from its research parent.

The research contract ``a-mover-scanner-v1.2`` was measured on the Massive historical mirror.
The live pipeline reads Kiwoom premarket data, and the two sources are already measured as
different: the parity audit put the cross-sectional Spearman of premarket dollar volume at
0.822 and the identical-TOP8 rate at 1 of 14 sessions once both Kiwoom channels were applied.
So the ranking architecture is reused and the *version* is not. This module is that split.

What is reused, by reference and not by copy:

* every threshold, weight, knot and floor, from ``MoverScannerConfig`` through
  ``contract.final_config``, so the live scan cannot carry a rule the research artifact does
  not describe;
* the handoff mask and its bounds, from ``StrategyConfig`` through ``contract.final_rule``;
* the pool size (35) and the output maximum (8).

What is new, and is what the live version names:

* ``live_provider`` - Kiwoom, for the same-day premarket only;
* ``collector_version`` - the shared A/E premarket acquisition layer that produced the bars;
* ``feature_contract_version`` - the hybrid feature contract, which is declared rather than
  hidden: the previous close, the daily baselines, the reference universe and the split
  calendar are Massive's, and only the same-day premarket is Kiwoom's;
* ``baseline_version`` - A's own 20-session premarket share-volume baseline;
* ``baseline_provider_contract`` - that baseline's provider mix. Twenty Kiwoom sessions do not
  exist on the first morning, so each session is Kiwoom's where Kiwoom has an observation and
  the frozen local Massive tape's otherwise. Twenty *combined* sessions is the precondition;
  the mix is carried on the run as ``baseline_mode`` with both session counts;
* ``scanner_checksum`` - computed over *this* declaration. The research checksum travels
  beside it as ``research_parent_checksum`` and is never presented as the live one.

:func:`verify` recomputes the research parent's two checksums before any live run and refuses
when either has moved, so the live contract cannot claim a parent that no longer exists. No
past record is rewritten by anything here: the research artifacts keep their own names, and
the legacy trade-value scanner keeps ``quant_v0``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from app.backtest.mover_scanner_v1 import contract as K
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.strategy.config import StrategyConfig

#: The live contract name. Distinct from every research name by construction.
LIVE_VERSION = "A-MOVER-LIVE-V1"
#: ``ScannerRun.score_version`` for a live run. Legacy keeps ``quant_v0``; the research
#: forward contract keeps ``mover_v1.2``; no stored row is reclassified.
RUN_SCORE_VERSION = "a_mover_live_v1"
#: ``ScannerRun.provider`` for a live run: the acquisition layer, not the ranking.
RUN_PROVIDER = "KIWOOM_AE_SHARED_PREMARKET"
#: The same-day premarket provider.
LIVE_PROVIDER = "KIWOOM"
#: The shared A/E acquisition layer's contract version (``acquisition``/``schedule``).
COLLECTOR_VERSION = "ae_shared_premarket_collector_v1"
#: The hybrid feature contract (``features``).
FEATURE_CONTRACT_VERSION = "a_mover_live_features_v1"
#: A's own premarket share-volume baseline identity (``baseline``).
BASELINE_VERSION = "A_MOVER_PM_VOLUME_V1"
#: The denominator's provider contract. Kiwoom owns every session it has an observation for and
#: the frozen local Massive minute tape owns the rest, so the twenty sessions exist on the first
#: morning instead of twenty mornings later. Which provider supplied each session travels on the
#: run as ``baseline_mode`` and the two session counts; it is recorded, never gated on.
BASELINE_PROVIDER_CONTRACT = "KIWOOM_PREFERRED_PER_SESSION+MASSIVE_TAPE_BOOTSTRAP"

#: Which provider is authoritative for which input. Declared, never implied.
HYBRID_SOURCES: dict[str, str] = {
    "same_day_premarket_bars": LIVE_PROVIDER,
    "premarket_rvol_baseline": BASELINE_PROVIDER_CONTRACT,
    "previous_regular_close": "MASSIVE_GROUPED_DAILY",
    "daily_volume_baselines": "MASSIVE_GROUPED_DAILY",
    "reference_universe": "MASSIVE_REFERENCE_CACHE",
    "split_calendar": "MASSIVE_SPLITS",
}


class LiveContractDrift(RuntimeError):
    """The research parent no longer reproduces its frozen checksums."""


@dataclass(frozen=True)
class LiveContract:
    """The live contract as one value. Every field is a claim a persisted row can carry."""

    scanner_version: str = LIVE_VERSION
    run_score_version: str = RUN_SCORE_VERSION
    research_parent_version: str = K.CONTRACT_VERSION
    research_parent_checksum: str = K.HANDOFF_CHECKSUM
    research_parent_discovery_checksum: str = K.DISCOVERY_CHECKSUM
    live_provider: str = LIVE_PROVIDER
    collector_version: str = COLLECTOR_VERSION
    feature_contract_version: str = FEATURE_CONTRACT_VERSION
    baseline_version: str = BASELINE_VERSION
    baseline_provider_contract: str = BASELINE_PROVIDER_CONTRACT
    hybrid_sources: dict[str, str] = field(default_factory=lambda: dict(HYBRID_SOURCES))
    #: Restated from the parent so the live declaration is self-describing.
    pool_size: int = K.POOL_SIZE
    top_count: int = MoverScannerConfig().top_count
    scan_cut_minute: int = K.SCAN_CUT_MINUTE
    #: Parity is an audit metric, never a precondition. Section M.
    parity_is_a_production_gate: bool = False

    def declaration(self) -> dict[str, Any]:
        body = {
            "scanner_version": self.scanner_version,
            "run_score_version": self.run_score_version,
            "research_parent_version": self.research_parent_version,
            "research_parent_checksum": self.research_parent_checksum,
            "research_parent_discovery_checksum": self.research_parent_discovery_checksum,
            "live_provider": self.live_provider,
            "collector_version": self.collector_version,
            "feature_contract_version": self.feature_contract_version,
            "baseline_version": self.baseline_version,
            "baseline_provider_contract": self.baseline_provider_contract,
            "hybrid_sources": dict(sorted(self.hybrid_sources.items())),
            "pool_size": self.pool_size,
            "top_count": self.top_count,
            "scan_cut_minute": self.scan_cut_minute,
            "parity_is_a_production_gate": self.parity_is_a_production_gate,
        }
        return body

    @property
    def scanner_checksum(self) -> str:
        """The live checksum, over the live declaration. Never the research checksum."""
        body = json.dumps(self.declaration(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def metadata(self) -> dict[str, Any]:
        """Section A's named metadata block, for a run record or a report."""
        return self.declaration() | {"scanner_checksum": self.scanner_checksum}


def current() -> LiveContract:
    return LiveContract()


def verify(strategy: StrategyConfig | None = None) -> dict[str, Any]:
    """Refuse to run live unless the research parent still reproduces its own checksums.

    The parent's own :func:`app.backtest.mover_scanner_v1.contract.verify` is the authority;
    this wrapper only renames its failure so a live caller records CONTRACT_DRIFT rather than
    a research exception, and attaches the live metadata that passed.
    """
    try:
        parent = K.verify()
    except K.ContractDrift as error:
        raise LiveContractDrift(str(error)) from error
    live = current()
    if parent["scanner_checksum"] != live.research_parent_checksum:
        raise LiveContractDrift("the research parent handoff checksum moved")
    if parent["discovery_checksum"] != live.research_parent_discovery_checksum:
        raise LiveContractDrift("the research parent discovery checksum moved")
    if parent["pool_size"] != live.pool_size or parent["top_count"] != live.top_count:
        raise LiveContractDrift("the research parent pool size or output maximum moved")
    if live.scanner_checksum == live.research_parent_checksum:
        raise LiveContractDrift("the live checksum must not equal the research checksum")
    # The parent's own verify builds its rule from ``StrategyConfig()``, so it cannot see a
    # caller that passes a different one. The live run must, because the handoff mask reads the
    # gap band it is handed: a tighter band with the frozen name would be a different contract
    # under the same label.
    rule = K.final_rule(strategy)
    if rule.checksum != live.research_parent_checksum:
        raise LiveContractDrift(
            f"the handoff checksum of the configuration in force is {rule.checksum}, not the "
            f"frozen {live.research_parent_checksum}; the Strategy A gap band or direction "
            "moved and the frozen mask is not what this run would apply")
    return {"live": live.metadata(), "research_parent": parent, "rule": rule.declaration()}

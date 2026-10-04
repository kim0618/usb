"""The live scan: the research ranking architecture, run on Kiwoom data, stamped as live.

Fixed and read, never written here: the discovery pool of 35, the five weights
(0.30 premarket dollar volume, 0.25 premarket relative volume, 0.20 gap quality,
0.15 premarket momentum, 0.10 tradability), the gap-quality knots, the actionability mask's
three conditions and the output maximum of 8. All of them arrive through
``mover_scanner_v1.contract.final_config`` and ``final_rule``, which read
``MoverScannerConfig`` and ``StrategyConfig``, so this module declares no threshold at all and
cannot drift from the frozen artifact.

What it does own is the *labelling*. A row produced here says ``A-MOVER-LIVE-V1`` and carries
the live checksum; the research checksum travels beside it as the parent. Presenting the
research checksum as the live one would make a reproducibility claim about data the research
run never saw.

Parity against the Massive historical TOP8 is computed as an audit metric when a comparison
set is supplied and is never consulted for admission. The reason is measured, not editorial:
the two sources differ (premarket dollar-volume Spearman 0.822, identical TOP8 in 1 of 14
sessions once both channels are applied), so a parity gate would permanently block a
Kiwoom-native strategy for being Kiwoom-native.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.backtest.mover_scanner_v1 import actionability as A
from app.backtest.mover_scanner_v1 import contract as K
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.scan import MoverCandidate, SessionScan, scan_session
from app.repositories.scanner import ScannerCandidateData
from app.services.mover_scanner_source import MoverDataUnavailableError, MoverScanInput
from app.strategy.config import StrategyConfig
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC


@dataclass(frozen=True)
class LiveScan:
    """One live session's scan, from discovery pool to the handed-off set."""

    session: date
    contract: LC.LiveContract
    scan: SessionScan
    selection: A.SessionHandoff
    source_name: str
    universe_as_of: date
    universe_checksum: str
    premarket_digest: str
    observed_at: datetime
    #: Section I's denominator provenance, as the source handed it over. None when the source
    #: does not mix providers; never invented here.
    baseline_provider_mix: Mapping[str, Any] | None = None
    baseline_readiness: Mapping[str, Any] | None = None

    @property
    def baseline_stamp(self) -> dict[str, Any]:
        """The three names section O requires on a candidate row, from the mix or as unknown.

        An absent mix is recorded as UNKNOWN rather than as zero sessions: a row saying
        ``massive_session_count = 0`` would be a claim about the denominator, and a source that
        did not report its mix has made no such claim.
        """
        mix = self.baseline_provider_mix or {}
        return {"baseline_mode": mix.get("baseline_mode", "UNKNOWN"),
                "baseline_session_count": mix.get("baseline_session_count"),
                "kiwoom_session_count": mix.get("kiwoom_session_count"),
                "massive_session_count": mix.get("massive_session_count")}

    @property
    def handoff(self) -> tuple[MoverCandidate, ...]:
        return self.selection.handoff

    @property
    def candidate_count(self) -> int:
        return len(self.selection.handoff)

    def metadata(self) -> dict[str, Any]:
        return self.contract.metadata() | {
            "session": self.session.isoformat(),
            "source_name": self.source_name,
            "live": True,
            "universe_as_of": self.universe_as_of.isoformat(),
            "universe_checksum": self.universe_checksum,
            "premarket_digest": self.premarket_digest,
            "observed_at": self.observed_at.isoformat(),
            "evaluated": self.scan.evaluated,
            "eligible": self.scan.eligible,
            "rejections": dict(self.scan.rejections),
            "discovery_pool_size": self.selection.discovery_pool_size,
            "actionable_pool_size": self.selection.actionable_pool_size,
            "handoff_size": self.candidate_count,
            "actionability_rejections": self.selection.rejection_counts,
            "baseline_provider_mix": (dict(self.baseline_provider_mix)
                                      if self.baseline_provider_mix is not None else None),
            "baseline_readiness": self.baseline_readiness,
        } | self.baseline_stamp


class LiveScanRefused(RuntimeError):
    """The live scan produced nothing, with the reason that must be recorded."""

    def __init__(self, refusal: CFG.Refusal, detail: str) -> None:
        super().__init__(f"{refusal}: {detail}")
        self.refusal = refusal
        self.detail = detail


def run(scan_input: MoverScanInput, *, observed_at: datetime,
        config: MoverScannerConfig | None = None,
        strategy: StrategyConfig | None = None,
        environ: dict[str, str] | None = None) -> LiveScan:
    """Verify the contract, scan, mask, and return at most ``top_count`` candidates."""
    if not CFG.enabled(environ):
        raise LiveScanRefused(CFG.Refusal.DISABLED, f"{CFG.ENV_FLAG} is not on")
    if not scan_input.live:
        raise LiveScanRefused(CFG.Refusal.DATA_UNAVAILABLE,
                              f"{scan_input.source_name} is not a live source")
    try:
        LC.verify(strategy)
    except LC.LiveContractDrift as error:
        raise LiveScanRefused(CFG.Refusal.CONTRACT_DRIFT, str(error)) from error
    config = config or K.final_config()
    rule = K.final_rule(strategy)
    scan = scan_session(scan_input.session, scan_input.symbols, scan_input.premarket,
                        scan_input.daily, config, strategy)
    selection = A.select(scan_input.session, scan.pool, rule)
    return LiveScan(session=scan_input.session, contract=LC.current(), scan=scan,
                    selection=selection, source_name=scan_input.source_name,
                    universe_as_of=scan_input.universe_as_of,
                    universe_checksum=scan_input.universe_checksum,
                    premarket_digest=scan_input.premarket_digest, observed_at=observed_at,
                    baseline_provider_mix=scan_input.baseline_provider_mix,
                    baseline_readiness=scan_input.baseline_readiness)


def run_from_source(source, session: date, *, observed_at: datetime,
                    config: MoverScannerConfig | None = None,
                    strategy: StrategyConfig | None = None,
                    environ: dict[str, str] | None = None) -> LiveScan:
    """Load from a ``MoverPremarketSource`` and scan, turning a data refusal into a named one."""
    config = config or K.final_config()
    try:
        scan_input = source.load(session, config)
    except MoverDataUnavailableError as error:
        raise LiveScanRefused(CFG.Refusal.DATA_UNAVAILABLE,
                              f"{error.reason}: {error.detail}") from error
    return run(scan_input, observed_at=observed_at, config=config, strategy=strategy,
               environ=environ)


def candidate_payload(live: LiveScan, config: MoverScannerConfig | None = None) -> dict[str, Any]:
    """The handoff payload with the live stamp replacing the research one, parent retained."""
    config = config or K.final_config()
    payload = A.handoff_payload(live.selection, config, live.observed_at)
    contract = live.contract
    payload["scanner"] = contract.scanner_version
    payload["rules_checksum"] = contract.scanner_checksum
    payload["research_parent_version"] = contract.research_parent_version
    payload["research_parent_checksum"] = contract.research_parent_checksum
    payload["provider_contract"] = dict(sorted(contract.hybrid_sources.items()))
    payload["live_provider"] = contract.live_provider
    payload["collector_version"] = contract.collector_version
    payload["feature_contract_version"] = contract.feature_contract_version
    payload["baseline_version"] = contract.baseline_version
    payload["baseline_provider_contract"] = contract.baseline_provider_contract
    payload.update(live.baseline_stamp)
    payload["source_name"] = live.source_name
    ranks = live.selection.rank_by_symbol()
    for entry, item in zip(payload["candidates"], live.selection.handoff, strict=True):
        entry["live_scanner_rank"] = item.rank
        entry["discovery_output_rank"] = ranks[item.symbol]
        entry["discovery_pool_rank"] = item.candidate_pool_rank
        entry["scanner_version"] = contract.scanner_version
        entry["scanner_checksum"] = contract.scanner_checksum
        entry["provider_contract"] = payload["provider_contract"]
        entry.update(live.baseline_stamp)
    return payload


def candidate_rows(live: LiveScan) -> list[ScannerCandidateData]:
    """The persistable rows, through the research module's own contiguity assertions."""
    rows = A.candidate_rows(live.selection, live.observed_at)
    contract = live.contract
    out: list[ScannerCandidateData] = []
    for row in rows:
        components = dict(row.score_components)
        components["scanner_version"] = contract.scanner_version
        components["scanner_checksum"] = contract.scanner_checksum
        components["research_parent_version"] = contract.research_parent_version
        components["research_parent_checksum"] = contract.research_parent_checksum
        components["provider_contract"] = dict(sorted(contract.hybrid_sources.items()))
        components["source_name"] = live.source_name
        components["collector_version"] = contract.collector_version
        components["feature_contract_version"] = contract.feature_contract_version
        components["baseline_version"] = contract.baseline_version
        components["baseline_provider_contract"] = contract.baseline_provider_contract
        components.update(live.baseline_stamp)
        if live.baseline_readiness is not None:
            components["baseline_readiness"] = {
                k: v for k, v in live.baseline_readiness.items() if k != "symbols"}
        out.append(ScannerCandidateData(
            symbol=row.symbol, rank=row.rank, is_top8=row.is_top8, score=row.score,
            score_components=components, observed_at=row.observed_at,
            available_at=row.available_at))
    return out


def parity(live: LiveScan, reference_top: Sequence[str]) -> Mapping[str, Any]:
    """An audit metric only. Never a precondition; section M.

    ``reference_top`` is the Massive historical arm's output for the same session. The overlap
    is recorded so the divergence is tracked over time, and nothing reads the verdict.
    """
    live_symbols = [item.symbol for item in live.selection.handoff]
    reference = list(reference_top)
    overlap = sorted(set(live_symbols) & set(reference))
    denominator = max(len(reference), 1)
    return {"is_production_gate": False,
            "live_top": live_symbols, "reference_top": reference,
            "overlap_symbols": overlap, "overlap_count": len(overlap),
            "overlap_ratio": len(overlap) / denominator,
            "identical": live_symbols == reference,
            "note": "recorded for audit; provider difference is measured and expected"}

"""A-MOVER-SCANNER-V1.1: the GPT handoff mask. Discovery is untouched; selection is not.

V1 discovers movers broadly and reports, as its own section 6 does, that about a quarter of the
slots it hands to GPT gap outside Strategy A's entry band. Those symbols are researched and
then refused by the premarket gate, so they cost a GPT call and a TOP8 slot for nothing.

This module is the one thing V1.1 adds: between the discovery pool and the GPT handoff, a mask
keeps only the candidates the *current* Strategy A long contract could admit at all, and the
pool is then re-ranked on the discovery score it already has. No score, weight, knot, floor,
universe rule or pool rule moves, so ``MoverScannerConfig.checksum`` stays V1's
``d900dffd7b23fea1…`` and a V1 artifact remains reproducible under its own name.

**The mask is three conditions and no more.** Direction, the gap minimum and the gap maximum.
A premarket volume floor is deliberately *not* applied here: relative volume is already a
quarter of the discovery score, GPT is being asked for catalyst quality rather than
participation, and the volume condition is the entry gate's to judge at the open with its own
window. Adding a volume reject here would discard names the gate might still admit, since the
gate reads 04:00-09:30 and this mask can only see 04:00-09:15.

**Every bound is read, never written.** ``HandoffRule.current`` takes the gap minimum, the gap
maximum and the direction from ``StrategyConfig`` and the handoff maximum from
``MoverScannerConfig.top_count``; this module declares no threshold of its own, so the scanner
and the engine cannot drift apart. The bounds travel in the rule's own declaration and
checksum, which means an artifact names the config that produced it.

**Scan time, not gate time.** The mask reads the 09:15 gap, which is what a live 09:15 handoff
can know. The 09:30 gate gap is a different number, so a masked-in candidate can still be
refused at the open and a masked-out one could in principle have recovered. That is a property
of handing off before the open, not an error, and the study measures the gate on its own window
separately.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from math import isfinite
from typing import Any

from app.backtest.mover_scanner_v1 import handoff as H
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.scan import MoverCandidate
from app.core.exceptions import DataError
from app.repositories.scanner import ScannerCandidateData
from app.strategy.config import GapDirection, StrategyConfig

#: The explicit new contract. V1's ``a-mover-scanner-v1`` keeps its own meaning and artifacts.
CONTRACT_VERSION = "a-mover-scanner-v1.1"


class Actionability(StrEnum):
    """Whether the current execution contract could admit this candidate at all."""

    PASS = "PASS"
    GAP_TOO_LOW_FOR_HANDOFF = "GAP_TOO_LOW_FOR_HANDOFF"
    GAP_TOO_HIGH_FOR_HANDOFF = "GAP_TOO_HIGH_FOR_HANDOFF"
    DIRECTION_NOT_ACTIONABLE = "DIRECTION_NOT_ACTIONABLE"


@dataclass(frozen=True)
class HandoffRule:
    """The actionability rule in force, with every bound read from an authoritative config.

    Construct it with :meth:`current`. The fields are stored as ``Decimal`` so the comparison
    is the gate's own arithmetic rather than a binary-float approximation of it.
    """

    gap_min_pct: Decimal
    gap_max_pct: Decimal
    direction: GapDirection
    handoff_maximum: int
    discovery_contract_version: str
    discovery_rules_checksum: str
    score_version: str
    contract_version: str = CONTRACT_VERSION
    #: Named so an artifact records what the mask did *not* test. Section D: no volume filter.
    mask_conditions: tuple[str, ...] = ("direction", "gap_min", "gap_max")
    applies_volume_filter: bool = False
    #: The window the mask can see, against the gate's own 04:00-09:30.
    gap_observed_at: str = "scan_cut"
    sources: Mapping[str, str] = field(default_factory=lambda: {
        "gap_min_pct": "StrategyConfig.premarket_gap_min_pct",
        "gap_max_pct": "StrategyConfig.premarket_gap_max_pct",
        "direction": "StrategyConfig.premarket_gap_direction",
        "handoff_maximum": "MoverScannerConfig.top_count",
    })

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.gap_min_pct < self.gap_max_pct:
            raise ValueError("the handoff gap band must be a non-empty band")
        if self.handoff_maximum < 1:
            raise ValueError("the handoff maximum must admit at least one candidate")

    @classmethod
    def current(cls, config: MoverScannerConfig | None = None,
                strategy: StrategyConfig | None = None) -> "HandoffRule":
        """Read the rule from the deployed configs. Nothing here is a literal."""
        config = config or MoverScannerConfig()
        strategy = strategy or StrategyConfig()
        return cls(
            gap_min_pct=Decimal(strategy.premarket_gap_min_pct),
            gap_max_pct=Decimal(strategy.premarket_gap_max_pct),
            direction=GapDirection(strategy.premarket_gap_direction),
            handoff_maximum=config.top_count,
            discovery_contract_version=config.contract_version,
            discovery_rules_checksum=config.checksum,
            score_version=config.score_version,
        )

    def declaration(self) -> dict[str, Any]:
        """The whole rule as plain data, for the artifact and the checksum."""
        return {
            "contract_version": self.contract_version,
            "discovery_contract_version": self.discovery_contract_version,
            "discovery_rules_checksum": self.discovery_rules_checksum,
            "score_version": self.score_version,
            "direction": str(self.direction),
            "gap_min_pct": str(self.gap_min_pct),
            "gap_max_pct": str(self.gap_max_pct),
            "handoff_maximum": self.handoff_maximum,
            "mask_conditions": list(self.mask_conditions),
            "applies_volume_filter": self.applies_volume_filter,
            "gap_observed_at": self.gap_observed_at,
            "sources": dict(self.sources),
        }

    @property
    def checksum(self) -> str:
        """The V1.1 checksum: the discovery checksum plus the mask, hashed together.

        It moves when a Strategy A gap bound moves, which is the point: an artifact cannot be
        read as describing a band the engine no longer uses.
        """
        body = json.dumps(self.declaration(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def direction_compatible(self, gap_pct: float) -> bool:
        """Whether the gap is on the side the gate admits at all."""
        gap = Decimal(repr(float(gap_pct)))
        if self.direction is GapDirection.UP:
            return gap > 0
        if self.direction is GapDirection.DOWN:
            return gap < 0
        return True

    def gap_size(self, gap_pct: float) -> Decimal:
        """The quantity the band bounds: the gap itself for UP, its magnitude otherwise.

        Only ``UP`` is deployed, and ``scan.gate_reading`` refuses to measure anything else.
        For ``DOWN`` and ``ANY`` the band is read on the gap's size, which is the only reading
        under which a positive 2-15% band is meaningful at all; a changed direction is a
        reviewable event rather than something this module decides quietly.
        """
        gap = Decimal(repr(float(gap_pct)))
        return gap if self.direction is GapDirection.UP else abs(gap)

    def verdict(self, gap_pct: float) -> Actionability:
        """The declared three-condition mask, direction first."""
        if not isinstance(gap_pct, (int, float)) or not isfinite(gap_pct):
            raise DataError("the actionability mask needs a finite gap")
        if not self.direction_compatible(gap_pct):
            return Actionability.DIRECTION_NOT_ACTIONABLE
        size = self.gap_size(gap_pct)
        if size < self.gap_min_pct:
            return Actionability.GAP_TOO_LOW_FOR_HANDOFF
        if size > self.gap_max_pct:
            return Actionability.GAP_TOO_HIGH_FOR_HANDOFF
        return Actionability.PASS

    def is_actionable(self, gap_pct: float) -> bool:
        return self.verdict(gap_pct) is Actionability.PASS


def discovery_order(pool: Sequence[MoverCandidate]) -> list[MoverCandidate]:
    """The pool in V1's own stage-2 order: full opportunity score, V1's tie-break exactly.

    ``scan_session`` sorts the pool by ``(-total_score, -pool_score, symbol)`` and takes the
    first ``top_count`` as V1's output. Reproducing that order here is what makes
    ``discovery_output_rank`` mean "the slot V1 would have given this candidate", so a
    replacement can be attributed to a discovery rank rather than guessed at.
    """
    return sorted(pool, key=lambda item: (-item.total_score, -item.pool_score, item.symbol))


@dataclass(frozen=True)
class AuditRow:
    """One discovery-pool member and what the mask made of it."""

    candidate: MoverCandidate
    verdict: Actionability
    discovery_output_rank: int

    @property
    def actionable(self) -> bool:
        return self.verdict is Actionability.PASS


@dataclass(frozen=True)
class SessionHandoff:
    """One session's handoff: what GPT is given, and the audit of what it was not."""

    session_date: date
    rule: HandoffRule
    #: Ranked 1..N contiguously, N at most ``handoff_maximum``, never padded.
    handoff: tuple[MoverCandidate, ...]
    #: Every discovery-pool member, in discovery order, actionable or not.
    audit: tuple[AuditRow, ...]
    #: V1's raw output for the same session: discovery order's first ``handoff_maximum``.
    discovery_top: tuple[MoverCandidate, ...]

    @property
    def discovery_pool_size(self) -> int:
        return len(self.audit)

    @property
    def actionable_pool_size(self) -> int:
        return sum(1 for row in self.audit if row.actionable)

    @property
    def handoff_size(self) -> int:
        return len(self.handoff)

    @property
    def invalid_removed(self) -> int:
        """V1 slots the mask refuses: the wasted GPT budget this stage recovers."""
        discovery = {item.symbol for item in self.discovery_top}
        return sum(1 for row in self.audit
                   if row.candidate.symbol in discovery and not row.actionable)

    @property
    def replacement_ranks(self) -> tuple[int, ...]:
        """The discovery ranks the handoff reached past V1's own output cut, ascending."""
        handed = {item.symbol for item in self.handoff}
        return tuple(sorted(row.discovery_output_rank for row in self.audit
                            if row.candidate.symbol in handed
                            and row.discovery_output_rank > self.rule.handoff_maximum))

    @property
    def replacement_added(self) -> int:
        """Handoff slots filled from discovery ranks below V1's own output cut."""
        return len(self.replacement_ranks)

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.audit:
            if not row.actionable:
                counts[str(row.verdict)] = counts.get(str(row.verdict), 0) + 1
        return counts

    def rank_by_symbol(self) -> dict[str, int]:
        return {row.candidate.symbol: row.discovery_output_rank for row in self.audit}


def select(session: date, pool: Sequence[MoverCandidate], rule: HandoffRule) -> SessionHandoff:
    """Discovery pool -> actionability mask -> re-rank on the discovery score -> at most N.

    The order matters and section E of the handoff contract names the wrong way round: masking
    V1's TOP8 would leave five or six names, while masking the whole pool lets discovery rank
    9-25 compete for the slot an unactionable name was occupying. The surviving candidates keep
    the discovery score they already had, so this re-ranks and never re-scores.
    """
    ordered = discovery_order(pool)
    audit = tuple(AuditRow(candidate=item, verdict=rule.verdict(item.gap_pct),
                           discovery_output_rank=position)
                  for position, item in enumerate(ordered, start=1))
    actionable = [row.candidate for row in audit if row.actionable]
    handoff = tuple(replace(item, rank=position) for position, item
                    in enumerate(actionable[:rule.handoff_maximum], start=1))
    return SessionHandoff(session_date=session, rule=rule, handoff=handoff, audit=audit,
                          discovery_top=tuple(ordered[:rule.handoff_maximum]))


def scan_time_label(config: MoverScannerConfig) -> str:
    return f"{config.scan_cut_minute // 60:02d}:{config.scan_cut_minute % 60:02d} ET"


def _row(candidate: MoverCandidate, rank: int | None, verdict: Actionability,
         discovery_output_rank: int, rule: HandoffRule, scan_time: str) -> dict[str, Any]:
    """The declared V1.1 output record (section G), as plain JSON data."""
    actionable = verdict is Actionability.PASS
    return {
        "session_date": candidate.session_date.isoformat(),
        "scan_time": scan_time,
        "rank": rank,
        "symbol": candidate.symbol,
        "discovery_pool_rank": candidate.candidate_pool_rank,
        "discovery_output_rank": discovery_output_rank,
        "discovery_total_score": candidate.total_score,
        "gap_pct": candidate.gap_pct,
        "pm_volume": candidate.pm_volume,
        "pm_dollar_volume": candidate.pm_dollar_volume,
        "pm_rvol": candidate.pm_rvol,
        "pm_momentum": candidate.pm_momentum,
        "pm_range": candidate.pm_range,
        "tradability_score": candidate.tradability_score,
        "component_scores": dict(candidate.component_scores),
        "actionable": actionable,
        "actionability_reason": str(verdict),
        "rejection_reason": None if actionable else str(verdict),
        "scanner_version": rule.contract_version,
        "scanner_checksum": rule.checksum,
        "discovery_rules_checksum": rule.discovery_rules_checksum,
        "entry_gate": {
            "gap_pct": candidate.gate.gap_pct, "volume_ratio": candidate.gate.volume_ratio,
            "gap_pass": candidate.gate.gap_pass, "volume_pass": candidate.gate.volume_pass,
            "both_pass": candidate.gate.both_pass,
        },
    }


def handoff_rows(selection: SessionHandoff, config: MoverScannerConfig) -> list[dict[str, Any]]:
    """The GPT-bound rows, ranked 1..N."""
    scan_time = scan_time_label(config)
    ranks = selection.rank_by_symbol()
    return [_row(item, item.rank, Actionability.PASS, ranks[item.symbol], selection.rule,
                 scan_time) for item in selection.handoff]


def audit_rows(selection: SessionHandoff, config: MoverScannerConfig) -> list[dict[str, Any]]:
    """Every discovery-pool member with its verdict, so an exclusion can be reviewed."""
    scan_time = scan_time_label(config)
    handed = {item.symbol: item.rank for item in selection.handoff}
    return [_row(row.candidate, handed.get(row.candidate.symbol), row.verdict,
                 row.discovery_output_rank, selection.rule, scan_time)
            for row in selection.audit]


def candidate_rows(selection: SessionHandoff, observed_at: datetime) -> list[ScannerCandidateData]:
    """The persistable rows, unchanged from V1's shape so no repository or prompt moves.

    The contiguity assertion is not decoration: the deployed prompt tells GPT to return
    "exactly the same N symbols" with "unique contiguous ranks 1..N", and a masked selection is
    the first thing that could break that invariant.
    """
    rows = H.candidate_rows(selection.handoff, observed_at)
    if [row.rank for row in rows] != list(range(1, len(rows) + 1)):
        raise DataError("the handoff ranks must be contiguous from 1")
    if len({row.symbol for row in rows}) != len(rows):
        raise DataError("the handoff must not repeat a symbol")
    if len(rows) > selection.rule.handoff_maximum:
        raise DataError("the handoff must not exceed its maximum")
    return rows


def handoff_payload(selection: SessionHandoff, config: MoverScannerConfig,
                    observed_at: datetime) -> dict[str, Any]:
    """Everything the GPT step needs, plus the rule that decided who is in it."""
    payload = H.handoff_payload(selection.session_date, selection.handoff, config, observed_at)
    ranks = selection.rank_by_symbol()
    payload["scanner"] = selection.rule.contract_version
    payload["rules_checksum"] = selection.rule.checksum
    payload["discovery_contract_version"] = selection.rule.discovery_contract_version
    payload["discovery_rules_checksum"] = selection.rule.discovery_rules_checksum
    payload["observed_at"] = observed_at.isoformat()
    payload["actionability"] = {
        "rule": selection.rule.declaration(),
        "discovery_pool_size": selection.discovery_pool_size,
        "actionable_pool_size": selection.actionable_pool_size,
        "invalid_removed_from_discovery_top": selection.invalid_removed,
        "replacement_added_from_below_the_cut": selection.replacement_added,
        "rejections": selection.rejection_counts,
    }
    for entry, item in zip(payload["candidates"], selection.handoff, strict=True):
        entry["actionable"] = True
        entry["actionability_reason"] = str(Actionability.PASS)
        entry["discovery_output_rank"] = ranks[item.symbol]
        entry["discovery_pool_rank"] = item.candidate_pool_rank
    return payload

"""The STRICT dataset readiness gate. An authoritative run either starts whole or not at all.

A partial run is the failure mode this exists to prevent: a screening that quietly skipped the
sessions it could not load reports a number for a study that never happened, and nothing in the
result would say so. So every check below is a refusal, not a warning, and the contract gives
the runner no flag that can turn one off.

The report names what failed and the first offending items, because "something is missing" is
not an instruction and "these 12 symbols have no warmup" is.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from app.backtest.strategy_b_e0.contract import Contract, RULES_PATH
from app.backtest.strategy_b_e0.universe import RunUniverse
from app.backtest.strategy_c_selection.rules import canonical_checksum

MAX_REPORTED_ITEMS = 20


class DatasetFacts(Protocol):
    """What the readiness gate needs to know about the stored data.

    A Protocol rather than a concrete store: the gate's logic is the same whether the bars sit
    on a Drive mount, in a local staging copy or in a test fixture, and it must be testable
    without either.
    """

    def missing_scope_pairs(self, pairs: Sequence[tuple[str, date]]) -> Sequence[tuple[str, date]]:
        """The (symbol, session) pairs with no stored bars."""

    def symbols_missing_warmup(self, universe: RunUniverse, sessions: int) -> Sequence[str]:
        """Symbols without the full RVOL warmup before their first scope session."""

    def symbols_missing_splits(self, symbols: Sequence[str]) -> Sequence[str]:
        """Symbols with no split record set, which the PIT share factor needs."""

    def sessions_missing_daily(self, sessions: Sequence[date]) -> Sequence[date]:
        """Sessions whose daily or grouped daily data the scope and CA flags depend on is absent."""

    def schema_mismatches(self) -> Mapping[str, str]:
        """Stored schema or collector format values that differ from the expected ones."""

    def checksum_mismatches(self, pairs: Sequence[tuple[str, date]]) -> Sequence[str]:
        """Stored files whose bytes no longer hash to their ledger entry."""

    def collection_completeness(self) -> Mapping[str, object]:
        """The completeness figure and absent symbols, recorded even when the gate passes."""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str
    items: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "items": list(self.items)}


PASS = "PASS"
PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
DATASET_NOT_READY = "DATASET_NOT_READY"
DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"
RUNNABLE = frozenset({PASS, PASS_WITH_WARNINGS})


@dataclass(slots=True)
class QualityGate:
    """The pre-registered data-quality gate. Only DATA_QUALITY exclusions are counted here;
    POLICY exclusions (IPO_WARMUP) are a declared rule, not a data defect, and never enter it."""

    required_pairs: int = 0
    quality_exclusions: int = 0
    overall_ratio: float = 0.0
    overall_limit: float = 0.0
    max_session_ratio: float = 0.0
    max_session: str | None = None
    session_limit: float = 0.0
    blocked: bool = False
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"required_pairs": self.required_pairs, "quality_exclusions": self.quality_exclusions,
                "overall_ratio": self.overall_ratio, "overall_limit": self.overall_limit,
                "max_session_ratio": self.max_session_ratio, "max_session": self.max_session,
                "session_limit": self.session_limit, "comparison": "STRICTLY_GREATER_BLOCKS",
                "blocked": self.blocked, "reasons": list(self.reasons)}


@dataclass(slots=True)
class PreflightReport:
    ready: bool = True
    checks: list[Check] = field(default_factory=list)
    completeness: Mapping[str, object] = field(default_factory=dict)
    status: str = PASS
    warnings: list[str] = field(default_factory=list)
    gate: QualityGate = field(default_factory=QualityGate)

    def add(self, check: Check) -> None:
        self.checks.append(check)
        self.ready = self.ready and check.passed

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def settle(self) -> None:
        """Blocking checks first, then the quality gate, then warnings. Deterministic order."""
        if self.failures:
            self.status = DATASET_NOT_READY
        elif self.gate.blocked:
            self.status = DATA_QUALITY_BLOCKED
        elif self.warnings:
            self.status = PASS_WITH_WARNINGS
        else:
            self.status = PASS
        self.ready = self.status in RUNNABLE

    def as_dict(self) -> dict[str, object]:
        return {"ready": self.ready, "status": self.status,
                "checks": [check.as_dict() for check in self.checks],
                "failed": [check.name for check in self.failures],
                "warnings": list(self.warnings), "quality_gate": self.gate.as_dict(),
                "collection_completeness": dict(self.completeness)}


def quality_gate(universe: RunUniverse, exclusions, *, overall_limit: float,
                 session_limit: float) -> QualityGate:
    """Strictly greater than the limit blocks; exactly at the limit does not."""
    required = len(universe.required_pairs())
    per_session: dict[date, int] = {}
    for item in exclusions:
        per_session[item.session] = per_session.get(item.session, 0) + 1
    gate = QualityGate(required_pairs=required, quality_exclusions=len(exclusions),
                       overall_limit=overall_limit, session_limit=session_limit)
    gate.overall_ratio = (len(exclusions) / required) if required else 0.0
    for session, count in sorted(per_session.items()):
        members = len(universe.symbols_for(session))
        ratio = count / members if members else 0.0
        if ratio > gate.max_session_ratio:
            gate.max_session_ratio, gate.max_session = ratio, session.isoformat()
    if gate.overall_ratio > overall_limit:
        gate.reasons.append(f"overall {gate.overall_ratio:.6f} > {overall_limit}")
    if gate.max_session_ratio > session_limit:
        gate.reasons.append(f"session {gate.max_session} {gate.max_session_ratio:.6f} > {session_limit}")
    gate.blocked = bool(gate.reasons)
    return gate


def config_agreement(contract: Contract, config) -> Check:
    """Every value the contract restates must equal the config the engine will actually run.

    The contract is the source of truth for what was pre-registered, but the engine reads
    ``StrategyBConfig``. Nothing in this package may hardcode one of these numbers, so the only
    honest guard is to compare the two and refuse when they disagree: a contract that declares a
    TTL the engine does not use describes a study nobody ran.
    """
    declared = {
        "signal_ttl_minutes": contract.raw["signal_ttl"]["signal_ttl_minutes"],
        "risk_per_trade_pct": contract.raw["sizing"]["risk_per_trade_pct"],
        "max_position_pct": contract.raw["sizing"]["max_position_pct"],
        "max_open_positions": contract.raw["sizing"]["max_open_positions"],
        "max_entries_per_symbol": contract.raw["sizing"]["max_entries_per_symbol"],
        "daily_loss_limit_r": contract.raw["sizing"]["daily_loss_limit_r"],
        "lift_horizon_minutes": contract.lift_horizon_minutes,
    }
    actual = {
        "signal_ttl_minutes": config.candidate.signal_ttl_minutes,
        "risk_per_trade_pct": config.risk.risk_per_trade_pct,
        "max_position_pct": config.risk.max_position_pct,
        "max_open_positions": config.risk.max_open_positions,
        "max_entries_per_symbol": config.risk.max_entries_per_symbol,
        "daily_loss_limit_r": config.risk.daily_loss_limit_r,
        "lift_horizon_minutes": config.exit.time_stop_minutes,
    }
    differences = [f"{key}: contract {declared[key]} != config {actual[key]}"
                   for key in declared if declared[key] != actual[key]]
    return Check("contract_matches_config", not differences,
                 f"{len(differences)} declared values differ from the running config",
                 _items(differences))


def run_preflight(contract: Contract, universe: RunUniverse, facts: DatasetFacts, *,
                  planned_symbols_by_session: Mapping[date, Sequence[str]] | None = None,
                  contract_path: Path | None = None, config=None,
                  rules_path: Path = RULES_PATH) -> PreflightReport:
    """Every readiness check the contract lists, in its order, all of them always evaluated.

    All checks run even after one fails, so a single report tells the whole story instead of
    making someone fix one thing at a time to discover the next.
    """
    report = PreflightReport()

    if config is None:
        from app.strategy_b.config import StrategyBConfig
        config = StrategyBConfig()
    report.add(config_agreement(contract, config))
    report.add(_window_matches(contract, universe))
    report.add(_membership(universe, planned_symbols_by_session))

    missing = facts.missing_scope_pairs(universe.required_pairs())
    report.add(Check("scope_bars_present", not missing,
                     f"{len(missing)} (symbol, session) pairs have no stored bars",
                     _items(f"{symbol} {day}" for symbol, day in missing)))

    warmup = facts.symbols_missing_warmup(universe, contract.warmup_sessions)
    report.add(Check("warmup_present", not warmup,
                     f"{len(warmup)} symbols lack the {contract.warmup_sessions}-session warmup",
                     _items(warmup)))

    splits = facts.symbols_missing_splits(universe.symbols)
    report.add(Check("splits_present", not splits,
                     f"{len(splits)} symbols have no split record set", _items(splits)))

    daily = facts.sessions_missing_daily(universe.sessions)
    report.add(Check("daily_present", not daily,
                     f"{len(daily)} sessions lack the daily data scope and CA flags need",
                     _items(str(day) for day in daily)))

    schema = facts.schema_mismatches()
    report.add(Check("schema_matches", not schema, f"{len(schema)} schema or format mismatches",
                     _items(f"{key}: {value}" for key, value in sorted(schema.items()))))

    checksums = facts.checksum_mismatches(universe.required_pairs())
    report.add(Check("file_checksums_match", not checksums,
                     f"{len(checksums)} stored files do not hash to their ledger entry",
                     _items(checksums)))

    report.add(_declared_checksum("rules_checksum_matches", rules_path, contract.rules_checksum))
    if contract_path is not None:
        report.add(_declared_checksum("contract_checksum_matches", contract_path,
                                      contract.canonical_checksum))

    for name, method, detail in (
            ("calendar_matches_universe", "calendar_mismatches", "calendar and universe sessions differ"),
            ("no_unsupported_early_close", "early_close_sessions",
             "early-close sessions in the window; the engine crashes on them (known defect)"),
            ("no_split_conflict_in_universe", "split_conflicts_in_universe",
             "conflicting split records for universe symbols")):
        probe = getattr(facts, method, None)
        if probe is not None:
            found = tuple(probe())
            report.add(Check(name, not found, f"{len(found)} {detail}", _items(str(x) for x in found)))

    report.completeness = facts.collection_completeness()
    if hasattr(facts, "quality_exclusions"):
        limits = _gate_limits(contract)
        report.gate = quality_gate(universe, facts.quality_exclusions(), **limits)
    if hasattr(facts, "warnings"):
        report.warnings = list(facts.warnings())
    report.settle()
    return report


def _gate_limits(contract: Contract) -> dict[str, float]:
    """The thresholds are the contract's, never this module's."""
    block = contract.raw["data_adapter"]["readiness"]
    if block["comparison"] != "STRICTLY_GREATER_BLOCKS":
        raise ValueError(f"unsupported comparison {block['comparison']!r}")
    return {"overall_limit": float(block["overall_quality_exclusion_limit"]),
            "session_limit": float(block["session_quality_exclusion_limit"])}


def _window_matches(contract: Contract, universe: RunUniverse) -> Check:
    want = (contract.scope_start, contract.scope_end)
    got = (universe.scope_start.isoformat(), universe.scope_end.isoformat())
    ok = want == got
    return Check("universe_window_matches_contract", ok,
                 f"universe window {got} against contract window {want}")


def _membership(universe: RunUniverse,
                planned: Mapping[date, Sequence[str]] | None) -> Check:
    """No session may be handed a symbol outside that session's scope set.

    This is the check that keeps the forward-looking union honest. The universe's symbol list is
    a superset of what any one session may trade, and feeding it whole into a session would be
    survivorship selection dressed up as a universe.
    """
    if planned is None:
        return Check("per_session_membership", True,
                     "no session plan supplied; the runner derives sessions from the artifact, "
                     "which cannot violate its own membership")
    offenders: list[str] = []
    for session in sorted(planned):
        for symbol in universe.membership_violations(session, planned[session]):
            offenders.append(f"{symbol} {session}")
    return Check("per_session_membership", not offenders,
                 f"{len(offenders)} (symbol, session) pairs are outside that session's scope set",
                 _items(offenders))


def _declared_checksum(name: str, path: Path, expected: str) -> Check:
    import json
    try:
        actual = canonical_checksum(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        return Check(name, False, f"{path} could not be read or parsed: {error}")
    return Check(name, actual == expected, f"{path.name} canonical {actual} against {expected}")


def _items(values) -> tuple[str, ...]:
    listed = list(values)
    head = tuple(str(value) for value in listed[:MAX_REPORTED_ITEMS])
    if len(listed) > MAX_REPORTED_ITEMS:
        head += (f"... and {len(listed) - MAX_REPORTED_ITEMS} more",)
    return head

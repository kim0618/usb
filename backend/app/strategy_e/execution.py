"""E-D2 deterministic selection and first-regular-minute open execution proxy.

This module starts with the frozen H5 candidates. It never receives Alpha feature magnitudes and
therefore cannot rank on them. It produces entry identity only: no exit, PnL, stop, size or cost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from math import isfinite
from numbers import Real
from pathlib import Path
from types import MappingProxyType

from app.market.calendar import MarketCalendar
from app.risk.config import RiskConfig
from app.strategy_e.signal import (
    DECISION_TIME_ET,
    SIGNAL_VERSION,
    STRATEGY_ID,
    TRADING_RULES_CANONICAL_SHA256,
    SignalResult,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
EXECUTION_RULES_PATH = (
    REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_execution_rules_v1.json"
)
EXECUTION_RULES_CANONICAL_SHA256 = (
    "d204b1dac9dd40530a7e03bebd36b32fd1950efde2e8b226a5eaedd5f196d041"
)
EXECUTION_VERSION = "STRATEGY_E_EXECUTION_V1"
ENTRY_MODEL = "FIRST_REGULAR_MINUTE_OPEN_PROXY_V1"
ENTRY_BAR_START_ET = "09:30"

NOT_SELECTED_CAPACITY = "NOT_SELECTED_CAPACITY"
MISSING_ENTRY_BAR = "NO_TRADE_MISSING_ENTRY_BAR"
INVALID_ENTRY_OPEN = "NO_TRADE_INVALID_ENTRY_OPEN"
DUPLICATE_ENTRY_BAR = "NO_TRADE_DUPLICATE_ENTRY_BAR"
SESSION_MISMATCH = "NO_TRADE_SESSION_MISMATCH"
INVALID_SESSION = "NO_TRADE_INVALID_SESSION"
SHORTENED_SESSION = "NO_TRADE_SHORTENED_SESSION"


class ExecutionContractError(ValueError):
    """Fail-closed rejection of the E-D2 batch contract or provenance."""


@dataclass(frozen=True)
class EntryBar:
    """One vendor aggregate identified by symbol, ET session and ET bar-start minute."""

    symbol: str
    session_date: date
    bar_start_et: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None


@dataclass(frozen=True)
class EntryRecord:
    strategy_id: str
    signal_version: str
    execution_version: str
    session_date: date
    symbol: str
    decision_time_et: str
    signal_digest: str
    signal_source_digest: str
    e_d0_rules_digest: str
    execution_rules_digest: str
    selection_rank: int
    alpha_candidate: bool
    selected: bool
    execution_eligible: bool
    entry_model: str
    entry_timestamp_et: str | None
    entry_price: float | None
    skip_reason: str | None


@dataclass(frozen=True)
class ExecutionBatch:
    records: tuple[EntryRecord, ...]
    execution_digest: str


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda value: value.isoformat() if isinstance(value, date) else value,
    ).encode("utf-8")


def _load_rules(path: Path = EXECUTION_RULES_PATH) -> Mapping:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionContractError(f"cannot load frozen Strategy E execution rules: {error}") from error
    found = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if found != EXECUTION_RULES_CANONICAL_SHA256:
        raise ExecutionContractError(
            f"Strategy E execution rules digest mismatch: {found} != "
            f"{EXECUTION_RULES_CANONICAL_SHA256}"
        )
    if payload.get("declaration", {}).get("contract_id") != "STRATEGY_E_EXECUTION_RULES_V1":
        raise ExecutionContractError("unsupported Strategy E execution rules version")
    risk = RiskConfig()
    declared = int(payload["selection"]["max_selected_candidates"])
    if declared != min(risk.max_new_symbols_per_day, risk.max_open_positions):
        raise ExecutionContractError("E-D2 selection limit conflicts with common Risk V1")
    if payload.get("entry", {}).get("model") != ENTRY_MODEL:
        raise ExecutionContractError("unsupported Strategy E entry model")
    return MappingProxyType(payload)


def _base_record(signal: SignalResult, symbol: str, rank: int, *, selected: bool,
                 eligible: bool, price: float | None, reason: str | None) -> EntryRecord:
    return EntryRecord(
        strategy_id=STRATEGY_ID,
        signal_version=SIGNAL_VERSION,
        execution_version=EXECUTION_VERSION,
        session_date=signal.session,
        symbol=symbol,
        decision_time_et=DECISION_TIME_ET,
        signal_digest=signal.decision_digest,
        signal_source_digest=signal.source_digest,
        e_d0_rules_digest=TRADING_RULES_CANONICAL_SHA256,
        execution_rules_digest=EXECUTION_RULES_CANONICAL_SHA256,
        selection_rank=rank,
        alpha_candidate=True,
        selected=selected,
        execution_eligible=eligible,
        entry_model=ENTRY_MODEL,
        entry_timestamp_et=ENTRY_BAR_START_ET if eligible else None,
        entry_price=price,
        skip_reason=reason,
    )


def _validate_signal(signal: SignalResult) -> tuple[str, ...]:
    if signal.strategy_id != STRATEGY_ID or signal.signal_version != SIGNAL_VERSION:
        raise ExecutionContractError("unsupported Strategy E signal identity")
    if signal.rules_digest != TRADING_RULES_CANONICAL_SHA256:
        raise ExecutionContractError("signal does not reference the frozen E-D0 rules")
    if signal.decision_time_et != DECISION_TIME_ET:
        raise ExecutionContractError("signal does not use the frozen 09:25 ET cutoff")
    candidates = tuple(sorted(signal.candidate_symbols))
    if len(candidates) != signal.candidate_count or len(set(candidates)) != len(candidates):
        raise ExecutionContractError("signal candidate identity or count is inconsistent")
    return candidates


def _entry_for_symbol(signal: SignalResult, symbol: str, rank: int,
                      bars: Sequence[EntryBar]) -> EntryRecord:
    symbol_bars = [bar for bar in bars if bar.symbol == symbol]
    if symbol_bars and not any(bar.session_date == signal.session for bar in symbol_bars):
        return _base_record(signal, symbol, rank, selected=True, eligible=False, price=None,
                            reason=SESSION_MISMATCH)
    exact = [bar for bar in symbol_bars
             if bar.session_date == signal.session and bar.bar_start_et == ENTRY_BAR_START_ET]
    if not exact:
        return _base_record(signal, symbol, rank, selected=True, eligible=False, price=None,
                            reason=MISSING_ENTRY_BAR)
    if len(exact) != 1:
        return _base_record(signal, symbol, rank, selected=True, eligible=False, price=None,
                            reason=DUPLICATE_ENTRY_BAR)
    price = exact[0].open
    if isinstance(price, bool) or not isinstance(price, Real) or not isfinite(price) or price <= 0:
        return _base_record(signal, symbol, rank, selected=True, eligible=False, price=None,
                            reason=INVALID_ENTRY_OPEN)
    return _base_record(signal, symbol, rank, selected=True, eligible=True, price=float(price),
                        reason=None)


def build_entry_records(signal: SignalResult, bars: Sequence[EntryBar], *,
                        rules_path: Path = EXECUTION_RULES_PATH,
                        calendar: MarketCalendar | None = None) -> ExecutionBatch:
    """Select H5 candidates and bind only the exact 09:30 aggregate ``open`` field."""
    rules = _load_rules(rules_path)
    candidates = _validate_signal(signal)
    limit = int(rules["selection"]["max_selected_candidates"])
    market_calendar = calendar or MarketCalendar("America/New_York")
    session = market_calendar.session(signal.session)

    records: list[EntryRecord] = []
    for rank, symbol in enumerate(candidates, start=1):
        if rank > limit:
            records.append(_base_record(signal, symbol, rank, selected=False, eligible=False,
                                        price=None, reason=NOT_SELECTED_CAPACITY))
        elif session is None:
            records.append(_base_record(signal, symbol, rank, selected=True, eligible=False,
                                        price=None, reason=INVALID_SESSION))
        elif session.is_early_close:
            records.append(_base_record(signal, symbol, rank, selected=True, eligible=False,
                                        price=None, reason=SHORTENED_SESSION))
        else:
            records.append(_entry_for_symbol(signal, symbol, rank, bars))

    payload = {
        "execution_rules_digest": EXECUTION_RULES_CANONICAL_SHA256,
        "execution_version": EXECUTION_VERSION,
        "signal_digest": signal.decision_digest,
        "signal_source_digest": signal.source_digest,
        "records": [asdict(record) for record in records],
    }
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return ExecutionBatch(tuple(records), digest)

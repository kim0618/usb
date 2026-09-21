"""E-D3 exact-09:34 Trading exit resolver.

The resolver intentionally does not import Strategy E Research labels. It reads one field only:
the ``close`` of the exact 09:34 ET aggregate. It computes no return or PnL.
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
from app.strategy_e.execution import (
    ENTRY_BAR_START_ET,
    ENTRY_MODEL,
    EXECUTION_RULES_CANONICAL_SHA256,
    EXECUTION_VERSION,
    EntryBar,
    EntryRecord,
    ExecutionBatch,
)
from app.strategy_e.signal import SIGNAL_VERSION, STRATEGY_ID, TRADING_RULES_CANONICAL_SHA256


REPO_ROOT = Path(__file__).resolve().parents[3]
EXIT_RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_exit_rules_v1.json"
EXIT_RULES_CANONICAL_SHA256 = (
    "f6f455641efebedfd65777ba4e00c47f1bc0adb60447e4c662d0908d9d38d58b"
)
HORIZON_SEMANTICS_CANONICAL_SHA256 = (
    "f96f78f4bbd4758722ced07f758f8f0e5c10bb7f3e47d96b47642486a3f59603"
)
EXIT_VERSION = "STRATEGY_E_EXIT_V1"
EXIT_MODEL = "FIXED_FIVE_MINUTE_CLOSE_PROXY_V1"
EXIT_BAR_START_ET = "09:34"

VALID_EXIT = "VALID_EXIT"
INVALID_EXIT = "INVALID_EXIT"
NO_VALID_ENTRY = "NO_VALID_ENTRY"
NO_EXIT_NO_VALID_ENTRY = "NO_EXIT_NO_VALID_ENTRY"
MISSING_EXIT_BAR = "NO_TRADE_MISSING_EXIT_BAR"
DUPLICATE_EXIT_BAR = "NO_TRADE_DUPLICATE_EXIT_BAR"
INVALID_EXIT_CLOSE = "NO_TRADE_INVALID_EXIT_CLOSE"
SESSION_MISMATCH = "NO_TRADE_SESSION_MISMATCH"
SYMBOL_MISMATCH = "NO_TRADE_SYMBOL_MISMATCH"
INVALID_SESSION = "NO_TRADE_INVALID_SESSION"
SHORTENED_SESSION = "NO_TRADE_SHORTENED_SESSION"


class ExitContractError(ValueError):
    """Fail-closed rejection of an invalid E-D3 batch or frozen provenance."""


@dataclass(frozen=True)
class ExitRecord:
    strategy_id: str
    signal_version: str
    execution_version: str
    exit_version: str
    session_date: date
    symbol: str
    signal_digest: str
    signal_source_digest: str
    execution_digest: str
    entry_model: str
    entry_timestamp_et: str | None
    entry_price: float | None
    entry_status: str
    exit_model: str
    exit_timestamp_et: str | None
    exit_price: float | None
    exit_valid: bool
    exit_status: str
    exit_reason: str | None
    e_d0_rules_digest: str
    e_d2_execution_rules_digest: str
    e_d3_horizon_semantics_digest: str
    e_d3_exit_rules_digest: str


@dataclass(frozen=True)
class ExitBatch:
    records: tuple[ExitRecord, ...]
    exit_digest: str


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda value: value.isoformat() if isinstance(value, date) else value,
    ).encode("utf-8")


def _load_rules(path: Path = EXIT_RULES_PATH) -> Mapping:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExitContractError(f"cannot load frozen Strategy E exit rules: {error}") from error
    found = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if found != EXIT_RULES_CANONICAL_SHA256:
        raise ExitContractError(
            f"Strategy E exit rules digest mismatch: {found} != {EXIT_RULES_CANONICAL_SHA256}"
        )
    if payload.get("declaration", {}).get("contract_id") != "STRATEGY_E_EXIT_RULES_V1":
        raise ExitContractError("unsupported Strategy E exit rules version")
    if payload.get("declaration", {}).get("version") != EXIT_VERSION:
        raise ExitContractError("unsupported Strategy E exit rules version")
    model = payload.get("model", {})
    if model.get("entry_model") != ENTRY_MODEL or model.get("exit_model") != EXIT_MODEL:
        raise ExitContractError("unsupported Strategy E entry or exit model")
    if model.get("fallback_policy") != "NONE":
        raise ExitContractError("Strategy E V1 exit fallback must remain disabled")
    upstream = payload.get("upstream", {})
    expected = {
        "e_d0_rules_canonical_sha256": TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": EXECUTION_RULES_CANONICAL_SHA256,
        "e_d2_execution_version": EXECUTION_VERSION,
        "e_d3_horizon_semantics_canonical_sha256": HORIZON_SEMANTICS_CANONICAL_SHA256,
    }
    if any(upstream.get(key) != value for key, value in expected.items()):
        raise ExitContractError("Strategy E exit provenance chain does not match frozen upstream")
    return MappingProxyType(payload)


def _entry_status(entry: EntryRecord) -> str:
    return "EXECUTED_PROXY" if entry.selected and entry.execution_eligible else (
        entry.skip_reason or "NOT_EXECUTED"
    )


def _record(entry: EntryRecord, execution_digest: str, *, valid: bool,
            price: float | None, reason: str | None, attempted: bool) -> ExitRecord:
    return ExitRecord(
        strategy_id=STRATEGY_ID,
        signal_version=SIGNAL_VERSION,
        execution_version=EXECUTION_VERSION,
        exit_version=EXIT_VERSION,
        session_date=entry.session_date,
        symbol=entry.symbol,
        signal_digest=entry.signal_digest,
        signal_source_digest=entry.signal_source_digest,
        execution_digest=execution_digest,
        entry_model=entry.entry_model,
        entry_timestamp_et=entry.entry_timestamp_et,
        entry_price=entry.entry_price,
        entry_status=_entry_status(entry),
        exit_model=EXIT_MODEL,
        exit_timestamp_et=EXIT_BAR_START_ET if attempted else None,
        exit_price=price,
        exit_valid=valid,
        exit_status=VALID_EXIT if valid else (INVALID_EXIT if attempted else NO_VALID_ENTRY),
        exit_reason=reason,
        e_d0_rules_digest=TRADING_RULES_CANONICAL_SHA256,
        e_d2_execution_rules_digest=EXECUTION_RULES_CANONICAL_SHA256,
        e_d3_horizon_semantics_digest=HORIZON_SEMANTICS_CANONICAL_SHA256,
        e_d3_exit_rules_digest=EXIT_RULES_CANONICAL_SHA256,
    )


def _validate_entry(entry: EntryRecord) -> None:
    if (entry.strategy_id != STRATEGY_ID or entry.signal_version != SIGNAL_VERSION
            or entry.execution_version != EXECUTION_VERSION):
        raise ExitContractError("unsupported Strategy E entry identity")
    if entry.e_d0_rules_digest != TRADING_RULES_CANONICAL_SHA256:
        raise ExitContractError("entry does not reference frozen E-D0 rules")
    if entry.execution_rules_digest != EXECUTION_RULES_CANONICAL_SHA256:
        raise ExitContractError("entry does not reference frozen E-D2 rules")


def resolve_exit_record(entry: EntryRecord, execution_digest: str,
                        bars: Sequence[EntryBar], *,
                        calendar: MarketCalendar | None = None) -> ExitRecord:
    """Resolve one entry against only its exact 09:34 aggregate close."""
    _validate_entry(entry)
    if not entry.selected or not entry.execution_eligible:
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=NO_EXIT_NO_VALID_ENTRY, attempted=False)
    if (entry.entry_model != ENTRY_MODEL or entry.entry_timestamp_et != ENTRY_BAR_START_ET
            or isinstance(entry.entry_price, bool) or not isinstance(entry.entry_price, Real)
            or not isfinite(entry.entry_price) or entry.entry_price <= 0):
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=NO_EXIT_NO_VALID_ENTRY, attempted=False)

    session = (calendar or MarketCalendar("America/New_York")).session(entry.session_date)
    if session is None:
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=INVALID_SESSION, attempted=True)
    if session.is_early_close:
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=SHORTENED_SESSION, attempted=True)
    if any(bar.symbol != entry.symbol for bar in bars):
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=SYMBOL_MISMATCH, attempted=True)
    if bars and not any(bar.session_date == entry.session_date for bar in bars):
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=SESSION_MISMATCH, attempted=True)

    exact = [bar for bar in bars
             if bar.session_date == entry.session_date and bar.bar_start_et == EXIT_BAR_START_ET]
    if not exact:
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=MISSING_EXIT_BAR, attempted=True)
    if len(exact) != 1:
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=DUPLICATE_EXIT_BAR, attempted=True)
    price = exact[0].close
    if (isinstance(price, bool) or not isinstance(price, Real)
            or not isfinite(price) or price <= 0):
        return _record(entry, execution_digest, valid=False, price=None,
                       reason=INVALID_EXIT_CLOSE, attempted=True)
    return _record(entry, execution_digest, valid=True, price=float(price), reason=None,
                   attempted=True)


def resolve_exit_batch(entries: ExecutionBatch,
                       bars_by_symbol: Mapping[str, Sequence[EntryBar]], *,
                       rules_path: Path = EXIT_RULES_PATH,
                       calendar: MarketCalendar | None = None) -> ExitBatch:
    """Resolve a deterministic entry batch; mapping and row order cannot affect output."""
    _load_rules(rules_path)
    if not entries.execution_digest:
        raise ExitContractError("E-D2 execution digest is required")
    ordered = tuple(sorted(entries.records, key=lambda record: record.symbol))
    if len({record.symbol for record in ordered}) != len(ordered):
        raise ExitContractError("duplicate entry-record symbol identity")
    records = tuple(
        resolve_exit_record(
            entry,
            entries.execution_digest,
            tuple(bars_by_symbol.get(entry.symbol, ())),
            calendar=calendar,
        )
        for entry in ordered
    )
    payload = {
        "execution_digest": entries.execution_digest,
        "exit_rules_digest": EXIT_RULES_CANONICAL_SHA256,
        "exit_version": EXIT_VERSION,
        "records": [asdict(record) for record in records],
    }
    return ExitBatch(records, hashlib.sha256(_canonical_bytes(payload)).hexdigest())

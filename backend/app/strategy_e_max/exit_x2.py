"""E-MAX-M4 X2 momentum-continuation exit, exactly as M0 declared it.

The frozen E-D3 resolver decides every position at the exact 09:34 close first. Only then, for a
position whose 09:34 exit is VALID and whose 09:34 close is strictly above its 09:30 entry open
(raw prices, no cost, no threshold), X2 holds to the exact 09:44 ET close. The hold decision uses
only the 09:30 open and the 09:34 close, both known once the 09:34 bar has closed (09:35:00 ET).

The 09:44 bar is validated with E-D3's own fail-closed rules (missing, duplicate, non-finite or
non-positive close, wrong session or symbol) and never replaced: an extended position without a
valid exact 09:44 bar is UNRESOLVED_EXIT / INVALID_FOR_STANDARD_PNL. A position that was not
extended keeps its E-D3 record byte for byte, so X1 and X2 can only differ on trades that were in
profit at 09:34.

Everything else (entry, chunked E-D2 / E-D3 calls, E-D5 classification, 1/n weights, E-D4 costs)
is ``capacity.execute``'s logic; with ``extend=False`` the output equals it exactly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import hashlib
from math import isfinite
from numbers import Real
from typing import Any

from app.strategy_e import exits, risk
from app.strategy_e.costs import CostScenario, apply_cost
from app.strategy_e.execution import ENTRY_BAR_START_ET, build_entry_records
from app.strategy_e.exits import (
    DUPLICATE_EXIT_BAR, EXIT_BAR_START_ET, INVALID_EXIT, INVALID_EXIT_CLOSE, MISSING_EXIT_BAR,
    SESSION_MISMATCH, SYMBOL_MISMATCH, VALID_EXIT, ExitRecord, resolve_exit_batch,
)
from app.strategy_e.risk import SIZED
from app.strategy_e_max.capacity import GROSS, SCENARIOS, CapacityError, chunks

EXTENDED_BAR_START_ET = "09:44"
X2_MODEL = "E_MAX_X2_MOMENTUM_CONTINUATION_0944_V1"


def continues(record: ExitRecord) -> bool:
    """The frozen X2 condition: a valid 09:34 exit with close(09:34) > open(09:30), raw prices."""
    return (record.exit_valid and record.exit_status == VALID_EXIT
            and record.exit_price is not None and record.entry_price is not None
            and record.exit_price > record.entry_price)


def extend(record: ExitRecord, bars: Sequence) -> ExitRecord:
    """Re-resolve a continuing position at the exact 09:44 close with E-D3's fail-closed rules."""
    if any(bar.symbol != record.symbol for bar in bars):
        return _unresolved(record, SYMBOL_MISMATCH)
    if bars and not any(bar.session_date == record.session_date for bar in bars):
        return _unresolved(record, SESSION_MISMATCH)
    exact = [bar for bar in bars if bar.session_date == record.session_date
             and bar.bar_start_et == EXTENDED_BAR_START_ET]
    if not exact:
        return _unresolved(record, MISSING_EXIT_BAR)
    if len(exact) != 1:
        return _unresolved(record, DUPLICATE_EXIT_BAR)
    price = exact[0].close
    if isinstance(price, bool) or not isinstance(price, Real) or not isfinite(price) or price <= 0:
        return _unresolved(record, INVALID_EXIT_CLOSE)
    return replace(record, exit_model=X2_MODEL, exit_timestamp_et=EXTENDED_BAR_START_ET,
                   exit_price=float(price), exit_valid=True, exit_status=VALID_EXIT,
                   exit_reason=None)


def _unresolved(record: ExitRecord, reason: str) -> ExitRecord:
    return replace(record, exit_model=X2_MODEL, exit_timestamp_et=EXTENDED_BAR_START_ET,
                   exit_price=None, exit_valid=False, exit_status=INVALID_EXIT, exit_reason=reason)


def execute(signal, selected: Sequence[str], bars: Mapping[str, Mapping[str, Sequence]],
            descriptors: Mapping[str, Mapping[str, float]], calendar, *, capacity: int,
            continuation: bool) -> dict[str, Any]:
    """``capacity.execute`` with the optional X2 continuation applied after E-D3."""
    if len(selected) > capacity or len(set(selected)) != len(selected):
        raise CapacityError(f"{len(selected)} selected exceeds capacity {capacity} or repeats")
    if not set(selected) <= set(signal.candidate_symbols):
        raise CapacityError("a selected symbol is not an H5 candidate of this session")
    exits._load_rules()
    risk._load_rules()
    staged, execution_digests, exit_digests = [], [], []
    decisions = []
    for chunk in chunks(selected):
        subset = replace(signal, candidate_symbols=chunk, candidate_count=len(chunk))
        entry_bars = [bar for s in chunk for bar in bars.get(s, {}).get(ENTRY_BAR_START_ET, ())]
        batch = build_entry_records(subset, entry_bars, calendar=calendar)
        exit_batch = resolve_exit_batch(
            batch, {s: tuple(bars.get(s, {}).get(EXIT_BAR_START_ET, ())) for s in chunk},
            calendar=calendar)
        execution_digests.append(batch.execution_digest)
        exit_digests.append(exit_batch.exit_digest)
        entries = {record.symbol: record for record in batch.records}
        for record in exit_batch.records:
            x1 = record
            if continuation and continues(record):
                record = extend(record, tuple(bars.get(record.symbol, {}).get(EXTENDED_BAR_START_ET, ())))
            decisions.append({"symbol": record.symbol,
                              "x1_exit_status": x1.exit_status, "x1_exit_price": x1.exit_price,
                              "extended": record is not x1,
                              "final_exit_timestamp_et": record.exit_timestamp_et,
                              "final_exit_status": record.exit_status,
                              "final_exit_reason": record.exit_reason,
                              "final_exit_price": record.exit_price,
                              "entry_price": record.entry_price})
            staged.append((entries[record.symbol], record, exit_batch.exit_digest))
    staged.sort(key=lambda item: item[1].symbol)
    classified = [(entry, record, digest, risk._classification(record))
                  for entry, record, digest in staged]
    executable = sum(int(values[2]) for *_, values in classified)
    weight = Fraction(1, executable) if executable else Fraction(0, 1)

    rows, returns = [], {name: Decimal(0) for name in SCENARIOS}
    exposure = Fraction(0, 1)
    for entry, record, digest, (_, _, is_exec, status, skip) in classified:
        w = weight if is_exec else Fraction(0, 1)
        exposure += w
        row: dict[str, Any] = {
            "session": signal.session.isoformat(), "symbol": record.symbol,
            "selection_rank": entry.selection_rank, "selected": entry.selected,
            "entry_status": record.entry_status, "entry_price": entry.entry_price,
            "exit_status": record.exit_status, "exit_reason": record.exit_reason,
            "exit_price": record.exit_price, "risk_status": status, "skip_reason": skip,
            "weight": f"{w.numerator}/{w.denominator}", "standard_pnl": status == SIZED,
            **descriptors[record.symbol],
        }
        if status == SIZED:
            dw = Decimal(w.numerator) / Decimal(w.denominator)
            for scenario in CostScenario:
                cost = apply_cost(record, digest, scenario)
                row[scenario.value] = cost.net_return
                returns[scenario.value] += dw * cost.net_return
                row[GROSS] = cost.gross_return
            returns[GROSS] += dw * row[GROSS]
        rows.append(row)

    def digest_of(parts: Sequence[str]) -> str:
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    return {"session": signal.session.isoformat(), "eligible": signal.eligible_count,
            "active": any(r["standard_pnl"] for r in rows), "exposure": str(exposure),
            "returns": returns, "records": rows,
            "exit_decisions": sorted(decisions, key=lambda d: d["symbol"]),
            "digests": {"signal": signal.decision_digest,
                        "execution": digest_of(execution_digests), "exit": digest_of(exit_digests),
                        "sizing": digest_of([f"{r['symbol']}:{r['weight']}" for r in rows])}}

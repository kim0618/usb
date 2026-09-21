"""E-MAX capacity execution: more than three selected symbols through the frozen layers, unmodified.

E-D2's rules fix ``max_selected_candidates = 3`` and E-D5 refuses more than three selected
positions, as Common Risk V1 requires. E-MAX-M2's C2 (max 5) is research-only and
not Common-Risk compliant, so it is executed here without touching either module:

* entry and exit are per-symbol decisions in E-D2 / E-D3 (a symbol's result depends only on its
  own bars and the session), so the selected set is fed to ``build_entry_records`` and
  ``resolve_exit_batch`` in canonical chunks of at most three;
* each exit record is classified by E-D5's own ``_classification``; the executable ones receive
  equal weights ``1 / n`` (normalized session gross exposure 1.0, as E-D5 does for n <= 3);
* costs come from E-D4 ``apply_cost`` unchanged.

With ``selected`` of three or fewer this reproduces ``strategy_e_d6.replay.replay_session``
record for record (checked before any M2 result is read).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import hashlib
from typing import Any

from app.strategy_e import exits, risk
from app.strategy_e.costs import CostScenario, apply_cost
from app.strategy_e.execution import ENTRY_BAR_START_ET, build_entry_records
from app.strategy_e.exits import EXIT_BAR_START_ET, resolve_exit_batch
from app.strategy_e.risk import SIZED

GROSS = "GROSS_0BP"
SCENARIOS = (GROSS,) + tuple(s.value for s in CostScenario)
CHUNK = 3


class CapacityError(ValueError):
    """The selected set is not a valid capacity selection."""


def chunks(selected: Sequence[str]) -> list[tuple[str, ...]]:
    ordered = sorted(selected)
    return [tuple(ordered[i:i + CHUNK]) for i in range(0, len(ordered), CHUNK)]


def execute(signal, selected: Sequence[str], bars: Mapping[str, Mapping[str, Sequence]],
            descriptors: Mapping[str, Mapping[str, float]], calendar,
            *, capacity: int) -> dict[str, Any]:
    """One session: entry, exit, equal weights and costs for ``selected`` (no replacement)."""
    if len(selected) > capacity or len(set(selected)) != len(selected):
        raise CapacityError(f"{len(selected)} selected exceeds capacity {capacity} or repeats")
    if not set(selected) <= set(signal.candidate_symbols):
        raise CapacityError("a selected symbol is not an H5 candidate of this session")
    exits._load_rules()
    risk._load_rules()
    staged = []
    execution_digests, exit_digests = [], []
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
            "digests": {"signal": signal.decision_digest,
                        "execution": digest_of(execution_digests), "exit": digest_of(exit_digests),
                        "sizing": digest_of([f"{r['symbol']}:{r['weight']}" for r in rows])}}

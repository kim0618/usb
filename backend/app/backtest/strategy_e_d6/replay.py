"""Session-by-session replay of frozen Strategy E V1 through the E-D1..E-D5 modules.

Nothing here decides anything. H5 comes from ``evaluate_h5_signal`` (which calls Research's
mask), selection and the 09:30 open from ``build_entry_records``, the 09:34 close from
``resolve_exit_batch``, weights from ``build_sizing_records`` and costs from ``apply_cost``. This
module only feeds each layer its own inputs and records what came back.

The point-in-time boundary is structural. The signal layer receives only the sealed 09:25
features. Minute bars are fetched afterwards, only for H5 candidates, and each execution layer is
handed only the exact bars of its own timestamp: 09:30 bars to entry, 09:34 bars to exit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e1_premarket.dataset import PremarketRows
from app.market.calendar import MarketCalendar
from app.strategy_e.costs import CostScenario, apply_cost
from app.strategy_e.execution import ENTRY_BAR_START_ET, EntryBar, build_entry_records
from app.strategy_e.exits import EXIT_BAR_START_ET, resolve_exit_batch
from app.strategy_e.risk import SIZED, build_sizing_records
from app.strategy_e.signal import SignalFrame, SignalResult, evaluate_h5_signal


GROSS = "GROSS_0BP"
SCENARIOS = (GROSS,) + tuple(scenario.value for scenario in CostScenario)
SCENARIO_BP = {GROSS: 0, "COST_05BP": 5, "COST_10BP": 10, "COST_15BP": 15, "COST_20BP": 20}
PRIMARY = "COST_10BP"
ENTRY_MINUTE = M.REGULAR_OPEN_MIN          # 09:30
EXIT_MINUTE = M.REGULAR_OPEN_MIN + 4       # 09:34
BAR_MINUTES = {ENTRY_MINUTE: ENTRY_BAR_START_ET, EXIT_MINUTE: EXIT_BAR_START_ET}
#: Pre-trade descriptors carried to the trade table for diagnostics only; no layer reads them.
DESCRIPTORS = ("close_price", "previous_day_dollar_volume")
RESEARCH_LABELS = ("R_5m", "R_5m_strict")


@dataclass(frozen=True)
class SessionFrame:
    session: date
    symbols: tuple[str, ...]
    features: dict[str, np.ndarray]
    descriptors: dict[str, dict[str, float]]      # symbol -> descriptor/label values


def session_frames(rows: PremarketRows) -> list[SessionFrame]:
    """Split the declared universe into one symbol-sorted 09:25 frame per session."""
    frames = []
    sessions = rows.sessions
    for session in sorted(set(sessions.tolist())):
        index = np.flatnonzero(sessions == session)
        index = index[np.argsort(rows.tickers[index].astype(str), kind="stable")]
        symbols = tuple(str(s) for s in rows.tickers[index])
        features = {name: np.asarray(rows.features[name][index], dtype=np.float64)
                    for name in SEALED_FEATURES}
        descriptors = {
            symbol: {**{name: float(rows.features[name][i]) for name in DESCRIPTORS},
                     **{name: float(rows.labels[name][i]) for name in RESEARCH_LABELS}}
            for symbol, i in zip(symbols, index)}
        frames.append(SessionFrame(session, symbols, features, descriptors))
    return frames


def evaluate_signals(frames: Sequence[SessionFrame], source_digest: str) -> list[SignalResult]:
    return [evaluate_h5_signal(SignalFrame(session=f.session, symbols=f.symbols,
                                           features=f.features, source_digest=source_digest))
            for f in frames]


def exact_bars(tape: M.SymbolTape, sessions: Sequence[date]) -> dict[date, dict[str, list[EntryBar]]]:
    """Every vendor bar starting exactly at 09:30 or 09:34 ET on the requested sessions.

    Duplicates are passed through as found, so the execution layers can reject them; nothing is
    filled, shifted or taken from a neighbouring minute.
    """
    out: dict[date, dict[str, list[EntryBar]]] = {}
    for session in sessions:
        day = np.flatnonzero(tape.et_day == M.ordinal(session))
        found: dict[str, list[EntryBar]] = {label: [] for label in BAR_MINUTES.values()}
        for i in day:
            label = BAR_MINUTES.get(int(tape.minute[i]))
            if label is None:
                continue
            vwap = float(tape.vwap[i])
            found[label].append(EntryBar(
                symbol=tape.symbol, session_date=session, bar_start_et=label,
                open=float(tape.open[i]), high=float(tape.high[i]), low=float(tape.low[i]),
                close=float(tape.close[i]), volume=float(tape.volume[i]),
                vwap=vwap if np.isfinite(vwap) else None))
        out[session] = found
    return out


def _decimal(weight: Fraction) -> Decimal:
    return Decimal(weight.numerator) / Decimal(weight.denominator)


def replay_session(signal: SignalResult, frame: SessionFrame,
                   bars: Mapping[str, Mapping[str, Sequence[EntryBar]]],
                   calendar: MarketCalendar) -> dict[str, Any]:
    """Run one session's H5 candidates through E-D2, E-D3, E-D5 and E-D4.

    ``bars`` maps symbol -> {"09:30": [...], "09:34": [...]} for that session.
    """
    entry_bars = [bar for symbol in signal.candidate_symbols
                  for bar in bars.get(symbol, {}).get(ENTRY_BAR_START_ET, ())]
    entries = build_entry_records(signal, entry_bars, calendar=calendar)
    exits = resolve_exit_batch(
        entries, {symbol: tuple(bars.get(symbol, {}).get(EXIT_BAR_START_ET, ()))
                  for symbol in signal.candidate_symbols}, calendar=calendar)
    sizing = build_sizing_records(exits)

    entry_by_symbol = {record.symbol: record for record in entries.records}
    exit_by_symbol = {record.symbol: record for record in exits.records}
    records = []
    session_return = {name: Decimal(0) for name in SCENARIOS}
    for sized in sizing.records:
        entry, exit_ = entry_by_symbol[sized.symbol], exit_by_symbol[sized.symbol]
        row: dict[str, Any] = {
            "session": signal.session.isoformat(), "symbol": sized.symbol,
            "selection_rank": entry.selection_rank, "selected": entry.selected,
            "entry_status": exit_.entry_status, "entry_price": entry.entry_price,
            "exit_status": exit_.exit_status, "exit_reason": exit_.exit_reason,
            "exit_price": exit_.exit_price, "risk_status": sized.risk_status,
            "skip_reason": sized.skip_reason,
            "weight": f"{sized.normalized_weight.numerator}/{sized.normalized_weight.denominator}",
            "standard_pnl": sized.risk_status == SIZED,
            **frame.descriptors[sized.symbol],
        }
        if sized.risk_status == SIZED:
            weight = _decimal(sized.normalized_weight)
            for scenario in CostScenario:
                cost = apply_cost(exit_, exits.exit_digest, scenario)
                row[scenario.value] = cost.net_return
                session_return[scenario.value] += weight * cost.net_return
                row[GROSS] = cost.gross_return
            session_return[GROSS] += weight * row[GROSS]
        records.append(row)
    return {
        "session": signal.session.isoformat(),
        "eligible": signal.eligible_count,
        "candidates": signal.candidate_count,
        "active": any(r["standard_pnl"] for r in records),
        "exposure": str(sizing.normalized_daily_gross_exposure),
        "returns": session_return,
        "records": records,
        "digests": {"signal": signal.decision_digest, "execution": entries.execution_digest,
                    "exit": exits.exit_digest, "sizing": sizing.sizing_digest},
    }

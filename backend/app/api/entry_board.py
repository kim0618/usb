"""Read-only Trading entry-board projection over the current ScannerRun authority.

The board lists exactly the candidates the entry runtime evaluates for the current
run's entry session, in the runtime's own fixed order (GPT rank, then symbol), next
to the StrategyState persisted for that session. It never fetches market data and
never writes; a value the backend did not persist is returned as null, not derived.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.service import decimal_string
from app.market.calendar import MarketCalendar
from app.models.research import GPTCandidateAnalysis
from app.models.scanner import ScannerRun
from app.models.strategy import PremarketDiagnosticRecord, StrategyStateRecord
from app.research.authority import CurrentAuthorityStatus, ResearchAuthorityService
from app.services.entry_management_runtime import intended_entry_bar_at, load_approved_candidates
from app.strategy.config import StrategyConfig
from app.strategy.lifecycle import StrategyPhase


class EntryBoardStatus(StrEnum):
    NO_SCANNER_RUN = "NO_SCANNER_RUN"
    # The latest run's entry session has already passed: today's run is missing.
    SCANNER_RUN_OUTDATED = "SCANNER_RUN_OUTDATED"
    NO_ACTIVE_ANALYSIS = "NO_ACTIVE_ANALYSIS"
    NO_APPROVALS = "NO_APPROVALS"
    READY = "READY"


#: A signal's price and bar survive into NO_TRADE when the signal ended unsettled.
SIGNAL_PHASES = frozenset({StrategyPhase.ENTRY_SIGNALLED.value, StrategyPhase.NO_TRADE.value})


def thresholds(config: StrategyConfig) -> dict[str, str]:
    return {"strategy_version": config.version,
            "premarket_gap_min_pct": str(config.premarket_gap_min_pct),
            "premarket_gap_max_pct": str(config.premarket_gap_max_pct),
            "premarket_volume_ratio_min": str(config.premarket_volume_ratio_min)}


def state_dict(row: StrategyStateRecord, fill_delay_bars: int) -> dict[str, Any]:
    signal_at = (row.last_market_as_of
                 if row.phase in SIGNAL_PHASES and row.entry_price is not None else None)
    return {"phase": row.phase, "phase_reason": row.phase_reason,
            "entry_price": decimal_string(row.entry_price),
            "initial_stop": decimal_string(row.initial_stop),
            "active_stop": decimal_string(row.active_stop),
            "signal_at": signal_at,
            "intended_execution_bar_at": None if signal_at is None
            else intended_entry_bar_at(signal_at, fill_delay_bars),
            "updated_at": row.updated_at}


def diagnostic_dict(row: PremarketDiagnosticRecord) -> dict[str, Any]:
    return {"previous_close": decimal_string(row.previous_close),
            "reference_price": decimal_string(row.reference_price),
            "gap_pct": decimal_string(row.gap_pct),
            "premarket_volume": decimal_string(row.premarket_volume),
            "historical_average_daily_volume": decimal_string(row.historical_average_daily_volume),
            "volume_ratio": decimal_string(row.volume_ratio),
            "minute_bars_count": row.minute_bars_count,
            "premarket_bars_count": row.premarket_bars_count,
            "first_timestamp": row.first_timestamp, "last_timestamp": row.last_timestamp,
            "invalid_field": row.invalid_field}


def entry_board(session: Session, *, calendar: MarketCalendar, as_of: datetime,
                fill_delay_bars: int, allow_outdated: bool = False,
                config: StrategyConfig | None = None) -> dict[str, Any]:
    authority = ResearchAuthorityService(session).current()
    run, analysis = authority.run, authority.analysis
    entry_session = None if run is None else calendar.next_trading_day(run.trading_date)
    board: dict[str, Any] = {
        "status": EntryBoardStatus.NO_SCANNER_RUN.value,
        "scanner_run_id": None if run is None else run.id,
        "analysis_session_date": None if run is None else run.trading_date,
        "entry_session_date": entry_session,
        "analysis_id": None, "analysis_at": None,
        "thresholds": thresholds(config or StrategyConfig()), "candidates": [],
    }
    if run is None or entry_session is None:
        return board
    if entry_session < as_of.astimezone(calendar.timezone).date() and not allow_outdated:
        return {**board, "status": EntryBoardStatus.SCANNER_RUN_OUTDATED.value}
    if authority.status is CurrentAuthorityStatus.NO_ACTIVE_ANALYSIS or analysis is None:
        return {**board, "status": EntryBoardStatus.NO_ACTIVE_ANALYSIS.value}
    board.update(analysis_id=analysis.id, analysis_at=analysis.analysis_at)
    candidates = [candidate for candidate in _approved(session, run)
                  if candidate.analysis_id == analysis.id]
    if not candidates:
        return {**board, "status": EntryBoardStatus.NO_APPROVALS.value}
    board["status"] = EntryBoardStatus.READY.value
    board["candidates"] = _rows(session, analysis.id, entry_session, candidates, fill_delay_bars)
    return board


def _approved(session: Session, run: ScannerRun):  # type: ignore[no-untyped-def]
    # The runtime's own loader: the board can never list a symbol the runtime skips.
    return [candidate for candidate in load_approved_candidates(session, run.trading_date)
            if candidate.scanner_run_id == run.id]


def _rows(session: Session, analysis_id: int, entry_session: date, candidates: list,  # type: ignore[type-arg]
          fill_delay_bars: int) -> list[dict[str, Any]]:
    symbols = [candidate.symbol for candidate in candidates]
    ranks = {row.symbol: row.gpt_rank for row in session.scalars(select(GPTCandidateAnalysis).where(
        GPTCandidateAnalysis.gpt_analysis_id == analysis_id, GPTCandidateAnalysis.symbol.in_(symbols)))}
    states = {row.symbol: row for row in session.scalars(select(StrategyStateRecord).where(
        StrategyStateRecord.trading_date == entry_session, StrategyStateRecord.book == "ACTUAL",
        StrategyStateRecord.variant == "ACTUAL", StrategyStateRecord.symbol.in_(symbols)))}
    state_ids = [row.id for row in states.values()]
    diagnostics = {row.strategy_state_id: row for row in session.scalars(
        select(PremarketDiagnosticRecord).where(PremarketDiagnosticRecord.strategy_state_id.in_(state_ids))
    )} if state_ids else {}
    rows = []
    for candidate in candidates:
        state = states.get(candidate.symbol)
        # The runtime skips a symbol whose date is owned by another candidate, so that
        # state is not this candidate's progress and is reported as a conflict instead.
        conflict = state is not None and state.scanner_candidate_id != candidate.candidate_id
        own = None if state is None or conflict else state
        diagnostic = None if own is None else diagnostics.get(own.id)
        rows.append({"rank": ranks.get(candidate.symbol), "symbol": candidate.symbol,
                     "scanner_candidate_id": candidate.candidate_id, "exchange": candidate.exchange or None,
                     "state_conflict": conflict,
                     "state": None if own is None else state_dict(own, fill_delay_bars),
                     "premarket": None if diagnostic is None else diagnostic_dict(diagnostic)})
    return rows

"""Read-only Strategy Performance projection over finished entry sessions.

This is the counterpart of the Trading entry board: the board is the current run's
live state, and this is the history of sessions that have already been judged. It
reads durable records and nothing else - it fetches no market data, evaluates no
strategy rule, and computes no gap, ratio, or threshold of its own. A value the
backend never stored comes back as null.

Two sources answer for a day, and every row says which one it came from:

``EVALUATION``    the Entry runtime's own ``paper_entry_evaluations`` row.
``LEGACY_STATE``  a session that predates that table, projected read-only from the
                  StrategyState, premarket diagnostic, and trade rows it did write.
                  Nothing is written back, and a reason those records never held is
                  reported as INCOMPLETE_LEGACY rather than reconstructed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.service import decimal_string
from app.market.calendar import MarketCalendar
from app.models.analytics import PaperEntryEvaluation
from app.models.scanner import ScannerRun
from app.models.simulation import SimulationTradeRecord
from app.models.strategy import PremarketDiagnosticRecord, StrategyStateRecord
from app.services.entry_evaluation_log import (
    FILLED_PHASES, EvaluationStatus, load_candidate_context, resolve, thresholds,
)
from app.services.entry_management_runtime import analysis_session_date, load_approved_candidates
from app.strategy.config import StrategyConfig
from app.strategy.engine import StrategyReason
from app.strategy.lifecycle import StrategyPhase

#: Where a day's rows came from; the UI labels a legacy day rather than hiding it.
SOURCE_EVALUATION = "EVALUATION"
SOURCE_LEGACY_STATE = "LEGACY_STATE"

GAP_REJECT_REASONS = frozenset({StrategyReason.GAP_TOO_LOW.value, StrategyReason.GAP_TOO_HIGH.value})
VOLUME_REJECT_REASON = StrategyReason.LOW_PREMARKET_VOLUME.value
OR_REJECT_REASON = StrategyReason.INSUFFICIENT_OPENING_RANGE.value
#: Statuses that mean the day has no answer for that candidate, not that it was excluded.
UNRESOLVED_STATUSES = frozenset({EvaluationStatus.IN_PROGRESS.value,
                                 EvaluationStatus.INCOMPLETE.value,
                                 EvaluationStatus.UNKNOWN.value})

#: Phases that prove the premarket gate passed; every other phase is before or at it.
PRE_GATE_PHASES = frozenset({StrategyPhase.RESEARCH_READY.value, StrategyPhase.HUMAN_APPROVED.value,
                             StrategyPhase.HUMAN_REJECTED.value,
                             StrategyPhase.PREMARKET_REJECTED.value})
#: Phases only reachable once a complete opening range was evaluated.
POST_OPENING_RANGE_PHASES = frozenset({StrategyPhase.WAITING_ENTRY.value,
                                       StrategyPhase.ENTRY_SIGNALLED.value}) | FILLED_PHASES
#: NO_TRADE reasons that themselves say whether a complete opening range existed.
OR_READY_BY_REASON = {
    OR_REJECT_REASON: False,
    StrategyReason.ENTRY_DEADLINE_EXPIRED.value: True,
    StrategyReason.ENTRY_SIGNAL_STALE.value: True,
    StrategyReason.ENTRY_SESSION_ENDED.value: True,
    StrategyReason.ENTRY_PRICE_ABOVE_CEILING.value: True,
    StrategyReason.ENTRY_RISK_REJECTED.value: True,
    StrategyReason.ENTRY_EXECUTION_REJECTED.value: True,
}


def _trade_dict(trade: SimulationTradeRecord | None) -> dict[str, Any]:
    if trade is None:
        return {"trade_status": None, "realized_pnl": None, "net_r": None, "exit_reason": None}
    closed = trade.status == "CLOSED"
    return {"trade_status": trade.status,
            "realized_pnl": decimal_string(trade.net_pnl) if closed else None,
            "net_r": decimal_string(trade.net_r) if closed else None,
            "exit_reason": trade.exit_reason}


def evaluation_row(row: PaperEntryEvaluation, trade: SimulationTradeRecord | None) -> dict[str, Any]:
    """One stored evaluation, serialized exactly as the runtime recorded it."""
    return {
        "source": SOURCE_EVALUATION, "rank": row.rank, "symbol": row.symbol,
        "exchange": row.exchange, "scanner_candidate_id": row.scanner_candidate_id,
        "final_status": row.final_status, "final_reason": row.final_reason,
        "final_detail": row.final_detail, "last_phase": row.last_phase,
        "premarket_passed": row.premarket_passed, "opening_range_ready": row.opening_range_ready,
        "entry_signalled": row.entry_signalled, "entry_filled": row.entry_filled,
        "previous_close": decimal_string(row.previous_close),
        "premarket_reference_price": decimal_string(row.premarket_reference_price),
        "gap_pct": decimal_string(row.gap_pct), "gap_min": decimal_string(row.gap_min),
        "gap_max": decimal_string(row.gap_max),
        "premarket_volume": decimal_string(row.premarket_volume),
        "historical_average_daily_volume": decimal_string(row.historical_average_daily_volume),
        "v1_volume_ratio": decimal_string(row.v1_volume_ratio),
        "v1_volume_min": decimal_string(row.v1_volume_min),
        "thresholds_source": "STORED" if row.gap_min is not None else None,
        "opening_range_high": decimal_string(row.opening_range_high),
        "opening_range_low": decimal_string(row.opening_range_low),
        "signal_price": decimal_string(row.signal_price),
        "initial_stop": decimal_string(row.initial_stop),
        "signal_at": row.signal_at, "intended_entry_bar_at": row.intended_entry_bar_at,
        "fill_price": decimal_string(row.fill_price),
        "fill_quantity": decimal_string(row.fill_quantity),
        "order_id": row.order_id, "last_progress_reason": row.last_progress_reason,
        "last_error_code": row.last_error_code, "error_tick_count": row.error_tick_count,
        "strategy_version": row.strategy_version, "evaluated_at": row.evaluated_at,
        "finalized_at": row.finalized_at, **_trade_dict(trade),
    }


def _legacy_opening_range_ready(state: StrategyStateRecord) -> bool | None:
    """Whether a complete opening range existed, when the stored phase or reason says so.

    A state reaches WAITING_ENTRY only after a complete range was evaluated, and each
    NO_TRADE reason listed above is reachable from exactly one side of that line. Any
    other combination - a reasonless NO_TRADE above all - is left unknown.
    """
    if state.phase in POST_OPENING_RANGE_PHASES:
        return True
    if state.phase == StrategyPhase.NO_TRADE.value:
        return OR_READY_BY_REASON.get(state.phase_reason or "")
    if state.phase in PRE_GATE_PHASES:
        return False
    return None


def legacy_row(candidate, state: StrategyStateRecord | None,  # type: ignore[no-untyped-def]
               diagnostic: PremarketDiagnosticRecord | None,
               trade: SimulationTradeRecord | None, config: StrategyConfig) -> dict[str, Any]:
    """Project one pre-table session's candidate from the records that session wrote.

    Only stored values are read. The thresholds are reported when the state records
    the strategy version now in force - the same frozen policy judged it - and null
    otherwise, so a past rejection is never explained against a threshold it was not
    measured by.
    """
    invalid_field = None if diagnostic is None else diagnostic.invalid_field
    resolution = resolve(state, invalid_field)
    signalled = state is not None and (
        state.phase in POST_OPENING_RANGE_PHASES - {StrategyPhase.WAITING_ENTRY.value}
        or (state.phase == StrategyPhase.NO_TRADE.value and state.entry_price is not None))
    same_policy = state is not None and state.strategy_version == config.version
    limits = thresholds(config) if same_policy else {}
    filled = state is not None and state.phase in FILLED_PHASES
    return {
        "source": SOURCE_LEGACY_STATE, "rank": candidate.rank, "symbol": candidate.symbol,
        "exchange": candidate.exchange or None,
        "scanner_candidate_id": candidate.candidate_id,
        "final_status": resolution.status.value, "final_reason": resolution.reason,
        "final_detail": resolution.detail,
        "last_phase": None if state is None else state.phase,
        "premarket_passed": None if state is None else state.phase not in PRE_GATE_PHASES,
        "opening_range_ready": None if state is None else _legacy_opening_range_ready(state),
        "entry_signalled": None if state is None else signalled,
        "entry_filled": None if state is None else filled,
        "previous_close": None if diagnostic is None else decimal_string(diagnostic.previous_close),
        "premarket_reference_price": None if diagnostic is None else decimal_string(diagnostic.reference_price),
        "gap_pct": None if diagnostic is None else decimal_string(diagnostic.gap_pct),
        "gap_min": decimal_string(limits.get("gap_min")),
        "gap_max": decimal_string(limits.get("gap_max")),
        "premarket_volume": None if diagnostic is None else decimal_string(diagnostic.premarket_volume),
        "historical_average_daily_volume": None if diagnostic is None
        else decimal_string(diagnostic.historical_average_daily_volume),
        "v1_volume_ratio": None if diagnostic is None else decimal_string(diagnostic.volume_ratio),
        "v1_volume_min": decimal_string(limits.get("v1_volume_min")),
        "thresholds_source": "STRATEGY_VERSION_MATCH" if same_policy else None,
        # The opening range itself was never stored before this table existed.
        "opening_range_high": None, "opening_range_low": None,
        "signal_price": decimal_string(state.entry_price) if signalled and state else None,
        "initial_stop": decimal_string(state.initial_stop) if signalled and state else None,
        "signal_at": state.last_market_as_of if signalled and state and not filled else None,
        "intended_entry_bar_at": None,
        "fill_price": decimal_string(state.entry_price) if filled and state else None,
        "fill_quantity": None if trade is None else decimal_string(trade.initial_quantity),
        "order_id": None, "last_progress_reason": None, "last_error_code": None,
        "error_tick_count": 0,
        "strategy_version": None if state is None else state.strategy_version,
        "evaluated_at": None if state is None else state.updated_at,
        "finalized_at": None, **_trade_dict(trade),
    }


def summarize(trading_date: date, rows: Sequence[dict[str, Any]], source: str) -> dict[str, Any]:
    """Count one day's funnel from its own rows; a zero-trade day is a normal row."""
    reasons = [row["final_reason"] for row in rows]
    statuses = [row["final_status"] for row in rows]
    filled = statuses.count(EvaluationStatus.TRADED.value)
    traded = [row for row in rows if row["final_status"] == EvaluationStatus.TRADED.value]
    # A realized result belongs to this day's own entries, so it is counted from the
    # rows that traded, never from any other row that happens to carry a trade.
    closed = [row for row in traded if row["realized_pnl"] is not None]
    # Only fully closed trades carry a realized result; a carry still open has none,
    # and a day with no entry at all realized exactly nothing.
    realized = (Decimal("0") if not traded else
                sum((Decimal(row["realized_pnl"]) for row in closed), Decimal("0"))
                if closed else None)
    total_r = (Decimal("0") if not traded else
               sum((Decimal(row["net_r"]) for row in closed), Decimal("0")) if closed else None)
    return {
        "trading_date": trading_date, "source": source,
        "approved_count": len(rows),
        "premarket_pass_count": sum(1 for row in rows if row["premarket_passed"] is True),
        "gap_rejected_count": sum(1 for reason in reasons if reason in GAP_REJECT_REASONS),
        "volume_rejected_count": reasons.count(VOLUME_REJECT_REASON),
        "or_ready_count": sum(1 for row in rows if row["opening_range_ready"] is True),
        "or_rejected_count": reasons.count(OR_REJECT_REASON),
        "signal_count": sum(1 for row in rows if row["entry_signalled"] is True),
        "execution_rejected_count": statuses.count(EvaluationStatus.EXECUTION_REJECTED.value),
        "no_entry_signal_count": statuses.count(EvaluationStatus.NO_ENTRY_SIGNAL.value),
        "premarket_rejected_count": statuses.count(EvaluationStatus.PREMARKET_REJECTED.value),
        "filled_count": filled,
        "data_error_count": statuses.count(EvaluationStatus.DATA_ERROR.value),
        "incomplete_count": sum(1 for status in statuses if status in UNRESOLVED_STATUSES),
        "trade_count": filled,
        "open_trade_count": len(traded) - len(closed),
        "realized_pnl": decimal_string(realized), "total_r": decimal_string(total_r),
        "result": "TRADED" if filled else "NO_TRADE_DAY",
    }


def _evaluation_days(session: Session, limit: int) -> list[date]:
    return list(session.scalars(select(PaperEntryEvaluation.trading_date).distinct()
                                .order_by(PaperEntryEvaluation.trading_date.desc()).limit(limit)))


def _legacy_days(session: Session, calendar: MarketCalendar, limit: int,
                 known: Iterable[date]) -> list[date]:
    """Entry sessions of completed scanner runs that never recorded an evaluation."""
    seen = set(known)
    runs = session.scalars(select(ScannerRun.trading_date).where(
        ScannerRun.status == "COMPLETED").distinct()
        .order_by(ScannerRun.trading_date.desc()).limit(limit * 2))
    days: list[date] = []
    for analysis_day in runs:
        entry_day = calendar.next_trading_day(analysis_day)
        if entry_day in seen:
            continue
        seen.add(entry_day)
        days.append(entry_day)
    return days


def day_rows(session: Session, trading_date: date, *, calendar: MarketCalendar,
             config: StrategyConfig) -> tuple[str, list[dict[str, Any]]]:
    """One day's candidate rows, from the evaluation table or projected from legacy records."""
    stored = list(session.scalars(select(PaperEntryEvaluation).where(
        PaperEntryEvaluation.trading_date == trading_date)))
    if stored:
        trades = _trades_by_uid(session, [row.trade_uid for row in stored])
        rows = [evaluation_row(row, trades.get(row.trade_uid or "")) for row in stored]
        rows.sort(key=lambda row: (row["rank"] is None, row["rank"], row["symbol"]))
        return SOURCE_EVALUATION, rows
    candidates = load_approved_candidates(session, analysis_session_date(calendar, trading_date))
    if not candidates:
        return SOURCE_LEGACY_STATE, []
    context = load_candidate_context(session, trading_date, candidates, calendar)
    rows = [legacy_row(item, context.state(item), context.diagnostic(item),
                       context.trade(item), config) for item in candidates]
    return SOURCE_LEGACY_STATE, rows


def _trades_by_uid(session: Session, uids: Sequence[str | None]) -> dict[str, SimulationTradeRecord]:
    wanted = [uid for uid in uids if uid]
    if not wanted:
        return {}
    return {row.trade_uid: row for row in session.scalars(select(SimulationTradeRecord).where(
        SimulationTradeRecord.trade_uid.in_(wanted)))}


def evaluation_history(session: Session, *, calendar: MarketCalendar, limit: int = 30,
                       config: StrategyConfig | None = None) -> list[dict[str, Any]]:
    """Recent-first daily funnel summaries; a day with no trade is still a row."""
    settings = config or StrategyConfig()
    days = _evaluation_days(session, limit)
    days += _legacy_days(session, calendar, limit, days)
    summaries: list[dict[str, Any]] = []
    for day in sorted(set(days), reverse=True):
        source, rows = day_rows(session, day, calendar=calendar, config=settings)
        if not rows:
            # No approved candidate that day: there is no funnel to report.
            continue
        summaries.append(summarize(day, rows, source))
        if len(summaries) == limit:
            break
    return summaries


def evaluation_detail(session: Session, trading_date: date, *, calendar: MarketCalendar,
                      config: StrategyConfig | None = None) -> dict[str, Any]:
    settings = config or StrategyConfig()
    source, rows = day_rows(session, trading_date, calendar=calendar, config=settings)
    return {"trading_date": trading_date, "source": source,
            "summary": summarize(trading_date, rows, source), "candidates": rows}

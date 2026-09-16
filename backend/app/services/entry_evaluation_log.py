"""Durable evaluation history for the approved candidates the Entry runtime judges.

One APPROVE candidate of one entry session gets exactly one row, and that row holds
what the Production Entry runtime actually decided: the gate reason it named, the
observations it judged against, the thresholds in force at that moment, and the
settlement it reached. Nothing here replays the session, re-derives a decision, or
invents a reason a runtime never produced; a stage the candidate never reached stays
NULL, and a session that ended without evidence ends INCOMPLETE rather than guessed.

This module is analytics only. No trading, risk, sizing, or entry decision reads it,
and a failure to write one row never changes what the entry path does: every live
call site funnels through ``EntryEvaluationRecorder.record``, which swallows and logs
its own failures for that reason. A write lost that way is repaired by finalization,
which reads the same durable records the runtime already wrote.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import PaperEntryEvaluation
from app.models.simulation import SimulationTradeRecord
from app.models.strategy import PremarketDiagnosticRecord, StrategyStateRecord
from app.strategy.config import StrategyConfig
from app.strategy.engine import StrategyReason
from app.strategy.lifecycle import TERMINAL_PHASES, StrategyPhase

logger = logging.getLogger(__name__)


class EvaluationStatus(StrEnum):
    """The one outcome group a candidate's entry session ends in."""

    #: Still being evaluated, or evaluated but not yet resolved by finalization.
    IN_PROGRESS = "IN_PROGRESS"
    TRADED = "TRADED"
    PREMARKET_REJECTED = "PREMARKET_REJECTED"
    OR_REJECTED = "OR_REJECTED"
    NO_ENTRY_SIGNAL = "NO_ENTRY_SIGNAL"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    DATA_ERROR = "DATA_ERROR"
    #: Evaluated but never resolved: the process was down, or the only evidence is a skip.
    INCOMPLETE = "INCOMPLETE"
    #: A stored reason this contract does not recognise; it is shown, never interpreted.
    UNKNOWN = "UNKNOWN"


#: Once written, these are never replaced by a different outcome. IN_PROGRESS is the
#: only status a later tick, or finalization, is allowed to overwrite.
TERMINAL_STATUSES = frozenset(set(EvaluationStatus) - {EvaluationStatus.IN_PROGRESS})

#: A candidate that never reached a decision and left no evidence at all.
NOT_EVALUATED = "NOT_EVALUATED"
#: A finished session whose durable records predate this table and record no reason.
INCOMPLETE_LEGACY = "INCOMPLETE_LEGACY"
#: A session that closed while the candidate was still mid-evaluation: the runtime
#: stopped before reaching a decision, and it never named one to report.
EVALUATION_INCOMPLETE = "EVALUATION_INCOMPLETE"

PREMARKET_REJECT_REASONS = frozenset({
    StrategyReason.GAP_TOO_LOW.value, StrategyReason.GAP_TOO_HIGH.value,
    StrategyReason.LOW_PREMARKET_VOLUME.value, StrategyReason.INVALID_PREMARKET_DATA.value,
    StrategyReason.NEGATIVE_CATALYST.value, StrategyReason.RESEARCH_BLOCKED.value,
    StrategyReason.HUMAN_NOT_APPROVED.value,
})
OR_REJECT_REASONS = frozenset({StrategyReason.INSUFFICIENT_OPENING_RANGE.value})
#: The entry deadline passing with a complete opening range and no breakout is the
#: runtime's own way of saying it evaluated to the end and found no signal.
NO_SIGNAL_REASONS = frozenset({StrategyReason.ENTRY_DEADLINE_EXPIRED.value})
EXECUTION_REJECT_REASONS = frozenset({
    StrategyReason.ENTRY_PRICE_ABOVE_CEILING.value, StrategyReason.ENTRY_RISK_REJECTED.value,
    StrategyReason.ENTRY_EXECUTION_REJECTED.value, StrategyReason.ENTRY_SIGNAL_STALE.value,
    StrategyReason.ENTRY_SESSION_ENDED.value,
})
#: Provider and data-authority failures, plus the two premarket inputs whose absence is
#: a data fault rather than a strategy verdict.
DATA_ERROR_REASONS = frozenset({
    "AUTH_FAILED", "MARKET_DATA_UNAVAILABLE", "UNSUPPORTED_EXCHANGE",
    "EXCHANGE_AUTHORITY_MISSING", "RATE_LIMITED", "INVALID_SYMBOL",
    "NO_DAILY_HISTORY", "NO_EXACT_PREVIOUS_CLOSE",
    StrategyReason.INVALID_MARKET_DATA.value,
})
#: The premarket diagnostic fields that make INVALID_PREMARKET_DATA a data fault.
DATA_FAULT_INVALID_FIELDS = frozenset({"NO_DAILY_HISTORY", "NO_EXACT_PREVIOUS_CLOSE"})

#: Phases a candidate's session can no longer leave; a reason missing from one of these
#: was never recorded, unlike one the runtime had not reached yet.
TERMINAL_STATE_PHASES = frozenset(phase.value for phase in TERMINAL_PHASES)

#: Strategy phases that prove this session's entry actually filled.
FILLED_PHASES = frozenset({
    StrategyPhase.POSITION_OPEN.value, StrategyPhase.PYRAMID_ADDED.value,
    StrategyPhase.OVERNIGHT_REVIEW.value, StrategyPhase.OVERNIGHT_HELD.value,
    StrategyPhase.DAY2_ACTIVE.value, StrategyPhase.EXIT_SIGNALLED.value,
    StrategyPhase.EXITED.value,
})


def classify(reason: str | None, *, invalid_field: str | None = None) -> EvaluationStatus:
    """Group one raw runtime reason code; the code itself is always kept beside it.

    ``invalid_field`` is the premarket diagnostic's own cause. The gate names every
    unusable premarket input INVALID_PREMARKET_DATA, but a missing daily history or
    an unproven previous close is a data fault, not a strategy verdict, and the
    diagnostic is the only record that tells the two apart.
    """
    if reason is None:
        return EvaluationStatus.INCOMPLETE
    if reason == StrategyReason.INVALID_PREMARKET_DATA.value and invalid_field in DATA_FAULT_INVALID_FIELDS:
        return EvaluationStatus.DATA_ERROR
    if reason in DATA_ERROR_REASONS:
        return EvaluationStatus.DATA_ERROR
    if reason in PREMARKET_REJECT_REASONS:
        return EvaluationStatus.PREMARKET_REJECTED
    if reason in OR_REJECT_REASONS:
        return EvaluationStatus.OR_REJECTED
    if reason in NO_SIGNAL_REASONS:
        return EvaluationStatus.NO_ENTRY_SIGNAL
    if reason in EXECUTION_REJECT_REASONS:
        return EvaluationStatus.EXECUTION_REJECTED
    if reason == StrategyReason.ABOVE_VWAP_AND_OR_BREAK.value:
        return EvaluationStatus.TRADED
    return EvaluationStatus.UNKNOWN


def premarket_final_reason(gate_reason: str, invalid_field: str | None) -> tuple[str, str | None]:
    """The reason and detail a premarket outcome is stored with.

    A data fault is stored under the diagnostic's own field so the row says which
    input was missing; the gate's code is kept as the detail, never dropped.
    """
    if gate_reason == StrategyReason.INVALID_PREMARKET_DATA.value and invalid_field is not None:
        if invalid_field in DATA_FAULT_INVALID_FIELDS:
            return invalid_field, gate_reason
        return gate_reason, invalid_field
    return gate_reason, None


def thresholds(config: StrategyConfig) -> dict[str, Decimal]:
    return {"gap_min": config.premarket_gap_min_pct, "gap_max": config.premarket_gap_max_pct,
            "v1_volume_min": config.premarket_volume_ratio_min}


@dataclass(frozen=True)
class EvaluationKey:
    """The identity one evaluation row is written under."""

    trading_date: date
    analysis_trading_date: date
    scanner_run_id: int
    gpt_analysis_id: int
    scanner_candidate_id: int
    symbol: str
    exchange: str | None = None
    rank: int | None = None


def upsert(session: Session, key: EvaluationKey, *, now: datetime, strategy_version: str,
           status: EvaluationStatus | None = None, reason: str | None = None,
           detail: str | None = None, phase: str | None = None,
           progress_reason: str | None = None, error_code: str | None = None,
           finalized: bool = False, flags: dict[str, bool] | None = None,
           values: dict[str, Any] | None = None) -> PaperEntryEvaluation:
    """Merge one observation into the candidate's single row, monotonically.

    A terminal outcome is never replaced by a different one: a candidate rejected on
    gap cannot later read as traded, and a restart that re-evaluates the same session
    reuses the stored outcome instead of writing a second row. Observations only ever
    fill in a NULL; a value the runtime recorded once is never erased by a later tick
    that no longer has it.
    """
    row = session.scalar(select(PaperEntryEvaluation).where(
        PaperEntryEvaluation.trading_date == key.trading_date,
        PaperEntryEvaluation.scanner_candidate_id == key.scanner_candidate_id))
    if row is None:
        row = PaperEntryEvaluation(
            trading_date=key.trading_date, analysis_trading_date=key.analysis_trading_date,
            scanner_run_id=key.scanner_run_id, gpt_analysis_id=key.gpt_analysis_id,
            scanner_candidate_id=key.scanner_candidate_id, symbol=key.symbol,
            final_status=EvaluationStatus.IN_PROGRESS.value, strategy_version=strategy_version,
            # Set here, not left to the column default: the counter is read and
            # incremented in this same transaction, before any flush applies one.
            error_tick_count=0, first_evaluated_at=now, created_at=now, updated_at=now)
        session.add(row)
    if row.exchange is None and key.exchange:
        row.exchange = key.exchange
    if row.rank is None and key.rank is not None:
        row.rank = key.rank
    for name, value in (values or {}).items():
        if getattr(row, name) is None and value is not None:
            setattr(row, name, value)
    for name, value in (flags or {}).items():
        if value:
            setattr(row, name, True)
        elif getattr(row, name) is None:
            setattr(row, name, False)
    if phase is not None:
        row.last_phase = phase
    if progress_reason is not None and row.final_status == EvaluationStatus.IN_PROGRESS.value:
        # Progress is evidence for an outcome not yet reached. A tick after the outcome -
        # a later skip of an already-filled symbol - must not overwrite what led to it.
        row.last_progress_reason = progress_reason
    if error_code is not None:
        # A tick failure is evidence, never a verdict: a provider that recovers on the
        # next tick must not leave a terminal failure behind, so only the count and the
        # last code are kept and finalization decides what they mean.
        row.error_tick_count += 1
        row.last_error_code = error_code
        row.last_error_at = now
    if status is not None and row.final_status == EvaluationStatus.IN_PROGRESS.value:
        row.final_status = status.value
        if status in TERMINAL_STATUSES:
            row.final_reason = reason
            row.final_detail = detail
            row.finalized_at = now if finalized else row.finalized_at
            # An outcome settles whether the entry filled; unlike the gates a candidate
            # may never have reached, this one is never left unknown.
            if row.entry_filled is None:
                row.entry_filled = status is EvaluationStatus.TRADED
    row.evaluated_at = now
    row.updated_at = now
    return row


class EntryEvaluationRecorder:
    """The Entry runtime's write seam; it owns its own short transaction per call."""

    def __init__(self, session_factory, *, strategy_version: str,  # type: ignore[no-untyped-def]
                 config: StrategyConfig | None = None) -> None:
        self.session_factory = session_factory
        self.strategy_version = strategy_version
        self.config = config or StrategyConfig()

    def record(self, key: EvaluationKey, *, now: datetime, **changes: Any) -> None:
        """Write one observation; a failure here is logged and never raised.

        Evaluation history is observability. Letting its write fail an entry tick
        would let a reporting table change what the account trades, so the entry path
        is deliberately insulated from it and finalization repairs what was lost.
        """
        try:
            with self.session_factory() as session:
                try:
                    upsert(session, key, now=now, strategy_version=self.strategy_version, **changes)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        except Exception:
            logger.exception("ENTRY EVALUATION RECORD FAILED: %s %s", key.symbol, key.trading_date)

    def premarket(self, key: EvaluationKey, *, now: datetime, passed: bool, gate_reason: str,
                  gap_pct: Decimal | None, volume_ratio: Decimal | None,
                  previous_close: Decimal | None, reference_price: Decimal | None,
                  premarket_volume: Decimal | None, average_volume: Decimal | None,
                  invalid_field: str | None, phase: str) -> None:
        """The gate the runtime actually applied, with the thresholds then in force."""
        reason, detail = premarket_final_reason(gate_reason, invalid_field)
        limits = thresholds(self.config)
        self.record(
            key, now=now, phase=phase,
            status=None if passed else classify(reason, invalid_field=invalid_field),
            reason=None if passed else reason, detail=None if passed else detail,
            finalized=not passed, flags={"premarket_passed": passed},
            progress_reason=gate_reason if passed else None,
            values={"gap_pct": gap_pct, "v1_volume_ratio": volume_ratio,
                    "previous_close": previous_close, "premarket_reference_price": reference_price,
                    "premarket_volume": premarket_volume,
                    "historical_average_daily_volume": average_volume,
                    "gap_min": limits["gap_min"], "gap_max": limits["gap_max"],
                    "v1_volume_min": limits["v1_volume_min"]})


@dataclass(frozen=True)
class Resolution:
    """One candidate's outcome, concluded strictly from evidence already stored."""

    status: EvaluationStatus
    reason: str | None
    detail: str | None


def resolve(state: StrategyStateRecord | None, invalid_field: str | None, *,
            error_count: int | None = 0, last_error_code: str | None = None,
            progress_reason: str | None = None) -> Resolution:
    """Conclude one candidate's session from the durable records the runtime wrote.

    The order is evidence, never inference: a filled entry is proved by the phase its
    own state reached, a rejection by the reason that state recorded, a data fault by
    the error ticks counted during the session, and anything else is INCOMPLETE. No
    branch produces a strategy reason the runtime did not itself name; a terminal
    phase that recorded none stays INCOMPLETE_LEGACY rather than being explained.
    """
    if state is not None and state.phase in FILLED_PHASES:
        return Resolution(EvaluationStatus.TRADED,
                          StrategyReason.ABOVE_VWAP_AND_OR_BREAK.value, None)
    if state is not None and state.phase_reason is not None:
        reason, detail = (premarket_final_reason(state.phase_reason, invalid_field)
                          if state.phase == StrategyPhase.PREMARKET_REJECTED.value
                          else (state.phase_reason, None))
        return Resolution(classify(reason, invalid_field=invalid_field), reason, detail)
    if (error_count or 0) > 0:
        return Resolution(EvaluationStatus.DATA_ERROR, last_error_code, None)
    if state is not None and state.phase in TERMINAL_STATE_PHASES:
        # The session is known to have ended, and the reason it ended for was never
        # recorded. Inventing one is exactly what this contract refuses.
        return Resolution(EvaluationStatus.INCOMPLETE, INCOMPLETE_LEGACY, state.phase)
    if state is not None:
        # Still mid-evaluation at the close: the runtime stopped before deciding.
        return Resolution(EvaluationStatus.INCOMPLETE, EVALUATION_INCOMPLETE, state.phase)
    return Resolution(EvaluationStatus.INCOMPLETE, progress_reason or NOT_EVALUATED, None)


def finalize(session: Session, key: EvaluationKey, *, now: datetime, strategy_version: str,
             state: StrategyStateRecord | None, diagnostic: PremarketDiagnosticRecord | None,
             trade: SimulationTradeRecord | None) -> PaperEntryEvaluation:
    """Give one candidate its single final outcome, once its session has closed.

    An outcome already reached stands: this only resolves a row still IN_PROGRESS, so
    running it twice, or after a restart, reuses what is stored instead of rewriting it.
    """
    row = upsert(session, key, now=now, strategy_version=strategy_version)
    if row.final_status != EvaluationStatus.IN_PROGRESS.value:
        # Already terminal: only the trade identity, which the fill wrote after the
        # outcome, may still be filled in.
        if row.trade_uid is None and trade is not None:
            row.trade_uid = trade.trade_uid
        return row
    resolution = resolve(state, None if diagnostic is None else diagnostic.invalid_field,
                         error_count=row.error_tick_count, last_error_code=row.last_error_code,
                         progress_reason=row.last_progress_reason)
    # Whether the entry filled is fully decided once the session has closed, so this
    # flag is never left unknown, unlike the gates a candidate may never have reached.
    row.entry_filled = resolution.status is EvaluationStatus.TRADED
    row.final_status = resolution.status.value
    row.final_reason = resolution.reason
    row.final_detail = resolution.detail
    row.finalized_at = now
    if state is not None:
        row.last_phase = state.phase
    if trade is not None and row.trade_uid is None:
        row.trade_uid = trade.trade_uid
    row.updated_at = now
    return row


def session_states(session: Session, trading_date: date, symbols: Sequence[str],
                   ) -> dict[str, StrategyStateRecord]:
    if not symbols:
        return {}
    rows = session.scalars(select(StrategyStateRecord).where(
        StrategyStateRecord.trading_date == trading_date, StrategyStateRecord.book == "ACTUAL",
        StrategyStateRecord.variant == "ACTUAL", StrategyStateRecord.symbol.in_(list(symbols))))
    return {row.symbol: row for row in rows}


def session_diagnostics(session: Session, states: Sequence[StrategyStateRecord],
                        ) -> dict[int, PremarketDiagnosticRecord]:
    ids = [row.id for row in states]
    if not ids:
        return {}
    return {row.strategy_state_id: row for row in session.scalars(
        select(PremarketDiagnosticRecord).where(
            PremarketDiagnosticRecord.strategy_state_id.in_(ids)))}


def session_trades(session: Session, symbols: Sequence[str], start: datetime, end: datetime,
                   ) -> dict[str, SimulationTradeRecord]:
    """The trades opened inside one regular session, by symbol.

    A trade carries no trading date of its own, so its session is the one its entry
    fill happened in; the broker opens at most one trade per symbol per session.
    """
    if not symbols:
        return {}
    rows = session.scalars(select(SimulationTradeRecord).where(
        SimulationTradeRecord.symbol.in_(list(symbols))).order_by(
        SimulationTradeRecord.entry_time))
    return {row.symbol: row for row in rows if start <= row.entry_time <= end}


@dataclass(frozen=True)
class CandidateContext:
    """The durable records one entry session left for a set of approved candidates."""

    states: dict[str, StrategyStateRecord]
    diagnostics: dict[int, PremarketDiagnosticRecord]
    trades: dict[str, SimulationTradeRecord]

    def state(self, candidate: Any) -> StrategyStateRecord | None:
        """The state row this candidate owns; one another candidate owns is not its own."""
        row = self.states.get(candidate.symbol)
        if row is None or row.scanner_candidate_id != candidate.candidate_id:
            return None
        return row

    def diagnostic(self, candidate: Any) -> PremarketDiagnosticRecord | None:
        row = self.state(candidate)
        return None if row is None else self.diagnostics.get(row.id)

    def trade(self, candidate: Any) -> SimulationTradeRecord | None:
        return self.trades.get(candidate.symbol)


def load_candidate_context(session: Session, trading_date: date, candidates: Sequence[Any],
                           calendar: Any) -> CandidateContext:
    """Read every durable record one entry session holds for these candidates."""
    symbols = [item.symbol for item in candidates]
    states = session_states(session, trading_date, symbols)
    window = calendar.session(trading_date)
    trades = ({} if window is None else
              session_trades(session, symbols, window.market_open, window.market_close))
    return CandidateContext(states, session_diagnostics(session, list(states.values())), trades)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

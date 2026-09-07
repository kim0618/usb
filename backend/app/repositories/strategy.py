"""SQLAlchemy persistence adapter for StrategyState."""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import StrategyStateRecord
from app.strategy.lifecycle import StrategyBook, StrategyPhase, StrategyState

# Phases that still hold, or are still settling, a broker position.
OPEN_PHASES = tuple(phase.value for phase in (
    StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED, StrategyPhase.EXIT_SIGNALLED,
    StrategyPhase.OVERNIGHT_REVIEW, StrategyPhase.OVERNIGHT_HELD, StrategyPhase.DAY2_ACTIVE))


class StrategyStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, symbol: str, trading_date: date, *, book: StrategyBook = StrategyBook.ACTUAL,
             variant: str = "ACTUAL") -> StrategyState | None:
        book = StrategyBook(book)
        variant = "ACTUAL" if book is StrategyBook.ACTUAL else variant.upper()
        row = self.session.scalar(select(StrategyStateRecord).where(
            StrategyStateRecord.symbol == symbol.upper(), StrategyStateRecord.trading_date == trading_date,
            StrategyStateRecord.book == book.value, StrategyStateRecord.variant == variant))
        return None if row is None else self._domain(row)

    def list_open(self, symbol: str, *, book: StrategyBook = StrategyBook.ACTUAL,
                  variant: str = "ACTUAL") -> tuple[StrategyState, ...]:
        """Every unfinished state for a symbol, newest first.

        A position carries no trading date of its own, so the driver finds its
        state by symbol. More than one row coming back is a contradiction the
        caller must refuse rather than silently resolve.
        """
        book = StrategyBook(book)
        variant = "ACTUAL" if book is StrategyBook.ACTUAL else variant.upper()
        rows = self.session.scalars(select(StrategyStateRecord).where(
            StrategyStateRecord.symbol == symbol.upper(),
            StrategyStateRecord.book == book.value,
            StrategyStateRecord.variant == variant,
            StrategyStateRecord.phase.in_(OPEN_PHASES),
        ).order_by(StrategyStateRecord.trading_date.desc(), StrategyStateRecord.id.desc()))
        return tuple(self._domain(row) for row in rows)

    def save(self, state: StrategyState, *, updated_at: datetime) -> StrategyStateRecord:
        row = self.session.scalar(select(StrategyStateRecord).where(
            StrategyStateRecord.symbol == state.symbol,
            StrategyStateRecord.trading_date == state.trading_date,
            StrategyStateRecord.book == state.book.value,
            StrategyStateRecord.variant == state.variant))
        if row is None:
            row = StrategyStateRecord(symbol=state.symbol, trading_date=state.trading_date,
                                      book=state.book.value, variant=state.variant,
                                      created_at=updated_at)
            self.session.add(row)
        values = {
            "scanner_candidate_id": state.scanner_candidate_id, "phase": state.phase.value,
            "entry_trading_date": state.entry_trading_date, "entry_price": state.entry_price,
            "initial_stop": state.initial_stop, "active_stop": state.active_stop,
            "highest_price": state.highest_price_since_entry, "add_count": state.add_count,
            "add_signal_issued": state.add_signal_issued, "holding_day": state.holding_day_number,
            "overnight": state.overnight, "last_market_as_of": state.last_market_as_of,
            "strategy_version": state.strategy_version, "trailing_profile": state.trailing_profile.value,
            "overnight_suitability": state.overnight_suitability.value, "updated_at": updated_at,
        }
        for name, value in values.items():
            setattr(row, name, value)
        self.session.flush()
        return row

    @staticmethod
    def _domain(row: StrategyStateRecord) -> StrategyState:
        return StrategyState(symbol=row.symbol, trading_date=row.trading_date,
            book=row.book, variant=row.variant,
            scanner_candidate_id=row.scanner_candidate_id, phase=row.phase,
            entry_trading_date=row.entry_trading_date, entry_price=row.entry_price,
            initial_stop=row.initial_stop, active_stop=row.active_stop,
            highest_price_since_entry=row.highest_price, add_count=row.add_count,
            add_signal_issued=row.add_signal_issued, holding_day_number=row.holding_day,
            overnight=row.overnight, last_market_as_of=row.last_market_as_of,
            strategy_version=row.strategy_version, trailing_profile=row.trailing_profile,
            overnight_suitability=row.overnight_suitability)

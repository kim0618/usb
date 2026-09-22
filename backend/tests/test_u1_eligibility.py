"""U1 historical eligibility: the ranges it derives, and the verdicts it reads from a snapshot.

These tests never touch the workspace or the network: the snapshot is a stub holding only the
fields the checker reads. What they pin is that U1 asks the *same* strict question V2 asks, over
the tape of a frozen snapshot instead of the collector manifest, and that the parameterised rule
block still produces V2's bytes when nobody passes a date.
"""

from dataclasses import dataclass
from datetime import date

import pytest

from app.backtest.historical_store.a_view import (
    EXCLUDE_EMPTY, EXCLUDE_INCOMPLETE, EXCLUDE_OUTSIDE,
)
from app.backtest.research.universe_selection import (
    Candidate, DataStatus, RankedTicker, TickerDetails, rule_block, rule_block_v2,
)
from app.backtest.research.u1_eligibility import (
    SnapshotEligibilityChecker, missing_minute_symbols, u1_ranges,
)
from app.backtest.research.universe_selection import DataEligibilityUnresolved
from app.backtest.replay.errors import DatasetChecksumMismatch
from app.dev.run_massive_spike import MARKET_TIMEZONE
from app.market.calendar import MarketCalendar

CALENDAR = MarketCalendar(MARKET_TIMEZONE)
ENTRY_START, ENTRY_END = date(2024, 10, 23), date(2025, 9, 12)


@dataclass
class StubRow:
    session_date: date


class StubSnapshot:
    """Only the surface ``SnapshotEligibilityChecker`` reads."""

    snapshot_id = "SNAP-TEST"

    def __init__(self, audit_days, daily_days, *, excluded=None, daily_error=None):
        self.audit = {"AAA": {day: object() for day in audit_days}} if audit_days else {}
        self._daily = daily_days
        self._excluded = excluded or {}
        self._daily_error = daily_error

    def strict_sessions(self, symbol, sessions):
        kept = [day for day in sessions if day not in self._excluded]
        return kept, {day: reason for day, reason in self._excluded.items() if day in sessions}


def _checker(monkeypatch, snapshot, ranges):
    def load(snap, symbol):
        if snap._daily_error is not None:
            raise snap._daily_error
        rows = tuple(StubRow(day) for day in sorted(snap._daily))
        return rows, ({"path": "x"},) if rows else ()

    monkeypatch.setattr("app.backtest.historical_store.a_view.load_ticker_daily", load)
    return SnapshotEligibilityChecker(snapshot, ranges, calendar=CALENDAR)


def _candidate(symbol="AAA", exchange="XNAS", name="A Inc"):
    ranked = RankedTicker(liquidity_rank=1, symbol=symbol, name=name, primary_exchange=exchange,
                          reference_close=100.0, adv_usd=1e9, history_sessions=25)
    details = TickerDetails(ticker=symbol, as_of=date(2024, 10, 21), market_cap=2e10,
                            sic_code="3674", sic_description="SEMI", cik="0001", list_date=None,
                            primary_exchange=exchange, security_type="CS", active=True, name=name)
    return Candidate(ranked=ranked, details=details, sector=None, decision=None, pool_rank=1)


def _ranges():
    return u1_ranges(CALENDAR, entry_start=ENTRY_START, entry_end=ENTRY_END,
                     premarket_history_sessions=20, settlement_sessions=1)


def test_ranges_are_walked_off_the_calendar_not_typed_in():
    ranges = _ranges()
    # 20 sessions of premarket history before the first entry, one settlement after the last.
    assert ranges.minute_start == date(2024, 9, 25)
    assert ranges.minute_end == date(2025, 9, 15)
    # The scanner's 25-session warmup ends on the session before the first entry.
    assert ranges.daily_start == date(2024, 9, 17)
    assert ranges.daily_end == date(2025, 9, 11)


def _grid(ranges):
    from app.backtest.collector.range import sessions_between
    return [w.session_date for w in sessions_between(CALENDAR, ranges.minute_start, ranges.minute_end)]


def _daily_grid(ranges):
    from app.backtest.collector.range import sessions_between
    return [w.session_date for w in sessions_between(CALENDAR, ranges.daily_start, ranges.daily_end)]


def test_a_clean_snapshot_tape_is_complete(monkeypatch):
    ranges = _ranges()
    snapshot = StubSnapshot(_grid(ranges), _daily_grid(ranges))
    verdict = _checker(monkeypatch, snapshot, ranges)(_candidate())
    assert verdict.eligible
    assert verdict.metadata is verdict.daily is verdict.minute is DataStatus.COMPLETE
    assert verdict.evidence["minute"]["excluded_sessions"] == 0


@pytest.mark.parametrize("reason", [EXCLUDE_INCOMPLETE, EXCLUDE_EMPTY, EXCLUDE_OUTSIDE])
def test_one_excluded_session_fails_the_minute_step(monkeypatch, reason):
    ranges = _ranges()
    grid = _grid(ranges)
    snapshot = StubSnapshot(grid, _daily_grid(ranges), excluded={grid[7]: reason})
    verdict = _checker(monkeypatch, snapshot, ranges)(_candidate())
    assert not verdict.eligible
    assert verdict.minute is DataStatus.FAILED
    assert verdict.failure_reason.startswith("MINUTE:REGULAR_MINUTES_MISSING")
    assert reason in verdict.failure_reason
    assert verdict.evidence["minute"]["first_excluded"] == grid[7].isoformat()


def test_a_symbol_without_a_minute_tape_fails_rather_than_passing_silently(monkeypatch):
    ranges = _ranges()
    snapshot = StubSnapshot([], _daily_grid(ranges))
    verdict = _checker(monkeypatch, snapshot, ranges)(_candidate())
    assert not verdict.eligible
    assert verdict.failure_reason.startswith("MINUTE:NOT_IN_SNAPSHOT")


def test_a_short_daily_tape_fails_before_the_minute_step_is_asked(monkeypatch):
    ranges = _ranges()
    daily = _daily_grid(ranges)
    snapshot = StubSnapshot(_grid(ranges), daily[5:])
    verdict = _checker(monkeypatch, snapshot, ranges)(_candidate())
    assert verdict.daily is DataStatus.FAILED
    assert verdict.minute is DataStatus.NOT_CHECKED
    assert verdict.failure_reason.startswith("DAILY:MISSING_DATA")


def test_a_storage_fault_stops_the_walk_instead_of_excluding_the_symbol(monkeypatch):
    ranges = _ranges()
    snapshot = StubSnapshot(_grid(ranges), _daily_grid(ranges),
                            daily_error=DatasetChecksumMismatch("member sha256 differs"))
    with pytest.raises(DataEligibilityUnresolved):
        _checker(monkeypatch, snapshot, ranges)(_candidate())


def test_bad_metadata_fails_before_any_tape_is_read(monkeypatch):
    ranges = _ranges()
    snapshot = StubSnapshot(_grid(ranges), _daily_grid(ranges))
    verdict = _checker(monkeypatch, snapshot, ranges)(_candidate(exchange="XASE"))
    assert verdict.metadata is DataStatus.FAILED
    assert verdict.daily is verdict.minute is DataStatus.NOT_CHECKED


def test_missing_minute_symbols_keeps_the_given_order():
    snapshot = StubSnapshot([date(2024, 10, 23)], [])
    assert missing_minute_symbols(snapshot, ["ZZZ", "AAA", "BBB"]) == ("ZZZ", "BBB")


def test_rule_block_defaults_are_the_v2_dates_and_only_the_dates_move():
    assert rule_block()["reference_session"] == "2025-09-12"
    assert rule_block()["selection_as_of"] == "2025-09-15"
    historical = rule_block_v2(selection_as_of=date(2024, 10, 22),
                               reference_session=date(2024, 10, 21))
    current = rule_block_v2()
    assert historical["selection_as_of"] == "2024-10-22"
    assert historical["reference_session"] == "2024-10-21"
    moved = {key for key in current if current[key] != historical[key]}
    assert moved == {"selection_as_of", "reference_session"}

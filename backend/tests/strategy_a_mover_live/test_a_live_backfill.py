"""The baseline backfill: exact plan, resumption, the collection guard and the validation.

Section H's items 1-6, exercised without a single network call: the plan is computed from the
durable rows and the calendar, the representative run goes through ``execute`` with a stand-in
source, and ``validate`` answers how many symbols now have a denominator.
"""

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.services import premarket_volume_history as V
from app.strategy_a_mover_live import backfill as BF
from app.strategy_a_mover_live import baseline as B
from tests.strategy_a_mover_live.fixtures import ET, SESSION

CAL = MarketCalendar("America/New_York")
NOW = datetime(2026, 9, 15, 22, 0, tzinfo=ET)        # outside the collection guard
SYMBOLS = [("AAA", "ND"), ("BBB", "ND")]


@pytest.fixture
def database(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'backfill.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


class Source:
    """A premarket session source that answers from memory and counts what it was asked."""

    def __init__(self, *, fail_for: set[date] | None = None) -> None:
        self.asked: list[tuple[str, date]] = []
        self.fail_for = fail_for or set()

    def fetch_session(self, symbol: str, exchange: str, start: datetime, end: datetime):
        day = start.astimezone(ET).date()
        self.asked.append((symbol, day))
        if day in self.fail_for:
            from app.core.exceptions import MarketDataError
            raise MarketDataError("MARKET_DATA_UNAVAILABLE", "stand-in failure")
        bars = []
        for minute in (240, 400, 554, 600):
            stamp = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=ET)
            label = MarketSession.PREMARKET if minute < 570 else MarketSession.REGULAR
            bars.append(MinuteBar(symbol=symbol, timestamp=stamp, open=10.0, high=10.1,
                                  low=9.9, close=10.0, volume=1_000, session=label,
                                  observed_at=stamp + timedelta(minutes=1),
                                  available_at=stamp + timedelta(minutes=1)))
        return V.SessionFetch(tuple(bars), 1, True)


def service(database, source=None) -> B.AMoverPremarketVolumeService:
    return B.AMoverPremarketVolumeService(database, source, calendar=CAL,
                                          clock=lambda: NOW.astimezone(timezone.utc))


# -- the plan is exact (section H items 1, 2, 3) ---------------------------------------------

def test_the_plan_names_the_exact_sessions_and_symbols(database):
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    assert len(plan.symbols) == 2
    item = plan.symbols[0]
    assert len(item.required_sessions) == B.MAX_LOOKBACK_SESSIONS
    assert SESSION not in item.required_sessions
    assert len(item.expected_sessions) == B.BASELINE_SESSIONS
    assert item.worst_case_session_count == B.MAX_LOOKBACK_SESSIONS


def test_the_estimate_is_scaled_to_e_own_per_symbol_unit(database):
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    estimate = plan.estimate()
    assert estimate["pending_symbols"] == 2
    assert estimate["sessions_to_fetch"] == 2 * B.BASELINE_SESSIONS
    # 223 pages covered E's own 20 sessions, so two symbols of 20 sessions is 2 x 223
    assert estimate["estimated_pages"] == round(2 * 223.0)
    assert estimate["estimated_calls"] == estimate["estimated_pages"]
    assert estimate["lanes"] == 1
    assert estimate["worst_case_sessions_to_fetch"] == 2 * B.MAX_LOOKBACK_SESSIONS
    assert estimate["worst_case_hours_high"] > estimate["estimated_hours_high"]
    assert estimate["estimated_hours_low"] < estimate["estimated_hours_high"]


def test_every_estimate_input_names_its_source(database):
    inputs = BF.plan(database, SESSION, SYMBOLS, calendar=CAL).estimate()["inputs"]
    for name, body in inputs.items():
        assert body["source"], name


def test_the_plan_declares_that_the_rows_are_the_checkpoint(database):
    body = BF.plan(database, SESSION, SYMBOLS, calendar=CAL).declaration()
    assert "checkpoint" in body["resumption"]
    assert body["identity"] == "A_MOVER_PM_VOLUME_V1"
    assert body["baseline_rule"]["window_minutes"] == [240, 555]


# -- a representative run through the real code path (section H items 5, 6) -------------------

def test_a_representative_run_collects_only_the_symbols_it_is_given(database):
    source = Source()
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    run = BF.execute(service(database, source), plan, confirm_network=True,
                     now=lambda: NOW, limit_symbols=1)
    assert run.symbols_done == ["AAA"]
    assert {symbol for symbol, _ in source.asked} == {"AAA"}
    assert run.sessions_fetched == B.BASELINE_SESSIONS
    assert len(source.asked) == B.BASELINE_SESSIONS       # twenty, not the whole lookback


def test_the_representative_run_produces_a_usable_denominator(database):
    source = Source()
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    BF.execute(service(database, source), plan, confirm_network=True, now=lambda: NOW,
               limit_symbols=1)
    body = BF.validate(database, SESSION, SYMBOLS, calendar=CAL)
    assert body["baseline_available"] == 1
    assert body["baseline_short"] == 1
    assert body["baseline_short_examples"]["BBB"]["covered_sessions"] == 0
    assert body["rows"]["rows"] == B.BASELINE_SESSIONS
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.available and item.median_volume == 3_000    # 04:00, 06:40 and 09:14


# -- resumption is the durable rows (section H item 4) ---------------------------------------

def test_a_second_run_resumes_instead_of_repeating(database):
    source = Source()
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    BF.execute(service(database, source), plan, confirm_network=True, now=lambda: NOW,
               limit_symbols=1)
    first_calls = len(source.asked)
    again = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    assert [item.symbol for item in again.pending] == ["BBB"]
    assert again.sessions_to_fetch == B.BASELINE_SESSIONS   # only BBB is left
    BF.execute(service(database, source), again, confirm_network=True, now=lambda: NOW)
    assert len(source.asked) == first_calls + B.BASELINE_SESSIONS
    assert BF.plan(database, SESSION, SYMBOLS, calendar=CAL).pending == ()


def test_a_provider_failure_is_retried_by_the_next_plan(database):
    day = CAL.previous_trading_day(SESSION)
    source = Source(fail_for={day})
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    run = BF.execute(service(database, source), plan, confirm_network=True, now=lambda: NOW,
                     limit_symbols=1)
    assert "AAA" in run.symbols_failed
    assert day in BF.plan(database, SESSION, SYMBOLS, calendar=CAL).symbols[0].missing_sessions


def test_progress_is_written_but_is_not_the_authority(database, tmp_path):
    source = Source()
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    path = tmp_path / "progress.json"
    BF.execute(service(database, source), plan, confirm_network=True, now=lambda: NOW,
               limit_symbols=1, progress_path=path)
    body = BF.read_progress(path)
    assert body["symbols_done"] == 1
    assert body["collector_version"]
    path.unlink()                                   # losing the file loses nothing
    assert [item.symbol for item in
            BF.plan(database, SESSION, SYMBOLS, calendar=CAL).pending] == ["BBB"]


# -- the guard and the explicit confirmation -------------------------------------------------

def test_collection_refuses_without_an_explicit_network_confirmation(database):
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    with pytest.raises(BF.BackfillRefused):
        BF.execute(service(database, Source()), plan, confirm_network=False, now=lambda: NOW)


@pytest.mark.parametrize("moment,inside", [
    (time(3, 54), False), (time(3, 55), True), (time(9, 15), True), (time(9, 34), True),
    (time(9, 35), False), (time(16, 0), False), (time(22, 0), False)])
def test_the_guard_window_is_the_live_collectors_window(moment, inside):
    assert BF.in_guard_window(datetime.combine(SESSION, moment, tzinfo=ET)) is inside


def test_a_run_stops_when_it_reaches_the_guard_window(database):
    source = Source()
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    inside = datetime.combine(SESSION, time(5, 0), tzinfo=ET)
    run = BF.execute(service(database, source), plan, confirm_network=True,
                     now=lambda: inside)
    assert run.guard_stops == 1
    assert run.symbols_done == []
    assert source.asked == []                      # the lanes were never touched


def test_the_guard_is_checked_before_every_symbol_not_once(database):
    """The clock crosses into the guard mid-run, and the run stops there."""
    source = Source()
    plan = BF.plan(database, SESSION, SYMBOLS, calendar=CAL)
    inside = datetime.combine(SESSION, time(4, 0), tzinfo=ET)
    # execute() reads the clock once at the start, then once before each symbol: the run starts
    # outside the guard, clears AAA, and finds itself inside it before BBB.
    moments = iter([NOW, NOW] + [inside] * 40)
    run = BF.execute(service(database, source), plan, confirm_network=True,
                     now=lambda: next(moments))
    assert run.symbols_done == ["AAA"]
    assert run.guard_stops == 1
    assert {symbol for symbol, _ in source.asked} == {"AAA"}

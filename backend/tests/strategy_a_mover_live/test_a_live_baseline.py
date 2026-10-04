"""A_MOVER_PM_VOLUME_V1: the denominator's window, membership and arithmetic.

Section S tests covered here: 7 (the baseline excludes the current session), 8 (a covered
session with no print contributes zero) and 9 (the 20-session denominator is exact).
"""

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine
from app.market.calendar import MarketCalendar
from app.models.analytics import PremarketVolumeSession
from app.services import premarket_volume_history as V
from app.strategy_a_mover_live import baseline as B
from tests.strategy_a_mover_live.fixtures import ET, SESSION

CAL = MarketCalendar("America/New_York")
NOW = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'a_live.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def prior_sessions(count: int, before: date = SESSION) -> list[date]:
    out, day = [], before
    for _ in range(count):
        day = CAL.previous_trading_day(day)
        out.append(day)
    return list(reversed(out))


def row(session, day: date, volume: int | None, *, symbol: str = "AAA", exchange: str = "ND",
        quality: str = V.SessionQuality.COMPLETE.value,
        source: str = B.SOURCE, collector: str = B.COLLECTOR_VERSION) -> None:
    session.add(PremarketVolumeSession(
        symbol=symbol, exchange=exchange, trading_date=day, source=source,
        collector_version=collector, premarket_volume=volume, bar_count=1 if volume else 0,
        regular_bar_count=1, first_timestamp=None, last_timestamp=None, pages_used=1,
        target_reached=True, quality_status=quality, quality_reason=None, collected_at=NOW))
    session.flush()


# -- membership and the exclusion of the current session (section S test 7) -----------------

def test_the_entry_session_is_never_in_its_own_denominator(database):
    for day in prior_sessions(20):
        row(database, day, 100)
    row(database, SESSION, 999_999)          # the numerator's own session, stored by a later run
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.available
    assert SESSION not in item.used_sessions
    assert item.median_volume == 100
    assert max(item.used_sessions) == CAL.previous_trading_day(SESSION)


def test_the_lookback_never_reaches_the_entry_session(database):
    days = B.lookback_sessions(CAL, SESSION)
    assert SESSION not in days
    assert days[0] == CAL.previous_trading_day(SESSION)
    assert len(days) == B.MAX_LOOKBACK_SESSIONS


# -- a covered session with no print is a zero (section S test 8) ----------------------------

def test_a_covered_session_with_no_premarket_print_contributes_zero(database):
    days = prior_sessions(20)
    for day in days[:-1]:
        row(database, day, 100)
    row(database, days[-1], None, quality=V.SessionQuality.NO_PREMARKET_BARS.value)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.available
    assert len(item.used_sessions) == 20
    assert item.volumes[-1] == 0.0
    assert item.median_volume == 100           # 19 x 100 and one 0: the median is still 100


def test_an_all_silent_history_has_a_zero_median_and_the_floor_is_the_scanners(database):
    for day in prior_sessions(20):
        row(database, day, None, quality=V.SessionQuality.NO_PREMARKET_BARS.value)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.available and item.median_volume == 0.0
    # the 1,000-share floor belongs to the scanner's division, not to this module
    assert item.rule.floor_applied_here is False


def test_an_uncovered_session_is_skipped_and_an_older_one_takes_its_place(database):
    days = prior_sessions(21)
    row(database, days[-1], None, quality=V.SessionQuality.PROVIDER_FAILURE.value)
    for day in days[:-1]:
        row(database, day, 100)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.available
    assert len(item.used_sessions) == 20
    assert days[-1] not in item.used_sessions
    assert days[0] in item.used_sessions       # the 21st-back session filled the gap
    assert days[-1] in item.missing_sessions


# -- the denominator is exact (section S test 9) ---------------------------------------------

def test_nineteen_covered_sessions_are_not_a_denominator(database):
    for day in prior_sessions(19):
        row(database, day, 100)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert not item.available
    assert item.status is B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS
    assert item.median_volume is None
    assert len(item.used_sessions) == 19


def test_the_median_is_over_exactly_the_last_twenty_covered_sessions(database):
    days = prior_sessions(25)
    volumes = {day: (index + 1) * 10 for index, day in enumerate(days)}
    for day in days:
        row(database, day, volumes[day])
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.available
    assert item.used_sessions == tuple(days[-20:])
    expected = sorted(volumes[day] for day in days[-20:])
    assert item.median_volume == (expected[9] + expected[10]) / 2
    assert item.volumes == tuple(float(volumes[day]) for day in days[-20:])


def test_e_rows_are_never_read_as_a_rows(database):
    """The V2 rows use E's source and collector version, so A cannot pick them up."""
    for day in prior_sessions(20):
        row(database, day, 100, source=V.SOURCE, collector=V.COLLECTOR_VERSION)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert not item.available
    assert len(item.used_sessions) == 0


def test_a_rows_do_not_disturb_the_v2_baseline(database):
    for day in prior_sessions(20):
        row(database, day, 100)                       # A's rows only
    database.commit()
    v2 = V.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert v2.status is V.V2Status.INSUFFICIENT_PREMARKET_HISTORY


def test_the_rule_declares_its_window_and_quantity():
    body = B.BaselineRule().declaration()
    assert body["window_minutes"] == [240, 555]
    assert body["quantity"] == "SHARE_VOLUME"
    assert body["sessions"] == 20
    assert body["silent_covered_session"] == "CONTRIBUTES_ZERO"
    assert body["current_session_in_denominator"] is False
    assert body["identity"] == "A_MOVER_PM_VOLUME_V1"


# -- the window ends at the cut, not at the open ---------------------------------------------

class _Window:
    def __init__(self, day: date) -> None:
        self.market_open = datetime.combine(day, time(9, 30), tzinfo=ET)
        self.market_close = datetime.combine(day, time(16, 0), tzinfo=ET)


class _Fetch:
    def __init__(self, bars, target_reached: bool = True) -> None:
        self.bars = bars
        self.pages_used = 1
        self.target_reached = target_reached


def _bar(day: date, minute: int, volume: int):
    from app.market.domain import MarketSession, MinuteBar
    stamp = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=ET)
    label = (MarketSession.PREMARKET if minute < 570
             else MarketSession.REGULAR if minute < 960 else MarketSession.POSTMARKET)
    return MinuteBar(symbol="AAA", timestamp=stamp, open=10.0, high=10.1, low=9.9, close=10.0,
                     volume=volume, session=label, observed_at=stamp + timedelta(minutes=1),
                     available_at=stamp + timedelta(minutes=1))


def test_collection_sums_only_the_window_before_the_cut():
    day = CAL.previous_trading_day(SESSION)
    bars = [_bar(day, 240, 100), _bar(day, 554, 200), _bar(day, 560, 400),
            _bar(day, 600, 800)]
    record = B.assess_session_at_cut("AAA", "ND", day, _Window(day), _Fetch(bars),
                                     collected_at=NOW)
    assert record.quality_status is V.SessionQuality.COMPLETE
    assert record.premarket_volume == 300           # 09:20 and the regular bar are outside
    assert record.bar_count == 2
    assert record.regular_bar_count == 2
    assert record.source == B.SOURCE
    assert record.collector_version == B.COLLECTOR_VERSION


def test_a_truncated_history_is_not_covered():
    day = CAL.previous_trading_day(SESSION)
    record = B.assess_session_at_cut("AAA", "ND", day, _Window(day),
                                     _Fetch([_bar(day, 240, 100)], target_reached=False),
                                     collected_at=NOW)
    assert record.quality_status is V.SessionQuality.TARGET_NOT_REACHED
    assert B.usable_volume(_stub(record)) is None


def _stub(record):
    class Row:
        quality_status = record.quality_status.value
        premarket_volume = record.premarket_volume
    return Row()


def test_a_service_plan_window_is_a_lookback_not_exactly_twenty(database):
    service = B.AMoverPremarketVolumeService(database, None, calendar=CAL)
    planned = service.required_sessions(SESSION)
    assert len(planned) == B.MAX_LOOKBACK_SESSIONS
    assert planned[-1] == CAL.previous_trading_day(SESSION)
    assert service.source_name == B.SOURCE
    assert service.collector_version == B.COLLECTOR_VERSION


def test_rows_statistics_reports_what_exists(database):
    for day in prior_sessions(3):
        row(database, day, 100)
    database.commit()
    body = B.rows_statistics(database)
    assert body["rows"] == 3 and body["symbols"] == 1 and body["covered_rows"] == 3
    assert body["identity"] == "A_MOVER_PM_VOLUME_V1"

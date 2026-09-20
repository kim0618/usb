"""Small synthetic tapes for Strategy B tests. No provider data is copied here.

Every fixture is deterministic and built from explicit numbers, so an expected value in a
test can be recomputed by hand from this file.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.strategy_b.corporate_actions import DelistingNotice
from app.strategy_b.features import SessionTape
from app.strategy_b.models import (
    Availability, FeatureSnapshot, HaltStatus, Measured, MomentumBar, OfficialDailyBar, RvolStatus,
    Session, TapeDensity,
)
from app.strategy_b.rvol import VolumeProfile, build_volume_profile
from app.strategy_b.scope import (
    ListingStatus, PriorDailyBar, ScopeDecision, ScopeExclusion, ScopeInputs, TickerMetadataAsOf,
)
from app.strategy_b.session import ET, AggregationScope, SessionBoundaries
from app.strategy_b.split_adjustment import SplitRecord


D = date(2026, 3, 10)
SYMBOL = "BTEST"


def et(hour: int, minute: int, second: int = 0, microsecond: int = 0, day: date = D) -> datetime:
    return datetime.combine(day, time(hour, minute, second, microsecond), tzinfo=ET)


def boundaries(day: date = D) -> SessionBoundaries:
    return SessionBoundaries.standard(day)


def make_bar(hour: int, minute: int, close: float, volume: float, *, open_: float | None = None,
             high: float | None = None, low: float | None = None, vwap: float | None = None,
             transactions: float | None = 10.0, day: date = D) -> MomentumBar:
    open_ = close if open_ is None else open_
    high = max(open_, close) + 0.05 if high is None else high
    low = min(open_, close) - 0.05 if low is None else low
    stamp = et(hour, minute, day=day)
    return MomentumBar(
        timestamp=stamp, open=open_, high=high, low=low, close=close, volume=volume,
        session=boundaries(day).classify(stamp), vwap=close if vwap is None else vwap,
        transactions=transactions)


def make_tape(bars: list[MomentumBar], day: date = D, symbol: str = SYMBOL) -> SessionTape:
    return SessionTape(symbol, boundaries(day), bars)


def minutes(start: datetime, count: int) -> list[datetime]:
    return [start + timedelta(minutes=k) for k in range(count)]


# ---- tapes -----------------------------------------------------------------------------

def dense_regular_session(day: date = D) -> list[MomentumBar]:
    """Every minute 09:30-10:29. Price climbs 1 cent a minute; volume 1000 + 10k."""
    bars = []
    for k, stamp in enumerate(minutes(et(9, 30, day=day), 60)):
        close = round(10.0 + 0.01 * k, 2)
        bars.append(make_bar(stamp.hour, stamp.minute, close, 1000.0 + 10 * k,
                             open_=round(close - 0.01, 2), vwap=round(close - 0.005, 3), day=day))
    return bars


def sparse_low_liquidity_session() -> list[MomentumBar]:
    """Three regular-session trades: 09:31, 09:34, 09:39."""
    return [
        make_bar(9, 31, 10.00, 100.0, open_=9.90, high=10.10, low=9.80, vwap=9.90),
        make_bar(9, 34, 10.50, 200.0, open_=10.40, high=10.60, low=10.30, vwap=10.40),
        make_bar(9, 39, 11.00, 300.0, open_=10.90, high=11.20, low=10.80, vwap=10.90),
    ]


def premarket_sparse_session() -> list[MomentumBar]:
    """Scattered premarket prints with a premarket high above anything in the regular session."""
    return [
        make_bar(4, 15, 8.00, 50.0),
        make_bar(6, 2, 8.40, 80.0),
        make_bar(8, 30, 9.00, 400.0, high=12.00),
        make_bar(9, 10, 9.20, 150.0),
        make_bar(9, 25, 9.50, 500.0),
        make_bar(9, 30, 9.80, 2000.0, vwap=9.70),
        make_bar(9, 33, 10.10, 1500.0, vwap=10.00),
    ]


def halt_like_gap_session() -> list[MomentumBar]:
    """Dense 09:30-09:40 flat at 10.00, silence, then a +12% reopening print at 09:45."""
    bars = [make_bar(9, m, 10.00, 1000.0, open_=10.00) for m in range(30, 41)]
    bars.append(make_bar(9, 45, 11.30, 5000.0, open_=11.20, high=11.50, low=11.10))
    bars += [make_bar(9, m, 11.30, 1500.0, open_=11.30) for m in range(46, 50)]
    return bars


@dataclass(frozen=True)
class ReverseSplitDay:
    split: SplitRecord
    previous_close_date: date
    previous_raw_close: float
    bars: list[MomentumBar]
    daily: OfficialDailyBar


def reverse_split_day() -> ReverseSplitDay:
    """1-for-10 reverse split effective on D. D-1 closed at 0.50 raw; D trades around 5.20."""
    bars = [
        make_bar(9, 30, 5.20, 3000.0, open_=5.10, high=5.30, low=5.00),
        make_bar(9, 36, 5.25, 800.0, open_=5.20, high=5.40, low=5.15),
    ]
    return ReverseSplitDay(
        split=SplitRecord(execution_date=D, split_from=10, split_to=1),
        previous_close_date=D - timedelta(days=1),
        previous_raw_close=0.50,
        bars=bars,
        daily=OfficialDailyBar(D, open=5.10, high=5.40, low=5.00, close=5.18, volume=4000.0),
    )


def past_session_dates(count: int, before: date = D) -> list[date]:
    """``count`` distinct earlier dates. Calendar realism does not matter to RVOL maths."""
    return [before - timedelta(days=k) for k in range(count, 0, -1)]


def flat_volume_profile(day: date, per_minute: float, *, scope=AggregationScope.SESSION_LOCAL) -> VolumeProfile:
    """A past session trading ``per_minute`` shares in every minute 09:30-09:59."""
    bars = [make_bar(s.hour, s.minute, 10.0, per_minute, day=day) for s in minutes(et(9, 30, day=day), 30)]
    return build_volume_profile(make_tape(bars, day), scope)


def ipo_short_history() -> tuple[ScopeInputs, list[VolumeProfile]]:
    """Listed three sessions before D: three daily bars and three RVOL profiles."""
    days = past_session_dates(3)
    metadata = TickerMetadataAsOf(SYMBOL, days[-1], "CS", "XNAS", "stocks", ListingStatus.ACTIVE,
                                  list_date=days[0])
    history = tuple(PriorDailyBar(d, close=12.0, volume=500_000.0) for d in days)
    return ScopeInputs(D, metadata, history), [flat_volume_profile(d, 100.0) for d in days]


def delisting_window() -> DelistingNotice:
    """A delisting announced a week before D that takes effect three days after D."""
    return DelistingNotice(announced_on=D - timedelta(days=7), effective_date=D + timedelta(days=3))


def future_split_leak_case() -> tuple[SplitRecord, list[VolumeProfile]]:
    """A 2-for-1 split two days after D, and a full 20-session RVOL history before D."""
    split = SplitRecord(execution_date=D + timedelta(days=2), split_from=1, split_to=2)
    return split, [flat_volume_profile(d, 100.0) for d in past_session_dates(20)]


def scope_inputs(*, symbol: str = SYMBOL, security_type: str = "CS", exchange: str = "XNAS",
                 market: str = "stocks", status: ListingStatus = ListingStatus.ACTIVE,
                 close: float = 5.0, volume: float = 1_000_000.0, sessions: int = 20,
                 test_issue: bool | None = None) -> ScopeInputs:
    days = past_session_dates(sessions)
    metadata = TickerMetadataAsOf(symbol, D - timedelta(days=1), security_type, exchange, market, status,
                                  test_issue=test_issue)
    return ScopeInputs(D, metadata, tuple(PriorDailyBar(d, close, volume) for d in days))


# ---- scanner, eligibility and setup fixtures ---------------------------------------------

def passing_snapshot(**overrides) -> FeatureSnapshot:
    """A snapshot that passes the B-F0 gate comfortably; a test breaks exactly one thing.

    Hand-checkable: momentum max(3/2, 1/4, 1/6) = 1.5, rvol 5/3, liquidity 500k/250k = 2.
    """
    base = dict(
        symbol=SYMBOL, as_of=et(10, 0), session=Session.REGULAR,
        price=Measured.of(10.0), price_age_seconds=60.0,
        return_1m=Measured.of(3.0), return_3m=Measured.of(1.0), return_5m=Measured.of(1.0),
        session_vwap=Measured.of(9.9), vwap_distance_pct=Measured.of(1.01),
        hod=Measured.of(10.2), lod=Measured.of(9.5), hod_distance_pct=Measured.of(-1.96),
        rolling_dollar_volume=Measured.of(500_000.0),
        cumulative_dollar_volume=Measured.of(2_000_000.0),
        volume_acceleration=Measured.of(1.5),
        rvol=Measured.of(5.0), rvol_status=RvolStatus.FULL,
        missing_minute_ratio=Measured.of(0.1), sparse_status=TapeDensity.DENSE,
        halt_inferred=HaltStatus.NO_HALT_SIGNAL, split_adjusted=False,
        corporate_action_flags=frozenset(),
    )
    return FeatureSnapshot(**(base | overrides))


def in_scope(included: bool = True, *,
             exclusions: tuple[ScopeExclusion, ...] = ()) -> ScopeDecision:
    return ScopeDecision(SYMBOL, D, included, exclusions, 10.0, 5_000_000.0, 20)


def at(offset: int) -> tuple[int, int]:
    """Hour and minute ``offset`` minutes after 09:40, so a long window can cross the hour."""
    moment = et(9, 40) + timedelta(minutes=offset)
    return moment.hour, moment.minute


def breakout_bars(consolidation: int = 4, *, low: float = 10.75, day: date = D) -> list[MomentumBar]:
    """Climb to an 11.00 high at 09:45, then hold under it for ``consolidation`` actual bars.

    Trigger with the default config: 11.00 x 1.001 = 11.011; initial stop: the window low.
    """
    bars = [make_bar(*at(k), 10.0 + 0.2 * k, 1000.0, high=10.1 + 0.2 * k, low=9.9 + 0.2 * k,
                     day=day) for k in range(5)]
    bars.append(make_bar(9, 45, 10.90, 4000.0, open_=10.85, high=11.00, low=10.80, day=day))
    for k in range(consolidation):
        bars.append(make_bar(*at(6 + k), 10.85, 900.0, open_=10.86, high=10.95,
                             low=low if k == 0 else low + 0.03, day=day))
    return bars

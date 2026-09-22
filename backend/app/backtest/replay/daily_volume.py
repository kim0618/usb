"""Which daily volume the premarket gate divides by, named as a versioned basis.

``build_premarket_context`` measures premarket volume against the mean volume of the last
20 daily bars. Paper reads those daily bars from Kiwoom (``acc_trde_qty``, the whole
session's volume); the replay provider derives its daily bar from the REGULAR minute
bars of the tape it replays. The two are not the same number: the Massive minute tape
carries 3.6% to 20.2% less volume than the Massive daily aggregate, while the Massive daily
aggregate and Kiwoom ``acc_trde_qty`` are MATCHING_BASIS (``docs/MASSIVE_DAILY_BASIS.md``).

- ``V1_REGULAR_MINUTE_SUM`` is what every stored run used. It stays the default, so no stored
  identity or result moves.
- ``V2_DAILY_AGGREGATE`` reads the denominator from the provider's daily aggregate bar, the
  same economic quantity paper reads. Only the denominator changes: the gap, the previous
  close and the premarket numerator are still taken from the replayed minute tape.

``DailyAggregateVolumeProvider`` is the V2 source. It serves daily bars only, clamped to one
clock, and reports every bar it serves to the replay's point-in-time audit, so a daily bar
that had not closed at the gate tick is a violation exactly as it is for the minute path.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime

from app.backtest.replay.provider import PointInTimeAudit
from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.market.symbols import normalize_symbol

PREMARKET_VOLUME_BASIS_V1 = "V1_REGULAR_MINUTE_SUM"
PREMARKET_VOLUME_BASIS_V2 = "V2_DAILY_AGGREGATE"
PREMARKET_VOLUME_BASES = (PREMARKET_VOLUME_BASIS_V1, PREMARKET_VOLUME_BASIS_V2)


class DailyAggregateVolumeProvider(MarketDataProvider):
    """Daily aggregate bars for the premarket denominator, never a minute source."""

    def __init__(self, bars: Mapping[str, Sequence[DailyBar]], *,
                 clock: Callable[[], datetime], calendar: MarketCalendar,
                 audit: PointInTimeAudit) -> None:
        self._bars = {normalize_symbol(symbol): tuple(sorted(items, key=lambda bar: bar.trading_date))
                      for symbol, items in bars.items()}
        self._clock = clock
        self._calendar = calendar
        self._audit = audit

    def get_daily_bars(self, symbols: Sequence[str], start: date | None = None,
                       end: date | None = None) -> list[DailyBar]:
        as_of = self._clock()
        served: list[DailyBar] = []
        for name in {normalize_symbol(symbol) for symbol in symbols}:
            for bar in self._bars.get(name, ()):
                if start is not None and bar.trading_date < start:
                    continue
                if end is not None and bar.trading_date > end:
                    continue
                if bar.available_at > as_of:
                    continue  # not yet published at this moment: never served
                served.append(bar)
        served.sort(key=lambda bar: (bar.trading_date, bar.symbol))
        self._audit.observe_daily(served, as_of, self._calendar)
        return served

    def get_minute_bars(self, symbols: Sequence[str], start: datetime | None = None,
                        end: datetime | None = None,
                        session: MarketSession | None = None) -> list[MinuteBar]:
        raise NotImplementedError("the daily volume basis serves daily bars only")

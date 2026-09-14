"""Completed Kiwoom bars stay visible to a live as_of captured just before the fetch."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.market.calendar import MarketCalendar
from app.market.kiwoom import KiwoomMarketDataProvider
from app.services.entry_management_runtime import EntryLifecycleService

ET = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 9, 14, 9, 31, tzinfo=ET)


def minute(clock: str, day: str = "20260914") -> dict[str, str]:
    return {"bus_dt": day, "cntr_tm": f"{day}{clock}", "open_pric": "105", "high_pric": "105",
            "low_pric": "105", "cur_prc": "105", "trde_qty": "10"}


class Client:
    def __init__(self, rows: list[dict[str, str]],
                 regular_closes: dict[str, str] | None = None) -> None:
        self.rows = rows
        self.regular_closes = regular_closes or {"20260911": "100"}

    def minute_chart(self, symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        rows = list(self.rows)
        if start is not None:
            day = start.astimezone(ET).strftime("%Y%m%d")
            close = self.regular_closes.get(day)
            if close is not None:
                rows.append({
                    "bus_dt": day, "cntr_tm": f"{day}155900", "open_pric": close,
                    "high_pric": close, "low_pric": close, "cur_prc": close,
                    "trde_qty": "1000",
                })
        return SimpleNamespace(rows=rows)

    def daily_chart(self, symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        # Mirrors live usa06012: strt_dt is the base date and rows run backwards from it.
        self.daily_base = start
        days = ("20260911", "20260910", "20260909", "20260801")
        return [{"dt": dt, "open_pric": "100", "high_pric": "100", "low_pric": "100",
                 "cur_prc": "100", "acc_trde_qty": "1000000"}
                for dt in days if start is None or dt <= start]


@pytest.mark.parametrize("lag", [timedelta(0), timedelta(milliseconds=1), timedelta(seconds=2)])
def test_fetch_after_live_as_of_keeps_completed_bars_visible(lag: timedelta) -> None:
    provider = KiwoomMarketDataProvider(Client([minute("080000"), minute("092900")]),
                                        clock=lambda: AS_OF + lag)
    bars = provider.get_minute_bars(["AAA"], datetime(2026, 9, 14, 4, tzinfo=ET), AS_OF)
    visible = [bar for bar in bars if bar.available_at <= AS_OF]
    assert [bar.timestamp.strftime("%H:%M") for bar in visible] == ["08:00", "09:29"]

    built = EntryLifecycleService._premarket_context(
        SimpleNamespace(calendar=MarketCalendar()), "AAA", AS_OF.date(), visible, provider, AS_OF)
    assert built.diagnostic.invalid_field is None
    assert built.diagnostic.previous_close == 100 and built.diagnostic.reference_price == 105


def test_bar_completed_after_as_of_is_never_visible() -> None:
    as_of = datetime(2026, 9, 14, 9, 30, 30, tzinfo=ET)
    provider = KiwoomMarketDataProvider(Client([minute("092900"), minute("093000")]),
                                        clock=lambda: datetime(2026, 9, 14, 9, 31, 5, tzinfo=ET))
    bars = provider.get_minute_bars(["AAA"], datetime(2026, 9, 14, 4, tzinfo=ET), None)
    assert [bar.timestamp.strftime("%H:%M") for bar in bars] == ["09:29", "09:30"]
    assert [bar.timestamp.strftime("%H:%M") for bar in bars if bar.available_at <= as_of] == ["09:29"]


def test_incomplete_bar_at_receipt_is_still_excluded() -> None:
    provider = KiwoomMarketDataProvider(Client([minute("093000"), minute("093100")]),
                                        clock=lambda: datetime(2026, 9, 14, 9, 31, 30, tzinfo=ET))
    assert [bar.timestamp.strftime("%H:%M") for bar in provider.get_minute_bars(["AAA"])] == ["09:30"]


def test_daily_bar_is_available_at_its_close_not_at_receipt() -> None:
    provider = KiwoomMarketDataProvider(Client([]), clock=lambda: datetime(2026, 9, 13, 12, tzinfo=ET))
    bars = provider.get_daily_bars(["AAA"], date(2026, 9, 1), date(2026, 9, 11))
    assert [(bar.trading_date, bar.available_at) for bar in bars] == [
        (date(2026, 9, 9), datetime(2026, 9, 9, 16, tzinfo=ET)),
        (date(2026, 9, 10), datetime(2026, 9, 10, 16, tzinfo=ET)),
        (date(2026, 9, 11), datetime(2026, 9, 11, 16, tzinfo=ET)),
    ]


def test_daily_request_anchors_on_window_end_so_previous_session_is_present() -> None:
    client = Client([])
    provider = KiwoomMarketDataProvider(client, clock=lambda: datetime(2026, 9, 14, 9, 31, tzinfo=ET))
    bars = provider.get_daily_bars(["AAA"], date(2026, 9, 14) - timedelta(days=45), date(2026, 9, 13))
    assert client.daily_base == "20260913"
    assert [bar.trading_date for bar in bars][-1] == date(2026, 9, 11)


def priced(clock: str, price: str) -> dict[str, str]:
    row = minute(clock)
    row.update(open_pric=price, high_pric=price, low_pric=price, cur_prc=price)
    return row


@pytest.mark.parametrize(("received_at", "provider_returns"), [
    # Live: the 09:31 bar is still forming when the response arrives.
    (AS_OF + timedelta(milliseconds=500), ["09:29", "09:30"]),
    # Historical: the provider returns the 09:31 bar as well, already complete.
    (datetime(2026, 9, 14, 10, 0, tzinfo=ET), ["09:29", "09:30", "09:31"]),
])
def test_future_bar_is_never_consumed_at_0931(received_at: datetime, provider_returns: list[str]) -> None:
    from app.strategy.indicators import available_regular_bars

    provider = KiwoomMarketDataProvider(
        Client([priced("092900", "101"), priced("093000", "102"), priced("093100", "999")]),
        clock=lambda: received_at)
    bars = provider.get_minute_bars(["AAA"], datetime(2026, 9, 14, 4, tzinfo=ET), AS_OF)
    assert [bar.timestamp.strftime("%H:%M") for bar in bars] == provider_returns

    visible = [bar for bar in bars if bar.available_at <= AS_OF]  # EntryLifecycleService.evaluate
    assert [bar.timestamp.strftime("%H:%M") for bar in visible] == ["09:29", "09:30"]
    assert [bar.close for bar in available_regular_bars(bars, AS_OF)] == [102]

    built = EntryLifecycleService._premarket_context(
        SimpleNamespace(calendar=MarketCalendar()), "AAA", AS_OF.date(), visible, provider, AS_OF)
    assert built.diagnostic.reference_price == 101
    assert 999 not in {bar.close for bar in visible}


def daily_client(closes: dict[str, str]) -> Client:
    client = Client([minute("080000")], regular_closes=closes)

    def daily_chart(symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        return [{"dt": dt, "open_pric": close, "high_pric": close, "low_pric": close,
                 "cur_prc": close, "acc_trde_qty": "1000000"}
                for dt, close in sorted(closes.items(), reverse=True) if start is None or dt <= start]

    client.daily_chart = daily_chart  # type: ignore[method-assign]
    return client


@pytest.mark.parametrize(("entry", "closes", "previous_close", "invalid"), [
    # The minute fixture, not the daily close field, supplies the expected close.
    (date(2026, 9, 14), {"20260910": "110", "20260911": "111"}, "111", None),
    # After Labor Day (09/07): the predecessor is Friday 09/04.
    (date(2026, 9, 8), {"20260903": "103", "20260904": "104"}, "104", None),
    # Predecessor missing: no fallback to the older 09/10 bar.
    (date(2026, 9, 14), {"20260909": "109", "20260910": "110"}, None, "NO_EXACT_PREVIOUS_CLOSE"),
])
def test_previous_close_is_the_exact_xnys_predecessor_only(entry, closes, previous_close, invalid) -> None:
    as_of = datetime.combine(entry, datetime.min.time(), ET).replace(hour=9, minute=31)
    row = minute("080000", entry.strftime("%Y%m%d"))
    client = daily_client(closes)
    client.rows = [row]
    provider = KiwoomMarketDataProvider(client, clock=lambda: as_of + timedelta(seconds=1))
    visible = [bar for bar in provider.get_minute_bars(["AAA"], as_of.replace(hour=4, minute=0), as_of)
               if bar.available_at <= as_of]
    built = EntryLifecycleService._premarket_context(
        SimpleNamespace(calendar=MarketCalendar()), "AAA", entry, visible, provider, as_of)
    assert (None if built.diagnostic.previous_close is None else str(built.diagnostic.previous_close)) == (
        None if previous_close is None else str(float(previous_close)))
    assert (None if built.diagnostic.invalid_field is None else built.diagnostic.invalid_field.value) == invalid

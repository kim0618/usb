"""The LIVE position card: held position, its real history, and a close-now estimate.

Every figure asserted here comes from Binance's own record. The walk-back over `userTrades` is
the part worth testing hardest: it is the only thing that can say when the held position was
opened, and getting it wrong would put a confident, wrong duration on the screen.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.filters import SymbolFilters
from app.crypto.live.models import CommissionRate, IncomeRow, LivePosition, UserTrade
from app.crypto.live.position_card import (NET_BASIS, OPEN_FROM_TRADES, OPEN_UNKNOWN, card,
                                           close_now, funding_since, opening)

from tests.crypto.binance_fixtures import (COMMISSION_RATE, EXCHANGE_INFO, FakeBinance,
                                           POSITION_RISK_FLAT, POSITION_RISK_LONG, SYMBOL,
                                           make_client, make_config)

FILTERS = SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL, fetched_at_ms=1)
COMMISSION = CommissionRate.from_payload(COMMISSION_RATE)
DEEP = {"bids": [["83400.00", "500"]], "asks": [["83400.10", "500"]]}


def trade(tid: int, *, buyer: bool, qty: str, price: str, at: int, commission: str = "0.04",
          realized: str = "0") -> UserTrade:
    return UserTrade.from_payload({
        "id": tid, "orderId": tid * 10, "symbol": SYMBOL, "side": "BUY" if buyer else "SELL",
        "positionSide": "BOTH", "price": price, "qty": qty,
        "quoteQty": str(Decimal(price) * Decimal(qty)), "realizedPnl": realized,
        "commission": commission, "commissionAsset": "USDT", "maker": False, "buyer": buyer,
        "time": at})


def held(qty: str = "0.015", entry: str = "82666.66", mark: str = "83400.00") -> LivePosition:
    return LivePosition.from_rows([{
        "symbol": SYMBOL, "positionAmt": qty, "entryPrice": entry, "breakEvenPrice": "82700.00",
        "markPrice": mark, "unRealizedProfit": "11.00", "liquidationPrice": "75100.10",
        "isolatedMargin": "0", "notional": "1251.00", "positionSide": "BOTH",
        "initialMargin": "125.10", "maintMargin": "5.00", "adl": 2, "marginAsset": "USDT",
        "updateTime": 1_790_700_000_000}], SYMBOL)


FLAT = LivePosition.from_rows(POSITION_RISK_FLAT, SYMBOL)


# ------------------------------------------------------------------ the walk back


def test_the_open_time_is_the_fill_that_took_the_account_off_flat() -> None:
    # Flat, then 0.010 opened at t=100, then 0.005 added at t=200. The position has been held
    # since 100, not since the last change at 200 - which is what positionRisk.updateTime says.
    trades = [trade(1, buyer=True, qty="0.010", price="82600", at=100),
              trade(2, buyer=True, qty="0.005", price="82800", at=200)]
    out = opening(trades, held())
    assert out["opened_at_ms"] == 100
    assert out["source"] == OPEN_FROM_TRADES
    assert out["fills"] == 2


def test_an_earlier_closed_round_trip_is_not_counted_as_this_position() -> None:
    # A completed LONG round trip before the current one must not drag the open time backwards.
    trades = [trade(1, buyer=True, qty="0.002", price="80000", at=10),
              trade(2, buyer=False, qty="0.002", price="80500", at=20, realized="1.00"),
              trade(3, buyer=True, qty="0.015", price="82666.66", at=300)]
    out = opening(trades, held())
    assert out["opened_at_ms"] == 300
    assert out["fills"] == 1
    assert out["realized_since_open"] == Decimal("0")


def test_a_partial_close_keeps_the_original_open_time_and_carries_its_realised_pnl() -> None:
    trades = [trade(1, buyer=True, qty="0.020", price="82000", at=50, commission="0.08"),
              trade(2, buyer=False, qty="0.005", price="83000", at=150, commission="0.02",
                    realized="5.00")]
    out = opening(trades, held())
    assert out["opened_at_ms"] == 50
    assert out["realized_since_open"] == Decimal("5.00")
    assert out["commission_paid"] == Decimal("0.10")


def test_a_page_that_does_not_reach_the_open_says_unknown_rather_than_guessing() -> None:
    # Only a later addition is visible; the fill that opened the position is off the page.
    trades = [trade(9, buyer=True, qty="0.005", price="83000", at=900)]
    out = opening(trades, held())
    assert out["opened_at_ms"] is None
    assert out["source"] == OPEN_UNKNOWN


def test_a_flat_account_has_no_window() -> None:
    assert opening([trade(1, buyer=True, qty="0.01", price="1", at=1)], FLAT)["opened_at_ms"] is None


def test_a_short_position_walks_back_the_same_way() -> None:
    short = LivePosition.from_rows([{**POSITION_RISK_LONG[0], "positionAmt": "-0.015",
                                     "entryPrice": "83000", "liquidationPrice": "0"}], SYMBOL)
    trades = [trade(1, buyer=False, qty="0.015", price="83000", at=400)]
    assert opening(trades, short)["opened_at_ms"] == 400


# ------------------------------------------------------------------ funding


def test_funding_is_summed_with_binances_own_sign_and_only_inside_the_window() -> None:
    rows = [IncomeRow.from_payload({"symbol": SYMBOL, "incomeType": "FUNDING_FEE",
                                    "income": inc, "asset": "USDT", "time": at})
            for inc, at in (("-0.50", 50), ("-0.25", 150), ("0.10", 250))]
    # Held since 100: the -0.50 at t=50 belongs to an earlier position.
    assert funding_since(rows, 100) == Decimal("-0.15")
    assert funding_since(rows, None) == Decimal(0)


# ------------------------------------------------------------------ closing now


def test_closing_a_long_is_priced_on_the_bids_and_charged_the_taker_rate() -> None:
    out = close_now(position=held(), depth=DEEP, commission=COMMISSION, filters=FILTERS)
    assert out["feasible"] is True
    assert out["exit_fill_price"] == Decimal("83400.00")
    assert out["exit_fee"] == Decimal("83400.00") * Decimal("0.015") * COMMISSION.taker
    assert out["gross_pnl"] == (Decimal("83400.00") - Decimal("82666.66")) * Decimal("0.015")
    assert out["basis"] == NET_BASIS


def test_closing_a_short_is_priced_on_the_asks_and_gains_when_price_fell() -> None:
    short = LivePosition.from_rows([{**POSITION_RISK_LONG[0], "positionAmt": "-0.010",
                                     "entryPrice": "84000", "liquidationPrice": "0"}], SYMBOL)
    out = close_now(position=short, depth=DEEP, commission=COMMISSION, filters=FILTERS)
    assert out["exit_fill_price"] == Decimal("83400.10")
    assert out["gross_pnl"] == (Decimal("84000") - Decimal("83400.10")) * Decimal("0.010")


def test_a_book_too_thin_to_absorb_the_position_refuses_rather_than_estimating() -> None:
    thin = {"bids": [["83400.00", "0.001"]], "asks": [["83400.10", "500"]]}
    out = close_now(position=held(), depth=thin, commission=COMMISSION, filters=FILTERS)
    assert out["feasible"] is False
    assert out["reject_code"] == "NO_LIQUIDITY"


# ------------------------------------------------------------------ the card


def build(position: LivePosition = None, trades=None, funding=None, depth=None):
    return card(position=position or held(),
                trades=trades if trades is not None else
                    [trade(1, buyer=True, qty="0.015", price="82666.66", at=100,
                           commission="0.62")],
                funding_rows=funding if funding is not None else [],
                depth=depth or DEEP, commission=COMMISSION, filters=FILTERS,
                leverage=Decimal(10))


def test_a_flat_account_produces_no_card() -> None:
    assert card(position=FLAT, trades=[], funding_rows=[], depth=DEEP, commission=COMMISSION,
                filters=FILTERS, leverage=Decimal(10)) == {"open": False}


def test_the_card_carries_binances_own_position_figures() -> None:
    out = build()
    assert out["open"] is True and out["side"] == "LONG"
    assert out["qty"] == Decimal("0.015")
    assert out["leverage"] == Decimal(10)
    assert out["entry_price"] == Decimal("82666.66")
    assert out["mark_price"] == Decimal("83400.00")
    assert out["liquidation_price"] == Decimal("75100.10")
    assert out["unrealized_pnl"] == Decimal("11.00")


def test_a_zero_liquidation_price_is_reported_as_absent_not_as_a_price() -> None:
    no_liq = LivePosition.from_rows([{**POSITION_RISK_LONG[0], "liquidationPrice": "0"}], SYMBOL)
    assert build(position=no_liq)["liquidation_price"] is None


def test_the_net_is_the_positions_whole_life_measured_on_this_book() -> None:
    funding = [IncomeRow.from_payload({"symbol": SYMBOL, "incomeType": "FUNDING_FEE",
                                       "income": "-0.30", "asset": "USDT", "time": 200})]
    out = build(funding=funding)
    close = out["close"]
    expected = (Decimal("0") + close["gross_pnl"] - Decimal("0.62") - close["exit_fee"]
                + Decimal("-0.30"))
    assert out["net_if_closed"] == expected
    assert out["net_complete"] is True
    assert out["funding_income"] == Decimal("-0.30")


def test_funding_paid_makes_the_net_smaller_not_larger() -> None:
    # The sign trap: Binance reports paid funding as negative income. Adding it must reduce
    # the net; inverting it would turn every funding payment into a gain.
    paid = [IncomeRow.from_payload({"symbol": SYMBOL, "incomeType": "FUNDING_FEE",
                                    "income": "-1.00", "asset": "USDT", "time": 200})]
    assert build(funding=paid)["net_if_closed"] < build(funding=[])["net_if_closed"]


def test_an_unknown_open_time_leaves_the_net_incomplete_rather_than_wrong() -> None:
    out = build(trades=[trade(9, buyer=True, qty="0.005", price="83000", at=900)])
    assert out["opened_at_ms"] is None
    assert out["net_complete"] is False
    assert out["commission_paid"] is None


def test_a_book_that_cannot_absorb_the_close_reports_no_net() -> None:
    out = build(depth={"bids": [["83400.00", "0.001"]], "asks": [["83400.10", "500"]]})
    assert out["close"]["feasible"] is False
    assert out["net_if_closed"] is None
    assert out["net_complete"] is False


# ------------------------------------------------------------------ through the adapter


def adapter(fake: FakeBinance | None = None):
    client, fake = make_client(fake, trading_enabled=False)
    return BinanceLiveAdapter(config=make_config(trading_enabled=False), client=client,
                              mirror=None), fake


def test_the_adapter_reads_nothing_extra_while_the_account_is_flat() -> None:
    live, fake = adapter(FakeBinance(position_rows=POSITION_RISK_FLAT))
    before = fake.count("/fapi/v1/depth"), fake.count("/fapi/v1/userTrades")
    out = live.get_position_card()
    assert out["open"] is False
    assert (fake.count("/fapi/v1/depth"), fake.count("/fapi/v1/userTrades")) == before


def test_the_adapter_builds_the_card_for_a_held_position_and_sends_no_order() -> None:
    live, fake = adapter(FakeBinance(position_rows=POSITION_RISK_LONG))
    out = live.get_position_card()
    assert out["open"] is True and out["available"] is True
    assert out["side"] == "LONG"
    assert fake.count("/fapi/v1/order") == 0
    assert fake.count("/fapi/v1/leverage") == 0

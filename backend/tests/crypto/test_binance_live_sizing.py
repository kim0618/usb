"""Quick-size ladder for BINANCE LIVE: what the server says can actually be placed.

The rule this file exists to hold is that the screen never derives a size. Every quantity below
is produced by `live.sizing` from Binance's own figures, and every one of these tests asserts
about a limit the order router would also apply, so the ladder cannot offer what the router
would then refuse.

Nothing here sends an order. `FakeBinance` counts what was asked, and the last test checks the
count of the order path is zero across the whole ladder.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.filters import SymbolFilters
from app.crypto.live.models import CommissionRate, LivePosition, MarkPrice
from app.crypto.live.sizing import (DEFAULT_FRACTIONS, MAX_DEFINITION, check, floor_to_step,
                                    max_open, presets)

from tests.crypto.binance_fixtures import (COMMISSION_RATE, DEPTH, EXCHANGE_INFO, FakeBinance, MARK_PRICE,
                               POSITION_RISK_FLAT, POSITION_RISK_LONG, SYMBOL, make_client,
                               make_config)

FILTERS = SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL, fetched_at_ms=1)
MARK = MarkPrice.from_payload(MARK_PRICE)
COMMISSION = CommissionRate.from_payload(COMMISSION_RATE)
FLAT = LivePosition.from_rows(POSITION_RISK_FLAT, SYMBOL)

#: Deep enough that the book is never the binding constraint unless a test makes it one.
DEEP = {"bids": [["83499.90", "500"]], "asks": [["83500.10", "500"]]}


def ladder(**overrides):
    params = dict(side="LONG", depth=DEEP, mark=MARK, commission=COMMISSION, filters=FILTERS,
                  leverage=Decimal(10), available=Decimal("874.475"), ceiling=None,
                  position=FLAT)
    params.update(overrides)
    return presets(**params)


def row(result, label):
    return next(item for item in result["presets"] if item["label"] == label)


# ------------------------------------------------------------------ the search


def test_max_is_the_largest_size_that_passes_every_rule_not_a_balance_times_leverage() -> None:
    # Margin is the binding constraint here: 874.475 available at 10x against an ~83,500 book.
    # A naive available * leverage / price would say 0.104; the real answer is lower because the
    # entry fee is charged on top of the margin and the size is floored to the step.
    result = ladder()
    max_qty = result["max_qty"]
    assert result["max_feasible"] is True
    assert result["max_definition"] == MAX_DEFINITION
    naive = Decimal("874.475") * Decimal(10) / Decimal("83500.10")
    assert max_qty < naive
    # And it is genuinely maximal: one more step does not fit.
    step = FILTERS.market_qty_step
    assert check(side="LONG", qty=max_qty, depth=DEEP, mark=MARK, commission=COMMISSION,
                 filters=FILTERS, leverage=Decimal(10), available=Decimal("874.475"),
                 ceiling=None, position=FLAT)["feasible"] is True
    assert check(side="LONG", qty=max_qty + step, depth=DEEP, mark=MARK, commission=COMMISSION,
                 filters=FILTERS, leverage=Decimal(10), available=Decimal("874.475"),
                 ceiling=None, position=FLAT)["feasible"] is False


def test_the_fractions_are_taken_from_max_and_not_from_the_balance() -> None:
    result = ladder()
    max_qty = result["max_qty"]
    step = FILTERS.market_qty_step
    for label, fraction in (("25%", Decimal("0.25")), ("HALF", Decimal("0.50")),
                            ("75%", Decimal("0.75"))):
        assert row(result, label)["qty"] == floor_to_step(max_qty * fraction, step)
    assert row(result, "MAX")["qty"] == max_qty


def test_every_fraction_is_floored_to_the_step_never_rounded_up() -> None:
    # A ceiling of 0.01 makes MAX exactly 0.010, so the fractions land on 0.0025 and 0.0075 and
    # have to come back as 0.002 and 0.007. Rounding either up would offer a size above the
    # fraction the operator asked for.
    result = ladder(ceiling=Decimal("0.01"))
    assert result["max_qty"] == Decimal("0.010")
    assert row(result, "25%")["qty"] == Decimal("0.002")
    assert row(result, "HALF")["qty"] == Decimal("0.005")
    assert row(result, "75%")["qty"] == Decimal("0.007")
    assert row(result, "MAX")["qty"] == Decimal("0.010")


def test_floor_to_step_rounds_down_on_the_examples_the_policy_names() -> None:
    step = Decimal("0.001")
    assert floor_to_step(Decimal("0.0027"), step) == Decimal("0.002")
    assert floor_to_step(Decimal("0.0059"), step) == Decimal("0.005")
    assert floor_to_step(Decimal("0.010"), step) == Decimal("0.010")


# ------------------------------------------------------------------ the limits


def test_the_local_ceiling_binds_before_margin_does_and_says_which_limit_was_hit() -> None:
    result = ladder(ceiling=Decimal("0.005"))
    assert result["max_qty"] == Decimal("0.005")
    refused = check(side="LONG", qty=Decimal("0.006"), depth=DEEP, mark=MARK,
                    commission=COMMISSION, filters=FILTERS, leverage=Decimal(10),
                    available=Decimal("874.475"), ceiling=Decimal("0.005"), position=FLAT)
    assert refused["reject_code"] == "QTY_ABOVE_LOCAL_MAXIMUM"
    assert "BINANCE_LIVE_MAX_QTY" in refused["reject_message"]


def test_nothing_in_the_ladder_ever_exceeds_the_local_ceiling() -> None:
    ceiling = Decimal("0.01")
    result = ladder(ceiling=ceiling)
    assert all(item["qty"] <= ceiling for item in result["presets"])


def test_a_size_below_the_exchange_minimum_is_refused_rather_than_rounded_up() -> None:
    refused = check(side="LONG", qty=Decimal("0.0005"), depth=DEEP, mark=MARK,
                    commission=COMMISSION, filters=FILTERS, leverage=Decimal(10),
                    available=Decimal("874.475"), ceiling=None, position=FLAT)
    assert refused["feasible"] is False
    assert refused["reject_code"] == "QTY_BELOW_MINIMUM"


def test_a_ceiling_under_the_exchange_minimum_offers_nothing_and_says_why() -> None:
    # 0.0005 is below minQty, so not even the smallest order fits. The ladder reports the
    # exchange's reason rather than a zero with no explanation.
    result = ladder(ceiling=Decimal("0.0005"))
    assert result["max_feasible"] is False
    assert result["max_qty"] == Decimal(0)
    assert result["reject_code"] == "QTY_ABOVE_LOCAL_MAXIMUM"
    assert all(item["feasible"] is False for item in result["presets"])


def test_a_fraction_that_lands_under_the_floor_is_offered_as_refused_not_rounded_up() -> None:
    # MAX 0.004 makes 25% land on 0.001, which is on the grid but worth less than MIN_NOTIONAL.
    # The row comes back infeasible with the exchange's reason so the button can be disabled;
    # it is never rounded up to the smallest orderable size.
    result = ladder(ceiling=Decimal("0.004"))
    assert result["max_qty"] == Decimal("0.004")
    quarter = row(result, "25%")
    assert quarter["qty"] == Decimal("0.001")
    assert quarter["feasible"] is False
    assert quarter["reject_code"] == "NOTIONAL_BELOW_MINIMUM"
    assert row(result, "HALF")["feasible"] is True


def test_the_ladder_reports_the_smallest_size_that_could_be_ordered_at_all() -> None:
    # 100 USDT of minimum notional at an ~83,500 book is 0.002 once it is put on the grid.
    result = ladder()
    assert result["instrument"]["smallest_orderable_qty"] == Decimal("0.002")


def test_a_notional_under_the_exchange_minimum_is_refused() -> None:
    # MIN_NOTIONAL is 100 USDT in the fixture; 0.001 BTC at ~83,500 is 83.5.
    refused = check(side="LONG", qty=Decimal("0.001"), depth=DEEP, mark=MARK,
                    commission=COMMISSION, filters=FILTERS, leverage=Decimal(10),
                    available=Decimal("874.475"), ceiling=None, position=FLAT)
    assert refused["reject_code"] == "NOTIONAL_BELOW_MINIMUM"


def test_margin_is_measured_against_the_real_available_balance_with_the_fee_on_top() -> None:
    result = ladder(available=Decimal("200"))
    priced = row(result, "MAX")
    assert priced["required_total"] == priced["required_margin"] + priced["entry_fee"]
    assert priced["required_total"] <= Decimal("200")
    assert priced["available_after"] == Decimal("200") - priced["required_total"]


def test_a_balance_that_cannot_cover_the_minimum_refuses_the_whole_ladder() -> None:
    result = ladder(available=Decimal("1"))
    assert result["max_feasible"] is False
    assert result["reject_code"] == "INSUFFICIENT_MARGIN"


def test_a_size_the_book_cannot_fill_both_ways_is_refused() -> None:
    thin = {"bids": [["83499.90", "0.002"]], "asks": [["83500.10", "500"]]}
    # The entry would fill on the deep ask; the immediate exit could not. The size is not offered.
    refused = check(side="LONG", qty=Decimal("0.010"), depth=thin, mark=MARK,
                    commission=COMMISSION, filters=FILTERS, leverage=Decimal(10),
                    available=Decimal("874.475"), ceiling=None, position=FLAT)
    assert refused["feasible"] is False
    assert refused["reject_code"] == "NO_LIQUIDITY"


def test_an_open_position_on_the_other_side_offers_nothing_and_does_not_invent_a_reverse() -> None:
    held = LivePosition.from_rows(POSITION_RISK_LONG, SYMBOL)
    result = ladder(side="SHORT", position=held)
    assert result["max_feasible"] is False
    assert result["reject_code"] == "REVERSE_NOT_ALLOWED"
    assert all(item["qty"] == Decimal(0) for item in result["presets"])


def test_no_leverage_means_no_size_rather_than_a_guessed_margin() -> None:
    result = ladder(leverage=None)
    assert result["max_feasible"] is False
    assert result["reject_code"] == "MARGIN_UNKNOWN"


def test_an_unreadable_balance_means_no_size() -> None:
    result = ladder(available=None)
    assert result["max_feasible"] is False
    assert result["reject_code"] == "MARGIN_UNKNOWN"


# ------------------------------------------------------------------ through the adapter


def adapter(fake: FakeBinance | None = None, *, max_open_qty: str | None = None):
    client, fake = make_client(fake, trading_enabled=False)
    config = make_config(trading_enabled=False, max_open_qty=max_open_qty)
    return BinanceLiveAdapter(config=config, client=client, mirror=None), fake


def test_the_adapter_prices_both_sides_off_one_book_read() -> None:
    live, fake = adapter(FakeBinance(position_rows=POSITION_RISK_FLAT))
    before = fake.count("/fapi/v1/depth")
    result = live.get_sizing()
    assert result["available"] is True
    assert set(result["sides"]) == {"LONG", "SHORT"}
    assert fake.count("/fapi/v1/depth") == before + 1


def test_the_adapter_applies_the_deployments_own_ceiling() -> None:
    live, _ = adapter(FakeBinance(position_rows=POSITION_RISK_FLAT), max_open_qty="0.01")
    result = live.get_sizing()
    assert result["sides"]["LONG"]["max_qty"] == Decimal("0.010")
    assert result["sides"]["LONG"]["local_max_qty"] == Decimal("0.01")


def test_reading_the_ladder_sends_no_order_and_changes_no_leverage() -> None:
    # The whole point: this is a read. Nothing on the trade path may be touched by sizing.
    live, fake = adapter(FakeBinance(position_rows=POSITION_RISK_FLAT), max_open_qty="0.01")
    live.get_sizing()
    live.get_sizing()
    assert fake.count("/fapi/v1/order") == 0
    assert fake.count("/fapi/v1/leverage") == 0
    assert fake.count("/fapi/v1/listenKey") == 0


def test_an_account_that_cannot_be_read_returns_a_reason_rather_than_a_size() -> None:
    fake = FakeBinance(position_rows=POSITION_RISK_FLAT)
    fake.fail("GET", "/fapi/v3/account", 401, -2015, "Invalid API-key")
    live, _ = adapter(fake)
    result = live.get_sizing()
    assert result["available"] is False
    assert result["reject_code"]
    assert result["reject_message"]

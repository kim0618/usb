"""Adding to a position the account already holds, and what the screen is told it is holding.

Two questions, both answered on the server:

* **How much more can be opened?** Not the position's total size - the size that fits in what is
  left. `availableBalance` is already net of the margin the open position is holding, so the
  ladder's MAX against it is an add-on size by construction, and these tests pin that down
  rather than leaving it to be inferred from a formula nobody wrote.
* **How much is riding on the position?** `abs(positionRisk.notional)`, which is a different
  number from the margin by a factor of the leverage and is the one the operator asked for.

The reverse rule is unchanged and is asserted here too: the held side may be added to, the other
side is refused, and nothing in this package turns the second into a reduce or a flip.

Nothing here sends an order.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.live.filters import SymbolFilters
from app.crypto.live.models import CommissionRate, LivePosition, MarkPrice
from app.crypto.live.orders import OPEN, LiveOrderRouter, OrderRefused, REVERSE_NOT_ALLOWED
from app.crypto.live.account import AccountReader
from app.crypto.live.position_card import (EXPOSURE_FROM_NOTIONAL, EXPOSURE_FROM_QTY_AND_MARK,
                                           MARGIN_FROM_POSITION_RISK, card, exposure)
from app.crypto.live.sizing import INSUFFICIENT_MARGIN, presets

from tests.crypto.binance_fixtures import (COMMISSION_RATE, DEPTH, EXCHANGE_INFO,
                                           INCOME_FUNDING, POSITION_RISK_FLAT, SYMBOL,
                                           USER_TRADES, FakeBinance, make_client, make_config)
from app.crypto.live.models import IncomeRow, UserTrade

FILTERS = SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL, fetched_at_ms=1)
MARK = MarkPrice.from_payload({"symbol": SYMBOL, "markPrice": "83500.00000000",
                               "indexPrice": "83510.0", "estimatedSettlePrice": "83505.0",
                               "lastFundingRate": "0.0001", "interestRate": "0.0001",
                               "nextFundingTime": 1_790_640_000_000, "time": 1})
COMMISSION = CommissionRate.from_payload(COMMISSION_RATE)
FLAT = LivePosition.from_rows(POSITION_RISK_FLAT, SYMBOL)
DEEP = {"bids": [["83499.90", "500"]], "asks": [["83500.10", "500"]]}


def held(side: str, qty: str = "0.080", *, entry: str = "83938.05",
         notional: str | None = None) -> LivePosition:
    """A position row shaped like the real account's, signed by side."""
    signed = qty if side == "LONG" else f"-{qty}"
    size = Decimal(qty)
    value = notional if notional is not None else format(
        size * Decimal("83500") * (1 if side == "LONG" else -1), "f")
    return LivePosition.from_rows([{**POSITION_RISK_FLAT[0], "positionAmt": signed,
                                    "entryPrice": entry, "breakEvenPrice": entry,
                                    "markPrice": "83500.00000000",
                                    "unRealizedProfit": "-73.70091304",
                                    "liquidationPrice": "87840.33",
                                    "notional": value,
                                    "initialMargin": format(abs(Decimal(value)) / 20, "f"),
                                    "maintMargin": "27.13958617"}], SYMBOL)


def ladder(side: str, *, position: LivePosition, available: str):
    return presets(side=side, depth=DEEP, mark=MARK, commission=COMMISSION, filters=FILTERS,
                   leverage=Decimal(20), available=Decimal(available), ceiling=None,
                   position=position)


def row(result, label):
    return next(item for item in result["presets"] if item["label"] == label)


# ------------------------------------------------------------------ the ladder while holding


def test_the_held_side_is_still_sized_while_a_position_is_open() -> None:
    # The defect this guards: a held position used to leave the screen with no offerable size at
    # all, even on the side it was perfectly able to add to.
    result = ladder("SHORT", position=held("SHORT"), available="400")

    assert result["max_feasible"] is True
    assert result["max_qty"] > 0
    for label in ("25%", "HALF", "75%", "MAX"):
        assert row(result, label)["feasible"] is True


def test_max_is_what_fits_in_the_remaining_balance_not_the_resulting_total() -> None:
    """`availableBalance` is already net of the open position's margin, so the ladder's answer
    is the add-on. A MAX that meant the resulting total would be an order for coins the account
    already holds."""
    position = held("SHORT", "0.080")
    result = ladder("SHORT", position=position, available="400")

    # The add-on's own margin and fee are what was measured, so MAX is affordable out of the
    # remaining balance rather than out of the position's notional.
    assert row(result, "MAX")["required_total"] <= Decimal("400")

    # The property that makes it an add-on size: it depends on what is left, not on what is
    # held. The same remaining balance yields the same MAX whether the account is flat or
    # already holds 0.080 on this side - which is only true because nothing adds the held
    # quantity in, and is what would break if MAX ever meant the resulting total.
    flat_result = ladder("SHORT", position=FLAT, available="400")
    assert result["max_qty"] == flat_result["max_qty"]
    bigger = ladder("SHORT", position=held("SHORT", "0.300"), available="400")
    assert bigger["max_qty"] == result["max_qty"]

    # And a smaller remaining balance does move it, so the figure is reading the balance.
    assert ladder("SHORT", position=position, available="40")["max_qty"] < result["max_qty"]


def test_a_maxed_out_position_is_refused_for_margin_and_says_so() -> None:
    # The real account on 2026-10-02: SHORT 0.080 held, availableBalance 0, so no add-on fits.
    # The honest answer is a refusal with the shortfall, not a size.
    result = ladder("SHORT", position=held("SHORT"), available="0")

    assert result["max_feasible"] is False
    assert result["reject_code"] == INSUFFICIENT_MARGIN
    assert result["max_qty"] == 0


def test_the_opposite_side_is_refused_for_reversing_and_not_for_margin() -> None:
    """Both ladders are computed, and the two refusals must not be confused: one is cured by
    closing, the other by funding."""
    position = held("SHORT")
    assert ladder("LONG", position=position, available="400")["reject_code"] == REVERSE_NOT_ALLOWED
    assert ladder("SHORT", position=position, available="0")["reject_code"] == INSUFFICIENT_MARGIN


def test_a_flat_account_sizes_both_sides() -> None:
    for side in ("LONG", "SHORT"):
        assert ladder(side, position=FLAT, available="400")["max_feasible"] is True


# ------------------------------------------------------------------ the router


def plan_router(position_rows):
    config = make_config(trading_enabled=False)
    client, fake = make_client()
    fake.position_rows = position_rows
    reader = AccountReader(client, config)
    return LiveOrderRouter(reader=reader, client=client, config=config), fake


def test_the_router_accepts_an_add_on_to_the_side_already_held() -> None:
    rows = [{**POSITION_RISK_FLAT[0], "positionAmt": "-0.080", "entryPrice": "83938.05",
             "notional": "-6684.00", "initialMargin": "334.20"}]
    subject, _fake = plan_router(rows)

    plan = subject.plan(side="SHORT", intent=OPEN, qty="0.005")

    assert plan.qty == Decimal("0.005")       # the add-on, not 0.085
    assert plan.reduce_only is False
    assert plan.position_qty_at_plan == Decimal("0.080")


def test_the_router_refuses_the_opposite_side_rather_than_reversing() -> None:
    rows = [{**POSITION_RISK_FLAT[0], "positionAmt": "-0.080", "entryPrice": "83938.05",
             "notional": "-6684.00", "initialMargin": "334.20"}]
    subject, fake = plan_router(rows)

    with pytest.raises(OrderRefused) as caught:
        subject.plan(side="LONG", intent=OPEN, qty="0.005")

    assert caught.value.code == REVERSE_NOT_ALLOWED
    assert fake.count("/fapi/v1/order") == 0


# ------------------------------------------------------------------ 포지션 규모


def test_exposure_is_binances_own_notional_as_a_positive_figure() -> None:
    # A SHORT's notional is negative; "포지션 규모" is a size and the direction is the badge
    # beside it. Figures from the real account on 2026-10-02.
    position = held("SHORT", "0.080", notional="-6788.74491304")
    reported = exposure(position)

    assert reported["exposure"] == Decimal("6788.74491304")
    assert reported["exposure_basis"] == EXPOSURE_FROM_NOTIONAL


def test_a_long_and_a_short_of_the_same_size_report_the_same_exposure() -> None:
    long_side = exposure(held("LONG", "0.080"))["exposure"]
    short_side = exposure(held("SHORT", "0.080"))["exposure"]
    assert long_side == short_side > 0


def test_exposure_falls_back_to_qty_times_binance_mark_and_labels_it() -> None:
    """Only when Binance sends no `notional` on the row. The fallback uses two fields of the
    same response, and the label says which answer the screen is reading."""
    rows = [{k: v for k, v in held("SHORT").view().items()}]  # shape only; build a bare row
    bare = LivePosition.from_rows([{**POSITION_RISK_FLAT[0], "positionAmt": "-0.080",
                                    "entryPrice": "83938.05", "markPrice": "83500.00",
                                    "notional": None}], SYMBOL)
    reported = exposure(bare)

    assert reported["exposure"] == Decimal("0.080") * Decimal("83500.00")
    assert reported["exposure_basis"] == EXPOSURE_FROM_QTY_AND_MARK
    assert rows  # the view shape is stable; nothing above depends on its contents


def test_a_flat_position_has_no_exposure_to_report() -> None:
    assert exposure(FLAT) == {"exposure": None, "exposure_basis": None}


def test_exposure_and_margin_are_different_numbers_and_the_card_carries_both() -> None:
    """The distinction the screen has to keep: the margin is the operator's own money being
    held, the exposure is what the market sees. At 20x they are twenty times apart, and
    `initialMargin` is Binance's rather than `notional / leverage`, a division that ignores the
    maintenance tier."""
    position = held("SHORT", "0.080", notional="-6788.74491304")
    built = card(position=position, trades=[UserTrade.from_payload(USER_TRADES[0])],
                 funding_rows=[IncomeRow.from_payload(INCOME_FUNDING[0])], depth=DEEP,
                 commission=COMMISSION, filters=FILTERS, leverage=Decimal(20))

    assert built["exposure"] == Decimal("6788.74491304")
    assert built["initial_margin"] == position.initial_margin
    assert built["margin_basis"] == MARGIN_FROM_POSITION_RISK
    assert built["exposure"] > built["initial_margin"] * 19


def test_the_card_route_converts_the_exposure_with_the_same_krw_helper(live_card) -> None:
    """One rate for the whole screen. A second conversion path is how the same USDT ends up as
    two different KRW figures on two panels."""
    body = live_card
    rate = Decimal(str(body["krw_per_usdt"]))

    assert Decimal(body["krw"]["exposure"]) == Decimal(str(body["exposure"])) * rate
    assert Decimal(body["krw"]["net_if_closed"]) == Decimal(str(body["net_if_closed"])) * rate
    assert Decimal(body["krw"]["unrealized_pnl"]) == Decimal(str(body["unrealized_pnl"])) * rate


@pytest.fixture
def live_card(tmp_path: Path):
    """`GET /api/crypto/binance/position` over the real routes with a held position."""
    import os

    from fastapi.testclient import TestClient

    from app.crypto.live.adapter import BinanceLiveAdapter
    from app.crypto.live.mirror import LiveMirror, default_path
    from app.crypto.terminal.api import app
    from app.crypto.terminal.live_routes import live_runtime

    config = make_config(trading_enabled=False)
    client_obj, fake = make_client()
    fake.position_rows = [{**POSITION_RISK_FLAT[0], "positionAmt": "-0.080",
                           "entryPrice": "83938.05", "breakEvenPrice": "83938.05",
                           "markPrice": "83500.00000000", "unRealizedProfit": "35.04",
                           "liquidationPrice": "87840.33", "notional": "-6680.00",
                           "initialMargin": "334.00", "maintMargin": "27.13"}]
    reader = AccountReader(client_obj, config)
    mirror = LiveMirror(path=default_path(config.fingerprint, tmp_path),
                        account_fingerprint=config.fingerprint)
    router_obj = LiveOrderRouter(reader=reader, client=client_obj, config=config, mirror=mirror)
    adapter = BinanceLiveAdapter(config=config, client=client_obj, reader=reader,
                                 router=router_obj, mirror=mirror)
    previous = (live_runtime.adapter, live_runtime.config)
    live_runtime.adapter, live_runtime.config = adapter, config
    os.environ.setdefault("CRYPTO_LIVE_USER_STREAM", "off")
    try:
        with TestClient(app) as test_client:
            body = test_client.get("/api/crypto/binance/position").json()
            if body.get("krw_per_usdt") is None:
                pytest.skip("no paper run configured, so there is no display rate to borrow")
            yield body
    finally:
        live_runtime.adapter, live_runtime.config = previous

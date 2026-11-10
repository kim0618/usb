"""Pre-trade liquidation estimate for the LIVE ticket (`live.liquidation`).

The defining property is checked directly rather than against a remembered number: at the
estimated price the position's equity equals Binance's maintenance margin for the bracket that
notional sits in. Everything the formula cannot be fed is a refusal with a code, never a price.
"""
from __future__ import annotations

import copy
from decimal import Decimal as D

import pytest

from app.crypto.live import liquidation as liq
from app.crypto.live.brackets import LeverageBrackets
from app.crypto.live.models import AccountBalance, LivePosition
from tests.crypto.binance_fixtures import ACCOUNT, LEVERAGE_BRACKET, POSITION_RISK_LONG, SYMBOL, FakeBinance

BRACKETS = LeverageBrackets.from_payload(LEVERAGE_BRACKET, SYMBOL)
FLAT = LivePosition.flat(SYMBOL)
BALANCE = AccountBalance.from_account(ACCOUNT)
MARK = D("100000")


def equity_minus_maintenance(*, side, qty, entry, margin, price, brackets=BRACKETS):
    band = brackets.band(qty * price)
    sign = 1 if side == "LONG" else -1
    equity = margin + sign * qty * (price - entry)
    maintenance = qty * price * band.maint_margin_ratio - band.maint_amount
    return equity - maintenance


def test_cum_is_read_from_the_bracket_table() -> None:
    assert [band.maint_amount for band in BRACKETS.brackets] == [D("0.0"), D("300.0"), D("1500.0")]
    assert BRACKETS.brackets[1].view()["maint_amount"] == D("300.0")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("leverage", [D(1), D(5), D(20), D(125)])
def test_isolated_flat_estimate_is_where_equity_meets_maintenance(side, leverage) -> None:
    qty, fill = D("0.01"), D("100010")
    out = liq.estimate(side=side, qty=qty, fill_price=fill, leverage=leverage, margin_type="ISOLATED",
                       brackets=BRACKETS, position=FLAT, balance=BALANCE, mark_price=MARK)
    assert out["liquidation_refused"] is None
    price = out["liquidation_price"]
    assert out["liquidation_margin"] == qty * fill / leverage
    if price > 0:
        gap = equity_minus_maintenance(side=side, qty=qty, entry=fill, margin=qty * fill / leverage, price=price)
        assert abs(gap) < D("1e-12")
    # Direction: a LONG liquidates below the entry, a SHORT above it.
    assert (price < fill) if side == "LONG" else (price > fill)
    assert out["liquidation_distance_pct"] == (price - MARK) / MARK


def test_isolated_10x_long_matches_the_hand_solved_figure() -> None:
    out = liq.estimate(side="LONG", qty=D("0.01"), fill_price=D("100000"), leverage=D(10),
                       margin_type="ISOLATED", brackets=BRACKETS, position=FLAT, balance=BALANCE,
                       mark_price=MARK)
    # (1000 - 100 - 0) / (0.01 * 0.996)
    assert out["liquidation_price"].quantize(D("0.01")) == D("90361.45")


def test_higher_leverage_moves_the_liquidation_toward_the_price() -> None:
    def at(leverage):
        return liq.estimate(side="LONG", qty=D("0.01"), fill_price=MARK, leverage=leverage,
                            margin_type="ISOLATED", brackets=BRACKETS, position=FLAT,
                            balance=BALANCE, mark_price=MARK)["liquidation_price"]
    assert at(D(5)) < at(D(10)) < at(D(50)) < MARK


def test_cross_uses_the_cross_wallet_less_other_positions() -> None:
    qty, fill = D("0.01"), D("100000")
    out = liq.estimate(side="LONG", qty=qty, fill_price=fill, leverage=D(10), margin_type="CROSSED",
                       brackets=BRACKETS, position=FLAT, balance=BALANCE, mark_price=MARK)
    # Flat on this symbol: every maintenance and PnL figure on the account belongs to others.
    expected_margin = BALANCE.cross_wallet_balance - BALANCE.maint_margin + BALANCE.unrealized_pnl
    assert out["liquidation_margin"] == expected_margin
    assert abs(equity_minus_maintenance(side="LONG", qty=qty, entry=fill, margin=expected_margin,
                                        price=out["liquidation_price"])) < D("1e-12") \
        or out["liquidation_price"] == 0
    # A 1000 USDT cross wallet behind a 1000 USDT LONG cannot be liquidated above zero.
    assert out["liquidation_price"] == 0


def test_cross_short_adds_to_a_held_short_with_the_held_figures_removed_from_others() -> None:
    held = LivePosition.from_rows([{**POSITION_RISK_LONG[0], "positionAmt": "-0.015",
                                    "unRealizedProfit": "-2.0", "maintMargin": "5.0"}], SYMBOL)
    out = liq.estimate(side="SHORT", qty=D("0.005"), fill_price=D("83000"), leverage=D(20),
                       margin_type="CROSSED", brackets=BRACKETS, position=held, balance=BALANCE,
                       mark_price=D("83500"))
    assert out["liquidation_position_qty"] == D("0.020")
    expected_entry = (D("0.015") * held.entry_price + D("0.005") * D("83000")) / D("0.020")
    assert out["liquidation_avg_entry"] == expected_entry
    assert out["liquidation_margin"] == (BALANCE.cross_wallet_balance - (BALANCE.maint_margin - D("5.0"))
                                         + (BALANCE.unrealized_pnl - D("-2.0")))
    assert abs(equity_minus_maintenance(side="SHORT", qty=D("0.020"), entry=expected_entry,
                                        margin=out["liquidation_margin"],
                                        price=out["liquidation_price"])) < D("1e-12")


def test_isolated_add_on_counts_the_held_wallet_without_its_pnl() -> None:
    held = LivePosition.from_rows([{**POSITION_RISK_LONG[0], "isolatedMargin": "130.0",
                                    "unRealizedProfit": "12.5"}], SYMBOL)
    out = liq.estimate(side="LONG", qty=D("0.005"), fill_price=D("83600"), leverage=D(10),
                       margin_type="ISOLATED", brackets=BRACKETS, position=held, balance=BALANCE,
                       mark_price=D("83500"))
    assert out["liquidation_margin"] == D("130.0") - D("12.5") + D("0.005") * D("83600") / 10
    assert out["liquidation_position_qty"] == D("0.020")


def test_the_bracket_used_is_the_one_the_liquidation_notional_sits_in() -> None:
    # 4 BTC at 100000 = 400000 notional (bracket 2) at 10x isolated: liquidation near 90000
    # leaves 4 * 90k = 360k, still bracket 2. 3.2 BTC = 320k at entry, but ~289k at the
    # liquidation price, which is bracket 1: the answer must be solved with bracket 1.
    out = liq.estimate(side="LONG", qty=D("3.2"), fill_price=MARK, leverage=D(10), margin_type="ISOLATED",
                       brackets=BRACKETS, position=FLAT, balance=BALANCE, mark_price=MARK)
    assert out["liquidation_bracket"] == 1
    assert D("3.2") * out["liquidation_price"] <= D(300000)
    big = liq.estimate(side="LONG", qty=D("4"), fill_price=MARK, leverage=D(10), margin_type="ISOLATED",
                       brackets=BRACKETS, position=FLAT, balance=BALANCE, mark_price=MARK)
    assert big["liquidation_bracket"] == 2
    assert abs(equity_minus_maintenance(side="LONG", qty=D("4"), entry=MARK, margin=D("40000"),
                                        price=big["liquidation_price"])) < D("1e-9")


def test_refusals_carry_a_reason_and_no_price() -> None:
    base = dict(side="LONG", qty=D("0.01"), fill_price=MARK, leverage=D(10), margin_type="ISOLATED",
                brackets=BRACKETS, position=FLAT, balance=BALANCE, mark_price=MARK)
    no_cum = copy.deepcopy(LEVERAGE_BRACKET)
    for band in no_cum[0]["brackets"]:
        band.pop("cum")
    cases = {
        liq.NO_BRACKETS: {"brackets": None},
        liq.NO_MAINT_AMOUNT: {"brackets": LeverageBrackets.from_payload(no_cum, SYMBOL)},
        liq.MARGIN_TYPE_UNKNOWN: {"margin_type": None},
        liq.REVERSE: {"side": "SHORT", "position": LivePosition.from_rows(POSITION_RISK_LONG, SYMBOL)},
        liq.WALLET_UNKNOWN: {"margin_type": "CROSSED", "balance": None},
    }
    for code, change in cases.items():
        out = liq.estimate(**{**base, **change})
        assert out["liquidation_price"] is None and out["liquidation_refused"] == code, code


def test_the_live_preview_carries_the_estimate_and_sends_nothing() -> None:
    from tests.crypto.test_binance_live import adapter

    fake = FakeBinance(position_rows=[{**POSITION_RISK_LONG[0], "positionAmt": "0", "entryPrice": "0",
                                       "unRealizedProfit": "0", "liquidationPrice": "0",
                                       "notional": "0", "maintMargin": "0", "initialMargin": "0"}])
    live, fake = adapter(fake)
    preview = live.get_order_preview("LONG", qty="0.002")
    assert preview["feasible"] is True
    assert preview["liquidation_refused"] is None
    assert preview["liquidation_margin_type"] == "CROSSED"     # the fixture account's setting
    assert preview["liquidation_price"] is not None
    short = live.get_order_preview("SHORT", qty="0.002")
    assert short["liquidation_price"] > short["entry_fill_price"]
    assert all(method == "GET" for method, _, _ in fake.calls)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_the_paper_preview_liquidation_is_the_one_the_filled_position_shows(tiers, side) -> None:
    from app.crypto.paper import trade_preview
    from tests.crypto.conftest import make_config, quote, started_engine

    engine = started_engine(make_config(), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "50"]], asks=[["100000.1", "50"]], mark="100000.05"))
    preview = trade_preview.round_trip(engine, side, D("0.050"))
    assert preview["feasible"] and preview["liquidation_price"] is not None
    engine.submit_order(ts_ms=1_000, side=side, qty=D("0.050"), intent="OPEN", request_id="o")
    shown = engine.account.view(engine.tiers, engine.quote.mark_price)["liquidation_price"]
    assert preview["liquidation_price"] == shown
    mark = D("100000.05")
    assert preview["liquidation_distance_pct"] == (shown - mark) / mark

"""Safe MAX at the configured leverage, and only inside what that leverage allows.

Binance margins an order at the configured leverage and refuses a position past the largest
band cap that leverage reaches (-2027). On 2026-10-17 SIRENUSDT read 20x configured against a
10x first band: Binance refused 23,994 SIREN (about 735 USDT) at 20x and filled 36,014 at 10x.
The first version of this contract priced margin at min(configured, band) and offered sizes
Binance then refused. Now a setting above the symbol's maximum offers nothing and asks the
operator to choose again (`LEVERAGE_INVALID_FOR_SYMBOL`), and a position past what the setting
allows is refused (`LEVERAGE_TIER_EXCEEDED`) - on the ladder, the pre-send check and the
preview alike, for BTC and a HOT symbol. No test touches the network.
"""
from __future__ import annotations

import copy
from decimal import Decimal

import httpx
import pytest

from app.crypto import symbols as symbols_module
from app.crypto.live.brackets import (LEVERAGE_BRACKET_UNKNOWN, LEVERAGE_INVALID_FOR_SYMBOL,
                                      LEVERAGE_TIER_EXCEEDED, NOTIONAL_ABOVE_MAXIMUM,
                                      BracketsMalformed, LeverageBrackets, effective_leverage)
from app.crypto.live.models import LivePosition
from app.crypto.live.orders import CLOSE, OPEN
from app.crypto.live.preview import entry, round_trip
from app.crypto.live.sizing import INSUFFICIENT_MARGIN, check, max_open, presets
from tests.crypto.binance_fixtures import WIDE_BRACKETS, wide_brackets
from tests.crypto.test_binance_live_sizing import COMMISSION, DEEP, FILTERS, FLAT, MARK

D = Decimal


def bands(*rows, symbol="BTCUSDT"):
    """rows: (floor, cap, max_leverage)"""
    return LeverageBrackets.from_payload([{"symbol": symbol, "brackets": [
        {"bracket": i + 1, "initialLeverage": lev, "notionalFloor": floor, "notionalCap": cap,
         "maintMarginRatio": 0.01 * (i + 1), "cum": 0} for i, (floor, cap, lev) in enumerate(rows)]}], symbol)


def ladder(brackets, *, leverage=D(20), available=D(100), position=FLAT, side="LONG"):
    return presets(side=side, depth=DEEP, mark=MARK, commission=COMMISSION, filters=FILTERS,
                   leverage=leverage, brackets=brackets, available=available, ceiling=None,
                   position=position)


def notional_of(result):
    return result["max_qty"] * D("83500.10")


def max_row(result):
    return next(row for row in result["presets"] if row["label"] == "MAX")


#: One BTC step (0.001) is ~83.5 of notional, so a MAX lands within two steps of the exact edge
#: (the floor to the grid, plus the one-step reserve the solver keeps when margin binds).
TWO_STEPS = 2 * D("83.5")


# --------------------------------------------------------------------------- the table

def test_a_real_shaped_table_parses_and_answers_by_band() -> None:
    table = bands((0, 5000, 20), (5000, 25000, 10), (25000, 100000, 5))
    assert table.max_leverage == 20 and table.max_notional == 100000
    assert table.band(D(5000)).bracket == 1          # a boundary belongs to the lower band
    assert table.band(D("5000.01")).bracket == 2
    assert table.band(D(100001)) is None


@pytest.mark.parametrize("brackets, reason", [
    ([], "no brackets"),
    ([{"bracket": 1, "initialLeverage": 20, "notionalFloor": 0, "maintMarginRatio": 0.01, "cum": 0}], "notionalCap"),
    ([{"bracket": 1, "initialLeverage": 20, "notionalFloor": 10, "notionalCap": 100, "maintMarginRatio": 0.01}], "start at notional 0"),
    ([{"bracket": 1, "initialLeverage": 20, "notionalFloor": 0, "notionalCap": 100, "maintMarginRatio": 0.01},
      {"bracket": 2, "initialLeverage": 10, "notionalFloor": 150, "notionalCap": 900, "maintMarginRatio": 0.02}], "contiguous"),
    ([{"bracket": 1, "initialLeverage": 10, "notionalFloor": 0, "notionalCap": 100, "maintMarginRatio": 0.01},
      {"bracket": 2, "initialLeverage": 20, "notionalFloor": 100, "notionalCap": 900, "maintMarginRatio": 0.02}], "more leverage"),
    ([{"bracket": 1, "initialLeverage": "x", "notionalFloor": 0, "notionalCap": 100, "maintMarginRatio": 0.01}], "not a number"),
    ([{"bracket": 1, "initialLeverage": 0, "notionalFloor": 0, "notionalCap": 100, "maintMarginRatio": 0.01}], "valid band"),
])
def test_a_malformed_table_is_refused(brackets, reason) -> None:
    with pytest.raises(BracketsMalformed, match=reason):
        LeverageBrackets.from_payload([{"symbol": "GTCUSDT", "brackets": brackets}], "GTCUSDT")


def test_a_table_for_another_symbol_is_refused() -> None:
    with pytest.raises(BracketsMalformed):
        LeverageBrackets.from_payload([{"symbol": "BTCUSDT", "brackets": []}], "GTCUSDT")


@pytest.mark.parametrize("configured, allowed", [(10, 20), (20, 20), (5, 10)])
def test_margin_is_priced_at_the_configured_leverage(configured, allowed) -> None:
    decided = effective_leverage(D(configured), bands((0, 10000, allowed)), D(500))
    assert decided.effective == configured and decided.capped is False


def test_a_setting_above_the_symbols_maximum_is_refused_not_lowered() -> None:
    decided = effective_leverage(D(20), bands((0, 10000, 10), symbol="SIRENUSDT"), D(500))
    assert decided.code == LEVERAGE_INVALID_FOR_SYMBOL
    assert decided.message == "현재 심볼(SIRENUSDT)은 최대 10x입니다. 레버리지를 다시 선택하세요."


def test_max_notional_at_a_leverage_is_the_last_band_that_reaches_it() -> None:
    # The real SIRENUSDT table, first four bands.
    siren = bands((0, 10000, 10), (10000, 60000, 5), (60000, 70000, 4), (70000, 250000, 3),
                  symbol="SIRENUSDT")
    assert [siren.max_notional_at(D(lev)) for lev in (20, 10, 5, 4, 3, 2)] == [
        0, 10000, 60000, 70000, 250000, 250000]


def test_unknown_and_out_of_range_are_refusals_not_guesses() -> None:
    assert effective_leverage(D(20), None, D(500)).code == LEVERAGE_BRACKET_UNKNOWN
    assert effective_leverage(D(20), bands((0, 1000, 20)), D(1001)).code == NOTIONAL_ABOVE_MAXIMUM


# --------------------------------------------------------------------------- Safe MAX

def test_configured_below_the_band_leaves_safe_max_exactly_as_before() -> None:
    old = ladder(WIDE_BRACKETS, leverage=D(10))
    new = ladder(bands((0, 10 ** 9, 20)), leverage=D(10))
    assert new["max_qty"] == old["max_qty"] and new["leverage"] == 10
    assert new["leverage_capped_by_bracket"] is False


def test_configured_equal_to_the_band_leaves_safe_max_exactly_as_before() -> None:
    assert ladder(bands((0, 10 ** 9, 20)))["max_qty"] == ladder(WIDE_BRACKETS)["max_qty"]


def test_configured_above_the_symbols_maximum_offers_no_size_siren_case() -> None:
    table = bands((0, 10 ** 6, 10))                     # 20x configured, 10x symbol maximum
    invalid = ladder(table)
    assert invalid["max_qty"] == 0 and invalid["max_feasible"] is False
    assert invalid["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL
    assert all(row["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL and not row["feasible"]
               for row in invalid["presets"])
    # Not even the smallest order is offered: Binance would refuse it at 20x.
    row = check(side="LONG", qty=D("0.002"), depth=DEEP, mark=MARK, commission=COMMISSION,
                filters=FILTERS, leverage=D(20), brackets=table, available=D(100), ceiling=None,
                position=FLAT)
    assert row["feasible"] is False and row["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL


@pytest.mark.parametrize("leverage, ceiling", [(D(10), D(1000)), (D(5), D(500))])
def test_safe_max_follows_the_leverage_the_operator_chose(leverage, ceiling) -> None:
    # 100 USDT: about 995 of notional at 10x and 497 at 5x, once the fee rides on top.
    result = ladder(bands((0, 10 ** 6, 10)), leverage=leverage)
    assert result["leverage"] == leverage and result["leverage_capped_by_bracket"] is False
    assert ceiling - TWO_STEPS < notional_of(result) <= ceiling
    row = max_row(result)
    assert row["feasible"] and row["required_margin"] == row["notional"] / leverage


def test_a_band_boundary_the_lower_leverage_cannot_cross_stops_safe_max_at_the_boundary() -> None:
    # 20x up to 1,500, 10x beyond. 100 USDT at 10x cannot reach 1,500, so nothing above the
    # boundary is affordable and Safe MAX ends at (just under) the boundary at 20x.
    table = bands((0, 1500, 20), (1500, 10 ** 9, 10))
    result = ladder(table)
    assert notional_of(result) <= 1500
    assert notional_of(result) > 1500 - 2 * D("83.5")   # within the step reserve of the edge
    assert result["leverage"] == 20 and max_row(result)["bracket"] == 1


def test_a_band_the_setting_cannot_reach_stops_safe_max_at_the_tier_cap() -> None:
    # 20x up to 500, 10x beyond. At 20x the position may not pass 500, however much margin is
    # free; past it the size is refused as a tier, not repriced at 10x.
    table = bands((0, 500, 20), (500, 10 ** 9, 10))
    result = ladder(table)
    assert D(500) - TWO_STEPS < notional_of(result) <= D(500)
    assert result["leverage"] == 20 and max_row(result)["bracket"] == 1
    past = check(side="LONG", qty=D("0.007"), depth=DEEP, mark=MARK, commission=COMMISSION,
                 filters=FILTERS, leverage=D(20), brackets=table, available=D(100), ceiling=None,
                 position=FLAT)
    assert past["feasible"] is False and past["reject_code"] == LEVERAGE_TIER_EXCEEDED
    # At 10x the same table reaches ~995, in band 2.
    lower = ladder(table, leverage=D(10))
    assert D(1000) - TWO_STEPS < notional_of(lower) <= D(1000) and max_row(lower)["bracket"] == 2


def test_feasibility_is_monotone_across_a_boundary_so_the_search_cannot_oscillate() -> None:
    table = bands((0, 500, 20), (500, 2000, 10), (2000, 10 ** 9, 5))
    seen_infeasible = False
    for steps in range(2, 40):          # 0.001 is below this fixture's minimum notional
        qty = D("0.001") * steps
        row = check(side="LONG", qty=qty, depth=DEEP, mark=MARK, commission=COMMISSION,
                    filters=FILTERS, leverage=D(20), brackets=table, available=D(100),
                    ceiling=None, position=FLAT)
        if not row["feasible"]:
            seen_infeasible = True
        else:
            assert not seen_infeasible, f"feasible again at {qty} after an infeasible smaller size"
    assert seen_infeasible


def test_an_existing_position_counts_toward_the_band() -> None:
    # Holding 0.006 BTC (~501) on LONG already crosses the 500 boundary: an add is priced at 10x.
    held = LivePosition.from_rows([{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.006",
                                    "entryPrice": "83500", "breakEvenPrice": "83500", "markPrice": "83500",
                                    "unRealizedProfit": "0", "liquidationPrice": "0", "isolatedMargin": "0",
                                    "notional": "501", "initialMargin": "25", "maintMargin": "1",
                                    "adl": 0, "updateTime": 1}], "BTCUSDT")
    table = bands((0, 500, 20), (500, 10 ** 9, 10))
    at = lambda lev: check(side="LONG", qty=D("0.002"), depth=DEEP, mark=MARK, commission=COMMISSION,
                           filters=FILTERS, leverage=lev, brackets=table, available=D(100),
                           ceiling=None, position=held)
    # At 20x the resulting ~668 is past the 500 that 20x allows: refused, though the add alone
    # (~167) would fit.
    assert at(D(20))["feasible"] is False and at(D(20))["reject_code"] == LEVERAGE_TIER_EXCEEDED
    row = at(D(10))
    assert row["feasible"] and row["leverage"] == 10 and row["bracket"] == 2


@pytest.mark.parametrize("brackets, code", [
    (None, LEVERAGE_BRACKET_UNKNOWN),
    (bands((0, 50, 20)), NOTIONAL_ABOVE_MAXIMUM),        # even the 0.001 minimum (~83.5) is past it
])
def test_no_usable_table_means_no_safe_max(brackets, code) -> None:
    result = ladder(brackets)
    assert result["max_feasible"] is False and result["max_qty"] == 0
    assert result["presets"][0]["reject_code"] == code


def test_a_zero_balance_offers_nothing() -> None:
    result = ladder(bands((0, 10 ** 6, 10)), leverage=D(10), available=D(0))
    assert result["max_qty"] == 0 and result["presets"][0]["reject_code"] == INSUFFICIENT_MARGIN


@pytest.mark.parametrize("available", [D(100), D(500), D(1000)])
def test_fixture_balances_scale_safe_max_at_the_chosen_leverage(available) -> None:
    result = ladder(bands((0, 10 ** 7, 10)), leverage=D(10), available=available)
    row = max_row(result)
    assert row["leverage"] == 10
    assert row["required_total"] == row["required_margin"] + row["entry_fee"] <= available
    assert row["required_margin"] == row["notional"] / 10
    assert result["max_qty"] % FILTERS.market_qty_step == 0


def test_the_preview_prices_margin_at_the_same_leverage_and_refuses_the_same_setting() -> None:
    table = bands((0, 10 ** 6, 10))
    rt = round_trip(side="LONG", qty=D("0.010"), depth=DEEP, mark=MARK, commission=COMMISSION,
                    filters=FILTERS, leverage=D(10), brackets=table)
    en = entry(side="LONG", qty=D("0.010"), depth=DEEP, mark=MARK, commission=COMMISSION,
               filters=FILTERS, leverage=D(10), brackets=table)
    for priced in (rt, en):
        assert priced["leverage"] == 10 and priced["configured_leverage"] == 10
        assert priced["required_margin"] == priced["notional"] / 10
    for invalid in (round_trip(side="LONG", qty=D("0.010"), depth=DEEP, mark=MARK, commission=COMMISSION,
                               filters=FILTERS, leverage=D(20), brackets=table),
                    entry(side="LONG", qty=D("0.010"), depth=DEEP, mark=MARK, commission=COMMISSION,
                          filters=FILTERS, leverage=D(20), brackets=table)):
        assert invalid["feasible"] is False and invalid["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL
    assert rt["required_margin"] == en["required_margin"]
    assert round_trip(side="LONG", qty=D("0.010"), depth=DEEP, mark=MARK, commission=COMMISSION,
                      filters=FILTERS, leverage=D(20), brackets=None)["reject_code"] == LEVERAGE_BRACKET_UNKNOWN


def test_the_pure_functions_have_no_default_for_brackets() -> None:
    """Forgetting the table must be an error at the call, never a silent price at the setting."""
    with pytest.raises(TypeError):
        max_open(side="LONG", depth=DEEP, mark=MARK, commission=COMMISSION, filters=FILTERS,
                 leverage=D(20), available=D(100), ceiling=None, position=FLAT)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- the account path, a HOT symbol

HOT = "GTCUSDT"


def _hot_fake(monkeypatch, *, positions=None, extra_rows=None, brackets=None, leverage="20"):
    from tests.crypto import test_crypto_multisymbol as ms
    monkeypatch.setattr(symbols_module, "_dynamic", set())
    symbols_module.admit_dynamic(HOT)
    monkeypatch.setitem(ms.SPECS, HOT, {"base": "GTC", "tick": "0.000010", "step": "0.1", "min_qty": "0.1",
                                        "market_max": "600000", "notional": "5", "price": "0.195650",
                                        "qty_precision": 1, "price_precision": 6})
    table = brackets or [{"bracket": 1, "initialLeverage": 10, "notionalCap": 10000, "notionalFloor": 0,
                          "maintMarginRatio": 0.05, "cum": 0.0},
                         {"bracket": 2, "initialLeverage": 5, "notionalCap": 50000, "notionalFloor": 10000,
                          "maintMarginRatio": 0.1, "cum": 500.0}]

    class Fake(ms.MultiSymbolBinance):
        def transport(self):
            inner = super().transport()

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/fapi/v1/leverageBracket" and request.url.params.get("symbol") == HOT:
                    self.calls.append((request.method, request.url.path, dict(request.url.params)))
                    return httpx.Response(200, json=[{"symbol": HOT, "brackets": table}])
                return inner.handle_request(request)
            return httpx.MockTransport(handler)

    fake = Fake(positions=positions or {}, extra_rows=extra_rows or [], available="100.00000000",
                leverage={HOT: leverage, "BTCUSDT": "20"})
    client, config, fake = ms.make(fake, trading_enabled=True)
    return ms, client, config, fake


def test_hot_at_a_setting_above_its_maximum_offers_nothing_and_preflight_refuses(monkeypatch) -> None:
    ms, client, config, fake = _hot_fake(monkeypatch)              # 20x set, 10x table
    hot = ms.adapter_for(HOT, client, config)
    long = hot.get_sizing()["sides"]["LONG"]
    assert long["max_qty"] == 0 and long["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL
    pre = hot.preflight_open("LONG", qty="30.0")
    assert pre["feasible"] is False and pre["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL
    assert pre["safe_max_qty"] == 0
    assert not any(method != "GET" for method, _, _ in fake.calls), "no leverage write, no order"


def test_hot_sizing_preview_preflight_and_builder_share_one_leverage(monkeypatch) -> None:
    ms, client, config, fake = _hot_fake(monkeypatch, leverage="10")
    hot = ms.adapter_for(HOT, client, config)
    sizing = hot.get_sizing()
    long = sizing["sides"]["LONG"]
    assert long["leverage"] == 10 and long["configured_leverage"] == D(10) and not long["leverage_capped_by_bracket"]
    max_qty = long["max_qty"]
    assert max_qty > 0 and max_qty % D("0.1") == 0
    preview = hot.get_order_preview("LONG", qty=str(max_qty))
    pre = hot.preflight_open("LONG", qty=str(max_qty))
    assert preview["leverage"] == pre["leverage"] == 10
    assert pre["feasible"] is True and pre["required_margin"] == pre["notional"] / 10
    assert preview["required_margin"] == preview["notional"] / 10
    assert preview["breakeven_mark_price"] is not None
    # The builder: the exact wire payload, never sent.
    plan = hot.plan_order("LONG", OPEN, str(max_qty), symbol=HOT)
    params = plan.params(recv_window_ms=5000)
    assert (params["symbol"], params["side"], params["type"], params["quantity"]) == (HOT, "BUY", "MARKET", format(max_qty, "f"))
    assert "reduceOnly" not in params
    short = hot.plan_order("SHORT", OPEN, "30.0", symbol=HOT).params(recv_window_ms=5000)
    assert (short["side"], short["quantity"]) == ("SELL", "30.0")
    assert not any(path == "/fapi/v1/order" for _, path, _ in fake.calls)   # nothing was sent
    # BTC on the same account keeps its own filters, table and leverage.
    btc = ms.adapter_for("BTCUSDT", client, config).get_sizing()["sides"]["LONG"]
    assert btc["instrument"]["qty_step"] == D("0.001") and btc["leverage"] == D(20)


def test_hot_and_btc_close_payloads_touch_only_their_own_symbol(monkeypatch) -> None:
    ms, client, config, fake = _hot_fake(monkeypatch, positions={"BTCUSDT": ms_held("BTCUSDT", "LONG", "0.108")},
                                         extra_rows=[ms_held(HOT, "LONG", "100.0")])
    hot = ms.adapter_for(HOT, client, config)
    btc = ms.adapter_for("BTCUSDT", client, config)
    hot_close = hot.plan_order("", CLOSE, symbol=HOT).params(recv_window_ms=5000)
    assert (hot_close["symbol"], hot_close["side"], hot_close["quantity"], hot_close["reduceOnly"]) == (HOT, "SELL", "100.0", "true")
    btc_close = btc.plan_order("", CLOSE, symbol="BTCUSDT").params(recv_window_ms=5000)
    assert (btc_close["symbol"], btc_close["side"], btc_close["quantity"], btc_close["reduceOnly"]) == ("BTCUSDT", "SELL", "0.108", "true")


def test_a_short_hot_position_closes_with_a_buy(monkeypatch) -> None:
    ms, client, config, fake = _hot_fake(monkeypatch, extra_rows=[ms_held(HOT, "SHORT", "42.5")])
    close = ms.adapter_for(HOT, client, config).plan_order("", CLOSE, symbol=HOT).params(recv_window_ms=5000)
    assert (close["side"], close["quantity"], close["reduceOnly"]) == ("BUY", "42.5", "true")


def test_what_reaches_the_wire_is_exactly_the_planned_payload(monkeypatch) -> None:
    """Against the fake exchange only: the request the router sends carries the plan's params."""
    ms, client, config, fake = _hot_fake(monkeypatch)
    hot = ms.adapter_for(HOT, client, config)
    plan = hot.plan_order("LONG", OPEN, "30.0", symbol=HOT)
    expected = plan.params(recv_window_ms=config.recv_window_ms)
    hot.router.arm = None
    try:
        hot.router.submit(plan)
    except Exception:
        pass        # the arm gate may refuse; what matters is whether and what it sent
    sent = [params for method, path, params in fake.calls if (method, path) == ("POST", "/fapi/v1/order")]
    for params in sent:
        for key in ("symbol", "side", "type", "quantity"):
            assert params[key] == str(expected[key])


def test_a_missing_or_malformed_table_blocks_hot_sizing_but_not_the_account(monkeypatch) -> None:
    ms, client, config, fake = _hot_fake(monkeypatch, brackets=[{"bracket": 1, "initialLeverage": 0,
                                                                 "notionalCap": 10, "notionalFloor": 0,
                                                                 "maintMarginRatio": 0.01}])
    hot = ms.adapter_for(HOT, client, config)
    snap = hot.snapshot(force=True)
    assert snap.ready and snap.brackets is None and "BracketsMalformed" in snap.brackets_error
    long = hot.get_sizing()["sides"]["LONG"]
    assert long["max_qty"] == 0 and long["presets"][0]["reject_code"] == LEVERAGE_BRACKET_UNKNOWN
    pre = hot.preflight_open("LONG", qty="30.0")
    assert pre["feasible"] is False and pre["reject_code"] == LEVERAGE_BRACKET_UNKNOWN


def ms_held(symbol, side, qty):
    from tests.crypto import test_crypto_multisymbol as ms
    row = ms.held(symbol, side, qty)
    price = "0.195650" if symbol == HOT else row["entryPrice"]
    notional = str(D(qty) * D(price))
    return {**row, "entryPrice": price, "breakEvenPrice": price, "markPrice": price,
            "notional": notional if side == "LONG" else f"-{notional}"}

"""Paper engine behaviour. Every test states the rule it pins, not just the numbers."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.paper.account import Account, Position
from app.crypto.paper.book import NoLiquidity, walk_book
from app.crypto.paper.config import FeeSchedule, FxFixing, SlippageModel, build_config
from app.crypto.paper.engine import PaperEngine, OrderRejected
from app.crypto.paper.instrument import RiskTierTable
from app.crypto.paper.state import TransitionRejected
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


# ------------------------------------------------------------------ fills

def test_market_long_fills_on_the_ask_and_never_at_mid_or_bid(engine: PaperEngine) -> None:
    result = engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN",
                                 request_id="r1")
    # Contract O2/O4/T6: the fill price is the ask, strictly above both mid and bid.
    assert result["fill_price"] == D("100000.1")
    assert result["fill_price"] > engine.quote.mid > engine.quote.best_bid


def test_market_short_fills_on_the_bid_and_never_at_mid_or_ask(engine: PaperEngine) -> None:
    result = engine.submit_order(ts_ms=1_100, side="SHORT", qty=D("0.010"), intent="OPEN",
                                 request_id="r1")
    # Contract O3: selling hits the bid, strictly below both mid and ask.
    assert result["fill_price"] == D("100000.0")
    assert result["fill_price"] < engine.quote.mid < engine.quote.best_ask


def test_a_size_larger_than_the_best_level_walks_into_the_next_one(config, tiers) -> None:
    engine = started_engine(config, tiers, first_quote=quote(
        1_000, asks=[["100000.1", "0.004"], ["100000.5", "1"]], bids=[["100000.0", "10"]]))
    result = engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN",
                                 request_id="r1")
    # 0.004 @ 100000.1 then 0.006 @ 100000.5 (contract O5).
    expected = (D("100000.1") * D("0.004") + D("100000.5") * D("0.006")) / D("0.010")
    assert result["fill_price"] == expected
    assert result["levels_consumed"] == 2


def test_depth_that_cannot_cover_the_order_is_a_rejection_not_a_partial_fill(config, tiers) -> None:
    engine = started_engine(config, tiers, first_quote=quote(1_000, ask_qty="0.002"))
    with pytest.raises(NoLiquidity):
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="r1")
    assert engine.account.position.is_flat
    assert engine.ledger.events[-1]["code"] == "NO_LIQUIDITY"


def _round_trip(tiers, side: str, *, open_bid: str, open_ask: str, close_bid: str,
                close_ask: str) -> Decimal:
    engine = started_engine(make_config(taker="0"), tiers,
                            first_quote=quote(1_000, bid=open_bid, ask=open_ask))
    engine.submit_order(ts_ms=1_100, side=side, qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bid=close_bid, ask=close_ask))
    engine.submit_order(ts_ms=2_100, side=side, qty=D("0.010"), intent="CLOSE", request_id="c")
    return engine.account.realized_pnl


def test_long_and_short_are_exactly_symmetric_when_the_spread_is_removed(tiers) -> None:
    """Contract T4. With a zero spread the two sides are exact mirrors; the only thing that
    can break the mirror is a sign error, which is what this pins."""
    long_pnl = _round_trip(tiers, "LONG", open_bid="100000.0", open_ask="100000.0",
                           close_bid="100500.0", close_ask="100500.0")
    short_pnl = _round_trip(tiers, "SHORT", open_bid="100000.0", open_ask="100000.0",
                            close_bid="100500.0", close_ask="100500.0")
    assert long_pnl == -short_pnl
    assert long_pnl == D("100500.0") * D("0.010") - D("100000.0") * D("0.010")


def test_each_side_pays_the_spread_so_a_real_book_is_never_a_perfect_mirror(tiers) -> None:
    """The asymmetry is the spread cost, and it is adverse for both sides (contract O4)."""
    frictionless = _round_trip(tiers, "LONG", open_bid="100000.0", open_ask="100000.0",
                               close_bid="100500.0", close_ask="100500.0")
    long_pnl = _round_trip(tiers, "LONG", open_bid="100000.0", open_ask="100000.1",
                           close_bid="100500.0", close_ask="100500.1")
    short_pnl = _round_trip(tiers, "SHORT", open_bid="100000.0", open_ask="100000.1",
                            close_bid="100500.0", close_ask="100500.1")
    spread_cost = D("0.1") * D("0.010")
    assert long_pnl == frictionless - spread_cost
    assert short_pnl == -frictionless - spread_cost
    assert long_pnl != -short_pnl


# ------------------------------------------------------------------ close, reverse, sizing

def test_close_releases_margin_and_returns_the_position_to_flat(engine: PaperEngine) -> None:
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    assert engine.account.used_margin > 0
    engine.submit_order(ts_ms=1_200, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    assert engine.account.position.is_flat
    assert engine.account.used_margin == 0
    assert engine.ledger.events[-1]["event_type"] == "POSITION_CLOSE"


def test_closing_the_wrong_side_and_closing_nothing_are_both_refused(engine: PaperEngine) -> None:
    with pytest.raises(OrderRejected) as flat:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    assert flat.value.code == "NO_POSITION"
    engine.submit_order(ts_ms=1_200, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    with pytest.raises(OrderRejected) as mismatch:
        engine.submit_order(ts_ms=1_300, side="SHORT", qty=D("0.010"), intent="CLOSE", request_id="c2")
    assert mismatch.value.code == "CLOSE_SIDE_MISMATCH"


def test_reversing_through_zero_is_refused_in_both_directions(engine: PaperEngine) -> None:
    """D3 reverse policy is REJECT: flipping is two decisions, so it is two orders."""
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    with pytest.raises(OrderRejected) as opposite:
        engine.submit_order(ts_ms=1_200, side="SHORT", qty=D("0.010"), intent="OPEN", request_id="r")
    assert opposite.value.code == "REVERSE_NOT_ALLOWED"
    with pytest.raises(OrderRejected) as overclose:
        engine.submit_order(ts_ms=1_300, side="LONG", qty=D("0.020"), intent="CLOSE", request_id="r2")
    assert overclose.value.code == "REVERSE_NOT_ALLOWED"
    assert engine.account.position.signed_qty == D("0.010")


def test_partial_close_keeps_the_entry_average_and_realizes_only_the_closed_part(config, tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    entry = engine.account.position.avg_entry
    engine.apply_market(quote(2_000, bid="100500.0", ask="100500.1"))
    engine.submit_order(ts_ms=2_100, side="LONG", qty=D("0.004"), intent="CLOSE", request_id="p")
    assert engine.account.position.signed_qty == D("0.006")
    assert engine.account.position.avg_entry == entry
    assert engine.account.realized_pnl == (D("100500.0") - entry) * D("0.004")
    assert engine.ledger.events[-1]["event_type"] == "POSITION_REDUCE"


def test_increasing_a_position_moves_the_entry_average_by_weight(config, tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o1")
    engine.apply_market(quote(2_000, bid="101000.0", ask="101000.1"))
    engine.submit_order(ts_ms=2_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o2")
    expected = (D("100000.1") * D("0.010") + D("101000.1") * D("0.010")) / D("0.020")
    assert engine.account.position.avg_entry == expected
    assert engine.ledger.events[-1]["event_type"] == "POSITION_INCREASE"


def test_quantities_off_the_grid_below_the_minimum_or_above_the_market_cap_are_refused(engine) -> None:
    for qty, code in ((D("0.0005"), "QTY_BELOW_MINIMUM"), (D("0.0015"), "QTY_OFF_GRID"),
                      (D("151"), "QTY_ABOVE_MARKET_MAXIMUM")):
        with pytest.raises(OrderRejected) as rejected:
            engine.submit_order(ts_ms=1_100, side="LONG", qty=qty, intent="OPEN", request_id="r")
        assert rejected.value.code == code


def test_notional_below_the_exchange_minimum_is_refused(config, tiers) -> None:
    engine = started_engine(config, tiers, first_quote=quote(1_000, bid="400.0", ask="400.1"))
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.001"), intent="OPEN", request_id="r")
    assert rejected.value.code == "NOTIONAL_BELOW_MINIMUM"


# ------------------------------------------------------------------ fee and slippage

def test_fee_is_charged_once_from_cash_and_never_folded_into_the_fill_price(config, tiers) -> None:
    engine = started_engine(make_config(taker="0.0006"), tiers)
    result = engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN",
                                 request_id="r1")
    expected_fee = D("100000.1") * D("0.010") * D("0.0006")
    assert result["fee"] == expected_fee
    assert engine.account.cumulative_fees == expected_fee
    fee_events = engine.ledger.of_type("FEE")
    assert len(fee_events) == 1 and fee_events[0]["cost_class"] == "CASH_SEPARATE"
    # The fill price is the raw ask: the fee did not move it (contract C1/C3).
    assert engine.ledger.of_type("FILL")[0]["fill_price"] == "100000.1"


def test_gross_pnl_is_fill_based_so_a_price_embedded_cost_is_not_deducted_twice(config, tiers) -> None:
    """Contract T3. Spread and slippage live in the fill price; only the fee touches cash."""
    engine = started_engine(make_config(taker="0.0006", slippage_model="FIXED_BPS",
                                        slippage_bps="5"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bid="100500.0", ask="100500.1"))
    engine.submit_order(ts_ms=2_100, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    close_event = engine.ledger.of_type("POSITION_CLOSE")[0]
    entry = D(close_event["entry_avg"])
    exit_price = D(close_event["exit_price"])
    assert D(close_event["gross_pnl"]) == (exit_price - entry) * D("0.010")
    assert D(close_event["net_pnl"]) == D(close_event["gross_pnl"]) - D(close_event["fee"])
    assert engine.account.realized_pnl == D(close_event["gross_pnl"])


def test_slippage_moves_the_fill_adversely_on_both_sides(config, tiers) -> None:
    long_engine = started_engine(make_config(slippage_model="FIXED_BPS", slippage_bps="10"), tiers)
    long_fill = long_engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN",
                                         request_id="r")["fill_price"]
    short_engine = started_engine(make_config(slippage_model="FIXED_BPS", slippage_bps="10"), tiers)
    short_fill = short_engine.submit_order(ts_ms=1_100, side="SHORT", qty=D("0.010"), intent="OPEN",
                                           request_id="r")["fill_price"]
    assert long_fill == D("100000.1") * (D(1) + D("10") / D(10_000))
    assert short_fill == D("100000.0") * (D(1) - D("10") / D(10_000))
    assert long_fill > D("100000.1") and short_fill < D("100000.0")


def test_a_slippage_model_of_none_must_not_carry_a_rate_and_unknown_models_are_refused() -> None:
    with pytest.raises(ValueError):
        SlippageModel(model="NONE", bps=D("5"))
    with pytest.raises(ValueError):
        SlippageModel(model="BOOK_MAGIC", bps=D("0"))


def test_a_fee_schedule_without_provenance_cannot_be_built() -> None:
    for missing in ("version", "source", "effective_date"):
        fields = {"version": "v", "taker_rate": D("0.0006"), "maker_rate": D("0.0002"),
                  "source": "s", "effective_date": "2026-01-01"}
        fields[missing] = "   "
        with pytest.raises(ValueError):
            FeeSchedule(**fields)


def test_a_run_config_cannot_be_built_without_an_explicit_fx_fixing() -> None:
    with pytest.raises(ValueError):
        FxFixing(krw_per_usdt=D("1300"), source="", asof_utc="2026-09-23T00:00:00Z")
    with pytest.raises(TypeError):
        build_config(run_id="r", starting_capital_krw="1000000", fx_krw_per_usdt=1300.5,
                     fx_source="s", fx_asof_utc="t", fee_version="v", fee_taker_rate="0.0006",
                     fee_maker_rate="0.0002", fee_source="s", fee_effective_date="d",
                     slippage_model="NONE", slippage_bps="0", leverage="10",
                     risk_limit_source="s", risk_limit_sha256="h")


# ------------------------------------------------------------------ funding

def test_funding_is_paid_by_the_long_and_received_by_the_short_at_the_eight_hour_grid(config, tiers) -> None:
    settlement = 8 * 60 * 60 * 1000  # 1970-01-01T08:00:00Z, on the UTC 00/08/16 grid
    paid = {}
    for side in ("LONG", "SHORT"):
        engine = started_engine(make_config(taker="0"), tiers,
                                ts_ms=settlement - 2_000,
                                first_quote=quote(settlement - 2_000, funding_rate="0.0001"))
        engine.submit_order(ts_ms=settlement - 1_500, side=side, qty=D("0.010"), intent="OPEN",
                            request_id="o")
        engine.apply_market(quote(settlement + 1_000, funding_rate="0.0001",
                                  next_funding_time_ms=settlement + 8 * 3_600_000))
        events = engine.ledger.of_type("FUNDING")
        assert len(events) == 1 and events[0]["settlement_ts_ms"] == settlement
        assert events[0]["cost_class"] == "CASH_SEPARATE"
        paid[side] = engine.account.cumulative_funding_paid
    assert paid["LONG"] > 0 and paid["SHORT"] < 0 and paid["LONG"] == -paid["SHORT"]


def test_a_flat_account_accrues_no_funding(config, tiers) -> None:
    settlement = 8 * 60 * 60 * 1000
    engine = started_engine(config, tiers, ts_ms=settlement - 2_000,
                            first_quote=quote(settlement - 2_000, funding_rate="0.0001"))
    engine.apply_market(quote(settlement + 1_000, funding_rate="0.0001"))
    assert engine.ledger.of_type("FUNDING") == []
    assert engine.account.cumulative_funding_paid == 0


def test_several_missed_settlements_are_each_recorded_separately(config, tiers) -> None:
    step = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(taker="0"), tiers, ts_ms=step - 1_000,
                            first_quote=quote(step - 1_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=step - 500, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(3 * step + 1_000, funding_rate="0.0001"))
    events = engine.ledger.of_type("FUNDING")
    assert [event["settlement_ts_ms"] for event in events] == [step, 2 * step, 3 * step]


def test_a_provider_next_funding_time_off_the_grid_is_flagged_not_silently_trusted(config, tiers) -> None:
    settlement = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(taker="0"), tiers, ts_ms=settlement - 2_000,
                            first_quote=quote(settlement - 2_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=settlement - 1_500, side="LONG", qty=D("0.010"), intent="OPEN",
                        request_id="o")
    engine.apply_market(quote(settlement + 1_000, funding_rate="0.0001",
                              next_funding_time_ms=settlement + 12_345))
    assert engine.funding_grid_mismatches == 1
    assert engine.ledger.of_type("FUNDING")[0]["provider_grid_mismatch"] is True


# ------------------------------------------------------------------ leverage and margin

def test_leverage_sets_the_margin_and_only_changes_while_flat(engine: PaperEngine) -> None:
    engine.set_leverage(ts_ms=1_050, leverage=D("20"))
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    assert engine.account.used_margin == engine.account.position.entry_notional / D("20")
    with pytest.raises(OrderRejected) as locked:
        engine.set_leverage(ts_ms=1_200, leverage=D("5"))
    assert locked.value.code == "LEVERAGE_LOCKED_WHILE_OPEN"


def test_leverage_outside_the_instrument_range_or_off_its_step_is_refused(engine: PaperEngine) -> None:
    for bad in (D("0.5"), D("151"), D("10.005")):
        with pytest.raises(ValueError):
            engine.set_leverage(ts_ms=1_050, leverage=bad)


def test_an_order_needing_more_margin_than_is_available_is_refused(config, tiers) -> None:
    engine = started_engine(make_config(leverage="1"), tiers)
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.100"), intent="OPEN", request_id="r")
    assert rejected.value.code == "INSUFFICIENT_MARGIN"
    assert engine.account.position.is_flat
    assert engine.account.cumulative_fees == 0


def test_leverage_above_the_tier_maximum_is_refused_at_submission(config, tiers) -> None:
    """Tier 2 caps leverage at 100x, so a large notional cannot keep a 150x setting."""
    engine = started_engine(make_config(leverage="150", capital_krw="40000000000", fx="1"), tiers,
                            first_quote=quote(1_000, bid="100000.0", ask="100000.1",
                                              bid_qty="100", ask_qty="100"))
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("5.000"), intent="OPEN", request_id="r")
    assert rejected.value.code == "LEVERAGE_ABOVE_TIER"


def test_a_notional_beyond_the_highest_risk_limit_is_refused(config, tiers) -> None:
    engine = started_engine(make_config(leverage="1", capital_krw="40000000000", fx="1"), tiers,
                            first_quote=quote(1_000, bid="100000.0", ask="100000.1",
                                              bid_qty="100", ask_qty="100"))
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("30.000"), intent="OPEN", request_id="r")
    assert rejected.value.code == "RISK_LIMIT_EXCEEDED"


# ------------------------------------------------------------------ risk tiers

def test_the_maintenance_margin_formula_is_continuous_across_every_real_tier_boundary() -> None:
    """The verified fact behind the deduction column: D2 left this UNKNOWN, D3 measured it."""
    import glob
    from pathlib import Path
    files = sorted(glob.glob("data/runtime/crypto/BTCUSDT/reference/risk_limit_*.json"))
    if not files:
        pytest.skip("no captured risk-limit response in this workspace")
    table = RiskTierTable.from_file(Path(files[-1]))
    assert len(table.tiers) >= 3
    assert table.verify_continuity() == []


def test_a_tier_boundary_belongs_to_the_lower_tier(tiers: RiskTierTable) -> None:
    assert tiers.tier_for_notional(D("300000")).risk_id == 1
    assert tiers.tier_for_notional(D("300000.01")).risk_id == 2
    assert tiers.maintenance_margin(D("300000")) == D("300000") * D("0.0033")


def test_growing_a_position_across_a_tier_boundary_raises_the_requirement_continuously(tiers) -> None:
    below = tiers.maintenance_margin(D("299999"))
    at = tiers.maintenance_margin(D("300000"))
    above = tiers.maintenance_margin(D("300001"))
    assert below < at < above
    # Just past the boundary the requirement grows at the *new* tier's rate, with no jump:
    # the deduction absorbs the rate change exactly.
    assert above - at == D("0.005")
    assert at - below == D("0.0033")


def test_the_position_view_reports_the_tier_it_is_currently_in(engine: PaperEngine) -> None:
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    view = engine.account.view(engine.tiers, D("100000"))
    assert view["risk_tier"] == 1
    assert view["maintenance_margin"] == D("1000.00") * D("0.0033")


# ------------------------------------------------------------------ liquidation

def test_a_long_is_liquidated_when_the_mark_reaches_the_computed_price(config, tiers) -> None:
    engine = started_engine(make_config(leverage="100", taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    liquidation_price = engine.account.liquidation_price(tiers, D("100000"))
    assert liquidation_price is not None
    engine.apply_market(quote(2_000, bid="99000.0", ask="99000.1",
                              mark=str(liquidation_price - D("1"))))
    liquidations = engine.ledger.of_type("LIQUIDATION")
    assert len(liquidations) == 1 and engine.liquidation_count == 1
    assert liquidations[0]["trigger"] == "MARK_PRICE"
    assert engine.account.position.is_flat
    assert engine.ledger.events[-1]["event_type"] in {"POSITION_CLOSE", "FEE"}


def test_a_short_is_liquidated_on_the_way_up(config, tiers) -> None:
    engine = started_engine(make_config(leverage="100", taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="SHORT", qty=D("0.010"), intent="OPEN", request_id="o")
    liquidation_price = engine.account.liquidation_price(tiers, D("100000"))
    engine.apply_market(quote(2_000, bid="101000.0", ask="101000.1",
                              mark=str(liquidation_price + D("1"))))
    assert engine.liquidation_count == 1
    assert engine.account.position.is_flat


def test_at_the_liquidation_price_position_equity_equals_the_maintenance_requirement(tiers) -> None:
    """The formula, checked against its own definition rather than a remembered constant."""
    for signed in (D("0.010"), D("-0.010")):
        account = Account(starting_capital_usdt=D("1000"),
                          position=Position(signed_qty=signed, avg_entry=D("100000"),
                                            leverage=D("50")))
        price = account.liquidation_price(tiers, D("100000"))
        assert price is not None
        equity = account.used_margin + account.unrealized_pnl(price)
        assert abs(equity - account.maintenance_margin(tiers, price)) < D("0.00000001")


def test_a_liquidation_is_not_approximated_as_a_large_loss_but_recorded_as_its_own_event(config, tiers) -> None:
    engine = started_engine(make_config(leverage="100", taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bid="50000.0", ask="50000.1", mark="50000.0"))
    event = engine.ledger.of_type("LIQUIDATION")[0]
    assert event["fill_basis"] == "MARK_AT_TRIGGER"
    assert D(event["unrealized_pnl"]) < 0
    assert D(event["maintenance_margin"]) > 0
    close = engine.ledger.of_type("POSITION_CLOSE")[0]
    assert close["reason"] == "LIQUIDATION"
    assert D(close["exit_price"]) == D("50000.0")


def test_funding_can_be_what_triggers_a_liquidation(config, tiers) -> None:
    """Funding settles before the liquidation check, so a settlement can tip a position over."""
    settlement = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(leverage="100", taker="0"), tiers,
                            ts_ms=settlement - 2_000,
                            first_quote=quote(settlement - 2_000, funding_rate="0"))
    engine.submit_order(ts_ms=settlement - 1_500, side="LONG", qty=D("0.010"), intent="OPEN",
                        request_id="o")
    order = [event["event_type"] for event in engine.ledger.events]
    engine.apply_market(quote(settlement + 1_000, bid="50000.0", ask="50000.1", mark="50000.0",
                              funding_rate="0.0001"))
    after = [event["event_type"] for event in engine.ledger.events][len(order):]
    assert after.index("FUNDING") < after.index("LIQUIDATION")


# ------------------------------------------------------------------ mark vs last

def test_valuation_and_liquidation_follow_the_mark_even_when_last_diverges(config, tiers) -> None:
    """Contract P2/P3/Q2: last price must not reach the account at all."""
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    entry = engine.account.position.avg_entry
    diverged = quote(2_000, bid="100000.0", ask="100000.1", mark="99000.0", last="105000.0")
    engine.apply_market(diverged)
    assert engine.account.unrealized_pnl(diverged.mark_price) == (D("99000.0") - entry) * D("0.010")
    view = engine.account.view(tiers, diverged.mark_price)
    assert view["unrealized_pnl"] < 0  # the last price is higher, the mark is what counts
    assert view["mark_notional"] == D("0.010") * D("99000.0")


def test_a_fill_uses_the_book_while_valuation_uses_the_mark(config, tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers,
                            first_quote=quote(1_000, bid="100000.0", ask="100000.1", mark="99500.0"))
    result = engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN",
                                 request_id="o")
    assert result["fill_price"] == D("100000.1")           # book
    assert engine.account.unrealized_pnl(D("99500.0")) < 0  # mark


# ------------------------------------------------------------------ reconnect

def test_an_open_position_survives_a_feed_reconnect_and_orders_wait_for_the_book(config, tiers) -> None:
    """After a reconnect the local book is empty while tickers still carry a mark. The position
    keeps its accounting, and an order is refused rather than filled against nothing."""
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    before = engine.account.view(tiers, D("100000"))
    empty_book = quote(2_000, bids=[], asks=[], mark="100200.0")
    engine.apply_market(empty_book)
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=2_100, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    assert rejected.value.code == "NO_QUOTE"
    assert engine.account.position.signed_qty == before["position_signed_qty"]
    assert engine.account.position.avg_entry == before["avg_entry"]
    # Valuation continued from the ticker mark even with no book.
    assert engine.account.unrealized_pnl(D("100200.0")) > 0
    engine.apply_market(quote(3_000, bid="100200.0", ask="100200.1"))
    engine.submit_order(ts_ms=3_100, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c2")
    assert engine.account.position.is_flat


def test_an_order_before_any_quote_is_refused(config, tiers) -> None:
    engine = PaperEngine(config, tiers)
    engine.start(1_000)
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="r")
    assert rejected.value.code == "NO_QUOTE"


# ------------------------------------------------------------------ invariants

def test_the_account_invariants_hold_after_every_event_of_a_full_cycle(config, tiers) -> None:
    settlement = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(taker="0.0006"), tiers, ts_ms=settlement - 3_000,
                            first_quote=quote(settlement - 3_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=settlement - 2_500, side="SHORT", qty=D("0.010"), intent="OPEN",
                        request_id="o")
    engine.account.assert_invariants(D("100000"))
    engine.apply_market(quote(settlement + 1_000, bid="99500.0", ask="99500.1",
                              funding_rate="0.0001"))
    engine.account.assert_invariants(D("99500"))
    engine.submit_order(ts_ms=settlement + 2_000, side="SHORT", qty=D("0.004"), intent="CLOSE",
                        request_id="p")
    engine.account.assert_invariants(D("99500"))
    engine.submit_order(ts_ms=settlement + 3_000, side="SHORT", qty=D("0.006"), intent="CLOSE",
                        request_id="c")
    engine.account.assert_invariants(D("99500"))
    account = engine.account
    assert account.equity(D("99500")) == (account.starting_capital_usdt + account.realized_pnl
                                          - account.cumulative_fees - account.cumulative_funding_paid)


def test_a_broken_invariant_is_raised_rather_than_reported_as_a_number(tiers) -> None:
    account = Account(starting_capital_usdt=D("1000"))
    account.realized_pnl = D("50")
    account.cumulative_fees = D("1")
    account.assert_invariants(D("100000"))
    object.__setattr__(account, "starting_capital_usdt", D("999"))
    account.position = Position(signed_qty=D("0.01"), avg_entry=D("100000"), leverage=D("10"))
    account.assert_invariants(D("100000"))  # still consistent: the identity is structural


def test_a_settlement_missed_while_nothing_was_watching_is_marked_an_estimate(config, tiers) -> None:
    """After downtime the engine still owes the funding, but the rate it has is today's, not
    the one that applied back then. Applying it silently would bury an estimate inside a
    figure that reads like a measurement."""
    step = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(taker="0"), tiers, ts_ms=step - 1_000,
                            first_quote=quote(step - 1_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=step - 500, side="LONG", qty=D("0.010"), intent="OPEN",
                        request_id="o")
    # One tick, three settlements behind: the process was down for a day.
    engine.apply_market(quote(3 * step + 1_000, funding_rate="0.0002"))

    events = engine.ledger.of_type("FUNDING")
    assert len(events) == 3
    assert [event["catch_up"] for event in events] == [True, True, False]
    assert events[0]["rate_basis"] == "CURRENT_RATE_APPLIED_TO_PAST_SETTLEMENT"
    assert events[-1]["rate_basis"] == "RATE_AT_SETTLEMENT"
    assert events[0]["observation_gap_ms"] > 8 * 60 * 60 * 1000
    assert engine.funding_catch_ups == 2
    assert engine.snapshot()["funding_catch_ups"] == 2
    # The money still moves: the position did hold through those settlements.
    assert engine.account.cumulative_funding_paid > 0


def test_a_settlement_observed_on_time_is_not_flagged(config, tiers) -> None:
    step = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(taker="0"), tiers, ts_ms=step - 2_000,
                            first_quote=quote(step - 2_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=step - 1_500, side="LONG", qty=D("0.010"), intent="OPEN",
                        request_id="o")
    engine.apply_market(quote(step + 1_000, funding_rate="0.0001"))
    event = engine.ledger.of_type("FUNDING")[0]
    assert event["catch_up"] is False and event["rate_basis"] == "RATE_AT_SETTLEMENT"
    assert engine.funding_catch_ups == 0

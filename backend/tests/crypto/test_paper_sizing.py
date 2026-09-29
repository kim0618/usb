"""Quick-size presets. The point of every test here is that the panel cannot disagree with the
engine: whatever MAX says is affordable must actually fill, and whatever it says is not must
actually be refused, for the engine's own stated reason."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.paper import sizing
from app.crypto.paper.engine import OrderRejected, PaperEngine
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


def deep_engine(tiers, **config_kwargs) -> PaperEngine:
    """A book deep enough that the binding constraint is margin, not liquidity."""
    return started_engine(make_config(**config_kwargs), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))


# ------------------------------------------------------------------ probing


def test_a_probe_leaves_the_live_engine_completely_untouched(engine: PaperEngine) -> None:
    before_events = len(engine.ledger.events)
    before_qty = engine.account.position.signed_qty
    before_fees = engine.account.cumulative_fees

    result = sizing.probe(engine, "LONG", D("0.010"))

    assert result.feasible
    # The whole design rests on this: a probe must not write a ledger event, move the position
    # or charge a fee. If it ever does, the panel starts trading by being looked at.
    assert len(engine.ledger.events) == before_events
    assert engine.account.position.signed_qty == before_qty
    assert engine.account.cumulative_fees == before_fees


def test_a_probe_reports_the_engines_own_fill_price_and_fee(engine: PaperEngine) -> None:
    probed = sizing.probe(engine, "LONG", D("0.010"))
    real = engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN",
                               request_id="r1")
    assert probed.fill_price == real["fill_price"]
    assert probed.fee == real["fee"]


def test_a_probe_prices_the_walk_into_deeper_levels_rather_than_the_top_of_book(config, tiers) -> None:
    thin = started_engine(config, tiers, first_quote=quote(
        1_000, asks=[["100000.1", "0.004"], ["100000.5", "1"]], bids=[["100000.0", "10"]]))
    probed = sizing.probe(thin, "LONG", D("0.010"))
    expected = (D("100000.1") * D("0.004") + D("100000.5") * D("0.006")) / D("0.010")
    assert probed.fill_price == expected


def test_an_unaffordable_probe_carries_the_engines_rejection_code(engine: PaperEngine) -> None:
    result = sizing.probe(engine, "LONG", D("100.000"))
    assert not result.feasible
    assert result.reject_code in {"INSUFFICIENT_MARGIN", "NO_LIQUIDITY"}
    assert result.reject_message


# ------------------------------------------------------------------ max


def test_max_is_the_largest_size_that_actually_fills(tiers) -> None:
    engine = deep_engine(tiers)
    ceiling = sizing.max_entry(engine, "LONG")
    assert ceiling.feasible

    # It fills for real.
    engine.submit_order(ts_ms=1_100, side="LONG", qty=ceiling.qty, intent="OPEN", request_id="r1")
    assert engine.account.position.abs_qty == ceiling.qty


def test_one_step_above_max_is_refused_by_the_engine(tiers) -> None:
    engine = deep_engine(tiers)
    ceiling = sizing.max_entry(engine, "LONG")
    step = engine.config.instrument.qty_step
    with pytest.raises(OrderRejected) as raised:
        engine.submit_order(ts_ms=1_100, side="LONG", qty=ceiling.qty + step, intent="OPEN",
                            request_id="r1")
    assert raised.value.code == "INSUFFICIENT_MARGIN"


def test_max_lands_on_the_quantity_grid(tiers) -> None:
    ceiling = sizing.max_entry(deep_engine(tiers), "LONG")
    step = D("0.001")
    assert ceiling.qty % step == 0


def test_more_leverage_buys_a_larger_max(tiers) -> None:
    low = sizing.max_entry(deep_engine(tiers, leverage="5"), "LONG")
    high = sizing.max_entry(deep_engine(tiers, leverage="20"), "LONG")
    assert high.qty > low.qty


def test_max_is_capped_by_book_depth_when_the_book_is_the_binding_constraint(tiers) -> None:
    shallow = started_engine(make_config(leverage="50"), tiers, first_quote=quote(
        1_000, asks=[["100000.1", "0.005"]], bids=[["100000.0", "100"]]))
    ceiling = sizing.max_entry(shallow, "LONG")
    assert ceiling.qty == D("0.005")


def test_max_reserves_the_fee_and_not_only_the_margin(tiers) -> None:
    engine = deep_engine(tiers)
    ceiling = sizing.max_entry(engine, "LONG")
    # Everything the entry consumes has to fit in what was available beforehand.
    assert ceiling.required_total == ceiling.reserved_margin + ceiling.fee
    assert ceiling.required_total <= engine.account.available_balance
    # And the fee is a real component, not a rounding artefact.
    assert ceiling.fee > 0


def test_max_is_zero_with_a_reason_when_nothing_is_affordable(tiers) -> None:
    broke = deep_engine(tiers, capital_krw="1000", fx="1000")  # 1 USDT of capital
    ceiling = sizing.max_entry(broke, "LONG")
    assert not ceiling.feasible
    assert ceiling.qty == 0
    assert ceiling.reject_code in {"INSUFFICIENT_MARGIN", "NOTIONAL_BELOW_MINIMUM"}


def test_max_is_refused_with_the_state_machines_reason_in_emergency(tiers) -> None:
    engine = deep_engine(tiers)
    engine.set_mode(ts_ms=1_100, action="EMERGENCY_ON", confirmed=True)
    ceiling = sizing.max_entry(engine, "LONG")
    assert not ceiling.feasible
    assert ceiling.reject_code == "NEW_ENTRY_BLOCKED_EMERGENCY"


def test_max_on_the_opposite_side_of_an_open_position_reports_reverse_not_allowed(tiers) -> None:
    engine = deep_engine(tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.001"), intent="OPEN", request_id="r1")
    ceiling = sizing.max_entry(engine, "SHORT")
    assert not ceiling.feasible
    assert ceiling.reject_code == "REVERSE_NOT_ALLOWED"


def test_max_accounts_for_margin_already_used_by_an_open_position(tiers) -> None:
    flat = deep_engine(tiers)
    from_flat = sizing.max_entry(flat, "LONG")

    held = deep_engine(tiers)
    held.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="r1")
    adding = sizing.max_entry(held, "LONG")

    # Adding to a position cannot buy as much as opening from flat did: the first entry's margin
    # and fee are already spent.
    assert adding.feasible
    assert adding.qty < from_flat.qty


# ------------------------------------------------------------------ presets


def test_presets_are_fractions_of_max_floored_to_the_grid(tiers) -> None:
    engine = deep_engine(tiers)
    payload = sizing.presets(engine, "LONG")
    by_label = {row["label"]: row for row in payload["presets"]}
    assert set(by_label) == {"25%", "HALF", "75%", "MAX"}

    ceiling = payload["max_qty"]
    step = D("0.001")
    assert by_label["MAX"]["qty"] == ceiling
    for label, fraction in (("25%", D("0.25")), ("HALF", D("0.50")), ("75%", D("0.75"))):
        assert by_label[label]["qty"] == sizing.floor_to_step(ceiling * fraction, step)
        assert by_label[label]["qty"] % step == 0


def test_every_feasible_preset_actually_fills(tiers) -> None:
    payload = sizing.presets(deep_engine(tiers), "LONG")
    for row in payload["presets"]:
        if not row["feasible"]:
            continue
        engine = deep_engine(tiers)
        engine.submit_order(ts_ms=1_100, side="LONG", qty=row["qty"], intent="OPEN",
                            request_id="r1")
        assert engine.account.position.abs_qty == row["qty"]


def test_each_preset_is_priced_by_its_own_probe_not_scaled_down_from_max(tiers) -> None:
    # A book where the second level is much worse, so a proportional estimate would be wrong.
    engine = started_engine(make_config(leverage="10"), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "100"]],
        asks=[["100000.1", "0.003"], ["120000.0", "100"]]))
    payload = sizing.presets(engine, "LONG")
    by_label = {row["label"]: row for row in payload["presets"]}
    small, biggest = by_label["25%"], by_label["MAX"]
    if small["feasible"] and biggest["feasible"] and small["qty"] < biggest["qty"]:
        # The larger order eats the expensive level, so its average price is strictly worse.
        assert biggest["fill_price"] > small["fill_price"]


def test_a_preset_below_the_minimum_order_size_is_infeasible_with_the_engines_reason(tiers) -> None:
    # MAX is 0.003, so 25% floors to 0.000 which is below minOrderQty.
    engine = started_engine(make_config(leverage="50"), tiers, first_quote=quote(
        1_000, asks=[["100000.1", "0.003"]], bids=[["100000.0", "100"]]))
    payload = sizing.presets(engine, "LONG")
    quarter = next(row for row in payload["presets"] if row["label"] == "25%")
    assert not quarter["feasible"]
    assert quarter["reject_code"] in {"QTY_NOT_POSITIVE", "QTY_BELOW_MINIMUM"}


def test_presets_report_the_liquidation_price_the_account_would_have(tiers) -> None:
    engine = deep_engine(tiers)
    payload = sizing.presets(engine, "LONG")
    biggest = next(row for row in payload["presets"] if row["label"] == "MAX")
    assert biggest["liquidation_price"] is not None

    engine.submit_order(ts_ms=1_100, side="LONG", qty=biggest["qty"], intent="OPEN",
                        request_id="r1")
    actual = engine.account.liquidation_price(engine.tiers, engine.quote.mark_price)
    assert biggest["liquidation_price"] == actual


def test_presets_carry_the_instrument_limits_so_the_screen_need_not_hardcode_them(tiers) -> None:
    payload = sizing.presets(deep_engine(tiers), "LONG")
    assert payload["instrument"]["qty_step"] == D("0.001")
    assert payload["instrument"]["min_order_qty"] == D("0.001")
    assert payload["instrument"]["min_notional_value"] == D("5")


def test_a_blocked_side_reports_every_preset_as_infeasible_with_one_reason(tiers) -> None:
    engine = deep_engine(tiers)
    engine.set_mode(ts_ms=1_100, action="EMERGENCY_ON", confirmed=True)
    payload = sizing.presets(engine, "LONG")
    assert payload["max_feasible"] is False
    assert all(not row["feasible"] for row in payload["presets"])
    assert all(row["reject_code"] == "NEW_ENTRY_BLOCKED_EMERGENCY" for row in payload["presets"])

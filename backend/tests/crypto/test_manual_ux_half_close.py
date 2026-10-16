"""HALF CLOSE V1: a sized CLOSE reduces the held position and can never open the other side.

PAPER: the engine's own partial CLOSE, through the order route, on a hand-fed app.
LIVE: the order router against the mock Binance transport; nothing reaches a real account.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.live import orders
from app.crypto.live.orders import OrderRefused
from app.crypto.terminal.executions import live_executions
from tests.crypto.binance_fixtures import POSITION_RISK_LONG
from tests.crypto.test_binance_live import FakeBinance, router
from tests.crypto.test_manual_ux_account_sharing import ARC, to_arc
from tests.crypto.test_manual_ux_final_v1 import account, client, executions, order  # noqa: F401
from tests.crypto.test_hot_symbol_v1 import clean_dynamic, paper  # noqa: F401
from tests.crypto.test_hot_symbol_v1 import order as hot_order

D = Decimal


def half(held: str, step: str = "0.001") -> str:
    """The rule the screen applies: half the held size in whole steps, floored."""
    steps = (D(held) / D(step)).to_integral_value()
    return str((steps // 2) * D(step))


# ------------------------------------------------------------------ PAPER

@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_a_b_paper_half_close_leaves_half_and_marks_a_partial_close(client, side) -> None:
    order(client, side, "0.100")
    before = account(client)
    order(client, side, half("0.100"), intent="CLOSE")
    after = account(client)
    assert after["position_side"] == side and D(after["position_qty"]) == D("0.050")
    assert after["avg_entry"] == before["avg_entry"]  # the engine's average, untouched by a reduce
    assert D(after["realized_pnl"]) != D(before["realized_pnl"])  # the closed half is realised
    last = executions(client)[-1]
    assert last["event_type"] == "PARTIAL_CLOSE" and last["side"] == side
    assert last["qty"] == "0.050" and last["mode"] == "PAPER" and last["symbol"] == "BTCUSDT"
    assert last["seq"] == 2 and last["ts_ms"] > 0 and D(last["price"]) > 0


def test_c_odd_quantity_floors_to_the_step() -> None:
    assert half("0.107") == "0.053"
    assert D("0.107") - D(half("0.107")) == D("0.054")
    assert half("0.001") == "0.000"  # one step cannot be halved: no HALF CLOSE


def test_d_paper_half_below_the_minimum_is_refused_and_sends_nothing(client) -> None:
    order(client, "LONG", "0.001")
    before = len(executions(client))
    # The screen never sends this (its button is off); the engine refuses it if anything does.
    response = client.post("/api/crypto/order", json={"side": "LONG", "intent": "CLOSE", "qty": "0.0005"})
    assert response.status_code == 409
    assert len(executions(client)) == before and D(account(client)["position_qty"]) == D("0.001")


def test_paper_half_close_never_opens_the_other_side(client) -> None:
    order(client, "LONG", "0.010")
    response = client.post("/api/crypto/order", json={"side": "LONG", "intent": "CLOSE", "qty": "0.020"})
    assert response.status_code == 409
    assert account(client)["position_side"] == "LONG" and D(account(client)["position_qty"]) == D("0.010")


def test_i_half_half_full_sequence(client) -> None:
    order(client, "LONG", "0.100")
    order(client, "LONG", half("0.100"), intent="CLOSE")             # 0.050 left
    order(client, "LONG", half("0.050"), intent="CLOSE")             # 0.025 left
    held = account(client)["position_qty"]
    order(client, "LONG", held, intent="CLOSE")
    assert account(client)["position_side"] is None
    rows = executions(client)
    assert [(row["seq"], row["event_type"], row["qty"]) for row in rows] == [
        (1, "INITIAL_ENTRY", "0.100"), (2, "PARTIAL_CLOSE", "0.050"),
        (3, "PARTIAL_CLOSE", "0.025"), (4, "FULL_CLOSE", "0.025")]
    # J. A reload reads the same rows: no duplicate.
    assert executions(client) == rows


def test_h_paper_half_close_on_btc_leaves_the_hot_position_alone(paper) -> None:  # noqa: F811
    client_, runtime, use_market, _ = paper
    assert hot_order(client_, "BTCUSDT", "LONG", qty="0.010").status_code == 200
    to_arc(client_, runtime, use_market)
    assert hot_order(client_, ARC, "LONG", qty="1000.0").status_code == 200
    arc_before = runtime.sessions[ARC].engine.account.position.signed_qty
    assert hot_order(client_, "BTCUSDT", "LONG", intent="CLOSE", qty="0.005").status_code == 200
    assert runtime.sessions["BTCUSDT"].engine.account.position.signed_qty == D("0.005")
    assert runtime.sessions[ARC].engine.account.position.signed_qty == arc_before


# ------------------------------------------------------------------ LIVE

def short_rows():
    return [{**POSITION_RISK_LONG[0], "positionAmt": "-0.015", "notional": "-1252.50000000"}]


@pytest.mark.parametrize("side,rows,order_side", [
    ("LONG", None, "SELL"), ("SHORT", "short", "BUY")])
def test_e_f_live_half_close_is_reduce_only(tmp_path: Path, side, rows, order_side) -> None:
    fake = FakeBinance()
    if rows == "short":
        fake.position_rows = short_rows()
    route, fake, _ = router(fake, armed=True, tmp_path=tmp_path)
    plan = route.plan(side=side, intent="CLOSE", qty=half("0.015"))
    assert plan.intent == orders.CLOSE and plan.reduce_only is True and plan.side == side
    assert plan.qty == D("0.007") and plan.position_qty_at_plan == D("0.015")
    route.submit(plan)
    _, path, params = fake.calls[-1]
    assert path == "/fapi/v1/order"
    assert params["reduceOnly"] == "true" and params["side"] == order_side
    assert params["quantity"] == "0.007"


def test_live_full_close_is_unchanged_and_still_reduce_only(tmp_path: Path) -> None:
    route, fake, _ = router(armed=True, tmp_path=tmp_path)
    plan = route.plan(side="LONG", intent="CLOSE")
    assert plan.qty == D("0.015") and plan.reduce_only is True


def test_live_half_close_larger_than_the_position_is_refused_never_flipped(tmp_path: Path) -> None:
    route, fake, _ = router(armed=True, tmp_path=tmp_path)
    with pytest.raises(OrderRefused) as caught:
        route.plan(side="LONG", intent="CLOSE", qty="0.020")
    assert caught.value.code == orders.REVERSE_NOT_ALLOWED
    assert fake.count("/fapi/v1/order") == 0


def test_d_live_half_below_the_exchange_minimum_is_refused(tmp_path: Path) -> None:
    fake = FakeBinance()
    fake.position_rows = [{**POSITION_RISK_LONG[0], "positionAmt": "0.002", "notional": "167.00"}]
    route, fake, _ = router(fake, armed=True, tmp_path=tmp_path)
    with pytest.raises(OrderRefused):
        route.plan(side="LONG", intent="CLOSE", qty=half("0.002"))  # 0.001 BTC: under MIN_NOTIONAL
    assert fake.count("/fapi/v1/order") == 0


def test_live_half_close_is_gated_like_every_live_order(tmp_path: Path) -> None:
    route, fake, _ = router(armed=False, tmp_path=tmp_path)
    plan = route.plan(side="LONG", intent="CLOSE", qty="0.007")
    with pytest.raises(OrderRefused) as caught:
        route.submit(plan)
    assert caught.value.code == orders.LIVE_TRADING_DISABLED
    assert fake.count("/fapi/v1/order") == 0


def test_live_half_then_full_marks_partial_then_full() -> None:
    lines = [
        {"event_type": "LIVE_ORDER_INTENT", "stage": "SUBMIT", "client_order_id": "h",
         "symbol": "BTCUSDT", "intent": "CLOSE", "side": "LONG", "position_qty_at_plan": "0.015",
         "leverage": "20", "account": "a"},
        {"event_type": "LIVE_ORDER_RESULT", "client_order_id": "h", "account": "a",
         "response": {"executedQty": "0.007", "avgPrice": "84000", "updateTime": 1}},
        {"event_type": "LIVE_ORDER_INTENT", "stage": "SUBMIT", "client_order_id": "f",
         "symbol": "BTCUSDT", "intent": "CLOSE", "side": "LONG", "position_qty_at_plan": "0.008",
         "leverage": "20", "account": "a"},
        {"event_type": "LIVE_ORDER_RESULT", "client_order_id": "f", "account": "a",
         "response": {"executedQty": "0.008", "avgPrice": "84100", "updateTime": 2}},
    ]
    rows = live_executions(lines, symbol="BTCUSDT", account="a")
    assert [(row["seq"], row["event_type"]) for row in rows] == [(1, "PARTIAL_CLOSE"), (2, "FULL_CLOSE")]

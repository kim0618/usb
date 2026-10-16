"""LIVE execution marker prices against the answers Binance really gives.

Binance's default MARKET answer (`newOrderRespType=ACK`) carries no `avgPrice`, no `cumQuote`
and no `fills`; every one of the 41 real results in the production audit mirror looks like
`_ack` below, and the first version of `live_executions` drew none of them. The price now comes
from the answer when it has one and otherwise from the `userTrades` the performance panel has
already cached on disk - never from a mark or a quote, and never from a new signed request.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.crypto.live.performance import ManualLivePerformance
from app.crypto.terminal import live_routes
from app.crypto.terminal.executions import (
    ADD_ENTRY, DROP_NO_PRICE, DROP_TRADES_INCOMPLETE, FULL_CLOSE, INITIAL_ENTRY, PARTIAL_CLOSE,
    PRICE_AVG, PRICE_CUM_QUOTE, PRICE_FILLS, PRICE_USER_TRADES, live_execution_audit,
    live_executions, live_executions_from_file, resolve_fill_price, user_trades_by_order)
from tests.crypto.test_binance_live_api import live  # noqa: F401  (fixture)


def _ack(cid: str, order_id: int, qty: str, *, side: str = "BUY", reduce_only: bool = False,
         at: int = 1_791_339_778_021) -> dict:
    """The real shape: copied key for key from a production `LIVE_ORDER_RESULT`."""
    return {"clientOrderId": cid, "closePosition": False, "cumQty": qty, "executedQty": qty,
            "goodTillDate": 0, "orderId": order_id, "origQty": qty, "origType": "MARKET",
            "positionSide": "BOTH", "price": "0.00", "priceMatch": "NONE", "priceProtect": False,
            "reduceOnly": reduce_only, "selfTradePreventionMode": "EXPIRE_MAKER", "side": side,
            "status": "FILLED", "stopPrice": "0.00", "symbol": "BTCUSDT", "timeInForce": "GTC",
            "type": "MARKET", "updateTime": at, "workingType": "CONTRACT_PRICE"}


def _intent(cid: str, *, intent: str = "OPEN", side: str = "LONG", held: str = "0",
            symbol: str = "BTCUSDT") -> dict:
    return {"account": "acct", "event_type": "LIVE_ORDER_INTENT", "stage": "SUBMIT",
            "client_order_id": cid, "symbol": symbol, "intent": intent, "side": side,
            "position_qty_at_plan": held, "leverage": 20}


def _result(cid: str, response: dict) -> dict:
    return {"account": "acct", "event_type": "LIVE_ORDER_RESULT", "client_order_id": cid,
            "response": response, "ts_ms": response.get("updateTime", 0)}


def _trade(trade_id: int, order_id: int, qty: str, price: str, *, cid: str = "x") -> dict:
    """A cached `userTrades` row, as `ManualLivePerformance` writes it."""
    return {"id": trade_id, "order_id": order_id, "client_order_id": cid, "qty": qty,
            "price": price, "quote_qty": str(Decimal(qty) * Decimal(price)), "symbol": "BTCUSDT",
            "side": "BUY", "buyer": True, "maker": False, "manual": True, "commission": "0",
            "commission_asset": "USDT", "position_side": "BOTH", "realized_pnl": "0",
            "time_ms": 1}


def _cache(path: Path, trades: list[dict]) -> Path:
    path.write_text(json.dumps({"version": 1, "trades": trades, "funding": []}))
    return path


# ------------------------------------------------------------------------- price resolution

def test_an_explicit_avg_price_wins() -> None:
    response = {**_ack("c", 1, "0.010"), "avgPrice": "84000.5"}
    trades = [_trade(1, 1, "0.010", "1")]  # a disagreeing cache does not override the answer
    assert resolve_fill_price(response, Decimal("0.010"), trades) == (Decimal("84000.5"), PRICE_AVG)


def test_fills_are_volume_weighted() -> None:
    response = {**_ack("c", 1, "0.004"), "fills": [{"qty": "0.001", "price": "84000"},
                                                   {"qty": "0.003", "price": "84100"}]}
    price, source = resolve_fill_price(response, Decimal("0.004"), None)
    assert (price, source) == (Decimal("84075"), PRICE_FILLS)


def test_cum_quote_over_executed_qty() -> None:
    response = {**_ack("c", 1, "0.003"), "cumQuote": "252.30000"}
    assert resolve_fill_price(response, Decimal("0.003"), None) == (Decimal("84100"),
                                                                    PRICE_CUM_QUOTE)


def test_a_market_ack_without_a_price_is_priced_from_the_cached_trades() -> None:
    trades = [_trade(1, 7, "0.002", "84000"), _trade(2, 7, "0.001", "84300")]
    price, source = resolve_fill_price(_ack("c", 7, "0.003"), Decimal("0.003"), trades)
    assert (price, source) == (Decimal("84100"), PRICE_USER_TRADES)


def test_a_three_way_average_is_cut_to_eight_places() -> None:
    trades = [_trade(1, 7, "0.001", "84000"), _trade(2, 7, "0.001", "84000"),
              _trade(3, 7, "0.001", "84001")]
    price, _ = resolve_fill_price(_ack("c", 7, "0.003"), Decimal("0.003"), trades)
    assert str(price) == "84000.33333333"


def test_no_price_anywhere_is_dropped_not_guessed() -> None:
    # `price: "0.00"` on a MARKET answer is not a fill price.
    assert resolve_fill_price(_ack("c", 7, "0.003"), Decimal("0.003"), None) == (None,
                                                                                 DROP_NO_PRICE)


def test_a_cache_that_has_not_caught_up_is_not_used() -> None:
    trades = [_trade(1, 7, "0.001", "84000")]  # 0.001 of 0.003 cached so far
    assert resolve_fill_price(_ack("c", 7, "0.003"), Decimal("0.003"), trades) == (
        None, DROP_TRADES_INCOMPLETE)


def test_zero_executed_qty_is_never_an_execution(tmp_path: Path) -> None:
    lines = [_intent("o"), _result("o", {**_ack("o", 7, "0"), "status": "EXPIRED"})]
    trades = user_trades_by_order(_cache(tmp_path / "p.json", [_trade(1, 7, "0.001", "84000")]))
    rows, dropped = live_execution_audit(lines, symbol="BTCUSDT", account="acct", trades=trades)
    assert rows == [] and dropped == []


# ------------------------------------------------------------- classification is unchanged

def _sequence() -> tuple[list[dict], list[dict]]:
    lines = [
        _intent("o1"), _result("o1", _ack("o1", 1, "0.010", at=1)),
        _intent("o2", held="0.010"), _result("o2", _ack("o2", 2, "0.010", at=2)),
        _intent("c1", intent="CLOSE", held="0.020"),
        _result("c1", _ack("c1", 3, "0.010", side="SELL", reduce_only=True, at=3)),
        _intent("c2", intent="CLOSE", held="0.010"),
        _result("c2", _ack("c2", 4, "0.010", side="SELL", reduce_only=True, at=4)),
    ]
    trades = [_trade(11, 1, "0.010", "84000"), _trade(12, 2, "0.004", "84100"),
              _trade(13, 2, "0.006", "84200"), _trade(14, 3, "0.010", "84500"),
              _trade(15, 4, "0.010", "84400")]
    return lines, trades


def test_add_entry_partial_close_and_full_close_keep_their_classes(tmp_path: Path) -> None:
    lines, trades = _sequence()
    rows = live_executions(lines, symbol="BTCUSDT", account="acct",
                           trades=user_trades_by_order(_cache(tmp_path / "p.json", trades)))
    assert [(r["event_type"], r["price"], r["qty"]) for r in rows] == [
        (INITIAL_ENTRY, "84000", "0.010"), (ADD_ENTRY, "84160", "0.010"),
        (PARTIAL_CLOSE, "84500", "0.010"), (FULL_CLOSE, "84400", "0.010")]
    assert {r["price_source"] for r in rows} == {PRICE_USER_TRADES}


def test_half_close_marker_uses_the_real_fill_price(tmp_path: Path) -> None:
    """A HALF CLOSE is a sized reduceOnly CLOSE; its marker is priced like any other fill."""
    lines = [_intent("o"), _result("o", _ack("o", 1, "0.107", at=1)),
             _intent("h", intent="CLOSE", held="0.107"),
             _result("h", _ack("h", 2, "0.053", side="SELL", reduce_only=True, at=2))]
    trades = user_trades_by_order(_cache(tmp_path / "p.json", [
        _trade(1, 1, "0.107", "84000"), _trade(2, 2, "0.050", "84210"),
        _trade(3, 2, "0.003", "84200")]))
    half = live_executions(lines, symbol="BTCUSDT", account="acct", trades=trades)[-1]
    assert half["event_type"] == PARTIAL_CLOSE
    exact = (Decimal("0.050") * 84210 + Decimal("0.003") * 84200) / Decimal("0.053")
    assert half["price"] == "84209.43396226" and abs(Decimal(half["price"]) - exact) < Decimal("1e-8")


# ------------------------------------------------------------------- reading the files

def test_reloading_gives_the_same_rows_and_a_repeated_trade_counts_once(tmp_path: Path) -> None:
    lines, trades = _sequence()
    mirror = tmp_path / "events.jsonl"
    mirror.write_text("".join(json.dumps(line) + "\n" for line in lines))
    cache = _cache(tmp_path / "p.json", trades + [trades[0]])  # trade 11 written twice
    first = live_executions_from_file(mirror, symbol="BTCUSDT", account="acct", trades_path=cache)
    second = live_executions_from_file(mirror, symbol="BTCUSDT", account="acct", trades_path=cache)
    assert first == second and len(first) == 4
    assert len({r["client_order_id"] for r in first}) == 4
    assert first[0]["price"] == "84000"


def test_a_missing_or_broken_cache_is_no_trades(tmp_path: Path) -> None:
    assert user_trades_by_order(None) == {}
    assert user_trades_by_order(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{not json")
    assert user_trades_by_order(tmp_path / "bad.json") == {}


def test_the_route_prices_from_the_cache_without_a_signed_request(live, monkeypatch,  # noqa: F811
                                                                    tmp_path: Path) -> None:
    client, adapter, fake, mirror = live
    lines, trades = _sequence()
    mirror.path.parent.mkdir(parents=True, exist_ok=True)
    with mirror.path.open("a") as handle:
        for line in lines:
            handle.write(json.dumps({**line, "account": mirror.account_fingerprint}) + "\n")
    performance = ManualLivePerformance(reader=adapter.reader,
                                        path=_cache(tmp_path / "perf.json", trades),
                                        krw_rate=lambda: None)
    monkeypatch.setattr(live_routes.live_runtime, "performance", performance)
    before = len(fake.calls)
    body = client.get("/api/crypto/binance/executions?symbol=BTCUSDT").json()
    assert [r["event_type"] for r in body["executions"]] == [
        INITIAL_ENTRY, ADD_ENTRY, PARTIAL_CLOSE, FULL_CLOSE]
    assert len(fake.calls) == before

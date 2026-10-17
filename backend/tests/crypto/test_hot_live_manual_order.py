"""HOT LIVE manual order unlock V1: the HOT symbol trades on the same manual contract as BTC.

What is proved here, against the deployed routes and a per-symbol fake exchange:

* armed, the HOT symbol takes a manual LONG and SHORT; disarmed, it sends nothing;
* the HOT order passes through `preflight_open`, Safe MAX and the exchange filters exactly as
  BTC's does, and an order those refuse never reaches Binance;
* a held HOT position can be added to, reduced reduceOnly by the opposite side, and refused a
  flip; HALF and FULL CLOSE are reduceOnly;
* AUTO stays on the default symbol: the exit guard refuses the HOT symbol, and nothing in the
  HOT modules can send an order;
* the screen's gates carry no HOT-specific switch, and BTC, ETH and SOL are unchanged.

No test touches the network.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.crypto import symbols as symbols_module
from app.crypto.live.leverage import LeverageCapability
from app.crypto.live.mirror import LiveMirror
from app.crypto.symbols import DEFAULT_SYMBOL, SUPPORTED_SYMBOLS, admit_dynamic
from tests.crypto import test_crypto_multisymbol as ms

HOT = "AGTUSDT"
ORDER_PATH = ("POST", "/fapi/v1/order")


@pytest.fixture(autouse=True)
def hot_symbol(monkeypatch):
    monkeypatch.setattr(symbols_module, "_dynamic", set())
    monkeypatch.setitem(ms.SPECS, HOT, {"base": "AGT", "tick": "0.0000100", "step": "1",
                                        "min_qty": "1", "market_max": "800000", "notional": "5",
                                        "price": "0.1200000", "qty_precision": 0,
                                        "price_precision": 7})
    admit_dynamic(HOT)


@pytest.fixture
def terminal(monkeypatch, tmp_path: Path):
    """The deployed app with BTC/ETH/SOL and the HOT symbol on one shared client, built the way
    `LiveRuntime._build_symbol` builds each of them. `armed` picks the trading gate."""
    from app.crypto.terminal import live_routes
    from app.crypto.terminal.server import app

    def build(*, armed: bool, hot_rows: list[dict[str, Any]] | None = None) -> tuple[Any, Any]:
        fake = ms.MultiSymbolBinance(extra_rows=hot_rows or [ms.flat(HOT)])
        client, config, fake = ms.make(fake, trading_enabled=armed)
        mirror = LiveMirror(path=tmp_path / "live" / "events.jsonl",
                            account_fingerprint=config.fingerprint)
        entries: dict[str, Any] = {}
        for symbol in (*SUPPORTED_SYMBOLS, HOT):
            capability = LeverageCapability(path=tmp_path / f"cap_{symbol}.json")
            adapter = ms.adapter_for(symbol, client, config, mirror=mirror, capability=capability)
            entries[symbol] = live_routes.SymbolRuntime(
                symbol=symbol, adapter=adapter, reader=adapter.reader, router=adapter.router,
                capability=capability, bootstrap=None, performance=None)
        runtime = live_routes.live_runtime
        monkeypatch.setattr(runtime, "symbols", entries)
        monkeypatch.setattr(runtime, "adapter", None)
        monkeypatch.setattr(runtime, "config", config)
        monkeypatch.setattr(runtime, "client", client)
        monkeypatch.setattr(runtime, "mirror", mirror)
        monkeypatch.setattr(runtime, "exit_guard", None)
        monkeypatch.setattr(runtime, "error", None)
        return TestClient(app), fake
    return build


def sent(fake: Any) -> list[dict[str, str]]:
    return [params for method, path, params in fake.calls if (method, path) == ORDER_PATH]


def order(client: Any, **body: Any) -> Any:
    return client.post("/api/crypto/binance/order", json={"symbol": HOT, **body})


# ------------------------------------------------------------------ open


@pytest.mark.parametrize("side, binance_side", [("LONG", "BUY"), ("SHORT", "SELL")])
def test_armed_hot_takes_a_manual_open_on_either_side(terminal, side, binance_side) -> None:
    client, fake = terminal(armed=True)
    response = order(client, side=side, intent="OPEN", qty="100")
    assert response.status_code == 200, response.json()
    assert response.json()["symbol"] == HOT
    [params] = sent(fake)
    assert (params["symbol"], params["side"], params["quantity"]) == (HOT, binance_side, "100")
    assert params.get("reduceOnly") in (None, "false")


def test_disarmed_hot_sends_nothing_and_names_the_arm_gate(terminal) -> None:
    client, fake = terminal(armed=False)
    for side in ("LONG", "SHORT"):
        response = order(client, side=side, intent="OPEN", qty="100")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    assert sent(fake) == []


def test_the_hot_open_runs_the_forced_preflight(terminal, monkeypatch) -> None:
    from app.crypto.terminal import live_routes

    client, fake = terminal(armed=True)
    adapter = live_routes.live_runtime.symbols[HOT].adapter
    calls: list[tuple[str, Any]] = []
    real = adapter.preflight_open

    def spy(side, qty=None, notional_usdt=None, **kw):
        calls.append((side, qty))
        return real(side, qty, notional_usdt, **kw)

    monkeypatch.setattr(adapter, "preflight_open", spy)
    assert order(client, side="LONG", intent="OPEN", qty="100").status_code == 200
    assert calls == [("LONG", "100")]


@pytest.mark.parametrize("qty, why", [
    ("10000000", "above Safe MAX / MARKET_LOT_SIZE"),
    ("10", "below MIN_NOTIONAL (10 x 0.12 = 1.2 USDT < 5)"),
    ("100.5", "off the stepSize grid"),
])
def test_safe_max_and_binance_filters_refuse_a_hot_open_before_it_is_sent(
        terminal, qty, why) -> None:
    client, fake = terminal(armed=True)
    response = order(client, side="LONG", intent="OPEN", qty=qty)
    assert response.status_code == 409, why
    assert sent(fake) == [], why


def test_a_hot_open_above_the_balance_reports_the_safe_max(terminal) -> None:
    client, fake = terminal(armed=True)
    response = order(client, side="LONG", intent="OPEN", qty="10000000")
    body = response.json()
    assert body["error"]["code"] == "ORDER_CAPACITY_CHANGED"
    safe_max = Decimal(str(body["safe_max_qty"]))
    assert safe_max > 0 and safe_max % 1 == 0          # on the HOT symbol's own step
    assert sent(fake) == []


# ------------------------------------------------------------------ held position


def test_a_held_hot_position_takes_an_additional_entry_on_its_side(terminal) -> None:
    for side, binance_side in (("LONG", "BUY"), ("SHORT", "SELL")):
        client, fake = terminal(armed=True, hot_rows=[ms.held(HOT, side, "500")])
        assert order(client, side=side, intent="OPEN", qty="100").status_code == 200
        [params] = sent(fake)
        assert params["side"] == binance_side and params.get("reduceOnly") in (None, "false")


def test_the_opposite_side_reduces_a_held_hot_position_and_never_flips_it(terminal) -> None:
    client, fake = terminal(armed=True, hot_rows=[ms.held(HOT, "LONG", "500")])
    assert order(client, side="SHORT", intent="OPEN", qty="200").status_code == 200
    [params] = sent(fake)
    assert (params["side"], params["quantity"], params["reduceOnly"]) == ("SELL", "200", "true")

    client, fake = terminal(armed=True, hot_rows=[ms.held(HOT, "LONG", "500")])
    response = order(client, side="SHORT", intent="OPEN", qty="800")
    assert response.status_code == 409
    assert sent(fake) == [], "a reduce larger than the position is a flip and is refused"


def test_hot_half_close_is_reduce_only(terminal) -> None:
    client, fake = terminal(armed=True, hot_rows=[ms.held(HOT, "SHORT", "500")])
    assert order(client, side="SHORT", intent="CLOSE", qty="250").status_code == 200
    [params] = sent(fake)
    assert (params["symbol"], params["side"], params["quantity"], params["reduceOnly"]) == \
        (HOT, "BUY", "250", "true")


def test_hot_full_close_is_reduce_only_for_the_held_size(terminal) -> None:
    client, fake = terminal(armed=True, hot_rows=[ms.held(HOT, "LONG", "500")])
    assert order(client, side="LONG", intent="CLOSE").status_code == 200
    [params] = sent(fake)
    assert (params["symbol"], params["side"], params["quantity"], params["reduceOnly"]) == \
        (HOT, "SELL", "500", "true")


def test_hot_close_is_refused_while_disarmed(terminal) -> None:
    client, fake = terminal(armed=False, hot_rows=[ms.held(HOT, "LONG", "500")])
    assert order(client, side="LONG", intent="CLOSE").status_code == 409
    assert sent(fake) == []


# ------------------------------------------------------------------ leverage and gates


def test_hot_leverage_write_names_the_hot_symbol_only(terminal) -> None:
    client, fake = terminal(armed=True)

    class Written(dict):
        """Reports 5x once Binance has been asked for it, as the real account would."""
        def get(self, symbol, default=None):
            written = any((m, p, q.get("symbol")) == ("POST", "/fapi/v1/leverage", symbol)
                          for m, p, q in fake.calls)
            return "5" if written else default

    fake.leverage = Written()
    response = client.post("/api/crypto/binance/leverage", json={"leverage": "5", "symbol": HOT})
    assert response.status_code == 200, response.json()
    writes = [params for method, path, params in fake.calls
              if (method, path) == ("POST", "/fapi/v1/leverage")]
    assert [(p["symbol"], p["leverage"]) for p in writes] == [(HOT, "5")]


def test_the_hot_screen_gets_the_same_gates_as_btc_with_no_hot_switch(terminal) -> None:
    for armed in (True, False):
        client, _ = terminal(armed=armed)
        hot = client.get("/api/crypto/binance/account", params={"symbol": HOT}).json()["gates"]
        btc = client.get("/api/crypto/binance/account", params={"symbol": "BTCUSDT"}).json()["gates"]
        assert hot == btc and hot["armed"] is armed
        assert "symbol_orders_enabled" not in hot and "symbol_orders_flag_name" not in hot
        status = client.get("/api/crypto/binance/status", params={"symbol": HOT}).json()["gates"]
        assert "symbol_orders_enabled" not in status


def test_the_hot_switch_is_gone_from_the_server() -> None:
    from app.crypto.terminal import live_routes
    source = inspect.getsource(live_routes)
    assert "BINANCE_LIVE_HOT_ORDERS" not in source and "HOT_LIVE_ORDERS_DISABLED" not in source


# ------------------------------------------------------------------ AUTO stays off


def test_auto_exit_refuses_the_hot_symbol(terminal, monkeypatch) -> None:
    from app.crypto.terminal import live_routes

    client, fake = terminal(armed=True)
    entry = live_routes.live_runtime.symbols[DEFAULT_SYMBOL]

    class Guard:
        adapter = entry.adapter

        def view(self) -> dict[str, Any]:
            return {"state": "OFF", "enabled": False, "symbol": None}

    monkeypatch.setattr(live_routes.live_runtime, "exit_guard", Guard())
    view = client.get("/api/crypto/binance/exit-guard", params={"symbol": HOT}).json()
    assert view["available"] is False and view["unavailable_reason"] == "AUTO_SINGLE_SYMBOL"
    refused = client.post("/api/crypto/binance/exit-guard",
                          json={"take_profit_krw": "10000", "stop_loss_krw": "-10000",
                                "symbol": HOT})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "AUTO_SINGLE_SYMBOL"
    assert sent(fake) == []


def test_reading_the_hot_symbol_sends_no_order(terminal) -> None:
    """Only a POST /order sends one. Every read the screen makes on the HOT tab sends none."""
    client, fake = terminal(armed=True, hot_rows=[ms.held(HOT, "LONG", "500")])
    for path in ("/api/crypto/binance/status", "/api/crypto/binance/account",
                 "/api/crypto/binance/sizing", "/api/crypto/binance/position",
                 "/api/crypto/binance/leverage", "/api/crypto/binance/exit-guard"):
        client.get(path, params={"symbol": HOT})
    client.get("/api/crypto/binance/preview", params={"symbol": HOT, "side": "LONG", "qty": "100"})
    assert sent(fake) == []


def test_no_hot_module_can_send_an_order() -> None:
    from app.crypto.hot_coins import lock, market, scanner, service, slot
    from app.crypto.terminal import hot_routes
    for module in (lock, market, scanner, service, slot, hot_routes):
        source = inspect.getsource(module)
        for call in ("new_order", "router.submit", "submit_close_only", "place_order",
                     "set_leverage", "/fapi/v1/order"):
            assert call not in source, f"{module.__name__} mentions {call}"


# ------------------------------------------------------------------ BTC/ETH/SOL unchanged


@pytest.mark.parametrize("symbol, qty", [("BTCUSDT", "0.002"), ("ETHUSDT", "0.010"),
                                         ("SOLUSDT", "1")])
def test_btc_eth_sol_manual_orders_are_unchanged(terminal, symbol, qty) -> None:
    client, fake = terminal(armed=True)
    response = client.post("/api/crypto/binance/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": qty, "symbol": symbol})
    assert response.status_code == 200, response.json()
    [params] = sent(fake)
    assert (params["symbol"], params["side"]) == (symbol, "BUY")

    client, fake = terminal(armed=False)
    response = client.post("/api/crypto/binance/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": qty, "symbol": symbol})
    assert response.json()["error"]["code"] == "LIVE_TRADING_DISABLED" and sent(fake) == []

"""LIVE symbol leverage cap V1: each symbol's own Binance bracket table bounds its leverage.

Against the deployed routes and a fake exchange that serves a different bracket table per
symbol. What is proved, each against the failure it would have:

* the offered leverage is the symbol's own table, never another symbol's and never "HOT";
* a setting above the symbol's maximum is reported, never offered, never lowered by the server,
  and an opening order at it is refused before anything is sent;
* Safe MAX is priced at the leverage the operator chose, and a position past what that leverage
  allows (the resulting position's tier) is refused locally;
* closes - HALF and FULL, reduceOnly - are not held back by an invalid setting;
* BTC, ETH and SOL are unchanged, and PAPER does not use any of this.

No test touches the network; no real order exists.
"""
from __future__ import annotations

import copy
import inspect
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto import symbols as symbols_module
from app.crypto.live.brackets import LEVERAGE_INVALID_FOR_SYMBOL, LEVERAGE_TIER_EXCEEDED
from app.crypto.live.leverage import LeverageCapability
from app.crypto.live.mirror import LiveMirror
from app.crypto.symbols import SUPPORTED_SYMBOLS, admit_dynamic
from tests.crypto import test_crypto_multisymbol as ms
from tests.crypto.binance_fixtures import LEVERAGE_BRACKET

D = Decimal
SIREN, AGT, WIDE = "SIRENUSDT", "AGTUSDT", "ZEREBROUSDT"
ORDER = ("POST", "/fapi/v1/order")
LEVERAGE_WRITE = ("POST", "/fapi/v1/leverage")


def table(*rows: tuple[int, int, int]) -> list[dict[str, Any]]:
    """rows: (floor, cap, initialLeverage)"""
    return [{"bracket": i + 1, "initialLeverage": lev, "notionalFloor": floor, "notionalCap": cap,
             "maintMarginRatio": 0.01 * (i + 1), "cum": 0} for i, (floor, cap, lev) in enumerate(rows)]


#: SIRENUSDT's table as Binance served it on 2026-10-17 (the first four bands).
SIREN_TABLE = table((0, 10000, 10), (10000, 60000, 5), (60000, 70000, 4), (70000, 250000, 3))
TABLES = {
    SIREN: SIREN_TABLE,
    AGT: table((0, 5000, 20), (5000, 50000, 10), (50000, 250000, 5)),
    WIDE: table((0, 5000, 50), (5000, 50000, 20), (50000, 250000, 10)),
    "BTCUSDT": table((0, 300000, 20), (300000, 800000, 10)),
}


@pytest.fixture(autouse=True)
def hot_symbols(monkeypatch):
    monkeypatch.setattr(symbols_module, "_dynamic", set())
    for symbol, base in ((SIREN, "SIREN"), (AGT, "AGT"), (WIDE, "ZEREBRO")):
        monkeypatch.setitem(ms.SPECS, symbol, {"base": base, "tick": "0.0000100", "step": "1",
                                               "min_qty": "1", "market_max": "5000000",
                                               "notional": "5", "price": "0.0306000",
                                               "qty_precision": 0, "price_precision": 7})
        admit_dynamic(symbol)


class PerSymbolBrackets(ms.MultiSymbolBinance):
    """`leverageBracket` answers each symbol with its own table; ETH and SOL keep the fixture's."""

    def transport(self) -> httpx.MockTransport:
        inner = super().transport()

        def handler(request: httpx.Request) -> httpx.Response:
            symbol = request.url.params.get("symbol")
            if request.url.path == "/fapi/v1/leverageBracket" and symbol in TABLES:
                self.calls.append((request.method, request.url.path, dict(request.url.params)))
                return httpx.Response(200, json=[{"symbol": symbol, "brackets": TABLES[symbol]}])
            return inner.handle_request(request)
        return httpx.MockTransport(handler)


@pytest.fixture
def terminal(monkeypatch, tmp_path: Path):
    from app.crypto.terminal import live_routes
    from app.crypto.terminal.server import app

    def build(*, armed: bool = True, leverage: dict[str, str] | None = None,
              rows: list[dict[str, Any]] | None = None, available: str = "147.78458089"):
        fake = PerSymbolBrackets(extra_rows=rows or [ms.flat(s) for s in (SIREN, AGT, WIDE)],
                                 available=available, leverage=leverage or {})
        client, config, fake = ms.make(fake, trading_enabled=armed)
        mirror = LiveMirror(path=tmp_path / "live" / "events.jsonl",
                            account_fingerprint=config.fingerprint)
        entries: dict[str, Any] = {}
        for symbol in (*SUPPORTED_SYMBOLS, SIREN, AGT, WIDE):
            capability = LeverageCapability(path=tmp_path / f"cap_{symbol}.json")
            adapter = ms.adapter_for(symbol, client, config, mirror=mirror, capability=capability)
            entries[symbol] = live_routes.SymbolRuntime(
                symbol=symbol, adapter=adapter, reader=adapter.reader, router=adapter.router,
                capability=capability, bootstrap=None, performance=None)
        runtime = live_routes.live_runtime
        for name, value in (("symbols", entries), ("adapter", None), ("config", config),
                            ("client", client), ("mirror", mirror), ("exit_guard", None),
                            ("error", None)):
            monkeypatch.setattr(runtime, name, value)
        return TestClient(app), fake
    return build


def calls(fake: Any, key: tuple[str, str]) -> list[dict[str, str]]:
    return [params for method, path, params in fake.calls if (method, path) == key]


def options(client: Any, symbol: str) -> dict[str, Any]:
    body = client.get("/api/crypto/binance/leverage", params={"symbol": symbol}).json()
    assert body["symbol"] == symbol
    return body


def sizing(client: Any, symbol: str, side: str = "LONG") -> dict[str, Any]:
    body = client.get("/api/crypto/binance/sizing", params={"symbol": symbol}).json()
    assert body["symbol"] == symbol
    return body["sides"][side]


def order(client: Any, symbol: str, **body: Any) -> Any:
    return client.post("/api/crypto/binance/order", json={"symbol": symbol, **body})


# ------------------------------------------------------------------ 1-2. the offered leverage


def test_btc_max_20_offers_1_to_20(terminal) -> None:
    client, _ = terminal()
    body = options(client, "BTCUSDT")
    assert body["max_leverage"] == 20 and body["options"] == [1, 2, 3, 5, 10, 20]
    assert body["current_valid"] is True and body["candidates"] == [1, 2, 3, 5, 10, 20, 50, 100]


def test_siren_max_10_offers_1_to_10_only(terminal) -> None:
    client, _ = terminal(leverage={SIREN: "10"})
    body = options(client, SIREN)
    assert body["max_leverage"] == 10 and body["options"] == [1, 2, 3, 5, 10]
    assert not {20, 50, 100} & set(body["options"])
    assert D(body["current"]) == 10 and body["current_valid"] is True


# ------------------------------------------------------------------ 3. the SIREN case


def test_a_20x_setting_on_siren_is_reported_never_offered_and_never_lowered(terminal) -> None:
    client, fake = terminal(leverage={SIREN: "20"})
    body = options(client, SIREN)
    assert D(body["current"]) == 20 and body["current_valid"] is False
    assert 20 not in body["options"], "the setting Binance refuses must not be a button"
    assert body["current_invalid_message"] == "현재 심볼(SIRENUSDT)은 최대 10x입니다. 레버리지를 다시 선택하세요."
    side = sizing(client, SIREN)
    assert D(side["max_qty"]) == 0 and side["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL
    assert all(D(row["qty"]) == 0 and not row["feasible"] for row in side["presets"])
    for qty in ("48057", "23994", "200"):          # what was really sent, and a small one
        response = order(client, SIREN, side="LONG", intent="OPEN", qty=qty)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == LEVERAGE_INVALID_FOR_SYMBOL
    assert calls(fake, ORDER) == [], "nothing reaches Binance"
    assert calls(fake, LEVERAGE_WRITE) == [], "the server never changes the leverage itself"


# ------------------------------------------------------------------ 4-5. the operator chooses


def test_at_10x_siren_sizes_and_the_order_passes_preflight(terminal) -> None:
    client, fake = terminal(leverage={SIREN: "10"})
    side = sizing(client, SIREN)
    assert side["max_feasible"] and D(side["leverage"]) == 10
    max_qty = D(str(side["max_qty"]))
    assert D(1300) < max_qty * D("0.0306") <= D(1478)        # ~147.78 USDT x 10, less fees
    response = order(client, SIREN, side="LONG", intent="OPEN", qty=format(max_qty, "f"))
    assert response.status_code == 200, response.json()
    assert [p["quantity"] for p in calls(fake, ORDER)] == [format(max_qty, "f")]


def test_at_5x_safe_max_is_priced_at_5x(terminal) -> None:
    client, _ = terminal(leverage={SIREN: "5"})
    five = sizing(client, SIREN)
    client, _ = terminal(leverage={SIREN: "10"})
    ten = sizing(client, SIREN)
    assert D(five["leverage"]) == 5 and D(ten["leverage"]) == 10
    ratio = D(str(ten["max_qty"])) / D(str(five["max_qty"]))
    assert D("1.95") < ratio < D("2.05")
    row = next(r for r in five["presets"] if r["label"] == "MAX")
    assert D(str(row["required_margin"])) == D(str(row["notional"])) / 5


# ------------------------------------------------------------------ 6-7. symbol switch


def test_agt_then_siren_then_another_hot_each_use_their_own_table(terminal) -> None:
    client, fake = terminal(leverage={AGT: "20", SIREN: "20", WIDE: "20"})
    agt, siren, wide = options(client, AGT), options(client, SIREN), options(client, WIDE)
    assert (agt["max_leverage"], agt["current_valid"]) == (20, True)
    assert (siren["max_leverage"], siren["current_valid"]) == (10, False)
    assert (wide["max_leverage"], wide["options"]) == (50, [1, 2, 3, 5, 10, 20, 50])
    # Sizing reads each symbol's table through its own reader, so AGT's 20x cannot carry over.
    assert D(sizing(client, AGT)["bracket_max_leverage"]) == 20 and sizing(client, AGT)["max_feasible"]
    assert D(sizing(client, SIREN)["bracket_max_leverage"]) == 10
    assert sizing(client, SIREN)["reject_code"] == LEVERAGE_INVALID_FOR_SYMBOL
    assert D(sizing(client, WIDE)["bracket_max_leverage"]) == 50 and sizing(client, WIDE)["max_feasible"]
    asked = {params["symbol"] for params in calls(fake, ("GET", "/fapi/v1/leverageBracket"))}
    assert {AGT, SIREN, WIDE} <= asked


# ------------------------------------------------------------------ 8. the resulting position's tier


def test_an_add_is_judged_by_the_tier_of_the_whole_resulting_position(terminal) -> None:
    held = [ms.held(SIREN, "LONG", "300000")]                 # ~9,180 USDT held at 0.0306
    client, fake = terminal(leverage={SIREN: "10"}, rows=held, available="10000")
    # +40,000 SIREN (~1,224) would leave ~10,404: past the 10,000 that 10x allows.
    response = order(client, SIREN, side="LONG", intent="OPEN", qty="40000")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == LEVERAGE_TIER_EXCEEDED
    assert calls(fake, ORDER) == []
    # +20,000 (~612) leaves ~9,792: inside band 1, sent.
    assert order(client, SIREN, side="LONG", intent="OPEN", qty="20000").status_code == 200
    assert D(sizing(client, SIREN)["max_qty"]) < 30000
    # At 5x the same add lands in band 2 (5x up to 60,000) and is allowed.
    client, fake = terminal(leverage={SIREN: "5"}, rows=held, available="10000")
    assert order(client, SIREN, side="LONG", intent="OPEN", qty="40000").status_code == 200


# ------------------------------------------------------------------ 9-10. closes are not held back


def test_half_and_full_close_go_out_reduce_only_at_an_invalid_setting(terminal) -> None:
    held = [ms.held(SIREN, "LONG", "47015")]
    client, fake = terminal(leverage={SIREN: "20"}, rows=held)
    assert options(client, SIREN)["current_valid"] is False
    half = order(client, SIREN, side="LONG", intent="CLOSE", qty="23507")
    assert half.status_code == 200, half.json()
    full = order(client, SIREN, side="LONG", intent="CLOSE")
    assert full.status_code == 200, full.json()
    reduce = order(client, SIREN, side="SHORT", intent="OPEN", qty="1000")   # opposite = reduce
    assert reduce.status_code == 200, reduce.json()
    sent = calls(fake, ORDER)
    assert [(p["side"], p["quantity"], p["reduceOnly"]) for p in sent] == [
        ("SELL", "23507", "true"), ("SELL", "47015", "true"), ("SELL", "1000", "true")]
    # The same account still may not add.
    assert order(client, SIREN, side="LONG", intent="OPEN", qty="1000").status_code == 409
    assert calls(fake, LEVERAGE_WRITE) == []


# ------------------------------------------------------------------ 11-12. unchanged elsewhere


@pytest.mark.parametrize("symbol, qty", [("BTCUSDT", "0.002"), ("ETHUSDT", "0.010"), ("SOLUSDT", "1")])
def test_btc_eth_sol_orders_are_unchanged(terminal, symbol, qty) -> None:
    client, fake = terminal()
    body = options(client, symbol)
    assert body["current_valid"] is True and 20 in body["options"]
    side = sizing(client, symbol)
    assert side["max_feasible"] and D(side["leverage"]) == 20
    assert order(client, symbol, side="LONG", intent="OPEN", qty=qty).status_code == 200
    assert [p["symbol"] for p in calls(fake, ORDER)] == [symbol]


def test_eth_and_sol_keep_the_fixture_table() -> None:
    assert LEVERAGE_BRACKET[0]["brackets"][0]["initialLeverage"] >= 20
    assert "ETHUSDT" not in TABLES and "SOLUSDT" not in TABLES


def test_paper_does_not_use_the_live_bracket_rule() -> None:
    import app.crypto.paper.engine as engine
    import app.crypto.paper.sizing as paper_sizing
    import app.crypto.terminal.api as paper_api
    for module in (engine, paper_sizing, paper_api):
        source = inspect.getsource(module)
        assert "live.brackets" not in source and "LEVERAGE_INVALID_FOR_SYMBOL" not in source


def test_the_cap_reads_nothing_new_from_binance(terminal) -> None:
    """Reading the screen at an invalid setting makes the same requests as at a valid one."""
    client, fake = terminal(leverage={SIREN: "20"})
    options(client, SIREN), sizing(client, SIREN)
    invalid = sorted({path for _, path, _ in fake.calls})
    client, fake = terminal(leverage={SIREN: "10"})
    options(client, SIREN), sizing(client, SIREN)
    assert invalid == sorted({path for _, path, _ in fake.calls})

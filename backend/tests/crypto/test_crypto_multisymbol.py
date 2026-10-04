"""Manual LIVE and PAPER across BTCUSDT, ETHUSDT and SOLUSDT.

The existing `test_binance_live*` files prove the single-symbol account path. These prove the
part that only exists once there are three, and the tests are written against the failure each
one would actually have:

* a symbol outside the whitelist is refused before anything is read or sent,
* a figure read for one symbol cannot reach another symbol's screen,
* an order cannot be built unless every symbol on its path agrees,
* one symbol's learned leverage restriction is not another's, while an account-wide restriction
  Binance stated is reported on all of them with its provenance,
* Safe MAX on one symbol is bounded by the balance the other symbols' positions have left,
* AUTO and C1, which are deliberately single-symbol, refuse rather than answer.

The fake transport here answers *per symbol*, unlike `binance_fixtures.FakeBinance`, which
serves BTCUSDT whatever it is asked. That is the point: a code path that reads the wrong symbol
gets the wrong instrument back and the assertion fails, rather than quietly passing because
every answer was the same.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto.live.account import AccountReader
from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.credentials import Credentials, load_config
from app.crypto.live.filters import SymbolFilters
from app.crypto.live.leverage import (AccountScopeAdvisory, LeverageCapability,
                                      account_scope_from_mirror, is_account_scoped)
from app.crypto.live.mirror import LiveEvent, LiveMirror
from app.crypto.live.orders import CLOSE, OPEN, SYMBOL_MISMATCH, LiveOrderRouter, OrderRefused
from app.crypto.live.rest import BinanceFuturesClient
from app.crypto.symbols import (DEFAULT_SYMBOL, SUPPORTED_SYMBOLS, SymbolNotSupported,
                                base_asset, resolve)
from tests.crypto.binance_fixtures import (ACCOUNT, DEPTH, LEVERAGE_BRACKET, SERVER_TIME,
                                           USER_TRADES, INCOME_FUNDING, LISTEN_KEY,
                                           NEW_ORDER_RESULT, POSITION_MODE_ONE_WAY, SET_LEVERAGE)

#: Binance's own numbers for the three instruments, as `exchangeInfo` publishes them. The
#: quantity steps differ by two orders of magnitude between BTC and SOL, which is why a test
#: that serves one symbol's filters for another would be proving nothing.
SPECS = {
    "BTCUSDT": {"base": "BTC", "tick": "0.10", "step": "0.001", "min_qty": "0.001",
                "market_max": "120", "notional": "100", "price": "83500.00",
                "qty_precision": 3, "price_precision": 2},
    "ETHUSDT": {"base": "ETH", "tick": "0.01", "step": "0.001", "min_qty": "0.001",
                "market_max": "2000", "notional": "20", "price": "3120.00",
                "qty_precision": 3, "price_precision": 2},
    "SOLUSDT": {"base": "SOL", "tick": "0.0100", "step": "1", "min_qty": "1",
                "market_max": "20000", "notional": "5", "price": "165.4300",
                "qty_precision": 0, "price_precision": 4},
}


def exchange_info(symbol: str) -> dict[str, Any]:
    spec = SPECS[symbol]
    return {"timezone": "UTC", "serverTime": 1_790_000_000_000, "symbols": [{
        "symbol": symbol, "pair": symbol, "contractType": "PERPETUAL", "status": "TRADING",
        "baseAsset": spec["base"], "quoteAsset": "USDT", "marginAsset": "USDT",
        "pricePrecision": spec["price_precision"], "quantityPrecision": spec["qty_precision"],
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": spec["tick"], "minPrice": "0.01",
             "maxPrice": "4529764"},
            {"filterType": "LOT_SIZE", "stepSize": spec["step"], "minQty": spec["min_qty"],
             "maxQty": "1000000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": spec["step"],
             "minQty": spec["min_qty"], "maxQty": spec["market_max"]},
            {"filterType": "MIN_NOTIONAL", "notional": spec["notional"]},
        ]}]}


def mark(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "markPrice": SPECS[symbol]["price"],
            "indexPrice": SPECS[symbol]["price"], "estimatedSettlePrice": SPECS[symbol]["price"],
            "lastFundingRate": "0.00010000", "interestRate": "0.00010000",
            "nextFundingTime": 1_790_640_000_000, "time": 1_790_639_000_000}


def book(symbol: str) -> dict[str, Any]:
    price = Decimal(SPECS[symbol]["price"])
    tick = Decimal(SPECS[symbol]["tick"])
    return {"symbol": symbol, "bidPrice": str(price - tick), "bidQty": "10",
            "askPrice": str(price + tick), "askQty": "10", "time": 1_790_639_000_001,
            "lastUpdateId": 1}


def depth(symbol: str) -> dict[str, Any]:
    price = Decimal(SPECS[symbol]["price"])
    tick = Decimal(SPECS[symbol]["tick"])
    return {"lastUpdateId": 1, "E": 1, "T": 1,
            "bids": [[str(price - tick * (i + 1)), "100000"] for i in range(10)],
            "asks": [[str(price + tick * (i + 1)), "100000"] for i in range(10)]}


def price_of(symbol: str) -> str:
    """A price for any symbol, including one this terminal does not trade.

    Off-list symbols appear in `positionRisk` because the account is one wallet and the operator
    can open a position in the Binance app, so the fixtures have to be able to build such a row.
    """
    spec = SPECS.get(symbol)
    return spec["price"] if spec else "1.0000"


def flat(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "positionSide": "BOTH", "positionAmt": "0", "entryPrice": "0",
            "breakEvenPrice": "0", "markPrice": price_of(symbol), "unRealizedProfit": "0",
            "liquidationPrice": "0", "isolatedMargin": "0", "notional": "0",
            "initialMargin": "0", "maintMargin": "0", "adl": 0, "updateTime": 1}


def held(symbol: str, side: str, qty: str) -> dict[str, Any]:
    amount = qty if side == "LONG" else f"-{qty}"
    price = price_of(symbol)
    return {**flat(symbol), "positionAmt": amount, "entryPrice": price,
            "breakEvenPrice": price, "unRealizedProfit": "1.25",
            "notional": str(Decimal(qty) * Decimal(price)), "initialMargin": "10",
            "maintMargin": "1"}


class MultiSymbolBinance:
    """A transport that is honest about which symbol it was asked for.

    `positionRisk` with no symbol returns every row, which is what the real V3 endpoint does and
    what the all-symbol summary route depends on.
    """

    def __init__(self, *, positions: dict[str, dict[str, Any]] | None = None,
                 available: str = "874.47500000", extra_rows: list[dict[str, Any]] | None = None,
                 leverage: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.positions = positions or {}
        self.available = available
        self.extra_rows = extra_rows or []
        self.leverage = leverage or {}
        self.errors: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}

    def fail(self, method: str, path: str, status: int, code: int, msg: str) -> None:
        self.errors[(method, path)] = (status, {"code": code, "msg": msg})

    def count(self, path: str, symbol: str | None = None) -> int:
        return sum(1 for _, called, params in self.calls
                   if called == path and (symbol is None or params.get("symbol") == symbol))

    def _position_rows(self, symbol: str | None) -> list[dict[str, Any]]:
        rows = [self.positions.get(name) or flat(name) for name in SUPPORTED_SYMBOLS]
        rows.extend(self.extra_rows)
        if symbol is None:
            return rows
        return [row for row in rows if row.get("symbol") == symbol]

    def _account(self) -> dict[str, Any]:
        payload = copy.deepcopy(ACCOUNT)
        payload["availableBalance"] = self.available
        for row in payload["assets"]:
            if row.get("asset") == "USDT":
                row["availableBalance"] = self.available
        return payload

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            parsed = urlparse(str(request.url))
            params = dict(parse_qsl(parsed.query))
            path = parsed.path
            self.calls.append((request.method, path, params))
            key = (request.method, path)
            if key in self.errors:
                status, body = self.errors[key]
                return httpx.Response(status, json=body)
            symbol = params.get("symbol")
            if path == "/fapi/v1/time":
                return httpx.Response(200, json=SERVER_TIME)
            if path == "/fapi/v1/exchangeInfo":
                if symbol not in SPECS:
                    return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
                return httpx.Response(200, json=exchange_info(symbol))
            if path == "/fapi/v1/premiumIndex":
                return httpx.Response(200, json=mark(symbol))
            if path == "/fapi/v1/ticker/bookTicker":
                return httpx.Response(200, json=book(symbol))
            if path == "/fapi/v1/depth":
                return httpx.Response(200, json=depth(symbol))
            if path == "/fapi/v3/account":
                return httpx.Response(200, json=self._account())
            if path == "/fapi/v3/positionRisk":
                return httpx.Response(200, json=self._position_rows(symbol))
            if path == "/fapi/v1/symbolConfig":
                return httpx.Response(200, json=[{
                    "symbol": symbol, "marginType": "CROSSED", "isAutoAddMargin": "false",
                    "leverage": self.leverage.get(symbol, "20"),
                    "maxNotionalValue": "20000000"}])
            if path == "/fapi/v1/positionSide/dual":
                return httpx.Response(200, json=POSITION_MODE_ONE_WAY)
            if path == "/fapi/v1/commissionRate":
                return httpx.Response(200, json={"symbol": symbol,
                                                 "makerCommissionRate": "0.000200",
                                                 "takerCommissionRate": "0.000500"})
            if path == "/fapi/v1/leverageBracket":
                row = copy.deepcopy(LEVERAGE_BRACKET[0])
                row["symbol"] = symbol
                return httpx.Response(200, json=[row])
            if path == "/fapi/v1/userTrades":
                return httpx.Response(200, json=[{**row, "symbol": symbol} for row in USER_TRADES])
            if path == "/fapi/v1/income":
                return httpx.Response(200, json=[{**row, "symbol": symbol}
                                                 for row in INCOME_FUNDING])
            if path == "/fapi/v1/listenKey":
                return httpx.Response(200, json=LISTEN_KEY if request.method == "POST" else {})
            if path == "/fapi/v1/order":
                return httpx.Response(200, json={**NEW_ORDER_RESULT, "symbol": symbol})
            if path == "/fapi/v1/leverage":
                return httpx.Response(200, json={**SET_LEVERAGE, "symbol": symbol})
            return httpx.Response(404, json={"code": -1121, "msg": "Invalid symbol."})
        return httpx.MockTransport(handler)


def make(fake: MultiSymbolBinance | None = None, *, trading_enabled: bool = False) -> Any:
    fake = fake or MultiSymbolBinance()
    client = BinanceFuturesClient(
        credentials=Credentials(api_key="test-api-key-0123456789",
                                api_secret="test-api-secret-abcdef"),
        base_url="https://fapi.binance.com", recv_window_ms=5_000,
        trading_enabled=trading_enabled, transport=fake.transport())
    config = load_config({"BINANCE_API_KEY": "test-api-key-0123456789",
                          "BINANCE_API_SECRET": "test-api-secret-abcdef",
                          "BINANCE_LIVE_TRADING_ENABLED": "true" if trading_enabled else "false",
                          "BINANCE_LIVE_CLIENT_ARMED": "true" if trading_enabled else "false"})
    return client, config, fake


def adapter_for(symbol: str, client: Any, config: Any, *, mirror: LiveMirror | None = None,
                capability: LeverageCapability | None = None) -> BinanceLiveAdapter:
    """One adapter for one symbol, assembled the way `LiveRuntime._build_symbol` assembles it."""
    scoped = config.for_symbol(symbol)
    reader = AccountReader(client, scoped)
    router = LiveOrderRouter(reader=reader, client=client, config=scoped, mirror=mirror,
                             capability=capability or LeverageCapability())
    return BinanceLiveAdapter(config=scoped, client=client, reader=reader, router=router,
                              mirror=mirror)


# ------------------------------------------------------------------ the whitelist


def test_the_whitelist_is_the_only_gate_on_which_symbols_exist() -> None:
    assert SUPPORTED_SYMBOLS == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert DEFAULT_SYMBOL == "BTCUSDT", "every route's no-symbol answer must stay BTCUSDT"
    for symbol in SUPPORTED_SYMBOLS:
        assert resolve(symbol) == symbol
    # Not chosen: an off-list symbol is refused, and so is anything that merely looks like one.
    for refused in ("XRPUSDT", "BTCUSD", "BTC", "BTCUSDT_241227", "btcusdt-perp", 7, [], {}):
        with pytest.raises(SymbolNotSupported):
            resolve(refused)
    # Absent means "the caller did not choose", which is the single-symbol path.
    assert resolve(None) == resolve("") == DEFAULT_SYMBOL
    assert (base_asset("BTCUSDT"), base_asset("SOLUSDT")) == ("BTC", "SOL")


# ------------------------------------------------------------------ per-symbol isolation


def test_each_symbols_reader_reads_its_own_instrument_and_cannot_be_repointed() -> None:
    client, config, fake = make()
    adapters = {symbol: adapter_for(symbol, client, config) for symbol in SUPPORTED_SYMBOLS}
    for symbol, adapter in adapters.items():
        snapshot = adapter.snapshot(force=True)
        assert snapshot.ready, [item.code for item in snapshot.blockers]
        assert snapshot.symbol == symbol
        assert snapshot.filters is not None and snapshot.filters.symbol == symbol
        assert snapshot.filters.base_asset == SPECS[symbol]["base"]
        # The number that matters: SOL's step is 1 and BTC's is 0.001 in this fixture. A reader
        # that answered with another symbol's filters would size an order a thousandfold wrong.
        assert snapshot.filters.market_qty_step == Decimal(SPECS[symbol]["step"])
        assert snapshot.mark is not None
        assert snapshot.mark.mark_price == Decimal(SPECS[symbol]["price"])
    # The config a reader holds is frozen, so nothing can re-point a built reader afterwards.
    with pytest.raises(Exception):
        adapters["ETHUSDT"].config.symbol = "BTCUSDT"  # type: ignore[misc]
    assert adapters["ETHUSDT"].reader.symbol == "ETHUSDT"


def test_one_symbols_snapshot_cache_is_not_another_symbols() -> None:
    """Three adapters, three caches. A shared cache would serve BTC's position on the ETH tab."""
    client, config, fake = make(MultiSymbolBinance(positions={
        "BTCUSDT": held("BTCUSDT", "LONG", "0.058"),
        "SOLUSDT": held("SOLUSDT", "SHORT", "12"),
    }))
    btc = adapter_for("BTCUSDT", client, config)
    eth = adapter_for("ETHUSDT", client, config)
    sol = adapter_for("SOLUSDT", client, config)
    assert btc.get_position()["side"] == "LONG"
    assert btc.get_position()["qty"] == Decimal("0.058")
    # ETH is flat and must read flat even though BTC's snapshot was taken first.
    assert eth.get_position()["is_flat"] is True
    assert sol.get_position()["side"] == "SHORT"
    assert sol.get_position()["qty"] == Decimal("12")
    # And each one asked Binance about itself.
    for symbol in SUPPORTED_SYMBOLS:
        assert fake.count("/fapi/v3/positionRisk", symbol) >= 1


def test_a_stream_event_marks_every_symbol_not_just_whichever_polled_first() -> None:
    """The regression this guards is specific and was real.

    One user data socket serves the whole account. The adapter used to read
    `stream.telemetry.dirty`, so whichever symbol polled first called `clear_dirty()` and the
    others never learned that a fill had happened: the ETH screen would sit on a cached snapshot
    after an ETH fill because the BTC screen had consumed the flag. Each adapter now keeps its
    own.
    """
    class Telemetry:
        dirty = False

    class Stream:
        telemetry = Telemetry()

        def clear_dirty(self) -> None:
            Stream.telemetry.dirty = False

    client, config, fake = make()
    stream = Stream()
    adapters = [adapter_for(symbol, client, config) for symbol in SUPPORTED_SYMBOLS]
    for adapter in adapters:
        adapter.stream = stream
        adapter.snapshot(force=True)
    before = {id(a): fake.count("/fapi/v3/account") for a in adapters}
    assert all(not adapter.dirty for adapter in adapters)

    # One account event, fanned out the way `LiveRuntime._on_stream_change` fans it out.
    Stream.telemetry.dirty = True
    for adapter in adapters:
        adapter.on_stream_change("ACCOUNT_UPDATE")
    assert all(adapter.dirty for adapter in adapters)

    # The first adapter refreshes and clears the shared flag. The others must still be dirty.
    adapters[0].snapshot()
    assert Stream.telemetry.dirty is False
    assert adapters[0].dirty is False
    assert all(adapter.dirty for adapter in adapters[1:]), \
        "a sibling symbol lost its event to whichever adapter polled first"
    for adapter in adapters[1:]:
        adapter.snapshot()
        assert adapter.dirty is False
    assert before  # the counts above exist so a future change can tighten this


# ------------------------------------------------------------------ the order path


def test_an_order_is_refused_unless_every_symbol_on_its_path_agrees() -> None:
    """The one bug that costs money: an order built through another symbol's path.

    Nothing downstream would catch it. `exchangeInfo` for the wrong symbol yields a plausible
    step size, `positionRisk` for the wrong symbol yields a plausible flat position, and Binance
    fills the result.
    """
    client, config, fake = make(trading_enabled=True)
    eth = adapter_for("ETHUSDT", client, config)
    # Agreeing: the screen asked for the symbol this adapter is about.
    plan = eth.plan_order("LONG", OPEN, qty="0.010", symbol="ETHUSDT")
    assert plan.symbol == "ETHUSDT"
    # Disagreeing: the screen asked for BTCUSDT and got routed to the ETH adapter.
    with pytest.raises(OrderRefused) as caught:
        eth.plan_order("LONG", OPEN, qty="0.010", symbol="BTCUSDT")
    assert caught.value.code == SYMBOL_MISMATCH
    assert "ETHUSDT" in caught.value.message and "BTCUSDT" in caught.value.message
    # No symbol at all is the single-symbol path and is not a disagreement.
    assert eth.plan_order("LONG", OPEN, qty="0.010").symbol == "ETHUSDT"


def test_the_mismatch_refusal_is_recorded_in_the_audit_mirror(tmp_path: Path) -> None:
    client, config, fake = make(trading_enabled=True)
    mirror = LiveMirror(path=tmp_path / "events.jsonl", account_fingerprint="deadbeef")
    sol = adapter_for("SOLUSDT", client, config, mirror=mirror)
    with pytest.raises(OrderRefused):
        sol.plan_order("LONG", OPEN, qty="5", symbol="ETHUSDT")
    lines = [json.loads(line) for line in mirror.path.read_text().splitlines() if line.strip()]
    # Two lines, and both are wanted: the symbol check writes its own, and `plan`'s refusal
    # wrapper writes the generic one every refusal gets. Only the first names the symbols.
    named = [row for row in lines if row.get("stage") == "SYMBOL"]
    assert len(named) == 1, "a refused symbol must leave a trace, like every other refusal"
    assert named[0]["code"] == SYMBOL_MISMATCH
    assert named[0]["symbols"]["request"] == "ETHUSDT"
    assert named[0]["symbols"]["router_config"] == "SOLUSDT"
    assert named[0]["symbols"]["filters"] == "SOLUSDT"
    assert [row["stage"] for row in lines if row.get("code") == SYMBOL_MISMATCH] == \
        ["SYMBOL", "PLAN"]


def test_a_close_rereads_the_position_and_checks_that_symbol_too() -> None:
    """CLOSE sends the quantity Binance just reported, so that read is checked as well.

    An earlier snapshot agreeing is not enough: the re-read is the number that goes on the wire.
    """
    client, config, fake = make(MultiSymbolBinance(positions={
        "ETHUSDT": held("ETHUSDT", "LONG", "0.500")}), trading_enabled=True)
    eth = adapter_for("ETHUSDT", client, config)
    plan = eth.plan_order("", CLOSE, symbol="ETHUSDT")
    assert (plan.symbol, plan.qty, plan.reduce_only) == ("ETHUSDT", Decimal("0.500"), True)

    # Now make the re-read answer about a different instrument than the filters.
    class WrongSymbolReader:
        symbol = "ETHUSDT"

        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def position(self) -> Any:
            from app.crypto.live.models import LivePosition
            return LivePosition.from_rows([held("BTCUSDT", "LONG", "0.058")], "BTCUSDT")

    eth.router.reader = WrongSymbolReader(eth.router.reader)  # type: ignore[assignment]
    with pytest.raises(OrderRefused) as caught:
        eth.plan_order("", CLOSE, symbol="ETHUSDT")
    assert caught.value.code == SYMBOL_MISMATCH


def test_a_leverage_write_names_one_symbol_and_leaves_the_others_alone() -> None:
    client, config, fake = make(MultiSymbolBinance(
        leverage={"BTCUSDT": "20", "ETHUSDT": "20", "SOLUSDT": "20"}), trading_enabled=True)
    eth = adapter_for("ETHUSDT", client, config)
    # The fake echoes whatever symbol was asked, and `set_leverage` re-reads `symbolConfig`
    # before trusting the response, so this asserts the write went to ETHUSDT.
    writes = [params for method, path, params in fake.calls
              if (method, path) == ("POST", "/fapi/v1/leverage")]
    assert writes == []
    with pytest.raises(OrderRefused):
        # 20 is already set and the fixture keeps reporting 20, so a request for 10 fails the
        # confirmation read - which is the right outcome and is not what this test is about.
        eth.set_leverage("10")
    writes = [params for method, path, params in fake.calls
              if (method, path) == ("POST", "/fapi/v1/leverage")]
    assert writes and all(item.get("symbol") == "ETHUSDT" for item in writes), \
        "a leverage write must carry the symbol it is for and no other"


# ------------------------------------------------------------------ Safe MAX on one wallet


def test_safe_max_on_one_symbol_is_bounded_by_what_the_others_have_left() -> None:
    """The shared-wallet requirement, proved with Binance's own figure rather than a subtraction.

    `assets[USDT].availableBalance` is already net of every open position's initial margin, on
    every symbol. So a held BTC position shrinks the ETH ladder without the ETH path knowing
    anything about BTC, and the ladder is not recomputing a balance of its own.
    """
    rich = MultiSymbolBinance(available="10000.00000000")
    poor = MultiSymbolBinance(available="100.00000000",
                              positions={"BTCUSDT": held("BTCUSDT", "LONG", "0.058")})
    client_a, config_a, _ = make(rich)
    client_b, config_b, _ = make(poor)
    wide = adapter_for("ETHUSDT", client_a, config_a).get_sizing()
    narrow = adapter_for("ETHUSDT", client_b, config_b).get_sizing()
    assert wide["available"] and narrow["available"]
    assert wide["sides"]["LONG"]["available_balance"] == Decimal("10000.00000000")
    assert narrow["sides"]["LONG"]["available_balance"] == Decimal("100.00000000")
    assert wide["sides"]["LONG"]["max_qty"] > narrow["sides"]["LONG"]["max_qty"] > 0, \
        "the ETH ladder must shrink when the wallet has less left, with no BTC logic in it"
    # And the ladder is this symbol's grid, not another's.
    assert wide["sides"]["LONG"]["instrument"]["qty_step"] == Decimal(SPECS["ETHUSDT"]["step"])


def test_safe_max_respects_each_symbols_own_grid_and_minimum_notional() -> None:
    """The three instruments are not interchangeable, and the ladder must say so.

    Each symbol's grid is its own: ETH's step is three decimals of a coin worth ~3,100 USDT and
    SOL's is a whole coin worth ~165, so the smallest *orderable* size - the one that clears
    both the quantity minimum and MIN_NOTIONAL - needs a different amount of margin on each. A
    ladder that reused BTC's grid would offer sizes the exchange refuses.
    """
    def ladders(available: str) -> dict[str, Any]:
        client, config, _ = make(MultiSymbolBinance(available=available))
        return {symbol: adapter_for(symbol, client, config).get_sizing()["sides"]["LONG"]
                for symbol in SUPPORTED_SYMBOLS}

    wide = ladders("40.00000000")
    for symbol, row in wide.items():
        grid = row["instrument"]
        assert grid["qty_step"] == Decimal(SPECS[symbol]["step"])
        assert grid["min_notional"] == Decimal(SPECS[symbol]["notional"])
        assert row["max_feasible"] is True
        # The offered MAX is on this symbol's own step grid, which is the thing a shared grid
        # would get wrong: 3 SOL is a valid size and 3.000 BTC is not.
        assert row["max_qty"] % Decimal(SPECS[symbol]["step"]) == 0

    # Margin, not notional, is what the wallet has to cover, and the smallest orderable size
    # needs a different amount of it per symbol. At 2 USDT only ETHUSDT's smallest order fits,
    # and the other two are refused with a reason rather than offered a size.
    thin = ladders("2.00000000")
    assert thin["ETHUSDT"]["max_feasible"] is True
    assert thin["BTCUSDT"]["max_feasible"] is False and thin["BTCUSDT"]["max_qty"] == 0
    assert thin["SOLUSDT"]["max_feasible"] is False and thin["SOLUSDT"]["max_qty"] == 0
    assert thin["BTCUSDT"]["reject_code"] == "INSUFFICIENT_MARGIN"


# ------------------------------------------------------------------ leverage capability


REFUSAL = ("You can start trading with more than 20x leverage by 2026-10-29 01:34 (UTC), "
           "because higher leverage is available 30 days after Futures account registration.")
UNLOCK_MS = 1_793_237_640_000


def test_a_refusal_whose_reason_names_the_account_is_classified_as_account_wide() -> None:
    """Requirement: do not copy BTC's restriction to ETH without evidence. This is the evidence.

    Binance's sentence does not mention BTCUSDT. The reason it gives is the age of the Futures
    *account*, which is not a property an instrument has. So the limit is the account's, and
    reporting it on ETHUSDT is reading Binance's own words rather than copying BTC's state.

    The classifier is narrow on purpose: a symbol-specific or unrelated refusal must not
    qualify, because a false positive greys out steps Binance never spoke about.
    """
    assert is_account_scoped(REFUSAL) is True
    for other in ("Leverage reduction is not supported in Isolated Margin Mode",
                  "Position is open, cannot change leverage for this symbol",
                  "The symbol does not support 50x leverage",
                  "Insufficient account balance", ""):
        assert is_account_scoped(other) is False, other


def test_one_symbols_learned_restriction_never_enters_anothers_store(tmp_path: Path) -> None:
    """Per-symbol capability, which is what the requirement asks for literally."""
    mirror = tmp_path / "events.jsonl"
    mirror.write_text(json.dumps({
        "event_type": "LIVE_LEVERAGE_REFUSED", "ts_ms": 1_790_854_715_989, "code": -4300,
        "requested": 50, "symbol": "BTCUSDT", "message": REFUSAL}) + "\n")
    btc = LeverageCapability(path=tmp_path / "cap_BTCUSDT.json")
    eth = LeverageCapability(path=tmp_path / "cap_ETHUSDT.json")
    btc_outcome = btc.bootstrap_from_mirror(mirror, symbol="BTCUSDT")
    eth_outcome = eth.bootstrap_from_mirror(mirror, symbol="ETHUSDT")
    assert btc_outcome.restriction is not None, "BTC heard this refusal and keeps it"
    assert eth_outcome.restriction is None, "ETH heard nothing and must store nothing"
    assert btc.restriction_for(50) is not None
    assert eth.restriction_for(50) is None
    # Two files, so neither can overwrite the other's knowledge.
    assert btc.path != eth.path


def test_an_account_wide_restriction_is_reported_on_every_symbol_with_its_provenance(
        tmp_path: Path) -> None:
    mirror = tmp_path / "events.jsonl"
    mirror.write_text(json.dumps({
        "event_type": "LIVE_LEVERAGE_REFUSED", "ts_ms": 1_790_854_715_989, "code": -4300,
        "requested": 50, "symbol": "BTCUSDT", "message": REFUSAL}) + "\n")
    advisory = account_scope_from_mirror(mirror, now_ms=UNLOCK_MS - 1)
    assert isinstance(advisory, AccountScopeAdvisory)
    assert advisory.observed_on_symbol == "BTCUSDT", "the screen must be able to say where"
    assert advisory.restriction.above == 20
    assert advisory.applies_to(50) and advisory.applies_to(100)
    assert not advisory.applies_to(20) and not advisory.applies_to(1)
    assert "계정 전체 제한" in advisory.message(50) and "BTCUSDT" in advisory.message(50)
    # It expires by itself at the instant Binance named, with no deploy and nothing to notice.
    assert account_scope_from_mirror(mirror, now_ms=UNLOCK_MS) is None


def test_a_symbol_scoped_refusal_is_not_promoted_to_the_account(tmp_path: Path) -> None:
    mirror = tmp_path / "events.jsonl"
    mirror.write_text(json.dumps({
        "event_type": "LIVE_LEVERAGE_REFUSED", "ts_ms": 1, "code": -4300, "requested": 50,
        "symbol": "BTCUSDT",
        "message": "The symbol does not support 50x leverage by 2026-10-29 01:34 (UTC)."}) + "\n")
    assert account_scope_from_mirror(mirror, now_ms=2) is None


def test_a_later_success_above_the_threshold_discards_the_advisory(tmp_path: Path) -> None:
    mirror = tmp_path / "events.jsonl"
    mirror.write_text("\n".join(json.dumps(row) for row in [
        {"event_type": "LIVE_LEVERAGE_REFUSED", "ts_ms": 1_790_854_715_989, "code": -4300,
         "requested": 50, "symbol": "BTCUSDT", "message": REFUSAL},
        # The success is on another symbol, and that is exactly why it counts: the claim being
        # contradicted is about the account.
        {"event_type": "LIVE_LEVERAGE_RESULT", "ts_ms": 1_790_900_000_000, "requested": 50,
         "symbol": "ETHUSDT"},
    ]) + "\n")
    assert account_scope_from_mirror(mirror, now_ms=1_790_950_000_000) is None


# ------------------------------------------------------------------ the routes


@pytest.fixture
def live_app(monkeypatch, tmp_path: Path):
    """The deployed app with all three symbols wired to the per-symbol fake exchange.

    Built the way `LiveRuntime` builds it - shared client and mirror, one `SymbolRuntime` each -
    and injected into the registry, so the routes exercise the real lookup rather than a
    single adapter standing in for three.
    """
    from app.crypto.terminal import live_routes
    from app.crypto.terminal.server import app

    fake = MultiSymbolBinance(positions={
        "BTCUSDT": held("BTCUSDT", "LONG", "0.058"),
        "SOLUSDT": held("SOLUSDT", "SHORT", "12"),
    }, extra_rows=[held("XRPUSDT", "LONG", "500")])
    client, config, fake = make(fake)
    mirror = LiveMirror(path=tmp_path / "live" / "events.jsonl",
                        account_fingerprint=config.fingerprint)
    runtime = live_routes.live_runtime
    entries: dict[str, Any] = {}
    for symbol in SUPPORTED_SYMBOLS:
        capability = LeverageCapability(path=tmp_path / f"cap_{symbol}.json")
        adapter = adapter_for(symbol, client, config, mirror=mirror, capability=capability)
        entries[symbol] = live_routes.SymbolRuntime(
            symbol=symbol, adapter=adapter, reader=adapter.reader, router=adapter.router,
            capability=capability, bootstrap=None, performance=None)
    monkeypatch.setattr(runtime, "symbols", entries)
    monkeypatch.setattr(runtime, "adapter", None)
    monkeypatch.setattr(runtime, "config", config)
    monkeypatch.setattr(runtime, "client", client)
    monkeypatch.setattr(runtime, "mirror", mirror)
    monkeypatch.setattr(runtime, "exit_guard", None)
    monkeypatch.setattr(runtime, "error", None)
    return TestClient(app), fake, mirror


def test_every_read_route_answers_about_the_symbol_it_was_asked_for(live_app) -> None:
    client, fake, _ = live_app
    for symbol in SUPPORTED_SYMBOLS:
        account = client.get("/api/crypto/binance/account", params={"symbol": symbol}).json()
        assert account["symbol"] == symbol
        assert account["base_asset"] == SPECS[symbol]["base"]
        assert account["filters"]["symbol"] == symbol
        assert account["mark"]["mark_price"] == SPECS[symbol]["price"]
        sizing = client.get("/api/crypto/binance/sizing", params={"symbol": symbol}).json()
        assert sizing["symbol"] == symbol
        leverage = client.get("/api/crypto/binance/leverage", params={"symbol": symbol}).json()
        assert leverage["symbol"] == symbol and leverage["base_asset"] == SPECS[symbol]["base"]
        fills = client.get("/api/crypto/binance/fills", params={"symbol": symbol}).json()
        assert fills["symbol"] == symbol


def test_no_symbol_means_the_default_symbol_on_every_route(live_app) -> None:
    """Every pre-existing single-symbol client passes nothing, and must keep meaning BTCUSDT."""
    client, _, _ = live_app
    for path in ("/api/crypto/binance/account", "/api/crypto/binance/sizing",
                 "/api/crypto/binance/leverage", "/api/crypto/binance/fills"):
        body = client.get(path).json()
        assert body["symbol"] == DEFAULT_SYMBOL, path


def test_an_unsupported_symbol_is_refused_by_every_route_before_anything_is_read(live_app) -> None:
    client, fake, _ = live_app
    before = len(fake.calls)
    for path in ("/api/crypto/binance/account", "/api/crypto/binance/sizing",
                 "/api/crypto/binance/position", "/api/crypto/binance/leverage",
                 "/api/crypto/binance/preview", "/api/crypto/binance/fills",
                 "/api/crypto/binance/funding", "/api/crypto/binance/events",
                 "/api/crypto/binance/performance", "/api/crypto/binance/exit-guard",
                 "/api/crypto/binance/status"):
        response = client.get(path, params={"symbol": "XRPUSDT", "qty": "1"})
        assert response.status_code == 400, path
        assert response.json()["error"]["code"] == "SYMBOL_NOT_SUPPORTED", path
    for body in ({"side": "LONG", "intent": "OPEN", "qty": "1", "symbol": "XRPUSDT"},):
        response = client.post("/api/crypto/binance/order", json=body)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "SYMBOL_NOT_SUPPORTED"
    response = client.post("/api/crypto/binance/leverage",
                           json={"leverage": "10", "symbol": "XRPUSDT"})
    assert response.status_code == 400
    assert len(fake.calls) == before, "a refused symbol must not reach Binance at all"


def test_the_positions_summary_is_one_read_and_names_the_untraded_symbols_too(live_app) -> None:
    client, fake, _ = live_app
    before = fake.count("/fapi/v3/positionRisk")
    body = client.get("/api/crypto/binance/positions").json()
    assert fake.count("/fapi/v3/positionRisk") == before + 1, \
        "three per-symbol reads could return three different instants"
    rows = {row["symbol"]: row for row in body["positions"]}
    assert set(rows) == set(SUPPORTED_SYMBOLS), "a flat symbol is a row, not an absence"
    assert (rows["BTCUSDT"]["side"], rows["BTCUSDT"]["qty"]) == ("LONG", "0.058")
    assert rows["ETHUSDT"]["is_flat"] is True
    assert (rows["SOLUSDT"]["side"], rows["SOLUSDT"]["qty"]) == ("SHORT", "12")
    assert rows["SOLUSDT"]["base_asset"] == "SOL"
    # A position opened in the Binance app on a symbol this screen does not trade still holds
    # margin that every tab's Safe MAX reflects, so hiding it would make the strip wrong.
    assert [row["symbol"] for row in body["others"]] == ["XRPUSDT"]
    assert body["others"][0]["tradable_here"] is False
    assert body["others"][0]["side"] == "LONG"


def test_switching_tabs_cannot_change_or_close_another_symbols_position(live_app) -> None:
    """Reading one symbol is a read of that symbol. Nothing else moves."""
    client, fake, _ = live_app
    before = client.get("/api/crypto/binance/positions").json()["positions"]
    for symbol in SUPPORTED_SYMBOLS:
        client.get("/api/crypto/binance/account", params={"symbol": symbol})
        client.get("/api/crypto/binance/sizing", params={"symbol": symbol})
        client.get("/api/crypto/binance/position", params={"symbol": symbol})
    after = client.get("/api/crypto/binance/positions").json()["positions"]
    assert before == after
    writes = [(method, path) for method, path, _ in fake.calls
              if method in {"POST", "PUT", "DELETE"} and path != "/fapi/v1/listenKey"]
    assert writes == [], f"a tab change must send no write at all, got {writes}"


def test_an_order_route_carries_the_symbol_into_the_routers_check(live_app) -> None:
    client, fake, _ = live_app
    # Trading is off in this fixture, so the expected refusal is the gate - after the symbol
    # check has already passed, which is what makes the plan in the response ETHUSDT's.
    response = client.post("/api/crypto/binance/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": "0.010",
                                 "symbol": "ETHUSDT"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    orders = [params for method, path, params in fake.calls
              if (method, path) == ("POST", "/fapi/v1/order")]
    assert orders == [], "V1 refuses at the gate and sends nothing"


def test_the_account_scope_advisory_reaches_every_symbols_leverage_route(live_app) -> None:
    """Binance refused 50x on BTCUSDT for a reason that names the account, not the instrument.

    Two things then have to be true at once, and they are different things:

    * the **advisory** is reported on all three symbols, labelled with the symbol it was heard
      on, because Binance's sentence was about the account;
    * the **per-symbol store** stays per symbol - only BTCUSDT's capability learned anything.
      That is what "do not propagate BTC's capability" means, and it is why the advisory is
      derived from the audit file on every request instead of being written into the others.
    """
    from app.crypto.terminal import live_routes

    client, fake, mirror = live_app
    mirror.append(LiveEvent.LEVERAGE_REFUSED, stage="EXCHANGE", symbol="BTCUSDT", code=-4300,
                  requested=50, message=REFUSAL)
    # Re-run the startup scan now the line exists, which is what a restart would do.
    for symbol, entry in live_routes.live_runtime.symbols.items():
        entry.capability.bootstrap_from_mirror(mirror.path, symbol=symbol)
    for symbol in SUPPORTED_SYMBOLS:
        body = client.get("/api/crypto/binance/leverage", params={"symbol": symbol}).json()
        scope = body["account_scope"]
        assert scope is not None, symbol
        assert scope["scope"] == "ACCOUNT"
        assert scope["observed_on_symbol"] == "BTCUSDT", "provenance, not a silent copy"
        assert scope["above"] == 20
        assert 50 in scope["blocked"] and 100 in scope["blocked"]
        assert 20 not in scope["blocked"] and 1 not in scope["blocked"]
        assert "계정 전체 제한" in scope["messages"]["50"]
        # The per-symbol store is untouched: only BTCUSDT heard the refusal.
        unavailable = body.get("unavailable") or {}
        if symbol == "BTCUSDT":
            assert "50" in unavailable
        else:
            assert unavailable == {}, f"{symbol} must not have learned BTC's restriction"


def test_auto_exit_refuses_a_symbol_it_does_not_watch(live_app, monkeypatch) -> None:
    """AUTO is out of scope for this step, so the guard stays single-symbol and says so.

    Answering with the guard's own state under another tab would show a BTC stop on an ETH
    screen; configuring one there would arm a close on a position the operator was not looking
    at. Both are refused.
    """
    from app.crypto.terminal import live_routes

    client, _, _ = live_app
    entry = live_routes.live_runtime.symbols[DEFAULT_SYMBOL]

    class Guard:
        adapter = entry.adapter

        def view(self) -> dict[str, Any]:
            return {"state": "OFF", "enabled": False, "symbol": None}

    monkeypatch.setattr(live_routes.live_runtime, "exit_guard", Guard())
    own = client.get("/api/crypto/binance/exit-guard",
                     params={"symbol": DEFAULT_SYMBOL}).json()
    assert own["available"] is True and own["guard_symbol"] == DEFAULT_SYMBOL
    other = client.get("/api/crypto/binance/exit-guard", params={"symbol": "ETHUSDT"}).json()
    assert other["available"] is False
    assert other["unavailable_reason"] == "AUTO_SINGLE_SYMBOL"
    assert DEFAULT_SYMBOL in other["unavailable_message"]
    refused = client.post("/api/crypto/binance/exit-guard",
                          json={"take_profit_krw": "10000", "stop_loss_krw": "-10000",
                                "symbol": "ETHUSDT"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "AUTO_SINGLE_SYMBOL"


def test_the_status_route_publishes_the_servers_symbol_list_without_a_key(monkeypatch) -> None:
    """The tab strip renders before anything is read, so the list has to come from the server
    even when there is no Binance key at all - otherwise the client would be inventing it."""
    from app.crypto.terminal import live_routes
    from app.crypto.terminal.server import app

    for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_LIVE_TRADING_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(live_routes.live_runtime, "symbols", {})
    monkeypatch.setattr(live_routes.live_runtime, "adapter", None)
    monkeypatch.setattr(live_routes.live_runtime, "config", None)
    monkeypatch.setattr(live_routes.live_runtime, "client", None)
    monkeypatch.setattr(live_routes.live_runtime, "error", None)
    body = TestClient(app).get("/api/crypto/binance/status").json()
    assert body["available"] is False
    assert body["symbols"] == list(SUPPORTED_SYMBOLS)
    assert body["default_symbol"] == DEFAULT_SYMBOL
    assert body["symbol"] == DEFAULT_SYMBOL


# ------------------------------------------------------------------ PAPER and the chart


def test_each_paper_symbol_gets_its_own_measured_instrument_never_the_defaults() -> None:
    """The paper engine's quantity rules are only as correct as its `InstrumentSpec`.

    BTCUSDT's spec is the one measured in D1 and written into `instrument.py` as the defaults.
    The other two cannot be added that way: their steps differ by up to two orders of magnitude,
    and a plausible number in `qty_step` is an order the exchange refuses or one it accepts at a
    size nobody intended. So they are parsed from a saved `instruments-info` response, and a
    missing field raises rather than inheriting BTC's value.
    """
    from app.crypto.paper.instrument import InstrumentSpec

    measured = {}
    for symbol in ("ETHUSDT", "SOLUSDT"):
        folder = Path("data/runtime/crypto") / symbol / "reference"
        files = sorted(folder.glob("instruments_info_*.json"))
        assert files, f"{symbol} has no measured instruments-info response"
        measured[symbol] = InstrumentSpec.from_file(files[-1], symbol)

    btc = InstrumentSpec()
    assert btc.symbol == "BTCUSDT" and btc.qty_step == Decimal("0.001")
    # The differences are real and large. A shared spec would be wrong by 100x on SOL.
    assert measured["ETHUSDT"].qty_step == Decimal("0.01")
    assert measured["SOLUSDT"].qty_step == Decimal("0.1")
    assert measured["SOLUSDT"].qty_step != btc.qty_step
    for symbol, spec in measured.items():
        assert spec.symbol == symbol
        assert spec.contract_type == "LinearPerpetual"
        # Provenance, not a literal: the source names the file and its sha256.
        assert "instruments-info" in spec.source and "sha256" in spec.source


def test_a_response_missing_a_quantity_field_is_refused_rather_than_defaulted() -> None:
    from app.crypto.paper.instrument import InstrumentSpec

    payload = {"result": {"list": [{
        "symbol": "ETHUSDT", "contractType": "LinearPerpetual", "settleCoin": "USDT",
        "fundingInterval": 480,
        "lotSizeFilter": {"minOrderQty": "0.01", "maxOrderQty": "10000",
                          "maxMktOrderQty": "2000", "minNotionalValue": "5"},
        "priceFilter": {"tickSize": "0.01"},
        "leverageFilter": {"minLeverage": "1", "maxLeverage": "150", "leverageStep": "0.01"},
    }]}}
    with pytest.raises(ValueError, match="qtyStep"):
        InstrumentSpec.from_instruments_info(payload, "ETHUSDT", source="test")


def test_every_symbols_risk_table_is_verified_for_itself_not_assumed_from_btc() -> None:
    """The maintenance-margin formula is continuous across tier boundaries, per symbol.

    `MM(notional) = notional * rate - deduction` is only meaningful if it is continuous, and a
    table that failed it would make every margin figure on that screen wrong. SOLUSDT's own
    first-tier ceiling is 100x where BTCUSDT's is 150x, which is one of the reasons this is
    checked per symbol rather than once.
    """
    from app.crypto.paper.instrument import RiskTierTable

    ceilings = {}
    for symbol in SUPPORTED_SYMBOLS:
        files = sorted((Path("data/runtime/crypto") / symbol / "reference").glob("risk_limit_*.json"))
        assert files, f"{symbol} has no measured risk-limit response"
        table = RiskTierTable.from_file(files[-1])
        table.verify_continuity()
        assert len(table.tiers) > 1
        ceilings[symbol] = table.tiers[0].max_leverage
    assert ceilings["SOLUSDT"] != ceilings["BTCUSDT"], \
        "the tables are genuinely different, so sharing one would be wrong"


def test_a_per_symbol_paper_run_never_opens_the_default_symbols_ledger(tmp_path: Path) -> None:
    """"Do not damage the existing BTC ledger", structurally.

    Each symbol's run id is the base id plus the symbol, so each gets its own directory, its own
    `ledger.jsonl` and its own input tape. The default symbol keeps its id unchanged, so no file
    it owns is opened by another symbol's session.
    """
    from app.crypto.terminal.api import symbol_run_config
    from app.crypto.paper.config import build_config

    base = build_config(
        run_id="paper-d4-v3-001", starting_capital_krw="1000000", fx_krw_per_usdt="1344.00",
        fx_source="test", fx_asof_utc="2026-10-04T00:00:00Z", fee_version="v",
        fee_taker_rate="0.00055", fee_maker_rate="0.0002", fee_source="s",
        fee_effective_date="2026-09-02", slippage_model="NONE", slippage_bps="0",
        leverage="10", risk_limit_source="btc.json", risk_limit_sha256="0" * 64)
    eth, tiers = symbol_run_config(base, "ETHUSDT")
    assert eth.run_id == "paper-d4-v3-001-ETHUSDT" != base.run_id
    assert eth.instrument.symbol == "ETHUSDT"
    assert eth.instrument.qty_step == Decimal("0.01")
    # Same experiment: identical capital, FX fixing and fee schedule, so a difference between
    # two screens is the instrument's doing and not a different fee table's.
    assert (eth.starting_capital_krw, eth.fx, eth.fees, eth.slippage, eth.leverage) == \
        (base.starting_capital_krw, base.fx, base.fees, base.slippage, base.leverage)
    # Its own risk table, with the sha256 of the file actually read.
    assert eth.risk_limit_sha256 == tiers.source_sha256 != base.risk_limit_sha256
    # And the default symbol's config object is untouched by having built another one.
    assert base.run_id == "paper-d4-v3-001" and base.instrument.symbol == "BTCUSDT"


def test_the_chart_history_cache_is_keyed_by_symbol() -> None:
    """A cache keyed on (timeframe, before, limit) alone would serve BTC's bars to an ETH
    request made inside the TTL: the chart would show the wrong instrument while every label on
    the screen said the right one."""
    from app.crypto.terminal.chart_history import BybitChartHistory

    asked: list[str] = []

    def fake(path: str, params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params["symbol"])
        return {"retCode": 0, "result": {"list": [
            [str(60_000 * i), "1", "2", "0.5", "1.5", "10"] for i in range(1, 30)]}}

    history = BybitChartHistory(request=fake)
    assert history.get("1m", 5)["symbol"] == "BTCUSDT"
    assert history.get("1m", 5, symbol="ETHUSDT")["symbol"] == "ETHUSDT"
    assert history.get("1m", 5)["symbol"] == "BTCUSDT"
    assert asked == ["BTCUSDT", "ETHUSDT"], "the repeat BTC read is a cache hit, not a refetch"
    with pytest.raises(SymbolNotSupported):
        history.get("1m", 5, symbol="XRPUSDT")


def test_each_symbols_public_feed_subscribes_to_its_own_topics_only() -> None:
    """One socket per symbol. A shared socket would mean an ETH book gap discarding BTC's book,
    because the continuity rule - a sequence gap discards and resubscribes - is per instrument.
    """
    from app.crypto.terminal.feed import BybitPublicFeed, book_topic, topics_for

    for symbol in SUPPORTED_SYMBOLS:
        feed = BybitPublicFeed(symbol=symbol)
        assert feed.symbol == symbol
        assert feed.view()["symbol"] == symbol
        assert topics_for(symbol) == (f"orderbook.50.{symbol}", f"tickers.{symbol}",
                                      f"kline.1.{symbol}")
        assert all(symbol in topic for topic in topics_for(symbol))
        assert book_topic(symbol) == f"orderbook.50.{symbol}"
    # The default is unchanged, so every existing caller still gets BTCUSDT.
    assert BybitPublicFeed().symbol == DEFAULT_SYMBOL


# ------------------------------------------------------------------ what stays single-symbol


def test_c1_is_offered_on_the_default_symbol_only(monkeypatch) -> None:
    """C1 is a BTCUSDT research result. Offering its state under another tab would present a
    BTC signal as that instrument's, which is the contamination this step must prevent."""
    from app.crypto.terminal import api as terminal_api
    from app.crypto.terminal.server import app

    class Session:
        def __init__(self, symbol: str) -> None:
            class Spec:
                pass
            spec = Spec()
            spec.symbol = symbol
            class Config:
                pass
            self.config = Config()
            self.config.instrument = spec

    default = Session(DEFAULT_SYMBOL)
    others = {symbol: Session(symbol) for symbol in SUPPORTED_SYMBOLS}
    others[DEFAULT_SYMBOL] = default

    class Auto:
        state = type("S", (), {"enabled": False})()

        def view(self) -> dict[str, Any]:
            return {"enabled": False, "state": "OFF"}

    runtime = terminal_api.runtime
    monkeypatch.setattr(runtime, "session", default)
    monkeypatch.setattr(runtime, "c1_auto", Auto())
    monkeypatch.setattr(runtime, "selected_session",
                        lambda symbol=None: others[resolve(symbol)])
    client = TestClient(app)

    allowed = client.get("/api/crypto/paper/c1-auto", params={"symbol": DEFAULT_SYMBOL})
    assert allowed.status_code == 200 and allowed.json()["symbol"] == DEFAULT_SYMBOL
    for symbol in ("ETHUSDT", "SOLUSDT"):
        refused = client.get("/api/crypto/paper/c1-auto", params={"symbol": symbol})
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "C1_SINGLE_SYMBOL"
        toggled = client.post("/api/crypto/paper/c1-auto",
                              json={"enabled": True, "symbol": symbol})
        assert toggled.status_code == 409
        assert toggled.json()["error"]["code"] == "C1_SINGLE_SYMBOL"


def test_the_manual_terminal_holds_no_market_structure_panel_at_all() -> None:
    """Requirement: never show BTC walls or flow on an ETH or SOL screen.

    It holds structurally rather than by a condition: the manual terminal imports nothing from
    the market-structure or liquidity-map layers, which live on their own preview route. This
    test is the guard that keeps it that way - a future session wiring a wall ladder into the
    terminal would have to see this fail and decide about the symbol deliberately.
    """
    import ast

    terminal = Path("frontend/components")
    screens = ["crypto-live-terminal.tsx", "crypto-terminal-layout.tsx",
               "crypto-paper-terminal.tsx", "crypto-symbol-tabs.tsx"]
    forbidden = ("liquidity-map", "market-structure", "market_structure", "wallstate", "wallrule")
    for name in screens:
        text = (terminal / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{name} reaches for {token}"
    page = (Path("frontend/app/crypto-paper/page.tsx")).read_text(encoding="utf-8")
    for token in forbidden:
        assert token not in page, f"the terminal page reaches for {token}"

    # Same on the backend: the terminal package must not import the market-structure collector.
    backend = Path("backend/app/crypto/terminal")
    for path in sorted(backend.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
            for name in names:
                assert "market_structure" not in name and "liquidity_map" not in name, \
                    f"{path.name} imports {name}"


def test_the_default_symbols_15s_feed_has_one_authority_not_two(monkeypatch) -> None:
    """A registry entry and a module attribute naming the same object will drift.

    The regression this pins: `trade_feeds` captured `trade_feed` at import time, so anything
    that replaced the module attribute afterwards left the route serving the captured object.
    The symptom was an empty 15 s chart with no error anywhere - the route answered 200 with
    zero candles while a perfectly healthy feed sat beside it holding them.
    """
    from app.crypto.terminal import server as terminal_server
    from app.crypto.terminal.trade_candles import TradeCandleFeed

    replacement = TradeCandleFeed()
    monkeypatch.setattr(terminal_server, "trade_feed", replacement)
    assert terminal_server.trade_feed_for(DEFAULT_SYMBOL) is replacement
    # And the registry is corrected rather than left holding the stale object.
    assert terminal_server.trade_feeds[DEFAULT_SYMBOL] is replacement
    # Another symbol still gets its own, and it is not the default's.
    monkeypatch.setitem(terminal_server.trade_feeds, "ETHUSDT", TradeCandleFeed("ETHUSDT"))
    eth = terminal_server.trade_feed_for("ETHUSDT")
    assert eth.symbol == "ETHUSDT" and eth is not replacement


def test_the_measured_reference_files_are_found_without_depending_on_the_working_directory(
        monkeypatch, tmp_path: Path) -> None:
    """The deployed unit runs with `WorkingDirectory=/root/usb_runtime/crypto_paper`.

    A bare relative `data/runtime/crypto` resolves to a directory that does not exist there, so
    ETHUSDT and SOLUSDT would have reported "no measurement" in production while every test
    passed locally. This was found by running the preview from `backend/` rather than the
    repository root, and it is pinned here so it cannot come back.
    """
    from app.crypto.terminal.api import (SYMBOL_REFERENCE_ENV, SYMBOL_REFERENCE_ROOT,
                                         symbol_reference_roots)

    monkeypatch.delenv(SYMBOL_REFERENCE_ENV, raising=False)
    run = Path("/root/usb_runtime/crypto_paper/run")
    roots = symbol_reference_roots(run)
    # Beside the run state, then its parent, then the repository-relative path. The first two
    # are what make a deployment work without the process being started from the repo.
    assert roots[0] == run / "reference"
    assert roots[1] == run.parent
    assert SYMBOL_REFERENCE_ROOT in roots
    # An explicit variable wins over all of it.
    monkeypatch.setenv(SYMBOL_REFERENCE_ENV, str(tmp_path))
    assert symbol_reference_roots(run)[0] == tmp_path


def test_a_missing_measurement_names_the_paths_it_looked_in(tmp_path: Path, monkeypatch) -> None:
    """The refusal has to be actionable. "No measurement" without a path is a dead end for the
    operator, and silently inheriting another symbol's spec is the alternative this rejects."""
    from app.crypto.terminal import api as terminal_api
    from app.crypto.terminal.api import (SYMBOL_REFERENCE_ENV, SymbolReferenceMissing,
                                         symbol_run_config)
    from app.crypto.paper.config import build_config

    monkeypatch.setenv(SYMBOL_REFERENCE_ENV, str(tmp_path / "nothing-here"))
    # The repository-relative fallback has to be emptied as well, or this test would pass only
    # because it happens to run from the repository root - which is the very assumption the
    # production defect was made of.
    monkeypatch.setattr(terminal_api, "SYMBOL_REFERENCE_ROOT", tmp_path / "also-nothing")
    base = build_config(
        run_id="r", starting_capital_krw="1000000", fx_krw_per_usdt="1344.00", fx_source="t",
        fx_asof_utc="2026-10-04T00:00:00Z", fee_version="v", fee_taker_rate="0.00055",
        fee_maker_rate="0.0002", fee_source="s", fee_effective_date="2026-09-02",
        slippage_model="NONE", slippage_bps="0", leverage="10", risk_limit_source="x",
        risk_limit_sha256="0" * 64)
    with pytest.raises(SymbolReferenceMissing) as caught:
        symbol_run_config(base, "ETHUSDT", None, paper_root=tmp_path / "run")
    message = str(caught.value)
    assert "ETHUSDT" in message
    assert str(tmp_path / "nothing-here") in message
    assert SYMBOL_REFERENCE_ENV in message


def test_a_mirror_line_that_names_no_symbol_belongs_to_the_default_symbol_only(
        tmp_path: Path) -> None:
    """Found on the real account during the production gate, after the deploy.

    The two `-4300` refusals in this account's audit file were written before the `symbol` field
    existed. `restriction_from_mirror` skipped only lines naming a *different* symbol, so an
    unnamed line was adopted by whichever symbol asked - and ETHUSDT and SOLUSDT each wrote
    BTCUSDT's restriction into their own capability file the first time they were built. That is
    precisely "propagate BTC's capability to the others without evidence".

    The account-wide limit still reaches the other symbols, but through the advisory, which says
    it is the account's and names where it was heard (or that it was unnamed).
    """
    mirror = tmp_path / "events.jsonl"
    mirror.write_text(json.dumps({
        "event_type": "LIVE_LEVERAGE_REFUSED", "ts_ms": 1_790_854_714_521, "code": -4300,
        "requested": 50, "message": REFUSAL}) + "\n")  # note: no "symbol" key at all

    from app.crypto.live.leverage import restriction_from_mirror

    default = restriction_from_mirror(mirror, symbol="BTCUSDT", default_symbol="BTCUSDT",
                                      now_ms=UNLOCK_MS - 1)
    assert default.restriction is not None, "the default symbol owns its own legacy lines"
    for other in ("ETHUSDT", "SOLUSDT"):
        outcome = restriction_from_mirror(mirror, symbol=other, default_symbol="BTCUSDT",
                                          now_ms=UNLOCK_MS - 1)
        assert outcome.restriction is None, f"{other} adopted a line that never named it"

    # The per-symbol stores stay clean, and the advisory is what carries the account-wide fact.
    caps = {s: LeverageCapability(path=tmp_path / f"cap_{s}.json") for s in SUPPORTED_SYMBOLS}
    for symbol, capability in caps.items():
        capability.bootstrap_from_mirror(mirror, symbol=symbol, default_symbol="BTCUSDT",
                                         now_ms=UNLOCK_MS - 1)
    assert caps["BTCUSDT"].restriction_for(50) is not None
    assert caps["ETHUSDT"].restriction_for(50) is None
    assert caps["SOLUSDT"].restriction_for(50) is None
    advisory = account_scope_from_mirror(mirror, now_ms=UNLOCK_MS - 1)
    assert advisory is not None and advisory.applies_to(50)
    assert advisory.observed_on_symbol is None, "an unnamed line cannot claim a symbol"

    # Single-symbol callers that pass no default are unchanged.
    legacy = restriction_from_mirror(mirror, symbol="BTCUSDT", now_ms=UNLOCK_MS - 1)
    assert legacy.restriction is not None


# ------------------------------------------------------------------ one PAPER wallet


def _account(**over: Any):
    from app.crypto.paper.account import Account
    base = {"starting_capital_usdt": Decimal("745.71"), "capital_base_usdt": Decimal("745.71")}
    return Account(**{**base, **over})


def _position(qty: str, entry: str, leverage: str = "10"):
    from app.crypto.paper.account import Position
    return Position(signed_qty=Decimal(qty), avg_entry=Decimal(entry), leverage=Decimal(leverage))


def test_an_account_with_no_shared_wallet_behaves_exactly_as_before() -> None:
    """The single-symbol run must not move. Every figure and both invariants, unchanged."""
    account = _account()
    assert account.cash is None
    assert account.wallet_balance == Decimal("745.71")
    assert account.available_balance == Decimal("745.71")
    account.position = _position("0.01", "84800")
    assert account.used_margin == Decimal("84.800")
    assert account.available_balance == Decimal("745.71") - Decimal("84.800")
    account.assert_invariants(Decimal("84800"))


def test_three_symbols_draw_on_one_wallet_and_do_not_each_bring_their_own(
) -> None:
    """Adding an instrument adds an instrument, not another starting balance.

    This is the whole requirement: a real Binance futures wallet is one purse, so margin posted
    on BTCUSDT is margin ETHUSDT cannot also spend. Before this, each paper symbol had its own
    745.71 and the screen accepted sizes the live account would refuse - the rehearsal was
    looser than the thing it rehearses, which is the dangerous direction.
    """
    from app.crypto.paper.account import SharedCash

    btc, eth, sol = _account(), _account(), _account()
    purse = SharedCash(owner="BTCUSDT")
    for symbol, account in (("BTCUSDT", btc), ("ETHUSDT", eth), ("SOLUSDT", sol)):
        purse.join(symbol, account)

    # Not 3 x 745.71. The members' own capital bases are ignored; the owner's is the wallet's.
    assert purse.wallet_balance == Decimal("745.71")
    assert btc.available_balance == eth.available_balance == sol.available_balance

    btc.position = _position("0.05", "84800")
    posted = btc.used_margin
    assert posted == Decimal("424.000")
    # Every symbol sees the money go, including the two that did nothing.
    for account in (btc, eth, sol):
        assert account.available_balance == Decimal("745.71") - posted
    # Each symbol's own margin is still its own: this is what backs that position.
    assert (eth.used_margin, sol.used_margin) == (Decimal(0), Decimal(0))

    eth.position = _position("0.1", "2690")
    assert purse.used_margin == posted + eth.used_margin
    for account in (btc, eth, sol):
        assert account.available_balance == Decimal("745.71") - purse.used_margin


def test_attaching_a_wallet_carries_the_running_balance_across_untouched() -> None:
    """Measured against the real server run: capital base 7457.12 after three resets, 1930.80
    realised, 1102.06 of fees and 75.15 of funding received, leaving 9105.62.

    The purse takes the owner's capital base rather than a seed of its own, so the balance is
    carried by construction and there is no migration arithmetic to get wrong.
    """
    from app.crypto.paper.account import SharedCash

    btc = _account(capital_base_usdt=Decimal("7457.121551081282624906785981"),
                   realized_pnl=Decimal("1930.803539999999999999999976"),
                   cumulative_fees=Decimal("1102.060349357"),
                   cumulative_funding_paid=Decimal("-75.1519110740785"),
                   charges_at_anchor=Decimal("744.607646"))
    before = btc.wallet_balance
    purse = SharedCash(owner="BTCUSDT")
    purse.join("BTCUSDT", btc)
    assert purse.wallet_balance == before, "attaching a wallet must not move the money"
    for symbol in ("ETHUSDT", "SOLUSDT"):
        purse.join(symbol, _account())
    assert purse.wallet_balance == before, "a joining symbol must not add a second balance"


def test_the_shared_wallet_states_both_invariants_where_they_are_true() -> None:
    """A6 and P5 cannot be stated per symbol once the wallet is shared.

    P5 reads `equity = available + used_margin + unrealized`; with one wallet behind three
    screens the left side is the purse's while `used_margin` on the right is one symbol's. The
    statement is true of the purse, so the check moved there rather than being dropped.
    """
    from app.crypto.paper.account import SharedCash

    btc, eth = _account(), _account()
    purse = SharedCash(owner="BTCUSDT")
    purse.join("BTCUSDT", btc)
    purse.join("ETHUSDT", eth)
    btc.position = _position("0.05", "84800")
    eth.position = _position("0.1", "2690")
    marks = {"BTCUSDT": Decimal("85000"), "ETHUSDT": Decimal("2700")}
    purse.assert_invariants(marks)
    # The per-symbol check is a no-op on a shared wallet rather than a wrong assertion.
    btc.assert_invariants(marks["BTCUSDT"])
    # The purse's P5 is the one that would now catch a mistake: it is the only place where the
    # wallet on the left and every symbol's margin on the right are the same wallet's.
    assert purse.available_balance + purse.used_margin == purse.wallet_balance
    # A member whose margin the purse cannot see would break it, which is what the sum guards.
    orphan = _account()
    orphan.position = _position("1", "84800")
    assert orphan.used_margin not in (Decimal(0),)
    assert purse.used_margin == btc.used_margin + eth.used_margin, \
        "the purse must count every member and only members"
    # And the single-account path still checks itself.
    solo = _account()
    solo.position = _position("0.05", "84800")
    solo.assert_invariants(Decimal("85000"))


def test_a_balance_reset_re_anchors_the_whole_wallet_not_one_symbols_share() -> None:
    from app.crypto.paper.account import SharedCash

    btc, eth, sol = _account(), _account(), _account()
    purse = SharedCash(owner="BTCUSDT")
    for symbol, account in (("BTCUSDT", btc), ("ETHUSDT", eth), ("SOLUSDT", sol)):
        purse.join(symbol, account)
    btc.realized_pnl = Decimal("300")
    eth.realized_pnl = Decimal("-120")
    sol.cumulative_fees = Decimal("5")
    assert purse.wallet_balance == Decimal("745.71") + Decimal("300") - Decimal("120") - Decimal("5")

    purse.apply_capital_reset(Decimal("1000"), ts_ms=1)
    # Lands exactly on the target: re-anchoring only the owner would have left the other two
    # members' deltas still counted in the wallet.
    assert purse.wallet_balance == Decimal("1000")
    assert purse.available_balance == Decimal("1000")
    # Nothing cumulative was rewound, so the per-instrument analytics keep their whole history.
    assert (btc.realized_pnl, eth.realized_pnl, sol.cumulative_fees) == (
        Decimal("300"), Decimal("-120"), Decimal("5"))


def test_the_runtime_gives_every_symbol_the_same_wallet(monkeypatch, tmp_path: Path) -> None:
    """End to end through `Runtime`, which is where the purse is actually assembled."""
    from app.crypto.terminal import api as terminal_api

    runtime = terminal_api.Runtime()
    accounts: dict[str, Any] = {}

    class FakeSession:
        def __init__(self, symbol: str) -> None:
            self.config = type("C", (), {})()
            self.config.instrument = type("I", (), {"symbol": symbol})()
            self.config.run_id = f"run-{symbol}"
            self.engine = type("E", (), {})()
            self.engine.account = _account()
            accounts[symbol] = self.engine.account

    btc = FakeSession("BTCUSDT")
    runtime.session = btc
    runtime.sessions["BTCUSDT"] = btc
    from app.crypto.paper.account import SharedCash
    runtime.cash = SharedCash(owner="BTCUSDT")
    runtime.cash.join("BTCUSDT", btc.engine.account)
    for symbol in ("ETHUSDT", "SOLUSDT"):
        session = FakeSession(symbol)
        runtime.cash.join(symbol, session.engine.account)
        runtime.sessions[symbol] = session

    assert runtime.cash.wallet_balance == Decimal("745.71")
    accounts["BTCUSDT"].position = _position("0.05", "84800")
    assert accounts["ETHUSDT"].available_balance == Decimal("745.71") - Decimal("424.000")
    assert accounts["SOLUSDT"].available_balance == accounts["ETHUSDT"].available_balance


def test_a_symbol_that_has_traded_joins_the_wallet_before_the_first_request(
        monkeypatch, tmp_path: Path) -> None:
    """Sessions are built on first use; the wallet cannot wait for that.

    Observed in the preview: three polls seconds apart returned three different wallet figures,
    because each lazily built session added its own realised PnL to the purse as it was first
    visited. A symbol with history on disk contributes to the balance whether or not anybody is
    looking at it, so those are attached at build time. A symbol that has never traded has no
    run directory, contributes nothing, and stays lazy.

    Built with the real `Runtime` and real sessions rather than fakes: the thing under test is
    which symbols end up in the purse after `build`, and a stubbed session cannot answer that.
    """
    import json as _json
    from app.crypto.terminal.api import Runtime

    root = tmp_path / "run"
    root.mkdir(parents=True)
    risk = sorted((Path("data/runtime/crypto/BTCUSDT/reference")).glob("risk_limit_*.json"))[-1]
    config_path = tmp_path / "run_config.json"
    config_path.write_text(_json.dumps({
        "run_id": "wallet-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1341.00",
        "fx_source": "test", "fx_asof_utc": "2026-10-04T00:00:00Z", "fee_version": "v",
        "fee_taker_rate": "0.00055", "fee_maker_rate": "0.0002", "fee_source": "s",
        "fee_effective_date": "2026-09-02", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk.resolve()),
    }))

    # The public feed opens a socket and needs a running loop; this test is about the wallet,
    # so it is stubbed rather than started.
    def no_feed(self, symbol=None):
        return None

    monkeypatch.setattr(Runtime, "feed_for", no_feed)

    # First boot: only the default symbol exists, so only it is in the wallet.
    first = Runtime()
    first.build(config_path, root)
    assert first.cash is not None
    assert sorted(first.cash.members) == [DEFAULT_SYMBOL]

    # ETHUSDT is visited once, which creates its run directory.
    first.session_for("ETHUSDT")
    assert sorted(first.cash.members) == sorted([DEFAULT_SYMBOL, "ETHUSDT"])
    assert (root / "wallet-test-ETHUSDT").is_dir()

    # Second boot: ETHUSDT is in the wallet before anybody asks for it, SOLUSDT is not.
    second = Runtime()
    second.build(config_path, root)
    assert second.cash is not None
    members = sorted(second.cash.members)
    assert "ETHUSDT" in members, "a symbol with history must be in the wallet from the start"
    assert "SOLUSDT" not in members, "a symbol that never traded should stay lazy"

    # And every member reports the one wallet.
    wallets = {symbol: account.wallet_balance
               for symbol, account in second.cash.members.items()}
    assert len(set(wallets.values())) == 1, wallets

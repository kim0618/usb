"""Canned Binance responses and a transport that serves them without a network.

The shapes are the ones the official documentation shows and, where the docs site served a
truncated page, the samples recorded in the ccxt Binance implementation; the *paths* are the ones
probed against the live API on 2026-09-29. A test that passes here therefore proves the code
handles Binance's shape, not that Binance still sends it - that is what the read-only soak with a
real key is for.
"""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse

import httpx

SYMBOL = "BTCUSDT"

EXCHANGE_INFO: dict[str, Any] = {
    "timezone": "UTC", "serverTime": 1_790_000_000_000,
    "symbols": [{
        "symbol": SYMBOL, "pair": SYMBOL, "contractType": "PERPETUAL", "status": "TRADING",
        "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT",
        "pricePrecision": 2, "quantityPrecision": 3,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "556.80",
             "maxPrice": "4529764"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001",
             "maxQty": "120"},
            {"filterType": "MIN_NOTIONAL", "notional": "100"},
        ],
    }],
}

MARK_PRICE = {"symbol": SYMBOL, "markPrice": "83500.00000000", "indexPrice": "83510.00000000",
              "estimatedSettlePrice": "83505.00", "lastFundingRate": "0.00010000",
              "interestRate": "0.00010000", "nextFundingTime": 1_790_640_000_000,
              "time": 1_790_639_000_000}

BOOK_TICKER = {"symbol": SYMBOL, "bidPrice": "83499.90", "bidQty": "3.482",
               "askPrice": "83500.10", "askQty": "1.686", "time": 1_790_639_000_001,
               "lastUpdateId": 11_684_118_382_035}

DEPTH = {"lastUpdateId": 11_684_118_382_040, "E": 1_790_639_000_002, "T": 1_790_639_000_001,
         "bids": [["83499.90", "1.000"], ["83499.80", "2.000"], ["83499.70", "5.000"]],
         "asks": [["83500.10", "1.000"], ["83500.20", "2.000"], ["83500.30", "5.000"]]}

#: The top-level figures are Binance's USD valuation of the account and the asset row is the
#: USDT balance itself. They are deliberately different here (0.9994 apart, the peg measured on
#: the real account on 2026-09-29) so a test can tell which one the code read.
ACCOUNT = {
    "totalInitialMargin": "124.92500000", "totalMaintMargin": "4.99700000",
    "totalWalletBalance": "999.40000000", "totalUnrealizedProfit": "12.49250000",
    "totalMarginBalance": "1011.89250000", "totalPositionInitialMargin": "124.92500000",
    "totalOpenOrderInitialMargin": "0.00000000", "totalCrossWalletBalance": "999.40000000",
    "totalCrossUnPnl": "12.49250000", "availableBalance": "874.47500000",
    "maxWithdrawAmount": "874.47500000",
    "assets": [{"asset": "USDT", "walletBalance": "1000.00000000",
                "unrealizedProfit": "12.50000000", "marginBalance": "1012.50000000",
                "maintMargin": "5.00000000", "initialMargin": "125.00000000",
                "positionInitialMargin": "125.00000000", "openOrderInitialMargin": "0.00000000",
                "crossWalletBalance": "1000.00000000", "crossUnPnl": "12.50000000",
                "availableBalance": "875.00000000", "maxWithdrawAmount": "875.00000000",
                "updateTime": 1_790_638_000_000}],
    "positions": [{"symbol": SYMBOL, "positionSide": "BOTH", "positionAmt": "0.015",
                   "unrealizedProfit": "12.50000000", "isolatedMargin": "0",
                   "notional": "1252.50000000", "isolatedWallet": "0",
                   "initialMargin": "125.25000000", "maintMargin": "5.01000000",
                   "updateTime": 1_790_638_000_000}],
}

POSITION_RISK_LONG = [{
    "symbol": SYMBOL, "positionSide": "BOTH", "positionAmt": "0.015",
    "entryPrice": "82666.66666667", "breakEvenPrice": "82700.12", "markPrice": "83500.00000000",
    "unRealizedProfit": "12.50000000", "liquidationPrice": "75100.10", "isolatedMargin": "0",
    "notional": "1252.50000000", "isolatedWallet": "0", "initialMargin": "125.25000000",
    "maintMargin": "5.01000000", "positionInitialMargin": "125.25000000",
    "openOrderInitialMargin": "0", "adl": "2", "bidNotional": "0", "askNotional": "0",
    "marginAsset": "USDT", "updateTime": 1_790_638_000_000}]

POSITION_RISK_FLAT = [{
    "symbol": SYMBOL, "positionSide": "BOTH", "positionAmt": "0.000", "entryPrice": "0.0",
    "breakEvenPrice": "0.0", "markPrice": "83500.00000000", "unRealizedProfit": "0.00000000",
    "liquidationPrice": "0", "isolatedMargin": "0", "notional": "0", "isolatedWallet": "0",
    "initialMargin": "0", "maintMargin": "0", "positionInitialMargin": "0",
    "openOrderInitialMargin": "0", "adl": "0", "bidNotional": "0", "askNotional": "0",
    "marginAsset": "USDT", "updateTime": 0}]

SYMBOL_CONFIG = [{"symbol": SYMBOL, "marginType": "CROSSED", "isAutoAddMargin": "false",
                  "leverage": 10, "maxNotionalValue": "10000000"}]

COMMISSION_RATE = {"symbol": SYMBOL, "makerCommissionRate": "0.000200",
                   "takerCommissionRate": "0.000400"}

POSITION_MODE_ONE_WAY = {"dualSidePosition": False}
POSITION_MODE_HEDGE = {"dualSidePosition": True}

USER_TRADES = [{"buyer": False, "commission": "0.50100000", "commissionAsset": "USDT",
                "id": 698_759, "maker": False, "orderId": 25_851_813, "price": "83500.10",
                "qty": "0.015", "quoteQty": "1252.50150", "realizedPnl": "12.10000000",
                "side": "BUY", "positionSide": "BOTH", "symbol": SYMBOL, "pair": SYMBOL,
                "time": 1_790_638_000_000}]

INCOME_FUNDING = [{"symbol": SYMBOL, "incomeType": "FUNDING_FEE", "income": "-0.12500000",
                   "asset": "USDT", "info": "FUNDING_FEE", "time": 1_790_600_000_000,
                   "tranId": 9_689_322_392, "tradeId": ""}]

LISTEN_KEY = {"listenKey": "pqia91ma19a5s61cv6a81va65sdf19v8a65a1a5s61cv6a81va65sdf19v8a65a1"}

NEW_ORDER_RESULT = {"clientOrderId": "usbm-open-1790639000000", "cumQty": "0.015",
                    "executedQty": "0.015", "orderId": 22_542_179, "avgPrice": "83500.20",
                    "origQty": "0.015", "price": "0", "reduceOnly": False, "side": "BUY",
                    "positionSide": "BOTH", "status": "FILLED", "stopPrice": "0",
                    "closePosition": False, "symbol": SYMBOL, "timeInForce": "GTC",
                    "type": "MARKET", "origType": "MARKET", "updateTime": 1_790_639_000_010,
                    "workingType": "CONTRACT_PRICE", "priceProtect": False}

SET_LEVERAGE = {"leverage": 5, "maxNotionalValue": "20000000", "symbol": SYMBOL}

SERVER_TIME = {"serverTime": 1_790_639_000_000}


class FakeBinance:
    """An httpx transport that answers the registry's paths and counts what was asked.

    Anything not in `routes` is a 404 with Binance's own error shape, so a test that reaches for
    an endpoint this package should never call fails loudly rather than getting a stub.
    """

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.position_rows: Any = overrides.pop("position_rows", POSITION_RISK_LONG)
        self.position_mode: Any = overrides.pop("position_mode", POSITION_MODE_ONE_WAY)
        self.routes: dict[tuple[str, str], Any] = {
            ("GET", "/fapi/v1/time"): SERVER_TIME,
            ("GET", "/fapi/v1/exchangeInfo"): EXCHANGE_INFO,
            ("GET", "/fapi/v1/premiumIndex"): MARK_PRICE,
            ("GET", "/fapi/v1/ticker/bookTicker"): BOOK_TICKER,
            ("GET", "/fapi/v1/depth"): DEPTH,
            ("GET", "/fapi/v3/account"): ACCOUNT,
            ("GET", "/fapi/v3/positionRisk"): lambda: self.position_rows,
            ("GET", "/fapi/v1/symbolConfig"): SYMBOL_CONFIG,
            ("GET", "/fapi/v1/positionSide/dual"): lambda: self.position_mode,
            ("GET", "/fapi/v1/commissionRate"): COMMISSION_RATE,
            ("GET", "/fapi/v1/userTrades"): USER_TRADES,
            ("GET", "/fapi/v1/income"): INCOME_FUNDING,
            ("POST", "/fapi/v1/listenKey"): LISTEN_KEY,
            ("PUT", "/fapi/v1/listenKey"): {},
            ("DELETE", "/fapi/v1/listenKey"): {},
            ("POST", "/fapi/v1/order"): NEW_ORDER_RESULT,
            ("POST", "/fapi/v1/leverage"): SET_LEVERAGE,
        }
        self.errors: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        #: Failures that are served once and then forgotten, for testing a retry.
        self.transient: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
        self.routes.update(overrides.pop("routes", {}))

    def count(self, path: str) -> int:
        return sum(1 for _, called, _ in self.calls if called == path)

    def fail(self, method: str, path: str, status: int, code: int, msg: str) -> None:
        self.errors[(method, path)] = (status, {"code": code, "msg": msg})

    def fail_once(self, method: str, path: str, status: int, code: int, msg: str) -> None:
        """Refuse the next call to this path, then answer normally. A permanent `fail` cannot
        tell a retry apart from a give-up; this can."""
        self.transient.setdefault((method, path), []).append((status, {"code": code, "msg": msg}))

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            parsed = urlparse(str(request.url))
            params = dict(parse_qsl(parsed.query))
            key = (request.method, parsed.path)
            self.calls.append((request.method, parsed.path, params))
            queued = self.transient.get(key)
            if queued:
                status, body = queued.pop(0)
                return httpx.Response(status, json=body)
            if key in self.errors:
                status, body = self.errors[key]
                return httpx.Response(status, json=body)
            if key not in self.routes:
                return httpx.Response(404, json={"code": -1121, "msg": "Invalid symbol."})
            payload = self.routes[key]
            if callable(payload):
                payload = payload()
            return httpx.Response(200, json=payload,
                                  headers={"X-MBX-USED-WEIGHT-1M": "42"})
        return httpx.MockTransport(handler)


def make_client(fake: FakeBinance | None = None, *, trading_enabled: bool = False,
                key: str = "test-api-key-0123456789", secret: str = "test-api-secret-abcdef") -> Any:
    from app.crypto.live.credentials import Credentials
    from app.crypto.live.rest import BinanceFuturesClient

    fake = fake or FakeBinance()
    client = BinanceFuturesClient(credentials=Credentials(api_key=key, api_secret=secret),
                                  base_url="https://fapi.binance.com", recv_window_ms=5_000,
                                  trading_enabled=trading_enabled, transport=fake.transport())
    return client, fake


def make_config(*, trading_enabled: bool = False, credentials: bool = True) -> Any:
    from app.crypto.live.credentials import load_config

    env = {"BINANCE_LIVE_TRADING_ENABLED": "true" if trading_enabled else "false"}
    if credentials:
        env.update({"BINANCE_API_KEY": "test-api-key-0123456789",
                    "BINANCE_API_SECRET": "test-api-secret-abcdef"})
    return load_config(env)

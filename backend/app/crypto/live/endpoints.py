"""The closed set of Binance endpoints this package may call.

Every row was read off the current official documentation (the `doc` field is the page it came
from) rather than from memory, and the registry is the enforcement point: `resolve` refuses a
name it does not hold, and `guard` refuses a method/path pair that is on the deny list even if
somebody adds it to the registry later. The two checks are separate on purpose - the allow list
says what we meant to use, the deny list says what must never be reachable whatever anyone
means.

Deliberately absent, and denied:

* withdrawal and any `/sapi/` wallet path - the API key is not supposed to have the permission,
  and the code must not be able to use it even if the key someday does;
* Spot (`/api/v3/*`) and Margin order paths;
* internal transfer paths;
* `POST /fapi/v1/marginType` and `POST /fapi/v1/positionSide/dual` - V1 reads the account's
  margin mode and position mode and never changes them (the read paths are allowed).
"""
from __future__ import annotations

from dataclasses import dataclass

#: Signature requirement, in Binance's own vocabulary.
NONE = "NONE"
USER_DATA = "USER_DATA"
USER_STREAM = "USER_STREAM"
TRADE = "TRADE"

DEFAULT_BASE_URL = "https://fapi.binance.com"
#: The futures testnet moved to this host; the legacy `testnet.binancefuture.com` is not used.
TESTNET_BASE_URL = "https://demo-fapi.binance.com"

#: Websocket bases, after Binance's base-URL split.
#:
#: Binance moved websocket traffic onto three bases - `/public` (high-frequency book data),
#: `/market` (mark price, klines, tickers) and `/private` (user data) - and decommissioned the
#: legacy unified `/ws` and `/stream` bases on **2026-04-23**. The notice is explicit that an
#: unmigrated connection keeps receiving `/public` data and that `/market` and `/private` "stop
#: pushing data":
#: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice
#:
#: That is why a legacy user data socket looks healthy and delivers nothing. It is not an error
#: condition: the handshake succeeds, ping/pong is answered, `listenKey` is accepted without
#: complaint, and no frame ever arrives. Measured here on 2026-09-30 with two sockets sharing one
#: listen key while a leverage change fired - legacy `/ws/<key>` 0 frames, `/private/ws?listenKey=`
#: the `ACCOUNT_CONFIG_UPDATE` 130 ms later.
#:
#: The public base is kept for completeness only; the terminal's public tape is Bybit and nothing
#: in this package subscribes to a Binance market stream.
DEFAULT_WS_PUBLIC_URL = "wss://fstream.binance.com/public"
DEFAULT_WS_MARKET_URL = "wss://fstream.binance.com/market"
DEFAULT_WS_PRIVATE_URL = "wss://fstream.binance.com/private/ws"
TESTNET_WS_URL = "wss://demo-fstream.binance.com/private/ws"

_DOC_ROOT = "https://developers.binance.com/docs/derivatives/usds-margined-futures"


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    security: str
    weight: str
    doc: str

    @property
    def signed(self) -> bool:
        return self.security != NONE


def _endpoint(name: str, method: str, path: str, security: str, weight: str, doc: str) -> Endpoint:
    return Endpoint(name=name, method=method, path=path, security=security, weight=weight,
                    doc=f"{_DOC_ROOT}/{doc}")


ENDPOINTS: dict[str, Endpoint] = {endpoint.name: endpoint for endpoint in (
    # ---------------------------------------------------------------- public market data
    _endpoint("server_time", "GET", "/fapi/v1/time", NONE, "1",
              "market-data/rest-api/Check-Server-Time"),
    _endpoint("exchange_info", "GET", "/fapi/v1/exchangeInfo", NONE, "1",
              "market-data/rest-api/Exchange-Information"),
    _endpoint("mark_price", "GET", "/fapi/v1/premiumIndex", NONE, "1 with symbol",
              "market-data/rest-api/Mark-Price"),
    _endpoint("depth", "GET", "/fapi/v1/depth", NONE, "2 at limit 5-50, 5 at 100",
              "market-data/rest-api/Order-Book"),
    # `/fapi/v1/bookTicker` is *not* the path: it answers 404 (Binance's HTML error page). The
    # order-book ticker lives under `/ticker/`, confirmed by a live call on 2026-09-29.
    _endpoint("book_ticker", "GET", "/fapi/v1/ticker/bookTicker", NONE, "2",
              "market-data/rest-api/Symbol-Order-Book-Ticker"),
    _endpoint("klines", "GET", "/fapi/v1/klines", NONE, "limit dependent",
              "market-data/rest-api/Kline-Candlestick-Data"),
    # ---------------------------------------------------------------- account (read)
    _endpoint("account", "GET", "/fapi/v3/account", USER_DATA, "5",
              "account/rest-api/Account-Information-V3"),
    # V3, and the version matters: `/fapi/v1/positionRisk` is gone (404, same HTML error page a
    # nonexistent path returns), `/fapi/v2/positionRisk` and `/fapi/v3/positionRisk` both answer
    # `-2014 API-key format invalid` to an unsigned call, which is an existing signed endpoint
    # refusing the key. Probed live on 2026-09-29.
    #
    # V3 dropped `leverage` and `marginType` from the row, so those two come from
    # `symbol_config` below rather than from here. Reading them off a V2 response instead would
    # work today and would tie the account panel to the older endpoint.
    _endpoint("position_risk", "GET", "/fapi/v3/positionRisk", USER_DATA, "5",
              "trade/rest-api/Position-Information-V3"),
    _endpoint("symbol_config", "GET", "/fapi/v1/symbolConfig", USER_DATA, "5",
              "account/rest-api/Symbol-Config"),
    _endpoint("position_mode", "GET", "/fapi/v1/positionSide/dual", USER_DATA, "30",
              "trade/rest-api/Get-Current-Position-Mode"),
    _endpoint("commission_rate", "GET", "/fapi/v1/commissionRate", USER_DATA, "20",
              "account/rest-api/User-Commission-Rate"),
    # The account's *own* brackets, not the public table: Binance adjusts them per user, and the
    # response carries `notionalCoef` when it has. The leverage selector reads its allowed values
    # from here rather than holding a list, because a hardcoded list is wrong the day the
    # account's risk tier moves and it is wrong in the unsafe direction.
    _endpoint("leverage_bracket", "GET", "/fapi/v1/leverageBracket", USER_DATA, "1",
              "account/rest-api/Notional-and-Leverage-Brackets"),
    # Needed by reconcile: a position that is flat says nothing about a resting order, and the
    # operating screen has to be able to state "open orders 0" from a read rather than from an
    # assumption.
    _endpoint("open_orders", "GET", "/fapi/v1/openOrders", USER_DATA, "1 with symbol, 40 without",
              "trade/rest-api/Current-All-Open-Orders"),
    _endpoint("user_trades", "GET", "/fapi/v1/userTrades", USER_DATA, "5",
              "trade/rest-api/Account-Trade-List"),
    _endpoint("income", "GET", "/fapi/v1/income", USER_DATA, "30",
              "account/rest-api/Get-Income-History"),
    _endpoint("query_order", "GET", "/fapi/v1/order", USER_DATA, "1",
              "trade/rest-api/Query-Order"),
    # ---------------------------------------------------------------- user data stream
    _endpoint("listen_key_create", "POST", "/fapi/v1/listenKey", USER_STREAM, "1",
              "user-data-streams/Start-User-Data-Stream"),
    _endpoint("listen_key_keepalive", "PUT", "/fapi/v1/listenKey", USER_STREAM, "1",
              "user-data-streams/Keepalive-User-Data-Stream"),
    _endpoint("listen_key_close", "DELETE", "/fapi/v1/listenKey", USER_STREAM, "1",
              "user-data-streams/Close-User-Data-Stream"),
    # ---------------------------------------------------------------- trading (flag locked)
    _endpoint("new_order", "POST", "/fapi/v1/order", TRADE,
              "1 order rate limit, 0 IP", "trade/rest-api/New-Order"),
    _endpoint("set_leverage", "POST", "/fapi/v1/leverage", TRADE, "1",
              "trade/rest-api/Change-Initial-Leverage"),
)}

#: Method/path pairs that must never be reachable from this process.
DENIED: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/fapi/v1/marginType"),
    ("POST", "/fapi/v1/positionSide/dual"),
    ("POST", "/fapi/v1/multiAssetsMargin"),
    ("POST", "/fapi/v1/positionMargin"),
})

#: Any path containing one of these is refused regardless of method. `transfer` covers both the
#: futures internal transfer and the universal transfer paths; `withdraw` covers the wallet ones.
DENIED_FRAGMENTS: tuple[str, ...] = ("withdraw", "transfer", "capital", "sub-account")

#: Only the USDⓈ-M futures REST prefix is callable. This is what keeps Spot (`/api/v3/`), Margin
#: (`/sapi/v1/margin/`) and the wallet API structurally out of reach.
ALLOWED_PREFIX = "/fapi/"


class EndpointForbidden(RuntimeError):
    """A path outside the allow list, or on the deny list, was asked for."""

    def __init__(self, method: str, path: str, reason: str) -> None:
        super().__init__(f"{method} {path} is not callable from this process: {reason}")
        self.method = method
        self.path = path
        self.reason = reason


def guard(method: str, path: str) -> None:
    """Raise unless this exact method/path is permitted. Called on every request."""
    upper = method.upper()
    lowered = path.lower()
    if not path.startswith(ALLOWED_PREFIX):
        raise EndpointForbidden(upper, path, f"outside {ALLOWED_PREFIX} (USDⓈ-M futures only)")
    for fragment in DENIED_FRAGMENTS:
        if fragment in lowered:
            raise EndpointForbidden(upper, path, f"denied fragment {fragment!r}")
    if (upper, path) in DENIED:
        raise EndpointForbidden(upper, path, "on the deny list")
    known = {(endpoint.method, endpoint.path) for endpoint in ENDPOINTS.values()}
    if (upper, path) not in known:
        raise EndpointForbidden(upper, path, "not in the endpoint registry")


def resolve(name: str) -> Endpoint:
    endpoint = ENDPOINTS.get(name)
    if endpoint is None:
        raise EndpointForbidden("?", name, "unknown endpoint name")
    guard(endpoint.method, endpoint.path)
    return endpoint


def registry_view() -> list[dict[str, str]]:
    """The endpoint table, for the report and for the UI's provenance panel."""
    return [{"name": item.name, "method": item.method, "path": item.path,
             "security": item.security, "weight": item.weight, "doc": item.doc}
            for item in sorted(ENDPOINTS.values(), key=lambda row: (row.security, row.path))]

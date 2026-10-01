"""Binance USDⓈ-M Futures REST client.

Three things make it different from a thin `httpx` wrapper, and all three are safety rather than
convenience:

* **Every call goes through the endpoint registry.** A request is built from a registry name, so
  a path that is not in `endpoints.ENDPOINTS` cannot be reached by typing a string at the call
  site, and `endpoints.guard` re-checks the method/path pair on the way out.
* **TRADE endpoints need the client itself to be armed.** `trading_enabled=False` (the default)
  refuses a TRADE call before a request is constructed. That is a second, independent gate from
  the `BINANCE_LIVE_TRADING_ENABLED` environment flag the adapter checks, so arming live orders
  by accident takes two mistakes rather than one.
* **Nothing that leaves this class can carry a credential.** Every exception message and every
  telemetry string passes through `Credentials.redact`, and the request URL is never included in
  an error: a signed URL contains the signature and, for a mistake upstream, could contain more.

Clock: signed requests carry `timestamp`, and Binance rejects one that is outside `recvWindow` of
*its* clock - and, in the other direction, one that is more than a second in its future whatever
`recvWindow` says. The local clock in this environment has been observed seconds off, so the
offset to the exchange is measured with the public time endpoint (`sync_clock`, which the LIVE
runtime calls before the first account read) and applied to every signed request. A `-1021`
nevertheless re-measures the offset and repeats the request once, for reads only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from .credentials import Credentials
from .endpoints import TRADE, Endpoint, EndpointForbidden, resolve
from .signing import RECV_WINDOW_PARAM, TIMESTAMP_PARAM, canonical, signed_query

API_KEY_HEADER = "X-MBX-APIKEY"
USER_AGENT = "usb-crypto-binance-live/1"

#: Binance error codes worth naming. Everything else is surfaced with its own code.
CODE_TIMESTAMP_OUTSIDE_RECV_WINDOW = -1021
CODE_INVALID_SIGNATURE = -1022
CODE_INVALID_API_KEY = -2015


class TradingDisabled(RuntimeError):
    """A TRADE endpoint was requested on a client that was not armed for trading."""


@dataclass
class BinanceError(RuntimeError):
    """A refusal from Binance, or a transport failure, with the credential stripped out."""
    status: int
    code: int | None
    message: str

    def __post_init__(self) -> None:
        super().__init__(f"binance error {self.code} (HTTP {self.status}): {self.message}")

    @property
    def is_clock_skew(self) -> bool:
        return self.code == CODE_TIMESTAMP_OUTSIDE_RECV_WINDOW

    @property
    def is_auth(self) -> bool:
        return self.code in {CODE_INVALID_SIGNATURE, CODE_INVALID_API_KEY} or self.status in {401, 403}

    def view(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code, "message": self.message}


@dataclass
class RestTelemetry:
    requests: int = 0
    signed_requests: int = 0
    trade_requests: int = 0
    errors: int = 0
    rate_limited: int = 0
    used_weight_1m: int | None = None
    order_count_1m: int | None = None
    last_error: str | None = None
    last_request_ms: int | None = None
    clock_offset_ms: int = 0
    #: How many times a -1021 forced the offset to be re-measured. Non-zero means this machine's
    #: clock drifted far enough for Binance to refuse a signed read, which is worth seeing.
    clock_resyncs: int = 0
    #: Every registry name this client has actually called, in call order, deduplicated. The
    #: safety report reads it to show that no trading endpoint was touched during a read-only run.
    called: list[str] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {"requests": self.requests, "signed_requests": self.signed_requests,
                "trade_requests": self.trade_requests, "errors": self.errors,
                "rate_limited": self.rate_limited, "used_weight_1m": self.used_weight_1m,
                "order_count_1m": self.order_count_1m, "last_error": self.last_error,
                "last_request_ms": self.last_request_ms, "clock_offset_ms": self.clock_offset_ms,
                "clock_resyncs": self.clock_resyncs, "endpoints_called": list(self.called)}


class BinanceFuturesClient:
    """Synchronous client. Used from threads and from `asyncio.to_thread`, never inside the loop."""

    def __init__(self, *, credentials: Credentials | None, base_url: str,
                 recv_window_ms: int, trading_enabled: bool = False,
                 transport: httpx.BaseTransport | None = None, timeout: float = 10.0) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.recv_window_ms = recv_window_ms
        self.trading_enabled = trading_enabled
        self.telemetry = RestTelemetry()
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport,
                                    headers={"User-Agent": USER_AGENT})

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BinanceFuturesClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ helpers

    def _redact(self, text: str) -> str:
        return self.credentials.redact(text) if self.credentials is not None else text

    def now_ms(self) -> int:
        """Local clock corrected by the measured offset to the exchange."""
        return int(time.time() * 1000) + self.telemetry.clock_offset_ms

    def sync_clock(self) -> int:
        """Measure the offset to Binance's clock. Returns the offset in milliseconds.

        Half the round trip is *not* subtracted: the correction only has to bring the timestamp
        inside `recvWindow`, and guessing at network asymmetry would add an error of its own.
        """
        local_before = int(time.time() * 1000)
        payload = self.call("server_time")
        server = int(payload["serverTime"])
        self.telemetry.clock_offset_ms = server - local_before
        return self.telemetry.clock_offset_ms

    def _record_limits(self, response: httpx.Response) -> None:
        for header, attribute in (("X-MBX-USED-WEIGHT-1M", "used_weight_1m"),
                                  ("X-MBX-ORDER-COUNT-1M", "order_count_1m")):
            raw = response.headers.get(header)
            if raw is not None:
                try:
                    setattr(self.telemetry, attribute, int(raw))
                except ValueError:
                    pass

    # ------------------------------------------------------------------ the one request path

    def call(self, name: str, params: dict[str, Any] | None = None) -> Any:
        endpoint: Endpoint = resolve(name)
        if endpoint.security == TRADE and not self.trading_enabled:
            raise TradingDisabled(
                f"{endpoint.name} is a TRADE endpoint and this client is not armed for trading")
        try:
            return self._dispatch(endpoint, params)
        except BinanceError as exc:
            if not exc.is_clock_skew or endpoint.security == TRADE:
                raise
            # -1021 is a statement about *this* machine's clock, and Binance refuses a timestamp
            # more than a second in its future whatever `recvWindow` says. Re-measuring the
            # offset and asking once more turns a drifted clock into a recoverable condition
            # instead of a dead panel.
            #
            # A TRADE endpoint is never retried, and that is the reason the branch is written as
            # an exclusion rather than a preference: the first request may have reached the
            # matching engine before the clock check, and a second one would be a second order.
            self.telemetry.clock_resyncs += 1
            self.sync_clock()
            return self._dispatch(endpoint, params)

    def call_close_only(self, name: str, params: dict[str, Any]) -> Any:
        """Narrow permission used by an explicitly armed exit guard."""
        endpoint = resolve(name)
        if (endpoint.name != "new_order" or endpoint.security != TRADE
                or params.get("type") != "MARKET" or params.get("reduceOnly") != "true"):
            raise TradingDisabled(
                "close-only permission accepts only reduceOnly MARKET new_order")
        return self._dispatch(endpoint, params)

    def _dispatch(self, endpoint: Endpoint, params: dict[str, Any] | None = None) -> Any:
        """One request, exactly as asked. Gates and retries belong to `call`."""
        request_params = dict(params or {})
        headers: dict[str, str] = {}
        if endpoint.signed:
            if self.credentials is None:
                raise BinanceError(status=0, code=None,
                                   message=f"{endpoint.name} needs credentials and none are loaded")
            request_params[TIMESTAMP_PARAM] = self.now_ms()
            request_params.setdefault(RECV_WINDOW_PARAM, self.recv_window_ms)
            query = signed_query(request_params, secret=self.credentials.api_secret)
            headers[API_KEY_HEADER] = self.credentials.api_key
        else:
            query = canonical(request_params)
        url = f"{endpoint.path}?{query}" if query else endpoint.path

        self.telemetry.requests += 1
        if endpoint.signed:
            self.telemetry.signed_requests += 1
        if endpoint.security == TRADE:
            self.telemetry.trade_requests += 1
        if endpoint.name not in self.telemetry.called:
            self.telemetry.called.append(endpoint.name)
        self.telemetry.last_request_ms = int(time.time() * 1000)

        try:
            response = self._client.request(endpoint.method, url, headers=headers)
        except httpx.HTTPError as exc:
            self.telemetry.errors += 1
            # `str(exc)` on an httpx error can contain the request URL, which for a signed
            # request carries the signature. Only the exception class is kept.
            message = f"transport failure: {type(exc).__name__}"
            self.telemetry.last_error = message
            raise BinanceError(status=0, code=None, message=message) from None
        self._record_limits(response)
        if response.status_code in {418, 429}:
            self.telemetry.rate_limited += 1
        if response.status_code >= 400:
            self.telemetry.errors += 1
            body = self._error_body(response)
            self.telemetry.last_error = self._redact(f"{endpoint.name}: {body['message']}")
            raise BinanceError(status=response.status_code, code=body["code"],
                               message=self._redact(body["message"]))
        try:
            return response.json()
        except ValueError:
            self.telemetry.errors += 1
            message = f"{endpoint.name} returned a body that is not JSON"
            self.telemetry.last_error = message
            raise BinanceError(status=response.status_code, code=None, message=message) from None

    @staticmethod
    def _error_body(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {"code": None, "message": f"HTTP {response.status_code}"}
        if isinstance(payload, dict):
            code = payload.get("code")
            return {"code": int(code) if isinstance(code, (int, str)) and str(code).lstrip("-").isdigit() else None,
                    "message": str(payload.get("msg") or f"HTTP {response.status_code}")}
        return {"code": None, "message": f"HTTP {response.status_code}"}


def decimal(value: Any) -> Decimal:
    """Binance sends every figure as a string. Parsed in Decimal so no money passes through a
    float, which is the same rule the paper engine follows."""
    return Decimal(str(value))


__all__ = ["BinanceFuturesClient", "BinanceError", "TradingDisabled", "EndpointForbidden",
           "RestTelemetry", "decimal"]

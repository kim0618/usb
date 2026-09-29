"""LIVE (Binance real account) routes for the manual terminal.

Registered onto the same FastAPI instance `api.py` builds, in a separate module for the reason
`server.py` states: two sessions editing one `create_app()` in a shared working tree do not
merge. Nothing in `api.py` or `server.py` changes shape because of this file.

Route naming: everything LIVE sits under `/api/crypto/binance/`. The pre-existing
`/api/crypto/live` is the *paper* position's fast poll and keeps its name and its meaning; the
two were never the same thing and are not merged here.

Three properties hold for every route below:

* **The paper session is not touched.** No handler reads `runtime.session` except to borrow the
  run's fixed KRW rate for display, and none of them calls a method that writes to it.
* **Orders are refused while the flag is off.** `POST /order` and `POST /leverage` build the
  order, log the intent to the LIVE mirror and return `409 LIVE_TRADING_DISABLED`. That is the
  whole trading path, exercised end to end except for the send.
* **No credential reaches a response.** The only key-derived value that is ever serialised is
  the fingerprint from `LiveConfig.view()`.
"""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..live.account import AccountReader
from ..live.adapter import BinanceLiveAdapter
from ..live.credentials import LiveConfig, client_armed, load_config, load_credentials
from ..live.credentials import CredentialsMissing
from ..live.endpoints import registry_view
from ..live.mirror import LiveMirror, default_path
from ..live.orders import CLOSE, OPEN, LiveOrderRouter, OrderRefused
from ..live.rest import BinanceError, BinanceFuturesClient, TradingDisabled
from ..live.stream import UserDataStream
from .api import app, error, jsonable, runtime

LIVE_ROOT_ENV = "CRYPTO_LIVE_ROOT"
STREAM_ENV = "CRYPTO_LIVE_USER_STREAM"
SIDES = ("LONG", "SHORT")


class LiveOrderRequest(BaseModel):
    side: str = ""
    intent: str = OPEN
    qty: str | None = None
    notional_usdt: str | None = None
    confirmed: bool = False


class LiveLeverageRequest(BaseModel):
    leverage: str


class LiveRuntime:
    """Builds the adapter once, on the first LIVE request, and owns its shutdown.

    Lazy on purpose: the terminal starts and serves the paper screen on a machine with no
    Binance key at all, and constructing an HTTP client for an account that does not exist
    would turn a missing key into a startup failure.
    """

    def __init__(self) -> None:
        self.adapter: BinanceLiveAdapter | None = None
        self.config: LiveConfig | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def stream_enabled(self) -> bool:
        return os.environ.get(STREAM_ENV, "on").lower() not in {"off", "0", "false"}

    def build(self) -> BinanceLiveAdapter | None:
        """None when there is no key. The status route reports that; it is not an error."""
        with self._lock:
            if self.adapter is not None:
                return self.adapter
            try:
                config = load_config()
            except ValueError as exc:
                self.error = str(exc)
                return None
            self.config = config
            if not config.credentials_present:
                return None
            try:
                credentials = load_credentials()
            except CredentialsMissing as exc:
                self.error = str(exc)
                return None
            client = BinanceFuturesClient(credentials=credentials, base_url=config.base_url,
                                          recv_window_ms=config.recv_window_ms,
                                          # The second of the two gates, and a variable of its
                                          # own (`BINANCE_LIVE_CLIENT_ARMED`) rather than a
                                          # second reading of `BINANCE_LIVE_TRADING_ENABLED`:
                                          # both must be set for an order to be sent, so arming
                                          # a real account still takes two deliberate acts.
                                          trading_enabled=client_armed())
            try:
                # Before the first signed read, not lazily on its failure. Binance refuses a
                # timestamp more than a second in its own future, so a machine whose clock runs
                # fast - a WSL host resuming from sleep does - would otherwise serve a panel
                # blocked on CLOCK_SKEW while the key, the IP and the account were all fine.
                client.sync_clock()
            except BinanceError as exc:
                # Not fatal: the account read that follows reports an unreachable exchange as a
                # blocker with its own message, and that is a better error than a dead route.
                self.error = f"clock sync failed: {exc.message}"
            root = Path(os.environ.get(LIVE_ROOT_ENV, "data/runtime/crypto/live"))
            mirror = LiveMirror(path=default_path(config.fingerprint, root),
                                account_fingerprint=config.fingerprint)
            reader = AccountReader(client, config)
            router = LiveOrderRouter(reader=reader, client=client, config=config, mirror=mirror)
            adapter = BinanceLiveAdapter(config=config, client=client, reader=reader,
                                         router=router, mirror=mirror)
            if self.stream_enabled():
                stream = UserDataStream(client=client, config=config, mirror=mirror,
                                        on_change=adapter.on_stream_change)
                adapter.stream = stream
                stream.start()
            self.adapter = adapter
            return adapter

    def shutdown(self) -> None:
        adapter = self.adapter
        if adapter is None:
            return
        if adapter.stream is not None:
            adapter.stream.stop()
        adapter.client.close()
        self.adapter = None


live_runtime = LiveRuntime()

_previous_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_live(application: Any):
    async with _previous_lifespan(application) as state:
        try:
            yield state
        finally:
            live_runtime.shutdown()


app.router.lifespan_context = _lifespan_with_live


def _krw_rate() -> Decimal | None:
    """The paper run's fixed rate, borrowed for display only. Read, never written; absent when
    no paper run is configured, and the LIVE panel then shows USDT alone."""
    session = runtime.session
    if session is None:
        return None
    return session.config.fx.krw_per_usdt


def _krw(view: dict[str, Any], keys: tuple[str, ...], rate: Decimal | None) -> dict[str, Any] | None:
    if rate is None:
        return None
    out: dict[str, Any] = {}
    for key in keys:
        value = view.get(key)
        if isinstance(value, Decimal):
            out[key] = value * rate
    return out


def _decimal(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not a number: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


def _config_view() -> dict[str, Any]:
    config = live_runtime.config
    if config is None:
        try:
            config = load_config()
            live_runtime.config = config
        except ValueError as exc:
            return {"error": str(exc)}
    return config.view()


@app.get("/api/crypto/binance/status")
async def binance_status() -> Any:
    """Whether LIVE can be offered at all, and why not when it cannot.

    Answers without credentials and without touching the network when there is no key: the
    switch in the UI needs to know it should stay on PAPER before anything is attempted.
    """
    adapter = live_runtime.build()
    body: dict[str, Any] = {"source": "BINANCE_LIVE", "config": _config_view(),
                            "runtime_error": live_runtime.error,
                            "endpoints": registry_view()}
    if adapter is None:
        body.update({"available": False, "ready": False,
                     "blockers": [{"code": "CREDENTIALS_MISSING",
                                   "message": "BINANCE_API_KEY / BINANCE_API_SECRET가 없습니다. PAPER만 사용할 수 있습니다."}],
                     "gates": {"armed": False, "env_flag": False, "client_armed": False,
                               "env_flag_name": "BINANCE_LIVE_TRADING_ENABLED"}})
        return jsonable(body)
    snapshot = adapter.snapshot()
    body.update({"available": True, "ready": snapshot.ready,
                 "blockers": [item.view() for item in snapshot.blockers],
                 "gates": adapter.router.gate_view(),
                 "stream": adapter.stream.view() if adapter.stream is not None else None,
                 "mirror": adapter.mirror.view() if adapter.mirror is not None else None,
                 "rest": adapter.client.telemetry.view()})
    return jsonable(body)


@app.get("/api/crypto/binance/account")
async def binance_account(force: bool = False) -> Any:
    """Balance, position, leverage, margin mode and mark, as Binance reports them."""
    adapter = live_runtime.build()
    if adapter is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    snapshot = adapter.snapshot(force=force)
    view = snapshot.view()
    rate = _krw_rate()
    if snapshot.balance is not None:
        view["krw"] = _krw(snapshot.balance.view(),
                           ("wallet_balance", "available_balance", "margin_balance",
                            "unrealized_pnl"), rate)
    if snapshot.position is not None:
        view["position_krw"] = _krw(snapshot.position.view(), ("unrealized_pnl", "notional"), rate)
    view["krw_per_usdt"] = rate
    view["krw_note"] = "표시용 환산입니다. LIVE 회계 정본은 USDT이며 환율은 페이퍼 런에 고정된 값입니다."
    view["gates"] = adapter.router.gate_view()
    view["stream"] = adapter.stream.view() if adapter.stream is not None else None
    return jsonable(view)


@app.get("/api/crypto/binance/preview")
async def binance_preview(side: str | None = None, qty: str | None = None,
                          notional_usdt: str | None = None) -> Any:
    """Round-trip cost for one or both sides, priced on Binance's book."""
    adapter = live_runtime.build()
    if adapter is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    requested = SIDES if side is None else (side.upper(),)
    for candidate in requested:
        if candidate not in SIDES:
            return error(400, "UNKNOWN_SIDE", f"side must be LONG or SHORT, got {candidate!r}")
    if qty in (None, "") and notional_usdt in (None, ""):
        return error(400, "QTY_REQUIRED", "qty 또는 notional_usdt가 필요합니다.")
    sides = {candidate: adapter.get_order_preview(candidate, qty, notional_usdt)
             for candidate in requested}
    snapshot = adapter.snapshot()
    return jsonable({"source": "BINANCE_LIVE", "symbol": adapter.config.symbol,
                     "krw_per_usdt": _krw_rate(), "sides": sides,
                     "mark_price": snapshot.mark.mark_price if snapshot.mark else None,
                     "fetched_at_ms": snapshot.fetched_at_ms})


@app.get("/api/crypto/binance/fills")
async def binance_fills(limit: int = 50) -> Any:
    """Recent fills with Binance's own commission and realised PnL."""
    adapter = live_runtime.build()
    if adapter is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    try:
        rows = adapter.get_recent_fills(min(limit, 200))
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    return jsonable({"source": "BINANCE_LIVE", "total": len(rows), "fills": rows,
                     "authority": "binance GET /fapi/v1/userTrades"})


@app.get("/api/crypto/binance/funding")
async def binance_funding(limit: int = 50) -> Any:
    adapter = live_runtime.build()
    if adapter is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    try:
        rows = adapter.get_funding(min(limit, 200))
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    return jsonable({"source": "BINANCE_LIVE", "total": len(rows), "funding": rows,
                     "authority": "binance GET /fapi/v1/income?incomeType=FUNDING_FEE"})


@app.get("/api/crypto/binance/events")
async def binance_events(limit: int = 100) -> Any:
    """The LIVE audit mirror's tail. Never a source for the screen's figures."""
    adapter = live_runtime.build()
    if adapter is None or adapter.mirror is None:
        return jsonable({"source": "BINANCE_LIVE", "total": 0, "events": []})
    events = adapter.mirror.recent(min(limit, 500))
    return jsonable({"source": "BINANCE_LIVE", "total": adapter.mirror.count, "events": events,
                     "role": adapter.mirror.view()["role"]})


@app.post("/api/crypto/binance/order")
async def binance_order(request: LiveOrderRequest) -> Any:
    """Build the order, record the intent, and refuse to send it while the flag is off."""
    adapter = live_runtime.build()
    if adapter is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    intent = request.intent.upper()
    if intent not in (OPEN, CLOSE):
        return error(400, "UNKNOWN_INTENT", f"intent must be OPEN or CLOSE, got {intent!r}")
    side = request.side.upper()
    try:
        plan = adapter.plan_order(side, intent, request.qty, request.notional_usdt)
    except OrderRefused as exc:
        return error(409, exc.code, exc.message)
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    try:
        result = adapter.router.submit(plan)
    except OrderRefused as exc:
        # The expected V1 outcome: the plan is returned with the refusal so the UI can show
        # exactly what would have been sent.
        return error(409, exc.code, exc.message)
    except TradingDisabled as exc:
        return error(409, "LIVE_TRADING_DISABLED", str(exc))
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    adapter.resync()
    return jsonable({"plan": plan.view(), "response": result})


@app.post("/api/crypto/binance/leverage")
async def binance_leverage(request: LiveLeverageRequest) -> Any:
    adapter = live_runtime.build()
    if adapter is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    try:
        value = _decimal(request.leverage, "leverage")
    except ValueError as exc:
        return error(400, "INVALID_LEVERAGE", str(exc))
    if value <= 0 or value != value.to_integral_value():
        return error(400, "INVALID_LEVERAGE", "레버리지는 양의 정수여야 합니다.")
    try:
        return jsonable(adapter.set_leverage(str(value)))
    except OrderRefused as exc:
        return error(409, exc.code, exc.message)
    except (TradingDisabled, BinanceError) as exc:
        return error(409 if isinstance(exc, TradingDisabled) else 502,
                     "LIVE_TRADING_DISABLED" if isinstance(exc, TradingDisabled) else "BINANCE_ERROR",
                     str(exc))


__all__ = ["live_runtime"]

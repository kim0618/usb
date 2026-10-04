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
import time
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from fastapi.responses import JSONResponse

from ..live.account import AccountReader
from ..live.adapter import BinanceLiveAdapter
from ..live.arm import ArmRefused, ArmSession
from ..live.credentials import LiveConfig, client_armed, load_config, load_credentials
from ..live.credentials import CredentialsMissing
from ..live.endpoints import registry_view
from ..live.leverage import (ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE, RESTRICTION_CODES,
                             AccountScopeAdvisory, BootstrapOutcome, LeverageCapability,
                             account_scope_from_mirror)
from ..live.exit_guard import ExitGuard
from ..live.mirror import LiveEvent, LiveMirror, default_path
from ..live.orders import CLOSE, OPEN, LiveOrderRouter, OrderRefused
from ..live.performance import ManualLivePerformance
from ..live.models import LiveFieldMissing, LivePosition
from ..live.rest import BinanceError, BinanceFuturesClient, TradingDisabled
from ..live.stream import UserDataStream
from ..symbols import (DEFAULT_SYMBOL, SUPPORTED_SYMBOLS, SymbolNotSupported,
                            base_asset, resolve as resolve_symbol)
from .api import app, error, jsonable, runtime

LIVE_ROOT_ENV = "CRYPTO_LIVE_ROOT"
STREAM_ENV = "CRYPTO_LIVE_USER_STREAM"
SIDES = ("LONG", "SHORT")

#: The steps offered in the UI. Presentation only - every one of them is checked against the
#: account's own bracket table before it is shown, and the table is what decides. A ladder is
#: kept rather than showing all 150 values because leverage is a risk setting and a row of
#: meaningful steps is easier to choose correctly from than a slider.
#:
#: 100 being in this tuple is not a claim that the account can use it. Two further filters stand
#: between the tuple and an enabled button: the account's own bracket table (`max_leverage`), and
#: what Binance has actually been observed to refuse (`live.leverage.LeverageCapability`). On the
#: real account on 2026-10-01 the bracket table reached 150 while Binance refused 50 with code
#: -4300, so the table alone is not capability and this ladder alone is not either.
LEVERAGE_LADDER = (1, 2, 3, 5, 10, 20, 50, 100)

#: V1 reads the margin mode and does not offer to change it. `POST /fapi/v1/marginType` is on the
#: endpoint deny list, and taking it off would mean a write whose failure modes (an open
#: position, a resting order) all have to be handled on a screen whose job this week is manual
#: orders. Cross/Isolated is changed in the Binance app; this screen states which one is active.
MARGIN_TYPE_NOTE = ("마진 모드는 이 화면에서 바꾸지 않고 Binance에서 설정한 값을 그대로 표시합니다. "
                    "변경은 Binance 앱/웹에서 하세요.")


class LiveOrderRequest(BaseModel):
    side: str = ""
    intent: str = OPEN
    qty: str | None = None
    notional_usdt: str | None = None
    confirmed: bool = False
    #: Which instrument the button was under. Optional so an existing single-symbol client is
    #: unchanged, and carried all the way into `LiveOrderRouter.plan` where it is checked
    #: against the four symbols the order path already holds rather than used to pick one.
    symbol: str | None = None


class LiveLeverageRequest(BaseModel):
    leverage: str
    symbol: str | None = None


class LiveArmRequest(BaseModel):
    """The confirmation phrase is required verbatim; see `live.arm.CONFIRMATION`."""
    confirmation: str = ""
    note: str | None = None
    ttl_s: int | None = None


class ExitGuardRequest(BaseModel):
    take_profit_krw: str
    stop_loss_krw: str
    symbol: str | None = None


@dataclass
class SymbolRuntime:
    """Everything that is about exactly one instrument, in one object.

    The multi-symbol terminal is a dictionary of these, not a symbol-aware adapter. The
    difference is the isolation property: an `AccountReader` here was constructed with a frozen
    config naming one symbol, so there is no argument any caller can pass that would make it
    read another. A BTC figure cannot reach the ETH screen by being handed the wrong parameter,
    because the parameter does not exist.

    What is deliberately *not* here, and is shared instead: the HTTP client (one rate-limit
    budget and one clock sync for the account), the audit mirror (one file, so the order of
    events across symbols is the order they happened), the arm session (arming is permission
    over the account, not over an instrument) and the user data stream (Binance gives one socket
    per account).
    """
    symbol: str
    adapter: BinanceLiveAdapter
    reader: AccountReader
    router: LiveOrderRouter
    capability: LeverageCapability
    bootstrap: BootstrapOutcome | None = None
    performance: ManualLivePerformance | None = None


class LiveRuntime:
    """Builds the shared account objects once, then one `SymbolRuntime` per symbol on demand.

    Lazy on purpose, twice over. The terminal starts and serves the paper screen on a machine
    with no Binance key at all, so constructing an HTTP client for an account that does not
    exist would turn a missing key into a startup failure. And a symbol is built on its first
    request rather than all three at boot, because each one costs an `exchangeInfo`, a
    `commissionRate` and a `symbolConfig` read that an operator who only trades BTC should not
    pay for.

    `self.adapter`, `self.performance`, `self.leverage_capability` and `self.leverage_bootstrap`
    keep their old names and their old meaning - the *default* symbol's - so every existing
    caller and test that reaches for them, including the ones that inject a fake adapter, works
    unchanged.
    """

    def __init__(self) -> None:
        self.adapter: BinanceLiveAdapter | None = None
        self.config: LiveConfig | None = None
        self.error: str | None = None
        #: Built with the adapter and owned here rather than by the adapter, because the arm
        #: state has to survive a snapshot being thrown away and must not be reachable from
        #: anything that merely reads the account.
        self.arm: ArmSession | None = None
        self.exit_guard: ExitGuard | None = None
        self.performance: ManualLivePerformance | None = None
        #: What this account has actually been allowed to set, learned from Binance's refusals
        #: and persisted beside the mirror so a restart does not cost another refused click.
        self.leverage_capability: LeverageCapability | None = None
        #: What the startup scan of the audit mirror concluded. Reported on the leverage route
        #: so a quiet startup is still explicable.
        self.leverage_bootstrap: BootstrapOutcome | None = None
        #: Per-symbol runtimes, keyed by the canonical symbol. The default symbol's entry and
        #: `self.adapter` are the same adapter, never two.
        self.symbols: dict[str, SymbolRuntime] = {}
        #: The shared objects, built with the first symbol and reused by every later one.
        self.client: BinanceFuturesClient | None = None
        self.mirror: LiveMirror | None = None
        self.root: Path | None = None
        self.stream: UserDataStream | None = None
        self._lock = threading.Lock()

    def stream_enabled(self) -> bool:
        return os.environ.get(STREAM_ENV, "on").lower() not in {"off", "0", "false"}

    def build(self, symbol: str | None = None) -> BinanceLiveAdapter | None:
        """None when there is no key. The status route reports that; it is not an error.

        `symbol` defaults to the deployment's default, which is what makes every pre-existing
        call site - all of which pass nothing - keep meaning exactly what it meant.
        """
        entry = self.symbol_runtime(symbol)
        return entry.adapter if entry is not None else None

    def symbol_runtime(self, symbol: str | None = None) -> SymbolRuntime | None:
        """The per-symbol runtime, built on first use. Raises `SymbolNotSupported` off-list."""
        requested = resolve_symbol(symbol)
        with self._lock:
            existing = self.symbols.get(requested)
            # `self.adapter` is the default symbol's adapter and it is the authority on what
            # the default symbol currently is, even when something outside this class set it.
            # Checked before the cache rather than after, because an adapter swapped in from
            # outside must take effect immediately: a cached entry holding the previous one
            # would serve the wrong account for the rest of the process.
            if (self.adapter is not None and requested == self._default_symbol()
                    and (existing is None or existing.adapter is not self.adapter)):
                # Not cached. An adapter this runtime did not build is not this runtime's to
                # keep: caching it would outlive whatever swapped it in, and `_adopt` reads
                # nothing and allocates one small object, so rebuilding it per call is free.
                return self._adopt(self.adapter)
            if existing is not None:
                return existing
            if not self._build_shared():
                return None
            assert self.config is not None and self.client is not None and self.mirror is not None
            try:
                config = self.config.for_symbol(requested)
            except SymbolNotSupported as exc:
                self.error = exc.message
                return None
            entry = self._build_symbol(config)
            self.symbols[requested] = entry
            if requested == self._default_symbol():
                # The old attribute names keep pointing at the default symbol.
                self.adapter = entry.adapter
                self.performance = entry.performance
                self.leverage_capability = entry.capability
                self.leverage_bootstrap = entry.bootstrap
                self._build_exit_guard(entry)
            return entry

    def _default_symbol(self) -> str:
        return self.config.symbol if self.config is not None else DEFAULT_SYMBOL

    def _adopt(self, adapter: BinanceLiveAdapter) -> SymbolRuntime:
        """Wrap an adapter that was built elsewhere in the per-symbol shape.

        Nothing is constructed and nothing is read: this only gives the registry a handle on an
        adapter it did not create, so a route that asks by symbol and a test that injected by
        attribute are looking at the same object.
        """
        router = adapter.router
        return SymbolRuntime(symbol=adapter.config.symbol, adapter=adapter,
                             reader=router.reader, router=router,
                             capability=router.capability,
                             bootstrap=self.leverage_bootstrap,
                             performance=self.performance)

    def _build_shared(self) -> bool:
        """The account-wide objects: config, client, clock, audit mirror, arm session, stream.

        Built once for the whole account however many symbols are on screen. One client because
        Binance's rate limit is per IP and per account, not per symbol, and three clients would
        each think they had the whole budget. One mirror because the audit file's value is that
        it is the order things happened in. One arm session because arming is permission over
        the account.
        """
        if self.client is not None and self.mirror is not None:
            return True
        try:
            config = load_config()
        except ValueError as exc:
            self.error = str(exc)
            return False
        self.config = config
        if not config.credentials_present:
            return False
        try:
            credentials = load_credentials()
        except CredentialsMissing as exc:
            self.error = str(exc)
            return False
        client = BinanceFuturesClient(credentials=credentials, base_url=config.base_url,
                                      recv_window_ms=config.recv_window_ms,
                                      # Starts false. The `ArmSession` constructed below owns
                                      # this flag from here on and writes it through on every
                                      # transition: `BINANCE_LIVE_CLIENT_ARMED` set means
                                      # armed from boot (the local validation path), unset
                                      # means the operator arms a bounded session by hand.
                                      # Either way an order still needs both this and
                                      # `BINANCE_LIVE_TRADING_ENABLED`.
                                      trading_enabled=False)
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
        self.arm = ArmSession(client=client, env_armed=client_armed())
        mirror.append(LiveEvent.DISARM, reason="BOOT", env_armed=client_armed(),
                      symbols=list(config.symbols),
                      note="프로세스 기동. 세션 무장은 항상 해제 상태로 시작한다.")
        self.client = client
        self.mirror = mirror
        self.root = root
        if self.stream_enabled():
            # One socket for the account, fanned out to every adapter. `on_change` is this
            # runtime's method rather than one adapter's, because an `ACCOUNT_UPDATE` carries
            # no symbol and a balance change is an input to every symbol's Safe MAX.
            stream = UserDataStream(client=client, config=config, mirror=mirror,
                                    on_change=self._on_stream_change)
            self.stream = stream
            stream.start()
        return True

    def _on_stream_change(self, event: str) -> None:
        """Mark every built adapter. Called from the stream thread; marks only, never reads."""
        for entry in list(self.symbols.values()):
            entry.adapter.on_stream_change(event)

    def _capability_path(self, symbol: str) -> Path:
        """The default symbol keeps the original filename, every other symbol gets its own.

        Not a shared file with a symbol key inside it: the existing file is the BTC account's
        learned restriction and rewriting its shape at startup would risk the one piece of
        knowledge that survives a deploy.
        """
        assert self.root is not None
        if symbol == self._default_symbol():
            return self.root / "leverage_capability.json"
        return self.root / f"leverage_capability_{symbol}.json"

    def _performance_path(self, symbol: str) -> Path:
        assert self.root is not None
        if symbol == self._default_symbol():
            return self.root / "manual_performance.json"
        return self.root / f"manual_performance_{symbol}.json"

    def _build_symbol(self, config: LiveConfig) -> SymbolRuntime:
        assert self.client is not None and self.mirror is not None
        symbol = config.symbol
        reader = AccountReader(self.client, config)
        capability = LeverageCapability(path=self._capability_path(symbol))
        # The refusal that greys 50x and 100x out is already in the audit file from the
        # first time it happened, so the restriction is recovered here rather than being
        # relearned by making the operator send one more write Binance will reject. The
        # stored capability wins when it has one; this only speaks when it does not. Read
        # only: it opens a local file and makes no Binance request. Scanned per symbol, so a
        # refusal recorded against BTCUSDT does not become ETHUSDT's restriction by being in
        # the same file - see `/leverage`'s account-scope advisory for the case where Binance's
        # own sentence says the limit is the account's rather than the instrument's.
        bootstrap = capability.bootstrap_from_mirror(self.mirror.path, symbol=symbol,
                                                     default_symbol=self._default_symbol())
        router = LiveOrderRouter(reader=reader, client=self.client, config=config,
                                 mirror=self.mirror, arm=self.arm, capability=capability)
        adapter = BinanceLiveAdapter(config=config, client=self.client, reader=reader,
                                     router=router, mirror=self.mirror)
        adapter.stream = self.stream
        performance = ManualLivePerformance(
            reader=reader, path=self._performance_path(symbol),
            krw_rate=lambda: _krw_rate())
        return SymbolRuntime(symbol=symbol, adapter=adapter, reader=reader, router=router,
                             capability=capability, bootstrap=bootstrap,
                             performance=performance)

    def _build_exit_guard(self, entry: SymbolRuntime) -> None:
        """The automatic close-only guard, on the default symbol only.

        Left exactly where it was on purpose: AUTO is out of scope for the multi-symbol step, so
        the guard still watches one instrument and its `view()` names which one. The screen uses
        that name to show the panel on that symbol and to say "BTC 전용" on the others, rather
        than showing a BTC guard under an ETH tab.
        """
        if self.exit_guard is not None or self.root is None:
            return
        self.exit_guard = ExitGuard(adapter=entry.adapter, path=self.root / "exit_guard.json",
                                    krw_rate=lambda: _krw_rate())
        self.exit_guard.start()

    def shutdown(self) -> None:
        if self.exit_guard is not None:
            self.exit_guard.stop()
        if self.stream is not None:
            self.stream.stop()
        if self.client is not None:
            self.client.close()
        self.symbols = {}
        self.adapter = None
        self.client = None
        self.mirror = None
        self.stream = None
        self.exit_guard = None
        self.performance = None
        self.leverage_capability = None


live_runtime = LiveRuntime()

_previous_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_live(application: Any):
    async with _previous_lifespan(application) as state:
        try:
            guard_path = Path(os.environ.get(
                LIVE_ROOT_ENV, "data/runtime/crypto/live")) / "exit_guard.json"
            if guard_path.exists():
                live_runtime.build()
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


def _event_symbol_name(event: dict[str, Any]) -> str | None:
    """The symbol a mirror line is about, or None when it is account-wide.

    `None` is not "unknown": boot, arm, disarm and stream-state lines genuinely belong to the
    account rather than to an instrument, and they stay visible under every tab.
    """
    named = event.get("symbol")
    if isinstance(named, str) and named:
        return named
    plan = event.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("symbol"), str) and plan["symbol"]:
        return plan["symbol"]
    response = event.get("response")
    if isinstance(response, dict) and isinstance(response.get("symbol"), str) and response["symbol"]:
        return response["symbol"]
    return None


def _symbol_error(exc: SymbolNotSupported) -> JSONResponse:
    return JSONResponse(status_code=400,
                        content={"error": {"code": exc.code, "message": exc.message}})


def _entry(symbol: str | None) -> tuple[SymbolRuntime | None, JSONResponse | None]:
    """The per-symbol runtime for a request, or the response that explains why there is none.

    One function for all three refusals - an unsupported symbol, no API key, a config that will
    not load - so every LIVE route answers them identically and a new route cannot accidentally
    omit one.
    """
    try:
        entry = live_runtime.symbol_runtime(symbol)
    except SymbolNotSupported as exc:
        return None, _symbol_error(exc)
    if entry is None:
        return None, error(503, "LIVE_UNAVAILABLE",
                           live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    return entry, None


@app.get("/api/crypto/binance/status")
async def binance_status(symbol: str | None = None) -> Any:
    """Whether LIVE can be offered at all, and why not when it cannot.

    Answers without credentials and without touching the network when there is no key: the
    switch in the UI needs to know it should stay on PAPER before anything is attempted.

    `symbols` is always present, even with no key, because the tab strip has to be able to
    render before the first account read and must not invent its own list.
    """
    try:
        requested = resolve_symbol(symbol)
    except SymbolNotSupported as exc:
        return _symbol_error(exc)
    config_view = _config_view()
    permitted = config_view.get("symbols") or list(SUPPORTED_SYMBOLS)
    adapter = None if requested not in permitted else live_runtime.build(requested)
    body: dict[str, Any] = {"source": "BINANCE_LIVE", "config": config_view,
                            "symbol": requested, "symbols": permitted,
                            "default_symbol": DEFAULT_SYMBOL,
                            "base_asset": base_asset(requested),
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
                 "arm": live_runtime.arm.view() if live_runtime.arm is not None else None,
                 "stream": adapter.stream.view() if adapter.stream is not None else None,
                 "mirror": adapter.mirror.view() if adapter.mirror is not None else None,
                 "rest": adapter.client.telemetry.view()})
    return jsonable(body)


@app.get("/api/crypto/binance/account")
async def binance_account(force: bool = False, symbol: str | None = None) -> Any:
    """Balance, position, leverage, margin mode and mark, as Binance reports them.

    The balance is the account's and is therefore the same on every symbol; the position, the
    leverage, the margin mode, the mark and the filters are the requested symbol's and are read
    through that symbol's own reader.
    """
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
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
    # The unit a quantity on this screen is denominated in, from Binance's own `baseAsset`
    # rather than from the symbol string, so the panel never has to parse "BTCUSDT" itself.
    view["base_asset"] = (snapshot.filters.base_asset if snapshot.filters is not None
                          else base_asset(adapter.config.symbol))
    view["symbols"] = list(adapter.config.symbols)
    return jsonable(view)


@app.get("/api/crypto/binance/preview")
async def binance_preview(side: str | None = None, qty: str | None = None,
                          notional_usdt: str | None = None, symbol: str | None = None) -> Any:
    """Round-trip cost for one or both sides, priced on Binance's book."""
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
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
async def binance_fills(limit: int = 50, symbol: str | None = None) -> Any:
    """Recent fills with Binance's own commission and realised PnL, for one symbol."""
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        rows = adapter.get_recent_fills(min(limit, 200))
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    return jsonable({"source": "BINANCE_LIVE", "symbol": adapter.config.symbol,
                     "total": len(rows), "fills": rows,
                     "authority": "binance GET /fapi/v1/userTrades"})


@app.get("/api/crypto/binance/funding")
async def binance_funding(limit: int = 50, symbol: str | None = None) -> Any:
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        rows = adapter.get_funding(min(limit, 200))
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    return jsonable({"source": "BINANCE_LIVE", "symbol": adapter.config.symbol,
                     "total": len(rows), "funding": rows,
                     "authority": "binance GET /fapi/v1/income?incomeType=FUNDING_FEE"})


@app.get("/api/crypto/binance/events")
async def binance_events(limit: int = 100, symbol: str | None = None) -> Any:
    """The LIVE audit mirror's tail. Never a source for the screen's figures.

    One file for the whole account, so the tail is every symbol's events in the order they
    happened - that interleaving is the file's value and is not split up. `symbol` filters the
    *view* to lines that name it; lines that name none (boot, arm, disarm, stream state) are
    account-wide and are always included, because hiding them under a symbol tab would make the
    audit trail look like it had gaps.
    """
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    if adapter.mirror is None:
        return jsonable({"source": "BINANCE_LIVE", "total": 0, "events": []})
    events = adapter.mirror.recent(min(limit, 500))
    if symbol not in (None, ""):
        named = entry.symbol
        events = [item for item in events
                  if _event_symbol_name(item) in (None, named)]
    return jsonable({"source": "BINANCE_LIVE", "symbol": entry.symbol,
                     "total": adapter.mirror.count, "events": events,
                     "role": adapter.mirror.view()["role"]})


@app.post("/api/crypto/binance/order")
async def binance_order(request: LiveOrderRequest) -> Any:
    """Build the order, record the intent, and refuse to send it while the flag is off.

    `request.symbol` selects the per-symbol runtime *and* is carried into the router, which
    checks it against the symbol its own config, reader, snapshot, filters and the position
    Binance just reported all name. Selecting and verifying are deliberately two separate
    things: the selection could be wrong, and the verification is what notices.
    """
    entry, failure = _entry(request.symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    intent = request.intent.upper()
    if intent not in (OPEN, CLOSE):
        return error(400, "UNKNOWN_INTENT", f"intent must be OPEN or CLOSE, got {intent!r}")
    side = request.side.upper()
    preflight: dict[str, Any] | None = None
    if intent == OPEN:
        try:
            preflight = adapter.preflight_open(side, request.qty, request.notional_usdt)
        except BinanceError as exc:
            return error(502, "BINANCE_ERROR", exc.message)
        if not preflight.get("feasible"):
            reject_code = str(preflight.get("reject_code") or "UNSAFE_SIZE")
            capacity_changed = reject_code in {
                "INSUFFICIENT_MARGIN", "NO_LIQUIDITY", "NOTIONAL_ABOVE_MAXIMUM",
                "QTY_ABOVE_MARKET_MAXIMUM",
            }
            return JSONResponse(status_code=409, content=jsonable({
                "error": {"code": "ORDER_CAPACITY_CHANGED" if capacity_changed else reject_code,
                          "message": ("주문 가능 수량이 변경되었습니다." if capacity_changed else
                                      preflight.get("reject_message") or "주문할 수 없습니다.")},
                "safe_max_qty": preflight.get("safe_max_qty", 0),
                "preflight": preflight,
            }))
    try:
        plan = adapter.plan_order(side, intent, request.qty, request.notional_usdt,
                                  symbol=request.symbol or entry.symbol)
    except OrderRefused as exc:
        return error(409, exc.code, exc.message)
    except BinanceError as exc:
        if (preflight is not None and exc.code == -2019 and adapter.mirror is not None):
            adapter.mirror.append(
                LiveEvent.ORDER_REFUSED, stage="EXCHANGE_MARGIN_AUDIT", code=str(exc.code),
                message=exc.message, requested_qty=request.qty,
                available_balance=preflight.get("available_balance"),
                required_margin=preflight.get("required_margin"),
                expected_fill=preflight.get("expected_entry_vwap"),
                account_timestamp_ms=preflight.get("account_timestamp_ms"),
                final_resync_timestamp_ms=preflight.get("final_resync_timestamp_ms"))
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
    return jsonable({"symbol": entry.symbol, "plan": plan.view(), "response": result})


@app.post("/api/crypto/binance/leverage")
async def binance_leverage(request: LiveLeverageRequest) -> Any:
    """Change one symbol's leverage. Binance scopes `POST /fapi/v1/leverage` to a symbol, so
    this writes that symbol's setting and nothing else; the other symbols keep theirs."""
    entry, failure = _entry(request.symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        value = _decimal(request.leverage, "leverage")
    except ValueError as exc:
        return error(400, "INVALID_LEVERAGE", str(exc))
    if value <= 0 or value != value.to_integral_value():
        return error(400, "INVALID_LEVERAGE", "레버리지는 양의 정수여야 합니다.")
    try:
        return jsonable({"symbol": entry.symbol, **adapter.set_leverage(str(value))})
    except OrderRefused as exc:
        return error(409, exc.code, exc.message)
    except TradingDisabled as exc:
        return error(409, "LIVE_TRADING_DISABLED", str(exc))
    except BinanceError as exc:
        if exc.status == 0:
            return error(504, "LEVERAGE_TIMEOUT",
                         "Binance 응답 시간이 초과되었습니다. 기존 레버리지를 유지합니다.")
        if exc.code in RESTRICTION_CODES:
            # Reached only when the refusal arrived but nothing could be learned from it; the
            # router turns a parseable one into `OrderRefused` above, which is the 409 branch.
            return error(409, ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE,
                         "현재 계정에서 선택한 레버리지를 아직 사용할 수 없습니다.")
        if exc.code == -4028:
            return error(409, "UNSUPPORTED_LEVERAGE",
                         "Binance 계정에서 선택한 레버리지를 사용할 수 없습니다.")
        if exc.code in {-4202, -4203, -4205, -4206}:
            return error(409, "ACCOUNT_LEVERAGE_LIMIT",
                         "Binance 계정 제한으로 선택한 레버리지를 사용할 수 없습니다.")
        suffix = f" (오류 코드 {exc.code})" if exc.code is not None else ""
        return error(502, "BINANCE_LEVERAGE_REJECTED",
                     f"Binance에서 레버리지 변경을 거부했습니다{suffix}.")


@app.get("/api/crypto/binance/position")
async def binance_position_card(symbol: str | None = None) -> Any:
    """The held position with its own history and a close-now estimate. Read only."""
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        card = adapter.get_position_card()
        rate = _krw_rate()
        converted = _krw(card, ("unrealized_pnl", "net_if_closed", "exposure",
                                "initial_margin"), rate)
        if converted is not None:
            card["krw"] = converted
            card["krw_per_usdt"] = rate
        return jsonable({"source": "BINANCE_LIVE", **card})
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)


@app.get("/api/crypto/binance/sizing")
async def binance_sizing(symbol: str | None = None) -> Any:
    """The quick-size ladder, computed on the server from Binance's own figures.

    Read only. It sends nothing and arms nothing; it answers what could be placed if the
    operator chose to, and the order route still applies every one of these rules again.

    Safe MAX for one symbol is bounded by the *account's* `availableBalance`, which Binance has
    already reduced by the initial margin every other symbol's open position is holding. So a
    held BTC position shrinks the ETH ladder without this route knowing anything about BTC, and
    the figure is Binance's own rather than a subtraction performed here.
    """
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        return jsonable({"source": "BINANCE_LIVE", **adapter.get_sizing()})
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)


@app.get("/api/crypto/binance/positions")
async def binance_positions() -> Any:
    """Every symbol's open position in one answer, for the strip above the tabs.

    One unfiltered `GET /fapi/v3/positionRisk` (weight 5) rather than one call per symbol: the
    response already carries every position the account holds, and `LivePosition.from_rows` is
    the same parser the per-symbol account route uses, so a figure here cannot disagree with the
    figure on the tab. Three separate calls would cost 15 weight and could return three
    different instants, which is exactly how a summary ends up contradicting the detail.

    `others` is the part nobody asked for and the screen needs anyway. The account is one
    Binance Futures wallet and the operator can open a position in the Binance app on a symbol
    this terminal does not list. That position holds margin, which this screen's Safe MAX
    already reflects, and leaving it invisible would mean a strip titled "OPEN POSITIONS" that
    omits an open position. It is reported read-only and separately from the tradable symbols,
    with no controls attached.
    """
    entry, failure = _entry(None)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        rows = adapter.client.call("position_risk", {})
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    if not isinstance(rows, list):
        return error(502, "BINANCE_ERROR", "positionRisk 응답 형식이 바뀌었습니다.")
    permitted = list(adapter.config.symbols)
    rate = _krw_rate()
    positions: list[dict[str, Any]] = []
    for symbol in permitted:
        try:
            position = LivePosition.from_rows(rows, symbol)
        except (LiveFieldMissing, ValueError) as exc:
            positions.append({"symbol": symbol, "available": False,
                              "reject_code": "RESPONSE_SHAPE_CHANGED",
                              "reject_message": str(exc)})
            continue
        view = position.view()
        positions.append({**view, "available": True, "base_asset": base_asset(symbol),
                          "krw": _krw(view, ("unrealized_pnl", "notional"), rate)})
    listed = set(permitted)
    others: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("symbol")
        if not isinstance(name, str) or name in listed:
            continue
        try:
            amount = Decimal(str(row.get("positionAmt") or "0"))
        except (InvalidOperation, ValueError):
            continue
        if amount == 0:
            continue
        others.append({"symbol": name, "signed_qty": amount,
                       "qty": abs(amount), "side": "LONG" if amount > 0 else "SHORT",
                       "unrealized_pnl": row.get("unRealizedProfit"),
                       "notional": row.get("notional"), "tradable_here": False})
    return jsonable({
        "source": "BINANCE_LIVE", "symbols": permitted, "default_symbol": DEFAULT_SYMBOL,
        "positions": positions, "others": others,
        "krw_per_usdt": rate,
        "fetched_at_ms": int(time.time() * 1000),
        "authority": "binance GET /fapi/v3/positionRisk (all symbols, one read)",
        "others_note": "이 터미널에서 거래하지 않는 심볼의 보유 포지션입니다. 조회만 가능합니다.",
    })


@app.get("/api/crypto/binance/arm")
async def binance_arm_state() -> Any:
    """Whether this process is armed for manual LIVE orders, and for how much longer."""
    adapter = live_runtime.build()
    if adapter is None or live_runtime.arm is None:
        return jsonable({"armed": False, "available": False,
                         "reason": live_runtime.error or "Binance API 키가 설정되지 않았습니다."})
    return jsonable({**live_runtime.arm.view(), "available": True,
                     "capability": _config_view().get("trading_enabled")})


@app.post("/api/crypto/binance/arm")
async def binance_arm(request: LiveArmRequest) -> Any:
    """Open a bounded manual-trading window.

    Arming is not the same thing as being allowed to trade: `BINANCE_LIVE_TRADING_ENABLED` is
    still the capability, and a deployment without it stays refused at the router's gate. This
    route only supplies the second half, and only for `ttl_s`.
    """
    adapter = live_runtime.build()
    if adapter is None or live_runtime.arm is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    if not (live_runtime.config and live_runtime.config.trading_enabled):
        # Refused here rather than armed-but-useless, so the screen says the true reason.
        if adapter.mirror is not None:
            adapter.mirror.append(LiveEvent.ARM_REFUSED, code="LIVE_TRADING_DISABLED")
        return error(409, "LIVE_TRADING_DISABLED",
                     "이 서버는 BINANCE_LIVE_TRADING_ENABLED=false 입니다. 무장할 수 없습니다.")
    try:
        state = live_runtime.arm.arm(confirmation=request.confirmation, note=request.note,
                                     ttl_s=request.ttl_s)
    except ArmRefused as exc:
        if adapter.mirror is not None:
            adapter.mirror.append(LiveEvent.ARM_REFUSED, code=exc.code, message=exc.message)
        return error(400, exc.code, exc.message)
    if adapter.mirror is not None:
        adapter.mirror.append(LiveEvent.ARM, ttl_s=state["ttl_s"],
                              expires_at_ms=state["expires_at_ms"], note=request.note)
    return jsonable({**state, "available": True, "gates": adapter.router.gate_view()})


@app.post("/api/crypto/binance/disarm")
async def binance_disarm() -> Any:
    adapter = live_runtime.build()
    if adapter is None or live_runtime.arm is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    state = live_runtime.arm.disarm()
    if adapter.mirror is not None:
        adapter.mirror.append(LiveEvent.DISARM, reason="MANUAL")
    return jsonable({**state, "available": True, "gates": adapter.router.gate_view()})


@app.get("/api/crypto/binance/performance")
async def binance_manual_performance(symbol: str | None = None) -> Any:
    """Realised performance for one symbol, from that symbol's own trade cursor.

    Per symbol rather than per account: the cursor pages `userTrades`, which Binance scopes to a
    symbol, and a cumulative figure that mixed three instruments would not be comparable with
    the position card beside it.
    """
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    if entry.performance is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    return jsonable({**entry.performance.refresh(), "symbol": entry.symbol})


@app.get("/api/crypto/binance/exit-guard")
async def binance_exit_guard(symbol: str | None = None) -> Any:
    """The automatic close-only guard. One symbol only, and it names which one.

    AUTO is explicitly out of scope for the multi-symbol step, so the guard was not made
    symbol-aware. What changed is that it now *refuses* rather than answering: asked about a
    symbol it does not watch, it returns `guard_symbol` and an unavailable reason, so the screen
    can say "BTC 전용" instead of rendering a BTC guard under an ETH tab.
    """
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    if live_runtime.exit_guard is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    guard_symbol = live_runtime.exit_guard.adapter.config.symbol
    if entry.symbol != guard_symbol:
        return jsonable({"state": "UNAVAILABLE", "enabled": False, "available": False,
                         "symbol": None, "guard_symbol": guard_symbol,
                         "requested_symbol": entry.symbol,
                         "unavailable_reason": "AUTO_SINGLE_SYMBOL",
                         "unavailable_message":
                             f"자동청산은 {guard_symbol} 전용입니다. {entry.symbol}에는 적용되지 않습니다."})
    return jsonable({**live_runtime.exit_guard.view(), "available": True,
                     "guard_symbol": guard_symbol})


@app.post("/api/crypto/binance/exit-guard")
async def configure_binance_exit_guard(request: ExitGuardRequest) -> Any:
    entry, failure = _entry(request.symbol)
    if failure is not None:
        return failure
    assert entry is not None
    if live_runtime.exit_guard is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    guard_symbol = live_runtime.exit_guard.adapter.config.symbol
    if entry.symbol != guard_symbol:
        # Refused rather than applied to the guard's own symbol. Configuring an ETH stop that
        # silently armed a BTC close is the single worst outcome available here.
        return error(409, "AUTO_SINGLE_SYMBOL",
                     f"자동청산은 {guard_symbol} 전용입니다. {entry.symbol}에는 설정할 수 없습니다.")
    if not (live_runtime.config and live_runtime.config.trading_enabled):
        return error(409, "LIVE_TRADING_DISABLED",
                     "이 서버는 LIVE close-only 권한이 비활성화돼 있습니다.")
    try:
        take_profit = _decimal(request.take_profit_krw, "take_profit_krw")
        stop_loss = _decimal(request.stop_loss_krw, "stop_loss_krw")
        return jsonable(live_runtime.exit_guard.configure(
            take_profit_krw=take_profit, stop_loss_krw=stop_loss))
    except ValueError as exc:
        return error(400, "INVALID_EXIT_GUARD", str(exc))
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)


@app.delete("/api/crypto/binance/exit-guard")
async def disable_binance_exit_guard() -> Any:
    adapter = live_runtime.build()
    if adapter is None or live_runtime.exit_guard is None:
        return error(503, "LIVE_UNAVAILABLE",
                     live_runtime.error or "Binance API 키가 설정되지 않았습니다.")
    return jsonable(live_runtime.exit_guard.disable())


@app.get("/api/crypto/binance/leverage")
async def binance_leverage_options(symbol: str | None = None) -> Any:
    """What leverage this account may actually select on one symbol, read from Binance.

    The offered values are the intersection of a short presentation ladder with the account's
    own bracket table, plus whatever Binance currently reports as set - so a leverage the
    operator is already on is never missing from the row they are looking at, even if it is not
    one of the ladder's steps.

    Three different things decide a step on a multi-symbol screen, and they are reported
    separately rather than merged into one boolean:

    * `max_leverage` - the **symbol's** bracket table. Per symbol, read per symbol.
    * `unavailable` - what Binance has refused **on this symbol**. Per symbol by construction:
      each symbol has its own `LeverageCapability` with its own file, and the audit scan that
      seeds it skips lines naming another symbol.
    * `account_scope` - a refusal whose own sentence says the limit belongs to the **account**
      (the 30-day Futures registration cap). Reported on every symbol because Binance said it
      about the account, and carrying the symbol it was heard on so the screen can show its
      provenance. This is not BTC's capability being copied into ETH's: the per-symbol stores
      are untouched and this field is derived from the audit file on every request.
    """
    entry, failure = _entry(symbol)
    if failure is not None:
        return failure
    assert entry is not None
    adapter = entry.adapter
    try:
        payload = adapter.client.call("leverage_bracket", {"symbol": adapter.config.symbol})
    except BinanceError as exc:
        return error(502, "BINANCE_ERROR", exc.message)
    row = payload[0] if isinstance(payload, list) and payload else (payload or {})
    brackets = row.get("brackets") or []
    if not brackets:
        return error(502, "BINANCE_ERROR", "레버리지 구간표를 읽지 못했습니다.")
    maximum = max(int(item["initialLeverage"]) for item in brackets)
    snapshot = adapter.snapshot()
    current = snapshot.symbol_config.leverage if snapshot.symbol_config else None
    options = sorted({value for value in LEVERAGE_LADDER if value <= maximum}
                     | ({int(current)} if current else set()))
    # The bracket table says what the *symbol* allows. What this *account* allows is only known
    # from Binance's own refusals, so the steps it has refused are reported alongside the
    # options rather than quietly dropped: a button that is there and greyed with a reason is
    # what an operator can act on, and the reason expires by itself.
    capability = adapter.router.capability
    # Derived from the shared audit file on every request rather than held in state, so it
    # expires at the instant Binance named without anything having to notice.
    advisory: AccountScopeAdvisory | None = None
    if adapter.mirror is not None:
        try:
            advisory = account_scope_from_mirror(adapter.mirror.path)
        except OSError:
            advisory = None
    account_scope = None
    if advisory is not None:
        blocked = sorted(value for value in options if advisory.applies_to(value))
        if blocked:
            account_scope = {
                **advisory.view(),
                "blocked": blocked,
                "messages": {str(value): advisory.message(value) for value in blocked},
                "authority": "binance POST /fapi/v1/leverage refusal whose own reason names "
                             "the Futures account's registration age, not the instrument",
            }
    return jsonable({
        "symbol": adapter.config.symbol,
        "symbols": list(adapter.config.symbols),
        "base_asset": base_asset(adapter.config.symbol),
        "account_scope": account_scope,
        "current": current,
        "margin_type": snapshot.symbol_config.margin_type if snapshot.symbol_config else None,
        "max_leverage": maximum,
        "options": options,
        "brackets": brackets,
        "notional_coef": row.get("notionalCoef"),
        "unavailable": capability.unavailable(options),
        "capability": capability.view(options),
        "capability_bootstrap": (entry.bootstrap.view()
                                 if entry.bootstrap is not None else None),
        "authority": "binance GET /fapi/v1/leverageBracket",
        "capability_authority": "binance POST /fapi/v1/leverage refusals observed by this account",
        "margin_type_note": MARGIN_TYPE_NOTE,
    })


__all__ = ["live_runtime"]

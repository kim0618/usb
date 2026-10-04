"""Standalone FastAPI app for the crypto paper terminal.

It is a separate ASGI app rather than a route on the equity API on purpose: the equity app's
startup owns a database, a Kiwoom profile and the Strategy A/E runtimes, and none of that
should have to be up for a public-data paper terminal. This app imports nothing from the
equity side.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..paper import PAPER_ENGINE_VERSION, sizing
from ..paper.book import NoLiquidity
from ..paper.config import PaperRunConfig, build_config
from ..paper.engine import OrderRejected
from ..paper.analytics import reconcile, summarize
from ..paper.account import SharedCash
from ..paper.instrument import InstrumentSpec, RiskTierTable
from ..paper.state import TransitionRejected
from ..paper.c1_auto import (C1AutoController, MANUAL_CLOSE_DURING_AUTO, MANUAL_SOURCE)
from ..symbols import (DEFAULT_SYMBOL, SUPPORTED_SYMBOLS, SymbolNotSupported,
                            resolve as resolve_symbol)
from .chart_history import BybitChartHistory
from .feed import BybitPublicFeed
from .session import PaperSession

DEFAULT_ROOT = Path("data/runtime/crypto/paper")
CONFIG_ENV = "CRYPTO_PAPER_RUN_CONFIG"
chart_history = BybitChartHistory()

REQUIRED_CONFIG_FIELDS = (
    "run_id", "starting_capital_krw", "fx_krw_per_usdt", "fx_source", "fx_asof_utc",
    "fee_version", "fee_taker_rate", "fee_maker_rate", "fee_source", "fee_effective_date",
    "slippage_model", "slippage_bps", "leverage", "risk_limit_path",
)


class ConfigMissing(RuntimeError):
    pass


#: A per-symbol PAPER run needs two measured files, and neither can be guessed from the symbol
#: name: the instrument spec (tick, quantity step, minimum notional) and the risk-limit tier
#: table. SOLUSDT's quantity step is 0.1 and BTCUSDT's is 0.001, so inheriting the default spec
#: would size a SOL order a hundred steps wrong. The run config may name them explicitly under
#: `symbols`; failing that they are discovered by the convention the BTC files already follow,
#: and the resolved path and its sha256 are recorded in the run's own config snapshot.
#:
#: Where they are looked for is deliberately not one relative path. The deployed unit runs with
#: `WorkingDirectory=/root/usb_runtime/crypto_paper`, not the repository root, which is exactly
#: why `CRYPTO_PAPER_ROOT` and `CRYPTO_PAPER_RUN_CONFIG` are absolute in that unit. A bare
#: relative `data/runtime/crypto` therefore resolves to a directory that does not exist there,
#: and the symptom would be ETHUSDT and SOLUSDT quietly reporting "no measurement" in
#: production while every test passed locally. So: an explicit variable first, then beside the
#: run state, then the repository-relative path, and the refusal names every path it tried.
SYMBOL_REFERENCE_ENV = "CRYPTO_SYMBOL_REFERENCE_ROOT"
SYMBOL_REFERENCE_ROOT = Path("data/runtime/crypto")


def symbol_reference_roots(paper_root: Path | None = None) -> list[Path]:
    """Every directory a symbol's measured responses may live in, in priority order."""
    roots: list[Path] = []
    named = (os.environ.get(SYMBOL_REFERENCE_ENV) or "").strip()
    if named:
        roots.append(Path(named))
    if paper_root is not None:
        # `CRYPTO_PAPER_ROOT` is the run state's directory. The reference files are the same
        # kind of thing - measured once, read at startup - so a deployment that ships them
        # beside the run needs no new variable.
        roots.append(Path(paper_root) / "reference")
        roots.append(Path(paper_root).parent)
    roots.append(SYMBOL_REFERENCE_ROOT)
    seen: set[str] = set()
    ordered: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            ordered.append(root)
    return ordered


def _candidates(symbol: str, prefix: str, paper_root: Path | None) -> list[Path]:
    return [root / symbol / "reference" for root in symbol_reference_roots(paper_root)]


def _discover(symbol: str, prefix: str, paper_root: Path | None = None) -> Path | None:
    """The newest saved response of one kind for one symbol, or None.

    Newest by filename, which carries the capture timestamp. `None` rather than a fallback to
    another symbol's file: a missing measurement makes that symbol's PAPER unavailable with a
    reason, and that is the correct outcome - the alternative is a screen that sizes orders off
    numbers nobody measured.
    """
    for folder in _candidates(symbol, prefix, paper_root):
        if not folder.is_dir():
            continue
        found = sorted(folder.glob(f"{prefix}_*.json"))
        if found:
            return found[-1]
    return None


class SymbolReferenceMissing(RuntimeError):
    """No measured instrument spec or risk table for a symbol. That symbol gets no PAPER run."""


def symbol_run_config(base: PaperRunConfig, symbol: str,
                      named: dict[str, Any] | None = None,
                      paper_root: Path | None = None) -> tuple[PaperRunConfig, RiskTierTable]:
    """The same run - same capital, same FX fixing, same fee schedule - on another instrument.

    Same economics on purpose: the three screens are one paper experiment, so a figure differing
    between them has to be the instrument's doing and not a different fee table's. What does
    change is everything that is a property of the instrument, and all of it comes from a saved
    Bybit response rather than from this function.

    The run id is suffixed rather than reused. Each symbol therefore gets its own directory, its
    own ledger and its own input tape, and the existing BTCUSDT run keeps its id untouched -
    which is the whole of "do not damage the existing BTC ledger": no file it owns is opened by
    another symbol's session.
    """
    def looked_in() -> str:
        return ", ".join(str(folder) for folder in _candidates(symbol, "", paper_root))

    spec_path = Path(named["instrument_path"]) if named and named.get("instrument_path") \
        else _discover(symbol, "instruments_info", paper_root)
    risk_path = Path(named["risk_limit_path"]) if named and named.get("risk_limit_path") \
        else _discover(symbol, "risk_limit", paper_root)
    if spec_path is None or not spec_path.exists():
        raise SymbolReferenceMissing(
            f"{symbol}: 측정된 instruments-info 응답이 없어 PAPER를 구성하지 않습니다. "
            f"찾아본 경로: {looked_in()} (또는 {SYMBOL_REFERENCE_ENV} 설정)")
    if risk_path is None or not risk_path.exists():
        raise SymbolReferenceMissing(
            f"{symbol}: 측정된 risk-limit 응답이 없어 PAPER를 구성하지 않습니다. "
            f"찾아본 경로: {looked_in()} (또는 {SYMBOL_REFERENCE_ENV} 설정)")
    instrument = InstrumentSpec.from_file(spec_path, symbol)
    tiers = RiskTierTable.from_file(risk_path)
    # The continuity the margin formula depends on is re-verified per symbol rather than assumed
    # from BTCUSDT's table. A table that fails it would make every maintenance-margin figure on
    # that screen wrong, so the symbol is refused instead.
    tiers.verify_continuity()
    config = PaperRunConfig(
        run_id=f"{base.run_id}-{symbol}", starting_capital_krw=base.starting_capital_krw,
        fx=base.fx, fees=base.fees, slippage=base.slippage, leverage=base.leverage,
        risk_limit_source=str(risk_path), risk_limit_sha256=tiers.source_sha256,
        instrument=instrument)
    return config, tiers


def load_run_config(path: Path) -> tuple[PaperRunConfig, RiskTierTable]:
    """Every provenance field is mandatory. A missing fee or FX figure stops the run here
    rather than being filled in with a plausible number."""
    if not path.exists():
        raise ConfigMissing(f"run config not found: {path}")
    raw = json.loads(path.read_text())
    missing = [field for field in REQUIRED_CONFIG_FIELDS if not str(raw.get(field, "")).strip()]
    if missing:
        raise ConfigMissing(f"run config is missing required fields: {', '.join(missing)}")
    tiers = RiskTierTable.from_file(Path(raw["risk_limit_path"]))
    config = build_config(
        run_id=raw["run_id"], starting_capital_krw=raw["starting_capital_krw"],
        fx_krw_per_usdt=raw["fx_krw_per_usdt"], fx_source=raw["fx_source"],
        fx_asof_utc=raw["fx_asof_utc"], fee_version=raw["fee_version"],
        fee_taker_rate=raw["fee_taker_rate"], fee_maker_rate=raw["fee_maker_rate"],
        fee_source=raw["fee_source"], fee_effective_date=raw["fee_effective_date"],
        slippage_model=raw["slippage_model"], slippage_bps=raw["slippage_bps"],
        leverage=raw["leverage"], risk_limit_source=raw["risk_limit_path"],
        risk_limit_sha256=tiers.source_sha256,
        fee_basis=raw.get("fee_basis", "ASSUMED_PUBLIC_NON_VIP"))
    return config, tiers


class Runtime:
    """Holds one feed and one session per symbol, and drives the 1 Hz observation loop.

    `self.feed` and `self.session` keep their names and keep meaning the *default* symbol's, so
    every existing caller - the C1 controller, the LIVE routes borrowing the FX rate, the tests
    that reach straight into `runtime.session` - is unchanged.

    One feed per symbol rather than one feed tracking three: the local order book's continuity
    rule discards the book and resubscribes on a sequence gap, and sharing a socket would mean
    an ETH gap throwing away the BTC book. One session per symbol rather than one engine holding
    three positions: `paper.account.Account` owns exactly one `Position` and derives
    `available_balance` from it, and the engine's determinism proof is about that shape. Giving
    it three positions is a rewrite of the proven core, so it was not done - see
    `MULTI_SYMBOL` in the report for what that costs (each symbol has its own virtual cash).
    """

    def __init__(self) -> None:
        self.feeds: dict[str, BybitPublicFeed] = {}
        self.sessions: dict[str, PaperSession] = {}
        #: One wallet behind all three engines. Built with the default symbol's session and
        #: owned by it, so the money that run already holds carries across unchanged and a
        #: second symbol does not arrive with a starting balance of its own.
        self.cash: SharedCash | None = None
        #: Why a symbol has no session, keyed by symbol. A missing measurement is reported, not
        #: worked around.
        self.symbol_errors: dict[str, str] = {}
        self.feed = BybitPublicFeed()
        self.session: PaperSession | None = None
        self.c1_auto: C1AutoController | None = None
        self.error: str | None = None
        self.base_config: PaperRunConfig | None = None
        self.named_symbols: dict[str, Any] = {}
        self.root: Path = DEFAULT_ROOT
        self._pump: asyncio.Task | None = None

    # ------------------------------------------------------------------ construction

    def build(self, config_path: Path, root: Path) -> None:
        """Build the default symbol's run. The others are built on first use."""
        config, tiers = load_run_config(config_path)
        self.root = root
        self.base_config = config
        try:
            raw = json.loads(config_path.read_text())
            named = raw.get("symbols")
            self.named_symbols = named if isinstance(named, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.named_symbols = {}
        symbol = config.instrument.symbol
        self.session = PaperSession(config=config, tiers=tiers, root=root)
        self.sessions[symbol] = self.session
        self.feeds[symbol] = self.feed
        self.feed.symbol = symbol
        # AUTO is out of scope for the multi-symbol step, so the C1 controller stays bound to
        # the one session it has always been bound to.
        self.c1_auto = C1AutoController(session=self.session)
        # After recovery, so the capital base the purse inherits is the replayed one rather
        # than the configured one. A run that has been reset three times holds the figure those
        # resets left, and that is the balance the other symbols must draw from.
        self.cash = SharedCash(owner=symbol)
        self.cash.join(symbol, self.session.engine.account)
        # Last, because it builds other symbols' sessions and those look the default one up.
        self._join_symbols_with_history()

    def _join_symbols_with_history(self) -> None:
        """Attach every symbol that has already traded, before serving the first request.

        Sessions are otherwise built on first use, which is right for a symbol nobody has
        opened. It is wrong for the wallet: a symbol with realised PnL on disk contributes to
        the shared balance whether or not anybody is looking at it, so a lazily attached member
        means the wallet reads high (or low) until that tab is first visited and then silently
        changes. Observed in the preview as three different wallet figures from three polls
        taken seconds apart.

        A symbol with no run directory has never traded and contributes nothing, so leaving it
        lazy costs the wallet nothing and saves a replay.
        """
        if self.base_config is None:
            return
        for symbol in SUPPORTED_SYMBOLS:
            if symbol in self.sessions:
                continue
            try:
                config, _ = symbol_run_config(self.base_config, symbol,
                                              self.named_symbols.get(symbol),
                                              paper_root=self.root)
            except (SymbolReferenceMissing, ValueError, OSError):
                continue        # no measurement; `session_for` reports it when asked
            if not (self.root / config.run_id).is_dir():
                continue        # never traded
            try:
                self.session_for(symbol)
            except ConfigMissing:
                continue

    def symbols(self) -> list[str]:
        """Every symbol a PAPER screen may ask for, default first."""
        return list(SUPPORTED_SYMBOLS)

    def feed_for(self, symbol: str | None = None) -> BybitPublicFeed:
        """The feed for one symbol, started on first use.

        Lazy because a socket per symbol costs a connection and a book for a screen nobody is
        looking at. Started here rather than in the lifespan for the same reason: the first
        request for ETHUSDT is what makes an ETHUSDT subscription worth having.
        """
        instrument = resolve_symbol(symbol)
        existing = self.feeds.get(instrument)
        if existing is not None:
            return existing
        feed = BybitPublicFeed(symbol=instrument)
        self.feeds[instrument] = feed
        feed.start()
        return feed

    def session_for(self, symbol: str | None = None) -> PaperSession:
        """The paper session for one symbol, built on first use.

        Raises `ConfigMissing` when the symbol has no measured instrument spec or risk table,
        which is the honest refusal: that symbol's quantity rules are unknown and an engine
        configured with another symbol's would accept sizes the exchange does not have.
        """
        instrument = resolve_symbol(symbol)
        existing = self.sessions.get(instrument)
        if existing is not None:
            return existing
        failure = self.symbol_errors.get(instrument)
        if failure is not None:
            raise ConfigMissing(failure)
        if self.base_config is None:
            raise ConfigMissing(self.error or "paper session is not configured")
        try:
            config, tiers = symbol_run_config(self.base_config, instrument,
                                              self.named_symbols.get(instrument),
                                              paper_root=self.root)
        except (SymbolReferenceMissing, ValueError, OSError) as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.symbol_errors[instrument] = message
            raise ConfigMissing(message) from None
        session = PaperSession(config=config, tiers=tiers, root=self.root)
        if self.cash is not None:
            # Joins the existing wallet. Its own capital base is ignored by the purse, so this
            # adds an instrument rather than more money: a BTCUSDT position immediately shrinks
            # what this symbol may open, which is what the real Binance wallet does.
            self.cash.join(instrument, session.engine.account)
        self.sessions[instrument] = session
        self.feed_for(instrument)
        return session

    # ------------------------------------------------------------------ the loop

    async def _observe_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            # Snapshot the mapping: `session_for` can add an entry from a request thread while
            # this loop is iterating, and mutating a dict mid-iteration is a crash that would
            # stop every symbol's tape, not just the new one's.
            for symbol, session in list(self.sessions.items()):
                feed = self.feeds.get(symbol)
                if feed is None:
                    continue
                quote = feed.quote()
                if quote is None:
                    continue
                try:
                    if not session.is_started:
                        session.start(quote.ts_ms)
                    session.observe(quote)
                    # AUTO reconciles on the default session only, unchanged.
                    controller = self.c1_auto
                    if controller is not None and session is self.session:
                        from . import c1_routes
                        c1 = c1_routes.c1_runtime
                        controller.reconcile(quote, c1.signals.values() if c1 is not None else ())
                except Exception as exc:  # a bad tick must not kill the loop silently
                    self.error = f"{symbol} {type(exc).__name__}: {exc}"

    def start(self) -> None:
        self.feed.start()
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._observe_loop())

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        for feed in list(self.feeds.values()):
            await feed.stop()

    def require_session(self) -> PaperSession:
        if self.session is None:
            raise ConfigMissing(self.error or "paper session is not configured")
        return self.session

    def selected_session(self, symbol: str | None = None) -> PaperSession:
        """The session a request is about.

        `None` means the default symbol and goes through `require_session`, which is the exact
        path and the exact error every existing caller already had.
        """
        if symbol in (None, ""):
            return self.require_session()
        return self.session_for(symbol)


runtime = Runtime()


class OrderRequest(BaseModel):
    side: str
    intent: str = "OPEN"
    qty: str | None = None
    notional_usdt: str | None = None
    reason: str = MANUAL_SOURCE
    symbol: str | None = None


class LeverageRequest(BaseModel):
    leverage: str
    symbol: str | None = None


class ModeRequest(BaseModel):
    action: str
    confirmed: bool = False
    symbol: str | None = None


class AutoRequest(BaseModel):
    enabled: bool
    symbol: str | None = None


def _decimal(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not a number: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def paper_session(symbol: str | None):
    """`(session, None)` or `(None, the response that says why not)`.

    One helper for the three refusals every paper route shares - an off-list symbol, no run
    configured, a symbol with no measured instrument spec - so a route cannot omit one and so
    the codes are identical everywhere.
    """
    try:
        return runtime.selected_session(symbol), None
    except SymbolNotSupported as exc:
        return None, error(400, exc.code, exc.message)
    except ConfigMissing as exc:
        return None, error(503, "RUN_NOT_CONFIGURED", str(exc))


def paper_feed(symbol: str | None):
    try:
        return runtime.feed_for(symbol), None
    except SymbolNotSupported as exc:
        return None, error(400, exc.code, exc.message)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    config_path = Path(os.environ.get(CONFIG_ENV, DEFAULT_ROOT / "run_config.json"))
    root = Path(os.environ.get("CRYPTO_PAPER_ROOT", DEFAULT_ROOT))
    try:
        runtime.build(config_path, root)
    except Exception as exc:
        runtime.error = f"{type(exc).__name__}: {exc}"
    try:
        await asyncio.to_thread(runtime.feed.seed_klines)
    except Exception as exc:
        runtime.feed.telemetry.last_error = f"kline seed failed: {exc}"
    runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="US-B CRYPTO Paper Terminal", version=PAPER_ENGINE_VERSION, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "engine": PAPER_ENGINE_VERSION,
                "configured": runtime.session is not None, "error": runtime.error}

    @app.get("/api/crypto/state")
    async def state(symbol: str | None = None) -> Any:
        session, failure = paper_session(symbol)
        if failure is not None:
            return failure
        feed, feed_failure = paper_feed(symbol)
        if feed_failure is not None:
            return feed_failure
        snapshot = session.snapshot()
        snapshot["symbol"] = session.config.instrument.symbol
        snapshot["symbols"] = runtime.symbols()
        # The quantity grid, so the order ticket can default to a size this instrument accepts
        # rather than to BTC's 0.001 - which is below SOLUSDT's 0.1 minimum and would open the
        # panel pre-filled with a quantity the engine refuses.
        spec = session.config.instrument
        snapshot["instrument"] = {
            "symbol": spec.symbol, "qty_step": spec.qty_step,
            "min_order_qty": spec.min_order_qty, "max_mkt_order_qty": spec.max_mkt_order_qty,
            "min_notional_value": spec.min_notional_value, "tick_size": spec.tick_size,
            "max_leverage": spec.max_leverage, "source": spec.source}
        snapshot["feed"] = feed.view()
        snapshot["server_time_ms"] = int(time.time() * 1000)
        snapshot["engine_version"] = PAPER_ENGINE_VERSION
        account = snapshot.get("account")
        if account is not None:
            rate = session.config.fx.krw_per_usdt
            snapshot["krw"] = {key: account[key] * rate for key in
                               ("equity", "available_balance", "realized_pnl", "unrealized_pnl",
                                "used_margin", "wallet_balance")}
        default = session is runtime.session
        snapshot["paper_source"] = ("PAPER_C1_AUTO" if default and runtime.c1_auto
                                    and runtime.c1_auto.state.enabled else "PAPER_MANUAL")
        # AUTO is the default symbol's only, so it is reported as absent on the others rather
        # than reported as the default symbol's state under their tab.
        snapshot["c1_auto"] = (runtime.c1_auto.view() if default and runtime.c1_auto else None)
        snapshot["c1_available"] = default
        return jsonable(snapshot)

    @app.get("/api/crypto/paper/c1-auto")
    async def c1_auto_state(symbol: str | None = None) -> Any:
        """C1 AUTO, on the default symbol only.

        C1 is a BTCUSDT research result. Reporting its state under an ETH or SOL tab would be
        presenting a BTC signal as that instrument's, which is the contamination this step is
        required to prevent, so the route refuses instead of answering.
        """
        session, failure = paper_session(symbol)
        if failure is not None:
            return failure
        if session is not runtime.session:
            return error(409, "C1_SINGLE_SYMBOL",
                         f"C1은 {DEFAULT_SYMBOL} 연구 결과이므로 "
                         f"{session.config.instrument.symbol}에는 제공되지 않습니다.")
        if runtime.c1_auto is None:
            return error(503, "RUN_NOT_CONFIGURED", "C1 AUTO is not configured")
        return jsonable({**runtime.c1_auto.view(), "symbol": DEFAULT_SYMBOL})

    @app.post("/api/crypto/paper/c1-auto")
    async def c1_auto_toggle(request: AutoRequest) -> Any:
        if request.symbol not in (None, "") and resolve_symbol(request.symbol) != DEFAULT_SYMBOL:
            return error(409, "C1_SINGLE_SYMBOL",
                         f"C1 AUTO는 {DEFAULT_SYMBOL} 전용입니다.")
        if runtime.c1_auto is None:
            return error(503, "RUN_NOT_CONFIGURED", "C1 AUTO is not configured")
        quote = runtime.feed.quote()
        if quote is None:
            return error(409, "NO_QUOTE", "C1 AUTO 전환에 필요한 현재 호가가 없습니다.")
        if request.enabled:
            from . import c1_routes
            if c1_routes.c1_runtime is None:
                return error(409, "C1_SIGNAL_UNAVAILABLE", "C1 신호 레이어가 준비되지 않았습니다.")
            return jsonable(runtime.c1_auto.enable(quote))
        success, body = runtime.c1_auto.disable(quote)
        if not success:
            return error(409, "AUTO_CLOSE_FAILED", "AUTO 포지션 청산에 실패해 AUTO를 유지합니다.")
        return jsonable(body)

    @app.get("/api/crypto/chart")
    async def chart(limit: int = 120, symbol: str | None = None) -> Any:
        feed, failure = paper_feed(symbol)
        if failure is not None:
            return failure
        # The symbol is returned with the bars so the chart can discard a response that arrived
        # after the operator changed tabs, instead of drawing it under the new label.
        return jsonable({"symbol": feed.symbol, "bars": feed.chart(min(limit, 600))})

    @app.get("/api/crypto/chart-history")
    async def chart_history_route(timeframe: str, limit: int = 500,
                                  before_ms: int | None = None,
                                  symbol: str | None = None) -> Any:
        """Paged display history. It never feeds orders, sizing, PnL or the paper ledger."""
        try:
            body = await asyncio.to_thread(chart_history.get, timeframe, limit, before_ms, symbol)
        except SymbolNotSupported as exc:
            return error(400, exc.code, exc.message)
        except ValueError as exc:
            return error(400, "CHART_QUERY_INVALID", str(exc))
        except Exception as exc:
            return error(502, "CHART_SOURCE_UNAVAILABLE",
                         f"{type(exc).__name__}: {exc}")
        return jsonable(body)

    @app.get("/api/crypto/performance")
    async def performance(symbol: str | None = None) -> Any:
        """Everything here is folded out of the ledger, not read off a running total."""
        session, failure = paper_session(symbol)
        if failure is not None:
            return failure
        events = session.engine.ledger.events
        account = session.engine.account
        summary = summarize(events, starting_capital=account.starting_capital_usdt)
        rate = session.config.fx.krw_per_usdt
        summary["krw"] = {key: Decimal(summary[key]) * rate
                          for key in ("gross_pnl", "fees", "funding", "net_pnl", "max_drawdown",
                                      "ending_equity")}
        summary["reconciliation"] = reconcile(
            events, starting_capital=account.starting_capital_usdt,
            account_realized=account.realized_pnl, account_fees=account.cumulative_fees,
            account_funding=account.cumulative_funding_paid)
        summary["source"] = "LIVE_PAPER"
        summary["paper_source"] = ("PAPER_C1_AUTO" if session is runtime.session
                                   and runtime.c1_auto and runtime.c1_auto.state.enabled
                                   else "PAPER_MANUAL")
        summary["run_id"] = session.config.run_id
        summary["symbol"] = session.config.instrument.symbol
        return jsonable(summary)

    @app.get("/api/crypto/trades")
    async def trades(limit: int = 50, symbol: str | None = None) -> Any:
        session, failure = paper_session(symbol)
        if failure is not None:
            return failure
        from ..paper.analytics import build_trades
        rows = build_trades(session.engine.ledger.events)
        return jsonable({"symbol": session.config.instrument.symbol, "total": len(rows),
                         "trades": [trade.view() for trade in rows[-min(limit, 200):]][::-1]})

    @app.get("/api/crypto/ledger")
    async def ledger(limit: int = 100, symbol: str | None = None) -> Any:
        session, failure = paper_session(symbol)
        if failure is not None:
            return failure
        events = session.engine.ledger.events
        return jsonable({"symbol": session.config.instrument.symbol, "total": len(events),
                         "events": events[-min(limit, 500):][::-1]})

    @app.post("/api/crypto/order")
    async def order(request: OrderRequest) -> Any:
        session, failure = paper_session(request.symbol)
        if failure is not None:
            return failure
        feed, feed_failure = paper_feed(request.symbol)
        if feed_failure is not None:
            return feed_failure
        # The one check that makes the symbol parameter safe rather than decorative: the engine
        # about to be asked for a fill and the feed supplying the price must be the same
        # instrument. They are, by construction, because both were looked up with the same
        # string - and that is precisely why asserting it is cheap and why its absence would be
        # invisible.
        if session.config.instrument.symbol != feed.symbol:
            return error(500, "SYMBOL_MISMATCH",
                         f"엔진({session.config.instrument.symbol})과 "
                         f"피드({feed.symbol})의 심볼이 다릅니다.")
        quote = feed.quote()
        if quote is None:
            return error(409, "NO_QUOTE", "no live quote yet; the feed has not delivered a book and mark")
        try:
            if request.qty is not None:
                qty = _decimal(request.qty, "qty")
            elif request.notional_usdt is not None:
                notional = _decimal(request.notional_usdt, "notional_usdt")
                reference = quote.best_ask if request.side == "LONG" else quote.best_bid
                if reference is None:
                    return error(409, "NO_QUOTE", "the book side needed for sizing is empty")
                step = session.config.instrument.qty_step
                qty = (notional / reference / step).to_integral_value(rounding="ROUND_DOWN") * step
            else:
                return error(400, "QTY_REQUIRED", "pass either qty or notional_usdt")
        except ValueError as exc:
            return error(400, "INVALID_REQUEST", str(exc))
        if (session is runtime.session and runtime.c1_auto is not None
                and runtime.c1_auto.state.enabled):
            if request.intent == "OPEN":
                return error(409, "AUTO_MANAGED", "C1 AUTO 중에는 자동 진입만 허용됩니다.")
            if runtime.c1_auto.owns_position():
                if not runtime.c1_auto.close(quote, MANUAL_CLOSE_DURING_AUTO):
                    return error(409, "AUTO_CLOSE_FAILED", "AUTO 포지션 청산에 실패했습니다.")
                return jsonable({"events": [], "state": session.snapshot()})
        # Put the engine on the tick this order is about to be judged against, then re-check
        # the size on it. A preset was priced seconds ago and the book moves; letting a stale
        # preview through is how an operator ends up holding a position the engine will refuse
        # to close. `quote=None` below because the observation has already happened here.
        session.observe(quote, force=True)
        if request.intent == "OPEN":
            guard = sizing.probe(session.engine, request.side, qty, require_exit=True)
            if not guard.feasible:
                safe = sizing.max_entry(session.engine, request.side)
                return error(409, guard.reject_code or "UNSAFE_SIZE",
                             f"{guard.reject_message} "
                             f"(현재 호가 기준 안전 최대 {safe.qty if safe.feasible else 0})")

        payload = {"command": "ORDER", "ts_ms": quote.ts_ms, "side": request.side,
                   "qty": str(qty), "intent": request.intent,
                   "request_id": f"{request.intent.lower()}-{quote.ts_ms}-{len(session.tape)}",
                   "reason": request.reason}
        result = session.command(payload, quote=None)
        if result["rejection"] is not None:
            rejection = result["rejection"]
            return error(409, str(rejection["code"]), str(rejection["message"]))
        return jsonable({"symbol": session.config.instrument.symbol,
                         "events": result["events"], "state": session.snapshot()})

    @app.post("/api/crypto/leverage")
    async def leverage(request: LeverageRequest) -> Any:
        session, failure = paper_session(request.symbol)
        if failure is not None:
            return failure
        if (session is runtime.session and runtime.c1_auto is not None
                and runtime.c1_auto.state.enabled):
            return error(409, "AUTO_LEVERAGE_FIXED", "C1 AUTO 레버리지는 10x로 고정됩니다.")
        try:
            value = _decimal(request.leverage, "leverage")
            session.config.validate_leverage(value)
        except (ValueError, TypeError) as exc:
            return error(400, "INVALID_LEVERAGE", str(exc))
        if not session.engine.account.position.is_flat:
            return error(409, "LEVERAGE_LOCKED_WHILE_OPEN",
                         "leverage can only change while the position is flat")
        feed, feed_failure = paper_feed(request.symbol)
        if feed_failure is not None:
            return feed_failure
        quote = feed.quote()
        ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
        session.command({"command": "SET_LEVERAGE", "ts_ms": ts, "leverage": str(value)}, quote=quote)
        return jsonable({"symbol": session.config.instrument.symbol,
                         "state": session.snapshot()})

    @app.post("/api/crypto/mode")
    async def mode(request: ModeRequest) -> Any:
        session, failure = paper_session(request.symbol)
        if failure is not None:
            return failure
        try:
            session.engine.state.check(request.action, confirmed=request.confirmed)
        except TransitionRejected as exc:
            return error(409, exc.code, str(exc))
        feed, feed_failure = paper_feed(request.symbol)
        if feed_failure is not None:
            return feed_failure
        quote = feed.quote()
        ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
        result = session.command({"command": "SET_MODE", "ts_ms": ts, "action": request.action,
                                  "confirmed": request.confirmed, "reason": "OPERATOR"}, quote=quote)
        return jsonable({"events": result["events"], "state": session.snapshot()})

    @app.post("/api/crypto/fault/drop-feed")
    async def drop_feed(symbol: str | None = None) -> Any:
        """Operational test hook. Off unless CRYPTO_PAPER_FAULT_INJECTION=1 is set."""
        if os.environ.get("CRYPTO_PAPER_FAULT_INJECTION") != "1":
            return error(404, "FAULT_INJECTION_DISABLED", "fault injection is not enabled")
        feed, failure = paper_feed(symbol)
        if failure is not None:
            return failure
        dropped = await feed.inject_disconnect()
        return jsonable({"dropped": dropped, "feed": feed.view()})

    @app.post("/api/crypto/emergency-close")
    async def emergency_close(symbol: str | None = None) -> Any:
        session, failure = paper_session(symbol)
        if failure is not None:
            return failure
        feed, feed_failure = paper_feed(symbol)
        if feed_failure is not None:
            return feed_failure
        quote = feed.quote()
        ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
        result = session.command({"command": "EMERGENCY_CLOSE", "ts_ms": ts, "reason": "EMERGENCY"},
                                 quote=quote)
        return jsonable({"events": result["events"], "state": session.snapshot()})

    return app


app = create_app()

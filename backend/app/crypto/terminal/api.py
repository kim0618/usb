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
from ..paper.instrument import RiskTierTable
from ..paper.state import TransitionRejected
from ..paper.c1_auto import (C1AutoController, MANUAL_CLOSE_DURING_AUTO, MANUAL_SOURCE)
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
    """Holds the feed and the session, and drives the 1 Hz observation loop."""

    def __init__(self) -> None:
        self.feed = BybitPublicFeed()
        self.session: PaperSession | None = None
        self.c1_auto: C1AutoController | None = None
        self.error: str | None = None
        self._pump: asyncio.Task | None = None

    def build(self, config_path: Path, root: Path) -> None:
        config, tiers = load_run_config(config_path)
        self.session = PaperSession(config=config, tiers=tiers, root=root)
        self.c1_auto = C1AutoController(manual_config=config, tiers=tiers, root=root)

    async def _observe_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            session = self.session
            if session is None:
                continue
            quote = self.feed.quote()
            if quote is None:
                continue
            try:
                if not session.is_started:
                    session.start(quote.ts_ms)
                session.observe(quote)
                controller = self.c1_auto
                if controller is not None:
                    from . import c1_routes
                    c1 = c1_routes.c1_runtime
                    controller.reconcile(quote, c1.signals.values() if c1 is not None else ())
            except Exception as exc:  # a bad tick must not kill the loop silently
                self.error = f"{type(exc).__name__}: {exc}"

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
        await self.feed.stop()

    def require_session(self) -> PaperSession:
        if self.session is None:
            raise ConfigMissing(self.error or "paper session is not configured")
        return self.session

    def selected_session(self) -> PaperSession:
        manual = self.require_session()
        if self.c1_auto is not None and self.c1_auto.state.enabled:
            return self.c1_auto.session
        return manual


runtime = Runtime()


class OrderRequest(BaseModel):
    side: str
    intent: str = "OPEN"
    qty: str | None = None
    notional_usdt: str | None = None
    reason: str = MANUAL_SOURCE


class LeverageRequest(BaseModel):
    leverage: str


class ModeRequest(BaseModel):
    action: str
    confirmed: bool = False


class AutoRequest(BaseModel):
    enabled: bool


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
    async def state() -> Any:
        if runtime.session is None:
            return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
        session = runtime.selected_session()
        snapshot = session.snapshot()
        snapshot["feed"] = runtime.feed.view()
        snapshot["server_time_ms"] = int(time.time() * 1000)
        snapshot["engine_version"] = PAPER_ENGINE_VERSION
        account = snapshot.get("account")
        if account is not None:
            rate = session.config.fx.krw_per_usdt
            snapshot["krw"] = {key: account[key] * rate for key in
                               ("equity", "available_balance", "realized_pnl", "unrealized_pnl",
                                "used_margin", "wallet_balance")}
        snapshot["paper_source"] = ("PAPER_C1_AUTO" if runtime.c1_auto
                                    and runtime.c1_auto.state.enabled else "PAPER_MANUAL")
        snapshot["c1_auto"] = runtime.c1_auto.view() if runtime.c1_auto else None
        return jsonable(snapshot)

    @app.get("/api/crypto/paper/c1-auto")
    async def c1_auto_state() -> Any:
        if runtime.c1_auto is None:
            return error(503, "RUN_NOT_CONFIGURED", "C1 AUTO is not configured")
        return jsonable(runtime.c1_auto.view())

    @app.post("/api/crypto/paper/c1-auto")
    async def c1_auto_toggle(request: AutoRequest) -> Any:
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
    async def chart(limit: int = 120) -> Any:
        return jsonable({"bars": runtime.feed.chart(min(limit, 600))})

    @app.get("/api/crypto/chart-history")
    async def chart_history_route(timeframe: str, limit: int = 500,
                                  before_ms: int | None = None) -> Any:
        """Paged display history. It never feeds orders, sizing, PnL or the paper ledger."""
        try:
            body = await asyncio.to_thread(chart_history.get, timeframe, limit, before_ms)
        except ValueError as exc:
            return error(400, "CHART_QUERY_INVALID", str(exc))
        except Exception as exc:
            return error(502, "CHART_SOURCE_UNAVAILABLE",
                         f"{type(exc).__name__}: {exc}")
        return jsonable(body)

    @app.get("/api/crypto/performance")
    async def performance() -> Any:
        """Everything here is folded out of the ledger, not read off a running total."""
        if runtime.session is None:
            return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
        session = runtime.selected_session()
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
        summary["paper_source"] = ("PAPER_C1_AUTO" if runtime.c1_auto
                                   and runtime.c1_auto.state.enabled else "PAPER_MANUAL")
        summary["run_id"] = session.config.run_id
        return jsonable(summary)

    @app.get("/api/crypto/trades")
    async def trades(limit: int = 50) -> Any:
        if runtime.session is None:
            return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
        from ..paper.analytics import build_trades
        session = runtime.selected_session()
        rows = build_trades(session.engine.ledger.events)
        return jsonable({"total": len(rows),
                         "trades": [trade.view() for trade in rows[-min(limit, 200):]][::-1]})

    @app.get("/api/crypto/ledger")
    async def ledger(limit: int = 100) -> Any:
        if runtime.session is None:
            return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
        events = runtime.selected_session().engine.ledger.events
        return jsonable({"total": len(events), "events": events[-min(limit, 500):][::-1]})

    @app.post("/api/crypto/order")
    async def order(request: OrderRequest) -> Any:
        try:
            session = runtime.selected_session()
        except ConfigMissing as exc:
            return error(503, "RUN_NOT_CONFIGURED", str(exc))
        quote = runtime.feed.quote()
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
        if runtime.c1_auto is not None and runtime.c1_auto.state.enabled:
            if request.intent == "OPEN":
                return error(409, "AUTO_MANAGED", "C1 AUTO 중에는 자동 진입만 허용됩니다.")
            if not runtime.c1_auto.close(quote, MANUAL_CLOSE_DURING_AUTO):
                return error(409, "AUTO_CLOSE_FAILED", "AUTO 포지션 청산에 실패했습니다.")
            return jsonable({"events": [], "state": runtime.c1_auto.session.snapshot()})
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
        return jsonable({"events": result["events"], "state": session.snapshot()})

    @app.post("/api/crypto/leverage")
    async def leverage(request: LeverageRequest) -> Any:
        if runtime.c1_auto is not None and runtime.c1_auto.state.enabled:
            return error(409, "AUTO_LEVERAGE_FIXED", "C1 AUTO 레버리지는 10x로 고정됩니다.")
        try:
            session = runtime.require_session()
        except ConfigMissing as exc:
            return error(503, "RUN_NOT_CONFIGURED", str(exc))
        try:
            value = _decimal(request.leverage, "leverage")
            session.config.validate_leverage(value)
        except (ValueError, TypeError) as exc:
            return error(400, "INVALID_LEVERAGE", str(exc))
        if not session.engine.account.position.is_flat:
            return error(409, "LEVERAGE_LOCKED_WHILE_OPEN",
                         "leverage can only change while the position is flat")
        quote = runtime.feed.quote()
        ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
        session.command({"command": "SET_LEVERAGE", "ts_ms": ts, "leverage": str(value)}, quote=quote)
        return jsonable({"state": session.snapshot()})

    @app.post("/api/crypto/mode")
    async def mode(request: ModeRequest) -> Any:
        try:
            session = runtime.require_session()
        except ConfigMissing as exc:
            return error(503, "RUN_NOT_CONFIGURED", str(exc))
        try:
            session.engine.state.check(request.action, confirmed=request.confirmed)
        except TransitionRejected as exc:
            return error(409, exc.code, str(exc))
        quote = runtime.feed.quote()
        ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
        result = session.command({"command": "SET_MODE", "ts_ms": ts, "action": request.action,
                                  "confirmed": request.confirmed, "reason": "OPERATOR"}, quote=quote)
        return jsonable({"events": result["events"], "state": session.snapshot()})

    @app.post("/api/crypto/fault/drop-feed")
    async def drop_feed() -> Any:
        """Operational test hook. Off unless CRYPTO_PAPER_FAULT_INJECTION=1 is set."""
        if os.environ.get("CRYPTO_PAPER_FAULT_INJECTION") != "1":
            return error(404, "FAULT_INJECTION_DISABLED", "fault injection is not enabled")
        dropped = await runtime.feed.inject_disconnect()
        return jsonable({"dropped": dropped, "feed": runtime.feed.view()})

    @app.post("/api/crypto/emergency-close")
    async def emergency_close() -> Any:
        try:
            session = runtime.require_session()
        except ConfigMissing as exc:
            return error(503, "RUN_NOT_CONFIGURED", str(exc))
        quote = runtime.feed.quote()
        ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
        result = session.command({"command": "EMERGENCY_CLOSE", "ts_ms": ts, "reason": "EMERGENCY"},
                                 quote=quote)
        return jsonable({"events": result["events"], "state": session.snapshot()})

    return app


app = create_app()

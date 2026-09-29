"""ASGI entrypoint for the deployed terminal: the D3 app plus the D3.5 sizing route.

Why the route lives here and not in `api.py`: that module is being extended by the research
session, and two sessions editing one `create_app()` in a shared working tree do not merge -
whoever saves last silently wins. Registering from a separate module keeps the two bodies of
work in separate files. `app` below is the *same* FastAPI instance `api.py` builds, so the
lifespan, the feed and the paper session are the originals, not a second copy.

If `api.py` is ever restructured so that `app` or `runtime` no longer exists, this import fails
at startup and systemd reports it. That is the intended failure: loud, not silent.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from ..paper import pnl_breakdown, sizing, trade_preview
from ..paper.config import DEFAULT_STARTING_CAPITAL_KRW
from ..paper.engine import OrderRejected
from .api import app, error, jsonable, runtime
from .trade_candles import TradeCandleFeed

SIDES = ("LONG", "SHORT")


class ResetRequest(BaseModel):
    """`target_krw` is optional so the button does not have to know the figure; when it is left
    out the run's configured default applies."""
    target_krw: str | None = None
    reason: str = "USER_RESET"


@app.get("/api/crypto/sizing")
async def sizing_quote(side: str | None = None) -> Any:
    """Quantities the engine would actually accept right now, priced per preset.

    Both sides are returned by default. They are not mirror images of each other: a LONG walks
    the ask side and a SHORT walks the bid side, so the affordable size differs whenever the
    book is lopsided, and the panel should not guess one from the other.
    """
    if runtime.session is None:
        return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")

    requested = SIDES if side is None else (side.upper(),)
    for candidate in requested:
        if candidate not in SIDES:
            return error(400, "UNKNOWN_SIDE", f"side must be LONG or SHORT, got {candidate!r}")

    session = runtime.session
    quote = runtime.feed.quote()
    if quote is not None:
        # Price the presets against the tick the operator is looking at, not the one that
        # happened to be recorded a second ago.
        session.observe(quote, force=True)

    engine = session.engine
    payload: dict[str, Any] = {
        "run_id": session.config.run_id,
        "leverage": engine.leverage,
        "quote_ts_ms": engine.quote.ts_ms if engine.quote is not None else None,
        "mark_price": engine.quote.mark_price if engine.quote is not None else None,
        "available_balance": engine.account.available_balance,
        "sides": {candidate: sizing.presets(engine, candidate) for candidate in requested},
    }
    return jsonable(payload)


@app.post("/api/crypto/reset")
async def reset_account(request: ResetRequest) -> Any:
    """Restore the virtual balance without deleting anything.

    Orders, fills, fees, funding, the ledger and every analytic folded from it are left exactly
    as they were; the run gains one ACCOUNT_RESET line saying where the capital line was
    redrawn. That is the whole difference between this and starting a new run, and it is why
    the trade history stays one continuous record.
    """
    if runtime.session is None:
        return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
    session = runtime.session

    try:
        target = (Decimal(request.target_krw) if request.target_krw is not None
                  else DEFAULT_STARTING_CAPITAL_KRW)
    except (InvalidOperation, ValueError):
        return error(400, "RESET_TARGET_INVALID", f"target_krw is not a number: {request.target_krw!r}")
    if not target.is_finite() or target <= 0:
        return error(400, "RESET_TARGET_INVALID", f"reset target must be positive: {target}")

    # Checked here as well as in the engine so the refusal never reaches the tape: a rejected
    # reset should leave no trace at all, the way a rejected leverage change does.
    if not session.engine.account.position.is_flat:
        side = session.engine.account.position.side
        return error(409, "RESET_BLOCKED_OPEN_POSITION",
                     f"{side} 포지션이 열려 있습니다. 청산한 뒤 초기화할 수 있습니다.")

    quote = runtime.feed.quote()
    ts = quote.ts_ms if quote is not None else int(time.time() * 1000)
    try:
        result = session.command({"command": "ACCOUNT_RESET", "ts_ms": ts,
                                  "target_krw": str(target), "reason": request.reason},
                                 quote=quote)
    except OrderRejected as exc:
        return error(409, exc.code, str(exc))
    return jsonable({"events": result["events"], "state": session.snapshot()})


@app.get("/api/crypto/pnl-breakdown")
async def pnl_breakdown_view() -> Any:
    """Price PnL and trading costs, apart, for the open position and every closed trade.

    Read-only. The open-position close figures come from a clone that was actually sent the full
    CLOSE, so the fee and the fill are the engine's. This route does not force a fresh market
    observation: the sizing route polled on the same tick already does, and recording the tape a
    second time per second would double its growth on a disk that is the binding limit. The
    quote timestamp is returned so the panel can say which book the preview was priced on.
    """
    if runtime.session is None:
        return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
    engine = runtime.session.engine
    rate = runtime.session.config.fx.krw_per_usdt
    position = pnl_breakdown.open_position_preview(engine)
    trades = pnl_breakdown.closed_trade_breakdowns(engine.ledger.events)
    # KRW at the run's fixed rate, converted here like `/state` does, so the panel shows won
    # without multiplying anything itself.
    position["krw"] = pnl_breakdown.to_krw(position, rate)
    for row in trades:
        row["krw"] = pnl_breakdown.to_krw(row, rate)
    return jsonable({
        "run_id": runtime.session.config.run_id,
        "krw_per_usdt": rate,
        "position": position,
        "trades": trades[-200:][::-1],
        "trade_total": len(trades),
    })


def _preview_qty(raw: str | None, notional: str | None, side: str, quote: Any,
                 step: Decimal) -> tuple[Decimal | None, str | None]:
    """The quantity an order would carry. A notional is converted exactly as the order route in
    api.py converts it - divided by the side's best price and floored to the step - so a preview
    of "100 USDT" prices the same coins the button would send."""
    try:
        if raw is not None:
            qty = Decimal(raw)
        elif notional is not None:
            reference = quote.best_ask if side == "LONG" else quote.best_bid
            if reference is None:
                return None, "NO_QUOTE"
            qty = (Decimal(notional) / reference / step).to_integral_value(rounding="ROUND_DOWN") * step
        else:
            return None, None
    except (InvalidOperation, ValueError):
        return None, "INVALID_QTY"
    if not qty.is_finite() or qty <= 0:
        return None, "QTY_NOT_POSITIVE"
    return qty, None


@app.get("/api/crypto/order-preview")
async def order_preview(long_qty: str | None = None, short_qty: str | None = None,
                        notional_usdt: str | None = None) -> Any:
    """What entering and immediately closing would cost, per side, before the button is pressed.

    Priced on clones advanced to the newest feed quote. Nothing is recorded: not the tape, not
    the ledger. Both sides in one request so the panel refreshes with one round trip.
    """
    if runtime.session is None:
        return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
    session = runtime.session
    quote = runtime.feed.quote()
    engine = trade_preview.advanced(session.engine, quote)
    rate = session.config.fx.krw_per_usdt
    step = session.config.instrument.qty_step
    sides: dict[str, Any] = {}
    for side, raw in (("LONG", long_qty), ("SHORT", short_qty)):
        if engine.quote is None:
            sides[side] = {"side": side, "feasible": False, "reject_stage": "QUOTE",
                           "reject_code": "NO_QUOTE", "reject_message": "no market quote yet"}
            continue
        qty, problem = _preview_qty(raw, notional_usdt, side, engine.quote, step)
        if problem is not None:
            sides[side] = {"side": side, "feasible": False, "reject_stage": "INPUT",
                           "reject_code": problem, "reject_message": problem,
                           "quote_ts_ms": engine.quote.ts_ms}
            continue
        if qty is None:
            continue
        row = trade_preview.round_trip(engine, side, qty)
        row["krw"] = pnl_breakdown.to_krw(row, rate)
        sides[side] = row
    # Staleness is judged like the terminal's existing 5 s contract: server clock against the
    # server's own receive time, never against the exchange's timestamp (the clocks can differ).
    return jsonable({"run_id": session.config.run_id, "server_time_ms": int(time.time() * 1000),
                     "feed_connected": runtime.feed.telemetry.connected,
                     "feed_last_message_ms": runtime.feed.telemetry.last_message_ms,
                     "quote_ts_ms": engine.quote.ts_ms if engine.quote is not None else None,
                     "krw_per_usdt": rate, "sides": sides})


LIVE_FIELDS = (
    "position_open", "side", "qty", "leverage", "quote_ts_ms", "mark_price", "unrealized_pnl",
    "unrealized_pct_of_margin", "close_feasible", "close_reject_code", "close_reject_message",
    "expected_close_fill_price", "expected_close_fee", "expected_close_slippage_pnl",
    "expected_position_net_if_closed", "expected_segment_net_if_closed",
)


@app.get("/api/crypto/live")
async def live() -> Any:
    """The open position on the newest feed quote, small enough to poll several times a second.

    The engine and the tape keep their 1 Hz rhythm; this answers from a clone advanced to the
    latest tick, so the headline PnL can move with the market without recording it.
    """
    if runtime.session is None:
        return error(503, "RUN_NOT_CONFIGURED", runtime.error or "paper run is not configured")
    session = runtime.session
    preview = trade_preview.live_position(session.engine, runtime.feed.quote())
    body = {key: preview.get(key) for key in LIVE_FIELDS}
    body["krw"] = pnl_breakdown.to_krw(body, session.config.fx.krw_per_usdt)
    body["server_time_ms"] = int(time.time() * 1000)
    body["feed_connected"] = runtime.feed.telemetry.connected
    body["feed_last_message_ms"] = runtime.feed.telemetry.last_message_ms
    return jsonable(body)


# --------------------------------------------------------------------- 15 s candles (UI only)
#
# A second, trade-only Bybit connection on its own thread (see trade_candles.py). It is started
# around the app's existing lifespan rather than inside it, so api.py stays untouched and the
# execution feed is started exactly as before. `CRYPTO_TRADE_CANDLES=off` disables it without a
# code change; the 15 s chart then reports itself unavailable and nothing else notices.
trade_feed = TradeCandleFeed()
_original_lifespan = app.router.lifespan_context


def trade_candles_enabled() -> bool:
    return os.environ.get("CRYPTO_TRADE_CANDLES", "on").lower() not in {"off", "0", "false"}


@asynccontextmanager
async def _lifespan_with_trades(application: Any):
    async with _original_lifespan(application) as state:
        if trade_candles_enabled():
            trade_feed.start()
        try:
            yield state
        finally:
            trade_feed.stop()


app.router.lifespan_context = _lifespan_with_trades


@app.get("/api/crypto/candles-15s")
async def candles_15s(since_ms: int | None = None) -> Any:
    """Finalized 15 s candles after `since_ms` (all kept history when omitted) plus the open one.

    Incremental by design: a client passes the last finalized start it holds and receives only
    what is new, so the full series is never re-sent every tick. Aggregated server-side; raw
    trades never leave the process.
    """
    if not trade_candles_enabled():
        return jsonable({"timeframe": "15s", "status": "DISABLED", "candles": [], "current": None,
                         "server_time_ms": int(time.time() * 1000)})
    return jsonable(trade_feed.view(since_ms=since_ms))


# --------------------------------------------------------------------- Binance LIVE routes
#
# Imported last, and only for its side effect: `live_routes` registers the `/api/crypto/binance/*`
# handlers on the same app and wraps the lifespan once more so the user data stream is stopped on
# shutdown. It is imported here rather than in `api.py` for the reason stated at the top of this
# file - separate files, separate sessions - and the deployed entrypoint is this module, so the
# LIVE routes exist wherever the terminal runs. They answer `503 LIVE_UNAVAILABLE` until a
# Binance key is present in the environment, and orders stay refused until
# `BINANCE_LIVE_TRADING_ENABLED` is turned on, which V1 does not do.
from . import live_routes  # noqa: E402,F401

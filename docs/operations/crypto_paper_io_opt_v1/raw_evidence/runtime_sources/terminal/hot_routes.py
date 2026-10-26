"""`GET /api/crypto/hot-coins`: the HOT Coin Finder, read-only.

Registered from its own module and imported last by `server.py`, the arrangement `live_routes`
uses, so `api.py` stays untouched. One GET route and nothing else: no account,
no key, no order, no leverage, no AUTO. The browser never calls Binance itself; every scan goes
through here so the request budget, the single-flight guard and the symbol encoding live in one
place, and so Phase 2's lock check has a server to run on.

The scan is blocking HTTP and runs on a worker thread. The terminal is one event loop under
`--workers 1`, and a scan of a few seconds on that loop would stall every LIVE poll with it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from ..hot_coins import scanner as hot_scanner
from ..hot_coins.scanner import ScanFailed
from ..hot_coins.service import HotCoinService
from .api import app, error, jsonable

CONTRACT_VERSION = "HOT_V0"

#: Replaced in tests with a service built on a fixture transport.
service = HotCoinService()

_FAILURE_STATUS = {"HOT_SCAN_RATE_BUDGET": 503, "HOT_SCAN_TIMEOUT": 504}


def contract() -> dict[str, Any]:
    return {
        "version": CONTRACT_VERSION,
        "universe": "Binance USD-M PERPETUAL, TRADING, USDT-quoted, BTCUSDT excluded",
        "rank": "one_hour_range_pct desc",
        "min_quote_volume_24h": hot_scanner.MIN_QUOTE_VOLUME_24H,
        "min_quote_volume_1h": hot_scanner.MIN_QUOTE_VOLUME_1H,
        "max_spread_bps": hot_scanner.MAX_SPREAD_BPS,
        "max_round_trip_slippage_bps": hot_scanner.MAX_ROUND_TRIP_SLIPPAGE_BPS,
        "slippage_notional_usdt": hot_scanner.SLIPPAGE_NOTIONAL_USDT,
        "top_n": hot_scanner.TOP_N,
    }


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@app.get("/api/crypto/hot-coins")
async def hot_coins(debug: bool = False) -> Any:
    try:
        answer = await asyncio.to_thread(service.scan)
    except ScanFailed as exc:
        return error(_FAILURE_STATUS.get(exc.code, 502), exc.code, exc.message)
    except Exception as exc:  # anything unexpected is still a failed scan, never a 500 page
        return error(502, "HOT_SCAN_FAILED", f"탐색에 실패했습니다: {type(exc).__name__}")
    outcome = answer.outcome
    decision = outcome.decision
    if not outcome.applied or answer.result is None:
        return error(409, decision.code, decision.message)
    result = answer.result
    body: dict[str, Any] = {
        "status": "OK",
        "scanned_at_ms": result.scanned_at_ms,
        "scanned_at": _iso(result.scanned_at_ms),
        "cached": answer.cached,
        "coalesced": answer.coalesced,
        "top": [candidate.as_dict() for candidate in result.top],
        "universe_size": result.universe_size,
        "stages": result.stage_counts,
        "rejection_counts": result.rejection_counts(),
        "api": {**result.api.as_dict(), "duration_ms": result.duration_ms},
        "lock": decision.as_dict(),
        "contract": contract(),
    }
    if debug:
        body["rejections"] = dict(sorted(result.rejections.items()))
    return jsonable(body)


# =========================================================================== HOT slot (V1)
#
# The persistent HOT symbol beside BTC. `GET /api/crypto/hot` reads it and its lock;
# `POST /api/crypto/hot/scan` is the only thing that changes it, through the guarded scan in
# `hot_coins.slot`. The paper exposure source is registered here; the Binance one is registered
# by `live_routes`, which is the one module allowed to read the account.

import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402

from ..hot_coins import market as hot_market  # noqa: E402
from ..hot_coins.lock import LockDecision, SymbolExposure, scan_permission  # noqa: E402
from ..hot_coins.slot import HOT_SCAN_IN_PROGRESS, CombinedExposure, controller  # noqa: E402
from ..symbols import (DEFAULT_SYMBOL, admit_dynamic, permitted_symbols,  # noqa: E402
                       retire_dynamic, SUPPORTED_SYMBOLS)
from .api import runtime  # noqa: E402

SLOT_PATH_ENV = "CRYPTO_HOT_SLOT_PATH"


class PaperExposure:
    """The paper engine's exposure on one symbol: its position and its orders in flight.

    Paper orders are market orders filled in the same call, so there is never a resting one.

    No session is flat only when the symbol has never traded. A run directory on disk with no
    session behind it is a run that failed to recover at boot (a refused replay, say), and its
    position is unknown, not zero: reading it as flat let a scan replace a HOT symbol that still
    held a paper position."""

    def exposure(self, symbol: str) -> SymbolExposure:
        session = runtime.sessions.get(symbol) or runtime.retired.get(symbol)
        if session is None:
            if runtime.base_config is None:
                raise RuntimeError("paper runtime is not configured")
            if (runtime.root / f"{runtime.base_config.run_id}-{symbol}").exists():
                raise RuntimeError(f"{symbol} has a paper run on disk that is not loaded")
            qty = Decimal(0)
        else:
            qty = session.engine.account.position.signed_qty
        return SymbolExposure(symbol, Decimal(qty), 0, controller.paper_in_flight.count(symbol))


paper_exposure = PaperExposure()
controller.add_source(paper_exposure)


def _prepare(symbol: str, metrics: Any) -> str | None:
    """Re-confirm the new HOT symbol on Binance and measure what its paper run needs."""
    scanner = service._scanner_instance()
    row = scanner.symbol_row(symbol)
    if (row is None or row.get("contractType") != "PERPETUAL" or row.get("status") != "TRADING"
            or row.get("quoteAsset") != "USDT"):
        raise ScanFailed("HOT_SYMBOL_NOT_TRADABLE", f"{symbol}은 거래 가능한 USDT 무기한 선물이 아닙니다.")
    if symbol in SUPPORTED_SYMBOLS or runtime.base_config is None:
        return None
    if hot_market.find_reference(runtime.root, symbol) is not None:
        return None
    try:
        hot_market.fetch_reference(runtime.root, symbol, row,
                                   bracket_provider=controller.bracket_provider)
    except hot_market.MarginTableUnavailable as exc:
        return str(exc)
    except Exception as exc:  # the HOT symbol is still valid; only its paper run is not
        return f"{symbol} PAPER 기준 응답 측정 실패: {type(exc).__name__}"
    return None


def _admit(symbol: str) -> None:
    admit_dynamic(symbol)


def _release_sync(symbol: str) -> None:
    if symbol == DEFAULT_SYMBOL:
        return
    from . import server
    server.retire_trade_feed(symbol)


controller.prepare = _prepare
controller.on_commit.append(_admit)
controller.on_retire.append(_release_sync)


def slot_path() -> Path:
    named = (os.environ.get(SLOT_PATH_ENV) or "").strip()
    if named:
        return Path(named)
    return Path(os.environ.get("CRYPTO_PAPER_ROOT", "data/runtime/crypto/paper")) / "hot_slot.json"


_original_hot_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_hot(application: Any):
    # Before the paper runtime builds, so the HOT symbol is permitted when its run is opened.
    slot = controller.configure(slot_path())
    if slot is not None:
        try:
            admit_dynamic(slot.symbol)
        except Exception:
            pass
    async with _original_hot_lifespan(application) as state:
        yield state


app.router.lifespan_context = _lifespan_with_hot


def _paper_view(symbol: str | None) -> dict[str, Any] | None:
    if symbol is None:
        return None
    if runtime.base_config is None:
        return {"available": False, "reason": runtime.error or "PAPER 런 설정이 없습니다."}
    if symbol in runtime.sessions or symbol in runtime.retired or symbol in SUPPORTED_SYMBOLS:
        return {"available": True, "reason": None}
    if hot_market.find_reference(runtime.root, symbol) is None:
        return {"available": False, "reason": runtime.symbol_errors.get(symbol)
                or f"{symbol} PAPER 마진표가 측정되지 않았습니다."}
    return {"available": True, "reason": None}


def _hot_body(lock_decision: Any | None = None) -> dict[str, Any]:
    slot = controller.current()
    decision = lock_decision or controller.lock()
    held = [s for s in runtime.held_symbols() if s not in (DEFAULT_SYMBOL, slot.symbol if slot else None)]
    return {
        "main": DEFAULT_SYMBOL,
        "hot": slot.as_dict() if slot else None,
        "lock": decision.as_dict(),
        "scanning": controller.scanning,
        "held": held,
        "permitted": list(permitted_symbols()),
        "paper": _paper_view(slot.symbol if slot else None),
        "server_time_ms": int(__import__("time").time() * 1000),
    }


#: How old the LIVE account's part of the lock may be on the polled LIVE screen.
VIEW_LIVE_TTL_MS = 30_000
#: `lock_basis` values: which reading of the account the displayed lock was taken from.
LOCK_FRESH = "FRESH"
LOCK_PAPER_ONLY = "PAPER_ONLY"
LOCK_LIVE_RECENT = "LIVE_RECENT_30S"


class _RecentExposure:
    """A source that reads the exchange, answered from its recent reading for a display."""

    def __init__(self, source: Any) -> None:
        self.source = source

    def exposure(self, symbol: str) -> SymbolExposure:
        return self.source.recent_exposure(symbol, VIEW_LIVE_TTL_MS)


def _view_lock(source: str | None) -> tuple[LockDecision, str]:
    """The lock as the polled screen shows it, which is not the lock a scan obeys.

    The PAPER screen leaves the exchange-reading sources out: its 5 s poll made two signed
    Binance reads per call for a button an operator presses a few times a day. The LIVE screen
    reuses a signed read for up to 30 s. Either way the scan itself (`POST /hot/scan`) re-reads
    every source fresh before and after, so a stale or partial display can make the button
    look available but cannot let a replacement through. No `source` is the original, fresh
    reading, for any caller that does not say which screen it is.
    """
    if source not in ("PAPER", "BINANCE_LIVE"):
        return controller.lock(), LOCK_FRESH
    parts: list[Any] = []
    for item in list(controller.sources):
        if getattr(item, "reads_exchange", False):
            if source == "PAPER":
                continue
            parts.append(_RecentExposure(item))
        else:
            parts.append(item)
    slot = controller.current()
    decision = scan_permission(slot.symbol if slot else None, CombinedExposure(parts))
    return decision, (LOCK_PAPER_ONLY if source == "PAPER" else LOCK_LIVE_RECENT)


@app.get("/api/crypto/hot")
async def hot_state(source: str | None = None) -> Any:
    """The current HOT symbol and whether 코인탐색 may replace it right now.

    `source` names the account on screen (PAPER or BINANCE_LIVE); see `_view_lock`."""
    def read() -> dict[str, Any]:
        decision, basis = _view_lock(source)
        return {**_hot_body(decision), "lock_basis": basis}
    body = await asyncio.to_thread(read)
    return jsonable(body)


@app.post("/api/crypto/hot/scan")
async def hot_scan() -> Any:
    """Scan, and replace the HOT symbol with the scan's #1 if the lock allows it at both ends."""
    def run():
        return controller.scan_and_replace(lambda: service.scan().result)
    try:
        outcome = await asyncio.to_thread(run)
    except ScanFailed as exc:
        return error(_FAILURE_STATUS.get(exc.code, 502), exc.code, exc.message)
    except Exception as exc:
        return error(502, "HOT_SCAN_FAILED", f"탐색에 실패했습니다: {type(exc).__name__}")
    if outcome.code == HOT_SCAN_IN_PROGRESS:
        return error(409, HOT_SCAN_IN_PROGRESS, "이미 탐색 중입니다.")
    if not outcome.decision.allowed:
        body = {"error": {"code": outcome.decision.code, "message": outcome.decision.message},
                **_hot_body(outcome.decision)}
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content=jsonable(body))
    released = None
    if outcome.changed and outcome.previous:
        previous = outcome.previous
        try:
            released = await runtime.retire(previous)
            if previous not in SUPPORTED_SYMBOLS:
                retire_dynamic(previous)
        except RuntimeError as exc:
            # A position appeared after the lock: the symbol stays admitted and is served as a
            # held symbol so it can be closed. The new HOT symbol stands.
            released = {"symbol": previous, "kept": str(exc)}
    result = outcome.result
    body = _hot_body(outcome.decision)
    body.update({
        "status": outcome.code, "changed": outcome.changed, "previous": outcome.previous,
        "released": released, "paper_note": outcome.prepare_error,
        "scan": {"scanned_at_ms": result.scanned_at_ms, "universe_size": result.universe_size,
                 "stages": result.stage_counts, "rejection_counts": result.rejection_counts(),
                 "api": {**result.api.as_dict(), "duration_ms": result.duration_ms}}
        if result is not None else None,
    })
    return jsonable(body)

"""`GET /api/crypto/hot-coins`: the HOT Coin Finder, read-only.

Registered from its own module and imported last by `server.py`, the arrangement `live_routes`
and `c1_routes` use, so `api.py` stays untouched. One GET route and nothing else: no account,
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

"""Read-only HTTP surface for the C1 signal layer.

A separate module registering on the same app, for the reason stated at the top of `server.py`:
two sessions editing one `create_app()` do not merge. `api.py` is untouched.

Two safety properties this module is responsible for:

* Every route here is a GET except `/attribute`, and that one writes a line to C1's own ledger -
  it places no order, moves no leverage, arms nothing and touches neither the PAPER account nor
  the Binance account. There is no code path from this module to an exchange write.
* The loop is off unless `CRYPTO_C1_SIGNAL=on`. Importing this module into the deployed app
  therefore changes nothing about a running terminal: the routes answer 503 and no venue is
  polled until the environment says otherwise.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..c1.contract import CONTRACT_DOC, CONTRACT_SHA256, DIRECTION_CONTRACT, SCHEMA_VERSION
from ..c1.runtime import C1Runtime
from ..c1.store import DEFAULT_ROOT
from .api import app, error, jsonable

ENABLE_ENV = "CRYPTO_C1_SIGNAL"
ROOT_ENV = "CRYPTO_C1_ROOT"
FIXTURE_ENV = "C1_FIXTURE"

c1_runtime: C1Runtime | None = None


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "off").strip().lower() in ("on", "1", "true", "yes")


def _root() -> Path:
    return Path(os.environ.get(ROOT_ENV, str(DEFAULT_ROOT)))


def _unavailable() -> Any:
    return error(503, "C1_SIGNAL_DISABLED",
                 f"C1 신호 레이어가 꺼져 있습니다 ({ENABLE_ENV}=on 으로 켭니다).")


_previous_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_c1(application: Any):
    global c1_runtime
    async with _previous_lifespan(application) as state:
        try:
            if enabled():
                fixture = os.environ.get(FIXTURE_ENV, "").strip()
                if fixture:
                    from ..c1.fixture import FixtureRuntime

                    c1_runtime = FixtureRuntime(_root(), Path(fixture))
                else:
                    c1_runtime = C1Runtime(_root())
                await c1_runtime.start()
            yield state
        finally:
            if c1_runtime is not None:
                await c1_runtime.stop()
                c1_runtime = None


app.router.lifespan_context = _lifespan_with_c1


@app.get("/api/crypto/c1/state")
async def c1_state() -> Any:
    """Everything the screen needs about the signal layer, in one read."""
    if not enabled() or c1_runtime is None:
        return jsonable({"enabled": False, "schema_version": SCHEMA_VERSION,
                         "direction_contract": DIRECTION_CONTRACT,
                         "contract": {"document": CONTRACT_DOC, "sha256": CONTRACT_SHA256},
                         "ready": False, "active": [], "signals_total": 0,
                         "server_time_ms": int(time.time() * 1000)})
    body = c1_runtime.snapshot()
    body["enabled"] = True
    body["server_time_ms"] = int(time.time() * 1000)
    return jsonable(body)


@app.get("/api/crypto/c1/markers")
async def c1_markers(from_ms: int | None = None, to_ms: int | None = None,
                     limit: int = 500) -> Any:
    """The signal series the chart draws, for a time range.

    One series for every timeframe: the caller asks for a window of history, not for a timeframe.
    """
    if not enabled() or c1_runtime is None:
        return jsonable({"enabled": False, "markers": []})
    return jsonable({"enabled": True, "markers": c1_runtime.markers(from_ms, to_ms,
                                                                    min(max(limit, 1), 2000))})


@app.get("/api/crypto/c1/ledger")
async def c1_ledger(limit: int = 200) -> Any:
    """The shadow ledger: official 4 h results plus the observation horizons.

    Its own ledger, never merged with the PAPER or LIVE one. `summary` covers the official
    horizon only; the observations in each row are research material.
    """
    if not enabled() or c1_runtime is None:
        return _unavailable()
    from ..c1.shadow import summarize

    rows = sorted((trade.to_json() for trade in c1_runtime.trades.values()),
                  key=lambda row: row.get("entry_at_ms") or 0, reverse=True)
    return jsonable({"ledger": "C1_SHADOW", "isolated_from": ["MANUAL_PAPER", "MANUAL_LIVE", "AUTO"],
                     "summary": summarize(c1_runtime.trades.values()),
                     "total": len(rows), "trades": rows[:min(max(limit, 1), 1000)]})


@app.get("/api/crypto/c1/c1x")
async def c1x_diagnostics(limit: int = 200) -> Any:
    """The premium-normalization diagnostic records, newest first, with the E0 pairing.

    A diagnostic, not an exit: every record says so, no route here closes anything, and the
    forward tally carries `forward_sample_sufficient` so an early count is not read as a result.
    """
    if not enabled() or c1_runtime is None:
        return _unavailable()
    rows = sorted((event.to_json() for event in c1_runtime.c1x.values()),
                  key=lambda row: row.get("entry_at_ms") or 0, reverse=True)
    return jsonable({"id": "C1x", "is_exit": False,
                     "meaning": "PREMIUM_NORMALIZATION_DIAGNOSTIC",
                     "summary": c1_runtime.c1x_summary(),
                     "total": len(rows), "events": rows[:min(max(limit, 1), 1000)]})


@app.get("/api/crypto/c1/attribution")
async def c1_attribution() -> Any:
    """Links the operator declared, and the signals they could declare one against."""
    if not enabled() or c1_runtime is None:
        return _unavailable()
    return jsonable({"source": "USER_DECLARED", "inferred": 0,
                     "attributable_signal_ids": c1_runtime.attributable(),
                     "attributions": c1_runtime.attributions()})


class AttributionRequest(BaseModel):
    signal_id: str
    account: str
    trade_ref: str
    note: str = ""


@app.post("/api/crypto/c1/attribute")
async def c1_attribute(request: AttributionRequest) -> Any:
    """Record that a trade the operator made came from a C1 signal.

    The operator's statement is the only thing that creates this link. This route writes one line
    to C1's attribution file; it has no access to an order, a position or a leverage setting.
    """
    if not enabled() or c1_runtime is None:
        return _unavailable()
    account = request.account.strip().upper()
    if account not in ("PAPER", "BINANCE_LIVE"):
        return error(400, "UNKNOWN_ACCOUNT", "account must be PAPER or BINANCE_LIVE")
    if not request.trade_ref.strip():
        return error(400, "TRADE_REF_REQUIRED", "trade_ref must identify the operator's own trade")
    try:
        attribution = c1_runtime.attribute(signal_id=request.signal_id, account=account,
                                           trade_ref=request.trade_ref.strip(), note=request.note)
    except KeyError:
        return error(404, "UNKNOWN_SIGNAL", f"no such C1 signal: {request.signal_id}")
    return jsonable({"attribution": attribution.to_json()})

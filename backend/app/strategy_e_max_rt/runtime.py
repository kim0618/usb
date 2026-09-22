"""Host the E-MAX V1 engine inside the common runtime process, isolated from Strategy A.

``start_from_env`` is the only call ``app.main`` makes. With ``STRATEGY_E_MAX_ENABLED`` unset or false
it returns without doing anything, so A's startup path is unchanged. When enabled it starts one
asyncio task that ticks the engine every ``interval`` seconds in a worker thread; any exception is
caught per tick and recorded as E's ERROR state, never propagated to A's loops. A's broker, account,
tables and risk engine are never touched: E owns its own SimBroker book and JSON state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from app.market.calendar import MarketCalendar
from app.market.provider import MarketDataProvider
from app.strategy_e_max_rt import config as CFG, decision as DEC, engine as ENG, kiwoom_capability as KC

logger = logging.getLogger(__name__)
READINESS_DIR = CFG.REPO_ROOT / "data/runtime/strategy_e_max/forward/collection/readiness"
_task: asyncio.Task | None = None
_engine: ENG.Engine | None = None
_status: dict[str, Any] = {"strategy_id": CFG.STRATEGY_ID, "phase": ENG.Phase.DISABLED}


def latest_universe_rows(directory: Path = READINESS_DIR) -> int | None:
    """The most recent D-1 eligible-universe size the forward collector measured, if any."""
    files = sorted(directory.glob("readiness_*.json")) if directory.exists() else []
    for path in reversed(files):
        counts = json.loads(path.read_text(encoding="utf-8")).get("inventory", {}).get("eligible_universe") or {}
        if counts:
            return int(counts[sorted(counts)[-1]])
    return None


@dataclass(frozen=True)
class KiwoomMeasuredCapacity:
    """The measured Kiwoom limits (E-RT1 capability manifest) applied to the canonical universe."""

    universe_rows: int | None
    source: str = "KIWOOM_NATIVE"

    def blockers(self) -> list[str]:
        if self.universe_rows is None:
            return ["canonical D-1 universe size unknown in this runtime"]
        out = KC.blockers(self.universe_rows)
        out.append("RVOL denominator: no whole-universe 20-session premarket history from Kiwoom, and Kiwoom "
                   "premarket volume is not the Massive development measure (irregular extra prints)")
        return out


def kiwoom_capacity(session: date) -> KiwoomMeasuredCapacity:
    return KiwoomMeasuredCapacity(latest_universe_rows())


def build_engine(provider_factory: Callable[[], MarketDataProvider], *, config: CFG.RuntimeConfig | None = None,
                 decision_source: DEC.DecisionSource | None = None,
                 calendar: MarketCalendar | None = None) -> ENG.Engine:
    config = config or CFG.from_env()
    source = decision_source or DEC.RealtimeSourceGate(kiwoom_capacity, source="KIWOOM_NATIVE")
    return ENG.Engine(config, provider_factory, source, calendar or MarketCalendar("America/New_York"),
                      ENG.Store(config.state_dir, config.strategy_id))


async def _loop(engine: ENG.Engine, clock: Callable[[], datetime], interval: float) -> None:
    global _status
    while True:
        try:
            _status = await asyncio.to_thread(engine.tick, clock())
        except asyncio.CancelledError:
            raise
        except Exception as error:     # isolation: E never takes the process or A's loops down
            logger.exception("E-MAX tick failed")
            _status = {"strategy_id": engine.config.strategy_id, "phase": ENG.Phase.ERROR, "error": repr(error)}
        await asyncio.sleep(interval)


def start(engine: ENG.Engine, *, clock: Callable[[], datetime] | None = None, interval: float = 15.0) -> bool:
    global _task, _engine
    if not engine.config.enabled:
        return False
    if _task is not None and not _task.done():
        raise RuntimeError("E-MAX runtime already started")
    _engine = engine
    _task = asyncio.get_running_loop().create_task(_loop(engine, clock or (lambda: datetime.now(timezone.utc)), interval),
                                                   name="strategy-e-max-v1")
    logger.warning("STRATEGY E-MAX V1: SIMULATION / VIRTUAL ONLY (live margin approved=%s)", CFG.LIVE_MARGIN_APPROVED)
    return True


def start_from_env(provider_factory: Callable[[], MarketDataProvider]) -> bool:
    """Called once from the app lifespan; never raises into A's startup."""
    try:
        config = CFG.from_env()
        if not config.enabled:
            return False
        return start(build_engine(provider_factory, config=config))
    except Exception:
        logger.exception("E-MAX runtime failed to start; Strategy A continues")
        return False


async def stop() -> None:
    global _task, _engine
    task, _task, _engine = _task, None, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def status() -> dict[str, Any]:
    running = _task is not None and not _task.done()
    return {**_status, "task_running": running, "mode": "SIMULATION_VIRTUAL_ONLY",
            "live_margin_approved": CFG.LIVE_MARGIN_APPROVED}

"""The panel's own ASGI app: GET only, on its own port, registered on nothing else.

Why a separate app rather than routes on the deployed terminal, restated here because it is the
whole isolation argument. The terminal app owns the manual order path, the arm session and the AUTO
controls. Adding routes there - even read-only ones - puts this code in the same process, inside the
same lifespan, behind the same deploy. A separate app on a separate port cannot reach the order path
even by accident, can be started and killed without touching the terminal, and cannot be deployed
to production by forgetting something, because nothing in production imports it.

Every route is a GET. The only thing this process mutates is the append-only forward context
journal, and that happens on a timer inside the lifespan rather than on a request, so no HTTP call
can cause a write. The timer only exists when `MC_V1_JOURNAL_ROOT` is set; with it unset the
process writes nothing at all.

    MS_V0_ROOT=/path/to/journal \
    MC_V1_JOURNAL_ROOT=/path/to/tape \
    python -m app.crypto.market_context_v1 8012
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import PREVIEW_VERSION
from . import assemble as ASM
from . import contract as C
from . import liquidity as LIQ
from . import structure as STR
from .assemble import Panel

#: Default port for the isolated preview. Chosen away from the terminal's 8000 and away from the
#: Liquidity Map preview's 8011, so all three can run at once.
DEFAULT_PORT = 8012
#: How often the journal timer polls. The collector's own cadence: a faster tick would record the
#: same sample twice and a slower one would lose the mid path the wall-vanish test needs.
JOURNAL_TICK_S = 1.0
#: Anchor length, overridable for a cheaper start in a constrained environment.
ANCHOR_DAYS_ENV = "MC_V1_ANCHOR_DAYS"
#: How much published history the replay route keeps, in hours.
#:
#: It changes **only** how many already-published minutes are retained for `structure/replay`:
#: the series, the anchor, the level book and the thetas are untouched, and the live reading is
#: the last closed minute either way. It exists because the states section 10 asks for - a range,
#: a failed break - are not produced on request, and over the default 24 h window the last day of
#: BTCUSDT held neither.
PUBLISH_WINDOW_H_ENV = "MC_V1_PUBLISH_WINDOW_H"


def anchor_days() -> int:
    raw = os.environ.get(ANCHOR_DAYS_ENV)
    if not raw:
        return C.ANCHOR_DAYS
    try:
        parsed = int(raw)
    except ValueError:
        return C.ANCHOR_DAYS
    return parsed if 2 <= parsed <= 120 else C.ANCHOR_DAYS


def publish_window_ms() -> int:
    raw = os.environ.get(PUBLISH_WINDOW_H_ENV)
    if not raw:
        return C.PUBLISH_WINDOW_MS
    try:
        hours = int(raw)
    except ValueError:
        return C.PUBLISH_WINDOW_MS
    return hours * 3_600_000 if 1 <= hours <= 24 * 90 else C.PUBLISH_WINDOW_MS


class PanelState:
    """One `Panel` for the life of the process, so the anchor and the trackers are stable."""

    def __init__(self) -> None:
        self.panel: Panel | None = None

    def get(self) -> Panel:
        if self.panel is None:
            self.panel = Panel(anchor_days=anchor_days(),
                               publish_window_ms=publish_window_ms())
        return self.panel


state = PanelState()


async def _journal_timer(panel: Panel) -> None:
    """Keep the tape going while the process is up, whether or not a browser is watching.

    A tape that only advances when someone has the screen open would be a record of when the
    operator was looking, not of what the market did, and the questions this is collected for are
    about the market.
    """
    while True:
        try:
            await asyncio.to_thread(panel.poll, C.BTC)
        except Exception:
            # A tape that stops the process it rides on would be worse than a gap in the tape.
            pass
        await asyncio.sleep(JOURNAL_TICK_S)


@contextlib.asynccontextmanager
async def lifespan(application: FastAPI):
    """Take the journal's writer lock at boot, and give it back at shutdown.

    Acquired here rather than left to the first closed minute so that a second preview against the
    same tape is refused while someone is still looking at the console that started it, instead of
    a minute later in a log nobody reads. The refusal is **not** a startup failure: the panel is a
    read-only screen and the tape is one of the things it reports on, so a refused writer serves
    every route exactly as before and says `REFUSED` in `journal.status`. The timer is the part
    that is skipped, because its only job is to write.
    """
    panel = state.get()
    task = None
    if panel.journal.enabled:
        panel.journal.start()
        if panel.journal.writable:
            task = asyncio.create_task(_journal_timer(panel))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        panel.journal.stop()


app = FastAPI(title="US-B Manual Market Context V1 preview", version=PREVIEW_VERSION,
              description="Read-only context panel over already validated layers.",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def _symbol(raw: str | None) -> str | None:
    symbol = (raw or C.BTC).upper()
    return symbol if symbol in C.SUPPORTED_SYMBOLS else None


@app.get("/api/market-context/health")
async def health() -> Any:
    panel = state.get()
    return {"panel_version": PREVIEW_VERSION, "mode": "READ_ONLY_CONTEXT_PANEL",
            "contract": C.contract_identity(),
            "supported_symbols": list(C.SUPPORTED_SYMBOLS),
            "symbol_support": ASM.SYMBOL_SUPPORT,
            "layer_order": list(C.LAYERS),
            "freshness_bounds": C.freshness_bounds(),
            "anchor_ms": panel.anchor_ms, "anchor_days": panel.anchor_days,
            "publish_window_ms": panel.publish_window_ms,
            "journal": panel.journal.view(),
            "liquidity_root_env": "MS_V0_ROOT",
            "liquidity_root": os.environ.get("MS_V0_ROOT"),
            "server_time_ms": int(time.time() * 1000)}


@app.get("/api/market-context/panel")
async def panel(symbol: str | None = None, min_notional_usdt: str | None = None) -> Any:
    """One poll of the whole panel. The screen's only endpoint."""
    resolved = _symbol(symbol)
    if resolved is None:
        return error(400, "SYMBOL_NOT_SUPPORTED",
                     f"symbol must be one of {', '.join(C.SUPPORTED_SYMBOLS)}")
    snapshot = None
    if resolved == C.BTC:
        snapshot = await LIQ._read_async(min_notional_usdt=min_notional_usdt,
                                         wall_limit=LIQ.DEFAULT_WALL_LIMIT)
    return await asyncio.to_thread(state.get().poll, resolved,
                                   liquidity_snapshot=snapshot,
                                   min_notional_usdt=min_notional_usdt)


@app.get("/api/market-context/structure/states")
async def structure_states() -> Any:
    """Which published minutes show each state the preview has to be checked against.

    This exists so that "we verified the panel near resistance, near support, on a breakout and
    inside a range" is a navigation to real minutes rather than a wait for the market to oblige.
    It reads the same published rows the live panel reads and invents nothing.
    """
    panel_obj = state.get()
    panel_obj.refresh_structure(C.BTC, int(time.time() * 1000))
    run = panel_obj.runs.get(C.BTC)
    if run is None:
        return {"available": False,
                "unavailable_reason": panel_obj.structure_errors.get(C.BTC, "NO_SERIES_YET"),
                "cases": {}}
    cases: dict[str, list[dict[str, Any]]] = {name: [] for name in (
        "NEAR_RESISTANCE", "NEAR_SUPPORT", "BREAKOUT_FRESH", "IN_RANGE", "RETEST_OPEN",
        "FAILED_BREAK", "NO_LEVEL_ABOVE", "NO_LEVEL_BELOW")}
    for row in run.rows:
        entry = {"info_ts": row["info_ts"], "close": row["close"],
                 "res_price": row.get("res_price"), "res_distance_pct": row.get("res_distance_pct"),
                 "sup_price": row.get("sup_price"), "sup_distance_pct": row.get("sup_distance_pct"),
                 "range_state": row.get("range_state"),
                 "retest_state": row.get("retest_state"),
                 "breakout_age_min": row.get("last_breakout_age_min"),
                 "failed_break_state": row.get("failed_break_state")}
        res, sup = row.get("res_distance_pct"), row.get("sup_distance_pct")
        if res is not None and abs(float(res)) <= 0.10:
            cases["NEAR_RESISTANCE"].append(entry)
        if sup is not None and abs(float(sup)) <= 0.10:
            cases["NEAR_SUPPORT"].append(entry)
        age = row.get("last_breakout_age_min")
        if isinstance(age, int) and age <= 15:
            cases["BREAKOUT_FRESH"].append(entry)
        if row.get("range_state") == "IN_RANGE":
            cases["IN_RANGE"].append(entry)
        if row.get("retest_state") in ("PENDING", "RETESTED"):
            cases["RETEST_OPEN"].append(entry)
        if row.get("failed_break_state") not in (None, "NONE"):
            cases["FAILED_BREAK"].append(entry)
        if row.get("res_price") is None:
            cases["NO_LEVEL_ABOVE"].append(entry)
        if row.get("sup_price") is None:
            cases["NO_LEVEL_BELOW"].append(entry)
    return {"available": True, "unavailable_reason": None,
            "published_minutes": len(run.rows),
            "publish_from_ms": run.publish_from_ms, "publish_to_ms": run.publish_to_ms,
            "cases": {name: {"minutes": len(rows), "first": rows[0] if rows else None,
                             "samples": rows[:3], "last": rows[-1] if rows else None}
                      for name, rows in cases.items()}}


@app.get("/api/market-context/structure/replay")
async def structure_replay(info_ts: int, symbol: str | None = None) -> Any:
    """The PRICE layer as it was published for one past minute. Labelled REPLAY, never live.

    Only the structure layer can be replayed: the book and flow layers come from a journal whose
    historical samples this viewer does not reassemble, and the derivatives endpoints have no
    history at this resolution. Pretending otherwise would put four live layers beside one
    historical one under a single timestamp, which is the worst thing a context panel can do.
    """
    resolved = _symbol(symbol)
    if resolved is None:
        return error(400, "SYMBOL_NOT_SUPPORTED", "symbol must be supported")
    if resolved != C.BTC:
        return error(400, "REPLAY_IS_BTC_ONLY", "only BTCUSDT has a structure layer to replay")
    panel_obj = state.get()
    panel_obj.refresh_structure(C.BTC, int(time.time() * 1000))
    run = panel_obj.runs.get(C.BTC)
    if run is None:
        return error(503, "NO_SERIES_YET",
                     panel_obj.structure_errors.get(C.BTC, "the series is not loaded"))
    row = run.row_at(info_ts)
    if row is None:
        return error(404, "MINUTE_NOT_PUBLISHED",
                     f"{info_ts} is outside {run.publish_from_ms}..{run.publish_to_ms}")
    frozen = STR.StructureRun(
        anchor_ms=run.anchor_ms, series_first_ms=run.series_first_ms,
        series_last_open_ms=run.series_last_open_ms, series_rows=run.series_rows,
        publish_from_ms=run.publish_from_ms, publish_to_ms=run.publish_to_ms, rows=[row],
        computed_at_ms=run.computed_at_ms, recompute_ms=run.recompute_ms,
        repaint_violations=run.repaint_violations, vanished_levels=run.vanished_levels)
    return {"panel_version": PREVIEW_VERSION, "mode": "READ_ONLY_STRUCTURE_REPLAY",
            "replay": {"is_replay": True, "info_ts": info_ts,
                       "layers_replayed": ["PRICE"],
                       "layers_not_replayed": ["LIQUIDITY", "FLOW", "DERIVATIVES", "AUXILIARY"],
                       "reason": "ONLY_THE_BAR_SERIES_HAS_HISTORY_AT_THIS_RESOLUTION"},
            "symbol": resolved,
            "contract": C.contract_identity(),
            "server_time_ms": int(time.time() * 1000),
            "layers": {"PRICE": STR.structure_view(frozen, symbol=resolved,
                                                   now_ms=int(time.time() * 1000))},
            "notes": C.notes_payload()}


@app.get("/api/market-context/structure/prefix-check")
async def prefix_check(cut: int = 60) -> Any:
    """Run the anchored series truncated and whole, and compare the minutes they share.

    The measurement behind the one promise the structure layer makes: extending the series forward
    does not move a line already on the screen.
    """
    if cut < 1 or cut > 2000:
        return error(400, "CUT_OUT_OF_RANGE", "cut must be 1..2000 minutes")
    panel_obj = state.get()
    panel_obj.refresh_structure(C.BTC, int(time.time() * 1000))
    series = panel_obj.series.get(C.BTC)
    if series is None or len(series.rows) <= cut:
        return error(503, "NO_SERIES_YET", "the series is not long enough yet")
    return await asyncio.to_thread(
        STR.prefix_check, series.rows, cut=cut,
        publish_window_ms=panel_obj.publish_window_ms)


__all__ = ["app", "state", "PanelState", "DEFAULT_PORT", "JOURNAL_TICK_S",
           "anchor_days", "publish_window_ms", "lifespan"]

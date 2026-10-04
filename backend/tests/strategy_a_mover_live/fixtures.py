"""Shared stand-ins for the A-MOVER-LIVE-V1 suite. No network, no provider, no replay.

The Kiwoom pages are the recorded production payload the repository already keeps
(``tests/fixtures/kiwoom_us_raw_production.json``), re-dated onto the test session, so the
timestamp parsing, the newest-first page order and the 24:xx business-hour rows are the real
feed's and not an idealisation of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from app.backtest.mover_scanner_v1.daily import DailyPanel
from app.integrations.kiwoom.client import KiwoomPage
from app.strategy_e_max_rt import finalizer as FZ

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 9, 15)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def recorded_premarket_rows() -> list[dict]:
    body = json.loads((FIXTURES / "kiwoom_us_raw_production.json").read_text(encoding="utf-8"))
    return list(body["amd_20260910_premarket"])


def redate(rows: Sequence[Mapping[str, str]], session: date) -> list[dict]:
    """The recorded rows moved onto ``session``, keeping only that business date's rows."""
    stamp = session.strftime("%Y%m%d")
    out = []
    for row in rows:
        if str(row["bus_dt"]) != "20260910":
            continue
        clock = str(row["cntr_tm"])[8:]
        out.append(dict(row) | {"bus_dt": stamp, "cntr_tm": stamp + clock})
    return out


def minute_rows(session: date, minutes: Sequence[int], *, price: float = 10.0,
                volume: float = 1000.0) -> list[dict]:
    """Synthetic minute rows in Kiwoom's own shape, newest first as the feed returns them."""
    stamp = session.strftime("%Y%m%d")
    rows = []
    for minute in sorted(minutes, reverse=True):
        rows.append({"bus_dt": stamp,
                     "cntr_tm": f"{stamp}{minute // 60:02d}{minute % 60:02d}00",
                     "open_pric": f"{price:.4f}", "high_pric": f"{price + 0.2:.4f}",
                     "low_pric": f"{price - 0.2:.4f}", "cur_prc": f"{price + 0.1:.4f}",
                     "trde_qty": f"{volume:.0f}"})
    return rows


class VirtualClock:
    """A monotonic ET clock the tests advance explicitly; nothing here sleeps."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.calls = 0

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def at(self, moment: time, session: date | None = None) -> datetime:
        return datetime.combine(session or self.now.date(), moment, tzinfo=ET)


@dataclass
class RecordingClient:
    """A Kiwoom client stand-in: one page per symbol, every request counted per API id."""

    pages: Mapping[str, list[dict]]
    clock: VirtualClock | None = None
    seconds_per_call: float = 1 / 4.9
    request_counts: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    continuation_pages: int = 0
    #: A deep feed: every page reports more history behind it, so a catch-up budget runs out.
    always_continue: bool = False

    def request(self, api_id: str, path: str, body: Mapping[str, str], continuation=None
                ) -> KiwoomPage:
        self.request_counts[api_id] = self.request_counts.get(api_id, 0) + 1
        symbol = str(body["stk_cd"])
        self.calls.append((api_id, symbol))
        if self.clock is not None:
            self.clock.tick(self.seconds_per_call)
        rows = list(self.pages.get(symbol, []))
        more = self.always_continue or (self.continuation_pages > 0 and continuation is None)
        return KiwoomPage(body={"result_list": rows}, continuation=more, next_key=None)


def lane(name: str, api_id: str, client: RecordingClient) -> FZ.Lane:
    return FZ.Lane(name, api_id, client)   # type: ignore[arg-type]


def cache(symbol: str, exchange: str = "ND", *, complete_through: int | None = None,
          bars: Mapping[int, Sequence[float]] | None = None) -> FZ.SymbolCache:
    item = FZ.SymbolCache(symbol, exchange, symbol)
    if bars:
        item.bars = {minute: list(values) for minute, values in bars.items()}
    item.complete_through = complete_through
    return item


def daily_panel(symbols: Sequence[str], session: date, *, sessions: int = 30,
                close: float = 10.0, volume: float = 5_000_000.0) -> DailyPanel:
    """A flat daily history ending the session before ``session``, with an empty live column."""
    grid = [session - timedelta(days=sessions - index) for index in range(sessions)] + [session]
    span = len(grid)
    closes, volumes = {}, {}
    for symbol in symbols:
        closes[symbol] = np.full(span, np.nan)
        volumes[symbol] = np.full(span, np.nan)
        for position in range(span - 1):
            closes[symbol][position] = close
            volumes[symbol][position] = volume
    return DailyPanel(tuple(grid), closes, volumes)

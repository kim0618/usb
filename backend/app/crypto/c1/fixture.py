"""A C1 runtime that serves recorded signals instead of polling a venue.

For the preview screen only. A reviewer has to be able to look at a LONG marker, its 4 h detail
and a settled result now, and real C1 events are rare - 0.7% of bars, 180 days in five years - so
waiting for the market to produce one is not a review.

What this changes and what it does not: it changes where signals come from. It does not change
what a signal is. The records it loads were produced by the real engine replaying real history
(`scripts/c1_fixture.py`), so the shapes, the ids, the feature snapshots and the 4 h arithmetic on
the preview screen are the production ones.

It cannot be reached in production: `c1_routes` only builds it when `C1_FIXTURE` names a file,
and nothing sets that variable unless a person does.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import C1xEvent, ShadowTrade, Signal
from .runtime import C1Runtime


class FixtureRuntime(C1Runtime):
    MODE = "FIXTURE"

    def __init__(self, root: Path | str, fixture: Path | str) -> None:
        super().__init__(root, market=None)
        self.fixture = Path(fixture)

    @staticmethod
    def write_fixture(path: Path | str, body: dict[str, Any], *, indent: int | None = None) -> Path:
        """Write a preview fixture, creating its directory tree first.

        Fixture producers may target a fresh nested preview directory. Keeping directory creation
        in the fixture contract prevents scripts and tests from each making different assumptions
        about which parent already exists.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, indent=indent), encoding="utf-8")
        return target

    def bootstrap(self) -> None:
        body = json.loads(self.fixture.read_text(encoding="utf-8"))
        self.signals = {row["signal_id"]: Signal.from_json(row) for row in body.get("signals", [])}
        self.trades = {row["signal_id"]: ShadowTrade.from_json(row) for row in body.get("shadow", [])}
        self.c1x = {row["signal_id"]: C1xEvent.from_json(row) for row in body.get("c1x", [])}
        self.last_decided_at_ms = body.get("last_decided_at_ms")
        self.ready = True
        self.error = None

    def refresh_grid(self) -> None:          # no venue, no grid
        return

    def refresh_c1x(self) -> None:           # the diagnostics come from the file, not the market
        return

    def tick(self) -> None:
        self.last_tick_ms = self.now_ms()

    async def start(self) -> None:
        self.bootstrap()

    async def stop(self) -> None:
        return

    def snapshot(self) -> dict[str, Any]:
        body = super().snapshot()
        body["mode"] = self.MODE
        body["fixture"] = str(self.fixture)
        body["fixture_note"] = ("미리보기 전용 고정 데이터입니다. 실제 시장 평가 루프가 아닙니다.")
        return body

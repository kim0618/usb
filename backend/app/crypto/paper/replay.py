"""Replay driver: pacing only.

The engine's clock is the tape, always. This driver decides *when* the next record is handed
over in wall-clock terms, never what time the engine thinks it is. That separation is why
`STEP`, `ACCELERATED` and `REALTIME` all produce the same ledger from the same tape.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import PaperRunConfig
from .engine import PaperEngine, apply_tape_record
from .instrument import RiskTierTable
from .ledger import InputKind, InputTape, Ledger

STEP = "STEP"
ACCELERATED = "ACCELERATED"
REALTIME = "REALTIME"
MODES = (STEP, ACCELERATED, REALTIME)


@dataclass
class ReplayDriver:
    """Feeds recorded input into a paper engine at a chosen pace."""
    config: PaperRunConfig
    tiers: RiskTierTable
    records: Sequence[dict[str, Any]]
    mode: str = ACCELERATED
    speed: Decimal = Decimal(1)
    ledger_path: Path | None = None
    sleeper: Callable[[float], None] = time.sleep
    engine: PaperEngine = field(init=False)
    cursor: int = field(default=0, init=False)
    _last_ts_ms: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown replay mode: {self.mode}")
        if self.speed <= 0:
            raise ValueError("speed must be positive")
        self.engine = PaperEngine(self.config, self.tiers,
                                  ledger=Ledger(path=self.ledger_path))

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.records)

    @property
    def progress(self) -> dict[str, Any]:
        current = self.records[self.cursor - 1] if self.cursor else None
        return {
            "mode": self.mode, "speed": str(self.speed),
            "cursor": self.cursor, "total": len(self.records),
            "finished": self.finished,
            "current_ts_ms": int(current["payload"]["ts_ms"]) if current else None,
            "ledger_events": len(self.engine.ledger.events),
        }

    def step(self, count: int = 1) -> int:
        """Apply up to `count` records. Returns how many were actually applied."""
        applied = 0
        while applied < count and not self.finished:
            record = self.records[self.cursor]
            if self.mode == REALTIME:
                self._pace(int(record["payload"]["ts_ms"]))
            apply_tape_record(self.engine, record)
            self._last_ts_ms = int(record["payload"]["ts_ms"])
            self.cursor += 1
            applied += 1
        return applied

    def run(self, *, limit: int | None = None) -> int:
        """Drive to the end, or `limit` records, whichever comes first."""
        target = len(self.records) - self.cursor if limit is None else limit
        return self.step(target)

    def _pace(self, ts_ms: int) -> None:
        if self._last_ts_ms is None:
            return
        gap = (ts_ms - self._last_ts_ms) / 1000 / float(self.speed)
        if gap > 0:
            self.sleeper(gap)


def replay_records(config: PaperRunConfig, tiers: RiskTierTable,
                   records: Sequence[dict[str, Any]]) -> PaperEngine:
    """Run a tape start to finish with no pacing. Used to prove mode independence."""
    driver = ReplayDriver(config=config, tiers=tiers, records=records, mode=ACCELERATED)
    driver.run()
    return driver.engine


def write_tape(records: Sequence[dict[str, Any]], path: Path) -> Path:
    """Persist a built tape so a replay run is reproducible from a file like a live one."""
    tape = InputTape(path=path)
    for record in records:
        tape.record(record["kind"], record["payload"])
    return path

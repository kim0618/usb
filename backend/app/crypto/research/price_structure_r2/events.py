"""R2 events: R1's machines, with the one hook S3 needs.

Contract: ``docs/crypto/price_structure_r2/CONTRACT_R2.md`` section 6.

Nothing about the breakout rule, the sweep rule, the buffer, the cooldown or the retest geometry
changes. The only addition is that a ``RETESTED`` termination is recorded, because that is the
instant at which contract section 6 says a flip may be claimed. The engine drains the queue
immediately afterwards, inside the same 5m step, in the order of contract section 3.6.
"""
from __future__ import annotations

from ..price_structure_r1.events import (  # carried over unchanged, contract section 2
    ALLOWED_TRANSITIONS,
    RESOLVED,
    TERMINAL,
    Breakout,
    RangeEdge,
    Retest,
    buffer,
)
from ..price_structure_r1.events import EventEngine as EventEngineR1


class EventEngine(EventEngineR1):
    def __init__(self) -> None:
        super().__init__()
        self.flip_queue: list[tuple] = []
        self.flips_claimed: list[dict] = []

    def _terminate(self, r: Retest, state: str, at_ms: int, reason: str) -> None:
        super()._terminate(r, state, at_ms, reason)
        if state == "RETESTED":
            self.flip_queue.append((r.breakout, at_ms))

    def drain_flips(self) -> list[tuple]:
        out, self.flip_queue = self.flip_queue, []
        return out

"""Control points: what an ordinary moment in the same market looked like.

Without this the entry statistics say nothing. Every moment in a trending hour has a negative
five minute return; finding one at the entries would prove only that the hour was trending. The
comparison has to be against moments the operator did *not* choose.

The rule is fixed here, before any entry feature is compared: same UTC day as the entry, at least
`MIN_SEPARATION_SEC` away from every manual entry, far enough from a position being open, and
with the same hour of tape history behind it. The seed is fixed so the draw is reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .context import Tape
from .ledger import ManualTrade

SEED = 20260928
CONTROLS_PER_ENTRY = 40
MIN_SEPARATION_SEC = 600          # 10 minutes from any manual entry
REQUIRED_HISTORY_SEC = 3600
DAY_MS = 86_400_000


@dataclass(frozen=True)
class ControlPoint:
    ts_ms: int
    matched_entry_index: int
    matched_side: str


def _position_intervals(trades: Sequence[ManualTrade],
                        open_position_ts: int | None, snapshot_ts: int) -> list[tuple[int, int]]:
    spans = [(t.opened_ts_ms, t.closed_ts_ms) for t in trades]
    if open_position_ts is not None:
        spans.append((open_position_ts, snapshot_ts))
    return spans


def sample(tape: Tape, trades: Sequence[ManualTrade], *,
           open_position_ts: int | None = None, snapshot_ts: int | None = None,
           per_entry: int = CONTROLS_PER_ENTRY) -> list[ControlPoint]:
    """Draw control instants for every manual entry, under the rule stated above."""
    rng = np.random.default_rng(SEED)
    snapshot_ts = snapshot_ts or int(tape.ts_ms[-1])
    entries = np.array([t.opened_ts_ms for t in trades], dtype=np.int64)
    busy = _position_intervals(trades, open_position_ts, snapshot_ts)
    separation_ms = MIN_SEPARATION_SEC * 1000

    out: list[ControlPoint] = []
    for trade in trades:
        day_start = (trade.opened_ts_ms // DAY_MS) * DAY_MS
        lo = max(int(tape.ts_ms[0]) + REQUIRED_HISTORY_SEC * 1000, day_start)
        hi = min(int(tape.ts_ms[-1]), day_start + DAY_MS)
        if hi <= lo:
            continue
        candidates = tape.ts_ms[(tape.ts_ms >= lo) & (tape.ts_ms <= hi)]
        if len(candidates) == 0:
            continue

        far_from_entries = np.min(np.abs(candidates[:, None] - entries[None, :]),
                                  axis=1) >= separation_ms
        usable = candidates[far_from_entries]
        if len(usable) == 0:
            continue
        in_position = np.zeros(len(usable), dtype=bool)
        for start, end in busy:
            in_position |= (usable >= start) & (usable <= end)
        usable = usable[~in_position]
        if len(usable) == 0:
            continue

        take = min(per_entry, len(usable))
        picked = rng.choice(usable, size=take, replace=False)
        out.extend(ControlPoint(ts_ms=int(stamp), matched_entry_index=trade.index,
                                matched_side=trade.side) for stamp in sorted(picked))
    return out


def describe_rule() -> dict[str, object]:
    return {
        "same_utc_day_as_the_entry": True,
        "min_separation_from_any_manual_entry_sec": MIN_SEPARATION_SEC,
        "excludes_moments_with_a_position_open": True,
        "required_tape_history_sec": REQUIRED_HISTORY_SEC,
        "controls_per_entry": CONTROLS_PER_ENTRY,
        "seed": SEED,
        "fixed_before_any_comparison": True,
    }

"""Checkpoint policy: when a forward verdict may be re-stated, and when it may not.

Optional stopping is the easiest way to manufacture a result from a live experiment: watch the
running total and announce a verdict on the day it looks best. The declared defence is that the
sample sizes at which the study may be re-judged are fixed in advance, and only the last of them
can promote anything.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

#: Declared before any forward observation existed.
CHECKPOINTS = (250, 500, 1000, 2000)
#: Only this one can promote; the rest are diagnostic.
PROMOTION_CHECKPOINT = 2000


def due(h5_rows: int, already_taken: Sequence[int]) -> int | None:
    """The largest declared checkpoint the sample has reached and not yet recorded."""
    reached = [c for c in CHECKPOINTS if h5_rows >= c and c not in set(already_taken)]
    return max(reached) if reached else None


def taken(directory: Path) -> list[int]:
    if not directory.exists():
        return []
    out = []
    for path in directory.glob("checkpoint_*.json"):
        try:
            out.append(int(path.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return sorted(out)


def status(h5_rows: int, directory: Path) -> dict[str, Any]:
    recorded = taken(directory)
    pending = due(h5_rows, recorded)
    upcoming = [c for c in CHECKPOINTS if c > h5_rows]
    return {
        "h5_rows": h5_rows,
        "checkpoints": list(CHECKPOINTS),
        "recorded": recorded,
        "checkpoint_due": pending,
        "next_checkpoint": upcoming[0] if upcoming else None,
        "rows_to_next": (upcoming[0] - h5_rows) if upcoming else 0,
        "promotion_checkpoint": PROMOTION_CHECKPOINT,
        "may_promote_now": pending == PROMOTION_CHECKPOINT,
        "verdict_may_be_restated": pending is not None,
        "note": "daily returns may be recorded at any time; a PASS/FAIL verdict may only be "
                "re-stated at a declared checkpoint, and only the 2000-row checkpoint can promote.",
    }


def write(directory: Path, rows: int, payload: Mapping[str, Any]) -> Path:
    path = directory / f"checkpoint_{rows}.json"
    if path.exists():
        raise FileExistsError(f"checkpoint {rows} is already recorded at {path}")
    directory.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["checkpoint_rows"] = rows
    body["recorded_at"] = datetime.now(timezone.utc).isoformat()
    body["may_promote"] = rows == PROMOTION_CHECKPOINT
    path.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    return path

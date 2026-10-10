"""The V0 wall rows Market Context R0's replay reads, and only those.

R0's T2 frame (`market_context_r0.journal.replay`) rebuilds the resting candidate set from the V0
`wall` stream and applies `lm-wall.v2` to each candidate's **OPENED row** - the notional and the
multiple the candidate had when it opened, which a V0 journal never updates. The Liquidity Map
viewer and the Market Context panel apply the same rule to the candidates' **current** values. The
two selections differ in most seconds (measured on a 25-minute recording: bin sets equal in 394 of
2,996 side-samples), so the `wall_v2` stream, which follows the viewer, cannot reproduce R0.

What R0 needs is far less than the V0 stream, because of one property of open-row values: they
never change. A candidate that fails R1 (notional) or R2 (multiple) on its OPENED row fails them
for its whole life under R0's reading, so R0 can never select it, and it can be left out. This
filter therefore keeps:

* every resting row (R0's own classification: `status == ACTIVE`, or not ENDED/INTERRUPTED with
  an OPENED/UPDATED event) whose values pass R1 and R2, or whose key is already kept - an update of
  a kept candidate replaces its values in R0, so it must replace them here too;
* every terminal row (everything else, which R0 pops) of a kept key, after which the key is
  forgotten.

Each kept row is written with the thirteen payload fields `wallrule.select` reads and the V0
envelope's own `seq`, so R0's merge rule - wall rows with `seq` below the derived record's - can
be applied unchanged. Measured on the same recording: 13% of V0 wall rows, 12 MB/day compressed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ..liquidity_map import wallrule as R

#: Exactly what `wallrule.select` reads from a candidate payload, plus the two fields R0's replay
#: uses to classify a row as resting or terminal.
FIELDS: tuple[str, ...] = ("event", "status", "side", "price", "qty", "notional", "multiple",
                           "first_seen_ms", "persistence_ms", "coverage", "generation",
                           "local_average", "neighbours")

RESTING_STATUS = "ACTIVE"
TERMINAL_STATUSES = ("ENDED", "INTERRUPTED")
RESTING_EVENTS = ("OPENED", "UPDATED")


def is_resting(payload: dict[str, Any]) -> bool:
    """`market_context_r0.journal.replay`'s classification, restated verbatim."""
    status, event = str(payload.get("status") or ""), str(payload.get("event") or "")
    return status == RESTING_STATUS or (status not in TERMINAL_STATUSES
                                        and event in RESTING_EVENTS)


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def passes_open_floors(payload: dict[str, Any]) -> bool:
    """R1 and R2 of `lm-wall.v2` on the row's own values. R3 and R4 move; these two never do."""
    notional, multiple = _decimal(payload.get("notional")), _decimal(payload.get("multiple"))
    return (notional is not None and multiple is not None
            and notional >= R.MIN_NOTIONAL_USDT and multiple >= R.MIN_MULTIPLE)


@dataclass
class OpenRowFilter:
    """Decides, row by row and in envelope order, which V0 wall rows R0 could ever use."""

    kept: set[tuple[str, str]] = field(default_factory=set)
    admitted: int = 0
    refused: int = 0

    def admit(self, payload: dict[str, Any], seq: int) -> dict[str, Any] | None:
        key = (str(payload.get("side") or ""), str(payload.get("price") or ""))
        if is_resting(payload):
            if key not in self.kept and not passes_open_floors(payload):
                self.refused += 1
                return None
            self.kept.add(key)
        else:
            if key not in self.kept:
                self.refused += 1
                return None
            self.kept.discard(key)
        self.admitted += 1
        return {"v0_seq": seq, **{name: payload.get(name) for name in FIELDS}}

    def counters(self) -> dict[str, int]:
        return {"admitted": self.admitted, "refused": self.refused, "kept_open": len(self.kept)}


__all__ = ["OpenRowFilter", "FIELDS", "is_resting", "passes_open_floors"]

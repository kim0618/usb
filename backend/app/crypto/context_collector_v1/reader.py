"""Read a context root back as one record per second, for forward research.

Two wall selections can be rebuilt from the journal, and the reader returns both, labelled,
because they answer different questions and a study has to choose one in its contract:

* `walls_display` - the `lm-wall.v2` bins the Liquidity Map viewer and the Market Context panel
  selected from each candidate's **current** values, replayed from `wall_v2` transitions;
* `walls_r0` - the bins Market Context R0's replay selects from each candidate's **OPENED-row**
  values, recomputed here with `wallrule.select` over the `wall_r0` rows, merged on R0's own rule:
  rows whose V0 `seq` is below the sample's V0 `derived` seq.

Everything else a second carries - mid, best bid and ask, book state and generation, the depth
bands with their coverage, the flow windows with theirs - is read from the `context` record as
written. Nothing here computes a new quantity.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from ..liquidity_map import wallrule as R
from .contract import COMPRESSED_SUFFIX, CONTEXT_KIND, WALL_R0_KIND, WALL_V2_KIND
from .wallr0 import is_resting


def _lines(path: Path) -> list[dict[str, Any]]:
    """Whole records only: a torn trailing line is absent, not data."""
    data = gzip.decompress(path.read_bytes()) if path.name.endswith(COMPRESSED_SUFFIX) \
        else path.read_bytes()
    cut = data.rfind(b"\n")
    return [json.loads(line) for line in data[:cut].split(b"\n") if line] if cut >= 0 else []


def records(root: Path, kind: str, session8: str | None = None) -> list[dict[str, Any]]:
    directory = root / kind
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        # Sealed, compressed, and the file still being written: a live `.open` file is read
        # up to its last complete line, exactly like a sealed one.
        if not (path.name.endswith(".jsonl") or path.name.endswith(COMPRESSED_SUFFIX)
                or path.name.endswith(".jsonl.open")):
            continue
        if session8 is not None and f"-{session8}-" not in path.name:
            continue
        out.extend(_lines(path))
    return out


@dataclass
class Second:
    """One sample, as a forward study reads it."""

    sample_ms: int
    session_id: str
    seq: int
    v0_derived_seq: int | None
    context: dict[str, Any]
    walls_display: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    walls_r0: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def walls_primary(self) -> dict[str, list[dict[str, Any]]]:
        """CONTRACT_CTX_V1_1 section 4: the research primary, OPENED-row values (R0)."""
        return self.walls_r0

    @property
    def walls_secondary_current(self) -> dict[str, dict[str, dict[str, Any]]]:
        """CONTRACT_CTX_V1_1 section 4: SECONDARY_DIAGNOSTIC, current values (the panel)."""
        return self.walls_display


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def seconds(root: Path, session8: str | None = None, *, with_r0: bool = True
            ) -> Iterator[Second]:
    """Every `context` record with a sample, in envelope order, with both wall selections."""
    stream = (records(root, CONTEXT_KIND, session8) + records(root, WALL_V2_KIND, session8)
              + (records(root, WALL_R0_KIND, session8) if with_r0 else []))
    # Sessions in the order they started, records inside a session in envelope order. Sorting
    # on the session id alone would order sessions by a random UUID.
    first_ms: dict[str, int] = {}
    for record in stream:
        sid = record["session_id"]
        first_ms[sid] = min(first_ms.get(sid, record["receive_ms"]), record["receive_ms"])
    stream.sort(key=lambda record: (first_ms[record["session_id"]], record["session_id"],
                                    record["seq"]))
    display: dict[tuple[str, str], dict[str, Any]] = {}
    resting: dict[tuple[str, str], dict[str, Any]] = {}
    pending_r0: list[dict[str, Any]] = []
    session = None
    for record in stream:
        if record["session_id"] != session:
            session = record["session_id"]
            display.clear()
            resting.clear()
            pending_r0.clear()
        payload = record["payload"]
        if record["kind"] == WALL_V2_KIND:
            key = (payload["side"], payload["bin_low"])
            if payload["event"] == "OPEN":
                display[key] = dict(payload)
            elif payload["event"] == "CHANGE" and key in display:
                display[key].update({name: payload[name] for name in payload.get("changed") or []})
            else:
                display.pop(key, None)
            continue
        if record["kind"] == WALL_R0_KIND:
            pending_r0.append(payload)
            continue
        if payload.get("sample_ms") is None:
            continue
        derived_seq = payload.get("v0_derived_seq")
        # R0's merge: only rows the V0 sequence placed before this sample's derived record.
        still_pending = []
        for row in pending_r0:
            if derived_seq is not None and row["v0_seq"] < derived_seq:
                key = (str(row.get("side") or ""), str(row.get("price") or ""))
                if is_resting(row):
                    resting[key] = row
                else:
                    resting.pop(key, None)
            else:
                still_pending.append(row)
        pending_r0 = still_pending
        book = payload.get("book") or {}
        walls_r0: dict[str, list[dict[str, Any]]] = {}
        if with_r0:
            rows = list(resting.values())
            for side in (R.SIDE_BID, R.SIDE_ASK):
                walls_r0[side] = R.select(rows, side=side, mid=_decimal(book.get("mid")),
                                          latest_sample_ms=payload["sample_ms"],
                                          known_low=_decimal(book.get("known_low")),
                                          known_high=_decimal(book.get("known_high"))).walls
        by_side: dict[str, dict[str, dict[str, Any]]] = {R.SIDE_ASK: {}, R.SIDE_BID: {}}
        for (side, low), wall in display.items():
            by_side[side][low] = wall
        yield Second(sample_ms=payload["sample_ms"], session_id=record["session_id"],
                     seq=record["seq"], v0_derived_seq=derived_seq, context=payload,
                     walls_display=by_side, walls_r0=walls_r0)


__all__ = ["Second", "seconds", "records"]

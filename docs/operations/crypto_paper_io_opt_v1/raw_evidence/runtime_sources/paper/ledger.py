"""Append-only ledger and input tape.

The ledger is the authority (contract P7). Aggregates are derived from it; when an aggregate
and the ledger disagree, the aggregate is wrong. Both files are canonical JSONL so that a
replay can be compared byte for byte.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


class EventType:
    RUN_START = "RUN_START"
    MODE_CHANGE = "MODE_CHANGE"
    LEVERAGE_CHANGE = "LEVERAGE_CHANGE"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    FILL = "FILL"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_INCREASE = "POSITION_INCREASE"
    POSITION_REDUCE = "POSITION_REDUCE"
    POSITION_CLOSE = "POSITION_CLOSE"
    FEE = "FEE"
    FUNDING = "FUNDING"
    LIQUIDATION = "LIQUIDATION"
    ACCOUNT_RESET = "ACCOUNT_RESET"


class InputKind:
    MARKET = "MARKET"
    COMMAND = "COMMAND"


def canonical(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


@dataclass
class Ledger:
    """In-memory sequence with an optional durable sink. Sequence numbers start at 1."""
    events: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = None

    def append(self, *, ts_ms: int, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {"seq": len(self.events) + 1, "ts_ms": int(ts_ms), "event_type": event_type}
        event.update(jsonable(payload))
        self.events.append(event)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as stream:
                stream.write(canonical(event))
                stream.flush()
                os.fsync(stream.fileno())
        return event

    def bytes(self) -> bytes:
        return b"".join(canonical(event) for event in self.events)

    def of_type(self, *types: str) -> list[dict[str, Any]]:
        wanted = set(types)
        return [event for event in self.events if event["event_type"] in wanted]


@dataclass(frozen=True)
class TapeSummary:
    count: int
    first_start_ms: int | None
    last_market_ms: int | None


@dataclass
class InputTape:
    """Everything the engine was allowed to see, in the order it saw it.

    Replay determinism rests on this file being complete: the engine never reads a clock, a
    network socket or a random source, so the tape plus the config fully determines the ledger.
    """
    path: Path | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    # A live session appends for as long as the service runs, at 1 Hz. Keeping every record in
    # memory as well as on disk made the process grow without bound: after a day the list was
    # ~200MB and the service could not start inside its memory limit. The file is the tape;
    # the list is only needed by a replay, which builds its own.
    retain: bool = True
    count: int = 0

    def __post_init__(self) -> None:
        if self.records and not self.count:
            self.count = len(self.records)

    def record(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.count += 1
        entry = {"seq": self.count, "kind": kind, "payload": jsonable(payload)}
        if self.retain:
            self.records.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as stream:
                stream.write(canonical(entry))
                stream.flush()
                os.fsync(stream.fileno())
        return entry

    @classmethod
    def read(cls, path: Path) -> "InputTape":
        tape = cls(path=None)
        for record in cls.stream(path):
            tape.records.append(record)
        tape.count = len(tape.records)
        return tape

    @classmethod
    def stream(cls, path: Path) -> Iterator[dict[str, Any]]:
        """One record at a time, so a long tape can be replayed without being held whole."""
        with path.open() as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    @classmethod
    def scan(cls, path: Path) -> "TapeSummary":
        """What a restored session needs to know about the tape, in one streaming pass."""
        count = 0
        first_start_ms: int | None = None
        last_market_ms: int | None = None
        for record in cls.stream(path):
            count += 1
            payload = record.get("payload") or {}
            if record.get("kind") == InputKind.COMMAND:
                if first_start_ms is None and payload.get("command") == "START":
                    first_start_ms = int(payload["ts_ms"])
            elif record.get("kind") == InputKind.MARKET:
                last_market_ms = int(payload["ts_ms"])
        return TapeSummary(count=count, first_start_ms=first_start_ms,
                           last_market_ms=last_market_ms)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.records)

    def __len__(self) -> int:
        return self.count

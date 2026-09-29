"""The LIVE audit ledger: what this process saw and did on a real account.

It is a *mirror*, never a source. Binance is the authority for balance, position, fills and
funding; this file exists so that afterwards there is a local record of what the screen was
showing and what the operator pressed, in order, with timestamps. Nothing in this package ever
reads it back to build a snapshot - the restart path always re-reads Binance.

Kept deliberately apart from the paper ledger:

* a different directory (`data/runtime/crypto/live/` by default, never the paper run's root),
* a different event vocabulary, so a LIVE line can never be folded into a paper analytic by a
  helper that matches on `event_type`,
* a constructor that refuses a path inside a paper run directory. The paper ledger is the input
  to a determinism proof, and one foreign line would break a replay that must stay byte-exact.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("data/runtime/crypto/live")
LEDGER_NAME = "binance_live_events.jsonl"
#: Any path segment that says this is a paper run. Checked against the resolved path.
PAPER_SEGMENTS = ("paper",)


class MirrorPathRefused(RuntimeError):
    """The mirror was pointed at the paper run's storage. Refused before anything is written."""


class LiveEvent:
    #: What the account looked like on one read. Written on a state change, not every poll.
    SNAPSHOT = "LIVE_SNAPSHOT"
    #: An operator asked for an order. Written before anything is sent, so a refusal still
    #: leaves a record that the button was pressed.
    ORDER_INTENT = "LIVE_ORDER_INTENT"
    ORDER_REFUSED = "LIVE_ORDER_REFUSED"
    ORDER_SENT = "LIVE_ORDER_SENT"
    ORDER_RESULT = "LIVE_ORDER_RESULT"
    #: A user data stream frame, recorded by event type only.
    STREAM_EVENT = "LIVE_STREAM_EVENT"
    STREAM_STATE = "LIVE_STREAM_STATE"
    #: A REST re-read after a reconnect or a restart, with what changed.
    RECONCILE = "LIVE_RECONCILE"
    LEVERAGE_INTENT = "LIVE_LEVERAGE_INTENT"
    LEVERAGE_REFUSED = "LIVE_LEVERAGE_REFUSED"
    LEVERAGE_RESULT = "LIVE_LEVERAGE_RESULT"


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def canonical(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


@dataclass
class LiveMirror:
    """Append-only, one JSON object per line, flushed and fsynced per line like the paper
    ledger. `account_fingerprint` identifies which API key the run belongs to without holding
    any part of the key."""
    path: Path
    account_fingerprint: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    #: Kept small: this is an operator record, not a research dataset, and the process is
    #: memory-capped on the server.
    retain: int = 500
    count: int = 0

    def __post_init__(self) -> None:
        resolved = self.path.expanduser().resolve()
        if any(segment in PAPER_SEGMENTS for segment in resolved.parts):
            raise MirrorPathRefused(
                f"the LIVE mirror may not be written inside a paper run directory: {resolved}")
        self.path = resolved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            # Continue the sequence rather than restarting it, so the file reads as one record
            # across restarts. Only the count is needed; the lines are not replayed into
            # anything.
            with self.path.open("rb") as stream:
                self.count = sum(1 for _ in stream)

    def append(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.count += 1
        event = {"seq": self.count, "ts_ms": int(time.time() * 1000), "event_type": event_type,
                 "account": self.account_fingerprint}
        event.update(jsonable(payload))
        self.events.append(event)
        if len(self.events) > self.retain:
            del self.events[:-self.retain]
        with self.path.open("ab") as stream:
            stream.write(canonical(event))
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """The tail, newest first. Read from memory when it is there, from the file otherwise,
        so a restarted process can still show the history it did not write."""
        if self.events:
            return self.events[-limit:][::-1]
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[::-1]

    def view(self) -> dict[str, Any]:
        return {"path": self.path.as_posix(), "events": self.count,
                "account": self.account_fingerprint,
                "role": "AUDIT_MIRROR_ONLY: binance is authoritative, this file is never replayed"}


def default_path(fingerprint: str | None, root: Path | None = None) -> Path:
    """One directory per API key fingerprint, so two keys never interleave in one file."""
    base = DEFAULT_ROOT if root is None else root
    return base / (fingerprint or "no-key") / LEDGER_NAME


__all__ = ["LiveEvent", "LiveMirror", "MirrorPathRefused", "DEFAULT_ROOT", "default_path"]

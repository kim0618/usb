"""Append-only persistence for signals, shadow trades and attributions.

Its own directory, separate from the manual PAPER ledger, the Binance LIVE mirror and anything
AUTO. The three never share a file, so no later reader can total C1's shadow results together
with trades a person actually made.

Append-only with last-write-wins per id, which is the pattern the paper ledger already uses here:
a state change is a new line, the file is never rewritten in place, and a torn tail from a kill
mid-write is dropped on load rather than guessed at. The cursor in `state.json` is the only
mutable file, and it is rewritten atomically.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

from .contract import CONTRACT_SHA256, SCHEMA_VERSION
from .models import Attribution, C1xEvent, ShadowTrade, Signal

DEFAULT_ROOT = Path("data/runtime/crypto/c1")
SIGNALS_FILE = "signals.jsonl"
SHADOW_FILE = "shadow.jsonl"
ATTRIBUTION_FILE = "attributions.jsonl"
C1X_FILE = "c1x.jsonl"
STATE_FILE = "state.json"


def _read_lines(path: Path) -> Iterator[dict[str, Any]]:
    """Every intact JSON line. A final partial line is skipped: a process killed between write
    and fsync leaves one, and it is not a record."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class C1Store:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- paths
    @property
    def signals_path(self) -> Path:
        return self.root / SIGNALS_FILE

    @property
    def shadow_path(self) -> Path:
        return self.root / SHADOW_FILE

    @property
    def c1x_path(self) -> Path:
        return self.root / C1X_FILE

    @property
    def attribution_path(self) -> Path:
        return self.root / ATTRIBUTION_FILE

    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    # ---------------------------------------------------------------- write
    def _append(self, path: Path, rows: Iterable[dict[str, Any]]) -> int:
        payloads = list(rows)
        if not payloads:
            return 0
        with path.open("a", encoding="utf-8") as handle:
            for row in payloads:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return len(payloads)

    def append_signals(self, signals: Iterable[Signal]) -> int:
        return self._append(self.signals_path, (signal.to_json() for signal in signals))

    def append_shadow(self, trades: Iterable[ShadowTrade]) -> int:
        return self._append(self.shadow_path, (trade.to_json() for trade in trades))

    def append_c1x(self, events: Iterable[C1xEvent]) -> int:
        return self._append(self.c1x_path, (event.to_json() for event in events))

    def append_attribution(self, attribution: Attribution) -> int:
        return self._append(self.attribution_path, [attribution.to_json()])

    def write_cursor(self, *, last_decided_at_ms: int | None, armed: bool,
                     extra: dict[str, Any] | None = None) -> None:
        """Replace the cursor atomically: write a sibling, fsync, rename.

        A cursor half-written would be worse than none, because recovery reads it to decide which
        bars it has already judged.
        """
        body = {"schema_version": SCHEMA_VERSION, "contract_sha256": CONTRACT_SHA256,
                "last_decided_at_ms": last_decided_at_ms, "armed": armed}
        body.update(extra or {})
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root,
                                             prefix=".state-", delete=False)
        try:
            json.dump(body, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, self.state_path)

    # ---------------------------------------------------------------- read
    def signals(self) -> dict[str, Signal]:
        """Signals by id, each at its latest written state."""
        out: dict[str, Signal] = {}
        for row in _read_lines(self.signals_path):
            try:
                signal = Signal.from_json(row)
            except (KeyError, TypeError, ValueError):
                continue
            out[signal.signal_id] = signal
        return out

    def shadow(self) -> dict[str, ShadowTrade]:
        out: dict[str, ShadowTrade] = {}
        for row in _read_lines(self.shadow_path):
            try:
                trade = ShadowTrade.from_json(row)
            except (KeyError, TypeError, ValueError):
                continue
            out[trade.signal_id] = trade
        return out

    def c1x(self) -> dict[str, C1xEvent]:
        """Diagnostics by signal id, each at its latest written state."""
        out: dict[str, C1xEvent] = {}
        for row in _read_lines(self.c1x_path):
            try:
                event = C1xEvent.from_json(row)
            except (KeyError, TypeError, ValueError):
                continue
            out[event.signal_id] = event
        return out

    def attributions(self) -> list[Attribution]:
        out: list[Attribution] = []
        for row in _read_lines(self.attribution_path):
            try:
                out.append(Attribution.from_json(row))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def cursor(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"last_decided_at_ms": None, "armed": True}
        try:
            body = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"last_decided_at_ms": None, "armed": True}
        if body.get("contract_sha256") not in (None, CONTRACT_SHA256):
            # A cursor written under a different contract is not this engine's cursor. Start over
            # rather than carry a position in a grid that was judged by other rules.
            return {"last_decided_at_ms": None, "armed": True,
                    "rejected_cursor": "CONTRACT_SHA256_MISMATCH"}
        return body

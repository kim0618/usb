"""H's own append-only files: the immutable launch snapshot and the forward ledger.

Strategy A keeps its paper records in the paper database and Strategy E keeps its in engine session
files; neither can see the other's rows, and H is a third such book rather than a tenant of either
(``app.strategies.books``). H's records are not trade-shaped - a WATCH is an observation with no
position - so they go in files of their own instead of being forced into ``simulation_trades``.

Both files are JSON Lines and are only ever appended to. A decision that changes does not rewrite
its old row: a new row is appended with ``previous_decision`` set, so the decision history of an
issuer is the file read in order. ``append`` refuses a row whose identity already exists, which is
what stops a re-run of the launcher from writing a second launch snapshot for the same issuer.

Nothing here reads or writes anything A or E owns.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[4]
ROOT_ENV = "STRATEGY_H_FORWARD_DIR"
DEFAULT_ROOT = REPO_ROOT / "data/runtime/strategy_h_v2/d7"

LAUNCH_SNAPSHOT = "launch_snapshot.jsonl"
FORWARD_LEDGER = "forward_ledger.jsonl"
PRICES = "prices"
SNAPSHOT_IDENTITY = ("strategy_id", "ticker", "record")
LEDGER_IDENTITY = ("strategy_id", "ticker", "thesis_version", "decision_time")


class AppendOnlyViolation(RuntimeError):
    pass


def root() -> Path:
    return Path(os.environ.get(ROOT_ENV, str(DEFAULT_ROOT)))


def path(name: str) -> Path:
    return root() / name


def price_dir() -> Path:
    return root() / PRICES


def read(name: str) -> list[dict[str, Any]]:
    """Every row of one file in write order; an absent or half-written file reads as what it has."""
    target = path(name)
    out: list[dict[str, Any]] = []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue                      # a torn final line is skipped, never guessed at
        if isinstance(row, dict):
            out.append(row)
    return out


def identity_of(row: Mapping[str, Any], keys: Sequence[str]) -> tuple:
    return tuple(row.get(key) for key in keys)


def identities(name: str, keys: Sequence[str]) -> set[tuple]:
    return {identity_of(row, keys) for row in read(name)}


def append(name: str, rows: Sequence[Mapping[str, Any]], *, keys: Sequence[str]) -> int:
    """Append rows whose identity is not already in the file. Returns how many were written.

    A duplicate identity raises rather than being skipped silently: the caller asked to record
    something the file already states, and the two could differ.
    """
    if not rows:
        return 0
    existing = identities(name, keys)
    fresh: list[Mapping[str, Any]] = []
    for row in rows:
        ident = identity_of(row, keys)
        if ident in existing:
            raise AppendOnlyViolation(f"{name} already carries {dict(zip(keys, ident))}")
        existing.add(ident)
        fresh.append(row)
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) + "\n")
    return len(fresh)


def snapshots() -> list[dict[str, Any]]:
    return read(LAUNCH_SNAPSHOT)


def ledger() -> list[dict[str, Any]]:
    return read(FORWARD_LEDGER)


def append_snapshots(rows: Sequence[Mapping[str, Any]]) -> int:
    return append(LAUNCH_SNAPSHOT, rows, keys=SNAPSHOT_IDENTITY)


def append_ledger(rows: Sequence[Mapping[str, Any]]) -> int:
    return append(FORWARD_LEDGER, rows, keys=LEDGER_IDENTITY)


def launched() -> bool:
    return any(row.get("record") == "LAUNCH" for row in snapshots())


def launch_rows() -> list[dict[str, Any]]:
    return [row for row in snapshots() if row.get("record") == "LAUNCH"]


def history(ticker: str) -> list[dict[str, Any]]:
    """One issuer's ledger rows in write order: its decision history and its thesis versions."""
    return [row for row in ledger() if row.get("ticker") == ticker]


def latest_per_ticker() -> dict[str, dict[str, Any]]:
    """The newest ledger row per issuer. File order is write order, so the last one wins."""
    out: dict[str, dict[str, Any]] = {}
    for row in ledger():
        ticker = row.get("ticker")
        if isinstance(ticker, str):
            out[ticker] = row
    return out


def iter_files() -> Iterator[Path]:
    for name in (LAUNCH_SNAPSHOT, FORWARD_LEDGER):
        target = path(name)
        if target.exists():
            yield target

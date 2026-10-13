"""H's paper book files, beside the forward shadow's and never inside them.

``<forward root>/paper/`` holds three files:

* ``launch.json``: the ``H_PAPER_V1_LAUNCH`` record. Written once; a second launch is refused, so the
  paper clock can never be restarted to hide a result.
* ``events.jsonl``: every paper event (pending entry, fill, waiting slot, cancellation, exit, NAV)
  in processing order. Append-only. The engine recomputes the whole event list from the forward
  ledger and the price store on every run; what is already stored must be reproduced exactly as a
  prefix, or nothing is appended and the run reports an integrity failure.
* ``status.json``: the last run's infrastructure state (stalled at a gap, missing open, problems).

Living under the forward root means the existing ``STRATEGY_H_FORWARD_DIR`` isolation of every test
covers the paper book too. Nothing here reads or writes anything A or E owns.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.strategies.h_forward import store as ST

PAPER_DIR = "paper"
LAUNCH = "launch.json"
EVENTS = "events.jsonl"
STATUS = "status.json"


class PaperIntegrityError(RuntimeError):
    pass


def root() -> Path:
    return ST.root() / PAPER_DIR


def path(name: str) -> Path:
    return root() / name


def launch() -> dict[str, Any] | None:
    try:
        body = json.loads(path(LAUNCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def write_launch(body: Mapping[str, Any]) -> None:
    target = path(LAUNCH)
    if target.exists():
        raise PaperIntegrityError("H paper is already launched; the launch record is written once")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".partial")
    temp.write_text(json.dumps(dict(body), sort_keys=True, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, target)


def events() -> list[dict[str, Any]]:
    target = path(EVENTS)
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
            continue                        # a torn final line is skipped, never guessed at
        if isinstance(row, dict):
            out.append(row)
    return out


def canonical(row: Mapping[str, Any]) -> str:
    return json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=str)


def append_events(rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    target = path(EVENTS)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical(row) + "\n")
    return len(rows)


def write_status(body: Mapping[str, Any]) -> None:
    target = path(STATUS)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".partial")
    temp.write_text(json.dumps(dict(body), sort_keys=True, ensure_ascii=False, indent=1, default=str) + "\n",
                    encoding="utf-8")
    os.replace(temp, target)


def status() -> dict[str, Any] | None:
    try:
        return json.loads(path(STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

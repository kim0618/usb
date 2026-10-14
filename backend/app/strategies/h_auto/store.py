"""H autonomous pipeline files, under the H forward store (``<store>/auto/``).

Everything here is H's own and append-only except the small JSON view ``run_status.json``, which is
replaced atomically. V1's ``spend.jsonl`` and ``authorization.json`` (AI budget) are no longer read or
written; an existing copy is left in place as history. Nothing here touches A, E or the forward
ledger directly: the ledger is written only through ``h_forward.transitions``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST

AUTO = "auto"
QUEUE_EVENTS = "queue_events.jsonl"
THESIS_EVIDENCE = "thesis_evidence.jsonl"
RUN_STATUS = "run_status.json"
NEW_CANDIDATES = "new_candidates.json"


def root() -> Path:
    return ST.root() / AUTO


def path(name: str) -> Path:
    return root() / name


def read_jsonl(name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        text = path(name).read_text(encoding="utf-8")
    except OSError:
        return out
    lines = text.splitlines()
    for n, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            if n == len(lines) - 1:
                continue                  # a torn final line from a crash: skipped, never guessed at
            raise
        if isinstance(row, dict):
            out.append(row)
    return out


def append_jsonl(name: str, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        handle.flush()
    return len(rows)


def read_json(name: str) -> dict[str, Any] | None:
    return SR.read_json(path(name))


def write_json(name: str, body: Mapping[str, Any]) -> str:
    path(name).parent.mkdir(parents=True, exist_ok=True)
    return SR.atomic_write_json(path(name), body)

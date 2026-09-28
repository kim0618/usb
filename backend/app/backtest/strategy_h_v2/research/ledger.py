"""D3 immutable research ledger: one versioned record per candidate, never overwritten. A rerun
that wants to update a candidate's research creates V2, V3, ... - V1 is never edited or deleted
(D3 brief §35)."""

from __future__ import annotations

import re
from pathlib import Path

from app.backtest.strategy_h_v2.research.schema import HResearchInterpretationV1

_VERSION_RE = re.compile(r"^V(\d+)\.json$")


def ledger_dir(root: Path, ticker: str) -> Path:
    return root / "ledger" / ticker


def next_version(root: Path, ticker: str) -> int:
    directory = ledger_dir(root, ticker)
    if not directory.exists():
        return 1
    versions = [
        int(match.group(1))
        for path in directory.glob("V*.json")
        if (match := _VERSION_RE.match(path.name))
    ]
    return max(versions, default=0) + 1


def write_research_output(root: Path, output: HResearchInterpretationV1) -> Path:
    directory = ledger_dir(root, output.ticker)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"V{output.version}.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable ledger entry {path}")
    path.write_text(output.model_dump_json(indent=2))
    return path


def read_research_output(root: Path, ticker: str, version: int) -> HResearchInterpretationV1:
    path = ledger_dir(root, ticker) / f"V{version}.json"
    return HResearchInterpretationV1.model_validate_json(path.read_text())


def latest_version(root: Path, ticker: str) -> HResearchInterpretationV1 | None:
    current = next_version(root, ticker) - 1
    if current < 1:
        return None
    return read_research_output(root, ticker, current)

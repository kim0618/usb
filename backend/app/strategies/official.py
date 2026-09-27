"""The official A/E paper clock (docs/operations/ae_official_paper_v1.json).

Official paper metrics count only ACCOUNTING_V1 rows on or after one start session, shared by A and
E. The start is an operator setting, ``AE_OFFICIAL_PAPER_START`` (ET session date), set once V1 and
E's cost contract are deployed and verified. Until it is set the clock has not started: official
metrics are empty, every existing row is LEGACY or PRE_OFFICIAL, and no gate sample accumulates.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "docs/operations/ae_official_paper_v1.json"
CHECKSUM = REPO_ROOT / "docs/operations/ae_official_paper_v1.sha256"
START_ENV = "AE_OFFICIAL_PAPER_START"
PAPER_EVALUATION_VERSION = "AE_PAPER_V1"
ACCOUNTING_VERSION = "V1"

OFFICIAL, LEGACY, PRE_OFFICIAL = "OFFICIAL", "LEGACY", "PRE_OFFICIAL"


class OfficialContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != CHECKSUM.read_text(encoding="utf-8").strip():
        raise OfficialContractError("AE official paper contract does not match its frozen checksum")
    return body


def start() -> date | None:
    """The first official session, or ``None`` while the clock has not started."""
    text = os.environ.get(START_ENV, "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise OfficialContractError(f"{START_ENV}={text!r} is not an ISO session date") from error


def classify(session: str | None, accounting: str | None) -> str:
    """Where one row belongs. V0 is always legacy; V1 counts only from the start session on."""
    if accounting != ACCOUNTING_VERSION:
        return LEGACY
    begin = start()
    if begin is None or session is None or session < begin.isoformat():
        return PRE_OFFICIAL
    return OFFICIAL


def state() -> dict[str, Any]:
    begin = start()
    return {"paper_evaluation_version": PAPER_EVALUATION_VERSION, "accounting_version": ACCOUNTING_VERSION,
            "official_paper_start": None if begin is None else begin.isoformat(),
            "status": "STARTED" if begin else "NOT_STARTED",
            "gate_contract": contract()["gate"]["contract_id"]}

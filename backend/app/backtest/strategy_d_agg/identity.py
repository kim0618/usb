"""D-AGG run identity: V2-A's recipe, this package's name and code digest.

The recipe is copied rather than imported with a different ``package_dir`` so this module owns
its constants. Same canonical JSON, same refusal of floats, same exclusion of wall clock, host
and paths; a different strategy id and run prefix so a D-AGG run can never be read as a V2-A run.

Import rule: the standard library and ``strategy_d_agg.models``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_d_agg.models import STRATEGY_ID, FreezeIdentity, HardFail

IDENTITY_SCHEMA = "d-agg-run-identity-v1"
PACKAGE_NAME = "app.backtest.strategy_d_agg"
PACKAGE_DIR = Path(__file__).resolve().parent
RUN_PREFIX = {"D1": "dagg1", "D2": "dagg2", "D4": "dagg4"}


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _assert_plain(payload: Any, where: str) -> None:
    if isinstance(payload, bool) or payload is None or isinstance(payload, (int, str)):
        return
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            _assert_plain(value, f"{where}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for i, value in enumerate(payload):
            _assert_plain(value, f"{where}[{i}]")
        return
    raise HardFail("R2", f"identity payload {where} has non-canonical type {type(payload).__name__}")


def code_digest(package_dir: Path = PACKAGE_DIR) -> str:
    """sha256 over ``{relpath}\\t{sha256}\\n`` of the package's ``*.py``, relative path ascending."""
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*.py"), key=lambda p: str(p.relative_to(package_dir))):
        if "__pycache__" in path.parts:
            continue
        body = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{path.relative_to(package_dir).as_posix()}\t{body}\n".encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class RunIdentity:
    phase: str
    payload: dict[str, Any]
    digest: str
    run_id: str

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "identity_digest": self.digest, **self.payload}


def run_identity(*, phase: str, rules_checksum: str, freeze: FreezeIdentity,
                 parent: str | None, extra: Mapping[str, Any] | None = None,
                 package_dir: Path = PACKAGE_DIR) -> RunIdentity:
    if phase not in RUN_PREFIX:
        raise HardFail("R2", f"unknown D-AGG phase {phase!r}")
    payload: dict[str, Any] = {
        "identity_schema": IDENTITY_SCHEMA,
        "strategy_id": STRATEGY_ID,
        "study_class": "SCREENING",
        "phase": phase,
        "rules_checksum": rules_checksum,
        "data": freeze.as_dict(),
        "code": {"package": PACKAGE_NAME, "code_digest": code_digest(package_dir)},
        "parent": parent,
    }
    if extra:
        payload.update(extra)
    _assert_plain(payload, "identity")
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return RunIdentity(phase, payload, digest, f"{RUN_PREFIX[phase]}-{digest[:12]}")

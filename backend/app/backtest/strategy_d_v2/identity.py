"""D-V2A run identity, isolated from V1's and from the common engine identity.

The recipe is V1's, deliberately: the same canonical JSON, the same refusal of floats, the same
exclusion of wall clock, host and paths. What differs is what the payload names - a new strategy
id, this package's code digest, and the V2-A run prefixes - because a V2-A artifact must never
be admissible to a V1 phase or the other way round.

Import rule for this module: the standard library, V1's ``models`` types, and nothing else.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_d_v2.models import STRATEGY_ID, FreezeIdentity, HardFail

IDENTITY_SCHEMA = "d-v2a-run-identity-v1"
PACKAGE_NAME = "app.backtest.strategy_d_v2"
PACKAGE_DIR = Path(__file__).resolve().parent
#: Run id prefixes per phase. Distinct from V1's so a run directory cannot be misread.
RUN_PREFIX = {"D1": "dv2a1", "D2": "dv2a2", "D3": "dv2a3", "D4": "dv2a4"}
#: The serialization contract every V2-A hash string obeys. The query hash is V1's, by
#: declaration: the two studies draw the same sample, which is what makes them comparable.
SERIALIZATION = {"date": "iso-8601-date", "query_hash": "Q|20260917|{D}|{ticker}",
                 "vector": "ten percentile ranks in declaration order"}


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _assert_plain(payload: Any, where: str) -> None:
    """Identity payloads carry ints and strings only; a float would make the digest machine bound."""
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
    raise HardFail("R2", f"identity payload {where} has non-canonical type {type(payload).__name__}"
                         " (floats must be passed as repr strings)")


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
                 parent: str | None = None, extra: Mapping[str, Any] | None = None,
                 package_dir: Path = PACKAGE_DIR) -> RunIdentity:
    """Build the identity of one V2-A run. Wall clock, host, pid and paths stay out by design."""
    if phase not in RUN_PREFIX:
        raise HardFail("R2", f"unknown V2-A phase {phase!r}")
    payload: dict[str, Any] = {
        "identity_schema": IDENTITY_SCHEMA,
        "strategy_id": STRATEGY_ID,
        "study_class": "SCREENING",
        "phase": phase,
        "rules_checksum": rules_checksum,
        "data": freeze.as_dict(),
        "code": {"package": PACKAGE_NAME, "code_digest": code_digest(package_dir)},
        "serialization": dict(SERIALIZATION),
        "parent": parent,
    }
    if extra:
        payload.update(extra)
    _assert_plain(payload, "identity")
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return RunIdentity(phase, payload, digest, f"{RUN_PREFIX[phase]}-{digest[:12]}")

"""D1~D4 run identity, deliberately isolated from ``app.backtest.engine.identity``.

The common ``run_identity`` recipe is reusable, but importing that module also loads
``research.contract -> authority.contract -> app.strategy.lifecycle``, which puts A's
``StrategyV0Engine`` into a D research process (D_REUSE_MATRIX_V1 §5, measured). This identity
goes into every D artifact from D1 on, so the isolation is cheaper now than later. When the
common identity is split into a leaf module, D moves to it and ``identity_schema`` is raised;
existing runs keep the value they were written with (D1 Pre-flight Contract §7.1).

Import rule for this module: the standard library and D ``models`` only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_d_analog.models import FreezeIdentity, HardFail

IDENTITY_SCHEMA = "d-run-identity-v1"
PACKAGE_NAME = "app.backtest.strategy_d_analog"
PACKAGE_DIR = Path(__file__).resolve().parent
#: Run id prefixes per phase (D1 Pre-flight §7.2).
RUN_PREFIX = {"D1": "dpit1", "D2": "dneigh1", "D3": "dsig1", "D4": "deval1"}
#: The serialization contract every D hash string obeys (D1 Pre-flight §5).
SERIALIZATION = {"date": "iso-8601-date", "query_hash": "Q|20260917|{D}|{ticker}",
                 "n1_hash": "N1|20260917|{r}|{D}|{qt}|{d}|{lt}", "replicate_base": 0}


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """The D0 checksum recipe, used for every identity digest as well."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _assert_plain(payload: Any, where: str) -> None:
    """Identity payloads carry ints and strings only; a float would make the digest platform bound."""
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
    """Build the identity of one D run. Wall clock, host, pid and paths stay out of it by design."""
    if phase not in RUN_PREFIX:
        raise HardFail("R2", f"unknown D phase {phase!r}")
    payload: dict[str, Any] = {
        "identity_schema": IDENTITY_SCHEMA,
        "strategy_id": "HISTORICAL_ANALOG_V1",
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


def query_order_key(session: str, ticker: str) -> str:
    """``sha256('Q|20260917|<ISO date>|<ticker>')``; the ticker is the grouped ``T``, unnormalized."""
    return hashlib.sha256(f"Q|20260917|{session}|{ticker}".encode("utf-8")).hexdigest()


def n1_order_key(replicate: int, query_session: str, query_ticker: str,
                 library_session: str, library_ticker: str) -> str:
    """``sha256('N1|20260917|<r>|<D>|<qt>|<d>|<lt>')`` with ``r`` counted from 0, decimal, unpadded."""
    if replicate < 0:
        raise HardFail("F5", f"N1 replicate {replicate} is below the declared base 0")
    body = (f"N1|20260917|{replicate}|{query_session}|{query_ticker}"
            f"|{library_session}|{library_ticker}")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def query_sample(session: str, tickers: Sequence[str], limit: int) -> tuple[str, ...]:
    """The date's sample: eligible tickers by ``order_key`` ascending, ticker as the guard key."""
    ordered = sorted(tickers, key=lambda t: (query_order_key(session, t), t))
    return tuple(ordered[:limit])

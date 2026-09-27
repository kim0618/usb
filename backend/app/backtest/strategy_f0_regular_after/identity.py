"""F0 run identity: the D-AGG recipe (canonical JSON, package code digest), F0 constants.

Copied rather than imported so this package owns its constants. Wall clock, host and paths are
never part of the identity; git HEAD is recorded beside it, not hashed into it.
"""

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

IDENTITY_SCHEMA = "f0-run-identity-v1"
PACKAGE_DIR = Path(__file__).resolve().parent
RUN_PREFIX = "f0"
COMPONENTS = ("rules_checksum", "code_digest", "minute_read_set", "daily_read_set", "sessions")


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def code_digest(package_dir: Path = PACKAGE_DIR) -> str:
    """sha256 over ``{relpath}\\t{sha256}\\n`` of the package's ``*.py``, relative path ascending."""
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*.py"), key=lambda p: str(p.relative_to(package_dir))):
        if "__pycache__" in path.parts:
            continue
        body = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{path.relative_to(package_dir).as_posix()}\t{body}\n".encode())
    return digest.hexdigest()


def run_identity(parts: Mapping[str, Any]) -> tuple[str, str]:
    """(digest, run_id) of the declared components; a missing or extra component is refused."""
    if set(parts) != set(COMPONENTS):
        raise ValueError(f"identity needs exactly {COMPONENTS}, got {sorted(parts)}")
    payload = {"schema": IDENTITY_SCHEMA, **parts}
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return digest, f"{RUN_PREFIX}-{digest[:12]}"

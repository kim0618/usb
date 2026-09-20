"""D2 output: the neighbour sets, the library and query manifests, and the digests that bind them.

What is written is what D3 is allowed to receive - the *identity* of each neighbour and how
similar it was. No forward return, no excess return, no MFE or MAE appears in any table here,
which is what makes the alpha blindness of this phase checkable by reading the schema rather
than by trusting the pipeline (D2 §12.3).

Digests come in two forms for one reason. Small tables use the C ``table_digest`` recipe (CSV at
``%.17g``) so a D artifact can be compared against a C-style artifact by eye and by script; the
neighbour tables are tens of millions of rows and are digested column by column from their raw
bytes instead, which is the same guarantee without building a gigabyte of text.

``COMPLETE.json`` is written last and only after every check has passed. A run that stopped
half-way leaves a directory D3 will refuse to read.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.strategy_d_analog.models import HardFail
from app.backtest.workspace.safe_write import sha256_file, write_bytes_atomic

COMPLETE = "COMPLETE.json"
COMPRESSION = "zstd"
#: Parquet schema-metadata key carrying the four values every D artifact must name (Pre-flight
#: §9.2). A table that reaches D3 without them cannot be checked for freeze mixing.
IDENTITY_KEY = b"d_identity"
IDENTITY_FIELDS = ("freeze_id", "freeze_digest", "grid_digest", "rules_checksum")
#: Column order of the neighbour digest. Fixed here because the digest is the D2 -> D3 handshake.
NEIGHBOR_DIGEST_COLUMNS = ("query_date_idx", "sample_rank", "rank", "library_row",
                           "neighbor_end_idx", "neighbor_ticker_col", "neighbor_figi_code",
                           "metric_value", "rank_score", "label_end_idx")


def table_digest(table: pa.Table) -> str:
    """The C recipe: CSV with ``%.17g`` floats, no index, sha256 of the text. Small tables only."""
    buffer = io.StringIO()
    names = table.column_names
    buffer.write(",".join(names) + "\n")
    columns = [column.to_pylist() for column in table.columns]
    for row in range(table.num_rows):
        cells = []
        for column in columns:
            value = column[row]
            if value is None:
                cells.append("")
            elif isinstance(value, float):
                cells.append(f"{value:.17g}")
            else:
                cells.append(str(value))
        buffer.write(",".join(cells) + "\n")
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def column_digest(columns: Mapping[str, np.ndarray], order: Sequence[str]) -> str:
    """sha256 over each named column's little-endian bytes, in the declared column order."""
    digest = hashlib.sha256()
    for name in order:
        array = columns[name]
        kind = "<f8" if array.dtype.kind == "f" else "<i8"
        digest.update(f"{name}\t{array.shape[0]}\n".encode())
        digest.update(np.ascontiguousarray(array.astype(kind)).tobytes())
    return digest.hexdigest()


def identity_block(freeze, rules_checksum: str) -> dict[str, str]:
    """The four values every D artifact carries, whatever its format (Pre-flight §9.2)."""
    return {"freeze_id": freeze.freeze_id, "freeze_digest": freeze.freeze_digest,
            "grid_digest": freeze.grid_digest, "rules_checksum": rules_checksum}


def _identity_bytes(identity: Mapping[str, str]) -> bytes:
    missing = [name for name in IDENTITY_FIELDS if not identity.get(name)]
    if missing:
        raise HardFail("F1", f"artifact identity is missing {missing}")
    return json.dumps(dict(identity), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def read_identity(path: Path) -> dict[str, Any]:
    """The identity a parquet artifact was written with; D3's freeze-mixing check reads this."""
    metadata = (pq.read_schema(path).metadata or {}).get(IDENTITY_KEY)
    if metadata is None:
        raise HardFail("F1", f"{path.name} carries no {IDENTITY_KEY.decode()} metadata")
    return json.loads(metadata)


def write_table(path: Path, table: pa.Table, identity: Mapping[str, str]) -> dict[str, Any]:
    """Write one parquet file atomically, stamped with the dataset and rules it came from."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = table.replace_schema_metadata(
        {**(table.schema.metadata or {}), IDENTITY_KEY: _identity_bytes(identity)})
    buffer = pa.BufferOutputStream()
    pq.write_table(stamped, buffer, compression=COMPRESSION)
    write_bytes_atomic(path, buffer.getvalue().to_pybytes())
    return {"rows": table.num_rows, "bytes": path.stat().st_size, "file_sha256": sha256_file(path)}


def write_json(path: Path, payload: Mapping[str, Any],
               identity: Mapping[str, str] | None = None) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if identity is not None:
        _identity_bytes(identity)
        payload = {**dict(identity), **dict(payload)}
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_bytes_atomic(path, body)
    return {"bytes": len(body), "file_sha256": hashlib.sha256(body).hexdigest(),
            "content_sha256": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")).hexdigest()}


def write_complete(run_dir: Path, payload: Mapping[str, Any],
                   identity: Mapping[str, str]) -> Path:
    """The admission token for D3. Written after the last check, never before."""
    path = run_dir / COMPLETE
    body = {**dict(identity), **dict(payload),
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _identity_bytes(identity)
    write_bytes_atomic(path, (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path


def load_complete(run_dir: Path) -> dict[str, Any]:
    """Read a finished D2 run. A directory without the token is not a run D3 may use."""
    path = run_dir / COMPLETE
    if not path.exists():
        raise HardFail("R2", f"{run_dir} has no {COMPLETE}: the run did not finish its checks")
    return json.loads(path.read_text(encoding="utf-8"))

"""Deterministic Strategy H0 SEC pilot helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
from typing import Any


def deterministic_ciks(submissions_root: Path, size: int) -> list[str]:
    if not 30 <= size <= 100:
        raise ValueError("pilot size must be in [30, 100]")
    ciks = sorted({p.name.removeprefix("CIK").removesuffix(".json.gz")
                   for p in submissions_root.rglob("CIK*.json.gz")})
    if len(ciks) < size:
        raise ValueError(f"only {len(ciks)} submission documents are available")
    return ciks[:size]


def acceptance_index(document: dict[str, Any]) -> dict[str, datetime]:
    recent = ((document.get("filings") or {}).get("recent") or {})
    output: dict[str, datetime] = {}
    for accession, value in zip(recent.get("accessionNumber") or [],
                                recent.get("acceptanceDateTime") or [], strict=False):
        if not accession or not value:
            continue
        stamp = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            parsed = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        output[str(accession)] = parsed
    return output


def read_gzip_json(path: Path) -> dict[str, Any]:
    return json.loads(gzip.decompress(path.read_bytes()))

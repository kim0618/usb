"""Appending outcomes after 09:35, and only to a decision that was already sealed."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e1_forward.layout import ForwardViolation, require_forward_session

LABEL_FORMAT = "e1-h5-forward-label-v1"
#: Outcome columns. R_5M is the declared primary; the rest are recorded, never gated on here.
LABEL_COLUMNS = ("R_1m", "R_5m", "R_5m_strict", "R_15m", "MFE_5m", "MAE_5m",
                 "MFE_15m", "MAE_15m", "open_570")


def build(session: date, seal: Mapping[str, Any], symbols: Sequence[str],
          labels: Mapping[str, np.ndarray], *, sources: Mapping[str, str],
          created_at: datetime | None = None) -> dict[str, Any]:
    """Attach outcomes to a sealed decision, row for row, refusing any mismatch.

    The symbol set must equal the sealed one exactly. A label file that quietly covered a
    different universe than the one that was decided would make the whole seal decorative.
    """
    require_forward_session(session)
    if seal["session"] != session.isoformat():
        raise ForwardViolation(f"seal is for {seal['session']}, labels are for {session}")
    sealed_symbols = [row["symbol"] for row in seal["rows"]]
    if sorted(sealed_symbols) != sorted(str(s) for s in symbols):
        missing = sorted(set(sealed_symbols) - {str(s) for s in symbols})
        extra = sorted({str(s) for s in symbols} - set(sealed_symbols))
        raise ForwardViolation(
            f"label universe does not match the sealed universe; missing {missing[:5]} "
            f"({len(missing)}), unexpected {extra[:5]} ({len(extra)})")
    missing_columns = [name for name in LABEL_COLUMNS if name not in labels]
    if missing_columns:
        raise ForwardViolation(f"labels are missing declared columns: {missing_columns}")
    position = {str(symbol): i for i, symbol in enumerate(symbols)}
    rows = []
    for sealed in seal["rows"]:
        i = position[sealed["symbol"]]
        rows.append({"symbol": sealed["symbol"], "h5": bool(sealed["h5"]),
                     **{name: float(labels[name][i]) for name in LABEL_COLUMNS}})
    digest = hashlib.sha256()
    digest.update(f"{LABEL_FORMAT}\n".encode())
    for row in sorted(rows, key=lambda r: r["symbol"]):
        cells = [row["symbol"]] + [f"{row[name]:.17g}" for name in LABEL_COLUMNS]
        digest.update(("\t".join(cells) + "\n").encode())
    return {
        "format": LABEL_FORMAT,
        "session": session.isoformat(),
        "decision_digest": seal["decision_digest"],
        "label_digest": digest.hexdigest(),
        "sources": dict(sources),
        "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
        "rows": rows,
    }


def write(path: Path, payload: Mapping[str, Any]) -> Path:
    if path.exists():
        raise ForwardViolation(f"labels already exist at {path}; outcomes are appended once")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    partial.replace(path)
    return path

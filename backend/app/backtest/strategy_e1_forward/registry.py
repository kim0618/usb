"""The append-only forward observation registry.

One line per (symbol, session), written when the outcome is attached, carrying every digest the
declaration asks for so a row can be traced back to the exact bytes and the exact rule that
produced it. Appending is the only supported operation: a session already present is refused
rather than replaced, so an accumulating holdout cannot be quietly rewritten.
"""

from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_e1_forward.layout import ForwardViolation, require_forward_session

FIELDS = ("symbol", "session_date", "daily_source_digest", "reference_source_digest",
          "minute_source_digest", "rules_digest", "feature_digest", "decision_time",
          "provenance", "h5", "R_5M", "available_at", "created_at")


def sessions_present(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["session_date"] for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()}


def read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_session(path: Path, session: date, seal: Mapping[str, Any],
                   labels: Mapping[str, Any], *, available_at: datetime | None = None) -> int:
    """Append one sealed-and-labelled session. Refuses a session the registry already holds."""
    require_forward_session(session)
    if seal["decision_digest"] != labels["decision_digest"]:
        raise ForwardViolation("labels do not carry the digest of the seal they claim to label")
    if session.isoformat() in sessions_present(path):
        raise ForwardViolation(
            f"{session.isoformat()} is already in the registry; the forward record is append-only")
    outcome = {row["symbol"]: row for row in labels["rows"]}
    moment = (available_at or datetime.now(timezone.utc)).isoformat()
    lines = []
    for row in seal["rows"]:
        entry = {
            "symbol": row["symbol"],
            "session_date": session.isoformat(),
            "daily_source_digest": seal["sources"].get("daily", "UNKNOWN"),
            "reference_source_digest": seal["sources"].get("reference", "UNKNOWN"),
            "minute_source_digest": seal["sources"].get("minute", "UNKNOWN"),
            "rules_digest": seal["rules_digest"],
            "feature_digest": seal["decision_digest"],
            "decision_time": "09:25 ET",
            "provenance": seal["provenance"],
            "h5": bool(row["h5"]),
            "R_5M": float(outcome[row["symbol"]]["R_5m"]),
            "available_at": moment,
            "created_at": labels["created_at"],
        }
        lines.append(json.dumps({k: entry[k] for k in FIELDS}, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(lines)


def h5_count(path: Path) -> int:
    return sum(1 for row in read(path) if row.get("h5"))

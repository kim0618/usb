"""Read-only operating summary for the dashboard strategy cards.

Nothing here changes a figure: every value is either a pass-through of a ledger field the
cards endpoint already reads, or a label for one. The dashboard shows ONE state and ONE mode per
strategy; the raw ``operational_status`` / ``runtime_status`` stay on the card for every other
screen. A value the source does not record is ``None`` and the screen prints "-".
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

RUNNING, WAITING, ISSUE = "RUNNING", "WAITING", "ISSUE"
OFFICIAL_PAPER, PAPER, SHADOW, RETIRED = "OFFICIAL PAPER", "PAPER", "SHADOW", "RETIRED"

_RUNNING_OPS = {"RUNNING", "POSITION_OPEN"}
_ISSUE_REASONS = {
    "NO_ACTIVE_SIM_BROKER": "시뮬레이션 러너 없음",
    "NO_DECISION": "결정 없음",
    "ERROR": "러너 오류",
}


def mode_label(card: Mapping[str, Any], *, shadow: bool, official_evidence: bool) -> str:
    """One operating-mode word: OFFICIAL PAPER, PAPER, SHADOW or RETIRED."""
    if card.get("lifecycle") == "RETIRED":
        return RETIRED
    if shadow or card.get("lifecycle") == "FORWARD_SHADOW":
        return SHADOW
    clock = card.get("paper_clock") or {}
    return OFFICIAL_PAPER if official_evidence and clock.get("status") == "STARTED" else PAPER


def normalize_status(card: Mapping[str, Any], *, waiting_signal_is_running: bool) -> tuple[str, str | None]:
    """Fold the card's raw states into RUNNING / WAITING / ISSUE (+ a short reason for ISSUE only).

    ``waiting_signal_is_running``: Strategy A's WAITING_SIGNAL means its runner is alive and
    looking for an entry, which is RUNNING. For E, WAITING_SIGNAL is the engine being between
    sessions or not yet started, which is WAITING.
    """
    op = card.get("operational_status")
    runtime = card.get("runtime_status") or ""
    if runtime in _ISSUE_REASONS:
        return ISSUE, _ISSUE_REASONS[runtime]
    if op == "DATA_ERROR":
        return ISSUE, _ISSUE_REASONS.get(runtime, "데이터 오류")
    if op in _RUNNING_OPS:
        return RUNNING, None
    if op == "WAITING_SIGNAL":
        return (RUNNING if waiting_signal_is_running else WAITING), None
    if op == "PAUSED":
        return WAITING, None
    return ISSUE, "상태 확인 불가"


def operating_day(start: str | None, now: datetime | None = None) -> int | None:
    """Calendar day number since ``start`` (an ISO date or timestamp), the start day being day 1.

    Counted in KST, the zone the dashboard displays. A start in the future is ``None``.
    """
    if not start:
        return None
    begin = _kst_date(start)
    if begin is None:
        return None
    today = (now or datetime.now(timezone.utc)).astimezone(KST).date()
    days = (today - begin).days + 1
    return days if days >= 1 else None


def _kst_date(value: str) -> date | None:
    text = str(value)
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).date()


def latest_mtime(paths: Iterable[Path]) -> str | None:
    """The newest modification time among ``paths`` as a UTC ISO timestamp (checkpoint files)."""
    stamps = []
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).isoformat()


def block(*, mode: str, status: tuple[str, str | None], since: str | None, sessions: int | None,
          session_kind: str, last_run_at: str | None, last_decision: str | None,
          now: datetime | None = None) -> dict[str, Any]:
    """The dashboard block attached to a card; all values display-ready or ``None``."""
    state, reason = status
    return {
        "mode": mode, "status": state, "status_reason": reason if state == ISSUE else None,
        "operating_since": since, "operating_day": operating_day(since, now),
        "session_count": sessions, "session_kind": session_kind,
        "last_run_at": last_run_at, "last_decision": last_decision,
    }

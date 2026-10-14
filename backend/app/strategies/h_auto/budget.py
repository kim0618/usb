"""The research budget, enforced in code before every candidate starts.

Three limits from ``h_autonomous_pipeline_v1`` §budget, all checked before a reservation is taken:

* ``daily_hard_budget_usd`` - per America/New_York date: settled spend plus open reservations;
* ``per_candidate_reservation_usd`` - the contract worst case (D3 3 x $2 + D4 3 x $2), reserved in
  full when a candidate starts and replaced by the actual spend when it ends;
* ``max_research_candidates_per_day`` - reservations taken today, settled or not;

and one operator limit, ``cumulative_cap_usd`` in ``authorization.json``: spend since the
authorization. A missing authorization means no candidate starts at all.

The ledger ``<store>/auto/spend.jsonl`` is append-only. A reservation that was never settled (the
process died mid-call) keeps counting at its full amount, so a crash can never free budget.
A failure is paid for and counted like a success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import store as AS

ET = ZoneInfo("America/New_York")
RESERVE, SETTLE = "RESERVE", "SETTLE"
NOT_AUTHORIZED = "RESEARCH_NOT_AUTHORIZED"
SCOPES = ("REFRESH_ONLY", "REFRESH_AND_NEW_CANDIDATE")


def et_day(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(ET).date().isoformat()


def authorization() -> dict[str, Any] | None:
    body = AS.read_json(AS.AUTHORIZATION)
    if not body or body.get("scope") not in SCOPES:
        return None
    try:
        cap = float(body["cumulative_cap_usd"])
        datetime.fromisoformat(str(body["authorized_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return body | {"cumulative_cap_usd": cap}


def authorize(*, by: str, cumulative_cap_usd: float, scope: str = "REFRESH_ONLY",
              at: str | None = None) -> dict[str, Any]:
    """The operator act that turns research on. Never called by the daily job."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if cumulative_cap_usd <= 0:
        raise ValueError("cumulative cap must be positive")
    body = {"authorized_at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "authorized_by": by, "cumulative_cap_usd": float(cumulative_cap_usd), "scope": scope,
            "contract_id": AC.contract_id(), "contract_sha256": AC.sha256()}
    AS.write_json(AS.AUTHORIZATION, body)
    return body


def ledger() -> list[dict[str, Any]]:
    return AS.read_jsonl(AS.SPEND)


@dataclass(frozen=True)
class Position:
    day: str
    settled_today: float
    open_reserved_today: float
    started_today: int
    spent_since_authorization: float

    @property
    def committed_today(self) -> float:
        return self.settled_today + self.open_reserved_today


def position(day: str, auth: Mapping[str, Any] | None = None) -> Position:
    rows = ledger()
    settled = {r["reservation_id"]: r for r in rows if r.get("kind") == SETTLE}
    reserves = [r for r in rows if r.get("kind") == RESERVE]
    since = str((auth or {}).get("authorized_at") or "9999")

    def committed(r: Mapping[str, Any]) -> float:
        s = settled.get(r["reservation_id"])
        return float(s["actual_usd"]) if s else float(r["amount_usd"])

    today = [r for r in reserves if r.get("day") == day]
    return Position(
        day=day,
        settled_today=sum(committed(r) for r in today if r["reservation_id"] in settled),
        open_reserved_today=sum(float(r["amount_usd"]) for r in today if r["reservation_id"] not in settled),
        started_today=len(today),
        spent_since_authorization=sum(committed(r) for r in reserves if str(r.get("at")) >= since))


def check(*, day: str, reason: str) -> dict[str, Any]:
    """Would a candidate be admitted now? Pure read; ``reserve`` takes the reservation."""
    auth = authorization()
    reserve_amount = AC.reservation()
    if auth is None:
        return {"admitted": False, "why": NOT_AUTHORIZED}
    if reason == "NEW_CANDIDATE" and auth["scope"] != "REFRESH_AND_NEW_CANDIDATE":
        return {"admitted": False, "why": "SCOPE_EXCLUDES_NEW_CANDIDATES"}
    pos = position(day, auth)
    if pos.started_today >= AC.max_per_day():
        return {"admitted": False, "why": "MAX_RESEARCH_CANDIDATES_PER_DAY", "position": pos.__dict__}
    if AC.daily_hard_budget() - pos.committed_today < reserve_amount:
        return {"admitted": False, "why": "DAILY_HARD_BUDGET", "position": pos.__dict__}
    if float(auth["cumulative_cap_usd"]) - pos.spent_since_authorization < reserve_amount:
        return {"admitted": False, "why": "OPERATOR_CUMULATIVE_CAP", "position": pos.__dict__}
    return {"admitted": True, "why": None, "position": pos.__dict__, "reservation_usd": reserve_amount}


def reserve(*, queue_id: str, day: str, run_id: str) -> str:
    reservation_id = f"{run_id}|{queue_id}"
    AS.append_jsonl(AS.SPEND, [{"kind": RESERVE, "reservation_id": reservation_id, "queue_id": queue_id,
                                "day": day, "amount_usd": AC.reservation(),
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])
    return reservation_id


def settle(reservation_id: str, *, actual_usd: float, calls: int, detail: Mapping[str, Any] | None = None) -> None:
    if any(r.get("kind") == SETTLE and r.get("reservation_id") == reservation_id for r in ledger()):
        raise RuntimeError(f"{reservation_id} is already settled")
    AS.append_jsonl(AS.SPEND, [{"kind": SETTLE, "reservation_id": reservation_id,
                                "actual_usd": round(float(actual_usd), 6), "calls": calls,
                                "detail": dict(detail or {}),
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])


def summary(day: str | None = None) -> dict[str, Any]:
    day = day or et_day()
    auth = authorization()
    pos = position(day, auth)
    return {"day": day, "authorized": auth is not None, "scope": (auth or {}).get("scope"),
            "cumulative_cap_usd": (auth or {}).get("cumulative_cap_usd"),
            "spent_since_authorization_usd": round(pos.spent_since_authorization, 4),
            "daily_hard_budget_usd": AC.daily_hard_budget(), "committed_today_usd": round(pos.committed_today, 4),
            "started_today": pos.started_today, "max_per_day": AC.max_per_day(),
            "reservation_usd": AC.reservation()}

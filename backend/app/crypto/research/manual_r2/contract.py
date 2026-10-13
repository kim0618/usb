"""The forward provenance contract: where a trade came from, carried all the way through.

MANUAL-R1 could not tell a developer's smoke order from the operator's own trade because the
ledger records neither. This module defines what would have to be true for that question to be
answerable from the record alone, and implements the rules as checks so they can be tested.

The single rule that matters most is that USER_MANUAL is never a default. An order whose origin
nobody stated is UNKNOWN, and UNKNOWN is counted in no performance figure. That fails toward an
honest gap instead of toward an invented habit.

The vocabulary is imported from the MANUAL-R1 audit rather than restated here: the retroactive
labels and the forward labels have to be the same set, or the audit's findings cannot be written
into the system that replaces it.
"""
from __future__ import annotations

from typing import Any

from ..manual_r1.provenance import (  # noqa: F401  (re-exported as the shared vocabulary)
    AUTO, AUTO_PAPER, ORIGIN_GROUP, ORIGINS, RESEARCH, TEST, UNKNOWN, UNKNOWN_GROUP,
    UNKNOWN_LEGACY, USER, USER_MANUAL,
)

#: Missing origin on a live call. Not USER_MANUAL, and this is the whole point.
DEFAULT_ORIGIN = UNKNOWN

#: Missing origin on an event written before the contract existed.
LEGACY_ORIGIN = UNKNOWN_LEGACY

#: Origins that may never be selected implicitly, only by an explicit caller.
NEVER_DEFAULT = (USER_MANUAL, AUTO_PAPER)

#: What today's `reason` strings attribute a trade to. The production default is the bare string
#: "MANUAL", not "USER_MANUAL", so a check that only looked for the new vocabulary would walk
#: straight past the very line that caused this whole correction.
LEGACY_REASON_ATTRIBUTION = {
    "MANUAL": USER_MANUAL,
    "OPERATOR": USER_MANUAL,
    "AUTO": AUTO_PAPER,
    "EMERGENCY": None,      # an action on an existing position; attributes to nobody new
    "LIQUIDATION": None,
}

#: Every ledger event type that must carry the origin of the order that caused it.
#: FEE is on this list because the MANUAL-R1 correction found 101.13 USDT of test fees sitting
#: inside the operator's result; fees follow the order, not the account.
ORIGIN_BEARING_EVENTS = (
    "ORDER_SUBMITTED", "ORDER_REJECTED", "FILL", "FEE",
    "POSITION_OPEN", "POSITION_INCREASE", "POSITION_REDUCE", "POSITION_CLOSE",
    "FUNDING", "LIQUIDATION",
)

#: Origin groups that a performance view may show. UNKNOWN is deliberately absent.
REPORTABLE_GROUPS = (USER, TEST, RESEARCH, AUTO)


class ProvenanceError(ValueError):
    """An origin that the contract does not allow."""


class OriginRequired(ProvenanceError):
    """A caller that must state its origin did not state one."""


def validate_origin(origin: str) -> str:
    """Accept only the closed vocabulary. Free text would stop this being a partition."""
    if origin not in ORIGINS:
        raise ProvenanceError(f"origin {origin!r} is not in the closed vocabulary")
    return origin


def require_origin(origin: str | None, *, caller: str) -> str:
    """For smoke helpers, CLIs and fixtures: state your origin or do not place the order.

    These are exactly the callers that produced the seventeen mixed trades, so they get no
    default at all -- not even the safe one.
    """
    if origin is None:
        raise OriginRequired(f"{caller} must pass an explicit origin; there is no default here")
    validate_origin(origin)
    if origin == USER_MANUAL:
        raise ProvenanceError(
            f"{caller} may not claim USER_MANUAL; that origin belongs to the UI order path")
    return origin


def resolve_order_origin(origin: str | None) -> str:
    """For the live order path: an unstated origin is UNKNOWN, never USER_MANUAL."""
    if origin is None:
        return DEFAULT_ORIGIN
    return validate_origin(origin)


def resolve_event_origin(event: dict[str, Any]) -> str:
    """For reading a ledger: an event written before the contract is UNKNOWN_LEGACY.

    Distinguishing this from a plain UNKNOWN matters. UNKNOWN means the system asked and got no
    answer; UNKNOWN_LEGACY means the system never asked. Only the first is a fault.
    """
    origin = event.get("origin")
    if origin is None:
        return LEGACY_ORIGIN
    if origin not in ORIGINS:
        # An unrecognised label is not silently coerced to a good one.
        return UNKNOWN
    return origin


def group_of(origin: str) -> str:
    return ORIGIN_GROUP[validate_origin(origin)]


def is_countable_group(origin: str, group: str) -> bool:
    """Whether an origin belongs in `group`'s performance figures."""
    return group_of(origin) == group and group in REPORTABLE_GROUPS


def attribution_of(value: str | None) -> str | None:
    """What a `reason` or `origin` value attributes a trade to, in the new vocabulary."""
    if value is None:
        return None
    if value in ORIGINS:
        return value
    return LEGACY_REASON_ATTRIBUTION.get(value)


def check_no_unsafe_default(signature_default: str | None, *, where: str) -> None:
    """Assert a function signature does not hand out a privileged origin for free.

    This is the check that `terminal/api.py` currently fails: its order model declares
    `reason: str = "MANUAL"`, so a call that names no origin is recorded as the operator's.
    """
    attributed = attribution_of(signature_default)
    if attributed in NEVER_DEFAULT:
        raise ProvenanceError(
            f"{where} defaults to {signature_default!r}, which attributes to {attributed}; "
            f"origins that assign a trade to a person or a strategy must be stated explicitly")


def contract_payload() -> dict[str, Any]:
    """The machine-readable contract, written alongside the prose document."""
    return {
        "record": "MANUAL_R2_PROVENANCE_CONTRACT_V1",
        "status": "DESIGN_ONLY",
        "production_applied": False,
        "vocabulary": {
            "origins": list(ORIGINS),
            "origin_group": dict(ORIGIN_GROUP),
            "closed": True,
            "extension_rule": "a new origin requires a contract version bump; existing origins "
                              "never change meaning",
        },
        "defaults": {
            "unstated_origin_on_live_call": DEFAULT_ORIGIN,
            "missing_origin_on_stored_event": LEGACY_ORIGIN,
            "never_default": list(NEVER_DEFAULT),
            "rule": "USER_MANUAL is assigned only on the UI order path, explicitly",
            "legacy_reason_attribution": dict(LEGACY_REASON_ATTRIBUTION),
        },
        "propagation": {
            "assigned_at": "order intent, before the engine sees it",
            "carried_through": ["OrderRequest", "session command", "tape COMMAND record",
                                "engine.submit_order", "ledger event payload", "analytics Trade"],
            "origin_bearing_events": list(ORIGIN_BEARING_EVENTS),
            "funding_rule": "FUNDING inherits the origin of the position it accrues against",
            "liquidation_rule": "LIQUIDATION inherits the origin of the position it closes; "
                                "being liquidated does not change who opened it",
            "tape_rule": "the COMMAND record carries origin so replay restores it; storing it "
                         "only in the ledger loses it on rebuild",
        },
        "backward_compatibility": {
            "rewrite_history": False,
            "existing_events": LEGACY_ORIGIN,
            "replay_must_be_byte_identical_for_existing_tapes": True,
            "schema_version_bump_required": False,
            "reason": "ledger.append accepts arbitrary payload keys, so adding one is additive",
        },
        "analytics_separation": {
            "manual_view": {"filter": f"origin_group == {USER}"},
            "auto_view": {"filter": f"origin == {AUTO_PAPER}"},
            "test_view": {"filter": f"origin_group == {TEST}", "default_hidden": True},
            "unknown": {"included_in_any_performance_view": False},
            "never_combine": ["manual_view", "auto_view"],
            "equity_curve_rule": "drawdown and equity are computed after the origin filter, "
                                 "not before",
        },
        "test_policy": {
            "preferred": "isolated instance on a separate port and run directory",
            "if_production_unavoidable": "an explicit TEST origin is mandatory",
            "forbidden_while_user_position_open": ["order", "ACCOUNT_RESET", "EMERGENCY"],
            "symmetry_rule": "do not assume an existing trade is a developer test, and do not "
                             "assume it is a user trade either",
        },
        "known_violation": {
            "file": "backend/app/crypto/terminal/api.py",
            "symbol": "order request model",
            "current": "reason: str = \"MANUAL\"",
            "violates": "defaults.never_default",
            "fixed_in_this_step": False,
            "note": "reported for approval; production code was not modified",
        },
    }

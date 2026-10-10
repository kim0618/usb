"""Wall continuity V1.2: whether a wall's observed span may cross a resnapshot.

The rule is frozen in `docs/crypto/liquidity_map_v1/WALL_CONTINUITY_V1_2.md`
(`lm-continuity.v1`), hashed beside it, and was frozen before the carry rates it produces were
looked at. This module implements that document and nothing else.

Why it exists, stated once here because it is the whole design:

A resnapshot increments the book `generation`, and a new generation ends every V0 wall candidate
as UNKNOWN. That is correct for a resync that followed a gap, a reconnect or a stale socket: the
stream was interrupted for an interval that cannot be bounded from the data, and nothing may be
stitched across it. But V1.1 added two *voluntary* resnapshots - the coverage-edge refresh and
the hourly safety refresh - which replace a perfectly healthy book on purpose, to keep the
protected +-0.1% band inside the snapshot's known interval. Those paid the same price: a wall
that never moved had its span reset, and `lm-wall.v2` R4 then refused it for 10 s. With a 300 s
cooldown, a trending market can pay that every five minutes, and no wall can ever show a span
longer than the gap between two refreshes. The screen then reports a book with no durable
structure in it, which is a statement about bookkeeping, not about the market.

So the two cases are told apart, and the split is **not** done here. It is done by the
collector, because the collector is the only process that holds the websocket chain and the
book and can therefore *prove* anything about them. It publishes, per candidate, whether that
exact side and price was present in the newly installed snapshot across a refresh whose five
gates all held. This module does three things with that:

1. reads the proof, and refuses anything that is not positively marked as carried,
2. turns a carried proof into the span R4 is measured against, which can only ever be longer
   than the candidate's own span and can therefore only keep a wall that R4 would have dropped,
3. decides wall-level identity at the V2 bin, where it is **stricter** than the bin: a wall
   carries only if one of its own members is a carried member. Matching on `(side, bin)` alone
   would carry the identity of an order that was never seen resting, because a bin whose old
   member vanished and whose new member sits at a different price inside the same 5 USDT still
   matches on side and bin. Requiring a carried member makes "same side, same frozen V2 bin"
   true by construction instead of assumed, since a carried member has the same exact price.

What this module does not do: it cannot create a wall, remove a wall, shorten a span, or move
any threshold. `lm-wall.v2` R1, R2, R3 and R5 take no input from it, and R4's floor of 10,000 ms
is untouched. `order_identity_proven` stays false on every wall, carried or not.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..market_structure_v0.walls import (CARRY_CARRIED, CARRY_NEW, CARRY_PROOF, HARD, SOFT,
                                         SOFT_GATES, SOFT_WINDOW_MAX_MS, WINDOW_EXCEEDED,
                                         WINDOW_EXEMPT, WINDOW_NOT_MEASURED, WINDOW_WITHIN_MAX)

#: Version of the continuity rule. Independent of `lm-wall.v2` and of `btc-ms.v0.1`, neither of
#: which this changes. v2 narrowed the SOFT window to 300 ms and stated that a journal
#: reconstruction carries nothing; v3 replaced S3 NEWER with S3 CHAIN, because a voluntary
#: refresh is now staged on a second book and swapped in at an identical update id instead of
#: replacing the live one; v4 exempted that staged case from the 300 ms ceiling, because a
#: window in which every frame was replayed individually bounds nothing; v5 gave every snapshot
#: request an owner, so a response that arrives after its attempt ended can no longer be
#: installed by the recovery path, and separated the two voluntary triggers' floors. The frozen
#: document's changelog records all of it.
RULE_VERSION = "lm-continuity.v5"
RULE_RELATIVE_PATH = "docs/crypto/liquidity_map_v1/WALL_CONTINUITY_V1_2.md"

#: Which span R4 was measured against. Published on every wall, so a carried span can never
#: appear on a screen without the screen also saying that it is carried.
SOURCE_OWN = "OWN"
SOURCE_CARRIED = "CARRIED"

#: Why a wall is not carried. Having no earlier observation to carry is the ordinary case and
#: not a failure; a collector that publishes no ledger at all is a different situation and has to
#: be distinguishable from it, because the screen looks identical.
NOT_CARRIED_NEW = "NO_EARLIER_OBSERVATION"
NOT_CARRIED_NO_LEDGER = "COLLECTOR_PUBLISHES_NO_LEDGER"


def repo_root() -> Path:
    """`backend/app/crypto/liquidity_map/continuity.py` -> repository root."""
    return Path(__file__).resolve().parents[4]


def rule_path() -> Path:
    return repo_root() / RULE_RELATIVE_PATH


def rule_identity(path: Path | None = None) -> dict[str, Any]:
    """The rule's version, its hash as computed now, and whether the sidecar still agrees.

    A missing document is reported rather than raised, for the same reason the V0 contract and
    `lm-wall.v2` do it: the viewer must be runnable from a checkout that ships code without docs,
    and it then says so out loud instead of implying a frozen rule it cannot see.
    """
    target = path or rule_path()
    recorded_path = target.with_suffix(".sha256")
    identity: dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "rule_relative_path": RULE_RELATIVE_PATH,
        "rule_sha256": None,
        "recorded_sha256": None,
        "sha256_agrees": None,
        "status": "MISSING",
    }
    if not target.exists():
        return identity
    identity["rule_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    identity["status"] = "PRESENT"
    if recorded_path.exists():
        recorded = recorded_path.read_text(encoding="utf-8").split()
        identity["recorded_sha256"] = recorded[0] if recorded else None
        identity["sha256_agrees"] = identity["recorded_sha256"] == identity["rule_sha256"]
    return identity


def rule_view() -> dict[str, Any]:
    """The rule as the screen states it. Frozen behaviour, not settings."""
    return {
        "rule_version": RULE_VERSION,
        "identity": rule_identity(),
        "is_frozen_not_tunable": True,
        "hard_carries_nothing": True,
        "soft_requires_all_gates": list(SOFT_GATES),
        "soft_window_max_ms": SOFT_WINDOW_MAX_MS,
        # v4: the one case in which that ceiling does not apply, and the conditions under which
        # it does not. Stated on the screen's own rule panel so the exemption is visible before
        # anybody goes looking for the transition that used it.
        "soft_window_exemption": WINDOW_EXEMPT,
        "soft_window_exemption_requires": ["CHAIN_PRESERVED", "VOLUNTARY", "CONTINUITY",
                                           "CHAIN", "WINDOW_MEASURED"],
        "wall_identity_rule": "AT_LEAST_ONE_CARRIED_MEMBER_IN_THE_SAME_V2_BIN",
        "member_identity_rule": "SAME_SIDE_AND_EXACT_PRICE",
        "install": "STAGED_BUFFER_AND_REPLAY_ATOMIC_SWAP",
        "chain_gate": "SWAP_ONLY_AT_AN_IDENTICAL_UPDATE_ID",
        "affects": "WALL_RULE_V2_R4_INPUT_ONLY",
        "changes_v2_thresholds": False,
        "changes_data_contract": False,
        "can_only_extend_a_span": True,
        "proof_published_by_collector": CARRY_PROOF,
    }


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def member_status(payload: dict[str, Any]) -> str:
    """One candidate's continuity status, as the collector published it.

    Anything this reader does not positively recognize as carried is `NEW`. A state file from a
    collector that publishes no ledger at all therefore behaves exactly like V1.1 did, rather
    than inheriting a carry it cannot see.
    """
    return CARRY_CARRIED if str(payload.get("continuity_status") or "") == CARRY_CARRIED \
        else CARRY_NEW


def carried_span_ms(payload: dict[str, Any], latest_sample_ms: int | None) -> int | None:
    """The span since the proven origin, or None when there is nothing proven to measure from.

    Measured against the newest sample the viewer can see, the same way `lm-wall.v2` measures the
    candidate's own span, so the two are comparable. The collector's own live figure is preferred
    when it is larger, which is the case in the compact checkpoint where it is computed from the
    monotonic clock rather than from receipt times.
    """
    if member_status(payload) != CARRY_CARRIED:
        return None
    origin = _integer(payload.get("continuity_first_seen_ms"))
    published = _integer(payload.get("continuity_persistence_ms"))
    spans = [span for span in (
        (max(0, latest_sample_ms - origin)
         if origin is not None and isinstance(latest_sample_ms, int) else None),
        published,
    ) if span is not None]
    return max(spans) if spans else None


def wall_continuity(members: list[Any], latest_sample_ms: int | None) -> dict[str, Any]:
    """The bin-level verdict for one V2 wall, from its members.

    `members` are `wallrule.Member` instances, which carry both spans already. A wall is carried
    if and only if at least one of them is, and its origin is the earliest among those that are:
    the bin has been continuously occupied at least since then, which is the weakest claim the
    evidence supports and therefore the right one.

    A set rebuilt from the journal's `wall` transitions reaches here with no `continuity_*` field
    on any member, because the ledger never travels in a `wall` record. Every such wall is `NEW`
    with the reason `COLLECTOR_PUBLISHES_NO_LEDGER`, which is the frozen rule's decision and not
    a gap: the fallback path is taken precisely when the collector's own account is missing or
    untrusted, and inferring an identity on top of that is the stitching this rule refuses.
    """
    carried = [member for member in members if member_status(member.payload) == CARRY_CARRIED]
    if not carried:
        has_ledger = any("continuity_status" in member.payload for member in members)
        return {
            "continuity_status": CARRY_NEW,
            "not_carried_reason": (NOT_CARRIED_NEW if has_ledger else NOT_CARRIED_NO_LEDGER),
            "continuity_first_seen_ms": None,
            "carried_persistence_ms": None,
            "carried_members": 0,
            "continuity_refreshes": None,
            "continuity_proof": None,
        }
    origins = [origin for origin in
               (_integer(member.payload.get("continuity_first_seen_ms")) for member in carried)
               if origin is not None]
    spans = [span for span in (member.carried_span_ms for member in carried) if span is not None]
    refreshes = [count for count in
                 (_integer(member.payload.get("continuity_refreshes")) for member in carried)
                 if count is not None]
    return {
        "continuity_status": CARRY_CARRIED,
        "not_carried_reason": None,
        "continuity_first_seen_ms": min(origins) if origins else None,
        "carried_persistence_ms": max(spans) if spans else None,
        "carried_members": len(carried),
        "continuity_refreshes": max(refreshes) if refreshes else None,
        "continuity_proof": CARRY_PROOF,
    }


def ledger_view(continuity: dict[str, Any] | None) -> dict[str, Any]:
    """The collector's ledger as the screen reports it, or the reason it is unavailable.

    Reported rather than restated, for the same reason the resnapshot policy is: a viewer that
    printed its own idea of the last transition would keep printing it after the collector's
    changed. The one thing added here is this viewer's own rule identity, because the carry on
    screen is the product of both and a drifted rule has to be visible.
    """
    view: dict[str, Any] = {"available": False, "unavailable_reason": NOT_CARRIED_NO_LEDGER,
                            "rule": rule_view()}
    if not continuity:
        return view
    last = continuity.get("last_transition") or None
    view.update({
        "available": True,
        "unavailable_reason": None,
        "proof": continuity.get("proof"),
        "window_max_ms": continuity.get("window_max_ms"),
        "gates_required": continuity.get("gates_required"),
        "active_carried": continuity.get("active_carried"),
        "soft_refreshes": continuity.get("soft_refreshes"),
        "soft_window_exempt": continuity.get("soft_window_exempt"),
        "hard_transitions": continuity.get("hard_transitions"),
        "wall_carried_total": continuity.get("wall_carried_total"),
        "wall_ended_total": continuity.get("wall_ended_total"),
        "wall_unknown_total": continuity.get("wall_unknown_total"),
        "proofs_superseded": continuity.get("proofs_superseded"),
        "pending_proof": continuity.get("pending_proof"),
        "last_transition": None if not isinstance(last, dict) else {
            "refresh_type": last.get("refresh_type"),
            "continuity_reason": last.get("continuity_reason"),
            "cause": last.get("cause"),
            "generation_from": last.get("generation_from"),
            "generation_to": last.get("generation_to"),
            "receive_ms": last.get("receive_ms"),
            "candidates_before": last.get("candidates_before"),
            "carried_entering": last.get("carried_entering"),
            "carried_lost": last.get("carried_lost"),
            "eligible": last.get("eligible"),
            "wall_carried": last.get("wall_carried"),
            "wall_ended": last.get("wall_ended"),
            "wall_unknown": last.get("wall_unknown"),
            "carried_truncated": last.get("carried_truncated"),
            "overlap_check": (last.get("proof") or {}).get("overlap_check"),
            "gates": (last.get("proof") or {}).get("gates"),
            "window_ms": (last.get("proof") or {}).get("window_ms"),
            "refresh_trigger": (last.get("proof") or {}).get("reason"),
            # V1.3: how the new generation's book was obtained. A replayed chain means every
            # event between the snapshot and the swap was applied rather than absorbed.
            "basis": (last.get("proof") or {}).get("basis"),
            "replayed_frames": (last.get("proof") or {}).get("replayed_frames"),
            "chain_preserved": (last.get("proof") or {}).get("chain_preserved"),
            # V1.4: which of the three cases satisfied S4. A carry that used the exemption is
            # never allowed to look like one that was inside the ceiling.
            "window_verdict": (last.get("proof") or {}).get("window_verdict"),
            "window_exempt": (last.get("proof") or {}).get("window_exempt"),
        },
    })
    return view


__all__ = ["RULE_VERSION", "RULE_RELATIVE_PATH", "SOURCE_OWN", "SOURCE_CARRIED",
           "NOT_CARRIED_NEW", "NOT_CARRIED_NO_LEDGER", "HARD", "SOFT",
           "CARRY_CARRIED", "CARRY_NEW", "CARRY_PROOF", "repo_root", "rule_path",
           "rule_identity", "rule_view", "member_status", "carried_span_ms", "wall_continuity",
           "ledger_view", "WINDOW_WITHIN_MAX", "WINDOW_EXEMPT", "WINDOW_EXCEEDED",
           "WINDOW_NOT_MEASURED"]

"""The human decision record: why the operator took a trade, captured before the trade.

MANUAL-R1's H2 asked what information separates the operator's LONG from their SHORT. The ledger
cannot answer it, and neither can a larger ledger: entry price and time are the result of a
decision, not its reason. Only a label recorded at decision time can close that question.

Two properties make such a label worth anything. It is written before the order, so it cannot be
assembled from the outcome; and GUT is an allowed answer, so an operator with no articulable
reason is not pushed into picking a plausible-sounding one and poisoning the whole set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- reason codes. Closed, and deliberately including an honest "I cannot say". ---
TREND_CONTINUATION = "TREND_CONTINUATION"
REVERSAL_EXHAUSTION = "REVERSAL_EXHAUSTION"
RANGE_EDGE = "RANGE_EDGE"
BREAKOUT = "BREAKOUT"
PULLBACK_ENTRY = "PULLBACK_ENTRY"
LIQUIDATION_CASCADE = "LIQUIDATION_CASCADE"
FUNDING_SKEW = "FUNDING_SKEW"
NEWS_EVENT = "NEWS_EVENT"
GUT = "GUT"
OTHER = "OTHER"

REASON_CODES = (TREND_CONTINUATION, REVERSAL_EXHAUSTION, RANGE_EDGE, BREAKOUT, PULLBACK_ENTRY,
                LIQUIDATION_CASCADE, FUNDING_SKEW, NEWS_EVENT, GUT, OTHER)

#: How a trade relates to its label, for the research filter.
PRESENT = "PRESENT"
MISSING = "MISSING"                 # no label was recorded; the trade still counts as a trade
RETROSPECTIVE = "RETROSPECTIVE"     # recorded after the order; excluded from label analysis

CONVICTION_MIN, CONVICTION_MAX = 1, 5

REQUIRED = ("decision_id", "decided_at_ms", "server_received_ms", "side", "reason_code",
            "conviction")
OPTIONAL = ("expected_direction_bp", "expected_horizon_minutes", "invalidation_price",
            "free_note")
AUTO_ATTACHED = ("mark_at_decision", "spread_at_decision", "basis_at_decision",
                 "tape_snapshot_ts_ms", "ui_context", "origin")

SIDES = ("LONG", "SHORT")


class DecisionError(ValueError):
    """A decision record the contract does not accept."""


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    decided_at_ms: int
    server_received_ms: int
    side: str
    reason_code: str
    conviction: int
    request_id: str | None = None
    expected_direction_bp: float | None = None
    expected_horizon_minutes: float | None = None
    invalidation_price: float | None = None
    free_note: str | None = None
    mark_at_decision: float | None = None
    tape_snapshot_ts_ms: int | None = None
    revision_of: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def latency_ms(self) -> int:
        return self.server_received_ms - self.decided_at_ms

    @property
    def is_revision(self) -> bool:
        return self.revision_of is not None

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "extra"}
        out.update(self.extra)
        out["latency_ms"] = self.latency_ms
        return out


def validate(record: DecisionRecord) -> DecisionRecord:
    """Reject anything that would make the label unusable as evidence."""
    if record.side not in SIDES:
        raise DecisionError(f"side {record.side!r} must be one of {SIDES}")
    if record.reason_code not in REASON_CODES:
        raise DecisionError(f"reason_code {record.reason_code!r} is not in the closed vocabulary")
    if not isinstance(record.conviction, int) or \
            not CONVICTION_MIN <= record.conviction <= CONVICTION_MAX:
        raise DecisionError(
            f"conviction must be an integer in [{CONVICTION_MIN}, {CONVICTION_MAX}]")
    if record.reason_code == OTHER and not (record.free_note or "").strip():
        raise DecisionError("reason_code OTHER requires a free_note; otherwise it is unreadable")
    if record.server_received_ms < record.decided_at_ms:
        raise DecisionError("server_received_ms precedes decided_at_ms: the decision cannot have "
                            "arrived before it was made")
    if record.tape_snapshot_ts_ms is not None and \
            record.tape_snapshot_ts_ms > record.decided_at_ms:
        raise DecisionError("attached market context is newer than the decision; a PIT violation")
    return record


def label_for(record: DecisionRecord | None, *, order_ts_ms: int | None = None) -> str:
    """Which research bucket a trade's label falls in.

    A record written after its own order is not a decision, it is a recollection, and the two
    cannot be told apart once they are in the same column.
    """
    if record is None:
        return MISSING
    if order_ts_ms is not None and record.decided_at_ms > order_ts_ms:
        return RETROSPECTIVE
    if record.is_revision:
        return RETROSPECTIVE
    return PRESENT


def usable_for_label_analysis(label: str) -> bool:
    return label == PRESENT


def schema_payload() -> dict[str, Any]:
    """The machine-readable schema, written alongside the prose document."""
    return {
        "record": "MANUAL_R2_HUMAN_DECISION_SCHEMA_V1",
        "status": "DESIGN_ONLY",
        "ui_implemented": False,
        "collection_started": False,
        "storage": {
            "file": "decisions.jsonl",
            "location": "the run directory, beside the ledger but not inside it",
            "format": "append-only JSONL",
            "linked_to_order_by": "decision_id <-> request_id",
            "survives_account_reset": True,
            "why_not_in_ledger": "the ledger is replayed byte-for-byte for accounting integrity; "
                                 "decision records allow revisions and will change shape more "
                                 "often, and mixing them would let a label change break replay",
        },
        "fields": {
            "required": list(REQUIRED),
            "optional": list(OPTIONAL),
            "auto_attached": list(AUTO_ATTACHED),
        },
        "reason_codes": {
            "values": list(REASON_CODES),
            "closed": True,
            "no_default_selection": True,
            "gut_is_valid": True,
            "gut_rationale": "removing the honest option makes an operator pick a plausible one, "
                             "which corrupts every label rather than just that one",
            "other_requires_free_note": True,
        },
        "conviction": {"type": "int", "min": CONVICTION_MIN, "max": CONVICTION_MAX},
        "point_in_time": {
            "recorded_before_order": True,
            "post_order_entry_allowed": False,
            "edits": "append-only revision records; research uses the first record only",
            "attached_context_rule": "market context is read from a snapshot at or before "
                                     "decided_at_ms",
        },
        "missing_data": {
            "labels": [PRESENT, MISSING, RETROSPECTIVE],
            "usable_for_label_analysis": [PRESENT],
            "imputation_allowed": False,
            "imputation_rationale": "inferring a reason_code from price action records the "
                                    "analyst's hypothesis, not the operator's reason",
            "missing_trades_still_count_in_performance": True,
        },
        "sample_size": {
            "decided_now": False,
            "rule": "MANUAL-R2 collects only; the pass/fail criteria are preregistered before "
                    "collection starts, in MANUAL-R3",
            "forbidden": "deriving a rule from interim results while collection is running",
        },
        "implementation_options": [
            {"option": "A", "form": "label input inside the order modal",
             "coverage": "complete", "pit": "strong", "cost": "UI work, friction at order time"},
            {"option": "B", "form": "separate note screen",
             "coverage": "partial", "pit": "weak", "cost": "low"},
            {"option": "C", "form": "no capture; accumulate trades only",
             "coverage": "none", "pit": "n/a",
             "cost": "H2 stays unanswerable; sample size grows without resolving direction"},
        ],
        "option_choice": "OPERATOR_DECISION_PENDING",
    }

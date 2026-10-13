"""Tests for the provenance correction (MANUAL-R1.1) and the forward contracts (MANUAL-R2).

The point of every test here is the same: an uncertain trade must not quietly pick a side. The
audit tests pin which trades are countable, the accounting tests pin that the groups partition
the ledger without overlapping, and the contract tests pin that no code path hands out
USER_MANUAL to a caller that did not ask for it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.crypto.research.manual_r1 import ledger as L
from app.crypto.research.manual_r1 import provenance as P
from app.crypto.research.manual_r1 import subset as S
from app.crypto.research.manual_r2 import contract as C
from app.crypto.research.manual_r2 import decision as D
from app.crypto.research.manual_r2 import runner as R2

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = REPO_ROOT / "data/research/crypto/manual_r1/snapshot"

pytestmark = pytest.mark.skipif(not (SNAPSHOT / "ledger.jsonl").exists(),
                                reason="MANUAL-R1 snapshot not present")


@pytest.fixture(scope="module")
def book():
    return L.reconstruct(L.load_events(SNAPSHOT / "ledger.jsonl"))


@pytest.fixture(scope="module")
def result():
    return S.run(write=False)


# --- the provenance audit ---------------------------------------------------------------

def test_every_completed_trade_is_classified(book):
    assert sorted(P.AUDIT) == [t.index for t in book.trades]


def test_origins_and_confidences_are_from_the_closed_vocabulary():
    for row in list(P.AUDIT.values()) + [P.OPEN_POSITION]:
        assert row.origin in P.ORIGINS
        assert row.confidence in (P.CONFIRMED, P.HIGH, P.MEDIUM, P.CONFIDENCE_UNKNOWN)
        assert row.detail_confidence in (P.CONFIRMED, P.HIGH, P.MEDIUM,
                                        P.CONFIDENCE_UNKNOWN, P.DETAIL_UNSPECIFIED)
        assert row.origin in P.ORIGIN_GROUP


def test_only_confirmed_and_high_confidence_trades_are_countable():
    for row in P.AUDIT.values():
        countable = row.include_in_manual_performance or row.include_in_test_performance
        assert countable == (row.confidence in P.COUNTABLE), row.trade_index


def test_medium_confidence_never_enters_a_performance_figure():
    for row in P.AUDIT.values():
        if row.confidence in (P.MEDIUM, P.CONFIDENCE_UNKNOWN):
            assert not row.include_in_manual_performance
            assert not row.include_in_test_performance


def test_the_only_countable_user_trade_is_seventeen():
    assert P.summary()["countable_user"] == [17]


def test_every_trade_before_seventeen_is_development():
    # The operator's Q3 declaration covers all sixteen at once, so the TEST group is no longer
    # just the three that happened to be documented.
    assert P.summary()["countable_test"] == list(range(1, 17))


def test_nothing_is_counted_nowhere_any_more():
    assert P.summary()["counted_nowhere"] == []
    assert P.summary()["unknown_attribution"] == []


def test_the_liquidation_is_development_on_the_declaration_alone(book):
    # R1.1 left trades 13 and 14 UNKNOWN because no document explains them, and that is still
    # true: what moved them is the operator's word, and the row has to say so rather than imply
    # a document was found. Getting this wrong in the other direction is what R1.1 corrected.
    for index in (13, 14):
        row = P.AUDIT[index]
        assert row.origin == P.TEST_UNSPECIFIED
        assert row.evidence_reference == P.DECLARATION
        assert row.include_in_test_performance
        assert not row.include_in_manual_performance
    assert next(t for t in book.trades if t.index == 13).liquidated


def test_every_attribution_rests_on_the_declaration():
    for row in list(P.AUDIT.values()) + [P.OPEN_POSITION]:
        assert row.evidence_reference == P.DECLARATION, row.trade_index
        assert row.evidence_tier == P.TIER_1, row.trade_index


def test_an_unspecified_test_makes_no_subtype_claim():
    for row in P.AUDIT.values():
        if row.origin == P.TEST_UNSPECIFIED:
            assert not row.claims_a_test_subtype
            assert row.detail_confidence == P.DETAIL_UNSPECIFIED, row.trade_index
            assert row.detail_reference is None, row.trade_index


def test_a_named_subtype_cites_its_own_evidence():
    named = [r for r in P.AUDIT.values() if r.claims_a_test_subtype]
    assert [r.trade_index for r in named] == [6, 15, 16]
    for row in named:
        assert row.detail_reference, row.trade_index
        assert row.detail_evidence_tier in (P.TIER_1, P.TIER_2), row.trade_index
        assert row.detail_confidence in (P.CONFIRMED, P.HIGH), row.trade_index


def test_subtype_confidence_cannot_move_a_trade_between_groups():
    # detail_confidence describes how precisely a test is named. Whose money it was does not
    # depend on that, and a row whose attribution is weak must stay out however well its
    # subtype is documented.
    weak = P.Classification(99, P.SIZING_TEST, P.MEDIUM, P.TIER_3, "a hunch", "weak attribution",
                            P.CONFIRMED, P.TIER_1, "a document naming the subtype")
    assert not weak.include_in_test_performance
    assert not weak.include_in_manual_performance


def test_every_classification_names_its_evidence():
    for row in list(P.AUDIT.values()) + [P.OPEN_POSITION]:
        assert row.evidence_note.strip()
        if row.confidence in P.COUNTABLE:
            assert row.evidence_reference, row.trade_index
            assert row.evidence_tier in (P.TIER_1, P.TIER_2), row.trade_index


def test_confirmed_classifications_are_tier_one():
    for row in list(P.AUDIT.values()) + [P.OPEN_POSITION]:
        if row.confidence == P.CONFIRMED:
            assert row.evidence_tier == P.TIER_1, row.trade_index


def test_open_position_is_the_operators():
    assert P.OPEN_POSITION.origin == P.USER_MANUAL
    assert P.OPEN_POSITION.confidence == P.CONFIRMED
    assert P.OPEN_POSITION.include_in_manual_performance


def test_unclassified_index_falls_back_to_unknown():
    row = P.classify(999)
    assert row.origin == P.UNKNOWN
    assert not row.include_in_manual_performance
    assert not row.include_in_test_performance


# --- accounting after the split ---------------------------------------------------------

def test_groups_partition_the_ledger_without_overlap(result, book):
    user = set(result["groups"]["USER"]["trade_indices"])
    test = set(result["groups"]["TEST"]["trade_indices"])
    nowhere = set(result["counted_nowhere"]["trade_indices"])
    assert not (user & test) and not (user & nowhere) and not (test & nowhere)
    assert user | test | nowhere == {t.index for t in book.trades}


def test_group_nets_sum_to_the_ledger_total(result, book):
    total = (result["groups"]["USER"]["net_pnl"] + result["groups"]["TEST"]["net_pnl"]
             + result["counted_nowhere"]["net_pnl"])
    assert total == pytest.approx(sum(t.net_pnl for t in book.trades), abs=1e-6)


def test_user_group_net_is_the_single_confirmed_trade(result, book):
    trade = next(t for t in book.trades if t.index == 17)
    assert result["groups"]["USER"]["net_pnl"] == pytest.approx(trade.net_pnl, abs=1e-6)
    assert result["groups"]["USER"]["net_pnl"] == pytest.approx(330.75, abs=0.01)


def test_test_group_carries_its_own_costs(result):
    # Every cost of developing on the production account, now that all sixteen are attributed.
    # MANUAL-R1 reported this whole figure as the operator's trading result.
    assert result["groups"]["TEST"]["trades"] == 16
    assert result["groups"]["TEST"]["net_pnl"] == pytest.approx(-744.61, abs=0.01)
    assert result["cost_of_development_on_production"]["net_pnl"] == pytest.approx(
        result["groups"]["TEST"]["net_pnl"], abs=1e-9)


def test_the_declaration_moved_no_money_into_the_user_column(result):
    # The whole correction added 643.48 USDT of losses to the TEST group and left the operator's
    # figure exactly where it was. If this ever fails, a test has been relabelled as a user
    # trade, which is the error R1.1 exists to prevent.
    assert result["groups"]["USER"]["trades"] == 1
    assert result["groups"]["USER"]["net_pnl"] == pytest.approx(330.75, abs=0.01)


def test_the_raw_ledger_total_is_untouched_by_the_overlay(result, book):
    assert result["all_completed_trades_for_reference"]["net_pnl"] == pytest.approx(
        sum(t.net_pnl for t in book.trades), abs=1e-9)
    assert result["all_completed_trades_for_reference"]["net_pnl"] == pytest.approx(
        -413.86, abs=0.01)


def test_the_overlay_writes_beside_the_v1_artefacts_not_over_them(result):
    # The R1.1 reports cite the v1 files by name. An overlay that rewrote its own history in
    # place would leave those reports pointing at numbers they never made.
    assert S.RESULTS_NAME != "manual_subset_results_v1.json"
    assert S.PROVENANCE_NAME != "trade_provenance_v1.parquet"
    assert result["record"].endswith("_V2")
    assert result["supersedes"] == "MANUAL_R1_MANUAL_SUBSET_RESULTS_V1"


def test_the_declaration_is_quoted_not_paraphrased(result):
    declaration = result["q3_declaration"]
    assert declaration["source"] == P.DECLARATION
    assert declaration["evidence_tier"] == P.TIER_1
    assert declaration["user_trades_named"] == {"completed": [17], "open": 1}
    assert "#17" in declaration["statement"]


def _keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def test_ratios_are_withheld_for_the_one_trade_user_group(result):
    # Checked on keys, not on the serialised text: the withholding note names the metrics it is
    # refusing to report, and a substring search would flag its own explanation.
    assert result["groups"]["USER"]["ratios"] == "WITHHELD_INSUFFICIENT_SAMPLE"
    banned = {"profit_factor", "win_rate", "max_drawdown", "expectancy", "sharpe"}
    assert not (_keys(result["groups"]["USER"]) & banned)


def test_verdicts_are_the_corrected_ones(result):
    assert result["aggregate_verdict"] == "INVALID_MIXED_PROVENANCE"
    assert result["user_manual_verdict"] == "INSUFFICIENT_SAMPLE"
    assert result["provenance_audit_verdict"] == "PARTIAL"
    assert result["auto_readiness"] == "NOT_AUTHORIZED"


def test_the_run_reports_that_it_changed_nothing(result):
    assert result["ledger_modified"] is False
    assert result["production_touched"] is False


def test_contaminated_hypotheses_are_not_silently_rerun(result):
    assert result["not_recomputed"]["h1_h2_h3"] == "CONTAMINATED"
    assert result["not_recomputed"]["h4"] == "NOT_EVALUABLE"


def test_h2_is_withdrawn_as_a_statement_about_the_operator(result):
    # H2 read "the operator has no information distinguishing LONG from SHORT" off sixteen
    # trades now confirmed to be development work. It is removed, not restated with a caveat.
    assert result["not_recomputed"]["h2_status"] == "REMOVED_FROM_USER_FINDINGS"
    assert result["not_recomputed"]["user_behaviour_evaluable"] is False


def test_attribution_is_complete_while_the_audit_stays_partial(result):
    # Two different claims. Every trade now has an owner; most still have no named test type.
    assert result["attribution_verdict"] == "COMPLETE_BY_DECLARATION"
    assert result["provenance_audit_verdict"] == "PARTIAL"
    assert len(P.summary()["subtype_unspecified"]) == 13


# --- the forward provenance contract -----------------------------------------------------

def test_an_unstated_origin_is_unknown_not_user_manual():
    assert C.resolve_order_origin(None) == C.UNKNOWN
    assert C.resolve_order_origin(None) != C.USER_MANUAL


def test_smoke_callers_get_no_default_at_all():
    with pytest.raises(C.OriginRequired):
        C.require_origin(None, caller="smoke helper")


def test_a_smoke_caller_may_not_claim_to_be_the_operator():
    with pytest.raises(C.ProvenanceError):
        C.require_origin(C.USER_MANUAL, caller="smoke helper")


def test_smoke_callers_may_state_a_test_origin():
    assert C.require_origin(P.DEPLOY_SMOKE, caller="smoke helper") == P.DEPLOY_SMOKE


def test_free_text_origins_are_rejected():
    with pytest.raises(C.ProvenanceError):
        C.validate_origin("probably the user")


def test_the_production_default_string_is_caught():
    # The live signature reads `reason: str = "MANUAL"`, which is not the literal "USER_MANUAL".
    # A checker that missed this would miss the only line that matters.
    with pytest.raises(C.ProvenanceError):
        C.check_no_unsafe_default("MANUAL", where="terminal/api.py order request")
    with pytest.raises(C.ProvenanceError):
        C.check_no_unsafe_default("AUTO", where="hypothetical auto route")


def test_safe_defaults_pass_the_check():
    for value in (None, C.UNKNOWN, "EMERGENCY", "LIQUIDATION", P.DEV_SMOKE):
        C.check_no_unsafe_default(value, where="test")


def test_fee_events_must_carry_origin():
    # The correction found test fees inside the operator's result; fees follow the order.
    assert "FEE" in C.ORIGIN_BEARING_EVENTS
    assert "FUNDING" in C.ORIGIN_BEARING_EVENTS
    assert "LIQUIDATION" in C.ORIGIN_BEARING_EVENTS


def test_unknown_is_not_a_reportable_group():
    assert C.UNKNOWN_GROUP not in C.REPORTABLE_GROUPS
    assert not C.is_countable_group(C.UNKNOWN, C.USER)
    assert not C.is_countable_group(C.UNKNOWN_LEGACY, C.TEST)


def test_manual_and_auto_are_different_groups():
    assert C.group_of(C.USER_MANUAL) != C.group_of(C.AUTO_PAPER)


def test_the_audit_and_the_contract_share_one_vocabulary():
    # Two copies of this list would let the retroactive labels drift away from the forward ones,
    # and then the audit's findings could not be loaded into the system that replaces it.
    assert C.ORIGINS is P.ORIGINS
    assert C.ORIGIN_GROUP is P.ORIGIN_GROUP


# --- backward compatibility --------------------------------------------------------------

def test_an_event_with_no_origin_reads_as_legacy_not_as_a_fault():
    assert C.resolve_event_origin({"event_type": "FILL"}) == C.UNKNOWN_LEGACY
    assert C.UNKNOWN_LEGACY != C.UNKNOWN


def test_every_event_in_the_real_snapshot_is_legacy():
    events = L.load_events(SNAPSHOT / "ledger.jsonl")
    assert events
    assert {C.resolve_event_origin(e) for e in events} == {C.UNKNOWN_LEGACY}


def test_an_unrecognised_stored_origin_is_not_coerced_to_a_good_one():
    assert C.resolve_event_origin({"origin": "USER_MANUAL_probably"}) == C.UNKNOWN


def test_a_stored_origin_is_read_back_unchanged():
    assert C.resolve_event_origin({"origin": P.SIZING_TEST}) == P.SIZING_TEST


def test_the_contract_does_not_rewrite_history():
    payload = C.contract_payload()
    assert payload["backward_compatibility"]["rewrite_history"] is False
    assert payload["backward_compatibility"][
        "replay_must_be_byte_identical_for_existing_tapes"] is True


def test_the_contract_declares_itself_unapplied():
    payload = C.contract_payload()
    assert payload["status"] == "DESIGN_ONLY"
    assert payload["production_applied"] is False
    assert payload["known_violation"]["fixed_in_this_step"] is False


# --- analytics separation ----------------------------------------------------------------

def test_a_performance_view_never_combines_manual_and_auto():
    payload = C.contract_payload()["analytics_separation"]
    assert payload["never_combine"] == ["manual_view", "auto_view"]
    assert payload["unknown"]["included_in_any_performance_view"] is False


def test_drawdown_is_computed_after_the_origin_filter():
    payload = C.contract_payload()["analytics_separation"]
    assert "after the origin filter" in payload["equity_curve_rule"]


def test_each_origin_belongs_to_exactly_one_group():
    for origin in C.ORIGINS:
        groups = [g for g in (C.USER, C.TEST, C.RESEARCH, C.AUTO, C.UNKNOWN_GROUP)
                  if C.group_of(origin) == g]
        assert len(groups) == 1, origin


# --- the human decision record ------------------------------------------------------------

def _record(**overrides) -> D.DecisionRecord:
    base = dict(decision_id="d1", decided_at_ms=1_000_000, server_received_ms=1_000_400,
                side="SHORT", reason_code=D.REVERSAL_EXHAUSTION, conviction=3)
    base.update(overrides)
    return D.DecisionRecord(**base)


def test_a_valid_record_passes():
    assert D.validate(_record()).latency_ms == 400


def test_gut_is_an_allowed_answer():
    # Without it, an operator with no articulable reason picks a plausible code and every label
    # in the set becomes suspect, not just that one.
    assert D.GUT in D.REASON_CODES
    D.validate(_record(reason_code=D.GUT))


def test_other_requires_a_note():
    with pytest.raises(D.DecisionError):
        D.validate(_record(reason_code=D.OTHER))
    D.validate(_record(reason_code=D.OTHER, free_note="funding flipped hard"))


def test_free_text_reason_codes_are_rejected():
    with pytest.raises(D.DecisionError):
        D.validate(_record(reason_code="felt right"))


def test_conviction_must_be_in_range():
    for bad in (0, 6, 2.5, "3"):
        with pytest.raises(D.DecisionError):
            D.validate(_record(conviction=bad))


def test_a_decision_cannot_arrive_before_it_was_made():
    with pytest.raises(D.DecisionError):
        D.validate(_record(server_received_ms=999_999))


def test_attached_context_may_not_be_newer_than_the_decision():
    # A PIT violation here would let the label be justified by a price the operator had not seen.
    with pytest.raises(D.DecisionError):
        D.validate(_record(tape_snapshot_ts_ms=1_000_001))
    D.validate(_record(tape_snapshot_ts_ms=1_000_000))
    D.validate(_record(tape_snapshot_ts_ms=999_000))


def test_a_label_written_after_its_order_is_retrospective():
    record = _record(decided_at_ms=2_000_000, server_received_ms=2_000_000)
    assert D.label_for(record, order_ts_ms=1_999_000) == D.RETROSPECTIVE
    assert D.label_for(record, order_ts_ms=2_000_000) == D.PRESENT


def test_a_revision_is_never_used_as_the_decision():
    assert D.label_for(_record(revision_of="d1")) == D.RETROSPECTIVE


def test_a_missing_label_is_missing_not_guessed():
    assert D.label_for(None) == D.MISSING
    assert not D.usable_for_label_analysis(D.MISSING)
    assert not D.usable_for_label_analysis(D.RETROSPECTIVE)
    assert D.usable_for_label_analysis(D.PRESENT)


def test_missing_labels_do_not_remove_a_trade_from_performance():
    schema = D.schema_payload()
    assert schema["missing_data"]["missing_trades_still_count_in_performance"] is True
    assert schema["missing_data"]["imputation_allowed"] is False


def test_the_schema_declares_nothing_was_built_or_collected():
    schema = D.schema_payload()
    assert schema["status"] == "DESIGN_ONLY"
    assert schema["ui_implemented"] is False
    assert schema["collection_started"] is False
    assert schema["option_choice"] == "OPERATOR_DECISION_PENDING"


def test_sample_size_is_not_decided_from_interim_results():
    schema = D.schema_payload()
    assert schema["sample_size"]["decided_now"] is False


def test_decisions_are_stored_outside_the_ledger():
    storage = D.schema_payload()["storage"]
    assert storage["file"] == "decisions.jsonl"
    assert storage["survives_account_reset"] is True


# --- artefacts ---------------------------------------------------------------------------

def test_the_contract_artefacts_serialise(tmp_path, monkeypatch):
    monkeypatch.setattr(R2, "OUT_DIR", tmp_path)
    R2.run(write=True)
    for name in ("provenance_contract_v1.json", "human_decision_schema_v1.json"):
        payload = json.loads((tmp_path / name).read_text())
        assert payload["status"] == "DESIGN_ONLY"

"""H-V2-D4-E7R: the repaired decision-leakage semantics.

The two fixtures that matter most are not invented. They are the exact sentences run
`D4_BR_C-20261001T005758Z` produced - one in COLL's final output, which failed E7 and the run, and
one in COLL's initial response, which cost it a repair round. A detector repair that cannot state its
own motivating cases is a detector repair nobody can check.

What these tests are NOT allowed to do, and what a reader should look for: make E7 weaker. Every
decision-language fixture in §3 and §7 of the brief is asserted to still be rejected, the pre-E7R
contextual work on "fair value" and "approve/reject" is asserted unchanged, and the full stored
corpus is replayed so "no genuine leak was dropped" rests on the artifact rather than on this
docstring.
"""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.research.validation_v2 import (
    LanguageVerdict,
    classify_investment_language,
    decision_leakage_findings,
)

# --- The two real false positives, verbatim ----------------------------------------------------

COLL_FINAL_OUTPUT_SENTENCE = (
    "The flat pre-event return suggests the guidance cut was not anticipated in the price "
    "beforehand: there was neither a run-up nor a sell-off before the event."
)
"""`root.market_expectation_evidence.pre_event_positioning_reading[1].text`, run
D4_BR_C-20261001T005758Z. The sentence that failed E7 and the run."""

COLL_INITIAL_RESPONSE_SENTENCE = (
    "Nothing here is a valuation, a price target or a decision."
)
"""COLL's `limitations`, same run, initial response. A disclaimer read as the thing it disclaims."""


def test_the_sell_off_sentence_is_not_decision_leakage():
    assert decision_leakage_findings(COLL_FINAL_OUTPUT_SENTENCE) == ()


def test_the_negated_price_target_disclaimer_is_not_decision_leakage():
    assert decision_leakage_findings(COLL_INITIAL_RESPONSE_SENTENCE) == ()
    assert all(m.verdict != LanguageVerdict.VIOLATION
               for m in classify_investment_language(COLL_INITIAL_RESPONSE_SENTENCE))


# --- §3/§7: what E7 must still reject ----------------------------------------------------------

DECISION_LANGUAGE = (
    "Buy the stock.",
    "Sell the shares.",
    "Sell the stock.",
    "I recommend buying.",
    "I recommend selling shares.",
    "We recommend buying the shares.",
    "APPROVE.",
    "WATCH.",
    "REJECT.",
    "APPROVE this investment.",
    "My price target is $50.",
    "Price target is $50.",
    "Fair value is $60.",
    "Entry is $40.",
    "Exit at $55.",
    "you should buy the stock",
    "investors should exit the position",
    "this is a strong buy",
    "our price target is $50",
    "shares remain undervalued relative to peers",
    "the stock looks cheap relative to peers",
    "we approve this stock as a core holding for the portfolio",
)


@pytest.mark.parametrize("text", DECISION_LANGUAGE)
def test_actual_decision_language_is_still_rejected(text):
    assert decision_leakage_findings(text), text


# --- §4/§7: market and business language is not a decision -------------------------------------

MARKET_AND_BUSINESS_LANGUAGE = (
    "sell-off",
    "sold off",
    "buyback",
    "buyer",
    "selling pressure",
    "no sell-off",
    "not a buy recommendation",
    "not a price target",
    "no valuation is provided",
    "the filing was approved",
    "customer purchase",
    "There was a sell-off.",
    "There was neither a run-up nor a sell-off.",
    "This is not a price target.",
    "No valuation is provided.",
    "No price target is provided.",
    "This is not a recommendation to buy.",
    "This analysis does not issue an investment decision.",
    "The board approved the transaction.",
    "the Board of Directors approved a share repurchase program",
    "shareholders rejected the proposal at the annual meeting",
    "customers buy replacement parts through our distributor network",
    "the company sells HVAC equipment to commercial customers",
    "the equipment was expensive to manufacture",
    "revenue grew as selling pressure in the channel eased",
    "the shares traded off 4% on heavy volume",
    "sell-side coverage of the name is thin",
    "the buy-side has not published an estimate",
)


@pytest.mark.parametrize("text", MARKET_AND_BUSINESS_LANGUAGE)
def test_market_and_business_language_is_allowed(text):
    assert decision_leakage_findings(text) == (), text


# --- §5: negation is per clause, not a blanket exemption ---------------------------------------

def test_a_denial_followed_by_an_actual_instruction_is_still_caught():
    """The rule that keeps negation from becoming an escape hatch. The brief's own example."""
    findings = decision_leakage_findings("Do not sell; buy instead.")
    assert findings, "the BUY in the second clause is an instruction"
    assert "action" in findings[0].term


def test_a_trailing_disclaimer_does_not_excuse_an_earlier_assertion():
    """D4-BR §G's convention, which this repair inherits rather than redefines: polarity is read per
    clause and a disclaimer behind a semicolon does not reach the clause above it."""
    assert decision_leakage_findings("Buy the stock; this is not a recommendation.")
    assert decision_leakage_findings("Our price target is $50. This is not a price target.")


def test_a_denial_in_the_same_clause_does_reach_the_term():
    assert decision_leakage_findings("This is not a recommendation to buy the stock.") == ()
    assert decision_leakage_findings("Nothing in this analysis is a price target.") == ()


# --- §6: one authority -------------------------------------------------------------------------

def test_the_audit_has_no_decision_vocabulary_of_its_own():
    """The shadow list is what failed D4-BR-C. E7 now asks the classifier."""
    import inspect

    from app.dev import audit_strategy_h_v2_d4_1 as audit

    assert not hasattr(audit, "DECISION_VOCABULARY")
    source = inspect.getsource(audit)
    assert "decision_leakage_findings" in source
    assert hasattr(audit, "AUDIT_BANNED_FIELDS"), (
        "the field-NAME check is a different obligation and stays local and independent"
    )


def test_the_validator_and_the_audit_now_agree_on_both_coll_sentences():
    """The disagreement was the defect: the generation-time validator passed the sell-off sentence
    while the audit failed it, and rejected the disclaimer the audit would have passed."""
    from app.backtest.strategy_h_v2.research.schema_v2 import _no_investment_language_v2

    for sentence in (COLL_FINAL_OUTPUT_SENTENCE, COLL_INITIAL_RESPONSE_SENTENCE):
        assert _no_investment_language_v2(sentence) == sentence
        assert decision_leakage_findings(sentence) == ()


def test_decision_leakage_findings_returns_only_violations():
    """E7's threshold of zero is only meaningful if what it counts are assertions. An UNCLASSIFIED
    lexical hit is not a decision leak."""
    text = "the fair value measurement was performed by a third party"
    assert any(m.verdict != LanguageVerdict.VIOLATION
               for m in classify_investment_language(text))
    assert decision_leakage_findings(text) == ()


# --- §7: the pre-E7R contextual work is preserved ----------------------------------------------

def test_gaap_fair_value_is_still_allowed_by_the_existing_contextual_rule():
    for text in (
        "the derivatives are measured at fair value each period",
        "changes in the fair value of commodity derivatives",
        "the fair value hierarchy classifies these instruments as Level 2",
        "Fair value under GAAP was $5.0 million.",
        "The fair value was $5.0 million as of December 31, 2025.",
        "the aggregate intrinsic value of options exercised was $3.2 million",
    ):
        assert decision_leakage_findings(text) == (), text


def test_the_stock_fair_value_opinion_is_still_blocked():
    for text in (
        "the stock's fair value is $180, well above the current price",
        "we believe the fair value of the shares is materially higher than trading price",
    ):
        assert decision_leakage_findings(text), text


def test_the_retired_unconditional_jargon_list_is_gone():
    """§6. The phrases were never wrong; the claim that a phrase cannot be mentioned to deny it was.
    They live in `_JARGON_TERMS` now, where clause-level negation reaches them."""
    from app.backtest.strategy_h_v2.research import validation_v2

    assert not hasattr(validation_v2, "_UNAMBIGUOUS_TERMS")
    assert not hasattr(validation_v2, "_UNAMBIGUOUS_RE")
    joined = " ".join(validation_v2._JARGON_TERMS)
    for phrase in ("price target", "price objective", "entry zone", "undervalued", "overvalued"):
        assert phrase in joined, phrase


# --- §8/§9: the stored-corpus replay ------------------------------------------------------------

def test_the_full_corpus_replay_removes_one_false_positive_and_exposes_nothing():
    """Data-backed, so it skips on a clean checkout - `data/runtime` is gitignored. Every contract
    claim above is also asserted on an inline fixture."""
    from app.dev.replay_d4_e7r_decision_leakage import ANALYSES_GLOB, replay

    from pathlib import Path
    if not list(Path(".").glob(ANALYSES_GLOB)):
        pytest.skip("no stored analyses in this checkout")

    report = replay()
    assert report["live_calls"] == 0 and report["live_cost_usd"] == 0.0
    assert report["total_outputs"] == 15, report["total_outputs"]
    assert report["d4_analysis_records_seen"] == 19
    assert len(report["records_without_final_output"]) == 4, (
        "D4.1's two pilot candidates and Tier B's FRPT/SPSC produced no final output, and E7 is "
        "measured on final outputs by contract"
    )
    assert report["old_e7_findings"] == 1
    assert report["new_e7_findings"] == 0
    assert report["false_positives_removed"] == 1
    assert report["newly_exposed_violations"] == 0
    assert report["retained_violations"] == 0

    removed = [f for row in report["rows"] for f in row["removed"]]
    assert len(removed) == 1 and removed[0]["match"] == r"\bsell\b"
    assert "sell-off" in removed[0]["text"]


def test_the_coll_bytes_now_pass_e7_through_the_real_audit_path():
    """§9, and the check that this is a rule change rather than a COLL exemption: the same stored
    bytes are re-scored by the actual confirmation audit, not by a fixture."""
    import json
    from pathlib import Path

    from app.backtest.strategy_h_v2.expectation.d4_br_c_contract import D4_BR_C_ROOT
    from app.dev.audit_strategy_h_v2_d4_br_c import audit_confirmation
    from app.dev.replay_d4_e7r_decision_leakage import HISTORICAL

    run_id = "D4_BR_C-20261001T005758Z"
    stored_path = D4_BR_C_ROOT / f"{run_id}.gateaudit.json"
    if not stored_path.exists():
        pytest.skip(f"{stored_path} is not in this checkout")

    stored = json.loads(stored_path.read_text())
    assert stored["verdict"] == HISTORICAL["d4_br_c_verdict"] == "FAIL", (
        "the stored historical artifact is not rewritten by this step"
    )
    assert [g for g in stored["gates"] if g["gate"] == "E7"][0]["status"] == "FAIL"
    assert stored["d5_authorization"] == HISTORICAL["d5_authorization"]

    replayed = audit_confirmation(run_id)
    assert [g for g in replayed["gates"] if g["gate"] == "E7"][0]["status"] == "PASS"
    assert [g for g in replayed["gates"] if g["gate"] == "E7"][0]["observed"] == "0"
    assert replayed["verdict"] == "PASS"
    assert replayed["d5_authorization"] == "READY FOR CONTRACT DESIGN"

    # §12: no other gate semantics moved. Asserted key by key rather than summarised.
    for gate in ("E1", "E2", "E3", "E4", "E5", "E6", "E8", "SF1"):
        old = next(g for g in stored["gates"] if g["gate"] == gate)
        new = next(g for g in replayed["gates"] if g["gate"] == gate)
        assert (old["status"], old["observed"]) == (new["status"], new["observed"]), gate
    assert stored["rules"] == replayed["rules"], "C1/C4/C5/C6 unchanged"
    assert stored["state_fidelity"] == replayed["state_fidelity"], "SF1 unchanged"
    assert stored["m8_scope"] == replayed["m8_scope"], "M8/R3 unchanged"
    assert stored["expectation_gap_distribution"] == replayed["expectation_gap_distribution"]
    assert stored["budget"] == replayed["budget"]

    # The replay is in memory. The artifact on disk still records the FAIL it recorded.
    assert json.loads(stored_path.read_text())["verdict"] == "FAIL"
    assert json.loads(stored_path.read_text())["d5_authorization"] == "NOT READY"


def test_tier_b_stays_fail_with_no_gate_movement():
    """E7R must not regrade any other live run either."""
    import json

    from app.backtest.strategy_h_v2.expectation.d4_b_contract import D4_B_ROOT
    from app.dev.audit_strategy_h_v2_d4_b import audit_run

    run_id = "D4_B-20260930T053146Z"
    path = D4_B_ROOT / f"{run_id}.gateaudit.json"
    if not path.exists():
        pytest.skip(f"{path} is not in this checkout")
    stored = json.loads(path.read_text())
    fresh = audit_run(run_id)
    assert stored["verdict"] == fresh["verdict"] == "FAIL"
    for gate in ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "SF1"):
        old = next(g for g in stored["gates"] if g["gate"] == gate)
        new = next(g for g in fresh["gates"] if g["gate"] == gate)
        assert (old["status"], old["observed"]) == (new["status"], new["observed"]), gate
    assert stored["rules"] == fresh["rules"]


# --- §10: the repair-round history is not retroactively reduced --------------------------------

def test_the_coll_repair_count_is_not_rewritten():
    """The disclaimer would pass today. COLL still spent the round, and the record says so."""
    import json
    from pathlib import Path

    import glob as _glob

    from app.backtest.strategy_h_v2.expectation.d4_br_c_contract import D4_BR_C_ROOT

    paths = [p for p in _glob.glob(str(D4_BR_C_ROOT / "analyses/**/COLL/*.json"), recursive=True)
             if not p.endswith("expectation_evidence.json")]
    if not paths:
        pytest.skip("no stored COLL record in this checkout")
    record = json.loads(Path(paths[0]).read_text())
    assert len(record["repair_rounds"]) == 2, "what the run recorded, unchanged"
    assert record["initial_failure_codes"] == ["PROHIBITED_LANGUAGE"]
    assert any("price target" in d for d in record["initial_failure_details"])
    assert decision_leakage_findings(COLL_INITIAL_RESPONSE_SENTENCE) == (), (
        "diagnostic only: the sentence is allowed now, and that does not refund the round"
    )

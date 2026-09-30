"""D4-S M8 compound-coverage audit: the six findings the old citation-form bug hid.

Scope of this file. It is an AUDIT, not a repair. `numeric_roles.py` is NOT modified by this step
and M8's claim scope stays atomic, because changing either after seeing these six results is the
result-driven tuning the brief forbids. So the tests below PIN the current behaviour, including the
two defects they expose, and name them as defects so that a later repair breaks a test that says
exactly what it was protecting.

The six are six (fact, claim) pairs across FOUR claims - two claims cite two code facts each and
were flagged on both. Every one of the four uses `ClaimV2`'s compound citation form, cites ONLY code
facts, and resolves completely.

Verdict this file encodes:

    TRUE_NUMERIC_DEFECT        0
    MATCHER_FALSE_POSITIVE     4
    NOT_A_NUMERIC_RESTATEMENT  2
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.expectation.numeric_roles import (
    NumericRole,
    classify_roles,
    fact_is_restated,
)

# --- the six, verbatim from the stored records ---------------------------------------------------
#
# Inlined so this file runs on a fresh checkout: the stored analyses live under `data/runtime`, which
# is gitignored. `test_the_stored_records_still_yield_exactly_these_six` re-derives them from the
# real artifacts when those are present, so the inline copies cannot drift unnoticed.

GOOG_GAP_6 = (
    "No large completed re-rating is evident that would indicate expectations ran ahead of "
    "evidenced progress. The stock is below its 252-session high, and fundamentals continued "
    "improving on the operating line, so the evidence does not support NEGATIVE either."
)
SCCO_PRICE_5 = (
    "Across the 1- and 3-session windows the 10-Q reactions point in opposite directions, so no "
    "clear reaction can be attributed to the small production guidance increase."
)
SCCO_GAP_5 = (
    "No evidence in the pool shows expectations running ahead of evidenced progress either. The "
    "shares are below their 252-session closing high, and relative strength over 3 and 6 months "
    "is negative."
)
BSY_GAP_2 = (
    "The reality side is mixed: D3 records durable, steady top-line growth alongside code-owned "
    "DECELERATING operating income, DETERIORATING EPS and INFLECTION_NEGATIVE free cash flow. "
    "That is not a clean improvement or a clean deterioration."
)

TRUE_NUMERIC_DEFECT = "TRUE_NUMERIC_DEFECT"
MATCHER_FALSE_POSITIVE = "MATCHER_FALSE_POSITIVE"
NOT_A_NUMERIC_RESTATEMENT = "NOT_A_NUMERIC_RESTATEMENT"

#: (case, run, ticker, claim path, text, fact path, fact value, unit, classification)
SIX_CASES = (
    (1, "D4.3A", "GOOG", "root.gap_rationale[6]", GOOG_GAP_6,
     "research_facts.fundamental_changes.operating_income.state", "ACCELERATING", "STATE_TOKEN",
     NOT_A_NUMERIC_RESTATEMENT),
    (2, "D4.3A", "SCCO", "root.market_expectation_evidence.price_reaction_reading[5]", SCCO_PRICE_5,
     "price_reaction.SEC:0001001838:0001104659-26-089169.return_1d", -0.012378378378378296,
     "RETURN_FRACTION", MATCHER_FALSE_POSITIVE),
    (3, "D4.3A", "SCCO", "root.market_expectation_evidence.price_reaction_reading[5]", SCCO_PRICE_5,
     "price_reaction.SEC:0001001838:0001104659-26-089169.return_3d", 0.05491891891891898,
     "RETURN_FRACTION", MATCHER_FALSE_POSITIVE),
    (4, "D4.3A", "SCCO", "root.gap_rationale[5]", SCCO_GAP_5,
     "pre_event_price_context.drawdown_from_252s_close_high", -0.13573054164770137,
     "RETURN_FRACTION", MATCHER_FALSE_POSITIVE),
    (5, "D4.3A", "SCCO", "root.gap_rationale[5]", SCCO_GAP_5,
     "pre_event_price_context.relative_strength_6m", -0.051475145039365344,
     "RETURN_FRACTION", MATCHER_FALSE_POSITIVE),
    (6, "Final Tier A V3", "BSY", "root.gap_rationale[2]", BSY_GAP_2,
     "research_facts.fundamental_changes.revenue.state", "STABLE", "STATE_TOKEN",
     NOT_A_NUMERIC_RESTATEMENT),
)


def _roles(text: str) -> dict[str, str]:
    return {f.raw: f.role.value for f in classify_roles(text)}


def _value_restatements(text: str) -> list[str]:
    return [f.raw for f in classify_roles(text)
            if f.role == NumericRole.VALUE_RESTATEMENT and f.token is not None]


# --- the verdict ---------------------------------------------------------------------------------

def test_the_classification_totals():
    tally = {TRUE_NUMERIC_DEFECT: 0, MATCHER_FALSE_POSITIVE: 0, NOT_A_NUMERIC_RESTATEMENT: 0}
    for case in SIX_CASES:
        tally[case[-1]] += 1
    assert tally == {TRUE_NUMERIC_DEFECT: 0, MATCHER_FALSE_POSITIVE: 4,
                     NOT_A_NUMERIC_RESTATEMENT: 2}
    assert len(SIX_CASES) == 6
    assert len({(c[2], c[3]) for c in SIX_CASES}) == 4, "six findings across four claims"


@pytest.mark.parametrize("case", SIX_CASES, ids=lambda c: f"case{c[0]}-{c[2]}")
def test_every_one_of_the_six_is_currently_flagged(case):
    """The starting fact. All six read `fact_is_restated() is False` today, which is why widening
    M8's scope without a matcher repair would turn both runs' M8 from PASS to FAIL."""
    _, _, _, _, text, _, value, unit, _ = case
    assert fact_is_restated(text, value, unit) is False


#: How far the closest surviving token is from the closest reading of the cited fact, as a multiple
#: of that reading's own tolerance. Measured, not asserted loosely, because "not a near miss" is a
#: claim about a distance and the distances differ by 20x across the four cases.
#:
#: Case 2 is the one worth reading carefully: "1" sits 0.2 away from 1.2, which is the
#: percentage-form reading of return_1d (-1.2378%), only 4x its 0.05 tolerance. That proximity is a
#: coincidence of this quarter's reaction happening to be about -1.2%, and the reading is still
#: wrong: "1" is the elided first half of "1- and 3-session windows", and `return_1d`'s own
#: description is "1-session event return". But it is recorded as 4x rather than described as
#: comfortably distant, because it is the single case where a tolerance choice could have changed
#: the answer.
MARGIN_RATIOS = {2: 4.0, 3: 81.8, 4: 55.8, 5: 16.5}


@pytest.mark.parametrize("case", SIX_CASES, ids=lambda c: f"case{c[0]}-{c[2]}")
def test_none_of_the_six_is_a_true_numeric_defect(case):
    """No claim among the six states a number that is a reading of its cited fact.

    For the two STATE_TOKEN cases the fact carries no number at all. For the four RETURN_FRACTION
    cases the surviving tokens are window LENGTHS, and the measured distance to the nearest fact
    reading is recorded in `MARGIN_RATIOS` rather than waved at.
    """
    number, _, _, _, text, _, value, unit, classification = case
    assert classification != TRUE_NUMERIC_DEFECT
    if unit == "STATE_TOKEN":
        assert not isinstance(value, (int, float))
        assert number not in MARGIN_RATIOS
        return
    from app.backtest.strategy_h_v2.expectation.numeric_roles import (
        _fact_candidates, _token_values)
    candidates = _fact_candidates(float(value), unit)
    tokens = [f.token for f in classify_roles(text)
              if f.role == NumericRole.VALUE_RESTATEMENT and f.token is not None]
    ratios = [abs(abs(v) - candidate) / tolerance
              for token in tokens for v in _token_values(token, text)
              for candidate, tolerance in candidates]
    assert min(ratios) == pytest.approx(MARGIN_RATIOS[number], abs=0.1)
    assert min(ratios) > 1.0, "if this were <= 1 the matcher would have called it a match"


# --- mechanism A: the STATE_TOKEN branch never consults the role classifier ----------------------
#
# `fact_is_restated`'s STATE_TOKEN branch is
#
#     return str(value).upper() in text.upper() or not any(c.isdigit() for c in text)
#
# a raw character scan. `classify_roles` is not called, so the exclusions D4.3R built (a session
# count, a stage identifier) do not apply and ANY digit anywhere disables the "purely qualitative"
# escape. Cases 1 and 6 are that, and nothing else.

@pytest.mark.parametrize("text,digit,role", [
    (GOOG_GAP_6, "252-session", NumericRole.SESSION_COUNT.value),
    (BSY_GAP_2, "D3", NumericRole.IDENTIFIER.value),
])
def test_the_state_token_cases_have_no_value_restatement_at_all(text, digit, role):
    """The role classifier already knows these digits are not values. The STATE_TOKEN branch simply
    never asks it."""
    assert _value_restatements(text) == []
    assert _roles(text)[digit] == role


def test_a_state_token_claim_passes_when_its_sentence_happens_to_have_no_digit():
    """The same qualitative statement, with the incidental digit removed, passes. Nothing about the
    claim's relationship to the cited fact changed - only an unrelated character."""
    assert fact_is_restated("Fundamentals continued improving on the operating line.",
                            "ACCELERATING", "STATE_TOKEN") is True
    assert fact_is_restated(GOOG_GAP_6, "ACCELERATING", "STATE_TOKEN") is False


def test_known_defect_state_token_branch_is_also_a_false_negative():
    """The same raw digit scan that produces cases 1 and 6 ALSO lets a genuine mis-restatement
    through, which is the more serious half of the finding.

    "Revenue is ACCELERATING" against a fact whose value is STABLE is exactly what M8 exists to
    catch, and it passes - because the sentence contains no digit. The branch is not merely
    over-strict; it is keyed on something unrelated to the question. Recorded and NOT repaired here:
    fixing it would make M8 start enforcing state-token ownership properly, which is a change to
    what M8 enforces and needs preregistering rather than patching mid-audit.
    """
    assert fact_is_restated("Revenue is ACCELERATING.", "STABLE", "STATE_TOKEN") is True
    assert fact_is_restated("Revenue is ACCELERATING over 3 quarters.", "STABLE",
                            "STATE_TOKEN") is False


# --- mechanism B: _SESSION_COUNT does not cover an elided coordination --------------------------
#
# `_SESSION_COUNT` needs the digit adjacent to its unit word, and needs a HYPHEN for day/month/year.
# "1- and 3-session" elides the first unit word, and "3 and 6 months" uses a space. So the leading
# digit of a coordinated window pair survives as a bare COUNT token and is compared to the fact.

def test_the_window_pair_leaves_its_first_digit_unmasked():
    roles = _roles(SCCO_PRICE_5)
    assert roles["3-session"] == NumericRole.SESSION_COUNT.value
    assert roles["1"] == NumericRole.VALUE_RESTATEMENT.value, (
        "the elided '1-' of '1- and 3-session' is the whole mechanism of cases 2 and 3")
    assert _value_restatements(SCCO_PRICE_5) == ["1"]


def test_a_spaced_month_window_pair_leaves_both_digits_unmasked():
    roles = _roles(SCCO_GAP_5)
    assert roles["252-session"] == NumericRole.SESSION_COUNT.value
    assert _value_restatements(SCCO_GAP_5) == ["3", "6"]


@pytest.mark.parametrize("text", [
    "Across the 1-session and 3-session windows the reactions diverge.",
    "Relative strength over 3-month and 6-month windows is negative.",
])
def test_the_same_claims_are_clean_once_each_window_carries_its_unit_word(text):
    """Written out rather than elided, the identical statement passes against the identical facts.
    That is what makes these four matcher false positives rather than defects: the finding tracks
    English ellipsis, not anything about numeric ownership."""
    assert _value_restatements(text) == []
    assert fact_is_restated(text, -0.012378378378378296, "RETURN_FRACTION") is True
    assert fact_is_restated(text, 0.05491891891891898, "RETURN_FRACTION") is True


def test_the_digits_are_the_cited_facts_own_window_labels():
    """`code_facts` labels these facts by window - `return_1d.description` is "1-session event
    return" and `relative_strength_6m`'s is "over the same 126 sessions". `_SESSION_COUNT`'s own
    comment anticipates exactly this: "a claim describing the same window in prose necessarily
    contains that same number, and it is not the number the fact evaluates to"."""
    assert "1-" in SCCO_PRICE_5 and "3-session" in SCCO_PRICE_5
    assert "3 and 6 months" in SCCO_GAP_5


# --- what the brief's §10 asks for directly ------------------------------------------------------

def test_a_true_numeric_restatement_that_matches_exactly_passes():
    assert fact_is_restated("The 1-session reaction was -1.24%.", -0.012378378378378296,
                            "RETURN_FRACTION") is True


def test_a_true_numeric_restatement_that_mismatches_is_caught():
    """M8's actual subject, unchanged: a claim citing a code-owned fact while stating a DIFFERENT
    number. Neither mechanism above weakens this."""
    assert fact_is_restated("The 1-session reaction was -4.10%.", -0.012378378378378296,
                            "RETURN_FRACTION") is False


def test_a_fiscal_period_number_is_not_a_restatement():
    assert _roles("The 2Q26 reaction was mildly negative.")["2Q26"] == \
        NumericRole.FISCAL_PERIOD.value
    assert fact_is_restated("The 2Q26 reaction was mildly negative.", 0.05491891891891898,
                            "RETURN_FRACTION") is True


def test_a_session_count_number_is_not_a_restatement():
    assert _roles("Below its 252-session closing high.")["252-session"] == \
        NumericRole.SESSION_COUNT.value
    assert fact_is_restated("Below its 252-session closing high.", -0.13573054164770137,
                            "RETURN_FRACTION") is True


def test_a_compound_claim_citing_two_facts_and_stating_neither_is_clean():
    """The shape all four of the audited claims have: two code facts cited, no value stated for
    either. `fact_is_restated`'s "no restatement candidates" escape covers this correctly."""
    text = "The 1-session and 3-session windows point in opposite directions."
    assert _value_restatements(text) == []
    assert fact_is_restated(text, -0.012378378378378296, "RETURN_FRACTION") is True
    assert fact_is_restated(text, 0.05491891891891898, "RETURN_FRACTION") is True


def test_known_blocker_a_compound_claim_that_states_one_fact_is_flagged_on_the_others():
    """MECHANISM C: a LATENT blocker, found by writing this test rather than by reading the six.

    Measured exposure in the stored data: ZERO occurrences. Of the three claims across both runs
    that cite two or more code facts AND state a value, two state no matching value at all (those
    are cases 2-5, mechanism B) and one states EVERY cited value and passes cleanly - see
    `test_a_compound_claim_stating_all_of_its_cited_values_passes`. So this is a structural exposure
    the two Tier A runs happen not to exhibit, not an observed defect, and it is reported that way.

    The mechanism. `fact_is_restated(text, value, unit)` is called once per (claim, cited fact) and
    asks "does this text restate THIS fact". It loops every value token against that one fact, and
    its only escape is `if not restatements: return True` - no value token anywhere in the sentence.
    So the three outcomes for a compound claim are:

        states NO cited value     -> every cited fact passes (the escape)
        states ALL cited values   -> every cited fact finds its own match, all pass
        states SOME cited values  -> the silent facts are judged against a number that was never
                                     about them, and are flagged

    Only the middle-out case breaks, and it breaks because the comparison is per-fact while the
    citation is per-claim. Sound for the atomic form, where exactly one fact is cited.

    This is why "include compound claims in M8" is not a one-line scope widening. Left unrepaired
    here: it changes how M8 compares, which must be preregistered, not patched mid-audit.
    """
    text = "The 1-session reaction was -1.24% while the 3-session window went the other way."
    assert fact_is_restated(text, -0.012378378378378296, "RETURN_FRACTION") is True
    assert fact_is_restated(text, 0.05491891891891898, "RETURN_FRACTION") is False


def test_a_compound_claim_stating_all_of_its_cited_values_passes():
    """The other side of mechanism C's boundary, and a real shape from the stored data: Final Tier A
    V3's SCCO `priced_in_assessment.claims[1]` cites `last_close` and `close_252s_low` and states
    both. Each fact finds its own token, so both pass - which is why mechanism C has zero observed
    occurrences despite being structurally present."""
    text = ("The last close of 189.88 USD compares with a 252-session low close of 106.88 USD, "
            "which is consistent with expectations having already moved materially over the year.")
    assert fact_is_restated(text, 189.88, "USD") is True
    assert fact_is_restated(text, 106.88, "USD") is True


# --- the inline fixtures must not drift from the real artifacts ---------------------------------

D4_2_ROOT = Path("data/runtime/strategy_h_v2/d4_2")
NEEDS_STORED_RUNS = pytest.mark.skipif(
    not all((D4_2_ROOT / "analyses" / r).exists()
            for r in ("D4_2_A-20260929T072105Z", "D4_2_A-20260930T012115Z")),
    reason="both Tier A runs' stored analyses are gitignored runtime data",
)


@NEEDS_STORED_RUNS
def test_the_stored_records_still_yield_exactly_these_six():
    """Re-derives the six from the real artifacts and matches them against the inline copies, so a
    fixture cannot quietly diverge from the run it claims to describe."""
    from app.dev.audit_strategy_h_v2_d4_2 import audit_run

    derived = []
    for run in ("D4_2_A-20260929T072105Z", "D4_2_A-20260930T012115Z"):
        for candidate in audit_run(run)["candidates"]:
            for gap in candidate["defects"].get("compound_claim_coverage_gap") or []:
                assert gap["check"] == "code_owned_numeric", (
                    "a future_source finding would be a different audit")
                derived.append((candidate["ticker"], gap["path"],
                                gap["evidence_id"].rsplit(":CHUNK:", 1)[-1], gap["fact_value"]))

    expected = [(c[2], c[3], c[5], c[6]) for c in SIX_CASES]
    assert sorted(derived) == sorted(expected)


@NEEDS_STORED_RUNS
def test_every_audited_claim_uses_a_fully_resolved_compound_citation():
    """§2. `source_id = null` is the contract, not a defect: each claim carries >= 2 evidence_ids,
    every one resolves, and the source derived from them resolves too. Re-validated against
    `ClaimV2` with the real valid-id context, which is a stricter check than the audit performs."""
    from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
    from app.backtest.strategy_h_v2.expectation.code_facts import (
        build_code_fact_index, code_source_id)
    from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
    from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
    from app.backtest.strategy_h_v2.research.schema_v2 import ClaimV2
    from app.dev.audit_strategy_h_v2_d4_1 import _citation_form, _claim_source_ids, iter_claims
    from app.dev.run_strategy_h_v2_d4_2 import ANALYSES_ROOT

    seen = 0
    for number, _, ticker, path, _, _, _, _, _ in SIX_CASES:
        run = ("D4_2_A-20260930T012115Z" if number == 6 else "D4_2_A-20260929T072105Z")
        directory = ANALYSES_ROOT / run / ticker
        bundle = ExpectationEvidenceBundleV1.model_validate_json(
            (directory / "expectation_evidence.json").read_text())
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        facts = build_code_fact_index(
            bundle, research_facts=package.evidence_bundle.fundamental_changes)
        valid_evidence = ({c.evidence_id for c in package.chunks}
                          | set(bundle.valid_evidence_ids()) | set(facts))
        valid_sources = ({s.source_id for s in package.source_manifest}
                         | {code_source_id(bundle.bundle_id)})
        record = next(json.loads(p.read_text())
                      for p in sorted(directory.glob("*.json"))
                      if p.name != "expectation_evidence.json")
        claim = {p: c for p, c in iter_claims(record["final_output"])}[path]

        assert _citation_form(claim) == "COMPOUND"
        assert claim["source_id"] is None
        assert len(claim["evidence_ids"]) >= 2
        assert all(e in valid_evidence for e in claim["evidence_ids"])
        assert all(s in valid_sources for s in _claim_source_ids(claim))
        assert all(e in facts for e in claim["evidence_ids"]), (
            "all four audited claims cite code facts only")
        ClaimV2.model_validate(claim, context={"valid_source_ids": valid_sources,
                                               "valid_evidence_ids": valid_evidence})
        seen += 1
    assert seen == 6


@NEEDS_STORED_RUNS
def test_the_unsourced_seventeen_result_is_preserved():
    """§9. This audit must not walk back D4-S's finding that the 17 were a citation-FORM artifact."""
    from app.dev.audit_strategy_h_v2_d4_2 import audit_run

    for run in ("D4_2_A-20260929T072105Z", "D4_2_A-20260930T012115Z"):
        for candidate in audit_run(run)["candidates"]:
            assert candidate["defects"]["unsourced_material_claims"] == []


@NEEDS_STORED_RUNS
def test_both_historical_m8_verdicts_are_unchanged():
    """§7. M8's scope is still atomic, so both runs read exactly what they read historically."""
    from app.dev.audit_strategy_h_v2_d4_2 import audit_run

    for run in ("D4_2_A-20260929T072105Z", "D4_2_A-20260930T012115Z"):
        report = audit_run(run)
        by_gate = {g["gate"]: g["status"] for g in report["gates"]}
        assert by_gate["M8"] == "PASS"
        assert all(status == "PASS" for status in by_gate.values())
        for candidate in report["candidates"]:
            assert candidate["defects"]["code_owned_numeric_defects"] == []

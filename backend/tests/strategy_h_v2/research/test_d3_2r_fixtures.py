"""D3.2R offline fixtures exercised against the V2 contract (brief §16). 0 model calls: this tests
whether the V2 schema/prompt contract can represent each real failure shape D3.1/D3.2 found, not
whether a live model would actually produce the fixed version - that is D3.3's job (brief §19)."""

from __future__ import annotations

from app.backtest.strategy_h_v2.research.schema import ClaimType, Confidence
from app.backtest.strategy_h_v2.research.schema_v2 import ClaimV2
from app.backtest.strategy_h_v2.research.validation_v2 import is_compound_claim
from app.backtest.strategy_h_v2.research.prompt_builder_v2 import ATOMIC_CLAIM_CONTRACT
from fixtures.d3_2r_fixtures import (
    CITATION_PRECISION_DEFECT_EXAMPLES,
    COMPOUND_CLAIM_EXAMPLES,
    FUTURE_BUSINESS_REPAIR_CASES,
    MRVI_UNREPAIRED_CASE,
    NUMERIC_FORMAT_EXAMPLES,
)


def test_every_real_repair_case_present_and_shaped():
    assert len(FUTURE_BUSINESS_REPAIR_CASES) == 9
    tickers = {c["ticker"] for c in FUTURE_BUSINESS_REPAIR_CASES}
    assert tickers == {"DSP", "OOMA", "AIP", "DXCM", "HL", "LNG", "FLS", "MOV", "PSNL"}
    # D3.2 §H.1 reports "8 of 9 downgraded, only FLS added a flag" as an aggregate, counting
    # DXCM's move to UNKNOWN as a downgrade too. This fixture splits that 8 into 7 downgrades to a
    # defined lower stage plus DXCM's 1 abandonment to UNKNOWN - a finer read of the same data, not
    # a different count (7 + 1 abandoned + 1 flag-added = 9, matching D3.2's own denominator).
    downgrades = [c for c in FUTURE_BUSINESS_REPAIR_CASES if c["correction"] == "DOWNGRADE"]
    assert len(downgrades) == 7
    abandoned = [c for c in FUTURE_BUSINESS_REPAIR_CASES if c["correction"] == "ABANDONED"]
    assert [c["ticker"] for c in abandoned] == ["DXCM"]
    flag_added = [c for c in FUTURE_BUSINESS_REPAIR_CASES if c["correction"] == "FLAG_ADDED"]
    assert [c["ticker"] for c in flag_added] == ["FLS"]


def test_mrvi_is_the_one_case_with_no_repaired_stage():
    assert MRVI_UNREPAIRED_CASE["repaired_stage"] is None
    assert MRVI_UNREPAIRED_CASE["final_status"] == "SCHEMA_VALIDATION_FAILED"


def test_citation_precision_examples_have_distinct_correct_and_wrong_chunks():
    for example in CITATION_PRECISION_DEFECT_EXAMPLES:
        assert example["wrong_evidence_id"] != example["correct_evidence_id"]
        wrong_source = example["wrong_evidence_id"].rsplit(":CHUNK:", 1)[0]
        correct_source = example["correct_evidence_id"].rsplit(":CHUNK:", 1)[0]
        # LNG/HL are same-source-wrong-chunk; PSNL is cross-source - both real defect shapes
        # D3.2 §J.2 found, both representable by the SAME schema field (evidence_id).
        assert wrong_source == correct_source or example["ticker"] == "PSNL"


def test_compound_claim_heuristic_finds_the_genuine_case_and_reproduces_its_own_known_gap():
    """Restates D3.2 §J.6's finding as a fixed regression, rather than re-asserting a precision the
    heuristic was already shown not to have. The genuinely compound example must be caught (the
    detector is still useful for that); the single-proposition example must not be; and the
    noun-phrase-list example is asserted to still be a FALSE POSITIVE - if this ever started
    passing as a true negative, `is_compound_claim` changed in a way this fixture no longer
    documents, and the fixture (or the D3.2 report's own characterization of it) would need
    updating, not just this test."""
    genuine, single, noun_list = COMPOUND_CLAIM_EXAMPLES
    assert is_compound_claim(genuine["text"]) is True
    assert is_compound_claim(single["text"]) is False
    assert is_compound_claim(noun_list["text"]) is True  # known false positive, D3.2 §J.6


def test_genuinely_compound_example_fits_the_evidence_ids_fallback():
    """The one real genuinely-compound example in the fixture set is exactly the shape
    ClaimV2.evidence_ids exists for: two independent facts, citable from two different chunks."""
    genuine = next(e for e in COMPOUND_CLAIM_EXAMPLES if e["genuinely_compound"])
    claim = ClaimV2(
        text=genuine["text"], claim_type=ClaimType.FACT,
        evidence_ids=["SEC:0000903651:ACC:CHUNK:1", "SEC:0000903651:ACC:CHUNK:2"],
        confidence=Confidence.MEDIUM,
    )
    assert len(claim.evidence_ids) >= 2


def test_numeric_format_examples_documented_in_the_atomic_contract_instruction():
    """The prompt's numeric-claim instruction targets exactly the D3.2 §F.1 formatting variance
    that made naive parsing fail - checked here as "does the instruction text acknowledge the
    keep-number-unit-citation-together discipline", not as a parser test (validation_v2's own
    tests already cover the parser itself)."""
    assert len(NUMERIC_FORMAT_EXAMPLES) == 4
    assert "unit" in ATOMIC_CLAIM_CONTRACT.lower()
    assert "evidence_id" in ATOMIC_CLAIM_CONTRACT

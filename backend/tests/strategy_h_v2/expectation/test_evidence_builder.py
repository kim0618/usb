"""Expectation evidence bundle + builder, including runs against the real D2.1 packages.

The synthetic cases pin the contract; the real-data cases exist because a keyword locator and an
event aligner can only be wrong in ways a fixture never reproduces - both bugs this builder had
before it ever saw real input (shares_outstanding filed somewhere other than the coverage table
said, and earnings chunks carrying SEC_8K rather than EARNINGS_RELEASE) were found that way.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path

import pytest
from d4_helpers import NOW, chunk, excerpt, package, sessions, source, utc

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.evidence.sources import DatePrecision, SourceType
from app.backtest.strategy_h_v2.expectation import price_engine
from app.backtest.strategy_h_v2.expectation.evidence_builder import (
    build_expectation_evidence_bundle,
    build_valuation_context_stub,
    locate_excerpts,
    _GUIDANCE_RE,
    _MANAGEMENT_RE,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import (
    EvidenceBlock,
    ExpectationEvidenceBundleV1,
    LocatedExcerpt,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability

PACKAGES = Path("data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages")
SERIES = sessions(300, start=date(2025, 12, 1))
BENCH = {day: 50.0 + i * 0.1 for i, day in enumerate(sorted(SERIES))}


def _build(pkg: AIResearchInputV1, **kwargs) -> ExpectationEvidenceBundleV1:
    return build_expectation_evidence_bundle(
        pkg, bundle_id="EB-TEST", series=kwargs.pop("series", SERIES),
        benchmark=kwargs.pop("benchmark", BENCH), generated_at=NOW,
    )


# --- availability discipline ------------------------------------------------------------------

def test_consensus_is_source_not_available_and_never_neutral():
    bundle = _build(package())
    assert bundle.consensus.status == EvidenceAvailability.SOURCE_NOT_AVAILABLE
    assert bundle.estimate_revisions.status == EvidenceAvailability.SOURCE_NOT_AVAILABLE
    assert bundle.consensus.excerpts == []
    assert "2,010 of 2,010" in bundle.consensus.note


def test_consensus_absence_is_recorded_in_unknown_fields_not_dropped():
    bundle = _build(package())
    assert "consensus" in bundle.unknown_fields
    assert "estimate_revisions" in bundle.unknown_fields


def test_source_not_available_cannot_carry_excerpts():
    with pytest.raises(ValueError, match="no provider is connected"):
        EvidenceBlock(status=EvidenceAvailability.SOURCE_NOT_AVAILABLE, note="x",
                      excerpts=[excerpt()])


def test_available_with_no_excerpts_is_rejected():
    """The combination that would let an empty search result read as a full one."""
    with pytest.raises(ValueError, match="empty result"):
        EvidenceBlock(status=EvidenceAvailability.AVAILABLE, excerpts=[])


def test_an_absent_block_must_say_why():
    with pytest.raises(ValueError, match="absence is never anonymous"):
        EvidenceBlock(status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE)


# --- locator ----------------------------------------------------------------------------------

def test_guidance_language_is_located_with_the_terms_that_found_it():
    pkg = package(chunks=[chunk("We now expect full-year revenue of $1.0 to $1.1 billion.")])
    bundle = _build(pkg)
    assert bundle.guidance.status == EvidenceAvailability.AVAILABLE
    assert bundle.guidance.excerpts[0].matched_terms


def test_a_candidate_with_no_guidance_language_reports_not_found_not_available():
    bundle = _build(package(chunks=[chunk("The registrant operates two segments.")]))
    assert bundle.guidance.status == EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE
    assert "guidance" in bundle.unknown_fields


def test_locator_order_is_by_recency_then_chunk_index_not_by_match_count():
    """A filing that repeats the word "guidance" eight times is not eight times more relevant;
    ranking by match count would make selection depend on filing style."""
    old = chunk("guidance guidance guidance guidance", source_id="SEC:1:OLD", index=0,
                published_at=utc(2026, 1, 1))
    new = chunk("we now expect", source_id="SEC:1:NEW", index=0, published_at=utc(2026, 6, 1))
    located = locate_excerpts([old, new], _GUIDANCE_RE)
    assert [e.source_id for e in located] == ["SEC:1:NEW", "SEC:1:OLD"]


def test_management_signal_terms_locate_a_delayed_milestone():
    located = locate_excerpts(
        [chunk("The facility milestone has been delayed to the second half.")], _MANAGEMENT_RE,
    )
    assert located and "delayed" in " ".join(located[0].matched_terms)


def test_an_excerpt_id_must_belong_to_its_own_source():
    with pytest.raises(ValueError, match="does not belong to source"):
        LocatedExcerpt(source_id="SEC:1:A", evidence_id="SEC:1:B:CHUNK:0", source_type="SEC_8K",
                       published_at=NOW, text="x")


# --- price reaction ---------------------------------------------------------------------------

def test_a_future_price_bar_is_rejected_rather_than_trimmed():
    future = dict(SERIES)
    future[date(2026, 12, 31)] = 200.0
    with pytest.raises(price_engine.FuturePriceLeakError):
        _build(package(), series=future)


def test_every_pit_eligible_event_gets_a_reaction_record():
    events = [source(f"SEC:1:E{i}", available_at=utc(2026, 3, i + 1, 22, 0)) for i in range(3)]
    bundle = _build(package(sources=events))
    assert len(bundle.price_reaction) == 3
    assert [r.source_id for r in bundle.price_reaction] == ["SEC:1:E2", "SEC:1:E1", "SEC:1:E0"]


def test_an_ambiguously_timed_event_carries_the_flag_and_the_reason():
    bundle = _build(package(sources=[source("SEC:1:E", available_at=utc(2026, 3, 2, 20, 30))]))
    reaction = bundle.price_reaction[0]
    assert reaction.alignment_ambiguous is True
    assert "exchange calendar" in reaction.ambiguity_reason


def test_a_date_only_source_is_always_flagged_ambiguous():
    bundle = _build(package(sources=[
        source("SEC:1:E", available_at=utc(2026, 3, 2), precision=DatePrecision.DATE_ONLY),
    ]))
    assert bundle.price_reaction[0].alignment_ambiguous is True


# --- valuation context stub -------------------------------------------------------------------

def test_the_stub_never_computes_a_multiple():
    stub = build_valuation_context_stub(package(), 100.0)
    assert stub["trailing_multiples"]["status"] == "NOT_COMPUTABLE"
    assert stub["historical_multiple_percentile"]["status"] == "SOURCE_NOT_AVAILABLE"
    assert "3.0%" in stub["trailing_multiples"]["note"]


def test_market_cap_is_computed_from_where_shares_outstanding_actually_lives():
    pkg = package(fundamental_changes={
        "shares_outstanding": {"state": "STABLE", "current_value": 1_000_000.0,
                               "points": [{"end": "2026-06-30", "value": 1_000_000.0}]},
    })
    stub = build_valuation_context_stub(pkg, 25.0)
    assert stub["market_cap"]["value"] == pytest.approx(25_000_000.0)
    assert stub["market_cap"]["shares_as_of"] == "2026-06-30"


def test_market_cap_is_none_not_zero_when_the_share_count_is_missing():
    stub = build_valuation_context_stub(package(), 25.0)
    assert stub["market_cap"]["value"] is None
    assert stub["status"] == "NOT_COMPUTABLE"


def test_net_debt_and_enterprise_value_carry_their_own_as_of_dates():
    pkg = package(
        fundamental_changes={"shares_outstanding": {"current_value": 1_000.0, "points": []}},
        balance_sheet={
            "cash": {"status": "OK", "value": 40.0, "end": "2026-06-30"},
            "total_debt": {"status": "OK", "value": 100.0, "end": "2026-03-31"},
        },
    )
    stub = build_valuation_context_stub(pkg, 10.0)
    assert stub["net_debt"]["value"] == pytest.approx(60.0)
    assert stub["net_debt"]["cash_as_of"] == "2026-06-30"
    assert stub["net_debt"]["total_debt_as_of"] == "2026-03-31"
    assert stub["enterprise_value"]["value"] == pytest.approx(10_000.0 + 60.0)


def test_a_code_owned_context_block_cannot_hold_an_interpretation():
    with pytest.raises(ValueError, match="interpretive language"):
        ExpectationEvidenceBundleV1(
            bundle_id="B", company_id="1", ticker="A", decision_time=NOW, data_cutoff=NOW,
            generated_at=NOW, research_input_package_id="R:A",
            guidance=EvidenceBlock(status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE, note="n"),
            earnings_history=EvidenceBlock(
                status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE, note="n"),
            management_expectation_signals=EvidenceBlock(
                status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE, note="n"),
            consensus=EvidenceBlock(status=EvidenceAvailability.SOURCE_NOT_AVAILABLE, note="n"),
            estimate_revisions=EvidenceBlock(
                status=EvidenceAvailability.SOURCE_NOT_AVAILABLE, note="n"),
            pre_event_price_context={"note": "this stock looks undervalued"},
        )


def test_verbatim_source_excerpts_are_exempt_from_the_interpretive_scan():
    """D3.1 §I.2 measured what a substring scan does to real filing prose. An excerpt's job is to
    be the source's own words, so "the Board approved the repurchase" must survive intact."""
    bundle = _build(package(chunks=[
        chunk("We now expect the Board approved repurchase to continue through fiscal 2027."),
    ]))
    assert "approved" in bundle.guidance.excerpts[0].text


# --- real D2.1 data ----------------------------------------------------------------------------

REAL = pytest.mark.skipif(not PACKAGES.exists(), reason="D2.1 package snapshot not present")


@REAL
@pytest.mark.parametrize("ticker", ["LUV", "GD", "BLKB"])
def test_real_package_builds_and_locates_earnings_material_behind_an_8k(ticker: str):
    """Earnings releases reach D2.1 as EX-99 exhibits of item-2.02 8-Ks, so their chunks carry
    SEC_8K. Selecting on source_type finds nothing; the materialization role is the real
    identifier. Locked here against the actual packages."""
    pkg = AIResearchInputV1.model_validate_json((PACKAGES / f"{ticker}.json").read_text())
    cutoff = pkg.evidence_bundle.data_cutoff.date()
    series = {cutoff - timedelta(days=400 - i): 100.0 + i * 0.1 for i in range(390)}
    bench = {day: 50.0 for day in series}
    bundle = build_expectation_evidence_bundle(
        pkg, bundle_id=f"EB-{ticker}", series=series, benchmark=bench, generated_at=NOW,
    )
    assert bundle.ticker == ticker
    assert bundle.earnings_history.status == EvidenceAvailability.AVAILABLE
    assert bundle.guidance.status == EvidenceAvailability.AVAILABLE
    assert bundle.consensus.status == EvidenceAvailability.SOURCE_NOT_AVAILABLE


@REAL
def test_real_earnings_material_is_not_findable_by_source_type_alone():
    """The regression this guards: an earlier builder filtered chunks on
    `source_type in (EARNINGS_RELEASE, EARNINGS_CALL)` and found zero for LUV while its
    materialization record did carry an EARNINGS_RELEASE document."""
    pkg = AIResearchInputV1.model_validate_json((PACKAGES / "LUV.json").read_text())
    by_type = [c for c in pkg.chunks
               if c.source_type in (SourceType.EARNINGS_RELEASE, SourceType.EARNINGS_CALL)]
    roles = {d.role for d in pkg.materialization.documents}
    assert by_type == []
    assert "EARNINGS_RELEASE" in roles


@REAL
def test_real_price_context_reproduces_the_d1_bundle_values_independently():
    """D4 recomputes 1M/3M return and relative strength from the raw panel. D1 already stored its
    own values in every evidence bundle, computed by separate code. They must agree - an
    independent agreement is worth far more than either number checked against itself."""
    import gzip

    daily = Path("data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily")
    if not daily.exists():
        pytest.skip("daily price panel not present")
    pkg = AIResearchInputV1.model_validate_json((PACKAGES / "LUV.json").read_text())
    luv: dict[date, float] = {}
    spy: dict[date, float] = {}
    for path in sorted(daily.rglob("*.json.gz")):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if "body" not in doc or "session" not in doc:
            continue
        day = date.fromisoformat(doc["session"])
        for row in doc["body"].get("results", []):
            if row.get("T") == "LUV" and isinstance(row.get("c"), (int, float)):
                luv[day] = float(row["c"])
            elif row.get("T") == "SPY" and isinstance(row.get("c"), (int, float)):
                spy[day] = float(row["c"])
    context = price_engine.price_level_context(luv, spy).to_dict()
    d1 = pkg.evidence_bundle.price_context
    assert context["return_1m"]["value"] == pytest.approx(d1["return_1m"])
    assert context["return_3m"]["value"] == pytest.approx(d1["return_3m"])
    assert context["relative_strength_1m"]["value"] == pytest.approx(d1["relative_strength_1m"])
    assert context["relative_strength_3m"]["value"] == pytest.approx(d1["relative_strength_3m"])
